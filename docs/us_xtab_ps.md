# Crosstab poststratification of a poll onto Texas counties (county distribution only)

**Status: research. Nothing registered was edited. This section (Bar) was written before any county score was computed.**
Code: `analysis/us_xtab_ps.py`. Outputs (gitignored): `results/us_xtab_ps/*.csv`.

*This analysis was conducted using data from the Redistricting Data Hub.* (L2 county turnout files, RDH noncommercial terms;
the files stay on the SSD, only aggregate derived tables are published here.)

## Question

v3 puts the statewide poll average on the county baselines as a **uniform additive swing** (county share of the last same-office
race + one constant). A poll's own crosstabs say that groups moved by different amounts (Hispanic, young, college, ...).
Does poststratifying those group margins onto each county's electorate give a **better county distribution** than the
uniform swing, holding the statewide level fixed?

## Openly published polls used (and not used)

- **Siena/Spectrum News Texas likely voters, 16-19 Oct 2022, 649 LV** (governor: O'Rourke 43, Abbott 52). Crosstabs PDF on
  `sri.siena.edu/wp-content/uploads/2022/10/TX1022-Crosstabs.pdf` (linked from the sri.siena.edu release). Groups printed:
  party ID, gender, region, age (18-49 / 50-64 / 65+), ethnicity (white / Black / Latino), education (<BA / BA+), income.
  The printed "Nature of the Sample" gives the weighted group shares.
- **Siena's final 2024 Texas poll (NYT/Siena, 23-26 Oct 2024) is NOT openly available.** sri.siena.edu's release page links
  only to nytimes.com interactives for the crosstabs; nytimes.com blocks automated access and I did not bypass it. No Siena
  PDF exists on sri.siena.edu for it. **Fallback for 2024:** the University of Texas / Texas Politics Project October 2024
  poll (2-10 Oct, 1,091 likely voters), whose release page (texaspolitics.utexas.edu) publishes a table of group vote shares
  for the Senate and presidential races: Latino 22% / Black 13% / White 59%, men 46% / women 54%, 18-29 16%. It has no age
  bands beyond 18-29 and no education, so the 2024 test is coarser (race, gender, 18-29 only). Not Siena; stated as such.
- **2026:** the Oct 27-Nov 1 2026 NYT/Siena Texas likely-voter crosstabs (1,215 LV; the PDF on the SSD, columns after
  Houston cut off): gender, age (18-29 / 30-44 / 45-64 / 65+), race (white / Black / Hispanic / other), education, race x
  education (4 cells), Dallas-Fort Worth, Houston.

## Model (fixed before computing)

Groups used (both PDFs have them where available): gender, age band, race/ethnicity, education (+ race x education cell in 2026).
Party ID and income are not used (no county composition for them). Region is **not** used: Siena's regions (Austin, DFW,
Houston, "Minor", Rural, San Antonio/South) are not defined by county in the PDF.

1. **Poll fit.** Two-party D share per printed group, $d_g = D/(D+R)$. Cells = product of the poll's weighted group shares
   (independence between dimensions, except that the 2026 race x education cell is one dimension). Additive logit
   $\text{logit}\,p = \alpha + \sum_k \beta_{k,g}$ with $\beta$ centred to zero on the poll's weighted shares inside every
   dimension; weighted least squares on the group margins (weights = group $n_\text{eff}$), so that every published margin is
   reproduced as closely as the (rounded, mutually inconsistent) inputs allow. "Other race" = remainder, its $d$ taken from the
   topline identity and clipped to [0.25, 0.75].
2. **Shrinkage rule (stated before computing).** $\beta^{s}_{k,g} = \beta_{k,g}\cdot n^\text{eff}_g/(n^\text{eff}_g+16)$,
   $n^\text{eff}_g = $ group weighted share $\times$ poll $n / 1.75$ (design effect 1.75, the Siena 2022 MOE $5.1$ pts at
   $n=649$; used for every poll). 16 = $4/\tau^2$ with $\tau = 0.5$ logit points of prior sd of a group effect. Toward the
   statewide value = toward 0. Sensitivities (not used for the bar): no shrinkage, and 16 -> 64.
