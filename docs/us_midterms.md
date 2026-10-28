# US midterms 2026-11-03: election-night model (`brunder.us`)

Forward test of the Brazil question on the US midterms: does the Polymarket price of a statewide race lag what the
official county count already implies? Same engine as Brazil (`brunder.nowcast`), with counties as units and the
state as the region. Everything below was fixed before the first polls closed
(2026-11-03 23:00 UTC) and uses no 2026 count data; the configs are frozen and registered by the integrator.

## Races, feeds and markets

| race_id | config | official feed (recorded by `brunder snap`) | units | Polymarket market (yes_is = a = Democrat) |
|---|---|---|---|---|
| ca-gov | `configs/us2026_ca_gov_v1.toml` | CA SoS API `/returns/governor/county/all` (+ `/returns/status`) | 58 counties | `california-governor-election-2026` / `will-xavier-becerra-win-the-california-governor-election-in-2026` |
| tx-sen | `configs/us2026_tx_sen_v1.toml` | TX SoS Civix ENR election 53815 + countyInfo | 254 counties | `texas-senate-election-winner` / `will-the-democrats-win-the-texas-senate-race-in-2026` |
| tx-gov | `configs/us2026_tx_gov_v1.toml` | same | 254 counties | `texas-governor-winner-2026` / `will-the-democrats-win-the-texas-governor-race-in-2026` |
| oh-sen | `configs/us2026_oh_sen_v1.toml` | OH SoS NIST 1622 XML + summary CSV | 88 counties | `ohio-senate-election-winner` / `will-the-democrats-win-the-ohio-senate-race-in-2026` |
| oh-gov | `configs/us2026_oh_gov_v1.toml` | same | 88 counties | `ohio-governor-winner-2026` / `will-the-democrats-win-the-ohio-governor-race-in-2026` |
| ga-sen | `configs/us2026_ga_sen_v1.toml` | GA SoS Enhanced Voting ballot-item breakdown (day-of id) | 159 counties | `georgia-senate-election-winner` / `will-the-democrats-win-the-georgia-senate-race-in-2026` |
| ga-gov | `configs/us2026_ga_gov_v1.toml` | same | 159 counties | `georgia-governor-winner-2026` / `will-the-democrats-win-the-georgia-governor-race-in-2026` |

Candidates (a = Democrat, b = Republican): Becerra–Hilton, Talarico–Paxton, Hinojosa–Abbott, Brown–Husted,
Acton–Ramaswamy, Ossoff–Collins, Bottoms–Jackson. The Republican-side Polymarket markets exist too (e.g.
`will-the-republicans-win-the-texas-senate-race-in-2026`); the configs use the Democratic side only. Kalshi tickers
are matched in `configs/pairs/us2026.toml` (Kalshi workstream); Kalshi prices are shown credited "© Kalshi" under the
project's decision (`docs/kalshi.md`).

Not covered: Kansas, Michigan, Iowa (their results sites block or disallow automated access; not circumvented),
Maine (no official night feed) — see `deploy/brunder-snap-us.service`.

Alaska (Senate, governor) is excluded from the scored set. It holds a top-four primary and a ranked-choice general,
so one party can field several finalists: the certified 2026 primary has two major Democratic governor tickets
(Kreiss-Tomkins/Johnson 37,006 and Begich/Hnilicka 34,172 first-choice votes) and more than one Democrat in the
Senate race. The model's two sides are party totals, while Polymarket's Alaska markets are candidate-specific
(Peltola, Kreiss-Tomkins) and resolve on the ranked-choice tabulation, so model and market would answer different
questions. A candidate-level ranked-choice model was not built before the freeze. The Alaska parser, its fixture
and its priors stay (the feed is still recorded) for a later, separately registered model.

## Feeds and parsers

The night run never contacts the official feeds: `brunder snap us_midterms_2026` (deploy/brunder-snap-us.service)
polls them every 30 s and stores each changed response as `data/raw/snap/us_midterms_2026/<key>/<UTC time>.<ext>[.gz]`
(key = `snap._name(url)`). `brunder.us` reads the latest snapshot at or before each minute.

