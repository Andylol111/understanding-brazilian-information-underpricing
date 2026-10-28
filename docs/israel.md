# Israel, 26th Knesset (27 October 2026): seats, count model, and the recorded night

Status: seat allocation done and tested on the official 2022 result; count model calibrated on the
official 2021 and 2022 counts and frozen (`configs/il2026_v1.toml`); scored once on the recorded 2026
count. Evidence label for 2026: **pre-specified** (settings frozen before any 2026 model run, after the
election; the aggregate count and the market prices had been looked at while taking inventory). The
2026 outcome used below is **provisional** (latest count, double envelopes not yet counted) until the
CEC publishes its final result. Nothing from 2026 is published on the site (see "What can be published").

## Data sources and terms

| source | what | how obtained | terms | in `site/`? |
|---|---|---|---|---|
| Central Elections Committee (CEC), ballot-box files `expb.csv` for the 23rd (2020), 24th (2021) and 25th (2022) Knesset | final results by ballot box; for 2021 and 2022 also the count over time (every distinct capture during the count: 12 and 11 captures between 1% and 95% of boxes) | Internet Archive Wayback Machine copies of `mediaNN.bechirot.gov.il/files/expb.csv` (`analysis/il_fetch.py`, 3 s between downloads) | Official data. The same results are listed on data.gov.il ("תוצאות בחירות - ועדת הבחירות המרכזית לכנסת", licence "Other (Open)", `isopen: true`). Raw files are not committed (project rule) | derived aggregates only, with attribution |
| 2026 recording on the VM (`data/raw/snap/israel_2026`, copied read-only) | votes26.com API (live nowcast checkpoints incl. each CEC release's national total and capture time, exit polls, turnout; from 20:36 UTC), earthelects.com `results.json` (district totals per CEC release), books of 74 Polymarket markets every 30 s (from 18:31 UTC 27 Oct); all until 12:00 UTC 28 Oct | `brunder snap` | votes26 and earthelects are third-party derivatives of the CEC count: private research only | no |
| github.com/navonyaniv/election-il-2026, history of `results.json` (45 commits) | the same CEC releases by locality (votes per list, ballot boxes counted / total) | GitHub API + raw files (`analysis/il_fetch.py`) | third-party mirror, no licence (all rights reserved): private research only | no |
| CEC 2026 results site `votes26.bechirot.gov.il` | official count | not obtained: the recorder (which saves only HTTP 200 answers) saved nothing from it all night; from here the CEC results hosts (`votes25`/`votes26.bechirot.gov.il`) answer 403 (CloudFront) or 404. Not circumvented | official | — |
| Wayback Machine, 2026 | only one capture of the CEC national results page (22:34 UTC 27 Oct) and none of `media26` files | CDX search | — | — |
| data.gov.il | metadata of the results dataset (one `package_show` call) | the download host `e.data.gov.il` answers 403 from here; `robots.txt` disallows `/api/`, so no further API use | Other (Open) | — |
| Surplus-vote agreements 2026 | five pairs filed by the 16 Oct deadline | news reports (Kikar HaShabbat, Emess, 16 Oct 2026); the CEC's agreements page (gov.il) answers 403 | facts | yes |
| Polymarket | order books / prices | recorded books | as for the Brazil site | as for Brazil |

The 2022 count-order files that votes26 publishes (`replay-k25-real.json`, `first-vs-rest-k25.json`) were
copied but not used: the Wayback captures of the CEC's own `expb.csv` give the same 2022 count over time
from the official source.

### What can and cannot be published

- **Cannot**: anything built from the 2026 count (timeline `raw`/`share`/`P`, unit frames, settle times of
  the model, calibration). The only 2026 count we have is third-party (votes26, earthelects, navonyaniv),
  and no official count-over-time for 2026 exists in any archive. So `site/data/il2026/` is **not**
  produced and the dashboard should list Israel 2026 as not available (or model-only text).
- **Can**: the seat allocator and its test (official 2022 votes); results from the official 2021/2022
  replays (calibration tables, an "Israel 2022, in-sample" replay if wanted); the official 2026 final
  result once the CEC publishes it (a final map, not a replay); Polymarket prices on the same basis as
  Brazil.

## Seat allocation (`brunder.israel.allocate`, `allocate_many`)

Knesset Elections Law sections 81-81A ("Bader-Ofer"): lists below 3.25% of valid votes are out; general
quota = passing votes / 120; floor seats; the remaining seats one at a time to the largest votes /
(seats + 1), with the two lists of a surplus agreement counted as one list if both passed; the pair's
seats are split between them the same way. `allocate_many` does this for thousands of simulated results
at once.

`tests/test_israel.py` reproduces the official 25th Knesset exactly from the official final votes
(4,764,742 valid; Likud 32, Yesh Atid 24, Religious Zionism 14, National Unity 12, Shas 11, UTJ 7,
Yisrael Beiteinu 6, Ra'am 5, Hadash-Ta'al 5, Labor 4). Without the agreements Likud's 32nd seat goes
to Labor, so the agreements are exercised by the test.

2026 agreements used: Likud-Religious Zionism-Zehut, Shas-UTJ, Yashar!-The Democrats, Together-Yisrael
Beiteinu, Ra'am-Joint List.

## Count model (`brunder.israel.project`, `simulate`, `outcomes`)

One projection per CEC release. Units are localities (CBS/CEC code; 1,215 of the 1,224 2026 localities
exist in 2022) plus the double envelopes (code 9999), with the 2022 final result as prior.

- Uncounted votes: prior valid votes x growth (national growth estimated from counted localities; local
  growth from the locality's own counted boxes, both shrunk), times the share of its boxes not counted.
  Box totals come from the polling-station list (published before election day).
- Shares of the uncounted votes: weighted regression of each 2026 list's share in counted localities on
  the locality's 2022 composition (lists with >= 1% plus "other"), shrunk weakly to the national count.
  No lineage between 2022 and 2026 lists is needed (Yashar!, Together and the reunited Joint List are new).
  A partly counted locality mixes its own share with the regression; envelopes use the regression only.
- Uncertainty: locality residual variance, coefficient uncertainty, a shared error tau x sqrt(m(1-m)) on
  each list's share of the uncounted votes, and a log-normal error on the uncounted total. 4,000 draws
  per release go through the allocator; market probabilities are the share of draws where the market's
  rule (as written in the Polymarket rules, incl. tie-breaks) resolves YES.

### Settings, from 2021 and 2022 only (`analysis/il_calibrate.py`)

Replays of the official 2021 count (prior 2020) and 2022 count (prior 2021); 23 captures with 1-95% of
boxes counted, 299 list-share z-values (lists >= 1%).

| setting | value | how chosen |
|---|---|---|
| prior_votes | 100 | lowest share MAE on a grid 100-80,000 (0.18 pt; 0.33 pt at 20,000) |
| own_votes | 100 | same grid search, 25-4,000 (differences < 0.01 pt) |
| tau | 0.01 | smallest value with 90% coverage >= 0.90 in both halves of the count: 0.91 (1-50% counted) and 0.99 (50-95%); tau = 0 gave 0.89 / 0.98 |
| turnout_sd | 0.155 | RMS log error of the projected uncounted total |
| others | defaults | not tuned |

Two changes were made on the 2021/2022 results before freezing: the envelope unit stopped mixing in its
own counted share (envelopes are counted in batches by voter category; the first version gave |z| up to
33 at one 2022 capture), and the tau rule was changed from one pooled coverage to coverage in each half
(the late captures outnumber the early ones 2:1 and alone picked tau = 0 while 2021's 5-50% coverage was
about 0.8). The config was frozen at 2026-10-28 16:53 UTC; sha256
`575d282e646f03bdee836ace62fac28497622876a7d4804cb30d7d5715e50b25`. It is not yet committed or registered
(see "Open problems").

## 2026 count night, scored (`analysis/il_score.py`)

18 CEC releases between 22:23 UTC (15 boxes, 0.1%) and 10:40 UTC (84.6% of regular boxes) were seen by
our recorder. Each is used from the first time the recorder saw its national total (votes26 or
earthelects file, whichever came first; 0.2-0.7 minutes after the CEC-file capture time votes26 reports),
with its locality-level content from the navonyaniv mirror (matched on the national total). Market price:
mid if spread <= 10c, else last trade, bounded by the best bid/ask.

Outcome (provisional, latest count 12:34 UTC 28 Oct, 11,168 of 12,018 regular boxes, envelopes not
counted): Likud 26, Yashar! 21, Joint List 12, Together 12, The Democrats 8, Yisrael Beiteinu 7, Otzma
7, UTJ 7, Ra'am 7, Shas 7, RZ-Zehut 6. Netanyahu bloc 53, opposition bloc 48. All twelve scored markets
are on the same side as their end-of-recording prices.

Settled = first time after which P(eventual outcome) >= 0.9 holds to 12:00 UTC (UTC times, 27/28 Oct).
"Contested" = market price at the first release between 0.1 and 0.9.

| market | outcome | price at 1st release | model | model from 1% | Polymarket | raw count | lead (min) | lead from 1% |
|---|---|---|---|---|---|---|---|---|
| Likud most seats | yes | 0.85 | 27 22:24 | 28 00:13 | 27 23:03 | 27 22:23 | +39 | -70 |
| Yashar! most seats | no | 0.135 | 27 22:24 | 28 00:13 | 27 23:03 | 27 22:23 | +39 | -70 |
| Likud 20-24 seats | no | 0.30 | 27 22:24 | 28 00:13 | 28 00:19 | 27 22:23 | +115 | +6 |
| Likud 25-29 seats | yes | 0.58 | 28 00:40 | 28 00:40 | 28 01:21 | 28 02:49 | +41 | +41 |
| Likud 30-34 seats | no | 0.10 | 28 00:40 | 28 00:40 | 28 00:51 | 28 02:49 | +11 | +11 |
| Netanyahu bloc more seats | yes | 0.555 | 28 01:48 | 28 01:48 | 28 00:46 | 27 22:23 | -62 | -62 |
| 6 uncontested markets (Together/Democrats/Shas/Otzma most seats; Likud < 20, 35+) | no | <= 0.006 | | | already settled before the count | | | |

Lead = Polymarket settle minus model settle (positive: the model settled first). Contested markets: 6;
median lead **+39 min** as frozen (model first in 5, market first in 1). The frozen config lets the model
speak from the first release, which had 15 ballot boxes (0.1%), below the 1% lower edge of the range the
settings were calibrated on; on that release the model put Likud at 37 seats. Requiring >= 1% counted (a
sensitivity added after the first run, not pre-specified) gives a median lead of **-28 min** (3 each).
The robust part is the Likud seat bands: the model had Likud in 25-29 at P = 0.91 at 7.6% counted (00:40)
and settled 41 minutes before the market; the market settled the bloc market an hour before the model,
whose P(bloc) fell back to 0.77 at 13.8% counted.

Other standard statistics (`results/israel/stats.json`):

- Brier score over the 12 markets at each release: market better up to 2.5% counted (0.015 vs 0.041),
  model better from 7.6% (0.0014 vs 0.0042) and at every later release except 13.8% (0.0043 vs 0.0016).
- Early error, mean absolute error of list shares (lists >= 1%), model vs raw count: 0.81 vs 1.90 pt at
  2.5% counted; 0.45 vs 1.98 at 7.6%; 0.60 vs 1.53 at 13.8%; 0.45 vs 1.06 at 25%. Likud: +1.65 vs +5.87
  pt at 2.5%.
- Calibration of list shares, 1-95% counted: 90% coverage 0.76, mean z 0.05 (195 z-values, 15 releases)
  against the provisional outcome. The projection includes the double envelopes and the outcome does not
  (Ra'am, z +4 to +11, is the main miss), so this number waits for the official final.
- Catch-up slope (30-min price move on model-market gap, same filters and bootstrap as Brazil): -0.02
  (95% CI -0.09 to 0.09; 4 markets, 355 minutes, 15 clusters): no sign the market drifted toward the
  model.
- Model only (no market): P(Netanyahu bloc >= 61 seats) fell from 0.91 at 0.1% counted to 0.02 at 0.65%
  and 0 from 7.6%; outcome no (53).
- Capacity and arbitrage: not computed (no trading rule specified for Israel; Kalshi pairs are the
  Kalshi workstream's).

Changes to the scoring script after its first run (data handling, not model settings): box totals per
locality are now the largest seen across the mirror files (the first mirror file listed only reporting
localities, which made the first release look 100% counted); market prices are bounded by the book (one
one-sided book showed a stale 0.999 last trade under a 0.001 ask); the 1%-counted sensitivity above.

## Commands

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_fetch.py        # CEC files (Wayback) + mirror history
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_calibrate.py    # settings from 2021/2022
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_score.py configs/il2026_v1.toml
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_score.py configs/il2026_v1.toml --official <CEC final expb.csv>
    uv run pytest -q tests/test_israel.py

The recording was copied from the VM with
`ssh oracle-slotbank 'sudo -u brunder tar -C /srv/brunder/repo/data/raw/snap -czf - israel_2026' | tar -C $BRUNDER_DATA/raw/snap -xzf -`.

## Open problems

- The outcome is provisional. When the CEC publishes the final result (ballot-box file), rerun with
  `--official`; settle times only change if a seat outcome flips (Likud 26 sits inside 25-29; blocs 53-48).
- `configs/il2026_v1.toml` must be committed and registered (`brunder register`) to carry the
  pre-specified label on the record; the freeze time above is from this workstream, not git.
- The 2026 agreements are from news reports, not the CEC's own list (403 from here).
- The first release (0.1%) is outside the calibrated range; a gate at 1% should be part of the next
  version's config, decided before the next election.
- Calibration rests on two past counts with sparse archive captures (2 captures below 50% counted in
  2022, 5 in 2021).
- The model is locality-level because the recorded 2026 data are; the 2022 calibration could use ballot
  boxes but 2026 replays cannot.
