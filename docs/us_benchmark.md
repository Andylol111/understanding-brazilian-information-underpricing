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

Reconstruction notes and limits: see section 1.
