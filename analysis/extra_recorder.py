"""Passive recorder for three extra election-night sources, one request at a time, every 60 s.

  va  Virginia Enhanced Voting (enr.elections.virginia.gov): the election document (absentee-by-method statistics,
      registration by locality) and the ballot items of the U.S. Senate race and the 11 U.S. House races
      (county x candidate x vote method).
  ia  Iowa Clarity (electionresults.iowa.gov, 2026 General EID 127077): current_ver.txt every cycle; when the
      version changes, json/en/summary.json and reports/detailxml.zip.
  sc  South Carolina Clarity (www.enr-scvotes.org): elections.json tried once; on a block/challenge the source is
      recorded as blocked and never requested again (also not after a restart); on 200 without a 2026-11-03
      election the listing is re-read every 10 min; with an EID it is polled like Iowa.

Output: <out>/<source>/<UTC timestamp>.<ext>.gz (one file per changed response) and <out>/manifest.jsonl with one
line per request: ts, source, url, status, bytes, sha256, version, file, note (+ event="blocked"). Conditional GET
(If-None-Match / If-Modified-Since) when the server sent validators; an unchanged body (same sha256) is not stored
again. Honest user agent, robots.txt per host, no retry loop, no bypass: 401/403/429, an empty 202, a challenge
header or an HTML page where data was expected stops that source. Exits by itself at --until.

    uv run python analysis/extra_recorder.py --out /srv/brunder/extrarec --until 2026-11-04T12:00:00Z
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import resource
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UA = "brunder/0.1 (research; recorder)"
INTERVAL = 60.0          # seconds between two requests to the same URL
GAP = 1.5                # seconds between any two requests
VA_BASE = "https://enr.elections.virginia.gov/results/public/api/elections/virginia/2026-November-General/data"
IA_BASE = "https://electionresults.iowa.gov/IA/127077"
SC_BASE = "https://www.enr-scvotes.org/SC"
SC_RETRY = 600.0
DEFAULT_OUT = "/srv/brunder/extrarec"
DEFAULT_UNTIL = "2026-11-04T12:00:00Z"
_CHALLENGE_BODY = re.compile(rb"captcha|challenge-platform|_incapsula_|cf-chl|access denied|just a moment", re.I)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(t: datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%SZ")


class Blocked(Exception):
    pass


class Recorder:
    def __init__(self, out: Path, until: datetime, interval: float = INTERVAL, gap: float = GAP):
        self.out, self.until, self.interval, self.gap = out, until, interval, gap
        self.out.mkdir(parents=True, exist_ok=True)
        self.manifest = self.out / "manifest.jsonl"
        self.web = httpx.Client(headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=40.0,
                                follow_redirects=False, http2=False)
        self.valid: dict[str, dict] = {}       # url -> {etag, last_modified, sha}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.stopped: dict[str, str] = self._read_blocked()
        self.n_requests = 0
        self.stats: dict[int, int] = {}
        self.bytes = 0
        self._last_req = 0.0

    # ------------------------------------------------------------------------------------------ manifest
    def _read_blocked(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.manifest.exists():
            for ln in self.manifest.read_text().splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("event") == "blocked":
                    out[r["source"]] = r.get("note", "")
        return out

    def log(self, **row) -> None:
        with self.manifest.open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    def store(self, source: str, ext: str, body: bytes, t: datetime) -> str:
        d = self.out / source
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{stamp(t)}.{ext}.gz"
        n = 1
        while p.exists():                  # two answers within one second (cannot happen at the 1.5 s pace)
            p = d / f"{stamp(t)}-{n}.{ext}.gz"
            n += 1
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(gzip.compress(body, 6))
        os.replace(tmp, p)
        return str(p.relative_to(self.out))

    # ------------------------------------------------------------------------------------------ http
    def _pace(self) -> None:
        wait = self._last_req + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.monotonic()

    def _count(self, status: int, n: int) -> None:
        self.n_requests += 1
        self.stats[status] = self.stats.get(status, 0) + 1
        self.bytes += n

    def allowed(self, url: str, source: str) -> bool:
        """robots.txt of the host (fetched once per run). 404/410 = no restrictions; 401/403 or a challenge stops
        the source; other failures: not allowed this cycle."""
        u = urlsplit(url)
        host = f"{u.scheme}://{u.netloc}"
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            self._pace()
            t = now()
            try:
                r = self.web.get(host + "/robots.txt")
            except httpx.HTTPError as e:
                self.log(ts=t.isoformat(), source=source, url=host + "/robots.txt", status=0, bytes=0, sha256=None,
                         version=None, file=None, note=f"error {type(e).__name__}")
                return False
            self._count(r.status_code, len(r.content))
            self.log(ts=t.isoformat(), source=source, url=host + "/robots.txt", status=r.status_code,
                     bytes=len(r.content), sha256=hashlib.sha256(r.content).hexdigest(), version=None, file=None,
                     note="robots")
            if r.status_code in (404, 410):
                rp.parse([])
            elif r.status_code == 200 and b"<html" not in r.content[:300].lower():
                rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403, 429, 202) or r.status_code == 200:
                self.block(source, host + "/robots.txt", r.status_code, f"robots.txt answered {r.status_code}")
                raise Blocked(source)
            else:
                return False
            self.robots[host] = rp
        rp = self.robots[host]
        return rp is not None and rp.can_fetch(UA, url)

    def block(self, source: str, url: str, status: int, why: str) -> None:
        self.stopped[source] = why
        self.log(ts=now().isoformat(), source=source, url=url, status=status, bytes=0, sha256=None, version=None,
                 file=None, event="blocked", note=f"blocked: {why}")
        print(f"[{stamp(now())}] {source} BLOCKED ({status}): {why}", file=sys.stderr, flush=True)

    def fetch(self, source: str, url: str, ext: str, *, expect: str, version: str | None = None) -> tuple[int, bytes | None]:
        """One conditional GET. Returns (status, body) with body None when nothing new (304, unchanged sha,
        error). Raises Blocked after recording a block/challenge."""
        if not self.allowed(url, source):
            self.log(ts=now().isoformat(), source=source, url=url, status=0, bytes=0, sha256=None, version=version,
                     file=None, note="robots disallow or robots unavailable")
            return 0, None
        v = self.valid.setdefault(url, {})
        hdr = {}
        if v.get("etag"):
            hdr["If-None-Match"] = v["etag"]
        if v.get("last_modified"):
            hdr["If-Modified-Since"] = v["last_modified"]
        self._pace()
        t = now()
        try:
            r = self.web.get(url, headers=hdr)
        except httpx.HTTPError as e:
            self.log(ts=t.isoformat(), source=source, url=url, status=0, bytes=0, sha256=None, version=version,
                     file=None, note=f"error {type(e).__name__}")
            return 0, None
        body = r.content
        self._count(r.status_code, len(body))
        sha = hashlib.sha256(body).hexdigest() if body else None
        base = dict(ts=t.isoformat(), source=source, url=url, status=r.status_code, bytes=len(body), sha256=sha,
                    version=version or r.headers.get("etag") or r.headers.get("last-modified"))
        why = self._challenge(r, body, expect)
        if why:
            self.block(source, url, r.status_code, why)
            self.log(**base, file=None, note="challenge response")
            raise Blocked(source)
        if r.status_code == 304:
            self.log(**base, file=None, note="not modified")
            return 304, None
        if r.status_code != 200:
            self.log(**base, file=None, note="unexpected status")
            return r.status_code, None
        v["etag"], v["last_modified"] = r.headers.get("etag"), r.headers.get("last-modified")
        if v.get("sha") == sha:
            self.log(**base, file=None, note="unchanged")
            return 200, None
        v["sha"] = sha
        self.log(**base, file=self.store(source, ext, body, t), note="stored")
        return 200, body

    @staticmethod
    def _challenge(r: httpx.Response, body: bytes, expect: str) -> str | None:
        if r.status_code in (401, 403, 429):
            return f"HTTP {r.status_code}"
        if r.status_code == 202 and not body:
            return "empty 202 (bot challenge)"
        h = {k.lower(): v for k, v in r.headers.items()}
        if h.get("cf-mitigated") or h.get("x-amzn-waf-action"):
            return "challenge header"
        if r.status_code == 200:
            ct = h.get("content-type", "")
            if expect != "html" and ("text/html" in ct or body[:200].lstrip().lower().startswith((b"<!doctype html", b"<html"))):
                return "HTML page instead of data" + (" (challenge markers)" if _CHALLENGE_BODY.search(body[:4000]) else "")
        return None

    # ------------------------------------------------------------------------------------------ sources
    def cycle_va(self, st: dict) -> None:
        status, body = self.fetch("va", f"{VA_BASE}", "main.json", expect="json")
        if body:
            try:
                items = json.loads(body).get("ballotItems") or []
            except ValueError:
                items = []
            ids = []
            for b in items:
                name = next((n["text"] for n in b.get("name", []) if n.get("languageId") == "en"), "")
                if re.search(r"United States Senate|House of Representatives", name):
                    ids.append(b["id"])
            if ids:
                st["items"] = ids
        for i in st.get("items", []):
            self.fetch("va", f"{VA_BASE}/ballot-item/{i}", f"item-{i[-12:]}.json", expect="json")

    def cycle_clarity(self, source: str, base: str, st: dict) -> None:
        status, body = self.fetch(source, f"{base}/current_ver.txt", "current_ver.txt", expect="text")
        if body is None:
            return
        ver = body.decode("ascii", "replace").strip()
        if not re.fullmatch(r"\d+", ver) or ver == st.get("ver"):
            return
        st["ver"] = ver
        for rel, ext, expect in (("json/en/summary.json", "summary.json", "json"),
                                 ("reports/detailxml.zip", "detailxml.zip", "zip")):
            # a new version path is a new URL: no validators, no sha carry-over
            self.fetch(source, f"{base}/{ver}/{rel}", ext, expect=expect, version=ver)

    def cycle_sc(self, st: dict) -> None:
        if st.get("base"):
            self.cycle_clarity("sc", st["base"], st)
            return
        if time.monotonic() < st.get("retry", 0):
            return
        st["retry"] = time.monotonic() + SC_RETRY
        status, body = self.fetch("sc", f"{SC_BASE}/elections.json", "elections.json", expect="json")
        if body is None:
            body = st.get("last_listing")
            if body is None:
                return
        st["last_listing"] = body
        try:
            lst = json.loads(body)
        except ValueError:
            return
        eids = [e["EID"] for e in lst if str(e.get("Date", "")).startswith("11/3/2026")]
        if eids:
            st["base"] = f"{SC_BASE}/{eids[0]}"
            print(f"[{stamp(now())}] sc eid {eids[0]}", file=sys.stderr, flush=True)

    # ------------------------------------------------------------------------------------------ main loop
    def run(self, sources: list[str]) -> None:
        state = {s: {} for s in sources}
        fns = {"va": self.cycle_va, "ia": lambda st: self.cycle_clarity("ia", IA_BASE, st), "sc": self.cycle_sc}
        while now() < self.until:
            t0 = time.monotonic()
            for s in sources:
                if s in self.stopped or now() >= self.until:
                    continue
                try:
                    fns[s](state[s])
                except Blocked:
                    pass
            if all(s in self.stopped for s in sources):
                print("all sources stopped", file=sys.stderr, flush=True)
                return
            rest = min(t0 + self.interval - time.monotonic(), (self.until - now()).total_seconds())
            if rest > 0:
                time.sleep(rest)

    def summary(self) -> str:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return (f"requests={self.n_requests} statuses={dict(sorted(self.stats.items()))} bytes={self.bytes} "
                f"cpu_user={ru.ru_utime:.2f}s cpu_sys={ru.ru_stime:.2f}s maxrss_kb={ru.ru_maxrss} "
                f"stopped={self.stopped}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--until", default=DEFAULT_UNTIL, help="ISO UTC time at which the process exits")
    ap.add_argument("--sources", default="va,ia,sc")
    ap.add_argument("--interval", type=float, default=INTERVAL)
    a = ap.parse_args(argv)
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00"))
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    rec = Recorder(Path(a.out), until, interval=max(a.interval, INTERVAL))
    try:
        rec.run([s for s in a.sources.split(",") if s])
    finally:
        print(rec.summary(), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
