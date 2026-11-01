# Real US count nights: archived, timestamped county returns replayed through v1 / v3 / unified

**Status: internal validation.** The replay of `brunder.us.project` on real election-night count orders (2020, 2022, 2024) is built and run
(`analysis/us_real_nights.py`), but almost all usable county-by-time data is New York Times / Edison Research material whose terms forbid
copying, archiving and redistribution. It stays on the SSD (`$BRUNDER_DATA/raw/us/real_nights/`, each folder with a `LICENCE.txt`), and
**nothing derived from it is published here**: the result tables are in `$BRUNDER_DATA/derived/us_real_nights/REPORT.md` (+ CSVs) and are
not committed. The only state-published series that exists in the archive with a usable time resolution (Michigan 2022 governor, 5 captures)
is reported in section 5. Nothing under `brunder.us`, `brunder.nowcast` or the registered configs was touched.

## 1. What exists (searched 2026-10-31; every query went to web.archive.org's CDX API / `id_` captures or to GitHub raw, never to a live election host)

| source | what it holds | time resolution | usable? | terms |
|---|---|---|---|---|
| NYT/Edison 2020 **state pages** `static01.nyt.com/elections-assets/2020/data/api/2020-11-03/state-page/<state>.json`, Internet Archive captures | county returns of president, Senate, governor (and others), absentee split, precincts reporting, page time `last_updated` | 40-55 captures per state over 3-8 Nov 2020 (GA 55, AZ 40, MI 49, NC 46, TX 44 fetched) | yes, but captures are hours apart in the first night: sparse for Senate/governor | NYT terms of service (§2.1, 2.3, 4.1(2), 4.1(5): personal use only, no scraping, no caching/archiving or re-supply of data sets) -> internal only |
| NYT/Edison 2020 **race pages** (`race-page/<state>/<race>.json`) | same layout, one race | 2-20 captures (GA senate 5, MI senate 20, NC senate 2, TX senate 5; none for AZ special, NC governor) | merged into the state-page frames | as above |
| `github.com/d0ntw84f8/election2020-timeseries-data` `county-level-president/county_presidential.csv` (and state-level `house-senate-president`) | county president 3-8 Nov 2020, all states (~156 k rows), absentee split, file stamp + last_updated; **no precinct counts** | 19-135 frames per state (GA 124, PA 135, MI 81, TX 57, AZ 48, WI 46, NC 39, FL 32, OH 30, NV 19) | **yes, the dense 2020 source** (president only) | repo MIT licence covers the code/compilation; the counts are NYT/Edison, same restriction -> internal only |
| NYT 2022 race pages `.../2022/data/2022-11-08/results-<state>-<us-senate|governor>.json` | county returns, precincts reporting, `updated_at` | 4-10 captures per race between the evening of 8 Nov and 10 Nov, most after the count is mostly done | sparse only (error at 1-6 captured points per race) | NYT terms -> internal only |
| NYT 2024 feeds `.../pages/data/feeds/<uuid>/{pres,sen,gov}-county-data/2024-11-05.json` | every county of every race of the office: votes, `advance` (early) votes, precincts, expected votes, race `updated_at` | **pres 285 captures** (5 Nov 22:40 UTC - 12 Nov), sen 49, gov 36 | pres dense; sen/gov sparse in the night (2-6 captures before 90 % counted) | NYT terms -> internal only |
| Michigan Dept of State `mielections.us/election/results/DATA/2022GEN_MI_CENR_BY_COUNTY.xls` (tab-separated), Internet Archive captures | official county returns, all contests | 5 captures, 04:13 UTC 9 Nov - 05:14 UTC 10 Nov 2022 (0.3 / 8.7 / 37.5 / 65.7 / 99.5 % of the final vote) | yes (one governor night) | state public record; the michigan.gov terms of use (Dec 2021) bar automated access to the live site (not used: archive copies only), commercial use and copying / redistribution of content without permission, and no separate terms were found for the mielections.us file host -> my reading: aggregate derived statistics of public-record vote counts, non-commercial, are fine, raw files stay on the SSD (the integrator's call) |
| GA Clarity (`results.enr.clarityelections.com/GA/<id>/<ver>/json/ALL.json`) | official county JSON per version | `ALL.json` captured once per version; for the 2022 general (election id 115465) one version seen, 308628 at 00:42 UTC 9 Nov | no (single frames) | state record |
| FL `flelectionfiles.floridados.gov/enightfilespublic/` | official election-night files | one `ElecResultsFL.xls` capture for 2022 (01:14 UTC 9 Nov), PDFs for 2022/2024 | no | state record |
| NC `er.ncsbe.gov` / `dl.ncsbe.gov/ENRS` (queried through the archive only) | HTML shell of the single-page app (3-6 captures), absentee files; no results zips archived | - | no | state record |
| AZ `results.arizona.vote`, OH `liveresults.ohiosos.gov`, PA `electionreturns.pa.gov`, NV `silverstateelection.nv.gov` | JavaScript shells / API stubs / bot-challenge pages; a few county API calls for PA 2022 | no county time series | no | - |
| TX SoS `results.texas-election.com/static/data/election/47009/<ver>/` | `Federal.json` / `Race.json` per version: statewide totals and the race menu (7 versions, 9-10 Nov 2022); `County.json` captured for only 2 versions and not parsed | 7 | no (statewide) | state record |
| `MEDSL/election_night2020` (GitHub) | NYT-scraped 2020 president (state and 15-minute county tables); only the final county table is in the repository, no licence file | - | not needed (d0ntw covers it) | NYT data, no licence -> internal only |
| `alex/nyt-2020-election-scraper` (GitHub) | NYT "votes remaining" page, state level, president | - | no (no counties) | MIT licence on the code; NYT data |
| MIT Election Lab / Harvard Dataverse / tonmcg / other final-returns repositories; the CHI 2024 paper on vote-count visualisation | final returns, or text about election-night data | no timestamps | no | - |
| 2018 | nothing found: no NYT captures of the 2018 data files, Clarity GA 2018 only as HTML shells | - | no | - |

So **real county-by-time data exists densely for the 2020 president (10 target states) and the 2024 president (10 target states), and for
Senate/governor only as a handful of captures per race** (2020: 1-3 night captures, 2022: late captures, 2024: 2-6 night captures).

## 2. Snapshot format

`analysis/us_real_nights.py build` writes one parquet per race-night to `$BRUNDER_DATA/processed/us_real_nights/<year>_<state>_<office>[_sp][_i][_off].parquet`:

| column | meaning |
|---|---|
| `ts` | UTC time the source published that frame (NYT race `last_updated` / `updated_at`; d0ntw file stamp; for the Michigan file the archive capture time) |
| `fips` | 5-digit county FIPS (counties missing from `brunder.us.counties` dropped; duplicate captures with the same `ts` collapsed) |
| `d`, `r` | cumulative Democratic / Republican votes of the county |
| `tot`, `exp` | all-candidate votes, the source's expected vote (informational; not used by the model) |
| `ed`, `er` | early / absentee-advance Dem / Rep votes (NaN when the source has none) |
| `rep`, `units` | precincts reporting / total (NaN when absent) |

`feed_rows` turns a frame into the rows `brunder.us.project` reads (same columns as `analysis/us_calibrate.rows_at`): `reporting`/`units`
feed `f_complete`, `early` feeds `vote_type` (when the registered config of the state uses it).

## 3. Replay

For every night with a certified result in the unified panel (statewide two-party share and votes), every frame (frames with unchanged totals
are skipped) is scored under:

* **v1**: last same-office county result (else last president, shifted as in the backtest), votes = its votes (president base x 0.72 for midterms
  without a same-office base; president races: the previous president), no swing prior. Count settings (`tau`, `rho`, `f_max`, `f_complete`,
  `learn_turnout`, `vote_type`, `early_share`) = the registered v1 config of the state/office (`configs/us2026_<st>_<office>_v1.toml`, else the other
  office, else the civic template); OH's `use_outstanding` (no such field in the sources) is replaced by `f_complete = 0.98`. `turnout_ratio = 1`
  because the base votes already carry the ratio.
* **v1 (no flag)**: v1 as if the feed had no completeness flag (`f_complete = 0`, no learned turnout), a sensitivity on the 2024 nights.
* **v3**: v1 + the poll swing of the unified panel's final-week poll average (`swing_prior_mean = P - baseline`, `swing_prior_sd` = registered sd by
  number of polls, `count_scaled_terms`), when the race has polls.
