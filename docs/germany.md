# Germany 2025: Bundestag election (23 February 2025)

**RETROSPECTIVE replay, not pre-registered.** It was built after the election, with the result and the Polymarket
resolutions known. It compares a count model against Polymarket minute by minute. The model is
`brunder.germany`, applied to the 299 Wahlkreise. V1, the headline, chose settings on the **2021 election night only**
(never on 2025). V2 and v3 are sensitivities chosen after the 2025 result was known; see "Sensitivity v2" and
"Sensitivity v3" (poll prior).

## Feasibility: the official count over time exists, partly

The Bundeswahlleiterin's results site (`bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/`) is **server-rendered
HTML**, with no JSON or XML count files behind it. Each page carries a caption such as
`Zwischenergebnis: 23.02.2025 22:11:26 Uhr, 80 von 299 Wahlkreisen ausgezählt` (Berlin time) and a table of first and
second votes. The page-level stamp is the time the data was computed, so each capture says what the site showed
since then. The open-data CSV (`kerg2.csv`) was captured once, at 03:23 CET, and is not used for the night.

- **Pages used** (Internet Archive CDX, 2025-02-23 17:00 to 2025-02-24 06:00 UTC, de and en, www and bare host):
  - 314 captures: 28 national (`bund-99`), 67 Land (`land-N`), 219 single Wahlkreis.
  - 269 carry a result table, the rest were taken before the first Wahlkreis reported.
  - They hold **15 distinct national stamps** (3 of 299 Wahlkreise at 21:00 CET, 80 at 22:11, 175 at 23:02, 299 at
    01:33, with gaps of 6 to 69 minutes, median 13.5), **40 distinct Land stamps** and **177 single-Wahlkreis pages**.
  - A Wahlkreis page is stamped with that Wahlkreis's own arrival time, so for 177 of 299 Wahlkreise the exact time
    they entered the count is known (first one 20:38 CET). The other 122 are known only through the totals on the
    national and Land pages.
  - 129 distinct minutes at which something new was visible (from 20:38 CET to 01:34 CET).
  - Wahlkreis pages match the final result within 0.003% of valid votes (median; largest 1.6%).
- **Not available:** the Wahlkreis-by-Wahlkreis counted state at an arbitrary time. The model therefore combines three
  levels: exact Wahlkreise (page captured), Land totals (Land page) and the national total (national page); see
  "Model".
- `robots.txt` answers 404 (no restrictions). Requests: sequential, at least 1.1 s apart, User-Agent
  `brunder/0.1 (research; recorder)`, also to `web.archive.org`.
- **Licences.**
  - The open data (`kerg2.csv`, `btwkr25_umrechnung_btw21.csv`, Wahlkreis names, parties):
    **Datenlizenz Deutschland - Namensnennung - Version 2.0**, (c) Die Bundeswahlleiterin, Wiesbaden. The site publishes
    them with attribution.
  - The Wahlkreis geometry is (c) Die Bundeswahlleiterin / Statistisches Bundesamt 2024, Grundlage der Geoinformationen
    (c) Geobasis-DE / BKG 2024, same licence.
  - The night pages carry no licence statement. They are official pages of a federal authority. The site publishes only
    the derived per-minute series and the per-Wahlkreis counted flags and shares, with attribution, not the HTML. If the
    owner wants a stricter reading, remove `count_timeline` from `meta.json`.

## Data on disk ($BRUNDER_DATA = ~/brunder-data-local)

