# Hispanic voters are not one group: origin-group estimates by place and cycle

Estimates for places, not counts of how people voted. Nobody's ballot is observed: the numbers come from a regression of tract and county
results on who lives there, so each value is an estimate with the error it carries. Cycles: 2016, 2020, 2022, 2024.

## What was built

| Piece | File |
|---|---|
| County table: dominant Hispanic origin among citizen adults, its share, Hispanic share of CVAP (2024 ACS; every vintage in `_all`) | `$BRUNDER_DATA/processed/us2026/origin/county_origin_groups.csv`, `county_origin_groups_all.csv` |
| Estimates: origin x region x cycle, 90 % interval, citizen adults | `data_public/us_hispanic_origin_estimates.csv` (aggregates only) |
| Validation tables: survey cells, published polls, leave-one-cycle-out scores | `data_public/us_hispanic_origin_validation.csv`, `..._loco.csv`, `..._loco_county2022.csv`, `..._polls.csv` |
| Page data (Hispanic groups, selected regions, validation summary) | `site/data/us2026_context/origin.json` (nothing reads it yet; `meta.json` is untouched) |
| Code | `analysis/us_origin_{data,pums,groups,frames,fit,ces,report,export}.py`, estimator `src/brunder/origin_ei.py`, tests `tests/test_origin_ei.py`, `tests/test_origin_pipeline.py` |

## Data

* **Origin and citizenship.** ACS 5-year **B03001** (Hispanic origin by specific origin: Mexican, Puerto Rican, Cuban, Dominican, Central American
  by country, South American by country, Other), **B05003I** (Hispanic citizens of voting age), **B05003** (all citizens of voting age) and **B03002**
  (non-Hispanic race), at county and tract level for every state, vintages ending 2016, 2020, 2022 and 2024. The Census API now sends every request
  to a key page, so the keyless bulk summary files under `www2.census.gov/programs-surveys/acs/summary_file` are used (public domain, one request at a
  time, raw files under `raw/us/census/b03001/`). The ACS publishes no origin x citizenship table, so a group's citizen adults are its B03001
  population times a state x origin ratio (citizen adults per person) from the ACS 5-year **PUMS**, shrunk toward the national ratio, and the seven
  origins are then rescaled so they add to the published B05003I count of the tract. Totals are the published ones; only the split between origins is
  estimated. Non-Hispanic white, Black, Asian and other citizen adults are built the same way from B03002 and B05003. County file 2024: Mexican is the
  dominant origin in 2,457 of 3,144 counties, Puerto Rican in 358, Other Hispanic 185, Central American 58, South American 36, Cuban 21, Dominican 12.
* **Votes by tract.** Official precinct results already spread to census tracts by Fekrazad (Harvard Dataverse, doi:10.7910/DVN/Z8TSH3, CC0):
  2016 precinct data from VEST, 2020 from MEDSL, 2024 from the New York Times precinct map, each spread in proportion to household population
  (2016 and 2020: the RLCR main method; 2024: the land-cover method). Contiguous states only. County sums match the official county results
  (D and R totals within 0.2 % in all three years; a county's two-party share from its tracts is within 0.005 of the official one in 99.6 to 99.9 % of the 3,095 to 3,100 counties compared,
  worst gap 0.13 to 0.16 in one county per year). The tract numbers are allocated, not counted: a precinct that straddles tracts gives every tract the same party share, which smooths real
  differences between neighbouring tracts.
* **Geography of vintages.** Tract boundaries change in 2020, so the ACS vintage must share the votes' tracts: 2016 votes with ACS 2012-16, 2020 votes with
  ACS 2018-22, 2024 votes with ACS 2020-24. Tracts kept: 71,613 (2016), 81,706 (2020), 82,246 (2024): at least 200 citizen adults, some two-party votes, and
  votes at most 1.3 x citizen adults.
* **2022 has no tract-level precinct file with a tract join**, so 2022 uses the official **county** results (`unified/results_county`): governor where it
  validated in at least 90 % of the state's counties, else Senate, else the House total; 2,994 counties, with group shares from ACS 2018-22.
* **Surveys used for validation, never as input to the estimates:** Cooperative Election Study 2016, 2020, 2024 (Harvard Dataverse, CC0; validated voters,
  weight `vvweight_post`, origin from the check-all "Latin heritage" question), and the published polls in `data_public/us_hispanic_origin_polls.csv`.

## Model

Unit: tract (2016, 2020, 2024) or county (2022). For each party, votes per citizen adult of the unit are regressed on the unit's group shares of citizen
adults (11 groups: seven Hispanic origins, non-Hispanic white, Black, Asian, other), no intercept, weights = citizen adults (capped). The coefficient of
group k is its votes per group member, so for the Democratic and Republican regressions together

* preference of group k = beta_D / (beta_D + beta_R), its two-party Democratic share;
* turnout of group k = beta_D + beta_R (of citizen adults).

This is the Goodman double regression: unequal turnout between groups stays out of the preference estimate. It is made hierarchical and bounded
(Gibbs sampler, `src/brunder/origin_ei.py`):

1. coefficients are in [0, 1] and beta_D + beta_R <= 1;
2. **region effects:** an area x group coefficient is drawn around its state's value, which is drawn around the national value; the spreads are learned.
   Areas are 86 pooling units: Miami-Dade, Broward, Palm Beach, Orlando (Orange/Osceola), Tampa Bay, Harris, Bexar, Rio Grande Valley, El Paso, Laredo,
   Dallas/Tarrant, Travis, Maricopa, Pima, Clark, New York City, Long Island, northern New Jersey, Middlesex/Monmouth, Los Angeles, San Diego, Orange CA,
   Inland Empire, Central Valley, Bay Area, Cook, Philadelphia, Lehigh Valley/Berks, Bernalillo, Denver, Atlanta core, Mecklenburg/Wake, Boston area,
   Fairfax/Prince William, DC, Providence, Connecticut urban counties, and "the rest of" each state;
