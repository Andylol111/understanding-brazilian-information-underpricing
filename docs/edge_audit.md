# Edge audit: does the count model hold, and is there money in it?

One method, applied to every election the project can score: Brazil 2026 first round (`br2026r1`),
runoff (`br2026r2`), six governor runoffs (`br2026r2gov`), Brazil 2022 first round and runoff
(`br2022r1`, `br2022r2`; model only, no prices survive) and Israel 2026 (`il2026`).
Script: `analysis/edge_audit.py`. Tables: `results/edge_audit/`. Site: an `accuracy` and a `profit` block in
each `site/data/<eid>/stats.json` (schema at the end). Mexico 2024 (`mx2024`, not-preregistered: config
committed after scoring, one exploratory look at 2024 before freezing) is scored with the same functions by
`analysis/mx_score.py` (`results/mexico/mx2024_v1/score.json`), and Hungary 2026 (`hu2026`,
not-preregistered: settings frozen from the 2022 count after the 2026 market resolutions had been seen, then
scored once) by `analysis/hu_score.py` (`results/hungary/stats.json`); their numbers are quoted here but not
produced by this script.

Evidence labels: **in-sample** = settings were chosen on this night (Brazil first round);
**pre-specified** = settings frozen and registered before the count data were examined, outcome already
known (Brazil runoff and governors); **not-preregistered** = no frozen config or trading rule existed before
scoring (Israel, Mexico, Hungary; the Brazil rule is applied unchanged); **model-only** = no market prices
(Brazil 2022).

## Summary

**1. Does the model's projection hold? Mostly yes in Brazil; less so in Israel; not in Mexico; in Hungary
the shares and intervals held but P(win) was far worse than the market.**

- **Projected shares.** Early in the count the projection's error is about half the raw count's in Brazil
  and Israel; Mexico is the exception at 1% counted
  (mean absolute error across races, percentage points):

  | | races | 1% counted | 5% | 10% | 25% | 50% |
  |---|---|---|---|---|---|---|
  | Brazil runoff (pre-specified) | 28 | 2.89 vs raw 5.75 | 2.24 vs 4.61 | 1.97 vs 3.76 | 1.38 vs 2.52 | 0.59 vs 1.50 |
  | Brazil governors (pre-specified) | 6 | 1.69 vs 2.21 | 1.37 vs 2.18 | 1.19 vs 1.80 | 0.96 vs 1.99 | 0.71 vs 1.64 |
  | Brazil first round (in-sample) | 28 | 3.59 vs 5.41 | 1.75 vs 3.39 | 1.60 vs 3.17 | 1.10 vs 2.52 | 0.65 vs 1.63 |
  | Brazil 2022 R1 / R2 (model-only) | 1 / 1 | 2.00 vs 6.75 / 0.76 vs 7.50 | 0.41 / 0.36 | 0.24 / 0.00 | 0.23 / 0.19 | 0.25 / 0.31 |
  | Israel lists (not-preregistered)¹ | 13 lists | 0.81 vs 1.90 | 0.45 vs 1.98 | 0.60 vs 1.53 | 0.45 vs 1.06 | 0.30 vs 0.50 |
  | Mexico 2024 (not-preregistered)⁴ | 1 | 1.86 vs 0.29 | 0.43 vs 1.56 | 0.59 vs 2.53 | 1.51 vs 2.90 | 1.55 vs 2.22 |
  | Hungary 2026 list share (not-preregistered)⁵ | 1 | 10.20 vs 10.56 | 3.83 vs 3.87 | 2.91 vs 2.83 | 1.60 vs 1.04 | 0.39 vs 0.56 |
  | Hungary 2026 seats (not-preregistered)⁵ | 1 | 2.23 vs 6.64 | 2.23 vs 6.64 | 0.66 vs 3.97 | 0.13 vs 2.12 | 0.14 vs 1.48 |

  ¹ first release at or above each threshold (the first one past 1% was at 2.5%); against the provisional count.
  ⁴ `analysis/mx_score.py`; at 1% counted the raw count was closer than the model.
  ⁵ `analysis/hu_score.py`; absolute errors (the list projection added nothing over the raw count early on).
