"""Polymarket ↔ Kalshi cross-venue arbitrage: fee models, pair configs, book walks, minute history.

A pair is two binary markets on the same proposition A (configs/pairs/<election>.toml). Holding A on
one venue and not-A on the other pays exactly $1 whatever happens — if both resolve the same way.
Resolution rules that differ (sources, deadlines, ties, annulment) break that, so every pair carries
`resolution_differences` and `risk_free`; nothing here is risk-free when `risk_free = false`.

Fees (docs/kalshi.md, with sources):
  Kalshi taker   fee = round_up(M × 0.07 × C × P × (1 − P)), M = series/event fee multiplier;
                 rounded up to $0.000001, then the balance change (C·P + fee) up to the member's
                 precision ($0.01 non-direct, $0.0001 direct) — net: the familiar "round up to the cent".
  Kalshi maker   0 unless the series fee_type includes maker fees (M_maker × 0.0175, i.e. 0.25 × taker).
  Polymarket     fee = C × feeRate × (p(1 − p))^exponent, taker only, USDC, rounded to 5 decimals;
                 feeRate/exponent from the market's own `feeSchedule` (0 when `feesEnabled` is false).

Combinations, with K YES ≡ PM YES (`same_side`):  PM YES + K NO,  PM NO + K YES;
             with K YES ≡ PM NO  (opposite):      PM YES + K YES, PM NO + K NO.
"""

from __future__ import annotations

import math
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

K_TAKER_RATE = 0.07
K_MAKER_RATE = 0.0175
PAIRS_DIR = Path(__file__).resolve().parents[2] / "configs" / "pairs"


# ---------------------------------------------------------------------------------------------- fees

def _ceil(x: float, step: float) -> float:
    """Round x up to a multiple of step, tolerant of float noise (0.07·100·0.25 = 1.7500000000000002)."""
    n = round(x / step, 6)
    return math.ceil(n) * step


def kalshi_fee(price: float, count: float, multiplier: float = 1.0, rate: float = K_TAKER_RATE,
               precision: float = 0.01) -> float:
    """Kalshi fee in dollars for one order filled at a single price (see `kalshi_fee_levels` for a walk)."""
    return kalshi_fee_levels([(price, count)], multiplier, rate, precision)


def kalshi_fee_levels(fills: list[tuple[float, float]], multiplier: float = 1.0, rate: float = K_TAKER_RATE,
                      precision: float = 0.01) -> float:
    """Fee of one order filled across (price, count) levels. Kalshi's fee accumulator makes an order's
    total fee converge to one equivalent fill: model fees summed, trade fee rounded up to $0.000001,
    then the balance change rounded up to `precision` (cost side)."""
    if multiplier == 0 or not fills:
        return 0.0
    notional = sum(p * c for p, c in fills)
    model = sum(multiplier * rate * c * p * (1 - p) for p, c in fills)
    trade_fee = _ceil(model, 1e-6)
    return round(_ceil(notional + trade_fee, precision) - notional, 6)


def pm_fee(price: float, shares: float, rate: float, exponent: float = 1.0) -> float:
    """Polymarket taker fee in USDC (docs.polymarket.com/trading/fees), rounded to 5 decimals."""
    if rate == 0:
        return 0.0
    return round(shares * rate * (price * (1 - price)) ** exponent, 5)


def pm_unit_fee(price, rate: float, exponent: float = 1.0):
    """Per-share Polymarket fee (vectorised, unrounded) for minute-level approximations."""
    return rate * (np.asarray(price) * (1 - np.asarray(price))) ** exponent


def kalshi_unit_fee(price, multiplier: float = 1.0):
    """Per-contract Kalshi model fee (vectorised, before cent rounding: exact for large orders)."""
    return multiplier * K_TAKER_RATE * np.asarray(price) * (1 - np.asarray(price))


@dataclass(frozen=True)
class Fee:
    """One taker leg's fee model. venue 'polymarket': rate = feeSchedule.rate, exponent as listed;
    venue 'kalshi': rate = the series/event fee multiplier M (fee coefficient 0.07·M)."""
    venue: str
    rate: float = 0.0
    exponent: float = 1.0

    def unit(self, price):
        """Per-unit fee before rounding (vectorised): what the marginal unit costs."""
        if self.venue == "polymarket":
            return pm_unit_fee(price, self.rate, self.exponent)
        return kalshi_unit_fee(price, self.rate)

    def total(self, fills: list[tuple[float, float]]) -> float:
        """Fee of one order filled across (price, size) levels, with the venue's rounding."""
        if self.venue == "polymarket":
            return sum(pm_fee(p, q, self.rate, self.exponent) for p, q in fills)
        return kalshi_fee_levels(fills, self.rate)


