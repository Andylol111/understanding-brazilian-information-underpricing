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

Not covered: Kansas, Michigan, Iowa, Wisconsin, Nevada, New Hampshire, Maine and others (their results sites block or
disallow automated access, or there is no official night feed; not circumvented) — see "States checked" below and
`deploy/brunder-snap-us.service`.

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
| `pa_gov_2022`, `mn_gov_2022`, `nm_gov_2022`, `nm_sen_2024` | PA/MN/NM certified county results | PA DoS API, MN SoS results file, NM SoS archive (`brunder.us_feeds_extra`) | public record |
| `ne_sen_2024`, `ne_gov_2022`, `az_gov_2022` | NE Senate 2024 (Osborn as dem), NE/AZ governor 2022 | MEDSL 2024 Senate county file, 2022 precinct returns | CC0 |

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

## More states (added 2026-10-28; settings chosen without any 2026 general-election data)

| race_id | config | feed (recorder) | units | prior (official) | settings |
|---|---|---|---|---|---|
| sc-sen | `us2026_sc_sen_v1` | SC Election Commission Clarity ENR, `enr-scvotes.org/SC/<eid>/<version>/json/{ALL.json, en/summary.json, status.json}`; eid found on the day from scvotes.gov's results page | 46 counties | 2022 Senate share, 2022 governor two-party votes (Clarity, certified) | τ 0.06, ρ 0.3 (GA no-split calibration); complete = all precincts in (status.json PX = PY) → 98 %, learned turnout |
| sc-gov | `us2026_sc_gov_v1` | same | 46 counties | 2022 governor | as sc-sen |
| vt-gov | `us2026_vt_gov_v1` | Vermont SoS static JSON `static.electionresults.vermont.gov/elections/<guid>.json` + its statewide file, renamed on every update | 247 towns summed to 14 counties | 2024 governor (same incumbent), votes × 0.72 | τ 0.06, ρ 0.3; county complete when all its towns reported → 98 %, learned turnout |

Arizona governor: the feed is ready, and `parse_ga(item, "az")` reads AZ's Enhanced Voting ballot items, tested on
the live test data. AZ's certified county results (azsos.gov, apps.azsos.gov) sit behind a Cloudflare challenge that
is not circumvented, and Enhanced Voting holds no earlier AZ general election. On 2026-10-30 the race got configs
with a prior from MEDSL's 2022 precinct returns (next sections).

Settings: SC and VT feeds report no vote-type split, so they use the Georgia calibration without a split (τ 0.06,
ρ 0.3). Both states count on election night (SC early and absentee ballots, VT town hand counts including
absentees), so complete counties are taken as 98 % counted, as in GA and TX. SC's `PX`/`PY` were only seen on
finished elections (equal); that they mean reported/total precincts on the night is an assumption.

Florida was first skipped on 2026-10-28 for want of a county endpoint. It was added on 2026-10-29 (next section).

### North Carolina and Florida (added 2026-10-29; settings chosen without any 2026 general-election data)

| race_id | configs | feed (recorder) | units | prior (official) | settings |
|---|---|---|---|---|---|
| nc-sen | `us2026_nc_sen_v1..v3` | NCSBE ENR, `er.ncsbe.gov/enr/20261103/data/county.txt` + `results_<county id>.txt` (101 static files; `brunder snap --ncsbe <base>`) | 100 counties | 2022 Senate share and two-party votes (NCSBE certified precinct file `results_pct_20221108.zip`) | GA vote-type settings: τ 0.04, ρ 0.6, `vote_type` (early = one-stop + mail, reported per candidate), early_share 0.46 (NC 2022 Senate); complete = all precincts in → 98 %, learned turnout |
| fl-sen | `us2026_fl_sen_v1..v3` | FL Division of Elections election-night file `flelectionfiles.floridados.gov/enightfilespublic/20261103_ElecResultsFL.txt` (one tab-separated file, plain `--url`) | 67 counties | 2022 Senate (official results extract, results.elections.myflorida.com) | no vote-type split, as SC: τ 0.06, ρ 0.3; complete = all precincts in → 98 %, learned turnout |
| fl-gov | `us2026_fl_gov_v1..v3` | same | 67 counties | 2022 governor | as fl-sen |

- **Configs:** v2 and v3 were built by `analysis/us_polls.py configs` / `next` with the same rules as the other races. v1 is not registered, so `next` builds v3 from the race's own v2.
- **Parsers:** `parse_nc` and `parse_fl`.
  - NC was tested on all 100 county files of the 2022 general. The Senate statewide sum equals the certified Budd 1,905,786 / Beasley 1,784,049.
  - FL was tested on the 2022 official extract: Rubio 4,474,847 / Demings 3,201,522 and DeSantis 4,614,210 / Crist 3,106,313. It was also tested on the 2024 election-night file and on the 2026 file, which is still the zeroed test deck.
- **Recorder cost:** `snap` fetches resolved URLs 8 at a time, so NC's 101 files per poll do not delay the other feeds.
- **NC early-vote bias:** the NC 2022 one-stop vote was more Republican than the final result. In an early-votes-only replay against a 2024-president prior, the share was −3 pts, within 1 sd. Election Day precincts correct it.
- **FL count order:** FL posts early and mail votes at 7 p.m. local time with 0 precincts reported. The model reads that as votes counted against expected votes, since the file has no split.

**States checked and left out on 2026-10-29** (no bot protection circumvented):

| state | reason |
|---|---|
| Pennsylvania | electionreturns.pa.gov is served behind Incapsula |
| Wisconsin | elections.wi.gov answers 403 under Cloudflare, and there is no statewide election-night feed (counties report) |
| New Hampshire | sos.nh.gov answers 403 under Akamai ("Access Denied") |
| Maine | maine.gov answers 403, and there is no official election-night feed |
| Nebraska | electionresults.nebraska.gov is open, but serves ASP.NET HTML pages only; there is no machine-readable county endpoint and no parser in the time available. Senate is Osborn (independent) v Ricketts |

Pennsylvania and Nebraska were revisited on 2026-10-30 and added (next section): PA's results *API* answers plain
requests (only its HTML landing page carries the Incapsula script), and Nebraska's county maps are drawn from a JSON
web service.

### Pennsylvania, Minnesota, Nebraska, New Mexico, Arizona governor (added 2026-10-30; no 2026 count data used)

Code: `src/brunder/us_feeds_extra.py` (feeds, parsers, priors; `brunder.us` merges its `FEEDS`, `STATES`,
`PRIOR_SOURCES`, `SOURCE_INFO` and dispatches `parse_feed`). Every feed is recorded with a plain `--url` and was
fetched from both a Mac and the recorder VM (HTTP 200, same bytes) on 2026-10-29/30, cache-buster `t=` included.