- **Uncertainty.** Share of minutes (1–95% counted) whose 90% interval contains the final share,
  minutes pooled across races: runoff **96%** (2 of 28 races below 80%: Amapá 38%, Maranhão 68%), governors
  **99.6%**, 2022 **100%** (both rounds). The first round, where the settings were chosen, covers only
  **85%** (8 of 28 races below 80%), and **64%** between 90% and 95% counted: the late-count
  overconfidence already documented in `docs/edge_tool.md`. Israel list shares: **76%** (195 list ×
  release pairs, 15 releases), i.e. the Israel intervals were too narrow. Mexico 2024: **10%** (mean z
  +3.3; 14% before 90% counted): the projection stayed biased about 1.5 points while its interval shrank, a
  clear calibration failure on a new country. Mexico adds no P(win) minutes (model and
  market were both ≥ 0.9 before 1% was counted). Hungary 2026: list share **99.8%**, seats **100%**.
- **P(win) against the market, same minutes** (from 1% counted until both had settled ≥ 0.9; Brier score,
  lower is better; 95% interval from a bootstrap over races):

  | | races | minutes | model | market | model − market |
  |---|---|---|---|---|---|
  | Brazil runoff + governors (pre-specified) | 19 | 1,932 | 0.0337 | 0.0468 | −0.013 [−0.046, +0.016] |
  | Brazil first round (in-sample) | 10 | 1,099 | 0.0444 | 0.0615 | −0.017 [−0.041, +0.0002] |
  | all Brazil 2026 | 29 | 3,031 | 0.0376 | 0.0521 | −0.015 [−0.036, +0.006]; log score −0.065 [−0.125, −0.003] |
  | Israel (not-preregistered) | 4 | 211 | 0.0699 | 0.0126 | **+0.057 [+0.013, +0.113]** (market better) |
  | Hungary, all 39 markets (not-preregistered)⁵ | 39 | 50,141 | 0.0794 | 0.0072 | **+0.072 [+0.032, +0.119]** (market better) |

  In Brazil the model scored better than the market in 17 of 29 races, but the Brier difference is
  within noise; only the log score over all three Brazil nights (one of them in-sample) excludes zero. In
  Israel and Hungary (model better in 15 of 39 markets) the market was clearly better. Reliability
  (pre-specified Brazil, minutes pooled): when the model
  said ≥ 0.9 the favourite won in all 1,124 minutes (10 races); when the market priced 0.6–0.9 the
  favourite also won every time (639 minutes, up to 8 races), i.e. the market was under-confident during
  the count, which is the lag the project is about. The model's middle bins (0.4–0.8) rest on 2–3 races
  each and are not informative.

**2. Is there real, executable profit after costs? At the signal price, very little; none that is
statistically distinguishable from luck on the pre-specified nights, and a large loss on Hungary. Under the
looser "model limit" execution (below, defined after the outcomes were known) the Brazil runoff's national
market alone offered $3.1M of trades the model priced ≥ 5¢ too cheap; everything else is small.**

Frozen rule (`night.evaluate`, favoured side only, edge ≥ 5¢ after costs, ≥ 1% of the market counted,
first signal per market, hold to resolution). Headline execution: Brazil, real taker trades on our side at
or better than the signal price from 60 s to 30 min after the signal (no order books were recorded for
Brazil; Hungary likewise from real trades); Israel, a limit-order walk of the recorded order book 60 s after
the signal.

| | evidence | signals (won) | $ that could be bought | P&L | per $ | from the largest trade | p if prices were fair² |
|---|---|---|---|---|---|---|---|
| Brazil first round | in-sample | 7 (5) | $2,729 | **+$3,411** | +125% | 99.8% (national market) | 0.34 |
| Brazil runoff | pre-specified | 12 (11) | **$0** | $0 | – | – | – |
| Brazil governors | pre-specified | 3 (3) | **$0** ($2.40 with zero latency) | $0 | – | – | – |
| Israel | not-preregistered | 3 (3)³ | $22,089 | **+$2,631** | +11.9% | 59% | 0.68 |
| Mexico 2024 (`mx_score.py`) | not-preregistered | 0 | $0 | $0 | – | – | – |
| Hungary 2026 (`hu_score.py`) | not-preregistered | 23 (13) | $78,364 | **−$16,618** | −21% | −$21,530 in one market⁶ | 0.89 |

