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
| Polymarket: 8 events, 54 markets, 1-minute YES prices 12–19 Apr | `raw/polymarket/<event>/` | 3.5 MB |
| taker trades per signal window, all outputs | `results/hungary/` | 2.8 MB |

Fetch the data with `analysis/hu_fetch.py meta|night|stations|wayback|markets`.

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
  - `analysis/hu_fetch.py`, `hu_calibrate.py`, `hu_score.py`, `hu_export.py`, `hu_geo.py`
  - `tests/test_hungary.py`
- **Config:** `configs/hu2026_v1.toml`, to be registered by the integrator.
- **Site:**
  - `site/data/hu2026/{meta,timeline,units,stats,entry}.json`
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
```

## Limitations

- The unit is the district, not the polling station: there is no station-level count over time without about 3,200
  requests per publication.
- The 2022 calibration rests on 9 archived frames and one election. `env_sd` is a judgment.
- The district τ was calibrated to 0. The first 2026 publication's seat interval was too narrow.
- Third-party votes are projected at their counted share with a 10% shared error.
- The next-PM price is mapped to "most seats".
- The 2018 postal split used as the 2022 calibration prior is an assumption (5% opposition). It affects only the 2022
  list calibration.
