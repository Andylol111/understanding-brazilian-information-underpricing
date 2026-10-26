"""Between-rounds projection: runoff share from first-round municipal results and vote transfers.

Per municipality i with first-round valid votes T_i split into the two runoff candidates' shares
(a_i, b_i) and everyone else (o_i = 1 − a_i − b_i), the runoff two-party share of candidate A is

    s2_i = α·a_i + β·b_i + θ·o_i

α = share of A's first-round voters who vote A again, β = share of B's first-round voters who switch
to A, θ = share of eliminated candidates' voters who go to A (all among voters who cast a valid
runoff vote). Fitted by bounded, T-weighted least squares across municipalities (ecological
regression), and applied out of sample: transfers estimated on cycle c project cycle c + 4 from its
first round only — information available on first-round night.

Baseline ("proportional"): s2_i = a_i / (a_i + b_i), i.e. others split like the top two.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from . import tse_open

# runoff pairs (A = PT candidate, B = opponent) by election year
PAIRS = {2014: ("13", "45"), 2018: ("13", "17"), 2022: ("13", "22"), 2026: ("13", "22")}


@dataclass(frozen=True)
class Transfers:
    alpha: float
    beta: float
    theta: float
    year: int


def municipal(year: int, r1: pd.DataFrame | None = None, r2: pd.DataFrame | None = None) -> pd.DataFrame:
    """First-round shares (a, b, o), first-round valid T, and runoff share s2 per municipality.
    r1/r2 override the open-data source (2026 comes from the archived live feed)."""
    A, B = PAIRS[year]
    if r1 is None or r2 is None:
        d = tse_open.votes_by_municipality(year)
        r1, r2 = d[d["round"] == 1], d[d["round"] == 2]
    g1 = r1.pivot_table(index=["uf", "cd_mun"], columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    T = g1.sum(axis=1)
    out = pd.DataFrame({"T": T, "a": g1[A] / T, "b": g1[B] / T}).dropna()
    out["o"] = 1 - out["a"] - out["b"]
    g2 = r2.pivot_table(index=["uf", "cd_mun"], columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    out["s2"] = (g2[A] / (g2[A] + g2[B])).reindex(out.index)
    out["V2"] = (g2[A] + g2[B]).reindex(out.index)
    return out[out["T"] > 0].reset_index()


def fit(m: pd.DataFrame, year: int) -> Transfers:
    m = m.dropna(subset=["s2"])
    w = np.sqrt(m["T"].to_numpy())
    X = m[["a", "b", "o"]].to_numpy() * w[:, None]
    y = m["s2"].to_numpy() * w
    r = lsq_linear(X, y, bounds=(0, 1))
    return Transfers(*r.x, year=year)


def project(m: pd.DataFrame, t: Transfers | None) -> pd.Series:
    """Projected runoff share per municipality; t=None → proportional baseline."""
    if t is None:
        return m["a"] / (m["a"] + m["b"])
    return (t.alpha * m["a"] + t.beta * m["b"] + t.theta * m["o"]).clip(0, 1)


def national(m: pd.DataFrame, s: pd.Series, weights: str = "T") -> float:
    return float((s * m[weights]).sum() / m[weights].sum())
