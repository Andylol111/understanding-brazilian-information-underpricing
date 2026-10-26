# Cross-category program: where does Polymarket under-weight information?

The Brazil runoff showed one market pricing public information too slowly. The election
exploration then showed that the same kind of signal behaves differently by market: big moves
revert in thin election markets and hold in deep ones. This program measures each category
separately, under rules designed so that a positive result survives an out-of-sample test.

## The three signatures of under-weighted information

| Study (`prereg/`) | What is measured | Under-weighting looks like | Over-reaction looks like |
|---|---|---|---|
| `shock_depth_v1` | after the first ≥10-pt one-hour move, drift s·(y − p_entry) to resolution, split by taker $ traded in the 7 days *before* the move | deep markets keep moving the same way (drift > 0) | thin markets revert (drift < 0) |
| `favourite_tilt_v1` | at E−1d / E−6h / E−1h, excess return y − p_entry against p − ½ | slope > 0: favourites win more than priced | slope < 0: favourite–longshot bias |
| `late_certainty_v1` | buy the side priced 80–97¢ at E−6h (else E−1h), all-in cost | net return per $ > 0 | net return < 0 |

E is the market's scheduled information time: game start for sports, event start time where
Polymarket publishes one, otherwise the scheduled end date. Never the close time, which is when
resolution happened.

## Rules against look-ahead and over-fitting

1. **Universe sampled ex ante.** For each category and ISO week (2024-01 → now), every closed event
   ending that week is listed; each event gets one primary category from its own tags by a fixed
   priority (elections > mentions > geopolitics > politics > economy > crypto > weather >
   tech-science > business > sports > culture); up to 12 events per category-week are drawn by a
   seed derived from (category, week), and up to 20 resolved binary markets per event. No volume
   filter: lifetime volume is only known after the fact.
2. **Point-in-time prices.** Every feature is the last price at or before the signal time
   (`pit.asof`). Verified against trades: a prices-history point at t tracks the last trade ≤ t, not
   later trades (`brunder verify`, results below).
3. **Execution after the signal.** Entries fill at the price 5 minutes after the signal, never at
   the price that defined it, and only if the market was still open then.
4. **Costs per market.** Entry cost = price + 1¢ half-spread + that market's own taker fee
   rate × q(1 − q) from its `feeSchedule` (0 where fees were disabled).
5. **Depth known at entry.** Depth is taker notional in the 7 days before the signal, from
   data-api trades, not lifetime volume.
6. **Embargoed time split.** CUTOFF = 2026-04-01. An event is *discovery* only if every market in it
   closed before the cutoff, and *holdout* only if every market's scheduled time is on or after it.
   Events straddling the cutoff are in neither.
7. **Pre-registration and a locked holdout.** Specs were committed and registered (hash + commit in
   `prereg/registry.jsonl`) before any run on this universe. The holdout split refuses to open for
   an unregistered or edited spec, and every look is logged in `prereg/holdout_runs.jsonl`.
8. **Multiple testing.** Within each spec, every category × test is one hypothesis; p-values are
   Holm-adjusted across all of them (Benjamini–Hochberg q also reported).
9. **Dependence.** Markets in the same event are not independent; all intervals and p-values come
   from an event-level cluster bootstrap (2,000 draws).
10. **Label hygiene.** Markets whose price after close contradicts the recorded outcome are flagged
    and dropped from every study.
11. **Executability.** Holdout runs check each entry against actual trades: how much taker notional
    traded on the same side at our limit or better within 30 minutes.

Known limits: Gamma's scheduled dates can be edited after the fact (`hasReviewedDates`), which this
pipeline cannot audit. Prices-history is mid/last-price data, not the order book; executability
comes from trades, not quotes. The **elections** holdout is contaminated: the exploration that
produced `shock_depth_v1` looked at election markets from every period.

Change after registration (data rule, before any study was run): every feature reads the price
5 minutes before the signal (`pit.OBS_LAG`), on top of the 5-minute execution delay, because the
trade check below could not tell which side of t a price point summarises.

Erratum in `favourite_tilt_v1.toml` (left unedited so its registered hash stays valid): the spec says
a negative slope is "the classic favourite–longshot bias". It is the reverse. The classic bias
(long shots over-priced) gives a **positive** slope, the same sign as under-weighted information,
so this study cannot separate the two explanations.

## Universe and verification (run 2026-10-26)

- 11,147 events sampled across 11 categories (business is small: 117 events), 69,828 resolved binary
  markets; 57,853 with a price path, 11,975 never traded. 0 label conflicts among 57,579 checked.
- Split: discovery = events fully closed before 2026-04-01; holdout = events scheduled on/after it.
- Price semantics (`results/verification/price_semantics.csv`, 113 markets ≥ $50k): median absolute
  gap between a price point and the last trade before it is 0.05–2.5¢ by category, about the same
  as to the first trade after it. Hence the observation lag above.

