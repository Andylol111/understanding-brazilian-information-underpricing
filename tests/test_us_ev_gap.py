from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

P = Path(__file__).parents[1] / "analysis" / "us_ev_gap.py"
spec = importlib.util.spec_from_file_location("us_ev_gap", P)
eg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eg)


def _row(state, year, date, d, r, o=0, kind="total"):
    return {"state": state, "year": year, "date": date, "D": d, "R": r, "O": o, "kind": kind, "note": "cast"}


def test_same_day_never_uses_later_historical_date():
    p = pd.DataFrame([
        _row("aa", 2024, "2024-10-31", 40, 30),   # ED-5: eligible
        _row("aa", 2024, "2024-11-02", 50, 30),   # ED-3: forbidden look-ahead
        _row("aa", 2026, "2026-10-30", 60, 40),   # ED-4
    ])
    got = eg.select_same_day(p)
    old = got[got.year == 2024].iloc[0]
    assert old.date == pd.Timestamp("2024-10-31")
    assert old.date_lag == 1


def test_same_day_rejects_snapshot_more_than_two_days_stale():
    p = pd.DataFrame([
        _row("aa", 2024, "2024-10-29", 40, 30),   # ED-7, target ED-4
        _row("aa", 2026, "2026-10-30", 60, 40),
    ])
    got = eg.select_same_day(p)
    assert set(got.year) == {2026}


def test_metric_uses_only_prior_cycles_and_all_ballots_denominator():
    s = pd.DataFrame([
        _row("aa", 2020, "2020-10-30", 60, 30, 10),
        _row("aa", 2022, "2022-11-04", 40, 40, 20),
        _row("aa", 2024, "2024-11-01", 30, 50, 20),
    ])
    got = eg.ev_metrics(s).set_index("year")
    assert got.loc[2020, "ev_margin"] == 30
    assert np.isnan(got.loc[2020, "ev_gap"])
    assert got.loc[2022, "ev_gap"] == -30
    assert got.loc[2024, "ev_gap"] == -35  # -20 less the prior mean (+15)


def test_turnout_normalised_variant():
    s = pd.DataFrame([_row("aa", 2020, "2020-10-30", 50, 50), _row("aa", 2022, "2022-11-04", 36, 50)])
    r = pd.DataFrame([
        {"state": "aa", "year": 2020, "date": "2020-11-01", "D": 100, "R": 200, "O": 0},
        {"state": "aa", "year": 2022, "date": "2022-11-01", "D": 120, "R": 200, "O": 0},
    ])
    got = eg.ev_metrics(s, r).set_index("year")
    assert np.isclose(got.loc[2020, "ev_margin_norm"], 100 / 3)
    assert np.isclose(got.loc[2022, "ev_gap_norm"], 100 * 0.05 / 0.55 - 100 / 3)


def test_race_panel_cycle_fixed_effects_are_zero_mean():
    metrics = pd.DataFrame({"state": ["aa", "bb", "aa", "bb"], "year": [2020, 2020, 2022, 2022],
                            "ev_gap": [2.0, -1.0, 4.0, 0.0]})
    misses = pd.DataFrame({"race": ["r1", "r2", "r3", "r4"], "state": ["aa", "bb", "aa", "bb"],
                           "cycle": [2020, 2020, 2022, 2022], "type": ["Sen-G"] * 4,
                           "miss": [0.01, -0.02, 0.03, 0.00]})
    got = eg.race_panel(metrics, misses)
    assert np.allclose(got.groupby("cycle")["x_centered"].mean(), 0)
    assert np.allclose(got.groupby("cycle")["miss_centered"].mean(), 0)


def test_directional_sign_test_is_not_flipped_by_fitted_positive_slope():
    # Three cycles are needed for each leave-one-cycle-out fit. Here miss follows +x, contrary to the registered sign.
    rows = []
    for cycle in (2018, 2020, 2022):
        for state, x in (("aa", -1.0), ("bb", 1.0)):
            rows.append({"cycle": cycle, "state": state, "race": f"{cycle}-{state}", "type": "Sen-G",
                         "x": x, "x_centered": x, "miss": x, "miss_centered": x,
                         "cluster": f"{state}-{cycle}"})
    score = eg._score(pd.DataFrame(rows))
    assert score["sign_agreement"] == 0
    assert score["rmse_adjusted"] < score["rmse_none"]
