# Count-night benchmarks: where the votes should land before they arrive (Senate, governor, House 2026)

**Status: a frozen pre-election artefact (2026-11-02), not a registered model and not used live.** It adds nothing to
`configs/` or the dashboards. No 2026 count data enters it; parameters are learned on 2014-2024 only. Rebuild:

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python -W ignore analysis/us_benchmarks.py build      # data_public/us2026_benchmarks.csv + data_public/us2026_benchmarks/<race>.json (deterministic)
uv run python -W ignore analysis/us_benchmarks.py backtest   # results/us_benchmarks/backtest_*.csv (2016-2024, 5-15 minutes)
uv run python -W ignore analysis/us_benchmarks.py tables     # results/us_benchmarks/doc_tables.md (every table below)
# inputs, rebuilt only if their sources change: ... order (needs ~/us2024_data), ... resid
```

Idea (from a user): deep prediction markets re-price abruptly when votes pile in. Compute *before* polls close where each county's
vote should land, how much a county moving off its benchmark should move the statewide estimate, and what the raw count will look
like if every county is on benchmark (the "blue/red mirage"), instead of only projecting once votes arrive.

## The plain answer

- **The artefact** covers every race with a unified prior: 31 statewide (Senate and governor) races and 75 House districts, 3,280
  county rows. No race lacks a unified prior (all 110 registered races are covered), so the registered-v3 fallback built into the
  script was not used. Per county: benchmark share and votes, 5-95 % band, expected report order and minutes, the nowcast's own
  sensitivity of the statewide margin and win probability to the county's result (direct and propagated part), an ex-ante "tell"
  score, and per race the benchmark raw-count path.
- **Backtest verdict (237 past statewide races of 2016-2024, leave-one-cycle-out county priors, simulated and real orders): the
  idea does not help beyond what the registered-style nowcast already does.** Using benchmarks to read the count beats reading the
  raw count by 2.7-7.6 points of final share at 5-50 % counted and beats the prior alone, but the "tell-weighted" estimate (only
  counties flagged as tells) is *worse* than the nowcast that uses every reported county (MAE +0.44 / +0.36 / +0.30 / +0.24 points
  at 5 / 10 / 25 / 50 % counted, bootstrap intervals exclude zero) and worse than the same estimator using all counted counties. The
  ex-ante tell score does predict which counties move the nowcast (Spearman about 0.28 with the realised |change|, 237 races), but
  county size alone predicts as well (0.31): a null result for "special tell counties". The table of benchmarks is still a
  publishable what-to-expect sheet; that is what it is shipped as.
- **What the raw count will show.** If every county lands on benchmark and counties report at the pace of the 2020 and 2024
  presidential nights, the raw margin sits far from the final benchmark margin early on (section 3): Texas, Kansas, Minnesota,
  New York, Colorado, Nebraska and Pennsylvania lean blue early (+15 to +27 points at 10 % counted); Georgia leans red (-17). That is geography and order only; the real mirage also contains early /
  mail / Election Day mixture inside counties, which this artefact does not model (section 5).

## 1. Method

**Inputs.** `data_public/us2026_unified_priors.csv` / `us2026_unified_house_priors.csv` (county prior: s0 = D/(D+R), V = D+R,
the unified challenger of `docs/us_unified.md`) and `configs/us2026_unified/**` (tau, rho, sigma_mun0, f_complete, vote_type,
early_share, swing_prior_sd, and `[prior]` statewide mean / sd / P(win)). Where the same prior source has a civicAPI and a recorded-feed config
(Ohio, South Carolina) the recorded-feed config's count settings are used. The nowcast is `brunder.us.project` (registered, unchanged).
Race-level statistics (mean, sd, P(win)) are the config's; `P(D) nowcast` in the table is the nowcast's own pre-count probability, which
also carries sigma_mun0 = 8 points of independent county noise and is therefore closer to 50 % than the config's.

**County band** (`resid_sd_pts`, `band_lo/hi`). The county error of the unified prior given the *true* statewide share, by county size, from
the leave-one-cycle-out county priors of 260 statewide races 2016-2024 (`us2026_benchmarks_resid.csv`; vote-weighted RMSE 2.03 points as in
`docs/us_unified.md`; 3.9 points for counties under 1,000 votes, 1.8 above 100,000). `band_lo/hi` = s0 +- 1.645 x that sd: where a county should land *if the
statewide result is on benchmark*. `band_state_lo/hi` adds the statewide sd (the whole map can shift together): s0 +- 1.645 x sqrt(statewide sd^2 + resid sd^2).

| bin_lo | bin_hi | n | median_V | rmse_pts | mae_pts |
|---|---|---|---|---|---|
| 0 | 1,000 | 671 | 680 | 3.92 | 2.90 |
| 1,000 | 2,500 | 1,636 | 1,731 | 3.63 | 2.76 |
| 2,500 | 5,000 | 2,433 | 3,700 | 3.35 | 2.56 |
| 5,000 | 10,000 | 3,186 | 7,156 | 3.14 | 2.32 |
| 10,000 | 25,000 | 3,716 | 14,948 | 2.71 | 2.00 |
| 25,000 | 50,000 | 1,740 | 34,234 | 2.47 | 1.70 |
| 50,000 | 100,000 | 1,143 | 69,216 | 2.05 | 1.55 |
| 100,000 | 1,000,000,000 | 1,477 | 199,884 | 1.81 | 1.38 |

**Expected reporting order** (`exp_rank`, `exp_cum_before/after`, `exp_minutes`, `p_early`). Empirical, from the count feeds of the 2020 (NYT/Edison
county feed) and 2024 (CNN/Edison) presidential nights (`us2026_benchmarks_order.csv`: when every county reached 10 / 25 / 50 / 75 / 95 / 100 % of its final
vote, UTC; 2024's clock is US Eastern in the file and converted). `score` = 2/3 mean percentile of the county's completion time in the cycles available
(both cycles for 30 of the 31 statewide races; Vermont's 2024 file is by town, so its order uses 2020 and size) + 1/3 percentile of its expected votes; `exp_rank` orders by it. How good
the order is: county size alone has a Spearman correlation of only 0.28-0.36 with completion time, one cycle's order predicts the other's at 0.44, the 2/3-1/3 blend at
0.44-0.48 (mean over states; `order_validation.csv`), so the order is uncertain and is treated as such (below). `exp_minutes` = geometric mean over the cycles of minutes
after poll close to 95 % of the county's vote (days for counties with late mail; presidential, mail-heavy 2020 included; indicative only). The simulated order of
`analysis/us_calibrate.py` (size-dependent, Georgia-calibrated) is used in the backtest, not in the 2026 table.

| target_cycle | counties | size | other_cycle | blend_0.25 | blend_0.50 | blend_0.67 | blend_0.75 |
|---|---|---|---|---|---|---|---|
| 2020 | 73 | 0.28 | 0.44 | 0.35 | 0.41 | 0.44 | 0.45 |
| 2024 | 73 | 0.36 | 0.44 | 0.42 | 0.47 | 0.48 | 0.48 |

**Sensitivity** (`dMargin_dshare`, `dP_dshare`). County c is fed to `brunder.us.project` as a complete county (f_complete or f_max of its expected vote, early batch
first when `vote_type`) at s0 +- 1 point, after the counties expected before it, all complete on benchmark, with the config's own tau, rho, sigma_mun0, swing prior and
count rules. `dMargin_dshare` = votes of statewide projected margin per +1 point of the county's share (central difference); `dP_dshare` = points of
P(win) per +1 point. Direct part = the county's own votes, 2 V per unit share (`dMargin_direct`, and `dP_direct` via the density at the projected
margin); propagated part = the rest (`dMargin_prop`, `dP_prop`): what the nowcast passes to the uncounted vote through the swing (swing weights, rho, the swing prior). The
margin responds linearly (|mean of the +/- responses - base| < 0.001 of the response) and its sd does not depend on the deviation. These are the *registered nowcast's reactions*
at the expected position, not statements about the truth: with one county counted the nowcast gives it the weight sd_prior^2/(sd_prior^2 + sigma_mun0^2) (about 0.1) whatever its size, so the first
counties move the projection by thousands of votes per point; late counties pass little because the margin is nearly decided (`dP_dshare` ~ 0).

**Tell** (`tell_margin`, `tell_P`, `*_per10k`, `p_early`, `tell_early`, `sd_reduction`, `tell_top`). Because the order is uncertain, the same sensitivity is averaged over 80 random orders around the
expected one (percentile score + N(0, 0.57^2) noise, the noise that reproduces the 0.45 order predictability; seeded by race id): the county completes after the counties before it on benchmark, its share is drawn
from N(s0, statewide sd^2 + resid sd^2) (the shared statewide shift plus the county's own residual), and `tell_margin` = expected |change of the projected statewide margin| in votes, `tell_P`
= expected |change of P(win)| in points, `*_per10k` per 10,000 votes of the county. `p_early` = share of orders in which the county completes within the first 40 % of the race's vote; `tell_early` = expected
|change of the margin| counted only when it reports early; `sd_reduction(_frac)` = mean fall of the projected margin sd when it reports (negative early: the nowcast adds reporting-order uncertainty
(`count_scaled_terms`) when the first counties arrive; 45 % of county rows). `tell_top` = top 5 by `tell_early` among counties with `p_early >= 0.5`. The orders use `fast_nowcast`, a vectorised copy of
`us.project` (one unit per county, or early / late units with `vote_type`), checked against `us.project` on random counted sets and all count-rule variants of the 2026 configs: max difference 5e-10 votes.

**Benchmark raw-count path** (JSON `benchmark_path`; q = 5 ... 90 % of the final vote counted). `raw_margin_pts` = D - R raw count margin (points of the two-party vote) if every county is on benchmark and has reported
its typical share of vote (the 2020 / 2024 presidential pace curves of its own county, averaged) when q of the race is in; `gap_pts` = that minus the final benchmark margin (+ = blue mirage), `minutes_after_close` the time q is reached.
`nowcast_p_win` / `nowcast_margin_sd_pts`: the nowcast's P(win) and margin sd at that state of the count with everything on benchmark (the number the model will show if nothing surprises).
`*_order` = same with whole counties in the expected order (an extreme: every county reports 100 % at once).

## 2. Files and columns

`data_public/us2026_benchmarks.csv`: one row per county (House: the county's part of the district), header lines `#`. `data_public/us2026_benchmarks/<race_id>.json` (House: `house_<st>-<nn>.json`): race summary, parameters used, order source,
`benchmark_path`, `top_tells`. Support: `us2026_benchmarks_order.csv`, `us2026_benchmarks_resid.csv`.

| column | meaning |
|---|---|
| race_id, unit_id, name, office, state, kind | race (`ga-gov`, `house:GA-07`), county (`us-<st>-<fips>`), `county` or `county_piece` |
| s0, V, turnout_share, margin_votes | benchmark two-party D share, expected two-party votes, V / race total, V (2 s0 - 1) |
| statewide_mean, statewide_sd, p_win | config `[prior]`: statewide share, its sd, P(D wins) |
| resid_sd_pts, band_lo, band_hi, band_state_lo, band_state_hi | county residual sd (points); 5-95 % bands (shares) given / with the statewide result |
| exp_rank, exp_cum_before, exp_cum_after, exp_minutes, order_source | expected order, share of the race's vote in before / after the county, minutes after close to 95 %, cycles behind the order |
| dMargin_dshare, dMargin_direct, dMargin_prop | votes of statewide margin per +1 point of the county's share (total / own votes / propagated) |
| dP_dshare, dP_direct, dP_prop | points of P(win) per +1 point (total / own votes / propagated) |
| p_early, tell_margin, tell_margin_per10k, tell_P, tell_P_per10k, tell_early | order-averaged tells (section 1) |
| sd_reduction, sd_reduction_frac | mean fall of the nowcast margin sd when the county reports (votes, fraction) |
| tell_rank, tell_top | rank among early counties by `tell_early`; 1 = top 5 |

## 3. The 2026 table (statewide)

`gap 10/25/50 %` = benchmark raw-count margin minus final benchmark margin in points (+ blue mirage) when 10 / 25 / 50 % of the vote is counted, counties on benchmark at typical pace. `P(D) at 50 %` =
the nowcast's P(D wins) at 50 % counted if all on benchmark. `tells` = first three `tell_top` counties. The House districts (75 districts, 536 county pieces, 194 tell flags) are in the CSV and JSON.

| race | counties | votes (k) | mean | sd | P(D) config | P(D) nowcast | benchmark margin | gap 10% | gap 25% | gap 50% | P(D) at 50% | tells |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| az-gov | 15 | 2,578 | 0.538 | 0.030 | 0.894 | 0.740 | +7.6 | +0.6 | +1.3 | +1.3 | 0.879 | Pinal, Yavapai, Mohave |
| ca-gov | 58 | 11,405 | 0.634 | 0.030 | 1.000 | 1.000 | +26.8 | -4.7 | +0.9 | +0.2 | 1.000 | Santa Cruz, El Dorado, Solano |
| co-gov | 64 | 2,429 | 0.621 | 0.074 | 0.948 | 0.940 | +24.2 | +15.0 | +1.6 | -1.1 | 1.000 | Douglas, Broomfield, Lake |
| co-sen | 64 | 2,417 | 0.614 | 0.074 | 0.938 | 0.929 | +22.9 | +15.3 | +1.6 | -1.1 | 1.000 | La Plata, Conejos, Phillips |
| fl-gov | 67 | 8,035 | 0.470 | 0.023 | 0.096 | 0.145 | -5.9 | -2.4 | +0.7 | +0.9 | 0.148 | Pinellas, Lake, Pasco |
| fl-sen | 67 | 8,753 | 0.464 | 0.028 | 0.099 | 0.134 | -7.2 | -2.1 | +0.9 | +0.8 | 0.108 | Pinellas, Pasco, Lake |
| ga-gov | 159 | 3,981 | 0.492 | 0.028 | 0.383 | 0.390 | -1.7 | -17.0 | -8.7 | -7.9 | 0.324 | Habersham, Oconee, McDuffie |
| ga-sen | 159 | 3,945 | 0.555 | 0.028 | 0.974 | 0.965 | +10.9 | -17.1 | -8.8 | -8.0 | 0.998 | Oconee, Greene, Douglas |
| ia-gov | 99 | 1,229 | 0.521 | 0.023 | 0.829 | 0.775 | +4.3 | +9.1 | +3.2 | +4.1 | 0.775 | Dallas, Cerro Gordo, Jones |
| ia-sen | 99 | 1,240 | 0.492 | 0.023 | 0.355 | 0.384 | -1.7 | +9.0 | +3.2 | +4.0 | 0.384 | Dallas, Cerro Gordo, Des Moines |
| ks-gov | 105 | 994 | 0.490 | 0.030 | 0.369 | 0.400 | -2.0 | +21.9 | +14.8 | +8.7 | 0.360 | Butler, Reno, Ellis |
| ks-sen | 105 | 992 | 0.490 | 0.030 | 0.370 | 0.400 | -2.0 | +22.7 | +15.3 | +9.0 | 0.361 | Reno, Wyandotte, Butler |
| mi-gov | 83 | 4,323 | 0.554 | 0.023 | 0.991 | 0.963 | +10.7 | +9.8 | +5.8 | -0.3 | 0.970 | Livingston, Eaton, Marquette |
| mi-sen | 83 | 4,488 | 0.531 | 0.023 | 0.913 | 0.845 | +6.1 | +9.6 | +5.8 | -0.4 | 0.858 | Livingston, Eaton, St. Joseph |
| mn-gov | 87 | 2,446 | 0.559 | 0.030 | 0.974 | 0.941 | +11.8 | +26.8 | +8.7 | +8.2 | 0.980 | Hennepin, Blue Earth, Steele |
| mn-sen | 87 | 2,563 | 0.544 | 0.030 | 0.925 | 0.878 | +8.7 | +26.5 | +8.4 | +8.0 | 0.936 | Rice, Nicollet, Blue Earth |
| nc-sen | 100 | 4,006 | 0.549 | 0.028 | 0.961 | 0.950 | +9.9 | +2.5 | +6.2 | -0.0 | 0.996 | Pitt, Catawba, Harnett |
| ne-gov | 93 | 681 | 0.453 | 0.030 | 0.061 | 0.131 | -9.4 | +14.5 | +10.7 | +5.9 | 0.052 | Richardson, Clay, Seward |
| ne-sen | 93 | 766 | 0.472 | 0.030 | 0.181 | 0.254 | -5.5 | +14.1 | +10.2 | +5.7 | 0.169 | Richardson, Clay, Merrick |
| nm-gov | 33 | 697 | 0.591 | 0.074 | 0.889 | 0.871 | +18.2 | -4.0 | -0.6 | -0.0 | 0.997 | Grant, Curry, Chaves |
| nm-sen | 33 | 739 | 0.601 | 0.048 | 0.983 | 0.963 | +20.3 | -4.0 | -0.5 | -0.1 | 0.999 | Grant, San Juan, Curry |
| ny-gov | 62 | 6,144 | 0.587 | 0.030 | 0.998 | 0.993 | +17.3 | +23.3 | +14.4 | +4.9 | 0.998 | Queens, Monroe, Chautauqua |
| oh-gov | 88 | 4,274 | 0.504 | 0.023 | 0.579 | 0.565 | +0.9 | +5.2 | +1.0 | -2.0 | 0.561 | Warren, Medina, Hancock |
| oh-sen | 88 | 4,595 | 0.513 | 0.023 | 0.713 | 0.677 | +2.5 | +5.4 | +1.1 | -2.0 | 0.667 | Warren, Medina, Trumbull |
| pa-gov | 67 | 5,333 | 0.617 | 0.030 | 1.000 | 1.000 | +23.4 | +14.8 | -1.5 | -4.7 | 1.000 | Lackawanna, Lancaster, Lycoming |
| sc-gov | 46 | 1,812 | 0.428 | 0.048 | 0.068 | 0.082 | -14.3 | +2.7 | -0.7 | -2.3 | 0.010 | Oconee, Chesterfield, Laurens |
| sc-sen | 46 | 2,568 | 0.473 | 0.030 | 0.190 | 0.226 | -5.3 | +2.6 | -0.8 | -2.2 | 0.188 | Chesterfield, Oconee, Laurens |
| tx-gov | 254 | 8,338 | 0.481 | 0.023 | 0.205 | 0.234 | -3.7 | +18.4 | +12.1 | +8.6 | 0.126 | Hopkins, Brazos, Nacogdoches |
| tx-sen | 254 | 9,112 | 0.507 | 0.023 | 0.631 | 0.615 | +1.5 | +18.5 | +12.2 | +8.5 | 0.679 | Collin, Williamson, Parker |
| vt-gov | 14 | 287 | 0.507 | 0.048 | 0.559 | 0.551 | +1.4 | -5.8 | -0.6 | -0.4 | 0.588 | Rutland, Washington, Windham |
| wi-gov | 72 | 2,617 | 0.533 | 0.030 | 0.862 | 0.826 | +6.6 | +10.9 | +8.1 | +3.2 | 0.868 | Dane, Rock, Jefferson |

Sanity check against the past (`analysis/us_benchmarks.py mirage`): the benchmark gap (geography and order only) correlates with the historical gap of the presidential raw count
(`site/data/us2026/tells.json`, mean of 2020 and 2024) at 0.6-0.73 across the 31 races but has about half of its size:

| q | races | corr | benchmark gap (mean) | historical gap (mean) |
|---|---|---|---|---|
| 10 | 31 | 0.60 | +7.8 | +11.9 |
| 25 | 31 | 0.68 | +4.1 | +8.4 |
| 50 | 31 | 0.73 | +1.6 | +4.1 |
| 75 | 30 | 0.66 | -0.3 | +0.1 |

## 4. Backtest (statewide races of 2016-2024)

Design. 260 past statewide races; the 237 with at least 10 counties. County priors are the unified LOCO priors of `analysis/us_unified_backtest.py` (predicted statewide share and sd, predicted county shares and votes; priors fitted without the race's cycle);
the county residual profile excludes the race's own cycle. Counties report *complete* at their certified result, in an order: **calibrate** = size-dependent random order of `analysis/us_calibrate.py` (60 nights per race), **weak** = size percentile + noise with
the empirical Spearman (about 0.35; 60 nights), **real** = the order of the same night's presidential count feed (2020 and 2024, 75 races, one order). At 5 / 10 / 25 / 50 % of the final vote counted (first prefix of the order reaching it) the
final two-party share is estimated by: (a) **raw** counted share; (b) **nowcast** = registered-style (`us.project` numbers: tau 0.04, rho 0.6, sigma_mun0 0.08, kappa 50,000, swing prior N(0, the race's LOCO sd), f_complete 0.98, learn_turnout), benchmark-adjusted by construction;
(c) **tell** = certified counted counties + benchmark for the others shifted by a Bayes-shrunk, sensitivity-weighted mean deviation of the *reported tell counties only* (top 5 per race by order-averaged `tell_early`, ex ante from the LOCO prior, weights = their `g_early`);
`tell_all` = the same estimator using every counted county (weights = votes); `benchmark` = the prior's statewide share (no count). Errors are race-level mean absolute errors over nights, in points of two-party share (margin = 2 x), races equally weighted.

Estimator MAE, **calibrate** order (237 races):

| check | raw | benchmark | nowcast | tell | tell_all |
|---|---|---|---|---|---|
| 0.05 | 9.58 | 2.80 | 1.58 | 2.02 | 1.25 |
| 0.1 | 8.20 | 2.80 | 1.34 | 1.70 | 1.07 |
| 0.25 | 5.84 | 2.80 | 0.93 | 1.23 | 0.77 |
| 0.5 | 3.42 | 2.80 | 0.53 | 0.77 | 0.47 |

Wrong-winner rate (estimate on the wrong side of 50 % of the final result):

| check | raw | benchmark | nowcast | tell | tell_all |
|---|---|---|---|---|---|
| 0.05 | 0.281 | 0.093 | 0.048 | 0.057 | 0.039 |
| 0.1 | 0.254 | 0.093 | 0.041 | 0.044 | 0.031 |
| 0.25 | 0.191 | 0.093 | 0.030 | 0.029 | 0.021 |
| 0.5 | 0.102 | 0.093 | 0.017 | 0.019 | 0.016 |

Weak-size order and the real 2020 / 2024 presidential order give the same ranking of the estimators (weak: tell 2.03 / 1.71 / 1.22 / 0.73 vs nowcast 1.57 / 1.34 / 0.93 / 0.51; real: 2.06 / 1.73 / 1.28 / 0.82 vs 1.79 / 1.64 / 1.13 / 0.64; `backtest_error_all_cycles.csv`). By cycle, calibrate order (2018 and 2022; 2016, 2020 and 2024 are in `backtest_error_by_cycle.csv`, same ranking):

| t | check | raw | benchmark | nowcast | tell | tell_all |
|---|---|---|---|---|---|---|
| 2018 | 0.05 | 9.70 | 2.05 | 1.35 | 1.77 | 1.31 |
| 2018 | 0.1 | 8.26 | 2.05 | 1.20 | 1.60 | 1.13 |
| 2018 | 0.25 | 5.84 | 2.05 | 0.89 | 1.27 | 0.81 |
| 2018 | 0.5 | 3.28 | 2.05 | 0.53 | 0.80 | 0.49 |
| 2022 | 0.05 | 10.24 | 2.60 | 1.51 | 1.84 | 1.03 |
| 2022 | 0.1 | 8.77 | 2.60 | 1.27 | 1.56 | 0.88 |
| 2022 | 0.25 | 6.16 | 2.60 | 0.85 | 1.10 | 0.66 |
| 2022 | 0.5 | 3.54 | 2.60 | 0.49 | 0.67 | 0.41 |

Real presidential-night order, 2020 and 2024:

| t | check | raw | benchmark | nowcast | tell | tell_all |
|---|---|---|---|---|---|---|
| 2020 | 0.05 | 10.85 | 3.90 | 2.22 | 2.50 | 1.66 |
| 2020 | 0.1 | 10.01 | 3.90 | 1.97 | 1.98 | 1.46 |
| 2020 | 0.25 | 7.39 | 3.90 | 1.30 | 1.35 | 0.94 |
| 2020 | 0.5 | 3.69 | 3.90 | 0.62 | 0.80 | 0.45 |
| 2024 | 0.05 | 11.77 | 2.30 | 1.32 | 1.59 | 1.25 |
| 2024 | 0.1 | 11.00 | 2.30 | 1.28 | 1.46 | 1.18 |
| 2024 | 0.25 | 8.25 | 2.30 | 0.95 | 1.21 | 0.83 |
| 2024 | 0.5 | 4.75 | 2.30 | 0.65 | 0.83 | 0.61 |

**Does (c) beat (a) and (b) at the same % counted?** Paired by race (bootstrap 95 % interval over races; tell_minus_X < 0 = the tell-weighted estimate is better):

| subset | races | check | raw | benchmark | nowcast | tell_all |
|---|---|---|---|---|---|---|
| all 5 cycles | 237 | 0.05 | -7.56 [-8.07, -7.06] | -0.78 [-1.01, -0.58] | +0.44 [+0.34, +0.54] | +0.77 [+0.62, +0.91] |
| all 5 cycles | 237 | 0.10 | -6.49 [-6.94, -6.05] | -1.10 [-1.37, -0.86] | +0.36 [+0.27, +0.45] | +0.63 [+0.52, +0.76] |
| all 5 cycles | 237 | 0.25 | -4.61 [-4.95, -4.28] | -1.57 [-1.90, -1.26] | +0.30 [+0.22, +0.38] | +0.46 [+0.39, +0.53] |
| all 5 cycles | 237 | 0.50 | -2.65 [-2.87, -2.46] | -2.03 [-2.38, -1.69] | +0.24 [+0.18, +0.30] | +0.29 [+0.24, +0.34] |
| 2018 | 58 | 0.05 | -7.93 [-8.84, -7.03] | -0.28 [-0.60, +0.00] | +0.41 [+0.22, +0.63] | +0.45 [+0.19, +0.73] |
| 2018 | 58 | 0.10 | -6.66 [-7.44, -5.82] | -0.45 [-0.85, -0.10] | +0.40 [+0.23, +0.59] | +0.47 [+0.27, +0.69] |
| 2018 | 58 | 0.25 | -4.56 [-5.20, -3.96] | -0.77 [-1.23, -0.36] | +0.39 [+0.22, +0.55] | +0.47 [+0.31, +0.63] |
| 2018 | 58 | 0.50 | -2.48 [-2.83, -2.12] | -1.25 [-1.73, -0.82] | +0.27 [+0.17, +0.39] | +0.31 [+0.20, +0.42] |
| 2022 | 62 | 0.05 | -8.40 [-9.55, -7.26] | -0.76 [-1.09, -0.44] | +0.33 [+0.16, +0.50] | +0.81 [+0.54, +1.09] |
| 2022 | 62 | 0.10 | -7.22 [-8.17, -6.28] | -1.04 [-1.47, -0.65] | +0.29 [+0.13, +0.45] | +0.68 [+0.48, +0.91] |
| 2022 | 62 | 0.25 | -5.05 [-5.75, -4.32] | -1.49 [-2.05, -0.97] | +0.26 [+0.13, +0.39] | +0.45 [+0.31, +0.60] |
| 2022 | 62 | 0.50 | -2.87 [-3.28, -2.47] | -1.93 [-2.56, -1.39] | +0.19 [+0.11, +0.27] | +0.26 [+0.18, +0.35] |
| close races (margin under 10 pts) | 80 | 0.05 | -8.27 [-8.98, -7.47] | -0.55 [-0.90, -0.29] | +0.28 [+0.15, +0.42] | +0.46 [+0.29, +0.65] |
| close races (margin under 10 pts) | 80 | 0.10 | -6.82 [-7.42, -6.20] | -0.79 [-1.17, -0.49] | +0.22 [+0.09, +0.36] | +0.38 [+0.23, +0.54] |
| close races (margin under 10 pts) | 80 | 0.25 | -4.42 [-4.88, -3.88] | -1.10 [-1.51, -0.72] | +0.23 [+0.09, +0.38] | +0.33 [+0.23, +0.45] |
| close races (margin under 10 pts) | 80 | 0.50 | -2.43 [-2.74, -2.11] | -1.47 [-1.96, -1.08] | +0.21 [+0.11, +0.34] | +0.26 [+0.16, +0.37] |

(a) yes, by a wide margin (the raw count carries the order mirage: bias -7 points of share at 5 % counted under the calibrated order). (b) no: the tell-weighted estimate is worse than the nowcast at every checkpoint (also in 2018 and 2022 separately, close races, and under the weak and
real orders) and the nowcast is the better of the two in 77 % of races at 5 % counted. Selecting tells discards information: the same estimator on all counted counties (`tell_all`) is 0.3-0.8 points better than on tells only, and is also better than the registered-style nowcast.
That last comparison must not be read as an argument to retune the registered nowcast: here every county reports complete at its certified share, with no early / late vote-type bias inside the county, which the nowcast's sigma_mun0 = 8 points (against 2-4 points here)
is there to absorb.

**Does the ex-ante tell score predict a county's realised information?** Realised information = |change of the nowcast's projected share| when the county reports in the first 40 % of the vote, in 10 simulated nights per race. Spearman correlation across the counties of a night, averaged over race x nights:

| scheme | t | race_sims | races | rho_tell | rho_tell_early | rho_tellP | rho_size | rho_sens |
|---|---|---|---|---|---|---|---|---|
| calibrate | 2018 | 575 | 58 | 0.287 | 0.228 | 0.192 | 0.320 | 0.312 |
| calibrate | 2022 | 605 | 62 | 0.310 | 0.255 | 0.202 | 0.316 | 0.315 |
| weak | 2018 | 551 | 58 | 0.340 | 0.264 | 0.212 | 0.384 | 0.369 |
| weak | 2022 | 598 | 62 | 0.354 | 0.284 | 0.218 | 0.385 | 0.369 |
| calibrate | all | 2325 | 237 | 0.281 | 0.229 | 0.167 | 0.305 | 0.298 |
| real | all | 71 | 71 | 0.260 | 0.218 | 0.158 | 0.293 | 0.278 |
| weak | all | 2272 | 237 | 0.324 | 0.262 | 0.185 | 0.367 | 0.348 |

`rho_tell` = order-averaged `tell_margin`, `rho_tell_early` = the ranking quantity `tell_early`, `rho_tellP` = `tell_P`, `rho_size` = county size, `rho_sens` = the expected sensitivity. The score carries real information (0.26-0.32 over all cycles), but it is no better than county size (0.29-0.37):
what moves the projection is how many votes a county brings; the nowcast has no memory of anything else. Flagged tells do move the projection more (0.19 vs 0.11 points of share per county under the calibrated order) but they are larger counties (28k vs 22k votes);
per 10,000 votes the difference is small (0.34 vs 0.29 points):

| scheme | flag | counties | info_pts_mean | info_pts_per10k_mean | mean_V |
|---|---|---|---|---|---|
| calibrate | False | 100517 | 0.114 | 0.291 | 21,547 |
| calibrate | True | 8167 | 0.186 | 0.337 | 27,797 |
| real | False | 3101 | 0.112 | 0.297 | 25,472 |
| real | True | 236 | 0.148 | 0.307 | 32,339 |
| weak | False | 82615 | 0.133 | 0.300 | 26,199 |
| weak | True | 6623 | 0.202 | 0.357 | 29,814 |

**County bands.** Share of past county results inside the benchmark band (profile fitted without the race's cycle; `band` given the true statewide share, `band_state` with the statewide sd and the predicted statewide share):

| cycle / votes of the county | counties | cover_given_state | cover_with_state_sd |
|---|---|---|---|
| 2016 | 2511 | 0.729 | 0.709 |
| 2018 | 3945 | 0.915 | 0.934 |
| 2020 | 2824 | 0.948 | 0.861 |
| 2022 | 4063 | 0.967 | 0.974 |
| 2024 | 2538 | 0.934 | 0.939 |
| all | 15881 | 0.908 | 0.897 |
| 0-1,000 | 671 | 0.909 | 0.925 |
| 1,000-2,500 | 1636 | 0.906 | 0.886 |
| 2,500-5,000 | 2433 | 0.894 | 0.868 |
| 5,000-10,000 | 3186 | 0.915 | 0.883 |
| 10,000-25,000 | 3710 | 0.913 | 0.892 |
| 25,000-50,000 | 1715 | 0.919 | 0.917 |
| 50,000-100,000 | 1105 | 0.895 | 0.937 |
| 100,000+ | 1425 | 0.899 | 0.929 |

Over 15,881 counties the nominal 90 % band holds (0.91 given the statewide result, 0.90 with the statewide sd), by size within 0.87-0.94; by cycle 2016 under-covers badly (0.73 / 0.71: the 2016 county map swung away from the model) and 2022 over-covers (0.97).
A band is a statement about an average cycle, not about 2026's.

## 5. Honest reading

- **Use the artefact as a what-to-expect table, not as a better estimator.** The sensitivities and tells describe how the *registered nowcast* will move, not where the truth is; the backtest says the nowcast
  that uses all reported counties is the better reader of the count and that tell counties add nothing beyond size. If a county is off its band, the statewide estimate moves by `dMargin_dshare` per point, whether or not it is a "tell".
- **The benchmark path is geography and order only.** The raw-count gap of a real night also contains the within-county mixture of early, mail and Election Day votes (e.g. Georgia posts each county's early and mail batch first and the Election Day precincts later, with a different lean; `docs/us_midterms.md`, `analysis/us_calibrate.py`), which this table does not model (the section 3 check shows the order effect alone reproduces about half of the historical 2020 / 2024 presidential gap). Expect real
  gaps with the same sign and larger size where mail or early votes post in blocks.
- **The order is a 2020 / 2024 presidential estimate** (one mail-heavy COVID cycle and one cycle with overcounted county files, order predicted at about 0.45 Spearman). A midterm count can reorder counties; the tells are averaged over 80 random orders for that reason, the path is not.
- **Sensitivities are conditional on the expected position.** After the counties expected before it, all on benchmark. A county reporting earlier or later, or after counties that are off benchmark, has a different weight (the nowcast's weight on a county depends on how much is already counted);
  `dP_dshare` is ~0 for counties expected last in a race the nowcast has already decided (Harris TX, Wayne MI). Single-county House districts: P is a step in the final result, hence `dP_dshare` up to 50 points per point.
- **House county pieces** use the same size profile (extrapolated below the smallest backtest counties; pieces were not backtested) and the district's statewide sd; the House configs use tau 0.01. The 75 districts are published with the same columns; treat their tells as indicative.
- **Backtest limits.** Complete-county reports only (no partial counts, no within-county mirage); the nowcast with fixed registered-style parameters (not the 2026 per-state ones); LOCO county priors from the unified model; races equally weighted; nights simulated except the 75 real-order races (one order each).
  2016 is a weak cycle for the band.

## 6. Using it on the night

1. Find the race and the county. If its reported share (when its count is essentially complete) is inside `band_lo / band_hi`, it is on benchmark and the statewide estimate should not move beyond what the path says; outside it, the projected statewide margin moves by about
   `dMargin_dshare` x (points off s0) votes and P(win) by `dP_dshare` x (points off s0), valid if the counties before it were on benchmark (otherwise use the live `project` output).
2. Read the early raw count against `benchmark_path` of the race JSON: at 10 / 25 / 50 % counted the raw margin *should* be `raw_margin_pts` if everyone is on benchmark. A raw count equal to that path is not news; a move of the market toward the raw count of a region whose gap is large is the mirage.
3. The model's own P(win) with everything on benchmark at that point is `nowcast_p_win` in the path (the pre-count P drifts toward the benchmark margin's side as sd falls: Georgia governor 0.39 pre-count, 0.32 at 50 %).
4. Do not trust the first tiny counties: `tell_early` ranks counties by expected move, but in the backtest large counties and the estimator over all reported counties are the better reading.

## 7. Files

`analysis/us_benchmarks.py`; `data_public/us2026_benchmarks.csv`, `data_public/us2026_benchmarks/*.json`, `data_public/us2026_benchmarks_order.csv`, `data_public/us2026_benchmarks_resid.csv`; `results/us_benchmarks/*.csv` (gitignored; every table above is rendered from them by `us_benchmarks.py tables`).
Licences: the benchmarks derive from the unified priors (MEDSL CC0, state records, Census, VoteHub CC BY 4.0, FiveThirtyEight CC BY 4.0) and the NYT/Edison 2020 and CNN/Edison 2024 county count feeds (Harvard Dataverse, CC0) for the order only.
