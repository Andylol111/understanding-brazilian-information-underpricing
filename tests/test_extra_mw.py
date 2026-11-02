"""brunder.extra_clarity (IA/CO Clarity ENR) and brunder.extra_mt (MT results web service): House rows on official
2024 files (fixtures, real past-cycle data: Iowa SoS ENR election 122322, Colorado SoS ENR election 122598, Montana
GetMapDataArchive election 450002785) equal the certified totals in site/data/us2026/house/districts.json; the 2026
pre-count files (IA election 127077 before polls open, MT test site) parse without error."""
import gzip
import json
from pathlib import Path

import pandas as pd

from brunder import extra_clarity as C
from brunder import extra_mt as M

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us"
DJ = json.loads((ROOT / "site" / "data" / "us2026" / "house" / "districts.json").read_text())["districts"]


def _fx(name):
    return gzip.decompress((FIX / name).read_bytes())


def _clarity(st, tag):
    return {r: _fx(f"extra_clarity_{st}_{tag}_{r}.json.gz") for r in C.FILES}


def _check(df, st):
    assert set(df.columns) >= {"level", "unit_id", "race", "party", "votes", "reporting", "units"}
    assert (df["level"] == "county").all() and df["unit_id"].str.startswith(f"us-{st}-").all()
    g = df[df["party"].isin(["DEM", "REP"])].groupby(["race", "party"])["votes"].sum().unstack()
    for race, x in g.iterrows():
        h = DJ[f"{st.upper()}-{race[6:]}"]["house24"]
        assert (x["DEM"], x["REP"]) == (h["dv"], h["rv"]), race
    return g


def test_iowa_2024_exact():
    g = _check(C.parse("ia_house", _clarity("ia", "2024")), "ia")
    assert list(g.index) == ["house-01", "house-02", "house-03", "house-04"]


def test_colorado_2024_exact_split_counties():
    df = C.parse("co_house", _clarity("co", "2024"))
    g = _check(df, "co")
    assert len(g) == 8
    adams = df[df["unit_id"] == "us-co-08001"]["race"].unique()    # Adams County: CO-04 and CO-08 parts
    assert {"house-08"} <= set(adams) and len(adams) >= 2
    assert (df.groupby("unit_id")["units"].first() > 0).all()


def test_montana_2024_exact():
    df = M.parse("mt_house", {"d01": _fx("extra_mt_2024_cd01.json.gz")})
    _check(df, "mt")
    assert df["unit_id"].nunique() == 16 and set(df["race"]) == {"house-01"}


def test_precount_and_garbage():
    df = C.parse("ia_house", _clarity("ia", "2026pre"))
    assert set(df["race"]) == {"house-01", "house-02", "house-03", "house-04"} and df["unit_id"].nunique() == 99
    assert df["votes"].sum() == 0
    mt = M.parse("mt_house", {"d01": _fx("extra_mt_2026test_cd01.json.gz")})
    assert mt["unit_id"].nunique() == 16 and (mt["units"] > 0).all()
    for bad in ({}, {"all": b"", "summary": b"", "status": None}, {"all": b"{", "summary": b"[]", "status": b"<html>"}):
        assert C.parse("co_house", bad).empty
    assert M.parse("mt_house", {"d01": b"<html>challenge</html>"}).empty
    assert M.parse("mt_house", {"d01": None}).empty


def test_resolver_uses_listing_and_version():
    C._EID.clear()
    C._VER.clear()
    listing = json.dumps([{"EID": "1", "Date": "11/5/2024 12:00:00 AM", "County": ""},
                          {"EID": "127077", "Date": "11/3/2026 12:00:00 AM", "County": ""}]).encode()
    calls = []

    def get(url):
        calls.append(url)
        return listing if url.endswith("elections.json") else b"383162\n"

    specs = [s for s in C.RECORD if s["key"].startswith("clarity_ia_")]
    urls = [s["resolve"](get) for s in specs]
    assert urls == [f"{C.BASES['ia']}/127077/383162/{C.FILES[r]}" for r in ("all", "summary", "status")]
    assert sum(u.endswith("current_ver.txt") for u in calls) == 1        # memoised within a cycle
    assert all(s["interval"] >= 30 and s["host_class"] == "server-blocked" for s in specs)
    C._EID.clear()
    C._VER.clear()
    assert specs[0]["resolve"](lambda url: b"[]") is None                  # election not listed yet


