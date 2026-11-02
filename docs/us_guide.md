# US election-night guide (site/guide.html)

One page for the night. Everything on it is computed in the browser from the files the US dashboard already reads
(`data/us2026/meta.json`, `timeline.json` + `timeline_tail.json`, `overview.json`, `units/<race>.json`; with `?live=<base>` from the
live server every 15 s, units every 30 s, overview every 60 s) plus static files. No number is typed in.

| Part | Source | Rule |
|---|---|---|
| What to watch now | timeline rows, units files | events, below |
| Balance of power | overview (Senate holdovers, race poll chances, control prices), `house/districts.json` baseline, live: unit timelines, `house_live/timeline_tail.json`, `national_live/national.json` | seats = seats not up + sum of race chances for the Democratic side (`balanceOfPower`); polls row from poll chances (else market) and district baselines, live row from the live model's latest chance (races without a county model at the market, other districts at baseline), shown once a race has votes; experimental national model line from its seat mean; margin `D +n` / `R +n` = seats above the majority line (rounded D count); P(control) of the Senate rows: Poisson-binomial, races independent |
| Races | meta + timeline (races with a county count), overview (market-only races, House control prices) | model / Polymarket / Kalshi chance of the side the race leans to; share counted; status; lead |
| Model and market through the night | timeline rows | one panel per race with a count; dashed lines mark each source reaching 90%, the ring the moment both are there |
| Where the electorate went | units files + `data/us2026_groups` through `site/groups-agg.js` | top 3 shifts against the 2024 president among groups with a headline (the complete-counties rule: at least 3 fully counted counties covering at least 15% of the group's expected vote), nationally and for one state |
| Before the results | `data/ev2026/index.json`, `data/us2026_context/ev_groups.json`, `data/us2026/tells.json`, race poll-close times in `meta.json`, `data/us2026_guide/watch.json` | early-vote totals and pace against 2022, biggest changes in who has voted early, poll closing and how far the early count stood from the final result (2020, 2024), counties to watch first |

`data/us2026_guide/watch.json` is written by `analysis/us_guide_data.py` from `data_public/us2026_benchmarks.csv` (the `tell_top`
counties of each Senate and governor race).

## Events (`site/guide-rules.js`)

Nothing is reported before a race has 1% counted; what the model and the prices said before is the starting point of every rule.
Each line appears once. A held-back line is retried on later rows.

* The model first reaches 90 / 95 / 99% for a side (one line for the highest level crossed on a row); Polymarket or Kalshi first reach 90%.
* The model and Polymarket (Kalshi if there is no Polymarket) are more than 15 points apart; later back within 5. At most one gap line per race per 10 minutes.
* The model's favourite changes (past 50% by 2 points; one line per race per 10 minutes); the count's leader changes (from 5% counted, by 0.4 points of the two-party vote; per 15 minutes).
* A state first passes 10 / 50 / 90% of its expected vote counted.
* A group moves 3 points or more against the 2024 president, or its turnout is 10% or more off what was expected: only groups that pass the complete-counties rule; one line per 5 minutes in total, the strongest first.

The live files carry no vote-type split of the remaining vote, so there is no counting-order line on the night; the schedule shows the
historical gap between the early count and the final result (`tells.json`) instead.

## Lead time

Minutes between the model's current unbroken stretch at 90% or more on a side and Polymarket's on the same side, each moved up to
the 1% gate (the dashboard's definition of settle time, applied while the night runs). Positive: the model was first. Shown once both are at 90%.

Tests: `tests/test_us_guide.py` (node, synthetic frames). Page check: `analysis/site_check.py` case `guide`.
