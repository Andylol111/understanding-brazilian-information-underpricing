# Early-vote party composition and the statewide polling miss

**Status: analysis and a pre-registered hypothesis, never a model input.** Reproduce with

```sh
BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_gap.py all
```

Tables: `data_public/us_ev_party_*.csv` (compact, derived). Tests: `tests/test_us_ev_gap.py`.

## Specification (fixed 2026-10-30 19:10 UTC, before any EV measure was compared with any polling miss)

Disclosure of what had already been seen: the polling-miss table existed only as the cycle-level means of
[us_poll_error.md](us_poll_error.md), and [us_midterms.md](us_midterms.md) shows NC's registration-implied EV margin
overstating the Democrat by 10.8 points in 2022 and 2.0 in 2024. No other EV figure had been compared with any miss.

**Race universe and outcome.** Statewide Democrat-vs-Republican Senate, governor and president races 2016-2024 with at
least one poll in the final 21 days. `miss` = 100 x (v3 poll average as of the eve - certified two-party Democratic
share), in points of two-party share; positive = the polls overstated the Democrat. The average is the frozen v3 rule
([us_poll_error.md](us_poll_error.md)); the unit of inference is the state-cycle (a state's Senate, governor and
president races in one cycle share one EV measure).

**EV data.** Cumulative ballots *returned, accepted or cast* (never requested) by the voter's party registration:
D, R, O (everything else), early in-person plus mail ("total"), from official archives only.

**Timing, no look-ahead.** Each state's cut-off is its latest 2026 data date; T_s = 2026-11-03 minus that date, in days.
For a past cycle c use the latest observation dated no later than ED_c - T_s and no more than 2 days earlier; otherwise
the state-cycle is missing. Nothing dated after ED_c - T_s is used.

**Metric.**

- EV margin: e(s,c) = 100 x (D - R) / (D + R + O), points of D-R margin (registration is not a vote: loyalty 1, others
  split evenly; the same definition as `margin_ev_raw` in `earlyvote.json`).
- **EV gap (primary):** x(s,c) = e(s,c) - mean of e(s,c') over the state's earlier cycles c' < c that have data at the
  same T_s, equal weights, midterm and presidential cycles together; requires at least one earlier cycle.
- Implied margin shift: x itself (points of final D-R margin if the EV composition passed through one for one; half of
  it in points of two-party share, the unit of `miss`).
- Turnout-normalised variant: party counts divided by that party's registered voters (snapshot nearest Election Day of
  the same cycle), e_N = 100 x (r_D - r_R) / (r_D + r_R), gap x_N defined as x. Reported where registration by party
  exists for every cycle used.
- Same-type variant: earlier cycles of the same type only (midterm with midterm, presidential with presidential).
  Sensitivity only.

**Hypothesis and sign.** More Democratic EV than the state's own history means the electorate is more Democratic than
the polls assumed, so the miss is lower: miss = alpha_cycle + gamma x with **gamma < 0**. Predicted sign of a race's
miss (within its cycle) = -sign(x - cycle mean of x).

**Tests (out of sample).** Cycle fixed effects: x and miss are demeaned within each cycle over the covered races, so
the cycle-wide polling miss (descriptive only) cannot drive the result. Leave-one-cycle-out: gamma is fitted by least
squares on the demeaned data of the other cycles and applied to the held-out cycle. Reported: (1) sign agreement
between -x~ and the demeaned miss, with a one-sided binomial test against 0.5 and a Wilson 95% interval; (2) the size:
RMSE of the demeaned miss with no adjustment versus after subtracting gamma_hat x~, as Delta RMSE = RMSE(adjusted) -
RMSE(none), negative is better; (3) the slope. All intervals are cluster bootstraps over state-cycles (2,000
resamples, seed 0). Coverage per state (cycles with data, races) is reported. Descriptive extras: pooled correlation
without demeaning; by office; the two variants.

**Decision rule (include / do not include).** Include the EV gap in any later model version only if (a) the leave-one-
out sign agreement has a Wilson lower bound above 50% **and** (b) the bootstrap interval of Delta RMSE lies entirely
below zero, on at least 30 races, 3 cycles and 3 states. Otherwise it is not included.

**2026 hypothesis** (`configs/hypotheses/us2026_ev_gap_v1.toml`): per covered, polled 2026 race, predicted sign
-sign(x~) and size max(0, -gamma_hat) x~ in points of two-party share (gamma_hat fitted on all 2016-2024 cycles); primary
test = one-sided binomial test of sign agreement over covered polled races, scored after certification on the
cycle-demeaned miss; the raw (not demeaned) sign is secondary.

Specification note on components, written before the first run: a state with no total series may use one consistent
component in every cycle. Only Maryland uses this, with in-person early votes; mail ballots by party are published only
as final canvass totals there. It is labelled `ev_kind = inperson`.

Run disclosure: the first full run was made before Maryland's panel had arrived. It had 20 races in 4 states and gave
sign agreement 16/19. The table below is the complete run. Nothing in the specification changed between the two runs.

## Data: which states publish early votes by party, and by date (official sources only)

The collector modules are kept with the raw data (`$BRUNDER_DATA/tmp/ev_src/ev_*.py`); this run used
`BRUNDER_DATA=~/ev_drift_local`. The compact panel is `data_public/us_ev_party_panel.csv` (state, year, date, kind, D,
R, O, note), covering ED-30 to ED.

