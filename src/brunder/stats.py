"""Inference utilities. Markets inside one event are dependent (negative-risk siblings, same news),
so every interval resamples whole events (cluster bootstrap)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd


def cluster_bootstrap(df: pd.DataFrame, stat: Callable[[pd.DataFrame], float], cluster: str = "event_id",
                      B: int = 2000, seed: int = 0) -> dict:
    """Point estimate, percentile 95% CI and one-sided bootstrap p-values for stat(df)."""
    est = float(stat(df))
    codes, uniq = pd.factorize(df[cluster])
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(uniq) + 1))
    groups = [order[bounds[i]:bounds[i + 1]] for i in range(len(uniq))]
    rng = np.random.default_rng(seed)
    draws = np.empty(B)
    for b in range(B):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(uniq), len(uniq))])
        draws[b] = stat(df.iloc[idx])
    draws = draws[np.isfinite(draws)]
    return {"estimate": est, "lo95": float(np.percentile(draws, 2.5)), "hi95": float(np.percentile(draws, 97.5)),
            "p_greater": float(max(np.mean(draws <= 0), 1 / B)),      # H1: stat > 0
            "p_less": float(max(np.mean(draws >= 0), 1 / B)),         # H1: stat < 0
            "n": len(df), "clusters": len(uniq)}


def mean_of(col: str) -> Callable[[pd.DataFrame], float]:
    return lambda d: float(d[col].mean())


def slope_of(y: str, x: str) -> Callable[[pd.DataFrame], float]:
    def f(d: pd.DataFrame) -> float:
        xv = d[x].to_numpy(float)
        if np.ptp(xv) == 0:
            return np.nan
        return float(np.polyfit(xv, d[y].to_numpy(float), 1)[0])
    return f


def holm(p: pd.Series) -> pd.Series:
    """Holm–Bonferroni adjusted p-values (family-wise error control)."""
    p = p.astype(float)
    order = p.sort_values()
    m = len(p)
    adj = np.maximum.accumulate([(m - i) * v for i, v in enumerate(order.to_numpy())])
    return pd.Series(np.minimum(adj, 1.0), index=order.index).reindex(p.index)


def bh(p: pd.Series) -> pd.Series:
    """Benjamini–Hochberg adjusted p-values (false-discovery-rate control)."""
    p = p.astype(float)
    order = p.sort_values()
    m = len(p)
    raw = order.to_numpy() * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(raw[::-1])[::-1]
    return pd.Series(np.minimum(adj, 1.0), index=order.index).reindex(p.index)
