"""brunder.nowcast_order: live reporting-order slope on top of the base nowcast (synthetic data, no network)."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from brunder import nowcast as nc
from brunder import nowcast_order as no


def world(gamma=0.04, seed=3, n_units=240, count_frac=0.3):
    """Units in 4 states; the true swing falls with unit size (small = rural swings toward A by `gamma` per sd of log size).
    The smallest units report first: the state shows the smallest `count_frac` of units, completely counted."""
    rng = np.random.default_rng(seed)
    uf = np.repeat(["a", "b", "c", "d"], n_units // 4)
    V0 = np.exp(rng.normal(8.0, 1.0, n_units))
    s0 = np.clip(rng.normal(0.5, 0.08, n_units), 0.1, 0.9)
    x = (np.log(V0) - np.log(V0).mean()) / np.log(V0).std()
    final_s = np.clip(s0 - 0.01 - gamma * x + rng.normal(0, 0.01, n_units), 0.01, 0.99)
    prior = pd.DataFrame({"uf": uf, "cd_mun": [f"{i:04d}" for i in range(n_units)], "s0": s0, "V0": V0})
    k = int(count_frac * n_units)
    first = np.argsort(V0)[:k]
    n = np.zeros(n_units)
    a = np.zeros(n_units)
    f = np.zeros(n_units)
    n[first], a[first], f[first] = V0[first], (V0 * final_s)[first], 1.0
    state = prior[["uf", "cd_mun"]].assign(n=n, a=a, f=f)
    state = state[state["n"] > 0].reset_index(drop=True)
    truth = float((V0 * final_s).sum() / V0.sum())
    return state, prior, truth


def test_none_is_the_base_model():
    state, prior, _ = world()
    base = nc.nowcast(state, prior, nc.Params(tau=0.01))
    out = no.order_nowcast(state, prior, nc.Params(tau=0.01), no.OrderParams("none"))
    assert out["margin_hat"] == base["margin_hat"] and out["margin_sd"] == base["margin_sd"]


def test_tiny_slope_prior_recovers_the_base_model():
    state, prior, _ = world()
    base = nc.nowcast(state, prior, nc.Params(tau=0.01))
    out = no.order_nowcast(state, prior, nc.Params(tau=0.01), no.OrderParams("size", 1e-9))
    assert out["share_hat"] == pytest.approx(base["share_hat"], abs=1e-6)
    assert out["margin_sd"] == pytest.approx(base["margin_sd"], rel=1e-4)


def test_size_slope_removes_a_lopsided_count_bias_and_widens_the_band():
    state, prior, truth = world(gamma=0.04)
    p = nc.Params(tau=0.005)
    base = nc.nowcast(state, prior, p)
    out = no.order_nowcast(state, prior, p, no.OrderParams("size", 0.05))
    assert abs(out["share_hat"] - truth) < 0.5 * abs(base["share_hat"] - truth)      # bias mostly removed
    assert abs(base["share_hat"] - truth) > 0.004                                     # and there was a bias to remove
    assert out["hisp_beta"] < -0.02                                                   # swing falls with size
    assert out["margin_sd"] > base["margin_sd"]                                       # slope uncertainty is carried
    assert abs(truth - out["share_hat"]) < 1.645 * out["share_sd"]                    # and the band holds the truth


def test_no_slope_in_the_world_gives_an_insignificant_slope_and_a_wider_band():
    state, prior, truth = world(gamma=0.0)
    p = nc.Params(tau=0.005)
    base = nc.nowcast(state, prior, p)
    out = no.order_nowcast(state, prior, p, no.OrderParams("size", 0.05))
    assert abs(out["hisp_beta"]) < 2 * out["hisp_beta_sd"]                            # no significant slope
    assert abs(out["share_hat"] - base["share_hat"]) < out["share_sd"]                # the shift is within the band
    assert out["share_sd"] > base["share_sd"]                                         # the price of not knowing: wider


def test_covariate_uses_only_the_prior():
    state, prior, _ = world()
    x0 = no.covariate(prior, "size")
    p2 = prior.copy()
    p2.loc[p2.index[:5], "s0"] = 0.9                       # a change to the prior moves 'lean', not 'size'
    assert no.covariate(p2, "size").equals(x0)
    assert not no.covariate(p2, "lean").equals(no.covariate(prior, "lean"))
    w = prior["V0"]
    assert float((x0 * w).sum() / w.sum()) == pytest.approx(0.0, abs=1e-9) or abs((x0 * w).sum() / w.sum()) < 0.5
    assert no.covariate(prior, "size").isna().sum() == 0


def test_size_column_overrides_v0_and_unpooled_units_get_no_covariate():
    _, prior, _ = world()
    prior = prior.assign(size=1.0 + np.arange(len(prior)))
    prior.loc[prior.index[:3], "uf"] = "zz"
    x = no.covariate(prior, "size")
    assert x.iloc[:3].isna().all() and x.iloc[3:].notna().all()
    assert x.iloc[10] < x.iloc[200]                        # follows the size column, not V0


def test_imbalance_is_negative_when_small_units_report_first_and_zero_when_all_counted():
    state, prior, _ = world()
    assert no.order_imbalance(state, prior, "size") < -0.8
    full = prior[["uf", "cd_mun"]].assign(n=prior["V0"], a=prior["V0"] * prior["s0"], f=1.0)
    assert no.order_imbalance(full, prior, "size") == pytest.approx(0.0, abs=1e-9)
    assert no.order_imbalance(state.iloc[:0], prior, "size") == 0.0


def test_swing_correlation_sign_follows_the_world():
    s_neg, prior, _ = world(gamma=0.04)
    assert no.swing_correlation(s_neg, prior, "size") < -0.5
    s_pos, prior2, _ = world(gamma=-0.04)
    assert no.swing_correlation(s_pos, prior2, "size") > 0.5


def test_with_tau_matches_a_direct_run():
    state, prior, _ = world()
    r0 = no.order_nowcast(state, prior, nc.Params(tau=0.0), no.OrderParams("size", 0.03))
    r2 = no.order_nowcast(state, prior, nc.Params(tau=0.02), no.OrderParams("size", 0.03))
    s = no.with_tau(r0, 0.0, 0.02)
    assert s["margin_sd"] == pytest.approx(r2["margin_sd"], rel=1e-9)
    assert s["share_sd"] == pytest.approx(r2["share_sd"], rel=1e-9)
    assert s["p_win"] == pytest.approx(r2["p_win"], rel=1e-9)


def test_params_validate():
    with pytest.raises(ValueError):
        no.OrderParams("rural")
    with pytest.raises(ValueError):
        no.OrderParams("size", 0.0)


# ------------------------------------------------------------------------------------------------ hierarchical tau
def test_needed_tau_is_the_smallest_grid_value_that_covers_and_is_monotone_in_the_error():
    n = 40
    margin_sd0 = np.full(n, 1000.0)
    total = np.full(n, 1e6)
    two_r = np.full(n, 1.6e6)                       # sd(tau) = sqrt(1000^2 + (1.6e6 tau)^2) / 2e6
    small = needed_err(0.0006, n)                   # inside the tau = 0 band (z = 1.645 * 0.0005 = 0.00082)
    big = needed_err(0.01, n)
    assert no.needed_tau(small, margin_sd0, two_r, total) == 0.0
    t_big = no.needed_tau(big, margin_sd0, two_r, total)
    assert t_big > 0
    sd = np.sqrt(1000.0**2 + (1.6e6 * t_big) ** 2) / 2e6
    assert 0.01 < 1.645 * sd                        # covers at the returned tau ...
    prev = max(g for g in no.TAU_GRID if g < t_big)
    assert 0.01 >= 1.645 * np.sqrt(1000.0**2 + (1.6e6 * prev) ** 2) / 2e6   # ... and not at the previous grid value
    assert no.needed_tau(needed_err(5.0, n), margin_sd0, two_r, total) == no.TAU_GRID[-1]   # nothing covers: largest grid value


def needed_err(x, n):
    return np.full(n, x)


def test_mixture_with_one_tau_is_the_point_forecast_and_with_several_is_wider_than_the_narrowest():
    from scipy.stats import norm
    sh, m0, tr, tot = np.array([0.5]), np.array([1000.0]), np.array([1.6e6]), np.array([1e6])
    lo, hi = no.mixture_band(sh, m0, tr, tot, [0.01])
    sd = np.sqrt(1000.0**2 + (1.6e6 * 0.01) ** 2) / 2e6
    assert lo[0] == pytest.approx(0.5 - 1.6449 * sd, rel=1e-4) and hi[0] == pytest.approx(0.5 + 1.6449 * sd, rel=1e-4)
    lo2, hi2 = no.mixture_band(sh, m0, tr, tot, [0.0, 0.03])
    lo0, hi0 = no.mixture_band(sh, m0, tr, tot, [0.0])
    assert hi2[0] - lo2[0] > hi0[0] - lo0[0]
    assert (lo2 < sh).all() and (hi2 > sh).all()
    pw = no.mixture_pwin(np.array([3000.0]), m0, tr, [0.0, 0.03])
    assert pw[0] == pytest.approx(0.5 * (norm.cdf(3000 / 1000.0) + norm.cdf(3000 / np.sqrt(1000.0**2 + (1.6e6 * 0.03) ** 2))))
    assert no.mixture_pwin(np.array([3000.0]), m0, tr, [0.03])[0] < no.mixture_pwin(np.array([3000.0]), m0, tr, [0.0])[0]


def test_frame_arrays_inverts_the_nowcast_output():
    state, prior, _ = world()
    r = nc.nowcast(state, prior, nc.Params(tau=0.0))
    fr = pd.DataFrame([{"margin_sd0": r["margin_sd"], "share_sd0": r["share_sd"], "counted": r["counted_frac"]}])
    a = no.frame_arrays(fr)
    r2 = nc.nowcast(state, prior, nc.Params(tau=0.02))
    sd = no._sd_matrix(a["margin_sd0"], a["two_r"], a["total"], [0.02])[0, 0]
    assert sd == pytest.approx(r2["share_sd"], rel=1e-9)
