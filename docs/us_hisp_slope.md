# Model v5: the night Hispanic slope (secondary), and the v6 mixture option

Evidence (integrator, `data_public/us_county_panel.csv`): within a state the county swing is largely explained by the
county's Hispanic share (R² FL 0.86/0.85 for 2016→20 / 2020→24, TX 0.55/0.59, CA 0.05/0.67, NV 0.65/0.63, AZ 0.15/0.12)
and the slope **changes sign between cycles** (positive 2012→16, negative after). So the slope is never carried over:
it is estimated from the night's own counted counties. Uniform swing (v1–v4) then misses the remaining counties when
the counted ones are not representative.

## Model (`brunder.nowcast`, off by default)

`[model] hisp_slope = true`, `hisp_slope_tau = τ_β`, `[count] hisp_share = "data_public/us2026_hispanic_share.csv"`.

- A unit's expected swing = state swing + β·x_i, x_i = h_i − h̄, h̄ = expected-vote (V0) weighted mean of the Hispanic
  share h in the unit's region (the state; with `vote_type` the early / Election Day region). With nothing counted
  Σ R_i x_i = 0, so the pre-count forecast and variance are exactly v3's.
- β is estimated each minute by weighted least squares (weights = counted votes) of the counted units' deviations from
  their region mean on x_i minus its counted mean (one slope, region intercepts), residual variance σ²_mun (the model's own
  posterior blend), and combined with a Normal(0, τ_β²) prior: β = β̂·P/(P+1/τ²), Var β = 1/(P+1/τ²), P = 1/Var β̂.
  Few counted counties or no spread in h ⇒ β = 0.
- Everything else (state swing, poll prior, ρ carry-over, σ_mun, between-region terms) is v3 run on the deviations net of β·x;
  the counted composition bias of the state swing is therefore corrected too (δ_u uses d_i − β x_i).
- β's uncertainty enters the margin variance as (dM/dβ)²·Var β with dM/dβ = Σ 2R_i (1−ρ·counted_i)(x_i + dδ_u/dβ),
  not scaled by the counted weight (it is parameter uncertainty, not reporting-order bias).
- `hisp_blend = w` (v6): forecast = mixture w·v5 + (1−w)·v3 of the two normal predictive distributions
  (mean, variance incl. the between-model term, P(win) = w P5 + (1−w) P3; per-county projections are the w-weighted mean).
- `hisp_slope = false` (default): code path and outputs bit-identical to before for every registered config
  (`tests/test_us.py::test_hisp_slope_off_matches_previous_model_for_every_registered_config`, golden file
  `tests/fixtures/us_golden_off.json` written by the commit before this change).

Hispanic share: Census ACS 2020–24 5-year table B03003 (public domain) = `hispanic_pct` of `site/data/us2026/hub/census.json`
= `hisp` of the county panel (identical numbers, checked for all 3,104 counties); `analysis/us_polls.py hispanic_csv`
writes the small file the model reads (counties of every configured state; a missing county gets the state mean).

## Backtest (`analysis/us_hisp_backtest.py`, results in `results/us_hisp_backtest/`, not committed)