| state | source | cycles with data by date | same-T use | caveats |
|---|---|---|---|---|
| NC | NCSBE bucket `dl.ncsbe.gov/ENRS/<date>/absentee_<date>.zip` (per ballot; accepted only); 2026 `absentee_counts_county_20261103.csv` | 2016-2024 daily, 2026 to 10-29 | all | 2016-24 rebuilt by return date from the final files, so later rejections are excluded; totals equal `absentee_counts_state` within 5 of 4.5M |
| PA | data.pa.gov DOS mail-ballot files (`mcba-yywm`, `uhfm-zhus`, `3q5t-ddp8`, `nbwd-pfn4`), ballots returned by party and date | 2020-2024 daily, 2026 to 10-29 | 2022+ (needs a prior cycle) | no party mail series for 2016/18; mail only (PA has no in-person early voting); final files include about 1% later-cancelled ballots |
| CO | SoS daily "Ballots Returned" release attachments (xlsx 2020-26, PDF 2016/18) | 2016-2026, weekdays | all | weekday releases only (no T=3,4 except 2020); counts include rejected returns; 40 of 41 xlsx dates equal the release headline |
| MD | SBE `press_room/<year>_stats/.../EarlyVoting RAW data.csv` | 2016-2026 in person, daily | all (in person only) | mail ballots by party are published only as final totals; raw file differs from the official final count by 0.0-1.0% |
| OK | State Election Board daily absentee/early PDFs by party | 2020, 2022, 2024, 2026 (10-28/29) | 2022+ | mail = received; 2016/18 not published |
| FL | DOE vote-by-mail / early-voting compilations | final only for 2016-24; 2026 to 10-28 | none (no past daily series) | 2018/2016 and 2020 mail `Stats_*.txt` 404; past "final" files postdate ED |
| NM | SoS daily turnout PDFs | 2018 only | none | 2016, 2020-26 not published by party and date |
| LA | `electionstatistics.sos.la.gov` final by-party report | final 2016-24; 2026 to 10-29 | none | no by-date series |
| ID | voteidaho.gov absentee tracker (Datawrapper versions) | 2026 only | none | 2022/24 behind an AWS-WAF captcha: not used |
| CT, OR, SD | SoS daily releases / overwritten PDF / weekly page | 2026 only | none | no earlier cycles by party and date |
| KS | SoS statistics page | 2020 final only | none | 2026 in a Power BI embed: not used |

These sources were blocked and not circumvented: NV (`nvsos.gov`, Incapsula), AZ (`azsos.gov`, Cloudflare), IA (`sos.iowa.gov`,
403; it does publish daily party PDFs), ME (`maine.gov`, 403), NH (`sos.nh.gov`, 403), RI (`elections.ri.gov`, 403),
GA (403, and no party registration), NY (`elections.ny.gov`, Cloudflare), MA (`sec.state.ma.us`, Incapsula), and NCSBE's
versioned daily 2026 objects (403). These states publish no party breakdown by date: KY, DE, WV, UT, NJ (only third-party
figures), WY, DC, AK, CA. VT and HI have no party registration.

## Results (2016-2024, out of sample)

The polling misses are in `data_public/us_ev_party_misses.csv`: 372 statewide races, v3 average. The metrics are in
`us_ev_party_metrics.csv` and the tests in `us_ev_party_test.csv`. Every covered state has its 2026 cut-off at T = 5
days (MD T = 4).

Coverage, meaning races that have both a poll miss and an EV gap (a prior cycle is needed, so a state's first cycle never
counts):

| state | cycles | races |
|---|---|---:|
| CO | 2018, 2020, 2022, 2024 | 6 |
| MD | 2020, 2022, 2024 | 4 |
| NC | 2020, 2022, 2024 | 6 |
| OK | 2022, 2024 | 4 |
| PA | 2022, 2024 | 4 |
| **total** | 14 state-cycles, 4 cycles | **24** |

Leave-one-cycle-out, with cycle fixed effects. Brackets give the Wilson 95% interval for sign agreement and state-cycle
cluster-bootstrap 95% intervals for everything else:

| variant | races | sign agreement | one-sided p | RMSE none -> adjusted | Delta RMSE | mean LOCO slope |
|---|---:|---|---:|---|---|---|
| **primary: EV gap** | 24 | **14/23 = 0.61 [0.41, 0.78]** | 0.20 | 2.59 -> 3.20 | **+0.61 [-0.33, +3.41]** | -0.13 [-0.34, +0.07] |
| turnout-normalised | 20 | 9/19 = 0.47 [0.27, 0.68] | 0.68 | 1.25 -> 1.42 | +0.17 [-0.21, +3.03] | -0.00 |
| same cycle type only | 18 | 10/18 = 0.56 [0.34, 0.75] | 0.41 | 1.33 -> 1.47 | +0.14 [-0.23, +2.18] | -0.02 |
| primary, Senate only | 9 | 7/9 | 0.09 | 3.36 -> 3.47 | +0.11 | -0.29 |
| primary, governor only | 7 | 4/6 | 0.34 | 2.71 -> 2.26 | -0.46 | -0.34 |

