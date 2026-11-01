# Early-vote database (`$BRUNDER_DATA/us_ev`)

**Status: data collection infrastructure, not a model input.** Official, openly licensed state publications of ballots already
cast (mail and in-person early voting), kept as immutable dated snapshots plus an aggregate SQLite database, refreshed every
3 hours until 2026-11-04T06:00Z. Code: `analysis/us_ev_db.py` (core, CLI, NC/FL/TX collectors), `analysis/us_ev_legacy.py`
(ingest of the files that pre-date the database), `analysis/us_ev_src_{a,b,c,d,e}.py` (the other states: A = AK AL AR AZ CA CO CT DC
DE GA HI ID, B = IA IL IN KS KY LA MA MD ME MI MN MO MS, C = MT NE NH NJ NM NV NY ND OH OK OR PA, D = RI SC SD TN UT VA VT WA WI WV
WY, E = county offices and the secondary UF tracker, see below). Tests: `tests/test_us_ev_db.py`, `tests/test_us_ev_src_*.py` (parsers on trimmed real excerpts, aggregate rows only).

## Layout (all on the SSD, `BRUNDER_DATA=~/brunder-data-local`; `BRUNDER_EV_ROOT` overrides the `us_ev` directory for scratch runs)

```
us_ev/raw/<state>/<YYYYMMDDTHHMMZ>_<source_id>[-<part>].<ext>   immutable snapshots (mode 0444), UTC fetch time
us_ev/MANIFEST.sha256                                            "<sha256>  <path relative to us_ev>" per snapshot
us_ev/ev.sqlite                                                  tables below (WAL)
us_ev/logs/{collect,loop}_<date>.log, loop_stdout.log
```

Tables: `sources(state, source_id, url, kind, format, cadence, access, licence, notes)`; `snapshots(snapshot_id, source_id, part,
state, url, fetched_utc, file, sha256, http_status, bytes, valid, etag, last_modified, parsed, error)`; `fetch_log(ts, state,
source_id, part, url, outcome, http_status, message, snapshot_id)` (outcomes: new, unchanged, skipped, failed, collector_ok /
_partial / _failed / _error); `ev_county(state, county_fips, as_of_date, measure, party, age_band, count, source_id,
snapshot_id)`; `ev_state(state, as_of_date, measure, party, age_band, count, source_id, snapshot_id)`; views `ev_county_latest`,
`ev_state_latest` (latest snapshot per key).

Vocabulary. `measure`: `mail_requested`, `mail_returned`, `mail_accepted`, `inperson_early`, `total` (mail + in person; never
requested). `party` (registration): `D`, `R`, `U` (unaffiliated), `other`, `ALL` (state does not split). `age_band`: `ALL` unless
published. `as_of_date` = the date the cumulative count is *through*, as established per source (the `notes` column of
`sources` says how: e.g. NC = day before the object's Last-Modified, FL = day before the file's CompileDate). `county_fips` =
5-digit FIPS (Connecticut planning regions and Alaska districts keep their geometry ids). A row is stored once per value: a new
snapshot only adds rows whose latest stored value differs (restatements are kept, `*_latest` picks the newest).
Semantics differ by state (returned vs accepted, rejected included or not): read `sources.notes` before comparing states. State
rows are county sums where the source gives no statewide figure (noted per source).

## Rules the code enforces

Honest client (`User-Agent: brunder/0.1 (research; recorder)`), at most one request per second per host, `robots.txt` honoured
(4xx = no restrictions, as in RFC 9309; a disallow means no collector, e.g. LA, VA). A response is saved **only** when HTTP 200 *and*
the generic check (magic bytes for PDF/ZIP/XLSX, JSON parse, no HTML where data was expected, no Cloudflare / Incapsula /
captcha / WAF markers) *and* the source's own validation pass; otherwise a `valid=0` snapshot row and a `fetch_log` line are written and
**no file** (error and challenge bodies are never cached). Unchanged data is skipped by `If-None-Match` / `If-Modified-Since`
(304) or by identical sha256. Bot walls, logins and captchas are never circumvented: such sources are registered with
`not_collected(...)` and shown in `sources` / `status`. Voter-level files (ME absentee voter file, NC per-ballot files) stay as raw
snapshots on the SSD; only aggregates reach the tables, and nothing here is committed or published.

## CLI

