"""Polymarket WebSocket recorder: framing, archive lines, level crossings, reconnect (local mock server, no network)."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import struct
from datetime import datetime, timedelta, timezone

from brunder import pm_ws, snap  # noqa: F401  (snap: imported up front, run() imports it lazily and the test's clock is short)

TOK = {"A" * 5: "ev-a/m1", "B" * 5: "ev-b/m2"}
T0 = datetime(2026, 11, 4, 1, 2, 3, 456789, tzinfo=timezone.utc)


def test_tracker_crossings_use_mid_then_last():
    tr = pm_ws.Tracker({"t1": "ev/m"})
    book = {"event_type": "book", "asset_id": "t1", "timestamp": "1000000",
            "bids": [{"price": "0.40", "size": "1"}, {"price": "0.38", "size": "1"}],
            "asks": [{"price": "0.44", "size": "1"}, {"price": "0.50", "size": "1"}]}
    assert tr.feed(book, T0) == []                                   # first price only sets the side (mid 0.42)
    pc = {"event_type": "price_change", "timestamp": "1793000123456",
          "price_changes": [{"asset_id": "t1", "price": "0.5", "size": "9", "side": "BUY", "best_bid": "0.50",
                             "best_ask": "0.54"}]}
    c = tr.feed(pc, T0)                                              # mid 0.52: crosses 0.50 up only
    assert [(x["level"], x["dir"], x["kind"], x["price"]) for x in c] == [(0.50, "up", "mid", 0.52)]
    assert c[0]["ts"] == 1793000123456 and c[0]["t"] == "2026-10-26T07:35:23Z" and c[0]["slug"] == "ev/m"
    wide = {"event_type": "price_change", "timestamp": "1793000124000",
            "price_changes": [{"asset_id": "t1", "price": "0.9", "size": "9", "side": "BUY", "best_bid": "0.30",
                               "best_ask": "0.70"}]}
    assert tr.feed(wide, T0) == []                                   # spread 40c: falls back to last (none yet)
    lt = {"event_type": "last_trade_price", "asset_id": "t1", "price": "0.20", "timestamp": "1793000125000"}
    c = tr.feed(lt, T0)                                              # last 0.20: down through 0.25 and 0.50
    assert [(x["level"], x["dir"], x["kind"]) for x in c] == [(0.25, "down", "last"), (0.50, "down", "last")]
    assert tr.feed({"event_type": "book", "asset_id": "other", "bids": [], "asks": []}, T0) == []


def test_archive_line_keeps_event_and_adds_slug():
    msg = {"event_type": "price_change", "market": "0x1", "timestamp": "1700000000123",
           "price_changes": [{"asset_id": "A" * 5, "price": "0.1", "best_bid": "0.1", "best_ask": "0.2"},
                             {"asset_id": "zzz", "price": "0.9"}]}
    d = pm_ws.archive_line(msg, T0, 3, TOK)
    assert d["ts"] == 1700000000123 and d["conn"] == 3 and d["recv"] == "2026-11-04T01:02:03.456789+00:00"
    assert d["price_changes"][0]["slug"] == "ev-a/m1" and "slug" not in d["price_changes"][1]
    assert d["event_type"] == "price_change" and d["market"] == "0x1"


async def _server(script, port_box: list):
    """Minimal RFC 6455 server: accepts the handshake, hands `script(send, recv)` the connection."""
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter):
        head = (await r.readuntil(b"\r\n\r\n")).decode()
        key = next(ln.split(":", 1)[1].strip() for ln in head.split("\r\n") if ln.lower().startswith("sec-websocket-key"))
        acc = base64.b64encode(hashlib.sha1((key + pm_ws._GUID).encode()).digest()).decode()
        w.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Date: Wed, 04 Nov 2026 01:02:03 GMT\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode())
        await w.drain()

        async def send(text: str, op: int = 1):
            b = text.encode()
            n = len(b)
            hdr = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n)
                                        if n < 65536 else bytes([127]) + struct.pack("!Q", n))
            w.write(hdr + b)
            await w.drain()

        async def recv() -> str:
            b0, b1 = await r.readexactly(2)
            n = b1 & 0x7F
            n = struct.unpack("!H", await r.readexactly(2))[0] if n == 126 else n
            mask = await r.readexactly(4)
            data = await r.readexactly(n)
            return bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode()
        try:
            await script(send, recv)
        finally:
            w.close()
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port_box.append(srv.sockets[0].getsockname()[1])
    return srv


def _run(script, out, seconds=3.0, **kw):
    async def go():
        box: list = []
        srv = await _server(script, box)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        try:
            await pm_ws.run("t", TOK, until, out_root=out, url=f"ws://127.0.0.1:{box[0]}/ws/market", **kw)
        finally:
            srv.close()
    asyncio.run(go())


def test_recorder_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_ws, "MIN_RECONNECT", 0.2)
    sub = []

    async def script(send, recv):
        sub.append(json.loads(await recv()))
        a = "A" * 5
        await send(json.dumps([{"event_type": "book", "asset_id": a, "market": "0x1", "timestamp": "1793000000000",
                                "bids": [{"price": "0.40", "size": "5"}], "asks": [{"price": "0.44", "size": "5"}]}]))
        await send(json.dumps({"event_type": "price_change", "market": "0x1", "timestamp": "1793000001500",
                               "price_changes": [{"asset_id": a, "price": "0.5", "size": "1", "side": "BUY",
                                                  "best_bid": "0.52", "best_ask": "0.56"}]}))
        await send(json.dumps({"event_type": "last_trade_price", "asset_id": a, "price": "0.54", "size": "3",
                               "timestamp": "1793000002000"}))
        await send("garbage not json")
        assert await recv() == "PING"                                # heartbeat, answered by a text PONG
        await send("PONG")

    monkeypatch.setattr(pm_ws, "PING_EVERY", 0.3)
    _run(script, tmp_path, seconds=1.5)
    assert sub[0] == {"assets_ids": sorted(TOK), "type": "market", "custom_feature_enabled": True}
    d = tmp_path / "t"
    rows = []
    for f in (d / "polymarket_ws").iterdir():
        rows += [json.loads(x) for x in (gzip.open(f, "rt") if f.suffix == ".gz" else open(f)).read().splitlines()]
    kinds = [r["event_type"] for r in rows]
    assert kinds[0] == "_connect" and rows[0]["http_date"] == "Wed, 04 Nov 2026 01:02:03 GMT"
    assert kinds[1:5] == ["book", "price_change", "last_trade_price", "_text"]
    assert rows[2]["ts"] == 1793000001500 and rows[2]["price_changes"][0]["slug"] == "ev-a/m1"
    assert "_disconnect" in kinds and kinds.count("_connect") >= 2   # the server hung up: reconnect after MIN_RECONNECT
    cr = [json.loads(x) for x in (d / "ws_crossings.jsonl").read_text().splitlines()]
    assert [(c["level"], c["dir"], c["kind"], c["ts"]) for c in cr[:1]] == [(0.50, "up", "mid", 1793000001500)]
    assert json.loads((d / "ws_tokens.json").read_text()) == TOK
    ix = [json.loads(x) for x in (d / "index.jsonl").read_text().splitlines()]
    assert ix and ix[0]["key"] == "polymarket_ws" and ix[0]["gzip"] is True and ix[0]["events"] == len(rows)
    assert not list((d / "polymarket_ws").glob("*.jsonl"))          # raw hour file replaced by its .gz


def test_sharding_and_unit_slugs(tmp_path):
    p = tmp_path / "u.service"
    p.write_text("ExecStart=x \\\n  --pm-event a-b-1 --pm-event c_d \\\n  --pm-event e.f --interval 30\n")
    assert pm_ws.slugs_from_unit(p) == ["a-b-1", "c_d", "e.f"]
    seen = []

    async def script(send, recv):
        seen.append(json.loads(await recv())["assets_ids"])
        await asyncio.sleep(0.3)

    _run(script, tmp_path, seconds=1.2, shard=1)
    assert sorted(seen[:2]) == [["A" * 5], ["B" * 5]]
