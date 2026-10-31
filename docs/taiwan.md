# Taiwan: Taipei mayoral election 2026 (`tw2026_taipei`)

This is a **forward election-night test**. `configs/tw2026_taipei_v1.toml` is frozen but intentionally
unregistered; the integrator must commit and hash-register it before polls close at **16:00 Taiwan time =
08:00 UTC on 28 November 2026**. The six candidates were approved by the Central Election Commission (CEC),
and ballot numbers were drawn on 27 October: Kuo Hsi (郭璽, 1), Lin Chih-cheng (林志成, 2), Puma Shen
(沈伯洋, DPP, 3), Hsiao Wen-kan (蕭文乾, 4), Chiang Wan-an (蔣萬安, KMT, 5), and Tang Hsin-min (唐新民, 6).
CEC sources: https://web.cec.gov.tw/central/article/65212 and https://web.cec.gov.tw/mect/article/65401.

Code is in `src/brunder/taiwan.py`; historical preparation/calibration and the software rehearsal are
`analysis/tw_calibrate.py` and `analysis/tw_rehearsal.py`; parser/model tests use real condensed CEC fixtures in
`tests/fixtures/tw2022_taipei_stations.json` and `tests/fixtures/tw2024_cec_station.html`.

## Markets (observed 2026-10-30)

Both required venues have an active mutually exclusive Taipei-winner event.

| Venue | Event / outcome | YES | Price or quote | Volume |
|---|---|---|---:|---:|
| Polymarket | `taipei-mayor-election-winner-20260813125857100` / `will-chiang-wan-an-win-the-next-taipei-mayor-election-20260813125857101` | Chiang Wan-an | 0.67 | $1,138,169 |
| Polymarket | same / `will-puma-shen-win-the-next-taipei-mayor-election-20260813125857102` | Puma Shen | 0.3215 | $1,171,206 |
| Polymarket | same / `will-kuo-hsi-win-the-next-taipei-mayor-election-20260813125857103` | Kuo Hsi | 0.0005 | $136,041 |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-CWAN` | Chiang Wan-an | 0.64 / 0.67 bid/ask | 162,453 contracts |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-SPY` | Shen Bo-yang (Puma Shen) | 0.33 / 0.35 | 383,852 |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-KHSI` | Kuo Hsi | 0.00 / 0.01 | 1,101 |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-THM` | Tang Hsin-min | 0.00 / 0.01 | 175 |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-HWK` | Hsiao Wen-kan | 0.00 / 0.01 | 1,110 |
| Kalshi | `KXTAIPEIMAYOR-26NOV28-LCC` | Lin Chih-cheng | 0.00 / 0.01 | 175 |

The Polymarket event reported $2,445,416 total volume. It resolves to the next elected Taipei mayor, using credible
reporting and, if ambiguous, official CEC results. Kalshi's event is `KXTAIPEIMAYOR-26NOV28`, is mutually
exclusive, and names the CEC as settlement source. Configured token IDs and tickers are frozen in the config.
Polymarket's unused Candidate A-Z shells had zero volume and are excluded; the six official candidates are all
represented by the six model probabilities and by Kalshi.

## CEC reachability and formats

Checks used `User-Agent: brunder/0.1 (research; recorder)` and did not evade a block.

* `https://db.cec.gov.tw/robots.txt` returned 404 (no published robots rules). One 2022 result-page request returned
  HTTP 200 (`text/html`, 268,669 bytes). Its public static T-level endpoint returned HTTP 200 JSON. The JSON is a
  dictionary of li groups containing one row per candidate and polling station, with `dept_code`, `li_code`,
  `tbox_no`, candidate name/party, votes and percentage. Official Taipei files contain 1,534 stations / 456 li in
  2014, 1,563 / 456 in 2018, and 1,755 / 456 in 2022. The database says its data are updated within seven days,
  so it is a historical/final source, not the night feed.
* The prior live-results implementation is independently visible in the CEC's 2024 site. Its HTML fragments carry
  `已送投票所數/總投票所數` (stations received/total), candidate or party rows, and `資料時間: MM/DD HH:MM`.
  The real archived fixture has progress 1/1 and a 01/13 22:21 stamp. `parse_result_html` enforces that contract.