Past races from the county panel, run through `brunder.us.project` with each state's own v3 `[count]`/`[model]`
(TX FL CA AZ NM NC GA PA): president 2016→20 and 2020→24, Senate/governor 2018→22 where both county files exist
(TX gov, FL sen+gov, CA gov, AZ sen+gov, GA gov, PA sen+gov), 2012→16 president as the tuning/sensitivity cycle, and
GA 2022 governor+Senate again with the **real** certified vote-type split (`analysis/us_calibrate.py`). 30 simulated
nights per case and order scenario, checkpoints 1/5/10/25/50/75 % counted, simulated poll prior (true swing + N(0, v3's swing_prior_sd²)),
identical nights and poll errors for v3 and v5. Order scenarios: `size` = the us_calibrate generator (early batch + 4 Election Day
chunks, larger counties later); `hisp_last` pessimistic = counties in order of Hispanic share, Hispanic-heavy last
(start times spread over 270 min); `hisp_first` optimistic = the reverse.

**hisp_slope_tau chosen before looking at the scored cycles**: smallest pooled error on the tuning cycles 2012→16 and 2016→20
(τ ∈ 0.05, 0.1, **0.2**, 0.4, 0.8 → mean |margin error| 2.63, 2.55, **2.51**, 2.64, 2.96 pts; v3 2.68) ⇒ τ_β = 0.2; 2020→24 and 2018→22 are out of sample.
The median night β has the expected sign pattern (2016 +, 2020/2022/2024 − in FL, TX; about +0.1 in GA/NC when the low-Hispanic counties report first).

Mean |statewide margin error| in points of two-party Dem−Rep margin, v3 / v5 (90 % band coverage in %, v3 / v5),
scored cycles 2020/2022/2024, all races of the state:

| state | counted | size | hisp_last | hisp_first |
|---|---|---|---|---|
| TX | 5% | 2.49 / 2.51 (100 / 100) | 2.57 / 2.70 (100 / 100) | 4.61 / 2.56 (96 / 100) |
| TX | 25% | 1.72 / 1.71 (100 / 100) | 1.94 / 2.67 (100 / 100) | 3.41 / 2.18 (94 / 100) |
| TX | 50% | 1.18 / 1.11 (100 / 100) | 2.06 / 2.39 (100 / 100) | 1.53 / 2.09 (100 / 100) |
| FL | 5% | 2.45 / 2.17 (100 / 100) | 3.78 / 3.83 (100 / 100) | 3.86 / 3.71 (99 / 100) |
| FL | 25% | 1.70 / 1.25 (100 / 100) | 4.38 / 3.74 (100 / 100) | 2.85 / 1.73 (100 / 100) |
| FL | 50% | 0.96 / 0.87 (100 / 100) | 3.70 / 1.69 (100 / 100) | 1.97 / 0.77 (100 / 100) |
| CA | 5% | 2.36 / 2.30 (100 / 100) | 2.80 / 2.44 (100 / 100) | 2.92 / 2.86 (100 / 100) |
| CA | 25% | 1.25 / 1.15 (100 / 100) | 2.10 / 1.77 (100 / 100) | 2.01 / 1.77 (100 / 100) |
| CA | 50% | 0.58 / 0.57 (100 / 100) | 1.68 / 1.28 (100 / 100) | 1.02 / 0.74 (100 / 100) |
| AZ | 5% | 2.70 / 2.74 (100 / 100) | 2.97 / 2.97 (100 / 100) | 3.51 / 3.62 (100 / 100) |
| AZ | 25% | 1.44 / 1.44 (100 / 100) | 1.40 / 1.38 (100 / 100) | 1.66 / 1.75 (100 / 100) |
| AZ | 50% | 0.95 / 0.95 (100 / 100) | 1.21 / 1.18 (100 / 100) | 1.52 / 1.63 (100 / 100) |
| NM | 5% | 2.41 / 2.35 (100 / 100) | 3.15 / 3.27 (100 / 100) | 4.50 / 4.46 (100 / 100) |
| NM | 25% | 1.56 / 1.58 (100 / 100) | 1.75 / 2.26 (100 / 100) | 2.33 / 1.99 (100 / 100) |
| NM | 50% | 0.43 / 0.42 (100 / 100) | 1.41 / 1.55 (100 / 100) | 1.30 / 0.96 (100 / 100) |
| NC | 5% | 2.59 / 2.60 (100 / 100) | 2.55 / 2.37 (100 / 100) | 3.65 / 3.66 (95 / 95) |
| NC | 25% | 1.66 / 1.67 (100 / 100) | 1.44 / 1.12 (100 / 100) | 1.51 / 1.59 (100 / 100) |
| NC | 50% | 0.86 / 0.86 (100 / 100) | 0.83 / 0.70 (100 / 100) | 0.94 / 1.00 (100 / 100) |
| GA | 5% | 2.79 / 2.81 (100 / 100) | 2.30 / 2.34 (100 / 100) | 3.84 / 3.84 (99 / 99) |
| GA | 25% | 2.67 / 2.68 (99 / 99) | 1.65 / 1.79 (100 / 100) | 2.08 / 2.07 (100 / 100) |
| GA | 50% | 2.12 / 2.13 (99 / 99) | 1.21 / 1.36 (100 / 100) | 1.56 / 1.53 (100 / 100) |
| PA | 5% | 2.65 / 2.66 (100 / 100) | 1.94 / 1.93 (100 / 100) | 3.15 / 3.16 (100 / 100) |
| PA | 25% | 1.66 / 1.64 (100 / 100) | 1.39 / 1.21 (100 / 100) | 1.80 / 1.88 (100 / 100) |
| PA | 50% | 1.04 / 1.05 (100 / 100) | 0.79 / 1.26 (100 / 100) | 1.35 / 1.61 (100 / 100) |
| pooled | 5% | 2.56 / 2.52 (100 / 100) | 2.76 / 2.73 (100 / 100) | 3.75 / 3.48 (99 / 99) |
| pooled | 25% | 1.71 / 1.64 (100 / 100) | 2.01 / 1.99 (100 / 100) | 2.21 / 1.87 (99 / 100) |
| pooled | 50% | 1.02 / 0.99 (100 / 100) | 1.61 / 1.42 (100 / 100) | 1.40 / 1.29 (100 / 100) |

Pooled over the eight states and all six checkpoints: `size` 1.91 / 1.87, `hisp_last` 2.20 / 2.14, `hisp_first` 2.59 / 2.43 pts.
Per state, all scenarios and checkpoints (v3 → v5): FL 2.70 → 2.20, CA 1.99 → 1.83, TX 2.39 → 2.28, NC 1.97 → 1.92, AZ 2.21 → 2.24,
GA 2.28 → 2.30, NM 2.40 → 2.41, PA 1.93 → 1.98. Where the effect is ~0 (AZ, GA, PA, NM, NC) shrinkage keeps v5 within ±0.06 pts (±3 %) of v3.
Harm: TX `hisp_last` 25/50 % +0.74/+0.33 pts (the counted low-Hispanic counties show a *positive* slope, the unobserved border counties the opposite: a
linear slope extrapolates the wrong way), NM `hisp_last` 25 % +0.50, PA `hisp_last` 50 % +0.47 (pa: small n, β noisy).
GA 2022 real vote-type order: v3 / v5 = 3.26/3.25 (size), 1.82/1.92 (hisp_last), 2.59/2.53 (hisp_first) — no effect, as expected (the error there is the vote-type gap).
2012→16 (opposite slope sign, not scored): v5 better in FL, GA, NC, equal elsewhere except NM +0.14.
Coverage: the 90 % band covers 99–100 % for both models (v3 is conservative on these nights: its τ is calibrated on the Georgia
vote-type gap); v5's bands are 3–8 % wider than v3's (β uncertainty), and never cover less than v3's. Win probability log score
(all rows, clipped 1e-4): v3 0.269, v5 0.275 (TX 0.052 → 0.103, NC 0.391 → 0.412, FL 0.131 → 0.139; AZ, GA, PA, NM slightly better); Brier 0.0816 vs 0.0827.

## Stacking v3 vs v5 (leave-one-cycle-out over 2020, 2022, 2024; all scenarios and checkpoints pooled)

| group | w (win-prob score) | LOCO log score v3 / v5 / blend | w (margin density) | LOCO margin log score v3 / v5 / blend |
|---|---|---|---|---|
| Hispanic-heavy (TX FL CA NM) | 0.00 | -0.101 / -0.116 / -0.101 | 0.00 | 2.380 / 2.358 / 2.378 |
| other (AZ NC GA PA) | 1.00 | -0.404 / -0.402 / -0.407 | 0.00 | 2.526 / 2.510 / 2.526 |
| TX | 0.00 | -0.052 / -0.103 / -0.052 | 0.20 | 2.733 / 2.709 / 2.704 |
| FL | 0.00 | -0.131 / -0.138 / -0.131 | 0.00 | 2.383 / 2.376 / 2.383 |
| CA | 0.00 | -0.032 / -0.035 / -0.032 | 0.00 | 2.100 / 2.074 / 2.100 |
| NM | 1.00 | -0.216 / -0.213 / -0.216 | 0.00 | 2.263 / 2.224 / 2.263 |
| AZ | 1.00 | -0.481 / -0.476 / -0.476 | 0.00 | 2.265 / 2.255 / 2.265 |
| GA | 1.00 | -0.421 / -0.415 / -0.429 | 0.00 | 2.606 / 2.589 / 2.606 |
| PA | 1.00 | -0.311 / -0.306 / -0.306 | 0.00 | 2.622 / 2.595 / 2.622 |
| NC | 0.00 | -0.391 / -0.412 / -0.391 | 0.00 | 2.657 / 2.650 / 2.657 |
| all 8 | 0.00 | -0.269 / -0.275 / -0.269 | 0.00 | 2.461 / 2.443 / 2.461 |

Win-probability weights are 0 or 1 everywhere and move the out-of-sample log score by less than 0.015 nats: the statewide win
probabilities of v3 and v5 are too alike on these nights to prefer either (most races are far from 50/50). A blend with these
weights is v3 for TX/FL/CA/NC and v5 for AZ/NM/GA/PA. `analysis/us_polls.py next_blend --races … --tau 0.2 --weights fl-gov=0.5,…`
writes v6 files for any weights (none are generated: no fitted weight is strictly between 0 and 1).

## Decision

v5 is not "clearly better": lower pooled error (−2 to −6 %; FL −18 %, CA −8 %, TX −5 %), no loss of coverage, ~0 effect where the effect is ~0, but the
win-probability log score is slightly worse (wider bands, TX stress order extrapolation) and the 85–95 % coverage criterion cannot be
tested because both models cover 99–100 % on these simulated nights. v3 stays primary; the v5 files are written as `role = "secondary"` for the
eight county-model races in TX, FL, CA, AZ, NM so that the 2026 count scores them live; whether FL/CA (where the gain is robust) deserve primary status is the integrator's call.

Caveats: ACS 2020–24 Hispanic shares are used for all backtest years (slow-moving, slight look-ahead); the simulated nights have no vote-type
partisan gap except the GA replay; v5's σ_mun uses the slope-adjusted residuals but τ (reporting-order term) was not re-calibrated for v5.
