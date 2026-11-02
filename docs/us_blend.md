# US blends and corrections: can the models' weaknesses be made complementary? (written and committed BEFORE any blend number was computed)

## 0. Pass bars (fixed now; nothing below was computed when this section was committed)

What was seen before this section: the parent models' own numbers (`docs/us_benchmark.md`: v3 pooled CRPS 2.286, unified 2.115; House
v2 / unified in `docs/us_unified.md`; the Georgia simulated count tables; the internal real-night verdicts in words). No blend, no
level correction, no band multiplier and no House v1-count configuration has been evaluated.

**Common rules (every component).**

1. **Out of sample only.** Every evaluated prediction is leave-one-cycle-out (LOCO). A fitted number is fitted on the other cycles' LOCO
   predictions, never on the held-out cycle. At most **one fitted weight / multiplier / shrink per blend or correction**
   (equal-weight blends fit nothing). Parents are the predictions already in `data_public/us_benchmark_statewide_races.csv` and the
   House / county LOCO panels of `analysis/us_benchmark.py`; nothing in them is refitted or changed.
2. **Registered models must not change.** Blends are new configs under `configs/us2026_blend/`, not registered by this work.
3. **Blend operator (moment mixing).** For parents (mu1, sd1), (mu2, sd2) and weight w on parent 1:
   mu = w mu1 + (1-w) mu2, sd^2 = w sd1^2 + (1-w) sd2^2 + w(1-w)(mu1-mu2)^2; the blend is scored as Normal(mu, sd). The LOCO weight is
   chosen on the grid {0, 0.1, ..., 1} to minimise mean CRPS on the training cycles. County shares: s = w s1 + (1-w) s2 (county
   expected votes are not blended; parents' counts are unchanged).
4. **Tests.** Paired, per race (or district), on the same metric as `docs/us_benchmark.md` section 0.5 (statewide: CRPS of the share
   forecast; county: race-mean vote-weighted county share MAE; House: CRPS of the district share forecast); improvement = parent
   minus blend, positive = blend better. 20,000 bootstrap resamples of races within cycle (`us_benchmark.boot_delta`, seed fixed in
   the script), two-sided p = 2 min(P(d<=0), P(d>=0)), 95 % interval unadjusted.
5. **Multiplicity: one Holm family over EVERY test run in this document**, all six components, every class, every candidate,
   losers included. Each component writes its raw tests to `data_public/us_blend_tests_<component>.csv` with the columns
   `component, test, class, n, mean_delta, lo, hi, p, cycles_pos, cycles_eval`; `analysis/us_blend_holm.py` concatenates all of them,
   applies Holm (and also reports the family size) into `data_public/us_blend_tests.csv`. A test counts as passed only when its Holm-adjusted
   p < 0.05 AND its 95 % interval excludes zero on the held-out cycles.
6. **Cycle consistency.** The gain must have the same sign in at least **4 of 5 statewide cycles** (2016-2024) or **5 of 6 House cycles**
   (2014-2024). County-share blends use the statewide rule (5 cycles). Level corrections: see 5.4. Count-stage: see 5.5. A component with fewer than
   30 races (or districts) in the tested class is "not tested".
7. **A blend passes only if** (a) it beats **both** parents (v3 and unified statewide / county; v2 and unified House) in the **pooled** test, each passing
   rules 5 and 6; (b) pooled Brier of the win probability is not worse than the primary's (v3, House v2) and pooled 90 % coverage >= 0.85;
   (c) the weight is the equal weight or one LOCO weight (rule 1). Volatility-tier results (C1 low / medium / high; House H1
   polled / unpolled) and per-cycle results are reported and their tests are in the Holm family, but a blend is never
   promoted for one tier only (that would be a second fitted choice).
8. If a blend does not pass, the null is documented with the numbers (not hidden) and no config is shipped for it. A passing blend ships as
   a config set + builder (rule 2); the integrator decides whether to register it.
9. **Primary baselines.** Statewide: v3 with the LOCO-refit sd (the rule of `docs/us_benchmark.md` 0.4); House: v2 with the LOCO-refit sd.

## 5. Component bars (what each one tests, fixed now)

### 5.1 Statewide pre-count blend (v3 + unified), 264 races, 5 cycles
Tests: {equal weight, LOCO weight} x {vs v3, vs unified} x {pooled, C1 low, C1 medium, C1 high} = 16 tests, all in the Holm family. Pass: rule 7.

### 5.2 County share blend (v3 + unified county shares)
Tests: same 16 on race-mean vote-weighted county share MAE (5 cycles, races with county returns). Pass: rule 7(a), (c) (no win probability at county level, so 7(b) does not apply).

### 5.3 House pre-count blend (v2 + unified), 2,223 district-cycles, 6 cycles
Tests: {equal, LOCO} x {vs v2, vs unified} x {pooled, H1 polled, H1 unpolled} = 12 tests. Pass: rule 7 with the 5-of-6 cycle rule.

### 5.4 Turnout LEVEL correction (statewide expected votes; the county pattern is untouched)
Corrected level V' = V x exp(lambda x z), where V is the parent's statewide expected two-party vote (parents: the registered v1 level rule and the
unified `predict_vstate` level), z is a log-ratio signal known before the election, and lambda in [0, 1] is the single fitted shrink
(grid step 0.05, fitted LOCO over midterm cycles by minimising mean |log(V'/V_actual)| on training cycles). Signals, all fixed now, each its own
test: **S1 early-vote pace** = log(cumulative early/mail ballots returned by the same days-before-election this cycle / the previous midterm's),
only where the early-vote history exists (sources: `$BRUNDER_DATA/us_ev/ev.sqlite`, `ev_hist_*.parquet`); **S2 registration growth** = log(active registered
voters now / at the same point before the previous midterm); **S3 generic-ballot enthusiasm** = standardized national indicator from pre-election
inputs available to each cycle (generic-ballot margin magnitude and/or enthusiasm series already in the repo; if no such series exists for a cycle the signal is
"not available" for it, not imputed). A signal is dropped as "not evaluable" if fewer than 2 midterm cycles have it.
Target cycles: 2022, and 2018 where S1 history exists. Metric per race: |log level error| (relative level error), before vs after. Pass: improvement in
mean |level error| with 95 % interval excluding zero (bootstrap over races within cycle), Holm p < 0.05 in the global family, **and the gain positive in every
held-out midterm cycle that can be evaluated (at least 2 required; with one evaluable cycle the correction is reported as "not demonstrable" and not shipped)**. Applied
to both parents' levels (2 baselines x up to 3 signals = up to 6 tests). Level error before/after is reported pooled and per cycle (signed and absolute).

### 5.5 Count-stage band widening (one multiplier m on the count-stage sd)
m multiplies the sd of the projected final two-party share (the count-stage predictive band) at each checkpoint; one fitted m. Fitted on 2024 real nights and
tested on 2020 real nights, and vice versa (2 folds; the real-night sets of `analysis/us_real_nights.py` / `us_benchmark_count.py real`, NYT/Edison-derived: internal only,
only the verdict is published). Checkpoints 10, 25, 50, 75 % of the final vote counted; m fitted by minimising the mean Normal CRPS of the projected final share
(Gaussian from the projection's own mean and sd) on the training year; evaluation metric = same CRPS on the held-out year (paired per night-checkpoint, bootstrap over nights; nights are not
independent, so intervals are optimistic and stated as such). Pass: both folds have held-out improvement > 0 with interval excluding zero, both Holm p < 0.05 in the global family, and the held-out
90 % coverage moves toward 0.90 without exceeding 0.96. The fitted m (full-sample value reported as secondary) is then applied to the Georgia simulated count nights (official certified results
by vote type, publishable) and their before/after coverage and CRPS published. If either fold fails, the null is the verdict.

### 5.6 House v2 starting estimate with v1 count settings vs registered v2 (Georgia-style simulated House nights only)
Georgia House districts of 2022 and 2024 certified by vote type, the simulated count generator of `analysis/us_calibrate.py`, 200 draws per district, paired. Candidate = registered v2's
starting estimate (prior mean / sd) with the count settings (turnout learning, early-share, f_complete etc.) of the registered House v1; comparator = registered v2 unchanged. Metric = mean absolute
margin error at checkpoints 10, 25, 50, 75 % counted (pooled over checkpoints, per-checkpoint reported) and 90 % coverage. Pass: pooled improvement > 0 with the draw-bootstrap interval excluding zero, Holm p < 0.05
in the global family, and the gain positive in both 2022 and 2024 and in at least two thirds of districts. **Limitation (stated whatever the result):** simulated count orders on one state's
districts, draws within a district are not independent; even a pass cannot show a real-night effect and is labelled "simulation-only".

## 6. Everything else
Seeds: bootstrap seed 20261103; each script prints its test rows and writes only aggregates. Real-night numbers (NYT/Edison) stay under `$BRUNDER_DATA/derived/us_blend/`. Published L2 / RDH-derived
analyses carry: "This analysis was conducted using data from the Redistricting Data Hub."

---
# Results (written after the bars above were committed in 72deed5; the bars were not changed)

Global Holm family: **49 tests** (every row with a p-value in `data_public/us_blend_tests_*.csv`, losers included; merged by `analysis/us_blend_holm.py` into `data_public/us_blend_tests.csv`). "Delta" = parent minus blend (positive = blend better). Weights: LOCO weight on the first parent (v3 / v2 / v3 county) was 0.1-0.3 in every held-out cycle.

## R1. Statewide pre-count blend (v3 + unified): FAIL (null versus unified)
| model | CRPS pts | MAE | Brier | cov90 |
|---|---|---|---|---|
| v3 (LOCO sd) | 2.286 | 3.107 | 0.0593 | 0.879 |
| unified | 2.115 | 2.821 | 0.0549 | 0.886 |
| blend, equal weight | 2.108 | 2.812 | 0.0545 | 0.898 |
| blend, LOCO weight | 2.091 | 2.783 | 0.0543 | 0.902 |

Versus v3: equal +0.178 [0.083, 0.277] (Holm p 0.010, 4/5 cycles); LOCO +0.195 [0.037, 0.345] (Holm p 0.45, 4/5). Versus unified: equal +0.007 [-0.104, 0.142]; LOCO +0.024 [-0.031, 0.092] (Holm p 1). By tier (equal weight vs v3): low +0.066 (5/5), medium +0.249 (5/5), high +0.249 [-0.084, 0.592]; versus unified the blend is slightly worse in low and medium and positive but not significant in high. Per cycle the blend sits between or at the better parent; in 2020 (unified 2.664, v3 3.252) equal weight 2.896. **The blend adds nothing over unified**: it only reproduces most of unified's gain over v3. Bar 7(a) fails; no config.

## R2. County share blend (v3 + unified): FAIL (null versus unified)
Race-mean vote-weighted county share MAE (260 races): v3 3.672, unified 3.185, equal 3.239, LOCO 3.176. Versus v3 both blends are significant (equal +0.433 [0.277, 0.579], 5/5; LOCO +0.495 [0.242, 0.721], 5/5, Holm p 0.0068). Versus unified: equal -0.055 [-0.197, +0.100]; LOCO +0.008 [-0.050, +0.065] (4/5 cycles, Holm p 1). Fail.

## R3. House pre-count blend (v2 + unified), 2,223 district-cycles: FAIL (null versus unified)
CRPS: v2 2.617, unified 2.300, equal 2.314, LOCO 2.294 (cov90 0.900 / 0.900 / 0.927 / 0.922). Versus v2 both blends win in 6/6 cycles (equal +0.303 [0.270, 0.338], LOCO +0.322 [0.271, 0.376]). Versus unified pooled: equal -0.014 [-0.048, +0.020] (4/6); LOCO +0.005 [-0.010, +0.021] (4/6). The only significant versus-unified result is the LOCO blend in the 319 polled districts (+0.142 [0.089, 0.198], Holm p 0.002, 3/4 cycles), but tier-only promotion is forbidden by bar 7 (it would be a second fitted choice), and equal weight in unpolled districts is significantly worse than unified (-0.043 [-0.076, -0.010]). Fail.

## R4. Turnout level correction: FAIL (not demonstrable / worse)
130 races (2018: 64, 2022: 66). S1 (early-vote pace) and S2 (registration growth) have one midterm cycle with the needed previous-midterm history (2014 data absent), so no held-out evaluation is possible: "not evaluable", not shipped. S3 (generic-ballot enthusiasm) is evaluable and hurts: v1 |log level error| 0.157 -> 0.274 (gain -0.117 [-0.132, -0.100], 0/2 cycles), unified 0.166 -> 0.180 (gain -0.015 [-0.038, +0.010], 1/2 cycles). Signed level error, unified: 2018 -0.203 -> -0.148, 2022 +0.113 -> -0.201. With two midterm cycles the shrink is not identifiable (the one training cycle gives lambda 0.35 with the wrong sign for the other). Details: `data_public/us_blend_turnout_levels.csv`. This analysis was conducted using data from the Redistricting Data Hub.

## R5. Count-band widening: FAIL (null)
Real nights (internal; verdict only): fitted on 2024 and tested on 2020, and vice versa, the held-out CRPS got worse in both folds with the interval on the wrong side of zero (Holm p 0.495 and 0.013 for the "improvement" two-sided tests, both negative); the two years need opposite corrections (m 0.91 vs 1.96). Georgia simulated nights (official results; 3 scopes x 200 draws, 2,400 draw-checkpoints): the registered v3 band has 90 % coverage 0.948 and CRPS 0.837 share points; m = 0.911 gives 0.896 / 0.829, m = 1.211 (full-sample fit) 0.999 / 0.870, m = 1.963 1.000 / 1.077. The registered configs cannot express an sd multiplier (no setting; `share_sd` in `brunder.us.project`). Nothing to ship. Caveat: few nights, dependent checkpoints.

## R6. House v2 start + v1 count settings, Georgia simulated House nights: PASS (simulation-only)
14 districts x 2 cycles x 200 paired draws. Mean absolute margin error pooled over the 10/25/50/75 % checkpoints: registered v2 4.796 pts, candidate 4.237; gain +0.559 [0.538, 0.580] (draw bootstrap; Holm p 0.0025 in the 49-test family); district-cluster interval [0.313, 0.809]. 2022 +1.036, 2024 +0.082 (both positive, 2/2); 13/14 districts positive on their two-cycle mean, 21/28 district-cycles, 2024 alone 9/14 (0.64). Pooled 90 % coverage 0.838 -> 0.912. By checkpoint: +0.08, +0.42, +0.46, +1.28 (10/25/50/75 %). **Limitations:** Georgia only, simulated count orders, one state's 14 districts, draws within a district are not independent (the draw interval is optimistic), the gain is almost all 2022 (2024 is a near tie, slightly negative at 50/75 %), and the 2022 House starting estimate had to be constructed by splitting 2020 county president totals over redrawn 2022 districts (start errors up to 12-14 pts in GA-06/GA-13). It is not evidence for a real night or for another state. The change: in `[count]`, `vote_type = true` and `early_share = 0.64` (v1's values); `[prior]`/`[model]` unchanged.

## Ready-to-register set
`configs/us2026_blend/ga-{01,03,07,12}_v2c.toml` (the four GA House v2 configs with those two lines changed, id suffixed `v2c`), built by `analysis/us_blend_build_configs.py`. **Not registered.** Everything else failed its bar and ships nothing.

## Overall
The models' weaknesses are not complementary in a way that a fixed blend can exploit: unified already captures what v3/v2 contribute at the pre-count stage (blends only recover the gain over v3/v2 and never beat unified, at statewide, county or House level), the level correction cannot be identified from two midterm cycles, and one count-band multiplier does not transfer between 2020 and 2024. The one pass is a count-setting change on Georgia House nights, simulation-only.
