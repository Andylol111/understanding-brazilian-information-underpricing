"""Hispanic-origin ecological regression (brunder.origin_ei) on synthetic tracts with known group preferences."""
import numpy as np

from brunder import origin_ei as ei

K = 6                                    # e.g. Mexican, Puerto Rican, Cuban, other Hispanic, non-Hispanic white, Black


def _world(seed=0, n_states=3, areas_per=3, units=250, noise=0.02):
    rng = np.random.default_rng(seed)
    n_areas = n_states * areas_per
    state = np.repeat(np.arange(n_states), areas_per)
    pref_nat = np.array([0.62, 0.72, 0.40, 0.55, 0.40, 0.90])
    turn_nat = np.array([0.45, 0.50, 0.65, 0.45, 0.65, 0.55])
    pref = np.clip(pref_nat + rng.normal(0, 0.03, (n_states, K))[state] + rng.normal(0, 0.02, (n_areas, K)), 0.05, 0.95)
    turn = np.clip(turn_nat + rng.normal(0, 0.02, (n_areas, K)), 0.2, 0.9)
    bd, br = pref * turn, (1 - pref) * turn
    X, area, cvap = [], [], []
    for a in range(n_areas):
        conc = rng.dirichlet(np.ones(K) * 0.7) * 4 + 0.05
        X.append(rng.dirichlet(conc, size=units))
        area.append(np.full(units, a))
        cvap.append(rng.integers(500, 4000, units))
    X, area, cvap = np.vstack(X), np.concatenate(area), np.concatenate(cvap).astype(float)
    yd = (X * bd[area]).sum(1) + rng.normal(0, noise, len(X))
    yr = (X * br[area]).sum(1) + rng.normal(0, noise, len(X))
    return {"X": X, "area": area, "cvap": cvap, "yd": yd, "yr": yr, "pref": pref, "turn": turn, "state": state, "n_areas": n_areas}


def _fit(w, seed=1, **kw):
    sd, sr = (ei.suff_stats(w["X"], y, w["cvap"], w["area"], w["n_areas"]) for y in (w["yd"], w["yr"]))
    f = ei.fit(sd, sr, w["state"], seed=seed, n_draws=250, burn=250, **kw)
    return f["bd"], f["br"]


def test_suff_stats_match_direct_regression():
    w = _world(n_states=1, areas_per=2, units=40)
    s = ei.suff_stats(w["X"], w["yd"], w["cvap"], w["area"], w["n_areas"])
    m = w["area"] == 1
    assert np.allclose(s.A[1], (w["X"][m] * w["cvap"][m, None]).T @ w["X"][m])
    assert np.allclose(s.b[1], (w["X"][m] * w["cvap"][m, None]).T @ w["yd"][m])
    assert s.n.tolist() == [40, 40]


def test_recovers_known_preferences_and_turnout():
    w = _world()
    bd, br = _fit(w)
    pref, turn = ei.preference(bd, br)
    med, lo, hi = ei.summarise(pref)
    err = np.abs(med - w["pref"])
    assert err.mean() < 0.04 and err.max() < 0.15
    assert np.abs(ei.summarise(turn)[0] - w["turn"]).mean() < 0.04
    cover = ((w["pref"] >= lo) & (w["pref"] <= hi)).mean()
    assert cover > 0.75                                     # nominal 90 %, deliberately loose for a short chain


def test_draws_are_bounded_and_intervals_widen_with_noise():
    quiet, loud = _world(noise=0.005), _world(noise=0.05)
    widths = []
    for w in (quiet, loud):
        bd, br = _fit(w)
        assert bd.min() >= 0 and bd.max() <= 1
        pref, _ = ei.preference(bd, br)
        _, lo, hi = ei.summarise(pref)
        widths.append(np.mean(hi - lo))
    assert widths[1] > widths[0]


def test_pooling_helps_an_area_with_collinear_groups():
    """In one area two groups always move together, so only the state/nation prior can split them."""
    w = _world(seed=3)
    a = 0
    m = w["area"] == a
    X = w["X"].copy()
    X[m, 3] = X[m, 2] * 0.5                                  # group 3 proportional to group 2 inside area 0
    X[m] /= X[m].sum(1, keepdims=True)
    w["X"] = X
    w["yd"] = (X * (w["pref"] * w["turn"])[w["area"]]).sum(1) + np.random.default_rng(9).normal(0, 0.02, len(X))
    w["yr"] = (X * ((1 - w["pref"]) * w["turn"])[w["area"]]).sum(1) + np.random.default_rng(10).normal(0, 0.02, len(X))
    bd, br = _fit(w)
    pref, _ = ei.preference(bd, br)
    med, lo, hi = ei.summarise(pref)
    # the pooled estimate for the collinear pair stays near the truth of the other areas of the state, with a wider interval
    assert np.abs(med[a, 2:4] - w["pref"][a, 2:4]).max() < 0.25
    assert (hi - lo)[a, 3] > (hi - lo)[4, 3]


def test_aggregate_is_cvap_weighted():
    rng = np.random.default_rng(0)
    bd = rng.uniform(0.1, 0.3, (5, 4, 3))
    br = rng.uniform(0.1, 0.3, (5, 4, 3))
    cv = rng.uniform(100, 1000, (4, 3))
    pref, turn = ei.aggregate(bd, br, cv, np.array([True, True, False, False]))
    d = (bd[:, :2] * cv[:2]).sum(1)
    r = (br[:, :2] * cv[:2]).sum(1)
    assert np.allclose(pref, d / (d + r))
    assert np.allclose(turn, (d + r) / cv[:2].sum(0))
