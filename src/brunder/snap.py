"""Passive election-night recorder: snapshot URLs and Polymarket order books on change.

Used for counts whose timing would otherwise be lost (a live results feed only shows its current
state). Every `interval` seconds it fetches each URL and each market's book; a copy is written only when
the content changed, named by fetch time (UTC, microseconds), with a JSONL index of every change.
Nothing is modelled or traded; this only builds development data for later replays.

    brunder snap israel_2026 --url https://votes26.bechirot.gov.il/ --pm-event <slug> --until 2026-10-28T12:00Z
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import _http, live, polymarket
from .paths import RAW

SNAP = RAW / "snap"
UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}


def _name(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])[:120]


async def run(tag: str, urls: list[str], pm_events: list[str], interval: float = 30.0,
              until: datetime | None = None) -> None:
    out = SNAP / tag
    out.mkdir(parents=True, exist_ok=True)
    tokens = {}
    for ev in pm_events:
        for m in polymarket.event(ev)["markets"]:
            if m.get("closed"):
                continue
            ids = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds") or []
            if ids:
                tokens[ids[0]] = f"{ev}/{m['slug']}"
    print(f"snap {tag}: {len(urls)} urls, {len(tokens)} markets, every {interval:.0f}s until {until}", flush=True)
    last: dict[str, str] = {}
    index = out / "index.jsonl"

    def save(key: str, body: bytes, ext: str, t: datetime) -> None:
        h = hashlib.sha256(body).hexdigest()
        if last.get(key) == h:
            return
        last[key] = h
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.{ext}"
        f.write_bytes(body)
        with index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(out)), "sha256": h,
                                 "bytes": len(body)}) + "\n")

    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as web, _http.aclient() as pm:
        while until is None or datetime.now(timezone.utc) < until:
            t = datetime.now(timezone.utc)
            try:
                rs = await asyncio.gather(*(web.get(u, params={"t": int(t.timestamp())}) for u in urls),
                                          return_exceptions=True)
                for u, r in zip(urls, rs):
                    if isinstance(r, httpx.Response) and r.status_code == 200:
                        ext = "json" if "json" in r.headers.get("content-type", "") or u.endswith(".json") else "html"
                        save(_name(u), r.content, ext, t)
                if tokens:
                    books = await live.fetch_books(pm, list(tokens))
                    if books:
                        save("polymarket_books", json.dumps({tokens[k]: v for k, v in books.items()}, sort_keys=True).encode(),
                             "json", t)
            except Exception as e:                                   # never stop on the night
                print(f"{t:%H:%M:%S} snap error, continuing: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(max(0.0, interval - (datetime.now(timezone.utc) - t).total_seconds()))
