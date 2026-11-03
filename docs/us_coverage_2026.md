# US 2026 election-night coverage of competitive races (added sources)

Status 2026-11-02 ~22:00 UTC. Every model here is a SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED (`role =
"secondary"` or "SECONDARY SOURCE … UNREGISTERED" header), outside registered scoring, in its own process and output
folder. Slices: Coverage (statewide gaps; House in WA, OR, NV, AK, ME, NH, MS; integration), CoverNE
(docs/us_coverage_2026_ne.md: NY, NJ, VA, PA, CT, MD, MA), CoverMW (IA, CO, MT, MO, MI, WI, CA, IL, KS, IN, KY, UT).

Competitive = statewide race with any of poll / Polymarket / Kalshi chance in [0.10, 0.90]
(site/data/us2026/overview.json); House district with model baseline or any market P(D) in [0.15, 0.85]
(house/districts.json, markets.json). 120 House districts qualify, 60 outside the 71 already live.

## Probe rules followed

Every probe from the Mac only (nothing requested from the election server), one polite GET per URL, User-Agent
`brunder/0.1 (research; recorder)`, robots.txt read first, ≥ 1.5 s between requests to a host, no retry, no bypass of
any challenge/403. Never requested: the do-not-request hosts (incl. civicapi.org, *.azurewebsites.us, electionreturns.pa.gov).
civicAPI coverage is judged from what the existing `brunder-civic-us` recorder will record (brunder.civic RACES:
`me-sen` 87188, `me-gov` 87189, `nv-gov` 87260, `nh-sen` 87283, `nh-gov` 87284, House 87524 = 435 district totals)
and from the recorded fixtures on disk (tests/fixtures/us/civic_nh_gov_2026.json.gz: 320 towns + 10 county roll-ups,
all mapped; civic_sc_sen_2026, civic_ia_pres_2024). No civic archive of the night exists yet on the Mac or the server
(server data/raw/snap holds only israel_2026, nz_2026). civicAPI does NOT list OR, MS, AK, MT races.

## Verdicts: statewide gaps

| Race | Chances (poll / PM / Kalshi) | Candidate sources tested | Verdict |
|---|---|---|---|
| ME-Sen | .63 / .705 / .685 | maine.gov results: CloudFront 403 (robots too) | **built on civicAPI** (recorded `civic_87188`, county rows) — `me_sen_v1` |
| NV-Gov | .57 / .785 / .755 | silverstateelection.nv.gov, nvsos.gov: Incapsula challenge ("Pardon Our Interruption", `x-iinfo`); elections.clarkcountynv.gov robots `Disallow: /`; electionresults.washoecounty.gov 503, washoecounty.gov/voters/results 403 | **built on civicAPI** (`civic_87260`) — `nv_gov_v1` |
| OR-Gov | .60 / .845 / .805 | results.oregonvotes.gov 200; its map service `orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData` 200 JSON (NE/NM vendor format, 36 counties) | **viable, built** (`or_gov_v1`, brunder.extra_or). User decision 2026-11-02: use the CDN host only; `*.azurewebsites.us` stays forbidden (exact host / subdomain rule, redirects never followed; test) |
| NH-Sen | .82 / .94 / .9255 | sos.nh.gov results 404 (now); nh.electionstats.com → nh.elstats2.civera.com robots `Disallow: /` | **built on civicAPI** (`civic_87283`) — `nh_sen_v1` |
| NH-Gov | .16 / .0765 / .0765 | as NH-Sen | **built on civicAPI** (`civic_87284`) — `nh_gov_v1` |
| MS-Sen | .33 / .11 / .105 | sos.ms.gov (Cloudflare): result archive pages/PDF only, no night feed; not in civicAPI RACES | **no data** |
| MT-Sen | .006 / .09 / .115 | electionresults.mt.gov Cloudflare 403; esersws.mt.gov GetMapData reachable (CoverMW, MT-01); not in civicAPI | **viable source, not built** (low priority per integrator; race id of the Senate contest not looked up) |
| AK-Sen, AK-Gov | .54/.745/.725; .93/.884/.865 | elections.alaska.gov is a do-not-request host (already recorded by brunder-snap-us); top-four ranked choice: party totals do not answer the candidate markets (brunder.us meta note) | **out of scope** (RCV; no added fetch allowed) |
| ME-Gov | PM .968 / Kalshi .9625 | — | not competitive |

