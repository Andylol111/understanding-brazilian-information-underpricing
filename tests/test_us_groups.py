"""analysis/us_groups.py (county classes, key regions, county table) and site/groups-agg.js (live group figures), on
synthetic counties and units files. The JS module runs in node."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import us_groups as ug  # noqa: E402


def demo_rows():
    # fips, st, pop, density, ba, age65, medinc, white_nh, black, hisp
    rows = [
        ("13121", "ga", 1_080_000, 2000.0, 0.55, 0.12, 85000, 0.38, 0.43, 0.08),   # big city, no majority group
        ("13135", "ga", 960_000, 2200.0, 0.40, 0.11, 80000, 0.33, 0.30, 0.22),    # big city
        ("13059", "ga", 130_000, 1060.0, 0.45, 0.10, 45000, 0.56, 0.26, 0.10),    # suburb, majority white
        ("13045", "ga", 120_000, 240.0, 0.20, 0.15, 60000, 0.72, 0.18, 0.06),     # town (density under the suburb bar)
        ("13001", "ga", 18_000, 35.0, 0.12, 0.20, 42000, 0.72, 0.17, 0.10),       # rural
        ("13043", "ga", 11_000, 40.0, 0.10, 0.19, 38000, 0.36, 0.60, 0.03),       # rural, majority Black
        ("48061", "tx", 420_000, 470.0, 0.19, 0.15, 47000, 0.07, 0.01, 0.90),     # suburb by size/density, majority Hispanic
        ("48301", "tx", 60, 0.1, 0.05, 0.30, None, 0.80, 0.0, 0.15),              # rural, income missing
    ]
    return pd.DataFrame(rows, columns=["fips", "st", "pop_total", "density", "ba", "age65", "medinc", "white_nh", "black", "hisp"])


def test_place_and_race_classes():
    assert ug.place_class(600_000, 1500) == "big_city"
    assert ug.place_class(600_000, 900) == "suburb"          # big but not dense enough for a big city
    assert ug.place_class(150_000, 300) == "suburb"
    assert ug.place_class(150_000, 200) == "town"
    assert ug.place_class(40_000, 99) == "rural"
    assert ug.place_class(40_000, 150) == "town"
    assert ug.race_class(0.38, 0.43, 0.08) == "none"
    assert ug.race_class(0.36, 0.60, 0.03) == "black"
    assert ug.race_class(0.07, 0.01, 0.90, "Mexican", 0.93) == "hisp_mex"
    assert ug.race_class(0.15, 0.15, 0.69, "Cuban", 0.55) == "hisp_cub"
    assert ug.race_class(0.30, 0.10, 0.55, "Other Hispanic", 0.70) == "hisp_oth"
    assert ug.race_class(0.10, 0.30, 0.55, "Dominican", 0.40) == "hisp_mix"     # largest origin under half: mixed
    assert ug.race_class(0.30, 0.10, 0.55, "Mexican", 0.44) == "hisp_mix"
    assert ug.race_class(0.30, 0.10, 0.55) == "hisp_mix"                        # no origin estimate
    assert ug.race_class(0.40, 0.10, 0.45, "Mexican", 0.9) == "none"            # Hispanic plurality, not a majority
    assert ug.race_class(0.72, 0.18, 0.06) == "white"


def test_classify_terciles_and_missing():
    d, cuts = ug.classify(demo_rows())
    by = d.set_index("fips")
    lo, hi = cuts["edu"]
    assert lo < hi
    assert by.loc["13121", "edu"] == "high" and by.loc["48301", "edu"] == "low"
    assert pd.isna(by.loc["48301", "inc"])                   # missing income: no income group
    assert by.loc["13001", "place"] == "rural" and by.loc["13045", "place"] == "town"
    # terciles split the counties into three groups of near-equal size
    counts = d["age"].value_counts()
    assert set(counts.index) == {"old", "mid", "young"} and counts.max() - counts.min() <= 1


def test_regions_resolve_rural_and_drop_absent():
    d, _ = ug.classify(demo_rows())
    regions = {"ga": [("atl", "Metro Atlanta", ["13121", "13135", "99999"]), ("rural", "Rural Georgia", "rural")],
               "nv": [("clark", "Las Vegas", ["32003"])]}
    out = ug.resolve_regions(d, regions)
    assert out["ga"][0] == {"id": "atl", "label": "Metro Atlanta", "fips": ["13121", "13135"]}
    assert out["ga"][1]["fips"] == ["13001", "13043"]
    assert "nv" not in out                                   # no county of the region in the table


def test_county_table_margins_and_classes():
    res = pd.DataFrame([("13121", "ga", 2024, "pres", 0.75), ("13121", "ga", 2022, "sen", 0.80), ("13001", "ga", 2024, "pres", 0.25)],
                       columns=["fips", "st", "year", "office", "s2"])
    g, c = ug.build(demo_rows(), res)
    i = c["fips"].index("13121")
    assert c["p24"][i] == 0.5 and c["s22"][i] == 0.6 and c["g22"][i] is None and c["p20"][i] is None
    assert c["p24"][c["fips"].index("13001")] == -0.5
    dims = [d["id"] for d in g["dimensions"]]
    assert dims == ug.DIM_IDS
    # class indices point into each dimension's groups
    assert g["dimensions"][0]["groups"][c["cls"][i][0]]["id"] == "big_city"
    assert g["dimensions"][4]["groups"][c["cls"][i][4]]["id"] == "none"
    assert c["cls"][c["fips"].index("48301")][3] is None
    json.dumps(g, allow_nan=False), json.dumps(c, allow_nan=False)


# ------------------------------------------------------------------------------------------------ groups-agg.js (node)
NODE = shutil.which("node")


def run_js(script: str):
    if not NODE:
        pytest.skip("node not installed")
    agg = (ROOT / "site" / "groups-agg.js").as_uri()
    src = f"import * as G from {json.dumps(agg)};\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", src], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


COUNTIES = {"fips": ["01001", "01003", "01005", "01007"], "pop": [100, 300, 100, 100],
            "ba": [0.2, 0.4, 0.1, 0.3], "age65": [0.2, 0.1, 0.3, 0.2], "medinc": [50000, 70000, 40000, None],
            "white": [0.6, 0.7, 0.4, 0.5], "black": [0.3, 0.2, 0.55, 0.4], "hisp": [0.05, 0.05, 0.03, 0.05],
            "cls": [[0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [3, 2, 0, 2, 1], [0, 0, 0, None, 0]],
            "p20": [0.1, 0.2, -0.3, 0.0], "p24": [0.0, 0.1, -0.4, -0.1], "g22": [0.05, 0.15, -0.35, None], "s22": [0.0, 0.0, 0.0, 0.0]}
RACE = {"race_id": "al-gov", "a": {"id": "dem"}, "b": {"id": "rep"}}


def units(frames):
    return {"race_id": "al-gov", "unit_ids": ["us-al-01001", "us-al-01003", "us-al-01005", "us-al-01007"],
            "V0": [1000, 3000, 1000, 1000], "turnout_exp": [1000, 3000, 1000, 1000], "frames": frames}


FRAMES = [
    {"t": "2026-11-04T00:10:00Z", "f": [0, 0, 0, 0], "a": [-1, -1, -1, -1], "proj": [500, 550, 300, 450]},
    # 01001 complete (96%), 01003 part-counted early ballots (60%, very Democratic), 01005 complete, 01007 nothing
    {"t": "2026-11-04T01:00:00Z", "f": [960, 600, 1000, 0], "a": [520, 700, 290, -1], "proj": [515, 560, 290, 440],
     "proj_poll": [516, 562, 291, 441], "turnout_so_far": [1010, 1900, 1020, 0], "turnout_ratio_live": [1050, 1060, 1020, 1000]},
]


def test_agg_complete_only_headline():
    r = run_js(f"""