² exact probability of at least this P&L if every filled trade had won with probability equal to the
all-in price paid. ³ all three in the same event (Likud's seat band), so effectively one bet; outcome is
the provisional count (markets not yet resolved). ⁶ "Tisza ≥ 130 seats" bought NO at 18:00 UTC with model
P(YES) 0.006; it resolved YES. Without it +$4,913; bootstrap over markets [−$64,368, +$12,748].

- **At the signal price the pre-specified nights offered nothing**: on the runoff the minute price history
  lagged the trades by 2–4¢, so no taker trade happened at or below the signal price, at any size.
- **Accepting 5¢ worse** (which still satisfies the 5¢ edge rule for most pre-specified signals): $2,207
  could be bought across the runoff and governors after a 60 s latency, P&L **+$3,719** (bootstrap over
  markets [+$31, +$9,344]; p = 0.016 under fair prices), 62% of it from the national runoff market. This is
  the only result with p < 0.05, it rests on one national market plus a Minas Gerais long shot (signal price
  4.6¢, filled at up to 9.6¢), and the
  pre-registration said no edge claim would rest on these replays (`docs/edge_tool.md`).
- **First round**: the reported $4,111 (+2¢) and $6,906 (+5¢) are mostly **not** trades the rule would
  take: the national signal had a 6.4¢ edge, so 2¢ worse leaves 4.4¢ < 5¢. Rule-consistent fills at +5¢:
  $163 (P&L +$118).
- **Israel** is the only night with real size and a gain: the recorded book offered $22k at prices still
  leaving ≥ 5¢ of model edge one minute after the signal (28k with zero latency; 24k at 30 s), and $25 to
  $1,000 per signal were fillable on all three signals. But the return was +12% on near-certain sides (paid
  0.85–0.93), the bets are one event, not pre-registered, against a provisional outcome, and a gain this
  size would happen 68% of the time if the prices paid were fair. The same night the market's P(win)
  beat the model's.
- **Hungary** had size too ($78k fillable after 60 s at the signal price, 23 signals) and lost: 13 of 23
  won, −$16,618 (−21% per $). The loss comes from one overconfident tail call (model P 0.006 on "Tisza ≥ 130
  seats", which happened; a near-identical 130-seat market lost another $1,396); the other 22 signals made
  +$4,913. Small sizes lost too ($25 per signal: −$144 on $325).
- **Maximum total profit available** (all fills, no size cap), most generous definition per night: first
  round $6,972 (+5¢, zero latency, mostly rule-breaking), runoff + governors $6,512 (+5¢, zero latency),
  Israel $3,708 (zero latency); Hungary $1,225 (+2¢, zero latency, 5-minute window; every 30-minute window
  loses).

**What each answer depends on.** Few independent bets. Brazil and Israel: 25 signals, only 6 separate
events (the two Brazil national markets, three governor races, Israel's Likud seat band), the other 17 are
state markets moving with the same national swing. Hungary adds 23 signals on one election. Brazil fills
assume we could have taken the
liquidity other takers took (an upper bound for one trader; no book was recorded). The Israel outcome is
provisional. Every interval treats a market as the unit; with 3–15 units they are wide, and the bootstrap
cannot show downside when every sampled bet won (Israel), which is why the exact fair-price p-value is
reported beside it.

### $10,000 in each election

Two execution models, the same for every election with trades or recorded books. The headline (`invest10k`)
is **model limit**; the stricter **signal price** model is kept beside it (`invest10k.signal_price`).

**Model limit** (added on 2026-10-28 at the integrator's request, after every outcome here was known, so it
is a post-hoc execution model, not a pre-registered one). From the 1% gate on, any real taker trade on the side
the model favours (Israel: any resting ask in the recorded book, one snapshot per release, taken ≥ 60 s after
it) is eligible when the model's probability for that side, as known ≥ 60 s earlier, exceeds the trade's
all-in price (incl. taker fee) by ≥ 5¢. **$10,000 at poll close takes 10% of each eligible trade in time order
until cash runs out, no per-market cap, held to resolution; ending value = cash + payouts.** Variants: all of
each eligible trade (`first`), a $1,000-per-market cap (`_cap`), 10¢ / 20¢ edge, 300 s delay. "Eligible volume"
is everything that qualified: an upper bound for one trader, since other traders took those trades.

| | evidence | eligible volume, 5¢ / 60 s | its P&L | $10,000 → (10% of each trade) | all of each trade | with $1,000/market cap (10%) | signal price (old headline) |
|---|---|---|---|---|---|---|---|
| Brazil first round | in-sample | $143,228 | +$52,005 | **$14,763** | $21,643 | $11,215 | $11,282 |
| Brazil runoff | pre-specified | $3,107,751 | +$892,007 | **$20,762** | $21,905 | $11,637 | $10,000 |
| Brazil governors | pre-specified | $695 | +$319 | **$10,032** | $10,319 | $10,032 | $10,000 |
| Israel | not-preregistered | $123,421 (book depth) | +$9,786 | **$10,852** | $11,581 | $10,341 | $10,425 |
| Hungary | not-preregistered | $614,795 | +$52,748 | **$11,011** | $10,365 | $11,218 | $7,989 |
| Mexico | not-preregistered | $0 | $0 | **$10,000** | $10,000 | $10,000 | $10,000 |
| **all six** | | | | **$77,420 of $60,000 (+29.0%)** | | | $59,695 (−0.5%) |

- **Concentration.** 99% of the runoff's eligible volume, and 99.8% of its bankroll gain, is the national
  "Lula wins" market; 98.5% of the first round's is its national market; the governors' is all one market
  (Rio Grande do Norte); Hungary's largest market holds 38% of its eligible volume. **Each election is one
  event**: all of its markets ride on the same count, so the six elections are six outcomes, and +29% pooled
  rests mainly on one market (the runoff national, +$10,737 of the runoff's +$10,762 and of the +$17,420 pooled gain) and on the in-sample
  first round (+$4,763).
- **Sensitivity.** Runoff: 10¢ edge $20,762, 20¢ $20,829; 300 s delay $16,554 (5¢), $16,599 (20¢). First
  round: 10¢ / 300 s, 10% → $10,925. Hungary turns negative at 20¢ (eligible P&L −$14,923; all-of-each-trade
  bankroll $325): the high-edge trades there are the model's overconfident tail calls ("Tisza ≥ 130 seats").
  Israel has no eligible depth at 20¢ and $8,717 at 10¢.
- **Reconciliation with the integrator's quick check** (runoff $21,876 / $20,660 / $16,649 for all / 10% /
  10¢-300 s-10%): here $21,905 / $20,762 / $16,554. The check used only the cached 2-hour trade windows after
  each first signal; this audit reads every taker trade of every market from the gate to the end of the night
  (archive or data API, `results/edge_audit/trades/`), so eligible runoff volume is $3.11M here vs $2.19M.
  First round (check $24,882 / $16,716 / $11,905; here $21,643 / $14,763 / $10,925): the two cached
  national-market files overlap (entries at 20:19 and 20:28), so reading both counts 2,328 of their 4,874
  rows twice; the 4% taker fee of the first-round markets, included here, accounts for a few hundred dollars
  more (without it: $22,065 / $15,007 / $11,186).

**Signal price** (the earlier headline): each signal of the frozen rule stakes min($1,000, cash left, the real
taker trades at or better than the signal minute's price from 60 s to 30 min after it; Israel: the recorded
book 60 s after the signal at the average cost of a $1,000 limit walk) and is held to resolution:

| | evidence | bets (won) | deployed | ending value | return |
|---|---|---|---|---|---|
| Brazil first round | in-sample | 3 (2) | $1,058 | **$11,282** | +12.8% (99% of the gain from the national market) |
| Brazil runoff | pre-specified | 0 of 12 signals fillable | $0 | $10,000 | 0% |
| Brazil governors | pre-specified | 0 of 3 signals fillable | $0 | $10,000 | 0% |
| Israel | not-preregistered | 3 (3), one event | $3,000 | $10,425 | +4.25% (provisional outcome) |
| Hungary | not-preregistered | 13 (6) of 23 signals | $6,624 | $7,989 | −20.1% |
| Mexico | not-preregistered | 0 | $0 | $10,000 | 0% |
| **all six** | | 19 (11) | $10,682 | **$59,695 of $60,000** | **−0.5%** |

The two models differ because the minute price history lags real trades: at the runoff's signal minute takers
were already paying 2–4¢ more than the minute price, so "signal price" fills nothing, while many later trades
were still priced ≥ 5¢ below the model's probability, which "model limit" counts.

## Method

Inputs are cached outputs only; no registered replay is rerun (`analysis/night_replay.py`,
`analysis/history_review.py`).

| input | used for |
|---|---|
| `results/night/<cid>/ticks.parquet` (1-minute: price, P, all-in costs, edges, counted, national share/sd) | signals, P(win) scoring, national share |
| `results/night/<cid>/{evaluation.csv, final.json, tally.parquet}` | outcomes, final share, raw national tally |
| `results/export_brazil/*.pkl` (per-state projected share, sd, raw share; `analysis/export_brazil.py`) | state-race share error and coverage |
| `results/night/<cid>/capacity_trades/*.parquet` (Polymarket taker trades, 2 h after each signal) | Brazil fills |
| `~/brunder-stage/*/raw/polymarket/*/markets.parquet` | YES token of each Brazil market |
| `results/history/<cid>/{ticks.parquet, summary.json}` | Brazil 2022 |
| `results/israel/{releases.csv, shares.csv, settle.csv, ticks.parquet}` (`analysis/il_score.py`) | Israel model P per release, list shares, settle times |
| `$BRUNDER_DATA/raw/snap/israel_2026/polymarket_books/*.json` (recorded every 30 s) | Israel book walks |
| Polymarket Gamma `feeSchedule` (cached in `results/edge_audit/il_fees.json`) | Israel taker fees (all 0 for these 12 markets) |

**Accuracy.** Share error at the first minute with ≥ 1/5/10/25/50% counted (race's own counted fraction
for state markets), model and raw count minus the final two-way share. Coverage: |z| < 1.645 with
z = (final − share)/sd over 1–95% counted, split at 90%. These reproduce every `calibration` and
`early_error` value already in the stats files exactly. P(win): Brier and log score (probabilities clipped
to [0.001, 0.999]) on minutes with ≥ 1% of the race counted until the later of model and market first
staying ≥ 0.9 on the winner (whole night if either never settles), so the comparison covers the period in
which the race was open for at least one of them; races already decided for both before 1% counted
contribute nothing. The difference is pooled over minutes; its interval resamples races (2,000 draws).
Reliability: ten equal P bins, minutes pooled, with the number of races in each bin. Israel: model P is the
latest release's; seat-market outcomes and list shares are against the provisional count.

**Profit.** Signals: `night.evaluate(..., favoured_only=True)` on the cached 1-minute ticks with each
config's `[trade]` (min_edge 0.05, min_counted 0.01; costs = price + 1¢ half-spread + fee_rate·p(1−p),
fee 0.04 on the first-round markets, 0 on the runoff/governor markets). For Israel, the same rule is
evaluated at each recorded book snapshot after a release with ≥ 1% of boxes counted, with the cost of
walking a $100 order (the live engine's definition).

- Brazil fills: taker trades long our side at a YES-equivalent price at or better than the limit
  (`night_capacity.same_side_fills`), in [signal + latency, signal + horizon); limit = the signal minute's
  price, +2¢, +5¢; latency 0 or 60 s; horizon 5 or 30 min. Return per $ at the signal's all-in cost plus the
  slippage, held to resolution. A variant `_rule` keeps a slipped fill only if the model edge at the
  slipped cost is still ≥ 5¢.
- Israel fills: walk the first recorded book at or after signal + 0/30/60 s as a limit order (only levels
  whose all-in unit cost ≤ P(favoured side) − 5¢), each size walked separately, fee from the market's
  feeSchedule.
- Per size ($25, $100, $500, $1,000, $5,000 per signal, and `max` = everything available): signals
  fillable in full, $ deployed (Σ min(size, available)), P&L, return per $, bootstrap over signals (10,000
  draws; reported when ≥ 2 signals filled), exact fair-price p-value, the single largest trade's share of
  the P&L and the P&L without it. Hit rate with a Clopper–Pearson interval.

Per-size tables, Brazil, headline execution (60 s, 30 min, signal price):

| size | first round: fillable / P&L | runoff | governors |
|---|---|---|---|
| $25 | 2 of 7 / +$32 | 0 of 12 / $0 | 0 of 3 / $0 |
| $100 | 1 / +$135 | 0 / $0 | 0 / $0 |
| $500 | 1 / +$645 | 0 / $0 | 0 / $0 |
| $1,000 | 1 / +$1,282 | 0 / $0 | 0 / $0 |
| $5,000 | 0 / +$3,411 ($2,729 deployed) | 0 / $0 | 0 / $0 |

At +5¢ (60 s, 30 min), runoff + governors pooled: $25: 5 of 15 fillable, +$275; $100: 2, +$1,022;
$500: 1, +$2,028; $1,000: 1, +$2,659; $5,000: 0, +$3,719 ($2,207 deployed).

Israel (book walk at +60 s): $25, $100, $500, $1,000: 3 of 3 fillable, +$11, +$44, +$216, +$425
(+14–15% per $); $5,000: 2 of 3, +$1,520 on $11,809; everything: +$2,631 on $22,089.

## Sanity checks against the reported numbers

| | reported (signal / +2¢ / +5¢, 30 min) | recomputed here (zero latency) |
|---|---|---|
| first round, 7 signals | $2,741 / $4,111 / $6,906 | $2,740.75 / $4,111.30 / $6,906.22 |
| runoff presidential, 12 | $0 / $100 / $4,375 | $0 / $99.55 / $4,375.22 |
| governors, 3 | $2 / $5 / $45 | $2.40 / $4.95 / $45.18 |

The signals (market, side, minute, price) are identical to those in the capacity runs, and P&L at
$500 per signal reproduces `docs/edge_tool.md` ($645 / $510 / $555; $0 / $15 / $1,996; $2 / $4 / $36).
Mean return per $ at the quote on the 1-minute ticks: first round +17.4% (the documented 1-minute
figure), runoff 11 of 12 won, governors AM +6%, RN +98%.

Differences, explained:
- **Latency.** Moving the start of the trade window from the signal minute to 60 s later removes the
  governors' only fill (RN, $2.40 traded in the first minute) and halves the runoff's +5¢ capacity
  ($4,375 → $2,166), because the national market repriced within a minute. The first round barely
  changes ($2,741 → $2,729).
- **First-round `capacity` key on the site.** An earlier export read `capacity.csv`, the first-round run
  *without* the favoured-side rule (12 entries, including long shots; the national entry fired at 20:19
  at 0.655 and found $52), and showed $110 with no slippage figures. `analysis/export_brazil.py` now reads
  `capacity_favoured_only_true.csv`, and `stats.json` `capacity.polymarket` = $2,740.75 / $4,111.30 /
  $6,906.22, equal to the audit's recomputation (`reconcile.csv`, columns `site_stats_*`).
- **Registered 2-minute evaluations.** `evaluation.csv` comes from the registered 2-minute replay; this
  audit uses the 1-minute ticks the capacity runs used, so a few entries are one minute earlier and their
  quoted returns differ (e.g. ES +61% here vs +77% registered). The first round's `evaluation.csv` is also
  without the favoured-side rule.

## `stats.json` blocks

Added to `site/data/<eid>/stats.json`; no other key is changed. Intervals are `[lo, hi]` or null; money in
USD; shares in percentage points.

```
"accuracy": {
  "evidence": "in-sample" | "pre-specified" | "model-only" | "not-preregistered",
  "share_error_pt": {"<race>": {"1%": {"model": pt, "raw": pt}, "5%", "10%", "25%", "50%"}},   // Brazil only
  "share_error_mae_pt": {"1%": {"model", "raw", "races"}, ...},          // il2026: {"model", "raw", "counted", "lists"}
  "coverage90": {"<race>": {"1-95%": {"coverage90", "mean_z", "n"}, "1-90%": {...}, "90-95%": {...}}},  // Brazil only
  "coverage90_pooled": {"1-95%": {"races", "median", "min", "races_below_80pct", "minutes_weighted"}, ...},  // Brazil
  "coverage90_list_shares": {"1-95%": {"coverage90", "mean_z", "n", "releases"}, ...},                   // il2026
  "pwin": {"races", "minutes", "brier_model", "log_model",
           "brier_market", "log_market", "brier_diff", "log_diff", "brier_diff_ci", "log_diff_ci",   // absent without prices
           "races_model_better_brier", "races_market_better_brier",
           "reliability_model":  [{"bin": "0.0-0.1", "minutes", "races", "mean_p", "freq"}, ...],
           "reliability_market": [... same, binned by price; [] without prices]},
  "pwin_window": "<sentence>",
  "outcome": "<sentence>"                                                  // il2026
}
"profit": {                                                                // br2026r1, br2026r2, br2026r2gov, il2026
  "evidence", "rule", "execution", "outcome" (il2026),
  "signals", "won", "hit_rate", "hit_rate_ci",
  "max_deployed", "max_profit", "max_profit_ci", "max_return_per_usd", "p_market_fair",
  "largest_trade_share", "pnl_without_largest",
  "by_size": {"25" | "100" | "500" | "1000" | "5000": {"fillable", "filled_any", "deployed", "pnl", "return_per_usd",
              "pnl_ci", "return_ci", "p_market_fair", "largest_trade_share"}},
  "variants": {"<execution key>": {"max_deployed", "max_profit"}}
}
```

`fillable` = signals whose available dollars cover the whole size; `deployed` = Σ min(size, available);
`largest_trade_share` = largest single trade's P&L / total P&L (null if the total ≤ 0); `p_market_fair` =
exact P(P&L ≥ observed) if each filled trade won with probability equal to its all-in price. The headline
execution is Brazil `trades_lat60s_30min_slip0c`, Israel `book_lat60s`. Variant keys:
`trades_lat{0,60}s_{5,30}min_slip{0,2,5}c[_rule]` (Brazil), `book_lat{0,30,60}s` (Israel). Israel blocks
hold aggregates only (the 2026 count comes from third-party mirrors, private research use;
`docs/israel.md`).

`"invest10k"` (br2026r1, br2026r2, br2026r2gov, il2026, hu2026, mx2024) is the **model-limit** headline, with
the signal-price result nested in it:

```
{"evidence", "variant": "model_limit_edge5c_delay60s_10pct", "execution": "<sentence>", "bankroll": 10000,
 "ending_value", "deployed", "bets" (fills), "markets", "won" (fills on the winning side), "return_pct",
 "eligible_volume", "eligible_pnl", "largest_market", "largest_market_pnl", "largest_market_share" (null if P&L <= 0),
 "bets_path": [{"t" (first fill), "t_last", "market", "side", "n_fills", "stake", "won", "payout", "pnl"}],   // per market and side
 "value_path": [{"t", "value"}, ..., {"t", "value", "resolved": true}],
 "note": "<single event / concentration / upper bound>",
 "signal_price": {"evidence", "execution", "bankroll", "ending_value", "deployed", "bets", "won", "return_pct", "signals",
                  "largest_market", "largest_market_pnl", "largest_market_share",
                  "bets_path": [{"t", "market", "side", "stake", "won", "payout", "pnl"}], "value_path": [...]}}
```

`"profit_model_limit"` (same elections): `{"markets_scored", "grid": {"edge{5,10,20}c_delay{60,300}s":
{"eligible_trades", "eligible_volume", "eligible_pnl", "eligible_return", "markets", "largest_market",
"largest_market_volume_share", "largest_market_pnl_share", "volume_won_share", "bankroll": {"first" | "first_cap"
| "10pct" | "10pct_cap": {"ending_value", "deployed", "fills", "markets", "return_pct"}}}}}`.

`value_path`: from poll close to the end of the price data, every 5 minutes (coarser, in multiples of 5 minutes,
when that would exceed 150 points), cash + open positions marked to market at the last known Polymarket minute
price of the side held (at cost if the market has no price yet); the
last point is the ending value after payouts. **Israel is published as totals only**: `bets_path` and
`value_path` are null in both models, no market names (also none in its `profit_model_limit` grid), and a
`note` (one event, provisional outcome); the bet times and value path come from the private-research 2026
count and are kept in `results/edge_audit/landing_summary.json` only.

### `site/data/summary.json` (homepage; written by the same run, ~60 kB)

```
{"generated": ISO, "doc": "docs/edge_audit.md",
 "elections": [{"id", "name", "evidence"}],
 "projection_error": {"thresholds": ["1%","2%","5%","10%","25%","50%","75%","90%"],
   "by_election": {"<eid>": {"main_race", "main": {"1%": {"model", "raw"} | null, ...} | null, "races",
                             "mae": {"1%": {"model", "raw", "races"}, ...}, "note" (il2026)}},
   "pooled": {"main": {"1%": {"model", "raw", "elections"}, ...}, "all_races": {"1%": {"model", "raw", "races", "note"}, ...}}},
 "calibration": {"by_election": {"<eid>": {"coverage90", "minutes", "races", "basis"}},   // il2026: "releases", "list_releases"
   "reliability": {"elections", "races", "minutes", "note",
                   "model": [{"bin", "minutes", "races", "mean_p", "freq"}], "market": [...],
                   "by_election": {"<eid>": {"minutes", "races", "model": [...], "market": [...]}}}},
 "invest10k": {"rule", "headline": "model_limit", "by_election": {"<eid>": <invest10k without "signal_price">},
               "pooled": {"elections", "bankroll", "ending_value", "deployed", "bets", "won", "return_pct"},
               "signal_price": {"rule", "by_election": {"<eid>": <signal_price without bets_path / value_path>}, "pooled": {...}},
               "grid": {"<eid>": {"edge5c_delay60s": {"eligible_volume", "eligible_pnl", "first", "first_cap", "10pct",
                                                     "10pct_cap"}, ...}}},   // ending values per bankroll mode
 "settle_lead": {"note", "by_election": {"<eid>": [{"race", "market", "gate", "model", "market_settle", "lead_min", "lead_vs"}]}}}
```

- Projection error: absolute points, model projection and raw count minus the final share, at the first
  minute (release, for Israel) with at least each share counted. Main races: `br` (Brazil, national two-way
  share), `list` (Hungary, Tisza's national list share as scored by `hu_score.py`), `mx`, `likud` (Israel list share); the
  governors have no single main race (`main` null, `mae` over the six). `pooled.main` averages the main races
  over elections; `pooled.all_races` averages every Brazil, Hungary and Mexico race.
- Calibration: minutes with 1–95% counted whose 90% interval contained the final share, pooled over the
  election's races (Israel: list × release pairs). Reliability: model P (and market price) in ten bins against
  the observed frequency, over the P(win) window (1% counted until both settled), for resolved outcomes only
  (Brazil 2026, Brazil 2022, Hungary; Israel excluded as provisional). Hungary has 50,141 of the 53,260
  pooled minutes, so the pooled table mostly describes Hungary; use `by_election` for the others.
- Settle lead: from `stats.json` `settle_gated` (model counted only from ≥ 1% counted; Brazil 2022 against the
  raw count, no prices); Israel: the aggregate gated lead for "Likud most seats" (no times; private count).
- Pooled projection error over main races (7 elections): 1% counted 2.72 vs raw 4.84 points; 10% 0.63 vs
  3.51; 50% 0.46 vs 1.87.

## Outputs (`results/edge_audit/`, not published)

`accuracy_share_error.csv`, `accuracy_coverage.csv`, `accuracy_pwin.csv` (pools: each election,
`brazil2026_all`, `brazil2026_prespecified`, `all_with_market`), `accuracy_pwin_by_race.csv`,
`accuracy_reliability.csv`, `profit_signals_brazil.csv` (one row per signal × execution variant),
`profit_signals_israel.csv` (private: contains model P from the 2026 count), `profit_by_size.csv`,
`reconcile.csv`, `summary.json`, `profit_model_limit.csv` (every election × edge × delay × bankroll mode),
`landing_summary.json` (full, incl. Israel's paths; the site copy drops those), `il_fees.json`, `trades/` (taker
trades fetched from the Polymarket data API for markets without an archived trades file).

    uv run python analysis/edge_audit.py            # writes site blocks too
    uv run python analysis/edge_audit.py --no-site  # tables only

Run from the repository root; no environment variables are needed. Brazil inputs are read from
`results/` and `~/brunder-stage` (`BRUNDER_STAGE_ROOT` overrides); the recorded Israel books from
`$BRUNDER_DATA/raw/snap/israel_2026/polymarket_books` if present, else from `$BRUNDER_IL_DATA` (default
`~/brunder-data-local`). Taker trades come from an archived `trades/<slug>.parquet` (with `is_taker`) under
`$BRUNDER_DATA`, `$BRUNDER_IL_DATA` or a stage when one exists, else from the public data API (first run only;
cached). The script only adds or replaces its own `accuracy`, `profit`, `invest10k` and `profit_model_limit` keys
and rewrites `site/data/summary.json`, so it can run before or after `analysis/export_brazil.py` /
`analysis/il_export.py` / `analysis/hu_score.py` / `analysis/mx_score.py`; exporters that rewrite `stats.json`
drop the audit's keys, so run the audit again after them.
