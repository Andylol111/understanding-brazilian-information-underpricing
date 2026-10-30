# Does a state's partisan drift predict its polling miss?

**Answer: weakly, and mostly through the state's lean level rather than its drift.** States that were moving toward a
party before an election do tend to beat their polls in that party's direction. But the gain is small (0.07 of a
2.4-point race RMSE), it comes almost entirely from 2016–24, and it mostly disappears once the presidential lean
*level* is controlled for: polls understate the dominant party of lopsided states. Census electorate change and
own-office (Senate/governor) trends do not help. The best pre-specified drift measure is frozen for 2026 as a
direction test, flagged `weak_historical_signal = true`.

This builds on [us_poll_error.md](us_poll_error.md) (cycle-wide common miss; the persistent state component is only
5.6 % of variance; constant bias corrections flip between 2006–14 and 2016–22) and
[us_fundamentals.md](us_fundamentals.md) (county fundamentals and state-residual persistence fail out of sample).
Nothing here edits a registered config, the registry or `src/brunder/*`.

```sh
export BRUNDER_DATA=~/ev_drift_local          # any data root; all inputs are re-fetched there
uv run python analysis/us_drift.py fetch      # ACS PUMS (reduced to state tables), MEDSL, Census NETMIG
uv run python analysis/us_drift.py panel      # -> data_public/us_state_drift_panel.csv
uv run python analysis/us_drift.py tests      # -> data_public/us_state_drift_tests.csv, results/us_drift/
uv run python analysis/us_drift.py predict    # -> data_public/us_state_drift_2026.csv, configs/hypotheses/us2026_drift_v1.toml
uv run python analysis/us_drift.py score certified.csv   # after certification (columns race, d2)
```

Unit tests are in `tests/test_us_drift.py`.

## Data

The miss is the certified two-party D share minus the final poll average, in points. A positive miss means the polls
understated the Democrat. The poll average is the v3 rule of `analysis/us_poll_error.py`: √n weights, 14-day half-life,
45-day window and a 25 % pollster cap. The universe is Senate, governor and state-presidential races, 2006–2024, with a
poll in the final 21 days. Races where D + R average under 75 % of the poll are excluded; this removes 17 races with a
strong independent, such as Maine with King, and the rule was fixed before any measure was tested. That leaves
**710 races** (282 Senate, 206 governor, 222 president) in 10 cycles.

- Polls: FiveThirtyEight `raw_polls.csv` through 2022, which also carries the certified results (CC BY 4.0). 2024 uses
  538's Senate, governor and president poll files archived by the Wayback Machine on 2024-11-18 (CC BY 4.0). For each
  poll it takes one question: LV over RV over adults, then the ballot with the most candidates.
- 2024 results and all vote history: MEDSL state returns 1976–2024 for president and Senate
  (doi:10.7910/DVN/42MVDX, CC0); 2024 governor from MEDSL 2024 precinct files (CC0).
- National House vote, the reference for Senate and governor leans: the certified result 538 attaches to its
  generic-ballot rows, 1998–2022. MEDSL's House file sits behind a Dataverse guestbook, so it was not used. As a result,
  2024 Senate and governor contests do not enter an office trend.
- Electorate: the ACS 1-year PUMS person files for 2008, 2009, 2011, 2013, 2015, 2017, 2019, 2021, 2023 and 2024
  (public domain). Each about 600 MB zip was checked against its Content-Length and `testzip`, reduced to a 4 KB state
  table, then deleted. Census population estimates NETMIG come from vintages 2019 and 2025.

**No look-ahead.** For a race in year *t*:

- presidential measures use only elections before *t*;
- office trends use the two latest same-office contests before *t*;
- ACS uses survey year *t−1*, released in September of *t*; 2026 uses 2024;
- migration uses the years ending before *t*.

One caveat: population-estimate vintages are revised later, as in `us_fundamentals.md`. Migration fails anyway.

## Measures

All lean values are the state two-party D share minus the national share, in points.

