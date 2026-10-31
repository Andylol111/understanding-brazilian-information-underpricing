# Hungary 2026: National Assembly election (12 April 2026)

Replay of the official count night against Polymarket. The model is `brunder.nowcast`, applied at the level of the 106
single-member districts, and the seat law is implemented in full. Evidence label: **not pre-registered** (see
"Evidence").

## Feasibility: the official count over time exists

The National Election Office (Nemzeti Választási Iroda, NVI) results site `vtr.valasztas.hu/ogy2026` is a static app.
It reads JSON from `https://vtr.valasztas.hu/ogy2026/data/`:

- `config.json` names the current folders, e.g. `{"ver":"04112100","napkozi":"04121855","szavossz":"05071600"}`.
- Every count publication is a new folder `<MMDDHHMM>` in Budapest local time, holding:
  - `szavossz/OevkJkv.json`: each district's candidate votes and polling stations processed;
  - `szavossz/ListasJkv.json`: list votes, national and 20 counties, domestic and postal;
  - `LevelJkv.json`, `Patko.json`, `DHondtMatrix.json`, and others.
- **Old folders stay online.** A HEAD request for every minute from 19:00 to 08:00 found the folders, because no
  listing exists. That was 780 requests at under 1 request per second, with an honest User-Agent.
  - The night has **40 publications**, every 10 minutes from 20:00 to 02:05 CEST, then 03:11, 04:00 and 06:00.
  - Twelve later folders, up to the final `05071600`, were found from the Internet Archive's copies of `config.json`.
- The finest unit published over time is the district (OEVK, 106). Polling-station results exist only as one file per
  settlement (about 3,200 per publication), which is too many requests per version to replay. They were not fetched.
- **2022 night folders were removed from the server** (HEAD 404 for every minute). The Internet Archive kept 9 of
  them: OevkJkv at 20:53, 21:05, 21:15, 23:10, 00:00, 00:40, 00:50, 01:00 and 07:00, and ListasJkv at 5 of those
  times. These are the only 2022 replay data and are used for calibration. No 2018 count over time exists. The 2018
  district results come from the 2022 site's `ElozoOevkEredmenyek.json`.
- `robots.txt` is `User-agent: * / Disallow:` (everything allowed). No terms of use are published on
  valasztas.hu.
  - Election results are official communications of a public body. They are not protected by copyright in Hungary:
    Act LXXVI of 1999, s. 1(4), as amended by Act XXXVII of 2021: "hatósági vagy más hivatalos közlemények".
  - They are also public data of public interest (Act CXII of 2011).
  - The site therefore publishes the count over time (`count_timeline: true`) and the NVI district polygons, with
    attribution.

## Data on disk ($BRUNDER_DATA = ~/brunder-data-local)

| what | where | size |
|---|---|---|
| 2026 metadata, 52 count publications (OevkJkv, ListasJkv, LevelJkv), final, station lists of split settlements | `raw/hungary/ogy2026/` | 19 MB |
| 2022 metadata, final, 9 archived night publications, station lists | `raw/hungary/ogy2022/` | 6.5 MB |
| NVI results archive 1990-2024 (309 MB zip; 2014 and 2018 station results, 2010 county list results) and 5 Internet Archive copies of the 2018 night list page | `raw/hungary/nvi_archive/`, `raw/hungary/ogy2018_wb/` | 0.6 GB |
| Polymarket: 8 events, 54 markets, 1-minute YES prices 12–19 Apr | `raw/polymarket/<event>/` | 3.5 MB |
| taker trades per signal window, all outputs | `results/hungary/` | 2.8 MB |

Fetch the data with `analysis/hu_fetch.py meta|night|stations|wayback|markets` and, for v2, `nvi|wayback2018`.

## Seat law (`brunder.hungary.allocate_many`, `comp_votes`)

- 106 districts, first past the post.
- 93 list seats, allocated as follows:
  - **Compensation votes** count every vote of a losing candidate, plus the winner's votes minus (runner-up + 1).
    They go to the list of the candidate's nominating group.
  - **Thresholds** are 5% of all valid list votes for a party, 10% for a joint list of two parties, and 15% for three
    or more.
  - **Nationality lists** have a preferential quota of floor((list + compensation votes of passing lists + all
    nationality-list votes) / 93 / 4).
  - **D'Hondt** allocates the remaining seats.
