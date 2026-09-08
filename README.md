# Is the Economy Producing?

Industrial production is one of the monthly series the NBER's dating committee actually
watches, so it cannot warn you about a recession: it is part of what a recession is. This
page computes exactly how much it does not lead, then puts the rule the public uses
instead on trial against the same committee. Live at
[output.chrislawrence.ca](https://output.chrislawrence.ca).

No framework, no build step, no package manager. Plain HTML, CSS and vanilla JS on an
nginx front, with a stdlib-Python updater sidecar. Part of the economic tracker collection
(diesel, debt, jobs, yield, housing, credit, lending, freight, consumer) on the shared
[`econ-core`](https://github.com/Lawrence908/econ-core/blob/main/CONTRACT.md) series contract.

## Layout

```
src/index.html    markup, styling, the TimeChart canvas engine, every render function
data/series.json  machine-fetched, rewritten wholesale each run, never hand-edited
data/meta.json    curated; deliberately near-empty (no hand-entered figure exists here)
data/recessions.json  vendored from econ-core; never edited here
api/server.py     updater, both computed engines, the vintage probe, read-only status API
api/econcore.py   vendored, stamped copy of the shared fetchers
```

## The series

Twenty-five series on the econ-core contract. **Production**: the Federal Reserve's G.17
index from January 1919, the deepest monthly series on the site, with manufacturing,
mining, utilities, final products and business equipment components. **Capacity**: CUMFNS
manufacturing utilization from 1948, preferred to TCU total industry (1967) for nineteen
extra years on the same question. **Output**: real GDP quarterly from 1947-Q1.
**The committee's other indicators**: real manufacturing and trade sales, real personal
income less transfers, and payrolls. **Canada**: quarterly GDP from 1961-Q1 (v62305752)
and monthly GDP by industry from 1997 (industrial production v65201219, all industries
v65201210, goods-producing v65201211), plus manufacturing shipments from 1992 (v800450),
every vector title-verified live on each fetch.

Two upstream facts the code knows about so nobody rediscovers them. **There is no free
purchasing managers' index**: FRED's `NAPM` returns 404 because the ISM withdrew
redistribution rights, and no proxy is substituted for it. **ALFRED vintage access is
keyed-only**, which shapes the whole design of the vintage card below.

## Two computed tables

**The downturn table, scored.** Stretches where the 3-month average of industrial
production sits at least 3 percent below its best such average in the prior two years,
sustained three months, merged across gaps under nine months, each dated at its CREST and
its ALARM. On current data: 18 downturns across 107 years, 17 of them attached to a
recession and no recession since 1919 left uncredited. The crest leads by a median of
**zero** months and the alarm trails by four. Compare housing's crest at 20 months and
yield's first inverted month at 12: output does not see the recession coming because
output is what the recession is made of. The single surviving false alarm is 2015-16, a
real industrial recession driven by collapsing oil drilling and a strong dollar that never
became an economy-wide one, and it stays in the table.

**The two-quarter rule, on trial.** Not the family's lag engine but the same spirit: take
the rule everybody repeats, compute it, and score it against the committee that actually
decides. Since 1947-Q2 it fires 11 times, confirms a real recession 10 times, false-alarms
once (1947-Q2), and **has never once led**, at a median lag of +1 quarter. More
importantly it misses two recessions outright: 1960-61, and 2001, where the negatives fell
in Q1 and Q3 with a positive Q2 wedged between them so two never landed in a row during a
downturn nobody disputes.

**The 2022 vintage card.** In July 2022 the second consecutive negative quarter printed
and the country argued all summer about whether a recession had begun. As published on 29
July 2022 those quarters read −0.40% and −0.23%: the rule had fired. Today they read
−0.26% and **+0.16%**: under the same rule it never fired at all. This is the family's
first real use of the `vintages` field econ-core reserved in its contract and had never
populated.

### Where the rules came from

The downturn rule took one tuning pass at the dry-run gate and was then frozen. A
symmetric twelve-month attribution window left the 1974-75 collapse scored as a false alarm
while the November 1973 recession went credited to nothing, because production kept sliding
for thirteen months after that peak before clearing the threshold. That is a scoring
artefact, not a finding. Widening the before-window to eighteen months fixes it and credits
every recession since 1919; eighteen is not a new number, since credit uses the same
before-window for the same reason, that an indicator which confirms late needs room behind
it. The two-quarter rule needed no tuning because it is not ours to tune; it is scored
exactly as stated.

## The key constraint

Only the vintage card needs `FRED_API_KEY`. ALFRED is keyed-only and the keyless endpoint
serves current data alone, so the vintage probe is a **degradable enhancement, never a
dependency**: it is fetched once and stored, carried forward on later runs because history
as published on a past date does not change, and when no key and no stored copy exist the
card renders a stated gap while every other series refreshes untouched. Killing the key
must never break a refresh, and it does not.

## The updater

```bash
docker exec output-updater python /app/server.py --once      # dry run
docker exec output-updater python /app/server.py --refresh   # what cron runs
```

Host crontab, daily at 07:30 Pacific, log bounded monthly. Family guardrails: stale or
shrunken upstreams kept, failures carry forward with the error recorded, revisions logged
to `changelog.jsonl`. Expect that log to be busy: industrial production is benchmarked
annually and can move years of history at once, and real GDP is revised three times per
quarter plus each annual update. The vintage card above is that process made visible.

## Provenance

Assembled with Claude, made by Anthropic. Published indices and published aggregates, with
computed history and both rules printed. No forecasts.