| measure | definition |
|---|---|
| `pres_lean` | latest pre-election presidential lean (the **level**: a control, not a drift) |
| `pres_d1/d2/d3` | change in presidential lean over the previous 1, 2 or 3 presidential cycles |
| `office_trend` | own-office lean trend per 4 years between the two latest prior contests (Senate within 6 years, governor within 4; president = d1) |
| `drift_combo` | **pre-specified combination**: mean of d1, d2/2 and d3/3 (drift per cycle) |
| `ncw`, `hisp`, `a65` `_s/_l` | change in the share of non-college non-Hispanic whites (adults 25+), Hispanics (adults) and 65+ (adults), per 4 years, over about 4 (3–5) or about 8 (7–9) years |
| `nm_s/_l` | mean annual net migration (% of population), previous 4 or 8 years |

## Test

Every test is within-cycle. Both the miss and the measure are demeaned by cycle (cycle fixed effects), so the common
miss, which is unforecastable per `us_poll_error.md`, is removed. The slope is fitted with **leave-one-cycle-out**
(LOCO), and the model is scored against the cycle mean alone. Intervals are 95 % cycle-cluster bootstraps over the 10
cycles. `cross-era` fits on 2016–24 and predicts 2006–14, and the reverse. The include rule was fixed before the
results:

- the pooled LOCO ΔRMSE 95 % interval is below 0;
- the sign-agreement interval is above 0.5;
- both eras improve with the same slope sign;
- the cross-era ΔRMSE is below 0.

Cycle-level numbers are descriptive.

| measure | coverage | sign agreement [95 %] | LOCO ΔRMSE [95 %] | cycles better | slope 06–14 / 16–24 | within-era ΔRMSE 06–14 / 16–24 | cross-era ΔRMSE [95 %] | rule |
|---|---|---|---|---|---|---|---|---|
| pres_lean (level) | 710, 2006+ | .613 [.57,.65] | **−0.105 [−0.187,−0.008]** | 7/10 | +0.06 / +0.09 | −0.044 / −0.158 | −0.098 [−0.173,−0.008] | include (not a drift) |
| pres_d1 | 710 | .549 [.51,.59] | −0.036 [−0.087,+0.019] | 7/10 | +0.10 / +0.24 | +0.007 / −0.078 | −0.019 [−0.077,+0.044] | don't |
| pres_d2 | 710 | .579 [.52,.63] | −0.069 [−0.136,+0.006] | 7/10 | +0.06 / +0.24 | −0.004 / −0.186 | −0.003 [−0.067,+0.075] | don't |
| pres_d3 | 710 | .568 [.53,.61] | −0.059 [−0.112,−0.007] | 8/10 | +0.07 / +0.17 | −0.018 / −0.117 | −0.032 [−0.077,+0.021] | include (weak) |
| office_trend | 696 | .483 [.45,.51] | +0.004 [−0.006,+0.017] | 5/10 | −0.02 / +0.01 | +0.002 / +0.009 | +0.019 [+0.003,+0.036] | don't |
| **drift_combo** | 710 | .576 [.53,.62] | **−0.072 [−0.135,−0.005]** | 8/10 | +0.18 / +0.50 | −0.016 / −0.164 | −0.028 [−0.086,+0.043] | include (weak) |
| ncw_s | 497, 2012+ | .445 [.41,.48] | +0.012 [+0.003,+0.021] | 2/7 | −0.01 / +0.11 | +0.056 / +0.023 | +0.001 | don't |
| ncw_l | 358, 2016+ | .494 | +0.020 [+0.004,+0.041] | 0/5 | — / +0.04 | — / +0.020 | — | don't |
| hisp_s | 497, 2012+ | .590 [.52,.65] | −0.009 [−0.036,+0.032] | 4/7 | +0.47 / +0.68 | −0.005 / +0.004 | −0.017 [−0.036,+0.010] | don't |
| hisp_l | 358, 2016+ | .578 | −0.006 [−0.048,+0.042] | 3/5 | — / +0.88 | — | — | don't |
| a65_s | 497, 2012+ | .495 | +0.022 [−0.002,+0.051] | 3/7 | +0.24 / +0.36 | −0.003 / +0.054 | −0.005 | don't |
| a65_l | 358, 2016+ | .547 | +0.002 [−0.015,+0.024] | 3/5 | — / +0.49 | — | — | don't |
| nm_s | 424, 2014+ | .547 | +0.007 [−0.004,+0.025] | 5/6 | +0.62 / +0.18 | — / +0.010 | +0.007 | don't |
| nm_l | 267, 2018+ | .569 [.54,.60] | −0.016 [−0.020,−0.014] | 4/4 | — / +0.60 | — / −0.016 | — | exploratory (2018–24 only) |

