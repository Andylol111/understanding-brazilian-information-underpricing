# Model review: calibration across every replay, failure modes, ranked improvements, and a prototype

Written 2026-10-31. Analysis and prototype only: no published site data, config, registry entry or `nowcast.py` was changed.
Everything here is **retrospective** ("not pre-registered"); anything chosen after a result was seen is called a sensitivity.

## Summary

1. **Calibration is not stable from one night to the next.** The smallest reporting-order error `tau` that gives a 90% band
   that holds on a night ranges from **0 to 0.03** on the nine real-order nights we can rebuild (Brazil 2022 R1 needs 0,
   Mexico 2024 needs 0.03). Every published replay picked one `tau` from one or two earlier nights. That is the main
   reason the record flips between "overconfident and early" (Poland runoff: coverage 0.46, model 46 minutes ahead) and
   "safe and late" (the same night with settings pooled on two nights: coverage 1.00, model 126 minutes *behind*).
2. **"Small and rural units report first" is not what the data show, except mildly in Poland.** The raw early tally is wrong mostly
   because of *which areas* report first (their prior lean), not their size. The prior already absorbs that. What is left is
   (a) between-unit swing that correlates with report time in Mexico, (b) late sections inside already-partly-counted
   municipalities in Brazil (+0.3 to +0.7 pt at 25% counted), (c) the cells' hidden composition in Poland (+2.8 to +3.0 pt), and
   (d) a target mismatch in Mexico (PREP ends 0.5 pt below the official count).
3. **Simulated count orders are not reliably gentler than real ones.** On the *same* Brazilian and Mexican elections the
   simulated order needs a smaller `tau` on 4 of 7 nights and the same on 3; never a larger one. In Poland the simulated order
   was 8 times *harsher* than the real night (`tau` 0.04 vs 0.005). The direction of the mistake differs by country, so a simulation cannot stand in for a real night.
   Choosing `tau` on the simulated versions of the other six nights gives half the `tau` of the real versions (mean 0.014 vs 0.028)
   and holds the real night's final share in 89% of frames instead of 94%.
4. **Ranked improvements** (section 4): (1) make `tau` a distribution learned across nights, (2) calibrate on real-order nights only and
   log each night's own `tau`, (3) model the gap between the count and the target explicitly, (4) a live within-unit order drift
   term, (5) a live slope on size or prior lean, (6) a reporting-time covariate learned from earlier nights, (7) inflate by the
   dispersion of swing, (8) effective-sample-size honesty (blocked coverage). Items 1 and 5 are prototyped here.