# ---------------------------------------------------------------------------------------------- pairs

@dataclass
class Pair:
    id: str
    race_id: str
    proposition: str                  # plain statement of A
    pm_market: str                    # Polymarket market slug
    kalshi_ticker: str
    same_side: bool                   # Kalshi YES ≡ Polymarket YES
    pm_event: str = ""
    kalshi_event: str = ""
    kalshi_series: str = ""
    pm_question: str = ""
    pm_yes_token: str = ""
    pm_condition_id: str = ""
    pm_yes_is: str = "a"              # race side of the PM YES token: "a" | "b" | "other" (contract `yes_is`)
    kalshi_yes_is: str = "a"
    pm_fee_rate: float = 0.0
    pm_fee_exponent: float = 1.0
    kalshi_fee_multiplier: float = 1.0
    risk_free: bool = False
    resolution_differences: list[str] = field(default_factory=list)
    pm_rules: str = ""                # Polymarket's resolution text, verbatim
    kalshi_resolution: str = ""       # Kalshi's resolution in our own words (Kalshi text is not reproduced)
    kalshi_url: str = ""

    def pm_fee_model(self) -> Fee:
        return Fee("polymarket", self.pm_fee_rate, self.pm_fee_exponent)

    def k_fee_model(self) -> Fee:
        return Fee("kalshi", self.kalshi_fee_multiplier)


def load_pairs(path: Path | str) -> tuple[dict, list[Pair]]:
    """configs/pairs/<election>.toml → (header, pairs)."""
    d = tomllib.loads(Path(path).read_text())
    fields = Pair.__dataclass_fields__
    pairs = [Pair(**{k: v for k, v in p.items() if k in fields}) for p in d.pop("pair", [])]
    return d, pairs


# ---------------------------------------------------------------------------------------------- matching

_HONORIFIC = {"sen", "rep", "gov", "dr", "mr", "mrs", "ms", "president"}


def norm(s: str) -> str:
    """Comparable label: ASCII lowercase words, parentheticals and honorifics dropped.
    'Sen. Dan Sullivan' → 'dan sullivan'; 'James Talarico (D)' → 'james talarico'."""
    s = re.sub(r"\([^)]*\)", " ", s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _HONORIFIC)


def same_name(a: str, b: str) -> bool:
    """Labels name the same person/party: equal after `norm`, or the shorter one's words all appear in
    the longer one and either it is a single word ('Lula') or the last words agree ('Flavio Bolsonaro'
    vs 'Flávio Nantes Bolsonaro'). 'Dan Sullivan' ≠ 'Daniel J. Sullivan Jr.'."""
    x, y = norm(a).split(), norm(b).split()
    if not x or not y:
        return False
    if x == y:
        return True
    small, big = (x, y) if len(x) <= len(y) else (y, x)
    return set(small) <= set(big) and (len(small) == 1 or small[-1] == big[-1])


def match_outcomes(pm_labels: list[str], k_labels: list[str], aliases: dict[str, str] | None = None
                   ) -> list[tuple[int, int]]:
    """Pair Polymarket and Kalshi outcome labels one-to-one. `aliases` maps a PM label to the Kalshi
    label it must pair with (overrides). A name match is kept only when it is unique both ways, so
    'Bolsonaro' against 'Jair Bolsonaro' and 'Flávio Bolsonaro' pairs with neither."""
    aliases = {norm(k): norm(v) for k, v in (aliases or {}).items()}
    cand: dict[int, list[int]] = {}
    for i, a in enumerate(pm_labels):
        if norm(a) in aliases:
            cand[i] = [j for j, b in enumerate(k_labels) if norm(b) == aliases[norm(a)]]
        else:
            cand[i] = [j for j, b in enumerate(k_labels) if same_name(a, b)]
    used: dict[int, int] = {}
    for js in cand.values():
        if len(js) == 1:
            used[js[0]] = used.get(js[0], 0) + 1
    return [(i, js[0]) for i, js in cand.items() if len(js) == 1 and used[js[0]] == 1]


# ---------------------------------------------------------------------------------------------- books