Each parser turns snapshot bytes into the same long table: `level` (state / county / district), `unit_id`,
`race` (gov / sen), `candidate`, `party`, `votes`, `early` (early in-person + mail votes where the feed splits
them), and the unit's progress fields `reporting`/`units` (precincts or polling places), `ballots` (counted),
`registered`, `outstanding` (ballots known to be uncounted). Unit ids are `us-<st>-<5-digit Census county FIPS>`
(`us-ak-hd01`…`us-ak-hd40` for Alaska), matching `site/data/geo/us_counties.topo.json` (`unit_id`).

| feed | format notes | fixture (tests/fixtures/us, fetched once on 2026-10-28) |
|---|---|---|
| CA | list of `{raceTitle "Governor - <County> County Results", Reporting "x% (k of n) precincts reporting", candidates[{Name, Party, Votes}]}`; `/status` adds per-county ballots counted and registration. Precincts reporting is not the share of ballots counted (mail and provisionals for weeks) | `ca_governor_county_all.json`: the live endpoint, currently showing the SoS's pre-election test data (Becerra v Hilton); `ca_status.json` |
| TX | base64-encoded JSON. `countyInfo`: per Texas county number `{N, Summary{PLR/NPL polling places, PRR/PRP precincts, VC, RV}, Races{id: {C{cand: {N, P, V, EV}}}}}`; county number = Texas SoS order (≠ FIPS order; table in `us.py`, checked against the feed's own lookup) | 2026 general 53815 (pre-election zeros, 3 counties) and the 2026 Democratic primary 53814 (real counts, 254 counties, Senate + governor only) |
| OH | NIST SP 1500-100 XML: county GpUnits with SubUnitsReported/TotalSubUnits, VotersParticipated, BallotsOutstanding (absentee, provisional); VoteCounts per GpUnit. CSV summary repeats the progress fields | the SoS's mock-election files of 2026-10-28 (`IsTest="true"`), trimmed to the governor and Senate contests |
| AK | JSON despite `.js`: contestNames, candidateNames "Last, First (PTY)", per house district per contest b/t/v/p/r and candidate votes | the certified 2026 primary (same file and format the general will use) |
| GA | ballot item `…/elections/Georgia/<id>/data/ballot-item/<item>`: statewide + per county `ballotOptions` with `groupResults` by vote type and precinct reporting status | 2024 general, president (certified; same API and format) |

GA recording: the original recorder only saved `…/<id>/localities` (per-county ballots cast, no candidate votes). The
integrator extends `snap._resolve` to also save `…/<id>/data` and the Governor / U.S. Senate ballot items as keys
`ga_enr_Georgia_data` and `ga_enr_Georgia_item_<itemId>`; `brunder.us` reads every `ga_enr_Georgia_item_*` key.

Tests (`tests/test_us.py`): county totals add up to the feed's statewide total for every fixture (Alaska federal
races: within 0.1 %, the gap being overseas federal-only ballots that belong to no district); unit ids equal the
Census 2020 county FIPS list (`census_national_county2020_subset.txt`); model sanity on synthetic partial counts;
an export dry run that checks the dashboard contract; malformed snapshots (truncated JSON, bad gzip, `.tmp/`
files) skipped in favour of the previous valid one; a failing feed parser or race model not blocking the other races,
with retries that neither duplicate nor drop minutes.

## County priors (official certified results)

`uv run brunder us priors` downloads (once, into `data/raw/us/`) and builds `data/processed/us2026/priors.csv`
(source, unit_id, dem, rep, total):

| source | race | origin | licence |
|---|---|---|---|
| `ca_gov_2022`, `ca_pres_2024` | CA governor 2022, president 2024 | CA SoS Statement of Vote (`csv-candidates.xlsx`, `csv-all-candidates.xlsx`) | public record |
| `tx_gov_2022`, `tx_sen_2024`, `tx_pres_2024` | TX governor 2022, Senate 2024, president 2024 | Texas Legislative Council, Capitol Data Portal (VTD returns summed to counties) | CC BY |
| `ga_gov_2022`, `ga_sen_2022`, `ga_pres_2024`, `ga_pres_2020` | GA certified results by county and vote type | GA SoS Enhanced Voting | public record |
| `ak_sen_p2026`, `ak_gov_p2026` | AK 2026 top-four primary by house district (built, not used: Alaska excluded) | AK Division of Elections (`enr26` while it still holds the primary; a copy is the test fixture) | public record |
| `oh_pres_2024` | OH president 2024 | MIT Election Data and Science Lab county returns, doi:10.7910/DVN/VOQCHQ | CC0 |