const C = {json.dumps(COUNTIES)}, U = {json.dumps(units(FRAMES))};
const P = G.prepRace(U, {json.dumps(RACE)}, C, G.countyIndex(C));
const all = P.fips.map((_, j) => j);
const g0 = G.membersOf(P, C, {{ dim: 0, group: 0 }});
console.log(JSON.stringify({{
  k0: G.frameAt(P, Date.parse("2026-11-04T00:30:00Z")), kEnd: G.frameAt(P), kBefore: G.frameAt(P, 0),
  g0, reg: G.membersOf(P, C, {{ fips: ["01005", "09999"] }}),
  early: G.aggregate([{{ prep: P, k: 0, members: all }}], "p24"),
  now: G.aggregate([{{ prep: P, k: 1, members: all }}], "p24"),
  now22: G.aggregate([{{ prep: P, k: 1, members: all }}], "o22"),
  g0now: G.aggregate([{{ prep: P, k: 1, members: g0 }}], "p24"),
}}));""")
    assert (r["k0"], r["kEnd"], r["kBefore"]) == (0, 1, -1)
    assert r["g0"] == [0, 1, 3] and r["reg"] == [2]
    e = r["early"]
    assert e["counted"] == 0 and e["complete"]["n"] == 0 and e["complete"]["swing"] is None and e["partial"] is None
    n = r["now"]
    # counted share weighted by expected votes: (960 + 3*600 + 1000) / 6000 / 1000
    assert n["counted"] == pytest.approx((0.96 * 1000 + 0.6 * 3000 + 1.0 * 1000) / 6000)
    # headline: only 01001 (+0.04 vs 0.0) and 01005 (-0.42 vs -0.4) — never the part-counted 01003; weighted by votes counted
    c = n["complete"]
    assert c["n"] == 2 and sorted(b["fips"] for b in c["basis"]) == ["01001", "01005"]
    assert all(b["f"] >= 950 for b in c["basis"])
    assert c["swingAny"] == pytest.approx((0.04 * 1010 + (-0.42 + 0.4) * 1020) / (1010 + 1020))
    assert c["evShare"] == pytest.approx(2000 / 6000)
    assert c["swing"] is None and not c["enough"]          # 2 complete counties: under the 3-county floor, no headline
    # partial margin of all counted votes (incl. the early-ballot county), shown because the group is over 50 % counted
    assert n["partial"] == pytest.approx((0.04 * 1010 + 0.4 * 1900 - 0.42 * 1020) / (1010 + 1900 + 1020))
    # projection: poll model's proj where present, the count itself for a fully counted county (01005: a = 290), V0 weights
    pm = (0.032 * 1000 + 0.124 * 3000 - 0.42 * 1000 - 0.118 * 1000) / 6000
    assert n["proj"]["margin"] == pytest.approx(pm)
    assert n["proj"]["base"] == pytest.approx((0 + 0.1 * 3000 - 0.4 * 1000 - 0.1 * 1000) / 6000)
    assert n["proj"]["shift"] == pytest.approx(pm - n["proj"]["base"])
    # turnout: complete counties' counted votes / expected; projection weighted by expected votes
    assert n["turnout"]["complete"] == pytest.approx(2030 / 2000)
    assert n["turnout"]["proj"] == pytest.approx((1050 + 3 * 1060 + 1020 + 1000) / 6000)
    # vs 2022 same office (governor): 01001 0.04 - 0.05, 01005 -0.42 + 0.35
    assert r["now22"]["complete"]["swingAny"] == pytest.approx((-0.01 * 1010 - 0.07 * 1020) / 2030)
    # group 0 of dimension 0 (01001, 01003, 01007): one complete county
    assert r["g0now"]["complete"]["n"] == 1 and r["g0now"]["complete"]["swingAny"] == pytest.approx(0.04)
    assert r["g0now"]["complete"]["swing"] is None


def test_agg_headline_floor_counties_and_vote_share():
    def run(v0, f):
        u = units([{"t": "2026-11-04T02:00:00Z", "f": f, "a": [520, 600, 290, 450], "proj": [515, 560, 290, 440]}])
        u["V0"] = v0
        return run_js(f"""