- The code reproduces the official 2022 result exactly:
  - Fidesz-KDNP 135 (87 + 48), opposition 57 (19 + 38), Mi Hazánk 6, German minority 1.
  - It also reproduces the official compensation votes (`tests/test_hungary.py`).
- On the 2026 final it gives Tisza 141 (96 + 45), Fidesz-KDNP 52 (10 + 42) and Mi Hazánk 6, which equals `Patko.json`.

## Model (`brunder.hungary`, config `configs/hu2026_v1.toml`)

At each publication the model runs the following steps:

- **List race:** Tisza vs Fidesz-KDNP national list vote.
  - `brunder.nowcast.nowcast` runs over the 20 counties as one pooled group.
  - The prior is the 2022 two-way district vote (joint opposition vs Fidesz), summed by county.
  - Two terms are added on top:
    - **Postal ballots:** the expected number is registered postal voters × the 2022 return rate. Their split is the
      2022 postal split, shrunk toward the postal count.
    - **Envelope ballots:** transferred and consular voters, counted days later. They are expected at the 2022
      return rate and vote at the projected share + `env_shift`, which is 2022's late shift.
- **Districts:** `brunder.nowcast`'s equations per district (106 districts in 20 county groups), with joint draws.
  - `district_draws` reproduces `nowcast.nowcast`'s mean and sd exactly (tested).
  - The prior is the 2022 official district votes mapped onto the 2024 district map. Budapest, Pest, Fejér and
    Csongrád-Csanád were redrawn. The mapping goes by settlement, and by polling station inside the 23 split
    settlements (`crosswalk`).
- **Seats:** each draw has 106 winners, compensation votes, list votes (third parties at their counted share) and
  the seat law applied. The list and district models share one national normal draw.
- **Markets priced** (39):
  - most seats;
  - list-vote winner;
  - list margin brackets (13);
  - Tisza seat bands (8) and at-least thresholds (5);
  - turnout brackets (7);
  - next PM: Magyar / Orbán, priced as "most seats", which is a mapping and not the market's rule.

Settings were chosen on 2022 only (`analysis/hu_calibrate.py`, `results/hungary/calibration_2022.json`):

| setting | value | how chosen |
|---|---|---|
| district τ / σ_mun0 / κ | 0 / 0.10 / 200k | smallest τ with district 90% coverage ≥ 0.90 (0.934 over 500 district-frames), then lowest MAE |
| list τ | 0.05 | smallest τ whose 90% interval covers the 2022 final in all 5 list frames |
| postal return, A+B postal share | 0.589, 0.980 | 2022 final |
| envelope rate, env_shift | 0.928, +0.190 | 2022 final vs the last 2022 night frame |
| env_sd | 0.10 | judgment, not fitted |
| turnout sd | 0.018 | RMS projection error over the 2022 frames |

## v2 (sensitivity): uncertainty settings from more than one earlier night

**v1 used one night only** (2022: 9 archived district frames, 5 list frames). Poland 2025 and Mexico 2024 showed that
an uncertainty setting (tau) chosen on one night can be overconfident, and v1's district tau was calibrated to 0.
`configs/hu2026_v2.toml` redoes the choice over every earlier night that can be replayed. It is labelled **chosen after
the result was known (sensitivity)**: the 2026 count and result had been seen when it was written, and v1 stays the
headline. The rule below was written on 2026-10-30 before `analysis/hu_calibrate_v2.py` was run, and is not changed
after seeing its results (any change would be disclosed here).

**Earlier nights found** (all official or openly licensed; the NVI publishes results as official communications, no
terms of use, robots.txt allows everything):

