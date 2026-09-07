# output.chrislawrence.ca — build plan

One narrow question: **is the economy actually producing, and does output warn or
only confirm?** Output is the thing a recession *is*: industrial production is one of
the monthly series the NBER's dating committee actually watches, so it cannot lead by
construction. The site computes exactly how much it does not lead, and then turns the
same question on the rule the public believes instead. Two computed tables: a century
of output downturns, and the two-quarter rule put on trial against the committee's own
dates.

Site ten of the family, eighth econ-core consumer. Written 2026-09-07; every series
probed live from daedalus that day through econcore's fetchers.

## Verified sources

### United States (FRED, keyless, all probed)

| Series | What | Depth | Freq | Latest |
|---|---|---|---|---|
| `INDPRO` | Industrial production index | **1919-01 →** | monthly | 102.99 |
| `IPMAN` | IP, manufacturing | 1972-01 → | monthly | 99.31 |
| `IPMINE` | IP, mining | **1919-01 →** | monthly | 122.43 |
| `IPUTIL` | IP, utilities | 1939-01 → | monthly | 110.70 |
| `IPFINAL` | IP, final products | 1939-01 → | monthly | 99.01 |
| `IPBUSEQ` | IP, business equipment | 1947-01 → | monthly | 100.41 |
| `CUMFNS` | Capacity utilization, manufacturing | **1948-01 →** | monthly | 76.01 |
| `TCU` | Capacity utilization, total industry | 1967-01 → | monthly | 76.29 |
| `GDPC1` | Real GDP, chained | **1947-Q1 →** | quarterly | $24,269.6B |
| `CMRMTSPL` | Real manufacturing and trade sales | 1967-01 → | monthly | 1,590,918 |
| `W875RX1` | Real personal income less transfers | 1959-01 → | monthly | 16,614.7 |
| `PAYEMS` | Nonfarm payrolls | 1939-01 → | monthly | 159,075k |

`INDPRO` at 1,291 observations from January 1919 is the deepest monthly series in the
family, matching credit's Moody's yields. `CUMFNS` (1948) is deliberately preferred to
`TCU` (1967) as the capacity series: nineteen extra years for the same question.

**`NAPM` (ISM PMI) returns 404 on FRED and is not available keyless.** The ISM
withdrew redistribution rights; there is no free PMI. That gap is stated on the page
rather than substituted for.

### Canada (StatCan WDS, all probed and title-verified)

| Vector | What | Depth | Status |
|---|---|---|---|
| `v62305752` | GDP at market prices, chained 2017$, SAAR (36-10-0104) | **1961-Q1 →** | live, title-verified |
| `v65201219` | Industrial production, monthly, chained 2017$ (36-10-0434) | 1997-01 → | live, title-verified |
| `v65201210` | All industries GDP, monthly (36-10-0434) | 1997-01 → | live, title-verified |
| `v65201211` | Goods-producing industries, monthly (36-10-0434) | 1997-01 → | live, title-verified |
| `v800450` | Manufacturing shipments, SA (16-10-0047) | 1992-01 → | live, title-verified |

Canada's deep anchor is quarterly and reaches 1961; the monthly industrial production
series only begins in 1997, so the Canadian card carries both and says which is which.
No splice.

## The features: two tables

**Output downturns, scored** (INDPRO, 1919 →). The family's two clocks, in drawdown
form:

- The CREST: the highest 3-month average in the two years before the alarm.
- The ALARM: the 3-month average at least 3% below its trailing 24-month high, at
  least three consecutive months; episodes merging within nine clear months; NBER
  peaks assigned to the nearest episode within twelve months either side; the usual
  outcome vocabulary. Threshold tuned once at the dry-run gate, then frozen and
  printed.
- Probed result at 3% / 3 months: **19 episodes across 107 years, 18 credited, one
  uncredited (1920-01, at the very start where the lookback is left-censored), median
  lead from the alarm −3 months.** The alarm fires *after* the recession starts. That
  negative median is the finding, not a defect: output confirms.

**The two-quarter rule, on trial** (real GDP, 1947-Q2 →). Not the family's lag engine
this time but the same spirit: take the rule everyone repeats, compute it, and score it
against the committee that actually decides. Runs of consecutive negative quarters,
attached to the nearest NBER peak within four quarters.

Probed result, and it is better than expected:

- **Fires 11 times.** One is a false alarm with no recession attached (1947-Q2).
- **Misses two recessions outright**: 1960-04 and 2001-03. In 2001 the negative
  quarters were Q1 (−0.33%) and Q3 (−0.40%) with a positive Q2 (+0.62%) wedged
  between them, so the rule never triggered during a recession everyone agrees
  happened.
