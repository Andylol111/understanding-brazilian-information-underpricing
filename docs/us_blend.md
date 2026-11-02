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