| night | what exists | used as |
|---|---|---|
| 2022-04-03 | 9 Internet Archive copies of the NVI JSON count (district votes, 20:53 to 07:00 CEST), 5 with list votes | real order; prior = 2018 on the same districts (as v1) |
| 2018-04-08 | the NVI site kept no timeline. The Internet Archive has 5 to 7 captures of the national list page and the district leader table from 22:43 CEST (69% counted), with national totals only and no district votes. Official final polling-station results (NVI `valasztasi_eredmenyek_1990-2024.zip`) | **simulated orders** of the 2018 stations; prior = 2014 on the same 106 districts |
| 2014-04-06 | same: no timeline; official station-level final | list model only (county prior = 2010 list votes by county); no district prior exists (2010 used a different 176-district map) |

The 2018 and 2014 simulated frames take the stations of every district in one of two orders, five seeds each:
**random**, and **smallest first** (by registered voters times a lognormal(0, 0.3) draw). The same fraction of
stations is counted in every district: 2%, 5%, 10%, 20%, 30%, 45%, 60%, 75%, 90%. This is not the real 2018 order
(none is archived); it is the stated stand-in, as for Poland, Taiwan and NZ. The A side is the whole opposition
(Jobbik, MSZP and its partners, DK, LMP, Momentum, Egyutt) against Fidesz-KDNP, as in v1's 2018 prior. Postal and
envelope votes did not exist in a comparable form in 2014 and 2018, so the postal and envelope settings
(`mail_*`, `envelope_rate`, `env_*`, `turnout_sd`) stay as v1's 2022-only values.

**Selection rule** (each night weighted equally; a night's coverage is the share of its frames, or district-frames,
with the final inside the 90% interval, over frames with 1 to 95% of stations counted):

