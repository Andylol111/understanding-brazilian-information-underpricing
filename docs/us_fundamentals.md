# US 2026 fundamentals: county panel and out-of-sample tests

**Decision: no fundamentals adjustment.** None of economics, migration composition, net migration, population growth,
or their combination improves county margin RMSE in every next-cycle test. The hierarchical version is much worse.
The same adjustments do not improve the 2018/2022 House Brier score. No v5 / Ohio v6 was generated; v3 stays the
primary county model and v4 stays the turnout secondary.

Reproduce with:

```sh
BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_fundamentals.py fetch panel tests house
```

The public derived panel is `data_public/us_county_panel.csv`; detailed tables are written to
`$BRUNDER_DATA/processed/us2026/fundamentals/` (gitignored). Nothing writes a config, registered file, frozen poll
average, or registry entry.

## Data and timing

All sources are official/public domain except the election and polling datasets with the licences stated below.

- [Census Population Estimates Program](https://www.census.gov/programs-surveys/popest/data/data-sets.html): county
  totals and components of change, vintages 2007–2025. Each row uses the vintage published in March of that election
  year (`year - 1` estimates), not a later vintage: `pop`, annualised `pop_g`, net domestic `ndm`, and international
  `imig` migration rates.
- [IRS SOI county migration](https://www.irs.gov/statistics/soi-tax-stats-migration-data): public-domain inflow and
  outflow files. Each election uses the two pairs ending 3–4 years earlier (about the publication lag); 2026 uses
  2021–22 and 2022–23. `irs_in`, `irs_out`, and `irs_net` use exemptions as people. `inflow_d` is the exemptions-
  weighted prior presidential two-party D share of disclosed origin counties. `mig_comp = irs_in × (inflow_d - own
  prior share)`. Suppressed origins do not enter `inflow_d` but remain in total inflow.
- [BLS LAUS](https://www.bls.gov/lau/): county annual unemployment rate through `year - 1`; `dur` is the change from
  `year - 3`. [BEA CAINC1](https://www.bea.gov/data/income-saving/personal-income-county-metro-and-other-areas):
  per-capita personal income through `year - 2`; `ginc` is its two-year annualised log growth. The observations were
  public before each election. Current official files contain routine historical revisions, so a real-time-vintage
  replay would be required before ever reconsidering economics; economics loses even in this favourable revised test.
- Census ACS 2020–24 5-year static shares (`ba`, `hisp`, `black`, `lninc`) are included for joining/display only. They
  are **not** used in the out-of-time fits: the prior analysis already found static demographic levels fail across
  cycles (`docs/us_model_analysis.md`, section 2).
- [MIT MEDSL County Presidential Returns 2000–2024](https://doi.org/10.7910/DVN/VOQCHQ) and 2018/2022 precinct
  returns (CC0); 2016 precinct president/House returns (MIT licence); FEC 2022 and Clerk of House district results
  (public domain); FiveThirtyEight `raw_polls` generic-ballot polls (CC BY 4.0).

The panel is one row per county × even year 2008–2026: 31,040 rows, 4.4 MB. Alaska is excluded (returns are by state
House district); Connecticut is excluded (county units changed to planning regions in 2022). Historical FIPS mergers
are normalised. `s_pres/v_pres`, `s_house/v_house`, `s_sen/v_sen`, and `s_gov/v_gov` retain every cached result
(president 2008–24; other offices 2018/22/24). Generic `s/votes/race` is president in presidential years and the
regular Senate race, else governor, in 2018/22. The panel does not invent 2010/14 results and has no 2026 result.

## Design

For test year `t`, all coefficients are fitted only on cycles before `t` (2012 is training-only; tests are
2016/18/20/22/24). The target is county two-party D-margin change from the previous presidential election. For
midterms the same target is also reported as `incumbent drift` by multiplying by the president-party sign (RMSE and
coverage are invariant to that sign). Economic changes are signed by the president's party. States with either race
outside 15–85% two-party share are excluded. Features are clipped at training 0.5/99.5 percentiles, missing values
get the training-weighted group mean, and WLS uses square-root votes.

`state` gives every method the actual statewide swing, exactly the current county-model comparison. `national` gives
only the actual national swing, the House/unpolled-race comparison. Errors are county vote-weighted margin RMSE in
points. `Δ = model - plain uniform`; negative is better. Intervals are 90% state-cluster bootstrap intervals (1,000
resamples). Coverage is the vote-weighted share of counties inside ±1.645 training-residual SD; it is diagnostic,
not a tuned interval. Include requires at least two OOS tests, improvement in every test, and pooled 90% upper bound
below zero.

## County margin results: current statewide-uniform baseline

Each cell is `ΔRMSE [90% interval]; coverage plain/model`. IRS is unavailable for the 2016 test.

| hypothesis | 2016 | 2018 | 2020 | 2022 | 2024 | pooled Δ [90%]; wins | decision |
|---|---:|---:|---:|---:|---:|---:|---|
| economic change × incumbent | −0.10 [−0.14,−0.06]; .53/.52 | +0.14 [.05,.22]; .97/.96 | +0.19 [.05,.29]; .98/.97 | +0.08 [.01,.16]; 1.00/1.00 | −0.25 [−.35,−.14]; .98/.99 | +0.01 [−.03,.05]; 2/5 | **don't include** |
| PEP net domestic migration | +0.09 [.03,.16]; .53/.52 | −0.08 [−.12,−.05]; .97/.97 | −0.43 [−.53,−.30]; .98/.98 | +0.99 [.60,1.31]; 1.00/.99 | −0.31 [−.49,−.09]; .98/.99 | +0.05 [−.03,.14]; 3/5 | **don't include** |
| IRS inflow composition + net flow | — | +0.79 [.65,.97]; .97/.99 | −0.19 [−.28,−.08]; .98/.98 | +0.25 [.11,.37]; 1.00/1.00 | −0.18 [−.27,−.07]; .98/.99 | +0.17 [.11,.24]; 2/4 | **don't include** |
| population growth | −0.21 [−.25,−.18]; .53/.54 | +0.57 [.37,.78]; .97/.93 | −0.53 [−.68,−.39]; .98/.98 | +1.50 [1.06,1.85]; 1.00/.97 | −0.13 [−.30,.08]; .98/.98 | +0.24 [.14,.33]; 3/5 | **don't include** |
| all fundamentals | — | +3.73 [2.91,4.39]; .97/.74 | +0.47 [.19,.74]; .98/.96 | +2.10 [1.54,2.55]; 1.00/.94 | −0.02 [−.30,.32]; .98/.99 | +1.57 [1.31,1.79]; 1/4 | **don't include** |

The economic coefficients also change meaning over time: signed income growth is negative in the 2016/2018 fits and
positive by 2022/2024. Migration and growth show large 2020 gains and larger 2022 losses. These are cycle-specific
patterns, not forecastable effects.

## National swing and hierarchical model

The national-only baseline is harder (plain RMSE: 11.00, 11.80, 5.70, 6.98, 4.42 in 2016–24). Pooled ΔRMSE is
+0.08 [−.01,.17] economics; −0.04 [−.13,.07] PEP migration; +0.03 [−.05,.11] IRS migration; +0.19 [.03,.35]
population; +1.60 [1.27,1.92] all. No feature wins every cycle.

The proposed hierarchy—national swing + all features + `rho ×` the state's last-cycle residual—fails badly:

| test | rho fitted before test | current state-uniform | national-uniform | hierarchy | Δ vs national [90%] | state RMSE national / hierarchy |
|---|---:|---:|---:|---:|---:|---:|
| 2020 | −1.08 | 4.95 | 5.70 | 13.41 | +7.71 [5.56,9.77] | 2.94 / 17.05 |
| 2022 | −0.09 | 2.97 | 6.98 | 8.67 | +1.69 [1.18,2.15] | 6.81 / 7.76 |
| 2024 | −0.05 | 3.18 | 4.42 | 5.25 | +0.83 [.26,1.40] | 2.69 / 3.12 |

State deviations do not persist: `rho` reverses or is near zero. **Do not include hierarchy/state trend.**

## Turnout

Error is `100 × log(predicted / actual county votes)`, vote-weighted RMSE; every method receives the actual statewide
total. This tests distribution, not statewide turnout forecasting.

| feature vs flat prior-election turnout | 2016 Δ [90%] | 2018 | 2020 | 2022 | 2024 | pooled; wins | decision |
|---|---:|---:|---:|---:|---:|---:|---|
| population growth | −0.73 [−.87,−.58] | −0.17 [−.37,−.13] | −1.18 [−1.51,−.88] | +0.19 [−.76,.96] | −1.72 [−1.97,−1.45] | −0.72 [−.95,−.56]; 4/5 | don't include (fails 2022) |
| PEP net migration | −0.56 [−.71,−.42] | −0.00 [−.35,.14] | −0.87 [−1.12,−.64] | +0.15 [−.92,.97] | −1.79 [−2.06,−1.50] | −0.61 [−.88,−.44]; 4/5 | don't include (fails 2022) |
| population + migration | — | −0.11 [−.51,.00] | −1.29 [−1.65,−.95] | +0.14 [−.89,.93] | −1.24 [−1.46,−1.01] | −0.62 [−.95,−.41]; 3/4 | don't include (fails 2022) |

On the existing own-county 2018/2016 drop-off baseline, 2022 RMSE is 10.13; adding population is 9.81, migration
9.70, both 9.80. That is one test only, so it cannot qualify. Keep v4 as a secondary; do not add a fundamental layer.

## House backtest

Construction: prior presidential district lean + the final 45-day generic-ballot average (2018 D2=.539, 74 polls;
2022 D2=.500, 110 polls). 2016 precinct votes are joined to the House district on each ballot (PA excluded after its
map change); 2022 uses 2020 lean on unchanged 2022/2024 lines. County adjustments are aggregated with prior votes and
Census block/population shares. Probabilities use one common share SD (.1417, 2018 plain residual RMSE), so Brier
changes isolate the adjustment. The table covers 406 and 360 seats with a D/R outcome and usable lean/crosswalk.

| adjustment | 2018 Brier plain / model; Δ [90%] | 2022 Brier plain / model; Δ [90%] | decision |
|---|---:|---:|---|
| economic | .0965 / .0964; −.0001 [−.0006,.0004] | .0800 / .0796; −.0003 [−.0011,.0004] | don't include |
| PEP migration | .0965 / .0976; +.0011 [.0004,.0018] | .0800 / .0824; +.0025 [.0012,.0037] | don't include |
| IRS migration | .0965 / .0975; +.0010 [−.0003,.0021] | .0800 / .0812; +.0012 [.0004,.0020] | don't include |
| population growth | .0965 / .1006; +.0041 [.0007,.0075] | .0800 / .0832; +.0033 [.0015,.0049] | don't include |
| all | .0965 / .0995; +.0031 [−.0024,.0093] | .0800 / .0854; +.0054 [.0034,.0073] | don't include |

Calibration (`n, mean p, observed D win rate`) for the plain baseline is in `house_backtest.csv`; its 2022 bins from
0 to 1 are: `80,.11,.01 | 73,.29,.03 | 51,.50,.45 | 56,.70,.96 | 100,.92,.98`. The feature variants do not repair
the clear candidate/incumbency calibration miss in the 0.2–0.4 and 0.6–0.8 bins.

## 2026 market gaps (model-only)

No feature won, so `model` below is the existing display-only 2024 lean on 2026 lines + generic ballot, **not a race
forecast**. Markets contain candidates/incumbency/information this baseline deliberately omits. Price is the mean of
available Polymarket/Kalshi D probabilities; gaps are model minus market. These are research flags, not trades.

| district | model D | market D | gap |
|---|---:|---:|---:|
| TX-15 | .227 | .870 | −.643 |
| TX-28 | .523 | .927 | −.404 |
| PA-01 | .863 | .473 | +.391 |
| AK-AL | .399 | .018 | +.381 |
| OH-07 | .482 | .860 | −.378 |
| OH-10 | .650 | .285 | +.365 |
| TN-08 | .386 | .030 | +.356 |
| NC-01 | .482 | .833 | −.351 |
| WA-05 | .499 | .160 | +.338 |
| IA-01 | .593 | .929 | −.336 |

## Final include / don't-include list

- Economic change × incumbent party: **don't include** (2/5 state tests; pooled estimate slightly worse).
- Inflow origin partisanship and IRS net flows: **don't include** (2/4; significantly worse pooled).
- PEP net domestic migration: **don't include** (3/5; 2022 failure is large).
- Population growth: **don't include for margin** (3/5 and worse pooled); **don't add to turnout** (fails 2022).
- National swing + state effect + county features: **don't include** (0/3, severe failure).
- Own-county prior midterm drop-off: unchanged—v4 secondary only; fundamentals on top have one test, not enough.
- Statewide prior shift / v5: **none**. No GA replay or v3 comparison is warranted because no feature passed OOS.
