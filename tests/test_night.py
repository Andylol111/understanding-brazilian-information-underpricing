import numpy as np
import pandas as pd

from brunder import night


def test_edges_are_expected_profit_per_share_of_each_side():
    e = night.edges(0.80, yes_cost=0.70, no_cost=0.32)
    assert np.isclose(e["edge_yes"], 0.10) and np.isclose(e["edge_no"], 0.20 - 0.32)


def test_evaluate_measures_lead_and_settles_the_first_signal():
    t = pd.date_range("2026-10-04 20:00", periods=6, freq="10min", tz="UTC")
    ticks = pd.DataFrame({
        "t": t, "scope": "br",
        "P": [0.5, 0.95, 0.97, 0.99, 0.99, 0.99],        # model settles above 0.9 at t[1]
        "price": [0.5, 0.5, 0.6, 0.92, 0.95, 0.99],      # market settles at t[3]
    })
    ticks["yes_cost"] = ticks["price"] + 0.01
    ticks["no_cost"] = 1 - ticks["price"] + 0.01
    ticks["edge_yes"] = ticks["P"] - ticks["yes_cost"]
    ticks["edge_no"] = 1 - ticks["P"] - ticks["no_cost"]
    r = night.evaluate(ticks, {"br": 1}, min_edge=0.05).iloc[0]
    assert r["lead_min"] == 20
    assert r["side"] == "YES" and r["entry"] == t[1]
    assert np.isclose(r["return_per_$"], 1 / 0.51 - 1)


def test_margin_partials_subtract_the_opponent():
    cfg = {"election": {"race": "margin", "cand_a": "13", "cand_b": "22"}}
    by = pd.Series({"13": 450.0, "22": 470.0, "70": 80.0})
    assert night._numerator(by, "13", "22", "margin") == -20.0
    assert night._numerator(by, "13", "22", "share") == 450.0
    assert cfg["election"]["race"] == "margin"
