# L2-informed expected votes for the unified challenger (county and House county × district pieces)

**Status: an evaluation plus one option. The county component is a documented null; the House piece split for the redrawn states
passes its bar and is shipped as option priors (new files only). Nothing registered was edited.** Code: `src/brunder/us_l2turnout.py`,
`analysis/us_l2turnout.py`, tests `tests/test_us_l2turnout.py`. Results (gitignored): `results/us_l2turnout/*.csv`; every table below is
reproduced from them.

*This analysis was conducted using data from the Redistricting Data Hub.* The L2 fields (ethnicity, party where not registered) are
modelled; every L2 file is a later snapshot of the rolls, so older elections inside it suffer survivorship (movers and deaths are gone,
purged non-voters are gone). The RDH/L2 files stay on the SSD (`raw/us/rdh/`); only aggregate derived statistics are published here
(tables, `data_public/us_l2turnout_params.json`, the option priors and the district shift table).

## The plain answer

- **County expected votes of the statewide races: no.** Out of sample, the L2 composition model (state group turnout rates × the county's
  registrants by ethnicity and gender, adjusted by the county's own residual) is **worse** than the challenger's turnout shares in both
  held-out cycles: mean total-variation distance of the county shares of a race's votes 0.0242 vs 0.0214 in 2022 (65 races) and 0.0211 vs
  0.0197 in 2024 (43 races); pooled +10.9 % worse, state-clustered bootstrap 90 % interval of the difference [+0.0014, +0.0031] (above zero).
  Blending with weights chosen on the other cycle does not help; roll growth as an extra regressor of the challenger's own turnout model
  does not help (its coefficient changes sign between cycles); the statewide level is worse (0.19-0.31 vs 0.11-0.13 absolute log error).
  The challenger's 2026 expected votes and `data_public/us2026_unified_priors.csv` are therefore **unchanged**.
- **House county × district pieces, roll-growth expected votes (same idea at block level): no** (TV 0.0147-0.0169 vs 0.0123 for the last
  presidential piece shares on 337 multi-piece districts of 2024).
- **House pieces in the redrawn states, a different use of L2 (where the voters live): yes, by a wide margin.** The registered House priors
  split a (county, 2024 district) piece over the 2026 districts by 2020 block *population* and give every sub-piece the parent's two-party
  share. Against the 2024 presidential vote by block (RDH results), population misallocates **6.3 %** of the votes in split pieces; the
  county piece's own L2 votes of 2020 (a file known before the 2024 election) misallocate **1.8 %** (7 of 7 states better, interval of the
  relative change [-77 %, -65 %]); the mean of the 2020 and 2022 shares 1.6 %. The Democratic share of sub-pieces departs from the parent's by a
  predictable amount (leave-one-state-out RMSE 4.9 → 2.5 points, 7 of 7 states better). At district level (95 districts of FL NC OH TX, block-results
  truth) the 2024 presidential two-party D share RMSE falls from **2.13 to 1.03 points** (state-out coefficients) and the expected two-party
  votes' mean absolute error from 2.4 % to 0.6 %.
  Shipped as `data_public/us2026_unified_house_priors_l2.csv` (+ `..._l2votes.csv`, votes only) for the 50 of the 75 configured House
  districts that are in redrawn states; the district forecast means / sds are kept. The implied move of the district means if the district
  presidential share is also taken from the L2 pieces is tabulated in `data_public/us2026_l2turnout_house_mu_shift.csv` (informational, not applied).

## 1. The bars (stated before the numbers they judge)

County component (recorded in the work log `local://progress_L2Turnout.md` before any out-of-sample number was computed): adopt only if, in the strict
out-of-sample variant, (1) mean TV lower than the challenger's in both 2022 and 2024, (2) pooled reduction ≥ 10 % with the state-clustered
bootstrap 90 % interval of the difference entirely below zero, (3) statewide level not worse by more than 0.02 and House pieces not worse.
*Result: fails (1), (2) and (3).*

