"""Helpers of the Hispanic-origin pipeline (analysis/us_origin_*.py) on synthetic data; no data files are read."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_origin_fit as M
import us_origin_frames as F
import us_origin_report as Rp

from brunder import origin_ei as ei

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_origin_ei import K, _world


def test_context_terms_average_to_zero_per_area():
    w = _world(seed=2, n_states=1, areas_per=3, units=60)
    Z, e = ei.context_terms(w["X"], w["cvap"], w["area"], w["n_areas"])
    for a in range(w["n_areas"]):
        m = w["area"] == a
        assert np.allclose((w["cvap"][m, None] * w["X"][m] * (w["X"][m] - e[a])).sum(0), 0.0, atol=1e-6)
        assert np.allclose(Z[m], w["X"][m] * (w["X"][m] - e[a]))


def test_turnout_prior_anchors_collinear_groups():
    """Two groups that always move together: without a turnout prior their split is the prior's; with one it follows the truth."""
    w = _world(seed=5, n_states=1, areas_per=4, units=200)
    X = w["X"].copy()
    X[:, 3] = X[:, 2] * 0.6
    X /= X.sum(1, keepdims=True)
    bd, br = w["pref"] * w["turn"], (1 - w["pref"]) * w["turn"]
    rng = np.random.default_rng(1)
    yd = (X * bd[w["area"]]).sum(1) + rng.normal(0, 0.01, len(X))
    yr = (X * br[w["area"]]).sum(1) + rng.normal(0, 0.01, len(X))
    sd, sr = (ei.suff_stats(X, y, w["cvap"], w["area"], w["n_areas"]) for y in (yd, yr))
    free = ei.fit(sd, sr, w["state"], n_beta=K, n_draws=150, burn=200, seed=3)
    anch = ei.fit(sd, sr, w["state"], n_beta=K, n_draws=150, burn=200, seed=3, turn_mean=w["turn"], turn_sd=0.03)
    err = lambda f: np.abs((f["bd"] + f["br"]).mean(0)[:, 2:4] - w["turn"][:, 2:4]).mean()
    assert err(anch) < err(free)
    assert err(anch) < 0.05


def test_fixed_prior_is_returned_where_there_is_no_data():
    w = _world(seed=1, n_states=1, areas_per=3, units=80)
    keep = w["area"] != 2                                      # area 2 has no units
    sd, sr = (ei.suff_stats(w["X"][keep], y[keep], w["cvap"][keep], w["area"][keep], w["n_areas"]) for y in (w["yd"], w["yr"]))
    centre_d, centre_r = np.full((3, K), 0.2), np.full((3, K), 0.1)
    f = ei.fit(sd, sr, np.zeros(3, int), n_beta=K, n_draws=200, burn=150, seed=0, prior_d=centre_d, prior_r=centre_r, prior_sd=0.02)
    assert np.allclose(f["bd"][:, 2].mean(0), 0.2, atol=0.01) and np.allclose(f["br"][:, 2].mean(0), 0.1, atol=0.01)
    assert f["bd"][:, 2].std(0).mean() > 0.01                  # still a distribution, not a point


def test_shift_to_totals_matches_each_area():
    rng = np.random.default_rng(0)
    area = rng.integers(0, 4, 400)
    v = rng.uniform(100, 2000, 400)
    d = v * rng.uniform(0.3, 0.7, 400)
    d_t = np.bincount(area, weights=d, minlength=4)
    p = Rp._shift_to_totals(rng.normal(0, 1, 400), v, d_t, area, 4)
    assert np.allclose(np.bincount(area, weights=v * p, minlength=4), d_t, rtol=1e-6)


def test_group_cvap_raked_to_published_totals(monkeypatch):
    rng = np.random.default_rng(3)
    n = 20
    tr = pd.DataFrame({"geoid": [f"12{i:09d}" for i in range(n)], "pop": rng.integers(3000, 6000, n).astype(float)})
    for g in F.HISP:
        tr[g] = rng.integers(0, 400, n).astype(float)
    tr["hisp"] = tr[F.HISP].sum(axis=1)
    tr["nh_white"], tr["nh_black"], tr["nh_asian"] = (rng.integers(200, 2000, n).astype(float) for _ in range(3))
    tr["h_cvap"] = tr["hisp"] * 0.55
    tr["a_cvap"] = tr["h_cvap"] + (tr["pop"] - tr["hisp"]) * 0.7
    ratios = pd.DataFrame({g: [0.5 + 0.02 * i] for i, g in enumerate(F.GROUPS)}, index=pd.Index([12], name="state_fips"))
    monkeypatch.setattr(F, "ratios", lambda vintage: ratios)
    cv = F.group_cvap(tr, 2024)
    assert np.allclose(cv[F.HISP].sum(axis=1), tr["h_cvap"])
    assert np.allclose(cv[F.NH].sum(axis=1), tr["a_cvap"] - tr["h_cvap"])
    assert (cv >= 0).all().all()


def test_turnout_prior_reproduces_area_votes():
    rng = np.random.default_rng(2)
    groups = M.VARIANTS["origin"]
    cv = rng.uniform(1e3, 1e5, (6, len(groups)))
    votes = cv.sum(1) * rng.uniform(0.5, 0.7, 6)
    t0, sd = M.turnout_prior(2020, groups, cv, votes)
    assert np.allclose((cv * t0).sum(1), votes, rtol=0.05)      # clipping at 2 % / 98 % is the only source of slack
    assert t0[:, groups.index("nh_white")].mean() > t0[:, groups.index("mexican")].mean()
    assert sd.shape == t0.shape


def test_area_regions_cover_states_and_nest():
    areas = pd.DataFrame({"area": ["Miami-Dade", "Broward", "rest-12", "Harris", "rest-48"],
                          "state_fips": ["12", "12", "12", "48", "48"]})
    r = Rp.area_regions(areas)
    assert r["Florida"].tolist() == [True, True, True, False, False]
    assert r["Texas"].sum() == 2 and r["United States (contiguous)"].all()
    assert r["Rest of Florida"].tolist() == [False, False, True, False, False]
