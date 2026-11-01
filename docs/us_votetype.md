# Reporting-order (vote-type) risk for the US county count model: an unregistered option, tested leave-one-cycle-out

**Status: option, not registered, not part of the unified freeze.** `brunder.us_votetype` post-processes the output of `brunder.us.project`; nothing in
`brunder.us`, `brunder.nowcast`, `brunder.us_unified` or the registered configs is changed, and the v1 / v3 / unified projections run exactly as before.
The real-night tests (2020, 2022, 2024 count orders) use New York Times / Edison Research copies that stay on the SSD, so **no number derived from
them is published here** (they are in `$BRUNDER_DATA/derived/us_votetype/REPORT.md`). Published below: the method and the Georgia simulated nights (official
certified returns by vote type, simulated count order). Verdict on the real nights, in
words: **the extension did not meet the out-of-sample gain criterion (section 4), so it is not proposed for the unified challenger freeze.**

## 1. The problem

`brunder.nowcast` projects a county's uncounted votes from the county's counted votes and a state swing. In the US a county's votes arrive in batches of
different types (early in person, mail, Election Day, provisional) that lean differently, in an order that differs by state and cycle. When the votes still out are
of another type than the votes already in, the counted part is a biased sample of the county and the count-based sd does not know it. The real count
nights replayed in `docs/us_real_nights.md` show the consequence: with a late-counted vote type out (2020 president) the 90 % bands held far less often than 90 %.

How different the types are depends on the cycle, not only on the state. Official Georgia certified returns (Advanced Voting + Absentee by Mail against Election Day +
provisional; `analysis/us_calibrate.py`):

| election | early share of the vote | Democratic share, early | Democratic share, Election Day | within-county gap (early minus Election Day) |
|---|---|---|---|---|
| 2020 president | 0.80 | 0.529 | 0.388 | 0.101 |
| 2022 governor | 0.64 | 0.511 | 0.374 | 0.102 |
| 2022 Senate | 0.64 | 0.551 | 0.421 | 0.093 |
| 2024 president | 0.77 | 0.497 | 0.464 | 0.015 |

The early share moves with the cycle kind (midterm 0.64, presidential 0.77-0.80) and the lean gap collapsed in 2024. A history from other cycles therefore sets
only a prior; the night's own early / late counts have to take over as soon as they exist.

## 2. Method

Two vote types per county: **early** (the feed's early / absentee / advance split, `early` in the feed rows, `ne`, `ae` in `brunder.us.project`'s unit table) and **late** (the rest).

*History* (`fit_profile`, always fitted on cycles other than the one being projected; cycles of the same kind, presidential or midterm, weigh 1, the other kind 0.25):
`pi_c` the county's final early share; `g_c` its early-minus-late Democratic share gap, shrunk to the state's gap (`k_gap` = 20 000 pseudo-votes); `rho_s(f)` the state's counting
order, i.e. the early share of the counted votes at counted fraction `f` relative to the final early share (10 bins). A night only counts as history when its feed has an early split and the
final early share is between 0.05 and 0.98.

*At a count state* (`adjust`), per county with counted votes `n`, early part `e` and the model's remaining votes `R` (`E = n + R`):

* `q`: early share of the counted votes: the feed's split when it reports one for the county, otherwise the state's order `pi_c · rho_s(f)`;
* `pi_c` is rescaled by the ratio of observed to expected early votes in the counties that are (nearly) in (`k_psi` = 50 000 pseudo early votes);
* `r_e = clip(pi_c E − e, 0, R) / R`: early share of the votes still out;
* `g`: county gap = the history's gap combined with the gap seen tonight in counties that have both types counted; tonight's state-wide gap carries `W_obs / (W_obs + k_state)` of the weight (`k_state` = 100 000);
* the nowcast carries a fraction `rho` of a county's counted deviation, which contains the mix effect `(q − pi) g`, into the remaining votes and moves the state swing by the weighted mean `M` of those mix effects;
  the remaining votes' true mix effect is `(r_e − pi) g`, so the county's remaining Democratic share is shifted by
  `shift_c = lam · [ (r_e − pi) g − rho (q − pi) g − (1 − rho) w M ]` (`w` = the weight the swing puts on counted votes);