ΔRMSE is in points of two-party share; the baseline RMSE after cycle fixed effects is 2.40. `slope` is the miss per
point of the demeaned measure. Senate, governor and president subsamples point the same way and are each
insignificant except `drift_combo` for president: −0.152 [−0.244,−0.031]. Senate is −0.047 [−0.118,+0.043] and
governor −0.014 [−0.079,+0.047]. Adding cycle × office fixed effects changes nothing (−0.072 [−0.132,−0.006]).

**Drift versus lean level.** Holding the level fixed:

| measure | slope [95 %] | LOCO ΔRMSE over lean level alone [95 %] |
|---|---|---|
| drift_combo | 0.20 [0.06,0.34] | −0.018 [−0.051,+0.015] |
| pres_d1 | 0.11 [0.03,0.18] | −0.017 [−0.043,+0.009] |
| pres_d2 | 0.09 [0.02,0.16] | −0.015 [−0.052,+0.025] |
| pres_d3 | 0.04 [−0.02,0.10] | +0.002 [−0.015,+0.018] |
| office_trend | −0.01 [−0.03,+0.01] | −0.001 [−0.014,+0.013] |

The drift coefficient survives as an in-sample association. It does not improve out-of-cycle prediction beyond the
level.

**Era stability.** These are descriptive by-cycle slopes. Unlike the constant bias correction of `us_poll_error.md`,
the trend correction does **not flip sign**: the `drift_combo` slope is positive in 9 of 10 cycles, negative only in
2006 (−0.50). The 2008–24 slopes are +0.24, +0.25, +0.15, +0.41, +0.53, +0.61, +0.45, +0.53 and +0.39. Its size does
change: 0.18 in 2006–14 against 0.50 in 2016–24. Fitted on one era and applied to the other, it does not significantly
help (−0.028 [−0.086,+0.043]). The lean-level effect is the more stable one: 0.06 / 0.09, and cross-era
−0.098 [−0.173,−0.008].

## Include / don't

- **Presidential lean change (`pres_d1`, `pres_d2`):** don't include. Both point the right way, but their pooled
  intervals cross zero and neither helps before 2016.
- **`pres_d3` and the combination `drift_combo`:** these meet the fixed rule, so they are included as a **weak** signal
  only. About 0.07 points of RMSE, the interval barely below zero, no significant gain across eras or over the lean
  level. Not strong enough to adjust a model; adequate for a registered direction test.
- **Senate/governor own-office trend:** don't include. It is null and slightly harmful across eras.
- **Census electorate change (non-college white, Hispanic, 65+, migration):** don't include. Coverage starts in 2012 or
  later, and nothing helps reliably. Hispanic growth shows sign agreement but no RMSE gain; long migration is only
  2018–24. This matches `us_fundamentals.md`.
- **Lean level (finding, not drift):** polls understate the home party of lopsided states, about 0.08 point per point of
  lean. It is stable across eras and is the strongest result here. It is not part of the drift hypothesis; it is
  registered as its own forecast, `us2026_lean_v1` (below). A first run listed it among the 2026 drift candidates, where
  it won the selection. It was then removed from `FORECAST_CANDIDATES` because it is a level, not a drift. That removal
  is a post-result choice and is disclosed here.

## 2026: the frozen direction test

The measure is `drift_combo`, the best pre-specified drift measure by pooled LOCO ΔRMSE. It is computed from the MEDSL
2012/2016/2020/2024 presidential leans. The slope is 0.339 [0.198,0.459]. A prediction is 0.339 × (race drift − mean
drift of the 48 polled races). It is a *relative* miss, because the common 2026 miss is not forecast. The 90 % interval
adds the LOCO residual sd (2.32) to the slope uncertainty, so every interval is about ±3.8 points against point values
of ±1. Drift is uncorrelated with lean across these 48 races (r = −0.01), so 2026 tests drift rather than the level.