3. **Composition.** County electorate = L2 voted counts by gender and by race/ethnicity (L2 eur, hisp, aa, other = esa + oth;
   unknown spread proportionally), age bands from the L2 roll (45-54 split at 50 half/half), education from ACS county
   `ba` (county_demo, vintage not after the test election). Within a county the dimensions are independent (L2 gives only
   margins). The 2026 base is the **2022 midterm** L2 file (gender, ethnicity voted 2022), not 2024.
   **Calibration:** statewide group shares (county L2-vote-weighted) are tilted to the poll's own weighted group shares
   by county-constant multiplicative factors per group, iterated to convergence (raking), the county's total weight unchanged.
4. **Poststratify** $p_c = \sum_\text{cells} \prod_k s_{c,k,g}\;\text{expit}(\alpha+\sum \beta^s)$.
5. **Level fixed.** Additive constant $\delta$ so that the vote-weighted statewide share equals the **target**: the frozen v3
   poll average in 2026 (Senate 0.5129, governor 0.4856 from `data_public/us2026_polls.json`, **not** Siena's topline) and
   the actual statewide two-party result in the backtests. The uniform-swing comparator gets the same treatment: county
   baseline + its own constant to the same target. So only the county distribution differs.
6. **Hybrid (declared secondary):** $0.5\,(\text{baseline}+\delta_0) + 0.5\,(p_c^{PS}+\delta_1)$, each raked to the target. Reported;
   not part of the bar; no weight tuned.

County baselines (uniform-swing comparator = v3 rule, same office, most recent): 2022 governor <- 2018 governor; 2024 Senate
<- 2018 Senate; 2024 president <- 2020 president (descriptive). Compositions known before the test election: 2022 test uses L2
voted **2020** (gender, race; file snapshot 2023-02, a known survivorship limit), the L2 roll age bands of the 2023-02 snapshot
(registrant ages, not result-informed; the one place where a post-election file is used), ACS 2020; the 2024 test uses L2 voted 2022
and the same roll file.

## Bar (pass criteria, written before computing)

Per validation poll $v$ (Siena 2022 governor; UT/TxPP 2024 Senate): scoring on the **actual** county two-party results,
weights = actual two-party votes. Metrics, both for PS-only versus uniform swing:
(a) vote-weighted mean absolute error of the county D share, (b) unweighted mean absolute error of the county margin ($2s-1$,
points). Difference $\Delta$ = uniform - PS (positive = PS better). **Pass** requires, in **every** available validation poll,
$\Delta>0$ in (a) and (b) with the 95% **county-cluster bootstrap** percentile interval (B = 2,000 resamples of the 254 counties,
seed 20261103) excluding zero. Rio Grande Valley (Cameron, Hidalgo, Starr, Willacy) and the other border counties (El Paso, Webb,
Maverick, Zapata, Val Verde, Kinney, Hudspeth, Presidio, Brewster, Terrell) are reported separately (no bar of their own).
**If the bar is not passed, no `configs/us2026_xtab/` and no priors file are built;** only the descriptive 2026 county table is
published, labelled "one poll, not validated". Because Siena's final 2024 crosstabs are not open, the 2024 leg is a different
pollster with coarser groups; this is stated wherever the result is used.

## Amendment before any score was computed: pooling (instruction from the lead)

To avoid one poll dominating, the group vote shares are **pooled over all openly published Texas crosstabs in the window**
(2026: field end from 2026-09-20; 2024: from 2024-10-01; 2022: from 2022-10-01), Siena being one input. Collected by three
helper agents under the access rules (printed numbers only; bot-walled pages logged `blocked`, not retried) into
`data_public/us_xtab_ps_{manifest,rows}_{2026,2024,2022}.csv`. In the model:

- Observations = every printed (poll, contest, group) two-party share $d = D/(D+R)$. A group may be a set of model cells
  (e.g. age 18-49 = my bands 18-29, 30-44, 45-49; race x education cells). Model age levels: 18-29, 30-44, 45-49, 50-64, 65+.
- **Poll-specific intercept** (house level / topline differences) + **shared** group effects: logit $p = \alpha_{poll}$ + gender + age +
  race + education + one white x BA+ term. Weighted least squares on the margins, observation weight =
  $\frac{n_g/1.75}{\hat d(1-\hat d)}\,0.5^{\text{age}/14}$, $n_g$ = printed respondents of the group (else sample share x poll n),
  age in days to the as-of date (2026-11-02; backtests: the day before the election), the poll rule's 14-day half-life.
  A tiny ridge (1e-3) on the effects only for conditioning. Each poll's total weight share is reported.
- Shrinkage as above, with $n^\text{eff}_g$ = the **pooled** effective n of the level (sum over polls, no recency).
- Reference group shares (to turn a group set into cells and to calibrate county compositions) = the weighted
  ($\sqrt{n}\times 0.5^{\text{age}/14}$) mean of the polls' printed weighted sample shares.
- Backtest 2022: Siena/Spectrum Oct 2022 + other 2022 polls with open crosstabs. Backtest 2024: the openly published 2024 polls
  (UT/TxPP etc.). The NYT/Siena 2024 and 2022-style NYT pages are on nytimes.com only; the user-saved copy, if one appears in
  ~/Downloads before the final run, is added as a further input and noted (at the time of writing only the Oct 2026 PDF was there).
- 2024 contests: Senate (primary, bar) and president (descriptive); baselines 2020 Senate / 2020 president (v3 rule: same office,
  most recent; 2022 governor <- 2018 governor). The bar applies to 2022 governor and 2024 Senate.
- L2 composition for the 2024 backtests = the 2022 midterm file; for 2026 also the 2022 file (per the lead), calibrated to the
  pooled polls' weighted shares (county-constant multiplicative tilts per group, iterated). 2022 backtest: voted 2020.

*(Everything below the next heading was added after the computation.)*

## Results

### 1. Pre-declared test: PS-only vs uniform swing (both raked to the actual statewide result)

Vote-weighted county share MAE (points) / unweighted county margin MAE (points). Primary shrinkage (K = 16).

| test | subset | uniform swing | PS only | hybrid 50/50 |
|---|---|---|---|---|
| 2022 governor | all 254 | 2.29 / 8.4 | 10.26 / 36.0 | 5.99 / 21.8 |
| 2022 governor | RGV (4) | 4.54 / 9.6 | 1.24 / 3.6 | 2.14 / 5.7 |
| 2022 governor | other border (10) | 5.91 / 15.7 | 8.77 / 22.7 | 3.00 / 15.3 |
| 2022 governor | non-border (240) | 2.11 / 8.1 | 10.58 / 37.0 | 6.20 / 22.3 |
| 2024 Senate | all 254 | 1.51 / 3.6 | 8.79 / 30.2 | 4.67 / 16.1 |
| 2024 Senate | RGV (4) | 6.14 / 13.3 | 1.03 / 6.7 | 2.86 / 7.2 |
| 2024 Senate | other border (10) | 8.42 / 12.5 | 7.53 / 18.1 | 2.23 / 12.3 |
| 2024 Senate | non-border (240) | 1.13 / 3.1 | 9.08 / 31.1 | 4.80 / 16.4 |
| 2024 president (descriptive) | all 254 | 1.54 / 4.0 | 8.77 / 30.2 | 4.11 / 14.0 |
| 2024 president (descriptive) | RGV (4) | 6.48 / 12.1 | 0.83 / 6.0 | 3.22 / 7.4 |
| 2024 president (descriptive) | other border (10) | 7.21 / 8.5 | 8.97 / 20.6 | 3.36 / 13.0 |
| 2024 president (descriptive) | non-border (240) | 1.18 / 3.7 | 9.02 / 31.0 | 4.17 / 14.2 |

County-cluster bootstrap (B = 2,000, seed 20261103) of uniform minus PS (positive = PS better), 95% interval:

| test | variant | d weighted share MAE (pts) | d margin MAE (pts) | bar |
|---|---|---|---|---|
| 2022 governor | primary, ps | -7.97 [-11.15, -5.64] | -27.5 [-29.2, -25.7] | fail |
| 2022 governor | primary, hybrid | -3.70 [-5.31, -2.54] | -13.4 [-14.3, -12.4] |  |
| 2022 governor | noshrink, ps | -7.90 [-10.99, -5.58] | -27.1 [-28.8, -25.3] |  |
| 2022 governor | shrink64, ps | -8.14 [-11.46, -5.80] | -28.4 [-30.2, -26.6] |  |
| 2024 Senate | primary, ps | -7.28 [-10.05, -5.16] | -26.6 [-28.3, -24.7] | fail |
| 2024 Senate | primary, hybrid | -3.16 [-4.58, -1.99] | -12.4 [-13.5, -11.3] |  |
| 2024 Senate | noshrink, ps | -7.25 [-9.99, -5.13] | -26.3 [-28.0, -24.4] |  |
| 2024 Senate | shrink64, ps | -7.41 [-10.22, -5.27] | -27.4 [-29.2, -25.5] |  |
| 2024 president (descriptive) | primary, ps | -7.23 [-10.06, -5.13] | -26.1 [-27.7, -24.4] | fail |
| 2024 president (descriptive) | primary, hybrid | -2.57 [-3.95, -1.51] | -10.0 [-10.8, -9.1] |  |
| 2024 president (descriptive) | noshrink, ps | -7.21 [-10.00, -5.13] | -25.9 [-27.5, -24.2] |  |
| 2024 president (descriptive) | shrink64, ps | -7.34 [-10.22, -5.24] | -26.9 [-28.5, -25.2] |  |

**The bar is failed in both validation polls (2022 Siena-led pool, 2024 pool): the PS-only county distribution is far worse than the uniform swing on both metrics, and the interval is entirely on the wrong side of zero.** It is not rescued by no shrinkage or K = 64. The reason is structural: a composition-only model has no county identity (its county shares are compressed toward the state mean: county-level partisanship that demography does not explain is lost). Where composition is the story it does better: in the four RGV counties PS-only has 1.2 (2022), 1.0 (2024 Senate) points of error against 4.5 and 6.1 for the uniform swing (RGV is 4 of 254 counties and about 3% of the votes, and a mean over 4 counties is not a significance claim; other border counties: PS-only worse in 2022 and about equal in 2024).

### 2. Exploratory, post hoc (written after the PS-only result): swing differential

County share = 2022-governor share + [PS(pooled 2024 polls) - PS(pooled 2022 polls)] + constant, both PS on the same L2-2022 / ACS-2024 composition; uniform swing from the same 2022 baseline; both raked to the actual statewide result. The baseline office differs from the contest (governor to Senate / president), equally for both comparators. Not part of the pre-declared bar, one validation leg only (no 2020 pool exists).

| test | subset | uniform | PS swing |
|---|---|---|---|
| 2024 Senate | all | 1.56 / 4.0 | 1.50 / 5.8 |
| 2024 Senate | RGV | 6.33 / 12.9 | 1.57 / 5.8 |
| 2024 Senate | other border | 6.18 / 8.2 | 1.78 / 7.8 |
| 2024 Senate | non-border | 1.25 / 3.7 | 1.49 / 5.7 |
| 2024 president | all | 2.08 / 6.1 | 1.55 / 5.7 |
| 2024 president | RGV | 8.80 / 19.4 | 1.42 / 5.2 |
| 2024 president | other border | 7.21 / 10.7 | 1.62 / 8.2 |
| 2024 president | non-border | 1.68 / 5.7 | 1.55 / 5.6 |

2024 Senate: uniform minus PS-swing, weighted share MAE +0.05 pts [-0.38, +0.51]; margin MAE -1.8 pts [-2.3, -1.3].

2024 president: uniform minus PS-swing, weighted share MAE +0.53 pts [+0.01, +1.16]; margin MAE +0.4 pts [-0.2, +1.0].

Reading: the swing differential removes most of the RGV / border error (about 6 points to 1.6) and costs a little in the other 240 counties (1.25 to 1.49 points, Senate). Vote-weighted overall it is a tie for the Senate (interval spans 0; the unweighted margin is *worse*, interval below 0) and slightly better for the president (interval just above 0 on the weighted metric, spanning 0 on the margin). It would not pass the stated bar (both metrics, both polls), and it is a single post-hoc leg. **Not validated.**

### 3. Which polls were pooled and with what weight (share of the least-squares weight)

2022 governor: bsp-univision-20221018 12%; civiqs-20221011 6%; cws-20221105 18%; emerson-20221019 10%; marist-20221006 9%; siena-20221019 9%; uh-hobby-20221026 15%; utexas-txpp-20221017 10%; uttyler-20221024 10%.

2024 Senate: cmc-rose-20241017 20%; emerson-20241021 17%; hobby-20241010 15%; marist-20241007 19%; ut-txpp-20241010 15%; uttyler-20241021 12%. (The president pool is the same polls, weights within 3 points.)

2026 Senate: nyt-siena-20261101 18.3%; fox-20261019 12.5%; onpoint-20261028 12.5%; tsu-20261025 12.0%; yougov-20261005 9.1%; uh-hobby-20261014 7.1%; marist-20261011 5.7%; onpoint-20261018 5.2%; emerson-20261019 3.7%; fox-20260928 2.9%; trafalgar-20261008 2.8%; quantus-20261011 2.6%; marist-20260920 2.1%; utexas-tpp-20261005 1.9%; reconmr-20261008 1.8%.

2026 governor: nyt-siena-20261101 19.1%; fox-20261019 13.3%; onpoint-20261028 12.9%; tsu-20261025 12.7%; yougov-20261005 9.7%; uh-hobby-20261014 7.6%; marist-20261011 6.1%; onpoint-20261018 5.5%; emerson-20261019 3.9%; fox-20260928 3.1%; marist-20260920 2.2%; utexas-tpp-20261005 2.1%; reconmr-20261008 1.9%.

Blocked or without open crosstabs (not pooled): see the `status` column of `data_public/us_xtab_ps_manifest_*.csv` (NYT/Siena 2024 Oct 4 and Oct 26 on nytimes.com, blocked, not fetched; CNN/SSRS, Univision/YouGov, VoteHub 2026 403; others topline-only). The Siena 2022 poll is parsed by the script from the sri.siena.edu PDF. Caveats from collection: Emerson numbers are weighted-count row percents from a public sheet; ReconMR/Emerson percentages include undecideds (two-party share taken as D/(D+R)); Fox Sep n from a tracker; one 2022 poll is Republican-sponsored (CWS Research, 18% of the 2022 weight); where neither n nor a share is printed the observation uses the consensus share x poll n (UT/TxPP 2022, 2024).

2026 sen pooled group effects (logit, centred, after shrinkage): gender M/F -0.21/+0.20; age 18-29 +1.22, 30-44 +0.43, 45-49 -0.30, 50-64 -0.20, 65+ -0.57; race white -0.86, Black +2.22, Hispanic +0.85, other +0.78; BA+ -0.20 (no BA +0.17); white x BA+ +1.31. Calibration shares (pooled, weighted): white 0.591, Black 0.115, Hispanic 0.238.

2026 gov pooled group effects (logit, centred, after shrinkage): gender M/F -0.24/+0.22; age 18-29 +1.07, 30-44 +0.46, 45-49 -0.27, 50-64 -0.25, 65+ -0.50; race white -0.85, Black +2.15, Hispanic +0.89, other +0.71; BA+ -0.19 (no BA +0.17); white x BA+ +1.28. Calibration shares (pooled, weighted): white 0.591, Black 0.115, Hispanic 0.238.


## 2026: descriptive county table, ONE POLL POOL, NOT VALIDATED

The PS-only county distribution failed its bar, so **nothing is built for registration** (no `configs/us2026_xtab/`, no priors file, no builder). The tables below are descriptive. Statewide level is fixed at the frozen v3 poll average (Senate 0.5129, governor 0.4856) in every column, so differences are purely county distribution. 2026 base: L2 2022 midterm gender x ethnicity voted, L2 roll age bands (2023-02), ACS 2026 (vintage 2024) BA share, calibrated to the pooled polls' weighted shares. v3 prior = county baseline (Senate: 2024 Senate; governor: 2022 governor) + uniform additive swing, weights = 2022 governor two-party votes (the v3 rule).

Columns: v3 prior; **PS only** (failed in the backtests, shown for completeness; do not use); **PS swing** (exploratory: baseline + [PS(2026 polls) - PS(2024 Senate pool or 2022 governor pool)], unvalidated, mixed result in the one post-hoc test above).


### Senate (Talarico D): two-party D share

| county | expected 2-party votes | v3 prior | PS only | PS swing | PS swing - v3 |
|---|---|---|---|---|---|
| Hidalgo | 139,454 | 0.588 | 0.633 | 0.635 | +0.047 |
| Cameron | 75,957 | 0.576 | 0.603 | 0.614 | +0.038 |
| Starr | 10,915 | 0.542 | 0.671 | 0.598 | +0.056 |
| Willacy | 3,793 | 0.604 | 0.601 | 0.644 | +0.039 |
| Webb | 43,565 | 0.600 | 0.652 | 0.651 | +0.051 |
| El Paso | 162,729 | 0.658 | 0.599 | 0.693 | +0.034 |
| Bexar | 533,016 | 0.637 | 0.547 | 0.652 | +0.015 |
| Harris | 1,085,914 | 0.613 | 0.575 | 0.616 | +0.004 |
| Dallas | 617,318 | 0.700 | 0.592 | 0.694 | -0.006 |
| Travis | 455,387 | 0.776 | 0.530 | 0.784 | +0.008 |
| Tarrant | 583,023 | 0.555 | 0.530 | 0.550 | -0.006 |
| RGV (4), vote-weighted | 230,119 | 0.582 | 0.625 | 0.626 | +0.044 |
| rural white (ACS white NH > 80%, 20 counties), vote-weighted | 135,622 | 0.204 | 0.357 | 0.181 | -0.023 |

Largest PS-swing differences from v3 among counties with more than 20,000 expected votes: Webb 0.600->0.651 (+0.051); Hidalgo 0.588->0.635 (+0.047); Cameron 0.576->0.614 (+0.038); El Paso 0.658->0.693 (+0.034); Bowie 0.318->0.288 (-0.030); Harrison 0.305->0.279 (-0.026); Hood 0.239->0.213 (-0.026); Gregg 0.354->0.330 (-0.025).

Vote-weighted mean absolute difference from v3: PS swing 1.11 pts, PS only 8.36 pts. Siena-topline variant (descriptive only, separate columns `ps_siena_topline`, `v3_siena_topline` in the CSV): statewide 0.5376 (Senate) and 0.5053 (governor) instead of the poll average; same county distributions.

### Governor (Hinojosa D): two-party D share

| county | expected 2-party votes | v3 prior | PS only | PS swing | PS swing - v3 |
|---|---|---|---|---|---|
| Hidalgo | 139,454 | 0.634 | 0.612 | 0.625 | -0.009 |
| Cameron | 75,957 | 0.589 | 0.581 | 0.578 | -0.011 |
| Starr | 10,915 | 0.632 | 0.650 | 0.611 | -0.021 |
| Willacy | 3,793 | 0.604 | 0.581 | 0.576 | -0.028 |
| Webb | 43,565 | 0.664 | 0.631 | 0.656 | -0.008 |
| El Paso | 162,729 | 0.687 | 0.577 | 0.680 | -0.007 |
| Bexar | 533,016 | 0.624 | 0.522 | 0.625 | +0.000 |
| Harris | 1,085,914 | 0.589 | 0.546 | 0.592 | +0.002 |
| Dallas | 617,318 | 0.677 | 0.564 | 0.677 | +0.000 |
| Travis | 455,387 | 0.778 | 0.501 | 0.805 | +0.027 |
| Tarrant | 583,023 | 0.520 | 0.502 | 0.522 | +0.002 |
| RGV (4), vote-weighted | 230,119 | 0.618 | 0.603 | 0.608 | -0.011 |
| rural white (ACS white NH > 80%, 20 counties), vote-weighted | 135,622 | 0.165 | 0.331 | 0.141 | -0.024 |

Largest PS-swing differences from v3 among counties with more than 20,000 expected votes: Travis 0.778->0.805 (+0.027); Henderson 0.214->0.189 (-0.025); Collin 0.490->0.515 (+0.025); Kerr 0.250->0.227 (-0.024); Van Zandt 0.161->0.137 (-0.024); Brazos 0.433->0.456 (+0.023); Jefferson 0.473->0.452 (-0.022); Burnet 0.249->0.228 (-0.020).

Vote-weighted mean absolute difference from v3: PS swing 1.03 pts, PS only 9.28 pts. Siena-topline variant (descriptive only, separate columns `ps_siena_topline`, `v3_siena_topline` in the CSV): statewide 0.5376 (Senate) and 0.5053 (governor) instead of the poll average; same county distributions.

## Conclusions

1. **Poststratifying pooled crosstabs onto county composition fails as a replacement for v3's county distribution.** Pre-declared bar failed in both validation polls (2022 governor, 2024 Senate) on both metrics: vote-weighted share MAE 10.3 vs 2.3 points (2022) and 8.8 vs 1.5 (2024 Senate) for PS-only vs uniform swing; intervals entirely on the wrong side. A composition-only model throws away the county baseline, which carries most of the information.
2. It captures what the uniform swing cannot: the Hispanic (RGV/border) movement. RGV error 1.0-1.2 (PS-only) and 1.6 (swing form) vs 4.5-6.3 points for the uniform swing in the backtests. The swing form's overall result is mixed (Senate tie on the weighted metric, worse on the unweighted margin; president slightly better) and it was devised after seeing the failure and tested on one leg. Not validated, not built.
3. **Siena 2022 is the only Siena Texas crosstab open to read; final 2024 NYT/Siena crosstabs are on nytimes.com only** (blocked; the files the user saved in ~/Downloads were Oct 2026 only). The 2024 leg is therefore a pool of six other pollsters (UT/TxPP, Emerson, Marist, UH Hobby, CMC-Rose, UT Tyler). The 2022 pool includes Siena (9% of weight).
4. Limits: composition is margins only (independence within a county; race x education only through one interaction term); L2 files are 2023-02 snapshots (survivorship); roll ages are registrants; ACS education is population, tilted to poll shares; the effective-n and design effect are assumptions; the crosstab numbers were transcribed by helper agents and spot-checked, not double-keyed.
5. Registered/frozen files were not touched. New files: `analysis/us_xtab_ps.py`, `docs/us_xtab_ps.md`, `data_public/us_xtab_ps_{manifest,rows}_{2022,2024,2026}.csv`; outputs under `results/us_xtab_ps/`. One conditioning change after the first backtest run (ridge rescaled; the first run had an unidentified 45-49 age effect of 10 logits); results above are from the final code; the conclusion did not change.
