# Who moves Polymarket prices in the 2026 Senate/governor markets (`analysis/us_wallets.py`)

Display and analysis only; nothing here is registered or feeds a frozen config. Output:
`site/data/us2026/hub/wallets.json` (built 2026-10-29, 0.5 MB).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_wallets.py [fetch|build]

## Sources and licence

Polymarket's public, unauthenticated APIs only (polymarket.com robots.txt disallows nothing; data-api has none;
requests paced, 4 threads, retry/backoff):
Gamma `/events` (markets, resolutions), data-api `/trades` (fills, all participants and taker-only), `/holders`
(top 500 holders per token), `/oi` (open interest), `/activity` (first activity of a wallet, ascending, limit 1).
Public market data. No on-chain node, no Kalshi data (see below). Raw fills and first-activity dates are cached under
`$BRUNDER_DATA/raw/polymarket/wallets` and never published.

## Privacy

Aggregate and pseudonymous only. Wallets are Polymarket proxy-wallet hashes. The site file contains no addresses,
usernames, pseudonyms or per-wallet rows (a test fails on any 40-hex address); profile fields returned by the API are
dropped at download. Nothing tries to identify, geolocate or link a wallet to a person. **Location and identity of
traders are not observable from these data and are not pursued.**

**Kalshi** publishes no trader-level data: its public API exposes aggregate volume, open interest, candles, the
order book and anonymous trade prints, never who traded. Everything below is therefore Polymarket-only.

## Coverage

- Live: all 71 Senate/governor markets of the race registry (the Democratic-side Yes token as in `markets.json`),
  full fill history to 2026-10-29. House: not covered (no House markets in the race registry; skipped for budget).
- Seasoning universe: 2,257 resolved binary political markets (Gamma tag `politics`, closed 2023-01-01 .. 2024-10-21,
  volume $10k–$3M; the few larger nomination markets were skipped for download cost) + 92 Democratic-side 2024
  Senate (33) / governor (11) / presidential state (48) markets. 2.2 M fill rows.
- Data-api fills cover a median ~0.49 of Gamma's `volume` field summed over both sides, i.e. essentially full
  coverage; a few early-2024 markets are thin (min 0.06), probably fills before the data-api's history.

## Definitions

- **YES flow**: YES-share change in YES-equivalent dollars: buy YES +s·p, sell YES −s·p, buy NO −s·(1−q), sell NO
  +s·(1−q) (a NO share is a short YES share).
- **Size buckets**: taker orders (taker-only fills grouped by transaction and wallet), by dollars paid: small < $100,
  medium $100–1k, large ≥ $1k. Net = taker YES flow (aggressor side).
- **Realised PnL** per wallet and resolved market: cash from all its fills (maker and taker) + final shares × payout.
  Exact for a binary market: splits, merges and redemptions net out because YES + NO = 1 (tested). Fees ignored
  (≈0 on these markets). Neg-risk conversions across markets of one event are not in fills (small error).
- **Seasoned**: total realised PnL > 0 over ≥ 3 resolved markets of the seasoning universe. 2026: 12,022 of 34,069
  wallets with ≥ 3 markets (107,712 traded). 2024 test: pre-window universe only (2,246 markets with fills), 9,615
  seasoned. No look-ahead: the 2024 set uses markets resolved before 22 Oct 2024.
- **Concentration**: top-10 share and HHI (0–10,000) of YES/NO token holdings over max(open interest, sum of the
  500 listed holders).
- **Wallet age**: days since first Polymarket activity for wallets with ≥ $100 traded in the race in the last 30 days.
- **Price impact** (Kyle's λ): OLS of the bar's YES-price change (0–1) on net taker YES flow ($1k), hourly and daily
  bars on a calendar grid (zero-flow bars included, price carried forward), HC1 t.
- **Lead/lag**: lead = corr(flow_t, Δp_t+1), follow = corr(Δp_t, flow_t+1), for large-order and seasoned flow.

## 2026 results (71 markets, 2026-10-29)

- Concentration is high: median top-10 share of the YES token 0.84 (HHI 1,276); NO token 0.94. Even the deepest
  market (TX-Sen, $3.0M) has top-10 = 0.31 (YES) / 0.78 (NO).
- Thin participation: median 271 wallets ever, 119 active in the last 30 days (max TX-Sen 3,950 / 2,412).
- Mostly established wallets: median age of active wallets 279 days; median 5% under 30 days (max 30%).
- Seasoned wallets: median 12 per market, 16% of gross traded (max 59%).
- Price impact: median λ = 0.011 per $1k (1.1 pp per $1k net taker flow, hourly), 0.05–0.1 pp per $1k in the
  $1M+ markets; t ≈ 3.5 median, R² small (flow explains little of hourly moves).
- Large takers were net Dem-YES buyers in most markets (median +$5k; 52 markets with large orders); small takers
  roughly flat to net sellers.
- Leading: none. Pooled over races, corr(flow_t, Δp_t+1) ≈ 0 for large and seasoned flow at 1h and 1d (medians
  0.000–0.003, volume-weighted ≤ 0.022); seasoned daily flow leads more than it follows in 62% of races, large
  flow in 37%. No sign that either group moves ahead of price.

## Historical check: 2024 (seasoned flow in the final two weeks vs outcome, price as offset)

85 resolved Democratic-side 2024 state markets (48 presidential, 30 Senate, 7 governor; 7 had no fills in the
window/24h before). x = seasoned wallets' net YES-equivalent $ over 22 Oct – 5 Nov 11:00 UTC (before any poll
closed) / market gross $ in the window; price p = YES-equivalent VWAP of the final 24 h before the cutoff.
Model: dem_won ~ α + β·x + offset(logit p).

| sample | n | β (SE) | p | per SD of x (0.26) | LOO log loss: price / +α / +α+βx |
|---|---|---|---|---|---|
| all | 85 | −2.34 (2.27) | 0.30 | −0.62 log-odds | 0.129 / 0.116 / 0.111 |
| competitive (0.05 < p < 0.95) | 31 | −2.75 (3.02) | 0.36 | — | 0.322 / 0.277 / 0.273 |

Seasoned flow did **not** predict outcomes beyond price: the coefficient is not significant and has the *wrong*
sign. Most of the log-loss gain over price alone comes from the intercept α ≈ −1.0 (the 2024 cycle-wide shift to
the right that prices under-priced, absorbed by one parameter fit on the same cycle — not a usable edge). The sign of
x matched outcome − price in 58% of 71 markets with |x| > 0.01, but seasoned wallets were net Dem sellers on
average (mean x = −0.14), so this mostly restates the common shift. Markets within a cycle are strongly correlated,
so the effective n is far below 85; treat the result as "no detectable edge", not as evidence of a negative one.
Market Brier score in the sample: 0.035.

## Known limits

Holder lists are capped at 500 per token; open interest is used as the denominator. Seasoning excludes markets above
$3M and pre-2023 markets (2022 Polymarket markets have no data-api fills). Wallets can hold several proxies and
size buckets are per transaction, so "large" understates split orders. Correlations use calendar bars where most
hours have no trade in small markets.
