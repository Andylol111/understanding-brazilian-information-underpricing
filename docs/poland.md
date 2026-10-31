# Poland 2025: presidential election, round 1 (18 May) and runoff (1 June)

Replay of the official PKW count against Polymarket and Kalshi. Dashboard entries `pl2025r2` (runoff, the main
target) and `pl2025r1` (first round, most votes: Trzaskowski vs Nawrocki). The model is `brunder.nowcast`, applied to
128 cells (16 voivodeships x 8 gmina-size classes) plus abroad. Evidence label: **retrospective, not pre-registered**
(see "Evidence").

## Feasibility: PKW's count over time exists, but only as far as the Internet Archive kept it

The results site `wybory.gov.pl/prezydent2025` (now `prezydent2025.pkw.gov.pl/prezydent2025`) is a single-page app. It
reads gzip-compressed protobuf files from `/prezydent2025/data/`:

- `config.blob` names the current versions.
- `obkw/pl_po_woj.<epoch>.blob` (round 1) and `obkw2/pl_po_woj.<epoch>.blob` (runoff): the whole country in one file,
  per voivodeship plus abroad and ships: obwody (polling districts) counted, eligible voters counted, valid votes and
  votes per candidate, and, nationally, the same by gmina-size class. `<epoch>` is the version, in unix seconds, the
  moment PKW generated the file; on the night a new version came every ~4 minutes.
- Finer files (`obkw2/pow/<TERYT>.<epoch>.blob`, `gm/…`, `kw/…`, one per powiat, gmina, commission) exist, but the
  site only fetches them when a visitor opens that page.
- The schema was read off the site's own JavaScript (`j/20250530104156384.min.js`, the generated protobuf.js decoders
  `HierarchiaOBKW`, `Statystyki`, `StatRes`, `Protokol`, `PozProtokolu`, `HierarchyRes`, `Zestawienia`) and is
  reproduced in `src/brunder/poland.py`. The decoder reproduces PKW's final: the last archived runoff version sums
  to Trzaskowski 10,237,286 and Nawrocki 10,606,877 (PKW: 10,237,677 and 10,606,877; the 391-vote difference is a later
  correction), and every round-1 and runoff total agrees with the gmina csv to within 0.01 pt.
- **The live server no longer serves old versions**: it answers every unknown path with the app shell (HTTP 200,
  HTML). The count history is what the Internet Archive captured (`web.archive.org` CDX + `id_` snapshots, 1 request
  per 1.1 s, honest User-Agent; `robots.txt` of both hosts redirects to the home page, i.e. there is none).
  - Runoff: 28 archived versions, of which 26 hold a count: first at 18:59 UTC (abroad only, 129 obwody, a minute
    before the polls closed), first domestic at 19:31, then 18 more to 21:59 (4-12 minutes apart, except gaps of
    36, 20 and 28 minutes at 20:03-20:39, 20:39-20:59 and 20:59-21:27), then 23:11, 00:19, 00:31, 00:58, 01:02, 01:07 and the complete count at 03:13 UTC. **No version was kept between
    22:00 and 23:11 UTC (counted 15% -> 49%) or between 01:07 and 03:13.**
  - Round 1: 31 archived versions listed (30 downloaded: one capture was a redirect record), 28 with a count, 19:07 to
    23:19 UTC every 4-12 minutes with five gaps of 28-44 minutes, then 00:59 (56% counted), **03:15** (95%), 03:31,
    04:23 and five more to 09:02 UTC. Nothing between 00:59 and 03:15 UTC.
  - So the replay is a good record of the count as the public saw it up to 22:00 UTC and sparse afterwards; the
    model's late settle times are partly this gap (see "Results").
- PKW does not publish separate "partial results at x% of obwody" releases on the 2025 site: the versions above are
  its intermediate publication. The 2020 night (28 June and 12 July 2020) is **not archived** on
  `prezydent20200628.pkw.gov.pl` (0 captures on the two count days; the earliest capture is from 2022), so no 2020
  count over time exists for calibration.
- The finest unit published over time is the voivodeship; the size-class table is national only.

## Earlier nights: what the Internet Archive holds (searched 2026-10-31)

PKW's count-night versions exist as separately addressed files (`pl_po_woj.<epoch>.blob`) only for 2025. Before that the
site used one fixed address per file, so the archive holds whatever a visitor's crawl fetched at that moment. CDX
listings of each election's two days, then the national `pl_po_woj.blob` / `wyn/pl.blob` captures decoded where the
format matches (2023 on):

