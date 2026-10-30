import json
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import taiwan as tw

F = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[1]


def test_real_cec_station_json_parser():
    data = (F / "tw2022_taipei_stations.json").read_bytes()
    got = tw.parse_static_results(data, {"蔣萬安": "KMT", "陳時中": "DPP", "黃珊珊": "TPP"})
    assert got.index.names == ["unit_id", "station_id"]
    assert got.loc[("tw-010-0002", "0596"), ["KMT", "DPP", "TPP"]].tolist() == [335, 213, 147]
    assert got.loc[("tw-010-0002", "0597")].sum() == 709


def test_real_cec_live_html_parser():
    got = tw.parse_result_html((F / "tw2024_cec_station.html").read_bytes(),
                               {"kmt": "中國國民黨", "dpp": "民主進步黨", "tpp": "台灣民眾黨"})
    assert got == {"places": 1, "places_total": 1,
                   "votes": {"kmt": 334, "dpp": 2, "tpp": 286}, "stamp": "01/13 22:21"}


def test_real_2026_cec_taipei_topology_parser():
    got = tw.parse_2026_districts((F / "tw2026_cec_taipei_districts.json").read_bytes())
    assert got.loc["tw-010-0002"].to_dict() == {"district_name": "松山區", "li_name": "莊敬里"}
    assert got.index.tolist() == ["tw-010-0002", "tw-010-0003", "tw-010-0004"]


def test_cec_li_aggregate_serial_preserves_field_widths():
    assert tw._cec_unit_serial("tw-010-0002") == "63000001000020000"
    assert tw._cec_result_url("https://info.cec.gov.tw/vote2026", "M1", "tw-010-0002") == (
        "https://info.cec.gov.tw/vote2026/zh-TW/M1/63000001000020000.html")


def test_public_labels_name_taiwan_explicitly():
    entry = tw.entry()
    assert entry["name"] == "Taiwan: Taipei mayoral election 2026"
    assert entry["country"] == "TW"
    assert entry["country_name"] == "Taiwan"




def test_public_history_reproduces_official_totals():
    d = tw.load_history()
    totals = d.groupby("year")[[*tw.PARTIES, "valid"]].sum()
    assert totals.loc[2014, "valid"] == 1_494_046
    assert totals.loc[2018, ["KMT", "DPP", "TPP"]].tolist() == [577_096, 244_342, 580_663]
    assert totals.loc[2022, ["KMT", "DPP", "TPP"]].tolist() == [575_590, 434_558, 342_141]
    assert (totals[[*tw.PARTIES]].sum(axis=1) == totals["valid"]).all()


def test_frozen_prior_has_456_li_and_exact_poll_target():
    cfg = tw.load_config(ROOT / "configs" / "tw2026_taipei_v1.toml")
    units = tw.prior_units(cfg)
    candidates = [c["id"] for c in cfg["candidates"]]
    target = np.array([c["prior_share"] for c in cfg["candidates"]])
    actual = (units[candidates].to_numpy() * units["V0"].to_numpy()[:, None]).sum(axis=0) / units["V0"].sum()
    assert len(units) == 456 and units["region"].nunique() == 12
    assert np.allclose(actual, target, atol=1e-8)


def test_nowcast_equals_complete_count():
    units = pd.DataFrame({"V0": [100, 100], "stations": [1, 1],
                          "a": [.6, .4], "b": [.4, .6]}, index=["u1", "u2"])
    count = pd.DataFrame({"a": [55, 35], "b": [45, 65], "places": [1, 1]}, index=units.index)
    p = tw.project(units, count, tw.Settings(floor_sd=0), ["a", "b"])
    assert p.counted_frac == 1
    assert np.allclose(p.shares, [.45, .55])
    assert np.allclose(p.raw_shares, [.45, .55])
    assert p.votes.sum() == 200


def test_precount_candidate_probabilities_are_complete():
    cfg = tw.load_config(ROOT / "configs" / "tw2026_taipei_v1.toml")
    units, p, probs = tw._initial(cfg)
    assert len(units) == 456
    assert set(probs) == {c["id"] for c in cfg["candidates"]}
    assert abs(sum(probs.values()) - 1) < 1e-12
    assert p.counted_frac == 0 and p.sd == .06
