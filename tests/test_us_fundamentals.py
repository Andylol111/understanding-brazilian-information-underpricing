"""Behavioural tests for analysis/us_fundamentals.py."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("us_fundamentals", ROOT / "analysis" / "us_fundamentals.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fips_normalises_historical_county_changes():
    f = _mod()._fips(pd.Series([1001, "46113", "51515.0", "02270"]))
    assert f.tolist() == ["01001", "46102", "51019", "02158"]


def test_irs_pairs_respect_publication_lag():
    m = _mod()
    assert m.irs_pairs(2016) == [11, 12]       # 2011-12 / 2012-13, not election-year flows
    assert m.irs_pairs(2026) == [21, 22]       # latest is 2022-23, published in 2026
    assert m.irs_pairs(2014) == []              # no two usable pairs in the CSV series


def test_total_mode_uses_total_else_sums_modes():
    m = _mod()
    d = pd.DataFrame({"pk": ["a", "a", "a", "b", "b"], "mode": ["total", "mail", "day", "mail", "day"],
                      "votes": [10, 6, 4, 3, 5]})
    got = m._total_mode(d, ["pk"])
    assert got.groupby("pk")["votes"].sum().to_dict() == {"a": 10, "b": 8}


def test_recentre_matches_each_state_result():
    m = _mod()
    d = pd.DataFrame({"st": ["01", "01", "02", "02"], "s": [.4, .6, .2, .8], "votes": [1, 3, 2, 2]})
    p = m._recentre(pd.Series([.3, .3, .6, .6]), d, "st")
    for st, g in d.groupby("st"):
        assert np.average(p[g.index], weights=g["votes"]) == pytest.approx(np.average(g["s"], weights=g["votes"]))


def test_prediction_frame_does_not_require_target_result():
    m = _mod()
    rows = []
    for y, s, v in [(2020, .4, 100), (2022, np.nan, np.nan)]:
        rows.append({"fips": "01001", "st": "01", "year": y, "s": s, "votes": v, "inc": 1,
                     "dur": 2, "ginc": 3, "pop_g": 1, "ndm": .5})
    d = m.prediction_frame(pd.DataFrame(rows), 2022, 2020)
    assert list(d.index) == ["01001"] and d.loc["01001", "s_p"] == .4 and d.loc["01001", "x_dur"] == 2


def test_include_rule_requires_every_cycle_and_interval_below_zero(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "_boot_delta", lambda _: (np.array([[-2., -1.], [-1., -.5]]), np.array([-1., -.5])))
    rows, boots = [{"test": 2018}, {"test": 2022}], {}
    cyc = [(np.array([1]), np.array([0]), np.array([1]), np.array(["A"]))] * 2
    m._attach_boot(rows, cyc, boots, ("x", "state"))
    assert bool(m._pooled(boots).iloc[0]["include"])
    boots[("x", "state")] = (np.array([[-2., 1.], [-1., 2.]]), np.array([-1., 1.]), [2018, 2022])
    assert not bool(m._pooled(boots).iloc[0]["include"])


def test_public_panel_is_compact_long_panel_without_future_results():
    p = _mod().load_panel()
    assert set(range(2008, 2028, 2)) == set(p["year"])
    assert p.duplicated(["fips", "year"]).sum() == 0
    assert {"pop_g", "ndm", "imig", "ur", "dur", "ginc", "irs_net", "inflow_d", "mig_comp"} <= set(p)
    assert p.loc[p["year"] == 2026, ["s", "votes"]].isna().all().all()
    assert (ROOT / "data_public" / "us_county_panel.csv").stat().st_size < 5_000_000
