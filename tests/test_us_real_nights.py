"""analysis/us_real_nights.py: parsers of the archived NYT/Edison county returns, the feed-row bridge to brunder.us and the night
summary (synthetic data; no archive access, no SSD panels)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
us_real_nights = pytest.importorskip("us_real_nights")
rn = us_real_nights


def _race22():
    def unit(fips, d, r, rep=1, units=2):
        return {"level": "county", "fips_state": "13", "fips_county": fips, "total_votes": d + r + 5, "total_expected_vote": 1000,
                "precincts_reporting": rep, "precincts_total": units,
                "candidates": [{"nyt_id": "warnock-r", "votes": {"total": d}}, {"nyt_id": "walker-h", "votes": {"total": r}},
                               {"nyt_id": "oliver-c", "votes": {"total": 5}}]}
    return {"updated_at": "2022-11-09T03:00:00.000Z",
            "candidate_metadata": {"warnock-r": {"party": {"nyt_id": "DEM"}}, "walker-h": {"party": {"nyt_id": "GOP"}},
                                   "oliver-c": {"party": {"nyt_id": "LIB"}}},
            "reporting_units": [{"level": "state", "nyt_id": "GA-state"}, unit("001", 300, 700), unit("003", 600, 400, rep=2)]}


def test_parse_nyt_race_takes_two_party_votes_by_party_and_skips_state_row():
    ts, f = rn.parse_nyt_race(_race22())
    assert ts == pd.Timestamp("2022-11-09T03:00:00Z")
    assert list(f["fips"]) == ["13001", "13003"] and list(f["d"]) == [300, 600] and list(f["r"]) == [700, 400]
    assert list(f["units"]) == [2, 2] and list(f["rep"]) == [1, 2]


def test_parse_nyt_race_without_counties_is_none():
    assert rn.parse_nyt_race({"updated_at": "2022-11-09T03:00:00Z", "reporting_units": [{"level": "state"}]}) is None


def test_parse_nyt2020_race_maps_candidate_keys_to_parties():
    page = {"data": {"races": [{"race_id": "GA-G-S-2020-11-03", "last_updated": "2020-11-04T05:00:00Z",
                                "candidates": [{"party_id": "republican", "candidate_key": "perdued"}, {"party_id": "democrat", "candidate_key": "ossoffj"},
                                               {"party_id": "libertarian", "candidate_key": "hazels"}],
                                "counties": [{"fips": "13001", "votes": 1000, "results": {"perdued": 600, "ossoffj": 350, "hazels": 50},
                                              "results_absentee": {"perdued": 300, "ossoffj": 200, "hazels": 10}, "reporting": 4, "precincts": 9,
                                              "tot_exp_vote": 1200}]}]}}
    ts, f = rn.parse_nyt2020_race(page, "GA-G-S-2020-11-03")
    assert ts == pd.Timestamp("2020-11-04T05:00:00Z")
    row = f.iloc[0]
    assert (row["d"], row["r"], row["ed"], row["er"], row["units"]) == (350, 600, 200, 300, 9)
    assert rn.parse_nyt2020_race(page, "GA-G-P-2020-11-03") is None


def test_finish_drops_unknown_counties_and_duplicate_captures():
    f = pd.DataFrame({"fips": ["13001", "99999"], "d": [1, 2], "r": [3, 4], "tot": [4, 6]})
    ts = pd.Timestamp("2020-11-04T05:00:00Z")
    d = rn._finish([(ts, f), (ts, f)], {"13001"})
    assert len(d) == 1 and d["ts"].dt.tz is not None and list(d.columns) == rn.SNAP_COLS


def test_feed_rows_are_two_party_feed_rows_with_optional_early_split():
    fr = pd.DataFrame({"fips": ["13001", "13003"], "d": [10.0, 20.0], "r": [30.0, 5.0], "ed": [4.0, 8.0], "er": [12.0, 2.0],
                       "rep": [1.0, 2.0], "units": [2.0, 2.0]})
    rows = rn.feed_rows(fr, "ga")
    assert list(rows.columns) == rn.us.COLS
    assert set(rows["party"]) == {"DEM", "REP"} and rows.loc[rows["party"] == "DEM", "votes"].sum() == 30
    assert rows.loc[rows["party"] == "REP", "early"].sum() == 14
    no_split = rn.feed_rows(fr.assign(ed=np.nan, er=np.nan), "ga")
    assert no_split["early"].isna().all()


def test_night_summary_minutes_to_90_needs_persistence_and_checkpoints_use_nearest_frame():
    minute = np.array([-1, 0, 30, 60, 90, 120, 150], float)
    counted = np.array([0, .05, .25, .5, .75, .9, .99])
    pcor = np.array([.5, .6, .95, .8, .95, .97, .99])          # dips at 60: decided from minute 90, not 30
    d = pd.DataFrame({"night": "n", "variant": "v1", "minute": minute, "counted": counted, "p_correct": pcor,
                      "err": [.03, .02, .01, -.01, .005, .002, .001], "sd": .02, "final": .5, "win": True})
    d["cover90"] = d["err"].abs() / d["sd"] < 1.645
    ns = rn.night_summary(d).iloc[0]
    assert ns["minutes_to_90"] == 90 and ns["counted_at_90"] == 0.75 and ns["decided"]
    assert ns["abs_err_25_pts"] == pytest.approx(1.0) and ns["abs_err_50_pts"] == pytest.approx(1.0)
    assert ns["pre_err_pts"] == pytest.approx(3.0)


def test_night_summary_never_decided():
    d = pd.DataFrame({"night": "n", "variant": "v1", "minute": [-1, 0, 10], "counted": [0, .1, .5], "p_correct": [.5, .5, .6],
                      "err": [0.0, 0.0, 0.0], "sd": .02, "final": .5, "win": True, "cover90": True})
    ns = rn.night_summary(d).iloc[0]
    assert not ns["decided"] and np.isnan(ns["minutes_to_90"])


def test_wayback_client_refuses_live_hosts():
    wb = rn.Wayback(gap=0)
    with pytest.raises(AssertionError):
        wb.get("https://er.ncsbe.gov/results")
