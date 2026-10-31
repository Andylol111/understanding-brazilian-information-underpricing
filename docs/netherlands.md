# Netherlands: Tweede Kamer election of 29 October 2025 (`nl2025`)

**Retrospective, not pre-registered.** This is a replay written a year after the vote, with the result and the
market prices known. The evidence label is `not-preregistered`: the projection's settings were chosen on the 2017,
2021 and 2023 elections and scored once on 2025, but were not frozen in the repository before scoring.

**No count history: market vs outcome only.** No timestamped per-gemeente count of the night could be rebuilt from an
official or openly licensed source (see "Count history"), so there is no count model, no accuracy, calibration or profit
result. The dashboard entry is a *final-result scoring page* (`meta.count_timeline: false`, like `ca2025`): Polymarket
prices over the night, the official final result on the map, and a projection made **before any votes** (the 2023
result, and the final polls) that stays flat across the night.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_calibrate.py   # settings, from 2017/2021/2023 only
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_geo.py         # maps
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_export.py      # site/data/nl2025/*
    uv run pytest -q tests/test_netherlands.py

Code: `src/brunder/netherlands.py` (Kiesraad parsers, seat allocation, poll-table parser, projection, market rules),
`analysis/nl_calibrate.py`, `analysis/nl_geo.py`, `analysis/nl_export.py`; frozen settings `configs/nl2025_v1.toml` (not
registered). Tests: `tests/test_netherlands.py`. Raw downloads are cached in `$BRUNDER_DATA/raw/netherlands`
(about 170 MB with the 2017 EML archive; never committed): `x12 x17 x21 x23 x25` (unzipped Kiesraad CSVs), `eml17`
(2017 kieskring EML), `wiki/polls_html_<year>.json`, `gemeente_2025.geojson`, `polymarket/`.

## Data sources and licences

| Data | Source | Licence |
|---|---|---|
| Final results 2025: votes by list for every gemeente, seats (`TK2025_uitslag.csv`, 2.3 MB zip) | Kiesraad, data.overheid.nl dataset `verkiezingsuitslag-tweede-kamer-2025` (also EML; the CSV is "as imported on verkiezingsuitslagen.nl", its README calls it research data, not the official result) | **CC0 1.0** |
| 2023 result by gemeente (the prior), and 2012, 2017 (kieskring EML; the gemeente CSV covers only 279 of 388 gemeenten), 2021 (national totals for the calibration) | Kiesraad, data.overheid.nl datasets `verkiezingsuitslag-tweede-kamer-2023/-2021/-2017/-2012` | **CC0 1.0** |
| Gemeente boundaries 2025, `gemeente_gegeneraliseerd` (342 polygons, land only), simplified to 107 kB | CBS / Kadaster "Gebiedsindelingen 2025" via PDOK WFS | **CC BY 4.0** (CBS, Kadaster) |
| Poll tables (fieldwork end date, seats by list) for 2017, 2021, 2023 and 2025 | English Wikipedia, "Opinion polling for the <year> Dutch general election" (2025 page revision 1381269072), polls by Ipsos I&O, Peil.nl, Verian and others | **CC BY-SA 4.0** |
| Price history (1-minute) and market rules | Polymarket CLOB `prices-history` and Gamma API, by token id | Polymarket public data |
| Candlesticks (1-minute, historical tier) of the "most seats" markets `KXPARTYWINNL-25-<party>` ("Who will win the Dutch general election?") | Kalshi Trade API `/historical/markets/<ticker>/candlesticks` | Kalshi public market data |
| Internet Archive CDX index and `id_` captures (searched; see below) | web.archive.org | the archived sites' own terms; nothing from them is published |

Honest client: `User-Agent: brunder/0.1 (research; recorder)`, at most one request a second to any host, and 10 s between
downloads from data.overheid.nl, whose robots.txt sets `Crawl-delay: 10` (the files themselves are not disallowed).
The dashboard's `meta.sources` names each source with its licence.

## Count history: what was searched, and why there is none

Window: 2025-10-29 20:00 UTC (polls closed 21:00 CET) to 2025-10-30 12:00 UTC.

- **Kiesraad results site** (`verkiezingsuitslagen.nl`). The CDX index has no capture of `/verkiezingen/detail/TK20251029` (or
  `detailJson/TK20251029`) before **2025-11-14**; inside the window it holds only the landing page (before the polls closed),
  stylesheets and older elections' pages that the archive crawled that day. The final results appear on data.overheid.nl
  only afterwards (dataset created 2025-11-11).
