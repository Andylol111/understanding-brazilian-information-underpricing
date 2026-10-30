"""analysis/us_drift.py: drift measures without look-ahead, cycle-fixed-effect out-of-cycle tests, 2026 scoring."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def m():
    sys.path.insert(0, str(ROOT / "analysis"))
    spec = importlib.util.spec_from_file_location("us_drift", ROOT / "analysis" / "us_drift.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["us_drift"] = mod
    spec.loader.exec_module(mod)
    return mod


def _panel(slopes: dict[int, float], n: int = 30, seed: int = 0, noise: float = 0.5) -> pd.DataFrame:
    """Races whose miss = cycle shift + slope[cycle] * x + noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for c, b in slopes.items():
        x = rng.normal(0, 2, n)
        shift = rng.normal(0, 3)
        for i in range(n):
            rows.append({"race": f"{c}-{i}", "cycle": c, "state": f"s{i}", "office": "sen", "x": x[i],
                         "miss": shift + b * x[i] + rng.normal(0, noise)})
    return pd.DataFrame(rows)


def test_cycle_fixed_effects_remove_the_common_miss(m):
    d = m.demean(_panel({2006: 1.0, 2008: 1.0, 2010: 1.0}), "x", ["cycle"])
    assert d.groupby("cycle")["y"].mean().abs().max() < 1e-9
    assert d.groupby("cycle")["x"].mean().abs().max() < 1e-9
    assert m.slope(d) == pytest.approx(1.0, abs=0.1)


def test_leave_one_cycle_out_never_fits_on_the_held_out_cycle(m):
    d = m.demean(_panel({2006: 1.0, 2008: 1.0, 2010: -1.0}), "x", ["cycle"])
    P = m.predict(d, "loco")
    b = P.groupby("cycle")["beta"].first()
    assert b[2006] == pytest.approx(0.0, abs=0.2)             # fitted on +1 and -1 cycles
    assert b[2010] == pytest.approx(1.0, abs=0.1)             # fitted on the two +1 cycles only
    past = m.predict(d, "past")
    assert set(past["cycle"]) == {2010}                       # needs two earlier cycles


def test_stable_signal_beats_baseline_and_flipping_signal_does_not(m):
    stable = m.evaluate(_panel({c: 1.0 for c in range(2006, 2026, 2)}), "x")
    flip = m.evaluate(_panel({c: (1.0 if i % 2 else -1.0) for i, c in enumerate(range(2006, 2026, 2))}), "x")
    assert stable["d_rmse"] < -1 and stable["d_hi"] < 0 and stable["agree_lo"] > 0.5
    assert stable["cycles_improved"] == stable["cycles"]
    assert flip["d_rmse"] > 0 and flip["agree"] < 0.6


def test_cross_era_fit_uses_only_the_other_era(m):
    d = m.demean(_panel({2006: 1.0, 2008: 1.0, 2016: -1.0, 2018: -1.0}), "x", ["cycle"])
    P = m.predict(d, "cross")
    assert P.loc[P["cycle"] <= 2014, "beta"].iloc[0] == pytest.approx(-1.0, abs=0.1)
    assert P.loc[P["cycle"] >= 2016, "beta"].iloc[0] == pytest.approx(1.0, abs=0.1)


def test_sign_agreement_and_binomial(m):
    P = pd.DataFrame({"cycle": [1, 1, 1, 2], "state": list("abcd"), "x": [1.0, -1, 1, 1], "pred": [1.0, -1, 1, -1],
                      "y": [2.0, -1, -1, 3]})
    a = m.aggregate(m.cycle_stats(P))
    assert (a["k"], a["ns"]) == (2, 4)
    assert m.aggregate(m.cycle_stats(P, fixed_sign=True))["k"] == 3
    assert m.binom_p(10, 10) == pytest.approx(0.5**10)


def test_cluster_sign_counts_a_states_two_races_once(m):
    P = pd.DataFrame({"cycle": [2022] * 4, "state": ["ga", "ga", "oh", "oh"], "pred": [1.0, 1, 1, 1],
                      "x": [1.0] * 4, "y": [1.0, 1, -1, -1]})
    assert m.cluster_sign_p(P)[:2] == (1, 2)                  # ga agrees, oh does not


def test_office_trend_uses_only_elections_before_the_race(m):
    h = object.__new__(m.History)
    h.sen = {("ga", 2014): -3.0, ("ga", 2016): -1.0, ("ga", 2020): 2.0, ("ga", 2022): 3.0}
    h.gov = {("ga", 2014): -4.0, ("ga", 2018): 0.0}
    h.p = {}
    t, y1, y2 = h.office_trend("sen", "ga", 2020)             # 2020's own result is unknown: 2016 vs 2014
    assert (y1, y2) == (2016, 2014) and t == pytest.approx((-1 - -3) * 4 / 2)
    t, y1, y2 = h.office_trend("sen", "ga", 2026)             # 2022 vs 2020, 2 years apart
    assert (y1, y2) == (2022, 2020) and t == pytest.approx(2.0)
    assert np.isnan(h.office_trend("gov", "ga", 2026)[0])     # latest governor race 8 years before: stale
    t, _, _ = h.office_trend("gov", "ga", 2022)
    assert t == pytest.approx(4.0)                             # 2018 vs 2014, 4 years apart


