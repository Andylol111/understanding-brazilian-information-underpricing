"""analysis/extra_parse.py (VA Enhanced Voting, IA Clarity) and analysis/extra_recorder.py (stubbed transport)."""
import gzip
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "extra"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "analysis" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


parse = _load("extra_parse")
rec = _load("extra_recorder")


def test_va_item_rows_county_candidate_vote_type():
    doc = json.loads((FIX / "va_item.json").read_bytes())
    b = doc["ballotItemWithBreakdown"]
    for g in b["breakdownResults"][1]["ballotOptions"][0]["groupResults"]:
        if g["groupName"][0]["text"] == "Mailed Absentee":
            g["voteCount"] = 7
    df = parse.va_item_rows(json.dumps(doc))
    assert set(df.level) == {"state", "county"}
    assert df.contest.iloc[0] == "Member, United States Senate"
    assert set(df.vote_type) == {"Total", "Early Voting", "Election Day", "Mailed Absentee", "Provisional", "Post-Election"}
    assert df[df.level == "county"].county.nunique() == 3
    r = df[(df.vote_type == "Mailed Absentee") & (df.votes == 7)]
    assert len(r) == 1 and r.iloc[0].candidate and r.iloc[0].county == b["breakdownResults"][1]["locality"]["name"][0]["text"]
    assert df.votes.sum() == 7


def test_va_statistics_has_absentee_by_method():
    s = parse.va_statistics((FIX / "va_main.json").read_bytes())
    m = s[s.group == "Absentee Ballots Returned by Method"].set_index("item").value
    assert {"Mail", "In Person", "Drop Off", "Mail (Non-USPS)", "Designated Representative"} <= set(m.index)
    reg = parse.va_registration((FIX / "va_main.json").read_bytes())
    assert len(reg) == 3 and (reg.total == reg.active + reg.inactive).all()


def test_ia_rows_vote_types_from_zip():
    df = parse.ia_rows((FIX / "ia_detailxml.zip").read_bytes())
    c = df[df.level == "county"]
    assert set(c.vote_type) == {"Election Day", "Absentee"}
    assert set(c.county) == {"Adair", "Adams", "Allamakee"}
    assert set(c.candidate) == {"Ashley Hinson", "Josh Turek"} and set(c.party) == {"REP", "DEM"}
    assert len(c) == 3 * 2 * 2 and df.votes.sum() == 0


def _recorder(tmp_path, handler, **kw):
    r = rec.Recorder(tmp_path, datetime.now(timezone.utc) + timedelta(hours=1), gap=0)
    r.web = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": rec.UA})
    return r


def test_clarity_fetches_on_version_change_only_and_stores_gz(tmp_path):
    ver = {"v": "100"}
    seen = []

    def h(req):
        seen.append(req.url.path)
        p = req.url.path
        if p == "/robots.txt":
            return httpx.Response(404)
        if p.endswith("current_ver.txt"):
            return httpx.Response(200, text=ver["v"], headers={"content-type": "text/plain"})
        if p.endswith("summary.json"):
            return httpx.Response(200, json=[], headers={"content-type": "application/json"})
        return httpx.Response(200, content=b"PK-zip", headers={"content-type": "application/zip"})

    r = _recorder(tmp_path, h)
    st = {}
    r.cycle_clarity("ia", rec.IA_BASE, st)
    r.cycle_clarity("ia", rec.IA_BASE, st)
    assert sum(p.endswith("detailxml.zip") for p in seen) == 1
    ver["v"] = "101"
    r.cycle_clarity("ia", rec.IA_BASE, st)
    assert sum(p.endswith("detailxml.zip") for p in seen) == 2
    zips = sorted((tmp_path / "ia").glob("*.detailxml.zip.gz"))
    assert len(zips) == 2 and gzip.decompress(zips[0].read_bytes()) == b"PK-zip"
    rows = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    z = [x for x in rows if x["url"].endswith("detailxml.zip")]
    assert z[-1]["version"] == "101" and z[-1]["status"] == 200 and z[-1]["bytes"] == 6 and len(z[-1]["sha256"]) == 64


def test_challenge_blocks_source_and_persists(tmp_path):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(202, content=b"")

    r = _recorder(tmp_path, h)
    try:
        r.cycle_sc({})
    except rec.Blocked:
        pass
    assert "sc" in r.stopped
    assert "sc" in rec.Recorder(tmp_path, r.until).stopped          # a restart does not retry
    last = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[-1])
    assert last["note"] == "challenge response" or last.get("event") == "blocked"


def test_not_modified_and_unchanged_not_stored(tmp_path):
    n = {"i": 0}

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        n["i"] += 1
        if req.headers.get("if-none-match") == '"a"':
            return httpx.Response(304)
        return httpx.Response(200, json={"x": 1}, headers={"etag": '"a"', "content-type": "application/json"})

    r = _recorder(tmp_path, h)
    assert r.fetch("va", rec.VA_BASE, "main.json", expect="json")[1] is not None
    assert r.fetch("va", rec.VA_BASE, "main.json", expect="json") == (304, None)
    assert len(list((tmp_path / "va").glob("*.gz"))) == 1