| race_id | configs | feed (recorder `--url`) | units | prior | settings |
|---|---|---|---|---|---|
| pa-gov | `us2026_pa_gov_v1..v3` | PA DoS `electionreturns.pa.gov/api/ElectionReturn/GetCountyBreak?officeId=3…&electionid=117…` (2026 general = election 117) | 67 counties | 2022 governor (Shapiro v Mastriano), same API, finished election 94 | GA vote-type settings τ 0.04, ρ 0.6, `vote_type` (early = mail-in), early_share 0.23 (PA 2022 governor); no complete flag (f_complete 0), f ≤ 0.95, no learned turnout |
| mn-sen | `us2026_mn_sen_v1..v3` | MN SoS `electionresultsfiles.sos.mn.gov/20261103/allracesbycounty.txt` | 87 counties | 2022 governor share and votes (MN SoS results file) | no split, τ 0.06, ρ 0.3; complete = all precincts in → 98 %, learned turnout |
| mn-gov | `us2026_mn_gov_v1..v3` | same file | 87 counties | 2022 governor | as mn-sen |
| ne-sen | `us2026_ne_sen_v1..v3` | NE SoS `neresultswebservice.azurewebsites.us/ResultsAjax.svc/GetMapData?…raceID=13224&osn=102…` | 93 counties | 2024 Senate share with Osborn as side a (MEDSL county file, CC0), 2022 governor votes | as mn-sen; side a = Dan Osborn (party code PET, "By Petition"), no Democrat on the ballot |
| ne-gov | `us2026_ne_gov_v1..v3` | same service, `raceID=13508&osn=105` | 93 counties | 2022 governor (MEDSL 2022 precinct returns, CC0) | as mn-sen |
| nm-gov | `us2026_nm_gov_v1`, `_v2` (= v1, no polls) | NM SoS `nmresultswebservice.azurewebsites.us/NMResultsAjax.svc/GetMapData?type=SW…raceID=11530&osn=202…` | 33 counties | 2022 governor (NM SoS archive, election 2838) | as mn-sen |
| nm-sen | `us2026_nm_sen_v1..v3` | same service, `type=FED…raceID=11185&osn=180` | 33 counties | 2024 Senate share (NM SoS archive, election 2882), 2022 governor votes | as mn-sen |
| az-gov | `us2026_az_gov_v1..v3` | AZ Enhanced Voting (already recorded, `--ga-enr …/Arizona`) | 15 counties | 2022 governor (Hobbs v Lake; MEDSL 2022 precinct returns, CC0) | as CA (days-long count of late early ballots): τ 0.08, ρ 0.3, f ≤ 0.90, no complete flag, no learned turnout |

