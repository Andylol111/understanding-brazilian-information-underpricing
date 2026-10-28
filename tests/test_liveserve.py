import asyncio
import gzip
import json
import os
import socket
import threading
import time

import pytest

from brunder import liveserve

PAGES = liveserve.PAGES_ORIGIN


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "data"
    (d / "us2026" / "units").mkdir(parents=True)
    (d / "us2026" / "timeline.json").write_text(json.dumps({"tx-sen": [{"t": "2026-11-04T01:00:00Z"}] * 200}))
    (d / "us2026" / "units" / "tx-sen.json").write_text('{"race_id":"tx-sen"}')
    (d / "elections.json").write_text("[]")
    (d / "nights.json").write_text("[]")                                   # in the root but not whitelisted
    (d / "br2022r1").mkdir()
    (d / "br2022r1" / "meta.json").write_text("{}")                        # another election: not served
    (tmp_path / "secret.json").write_text('{"key":"x"}')                   # outside the root
    os.symlink(tmp_path / "secret.json", d / "us2026" / "leak.json")      # symlink out of the root
    return d


@pytest.fixture
def server(root):
    return liveserve.LiveServer(liveserve.Files(root, ["us2026"], ["elections.json"]), [PAGES, "http://localhost:8000"])


def split(resp: bytes):
    head, _, body = resp.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    return int(lines[0].split(" ")[1]), headers, body


def get(server, path, method="GET", **h):
    status, out = server.handle(method, path, {k.replace("_", "-").lower(): v for k, v in h.items()})
    s, headers, body = split(out)
    assert s == status
    return status, headers, body


@pytest.mark.parametrize("path", [
    "/", "/us2026/", "/us2026", "/nights.json", "/br2022r1/meta.json", "/../secret.json", "/us2026/../secret.json",
    "/us2026/..%2f..%2fsecret.json", "/%2e%2e/secret.json", "/us2026/leak.json", "/us2026//timeline.json",
    "/us2026/timeline.json.tmp", "/us2026/timeline.JSON", "//etc/passwd", "/us2026/units", "/us2026/./timeline.json",
    "/us2026\\..\\secret.json"])
def test_only_whitelisted_json_inside_root_is_served(server, path):
    status, h, body = get(server, path)
    assert status == 404 and b"key" not in body and b"tx-sen" not in body
    assert h["access-control-allow-origin"] == PAGES                       # the dashboard sees the 404, not a CORS error


def test_serves_live_files_with_query_strings(server):
    assert get(server, "/us2026/timeline.json?ts=1")[0] == 200
    assert get(server, "/us2026/units/tx-sen.json")[0] == 200
    assert get(server, "/elections.json")[0] == 200
    assert get(server, "/us2026/missing.json")[0] == 404


def test_gzip_when_accepted_and_identity_otherwise(server, root):
    raw = (root / "us2026" / "timeline.json").read_bytes()
    s, h, body = get(server, "/us2026/timeline.json", accept_encoding="gzip, deflate, br")
    assert s == 200 and h["content-encoding"] == "gzip" and gzip.decompress(body) == raw
    assert int(h["content-length"]) == len(body) < len(raw)
    assert h["content-type"].startswith("application/json") and h["cache-control"] == "public, max-age=10"
    assert "accept-encoding" in h["vary"].lower()
    s, h, body = get(server, "/us2026/timeline.json")
    assert "content-encoding" not in h and body == raw
    assert "content-encoding" not in get(server, "/us2026/timeline.json", accept_encoding="gzip;q=0")[1]


