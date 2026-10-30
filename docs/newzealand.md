# New Zealand 2026 general election (7 November 2026)

This is a **forward** election-night test. The registered v1 model is frozen in `configs/nz2026_v1.toml`;
the complete v2 in `configs/nz2026_v2.toml` inherits the identical model, prior, feed and seed and adds only the
second-/third-place seat-rank markets. Both must be committed and hash-registered by **2026-11-05 12:00 UTC**,
before polls close. A draft v3 (`configs/nz2026_v3.toml`: honest seat tails, special-vote shift, poll prior) and its backtest are in
`docs/newzealand_v3.md`. Election day is Saturday 7 November. Voting closes at
**19:00 NZDT (UTC+13), 06:00 UTC**. The Electoral Commission's 2026 timetable gives the same local closing time
and says preliminary results are released progressively from 19:00.

## Feasibility and the legitimate results route

### The public results website is not the machine feed

`https://electionresults.govt.nz/` is the human results site. On 28 October 2026 it returned a Cloudflare 403 to
ordinary non-browser requests from both the development network and the project VM, including `robots.txt`. We do
not evade that control and the recorder does not request that host.

The Commission publishes a separate machine-readable source for election-night graphics:

- **2026 Media Kit:**
  `https://elections.nz/assets/2026-Media-Kit-/2026-General-Election-media-kit.pdf`, page 11, “Reporting the
  election results”. It says the Commission's XML feed is available to the media on election night, identifies
  `http://media.election.net.nz/xml/` (redirects to HTTPS), and says these are live feeds as results are entered.
- **HTTPS feed:** `https://media.election.net.nz/xml/`.
- **Contact / access request:** email `media@elections.govt.nz` and ask to receive testing updates for graphics
  production and to confirm research archiving/republication. The Media Kit gives media phones `04 806 3536` and
  `027 551 7845`. There is no separate application form named in the kit; the general contact form is
  `https://elections.nz/contact/`.
- The XML index and `election.xml` returned HTTP 200 with the project's honest research User-Agent from both the
  development machine and VM on 28 October. No authentication, browser automation, challenge bypass, or hidden
  endpoint is used. The courtesy email above should still be sent because the kit describes the feed as a media
  feed.

The Media Kit's publication targets are the first result before 19:30, 50% of voting-place results by 22:00, and
95% by 23:30. “Percent counted” means **the percentage of voting-place results**, not votes. The recorder polls
once every 120 seconds, sequentially with 0.5 seconds between requests (about 0.6 requests/second), and writes only
changed bytes.

### Feed format (2026 test files observed 22 October)

The index contained:

- `election.xml`: national places counted, votes and the Commission's current party/list/electorate seat display;
- `electorates.xml`: the 71 electorate numbers and names (64 general, 7 Māori);
- `parties.xml`: identifiers, abbreviations, names, and registered status;
- `candidates.xml`: candidate identifier, electorate, party and list rank;
- `statistics.xml`: reference totals;
- `e01/e01.xml` … `e71/e71.xml`: one aggregate per electorate, with total/counting voting places, party votes,
  candidate votes and update time;
- `eNN/votingplaces/*.xml`: individual voting-place records.

`brunder nz record` deliberately records the five reference/national files and only the 71 **electorate
aggregates**, not the thousands of electorate voting-place XML files. References are refreshed hourly;
aggregates are refreshed every two minutes. Parser fixtures in `tests/fixtures/nz2026_*.xml` are whitespace-
condensed copies of the Commission's 2026 test files.

### Results copyright and publication

The results site's “About this site” terms (`https://archive.electionresults.govt.nz/about.html`) state that Crown
or licensors own copyright and permit reproduction without further permission if the material is reproduced
accurately, is not used derogatorily or in a misleading context, and its source and copyright status are
acknowledged. Published derived data therefore credits **New Zealand Electoral Commission, Crown copyright** and
keeps preliminary results clearly separate from the official result.

Election-night results are preliminary ordinary votes. They exclude special and overseas votes. The Media Kit
sets **27 November 2026** as the target declaration date. Once published, ingest the official 2026 CSVs and export
a final frame; do not label the preliminary count certified.

## Official browser downloads and ingest

The official 2023 downloads are linked from:

