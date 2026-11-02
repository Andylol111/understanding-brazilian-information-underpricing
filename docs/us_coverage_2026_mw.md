# US House coverage 2026: MI, WI, IA, CO, CA, IL, KS, MO, IN, KY, UT, MT

Secondary sources added 2026-11-02, unregistered, kept out of registered scoring. Integration convention: the
`extra_<src>` modules of `docs/us_coverage_2026.md` (branch `cover-mw`). Code: `src/brunder/extra_{clarity,mt,mo,in,wi,mi}.py`;
priors, configs and checks: `analysis/us_extra_mw.py`; tests: `tests/test_extra_mw.py`.

## Districts

Competitive = model (`districts.json` baseline) or market chance in 0.15–0.85. "Cover" is the share of the
district's 2024 presidential vote in counties with a night source. Uncovered counties stay uncounted, and the model
projects them from the prior and the swing seen in the covered ones.

| district | P(D) pm / kalshi / model | verdict | source | cover |
|---|---|---|---|---|
| MI-04 | 0.575 / 0.585 / 0.70 | viable, partial | Ottawa County Clarity summary.json (server-blocked) | 26 % |
| WI-01 | 0.46 / 0.475 / 0.73 | viable, partial | Kenosha County page + Rock County page | 38 % |
| MT-01 | 0.47 / 0.445 / 0.47 | viable | MT SoS web service esersws.mt.gov GetMapData | 100 % |
| KY-06 | 0.56 / 0.545 / 0.34 | blocked | vrsws.sos.ky.gov Akamai 403; Clarity /KY 403; no Fayette County feed found | – |
| CO-05 | 0.425 / 0.425 / 0.56 | viable | CO SoS Clarity statewide (server-blocked) | 100 % |
| CO-04 | 0.38 / 0.365 / 0.23 | viable | CO SoS Clarity statewide (server-blocked) | 100 % |
| MO-02 | 0.365 / 0.355 / 0.63 | viable, partial | St. Louis County PDF + St. Charles County Live Voter Turnout page | 84 % |
| CO-03 | 0.35 / 0.365 / 0.54 | viable | CO SoS Clarity statewide (server-blocked) | 100 % |
| MI-10 | 0.69 / 0.70 / 0.66 | viable, partial | Macomb County unofficial summary PDF (no precinct counts) | 88 % |
| IA-02 | 0.705 / 0.715 / 0.54 | viable | IA SoS Clarity statewide (server-blocked) | 100 % |
| MI-07 | 0.74 / 0.745 / 0.83 | no data | Ingham, Livingston: Enhanced Voting (never requested); Eaton/Clinton/Shiawassee: none found | – |
| KS-02 | 0.205 / 0.205 / 0.19 | no data (not built) | KS SoS robots.txt disallows /ent/, /api/; Shawnee County posts a PDF (cdn.snco.gov, 27 %) | – |
| IN-05 | 0.175 / 0.175 / 0.28 | viable | Indiana ENR OffCatC_1005 JSON (county parts) | 100 % |
| CO-08 | 0.845 / 0.845 / 0.81 | viable | CO SoS Clarity statewide (server-blocked) | 100 % |
| IA-03 | 0.85 / 0.835 / 0.74 | viable | IA SoS Clarity statewide (server-blocked) | 100 % |
| WI-03 | 0.86 / 0.875 / 0.63 | no data | 19 counties; none on Clarity; statewide site Cloudflare 403 | – |
| MI-01 | 0.1465 / 0.135 / 0.16 | no data | Grand Traverse Clarity (13 %, 2026 general not listed yet); 35 other counties | – |
| IA-01 | 0.935 / 0.922 / 0.59 | viable | IA SoS Clarity statewide (server-blocked) | 100 % |
| MI-08 | 0.95 / 0.943 / 0.81 | no data | Genesee Clarity site lists no elections; Saginaw/Bay/Midland none found | – |
| WI-06, WI-08 | 0.08–0.13 / – / 0.29–0.30 | no data (model-only competitive) | none on Clarity | – |
| CA-05, CA-13, CA-22, CA-23, CA-40 | ≤0.07 or ≥0.90 / – / 0.25–0.74 | not pursued (model-only competitive) | api.sos.ca.gov forbidden; Madera/Merced Clarity sites list no elections | – |