House split of a 2024 piece over the 2026 lines (**written after an exploratory look** at the population-vs-L2 split error, which showed a
6.3 % vs ~1-3 % gap; the criteria below are therefore not blind, but the gap is large and no parameter was tuned on it): the key known before
the 2024 election (L2 votes 2020 of the 2023-02 file) must cut the misallocated share against block population by ≥ 50 %, be better in ≥ 5 of the
7 validation states, with a county-clustered bootstrap interval excluding 0; the Democratic-share shift must cut the leave-one-state-out RMSE by
≥ 25 % pooled and in ≥ 5 of 7 states. *Result: passes both (72 %, 7/7; 49 %, 7/7).*

## 2. Inputs and what is wrong with them

| file | content | snapshot | used for |
|---|---|---|---|
| `*_l2_turnout_2020blockAgg` (b20) | 2020 general, registered / voted by ethnicity, 2020 block | 2021-08 | county survivorship check only |
| `*_l2_2022stats_county` / `_2020block` (c22, k22) | 2020 and 2022 generals by ethnicity and gender; party and age of the roll | 2023-02 | rates 2020 / 2022, rolls 2020 / 2022, split keys, party shares |
| `*_l2_2024_gen_stats_2020county` / `_2020block` (c24, k24) | 2024 general by gender, age, party, ethnicity, ethnicity × party | 2024-12 | roll 2024, split key 2024, share shift |
| RDH block results 2020 / 2024 | presidential votes disaggregated to 2020 blocks | - | truth for the House pieces; official piece base |
| `raw/us/house/pieces.csv`, cd119 / cd120 block equivalency | county × district blocks and population | - | piece geometry, registered split key |

Data problems found and handled in `us_l2turnout.py`: the c22 files of ID, MA, MS, NH, WV lost the zero padding of the county FIPS ("161" for 16001;
repaired against the state FIPS); `TX_l2_turnout_2020blockAgg.zip` has a bad CRC-32 (read through `unzip -p`; county sums correlate 0.99995 with c22);
`VA_l2_turnout_2020blockAgg.zip` failed its CRC once and passed on re-test (the reader falls back to `unzip -p`); CA and LA have no 2024 block results
and WI names its fields `PREDEM24` / `PREREP24`; CT's L2 counties are not the planning regions of the 2022+ result files (excluded from the county tests).

Survivorship, measured on counties against the official two-party presidential votes of 2020 (1 963 counties):

| file | state-total ratio L2 voted / official | within-state sd of the county log ratio | vote-weighted sd |
|---|---|---|---|
| b20 (2021-08) | 0.994 | 0.098 | 0.053 |
| c22 (2023-02) | 0.954 | 0.030 | 0.032 |

b20 is too noisy for a base (the county ratio wanders by ±10 %); c22 is within 3 % at county level but keeps only 95 % of the 2020 ballots. Every county
model below therefore uses c22's own 2020 and 2022 columns (same snapshot) and the official presidential votes as the base.

## 3. County expected votes (statewide Senate / governor races)