- **Kiesraad** (`kiesraad.nl`): news pages only, no results.
- **NOS results app** (`app.nos.nl/nieuws/tk2025/`): about 11 captures of the main page and a handful of gemeente pages
  (Amsterdam 03:47 UTC, Tilburg, Groningen, ...). The numbers are loaded by script from NOS's own back end (`/api` is
  disallowed in its robots.txt) and the archived HTML says "Wachten op uitslagen..." with zero votes (captured copies read at
  00:32 and 03:47 UTC). Nothing to read, and NOS content has no open licence, so it was not pursued further.
- Other news sites (nu.nl, nrc.nl, nos.nl articles) and ANP feeds: not openly licensed; domain-wide CDX queries timed out and
  were not pursued.
- Gemeenten publish only their own results (Nijmegen's open data is CC BY 4.0 but is one gemeente and was loaded in December).

So the only count information from the night would be third-party news pages, which this project does not use. The
final result is by polling station (`TK2025_Stemmen_Per_Lijst_Per_Stembureau.csv`) and carries no time stamps.

## Seat allocation (`brunder.netherlands.allocate`)

Kieswet (no list combinations since 2017): a list needs one quota (all valid votes / 150) to take part, seats go by D'Hondt's
highest average. It reproduces the official seats from the official votes in 2017, 2021, 2023 and 2025 (`tests` check 2023
and 2025 with the real national votes). 2025: D66 26, PVV 26, VVD 22, GL/PvdA 20, CDA 18, JA21 9, FvD 7, BBB 4, SP 3, DENK 3,
PvdD 3, SGP 3, CU 3, 50PLUS 2, Volt 1 (NSC, with 0.37% of the vote, none).

Polymarket's rule for "most seats" and "second most seats" is a tie going to **the alphabetically first abbreviation**, so the
26-26 tie between D66 and PVV resolved to D66 (D66 also had more votes: 1,790,634 against 1,760,966). `rank_order` implements
the rule; `analysis/nl_export.py` asserts that the rule applied to the official seats reproduces Polymarket's resolution of
all 45 markets used.

## Projection before any votes (`simulate_seats`)

Two versions, drawn as the dashboard's "without polls" (dashed) and "model, polls" lines; both flat, because no count enters:

1. **Without polls**: the 2023 national result for the 16 lists that stood in both elections (shares of those lists), shares
   perturbed by independent normal errors with sd = `err_prev` x sqrt(b(1-b)).
2. **With polls** (the headline): the average of each pollster's last poll that ended 1-7 days before the vote, as seats/150
   (Ipsos I&O 25-28 Oct, Verian 27-28 Oct, Peil.nl 28 Oct; the election-day exit poll is excluded), with `err` x sqrt(b(1-b)).
   Average seats: PVV 25.0, GL/PvdA 23.7, D66 23.0, CDA 19.3, VVD 18.3, JA21 9.3, FvD 6.3, SP 4.3, BBB 4.0.

Each draw adds the fixed pool of "other" lists (they count in the quota, never take a seat), runs `allocate`, and the share of
20,000 draws in which a market's outcome holds is the chance. The markets' outcome rules are those on Polymarket.

### Settings, from earlier elections only (`analysis/nl_calibrate.py`, `configs/nl2025_v1.toml`)

Backtests on 2017 (previous result 2012), 2021 (2017) and 2023 (2021); 2025 is never used. Final polling average of the last
poll per pollster, 1-7 days before the vote, against the official national result (shares among the lists of the poll table).

| setting | value | how chosen |
|---|---|---|
| `w_prev`, weight of the previous result in the central share | 0 | lowest RMS share error over the three backtests: 1.41 pt at 0, 1.44 at 0.05, 1.60 at 0.15, 2.82 at 0.5 |
| `err`, share error sd = err x sqrt(b(1-b)) | 0.048 | RMS of 42 standardised errors (lists with b >= 0.5%) at that weight; RMS error of the poll average alone 1.51 pt (2017), 1.22 (2021), 1.52 (2023) |
| `err_prev` ("without polls") | 0.174 | same for the previous result alone, 39 errors |
| `other` | 0.018 | mean share of valid votes for lists that won no seat: 1.53% (2017), 1.98% (2021), 1.87% (2023) |

Caveats: three elections, 42 errors, and the same elections give both `w_prev` and `err`; independent errors by list ignore the
correlation between rival parties; poll seats / 150 stands in for vote shares. The poll average missed PVV by +5.1 points in
2023 and VVD by +4.2 in 2017, which the normal error model treats as rare.

## Result against the market

Official 2025: D66 26 seats (16.94% of votes), PVV 26 (16.66%), VVD 22, GL/PvdA 20, CDA 18, JA21 9. By gemeente (342 on the
map) PVV had the most votes in 158, D66 in 80, VVD 42, CDA 38, SGP 16 and GL/PvdA 8; D66 won the national vote through the cities.

**Most seats (D66)**, Polymarket price of "D66 wins the most seats" (UTC):

| time | price | note |
|---|---|---|
| 18:00 | 0.36 | window opens (19:00 CET) |
| 19:59 / 20:00 | 0.51 / 0.86 | polls close, exit poll (Ipsos I&O: D66 27, PVV 25, VVD 23) |
| 00:07 | 0.54 | the low of the night after poll close |
| 00:59 | 0.91 | first minute from which the price stays at or above 0.9 (first touch of 0.9 is the same minute) |
| 01:11 | 0.95 | first touch of 0.95; it later falls back to 0.94 at 12:00 and never stays above 0.95 |

**Kalshi** (five of its fifteen markets were quoted: D66, PVV, GL/PvdA, VVD, CDA; the rest had no quotes in the window) priced
the tie risk differently and settled it differently: it settled D66 and PVV at **$0.50 each** (a scalar "tie split"), Polymarket
gave the tie to D66 by its alphabetical rule. Kalshi's D66 price was 0.375 at 18:00, 0.49 at 19:59, 0.79 at 20:30, 0.85 at
21:00, 0.55 at 23:00, 0.64 at 01:00 and 0.55 at 12:00 next day, so it never stayed above 0.9 (`settle.kalshi` applies the 0.9
rule to the Polymarket outcome and is null for D66 and PVV). The two venues' D66 and PVV markets are therefore not
risk-free pairs and no arbitrage was computed.

PVV's "most seats" price fell from 0.435 (18:00) to 0.155 (poll close), rose to 0.40 at 00:30 UTC and fell to 0.09 at 00:59.

**Model**: the poll-based projection never reached 0.9 for D66 (0.24 for D66 to win the most seats, PVV 0.45, GL/PvdA 0.29);
the 2023 result alone gave D66 0.2% and PVV 66%. There is no count model, so no "model 90% sure" time and no lead over the
market can be measured. What the page can say: the price of the eventual winner moved from 0.36 to 0.86 on the exit poll
(public information, not the count) and then did not settle until about five hours after the polls closed, with the tie
unresolved by the exit poll.

**Brier score** (`stats.score`, mean over the group's markets; lower is better; the projection is flat so it has one value):

| group (markets) | market 18:00 | market 20:00 (poll close) | market 21:00 | market 23:00 | market 12:00 next day | projection, polls | projection, 2023 only |
|---|---|---|---|---|---|---|---|
| most seats (15) | 0.041 | 0.003 | 0.003 | 0.018 | 0.0004 | 0.058 | 0.098 |
| second most (6) | 0.113 | 0.099 | 0.008 | 0.046 | 0.032 | 0.114 | 0.138 |
| seat bands (24) | 0.074 | 0.109 | 0.015 | 0.022 | 0.001 | 0.077 | 0.219 |
| all (45) | 0.068 | 0.072 | 0.010 | 0.024 | 0.005 | 0.076 | 0.168 |

The projection from the polls scores like the market did before the polls closed (all markets 0.076 against 0.068) and far
better than the 2023 result alone (0.168); once the exit poll was out the market was much better. The 2023 gemeente result
scaled by the polling swing predicts the D66 share of D66 + PVV in the 342 gemeenten with a mean absolute error of 4.0
points (27.1 for the 2023 result unchanged).

Not computed, and why: Brier by percentage counted, settle times of a count model, catch-up slope, capacity and profit
(no count over time); arbitrage (tie rules differ across venues and there is no count to trade on).

## What the dashboard shows

`site/data/nl2025/`: `meta.json` (45 races, every Polymarket question its own race with A = YES: 15 "most seats", 6 "second
most seats", 24 seat bands for PVV, GL/PvdA, VVD, D66, CDA, JA21; the headline race is first: D66 most seats), `timeline.json`
(Polymarket 1-minute price from 18:00 to 12:00 UTC, a row only where the price moves; `P` = without polls, `P_v2` = with polls,
both constant), `units.json` (one final frame over 342 gemeenten: `a` = D66 share of D66 + PVV x1000, `prior` = the same in 2023,
`proj` = the poll-swing projection, `swing`, `winner`, `margin`), `stats.json`, `entry.json` (merged into
`site/data/elections.json`). Maps: `site/data/geo/nl_gemeenten.topo.json` (342 gemeenten) and `nl_regions.topo.json` (12
provinces). Bonaire, Sint Eustatius, Saba and voters abroad are in the national totals but have no polygon.

Front-end changes: only `NL` added to the level names (`election.js`) and to the homepage's regional-map list (`world-map.js`).

## Open problems

- If a timestamped per-gemeente feed of the night turns up with an open licence (ANP/Kiesraad data, a gemeente), a Hungary-style
  count replay is possible on the same units and files; the model there would be calibrated on the 2017-2023 counts, which
  have the same lack of history, so the first step is a count history for any past election.
- The calibration uses seats/150 as vote shares and three elections; a second source of poll data (percentages) would tighten it.
