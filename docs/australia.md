# Australia 2025: House of Representatives election (3 May 2025)

**RETROSPECTIVE replay, not pre-registered.** It was built after the election, with the result and the market
resolutions known. It compares a count model with Polymarket and Kalshi, minute by minute, on the night the AEC
published its count. The model is `brunder.australia`, applied to the 150 divisions and to the polling places inside them.
**v1 is the headline.** v2 is a sensitivity, written after v1 had been scored on the 2025 count; see "Order of work".

## The official count over time exists, in full

The Australian Electoral Commission (AEC) publishes an archive of its **election-night media feed**: one zip per
update, every 90 seconds on election night and every 15 minutes after. The archive is listed on the Tally Room page
(`results.aec.gov.au`, "Media feed archive") and sits at `ftp://mediafeedarchive.aec.gov.au/<event id>/`
(anonymous FTP; events 31496 = 2025, 27966 = 2022, 24310 = 2019, 20499 = 2016).

- **Variant used:** `Detailed/Light` (one XML message per update, 21 MB unzipped, 0.7 MB zipped) and
  `Detailed/Preload` (static tables). Detailed means polling-place level. The user guide
  (`results.aec.gov.au` links `media-feed-user-guide-v4-4.pdf`) describes all variants.
- **What a message holds, per division and per polling place:** first-preference votes by candidate, and the
  two-candidate-preferred (TCP) count between the two candidates the AEC picked, each with the time it was published. The
  division also has the national-style two-party-preferred (2PP) count, Labor against the Coalition.