The president-only subset cannot be fitted leave-one-cycle-out, because it has only 2 cycles. The pooled correlation
of the EV gap with the miss, without demeaning, is -0.39. That is descriptive only: it mixes the cycle-wide miss with
cycle-wide EV shifts such as 2020's.

**Conclusion: do not include.** The sign points the hypothesised way (slopes are negative, and 61% of signs agree), but
neither pre-registered condition holds:

- the Wilson lower bound is 0.41, not above 0.50;
- adjusting the misses makes them *larger* out of sample (Delta RMSE +0.61, interval above zero in part);
- the 24 races fall short of the 30 the rule requires.

The leave-one-cycle-out slope is unstable because 2020 (the mail surge) and PA 2024 (gap -24 points as Republicans
adopted mail voting) dominate it. The turnout-normalised variant, which removes registration drift, shows nothing.

## 2026 hypothesis (`configs/hypotheses/us2026_ev_gap_v1.toml`)

gamma_hat = -0.129 points of two-party share per point of EV gap, fitted on all 24 races. The covered, polled 2026 races
are those in a state with a same-T history and a v3 poll average:

| race | EV through | EV margin | EV gap | centred | predicted sign of centred miss | predicted size |
|---|---|---:|---:|---:|---|---:|
| MD governor (in person) | 2026-10-30 | +36.0 | +6.84 | +2.23 | polls understate D | -0.29 |
| NC Senate | 2026-10-29 | +13.7 | +6.70 | +2.09 | polls understate D | -0.27 |
| PA governor | 2026-10-29 | +39.6 | +0.29 | -4.32 | polls overstate D | +0.56 |

CO and OK have EV gaps (+4.54 and +7.10) but no polled race in our averages, so they are not scored. The primary test is a
one-sided exact binomial test of sign agreement, with its p-value reported. The family {us2026_ev_gap_v1,
us2026_drift_v1} is evaluated with a Holm correction at alpha = 0.05. With 3 races the smallest attainable p is 0.125,
so this hypothesis **cannot reject on its own**; it adds its p-value to the family. It is scored after certification.

## EV-informed projection (display only): `site/data/us2026/ev_projection.json`

For each polled race in a state with a 2026 *total* EV series, and for each EV data date, the final D-R margin is
split into two parts:

- **Already voted:** the official count and party mix. Vote choice is D 0.95 (sd 0.03), R 0.05 (sd 0.03), and
  others at the race poll average (sd 0.08). This is a documented loyalty assumption. The 2026 crosstabs
  (`data_public/us2026_crosstabs.csv`) print whole-sample party ID or registration, not the vote by party, for too
  few polls.
- **Yet to vote:** its size is the model v4 expected turnout (`us2026_expected_votes_v4.csv`, cv 0.08). Its preference
  is the poll average adjusted algebraically for the already-voted mix: (p - f x ev) / (1 - f), with the poll sd
  inflated by 1/(1-f).

With 20,000 draws, a seed derived from the state, date and race, the file gives the mean, sd, quantiles
(2.5/10/25/50/75/90/97.5%) and 31 density points (normal approximation). The point estimate stays anchored to the poll
average by construction, so the EV mix changes the decomposition and its uncertainty, not the centre.

**"Already voted" mix check:** no poll in the published crosstabs reports the party mix of its already-voted subsample.
`mix_checks.flags` is therefore empty, and no whole-sample mix is passed off as one.

**Coverage:** FL (Senate, governor), NC Senate, OR governor, PA governor and SD governor. CT governor is not covered,
because the v4 expected-turnout table has no CT units (planning regions). MD is not covered because it has no total
series.

Schema: `{built, as_of, note, schema, sources, assumptions, mix_checks{available, flags[], note}, races{race:{date:{ev_ballots,
expected_turnout, ev_fraction, official_ev_party_mix{D,R,O}, ev_dem_share_assumption, remaining_dem_share, mean_margin,
sd_margin, quantiles{"0.025".."0.975"}, density[[margin, density]]}}}, latest{race: <same record>}}`. Margins are in
points of D-R. Rebuild after new EV files arrive with `analysis/us_ev_gap.py all`.

## Count-night tells: raw count margin vs final margin (descriptive; informs a later model version, not 2026's)

**What this is.** For each state, how far the two-party D - R margin of the votes counted so far sat from the final certified margin when 10, 25, 50 and 75 % of the final two-party vote was counted, and which vote types reported when. It is a description of 2020 and 2024; it feeds no registered 2026 configuration or hypothesis.

