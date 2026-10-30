from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_national_data as nd  # noqa: E402


def test_poll_average_eve_window_and_two_party_share():
    rows = pd.DataFrame([
        {"pollster": "A", "end": "2024-11-04", "n": 400, "s": .52},
        {"pollster": "B", "end": "2024-10-21", "n": 400, "s": .48},
        {"pollster": "old", "end": "2024-09-01", "n": 10000, "s": .99},
        {"pollster": "day-of", "end": "2024-11-05", "n": 10000, "s": .01},
    ])
    got = nd.poll_average(rows, "2024-11-05")
    # With only two pollsters the 25% cap becomes 1/k=50%, so both receive equal weight.
    assert got["n_polls"] == 2
    assert np.isclose(got["avg"], .50)


def test_generic_average_deduplicates_pollster_day_and_caps():
    p = pd.DataFrame([
        {"pollster": "A", "date": pd.Timestamp("2022-11-07"), "n": 100, "s": .40},
        {"pollster": "A", "date": pd.Timestamp("2022-11-07"), "n": 400, "s": .60},
        {"pollster": "B", "date": pd.Timestamp("2022-11-07"), "n": 400, "s": .50},
        {"pollster": "C", "date": pd.Timestamp("2022-01-01"), "n": 9999, "s": .99},
    ])
    got = nd.generic_average(2022, p)
    assert got["n_polls"] == 2
    assert np.isclose(got["avg"], .55)


def test_poll_close_uses_cycle_date_and_split_state_last_close():
    x = nd.poll_close(2020).set_index("state")
    assert x.loc["ga", "close_utc"] == "2020-11-04T00:00:00Z"
    assert x.loc["tx", "close_utc"] == "2020-11-04T01:00:00Z"
    assert x.loc["tx", "last_close_utc"] == "2020-11-04T02:00:00Z"
    assert x.loc["ak", "last_close_utc"] == "2020-11-04T06:00:00Z"


def test_race_list_has_distinct_regular_and_special_keys():
    races = nd.race_list(2020)
    assert ("ga", "sen", False) in races
    assert ("ga", "sen", True) in races
    assert len(races) == len({nd.race_key(*x) for x in races})


def test_load_cycle_reads_complete_cache_without_build(tmp_path, monkeypatch):
    monkeypatch.setattr(nd, "OUT", tmp_path)
    dest = tmp_path / "2018"
    dest.mkdir()
    for name in nd.TABLES:
        pd.DataFrame({"value": [name]}).to_csv(dest / f"{name}.csv", index=False)
    got = nd.load_cycle(2018)
    assert set(got) == set(nd.TABLES)
    assert got["races"].iloc[0, 0] == "races"
