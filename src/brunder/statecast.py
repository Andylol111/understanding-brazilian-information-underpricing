"""State-level count nowcast from per-state partial results.

The modelled quantity x is either a vote share (runoff: A / valid, win if x > ½) or a margin
(first-round plurality: (A − B) / valid, win if x > 0); callers pass `a` as A votes or A − B votes
and set Params.threshold. Per state u with prior x0_u, expected valid votes V_u, counted valid n_u,
observed x̂_u = a_u / n_u and sections-counted fraction f_u; N = domestic counted valid votes:

  national swing     δ   = Σ_{domestic} n_u (x̂_u − x0_u) / (N + κ_nat)     (shrunk to 0 = prior)
  state swing        δ_u = λ_u (x̂_u − x0_u) + (1 − λ_u) δ,   λ_u = n_u / (n_u + κ)
  final state value  X_u = f_u x̂_u + (1 − f_u)(x0_u + δ_u)
  national           X   = Σ w_u X_u,   w_u = V_u / Σ V

Errors on a state's uncounted votes: own e_u ~ σ²_own,u = (1 − λ_u) σ²_swing + λ_u σ²_within,
national-swing estimate e_δ ~ σ²_δ = σ²_nat κ_nat / (N + κ_nat) entering with weight (1 − λ_u),
and a residual shared error e_τ ~ τ². With w̃_u = w_u (1 − f_u):

  Var X_u = (1 − f_u)² [σ²_own,u + (1 − λ_u)² σ²_δ + τ²]
  Var X   = Σ w̃_u² σ²_own,u + (Σ w̃_u (1 − λ_u))² σ²_δ + (Σ w̃_u)² τ²

σ_nat makes the model defer to the count while little is counted: before the first returns the
prior's national level is only as good as a cross-cycle swing forecast. Defaults come from data other
than any count being scored (see docs/research_design.md): σ_swing = 6.6 pt, σ_within = σ_swing,
σ_nat = 5 pt, κ_nat = κ = 200,000 votes, τ = 1 pt. Double the σ's and τ for a margin.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class Params:
    kappa: float = 200_000.0      # pseudo-votes: a state needs ~this many counted votes to move off δ
    sigma_swing: float = 0.066
    sigma_within: float = 0.066
    sigma_nat: float = 0.05       # prior sd of the national swing, before anything is counted
    kappa_nat: float = 200_000.0  # counted votes that halve the national-swing prior variance
    tau: float = 0.01
    threshold: float = 0.5        # 0.5 for a share, 0.0 for a margin


def _core(states: pd.DataFrame, prior: pd.DataFrame, p: Params):
    d = prior.merge(states, on="uf", how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    d["o"] = np.where(d["n"] > 0, d["a"] / d["n"].where(d["n"] > 0), np.nan)
    dom = d[(d["uf"] != "zz") & (d["n"] > 0)]
    N = float(dom["n"].sum())
    delta = float((dom["n"] * (dom["o"] - dom["s0"])).sum() / (N + p.kappa_nat)) if len(dom) else 0.0
    d["dev"] = (d["o"] - d["s0"]).fillna(delta)
    d["lam"] = d["n"] / (d["n"] + p.kappa)
    d["delta_u"] = d["lam"] * d["dev"] + (1 - d["lam"]) * delta
    d["S"] = np.where(d["n"] > 0, d["f"] * d["o"].fillna(0) + (1 - d["f"]) * (d["s0"] + d["delta_u"]), d["s0"] + delta)
    d["var_own"] = (1 - d["lam"]) * p.sigma_swing**2 + d["lam"] * p.sigma_within**2
    var_delta = p.sigma_nat**2 * p.kappa_nat / (N + p.kappa_nat)
    return d, delta, var_delta


def project(states: pd.DataFrame, prior: pd.DataFrame, p: Params = Params()) -> tuple[pd.DataFrame, float]:
    """Per-state table (X_u = 'S', sd, p_win) and the national swing δ.
    states: uf, n (counted valid), a (numerator), f (sections fraction). prior: uf, s0, V."""
    d, delta, var_delta = _core(states, prior, p)
    d["sd"] = (1 - d["f"]) * np.sqrt(d["var_own"] + (1 - d["lam"]) ** 2 * var_delta + p.tau**2)
    with np.errstate(divide="ignore", invalid="ignore"):
        d["p_win"] = np.where(d["sd"] > 0, norm.cdf((d["S"] - p.threshold) / d["sd"]), (d["S"] > p.threshold).astype(float))
    return d, delta


def _weights(d: pd.DataFrame) -> pd.Series:
    """Expected final valid votes per state: counted votes plus the uncounted share of the prior
    total scaled by observed turnout (Σ n / Σ f·V over reporting states). Equals the actual votes once
    a state is fully counted, so a complete count reproduces the actual national result."""
    rep = d["f"] > 0
    r = float(d.loc[rep, "n"].sum() / (d.loc[rep, "f"] * d.loc[rep, "V"]).sum()) if rep.any() else 1.0
    Vhat = d["n"] + (1 - d["f"]) * d["V"] * r
    return Vhat / Vhat.sum()


def nowcast(states: pd.DataFrame, prior: pd.DataFrame, p: Params = Params()) -> dict:
    """National value of x with state-specific, national-swing and residual shared uncertainty."""
    d, delta, var_delta = _core(states, prior, p)
    w = _weights(d)
    S = float((w * d["S"]).sum())
    wt = w * (1 - d["f"])
    var_state = float((wt**2 * d["var_own"]).sum())
    var_nat = float((wt * (1 - d["lam"])).sum() ** 2 * var_delta + wt.sum() ** 2 * p.tau**2)
    sd = np.sqrt(var_state + var_nat)
    return {"share": S, "sd": sd, "sd_state": np.sqrt(var_state), "sd_national": np.sqrt(var_nat),
            "p_win": float(norm.cdf((S - p.threshold) / sd)) if sd > 0 else float(S > p.threshold),
            "swing": delta, "counted_frac": float(d["n"].sum() / d["V"].sum())}
