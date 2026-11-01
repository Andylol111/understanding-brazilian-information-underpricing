# US benchmark: every version side by side (statewide v1-v5, unified; House v1, v2, unified)

## 0. Pre-registered selection rule (written and committed BEFORE any benchmark number was computed)

**Status-quo-favouring by design.** The pooled pre-count numbers for v1, v3 and the unified challenger (statewide) and for
House v1/v2/unified had already been seen before this rule was written (`docs/us_unified.md`). Nothing below was tuned on
them, but the rule is deliberately conservative: it can only keep the primary or replace it on strong evidence.

1. **Primary (headline on every surface):** v3 for statewide races (v4 in Ohio, as registered), House v2. v1, v2, v4, v5 and the
   unified model are candidates.
2. **A candidate is recommended for a race class instead of the primary only if ALL hold** (otherwise "keep primary"):
   (a) the class has at least **30 race-cycles** in the paired set (fixed now); (b) the candidate's improvement over the primary on the class's
   metric has a **two-sided bootstrap p-value that survives Holm correction at 0.05 across EVERY comparison run** in
   this benchmark (versions x metrics x classes x class schemes, losers and discarded schemes included; the full log is
   `data_public/us_benchmark_comparisons.csv`); (c) the improvement has the same sign in at least two thirds of the
   cycles that hold >= 5 races of the class; (d) for statewide win probabilities the candidate's Brier score is not worse
   than the primary's and its 90 % coverage is >= 0.80; (e) it must also win against the primary's registered-constant
   uncertainty (sensitivity, see 4).
3. **Out of sample only.** Every candidate prediction is leave-one-cycle-out (LOCO, held-out cycle never in the fit; unified uses
   the `analysis/us_unified_backtest.py` nested fits). The v1..v5 rules have no free parameter except the registered
   constants (poll sd 0.039, sd bins 0.0429/0.0334/0.0228 estimated on 2016-2022 polls: in-sample for 2016-2022, out of sample for
   2024; this flatters v2/v3 and so also favours the status quo).
4. **Primary baseline for v3's uncertainty = v3 with its sd refit leave-one-cycle-out by poll-count bin** (the fair out-of-sample
   comparison); the registered-constant v3 is the sensitivity (condition (e)).
5. **Metrics (one primary metric per layer to limit multiplicity):** statewide = CRPS of the share forecast (Normal(mu, sd), points);
   county share = race-mean vote-weighted county share MAE (points); county expected vote = race-mean vote-weighted absolute
   percentage error of the county expected two-party vote (level included). Brier, log loss, coverage, bias are reported but not
   tested. House = CRPS of the district share forecast.
6. **Class schemes (all run, all logged):** C1 composite volatility tier from pre-count observables = number of flags among
   {polls in window <= 3 (unpolled counts), |poll average - last same-office result| >= 6 points, state's own mean |poll miss|
   in earlier cycles >= 3 points or fewer than 2 earlier polled races}; low = 0 flags, medium = 1, **high = 2+**. C2 poll count
   (0 / 1-5 / 6+). C3 |poll average - v1| (<3 / 3-6 / >=6 points, polled only). C4 state historical poll miss (<2 / 2-4 / >=4 points
   or no history). House: H1 polled vs unpolled, H2 competitiveness |last president - 0.5| (<0.05 / 0.05-0.15 / >0.15). C1 and
   H1 are the headline schemes; the others are robustness (they still enter Holm).
7. **Bootstrap:** 20,000 resamples of races within cycle (paired by race), two-sided p = 2 min(P(delta <= 0), P(delta >= 0)),
   95 % interval shown unadjusted and Holm-adjusted p given. Races within a cycle share a national shock, so cycle-level consistency (c)
   is part of the rule; five cycles cannot support a cycle-level bootstrap.
8. Versions whose pre-count forecast is identical by construction are reported as one row and not tested twice: statewide share and sd of v3 = v4 = v5
   (v4 re-centres its swing prior on its own expected-vote weights "so it still starts at the poll average"; v5 equals v3 "exactly"
   with nothing counted, `docs/us_hisp_slope.md`); county share of v2 = v3 = v5. v2 differs from v3 only in its sd.
9. The benchmark pick per class is always reported as a **secondary**, with interval, whether or not the rule recommends a change.


## 1. What is compared, and how each version is rebuilt for past cycles

All versions are scored on the same races: the 264 statewide Senate/governor races of 2016-2024 (219 with polls; 5 cycles; specials included, runoffs excluded, the `analysis/us_unified_backtest.py` race panel) and the 2,223 contested House districts of 2014-2024 (same-line transitions, `analysis/us_unified_house_2026.py`). There are no registered configs for past cycles, so each version is rebuilt by rule from its 2026 definition with information available before the election.

