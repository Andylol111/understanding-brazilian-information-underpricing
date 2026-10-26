"""Data verification run before any study: is the price path point-in-time, and are labels clean?

price_semantics: for a seeded sample of markets per category, compare every prices-history point
(t, p) with the last trade at or before t and the first trade after t. If points were labelled
with later information, p would track the trade after t more closely than the trade before.
Label conflicts are computed in studies.build_observations (post-close price contradicting the
recorded outcome) and those markets are excluded from every study.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from . import _http, universe
from .paths import ROOT


async def _one(client, sem, m) -> dict | None:
    f = universe.DIR / "prices" / f"{m.condition_id}.parquet"
    if not f.exists():
        return None
    px = pd.read_parquet(f)
    lo, hi = m.scheduled - pd.Timedelta(days=1), m.scheduled + pd.Timedelta(days=1)
    px = px[(px["time"] >= lo) & (px["time"] <= hi)]
    if len(px) < 20:
        return None
    async with sem:
        try:
            fills, _ = await universe._trades_window(client, m.condition_id, int(lo.timestamp()), int(hi.timestamp()), 20)
        except RuntimeError:
            return None
    if len(fills) < 20:
        return None
    tr = pd.DataFrame(fills)
    tr["p_yes"] = np.where(tr["asset"] == m.yes_token, tr["price"].astype(float), 1 - tr["price"].astype(float))
    tr = tr.sort_values("timestamp")
    T = tr["timestamp"].to_numpy()
    P = tr["p_yes"].to_numpy()
    q = ((px["time"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1s")).to_numpy()
    i = np.searchsorted(T, q, side="right")
    ok = (i > 0) & (i < len(T))
    before, after = P[np.clip(i - 1, 0, None)][ok], P[np.clip(i, None, len(T) - 1)][ok]
    p = px["price"].to_numpy()[ok]
    return {"category": m.category, "condition_id": m.condition_id, "points": int(ok.sum()),
            "mad_last_trade_before": float(np.mean(np.abs(p - before))),
            "mad_first_trade_after": float(np.mean(np.abs(p - after)))}


async def _price_semantics(per_category: int, seed: int) -> pd.DataFrame:
    _, mk = universe.load()
    mk = mk[mk["volume"] >= 5e4].dropna(subset=["scheduled"])
    sample = mk.sample(frac=1, random_state=seed).groupby("category").head(per_category)
    sem = asyncio.Semaphore(8)
    async with _http.aclient() as client:
        res = await asyncio.gather(*(_one(client, sem, m) for m in sample.itertuples()))
    return pd.DataFrame([r for r in res if r])


def price_semantics(per_category: int = 15, seed: int = 0) -> pd.DataFrame:
    d = asyncio.run(_price_semantics(per_category, seed))
    out = ROOT / "results" / "verification"
    out.mkdir(parents=True, exist_ok=True)
    d.to_csv(out / "price_semantics_markets.csv", index=False)
    s = d.groupby("category").agg(markets=("points", "size"), points=("points", "sum"),
                                  mad_before=("mad_last_trade_before", "median"),
                                  mad_after=("mad_first_trade_after", "median"),
                                  share_before_closer=("mad_last_trade_before",
                                                       lambda x: float((x <= d.loc[x.index, "mad_first_trade_after"]).mean())))
    s.to_csv(out / "price_semantics.csv")
    return s
