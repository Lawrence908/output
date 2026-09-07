#!/usr/bin/env python3
"""output.chrislawrence.ca data updater and read-only status API.

One narrow question: is the economy actually producing, and does output warn
or only confirm? Industrial production is one of the monthly series the NBER's
dating committee actually watches, so it cannot lead by construction. This
file computes exactly how much it does not, and then turns the same scrutiny
on the rule the public believes instead:

  * the DOWNTURN table, scored: industrial production since January 1919,
    every drawdown dated at its crest and its alarm and attributed to NBER
    peaks. The median lead is negative and that is the finding, not a defect;
  * the TWO-QUARTER table, on trial: the folk definition of a recession,
    computed on real GDP since 1947 and scored against the committee that
    actually decides. It false-alarms, it misses, and it never leads.

Everything live on the page comes from series.json, machine-owned and
rewritten wholesale each run. data/meta.json and the vendored recessions.json
are never touched by automation. No curated figure, no hand ritual.

Guardrails, inherited from jobs: stale or shrunken upstreams are kept rather
than written, a failed fetch carries the previous series forward and records
the error, and revisions to already-published observations land in
changelog.jsonl. Industrial production is benchmarked annually and GDP is
revised three times per quarter plus annually, so the revision card is
expected busy and says so.

Two upstream facts this file knows about so nobody rediscovers them (probed
live 2026-09-07):

  * the ISM purchasing managers' index is NOT available keyless. FRED's NAPM
    returns 404; the ISM withdrew redistribution rights. There is no free PMI
    and the page states that gap rather than substituting a proxy for it;
  * ALFRED vintage access is KEYED ONLY. The keyless CSV endpoint has no
    real-time view at all. The vintage comparison is therefore a degradable
    enhancement and never a dependency: without a key the card renders a
    stated gap and every other series on the page is untouched. Killing
    FRED_API_KEY must never break a refresh.

HTTP here is read-only. Runs happen via host cron calling
`docker exec output-updater python /app/server.py --refresh`.
"""

import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import econcore

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