1. **District model.** tau is the smallest value on the grid {0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05} for which the
   pooled coverage (mean of the nights' coverages) is at least 0.90 and the worst night's coverage is at least 0.75,
   **both** for the 106 district shares and for the national two-way district share, which drives the seats.
   `sigma_mun0` and `kappa` are the grid values with the lowest mean (over nights) absolute district error at that tau.
2. **List model.** The same rule for tau, on the national list share: pooled coverage at least 0.90 and worst night
   at least 0.75, over the 2022 frames and the 2018 and 2014 simulated frames.
3. Everything else is as in v1.

The 2026 replay then runs with v2 and is reported next to v1 (coverage of the final, MAE by share counted, minutes to
90% and 95% against the market and against the markets that were decided before the count, Brier).

### v2 result: what the rule chose (`results/hungary/calibration_v2.json`, `analysis/hu_calibrate_v2.py`)

| district model (sigma_mun0 0.10 in the v1 row, the lowest-error setting at that tau otherwise) | 2022 district / national coverage | 2018 district / national coverage | MAE 2022 / 2018 |
|---|---|---|---|
| v1: tau 0, sigma_mun0 0.10 | 0.93 / 1.00 | 0.91 / **0.54** | 1.29 / 2.45 pt |
| **v2: tau 0.015, sigma_mun0 0.03**, kappa 200k | 0.93 / 1.00 | 0.90 / 0.80 | 1.29 / 2.45 pt |
| tau 0.03, sigma_mun0 0.10 (not chosen) | 0.96 / 1.00 | 0.93 / 0.94 | 1.29 / 2.44 pt |

| list model: coverage of the national list share | 2022 (5 frames) | 2018 (90 simulated) | 2014 (90 simulated) |
|---|---|---|---|
| v1: tau 0.05 (all 5 of 2022) | 1.00 | 0.98 | 1.00 |
| **v2: tau 0.03** | 0.80 | 0.92 | 0.98 |
| tau 0.02 (fails the rule) | 0.80 | 0.58 | 0.89 |

- **v1's district tau of 0 would have failed the multi-night rule:** the national district share was inside its 90%
  interval in only 54% of the 2018 frames. The district-level share was fine in both nights.
- **Chosen:** district tau 0.015 (sigma_mun0 0.03, kappa 200,000); list tau 0.03. Nothing else changes.
- **Disclosures** (made after the results were seen; the rule was not changed):
  - The written rule did not say whether the coverage test is applied per (sigma_mun0, kappa) setting or to the mean over
    them. The script follows v1's procedure: tau is the smallest value that any setting passes, then the lowest-error
    passing setting is taken. The mean-over-settings reading would have chosen tau 0.03 instead (2018 national coverage
    0.94).
  - Both choices are on the edge: the pooled national district coverage is exactly 0.90 (1.00 and 0.80) and the pooled
    list coverage is exactly 0.90 (0.80, 0.92 and 0.98).
  - The 2018 simulation weights two orders equally. The five archived 2018 national list pages (from 22:43 CEST) give
    the real order's early bias: the raw domestic opposition share was short of the final by 1.9, 1.6, 1.3, 1.0 and 0.7
    points at 69%, 75%, 85%, 92% and 97.5% of stations counted. The simulated smallest-first order is short by 1.7 / 1.2
    / 0.85 points at 60% / 75% / 90%, the random order by 0. The real 2018 order was therefore at least as biased as
    smallest-first, so the random order is optimistic. Under smallest-first alone the v2 settings cover 0.82 of districts
    and 0.60 of the national district shares (list tau 0.03: 0.84): **v2 is still not conservative against a
    realistic order**. Under it, tau 0.03 would cover 0.86 / 0.89.
  - The 2018 and 2014 frames leave out postal and envelope ballots (not comparable then), so the postal and envelope
    settings remain 2022-only values from v1. 2014 cannot calibrate the district model (no prior on the same map).

### v2 result: the 2026 replay against v1 (`configs/hu2026_v2.toml`, `analysis/hu_score.py`, `analysis/hu_export_v2.py`)

Same 40 night publications, same prior, same markets. Only the uncertainty settings differ. Evidence label:
**sensitivity, chosen after the result was known**; v1 stays the headline. The site shows v1 and v2 for the six
dashboard races (`P_sensitivity` in the timeline, `stats.sensitivity`).

**v1 already used one night (2022), so v2 is the first multi-night version. It changes very little:**

| | v1 | v2 |
|---|---|---|
| 90% coverage of the final, 1-95% counted: list / seats | 0.998 / 1.00 | 0.998 / 1.00 |
| list share error at 1% / 5% / 10% / 25% counted (model, pts) | -10.20 / -3.83 / -2.91 / -1.60 | the same (the point estimate does not depend on tau) |
| seats share error at 1-5% / 10% / 25% counted (model, pts; raw -6.64 / -3.97 / -2.12) | -2.23 / -0.66 / +0.13 | -2.22 / -0.67 / +0.13 |
| first publication: Tisza seats | 115 ± 6 | 115 ± 8 |
| first publication: P(Tisza ≥ 120) / P(≥ 130) (outcome: yes; market 0.69 / 0.34) | 0.23 / 0.006 | 0.32 / 0.044 |
| contested markets (20): model settled first / market first; median lead | 13 / 5; +11.5 min | 12 / 6; +14.0 min |
| Brier over all 39 markets: model / market | 0.0794 / 0.0072 | 0.0789 / 0.0072 |

- **Coverage did not move.** The final two-way shares were already inside v1's 90% intervals in every minute window
  that counts (seats) or nearly every one (list 0.998), including the first publication (district share 57.9 ± 2.3
  against a final of 60.1). The miss in 2026 is the seat count, which v2 only partly widens: at the first publication
  141 seats is 3.1 sd above v2's 115.5 (it was 4.3 sd above v1's 115 ± 6), so the 130+ markets still start at 4%.
- The widest changes are on the list race: with the lower list tau (0.03 against 0.05) the model settles on the
  list winner 20 minutes earlier (-115 to -95 min against a market that was at 0.95 before the count), and on the
  9+ margin 10 minutes earlier.
- **Minutes to 0.90 / 0.95 on the outcome against Polymarket** (positive = model first; within the 19:00-08:00 CEST
  window; v1 / v2):

  | race | market before the count | to 0.90 | to 0.95 |
  |---|---|---|---|
  | most seats Tisza | 0.895 (contested) | +11 / +11 | +30 / +30 |
  | tisza 130+ seats | 0.34 (contested) | +37 / +27 | +54 / +44 |
  | tisza 120+ seats | 0.69 (contested) | +27 / +27 | +51 / +51 |
  | list winner Tisza | 0.95 (**decided before the count**) | -115 / -95 | -41 / -21 |
  | list margin 9+ | 0.70 (contested) | -67 / -57 | -69 / -49 |
  | turnout 77-80% | 0.675 (contested) | neither settles in the window | neither |

  Nineteen of the 39 markets were already ≥ 0.90 or ≤ 0.10 at the first publication, so "minutes ahead" there is
  not information the count supplied.