Model (`us_l2turnout.model_ballots`): `ballots_c = reg_c × R × Π_k I_k(c)`, with `I_k(c) = Σ_g reg_g(c) r_g / (reg(c) R)` the composition index of dimension k
(ethnicity, gender; age and party exist only for 2024, so they cannot be validated out of sample), `r_g` the state's pooled 2020 group turnout rate, then
`V_c = ballots_c(target roll) × (official 2020 two-party presidential votes_c / ballots_c(2020 roll))^λ` (the county's own residual, shrunk by λ), scaled to a
state total (the challenger's V_state). Tested on the 108 statewide races of the held-out cycles with L2 counties covering ≥ 98 % of the race (CT excluded).
The challenger's turnout model is refitted on the other four cycles of 2016-2024 exactly as `us_unified_backtest.county_backtest` does; v1 = last same-office
votes (else president × 0.72 in midterms). Metric: total-variation distance between predicted and actual county share of the race's two-party votes (the challenger's
own turnout metric; ×2 is the vote-weighted mean absolute percentage error of county votes after scaling to the true state total).

Strict variant: 2022 target uses the 2022 roll of the 2023-02 file (the nearest available; it post-dates the election); 2024 uses the same file's 2022 roll, two
years stale, which mirrors 2026 (2024 roll, 2022 rates). "Fresh" uses the Dec-2024 roll for 2024 (an upper bound that leaks the election-day roll).

| design | cycle | races | L2 (eth+gen, λ=1) | challenger | v1 | last presidential | median county abs % error L2 / challenger | races L2 better |
|---|---|---|---|---|---|---|---|---|
| strict | 2022 | 65 | 0.0242 | 0.0214 | 0.0234 | 0.0235 | 5.17 % / 5.13 % | 18 |
| strict | 2024 | 43 | 0.0211 | 0.0197 | 0.0266 | 0.0222 | 3.49 % / 3.36 % | 16 |
| fresh 2024 roll | 2024 | 43 | 0.0204 | 0.0197 | 0.0266 | 0.0222 | 3.16 % / 3.36 % | 19 |

Paired difference (L2 − challenger), state-clustered bootstrap, 4 000 draws:

| design | races | n | mean difference | 90 % interval | relative |
|---|---|---|---|---|---|
| strict | pooled | 108 | +0.0022 | [+0.0014, +0.0031] | +10.9 % |
| strict | 2022 | 65 | +0.0028 | [+0.0017, +0.0039] | +13.0 % |
| strict | 2024 | 43 | +0.0014 | [+0.0002, +0.0027] | +7.3 % |
| fresh 2024 roll | pooled | 108 | +0.0020 | [+0.0010, +0.0029] | +9.5 % |
| fresh 2024 roll | 2024 | 43 | +0.0007 | [-0.0006, +0.0021] | +3.8 % |

The residual shrinkage matters (λ = 0 / 0.5 / 1: 0.034 / 0.028 / 0.024 in 2022, strict 2024 0.030 / 0.023 / 0.021; ethnicity and gender give the same numbers to the
fourth decimal once λ = 1 because the base residual carries almost everything). With λ = 1 the model is the last presidential result times the composition-adjusted roll growth,
and the growth carries no usable signal at county level.

Other tests:

| test | result |
|---|---|
| blend `(1-w) log V_ch + w log V_L2`, w chosen on the other cycle | 2022: w* = 0.4 → 0.0220 vs 0.0214; 2024: w* = 0 → 0.0197 (no change). In hindsight w = 0.4 helps 2024 (0.0187) and w = 0 is best for 2022. |
| roll growth `log(M_T/M_base)` added to the challenger's regression (fitted on the other cycle) | 2022 0.0216 vs 0.0213; 2024 0.0208 vs 0.0207 (stale) / 0.0214 (fresh); coefficient +0.51 fitted on 2024, -0.14 fitted on 2022 |
| L2 ethnic composition of the roll (Hispanic, Black, Asian shares of b20) as extra terms of the challenger's turnout regression, leave-one-cycle-out over all 2016-2024 | 0.0188 → 0.0184 (-2.1 %, interval of the difference [-0.0008, -0.0001]); by cycle 2016 0.0195 → 0.0181, 2018 0.0175 → 0.0184, 2020 0.0153 → 0.0149, 2022 0.0214 → 0.0201, 2024 0.0197 → 0.0196. Below the 10 % bar; the 2021 composition is not a pre-2016 observation either. |
| **diagnostic, not out of sample in time**: 2022 group drop-off 2020 → 2022 (ethnicity, gender) learned from the OTHER states' 2022 | 0.0200 vs challenger 0.0214 (-6 %, interval [-0.0032, +0.0004]); blended at w = 0.6 0.0194. It shows where an L2 gain could come from (the midterm electorate's group mix) but it uses the held-out cycle's own national pattern, so it is no evidence for 2026. |
| statewide level (abs log error of V_state; L2 = last presidential votes × roll growth × the challenger's race-type ratio) | 2022 L2 0.31 vs challenger 0.13 (v1 0.14); 2024 strict 0.19 / fresh 0.25 vs 0.11 (v1 0.16). The L2 roll growth overstates turnout growth (new registrants vote less). |

**Why it does not win.** The challenger's turnout share already carries the county's last presidential share plus the last same-office and House ratios and population
growth; the L2 additions are (a) roll growth, which is noisy across two snapshots, (b) the composition of the roll, which moves a county's expected share by ~0.1 % (the ACS
demographic terms had the same fate in `docs/us_unified.md`). A real midterm drop-off by group is the one thing L2 adds, and it cannot be tested on a held-out cycle with
two files.