- **What Preload holds:** candidates, parties, and the previous election's votes mapped on to this election's polling
  places and divisions (the AEC's own "Historic" figures), including the **notional 2022 2PP on the 2025 boundaries**
  (the 2024 redistribution moved NSW, Victoria, WA and NT boundaries). That is the model's prior.
- **Time resolution:** 365 messages between 16:59 AEST on 3 May and 04:00 AEST on 4 May 2025 (2025-05-03T06:59Z to
  18:00Z): 40 an hour until about 01:00 AEST, 4 an hour after. Each message is used from its own creation time.
  First two-candidate count: 18:33 AEST (08:33Z); 1% of the expected polling-place votes in at 18:54 AEST.
  Stamps are Canberra local time (AEST, UTC+10 in May).
- **Not in the night feed:** postal, absent and provisional votes arrive in later messages (the archive continues to
  October 2025). The final message (13 Oct 2025) is the "result" used for scoring; it matches all 66 market resolutions.
- **Pre-6pm and "Maverick":** the AEC releases the TCP pair at 18:00. Where its pre-selected pair was wrong the message
  carries `Maverick="true"` and no TCP count. **21 of 150 divisions stayed Maverick all night** (Bean, Blaxland,
  Farrer, Hunter, Newcastle, Richmond, Watson, Brisbane, Griffith, Maranoa, Ryan, Mayo, Franklin, Bendigo, Calwell,
  Flinders, Fraser, Melbourne, Monash, Bullwinkel, Fremantle). The AEC still published their division-level Labor-v-Coalition
  2PP count; the model uses that plus the first-preference leaders (below).
- **Licence:** the AEC applies **Creative Commons Attribution 4.0** to its website material
  (https://www.aec.gov.au/footer/Copyright.htm; exceptions: Coat of Arms, logos, maps, third-party content). The Tally
  Room and media feed pages are AEC website material. Attribution: "(c) Commonwealth of Australia (Australian Electoral
  Commission)". `robots.txt` on `results.aec.gov.au` disallows only a few old polling-place HTML pages; the archive is FTP.
  Requests: anonymous FTP, one file per second at most, from `analysis/au_fetch.py` (User-Agent not applicable to FTP;
  login `anonymous` with a research contact string).
- **Geometry:** `Federal Electoral Divisions (March 2025)`, the AEC's boundaries as republished by Geoscience Australia's
  Digital Atlas of Australia, tagged **CC BY 4.0**; the AEC's own download sits behind a click-through licence form, which
  was not used. Simplified to 620 kB (`analysis/au_geo.py`, mapshaper). Credit: "This product incorporates data that is (c)
  Commonwealth of Australia (Australian Electoral Commission) 2025." Note the AEC's copyright page excludes "AEC's maps"
  from its CC BY grant; the boundary data's own AEC licence permits derivative products with that credit.

## Data on disk ($BRUNDER_DATA = ~/brunder-data-local)

| what | where | size |
|---|---|---|
| AEC 2025 night messages (365 `Detailed/Light`), final message, Preload | `au/31496/Detailed-{Light,Preload}/` | 222 MB |
| AEC 2022 and 2019 night messages, final messages, Preload | `au/27966/`, `au/24310/` | 225 MB, 226 MB |
| AEC 2016 final message and Preload (for 2019's prior and 2016's swing) | `au/20499/` | 3 MB |
| per-frame statistics (`analysis/au_observe.py`) | `au/obs_{2025,2022,2019}.pkl` | 15 MB each |
| Polymarket: 13 events, 1-minute YES prices 04:00 UTC 3 May to 00:00 UTC 6 May | `raw/polymarket/<event>/` | 2 MB |
| Kalshi: 8 events, 1-minute candles | `raw/kalshi/australia/` | tiny |
| geometry source | `raw/australia/geo/` | 160 MB |

```
export BRUNDER_DATA=~/brunder-data-local
uv run python analysis/au_fetch.py get 31496 Detailed/Preload
uv run python analysis/au_fetch.py get 31496 Detailed/Light --since 20250503165000 --until 20250504040000
uv run python analysis/au_fetch.py get 31496 Detailed/Light --since 20251013133229      # final message
# the same for 27966 (2022: 20220521165000..20220522040000), 24310 (2019: 20190518165000..20190519040000),
# and the last Detailed/Light + Preload of 20499 (2016)
uv run python analysis/au_fetch.py markets                                # Polymarket + Kalshi
for y in 2025 2022 2019; do uv run python analysis/au_observe.py $y; done
uv run python analysis/au_calibrate.py 2019 --out results/australia/calibration_2022_check.json --check 2022
uv run python analysis/au_calibrate.py 2022 2019 --out results/australia/calibration_2025_v1.json
uv run python analysis/au_calibrate.py 2022 2019 --variant 2 --out results/australia/calibration_2025_v2.json
uv run python analysis/au_config.py results/australia/calibration_2025_v1.json    # configs/au2025_v1.toml
uv run python analysis/au_config.py results/australia/calibration_2025_v2.json
AU_OUT=results/australia/v1 uv run python analysis/au_score.py configs/au2025_v1.toml
AU_OUT=results/australia/v2 uv run python analysis/au_score.py configs/au2025_v2.toml
uv run python analysis/au_export.py && uv run python analysis/decided_stats.py au2025
uv run python analysis/mix_au.py run && uv run python analysis/mix_au.py export && uv run python analysis/decided_stats.py au2025   # tau mixture sensitivity
```

## Markets that existed during the count

Polymarket (13 events; volume in USD) and Kalshi (8 events; contracts). Everything below was open on the night.

| event | markets | volume | modelled? |
|---|---|---|---|
| Next Prime Minister of Australia (`next-prime-minister-of-australia`) | Albanese, Dutton, Chalmers, Plibersek | $8.6M | Albanese, Dutton: as "the party with the most seats wins the prime ministership". Chalmers, Plibersek: no |
| Next Government of Australia | Labor majority / minority, Coalition majority / minority, other | $3.4M | majority markets (76 seats). Minorities depend on bargaining: not modelled |
| Parliamentary election winner (most seats) | Labor, Coalition, Greens, other | $4.0M | yes (+ Kalshi `KXAUSTRALIA`, 5 markets, 163k contracts) |
| Most primary votes | Labor / Coalition | $1.2M | **no** (the model projects seats, not national first-preference totals) |
| Seat margin of victory | 10 bands of Labor minus Coalition seats | $0.55M | yes |
| # seats Labor / Coalition win | 6 and 8 bands | $0.14M, $0.40M | yes (+ Kalshi `KXAUSSEATSLABOR-25`, `KXAUSSEATSLIBNAT-25`) |
| Hung parliament | yes/no | $0.12M | yes |
| Greens 4+ seats | yes/no | $0.09M | yes (+ Kalshi `KXAUSSEATSGREENS-25`) |
| Dutton loses his seat / Albanese loses his seat | yes/no each | $0.23M, $0.17M | yes (+ Kalshi `KXAUSSEATDUTTON-25`, `KXAUSSEATALBANESE-25`) |
| Seat of Bennelong / Brisbane | 8 candidates each | $0.27M, $0.14M | Labor, Liberal/LNP, Greens only (+ Kalshi; the minor candidates have no model) |

Sixty-six markets in 41 questions ("races") are scored (`configs/au2025_v1.toml`).

## Model

Everything is in `src/brunder/australia.py`; `analysis/au_observe.py` reduces every feed message to per-division
statistics; `analysis/au_score.py` projects, simulates and scores.

1. **Contest:** the AEC's two-candidate pair per division (e.g. Labor v Liberal, Labor v Greens, Liberal v an independent).
   Candidates are matched across elections by party group (the Coalition is one group: Liberal, National, LNP, CLP) and, for
   independents, surname.
2. **Prior:** for Labor-v-Coalition divisions, the AEC's notional 2022 2PP on the 2025 boundaries. For other pairs, the previous
   election's polling-place TCP counts in the same pair, weighted by each place's previous votes (needs 40% of the weight).
   Otherwise 0.5 with a wide error.
3. **Swing of a division:** over the polling places already counted, the votes for the first-listed candidate minus what the same places
   gave last time (previous election's final polling-place TCP, matched by polling place id). This is the AEC's own "matched
   places" idea: big and small, rural and city places do not bias the swing.
4. **Pooling:** the division swing is pulled towards its state's swing and the country's, with `K` pseudo-votes
   (`K_d`, `K_s`, `K_n`); only Labor-v-Coalition divisions feed the state and national swing.
5. **Seat draws:** 8,000 joint draws of national, state and division errors, then the winner of each division is the
   candidate ahead on the TCP count. The winner's party group (Labor, Coalition, Greens, others) gives seat counts; each market is a rule
   on those counts (`market_hit`).
6. **Maverick divisions** (no TCP count): the division's Labor-v-Coalition 2PP count decides between those two, and a
   logistic rule on first preferences (`m_a`, `m_b`) lets a third candidate (Greens, an independent) overtake. The same
   rule is fitted on 2019 and 2022.
7. **Not modelled:** who forms government in a hung parliament, minority and other-formation markets, other prime-minister
   names, the national first-preference vote, small-party seat outcomes, the Speaker, and postal/absent vote timing
   beyond what the swing absorbs.

## Settings: chosen on 2022 and 2019, replayed in feed order

`analysis/au_calibrate.py` replays the 2022 and 2019 count nights from the same AEC archive (368 and 365 messages) and, on
the messages with 2% to 95% of the expected polling-place votes counted, scores division shares (normal log score by kind of
evidence), the winner of every division, Labor's seat count, and the Brier score of the seat-count events the 2025 markets
use (most seats, majority, hung, seat bands, margin bands). Coordinate search on small grids, two sweeps.

- `s_nat0` (national swing prior sd) = root-mean-square of the national 2PP swings of 2016, 2019 and 2022 (+3.1, -1.2,
  +3.7 points; AEC notional start vs final) = 2.87 points. For the 2022 check only 2016 and 2019 (2.37).
- v1 settings (fitted on 2022 + 2019): `K_d` 1500, `K_s` 1000, `K_n` 8000 votes; `s_d0` 0.035, `s_floor` 0.008, `s_st0` 0.012;
  `fb` 0.1 (weight of a swing measured against the division prior rather than matched places); `m_a` -1, `m_b` 14.
  Several values sit on the lower edge of the grid.
- **Out-of-sample check of the method:** settings fitted on 2019 alone, then scored on 2022 (not used in the fit): Labor's final
  seat count inside the 90% range in 53% of messages (in sample: 100%), division-share 90% ranges held 91% (matched places).
- **No 2025 count** entered any v1 or v2 setting.

## Order of work (what was seen when)

1. Data, parser, model structure and a smoke test with untuned default settings on the 2025 messages (the early-night seat
   estimate looked right; nothing was tuned on it). The Maverick rule was added after seeing which divisions the 2025
   feed flagged all night; its two parameters are fitted on 2019 and 2022.
2. Settings fitted on 2022 + 2019: **v1**. Scored once on 2025 against both platforms (this is the headline).
3. v1's 90% ranges covered the final result in only 10% of minutes. The misses were inspected (a third-candidate rule that
   gave independents too many wins in safe Labor divisions; no floor under the state and national swing errors, so the model
   grew sure as counted votes piled up). **v2** fixes both, with settings again fitted only on 2022 + 2019. It was written
   *after* seeing the 2025 outcome and is published as a sensitivity.

## Results (2025; v1 unless stated)

- **Which markets count:** 41 questions, 66 markets. **17 questions were already at 0.9 or more on the eventual outcome
  when polls closed** (and never fell below 0.8): most seats (all four Polymarket outcomes, Kalshi too), the prime-minister
  markets (Albanese 0.954 at close), and others. **The $8.6M next-prime-minister market was decided before the count.**
  They are excluded from the headline below.
- **24 contested questions** (seat and margin bands, majority, hung parliament, Dutton's seat, Bennelong and Brisbane).
  The model's probability reached 0.9 on the outcome before the market's on **21**, and was never second on any, by a median of
  **109 minutes** (gated at 1% counted; platform = Polymarket where the question has one). Three contested questions have no
  usable comparison (the model or the market never reached and held 0.9: Brisbane Labor/Greens, Greens 4+).
- **Probability scores** (Brier, minutes from the first TCP count to 02:37 AEST): on the contested markets, model 0.053 against
  market 0.064 (difference -0.011, 95% CI -0.048 to +0.062, bootstrap over events: not significant); on all 66 markets 0.031 against
  0.038 (CI -0.028 to +0.036). v2: 0.048 / 0.064 and 0.028 / 0.038.
- **The caution that matters: the model was overconfident.** Over the messages with 1% to 95% counted, Labor's final seat
  count (94) was inside the model's 90% range **10% of the time** (v1; the model had 89 to 93 seats with sd about 2) and its final
  national 2PP (55.22%) inside it 16% of the time (the model sat 0.4 to 0.7 points above the final count all night). Floors in v2 raise
  these to 79% and 56%. The "model first" lead therefore overstates how early a calibrated model would have been sure; it is the
  time the model's central estimate crossed 0.9, not a calibrated claim. In the first 5% of the count the model's national 2PP
  was 0.8 points under the final and its Labor seat count 8 seats under.
- **Where the model was wrong** (at 90% counted): Goldstein (model 93% for the independent; the Liberal won by 0.2 points),
  Melbourne (model 92% Greens; Labor won), Ryan (model 36% Greens; the Greens held it), and the Maverick divisions as a group
  (the model's central seat count was 89.6 against 94).
- **What was priced when:** Polymarket's Labor 85+ seats market was 0.10 at 20:00 AEST, first reached 0.9 at 21:59 and stayed above it
  from 22:10 AEST; the model was above 0.9 from 19:36 AEST. The most-seats and prime-minister markets were at 0.95 to 0.97 from poll close (exit polls and polls, not the count).

## Tau mixture (retrospective sensitivity)

A third model line, written after the 2025 result was known (label "tau mixture over earlier nights (chosen after the result was known)"). It is a sensitivity, never the headline. `analysis/mix_au.py`.

**What it is.** The model's error that does not shrink as polling places report is the national swing floor `s_nat_f`: in v1 it is 0, which is why the band narrows too fast. The mixture asks each earlier count night what that floor had to be: the smallest value on the grid (0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5 points; `nowcast_order.TAU_GRID`) at which that night's own 90% range of Labor's national two-party share held the AEC's final share on at least 90% of its frames (1% to 95% counted), with every other v1 setting unchanged. **2019 needed 0.50 points** (v1's 90% range held in 20% of its frames), **2022 needed 0.75 points** (46%). 2025 is then run twice, once with each floor, with the same random numbers as v1; each market's probability is the plain average of the two runs (equal weights, which is the same as pooling their 8,000 + 8,000 seat draws). The two-party share band is the 5%-95% range of the equal-weight mix of the two normal bands. Nothing from 2025 chose a floor; only two earlier nights exist in the archive, so the mixture has two components. Output: `$BRUNDER_DATA/results/mix/au/`.

| 2025, minutes 1% to 95% counted | v1 | v2 | tau mixture |
|---|---:|---:|---:|
| Labor's final two-party share (55.22%) inside the 90% range | 13% | 65% | 99.8% |
| mean width of that range | 0.9 pt | 1.4 pt | 2.4 pt |
| Labor's final seat count (94) inside the 90% range (frames, as the v1 headline) | 10% | 79% | 18% |
| contested questions where the model got to 0.9 first / lead (median, gated at 1% counted) | 21 of 21 / 109 min | 21 of 21 / 109 min | 21 of 21 / 109 min |

(v1 two-party row computed from the published v1 timeline with the same rule as the other two; the v1 seat and lead entries are the published v1 headline numbers. v2 and mixture rows are `stats.sensitivities.{v2,mix}` in `site/data/au2025/stats.json`.) The median lead to 0.95 is 126 minutes for v2 and 106 for the mixture.

**Reading it.** The mixture fixes the national two-party range (it now holds the final share almost always) but barely helps the seat count (18%, v1 10%, v2 79%). v2 changes other things (a floor under the state swing errors and a different third-candidate rule; its national floor is 0) and lifted the seat range to 79%; a wider national swing alone, which is what the mixture adds, did not. The earlier leads survive (the same 109 minutes, model first on all 21). Caveat: it was built after seeing the result, and with two earlier nights the mixture has only two components, not a distribution.

## Files

`src/brunder/australia.py`, `analysis/au_{fetch,observe,calibrate,config,score,export,geo}.py`, `analysis/mix_au.py`, `configs/au2025_v{1,2}.toml`,
`tests/test_australia.py`, `tests/test_mix_au_hu.py`, `site/data/au2025/`, `site/data/geo/au_{divisions,regions}.topo.json`.