- **Parsers** (`tests/test_us_feeds_extra.py`), each statewide sum equal to the certified result:
  - PA, 2022 governor, all 67 counties: Shapiro 3,031,137 / Mastriano 2,238,477; mail-in read as early.
  - MN, 2022 governor: Walz 1,312,349 / Jensen 1,119,941; 2024 Senate: Klobuchar 1,792,441 / White 1,291,712. DFL is
    read as DEM. County ID = alphabetical index, FIPS = 2 × ID − 1 (checked against the SoS county table).
  - NE, 2020 Senate (the archive endpoint does not serve the 2022/2024 generals' pages; the schema is the 2026 one):
    Sasse 583,507 / Janicek 227,191.
  - NM, 2022 governor: Lujan Grisham 370,168 / Ronchetti 324,701; 2024 Senate: Heinrich 497,333 / Domenici 405,978.
  - All four also parse the 2026 files as published now (zeroed test decks; MN's holds test numbers). NM shows `*`
    as a county's votes until it reports (read as 0). NE/NM rows are kept only if `RaceName` is the expected race.
- **Priors** (`data_public/us2026_priors.csv`, rows appended, existing rows unchanged): each equals the certified
  totals above; NE 2024 Senate Osborn 436,493 / Fischer 499,124; NE 2022 governor Blood 242,006 / Pillen 398,334;
  AZ 2022 governor Hobbs 1,287,875 / Lake 1,270,796 (MEDSL; the canvass has 1,287,891 / 1,270,774, ≤ 0.002 % off).
- **Snapshot checks:** PA answers a JSON *string* holding the document, and MN's file is ISO-8859-1 text; both keys get
  their own completeness check in `Archive` (`us_feeds_extra.CHECKS`: both JSON layers parse; text ends with a line
  break). Every other key keeps `_check`.
- **Existing states unchanged:** a regression test runs every earlier parser on every earlier fixture through
  `parse_feed` and compares SHA-256 digests with those computed by the code of commit 3fa8783
  (`tests/fixtures/us/parse_digests_head.json`); the earlier `FEEDS` keys are compared too.
- **Race ids:** NE/NM race ids are the 2026 general's, read from the states' results pages (`BuildCounty(type, race,
  osn)`); a changed id would yield no rows, not wrong rows.
- **PA counting:** the API has no precinct counts and the county status messages are unreliable (Philadelphia 2022
  still reads 89 of 1,703 divisions after certification), so no PA county is ever taken as complete.
- **Settings** were set by analogy (no PA/MN/NE/NM night replay exists): vote-type states as GA/NC, no-split states
  as SC/FL, AZ as CA.

**States checked on 2026-10-29/30** (Mac and recorder VM; no bot protection circumvented):

| state (races) | endpoint checked | Mac | VM | status |
|---|---|---|---|---|
| PA (gov) | electionreturns.pa.gov API | 200 | 200 | added |
| MN (sen, gov) | electionresultsfiles.sos.mn.gov media files | 200 | 200 | added (the results website itself is behind a Radware bot manager; the files host is not) |
| NE (sen, gov) | neresultswebservice.azurewebsites.us | 200 | 200 | added |
| NM (gov, sen) | nmresultswebservice.azurewebsites.us | 200 | 200 | added |
| AZ (gov) | app.enhancedvoting.com (Arizona) | 200 | 200 | added (prior: MEDSL 2022) |
| MI (sen, gov) | mielections.us | timeout | timeout | no (earlier Cloudflare) |
| WI (gov) | elections.wi.gov | 403 CF | 403 CF | no; no statewide night feed |
| IA (sen, gov) | electionresults.iowa.gov (Clarity) | 200 | 202 challenge | no (recorder runs on the VM) |
| KS (sen, gov) | sos.ks.gov | — | — | no: robots.txt disallows /ent/ and /api/ |
| NV (gov) | silverstateelection.nv.gov | Incapsula JS | 403 | no |
| NH (sen, gov) | sos.nh.gov | 403 Akamai | 403 Akamai | no |
| ME (sen, gov) | maine.gov | 403 | 403 | no; no night feed |
| CO (sen, gov) | results.enr.clarityelections.com/CO (county Clarity) | 200 | 202 challenge | no |
| NY (gov) | nyenr.elections.ny.gov | CF challenge | CF challenge | no |
| IL (gov, sen) | elections.il.gov | 200 | 403 CF | no; no statewide night feed |
| MA (gov, sen) | electionstats.state.ma.us | 200 | 200 | no: certified results only |
| KY, AR, WV | results.enr.clarityelections.com | 403/200 | 202 challenge | no |
| OK, MT, TN | results.okelections.gov, electionresults.mt.gov, sos.tn.gov | 403 | 403 | no |
| SD | electionresults.sd.gov (same vendor as NE/NM) | 200 | 200 | feasible, not added (uncompetitive, low volume) |
| OR, VA, LA, RI, CT, MD, DE, HI, WY, MS, AL | state results sites | 200 | 200 | not pursued (uncompetitive, low market volume) |
| AK | elections.alaska.gov (recorded) | 200 | 200* | excluded (ranked-choice; see above). *VM needs the unit's SSL_CERT_FILE |

## Polls

Main fixed this rule before any average was computed. The code is `brunder.us.poll_rows` / `poll_average`, and
`analysis/us_polls.py` runs it.

- **Source:** VoteHub Polling API, `api.votehub.com/polls?poll_type=us-senator|governor`. Licence CC BY 4.0
  ([votehub.com/polls/api](https://votehub.com/polls/api/)). The raw files are cached under `data/raw/us/polls`
  (gitignored). Only our averages are published, in `data_public/us2026_polls.json`, with credit to VoteHub.
- **Which polls count:** a poll counts toward a race if its subject is "2026 <State>" and its type is the race's
  office, if it names the Democratic-side candidate (same surname), and if it has a sample size.
- **Share:** the two-party Democratic share is D / (D + opponent). The opponent is the modeled config's Republican
  if the poll names them; otherwise it is the strongest other candidate in the poll.
- **One version per poll:** a poll is one pollster with the same field dates. Likely voters are preferred over
  registered voters, and registered voters over adults. After that, the version with the most candidates (the full
  ballot) is kept.
- **Window:** field end dates in the 45 days up to 2026-11-02.
- **Weights:** each poll gets weight ∝ √n × 0.5^(age_days / 14). No pollster may hold more than 25 % of the total
  weight; when fewer than 4 pollsters are in the window the cap is 1/k for k pollsters. Weight above the cap is
  given to the other pollsters in proportion to their weights.
- **Error:** sd = √(0.039² + Σ w² s(1−s) / n₂), where n₂ is a poll's two-party respondents.
  - 0.039 is the RMSE of final-week poll averages, two-party Democratic share, Senate and governor races 2016–2022.
    It covers 167 races and 544 polls in FiveThirtyEight's pollster-ratings `raw_polls.csv` (CC BY 4.0; polls
    with median field date ≤ 7 days before the election). On the margin it is ≈ 7.8 points.
  - Over the same races the polls overstated the Democrat by 1.4 points on average. That bias is not corrected.
  - Adding the sampling term on top counts some sampling error twice, so the sd is slightly conservative.
  - `uv run python analysis/us_polls.py error` reproduces the 0.039.
- **Chance from polls:** p_win = Φ((avg − 0.5) / sd).
- **No polls in the window:** the race gets no poll prior and v2 = v1. `polls` is null in the overview.

Model v2 (`configs/us2026_<st>_<race>_v2.toml`) is v1 plus a prior on the statewide swing. That prior is
`brunder.nowcast.Params.swing_prior_mean / swing_prior_sd`, optional and off by default: Brazil and v1 results are
bit-identical, tested against golden values.

- **Prior:** the mean is poll_avg − the prior's statewide share (V-weighted, the model's expected-vote weights). The
  sd is poll_sd. Before the count, the counties therefore aggregate to the poll average.
- **During the count:** the statewide swing is the precision-weighted mix of this prior and the swing observed in
  counted votes.
  - The observed swing's variance is the model's between-county variance of the counted units' weighted mean (σ_mun²
    Σn²/(Σn)²). With several pooled regions (the GA/TX vote-type split) it also includes the between-region variance.
  - Early on the polls dominate; as counties report, the count takes over.
  - Every region's swing moves by the same amount, so per-county deviations work as in v1.
  - The uncertainty of the remaining-vote projection uses the posterior variance of the swing, with τ unchanged.
- **Wider before the count:** the pre-count sd is √(τ² + σ_uf0² + poll_sd² + county terms), wider than v1's.
- **Settings:** everything else (τ, κ, ρ, count rules) is identical to v1. The poll average, its date and the swing
  prior are frozen into the config; nothing is fetched on the night.
- **Known bias:** historically the final polls overstated the Democrat by 1.4 points (two-party share; same 538 data).
  This is not corrected.
- **Ohio:** it has polls but no county prior, so there is no baseline and v2 = v1 there (P_v2 is empty, as P is).
- **Effect:** `analysis/us_polls.py effect` tabulates P vs P_v2 at 0/1/5/10/25/50 % counted. It covers Georgia
  2022 replays (certified vote-type results, simulated count order, v2 prior from our rule on 538's 2022 polls) and
  synthetic nights for every modeled race.

## Early vote by county (display only; `brunder us earlyvote`)

`site/data/us2026/earlyvote.json` lists ballots already cast, by county, from official state publications. It is
not used by any model. `brunder us preelection` also refreshes it, so the 30-minute CI run keeps it current. If a
state fails, its last section is kept and marked `stale`.

| state | source | fields | cadence |
|---|---|---|---|
| TX | Texas SoS Early Voting Turnout (goelect.txelections.civixapps.com `getFile?type=EVR_EARLYVOTING`) | cumulative in person, by mail | daily |
| FL | Florida Division of Elections `Stats_<n>_VbmVoted.txt` / `_EarlyVoted.txt` | mail voted, early voted, by party registration | daily |
| NC | NC State Board `absentee_counts_county_20261103.csv` (dl.ncsbe.gov) | early voting (one-stop), mail returned, by party registration | daily ~08:40 UTC |

- **Licence and privacy:** all three sources are public record. Only county counts are written; the Texas statewide
  roster (voter names) is never fetched.
- **Baseline:** `share_of_2022_total` divides ev_total by the county's 2022 total votes, listed in
  `data_public/us2026_ev_baseline.csv`:
  - TX: 2022 governor (Texas Legislative Council);
  - FL: 2022 governor (Florida Division of Elections results extract);
  - NC: 2022 US Senate (NCSBE `results_pct_20221108`).
- **NC caching:** the 23 MB NC file is cached under `data/raw/us/earlyvote` by its S3 LastModified, so it is
  downloaded once per publication. CI needs a cache of that directory for this to work there.
- **Not available:** Georgia (sos.ga.gov) and Arizona (azsos.gov) sit behind a Cloudflare challenge, which is not
  circumvented.
- **Not attempted yet:** SC, CA, VT, PA, MI.

Poll commands (section above):

```
uv run python analysis/us_polls.py build      # VoteHub → data_public/us2026_polls.json (overview polls)
uv run python analysis/us_polls.py configs    # v2 configs from v1 + averages (then freeze: commit + register)
uv run python analysis/us_polls.py effect     # P vs P_v2 as the count grows (data/processed/us2026/polls_v2_effect.csv)
```

## Model v3 (Ohio v4): pre-count uncertainty = poll error only

`analysis/us_polls.py next` writes each race's next version from its registered poll model. It never writes
registered files and is idempotent. Two changes, nothing else:

1. **`count_scaled_terms = true`** (`brunder.nowcast`, optional, off by default; v1 and v2 are bit-identical).
   - The reporting-order term (τ) and the between-region term are scaled by the weight the counted votes have in
     the swing, `w_obs`. That weight is 0 before the count and approaches 1 as counties report.
   - These terms describe how counted votes misstate the remaining ones. Stacking them on the poll prior at 0 %
     counted (v2) double-counted.
   - Identity, tested: before any vote, share sd² = poll sd² + σ_mun0² ΣR²/(ΣR)², the poll error plus county noise.
2. **`swing_prior_sd` by number of polls.** Our averaging rule was replayed on 538's raw_polls for 2016–2022
   (192 Senate and governor races, CC BY 4.0; `analysis/us_polls.py errorbins`).

   | polls in window | races | RMSE (two-party share) | used |
   |---|---|---|---|
   | 1–2 | 56 | 0.0429 | 0.0429 |
   | 3–5 | 50 | 0.0331 | 0.0334 (3–10 pooled) |
   | 6–10 | 29 | 0.0338 | 0.0334 |
   | 11+ | 57 | 0.0228 | 0.0228 |

   - The 3–5 and 6–10 bins are pooled because 6–10 was not more accurate; the sd never decreases with fewer
     polls.
   - The bin RMSE is used as is, never below it, with no extra sampling term since the RMSE already contains it.
   - Held out: bins fit on 2016–2020 put 70 % of the 2022 races within ±1 sd (nominal 68 %) and 92 % within
     ±1.645 sd (90 %).
   - Polls overstated the Democrat by 1.4 points on average; this is not corrected.

3. **Count-time check** (`analysis/us_polls.py calib`): Georgia 2022 governor and Senate, certified results by
   vote type, 40 simulated nights each with early and mail votes posted first. Poll priors use our rule on 538's
   2022 polls. GA 2024 has no polls in that file, so it is not included. All four variants put the final share
   inside the 90 % interval in every case:

   | % counted | mean \|z\| v1 / v2 / v3 | margin MAE (pts) v1 / v2 / v3 | margin sd (pts) v2 / v3 |
   |---|---|---|---|
   | 1 | 0.45 / 0.24 / 0.19 | 4.6 / 2.4 / 1.2 | 10.3 / 6.3 |
   | 5 | 0.71 / 0.38 / 0.33 | 6.3 / 3.6 / 2.0 | 9.6 / 6.0 |
   | 10 | 0.84 / 0.46 / 0.44 | 6.9 / 4.1 / 2.4 | 9.0 / 5.5 |
   | 25 | 1.03 / 0.67 / 0.73 | 7.0 / 4.9 / 3.5 | 7.3 / 4.8 |
   | 50 | 1.24 / 0.92 / 1.09 | 5.8 / 4.6 / 3.6 | 5.0 / 3.3 |
   | 75 | 0.26 / 0.14 / 0.14 | 0.6 / 0.3 / 0.2 | 2.5 / 1.7 |

   - v3 held: coverage was 100 % from 5 % to 25 % counted, against the 85 % threshold. So no v4 was written.
   - The alternative scaling (`count_scale_mode = "any"`: terms fully present from the first returns) had mean
     |z| ≤ 0.76 at every checkpoint. It remains an option in `brunder.nowcast`.
   - Two races are a thin test.

## County baselines and estimates for every race (display only)

- **Baseline:** `data_public/us2026_county_baseline.csv` holds 2024 president and 2024 regular Senate results by
  county for 49 states plus Connecticut's planning regions (`build_county_baseline`).
  - Source: MIT Election Data and Science Lab precinct returns 2024 (github.com/MEDSL/2024-elections-official;
    Dataverse doi:10.7910/DVN/XDJYKC and ZCM3BN, CC0 1.0), summed to Census county FIPS.
  - Only candidate rows count: CONTEST TOTAL, under/over votes and similar rows are dropped. Harris and Trump are
    identified by candidate name across ballot lines, which covers NY fusion lines and the empty or NONPARTISAN
    party fields in NE and parts of OR.
  - Rows labelled "COUNTY TOTAL" are dropped. Where a county's TOTAL rows and vote-mode rows describe the same
    precincts, they duplicate each other and only the modes are kept; otherwise they are summed (NJ has both
    cases).
  - Each state uses whichever mode rule (precinct overlap, TOTAL-first, or all rows) brings its county sums closest
    to the official statewide totals (`data_public/us2026_state_pres24.csv`).
  - Connecticut towns are mapped to the 2022 planning regions (Census 2023 gazetteer). SD 46113 is mapped to 46102.
  - **The build fails** if any county of a state with a 2026 race lacks 2024 two-party votes, or if a race state's
    county sums are more than 2 % off its official totals. Exceptions:
    - Alaska: it reports by house district, not borough.
    - Hawaii's Kalawao: its votes are reported with Maui.
    - Louisiana: MEDSL's precinct file has Election Day votes only (49 % short), so parish shares exclude early
      votes. Its estimate carries that caveat.
  - Remaining deviations from the official totals: NJ 1.4 %, ME 1.2 %. IN (5 %) and MO (8 %) have no 2026 race.
- **Ohio prior:** the same data gives `oh_pres_2024`, the prior the registered Ohio configs name, so Ohio v1 and
  v2 now run. Only data was added; no setting changed.
- **Estimate for unmodeled races** (`race_estimate`, written to `units/<race>.json` with an `estimate` block):
  - County share = 2024 president two-party share + (statewide target − the baseline statewide share), a uniform
    swing.
  - The target is the poll average (`basis: polls`). Without polls the 2024 county shares are shown unshifted
    (`basis: "past results (no recent polls)"`). A market-implied fallback would show the price back to itself,
    so none is used.
  - Statewide sd = the poll sd, else 0.06.
  - County sd = √(statewide sd² + 0.015²). The 0.015 is the sd of Georgia's county swings around the statewide
    swing, 2020 → 2024 president, weighted by votes (`county_resid_sd` = 0.0154). It comes from one state, a
    presidential pair, and is small.
  - Expected votes = 0.72 × 2024 total. MEDSL's 2022 files were not used in this pass.
  - Coverage: 69 of 71 races have a county map; the 10 modeled races use their model's file. AK ×2 has none.
    Hawaii's map has 4 of 5 counties (Kalawao reports with Maui).

## Margin scale (the comparison that does not conflate)

Margins are D − R in points of the two-party vote.

- **Fields:** timeline rows of modeled races and overview `races[].margins` carry:
  - `margin_model` / `margin_model_sd`: the projection;
  - `margin_v2`;
  - `margin_polls`;
  - `margin_market` / `margin_market_kalshi` = Φ⁻¹(price) × σ.
- **σ:** the model's margin sd at that minute (pre-count: the model's pre-count sd). Without a model it is the poll
  average's sd, else the estimate's. So `margin_market` is the margin the price asks us to believe at our own
  uncertainty.
- **Scoring after the count** (`score_race`) keeps three scores separate, never combined:
  - margin error, model − certified, in points;
  - turnout error, projected total votes (`votes_proj`) / certified − 1;
  - probability calibration, Brier and log loss of P against the outcome.
  - the full post-election pipeline (every registered metric per race and version, execution, Senate control,
    display-only candidates; `uv run brunder us score`) is `analysis/us_score.py`, documented in
    [`docs/us_scoring.md`](us_scoring.md).

## Early-vote implied margin and its history (display only)

- **Raw margin:** `margin_ev_raw` = 100 × (D − R) / all early-vote ballots, by the voters' party registration. It
  needs a party split, so it exists for NC and FL only (TX publishes none).
  - Registration is not a vote. The raw number assumes every registered partisan votes their party and other voters
    split evenly.
  - Two past elections cannot separate party loyalty from the unaffiliated split. So instead of fitting them, the
    historical bias absorbs both.
- **Bias:** `ev_bias_hist` = mean over past generals of (raw margin − certified two-party margin of the top race),
  taken on the same number of days before Election Day as today's data (`data_public/us2026_ev_history.json`).
  - Source: NC SBE per-ballot absentee files 2022 and 2024, counting accepted ballots by return date.
  - Finals: NCSBE 2022 Senate results; MEDSL 2024 president.
  - FL has no dated history: its daily files are overwritten. TX has no party data.
  - Positive = early vote overstated the Democrat.
- **Flag:** `margin_ev_corrected` = raw − bias. `market_closer_to_raw_ev` is true when the market-implied margin is
  nearer the raw EV margin than the corrected one, a sign the price may lean on early vote.
  - Caveat: NC's bias was 10.4 points in 2022 and 2.5 in 2024 (7 days before). With two elections it is a rough
    correction, not a calibrated one.

## US midterms hub data (`analysis/us_hub.py`; display and analysis only)

Files are in `site/data/us2026/hub/`, each under 1.5 MB. Rebuild them with
`uv run python analysis/us_hub.py [polls pollsters markets backtest census]`.

| file | content | sources (licence) |
|---|---|---|
| `polls.json` | Every poll of each race (Democratic side named) with its exact normalised weight in our average (as of 2026-11-02, after the pollster cap), polls left out with the reason, and the daily average for the last 60 days using only polls published by each day | VoteHub (CC BY 4.0) |
| `pollsters.json` | Per pollster, Senate/governor polls of the final 21 days, 2016–2022: polls, races, lean (mean poll − result, two-party D points), RMSE, and whether the pollster is in our 2026 averages | 538 raw_polls (CC BY 4.0) |
| `markets.json` | Per race: Polymarket daily price (last 90 days), total volume and spread; Kalshi daily midpoint, volume and spread. Plus Polymarket's resolved 2024 Senate/governor markets, final-week price vs outcome: 34 markets, Brier 0.037, calibration bins | Polymarket public data; © Kalshi |
| `backtest.json` | Our pre-count rule on 2018 and 2022 (126 races): poll average plus v3 error bins, with bins fit leaving the tested cycle out, vs a past-results-only baseline (the state's previous presidential share, sd 0.06) | 538 raw_polls and results (CC BY 4.0) |
| `census.json` | Per county: 2020 estimates base, 2025 estimate, change; age 65+ (PEP 2025); Bachelor's+, Hispanic, Black alone and median income (ACS 2020–2024 5-year summary file). Per state: population change | US Census Bureau (public domain) |

- **Backtest (pre-count only; no count-over-time data for those years):** model vs baseline.
  - 68 % coverage 0.75 vs 0.73; 90 % coverage 0.90 vs 0.87.
  - Margin MAE 4.4 vs 10.7 points.
  - Brier 0.052 vs 0.141; log loss 0.17 vs 0.57.
- **Limits:**
  - Polymarket has no public daily volume (total only).
  - The 2022 Polymarket Senate markets have no CLOB price history, so 2022 is not scored.
  - The ACS API now requires a key, so the public summary-file tables are used instead.
  - `pop2020` is the Census 2020-based estimates base, not the census count.
- **Redistricting:** Senate and governor races are statewide, so district maps, including mid-decade House
  redistricting, do not affect them. There has been no reapportionment since 2020.

## Poll crosstabs and recalled 2024 vote (`analysis/us_crosstabs.py`; display only)

Full write-up: [`docs/us_crosstabs.md`](us_crosstabs.md). The output is `site/data/us2026/hub/crosstabs.json`, built from
the per-poll values in `data_public/us2026_crosstabs.csv`. For every poll in our averages it records whether the
pollster's public release prints crosstabs. Where it does, it gives the recalled 2024 vote, party / education / race / age
mix, LV screen and mode, plus the gap between recalled vote and the certified 2024 result. Each topline is shown raw and
reweighted to the certified result. Per race, the raw and adjusted averages are shown side by side, and a 2018-2022 test
checks whether D-leaning pollsters missed more.

- **Sources:** pollster/sponsor release pages and their linked files (robots.txt honoured; 4xx answers were not
  retried); VoteHub (CC BY 4.0); MEDSL 2024 president by state (CC0); 538 raw_polls (CC BY 4.0).
- **Registered models:** unchanged.

## US House districts (`analysis/us_house.py`; display and analysis only, nothing registered)

Files are in `site/data/us2026/house/`, each under 2 MB. Rebuild with
`BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_house.py [geo] [data] [markets]` (default: all, in
that order; `markets` re-reads the `data` output). Downloads are cached under `raw/us/house` and `raw/us/medsl24`.

**Maps in force on 2026-11-03.** Nine states vote on lines that differ from the 119th Congress (2024) lines: AL
(2023 legislative map, reinstated by the Supreme Court), CA (Prop 50), FL (May 2026), LA (May 2026, after
*Louisiana v. Callais*), NC (Oct 2025), OH (commission, Oct 2025), TN (May 2026), TX (2025, upheld for 2026) and
UT (court-adopted map). Missouri's 2025 map is not used: the 2022 map applies on 2026-11-03 (Supreme Court stay of
2026-09-25). Virginia's map was struck down; SC and IN did not pass maps. Status from Ballotpedia and NCSL
(2026-10-28); the code checks it against the Census Bureau's 120th Congressional District block equivalency file
(version of 2026-10-07): exactly these nine states differ from the 119th file, and Missouri's 120th lines equal
its 2022 lines.

| file | content |
|---|---|
| `districts_2026.topo.json`, `districts_2024.topo.json` | object `districts`; properties `id` ("TX-07", at-large "AK-AL"), `st`, `redrawn`. 2024: Census cartographic boundary cd119 1:500k. 2026: the same, with the nine redrawn states from TIGER/Line 2026 `cd120` clipped to the 1:500k state outlines. Both simplified with one distance tolerance (250 m) |
| `districts.json` | per 2026 district: `pres24`/`pres20` {d2, margin, dv, rv, tv, method}, `swing_pres`, `redrawn`; unchanged lines: `house24`, `house22`, `swing_house`; redrawn: `pop2020`, `core_2024` (2024 district that gave it most people), `core_share`, `house24_core`; `ev` (TX/FL/NC); `market_p_d` {pm, kalshi}; `poll_avg`; `baseline` (model only). Plus `national`, `state_check`, `notes` |
| `districts_2024.json` | per 2024 district: `pres24`, `pres20`, `house24` {winner, winner_party, d_cand, r_cand, dv, rv, tv, d2, margin, uncontested, d_vs_r, incumbent, incumbent_party}, `house22` (FEC), `swing_pres`, `swing_house` |
| `flows.json` | per redrawn state: `flows` [2024 district, 2026 district, 2020 population, share of old, share of new, Harris votes, Trump votes]; per new district `lean_change` vs its core 2024 district; `old_seats`/`new_seats` {districts, harris_won, expected_d}; `house24_won_d` |
| `swing.json` | per county (`us-<st>-<fips>`, the ids of `geo/us_counties.topo.json`): [2020 margin, 2024 margin], president |
| `earlyvote.json` | per 2026 district in TX, FL, NC: ev_total, baseline_2022, share_of_2022_total, by_party (FL, NC), approx |
| `markets.json` | per district: Polymarket and Kalshi P(Democratic party wins), spreads, volume, candidate names; VoteHub district polls with our average; the baseline; the generic-ballot average |

Margins are D − R as a percentage of all votes for the office; `d2` is the two-party Democratic share.

- **2024 president, 2024 lines (`method`: county / precinct / population).** Each county's total (MEDSL county
  file) is split over its districts by MEDSL's 2024 precinct returns: every precinct goes to the House district on
  its ballot (split by House votes if a precinct lists several). Counties inside one district (from the Census
  block equivalency file) are exact. Precincts with no House contest are given to a county's districts that have
  no precinct data (uncontested races), by block count. Checks: Trump carried 230 of 435 districts (the published
  count); NE-02 Harris +4.6, AK Trump +13.1, TX-28 Trump +7.3, TX-34 Trump +4.4. District sums equal the county
  file within 0.5 % in every state but NY, whose 2 % statewide-only ballots (county code 36000) carry no county.
