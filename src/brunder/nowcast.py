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

from dataclasses import dataclass, replace

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
    # Optional prior on the national swing (US model v2: poll average minus the prior's statewide share, and the poll
    # error). Off (None) by default: results are then exactly those without these fields. When set, the swing is the
    # precision-weighted combination of this prior and the swing observed in counted votes, whose variance is the
    # between-unit (and, with several pooled regions, between-region) variance of their weighted mean; every
    # region's swing moves by the same amount, and the remaining-vote uncertainty uses the posterior variance.
    swing_prior_mean: float | None = None
    swing_prior_sd: float | None = None
    # With a swing prior: scale the reporting-order term (tau) and the between-region term by the weight the counted
    # votes have in the swing (w_obs, 0 before any count). They describe how counted votes misstate the remaining
    # ones, so before the count the statewide uncertainty is the prior's alone. Off by default (v2 behaviour).
    count_scaled_terms: bool = False
    # How they scale in: "weight" = w_obs (v3); "any" = fully present from the first counted vote (0 only before
    # the count), because reporting-order bias is largest in the first returns.
    count_scale_mode: str = "weight"
    # Model v5 ("night Hispanic slope"; off by default: results are then exactly those without these fields). Needs a
    # per-unit column `h` in the prior (Hispanic share). A unit's expected swing becomes
    #   state swing + β·(h_i − h̄_region),   h̄_region = expected-vote-weighted mean of h in the unit's region,
    # with β estimated from the night's own counted units (vote-weighted least squares of the deviations from the
    # region mean on h_i − h̄_counted) and shrunk by a Normal(0, hisp_slope_tau²) prior; β's posterior variance is
    # propagated into the margin variance. The slope is never carried over from earlier cycles (its sign flips).
    hisp_slope: bool = False
    hisp_slope_tau: float = 0.5
    # Model v6: weight w in (0, 1) on the v5 predictive distribution; 1 - w on the same model without the slope. The
    # margin forecast is the mixture of the two normals (win probability = w·P5 + (1-w)·P3). None = no mixture.
    hisp_blend: float | None = None


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


def hisp_dev(df: pd.DataFrame) -> pd.Series:
    """h_i − h̄ of each unit: the unit's Hispanic share minus the expected-vote (V0) weighted mean of its region (uf).
    Units without a share get the region mean (0)."""
    h = df["h"].astype(float)
    w = df["V0"].astype(float)
    ok = h.notna() & (w > 0)
    wh = (h.where(ok, 0.0) * w.where(ok, 0.0)).groupby(df["uf"]).sum()
    ww = w.where(ok, 0.0).groupby(df["uf"]).sum()
    mean = (wh / ww.where(ww > 0)).reindex(df["uf"]).to_numpy()
    return (h - mean).fillna(0.0)


def _slope(df: pd.DataFrame, counted: pd.Series, p: Params) -> tuple[float, float]:
    """Posterior (mean, variance) of the Hispanic slope β from the counted units' deviations d_i (column "d").
    Weighted least squares with weights n_i of d_i − d̄_region on x_i − x̄_region (means over counted units; one
    slope, region-specific intercepts), residual variance σ²_mun (posterior blend as in the main model), combined
    with the Normal(0, tau²) prior: β = β̂ P/(P + 1/τ²), Var β = 1/(P + 1/τ²), P = 1/Var β̂ (0 when the counted
    units do not vary in h, so β = 0 and Var β = τ²)."""
    prior_prec = 1.0 / p.hisp_slope_tau**2
    c = df[counted]
    if len(c) < 3:
        return 0.0, 1.0 / prior_prec
    w = c["n"]
    g = c["uf"]
    wsum = w.groupby(g).transform("sum")
    dt = c["d"] - (w * c["d"]).groupby(g).transform("sum") / wsum
    xt = c["x"] - (w * c["x"]).groupby(g).transform("sum") / wsum
    sxx = float((w * xt**2).sum())
    if sxx <= 1e-12 * float(w.sum()):
        return 0.0, 1.0 / prior_prec
    b_hat = float((w * xt * dt).sum()) / sxx
    s2 = _post_var((dt - b_hat * xt).to_numpy(), w.to_numpy(), p.sigma_mun0, p.nu_mun)
    var_hat = s2 * float((w**2 * xt**2).sum()) / sxx**2
    if var_hat <= 0:
        return 0.0, 1.0 / prior_prec
    prec = 1.0 / var_hat
    return b_hat * prec / (prec + prior_prec), 1.0 / (prec + prior_prec)


