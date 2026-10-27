"""Election-night rehearsal: replay a past count through a simulated TSE CDN and run the live engine
(`night.run_live`, unchanged) against it, on a simulated clock.

The count is rebuilt from BU-web section receipts (`bweb.sections`): at simulated time t a section is
published iff it was received at or before t (publication lag 0, calibrated on the 2026 first round).
`SimCount` renders the live-feed files the engine reads (catalogue, state progress files, state and
municipality vote files) in the feed's own JSON shape, from cumulative sums per municipality.

`SimCDN` is an httpx transport standing in for Akamai: several edge "IPs", each caching every file for
`ttl_s` simulated seconds from the request that filled it (so edges refresh on independent timers),
ETag / If-None-Match → 304, a per-client token-bucket rate limit that answers 429 and locks the client
out for `lockout_s` when exceeded, and a random round-trip time. Its parameters are guesses where the
real values are unknown (election-night TTL, the real rate limit); the rehearsal reports them.

Markets: books are synthesised from the archived Polymarket price at simulated time t (one level at
price ± half-spread, deep), so the live cost matches the replay's cost model.

What the rehearsal measures: how far the live engine's view of the count trails the truth (data delay),
how its probabilities differ from the ideal replay at the same instant, and whether the request budget
keeps up at peak reporting. It runs the real FastFeed, MunTracker and model code.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import bweb, night, polymarket, tse_live
from .paths import POLYMARKET


class SimClock:
    """Simulated UTC time running `speed`× wall time from `start`."""

    def __init__(self, start: pd.Timestamp, speed: float = 1.0):
        self.start = pd.Timestamp(start).tz_convert("UTC").to_pydatetime()
        self.speed, self.w0 = speed, time.monotonic()

    def now(self) -> datetime:
        return self.start + timedelta(seconds=(time.monotonic() - self.w0) * self.speed)


@dataclass
class _Series:
    t: np.ndarray          # receipt times (int64 ns UTC), sorted
    a: np.ndarray          # cumulative votes for A
    b: np.ndarray          # cumulative votes for B
    v: np.ndarray          # cumulative valid votes
    total: int             # sections in this unit

    def at(self, t_ns: int) -> tuple[int, int, int, int, int | None]:
        i = int(np.searchsorted(self.t, t_ns, side="right"))
        if i == 0:
            return 0, 0, 0, 0, None
        return i, int(self.a[i - 1]), int(self.b[i - 1]), int(self.v[i - 1]), int(self.t[i - 1])


def _series(g: pd.DataFrame) -> _Series:
    g = g.sort_values("t_ns")
    return _Series(g["t_ns"].to_numpy(), g["a"].cumsum().to_numpy(), g["b"].cumsum().to_numpy(),
                   g["valid"].cumsum().to_numpy(), len(g))


class SimCount:
    """The count at any simulated time, rendered as live-feed files."""

    def __init__(self, cfg: dict, tz_offset: str = "3h"):
        E, R = cfg["election"], cfg["replay"]
        self.ele, self.cand_a, self.cand_b = int(E["ele"]), E["cand_a"], E.get("cand_b") or "00"
        self.cargo = E.get("cargo") or night.CARGO_CODE[E.get("cargo_name", "PRESIDENTE")]
        s = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
        if "b" not in s:
            s["b"] = 0
        s["uf"] = s["uf"].str.lower()
        s["t_ns"] = (s["received"] + pd.Timedelta(tz_offset)).dt.tz_localize("UTC").dt.as_unit("ns").astype("int64")
        self.sections = s
        self.mun = {k: _series(g) for k, g in s.groupby(["uf", "cd_mun"])}
        self.state = {k: _series(g) for k, g in s.groupby("uf")}
        self.nat = _series(s)
        self.ufs = sorted(self.state)
        self.muns = {uf: sorted(m for (u, m) in self.mun if u == uf) for uf in self.ufs}
        self.first_ns, self.last_ns = int(s["t_ns"].min()), int(s["t_ns"].max())
        e = f"e{self.ele:06d}"
        c = f"c{self.cargo:04d}"
        self._re = {
            "cm": re.compile(rf"/oficial/[^/]+/{self.ele}/config/mun-{e}-cm\.json$"),
            "ab": re.compile(rf"/oficial/[^/]+/{self.ele}/dados/([a-z]{{2}})/\1-{e}-ab\.json$"),
            "u": re.compile(rf"/oficial/[^/]+/{self.ele}/dados/([a-z]{{2}})/\1(\d{{5}})?-{c}-{e}-u\.json$"),
        }

    def counted_frac(self, t_ns: int) -> float:
        return self.nat.at(t_ns)[0] / self.nat.total

    @staticmethod
    def _stamp(t_ns: int, uf: str) -> tuple[str, str]:
        local = datetime.fromtimestamp(t_ns / 1e9, timezone.utc) + timedelta(hours=tse_live.UTC_OFFSET_H.get(uf, -3))
        return local.strftime("%d/%m/%Y"), local.strftime("%H:%M:%S")

    def _unit(self, tpabr: str, cd: str, uf: str, ser: _Series, t_ns: int) -> dict:
        st, a, b, v, last = ser.at(t_ns)
        dg, hg = self._stamp(last if last is not None else t_ns, uf)
        cands = [{"sg": "A", "cand": [{"n": self.cand_a, "nmu": "A", "vap": str(a)}]},
                 {"sg": "B", "cand": [{"n": self.cand_b, "nmu": "B", "vap": str(b)}]},
                 {"sg": "O", "cand": [{"n": "0", "nmu": "OTHERS", "vap": str(v - a - b)}]}]
        return {"ele": str(self.ele), "tpabr": tpabr, "cdabr": cd, "dg": dg, "hg": hg, "dt": dg, "ht": hg,
                "idg": str(st + 1), "tf": "s" if st == ser.total else "n",
                "s": {"ts": str(ser.total), "st": str(st), "pst": f"{100 * st / ser.total:.2f}".replace(".", ",")},
                "e": {"te": "0"}, "v": {"tv": str(v), "vv": str(v), "vb": "0", "tvn": "0"},
                "carg": [{"agr": [{"par": cands}]}]}

    def render(self, path: str, t_ns: int) -> bytes | None:
        if self._re["cm"].search(path):
            return json.dumps({"abr": [{"cd": uf, "ds": uf.upper(), "mu": [{"cd": m, "nm": m} for m in self.muns[uf]]}
                                       for uf in self.ufs]}).encode()
        if m := self._re["ab"].search(path):
            uf = m.group(1)
            if uf not in self.state:
                return None
            st_uf = self.state[uf].at(t_ns)
            dg, hg = self._stamp(st_uf[4] if st_uf[4] is not None else t_ns, uf)
            rows = []
            for mun in self.muns[uf]:
                ser = self.mun[(uf, mun)]
                st, *_, last = ser.at(t_ns)
                dt, ht = self._stamp(last if last is not None else t_ns, uf)
                rows.append({"tpabr": "mun", "cdabr": mun, "dt": dt, "ht": ht,
                             "s": {"ts": str(ser.total), "st": str(st)}, "e": {"te": "0", "c": "0"}})
            return json.dumps({"ele": str(self.ele), "dg": dg, "hg": hg, "idg": str(st_uf[0] + 1), "abr": rows}).encode()
        if m := self._re["u"].search(path):
            uf, mun = m.group(1), m.group(2)
            if uf == "br":
                return json.dumps(self._unit("pa", "br", "br", self.nat, t_ns)).encode()
            if mun:
                ser = self.mun.get((uf, mun))
                return None if ser is None else json.dumps(self._unit("mu", mun, uf, ser, t_ns)).encode()
            ser = self.state.get(uf)
            return None if ser is None else json.dumps(self._unit("uf", uf, uf, ser, t_ns)).encode()
        return None


class SimCDN(httpx.AsyncBaseTransport):
    """Stand-in for the Akamai edges in front of the feed (see module docstring)."""

    def __init__(self, count: SimCount, clock: SimClock, ttl_s: float = 60.0, limit_rps: float = 20.0,
                 burst: float = 40.0, lockout_s: float = 60.0, rtt_s: tuple[float, float] = (0.03, 0.09), seed: int = 0):
        self.count, self.clock = count, clock
        self.ttl_ns, self.limit_rps, self.burst, self.lockout_s, self.rtt_s = int(ttl_s * 1e9), limit_rps, burst, lockout_s, rtt_s
        self.rng = random.Random(seed)
        self.cache: dict[tuple[str, str], tuple[bytes | None, str, int]] = {}
        self.tokens, self.t_last, self.locked_until = burst, time.monotonic(), 0.0
        self.stats = {"requests": 0, "200": 0, "304": 0, "404": 0, "429": 0, "lockouts": 0}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self.rng.uniform(*self.rtt_s))
        self.stats["requests"] += 1
        w = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (w - self.t_last) * self.limit_rps)
        self.t_last = w
        if w < self.locked_until or self.tokens < 1:
            if w >= self.locked_until:
                self.locked_until = w + self.lockout_s
                self.stats["lockouts"] += 1
            self.stats["429"] += 1
            return httpx.Response(429, request=request)
        self.tokens -= 1
        t_ns = int(pd.Timestamp(self.clock.now()).value)
        key = (request.url.host, request.url.path)
        hit = self.cache.get(key)
        if hit is None or t_ns >= hit[2]:
            body = self.count.render(request.url.path, t_ns)
            hit = (body, f'"{hash(body)}"', t_ns + self.ttl_ns)
            self.cache[key] = hit
        body, etag, _ = hit
        if body is None:
            self.stats["404"] += 1
            return httpx.Response(404, request=request)
        if request.headers.get("if-none-match") == etag:
            self.stats["304"] += 1
            return httpx.Response(304, headers={"etag": etag}, request=request)
        self.stats["200"] += 1
        return httpx.Response(200, content=body, headers={"etag": etag, "content-type": "application/json"},
                              request=request)


def _price_books(cfg: dict, half_spread: float, clock: SimClock):
    """books_fn for run_live: one deep level at archived price ± half-spread, as of simulated now."""
    series = {}
    for m in night._markets(cfg):
        ev = polymarket.event(m["event"])
        tok = polymarket.markets(ev).set_index("slug").loc[m["market"], "yes_token"]
        px = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
        series[tok] = px.set_index("time")["price"].sort_index()

    async def books_fn(tokens):
        t = pd.Timestamp(clock.now())
        out = {}
        for tok in tokens:
            q = series.get(tok)
            if q is None or q[:t].empty:
                continue
            p = float(q[:t].iloc[-1])
            out[tok] = {"asks": [{"price": str(min(p + half_spread, 0.999)), "size": "1e9"}],
                        "bids": [{"price": str(max(p - half_spread, 0.001)), "size": "1e9"}]}
        return out
    return books_fn


async def run(cfg_path: Path, out_dir: Path, speed: float = 1.0, interval: float = 5.0,
              start: str | None = None, end: str | None = None, cdn: dict | None = None,
              feed: dict | None = None, n_edges: int = 9) -> dict:
    """Rehearse one night. start/end: simulated UTC bounds (default: 10 min before the first section
    to the time 99.5% of sections are in). Returns the CDN stats and run parameters."""
    cfg = night.load_config(cfg_path)
    count = SimCount(cfg)
    t99 = int(np.quantile(count.sections["t_ns"], 0.995))
    t_start = pd.Timestamp(start, tz="UTC") if start else pd.Timestamp(count.first_ns - int(600e9), tz="UTC")
    t_end = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp(t99, tz="UTC")
    clock = SimClock(t_start, speed)
    sim = SimCDN(count, clock, **(cdn or {}))
    edges = [f"10.0.0.{i}" for i in range(1, n_edges + 1)]
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in ("ticks.jsonl", "latency.jsonl"):
        (out_dir / f).unlink(missing_ok=True)
    w0 = time.monotonic()
    await night.run_live(cfg_path, interval, clock=clock.now,
                         books_fn=_price_books(cfg, cfg.get("trade", {}).get("half_spread", 0.01), clock),
                         feed_opts={"edges": edges, "transport": sim, **(feed or {})},
                         out_dir=out_dir, stop_at=t_end.floor("s").to_pydatetime(), quiet=True)
    meta = {"config": str(cfg_path), "speed": speed, "interval_s": interval, "sim_start": t_start.isoformat(),
            "sim_end": t_end.isoformat(), "wall_s": round(time.monotonic() - w0, 1), "edges": n_edges,
            "cdn": {"ttl_s": sim.ttl_ns / 1e9, "limit_rps": sim.limit_rps, "burst": sim.burst,
                    "lockout_s": sim.lockout_s, **sim.stats}, "feed": feed or {}}
    (out_dir / "rehearsal.json").write_text(json.dumps(meta, indent=1))
    return meta