```sh
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/us_ev_db.py collect [--states nc,fl] [--sources nc_abs_counts]   # one pass, per-source log, exit 1 on failure
uv run python analysis/us_ev_db.py status          # per state: latest 2026 as_of, measures, party yes/no, level, last fetch ok/failed
uv run python analysis/us_ev_db.py sources         # registry incl. blocked / not published
uv run python analysis/us_ev_db.py ingest-legacy   # idempotent; already run
uv run python analysis/us_ev_db.py loop --every-hours 3 --until 2026-11-04T06:00:00+00:00
```

`loop` runs `collect` in a fresh subprocess each time (new or edited collector modules are picked up; a crash or hang never
stops the schedule). A collector is `@source(state, source_id, url, kind, format, cadence, ...) def collect_x(st)` that calls
`st.fetch(...)` (returns `Fetched`: `ok`, `changed`, `needs_parse`, `body`, `snapshot_id`) and `st.add_rows(rows, ...)`; a
parser is a pure function of bytes. One lock file prevents overlapping runs.

## Schedule (started 2026-10-31 20:19Z from the Mac that holds the SSD)

```sh
cd <repo>/ubiu-evdb && export BRUNDER_DATA=$HOME/brunder-data-local
nohup caffeinate -i uv run python analysis/us_ev_db.py loop --every-hours 3 --until 2026-11-04T06:00:00+00:00 \
  > $BRUNDER_DATA/us_ev/logs/loop_stdout.log 2>&1 & disown
```

Log: `us_ev/logs/loop_<date>.log` (and `collect_<date>.log`). Stop: `pkill -f "us_ev_db.py loop"`. The loop reads the code in this
worktree, so keep `analysis/us_ev_*.py` in place until it ends. Run `collect --states xx` by hand for an extra pass (a
concurrent run exits at once if the loop holds the lock).

## Data on the SSD before the database (read-only, `ingest-legacy`)

`tmp/ev_src/*_panel.csv` (state-level cumulative D/R/other by date, kinds mail / inperson / total, 2016-2026; the collectors behind
[us_ev_gap.md](us_ev_gap.md)) go to `ev_state` as `legacy_<module>_<state>`, `raw/us/ev_party/<state>/` and `raw/us/earlyvote/` files below
60 MB are registered as snapshots (sha256 in the manifest, not copied) as `legacy_raw_<state>`, and the 2026 NC county counts and FL
county stats files are parsed into the live sources' series. The TX/FL/NC pipeline outputs (`site/data/us2026/earlyvote.json`) are not
touched. Historical (2016-2024) daily county series do not exist in any open source (NC and FL publish final files only; state panels
above are reconstructions by return date, with the look-ahead caveats of [us_ev_gap.md](us_ev_gap.md)).

## Gap fillers (batch E, `us_ev_src_e.py`)

County offices that publish what a blocked or silent state does not. **County rows only, never state totals** (each source covers one
county or city, so a "state" figure would be wrong); all `party` ALL except Alameda.

| source_id | covers | measure | note |
|---|---|---|---|
| `ny_nyc_checkins` | NYC's five boroughs = counties 36061/36005/36047/36081/36085 | `inperson_early` | vote.nyc "Early Voting Check-Ins", cumulative at close of polls each day; each day's boroughs must add up to the printed citywide figure |
| `nv_clark_ev` | Clark County NV (32003) | `inperson_early` | Election Department turnout page, day totals accumulated; the sum must equal its "Accumulated Total"; unreported (zero) days are skipped |
| `tn_davidson_ev` | Davidson County TN (47037) | `inperson_early` | nashville.gov Early Voting Statistics, cumulative per day |
| `ca_alameda_vbm` | Alameda County CA (06001) | `mail_requested` (= ballots issued), `mail_returned`, by party | gives the party split the statewide `ca_ev_county` lacks; page has no timestamp, so as_of = fetch date (Pacific) |

**Secondary source `uf_election_lab`** (`kind = secondary`, `state = us`): the University of Florida Election Lab's early-vote tracker CSV
(`election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv`, all states, robots.txt allows it). Licence on its tracker page: Creative
Commons Attribution-NonCommercial-NoDerivatives 4.0, attribution required, non-commercial use only; figures are stored exactly as
published and shown with "Source: UF Election Lab (CC BY-NC-ND 4.0)". It is a compilation, not an official count: the Lab
fills some states by estimate or from AP and county contacts (its per-state notes say so, e.g. Michigan in-person is estimated, Arizona
"requests" are ballots issued). Rows go to `ev_state` under their own `source_id` and are never merged into official rows: use them only
for states with no official row, and `status` ignores them when it reports coverage. Mapping: request -> `mail_requested`, accept ->
`mail_accepted`, inperson -> `inperson_early`, voted -> `total`; dem/rep/none -> D/R/U; 0 means "not reported" and is not stored.