def _mixture(r3: dict, r5: dict, w: float) -> dict:
    """Mixture w·r5 + (1−w)·r3 of two nowcasts' predictive normals for the final margin."""
    m = w * r5["margin_hat"] + (1 - w) * r3["margin_hat"]
    var = w * r5["margin_sd"] ** 2 + (1 - w) * r3["margin_sd"] ** 2 + w * (1 - w) * (r5["margin_hat"] - r3["margin_hat"]) ** 2
    sd = float(np.sqrt(var))
    scale = r3["share_sd"] / r3["margin_sd"] if r3["margin_sd"] > 0 else np.nan     # share = 0.5 + margin / (2 total)
    out = dict(r3)
    out.update({"p_win": w * r5["p_win"] + (1 - w) * r3["p_win"], "margin_hat": m, "margin_sd": sd,
                "share_hat": w * r5["share_hat"] + (1 - w) * r3["share_hat"],
                "share_sd": sd * scale if np.isfinite(scale) else r3["share_sd"],
                "p_win_v3": r3["p_win"], "p_win_v5": r5["p_win"], "margin_hat_v3": r3["margin_hat"],
                "margin_hat_v5": r5["margin_hat"], "hisp_blend": w})
    out.update({k: v for k, v in r5.items() if k.startswith("hisp_")})
    return out


def nowcast(state: pd.DataFrame, prior: pd.DataFrame, p: Params = Params(), uf: str | None = None) -> dict:
    """P(A wins) given the count state. uf restricts the race to one state (e.g. '1st place in SP')."""
    if p.hisp_blend is not None:
        return _mixture(nowcast(state, prior, replace(p, hisp_slope=False, hisp_blend=None), uf),
                        nowcast(state, prior, replace(p, hisp_slope=True, hisp_blend=None), uf), p.hisp_blend)
    df = prior.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    if uf is not None:
        df = df[df["uf"] == uf.lower()]
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0

    expected_counted = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / expected_counted if expected_counted > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k

    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    beta, var_beta, bx = 0.0, 0.0, 0.0
    if p.hisp_slope:
        df["x"] = hisp_dev(df)
        beta, var_beta = _slope(df, counted, p)
        df["d"] = df["d"] - beta * df["x"]           # deviations net of the slope: everything below is as before
        bx = beta * df["x"]
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
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted + bx).clip(0, 1)

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
    if p.swing_prior_sd is not None:
        var_obs = np.inf
        if Wp.sum() > 0:
            var_obs = sigma_mun2 * W2p.sum() / Wp.sum() ** 2
            if len(by_uf) > 1:                       # several regions: the observed mean also mixes regions
                var_obs += sigma_between2 * (Wp**2).sum() / Wp.sum() ** 2
        s2 = p.swing_prior_sd**2
        w_obs = s2 / (s2 + var_obs) if np.isfinite(var_obs) else 0.0
        delta_post = w_obs * delta + (1 - w_obs) * (p.swing_prior_mean or 0.0)
        var_post = (1 - w_obs) * s2                 # = 1 / (1/s2 + 1/var_obs)
        df["delta_u"] = df["delta_u"] + (delta_post - delta)
        resid_post = (df["d"] - df["delta_u"]).fillna(0.0)
        df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid_post * counted + bx).clip(0, 1)
        scale = (w_obs if p.count_scale_mode == "weight" else float(np.isfinite(var_obs))) \
            if p.count_scaled_terms else 1.0
        var_uf = var_uf * scale
        var_nat = twoR.sum() ** 2 * p.tau**2 * scale + twoR_u.sum() ** 2 * var_post
        extra = {"swing_obs": delta, "swing_prior_weight": float(1 - w_obs)}
        delta = delta_post
    else:
        extra = {}
    var_hisp = 0.0
    if p.hisp_slope:
        # error of the margin from β's uncertainty: dM/dβ · Var β, where a unit's ŝ moves by (1-ρ·counted)(x_i + dδ_u/dβ)
        # (β enters the state swing through the counted units' composition: δ_u uses d_i − β x_i)
        n_x = (df["n"] * df["x"]).where(counted, 0.0)
        pool_c = counted & ~df["uf"].isin(p.unpooled)
        xbar_pool = float(n_x[pool_c].sum() / df["n"][pool_c].sum()) if pool_c.any() else 0.0
        xbar_sum = n_x.groupby(df["uf"]).sum().reindex(df["uf"]).to_numpy()
        d_delta = -(xbar_sum + p.kappa * xbar_pool) / (df["W"].to_numpy() + p.kappa)
        if p.swing_prior_sd is not None:
            d_delta = d_delta + (1 - w_obs) * xbar_pool
        g = (1 - p.rho * counted) * (df["x"] + d_delta)
        var_hisp = float((2 * df["R"] * g).sum()) ** 2 * var_beta
        extra = {**extra, "hisp_beta": beta, "hisp_beta_sd": float(np.sqrt(var_beta)), "hisp_margin_sd": float(np.sqrt(var_hisp))}
    sd = float(np.sqrt(var_mun + var_uf + var_nat + var_hisp))

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
        **extra,
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
