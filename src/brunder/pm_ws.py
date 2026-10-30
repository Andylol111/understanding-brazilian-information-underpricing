"""Polymarket market-channel WebSocket recorder: every book / price change / trade with the server's timestamp.

Public, no auth (wss://ws-subscriptions-clob.polymarket.com/ws/market; docs.polymarket.com/market-data/realtime-data:
application heartbeat `PING` every 10 s, subscription frame {"assets_ids": [...], "type": "market"}). Passive: it writes
only under its own recorder tag and never touches the archive that `brunder us live` reads.

    <root>/<tag>/polymarket_ws/<UTC start>.jsonl[.gz]  10-minute files, one line per event, in arrival order:
        {"recv": "<UTC iso, µs, local receipt>", "conn": <shard>, "ts": <server ms since epoch | null>,
         "slug": "<event>/<market>" (price_change: one per entry), …the server's event verbatim (event_type, asset_id…)}
        plus {"event_type": "_connect" | "_disconnect", …} lines (http_date = server clock at the handshake)
    <root>/<tag>/ws_crossings.jsonl                      the second-level threshold-crossing log (see Crossings)
    <root>/<tag>/ws_tokens.json                          token -> "<event>/<market>" for every subscribed token
    <root>/<tag>/index.jsonl                             one line per closed file (shape of brunder.snap's; the closed
                                                         file is gzipped: <start>.jsonl.gz, `bytes`/`sha256` are of the raw text)

Crossings: per YES token the price is tracked as brunder.live.top does (mid of best bid/ask when the spread ≤ 10c, else
the last trade). Whenever it moves across one of LEVELS a line is appended:
    {"slug", "token", "level", "dir": "up"|"down", "price", "kind": "mid"|"last", "event_type",
     "ts": <server ms>, "t": "<server time, UTC, whole seconds>", "recv": "<local receipt, µs>"}
The first price seen only sets the side (no crossing). The client is stdlib asyncio + ssl (RFC 6455); no dependency.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import math
import os
import re
import ssl
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import certifi

URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
UA = "Mozilla/5.0 (research; brunder)"
PING_EVERY = 10.0              # docs: send PING every 10 s
SILENCE = 30.0                 # no frame (not even PONG) for this long: reconnect
MIN_RECONNECT = 10.0           # never reconnect faster than this
MAX_RECONNECT = 60.0
SHARD = 250                    # tokens per connection
ROTATE_MIN = 10               # a new archive file every 10 minutes (bounded size before it is gzipped)
LEVELS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
MAX_SPREAD = 0.10              # as brunder.live.top: mid only when bid-ask spread ≤ 10c
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ------------------------------------------------------------------ minimal RFC 6455 client

class Handshake(Exception):
    def __init__(self, status: int, headers: dict[str, str]):
        super().__init__(f"HTTP {status}")
        self.status, self.headers = status, headers


class Conn:
    """One client WebSocket connection: text frames in/out, ping/pong/close handled, fragments joined."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict[str, str]):
        self.r, self.w, self.headers = reader, writer, headers

    @classmethod
    async def open(cls, url: str, timeout: float = 20.0, ctx: ssl.SSLContext | None = None) -> "Conn":
        u = urlsplit(url)
        tls = u.scheme == "wss"
        port = u.port or (443 if tls else 80)
        if tls and ctx is None:
            ctx = ssl.create_default_context(cafile=certifi.where())
        r, w = await asyncio.wait_for(asyncio.open_connection(u.hostname, port, ssl=ctx if tls else None,
                                                              server_hostname=u.hostname if tls else None), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        w.write((f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: {UA}\r\n"
                 f"Origin: https://{u.hostname}\r\n\r\n").encode())
        await w.drain()
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout)
        lines = head.decode("latin1").split("\r\n")
        status = int(lines[0].split()[1])
        hdr = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ":" in ln)}
        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if status != 101 or hdr.get("sec-websocket-accept") != accept:
            w.close()
            raise Handshake(status, hdr)
        return cls(r, w, hdr)

    async def _send(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n) if n < 65536 \
            else bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if n else b""
        self.w.write(head + mask + body)
        await self.w.drain()

    async def send_text(self, s: str) -> None:
        await self._send(0x1, s.encode())

    async def recv(self) -> str | None:
        """Next text message (str), or None once the server closed. Replies to ping, ignores pong/binary."""
        buf, text = b"", False
        while True:
            b0, b1 = await self.r.readexactly(2)
            fin, op, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", await self.r.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.r.readexactly(8))[0]
            mask = await self.r.readexactly(4) if b1 & 0x80 else None
            data = await self.r.readexactly(n) if n else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                return None
            if op == 0x9:
                await self._send(0xA, data)
                continue
            if op == 0xA:
                return ""                                   # pong: a sign of life, no message
            if op in (0x1, 0x2):
                buf, text = data, op == 0x1
            elif op == 0x0:
                buf += data
            if fin and (op in (0x0, 0x1, 0x2)):
                return buf.decode() if text else ""

    async def close(self) -> None:
        try:
            await self._send(0x8, b"")
        except Exception:
            pass
        self.w.close()