SERIES_FILE = os.path.join(DATA_DIR, "series.json")
CHANGELOG = os.path.join(DATA_DIR, "changelog.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "updater-state.json")
RECESSIONS_FILE = os.path.join(DATA_DIR, "recessions.json")

CURATED = ["meta", "recessions"]
SHRINK_TOLERANCE = 0.9
CHANGELOG_IN_PAYLOAD = 100

# The downturn rule. Consumer's drawdown shell with the confidence peak
# renamed to a crest: there the question was how far the mood had fallen from
# its own recent best, here it is how far production has. Frozen after one
# tuning pass at the dry-run gate; thresholds are content, not knobs.
#
# What that pass decided, recorded so nobody re-litigates it. A symmetric
# twelve-month window (consumer's) left the 1974-75 collapse scored as a false
# alarm while the November 1973 recession went credited to nothing at all,
# because industrial production kept sliding for thirteen months after that
# peak before clearing the threshold. That is a scoring artefact, not a
# finding. Widening the before-window to eighteen months fixes it and credits
# every recession since 1919. Eighteen is not a new number here: credit uses
# exactly the same before-window for exactly the same reason, that an
# indicator which confirms late needs room behind it. The one false alarm left
# standing is 2015-16, which is a real industrial recession that never became
# an economy-wide one, and it stays.
DOWNTURN_RULE = {
    "series": "us_ip",
    "basis": "3-month average versus its trailing 24-month high",
    "threshold_below_pct": 3.0,
    "sustain_months": 3,
    "merge_gap_months": 9,
    "crest_lookback_months": 24,
    "window_before_months": 18,
    "window_after_months": 12,
    "statement": ("A downturn is a stretch where the 3-month average of "
                  "industrial production sits at least 3 percent below its "
                  "highest such average over the prior two years, for at "
                  "least three months in a row; stretches separated by fewer "
                  "than nine clear months merge into one. Each downturn is "
                  "dated two ways: the CREST, the highest average in the two "
                  "years before it began, and the ALARM, the first month past "
                  "the threshold. An NBER peak from eighteen months before the "
                  "alarm to twelve months after the last signalling month is "
                  "assigned to the nearest downturn; the window is wider "
                  "behind than ahead because production keeps sliding well "
                  "into a recession before it clears any threshold. Lead time "
                  "is reported from both clocks, and both are expected to be "
                  "flat or negative: industrial production is one of the "
                  "series the dating committee itself watches, so it confirms "
                  "a recession rather than predicting one. A crest window "
                  "reaching before January 1919 renders as left-censored."),
}

# The folk rule, on trial. Not scored as a predictor because nobody claims it
# is one; scored as a DEFINITION, against the committee that actually defines.
TWO_QUARTER_RULE = {
    "series": "us_gdp_qoq",
    "basis": "consecutive quarters of negative real GDP growth",
    "sustain_quarters": 2,
    "attach_radius_quarters": 4,
    "statement": ("The rule everyone repeats: two consecutive quarters of "
                  "falling real GDP. Computed here as every run of two or "
                  "more consecutive negative quarters since 1947-Q2, each "
                  "attached to the nearest NBER peak within four quarters. "
                  "The table scores the rule the way the episode tables on "
                  "the sibling sites score their thresholds: a firing with no "
                  "recession attached is a false alarm, a recession with no "
                  "firing attached is a miss, and the lag says whether the "
                  "rule would have told you anything you did not already "
                  "know. The NBER has never used this rule and says so "
                  "publicly; it dates cycles on depth, diffusion and duration "
                  "across several monthly series."),
}

# The vintage the 2022 argument actually happened on: the advance estimate for
# 2022-Q2, published 28 July 2022, which put a second consecutive negative
# quarter on the board and started the "technical recession" fight. Fetched
# once and stored, because history as published on a past date does not
# change. Keyed only; absent a key the card states the gap.
VINTAGE_PROBES = [
    {"series_id": "GDPC1", "vintage": "2022-07-29",
     "label": "as published 29 July 2022",
     "note": "The advance estimate for 2022-Q2, the reading the technical-recession argument was had over."},
]

_payload_cache = {"stamp": None, "body": None}
_state = {"last_run": None, "results": []}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# the series list
#
# Adding a series is a human decision with a verified source; the updater only
# refreshes what is declared. Depths in the notes were probed live on
# 2026-09-07, not assumed. StatCan vectors were resolved from cube metadata
# (36-10-0104, 36-10-0434, 16-10-0047) and carry expect_title so a renumbered
# vector fails loudly instead of returning someone else's numbers.
# --------------------------------------------------------------------------

def _fred(series_id):
    return lambda: econcore.fred_series(series_id, FRED_KEY)


def _wds(vector_id, expect_title):
    return lambda: econcore.wds_vector(vector_id, expect_title=expect_title)


GDP_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3610010401"
MGDP_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3610043401"
MFG_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1610004701"

FETCHED = [
    {
        "id": "us_ip",
        "fetch": _fred("INDPRO"),
        "label": "US industrial production",
        "source": "Federal Reserve G.17 industrial production index, via FRED INDPRO",
        "source_url": "https://fred.stlouisfed.org/series/INDPRO",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1919, index 2017 = 100. The deepest monthly series on this site and one of the deepest in the collection. The downturn table computes from this series. Benchmarked annually, so the revision log will show it.",
    },
    {
        "id": "us_ip_manufacturing",
        "fetch": _fred("IPMAN"),
        "label": "US industrial production, manufacturing",
        "source": "Federal Reserve G.17, via FRED IPMAN",
        "source_url": "https://fred.stlouisfed.org/series/IPMAN",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1972. The largest component of the total, and the one people usually mean when they say industrial production.",
    },
    {
        "id": "us_ip_mining",
        "fetch": _fred("IPMINE"),
        "label": "US industrial production, mining",
        "source": "Federal Reserve G.17, via FRED IPMINE",
        "source_url": "https://fred.stlouisfed.org/series/IPMINE",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1919, the same start as the total. Shale turned this component into the most violent of the three in the 2010s.",
    },
    {
        "id": "us_ip_utilities",
        "fetch": _fred("IPUTIL"),
        "label": "US industrial production, utilities",
        "source": "Federal Reserve G.17, via FRED IPUTIL",
        "source_url": "https://fred.stlouisfed.org/series/IPUTIL",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1939. Weather moves this component more than the business cycle does, which is why the three are drawn separately rather than as one line.",
    },
    {
        "id": "us_ip_final",
        "fetch": _fred("IPFINAL"),
        "label": "US industrial production, final products",
        "source": "Federal Reserve G.17, via FRED IPFINAL",
        "source_url": "https://fred.stlouisfed.org/series/IPFINAL",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1939. Output at the end of the chain rather than materials feeding into it.",
    },
    {
        "id": "us_ip_busequip",
        "fetch": _fred("IPBUSEQ"),
        "label": "US industrial production, business equipment",
        "source": "Federal Reserve G.17, via FRED IPBUSEQ",
        "source_url": "https://fred.stlouisfed.org/series/IPBUSEQ",
        "units": "index_2017_100", "freq": "monthly",
        "note": "Monthly since January 1947. The capital-spending end of production, and the most cyclical slice of the index.",
    },
    {
        "id": "us_caputil_mfg",
        "fetch": _fred("CUMFNS"),
        "label": "US capacity utilization, manufacturing",
        "source": "Federal Reserve G.17 capacity utilization, manufacturing, via FRED CUMFNS",
        "source_url": "https://fred.stlouisfed.org/series/CUMFNS",
        "units": "percent", "freq": "monthly",
        "note": "Monthly since January 1948. Deliberately preferred to the total-industry series (TCU, 1967) for nineteen extra years on the same question: how much of what the country can build is actually being built.",
    },
    {
        "id": "us_caputil_total",
        "fetch": _fred("TCU"),
        "label": "US capacity utilization, total industry",
        "source": "Federal Reserve G.17 capacity utilization, total industry, via FRED TCU",
        "source_url": "https://fred.stlouisfed.org/series/TCU",
        "units": "percent", "freq": "monthly",
        "note": "Monthly since January 1967. The broader measure, shown beside the deeper manufacturing one rather than instead of it.",
    },
    {
        "id": "us_gdp",
        "fetch": _fred("GDPC1"),
        "label": "US real gross domestic product",
        "source": "BEA real GDP, chained 2017 dollars, SAAR, via FRED GDPC1",
        "source_url": "https://fred.stlouisfed.org/series/GDPC1",
        "units": "USD_billions_chained_2017", "freq": "quarterly",
        "note": "Quarterly since 1947-Q1. The two-quarter table computes from the quarter-over-quarter change of this series. Revised three times per quarter and again at the annual update, which is exactly why the vintage card exists.",
    },
    {
        "id": "us_coincident_sales",
        "fetch": _fred("CMRMTSPL"),
        "label": "US real manufacturing and trade sales",
        "source": "BEA/Census real manufacturing and trade industries sales, via FRED CMRMTSPL",
        "source_url": "https://fred.stlouisfed.org/series/CMRMTSPL",
        "units": "USD_millions_chained", "freq": "monthly",
        "note": "Monthly since January 1967. One of the four monthly indicators the NBER's dating committee cites. Carried so the page can show what the committee is actually looking at.",
    },
    {
        "id": "us_coincident_income",
        "fetch": _fred("W875RX1"),
        "label": "US real personal income less transfers",
        "source": "BEA real personal income excluding transfer receipts, via FRED W875RX1",
        "source_url": "https://fred.stlouisfed.org/series/W875RX1",
        "units": "USD_billions_chained_2017", "freq": "monthly",
        "note": "Monthly since January 1959. The second of the committee's four monthly indicators.",
    },
    {
        "id": "us_payrolls",
        "fetch": _fred("PAYEMS"),
        "label": "US nonfarm payroll employment",
        "source": "BLS total nonfarm payrolls, via FRED PAYEMS",
        "source_url": "https://fred.stlouisfed.org/series/PAYEMS",
        "units": "thousands_of_persons", "freq": "monthly",
        "note": "Monthly since January 1939. The third of the committee's four, and the one the news reports. The unemployment side of the same story lives on the jobs page.",
    },
    {
        "id": "ca_gdp",
        "fetch": _wds(62305752, "Gross domestic product at market prices"),
        "label": "Canada real GDP",
        "source": "StatCan table 36-10-0104-01, chained 2017 dollars, SAAR, vector v62305752",
        "source_url": GDP_TABLE,
        "units": "CAD_millions_chained_2017", "freq": "quarterly",
        "note": "Quarterly since 1961-Q1, live. The deep Canadian anchor, on the same real chained basis as the US series.",
    },
    {
        "id": "ca_ip",
        "fetch": _wds(65201219, "Industrial production"),
        "label": "Canada industrial production",
        "source": "StatCan table 36-10-0434-01, monthly GDP by industry, chained 2017 dollars, SAAR, vector v65201219",
        "source_url": MGDP_TABLE,
        "units": "CAD_millions_chained_2017", "freq": "monthly",
        "note": "Monthly since January 1997, live. Canada's answer to INDPRO, but only a quarter as deep; the monthly GDP-by-industry programme does not run back further and nothing is extended backward to pretend otherwise.",
    },
    {
        "id": "ca_gdp_monthly",
        "fetch": _wds(65201210, "All industries"),
        "label": "Canada monthly GDP, all industries",
        "source": "StatCan table 36-10-0434-01, all industries, chained 2017 dollars, SAAR, vector v65201210",
        "source_url": MGDP_TABLE,
        "units": "CAD_millions_chained_2017", "freq": "monthly",
        "note": "Monthly since January 1997. Canada publishes a monthly GDP estimate, which the United States does not; it sits here as the broader context for the industrial slice.",
    },
    {
        "id": "ca_goods",
        "fetch": _wds(65201211, "Goods-producing industries"),
        "label": "Canada goods-producing industries",
        "source": "StatCan table 36-10-0434-01, goods-producing industries, vector v65201211",
        "source_url": MGDP_TABLE,
        "units": "CAD_millions_chained_2017", "freq": "monthly",
        "note": "Monthly since January 1997. Broader than industrial production, narrower than the whole economy.",
    },
    {
        "id": "ca_mfg_shipments",
        "fetch": _wds(800450, "Sales of goods manufactured"),
        "label": "Canada manufacturing shipments",
        "source": "StatCan table 16-10-0047-01, manufacturing sales, seasonally adjusted, vector v800450",
        "source_url": MFG_TABLE,
        "units": "CAD_thousands", "freq": "monthly",
        "note": "Monthly since January 1992, nominal and seasonally adjusted. Deeper than the monthly GDP series by five years, but in dollars rather than volume, so it is drawn on its own axis and never mixed with the chained series.",
    },
]


# --------------------------------------------------------------------------
# derived series: the constructions, stated
# --------------------------------------------------------------------------

DERIVED = [
    ("us_ip", "us_ip_yoy", 12, "monthly",
     "US industrial production growth, year over year",
     "Computed here from the index in this payload."),
    ("us_gdp", "us_gdp_qoq", 1, "quarterly",
     "US real GDP growth, quarter over quarter",
     "Computed here as the plain quarter-over-quarter percent change, NOT annualized. The two-quarter table scores the sign of this series, and the sign is identical either way; the level is left unannualized so the figures in the table are the actual quarterly changes."),
    ("us_gdp", "us_gdp_yoy", 4, "quarterly",
     "US real GDP growth, year over year",
     "Computed here, four quarters apart."),
    ("us_coincident_sales", "us_coincident_sales_yoy", 12, "monthly",
     "US real manufacturing and trade sales growth, year over year",
     "Computed here."),
    ("us_coincident_income", "us_coincident_income_yoy", 12, "monthly",
     "US real personal income less transfers, year over year",
     "Computed here."),
    ("us_payrolls", "us_payrolls_yoy", 12, "monthly",
     "US payroll growth, year over year",
     "Computed here."),
    ("ca_gdp", "ca_gdp_yoy", 4, "quarterly",
     "Canada real GDP growth, year over year",
     "Computed here from the quarterly chained level in this payload."),
    ("ca_ip", "ca_ip_yoy", 12, "monthly",
     "Canada industrial production growth, year over year",
     "Computed here from the monthly chained level in this payload."),
]


def build_derived(series):
    out = {}
    for src_id, new_id, periods, freq, label, note in DERIVED:
        src = series.get(src_id)
        if not src or len(src["obs"]) <= periods:
            continue
        out[new_id] = econcore.make_series(
            new_id, label,
            "Derived: %d-period percent change of %s" % (periods, src["source"]),
            src["source_url"], "percent", freq,
            [[d, round(v, 3)] for d, v in
             econcore.yoy_percent(src["obs"], periods)],
            confidence="estimate", note=note)
    return out


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def _mi(year_month):
    year, month = year_month.split("-")[:2]
    return int(year) * 12 + int(month) - 1


def _qi(date):
    """Quarter index from an ISO date."""
    year, month = int(date[:4]), int(date[5:7])
    return year * 4 + (month - 1) // 3


def _qlabel(date):
    year, month = int(date[:4]), int(date[5:7])
    return "%dQ%d" % (year, (month - 1) // 3 + 1)


def _three_month_avg(obs):
    return [[obs[i][0], (obs[i][1] + obs[i - 1][1] + obs[i - 2][1]) / 3.0]
            for i in range(2, len(obs))]


def _runs_and_groups(flags_idx, months, merge_gap):
    runs = [[flags_idx[0], flags_idx[0]]]
    for i in flags_idx[1:]:
        if i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    groups = [runs[0][:]]
    for start_i, end_i in runs[1:]:
        gap = _mi(months[start_i][0]) - _mi(months[groups[-1][1]][0]) - 1
        if gap < merge_gap:
            groups[-1][1] = end_i
        else:
            groups.append([start_i, end_i])
    return groups


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return (ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2.0)


def build_downturns(ip_entry, recessions):
    """The downturn table: consumer's drawdown shell with the confidence peak
    renamed to a crest. Both clocks are reported, and the alarm's median lead
    is expected negative because industrial production is one of the series
    the dating committee watches."""
    avgs = _three_month_avg(ip_entry["obs"])
    months = [[d[:7], v] for d, v in avgs]
    look = DOWNTURN_RULE["crest_lookback_months"]
    threshold = DOWNTURN_RULE["threshold_below_pct"]

    below = []
    for i, (month, value) in enumerate(months):
        lo_mi = _mi(month) - look
        prior = [v for m, v in months[:i] if _mi(m) >= lo_mi]
        if not prior:
            continue
        high = max(prior)
        if high <= 0:
            continue
        below.append([month, (high - value) / high * 100.0])

    qualifying = [i for i, (_, d) in enumerate(below) if d >= threshold]
    if not qualifying:
        return {"rule": DOWNTURN_RULE, "episodes": [], "stats": {}}

    groups = _runs_and_groups(qualifying, below, DOWNTURN_RULE["merge_gap_months"])

    def longest_run(lo, hi):
        best = run = 0
        for i in range(lo, hi + 1):
            run = run + 1 if below[i][1] >= threshold else 0
            best = max(best, run)
        return best

    groups = [g for g in groups
              if longest_run(g[0], g[1]) >= DOWNTURN_RULE["sustain_months"]]

    shells = []
    for lo, hi in groups:
        span = below[lo:hi + 1]
        start, end = span[0][0], span[-1][0]
        lo_mi = _mi(start) - look
        prior = [m for m in months if lo_mi <= _mi(m[0]) < _mi(start)]
        crest = max(prior, key=lambda m: m[1]) if prior else None
        within = [m for m in months
                  if _mi(start) <= _mi(m[0]) <= _mi(end) + 6]
        trough = min(within, key=lambda m: m[1])
        shells.append({
            "start": start, "end": end, "span": span,
            "crest": crest, "trough": trough,
            "left_censored": lo_mi < _mi(months[0][0]),
            "window_lo": _mi(start) - DOWNTURN_RULE["window_before_months"],
            "window_hi": _mi(end) + DOWNTURN_RULE["window_after_months"],
            "peaks": [],
        })

    bands = recessions["us"]["bands"]
    data_through = _mi(recessions["us"]["as_of"][:7])
    first_mi = _mi(months[0][0])
    assigned = set()
    for band in bands:
        peak_month = band["peak"]
        candidates = [s for s in shells
                      if s["window_lo"] <= _mi(peak_month) <= s["window_hi"]]
        if not candidates:
            continue
        best = max(candidates, key=lambda s: _mi(s["start"]))
        best["peaks"].append(peak_month)
        assigned.add(peak_month)

    episodes = []
    for s in shells:
        led = [p for p in s["peaks"] if _mi(p) >= _mi(s["start"])]
        if led:
            outcome = "recession"
        elif s["peaks"]:
            outcome = "coincident"
        elif s["window_hi"] > data_through:
            outcome = "pending"
        else:
            outcome = "none_in_window"
        first_peak = s["peaks"][0] if s["peaks"] else None
        episodes.append({
            "start": s["start"],
            "end": s["end"],
            "left_censored": s["left_censored"],
            "crest": ({"month": s["crest"][0], "value": round(s["crest"][1], 2)}
                      if s["crest"] else None),
            "trough": {"month": s["trough"][0],
                       "value": round(s["trough"][1], 2)},
            "deepest_below_pct": round(max(d for _, d in s["span"]), 1),
            "months_signalling": len([1 for _, d in s["span"] if d >= threshold]),
            "months_span": _mi(s["end"]) - _mi(s["start"]) + 1,
            "recessions": s["peaks"],
            "lead_from_crest_months": (_mi(first_peak) - _mi(s["crest"][0])
                                       if first_peak and s["crest"] else None),
            "lead_from_alarm_months": (_mi(first_peak) - _mi(s["start"])
                                       if first_peak else None),
            "outcome": outcome,
        })

    last_month = months[-1][0]
    for e in episodes:
        e["still_running"] = e["end"] == last_month

    alarm_leads = [e["lead_from_alarm_months"] for e in episodes if e["recessions"]]
    stats = {}
    if alarm_leads:
        stats = {
            "episodes": len(episodes),
            "credited_episodes": len(alarm_leads),
            "median_lead_from_alarm_months": _median(alarm_leads),
            "median_lead_from_crest_months": _median(
                [e["lead_from_crest_months"] for e in episodes
                 if e["lead_from_crest_months"] is not None]),
            "alarm_led": len([e for e in episodes if e["outcome"] == "recession"]),
            "coincident": len([e for e in episodes if e["outcome"] == "coincident"]),
            "false_positives": len([e for e in episodes
                                    if e["outcome"] == "none_in_window"]),
            "pending": len([e for e in episodes if e["outcome"] == "pending"]),
            "false_positive_years": [e["start"][:4] for e in episodes
                                     if e["outcome"] == "none_in_window"],
            "uncredited_recessions": [b["peak"] for b in bands
                                      if _mi(b["peak"]) >= first_mi
                                      and b["peak"] not in assigned],
        }
    return {"rule": DOWNTURN_RULE, "episodes": episodes, "stats": stats}


def build_two_quarter(qoq_entry, recessions):
    """The folk rule, on trial. Every run of two or more consecutive negative
    quarters, attached to the nearest NBER peak, plus the recessions the rule
    never fired for at all. The misses are the point as much as the firings."""
    obs = qoq_entry["obs"]
    negatives = [i for i, (_, v) in enumerate(obs) if v < 0]
    bands = recessions["us"]["bands"]
    first_qi = _qi(obs[0][0])
    radius = TWO_QUARTER_RULE["attach_radius_quarters"]

    firings, attached_peaks = [], set()
    if negatives:
        runs = [[negatives[0], negatives[0]]]
        for i in negatives[1:]:
            if i == runs[-1][1] + 1:
                runs[-1][1] = i
            else:
                runs.append([i, i])
        runs = [r for r in runs
                if r[1] - r[0] + 1 >= TWO_QUARTER_RULE["sustain_quarters"]]

        for lo, hi in runs:
            span = obs[lo:hi + 1]
            start, end = span[0][0], span[-1][0]
            worst = min(span, key=lambda o: o[1])
            nearest_peak, nearest_gap = None, None
            for band in bands:
                gap = _qi(start) - _qi(band["peak"] + "-01")
                if abs(gap) <= radius and (nearest_gap is None
                                           or abs(gap) < abs(nearest_gap)):
                    nearest_peak, nearest_gap = band["peak"], gap
            if nearest_peak:
                attached_peaks.add(nearest_peak)
            firings.append({
                "start": start, "end": end,
                "start_label": _qlabel(start), "end_label": _qlabel(end),
                "quarters": hi - lo + 1,
                "worst": {"quarter": _qlabel(worst[0]),
                          "value": round(worst[1], 2)},
                "recession_peak": nearest_peak,
                "lag_quarters": nearest_gap,
                "verdict": "confirmed" if nearest_peak else "false_alarm",
            })

    # The recessions the rule never fired for, with the quarters around each
    # peak so a reader can see exactly how it failed to trigger.
    missed = []
    for band in bands:
        peak = band["peak"]
        if _qi(peak + "-01") < first_qi or peak in attached_peaks:
            continue
        around = [{"quarter": _qlabel(d), "value": round(v, 2)}
                  for d, v in obs if abs(_qi(d) - _qi(peak + "-01")) <= 3]
        missed.append({"recession_peak": peak, "quarters": around})

    lags = [f["lag_quarters"] for f in firings if f["lag_quarters"] is not None]
    stats = {
        "firings": len(firings),
        "confirmed": len([f for f in firings if f["verdict"] == "confirmed"]),
        "false_alarms": len([f for f in firings if f["verdict"] == "false_alarm"]),
        "false_alarm_quarters": [f["start_label"] for f in firings
                                 if f["verdict"] == "false_alarm"],
        "missed_recessions": [m["recession_peak"] for m in missed],
        "missed": len(missed),
        "median_lag_quarters": _median(lags),
        "led": len([l for l in lags if l < 0]),
        "first_quarter": _qlabel(obs[0][0]),
    }
    return {"rule": TWO_QUARTER_RULE, "firings": firings, "misses": missed,
            "stats": stats}


def build_status(series, downturns):
    """Per-series entries plus the shared headline block the hub reads.

    The headline follows econ-core's status-block contract: a binary state, a
    label that asserts something about the world, the figures behind it, the
    observation date it was computed from, and the printed rule. A state
    without a rule is an opinion.
    """
    status = {}

    ip = series.get("us_ip")
    ip_yoy = series.get("us_ip_yoy")
    running = None
    episodes = (downturns or {}).get("episodes") or []
    if episodes and episodes[-1].get("still_running"):
        running = episodes[-1]

    if ip and ip["obs"]:
        obs = ip["obs"]
        latest = obs[-1]
        peak = max(obs, key=lambda o: o[1])
        status["us_ip"] = {
            "latest": [latest[0], round(latest[1], 2)],
            "record_high": [peak[0], round(peak[1], 2)],
            "below_record_pct": round((peak[1] - latest[1]) / peak[1] * 100.0, 1),
            "first": obs[0][0],
            "observations": len(obs),
        }
    if ip_yoy and ip_yoy["obs"]:
        status["us_ip_yoy"] = {"latest": [ip_yoy["obs"][-1][0],
                                          ip_yoy["obs"][-1][1]]}

    for sid in ("us_caputil_mfg", "us_caputil_total", "us_gdp_qoq",
                "us_gdp_yoy", "ca_gdp_yoy", "ca_ip_yoy", "us_payrolls_yoy"):
        entry = series.get(sid)
        if entry and entry["obs"]:
            status[sid] = {"latest": [entry["obs"][-1][0], entry["obs"][-1][1]]}

    cap = series.get("us_caputil_mfg")
    if cap and cap["obs"]:
        values = [v for _, v in cap["obs"]]
        latest_v = values[-1]
        status["us_caputil_mfg"]["percentile"] = round(
            len([v for v in values if v <= latest_v]) / float(len(values)) * 100.0, 1)

    status["downturn_active"] = bool(running)
    status["signal_active"] = bool(running)

    # The status block, per econ-core CONTRACT.md.
    if ip and ip["obs"]:
        as_of = ip["obs"][-1][0]
        yoy = status.get("us_ip_yoy", {}).get("latest")
        if running:
            detail = ("industrial production %.1f, %.1f%% below its %s crest"
                      % (ip["obs"][-1][1], running["deepest_below_pct"],
                         running["crest"]["month"] if running["crest"] else "prior"))
            status["headline"] = {
                "state": "signal",
                "label": "Output in downturn",
                "detail": detail,
                "as_of": as_of,
                "rule": DOWNTURN_RULE["statement"],
            }
        else:
            detail = "industrial production %.1f" % ip["obs"][-1][1]
            if yoy:
                detail += ", %+.1f%% year over year" % yoy[1]
            below = status["us_ip"]["below_record_pct"]
            if below > 0.05:
                detail += ", %.1f%% below its record" % below
            status["headline"] = {
                "state": "normal",
                "label": "Output expanding" if (yoy and yoy[1] >= 0)
                         else "Output flat to falling",
                "detail": detail,
                "as_of": as_of,
                "rule": DOWNTURN_RULE["statement"],
            }
    return status


def build_vintages(series, old_payload):
    """The as-published readings behind the 2022 argument.

    Keyed only, fetched once, and carried forward from the stored payload on
    every later run: history as published on a past date does not change, and
    a missing key must never break a refresh. Without a key and without a
    stored copy, this returns an explicit unavailable marker that the page
    renders as a stated gap.
    """
    stored = (old_payload or {}).get("vintages") or {}
    out = {}
    for probe in VINTAGE_PROBES:
        key = "%s@%s" % (probe["series_id"], probe["vintage"])
        prior = stored.get(key)
        if prior and prior.get("obs"):
            out[key] = prior
            continue
        if not FRED_KEY:
            out[key] = {"available": False,
                        "reason": "ALFRED vintages require a FRED API key; "
                                  "the keyless CSV endpoint has no real-time view.",
                        **{k: probe[k] for k in ("series_id", "vintage", "label", "note")}}
            continue
        try:
            obs = econcore.alfred_series(probe["series_id"], FRED_KEY,
                                         probe["vintage"])
            qoq = [[d, round(v, 3)] for d, v in econcore.yoy_percent(obs, 1)]
            out[key] = {"available": True, "obs": qoq,
                        **{k: probe[k] for k in ("series_id", "vintage", "label", "note")}}
        except Exception as exc:  # noqa: BLE001 - the card degrades, the page renders
            out[key] = {"available": False,
                        "reason": "%s: %s" % (type(exc).__name__, exc),
                        **{k: probe[k] for k in ("series_id", "vintage", "label", "note")}}
    return out


def build_analysis(series):
    analysis = {}
    downturns = None
    try:
        recessions = econcore.load_recessions(RECESSIONS_FILE)
    except Exception as exc:  # noqa: BLE001 - both tables degrade, page renders
        analysis["downturns_error"] = "%s: %s" % (type(exc).__name__, exc)
        analysis["status"] = build_status(series, None)
        return analysis

    ip = series.get("us_ip")
    if ip:
        try:
            downturns = build_downturns(ip, recessions)
            analysis["downturns"] = downturns
        except Exception as exc:  # noqa: BLE001
            analysis["downturns_error"] = "%s: %s" % (type(exc).__name__, exc)
    qoq = series.get("us_gdp_qoq")
    if qoq:
        try:
            analysis["two_quarter"] = build_two_quarter(qoq, recessions)
        except Exception as exc:  # noqa: BLE001
            analysis["two_quarter_error"] = "%s: %s" % (type(exc).__name__, exc)

    analysis["status"] = build_status(series, downturns)
    return analysis


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def load_old_payload():
    try:
        with open(SERIES_FILE) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - first run, or corrupt file: start clean
        return {}


def _diff_revisions(series_id, old_obs, new_obs):
    old_map = dict(map(tuple, old_obs))
    changed = [(d, old_map[d], v) for d, v in new_obs
               if d in old_map and abs(old_map[d] - v) > 1e-9]
    if not changed:
        return None
    deltas = [abs(after - before) for _, before, after in changed]
    return {
        "series": series_id, "action": "revised",
        "changed": len(changed),
        "span": [changed[0][0], changed[-1][0]],
        "max_delta": round(max(deltas), 4),
        "sample": [{"date": d, "before": b, "after": a}
                   for d, b, a in changed[:3]],
    }


def refresh_series(dry=False):
    old_payload = load_old_payload()
    old = old_payload.get("series", {})
    series, errors, results = {}, {}, []

    for spec in FETCHED:
        sid = spec["id"]
        prev = old.get(sid)
        rec = {"series": sid, "action": "fetched"}
        try:
            obs = spec["fetch"]()
            doc = econcore.make_series(
                sid, spec["label"], spec["source"], spec["source_url"],
                spec["units"], spec["freq"], obs, note=spec.get("note"))
            if prev and prev.get("obs"):
                if doc["as_of"] < prev["as_of"]:
                    rec.update(action="stale-upstream",
                               reason="upstream at %s, behind stored %s; kept"
                                      % (doc["as_of"], prev["as_of"]))
                    doc = prev
                elif len(obs) < len(prev["obs"]) * SHRINK_TOLERANCE:
                    rec.update(action="shrunk",
                               reason="%d obs against %d stored; kept"
                                      % (len(obs), len(prev["obs"])))
                    doc = prev
                else:
                    revision = _diff_revisions(sid, prev["obs"], obs)
                    if revision and prev.get("source") == doc.get("source"):
                        if not dry:
                            econcore.log_revision(CHANGELOG, revision)
                        rec.update(action="revised", changed=revision["changed"])
                    added = len(obs) - len(prev["obs"])
                    if added > 0:
                        rec["added"] = added
            series[sid] = doc
        except Exception as exc:  # noqa: BLE001 - one dead endpoint, one chart
            errors[sid] = "%s: %s" % (type(exc).__name__, exc)
            rec.update(action="error", reason=errors[sid])
            if prev:
                series[sid] = prev
                rec["carried_forward"] = True
        results.append(rec)
        print("%-26s %-14s %s" % (sid, rec["action"], rec.get("reason", "")),
              flush=True)

    if not series:
        raise ValueError("nothing fetched and nothing stored; refusing to write")

    series.update(build_derived(series))
    analysis = build_analysis(series)
    vintages = build_vintages(series, old_payload)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Machine-fetched. Never hand-edited; the updater rewrites this file wholesale.",
        "econcore": econcore.VERSION,
        "fred_key_used": bool(FRED_KEY),
        "errors": errors,
        "series": series,
        "analysis": analysis,
        "vintages": vintages,
    }

    if dry:
        total = sum(len(s["obs"]) for s in series.values())
        print("dry run: %d series, %d observations, %d errors -- not written"
              % (len(series), total, len(errors)), flush=True)
        return payload

    tmp = SERIES_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SERIES_FILE)

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["results"] = results
    _save_state()

    total = sum(len(s["obs"]) for s in series.values())
    print("series refreshed: %d series, %d observations, %d errors"
          % (len(series), total, len(errors)), flush=True)
    return payload


