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
    target = tw.poll_target(cfg)
    actual = (units[candidates].to_numpy() * units["V0"].to_numpy()[:, None]).sum(axis=0) / units["V0"].sum()
    assert len(units) == 456 and units["region"].nunique() == 12
    assert np.allclose(actual, target, atol=1e-8)


def test_poll_prior_corrects_past_taipei_kmt_understatement():
    cfg = tw.load_config(ROOT / "configs" / "tw2026_taipei_v1.toml")
    bias = tw.kmt_two_way_poll_bias()
    target = dict(zip([c["id"] for c in cfg["candidates"]], tw.poll_target(cfg)))
    assert round(bias, 4) == .0523
    assert round(target["chiang"], 4) == .4801 and round(target["shen"], 4) == .4999
    assert abs(sum(target.values()) - 1) < 1e-12
    _, _, probs = tw._initial(cfg)
    assert .35 < probs["chiang"] < .5


def test_taipei_li_map_matches_model_units():
    cfg = tw.load_config(ROOT / "configs" / "tw2026_taipei_v1.toml")
    meta = tw.meta(cfg, "pre-count", {c["id"]: 0 for c in cfg["candidates"]})
    path = ROOT / "site" / "data" / meta["geometry"]
    geo = json.loads(path.read_text())
    ids = [g["properties"]["unit_id"] for g in geo["objects"][meta["geometry_object"]]["geometries"]]
    assert len(ids) == len(set(ids)) == 456
    assert set(ids) == set(tw.prior_units(cfg).index)
    assert path.stat().st_size <= 1_000_000


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


def test_tau_mixture_components_and_config():
    cfg = tw.load_config(ROOT / "configs" / "tw2026_taipei_v1.toml")
    st = tw.settings(cfg)
    assert st.tau_mixture == (0.0075, 0.02, 0.005, 0.005) and st.tau == 0.02
    assert tw.component_sds(st, .5, True) == (.00375, .01, .0025, .0025)
    assert tw.component_sds(st, 0, False) == (.06,)
    assert tw.component_sds(tw.Settings(), .5, True) == (.01,)  # empty mixture = point tau


def test_tau_mixture_probabilities_between_components_and_point_unchanged():
    units = pd.DataFrame({"V0": [100, 100], "stations": [1, 1], "a": [.5, .5], "b": [.5, .5]}, index=["u1", "u2"])
    count = pd.DataFrame({"a": [52, 0], "b": [48, 0], "places": [1, 0]}, index=units.index)
    cand = ["a", "b"]
    P = {}
    for name, st in {"lo": tw.Settings(tau=.005), "hi": tw.Settings(tau=.05),
                     "mix": tw.Settings(tau_mixture=(.005, .05)), "pt": tw.Settings(tau_mixture=(.05,))}.items():
        P[name] = tw.win_probabilities(tw.project(units, count, st, cand), st, 1)["a"]
    assert P["lo"] > P["mix"] > P["hi"]
    assert P["pt"] == P["hi"]
