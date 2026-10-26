"""State-level count nowcast from per-state partial results.

The modelled quantity x is either a vote share (runoff: A / valid, win if x > ½) or a margin
(first-round plurality: (A − B) / valid, win if x > 0); callers pass `a` as A votes or A − B votes
and set Params.threshold. Per state u with prior x0_u, expected valid votes V_u, counted valid n_u,
observed x̂_u = a_u / n_u and sections-counted fraction f_u:

  national swing     δ   = Σ_{domestic} n_u (x̂_u − x0_u) / Σ n_u
  state swing        δ_u = λ_u (x̂_u − x0_u) + (1 − λ_u) δ,   λ_u = n_u / (n_u + κ)
  final state value  X_u = f_u x̂_u + (1 − f_u)(x0_u + δ_u)
  national           X   = Σ w_u X_u,   w_u = V_u / Σ V

Uncertainty of the uncounted votes, w̃_u = w_u (1 − f_u):
  state-specific     Σ w̃_u² [ (1 − λ_u) σ²_swing + λ_u σ²_within ]   (independent across states)
  shared national    (Σ w̃_u)² τ²                                      (common to every state)
Defaults come from data other than any count being scored: σ_swing = 6.6 pt is the between-state
dispersion of the PT first-round share swing 2018 → 2022 (double it for a margin); σ_within = σ_swing
(conservative); τ = 1 pt (not yet calibrated on a state-level path).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class Params:
    kappa: float = 200_000.0     # pseudo-votes: a state needs ~this many counted votes to move off δ
    sigma_swing: float = 0.066
    sigma_within: float = 0.066
    tau: float = 0.01
    threshold: float = 0.5       # 0.5 for a share, 0.0 for a margin


def project(states: pd.DataFrame, prior: pd.DataFrame, p: Params = Params()) -> tuple[pd.DataFrame, float]:
    """Per-state table (X_u = 'S', sd, p_win) and the pooled domestic swing δ.
    states: uf, n (counted valid), a (numerator), f (sections fraction). prior: uf, s0, V."""
    d = prior.merge(states, on="uf", how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    d["o"] = np.where(d["n"] > 0, d["a"] / d["n"].where(d["n"] > 0), np.nan)
    dom = d[(d["uf"] != "zz") & (d["n"] > 0)]
    delta = float(np.average(dom["o"] - dom["s0"], weights=dom["n"])) if len(dom) else 0.0
    d["dev"] = (d["o"] - d["s0"]).fillna(delta)
    d["lam"] = d["n"] / (d["n"] + p.kappa)
    d["delta_u"] = d["lam"] * d["dev"] + (1 - d["lam"]) * delta
    d["S"] = np.where(d["n"] > 0, d["f"] * d["o"].fillna(0) + (1 - d["f"]) * (d["s0"] + d["delta_u"]), d["s0"] + delta)
    d["var_own"] = (1 - d["f"]) ** 2 * ((1 - d["lam"]) * p.sigma_swing**2 + d["lam"] * p.sigma_within**2)
    d["sd"] = np.sqrt(d["var_own"] + ((1 - d["f"]) * p.tau) ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        d["p_win"] = np.where(d["sd"] > 0, norm.cdf((d["S"] - p.threshold) / d["sd"]), (d["S"] > p.threshold).astype(float))
    return d, delta


def nowcast(states: pd.DataFrame, prior: pd.DataFrame, p: Params = Params()) -> dict:
    """National value of x with state-specific and shared national uncertainty."""
    d, delta = project(states, prior, p)
    w = d["V"] / d["V"].sum()
    S = float((w * d["S"]).sum())
    wt = w * (1 - d["f"])
    var_state = float((wt**2 * ((1 - d["lam"]) * p.sigma_swing**2 + d["lam"] * p.sigma_within**2)).sum())
    var_nat = float(wt.sum() ** 2 * p.tau**2)
    sd = np.sqrt(var_state + var_nat)
    return {"share": S, "sd": sd, "sd_state": np.sqrt(var_state), "sd_national": np.sqrt(var_nat),
            "p_win": float(norm.cdf((S - p.threshold) / sd)) if sd > 0 else float(S > p.threshold),
            "swing": delta, "counted_frac": float(d["n"].sum() / d["V"].sum())}