- statistics index: `https://electionresults.govt.nz/electionresults_2023/statistics/index.html`;
- all electorate voting-place downloads: `https://electionresults.govt.nz/electionresults_2023/statistics/votes-by-voting-place-electorate-index.html`;
- overall result: `https://electionresults.govt.nz/electionresults_2023/statistics/csv/overall-results-summary.csv`;
- party votes by electorate: `https://electionresults.govt.nz/electionresults_2023/statistics/csv/votes-for-registered-parties-by-electorate.csv`;
- electorate winners: `https://electionresults.govt.nz/electionresults_2023/statistics/csv/winning-electorate-candidates.csv`;
- each party-vote voting-place file: `https://electionresults.govt.nz/electionresults_2023/statistics/csv/party-votes-by-voting-place-1.csv`
  through `party-votes-by-voting-place-72.csv`;
- each candidate-vote voting-place file uses the parallel name
  `candidate-votes-by-voting-place-1.csv` through `...-72.csv`.

If automated access is blocked, download in a normal human browser; do not automate the browser or solve the
challenge. Put the first three named files and any voting-place files you need in (for example)
`~/Downloads/nz2023/`, then:

```bash
BRUNDER_DATA=~/brunder-data-local uv run brunder nz ingest --year 2023 --source ~/Downloads/nz2023
BRUNDER_DATA=~/brunder-data-local uv run python analysis/nz_fetch.py public-prior
```

This writes validated raw files under `$BRUNDER_DATA/raw/newzealand/2023/` (including optional party/candidate
voting-place files) and the small attributed derived prior `data_public/nz2023_electorates.csv`. The committed
electorate prior is built from official files obtained through the Internet Archive's unmodified captures because
the live results host blocked the development network; its rows and totals reproduce the official files. The test
fixture `nz2023_overall_results.csv` is a real official result, and
`nz2023_party_voting_places_1.csv` contains real Auckland Central voting-place rows.

After 27 November, use the equivalent 2026 paths (replace `electionresults_2023` by `electionresults_2026`) when
they appear, download the three files, and run:

```bash
BRUNDER_DATA=~/brunder-data-local uv run brunder nz ingest --year 2026 --source ~/Downloads/nz2026
```

## Boundaries and map

The 2025 Representation Commission boundaries used for the 2026 election are official Stats NZ data under
**Creative Commons Attribution 4.0 International**:

- General Electorates 2025 (64): `https://datafinder.stats.govt.nz/layer/122741-general-electorates-2025/`;
- Māori Electorates 2025 (7): `https://datafinder.stats.govt.nz/layer/122742-maori-electorates-2025/`;
- Urban Rural 2025 Clipped: `https://datafinder.stats.govt.nz/layer/120964-urban-rural-2025-clipped/`;
- Stats NZ public ArcGIS electorate services and the current Urban Rural land classes are used by the
  reproducible builder.

The legal electorate polygons extend offshore to the 12-nautical-mile limit.
`uv run python analysis/nz_geo.py` intersects them with Stats NZ's official land classes, excludes the remote Chatham Islands
from this main-islands view, adds feed-identical IDs `nz-gen-01` … `nz-gen-64` and
`nz-maori-65` … `nz-maori-71`, and writes a simplified topology with shared borders to
`site/data/geo/nz_electorates.topo.json`. General and Māori electorates overlap by design, so the dashboard
offers separate `general` and `maori` layers and defaults to the general layer. Credit: **Stats NZ, General
Electorates 2025, Māori Electorates 2025 and Urban Rural land classes, CC BY 4.0**.

## Seat law and exact 2023 reproduction

`brunder.newzealand.allocate_many` implements the Electoral Act 1993 seat rules:

1. A registered party qualifies with at least 5% of valid party votes or one electorate winner.
2. The 120-seat proportional pool (less independent/non-list-party electorate winners) is allocated by modified
   Sainte-Laguë, quotients `votes / (1, 3, 5, …)`.
3. A party keeps every electorate it wins. If that exceeds its proportional entitlement, the excess is an
   overhang and the House expands; other parties do not lose entitlement seats.

`tests/test_newzealand.py` reads the real Commission 2023 summary and reproduces every official party total:
ACT 11, Green 15, Labour 34, National 48, NZ First 8, Te Pāti Māori 6; 122 seats in total. Te Pāti Māori's six
electorates exceeded its four-seat party-vote entitlement, producing a two-seat overhang. The later Port Waikato
by-election is not part of that official general-election allocation.

## Frozen model

The unit is an electorate. At each XML release the model:

- aggregates current party and candidate votes in each electorate;
- estimates expected remaining valid votes from 2023 turnout, voting places counted and a nationally shrunk
  turnout-growth ratio;
