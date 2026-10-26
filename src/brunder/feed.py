"""Low-latency reader for the TSE results feed: sample several CDN edges, keep the newest copy,
within a request budget the CDN tolerates.

The feed (resultados.tse.jus.br) is served by Akamai. Each edge server caches a file for up to
`max-age` seconds (~60 s outside election night), the cache ignores query strings (cache-busting does
nothing), and edges refresh on independent timers: on 2026-10-26 nine edges for one file had 9–57 s
left. Reading one edge sees data up to one TTL old; reading k edges and keeping the copy with the
highest `idg` (TSE's generation counter) cuts the expected age to roughly TTL/(k+1).

The CDN rate-limits per client: an unbudgeted fan-out (≈50k requests in two minutes, 2026-10-26) got
every edge answering 429, and so did a 40 s run peaking at 40 req/s shortly afterwards (lockout length
logged in logs/tse_throttle_probe.log). So all requests share one adaptive limiter (AIMD): start at
`rps`, add a little per clean response up to `max_rps`, halve and pause everything for `cooldown_s` on
any 429. Defaults are deliberately low; the tool must never be locked out on a count night, and it
does not evade the limit (no IP rotation or proxies).

Two read modes:
  get(url, unit)            hot files (state totals, progress files): `fanout` edges, rotated every
                            poll so every edge's timer is sampled; newest copy wins.
  get_one(url, unit, edge)  bulk files (municipalities): one request, to `edge` — pass the edge that
                            just served the state's newest progress file — falling back to others.
Both send If-None-Match with the edge's last ETag (unchanged copies cost a 304, no body), and report
staleness: fetch time − the file's own generation time (dg/hg, the unit's local time zone). With
`log`, every read is appended to a JSONL file so the night's real latency is measured, not assumed.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import tse_live

UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
RESOLVE_EVERY = 300.0
MISSING = "missing"
THROTTLED = "throttled"


class FeedUnavailable(RuntimeError):
    """No CDN edge served this file (network errors or throttling); retry later."""


@dataclass
class Copy:
    body: bytes
    idg: int
    edge: str
    generated_utc: datetime | None
    fetched_utc: datetime

    @property
    def age_s(self) -> float | None:
        return (self.fetched_utc - self.generated_utc).total_seconds() if self.generated_utc else None


@dataclass
class _EdgeState:
    etag: dict[str, str] = field(default_factory=dict)
    body: dict[str, bytes] = field(default_factory=dict)
    benched_until: float = 0.0
    strikes: int = 0


def _idg_and_time(body: bytes, unit: str) -> tuple[int, datetime | None]:
    """TSE generation counter and generation time; a copy whose stamps don't parse ranks oldest (−1)
    but is still served, never silently dropped."""
    d = json.loads(body)
    try:
        idg = int(d.get("idg") or 0)
    except (TypeError, ValueError):
        idg = -1
    try:
        t = tse_live._local_to_utc(d.get("dg"), d.get("hg"), unit)
    except (TypeError, ValueError):
        t = None
    return idg, (t.to_pydatetime() if t is not None and t == t else None)


def _bench_s(strikes: int) -> float:
    """Seconds an edge sits out after `strikes` consecutive network/server failures."""
    return min(2.0**strikes, 60.0)


class _Limiter:
    """Token bucket with AIMD rate: +`step` req/s per clean response, ×½ and a global pause on 429."""

    def __init__(self, rps: float, max_rps: float, min_rps: float, cooldown_s: float, step: float = 0.05):
        self.rate, self.max_rps, self.min_rps, self.cooldown_s, self.step = rps, max_rps, min_rps, cooldown_s, step
        self.tokens, self.t_last, self.paused_until = 1.0, time.monotonic(), 0.0
        self.throttles = 0

    async def take(self) -> None:
        while True:
            now = time.monotonic()
            if now < self.paused_until:
                await asyncio.sleep(self.paused_until - now)
                continue
            self.tokens = min(max(self.rate, 1.0), self.tokens + (now - self.t_last) * self.rate)
            self.t_last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            await asyncio.sleep((1.0 - self.tokens) / self.rate)

    def ok(self) -> None:
        self.rate = min(self.max_rps, self.rate + self.step)

    def throttled(self) -> None:
        now = time.monotonic()
        if now >= self.paused_until:                    # one 429 burst = one halving
            self.throttles += 1
            self.rate = max(self.min_rps, self.rate / 2)
            self.paused_until = now + self.cooldown_s
            self.tokens = 0.0


class FastFeed:
    """Usage: async with FastFeed(log=path) as f: copy = await f.get(url, unit="sp")."""

    def __init__(self, host: str = "resultados.tse.jus.br", fanout: int = 2, rps: float = 5.0,
                 max_rps: float = 10.0, min_rps: float = 1.0, cooldown_s: float = 30.0, timeout: float = 10.0,
                 log: Path | None = None, edges: list[str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.host, self.fanout, self.timeout, self.log = host, fanout, timeout, log
        self.limiter = _Limiter(rps, max_rps, min_rps, cooldown_s)
        self._fixed_edges = edges
        self._transport = transport
        self._edges: list[str] = []
        self._resolved_at = 0.0
        self._state: dict[str, _EdgeState] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._rot: dict[str, int] = {}

    async def __aenter__(self):
        self._resolve()
        return self

    async def __aexit__(self, *exc):
        await asyncio.gather(*(c.aclose() for c in self._clients.values()))

    def _resolve(self) -> None:
        if self._fixed_edges is not None:
            ips = self._fixed_edges
        else:
            try:
                ips = sorted({ai[4][0] for ai in socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
                              if ai[0] == socket.AF_INET})
            except OSError:
                ips = self._edges                      # keep the last good set through a DNS blip
        if ips:
            self._edges = ips
        for ip in self._edges:
            self._state.setdefault(ip, _EdgeState())
            if ip not in self._clients:
                # One client per edge, each one multiplexed HTTP/2 connection. Measured 2026-10-26, 29
                # state files: one edge 0.12 s per pass (HTTP/2) vs 0.45 s (HTTP/1.1, 4 connections);
                # nine edges sharing one client 1.5–3 s (httpcore's pool serialises assignment).
                self._clients[ip] = httpx.AsyncClient(timeout=self.timeout, headers=UA, follow_redirects=False,
                                                      http2=True, transport=self._transport)
        self._resolved_at = time.monotonic()

    def _live_edges(self) -> list[str]:
        if time.monotonic() - self._resolved_at > RESOLVE_EVERY:
            self._resolve()
        now = time.monotonic()
        return [ip for ip in self._edges if self._state[ip].benched_until <= now]

    async def _one(self, ip: str, path: str) -> bytes | str | None:
        """Body, MISSING (the file does not exist), THROTTLED (429), or None (edge failed)."""
        st = self._state[ip]
        headers = {"Host": self.host}
        if path in st.etag:
            headers["If-None-Match"] = st.etag[path]
        await self.limiter.take()
        try:
            r = await self._clients[ip].get(f"https://{ip}{path}", headers=headers,
                                            extensions={"sni_hostname": self.host})
        except (httpx.HTTPError, OSError):
            st.strikes += 1
            st.benched_until = time.monotonic() + _bench_s(st.strikes)
            return None
        if r.status_code == 429:
            self.limiter.throttled()
            return THROTTLED
        if r.status_code in (500, 502, 503, 504):
            st.strikes += 1
            st.benched_until = time.monotonic() + _bench_s(st.strikes)
            return None
        self.limiter.ok()
        if r.status_code == 304:
            return st.body.get(path)
        if r.status_code in (403, 404) or (r.status_code == 200 and b"NoSuchKey" in r.content[:300]):
            return MISSING
        if r.status_code != 200 or r.content.lstrip()[:1] != b"{":
            return None
        st.strikes = 0
        if "etag" in r.headers:
            st.etag[path] = r.headers["etag"]
        st.body[path] = r.content
        return r.content

    def _pick(self, path: str, bodies: list, ips: list[str], unit: str, mode: str) -> Copy | None:
        if all(b is None or b is THROTTLED for b in bodies):
            raise FeedUnavailable(path)
        now = datetime.now(timezone.utc)
        best: Copy | None = None
        idgs = []
        for ip, body in zip(ips, bodies):
            if not isinstance(body, bytes):
                continue
            try:
                idg, gen = _idg_and_time(body, unit)
            except ValueError:                   # not JSON: an error page that slipped through
                continue
            idgs.append(idg)
            if best is None or idg > best.idg:
                best = Copy(body, idg, ip, gen, now)
        if best is not None and self.log is not None:
            rec = {"t": now.isoformat(), "path": path, "mode": mode, "edge": best.edge, "idg": best.idg,
                   "age_s": best.age_s, "edges_asked": len(ips), "edges_ok": len(idgs),
                   "edges_newest": sum(i == best.idg for i in idgs),
                   "idg_spread": (max(idgs) - min(idgs)) if idgs else 0, "rps": round(self.limiter.rate, 2)}
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return best

    async def get(self, url: str, unit: str = "br") -> Copy | None:
        """Newest copy across `fanout` edges (rotated per path); None if the file does not exist.
        Raises FeedUnavailable if no asked edge served it."""
        path = urlsplit(url).path
        live = self._live_edges() or self._edges
        k = min(self.fanout, len(live))
        start = self._rot.get(path, 0)
        self._rot[path] = (start + k) % len(live)
        ips = [live[(start + i) % len(live)] for i in range(k)]
        bodies = await asyncio.gather(*(self._one(ip, path) for ip in ips))
        return self._pick(path, bodies, ips, unit, "fanout")

    async def get_one(self, url: str, unit: str = "br", edge: str | None = None) -> Copy | None:
        """One request, to `edge` if given and healthy, else the next healthy edge; tries up to three
        edges on failure. None if the file does not exist; FeedUnavailable if none served it."""
        path = urlsplit(url).path
        live = self._live_edges() or self._edges
        order = ([edge] if edge in live else []) + [ip for ip in live if ip != edge]
        tried, bodies = [], []
        for ip in order[:3]:
            b = await self._one(ip, path)
            tried.append(ip)
            bodies.append(b)
            if isinstance(b, bytes) or b is MISSING:
                break
            if b is THROTTLED:                   # the limiter has paused; don't burn other edges
                break
        return self._pick(path, bodies, tried, unit, "single")
