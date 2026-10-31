# Spain: general election 2026 (`es2026`)

This is a **forward election-night test**. `configs/es2026_v1.toml` is frozen but intentionally unregistered; the
integrator must commit and hash-register it before the first results at **20:00 UTC on 29 November 2026** (21:00 in
Madrid). Nothing here was tuned on the 2026 result; the count has not happened.

## Which election

The Spanish **general election to the Cortes Generales**, Sunday **29 November 2026**: 350 seats in the Congress of
Deputies (52 constituencies: 50 provinces plus Ceuta and Melilla) and 209 in the Senate. The Council of Ministers
called it on 5 October (Real Decreto 806/2026, BOE 6 October). Polls are open 09:00-20:00; the campaign runs 13-27
November; polls may not be published 24-28 November. The night count is "provisional" (escrutinio provisional); the
general count (escrutinio general, 4-7 December) adds the votes cast abroad (CERA, about 0.8% of valid votes in 2023) and
gives the official result the markets settle on. Sources: La Moncloa guide of 20 October, Interior Ministry
note of 21 October, Junta Electoral Central calendar (`eg2026_calendario.pdf`).
Seats per province are the 2023 ones except Cádiz 9 to 8 and Madrid 37 to 38.

The Prime Minister markets are **not modelled**: the count does not decide who forms a government.

## Markets (observed 2026-10-31)

Polymarket (Gamma API, volume in USD) and Kalshi (volume in contracts, bid/ask at the observation):

| Question | Polymarket market (volume) | Kalshi market (volume, mid) |
|---|---|---|
| Most seats: PP | `will-pp-win-the-most-seats-in-the-2026-spanish-general-election` ($105,627) | `KXSPAINPARLI-27-PP` (11,813; 0.82) |
| Most seats: PSOE | `...psoe-win-the-most-seats...` ($151,406) | `KXSPAINPARLI-27-PSOE` (7,613; 0.16) |
| Most seats: Vox | `...vox-win-the-most-seats...` ($16,020) | `KXSPAINPARLI-27-VOX` (4,633; 0.04) |
| Second place: PSOE / PP / Vox | `...win-the-second-most-seats...` ($3,471 / $1,457 / $740) | `KXSPAIN2ND-26NOV29-2-{PSOE,PP,VOX}` (502 / 1,368 / 490) |
| Third place: Vox / PP / PSOE | `...win-the-third-most-seats...` ($5,737 / $908 / $795) | `KXSPAIN3RD-26NOV29-3-{VOX,PP,PSOE}` (103 / 500 / 76) |
| PP + Vox at least 50% of the vote | `spain-general-election-pp-vox-get-50-of-vote` ($17,881) | `KXVOTESPAIN-26NOV29PPVOX-50` (16,705; 0.43) |
| Vote-share lines (Kalshi only) | none | `KXVOTESPAIN-26NOV29{PP,VOX,PSOE}-<n>` (41 lines; the 13 with 100+ contracts are in the config) |
| Next prime minister | `next-prime-minister-of-spain-20260625005215443` ($2.5M event) | `KXSPANISHPM-27AUG22-*` (5,244-18,123) |

Polymarket also lists Frente Amplio and Podemos (seats markets) and unused "Party A-Z" shells; Kalshi lists Sumar
(2 contracts). None is modelled. The earlier scan's "13 Kalshi markets" are the 13 Kalshi-Polymarket pairs in
`configs/pairs/es2026.toml`. Both venues resolve on the **official** result (Interior Ministry / Junta Electoral
Central), not the night count. Kalshi's winner market may resolve early on media consensus; Kalshi counts PSC seats as
PSOE. 22 races are configured: 10 with both venues, 12 Kalshi only.

## Official feed: what is reachable (checked 2026-10-31)

All checks used `User-Agent: brunder/0.1 (research; recorder)`. The election server (`oracle-slotbank`) made 13 requests, at
least 2 s apart; the Mac made a few more (Internet Archive at 1 request/s, Polymarket and Kalshi APIs, Interior pages).
Nothing was retried against a block.

* **The Interior Ministry's own feed is not usable.** Its note of 21 October says the provisional results go
  to accredited media only, as ASCII files in a repository with a user name and password mailed on 17 November (one
  connection per outlet; applications closed 28 October; tests 18 November). This project does not use logins.
* **There is no public Interior results site yet.** In 2023 the Ministry opened `resultados.generales23j.es` two days
  before the vote, with an open JSON back end (`/backend-difu/...`); that site is offline. The official 2026 site,
  `29neleccionesgenerales.es`, carries election information only (no results). Candidate hosts such as
  `resultados.generales29n.es` do not resolve in DNS. If the Ministry announces a results site before polling day,
  the recorder needs a second adapter (see "Risks").
