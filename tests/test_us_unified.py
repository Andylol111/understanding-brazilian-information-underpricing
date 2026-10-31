"""Unit tests of brunder.us_unified numerics (synthetic data; the panels are not needed)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import us_unified as uu


def test_wridge_recovers_coefficients_and_penalises_only_marked_columns():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 3))
    y = 0.2 + X @ np.array([1.0, 0.5, 0.0]) + rng.normal(scale=0.01, size=500)
    b0, b = uu.wridge(X, y)
    assert abs(b0 - 0.2) < 0.01 and np.allclose(b, [1.0, 0.5, 0.0], atol=0.01)
    _, bp = uu.wridge(X, y, lam=100.0, pen=np.array([0.0, 1.0, 1.0]))
    assert abs(bp[0] - 1.0) < 0.05          # unpenalised column keeps its coefficient
    assert abs(bp[1]) < 0.1                 # penalised ones shrink toward zero


def test_blend_weight_falls_with_polls_and_is_one_without():
    n = np.array([0, 1, 4, 16])
    w = uu._blend_w(n, 0.5, 1.0)
    assert w[0] == 1.0 and np.all(np.diff(w[1:]) < 0) and w[1] < 1.0


def test_n_bin_edges():
    assert list(uu.n_bin([0, 1, 2, 3, 5, 6, 10, 11])) == [0, 1, 1, 2, 2, 3, 3, 4]


def test_score_perfect_and_calibrated_normal():
    y = np.array([0.6, 0.4])
    s = uu.score(y, y, np.array([0.02, 0.02]))
    assert s["mae"] == 0 and s["cov90"] == 1.0 and s["brier"] < 1e-6
    rng = np.random.default_rng(1)
    mu = rng.uniform(0.4, 0.6, 20000)
    y = mu + rng.normal(scale=0.03, size=20000)
    s = uu.score(y, mu, np.full(20000, 0.03))
    assert abs(s["cov90"] - 0.90) < 0.01
    # CRPS of a standard normal forecast of a standard normal draw: 1/sqrt(pi) * (sqrt(2) - 1)... check against sampling
    assert 0.0 < s["crps"] < 3.0


def test_county_prior_recentres_on_state_share_and_scales_votes():
    C = pd.DataFrame({"fips": ["1", "2", "3"], "race": "r", "w0": [100.0, 50.0, 10.0], "sp_b": [0.7, 0.5, 0.3]})
    C = uu.add_dev(C, ["sp_b"])
    share = {"feats": ["sp_b"], "b0": 0.0, "b": {"sp_b": 0.8}}
    turn = {"feats": [], "b0": 0.0, "b": {}}
    D = C.assign(l_lp=np.log(C["w0"] / C["w0"].sum()))
    D = D.assign(**{})
    pr = uu.county_prior(D, share, turn, 0.55, 1_000_000.0)
    assert abs(np.average(pr["s0"], weights=pr["share_v"]) - 0.55) < 1e-9
    assert abs(pr["V"].sum() - 1_000_000.0) < 1e-6
    assert pr["s0"].iloc[0] > pr["s0"].iloc[2]          # keeps the county ordering


def test_nat_fit_predict_roundtrip():
    n = pd.DataFrame({"cycle": [2010, 2012, 2014, 2016], "y_c": [-0.05, 0.0, -0.03, -0.01], "gb_d": [-0.06, 0.0, -0.04, 0.0]})
    m = uu.fit_nat(n)
    p = uu.predict_nat(m, n.iloc[0])
    assert abs(float(p[0]) - (-0.05)) < 0.02
    oof = uu.nat_oof(n)
    assert set(oof) == {2010, 2012, 2014, 2016}