Ohio: the SoS sites answer scripts with a 403 block page and Dataverse requires a guestbook (name, e-mail,
institution), so the MEDSL file is not downloaded by code; save `countypres_2000-2024.csv` (original CSV) under
`data/raw/us/medsl/` by hand and rerun `brunder us priors --source oh_pres_2024`. Until then the Ohio races are
skipped (no prior → no rows). Alaska's server omits an intermediate certificate: run with the same
`SSL_CERT_FILE` as the recorder service, or keep the saved copy.

Checks: the built totals equal the certified statewide results (e.g. TX 2024 president 4,835,134 D / 6,393,403 R;
GA 2024 president 2,548,017 / 2,663,117; CA 2022 governor 6,470,104 / 4,462,914).

Prior per race (config `[prior]`): `share` = two-party Democratic share of the most comparable recent race (same
office in the last midterm where there was one; TX Senate: the 2024 Senate race; Ohio: 2024 president), `votes` =
that race's two-party votes, the base of the expected vote.

## Model

`brunder.nowcast` unchanged (docs/research_design.md): per unit prior share s0 and expected votes V0, observed
swing d = a/n − s0, region swing shrunk to the pooled swing, remaining votes projected at s0 + swing (+ ρ of the
unit's own deviation), variance from unit dispersion, region uncertainty and τ, the systematic error of the
uncounted vote. Two-party share; P(Democrat wins) = P(final two-party share > ½).

### What is new for the US: how much is counted (`[count]`, `brunder.us.CountRules`)

Brazil reports sections counted; US feeds report precincts, which say little about the mail, early and
provisional ballots still to come. So f = n / E, with E the expected two-party vote of the county:

- E = prior two-party votes × `turnout_ratio`; f is capped at `f_max` while a county is not known to be complete
  (a county above expectation then raises the turnout scale k of the whole state, as in nowcast).
- `f_complete` (TX, GA): once every polling place / precinct of a county reported, that county is taken as
  98 % counted (late mail and provisionals). TX and GA count almost everything on election night.
- `use_outstanding` (OH): once Election Day precincts are in, remaining = the outstanding absentee + provisional
  ballots the SoS reports per county, converted to two-party votes at the county's counted rate.
- `learn_turnout` (TX, GA, OH): complete counties' realised/expected vote ratio (shrunk with 200,000 pseudo-votes
  toward 1) rescales the expected vote of unfinished counties.
- CA ignores the completion flags (it counts mail for weeks), so f rests on E alone: 2022 governor two-party
  votes × 1.0.

### Vote types (`vote_type`, TX and GA)

The US count order problem: counties first post their early in-person + mail votes, then Election Day precincts,
and the two differ by many points (GA certified: Democratic two-party share 55.1 % in early+mail vs 42.1 % on
Election Day in the 2022 Senate race; 51.1 % vs 37.4 % for governor; 49.7 % vs 46.4 % in 2024). A county-level
model extrapolates the early lean into the uncounted Election Day vote. TX (`EV`) and GA (`groupResults`) split
every county's count by vote type, so with `vote_type = true` each county becomes two nowcast units — the early
batch (complete once posted) and the rest — with vote type as the region: the Election Day swing is learned from
Election Day returns. Before a county posts anything its expected vote is split `early_share` : rest (GA 0.64 =
2022 certified advance + mail share; TX 0.6, an assumption). If the feed carries no early split at all the model
falls back to one unit per county.

### Settings and their pre-election evidence (`analysis/us_calibrate.py`)

Georgia's certified 2022 and 2024 results by county and vote type are the only official US data at hand that
let a count be replayed in a realistic order. Simulated nights: each county posts its early batch in the first
~90 minutes (large counties later), then Election Day in four chunks over ~5 hours, the last one flagging the
county complete. Priors as on a real night (2024 president ← 2022 Senate × 1.30; 2022 governor and Senate ←
2020 president × 0.80, typical presidential/midterm ratios). The model runs with the production Georgia `[count]`
settings (f_complete 0.98, learned turnout, f_max 0.95, early_share 0.64). 30 nights per race, checkpoints at 5–90 %
counted.