### 2026 sensitivity (not adopted)

Production form of the component: expected ballots of a county = its 2022 L2 ballots × M(2024 roll) / M(2022 roll) under the state's 2022 group rates. Against
`data_public/us2026_unified_priors.csv` the county vote shares differ by a mean TV of 0.0145 per race (31 races; range 0.0076-0.0304, NY gov highest), which is the size of
the typical error of either model (0.020). It raises / cuts by more than 10 % the counties holding on average 1.6 % / 1.4 % of a race's votes (max 13 % in NM Senate, 12 % CA governor).

## 4. House pieces

### 4.1 Roll-growth expected votes on the 2024 lines (null)

3 531 county × district pieces of 48 states (CA and LA have no 2024 block results), 337 districts with ≥ 2 pieces; truth = 2024 presidential two-party votes by piece from the
RDH block results; baseline = 2020 presidential votes of the piece (what the registered method does with the latest president). Mean within-district TV of the piece split:

| model | mean TV | vote-weighted TV | difference to baseline (90 % interval, state-clustered) |
|---|---|---|---|
| last presidential piece votes | 0.0123 | 0.0121 | - |
| L2 roll 2022 (stale), λ = 0.5 | 0.0137 | 0.0134 | +0.0015 [+0.0003, +0.0028] |
| L2 roll 2024 (fresh), λ = 0.5 | 0.0132 | 0.0127 | +0.0010 [-0.0008, +0.0031] |
| L2 roll 2022 + own growth trend φ = 1 (strict), λ = 0.5 | 0.0147 | 0.0143 | +0.0024 [+0.0007, +0.0043] |
| L2 roll 2022 (stale), λ = 1 | 0.0147 | 0.0150 | +0.0025 [-0.0009, +0.0084] |
| L2 roll 2024 (fresh), λ = 1 | 0.0169 | 0.0162 | +0.0046 [-0.0015, +0.0135] |
| L2 composition only, roll 2024 | 0.0167 | 0.0162 | +0.0044 [+0.0027, +0.0064] |

For reference the L2 votes of 2024 themselves (a contemporaneous measure) sit at TV 0.0056 from the block-results truth and the L2 votes of 2020 at 0.0113 (vs 0.0123 for the official 2020
piece votes): L2's geocoding is good; it is the registration-roll growth that carries no signal.

### 4.2 Splitting a 2024 piece over the 2026 districts (redrawn states) — adopted as an option