- The two-race Brier of the edge-audit window rises from 0.144 to 0.230, but its window shrinks from 58 to 38 minutes
  (it ends when model and market have both settled, and the v2 list model settles sooner), so the two numbers are
  not comparable. The all-market figure is the one to read.
- No profit test was run for v2 (`signals` is skipped for sensitivity configs). v1's result stands.

## Evidence: not pre-registered

- **Before the freeze:** the 2026 market resolutions, the 02:05 CEST national list summary (Tisza 53.2%, Fidesz-KDNP
  38.3%) and one district row were seen while checking that the feed keeps its versions.
- **2026-10-28 18:59 UTC:** settings frozen in `configs/hu2026_v1.toml` (sha256
  `6fec99124024753f9ea3a11b9744ad7874aba56c21dc13fa469ff2d72d78fb5e`). No 2026 count went into any setting.
- **19:01:53 UTC:** one scoring run on the 2026 count (`analysis/hu_score.py`). This was before the config was
  committed or registered.
- **Afterwards:** no setting changed. A re-run that added only the evidence label and note gave identical numbers.
- The config's header comment still says "pre-specified". Its file was kept unchanged so that the sha256 above
  holds. The label is not-preregistered.

## Results (`results/hungary/stats.json`; outcome = Polymarket resolution, identical to the official final)

The official final was:

| quantity | value |
|---|---|
| Tisza two-way list share | 57.94% |
| margin | 14.67 pts |
| Tisza two-way district share | 60.08% |
| turnout | 78.99% |
| Tisza seats | 141 |
| Fidesz-KDNP seats | 52 |

**Does the projection hold?**

- **District model** (seats race; early error of the projected two-way district vote vs raw):

  | first publication with | model | raw |
  |---|---|---|
  | ≥1% / 5% of stations (20:00 CEST) | −2.2 pts | −6.6 pts |
  | ≥10% | −0.7 pts | −4.0 pts |
  | ≥25% | +0.1 pts | −2.1 pts |

  - 90% coverage was 1.00 over 1–95% counted.
  - Seats were projected at 115 ± 6 at the first publication, 131 at 20:25, 139 ± 2.7 at 21:05, and 140 ± 1.5 from
    22:30 (final 141).
  - The first publication's interval missed: P(Tisza ≥ 130) = 0.006.
- **List model:** no better than the raw count early on.

  | counted | model | raw |
  |---|---|---|
  | 1% | −10.2 pts | −10.6 pts |
  | 5% | −3.8 pts | −3.9 pts |

  - P(Tisza wins the list vote) was 0.19 at 20:00 and 0.74 at 20:25, against a market at 0.95.
  - County units are too coarse to correct the small-station-first reporting order. The finer district model was
    far closer from the same publication.
  - From 50% counted the list model is within 0.4 pts, and it ends at 57.98–58.01% from 22:00 (final 57.94%).
- **Settle (≥ 0.9 on the winner to the end; 20 markets contested at the first publication):**
  - The model was first on 13 and the market on 5; the median lead was +11.5 min.
  - Most seats: +11 min. Next PM: +17 / +18 min. Tisza ≥ 100 / 110 / 120 / 130 seats: +20 / +25 / +27 / +37 min.
    130+ band: +34 min.
  - The market was first on the list winner (−115 min) and on the margin brackets, where the model only settled
    after the postal and envelope count.
- **P(win) scores.**

  | | minutes | Brier, model | Brier, market |
  |---|---|---|---|
  | two race markets, edge-audit window | 58 | 0.144 | 0.003 |
  | all 39 markets | 50,141 | 0.079 | 0.007 |

  The all-market difference is +0.072, with a race-cluster CI of +0.032 to +0.119.
