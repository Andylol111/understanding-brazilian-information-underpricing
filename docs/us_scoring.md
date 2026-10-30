# US midterms 2026: scoring after the count (`analysis/us_score.py`)

One script scores the registered forward test (prereg/NOTES.md, US section) and the display-only candidates for
future versions against the same outcomes. It only reads; every run rebuilds `site/data/us2026/score.json` and
`site/data/us2026/score.md` from scratch, so it is safe to re-run whenever results update.

```
uv run brunder us score [configs/us2026_*_v1.toml] [--out site/data] [--snap data/raw/snap/us_midterms_2026]
uv run python analysis/us_score.py [--out site/data] [--snap DIR] [--final]
```

Default configs: every `configs/us2026_*_v1.toml` except the House district configs (`us2026_house_*`, a separate study scored with its own feeds); later versions (`_v2` … `_v5`) are attached per race by
`brunder.us.load_races`. Each version's file hash is checked against `prereg/registry.jsonl`
(`races.<id>.registration`).

## Outcomes and their label

| status | source |
|---|---|
| `certified` | `data_public/us2026_certified.csv`, one row per race from the state's certified canvass |
| `provisional` | the latest snapshot of the race's official election-night feed in the recorder archive (`brunder.us.parse_feed`, the parser the live model used). Not certified. |
| `none` | neither; the race is listed with the reason and not scored |

`data_public/us2026_certified.csv` (comment lines start with `#`):

```
race,dem,rep,total,certified,source
ga-sen,<dem-side votes>,<Republican votes>,<all candidates>,2026-12-01,<URL of the certified canvass>
```

`race` is the race key (`ga-sen`, `ne-sen`, …, as in `overview.json`); `dem` is the dem-side candidate (the
Democrat, or the flagged independent in MT / NE). Optional county detail of the modelled races goes in
`data_public/us2026_certified_counties.csv` (`race,unit_id,dem,rep,total,source`); without it, county blocks use
the provisional feed counties and say so (`county_status`). Enter only figures from the state's own certified
publication. `outcome_status` is `certified` only when every scored race is.

## Registered metrics (per race and version, `races.<id>.versions.<v>`)

The primary version is `brunder.us.poll_model` (v3; Ohio v4); `role` marks the others secondary. Counted
fractions are the timeline's `counted` (v1's share of expected two-party votes), so every version is read at the
same minutes.

- `score_race`: `brunder.us.score_race` (docs/us_midterms.md "Scoring after the count") on every count row:
  margin error (model − outcome, points), turnout error (v1 `votes_proj` / outcome two-party votes − 1),
  Brier and log loss of P.
- `at_fractions`: at the first minute with ≥ 1, 5, 10, 25, 50, 75, 90 % counted: margin error of the model and of
  the raw count, z = (outcome − share) / sd and whether the outcome is inside the 90 % band (|z| < 1.645).
  `pooled.<label>.at_fractions` gives 90 % coverage and mean absolute error over races at each fraction.
- `band`: coverage over all minutes 1–95 %, 1–90 %, 90–95 % counted (`edge_audit.coverage`).
- `turnout_at_fractions`: this version's projected two-party total / outcome − 1, recomputed with
  `brunder.us.project` on the archived feed at each checkpoint minute (q = 0: the expected vote before returns);
  `share_check` is |recomputed share − published share| and should be 0.
- `probability`: `gate_to_settle` = model and Polymarket Brier / log loss on the same minutes, from the 1 % gate
  until both settled (`edge_audit.pwin_minutes`); `at_poll_close` = the last pre-count row.
- `settle`: model = first minute from which P(winner) ≥ 0.9 holds to the end, counted from the 1 % gate
  (max(settle, gate)); Polymarket / Kalshi = the same on their price over the whole series (pre-count rows
  included, so a market already ≥ 0.9 before the count settles then). `lead_min` = market − model, positive when
  the model settled first.