def test_priors_cover_feed_counties():
    """Every prior county piece is a county the feed reports for that district, and vice versa (2024 lines = 2026)."""
    parsed = {"ia": C.parse("ia_house", _clarity("ia", "2024")), "co": C.parse("co_house", _clarity("co", "2024")),
              "mt": M.parse("mt_house", {"d01": _fx("extra_mt_2024_cd01.json.gz")})}
    for src in ("clarity_mw", "mt"):
        pri = pd.read_csv(ROOT / "data_public" / f"us2026_extra_priors_{src}.csv", comment="#")
        for s, p in pri.groupby("source"):
            df = parsed[s[:2]]
            assert set(p["unit_id"]) == set(df[df["race"] == f"house-{s[3:5]}"]["unit_id"]), s


def test_missouri_mo02_2024_county_parts():
    from brunder import extra_mo as MO
    df = MO.parse("mo_house", {"stlouis": _fx("extra_mo_stlouis_2024_subset.pdf.gz"),
                               "stcharles": _fx("extra_mo_stcharles_2024_subset.html.gz")})
    g = df[df["race"] == "house-02"].groupby(["unit_id", "party"])["votes"].sum()
    # St. Louis County certified "Official Results" (2024-11-19) and St. Charles County's final page
    assert (g["us-mo-29189", "DEM"], g["us-mo-29189", "REP"]) == (144111, 150461)
    assert (g["us-mo-29183", "DEM"], g["us-mo-29183", "REP"]) == (20592, 36153)
    h = DJ["MO-02"]["house24"]                        # the rest is Franklin and Warren (not covered)
    assert g.xs("DEM", level=1).sum() < h["dv"] and g.xs("REP", level=1).sum() < h["rv"]
    stl = df[df["unit_id"] == "us-mo-29189"]
    assert (stl["reporting"] == 231).all() and (stl["units"] == 231).all()
    pre = MO.parse("mo_house", {"stcharles": _fx("extra_mo_stcharles_2026pre_subset.html.gz"), "stlouis": b"<html>404"})
    p2 = pre[pre["race"] == "house-02"]
    assert set(p2["party"]) >= {"DEM", "REP"} and p2["votes"].sum() == 0 and (p2["units"] == 35).all()
    assert MO.parse("mo_house", {}).empty


def test_indiana_2024_in05_exact_and_resolver():
    from brunder import extra_in as IN
    df = IN.parse("in_house", {"offcat": _fx("extra_in_2024_offcat1005.json.gz")})
    x = df[(df["race"] == "house-05") & df["party"].isin(["DEM", "REP"])].groupby("party")["votes"].sum()
    h = DJ["IN-05"]["house24"]
    assert (x["DEM"], x["REP"]) == (h["dv"], h["rv"]) and df[df["race"] == "house-05"]["unit_id"].nunique() == 6
    pre = IN.parse("in_house", {"offcat": _fx("extra_in_2026pre_offcat1005.json.gz")})
    assert pre["race"].nunique() == 9 and pre["votes"].sum() == 0
    assert IN.parse("in_house", {"offcat": b"<html>"}).empty and IN.parse("in_house", {}).empty
    IN._VER.clear()
    spec = IN.RECORD[0]
    assert spec["resolve"](lambda u: b'{"Root":{"VersionType":"B"}}') == f"{IN.BASE}/OffCatC_1005_B.json"
    IN._VER.clear()
    assert spec["resolve"](lambda u: None) is None