5. **Prototype `src/brunder/nowcast_order.py`** (new module, `nowcast.py` untouched, 13 tests). Leave-one-election-out on nine
   real-order nights:
   - **`tau` mixture (item 1)**: coverage of the 90% band 0.91 (one point `tau` by the repo's rule: 0.96), mean band width 4.5 pt (7.0), Winkler
     interval score 5.2 pt (7.3), Brier of P(outcome) 0.042 (0.056). On the minute-level targets it gets back the timing of the *published* v1
     replays with far better coverage: Poland runoff +47 min at coverage 0.77 (published v1: +47 min at 0.39; pooled settings: −126 min at 1.00),
     Brazil R1 +35 min (+20 on this harness with the published settings, 28 on the dashboard; −29 for the repo rule), Brazil runoff +80 min (same). Mexico 2024 does not move (it is a target-mismatch night).
   - **Order slope (item 5)**: mean absolute error −4% pooled (0.95 → 0.92 pt) and −16 to −18% on Mexico 2024, but the narrower bands that
     appear under the repo's selection rule come from one boundary effect (a training night at coverage 0.73 vs 0.80 against a 0.75 floor), and
     disappear at floors of 0.70 or 0.90. It is **not** the top item. Evidence is in section 5.

## 1. What was reviewed, and how

- **Published numbers**: `site/data/*/stats.json` (calibration, early_error, settle, lead_contested, market_classification, every
  `sensitivity*` block), `docs/*.md` calibration sections, `docs/us_*.md` backtests, `data_public/us_*backtest*`. Table built by
  `analysis/model_review_table.py` (writes `data_public/model_review_table.csv`).
- **Recomputed from raw data** on the SSD (`$BRUNDER_DATA`): nine real-order count nights replayed with the repo's own loaders
  (`analysis/mr_nights_{br,mx,pl}.py`, which wrap `night.py`, `bweb.py`, `mexico.py`, `poland.py`): Brazil 2022 R1 and R2, Brazil 2026 R1 and R2,
  Mexico 2012, 2018 and 2024, Poland 2025 R1 and R2. The harness reproduces the published numbers (Brazil 2026 R1 coverage 0.939, Mexico 2024 0.102 / z +3.30,
  Poland runoff 0.385 on 13 versions vs 0.464 on 394 minutes of held versions; model settle times within a minute of the published ones).
- **Not replayable here** (no count history or raw data not on the SSD): Netherlands, Canada, Quebec (no count model), New Zealand (simulated, no prices),
  Taiwan and US 2026 (forward), Hungary / Germany / Israel (reviewed from their published stats and docs only).
- **Conventions**: coverage = share of ticks with 1% < counted ≤ 95% whose 90% band holds the official final share; z = (final − projected) / sd, so **z > 0 means the model
  projected candidate A too low**; "lead" = market's settle time minus model's, positive = model first, contested markets only (those *decided before the count* are excluded).

## 2. Cross-election table

Per election and model version, from the published stats (values re-aggregated over the races of an election; "–" = not published).
MAE = mean absolute error of the projected share, in points, at the first minute with 1 / 5 / 10 / 25 / 50% counted; raw-tally MAE shown for comparison.

| election | version | 90% cov | mean z | MAE pt at 1 / 5 / 10 / 25 / 50% counted | raw-tally MAE at 1 / 5 / 10 / 25% | lead on contested, min (from 1% gate) |
|---|---|---:|---:|---|---|---:|
| br2022r1 | v1, headline (national) | 1.00 | 0.00 | 2.00 / 0.41 / 0.24 / 0.22 / – | 6.74 / 6.51 / 5.84 / 5.24 | – |
| br2022r1 | v2 (sens.), retrospective sensitivity | 1.00 | – | 1.75 / 0.16 / 0.04 / 0.26 / 0.17 | – / – / – / – | – |
| br2022r2 | v1, headline (national) | 1.00 | 0.19 | 0.76 / 0.36 / 0.00 / 0.19 / – | 7.50 / 4.00 / 2.92 / 2.15 | – |
| br2022r2 | v2 (sens.), retrospective sensitivity | 0.71 | – | 0.58 / 0.24 / 0.11 / 0.25 / 0.30 | – / – / – / – | – |
| br2026r1 | v1, headline (national) | 0.94 | 0.91 | 1.36 / 0.22 / 0.54 / 0.80 / – | 2.02 / 4.01 / 4.57 / 4.36 | 28 |
| br2026r1 | v2 (sens.), retrospective sensitivity | 0.28 | – | 1.44 / 0.12 / 0.60 / 0.86 / 0.65 | – / – / – / – | 46 |
| br2026r1 | v1, state/governor races pooled | 0.85 | 0.47 | 3.67 / 1.81 / 1.64 / 1.11 / – | 5.53 / 3.37 / 3.12 / 2.46 | 48 (38) |
| br2026r2 | v1, headline (national) | 1.00 | -0.20 | 1.20 / 1.10 / 0.07 / 0.00 / – | 0.87 / 0.65 / 1.30 / 2.28 | 80 |
| br2026r2 | v2 (sens.), retrospective sensitivity | 1.00 | – | 1.15 / 0.88 / 0.08 / 0.19 / 0.01 | – / – / – / – | 198 |
| br2026r2 | v1, state/governor races pooled | 0.95 | -0.20 | 2.95 / 2.29 / 2.04 / 1.43 / – | 5.93 / 4.76 / 3.85 / 2.53 | 76 (80) |
| br2026r2gov | v1, state/governor races pooled | 0.99 | -0.19 | 1.68 / 1.37 / 1.19 / 0.96 / – | 2.20 / 2.18 / 1.79 / 1.99 | 32 (32) |
| pl2025r1 | v1, headline (winner race) | 1.00 | 0.36 | 0.67 / 1.08 / 2.04 / 0.48 / – | 5.83 / 0.12 / 1.82 / 3.06 | -90 |
| pl2025r2 | v1, headline (winner race) | 0.46 | 1.95 | 2.43 / 1.34 / 3.08 / 0.90 / – | 7.42 / 0.74 / 2.65 / 3.79 | 46 |
| pl2025r2 | v2 (sens.), pooled settings, chosen after result | 1.00 | 0.53 | 1.66 / 1.33 / 2.58 / 0.94 / – | 7.42 / 0.74 / 2.65 / 3.79 | -126 |
| pl2025r2 | v1b (sens.), 2020-simulation settings | 1.00 | 0.41 | – / – / – / – / – | – / – / – / – | -126 |
| mx2024 | v1, vs the official cómputo | 0.10 | 3.30 | 1.86 / 0.42 / 0.59 / 1.51 / 1.55 | 0.29 / 1.56 / 2.53 / 2.90 | – |
| mx2024 | v2 (sens.), vs the official cómputo | 0.14 | 2.13 | 1.75 / 0.38 / 0.66 / 1.51 / 1.50 | 0.29 / 1.56 / 2.53 / 2.90 | – |
| mx2024_qc | v1+quick count, PREP + INE quick count | 0.11 | 3.18 | 1.86 / 0.43 / 0.59 / 1.51 / – | 0.29 / 1.56 / 2.53 / 2.90 | – |
| hu2026 | v1, district-list/seats pooled | 1.00 | 0.04 | 6.21 / 3.03 / 1.79 / 0.86 / – | 8.60 / 5.25 / 3.40 / 1.58 | – (32) |
| hu2026 | v2 (sens.), settings from 2022/2018/2014, after result | 1.00 | 0.04 | 6.21 / 3.03 / 1.79 / 0.86 / – | 8.60 / 5.25 / 3.40 / 1.58 | 14 |
| de2025 | v1, 8 parties / thresholds | 0.98 | 0.03 | 1.83 / 1.01 / 1.08 / 0.95 / – | 4.64 / 2.34 / 2.03 / 1.50 | -189 (-189) |
| de2025 | v2 (sens.), kappa 3e7, after result | 0.98 | 0.01 | 1.83 / 1.02 / 1.08 / 0.96 / – | 4.64 / 2.34 / 2.03 / 1.50 | -86 |
| il2026 | v1, list shares (MAE, vs provisional outcome) | 0.76 | 0.05 | 0.81 / 0.45 / 0.60 / 0.45 / – | 1.90 / 1.98 / 1.53 / 1.06 | 39 (-62) |

| election | order | unit | prior | settings (chosen on) |
|---|---|---|---|---|
| br2022r1 | real (BU-web receipt times) | municipality, 5,708 (sections within) | 2018 R1 by municipality (Haddad vs Bolsonaro as proxy) | 0.015 (2026 R1 dev night, frozen before 2022 files were downloaded) |
| br2022r2 | real (BU-web receipt times) | municipality, 5,708 | 2022 R1 by municipality | 0.015 (2026 R1 dev night (frozen before)) |
| br2026r1 | real (BU-web receipt times, rebuilt) | municipality, 5,708 | 2022 R1 by municipality | 0.015 (this night itself (development set: in-sample)) |
| br2026r2 | real (BU-web receipt times, rebuilt) | municipality, 5,716 | 2026 R1 by municipality | 0.015 (2026 R1 (earlier election, but result of R2 already known when frozen)) |
| br2026r2gov | real (BU-web receipt times, rebuilt) | municipality (DF: one unit) | own 2026 R1 governor result by municipality | 0.015 (presidential R1 dev night (never developed on governors)) |
| pl2025r1 | real archived versions (31) but model settings from a SIMULATED night | voivodeship x gmina-size class cells (128), counted marginals only | 2020 R1 by cell (Duda stands in for Nawrocki) | 0.04 (rho 0.5, kappa 1e5) (2020 runoff counted in simulated smallest-first order (48 frames)) |
| pl2025r2 | real archived versions (31 per night, 14 in 1-95%) | voivodeship x gmina-size class cells (128) | 2020 runoff by cell | 0.005 (rho 0.8, kappa 1e5) (2025 R1 real night alone (14 frames)) |
| mx2024 | real (acta verification times, 5-min PREP cuts) | electoral section, 70,537 | 2018 cómputo by section | tau 0.01 (2018 night alone) |
| mx2024_qc | real (PREP) + INE quick count | electoral section, 70,537 | 2018 cómputo by section | 0.01 (2018 night alone; quick count combined by inverse variance) |
| hu2026 | real official feed (40 publications, 10-min) | OEVK district 106 (list: 20 counties pooled) | 2022 district votes on 2024 map | district 0.0, list 0.05 (2022 night (9 archived frames)) |
| de2025 | real archived pages (129 distinct minutes; between pages the state is held) | Wahlkreis 299 (177 individually dated, rest via Land/national totals) | 2021 result on 2025 Wahlkreise | sd_a 0.2, sd_b 0.9, kappa 3e6 (own module, not nowcast.py) (2021 night only) |
| il2026 | real CEC releases (18) via recorder/mirror | locality ~1,200 + double envelopes | 2022 Knesset by locality | 0.01 (2021 + 2022 Wayback captures (23)) |
| nl2025 | none (no timestamped count) | national list shares | 2023 result + final polls | n/a (2017/2021/2023 poll errors) |
| ca2025 | none (6 page captures) | 343 districts (not modelled) | 2021 transposed | n/a (n/a) |
| qc2026 | none (3 captures) | 127 circonscriptions (not modelled) | none | n/a (n/a) |
| nz2023 | simulated (six orders; 'night' for the page) | electorate 72 | 2020 electorates | n/a (seat model) (n/a; no prices exist) |
| nz2020 | simulated | electorate 72 | 2017 electorates | n/a (seat model) (in-sample) |


Reading it:

- **Overconfident**: Poland runoff v1 (0.46), Mexico 2024 v1 and v2 (0.10, 0.14; against the PREP's own end 0.40 and 0.81), Brazil 2026 R1 v2 (0.28), Brazil 2022 R2 v2 (0.71),
  Israel (0.76, partly an outcome artefact), Brazil 2026 R1 state races (mean 0.85: Piauí 0.12, Bahia 0.52, Espírito Santo 0.54, Mato Grosso do Sul 0.55, Pará 0.57, the
  last one contested), Brazil runoff Amapá 0.38 (contested, mean z −1.5).
- **Conservative** (bands that held in 98 to 100% of minutes): Poland round 1 (`tau` from a simulated night) and runoff v2 (pooled hard night), Germany, Hungary, Brazil 2022 and the governor runoffs.
  Conservative is not free: it is where the model is late (Germany −189 min, Poland R1 −90, Poland runoff v2 −126).
- **Bias direction**: on the nine rebuilt nights the model at `tau` 0 has mean z above +1 on six (Poland runoff +2.7, Mexico 2024 +6.0, Brazil 2026 R1 +2.4, Mexico 2018 +1.8, Poland R1 +1.3, Brazil 2022 R2 +1.3), near 0 on one (Brazil 2022 R1) and negative on two (Mexico 2012 −4.0, Brazil 2026 runoff −0.7). In the published tables 15 of 19 versions have mean z > 0, and the three negative ones are Brazil's 2026 runoff and governors (≈ −0.2). Positive means the model projected A (the candidate it tracks: Lula, Trzaskowski, Sheinbaum) too low: the votes still to come lean more to A than the model expects.
- **The model is not always better than the raw tally at the very start.** At 1% counted it is worse in Mexico 2024 (1.86 vs 0.29 pt), and at 5% worse in Poland round 1 (1.08 vs 0.12) and Brazil's 2026 runoff (1.10 vs 0.65).
  Where the first returns are representative, the prior-heavy start costs accuracy. Elsewhere it is 3 to 8 times better (Brazil 2022 R1: 2.00 vs 6.74 pt at 1%).
- **Leads** (contested markets): Poland runoff +46, Brazil R1 +28, Brazil runoff +80, Brazil governors +32, Hungary +32 (gated), Israel +39 (−62 gated), Poland R1 −90, Germany −189.
- **US**: every count-time backtest uses simulated orders (Georgia 2022/2024 for tau; the national and Hispanic-slope backtests too); `tau` 0.04 to 0.08 was set by analogy for most races (Brazil's was 0.015), and
  the Hispanic slope (v5) cut MAE only 2 to 6% with coverage 99 to 100% for both models, so the 85 to 95% test could not be applied (`docs/us_hisp_slope.md`, `docs/us_midterms.md`). The 2026 count has not happened.

## 3. Failure modes, with evidence

### F1. One or two nights cannot give `tau`; the night-to-night spread is as big as the value

| night | tau needed, real order (frames) | coverage at tau 0 | mean z at tau 0 | MAE pt (tau-free) | tau needed, simulated order, same election | mean z at tau 0, simulated | MAE pt, simulated |
|---|---:|---:|---:|---:|---:|---:|---:|
| br2022r1 | 0 | 1.00 | 0.01 | 0.46 | 0 | 0.34 | 0.22 |
| br2022r2 | 0.005 | 0.60 | 1.29 | 0.30 | 0.005 | -3.11 | 0.40 |
| br2026r1 | 0.015 | 0.47 | 2.45 | 0.60 | 0 | -0.41 | 0.18 |
| br2026r2 | 0.0025 | 0.87 | -0.69 | 0.38 | 0.0025 | -2.21 | 0.31 |
| mx2012 | 0.015 | 0.33 | -4.00 | 0.96 | 0 | 0.67 | 0.44 |
| mx2018 | 0.02 | 0.43 | 1.78 | 1.33 | 0.0075 | 2.09 | 0.93 |
| mx2024 | 0.03 | 0.40 | 6.04 | 1.14 | 0.02 | -2.36 | 1.53 |
| pl2025r1 | 0.0075 | 0.71 | 1.32 | 1.75 | – | – | – |
| pl2025r2 | 0.02 | 0.31 | 2.73 | 1.52 | – | – | – |

- `tau needed` = smallest grid value with 90% coverage on that night alone (base model, rho 0.8, kappa 50,000, frames at 1 to 95% counted).
- The same model needs 0 on Brazil 2022 R1 and 0.03 on Mexico 2024. Poland needs 0.0075 on round 1 and 0.02 on the runoff.
- Where `tau` came from, and what happened: Mexico 2024 from 2018 alone (0.01; needs 0.03; coverage 0.10), Poland runoff from round 1 alone (0.005; needs 0.02; coverage 0.46),
  Brazil 2026 v2 from four simulated and two real nights (0.005; needs 0.015; coverage 0.28), Hungary v1 from 2022 alone (district `tau` 0; national district share inside its band in 54% of simulated 2018 frames).
  The Brazil 2026 R1 v1 `tau` (0.015) was set *on that night* (in-sample), which is why it looks right there.
- Frames are highly correlated inside a night (Poland round 1: 14 versions at 1 to 95%; Hungary 2022: 9 archived frames; Israel 2022: two captures under 50%), so a coverage of 1.00 or 0.93 from one night is weak evidence either way.

### F2. Simulated orders are the wrong stand-in, in both directions

- On the same election (rows above, right-hand columns; `analysis/model_review_sim_vs_real.py`): simulated small-section-first order needs a smaller `tau` on Brazil 2026 R1 (0 vs 0.015), Mexico 2012 (0 vs 0.015), 2018 (0.0075 vs 0.02) and 2024 (0.02 vs 0.03), the same on the other three Brazilian nights,
  and the sign of the mean z often differs (Brazil 2026 R1: real +2.45, simulated −0.41; Mexico 2024: real +6.0, simulated −2.4).
- Poland is the reverse (docs/poland.md): the 2020 simulation needed `tau` 0.04, the real 2025 round-1 night 0.005. Hungary's real 2018 national tally was *more* biased than the smallest-first simulation (docs/hungary.md).
- Choosing `tau` on the simulated versions of the *other* six nights (repo rule, 0.90 pooled, 0.75 worst) gives 0.015 (0.005 when Mexico 2024 is held out); on the real versions 0.030 (0.015). Mean over the seven nights: 0.014 vs 0.028. On the real target nights coverage is 0.894 vs 0.943 (Mexico 2024 0.40 vs 0.60, Mexico 2018 0.86 vs 1.00), and the band is 44% narrower (1.1 vs 2.0 pt).
  Seven nights, two of them affected: an indication, not a test.

### F3. Who reports first: area composition, not size, and the prior already pays for it

| night | counted | size imbalance (sd) | lean imbalance (sd) | corr(size, swing) | corr(lean, swing) | raw tally error (pt) | of which composition (pt) |
|---|---|---:|---:|---:|---:|---:|---:|
| br2022r1 | 10% | 0.02 | -0.22 | 0.67 | -0.68 | -5.84 | -5.07 |
| br2022r1 | 25% | 0.01 | -0.21 | 0.67 | -0.71 | -5.24 | -4.81 |
| br2022r2 | 10% | 0.07 | -0.18 | 0.12 | -0.19 | -2.92 | -2.80 |
| br2022r2 | 25% | -0.09 | -0.12 | 0.12 | -0.29 | -2.13 | -1.85 |
| br2026r1 | 10% | 0.07 | -0.28 | 0.46 | -0.29 | -4.57 | -4.29 |
| br2026r1 | 25% | -0.05 | -0.22 | 0.49 | -0.33 | -4.36 | -3.46 |
| br2026r2 | 10% | 0.11 | -0.10 | 0.10 | -0.12 | -1.30 | -1.45 |
| br2026r2 | 25% | -0.07 | -0.14 | 0.21 | -0.29 | -2.28 | -2.10 |
| mx2012 | 10% | -0.19 | -0.01 | 0.04 | -0.56 | 0.86 | -0.27 |
| mx2012 | 25% | -0.06 | 0.05 | 0.05 | -0.55 | 1.65 | 0.87 |
| mx2018 | 10% | -0.04 | 0.04 | 0.11 | -0.15 | -2.85 | 0.56 |
| mx2018 | 25% | 0.01 | 0.11 | 0.10 | -0.18 | 0.04 | 1.68 |
| mx2024 | 10% | -0.16 | -0.13 | -0.12 | -0.18 | -2.53 | -2.21 |
| mx2024 | 25% | -0.01 | -0.09 | -0.12 | -0.14 | -2.90 | -1.54 |
| pl2025r1 | 10% | 0.10 | 0.03 | -0.57 | -0.53 | -0.47 | 2.69 |
| pl2025r1 | 25% | -0.09 | -0.12 | -0.56 | -0.47 | -2.60 | -0.77 |
| pl2025r2 | 10% | -0.06 | -0.17 | 0.02 | -0.05 | -2.65 | -0.25 |
| pl2025r2 | 25% | -0.21 | -0.28 | 0.05 | -0.03 | -3.97 | -2.04 |

- `size imbalance` < 0 means small units are over-represented among counted votes. It is near 0 in Brazil (0.02 to 0.11 at 10%) and Mexico (−0.19 to −0.04), and mildly negative in Poland's runoff (−0.06 at 10%, −0.21 at 25%).
  Time-vs-size order (Spearman): Brazil municipalities complete later when big (+0.36 to +0.42 on all four nights, stable across 2022 and 2026), Mexico sections show none (−0.12 to −0.03).

| night | Spearman(complete time, size) | Spearman(visible time, size) |
|---|---:|---:|
| br2022r1 | 0.37 | – |
| br2022r2 | 0.36 | – |
| br2026r1 | 0.42 | – |
| br2026r2 | 0.42 | – |
| mx2012 | – | -0.12 |
| mx2018 | – | -0.03 |
| mx2024 | – | -0.05 |
| pl2025r1 | – | – |
| pl2025r2 | – | – |

- The raw tally's error is almost entirely **composition by prior lean**: at 10% counted Brazil 2022 R1 raw −5.8 pt, of which −5.1 pt is the prior share of the units counted so far vs all units (columns on the right). States that lean to the right report first.
  The model starts from the prior, so it is already corrected for this, which is why it beats the raw tally 3 to 8 times in Brazil. A size or "rural" term would not have fixed Brazil's early miss: the counted set was not small, it was *right-leaning*.
- Corr(lean, swing) is clearly negative in Brazil (−0.1 to −0.7 at 10%), Mexico 2012 (−0.55) and Poland round 1 (−0.5): units that leaned to A before swung less than units that leaned away. That is mean reversion; the live slope on lean (section 5) is the cheap way to use it.

### F4. What is left after composition: where the residual miss sits

| night | counted | between units (pt) | within partly counted units (pt) | model miss (pt) | late-minus-early gap inside partly counted units (pt) | share of remaining votes in partly counted units |
|---|---|---:|---:|---:|---:|---:|
| br2022r1 | 10% | -0.82 | 0.30 | -0.52 | 0.09 | 0.58 |
| br2022r1 | 25% | -0.77 | 0.73 | -0.03 | 1.20 | 0.75 |
| br2022r2 | 10% | 0.30 | -0.04 | 0.25 | -0.27 | 0.53 |
| br2022r2 | 25% | 0.15 | 0.29 | 0.44 | 0.45 | 0.74 |
| br2026r1 | 10% | 0.03 | 0.30 | 0.33 | 0.48 | 0.54 |
| br2026r1 | 25% | -0.03 | 0.59 | 0.57 | 0.96 | 0.77 |
| br2026r2 | 10% | 0.11 | -0.04 | 0.07 | -0.06 | 0.46 |
| br2026r2 | 25% | 0.22 | -0.11 | 0.11 | -0.46 | 0.56 |
| mx2012 | 10% | -0.95 | 0.05 | -0.90 | 0.38 | 0.10 |
| mx2012 | 25% | -0.97 | 0.06 | -0.90 | 0.32 | 0.17 |
| mx2018 | 10% | 1.32 | 0.05 | 1.38 | -0.09 | 0.14 |
| mx2018 | 25% | -0.29 | 0.02 | -0.27 | -0.25 | 0.26 |
| mx2024 | 10% | 1.03 | -0.01 | 1.02 | -0.02 | 0.13 |
| mx2024 | 25% | 1.72 | 0.02 | 1.74 | -0.03 | 0.25 |
| pl2025r1 | 10% | 0.00 | 0.36 | 0.36 | 0.04 | 1.00 |
| pl2025r1 | 25% | 0.00 | 0.59 | 0.59 | 0.53 | 1.00 |
| pl2025r2 | 10% | 0.00 | 2.97 | 2.97 | 3.69 | 1.00 |
| pl2025r2 | 25% | 0.00 | 2.77 | 2.77 | 3.65 | 1.00 |

- `between` = units with nothing counted yet; `within` = units already partly counted; both are the model's miss in points of the final share, positive = projected A too low (exact up to the turnout scale; truth = the night's own last state).
- **Mexico 2024**: +1.0 pt at 10% and +1.7 pt at 25% counted, all *between* units. The sections that report later swung more to the left candidate than the early ones, which no regional swing sees. Mexico 2012 is the opposite (−0.9) and 2018 flips sign (+1.3 at 10%, −0.3 at 25%): the sign is not stable, so a fixed correction would not transport.
- **Brazil**: small *within* terms of +0.3 to +0.7 pt at 25% in 2022 R1/R2 and 2026 R1, with late sections of partly counted municipalities leaning 0.5 to 1.2 pt more to A than the early ones at 25%. It is the within-unit part of "urban boxes last", visible because Brazil's units are municipalities with sections inside.
  On the 2026 runoff it is the other way (−0.1).
- **Poland runoff**: +2.8 to +3.0 pt, all *within* cells: the unobserved part of each voivodeship x size-class cell (the big cities of a class) votes 3.7 pt more for Trzaskowski than the part already counted. The state is rebuilt from published marginals (docs/poland.md), so this is partly the reconstruction's blind spot: a **granularity limit**.
- The model's `rho` (0.8) carries the counted part's deviation into the remainder of a unit; it has no term for "the remainder differs from the counted part in a known direction".

### F5. Target mismatch: the model can be right about the count and wrong about the result

- **Mexico**: the PREP stops at 93% of actas and ends at 68.02% two-way; the official count gives 68.52% (the 2,302 sections the PREP never counted voted 74% for the winner). Gaps in earlier nights: 0.04 (2012) and 0.24 (2018). Against the PREP's own end the same bands hold in 40% (v1) to 81% (v2) of minutes.
  Our minute-level harness shows the same: 0.13 to 0.15 against the official count and 0.80 to 0.92 against the PREP's end, whatever the settings arm.
- **Israel**: projection includes double envelopes, the provisional outcome does not (coverage 0.76). **New Zealand**: night counts exclude special votes (16 to 21% of the vote), so v1 seat bands cover 0.50 once all ordinary votes are in.
  **Hungary**: the first seat interval (115 ± 6) against a final of 141. These are not order effects and no reporting-order term can fix them.

### F6. Granularity limits what the model can see

- Poland: 16 voivodeships and 8 size classes, never polling districts; 31 versions a night, 14 in the 1 to 95% window, 2h16 of the first round night with no archived version.
- Germany: 177 of 299 Wahlkreise individually dated; between pages the state is held ("flatters the model by the page lag"); in 2021 small rural Wahlkreise reported first, which the model assumes away for unidentified ones.
- Israel: 18 releases in the night; first release at 0.1% counted is outside the calibrated range. Hungary list model at county level was no better than the raw count early (docs/hungary.md).
- Brazil governors: Distrito Federal is one municipality, so the whole uncertainty is within-unit order.
- The units that matter (polling stations) exist in every country's final data but not in its live feed.

### F7. State and sub-race markets are not calibrated by the national `tau`

Brazil 2026 R1: national coverage 0.94, but 11 of 28 races below 0.90 (contested ones: Pará 0.57, Amazonas 0.77, Tocantins 0.86; mean contested 0.84). Runoff: 3 of 28 below 0.90 (Amapá 0.38, Maranhão 0.68, Mato Grosso do Sul 0.87; contested mean 0.93).
The state markets share one night's order and are not independent, and their units (municipalities of one state) are fewer and more varied than the nation's.

## 4. Ranked improvements

Ranked by (evidence that it addresses an observed failure) x (expected effect) / (cost). "Test without look-ahead" means the parameter is fixed using only earlier nights (chronological) or other nights (leave one election out, labelled as such).

| # | improvement | observed failure it targets | expected effect | how to test without look-ahead | status |
|---|---|---|---|---|---|
| 1 | **`tau` as a distribution learned across nights** (equal-weight mixture over each earlier night's needed `tau`, then a hierarchical prior as nights accumulate) | F1, F2: one point `tau`, spread 0 to 0.03 | the repo's two bad corners (early + overconfident, safe + late) collapse to one: same timing as the published v1 with the coverage of the pooled settings. Pooled: band width −36%, interval score −29%, Brier −25%, coverage at the nominal 0.90 | chronological and leave-one-election-out (section 5); then forward on the next nights (NZ, Taiwan, US 2026, Bulgaria) with the mixture frozen before the night | **prototyped, evaluated** |
| 2 | **Calibrate on real-order nights only; log each night's own `tau`; tag every setting with the number and type of nights behind it** | F2, F1 | removes the 50% `tau` shortfall from simulated nights; stops one-night selections being presented as calibrated | per country, compare held-out coverage of `tau` from real vs simulated versions (F2 table: 0.94 vs 0.89) | partly evaluated (F2) |
| 3 | **Count-to-target gap term** for sources that stop short (PREP, provisional counts, special votes): an explicit N(0, sigma_gap²) on the final margin, sigma_gap from earlier nights' (end-of-count − official) | F5: Mexico 2024, Israel, NZ, Hungary seats | the right coverage when the target is the official result; for Mexico the earlier gaps (0.04, 0.24) predict RMS 0.17 pt, and 2024's 0.50 would still miss: the term cannot be learned from two nights, so say so on the page | chronological over countries with a stated gap; Mexico is the stress test | proposed |
| 4 | **Within-unit order drift, estimated live**: among partly counted units compare the latest increment's share with the earlier part, and apply the (shrunk) drift to remaining votes of partly counted units | F4 Brazil (+0.3 to +0.7 pt national; late sections +0.5 to +1.2 pt), Poland if finer data | removes most of Brazil's positive z at 10 to 50% counted; tau could fall; improves lead on R1-type nights | Brazil 2022 R1/R2 -> 2026 R1/R2 chronological; the estimator uses only receipt times already published | proposed (needs section-level receipt data; Brazil has it) |
| 5 | **Live slope of swing on size or prior lean** (prototyped) | F3: corr(lean, swing) −0.2 to −0.7; size mildly in Poland | small: MAE −4% pooled, −16 to −18% on Mexico 2024; no robust gain in pooled sharpness (below) | chronological and LOEO (section 5) | **prototyped, evaluated: keep as an optional diagnostic** |
| 6 | **Reporting-time covariate learned from the previous night**: each unit's typical arrival rank in the last count (Brazil Spearman with size +0.4 and stable 2022 to 2026) as the covariate of the swing slope | F4 Mexico 2024 (+1.7 pt between units): late-reporting sections swing differently | possibly the Mexican bias; unproven | Mexico 2018 PREP arrival rank -> 2024 sections; Brazil 2022 -> 2026 municipalities | proposed |
| 7 | **Inflate sd by the dispersion of swing among counted units** (and by the live `corr(lean, swing)`) | F7: state races below 0.90 | better state-market coverage; may hurt national sharpness | Brazil states 2022 -> 2026; check mean |z| | not tested |
| 8 | **Effective-sample honesty**: report coverage with a blocked interval (nights, not frames) and publish `tau` with its night count | F1 (14 correlated frames) | none on accuracy; prevents over-reading a 1.00 | n/a | proposed |

## 5. Prototype: `src/brunder/nowcast_order.py`

New module; imports `brunder.nowcast`, does not edit it. 13 tests (`tests/test_nowcast_order.py`).

**Part 1, hierarchical tau (the top item).**
`needed_tau(err, margin_sd0, two_r, total)` is a night's own `tau` (smallest grid value with 90% coverage); `mixture_pwin` and `mixture_band` turn the `tau` of earlier nights into the
equal-weight mixture of normals `N(share_hat, sd(tau_j))`: P(A wins) = mean of Phi(margin / sd_j), band = mixture quantiles. `with_tau` re-scales a run to another `tau` (the `tau` term is additive in the margin variance, tested against a direct run).
Nothing needs the night being scored.

**Part 2, live order slope.** `order_nowcast` wraps `nowcast.nowcast` with the existing `hisp_slope` machinery (built for the US Hispanic slope) on a prior-only covariate: `size` (log size, standardised; Poland uses the size class's mean gmina electorate because a cell is a pool of many reporting units) or `lean` (prior share).
`order_imbalance` and `swing_correlation` are the live diagnostics of F3.

**Protocol** (`analysis/model_review_frames.py`, `model_review_loeo.py`, `model_review_tau_mix.py`, `model_review_targets.py`): frames at the first minute 1, 2, 3, 5, ... 95% of the votes are counted (Poland: each archived version); each night weighted equally.
Held-out night X: settings chosen on the other nights (LOEO, which uses later nights too and so is a robustness check) or only on earlier nights (chronological, no look-ahead; Mexico 2012 and 2018 are the first two nights, so the chronological scheme scores seven). Rule (fixed before the run, same for every family):
feasible = pooled coverage ≥ 0.90 and worst training night ≥ 0.75 (the repo's rule); "repo" = smallest feasible `tau` at rho 0.8 / kappa 50,000 then lowest MAE; "sharp" = narrowest mean band among feasible choices.

### 5.1 Frame level, all nine nights

| scheme | arm | held-out nights | mean coverage | worst coverage | MAE pt | mean band sd pt | mean z |
|---|---|---:|---:|---:|---:|---:|---:|
| chronological | base, repo rule | 7 | 0.94 | 0.60 | 0.89 | 1.76 | 0.36 |
| chronological | + lean slope | 7 | 0.92 | 0.60 | 0.87 | 1.38 | 0.46 |
| chronological | base, sharpness rule | 7 | 0.94 | 0.60 | 0.89 | 1.75 | 0.36 |
| chronological | + size or lean slope | 7 | 0.92 | 0.60 | 0.87 | 1.38 | 0.46 |
| chronological | + size slope | 7 | 0.92 | 0.60 | 0.87 | 1.38 | 0.49 |
| loeo | base, repo rule | 9 | 0.96 | 0.60 | 0.99 | 2.10 | 0.29 |
| loeo | + lean slope | 9 | 0.94 | 0.60 | 0.93 | 1.46 | 0.31 |
| loeo | base, sharpness rule | 9 | 0.96 | 0.60 | 0.95 | 2.04 | 0.28 |
| loeo | + size or lean slope | 9 | 0.94 | 0.60 | 0.93 | 1.47 | 0.30 |
| loeo | + size slope | 9 | 0.94 | 0.60 | 0.92 | 1.47 | 0.34 |

Sensitivity to the worst-night floor (LOEO, sharpness rule; the repo's floor is 0.75):

| worst-night floor | cov (base) | cov (size/lean slope) | sd (base) | sd (size/lean slope) | mae (base) | mae (size/lean slope) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.91 | 0.90 | 1.19 | 1.19 | 0.95 | 0.94 |
| 0.50 | 0.91 | 0.90 | 1.19 | 1.19 | 0.95 | 0.94 |
| 0.60 | 0.91 | 0.90 | 1.19 | 1.19 | 0.95 | 0.94 |
| 0.70 | 0.93 | 0.94 | 1.47 | 1.47 | 0.93 | 0.93 |
| 0.75 | 0.96 | 0.94 | 2.04 | 1.47 | 0.95 | 0.93 |
| 0.80 | 0.96 | 0.94 | 2.06 | 1.48 | 0.96 | 0.94 |
| 0.90 | 0.96 | 0.96 | 2.06 | 2.06 | 0.96 | 0.94 |

The slope family is narrower than the base only at floors 0.75 and 0.80: at 0.75 one training night (Mexico 2024) reaches coverage 0.80 with the slope at `tau` 0.02 but 0.73 without it (11 of 15 vs 12 of 15 frames).
At every other floor the two families are identical to two decimals. So the slope is **not shown to help** the selection; its MAE gain (−4% pooled; −16 to −18% on Mexico 2024) is real but small.

### 5.2 Hierarchical tau, frame level (base model)

| scheme | arm | held-out nights | mean coverage | worst coverage | mean 90% band width pt | Winkler interval score pt | Brier of P(outcome) |
|---|---|---:|---:|---:|---:|---:|---:|
| chronological | max of training needs | 7 | 0.96 | 0.73 | 6.50 | 6.59 | 0.07 |
| chronological | mixture of training needs | 7 | 0.90 | 0.53 | 4.61 | 5.37 | 0.05 |
| chronological | point (repo rule) | 7 | 0.94 | 0.60 | 6.08 | 6.42 | 0.06 |
| loeo | max of training needs | 9 | 0.97 | 0.73 | 7.11 | 7.18 | 0.06 |
| loeo | mixture of training needs | 9 | 0.91 | 0.53 | 4.46 | 5.15 | 0.04 |
| loeo | point (repo rule) | 9 | 0.96 | 0.60 | 7.00 | 7.26 | 0.06 |

Per held-out night (LOEO):

| night | cov: max of training needs | cov: mixture of training needs | cov: point (repo rule) | Winkler: max of training needs | Winkler: mixture of training needs | Winkler: point (repo rule) |
|---|---:|---:|---:|---:|---:|---:|
| br2022r1 | 1.00 | 1.00 | 1.00 | 7.43 | 4.86 | 7.43 |
| br2022r2 | 1.00 | 1.00 | 1.00 | 6.83 | 4.03 | 6.83 |
| br2026r1 | 1.00 | 1.00 | 1.00 | 6.94 | 4.02 | 6.94 |
| br2026r2 | 1.00 | 1.00 | 1.00 | 6.80 | 3.98 | 6.80 |
| mx2012 | 1.00 | 1.00 | 1.00 | 6.91 | 3.97 | 6.91 |
| mx2018 | 1.00 | 0.86 | 1.00 | 7.54 | 4.50 | 7.54 |
| mx2024 | 0.73 | 0.53 | 0.60 | 5.47 | 6.60 | 6.21 |
| pl2025r1 | 1.00 | 1.00 | 1.00 | 10.35 | 7.58 | 10.35 |
| pl2025r2 | 1.00 | 0.77 | 1.00 | 6.36 | 6.87 | 6.36 |

The mixture has the better interval score on seven of nine nights, and a lower coverage on three of them (Mexico 2018 0.86, Poland runoff 0.77, Mexico 2024 0.53, the last two with a worse score as well: Mexico 2024 6.6 vs 6.2, Poland runoff 6.9 vs 6.4). It is calibrated on average (0.91), not on every night: no `tau` could be on Mexico 2024.

### 5.3 Minute level on the four targets (1-minute ticks; Mexico 3-minute; Poland each archived version)

Settle = first tick from which P(eventual outcome) ≥ 0.9 to the end of the count; lead = Polymarket's published settle minus the model's, measured from the 1% gate, positive = model first. Mexico 2024 was decided in the market before the count, so there is no contested market.
"Published v1 settings" is the reference: it reproduces the dashboard (Brazil R1 coverage 0.94, lead +20; Mexico 0.10).

| night | arm | settings from | slope | tau | 90% coverage | mean z | MAE pt | mean band sd pt | model P≥0.9 for good (UTC) | lead over Polymarket, min |
|---|---|---|---|---|---:|---:|---:|---:|---|---:|
| pl2025r2 | published v1 settings (reference) | reference | – | 0.005 | 0.38 | +1.99 | 1.51 | 0.92 | 21:27 | +47 |
| pl2025r2 | base, repo rule | loeo | – | 0.03 | 1.00 | +0.69 | 1.53 | 1.86 | 00:19 | -126 |
| pl2025r2 | base, repo rule | chronological | – | 0.03 | 1.00 | +0.69 | 1.53 | 1.86 | 00:19 | -126 |
| pl2025r2 | base, sharpness rule | loeo | – | 0.03 | 1.00 | +0.69 | 1.53 | 1.86 | 00:19 | -126 |
| pl2025r2 | base, sharpness rule | chronological | – | 0.03 | 1.00 | +0.69 | 1.53 | 1.86 | 00:19 | -126 |
| pl2025r2 | + size slope | loeo | size 0.01 | 0.02 | 0.85 | +0.97 | 1.51 | 1.32 | 21:31 | +43 |
| pl2025r2 | + size slope | chronological | size 0.01 | 0.02 | 0.85 | +0.97 | 1.51 | 1.32 | 21:31 | +43 |
| pl2025r2 | + lean slope | loeo | lean 0.02 | 0.02 | 0.85 | +0.97 | 1.50 | 1.33 | 21:31 | +43 |
| pl2025r2 | + lean slope | chronological | lean 0.02 | 0.02 | 0.85 | +0.97 | 1.50 | 1.33 | 21:31 | +43 |
| pl2025r2 | base, tau mixture | loeo | – | mix of 8 | 0.77 | +1.18 | 1.52 | 1.18 | 21:27 | +47 |
| pl2025r2 | base, tau mixture | chronological | – | mix of 6 | 0.77 | +1.11 | 1.52 | 1.24 | 21:31 | +43 |
| mx2024 | published v1 settings (reference) | reference | – | 0.01 | 0.10 | +3.30 | 0.84 | 0.35 | 02:06 | – |
| mx2024 | base, repo rule | loeo | – | 0.015 | 0.14 | +2.41 | 0.89 | 0.51 | 02:06 | – |
| mx2024 | base, repo rule | chronological | – | 0.015 | 0.14 | +2.27 | 0.84 | 0.47 | 02:06 | – |
| mx2024 | base, sharpness rule | loeo | – | 0.015 | 0.13 | +2.47 | 0.89 | 0.47 | 02:06 | – |
| mx2024 | base, sharpness rule | chronological | – | 0.015 | 0.14 | +2.27 | 0.84 | 0.47 | 02:06 | – |
| mx2024 | + size slope | loeo | size 0.01 | 0.015 | 0.15 | +2.07 | 0.75 | 0.47 | 02:06 | – |
| mx2024 | + size slope | chronological | size 0.01 | 0.015 | 0.14 | +2.32 | 0.81 | 0.47 | 02:06 | – |
| mx2024 | + lean slope | loeo | lean 0.01 | 0.015 | 0.15 | +1.96 | 0.73 | 0.47 | 02:06 | – |
| mx2024 | + lean slope | chronological | lean 0.01 | 0.015 | 0.15 | +1.96 | 0.73 | 0.47 | 02:06 | – |
| mx2024 | base, tau mixture | loeo | – | mix of 8 | 0.13 | +2.48 | 0.84 | 0.45 | 02:06 | – |
| mx2024 | base, tau mixture | chronological | – | mix of 4 | 0.13 | +2.49 | 0.84 | 0.44 | 02:06 | – |
| br2026r1 | published v1 settings (reference) | reference | – | 0.015 | 0.94 | +0.91 | 0.47 | 0.74 | 21:10 | +20 |
| br2026r1 | base, repo rule | loeo | – | 0.03 | 1.00 | +0.46 | 0.49 | 1.38 | 21:59 | -29 |
| br2026r1 | base, repo rule | chronological | – | 0.03 | 1.00 | +0.46 | 0.49 | 1.38 | 21:59 | -29 |
| br2026r1 | base, sharpness rule | loeo | – | 0.03 | 1.00 | +0.48 | 0.47 | 1.36 | 21:59 | -29 |
| br2026r1 | base, sharpness rule | chronological | – | 0.03 | 1.00 | +0.48 | 0.47 | 1.36 | 21:59 | -29 |
| br2026r1 | + size slope | loeo | size 0.01 | 0.02 | 1.00 | +0.80 | 0.52 | 0.93 | 21:19 | +11 |
| br2026r1 | + size slope | chronological | size 0.01 | 0.02 | 1.00 | +0.80 | 0.52 | 0.93 | 21:19 | +11 |
| br2026r1 | + lean slope | loeo | lean 0.01 | 0.02 | 1.00 | +0.74 | 0.48 | 0.93 | 21:22 | +8 |
| br2026r1 | + lean slope | chronological | lean 0.01 | 0.02 | 1.00 | +0.74 | 0.48 | 0.93 | 21:22 | +8 |
| br2026r1 | base, tau mixture | loeo | – | mix of 8 | 0.99 | +0.84 | 0.47 | 0.78 | 20:55 | +35 |
| br2026r1 | base, tau mixture | chronological | – | mix of 7 | 1.00 | +0.79 | 0.47 | 0.83 | 21:01 | +29 |
| br2026r2 | published v1 settings (reference) | reference | – | 0.015 | 1.00 | -0.20 | 0.33 | 1.04 | 20:06 | +80 |
| br2026r2 | base, repo rule | loeo | – | 0.03 | 1.00 | -0.08 | 0.31 | 1.98 | 23:31 | -7 |
| br2026r2 | base, repo rule | chronological | – | 0.03 | 1.00 | -0.08 | 0.31 | 1.98 | 23:31 | -7 |
| br2026r2 | base, sharpness rule | loeo | – | 0.03 | 1.00 | -0.11 | 0.35 | 1.97 | 23:31 | -7 |
| br2026r2 | base, sharpness rule | chronological | – | 0.03 | 1.00 | -0.11 | 0.35 | 1.97 | 23:31 | -7 |
| br2026r2 | + size slope | loeo | size 0.01 | 0.02 | 1.00 | -0.16 | 0.38 | 1.34 | 23:14 | +10 |
| br2026r2 | + size slope | chronological | size 0.01 | 0.02 | 1.00 | -0.16 | 0.38 | 1.34 | 23:14 | +10 |
| br2026r2 | + lean slope | loeo | lean 0.01 | 0.02 | 1.00 | -0.15 | 0.36 | 1.34 | 23:14 | +10 |
| br2026r2 | + lean slope | chronological | lean 0.01 | 0.02 | 1.00 | -0.15 | 0.36 | 1.34 | 23:14 | +10 |
| br2026r2 | base, tau mixture | loeo | – | mix of 8 | 1.00 | -0.18 | 0.33 | 1.15 | 20:06 | +80 |
| br2026r2 | base, tau mixture | chronological | – | mix of 8 | 1.00 | -0.18 | 0.33 | 1.15 | 20:06 | +80 |

- **Poland runoff**: the repo rule gives coverage 1.00 and the model 126 minutes *late*. The mixture gives +47 min with coverage 0.77 (published v1: +47 at 0.39). The mixture's `tau`s (0 to 0.03) put the first usable P ≥ 0.9 at the 21:27 version, the one where the raw tally also turns.
  Its band is still short of 0.90: the runoff needed 0.02 and the mixture's mean `tau` of 0.012 is below that.
- **Brazil R1**: the mixture settles at 20:55 (35 min before Polymarket), coverage 0.99; the repo rule gives 21:59 (29 min after). **Brazil runoff**: +80 min with the mixture (same as the published v1), −7 with the repo rule.
- **Mexico 2024**: no change by any arm against the official count (coverage 0.13 to 0.15, mean z +2.0 to +2.5). Against the PREP's end: 0.80 to 0.90. The size or lean slope cuts MAE 0.89 → 0.73–0.75 pt here.
- The size / lean slope arms give the same timing as the mixture on the Brazil nights only because their selection rule picked `tau` 0.02 (see the floor sensitivity).

### 5.4 What the prototype does and does not show

- It shows that **using the cross-night spread of `tau` instead of one number** removes the published choice between a model that is early and over-confident and one that is safe and late, on every night but the two that cannot be fixed by `tau` (Mexico 2024) or where coverage is still 0.77 (Poland runoff).
- It shows no robust gain from a live size or lean slope.
- Not shown: any forward performance. Nine nights, of which four have a market; two countries have only one or two nights; Poland's cell state is reconstructed.
  Chronological numbers are close to the LOEO ones (mixture: coverage 0.90, width 4.6 vs 6.1 pt, interval score 5.4 vs 6.4, Brier 0.053 vs 0.062), but still use only seven scored nights.
- Settings not chosen on a held-out night: the `tau` grid, the 0.90 and 0.75 rule thresholds and the slope-sd grid were fixed by the repo's rule and before the runs. The mixture weights (equal) were not tuned.

## 6. Limits of this review

- The target nights' markets: four. Published lead is a median over few contested markets (Poland runoff 3, Brazil R1 6, Brazil R2 11 incl. states).
- Poland and Hungary/Germany/Israel data rest on archived snapshots, not feeds; "minutes to 90%" there is limited by what was archived.
- `tau` needed is computed at frames, not minutes; the published coverage (minute-weighted) differs slightly (Poland runoff 0.464 vs 0.385 frame).
- The US 2026 count (3 November 2026) has not happened: nothing here is a statement about it. No US file was touched.
- 2014/2018 Brazilian nights have no receipt times and are not replayed here (the repo uses them only as simulated nights).

## 7. Reproduce

    export BRUNDER_DATA=~/brunder-data-local
    uv run python analysis/model_review_table.py          # cross-election table -> data_public/model_review_table.csv
    uv run python analysis/model_review_frames.py         # stage 1: frames x configs (SSD), ~1.5 min
    uv run python analysis/model_review_loeo.py           # stage 2: leave-one-election-out / chronological selection
    uv run python analysis/model_review_tau_mix.py        # hierarchical tau
    uv run python analysis/model_review_targets.py        # stage 3: minute-level targets, settle vs Polymarket
    uv run python analysis/model_review_order.py          # F3 evidence
    uv run python analysis/model_review_decomp.py         # F4 decomposition
    uv run python analysis/model_review_sim_vs_real.py    # F2 (simulated vs real order on the same election)
    uv run pytest -q tests/test_nowcast_order.py

Outputs go to `$BRUNDER_DATA/results/model_review/` (SSD). Loaders: `analysis/mr_common.py`, `mr_nights_br.py`, `mr_nights_mx.py`, `mr_nights_pl.py`.