* **unified**: the unified county prior fitted without the night's cycle (`Ctx.models`: leave-one-cycle-out share / turnout / state-vote models, the LOCO
  state forecast and its sd), `swing_prior_mean = 0`, same count settings. Senate/governor only: the fitted panel has no president.

Per night and variant (`night_summary`): pre-count error and P(correct winner); **minutes to P(true winner) >= 0.9 that then stays so** (frames up to
99.5 % counted; minutes since the first county reported votes; the resolution is the source's frame spacing); absolute error in points at the frame nearest
to 10 / 25 / 50 / 75 % counted (within 5 points of the target; "counted" = two-party votes / certified two-party total); 90 % coverage over the 5-90 %
grid (frame within 3 points). This is the same metric set as `analysis/us_unified_replay.py sim`, whose Georgia 2022 numbers are re-run for the comparison.

Night classes: **dense real** (>= 10 frames between 5 and 90 % counted), **sparse real** (the rest: errors at the captured points only, no minutes) and
**interpolated (pres clock)**: the 2024 Senate/governor races of the ten states, whose few real captures are completed on the county clock of the 2024 president feed
(`interpolate_race`): between two real captures of the race each county moves from its value at the earlier to its value at the later capture in proportion
to that county's president count progress; real values are reproduced exactly at capture times and only frames from the first real capture on are scored.
Leave-one-capture-out check (`interp_validation`): the 127 interior real captures of the nine nights are rebuilt from their neighbours with a mean absolute error of 0.08 points
in the statewide two-party share (median 0.002, largest 2.6); rebuilding the first real capture from zero is much worse (8 nights: mean 1.4, largest 5.8 points), which is why frames before the
first real capture are not scored.
This is a timing reconstruction, not a recording: the in-between mix of early and Election Day batches is assumed linear in the president's progress.

## 4. What was run

| class | nights |
|---|---|
| dense real, president | 2020: AZ, GA, MI, OH, PA, TX, WI; 2024: AZ, FL, GA, MI, NC, NV, OH, PA, TX, WI |
| dense real, Senate/governor | 2020 AZ special Senate; 2024 AZ Senate |
| interpolated (pres clock), Senate/governor | 2024: AZ, FL, MI, NV, OH, PA, TX, WI Senate; NC governor |
| sparse real, president | 2020: FL, NC, NV |
| sparse real, Senate/governor | 2020: GA, MI, NC, TX Senate, NC governor; 2022: 18 races of AZ, FL, GA, MI, NC, NV, OH, PA, TX, WI (Senate and governor; one MI governor night is the official file); 2024: 8 races |

62 night keys in the ten target states (the nine interpolated 2024 nights re-use the races of the sparse real 2024 captures, so they are not 62 distinct races; the other states' 2024 Senate/governor
frames are built but not replayed by default). Source copies: 222 MB on the SSD.

## 5. Results

The numbers derived from the NYT/Edison material (minutes to 90 %, errors at 10/25/50/75 % counted, coverage, paired v1 / v3 / unified differences, the Georgia real vs
simulated comparison) are **internal validation, not published**: `$BRUNDER_DATA/derived/us_real_nights/REPORT.md`. To publish any of it the integrator needs NYT's written
permission or equivalent official captures.

Official-source night (Michigan 2022 governor, certified two-party Democratic share 55.24 %), error of the projected final share in points (projection - final), model sd in points and
P(true winner) at each archive capture of the state file:

| capture (UTC) | counted | v1 error (sd) | v3 error (sd) | unified error (sd) | P(correct): v1 / v3 / unified |
|---|---|---|---|---|---|
| before the count | 0 % | -0.3 (7.0) | -3.0 (3.1) | -2.9 (3.0) | 0.76 / 0.76 / 0.78 |
| 9 Nov 04:13 | 0.3 % | -2.0 (8.0) | -2.8 (4.3) | -2.0 (4.2) | 0.66 / 0.72 / 0.78 |
| 9 Nov 07:02 | 8.7 % | -2.2 (5.9) | -2.5 (5.0) | -1.1 (5.0) | 0.69 / 0.71 / 0.80 |
| 9 Nov 09:51 | 37.5 % | -0.6 (4.0) | -0.7 (3.8) | 0.0 (3.9) | 0.88 / 0.88 / 0.91 |
| 9 Nov 16:33 | 65.7 % | 0.0 (2.4) | -0.2 (2.3) | 0.1 (2.3) | 0.98 / 0.99 / 0.99 |
| 10 Nov 05:14 | 99.5 % | 0.1 (0.4) | 0.1 (0.3) | 0.1 (0.4) | 1.00 |

One night, five captures taken by a crawler (capture time, not generation time of the file): a check of the plumbing, not evidence about the models.

## 6. Reproduce

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/us_real_nights.py fetch                 # raw copies -> $BRUNDER_DATA/raw/us/real_nights (archive.org only, 1 request / 1.2 s)
uv run python analysis/us_real_nights.py build                 # snapshots -> $BRUNDER_DATA/processed/us_real_nights
uv run python -W ignore analysis/us_unified_replay.py sim --n 30   # Georgia 2022 simulated reference (results/us_unified, gitignored)
uv run python -W ignore analysis/us_real_nights.py replay      # -> $BRUNDER_DATA/derived/us_real_nights/{replay_frames,replay_nights,replay_variants}.csv + REPORT.md
uv run python analysis/us_real_nights.py report                # rewrite REPORT.md from the CSVs
```

`replay --only <night key substrings>` writes to `derived/us_real_nights/partial/` and never overwrites the full run. Tests: `tests/test_us_real_nights.py`.

## 7. Limits

* NYT frames are the NYT's/Edison's view of the count, not the counties' own posting times; `ts` is the page's own time stamp (the Michigan file: the capture time).
* The d0ntw 2020 table has no precinct counts: 2020 president frames run without a completeness flag (the 2024 sensitivity "v1 (no flag)" shows how much that flag changes the 2024 nights).
  NYT `precincts_reporting` is not the GA/NC/FL/TX feed flag the registered configs were tuned on (some counties report 0 precincts while counting).
* No early-vote split exists for most 2022 and some 2024 races; `vote_type` is used only where the feed has `early` and the registered config asks for it.
* The unified challenger has no president version; president nights compare v1 and v3 only.
* Few races (62 nights, 11 dense Senate/governor or interpolated): differences between variants are small against night-to-night variation; the paired table is descriptive, not a test.
