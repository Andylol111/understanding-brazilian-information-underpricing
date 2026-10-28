# Canada: 2025 federal election and provincial elections

Dashboard entries: `ca2025` (federal, 2025-04-28) and `qc2026` (Quebec, 2026-10-05). Both show **prices only**
(`meta.count_timeline: false`), with a single final map frame from official results. No count model was run on
either. Neither Elections Canada nor Élections Québec keeps a count over time, so there is nothing to project
from, and the standard accuracy and profit questions can't be answered. `stats.json` sets `accuracy`,
`profit`, `calibration`, `early_error`, `catchup`, `capacity` and the model/raw settle times to null, with the
reason "no official count over time is available". Because no model was run, no frozen config
(`configs/<eid>_v1.toml`) exists either. Both entries are labelled `market-only`: no count model was run, and only
market prices and official final results are shown. The 0.9 settle rule is descriptive and was chosen after the fact.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/ca_export.py            # both
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/ca_export.py qc2026     # one

Code: `src/brunder/canada.py` (parsers, unit ids, market lists) and `analysis/ca_export.py` (download, geometry,
export). Tests: `tests/test_canada.py`. The exporter writes `site/data/<eid>/{meta,timeline,units,stats,entry}.json`;
the integrator merges `entry.json` into `elections.json`. Downloads are cached in `$BRUNDER_DATA/raw/canada`
(about 15 MB once the extracted shapefile is removed) and are never committed.

## Canada 2025 (`ca2025`)

### Sources and licences
| Data | URL | Licence |
|---|---|---|
| Official Voting Results, 45th GE, Table 12 (candidates by electoral district), CSV, 351 KB | https://www.elections.ca/res/rep/off/ovrGE45/62/data_donnees/table_tableau12.csv (report: https://www.elections.ca/res/rep/off/ovrGE45/home.html) | Elections Canada [Terms and Conditions](https://www.elections.ca/content.aspx?section=pri&document=index&lang=e), "Non-commercial Reproduction": *"Unless otherwise specified, you may reproduce the materials in whole or in part for non-commercial purposes, and in any format, without charge or further permission from Elections Canada, provided you … Exercise due diligence in ensuring the accuracy of the materials reproduced; Indicate both the complete title of the materials reproduced, as well as the author …; Indicate that the reproduction is a copy of the version available at [URL] …"*. The 45th GE results are not on open.canada.ca yet, but Elections Canada publishes the 42nd–44th GE official results there under the Open Government Licence – Canada (e.g. dataset 065439a9-c194-4259-9c95-245a852be4a1). |
| Transposition of the Vote from the 44th GE to the 2023 Representation Orders (`transposition_343_fed_vote_count.csv`, 57 KB) | https://open.canada.ca/data/en/dataset/08c4ffbc-c513-41a4-b741-d35b41b50c22 | [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada) (CKAN `license_id: ca-ogl-lgo`) |
| Electoral Geography Boundary Files (45th GE), Federal Electoral Districts 2025, SHP (10.3 MB zip) | https://open.canada.ca/data/en/dataset/97a2a33c-54cc-4f2e-82c1-047ad8212f05 (file: https://www.elections.ca/res/cir/mapsCorner/vector/FederalElectoralDistricts_2025_SHP.zip) | Open Government Licence – Canada (`ca-ogl-lgo`) |
| Election-night national results page `https://enr.elections.ca/National.aspx?lang=e`, 6 Internet Archive captures (2025-04-28 23:30 to 04-29 10:27 UTC) | https://web.archive.org/web/2025*/https://enr.elections.ca/National.aspx?lang=e | Elections Canada non-commercial reproduction terms (above); `enr.elections.ca/robots.txt` is a 404 |
| Polymarket price history (1-minute) and questions | public CLOB / Gamma APIs | Polymarket public data |

The dashboard is non-commercial research, which these terms allow. Every file names its source and URL in
`meta.sources`.

### What is published
- **Map** (`geo/ca_districts.topo.json`, object `districts`, 0.15 MB): the 343 federal electoral districts
  (2023 Representation Order), simplified to 1.5 % with mapshaper, `unit_id = ca-<province>-<FED number>`
  (e.g. `ca-on-35075`), `name` = English district name.
- **units.json**: one final frame. `a` is the Liberal share of Liberal + Conservative votes (×1000). `prior` is
  the same share in Elections Canada's own transposition of the 2021 vote onto the 2023 districts, and `V0` is the
  transposed Liberal + Conservative votes. Extra frame arrays: `winner` (party code), `margin` (winner minus
  runner-up as a share of valid votes, ×1000) and `swing` (change in Liberal two-way share, ×1000). The result
  matches the official total: LPC 169, CPC 144, BQ 22, NDP 7, GPC 1.