* the projection's sd is widened in quadrature: `margin sd² → margin sd² + (kappa · Σ_c 2 R_c |shift_c / lam|)²`, the exposure to the vote type still out. States without a profile get shift 0 and no widening.

`lam` (one weight for counted fraction below 15 %, 15-40 %, above 40 %) and `kappa` are fitted by leave-one-cycle-out (`analysis/us_votetype.py`): `lam` by least squares of the base error on the
unit shift, `kappa` by the Gaussian log score of the shifted errors, nights weighted equally, separately for frames where the feed reports an early split and for frames where it does not (the projection then rests on the history alone).
Training nights get their history from the cycles that are neither the tested nor their own.

## 3. Georgia simulated nights (official certified returns by vote type, order simulated as in `docs/us_unified.md` section 6, 30 nights per race)

History: official certified Georgia returns by vote type of the other cycles (2020 and 2022 / 2024 president / governor / Senate); `lam` / `kappa` fitted on the other simulated cycle (2022 races tested with
fits from the 2024 president nights and the reverse). `kappa` fits to 0 for 2022 and 0.18 for 2024; the uniform-widening control fits to 0 and is left out. Error = projected minus certified two-party Democratic share in points; "band" = share of checkpoints
(5-90 % counted, 5-point steps) at which the 90 % interval held.

| race | prior | setting | nights decided | median minutes to P(winner) ≥ 0.9 | error at 10 % | 25 % | 50 % | 75 % counted (points) | 90 % band held |
|---|---|---|---|---|---|---|---|---|---|
| 2022 governor | v1 | unextended | 1.00 | 136 | 3.64 | 3.74 | 3.13 | 0.37 | 0.99 |
| 2022 governor | v1 | with vote-type risk | 1.00 | 84 | 2.89 | 2.77 | 1.72 | 1.40 | 0.99 |
| 2022 governor | v3 | unextended | 1.00 | 80 | 1.39 | 1.91 | 1.99 | 0.13 | 0.99 |
| 2022 governor | v3 | with vote-type risk | 1.00 | 65 | 0.99 | 1.21 | 0.82 | 1.54 | 0.79 |
| 2022 governor | unified | unextended | 1.00 | 77 | 0.93 | 1.58 | 1.80 | 0.06 | 0.99 |
| 2022 governor | unified | with vote-type risk | 1.00 | 58 | 0.54 | 0.88 | 0.68 | 1.64 | 0.75 |
| 2022 Senate | v1 | unextended | 0.03 | 288 | 3.12 | 3.42 | 2.80 | 0.20 | 1.00 |
| 2022 Senate | v1 | with vote-type risk | 0.00 |  | 2.38 | 2.45 | 1.44 | 1.50 | 0.99 |
| 2022 Senate | v3 | unextended | 0.17 | 295 | 1.03 | 1.62 | 1.67 | 0.06 | 1.00 |
| 2022 Senate | v3 | with vote-type risk | 0.00 |  | 0.63 | 0.93 | 0.58 | 1.64 | 0.81 |
| 2022 Senate | unified | unextended | 0.00 |  | 0.93 | 1.46 | 1.38 | 0.71 | 1.00 |
| 2022 Senate | unified | with vote-type risk | 0.00 |  | 0.54 | 0.79 | 0.46 | 2.16 | 0.74 |
| 2024 president | v1 | unextended | 0.70 | 258 | 1.03 | 1.03 | 0.99 | 0.99 | 1.00 |
| 2024 president | v1 | with vote-type risk | 0.87 | 235 | 0.43 | 0.52 | 0.26 | 0.31 | 1.00 |
| 2024 president | v3 | unextended | 0.93 | 224 | 0.66 | 0.74 | 0.84 | 0.92 | 0.98 |
| 2024 president | v3 | with vote-type risk | 1.00 | 180 | 0.13 | 0.32 | 0.20 | 0.28 | 1.00 |

