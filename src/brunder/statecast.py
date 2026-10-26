"""State-level count nowcast: national share of candidate A from the per-state TSE files.

Why state level: the early national number is dominated by which states report first (2026 R1 at
1.7% counted: composition −4.95 pt vs within-state +0.7 pt), and state files are published before
the national aggregate. Per state u with prior share s0_u, expected valid votes V_u, counted valid n_u,
observed share o_u and sections-counted fraction f_u:

  national swing     δ   = Σ_{domestic} n_u (o_u − s0_u) / Σ n_u
  state swing        δ_u = (n_u (o_u − s0_u) + κ δ) / (n_u + κ)        (thin states shrink to δ)
  final state share  S_u = f_u o_u + (1 − f_u)(s0_u + δ_u)
  national           S   = Σ V_u S_u / Σ V_u
  variance           Var S = Σ (V_u/ΣV)² (1 − f_u)² σ²_state  +  ((Σ V_u (1 − f_u) λ_u)/ΣV)² σ²_δ

σ_state: dispersion of a state's early share around its final share (3.5 pt from the one archived
R1 cross-section — an estimate from one moment, not a curve). σ_δ: sampling error of the pooled
swing. Abroad ('zz') is its own unit and stays out of δ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class Params:
    kappa: float = 200_000.0     # pseudo-votes: a state needs ~this many counted votes to move off δ
    sigma_state: float = 0.035   # one-cross-section estimate (docs/research_design.md)


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
    W = d["V"] / d["V"].sum()
    S = float((W * d["S"]).sum())
    sig_delta = p.sigma_state / np.sqrt(max(len(dom), 1))
    var = float(((W * (1 - d["f"])) ** 2).sum() * p.sigma_state**2
                + ((W * (1 - d["f"]) * (1 - d["lam"])).sum() * sig_delta) ** 2)
    sd = np.sqrt(var)
    return {"share": S, "sd": sd, "p_win": float(norm.cdf((S - 0.5) / sd)) if sd > 0 else float(S > 0.5),
            "swing": delta, "counted_frac": float((d["n"]).sum() / d["V"].sum())}
