"""Election-night nowcast: P(candidate A wins | partial count) from municipality-level returns.

Model (A vs. rest-of-valid; in a runoff "rest" = the other candidate, in a first round
"A wins outright" ⇔ A > 50% of valid votes, the same margin):

  prior per municipality i:   s0_i  = prior A share,   V0_i = prior valid votes
  partial count at time t:    n_i   = counted valid,   a_i = counted A votes,  f_i = sections counted / total

  turnout scale     k   = Σ n_i / Σ f_i V0_i                          (national)
  remaining votes   R_i = (1 - f_i) V0_i k
  observed swing    d_i = a_i/n_i - s0_i                            (counted munis only)
  state swing       δ_u = (Σ_{i∈u} n_i d_i + κ δ) / (Σ_{i∈u} n_i + κ) (shrunk to national δ)
  remaining share   ŝ_i = s0_i + δ_u + ρ·(d_i - δ_u)·1[f_i > 0]

  final margin      M   = Σ (2a_i - n_i) + Σ R_i (2ŝ_i - 1)
  Var M             = Σ_i (2R_i)² σ²_mun,i + Σ_u R̃_u² σ²_u + (Σ 2R_i)² τ² + (Σ_u R̃_u (1-λ_u))² Var δ̂
                      with R̃_u = Σ_{i∈u} 2R_i,  λ_u = W_u / (W_u + κ),  W_u = Σ_{i∈u} n_i
  P(A wins)         = Φ(E[M] / sd M)

σ_mun:  dispersion of municipal swing around state swing (estimated from counted munis);
σ²_u:   λ_u² σ²_mun Σ n_i² / W_u²  (error of the state's own swing estimate)
        + (1-λ_u) σ²_between        (true state deviation not yet observed);
Var δ̂:  sampling error of the national swing from the munis counted so far
        = (σ²_mun Σ n_i² + σ²_between Σ_u W_u²) / W².  Large when few munis are in;
τ:      national systematic error of the remaining count — the reporting-order bias that
        makes early returns non-representative (small towns finish first). τ is the one
        parameter that must be calibrated on past counts (docs/research_design.md).

σ²_mun and σ²_between are posterior-mean blends of prior values (Params.sigma_mun0/nu_mun,
sigma_uf0/nu_uf) with the sample variances, so the first handful of returns cannot produce
overconfident probabilities. Abroad units (uf 'zz') get their own swing and stay out of δ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class Params:
    kappa: float = 50_000.0     # pseudo-votes shrinking state swing to national swing
    rho: float = 0.8            # persistence of within-municipality deviation into its uncounted votes
    tau: float = 0.01           # national systematic share error on uncounted votes
    sigma_mun0: float = 0.06    # prior sd of municipal swing around state swing ...
    nu_mun: float = 20.0        # ... worth this many municipalities
    sigma_uf0: float = 0.03     # prior sd of state swing around national swing ...
    nu_uf: float = 5.0          # ... worth this many states
    unpooled: tuple[str, ...] = ("zz",)   # units kept out of the national swing (abroad votes)


def prior_from_final(final: pd.DataFrame, cand_a: str, cand_b: str | None = None) -> pd.DataFrame:
    """Prior from a finished election/round. `final` columns: uf, cd_mun, cand_n, votes (valid votes).

    cand_b=None: share = A / all valid (first-round style).
    cand_b given: share = A / (A + B), i.e. the other candidates' voters split in proportion
    (a runoff prior from a first round; replace with an explicit transfer model if needed).
    """
    g = final.pivot_table(index=["uf", "cd_mun"], columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    valid = g.sum(axis=1)
    a = g[cand_a]
    share = a / (a + g[cand_b]) if cand_b is not None else a / valid
    return pd.DataFrame({"s0": share, "V0": valid}).reset_index().dropna()


def state_from_votes(votes: pd.DataFrame, cand_a: str) -> pd.DataFrame:
    """Municipality-level count state from parsed live `u` files (tse_live.parse_u rows, level=='mu')."""
    mu = votes[votes["level"] == "mu"]
    unit = mu.drop_duplicates(["uf", "cd_mun"]).set_index(["uf", "cd_mun"])
    a = mu[mu["cand_n"] == cand_a].set_index(["uf", "cd_mun"])["votes"]
    return pd.DataFrame({
        "n": unit["votes_valid"],
        "a": a.reindex(unit.index).fillna(0),
        "f": unit["sections_counted"] / unit["sections_total"].where(unit["sections_total"] > 0),
    }).fillna({"f": 0.0}).reset_index()


def _wvar(x: np.ndarray, w: np.ndarray) -> float:
    if w.sum() <= 0 or len(x) < 2:
        return 0.0
    m = np.average(x, weights=w)
    return float(np.average((x - m) ** 2, weights=w))


def _post_var(x: np.ndarray, w: np.ndarray, s0: float, nu: float) -> float:
    """Posterior-mean-style blend of a prior variance (worth nu obs) with the weighted sample variance."""
    n = len(x)
    return (nu * s0**2 + max(n - 1, 0) * _wvar(x, w)) / (nu + max(n - 1, 0))


def nowcast(state: pd.DataFrame, prior: pd.DataFrame, p: Params = Params(), uf: str | None = None) -> dict:
    """P(A wins) given the count state. uf restricts the race to one state (e.g. '1st place in SP')."""
    df = prior.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    if uf is not None:
        df = df[df["uf"] == uf.lower()]
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0

    expected_counted = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / expected_counted if expected_counted > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k

    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    c = df[counted]
    pool = c[~c["uf"].isin(p.unpooled)]
    delta = float(np.average(pool["d"], weights=pool["n"])) if len(pool) else 0.0
    by_uf = c.groupby("uf").apply(lambda g: pd.Series({"W": g["n"].sum(), "wd": (g["n"] * g["d"]).sum(),
                                                        "W2": (g["n"] ** 2).sum()}),
                                  include_groups=False) if len(c) else pd.DataFrame(columns=["W", "wd", "W2"])
    by_uf = by_uf.reindex(df["uf"].unique()).fillna(0.0)
    by_uf["delta_u"] = (by_uf["wd"] + p.kappa * delta) / (by_uf["W"] + p.kappa)
    df = df.join(by_uf[["delta_u", "W"]], on="uf")

    resid = (df["d"] - df["delta_u"]).fillna(0.0)
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted).clip(0, 1)

    sigma_mun2 = _post_var(resid[counted].to_numpy(), df.loc[counted, "n"].to_numpy(), p.sigma_mun0, p.nu_mun)
    pooled_uf = (by_uf["W"] > 0) & ~by_uf.index.isin(p.unpooled)
    raw_uf = by_uf.loc[pooled_uf, "wd"] / by_uf.loc[pooled_uf, "W"]
    sigma_between2 = _post_var(raw_uf.to_numpy(), by_uf.loc[pooled_uf, "W"].to_numpy(), p.sigma_uf0, p.nu_uf)
    lam = by_uf["W"] / (by_uf["W"] + p.kappa)
    # own-data estimation error of the state mean + remaining true state deviation
    by_uf["var_u"] = (lam**2 * sigma_mun2 * by_uf["W2"] / by_uf["W"].where(by_uf["W"] > 0) ** 2).fillna(0.0) \
        + sigma_between2 * (1 - lam)
    # estimation error of the national swing from the munis counted so far (shared by all states via 1-λ)
    Wp, W2p = by_uf.loc[pooled_uf, "W"], by_uf.loc[pooled_uf, "W2"]
    var_delta = (sigma_mun2 * W2p.sum() + sigma_between2 * (Wp**2).sum()) / Wp.sum() ** 2 if Wp.sum() > 0 else 0.0

    twoR = 2 * df["R"]
    twoR_u = df.assign(twoR=twoR).groupby("uf")["twoR"].sum().reindex(by_uf.index).fillna(0.0)
    var_mun = (twoR**2 * sigma_mun2 * np.where(counted, 1 - df["f"], 1.0)).sum()
    var_uf = (twoR_u**2 * by_uf["var_u"]).sum()
    var_nat = twoR.sum() ** 2 * p.tau**2 + (twoR_u * (1 - lam)).sum() ** 2 * var_delta
    sd = float(np.sqrt(var_mun + var_uf + var_nat))

    counted_margin = float((2 * df["a"] - df["n"]).sum())
    remaining_margin = float((df["R"] * (2 * df["s_hat"] - 1)).sum())
    m = counted_margin + remaining_margin
    total = df["n"].sum() + df["R"].sum()
    prob = float(norm.cdf(m / sd)) if sd > 0 else float(m > 0)
    return {
        "p_win": prob,
        "share_hat": 0.5 + m / (2 * total) if total else np.nan,
        "share_sd": sd / (2 * total) if total else np.nan,
        "margin_hat": m,
        "margin_sd": sd,
        "counted_frac": float(df["n"].sum() / total) if total else np.nan,
        "swing_nat": delta,
        "turnout_k": float(k),
    }


def reconstruct_states(final_state: pd.DataFrame, completion: pd.DataFrame, times: pd.DatetimeIndex,
                       release: pd.Timestamp | None = None) -> dict[pd.Timestamp, pd.DataFrame]:
    """Approximate count states when no live snapshots were recorded.

    completion: per-municipality `last_update_utc` from the final `ab` files (time the muni
    finished). A muni is treated as 0% counted before it finished and 100% after, so large
    municipalities that report gradually are invisible until they finish. The observed set at
    time t is therefore (a) smaller than a live observer's and (b) tilted further toward small,
    fast-finishing municipalities. The direction of the resulting bias in P(t) is not signed;
    treat reconstructed leads over the market as indicative until confirmed on live snapshots.

    release: the moment TSE results became public. Completion times can precede publication
    (embargo, or a publication pause as in the 2026 R2), so a muni finished before `release` is
    only visible from `release`. Without it, the reconstruction has look-ahead bias.
    """
    fin = final_state.merge(completion[["uf", "cd_mun", "last_update_utc"]], on=["uf", "cd_mun"], how="left")
    visible = fin["last_update_utc"] if release is None else fin["last_update_utc"].clip(lower=release)
    out = {}
    for t in times:
        done = (visible <= t).to_numpy()
        s = fin[["uf", "cd_mun"]].copy()
        s["n"] = np.where(done, fin["n"], 0.0)
        s["a"] = np.where(done, fin["a"], 0.0)
        s["f"] = done.astype(float)
        out[t] = s
    return out


def path(states: dict[pd.Timestamp, pd.DataFrame], prior: pd.DataFrame, p: Params = Params(),
         uf: str | None = None) -> pd.DataFrame:
    rows = [{"time": t, **nowcast(s, prior, p, uf)} for t, s in states.items()]
    return pd.DataFrame(rows).set_index("time")