| night | host | count captures during the night (UTC) | what they show |
|---|---|---|---|
| presidential 2015-05-10 / 05-24 | `prezydent2015.pkw.gov.pl` (HTML) | none: "partial results" page captured 11 May 13:16-14:50 (the afternoon after); nothing for 24 May (CDX returned an empty list for the results pages) | not usable |
| Sejm 2019-10-13 | `sejmsenat2019.pkw.gov.pl` / `wybory.gov.pl/sejmsenat2019` | none: 15 captures of `pl_po_okr_sejm.blob`, all 14 Oct 14:55-18:57 or 15 Oct | count already complete |
| presidential 2020-06-28 / 07-12 | `wybory.gov.pl/prezydent20200628` (older schema) | none: `pl_po_woj.blob` captured 28 Jun 14:47-17:35 (before the polls closed), then 29-30 Jun; runoff 12 Jul 14:46, 13 Jul 08:06 onward | count already complete |
| Sejm 2023-10-15 | `wybory.gov.pl/sejmsenat2023` | 3 snapshots: 22:48 (110 of 31,497 obwody), 16 Oct 09:54 (13,087 = 42%), 17 Oct (complete) | party lists, 1 usable mid-count frame |
| local 2024-04-07 | `wybory.gov.pl/samorzad2024` | 8 Apr 03:46 (358 of 31,456 obwody), 14:30 (847, the last) | 1-3% only, councils |
| European 2024-06-09 | `wybory.gov.pl/pe2024` | 23:18 (6,678 of 31,830 = 21%), then 10 Jun 09:24 (complete) | party lists, 1 mid-count frame |
| presidential 2025, round 1 and runoff | `wybory.gov.pl/prezydent2025` | **31 and 28 versions** (see above) | the two nights scored here |

So the 2025 round-1 night is the only earlier real night with a count history; the 2023 and 2024 snapshots are single frames
of party-list elections (no two-candidate share, a different prior) and are not scored. The 2020 and earlier nights can only
be simulated from final results.

## Sources and licences

| Data | URL | Licence |
|---|---|---|
| PKW count versions `pl_po_woj.<epoch>.blob`, runoff and round 1, via the Internet Archive | https://wybory.gov.pl/prezydent2025/data/obkw2/ (and `obkw/`) | **None stated.** No terms page on the site (checked 2026-10-30; the archive's crawl of the runoff day has only `/common/cookies`); the 2020 site's footer reads "Wszelkie prawa zastrzeżone" (all rights reserved); no `robots.txt`. Official documents and materials are outside copyright (Polish Copyright Act, art. 4(2)) and PKW results are public-sector information, but the files carry no re-use licence. **The files are not republished**; the site publishes derived per-minute series and, per voivodeship and version, the counted share, raw tally and projection (rounded to 0.1 pt). |
| PKW final results by gmina, 2025 round 1 and runoff (`wyniki_gl_na_kandydatow_po_gminach*_csv.1758650309.zip`) | https://prezydent2025.pkw.gov.pl/prezydent2025/pl/dane_w_arkuszach | as above; only the electorate of each cell and the final totals are used and published (final share) |
| PKW final results by gmina and by obwód, 2020 round 1 and runoff | https://prezydent20200628.pkw.gov.pl/prezydent20200628/pl/dane_w_arkuszach | as above; the prior (per-voivodeship share is published) and the simulation inputs |
| Natural Earth 1:10m admin-1 (voivodeship outlines) | https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip | public domain |
| Polymarket price history (1-minute) and questions | public CLOB / Gamma APIs; events `poland-presidential-election`, `poland-presidential-election-margin-of-victory`, `poland-presidential-election-1st-round-winner` | public market data |
| Kalshi 1-minute candles | series `KXPRESPOLAND`, `KXPOLANDMOV`, `KXPOLANDROUND1` | © Kalshi; the derived P line and settle times are published, no raw rows (docs/kalshi.md) |

The dashboard is non-commercial research. Every file names its source in `meta.sources`. The licence status of the
PKW files should be settled with PKW before anything beyond the derived series is published.

## Data on disk ($BRUNDER_DATA = ~/brunder-data-local)