def pm_levels(book: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Polymarket YES-token CLOB book → (YES asks, NO asks) as (price, shares), cheapest first.
    NO is bought against YES bids at 1 − bid (complementary matching)."""
    asks = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks") or [])
    no = sorted((round(1 - float(x["price"]), 6), float(x["size"])) for x in book.get("bids") or [])
    return asks, no


def kalshi_levels(parsed: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """`kalshi.parse_book` output → (YES asks, NO asks), cheapest first."""
    return sorted(parsed["yes_asks"]), sorted(parsed["no_asks"])


def walk_pair(a_asks: list[tuple[float, float]], b_asks: list[tuple[float, float]], size: float | None,
              fee_a: Fee = Fee("polymarket"), fee_b: Fee = Fee("kalshi", 1.0)) -> dict:
    """Buy two complementary legs (a: usually Polymarket, b: usually Kalshi) level by level, matching sizes.

    size=None: buy every unit whose marginal cost (both prices + both per-unit fees) is below $1, i.e.
    all profitable size. Otherwise buy up to `size` units regardless of profit. Fees are computed on each
    leg's whole order at the end (Kalshi cent rounding included). Returns units, cost (both legs incl.
    fees), avg_cost, net (= units − cost, the locked-in profit) and the marginal cost of the last unit."""
    i = j = 0
    pa = [list(x) for x in a_asks]
    pb = [list(x) for x in b_asks]
    units, fills_a, fills_b, last = 0.0, [], [], np.nan
    while i < len(pa) and j < len(pb):
        x, y = pa[i][0], pb[j][0]
        marg = x + fee_a.unit(x) + y + fee_b.unit(y)
        if size is None and marg >= 1:
            break
        q = min(pa[i][1], pb[j][1], (size - units) if size is not None else math.inf)
        if q <= 0:
            break
        units += q
        fills_a.append((x, q))
        fills_b.append((y, q))
        last = float(marg)
        pa[i][1] -= q
        pb[j][1] -= q
        if pa[i][1] <= 1e-12:
            i += 1
        if pb[j][1] <= 1e-12:
            j += 1
    if not units:
        return {"units": 0.0, "cost": 0.0, "avg_cost": np.nan, "net": 0.0, "marginal": np.nan,
                "fee_a": 0.0, "fee_b": 0.0}
    fa, fb = fee_a.total(fills_a), fee_b.total(fills_b)
    cost = sum(p * q for p, q in fills_a) + sum(p * q for p, q in fills_b) + fa + fb
    return {"units": units, "cost": cost, "avg_cost": cost / units, "net": units - cost, "marginal": last,
            "fee_a": fa, "fee_b": fb}


def combos(pair: Pair) -> list[tuple[str, str, str]]:
    """(name, Polymarket side, Kalshi side) for the pair's two complementary combinations."""
    if pair.same_side:
        return [("pm YES + k NO", "yes", "no"), ("pm NO + k YES", "no", "yes")]
    return [("pm YES + k YES", "yes", "yes"), ("pm NO + k NO", "no", "no")]


def book_arb(pair: Pair, pm_book: dict, k_parsed: dict, size: float | None = None) -> dict:
    """Both combinations of a pair from live books; returns the better one — most locked-in net when
    size is None, else lowest avg cost for `size` units — with `combo` naming the legs."""
    pm_yes, pm_no = pm_levels(pm_book)
    k_yes, k_no = kalshi_levels(k_parsed)
    side = {("pm", "yes"): pm_yes, ("pm", "no"): pm_no, ("k", "yes"): k_yes, ("k", "no"): k_no}
    best = None
    for name, p, k in combos(pair):
        r = walk_pair(side["pm", p], side["k", k], size, pair.pm_fee_model(), pair.k_fee_model())
        r["combo"] = name
        key = r["net"] if size is None else (-r["avg_cost"] if r["units"] else -np.inf)
        if best is None or key > best[0]:
            best = (key, r)
    return best[1]


# ---------------------------------------------------------------------------------------------- minute history