Looked at and **not collected** (`not_collected` entries in the same file, reasons in `sources`): Maricopa (Cloudflare 403), LA /
San Diego / Orange / San Bernardino / Sacramento (no daily data file), Santa Clara and Riverside (403), Franklin OH (voter-level files only),
Cuyahoga / Hamilton (nothing published), Summit OH (403), Detroit (403), Fulton / Cobb / DeKalb / Gwinnett GA (no statistics),
Westchester (403), Erie (nothing), Shelby TN (403), Clark's per-voter mail-ballot file (voter-level), Milwaukee / Madison / Minneapolis / Hennepin
(nothing published as data). Requests for access to the blocked state files are drafted (not sent) in the handoff notes.

## Sources (registry, as of 2026-10-31)

| state | source_id | level | format | cadence | status |
|---|---|---|---|---|---|
| AK | `ak_absentee_stats` | none | html | daily | blocked: Combined Ballot Report (htm) files return HTTP 405 'Human Verification' CAPTCHA page |
| AL | `al_absentee` | none | html |  | none published: the SoS absentee page (HTTP 200) has rules and forms only, no counts |
| AR | `ar_absentee` | none | html |  | none published: the SoS absentee page (HTTP 200) has rules only, no counts |
| AZ | `az_sos_ev` | none | html |  | blocked: azsos.gov and countingballots.maricopa.vote answer a Cloudflare 'Just a moment' challenge (403) |
| CA | `ca_ev_county` | county | pdf | daily (file per date; missing dates answer 40 | open |
| CO | `co_ev_county` | county | xlsx | weekdays, data through 11:59 PM of the previo | open |
| CT | `ct_ev_state` | state | html | daily (page for day D published D+1) | open |
| DC | `dc_ev_state` | state | pdf | daily | open |
| DE | `de_ev_county` | county | html | daily (timestamped 'Last Updated') | open |
| FL | `fl_county_stats` | county+state | txt (tab-separated) | daily (county compilations ~08:00 ET) | open |
| GA | `ga_ev_hub` | none | html |  | blocked: sos.ga.gov and mvp.sos.ga.gov answer a Cloudflare 'Just a moment' challenge (403) |
| HI | `hi_absentee_recon` | county | pdf | daily (one PDF per date, early-morning stamp) | open |
| HI | `hi_voting_report` | county | html | daily | open |
| IA | `ia_absentee_county` | none | pdf |  | blocked: HTTP 403 (bot wall) on the county PDF, the congressional PDF and the stats page, 2026-10-31 |
| ID | `id_ev_tracker` | county | json+csv | daily (~06:00 MT) | open |
| IL | `il_vbm` | none |  |  | none published |
| IN | `in_early_voting` | none |  |  | none published |
| KS | `ks_advance_voting` | none | dashboard |  | dashboard-only |
| KY | `ky_absentee` | none | pdf |  | none published |
| LA | `la_ev_parish` | none | pdf |  | blocked: robots.txt 'User-agent: * Disallow: /' on electionstatistics.sos.la.gov |
| MA | `ma_early_mail` | none |  |  | blocked: Incapsula (HTTP 307/x-iinfo challenge) on sec.state.ma.us; no statewide dashboard found |
| MD | `md_ev_inperson` | county | csv | once early voting has ended (Oct 22-29, 2026) | open |
| MD | `md_mail_county` | county | xlsx | daily (timestamped 'As of ... 6 PM') | open |
| ME | `me_absentee_file` | state | txt | periodic (several times a week near the elect | open |
| MI | `mi_voter_participation` | none | dashboard |  | dashboard-only |
| MN | `mn_absentee_county` | none | html |  | blocked: Radware bot-manager challenge (302 -> validate.perfdrive.com) for our research UA and for a plain req |
| MO | `mo_absentee` | none |  |  | none published |
| MS | `ms_absentee_county` | county | pdf | weekly (Mondays) | open |
| MS | `ms_absentee_state` | state | html | weekly (Mondays) | open |
| MT | `mt_absentee_count` | county | html |  | blocked: HTTP 403 (bot wall) for sosmt.gov and electionresults.mt.gov to the project UA (2026-10-31) |
| NC | `nc_abs_counts` | county+state | csv | daily ~08:40 UTC (S3 object, conditional GET) | open |
| ND | `nd_abev` | county | html | continuous (page has no timestamp) | open |
| NE | `ne_early_voting` | none |  |  | not published: page is voter guidance only (no counts, no data files; 2026-10-31) |
| NH | `nh_absentee` | none |  |  | blocked: HTTP 403 to the project UA on www.sos.nh.gov (2026-10-31) |
| NJ | `nj_periodic_reports` | county | pdf |  | not published pre-election: 2026 'Periodic Election Reporting' lists only election-night/post-election county  |
| NM | `nm_ev_results_portal` | none |  |  | not published: results portal is election-night only (shows 'Results last updated 10/22/2026', turnout 0); no  |
| NV | `nv_vivid` | county | dashboard |  | blocked: Incapsula challenge page (JS shell, 932 bytes) on nvsos.gov (2026-10-31) |
| NY | `ny_ev_public_reporting` | none |  |  | blocked: HTTP 403 to the project UA on publicreporting.elections.ny.gov and elections.ny.gov (2026-10-31) |
| OH | `oh_ev_dashboard` | county | html |  | blocked: HTTP 403 to the project UA on data.ohiosos.gov and www.ohiosos.gov (2026-10-31) |
| OK | `ok_ev_county` | county | pdf | daily ~07:30 CT (weekdays + weekend) | open |
| OR | `or_ballot_returns` | county | pdf | daily (Power BI export, ~afternoon PT) | open |
| PA | `pa_mail_daily_xlsx` | county | xlsx | daily ~08:00 ET | open |
| PA | `pa_mail_socrata` | county | json | daily ~05:00 ET (extract of the SURE system) | open |
| RI | `ri_ev_stats` | none | html |  | not published; elections.ri.gov answers HTTP 403 to the honest client |
| SC | `sc_absentee_county` | county+state | xlsx | daily (business days) by ~10:00 ET | open |
| SC | `sc_ev_county` | county+state | csv | daily ~10:00 ET | open |
| SD | `sd_absentee` | state | html | weekly (Fridays) | open |
| TN | `tn_early_voting_stats` | none | pdf | daily | blocked: HTTP 403 (CloudFront/WAF) on sos.tn.gov, tnsos.org/elections/ElectionData and sos-tn-gov-files.tnsosf |
| TX | `tx_evr` | county+state | json (base64 payload) | daily, close of business of each early-voting | open |
| UT | `ut_ev_stats` | none | xlsx |  | not published (in progress) |
| VA | `va_absentee_stats` | none | csv |  | not published (in progress); CSV host apps.elections.virginia.gov has robots.txt Disallow: / |
| VT | `vt_ballot_return_press` | none | html |  | press release only (no dataset) |
| WA | `wa_ballot_return_stats` | none | powerbi | business days after 5 p.m. | dashboard-only: Power BI publish-to-web embed (app.powerbigov.us); ballot-status-reports page returns HTTP 403 |
| WI | `wi_absentee_report` | none | csv | daily 7:30 a.m. | blocked: Cloudflare managed challenge (HTTP 403, cf-mitigated: challenge) for non-browser clients |
| WV | `wv_absentee_county` | county+state | xlsx | irregular (press releases ~every 2-3 days) | open |
| WY | `wy_absentee_stats` | none | html |  | not published |

Notes. `none` = looked at, nothing collectable: *blocked* (a bot wall answered the honest client or `robots.txt` disallows),
*dashboard-only* (Power BI / JS app without an export), *not published*. MN, WI, IA, MT, OH, TN, RI, NH, NY return a challenge or 403
to non-browser clients today (some answered a reviewer earlier: the block may be intermittent; they are not retried by the loop).
SD's page cell for 10/30 walk-in repeats the Republican figure, so the party sum is used. PA's xlsx is dated the day before its
stamp ("as of 10/31 08:00" -> 10/30). ND has no timestamp (as_of = fetch date). NM's portal is election-night only.

## UF Election Lab tracker (`uf_election_lab`, kind `secondary`)

A compilation of the state figures for all states (US.csv), kept under its own source_id and never merged into official rows.
Licence: CC BY-NC-ND 4.0; displayed with the permission of Dr. Michael McDonald, granted to the project (recorded 2026-11-01).
Label wherever shown: "UF Election Lab (compiled from official state sources), used with permission", linking to
https://election.lab.ufl.edu/early-vote/2026-early-voting. `analysis/us_ev_site.py` publishes it only for states with no official
count (status "compiled") and as a cross-check next to official figures; see [us_ev_site.md](us_ev_site.md).