- **2020 president (approximation):** each county's 2020 Democratic, Republican and total votes are split like the
  county's 2024 votes of the same kind. The district swing is therefore a weighted mix of county swings; it misses
  any within-county change in pattern.
- **2026 lines in the redrawn states (`method` crosswalk; approximation):** the (county × 2024 district) pieces
  above are split to 2026 districts by 2020 census block population (P.L. 94-171 block counts joined to the 119th
  and 120th block equivalency files). This assumes uniform vote within each piece. Votes are conserved per state
  (tested). No open precinct file was used: VEST and MEDSL's district-level House file sit behind a Dataverse
  guestbook, and the Census API now needs a key. Checks against reported numbers: TX map 30 Trump / 8 Harris seats;
  new NC-01 Trump +11; Utah's court map gives one Harris district. California's Prop 50 map comes out at 45 Harris
  / 7 Trump districts here, about two fewer Harris seats than reported, so in redrawn cities the error is a few
  points.
- **House results:** 2024 = MEDSL precinct returns summed by district (FEC has not published 2024 House results;
  only the ballot list, which gives the incumbent flag). Fusion lines count for the candidate's party. Specials are
  dropped unless a district has no regular race (MEDSL flags TX-18's regular race as special). 2022 = FEC
  *Federal Elections 2022*, official. `swing_house` is only set where the lines did not change (not AL, GA, LA,
  NC, NY) and both races were D vs R: 305 districts on 2024 lines.
- **Redistricting flows, seats at the 2024 vote (old → new map; Harris-won districts):** AL 2→1, CA 41→45,
  FL 8→5, LA 2→1, NC 3→3, OH 4→2, TN 1→0, TX 11→8, UT 0→1. Net −6 Democratic: 199 Harris-won districts of 435 on
  2026 lines (205 on 2024 lines). `expected_d` = Σ Φ((d2 − 0.5) / 0.0294), where 0.0294 is the sd of 2024 House
  vs president two-party shares over 395 D-vs-R districts.
- **Early vote:** county counts from `site/data/us2026/earlyvote.json`, split by 2020 population where a county
  is split (`approx` true). District totals add up to the county file.
- **Markets:** Polymarket events tagged `house-elections` (`<st>-<nn>-house-election-winner`): 434 districts.
  Kalshi `HOUSE<st><n>` series (contract terms HOUSEPARTY), events ending `-26`: 424. p = midpoint of best bid and
  ask where both exist, else the last price, for "Democratic party wins the seat".
- **District polls:** VoteHub `poll_type=us-representative` (CC BY 4.0), by `seat_name`; 177 polls, 141 matched
  to a D and an R candidate. Parties come from the market candidate names, else from FEC 2026 candidates of the
  state with the same surname (unique party only). Averaged with our Senate/governor rule (45-day window, same
  weights and cap); sd = poll-error bin by number of polls. 47 districts have an average in the window.
- **Baseline (MODEL-ONLY, display only, not registered):** share = district 2024 two-party Harris share on 2026
  lines + (generic-ballot average − 0.4925, the national 2024 two-party Harris share). sd = √(bin² + 0.0294²),
  bin from `POLL_SD_BINS` by the number of generic-ballot polls. p_d = Φ((share − 0.5) / sd). Generic ballot as
  of 2026-11-02 by our rule: 54.8 % D two-party, from only 2 polls with a sample size in the 45-day window (VoteHub
  has few recent generic-ballot polls), so sd 0.043 and swing +5.5 points. One national swing: errors are
  correlated across districts, and incumbency is ignored. Sum of p_d: 254.7 D seats.
- **Sources and licences:** Census block equivalency files, P.L. 94-171, TIGER/Line and cartographic boundaries
  (public domain); MEDSL 2024 precinct returns (github.com/MEDSL/2024-elections-official, CC0 1.0) and County
  Presidential Returns 2000–2024 (CC0 1.0); FEC (public domain); VoteHub (CC BY 4.0); Polymarket public data;
  © Kalshi.

## House districts on election night (`brunder.us_house_night`; configs `configs/us2026_house/<st>-<nn>_v1/v2.toml`) {#house}

The feeds recorded for the statewide races already carry every U.S. House contest by county in TX (Civix
`countyInfo`), FL (DOS file, `RaceCode` USR), NC (NCSBE `results_<county>.txt`), SC (Clarity), OH (NIST 1622 XML)
and MN (SoS `allracesbycounty.txt`, "U.S. Representative District <n>"); GA and AZ House ballot items are recorded
under their own keys (`ga_enr_<Georgia|Arizona>_house_<item>`; the statewide `…_item_*` keys are unchanged), and so
are, since 2026-10-30, one document per configured district in PA (DoS API `GetCountyBreak`, office 11,
`districtId` = district + 1, election 117; early = mail-in, no precinct counts), NE (SoS web service `GetMapData`
type CG, race ids 13165/13166) and NM (type FED, race 11187) (`brunder.us_feeds_extra.HOUSE_URLS`). SC is excluded
(its host challenges the recording server; `prereg/NOTES.md`). Not covered: CA (the SoS API reports House results
districtwide only, `/returns/us-rep/district/<n>`, no county split; `…/county` answers 403), AK (AK-AL P(D) 0.02),
VT (at-large, P(D) 0.98). Feed kinds `<st>_house` are parsed by `brunder.us_house_night.parse_house` into the
usual rows with race `house-<NN>` and one row per county × district (a split county appears in each of its
districts with only that district's votes and, in TX/NC/FL/MN, that district's precinct counts; NE/NM give the
county's). The model is `brunder.us.project`, on the district's counties (live: its exact numpy form,
`brunder.us_house_night.project`, equal to 1e-9 in tests).

- **Prior** (`data_public/us2026_house_priors.csv`, `analysis/us_house_night.py priors`): 2024 president in each
  county's part of each 2026 district, the allocation of `site/data/us2026/house/districts.json` (district sums
  equal it to < 1e-5; every district of TX, FL, NC, SC, OH, GA, AZ and, appended 2026-10-30 with the earlier rows
  unchanged, PA, MN, NE, NM: 156). v1: no shift. v2: `swing_prior_mean` = generic-ballot shift +0.0552 (average 0.5477 −
  national 2024 Harris share 0.4925), `swing_prior_sd` = 0.052 = √(0.0429² generic-ballot error + 0.0294² spread of
  2024 House vs president shares). Expected votes = 0.72 × 2024 president two-party votes, learned on the night.
- **Parser checks** (`analysis/us_house_night.py verify`): district totals equal the certified ones for FL 2022
  official extract vs FEC (27/27 exact), FL 2024 extract vs MEDSL (26 exact, 1 off by 1 vote), SC Clarity 2022 vs
  FEC (7/7) and 2024 vs MEDSL (7/7), NC certified 2022 precincts vs FEC (14/14), NC 2022 ENR county files vs the
  certified precincts (all cells exact), GA 2024 certified items vs MEDSL (14, max 7 votes), MN SoS county file 2022
  vs FEC (8/8) and 2024 vs MEDSL (8/8), PA DoS API 2024 (election 105) vs MEDSL (14/17 exact, all five configured
  districts exact; PA-02/03/05, Philadelphia/Delaware County, off by 500/1,549/15 in MEDSL's file), NM SoS archive
  2024 (election 2882) vs MEDSL (≤ 26 votes; the parser reproduces the SoS canvass exactly). TX, OH, AZ, NE have no
  past file of the night format (NE's archive endpoint serves no past general); their 2026 pre-election files list
  every district, D and R, and the prior's counties (TX-14 lists Chambers County, a sliver the block crosswalk gives
  no 2024 votes; FL-10 is absent, uncontested; PA-03 has no Republican; PA-05 lists Chester County, a sliver).
- **Replay** (`analysis/us_house_night.py replay`; `data_public/us2026_house_calibration.json`): 2024 districts with
  a final two-party share in [0.35, 0.65] (68: TX 16, FL 20, NC 9, SC 6, OH 8, GA 6, AZ 3; AZ-02/05/07 dropped
  because MEDSL's Arizona precinct file does not reproduce their certified result), 6 simulated nights each:
  certified county-part votes split by vote type where certified data have it (NC, SC, AZ precincts; GA items),
  else a split borrowed from a donor state's county parts; count order as `analysis/us_calibrate.py`; the prior is
  moved by an unknown national swing N(0, 0.04); expected votes off by exp(N(0, 0.08)); v2's swing prior is that
  swing plus N(0, 0.0429). Selection per feed group and version (≥ 85 % coverage of the 90 % band at each of 5/25/50 %
  counted, then smallest mean |error|, then smallest tau / sigma_uf0). Chosen: tau 0.01, sigma_uf0 0.03, sigma_mun0
  0.08; rho 0.3 (FL/SC/OH v1) or 0.6; vote_type on only for TX/NC/GA/AZ v1. Coverage at 5/25/50 % counted (mean
  |error|): FL/SC/OH v1 0.98/0.95/0.87 (0.016/0.015/0.013), v2 0.98/0.96/0.90 (0.024/0.018/0.014); TX/NC/GA/AZ v1
  0.98/0.98/0.95 (0.018/0.012/0.010), v2 0.96/0.96/0.93 (0.020/0.014/0.011). Weak spot: Maricopa-only AZ districts
  (AZ-01/04/08), 0.78 (v1) and 0.67 (v2), where the Election Day batch is far more Republican than the early batch
  and arrives last; no setting in the grid reaches 0.85 there.
- **Configs** (`analysis/us_house_night.py configs`; never rewrites a registered config): districts of these states
  with a Polymarket or Kalshi P(D) in [0.05, 0.95] in `markets.json` built 2026-10-29: 63 registered (TX 12, FL 20,
  NC 11, SC 4, OH 7, GA 4, AZ 5) and, added 2026-10-30 (`configs_new`), 12 more: PA-01/07/08/10/16, MN-01/02/06/08,
  NE-01/02, NM-02. These four feeds are not in the 2024 replay; they take the chosen settings of their group (PA: the
  vote-type group, mail-in = early, `early_share` 0.23 = PA 2022 midterm mail share as the registered pa-gov configs;
  MN/NE/NM: the no-split group). Race id `house:<ST>-<NN>`, Polymarket market "Will the Democratic Party win the
  <ST>-<NN> House seat?". Kalshi lines need the district's pair in `configs/pairs/us2026.toml` (YES = `<event>-D`);
  Polymarket lines need the event in the recorder's `--pm-event` list (all 75 are listed). The configs live in
  `configs/us2026_house/` (ids `us2026_house_<st>-<nn>_v<k>`), so globs over `configs/us2026_*_v1.toml` (pre-count,
  overview, scoring, poll generators) never see them.
- **Live, own process** (`deploy/brunder-us-house-live.service`: `python -m brunder.us_house_night live`, 60 s,
  `CPUQuota=25%`, `MemoryMax=1G`, `Nice=15`): never inside `brunder us live`; reads the same recorder archive and
  writes only `site/data/us2026/house_live/{meta,timeline,timeline_tail,status}.json` + `units/house:<ST>-<NN>.json`
  (same row and units shapes as the statewide files; rows older than 6 h / 48 h thinned to one per 10 min / hour).
  The unit runs all 71 districts with a recorded feed (59 registered + the 12 added; SC's 4 excluded). Where the
  time went (cProfile, 63 districts, mid-count): 88 % in `brunder.us.project` (two pandas projections per district
  per minute, ~64 ms each), feed parsing 5 %, file writes 8 %. `HouseExporter` now (a) projects with the numpy form
  (~3.6 ms), (b) splits a parsed feed by district once and re-projects a district only when its own rows changed
  (content digest), (c) looks a feed's snapshot files up once per minute for all its districts, (d) lists the
  archive incrementally (`IncrementalArchive`: each file name parsed once; the plain listing re-parses every name
  every cycle and grows with the two-week archive), (e) rewrites a units file only when the district gained a frame.
  Feeds are still parsed only when a snapshot file changes. Cost on the Mac (rehearsal archive below, 240 simulated
  minutes, every feed changing every minute; CPU s per cycle, median / p95 / max after the first): 63 districts
  before 3.76 / 5.83 / 7.17, after 0.42 / 0.78 / 1.01; the 59 live ones 0.42 / 0.71 / 0.88; identical
  `timeline.json` and units files before and after; peak RSS 0.3 GB.
- **Rehearsal:** `analysis/us_house_rehearsal.py` adds the House contests to the TX/NC/FL bodies of
  `analysis/us_night_replay.py` and writes the GA/AZ House items and OH/SC House-only files (statewide parsing of
  those bodies is unchanged: House rows carry no statewide race key).

## Wallet flow on Polymarket (`analysis/us_wallets.py`; display and analysis only, nothing registered)

`site/data/us2026/hub/wallets.json`: per Senate/governor market holder concentration, active wallets, wallet age,
taker flow by order size, "seasoned" (profitable on past resolved political markets) flow, price impact per $ and
lead/lag, plus the 2024 historical check (seasoned flow in the final two weeks did not predict outcomes beyond the
price: β = −2.3 ± 2.3, n = 85). Full method, coverage and results: `docs/us_wallets.md`.

- **Sources and licences:** Polymarket public APIs (Gamma `/events`; data-api `/trades`, `/holders`, `/oi`,
  `/activity`), public market data. Aggregates only; no wallet addresses or identities published, traders' identity
  and location not pursued. Kalshi exposes no trader-level data (aggregate volume and order book only).

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

Outstanding-vote display fields (pre-count, live and replay). They are not used by the model:
- `rem` / `rem_margin` per county in every map frame: the model's remaining two-party votes (expected vote minus
  counted, scaled by the night's turnout factor k), and their projected Democrat-minus-Republican margin in votes.
- `rem_total` / `rem_margin_total` per race in timeline rows.

All are integers rounded to tens. Before the count, rem is the whole expected vote. By construction
(counted margin + `rem_margin_total`) / (counted + `rem_total`) = 2·share − 1. They are model estimates, not
official counts of outstanding ballots.

Poll closes, Nov 4 UTC: Georgia 00:00 (7 p.m. ET), Ohio 00:30 (7:30 p.m. ET), Texas 01:00 (7 p.m. CT; 02:00 in
El Paso and Hudspeth, mountain time) and California 04:00 (8 p.m. PT). Standard time applies, because daylight saving
time ended on Nov 1. Sources: georgia.gov (vote in person on Election Day), Ohio SoS 2026 elections calendar, Texas
SoS news release of 2026-10-27, and the California SoS Elections Officers' Digest.

Priors come from the committed `data_public/us2026_priors.csv` (the registered input); a local
`data/processed/us2026/priors.csv` only adds sources the public file lacks, and a source whose local rows differ is
taken from the public file with a warning (a stale local build once left 8 of 13 races without a prior on the server).
That file is the same derived county table (official public-record and CC BY sources, attribution header), written
by `brunder us priors`. Races without a prior (Ohio, until the MEDSL file is added) are listed with prices and
`model_note` "county prior not available yet". The command refuses to overwrite files whose phase is live or
replay (`--force` overrides).

On the night `brunder us live` writes the same files with `phase = "live"`. It keeps
`timeline_precount.json` and puts those rows in front of its minute rows, so the switch needs no manual step.
Until a race has returns, its map shows the prior frame.

## National overview (`overview.json`: every Senate and governor race)

`data_public/us2026_races.json` is the committed race registry, built once by
`BRUNDER_DATA=… uv run python analysis/us_overview_races.py`. It lists:
- the 33 class II Senate seats and the 2 specials (Ohio, Florida). Seats, parties and classes come from the senate.gov
  contact XML; the specials come from senate.gov's appointed-senators list (Husted, Moody).
- the 36 governor races, with incumbent and party from nga.org (crawl delay respected).
- each race's Polymarket market and Kalshi ticker for P(dem_side). dem_side is the Democrat, except in Nebraska and
  Montana: there no Democrat is priced at 1 % or more and an independent is (Osborn, Bodnar), so the independent is
  the dem_side, flagged in `dem_side.aligned`.
- candidates (from the Kalshi listings) and `open` (true when the incumbent is not among them; Alaska from the
  certified primary; null for Kentucky).
- Senate holdovers (seats not up in 2026: D 32, R 31, I 2) and the Senate and House control markets.

`brunder us preelection` writes `us2026/overview.json` from it on every run: prices, and 14 days of history every
30 minutes (Polymarket CLOB price history; Kalshi hourly candles). Races with the county model carry `modeled: true`
and their `race_id`, and `meta.overview` points the page to the file. On the night `brunder us live`
refreshes the prices and adds a history point every 30 minutes from the recorder's `polymarket_books` /
`kalshi_books`. That only covers events and tickers the recorders follow, so the recorder's `--pm-event` list and the
Kalshi pairs file must include the registry's markets. A failing overview refresh never blocks the race files.

## Live and replay

```
uv run brunder us priors                                         # optional: sources not yet in data_public
uv run brunder us live configs/us2026_*_v1.toml --out <served data root> --until 2026-11-05T12:00Z
uv run brunder us export configs/us2026_*_v1.toml --out site/data   # replay from the archive afterwards
uv run python analysis/us_dryrun.py --out /tmp/us2026_dryrun     # synthetic night, end-to-end check (never publish)
uv run python analysis/us_night_replay.py run --work /tmp/brr --minutes 15   # past official counts (GA/NC/FL 2022, TX) as recorder snapshots, 12x
uv run --with playwright python analysis/us_night_replay.py page --live http://127.0.0.1:8791/   # headless page checks
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
- House districts (#house): 1–30 counties per district, so the swing is learned from few units and the
  within-county vote-type order dominates; Maricopa-only AZ districts are under-covered in the 2024 replay. The prior
  is the 2024 president vote: candidates and incumbency enter only through the count (v2: through the swing sd).

## Model v5 (secondary): night Hispanic slope; v6 mixture option

Optional county-swing slope on the Hispanic share learned from the night's counted counties (`[model] hisp_slope`, `hisp_blend`; off by default,
registered configs unchanged bit for bit). Method, backtest and decision: `docs/us_hisp_slope.md`.

## Evidence label

Forward: configs frozen and registered before 2026-11-03 23:00 UTC, outcomes unknown. The Georgia calibration is
in-sample for its settings.