| version | pre-count forecast for a past race | note |
|---|---|---|
| v1 | last same-office result (else last president); county shares = last same-office county result; county votes = that race's votes (midterm base x1.0, president base x0.72 for a midterm target, as the registered configs) | sd: leave-one-cycle-out RMSE by poll-count bin (the best case for v1; v1 itself has no pre-count uncertainty) |
| v2 | poll average (v1 where unpolled), county shares shifted uniformly to it, sd = sqrt(0.039^2 + sampling), sampling approximated as 0.25/(700 x polls) (past poll sample sizes are not stored) | differs from v3 only in sd |
| v3 (primary) | same mean as v2; sd = historical RMSE of a poll average with that many polls | two variants: **registered sd bins** (0.0429 / 0.0334 / 0.0228, estimated on 2016-2022 polls, in-sample there) and **LOCO-refit sd** (fair, the primary baseline of the rule) |
| v4 | statewide forecast identical to v3 (swing prior re-centred on its own expected-vote weights "so it still starts at the poll average"); county votes = last-president votes x the county's own previous-midterm drop-off (top race of t-4 / president of t-6), relative to the state, capped x0.61-x1.65, state ratio where missing | reconstructible for **2022 only** (2018 top race / 2016 president); 2018 would need 2014 county returns, absent from the panel; no drop-off exists in presidential years |
| v5 | identical to v3 with nothing counted (`docs/us_hisp_slope.md`: pre-count forecast and variance exactly v3's); the Hispanic slope acts only during the count | its past-cycle reconstruction is part 5 (count stage), with the Hispanic share from the ACS vintage ending t-2 |
| unified | nested leave-one-cycle-out fits of `analysis/us_unified_backtest.py` (state mean/sd, county share, county turnout share, statewide votes) | never the saved final-fit parameters |
| House v1 / v2 | v1 = last president by district, no shift; v2 = + generic-ballot shift of that cycle (registered sd 0.052; sd refit LOCO is the primary baseline) | unified = `analysis/us_unified_house_2026.py` LOCO |

Identities that follow from the registered definitions (and so are not tested twice): statewide share and sd of v3 = v4 = v5; county share of v2 = v3 = v5; county expected votes of v1 = v2 = v3 = v5. Presidential cycles (2016, 2020, 2024) are included for share errors; for expected votes only the midterm cycles (2018, 2022) are scored because the registered turnout rules are midterm rules.

Definitions of scores: share error in points of two-party Democratic share; P(win) = Phi((mean - 0.5)/sd); CRPS of Normal(mean, sd); coverage = share of races inside +-1.645 sd; county metrics vote-weighted, averaged over races; EV wMAPE = sum over counties |V_hat - V| / sum V (level error included).

## 2. Part A: pre-count, leave-one-cycle-out 2016-2024 (analysis/us_benchmark.py)

### 2.1 Statewide share and win probability, pooled

| model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|
| v1 | 264 | 5.726 | 0.135 | 4.365 | 0.124 | 0.428 | 0.890 |
| v2 (sd = poll sd) | 264 | 3.107 | 1.484 | 2.308 | 0.062 | 0.208 | 0.932 |
| v3 (sd refit LOCO) | 264 | 3.107 | 1.484 | 2.286 | 0.059 | 0.193 | 0.879 |
| v3 (registered sd bins) | 264 | 3.107 | 1.484 | 2.272 | 0.059 | 0.192 | 0.879 |
| unified | 264 | 2.821 | 1.093 | 2.115 | 0.055 | 0.174 | 0.886 |

By cycle (all 264 races):

| cycle | model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|---|
| 2016 | v1 | 43 | 5.766 | 3.662 | 4.309 | 0.184 | 0.519 | 0.907 |
| 2016 | v2 (sd = poll sd) | 43 | 2.836 | 2.333 | 2.038 | 0.082 | 0.242 | 0.884 |
| 2016 | v3 (sd refit LOCO) | 43 | 2.836 | 2.333 | 2.001 | 0.087 | 0.244 | 0.860 |
| 2016 | v3 (registered sd bins) | 43 | 2.836 | 2.333 | 2.001 | 0.088 | 0.248 | 0.860 |
| 2016 | unified | 43 | 2.879 | 2.143 | 2.058 | 0.084 | 0.238 | 0.837 |
| 2018 | v1 | 66 | 6.603 | -4.527 | 4.911 | 0.164 | 0.646 | 0.818 |
| 2018 | v2 (sd = poll sd) | 66 | 2.361 | -0.400 | 1.785 | 0.074 | 0.243 | 0.985 |
| 2018 | v3 (sd refit LOCO) | 66 | 2.361 | -0.400 | 1.741 | 0.071 | 0.229 | 0.985 |
| 2018 | v3 (registered sd bins) | 66 | 2.361 | -0.400 | 1.719 | 0.071 | 0.225 | 0.955 |
| 2018 | unified | 66 | 1.990 | -0.403 | 1.614 | 0.076 | 0.228 | 0.939 |
| 2020 | v1 | 43 | 6.728 | -0.173 | 5.002 | 0.085 | 0.283 | 0.860 |
| 2020 | v2 (sd = poll sd) | 43 | 4.592 | 3.120 | 3.162 | 0.068 | 0.225 | 0.930 |
| 2020 | v3 (sd refit LOCO) | 43 | 4.592 | 3.120 | 3.252 | 0.068 | 0.213 | 0.744 |
| 2020 | v3 (registered sd bins) | 43 | 4.592 | 3.120 | 3.213 | 0.069 | 0.215 | 0.814 |
| 2020 | unified | 43 | 3.729 | 2.971 | 2.664 | 0.046 | 0.157 | 0.791 |
| 2022 | v1 | 68 | 5.815 | 2.752 | 4.419 | 0.114 | 0.383 | 0.912 |
| 2022 | v2 (sd = poll sd) | 68 | 3.273 | 2.017 | 2.570 | 0.048 | 0.178 | 0.882 |
| 2022 | v3 (sd refit LOCO) | 68 | 3.273 | 2.017 | 2.517 | 0.040 | 0.149 | 0.824 |
| 2022 | v3 (registered sd bins) | 68 | 3.273 | 2.017 | 2.515 | 0.040 | 0.148 | 0.824 |
| 2022 | unified | 68 | 3.081 | 0.682 | 2.326 | 0.037 | 0.128 | 0.897 |
| 2024 | v1 | 44 | 3.257 | -0.062 | 2.893 | 0.058 | 0.221 | 0.977 |
| 2024 | v2 (sd = poll sd) | 44 | 2.784 | 1.057 | 2.116 | 0.040 | 0.152 | 0.977 |
| 2024 | v3 (sd refit LOCO) | 44 | 2.784 | 1.057 | 2.081 | 0.035 | 0.135 | 0.955 |
| 2024 | v3 (registered sd bins) | 44 | 2.784 | 1.057 | 2.074 | 0.034 | 0.132 | 0.932 |
| 2024 | unified | 44 | 2.723 | 1.113 | 2.060 | 0.031 | 0.117 | 0.932 |

v4 and v5 are v3 at this level. The poll-based versions carry a Democratic bias of about 1.5 points (poll miss of 2016-2024), unified about 1.1.

### 2.2 By volatility class (C1: composite tier from pre-count observables; flags = polls in window <= 3 incl. unpolled, |poll average - last result| >= 6 points, state's earlier mean |poll miss| >= 3 points or fewer than 2 earlier races)

| class | model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|---|
| high | v1 | 65 | 8.273 | 1.993 | 6.018 | 0.058 | 0.240 | 0.831 |
| high | v2 (sd = poll sd) | 65 | 4.283 | 2.696 | 3.165 | 0.020 | 0.080 | 0.831 |
| high | v3 (sd refit LOCO) | 65 | 4.283 | 2.696 | 3.181 | 0.018 | 0.071 | 0.800 |
| high | v3 (registered sd bins) | 65 | 4.283 | 2.696 | 3.164 | 0.017 | 0.070 | 0.785 |
| high | unified | 65 | 4.239 | 2.036 | 3.140 | 0.010 | 0.047 | 0.862 |
| low | v1 | 102 | 2.916 | 0.745 | 2.437 | 0.147 | 0.453 | 1.000 |
| low | v2 (sd = poll sd) | 102 | 1.991 | 1.004 | 1.503 | 0.091 | 0.294 | 0.980 |
| low | v3 (sd refit LOCO) | 102 | 1.991 | 1.004 | 1.431 | 0.089 | 0.270 | 0.922 |
| low | v3 (registered sd bins) | 102 | 1.991 | 1.004 | 1.420 | 0.090 | 0.272 | 0.912 |
| low | unified | 102 | 1.822 | 0.835 | 1.332 | 0.087 | 0.256 | 0.912 |
| medium | v1 | 97 | 6.976 | -1.752 | 5.284 | 0.144 | 0.526 | 0.814 |
| medium | v2 (sd = poll sd) | 97 | 3.493 | 1.175 | 2.580 | 0.059 | 0.204 | 0.948 |
| medium | v3 (sd refit LOCO) | 97 | 3.493 | 1.175 | 2.586 | 0.056 | 0.192 | 0.887 |
| medium | v3 (registered sd bins) | 97 | 3.493 | 1.175 | 2.572 | 0.056 | 0.189 | 0.907 |
| medium | unified | 97 | 2.922 | 0.734 | 2.252 | 0.052 | 0.173 | 0.876 |

Descriptive bootstrap intervals for the win-probability metrics (positive = candidate better than v3; **not** in the Holm family):

| scheme | class | candidate | abserr | brier | logloss |
|---|---|---|---|---|---|
| C1 | high | unified | +0.045 [-1.004, +0.995] | +0.008 [-0.009, +0.026] | +0.024 [-0.019, +0.070] |
| C1 | high | v1 | -3.989 [-5.622, -2.452] | -0.041 [-0.084, -0.007] | -0.168 [-0.358, -0.040] |
| C1 | low | unified | +0.169 [+0.035, +0.306] | +0.002 [-0.011, +0.013] | +0.014 [-0.021, +0.043] |
| C1 | low | v1 | -0.925 [-1.312, -0.549] | -0.058 [-0.084, -0.030] | -0.183 [-0.244, -0.119] |
| C1 | medium | unified | +0.571 [+0.185, +0.955] | +0.005 [-0.014, +0.022] | +0.019 [-0.023, +0.060] |
| C1 | medium | v1 | -3.483 [-4.611, -2.441] | -0.087 [-0.137, -0.040] | -0.334 [-0.570, -0.149] |
| pooled | all races | unified | +0.286 [-0.016, +0.571] | +0.004 [-0.005, +0.014] | +0.019 [-0.005, +0.040] |
| pooled | all races | v1 | -2.619 [-3.250, -2.023] | -0.065 [-0.089, -0.041] | -0.235 [-0.336, -0.151] |

Single-observable class schemes (robustness; they are in the Holm family too):

| class | model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|---|
| 1-5 polls | v1 | 91 | 6.574 | 0.735 | 5.009 | 0.073 | 0.268 | 0.890 |
| 1-5 polls | v2 (sd = poll sd) | 91 | 3.058 | 2.010 | 2.220 | 0.015 | 0.064 | 0.901 |
| 1-5 polls | v3 (sd refit LOCO) | 91 | 3.058 | 2.010 | 2.228 | 0.014 | 0.060 | 0.857 |
| 1-5 polls | v3 (registered sd bins) | 91 | 3.058 | 2.010 | 2.203 | 0.014 | 0.057 | 0.868 |
| 1-5 polls | unified | 91 | 3.024 | 1.537 | 2.261 | 0.010 | 0.044 | 0.857 |
| 6+ polls | v1 | 128 | 5.095 | -0.705 | 3.861 | 0.191 | 0.638 | 0.891 |
| 6+ polls | v2 (sd = poll sd) | 128 | 2.193 | 1.170 | 1.602 | 0.105 | 0.330 | 0.969 |
| 6+ polls | v3 (sd refit LOCO) | 128 | 2.193 | 1.170 | 1.550 | 0.100 | 0.302 | 0.891 |
| 6+ polls | v3 (registered sd bins) | 128 | 2.193 | 1.170 | 1.540 | 0.101 | 0.302 | 0.883 |
| 6+ polls | unified | 128 | 2.041 | 0.810 | 1.421 | 0.098 | 0.289 | 0.898 |
| unpolled | v1 | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v2 (sd = poll sd) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v3 (sd refit LOCO) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v3 (registered sd bins) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | unified | 45 | 4.630 | 1.000 | 3.795 | 0.022 | 0.108 | 0.911 |

| class | model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|---|
| 3-6 pts | v1 | 45 | 4.373 | 1.324 | 3.153 | 0.166 | 0.493 | 1.000 |
| 3-6 pts | v2 (sd = poll sd) | 45 | 2.531 | 1.996 | 1.827 | 0.094 | 0.284 | 0.956 |
| 3-6 pts | v3 (sd refit LOCO) | 45 | 2.531 | 1.996 | 1.812 | 0.101 | 0.288 | 0.867 |
| 3-6 pts | v3 (registered sd bins) | 45 | 2.531 | 1.996 | 1.809 | 0.102 | 0.290 | 0.844 |
| 3-6 pts | unified | 45 | 2.196 | 1.585 | 1.565 | 0.095 | 0.266 | 0.911 |
| <3 pts | v1 | 97 | 2.568 | 0.794 | 2.350 | 0.096 | 0.327 | 1.000 |
| <3 pts | v2 (sd = poll sd) | 97 | 2.198 | 0.878 | 1.621 | 0.059 | 0.208 | 0.969 |
| <3 pts | v3 (sd refit LOCO) | 97 | 2.198 | 0.878 | 1.561 | 0.053 | 0.180 | 0.938 |
| <3 pts | v3 (registered sd bins) | 97 | 2.198 | 0.878 | 1.546 | 0.053 | 0.179 | 0.928 |
| <3 pts | unified | 97 | 2.085 | 0.750 | 1.520 | 0.056 | 0.178 | 0.928 |
| >=6 pts | v1 | 77 | 10.45 | -2.078 | 7.535 | 0.187 | 0.677 | 0.688 |
| >=6 pts | v2 (sd = poll sd) | 77 | 3.011 | 2.048 | 2.176 | 0.062 | 0.198 | 0.896 |
| >=6 pts | v3 (sd refit LOCO) | 77 | 3.011 | 2.048 | 2.185 | 0.057 | 0.177 | 0.805 |
| >=6 pts | v3 (registered sd bins) | 77 | 3.011 | 2.048 | 2.159 | 0.056 | 0.173 | 0.831 |
| >=6 pts | unified | 77 | 3.057 | 1.293 | 2.204 | 0.049 | 0.153 | 0.805 |
| unpolled | v1 | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v2 (sd = poll sd) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v3 (sd refit LOCO) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | v3 (registered sd bins) | 45 | 5.807 | 1.311 | 4.495 | 0.034 | 0.151 | 0.889 |
| unpolled | unified | 45 | 4.630 | 1.000 | 3.795 | 0.022 | 0.108 | 0.911 |

| class | model | n | mae_pts | bias_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|---|
| 2-4 pts | v1 | 110 | 6.466 | 0.786 | 4.936 | 0.090 | 0.381 | 0.873 |
| 2-4 pts | v2 (sd = poll sd) | 110 | 3.897 | 2.754 | 2.850 | 0.061 | 0.198 | 0.882 |
| 2-4 pts | v3 (sd refit LOCO) | 110 | 3.897 | 2.754 | 2.863 | 0.059 | 0.190 | 0.818 |
| 2-4 pts | v3 (registered sd bins) | 110 | 3.897 | 2.754 | 2.847 | 0.059 | 0.187 | 0.827 |
| 2-4 pts | unified | 110 | 3.434 | 2.159 | 2.579 | 0.052 | 0.164 | 0.873 |
| <2 pts | v1 | 144 | 5.020 | -0.275 | 3.817 | 0.158 | 0.490 | 0.910 |
| <2 pts | v2 (sd = poll sd) | 144 | 2.502 | 0.642 | 1.882 | 0.066 | 0.226 | 0.965 |
| <2 pts | v3 (sd refit LOCO) | 144 | 2.502 | 0.642 | 1.833 | 0.063 | 0.204 | 0.917 |
| <2 pts | v3 (registered sd bins) | 144 | 2.502 | 0.642 | 1.822 | 0.063 | 0.205 | 0.910 |
| <2 pts | unified | 144 | 2.304 | 0.551 | 1.719 | 0.060 | 0.191 | 0.896 |
| >=4 pts or none | v1 | 10 | 7.759 | -1.130 | 5.967 | 0.005 | 0.033 | 0.800 |
| >=4 pts or none | v2 (sd = poll sd) | 10 | 3.132 | -0.373 | 2.478 | 0.010 | 0.063 | 1.000 |
| >=4 pts or none | v3 (sd refit LOCO) | 10 | 3.132 | -0.373 | 2.453 | 0.008 | 0.055 | 1.000 |
| >=4 pts or none | v3 (registered sd bins) | 10 | 3.132 | -0.373 | 2.446 | 0.008 | 0.054 | 1.000 |
| >=4 pts or none | unified | 10 | 3.533 | -2.811 | 2.719 | 0.006 | 0.044 | 0.900 |

### 2.3 County level

Race-mean metrics over the counties of each race (260 races with a county panel; v4 only for the 65 races of 2022):

| subset | model | races | share wMAE pts | EV wMAPE % | EV median county APE % | turnout-share TV % | level |log err| | level log bias |
|---|---|---|---|---|---|---|---|---|
| all cycles (v1, v3, unified) | unified | 260 | 3.185 | 13.97 | 12.66 | 1.865 | 0.135 | -0.016 |
| all cycles (v1, v3, unified) | v1 | 260 | 5.663 | 14.43 | 13.07 | 2.254 | 0.148 | -0.112 |
| all cycles (v1, v3, unified) | v3 | 260 | 3.672 | 14.43 | 13.07 | 2.254 | 0.148 | -0.112 |
| midterm 2018+2022 (v1, v3, unified) | unified | 132 | 2.866 | 16.61 | 14.94 | 1.920 | 0.166 | -0.042 |
| midterm 2018+2022 (v1, v3, unified) | v1 | 132 | 5.798 | 14.69 | 13.43 | 2.143 | 0.149 | -0.099 |
| midterm 2018+2022 (v1, v3, unified) | v3 | 132 | 3.429 | 14.69 | 13.43 | 2.143 | 0.149 | -0.099 |
| presidential cycles 2016/20/24 (v1, v3, unified) | unified | 128 | 3.513 | 11.25 | 10.30 | 1.808 | 0.104 | 0.011 |
| presidential cycles 2016/20/24 (v1, v3, unified) | v1 | 128 | 5.525 | 14.15 | 12.70 | 2.367 | 0.148 | -0.126 |
| presidential cycles 2016/20/24 (v1, v3, unified) | v3 | 128 | 3.922 | 14.15 | 12.70 | 2.367 | 0.148 | -0.126 |
| 2022 only (v1, v3, v4, unified) | unified | 67 | 3.131 | 15.10 | 12.37 | 2.109 | 0.130 | 0.114 |
| 2022 only (v1, v3, v4, unified) | v1 | 67 | 5.539 | 10.08 | 9.125 | 2.358 | 0.081 | 0.017 |
| 2022 only (v1, v3, v4, unified) | v3 | 67 | 3.571 | 10.08 | 9.125 | 2.358 | 0.081 | 0.017 |
| 2022 only (v1, v3, v4, unified) | v4 | 65 | 3.601 | 27.38 | 22.68 | 2.403 | 0.258 | 0.158 |

### 2.4 House districts

| model | n | mae_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|
| v1 | 2223 | 3.971 | 2.907 | 0.053 | 0.182 | 0.900 |
| v2 (registered sd 0.052) | 2223 | 3.518 | 2.615 | 0.047 | 0.168 | 0.924 |
| v2 (sd = poll sd) | 2223 | 3.518 | 2.617 | 0.046 | 0.166 | 0.900 |
| unified | 2223 | 3.184 | 2.300 | 0.033 | 0.119 | 0.900 |

By cycle:

| cycle | model | n | mae_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|
| 2014 | v1 | 318 | 5.974 | 4.345 | 0.065 | 0.213 | 0.736 |
| 2014 | v2 (registered sd 0.052) | 318 | 4.735 | 3.446 | 0.050 | 0.170 | 0.846 |
| 2014 | v2 (sd = poll sd) | 318 | 4.735 | 3.480 | 0.048 | 0.165 | 0.780 |
| 2014 | unified | 318 | 3.972 | 2.866 | 0.033 | 0.117 | 0.843 |
| 2016 | v1 | 371 | 5.106 | 3.716 | 0.061 | 0.213 | 0.792 |
| 2016 | v2 (registered sd 0.052) | 371 | 4.960 | 3.573 | 0.056 | 0.199 | 0.830 |
| 2016 | v2 (sd = poll sd) | 371 | 4.960 | 3.627 | 0.055 | 0.198 | 0.755 |
| 2016 | unified | 371 | 3.842 | 2.716 | 0.025 | 0.091 | 0.863 |
| 2018 | v1 | 392 | 3.707 | 2.709 | 0.061 | 0.199 | 0.936 |
| 2018 | v2 (registered sd 0.052) | 392 | 3.105 | 2.342 | 0.049 | 0.172 | 0.949 |
| 2018 | v2 (sd = poll sd) | 392 | 3.105 | 2.319 | 0.049 | 0.170 | 0.946 |
| 2018 | unified | 392 | 3.318 | 2.288 | 0.050 | 0.166 | 0.872 |
| 2020 | v1 | 405 | 2.726 | 2.179 | 0.053 | 0.184 | 0.963 |
| 2020 | v2 (registered sd 0.052) | 405 | 3.667 | 2.622 | 0.050 | 0.197 | 0.943 |
| 2020 | v2 (sd = poll sd) | 405 | 3.667 | 2.609 | 0.049 | 0.196 | 0.931 |
| 2020 | unified | 405 | 3.592 | 2.497 | 0.039 | 0.156 | 0.889 |
| 2022 | v1 | 339 | 3.347 | 2.412 | 0.046 | 0.156 | 0.956 |
| 2022 | v2 (registered sd 0.052) | 339 | 2.558 | 2.015 | 0.044 | 0.148 | 0.976 |
| 2022 | v2 (sd = poll sd) | 339 | 2.558 | 1.992 | 0.043 | 0.146 | 0.976 |
| 2022 | unified | 339 | 2.474 | 1.859 | 0.030 | 0.101 | 0.956 |
| 2024 | v1 | 398 | 3.368 | 2.363 | 0.036 | 0.132 | 0.985 |
| 2024 | v2 (registered sd 0.052) | 398 | 2.275 | 1.830 | 0.033 | 0.120 | 0.987 |
| 2024 | v2 (sd = poll sd) | 398 | 2.275 | 1.818 | 0.032 | 0.119 | 0.987 |
| 2024 | unified | 398 | 2.001 | 1.644 | 0.022 | 0.078 | 0.972 |

By class:

| class | model | n | mae_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|
| polled | v1 | 319 | 3.604 | 2.628 | 0.134 | 0.418 | 0.953 |
| polled | v2 (registered sd 0.052) | 319 | 2.994 | 2.287 | 0.130 | 0.426 | 0.953 |
| polled | v2 (sd = poll sd) | 319 | 2.994 | 2.264 | 0.130 | 0.424 | 0.953 |
| polled | unified | 319 | 2.756 | 1.956 | 0.133 | 0.417 | 0.862 |
| unpolled | v1 | 1904 | 4.032 | 2.954 | 0.040 | 0.143 | 0.891 |
| unpolled | v2 (registered sd 0.052) | 1904 | 3.606 | 2.670 | 0.033 | 0.124 | 0.920 |
| unpolled | v2 (sd = poll sd) | 1904 | 3.606 | 2.676 | 0.032 | 0.122 | 0.891 |
| unpolled | unified | 1904 | 3.256 | 2.357 | 0.017 | 0.069 | 0.907 |

| class | model | n | mae_pts | crps_pts | brier | logloss | cov90 |
|---|---|---|---|---|---|---|---|
| close (<0.05) | v1 | 463 | 5.270 | 3.841 | 0.189 | 0.560 | 0.803 |
| close (<0.05) | v2 (registered sd 0.052) | 463 | 4.403 | 3.219 | 0.151 | 0.473 | 0.860 |
| close (<0.05) | v2 (sd = poll sd) | 463 | 4.403 | 3.257 | 0.149 | 0.466 | 0.829 |
| close (<0.05) | unified | 463 | 3.454 | 2.493 | 0.099 | 0.319 | 0.849 |
| lean (0.05-0.15) | v1 | 991 | 3.944 | 2.863 | 0.029 | 0.133 | 0.903 |
| lean (0.05-0.15) | v2 (registered sd 0.052) | 991 | 3.362 | 2.498 | 0.032 | 0.140 | 0.934 |
| lean (0.05-0.15) | v2 (sd = poll sd) | 991 | 3.362 | 2.496 | 0.031 | 0.138 | 0.904 |
| lean (0.05-0.15) | unified | 991 | 3.048 | 2.211 | 0.027 | 0.107 | 0.913 |
| safe (>0.15) | v1 | 769 | 3.223 | 2.402 | 0.003 | 0.018 | 0.954 |
| safe (>0.15) | v2 (registered sd 0.052) | 769 | 3.186 | 2.401 | 0.003 | 0.019 | 0.951 |
| safe (>0.15) | v2 (sd = poll sd) | 769 | 3.186 | 2.386 | 0.003 | 0.020 | 0.936 |
| safe (>0.15) | unified | 769 | 3.198 | 2.297 | 0.003 | 0.014 | 0.914 |

### 2.5 The tests behind the recommendation (every row; the complete log with n<30 rows, other schemes and the registered-sd sensitivity is `data_public/us_benchmark_comparisons.csv`)

Positive improvement = candidate better than the primary on the layer's metric.

**Statewide share CRPS (points), primary v3 with LOCO-refit sd**

| scheme | class | candidate | n | improvement vs primary [95% CI] | p_holm | cycles same sign | rule met |
|---|---|---|---|---|---|---|---|
| pooled | all races | v1 | 264 | -2.079 [-2.559, -1.643] | 0.005 | 5/5 | no |
| pooled | all races | v2 | 264 | -0.022 [-0.044, -0.001] | 1.000 | 4/5 | no |
| pooled | all races | unified | 264 | +0.171 [-0.053, +0.374] | 1.000 | 4/5 | no |
| C1 | high | v1 | 65 | -2.838 [-4.091, -1.686] | 0.005 | 5/5 | no |
| C1 | high | v2 | 65 | +0.015 [-0.018, +0.051] | 1.000 | 3/5 | no |
| C1 | high | unified | 65 | +0.041 [-0.760, +0.745] | 1.000 | 3/5 | no |
| C1 | low | v1 | 102 | -1.006 [-1.174, -0.842] | 0.005 | 5/5 | no |
| C1 | low | v2 | 102 | -0.072 [-0.107, -0.035] | 0.005 | 4/5 | no |
| C1 | low | unified | 102 | +0.099 [+0.016, +0.177] | 0.856 | 5/5 | no |
| C1 | medium | v1 | 97 | -2.698 [-3.644, -1.878] | 0.005 | 5/5 | no |
| C1 | medium | v2 | 97 | +0.005 [-0.030, +0.042] | 1.000 | 2/5 | no |
| C1 | medium | unified | 97 | +0.333 [+0.113, +0.558] | 0.170 | 4/5 | no |

**County share wMAE (points), primary v3**

| scheme | class | candidate | n | improvement vs primary [95% CI] | p_holm | cycles same sign | rule met |
|---|---|---|---|---|---|---|---|
| pooled | all races | v1 | 260 | -1.992 [-2.555, -1.470] | 0.005 | 5/5 | no |
| pooled | all races | v4 | 65 | +0.017 [-0.066, +0.084] | 1.000 | 1/1 | no |
| pooled | all races | unified | 260 | +0.487 [+0.193, +0.757] | 0.121 | 5/5 | no |
| C1 | high | v1 | 64 | -2.879 [-4.241, -1.659] | 0.005 | 5/5 | no |
| C1 | high | v4 | 21 | n<30 |  |  | no |
| C1 | high | unified | 64 | +0.355 [-0.677, +1.268] | 1.000 | 3/5 | no |
| C1 | low | v1 | 101 | -1.048 [-1.502, -0.613] | 0.005 | 5/5 | no |
| C1 | low | v4 | 25 | n<30 |  |  | no |
| C1 | low | unified | 101 | +0.269 [+0.141, +0.400] | 0.005 | 5/5 | YES |
| C1 | medium | v1 | 95 | -2.397 [-3.510, -1.422] | 0.005 | 5/5 | no |
| C1 | medium | v4 | 19 | n<30 |  |  | no |
| C1 | medium | unified | 95 | +0.808 [+0.481, +1.148] | 0.005 | 5/5 | YES |

**County expected-vote wMAPE (%), midterms, primary v3 (v1 = v3 by construction, not a candidate)**

| scheme | class | candidate | n | improvement vs primary [95% CI] | p_holm | cycles same sign | rule met |
|---|---|---|---|---|---|---|---|
| pooled | all races | v4 | 65 | -17.188 [-20.236, -14.389] | 0.005 | 1/1 | no |
| pooled | all races | unified | 132 | -1.919 [-3.096, -0.793] | 0.018 | 1/2 | no |
| C1 | high | v4 | 21 | n<30 |  |  | no |
| C1 | high | unified | 35 | -2.083 [-4.839, +0.548] | 1.000 | 1/2 | no |
| C1 | low | v4 | 25 | n<30 |  |  | no |
| C1 | low | unified | 48 | -1.522 [-3.179, -0.055] | 1.000 | 1/2 | no |
| C1 | medium | v4 | 19 | n<30 |  |  | no |
| C1 | medium | unified | 49 | -2.190 [-4.062, -0.454] | 0.475 | 1/2 | no |

**County turnout-share distance (%), midterms**

| scheme | class | candidate | n | improvement vs primary [95% CI] | p_holm | cycles same sign | rule met |
|---|---|---|---|---|---|---|---|
| pooled | all races | v4 | 65 | -0.054 [-0.500, +0.306] | 1.000 | 1/1 | no |
| pooled | all races | unified | 132 | +0.223 [+0.124, +0.317] | 0.005 | 2/2 | YES |
| C1 | high | v4 | 21 | n<30 |  |  | no |
| C1 | high | unified | 35 | +0.267 [+0.097, +0.451] | 0.090 | 2/2 | no |
| C1 | low | v4 | 25 | n<30 |  |  | no |
| C1 | low | unified | 48 | +0.144 [-0.072, +0.320] | 1.000 | 1/2 | no |
| C1 | medium | v4 | 19 | n<30 |  |  | no |
| C1 | medium | unified | 49 | +0.270 [+0.162, +0.377] | 0.005 | 2/2 | YES |

**House district share CRPS (points), primary v2 with LOCO-refit sd**

| scheme | class | candidate | n | improvement vs primary [95% CI] | p_holm | cycles same sign | rule met |
|---|---|---|---|---|---|---|---|
| pooled | all races | v1 | 2223 | -0.291 [-0.340, -0.240] | 0.005 | 5/6 | no |
| pooled | all races | unified | 2223 | +0.317 [+0.252, +0.382] | 0.005 | 6/6 | YES |
| H1 | polled | v1 | 319 | -0.364 [-0.500, -0.224] | 0.005 | 4/4 | no |
| H1 | polled | unified | 319 | +0.308 [+0.059, +0.572] | 0.700 | 3/4 | no |
| H1 | unpolled | v1 | 1904 | -0.278 [-0.331, -0.225] | 0.005 | 5/6 | no |
| H1 | unpolled | unified | 1904 | +0.319 [+0.256, +0.381] | 0.005 | 4/6 | YES |
| H2 | close (<0.05) | v1 | 463 | -0.584 [-0.695, -0.469] | 0.005 | 6/6 | no |
| H2 | close (<0.05) | unified | 463 | +0.764 [+0.620, +0.906] | 0.005 | 4/6 | YES |
| H2 | lean (0.05-0.15) | v1 | 991 | -0.367 [-0.438, -0.295] | 0.005 | 5/6 | no |
| H2 | lean (0.05-0.15) | unified | 991 | +0.285 [+0.188, +0.385] | 0.005 | 5/6 | YES |
| H2 | safe (>0.15) | v1 | 769 | -0.016 [-0.096, +0.065] | 1.000 | 4/6 | no |
| H2 | safe (>0.15) | unified | 769 | +0.089 [-0.009, +0.190] | 1.000 | 4/6 | no |

## 5. Part B: count stage (analysis/us_benchmark_count.py) (every version through `brunder.us.project`)

`analysis/us_benchmark_count.py` runs every statewide version through `brunder.us.project` (imported read-only) on

1. **Georgia simulated nights**: 2022 governor, 2022 Senate and 2024 president, built from the certified county results by vote type (Georgia Secretary of State; early + mail batch first, Election Day in four chunks later; the generator of `analysis/us_calibrate.py`), **200 order draws per race**, seed `crc32(race|draw)` so every version sees the same draws. Aggregates: `data_public/us_benchmark_count_ga.csv` (long format: scope, version, metric, check, n, value, diff_vs_v3, diff_lo, diff_hi).
2. **Real past nights** (62 archived nights: NYT/Edison copies plus one official Michigan capture; 2020 president and Senate/governor, 2022 Senate/governor, 2024 president and Senate/governor, ten states; replay frames of `analysis/us_real_nights.py`). Numbers from these nights are internal and are not reproduced here; only verdicts in words (section "Real nights").

Every version sees only information available before the election. Checkpoints are shares of the final two-party vote counted (10, 25, 50, 75 %; "pre-count" = no vote in). Metrics: mean absolute margin error (margin = 2 x share - 1, points), signed share error, 90 % coverage (|z| < 1.645), projected-turnout error (projected two-party total vs certified), and the call. **Call rule (fixed before running):** the earliest checkpoint from which P(correct winner) >= 0.9 holds at every later checkpoint (to 95 % counted on the simulated nights, 99.5 % on real nights). A wrong call is P(wrong winner) >= 0.9 at any checkpoint. Differences vs v3 are paired; intervals are 95 % percentile bootstraps (2000 resamples; Georgia: draws, stratified by race; real nights: nights).

### 5.How each version's past-cycle configuration was reconstructed

There are no registered configs for past cycles (the 2026 files are the only ones), so each version is rebuilt by rule from its 2026 definition, with the state's own `[model]`/`[count]` settings (GA: tau 0.04, rho 0.6, sigma_mun0 0.08, vote_type, early_share 0.64, f_complete 0.98, learn_turnout, f_max 0.95).

| version | reconstruction for a past race in year t | approximation / what differs from 2026 |
|---|---|---|
| v1 | prior = last race for the same office (president: the last president; Senate/governor: the last Senate/governor county result, else last president); expected votes = that race's two-party votes x cycle ratio (0.80 midterm after a presidential year, 1.30 presidential after a midterm, else 1; the convention of `us_calibrate.py`); no polls | in 2026 the prior race is a midterm, so no cycle ratio is needed; GA 2022 Senate uses the 2020 regular Senate race |
| v2 | v1 + final-week poll average: `swing_prior_mean` = poll average - `baseline_share(prior)`, `swing_prior_sd` = poll sd | Georgia 2022: 538 `raw_polls` through `us_polls._poll_prior_538` (registered sd = 0.039 + sampling); real nights and GA 2024: state drift-panel average (same 538 rule; president: 538 president polls) with the flat 0.039 (sampling term, ~0.001, not stored) |
| v3 (primary) | v2 with `swing_prior_sd` = `poll_sd_by_n(number of polls)` and `count_scaled_terms` | exact |
| v4 | v3 + `expected_votes` (`us_model_analysis.expected_votes`): pres(t-2) two-party votes x the county's own drop-off (Senate, else governor, of t-4 over pres(t-6), all-candidate totals), relative to its state, capped x0.61-x1.65, `expected_votes_level = "file"`; `swing_prior_mean` re-centred on those weights | built from the county panel of `brunder.us_unified` (certified results), not the MEDSL tables of `validate_v4`; reproduces its GA turnout error (+21.5 %). **Presidential nights have no drop-off**: analogue = pres(t-4) x own change from pres(t-8) to pres(t-4), file used only to redistribute v3's statewide level (`level = "prior"`); not a registered recipe |
| v4d (sensitivity) | v4's county distribution with v3's statewide level (`level = "prior"`), midterms only | extra row, not a registered version |
| v5 | v3 + `hisp_slope = true`, `hisp_slope_tau = 0.2` | Hispanic share = ACS 5-year vintage ending t-2 (demographic panel of `brunder.us_unified`) passed on the prior frame (`h`), not the registered 2020-24 CSV (that would be look-ahead) |
| unified | `us_real_nights.unified_prior`: leave-one-cycle-out fits of `us_unified` (share, turnout, state-vote models, state sd), `swing_prior_mean = 0`, `swing_prior_sd` = its state sd, `count_scaled_terms` | **Senate/governor only**: the unified model has no president model, so it is absent on all president nights. The saved `us_unified_params.json` (final fit on 2016-2024) is never used |
| v3p, v4p (sensitivity, GA 2022) | v3 / v4 on the prior of `validate_v4` (2020 president x 0.80 instead of the last same-office race) | to reproduce `docs/us_model_analysis.md` |

Cross-checks that passed: v4p reproduces the documented GA 2022 turnout error (+21.5 %) and its margin errors (10/25/50/75 %: v3p 2.48/3.55/3.55/0.22, v4p 2.58/3.59/3.18/1.08 vs the documented 2.44/3.47/3.59/0.23 and 2.53/3.51/3.24/1.10; different draws); the unified rows of the real-night replay are identical to those of `us_real_nights.py` (893 rows, max |difference| 0); the script is deterministic (same output for 3 and 6 worker processes).

### 5.Georgia simulated nights

Georgia 2022 gov/Senate certified two-party Democratic share 0.4621 / 0.5049, polls 0.4611 (34 polls) / 0.5020 (40); 2024 president 0.4890, poll average 0.4930 (55). Only count-order randomness is resampled: **the interval is not a between-race interval** (each race has one realised poll error and one realised county pattern; two races in 2022, one in 2024). Pre-count rows have no draw variance (degenerate intervals).

#### Georgia 2022 governor + Senate pooled (2 races x 200 draws)

Mean absolute margin error, points (in brackets: difference vs v3 with its paired 95 % bootstrap interval over order draws)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | 4.49 (+4.12 [+4.12, +4.12]) | 7.48 (+4.80 [+4.72, +4.89]) | 7.77 (+3.88 [+3.84, +3.93]) | 6.49 (+2.43 [+2.42, +2.45]) | 1.32 (+0.49 [+0.48, +0.50]) |
| v2 | 0.38 (+0.00 [+0.00, +0.00]) | 4.46 (+1.79 [+1.76, +1.82]) | 5.48 (+1.59 [+1.57, +1.61]) | 5.11 (+1.05 [+1.04, +1.05]) | 1.04 (+0.20 [+0.19, +0.21]) |
| v3 (primary) | 0.38 | 2.67 | 3.89 | 4.06 | 0.84 |
| v4 | 0.38 (+0.00 [+0.00, +0.00]) | 2.51 (-0.17 [-0.17, -0.16]) | 3.55 (-0.34 [-0.34, -0.33]) | 3.15 (-0.91 [-0.94, -0.89]) | 1.10 (+0.27 [+0.22, +0.31]) |
| v4d (sens.) | 0.38 (+0.00 [+0.00, +0.00]) | 2.62 (-0.06 [-0.06, -0.06]) | 3.81 (-0.08 [-0.08, -0.08]) | 3.96 (-0.10 [-0.11, -0.10]) | 0.70 (-0.13 [-0.14, -0.12]) |
| v5 | 0.38 (+0.00 [+0.00, +0.00]) | 2.65 (-0.02 [-0.03, -0.01]) | 3.85 (-0.03 [-0.04, -0.03]) | 4.04 (-0.03 [-0.03, -0.02]) | 0.83 (-0.00 [-0.01, -0.00]) |
| unified | 0.72 (+0.34 [+0.34, +0.34]) | 1.92 (-0.75 [-0.80, -0.71]) | 3.12 (-0.76 [-0.81, -0.72]) | 3.03 (-1.03 [-1.06, -1.00]) | 0.80 (-0.03 [-0.07, -0.00]) |
| v3p (sens.) | 0.38 (+0.00 [+0.00, +0.00]) | 2.48 (-0.20 [-0.22, -0.17]) | 3.55 (-0.34 [-0.36, -0.31]) | 3.55 (-0.51 [-0.53, -0.49]) | 0.22 (-0.62 [-0.63, -0.60]) |
| v4p (sens.) | 0.38 (+0.00 [+0.00, +0.00]) | 2.58 (-0.10 [-0.12, -0.07]) | 3.59 (-0.30 [-0.33, -0.27]) | 3.18 (-0.88 [-0.91, -0.85]) | 1.08 (+0.25 [+0.20, +0.29]) |

90 % coverage of the final share (nominal 0.90)

| version | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|
| v1 | 1.00 | 1.00 | 0.76 | 1.00 |
| v2 | 1.00 | 1.00 | 1.00 | 1.00 |
| v3 (primary) | 1.00 | 1.00 | 0.86 | 1.00 |
| v4 | 1.00 | 1.00 | 1.00 | 1.00 |
| v4d (sens.) | 1.00 | 1.00 | 0.97 | 1.00 |
| v5 | 1.00 | 1.00 | 0.86 | 1.00 |
| unified | 1.00 | 1.00 | 1.00 | 1.00 |
| v3p (sens.) | 1.00 | 1.00 | 0.99 | 1.00 |
| v4p (sens.) | 1.00 | 1.00 | 1.00 | 1.00 |

Projected-turnout error of the projected two-party total: mean signed / mean absolute (%)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | +0.6 / 0.9 |
| v2 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | +0.6 / 0.9 |
| v3 (primary) | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | +0.6 / 0.9 |
| v4 | +21.5 / 21.5 | +21.5 / 21.5 | +21.5 / 21.5 | +21.5 / 21.5 | +17.8 / 17.8 |
| v4d (sens.) | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | +0.7 / 0.8 |
| v5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | -0.1 / 0.5 | +0.6 / 0.9 |
| unified | +7.6 / 7.6 | +7.6 / 7.6 | +7.6 / 7.6 | +7.6 / 7.6 | +7.0 / 7.0 |
| v3p (sens.) | +1.5 / 1.5 | +1.5 / 1.5 | +1.5 / 1.5 | +1.5 / 1.5 | +1.9 / 1.9 |
| v4p (sens.) | +21.5 / 21.5 | +21.5 / 21.5 | +21.5 / 21.5 | +21.5 / 21.5 | +17.8 / 17.8 |

Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later checkpoint (to 95 % counted); minutes are on the simulated clock

| version | calls (share of draws) | counted at call, median | counted at call, earliest | minutes to call, median | minutes, earliest | wrong calls | counted at call, mean diff vs v3 [95 % CI] |
|---|---|---|---|---|---|---|---|
| v1 | 0.93 | 0.705 | 0.651 | 151 | 78 | 0.000 | +0.043 [+0.039, +0.048] |
| v2 | 0.74 | 0.702 | 0.600 | 140 | 73 | 0.000 | +0.052 [+0.046, +0.058] |
| v3 (primary) | 1.00 | 0.711 | 0.408 | 165 | 66 | 0.000 |  |
| v4 | 1.00 | 0.758 | 0.401 | 175 | 63 | 0.000 | -0.022 [-0.026, -0.018] |
| v4d (sens.) | 1.00 | 0.733 | 0.408 | 136 | 65 | 0.000 | -0.045 [-0.049, -0.042] |
| v5 | 1.00 | 0.711 | 0.408 | 165 | 66 | 0.000 | -0.001 [-0.002, -0.000] |
| unified | 0.66 | 0.602 | 0.351 | 80 | 60 | 0.000 | -0.056 [-0.064, -0.048] |
| v3p (sens.) | 0.99 | 0.664 | 0.408 | 101 | 65 | 0.000 | -0.022 [-0.026, -0.018] |
| v4p (sens.) | 1.00 | 0.758 | 0.408 | 176 | 65 | 0.000 | -0.022 [-0.026, -0.018] |

#### Georgia 2024 president (1 race x 200 draws)

Mean absolute margin error, points (in brackets: difference vs v3 with its paired 95 % bootstrap interval over order draws)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | 2.47 (+1.65 [+1.65, +1.65]) | 1.08 (+0.20 [+0.13, +0.26]) | 1.89 (+0.51 [+0.47, +0.55]) | 2.22 (+0.40 [+0.39, +0.41]) | 2.25 (+0.19 [+0.18, +0.19]) |
| v2 | 0.82 (+0.00 [+0.00, +0.00]) | 0.94 (+0.05 [+0.03, +0.08]) | 1.59 (+0.20 [+0.19, +0.22]) | 1.99 (+0.17 [+0.17, +0.18]) | 2.15 (+0.08 [+0.08, +0.08]) |
| v3 (primary) | 0.82 | 0.89 | 1.39 | 1.82 | 2.06 |
| v4 | 0.82 (+0.00 [+0.00, +0.00]) | 0.61 (-0.28 [-0.28, -0.27]) | 1.01 (-0.37 [-0.38, -0.37]) | 1.32 (-0.50 [-0.50, -0.50]) | 1.46 (-0.60 [-0.61, -0.60]) |
| v5 | 0.82 (+0.00 [+0.00, +0.00]) | 0.93 (+0.04 [+0.03, +0.06]) | 1.42 (+0.03 [+0.02, +0.04]) | 1.84 (+0.02 [+0.02, +0.03]) | 2.07 (+0.01 [+0.00, +0.01]) |

90 % coverage of the final share (nominal 0.90)

| version | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|
| v1 | 1.00 | 1.00 | 1.00 | 1.00 |
| v2 | 1.00 | 1.00 | 1.00 | 1.00 |
| v3 (primary) | 1.00 | 1.00 | 1.00 | 0.67 |
| v4 | 1.00 | 1.00 | 1.00 | 1.00 |
| v5 | 1.00 | 1.00 | 1.00 | 0.66 |

Projected-turnout error of the projected two-party total: mean signed / mean absolute (%)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.2 / 5.2 | -5.2 / 5.2 |
| v2 | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.2 / 5.2 | -5.2 / 5.2 |
| v3 (primary) | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.2 / 5.2 | -5.2 / 5.2 |
| v4 | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.2 / 5.2 |
| v5 | -5.3 / 5.3 | -5.3 / 5.3 | -5.3 / 5.3 | -5.2 / 5.2 | -5.2 / 5.2 |

Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later checkpoint (to 95 % counted); minutes are on the simulated clock

| version | calls (share of draws) | counted at call, median | counted at call, earliest | minutes to call, median | minutes, earliest | wrong calls | counted at call, mean diff vs v3 [95 % CI] |
|---|---|---|---|---|---|---|---|
| v1 | 0.99 | 0.950 | 0.900 | 319 | 204 | 0.000 | +0.024 [+0.021, +0.028] |
| v2 | 0.99 | 0.950 | 0.900 | 321 | 204 | 0.000 | +0.029 [+0.025, +0.032] |
| v3 (primary) | 1.00 | 0.902 | 0.850 | 268 | 165 | 0.000 |  |
| v4 | 1.00 | 0.851 | 0.704 | 191 | 77 | 0.000 | -0.068 [-0.073, -0.063] |
| v5 | 1.00 | 0.902 | 0.850 | 268 | 165 | 0.000 | +0.000 [+0.000, +0.001] |

#### Georgia 2022 governor alone (200 draws)

Mean absolute margin error, points (in brackets: difference vs v3 with its paired 95 % bootstrap interval over order draws)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | 6.19 (+6.00 [+6.00, +6.00]) | 8.84 (+5.52 [+5.39, +5.64]) | 8.80 (+4.27 [+4.20, +4.35]) | 7.48 (+2.66 [+2.63, +2.68]) | 2.04 (+0.58 [+0.57, +0.58]) |
| v2 | 0.19 (+0.00 [+0.00, +0.00]) | 5.39 (+2.06 [+2.00, +2.11]) | 6.28 (+1.75 [+1.71, +1.78]) | 5.96 (+1.14 [+1.13, +1.15]) | 1.72 (+0.25 [+0.25, +0.26]) |
| v3 (primary) | 0.19 | 3.33 | 4.53 | 4.82 | 1.46 |
| v4 | 0.19 (+0.00 [+0.00, +0.00]) | 2.92 (-0.41 [-0.41, -0.40]) | 3.87 (-0.66 [-0.67, -0.66]) | 3.49 (-1.33 [-1.36, -1.30]) | 0.99 (-0.47 [-0.55, -0.40]) |
| v4d (sens.) | 0.19 (+0.00 [+0.00, +0.00]) | 3.03 (-0.29 [-0.30, -0.29]) | 4.13 (-0.40 [-0.40, -0.40]) | 4.29 (-0.53 [-0.53, -0.53]) | 0.85 (-0.61 [-0.63, -0.60]) |
| v5 | 0.19 (+0.00 [+0.00, +0.00]) | 3.32 (-0.01 [-0.02, -0.00]) | 4.50 (-0.03 [-0.04, -0.02]) | 4.79 (-0.03 [-0.04, -0.02]) | 1.46 (-0.01 [-0.01, -0.00]) |
| unified | 1.10 (+0.91 [+0.91, +0.91]) | 2.06 (-1.27 [-1.35, -1.18]) | 3.35 (-1.18 [-1.26, -1.10]) | 3.55 (-1.27 [-1.32, -1.21]) | 0.16 (-1.30 [-1.33, -1.27]) |
| v3p (sens.) | 0.19 (+0.00 [+0.00, +0.00]) | 2.87 (-0.46 [-0.51, -0.41]) | 3.91 (-0.62 [-0.67, -0.57]) | 3.94 (-0.88 [-0.92, -0.85]) | 0.29 (-1.17 [-1.19, -1.15]) |
| v4p (sens.) | 0.19 (+0.00 [+0.00, +0.00]) | 2.96 (-0.36 [-0.41, -0.32]) | 3.93 (-0.60 [-0.65, -0.55]) | 3.55 (-1.27 [-1.32, -1.22]) | 0.98 (-0.48 [-0.55, -0.40]) |

Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later checkpoint (to 95 % counted); minutes are on the simulated clock

| version | calls (share of draws) | counted at call, median | counted at call, earliest | minutes to call, median | minutes, earliest | wrong calls | counted at call, mean diff vs v3 [95 % CI] |
|---|---|---|---|---|---|---|---|
| v1 | 1.00 | 0.701 | 0.651 | 133 | 78 | 0.000 | +0.065 [+0.057, +0.073] |
| v2 | 1.00 | 0.700 | 0.600 | 133 | 73 | 0.000 | +0.063 [+0.055, +0.071] |
| v3 (primary) | 1.00 | 0.650 | 0.408 | 87 | 66 | 0.000 |  |
| v4 | 1.00 | 0.614 | 0.401 | 82 | 63 | 0.000 | -0.039 [-0.046, -0.033] |
| v4d (sens.) | 1.00 | 0.630 | 0.408 | 84 | 65 | 0.000 | -0.031 [-0.036, -0.025] |
| v5 | 1.00 | 0.650 | 0.408 | 87 | 66 | 0.000 | -0.002 [-0.004, -0.001] |
| unified | 1.00 | 0.554 | 0.351 | 77 | 60 | 0.000 | -0.080 [-0.090, -0.071] |
| v3p (sens.) | 1.00 | 0.601 | 0.408 | 79 | 65 | 0.000 | -0.052 [-0.060, -0.045] |
| v4p (sens.) | 1.00 | 0.609 | 0.408 | 82 | 65 | 0.000 | -0.040 [-0.047, -0.033] |

#### Georgia 2022 Senate alone (200 draws)

Mean absolute margin error, points (in brackets: difference vs v3 with its paired 95 % bootstrap interval over order draws)

| version | pre-count | 10 % | 25 % | 50 % | 75 % |
|---|---|---|---|---|---|
| v1 | 2.80 (+2.23 [+2.23, +2.23]) | 6.11 (+4.09 [+3.98, +4.20]) | 6.74 (+3.50 [+3.45, +3.55]) | 5.51 (+2.21 [+2.19, +2.23]) | 0.61 (+0.40 [+0.38, +0.42]) |
| v2 | 0.57 (+0.00 [+0.00, +0.00]) | 3.54 (+1.52 [+1.49, +1.55]) | 4.68 (+1.43 [+1.42, +1.45]) | 4.25 (+0.95 [+0.94, +0.96]) | 0.36 (+0.15 [+0.13, +0.17]) |
| v3 (primary) | 0.57 | 2.02 | 3.24 | 3.30 | 0.21 |
| v4 | 0.57 (+0.00 [+0.00, +0.00]) | 2.10 (+0.08 [+0.07, +0.08]) | 3.23 (-0.02 [-0.02, -0.01]) | 2.81 (-0.50 [-0.52, -0.46]) | 1.22 (+1.01 [+0.94, +1.07]) |
| v4d (sens.) | 0.57 (+0.00 [+0.00, +0.00]) | 2.20 (+0.18 [+0.18, +0.18]) | 3.49 (+0.24 [+0.24, +0.24]) | 3.62 (+0.32 [+0.32, +0.33]) | 0.56 (+0.35 [+0.33, +0.37]) |
| v5 | 0.57 (+0.00 [+0.00, +0.00]) | 1.99 (-0.03 [-0.04, -0.02]) | 3.21 (-0.04 [-0.05, -0.03]) | 3.28 (-0.02 [-0.03, -0.02]) | 0.21 (-0.00 [-0.00, -0.00]) |
| unified | 0.34 (-0.23 [-0.23, -0.23]) | 1.78 (-0.24 [-0.28, -0.20]) | 2.90 (-0.34 [-0.38, -0.31]) | 2.50 (-0.80 [-0.84, -0.76]) | 1.45 (+1.24 [+1.18, +1.29]) |
| v3p (sens.) | 0.57 (+0.00 [+0.00, +0.00]) | 2.09 (+0.07 [+0.03, +0.10]) | 3.20 (-0.05 [-0.08, -0.02]) | 3.17 (-0.14 [-0.15, -0.12]) | 0.15 (-0.06 [-0.09, -0.03]) |
| v4p (sens.) | 0.57 (+0.00 [+0.00, +0.00]) | 2.19 (+0.17 [+0.14, +0.21]) | 3.24 (-0.00 [-0.03, +0.03]) | 2.81 (-0.49 [-0.52, -0.46]) | 1.18 (+0.97 [+0.91, +1.03]) |

Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later checkpoint (to 95 % counted); minutes are on the simulated clock

| version | calls (share of draws) | counted at call, median | counted at call, earliest | minutes to call, median | minutes, earliest | wrong calls | counted at call, mean diff vs v3 [95 % CI] |
|---|---|---|---|---|---|---|---|
| v1 | 0.86 | 0.951 | 0.850 | 348 | 236 | 0.000 | +0.018 [+0.014, +0.022] |
| v2 | 0.49 | 0.951 | 0.901 | 344 | 282 | 0.000 | +0.029 [+0.022, +0.035] |
| v3 (primary) | 0.99 | 0.950 | 0.811 | 342 | 196 | 0.000 |  |
| v4 | 1.00 | 0.950 | 0.851 | 334 | 248 | 0.000 | -0.004 [-0.008, -0.000] |
| v4d (sens.) | 1.00 | 0.884 | 0.800 | 280 | 169 | 0.000 | -0.060 [-0.065, -0.055] |
| v5 | 0.99 | 0.950 | 0.811 | 342 | 196 | 0.000 | +0.000 [+0.000, +0.000] |
| unified | 0.32 | 0.952 | 0.950 | 351 | 316 | 0.000 | +0.022 [+0.015, +0.029] |
| v3p (sens.) | 0.99 | 0.951 | 0.811 | 348 | 196 | 0.000 | +0.008 [+0.006, +0.011] |
| v4p (sens.) | 1.00 | 0.950 | 0.851 | 334 | 249 | 0.000 | -0.004 [-0.008, +0.000] |

#### What the Georgia simulated nights say

- **Polls matter before and early in the count.** v1 starts at 4.5 points of margin error pre-count in 2022 (2.5 in 2024) against 0.4 (0.8) for every polled version, and stays 2.4-4.8 points worse at 10-50 % counted (2022 pooled). v2 is 1.0-1.8 points worse than v3 at 10-50 % counted (intervals exclude 0) and calls 5 points of the count later (and in 25 % of the 2022 draws never holds P >= 0.9): the count-scaled terms of v3 are what makes the poll prior hold early.
- **v5 (Hispanic slope) is indistinguishable from v3 in Georgia**: all differences are within 0.04 points of margin error, same calls. Georgia has little Hispanic-share variation across counties; this says nothing about TX/FL/CA/NM.
- **v4 improves the margin at 10-50 % counted in 2022 (-0.17, -0.34, -0.91 points) but is 0.27 points worse at 75 % and carries a +21.5 % statewide turnout error all night** (the 2018/2016 drop-off was unusually high); v4d (the county distribution with v3's level) removes the turnout error and keeps only a 0.06-0.13 point gain. On the 2024 president night the analogue v4 is better throughout (-0.28 to -0.60 points) and calls 7 points of the count earlier, but that is one race and an unregistered recipe. v4 has 100 % coverage where v3 under-covers at 50 % (gov).
- **unified has the smallest 2022 margin errors at 10-50 % counted** (-0.75, -0.76, -1.03 points vs v3, pooled; governor -1.2 to -1.3 at every checkpoint, Senate -0.2 to -0.8 and +1.2 worse at 75 %), it is worse pre-count (0.72 vs 0.38: it does not take the poll average as its centre) and its projected turnout is 7.6 % off pre-count and all night (governor +0.8 %, Senate +14.3 %). Its calls are earlier in the governor race (median 55 % counted vs 65 %) but in the Senate race (a one-point race) it holds P >= 0.9 in only a third of the draws (v3: all but one).
- **Coverage** of the 90 % interval is 100 % almost everywhere on the simulated nights (the intervals are wide); the exceptions are v1/v3/v5 at 50 % in the governor race and v3/v5 at 75 % on the 2024 president night (0.66). **No version made a wrong call** (P(wrong winner) >= 0.9) on any simulated night.

### 5.Real nights (verdict in words; numbers are internal and not published)

62 archived nights in ten states (2020 president and Senate/governor, 2022 Senate/governor, 2024 president and Senate/governor); 28 of them carry the call analysis, 34 are sparse. All numbers are under `$BRUNDER_DATA/derived/us_benchmark/` (NYT/Edison-derived, internal only). Nights are not independent (same states, same national error), so any interval is optimistic.

- **v3 beats v1 and v2 early** (clear at 10 % counted; v1 also at 50-75 %), and both call later than v3. After a quarter of the vote v2 and v3 are close.
- **v4 and v5 are within noise of v3**: no robust margin-error difference at any checkpoint, no change in calls (v5 equals v3; v4 within noise); v4's expected votes help the projected turnout only around half counted.
- **unified is within noise of v3 on margin error** on the Senate/governor nights it covers (it has no president model); slightly worse late in the count, a little later to call on the shared dense nights, and worse on projected turnout. The data cannot separate it from v3 at 10-50 % counted.
- **Coverage** of the 90 % interval is below nominal at 50-75 % counted for every version on real nights; differences between versions are inside the noise.
- **Wrong-side confidence** (P(wrong winner) >= 0.9 at some frame) occurred for the registered versions on several 2020 president nights (that year's early-count bias) and one 2024 president night; never on a Senate/governor night.

### 5.What could not be reconstructed, and approximations

- No registered v1-v5 configs exist for past cycles; all are rebuilt by rule (table above). v1's expected votes use the cycle ratio of `us_calibrate.py`, so v1/v3 differ slightly from the earlier internal replay (`us_real_nights.py`), which used the prior race's votes with ratio 1. The unified rows are identical to it.
- v4 and v5 for **president** nights use unregistered analogues (v4: pres(t-4) x own change, redistribution only) or are the same rule applied to a different office (v5); unified has **no president model** (absent on all president nights, including Georgia 2024).
- v5's Hispanic share is the ACS 5-year vintage ending t-2 from the demographic panel (2016-20 for 2022, published March 2022; 2018-22 for 2024, published December 2023), not the registered ACS 2020-24 CSV (look-ahead). It is passed on the prior frame, so the `[count] hisp_share` file path of the registered configs is not exercised; `project` uses the same column.
- v2's poll sd is the registered 0.039 + sampling term on Georgia 2022 and flat 0.039 elsewhere (sampling variances of past polls are not stored; ~0.001 difference). Georgia 2024 president polls come from the 538 president file via the drift panel.
- Minutes in the Georgia tables are on the simulated clock (synthetic arrival times, larger counties later), not wall-clock; real-night minutes are internal. Count nights are replays of one realised race each: three races, so no between-race interval exists for the Georgia tables.
- Real-night frames are the archived NYT/Edison snapshots (state-specific feed flags such as "all precincts reported" are as the snapshot has them); 34 of 62 nights have only a few frames and no call analysis.

Reproduce (deterministic; real-night inputs need `$BRUNDER_DATA/processed/us_real_nights`, internal):

    export BRUNDER_DATA=~/brunder-data-local
    uv run python analysis/us_benchmark_count.py ga --sims 200    # data_public/us_benchmark_count_ga.csv (about 3 minutes)
    uv run python analysis/us_benchmark_count.py real             # $BRUNDER_DATA/derived/us_benchmark/ (internal; about 7 minutes)
    uv run python analysis/us_benchmark_count.py tables           # markdown of the public Georgia table

Data: Georgia Secretary of State certified results by vote type (public record); FiveThirtyEight `raw_polls` / president polls (CC BY 4.0); MIT Election Data and Science Lab county returns (CC0); Census ACS (public domain). No Redistricting Data Hub file is used.

## 6. Part C: is the map's expected turnout (`turnout_exp`) reliable? (analysis/us_benchmark_turnout.py, data_public/us_benchmark_turnout.csv)

**Method.** For every 2022 and 2024 governor/Senate race with certified county returns in the unified panel (MEDSL, CC0; 108 races, 6,311 counties after dropping 3 Missouri races whose county sum disagrees with the statewide total by > 2 %), the expected two-party votes per county are rebuilt from pre-election inputs only and compared with the certified totals. Models: **v1 flat** (registered rule: last same-office midterm race x turnout_ratio 1.0, i.e. 2018 for 2022, 2020 president x 0.72 where missing; 2024: 2020 president x 1.0); **map** = `brunder.us.turnout_profile` called on that prior with the county's own midterm drop-off index (2022 analogue: 2018 top race / 2016 president, state-demeaned, capped x0.61-x1.65, state total unchanged; for 2024 the index is midterm-only so map = v1; counties without an own ratio get adj = 1, the live demographic fallback is not refitted); **v4** (`expected_votes`: 2020 president x own 2018/2016 drop-off, state flat ratio; midterm only); **unified** (`predict_vstate` x `fit_turnout` fitted on the other cycles, leave-one-cycle-out as in us_unified_backtest, never the final fit). Presidential races are not in the unified race panel, so "2024" means the 2024 governor/Senate races (presidential-year turnout). Share TV = half the sum of |predicted - actual county share of state votes|, in % of state votes.

**Thresholds fixed in advance:** the map number is reliable if median county |error| < 10 % AND mean |statewide level error| < 5 %.

Pooled, all races (108):

| model | races | median ape % | mean ape % | vote-wtd MAPE % | mean abs state level err % | state bias % | share TV % |
|---|---|---|---|---|---|---|---|
| v1 flat | 108 | 5.6 | 9.4 | 8.7 | 7.0 | +1.9 | 2.37 |
| map | 108 | 6.4 | 10.3 | 9.0 | 7.0 | +1.9 | 2.80 |
| v4 | 64 | 17.2 | 20.2 | 25.4 | 27.2 | +20.5 | 2.41 |
| unified (LOCO) | 108 | 10.0 | 14.2 | 14.8 | 13.3 | +12.3 | 2.06 |

2022 midterm (66 races; 22 in the 13 registered states):

| model | median ape % | mean ape % | vw MAPE % | state level MAE % | bias % | share TV % | registered states: median / state MAE / bias |
|---|---|---|---|---|---|---|---|
| v1 flat | 6.6 | 9.6 | 9.4 | 8.9 | +1.0 | 2.47 | 6.7 / 10.2 / -4.4 |
| map | 8.1 | 11.0 | 10.0 | 8.9 | +1.0 | 3.19 | 8.0 / 10.2 / -4.4 |
| v4 (64 races) | 17.2 | 20.2 | 25.4 | 27.2 | +20.5 | 2.41 | 19.3 / 28.4 / +12.5 |
| unified | 9.3 | 13.3 | 16.7 | 14.3 | +12.8 | 2.12 | 7.7 / 13.4 / +10.1 |

2024 presidential year (42 gov/Senate races; 13 in registered states; v1 = map, v4 not applicable):

| model | median ape % | mean ape % | vw MAPE % | state level MAE % | bias % | share TV % |
|---|---|---|---|---|---|---|
| v1 = map | 4.3 | 9.2 | 7.9 | 4.0 | +3.4 | 2.20 |
| unified | 10.7 | 15.6 | 12.4 | 11.9 | +11.4 | 1.95 |

Races passing both thresholds: v1 56 %, map 50 %, unified 23 %, v4 0 %. Counties within 10 %: v1 69 % (2022) / 82 % (2024); map 60 % / 82 %; unified 53 % / 45 %.

Worst counties (map, >= 5,000 actual votes; all counties in the CSV via `rank_worst_map`): Monroe IN (18105) 2024 gov/Senate +316 % and Yamhill OR (41057) 2022 governor +237 % (certified-panel values far below the pre-election base; likely panel entries, not investigated); Vermont 2022 Senate counties -76 % to -87 % (the 2018 base race had Sanders as an independent, so the D/R base is tiny); level errors of 20-65 % in Alabama, Idaho, North Dakota, South Carolina 2022 (uncontested or lopsided base races).

**Verdict (in words).** County shape: usable. Median county error 6-8 % in 2022 (below the 10 % bar) and 4 % in 2024. Statewide level: NOT reliable. Mean absolute statewide error 8.9 % in 2022 (10.2 % in the registered states) and 4.0 % in 2024 (just under 5 %, with a +3.4 % over-prediction bias), so the pre-set rule fails overall. The level is the base race's two-party votes x turnout_ratio 1.0, which ignores whether the base race was uncontested, lopsided or third-party and the year-to-year turnout swing. The display index only redistributes votes within a state, and out of time (2018/2016 -> 2022) it made the county shape slightly worse than the flat rule (median 8.1 % vs 6.6 %, TV 3.2 % vs 2.5 %). The unified expected votes improve the share of state votes (TV 2.06 vs 2.37 %) but its statewide level (13.3 % MAE, +12 % bias) is worse than the flat rule; v4's level error (+20 %) comes from its state ratio (2018/2016 was a high-turnout midterm pair); the 2026 recipe uses 2022/2020, which has no out-of-time test here. Reading: treat `turnout_exp` as a rough within-state county guide, not as a statewide total. Caveat: the published index also has a demographic fallback for counties without own ratios, not refitted in this backtest.


## 7. Part D: consistency audit (`data_public/us_benchmark_audit.csv`; checks of configs and prior files, nothing run)

Checked per race: election block (race id, state, race key, feed, poll close, name, both candidates), markets (Polymarket and Kalshi mapping), count settings that must not differ between versions (tau, rho, sigma_mun0, f_max, f_complete, learn_turnout, vote_type, early_share), prior source, poll average and poll count across v2-v5, and the county set (unit ids) of the registered prior, the unified prior and the v4 expected-vote file.

| family | races | consistent |
|---|---|---|
| civic | 14 | 14 |
| cross-feed | 4 | 0 |
| house | 75 | 0 |
| statewide | 21 | 21 |

- **Statewide (21 races) and civicAPI (14 races): consistent.** Same feed, race key, candidates, poll close, market mapping, count settings and the same county set in every version (including the v4 file and the unified prior). Informational: civic versions use `turnout_ratio` 0.72 on a presidential base while the unified prior already contains the expected vote (1.0), and v4 exists only for CA, GA, OH, SC, TX, VT.
- **Same race on two feeds (OH and SC governor and Senate: official feed vs civicAPI): same markets and candidates**; race ids differ by design (`...-civic`), so a market maps to two model rows per race.
- **House (75 districts): INCONSISTENT between v1 and v2.** The v2 header says "identical to v1 except a prior on the district swing", but every v2 also differs in count settings: in the 37 vote-type districts (AZ, GA, NC, ...) v1 has `vote_type = true` with an early share and v2 has `vote_type = false`; in the 38 FL/MN districts `rho` is 0.6 in v1 and 0.3 in v2 (the headers cite separately recalibrated replays). A v1-vs-v2 comparison on the night therefore mixes the swing prior with a different count rule, so the House v1/v2 night comparison is not a clean test of the generic-ballot prior. Unified House matches v1's count settings.
- Pre-count part A is not affected (it scores priors only).

## 8. Part E: recommendation

Rule: section 0. Count-stage and map evidence (parts 5-6) are descriptive and are used only to veto (keep primary), never to switch. Post hoc, and only in the keep-primary direction: a candidate that meets the rule on one layer but is clearly worse (Holm-significant) on another layer of the same output is not recommended for that output.

| layer | scheme | class | n | primary | benchmark pick (secondary) | p_holm | recommendation |
|---|---|---|---|---|---|---|---|
| statewide share CRPS (pts) | pooled | all races | 264 | v3 (LOCO-refit sd) | unified +0.171 [-0.053, +0.374] | 1.000 | keep primary |
| statewide share CRPS (pts) | C1 | high | 65 | v3 (LOCO-refit sd) | unified +0.041 [-0.760, +0.745] | 1.000 | keep primary |
| statewide share CRPS (pts) | C1 | low | 102 | v3 (LOCO-refit sd) | unified +0.099 [+0.016, +0.177] | 0.856 | keep primary |
| statewide share CRPS (pts) | C1 | medium | 97 | v3 (LOCO-refit sd) | unified +0.333 [+0.113, +0.558] | 0.170 | keep primary |
| county share wMAE (pts) | pooled | all races | 260 | v3 | unified +0.487 [+0.193, +0.757] | 0.121 | keep primary |
| county share wMAE (pts) | C1 | high | 64 | v3 | unified +0.355 [-0.677, +1.268] | 1.000 | keep primary |
| county share wMAE (pts) | C1 | low | 101 | v3 | unified +0.269 [+0.141, +0.400] | 0.005 | switch to unified |
| county share wMAE (pts) | C1 | medium | 95 | v3 | unified +0.808 [+0.481, +1.148] | 0.005 | switch to unified |
| county expected-vote wMAPE (%) | pooled | all races | 132 | v3 | v3 (primary best) -1.919 [-3.096, -0.793] | 0.018 | keep primary |
| county expected-vote wMAPE (%) | C1 | high | 35 | v3 | v3 (primary best) -2.083 [-4.839, +0.548] | 1.000 | keep primary |
| county expected-vote wMAPE (%) | C1 | low | 48 | v3 | v3 (primary best) -1.522 [-3.179, -0.055] | 1.000 | keep primary |
| county expected-vote wMAPE (%) | C1 | medium | 49 | v3 | v3 (primary best) -2.190 [-4.062, -0.454] | 0.475 | keep primary |
| county turnout-share TV (%) | pooled | all races | 132 | v3 | unified +0.224 [+0.124, +0.317] | 0.005 | switch to unified |
| county turnout-share TV (%) | C1 | high | 35 | v3 | unified +0.267 [+0.097, +0.451] | 0.090 | keep primary |
| county turnout-share TV (%) | C1 | low | 48 | v3 | unified +0.144 [-0.072, +0.320] | 1.000 | keep primary |
| county turnout-share TV (%) | C1 | medium | 49 | v3 | unified +0.270 [+0.162, +0.377] | 0.005 | switch to unified |
| House district share CRPS (pts) | pooled | all races | 2223 | v2 (LOCO-refit sd) | unified +0.317 [+0.252, +0.382] | 0.005 | switch to unified |
| House district share CRPS (pts) | H1 | polled | 319 | v2 (LOCO-refit sd) | unified +0.308 [+0.059, +0.572] | 0.700 | keep primary |
| House district share CRPS (pts) | H1 | unpolled | 1904 | v2 (LOCO-refit sd) | unified +0.319 [+0.256, +0.381] | 0.005 | switch to unified |
| House district share CRPS (pts) | H2 | close (<0.05) | 463 | v2 (LOCO-refit sd) | unified +0.764 [+0.620, +0.906] | 0.005 | switch to unified |
| House district share CRPS (pts) | H2 | lean (0.05-0.15) | 991 | v2 (LOCO-refit sd) | unified +0.285 [+0.188, +0.385] | 0.005 | switch to unified |
| House district share CRPS (pts) | H2 | safe (>0.15) | 769 | v2 (LOCO-refit sd) | unified +0.089 [-0.009, +0.190] | 1.000 | keep primary |

Reading, per race class (secondary pick = best tested candidate by point estimate, with interval; "recommendation" applies the rule):

- **Statewide share / win probability, every class (low, medium, HIGH volatility; by poll count; by swing from prior; by state poll-miss history): KEEP v3** (Ohio v4 = v3 here). Unified has the best point estimate in most classes (pooled +0.17 CRPS points [-0.05, +0.37]) but no class clears the rule: in the **high-volatility tier (n=65) the gain is +0.04 points [-0.76, +0.75]**, i.e. the data cannot say anything about the class that matters most; the largest gains are in the medium tier (+0.33 [+0.11, +0.56]; Holm p 0.17) and among races whose poll average moves 3-6 points from the last result (+0.25 [+0.06, +0.45]; Holm p 0.45). v2 is as good as v3 on the point (identical mean) and worse on log loss; v1 is clearly worse everywhere; v4 and v5 are v3 at this level. The unpolled class (n=45) favours unified over the last-result rule (+0.70 CRPS points [-0.45, +1.69], not significant) but v1 = v3 there by construction.
- **County share (shape of the count prior): unified wins in the low and medium tiers** (+0.27 and +0.81 pts of vote-weighted county error; 5/5 cycles; Holm p 0.005) and the rule is met; high tier +0.36 [-0.68, +1.27] (n=64) not significant. Only the count prior is affected, not the headline; the count-stage evidence (below) does not show a benefit large enough to see in margin error on real nights.
- **County expected votes / the map's turnout number: KEEP the primary rule, but do not trust its level.** Unified's turnout *shares* are better (TV 1.92 vs 2.14 %, Holm significant), but its statewide level is worse (EV wMAPE 16.6 vs 14.7 % in midterms, p_holm 0.02; map check: level error 13 % vs 7 %). v4's county distribution gives no gain pooled (TV 2.40 vs 2.36 %) and its level is wrong (+16 % bias; +27 % EV error; +21.5 % in the Georgia 2022 replay): its single-cycle win in the 6+-poll class on turnout shares is cancelled by a 14-point loss on expected-vote error in the same class. **Not recommended.**
- **House: unified is recommended for the pre-count district probability** (pooled +0.32 CRPS points [+0.25, +0.38], 6/6 cycles, Holm p 0.005; also in unpolled, close and lean districts; NOT shown in polled districts (n=319, Holm p 0.70) or safe seats (n=769)); it also halves the Brier score of unpolled districts. This is strong enough for a pre-count headline on the House overview, with one caveat: **no House count-stage replay exists for unified** (the audit also shows House v1 and v2 differ in count settings), so the live night headline stays v2 until that is run.
- **Count stage (Georgia simulated nights, real nights):** no version clearly beats v3; unified is better in the Georgia 2022 simulation at 10-50 % counted, within noise on real Senate/governor nights, worse on turnout and later to call. v4 and v5 add nothing measurable (v5: Georgia has little Hispanic variation; it is untested where it matters, in the registered-state backtest `docs/us_hisp_slope.md`). **Keep v3 (Ohio v4) as the headline during the count.**

Class-by-class headline decision:

| race class | strongest version (secondary, point estimate) | margin over primary | enough evidence to be the reported headline? |
|---|---|---|---|
| statewide, low volatility | unified | +0.10 CRPS pts [+0.02, +0.18] (Holm p 0.86) | no, keep v3 |
| statewide, medium volatility | unified | +0.33 [+0.11, +0.56] (Holm p 0.17) | no, keep v3 |
| statewide, HIGH volatility | unified (point) | +0.04 [-0.76, +0.75] | no, keep v3 (no evidence either way) |
| statewide, unpolled | unified | +0.70 [-0.45, +1.69] | no (n=45, not significant); v1 = v3 here |
| House, any | unified | +0.32 [+0.25, +0.38] | yes for pre-count; keep v2 on the night until a House count replay exists |
| county prior shape | unified | +0.27 / +0.81 pts (low / medium) | affects the count prior only; count-stage evidence neutral |
| map's expected votes | none (level fails the pre-set bar) | n/a | no; show the number as approximate (statewide level error 7-9 %) |

## 9. Honest limits

- Five cycles, races within a cycle share a national shock: bootstrap intervals resample races within cycle and are optimistic; the cycle-consistency condition and the Holm correction compensate only partly.
- v2/v3 registered poll-sd constants were estimated on 2016-2022 polls (in-sample for 2016-2022); v3 with LOCO-refit sd is the primary baseline and the registered-sd variant is the sensitivity (differences are in the third decimal of CRPS). v2's sampling term is approximated.
- The pooled numbers for v1/v3/unified were seen before the rule (section 0, status-quo-favouring).
- v4 is reconstructible for 2022 only; v5 only exists during the count. Part B reconstructions of past configs are by rule (see section 5).
- Of the schemes and tests run, none was discarded; the one change after the first run was dropping v1 as a candidate on the two turnout layers (identical to the primary by construction: 126 -> 106 tests; no result changed). All 152 comparison rows (106 tested, 46 not tested for n<30) are in `data_public/us_benchmark_comparisons.csv`.
- RDH/L2 data are not used anywhere in this benchmark.

Reproduce: `export BRUNDER_DATA=~/brunder-data-local; uv run python -W ignore analysis/us_benchmark.py all; uv run python analysis/us_benchmark_count.py ga --sims 200; uv run python analysis/us_benchmark_turnout.py`.
