"""brunder.civic (civicAPI, civicapi.org: secondary US midterms source): parser on real race documents, county mapping,
the recorder (polling, change detection, backoff, snapshot layout read back by brunder.us.Archive), the civicAPI configs,
the dashboard metadata's attribution, and the cross-validation helpers of analysis/us_civic.py."""
import asyncio
import copy
import gzip
import json
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from brunder import civic, snap, us

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us"
CONFIGS = sorted((ROOT / "configs" / "us2026_civic").glob("*.toml"))
sys.path.insert(0, str(ROOT / "analysis"))


def doc(name: str) -> dict:
    return json.loads(gzip.decompress((FIX / f"{name}.json.gz").read_bytes()))


@pytest.fixture(autouse=True)
def _mark(monkeypatch):
    """snap.mark (the live model's poll marker) belongs to the recorder-latency work; the recorder only calls it."""
    calls = []
    monkeypatch.setattr(snap, "mark", lambda out, name, nxt, interval: calls.append((name, nxt, interval)), raising=False)
    return calls


# ------------------------------------------------------------------ parser

def test_parse_real_filled_race_matches_official_baseline_exactly():
    """Iowa 2024 president as civicAPI serves it (no fips: names map): all 99 counties equal the MEDSL county baseline."""
    df = civic.parse_race(doc("civic_ia_pres_2024"), "ia", "pres")
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 99 and set(c["unit_id"]) == set(us.counties("ia")["unit_id"])
    g = c.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum")
    base = pd.read_csv(ROOT / "data_public" / "us2026_county_baseline.csv", comment="#").set_index("unit_id")
    assert (g["DEM"] == base.loc[g.index, "pres24_dem"]).all() and (g["REP"] == base.loc[g.index, "pres24_rep"]).all()
    s = df[df["level"] == "state"]
    assert s.groupby("party")["votes"].sum()["DEM"] == g["DEM"].sum()
    assert (c["units"] == civic.COMPLETE_AT).all() and (c["reporting"] == 100).all()


def test_parse_2026_race_every_county_maps_and_zero_votes():
    df = civic.parse_race(doc("civic_sc_sen_2026"), "sc", "sen")
    c = df[df["level"] == "county"]
    assert set(c["unit_id"]) == set(us.counties("sc")["unit_id"]) and len(set(c["unit_id"])) == 46
    assert c["votes"].sum() == 0 and set(c["party"]) == {"DEM", "REP", "CON", "LIB"}
    assert c[c["party"] == "REP"]["candidate"].iloc[0] == "Darline Graham"
    assert list(df.columns) == us.COLS


def test_name_fallback_maps_county_without_fips():
    d = doc("civic_sc_sen_2026")
    assert "fips" not in d["region_results"]["mccormick"]            # live data: this county has no fips, it maps by name
    d2 = copy.deepcopy(d)
    for v in d2["region_results"].values():
        v.pop("fips", None)                                          # every county by name
    c = civic.parse_race(d2, "sc", "sen")
    assert c[c["level"] == "county"]["unit_id"].nunique() == 46


def test_towns_are_not_counties_and_counties_are_found():
    d = doc("civic_nh_gov_2026")
    types = pd.Series([v["type"] for v in d["region_results"].values()]).value_counts().to_dict()
    assert types == {"Town": 320, "County": 10}
    df = civic.parse_race(d, "nh", "gov")
    c = df[df["level"] == "county"]
    assert set(c["unit_id"]) == set(us.counties("nh")["unit_id"]) and len(set(c["unit_id"])) == 10


def test_unmapped_county_is_skipped_not_fatal(capsys):
    d = doc("civic_sc_sen_2026")
    d["region_results"]["atlantis"] = {"name": "Atlantis", "type": "County", "fips": "45999", "percent_reporting": 100,
                                       "candidates": [{"name": "X", "party": "Democratic", "votes": 5}]}
    c = civic.parse_race(d, "sc", "sen")
    assert len(set(c[c["level"] == "county"]["unit_id"])) == 46
    assert "atlantis (45999)" in capsys.readouterr().out


def test_test_data_flag_gives_no_rows():
    d = doc("civic_sc_sen_2026")
    d["test_data"] = True
    assert civic.parse_race(d, "sc", "sen").empty