def test_presidential_drift_skips_the_same_year_result(m):
    h = object.__new__(m.History)
    h.p = {("fl", 2004): -3.0, ("fl", 2008): -1.0, ("fl", 2012): 0.0, ("fl", 2016): 4.0, ("fl", 2020): 6.0}
    assert h.pres_drift("fl", 2016, 1) == pytest.approx(0.0 - -1.0)       # 2012 vs 2008; 2016 itself unknown
    assert h.pres_drift("fl", 2018, 2) == pytest.approx(4.0 - -1.0)       # 2016 vs 2008
    assert h.pres_drift("fl", 2022, 3) == pytest.approx(6.0 - -1.0)       # 2020 vs 2008
    assert h.pres_drift("fl", 2022, 1) == pytest.approx(6.0 - 4.0)


def test_acs_windows_precede_the_election(m):
    assert m.acs_end(2022) == 2021 and m.acs_end(2026) == 2024
    assert m.acs_start(2021, 4, 3, 5) == 2017 and m.acs_start(2021, 8, 7, 9) == 2013
    assert m.acs_start(2024, 4, 3, 5) == 2019                  # 2020 does not exist; tie goes to the longer window
    assert m.acs_start(2011, 8, 7, 9) is None                  # no ACS year early enough
    assert all(m.acs_end(t) < t for t in range(2008, 2027, 2))


def test_netmig_requires_every_year(m):
    e = object.__new__(m.Electorate)
    e.nm = pd.Series({(1, y): 1.0 for y in range(2011, 2020)})
    assert e.netmig(1, 2016, 4) == pytest.approx(1.0)
    assert np.isnan(e.netmig(1, 2012, 4))                      # 2008-2011: pre-2011 years missing


def test_miss_is_certified_minus_poll_average(m):
    rows = [{"cycle": 2018, "race": "2018_Sen-G_OH", "race_id": 1, "type": "Sen-G", "state": "oh", "office": "sen",
             "special": False, "pollster": p, "pid": "p" + p, "partisan": None, "tte": t, "n": 800.0,
             "dem": 100 * s * 0.96, "opp": 100 * (1 - s) * 0.96, "s": s, "act": 0.55, "err": s - 0.55}
            for p, t, s in [("A", 4, 0.52), ("B", 9, 0.52)]]
    d = m.miss_table(pd.DataFrame(rows))
    assert d["miss"].iloc[0] == pytest.approx(3.0, abs=1e-6)  # certified 55 - poll 52: polls understated the Democrat


def test_score_2026_sign_agreement(m):
    pred = pd.DataFrame({"race": ["a-sen", "a-gov", "b-sen", "c-sen", "d-gov"], "state": list("aabcd"),
                         "avg": [50.0, 50, 50, 50, 50], "pred": [1.0, 1.0, -1.0, -1.0, 1.0]})
    cert = pd.DataFrame({"race": pred["race"], "d2": [0.53, 0.52, 0.47, 0.50, 0.44]})
    # misses +3 +2 -3 0 -6, mean -0.8 -> relative +3.8 +2.8 -2.2 +0.8 -5.2
    r = m.score_2026(pred, cert)
    assert r["signed"] == 5 and r["agree"] == 3
    assert r["p_binom"] == pytest.approx(0.5)                  # P(X >= 3 | n=5) = 0.5
    assert r["states"] == 4 and r["states_agree"] == 2


def test_lean_config_is_registered_separately_with_the_shared_family(m, tmp_path, monkeypatch):
    import tomllib
    monkeypatch.setattr(m, "ROOT", tmp_path)
    r = pd.DataFrame({"race": ["a-sen", "b-gov"], "state": ["a", "b"], "avg": [50.0, 52.0], "n_polls": [3, 4],
                      "pred": [1.0, -1.0], "lo90": [-2.0, -4.0], "hi90": [4.0, 2.0], "direction": ["D", "R"],
                      "pres_lean": [10.0, -10.0]})
    meta = {"measure": "pres_lean", "beta": 0.1, "beta_boot_95": [0.05, 0.15], "loco_resid_sd": 2.3,
            "races_fitted": 700, "cycles": 10}
    ch = {"d_rmse": -0.1, "d_lo": -0.2, "d_hi": -0.01, "agree": 0.6}
    res = pd.DataFrame([{"measure": "pres_lean", "sample": s, "mode": md, "d_rmse": -0.1, "d_lo": -0.2, "d_hi": -0.01}
                        for s, md in [("all", "cross-era"), ("era 2006-14", "loco"), ("era 2016-24", "loco")]])
    path = m.write_prereg_2026(r, meta, ch, res, pd.DataFrame())
    d = tomllib.loads(path.read_text())
    assert d["id"] == "us2026_lean_v1" and d["status"] == "unregistered" and "AFTER" in d["selection_note"]
    assert d["primary"]["family"] == ["us2026_drift_v1", "us2026_lean_v1"]
    assert d["primary"]["family_correction"] == "Holm" and d["primary"]["family_alpha"] == 0.05
    assert [p["lean"] for p in d["predictions"]] == [10.0, -10.0]
