"""analysis/night_monitor.py: the threshold logic on synthetic inputs (no network, no ssh)."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("night_monitor", Path(__file__).resolve().parents[1] / "analysis" / "night_monitor.py")
nm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nm)

NOW = int(datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc).timestamp())
REC = 18 * 3600 + int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp())      # 18:00 UTC
NIGHT = REC + 4.5 * 3600                                                           # 22:30 UTC


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def base(now=NOW, started=True):
    r = {"now": now, "units": {}, "journal": {}, "timers": {}, "poll": {}, "feed_t": {}, "out_mtime": {}, "out": {},
         "status": {}, "status_mtime": {}, "oom": 0, "disk_pct": 50, "load": 0.5, "cpus": 2, "mem_mb": 4000}
    for u, t in nm.UNITS.items():
        fire = (NIGHT if t == nm.NIGHT_TIMER else REC) if started else None
        r["timers"][t] = {"active": "active", "next": 0 if started else (NIGHT if t == nm.NIGHT_TIMER else REC), "last": fire or 0}
        r["units"][u] = {"active": "active" if started else "inactive", "sub": "running" if started else "dead", "restarts": 0,
                         "result": "success", "enter": int(fire or 0) + 5}
        r["journal"][u] = {"traceback": 0, "errors": 0, "sample": ""}
    return r


def healthy(now=NOW):
    r = base(now)
    r["poll"] = {n: {"mtime": now - 10, "done": iso(now - 10), "feeds": {}} for n in ("snap", "kalshi", "civic")}
    r["poll"]["snap"]["feeds"] = {"ca": {"last_ok": iso(now - 20), "last_status": 200, "fails": 0}}
    r["feed_t"] = {"kalshi_books": iso(now - 60), "polymarket_books": iso(now - 60)}
    r["ws_mtime"] = now - 100
    row = {"t": iso(now - 90), "counted": 0.4, "counties_reported": 10}
    for n in nm.OUTPUTS:
        r["out_mtime"][n] = now - 30
        r["out"][n] = {"tx-sen": [row], "tail": True}
    r["poll_extra"] = {"extra": now - 10, "mac": now - 10}
    return r


def test_mac_push_stale_alerts_only_in_its_window():
    t = int(datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc).timestamp())
    r = healthy(t)
    r["poll_extra"]["mac"] = t - 900                       # 15 min without a push, inside the window
    A, _ = nm.evaluate(r, {})[:2]
    assert any("Mac push" in a for a in A)
    r = healthy(NOW)                                        # 23:30 UTC: the window opens at 23:35
    del r["poll_extra"]["mac"]
    A, _ = nm.evaluate(r, {})[:2]
    assert not any("Mac push" in a for a in A)


def test_waiting_before_start_is_silent():
    r = base(NOW - 6 * 3600, started=False)
    r["now"] = REC - 3600
    for t in r["timers"].values():
        t["next"] = REC if t is not r["timers"][nm.NIGHT_TIMER] else NIGHT
    A, W, _ = nm.evaluate(r, {})
    assert A == [] and W == []


def test_healthy_is_silent():
    A, W, info = nm.evaluate(healthy(), {})
    assert A == [] and W == []
    assert info["races"]["us_summary"]["counting"] == 1


def test_inactive_after_timer_fired_alerts_but_grace_does_not():
    r = healthy()
    r["units"]["brunder-us-live"].update(active="inactive", sub="dead")
    assert any("brunder-us-live is inactive" in a for a in nm.evaluate(r, {})[0])
    r["now"] = int(NIGHT + 60)
    r["timers"][nm.NIGHT_TIMER]["last"] = int(NIGHT)
    for k in list(r["poll"]):
        r["poll"][k]["mtime"] = r["now"] - 5
        r["poll"][k]["done"] = iso(r["now"] - 5)
    r["out_mtime"] = {k: r["now"] - 5 for k in r["out_mtime"]}
    assert not any("brunder-us-live" in a for a in nm.evaluate(r, {})[0])


def test_failed_and_restart_and_traceback():
    r = healthy()
    r["units"]["brunder-site"].update(active="failed", result="exit-code")
    r["units"]["brunder-tunnel"]["restarts"] = 2
    r["journal"]["brunder-us-national"] = {"traceback": 1, "errors": 3, "sample": "Traceback"}
    r["journal"]["brunder-kalshi-us"] = {"traceback": 0, "errors": 2, "sample": "oops"}
    A, W, _ = nm.evaluate(r, {"units": {"brunder-tunnel": {"restarts": 0, "enter": r["units"]["brunder-tunnel"]["enter"], "active": "active"}}})
    assert any("brunder-site failed" in a for a in A)
    assert any("brunder-tunnel restarted" in a for a in A)
    assert any("brunder-us-national journal" in a for a in A)
    assert any("brunder-kalshi-us journal" in w for w in W)


def test_feed_403_stale_and_404_unpublished():
    r = healthy()
    r["poll"]["snap"]["feeds"] = {
        "tx": {"last_ok": iso(NOW - 900), "last_status": 403, "fails": 5},
        "nc": {"last_ok": None, "last_status": 404, "fails": 9},
        "ga": {"last_ok": iso(NOW - 10), "last_status": 200, "fails": 1},   # one blip: fine
    }
    A, W, info = nm.evaluate(r, {})
    assert any(a.startswith("feed tx [403") for a in A)
    assert not any("nc" in a for a in A) and any("not published" in w for w in W)
    assert not any("feed ga" in a for a in A)


def test_poll_marker_and_books_and_ws_stale():
    r = healthy()
    r["poll"]["kalshi"]["mtime"] = NOW - 400
    r["poll"]["kalshi"]["done"] = iso(NOW - 400)
    r["feed_t"]["polymarket_books"] = iso(NOW - 1200)
    r["ws_mtime"] = NOW - 1500
    A, _, _ = nm.evaluate(r, {})
    assert any("kalshi recorder poll marker" in a for a in A)
    assert any("polymarket_books" in a for a in A)
    assert any("pm-ws" in a for a in A)


def test_model_staleness_and_stall():
    r = healthy()
    r["out_mtime"]["us"] = NOW - 500
    r["out"]["house"] = {"fl-01": [{"t": iso(NOW - 900), "counted": 0.2}], "tail": True}
    A, W, _ = nm.evaluate(r, {})
    assert any("timeline_tail.json not written for 500" in a for a in A)
    assert any("newest timeline row" in a for a in A)
    prev = {"races": {"us": {"tx-sen": {"key": [0.4, 10], "since": NOW - 2400}}}}
    _, W, _ = nm.evaluate(healthy(), prev)
    assert any("tx-sen: counted unchanged" in w for w in W)


def test_vm_resources():
    r = healthy()
    r.update(disk_pct=91, load=6.0, mem_mb=100, oom=1)
    A, _, _ = nm.evaluate(r, {})
    assert len(A) == 4


def test_public_and_browser():
    pub = {"elections.json": {"status": 200, "json_ok": True}, "us2026/timeline_tail.json": {"status": 200, "json_ok": True, "fresh": True, "age_s": 900},
           "summary.json": {"status": 502, "error": ""}}
    A, _ = nm.evaluate_public(pub)
    assert len(A) == 2
    assert nm.evaluate_browser({"console_errors": [], "live_age_s": 40})[0] == []
    assert nm.evaluate_browser({"console_errors": ["boom"], "live_age_s": 400})[0].__len__() == 2
    assert nm.evaluate_browser({"console_errors": [], "live_age_s": None, "live_text": "Reconnecting…"})[0]


def test_parse_remote_roundtrip():
    txt = "\n".join([
        "NOW 100", "U brunder-site active running 0 success 90", "J brunder-site 0 2 some error",
        "T brunder-x.timer active 200 0", "OOM 0", "DF 54% 1000", "LOAD 0.1 0.2 0.3 2 6000", "TUNNEL https://a-b.trycloudflare.com",
        'PJ snap 99 {"done": "2026-11-03T00:00:00+00:00", "feeds": {}}', "F kalshi_books 2026-11-03T00:00:00+00:00", "WS 1.5",
        "O us 95", 'OJ us {"a": [{"t": "2026-11-03T00:00:00Z", "counted": 1}]}', 'OS house 90 {"errors": []}', "garbage line"])
    r = nm.parse_remote(txt)
    assert r["units"]["brunder-site"]["sub"] == "running" and r["journal"]["brunder-site"]["sample"] == "some error"
    assert r["tunnel"].endswith("trycloudflare.com") and r["cpus"] == 2 and r["poll"]["snap"]["mtime"] == 99
    assert nm.races_of(r["out"]["us"])["a"]["counted"] == 1
    assert r["status"]["house"] == {"errors": []}


def test_remote_script_is_read_only():
    s = nm.remote_script()
    for bad in ("restart", "systemctl start", "systemctl stop", "rm ", " > /", " kill ", "systemctl enable", "git "):
        assert bad not in s
    json.dumps(nm.TH)