def test_parse_feed_dispatch_and_both_races():
    sen, gov = doc("civic_sc_sen_2026"), doc("civic_sc_sen_2026")
    df = us.parse_feed("civic_sc", {"sen": json.dumps(sen).encode(), "gov": json.dumps(gov).encode()})
    assert set(df["race"]) == {"sen", "gov"}
    assert us.parse_feed("civic_ia", {}).empty
    assert us.FEEDS["civic_ia"] == {"sen": "civic_87151", "gov": "civic_87152"}


def test_house_rows():
    d = {"region_results": {"az-01": {"name": "AZ-1", "percent_reporting": 50,
                                      "candidates": [{"name": "A", "party": "Democratic", "votes": 3},
                                                     {"name": "B", "party": "Republican", "votes": 4}]},
                           "ak-al": {"name": "AK", "candidates": [{"name": "C", "party": "Republican", "votes": 1}]}}}
    h = civic.parse_house(d)
    assert list(h["district"]) == ["az-01", "az-01", "ak-al"] and h["votes"].sum() == 8


def test_race_table_is_consistent():
    assert civic.HOUSE_ID == 87524 and len(civic.TARGET) == 17
    assert {k[:2] for k in civic.TARGET} == {"sc", "ia", "mi", "wi", "nh", "nv", "ny", "co", "ks", "me"}
    assert {k[:2] for k in civic.XVAL} == {"tx", "ga", "nc", "fl", "oh", "pa", "az", "mn"}
    ids = [v for v in civic.RACES.values() if v is not None]
    assert len(ids) == len(set(ids))
    for st in civic.STATES:                                      # hooks in brunder.us for the states it does not know
        assert us.STATES[st] == civic.STATES[st] and us.POLL_CLOSE[st] == civic.POLL_CLOSE[st]


# ------------------------------------------------------------------ recorder

def _race(votes=0, last="2026-11-03T23:00:00.000Z", key="sc-sen", padding=0):
    d = doc("civic_sc_sen_2026")
    d["last_updated"] = last
    d["candidates"][0]["votes"] = votes
    if padding:
        d["pad"] = "x" * padding
    return d


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=civic.UA)


