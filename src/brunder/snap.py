"""Passive election-night recorder: snapshot URLs and Polymarket order books on change.

Used for counts whose timing would otherwise be lost (a live results feed only shows its current
state). Every `interval` seconds it fetches each URL and each market's book; a copy is written only when
the content changed, named by fetch time (UTC, microseconds), with a JSONL index of every change.
Nothing is modelled or traded; this only builds development data for later replays.

    brunder snap israel_2026 --url https://votes26.bechirot.gov.il/ --pm-event <slug> --until 2026-10-28T12:00Z
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
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


_GA_ITEMS: dict[str, list[str]] = {}       # election data URL -> ballot-item ids to record (stable per election)
_GA_RACES = ("governor", "us senate", "u.s. senate", "united states senate")


def _ga_item_ids(data: dict) -> list[str]:
    """Ballot items worth recording county breakdowns for: Governor and US Senate (names are language lists)."""
    ids = []
    for item in data.get("ballotItems") or []:
        names = item.get("name") or []
        text = " ".join(n.get("text", "") for n in names if isinstance(n, dict)).lower()
        if any(text.startswith(r) for r in _GA_RACES):
            ids.append(item["id"])
    return ids

async def _resolve(web: httpx.AsyncClient, clarity: list[str], ga_enr: list[str], date: str | None) -> list[tuple[str, str]]:
    """Day-of URLs whose election id / version is only known once published. Returns (key, url) pairs.
      clarity: Clarity/Scytl ENR base, e.g. https://electionresults.iowa.gov/IA. The election is the one
               dated `date` in elections.json; data lives under a version that changes on every upload.
      ga_enr:  Enhanced Voting jurisdiction URL, e.g. …/public/api/jurisdictions/Georgia; the election is
               the one with electionDate == `date`; records its per-county `localities`, the statewide
               `data` file, and the per-county breakdown (`data/ballot-item/<id>`) of Governor and US Senate."""
    out = []
    if not date:
        return out
    y, m, d = (int(x) for x in date.split("-"))
    for base in clarity:
        try:
            r = await web.get(f"{base}/elections.json")
            eids = [e["EID"] for e in r.json() if e.get("Date", "").startswith(f"{m}/{d}/{y} ")]
            if eids:
                ver = (await web.get(f"{base}/{eids[0]}/current_ver.txt")).text.strip()
                out.append((f"clarity_{_name(base)}", f"{base}/{eids[0]}/{ver}/json/ALL.json"))
        except Exception as e:                       # one source failing must not stop the others
            print(f"resolver {base}: {type(e).__name__}: {e}", flush=True)
    for url in ga_enr:
        try:
            j = (await web.get(url)).json()
            ids = [e["publicElectionId"] for e in j.get("elections", []) if e.get("electionDate") == date]
            if ids:
                api = url.split("/jurisdictions/")[0]
                name = url.rsplit("/", 1)[-1]
                base = f"{api}/elections/{name}/{ids[0]}"
                out.append((f"ga_enr_{name}_localities", f"{base}/localities"))
                out.append((f"ga_enr_{name}_data", f"{base}/data"))
                if not _GA_ITEMS.get(base):
                    _GA_ITEMS[base] = _ga_item_ids((await web.get(f"{base}/data")).json())
                out += [(f"ga_enr_{name}_item_{i}", f"{base}/data/ballot-item/{i}") for i in _GA_ITEMS[base]]
        except Exception as e:
            print(f"resolver {url}: {type(e).__name__}: {e}", flush=True)
    return out


async def run(tag: str, urls: list[str], pm_events: list[str], interval: float = 30.0,
              until: datetime | None = None, clarity: list[str] = (), ga_enr: list[str] = (),
              date: str | None = None) -> None:
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
        big = len(body) > 1_000_000                       # e.g. Texas county files, ~5 MB each
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.{ext}{'.gz' if big else ''}"
        # write elsewhere, then rename: readers scanning `d` (the live model) only ever see complete files
        tmp = out / ".tmp" / f"{key}.{f.name}"
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(gzip.compress(body) if big else body)
        os.replace(tmp, f)
        with index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(out)), "sha256": h,
                                 "bytes": len(body), "gzip": big}) + "\n")

    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as web, _http.aclient() as pm:
        while until is None or datetime.now(timezone.utc) < until:
            t = datetime.now(timezone.utc)
            # each stage isolated: a failing feed or resolver must not cost the other sources this poll
            try:
                # merge the cache-buster into any existing query (passing params= would replace it)
                rs = await asyncio.gather(*(web.get(httpx.URL(u).copy_merge_params({"t": int(t.timestamp())}))
                                            for u in urls), return_exceptions=True)
                for u, r in zip(urls, rs):
                    if isinstance(r, httpx.Response) and r.status_code == 200:
                        ext = "json" if "json" in r.headers.get("content-type", "") or u.endswith(".json") else "html"
                        save(_name(u), r.content, ext, t)
            except Exception as e:
                print(f"{t:%H:%M:%S} url error, continuing: {type(e).__name__}: {e}", flush=True)
            try:
                for key, u in await _resolve(web, list(clarity), list(ga_enr), date):
                    r = await web.get(u)
                    if r.status_code == 200:
                        save(key, r.content, "json", t)
            except Exception as e:
                print(f"{t:%H:%M:%S} resolver error, continuing: {type(e).__name__}: {e}", flush=True)
            try:
                if tokens:
                    books = await live.fetch_books(pm, list(tokens))
                    if books:
                        save("polymarket_books", json.dumps({tokens[k]: v for k, v in books.items()}, sort_keys=True).encode(),
                             "json", t)
            except Exception as e:                                   # never stop on the night
                print(f"{t:%H:%M:%S} books error, continuing: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(max(0.0, interval - (datetime.now(timezone.utc) - t).total_seconds()))