def minute_arb(pair: Pair, pm_price: pd.DataFrame, k_candles: pd.DataFrame, pm_half_spread: float = 0.01,
               start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Per-minute approximate arbitrage from price history (contract `arb.json` rows).

    Polymarket has no historical quotes: its asks are approximated as price ± `pm_half_spread`
    (pm_yes_ask = p + h, pm_no_ask = 1 − p + h, p = CLOB minute price). Kalshi asks are the candle's
    period-close quotes (yes_ask; no_ask = 1 − yes_bid), forward-filled across quiet minutes.
    Rows are on the minute grid, each holding the latest state known at that minute: the PM point
    stamped t → minute floor(t); the Kalshi candle ending at T (close quotes as of T) → minute T."""
    pm = pm_price.assign(t=pd.to_datetime(pm_price["time"], utc=True).dt.floor("min")).groupby("t")["price"].last()
    kc = k_candles.assign(t=pd.to_datetime(k_candles["time"], utc=True).dt.floor("min"))
    kc = kc.groupby("t")[["yes_bid", "yes_ask"]].last()
    lo = max(pm.index.min(), kc.index.min()) if start is None else pd.Timestamp(start)
    hi = min(pm.index.max(), kc.index.max()) if end is None else pd.Timestamp(end)
    grid = pd.date_range(lo.floor("min"), hi.floor("min"), freq="min", tz="UTC")
    df = pd.DataFrame(index=grid)
    df["pm"] = pm.reindex(grid.union(pm.index)).ffill().reindex(grid)
    kq = kc.reindex(grid.union(kc.index)).ffill().reindex(grid)
    # Kalshi quotes of 0 bid / 1 ask mean an empty side: no executable price
    df["k_yes_ask"] = kq["yes_ask"].where(kq["yes_ask"] < 1)
    df["k_no_ask"] = (1 - kq["yes_bid"]).where(kq["yes_bid"] > 0)
    df["pm_yes_ask"] = (df["pm"] + pm_half_spread).where(df["pm"] + pm_half_spread < 1)
    df["pm_no_ask"] = (1 - df["pm"] + pm_half_spread).where(1 - df["pm"] + pm_half_spread < 1)

    ask = {("pm", "yes"): df["pm_yes_ask"], ("pm", "no"): df["pm_no_ask"],
           ("k", "yes"): df["k_yes_ask"], ("k", "no"): df["k_no_ask"]}
    fa, fb = pair.pm_fee_model(), pair.k_fee_model()
    (n1, p1, k1), (n2, p2, k2) = combos(pair)
    c1, c2 = (ask["pm", p] + fa.unit(ask["pm", p]) + ask["k", k] + fb.unit(ask["k", k])
              for p, k in ((p1, k1), (p2, k2)))
    df["cost_a"] = np.fmin(c1, c2)
    df["combo"] = np.where(c1 <= c2, n1, np.where(c2.notna(), n2, None))
    df.loc[df["cost_a"].isna(), "combo"] = None
    df["net_edge"] = 1 - df["cost_a"]
    df.index.name = "t"
    return df.reset_index()


def trade_levels_pm(pm_trades: pd.DataFrame, side: str, yes_token: str) -> pd.DataFrame:
    """Polymarket taker fills → prices at which `side` ('yes'|'no') was demonstrably available:
    a taker BUY of that token at p, or a taker SELL of the other token at q (offer of `side` at 1 − q)."""
    t = pm_trades
    if "is_taker" in t:
        t = t[t["is_taker"]]
    is_yes = t["asset"].astype(str) == str(yes_token)
    want = is_yes if side == "yes" else ~is_yes
    buy = t[want & (t["side"] == "BUY")]
    sell = t[~want & (t["side"] == "SELL")]
    out = pd.concat([buy.assign(px=buy["price"].astype(float)), sell.assign(px=1 - sell["price"].astype(float))])
    return pd.DataFrame({"time": out["time"], "price": out["px"], "size": out["size"].astype(float)})


def trade_levels_kalshi(k_trades: pd.DataFrame, side: str) -> pd.DataFrame:
    """Kalshi fills → prices at which `side` was taken: taker_side == side at that side's price."""
    t = k_trades[k_trades["taker_side"] == side]
    px = t["price"] if side == "yes" else 1 - t["price"]
    return pd.DataFrame({"time": t["time"], "price": px.astype(float), "size": t["count"].astype(float)})


def walk_trades(a: pd.DataFrame, b: pd.DataFrame, fee_a: Fee, fee_b: Fee, window: str = "1min") -> pd.DataFrame:
    """Per `window` bucket, walk the two legs' traded levels (`trade_levels_*` frames) against each other
    (`walk_pair`, size=None). Rows with units > 0: units, USD cost of both legs, locked-in net USD."""
    cols = ["t", "units", "cost_usd", "net_usd", "avg_cost", "a_traded", "b_traded"]
    if a.empty or b.empty:
        return pd.DataFrame(columns=cols)
    a = a.assign(t=pd.to_datetime(a["time"], utc=True).dt.floor(window))
    b = b.assign(t=pd.to_datetime(b["time"], utc=True).dt.floor(window))
    bg = dict(tuple(b.groupby("t")))
    rows = []
    for t, ga in a.groupby("t"):
        gb = bg.get(t)
        if gb is None:
            continue
        r = walk_pair(sorted(zip(ga["price"], ga["size"])), sorted(zip(gb["price"], gb["size"])), None, fee_a, fee_b)
        if r["units"] > 0:
            rows.append({"t": t, "units": r["units"], "cost_usd": r["cost"], "net_usd": r["net"],
                         "avg_cost": r["avg_cost"], "a_traded": float(ga["size"].sum()),
                         "b_traded": float(gb["size"].sum())})
    return pd.DataFrame(rows, columns=cols)


def fillable(pair: Pair, pm_trades: pd.DataFrame, k_trades: pd.DataFrame, yes_token: str,
             window: str = "1min") -> pd.DataFrame:
    """Cross-venue arbitrage actually executable at traded prices, independent of the quote approximation.

    In every `window` bucket, the taker fills on each leg (prices at which that side was demonstrably
    available, `trade_levels_*`) are treated as that bucket's book and walked against each other
    (`walk_trades`): units whose two prices plus both fees cost < $1. Volume others took is a bound on
    what one more taker could have had, not a guarantee (they took it first). a = Polymarket, b = Kalshi."""
    out = [walk_trades(trade_levels_pm(pm_trades, p, yes_token), trade_levels_kalshi(k_trades, k),
                       pair.pm_fee_model(), pair.k_fee_model(), window).assign(combo=name)
           for name, p, k in combos(pair)]
    return pd.concat(out, ignore_index=True)


def pm_complement(price_a: pd.DataFrame, price_b: pd.DataFrame, fee_a: Fee, fee_b: Fee, half_spread: float,
                  start: pd.Timestamp, end: pd.Timestamp, two_way: bool = True) -> pd.DataFrame:
    """Polymarket-only check on two markets of one race ('A wins', 'B wins'). NO_A + NO_B pays at least
    $1 in any race (both pay if a third candidate wins); YES_A + YES_B pays $1 only if exactly one of
    them must win (two_way). Per minute, the cheaper allowed combination's cost incl. fees, with asks
    approximated as price ± half_spread (as in `minute_arb`)."""
    grid = pd.date_range(pd.Timestamp(start).floor("min"), pd.Timestamp(end).floor("min"), freq="min", tz="UTC")

    def on_grid(df):
        s = df.assign(t=pd.to_datetime(df["time"], utc=True).dt.floor("min")).groupby("t")["price"].last()
        return s.reindex(grid.union(s.index)).ffill().reindex(grid)

    pa, pb = on_grid(price_a), on_grid(price_b)
    ask = [(x + half_spread).where(x + half_spread < 1) for x in (pa, pb, 1 - pa, 1 - pb)]
    yy = ask[0] + fee_a.unit(ask[0]) + ask[1] + fee_b.unit(ask[1])
    nn = ask[2] + fee_a.unit(ask[2]) + ask[3] + fee_b.unit(ask[3])
    if not two_way:
        yy = yy * np.nan
    df = pd.DataFrame({"t": grid, "pm_a": pa.values, "pm_b": pb.values, "cost_a": np.fmin(yy, nn).values})
    df["combo"] = np.where(yy.fillna(np.inf).values <= nn.fillna(np.inf).values, "YES A + YES B", "NO A + NO B")
    df.loc[df["cost_a"].isna(), "combo"] = None
    df["net_edge"] = 1 - df["cost_a"]
    return df


def summarize(arb: pd.DataFrame, fills: pd.DataFrame | None = None) -> dict:
    """Contract `stats.arbitrage` fields for one pair (sum across pairs for an election)."""
    pos = arb["net_edge"] > 0
    return {"minutes": int(arb["cost_a"].notna().sum()), "minutes_with_net_arb": int(pos.sum()),
            "max_net_edge": float(arb["net_edge"].max()) if arb["net_edge"].notna().any() else None,
            "fillable_usd": float(fills["cost_usd"].sum()) if fills is not None and len(fills) else 0.0,
            "fillable_net_usd": float(fills["net_usd"].sum()) if fills is not None and len(fills) else 0.0}
