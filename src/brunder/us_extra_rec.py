"""Passive recorder for the 2026 U.S. sources added on 2026-11-02.

It writes the standard ``brunder.us.Archive`` layout into its OWN archive (never the shared one the registered
processes read): ``<key>/<poll UTC>.<ext>[.gz]``, ``index.jsonl`` and ``.poll/extra.json``.  Requests use the
identified research user agent, obey robots.txt, are paced at no more than one request per second per
host, use conditional validators, and stop a host on a challenge/403/429 instead of bypassing it.

    python -m brunder.us_extra_rec --out data/raw/snap/us_midterms_2026_extra --until 2026-11-04T10:00:00Z
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import extra_markets, snap
from .paths import RAW
from .us_extra_sources import records

UA = "brunder/0.1 (research; recorder)"
DEFAULT_OUT = RAW / "snap" / "us_midterms_2026_extra"
DEFAULT_UNTIL = "2026-11-04T10:00:00Z"
MIN_INTERVAL = 30.0
HOST_GAP = 1.05
_CHALLENGE = re.compile(rb"captcha|challenge-platform|_incapsula_|cf-chl|access denied|just a moment|pardon our interruption", re.I)
_FORBIDDEN = {
    "api.sos.ca.gov", "goelect.txelections.civixapps.com", "liveresults.ohiosos.gov", "elections.alaska.gov",
    "results.sos.ga.gov", "app.enhancedvoting.com", "static.electionresults.vermont.gov",
    "flelectionfiles.floridados.gov", "er.ncsbe.gov", "electionreturns.pa.gov",
    "electionresultsfiles.sos.mn.gov", "civicapi.org",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


_FORBIDDEN_DOMAINS = ("azurewebsites.us", "sos.ga.gov", "ohiosos.gov", "sos.mn.gov", "sos.ca.gov", "civicapi.org")


def forbidden(host: str) -> bool:
    """Exact host-name rule (no substring matching): a listed host, or the domain / any subdomain of a listed
    domain. orresultswebservices.azureedge.us (the Oregon CDN) passes; orresws.azurewebsites.us does not. Redirects
    are never followed, so a CDN can not lead the recorder to a forbidden origin."""
    host = host.lower().rstrip(".")
    return host in _FORBIDDEN or any(host == d or host.endswith("." + d) for d in _FORBIDDEN_DOMAINS)


class Blocked(RuntimeError):
    pass


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.index = out / "index.jsonl"
        self.manifest = out / "extra_manifest.jsonl"
        self.web = httpx.Client(headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=40,
                                follow_redirects=False, http2=False)
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_req: dict[str, float] = {}
        self.blocked: dict[str, str] = {}
        self.state: dict[str, dict] = {}
        self.stats = {"requests": 0, "changed": 0, "not_modified": 0, "errors": 0}

    def log(self, **row) -> None:
        with self.manifest.open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    def pace(self, host: str) -> None:
        wait = self.last_req.get(host, 0.0) + HOST_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_req[host] = time.monotonic()

    def allowed(self, url: str, source: str) -> bool:
        u = urlsplit(url)
        host = u.hostname or ""
        if forbidden(host):
            raise Blocked(f"forbidden host {host}")
        origin = f"{u.scheme}://{u.netloc}"
        if host in self.blocked:
            raise Blocked(self.blocked[host])
        if origin not in self.robots:
            ru = origin + "/robots.txt"
            self.pace(host)
            t = now()
            try:
                r = self.web.get(ru)
            except httpx.HTTPError as e:
                self.log(ts=t.isoformat(), source=source, url=ru, status=0, note=f"robots {type(e).__name__}")
                return False
            self.stats["requests"] += 1
            self.log(ts=t.isoformat(), source=source, url=ru, status=r.status_code, bytes=len(r.content), note="robots")
            rp = urllib.robotparser.RobotFileParser()
            if r.status_code in (404, 410):
                rp.parse([])
            elif r.status_code == 200 and not self.challenge(r, "text"):
                rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403, 429) or self.challenge(r, "text"):
                self.stop_host(host, source, ru, f"robots HTTP {r.status_code} or challenge")
                raise Blocked(self.blocked[host])
            else:
                return False
            self.robots[origin] = rp
        return self.robots[origin].can_fetch(UA, url)

    @staticmethod
    def challenge(r: httpx.Response, expect: str) -> bool:
        h = {k.lower(): v for k, v in r.headers.items()}
        if r.status_code in (401, 403, 429) or h.get("cf-mitigated") or h.get("x-amzn-waf-action"):
            return True
        body = r.content
        if _CHALLENGE.search(body[:5000]):
            return True
        return (expect != "html" and r.status_code == 200
                and ("text/html" in h.get("content-type", "").lower()
                     or body.lstrip()[:20].lower().startswith((b"<!doctype html", b"<html"))))

    def stop_host(self, host: str, source: str, url: str, note: str) -> None:
        self.blocked[host] = note
        self.log(ts=now().isoformat(), event="blocked", source=source, url=url, status=0, note=note)
        print(f"extra recorder: {host} blocked: {note}", flush=True)

    def get(self, source: str, url: str, *, expect: str = "json", headers: dict | None = None,
            params=None, json_body=None, soft_429: bool = False) -> httpx.Response | None:
        """One paced, robots-checked request (POST when json_body is given). A 401/403 / challenge stops the host
        for the run (Blocked); a 429 does too unless soft_429 (market data: the caller backs off and retries)."""
        if not self.allowed(url, source):
            self.log(ts=now().isoformat(), source=source, url=url, status=0, note="robots disallow")
            return None
        host = urlsplit(url).hostname or ""
        self.pace(host)
        t = now()
        try:
            if json_body is not None:
                r = self.web.post(url, json=json_body, headers=headers, params=params)
            else:
                r = self.web.get(url, headers=headers, params=params)
        except httpx.HTTPError as e:
            self.stats["errors"] += 1
            self.log(ts=t.isoformat(), source=source, url=url, status=0, note=type(e).__name__)
            return None
        self.stats["requests"] += 1
        self.log(ts=t.isoformat(), source=source, url=url, status=r.status_code, bytes=len(r.content),
                 etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"),
                 cache_control=r.headers.get("cache-control"), retry_after=r.headers.get("retry-after"), note="fetch")
        if soft_429 and r.status_code == 429:
            return r
        if self.challenge(r, expect):
            self.stop_host(host, source, url, f"HTTP {r.status_code} / challenge response")
            raise Blocked(self.blocked[host])
        return r

    def resolve(self, module, spec: dict) -> str | None:
        fn = spec.get("resolve")
        if fn is None:
            return spec["url"]

        def fetch(url: str, expect: str = "json") -> bytes | None:
            r = self.get(module.NAME, url, expect=expect)
            return r.content if r is not None and r.status_code == 200 else None
        return fn(fetch)

    def store(self, key: str, ext: str, body: bytes, t: datetime, meta: dict) -> None:
        d = self.out / key
        d.mkdir(parents=True, exist_ok=True)
        zipped = len(body) >= 64_000
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.{ext}{'.gz' if zipped else ''}"
        tmpdir = self.out / ".tmp"
        tmpdir.mkdir(exist_ok=True)
        tmp = tmpdir / f"{key}.{f.name}"
        tmp.write_bytes(gzip.compress(body, 6) if zipped else body)
        os.replace(tmp, f)
        with self.index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(self.out)),
                                 "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                                 "gzip": zipped, **meta}, separators=(",", ":")) + "\n")
        self.stats["changed"] += 1

    def poll(self, module, spec: dict, t: datetime) -> None:
        key = spec["key"]
        s = self.state.setdefault(key, {})
        try:
            url = self.resolve(module, spec)
        except Blocked:
            s["last_status"] = "blocked"
            return
        if not url:
            s["last_status"] = "unresolved"
            return
        if s.get("url") != url:                 # a new (versioned) URL: the old validators do not apply
            s.update(url=url, etag=None, last_modified=None)
        hdr = {}
        if s.get("etag"):
            hdr["If-None-Match"] = s["etag"]
        if s.get("last_modified"):
            hdr["If-Modified-Since"] = s["last_modified"]
        try:
            r = self.get(module.NAME, url, expect=spec.get("ext", "json"), headers=hdr)
        except Blocked:
            s["last_status"] = "blocked"
            return
        if r is None:
            s["last_status"] = "error"
            return
        s["last_status"] = r.status_code
        if r.status_code == 304:
            self.stats["not_modified"] += 1
            s["last_ok"] = now()
            return
        if r.status_code != 200:
            self.stats["errors"] += 1
            return
        body = r.content
        sha = hashlib.sha256(body).hexdigest()
        s.update(last_ok=now(), etag=r.headers.get("etag") or s.get("etag"),
                 last_modified=r.headers.get("last-modified") or s.get("last_modified"))
        if s.get("sha") == sha:
            self.stats["not_modified"] += 1
            return
        s["sha"] = sha
        self.store(key, spec.get("ext", "json"), body, t,
                   {k: v for k, v in (("fetched", now().isoformat()), ("etag", r.headers.get("etag")),
                                      ("last_modified", r.headers.get("last-modified"))) if v is not None})

    def _save_json(self, key: str, doc: dict, t: datetime) -> None:
        s = self.state.setdefault(key, {})
        body = json.dumps(doc, sort_keys=True).encode()
        sha = hashlib.sha256(body).hexdigest()
        s["last_ok"] = now()
        if s.get("sha") == sha:
            self.stats["not_modified"] += 1
            return
        s["sha"] = sha
        self.store(key, "json", body, t, {"fetched": now().isoformat()})

    def _market_status(self, key: str, r: httpx.Response | None) -> None:
        s = self.state.setdefault(key, {})
        s["last_status"] = "error" if r is None else r.status_code
        ra = r.headers.get("retry-after", "") if r is not None else ""
        s["retry_after"] = float(ra) if ra.isdigit() else 0.0

    def poll_pm(self, t: datetime) -> None:
        """One POST /books for every added race's YES token -> `polymarket_books` {"event/slug": book} (the
        registered brunder.snap format)."""
        tokens = extra_markets.pm_tokens()
        if not tokens:
            return
        try:
            r = self.get("markets", extra_markets.CLOB_BOOKS, json_body=[{"token_id": k} for k in tokens],
                         soft_429=True)
        except Blocked:
            self.state.setdefault(extra_markets.PM_KEY, {})["last_status"] = "blocked"
            return
        self._market_status(extra_markets.PM_KEY, r)
        if r is None or r.status_code != 200:
            return
        books = {tokens[b["asset_id"]]: b for b in r.json() if b.get("asset_id") in tokens}
        if books:
            self._save_json(extra_markets.PM_KEY, books, t)

    def poll_kalshi(self, t: datetime) -> None:
        """One GET /markets/orderbooks for every added race's ticker -> `kalshi_books` {ticker: orderbook_fp} (the
        registered brunder.kalshi format)."""
        tickers = extra_markets.kalshi_tickers()
        if not tickers:
            return
        try:
            r = self.get("markets", extra_markets.KALSHI_BOOKS, params=[("tickers", x) for x in tickers],
                         soft_429=True)
        except Blocked:
            self.state.setdefault(extra_markets.KALSHI_KEY, {})["last_status"] = "blocked"
            return
        self._market_status(extra_markets.KALSHI_KEY, r)
        if r is None or r.status_code != 200:
            return
        books = {b["ticker"]: b.get("orderbook_fp") or {} for b in r.json().get("orderbooks") or []}
        if books:
            self._save_json(extra_markets.KALSHI_KEY, books, t)

    def run(self, until: datetime, only: tuple[str, ...] = (), skip: tuple[str, ...] = ()) -> None:
        market_specs = [(None, {"key": extra_markets.PM_KEY, "interval": extra_markets.INTERVAL, "poll": self.poll_pm}),
                        (None, {"key": extra_markets.KALSHI_KEY, "interval": extra_markets.INTERVAL,
                                "poll": self.poll_kalshi})]
        specs = [(m, s) for m, s in records() + market_specs if (not only or s["key"].startswith(only))
                 and not (skip and s["key"].startswith(skip))]
        if not specs:
            raise ValueError(f"no RECORD spec matches {only}")
        due = {spec["key"]: 0.0 for _, spec in specs}
        while now() < until:
            m = time.monotonic()
            for module, spec in specs:
                key = spec["key"]
                if m < due[key] or now() >= until:
                    continue
                if "poll" in spec:
                    spec["poll"](now())
                else:
                    self.poll(module, spec, now())
                every = max(MIN_INTERVAL, float(spec.get("interval", 60)))
                s = self.state.setdefault(key, {})
                st = s.get("last_status")
                # transport errors / 429 / 5xx: back off (doubling, <= 600 s, never shorter than a Retry-After);
                # 200 / 304 / 404 keep the normal interval
                bad = st == "error" or (isinstance(st, int) and (st >= 500 or st == 429))
                s["fails"] = s.get("fails", 0) + 1 if bad else 0
                due[key] = time.monotonic() + max(min(600.0, every * 2 ** s["fails"]), s.pop("retry_after", 0.0))
            next_due = min(due.values(), default=m + 5)
            wait = max(0.25, min(5.0, next_due - time.monotonic(), (until - now()).total_seconds()))
            feeds = {key: {"interval": max(MIN_INTERVAL, float(spec.get("interval", 60))),
                           "last_ok": self.state.get(key, {}).get("last_ok").isoformat()
                           if self.state.get(key, {}).get("last_ok") else None,
                           "last_status": self.state.get(key, {}).get("last_status"),
                           "fails": self.state.get(key, {}).get("fails", 0)}
                     for _, spec in specs for key in [spec["key"]]}
            snap.mark(self.out, "extra", now() + timedelta(seconds=wait), min([MIN_INTERVAL] +
                      [max(MIN_INTERVAL, float(s.get("interval", 60))) for _, s in specs]), feeds)
            time.sleep(wait)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--until", default=DEFAULT_UNTIL)
    p.add_argument("--only", action="append", default=[],
                   help="record only RECORD keys with this prefix (repeatable), e.g. a Mac-side run of server-blocked hosts")
    p.add_argument("--skip", action="append", default=[], help="do not record RECORD keys with this prefix (repeatable)")
    a = p.parse_args(argv)
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00"))
    rec = Recorder(a.out)
    try:
        rec.run(until, tuple(a.only), tuple(a.skip))
    finally:
        print(f"extra recorder: {rec.stats}; blocked={rec.blocked}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
