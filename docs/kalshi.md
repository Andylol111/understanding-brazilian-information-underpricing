# Kalshi: market data, Polymarket ↔ Kalshi pairs, and cross-venue arbitrage

Code: `src/brunder/kalshi.py` (public market-data client), `src/brunder/arb.py` (fees, pair configs,
book walks, minute history), `analysis/arb_pairs.py` (writes `configs/pairs/<election>.toml`),
`analysis/arb_history.py` (past nights). Tests: `tests/test_kalshi.py`, `tests/test_arb.py`.

## Licence: Kalshi data stays off the public site and out of git

Checked on 2026-10-28. Kalshi grants no licence to republish its data, and no research or
non-commercial exception:

- **Kalshi Data Terms of Use** (<https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf>,
  linked from kalshi.com). "Kalshi Data" includes "volume, bid-ask prices, … settlement prices, …
  open interest and related information, market descriptions". §II: "unless Kalshi … give[s] you
  prior written authorization, you are strictly prohibited from … collecting, copying, reproducing,
  downloading …, distributing, disseminating, publicly displaying, publishing, editing, adapting,
  creating derivative works, … scraping, compiling … in relation to any Kalshi Data". §I allows access
  "only for your personal use for non-commercial purposes". That use excludes "providing archived or
  cached data sets containing Kalshi Data to another person or entity".
