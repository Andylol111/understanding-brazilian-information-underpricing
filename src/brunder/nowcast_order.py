"""Reporting-order-aware nowcast (prototype): (1) a hierarchical tau and (2) a live slope on unit size or prior lean.

Part 1, `tau` as a distribution (the part the model review found to matter): the reporting-order error `tau` of
`brunder.nowcast` differs a lot between count nights (smallest tau with 90% coverage: 0 to 0.03 on nine real-order nights,
docs/model_review.md) and is today a single number chosen on one or two earlier nights. `needed_tau` estimates a night's tau
from its own frames; `mixture_band` / `mixture_pwin` turn the taus of earlier nights into a predictive mixture instead of
one point (an empirical-Bayes draw from the cross-election tau distribution). Pure functions on numpy arrays; `nowcast_order`
needs nothing from the night being scored.

Part 2, the live slope (below).

Why. `brunder.nowcast` extrapolates the swing of the counted units to the uncounted ones through one national swing
(shrunk state swings) plus a fixed national error `tau` that is supposed to cover "small units report first". Where the
swing depends on the unit (rural towns swing differently from big cities, strong units swing more than weak ones), the
early count is unrepresentative in a way the model can see: the counted units are small, the missing votes are in big
units. `tau` is then calibrated on one or two earlier nights and does not know whether the counted set is lopsided now.

What. This module adds one explicit term, estimated from the night's own counted units and carried with its own
uncertainty:  expected swing of unit i = state swing + beta * (x_i - x_region),  x_i = a standardised, prior-only unit
covariate:
  "size": log of the unit's size (its expected votes V0, or a `size` column of the prior when V0 is a pool of many reporting
          units, as in Poland's size-class cells); the rurality / reporting-order proxy.
  "lean": the unit's prior share s0 (swing proportional to prior lean; also catches regression to the mean).
beta is the vote-weighted least-squares slope of the counted units' deviations on x (one slope, region intercepts), shrunk
to 0 by a Normal(0, slope_sd^2) prior; its posterior variance enters the variance of the final margin multiplied by the
(very uneven) sum of x over the uncounted votes, so the band is wide exactly while the counted set is lopsided and narrows
as the large units come in. The covariate uses only the prior (never the night's counts), so the term has no look-ahead.

This is the machinery `brunder.nowcast` already has for the US "Hispanic slope" (v5: `hisp_slope`, column `h`), reused with
other covariates; `nowcast.py` is not touched. Everything else (rho, kappa, tau, sigma_mun0, ...) is the base model's.

Scale: x is standardised (V0-weighted over the pooled units), so slope_sd is the prior sd of the swing difference, in
share units, between a unit one sd above and one sd below the regional mean in size (or prior share).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import nowcast

COVARIATES = ("none", "size", "lean")


@dataclass(frozen=True)
class OrderParams:
    covariate: str = "size"     # one of COVARIATES; "none" = the base model unchanged
    slope_sd: float = 0.02      # prior sd of the slope per sd of the covariate (share units)

    def __post_init__(self):
        if self.covariate not in COVARIATES:
            raise ValueError(f"covariate must be one of {COVARIATES}")
        if self.slope_sd <= 0:
            raise ValueError("slope_sd must be positive")


def covariate(prior: pd.DataFrame, kind: str, unpooled: tuple[str, ...] = ("zz",)) -> pd.Series:
    """Standardised unit covariate from the prior alone (index of `prior`). Pooled units only define the mean and sd;
    unpooled units (abroad, early voting) get NaN, i.e. no slope contribution. 'size' uses prior["size"] if present,
    else prior["V0"]."""
    if kind == "size":
        raw = np.log(prior["size"] if "size" in prior else prior["V0"]).astype(float)
    elif kind == "lean":
        raw = prior["s0"].astype(float)
    else:
        raise ValueError(f"no covariate {kind!r}")
    ok = ~prior["uf"].isin(unpooled) & raw.notna() & (prior["V0"] > 0)
    w = prior["V0"].where(ok, 0.0).astype(float)
    mean = float((raw.where(ok, 0.0) * w).sum() / w.sum())
    sd = float(np.sqrt(((raw.where(ok, mean) - mean) ** 2 * w).sum() / w.sum()))
    x = (raw - mean) / sd if sd > 0 else raw * 0.0
    return x.where(ok)


def order_imbalance(state: pd.DataFrame, prior: pd.DataFrame, kind: str = "size",
                    unpooled: tuple[str, ...] = ("zz",)) -> float:
    """How unrepresentative the counted set is, live: counted-vote-weighted mean of the covariate minus its expected-vote
    weighted mean over all pooled units, in sd units. Strongly negative for 'size' = the small units report first."""
    pri = prior.assign(x=covariate(prior, kind, unpooled))
    m = pri.merge(state[["uf", "cd_mun", "n"]], on=["uf", "cd_mun"], how="left").fillna({"n": 0.0})
    m = m[m["x"].notna()]
    if m["n"].sum() <= 0:
        return 0.0
    return float((m["x"] * m["n"]).sum() / m["n"].sum() - (m["x"] * m["V0"]).sum() / m["V0"].sum())


def swing_correlation(state: pd.DataFrame, prior: pd.DataFrame, kind: str = "size",
                      unpooled: tuple[str, ...] = ("zz",)) -> float:
    """Live correlation (vote-weighted, region means removed) between the covariate and the counted units' swing
    d_i = a_i / n_i - s0_i. NaN with fewer than 3 counted pooled units or no variation."""
    pri = prior.assign(x=covariate(prior, kind, unpooled))
    m = pri.merge(state[["uf", "cd_mun", "n", "a"]], on=["uf", "cd_mun"], how="inner")
    m = m[(m["n"] > 0) & m["x"].notna()]
    if len(m) < 3:
        return float("nan")
    d = m["a"] / m["n"] - m["s0"]
    w = m["n"]
    g = m["uf"]
    dd = d - (w * d).groupby(g).transform("sum") / w.groupby(g).transform("sum")
    xx = m["x"] - (w * m["x"]).groupby(g).transform("sum") / w.groupby(g).transform("sum")
    sxx, syy = float((w * xx**2).sum()), float((w * dd**2).sum())
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return float((w * xx * dd).sum() / np.sqrt(sxx * syy))


def order_nowcast(state: pd.DataFrame, prior: pd.DataFrame, p: nowcast.Params = nowcast.Params(),
                  o: OrderParams = OrderParams(), uf: str | None = None) -> dict:
    """`nowcast.nowcast` with the live order slope. covariate 'none' returns the base model. Extra keys: order_covariate,
    order_imbalance, order_corr, and (via the base machinery) hisp_beta, hisp_beta_sd, hisp_margin_sd = the part of the
    margin sd that comes from the slope's uncertainty."""
    if o.covariate == "none":
        return nowcast.nowcast(state, prior, p, uf)
    pri = prior.assign(h=covariate(prior, o.covariate, p.unpooled))
    r = nowcast.nowcast(state, pri, replace(p, hisp_slope=True, hisp_slope_tau=o.slope_sd, hisp_blend=None), uf)
    r["order_covariate"] = o.covariate
    r["order_imbalance"] = order_imbalance(state, prior, o.covariate, p.unpooled)
    r["order_corr"] = swing_correlation(state, prior, o.covariate, p.unpooled)
    return r


