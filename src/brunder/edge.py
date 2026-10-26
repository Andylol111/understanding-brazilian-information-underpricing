"""Information edge: model probability P(t) vs. market price p(t).

edge(t) = P(t) - p(t). The underpricing hypothesis says that public, cheaply computable
projections (the count itself, polls aggregates) move P before p, i.e. the market
incorporates the information with a lag and with too little weight.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def align(model: pd.Series, market: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """Put model P and market price on one as-of grid (last value carried forward, no look-ahead)."""
    m = model.sort_index()
    k = market.set_index("time")["price"].sort_index()
    grid = pd.date_range(max(m.index[0], k.index[0]).ceil(freq), min(m.index[-1], k.index[-1]), freq=freq)
    df = pd.DataFrame({
        "P": m.reindex(m.index.union(grid)).ffill().reindex(grid),
        "p": k.reindex(k.index.union(grid)).ffill().reindex(grid),
    })
    df["edge"] = df["P"] - df["p"]
    return df.dropna()


def crossing_times(s: pd.Series, levels=(0.6, 0.75, 0.9, 0.95, 0.99)) -> pd.Series:
    """First time the series stays at/above each level for the rest of the sample."""
    out = {}
    for lv in levels:
        below = s < lv
        last_below = below[below].index.max() if below.any() else None
        after = s.index[s.index > last_below] if last_below is not None else s.index
        out[lv] = after[0] if len(after) else pd.NaT
    return pd.Series(out, name=s.name)


def information_lag(df: pd.DataFrame, max_lag: int = 120) -> pd.DataFrame:
    """corr(Δp_t, ΔP_{t-L}) for L in [-max_lag, max_lag] grid steps. Peak at L>0 ⇒ market lags model."""
    dP, dp = df["P"].diff(), df["p"].diff()
    rows = [{"lag": L, "corr": dp.corr(dP.shift(L))} for L in range(-max_lag, max_lag + 1)]
    return pd.DataFrame(rows).set_index("lag")


def scores(df: pd.DataFrame, outcome: int) -> pd.DataFrame:
    """Brier and log score of model vs. market over the aligned window (lower is better)."""
    eps = 1e-6
    out = {}
    for col in ("P", "p"):
        x = df[col].clip(eps, 1 - eps)
        out[col] = {"brier": float(((x - outcome) ** 2).mean()),
                    "logloss": float(-(outcome * np.log(x) + (1 - outcome) * np.log(1 - x)).mean())}
    return pd.DataFrame(out).rename(columns={"P": "model", "p": "market"})


def kelly(P: float, cost: float) -> float:
    """Full-Kelly fraction of bankroll for a binary contract paying 1, bought at `cost`."""
    return max((P - cost) / (1 - cost), 0.0) if cost < 1 else 0.0


def backtest(df: pd.DataFrame, outcome: int, threshold: float = 0.05, half_spread: float = 0.005,
             fee: float = 0.0, stake: float = 1.0, cooldown: str = "5min") -> tuple[pd.DataFrame, dict]:
    """Buy YES when P - p > threshold, NO when p - P > threshold; hold to resolution.

    Execution at p ± half_spread plus proportional `fee`; `stake` dollars per signal; at most one
    entry per `cooldown`. Capacity is NOT modelled here — cap stakes with traded volume
    (microstructure.capacity) before quoting any dollar edge.
    """
    trades, last = [], None
    cd = pd.Timedelta(cooldown)
    for t, r in df.iterrows():
        if last is not None and t - last < cd:
            continue
        if r.edge > threshold:
            side, cost, win = "YES", r.p + half_spread, outcome == 1
        elif -r.edge > threshold:
            side, cost, win = "NO", (1 - r.p) + half_spread, outcome == 0
        else:
            continue
        cost = min(cost * (1 + fee), 0.999)
        shares = stake / cost
        trades.append({"time": t, "side": side, "P": r.P, "p": r.p, "cost": cost,
                       "pnl": shares * (1.0 if win else 0.0) - stake})
        last = t
    tr = pd.DataFrame(trades)
    summary = {"n": len(tr), "pnl": float(tr["pnl"].sum()) if len(tr) else 0.0,
               "roi": float(tr["pnl"].sum() / (stake * len(tr))) if len(tr) else 0.0,
               "hit_rate": float((tr["pnl"] > 0).mean()) if len(tr) else np.nan}
    return tr, summary