* On 30 October, `vote2026.cec.gov.tw` was not in DNS and the Mac received HTTP 403 from
  `info.cec.gov.tw`. A server check used the research User-Agent, requested `robots.txt` first (HTTP 403), waited at
  least two seconds between requests, and made no attempt to evade it. From that server,
  `https://info.cec.gov.tw/vote2026/` returned HTTP 200 HTML. Its runtime config sets the public source to
  `/vote2026/static`; the published app chunks expose election-information endpoints, not live-count endpoints.
  `static/json/dist/63000_deptLi.json` returned HTTP 200 JSON with all 12 Taipei districts and 456 li; the committed
  condensed real fixture records its `{depts:[{deptCode,deptName,lis:[{liCode,liName}]}]}` format.
  `static/json/cand/00000.json` and `static/json/type/C.json` were not yet published (404).
* Candidate result paths under the official app base, for example
  `/vote2026/zh-TW/M1/63000001000020000.html` and the `C1` equivalent, currently return the 3,732-byte app shell,
  not count data. No result URL or result schema occurs in the deployed JavaScript. The recorder now targets this
  official base and tries both historical mayor prefixes, but accepts a response only if the strict 2024
  stations-received/candidate/timestamp parser succeeds. The precise 2026 count endpoint and whether its granularity
  is li or polling station therefore remain **unverified** until CEC publishes results.

The recorder requests the 456 candidate li aggregate paths sequentially with a two-second delay. One pass takes
about 15 minutes and starts no more often than every 20 minutes; only changed, successfully parsed results are
stored. It also records the Polymarket event and all six Kalshi books every 30 seconds. The live exporter reads that
archive only.

CEC election data are published through the government open-data platform under the **Taiwan Government Data Open
Licence 1.0**: https://data.gov.tw/license. The committed `data_public/tw_taipei_mayor_2014_2022.csv` is a small,
attributed derivative. Raw 4-11 MB official JSON files remain under `$BRUNDER_DATA/raw/taiwan/cec/`, not in Git.

## Frozen prior and nowcast

