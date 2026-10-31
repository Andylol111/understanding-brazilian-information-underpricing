"""Early-vote database core (analysis/us_ev_db.py): parsers on small real excerpts (aggregate rows only), the
manifest / sha256 idempotency of snapshots, and the rule that error or challenge bodies are never cached."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))
import us_ev_db as db  # noqa: E402
import us_ev_legacy as legacy  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "us_ev"


class Server:
    """httpx.MockTransport handler whose reply can be swapped between calls."""

    def __init__(self, body=b"a,b\n1,2\n", status=200, headers=None):
        self.body, self.status, self.headers, self.calls = body, status, headers or {}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404, text="not found")
        self.calls.append(dict(req.headers))
        inm = req.headers.get("if-none-match")
        if inm and inm == self.headers.get("etag") and self.status == 200:
            return httpx.Response(304)
        return httpx.Response(self.status, content=self.body, headers=self.headers)


def make_store(tmp_path, server) -> db.Store:
    return db.Store(tmp_path / "us_ev", client=httpx.Client(transport=httpx.MockTransport(server)), min_interval=0)


def raw_files(st: db.Store) -> list[Path]:
    return sorted(p for p in st.raw.rglob("*") if p.is_file())


# ------------------------------------------------------------------------------------------ parsers

def test_parse_nc_counts_sums_and_party_mapping():
    crow, srow = db.parse_nc_counts((FIX / "nc_counts_excerpt.csv").read_bytes(), "2026-10-30")
    d = pd.read_csv(FIX / "nc_counts_excerpt.csv")
    n = d.columns[-1]
    c = pd.DataFrame(crow)
    assert set(c["county_fips"]) == {"37001", "37183"}                       # Alamance, Wake
    assert set(c["measure"]) == {"inperson_early", "mail_accepted", "total"}
    assert set(c["party"]) == {"D", "R", "U", "other", "ALL"} or set(c["party"]) <= set(db.PARTIES)
    mail = d["abs_request_type"].isin(["CIVILIAN", "MILITARY", "OVERSEAS"])
    s = pd.DataFrame(srow).set_index(["measure", "party"])["count"]
    assert s[("mail_accepted", "ALL")] == d.loc[mail, n].sum()
    assert s[("inperson_early", "ALL")] == d.loc[~mail, n].sum()
    assert s[("total", "ALL")] == d[n].sum()
    assert s[("total", "D")] == d.loc[d["party_cd"] == "DEM", n].sum()
    assert s[("total", "U")] == d.loc[d["party_cd"] == "UNA", n].sum()
    assert (c.groupby(["measure", "party"])["count"].sum().reindex(s.index) == s).all()


def test_nc_validate_rejects_html_and_wrong_header():
    db._nc_validate((FIX / "nc_counts_excerpt.csv").read_bytes())
    with pytest.raises(ValueError):
        db._nc_validate(b"<html>Access Denied</html>")


def _fl(name):
    return db.parse_fl_stats((FIX / f"fl_{name}_excerpt.txt").read_bytes())


def test_parse_fl_stats_asof_is_day_before_compile_date():
    as_of, units = _fl("VbmVoted")
    assert as_of == "2026-10-29"                                            # compiled 10/30
    assert set(units) == {"STATE", "Alachua", "Baker", "Leon"}
    assert units["STATE"]["ALL"] == 1_769_250 and units["STATE"]["D"] == 762_012 and units["STATE"]["U"] == 341_393
    assert units["Alachua"]["R"] + units["Alachua"]["D"] + units["Alachua"]["U"] + units["Alachua"]["other"] == units["Alachua"]["ALL"]


def test_fl_rows_total_and_requested_only_when_asof_agree():
    parts = {k: _fl(n) for k, n in (("vbm_voted", "VbmVoted"), ("early_voted", "EarlyVoted"), ("vbm_provided", "VbmProvided"))}
    crow, srow = db.fl_rows(parts)
    s = pd.DataFrame(srow).set_index(["measure", "party"])["count"]
    assert s[("total", "ALL")] == 1_769_250 + 2_504_784
    assert s[("mail_requested", "ALL")] == 1_769_250 + 673_998
    assert {r["county_fips"] for r in crow} == {"12001", "12003", "12073"}  # Alachua, Baker, Leon
    parts["early_voted"] = ("2026-10-28", parts["early_voted"][1])
    _, srow2 = db.fl_rows(parts)
    assert ("total", "ALL") not in {(r["measure"], r["party"]) for r in srow2}


def test_parse_tx_evr_cumulative_by_county():
    body = (FIX / "tx_evr_excerpt.json").read_bytes()
    db._tx_validate(body)
    crow, srow = db.parse_tx_evr(body)
    j = db._tx_payload(body)
    ip = sum(r["total_in_person_votes_for_election"] for r in j["turnout_by_county"])
    ml = sum(r["total_mail_votes_for_election"] for r in j["turnout_by_county"])
    s = {(r["measure"], r["party"]): r["count"] for r in srow}
    assert s[("inperson_early", "ALL")] == ip and s[("mail_returned", "ALL")] == ml and s[("total", "ALL")] == ip + ml
    assert {r["county_fips"] for r in crow} == {"48001", "48003", "48453"}   # Anderson, Andrews, Travis
    assert all(r["as_of_date"] == j["early_voting_date"] for r in crow)


def test_county_fips_names():
    assert db.county_fips("la", "St. Tammany") == "22103"
    assert db.county_fips("fl", "Miami-Dade") == "12086"
    assert db.county_fips("nc", "MECKLENBURG") == "37119"
    with pytest.raises(KeyError):
        db.county_fips("nc", "Nowhere")


# ------------------------------------------------------------------------------------------ snapshots / manifest

def test_snapshot_written_once_and_manifest_matches(tmp_path):
    srv = Server(headers={"etag": '"v1"'})
    st = make_store(tmp_path, srv)
    r1 = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc")
    assert r1.ok and r1.changed and r1.path.parent.name == "nc"
    assert r1.path.name.endswith("_t_src.csv") and len(r1.path.name.split("_")[0]) == 14       # YYYYMMDDTHHMMZ
    assert (r1.path.stat().st_mode & 0o222) == 0                                              # immutable
    r2 = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc")                # ETag -> 304
    assert r2.ok and not r2.changed and r2.status == 304 and r2.snapshot_id == r1.snapshot_id
    assert srv.calls[-1].get("if-none-match") == '"v1"'
    srv.headers = {}                                                                          # no validators: same sha256
    r3 = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", conditional=False)
    assert r3.ok and not r3.changed and r3.snapshot_id == r1.snapshot_id
    assert len(raw_files(st)) == 1
    man = (st.root / "MANIFEST.sha256").read_text().splitlines()
    assert man == [f"{db.sha256_bytes(srv.body)}  {r1.path.relative_to(st.root)}"]
    row = st.db.execute("SELECT sha256, bytes, http_status, valid FROM snapshots").fetchall()
    assert row == [(db.sha256_bytes(srv.body), len(srv.body), 200, 1)]
    srv.body = b"a,b\n1,3\n"                                                                  # changed content: new file
    r4 = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", conditional=False)
    assert r4.changed and r4.snapshot_id != r1.snapshot_id and len(raw_files(st)) == 2
    assert r1.path.read_bytes() == b"a,b\n1,2\n"                                              # old snapshot untouched
    assert len((st.root / "MANIFEST.sha256").read_text().splitlines()) == 2


def test_two_snapshots_in_one_minute_never_overwrite(tmp_path):
    srv = Server()
    st = make_store(tmp_path, srv)
    a = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", part="p")
    srv.body = b"x,y\n9,9\n"
    b = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", part="p")
    assert a.path != b.path and a.path.exists() and b.path.exists()


@pytest.mark.parametrize("status,body", [(503, b"Service Unavailable"), (404, b"not found"), (403, b"Forbidden"),
                                         (500, b"<html>error</html>")])
def test_http_errors_are_not_cached(tmp_path, status, body):
    st = make_store(tmp_path, Server(body=body, status=status))
    r = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc")
    assert not r.ok and r.status == status and raw_files(st) == []
    assert not (st.root / "MANIFEST.sha256").exists()
    assert st.db.execute("SELECT valid, file, sha256, http_status FROM snapshots").fetchall() == [(0, None, None, status)]
    assert st.db.execute("SELECT outcome FROM fetch_log").fetchall() == [("failed",)]


@pytest.mark.parametrize("ext,body", [
    ("csv", b"<!DOCTYPE html><html><title>Just a moment...</title></html>"),   # Cloudflare challenge served as 200
    ("csv", b""),
    ("pdf", b"<html>Request unsuccessful. Incapsula incident ID: 1</html>"),
    ("xlsx", b"<html>denied</html>"),
    ("json", b"not json"),
    ("html", b"<html><body>Access Denied - captcha</body></html>"),
])
def test_challenge_and_invalid_bodies_with_200_are_not_cached(tmp_path, ext, body):
    st = make_store(tmp_path, Server(body=body))
    r = st.fetch("t_src", "http://example.test/a." + ext, ext=ext, state="nc")
    assert not r.ok and raw_files(st) == []
    assert st.db.execute("SELECT valid, file FROM snapshots").fetchall() == [(0, None)]


def test_source_validation_failure_and_skip(tmp_path):
    st = make_store(tmp_path, Server(body=b"wrong,header\n1,2\n"))

    def need_header(b: bytes):
        if not b.startswith(b"county,"):
            raise ValueError("bad header")
    r = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", validate=need_header)
    assert not r.ok and raw_files(st) == [] and "bad header" in r.message

    def later(b: bytes):
        raise db.Skip("not yet reported")
    r = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", validate=later)
    assert not r.ok and raw_files(st) == []
    assert st.db.execute("SELECT outcome FROM fetch_log ORDER BY rowid").fetchall() == [("failed",), ("skipped",)]
    assert st.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1               # skip leaves no snapshot row


def test_failure_after_success_keeps_previous_snapshot(tmp_path):
    srv = Server()
    st = make_store(tmp_path, srv)
    ok = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc")
    srv.status, srv.body = 502, b"Bad gateway"
    bad = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc", conditional=False)
    assert not bad.ok and len(raw_files(st)) == 1 and st.last_valid("t_src")[0] == ok.snapshot_id


def test_robots_disallow_blocks_fetch(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, content=b"a,b\n1,2\n")
    st = db.Store(tmp_path / "us_ev", client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    assert not st.fetch("t_src", "http://example.test/private/a.csv", ext="csv", state="nc").ok
    assert st.fetch("t_src", "http://example.test/public/a.csv", ext="csv", state="nc").ok


# ------------------------------------------------------------------------------------------ rows

def _row(**kw):
    r = {"state": "nc", "county_fips": "37001", "as_of_date": "2026-10-30", "measure": "total", "party": "ALL", "count": 10}
    return {**r, **kw}


def test_add_rows_skips_identical_latest_and_keeps_restatements(tmp_path):
    st = make_store(tmp_path, Server())
    a = st.fetch("t_src", "http://example.test/a.csv", ext="csv", state="nc")
    assert st.add_rows([_row()], source_id="t_src", snapshot_id=a.snapshot_id, level="county") == 1
    assert st.add_rows([_row()], source_id="t_src", snapshot_id=a.snapshot_id, level="county") == 0
    assert st.db.execute("SELECT parsed FROM snapshots").fetchone()[0] == 1
    assert st.add_rows([_row(count=12)], source_id="t_src", snapshot_id=a.snapshot_id + 1, level="county") == 1
    assert st.add_rows([_row(count=12)], source_id="t_src", snapshot_id=a.snapshot_id + 2, level="county") == 0
    assert st.db.execute("SELECT COUNT(*) FROM ev_county").fetchone()[0] == 2
    assert st.db.execute('SELECT "count" FROM ev_county_latest').fetchall() == [(12,)]


def test_add_rows_rejects_unknown_vocabulary(tmp_path):
    st = make_store(tmp_path, Server())
    with pytest.raises(ValueError):
        st.add_rows([_row(measure="votes")], source_id="t_src", snapshot_id=1, level="county")
    with pytest.raises(ValueError):
        st.add_rows([_row(party="DEM")], source_id="t_src", snapshot_id=1, level="county")


def test_state_level_rows_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "load_extensions", lambda: None)
    st = make_store(tmp_path, Server())
    db.register(db.Source("zz", "zz_test", "http://example.test", "state", "csv", "daily"))
    try:
        a = st.fetch("zz_test", "http://example.test/a.csv", ext="csv", state="zz")
        st.add_rows([{"state": "zz", "as_of_date": "2026-10-30", "measure": "total", "party": "D", "count": 5},
                     {"state": "zz", "as_of_date": "2026-10-30", "measure": "total", "party": "R", "count": 4}],
                    source_id="zz_test", snapshot_id=a.snapshot_id, level="state")
        t = db.status_table(st).set_index("state")
        assert t.loc["zz", "latest_as_of"] == "2026-10-30" and t.loc["zz", "party"] == "yes" and t.loc["zz", "level"] == "state"
    finally:
        db.SOURCES.pop("zz_test")


def test_run_collect_isolates_failures_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "load_extensions", lambda: None)
    st = make_store(tmp_path, Server(status=500, body=b"boom"))

    @db.source("zz", "zz_bad", "http://example.test", kind="state", format="csv", cadence="x")
    def bad(s):
        raise RuntimeError("parser exploded")

    @db.source("zz", "zz_down", "http://example.test", kind="state", format="csv", cadence="x")
    def down(s):
        s.fetch("zz_down", "http://example.test/a.csv", ext="csv")
    try:
        out = db.run_collect(["zz"], store=st, log=lambda m: None)
        assert out["zz_bad"].startswith("ERROR") and out["zz_down"].startswith("failed")
        t = db.status_table(st).set_index("state")
        assert "FAILED" in t.loc["zz", "last_fetch"]
    finally:
        db.SOURCES.pop("zz_bad"), db.SOURCES.pop("zz_down")


# ------------------------------------------------------------------------------------------ legacy

def test_legacy_panel_rows_skip_zero_padding_and_map_measures():
    df = pd.DataFrame([
        {"state": "nc", "year": 2024, "date": "2024-10-20", "D": 10, "R": 8, "O": 5, "kind": "mail", "note": "accepted"},
        {"state": "nc", "year": 2024, "date": "2024-10-20", "D": 20, "R": 18, "O": 15, "kind": "inperson", "note": ""},
        {"state": "nc", "year": 2024, "date": "2024-10-09", "D": 0, "R": 0, "O": 0, "kind": "total", "note": ""}])
    rows = legacy._panel_rows(df, "nc")
    got = {(r["measure"], r["party"]): r["count"] for r in rows}
    assert got[("mail_accepted", "ALL")] == 23 and got[("inperson_early", "other")] == 15
    assert not any(r["measure"] == "total" for r in rows)
    fl = legacy._panel_rows(df.iloc[:1], "ok")
    assert fl[0]["measure"] == "mail_returned"


def test_register_existing_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUNDER_DATA", str(tmp_path))
    f = tmp_path / "raw" / "us" / "ev_party" / "zz" / "x.csv"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"a,b\n1,2\n")
    st = make_store(tmp_path, Server())
    a = st.register_existing("legacy_raw_zz", f, state="zz")
    b = st.register_existing("legacy_raw_zz", f, state="zz")
    assert a == b
    assert (st.root / "MANIFEST.sha256").read_text().count("\n") == 1
    assert st.db.execute("SELECT file, sha256 FROM snapshots").fetchall() == [
        ("raw/us/ev_party/zz/x.csv", db.sha256_bytes(b"a,b\n1,2\n"))]