## Verdicts: House, Coverage slice

| District | Model P(D) | PM / Kalshi | Source | Verdict |
|---|---|---|---|---|
| WA-05 | .50 | .166 / .155 | results.votewa.gov Enhanced Voting ballot item | **built** `wa-05_v1` |
| WA-03 | .77 | .895 / .904 | same | **built** `wa-03_v1` |
| WA-04 | .16 | .09 / .0975 | same | **built** `wa-04_v1` |
| ME-02 | .55 | .435 / .435 | maine.gov 403; civicAPI House race (district total + percent reporting) | **built on civicAPI** `me-02_v1` (RCV transfers not modelled) |
| NV-02 | .38 | .39 / .395 | NV sites blocked (above); civicAPI House race | **built on civicAPI** `nv-02_v1` |
| NV-03 | .84 | .955 / .951 | same | **built on civicAPI** `nv-03_v1` |
| AK-AL | .40 | .018 / .018 | do-not-request host; RCV | out of scope |
| OR, NH, MS districts | | | | none competitive |

WA and the civicAPI House races are modelled with ONE unit per district (exact district totals; precinct progress
summed from the county breakdown for WA, civicAPI percent for the others): no county pieces of the 2026 lines for these
states were in the House prior file, so prior = district 2024 president (data_public/us2026_extra_priors_district.csv,
equal to districts.json pres24). The count-order correction across counties is therefore absent for these seven.

## Verdicts: other slices (folded here)

