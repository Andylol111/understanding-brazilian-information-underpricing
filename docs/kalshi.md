# Kalshi: market data, Polymarket ↔ Kalshi pairs, and cross-venue arbitrage

Code:
- `src/brunder/kalshi.py`: public market-data client. It covers the live and historical tiers, and turns
  books and candles into probabilities.
- `src/brunder/arb.py`: fees, pair configs, book walks, minute history.
- `analysis/arb_pairs.py`: writes `configs/pairs/<election>.toml`.
- `analysis/kalshi_export.py`: downloads the data and writes the Kalshi parts of the dashboard.
- `analysis/arb_history.py`: arbitrage within Polymarket only.

Tests: `tests/test_kalshi.py`, `tests/test_arb.py`.

## Licence and what is published

**Decision (project owner, 2026-10-28): full scope.** The project downloads Kalshi data and publishes
charts and aggregates derived from it, credited to Kalshi.

**Exception request, submitted 2026-10-28, pending.** Kalshi Institutional invited a one-time written
exception request, and the project submitted it. It declares:
- election markets only;
- aggregate statistics and charts, plus the code, are published;
- no raw data is redistributed;
- non-commercial use;
- data retained for up to 24 months;
- no AI training.

Until Kalshi answers, the terms below are what applies. Publishing therefore rests on the owner's
decision and the pending request.

**Published (site/, git), always credited "© Kalshi"** in each election's `meta.sources`:
- the per-minute Kalshi P(A) line in `timeline.json`;
- Kalshi settle times, catch-up and capacity statistics;
- `arb.json`: per-minute cross-venue costs and edges, plus the dollars fillable;
- `stats.arbitrage` and `summary.json["kalshi"]`;
- Kalshi tickers, with each market's resolution paraphrased in our own words.

**Not published:**
- raw Kalshi rows: candles, trades, order books and market records. They live under `$BRUNDER_DATA/raw/kalshi/` and
  `results/kalshi/`, both gitignored;
- Kalshi titles or rule text: the pairs files carry our own paraphrase (`kalshi_resolution`) and links;
- saved Kalshi API responses in tests. Test fixtures are built by hand.

**Open point for Kalshi.** `arb.json` rows carry Kalshi's best ask on each side per minute
(`k_yes_ask`, `k_no_ask`), which the dashboard contract asks for. That is per-minute quote data rather
than an aggregate. If Kalshi's answer limits publication to aggregates, drop those two fields and keep
`cost_a` and `net_edge`.

**The terms as read on 2026-10-28.** Kalshi grants no general licence to republish its data:

- **Kalshi Data Terms of Use** (<https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf>):
  - "Kalshi Data" includes "volume, bid-ask prices, … settlement prices, … open interest and related
    information, market descriptions".
  - §II: "unless Kalshi … give[s] you prior written authorization, you are strictly prohibited from …
    collecting, copying, reproducing, downloading …, distributing, disseminating, publicly displaying,
    publishing, editing, adapting, creating derivative works, … scraping, compiling … in relation to any
    Kalshi Data".
  - §I allows access "only for your personal use for non-commercial purposes".