* **`datos.generales29n.com` republishes the Ministry's provisional count as public JSON.** It is run by Elecciones 29N
  (Color Vivo Internet), "not an official web", and states: files are public, no key, reusable citing the source
  ("Fuente: Ministerio del Interior"; Ley 37/2007). Server request: `manifest.json` returned HTTP 200
  (`application/json`, 697 bytes: `{seq, phase: "pre", embargo: true, counted_pct: 0, base, ...}`); `robots.txt` HTTP 200 with
  no disallow rules; `3/es.json` HTTP 200 (national file, empty results). Its 2023 history files give the schema the
  live files share: `scope.{type,code}`, `counted.{tables,total_tables,pct}`, `totals.{valid,blank,null}`,
  `results[].{acronym,name,family,votes,seats}`. Real fixtures: `tests/fixtures/es_feed_hist_prov28.json` (Madrid
  2023), `es_feed_hist_es.json` (national 2023), `es_feed_manifest_pre.json`. Each publication `seq` has a national
  file and one file per province (`{base}/{seq}/prov/NN.json`); the manifest names the current `base`.
  **Live-night files have not been observed** (embargoed until the official time), so the live schema is assumed equal
  to the history schema; the parser is strict and rejects anything else.
* Data volume: 52 province files + 1 national per publication, fetched sequentially at 1.1 s apart (about 60 s),
  only when the manifest `seq` changes; a publication is stored only when every file parsed.
* `infoelectoral.interior.gob.es` (mesa and municipality zips) answers with a Cloudflare challenge (HTTP 403
  `cf-mitigated: challenge`) to the Mac **and** the server; it is not used. `descargas.interior.gob.es` (open data
  workbooks) is not challenged and is used for history.

**Count-history resolution**: the feed is provincial (52 units); there is no per-publication municipal data in the
recorder (municipal files exist on the relay but would need about 8,000 requests per cycle).

## Historical data and licences

* `data_public/es_congreso_province_2016_2023.csv`: Congress results by province for 2016-06-26, 2019-04-28,
  2019-11-10 and 2023-07-23, grouped into party lanes (PP, PSOE, Vox, Cs, left lists, ERC, Junts, Bildu, PNV, BNG,
  CCa, UPN, CUP, other). Source: Interior Ministry, "Elecciones Congreso" workbook on Infoelectoral's open-data page,
  **CC BY 4.0**, attribution "Fuente: Ministerio del Interior". Built by `analysis/es_history.py`; raw workbook in
  `$BRUNDER_DATA/es2026/raw/`. National valid-vote totals are asserted against the official ones; D'Hondt run on the
  provincial votes reproduces the official seats exactly for 2016 and 2023 (within 2 and 10 seats for the two 2019
  elections, where lists such as Más País/Compromís and regional coalitions do not fit the lanes).
* `data_public/es_2023_night_snapshot.json`: the only election-night state of an earlier Spanish night that exists
  online: the Internet Archive's copies of the Ministry's 2023 public site (see below).
* Polls: nine pollsters' published figures (datosclaros.es table of the last polls; ABC, 26 October); reported toplines.
* Map: Natural Earth Admin 1 (public domain), `analysis/es_geo.py`.

## Real archived count order

The Internet Archive holds the Ministry's 2023 national Congress file at 20:03 UTC on election night (41,711 of 60,314
tables, 69.2%) and at 00:24 UTC (all tables). In the early state **PSOE led PP in the counted votes (32.9% vs 31.6%)
although PP won (33.3% vs 32.0% at the end of the night count)**: relative errors of the counted shares were PP -5.1%,
PSOE +3.1%, Vox -0.2%, left lists -2.5% (`analysis/es_snapshot.py`). It is the only real order available.
The 2019 elections have no archived night state. Everything else is simulated and labelled so.

## Model

Units are the 52 constituencies. For each province the model keeps a prior (the 2023 result, Sumar split into Frente
Amplio and Podemos in the polls' ratio, raked to the **median of the nine frozen polls**: PP 31.5, PSOE 26.4, Vox 18.9,
Frente Amplio 6.5, Podemos 2.7), then follows the count:

