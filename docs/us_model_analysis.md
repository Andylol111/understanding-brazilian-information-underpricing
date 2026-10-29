# US midterms 2026: model-choice analysis on past data

This is an analysis only. No model or config changed, and v3 remains the poll model.
Reproduce with `uv run python analysis/us_model_analysis.py`; tables are written to `results/us_model_analysis/`
(gitignored).

## Data

All of it predates 2026:
- **County results:** MIT Election Data and Science Lab precinct returns 2018, 2022 and 2024 (github.com/MEDSL, CC0 1.0), summed to counties by candidate. The 2016 and 2020 county files sit behind a Dataverse guestbook and were not used.
- **Georgia:** Georgia SoS official county results for 2020 and 2024 (public record).
- **Polls:** FiveThirtyEight raw_polls 2016–2022 (CC BY 4.0).
- **Census:** ACS 2020–2024 5-year, PEP vintage 2025 and the 2023 county gazetteer (public domain).

Alaska is excluded because it reports by house district, not county. Connecticut is excluded because its county units changed in 2022. States where either race was uncontested (statewide two-party share outside 15–85 %) are dropped from tests 1–2.

## 1. Swing rule: uniform vs proportional (logit) vs hybrid

Each test predicts county two-party D shares from the earlier race's county shares plus the actual statewide
result.

| test | counties (states) | RMSE uniform | logit | hybrid | vote-weighted RMSE uniform | logit | hybrid |
|---|---|---|---|---|---|---|---|
| governor 2018 → 2022 | 1711 (27) | 4.33 | 4.35 | **4.30** | **3.49** | 3.73 | 3.57 |
| top race 2022 → president 2024 | 2336 (35) | 2.59 | 2.55 | **2.55** | **2.10** | 2.17 | 2.13 |
| GA president 2020 → 2024 (official) | 159 (1) | 1.61 | **1.58** | 1.59 | **1.77** | 1.82 | 1.79 |

Errors are in points of two-party share.

- **Bias by partisanship decile:** for governor 2018 → 2022, uniform swing's bias is flatter across deciles than logit's in the most Republican deciles (d1: +1.5 vs +1.7; d2: +1.2 vs +2.1).
- **Logit's advantage:** where logit wins, it is by 0.03 points unweighted. It loses on the vote-weighted measure everywhere, and that is the measure that matters for the statewide margin.
- **Conclusion: don't change (keep uniform swing).**

## 2. Demographics

The target is each county's swing beyond the uniform statewide swing. It is regressed on standardised Bachelor's+,
age 65+, Hispanic, Black alone, log median income and log density, with weights √votes.

| test | R² (out of sample) | RMSE uniform | RMSE + demographics |
|---|---|---|---|
| governor 2018 → 2022, leave one state out | 0.07 | 3.90 | 3.71 |
| top 2022 → president 2024, leave one state out | 0.03 | 2.17 | 2.14 |
| fit 2018 → 2022, test 2022 → 2024 | **−0.32** | 2.17 | 2.50 |
| fit 2022 → 2024, test 2018 → 2022 | 0.03 | 3.90 | 3.80 |

- **Coefficients are not stable across cycles.** Bachelor's+ is +0.94 then +0.01; Black +0.10 then +0.34; log density +0.08 then −0.46; log income +0.38 then +0.76.
- **Within-cycle gains don't carry over.** The small leave-one-state-out gains disappear across cycles, and the 2018 → 2022 fit makes the next cycle worse.
- **Conclusion: don't include.** Demographics add no reliable predictive value beyond uniform swing.

## 3. Turnout (midterm drop-off)

- **Model:** log(2022 top-race votes ÷ 2024 president votes) minus its state mean, regressed on the same covariates.
- **Fit:** leave-one-state-out R² = **0.05**.
- **Expected-vote templates:** each predicts the 2022 within-state county distribution of votes. The 2026 analogue uses 2024 as the presidential template; here 2024 comes after 2022, which is a caveat.

| template | counties (states) | RMSE of log county share | mean abs. error, % of county votes |
|---|---|---|---|
| flat ratio × 2024 president (current) | 2527 (39) | 0.118 | 7.2 |
| previous midterm (2018 top race) | 2072 (34) | 0.111 | 7.1 |
| 2024 president × demographic ratio (out of state) | 2527 (39) | 0.118 | 6.7 |

- **Result:** the demographic ratio cuts the mean absolute error from 7.2 % to 6.7 %. The RMSE is unchanged, and the effect is small.
- **Conclusion: don't change the model's expected vote.** The turnout model is used **for display only**: per-county expected turnout, a low-propensity index, and a live turnout-surprise readout (see below).