## Results

Holdout = first and only look (`prereg/holdout_runs.jsonl`). Holm-adjusted within each spec.
Figures: `results/studies/fig_<spec>.png`.

### Thin markets over-react (shock_depth_v1, thin_reversal)

After the first ≥10-pt one-hour move in a market that traded < $10k in the previous 7 days, the
price moves back toward the other side before resolution.

| category | discovery drift | holdout drift [95% CI] | holdout Holm p |
|---|---|---|---|
| mentions | −4.9 pt ✓ | −7.6 [−9.0, −6.2] | 0.007 ✓ |
| politics | −3.1 pt ✓ | −7.2 [−9.1, −5.3] | 0.007 ✓ |
| economy | −5.0 pt ✓ | −6.6 [−9.7, −3.6] | 0.007 ✓ |
| geopolitics | −4.4 pt | −8.1 [−12.7, −3.0] | 0.007 ✓ |
| tech-science | +1.4 pt | −5.5 [−8.2, −3.0] | 0.007 ✓ |
| elections (holdout contaminated) | −9.2 pt ✓ | −5.2 [−9.2, −1.2] | 0.036 ✓ |
| culture | −4.8 pt ✓ | −2.4 [−4.7, −0.2] | 0.105 |
| weather | −3.1 pt ✓ | −2.4 [−4.6, −0.3] | 0.076 |
| crypto | −6.3 pt ✓ | −6.9 [−16.4, +2.2] | 0.45 |
| sports | −7.2 pt ✓ | −1.1 [−3.4, +1.0] | 0.50 |

✓ = Holm-significant in that split. Every tested category has a negative holdout estimate; 6 of 10
are significant after correction. This is the most robust finding in the program.

**Deep markets do not under-react** (deep_continuation): no category is significant in either
split. Most deep cells hold 30–90 shocks, so they are under-powered. The Brazil-style "right
direction, too slow" pattern is not a systematic, detectable property of deep markets here.

### Mentions markets under-price near-certain outcomes

| study | category | discovery | holdout [95% CI] | holdout Holm p |
|---|---|---|---|---|
| favourite_tilt_v1 | mentions | +0.031 (p = 0.059) | +0.102 [+0.066, +0.138] | 0.005 ✓ |
| favourite_tilt_v1 | economy | +0.037 | +0.107 [+0.034, +0.176] | 0.014 ✓ |
| late_certainty_v1 | mentions | +1.2% (p = 0.073) | +2.1% per $ [+0.6, +3.6] | 0.027 ✓ |
| late_certainty_v1 | economy | +1.8% | +4.4% per $ [+0.7, +7.6] | 0.108 |

Mentions ("what will X say") is the one category that shows the same direction in all three studies:
favourites under-priced, 80–97¢ sides 6h/1h before the event returning +2.1% per $ net of spread
and fee, and thin-market spikes reverting. All other favourite-tilt and late-certainty cells are
not significant.

### Executability: the edges are real in prices but very small in dollars

For each holdout entry, taker notional that traded on the same side at the entry limit or better
within 30 minutes (`fills.csv`):

| study | category | entries | share with ≥ $100 fillable | total fillable |
|---|---|---|---|---|
| late_certainty_v1 | mentions | 1,851 | 0.6% | $8.3k |
| shock_depth_v1 (fade thin) | mentions | 4,734 | 0.4% | $7.8k |
| shock_depth_v1 (fade thin) | geopolitics | 342 | 4.7% | $147k |
| shock_depth_v1 (fade thin) | politics | 1,779 | 2.5% | $27k |

This counts only *other takers* who traded at our price; resting liquidity no one took is invisible
in trades, so it is a lower bound. Even so, the reversal shows up exactly where few dollars
trade, which is consistent with thin-market noise. Net of spread and fee, fading thin moves earned
more than its cost in the holdout only in mentions (+7.5% per $, [+1.8, +13.0]) and geopolitics
(+21% [+2.5, +40]); it lost in culture, sports and weather.

## What this supports

- A cross-category, out-of-sample result: **thin Polymarket markets over-react to large moves and
  mean-revert**, in 6 of 10 categories after correction, with the same sign in all 10.
- A category-specific tradable candidate: **mentions markets**, where near-certain sides are
  under-priced by about 2% per $ net of costs, but fillable volume in the 30 minutes after entry was
  tiny.
- Not supported: a general under-reaction in deep markets. The Brazil count-night lag is a case
  study, not a pattern this sample can detect.

Next: order-book snapshots (not trades) for executable size, a fresh holdout (events after
2026-10-26) for mentions with a new spec id, and a power analysis for deep_continuation before
spending another look on it.