| what | where | size |
|---|---|---|
| 2025 night pages (314 HTML) and CDX indexes | `raw/germany/wayback/`, `raw/germany/cdx_*.json` | 17 MB |
| 2021 night pages (407 HTML), 2021 `kerg2.csv` (with the 2017 result), CDX | `raw/germany/wayback2021/`, `open2021/` | 25 MB |
| 2017/2013 archive checks | `raw/germany/{cdx,wayback,captures}{2017,2013}*` | 1 HTML / no HTML |
| 2017 result by Wahlkreis with 2013 as Vorperiode (`btw17_kerg.csv`, Internet Archive copy of the Bundeswahlleiter file), poll pages (Wikipedia REST HTML) | `raw/germany/btw17_kerg.csv`, `raw/germany/polls/` | 0.2 MB + 7.7 MB |
| open data 2025, geometry | `raw/germany/open/`, `raw/germany/geo/` | 6 MB |
| Polymarket: 14 events, 1-minute YES prices 14:00 UTC 23 Feb to 12:00 UTC 24 Feb | `raw/polymarket/<event>/` | 2 MB |
| Kalshi 1-minute candles of 3 markets | `raw/kalshi/germany/` | tiny |

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/de_fetch.py open && uv run python analysis/de_fetch.py open2021
uv run python analysis/de_fetch.py wayback && uv run python analysis/de_fetch.py wayback2021
uv run python analysis/de_fetch.py wayback2017 && uv run python analysis/de_fetch.py wayback2013 && uv run python analysis/de_fetch.py open2017
uv run python analysis/de_fetch.py markets && uv run python analysis/de_fetch.py kalshi
uv run python analysis/de_calibrate.py            # v1: 2021 -> results/germany/calibration_2021.json
uv run python analysis/de_calibrate_v2.py         # fixed rule -> results/germany/calibration_v2.json
DE_OUT=results/germany/v1 uv run python analysis/de_score.py configs/de2025_v1.toml
DE_OUT=results/germany/v2 uv run python analysis/de_score.py configs/de2025_v2.toml
uv run python analysis/de_polls.py fetch && uv run python analysis/de_polls.py error   # data_public/de_polls_*.csv
uv run python analysis/de_calibrate_v3.py         # rule in 'Sensitivity v3' -> results/germany/calibration_v3.json
DE_OUT=results/germany/v3 uv run python analysis/de_score.py configs/de2025_v3.toml
uv run python analysis/de_geo.py && uv run python analysis/de_export.py
uv run python analysis/decided_stats.py de2025 && uv run python analysis/de_export_v3.py && uv run python analysis/decided_stats.py de2025
```

## Model (`brunder.germany`, config `configs/de2025_v1.toml`)

The race is the second-vote (list) share of eight groups: CDU/CSU, AfD, SPD, Greens, Die Linke, BSW, FDP and the rest.
At each of the 129 minutes the state is what the pages stamped up to then show:

1. **Counted votes by Land.** The latest Land page up to the minute, plus the exact Wahlkreise whose page is stamped
   after it. If a Land has no page yet, its identified Wahlkreise only. The surplus of the latest national page over the
   sum of the Länder is spread over the unfinished Länder in proportion to their uncounted electorate.
2. **Prior.** The 2021 result on the 2025 constituencies (the Bundeswahlleiterin's recomputation, published before the
   election). The electorate of each Wahlkreis (eligible voters) is known before polling day; it is the only 2025
   number taken from `kerg2.csv`, and it enters as weights, not as votes.
3. **Additive swing per party.** Per Land, the counted share minus the prior share of the counted votes (the exact
   Wahlkreise use their own prior, the rest the Land's prior), shrunk to the national swing by `kappa` pseudo-votes.
   It is added to the prior of every uncounted Wahlkreise (floored, renormalised over parties). The remaining valid
   votes are the uncounted electorate times the prior turnout times a national turnout scale (shrunk to 1 by `theta0`
   pseudo-votes).
4. **BSW has no prior** (it did not exist in 2021). Its prior share is `0.3 x (AfD + Die Linke)` 2021 share: a
   **judgment**, not calibrated, because BSW was a Linke split courting AfD voters. The level only shifts the national
   swing; the factor 0.1 and 1.0 change nothing that matters (BSW settle +6 min in all three; all-market Brier
   0.0428 / 0.0426 / 0.0421).
5. **Uncertainty.** The sd of a party's final share is `max(floor, sd_a x share x f_rem^sd_b)`, where `f_rem` is the
   expected share of valid votes still to come. Errors are correlated (2021 estimate, shrunk halfway to the sum-to-zero
   equicorrelation; BSW -0.1 with all). 20,000 joint draws (fixed, common random numbers) give every market's P(YES).
6. **Seats.** "Most seats" and "second most seats" are the order of the second-vote shares. Seats are proportional to
   second votes among the parties above 5% (or with three direct mandates, which changes nothing at the top), so the
   order is the same; Sainte-Laguë/Schepers is **not** run, because no market asks for a seat count.

**Markets priced (46):**
- most seats (7) and second most seats (6);
- CDU/CSU vote-share brackets (6 plus 6 two-point brackets), AfD (6 plus 5), SPD (6);
- thresholds: BSW, FDP and Die Linke 5% or more; Greens over 15%.

**Not priced:** chancellor, coalition (bargaining, not the count) and "Die Linke as a Fraktion" (needs the seat count).
Nine are drawn on the dashboard.

## Settings: chosen on the 2021 night only

`analysis/de_calibrate.py` runs the same pipeline on the 2021 night: Internet Archive copies of the 2021 pages (18
national, 14 Land and 138 Wahlkreis pages, 151 frames), with the **2017 result on the 2021 constituencies** as prior
(the "Vorperiode" columns of the 2021 `kerg2.csv`) and the 2021 result as truth. No 2025 number goes in.

| setting | value | how chosen |
|---|---|---|
| `kappa` | 3,000,000 | smallest mean squared share error of the six big parties (grid 3e5 to 3e7; 1e6 to 3e7 are within 1%) |
| `sd_a` | 0.20 | smallest value with pooled 90% coverage >= 0.90 (0.948; 0.15 gives 0.866) |
| `sd_b` | 0.90 | slope of log error on log `f_rem` (fitted 0.90) |
| `sd_floor` | 0.0001 | the 2021 last national page equals the final to rounding; floored at 0.0001 |
| correlations | 2021 | pooled standardised errors |
| `theta0` 1e6, `bsw_c` 0.3, BSW correlation -0.1 | | judgment, not tuned |

Tried and dropped on 2021 (they did not help): within-Land slopes of the swing on the prior share, and a ratio instead
of an additive swing (additive was 5-10% better).

**The 2021 night was a hard one for any prior-based model.** Root mean squared share error of the six big parties
in 2021, model against the raw count, by electorate share counted: 5.5 vs 9.6 points up to 10%, 4.7 vs 5.5 (10-30%),
2.3 vs 2.4 (30-50%), 1.5 vs 1.6 (50-70%), 1.4 vs 1.3 (70-90%). The 2017 to 2021 swing was large (CDU/CSU -9 points,
Greens +6) and the cities reported late, so the calibrated sd is wide. On 2025 it is **conservative**: the 90% band
covered the final share in 96.5% of 968 party-minutes (CDU/CSU and AfD 100%, BSW 89%).


## Sensitivity v2: fixed multi-night selection rule

This sensitivity was designed after the 2025 result was known; it is not pre-registered and does not replace the v1
headline. Before running it, the following rule was fixed. Use every earlier German election night for which the
Internet Archive has enough Bundeswahlleiter result pages with `Zwischenergebnis` stamps to replay a count. Give each
night equal weight (and use the real archived reporting order). On the existing `sd_a` grid, choose the smallest value
whose pooled 90% coverage of the final party share over frames with 1--95% of the electorate counted is at least 0.90
and whose worst single-night coverage is at least 0.75. At that `sd_a`, choose the other projection setting from its
existing grid by the lowest mean absolute projection error, averaging the per-night errors so each night has equal
weight; fit `sd_b` to the pooled earlier-night errors and estimate correlations from those errors.

The archive search covers the 2021, 2017 (24 September), and 2013 (22 September) election nights, both bare and `www`
hosts and German/English result paths. The 2021 night is usable. For 2017, CDX returns only one stamped result page
(Wahlkreis 129) during the night, not a replayable sequence. For 2013, CDX returns no result-page captures during the
night. Consequently this fixed rule has only one usable earlier night; v2 tests the common selection rule, but cannot
supply a genuinely multi-night German calibration. Open data retain the Datenlizenz Deutschland - Namensnennung 2.0;
the archived night page has no stated licence, so the site publishes only the capture count and derived series.

The fixed rule selected `kappa = 30,000,000` (lowest earlier-night MAE on the existing grid), `sd_a = 0.20` (2021
coverage 95.4%; `0.15` gave 86.5%), `sd_b = 0.931`, and `sd_floor = 0.0001`; correlations were re-estimated from 2021.
V2 is only 1.5% tighter on 2025 by mean full 90% interval width (4.55 points rather than 4.62). Coverage changed from
96.5% to 96.1% of the 968 party-minutes, while mean absolute projection error worsened from 0.482 to 0.502 points.
Thus the available-night rule does **not** support a materially tighter German model.

| 2025 replay | v1 headline | v2 sensitivity |
|---|---:|---:|
| 90% coverage, all party-minutes at 1--95% counted | 96.5% | 96.1% |
| mean absolute projection error | 0.482 pt | 0.502 pt |
| mean full width of 90% band | 4.62 pt | 4.55 pt |
| Brier, all 46 markets | 0.0426 | 0.0418 |
| Brier, four markets not settled before the count | 0.1402 | 0.1353 |
| markets Polymarket had settled before first count | 42 / 46 | 42 / 46 |

The four genuinely count-contested markets have identical settle times under v1 and v2: CDU/CSU 32--34% no at 22:03
UTC (market 20:38), AfD 20--25% yes at 23:45 (20:44), AfD 20--22% yes at 23:45 (20:46), and BSW 5%+ no at 00:17
(market 00:23, model six minutes first). Among the nine displayed races, only CDU/CSU most changes materially in its
first 95% time (21:40 v1, 21:17 v2); FDP under 5% moves later (first 90% / 95% and settled: 22:10 / 22:33 / 22:22 v1,
22:31 / 23:07 / 23:00 v2). The site retains v1 as the solid headline and draws v2 as a dashed sensitivity.

## Sensitivity v3: poll prior + recalibrated (chosen after the result was known)

**Why.** In v1 the model reached 90% on CDU/CSU-most-seats at 20:36 UTC, after its own raw count had settled (19:38, the first
page). A count model should not be slower than the raw count it projects. Two causes: (1) the prior is the 2021 result
with **no poll information** (2021: SPD 25.7, Union 24.1; 2025 final: Union 28.5, AfD 20.8, SPD 16.4), so the whole night
is spent unlearning 2021, while the market started from 2025 polls (and the 18:00 exit poll, which the model does not use);
(2) the sd was calibrated on one night, a hard one, and was too wide (90% band held 96.5%).

**Rule, written before the calibration was run (`analysis/de_calibrate_v3.py`); it is not changed after seeing results.**

1. *Poll prior.* National Zweitstimmen poll average = the plain mean of every poll with fieldwork ending in the 14 days
   before election day (Wikipedia "Opinion polling for the <year> German federal election", CC BY-SA 4.0, stored in
   `data_public/de_polls_<year>.csv`; wahlrecht.de not used: its terms were not reviewed), parties reported by at least half of the polls, no
   weighting. The previous election's result by Wahlkreis (2025: the 2021 result on the 2025 constituencies; 2021 night:
   2017 on the 2021 constituencies; 2017: 2013 on the 2017 constituencies) is tilted to the poll by **proportional swing per
   party** (every party's share in every Wahlkreis times one national factor, rows renormalised, repeated until the
   vote-weighted national shares equal the poll). BSW has no earlier result: its pattern is `bsw_c (AfD + Linke)` as in v1,
   scaled to its poll. No Land polls are used. The poll's weight fades as the count arrives: the national swing (counted minus
   prior share of the counted votes) is shrunk to zero by `prior_votes` pseudo-votes, `swing * N / (N + prior_votes)`.
2. *Earlier nights (equal weight each).* (A) 2021, the real archived order (151 frames). (B) 2017, **simulated** orders from
   the official 2017 result by Wahlkreis (`btw17_kerg.csv`, Vorperiode 2013): 21 orders (small-first by electorate; 10 noisy
   small-first, sort key log electorate + N(0, 0.5^2); 10 random), a frame after every 10th Wahlkreise, each counted Wahlkreis
   exactly identified (more favourable than the real nights, where only part of the counted Wahlkreise are identified; the
   worst-night condition below guards the real one). The real 2017 and 2013 nights are not replayable (docs above).
3. *Settings.* On a grid, choose `kappa` in {3e5, 1e6, 3e6, 1e7, 3e7} and `prior_votes` in {0, 1e5, 1e6, 1e7} jointly by the lowest mean
   absolute share error (the seven groups that existed on both earlier nights, BSW being zero; frames with 1% < counted <= 95%, nights equal weight, orders equal within night).
   Then `sd_b` = slope of log relative error on log f_rem, pooled (clipped to 0.5 to 1.5), and `sd_a` = the smallest value on the
   grid 0.02, 0.03, ..., 0.30 whose pooled (equal-night) 90% coverage of the final share is >= 0.90 **and** whose worst single-night
   coverage is >= 0.75, then raised to the historical final-poll error if that is larger: the root-mean-square log error of the
   14-day poll average against the official result, 2013, 2017, 2021, parties with >= 3% (`de_polls.py error`), because that is
   all the model knows before the first page. The party sd is `max(floor, sd_a x share x f_rem^sd_b)`, so before the count it is
   the poll error. Correlations: pooled standardised errors, shrunk halfway to the equicorrelation as in v1. `sd_floor`,
   `theta0`, `bsw_c`, `bsw_rho` as v1.
4. *Pre-count frame.* P at poll close (17:00 UTC) is the prior alone with f_rem = 1, no count and no exit poll.

Everything below is a sensitivity: the 2025 result, the polls' error and the question were known when v3 was designed.

**What the rule chose** (`results/germany/calibration_v3.json`; 2021 real order, 143 frames in the window; 2017 simulated, 588):

| setting | v1 | v3 | how |
|---|---:|---:|---|
| prior | 2021 result | 2021 tilted to the 2025 poll mean (n = 25, 10 to 22 Feb: Union 30.0, AfD 20.5, SPD 15.3, Greens 12.9, Linke 6.9, BSW 4.5, FDP 4.3, other 5.6) | proportional swing |
| `prior_votes` | 0 | 10,000,000 | grid {0, 1e5, 1e6, 1e7}, lowest mean absolute error (**grid edge**; at `kappa` 3e7: 1e6 gives 0.00440, 1e7 gives 0.00414) |
| `kappa` | 3,000,000 | 30,000,000 | grid 3e5 to 3e7 (**grid edge**, 3e6 to 3e7 within 1%) |
| `sd_b` | 0.90 | 0.992 | pooled slope |
| `sd_a` | 0.20 | 0.125 | grid rule gave 0.08 (pooled coverage 0.91, worst night 0.84), raised to the poll error 0.1253 (RMS log error of the final-fortnight poll averages 2013 / 2017 / 2021 over 18 party-elections; the largest: AfD 2013 +0.26, Linke 2021 -0.27, Greens 2013 -0.17) |

Which part helped, mean absolute share error of the seven earlier-night groups (frames 1% to 95% counted; recomputed after the
rule was applied): **2021, real order: 0.0083 with the v1 settings and no poll tilt, 0.0088 with the poll tilt and the v1 settings,
0.0047 with the v3 settings**; **2017, simulated orders: 0.0014, 0.0013, 0.0036**. So the gain comes from the heavier weight on
the prior's national level and the stronger Land shrinkage (`prior_votes`, `kappa`), not from the tilt itself, and it is paid
for on the easy simulated night, where v3 is worse than v1's settings; the equal-weight mean (0.0041 against 0.0048) favours it
only because 2021 was so much harder. With `sd_a` 0.125 the earlier nights are covered 94.8% pooled (2021: 89.6%, 2017: 100%).

**2025, v1 against v3** (`results/germany/{v1,v3}/stats.json`; same 46 markets, same prices, same window):

| | v1 | v3 |
|---|---:|---:|
| P before any count (17:00 UTC), Brier over 46 markets (market at 17:00 UTC: 0.0416) | none (first frame 19:38) | 0.0589 |
| CDU/CSU most seats, P at poll close (market 0.975) | none | 0.989 |
| AfD second, P at poll close (market 0.974) | none | 0.902 |
| 90% band held the final share, 968 party-minutes | 96.5% (Linke 95%, BSW 89%) | 95.7% (Linke **65%**, all others 100%) |
| mean absolute share error, 1% to 95% counted | 0.482 pt | 0.426 pt (1-5% counted: 1.30 to 0.87) |
| Brier, all 46 markets (market 0.0032) | 0.0426 | 0.0258 (model better on 15, market on 31) |
| Brier, the 4 contested markets (market 0.0349) | 0.1402 | 0.0779 |
| markets where the model settles (stays at 0.9 on the winner) after its own raw count | 29 of 46 | 17 of 46 |

*The seat-order and "most seats" markets are no longer slower than their own raw count (v1: 5 of them were, by 58 to 127
minutes; v3: none).* The 17 that remain are 15 vote-share brackets (CDU/CSU 8, AfD 5, SPD 2: here the raw count is in
the final bracket early and stays there, while the projection keeps a band around its own estimate) and two thresholds, FDP
5%+ (33 minutes later than its raw count) and Greens over 15% (35 minutes later). CDU/CSU most seats now settles at poll
close (17:00 UTC) instead of 20:43, because the poll prior already puts it at 0.99 (the market 0.975, which has the 18:00
exit poll; no version uses it).

Minutes to 0.90 / 0.95 on the winner (UTC, first minute at the level), the four markets Polymarket had not settled before the
first count information (the decided-before-count markets are excluded):

| market (outcome) | raw count settled | Polymarket 0.90 / settled | v1 0.90 / 0.95 | v3 0.90 / 0.95 |
|---|---|---|---|---|
| CDU/CSU 32-34% (no) | 21:14 | 17:01 / 20:38 | 22:03 / 22:22 | 21:20 / 21:40 |
| AfD 20-25% (yes) | 20:13 | 19:17 / 20:44 | 23:45 / 23:45 | 22:43 / 23:01 |
| AfD 20-22% (yes) | 22:03 | 19:18 / 20:46 | 23:45 / 23:45 | 22:48 / 23:29 |
| BSW 5%+ (no) | 00:17 | 23:03 / 00:23 | 19:38 / 19:59 (not staying) | 20:36 / 00:17 |

The model is still behind the market on the first three (market 0.90 at 17:01, 19:17 and 19:18 UTC, v3 at 21:20, 22:43 and
22:48) and level with it on BSW (+6 min, unchanged). v3 reaches 0.90 earlier than v1 by 43, 62 and 57 minutes on those three.
Over the five contested drawn races the median lead over Polymarket from 1% counted moves from -189 (v1) to -122 minutes (v3)
(`stats.json`, `lead_contested` and `sensitivities.v3`).

**Limits.** (1) The coverage did not fall to 90%: the poll error is the floor of the band, and for the big parties it is wide
compared with what the count needs after a few percent; Die Linke, whose 14-day poll average (6.9%) missed the result (8.8%)
by 0.24 in log terms while its band is a small fraction of a small share, is the one party under-covered (65%). (2) `kappa`
and `prior_votes` sit at the grid edge, so larger values were not tried (the rule was fixed in advance). (3) The 2017
"night" is a simulation with every counted Wahlkreis identified, so its coverage (100%) says little; the correlations
come from 2021 plus those simulated orders: Union and AfD errors came out *positively* correlated (+0.20), unlike v1
(-0.26), which makes "Union beats AfD" more certain. (4) Eighteen poll errors is a small sample for `sd_a`. (5) The
pre-count P is worse than the price at 17:00 UTC (Brier 0.059 against 0.042); the price at 17:00 UTC can already reflect the
18:00 exit poll, and the only poll input to the model is the Wikipedia table average. (6) Everything was
designed after the result was known: a sensitivity.

## Evidence: retrospective, not pre-registered

- The 2025 result, the Polymarket resolutions and the price paths were seen before any config was written. The
  author knew the BSW 4.98% result when writing the BSW judgment (`bsw_c`). V1 settings come from 2021. The v2 rule was
  written above before its calibration run, but both the rule and `configs/de2025_v2.toml` were chosen after the 2025
  result was known and are labelled sensitivity throughout.
- Neither config is registered (`prereg/` has no Germany entry); v1 remains the headline.
- Scoring window: from the first count information (19:38 UTC) to the last national page (00:33:37 UTC, 299 of 299).

## Results (v1 headline; v2 comparison above; outcome = Polymarket resolution, identical to the official final)

Official final: CDU/CSU 28.52%, AfD 20.80%, SPD 16.41%, Greens 11.61%, Die Linke 8.77%, **BSW 4.98%**, FDP 4.33%.

**Polymarket had already settled most markets before any count.** It was at or above 0.9 on the eventual outcome from
poll close (18:00 CET, when the exit poll came out; the exit poll is not an input of the count model) to the end of the
night on **42 of 46 markets**. The model's first information is 19:38 UTC (one Wahlkreis) and 1% of the electorate is
counted at 20:05 UTC.

| race (outcome) | model 90% / 95% (first) | model settled | Polymarket 90% / 95% (first) | Polymarket settled |
|---|---|---|---|---|
| CDU/CSU most seats (yes) | 20:36 / 21:40 | 20:43 | 17:00 / 17:00 | 17:00 |
| AfD second (yes) | 21:45 / 22:03 | 21:45 | 17:00 / 17:00 | 17:00 |
| **BSW 5%+ (no)** | 19:38 / 19:59 | **00:17** | 23:03 / 00:19 | **00:23** |
| FDP 5%+ (no) | 22:10 / 22:33 | 22:22 | 19:13 / 20:02 | 19:13 |
| Die Linke 5%+ (yes) | 19:38 / 19:38 | 20:13 | 17:00 / 17:00 | 17:00 |
| Greens over 15% (no) | 20:00 / 21:00 | 20:45 | 17:00 / 17:03 | 17:00 |
| CDU/CSU 28-30% (yes) | 00:12 / 00:13 | 00:12 | 17:38 / 17:49 | 17:40 |
| AfD 20-22% (yes), contested | 23:45 / 23:45 | 23:45 | 19:18 / 20:08 | 20:46 |
| SPD 15-20% (yes) | 22:22 / 23:07 | 23:07 | 17:05 / 17:30 | 17:05 |

All times UTC (CET = UTC+1), night of 23 to 24 February. "Settled" = stays at 0.9 or more on the outcome to 06:00 UTC.
The "first" columns are the first minute at or above the level, not necessarily staying there.

- **The four markets Polymarket had not settled at the first count information:**
  - BSW 5%+ (resolves no): the model settled first, **+6 min** (00:17 vs 00:23). The model had P(BSW >= 5%) of 0.03 to
    0.18 up to 21:17 UTC when the market was at 0.35 to 0.6, then 0.3 to 0.4 from 21:25 to 23:57 (the projected BSW
    share was 4.6 to 5.0% against the 5% line; the market was at 0.1 to 0.3 from 22:30), and only fell when 99% was counted. Both
    cleared 0.9 within six minutes of each other.
  - AfD 20-25% and 20-22% (yes): the market settled 181 and 179 min before the model.
  - CDU/CSU 32-34% (no): the market settled 85 min before the model.
- **Brier** (model vs Polymarket, minutes from first information to the last national page, by the electorate share
  counted):

  | counted | all 46 markets: model / market | the 4 contested markets: model / market |
  |---|---|---|
  | <1% | 0.093 / 0.007 | 0.256 / 0.072 |
  | 1-5% | 0.086 / 0.006 | 0.260 / 0.068 |
  | 5-10% | 0.077 / 0.005 | 0.235 / 0.054 |
  | 10-25% | 0.073 / 0.004 | 0.216 / 0.041 |
  | 25-50% | 0.053 / 0.005 | 0.182 / 0.052 |
  | 50-75% | 0.032 / 0.003 | 0.119 / 0.032 |
  | 75-95% | 0.012 / 0.001 | 0.058 / 0.012 |
  | 95%+ | 0.0005 / 0.0001 | 0.006 / 0.001 |

  Over all minutes: model 0.0426, market 0.0032, difference +0.039, event-cluster 95% CI +0.018 to +0.064; the market
  was better on 37 of 46 markets, the model on 9. The market scores better everywhere, because it already knew the
  exit poll and the model had no information for the first 2 h 38 min.
- **Projection error** (points, projected minus final, model / raw count, at the first minute with the electorate
  share counted):

  | counted | CDU/CSU share of CDU/CSU + AfD | CDU/CSU | AfD | BSW | FDP |
  |---|---|---|---|---|---|
  | 1% (20:05 UTC) | -2.5 / +2.7 | +1.3 / +10.6 | +3.3 / +4.7 | -1.5 / -1.9 | +1.6 / -1.1 |
  | 5% (20:20) | -1.4 / +0.4 | +0.5 / +4.6 | +1.6 / +3.0 | -1.0 / -0.9 | +1.0 / -0.9 |
  | 10% (20:41) | +1.9 / +1.8 | +2.7 / +4.7 | +0.3 / +1.7 | -1.2 / -1.0 | +0.3 / -0.6 |
  | 25% (21:10) | +1.7 / +1.5 | +2.6 / +3.6 | +0.3 / +1.3 | -0.7 / -0.5 | +0.2 / -0.3 |
  | 50% (22:00) | -0.0 / -0.5 | +0.5 / +1.1 | +0.4 / +1.3 | -0.2 / +0.0 | +0.4 / -0.3 |

  (All eight groups are in `stats.early_error`.) The model beat the raw count clearly up to 10% counted and was about
  equal after that. The first national page (3 of 299 Wahlkreise) overstated CDU/CSU by 10.6 points.
- **Kalshi** had only three markets open through the count (CDU/CSU most seats, AfD most seats, AfD second; 12 to 23
  one-minute candles each) and no usable threshold market (its "party in parliament" markets closed at 15:00 UTC, before
  polls closed). Its line is on the dashboard for those races, not scored.
- **Trading:** no rule was run, so there is no profit result.

## Files

- **Code:** `src/brunder/germany.py` (`poll_prior`, `Params.prior_votes`, `read_btw17_kerg`); `analysis/de_fetch.py`,
  `de_calibrate.py`, `de_calibrate_v2.py`, `de_calibrate_v3.py`, `de_polls.py`, `de_score.py`, `de_export.py`,
  `de_export_v3.py`, `de_geo.py`; `tests/test_germany.py` with fixtures in `tests/fixtures/germany/` (result-page tables
  as captured, rows of `kerg2.csv` and the 2021-on-2025 file). Polls: `data_public/de_polls_{2013,2017,2021,2025}.csv`,
  `de_poll_results.csv`.
- **Configs:** `configs/de2025_v1.toml` (headline), `configs/de2025_v2.toml` and `configs/de2025_v3.toml` (post-result
  sensitivities), not registered.
- **Site:** `site/data/de2025/{meta,timeline,units,stats,entry}.json`, `site/data/geo/de_wahlkreise.topo.json`
  (0.10 MB), an entry in `site/data/elections.json`, the election added to `analysis/site_check.py`.
  - Timeline: one row per minute from poll close (18:00 CET) to 07:00 CET, 9 races. The map has 129 frames, one per
    minute at which a page changed; the last (01:34 CET) is the official final.

## Limitations

- **Pages are sparse.** 15 national pages, gaps up to 69 minutes, 177 of 299 Wahlkreise individually dated. Between
  pages the model holds the last state. The 20:00 to 21:40 UTC pages are all from the evening's first third.
- **Unidentified counted Wahlkreise** are assumed to look like the Land's other unidentified Wahlkreise. In 2021 that
  was not true (small, rural Wahlkreise report first), which is why the calibrated sd is wide.
- **Page stamps are data times,** not publication times. A page may have appeared a few seconds to minutes after its
  stamp; each is used from its stamp, which flatters the model by that lag.
- **The prior's turnout** is the 2021 valid votes per eligible voter, scaled by a national factor (2025 turnout was
  6 points higher; the factor is learned from the count).
- **BSW** has no prior (judgment, see above) and only one election night (2025) to learn from.
- **Seat counts, overhang and compensation seats** are not modelled; the order of the second-vote shares is used.
- **One election** calibrates the sd in both versions because 2017 and 2013 are not replayable. Their 2025 coverage
  near 96% shows both are conservative on this night, not that either is calibrated across German elections.