## 4. Correlated polling error

Our averaging rule was applied to 538's 2016–2022 Senate and governor races (184 races in four even-year cycles).

- **Mean error by cycle (two-party share):** 2016 +1.9, 2018 −0.1, 2020 +3.5, 2022 +0.7 points.
- **Common component:** sd 1.5 points (about 3 points of margin). The race-specific sd is 2.9 points, so the cross-race correlation ρ = **0.21**. This is estimated from 4 cycles only.
- **2026 Senate control** (D caucus needs 51 with the Republican VP; 34 D and I holdovers):
  - Inputs: poll average and v3 sd where polled, else the Polymarket price.
  - Independent races: P(control) = **0.88**, seats 80 % interval 50–55.
  - Correlated (ρ = 0.21): **0.75**, interval 49–56.
- **Conclusion: include, for joint quantities only.** Senate control, and any multi-race figure, should use the common component. Per-race probabilities are unaffected. This is a display or analysis change, not a change to the registered models.
- **Now on the overview:** `overview.json` `offices.senate` shows `control_p_corr`, `control_p_indep`, `seats_80_corr`, `seats_80_indep` and `control_note`. `brunder.us.senate_control` (ρ = `SENATE_RHO` = 0.21) recomputes them on every pre-count run and every live overview refresh.

## 5. Drift during the count (GA 2022 governor and Senate, 40 simulated nights each, early votes first)

Bias is the model's margin minus the final margin, in points (sd across nights in brackets).

| % counted | v1 | v2 | v3 | raw count |
|---|---|---|---|---|
| 1 | +4.6 (2.3) | +2.4 (1.4) | +1.1 (0.8) | −30.5 |
| 5 | +6.3 (1.8) | +3.6 (1.1) | +2.0 (0.7) | −19.7 |
| 10 | +6.9 (1.4) | +4.1 (1.0) | +2.4 (0.7) | −2.3 |
| 25 | +7.0 (0.9) | +4.9 (0.7) | +3.5 (0.6) | +4.8 |
| 50 | +5.8 (1.0) | +4.6 (0.9) | +3.6 (0.9) | +7.4 |
| 75 | +0.6 (0.3) | +0.3 (0.3) | +0.1 (0.3) | +5.1 |

- **Bias direction:** all versions lean toward the Democrat while early votes dominate. v3 has the smallest bias throughout.
- **Most fragile window:** 25–50 % counted, while Election Day precincts arrive and the bias peaks before collapsing. Its sd is small, so the bias, not noise, is the risk.
- **Coverage:** the replays from the v3 check covered the final result at 90 % at every checkpoint.
- **Conclusion: needs more data.** GA is one state and two races. Nothing to change before the freeze; watch the 25–50 % window on the night.

## 6. Early vote as context (NC)

The raw early-vote margin by party registration, 7 days before Election Day, was +7.5 in 2022 against a final of
−3.3, a bias of **+10.8** points. In 2024 it was −1.3 against −3.3, a bias of **+2.0**. The bias varies too much
between elections to correct reliably.

**Conclusion: context only** (as now in `earlyvote.json`).

## Decision on model v4

- **Swing rule:** uniform still wins on vote-weighted error, so no change.
- **Demographics:** no stable out-of-sample value, so no change.
- **Turnout:** the turnout model only marginally beats the flat ratio on one metric and ties on the other, so no change.

Nothing won clearly on held-out data, so **no v4 was written**; v3 stays.

## New display fields from the turnout model (no model change)

- **Data:** `data_public/us2026_turnout_index.csv` holds, per county, `adj` (predicted relative midterm turnout) and `lowprop_index` (standardised predicted drop-off; higher means a larger drop-off).
- **Units files** of modeled races:
  - top level: `turnout_exp` (the model's expected two-party votes spread by `adj`, statewide total unchanged) and `lowprop_index`;
  - each frame: `turnout_so_far` and `turnout_ratio_live` (×1000: counted ÷ expected where ≥ 95 % reported, else projected ÷ expected).
- **Timeline rows:** `turnout_exp_total`, `turnout_proj_total`, `turnout_vs_expected` (%), `counties_reported`.
  - Across counties ≥ 95 % reported: `turnout_surprise_corr` and `turnout_surprise_slope` (log counted ÷ expected vs `lowprop_index`).
  - `margin_hot` and `margin_cold`: the D−R margin of counties above and below expected turnout.