**Method.** `analysis/us_ev_tells.py` builds a statewide cumulative D / R count series from the county (2020) or state (2024) count feeds, carrying each county's last value forward and dropping observations that lower a county's running maximum (feed glitches). "% counted" is counted two-party votes / certified two-party votes (MEDSL certified state totals). The raw margin at q % is interpolated linearly in votes between the two snapshots around q. gap(q) = raw margin - final margin, in points of D - R; positive means the early count was more Democratic than the final result ("blue mirage", later red shift), negative more Republican ("red mirage", later blue shift). Cells show the mean over the available cycles, (min … max) and the number of cycles n (at most 2). Classification: mean gap at 25 and 50 % beyond +-2 points. All numbers are in `site/data/us2026/tells.json` (per cycle in `gaps_by_cycle`, with hours since the first reported vote, margins of each 25 % count block, and the feed's coverage of the certified vote).

### Gap table (raw count margin minus final margin, points of D - R)

| state | cycles | 10 % | 25 % | 50 % | 75 % | count order |
|---|---|---|---|---|---|---|
| AK | 2020/2024 | -2.3 (-17.3 … +12.7), n=2 | -8.3 (-17.3 … +0.7), n=2 | -7.0 (-13.1 … -0.9), n=2 | -4.7 (-7.3 … -2.1), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| AL | 2020/2024 | +13.6 (+13.2 … +14.0), n=2 | +2.3 (-1.4 … +6.0), n=2 | -1.9 (-2.7 … -1.1), n=2 | -3.8 (-6.1 … -1.6), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| AR | 2020/2024 | +20.7 (-11.9 … +53.2), n=2 | +14.7 (+9.3 … +20.0), n=2 | +3.8 (+2.0 … +5.6), n=2 | -1.5 (-4.6 … +1.6), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| AZ | 2020/2024 | +8.2 (+5.6 … +10.9), n=2 | +8.2 (+5.6 … +10.9), n=2 | +8.7 (+6.4 … +10.9), n=2 | +3.1 (+0.0 … +6.2), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| CA | 2020/2024 | +2.5 (+2.0 … +3.1), n=2 | +5.5 (+0.0 … +11.0), n=2 | +6.8 (+2.8 … +10.8), n=2 | +0.8 (-1.1 … +2.6), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| CO | 2020/2024 | +25.1 (+19.4 … +30.9), n=2 | +7.7 (+3.2 … +12.1), n=2 | +3.4 (+2.1 … +4.7), n=2 | +2.2 (+0.9 … +3.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| CT | 2020/2024 | -0.2 (-3.6 … +3.1), n=2 | -5.4 (-5.6 … -5.2), n=2 | -5.8 (-6.0 … -5.7), n=2 | -4.6 (-5.2 … -4.0), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| DC | 2020 | -0.9 (-0.9 … -0.9), n=1 | -0.5 (-0.5 … -0.5), n=1 | -0.3 (-0.3 … -0.3), n=1 | +0.2 (+0.2 … +0.2), n=1 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| DE | 2020/2024 | +23.8 (-0.1 … +47.8), n=2 | +20.6 (-0.1 … +41.4), n=2 | +2.9 (-0.0 … +5.7), n=2 | -0.2 (-0.9 … +0.4), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| FL | 2020/2024 | +0.6 (-7.3 … +8.5), n=2 | +4.9 (+1.6 … +8.2), n=2 | +3.7 (+0.9 … +6.4), n=2 | +4.5 (+4.1 … +5.0), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| GA | 2020/2024 | -11.0 (-17.7 … -4.3), n=2 | -17.9 (-21.0 … -14.9), n=2 | -12.1 (-15.1 … -9.1), n=2 | -6.1 (-8.6 … -3.6), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| HI | 2020/2024 | +3.7 (+2.6 … +4.7), n=2 | +3.7 (+2.6 … +4.7), n=2 | +3.7 (+2.6 … +4.7), n=2 | +3.7 (+2.6 … +4.7), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| IA | 2020/2024 | +29.2 (+23.4 … +35.1), n=2 | +24.2 (+18.8 … +29.6), n=2 | +11.0 (+3.3 … +18.7), n=2 | +2.9 (-0.4 … +6.3), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| ID | 2020/2024 | +31.5 (+19.9 … +43.1), n=2 | +27.7 (+16.7 … +38.7), n=2 | +13.2 (+5.9 … +20.6), n=2 | +3.9 (+0.4 … +7.4), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| IL | 2020/2024 | +4.2 (-1.6 … +10.0), n=2 | +3.3 (-4.8 … +11.3), n=2 | +4.6 (-0.4 … +9.6), n=2 | -1.2 (-3.3 … +0.9), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| IN | 2020/2024 | -5.1 (-11.6 … +1.3), n=2 | -10.2 (-10.4 … -9.9), n=2 | -3.0 (-6.4 … +0.5), n=2 | -4.0 (-6.7 … -1.2), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| KS | 2020/2024 | +22.1 (+12.5 … +31.6), n=2 | +24.1 (+20.8 … +27.5), n=2 | +14.5 (+13.9 … +15.1), n=2 | +2.4 (+0.2 … +4.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| KY | 2020/2024 | +8.1 (-15.0 … +31.2), n=2 | +3.0 (+2.6 … +3.4), n=2 | +0.0 (-1.7 … +1.7), n=2 | +3.2 (+1.9 … +4.6), n=2 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| LA | 2020/2024 | -14.8 (-23.0 … -6.6), n=2 | -11.8 (-21.0 … -2.6), n=2 | -10.9 (-12.0 … -9.8), n=2 | -8.4 (-8.5 … -8.3), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| MA | 2020/2024 | +6.7 (+6.5 … +6.9), n=2 | -2.0 (-4.9 … +0.9), n=2 | +0.7 (+0.0 … +1.3), n=2 | +1.9 (+1.6 … +2.1), n=2 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| MD | 2020/2024 | -13.6 (-25.8 … -1.4), n=2 | +5.9 (-1.4 … +13.2), n=2 | +2.5 (-1.4 … +6.4), n=2 | -7.8 (-8.2 … -7.3), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| ME | 2020/2024 | -2.4 (-8.9 … +4.2), n=2 | -5.5 (-9.2 … -1.9), n=2 | -2.0 (-3.5 … -0.5), n=2 | +4.1 (+3.7 … +4.5), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| MI | 2020/2024 | -6.8 (-23.3 … +9.8), n=2 | -6.2 (-18.4 … +6.0), n=2 | -8.9 (-13.4 … -4.4), n=2 | -7.2 (-9.4 … -5.0), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| MN | 2020/2024 | +1.8 (-28.8 … +32.4), n=2 | +8.7 (-10.2 … +27.6), n=2 | +7.9 (+6.7 … +9.1), n=2 | -0.1 (-0.7 … +0.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| MO | 2020/2024 | +33.8 (+27.8 … +39.8), n=2 | +13.4 (+12.3 … +14.6), n=2 | -1.0 (-5.8 … +3.7), n=2 | -1.8 (-4.1 … +0.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| MS | 2020/2024 | +0.8 (-3.7 … +5.2), n=2 | -0.6 (-3.0 … +1.8), n=2 | -4.2 (-8.5 … +0.1), n=2 | -4.2 (-5.6 … -2.8), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| MT | 2020/2024 | +13.2 (-8.8 … +35.3), n=2 | +8.0 (+0.6 … +15.3), n=2 | +7.8 (+2.2 … +13.5), n=2 | +1.5 (-1.9 … +4.9), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| NC | 2020/2024 | +10.8 (+7.4 … +14.1), n=2 | +5.2 (-3.8 … +14.1), n=2 | +7.0 (-0.0 … +14.1), n=2 | +2.0 (-3.0 … +6.9), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| ND | 2020/2024 | +3.0 (-8.9 … +14.9), n=2 | +10.4 (-3.8 … +24.7), n=2 | +1.8 (-3.5 … +7.1), n=2 | -1.4 (-2.7 … -0.1), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| NE | 2020/2024 | +27.9 (+18.4 … +37.5), n=2 | +28.2 (+19.0 … +37.5), n=2 | +14.3 (+13.2 … +15.4), n=2 | +6.2 (+4.7 … +7.7), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| NH | 2020/2024 | +5.4 (+1.8 … +9.0), n=2 | +3.7 (+2.0 … +5.4), n=2 | +1.2 (+0.6 … +1.9), n=2 | +1.5 (+0.8 … +2.1), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| NJ | 2020/2024 | -10.0 (-15.9 … -4.1), n=2 | +3.0 (+0.6 … +5.5), n=2 | +2.3 (-0.1 … +4.7), n=2 | +1.6 (+0.0 … +3.2), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| NM | 2020/2024 | +1.6 (-3.1 … +6.3), n=2 | -2.4 (-11.2 … +6.3), n=2 | +0.1 (-1.0 … +1.1), n=2 | -1.4 (-2.1 … -0.6), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| NV | 2020/2024 | -13.8 (-21.5 … -6.0), n=2 | -3.3 (-4.9 … -1.7), n=2 | +0.2 (-0.2 … +0.6), n=2 | +0.1 (-1.6 … +1.9), n=2 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| NY | 2020/2024 | +34.8 (+34.4 … +35.2), n=2 | +20.9 (+18.1 … +23.6), n=2 | +6.2 (-0.7 … +13.2), n=2 | -0.7 (-4.9 … +3.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| OH | 2020/2024 | +19.8 (+18.2 … +21.4), n=2 | +20.9 (+20.3 … +21.4), n=2 | +12.4 (+5.1 … +19.6), n=2 | +1.9 (-1.4 … +5.2), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| OK | 2020/2024 | +11.7 (-1.7 … +25.0), n=2 | +7.0 (-10.9 … +25.0), n=2 | -0.5 (-11.6 … +10.5), n=2 | -2.4 (-6.5 … +1.8), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| OR | 2020/2024 | +14.4 (+6.7 … +22.1), n=2 | +14.4 (+6.7 … +22.1), n=2 | +8.6 (+4.4 … +12.8), n=2 | +1.7 (-2.0 … +5.4), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| PA | 2020/2024 | +37.7 (+30.9 … +44.5), n=2 | +9.2 (+3.7 … +14.8), n=2 | -5.1 (-10.2 … +0.0), n=2 | -5.9 (-10.3 … -1.4), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| RI | 2020/2024 | -21.8 (-31.5 … -12.0), n=2 | -20.4 (-31.5 … -9.3), n=2 | -15.1 (-21.1 … -9.1), n=2 | -8.3 (-10.8 … -5.8), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| SC | 2020/2024 | +6.2 (-2.9 … +15.3), n=2 | +0.1 (-1.5 … +1.7), n=2 | -2.6 (-3.5 … -1.7), n=2 | -4.6 (-6.9 … -2.3), n=2 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| SD | 2020/2024 | -8.9 (-14.9 … -2.9), n=2 | -7.5 (-11.0 … -4.0), n=2 | -7.4 (-9.4 … -5.4), n=2 | -5.7 (-5.9 … -5.4), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| TN | 2020/2024 | -23.0 (-26.9 … -19.2), n=2 | -19.1 (-21.8 … -16.3), n=2 | -5.2 (-7.7 … -2.8), n=2 | -2.7 (-7.8 … +2.4), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| TX | 2020/2024 | +23.6 (+21.2 … +26.0), n=2 | +14.1 (+7.9 … +20.2), n=2 | +10.1 (+9.1 … +11.0), n=2 | +3.0 (+0.6 … +5.5), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| UT | 2020/2024 | +27.3 (+18.9 … +35.8), n=2 | +26.9 (+18.9 … +34.9), n=2 | +8.6 (+7.5 … +9.7), n=2 | -0.3 (-0.7 … +0.1), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| VA | 2020/2024 | -1.5 (-16.8 … +13.9), n=2 | -13.5 (-30.2 … +3.1), n=2 | -14.8 (-23.1 … -6.4), n=2 | -5.2 (-6.2 … -4.3), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| VT | 2020/2024 | -4.8 (-6.2 … -3.3), n=2 | -3.6 (-7.2 … +0.1), n=2 | -2.3 (-5.5 … +0.9), n=2 | +0.0 (-0.7 … +0.7), n=2 | red mirage -> blue shift (early count more Republican than the final) |
| WA | 2020/2024 | -7.8 (-21.1 … +5.6), n=2 | -3.8 (-14.1 … +6.4), n=2 | +2.0 (-2.7 … +6.7), n=2 | +4.0 (+1.1 … +6.8), n=2 | cycle-dependent (red mirage in one cycle, blue mirage in the other) |
| WI | 2020/2024 | -1.5 (-4.6 … +1.6), n=2 | +3.4 (+0.2 … +6.5), n=2 | -1.1 (-3.0 … +0.9), n=2 | -4.3 (-5.7 … -2.9), n=2 | little early bias (|mean gap at 25-50 %| < 2 pts) |
| WV | 2020/2024 | +20.9 (+19.9 … +21.8), n=2 | +10.2 (+7.6 … +12.8), n=2 | +6.0 (-0.3 … +12.3), n=2 | +2.8 (+1.1 … +4.4), n=2 | blue mirage -> red shift (early count more Democratic than the final) |
| WY | 2020/2024 | -8.7 (-10.0 … -7.3), n=2 | -2.6 (-3.1 … -2.1), n=2 | -1.9 (-7.3 … +3.5), n=2 | -2.4 (-8.0 … +3.3), n=2 | red mirage -> blue shift (early count more Republican than the final) |

### Vote types: share of the two-party vote and D - R margin by mode of voting (certified)

Share = mode's share of the classified two-party vote; margin = two-party D - R in points. Early = in-person early / advance / one-stop; mail = absentee by mail; other = provisional and similar. `coverage` = share of the state's two-party vote that sits in counties with a mode breakdown (rows below 90 % are not reported).

| state | year (race) | early share / margin | mail share / margin | Election Day share / margin | other share / margin | coverage | source |
|---|---|---|---|---|---|---|---|
| AK | 2018 (governor) | 15% / +12.0 | 14% / -10.3 | 71% / -10.8 | – | 100% | medsl_precinct |
| AK | 2022 (governor) | 14% / -27.0 | 20% / -13.7 | 66% / -48.5 | – | 100% | medsl_precinct |
| AR | 2018 (governor) | 50% / -31.1 | 5% / -40.0 | 45% / -37.8 | 0% / -16.6 | 100% | medsl_precinct |
| AR | 2020 (us president) | 69% / -30.9 | 10% / +25.1 | 22% / -45.1 | 0% / +6.7 | 100% | medsl_county |
| AR | 2022 (governor) | 55% / -23.8 | 2% / -0.1 | 43% / -35.6 | 0% / +10.9 | 100% | medsl_precinct |
| AR | 2024 (us president) | 72% / -30.7 | 2% / +1.5 | 25% / -36.1 | 0% / +13.5 | 100% | medsl_county |
| AZ | 2020 (us president) | 88% / +4.7 | – | 11% / -34.3 | 1% / -14.4 | 100% | medsl_county |
| AZ | 2024 (us president) | 77% / -0.5 | – | 23% / -22.8 | 0% / -17.1 | 100% | medsl_precinct |
| CA | 2022 (governor) | – | 88% / +23.5 | 12% / -16.9 | – | 100% | medsl_precinct |
| CT | 2018 (governor) | – | 6% / +16.5 | 94% / +3.2 | – | 100% | medsl_precinct |
| DE | 2018 (us senate) | – | 5% / +25.9 | 95% / +22.5 | – | 100% | medsl_precinct |
| DE | 2024 (us president) | 41% / +10.3 | 8% / +45.2 | 52% / +14.2 | – | 100% | medsl_precinct |
| GA | 2018 (governor) | 48% / -1.1 | 6% / +24.2 | 46% / -5.2 | 0% / +44.2 | 100% | medsl_precinct |
| GA | 2020 (us president) | 54% / -6.3 | 26% / +30.6 | 19% / -23.1 | 0% / +29.8 | 100% | ga_sos |
| GA | 2022 (governor) | 58% / -0.3 | 6% / +25.9 | 36% / -25.3 | 0% / +23.4 | 100% | ga_sos |
| GA | 2024 (us president) | 71% / -3.0 | 5% / +30.2 | 23% / -7.3 | 0% / +20.0 | 100% | ga_sos |
| HI | 2018 (governor) | 13% / +32.5 | 87% / +37.6 | – | – | 100% | medsl_precinct |
| HI | 2022 (governor) | – | 96% / +29.4 | 4% / -48.4 | – | 100% | medsl_precinct |
| HI | 2024 (us president) | – | 92% / +28.2 | 8% / -33.6 | – | 100% | medsl_precinct |
| IA | 2020 (us president) | – | 59% / +16.1 | 41% / -44.3 | – | 100% | medsl_county |
| IA | 2022 (governor) | – | 30% / +12.9 | 70% / -32.7 | – | 100% | medsl_precinct |
| IA | 2024 (us president) | – | 41% / +4.5 | 59% / -25.8 | – | 100% | medsl_precinct |
| ID | 2018 (governor) | – | 12% / -12.3 | 88% / -23.4 | – | 100% | medsl_precinct |
| IN | 2018 (us senate) | 3% / -22.2 | 34% / -13.6 | 63% / -29.0 | 0% / -54.3 | 100% | medsl_precinct |
| MD | 2018 (governor) | 29% / -5.8 | 5% / -1.9 | 64% / -16.8 | 2% / +27.9 | 100% | medsl_precinct |
| MD | 2020 (us president) | 32% / +5.7 | 50% / +66.2 | 14% / -18.7 | 4% / +53.2 | 100% | medsl_county |
| MD | 2022 (governor) | 19% / +33.1 | 26% / +68.7 | 52% / +14.6 | 4% / +54.9 | 100% | medsl_precinct |
| MD | 2024 (us president) | 33% / +22.0 | 25% / +61.7 | 37% / +12.8 | 5% / +44.4 | 100% | medsl_precinct |
| MO | 2018 (us senate) | – | 5% / -22.1 | 94% / -5.1 | 0% / -43.6 | 100% | medsl_precinct |
| NC | 2020 (us president) | 66% / -5.7 | 18% / +43.2 | 16% / -33.1 | 0% / -13.8 | 100% | medsl_county |
| NC | 2022 (us senate) | 53% / +5.4 | 5% / +47.4 | 41% / -20.7 | 0% / +3.2 | 100% | medsl_precinct |
| NC | 2024 (us president) | 74% / -2.8 | 5% / +27.3 | 20% / -12.9 | 0% / +0.7 | 100% | medsl_precinct |
| NE | 2018 (governor) | – | 4% / -34.5 | 96% / -17.3 | – | 100% | medsl_precinct |
| NJ | 2018 (us senate) | – | 11% / +13.6 | 87% / +10.8 | 2% / +24.1 | 100% | medsl_precinct |
| NM | 2024 (us president) | 60% / +2.5 | 13% / +47.4 | 27% / -5.1 | – | 100% | medsl_county |
| OK | 2018 (governor) | 9% / -5.9 | 6% / +2.8 | 85% / -14.3 | – | 100% | medsl_precinct |
| OK | 2020 (us president) | 11% / -32.4 | 18% / +18.9 | 71% / -47.5 | – | 100% | medsl_county |
| OK | 2022 (governor) | 12% / -1.8 | 6% / +24.3 | 82% / -18.7 | – | 100% | medsl_precinct |
| OK | 2024 (us president) | 19% / -36.8 | 7% / +2.3 | 74% / -37.8 | – | 100% | medsl_county |
| PA | 2024 (us president) | – | 28% / +31.0 | 71% / -14.7 | 1% / +18.8 | 100% | medsl_county |
| RI | 2018 (governor) | – | 7% / +31.0 | 93% / +16.2 | – | 100% | medsl_precinct |
| RI | 2022 (governor) | 20% / +30.9 | 9% / +57.3 | 71% / +11.5 | – | 100% | medsl_precinct |
| RI | 2024 (us president) | 34% / +17.8 | 11% / +54.7 | 56% / +4.1 | – | 100% | medsl_precinct |
| SC | 2018 (governor) | – | 17% / +9.7 | 83% / -11.7 | 0% / +12.8 | 100% | medsl_precinct |
| SC | 2020 (us president) | 35% / +3.5 | 18% / +21.7 | 47% / -36.1 | 0% / -18.5 | 100% | medsl_county |
| SC | 2022 (governor) | 32% / -4.1 | 4% / -0.9 | 63% / -25.5 | 1% / -13.3 | 100% | medsl_precinct |
| SC | 2024 (us president) | 58% / -17.0 | 4% / +13.6 | 38% / -23.4 | 0% / -15.0 | 100% | medsl_county |
| VA | 2018 (us senate) | – | 10% / +33.9 | 90% / +14.3 | 0% / +44.3 | 100% | medsl_precinct |
| VA | 2020 (us president) | – | 63% / +31.5 | 36% / -26.9 | 0% / +33.2 | 100% | medsl_county |

### Georgia: vote types against the count

Certified Georgia SoS results by vote type (advance voting = early, absentee by mail, Election Day; margins D - R in points):

- 2018 (governor): early 48% of votes at -1.1, mail 6% of votes at +24.2, eday 46% of votes at -5.2
- 2020 (us president): early 54% of votes at -6.3, mail 26% of votes at +30.6, eday 19% of votes at -23.1; count gap at 25 % / 50 % counted -14.9 / -15.1
- 2022 (governor): early 58% of votes at -0.3, mail 6% of votes at +25.9, eday 36% of votes at -25.3
- 2024 (us president): early 71% of votes at -3.0, mail 5% of votes at +30.2, eday 23% of votes at -7.3; count gap at 25 % / 50 % counted -21.0 / -9.1

In every cycle the mail ballots are the most Democratic vote type and Election Day the most Republican. So a count that reports Election Day and advance votes before the mail ballots runs more Republican than the final result, a count that adds mail early narrows that; the sign and size of the gap in the table depend on when each county added its mail ballots (the 2020 order is in `gaps_by_cycle.2020.release`). That link is an inference from the two tables; the feeds do not label which vote types a snapshot contains.

### Coverage

- Timed 2020 (NYT/Edison county feed): 51 states + DC; missing: none.
- Timed 2024 (CNN/Edison state feed): 50 states; missing: DC.
- No timed count data for 2016, 2018 or 2022 in the local sources.
- Vote-type tables: 2018 16 states, 2020 9, 2022 11, 2024 13; states without a table in 2020: AK, AL, CA, CO, CT, DC, DE, FL, HI, ID, IL, IN, KS, KY, LA, MA, ME, MI, MN, MO, MS, MT, ND, NE, NH, NJ, NM, NV, NY, OH, OR, PA, RI, SD, TN, TX, UT, VT, WA, WI, WV, WY; in 2024: AK, AL, CA, CO, CT, DC, FL, ID, IL, IN, KS, KY, LA, MA, ME, MI, MN, MO, MS, MT, ND, NE, NH, NJ, NV, NY, OH, OR, SD, TN, TX, UT, VA, VT, WA, WI, WV, WY.
- States with neither: none.

### Caveats

- **2020 is a COVID cycle.** Mail voting was several times its usual share and very Democratic, so the 2020 gaps mostly reflect when each state counted mail ballots; 2024 is closer to a normal presidential cycle. With only two cycles, mean and range are not a forecast; where the cycles disagree the state is labelled cycle-dependent.
- **State reporting rules differ.** Some states may process mail ballots before Election Day (reported first or merged into precinct totals); others count them last or after Election Day (2020 release methods are in `gaps_by_cycle.2020.release`, from the NYT feed). Counting order inside a state also changed between 2020 and 2024 in several states (e.g. early processing laws).
- **Timed series are county granularity.** A feed snapshot shows what a county had reported, not which vote types; the vote-type tables come from certified returns and say nothing about timing. 2024 county feed overcounts relative to certified (MEDSL README); it is used only for the geography columns, the 2024 gaps use the state feed. New England, Alaska and Louisiana-style units are towns / districts, not counties. "Large units" = top fifth of units by final vote.
- **Feeds are unofficial.** Counts are news-agency feeds (NYT/Edison, CNN/Edison), not official counts; the 2024 state feed stopped on 2024-12-03 (Alaska and New York are at 95-96 % of the certified vote then) and DC is not in it.
- **Vote-type labels differ by state** (MEDSL labels mapped to early / mail / Election Day / other; per-state notes in `caveats`). Iowa and Virginia report one absentee row that includes in-person early voting, Arizona one early row that includes mail, Nebraska 2018 one combined absentee/early row. Presidential tables add up to the certified two-party vote within 3 % (`vs_certified` in tells.json); midterm tables have no certified reference in the local data and rely on the county totals. Files that put every vote into one mode (e.g. West Virginia, Kentucky 2020) are not shown.
- **Most states have no vote-type table.** MEDSL's county and precinct files carry only TOTAL rows for them and no other local source splits certified votes by mode; the NYT 2020 feed's absentee columns exist for a subset of counties only and mean different things by state (Georgia's include advance voting), so they are not used.
- **Georgia** is the full example: the SoS Enhanced Voting files split 2020 / 2022 / 2024 results into advance voting, mail and Election Day statewide and by county.

### Sources and licences

- 2020 county count feed (NYT/Edison), timed: `nyt_counties.tab`, 10.7910/DVN/LZXR5A, CC0 1.0
- 2024 state count feed (CNN/Edison mirrored by MEDSL), timed: `state_ENR_president.tab`, 10.7910/DVN/2CKSIK, CC0 1.0
- 2024 county count feed, timed (geography only): `county_ENR_president.csv`, 10.7910/DVN/2CKSIK, CC0 1.0
- certified president by state 1976-2024: `1976-2024-president.csv`, 10.7910/DVN/42MVDX, CC0 1.0
- certified county president by vote mode 2020, 2024: `countypres_2000-2024.csv`, 10.7910/DVN/VOQCHQ, CC0 1.0
- certified precinct returns by vote mode 2018, 2022, 2024: `medsl18/2018-<st>-precinct-general.zip, medsl22/2022-<st>-local-precinct-general.zip, medsl24/<st>24.zip`, github.com/MEDSL, CC0 1.0
- Georgia SoS Enhanced Voting results by vote type 2020, 2022, 2024: `ga/2020NovGen_pres.json, ga/2022NovGen_gov.json, ga/2024NovGen_pres.json`, results.sos.ga.gov, public record (state of Georgia)
- Not downloaded / not used: 10.7910/DVN/LZXR5A other files (nyt_states.tab, state_closing_times.tab, GA_precincts.csv, ...) (Dataverse guestbook required (name, e-mail); not requested, not used); 10.7910/DVN/K7760H (MEDSL 2020 precinct returns) (Dataverse guestbook required; not used); robots.txt of dataverse.harvard.edu (answered HTTP 202 (challenge) to plain requests; only the documented access API was used)