const C = {json.dumps(COUNTIES)};
const P = G.prepRace({json.dumps(u)}, {json.dumps(RACE)}, C, G.countyIndex(C));
console.log(JSON.stringify(G.aggregate([{{ prep: P, k: 0, members: [0, 1, 2, 3] }}]).complete));""")
    shown = run([1000, 3000, 1000, 1000], [1000, 400, 960, 990])         # 3 complete, 50% of expected vote
    assert shown["enough"] and shown["n"] == 3 and shown["swing"] == pytest.approx(shown["swingAny"])
    thin = run([100, 10000, 100, 100], [1000, 400, 960, 990])            # 3 complete but under 15% of expected vote
    assert thin["n"] == 3 and thin["evShare"] < 0.15 and thin["swing"] is None and thin["swingAny"] is not None


def test_agg_partial_hidden_under_half_and_noncomparable_race():
    fr = [{"t": "2026-11-04T01:00:00Z", "f": [400, 300, 200, 0], "a": [600, 650, 300, -1], "proj": [500, 550, 300, 450]}]
    r = run_js(f"""
const C = {json.dumps(COUNTIES)};
const P = G.prepRace({json.dumps(units(fr))}, {json.dumps(RACE)}, C, G.countyIndex(C));
const Q = G.prepRace({json.dumps(units(fr))}, {{ race_id: "ne-sen", a: {{ id: "ind" }}, b: {{ id: "dem" }} }}, C, G.countyIndex(C));
const all = [0, 1, 2, 3];
console.log(JSON.stringify({{ a: G.aggregate([{{ prep: P, k: 0, members: all }}]), q: G.aggregate([{{ prep: Q, k: 0, members: all }}]),
  pick: G.nationalPick([{{ race_id: "ga-gov", region: "ga", a: {{ id: "dem" }}, b: {{ id: "rep" }} }}, {{ race_id: "ga-sen", region: "ga", a: {{ id: "dem" }}, b: {{ id: "rep" }} }},
    {{ race_id: "ne-sen", region: "ne", a: {{ id: "pet" }}, b: {{ id: "rep" }} }}, {{ race_id: "ne-gov", region: "ne", a: {{ id: "dem" }}, b: {{ id: "rep" }} }}]).map((r) => r.race_id),
  times: G.timesOf([P]) }}));""")
    a = r["a"]
    assert a["counted"] < 0.5 and a["partial"] is None and a["complete"]["n"] == 0 and a["complete"]["swing"] is None
    assert a["proj"]["margin"] is not None
    assert r["q"]["proj"]["base"] is None and r["q"]["complete"]["swing"] is None   # side b not Republican: no baselines
    assert r["pick"] == ["ga-sen", "ne-gov"]
    assert len(r["times"]) == 1


def test_baseline_and_makeup():
    r = run_js(f"""
const C = {json.dumps(COUNTIES)};
const P = G.prepRace({json.dumps(units(FRAMES[:1]))}, {json.dumps(RACE)}, C, G.countyIndex(C));
console.log(JSON.stringify(G.baseline([{{ prep: P, k: 0, members: [0, 1, 2, 3] }}], C)));""")
    assert r["n"] == 4 and r["ev"] == 6000
    assert r["p24"] == pytest.approx((0 + 0.3 - 0.4 - 0.1) / 6)
    assert r["o22"] == pytest.approx((0.05 + 0.45 - 0.35) / 5)                       # 01007 has no 2022 governor margin
    assert r["makeup"]["ba"] == pytest.approx((20 + 120 + 10 + 30) / 600)            # population-weighted
    assert r["makeup"]["medinc"] == pytest.approx((50000 * 100 + 70000 * 300 + 40000 * 100) / 500)
