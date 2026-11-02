"""Added 2026 U.S. secondary sources: pure parsing and synthetic archive-to-site export."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pandas as pd
import pytest

from brunder import extra_civic_house, extra_ev, us, us_extra_live, us_house_night

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_us():
    """us_extra_live.install() patches brunder.us in-process (the extra service's own process); undo it after each
    test so other test files see the registered behaviour."""
    from brunder import us_feeds_extra
    names = ("parse_feed", "load_priors", "race_prior", "kalshi_markets", "pm_tokens")
    saved = {n: getattr(us, n) for n in names}
    dicts = {id(d): (d, dict(d)) for d in (us.FEEDS, us.STATES, us.POLL_CLOSE, us.SOURCE_INFO, us_feeds_extra.CHECKS)}
    yield
    for n, f in saved.items():
        setattr(us, n, f)
    for d, copy in dicts.values():
        d.clear()
        d.update(copy)
    us_extra_live._INSTALLED = False


def wa_item(district: int = 3, dem: int = 60, rep: int = 40) -> dict:
    def option(name, party, votes):
        return {"name": [{"languageId": "en", "text": name}], "party": {"abbreviation": party},
                "voteCount": votes}
    return {"ballotItemWithBreakdown": {
        "name": [{"languageId": "en", "text": f"Congressional District {district} - U.S. Representative"}],
        "summaryResults": {"ballotOptions": [option("Marie Gluesenkamp Perez", "DEM", dem),
                                               option("John Braun", "REP", rep)]},
        "breakdownResults": [
            {"precinctReportingStatus": {"reportingUnits": 3, "totalUnits": 7}},
            {"precinctReportingStatus": {"reportingUnits": 2, "totalUnits": 5}},
        ],
    }}


def test_vote_wa_item_is_one_district_unit_with_summed_precinct_progress():
    rows = extra_ev.parse_item(wa_item())
    assert list(rows.columns) == us.COLS
    assert set(rows["race"]) == {"house-03"}
    assert set(rows["unit_id"]) == {"us-wa-cd03"}
    assert set(rows["candidate"]) == {"Marie Gluesenkamp Perez", "John Braun"}
    assert set(rows["party"]) == {"DEM", "REP"}
    assert set(rows["reporting"]) == {5.0} and set(rows["units"]) == {12.0}
    assert rows["votes"].sum() == 100


def test_civic_house_adapter_uses_only_recorded_blob():
    d = {"region_results": {"nv-02": {"name": "Nevada District 2", "percent_reporting": 37,
            "candidates": [{"name": "Teresa Benitez-Thompson", "party": "Democratic", "votes": 123},
                           {"name": "David Flippo", "party": "Republican", "votes": 127}]}}}
    rows = extra_civic_house.parse("civic_house", {"house": json.dumps(d).encode()})
    assert set(rows["unit_id"]) == {"us-nv-cd02"}
    assert set(rows["race"]) == {"house-02"}
    assert set(rows["reporting"]) == {37.0} and set(rows["units"]) == {100.0}
    assert rows.groupby("party")["votes"].sum().to_dict() == {"DEM": 123.0, "REP": 127.0}


def test_synthetic_snapshot_exports_wa03_end_to_end(tmp_path):
    """A synthetic VoteWA ballot item crosses Archive -> parser -> projection -> site House files."""
    us_extra_live.install()
    arc = tmp_path / "snap"
    key = arc / "wa_house_03"
    key.mkdir(parents=True)
    (key / "20261104T050000.000000Z.json").write_text(json.dumps(wa_item(dem=6000, rep=4000)))
    cfg = ROOT / "configs" / "us2026_extra" / "wa-03_v1.toml"
    ex = us_house_night.HouseExporter([cfg], arc)
    ex.update(pd.Timestamp("2026-11-04T05:01:00Z"))
    out = tmp_path / "site" / "data" / "extra"
    d = ex.write(out)
    assert d == out / "us2026" / "house_live"
    timeline = json.loads((d / "timeline.json").read_text())
    assert "house:WA-03" in timeline and timeline["house:WA-03"]
    assert timeline["house:WA-03"][-1]["counted"] > 0
    assert (d / "units" / "house:WA-03.json").exists()


def test_civic_statewide_config_reads_linked_shared_archive(tmp_path):
    """ME Senate (civicAPI fallback): a race document in the SHARED archive is linked into the extra archive and
    modelled from the extra archive, the shared archive is never written."""
    us_extra_live.install()
    shared, extra = tmp_path / "shared", tmp_path / "extra"
    c = us.counties("me")
    regions = {n.lower(): {"name": n, "type": "County", "fips": u[-5:], "percent_reporting": 50,
                           "candidates": [{"name": "Troy Jackson", "party": "Democratic", "votes": 1000},
                                          {"name": "Susan Collins", "party": "Republican", "votes": 900}]}
               for u, n in zip(c["unit_id"], c["name"])}
    doc = {"election_type": "US Senate", "province": "ME", "election_date": "2026-11-03T05:00:00.000Z",
           "test_data": False, "percent_reporting": 50, "region_results": regions,
           "candidates": [{"name": "Troy Jackson", "party": "Democratic", "votes": 1000 * len(c)},
                          {"name": "Susan Collins", "party": "Republican", "votes": 900 * len(c)}]}
    k = shared / "civic_87188"
    k.mkdir(parents=True)
    (k / "20261104T020000.000000Z.json").write_text(json.dumps(doc))
    cfg = ROOT / "configs" / "us2026_extra" / "me_sen_v1.toml"
    made = us_extra_live.link_shared([cfg], extra, shared)
    assert extra / "civic_87188" in made and (extra / "civic_87188").is_symlink()
    ex = us.Exporter([cfg], extra)
    ex.update(pd.Timestamp("2026-11-04T02:05:00Z"))
    d = ex.write(tmp_path / "site" / "data" / "extra")
    tl = json.loads((d / "timeline.json").read_text())
    assert tl["me-sen"][-1]["counted"] > 0
    assert sorted(p.name for p in shared.iterdir()) == ["civic_87188"]


def test_recorder_stores_changes_and_stops_host_on_403(tmp_path, monkeypatch):
    from brunder import us_extra_rec as R
    monkeypatch.setattr(R, "HOST_GAP", 0.0)
    body = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["user-agent"] == R.UA
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        body["n"] += 1
        if body["n"] == 3:
            return httpx.Response(403, text="denied")
        return httpx.Response(200, json={"v": 1}, headers={"content-type": "application/json"})
    rec = R.Recorder(tmp_path)
    rec.web = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": R.UA})

    class M:
        NAME = "test"
    spec = {"key": "wa_house_03", "url": "https://results.votewa.gov/x", "interval": 60, "ext": "json"}
    for _ in range(4):
        rec.poll(M, spec, R.now())
    files = list((tmp_path / "wa_house_03").iterdir())
    assert len(files) == 1                                  # second 200 is unchanged: not stored again
    assert "results.votewa.gov" in rec.blocked              # 403 stops the host
    assert body["n"] == 3                                   # no request after the block
    idx = [json.loads(x) for x in (tmp_path / "index.jsonl").read_text().splitlines()]
    assert [x["key"] for x in idx] == ["wa_house_03"]


def test_recorder_refuses_forbidden_hosts():
    from brunder import us_extra_rec as R
    assert R.forbidden("civicapi.org") and R.forbidden("orresws.azurewebsites.us") and R.forbidden("results.sos.ga.gov")
    assert not R.forbidden("results.votewa.gov")


def test_vote_wa_real_2026_primary_item_cd05():
    """Real VoteWA ballot item (2026-08-04 top-two primary, WA-05, fetched 2026-11-02; reduced to the item): district
    totals = summaryResults, precinct progress = sum of the 12 county breakdowns (842/842, complete), and the
    county breakdowns add up to the district totals."""
    import gzip
    raw = gzip.decompress((ROOT / "tests" / "fixtures" / "us" / "extra_ev_wa_20260804_cd05.json.gz").read_bytes())
    rows = extra_ev.parse_item(raw)
    assert set(rows["race"]) == {"house-05"} and set(rows["unit_id"]) == {"us-wa-cd05"}
    by = rows.groupby("candidate")["votes"].sum()
    assert by["Michael Baumgartner"] == 96791 and by["Carmela Conroy"] == 38487
    assert set(rows["reporting"]) == {842.0} and set(rows["units"]) == {842.0}
    b = json.loads(raw)["ballotItemWithBreakdown"]
    assert len(b["breakdownResults"]) == 12
    county = {}
    for br in b["breakdownResults"]:
        for o in br["ballotOptions"]:
            county[o["name"][0]["text"]] = county.get(o["name"][0]["text"], 0) + o["voteCount"]
    assert all(county[k] == v for k, v in by.items())


def test_oregon_real_2026_precount_and_filled_count():
    """Real 2026 pre-count GetMapData (fetched 2026-11-02): 36 counties x 3 candidates (write-in dropped), parties from
    PartyName (PartyCode is NON for all); a filled copy gives exact county and state sums."""
    import gzip
    from brunder import extra_or
    raw = gzip.decompress((ROOT / "tests" / "fixtures" / "us" / "extra_or_2026_gov_precount.json.gz").read_bytes())
    df = extra_or.parse("or_extra", {"gov": raw})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 36 and set(c["unit_id"]) == set(us.counties("or")["unit_id"])
    assert dict(c.groupby("candidate")["party"].first()) == {"Tina Kotek": "DEM", "Christine Drazan": "REP",
                                                              "Brett Smith": "PAC"} or \
        c.groupby("candidate")["party"].first()[["Tina Kotek", "Christine Drazan"]].tolist() == ["DEM", "REP"]
    d = json.loads(raw)
    for x in d["d"]:
        x["calcCandidateVotes"] = 10 if x["calcCandidate"] == "Tina Kotek" else 7
        x["PrecinctsReporting"] = x["TotalPrecincts"]
    f = extra_or.parse("or_extra", {"gov": json.dumps(d).encode()})
    s = f[f["level"] == "state"].set_index("party")["votes"]
    assert s["DEM"] == 360 and s["REP"] == 252
    assert extra_or.parse("or_extra", {"gov": b"<html>"}).empty and extra_or.parse("or_extra", {"gov": None}).empty


def test_link_shared_never_links_keys_the_added_recorder_writes(tmp_path):
    us_extra_live.install()
    cfgs = sorted((ROOT / "configs" / "us2026_extra").glob("*_v1.toml"))
    made = {p.name for p in us_extra_live.link_shared(cfgs, tmp_path / "extra", tmp_path / "shared")}
    own = {s["key"] for m in us_extra_live.MODULES for s in m.RECORD}
    assert not made & own
    assert {"civic_87188", "civic_87260", "civic_87283", "civic_87284", "civic_87524"} <= made
    assert "polymarket_books" not in made and "kalshi_books" not in made


def test_pdf_and_html_snapshots_pass_the_archive_check():
    us_extra_live.install()
    us_extra_live._check_pdf(b"%PDF-1.7\n...\n%%EOF\n")
    us_extra_live._check_text("<html>é</html>".encode("latin-1"))


def test_markets_json_maps_every_added_race_with_yes_on_the_democrat():
    """configs/us2026_extra/markets.json (analysis/us_extra_markets.py, looked up 2026-11-02): every added race has a
    Polymarket and a Kalshi market, YES = config side a, the configs carry the same Polymarket event/market."""
    import tomllib
    from brunder import extra_markets
    m = extra_markets.load()
    for f in sorted((ROOT / "configs" / "us2026_extra").glob("*_v*.toml")):
        c = tomllib.loads(f.read_text())
        rid, dem = c["election"]["race_id"], c["election"]["a"]["name"]
        pm, k = m[rid]["pm"], m[rid]["kalshi"]
        assert pm["yes_is"] == "a" and "democrat" in pm["question"].lower(), rid
        assert (c["markets"]["polymarket"]["event"], c["markets"]["polymarket"]["market"]) == (pm["event"], pm["slug"])
        assert c["markets"]["polymarket"].get("yes_is", "a") == "a"
        assert k["yes_is"] == "a" and "democrat" in k["question"].lower(), rid
        yes_name = k["question"].split("YES:")[-1].strip().lower()
        assert dem.split()[-1].lower() in yes_name or yes_name.split()[-1] in dem.lower(), (rid, dem, yes_name)
        assert len(pm["token"]) > 20 and k["ticker"]
    assert len(m) == len(list((ROOT / "configs" / "us2026_extra").glob("*_v1.toml")))


def test_market_books_give_row_prices_oriented_to_the_democrat(tmp_path):
    """Books in the registered formats under the extra archive -> rows' pm / kalshi = P(side a), as for the
    registered races (Polymarket mid of the YES book; Kalshi mid of yes bid / implied ask)."""
    from brunder import extra_markets
    us_extra_live.install()
    arc = tmp_path / "snap"
    m = extra_markets.load()["house:WA-03"]
    for key, doc in (
            ("polymarket_books", {f"{m['pm']['event']}/{m['pm']['slug']}": {
                "asset_id": m["pm"]["token"], "bids": [{"price": "0.30", "size": "100"}],
                "asks": [{"price": "0.32", "size": "100"}], "last_trade_price": "0.31"}}),
            ("kalshi_books", {m["kalshi"]["ticker"]: {"yes_dollars": [["0.2800", "10"]], "no_dollars": [["0.7000", "10"]]}}),
            ("wa_house_03", wa_item(dem=6000, rep=4000))):
        (arc / key).mkdir(parents=True)
        (arc / key / "20261104T050000.000000Z.json").write_text(json.dumps(doc))
    cfg = ROOT / "configs" / "us2026_extra" / "wa-03_v1.toml"
    ex = us_house_night.HouseExporter([cfg], arc)
    assert ex.kalshi_mk["house:WA-03"]["ticker"] == m["kalshi"]["ticker"] and ex.tokens["house:WA-03"] == m["pm"]["token"]
    ex.update(pd.Timestamp("2026-11-04T05:01:00Z"))
    row = ex.timeline["house:WA-03"][-1]
    assert row["pm"] == pytest.approx(0.31) and row["kalshi"] == pytest.approx(0.29)


def test_forbidden_hosts_are_exact_and_the_oregon_cdn_passes():
    from brunder import us_extra_rec as R
    assert R.forbidden("orresws.azurewebsites.us") and R.forbidden("sdresws.azurewebsites.us")
    assert not R.forbidden("orresultswebservices.azureedge.us")
    assert not R.forbidden("azurewebsites.us.example.org")
    assert R.forbidden("civicapi.org") and R.forbidden("api.civicapi.org") and not R.forbidden("api.civicapi.org.example.com")
