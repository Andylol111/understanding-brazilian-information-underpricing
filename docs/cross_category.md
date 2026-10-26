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
   priority (elections > geopolitics > politics > economy > crypto > weather > mentions >
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
