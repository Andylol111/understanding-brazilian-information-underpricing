# Unified pre-count model (Senate / governor / House): one prior from every signal, handed to the count

**Status: an unregistered challenger, to be registered by the integrator as `configs/us2026_unified/*` (labelled
"unified"). It does not replace v1..v5.** Frozen inputs: polls and the national environment as of 2026-11-02, parameters
fitted on 2016-2024 only, no 2026 count data. Rebuild with

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python -W ignore analysis/us_unified_backtest.py all        # leave-one-cycle-out backtest + data_public/us_unified_params.json
uv run python -W ignore analysis/us_unified_2026.py statewide      # data_public/us2026_unified_priors.csv + configs/us2026_unified/*.toml
uv run python -W ignore analysis/us_unified_house_2026.py backtest # House LOCO
uv run python -W ignore analysis/us_unified_house_2026.py build    # data_public/us2026_unified_house_priors.csv + configs/.../house/*.toml
uv run python -W ignore analysis/us_unified_replay.py sim --n 30   # Georgia 2022 count-night replay
```

Phase 1 builders (kept with their READMEs in `$BRUNDER_DATA/processed/us2026/unified/README_Uni*.md`):
`analysis/us_unified_{results,demo,macro,ev,house}.py`. Library: `src/brunder/us_unified.py`. Tests: `tests/test_us_unified.py`.
Machine-readable results: `results/us_unified/*.csv` (gitignored; every table below is reproduced from them).

## The plain answer

- **Polled statewide races (219, 2016-2024, leave-one-cycle-out):** the unified mean is slightly better than the v3 poll
  average (MAE 2.45 vs 2.55 points, CRPS 1.77 vs 1.82), Brier 0.0616 vs 0.0646, log loss 0.188 vs 0.200, 90 % coverage 0.881
  vs 0.877. The gain is small, comes from a mild pull toward the last-presidential / same-office lean when there are few
  polls, and is **not significant** (219 races; per-cycle it is better in 2018, 2020, 2024 and worse in 2016, 2022 by
  0.04-0.05 points). Where polls exist, polls dominate: the fundamentals weight is k/(k+√n) = 0.5/(0.5+n^0.5).
- **Unpolled statewide races (45):** unified MAE 4.63 vs 5.81 for the registered v1 rule (last same-office result, no shift), Brier
  0.022 vs 0.034. This is the real gain of the "one model" idea: v1 has no national swing.
- **County priors (the part the count consumes):** given the same statewide share, the county prior is better than v1's
  (vote-weighted RMSE 2.03 vs 2.71 points) and better than the last presidential result alone (2.17). Deployed (with the
  predicted statewide share) county-share MAE is **2.6 vs 4.6 (v1) and 2.9 (v3: v1 + uniform poll shift)**; the county
  turnout share distance falls from 2.27 % to 1.87 % of the state's votes. The statewide expected-vote level is barely better
  than v1's (absolute log error 0.135 vs 0.152): the national turnout level of a midterm is not forecastable from 5 cycles.
- **Count replay (Georgia 2022 governor and Senate, certified returns by vote type, simulated order):** better early,
  not better late (section 6).
- **Does it beat v3 out of sample?** On point error and CRPS marginally yes, on win-probability scores marginally yes, none of
  it statistically distinguishable from zero; on the unpolled races and on counties clearly yes versus v1. It is registered
  as a labelled challenger, not a replacement.

## 1. Data panel and coverage per signal (Phase 1)

| signal | source (all public) | years / coverage | vintage rule | used |
|---|---|---|---|---|
| county president, Senate, governor, House two-party results | MEDSL county/precinct returns 2008-2024 (CC0), GA SoS, CA SoS (`results_county.parquet`, validated against official state totals in `results_validation.csv`: 723 pass, 32 fail, 18 no reference) | pres 2008-2024; Senate/governor 2016-2024 incl. specials and runoffs (kept separate); House totals 2016-2024. Gaps: TN 2022 governor, IN 2020 governor (53 counties), LA jungle races, AK by district (state level only), CT planning regions from 2022 | results of cycle t-2 / t-4 only | yes (core) |
| statewide results and incumbent party | `results_state.csv` (+ MEDSL 1976-2024 state files) | 789 contests; incumbent_party = party of the winner of the seat's previous regular election (manual override list) | known before t | seat holder (small weight) |
| polls | FiveThirtyEight `raw_polls` 2006-2022 and 538 2024 files via `data_public/us_state_drift_panel.csv` (v3 rule: √n weight, 14-day half-life, 45-day window, 25 % pollster cap); VoteHub 2026 (`data_public/us2026_polls.json`, as of 2026-11-02) | 710 polled races 2006-2024; 219 of the 264 statewide races of 2016-2024 | field end <= Election Day - 1 | yes |
| census / ACS | ACS 5-year ending t-2 (published Dec t-1; 2026: 2020-24), 17 county columns incl. education, Hispanic, Black, age 65+, 18-29, density and their changes vs t-4 and t-2; PEP population growth | all counties 2012-2026 (`county_demo.parquet`) | vintage ending t-2 | yes, shrunk to ~0 (section 3) |
| longer partisan trend | county president(b) - president(b-4) | 2016-2026 | | yes, ~0 weight |
| national environment | generic-ballot average (538 polls 2018-2024, 538 topline 2006-2016, VoteHub 2026), presidential approval (Gallup/APP, Wikipedia poll tables, VoteHub), UMich sentiment, unemployment, real DPI, CPI (ALFRED vintages dated T) | cycles 2006-2026 (`nat_panel.csv`) | as of Election Day - 4 | generic ballot only (section 2) |
| early vote | NC (every day 45..0, party, age, sex, race, 5 cycles), MD (in person), CO (county totals 2016, 2020-24), FL (one snapshot per election), TX (2016, 2018: 15 / 30 counties), PA/OK/NM/KS/LA state level (`ev_hist_*.parquet`); 2026 database `us_ev/ev.sqlite` (EVDB) | 14 state-cycles with an EV gap | days_before aligned | statewide EV gap **not used** (rule below); county EV share **not used** in the registered prior (not enough overlap, section 5) |
| House districts | MEDSL 1976-2024 House (official), 2016 precinct president/House splits, districts_2024.json, district polls 538 2018-2024 and VoteHub 2026 (`house_panel.parquet`) | same-line transitions 2014, 2016, 2018, 2020, 2024 (+ 2022 with the 2020 president on 2022 lines); 2026: 262 same-line and 173 redrawn districts | | yes |

Per-signal details, gaps and licences: the READMEs listed above. Not available: historical enthusiasm / registration-growth
series for most states (registration by county exists for NC, PA, CO, FL only), TX county early vote 2020-2024,
county age/sex early vote outside NC/MD.

## 2. The model (Phase 2)

1. **National swing** N = -0.017 + 0.676·(generic ballot average − last national presidential two-party share) (house swing,
   contested seats). Approval, consumer sentiment, unemployment and income terms were offered with ridge shrinkage and a
   10 %-improvement rule; none passed (below). 2026: **+2.03 points** (generic ballot 54.8 % from only 2 VoteHub polls;
   the raw shift of +5.5 used by the registered House v2 overstated the Democrats' realised swing by 2.7 points RMSE in 2006-2024).
2. **State share** `F = 0.513 + Σ b·x` on the last presidential lean of the state, the last same-office lean, the seat
   holder's party and the national swing (Lc +0.434, Oc_f +0.284, inc +0.034, swing_hat +0.837), blended with the poll average with weight k/(k+n^p) on F
   (k = 0.5, p = 0.5); a ridge-shrunk, mean-zero correction by poll lean and seat holder; predictive sd by number of polls
   (percent: 0: 7.45, 1: 4.80, 2: 3.03, 3: 2.80, 4: 2.26; bins 0 | 1-2 | 3-5 | 6-10 | 11+ polls) from out-of-fold residuals. A cycle-wide common error exists (sd 1.64 points) but
   the registered output is each race's marginal.
3. **County share** `s0 = S + Σ γ_j·(x_cj − x̄_j)` where x are the county's deviations from its state (baseline-vote weighted) in
   the last presidential result, last same-office result, last House result (core, unpenalised), the pre-2026 trend and
   demographic levels and shifts (ridge-penalised), then re-centred so that the expected-vote weighted mean equals S.
4. **County expected votes** `V = V_state · share_c`; share_c from the log share of the last presidential vote, the last
   same-office and House vote shares and population growth; V_state = geometric mean of (last presidential votes × midterm
   ratio) and (last same-office votes × midterm→midterm ratio).
5. **Output** `data_public/us2026_unified_priors.csv` (source `uni_<st>_<office>_2026`: dem+rep = V, dem/(dem+rep) = s0) and per
   config `[model] swing_prior_mean = 0`, `swing_prior_sd` = the predictive sd, `count_scaled_terms = true`, `turnout_ratio = 1`;
   count settings (tau, rho, vote_type, f_complete …) are those of the registered v1 of the same feed. The count itself is
   `brunder.nowcast` / `brunder.us.project`, unchanged.

### Weights learned out of sample (what helps, what gets ~0)

National swing, LOCO RMSE of the swing in points (10 cycles): the table shows the generic ballot alone is 1.72, the raw
generic ballot 2.68, a mean swing 3.78; the extras (approval, sentiment, unemployment, income) improve at most 7 %
(approval, λ = 0.1) and are rejected by the 10 % rule (and approval 2026 = -25 lies outside the fitted range).

| extras | lam | loco_rmse | loco_mae |
|---|---|---|---|
| - | 0.000 | 1.716 | 1.313 |
| app | 0.100 | 1.595 | 1.387 |
| app | 1.000 | 1.636 | 1.305 |
| ums | 0.100 | 1.679 | 1.401 |
| ums | 1.000 | 1.666 | 1.361 |
| unr | 0.100 | 2.271 | 1.734 |
| unr | 1.000 | 1.956 | 1.538 |
| dpi | 0.100 | 1.879 | 1.504 |
| dpi | 1.000 | 1.782 | 1.400 |
| app+ums+unr+dpi | 0.100 | 1.944 | 1.594 |
| app+ums+unr+dpi | 1.000 | 1.838 | 1.539 |
| app+ums+unr+dpi | 10.000 | 1.729 | 1.363 |
| raw generic ballot (a=0,b=1) |  | 2.675 | 2.066 |
| mean swing only |  | 3.777 | 3.202 |

County share coefficients (units of the within-state deviation of the feature; the three core terms carry the model):

| feature | coefficient |
|---|---|
| age65 | -0.007 |
| ba | 0.017 |
| black | 0.001 |
| d2_ba | 0.008 |
| d2_hisp | -0.027 |
| d_age18_29 | -0.066 |
| d_age65 | 0.004 |
| d_ba | 0.003 |
| d_black | 0.167 |
| d_hisp | -0.030 |
| d_lndens | 0.029 |
| hisp | -0.021 |
| lndens | 0.001 |
| lninc | 0.003 |
| pop_chg4 | 0.000 |
| pop_g | 0.001 |
| sh_f | 0.100 |
| so_f | 0.153 |
| sp_b | 0.704 |
| trend | -0.025 |

In share points per one standard deviation of the feature the core terms are 10.0 (president), 2.1 (last same-office), 1.4 (last
House) and every demographic / trend / growth term is below 0.2. They are kept at their shrunk values (ridge λ chosen by
leave-one-cycle-out, = 1) rather than set to zero by hand: **demographic levels and shifts, the long trend and the economic
terms help by < 0.1 points in county RMSE; they are effectively unused.** The EV composition gap of the statewide race
(14 covered races, 3 states) fails the pre-registered inclusion rule of `docs/us_ev_gap.md` (needs >= 30 races and 3 states with the
bootstrap interval below zero) and has weight 0; cycle pooling of poll residuals (a state's miss predicted from the others')
fitted to ~0 (signs flip by cycle; `cycle_common_sd` is reported only).

## 3. Backtest (Phase 3): statewide races, leave-one-cycle-out 2016-2024 (nested: every hyper-parameter chosen without the held-out cycle)

Share error in points of two-party share, P(win) = Φ((mean − ½)/sd). v1 = last same-office result (else last president) with its
sd refit (best case for v1); v3 = poll average with the registered poll-count sd (`POLL_SD_BINS`).

| subset | label | n | mae | rmse | brier | logloss | cov90 | crps | sd_mean |
|---|---|---|---|---|---|---|---|---|---|
| all races | unified | 264 | 2.821 | 4.280 | 0.055 | 0.174 | 0.886 | 2.115 | 3.748 |
| all races | v1 last race (sd refit) | 264 | 5.726 | 8.153 | 0.124 | 0.428 | 0.890 | 4.365 | 8.026 |
| polled | unified | 219 | 2.449 | 3.269 | 0.062 | 0.188 | 0.881 | 1.770 | 2.975 |
| polled | v1 last race (sd refit) | 219 | 5.710 | 8.065 | 0.142 | 0.484 | 0.890 | 4.338 | 7.918 |
| polled | v3 poll avg (registered sd) | 219 | 2.552 | 3.301 | 0.065 | 0.200 | 0.877 | 1.816 | 3.118 |
| polled | v3 poll avg (sd refit) | 219 | 2.552 | 3.301 | 0.064 | 0.201 | 0.877 | 1.832 | 3.206 |
| unpolled | unified | 45 | 4.630 | 7.447 | 0.022 | 0.108 | 0.911 | 3.795 | 7.508 |
| unpolled | v1 last race (sd refit) | 45 | 5.807 | 8.570 | 0.034 | 0.151 | 0.889 | 4.495 | 8.555 |
| unpolled | fundamentals only | 45 | 4.630 | 7.447 | 0.022 | 0.108 | 0.911 | 3.795 | 7.508 |

By number of polls:

| subset | label | n | mae | rmse | brier | logloss | cov90 | crps | sd_mean |
|---|---|---|---|---|---|---|---|---|---|
| polled n<=5 | unified | 91 | 3.024 | 4.129 | 0.010 | 0.044 | 0.857 | 2.261 | 3.820 |
| polled n<=5 | v1 last race (sd refit) | 91 | 6.574 | 9.345 | 0.073 | 0.268 | 0.890 | 5.009 | 9.216 |
| polled n<=5 | v3 poll avg (registered sd) | 91 | 3.058 | 3.970 | 0.014 | 0.057 | 0.868 | 2.203 | 3.737 |
| polled n<=5 | v3 poll avg (sd refit) | 91 | 3.058 | 3.970 | 0.014 | 0.060 | 0.857 | 2.228 | 3.904 |
| polled n>5 | unified | 128 | 2.041 | 2.482 | 0.098 | 0.289 | 0.898 | 1.421 | 2.374 |
| polled n>5 | v1 last race (sd refit) | 128 | 5.095 | 7.014 | 0.191 | 0.638 | 0.891 | 3.861 | 6.995 |
| polled n>5 | v3 poll avg (registered sd) | 128 | 2.193 | 2.727 | 0.101 | 0.302 | 0.883 | 1.540 | 2.677 |
| polled n>5 | v3 poll avg (sd refit) | 128 | 2.193 | 2.727 | 0.100 | 0.302 | 0.891 | 1.550 | 2.709 |

By cycle, polled races (unified vs v3):

| cycle | label | n | mae | brier | logloss | cov90 | crps |
|---|---|---|---|---|---|---|---|
| 2016 | unified | 43 | 2.879 | 0.084 | 0.238 | 0.837 | 2.058 |
| 2016 | v3 poll avg (registered sd) | 43 | 2.836 | 0.088 | 0.248 | 0.860 | 2.001 |
| 2018 | unified | 54 | 1.770 | 0.080 | 0.234 | 0.926 | 1.369 |
| 2018 | v3 poll avg (registered sd) | 54 | 1.841 | 0.074 | 0.224 | 0.944 | 1.356 |
| 2020 | unified | 34 | 3.533 | 0.055 | 0.172 | 0.735 | 2.437 |
| 2020 | v3 poll avg (registered sd) | 34 | 3.887 | 0.075 | 0.226 | 0.794 | 2.634 |
| 2022 | unified | 56 | 2.449 | 0.045 | 0.146 | 0.911 | 1.804 |
| 2022 | v3 poll avg (registered sd) | 56 | 2.405 | 0.044 | 0.153 | 0.839 | 1.805 |
| 2024 | unified | 32 | 1.869 | 0.037 | 0.129 | 0.969 | 1.290 |
| 2024 | v3 poll avg (registered sd) | 32 | 2.213 | 0.042 | 0.148 | 0.938 | 1.491 |

Chronological variant (train on earlier cycles only; 2020, 2022, 2024):

| subset | label | n | mae | rmse | brier | logloss | cov90 | crps | sd_mean |
|---|---|---|---|---|---|---|---|---|---|
| polled | unified | 122 | 2.520 | 3.253 | 0.045 | 0.149 | 0.852 | 1.815 | 3.001 |
| polled | v1 last race (sd refit) | 122 | 5.108 | 7.314 | 0.107 | 0.368 | 0.820 | 4.046 | 6.845 |
| polled | v3 poll avg (registered sd) | 122 | 2.767 | 3.516 | 0.052 | 0.172 | 0.852 | 1.954 | 3.110 |
| polled | v3 poll avg (sd refit) | 122 | 2.767 | 3.516 | 0.054 | 0.181 | 0.861 | 1.989 | 3.373 |
| unpolled | unified | 33 | 5.433 | 8.370 | 0.007 | 0.047 | 0.818 | 4.276 | 5.731 |
| unpolled | v1 last race (sd refit) | 33 | 6.208 | 9.483 | 0.031 | 0.148 | 0.848 | 4.993 | 9.437 |
| unpolled | fundamentals only | 33 | 5.433 | 8.370 | 0.007 | 0.047 | 0.818 | 4.276 | 5.731 |

**Reading.** On polled races the unified model matches or slightly beats v3; its win-probability scores are better than v3's
in 3 of 5 cycles' Brier and clearly in the chronological run (0.045 vs 0.052). The differences are within noise. It is clearly better than v1 everywhere
(v1 has no swing). On unpolled races the gain over v1 is 1.2 points of MAE; fundamentals alone are poor (sd 7.5 points), so
P(win) for unpolled seats is wide by construction.

### County priors

Given the true statewide share (isolates the county structure; vote-weighted points; 260 races):

| model | races | wRMSE_pts | wMAE_pts | RMSE_pts_unweighted |
|---|---|---|---|---|
| last president, shifted | 260.000 | 2.166 | 1.582 | 3.092 |
| unified | 260.000 | 2.032 | 1.488 | 2.939 |
| v1 (last race, shifted) | 260.000 | 2.707 | 1.939 | 3.985 |

Deployed (predicted statewide share, predicted expected votes; "v3" = v1 counties shifted uniformly to the poll average):

| model | races | county_share_wMAE_pts | county_share_wRMSE_pts | turnout_share_TV_pct | state_votes_abs_logerr | state_votes_logerr_bias |
|---|---|---|---|---|---|---|
| unified | 260.000 | 2.623 | 3.440 | 1.865 | 0.135 | -0.016 |
| unified (+demographic turnout terms) | 260.000 | 2.623 | 3.440 | 1.876 | 0.135 | -0.016 |
| v1 | 260.000 | 4.591 | 6.611 | 2.268 | 0.152 | -0.068 |
| v3 (polls, uniform shift) | 216.000 | 2.896 | 3.857 | 2.267 | 0.147 | -0.066 |

## 4. House districts (leave-one-cycle-out on same-line transitions 2014-2024; 2223 contested district-cycles)

F = lean of the district's last presidential result, last House result, seat holder, generic-ballot swing (k = 0.5, with district polls
where they exist: 319 district-cycles); sd by polled / not polled. v1 = president by district, no shift (sd refit); v2 = + the generic-ballot
shift with the registered sd 0.052.

| subset | label | n | mae | rmse | brier | logloss | cov90 | crps |
|---|---|---|---|---|---|---|---|---|
| all contested | unified | 2223 | 3.184 | 4.138 | 0.033 | 0.119 | 0.900 | 2.300 |
| all contested | v1 president, no shift (sd refit) | 2223 | 3.971 | 5.268 | 0.053 | 0.182 | 0.900 | 2.907 |
| all contested | v2 + generic ballot shift (sd 0.052) | 2223 | 3.518 | 4.800 | 0.047 | 0.168 | 0.924 | 2.615 |
| all contested | v2 shift (sd refit) | 2223 | 3.518 | 4.800 | 0.046 | 0.166 | 0.900 | 2.617 |
| polled | unified | 319 | 2.756 | 3.427 | 0.133 | 0.417 | 0.862 | 1.956 |
| polled | v1 president, no shift (sd refit) | 319 | 3.604 | 4.755 | 0.134 | 0.418 | 0.953 | 2.628 |
| polled | v2 + generic ballot shift (sd 0.052) | 319 | 2.994 | 4.238 | 0.130 | 0.426 | 0.953 | 2.287 |
| polled | v2 shift (sd refit) | 319 | 2.994 | 4.238 | 0.130 | 0.424 | 0.953 | 2.264 |
| polled | district poll average alone (sd 0.0429 bin) | 319 | 3.024 | 3.990 | 0.143 | 0.454 | 0.818 | 2.203 |
| unpolled | unified | 1904 | 3.256 | 4.245 | 0.017 | 0.069 | 0.907 | 2.357 |
| unpolled | v1 president, no shift (sd refit) | 1904 | 4.032 | 5.349 | 0.040 | 0.143 | 0.891 | 2.954 |
| unpolled | v2 + generic ballot shift (sd 0.052) | 1904 | 3.606 | 4.887 | 0.033 | 0.124 | 0.920 | 2.670 |
| unpolled | v2 shift (sd refit) | 1904 | 3.606 | 4.887 | 0.032 | 0.122 | 0.891 | 2.676 |
| close (2-party share 0.40-0.60) | unified | 794 | 2.931 | 3.823 | 0.089 | 0.312 | 0.906 | 2.121 |
| close (2-party share 0.40-0.60) | v1 president, no shift (sd refit) | 794 | 3.599 | 4.737 | 0.123 | 0.407 | 0.940 | 2.625 |
| close (2-party share 0.40-0.60) | v2 + generic ballot shift (sd 0.052) | 794 | 3.019 | 4.222 | 0.112 | 0.391 | 0.948 | 2.304 |
| close (2-party share 0.40-0.60) | v2 shift (sd refit) | 794 | 3.019 | 4.222 | 0.111 | 0.392 | 0.938 | 2.285 |

| cycle | label | n | mae | brier | cov90 |
|---|---|---|---|---|---|
| 2014 | unified | 318 | 3.972 | 0.033 | 0.843 |
| 2014 | v1 president, no shift (sd refit) | 318 | 5.974 | 0.065 | 0.736 |
| 2014 | v2 + generic ballot shift (sd 0.052) | 318 | 4.735 | 0.050 | 0.846 |
| 2014 | v2 shift (sd refit) | 318 | 4.735 | 0.048 | 0.780 |
| 2016 | unified | 371 | 3.842 | 0.025 | 0.863 |
| 2016 | v1 president, no shift (sd refit) | 371 | 5.106 | 0.061 | 0.792 |
| 2016 | v2 + generic ballot shift (sd 0.052) | 371 | 4.960 | 0.056 | 0.830 |
| 2016 | v2 shift (sd refit) | 371 | 4.960 | 0.055 | 0.755 |
| 2018 | unified | 392 | 3.318 | 0.050 | 0.872 |
| 2018 | v1 president, no shift (sd refit) | 392 | 3.707 | 0.061 | 0.936 |
| 2018 | v2 + generic ballot shift (sd 0.052) | 392 | 3.105 | 0.049 | 0.949 |
| 2018 | v2 shift (sd refit) | 392 | 3.105 | 0.049 | 0.946 |
| 2020 | unified | 405 | 3.592 | 0.039 | 0.889 |
| 2020 | v1 president, no shift (sd refit) | 405 | 2.726 | 0.053 | 0.963 |
| 2020 | v2 + generic ballot shift (sd 0.052) | 405 | 3.667 | 0.050 | 0.943 |
| 2020 | v2 shift (sd refit) | 405 | 3.667 | 0.048 | 0.931 |
| 2022 | unified | 339 | 2.474 | 0.030 | 0.956 |
| 2022 | v1 president, no shift (sd refit) | 339 | 3.347 | 0.046 | 0.956 |
| 2022 | v2 + generic ballot shift (sd 0.052) | 339 | 2.558 | 0.044 | 0.976 |
| 2022 | v2 shift (sd refit) | 339 | 2.558 | 0.043 | 0.976 |
| 2024 | unified | 398 | 2.001 | 0.022 | 0.972 |
| 2024 | v1 president, no shift (sd refit) | 398 | 3.368 | 0.036 | 0.985 |
| 2024 | v2 + generic ballot shift (sd 0.052) | 398 | 2.275 | 0.033 | 0.987 |
| 2024 | v2 shift (sd refit) | 398 | 2.275 | 0.032 | 0.987 |

Redrawn districts (173 of 435 in 2026: AL CA FL LA NC OH TN TX UT) use crosswalk estimates; their prior sd is widened by 2.0 points
(**a judgment, not calibrated**: no same-line history exists for the 2026 maps). 2026 forecast: 241.2 Democratic seats expected of 435
(registered House baseline: 254.7 with the full +5.5 shift). District county pieces get s0 = S_d + 0.957·(piece − district presidential share)
and V = 0.789 × the piece's 2024 presidential two-party votes (the 2018/2022 midterm ratio of House to presidential votes).

## 5. Early vote

Official county early-vote history exists for NC, MD, CO (and 2016/2018 TX large counties). The EV-pace diagnostic
(`$BRUNDER_DATA/processed/us2026/unified/ev_pace_diagnostic.csv`): a county's EV share blended with last cycle's final share reduces the
share error 17-18 % at 3-5 days in NC, 4 % in MD, -9 % in CO, -13 % in FL; the 2026 database has county EV for NC, FL, CO, MD, OK, PA, TX
but history of the same fields only for NC / MD / CO. With one usable state the weight cannot be learned out of sample, so the
registered prior **does not use the county EV**. This is a documented gap: the party and age composition fields are NC-only history.
The count model itself already uses early / Election Day vote types (`vote_type`).

## 6. Count-night replay (Georgia 2022, certified by vote type, order as `analysis/us_calibrate.py`, 30 simulated nights per race)

Priors compared inside `brunder.us.project` with the same count settings: unified (fitted without 2022; statewide forecast from the
leave-one-cycle-out table), registered v1 (2020 president × 0.8) and a v3-style prior (v1 + the poll swing with the registered poll sd).
`minutes_to_90`: simulated minutes until P(true winner) >= 0.9 and stays so; `decided` = share of nights where that happens.

| race | variant | decided | minutes_to_90_median | counted_at_90_median | pre_P | pre_p_correct | pre_err | pre_sd |
|---|---|---|---|---|---|---|---|---|
| ga_gov_2022 | unified | 1.000 | 76.576 | 0.550 | 0.040 | 0.960 | -0.005 | 0.025 |
| ga_gov_2022 | v1 (2020 president x0.8) | 1.000 | 135.950 | 0.700 | 0.511 | 0.489 | 0.039 | 0.047 |
| ga_gov_2022 | v3-style (v1 + poll swing) | 1.000 | 80.149 | 0.550 | 0.063 | 0.937 | -0.001 | 0.025 |
| ga_sen_2022 | unified | 0.000 |  |  | 0.551 | 0.551 | -0.002 | 0.025 |
| ga_sen_2022 | v1 (2020 president x0.8) | 0.033 | 287.697 | 0.900 | 0.511 | 0.511 | -0.004 | 0.047 |
| ga_sen_2022 | v3-style (v1 + poll swing) | 0.167 | 295.240 | 0.900 | 0.532 | 0.532 | -0.003 | 0.025 |

By checkpoint (share counted):

| variant | check | mae_pts | cover90 | p_correct |
|---|---|---|---|---|
| unified | 0.050 | 0.702 | 1.000 | 0.747 |
| unified | 0.250 | 1.520 | 1.000 | 0.808 |
| unified | 0.500 | 1.588 | 0.967 | 0.857 |
| unified | 0.750 | 0.387 | 1.000 | 0.710 |
| unified | 0.900 | 0.155 | 1.000 | 0.854 |
| v1 (2020 president x0.8) | 0.050 | 3.171 | 1.000 | 0.640 |
| v1 (2020 president x0.8) | 0.250 | 3.578 | 1.000 | 0.689 |
| v1 (2020 president x0.8) | 0.500 | 2.964 | 0.967 | 0.760 |
| v1 (2020 president x0.8) | 0.750 | 0.285 | 1.000 | 0.864 |
| v1 (2020 president x0.8) | 0.900 | 0.043 | 1.000 | 0.919 |
| v3-style (v1 + poll swing) | 0.050 | 1.013 | 1.000 | 0.733 |
| v3-style (v1 + poll swing) | 0.250 | 1.766 | 1.000 | 0.798 |
| v3-style (v1 + poll swing) | 0.500 | 1.827 | 0.967 | 0.875 |
| v3-style (v1 + poll swing) | 0.750 | 0.099 | 1.000 | 0.849 |
| v3-style (v1 + poll swing) | 0.900 | 0.040 | 1.000 | 0.938 |

**Reading.** Before and early in the count the unified prior gives the smallest error (5 / 25 / 50 % counted: 0.7 / 1.5 / 1.6 vs 3.2 / 3.6 / 3.0 for v1
and 1.0 / 1.8 / 1.8 for v3-style) and the governor race is called ~60 minutes before v1 and about as fast as v3-style. In the last quarter of the count it is
**not better and slightly worse** than v1/v3-style (75 / 90 % counted: 0.39 / 0.16 points vs 0.29 / 0.04 and 0.10 / 0.04), and the 0.5-point Senate race is
never called (it is a coin flip; v1 / v3-style call it on 3 % / 17 % of nights, at ~90 % counted). Two races, one state and a simulated order: this is a
check, not a calibration; `sigma_mun0`, tau and rho were not re-tuned for the unified prior (registered v1 values).
No recorded archive of a past US night was found under `$BRUNDER_DATA` (night/ and raw/snap hold Brazil and 2026 pre-count recordings); the GA/TX/FL/NC rehearsal archives of
`analysis/us_night_replay.py` are priors-derived (their "count" is built from the same prior races), so replaying them with unified priors would
measure nothing independent. The offline runner that applies the unified priors to a real recorded archive is `analysis/us_unified_replay.py export`.

## 7. 2026 (frozen 2026-11-02)

Unified forecast per statewide race (two-party Democratic share; `v3 P(D)` from the poll average and the registered poll-count sd):

| race | polls | poll avg | fundamentals F | unified mean | unified sd | unified P(D) | v3 P(D) (poll avg, registered sd) |
|---|---|---|---|---|---|---|---|
| az-gov | 3 | 0.530 | 0.557 | 0.538 | 0.030 | 0.894 | 0.819 |
| ca-gov | 5 | 0.629 | 0.640 | 0.634 | 0.030 | 1.000 | 1.000 |
| fl-gov | 11 | 0.476 | 0.444 | 0.471 | 0.023 | 0.096 | 0.146 |
| fl-sen | 10 | 0.467 | 0.455 | 0.464 | 0.028 | 0.099 | 0.161 |
| ga-gov | 8 | 0.493 | 0.485 | 0.492 | 0.028 | 0.382 | 0.422 |
| ga-sen | 7 | 0.551 | 0.565 | 0.555 | 0.028 | 0.974 | 0.935 |
| mn-gov | 3 | 0.547 | 0.589 | 0.559 | 0.030 | 0.974 | 0.922 |
| mn-sen | 3 | 0.524 | 0.602 | 0.544 | 0.030 | 0.925 | 0.763 |
| nc-sen | 9 | 0.560 | 0.489 | 0.549 | 0.028 | 0.961 | 0.964 |
| ne-gov | 3 | 0.465 | 0.421 | 0.453 | 0.030 | 0.061 | 0.146 |
| ne-sen | 3 | 0.490 | 0.421 | 0.472 | 0.030 | 0.181 | 0.377 |
| nm-gov | 0 |  | 0.591 | 0.591 | 0.074 | 0.889 |  |
| nm-sen | 1 | 0.600 | 0.598 | 0.601 | 0.048 | 0.983 | 0.990 |
| oh-gov | 13 | 0.515 | 0.441 | 0.505 | 0.023 | 0.579 | 0.740 |
| oh-sen | 13 | 0.520 | 0.472 | 0.513 | 0.023 | 0.713 | 0.805 |
| pa-gov | 4 | 0.623 | 0.586 | 0.617 | 0.030 | 1.000 | 1.000 |
| sc-gov | 2 | 0.428 | 0.437 | 0.429 | 0.048 | 0.068 | 0.046 |
| sc-sen | 3 | 0.490 | 0.425 | 0.473 | 0.030 | 0.190 | 0.378 |
| tx-gov | 18 | 0.486 | 0.455 | 0.481 | 0.023 | 0.205 | 0.270 |
| tx-sen | 20 | 0.514 | 0.460 | 0.508 | 0.023 | 0.631 | 0.736 |
| vt-gov | 2 | 0.508 | 0.496 | 0.507 | 0.048 | 0.559 | 0.578 |
| co-gov | 0 |  | 0.621 | 0.621 | 0.074 | 0.948 |  |
| co-sen | 0 |  | 0.614 | 0.614 | 0.074 | 0.937 |  |
| ia-gov | 12 | 0.534 | 0.445 | 0.521 | 0.023 | 0.829 | 0.932 |
| ia-sen | 12 | 0.498 | 0.454 | 0.492 | 0.023 | 0.355 | 0.474 |
| ks-gov | 3 | 0.476 | 0.536 | 0.490 | 0.030 | 0.369 | 0.233 |
| ks-sen | 3 | 0.509 | 0.432 | 0.490 | 0.030 | 0.370 | 0.603 |
| mi-gov | 11 | 0.548 | 0.580 | 0.554 | 0.023 | 0.991 | 0.982 |
| mi-sen | 17 | 0.524 | 0.567 | 0.531 | 0.023 | 0.913 | 0.857 |
| ny-gov | 5 | 0.579 | 0.605 | 0.587 | 0.030 | 0.998 | 0.991 |
| wi-gov | 3 | 0.520 | 0.571 | 0.533 | 0.030 | 0.862 | 0.721 |

Where the unified model and v3 differ most (OH, IA, NC): the polls and the fundamentals disagree (OH: 13 polls 51.5-52 % D vs F = 44-47 %), so the unified mean sits
1-2.5 points below the poll average. Races without polls (NM governor, CO governor and Senate) are forecast from F alone with sd 7.5 points
and carry the v1-style "past results + national swing" estimate.

## 8. Using it (freeze checklist for the integrator)

- Files to freeze: `configs/us2026_unified/*.toml` (35 statewide) and `configs/us2026_unified/house/*.toml` (75), `data_public/us2026_unified_priors.csv`,
  `data_public/us2026_unified_house_priors.csv`, `data_public/us_unified_params.json`, `data_public/us_unified_house_params.json`, `analysis/us_unified_replay.py`,
  `src/brunder/us_unified.py`.
- `brunder.us.load_priors()` does not read the unified files (it was not edited). `analysis/us_unified_replay.py export --snap <archive> --out <dir> [--house]`
  builds the priors frame and passes it into `brunder.us.Exporter(priors=…)` / `HouseExporter(priors=…)`; statewide output goes to `<out>/statewide`,
  House output to `<out>/house`.
- To refresh the polls before freezing: update `data_public/us2026_polls.json` (and `nat_panel.csv` via `analysis/us_unified_macro.py build`) and rerun the two `build`
  commands above; nothing else changes.

## 9. Limits

- 10 national cycles and 5 statewide cycles: the national swing model and the sd tables are estimated from very few independent cycle-level observations; the
  2026 generic ballot rests on 2 VoteHub polls (Ballotpedia's 19-poll average 54.3 % is not used: licence). A 1-point change in the generic ballot moves every
  prior by 0.7 points.
- Approval in 2026 (-25) is outside the 2006-2024 range; the model does not use it.
- Specials / incumbency: the seat holder's party is used, not whether the incumbent runs (unknown before the ballot lists for most statewide races).
- Early-vote county composition and enthusiasm are not in the registered prior (section 5); registration growth exists for 4 states only.
- The replay covers Georgia 2022 only; the late-count error is not better than the registered priors.
- **Not done: comparison with market prices.** No archived Polymarket / Kalshi price series for the 2016-2024 statewide races or House districts was assembled in this
  work (the markets of 2018 / 2020 did not exist on these venues), so P(win) is scored against outcomes only (Brier, log loss, coverage), not against the markets.
  For 2026 the pre-count unified probabilities sit beside the market prices in the usual pre-count dashboard once the configs are registered.
- **County turnout** is scored as the total-variation distance between the predicted and actual county shares of a race's votes (% of the state's votes that
  would have to move) and the statewide log error of expected votes, not as a per-county absolute error of vote counts.