3. **turnout anchor:** group turnout has a prior from the CPS turnout ratios of white, Black, Asian and Hispanic citizens to all citizens (Census P20
   reports: 2016 47.6/65.3/59.4/49.0 against 61.4 overall, 2020 53.7/71.0/63.0/59.0 against 66.8, 2024 50.6/70.5/59.6/57.1 against 65.3) times the area's
   own turnout, sd 0.08-0.12. Results hardly move without it (sensitivity below), but it stops collinear groups from taking turnouts above 100 %;
4. **two stages:** a model with one Hispanic group is fitted first; the seven origins are then fitted with their prior centred on their area's one-group
   value (sd 0.09 votes per citizen adult) and the non-Hispanic groups held near their stage-1 values (sd 0.03). Origins therefore move apart only
   where the data insist;
5. **2022 (county data):** the 2020 and 2024 coefficients, scaled by the area's 2022 turnout relative to their turnout, are the prior (sd 0.07); county results update
   it, mostly through the area totals. An area with one county (Miami-Dade) is one data point; its origin split is carried forward, not measured.

Intervals are 90 % posterior intervals widened by the model error measured against the survey (below).

Tested on synthetic tracts with known group preferences (`tests/test_origin_ei.py`): the sampler recovers preferences and turnout (mean error < 0.04, 90 %
intervals cover 81 to 93 % of the true values across eight seeds, 87 % on average), intervals widen with noise, pooling steadies an area whose two groups are collinear, a turnout prior
fixes the collinear split, a carried-forward prior comes back where an area has no data, and the sufficient statistics equal direct regression.

## Level and uncertainty: what the validation says

**Levels are the weak part; differences between origins are the stronger part.**

CES validated-voter cells (national, and states with at least 60 validated Hispanic voters; origin cells need at least 60 too; 54 cells scored, plus the three national Hispanic cells the shift is set on):

| Cycle | cells | raw bias (model minus survey) | raw rmse | after one national shift: bias | rmse | shift (logit) |
|---|---:|---:|---:|---:|---:|---:|
| 2016 | 16 | +0.159 | 0.171 | +0.008 | 0.080 | -1.010 |
| 2020 | 19 | +0.054 | 0.096 | -0.009 | 0.089 | -0.331 |
| 2024 | 19 | +0.028 | 0.131 | -0.001 | 0.130 | -0.133 |

The raw ecological estimate of Hispanic support for Democrats is too high: national Hispanic two-party Democratic share 0.856 / 0.733 / 0.609 against
0.684 / 0.664 / 0.577 in CES (2016 / 2020 / 2024; Pew validated voters 0.629 in 2020 and 0.515 in 2024; UnidosUS/BSP 0.626 in 2024). The usual reason is that
a regression on tract shares mixes how a group votes with where it lives (Hispanic voters in heavily Hispanic tracts vote more Democratic than Hispanic voters
elsewhere, and the regression weighs those tracts most). Adding group-concentration terms (`ctx` variant) overshoots the other way (bias -0.125 in 2020, -0.168 in 2024,
rmse 0.19 and 0.23) and was dropped. The bias is not uniform across regions: raw Hispanic estimates are high in Arizona, Illinois and Pennsylvania (+0.13 to +0.20) and low in Florida
(-0.04 in 2020, -0.07 in 2024); New York Puerto Ricans come out 0.18 (2020) and 0.27 (2024) below the survey.

* `dem_two_party` in the CSV is the raw ecological estimate and its posterior interval.
* `dem_two_party_aligned` (the figure in the tables below and in `origin.json`) moves every Hispanic-origin group of a cycle by the same logit shift so the national Hispanic
  value equals the CES value, and widens the interval by sigma_m = **0.085** (model error in probability: pooled 2020 and 2024 CES cells after subtracting survey error)
  plus 0.02 for the survey's own error in the shift. Tuned on 2020 cells only (sigma_m 0.063), the 90 % interval covered 95 % of the 2024 cells.
  Aligning to CES is a choice: the surveys themselves disagree (2024 Hispanic: Pew 0.515, CES 0.577, UnidosUS 0.626), and the Florida cells show the aligned figure is too low
  there (below). Anyone who prefers no survey input can use the raw columns and should add about +/-0.15.
* 2016 raw levels are not usable (+0.16); aligned 2016 is supported by CES but nothing independent.