CoverNE (detail docs/us_coverage_2026_ne.md): VA-01, VA-05 built (Virginia ENR items, 120 s, the pace of
brunder-extra-rec, whose regex records only Senate/VA-02/VA-07); MD-01 built (SBE static HTML); PA-11 **dropped**: its
document (districtId=12) is not in the registered recorder's PA list (districtIds 2, 8, 9, 11, 17 = PA-01/07/08/10/16) and
electionreturns.pa.gov must not get added requests; NY-01/02/03/17/21/23 blocked (state ENR Cloudflare challenge; county
sites loop/403/do-not-request host); NJ-02/07/09 no data (21 county sites, no statewide feed).
CoverMW (in progress; packages folded so far): IA-01/02/03 and CO-03/04/05/08 built on Clarity ENR — **server
blocked** (AWS WAF 202 challenge to the server IP, docs/us_midterms.md), Mac-verified; MT-01 built (esersws.mt.gov,
server unverified); MO-02 built, partial (St. Louis County PDF + St. Charles Live Voter Turnout page = 84 % of the
district's vote; Franklin/Warren projected); MI-10 built, partial 88 % (Macomb County PDF); MI-04 built, partial 26 %
(Ottawa County Clarity, server-blocked → Mac); WI-01 built, partial 38 % (Kenosha, Incapsula → Mac, and Rock County pages);
IN-05 built (enr.indianavoters.in.gov county file via settings.json
version, no precinct counts, server unverified); KY-06 blocked (vrsws.sos.ky.gov Akamai 403, Clarity KY 403). Verdicts for the rest of the slice: docs/us_coverage_2026_mw.md. Further
packages: CoverMW reports
directly to the integrator after this hand-off (fold steps below).

## Host table (Mac, 2026-11-02; second column of headers from the 100 s recorder smoke run)

| Host | Format / granularity | robots | 429 / Retry-After | Challenge markers | ETag / Last-Modified / Cache-Control | Poll | Status |
|---|---|---|---|---|---|---|---|
| results.votewa.gov | Enhanced Voting JSON, one item per district: county breakdown + precinct status (~0.5-0.7 MB) | 404 | none (8 req) | Cloudflare front (`cf-ray`, Azure FD `x-azure-ref`), no challenge | none / none / `public, max-age=60` | 60 s | viable; **Mac-verified, server unverified** |
| orresultswebservices.azureedge.us | vendor GetMapData JSON, county x candidate, precincts | 404 | none | Azure FD; backend cookie domain orresws.azurewebsites.us | none / none / `private` | 60 s | viable pending rule decision; server unverified |
| results.oregonvotes.gov | ASP.NET pages; export is a POST form (not used) | 404 | none | none (`volt-adc`) | `public, max-age=51` | — | page only |
| enr.elections.virginia.gov | Enhanced Voting JSON item (~1.5 MB) | 404 | none | Azure FD / Akamai markers | none / none / `public, max-age=37…60` | 120 s | viable; already used from the server by brunder-extra-rec |
| elections.maryland.gov | static HTML county table | 200 (results pages allowed) | none | Cloudflare | none | 60 s | viable; Mac-verified, server unverified |
| esersws.mt.gov | vendor GetMapData JSON | 404 | none | none | none / none / `private` | 60 s | viable; server unverified |
| electionresults.iowa.gov, results.enr.clarityelections.com | Clarity: elections.json, current_ver.txt, versioned ALL/summary/status JSON | 404 | none | AWS WAF 202 challenge **to the server** (Mac 200) | weak ETag; versioned files `max-age=86400`, CO `max-age=60, must-revalidate` | 60 s | server **blocked**; Mac only |
| extcontent.stlouisco.com, www.livevoterturnout.com | PDF report; 9.4 MB HTML (ETag/Last-Modified) | per CoverMW | none | Cloudflare / IIS | St. Charles ETag + Last-Modified, no-cache | 120 s | server unverified |
| silverstateelection.nv.gov, www.nvsos.gov | — | robots answers the challenge page | — | Incapsula | — | — | **blocked** |
| elections.clarkcountynv.gov | — | `Disallow: /` | — | — | — | — | **blocked (robots)** |
| electionresults.washoecounty.gov | — | 503 | — | — | — | — | no data now |
| www.maine.gov | — | 403 | — | CloudFront 403 | — | — | **blocked** |
| nh.elstats2.civera.com (nh.electionstats.com) | — | `Disallow: /` | — | — | — | — | **blocked (robots)** |
| www.sos.ms.gov | HTML/PDF archive | 200 | none | Cloudflare | `no-cache` | — | no night feed |

No 429, Retry-After or challenge was seen on any host used by the recorder. Every Cloudflare/Akamai/Azure-FD host is
"Mac-verified, server unverified": the recorder stops a host on its first 403/429/challenge and logs `event=blocked`.

## Market prices (added races)

`configs/us2026_extra/markets.json` (analysis/us_extra_markets.py, looked up from the Mac 2026-11-02): for all 27 added
races the Polymarket market (event, slug, YES token) and the Kalshi market (ticker) whose YES pays if the Democrat
(config side a) wins; every mapping printed with its title and live bid/ask and checked by a test against the config
(Polymarket event/market equal; Kalshi YES name = the Democrat). ME-02's Polymarket slug is
`will-the-democratic-party-win-the-me-01-house-seat-20260720160014016` (question: ME-02) as listed. The added recorder
polls ONE `POST clob.polymarket.com/books` (all tokens) and ONE `GET api.elections.kalshi.com/.../markets/orderbooks`
(all tickers) every 60 s, 429 → backoff honouring Retry-After (not a block), and stores `polymarket_books` /
`kalshi_books` in its own archive in the registered recorders' formats; the live wrapper maps them via markets.json, so
rows carry `pm` / `kalshi` = P(Democrat) through the unchanged exporter (registered pm_ws / kalshi recorders untouched).

## Partial coverage

A source module may declare `LIVE_UNITS = {kind: {unit ids}}` (MO-02, MI-04, MI-10, WI-01). The district's prior keeps
every county; units without a live source stay uncounted at their prior (the engine's normal treatment of unreported
units, no extra extrapolation). The row's `counted` is the live counted vote over the WHOLE district's expected vote.
meta.json of the extra output carries `coverage` (0-1, live share of the race's expected vote; 1.0 when full) for every
race and, below 1, `coverage_note` "partial: N% of the district's expected vote has a live source; the rest stays at its
prior". Test: MI-10 with Macomb fully counted reports counted ≈ coverage (0.88), not 100 %.

## Stale sources

During the night window (2026-11-03T23:30Z-2026-11-04T10:00Z) each output write checks every feed key of each race:
its last fresh time = latest of the recorder's per-key last successful poll (.poll/extra.json; the Mac run pushes
.poll/mac.json), the shared recorder's last finished cycle for linked civic keys, and the newest snapshot. A key older
than 10 min (or with no data) sets `stale: true` and `stale_since` (ISO UTC, the oldest stale key's last fresh time;
null if it never had data) on the race in meta.json and on its latest row in timeline.json / timeline_tail.json; the
next write without staleness drops them. Model numbers are unchanged. Test: synthetic 20-min-old snapshot.

## Integration convention (all slices)

* Source module `src/brunder/extra_<src>.py`: `NAME`, `RECORD` = [{key, url, interval ≥ 30, ext, optional
  `resolve(get)` (sync, called before each poll; `get` is the recorder's paced/robots-checked GET), optional `host_class`}],
  `FEEDS` {kind: {role: key or prefix*}}, `STATES`, `POLL_CLOSE`, optional `SOURCE_INFO`, pure `parse(kind, blobs)` →
  brunder.us.COLS rows (House: race "house-NN"; empty frame on empty/partial files). Registered in
  `src/brunder/us_extra_sources.py` MODULES.
* Configs `configs/us2026_extra/<st>-<nn>_v1.toml` (House) / `<st>_<sen|gov>_v1.toml`; `_v2` siblings are attached
  automatically. Priors `data_public/us2026_extra_priors_<src>.csv` (source,unit_id,dem,rep,total), merged at runtime
  only in the extra process (a source already in the committed files stays authoritative).
* Recorder `python -m brunder.us_extra_rec --out <archive> [--only/--skip <key prefix>]` writes its OWN archive
  (`data/raw/snap/us_midterms_2026_extra`, brunder snap layout + `.poll/extra.json` + `extra_manifest.jsonl` of every
  request with status and cache headers). Refuses the do-not-request hosts, honours robots, ≤ 1 req/s/host,
  conditional GET (validators reset when a versioned URL changes), unchanged bodies not stored, 5xx/transport errors
  back off (doubling ≤ 600 s), 401/403/429/challenge stops the host.
* Live `python -m brunder.us_extra_live <v1 configs> --snap <extra archive> --shared <shared archive> --out
  site/data/extra`: installs the modules' feeds/parsers/priors/archive checks into brunder.us in-process (frozen modules
  unchanged on disk), symlinks the shared keys it reads (civic_*, polymarket_books, kalshi_books; never a key the extra
  recorder writes), runs the unchanged statewide engine (`us.run_live`) and House engine (`us_house_night.run_live`).
  Output `<out>/us2026/{meta,timeline,timeline_tail,stats,units}.json` and `<out>/us2026/house_live/…` (same layout as
  `site/data/civic/us2026/`).

## Early signal from Indiana and Kentucky (display only, unregistered; added 2026-11-03)

`src/brunder/us_extra_inky.py`, run as one more thread of `brunder.us_extra_live` (crash-isolated: an exception is printed and the
cycle skipped, never raised into the models), writes `<out>/us2026/inky.json` every 30 s from files already on disk: the shared
archive's `civic_87524` (civicAPI House race, 435 districts: candidate votes + percent reporting; newest valid snapshot, a partial or
corrupt file falls back to the previous one), the added recorder's `in_house` (IN-05 county detail) and the committed baselines
(`site/data/us2026/house/districts.json`, `overview.json`, `data_public/us2026_extra_priors_in.csv`). No request to any host.

* Per district (IN, KY always; others from 20 % reporting): D two-party share so far D / (D + R) of the top Democrat and top Republican,
  2024 president D share and 2024 House D share on the 2026 lines (House: not for redrawn states), swing vs each, **swing vs expected**
  (counted share minus the district's pre-count expectation `baseline.share` = 2024 president on the 2026 lines + the poll-implied
  national swing; IN-05: minus the 2024 president of the counties that have reported + the same swing), counted votes vs expected votes
  at the reported % (expected = 0.72 x 2024 two-party votes x reporting %), an uncontested flag (no D-R pair on the ballot; no signal).
* Raw swings vs 2024 are labelled **composition-biased**: the first precincts to report, and early / mail ballots counted first, are
  not a random sample. The reporting % is shown with every figure.
* Aggregates, vote-weighted over contested districts with >= 10 % reporting: Indiana, Kentucky, both, and the nation
  (a national read only once 20 districts have >= 25 % reporting: "too early for a national read" before). `polls.text` compares with
  the swings the polls imply in the 7 Senate states (poll average of the dem side minus the state's 2024 two-party president share,
  `overview.json` and `state_check`).
* **Early-signal adjusted** (unregistered, not validated): the aggregate swing vs expected (national once ready, else IN + KY; at least
  5 districts) x n / (n + 20) is added to the pre-count share of every Senate and House race (a probit shift `dz` = shift / the race's sd;
  independent-aligned Senate seats, certain and uncontested seats stay). The site applies `dz` to the chance it shows for each race not
  yet counted and shows "Early-signal adjusted (unregistered, not validated): D N . R M" under each hemicycle; `adjusted.seats` is the
  same tally with every race at its pre-count chance. Nothing here is a model: `docs/us_crossrace.md` tested the sharing of information
  between races on the same ballot (small gain only with lagged reporting, bar not met); cross-state learning of a national swing was
  not tested.
* Pages: the night guide (section "Early signal") and the election page (tab "Early signal"), live pages only, read
  `extra/us2026/inky.json`; before the polls close they say "Waiting for 6 pm ET". Tests: `tests/test_us_extra_inky.py`,
  `tests/test_us_inky_js.py`.

## Test evidence

* `uv run --frozen pytest tests/test_us_extra.py tests/test_extra_ne.py tests/test_extra_mw.py tests/test_civic.py
  tests/test_us_feeds_extra.py`: 154 passed (Mac, after folding MI-04/MI-10/WI-01 and the market work). Includes: real VoteWA 2026-08-04 WA-05 item (totals = summaryResults,
  842/842 precincts, county breakdowns sum to the district); real Oregon 2026 pre-count map data (36 counties, parties
  from PartyName) + filled copy (exact sums); civicAPI House adapter; synthetic WA-03 snapshot → archive → House engine →
  `house_live/timeline.json` with counted > 0; ME-Sen civic document in a shared archive → link → statewide engine →
  `timeline.json` counted > 0, shared archive unchanged; recorder: change detection, 403 stops the host and no further
  request, forbidden hosts refused; link never touches recorder-owned keys; VA/MD/IA/CO/MT/MO 2024 files = certified
  totals (CoverNE/CoverMW tests).
* Mac smoke run (honest live requests, 100 s): 34 requests, 0 errors, 0 blocks, 12 snapshots (WA ×3, VA ×2, MD ×2,
  MT, IA Clarity ×3; CO 2026 general not yet listed); then `us_extra_live` with all v1 configs for 150 s on that archive:
  5 statewide + 17 House races written every cycle, House cycle 0.3 s, statewide pass < 1 s, peak RSS 200 MB; a found bug
  (MD HTML rejected by the archive's JSON/XML/UTF-8 check) fixed by per-key checks and retested.

## Install (integrator; nothing installed by Coverage)

1. Merge the cover branch (incl. folded CoverNE/CoverMW commits) where the server can fetch it. The pinned
   `/srv/brunder/repo` is NOT pulled: the units run from a separate checkout.
2. On the server: `sudo -n -u brunder git clone --branch <merged branch> <origin> /srv/brunder/usextra && cd
   /srv/brunder/usextra && sudo -n -u brunder /srv/brunder/.local/bin/uv sync --frozen`.
3. `sudo cp /srv/brunder/usextra/deploy/brunder-us-extra-{record,live}.service /etc/systemd/system/ && sudo mkdir -p
   /etc/systemd/system/brunder-site.service.d && sudo cp /srv/brunder/usextra/deploy/brunder-site.service.d/extra.conf
   /etc/systemd/system/brunder-site.service.d/` (first check `systemctl cat brunder-site` still matches
   deploy/brunder-site.service; the drop-in only adds `--election extra`).
4. `sudo systemctl daemon-reload && sudo systemctl enable brunder-us-extra-record.service brunder-us-extra-live.service`
   (WantedBy brunder-election-night.target: both start with the night stack at 22:30 UTC).
5. Pre-flight without network: `sudo -n -u brunder bash -c 'cd /srv/brunder/usextra && uv run --frozen pytest -q
   tests/test_us_extra.py tests/test_extra_ne.py tests/test_extra_mw.py'`.
6. Mac side (IA, CO, Ottawa MI Clarity and Kenosha WI: their hosts challenge the server's IP). On this Mac, in the
   merged checkout, any time before 23:30 UTC:
   `nohup deploy/mac_extra_rec_push.sh >/dev/null 2>&1 &`
   It waits for 2026-11-03T23:30Z, runs `brunder.us_extra_rec --only clarity_ --only mi_ottawa --only wi_kenosha`
   with this checkout's uv into `$BRUNDER_DATA/raw/snap/us_midterms_2026_extra_mac` until 2026-11-04T10:00Z, and every
   30 s rsyncs only those key folders into `oracle-slotbank:/srv/brunder/repo/data/raw/snap/us_midterms_2026_extra/`
   (`--rsync-path="sudo -n -u brunder rsync"`, files owned by brunder; never index.jsonl / .poll / manifest; rsync's
   dot-named temp files are ignored by the archive reader). Under `caffeinate -dims`; the recorder is restarted if it
   exits early and a failed push is retried on the next tick; nothing exits before UNTIL. Log: `<archive>.log`. The
   server recorder unit skips these keys (`--skip clarity_ --skip mi_ottawa --skip wi_kenosha`). Missing or broken
   pushed files = no data for those districts (pre-count frame), never a crash (test); if the Mac stops, the last pushed
   count stays. Tested 2026-11-02 22:23-22:26Z for 2 min against `/srv/brunder/scratch_cover_pushtest`: 5 snapshots
   (IA Clarity x3, Ottawa, Kenosha) recorded, 0 blocks, every push ok, files on the server owned by brunder; scratch dir
   deleted afterwards. The site reading `extra/us2026/` (front-end) is not built here.
7. Fold more CoverMW sources: cherry-pick, add the module to `MODULES` in src/brunder/us_extra_sources.py, add the v1
   configs to brunder-us-extra-live.service, rerun step 5.

Known limits: civicAPI's live cadence is unrehearsed (docs/us_midterms.md). Maine races are ranked-choice: first-round
two-party projection only.