**Reading.** With the early split in the feed and a profile from official returns, the extension cuts the error at 10 / 25 / 50 % counted by about a fifth to four fifths in every race, and the 2022
governor and the 2024 president are called earlier. The 2022 Senate (a 0.5-point race) was called on 3 % / 17 % of nights by v1 / v3 before and on none after. It does not help late in the 2022 nights: at 75 % counted the error is *larger* than the
unextended model's (0.1-0.7 up to 1.4-2.2 points) and, for v3 and unified, the 90 % band held 0.75-0.81 of the time instead of 0.99-1.00. The shift weight for that stage was fitted on the 2024 presidential nights, whose early share (0.77) is above the 2022
midterms' (0.64); with the history's early share too high the model keeps expecting early votes that were already counted. The extension does not correct that by itself (section 6). Simulated order, one state, two cycles: a check of the mechanics, not a calibration.

## 4. Real nights (internal) and the gain criterion

Leave-one-cycle-out over the 2020, 2022 and 2024 real nights of `docs/us_real_nights.md` (same night classes, same metrics), against the unextended v1 / v3 / unified and against a control that
widens the sd in proportion to the outstanding vote only. The criterion was written into `analysis/us_votetype.py` (`verdict`) and applied to every design tried: on the dense real presidential nights of **both** 2020 and 2024, for v1 and v3,
(1) mean absolute error at 25 / 50 / 75 % counted lower by ≥ 10 % at two of the three checkpoints, (2) coverage of the 90 % band nearer to 0.9, (3) median minutes to 90 % not more than 15 % later.

**Not met** (numbers internal). In words: the extension reduces the error in the first ~15 % of the count on most nights with a split in the feed, and does little between 25 and 75 % counted: the 2020 and 2024 cycles
have different counting orders and lean gaps (section 1), and a history from the other cycles transfers imperfectly; where the 2024 feed carries no split (several states) a history alone is unreliable. Coverage of the 90 % bands is
essentially unchanged: the widening that the leave-one-cycle-out fit selects is small because the cycles used for fitting do not contain a miss like 2020's. A larger fixed widening raises 2020 coverage substantially, but it is read off the tested cycles
and is not out of sample, so it is not claimed.

## 5. Michigan 2022 governor (official state file)

The official Michigan file has no vote-type split, so the extension has nothing to work from on that night. Its only input
there would be vote-type history fitted on the restricted archive, so no table is published; the unextended results for
this night are in `docs/us_real_nights.md` section 5.

## 6. Limits

* Only the early / absentee-advance split of a feed exists in the sources; election-day against mail against provisional is not separable, and the split's definition differs by state and cycle (some 2020 feeds report only part of the early vote, some have none).
* The history for every state except Georgia comes from the restricted archive of past nights; a production use would need official vote-type histories for each state (Georgia's certified returns by vote type are the only one in the repository).
* Three cycles: the leave-one-cycle-out fit of 2020 had only 2022 (sparse) and 2024 to learn from.
* The early share of the vote is a property of the cycle kind (Georgia: 0.64 in the 2022 midterm, 0.77-0.80 in presidential years); weighting same-kind cycles above the other kind and rescaling by the counties already in only soften a history from the wrong kind of cycle.
* Interpolated 2024 Senate / governor nights assume the batch mix moves with the president's count and are used as test nights only, never for fitting.

## 7. Reproduce

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python -W ignore analysis/us_votetype.py cache   # base v1 / v3 / unified projections of every real-night frame -> $BRUNDER_DATA/derived/us_votetype/cache
uv run python -W ignore analysis/us_votetype.py ga --n 30   # Georgia simulated nights (seed 20261103)
uv run python -W ignore analysis/us_votetype.py run     # leave-one-cycle-out fit + tables -> $BRUNDER_DATA/derived/us_votetype/REPORT.md (+ CSVs, fits)
```

Use: `brunder.us_votetype.project(rows, cfg, pri, profile, params)` = `brunder.us.project` + the adjustment; `fit_profile` builds the profile from past nights. Tests: `tests/test_us_votetype.py`.