| setting | 90 % coverage (worst checkpoint) | mean abs. error of projected share | error at 75 % / 90 % counted | raw count error at 75 % / 90 % |
|---|---|---|---|---|
| vote_type, τ = 0.04, ρ = 0.6 (chosen for TX, GA) | 100 % (100 %) | 1.81 pt | 0.50 / 0.20 pt | 2.00 / 0.52 pt |
| vote_type, τ = 0.03 | 95 % (79 %) | 1.81 pt | | |
| no split, τ = 0.06, ρ = 0.3 (chosen for OH) | 99 % (96 %) | 2.21 pt | 1.90 / 0.78 pt | 2.00 / 0.52 pt |
| no split, τ = 0.04 | 72 % (33 %) | 2.21 pt | | |

An earlier run used the default early_share 0.60 instead of the production 0.64; rerun with 0.64, the figures moved
in the third decimal (mean error 1.806 → 1.808 pt, error at 50 % counted 2.32 → 2.33 pt) and the chosen setting is
the same.

Rule (fixed before running it): the smallest mean error, then the smallest τ, among settings with ≥ 85 % coverage
at every checkpoint. These settings are in-sample for Georgia's past races; on 2026 they are forward.

- TX, GA: τ = 0.04, ρ = 0.6, σ_mun0 = 0.08, vote_type on.
- OH: τ = 0.06, ρ = 0.3 (no vote-type split in its feed; outstanding-ballot counts instead).
- CA: τ = 0.08, ρ = 0.3 — the no-split value widened by a third for a count that runs for weeks. A judgment, not
  calibrated: no official timing data for California's late count was available.
- κ, ν, σ_uf0: nowcast defaults. Brazil's τ was 0.015; the US values are 3–5 times wider.

Early-count errors (5–25 % counted) are about 2.5 points in every setting: before Election Day votes arrive, the
model only knows the early batches and the prior. The sd covers that, which is the point of the wide τ.

## Before the count (`brunder us preelection`)

```
uv run brunder us preelection configs/us2026_*_v1.toml --out site/data      # CI-safe: no private data needed
```

Writes `site/data/us2026/` with `meta.phase = "pre-count"`:
- per race the model's chance with zero votes counted (`races[].precount` and the `P`/`share`/`sd` of every
  timeline row). It is the frozen config applied to the county prior: the past certified results with their
  uncertainty, no polls.
- 14 days of prices every 30 minutes: `pm` from Polymarket's public CLOB price history, `kalshi` from Kalshi 1-minute
  candles sampled at each point (mid if the spread is at most 10c, else last trade). `counted`/`raw` are null.
- one map frame per race (nothing counted, projection = prior) and `prior_margin`.
- `stats.json` with null metrics, reason "count has not started".
- per-state poll closes (`poll_close_by_region`, `regions[].poll_close_utc`) and the Polymarket YES token ids.

Poll closes, Nov 4 UTC: Georgia 00:00 (7 p.m. ET), Ohio 00:30 (7:30 p.m. ET), Texas 01:00 (7 p.m. CT; 02:00 in
El Paso and Hudspeth, mountain time) and California 04:00 (8 p.m. PT). Standard time applies, because daylight saving
time ended on Nov 1. Sources: georgia.gov (vote in person on Election Day), Ohio SoS 2026 elections calendar, Texas
SoS news release of 2026-10-27, and the California SoS Elections Officers' Digest.

Priors come from `data/processed/us2026/priors.csv` when it exists, else from the committed `data_public/us2026_priors.csv`.
That file is the same derived county table (official public-record and CC BY sources, attribution header), written
by `brunder us priors`. Races without a prior (Ohio, until the MEDSL file is added) are listed with prices and
`model_note` "county prior not available yet". The command refuses to overwrite files whose phase is live or
replay (`--force` overrides).

On the night `brunder us live` writes the same files with `phase = "live"`. It keeps
`timeline_precount.json` and puts those rows in front of its minute rows, so the switch needs no manual step.
Until a race has returns, its map shows the prior frame.

## Live and replay

```
uv run brunder us priors                                         # once; copy data/processed/us2026/priors.csv to the server
uv run brunder us live configs/us2026_*_v1.toml --out <served data root> --until 2026-11-05T12:00Z
uv run brunder us export configs/us2026_*_v1.toml --out site/data   # replay from the archive afterwards
uv run python analysis/us_dryrun.py --out /tmp/us2026_dryrun     # synthetic night, end-to-end check (never publish)
```