Where the lines moved (AL CA FL LA NC OH TN TX UT) the registered priors split each (county, 2024 district) piece over the 2026 districts by block population
(`analysis/us_house_night.county_pieces`) and keep the parent's Democratic share in every sub-piece. Test set: the 364 sub-pieces of the 142 split pieces in the seven
states with 2024 block results (AL FL NC OH TN TX UT; 43 % of those states' votes). Error = Σ|predicted − actual sub-piece votes| / votes (actual: RDH block results, 2024 president).

| split key | known before 2024 | misallocated share of votes | relative to population | 90 % interval (county-clustered) |
|---|---|---|---|---|
| block population (registered) | yes | 6.25 % | 1 | - |
| block count | yes | 11.34 % | 1.81 | [0.56, 1.14] |
| L2 roll 2020 / 2022 (2023-02 file) | yes | 3.42 % / 3.35 % | 0.55 / 0.54 | [-0.52, -0.37] / [-0.51, -0.40] |
| L2 votes 2020 (2023-02 file) | yes | 1.77 % | 0.28 | [-0.77, -0.65] |
| L2 votes 2022 (2023-02 file) | yes | 2.12 % | 0.34 | [-0.70, -0.60] |
| mean of the 2020 and 2022 vote shares | yes | **1.58 %** | 0.25 | [-0.79, -0.69] |
| L2 roll 2024 (Dec-2024 file) | no (contemporaneous) | 3.14 % | 0.50 | [-0.54, -0.44] |
| L2 votes 2024 (Dec-2024 file) | no (contemporaneous) | 0.93 % | 0.15 | [-0.88, -0.82] |

By state (population → L2 votes 2020): AL 2.2 → 0.5 %, FL 4.9 → 1.6 %, NC 2.5 → 0.3 %, OH 5.9 → 0.7 %, TN 9.2 → 2.3 %, TX 7.0 → 2.0 %, UT 8.2 → 2.6 %. Votes, not registrants, are the key
(rolls misallocate 3.3-3.4 %). The production key is the mean of the 2022 and 2024 L2 vote shares (the analogue of the validated 2020 + 2022 mean).

Democratic share of a sub-piece minus its parent's (the registered method assumes 0; RMSE in points, vote-weighted, leave-one-state-out):

| roll used for the party and ethnicity shares | pooled | AL | FL | NC | OH | TN | TX | UT |
|---|---|---|---|---|---|---|---|---|
| no shift (registered) | 4.94 | 2.17 | 5.17 | 0.83 | 5.89 | 6.07 | 4.42 | 4.97 |
| 2023-02 roll (strict in time) | 2.54 | 1.39 | 1.71 | 0.54 | 2.53 | 3.01 | 3.07 | 2.01 |
| Dec-2024 roll (production) | 2.50 | 1.48 | 1.74 | 0.14 | 2.35 | 3.24 | 2.97 | 1.97 |

Coefficients on the within-piece-centred Democratic-minus-Republican registration share, Black, Hispanic and Asian shares of the roll (Dec-2024, all seven states): 0.56, -0.02, -0.38, 0.45
(`data_public/us_l2turnout_params.json`). The L2 party field is registered party where the state has it and modelled elsewhere (TX, AL, OH and TN have no registration by party).

District level (the check that matters for the priors): 95 districts of FL NC OH TX on the 2026 map, the 2024 presidential two-party Democratic share and votes from
(a) the registered pieces, (b) the L2 vote split, (c) the split and the shift with coefficients fitted on the other states, against the RDH block results aggregated onto the 2026 lines:

| | D share RMSE (points) | D share mean abs. error | votes mean / max abs. error |
|---|---|---|---|
| registered (population split) | 2.13 | 1.24 | 2.41 % / 13.6 % |
| L2 vote split | 2.06 | 1.22 | 0.58 % / 4.0 % |
| L2 vote split + shift (state-out) | **1.03** | 0.64 | 0.58 % / 4.0 % |

By state, D share RMSE registered → state-out: FL 2.96 → 0.80, NC 0.90 → 0.90 (no split piece moves), OH 0.87 → 0.57, TX 2.07 → 1.33. The truth is itself a block disaggregation of precinct
results (population-like inside a precinct), so it has its own error floor (~0.9 points even where the registered split is exact); the gain is understated if anything, and the registered split
is judged with its own 2024 piece votes from MEDSL precincts, not the RDH ones.

**The option priors.** `analysis/us_l2turnout.py priors` rebuilds the registered unified House prior (`s0 = μ + γ·(piece share − district share)`, `V = 0.789 ×` piece votes; reproduced from the
registered pieces to 0.05 votes before anything is changed) with the re-split pieces, keeping each district's registered μ and sd:

| file | what moves |
|---|---|
| `data_public/us2026_unified_house_priors_l2.csv` | piece votes (L2 split) and piece shares (L2 shift); the 25 untouched districts are byte-identical rows |
| `data_public/us2026_unified_house_priors_l2votes.csv` | piece votes only |

Effect on the 50 configured redrawn districts: 819 pieces re-split (none fell back to population); district 2024 presidential D share moves by 0.72 points on average (max 6.5; 18 districts by ≥ 0.5),
the district's expected votes by 2.1 % (max 13.6 %). Run it with `analysis/us_l2turnout.py export --snap <archive> --out <dir> [--votes-only]` (calls the registered `us_unified_replay.export` with the House
priors file swapped; configs and code untouched).

