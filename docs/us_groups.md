# US places page (`site/groups.html`)

The 2026 US count by kind of place: groups of counties side by side along five dimensions, plus key regions per state. It reads
the same files as the US dashboard (`us2026/meta.json`, `us2026/units/<race>.json`, and `civic/us2026/…` where served), from
`data/` or, with `?live=<base>`, from the live server every 15 s. No server changes. Every row is a group of places, not of voters.

| part | where |
|---|---|
| data builder | `analysis/us_groups.py` (reads `$BRUNDER_DATA/processed/us2026/unified/county_demo.parquet`, `results_county.parquet`) |
| data (committed, aggregates only) | `site/data/us2026_groups/groups.json` (dimensions, rules, tercile cuts, key regions), `counties.json` (makeup, group of each dimension, 2020/2024 president and 2022 governor/Senate margins per county) |
| page | `site/groups.html`, `groups.js`, `groups.css` (own `__BUILD`); figures in `site/groups-agg.js` |
| tests | `tests/test_us_groups.py` (builder + `groups-agg.js` in node on synthetic counties), `analysis/site_check.py --only groups` |
| night check | `analysis/groups_check.py` against `analysis/us_night_replay.py run … --live-port 8797 --civic` |

## Groups
- Urban to rural: big cities (500,000+ residents and 1,000+ per sq mi), suburbs (100,000+ and 250+ per sq mi), small cities and
  towns, rural (under 50,000 and under 100 per sq mi).
- Education, age (share aged 65+), income (median household income): thirds of all US counties.
- Race and ethnicity: majority white (non-Hispanic), majority Black, majority Hispanic or Latino named by origin only where one
  origin is half or more of the county's Hispanic citizens of voting age (Mexican, Cuban, Puerto Rican, another origin), else
  "mixed origin" (ACS composition columns `dominant_origin`, `dominant_share_of_hisp_cvap` of
  `processed/us2026/origin/county_origin_groups.csv`; no vote estimates from that work are used), no majority group.
- Key regions: `REGIONS` in the builder (county FIPS lists; "rural" = the state's rural counties).
ACS 5-year 2020-2024. The US units are counties only (no smaller units in the US files).

## Figures (per group, per race; nationally one race per state: the Senate race, else the governor's, Democrat vs Republican)
- Counted: share of the group's expected vote (V0) counted.
- Shift (headline): change in two-party margin from the same counties' 2024 president or 2022 same-office result, from
  **fully counted counties only** (95%+ counted), weighted by votes counted, with how many counties and what share of the
  group's expected vote that covers. Shown only once the complete counties are 3+ counties and 15%+ of the group's expected
  vote ("n of N counties complete" until then): early and mail ballots are often counted first and lean differently.
- Projected margin: the model's projected final share per county (`proj_poll`, else `proj`; a fully counted county's own count),
  weighted by expected votes, with the same counties' baseline.
- Turnout vs expected: votes counted / expected votes in fully counted counties; projected = `turnout_ratio_live`.
- Partial: the raw counted margin of all counted votes, grey, labelled, and hidden while the group is under 50% counted.
- Over the night: the complete-county shift and the projected margin per group at each frame of the units files.
Before the count the page shows each group's 2020 / 2022 / 2024 margins and makeup.
