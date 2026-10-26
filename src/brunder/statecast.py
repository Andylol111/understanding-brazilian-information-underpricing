"""State-level count nowcast: national share of candidate A from the per-state TSE files.

Why state level: the early national number is dominated by which states report first (2026 R1 at
1.7% counted: composition −4.95 pt vs within-state +0.7 pt), and state files are published before
the national aggregate. Per state u with prior share s0_u, expected valid votes V_u, counted valid n_u,
observed share o_u and sections-counted fraction f_u:

  national swing     δ   = Σ_{domestic} n_u (o_u − s0_u) / Σ n_u
  state swing        δ_u = λ_u (o_u − s0_u) + (1 − λ_u) δ,   λ_u = n_u / (n_u + κ)
  final state share  S_u = f_u o_u + (1 − f_u)(s0_u + δ_u)
  national           S   = Σ w_u S_u,   w_u = V_u / Σ V

Uncertainty of the uncounted votes, w̃_u = w_u (1 − f_u):
  state-specific     Σ w̃_u² [ (1 − λ_u) σ²_swing + λ_u σ²_within ]   (independent across states)
  shared national    (Σ w̃_u)² τ²                                      (one error common to all states:
                                                                         reporting-order / turnout bias)
Defaults are set from data other than any count being scored:
  σ_swing  = 6.6 pt: between-state dispersion of the PT first-round swing, 2018 → 2022.
  σ_within = σ_swing (no ex-ante estimate of within-state early-vs-final error; conservative).
  τ        = 1 pt, as in nowcast.py; not yet calibrated on a state-level count path.
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


def nowcast(states: pd.DataFrame, prior: pd.DataFrame, p: Params = Params()) -> dict:
    """states: uf, n (counted valid), a (A votes), f (sections fraction).
    prior: uf, s0 (prior A share), V (expected valid votes)."""
    d = prior.merge(states, on="uf", how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    d["o"] = np.where(d["n"] > 0, d["a"] / d["n"].where(d["n"] > 0), np.nan)
    dom = d[(d["uf"] != "zz") & (d["n"] > 0)]
    delta = float(np.average(dom["o"] - dom["s0"], weights=dom["n"])) if len(dom) else 0.0
    d["dev"] = (d["o"] - d["s0"]).fillna(delta)
    d["lam"] = d["n"] / (d["n"] + p.kappa)
    d["delta_u"] = d["lam"] * d["dev"] + (1 - d["lam"]) * delta
    d["S"] = np.where(d["n"] > 0, d["f"] * d["o"].fillna(0) + (1 - d["f"]) * (d["s0"] + d["delta_u"]), d["s0"] + delta)
    w = d["V"] / d["V"].sum()
    S = float((w * d["S"]).sum())
    wt = w * (1 - d["f"])
    var_state = float((wt**2 * ((1 - d["lam"]) * p.sigma_swing**2 + d["lam"] * p.sigma_within**2)).sum())
    var_nat = float(wt.sum() ** 2 * p.tau**2)
    sd = np.sqrt(var_state + var_nat)
    return {"share": S, "sd": sd, "sd_state": np.sqrt(var_state), "sd_national": np.sqrt(var_nat),
            "p_win": float(norm.cdf((S - 0.5) / sd)) if sd > 0 else float(S > 0.5),
            "swing": delta, "counted_frac": float(d["n"].sum() / d["V"].sum())}