# ------------------------------------------------------------------ events -> lines, price tracking

def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _ms(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="microseconds")


class Tracker:
    """Per-token best bid / ask / last and the side of each level; emits level crossings."""

    def __init__(self, tokens: dict[str, str], levels: tuple[float, ...] = LEVELS):
        self.slug, self.levels = tokens, levels
        self.bid: dict[str, float] = {}
        self.ask: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.side: dict[str, tuple[bool, ...]] = {}

    def price(self, tok: str) -> tuple[float, str]:
        bb, ba = self.bid.get(tok, math.nan), self.ask.get(tok, math.nan)
        if math.isfinite(bb) and math.isfinite(ba) and ba - bb <= MAX_SPREAD + 1e-9:
            return (bb + ba) / 2, "mid"
        return self.last.get(tok, math.nan), "last"

    def update(self, tok: str, ev: str, ts: int | None, recv: datetime, *, bid=None, ask=None, last=None) -> list[dict]:
        if tok not in self.slug:
            return []
        if bid is not None:
            self.bid[tok] = bid
        if ask is not None:
            self.ask[tok] = ask
        if last is not None:
            self.last[tok] = last
        p, kind = self.price(tok)
        if not math.isfinite(p):
            return []
        side = tuple(p >= L for L in self.levels)
        prev, self.side[tok] = self.side.get(tok), side
        if prev is None:
            return []
        t = datetime.fromtimestamp((ts if ts is not None else recv.timestamp() * 1000) // 1000, timezone.utc)
        return [{"slug": self.slug[tok], "token": tok, "level": L, "dir": "up" if now else "down", "price": round(p, 6),
                 "kind": kind, "event_type": ev, "ts": ts, "t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "recv": _iso(recv)}
                for L, was, now in zip(self.levels, prev, side) if was != now]

    def feed(self, msg: dict, recv: datetime) -> list[dict]:
        """Update from one server event; returns the crossings it caused."""
        ev, ts, out = msg.get("event_type"), _ms(msg.get("timestamp")), []
        if ev == "book":
            bids = [_f(x.get("price")) for x in msg.get("bids") or []]
            asks = [_f(x.get("price")) for x in msg.get("asks") or []]
            out += self.update(msg.get("asset_id"), ev, ts, recv, bid=max(bids, default=math.nan),
                               ask=min(asks, default=math.nan))
        elif ev == "price_change":
            for c in msg.get("price_changes") or []:
                bb, ba = c.get("best_bid"), c.get("best_ask")
                out += self.update(c.get("asset_id"), ev, ts, recv, bid=None if bb is None else _f(bb or "nan"),
                                   ask=None if ba is None else _f(ba or "nan"))
        elif ev == "best_bid_ask":
            out += self.update(msg.get("asset_id"), ev, ts, recv, bid=_f(msg.get("best_bid") or "nan"),
                               ask=_f(msg.get("best_ask") or "nan"))
        elif ev == "last_trade_price":
            out += self.update(msg.get("asset_id"), ev, ts, recv, last=_f(msg.get("price")))
        return out


def archive_line(msg: dict, recv: datetime, conn: int, slug: dict[str, str]) -> dict:
    """The archive line of one server event (verbatim fields + recv / conn / ts / slug)."""
    d = {"recv": _iso(recv), "conn": conn, "ts": _ms(msg.get("timestamp")), **msg}
    if msg.get("asset_id") in slug:
        d["slug"] = slug[msg["asset_id"]]
    if isinstance(msg.get("price_changes"), list):
        d["price_changes"] = [{**c, **({"slug": slug[c["asset_id"]]} if c.get("asset_id") in slug else {})}
                              for c in msg["price_changes"]]
    return d


# ------------------------------------------------------------------ the recorder

class Writer:
    """10-minute JSONL files under <out>/polymarket_ws/ (named by their UTC start), a line in index.jsonl when one is
    closed, the crossings file; every write is flushed (a crash loses nothing but the unparsed tail)."""

    def __init__(self, out: Path):
        self.out, self.dir = out, out / "polymarket_ws"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cross = (out / "ws_crossings.jsonl").open("a")
        self.fh, self.hour, self.path, self.n = None, None, None, 0
        self.jobs: list[threading.Thread] = []

    def write(self, d: dict, t: datetime) -> None:
        hour = t.replace(minute=t.minute - t.minute % ROTATE_MIN, second=0, microsecond=0)
        if hour != self.hour:
            self._close()
            self.hour, self.path = hour, self.dir / f"{hour:%Y%m%dT%H%M%S.%fZ}.jsonl"
            self.fh, self.n = self.path.open("a"), 0
        self.fh.write(json.dumps(d, separators=(",", ":")) + "\n")
        self.fh.flush()
        self.n += 1

    def crossing(self, c: dict) -> None:
        self.cross.write(json.dumps(c, separators=(",", ":")) + "\n")
        self.cross.flush()

    def _close(self) -> None:
        if self.fh is None:
            return
        self.fh.close()
        t = threading.Thread(target=self._seal, args=(self.path, self.n))    # gzip off the event loop
        t.start()
        self.jobs = [j for j in self.jobs if j.is_alive()] + [t]
        self.fh = None

    def _seal(self, path: Path, n: int) -> None:
        """File closed: gzip it in a stream (the raw file is removed once the .gz is in place) and index it."""
        gz = path.with_name(path.name + ".gz")
        tmp = self.out / ".tmp" / gz.name
        tmp.parent.mkdir(exist_ok=True)
        h, size = hashlib.sha256(), 0
        with path.open("rb") as src, gzip.open(tmp, "wb", 6) as dst:
            while chunk := src.read(1 << 20):
                h.update(chunk)
                size += len(chunk)
                dst.write(chunk)
        os.replace(tmp, gz)
        path.unlink()
        with (self.out / "index.jsonl").open("a") as ix:
            ix.write(json.dumps({"t": _iso(datetime.now(timezone.utc)), "key": "polymarket_ws",
                                 "file": str(gz.relative_to(self.out)), "sha256": h.hexdigest(),
                                 "bytes": size, "gzip": True, "events": n}) + "\n")

    def close(self) -> None:
        self._close()
        for j in self.jobs:
            j.join()
        self.cross.close()


class Stats:
    def __init__(self) -> None:
        self.events = self.reconnects = self.crossings = 0
        self.lag: list[float] = []

    def line(self, conns: int) -> str:
        lag = sorted(self.lag)
        q = lambda p: f"{lag[min(len(lag) - 1, int(p * len(lag)))]:.2f}s" if lag else "-"   # noqa: E731
        self.lag.clear()
        return f"ws: {self.events} events, {self.crossings} crossings, {self.reconnects} reconnects, " \
               f"{conns} connections, recv-ts lag p50 {q(0.5)} p95 {q(0.95)}"


async def _shard(i: int, assets: list[str], url: str, w: Writer, tr: Tracker, slug: dict[str, str], st: Stats,
                 until: datetime | None) -> None:
    fails = 0
    while until is None or datetime.now(timezone.utc) < until:
        started, conn = time.monotonic(), None
        try:
            conn = await Conn.open(url)
            now = datetime.now(timezone.utc)
            w.write({"recv": _iso(now), "conn": i, "event_type": "_connect", "n_assets": len(assets),
                     "http_date": conn.headers.get("date")}, now)
            await conn.send_text(json.dumps({"assets_ids": assets, "type": "market", "custom_feature_enabled": True}))
            pinger = asyncio.create_task(_ping(conn))
            try:
                while until is None or datetime.now(timezone.utc) < until:
                    raw = await asyncio.wait_for(conn.recv(), SILENCE)
                    if raw is None:
                        raise ConnectionError("closed by server")
                    recv = datetime.now(timezone.utc)
                    if raw in ("", "PONG"):
                        continue
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        w.write({"recv": _iso(recv), "conn": i, "event_type": "_text", "text": raw[:500]}, recv)
                        continue
                    for msg in data if isinstance(data, list) else [data]:
                        if not isinstance(msg, dict):
                            continue
                        w.write(archive_line(msg, recv, i, slug), recv)
                        st.events += 1
                        ts = _ms(msg.get("timestamp"))
                        if ts:
                            st.lag.append(recv.timestamp() - ts / 1000)
                        for c in tr.feed(msg, recv):
                            w.crossing(c)
                            st.crossings += 1
            finally:
                pinger.cancel()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:                                       # reconnect, never stop on the night
            now = datetime.now(timezone.utc)
            extra = ""
            if isinstance(e, Handshake):
                ra = e.headers.get("retry-after", "")
                extra = f" retry-after {ra}" if ra else ""
            w.write({"recv": _iso(now), "conn": i, "event_type": "_disconnect", "error": f"{type(e).__name__}: {e}{extra}"},
                    now)
            print(f"{now:%H:%M:%S} ws {i}: {type(e).__name__}: {e}{extra}, reconnecting", flush=True)
            st.reconnects += 1
            fails = 0 if time.monotonic() - started > 60 else fails + 1
            wait = min(MAX_RECONNECT, MIN_RECONNECT * 2 ** (fails - 1)) if fails else MIN_RECONNECT
            if isinstance(e, Handshake) and e.status == 429:
                ra = e.headers.get("retry-after", "")
                wait = max(wait, float(ra) if ra.isdigit() else 60.0)
            await asyncio.sleep(wait if until is None else max(0.0, min(wait, (until - datetime.now(timezone.utc)).total_seconds())))
        finally:
            if conn is not None:
                await conn.close()


async def _ping(conn: Conn) -> None:
    while True:
        await asyncio.sleep(PING_EVERY)
        await conn.send_text("PING")


async def run(tag: str, tokens: dict[str, str], until: datetime | None = None, out_root: Path | None = None,
              shard: int = SHARD, url: str = URL, levels: tuple[float, ...] = LEVELS) -> None:
    """Record the market channel for `tokens` ({YES token id: "<event>/<market>"}) until `until`."""
    from .snap import SNAP
    out = (out_root or SNAP) / tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "ws_tokens.json").write_text(json.dumps(tokens, indent=0, sort_keys=True))
    ids = sorted(tokens)
    shards = [ids[k:k + shard] for k in range(0, len(ids), shard)]
    print(f"pm ws {tag}: {len(ids)} tokens, {len(shards)} connection(s) of ≤ {shard}, until {until}", flush=True)
    w, tr, st = Writer(out), Tracker(tokens, levels), Stats()

    async def report() -> None:
        while True:
            await asyncio.sleep(60)
            print(f"{datetime.now(timezone.utc):%H:%M:%S} " + st.line(len(shards)), flush=True)

    rep = asyncio.create_task(report())
    try:
        async def staggered(i: int, a: list[str]) -> None:
            await asyncio.sleep(1.0 * i)                             # connections 1 s apart
            await _shard(i, a, url, w, tr, tokens, st, until)
        await asyncio.gather(*(staggered(i, a) for i, a in enumerate(shards)))
    finally:
        rep.cancel()
        w.close()


def slugs_from_unit(path: Path) -> list[str]:
    """Every `--pm-event <slug>` of a systemd unit (deploy/brunder-snap-us.service), so the WebSocket recorder follows
    exactly the markets the book recorder does."""
    return re.findall(r"--pm-event\s+([A-Za-z0-9._-]+)", Path(path).read_text())
