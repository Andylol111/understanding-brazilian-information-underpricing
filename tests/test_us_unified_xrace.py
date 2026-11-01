"""Unit tests of brunder.us_unified_xrace (synthetic data; the panels are not needed)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import us_unified_xrace as xr_




def test_joint_swing_posterior_limits():
    mu, sd = np.array([0.0, 0.0]), np.array([0.03, 0.04])
    m, P = xr_.joint_swing_posterior(mu, sd, 0.6, np.zeros(2), np.full(2, np.inf), 0.0)       # nothing observed: the prior
    assert np.allclose(m, 0) and np.allclose(P, [[0.03**2, 0.6 * 0.03 * 0.04], [0.6 * 0.03 * 0.04, 0.04**2]])
    y = np.array([0.02, np.nan])
    m, P = xr_.joint_swing_posterior(mu, sd, 0.6, y, np.array([1e-12, np.inf]), 0.0)          # A observed exactly: B moves by rho·sdB/sdA
    assert abs(m[0] - 0.02) < 1e-9 and abs(m[1] - 0.6 * 0.04 / 0.03 * 0.02) < 1e-6
    assert abs(P[1, 1] - 0.04**2 * (1 - 0.36)) < 1e-9
    m0, _ = xr_.joint_swing_posterior(mu, sd, 0.0, np.array([0.02, -0.01]), np.array([1e-4, 1e-4]), 0.0)   # rho 0, no noise covariance: independent
    m1, _ = xr_.joint_swing_posterior(mu, sd, 0.0, np.array([0.02, 0.0]), np.array([1e-4, 1e-4]), 0.0)
    assert abs(m0[0] - m1[0]) < 1e-12


def test_joint_swing_posterior_never_less_precise_than_own_data():
    mu, sd, v = np.zeros(2), np.array([0.03, 0.03]), np.array([4e-4, 4e-4])
    _, P = xr_.joint_swing_posterior(mu, sd, 0.5, np.array([0.01, 0.0]), v, 0.0)
    own = 1.0 / (1 / sd[0] ** 2 + 1 / v[0])
    assert P[0, 0] <= own + 1e-15
    _, Pc = xr_.joint_swing_posterior(mu, sd, 0.5, np.array([0.01, 0.0]), v, 0.9 * 4e-4)     # correlated sampling errors: less to gain
    assert Pc[0, 0] >= P[0, 0] - 1e-15


def test_estimate_crossrace_pairs_recovers_correlations():
    rng = np.random.default_rng(3)
    pairs = []
    for k in range(60):
        n = 40
        e = rng.normal(0, 0.02, n)
        ea, eb = e + rng.normal(0, 0.01, n), e + rng.normal(0, 0.01, n)
        za, zb = rng.normal(), rng.normal()
        zb = 0.7 * za + np.sqrt(1 - 0.49) * zb
        s0 = rng.uniform(0.3, 0.7, n)
        v = rng.uniform(1e3, 1e5, n)
        mk = lambda ee, z: pd.DataFrame({"fips": np.arange(n), "s0": s0, "s2": s0 + ee, "v2": v, "S_hat": 0.5, "sd": 0.03, "S_act": 0.5 + 0.03 * z})
        pairs.append((mk(ea, za), mk(eb, zb)))
    e = xr_.estimate_crossrace_pairs(pairs)
    assert abs(e["rho_e"] - 0.8) < 0.05 and abs(e["rho_s_raw"] - 0.7) < 0.2 and abs(e["beta_a_on_b"] - 0.8) < 0.05


def _toy_race(seed: int, n: int = 30):
    rng = np.random.default_rng(seed)
    uid = [f"us-xx-{i:05d}" for i in range(n)]
    V = rng.uniform(2e3, 5e4, n)
    s0 = rng.uniform(0.35, 0.65, n)
    return uid, V, s0


def _rows(uid, d, n, done):
    base = {"level": "county", "race": "x", "ballots": np.nan, "registered": np.nan, "outstanding": np.nan, "units": 1.0, "name": "",
            "candidate": "", "reporting": 1.0, "early": np.nan}
    u = np.asarray(uid)[done]
    return pd.concat([pd.DataFrame({**base, "unit_id": u, "party": "DEM", "votes": d[done]}),
                      pd.DataFrame({**base, "unit_id": u, "party": "REP", "votes": (n - d)[done]})], ignore_index=True)


def test_joint_project_without_counts_equals_separate_and_with_rho_zero_equals_separate():
    from brunder import us
    uid, V, s0 = _toy_race(1)
    pri = pd.DataFrame({"unit_id": uid, "name": uid, "s0": s0, "V": V})
    cfg = {"election": {"state": "xx", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
           "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "turnout_ratio": 1.0},
           "model": {"tau": 0.005, "rho": 0.3, "sigma_mun0": 0.02, "count_scaled_terms": True, "swing_prior_mean": 0.0, "swing_prior_sd": 0.03}}
    xr = {"rho_s": 0.7, "rho_e": 0.7, "beta_ab": 0.8, "beta_ba": 0.8}
    empty = _rows(uid, V * 0.5, V, np.zeros(len(uid), bool))
    (ra, _), (rb, _) = xr_.joint_project(empty, cfg, pri, empty, cfg, pri, xr)
    sep = us.project(empty, cfg, pri)[0]
    assert abs(ra["share_hat"] - sep["share_hat"]) < 1e-9 and abs(rb["share_sd"] - sep["share_sd"]) < 1e-9
    done = np.arange(len(uid)) < 12
    n = V.copy()
    ra_, rb_ = _rows(uid, (s0 + 0.04) * n, n, done), _rows(uid, (s0 - 0.01) * n, n, done)
    zero = {**xr, "rho_s": 0.0, "rho_n": 0.0}
    (ja, _), (jb, _) = xr_.joint_project(ra_, cfg, pri, rb_, cfg, pri, zero, county_info=False)
    sa, sb = us.project(ra_, cfg, pri)[0], us.project(rb_, cfg, pri)[0]
    assert abs(ja["share_hat"] - sa["share_hat"]) < 1e-9 and abs(jb["share_hat"] - sb["share_hat"]) < 1e-9


def test_joint_project_moves_uncounted_race_toward_counted_one_and_shifts_county_priors():
    from brunder import us
    uid, V, s0 = _toy_race(2)
    pri = pd.DataFrame({"unit_id": uid, "name": uid, "s0": s0, "V": V})
    cfg = {"election": {"state": "xx", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
           "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "turnout_ratio": 1.0},
           "model": {"tau": 0.005, "rho": 0.3, "sigma_mun0": 0.02, "count_scaled_terms": True, "swing_prior_mean": 0.0, "swing_prior_sd": 0.03}}
    xr = {"rho_s": 0.7, "rho_e": 0.7, "beta_ab": 0.8, "beta_ba": 0.8}
    done = np.arange(len(uid)) < 20
    ra = _rows(uid, (s0 + 0.05) * V, V, done)                      # race A: 20 counties in, all +5 points
    rb = _rows(uid, V * 0.5, V, np.zeros(len(uid), bool))          # race B: nothing counted
    (_, _), (jb, ub) = xr_.joint_project(ra, cfg, pri, rb, cfg, pri, xr)
    sb = us.project(rb, cfg, pri)[0]
    assert jb["share_hat"] > sb["share_hat"] + 0.01 and jb["share_sd"] < sb["share_sd"]
    assert jb["xr_county_shift_n"] == 20