def test_wisconsin_wi01_kenosha_rock():
    from brunder import extra_wi as WI
    df = WI.parse("wi_house", {"kenosha": _fx("extra_wi_kenosha_2024.html.gz"), "rock": _fx("extra_wi_rock_2024.html.gz")})
    g = df[df["race"] == "house-01"].groupby(["unit_id", "party"])["votes"].sum()
    assert (g["us-wi-55059", "DEM"], g["us-wi-55059", "REP"]) == (39813, 47584)     # Kenosha County's 2024 page
    assert (g["us-wi-55105", "DEM"], g["us-wi-55105", "REP"]) == (32018, 30294)     # Rock County, WI-01 part
    assert set(df[df["unit_id"] == "us-wi-55105"]["race"]) == {"house-01", "house-02"}
    rk = df[(df["unit_id"] == "us-wi-55105") & (df["race"] == "house-01")]
    assert (rk["reporting"] == 76).all() and (rk["units"] == 76).all()
    pre = WI.parse("wi_house", {"kenosha": _fx("extra_wi_kenosha_2026pre.html.gz"),
                                "rock": _fx("extra_wi_rock_2026pre.html.gz")})
    p1 = pre[pre["race"] == "house-01"]
    assert p1["unit_id"].nunique() == 2 and p1["votes"].sum() == 0 and set(p1["party"]) >= {"DEM", "REP"}
    assert set(p1.groupby("unit_id")["units"].first()) == {129.0, 78.0}
    assert WI.parse("wi_house", {"kenosha": b"<html>Incapsula incident</html>", "rock": None}).empty


def test_michigan_mi10_macomb_pdf():
    from brunder import extra_mi as MI
    df = MI.parse("mi_house", {"macomb": _fx("extra_mi_macomb_2024_unofficial.pdf.gz")})
    x = df[df["race"] == "house-10"].set_index("party")["votes"]
    assert (x["DEM"], x["REP"]) == (166802, 190543)          # Macomb's 2024 unofficial summary (night final)
    assert set(df["race"]) == {"house-09", "house-10"} and (df["unit_id"] == "us-mi-26099").all()
    pre = MI.parse("mi_house", {"macomb": _fx("extra_mi_macomb_2026pre.pdf.gz")})
    p10 = pre[pre["race"] == "house-10"]
    assert set(p10["party"]) >= {"DEM", "REP"} and pre["votes"].sum() == 0
    assert MI.parse("mi_house", {"macomb": b"<html>404</html>"}).empty and MI.parse("mi_house", {}).empty


def test_michigan_mi04_ottawa_clarity_summary():
    from brunder import extra_mi as MI
    df = MI.parse("mi_house", {"ottawa": _fx("extra_mi_ottawa_2024_summary.json.gz")})
    x = df[df["race"] == "house-04"].set_index("candidate")
    assert (x.loc["Jessica Swartz", "votes"], x.loc["Bill Huizenga", "votes"]) == (37295, 72859)
    assert (x["units"] == 74).all() and (x["unit_id"] == "us-mi-26139").all()
    pre = MI.parse("mi_house", {"ottawa": _fx("extra_mi_ottawa_2026pre_summary.json.gz")})
    p4 = pre[pre["race"] == "house-04"].set_index("party")
    assert p4.loc["DEM", "candidate"] == "Sean McCann" and p4.loc["REP", "candidate"] == "Bill Huizenga"
    assert pre["votes"].sum() == 0
    spec = next(s for s in MI.RECORD if s["key"] == "mi_ottawa_summary")
    from brunder import extra_clarity as C
    C._EID.clear()
    C._VER.clear()
    listing = json.dumps([{"EID": "127391", "Date": "11/3/2026 12:00:00 AM", "County": "Ottawa"}]).encode()
    url = spec["resolve"](lambda u: listing if u.endswith("elections.json") else b"382400")
    assert url == f"{MI.OTTAWA}/127391/382400/json/en/summary.json"