def _save_state():
    try:
        with _lock:
            snapshot = dict(_state)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# read-only HTTP
# --------------------------------------------------------------------------

def _load(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def data_stamp():
    newest = 0.0
    names = [n + ".json" for n in CURATED] + ["series.json", "changelog.jsonl"]
    for name in names:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(DATA_DIR, name)))
        except OSError:
            continue
    return newest


def build_data_payload():
    stamp = data_stamp()
    if _payload_cache["stamp"] == stamp and _payload_cache["body"] is not None:
        return _payload_cache["body"]

    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name in CURATED:
        try:
            payload[name] = _load(name + ".json")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            payload[name] = None
            payload.setdefault("errors", {})[name] = str(exc)
    try:
        doc = _load("series.json")
        payload["series"] = doc.get("series", {})
        payload["analysis"] = doc.get("analysis", {})
        payload["vintages"] = doc.get("vintages", {})
        payload["series_fetched_at"] = doc.get("fetched_at")
        payload["series_errors"] = doc.get("errors", {})
    except Exception as exc:  # noqa: BLE001 - charts degrade, page renders
        payload["series"] = {}
        payload["analysis"] = {}
        payload.setdefault("errors", {})["series"] = str(exc)

    recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
    payload["changelog"] = {"total": total, "recent": recent}

    _payload_cache["stamp"] = stamp
    _payload_cache["body"] = payload
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, cache="no-cache"):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Probe the dependency, not the process: no data, not healthy.
            try:
                doc = _load("series.json")
                ip = doc.get("series", {}).get("us_ip", {})
                st = doc.get("analysis", {}).get("status", {})
                self._send(200, {
                    "status": "ok",
                    "series": len(doc.get("series", {})),
                    "latest": ip.get("as_of"),
                    "headline": st.get("headline"),
                    "signal_active": st.get("signal_active"),
                    "errors": len(doc.get("errors", {})),
                    "fetched_at": doc.get("fetched_at"),
                })
            except Exception as exc:  # noqa: BLE001 - absent data IS the unhealthy case
                self._send(503, {"status": "no data", "error": str(exc)})
        elif path == "/api/data":
            self._send(200, build_data_payload(),
                       cache="public, max-age=300, must-revalidate")
        elif path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["fred_key"] = bool(FRED_KEY)
            snapshot["econcore"] = econcore.VERSION
            self._send(200, snapshot)
        elif path == "/api/changelog":
            recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
            self._send(200, {"total": total, "recent": recent})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    if "--refresh" in sys.argv:
        refresh_series()
        return
    if "--once" in sys.argv:
        refresh_series(dry=True)
        return

    print("updater starting: fred_key=%s (schedule: host cron)"
          % bool(FRED_KEY), flush=True)

    def warm():
        try:
            refresh_series()
        except Exception as exc:  # noqa: BLE001 - server must come up regardless
            print("initial fetch failed: %s" % exc, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
