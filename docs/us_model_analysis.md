# US midterms 2026: model-choice analysis on past data

This is an analysis only. No model or config changed, and v3 remains the poll model.
Reproduce with `uv run python analysis/us_model_analysis.py`; tables are written to `results/us_model_analysis/`
(gitignored).

## Data

All of it predates 2026:
- **County results:** MIT Election Data and Science Lab precinct returns 2018, 2022 and 2024 (github.com/MEDSL, CC0 1.0), summed to counties by candidate. Also MEDSL County Presidential Election Returns 2000–2024 (doi:10.7910/DVN/VOQCHQ, CC0) for 2016 and 2020, downloaded through the Dataverse guestbook and kept out of the repository.
- **Georgia:** Georgia SoS official county results for 2020 and 2024 (public record).
- **Polls:** FiveThirtyEight raw_polls 2016–2022 (CC BY 4.0).
- **Census:** ACS 2020–2024 5-year, PEP vintage 2025 and the 2023 county gazetteer (public domain).

Alaska is excluded because it reports by house district, not county. Connecticut is excluded because its county units changed in 2022. States where either race was uncontested (statewide two-party share outside 15–85 %) are dropped from tests 1–2.

## 1. Swing rule: uniform vs proportional (logit) vs hybrid

Each test predicts county two-party D shares from the earlier race's county shares plus the actual statewide
result. Errors are in points of two-party share.

| test | counties (states) | RMSE uniform | logit | hybrid | vote-weighted uniform | logit | hybrid |
|---|---|---|---|---|---|---|---|
| president 2016 → 2020 | 3103 (48) | 2.84 | **2.72** | 2.77 | 2.49 | **2.44** | 2.46 |
| president 2020 → 2024 | 3103 (48) | 1.71 | **1.56** | 1.61 | **1.67** | 1.72 | 1.68 |
| president 2016 → top race 2018 (midterm) | 2575 (41) | 3.67 | 3.81 | **3.66** | 2.96 | **2.73** | 2.78 |
| president 2020 → top race 2022 (midterm) | 2335 (35) | 2.26 | 2.24 | **2.22** | 1.73 | **1.67** | 1.69 |
| governor 2018 → 2022 | 1711 (27) | 4.33 | 4.35 | **4.30** | **3.49** | 3.73 | 3.56 |
| top race 2022 → president 2024 | 2336 (35) | 2.58 | 2.55 | **2.55** | **2.10** | 2.17 | 2.13 |
| GA president 2020 → 2024 (official) | 159 (1) | 1.61 | **1.58** | 1.59 | **1.77** | 1.82 | 1.79 |

- **Changed vs the previous run.** With 2016 and 2020 the picture is mixed.
  - Logit wins vote-weighted in both president → midterm tests (2.73 vs 2.96; 1.67 vs 1.73) and in 2016 → 2020 (2.44 vs 2.49).
  - Uniform wins in 2020 → 2024, governor 2018 → 2022 and midterm → president.
  - The hybrid is never the worst and is within 0.06 points vote-weighted of the best in every test.
- **Most Republican decile:** in president → midterm, uniform leaves −1.1 (2022) / +1.0 (2018) points of bias there; logit leaves +0.2 / −1.9.
- **Conclusion: needs more data / candidate for a future version.**
  - The 2026 case is president 2024 → midterm (several priors are 2024 president), where logit has won both times (−0.06 and −0.23 points vote-weighted).
  - Same-office midterm priors favour uniform.
  - The differences are a few hundredths to a quarter of a point at the county level and smaller statewide. Swing also only matters before the count is informative; the nowcast re-estimates it from returns.
  - Analysis only: no model change before the freeze. A hybrid is the safe choice to test next cycle.

## 2. Demographics

The target is each county's swing beyond the uniform statewide swing. It is regressed on standardised Bachelor's+,
age 65+, Hispanic, Black alone, log median income and log density, with weights √votes.

