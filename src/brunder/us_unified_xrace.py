"""Cross-race (joint) count update for two races on one ballot (Senate + governor; statewide race + House district).

A SENSITIVITY / OPTION, NOT PART OF THE FROZEN UNIFIED CHALLENGER: docs/us_crossrace.md shows it does not beat the separate models out of
sample when both races are counted simultaneously, so `brunder.us_unified`, the registered code and the configs are unchanged. Kept so the
experiment is reproducible (analysis/us_crossrace.py, analysis/us_crossrace_house.py) and usable if a race's results ever come on a
different schedule from the top of the ticket.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Method
#
# Two races on one ballot (Senate + governor of Ohio) have correlated swings from their priors. Hierarchical reading: a county's
# swing = common county swing + race-specific deviation. Two correlations carry it, both estimated on past cycles only
# (`estimate_crossrace`, analysis/us_crossrace.py):
#   rho_s   statewide level: corr of the two races' standardised statewide errors (statewide swing of race A vs race B);
#   rho_e   county level: corr of the counties' deviations from their race's statewide swing; the regression slope beta_<A←B> turns
#           B's county deviation into the expected deviation of A's result in the same county.
# `joint_project` runs `brunder.us.project` (unchanged) twice per race. Pass 0: each race alone. Then (1) the two statewide swings
# are updated jointly (bivariate Gaussian: prior covariance rho_s·sd_A·sd_B, observations = each race's swing seen in its
# counted counties with the nowcast's own variance and the noise covariance rho_e·σ_A·σ_B·Σ n_A n_B/(W_A W_B) — the same counties
# being counted for both races makes the two sampling errors correlated, so simultaneous reporting adds little), each race's swing
# prior is replaced by the effective prior that, combined by the nowcast with its own observation, reproduces the joint posterior;
# (2) a county not yet counted in race A but counted in race B has its prior share shifted by beta_<A←B>·(B's county deviation
# from B's posterior state swing). Pass 1 runs the nowcast with those priors. No parameter of brunder.nowcast or brunder.us changes.


def estimate_crossrace_pairs(pairs: list[tuple[pd.DataFrame, pd.DataFrame]]) -> dict:
    """Cross-race parameters from pairs (A, B) of races on one ballot; each frame holds the counties (or county pieces) of ONE race with
    columns fips, s0 (prior share), s2, v2 (result), S_hat, sd, S_act (statewide / district forecast, its sd, realised share). Counties
    are matched on fips. Returns
      rho_s  correlation of the standardised errors z = (S_act − S_hat)/sd of A and B over the pairs (clipped to [0, 0.9]; with a few
             dozen pairs the raw value is noisy, `rho_s_raw` is reported),
      rho_e  vote-weighted correlation of the counties' deviations of s2 − s0 from each race's weighted-mean swing (pooled),
      beta_a_on_b / beta_b_on_a  expected deviation of A per unit deviation of B (regression slopes) and the reverse."""
    zs, zg, ea, eb, ww, n_pairs = [], [], [], [], [], 0
    for A, B in pairs:
        m = A.merge(B, on="fips", suffixes=("_a", "_b"))
        if len(m) < 3:
            continue
        n_pairs += 1
        zs.append((m["S_act_a"].iloc[0] - m["S_hat_a"].iloc[0]) / m["sd_a"].iloc[0])
        zg.append((m["S_act_b"].iloc[0] - m["S_hat_b"].iloc[0]) / m["sd_b"].iloc[0])
        da, db = m["s2_a"] - m["s0_a"], m["s2_b"] - m["s0_b"]
        ea.append((da - np.average(da, weights=m["v2_a"])).to_numpy())
        eb.append((db - np.average(db, weights=m["v2_b"])).to_numpy())
        ww.append(np.sqrt(m["v2_a"] * m["v2_b"]).to_numpy())
    a, b, w = np.concatenate(ea), np.concatenate(eb), np.concatenate(ww)
    cov, va, vb = np.average(a * b, weights=w), np.average(a * a, weights=w), np.average(b * b, weights=w)
    raw = float(np.corrcoef(zs, zg)[0, 1])
    return {"rho_s": float(np.clip(raw, 0.0, 0.9)), "rho_s_raw": raw, "rho_e": float(cov / np.sqrt(va * vb)),
            "beta_a_on_b": float(cov / vb), "beta_b_on_a": float(cov / va), "sd_e_a": float(np.sqrt(va)), "sd_e_b": float(np.sqrt(vb)),
            "n_pairs": n_pairs, "n_counties": int(len(a))}


def estimate_crossrace(P: pd.DataFrame, cycles: list[int] | None = None) -> dict:
    """`estimate_crossrace_pairs` for the regular Senate (A) x governor (B) pairs of a panel of leave-one-cycle-out county priors
    (columns race, t, st, office, special, fips, s0, s2, v2, S_hat, S_act, sd) restricted to `cycles`. Keys as there, plus
    beta_g_on_s (governor deviation per unit Senate deviation = beta_b_on_a) and beta_s_on_g."""
    D = P if cycles is None else P[P["t"].isin(cycles)]
    pairs = []
    for _, g in D[D["special"] == 0].groupby(["t", "st"]):
        s, v = g[g["office"] == "sen"], g[g["office"] == "gov"]
        if len(s) and len(v):
            pairs.append((s, v))
    e = estimate_crossrace_pairs(pairs)
    return {**e, "beta_g_on_s": e["beta_b_on_a"], "beta_s_on_g": e["beta_a_on_b"], "sd_e_sen": e["sd_e_a"], "sd_e_gov": e["sd_e_b"],
            "cycles": sorted(int(x) for x in D["t"].unique())}


def joint_swing_posterior(mu: np.ndarray, sd: np.ndarray, rho_s: float, y: np.ndarray, v: np.ndarray, cov_noise: float) -> tuple[np.ndarray, np.ndarray]:
    """Posterior mean (2,) and covariance (2, 2) of the two statewide swings. mu, sd: prior means and sds; rho_s: prior correlation;
    y, v: observed swings in counted votes and their sampling variances (v = inf: not observed); cov_noise: covariance of the two
    sampling errors. Pure Gaussian conditioning."""
    S = np.array([[sd[0] ** 2, rho_s * sd[0] * sd[1]], [rho_s * sd[0] * sd[1], sd[1] ** 2]])
    obs = [i for i in range(2) if np.isfinite(v[i])]
    if not obs:
        return np.asarray(mu, float), S
    N = np.array([[v[i] if i == j else cov_noise for j in obs] for i in obs])
    H = np.eye(2)[obs]
    K = S @ H.T @ np.linalg.inv(H @ S @ H.T + N)
    m = np.asarray(mu, float) + K @ (np.asarray(y, float)[obs] - np.asarray(mu, float)[obs])
    return m, S - K @ H @ S


def _swing_obs(res: dict, cfg: dict) -> tuple[float, float, float, float]:
    """(y, var_obs, prior_mean, prior_sd) of a race from its pass-0 nowcast result: the swing seen in counted votes, its sampling
    variance as the nowcast weighed it (w_obs = s²/(s² + var_obs); inf before any count), and the configured swing prior."""
    M = cfg.get("model", {})
    mu, s = M.get("swing_prior_mean"), M.get("swing_prior_sd")
    if mu is None or s is None:
        raise ValueError("joint_project needs [model] swing_prior_mean and swing_prior_sd in both configs")
    wp = res.get("swing_prior_weight", 1.0)               # = 1 − w_obs
    w_obs = 1.0 - wp
    var = s * s * wp / w_obs if w_obs > 1e-12 else np.inf
    return float(res.get("swing_obs", 0.0)), float(var), float(mu), float(s)


def joint_project(rows_a: pd.DataFrame, cfg_a: dict, pri_a: pd.DataFrame, rows_b: pd.DataFrame, cfg_b: dict, pri_b: pd.DataFrame,
                  xr: dict, county_info: bool = True, r0: list | None = None) -> tuple[tuple[dict, pd.DataFrame], tuple[dict, pd.DataFrame]]:
    """`brunder.us.project` of two races of one ballot with the cross-race update described above. `xr`: rho_s, rho_e, beta_ab (expected
    deviation of A's county result per unit deviation of B's), beta_ba. The races may cover different counties (a House district is a
    subset of the state): counties are matched on unit_id. Returns ((res_a, units_a), (res_b, units_b)) as `us.project`; `res` also
    carries xr_swing_prior_mean / xr_swing_prior_sd (the effective priors) and xr_county_shift_n (counties whose prior was shifted).
    Configs with one nowcast unit per county only (no [count] vote_type); both need [model] swing_prior_mean / swing_prior_sd.
    `xr["rho_n"]` (optional, default rho_e) is the correlation of the two races' sampling errors of the observed swing when the same
    counties are counted; `county_info=False` leaves the county priors alone; `r0` = the two `us.project` results without the update
    (saves recomputing them when several variants are compared)."""
    from . import us
    for c in (cfg_a, cfg_b):
        if c.get("count", {}).get("vote_type"):
            raise ValueError("joint_project: vote_type counts are not supported")
    r0 = r0 or [us.project(rows_a, cfg_a, pri_a), us.project(rows_b, cfg_b, pri_b)]
    obs = [_swing_obs(r0[0][0], cfg_a), _swing_obs(r0[1][0], cfg_b)]
    y = np.array([o[0] for o in obs])
    v = np.array([o[1] for o in obs])
    mu = np.array([o[2] for o in obs])
    sd = np.array([o[3] for o in obs])
    ua, ub = r0[0][1].set_index("unit_id"), r0[1][1].set_index("unit_id")
    Wa, Wb = ua["n"].sum(), ub["n"].sum()
    cov = 0.0
    if np.isfinite(v).all() and Wa > 0 and Wb > 0:
        s2a, s2b = v[0] * Wa**2 / (ua["n"] ** 2).sum(), v[1] * Wb**2 / (ub["n"] ** 2).sum()      # σ²_mun the nowcast used (single region)
        common = ua.index.intersection(ub.index)
        cov = float(xr.get("rho_n", xr["rho_e"]) * np.sqrt(s2a * s2b) * (ua.loc[common, "n"] * ub.loc[common, "n"]).sum() / (Wa * Wb))
    m, Pc = joint_swing_posterior(mu, sd, xr["rho_s"], y, v, cov)
    eff = []
    for i in range(2):
        Pii = float(Pc[i, i])
        if np.isfinite(v[i]):
            inv = 1.0 / Pii - 1.0 / v[i]
            s2 = 1.0 / inv if inv > 1e-12 else sd[i] ** 2
            s2 = min(s2, sd[i] ** 2)
            mean = s2 * (m[i] / Pii - y[i] / v[i])
        else:
            s2, mean = Pii, float(m[i])
        eff.append((float(mean), float(np.sqrt(s2))))
    out = []
    pris, us_, rows_, cfgs_ = (pri_a, pri_b), (ua, ub), (rows_a, rows_b), (cfg_a, cfg_b)
    for i in range(2):
        j = 1 - i
        c2 = {**cfgs_[i], "model": {**cfgs_[i].get("model", {}), "swing_prior_mean": eff[i][0], "swing_prior_sd": eff[i][1]}}
        pr, n_shift = pris[i], 0
        if county_info:
            beta = xr["beta_ab"] if i == 0 else xr["beta_ba"]
            uid = pris[i]["unit_id"]
            nj = uid.map(us_[j]["n"]).fillna(0.0).to_numpy(float)
            aj = uid.map(us_[j]["a"]).fillna(0.0).to_numpy(float)
            ni = uid.map(us_[i]["n"]).fillna(0.0).to_numpy(float)
            sj = uid.map(pris[j].set_index("unit_id")["s0"]).to_numpy(float)
            # the other race's county deviation from its posterior statewide swing, where it has counted votes and this race has none
            dev = np.where(nj > 0, aj / np.where(nj > 0, nj, 1.0) - np.nan_to_num(sj) - m[j], 0.0)
            use = (nj > 0) & (ni == 0)
            if use.any():
                pr = pris[i].assign(s0=np.clip(pris[i]["s0"].to_numpy(float) + np.where(use, beta * dev, 0.0), 0.005, 0.995))
                n_shift = int(use.sum())
        res, units = us.project(rows_[i], c2, pr)
        res["xr_swing_prior_mean"], res["xr_swing_prior_sd"], res["xr_county_shift_n"] = eff[i][0], eff[i][1], n_shift
        out.append((res, units))
    return out[0], out[1]

