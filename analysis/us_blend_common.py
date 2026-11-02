"""Shared helpers of the blend study (docs/us_blend.md): moment mixing, LOCO weight, paired bootstrap (same as analysis/us_benchmark.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import ndtr

B = 20000
SEED = 20261103
GRID = np.round(np.arange(0, 1.0001, 0.1), 1)
TEST_COLS = ["component", "test", "class", "n", "mean_delta", "lo", "hi", "p", "cycles_pos", "cycles_eval"]


def crps_normal(y, mu, sd):
    z = (y - mu) / sd
    pdf = np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi)
    return sd * (z * (2 * ndtr(z) - 1) + 2 * pdf - 1 / np.sqrt(np.pi))


def mix(mu1, sd1, mu2, sd2, w):
    mu = w * mu1 + (1 - w) * mu2
    var = w * sd1**2 + (1 - w) * sd2**2 + w * (1 - w) * (mu1 - mu2) ** 2
    return mu, np.sqrt(var)


def loco_weight(d: pd.DataFrame, cyc: str, loss) -> pd.Series:
    """weight per row chosen on the other cycles; loss(w, sub) -> mean loss on sub."""
    out = pd.Series(np.nan, index=d.index)
    for c in d[cyc].unique():
        tr = d[d[cyc] != c]
        best = min(GRID, key=lambda w: loss(w, tr))
        out[d[cyc] == c] = best
    return out


def boot_delta(delta, cyc, rng):
    N = len(delta)
    tot = np.zeros(B)
    for c in np.unique(cyc):
        x = delta[cyc == c]
        idx = rng.integers(0, len(x), (B, len(x)))
        tot += x[idx].sum(1)
    m = tot / N
    p = 2 * min((m <= 0).mean(), (m >= 0).mean())
    return float(delta.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975)), float(min(max(p, 1 / B), 1.0))


def cycle_pos(delta, cyc, min_n=5):
    pos = tot = 0
    for c in np.unique(cyc):
        x = delta[cyc == c]
        if len(x) >= min_n:
            tot += 1
            pos += int(x.mean() > 0)
    return pos, tot


def test_row(component, test, cls, delta, cyc, rng):
    delta, cyc = np.asarray(delta, float), np.asarray(cyc)
    if len(delta) < 30:
        return {"component": component, "test": test, "class": cls, "n": len(delta), "p": np.nan}
    m, lo, hi, p = boot_delta(delta, cyc, rng)
    pos, tot = cycle_pos(delta, cyc)
    return {"component": component, "test": test, "class": cls, "n": len(delta), "mean_delta": m, "lo": lo, "hi": hi, "p": p,
            "cycles_pos": pos, "cycles_eval": tot}