- **Races** (26): each Polymarket binary question is its own race, with A = YES.

| Race | Polymarket market slug (event) |
|---|---|
| pm-carney, pm-poilievre | `will-mark-carney-be-the-next-canadian-prime-minister`, `will-pierre-poilievre-be-the-next-canadian-prime-minister` (`next-prime-minster-of-canada`) |
| most-lpc, most-cpc | `will-the-liberal-party-win-the-most-seats-in-the-next-canadian-election`, `will-the-conservative-party-win-the-most-seats-in-the-next-canadian-election` (`which-party-wins-most-seats-in-canadian-election`) |
| seatmargin-lpc-25-49 | `will-the-liberal-party-win-by-2549-seats` (`canada-election-of-seats-margin-of-victory`), the winning band |
| votemargin-lpc-0-3 | `will-the-liberal-party-win-the-canadian-election-by-0-3` (`canada-election-popular-vote-margin-of-victory`), the winning band |
| `<prov>-most-lpc`, `<prov>-most-cpc` (on, bc, ab, mb, sk, ns, nb, pe, nl); `qc-most-lpc`, `qc-most-bq` | `will-the-{liberal-party,conservative-party,bloc-qubcois}-win-the-most-seats-in-<province>-in-the-next-canadian-election` (`which-party-wins-most-seats-in-<province>`). Quebec's Liberal market slug reads `…-win-the-third-most-seats-in-quebec…`, but its question asks about the most seats. |

- **Timeline**: Polymarket 1-minute YES price from 2025-04-28 22:00 to 04-29 16:00 UTC (18:00 to 12:00 ET). A
  row is written only when the price changes, plus the final minute. Count fields are null.
- **stats.market_behaviour**: per-race Polymarket settle time (the eventual outcome's price stays ≥ 0.9 until
  the window ends) and the archived official snapshots. Each snapshot carries Elections Canada's own "Last
  updated at HH:MM ET" stamp, polls reporting, and seats leading or elected by party. The first capture after all
  polls closed (02:00 UTC) is stamped 04:43 UTC, with 86 % of polls reporting and LPC 163 / CPC 149. Polymarket's
  "Liberals win the most seats" market had settled at 02:00 UTC. There is no capture between 00:21 and 04:43
  UTC, so this only bounds when the official page showed the result; it doesn't date it. AB, SK and PE province
  markets were already settled when the window opened. The seat-margin band had not settled by 12:00 ET.

### What can't be done, and why
- **Count model, accuracy, profit.** Elections Canada publishes final results only: poll-by-poll and by
  district, with no timestamps. Its election-night site (`enr.elections.ca`) serves only the current state, and
  its `DownloadResults.aspx` feed was archived only after the count (09:17 UTC on 04-29, then 04-30). Without a
  count over time, there is nothing to replay.
- **Per-district Wayback reconstruction (optional item).** The CDX index shows per-district pages
  (`ElectoralDistricts.aspx?ed=<id>`) captured in roughly four sweeps during the count (around 00:46, 04:47,
  07:00 and 08:57 UTC), so the three-frame threshold is met. It is not cheap, though. It would take about 1,400
  archive fetches; the `ed` ids are ENR-internal and would need a mapping to FED numbers; the captures within a
  sweep are minutes apart, not simultaneous; and two of the sweeps come after 86 % and 98 % of polls had
  reported. Those frames could not test an early projection, so this was not done. The national-page snapshots
  above carry the same official time information at negligible cost.
- **Provincial seat comparison against the official page.** `Provinces.aspx` has a single capture inside the
  count (04:46 UTC).
- **Kalshi**: excluded by project rule.

## Quebec 2026 (`qc2026`)