- **Kalshi Developer Agreement v1.1** (<https://assets.kalshi.com/Kalshi-Developer-Agreement.pdf>).
  - §1: "Accessing or using Kalshi's API shall be considered a binding acceptance of this Agreement."
  - §3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange;
    all other usages are disallowed".
  - §3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API
    except for purposes of facilitating your own trading on Kalshi. You may not share such data or
    content with third parties in any manner without prior written authorization from Kalshi."
  - §3.5 prohibits access "for any other benchmarking or competitive purposes".
  - §6.4 requires prior written approval for public statements about Kalshi or its API.
  - The API docs (<https://docs.kalshi.com/welcome>) say that continuing to use the API binds the user
    to this agreement.
  - A newer version, dated 2026-07-01, exists on kalshi.com. Its page returned HTTP 429 to us, so it
    has not been read.

What this means here:

1. **Nothing Kalshi-derived goes in `site/` or git.** That covers prices, quotes, gaps, `arb.json`, and
   the `stats.arbitrage` numbers derived from them. The dashboard's `kalshi` timeline field and
   `<eid>/arb.json` stay null or absent until Kalshi grants written permission.
2. **No Kalshi history is downloaded or stored, and the election-night recorder is not run.**
   Downloading and storing Kalshi history for research is itself outside §3/§3.1, so the project
   decided on 2026-10-28 to skip it. The code exists so it can run once Kalshi gives written
   permission (next section). `analysis/arb_history.py` runs its cross-venue part only with
   `--kalshi-permission`. Without that flag it runs the Polymarket-only check (below).
3. **No Kalshi wording in git.** The Data Terms count "market descriptions" as Kalshi Data, so the pairs
   files and these docs never quote Kalshi titles or rules. Each pair keeps the ticker, a short
   paraphrase of how Kalshi resolves it, written by us (`kalshi_resolution`), and links to the market
   page and contract terms. Test fixtures are built by hand; no saved Kalshi API responses are in git.

### Asking Kalshi for permission

1. **Where to write.** Email <mailto:institutional@kalshi.com>, the contact on Kalshi's data page
   <https://institutional.kalshi.com/data>, or use its data-only application form
   <https://institutional.kalshi.com/application?type=data-only>.
2. **What to ask for, in writing**, naming the Developer Agreement §3/§3.1/§6.4 and the Data Terms §II:
   - (a) programmatic, unauthenticated download of public market data for named election events —
     1-min candlesticks, trades, and order-book snapshots every 30 s on election nights, at ≤ 10
     requests/s;
   - (b) storage in a private research archive;
   - (c) public, non-commercial display on the project's GitHub Pages site of derived series and
     statistics (Kalshi P(A) per minute, cross-venue price gaps, arbitrage counts and fillable size),
     attributed to Kalshi, without redistributing raw rows;
   - (d) the short rule quotations in `configs/pairs/*.toml`.
3. **What to include.** The repository URL, the site URL, the purpose (does public vote-count
   information get priced into prediction markets; cross-venue consistency), and a contact.
4. **If permission is granted:**
   - keep the written permission with the project records and cite it here;
   - run `analysis/arb_history.py --kalshi-permission br2026r1 br2026r2 il2026`;
   - install the recorder unit (see "Election-night recording");
   - let exporters fill the `kalshi` fields — only within the scope granted.

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

**Which elections have markets on both venues** (2026-10-28):

| election | pairs file | pairs | races with both venues |
|---|---|---|---|
| Brazil 2026, first round | `br2026r1.toml` | 46 | first-round winner, 2nd and 3rd place, outright win, whole-election winner |
| Brazil 2026, runoff | `br2026r2.toml` | 31 | winner; runoff states AL, AP, AM, MG, PA, RR, TO (Kalshi lists only these 7); São Paulo; vote abroad |
| Brazil 2026, governors | `br2026r2gov.toml` | 10 | Federal District only |
| Israel 2026 | `il2026.toml` | 69 | next PM, most seats, 2nd/3rd/4th place, bloc with most seats, lists winning a seat |
| US midterms 2026 | `us2026.toml` | 58 | House, Senate, CA gov, and senate/governor in TX, OH (special), GA, AK, ME, MI, IA, KS |
| Taipei / New Taipei mayor 2026-11-28 | `tw2026.toml` | 5 | Taipei, New Taipei |
| France 2027 presidential | `fr2027.toml` | 104 | winner, who advances, 1st/2nd/3rd in round 1, on the ballot, outright first-round win |
| Spain general election 2026-11-29 | `es2026.toml` | 13 | most seats, 2nd/3rd, next PM, PP+Vox ≥ 50% |

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
- **Minute history** (`arb.minute_arb`; contract `arb.json` rows) is an approximation.
  - Kalshi side: the 1-min candle's closing best bid/ask, forward-filled, assigned to the minute at
    which the candle closes, so there is no look-ahead.
  - Polymarket side: Polymarket keeps no quote history, so its asks are the 1-min CLOB price ± h,
    with h ∈ {0.005, 0.01, 0.02}, all reported.
  - The fee for each leg is per unit and not cent-rounded.
- **Fillable** (`arb.fillable`) uses no approximation. In each minute, the taker fills on each leg show
  prices at which that side was really available:
  - Polymarket: a taker BUY of the token at p, or a taker SELL of the other token at q, which offers
    this side at 1 − q;
  - Kalshi: `taker_side` at that side's price.

  These are walked against each other at cost < $1 after fees. Volume others took bounds what one
  more taker could have had; it was not free size.

## Results so far: Polymarket-only (no Kalshi data used)

Produced by `uv run python analysis/arb_history.py br2026r2 br2026r1 il2026` on 2026-10-28. Window:
12:00–12:00 UTC around each count. Evidence: in-sample and descriptive; nothing was fitted. Outputs
are in `results/arb/<eid>/pm_only_*` (gitignored).

**Method.** Within Polymarket, a race listed as two markets ("A wins", "B wins") can be bought as a
package: NO_A + NO_B pays at least $1 in any race, and YES_A + YES_B pays $1 when exactly one of the
two must win (the runoff). Fills from the two markets count as one package only if they fall in the
same short time bucket (1 s, 10 s or 1 min) at traded prices that cost below $1 after fees.

| night | races | combinations | fills within 1 s | within 10 s | within 1 min |
|---|---|---|---|---|---|
| Brazil runoff 2026-10-25 | 10 (national + 9 state/abroad) | YES+YES, NO+NO | $1.50M of packages, $11.9k net | $2.64M, $18.8k net | $4.22M, $45.8k net |
| Brazil first round 2026-10-04 | 4 (winner, 2nd place, whole election) | NO+NO only | $71.6k, $307 net | $98.0k, $436 net | $166.6k, $839 net |
| Israel 2026-10-27 | 0 | — | no race has a Polymarket market for each of two sides | | |

Notes on the runoff:

- **Almost all of it is the national race.** At 1 s it is $1.498M of the $1.501M, with 73% of it NO+NO.
- **It is concentrated in the count.** Of the $11.9k net at 1 s, $7.9k came between 22:00 and 23:00
  UTC and $3.4k between 23:00 and 24:00.
- **Edges are small.** The edge per package has a median of 0.3¢, a 90th percentile of 1.2¢ and a
  maximum of 7.3¢. That is the two markets lagging each other within a second while the count moved
  the price.
- **The quote approximation overstates the opportunity.** It counts 970 race-minutes with a positive
  net edge at h = 0.01 (2 188 at h = 0.005, 371 at h = 0.02), with a maximum of 22¢. Of the 970, 963
  are in thin state and abroad markets, where one-minute prices go stale; the national race has 7.
  Treat the traded-price numbers as the measurement.

**Fees.** Runoff markets were fee-free (`configs/night_r2_v1.toml`). The first-round markets carry
Polymarket's current 0.04 politics rate. The rate in force on 2026-10-04 has not been verified.

**Interpretation.** Volume others took bounds what one more taker could have had. It is not free size:
each package was assembled from two separate takers' fills. Kalshi cross-venue numbers do not exist
(licence section).

## Commands

```
uv run brunder kalshi search 'brazil|israel'              # find Elections series
uv run brunder kalshi event KXBRPRES-26                   # markets, quotes, volume
uv run python analysis/arb_pairs.py                       # regenerate configs/pairs/*.toml
uv run python analysis/arb_history.py br2026r1 br2026r2 il2026   # Polymarket-only → results/arb/<eid>/
# only with Kalshi's written permission:
uv run python analysis/arb_history.py --kalshi-permission br2026r1 br2026r2 il2026
uv run brunder kalshi record us_midterms_2026 --pairs configs/pairs/us2026.toml --interval 30 --until 2026-11-04T18:00Z
```

## Election-night recording (only with Kalshi's permission; not deployed)

`brunder kalshi record <tag>` polls the bulk order-book endpoint every `--interval` seconds, in chunks
of 50 tickers paced 0.1 s apart. The tickers come from `--ticker`, from every open market of
`--event`, or from a pairs file via `--pairs`.

It writes in `brunder snap`'s layout:

- `data/raw/snap/<tag>/kalshi_books/<UTC time>.json` holds `{ticker: orderbook_fp}`, written only when
  the books changed;
- each write appends one line to `<tag>/index.jsonl`.

So it can run next to `brunder snap <tag>` with the same tag. With permission, a unit modelled on
`deploy/brunder-snap-us.service` (same `[Unit]`/`[Service]` settings and timer) would use:

```
ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder kalshi record us_midterms_2026 --pairs configs/pairs/us2026.toml --interval 30 --until 2026-11-04T18:00Z
```