The universe is all 48 Senate and governor races in `site/data/us2026/hub/polls.json` (built 2026-10-29) with a poll in
the window, scored on the hub's own D-side/opponent pair (for example, NE Senate is Osborn vs Ricketts). Predicted
relative miss in points; + means the Democrat or D-side beats the polls:

`ny-gov −0.97 · fl-sen −0.76 · fl-gov −0.76 · ca-gov −0.48 · ia-gov/ia-sen −0.34 · ri-sen −0.32 · ms-sen −0.31 ·
nv-gov −0.26 · il-gov/il-sen −0.25 · tn-gov −0.22 · sc-sen/sc-gov −0.18 · tx-gov/tx-sen −0.15 · oh-gov/oh-sen −0.12 ·
ma-sen −0.12 · al-gov/al-sen −0.10 · mi-gov/mi-sen −0.08 · nm-sen −0.05 · ar-sen +0.03 · ct-gov +0.05 · sd-gov +0.08 ·
pa-gov +0.08 · az-gov +0.09 · nh-gov/nh-sen +0.15 · wi-gov +0.17 · vt-gov +0.18 · me-sen/me-gov +0.22 · md-gov +0.24 ·
mn-gov/mn-sen +0.25 · nc-sen +0.29 · ak-gov/ak-sen +0.33 · or-gov +0.42 · ga-gov/ga-sen +0.46 · ne-gov/ne-sen +0.53 ·
ks-gov/ks-sen +0.60` (24 D, 24 R).

The primary test is sign agreement with the relative miss, one-sided exact binomial. It belongs to the family
{`us2026_drift_v1`, `us2026_lean_v1`}, which is evaluated with Holm at α = 0.05 (the EV-gap forecast is descriptive and
outside the family). With the historical agreement rate
of 0.576 and 48 races, power is only about 0.13 at Holm's first step (α = 0.025) and 0.20 at α = 0.05. Senate and
governor races in the same state share their drift value and about half their error (correlation 0.55), so the per-race
binomial is anti-conservative. The state-collapsed test is the registered secondary. A null 2026 result is the expected
outcome even if the effect is real.

The config is `configs/hypotheses/us2026_drift_v1.toml`, status unregistered; the user registers it. Per-race inputs
are in `data_public/us_state_drift_2026.csv`.

## 2026: the lean-level forecast (`us2026_lean_v1`)

**Selection disclosure.** This forecast was chosen *after* seeing the 2006–2024 results: the lean level was in the first
candidate list, won it, and was removed from the drift candidates because it is a level. Its historical numbers
(slope 0.076 [0.046,0.102], LOCO ΔRMSE −0.105 [−0.187,−0.008], sign agreement .613 [.57,.65], cross-era −0.098
[−0.173,−0.008]) are therefore selected in-sample and should be read as optimistic. The config says so in
`selection_note`.

Prediction: 0.076 × (2024 presidential lean − mean lean of the 48 polled races), a relative miss, with 90 % intervals
of about ±3.8 points. Same 48 races, same primary test (one-sided exact binomial sign agreement), same tie and
scored-race rules and the same state-collapsed secondary as the drift forecast. 25 races lean D, 23 lean R; the largest
values are VT-gov +1.42, MD-gov +1.30, MA-sen +1.16, CA-gov +0.97 and AR-sen −1.02, AL −1.00, TN-gov −0.98, SD-gov
−0.97. Lean and drift are uncorrelated across these races (r = −0.01), so the two tests are nearly independent.
Power at the historical agreement rate (.613, optimistic) is about 0.27 at α = 0.025 and 0.38 at α = 0.05. Senate and
governor races in a state share the same lean, so the per-race binomial is anti-conservative here too.

Config: `configs/hypotheses/us2026_lean_v1.toml` (unregistered), predictions in
`data_public/us_state_drift_2026_lean.csv`. Score with
`uv run python analysis/us_drift.py score certified.csv --measure pres_lean`.