### Sources and licence
All Élections Québec files are published under the *Licence sur l'utilisation des données ouvertes du directeur
général des élections* (https://www.dgeq.org/licence.html): *"une licence non exclusive et non transférable vous
permettant d'utiliser, de reproduire, de traduire, de compiler et de communiquer au public par quelque moyen que
ce soit, en tout ou en partie, les données ouvertes … à toutes fins licites"*. The licence requires this mention,
which is shown verbatim in `meta.sources` and `meta.notes`: « Comprend des données ouvertes octroyées sous la
licence d'utilisation des données ouvertes du directeur général des élections disponible à l'adresse Web
dgeq.org. L'octroi de la licence n'implique aucune approbation par le directeur général des élections de
l'utilisation des données ouvertes qui en est faite. » The DGEQ can end the licence, and it has an indemnity
clause.

| Data | URL |
|---|---|
| Final results (`isResultatsFinaux: true`, stamped 2026-10-26 12:14 EDT), 208 KB | https://donnees.electionsquebec.qc.ca/production/provincial/resultats/archives/gen2026-10-05/resultats.json |
| Circonscriptions électorales 2026 (sans eau), GeoJSON, 2.4 MB | https://donnees.electionsquebec.qc.ca/autres/provincial/circonscriptions_electorales_sans_eau_2026.json (listed on https://www.dgeq.org/donnees.html) |
| Live results file, Internet Archive captures 20261006000043, 20261006013233, 20261006123508 | https://web.archive.org/web/2026*/https://donnees.electionsquebec.qc.ca/production/provincial/resultats/resultats.json |
| Polymarket | `quebec-general-election-winner` (PQ, PLQ, CAQ, PCQ "most seats" markets), `will-the-pq-win-a-majority-in-the-2026-quebec-general-election`, `will-paul-st-pierre-plamondon-be-the-next-premier-of-quebec-following-the-2026-quebec-general-election` (`next-premier-of-quebec-594`) |

### What is published
- **Map** (`geo/ca_qc_circonscriptions.topo.json`, object `circonscriptions`, 0.06 MB): 127 circonscriptions,
  `unit_id = ca-qc-p<circonscription number>`. The `p` keeps these apart from the federal `ca-qc-24xxx`.
- **units.json**: one final frame, the PQ share of PQ + Liberal votes, plus `winner` and `margin`. There is **no
  prior**: 2022 was held on 125 different circonscriptions, and Élections Québec publishes no transposition onto
  the 2026 map, so `prior` is -1 and `V0` is 0. Result: PQ 60, PLQ 39, PCQ 19, QS 9.
- **Races** (6): PQ, PLQ, CAQ and PCQ "most seats"; PQ majority; St-Pierre Plamondon as premier. The price
  window is 2026-10-05 22:00 to 10-06 16:00 UTC.
- **market_behaviour**: every "most seats" market and the premier market were already settled (≥ 0.9 on the
  outcome) at 18:00 EDT, before polls closed. "PQ wins a majority" settled "no" at 01:30 UTC. The official live
  file captured at 01:32 UTC (stamped 01:31 UTC, 12,296 of 20,704 polling stations) showed the PQ leading in 60
  of 127, short of the 64 needed for a majority.

### What can't be done
There is no count over time: the live file keeps only its latest state, and the Internet Archive holds three
captures, at 29 stations, at 59 % and at the end. So there is no model, accuracy or profit, as for `ca2025`.
`www.electionsquebec.qc.ca` sets `Crawl-delay: 10`. Only the separate data host and the archive were fetched,
one file at a time.

## Provincial elections skipped
- **Ontario 2025-02-27** (`ontario-parliamentary-election-winner`). Skipped because of the results licence.
  Elections Ontario's [terms of use](https://www.elections.on.ca/en/terms-of-use.html) say the content is "for
  your personal use only" and forbid "scripts, robots … to scrape the sites or services or otherwise copy data".
  The official 2025 results exist only as PDFs and a JS app, and data.ontario.ca carries no Elections Ontario
  results. The district boundaries *are* open (the Open Use Data Product Licence Agreement), but a map without
  results adds nothing. Publishing would need written permission from Elections Ontario.
- **British Columbia 2026-10-24** (`british-columbia-general-election-winner`). Skipped for now. Only the
  preliminary initial count exists, as HTML on `results.elections.bc.ca`, with no stated licence. The final
  count is scheduled for 2026-11-06 to 11-10. The Elections BC Open Data Licence covers the 2024 results CSV on
  the BC Data Catalogue and the 2023 boundaries (93 districts), so BC can be added the same way as Quebec once
  the 2026 final results are in that catalogue.
- **Alberta referendum 2026-10-19** (`will-alberta-vote-for-independence-in-2026`,
  `alberta-independence-referendum-margin-of-victory`). Skipped for now. Only unofficial results exist
  (`results.elections.ab.ca/data/8400`, no terms stated for that host), and the official results are due
  2026-10-29. Elections Alberta's [terms](https://www.elections.ab.ca/terms-conditions/) allow non-commercial
  reproduction only if "the materials not be modified", which rules out derived shares unless Elections Alberta
  agrees. Open boundaries exist (Provincial Electoral Division 2019, Open Government Licence – Alberta, via
  GeoDiscover). Of the 36 archived captures of the results page, none contains numbers, because the figures load
  by JS from `/data/8400`, which has no captures. Several of the referendum's markets were also still unresolved
  on 2026-10-28.
