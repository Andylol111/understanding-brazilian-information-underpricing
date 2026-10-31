# Australia 2025: is the model's lead real, or thin markets and contract caution?

**RETROSPECTIVE, not pre-registered.** Question: the model was first to 0.90 on 21 of 24 contested markets (median +109 min; the
same under the v2 and tau-mixture sensitivities). Is that an informational lead, or an artefact of thin, stale markets and of
contract conditions? Everything below is computed by `analysis/au_lead.py` from `site/data/au2025/{timeline,meta,stats,units}.json`
plus public Polymarket data fetched for the 24 contested markets (gamma market metadata, data-api taker trades, one request
per second, cached under `/tmp/au_lead_cache`, nothing committed). Times are AEST (UTC+10) unless marked Z. The count window is the
464 minutes from 18:54 (1% counted, the same gate as the headline) to 02:37 (last published frame).

## Verdict

**A real lead in the deepest markets, much smaller than the headline. The large leads come from thin, stale markets and
near-edge seat bands. The market's caution was justified for the first ~10% of the count and not after.**

| claim | evidence |
|---|---|
| The lead exists where there is trading | The 8 contested markets with at least $5,000 of taker volume in the window: model first on all 8, median lead **82 min** (Labor majority 52, Dutton loses 77, Coalition under 45 seats 266). Taker-dollar-weighted median lead over all 21: **52 min**. |
| The 109-minute median is lifted by thin markets | The 7 markets with under $1,000 of taker volume: median lead **174 min**. 9 of 24 contested markets traded under $1,000 in the whole window; the median market had 30 taker trades, $1,937, and a price change in 130 of 464 minutes. Lnc 60-64 seats: 4 trades, $35, price flat for 223 minutes, "lead" 189. Brisbane LNP: 7 trades, $203, "lead" 472. |
| The market did not react to the model's call for an hour | When the model first held 0.90 on the right outcome, Polymarket's price on that outcome was **0.69 (median)**, then 0.71 / 0.73 / 0.73 at +15 / +30 / +60 min and 0.95 at +120. 76% / 81% / 67% / 38% of the 21 markets were still under 0.90 at +15 / +30 / +60 / +120. The market reached 0.90 at a median **48% of the count in**, the model at 8%. |
| But the model's early calls were not safe | v1 at 0.90 or more on one side, one call per market and count bin: right in **10 of 14** markets at 0-5% counted (71%; Polymarket priced the called side at 0.73 on average), **12 of 15** at 5-10% (80%; price 0.67), 21 of 24 at 10-25% (88%; price 0.59), 21 of 24 at 25-50% (88%; 0.71), 21 of 22 at 50%+ (95%; 0.93). Below 10% counted the discount was about right; from 10% to 50% the market was 0.17 to 0.29 too low. The first 0.90 call after the gate was on the wrong side in 5 of 24 markets (4 for the mixture). |
| The market also knew things the model did not | At the last frame (02:37) the market priced the eventual outcome at 0.955 or more on all 24 markets. The model was at 0.29 (v1; 0.53 v2) on Greens 4+ seats (a Maverick-seat question) and 0.85 / 0.85 on Brisbane Labor / Greens. Brier at 02:37: model 0.023 (v1; 0.017 v2), Polymarket 0.0003. |
| Lead-lag: the market does not follow the model minute by minute, and the model does not follow the market | See (2). Within 30 min the market moves only at lag 1 (+0.07 per unit of model change) and then not at all; over 120 min it closes **72%** of the gap to the model, while the model itself moves **30%** of the way toward the market. |
| Contract caution is partly real | Seat-band markets whose final result sat near an edge settled latest (Spearman -0.64 between distance to the edge and lead over 17 band markets). Coalition 43 seats against the 45 edge: 1.5 seats, both Coalition band markets resolved on 26 May. Late-count drift to the Coalition exists but is small (below). |
| The shape of the market's catch-up looks like waiting for a call | 6 of 21 market crossings fall between 21:58 and 22:10 AEST (Labor and Coalition seat bands and margin bands), a cluster, not a drift. [INFERENCE] a media/AEC projection around then; there is no news feed in the repo to check. |

So: genuine lead in deep markets (about 50 to 80 min, mostly after the first 10% of the count); thin or stale markets for the
long tail of leads (7 of 21 markets under $1,000, median 174 min); contract and tail caution on near-edge bands and Maverick seats.
Effective sample is smaller than 24: the Labor and Coalition band markets are all functions of the same two seat totals.