- **Catch-up:** the slope of the 30-minute price move on the model-market gap is 0.001 (−0.006 to 0.008; 24 markets).
  The gap did not predict the price.

**Does executable profit exist?** The edge-audit method was used:

- the frozen Brazil night rule, favoured side only, edge ≥ 5¢ after the 1¢ half-spread and fees, ≥ 1% counted;
- one entry per market;
- fills are real taker trades on our side at or better than the signal price, fetched from the data API.

The results:

- **Headline** (60 s latency, 30-min window, no slip):
  - 23 signals, 13 won.
  - Fillable $78,364; P&L −$16,618 (95% CI −$64,368 to +$12,748); p (market fair) 0.89.
  - −$21,530 comes from one market: Tisza ≥ 130 seats NO at 20:00 CEST, model P 0.006, resolved YES. Without it the
    P&L is +$4,913.
  - The next-PM markets made +$6,562 on $52,759.
- **Other execution variants:**
  - Every 30-minute window loses.
  - The best variant is +$1,225 on $17,957 (0 s latency, 5 min, +2¢).
- **No profit after costs at fillable size.**

## Files

- **Code:**
  - `src/brunder/hungary.py`
  - `analysis/hu_fetch.py`, `hu_calibrate.py`, `hu_calibrate_v2.py`, `hu_score.py`, `hu_export.py`, `hu_export_v2.py`, `hu_geo.py`
  - `tests/test_hungary.py`
- **Config:** `configs/hu2026_v1.toml`, to be registered by the integrator; `configs/hu2026_v2.toml` (sensitivity: not
  to be registered as a headline; settings chosen after the result was known).
- **Site:**
  - `site/data/hu2026/{meta,timeline,units,stats,entry}.json`. Timeline rows and map frames cover poll close (19:00
    CEST) to 08:00 CEST, i.e. the 40 night publications. The official final is a meta note and `stats.final`; later
    publications are scored but not drawn. `stats.settle_gated` gives, per dashboard race, gate = first publication
    with >= 1% counted, model = max(model settle, gate), and the lead vs Polymarket in minutes.
  - `site/data/geo/hu_districts.topo.json`
  - The geometry is from the NVI `OevkPoligonok.json`, simplified, 0.08 MB.

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/hu_fetch.py meta 2022 2026 && uv run python analysis/hu_fetch.py night 2026
uv run python analysis/hu_fetch.py stations 2022 2026 && uv run python analysis/hu_fetch.py wayback 2022
uv run python analysis/hu_fetch.py markets
uv run python analysis/hu_calibrate.py          # 2022 only
uv run python analysis/hu_score.py configs/hu2026_v1.toml
uv run python analysis/hu_geo.py && uv run python analysis/hu_export.py
# v2 (sensitivity)
uv run python analysis/hu_fetch.py nvi && uv run python analysis/hu_fetch.py wayback2018
uv run python analysis/hu_calibrate_v2.py       # rule in the "v2" section; writes results/hungary/calibration_v2.json
uv run python analysis/hu_score.py configs/hu2026_v2.toml    # results/hungary_v2/; no profit test
uv run python analysis/hu_export_v2.py          # adds the v2 fields to site/data/hu2026 (run after hu_export.py)
```

## Limitations

- The unit is the district, not the polling station: there is no station-level count over time without about 3,200
  requests per publication.
- The 2022 calibration rests on 9 archived frames and one election. `env_sd` is a judgment.
- The district τ of v1 was calibrated to 0 on one night. The first 2026 publication's seat interval was too narrow;
  v2 (τ 0.015, from two nights) widens it only partly.
- v2's 2018 and 2014 nights use simulated counting orders (no timeline exists); the real 2018 order was at least as
  biased as the "smallest first" order, which v2's intervals do not cover well (0.82 of districts, 0.60 of national
  shares). v2's choice is on the edge of the rule.
- Third-party votes are projected at their counted share with a 10% shared error.
- The next-PM price is mapped to "most seats".
- The 2018 postal split used as the 2022 calibration prior is an assumption (5% opposition). It affects only the 2022
  list calibration.
