"""brunder.snap.run: conditional requests, per-feed interval, 429 backoff, fetch metadata in the index (local server)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brunder import snap

HITS: dict[str, list[tuple[float, bool]]] = {}
STATE = {"a": 1}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        cond = bool(self.headers.get("If-None-Match") or self.headers.get("If-Modified-Since"))
        HITS.setdefault(path, []).append((datetime.now(timezone.utc).timestamp(), cond))
        if path == "/c":
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.end_headers()
            return
        if path == "/d":
            self.send_response(403)
            self.end_headers()
            return
        body, etag, lm = {"/a": (json.dumps({"v": STATE["a"]}).encode(), f'"a{STATE["a"]}"', "Wed, 04 Nov 2026 01:00:00 GMT"),
                          "/b": (b'{"static": 1}', '"b1"', "Wed, 04 Nov 2026 00:00:00 GMT")}[path]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", lm)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_conditional_fast_backoff_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "MIN_INTERVAL", 0.2)
    HITS.clear()
    STATE["a"] = 1
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def bump():
        import time
        time.sleep(1.1)
        STATE["a"] = 2
    threading.Thread(target=bump, daemon=True).start()
    until = datetime.now(timezone.utc) + timedelta(seconds=3.0)
    asyncio.run(snap.run("t", [f"{base}/a", f"{base}/b", f"{base}/c", f"{base}/d"], [], interval=1.0, until=until,
                         out_root=tmp_path, intervals=snap.feed_intervals([f"{base}/a=0.2", f"{base}/d=0.2"])))
    srv.shutdown()
    a, b, c, d = HITS["/a"], HITS["/b"], HITS["/c"], HITS["/d"]
    assert len(a) >= 10 and 2 <= len(b) <= 4 and len(c) <= 2        # /a fast, /b every 1 s, /c backed off (≥ 1 s, then ≥ 2 s)
    assert 2 <= len(d) <= 5                                          # 403: backed off from 0.2 s (0.2–0.4, 0.4–0.8, …), not 15 polls
    assert not b[0][1] and all(x[1] for x in b[1:])                  # later GETs of /b are conditional
    ix = [json.loads(x) for x in (tmp_path / "t" / "index.jsonl").read_text().splitlines()]
    keys = [r["key"] for r in ix]
    assert keys.count("localhost") == 0 and len(ix) == 3             # a v1, b, a v2: the 304s wrote nothing
    ra = [r for r in ix if r["key"].endswith("_a")]
    assert len(ra) == 2 and ra[0]["etag"] == '"a1"' and ra[1]["etag"] == '"a2"'
    assert ra[0]["last_modified"] == "Wed, 04 Nov 2026 01:00:00 GMT" and "fetched" in ra[0] and ra[0]["http_ms"] >= 0
    assert datetime.fromisoformat(ra[0]["fetched"]) >= datetime.fromisoformat(ra[0]["t"])
    st = json.loads((tmp_path / "t" / ".poll" / "snap.json").read_text())["feeds"]
    ka, kd = (next(k for k in st if k.endswith(x)) for x in ("_a", "_d"))
    assert st[ka]["last_ok"] and st[ka]["last_status"] in (200, 304) and st[ka]["interval"] == 0.2
    assert st[kd]["last_ok"] is None and st[kd]["last_status"] == 403 and st[kd]["fails"] >= 2


def test_feed_intervals_and_backoff_jitter(monkeypatch):
    assert snap.feed_intervals(["https://x.gov/=60", "https://y.gov/a?b=c=5"]) == [("https://x.gov/", 60.0),
                                                                                  ("https://y.gov/a?b=c", 10.0)]
    f = snap.Feed("k", "u", 30)
    waits = [f.failed(0.0, 30) for _ in range(8)]
    assert all(30 <= w <= snap.BACKOFF_CAP for w in waits) and waits[-1] >= snap.BACKOFF_CAP / 2
    assert waits[3] >= 30 * 2 ** 4 * 0.5
    f.done(0.0, 30, type("R", (), {"status_code": 304, "headers": {}})())
    assert f.fails == 0 and f.last_ok is not None