## (1) Price when the model crossed 0.90, and what happened next

`model >= 0.90` is the headline definition: the first minute at or after the 1% gate from which the model's probability of the
eventual outcome stays at 0.90 or more to the end of the night (the same rule gives the market's time). Prices are
Polymarket's one-minute history on the eventual outcome. v1, v2 and the mixture give the same 21 / 0 / median 109. At other
thresholds (v1): 0.80 median 88 (n 23; market first on 1), 0.95 median 130, 0.99 median 185 (n 17); v2 88 / 126 / 196; mixture 85 / 106 / 179.
The lead grows with the threshold, so the market's last few cents are the slowest.

Three contested markets have no lead: Brisbane Labor and Greens (the model was on the wrong side for 75 minutes, then never held 0.90) and
Greens 4+ (the model was on the wrong side, YES, from 19:51 for 38 minutes, then never held 0.90 on NO). Polymarket's official
resolution time is gamma's `closedTime`; no market names the AEC final declaration (13 Oct 2025) as its trigger, all closed
between 12.5 hours and 555 hours after poll close.

| market | outcome | model >= 0.90 (AEST) | Polymarket price then | +15 min | +30 | +60 | +120 | market >= 0.90 (AEST) | lead (min) | Polymarket resolved (h after close) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| alp-majority | yes | 19:00 | 0.85 | 0.93 | 0.89 | 0.95 | 0.99 | 19:52 | 52 | 245.3 |
| grn-4plus | no | - | - | - | - | - | - | 23:05 | - | 102.5 |
| hung-parliament | no | 19:00 | 0.79 | 0.90 | 0.91 | 0.91 | 0.99 | 19:13 | 13 | 21.2 |
| alp-seats-70-74 | no | 18:59 | 0.84 | 0.84 | 0.95 | 0.94 | 0.97 | 19:22 | 23 | 14.0 |
| alp-seats-75-79 | no | 19:11 | 0.36 | 0.32 | 0.34 | 0.44 | 0.74 | 22:05 | 174 | 28.7 |
| alp-seats-80-84 | no | 19:36 | 0.76 | 0.78 | 0.76 | 0.72 | 0.73 | 23:55 | 259 | 99.4 |
| alp-seats-85plus | yes | 19:36 | 0.10 | 0.10 | 0.10 | 0.42 | 0.73 | 22:10 | 154 | 101.6 |
| lnc-seats-u45 | yes | 21:23 | 0.52 | 0.54 | 0.56 | 0.77 | 0.95 | 01:49 | 266 | 555.2 |
| lnc-seats-45-49 | no | 21:23 | 0.60 | 0.59 | 0.60 | 0.81 | 0.95 | 22:38 | 75 | 555.5 |
| lnc-seats-50-54 | no | 19:38 | 0.70 | 0.70 | 0.70 | 0.68 | 0.89 | 21:58 | 140 | 168.5 |
| lnc-seats-55-59 | no | 19:12 | 0.69 | 0.70 | 0.70 | 0.66 | 0.62 | 21:27 | 135 | 67.5 |
| lnc-seats-60-64 | no | 19:00 | 0.72 | 0.72 | 0.72 | 0.73 | 0.76 | 22:09 | 189 | 31.3 |
| lnc-seats-65-69 | no | 18:54 | 0.71 | 0.71 | 0.76 | 0.78 | 0.97 | 20:43 | 109 | 14.1 |
| seatmargin-alp40 | yes | 20:02 | 0.20 | 0.17 | 0.22 | 0.60 | 0.88 | 22:08 | 126 | 167.2 |
| seatmargin-alp30 | no | 20:02 | 0.51 | 0.57 | 0.57 | 0.60 | 0.93 | 22:02 | 120 | 164.7 |
| seatmargin-alp20 | no | 19:24 | 0.32 | 0.47 | 0.53 | 0.54 | 0.98 | 20:50 | 86 | 93.2 |
| seatmargin-alp10 | no | 19:03 | 0.63 | 0.72 | 0.74 | 0.94 | 0.97 | 19:58 | 55 | 34.3 |
| seatmargin-alp1 | no | 18:56 | 0.86 | 0.91 | 0.86 | 0.96 | 0.98 | 19:35 | 39 | 14.3 |
| dutton-loses | yes | 19:24 | 0.51 | 0.40 | 0.74 | 0.70 | 1.00 | 20:41 | 77 | 12.5 |
| bennelong-alp | yes | 19:36 | 0.61 | 0.94 | 0.94 | 0.96 | 0.99 | 19:50 | 14 | 21.3 |
| bennelong-lnc | no | 19:36 | 0.76 | 0.96 | 0.95 | 0.97 | 1.00 | 19:49 | 13 | 22.0 |
| brisbane-alp | yes | - | - | - | - | - | - | 22:28 | - | 21.4 |
| brisbane-grn | no | - | - | - | - | - | - | 22:29 | - | 22.0 |
| brisbane-lnc | no | 18:54 | 0.86 | 0.87 | 0.56 | 0.52 | 0.53 | 02:46 | 472 | 22.1 |

How to read it: in `alp-seats-85plus` the market sat at 0.10 for 30 minutes after the model had 0.90 on YES (0.42 at +60), and in
`seatmargin-alp40` at 0.17 to 0.22 (0.60 at +60); those are not threshold artefacts. In `lnc-seats-50-54`, `55-59` and `60-64` the price on NO
was 0.66 to 0.73 and flat for an hour after the model said NO: with 4 to 26 trades all night these look like stale quotes (the minute history repeating the
last trade or a wide quote [INFERENCE]), not a view. In `alp-majority` and `hung-parliament` the market was already 0.85 / 0.79 at the crossing and closed to 0.90 in 52 / 13 min: a real but
small lead. `brisbane-lnc` fell from 0.87 to 0.52 on NO after the model's crossing, on 7 trades.

## (2) Lead-lag

Pooled over all 24 contested markets, minutes from the 1% gate to 02:37 (10,392 market-minutes), market fixed effects (within
transform), 95% intervals from 2,000 bootstrap resamples of markets. Sum of the 30 lag coefficients (a model change of 1.0 moves the market by that much over the following 30 minutes):

| model | market on lagged model changes (lags 1-30) | active minutes only | model on lagged market changes (lags 1-30) | active minutes only |
|---|---|---|---|---|
| v1 | +0.092 [+0.006, +0.223] | +0.023 [-0.067, +0.124] | -0.189 [-0.302, -0.081] | -0.241 [-0.352, -0.130] |
| v2 | +0.094 [-0.001, +0.225] | +0.021 [-0.076, +0.115] | -0.180 [-0.280, -0.083] | -0.230 [-0.326, -0.134] |
| mixture | +0.090 [+0.005, +0.224] | +0.029 [-0.060, +0.120] | -0.184 [-0.293, -0.083] | -0.229 [-0.338, -0.126] |

Active minutes are those where the model or the market sat inside 0.02 to 0.98 a minute earlier. Within-market R-squared is 1%.
v1 individual lags, model to market: lag 1 +0.073, lag 2 -0.004, lag 3 -0.003, lag 5 -0.013, lag 10 +0.005, lag 20 -0.002, lag 30 +0.004.
The contemporaneous slope is +0.015 [-0.002, +0.047]. Adding own lags (joint regression) gives +0.115 [+0.025, +0.258] and -0.168 [-0.306, -0.048] (v1).
On a 5-minute grid with lags to 120 minutes the sum is +0.74 [-0.14, +3.99] model to market and -0.04 [-0.13, +0.02] market to model:
too unstable to read (collinear lags, a few large moves).

**Reading:** at the minute scale the market moves in the minute after a model change and in no later minute, and with a tiny
coefficient; with the dead minutes removed there is no significant response at all. The reverse sign is negative and significant in every
specification. The model reads only the AEC feed, so a negative coefficient cannot be the model learning from the market; it means the model's
changes partly reverse after the market moves (count batches that overshoot). This is a common-news pattern, not evidence that
the market leads at this scale, and not evidence that the model does. Neither direction is a clean test of who is informed, because
both series respond to the same public count.

A levels test is more informative. With gap = model P minus Polymarket price (YES side), `b` is the share of the gap the market
closes within h minutes and `c` the share the model closes by moving toward the market (same fixed effects and bootstrap):

| h (min) | v1 market closes gap (b) | v1 model moves to market (c) | v2 b / c | mixture b / c |
|---:|---|---|---|---|
| 15 | 0.147 [0.115, 0.188] | 0.028 [-0.024, 0.084] | 0.153 / 0.021 | 0.147 / 0.024 |
| 30 | 0.292 [0.232, 0.360] | 0.098 [-0.002, 0.205] | 0.305 / 0.080 | 0.294 / 0.088 |
| 60 | 0.505 [0.428, 0.589] | 0.233 [0.043, 0.426] | 0.534 / 0.179 | 0.511 / 0.219 |
| 120 | 0.719 [0.578, 0.854] | 0.303 [0.079, 0.535] | 0.772 / 0.234 | 0.729 / 0.282 |

So the market is slow (half the gap in an hour; half-life about 1 hour) and the model was the source of most of the
convergence, but 23% to 30% of each gap at 60 to 120 minutes closed because the **model retreated** toward the market: the model's early
extremes, which the 10% coverage of its 90% range already flags, were partly wrong.

## (3) Activity in the count window

Taker trades from the Polymarket data API (`takerOnly`, poll close 18:00 to 04:00; the counts after the 18:54 gate equal `trades_read` in `stats.json` profit_model_limit for all 24 markets),
minutes with a price change from the timeline (18:54 to 02:37), and the longest run of an unchanged price in that window.

| market | taker trades | taker $ | minutes with a price change (of 464) | longest flat run (min) | trades in lead window | count in at market >= 0.90 | final seats vs band edge | lead (min) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| alp-majority | 549 | 340,294 | 126 | 87 | 174 | 19% | 18.5 | 52 |
| grn-4plus | 100 | 6,951 | 248 | 42 | - | 76% | 2.5 | - |
| hung-parliament | 44 | 9,734 | 115 | 50 | 4 | 4% | 18.5 | 13 |
| alp-seats-70-74 | 12 | 8,681 | 63 | 30 | 2 | 7% | 19.5 | 23 |
| alp-seats-75-79 | 20 | 290 | 120 | 29 | 18 | 61% | 14.5 | 174 |
| alp-seats-80-84 | 29 | 654 | 191 | 44 | 25 | 84% | 9.5 | 259 |
| alp-seats-85plus | 86 | 7,684 | 242 | 45 | 34 | 61% | 9.5 | 154 |
| lnc-seats-u45 | 80 | 13,166 | 218 | 25 | 54 | 94% | 1.5 | 266 |
| lnc-seats-45-49 | 55 | 4,014 | 170 | 52 | 18 | 69% | 1.5 | 75 |
| lnc-seats-50-54 | 26 | 4,466 | 50 | 81 | 5 | 58% | 6.5 | 140 |
| lnc-seats-55-59 | 21 | 603 | 177 | 23 | 4 | 48% | 11.5 | 135 |
| lnc-seats-60-64 | 4 | 35 | 20 | 223 | 1 | 61% | 16.5 | 189 |
| lnc-seats-65-69 | 32 | 1,115 | 169 | 57 | 4 | 39% | 21.5 | 109 |
| seatmargin-alp40 | 60 | 18,092 | 215 | 56 | 11 | 61% | 11.5 | 126 |
| seatmargin-alp30 | 61 | 1,009 | 266 | 18 | 24 | 60% | 11.5 | 120 |
| seatmargin-alp20 | 44 | 6,474 | 93 | 221 | 10 | 40% | 21.5 | 86 |
| seatmargin-alp10 | 26 | 902 | 135 | 60 | 7 | 22% | 31.5 | 55 |
| seatmargin-alp1 | 38 | 2,461 | 139 | 182 | 13 | 12% | 41.5 | 39 |
| dutton-loses | 126 | 40,986 | 95 | 112 | 81 | 38% | - | 77 |
| bennelong-alp | 28 | 1,412 | 96 | 70 | 3 | 19% | - | 14 |
| bennelong-lnc | 13 | 250 | 82 | 70 | 2 | 18% | - | 13 |
| brisbane-alp | 12 | 389 | 115 | 30 | - | 67% | - | - |
| brisbane-grn | 10 | 643 | 170 | 33 | - | 68% | - | - |
| brisbane-lnc | 7 | 203 | 47 | 128 | 7 | 95% | - | 472 |

- **Essentially no trading** (fewer than 10 taker trades all night): `lnc-seats-60-64` (4 trades, $35) and `brisbane-lnc` (7, $203); `brisbane-grn` has 10, `brisbane-alp` 12, `alp-seats-70-74` 12, `bennelong-lnc` 13. Nine markets traded under $1,000.
- 10 of 24 markets had a flat price for 60 minutes or more (`lnc-60-64` 223, `seatmargin-alp20` 221, `alp1` 182, `brisbane-lnc` 128, `dutton` 112, `alp-majority` 87, `lnc-50-54` 81, `bennelong` 70 / 70, `alp10` 60).
- Every one of the 21 leads has at least one taker trade inside its lead window (fewest: `lnc-60-64`, 1 trade of $8), so none is a pure no-trade artefact; 6 markets have fewer than 25 trades and a median lead of 154 min against 86 min for the 15 with at least 25.
- Without the two markets under 10 trades the median lead is 86 min (n 19).

## (4) Contract conditions and the end of the night

**Resolution (Polymarket gamma metadata, all 24).** No market lists a `resolutionSource`; the rules say:

| markets | trigger in the rules | closed |
|---|---|---|
| seat-count bands (Labor, Coalition), margin bands, hung parliament, Greens 4+ | "consensus of credible reporting"; if ambiguous, official AEC results; if unknown by 31 Dec 2025 the lowest bracket | 3 May 22:02Z to 26 May 11:30Z (14 h to 555 h after close) |
| Labor majority | the first party that forms the government (PM appointed by the Governor-General, ministers sworn in), not seat count alone | 13 May (245 h) |
| Dutton loses his seat | credible reporting, AEC if ambiguous | 3 May 20:28Z (12.5 h after close, 06:28 on 4 May) |
| Bennelong, Brisbane (Labor, Liberal, Greens, LNP) | party of the winner; "official information from the Government of Australia, a consensus of credible reporting also used" | 4 May 05:16Z to 06:07Z (21 to 22 h) |

All closed months before the AEC's last message (13 Oct 2025), so a market's price at 0.99 was a bet on the reporting consensus, not on the AEC's final
declaration; and Labor majority carried government-formation risk the model does not price. The `endDate` fields are placeholders (3 May 12:00Z; Dutton 17 May).
Kalshi rule text is not in the repo and was not fetched.

**Why near-edge bands were slow.** Final seats: Labor 94, Coalition 43, Greens 1, others 12 (AEC final message). Distance of the result from the
nearest band edge (seats, 0.5 = just inside): Coalition under 45 and 45-49 both 1.5; Labor 80-84 and 85+ 9.5; Greens 4+ 2.5; Labor 70-79 14.5 to 19.5; margin bands 11.5 to 41.5.
Spearman correlation of distance with lead over the 17 band markets: **-0.64** (the nearer the edge, the longer the market
took). The model's seat sd was about 2 (its 90% range held the final Labor seat count 10% of the time), so 1.5 seats is inside its
own noise yet it sat at 0.9+ on both Coalition bands from 21:23; the market's caution there was reasonable, and both closed on 26 May, the latest of all.

**Postal and late-count drift.** Last published frame (02:37; 94.97% of expected polling-place votes) against the AEC's final result:

- National Labor two-party-preferred: raw count 55.37%, model 55.51%, final **55.22%**. The count moved 0.15 points toward the Coalition after the night; the model sat 0.29 above the final.
- Division by division (109 Labor-v-Coalition divisions, 21 Maverick divisions excluded because the AEC's 2PP is not their final two-candidate count): final Labor share minus the share already counted: median **-0.30 points**, vote-weighted -0.33 (74 of 109 toward the Coalition, 35 toward Labor; sign test p = 0.0002). Divisions under 95% counted drifted -0.65, the rest -0.27.
- It flipped almost nothing: two divisions where Labor led at 02:37 went to the Coalition (Longman 50.2% to 49.9%; Casey 50.2% to 47.1%, only 78% counted). The model's projected winner was wrong only in Longman (50.1%). Model projection minus final: +0.17 points vote-weighted, mean absolute error 0.60.
- Maverick divisions (21) were where the seat count surprised: final winners 15 Labor, 4 Coalition, 1 Greens, 1 other. v2's early seat error was -15.3 seats at 1% counted, -8.4 at 5%, -6.5 at 10% (`stats.sensitivity_v2.early_error`); v1's central estimate was 89.6 against 94.

Postal drift toward the Coalition is real and statistically clear but about 0.3 points: at that size it flips a division only if it was within a few tenths of a point, which the data show for two
divisions (Longman, Casey). The caution the market showed at the end of the night was justified only for Maverick-seat questions (Greens 4+, Brisbane: model 0.29 to 0.85, market 0.96 to 0.99 on the right side) and the Coalition 45 edge.

## (5) The $10,000 results: +0.3% at the signal price, +21.5% at the model limit

`stats.invest10k.signal_price`: the frozen rule (favoured side, edge at least 5c after costs, 1% counted, first signal per market) filled **3 bets of 25 signals**: $525.70
deployed, ending $10,033.59 (+0.34%). `stats.invest10k` (model limit: 10% of every real taker trade on the favoured side whose all-in price leaves the model at least 5c
of edge, model P known at least 60 s earlier, any price, no per-market cap): **235 bets in 23 markets, ending $12,145.76 (+21.46%)**.

**Why the signal-price rule barely trades.** It only fills taker trades at or better than the signal price between 60 s and 30 min after the signal. A replication on the 24 contested
markets (`signal_fills`: first minute where model P minus price minus 1c half-spread is at least 5c; it reproduces the three fills and the $468.31 Labor-majority stake) gives 24
signals: **3 with a fill, 10 with no taker trade on our side at all in the window, 11 with trades only at a worse price**. The signal price of a thin market is a stale quote. Examples: `lnc-seats-u45` YES signalled at 0.006, the first trades were 0.014 or higher; `dutton-loses` YES at 0.52, trades on our side from 0.62; `seatmargin-alp40` YES at 0.065 and `alp-seats-85plus` YES at 0.10, no trades; `bennelong-lnc` NO at 0.645, trades at 0.99.
The fills that happened: Labor majority YES (+$76.24 on $468.31), `seatmargin-alp1` NO (+$1.94), and Greens 4+ YES (-$44.58, the model's wrong early call).

**What drove the model-limit result** (`stats.invest10k.by_race`, profit = 10% of each eligible trade):

| market | stake | profit | share of the +$2,146 | first edge trade (side, price; model P) |
|---|---:|---:|---:|---|
| Coalition wins under 45 seats | $236 | **+$945** | 44% | YES at 0.014; 0.58 |
| Dutton loses his seat | $1,515 | **+$461** | 21% | YES at 0.62; 0.95 |
| Labor majority | $5,248 | **+$446** | 21% | YES at 0.84; 0.93 |
| Coalition 50-54 seats | $239 | +$72 | 3% | NO at 0.75; 1.00 |
| Labor margin 20-29 | $162 | +$60 | 3% | NO at 0.59; 0.95 |
| Labor 85+ seats | $88 | +$47 | 2% | YES at 0.14; 1.00 |
| 15 other markets with a gain | | +$168 | 8% | |
| Greens win 4+ seats (slug `...norwegian-election-328`) | $50 | -$50 | | YES at 0.13; 0.52 |
| Greens win Brisbane | $4 | -$4 | | YES at 0.55; 0.96 |

The top 3 markets are **86%** of the profit and the top 5 are 92%. Without the top 3 the model-limit result is +$294 (+2.9% of $10,000), close to the signal-price +0.3%; without the single Coalition-under-45
market, +12%. Labor majority carries 61% of the eligible volume ($52,483 of $85,439) but only 21% of the profit (buying at 0.84 to 0.93 for a result at 1.0). The Coalition-under-45 profit is a long-shot: the model said 0.58 while takers sold YES at 1.4 cents; that bet won
because the final count was 43 seats; the Greens 4+ and Greens Brisbane bets were the model's early wrong calls and lost. In the model-limit run the good fills are the ones the signal-price rule cannot get: trades at 0.014 (u45), 0.62 (Dutton) and 0.84 to 0.93 (majority) while the model was far above them.

## What this does and does not show

- The model's lead is **not a threshold artefact**: the gap is visible in prices at +15, +30 and +60 minutes after the crossing, and holds when thin markets are removed (82 min in the 8 deepest markets).
- It **is** inflated by thin markets, stale quotes and near-edge bands; the headline median should be quoted with the dollar-weighted 52 min and the deep-market 82 min.
- It is **not** a tradable lead before about 10% of the count: the model's 0.90 calls were right 71% (0-5%) and 80% (5-10%) of the time, against prices of 0.73 and 0.67.
- It cannot be separated from the market waiting for a public call (the 22:00 AEST cluster) with the data in the repo.
- The model and the market used the same public count; none of this is private information.
- Caveats: one election, 24 markets with correlated outcomes; the model was built after the result was known; Polymarket's minute history probably repeats stale quotes on thin markets [INFERENCE: not checked against the order book]; the `stats.json` trade counts come from the same API.

## Reproduce

```
uv run python analysis/au_lead.py fetch        # Polymarket gamma metadata + taker trades for the 24 contested markets (~2 min, 1 req/s)
uv run python analysis/au_lead.py run --boot 2000   # all tables above (about 5 min); the per-market markdown tables are printed at the end
```
