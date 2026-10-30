"""brunder.us_national_feeds: SD, VA, ID, MD, MA parsers on saved official payloads (tests/fixtures/us_national), checked
against certified statewide totals; fetch plans on a mock transport. No network except the optional live test."""
import asyncio
import gzip
import os
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from brunder import us
from brunder import us_national_feeds as N

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us_national"


def rd(name: str) -> bytes:
    b = (FIX / name).read_bytes()
    return gzip.decompress(b) if name.endswith(".gz") else b


def state(df: pd.DataFrame, race: str) -> pd.Series:
    return df[(df["level"] == "state") & (df["race"] == race)].groupby("party")["votes"].sum()


def county(df: pd.DataFrame, race: str) -> pd.Series:
    return df[(df["level"] == "county") & (df["race"] == race)].groupby("party")["votes"].sum()


def close(a: float, b: float, tol: float = 0.005) -> bool:
    return abs(a / b - 1) <= tol


def baseline_ids(st: str) -> set[str]:
    b = us.load_county_baseline()
    return set(b.loc[b["state"] == st, "unit_id"])


@pytest.fixture(autouse=True)
def _clear_cache():
    N._IDS.clear()
    yield
    N._IDS.clear()


# ------------------------------------------------------------------ certified totals (county rows vs statewide)
# Certified references (independent of the feeds): MEDSL 2024-senate-county.csv (CC0, state canvasses) for VA/MD/MA 2024
# Senate; MEDSL 2022-elections-official precinct files for SD 2022 (governor, Senate), MD and MA 2022 governor.

def test_sd_2022_certified():
    df = N.parse_national("sd", {"gov": rd("sd_2022_gov_mapdata.json.gz"), "sen": rd("sd_2022_sen_mapdata.json.gz")})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 66 and set(c["unit_id"]) == baseline_ids("sd")
    for race, (d, r) in {"gov": (123148, 217035), "sen": (91007, 242316)}.items():   # Smith/Noem, Bengs/Thune
        assert close(county(df, race)["DEM"], d) and close(county(df, race)["REP"], r)
        assert state(df, race)["DEM"] == d and state(df, race)["REP"] == r


def test_va_2024_senate_certified():
    df = N.parse_national("va", {"sen": rd("va_2024_sen_item.json.gz")})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 133 and set(c["unit_id"]) == baseline_ids("va")
    assert close(county(df, "sen")["DEM"], 2417115) and close(county(df, "sen")["REP"], 2019911)   # Kaine, Cao
    assert state(df, "sen")["DEM"] == 2417115
    assert c[c["name"] == "Fairfax city"]["unit_id"].iloc[0] == "us-va-51600"
    assert c[c["name"] == "Fairfax"]["unit_id"].iloc[0] == "us-va-51059"
    assert (c["reporting"] == c["units"]).all()


def test_md_certified():
    g = N.parse_national("md", {"gov": rd("md_2022_gov_detail.html.gz")})
    assert g[g["level"] == "county"]["unit_id"].nunique() == 24 and set(g["unit_id"]) - {"us-md"} == baseline_ids("md")
    assert close(county(g, "gov")["DEM"], 1293944) and close(county(g, "gov")["REP"], 644000)   # Moore, Cox
    s = N.parse_national("md", {"sen": rd("md_2024_sen_detail.html.gz")})
    assert close(county(s, "sen")["DEM"], 1650912)                                              # Alsobrooks, Hogan
    assert close(county(s, "sen")["REP"], 1294344)
    assert state(s, "sen")["DEM"] == 1650912
    bc = s[(s["level"] == "county") & (s["name"] == "Baltimore city")]
    assert bc["unit_id"].iloc[0] == "us-md-24510"


def test_ma_certified():
    s = N.parse_national("ma", {"sen": rd("ma_2024_sen_view.html.gz")})
    assert s[s["level"] == "county"]["unit_id"].nunique() == 14 and set(s["unit_id"]) - {"us-ma"} == baseline_ids("ma")
    assert state(s, "sen")["DEM"] == 2041668 and state(s, "sen")["REP"] == 1365440          # Warren, Deaton
    assert county(s, "sen")["DEM"] == 2041668
    g = N.parse_national("ma", {"gov": rd("ma_2022_gov_view.html.gz")})
    assert state(g, "gov")["DEM"] == 1584403 and state(g, "gov")["REP"] == 859343            # Healey, Diehl
    assert close(county(g, "gov")["DEM"], 1584403) and close(county(g, "gov")["REP"], 859343)  # county table: 0.2 % short


