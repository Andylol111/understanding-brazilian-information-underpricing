# US House coverage 2026: NY, NJ, VA, PA (not live), CT, MD, MA, DE, RI, VT

Probed 2026-11-02 from the Mac only (one polite GET per URL, User-Agent `brunder/0.1 (research; recorder)`, robots.txt
read first, no retries or bypass); nothing was requested from the election server. All models below are secondary,
UNREGISTERED ("added"), outside registered scoring. Competitive = model baseline P(D) or Polymarket/Kalshi P(D) in
[0.15, 0.85] (site/data/us2026/house/districts.json, markets.json; asof 2026-11-02).

## Competitive districts of the slice and verdicts

| District | Model P(D) | PM / Kalshi | Verdict | Source |
|---|---|---|---|---|
| VA-01 | 0.72 | 0.655 / 0.635 | **viable** (built) | Virginia Dept. of Elections Enhanced Voting API, county x candidate x vote method, precincts reporting |
| VA-05 | 0.45 | 0.425 / 0.425 | **viable** (built) | same API |
| MD-01 | 0.27 | 0.155 / 0.145 | **viable** (built) | MD State Board of Elections static HTML, county table (county, only its part of the district) |
| PA-11 | 0.16 | 0.046 / 0.047 | **viable, source not fetched** (built) | PA DoS GetCountyBreak, same API as the live PA House documents; host on the do-not-request list, so district 11's document was never requested |
| NY-01 | 0.53 | 0.21 / 0.205 | blocked / no data | see NY |
| NY-02 | 0.385 | 0.205 / 0.215 | blocked / no data | see NY |
| NY-03 | 0.74 | 0.885 / 0.90 | blocked / no data | see NY |
| NY-17 | 0.87 | 0.605 / 0.625 | blocked / no data | see NY |
| NY-21 | 0.175 | 0.34 / 0.335 | blocked / no data | see NY |
| NY-23 | 0.165 | 0.023 / 0.0245 | blocked / no data | see NY |
| NJ-02 | 0.48 | 0.295 / 0.285 | no data | see NJ |
| NJ-07 | 0.83 | 0.935 / 0.925 | no data | see NJ |
| NJ-09 | 0.83 | 0.97 / 0.9655 | no data | see NJ |
| VA-02, VA-07 | 0.85, 0.91 | 0.91, 0.96 | outside the range (model 0.8513 / 0.9083); recorded on the night by brunder-extra-rec; parser and priors ready if wanted | |
| CT, MA, DE, RI, VT | | | none competitive (every model and market P(D) outside [0.15, 0.85]) | |
| PA-01/07/08/10 | | | already live (71-district unit) | |

Not in this slice but competitive and unassigned: ME-02 (model 0.55, PM 0.435), not assessed (ranked-choice contest);
NH-01/02 are 0.9+. Say so if you want ME-02 probed.