- **Never leads.** Every attached firing came at a lag of 0 to +3 quarters.
- **The 2022 episode has been revised out of existence.** As published on 29 July
  2022, 2022-Q1 read −0.40% and Q2 −0.23%: two in a row, and the "technical
  recession" headlines were correct on the data as it then stood. Today's vintage
  reads Q1 −0.25% and **Q2 +0.16%**. The rule fired in real time and, on current
  data, never fired at all.

That last point is the site's sharpest card and the family's first real use of the
`vintages` field econ-core reserved in its contract and has never populated.

## The vintages card, and its hard constraint

ALFRED vintage access is **keyed only**; the keyless CSV endpoint has no real-time
view. The family rule is absolute: *kill `FRED_API_KEY` and everything still
refreshes.* So the vintage comparison is a **degradable enhancement**, never a
dependency:

- with a key, the updater stores the as-published 2022 readings alongside today's and
  the card draws both;
- without a key, the card renders a stated gap explaining that vintages need a key,
  and every other series on the page is untouched.

The vintage figures are fetched once and stored, not refetched daily: history as
published on a past date does not change.

## Architecture

Clone consumer/lending wholesale. nginx front `output` (host port **8136**, verified
free) + stdlib sidecar `output-updater`. Vendor econ-core; jobs guardrails; the
downturn engine is consumer's drawdown shell with the confidence peak renamed to a
crest, and the two-quarter engine is new but small. Host cron daily 07:30 PT (IP
mid-month, GDP quarterly with three estimates each, StatCan monthly GDP on its own
calendar); monthly log truncation. `data/meta.json` near-empty; no curated figure.
Industrial production is benchmarked annually and GDP is revised three times per
quarter plus annually, so the revision card is expected busy and says so.

## Page

1. Header, the question, chip: whether output is in a computed downturn now, IP level
   and YoY, from status tokens.
2. Tiles: IP index and YoY, capacity utilization with its historical percentile, real
   GDP growth, the computed downturn count with its median lead, and the two-quarter
   rule's score.
3. Main chart: industrial production, 1919 →, log scale option, NBER bands. A century
   of output in one line.
4. The downturn table with rule printed and computed footnotes.
5. The two-quarter rule on trial: real GDP quarterly growth with the zero line, the
   firings marked, and the table beneath naming the false alarm and the two misses.
6. The 2022 vintage card: as-published against today, drawn as two lines, with the
   keyless gap stated when no key is present.
7. Capacity and composition: CUMFNS since 1948, plus the IP subaggregates
   (manufacturing, mining, utilities) so "output" is not treated as one undifferentiated
   thing.
8. Canada: quarterly GDP YoY since 1962 and monthly industrial production since 1998,
   both drawn, never spliced; C.D. Howe bands.
9. Revisions card (busy, says why), sources card with every series, the ISM gap, the
   ALFRED key constraint, econ-core note, provenance line.

## Deploy checklist

Port 8136; `sites/output.caddy`; services.yml entry with dashy + kuma blocks;
`cf-access.sh create output.chrislawrence.ca --policy public` then poll for the bypass
to propagate (consumer needed one retry cycle); cron + truncation; screenshots (mobile
fullPage, desktop, both tables) + layout audit + console check; `ls -l data/`; commit;
push private `Lawrence908/output`.

## Anti-goals

- No splicing the Canadian monthly and quarterly bases, and no extending the 1997
  monthly series backward.
- No house "output index", no substituting a paid PMI, no inventing a PMI proxy.
- No treating the two-quarter rule as a strawman: it is scored on the same data and
  the same dates as everything else, and where it agrees with the committee the table
  says so.
- No claiming output predicts anything. The negative median lead is the content.
- No forecasts, no emdashes in page copy.

## Acceptance

- All series land with zero errors on a cold start, contract-validated, including all
  five Canadian vectors with live title checks.
- The downturn table credits every recession since 1919 except where the lookback is
  genuinely left-censored, or the discrepancy is investigated until the table is
  believed; the rule is then frozen and printed.
- The two-quarter table reproduces the probed record: one false alarm, two missed
  recessions, no leads.
- **Kill `FRED_API_KEY`: every series still refreshes and the vintage card degrades to
  a stated gap rather than erroring.**
- Both containers healthy, public 200, screenshots committed, zero console errors, no
  horizontal scroll, repo pushed, no machine-owned files in git.
