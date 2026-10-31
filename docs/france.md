# France: 2022 presidential nights replayed, 2024 legislative nights checked, 2027 forward test prepared

**RETROSPECTIVE, not pre-registered.** Built on 2026-10-31 with the 2022 results and the Polymarket resolutions known.
The model is `brunder.france`; every setting comes from nights before the one replayed (2017 for 2022). Replays:
`fr2022r1` (2022-04-10) and `fr2022r2` (2022-04-24). The 2024 legislative nights (2024-06-30, 2024-07-07) could **not**
be replayed: no national count history survives (section 1.3). The 2027 forward test (round 1 2027-04-18, round 2
2027-05-02; Polymarket about $161M) needs the live feed described in section 5.

## 1. Stage 1: what exists

### 1.1 Count-night history on the ministry's results site (Internet Archive CDX, UTC night windows)

| night | captures of the results site (status 200) | national page `FE.html`: captures / distinct contents | usable states with candidate votes and registered voters counted |
|---|---|---|---|
| 2017-04-23 round 1 | 19 (`elections.interieur.gouv.fr/presidentielle-2017`) | 5 / 4 | 4 (66% to 97% of registered voters), 2 inside the 1% to 95% window |
| 2017-05-07 round 2 | 1,354 (mostly pre-result commune pages) | 13 / 12 | 12 (62% to 100%), 9 inside the window |
| 2022-04-10 round 1 | 562 (`www.resultats-elections.interieur.gouv.fr/presidentielle-2022`) | 43 / 29 (41 files kept after URL variants) | **32** (43% at 21:42 CEST to 97%), 29 inside the window |
| 2022-04-24 round 2 | 120 | 44 / 15 | **12** (49% to 100%, complete at 23:19 UTC), 12 usable |
| 2024-06-30 round 1 (legislative) | 1,534 | none: the national page says "résultats non parvenus" all night | **0 national**; 189 constituency-page captures before 04:00 CEST covering 119 of 577 constituencies, 14 of them with two or more contents |
| 2024-07-07 round 2 (legislative) | 40 | none | **0**; 10 captures in the evening, 5 département pages |
| 2012 rounds 1 and 2 | 0 | 0 | 0 (no archived night, so 2017 round 1's prior is built from the 2012 final result only) |

- **The pages carry no time of their own.** A capture time is the only stamp; each distinct page content is dated by its
  **first** capture, so the count may have been public earlier (a bias against the model's lead, never for it).
  The page does carry the share of registered voters counted ("résultats incomplets calculés sur la base de 86% des
  électeurs inscrits", 2017: "des inscrits reçus") and every candidate's votes plus the counted registered voters,
  blanks, nulls and valid votes. 2017 and 2022 pages use the same table layout.
- Constituency pages of 2024 carry partial counts too (for example 7515: "87,56% d'inscrits reçus", candidate votes),
  but too few of them were captured to rebuild the 577-seat state the seat and majority markets need.
- Gate (>= 1 night with a real timestamped count history of >= 10 snapshots): **met by 2022 round 1 (32 states) and 2022
  round 2 (12 states)**; not met by any 2024 night, so no 2024 replay (ids `fr2024l1`, `fr2024l2` are not created).

### 1.2 The ministry's media feed (flux)

- **Public, no login:** `https://www.resultats-elections.interieur.gouv.fr/telechargements/` is an Apache-style index
  (`PR2022/`, `LG2024/`, `EU2024/`, `MUN2026/`, `SN2026/`, and since 2026-10-30 `PR2027essai1/` and `LEG2027essai1/`), with
  per-département, per-commune and per-arrondissement XML (`resultatsT1/<region>/<dpt>/…`, `index.xml`, `INDEX1FE.xml`).
  `robots.txt` says `User-agent: * / Crawl-delay: 5`.
- **Archive:** no time series. The Internet Archive holds one capture of the night's `index.xml` (2022 round 1: 2022-04-11
  08:14 UTC; round 2: 2022-04-25 08:17 UTC; 2024 round 1 `INDEX1FE.xml`: 2024-07-01 02:53 UTC) and one of some
  département files. Those index files give each département's **last update** (`DateDerMaj`, `HeureDerMaj`), the extraction
  time and, in 2024, the bureaux entered (`NbBureauxDeVoteSaisis`, `PourcentageBureauxDeVoteSaisis`). That is when each
  département *finished*, not a count-over-time series. The files are in `$BRUNDER_DATA/raw/france/wayback/feed/`; the model
  does not use them (using last-update stamps would leak which départements were still open).
- Not public: no archive of the live feed exists beyond the above; the ministry's own archive site
  (`archives-resultats-elections.interieur.gouv.fr`) answers 403 to our honest user agent (checked 2026-10-31, not bypassed).

### 1.3 Final results (official, Licence Ouverte / ministry open data, data.gouv.fr)

| election | file (under `raw/france/open/`) | rows |
|---|---|---|
| 2012 presidential, both rounds | `pr2012/PR12_Bvot_T1T2.txt` (by polling station, long format) | 36,791 communes |
| 2017 presidential round 1 / 2 | `pr2017/t1_PR17_BVot_T1_FE.txt`, `t2_…` (by polling station) | 35,719 communes |
| 2022 presidential round 1 / 2 | `pr2022/t1_resultats-par-niveau-burvot-t1-france-entiere.txt`, `t2_…` (+ départements, `subcom`, overseas) | 35,245 communes |
| 2024 legislative round 1 / 2 | `lg2024/t1_resultats-definitifs-par-bureau-de-vote.csv`, `t2_…`; by commune, constituency, département | |

National totals match the official results (2022 round 1: Macron 27.85%, Le Pen 23.15%, Mélenchon 21.95%). The 2022
datasets carry no licence tag on data.gouv.fr; 2012 and 2017 are `fr-lo` (Licence Ouverte); the 2024 ones `lov2`. The
aggregated multi-election tables (`Données des élections agrégées`, parquet, 160 MB) were listed but not downloaded.

### 1.4 Markets that existed

| night | Polymarket event (slug) | volume | price history |
|---|---|---|---|
| 2022 round 1 | `will-marine-le-pen-win-2nd-place-in-the-first-round-of-the-2022-french-presidential-election` | $167k | **none**: the CLOB `prices-history` returns `[]` at any fidelity; no trades via the data API |
| 2022 rounds 1, 2 | `who-will-win-the-2022-french-presidential-election` (Macron, Le Pen, Zemmour, Pécresse) | $788k | none |
| 2022 round 2 | `will-macron-win-round-2-…-with-54-of-votes-or-more` | $20k | none |
| 2024 both rounds | `which-party-wins-the-most-seats-in-french-election` | $917k | **1-minute, complete** (`raw/polymarket/<slug>/prices/*__lg24r1|r2.parquet`) |
| | `which-coalition-wins-most-seats-in-french-election` (RN/UXD, NFP, ENS, other) | $385k | 1-minute |
| | `national-rally-coalition-wins-a-majority-of-seats-in-french-election` | $85k | 1-minute |
| | `how-many-seats-will-national-rally|new-popular-front|the-ensemble-coalition-win-in-the-french-election` (seat buckets) | $167k / $33k / $38k | 1-minute |
| | `next-french-prime-minister` | $675k | 1-minute |

Kalshi: no French market existed in 2022 or 2024; its French series today (`KXFRENCHPRES1R`, `KXFRPRES1R1ST`,
`KXFRPRES1R2ND`, `KXFRPRESADVANCE`, `KXFRPRESBALLOT`, `KXFRENCHPM`, `KXSNAPELECTIONFR`) are all 2026/2027 markets.
So the 2022 replays have **no market line** and measure no lead; the 2024 nights have prices but no count history.

## 2. Model (`src/brunder/france.py`)

The count history is national only (one page for the whole country), so the model is a **size-order projection**:
communes are assumed counted from the smallest to the largest (French citizens abroad last). Units are the 35,245 communes
of the 2022 final file.

1. **Prior.** Each commune's share of Macron, Le Pen, Mélenchon and everybody else at the previous election of the same
   family: 2017 round 1 for 2022 round 1; 2022 round 1 (the two runoff candidates only, renormalised) for 2022 round 2;
   2012 for 2017 round 1 (Macron, who did not run, takes Hollande + Bayrou) and 2017 round 1 for 2017 round 2 (both
   only to calibrate). Communes without a counterpart take their département's average. The 2022 registered voters (the
   electoral roll) are the only 2022 number used: they set the size order.
2. **Counted set** at a state: the smallest communes whose registered voters add up to the page's counted registered voters.
3. **Ratio swing** per category: counted share over the prior's share for that set, shrunk in log space by
   `E / (E + kappa)`; uncounted communes' prior shares are multiplied by it and renormalised. Turnout ratio scales their votes.
4. **Share** = (counted + projected uncounted) / total. **sd at tau 0** = commune-level noise `sigma_u` over the
   uncounted communes plus the swing's own error (about 0.1 point). **Reporting-order error `tau`** adds
   `(1 - counted) * tau` in quadrature (`nowcast_order.needed_tau`, `mixture_band`): the mixture is the equal-weight
   set of the `tau` earlier nights needed.
5. **Races** (draws from the 3-candidate joint, conditioned to sum to 1): round 1: Le Pen ahead of Mélenchon (main; share =
   Le Pen / (Le Pen + Mélenchon)), Macron finishes first (share = Macron / (Macron + Le Pen)), Le Pen finishes second
   (the Polymarket question; P only); round 2: Macron wins (share of valid votes), Macron 54% or more (P only).

### Settings (`analysis/fr_calibrate.py` -> `data_public/fr_calibration_v1.json`)

| replayed night | earlier nights | sigma_u | kappa | tau needed per earlier night (frames) | tau mixture (headline) | single tau (`point`) |
|---|---|---|---|---|---|---|
| 2022 round 1 | 2017 R1 (2 frames), 2017 R2 (9) | 0.040 | 0 | 0.03, 0.00 | {0.03, 0.00} | 0.01 |
| 2022 round 2 | 2017 R1, 2017 R2, 2022 R1 (29) | 0.043 | 0 | 0.03, 0.00, 0.05 | {0.03, 0.00, 0.05} | 0.05 |

`kappa` is the grid value (0, 1e5, 1e6, 1e7) with the lowest mean absolute error on the earlier nights (0). Only two to
three earlier nights exist and one of them has two frames, so the mixture is thin. The single value is the repo's
pre-mixture rule (smallest `tau` with pooled coverage >= 0.90 and every earlier night >= 0.75).

## 3. Results

| night, race | 90% coverage (1% to 95% counted), mixture / single `tau` | mean z | MAE, points | notes |
|---|---|---|---|---|
| 2022 R1, Le Pen ahead of Mélenchon | **0.29** / 0.00 | -2.1 / -3.8 | 1.5 | model P = 0.94 at the first state (44% counted), 0.9998 with the single tau; truth: yes |
| 2022 R1, Macron first | 1.00 / 0.84 | -0.8 / -1.3 | 0.4 | |
| 2022 R2, Macron wins | 1.00 / 1.00 | +0.3 / +0.2 | 0.2 | P >= 0.999 from the first state (49% counted) |

- Round 1 **fails the calibration test**: the size-order projection puts Mélenchon's share 1.5 to 1.8 points too low from
  44% to 80% counted (big cities arrive late and the model's prior is 2017 for a candidate whose vote concentrated in cities),
  Macron 0.6 to 1.1 too high. The night's own `tau` would be 0.05 (top of the grid) against {0.03, 0.00} from 2017. This
  repeats `docs/model_review.md` finding F1: one or two earlier nights do not give `tau`; here the 2017 round 2 (a two-way
  vote, no ordering effect) says 0, the 2022 first round says 0.05.
- Round 2 is easy (two candidates, no ordering effect worth modelling): within 0.4 points from the first state.
- Neither night can be compared with a market: no price history. The outcome "model at 0.9 before the market" is
  therefore **not measured**; the exit-poll information released at 20:00 CEST (18:00 UTC), before the first archived
  state, is not used and would have decided both questions earlier than any count.

Output: `site/data/fr2022r1`, `fr2022r2` (timeline with the single-tau sensitivity as `P_sens_point`, no map, no units),
entries in `site/data/elections.json`, both ids added to `analysis/site_check.py`; `analysis/decided_stats.py` run.

## 4. Reproduce

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/fr_fetch.py open      # official finals 2012, 2017, 2022, 2024 (about 330 MB)
uv run python analysis/fr_fetch.py cdx       # CDX listings of the six count nights (cdx_<night>.json)
uv run python analysis/fr_fetch.py wayback   # national pages of the four presidential nights + feed index files (1.1 s apart)
uv run python analysis/fr_fetch.py markets   # Polymarket events + 1-minute prices (2024 only returns data)
uv run python analysis/fr_calibrate.py       # settings from earlier nights -> data_public/fr_calibration_v1.json
uv run python analysis/fr_export.py && uv run python analysis/decided_stats.py fr2022r1 fr2022r2
uv run python -m pytest tests/test_france.py -q
```

Data on disk (`$BRUNDER_DATA/raw/france/`, 313 MB): `open/` finals, `wayback/<night>/FE_<capture time>.html` and `cdx.json`,
`wayback/feed/` (index XML and CDX), `cdx_*.json` (all CDX listings, including the 2024 constituency pages and the
telechargements directory), `probe/` (single pages inspected by hand). Polymarket in `raw/polymarket/<slug>/`.

Requests: sequential, >= 1.1 s apart to each host, User-Agent `brunder/0.1 (research; recorder)`, `robots.txt` read
(ministry: Crawl-delay 5; the 403 pages of the ministry's WAF were not bypassed).

## 5. Stage 3: the live endpoints and what the 2027 forward test needs

**Reachability from the election server (`oracle-slotbank`, 2026-10-31 08:40 UTC; 9 requests, 6 s apart):**

| URL | status |
|---|---|
| `www.resultats-elections.interieur.gouv.fr/robots.txt` | 200 (`Crawl-delay: 5`) |
| `…/telechargements/` | **200** (live index; lists `PR2027essai1/`, `LEG2027essai1/`, `MUN2026/`, `SN2026/`) |
| `…/telechargements/LG2024/resultatsT1/INDEX1FE.xml`, `PR2022/resultatsT1/index.xml` | 301 to `archives-resultats-elections.interieur.gouv.fr/telechargements/LG2024/` and `interieur.gouv.fr/avotreservice/elections/telechargements/…` (old nights moved) |
| `…/presidentielle-2022/FE.html`, `…/legislatives2024/ensemble_geographique/index.html` | 301 to the archive site |
| `archives-resultats-elections.interieur.gouv.fr/resultats/presidentielle-2022/index.php` | **403** (also from the Mac) |
| `www.elections.interieur.gouv.fr/` | **403** (also from the Mac) |
| `…/presidentielle2027/` | 404 (the 2027 site path is not published yet) |

From the Mac, with the honest user agent: `…/telechargements/PR2027essai1/` and `LEG2027essai1/` answer **403
"Requête non aboutie"** (the ministry's request filter). Nothing was done to get around it. The folders were created on
2026-10-30 16:43 and 16:46 UTC, i.e. a rehearsal ("essai") of the 2027 feed is being set up.

**What the forward test needs**

1. **Live source and cadence.** Poll `…/telechargements/PR2027…/resultatsT1/` (index + département + commune XML) and the HTML results
   pages at <= 1 request per 5 s per host (Crawl-delay), with the honest user agent, from the election server. Whether the
   data files (not just the listing) are served to our agent on the night is **unverified**; test during the "essai"
   rehearsals when they open, and write the answer into this file. If the WAF keeps blocking, the HTML pages
   (`FE.html`-style national page, still public in 2022) are the fallback, and nothing is bypassed.
2. **Recorder.** A non-US recorder unit: writes every response with its receive time (the pages carry none; the XML
   index carries `DateDerMaj` per département) and the response headers (`Last-Modified`, `Date`), keeps the raw files, and
   also records Polymarket (the ~$161M presidential event and the first-round / second place / advance markets) and Kalshi
   (`KXFRENCHPRES1R`, `KXFRPRES1R1ST`, `KXFRPRES1R2ND`, `KXFRPRESADVANCE`) at 1-minute resolution. The archive has no
   count series for 2022 round 1 before 44% counted, so the live recorder is the only way to measure a lead.
3. **Model.** Replace the size-order assumption with the **feed's own counted flags** (commune and arrondissement files,
   département `Complet` / `% bureaux saisis`): the counted set becomes observed, `tau` only has to cover bureaux not yet in
   inside partly counted communes (small), and the Mélenchon-style ordering error of 2022 round 1 disappears. Keep the
   tau mixture as the order-error prior until a live night shows the real value; update it after the first 2026 or 2027
   night with the feed (`MUN2026`, `SN2026` files exist but are municipal / senatorial, not comparable).
4. **Freeze before the night.** Candidate list (published by the Constitutional Council about three weeks before round 1),
   category mapping and prior (2022 first-round communes; polls enter only if a registered rule is added), settings file
   `data_public/fr_calibration_v1.json` plus the 2022 nights added to the mixture: tau {0.03, 0.00, 0.05, 0.01} (2022 R1 and
   R2 included). Pre-registration (`prereg/`) and the config are for the integrator, not this branch.
5. **Round 2** 2027-05-02: the runoff pair is only known on the night of round 1; the prior is the round-1 commune shares of the
   two finalists renormalised, exactly as in `fr2022r2`.
6. **Known limits to disclose.** Exit-poll estimates at 18:00 UTC decide most French questions before any count (2022 round 2
   at P >= 0.999 from the first state); the model's value is in close second-place and threshold questions, not the winner. The
   headline coverage from 2022 round 1 (0.29) says the mixture must not be trusted as a calibrated 90% band yet.