### New York: blocked / no data
* `nyenr.elections.ny.gov` (state unofficial election night results) and `results.elections.ny.gov` (certified archive):
  Cloudflare managed challenge on every request, including robots.txt (HTTP 403, `cf-mitigated: challenge`, "Just a
  moment..."). Recorded as blocked; not bypassed.
* Counties (state ENR is the only statewide feed; each county posts its own): Suffolk (NY-01, NY-02) results page
  answers a redirect loop behind Cloudflare without a cookie; Westchester (NY-17) robots.txt is 403 (Apache);
  Nassau (NY-03) election-results page loads but holds no live feed yet (s-maxage 600); Rockland and Orange (NY-17) post
  on `app.enhancedvoting.com` (do-not-request host). NY-21 and NY-23 counties (North Country; Western NY) were not probed:
  more than ten county boards with different systems, no past-cycle machine-readable file located.
* civicAPI: only recorded data on disk can be used (Coverage's shared archive).

### New Jersey: no data
No statewide election-night candidate feed. nj.gov publishes certified results as per-county PDFs and, on the night,
per-county "periodic report" PDFs (turnout/ballots, not candidate votes). Counties post their own (21 county sites:
Clarity for Ocean; county-run pages for Atlantic, Cape May, Gloucester, Hunterdon, Monmouth, Morris, Union, Warren,
Somerset, Sussex...). NJ-02 spans 8 counties, NJ-07 6; no single format, no tested past-cycle file. Not built.

## Host table (Mac probes 2026-11-02)

| Host | Format / granularity | robots.txt | 429 / Retry-After | Challenge markers | ETag / Last-Modified / Cache-Control | Poll interval | Status |
|---|---|---|---|---|---|---|---|
| enr.elections.virginia.gov | Enhanced Voting JSON; election document (1.5 MB) + one ballot item per contest (1.2-1.7 MB: county x candidate x vote method, precincts reporting) | 404 (no restrictions) | none seen (10 requests) | none; `x-azure-ref` (Azure Front Door) and an Akamai `server-timing` marker in the chain | no ETag/Last-Modified; `Cache-Control: public, max-age=30` (document), `max-age=60` (items) | 60 s per item (the existing extrarec polls the same host every 120 s) | Mac-verified, server unverified (CDN chain; the same host is already requested from the server by brunder-extra-rec, its manifest is not readable by me) |
| elections.maryland.gov | static HTML, rewritten during the count; detail page = county x candidate totals; summary page = district total by vote type + "(r of N election day precincts reported)" | 200; disallows /cdn-cgi/, /system/, /search/, /melody/, /elections/results_data/ ... not the results pages | none seen (8 requests) | `server: cloudflare`, no challenge served to the Mac | none on the pages; robots has `max-age=14400` | 60 s (detail), 120 s (summary) | Mac-verified, server unverified (Cloudflare). 2026 pages exist now with "NR" cells ("0 of 309 election day precincts reported", last refreshed 10/31) |
| www.electionreturns.pa.gov | JSON-in-a-string, county break, U.S. House office 11 | not read (do-not-request) | n/a | n/a | n/a | 60 s as the registered PA recorder | not probed by me |
| nyenr.elections.ny.gov, results.elections.ny.gov | | 403 | | Cloudflare managed challenge | | | blocked |
| www.suffolkcountyny.gov | HTML | 200 (Cloudflare content-signal text; `max-age=1209600`) | | redirect loop on /Departments/BOE/Election-Results without cookies | | | unreachable without cookies; not pursued |
| www.westchestergov.com | HTML | 403 | | Apache | `Cache-Control: no-cache` | | blocked by robots handling |
| www.nassaucountyny.gov | HTML | 200 | | none | `s-maxage=600` | | no live feed visible yet |
| nj.gov/state/elections | PDFs | not read | | | | | no candidate feed |

## What was built (all new files)

* `src/brunder/extra_va.py` (VA-01, VA-05): RECORD = the two ballot items (ids fixed in the 2026 election document), FEEDS
  `va_house` roles `d01`, `d05`, STATES/POLL_CLOSE for `va`; `parse` handles any House item (race from "(Nth District)"),
  so VA-02/07 files from brunder-extra-rec (`/srv/brunder/extrarec/va/*.json.gz`; URL per file in manifest.jsonl) parse as
  well. Counties = localities (`us-va-51<mapFeatureId>`, cities included), precincts reporting/total per locality, early =
  Early Voting + Mailed Absentee.
* `src/brunder/extra_md.py` (MD-01): RECORD detail + summary pages, FEEDS `md_house` role `d01` (detail page); both page
  layouts handled; NR cells skipped; no vote type, no county precinct counts (NaN).
* `src/brunder/extra_pa.py` (PA-11): RECORD one URL (districtId 12, the registered recorder's pattern), FEEDS `pa_house_x`
  role `d11`; parse = `brunder.us_house_night.parse_pa`. STATES/POLL_CLOSE empty: Pennsylvania is already a brunder.us state.
* `data_public/us2026_extra_priors_{va,md,pa}.csv`, `configs/us2026_extra/{va-01,va-05,md-01,pa-11}_v1.toml`,
  `analysis/us_extra_ne.py` (priors + configs), `tests/test_extra_ne.py`, fixtures `tests/fixtures/us/extra_{va,md}_*`.

Test evidence (`uv run pytest tests/test_extra_ne.py`, 19 passed): VA 2024 general 1st/5th District items (real API files,
fetched 2026-11-02 from `2024NovemberGeneral`): statewide-item totals 208,445 / 269,657 and 184,229 / 249,564 reproduced
from the county rows, 18 and 24 localities, precincts reporting = total; MD 2024 District 1 county table: 154,985 / 246,356
reproduced (11 counties); PA parser on the registered 2024 PA-10 document; pre-count 2026 files (VA item with zero votes,
MD page with all NR) give an empty frame; priors' unit sets equal the feed unit sets (VA 18/24 on the live 2026 item and the
2024 item, MD 11) and district sums equal districts.json pres24 (<2 votes). Prior totals (`total`) come from
data_public/us2026_county_baseline.csv (MEDSL precinct-based county totals); dem/rep are identical to the registered
method's output.

## Known limits
* VA: the existing brunder-extra-rec regex records only Senate, VA-02, VA-07 items; VA-01/05 are recorded only if
  Coverage's recorder fetches them (RECORD above).
* MD vote-type split exists only at district level on the summary page; the model runs without it (vote_type = false).
* PA-11: the districtId = 12 URL is inferred from the registered pattern and could not be fetched; if the first
  recorded document is empty/404 the model has no data.
* Config market slugs follow the registered pattern `will-the-democratic-party-win-the-<st>-<nn>-house-seat`; not looked up
  against Polymarket (no network use for it).