def test_id_platform_checks():
    """No past general election exists on Idaho's Enhanced Voting site: the 2026 primary items (same API as VA) must
    have county rows that sum to the feed's own statewide summary, with all 44 counties."""
    for name, race in (("id_may2026_gov_rep_item.json.gz", "gov"), ("id_may2026_sen_rep_item.json.gz", "sen"),
                       ("id_may2026_gov_dem_item.json.gz", "gov")):
        df = N.parse_national("id", {race: rd(name)})
        c = df[df["level"] == "county"]
        assert c["unit_id"].nunique() == 44 and set(c["unit_id"]) == baseline_ids("id")
        by = pd.concat([c.groupby("candidate")["votes"].sum(), df[df["level"] == "state"].set_index("candidate")["votes"]], axis=1)
        assert (by.iloc[:, 0] == by.iloc[:, 1]).all()
    rep = N.parse_national("id", {"gov": rd("id_may2026_gov_rep_item.json.gz")})
    assert set(rep["party"]) == {"REP"} and rep[rep["level"] == "state"]["votes"].max() == 140709   # Little


# ------------------------------------------------------------------ 2026 shells (feeds exist, nothing counted yet)

def test_2026_shells():
    sd = N.parse_national("sd", {"gov": rd("sd_2026_gov_mapdata.json.gz"), "sen": rd("sd_2026_sen_mapdata.json.gz")})
    assert sd["votes"].sum() == 0 and {"DEM", "REP"} <= set(sd["party"])
    st = sd[sd["level"] == "state"]
    assert (st["reporting"] == 0).all() and (st["units"] == 671).all()
    assert set(sd.loc[sd["race"] == "gov", "candidate"]) >= {"Larry Rhoden and Tony Venhuizen", "Dan Ahlers and Steven McCleerey"}
    va = N.parse_national("va", {"sen": rd("va_2026_sen_item_shell.json.gz")})
    assert va["votes"].sum() == 0 and va[va["level"] == "county"]["unit_id"].nunique() == 133
    assert {"Mark R. Warner", "Bert Mizusawa"} <= set(va["candidate"])
    idd = N.parse_national("id", {"sen": rd("id_2026_sen_item_shell.json.gz"), "gov": rd("id_2026_gov_item_shell.json.gz")})
    assert idd["votes"].sum() == 0 and set(idd["race"]) == {"sen", "gov"}
    assert {"Jim Risch", "Brad Little", "Terri Pickens"} <= set(idd["candidate"])
    md = N.parse_national("md", {"gov": rd("md_2026_gov_detail_shell.html.gz")})
    assert md["votes"].sum() == 0 and md[md["level"] == "state"]["units"].iloc[0] == 1979 and (md["race"] == "gov").all()


def test_empty_and_unknown():
    for st in N.STATES_NATIONAL:
        assert N.parse_national(st, {}).empty and list(N.parse_national(st, {}).columns) == us.COLS
    with pytest.raises(KeyError):
        N.parse_national("wy", {})
    with pytest.raises(ValueError):                      # a wrong contest in the slot is refused, not parsed as a race
        N.parse_national("md", {"gov": b"<h2>Comptroller</h2><table></table>"})


def test_module_tables():
    assert set(N.STATES_NATIONAL) == set(N.FEEDS_NATIONAL) == set(N.RACES_NATIONAL) == set(N.POLL_CLOSE_NATIONAL)
    assert set(N.POLL_CLOSE_LAST_NATIONAL) <= set(N.STATES_NATIONAL)
    assert not set(N.STATES_NATIONAL) & set(us.FEEDS) and not set(N.STATES_NATIONAL) & set(N.NOT_USABLE)
    for st, t in N.POLL_CLOSE_NATIONAL.items():
        assert t.startswith("2026-11-04T") and t.endswith("Z")
        assert N.POLL_CLOSE_LAST_NATIONAL.get(st, t) >= t
    assert N.POLL_CLOSE_NATIONAL["va"] == "2026-11-04T00:00:00Z" and N.POLL_CLOSE_NATIONAL["md"] == "2026-11-04T01:00:00Z"


# ------------------------------------------------------------------ discovery and fetch plans

def test_discovery():
    assert N.sd_discover(rd("sd_2026_swr_page.html.gz")) == {"sen": ("14412", "110"), "gov": ("14896", "205")}
    assert N.sd_discover(rd("sd_2022_swr_page.html.gz")) == {"sen": ("10246", "110"), "gov": ("10244", "205")}
    assert N.ev_election_id(rd("va_jurisdiction_elections_subset.json.gz")) == "2026-November-General"
    assert N.ev_election_id(rd("id_jurisdiction_elections_subset.json.gz")) == "nov2026"
    assert list(N.ev_items(rd("va_2026_data_items_subset.json.gz"))) == ["sen"]
    assert set(N.ev_items(rd("id_2026_data_items_subset.json.gz"))) == {"sen", "gov"}
    assert N.ev_items(rd("id_may2026_data_items_subset.json.gz")) == {}      # primary titles carry a party suffix
    assert N.ma_discover(rd("ma_search_2024_senate.html.gz")) == "165304"
    with pytest.raises(RuntimeError):
        N.ma_discover(b"<html></html>")