1. counted votes are kept; the pooled national swing of the counted votes re-tilts every province's prior;
2. each province's own counted composition is blended in (`own_votes`);
3. uncounted votes and the votes abroad are projected from that composition;
4. 4,000 draws add a national and a province-level error to the uncounted part; every draw allocates seats province by
   province (D'Hondt, 3% of valid votes including blank, lists without a lane are "other" and win nothing);
5. each market's probability is the share of draws in which it resolves Yes (rank by seats, ties by votes; a vote-share
   line is reached at the rounded two-decimal share of valid votes, blank included).

Frozen settings (`[model]` in the config) are chosen on the three earlier elections only (each projected from the one before,
counts simulated): `own_votes` 10,000, `swing_votes` 1,000,000, `sd_votes` 1,000,000, `tau_nat` 0.06, `tau_prov` 0.12,
`cera_frac` 0.0083; `precount_sd` 0.12 is a judgement: the last-week poll median missed by 0.076 (RMS log error, ten party-elections
2019-2023; `analysis/es_poll_error.py`) and the 2026 polls are 5-7 weeks old.

### Calibration on earlier nights (retrospective, simulated count order, not pre-registered)

`analysis/es_calibrate.py`, result in `data_public/es_calibration_v1.json`. The size of the order bias in the
simulation comes from the real 2023 snapshot above. Scores are for PP, PSOE and Vox national shares at 5-95% counted.

| Set | Error (points), all / from 20% counted | 90% interval coverage, from 20% counted | Mean interval width (points) |
|---|---:|---:|---:|
| Fit: 2016>2019 and 2019>2019 (in sample) | 0.42 / 0.35 | 93% | 3.0 |
| Holdout 2023 (settings fit without 2023), real-snapshot bias | 0.47 / 0.43 | 93.5% | 3.7 |
| same, half the bias | 0.31 / 0.22 | 100% | 3.7 |
| same, twice the bias | 0.82 / 0.85 | 76% | 3.7 |

So the 90% intervals hold if the real order bias is like 2023's and fail if it is about twice as large. The calibration
priors are the previous election carried forward (a much worse prior than polls), so the early-count intervals are wide.

## Software rehearsal (retrospective, not pre-registered)

`analysis/es_rehearsal.py` replays **real final official province results** of 2019-11-10 and 2023-07-23 in the feed's JSON
format on a simulated clock (50 publications, 3 minutes apart from 20:02 UTC; provinces report at different speeds
with the calibration's order bias). The prior is the election before the replayed night. Three things were exercised:
`brunder spain live` read the recorder archive of each night and wrote the dashboard files; `brunder spain record` fetched 47 of the 50 publications of 2019-11-10 from a local replay server (52 provinces, one file per province and publication, no repeats; it always takes the newest publication, so fast ones can be skipped);
and `brunder spain export` produced the same timeline. At 99% counted the replays gave P = 1.0 to the true winner,
second and third place in both nights (2023: PP first, PSOE second, Vox third; 2019-11: PSOE first, PP second, Vox third).
This is a test of the plumbing, not of the model.

## Files and commands

* `src/brunder/spain.py`, `configs/es2026_v1.toml`, `tests/test_spain.py` (+ fixtures `tests/fixtures/es_feed_*.json`)
* `analysis/es_history.py`, `es_snapshot.py`, `es_poll_error.py`, `es_calibrate.py`, `es_rehearsal.py`, `es_settle.py`, `es_geo.py`
* `deploy/brunder-es-{record,live}.{service,timer}`: recorder starts 19:00 UTC (one hour before the first results), live
  model 19:30 UTC, both stop 2026-11-30 03:00 UTC.
* `brunder spain preelection|record|live|export configs/es2026_v1.toml`; after the count:
  `uv run python analysis/es_settle.py --national <final national file> [--provisional]` (writes
  `stats.json["settle"]`, then `analysis/decided_stats.py es2026`).
* Pre-count dashboard data: `site/data/es2026/`; entry in `site/data/elections.json` (status `scheduled`).

## Risks and open items

* **Third-party relay.** The only reachable public copy of the provisional count is a private site. It could lag, change schema, or go
  down. The recorder logs every publication and rejects unparsable files; the live exporter keeps the last good state.
* **Ministry public site.** If the Ministry opens a results site before 29 November (as in 2023), a second adapter is needed;
  nothing can be verified until it is announced.
* Live schema unverified (see above). The parser requires PP or UPN and PSOE votes in every province with counted tables, so a
  renamed list fails loudly instead of silently moving votes to "other".
* The night count excludes votes abroad; the markets settle on the general count. The model adds abroad votes from the 2023 share.
* Count order bias is the main error source; the calibration shows what happens when it is twice as large as in 2023.