- **Kalshi Developer Agreement v1.1** (<https://assets.kalshi.com/Kalshi-Developer-Agreement.pdf>):
  - §1: "Accessing or using Kalshi's API shall be considered a binding acceptance of this Agreement".
  - §3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange;
    all other usages are disallowed".
  - §3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API
    except for purposes of facilitating your own trading on Kalshi. You may not share such data or
    content with third parties in any manner without prior written authorization from Kalshi."
  - §3.5 prohibits access "for any other benchmarking or competitive purposes".
  - §6.4 requires prior written approval for public statements about Kalshi or its API.
  - A newer version dated 2026-07-01 exists on kalshi.com. Its page returned HTTP 429, so it was not
    read.

**Where the request went:** Kalshi Institutional (<mailto:institutional@kalshi.com>,
<https://institutional.kalshi.com/application?type=data-only>).

**When Kalshi answers:**
- record the answer here;
- if it narrows the scope, remove what falls outside it and re-run `analysis/kalshi_export.py export`;
- if it is refused, stop downloads and the recorder, and revert the Kalshi keys with the exporters (a
  re-export drops them).

## Public API (no key)

The base URL is `https://api.elections.kalshi.com/trade-api/v2`. Despite the name it covers all
markets; the docs also list `https://external-api.kalshi.com/trade-api/v2`. The following endpoints
all answered HTTP 200 without authentication on 2026-10-28:

| endpoint | used for |
|---|---|
| `GET /series?category=Elections`, `GET /series/{s}` | discovery; `fee_type`, `fee_multiplier`, `contract_terms_url`, settlement sources |
| `GET /events?series_ticker=…&with_nested_markets=true`, `GET /events/{e}` | events and their markets (cursor-paged) |
| `GET /markets/{t}` | rules (`rules_primary`/`rules_secondary`), status, result, quotes |
| `GET /markets/{t}/orderbook`, `GET /markets/orderbooks?tickers=…` (≤100) | books; **bids only** for YES and NO |
| `GET /markets/trades?ticker=…&min_ts&max_ts` | every fill: `yes_price_dollars`, `count_fp`, `taker_side` (cursor-paged, ≤1000/page) |
| `GET /series/{s}/markets/{t}/candlesticks?start_ts&end_ts&period_interval=1` | 1-min OHLC of trade price and of the best YES bid/ask |
| `GET /historical/cutoff` | records older than a moving cutoff live under `/historical/*` (2026-10-28: trades and settled markets before 2026-08-29) |
| `GET /historical/markets?event_ticker=…`, `/historical/markets/{t}`, `/historical/markets/{t}/candlesticks`, `/historical/trades` | the same data for older markets and fills; `kalshi.event/market/candlesticks/trades` route there automatically (historical candles use `close`, not `close_dollars`) |

Notes:

- **Prices and sizes are strings.** Prices are dollars ("0.6300") and sizes are fractional contract
  counts ("102275.95"). `kalshi.num` parses both.
- **Asks are implied by the opposite side's bids.** The YES ask is 1 − the best NO bid; `kalshi.parse_book`
  builds both ask ladders this way.
- **Rate limits** (<https://docs.kalshi.com/getting_started/rate_limits>) are token buckets. Basic tier
  reads are 200 tokens/s at 10 tokens per request, about 20 requests/s. An over-limit request gets 429
  with no `Retry-After`. No limit is documented for unauthenticated calls.
- **Our pacing.** `kalshi.py` spaces requests at least 0.1 s apart process-wide (≤10/s). It retries
  429/5xx with exponential backoff (`brunder._http`), as Developer Agreement §4.3 asks.
- **Website not used.** kalshi.com sits behind a Vercel bot checkpoint; we neither scrape nor
  circumvent it.

## Fees

**Kalshi.** Source: <https://kalshi.com/docs/kalshi-fee-schedule.pdf> (7 Jul 2026 update) and
<https://docs.kalshi.com/getting_started/fee_rounding>.

- Taker fee = round up(M × 0.07 × C × P × (1 − P)), where C is contracts, P is price in dollars, and M
  is the series multiplier (`fee_multiplier`; an event can override it).
- Rounding: the fee is rounded up to $0.000001. Then the member's balance change (C·P + fee) is rounded
  up to the balance precision: $0.01 for non-direct (retail/FCM) members, $0.0001 for direct members.
  A per-order fee accumulator makes a multi-fill order cost the same as one fill.
- Net effect: "round up to the next cent" per order. The schedule's table gives 100 contracts at $0.07
  (1¢), $0.63 (10¢) and $1.75 (50¢); `tests/test_arb.py` checks these.
- Makers pay nothing unless the series `fee_type` includes maker fees. In that case the maker
  coefficient is 0.0175, i.e. 0.25 × taker. Every Elections series read on 2026-10-28 was `quadratic`
  with M = 1 (two had M = 0), so these are taker-only.
- There is no settlement fee.

**Polymarket.** Source: <https://docs.polymarket.com/trading/fees>.

- Fee = C × feeRate × p × (1 − p), in USDC, takers only, rounded to 5 decimals.
- feeRate comes from each market's own `feeSchedule.rate` and is 0 when `feesEnabled` is false. The
  current Politics rate is 0.04.
- `feeSchedule.exponent` (1 on every market seen) is applied to p(1 − p). This generalisation is our
  reading; the docs show only exponent 1.
- **Fee schedules change.** The Lula market of `brazil-presidential-election` shows rate 0 today
  ("zero_fees"), while other candidates in the same event show 0.04. The pairs files hold the rate at
  generation time, not the rate in force on a past night. Check the night's own config (e.g.
  `fee_rate = 0.0` in `configs/night_r2_v1.toml`) before trusting historical net numbers.

`arb.kalshi_fee`, `arb.kalshi_fee_levels` and `arb.pm_fee` implement these. `arb.walk_pair` charges
them on the whole order.

## Pairs: `configs/pairs/<election>.toml`

Generate with `uv run python analysis/arb_pairs.py [eid …]`. That script holds the event-level SPEC;
edit SPEC there rather than the TOML files.

How pairs are found:

- Each election lists (Polymarket event, Kalshi event) groups on the same question.
- Outcomes are paired by name with `arb.match_outcomes`. Names are normalised (accents, "(D)", "Sen."
  dropped) and a pair is kept only if the match is unique both ways. Explicit aliases cover real
  renames, e.g. "Amcha Yisrael" = Kalshi's "People of Israel", or "PSOE" = "Spanish Socialist Workers'
  Party".

Each `[[pair]]` has:

- `pm_market` (slug) ↔ `kalshi_ticker`.
- `same_side` (Kalshi YES ≡ Polymarket YES).
- `pm_yes_is` / `kalshi_yes_is`: the race side, `a` | `b` | `other` (contract `yes_is`, extended with
  `other` for third candidates).
- Fee parameters.
- Polymarket's exact resolution text (`pm_rules`). For Kalshi: our own paraphrase
  (`kalshi_resolution`, from `K_SAYS` in `analysis/arb_pairs.py`), `kalshi_url` and the contract-terms
  PDF link.
- `resolution_differences`.

**`risk_free = false` for every pair.** No pair shares one resolution source and timetable, so "buy A
on one venue and not-A on the other" can lose on both legs. Recurring differences, flagged
automatically from the texts:

- **Sources.** Polymarket resolves on "a consensus of credible reporting", or official results if that
  is ambiguous. Kalshi resolves on the Source Agencies named in its contract terms.
- **Deadlines.** Polymarket falls back to "Other" if the result is not known by a date, e.g. 30 Jun
  2027. Kalshi has its own latest expiration date.
- **US Senate/governor party markets.** Kalshi pays on the party of whoever is *sworn in* or
  *inaugurated* (with an early media-consensus close). Polymarket pays on the election result, and
  its candidate named in the market can be replaced.
- **Party vs person.** In CA governor (`GOVPARTYCA`) and MI senate (`SENATEMI`), Kalshi resolves on
  the party while Polymarket resolves on the named person.
- **Ties.** Brazil state runoff markets: Kalshi splits exact ties proportionally, while Polymarket
  breaks ties alphabetically. Israel bloc market: Kalshi pays $0.50 on both sides of a seat tie, while
  Polymarket breaks the tie on combined votes, then 50-50.
- **Opposite-side Brazil state pairs.** These pair Polymarket "Lula wins X" with Kalshi "Flávio wins
  X". They assume exactly two candidates and no tie.
- **Certification.** Kalshi's Brazil state markets close on *certified* results; Polymarket can
  resolve earlier on credible reporting.

**Which elections have markets on both venues** (checked 2026-10-28):

| election | pairs file | pairs | races with both venues |
|---|---|---|---|
| Brazil 2026, first round | `br2026r1.toml` | 46 | first-round winner, 2nd and 3rd place, outright win, whole-election winner |
| Brazil 2026, runoff | `br2026r2.toml` | 31 | winner; runoff states AL, AP, AM, MG, PA, RR, TO (Kalshi lists only these 7); São Paulo; vote abroad |
| Brazil 2026, governors | `br2026r2gov.toml` | 10 | Federal District only |
| Israel 2026 | `il2026.toml` | 69 | next PM, most seats, 2nd/3rd/4th place, bloc with most seats, lists winning a seat |
| Hungary 2026-04-12 | `hu2026.toml` | 6 | most seats, next PM, Tisza 130+ / 120+ (Kalshi's thresholds are one seat higher: flagged) |
| Canada 2025-04-28 | `ca2025.toml` | 4 | most seats (Liberal, Conservative); next PM (Kalshi: the PM's party, not the person: flagged) |
| Quebec 2026-10-05 | `qc2026.toml` | 6 | most seats (PQ, PLQ, CAQ, PCQ), PQ majority (64+), next Premier |
| US midterms 2026 | `us2026.toml` | 58 | House, Senate, CA gov, and senate/governor in TX, OH (special), GA, AK, ME, MI, IA, KS |
| Taipei / New Taipei mayor 2026-11-28 | `tw2026.toml` | 5 | Taipei, New Taipei |
| France 2027 presidential | `fr2027.toml` | 104 | winner, who advances, 1st/2nd/3rd in round 1, on the ballot, outright first-round win |
| Spain general election 2026-11-29 | `es2026.toml` | 13 | most seats, 2nd/3rd, next PM, PP+Vox ≥ 50% |

Kalshi listed nothing for Mexico 2024 or Brazil 2022. Searched: the live and historical series and
market lists, including every `KXMEXICO*` series.

Not paired:

- Bracket markets (margins, vote shares, seat counts, turnout). The two venues use different bins.
- Polymarket outcomes that Kalshi does not list. `arb_pairs.py` prints these per group.

## Arbitrage method

- **Live books** (`arb.book_arb`) walk both ladders at once for a size, or for every unit whose
  marginal cost (two prices plus two per-unit fees) is below $1. The two legs are:
  - `same_side`: Polymarket YES + Kalshi NO, or Polymarket NO + Kalshi YES;
  - opposite: YES + YES, or NO + NO.

  Polymarket NO is bought against YES bids at 1 − bid. Kalshi fees are charged once on the walked
  order.
- **Minute history** (`arb.minute_arb`; contract `arb.json` rows).
  - Kalshi side: the 1-min candle's closing best bid/ask, forward-filled, assigned to the minute at
    which the candle closes, so there is no look-ahead.
  - Polymarket side: Polymarket keeps no quote history. Where the project recorded Polymarket books on
    the night (Israel, 30 s), those asks are used. Elsewhere the asks are the 1-min CLOB price ± 0.01
    (`pm_quotes` in each `arb.json` entry says which).
  - The fee for each leg is per unit and not cent-rounded.
  - Quote-based minutes overstate the opportunity: thin markets have stale quotes, and the Polymarket
    approximation is coarse. The fillable numbers are the measurement.
- **Fillable** (`arb.fillable`) uses no approximation. In each minute, the taker fills on each leg show
  prices at which that side was really available:
  - Polymarket: a taker BUY of the token at p, or a taker SELL of the other token at q, which offers
    this side at 1 − q;
  - Kalshi: `taker_side` at that side's price.

  These are walked against each other at cost < $1 after fees. Volume others took bounds what one
  more taker could have had; it was not free size.

## Dashboard data: `analysis/kalshi_export.py`

**Run order.** The election's own exporter (`export_brazil.py`, `il_export.py`, `hu_export.py`,
`ca_export.py`) runs first, then `edge_audit.py`, then:

```
uv run python analysis/kalshi_export.py              # download what is missing, then export all elections
uv run python analysis/kalshi_export.py export       # offline: re-add the Kalshi keys after a re-export
```

**What it writes.** Only Kalshi keys, so a re-export by another workstream removes them. Re-run this
script after one.
- **`timeline.json`:** `kalshi` in every row of each race with a Kalshi market.
- **`stats.json`:**
  - `settle.<race>.kalshi` and `settle_gated.<race>.kalshi`;
  - `catchup.kalshi` and `capacity.kalshi`;
  - `arbitrage` and `evidence.arbitrage`;
  - `reasons` for every Kalshi key.
- **`meta.json`:** Kalshi entries in `races[].markets` (ticker plus our own wording), the Kalshi source
  with its credit line, and a note.
- **`arb.json`:** written whole.
- **`summary.json`:** only the `"kalshi"` key.

**Elections covered.**
- With data: `br2026r1`, `br2026r2`, `br2026r2gov`, `il2026`, `hu2026`, `ca2025`, `qc2026`.
- Reason only, "Kalshi listed no market on this election": `br2022r1`, `br2022r2`, `mx2024`.

**Definitions** (full text in the module docstring):
- **Kalshi P(A):** at each row, the latest 1-min candle's bid/ask midpoint when the spread is at most
  10¢, else its last trade (`kalshi.prob_from_candles`). Markets whose YES is side b are inverted.
- **Settle:** the first row after which P(winner) stays ≥ 0.9 to the race's last row, as for
  Polymarket.
  - The winner is Kalshi's own settlement, or, while Kalshi has not settled, the side the Polymarket
    price settled on (Israel).
  - The 1% gate applies to the model only, so `settle_gated.kalshi` = `settle.kalshi`.
- **Catch-up:** the 30-minute slope of Kalshi's move on the model's lead over Kalshi (model P minus
  Kalshi P(A)), as `underpricing_figures.slope_ci` computes it for Polymarket. It needs model P.
- **Capacity:** the frozen favoured-side rule (Brazil configs' `min_edge`, `min_counted`), applied with
  Kalshi's own ask plus Kalshi's fee. Capacity is the Kalshi taker fills on that side at or below the
  signal ask (+2¢, +5¢) within 30 minutes. Brazil only: no other election has a frozen rule.
- **Arbitrage:** per pair and minute, the cheaper complementary combination including both venues'
  taker fees, plus `fill_usd` (both venues' real fills in that minute, combined cost below $1).
  - Pair totals are also given at 10-second and 1-second buckets.
  - `stats.arbitrage` sums the pairs: `fillable_usd` uses 1-minute buckets; `fillable_usd_1s` is the
    strict version.

**Raw inputs** (never committed):
- `$BRUNDER_DATA/raw/kalshi/<eid>/{candles,trades,markets}`;
- `$BRUNDER_DATA/raw/polymarket_window/<eid>/{prices,trades,book_tops.parquet}`.

**Windows:** each election's dashboard rows, widened from 24 h before to 2 h after. In all, 23 MB of
Kalshi data and 9 MB of Polymarket data, downloaded at 10 requests/s or less.

## Results (2026-10-28)

Evidence follows each election's label: Brazil runoff and governors pre-specified, Brazil first round
in-sample, others not-preregistered. Nothing was fitted. The arbitrage figures are net of both
venues' taker fees. Polymarket fees are the markets' current schedules; Brazil's runoff markets were
fee-free.

**Settle times** (Kalshi minus Polymarket, minutes; negative = Kalshi was ≥ 0.9 on the winner first):

| election | races compared | Kalshi first / same minute / Polymarket first | median | per race |
|---|---|---|---|---|
| Brazil runoff | 9 | 6 / 2 / 1 | −33 | br 0, al −109, am 0, ap −44, mg −7, pa −190, rr +175, sp −170, to −33 |
| Brazil first round | 1 | 1 / 0 / 0 | −2 | br −2 |
| Brazil governors | 1 | 0 / 0 / 1 | +1 | gov-df +1 |
| Israel | 4 | 1 / 0 / 3 | +3 | most-likud +3, most-yashar +3, most-together −89, bloc +174 |
| Hungary | 3 | 2 / 0 / 1 | −1 | seats +2, tisza130 −1, tisza120 −12 |
| Canada | 4 | 2 / 2 / 0 | −1 | pm-carney −2, pm-poilievre −2, most-lpc 0, most-cpc 0 |
| Quebec | 6 | 0 / 5 / 1 | 0 | pq-majority +10; the rest already settled at the first row |

Settles at a race's first row (Brazil `sp` on Kalshi, `rr` on Polymarket, Quebec) mean that venue was
already ≥ 0.9 when the window opened. The two venues mostly settle within minutes of each other. The
large gaps are thin state markets, where one venue sat on a stale quote.

**Cross-venue arbitrage** (from `stats.arbitrage`):

| election | pairs | minutes with net edge > 0 (quotes) | max net edge | fillable, 1-min buckets (net) | fillable, 1-s buckets |
|---|---|---|---|---|---|
| Brazil runoff | 9 | 431 | 27.1¢ | $943.9k ($10,521) | $88.6k |
| Brazil first round | 1 | 6 | 3.9¢ | $61.1k ($1,038) | $4.6k |
| Brazil governors | 1 | 0 | — | $0 | $0 |
| Israel (recorded Polymarket books) | 4 | 1,480 | 22.8¢ | $37.4k ($1,139) | $1.9k |
| Hungary | 6 | 362 | 15.8¢ | $248.2k ($1,514) | $5.4k |
| Canada | 4 | 241 | 7.2¢ | $1.78M ($16,459) | $52.2k |
| Quebec | 6 | 801 | 13.2¢ | $1.4k ($38) | $11 |
| US midterms, pre-election 21–28 Oct (`results/kalshi/us2026_preelection_arb.csv`) | 58 | 26,258 pair-minutes | 2.1¢ | $162.0k ($1,242) | — |

How to read the arbitrage table:

- **Fillable** is the size both venues' real takers traded in the same bucket at a combined cost below
  $1 after fees. It bounds what one more trader could have had; it is not free money.
- **Almost all of the fillable size is in the big markets** around the decisive minutes: the Brazil
  national market ($943k of the runoff's $944k) and Canada's prime-minister pairs ($1.77M of $1.78M:
  the Polymarket Carney/Poilievre markets against Kalshi's Liberal/Conservative PM-party markets).
- **Every pair has resolution differences** (`configs/pairs`):
  - Canada pairs a person on Polymarket with a party on Kalshi;
  - Hungary's thresholds differ by one seat;
  - US party markets pay on who is sworn in.

  These are near-arbitrages, not risk-free ones.
- **The largest quote edges are not executable.** They come from a stale or one-sided quote: Brazil
  `sp`, where Kalshi's Lula-in-São-Paulo ask was 1¢ while Polymarket's Lula price was 0.30, and
  Israel's bloc after midnight. Neither venue traded at those prices.

**Catch-up and capacity:**

| election | catch-up, 30 min: Kalshi | catch-up, 30 min: Polymarket | capacity: Kalshi (signal / +2¢ / +5¢) | capacity: Polymarket |
|---|---|---|---|---|
| Brazil runoff | 0.36 [0.09, 1.08], 7 markets | 0.20 [0.04, 0.38] | $132 / $200 / $4,802 | $0 / $100 / $4,375 |
| Brazil first round | 1.73 [0.68, 4.26], 1 market, 4 clusters | 0.55 [0.29, 0.75] | $326 / $699 / $886 | $2,741 / $4,111 / $6,906 |
| Brazil governors | none: too few minutes inside (0.02, 0.98) | 0.93 | $0 / $0 / $0 | $2 / $5 / $45 |
| Hungary | 0.06 [−0.20, 1.37], 2 markets | 0.00 | none: computed only where a frozen night config defines the rule | $80,282 / $139,290 / $316,742 (edge-audit rule) |

## Polymarket-only results (no Kalshi data)

Produced by `uv run python analysis/arb_history.py br2026r2 br2026r1 il2026` on 2026-10-28. Window:
12:00–12:00 UTC around each count. Evidence: in-sample and descriptive; nothing was fitted. Outputs
are in `results/arb/<eid>/pm_only_*` (gitignored).

**Method.** Within Polymarket, a race listed as two markets ("A wins", "B wins") can be bought as a
package. NO_A + NO_B pays at least $1 in any race. YES_A + YES_B pays $1 when exactly one of the two must
win (the runoff). Fills from the two markets count as one package only if they fall in the same short
time bucket (1 s, 10 s or 1 min) at traded prices that cost below $1 after fees.

| night | races | combinations | fills within 1 s | within 10 s | within 1 min |
|---|---|---|---|---|---|
| Brazil runoff 2026-10-25 | 10 (national + 9 state/abroad) | YES+YES, NO+NO | $1.50M of packages, $11.9k net | $2.64M, $18.8k net | $4.22M, $45.8k net |
| Brazil first round 2026-10-04 | 4 (winner, 2nd place, whole election) | NO+NO only | $71.6k, $307 net | $98.0k, $436 net | $166.6k, $839 net |

Runoff details:
- At 1 s, $1.498M of the $1.501M is the national race, 73% of it NO+NO.
- Most of the net came during the count: $7.9k of the $11.9k between 22:00 and 23:00 UTC.
- Edges per package were small: median 0.3¢, 90th percentile 1.2¢.
- The quote approximation (970 race-minutes at h = 0.01, 963 of them in thin state and abroad
  markets) overstates the opportunity.

First-round fees: the markets carry Polymarket's current 0.04 politics rate. The rate in force on
2026-10-04 has not been verified.

## Commands

```
uv run brunder kalshi search 'brazil|israel'              # find Elections series
uv run brunder kalshi event KXBRPRES-26                   # markets, quotes, volume
uv run python analysis/arb_pairs.py                       # regenerate configs/pairs/*.toml
uv run python analysis/kalshi_export.py                   # download + dashboard Kalshi keys (after exporters, edge_audit)
uv run python analysis/kalshi_export.py us2026            # US pre-election history → results/kalshi/
uv run python analysis/arb_history.py br2026r1 br2026r2   # Polymarket-only → results/arb/<eid>/
uv run brunder kalshi record us_midterms_2026 --pairs configs/pairs/us2026.toml --interval 30 --until 2026-11-20T00:00Z
```

## Election-night recording (US midterms)

**What it does.** `brunder kalshi record <tag>` polls the bulk order-book endpoint
(`GET /markets/orderbooks`) every `--interval` seconds. It sends the tickers in chunks of 50, with
requests paced at least 0.1 s apart. The tickers come from `--ticker`, from every open market of
`--event`, or from a pairs file via `--pairs`.

**What it writes,** in `brunder snap`'s layout:
- `data/raw/snap/<tag>/kalshi_books/<UTC time>.json` holds `{ticker: orderbook_fp}`, written only when
  the books changed;
- each write appends one line to `<tag>/index.jsonl`.

It can therefore run next to `brunder snap us_midterms_2026` with the same tag. A live exporter turns
the newest file into P(YES) per ticker with `kalshi.prob_from_book`.

**VM unit** (separate from `brunder-snap-us.service`; same limits; started by its own timer at the same
time as the snap recorder):

```
# /etc/systemd/system/brunder-kalshi-us.service
[Unit]
Description=brunder Kalshi order-book recorder: US midterms 2026-11-03 (us2026 pair tickers, 30 s)
Wants=network-online.target
After=network-online.target

[Service]
User=brunder
WorkingDirectory=/srv/brunder/repo
Environment=PYTHONUNBUFFERED=1
ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder kalshi record us_midterms_2026 --pairs configs/pairs/us2026.toml --interval 30 --until 2026-11-20T00:00Z
Restart=on-failure
RestartSec=30
Nice=10
CPUQuota=20%
MemoryMax=512M
IOSchedulingClass=idle

# /etc/systemd/system/brunder-kalshi-us.timer
[Unit]
Description=Start the Kalshi US midterms recorder before the first polls close (2026-11-03 23:00 UTC)

[Timer]
OnCalendar=2026-11-03 18:00:00 UTC
Persistent=true
Unit=brunder-kalshi-us.service

[Install]
WantedBy=timers.target
```

The 58 us2026 tickers need two requests per poll. At 30 s that is about 0.07 requests/s.