**Differences between origins** (group minus Mexican, national, ecological estimate against CES, with the survey's standard error):

| Cycle | Cuban: model / CES | Puerto Rican: model / CES | South American: model / CES | Central American: model / CES |
|---|---|---|---|---|
| 2016 | -0.28 / -0.23 (0.10) | +0.04 / -0.03 (0.05) | +0.01 / -0.08 (0.11) | +0.05 / +0.08 (0.11) |
| 2020 | -0.25 / -0.23 (0.06) | +0.03 / +0.00 (0.04) | +0.03 / +0.06 (0.05) | +0.12 / +0.03 (0.08) |
| 2024 | -0.21 / -0.13 (0.08) | +0.04 / +0.11 (0.05) | +0.04 / -0.05 (0.07) | +0.14 / -0.01 (0.11) |

Cubans lean Republican relative to Mexicans in every cycle, by 0.21 to 0.28 in the model and 0.13 to 0.23 in CES; every one of the twelve gaps is within 1.4 survey standard errors of the survey value.
The model puts Central Americans 0.05 to 0.14 above Mexicans in every cycle; CES is at +0.08, +0.03 and -0.01. Dominican cells rest on 38-46 CES respondents and are not scored.

**Published polls with an origin split** (aligned model against poll; poll converted to Democratic / (Democratic + Republican), or the one share reported; `us_hispanic_origin_validation.csv`):

| Poll | Universe | Poll | Model, raw | Model, aligned (90 %) | Inside |
|---|---|---:|---:|---|---|
| 2020 Hispanic, US, Pew validated voters (61 / 36) | voters | 0.629 | 0.733 | 0.664 (0.52-0.81) | yes |
| 2024 Hispanic, US, Pew validated voters (51 / 48) | voters | 0.515 | 0.609 | 0.577 (0.43-0.72) | yes |
| 2024 Hispanic, US, UnidosUS/BSP (62 / 37) | voters | 0.626 | 0.609 | 0.577 (0.43-0.72) | yes |
| 2024 Puerto Rican, US, UnidosUS/BSP (Harris 65) | voters | 0.650 | 0.642 | 0.610 (0.47-0.76) | yes |
| 2024 Mexican, US, UnidosUS/BSP (Harris 63) | voters | 0.630 | 0.602 | 0.569 (0.43-0.71) | yes |
| 2024 Cuban, US, UnidosUS/BSP (Trump 54) | voters | 0.460 | 0.389 | 0.358 (0.21-0.50) | yes |
| 2024 Cuban, Miami-Dade, FIU Cuba Poll (Harris 23 / Trump 68) | likely voters, pre-election | 0.253 | 0.216 | 0.194 (0.05-0.34) | yes |
| 2020 Cuban, Florida, AP VoteCast (Trump 52) | voters | 0.480 | 0.377 | 0.303 (0.16-0.45) | **no** |
| 2020 Cuban, Florida, Latino Decisions (Trump 55) | voters | 0.450 | 0.377 | 0.303 (0.16-0.45) | **no** |
| 2020 Puerto Rican, Florida, Telemundo/USA Today (Biden 66 / Trump 23) | registered, pre-election | 0.742 | 0.662 | 0.585 (0.43-0.74) | **no** |

Seven of ten polls fall inside the interval; the three misses are all Florida 2020, where the model is 0.07-0.10 (raw) and 0.15-0.18 (aligned) below the polls. CES is lower for those Florida cells
than the polls it is compared with (Cuban 0.339 +/- 0.078, Puerto Rican 0.657 +/- 0.074), so the survey evidence itself is mixed. The pre-election polls are not the vote. The Florida
Hispanic aligned figure is the cell most likely to be too low; the raw figure is closer there.

**Leave one cycle out.** The coefficients of the other two cycles (posterior means, area x group) predict each tract of the held-out cycle from its group shares; the area's total is matched
to its actual result (so the score is placement across tracts, not the swing). Skill = 1 - MSE / MSE of "every tract equals its area's result", tracts weighted by votes:

| Held out | all tracts: one Hispanic group | all tracts: seven origins | Hispanic >= 25 % tracts: one / seven | Florida: one / seven | Texas: one / seven | NY + NJ: one / seven |
|---|---|---|---|---|---|---|
| 2016 | 0.546 | 0.548 | 0.569 / 0.567 | 0.593 / 0.611 | 0.676 / 0.677 | 0.486 / 0.485 |
| 2020 | 0.507 | 0.511 | 0.542 / 0.560 | 0.586 / 0.611 | 0.598 / 0.604 | 0.446 / 0.451 |
| 2024 | 0.430 | 0.437 | 0.243 / 0.288 | 0.489 / 0.513 | 0.491 / 0.500 | 0.273 / 0.286 |

Group composition explains 43-55 % of the within-area tract variance in a cycle it never saw. Splitting Hispanic voters by origin adds a little everywhere and most where origins differ:
Florida (+0.02 of skill in each cycle) and Hispanic-heavy tracts (+0.02 in 2020, +0.045 in 2024, nothing in 2016); in Texas and New York / New Jersey it adds less than 0.015.
County results of 2022 held out the same way (2,994 counties): skill 0.490 with one Hispanic group, 0.497 with seven origins; in counties with a quarter or more Hispanic citizens 0.217 against 0.271;
in Florida 0.355 against 0.393. A county's own 2020 result predicts 2022 far better (skill 0.97); composition is not a forecast model, and this test only asks whether the origin split helps.

**Sensitivity (2020 and 2024 fits, CES rmse / national values):** prior sd of origins around the Hispanic value 0.05 / 0.09 / 0.15: rmse 2024 0.122 / 0.128 / 0.135; Mexican 2024 0.611 / 0.602 / 0.597;
Cuban in Florida 2024 0.290 / 0.265 / 0.248. Turnout prior sd x0.5, x1, x2, none: 2024 rmse 0.125 / 0.128 / 0.130 / 0.132; national Hispanic 2024 0.609 / 0.609 / 0.608 / 0.608.

## Estimates

Democratic share of the two-party vote, aligned figure with 90 % interval (including model error); CVAP is citizen voting-age population of the group in 2024; "-" means fewer than 25,000 group citizen adults
(the estimate would be mostly prior). Contiguous states. 2022 is the county-level fit, carried forward from 2020 and 2024 and updated by county results.

| Region | Group | CVAP 2024 | 2016 | 2020 | 2022 | 2024 |
|---|---|---:|---|---|---|---|
| United States (contiguous) | Hispanic (all origins) | 33,484k | 0.68 (0.54-0.83) | 0.66 (0.52-0.81) | 0.58 (0.43-0.73) | 0.58 (0.43-0.72) |
|  | Mexican | 18,935k | 0.71 (0.56-0.85) | 0.66 (0.52-0.81) | 0.57 (0.42-0.73) | 0.57 (0.43-0.71) |
|  | Puerto Rican | 4,222k | 0.79 (0.64-0.93) | 0.70 (0.55-0.84) | 0.61 (0.45-0.76) | 0.61 (0.46-0.76) |
|  | Cuban | 1,615k | 0.34 (0.20-0.49) | 0.40 (0.26-0.55) | 0.38 (0.23-0.54) | 0.36 (0.21-0.50) |
|  | Dominican | 1,243k | 0.82 (0.67-0.97) | 0.75 (0.60-0.89) | 0.61 (0.45-0.77) | 0.56 (0.41-0.71) |
|  | Central American | 2,295k | 0.81 (0.66-0.95) | 0.80 (0.66-0.95) | 0.68 (0.53-0.84) | 0.71 (0.57-0.86) |
|  | South American | 2,481k | 0.72 (0.57-0.87) | 0.70 (0.56-0.85) | 0.61 (0.46-0.76) | 0.61 (0.47-0.76) |
|  | Other Hispanic | 2,694k | 0.57 (0.42-0.72) | 0.65 (0.51-0.80) | 0.60 (0.45-0.75) | 0.62 (0.47-0.76) |
| Florida | Hispanic (all origins) | 3,537k | 0.48 (0.34-0.63) | 0.46 (0.32-0.61) | 0.44 (0.29-0.59) | 0.39 (0.24-0.53) |
|  | Mexican | 323k | 0.37 (0.18-0.55) | 0.30 (0.14-0.46) | 0.29 (0.11-0.48) | 0.17 (0.02-0.33) |
|  | Puerto Rican | 926k | 0.78 (0.62-0.94) | 0.58 (0.43-0.74) | 0.56 (0.37-0.74) | 0.49 (0.34-0.64) |
|  | Cuban | 1,020k | 0.27 (0.12-0.41) | 0.30 (0.16-0.45) | 0.28 (0.10-0.45) | 0.24 (0.09-0.39) |
|  | Dominican | 179k | 0.61 (0.43-0.79) | 0.55 (0.39-0.72) | 0.51 (0.34-0.68) | 0.46 (0.30-0.62) |
|  | Central American | 261k | 0.77 (0.60-0.94) | 0.64 (0.47-0.80) | 0.55 (0.37-0.74) | 0.50 (0.34-0.66) |
|  | South American | 636k | 0.64 (0.49-0.80) | 0.57 (0.42-0.72) | 0.52 (0.36-0.69) | 0.49 (0.34-0.64) |
|  | Other Hispanic | 192k | 0.50 (0.32-0.67) | 0.52 (0.36-0.68) | 0.51 (0.34-0.68) | 0.49 (0.32-0.65) |
| South Florida (Miami-Dade, Broward, Palm Beach) | Hispanic (all origins) | 1,658k | 0.37 (0.23-0.52) | 0.37 (0.23-0.52) | 0.34 (0.19-0.50) | 0.32 (0.17-0.46) |
|  | Mexican | 64k | 0.40 (0.20-0.60) | 0.43 (0.25-0.60) | 0.37 (0.17-0.57) | 0.29 (0.12-0.46) |
|  | Puerto Rican | 170k | 0.61 (0.40-0.82) | 0.52 (0.35-0.69) | 0.46 (0.27-0.65) | 0.44 (0.27-0.60) |
|  | Cuban | 733k | 0.21 (0.06-0.35) | 0.24 (0.10-0.39) | 0.23 (0.04-0.42) | 0.21 (0.07-0.36) |
|  | Dominican | 76k | 0.58 (0.37-0.80) | 0.52 (0.35-0.70) | 0.48 (0.27-0.69) | 0.43 (0.25-0.61) |
|  | Central American | 154k | 0.83 (0.64-1.00) | 0.65 (0.47-0.83) | 0.55 (0.32-0.78) | 0.51 (0.34-0.69) |
|  | South American | 385k | 0.60 (0.44-0.76) | 0.48 (0.33-0.63) | 0.43 (0.24-0.62) | 0.40 (0.25-0.55) |
|  | Other Hispanic | 74k | 0.41 (0.22-0.59) | 0.40 (0.23-0.57) | 0.40 (0.20-0.60) | 0.37 (0.20-0.55) |
| Miami-Dade | Hispanic (all origins) | 1,088k | 0.32 (0.17-0.46) | 0.32 (0.17-0.46) | 0.28 (0.12-0.45) | 0.27 (0.12-0.41) |
|  | Mexican | 25k | - | 0.30 (0.09-0.51) | 0.27 (0.01-0.53) | 0.19 (0.00-0.39) |
|  | Puerto Rican | 63k | 0.57 (0.27-0.87) | 0.43 (0.24-0.62) | 0.37 (0.10-0.64) | 0.34 (0.15-0.54) |
|  | Cuban | 610k | 0.19 (0.05-0.34) | 0.22 (0.08-0.37) | 0.21 (0.00-0.41) | 0.19 (0.05-0.34) |
|  | Dominican | 39k | 0.56 (0.28-0.84) | 0.51 (0.29-0.72) | 0.47 (0.17-0.76) | 0.39 (0.17-0.62) |
|  | Central American | 99k | 0.91 (0.71-1.00) | 0.66 (0.46-0.87) | 0.57 (0.26-0.88) | 0.56 (0.35-0.77) |
|  | South American | 209k | 0.56 (0.39-0.73) | 0.44 (0.29-0.59) | 0.38 (0.15-0.61) | 0.36 (0.21-0.50) |
|  | Other Hispanic | 42k | - | 0.35 (0.16-0.53) | 0.35 (0.10-0.60) | 0.34 (0.15-0.53) |
| Broward | Hispanic (all origins) | 377k | 0.56 (0.41-0.71) | 0.52 (0.37-0.67) | 0.48 (0.32-0.65) | 0.46 (0.31-0.60) |
|  | Puerto Rican | 70k | 0.71 (0.43-0.98) | 0.65 (0.43-0.87) | 0.60 (0.34-0.87) | 0.57 (0.37-0.77) |
|  | Cuban | 83k | 0.32 (0.15-0.49) | 0.39 (0.23-0.55) | 0.38 (0.15-0.61) | 0.35 (0.17-0.52) |
|  | Dominican | 27k | - | - | - | 0.50 (0.27-0.73) |
|  | Central American | 32k | - | 0.66 (0.42-0.90) | 0.55 (0.26-0.84) | 0.47 (0.27-0.68) |
|  | South American | 130k | 0.66 (0.46-0.86) | 0.52 (0.36-0.68) | 0.49 (0.27-0.71) | 0.45 (0.30-0.61) |
| Orlando (Orange/Osceola) | Hispanic (all origins) | 438k | 0.79 (0.63-0.96) | 0.61 (0.46-0.75) | 0.50 (0.30-0.70) | 0.48 (0.34-0.63) |
|  | Puerto Rican | 228k | 0.86 (0.67-1.00) | 0.58 (0.41-0.74) | 0.46 (0.16-0.76) | 0.46 (0.29-0.63) |
|  | Cuban | 35k | - | 0.69 (0.43-0.94) | 0.60 (0.29-0.91) | 0.57 (0.29-0.86) |
|  | Dominican | 35k | - | 0.58 (0.34-0.82) | 0.53 (0.22-0.83) | 0.50 (0.24-0.76) |
|  | South American | 81k | 0.78 (0.52-1.00) | 0.71 (0.50-0.91) | 0.61 (0.31-0.91) | 0.53 (0.33-0.74) |
| Florida I-4 (Orlando, Tampa Bay) | Hispanic (all origins) | 769k | 0.70 (0.54-0.85) | 0.55 (0.41-0.70) | 0.46 (0.29-0.63) | 0.43 (0.28-0.58) |
|  | Mexican | 67k | 0.36 (0.15-0.57) | 0.30 (0.10-0.49) | 0.28 (0.06-0.50) | 0.19 (0.01-0.37) |
|  | Puerto Rican | 338k | 0.83 (0.64-1.00) | 0.56 (0.40-0.72) | 0.45 (0.19-0.71) | 0.45 (0.28-0.61) |
|  | Cuban | 112k | 0.65 (0.43-0.86) | 0.53 (0.36-0.70) | 0.46 (0.23-0.68) | 0.41 (0.24-0.58) |
|  | Dominican | 53k | 0.66 (0.42-0.90) | 0.55 (0.35-0.75) | 0.50 (0.26-0.73) | 0.45 (0.24-0.65) |
|  | Central American | 35k | - | 0.52 (0.31-0.74) | 0.46 (0.23-0.68) | 0.40 (0.19-0.62) |
|  | South American | 122k | 0.71 (0.50-0.93) | 0.67 (0.48-0.85) | 0.59 (0.36-0.82) | 0.52 (0.34-0.71) |
|  | Other Hispanic | 42k | - | 0.54 (0.33-0.75) | 0.48 (0.25-0.70) | 0.42 (0.22-0.63) |
| Texas | Hispanic (all origins) | 6,285k | 0.70 (0.55-0.84) | 0.63 (0.49-0.78) | 0.58 (0.43-0.73) | 0.57 (0.43-0.72) |
|  | Mexican | 5,194k | 0.72 (0.57-0.86) | 0.62 (0.48-0.77) | 0.57 (0.41-0.73) | 0.56 (0.41-0.70) |
|  | Puerto Rican | 190k | 0.63 (0.43-0.82) | 0.72 (0.55-0.89) | 0.68 (0.49-0.86) | 0.71 (0.53-0.88) |
|  | Cuban | 62k | 0.54 (0.33-0.75) | 0.61 (0.42-0.80) | 0.57 (0.37-0.76) | 0.55 (0.35-0.75) |
|  | Central American | 215k | 0.67 (0.45-0.90) | 0.71 (0.53-0.88) | 0.67 (0.47-0.87) | 0.70 (0.53-0.88) |
|  | South American | 151k | 0.63 (0.42-0.83) | 0.75 (0.57-0.92) | 0.71 (0.52-0.89) | 0.73 (0.55-0.91) |
|  | Other Hispanic | 451k | 0.59 (0.40-0.78) | 0.64 (0.46-0.81) | 0.57 (0.38-0.76) | 0.60 (0.42-0.78) |
| Harris | Hispanic (all origins) | 932k | 0.74 (0.57-0.91) | 0.60 (0.45-0.75) | 0.52 (0.26-0.79) | 0.51 (0.36-0.65) |
|  | Mexican | 689k | 0.82 (0.63-1.00) | 0.61 (0.46-0.77) | 0.52 (0.17-0.88) | 0.50 (0.35-0.65) |
|  | Puerto Rican | 26k | - | 0.55 (0.24-0.86) | - | 0.50 (0.19-0.80) |
|  | Central American | 88k | 0.63 (0.27-0.99) | 0.61 (0.38-0.85) | 0.60 (0.28-0.93) | 0.62 (0.33-0.91) |
|  | South American | 46k | 0.54 (0.20-0.88) | 0.65 (0.40-0.90) | 0.59 (0.27-0.90) | 0.57 (0.29-0.85) |
|  | Other Hispanic | 55k | 0.55 (0.21-0.88) | 0.48 (0.21-0.75) | 0.46 (0.10-0.81) | 0.46 (0.18-0.75) |
| Bexar | Hispanic (all origins) | 779k | 0.83 (0.67-1.00) | 0.77 (0.62-0.92) | 0.68 (0.42-0.94) | 0.68 (0.54-0.83) |
|  | Mexican | 639k | 0.90 (0.72-1.00) | 0.78 (0.62-0.93) | 0.68 (0.38-0.99) | 0.69 (0.54-0.84) |
|  | Puerto Rican | 28k | - | 0.64 (0.35-0.93) | 0.63 (0.29-0.97) | 0.62 (0.34-0.91) |
|  | Other Hispanic | 84k | 0.59 (0.28-0.91) | 0.78 (0.54-1.00) | 0.67 (0.36-0.98) | 0.71 (0.44-0.98) |
| Texas border (Rio Grande Valley, El Paso, Laredo) | Hispanic (all origins) | 1,240k | 0.62 (0.47-0.76) | 0.58 (0.43-0.72) | 0.57 (0.40-0.74) | 0.50 (0.35-0.64) |
|  | Mexican | 1,161k | 0.62 (0.48-0.77) | 0.58 (0.43-0.72) | 0.57 (0.40-0.75) | 0.50 (0.35-0.64) |
|  | Other Hispanic | 56k | 0.46 (0.25-0.68) | 0.55 (0.36-0.75) | 0.51 (0.29-0.73) | 0.48 (0.30-0.66) |
| Arizona | Hispanic (all origins) | 1,296k | 0.83 (0.68-0.98) | 0.80 (0.65-0.95) | 0.72 (0.53-0.91) | 0.73 (0.58-0.88) |
|  | Mexican | 1,084k | 0.88 (0.72-1.00) | 0.82 (0.68-0.97) | 0.74 (0.53-0.94) | 0.75 (0.60-0.90) |
|  | Puerto Rican | 40k | 0.56 (0.30-0.82) | 0.70 (0.50-0.90) | 0.66 (0.45-0.86) | 0.66 (0.45-0.87) |
|  | Central American | 33k | - | 0.71 (0.49-0.92) | 0.66 (0.42-0.89) | 0.66 (0.43-0.89) |
|  | South American | 25k | - | - | - | 0.74 (0.54-0.95) |
|  | Other Hispanic | 95k | 0.62 (0.38-0.85) | 0.67 (0.47-0.87) | 0.62 (0.40-0.84) | 0.63 (0.42-0.85) |
| Maricopa | Hispanic (all origins) | 758k | 0.90 (0.74-1.00) | 0.92 (0.77-1.00) | 0.75 (0.51-1.00) | 0.86 (0.70-1.00) |
|  | Mexican | 622k | 0.98 (0.83-1.00) | 0.98 (0.83-1.00) | 0.77 (0.48-1.00) | 0.91 (0.74-1.00) |
|  | Puerto Rican | 25k | - | - | - | 0.72 (0.45-1.00) |
|  | Other Hispanic | 56k | 0.68 (0.33-1.00) | 0.69 (0.42-0.96) | 0.64 (0.33-0.95) | 0.68 (0.38-0.97) |
| Nevada | Hispanic (all origins) | 473k | 0.87 (0.71-1.00) | 0.88 (0.74-1.00) | 0.72 (0.51-0.92) | 0.80 (0.65-0.96) |
|  | Mexican | 329k | 0.95 (0.80-1.00) | 0.94 (0.79-1.00) | 0.74 (0.48-0.99) | 0.84 (0.68-1.00) |
|  | Central American | 40k | 0.83 (0.62-1.00) | 0.86 (0.66-1.00) | 0.72 (0.49-0.95) | 0.77 (0.54-1.00) |
|  | Other Hispanic | 40k | 0.70 (0.45-0.95) | 0.70 (0.47-0.92) | 0.67 (0.42-0.93) | 0.69 (0.47-0.92) |
| Clark | Hispanic (all origins) | 369k | 0.89 (0.74-1.00) | 0.90 (0.75-1.00) | 0.72 (0.49-0.95) | 0.79 (0.64-0.95) |
|  | Mexican | 252k | 0.98 (0.83-1.00) | 0.96 (0.81-1.00) | 0.74 (0.45-1.00) | 0.84 (0.67-1.00) |
|  | Central American | 32k | - | 0.87 (0.66-1.00) | 0.72 (0.45-0.99) | 0.75 (0.48-1.00) |
|  | Other Hispanic | 29k | - | 0.69 (0.43-0.96) | 0.66 (0.34-0.98) | 0.67 (0.39-0.95) |
| New York - New Jersey metro | Hispanic (all origins) | 2,264k | 0.84 (0.69-0.98) | 0.75 (0.60-0.89) | 0.58 (0.43-0.74) | 0.54 (0.39-0.68) |
|  | Mexican | 178k | 0.85 (0.67-1.00) | 0.87 (0.70-1.00) | 0.69 (0.49-0.89) | 0.67 (0.47-0.86) |
|  | Puerto Rican | 684k | 0.82 (0.65-0.99) | 0.62 (0.46-0.79) | 0.52 (0.29-0.75) | 0.45 (0.28-0.61) |
|  | Cuban | 85k | 0.60 (0.42-0.78) | 0.67 (0.50-0.85) | 0.57 (0.40-0.73) | 0.61 (0.43-0.78) |
|  | Dominican | 537k | 0.93 (0.78-1.00) | 0.82 (0.66-0.97) | 0.62 (0.42-0.82) | 0.54 (0.39-0.70) |
|  | Central American | 193k | 0.76 (0.57-0.95) | 0.83 (0.66-1.00) | 0.65 (0.46-0.83) | 0.57 (0.38-0.76) |
|  | South American | 429k | 0.85 (0.68-1.00) | 0.75 (0.59-0.91) | 0.56 (0.38-0.73) | 0.48 (0.32-0.65) |
|  | Other Hispanic | 159k | 0.85 (0.68-1.00) | 0.84 (0.67-1.00) | 0.65 (0.47-0.84) | 0.75 (0.58-0.93) |
| New York City | Hispanic (all origins) | 1,364k | 0.89 (0.74-1.00) | 0.80 (0.65-0.94) | 0.63 (0.43-0.83) | 0.59 (0.45-0.74) |
|  | Mexican | 126k | 0.91 (0.71-1.00) | 0.92 (0.74-1.00) | 0.73 (0.42-1.00) | 0.70 (0.47-0.94) |
|  | Puerto Rican | 444k | 0.85 (0.67-1.00) | 0.66 (0.49-0.83) | 0.54 (0.13-0.94) | 0.48 (0.31-0.66) |
|  | Cuban | 31k | 0.92 (0.73-1.00) | 0.94 (0.75-1.00) | 0.78 (0.50-1.00) | 0.90 (0.70-1.00) |
|  | Dominican | 386k | 0.97 (0.82-1.00) | 0.85 (0.69-1.00) | 0.69 (0.38-1.00) | 0.60 (0.44-0.77) |
|  | Central American | 76k | 0.73 (0.45-1.00) | 0.84 (0.61-1.00) | 0.62 (0.25-0.99) | 0.52 (0.22-0.82) |
|  | South American | 210k | 0.88 (0.70-1.00) | 0.82 (0.64-1.00) | 0.67 (0.36-0.99) | 0.55 (0.37-0.73) |
|  | Other Hispanic | 92k | 0.95 (0.78-1.00) | 0.96 (0.80-1.00) | 0.77 (0.49-1.00) | 0.95 (0.77-1.00) |
| Hudson/Passaic/Essex/Union NJ | Hispanic (all origins) | 465k | 0.77 (0.62-0.92) | 0.68 (0.53-0.83) | 0.55 (0.40-0.71) | 0.43 (0.28-0.58) |
|  | Puerto Rican | 120k | 0.91 (0.72-1.00) | 0.77 (0.51-1.00) | 0.63 (0.36-0.89) | 0.55 (0.27-0.83) |
|  | Cuban | 35k | 0.44 (0.23-0.64) | 0.54 (0.32-0.76) | 0.53 (0.35-0.70) | 0.50 (0.28-0.72) |
|  | Dominican | 91k | 0.86 (0.65-1.00) | 0.71 (0.52-0.90) | 0.55 (0.37-0.72) | 0.34 (0.14-0.53) |
|  | Central American | 37k | 0.71 (0.44-0.97) | 0.77 (0.53-1.00) | 0.67 (0.49-0.85) | 0.57 (0.34-0.79) |
|  | South American | 131k | 0.86 (0.66-1.00) | 0.63 (0.44-0.82) | 0.48 (0.31-0.66) | 0.32 (0.12-0.52) |
|  | Other Hispanic | 28k | - | 0.68 (0.39-0.96) | 0.57 (0.38-0.75) | 0.46 (0.15-0.77) |
| Pennsylvania | Hispanic (all origins) | 622k | 0.86 (0.71-1.00) | 0.84 (0.69-0.99) | 0.72 (0.55-0.89) | 0.78 (0.64-0.93) |
|  | Mexican | 83k | 0.84 (0.64-1.00) | 0.89 (0.72-1.00) | 0.78 (0.57-1.00) | 0.88 (0.72-1.00) |
|  | Puerto Rican | 314k | 0.92 (0.77-1.00) | 0.86 (0.70-1.00) | 0.71 (0.48-0.93) | 0.78 (0.61-0.95) |
|  | Dominican | 75k | 0.78 (0.58-0.99) | 0.75 (0.56-0.95) | 0.64 (0.42-0.86) | 0.64 (0.44-0.84) |
|  | Central American | 29k | - | 0.77 (0.57-0.97) | 0.70 (0.48-0.91) | 0.72 (0.52-0.93) |
|  | South American | 53k | 0.82 (0.62-1.00) | 0.88 (0.71-1.00) | 0.78 (0.59-0.97) | 0.86 (0.70-1.00) |
|  | Other Hispanic | 49k | 0.78 (0.56-1.00) | 0.83 (0.65-1.00) | 0.73 (0.50-0.95) | 0.81 (0.62-1.00) |
| California | Hispanic (all origins) | 8,453k | 0.69 (0.55-0.84) | 0.67 (0.53-0.82) | 0.57 (0.40-0.74) | 0.56 (0.42-0.71) |
|  | Mexican | 6,719k | 0.70 (0.56-0.85) | 0.66 (0.51-0.80) | 0.55 (0.37-0.73) | 0.54 (0.39-0.68) |
|  | Puerto Rican | 171k | 0.58 (0.39-0.77) | 0.59 (0.42-0.76) | 0.53 (0.34-0.71) | 0.50 (0.33-0.68) |
|  | Cuban | 80k | 0.49 (0.29-0.69) | 0.60 (0.43-0.78) | 0.56 (0.37-0.76) | 0.54 (0.36-0.72) |
|  | Central American | 703k | 0.92 (0.77-1.00) | 0.93 (0.78-1.00) | 0.75 (0.56-0.95) | 0.87 (0.71-1.00) |
|  | South American | 280k | 0.64 (0.44-0.83) | 0.72 (0.56-0.89) | 0.67 (0.49-0.85) | 0.69 (0.53-0.86) |
|  | Other Hispanic | 485k | 0.38 (0.21-0.56) | 0.55 (0.38-0.71) | 0.51 (0.32-0.70) | 0.50 (0.32-0.67) |
| Los Angeles | Hispanic (all origins) | 2,583k | 0.71 (0.56-0.85) | 0.72 (0.57-0.86) | 0.63 (0.39-0.87) | 0.61 (0.46-0.75) |
|  | Mexican | 1,900k | 0.72 (0.57-0.86) | 0.69 (0.55-0.83) | 0.62 (0.32-0.91) | 0.58 (0.43-0.72) |
|  | Puerto Rican | 38k | 0.65 (0.33-0.97) | 0.68 (0.43-0.94) | 0.59 (0.26-0.91) | 0.53 (0.25-0.81) |
|  | Cuban | 30k | 0.42 (0.16-0.69) | 0.58 (0.35-0.80) | 0.56 (0.25-0.87) | 0.52 (0.26-0.77) |
|  | Central American | 377k | 0.99 (0.84-1.00) | 0.99 (0.84-1.00) | 0.77 (0.48-1.00) | 0.95 (0.79-1.00) |
|  | South American | 101k | 0.55 (0.26-0.84) | 0.69 (0.45-0.92) | 0.63 (0.33-0.93) | 0.62 (0.40-0.84) |
|  | Other Hispanic | 131k | 0.29 (0.09-0.49) | 0.57 (0.34-0.80) | 0.55 (0.20-0.91) | 0.53 (0.26-0.80) |
| Illinois | Hispanic (all origins) | 1,218k | 0.90 (0.75-1.00) | 0.86 (0.72-1.00) | 0.78 (0.59-0.98) | 0.74 (0.60-0.89) |
|  | Mexican | 891k | 0.90 (0.75-1.00) | 0.85 (0.70-0.99) | 0.78 (0.56-1.00) | 0.69 (0.55-0.84) |
|  | Puerto Rican | 148k | 0.95 (0.80-1.00) | 0.94 (0.78-1.00) | 0.79 (0.57-1.00) | 0.88 (0.70-1.00) |
|  | Central American | 45k | 0.87 (0.68-1.00) | 0.84 (0.65-1.00) | 0.74 (0.52-0.96) | 0.82 (0.62-1.00) |
|  | South American | 58k | 0.91 (0.74-1.00) | 0.93 (0.77-1.00) | 0.81 (0.59-1.00) | 0.91 (0.74-1.00) |
|  | Other Hispanic | 47k | 0.83 (0.63-1.00) | 0.89 (0.72-1.00) | 0.78 (0.57-1.00) | 0.86 (0.67-1.00) |


## Reading the numbers

* Relative statements are the safe ones: Cuban Americans sit 0.2-0.3 below Mexican Americans nationally in all three cycles in the model (0.13-0.23 in CES); Puerto Rican and Mexican Americans
  are close (raw gap +0.03 to +0.04 in the model, 0.00 to +0.10 in CES); Central Americans are above the Mexican level (+0.05 to +0.14 raw). The origin groups are a real source of variation inside the Hispanic vote: in Florida the
  Cuban, Puerto Rican and South American estimates for 2024 span 0.24 to 0.49 against 0.39 for the Florida Hispanic total.
* Levels carry about +/-0.15 (90 %), and more in cells where the group is a small part of the area: the intervals in the table are wide on purpose.
* Changes over time inside one group and region are less reliable than they look because the cycle-level alignment is a single number and the 2016 geography (2010 tracts) differs from 2020 and 2024.
* Never read a cell as "X % of Cuban Americans voted for Y": it is an estimate for a group in a place, built from where people live.

## Limits

* **Tract votes are allocated**, not counted. The smoothing within precincts biases group coefficients toward each other; the size of this is not separately measured here.
* **Group identity comes from residence.** Citizen adults of an origin group are inferred from ACS origin counts and state PUMS ratios; within a tract the model assumes every member of a group votes alike
  (aggregation bias), which the Hispanic-in-Hispanic-tracts effect above is an example of.
* **Turnout is anchored by national CPS ratios**, which overstate turnout (self-reported) and do not split by origin; Hispanic origins share one prior ratio.
* **2022 is mostly carried forward**; origin splits for single-county areas are the 2020 / 2024 splits. 2022 has no survey check with an origin split (CES 2022 has no presidential vote), so only the
  composition test above covers it.
* **Dominicans, Central and South Americans** have 38-170 CES respondents in a cycle; the survey cannot referee them well. Other Hispanic is a residual group.
* Non-Hispanic groups are in the model as controls; their own estimates are in the CSV (`validated_against_survey = False`) and are rougher (the Black Democratic share comes out at 0.98 to 0.99).
* Alaska and Hawaii have no tract votes; Connecticut counties are the old eight in the 2016 and 2020 ACS vintages and the nine planning regions in 2022 and 2024 (tract votes do not depend on this; the county file `_all` does).

## Reproduce

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/us_origin_data.py fetch && uv run python analysis/us_origin_data.py build    # ACS B03001/B05003/B05003I/B03002 (bulk files)
uv run python analysis/us_origin_pums.py fetch 2016 2020 2024 && uv run python analysis/us_origin_pums.py build 2016 2020 2024
uv run python analysis/us_origin_groups.py                                                          # county_origin_groups.csv
uv run python analysis/us_origin_frames.py fetch && uv run python analysis/us_origin_frames.py build # tract frames
uv run python analysis/us_origin_ces.py fetch && uv run python analysis/us_origin_ces.py build
uv run python -c "import sys; sys.path.insert(0, 'analysis'); import us_origin_fit as M; M.fit_all(400, 400, specs=('plain',))"
uv run python analysis/us_origin_report.py fit2022 && uv run python analysis/us_origin_report.py loco
uv run python analysis/us_origin_export.py
uv run python -m pytest tests/test_origin_ei.py tests/test_origin_pipeline.py -q
```
Sources: Census Bureau ACS (public domain); Fekrazad precinct-to-tract reallocation (CC0); MEDSL and state results via `unified/results_county`; CES (CC0);
Pew Research Center, UnidosUS / BSP Research, FIU Cuba Poll, AP VoteCast (via PBS NewsHour), Latino Decisions (via MSU JSRI), USA Today / Telemundo (via PressReader) for the published polls
(URLs in `data_public/us_hispanic_origin_polls.csv`).