`live` re-reads the recorder's archive every 30 s, extends the per-minute timeline and rewrites the files
atomically; `export` does the same pass once (optionally `--until`). Output under `<out>/us2026/`: `meta.json`,
`timeline.json` (keyed by race_id), `units/<race_id>.json` (one contract units object per race, frames every 5
minutes thinned to ≤ 60), `units.json` (the first race) and `timeline_tail.json` (the same shape as timeline.json
restricted to each race's last 60 minutes, plus `"tail": true` and `"since"`, the earliest row included: what the
live page polls; timeline.json stays complete for the first load). `counted` = model share of the expected two-party vote;
`pm` = Polymarket mid of the configured market (last trade if the spread exceeds 10c), as P(Democrat);
`kalshi` = Kalshi P(Democrat): the midpoint of the market's order book when the spread is at most 10c
(`brunder.kalshi.prob_from_book`), else null, from the latest `kalshi_books` snapshot at or before the minute
(written into the same archive by `brunder kalshi record us_midterms_2026 --pairs configs/pairs/us2026.toml`). The
market per race is the side-a ticker in `configs/pairs/us2026.toml`, preferring the one paired with the race's
Polymarket market (ca-gov `KXGOVCA-26-XBEC`; tx-sen `SENATETX-26-D`, tx-gov `GOVPARTYTX-26-D`, oh-sen
`SENATEOHS-26-D`, oh-gov `GOVPARTYOH-26-D`, ga-sen `SENATEGA-26-D`, ga-gov `GOVPARTYGA-26-D`); meta lists it with
our own paraphrase of its resolution. The party markets pay on who is sworn in, Polymarket's on the election
result. No pairs file or no books means a null column; a bad books file keeps the last valid book and never stops a
race. Market prices are display only: they are not inputs to the model.

Failure isolation on the night: the reader ignores the recorder's `.tmp/` directory; a snapshot that does not
decompress or is not a complete JSON/XML/text document is never cached and the previous valid snapshot of that
key is used instead (retried only if the file changes). A feed whose snapshot is well-formed but fails to parse
keeps its last parsed state (nothing parsed yet = no returns). Each race has its own cursor: a race whose model
step raises publishes nothing for that minute and is retried on the next pass (live: up to 3 passes, then the
minute is left out; replay: left out at once) while the other races keep publishing, with no duplicate minutes.
Open errors are printed every pass.

## Serving the live dashboard

Public URL on the night (pages and geometry from GitHub Pages, live files through the tunnel):

```
https://andylol111.github.io/understanding-brazilian-information-underpricing/election.html?id=us2026&live=<tunnel URL>
```

The tunnel URL is a random `https://<words>.trycloudflare.com` printed by cloudflared when brunder-tunnel starts,
and it changes whenever that service restarts (re-publish the link then):

```
ssh oracle-slotbank 'journalctl -u brunder-tunnel | grep trycloudflare'
ssh oracle-slotbank 'journalctl -u brunder-tunnel | grep -o "https://[a-z0-9-]*\.trycloudflare\.com" | tail -1'
```

`brunder-election-night.timer` (2026-11-03 22:30 UTC) starts three capped services: brunder-us-live (writes
`site/data/us2026/` every 30 s), brunder-site and brunder-tunnel (Cloudflare quick tunnel to 127.0.0.1:8790).
brunder-site runs `brunder.liveserve` (stdlib only, system `python3` ≥ 3.10, one asyncio loop): it serves only
`us2026/**.json`, `elections.json` and `summary.json` from `site/data`, and 404 for everything else (no directory
listing; `..`, percent-encoding, backslashes and symlinks out of the root are refused). Each file is read and
gzipped once per change (the model replaces files atomically) and served from memory with
`Cache-Control: public, max-age=10`, a content-hash `ETag` (a rewrite with the same content still answers 304),
`Last-Modified` and `Access-Control-Allow-Origin: https://andylol111.github.io` (also on 404, so a missing optional
file is a 404 to the page rather than a CORS error); GET/HEAD/OPTIONS only, no request bodies, 16 kB of headers,
at most 1024 open connections (the longest-idle keep-alive is closed to admit a new one; 503 only when all are
busy). One journal line a minute: `journalctl -u brunder-site` shows requests, 200/304/404 counts and MB sent.