- `pooled.<label>.catchup`: the 30-minute catch-up slope, pooled over markets: OLS slope of the price's move over the
  next 30 minutes on the gap P − price, rows ≥ 1 % counted with both inside (0.02, 0.98), percentile CI from a
  bootstrap over (race, 30-minute block) clusters (`underpricing_figures.slope_ci`, as hu_score / il_score).
- `edge_rule` and `execution.<v>`: the frozen favoured-side rule of `configs/night_r2_v1.toml` [trade]
  (min_edge 0.05, min_counted 0.01, favoured only), unchanged. Signal = first count minute with ≥ 1 % counted where
  the side the model favours has edge ≥ 5c after the $100 walk of the recorded Polymarket book (`live.walk`, taker
  fee included). Execution at the first book ≥ signal + 0 / 30 / 60 s as a limit order (`edge_audit.walk_limit`):
  `signal` = the signal's all-in cost + 0 / 2 / 5c, `model` = P(favoured) − 5c; sizes $25 … $5,000 and the whole book;
  held to resolution. `p_market_fair` = P(P&L ≥ observed) if every fill won with probability equal to its price.
  Headline: 60 s, signal price, whole book.
  `model_limit`: edge_audit's second execution model on books (as for Israel): at the first book ≥ 60 / 300 s after
  each release (a count minute where the version's projection changed), every level on the favoured side whose
  all-in cost leaves the model ≥ 5 / 10 / 20c; eligible volume and P&L, and the $10,000 bankroll
  (`edge_audit.ml_bankroll`; headline 5c, 60 s, 10 % of each eligible level). Eligible volume is an upper bound.
  Fee rates come from the public Gamma `feeSchedule` (cached in `results/us_score/pm_fees.json`); a market whose
  fee could not be read is computed at 0 and flagged `fee_unverified`.
- `senate_control`: the forecast at the first poll close (2026-11-03 23:00 UTC) — `brunder.us.senate_control` on the
  overview with each race's Polymarket price at its last history point before that time — and the Polymarket
  control market then; outcome = dem-side seats (holdovers D + I plus races the dem side won) ≥ 51. Pending until
  every Senate race has a result; Brier and log loss per forecast.

## Display-only candidates (`display_only`; not registered tests)

- `crosstabs`: raw vs recalled-vote adjusted poll averages (`hub/crosstabs.json` races[].raw / adj, margin points)
  minus the outcome margin; MAE, mean and the number of races where the adjusted one is closer.
- `fundamentals`: `analysis/us_fundamentals.py` with 2026 as the test cycle: the 2026 county results of the modelled
  states (two-party D share, total votes; the Senate race where a Democrat is modelled, else governor; races with an independent on the dem side are left out) are written into the panel's
  2026 rows, every `HYP` / `TURNOUT_HYP` is fitted on all earlier cycles (`Fit`), and county margin ΔRMSE vs the
  statewide-uniform swing and county turnout ΔRMSE vs the flat ratio are reported (negative = the feature helps).
- `expected_vote_dropoff`: county distribution of the expected vote against the outcome per race: prior-race votes
  with a flat ratio (v1–v3), the county drop-off file of v4 / Ohio v5, and the display turnout index
  (`brunder.us.turnout_profile`). Error = 100 × log(predicted / actual), each template scaled to the actual state
  total, vote-weighted RMSE and MAE (the `us_fundamentals.turnout_tests` metric). The statewide turnout of each
  version is in `turnout_at_fractions` above.

## Registered look (`--final`)

Ordinary runs, including the automatic ones on provisional results, are monitoring runs (`reading`). `--final`
refuses unless every race with a count has a certified result; it then logs one look per registered config scored
(`brunder.registry.open_holdout`, prereg/holdout_runs.jsonl; configs must be committed and unchanged) and keeps
that score in `results/us_score/final_<UTC>.json`. Run it once, after certification.

## Tests

`tests/test_us_score.py` replays a night end to end (`analysis/us_night_replay.py feed` → `brunder.us.export` →
`us_score.run`): provisional outcomes equal the replayed elections' official totals, every metric is present per
race and version, re-projected shares equal the published ones, a certified row overrides the feed, re-runs are
identical, `--final` refuses without certified results; plus Senate control on small inputs.