def _transport() -> httpx.MockTransport:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        seen.append(u)
        assert req.headers["user-agent"].startswith("Mozilla")
        table = {
            N.SD_PAGE: "sd_2026_swr_page.html.gz",
            "raceID=14896": "sd_2026_gov_mapdata.json.gz", "raceID=14412": "sd_2026_sen_mapdata.json.gz",
            f"{N.VA_API}/jurisdictions/virginia": "va_jurisdiction_elections_subset.json.gz",
            "virginia/2026-November-General/data/ballot-item/": "va_2026_sen_item_shell.json.gz",
            "virginia/2026-November-General/data": "va_2026_data_items_subset.json.gz",
            f"{N.ID_API}/jurisdictions/id": "id_jurisdiction_elections_subset.json.gz",
            "id/nov2026/data/ballot-item/01000000-70f4-ca87-27ba-08df23e9954c": "id_2026_sen_item_shell.json.gz",
            "id/nov2026/data/ballot-item/01000000-70f4-ca87-1a95-08df23ecef0e": "id_2026_gov_item_shell.json.gz",
            "id/nov2026/data": "id_2026_data_items_subset.json.gz",
            N.FEEDS_NATIONAL["md"]["gov"]: "md_2026_gov_detail_shell.html.gz",
            "year_from:2026/year_to:2026/office_id:6/stage:General": "ma_search_2024_senate.html.gz",
            "year_from:2026/year_to:2026/office_id:3/stage:General": "ma_search_2024_senate.html.gz",
            "elections/view/165304/": "ma_2024_sen_view.html.gz",
        }
        for key, name in table.items():                     # most specific key first (insertion order)
            if key in u:
                return httpx.Response(200, content=rd(name))
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


@pytest.mark.parametrize("st", ["sd", "va", "id", "md"])
def test_fetch_and_parse_on_mock(st):
    t = _transport()
    with httpx.Client(transport=t) as c:
        blobs = N.fetch(st, c)
        n1 = len(t.seen)
        assert set(blobs) == set(N.RACES_NATIONAL[st])
        df = N.parse_national(st, blobs)
        assert set(df["race"]) == set(N.RACES_NATIONAL[st]) and df[df["level"] == "county"]["unit_id"].nunique() > 0
        N.fetch(st, c)                                       # second poll: discovery is cached, one request per key
        assert len(t.seen) - n1 == len(N.RACES_NATIONAL[st])


def test_fetch_ma_and_async_and_errors():
    t = _transport()
    with httpx.Client(transport=t) as c:                     # the mock serves the 2024 senate election for both searches
        assert set(N.fetch("ma", c)) == {"sen", "gov"}

    async def run():
        async with httpx.AsyncClient(transport=_transport()) as ac:
            return await N.afetch("va", ac)
    assert set(asyncio.run(run())) == {"sen"}
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(RuntimeError, match="503"):
        N.fetch("md", bad)


def test_sd_rediscovers_when_ids_change():
    calls = []

    def handler(req):
        u = str(req.url)
        calls.append(u)
        if u.startswith(N.SD_PAGE):
            return httpx.Response(200, content=rd("sd_2026_swr_page.html.gz"))
        if "raceID=14896" in u:
            return httpx.Response(200, content=rd("sd_2026_gov_mapdata.json.gz"))
        if "raceID=14412" in u:
            return httpx.Response(200, content=rd("sd_2026_sen_mapdata.json.gz"))
        return httpx.Response(200, content=b'{"d":null}')
    N._IDS["sd"] = {"sen": ("1", "110"), "gov": ("2", "205")}          # stale ids answer {"d": null}
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert set(N.fetch("sd", c)) == {"sen", "gov"}
    assert N._IDS["sd"]["gov"] == ("14896", "205")


@pytest.mark.skipif(not os.environ.get("BRUNDER_LIVE"), reason="live network (set BRUNDER_LIVE=1)")
@pytest.mark.parametrize("st", ["sd", "va", "id", "md"])
def test_live(st):
    with httpx.Client() as c:
        df = N.parse_national(st, N.fetch(st, c))
    assert df[df["level"] == "county"]["unit_id"].nunique() > 0 and not df["votes"].isna().any()