- applies a nationally shrunk multiplicative transition to each electorate's 2023 party composition, blending a
  partly counted electorate toward its own current composition;
- simulates national party shares, electorate winners (including independent/non-list-party candidates), the
  5%/one-electorate threshold, Sainte-Laguë, independent-seat pool adjustment and overhang;
- evaluates every configured Polymarket seat/vote outcome and the two comparable Kalshi winner outcomes.

Ten 2025-boundary names have no exact 2023 name. The frozen name-only prior crosswalk is: East Cape←East Coast,
Glendene←Kelston, Henderson←New Lynn, Kapiti←Ōtaki, Kenepuru←Mana, Mt Maunganui←Bay of Plenty,
Waitākere←Te Atatū, Wellington Bays←Rongotai, Wellington North←Ōhāriu, and Ōtāhuhu←Panmure-Ōtāhuhu. This is an
approximation, not a claim that boundaries are identical.

No archived, timestamped 2020 or 2023 count sequence is available from the Commission. The public 2023 XML
archive is a final state, not a count-over-time archive; a third-party 2023 replay uses a simulated clock and is
not calibration data. Settings therefore use only final official elections. `analysis/nz_calibrate.py` calculates
centred party log-share transition RMS 1.056 for 2017→2020 and 0.468 for 2020→2023; the frozen pooled value is
0.817. The remaining shrinkage/candidate settings are fixed design choices documented in the registered
`configs/nz2026_v1.toml`; v2 inherits those settings byte-for-byte and is not tuned on 2026.

Before the count the model is centred on the 2023 official result mapped as above and intentionally uses no polls
or market prices. `site/data/nz2026/` contains the baseline map, constant frozen model probabilities, real
Polymarket CLOB history, and Kalshi one-minute candles sampled every 30 minutes. Live rows append to the same
file and include a map frame every five minutes.

## Markets

Configured count-resolvable Polymarket events:

- `new-zealand-legislative-election-winner` (six named most-seat outcomes);
- `new-zealand-election-2nd-place-393` and `new-zealand-election-3rd-place` (six named party outcomes each, in v2);
- `nz-election-national-party-of-seats` and `nz-election-labour-party-of-seats` (all seat bands);
- `nz-election-opportunity-party-of-seats` (all seat bands; 2023 TOP is used as the prior predecessor);
- `nz-election-popular-vote-margin-of-victory` (all National/Labour margin bands and another-party outcome);
- `nz-election-labour-v-national-of-party-list-vote` (first CLOB token is Labour, second National).

The recorder also observes Kalshi `KXNEWZEALANDPARLI-26DEC31-NAT` and `...-LAB`. Kalshi's tie rule can depend on
which tied party enters government, while the simulation breaks a seat tie by party votes; the pair is therefore
**not** labelled risk-free arbitrage. Polymarket's next-prime-minister, coalition, government-membership and
turnout events were inventoried but are excluded from the count model because the count alone does not settle
coalition/PM outcomes and turnout needs a separate eligible-elector/special-vote model.

## Commands and VM services

Generate or refresh pre-count files (safe until live phase unless `--force`):

```bash
BRUNDER_DATA=~/brunder-data-local uv run brunder nz preelection configs/nz2026_v2.toml --out site/data --days 14
```

Exact systemd `ExecStart` lines for the integrator (local disk, not the optional SSD):

```ini
Environment=BRUNDER_DATA=/home/brunder/brunder-data-local
ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder nz record configs/nz2026_v2.toml --interval 120 --until 2026-11-28T12:00Z
```

```ini
Environment=BRUNDER_DATA=/home/brunder/brunder-data-local
ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder nz live configs/nz2026_v2.toml --out /srv/brunder/repo/site/data --interval 60 --until 2026-11-28T12:00Z
```

The existing live JSON server must include the election in its argument list:

```ini
ExecStart=/usr/bin/python3 -m brunder.liveserve --root /srv/brunder/repo/site/data --election us2026 --election nz2026 --host 127.0.0.1 --port 8790
```

`brunder nz record` starts the official XML recorder, Polymarket order-book recorder and the two Kalshi books in
one process. Start it before the count. The live exporter only reads that archive; it never polls the Commission.
A static replay uses:

```bash
BRUNDER_DATA=~/brunder-data-local uv run brunder nz export configs/nz2026_v2.toml --out site/data
```
