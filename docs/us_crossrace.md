# Cross-race information: does one race's live county count improve another race's projection on the same ballot?

**Status: a sensitivity study, not part of the frozen unified challenger.** Nothing registered or frozen changed: `src/brunder/us_unified.py`,
`analysis/us_unified_replay.py`, the configs and the registered code are exactly as before. The joint update that was tested lives in its own
module (`src/brunder/us_unified_xrace.py`, tests `tests/test_us_unified_xrace.py`) and is **not** used by any config or runner, because it
did not clear the bar set for adding it (section 5). Rebuild every number:

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python -W ignore analysis/us_crossrace.py panel                 # leave-one-cycle-out county priors of every Senate / governor race 2016-2024 (3 s)
uv run python -W ignore analysis/us_crossrace.py corr                  # swing correlations between races (section 1)
uv run python -W ignore analysis/us_crossrace.py replay --n 12         # Senate x governor count nights, separate vs joint (~30 min on 10 cores)
uv run python -W ignore analysis/us_crossrace.py tables replay         # sections 3-4 tables;  `cycles replay` = per-cycle bar table
uv run python -W ignore analysis/us_crossrace_house.py panel           # whole-county House districts
uv run python -W ignore analysis/us_crossrace_house.py corr
uv run python -W ignore analysis/us_crossrace_house.py replay --n 8    # statewide race x House district (~25 min)
uv run python -W ignore analysis/us_crossrace_house.py tables
uv run python -W ignore analysis/us_crossrace.py replay --n 6 --registered --name replay_registered   # robustness: registered tau / sigma_mun0
```

Outputs: `results/us_crossrace/*.parquet|csv` (gitignored; every table below is reproduced from them).

## The plain answer

- **The two races are strongly related at the county level and weakly at the state level.** Senate x governor in the same state-cycle (60-64
  pairs, 2016-2024): the counties' deviations from their own statewide swing correlate at **0.71** (vote-weighted, from the unified county prior;
  0.74 from the last presidential result; by cycle 0.60-0.83); the statewide swings correlate at only 0.46 in share points (0.28 from the presidential
  result; by cycle from -0.7 to +0.8, 4-26 pairs each) or 0.65 in standardised units (error / forecast sd).
  House districts made of whole counties against the statewide race of the same ballot: county correlation 0.46 (0.57 in 2018, **0.01 in 2022**), statewide-level correlation 0.17.
- **That does not translate into a better live projection when the two races report together.** With a common reporting order (county by county, both races at once; the
  case for Ohio's Acton / Brown), the joint model is indistinguishable from two separate models: mean absolute error of the final share at <= 50 % counted
  **+0.3 %** (+0.003 points, 95 % interval -0.002 .. +0.007), 90 % coverage and minutes to 90 % on the winner unchanged (-0.07 minutes), and slightly worse in 2018 (+1.1 %, interval excludes zero).
  The reason is simple: when both races are counted in the same counties, the second race's count contains no county that the first has not already shown; its
  sampling error is the first race's (0.7 correlated), and the cross-race statewide information is a few percent of the information in the race's own count.
- **Gains appear only when the races report on different schedules, and are small.** Independent county order for the two races: -1.3 % MAE and -0.7 minutes; the
  second race 30-150 minutes behind the first in every county ("lag", e.g. down-ballot races counted later): **-2.1 % MAE** (95 % interval -3.0 .. -0.7 %), -0.3 minutes (interval includes zero);
  the gain is larger late (-9 % to -12 % at 50-70 % counted, 0.03-0.04 points). Using only the county-level coupling (no statewide coupling) is the better and safer variant.
  For House districts behind a statewide race the effect is within +-0.7 % either way (worse in 2022).
- **Bar for adding it to the frozen set: not met** (section 5): no clear out-of-sample gain in accuracy AND minutes-to-90 in every cycle, none at all in the simultaneous case.
  Documented as a sensitivity; the unified challenger is unchanged.

## 1. Swing correlations between races (2016-2024; 2014 county results are not in the panel)

Swing from the prior: s2 - s0, the result minus the unified county prior of that race as it would have been published before the count (leave-one-cycle-out county priors of
`analysis/us_unified_backtest.py`: predicted statewide share, predicted county shares and votes). Statewide swing = vote-weighted mean of the counties' swing; county deviation =
swing minus the race's statewide swing. Panel: `analysis/us_crossrace.py panel` (Senate and governor races with county results; 60-64 same-state pairs, specials and regular races paired separately).

| quantity (Senate x governor) | all cycles | 2016 | 2018 | 2020 | 2022 | 2024 |
|---|---|---|---|---|---|---|
| pairs | 64 | 9 | 18 | 4 | 26 | 7 |
| corr of the statewide swings, from the county prior (share points) | 0.46 | 0.83 | 0.15 | 0.79 | 0.26 | -0.08 |
| corr of the statewide swings, from the last presidential result | 0.28 | 0.69 | 0.58 | -0.74 | -0.06 | -0.02 |
| median county-deviation correlation per pair, county prior | 0.75 | 0.78 | 0.74 | 0.34 | 0.75 | 0.52 |
| pooled vote-weighted county-deviation correlation, last presidential result | 0.74 | 0.83 | 0.79 | 0.63 | 0.66 | 0.60 |
| sd of the county deviation (points), Senate / governor | 1.9 / 2.1 | 2.5 / 3.0 | 2.4 / 2.3 | 1.5 / 1.8 | 1.4 / 1.7 | 1.5 / 2.0 |

Estimates used by the joint model (`estimate_crossrace`, fitted on the cycles other than the held-out one; all five cycles: rho_s 0.65 on standardised errors z = (S_act - S_hat) / sd,
rho_e 0.705, slopes: governor deviation = 0.80 x Senate deviation, Senate = 0.62 x governor; held-out values for 2016 / 2018 / 2020 / 2022 / 2024: rho_s 0.58 / 0.65 / 0.65 / 0.71 / 0.66, rho_e 0.68 / 0.71 / 0.71 / 0.71 / 0.71).
The statewide correlation is estimated from 39-59 pairs and moves by cycle (table), the county correlation is stable but not high enough to double the information of one race by watching the other (R^2 = 0.5 on one county).

House districts of whole counties (every county touched lies >= 90 % in the district; 78 contested districts 2016-2024, pairs with a Senate or governor race of the same ballot; the House prior is the district's last presidential
lean plus the generic-ballot swing, no district model, so deviations include prior error):

| cycle | partner | pairs | counties | statewide-level corr of errors | county-deviation corr | House slope on state deviation |
|---|---|---|---|---|---|---|
| all | Senate | 58 | 1173 | 0.37 | 0.48 | 0.51 |
| all | governor | 46 | 913 | -0.07 | 0.45 | 0.46 |
| 2018 | both | 24 | 592 | 0.17 | 0.57 | 0.55 |
| 2022 | both | 25 | 325 | 0.02 | 0.01 | 0.01 |

## 2. The models compared

**Separate**: each race through `brunder.us.project` (the registered count model) with its own prior (unified county prior s0, V; swing prior N(0, statewide sd); count rules of the registered civic v1:
f_complete 0.98, learn_turnout, count_scaled_terms; rho 0.3).

**Joint** (`brunder.us_unified_xrace.joint_project`): a county's swing = common county swing + race-specific deviation. Two couplings, each switchable:

1. *Statewide.* The two statewide swings (delta_A, delta_B) have a bivariate normal prior with correlation rho_s; each race's count gives an observation of its own swing (the nowcast's swing in counted
   votes, with its own sampling variance) with correlated sampling errors (correlation rho_n x the share of counted votes in counties counted in both: the same counties being counted makes the errors correlated).
   The joint posterior of each swing is passed back to the unchanged nowcast as the effective swing prior that, combined with the race's own observation, reproduces it.
2. *County.* A county not yet counted in race A but counted in race B has its prior share moved by beta x (B's county deviation from B's posterior statewide swing), beta = the regression slope estimated on past cycles.

Variants: `joint` (both, rho_n = rho_e), `joint_n` (rho_n estimated from simulated nights = max(that, rho_e); identical to `joint` to 3 decimals), `county_only` (statewide coupling off), `state_only_n` (county coupling off).
Parameters of the held-out cycle are never used: for each held-out cycle they are estimated on the other four.

**Replay**: for each of the 60 same-state Senate x governor pairs (+ 104 statewide-race x House-district pairs) simulated count nights: each county posts its whole result at once; lateness rises with county size (rank correlation 0.5, as the size-dependent order of
`analysis/us_calibrate.py`), 20-420 minutes after close. Scenarios: **same** (both races post a county together; Ohio's case), **indep** (each race has its own random order),
**lag** (race B posts each county 30-150 minutes after race A). Checkpoints at 2, 5, 10, 20, 30, 40, 50, 70, 90 % of the evaluated race's two-party vote; 12 nights (8 for House) x 3 scenarios per pair, both races evaluated; truth = the certified two-party result of the counties of both races.
Width: the registered tau / sigma_mun0 describe real feeds (partial-county order bias) and give 90 % intervals that cover 99.7 % here (counties post complete), which would make minutes-to-90 meaningless; they are set to tau 0.005, sigma_mun0 0.02 so the
*separate* model's 90 % coverage is ~0.90 (pilot on 22 state-cycles; 0.95 overall in the final run). The registered settings are the robustness run (section 6). Prior errors are the leave-one-cycle-out ones (real), the count order is simulated, not observed:
the replay has no within-county early / Election Day bias, which is common to both races anyway and carries no cross-race information in the simultaneous case.

## 3. Results: Senate x governor (60 state-cycles, held-out parameters)

Mean absolute error of the final two-party share in points, mean over race-cycles and nights, by share of the race's vote counted (model `separate` / `joint` / `county_only`):

| scenario | 5 % | 10 % | 20 % | 50 % | 70 % | 90 % |
|---|---|---|---|---|---|---|
| same | 1.209 / 1.209 / 1.209 | 1.012 / 1.014 / 1.012 | 0.810 / 0.813 / 0.810 | 0.462 / 0.464 / 0.462 | 0.271 / 0.271 / 0.271 | 0.064 / 0.065 / 0.064 |
| lag | 1.248 / 1.240 / 1.240 | 1.043 / 1.036 / 1.032 | 0.823 / 0.812 / 0.805 | 0.481 / 0.438 / 0.438 | 0.266 / 0.235 / 0.237 | 0.067 / 0.059 / 0.059 |

Paired differences to the separate model (negative = better), mean over race-cycles of the per-race-cycle mean difference, 95 % bootstrap interval over race-cycles (cluster = state-cycle):

| variant | scenario | cycles | checkpoints <= 50 % counted: d MAE (points) | 95 % interval | relative | d Brier |
|---|---|---|---|---|---|---|
| joint | same | all (120 race-cycles) | +0.003 | -0.002 .. +0.007 | +0.3 % | 0.000 |
| joint | same | 2018 | +0.010 | +0.003 .. +0.019 | +1.1 % | 0.000 |
| joint | same | 2022 | +0.001 | -0.005 .. +0.008 | +0.1 % | 0.000 |
| joint | indep | all | -0.012 | -0.026 .. +0.004 | -1.3 % | -0.001 |
| joint | lag | all | -0.019 | -0.030 .. -0.007 | -2.1 % | -0.001 |
| joint | lag | 2018 | -0.030 | -0.052 .. -0.011 | -3.1 % | -0.001 |
| joint | lag | 2022 | -0.010 | -0.027 .. +0.009 | -1.3 % | -0.001 |
| county_only | same | all | 0.000 | 0.000 .. 0.000 | 0 % | 0 |
| county_only | indep | all | -0.013 | -0.018 .. -0.008 | -1.5 % | -0.001 |
| county_only | lag | all | -0.021 | -0.028 .. -0.013 | -2.3 % | -0.001 |
| county_only | lag | 2018 / 2022 | -0.031 / -0.010 | -0.048 .. -0.015 / -0.019 .. -0.001 | -3.2 % / -1.4 % | -0.001 |
| state_only_n | same / indep / lag | all | +0.003 / -0.001 / +0.001 | all include 0 | | |

90 % coverage of the final share (all checkpoints): separate 0.954 / 0.952 / 0.945 (same / indep / lag), joint 0.953 / 0.960 / 0.952, county_only 0.954 / 0.961 / 0.952: not worse anywhere.

Minutes until P(true winner) >= 0.9 and stays so (per night and race; median over nights; share of nights decided in brackets): all races same 64.0 (0.987) for all variants; lag 118.0 (0.988) separate vs 117.7 (0.987) joint;
close races (|final - 0.5| < 5 points; 38 race-cycles) same 149.2 / 149.3, indep 133.9 / 128.4 (joint), lag 214.4 / 206.6 (joint). Paired mean change of the minutes, nights decided in both: joint same -0.07 (-0.5 .. +0.2); indep -0.7 (-1.4 .. -0.06), close -2.3 (-4.3 .. -0.4); lag -0.3 (-1.2 .. +0.6), close -1.0 (-3.7 .. +1.9).
(Absolute minutes depend on the simulated order and width; the differences are what matters. Median minutes to 90 % are mostly the first checkpoint for safe races, so a few minutes on close races is the only place a difference could show.)

### By held-out cycle, on the bar (MAE <= 50 % counted, coverage, minutes to 90 %; `pass` = better MAE and fewer minutes and coverage not lower by more than half a point)

| cycle | scenario | race-cycles | joint: d MAE (points) | d cover90 | d minutes | pass | county_only: d MAE | d cover90 | d minutes | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 2016 | same | 8 | -0.007 | -0.002 | -0.57 | yes | -0.000 | +0.000 | +0.00 | no |
| 2016 | indep | 8 | -0.022 | +0.014 | -3.64 | yes | -0.021 | +0.012 | -0.62 | yes |
| 2016 | lag | 8 | -0.042 | +0.017 | -3.21 | yes | -0.036 | +0.013 | -2.23 | yes |
| 2018 | same | 17 | +0.010 | -0.002 | -0.06 | no | -0.000 | +0.000 | +0.00 | no |
| 2018 | indep | 17 | -0.018 | +0.013 | -0.24 | yes | -0.021 | +0.015 | -0.30 | yes |
| 2018 | lag | 17 | -0.030 | +0.010 | +0.49 | no | -0.031 | +0.011 | +0.68 | no |
| 2020 | same | 4 | -0.002 | +0.001 | +0.00 | no | +0.000 | +0.000 | +0.00 | no |
| 2020 | indep | 4 | +0.047 | -0.001 | -1.08 | no | +0.009 | -0.001 | +0.00 | no |
| 2020 | lag | 4 | +0.021 | -0.005 | -2.36 | no | -0.001 | -0.002 | -0.64 | yes |
| 2022 | same | 24 | +0.001 | -0.001 | +0.04 | no | -0.000 | +0.000 | +0.00 | no |
| 2022 | indep | 24 | -0.013 | +0.003 | -0.17 | yes | -0.007 | +0.004 | +0.10 | no |
| 2022 | lag | 24 | -0.010 | +0.002 | +0.26 | no | -0.010 | +0.002 | +0.56 | no |
| 2024 | same | 7 | +0.002 | +0.000 | +0.00 | no | -0.000 | +0.000 | +0.00 | no |
| 2024 | indep | 7 | -0.015 | +0.014 | +0.00 | no | -0.019 | +0.012 | +0.00 | no |
| 2024 | lag | 7 | -0.017 | +0.007 | +0.00 | no | -0.022 | +0.008 | +0.00 | no |

(Full table with all variants: `results/us_crossrace/replay_cycle_variants.csv`. 2020 has 4 pairs and 2024 has 7, so their rows are noisy. `pass` is strict: a tie in the minutes (nothing changed) fails.)
Chosen leave-one-cycle-out (the variant with the best paired MAE on the other cycles, else separate), pooled over the three schedules: `county_only` in every held-out cycle; held-out d MAE <= 50 % counted -0.019 (2016), -0.017 (2018), +0.003 (2020), -0.006 (2022), -0.014 (2024) points.

## 4. Results: statewide race x House district (whole-county districts, 104 race pairs, 2016-2024)

The House race lags the statewide race by 30-150 minutes per county ("lag"), is counted independently ("indep") or together ("same"). Paired MAE difference at <= 50 % counted, House race:

| variant | scenario | all cycles (104 race-cycles) | 95 % interval | 2018 | 2022 |
|---|---|---|---|---|---|
| joint | same | -0.007 (-0.6 %) | -0.013 .. -0.001 | -0.015 | +0.003 |
| joint | indep | +0.003 (+0.2 %) | -0.005 .. +0.010 | -0.005 | -0.001 |
| joint | lag | -0.007 (-0.6 %) | -0.020 .. +0.007 | -0.008 | +0.010 |
| county_only | lag | -0.005 (-0.5 %) | -0.019 .. +0.008 | -0.011 | +0.020 (+1.8 %) |
| county_only | indep | +0.001 | -0.005 .. +0.007 | -0.004 | +0.012 |

MAE of the House race at 20 / 50 / 70 % counted, lag scenario: separate 1.113 / 0.574 / 0.270, joint 1.118 / 0.558 / 0.254. Minutes to 90 % (paired): joint lag +0.7 (-0.4 .. +2.4), indep -0.5 (-1.5 .. +0.2). No scenario clears zero in every cycle;
2022 is worse in the lag scenario because the House-statewide county correlation was 0.01 in 2022 against 0.46 estimated on the other cycles. Statewide races are unaffected (their projection hardly uses the House count).

## 5. Why the gain is small, and the decision

- *Simultaneous reporting.* A joint model gains only from counties that one race has counted and the other has not (county coupling) and from the other race's statewide swing (statewide coupling). With a common order there are no such counties; and the statewide information
  of race A about race B's swing is a few percent of what B's own counted counties say, because both are sampled from the same counties (sampling-error correlation ~0.7).
- *Different schedules.* The county coupling moves an uncounted county's prior by 0.6-0.8 x the other race's deviation there (R^2 0.5 of a deviation of about 2 points). The final margin is a sum over hundreds of counties, so county errors average out; what remains is the statewide swing, which each
  race's own count estimates quickly. Hence 1-3 % of MAE (0.01-0.04 points of share) even when one race leads the other by two hours in every county.
- *Statewide coupling is not worth it.* Its parameter (rho_s) is estimated from a few dozen pairs, flips sign by cycle, and the variant that uses it (`state_only_n`) is never better than the separate model.
- *The bar:* "the joint update must beat the plain separate-model replay out of sample on held-out cycles, every cycle reported, on BOTH accuracy (MAE by % counted) and minutes-to-90 on the winner, without worse 90 % coverage". It fails: none in the simultaneous case (the case that matters on election night: one county feed with both races), a
  <= 3 % MAE gain with changes of the minutes to 90 % between -3.2 and +0.5 in the lag scenarios, passes in only 5 of the 15 cycle x schedule cells for `joint` (4 of 15 for `county_only`; on MAE and coverage alone `joint` 10 of 15, `county_only` 9 of 15, of which 7 change the minutes to 90 % by more than 3 seconds, 3 of those for the worse) and is worse in 2018 and 2020 under some schedules (`joint` +0.010 in 2018 same, +0.047 in 2020 indep). The unified challenger is therefore left unchanged; the code stays in `us_unified_xrace.py` as a documented sensitivity.

## 6. Robustness: registered tau / sigma_mun0

`analysis/us_crossrace.py replay --registered` repeats the Senate x governor run with the registered count settings (tau 0.06, sigma_mun0 0.08; 6 nights per pair): see `results/us_crossrace/replay_registered_*.csv`; summary at the end of this section.

| variant | scenario | d MAE <= 50 % counted (points) | 95 % interval | relative | paired minutes to 90 % (all races), interval |
|---|---|---|---|---|---|
| joint | same | +0.022 | -0.000 .. +0.053 | +2.0 % | -0.66 (-1.9 .. +0.04) |
| joint | indep | -0.027 | -0.077 .. +0.029 | -2.5 % | -4.9 (-8.4 .. -1.7) |
| joint | lag | -0.037 | -0.075 .. +0.001 | -3.4 % | -1.4 (-4.2 .. +1.1) |
| county_only | same | 0.000 | | 0 % | 0 |
| county_only | indep | -0.029 | -0.044 .. -0.015 | -2.7 % | +0.7 (-0.5 .. +2.5) |
| county_only | lag | -0.032 | -0.047 .. -0.019 | -3.0 % | +0.8 (-0.3 .. +2.6) |

Same story with intervals about twice as wide (coverage is 0.999 for every variant: the registered widths are far too wide for this replay): nothing under a common order (the joint model is 2 % worse, the county variant
identical), 2.5-3.5 % of MAE (0.03-0.04 points) under different schedules, and minutes to 90 % that move in either direction (the joint model faster on independent orders, the county-only variant slower). Bar cells passed (strict): `joint` 6 of 15, `county_only` 3 of 15.

## 7. What this does and does not say

- It says the *projection from the count* gains little from the other race, in a replay with certified county results, simulated order and the registered nowcast. It does not say the races are independent: the county correlation of 0.7 is real and
  is why both races' prior swings look alike (the unified prior already carries the last presidential lean for both).
- Not tested: real county-by-county posting times of past midterm nights (the replay posts whole counties in a size-ordered random order), within-county partial counts, vote-type structure shared by the races (identical in both, so no extra information), districts split across counties (House districts here are whole-county districts only; their
  prior is the simple presidential-plus-generic-ballot one), 2014 (county results not in the panel: 2016-2024 only).
- Ohio 2026 (Acton / Brown): both races are in one county feed (or two civicAPI feeds posted close together), so the "same" row applies: expect no gain from a joint model; if one race's feed lags the other by hours, the "lag" rows (about -2 % MAE, no change in minutes to 90 %).