With `&live=` the page polls timeline/stats/arb and the selected race's units file every 15 s with
`fetch(..., {cache: "no-cache"})`, so the browser revalidates (If-None-Match) and an unchanged file costs a 304.
A failed refresh keeps the last good data and backs off 15 → 30 → 60 → 120 s, reset on the next success; a hidden
tab does not poll. The top bar shows "Live · updated N s ago" or "Reconnecting… · updated N ago". Only a 404 counts
as a missing optional file; a 5xx from the tunnel is a failed refresh, never an empty timeline.

Local check: `PYTHONPATH=src python3 -m brunder.liveserve --root results/us_rehearsal/site_data --election us2026
--allow-origin http://localhost:8000 --port 8790` with `python3 -m http.server 8000 -d site`, then
`http://localhost:8000/election.html?id=us2026&live=http://127.0.0.1:8790/`.

Payload (synthetic rehearsal, `analysis/us_rehearsal.py`, 5.2 h of minutes): timeline.json 262 kB raw / 12 kB
gzipped; units/<race>.json 47–187 kB / 8–29 kB (bounded: ≤ 60 frames); meta.json 5 kB / 1.5 kB. The rehearsal
compresses unusually well; a timeline with realistic noise is ~10 gzipped bytes per row and race, so 7 races reach
~150 kB gzipped after ~21 h and ~250 kB (1.85 MB raw) at 37 h, every 30 s to every open page. If that matters,
the incremental format is: the exporter also writes `timeline_tail.json` (the last 60 minutes per race, ~6 kB
gzipped); the page loads timeline.json once and then polls only the tail, merging rows by `t`.

Load test (local, Apple M-series, server on Python 3.10, 2 min, 300 clients each polling timeline + stats + arb +
one units file every 15 s like the page, with If-None-Match, while the files are rewritten every 30 s):

| data | origin connections | requests (200/304/404/503) | p50 / p99 / max | server CPU (mean / max of one core) | RSS max |
|---|---|---|---|---|---|
| rehearsal | pool of 100 (as cloudflared) | 9584 (2400/2392/4792/0) | 3.9 / 13.1 / 70 ms | 2.2 % / 4.8 % | 30 MB |
| 37 h timeline (250 kB gz) | pool of 100 | 9600 (2400/2400/4800/0) | 4.1 / 10.8 / 41 ms | 1.7 % / 6.0 % | 35 MB |
| 37 h timeline | up to 4 per client (~1200 sockets > the 1024 cap: idle ones closed) | 9524 (2400/2362/4762/0) | 6.0 / 23.5 / 55 ms | 4.2 % / 9.1 % | 63 MB |

About 80 requests/s, a few percent of one core: well inside `CPUQuota=20%` and `MemoryMax=256M`. The limit is
egress, not the server: with a 250 kB timeline, 300 pages pull ~2.7 MB/s (the tail format above cuts that to
~0.1 MB/s). Gzipping that timeline once per change takes ~30 ms here (longer on the VM), once every 30 s.

## Known weaknesses

- The expected vote is the weak point where completion flags are ignored (CA): a turnout far from the assumption
  mis-states `counted` and the weight of the remaining vote; the sd only partly absorbs it.
- CA's late count (late mail, provisionals) is not modelled by vote type; τ = 0.08 is a judgment.
- Ohio's prior is a presidential race and needs the hand-downloaded MEDSL file; turnout ratio 0.72 is the mean of
  the 2022/2024 ratios in CA (0.69), TX (0.71) and GA (0.75) official totals.
- Calibration rests on one state, simulated (not recorded) count orders, and three races; the vote-type timing
  (early batch posted whole and first) is GA/TX practice, not a law.
- Two-party share ignores third parties; party markets ("Will the Democrats win…") and candidate markets
  resolve on the plurality winner, which can differ from the two-party majority only if third parties are large.
- A feed that flags a county complete too early makes the model overconfident there (f jumps to 0.98).
- Snapshot cadence is 30 s; the timeline is per minute, so the model lags the feed by up to ~90 s.

## Evidence label

Forward: configs frozen and registered before 2026-11-03 23:00 UTC, outcomes unknown. The Georgia calibration is
in-sample for its settings.