def with_tau(result: dict, tau0: float, tau: float) -> dict:
    """The same result at another tau: the tau term is additive in the margin variance, Var M += (sum 2R)^2 (tau^2 - tau0^2),
    with sum 2R = 2 * total * (1 - counted) and total = margin_sd / (2 * share_sd). Returns margin_sd, share_sd, p_win."""
    from scipy.stats import norm
    total = result["margin_sd"] / (2 * result["share_sd"]) if result["share_sd"] > 0 else np.nan
    two_r = 2 * total * (1 - result["counted_frac"])
    var = max(result["margin_sd"] ** 2 + two_r**2 * (tau**2 - tau0**2), 0.0)
    sd = float(np.sqrt(var))
    return {"margin_sd": sd, "share_sd": sd / (2 * total) if total > 0 else np.nan,
            "p_win": float(norm.cdf(result["margin_hat"] / sd)) if sd > 0 else float(result["margin_hat"] > 0)}


# ---------------------------------------------------------------------------------------------------------- hierarchical tau
TAU_GRID = (0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
Z90 = 1.6448536269514722


def _sd_matrix(margin_sd0, two_r, total, taus) -> np.ndarray:
    """Share sd of every frame (rows) at every tau (columns): sqrt(margin_sd0^2 + (two_r tau)^2) / (2 total)."""
    m = np.asarray(margin_sd0, float)[:, None]
    r = np.asarray(two_r, float)[:, None]
    t = np.asarray(taus, float)[None, :]
    return np.sqrt(m**2 + (r * t) ** 2) / (2 * np.asarray(total, float)[:, None])


def frame_arrays(frames: pd.DataFrame) -> dict:
    """Arrays for the functions below from rows with `margin_sd0` (margin sd of a run at tau = 0), `counted` and `share_sd0`
    (share sd at tau = 0), as `nowcast` returns them: total votes = margin_sd0 / (2 share_sd0), two_r = 2 total (1 - counted)."""
    total = (frames["margin_sd0"] / (2 * frames["share_sd0"])).to_numpy(float)
    return {"margin_sd0": frames["margin_sd0"].to_numpy(float), "total": total, "two_r": 2 * total * (1 - frames["counted"].to_numpy(float))}


def needed_tau(err, margin_sd0, two_r, total, grid=TAU_GRID, level: float = 0.90) -> float:
    """Smallest tau of `grid` at which the share of frames whose |final - projected share| < z(level) sd(tau) is >= level
    (the night's own estimate of its reporting-order error); the largest grid value if none reaches it. Frames = one night's
    frames with 1% < counted <= 95% (the caller filters)."""
    z = norm.ppf(0.5 + level / 2)
    sd = _sd_matrix(margin_sd0, two_r, total, grid)
    cover = (np.abs(np.asarray(err, float))[:, None] < z * sd).mean(axis=0)
    ok = np.flatnonzero(cover >= level)
    return float(grid[ok[0]]) if len(ok) else float(grid[-1])


def mixture_pwin(margin_hat, margin_sd0, two_r, taus) -> np.ndarray:
    """P(A wins) = mean over the taus of Phi(margin_hat / margin_sd(tau)): the margin forecast as an equal-weight mixture of
    normals, one per earlier night's tau."""
    m = np.asarray(margin_hat, float)[:, None]
    sd = np.sqrt(np.asarray(margin_sd0, float)[:, None] ** 2 + (np.asarray(two_r, float)[:, None] * np.asarray(taus, float)[None, :]) ** 2)
    return norm.cdf(m / sd).mean(axis=1)


def mixture_band(share_hat, margin_sd0, two_r, total, taus, level: float = 0.90) -> tuple[np.ndarray, np.ndarray]:
    """Central `level` band of the equal-weight normal mixture of the final share (quantiles by bisection), one row per frame."""
    sh = np.asarray(share_hat, float)
    sd = _sd_matrix(margin_sd0, two_r, total, taus)
    out = []
    for q in ((1 - level) / 2, (1 + level) / 2):
        lo, hi = sh - 10 * sd.max(axis=1), sh + 10 * sd.max(axis=1)
        for _ in range(60):
            mid = (lo + hi) / 2
            below = norm.cdf((mid[:, None] - sh[:, None]) / np.where(sd > 0, sd, 1e-12)).mean(axis=1) < q
            lo, hi = np.where(below, mid, lo), np.where(below, hi, mid)
        out.append((lo + hi) / 2)
    return out[0], out[1]