| what | where | size |
|---|---|---|
| CDX listings of the two count days | `raw/poland/cdx_wybory_r{1,2}.json` | 1.6 MB |
| 28 + 30 count versions, candidate lists | `raw/poland/blobs/r{1,2}/`, `komitety*.blob` | 0.5 MB |
| final results (gmina csv 2020/2025, obwód csv 2020) | `raw/poland/final/` | 20 MB |
| Natural Earth admin-1, JS bundle | `raw/poland/geo/`, `app.min.js` | 49 MB, 1 MB |
| Polymarket, Kalshi | `raw/polymarket/poland-*`, `raw/kalshi/KXPRESPOLAND`, `KXPOLANDMOV-25`, `KXPOLANDROUND1-25` | < 5 MB |

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/pl_fetch.py cdx && uv run python analysis/pl_fetch.py blobs
uv run python analysis/pl_fetch.py final && uv run python analysis/pl_fetch.py markets && uv run python analysis/pl_fetch.py kalshi
uv run python analysis/pl_calibrate.py                  # earlier elections only -> results/poland/calibration.json
uv run python analysis/pl_calibrate_pooled.py           # v2 sensitivity: pooled over the earlier nights
uv run python analysis/pl_score.py configs/pl2025r2_v1.toml   # also pl2025r1_v1.toml, pl2025r2_sim2020_v1.toml, pl2025r2_v2.toml
uv run python analysis/pl_geo.py && uv run python analysis/pl_export.py
```

## Model (`brunder.poland`, configs `configs/pl2025r2_v1.toml`, `configs/pl2025r1_v1.toml`)

At each archived version the model runs `brunder.nowcast` over **cells**: voivodeship x gmina-size class (up to
5,000 / 5,001-10,000 / 10,001-20,000 / 20,001-50,000 / 50,001-100,000 / 100,001-200,000 / 200,001-500,000 / over
500,000 residents; 128 cells), grouped by voivodeship (the "state" of the nowcast: a voivodeship-level swing,
shrunk to the national swing with `kappa`), plus abroad with ships as its own group, kept out of the national swing.
A = Trzaskowski, B = Nawrocki; the model compares their two-way share.

- **Prior**: the 2020 election of the same round, Trzaskowski against **Duda** (the right-wing candidate whose place
  Nawrocki takes; Trzaskowski ran in both), by cell, with expected votes scaled by the cell's 2025 / 2020 electorate.
  The 2025 round-1 prior is the 2020 round 1; the 2025 runoff prior is the 2020 runoff.
- **Why cells.** The count is published by voivodeship and, nationally, by size class, never by gmina, and the order
  matters: small obwody finish first, big-city obwody last, so the early count leans toward the rural candidate. A
  voivodeship-only model (the first version, run on the runoff night with default settings) projected the runoff at
  Trzaskowski 46-47% from 21:27 UTC; the final was 49.1%.
- **Cell state from the two sets of marginals.** PKW's classes are by residents, which the csv does not give, so a
  gmina's class is rebuilt from its electorate: gminas sorted by eligible voters, cut so that each class holds the
  electorate PKW reports for it in the last round-1 version (the four biggest cities and the next two classes come out
  exactly as PKW's; the smaller classes within 1%). Then, per version:
  1. the counted share of each cell's electorate is `g = expit(alpha_voivodeship + beta_class)`, fitted so that its row and
     column sums equal PKW's counted-electorate marginals;
  2. each candidate's counted votes in a cell = counted voters x the prior's votes per voter, raked (IPF) to that
     candidate's counted-vote marginals by voivodeship and by class.
  On a simulated count (2020 obwody in smallest-first order) this reconstructs cell votes to within 6-27% and cell
  shares to within 0.8-4.5 points (vote-weighted), worse early; the national share is what matters and is exact.
- **f** (the share of a cell counted) is the **electorate** counted, not the obwody counted: the obwody counted first are
  small, so the share of obwody overstates how much is in.
- **Outputs**: P(Trzaskowski wins) = normal CDF of the projected margin; margin-band markets are priced from the same
  normal on the final share (Nawrocki's margin = 1 - 2 x share, in points of valid votes).

## Settings and how they were chosen

`analysis/pl_calibrate.py`, rule fixed before the run: grid tau x sigma_mun0 x kappa x rho; per frame with 1-95% of the
vote counted, z = (final two-way share - projected) / sd; **sigma_mun0 held at the nowcast default 0.06** (the first run,
which let it float, chose 0.10 with tau = 0 on 14 correlated frames, so it was fixed instead); tau = the smallest
value whose 90% coverage is >= 0.90; kappa and rho = lowest mean absolute error at that tau.

| election replayed | settings chosen on (earlier elections only) | tau | kappa | rho | coverage there |
|---|---|---|---|---|---|
| **runoff** (`pl2025r2_v1`) | the real **2025 round-1 count night** (31 archived versions, 14 at 1-95% counted), 2020 round 1 as prior | 0.005 | 100,000 | 0.8 | 0.93 (tau 0: 0.79; 0.01: 1.00); mean z +1.0, MAE 1.7 pt |
| **round 1** (`pl2025r1_v1`) | the **2020 runoff counted in simulated order** (48 frames; 2020 night not archived), 2020 round 1 as prior | 0.04 | 100,000 | 0.5 | 0.94 (tau 0.03: 0.83); mean z +0.75, MAE 2.3 pt |

The simulation orders each voivodeship's obwody by size with log-normal noise (sd 0.7), smallest first. It is harsher
than the real nights, which is why its tau is 8 times the real night's. The round-1 night was not used for the round-1
config; the runoff night was used for nothing. The runoff was also replayed with the simulation's settings
(`pl2025r2_sim2020_v1.toml`) as a sensitivity.

## Evidence: retrospective, not pre-registered

Written 2026-10-30, after both elections. The archived versions were downloaded and decoded first, a first,
voivodeship-only version of the model was run on the runoff night with default settings (no settings chosen, nothing
frozen), and the cell model then replaced it before the configs were written; the runoff config was frozen from the
round-1 night without reading the runoff result of the cell model. No setting was changed after the runoff scores were
seen. Every entry is labelled "not-preregistered" and the metrics `evidence` keys say so. The configs are not registered
(no change to `prereg/registry.jsonl`).

## Results (`results/poland/<eid>/stats.json`; outcome = the markets' resolution, identical to the official final)

### Runoff (`pl2025r2`): Nawrocki 50.89%, Trzaskowski 49.11% (margin 1.77 points; Nawrocki by 0-4 is the winning band)

Polymarket had Trzaskowski at 76% when the polls closed (19:00 UTC) and still 77% at 21:00 UTC; it fell to 25% by
21:30 and to 6% by 23:00 UTC.

| | UTC | CEST |
|---|---|---|
| polls close | 19:00 | 21:00 |
| first version with >= 1% counted (gate) | 20:39 | 22:39 |
| raw national tally puts Nawrocki ahead for good | 21:27 | 23:27 |
| **model 90% on Nawrocki / 95%** | **21:28 / 21:32** | 23:28 / 23:32 |
| **Polymarket 90% / 95%** (to the end of the night) | **22:14 / 23:12** | 00:14 / 01:12 |
| Kalshi 90% / 95% | 22:14 / 23:10 | 00:14 / 01:10 |

- **Lead of the model over Polymarket: +46 minutes at 90%, +100 at 95%** (from 1% counted; settle = staying at or above
  the level to the end of the price window, 08:00 UTC). At that moment the raw tally had already put Nawrocki ahead
  one minute before, so the model added nothing the count did not already show.
- **The model was overconfident.** Its 90% band held the final share in only **46%** of the minutes from 1% to 95%
  counted (20% from 1% to 90%, 97% from 90% to 95%; mean error +1.95 sd). Projected Trzaskowski share against the final
  49.11%: 5% counted 47.8%, 15% 46.2%, 49% 48.2%, 81% 48.7%, 92% 49.0%, with sds of 1.5, 0.8, 0.4, 0.1, 0.06 points.
  The round-1 night's order bias (the settings' source) was smaller than the runoff's, where the cities were later and
  more lopsided.
- **Sensitivity, settings from the 2020 simulation** (tau 0.04, rho 0.5): the model reaches 90% and 95% at 00:20 UTC,
  **126 minutes after Polymarket** (90%) and 68 minutes after its 95%; coverage of the 90% band 100%, mean error
  +0.4 sd. The gap between the two rows is the weakness of the setting-selection rule on a single earlier night.
- Early error of the projected share against the final (points): 1% counted -2.4 (raw tally +7.4), 5% -1.3 (-0.7),
  10% -3.1 (-2.7), 25% -0.9 (-3.8). Raw national tally at 22:00 UTC (15% counted): Trzaskowski 45.1%.
- **P(win) Brier, winner race** (model vs Polymarket, minutes with both): by share counted 1-5%: 0.015 vs 0.418 (48
  min); 5-10%: 0.001 vs 0.041 (20); 10-25%: 0.000 vs 0.013 (84); 25-50%: 0.000 vs 0.0009 (68); 50-75%: no minutes (no
  archived version, 22:00-23:11 UTC); 75-95%: 0.000 vs 0.0001 (174). From 1% counted until both settled (95 minutes): model
  0.008, market 0.229. With the 2020-simulation settings: 1-5% 0.087, 5-10% 0.047, 10-25% 0.031, 25-50% 0.035 against
  the market's 0.418 / 0.041 / 0.013 / 0.0009, and 0.046 vs 0.100 over the 221-minute window.
- **All markets** (5 of the 6 races have minutes in the window, 785 minutes): Brier model 0.255, market 0.088,
  difference +0.167 (race-cluster 95% interval -0.096 to +0.351): the margin bands are priced from a normal around an
  over-confident centre.
- Margin bands (Polymarket, 90% on the outcome): "Nawrocki by 0-4" (the winning band) 23:22 UTC, model 00:20; "Nawrocki
  by 4-8" (no) 23:11, model 00:20.
- **Catch-up** (30-minute price move on the model-market gap): Polymarket 0.26 of the gap (95% 0.02 to 0.44; 6 markets,
  461 market-minutes); Kalshi 0.27 (0.02 to 0.49). Both venues closed about a quarter of the gap within half an hour:
  prices followed the model, but the model was over-confident early.

### Runoff, v2: settings pooled over the earlier nights (`pl2025r2_v2`, **a sensitivity, chosen after the result was known**)

v1's overconfidence was seen on the runoff, then the settings were re-chosen from nights before 1 June 2025 only
(`analysis/pl_calibrate_pooled.py`, config `configs/pl2025r2_v2.toml`). The rule was written before the run: pooled
90% coverage = the mean over nights (each equal) of the share of frames, at 1-95% counted, whose 90% band holds the final
share; tau = the smallest value with pooled coverage >= 0.90; kappa and rho = lowest pooled mean absolute error there.
The nights are the two the cell model can be scored on: the real 2025 round-1 night (14 frames) and the 2020 runoff counted
in simulated order (48 frames); no other earlier night has count-night versions (table above). Result: **tau 0.03,
kappa 100,000, rho 0.5**; coverage by tau (night / simulation / pooled): 0.005: 0.93 / 0.00 / 0.46; 0.01: 1.00 / 0.17 /
0.58; 0.02: 1.00 / 0.73 / 0.86; **0.03: 1.00 / 0.83 / 0.92**; 0.04: 1.00 / 0.94 / 0.97. It is not a forward test: v1 stays
the headline, and the two are shown together on the page (second line on the chart, `meta.sensitivity`, `stats.sensitivity`).

| runoff | v1 (round-1 night alone) | v2 (pooled) |
|---|---|---|
| tau / kappa / rho | 0.005 / 100,000 / 0.8 | 0.03 / 100,000 / 0.5 |
| 90% band held the final share (1-95% counted) | **46%** (mean error +1.95 sd) | **100%** (+0.53 sd) |
| model 90% / 95% on Nawrocki (UTC) | 21:28 / 21:32 | 00:20 / 00:20 |
| Polymarket 90% / 95% | 22:14 / 23:12 | 22:14 / 23:12 |
| model minus Polymarket, minutes at 90% / 95% | -46 / -100 (model first) | +126 / +68 (model after) |
| error of the projected share at 1 / 5 / 10 / 25% counted (points) | -2.4 / -1.3 / -3.1 / -0.9 | -1.7 / -1.3 / -2.6 / -0.9 |
| Brier model vs market, winner race, by share counted 1-5 / 5-10 / 10-25 / 25-50% | .015 / .001 / .000 / .000 vs .418 / .041 / .013 / .001 | .067 / .026 / .012 / .015 vs the same |
| Brier over the window to both settling (model / market) | 0.008 / 0.229 (95 min) | 0.026 / 0.100 (221 min) |

With bands that held, the model is not ahead of the market: P(Nawrocki) was 88% at the 23:11 UTC version (49% counted) and
crosses 90% only at the 00:19 version (81% counted), which the archive's gap makes the next available one. The 46-minute
lead of v1 was bought with bands that were too narrow.

### Round 1 (`pl2025r1`): Trzaskowski 6,147,797, Nawrocki 5,790,804 (51.50% of their two-way vote; Trzaskowski wins the most votes)

- Polymarket had Trzaskowski at 95% before the count started, was down to 62% at 01:37 UTC (57 minutes below 90%
  between 19:05 and 01:45 UTC), and settled (>= 90% to the end) at 01:46 UTC (95%: 02:04); Kalshi 02:08. The model, with the conservative simulation settings, was at 0.3-0.7 until the
  version at 03:15 UTC and settled at **03:16 UTC, 90 minutes after Polymarket** (raw tally also at 03:15). The 90% band
  held the final share in 100% of minutes (mean error +0.36 sd): too wide, as intended by the settings, and 2h16
  of the night (00:59-03:15 UTC, 56% counted) has no archived version, so the model sat at P = 0.72 meanwhile.
- P(win) Brier by share counted (model vs market): 1-5% 0.386 vs 0.002, 5-10% 0.224 vs 0.001, 10-25% 0.221 vs 0.004, 50-75%
  0.081 vs 0.016.
- Early error: 1% counted -0.7 (raw +5.8), 5% -1.1 (+0.1), 10% -2.0 (-1.8), 25% -0.5 (-3.1).

### Not computed
- Profit / edge audit (no trades fetched, no frozen trading rule), capacity, Polymarket-Kalshi arbitrage (no pairs
  config). The dashboard shows them as not computed with the reason.

## Files

- **Code**: `src/brunder/poland.py` (decoder, classes, cell state, projection), `analysis/pl_fetch.py`, `pl_calibrate.py`,
  `pl_score.py`, `pl_export.py`, `pl_geo.py`; tests: `tests/test_poland.py`.
- **Configs**: `configs/pl2025r2_v1.toml`, `pl2025r1_v1.toml`, `pl2025r2_sim2020_v1.toml`, `pl2025r2_v2.toml` (v2: chosen
  after the result was known; none registered). Round 1 has no v2: its settings already come from earlier-election
  data only and the 2025 round-1 night may not be used for it.
- **Site**: `site/data/pl2025r2/`, `site/data/pl2025r1/` (`meta`, `timeline`, `units`, `stats`, `entry`),
  `site/data/geo/pl_voivodeships.topo.json` (16 voivodeships, Natural Earth, 10 kB), entries in `site/data/elections.json`.
  Timeline: one row per minute from 19:00 to 08:00 UTC; map frames: one per archived version. Abroad and ships are
  counted by the model and shown in the national numbers but are not on the map.

## Limitations

- The unit is the voivodeship; cell votes are reconstructed from marginals, not observed. The reconstruction treats the
  voivodeship effect and the class effect as additive on the logit of the counted share and multiplicative on votes.
- Gmina classes are rebuilt from electorate rank (the csv has no residents): the three largest classes are exact, the
  smaller ones within 1%.
- The archive's gaps (see above) limit what the replay can say about the middle and end of both nights. Settle times
  are therefore upper bounds on what a continuous feed would have shown.
- Settings come from one earlier night (round 1) or a simulation; the runoff's 90% band missed the final share in more
  than half the minutes. Prior: Duda stands in for Nawrocki, a candidate with a different electorate.
- The registered electorate of a unit is PKW's constant `votersCount`; the electorate counted exceeds it by 1.8% at
  the end, so f is capped at 1.
- The count licence is not settled (see "Sources and licences").


## Tau mixture (retrospective sensitivity)

Both Polish rounds carry a `mix` line: the base model (rho 0.8, kappa 50,000) with the uncertainty mixed over the reporting-order errors that earlier nights needed (round 1: five nights; runoff: six, including round 1), chosen after the results were known. On the runoff the 90% band holds the final share in 77% of minutes (v1 46%, pooled v2 100%) and the model reaches 90% on the winner 42 minutes before Polymarket (median of 3 contested markets; v1 +46, v2 -126). The band is still short of 90%. Margin-band markets use the same mixture. See `docs/model_review.md` section 8.
