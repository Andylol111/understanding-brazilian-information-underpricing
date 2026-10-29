"""US House 2026 district data (site/data/us2026/house, analysis/us_house.py): maps, past results, crosswalk,
flows, swing, early vote, markets and the model-only baseline."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
from scipy.stats import norm

from brunder import us

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "site" / "data" / "us2026" / "house"
REDRAWN = {"AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT"}


def _j(name):
    return json.loads((H / name).read_text())


def _mod():
    spec = importlib.util.spec_from_file_location("us_house", ROOT / "analysis" / "us_house.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("name", ["districts.json", "districts_2024.json", "flows.json", "swing.json",
                                  "earlyvote.json", "markets.json", "districts_2024.topo.json",
                                  "districts_2026.topo.json"])
def test_house_files_small_and_sourced(name):
    f = H / name
    assert f.exists() and f.stat().st_size < 2e6
    if not name.endswith(".topo.json"):
        assert _j(name).get("sources")


def test_maps_have_the_435_seats_of_the_2020_apportionment():
    d26, d24 = _j("districts.json")["districts"], _j("districts_2024.json")["districts"]
    for topo, data in (("districts_2026.topo.json", d26), ("districts_2024.topo.json", d24)):
        g = _j(topo)["objects"]["districts"]["geometries"]
        ids = [x["properties"]["id"] for x in g]
        assert len(ids) == len(set(ids)) == 435 and set(ids) == set(data)
        assert {x["properties"]["st"] for x in g if x["properties"]["redrawn"]} == REDRAWN
    seats = Counter(k[:2] for k in d26)
    assert seats == Counter(k[:2] for k in d24)              # no reapportionment between the maps
    assert (seats["CA"], seats["TX"], seats["FL"], seats["NY"], seats["MO"]) == (52, 38, 28, 26, 8)
    assert {k for k, n in seats.items() if n == 1} == {"AK", "DE", "ND", "SD", "VT", "WY"}
    assert all(k.endswith("-AL") for k in d26 if seats[k[:2]] == 1)


def test_2024_president_by_district_matches_known_totals():
    d = _j("districts_2024.json")["districts"]
    # Trump carried 230 of the 435 119th-Congress districts, Harris 205; Harris won NE-02, Trump ME-02 and AK
    assert sum(v["pres24"]["d2"] < 0.5 for v in d.values()) == 230
    assert d["NE-02"]["pres24"]["margin"] > 0 > d["ME-02"]["pres24"]["margin"]
    assert d["AK-AL"]["pres24"]["margin"] == pytest.approx(-13.1, abs=0.3)
    for v in d.values():
        p = v["pres24"]
        assert 0 < p["d2"] < 1 and (p["margin"] > 0) == (p["d2"] > 0.5)
        assert p["d2"] == pytest.approx(p["dv"] / (p["dv"] + p["rv"]), abs=1e-3)


def test_unchanged_states_keep_their_2024_numbers_and_the_crosswalk_conserves_votes():
    d26, d24 = _j("districts.json"), _j("districts_2024.json")["districts"]
    assert set(d26["redrawn"]) == REDRAWN
    for k, v in d26["districts"].items():
        assert v["redrawn"] == (v["st"] in REDRAWN)
        if not v["redrawn"]:
            assert v["pres24"] == d24[k]["pres24"] and v["house24"] == d24[k]["house24"]
    for st in REDRAWN:
        for key in ("dv", "rv", "tv"):
            new = sum(v["pres24"][key] for v in d26["districts"].values() if v["st"] == st)
            old = sum(v["pres24"][key] for v in d24.values() if v["st"] == st)
            assert new == pytest.approx(old, rel=1e-4), (st, key)
    for st, c in d26["state_check"].items():
        if c["county_file_2024"] and st != "NY":            # NY: statewide-only ballots carry no county
            assert c["d24_alloc"] == pytest.approx(c["d24_county_file"], rel=5e-3), st


def test_flows_are_population_shares_and_seat_counts_agree():
    f, d26 = _j("flows.json")["states"], _j("districts.json")["districts"]
    d24 = _j("districts_2024.json")["districts"]
    assert set(f) == REDRAWN
    for st, s in f.items():
        fl = pd.DataFrame(s["flows"], columns=["old", "new", "pop", "s_old", "s_new", "d", "r"])
        assert fl.groupby("old")["s_old"].sum().round(3).eq(1).all()
        assert fl.groupby("new")["s_new"].sum().round(3).eq(1).all()
        assert set(fl["new"]) == {k for k in d26 if k[:2] == st} and set(fl["old"]) == {k for k in d24 if k[:2] == st}
        assert s["new_seats"]["harris_won"] == sum(v["pres24"]["d2"] > 0.5 for k, v in d26.items() if k[:2] == st)
        assert s["old_seats"]["harris_won"] == sum(v["pres24"]["d2"] > 0.5 for k, v in d24.items() if k[:2] == st)
        for x in s["districts"].values():
            assert x["lean_change"] == pytest.approx(x["pres24_margin"] - x["core_margin"], abs=0.02)
    # the Texas 2025 map: Trump won 30 of the 38 districts at the 2024 vote; Utah's court map has one Harris seat
    assert f["TX"]["new_seats"]["harris_won"] == 8 and f["UT"]["new_seats"]["harris_won"] == 1


def test_swing_counties_join_the_county_map_and_house_swing_only_where_comparable():
    s = _j("swing.json")["counties"]
    geo = {uid for uid, _ in us._geo()}
    assert len(s) > 3000 and len(set(s) & geo) > 0.97 * len(s)
    d24 = _j("districts_2024.json")["districts"]
    for k, v in d24.items():
        if v["swing_house"] is not None:
            assert k[:2] not in {"AL", "GA", "LA", "NC", "NY"} and v["house24"]["d_vs_r"] and v["house22"]["d_vs_r"]
            assert v["swing_house"] == pytest.approx(v["house24"]["margin"] - v["house22"]["margin"], abs=0.02)


def test_early_vote_districts_add_up_to_the_county_file():
    ev = _j("earlyvote.json")["districts"]
    src = json.loads((ROOT / "site" / "data" / "us2026" / "earlyvote.json").read_text())
    d26 = _j("districts.json")["districts"]
    assert {k[:2] for k in ev} <= {"TX", "FL", "NC"} and set(ev) <= set(d26)
    for st in {k[:2] for k in ev}:
        counties = sum(c["ev_total"] or 0 for c in src["counties"].values() if c["state"] == st.lower())
        assert sum(v["ev_total"] for k, v in ev.items() if k[:2] == st) == pytest.approx(counties, rel=2e-3)


def test_markets_polls_and_model_only_baseline():
    m, d = _j("markets.json"), _j("districts.json")
    assert m["counts"]["pm"] > 300 and m["counts"]["kalshi"] > 300
    assert "MODEL-ONLY" in d["notes"]["baseline"]
    for k, v in m["districts"].items():
        assert k in d["districts"]
        for src in ("pm", "kalshi"):
            if v[src]:
                assert 0 <= v[src]["p"] <= 1
        b = v["baseline"]
        assert b["p_d"] == pytest.approx(norm.cdf((b["share"] - 0.5) / b["sd"]), abs=2e-3)
        if v["polls"] and v["polls"]["average"]:
            assert 0 < v["polls"]["average"]["avg"] < 1 and v["polls"]["polls"]
    g = d["national"]["generic_ballot"]
    assert g["sd_bin"] == us.poll_sd_by_n(g["n_polls"])


def test_alloc_calibrates_to_county_totals_and_fills_districts_missing_from_precincts():
    m = _mod()
    pc = {"01001": [1], "01003": [1, 2, 3]}
    shares = pd.DataFrame({"fips": ["01001", "01003", "01003", "01003"], "cd": [1, 1, 2, -1],
                           "d": [5.0, 30.0, 10.0, 6.0], "r": [5.0, 10.0, 30.0, 2.0], "t": [10.0, 40.0, 40.0, 8.0]})
    official = pd.DataFrame({"d": [100.0, 92.0], "r": [100.0, 84.0], "t": [200.0, 176.0]}, index=["01001", "01003"])
    w = pd.DataFrame({"county": ["01001", "01003", "01003", "01003"], "cd": [1, 1, 2, 3], "w": [1, .45, .45, .1]})
    a = m._alloc(pc, shares, official, w).set_index(["county", "cd"])
    assert a.loc[("01001", 1), "how"] == "whole" and a.loc[("01001", 1), "d"] == 100
    c = a.loc["01003"]
    assert c["d"].sum() == pytest.approx(92) and c["t"].sum() == pytest.approx(176)
    # district 3 has no House contest in the precinct file: it gets the county's unassigned precincts
    assert c.loc[3, "d"] / c["d"].sum() == pytest.approx(6 / 46) and c.loc[1, "d"] / c.loc[2, "d"] == 3
