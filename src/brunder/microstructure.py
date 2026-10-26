"""Trade-level diagnostics for manipulation and capacity on Polymarket.

Input: fills from polymarket.trades() — one row per participant per fill (taker and each
maker), with `is_taker` from polymarket.tag_takers. Map them first with `to_yes` to a common
YES-equivalent frame: buying NO at q ≡ selling YES at 1-q.

- Per-wallet positions / PnL / concentration: use all rows (each row is that wallet's own fill).
- Signed order flow (price impact, capacity): use taker rows only; maker rows are the passive
  other side of the same fills and would cancel or double count.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_yes(trades: pd.DataFrame, yes_token: str, invert: bool = False) -> pd.DataFrame:
    """YES-equivalent view of one market. invert=True maps a complementary market into this
    frame (e.g. 'Flávio wins' YES at q ≡ 'Lula wins' YES sold at 1-q in a two-candidate race)."""
    t = trades.copy()
    is_yes = t["asset"] == yes_token
    buy = t["side"] == "BUY"
    p = np.where(is_yes, t["price"], 1 - t["price"])
    s = np.where(is_yes == buy, 1, -1)              # +1 = pushes YES up / long YES
    t["price_yes"] = 1 - p if invert else p
    t["sign"] = -s if invert else s
    t["notional"] = t["size"] * t["price"]
    t["signed_yes_shares"] = t["sign"] * t["size"]
    return t


def concentration(t: pd.DataFrame, by: str | None = None, top: int = 10) -> pd.DataFrame:
    """Per-window wallet concentration: HHI of notional, top-N share, n wallets.

    by: pandas offset alias (e.g. '1h') to compute it through time, None for the whole sample."""
    def one(g: pd.DataFrame) -> pd.Series:
        v = g.groupby("proxyWallet")["notional"].sum().sort_values(ascending=False)
        s = v / v.sum()
        return pd.Series({"notional": v.sum(), "wallets": len(v), "hhi": float((s**2).sum()),
                          f"top{top}_share": float(s.head(top).sum())})
    if by is None:
        return one(t).to_frame().T
    return t.set_index("time").groupby(pd.Grouper(freq=by)).apply(one).dropna()


def wallet_flows(t: pd.DataFrame, outcome: int | None = None) -> pd.DataFrame:
    """Per-wallet net YES position change, gross notional, VWAP, and (given the resolved
    outcome) PnL held to resolution = Σ sign·size·(outcome − price_yes)."""
    t = t.assign(_pv=t["price_yes"] * t["size"])
    g = t.groupby("proxyWallet")
    out = pd.DataFrame({
        "trades": g.size(),
        "gross_notional": g["notional"].sum(),
        "net_yes_shares": g["signed_yes_shares"].sum(),
        "vwap_yes": g["_pv"].sum() / g["size"].sum(),
        "first": g["time"].min(), "last": g["time"].max(),
    })
    if outcome is not None:
        out["pnl"] = (t["signed_yes_shares"] * (outcome - t["price_yes"])).groupby(t["proxyWallet"]).sum()
    return out.sort_values("gross_notional", ascending=False)


def round_trips(t: pd.DataFrame, window: str = "10min", size_tol: float = 0.1) -> pd.DataFrame:
    """Opposite-side, similar-size fills by one wallet within `window`. Catches wash-trade
    candidates but also ordinary market-making churn — interpret together with `self_trades`."""
    w = pd.Timedelta(window)
    flags = []
    for wallet, g in t.sort_values("time").groupby("proxyWallet"):
        rows = g[["time", "sign", "size", "price_yes"]].to_numpy()
        for i in range(len(rows)):
            ti, si, qi, pi = rows[i]
            for j in range(i + 1, len(rows)):
                tj, sj, qj, pj = rows[j]
                if tj - ti > w:
                    break
                if sj == -si and abs(qj - qi) <= size_tol * max(qi, qj):
                    flags.append({"wallet": wallet, "t_open": ti, "t_close": tj, "size": qi,
                                  "p_open": pi, "p_close": pj})
                    break
    return pd.DataFrame(flags)


def self_trades(t: pd.DataFrame) -> pd.DataFrame:
    """Fills where one wallet is on both sides of the same transaction (opposite YES-equivalent
    signs) — direct wash trading. Returns one row per (tx, wallet) with the matched size."""
    x = t.assign(long=t["signed_yes_shares"].clip(lower=0), short=(-t["signed_yes_shares"]).clip(lower=0))
    s = x.groupby(["transactionHash", "proxyWallet"]).agg(
        long=("long", "sum"), short=("short", "sum"), time=("time", "first"), price_yes=("price_yes", "mean"))
    s = s[(s["long"] > 0) & (s["short"] > 0)].copy()
    s["washed_shares"] = s[["long", "short"]].min(axis=1)
    return s.reset_index()


def _takers(t: pd.DataFrame) -> pd.DataFrame:
    return t[t["is_taker"]] if "is_taker" in t else t


def price_impact(t: pd.DataFrame, prices: pd.DataFrame, horizons=("1min", "5min", "30min", "2h")) -> pd.DataFrame:
    """Kyle-λ style regression per horizon on 1-minute buckets: Δp from the end of minute m-1
    to m+h on net signed taker notional in minute m (OLS slope, t-stat).

    A large impact that later reverses (λ_short ≫ λ_long) is the signature of uninformed price
    pushing (manipulation); persistent impact is consistent with informed flow."""
    tk = _takers(t)
    flow = (tk["sign"] * tk["notional"]).groupby(tk["time"].dt.floor("1min")).sum()
    px = prices.set_index("time")["price"].sort_index()
    px = px[~px.index.duplicated()]
    at = lambda ts: px.reindex(px.index.union(ts)).ffill().reindex(ts).to_numpy()
    p0 = at(flow.index - pd.Timedelta("1min"))
    rows = []
    for h in horizons:
        y = at(flow.index + pd.Timedelta(h)) - p0
        x = flow.to_numpy()
        ok = np.isfinite(y)
        X, Y = x[ok], y[ok]
        if len(X) < 3 or X.var() == 0:
            continue
        beta = float(np.cov(X, Y, bias=True)[0, 1] / X.var())
        resid = Y - beta * X - (Y.mean() - beta * X.mean())
        se = float(np.sqrt(resid.var() / (len(X) * X.var())))
        rows.append({"horizon": h, "lambda_per_$10k": beta * 1e4, "t": beta / se if se else np.nan, "minutes": len(X)})
    return pd.DataFrame(rows).set_index("horizon")


def capacity(t: pd.DataFrame, at: pd.Timestamp, window: str = "15min", side: int = 1,
             max_price: float | None = None) -> float:
    """Taker notional that actually traded on `side` (+1 YES-buying) within ±window of `at`,
    optionally only at YES prices ≤ max_price. Proxy for how much of an edge was executable."""
    t = _takers(t)
    w = pd.Timedelta(window)
    g = t[(t["time"] >= at - w) & (t["time"] <= at + w) & (t["sign"] == side)]
    if max_price is not None:
        g = g[g["price_yes"] <= max_price]
    return float(g["notional"].sum())