IL, UT: no district in the competitive range. civicAPI: no recorded civic_* archive on this Mac. The data volume
`$BRUNDER_DATA` went offline during the work, and the server archive is not readable by the ssh user. The national
House race 87524 has district totals only, so it cannot give a county model.

## Hosts (probed from this Mac only, 2026-11-02; one request at a time, ≥ 1 s apart, UA "brunder/0.1 (research; recorder)")

No host returned 429 or Retry-After.

| host | format / granularity | robots.txt | challenge markers | caching | poll | server |
|---|---|---|---|---|---|---|
| electionresults.iowa.gov (Clarity) | JSON, county × contest; status.json precincts | 404 | none on Mac; VM got 202 challenge (docs/us_midterms.md, 2026-10-30) | CloudFront; current_ver.txt max-age=0, versioned files max-age=86400 + ETag/Last-Modified, elections.json max-age=60 | 60 s | **blocked** (server-blocked) |
| results.enr.clarityelections.com (CO state, MI/Ottawa) | same; county site: summary.json county totals with TP/PR | 404 | same; /KY/ 403 | same | 60 s | **blocked** (server-blocked) |
| esersws.mt.gov (MT data service) | JSON, county × candidate, county precincts | 404 | none (no CDN headers); front end electionresults.mt.gov is Cloudflare "Just a moment" 403 | Cache-Control private, no validators | 60 s | Mac-verified, server unverified |
| extcontent.stlouisco.com | PDF, county total, polling places reporting | 404 | Cloudflare (cf-cache-status DYNAMIC), no challenge | ETag + Last-Modified | 120 s | Mac-verified, server unverified (Cloudflare) |
| www.livevoterturnout.com (St. Charles) | 9.4 MB static HTML, contest precincts | 404 | none (IIS) | no-cache, ETag + Last-Modified | 120 s | Mac-verified, server unverified |
| enr.indianavoters.in.gov | JSON (Azure blob), county × district, no precinct counts | 400 (blob error, none) | Cloudflare, no challenge | ETag + Last-Modified; site refreshes every 2 min | 120 s | Mac-verified, server unverified (Cloudflare) |
| apps.kenoshacounty.org | HTML, contest polls reporting | 404 | Imperva/Incapsula (X-Iinfo), 200 on Mac | private | 120 s | Mac-verified, server unverified (Incapsula: datacenter-sensitive) |
| elections.co.rock.wi.us | HTML (http), contest precincts | 404 | Cloudflare, no challenge | Last-Modified | 120 s | Mac-verified, server unverified (Cloudflare) |
| macombgovdocs.org | PDF, county total, no precinct counts | 404 | none (Apache) | ETag + Last-Modified | 120 s | Mac-verified, server unverified |
| vrsws.sos.ky.gov | – | – | Akamai 403 (Reference ID page) | – | – | blocked |
| county.milwaukee.gov | – | – | Cloudflare challenge (cf-mitigated: challenge) | – | – | blocked |
| racinecounty.gov | – | – | 403 | – | – | blocked |
| enr.sos.mo.gov | HTML (ENR live only on the night) | – | Cloudflare; 307 to sos.mo.gov before the night | – | – | not built |

## Checks

`uv run pytest tests/test_extra_mw.py` passes all 11 tests. The 2024 fixtures are the official files of the 2024
general, and the parsed House totals match the certified totals in `site/data/us2026/house/districts.json`
exactly for IA-01..04, CO-01..08, MT-01 and IN-05. The archived Indiana file disagrees with the certified totals for
IN-02/04/09, so only IN-05 is configured. Some checks are against county reports instead of certified totals:

- MO-02: St. Louis County's certified report and St. Charles County's final page.
- WI-01: Kenosha's and Rock's 2024 pages.
- MI-10: Macomb's 2024 unofficial night-final PDF.
- MI-04: Ottawa's 2024 summary.

The 2026 pre-count files parse to zero votes with the 2026 candidates.

Each prior equals the district's `districts.json` 2024 president totals (max deviation 2.3e-06), allocated by
`analysis/us_house_night.county_pieces`. The configs use the replayed "no vote-type split" settings of
`data_public/us2026_house_calibration.json`; none of these feeds was replayed.