The unit is the **li** (里, neighborhood), the smallest stable administrative unit shared by all three historical
elections; polling-station numbers and boundaries change. There are 456 li pooled through a citywide swing, with
12 districts retained as dashboard regions. The map uses the Ministry of the Interior NLSC village boundary map
(https://data.gov.tw/dataset/7438; Open Government Data License 1.0), fetched from WFS `WFS:VILLAGE_NLSC` in
16 permitted 10 km requests and simplified by `analysis/tw_geo.py` to `site/data/geo/tw_taipei_li.topo.json`
(456 matching li ids, 0.15 MB).

The 2022 lane mapping is Chiang/KMT, Chen/DPP, Huang/TPP-aligned independent, and all others. Because no TPP
candidate registered in 2026, Huang's spatial profile transfers 80% to KMT and 20% to DPP. This was frozen from
public cross-tabs: 75% of TPP identifiers backed Chiang and 16% Shen in the October UDN survey; an earlier public
survey reported 88.5% for Chiang. Before raking, this 2022 profile implies Chiang 61.2% and Shen 36.2%; each li
profile is then multiplicatively raked to the poll target below.

Public polls exist, so the model uses a poll prior. Two October headline vote-intention polls available before
freeze are used:

| Field dates / source | n | Chiang | Shen | Others/undecided |
|---|---:|---:|---:|---:|
| Oct 20-22 and 25, UDN (reported by Taiwan News) | 1,115 | 41.0% | 45.0% | 14.0% |
| Oct 14-16, Taipei Trust in Democracy / Beacon | 1,075 | 33.3% | 50.3% | 16.4% |

Their mean Chiang share of Chiang+Shen is 43.75%. In the 2018 and 2022 Taipei polls used for calibration, KMT's
share against the leading rival was understated by 5.66 and 4.81 points; `poll_target` adds the mean, 5.23 points,
giving Chiang 48.99% of the two leading candidates. Named candidates receive 98%; the four minor candidates receive
2% in the September Beacon/Taipei Trust in Democracy proportions (Kuo 0.7, Tang 0.6, Hsiao 0.5, Lin 0.3). Frozen
expected vote: Chiang 48.01%, Shen 49.99%, Kuo 0.67%, Tang 0.57%, Hsiao 0.48%, Lin 0.29%; P(Chiang)=0.41.
Without the historical bias correction the same polls give Chiang 42.88% and P(Chiang)≈0.07. Pre-count uncertainty
remains six share points per candidate (an 8.5-point Chiang-Shen margin sd under the independent draw). Market
prices do not enter the model.

**Disclosure (decided before any 2026 count, 2026-10-30):** the historical poll-bias correction was added after the
first version of the model gave Chiang 0.07 against market prices of about 0.67. The correction uses only 2018 and
2022 Taipei polls and results (no 2026 result), but the decision to add it was prompted by the gap to the market,
which the registration states. Both numbers are recorded: without the correction P(Chiang) ≈ 0.07, with it 0.41.

For each release, the model:

1. estimates each li's final valid votes from 2022 turnout and a citywide growth ratio shrunk by 100,000 votes;
2. applies the observed citywide candidate swing to the prior, shrunk by 5,000 votes;
3. blends a reporting li toward its own observed composition (100 remaining-vote pseudo-votes);
4. adds observed votes to projected remaining votes and draws candidate shares with frozen
   `sd = max(0.002, 0.020 * (1 - counted))`; the largest draw wins.

`own_votes=100` and `swing_votes=5,000` minimized candidate-share MAE (0.00561) on 2014→2018 among the declared
4×4 grid. Historical CEC snapshots are final only, so count order is simulated: 20 random orders and 20 adverse
small-stations-first orders. `tau=0.020` is the smallest tested value with overall 90% interval coverage above 0.90
in both 2018 order families: 0.996 random and 0.943 small-first. Nothing was tuned on 2022.

## Out-of-sample 2022 backtest

The 2022 prior uses the public 1-5 November 2022 RWNews topline (Chiang 36, Chen 33, Huang 28, other 3) and official
2018 spatial profiles. Scores below average 20 random and 20 small-first station orders. Coverage is the fraction of
candidate shares inside the nominal 90% interval; Brier is the mean binary score over four winner outcomes.

| Votes counted | 90% coverage | Brier | Log loss | P(eventual winner) |
|---:|---:|---:|---:|---:|
| 1% | 0.994 | 0.0001 | 0.0050 | 0.995 |
| 5% | 1.000 | 0.0000 | 0.0001 | 1.000 |
| 10% | 1.000 | 0.0000 | 0.0000 | 1.000 |
| 25% | 1.000 | 0.0000 | 0.0000 | 1.000 |
| 50% | 1.000 | 0.0000 | 0.0000 | 1.000 |
| 75% | 1.000 | 0.0000 | 0.0000 | 1.000 |
| 90% | 1.000 | 0.0000 | 0.0000 | 1.000 |

The strong result is not a timestamped historical replay: order is simulated and must be labelled that way. It tests
count-order robustness, not whether a 2026 poll prior will be unbiased. Reproduce with
`uv run python analysis/tw_calibrate.py`; machine-readable output goes under ignored `results/taiwan/`.

## Commands and runbook

```bash
# Build pre-count dashboard files and current market rows (safe before the live phase)
BRUNDER_DATA=~/brunder-data-local uv run brunder taiwan preelection configs/tw2026_taipei_v1.toml --out site/data

# Recorder: systemd starts this at 06:00 UTC; one CEC request every two seconds
BRUNDER_DATA=~/brunder-data-local uv run brunder taiwan record configs/tw2026_taipei_v1.toml \
  --interval 1200 --until 2026-11-28T14:00Z

# Exporter: systemd starts this at 07:30 UTC
BRUNDER_DATA=~/brunder-data-local uv run brunder taiwan live configs/tw2026_taipei_v1.toml \
  --out site/data --interval 60 --until 2026-11-28T14:00Z
```

At 06:00 UTC, inspect the first CEC response before trusting the feed. An app-shell response will be rejected as
non-result HTML. If CEC publishes a different public result path or schema, preserve the response and make a new
versioned config/parser before using it; do not bypass a 403 or substitute an unofficial count. At 07:30, confirm
`/tw2026_taipei/timeline_tail.json` is served and add `--election tw2026_taipei` to the deployed
`brunder.liveserve` argument list. Continue through 100%, then preserve the final official db.cec.gov.tw JSON.

Software rehearsal using real 2022 station votes on a simulated 2026 clock:

```bash
rm -rf /tmp/tw-rehearsal /tmp/tw-site
uv run python analysis/tw_rehearsal.py --out /tmp/tw-rehearsal/snap
uv run brunder taiwan export configs/tw2026_taipei_v1.toml \
  --snap /tmp/tw-rehearsal/snap --out /tmp/tw-site --until 2026-11-28T11:01:00Z
```

The four deployment files are `deploy/brunder-tw-{record,live}.{service,timer}`. The timers start exactly at 06:00
and 07:30 UTC; both processes stop at 14:00 UTC.