def test_recorder_saves_on_change_with_index_fields(tmp_path):
    seen = {"n": 0, "ua": set(), "paths": []}
    bodies = [_race(0), _race(0), _race(5, "2026-11-03T23:01:00.000Z", padding=70_000)]

    def handler(req):
        seen["ua"].add(req.headers["user-agent"])
        seen["paths"].append(req.url.path)
        b = bodies[min(seen["n"], 2)]
        seen["n"] += 1
        return httpx.Response(200, json=b, headers={"etag": f'"e{seen["n"]}"', "last-modified": "Tue, 03 Nov 2026 23:00:00 GMT"})

    async def go():
        rec = civic.Recorder("t", {"sc-sen": 87409}, 30.0, tmp_path)
        async with _client(handler) as web:
            return [await rec.poll(web, "sc-sen", datetime.now(timezone.utc)) for _ in range(3)]
    res = asyncio.run(go())
    assert res == ["saved", "same", "saved"]
    assert seen["ua"] == {"brunder/0.1 (research; recorder)"} and set(seen["paths"]) == {"/api/v2/race/87409"}
    files = sorted((tmp_path / "t" / "civic_87409").iterdir())
    assert [f.suffix for f in files] == [".json", ".gz"]             # the 70 KB body is gzipped
    lines = [json.loads(x) for x in (tmp_path / "t" / "index.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["key"] == "civic_87409" and lines[0]["race"] == "sc-sen"
    for k in ("t", "fetched", "http_ms", "last_modified", "etag", "sha256", "bytes", "gzip", "civic_last_updated"):
        assert k in lines[0], k
    assert lines[1]["gzip"] is True and lines[1]["civic_last_updated"] == "2026-11-03T23:01:00.000Z"
    polls = [json.loads(x) for x in (tmp_path / "t" / "civic_polls.jsonl").read_text().splitlines()]
    assert [p["result"] for p in polls] == ["saved", "same", "saved"]
    assert not list((tmp_path / "t" / ".tmp").iterdir())            # temp files renamed away


def test_recorder_output_is_read_by_the_archive_and_parsed(tmp_path):
    async def go():
        rec = civic.Recorder("us_midterms_2026", {"sc-sen": 87409}, 30.0, tmp_path)
        t0 = datetime(2026, 11, 4, 0, 0, 5, tzinfo=timezone.utc)
        for i, v in enumerate((0, 1234)):
            body = _race(v, f"t{i}")
            async with _client(lambda r, b=body: httpx.Response(200, json=b)) as web:
                await rec.poll(web, "sc-sen", t0 + pd.Timedelta(seconds=30 * i))
    asyncio.run(go())
    arc = us.Archive(tmp_path / "us_midterms_2026")
    arc.refresh()
    assert arc.keys("civic_87409") == ["civic_87409"]
    t = pd.Timestamp("2026-11-04T00:00:40Z")
    rows = us.parse_feed("civic_sc", us.feed_blobs(arc, us.feed_files(arc, "civic_sc", t)))
    st = rows[rows["level"] == "state"]
    assert st["votes"].sum() == 1234 and set(rows["race"]) == {"sen"}
    early = us.parse_feed("civic_sc", us.feed_blobs(arc, us.feed_files(arc, "civic_sc", pd.Timestamp("2026-11-04T00:00:10Z"))))
    assert early[early["level"] == "state"]["votes"].sum() == 0


def test_recorder_errors_back_off_and_do_not_save(tmp_path, capsys):
    codes = iter([500, 429, 200])

    def handler(req):
        c = next(codes)
        if c == 200:
            return httpx.Response(200, json=_race(1))
        return httpx.Response(c, headers={"retry-after": "90"} if c == 429 else {})

    async def go():
        rec = civic.Recorder("t", {"sc-sen": 87409}, 30.0, tmp_path)
        async with _client(handler) as web:
            a = await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
            f1 = rec.fails["sc-sen"]
            b = await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
            f2, nb = rec.fails["sc-sen"], rec.not_before["sc-sen"]
            c = await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
            return a, b, c, f1, f2, nb, rec.fails
    a, b, c, f1, f2, nb, fails = asyncio.run(go())
    assert a.startswith("error") and b.startswith("error") and c == "saved"
    assert (f1, f2) == (1, 2) and "sc-sen" not in fails                  # success clears the failure count
    import time
    assert nb - time.monotonic() > 60                                    # Retry-After 90 s honoured (≥ the 60 s backoff)
    assert "HTTPStatusError" in capsys.readouterr().out


def test_recorder_rejects_non_race_documents(tmp_path):
    async def go():
        rec = civic.Recorder("t", {"sc-sen": 87409}, 30.0, tmp_path)
        async with _client(lambda r: httpx.Response(200, json={"error": "snapshot not found!"})) as web:
            return await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
    assert asyncio.run(go()).startswith("error")
    assert not (tmp_path / "t" / "index.jsonl").exists()


def test_recorder_warns_once_when_the_id_is_another_race(tmp_path, capsys):
    other = _race(0)
    other["province"] = "IA"

    async def go():
        rec = civic.Recorder("t", {"sc-sen": 87409}, 30.0, tmp_path)
        async with _client(lambda r: httpx.Response(200, json=other)) as web:
            await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
            other["last_updated"] = "x"
            await rec.poll(web, "sc-sen", datetime.now(timezone.utc))
    asyncio.run(go())
    out = capsys.readouterr().out
    assert out.count("is not what we resolved") == 1 and "province IA" in out


def test_record_loop_spreads_requests_marks_and_resolves(tmp_path, _mark):
    """Two races + one unresolved: requests are spread over the interval, never faster than it per race; the marker is
    written after each cycle with next = the next cycle's start; the unresolved race is searched and then polled."""
    times: list[tuple[str, float]] = []
    import time

    def handler(req):
        p = req.url.path
        if p.endswith("/race/search"):
            q = req.url.params["query"]
            r = {"id": 4242, "type": "Governor", "province": "AZ", "election_type": "Governor", "election_name": "Arizona Governor",
                 "election_date": "2026-11-03T05:00:00.000Z"}
            return httpx.Response(200, json={"races": [r] if q == "Arizona Governor" else []})
        times.append((p, time.monotonic()))
        d = _race(0)
        if p.endswith("/4242"):
            d.update(province="AZ", election_type="Governor")
        return httpx.Response(200, json=d)

    until = datetime.now(timezone.utc) + pd.Timedelta(seconds=2.2)
    asyncio.run(civic.record("t", {"sc-sen": 87409, "sc-gov": 87410, "az-gov": None}, interval=1.0, until=until,
                             out_root=tmp_path, transport=httpx.MockTransport(handler)))
    by = {}
    for p, t in times:
        by.setdefault(p, []).append(t)
    assert set(by) == {"/api/v2/race/87409", "/api/v2/race/87410", "/api/v2/race/4242"}
    for ts in by.values():
        assert len(ts) >= 2 and min(np.diff(ts)) >= 0.9                  # one request per race per interval
    first = sorted(t for ts in by.values() for t in ts)[:2]
    assert first[1] - first[0] >= 0.2                                     # spread, not a burst
    assert _mark and all(c[0] == "civic" and c[2] == 1.0 for c in _mark)
    assert all(b[1] > a[1] for a, b in zip(_mark, _mark[1:]))


# ------------------------------------------------------------------ configs

def _validated_keys():
    import us_civic as X
    return [k for k in X.CONFIG_KEYS if k[:2] in X.VALIDATED + X.PENDING]


def test_config_set_is_exactly_the_validated_states():
    import us_civic as X
    names = {p.name for p in CONFIGS}
    keys = _validated_keys()
    assert len(keys) == 14 and {k[:2] for k in keys} == set(X.VALIDATED + X.PENDING)
    assert {n for n in names if n.endswith("_v1.toml")} == {f"us2026_civic_{k.replace('-', '_')}_v1.toml" for k in keys}
    for key in keys:
        assert f"us2026_civic_{key.replace('-', '_')}_v2.toml" in names
    assert {n for n in names if n.endswith("_v3.toml")} >= {f"us2026_civic_{k.replace('-', '_')}_v3.toml" for k in keys
                                                           if k[:2] != "co"}
    # states that failed validation have no config (NV: counted primary 10 % low; ME: 6 of 16 counties; NH: unconfirmed)
    assert not {k[:2] for k in X.CONFIG_KEYS if f"us2026_civic_{k.replace('-', '_')}_v1.toml" not in names} - {"nv", "me", "nh"}


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_each_config_is_valid(path):
    c = us.load_config(path)
    E, st = c["election"], c["election"]["state"]
    assert c["id"] == path.stem and E["feed"] == f"civic_{st}" and E["feed"] in us.FEEDS
    assert E["race"] in us.FEEDS[E["feed"]] and E["source"] == "civicAPI (civicapi.org)" and E["source_url"] == "https://civicapi.org"
    assert E["race_id"] == (f"{st}-{E['race']}-civic" if st in ("sc", "oh") else f"{st}-{E['race']}")
    assert E["poll_close_utc"] == us.POLL_CLOSE[st] and E["a"]["party"] == "DEM" and E["b"]["party"] == "REP"
    pri = us.race_prior(c, us.load_priors())
    assert pri is not None and len(pri) == len(us.counties(st)) and pri["V"].gt(0).all()
    assert path.read_text().splitlines()[0].startswith("# US midterms 2026-11-03")
    assert c["count"]["f_complete"] == 0.98 and not c["count"]["use_outstanding"]
    assert (path.name.endswith("_v1.toml")) == (us.config_version(c) == 1)
    # candidates: Democrat and Republican as the registry names them
    reg = next(r for r in json.loads(us.RACES_REGISTRY.read_text())["races"]
               if r["race_key"] == f"{st}-{E['race']}")
    assert E["a"]["name"] == reg["dem_side"]["name"] and E["b"]["name"] == reg["candidates"]["R"]
    assert c["markets"]["polymarket"]["event"] == reg["pm"]["event"]


def test_versions_follow_the_poll_rules():
    for key in _validated_keys():
        stem = ROOT / "configs" / "us2026_civic" / f"us2026_civic_{key.replace('-', '_')}"
        v1, v2 = (us.load_config(Path(f"{stem}_v{i}.toml")) for i in (1, 2))
        assert "swing_prior_mean" not in v1["model"] and v1["election"] == v2["election"]
        if "poll_avg" in v2["prior"]:
            assert v2["model"]["swing_prior_sd"] == v2["prior"]["poll_sd"]
            v3 = us.load_config(Path(f"{stem}_v3.toml"))
            assert v3["model"]["count_scaled_terms"] and v3["model"]["swing_prior_sd"] == us.poll_sd_by_n(v2["prior"]["poll_n"])
            assert v3["model"]["swing_prior_mean"] == v2["model"]["swing_prior_mean"]
        assert set(us._sibling_versions(v1)) >= {"v2"}


def test_configs_state_the_unrehearsed_live_behaviour():
    for p in CONFIGS:
        if p.name.endswith("_v1.toml"):
            assert "SECONDARY SOURCE, LIVE BEHAVIOUR UNREHEARSED" in p.read_text()


def test_pending_states_say_so_and_validated_ones_do_not():
    import us_civic as X
    for p in CONFIGS:
        pending = f"_{p.stem.split('_')[2]}_" in tuple(f"_{x}_" for x in X.PENDING)
        assert ("VALIDATION PENDING" in p.read_text()) == pending, p.name
        assert "LIVE BEHAVIOUR UNREHEARSED" in p.read_text(), p.name


def test_civic_priors_are_the_2024_president_baseline():
    pri = pd.read_csv(us.CIVIC_PRIORS, comment="#")
    assert set(pri["source"]) == {f"{st}_pres_2024" for st in civic.STATES}
    assert not (set(pri["source"]) & set(pd.read_csv(us.PUBLIC_PRIORS, comment="#")["source"]))
    for st in civic.STATES:
        d = pri[pri["source"] == f"{st}_pres_2024"]
        assert set(d["unit_id"]) == set(us.counties(st)["unit_id"]) and (d["total"] >= d["dem"] + d["rep"]).all()


# ------------------------------------------------------------------ the model on civicAPI rows (synthetic filled copy)

def _filled(cfg: dict, pri: pd.DataFrame, share_shift: float, frac: float, complete: set[str]) -> pd.DataFrame:
    """A synthetic 'filled' copy of the SC document: every county has frac × its expected votes at the prior's share
    plus a shift; counties in `complete` report 100 %, the rest 60 %."""
    d = doc("civic_sc_sen_2026")
    by = pri.set_index("unit_id")
    for v in d["region_results"].values():
        uid = f"us-sc-{v['fips']}" if v.get("fips") else "us-sc-45065"
        n = by.loc[uid, "V"] * frac
        s = float(np.clip(by.loc[uid, "s0"] + share_shift, 0.02, 0.98))
        v["percent_reporting"] = 100 if uid in complete else 60
        for c in v["candidates"]:
            c["votes"] = round(n * s) if c["party"] == "Democratic" else round(n * (1 - s)) if c["party"] == "Republican" else 0
    return civic.parse_race(d, "sc", "sen")


def test_model_runs_on_civic_rows_and_recovers_a_known_swing():
    cfg = us.load_config(ROOT / "configs" / "us2026_civic" / "us2026_civic_sc_sen_v1.toml")
    pri = us.race_prior(cfg, us.load_priors())
    assert pri is not None and len(pri) == 46
    empty = civic.parse_race(doc("civic_sc_sen_2026"), "sc", "sen")
    res0, units0 = us.project(empty, cfg, pri)
    assert units0["n"].sum() == 0 and abs(res0["share_hat"] - us.baseline_share(pri)) < 1e-6
    big = set(pri.sort_values("V")["unit_id"].tail(30))
    rows = _filled(cfg, pri, 0.04, 0.5, big)
    res, units = us.project(rows, cfg, pri)
    assert units["complete"].sum() == 30 and res["counted_frac"] > 0.3
    assert res["share_hat"] == pytest.approx(us.baseline_share(pri) + 0.04, abs=0.01)
    assert res["raw"] == pytest.approx(us.baseline_share(pri) + 0.04, abs=0.02)


def test_meta_carries_the_attribution_and_labels_the_races():
    cfgs = [us.load_config(p) for p in CONFIGS if p.name.endswith("_v1.toml")]
    meta = us.meta(cfgs, {c["election"]["race_id"]: True for c in cfgs}, phase="live")
    assert all(r["source"] == {"name": "civicAPI (civicapi.org)", "url": "https://civicapi.org", "role": "secondary"}
               for r in meta["races"])
    src = [s for s in meta["sources"] if "civicAPI" in s["name"]]
    assert len(src) == 1 and src[0]["url"] == "https://civicapi.org" and "attribution" in src[0]["licence"]
    assert not any(s["name"].startswith("Official results feeds") for s in meta["sources"])
    assert any("civicAPI" in n for n in meta["notes"])
    assert meta["poll_close_by_region"]["ia"] == "2026-11-04T03:00:00Z" and "ks" in meta["poll_close_by_region"]
    off = us.meta([us.load_config(ROOT / "configs" / "us2026_sc_sen_v1.toml")], {"sc-sen": True}, phase="live")
    assert "source" not in off["races"][0]


# ------------------------------------------------------------------ analysis/us_civic.py

def test_candidate_check_against_the_registry():
    import us_civic as X
    d = doc("civic_sc_sen_2026")
    assert X.check_candidates(d, X.registry_race("sc-sen")) == []
    d["candidates"][1]["name"] = "Lindsey Graham"
    assert any("registry R = 'Darline Graham'" in f for f in X.check_candidates(d, X.registry_race("sc-sen")))
    assert X.same_person("John E. Sununu", "John Sununu") and not X.same_person("Robert Charles", "Bobby Charles")
    dup = doc("civic_sc_sen_2026")
    dup["candidates"].append({"name": "Other", "party": "Republican", "votes": 0})
    assert any(f.startswith("REP: 2 candidates") for f in X.check_candidates(dup, X.registry_race("sc-sen")))


def test_lag_and_compare_on_synthetic_series():
    import us_civic as X
    t = lambda s: pd.Timestamp("2026-11-04T01:00:00Z") + pd.Timedelta(seconds=s)
    off = pd.DataFrame({"t": [t(0), t(30), t(60), t(0), t(30), t(60)], "unit_id": ["a"] * 3 + ["b"] * 3,
                        "dem": [0, 10, 20, 0, 0, 5], "rep": [0, 10, 20, 0, 0, 5]})
    civ = pd.DataFrame({"t": [t(10), t(40), t(70), t(10), t(40), t(70)], "unit_id": ["a"] * 3 + ["b"] * 3,
                        "dem": [0, 0, 20, 0, 0, 5], "rep": [0, 0, 20, 0, 0, 6]})
    lag = X.lag_table(civ, off)
    a = lag[lag["unit_id"] == "a"].set_index("votes")["lag_s"]
    assert a[20.0] == 40 and a[40.0] == 10                               # 20 votes: official t=30, civic t=70
    row, counties, _ = X.compare_race("x-gov", civ, off)
    assert row["final_civic"] == 51 and row["final_official"] == 50 and row["lag_median_s"] > 0
    assert row["counties"] == 2 and row["counties_exact"] == 1 and counties.index[0] == "b"


def test_compare_reads_recorded_archives(tmp_path):
    """End to end on a recorder directory: civicAPI Georgia snapshots against Georgia's official feed snapshots."""
    import us_civic as X
    snapdir = tmp_path / "snap"
    assert X.compare(snapdir, tmp_path / "out").empty                   # nothing recorded: reports and returns


# ------------------------------------------------------------------ live unit and rehearsal replay

def _unit_civic_configs() -> list[Path]:
    text = (ROOT / "deploy" / "brunder-us-civic-live.service").read_text()
    return [ROOT / p for p in re.findall(r"configs/us2026_civic/us2026_civic_\w+_v1\.toml", text)]


def test_live_unit_loads_every_civic_v1_and_its_siblings():
    unit = _unit_civic_configs()
    assert sorted(p.name for p in unit) == sorted(p.name for p in CONFIGS if p.name.endswith("_v1.toml")) and len(unit) == 14
    cfgs, versions = us.load_races(unit)
    assert len(cfgs) == 14 and len({c["election"]["race_id"] for c in cfgs}) == 14
    for c in cfgs:
        have = versions[c["election"]["race_id"]]
        assert {"v2"} <= set(have) and all(Path(v["_path"]).parent.name == "us2026_civic" for v in have.values())
        assert ("v3" in have) == (c["election"]["state"] != "co")
    timers = [(ROOT / "deploy" / f).read_text() for f in ("brunder-snap-us.timer", "brunder-civic-us.timer")]
    assert all("OnCalendar=2026-11-03 18:00:00 UTC" in t for t in timers)


def test_replay_feed_writes_civic_keys_the_recorder_uses(tmp_path):
    import us_night_replay as R
    R.feed_civic(tmp_path / "snap", night=40)
    arc = us.Archive(tmp_path / "snap")
    arc.refresh()
    keys = {civic.RACES[f"{c['election']['state']}-{c['election']['race']}"] for c in us.load_races(R.CONFIGS)[0]
            if c["election"].get("source")}
    assert {k for k in arc._files} == {f"civic_{i}" for i in keys} | {"civic_87524"} and len(keys) == 14
    last = pd.Timestamp("2026-11-04T01:00:00Z")
    rows = us.parse_feed("civic_ia", us.feed_blobs(arc, us.feed_files(arc, "civic_ia", last)))
    assert rows[rows["level"] == "county"]["votes"].sum() > 0 and set(rows["race"]) == {"sen", "gov"}
    house = civic.parse_house(arc.read(arc.at("civic_87524", last)))
    assert house["district"].nunique() == 435 and "ak-al" in set(house["district"])