**Not applied: the district means.** μ of a redrawn district is built from the district's presidential share (the crosswalk of the registered pieces). If it is taken from the L2 pieces the registered linear
rule implies Δμ = w·b_Pc·ΔD2 (w = weight on the fundamentals, 1 without polls; b_Pc from `data_public/us_unified_house_params.json`; the House-core term is left alone): mean |Δμ| 0.39 points,
max 1.9 (FL-22 −1.9, FL-15 +1.9, TX-38 −1.8, TX-10 −1.7, FL-25 −1.6, FL-26 −1.5); 15 districts move by ≥ 0.5 point, none changes side of 0.5; the sum of P(D) over the 50 falls by 0.61 seats (max 0.09
in one district; registered sd including the redrawn 0.02 kept). Table: `data_public/us2026_l2turnout_house_mu_shift.csv`. Whether to carry this into the district means (and shrink the 0.02 redrawn sd) is a
call for the integrator; the coefficients behind it are those of the registered model and the piece shift was fitted on seven states.

## 5. Limits

- Two held-out cycles for the county test (2022, 2024), one design per cycle; the strict 2022 test uses a roll that post-dates the election. Midterm drop-off by group (the one place L2 should add something
  for 2026) is not testable on a held-out cycle with two files; the leave-one-state-out diagnostic (-6 %) is not evidence.
- The roll-growth tests mix snapshots (2023-02, 2024-12); county FIPS and a corrupt TX file are patched; CT and Alaska are out.
- The House split validation is on 2024 presidential votes (truth: disaggregated precinct data), for seven states, with Texas and Florida carrying most pieces; CA and LA are not validated
  (no 2024 block results) but are covered by the same code and fall under the same states' coefficients. The option applies to FL NC OH TX only because only those redrawn states have a configured feed.
- The share-shift coefficients (party registration is partly modelled by L2) were fitted on the seven validation states and then applied to the same four states of the option; the district check above uses
  state-out coefficients, the shipped file uses all-state ones.
- The split key is the 2022 + 2024 electorates' locations; the 2026 midterm electorate will differ, and no 2026 outcome was used.

## 6. Rebuild

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python -W ignore analysis/us_l2turnout.py build     # SSD frames: processed/us2026/l2turnout/{county_b20,county_c22,county_c24,pieces_l2}.parquet
uv run python -W ignore analysis/us_l2turnout.py county    # section 3
uv run python -W ignore analysis/us_l2turnout.py house     # 4.1
uv run python -W ignore analysis/us_l2turnout.py split     # 4.2 (split and shift tables)
uv run python -W ignore analysis/us_l2turnout.py dcheck    # 4.2 district check
uv run python -W ignore analysis/us_l2turnout.py priors    # option priors + params + district shift table
uv run python -W ignore analysis/us_l2turnout.py sens      # 2026 sensitivity
uv run python -m pytest -q tests/test_us_l2turnout.py tests/test_us_unified.py
```