def test_gzip_is_computed_once_per_change(server, root, monkeypatch):
    calls = []
    real = gzip.compress
    monkeypatch.setattr(liveserve.gzip, "compress", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        get(server, "/us2026/timeline.json", accept_encoding="gzip")
    assert len(calls) == 1
    f = root / "us2026" / "timeline.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text('{"changed":1}')
    os.replace(tmp, f)                                                     # how the model writes
    _, _, body = get(server, "/us2026/timeline.json", accept_encoding="gzip")
    assert gzip.decompress(body) == b'{"changed":1}' and len(calls) == 2


def test_etag_and_last_modified_give_304(server, root):
    s, h, body = get(server, "/us2026/timeline.json", accept_encoding="gzip")
    etag, lm = h["etag"], h["last-modified"]
    s, h2, body = get(server, "/us2026/timeline.json", accept_encoding="gzip", if_none_match=etag)
    assert s == 304 and body == b"" and h2["etag"] == etag and h2["access-control-allow-origin"] == PAGES
    assert "content-length" not in h2
    assert get(server, "/us2026/timeline.json", if_none_match=f"W/{etag}")[0] == 304   # weakened by a proxy
    assert get(server, "/us2026/timeline.json", if_none_match='"other", ' + etag)[0] == 304
    assert get(server, "/us2026/timeline.json", if_modified_since=lm)[0] == 304
    assert get(server, "/us2026/timeline.json", if_modified_since="Thu, 01 Jan 2004 00:00:00 GMT")[0] == 200
    assert get(server, "/us2026/timeline.json", if_none_match='"stale"', if_modified_since=lm)[0] == 200
    # same content rewritten: still 304; new content: 200 with a new ETag
    f = root / "us2026" / "timeline.json"
    raw = f.read_bytes()
    time.sleep(0.01)
    f.write_bytes(raw)
    assert get(server, "/us2026/timeline.json", accept_encoding="gzip", if_none_match=etag)[0] == 304
    f.write_text("{}")
    s, h3, _ = get(server, "/us2026/timeline.json", accept_encoding="gzip", if_none_match=etag)
    assert s == 200 and h3["etag"] != etag


def test_cors_headers_and_preflight(server):
    _, h, _ = get(server, "/us2026/timeline.json", origin=PAGES)
    assert h["access-control-allow-origin"] == PAGES and "origin" in h["vary"].lower()
    _, h, _ = get(server, "/us2026/timeline.json", origin="http://localhost:8000")
    assert h["access-control-allow-origin"] == "http://localhost:8000"
    _, h, _ = get(server, "/us2026/timeline.json", origin="https://evil.example")
    assert h["access-control-allow-origin"] == PAGES                       # the browser refuses it
    s, h, body = get(server, "/us2026/timeline.json", "OPTIONS", origin=PAGES,
                     access_control_request_method="GET", access_control_request_headers="if-none-match")
    assert s == 204 and body == b"" and h["access-control-allow-origin"] == PAGES
    assert h["access-control-allow-methods"] == "GET, HEAD, OPTIONS"
    assert "if-none-match" in h["access-control-allow-headers"].lower()
    assert get(server, "/nights.json", "OPTIONS", origin=PAGES)[0] == 404
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        s, h, _ = get(server, "/us2026/timeline.json", m)
        assert s == 405 and h["allow"] == "GET, HEAD, OPTIONS"


def test_head_has_headers_but_no_body(server):
    s, h, body = get(server, "/us2026/timeline.json", "HEAD", accept_encoding="gzip")
    assert s == 200 and body == b"" and int(h["content-length"]) > 0 and h["content-encoding"] == "gzip"


# ---------------------------------------------------------------- over a real socket

@pytest.fixture
def running(root):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    loop = asyncio.new_event_loop()
    srv = liveserve.LiveServer(liveserve.Files(root, ["us2026"], ["elections.json"]), [PAGES], max_connections=2)
    started = threading.Event()
    stop = asyncio.Event()

    async def run():
        server = await asyncio.start_server(srv.connection, "127.0.0.1", port, limit=liveserve.HEADER_LIMIT)
        started.set()
        async with server:
            await stop.wait()

    t = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    t.start()
    started.wait(5)
    yield port
    loop.call_soon_threadsafe(stop.set)
    t.join(5)


def raw_request(port, data: bytes) -> bytes:
    """Send, read until the server closes; a reset (server closed with unread input) ends the read too."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        out = b""
        while True:
            try:
                chunk = s.recv(65536)
            except ConnectionResetError:
                return out
            if not chunk:
                return out
            out += chunk


def test_socket_keep_alive_and_raw_traversal(running):
    with socket.create_connection(("127.0.0.1", running), timeout=5) as s:
        f = s.makefile("rb")
        for _ in range(3):                                               # three requests, one connection
            s.sendall(b"GET /us2026/timeline.json HTTP/1.1\r\nHost: x\r\nAccept-Encoding: gzip\r\n\r\n")
            assert f.readline().startswith(b"HTTP/1.1 200")
            n = 0
            while (line := f.readline()) != b"\r\n":
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            assert json.loads(gzip.decompress(f.read(n)))
    for target in (b"/../secret.json", b"/us2026/../../secret.json", b"../secret.json", b"/us2026/leak.json"):
        out = raw_request(running, b"GET " + target + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert out.split(b" ")[1] in (b"400", b"404") and b"key" not in out
    out = raw_request(running, b"GET /us2026/timeline.json HTTP/1.0\r\n\r\n")       # 1.0: closed after one
    assert out.startswith(b"HTTP/1.1 200") and b"Connection: close" in out


def test_socket_rejects_bodies_huge_headers_and_extra_connections(running):
    out = raw_request(running, b"POST /us2026/timeline.json HTTP/1.1\r\nContent-Length: 3\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400")
    out = raw_request(running, b"GET / HTTP/1.1\r\nX: " + b"a" * 20000 + b"\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 431") or out == b""
    a = socket.create_connection(("127.0.0.1", running), timeout=5)
    b = socket.create_connection(("127.0.0.1", running), timeout=5)
    try:
        time.sleep(0.2)
        out = raw_request(running, b"")                    # cap is 2: the third is told to retry, then closed
        assert out.startswith(b"HTTP/1.1 503") and b"Retry-After: 5" in out
    finally:
        a.close()
        b.close()


def test_socket_at_cap_closes_an_idle_keep_alive_instead_of_refusing(running):
    a = socket.create_connection(("127.0.0.1", running), timeout=5)   # served once, now idle between requests
    a.sendall(b"GET /elections.json HTTP/1.1\r\n\r\n")
    assert a.recv(65536).startswith(b"HTTP/1.1 200")
    b = socket.create_connection(("127.0.0.1", running), timeout=5)   # connected, request not sent yet
    try:
        time.sleep(0.2)
        out = raw_request(running, b"GET /elections.json HTTP/1.1\r\nConnection: close\r\n\r\n")
        assert out.startswith(b"HTTP/1.1 200")                           # cap is 2: `a` made room
        try:
            assert a.recv(10) == b""                                     # closed by the server
        except ConnectionResetError:
            pass
    finally:
        a.close()
        b.close()