| test | R² (out of sample) | RMSE uniform | RMSE + demographics |
|---|---|---|---|
| president 2016 → 2020, leave one state out | 0.23 | 2.53 | 2.20 |
| president 2020 → 2024, leave one state out | 0.14 | 1.62 | 1.50 |
| president 2016 → midterm 2018, leave one state out | 0.15 | 3.20 | 2.91 |
| president 2020 → midterm 2022, leave one state out | 0.04 | 1.79 | 1.75 |
| governor 2018 → 2022, leave one state out | 0.07 | 3.90 | 3.71 |
| fit pres 16 → 20, test pres 20 → 24 | **−0.26** | 1.62 | 1.82 |
| fit pres 16 → 18 midterm, test pres 20 → 22 midterm | **−0.22** | 1.79 | 1.98 |
| fit pres 20 → 24, test pres 16 → 20 | 0.11 | 2.53 | 2.37 |
| fit governor 18 → 22, test pres 20 → 22 midterm | **−0.84** | 1.79 | 2.42 |

Of the 20 fit-on-one-cycle, test-on-another pairs, 12 have negative R²; the best is 0.25.

- **Changed vs the previous run:** within a cycle, demographics now explain more (leave one state out, up to 0.23 in 2016 → 2020).
- **Across cycles it does not carry over.** The 2018 midterm fit makes 2022 worse (R² −0.22), and the 2016 → 2020 fit makes 2020 → 2024 worse (−0.26).
- **Coefficients still change sign:** Bachelor's+ is +0.43 / +0.09 / −0.63 / +0.08 / +0.94; Black is −0.14 / −0.15 / −1.02 / −0.52 / +0.11.
- **Conclusion: don't include (unchanged).** Demographic swing patterns are real within a cycle, but which ones next cycle brings is not predictable from the last.

## 3. Turnout (midterm drop-off)

- **Changed design:** with 2016 and 2020 we can use the right analogue for 2026. The 2022 midterm vs the 2020 president, and the 2018 midterm vs the 2016 president, using only data from before 2022 to predict it.
- **Demographic model:** log(midterm top-race votes ÷ previous presidential votes) minus its state mean.
  - Leave-one-state-out R² 0.09.
  - Fit on 2018/2016 and tested on 2022/2020: R² **−0.10**.
- **Expected-vote templates** for the 2022 within-state county distribution of votes:

| template | counties (states) | RMSE of log county share | mean abs. error, % of county votes |
|---|---|---|---|
| flat ratio × 2020 president (current approach) | 2527 (39) | 0.112 | 7.2 |
| previous midterm (2018 top race) | 2072 (34) | 0.111 | 7.1 |
| 2020 president × the county's own 2018/2016 drop-off | 2072 (34) | **0.103** | **6.1** |
| 2020 president × demographic drop-off (fit 2018/2016) | 2527 (39) | 0.110 | 7.3 |

- **Changed vs the previous run.** The county's own previous midterm drop-off is the best template out of time: −15 % in county vote error vs the flat ratio, and lower RMSE. The demographic model no longer helps out of time.
- **Conclusion: include in a future version** (2026 = 2024 president × the county's 2022/2020 drop-off); analysis only now.
  - The display index (`data_public/us2026_turnout_index.csv`) now uses each county's own 2022/2020 drop-off relative to its state, falling back to the demographic model where 2022 is missing (575 of 3102 counties). Values are capped at ×0.61–×1.65.
  - The units files' `turnout_exp` and `lowprop_index` follow it.

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

- **Swing rule:** logit wins the president → midterm tests and uniform wins the others, so there is no clear winner. A hybrid is the candidate for a future version.
- **Demographics:** no stable out-of-sample value, so no change.
- **Turnout:** the county's own previous midterm drop-off wins out of time (6.1 % vs 7.2 %). It is the candidate for a future version's expected vote.

The analysis pass wrote no v4; on user decision v4 was then built as a secondary model (next section).

## Model v4 (Ohio v5): secondary model with the county's own midterm drop-off

By user decision, v4 was built as a secondary model with `analysis/us_polls.py next_v4`, which never writes
registered files. The primary stays v3 (Ohio v4). `meta.races[].poll_model` names the primary and
`secondary_models` lists v4; the configs carry `role = "secondary"`.

- **The change:** expected two-party votes per county = 2024 president two-party votes × the county's own 2022
  top-race ÷ 2020 president ratio. The ratio is taken relative to the state's and capped ×0.61–×1.65, with the state's
  flat ratio where a county has no 2022 result (`data_public/us2026_expected_votes_v4.csv`;
  `[count] expected_votes`, level from the file).
- **Swing prior re-centred:** `swing_prior_mean` is re-centred on these weights, which is v2's definition of the
  prior. Before the count v4 therefore also starts at the poll average; its pre-count P is within 0.002 of v3's in
  every race.
- **Swing rule:** unchanged (uniform).

**GA 2022 count replays** (governor and Senate, 40 nights each, early votes first). The 2022 analogue of v4's rule is
2020 president × own 2018/2016 drop-off. Margin error is in points; turnout error is the projected two-party total vs
the final.

| % counted | 90 % coverage v3 / v4 | margin MAE v3 / v4 | margin bias v3 / v4 | turnout error v3 / v4 |
|---|---|---|---|---|
| 1 | 1.00 / 1.00 | 1.19 / 1.31 | +1.1 / +1.3 | +1.5 % / +21.5 % |
| 5 | 1.00 / 1.00 | 1.98 / 2.12 | +2.0 / +2.1 | +1.5 % / +21.5 % |
| 10 | 1.00 / 1.00 | 2.44 / 2.53 | +2.4 / +2.5 | +1.5 % / +21.5 % |
| 25 | 1.00 / 1.00 | 3.47 / 3.51 | +3.5 / +3.5 | +1.5 % / +21.5 % |
| 50 | 1.00 / 1.00 | 3.59 / 3.24 | +3.6 / +3.2 | +1.5 % / +21.5 % |
| 75 | 1.00 / 1.00 | 0.23 / 1.10 | +0.1 / −1.1 | +1.9 % / +18.0 % |

- **Turnout level:** the level variant shown is the one in the configs. Georgia's 2018 midterm turnout was unusually high, so the 2018/2016 ratio overstates 2022's level by 21.5 %.
- **Distribution only:** taking only the county distribution and keeping v3's statewide total scored worse on margin: MAE 3.96 at 50 %, coverage 0.95 (`results/us_model_analysis/v4_validation.csv`).
- **Before the count:** v4 is identical to v3 in the 2018/2022 pre-count backtest, because the statewide pre-count prior is the poll average either way.
- **Conclusion:** on this replay v4 is no better than v3; it is slightly worse early and late and better at 50 %. Its expected 2026 level comes from the 2022/2020 ratio, which is the main risk. It is kept as the secondary model.

## New display fields from the turnout model (no model change)

- **Data:** `data_public/us2026_turnout_index.csv` holds, per county:
  - `adj`: relative midterm turnout, from the county's own 2022/2020 drop-off relative to its state, else the demographic model; capped at ×0.61–×1.65;
  - `lowprop_index`: standardised drop-off; higher means a larger drop-off;
  - `basis`: which of the two `adj` came from.
- **Units files** of modeled races:
  - top level: `turnout_exp` (the model's expected two-party votes spread by `adj`, statewide total unchanged) and `lowprop_index`;
  - each frame: `turnout_so_far` and `turnout_ratio_live` (×1000: counted ÷ expected where ≥ 95 % reported, else projected ÷ expected).
- **Timeline rows:** `turnout_exp_total`, `turnout_proj_total`, `turnout_vs_expected` (%), `counties_reported`.
  - Across counties ≥ 95 % reported: `turnout_surprise_corr` and `turnout_surprise_slope` (log counted ÷ expected vs `lowprop_index`).
  - `margin_hot` and `margin_cold`: the D−R margin of counties above and below expected turnout.
