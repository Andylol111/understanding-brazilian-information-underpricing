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

_CLARITY_EID: dict[str, str] = {}           # clarity spec -> election id, once found


async def _clarity_eid(web: httpx.AsyncClient, spec: str, m: int, d: int, y: int) -> str | None:
    """Election id for a clarity spec "BASE" or "BASE|LISTING_URL|TEXT_REGEX": the election dated m/d/y in
    BASE/elections.json, else the first link to BASE/<eid> on the listing page whose text matches the regex
    (SC's elections.json is empty; scvotes.gov links each election, e.g. "2026 General Election")."""
    if spec in _CLARITY_EID:
        return _CLARITY_EID[spec]
    base, _, rest = spec.partition("|")
    page, _, pattern = rest.partition("|")
    eid = None
    try:
        r = await web.get(f"{base}/elections.json")
        eids = [e["EID"] for e in r.json() if e.get("Date", "").startswith(f"{m}/{d}/{y} ")]
        eid = str(eids[0]) if eids else None
    except Exception:
        eid = None
    if eid is None and page:
        html = (await web.get(page)).text
        host = re.escape(base.split("://", 1)[-1])
        for mm in re.finditer(rf'href="(?:https?://)?{host}/(\d+)[^"]*"[^>]*>(.*?)</a>', html, re.S | re.I):
            if re.search(pattern, re.sub(r"<[^>]+>", "", mm.group(2)), re.I):
                eid = mm.group(1)
                break
    if eid is not None:
        _CLARITY_EID[spec] = eid
    return eid


async def _resolve(web: httpx.AsyncClient, clarity: list[str], ga_enr: list[str], date: str | None,
                   vt_static: list[str] = (), ncsbe: list[str] = ()) -> list[tuple[str, str]]:
    """Day-of URLs whose election id / version is only known once published. Returns (key, url) pairs.
      clarity: Clarity/Scytl ENR base, e.g. https://electionresults.iowa.gov/IA, optionally "BASE|LISTING_URL|REGEX"
               (see _clarity_eid). Data lives under a version that changes on every upload; records ALL.json
               (key clarity_<base>), en/summary.json (…_summary) and status.json (…_status).
      ga_enr:  Enhanced Voting jurisdiction URL, e.g. …/public/api/jurisdictions/Georgia; the election is
               the one with electionDate == `date`; records its per-county `localities`, the statewide
               `data` file, and the per-county breakdown (`data/ballot-item/<id>`) of Governor and US Senate.
      vt_static: Vermont results file static.electionresults.vermont.gov/elections/<guid>.json; records it
               (vt_static_main) and the statewide-offices file it points to, renamed on every update
               (vt_static_statewide).
      ncsbe:   NC State Board of Elections ENR base, e.g. https://er.ncsbe.gov/enr/20261103/data; records
               county.txt (ncsbe_county) and every county's results_<id>.txt (ncsbe_results_<id>), ids from county.txt."""
    out = []
    if not date:
        return out
    y, m, d = (int(x) for x in date.split("-"))
    for spec in clarity:
        base = spec.partition("|")[0]
        try:
            eid = await _clarity_eid(web, spec, m, d, y)
            if eid:
                ver = (await web.get(f"{base}/{eid}/current_ver.txt")).text.strip()
                key = f"clarity_{_name(base)}"
                out.append((key, f"{base}/{eid}/{ver}/json/ALL.json"))
                out.append((f"{key}_summary", f"{base}/{eid}/{ver}/json/en/summary.json"))
                out.append((f"{key}_status", f"{base}/{eid}/{ver}/json/status.json"))
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
    for url in vt_static:
        try:
            main = (await web.get(url)).json()
            root = url.split("/elections/")[0]
            out.append(("vt_static_main", url))
            path = (main.get("stateWide") or {}).get("path")
            if path:
                out.append(("vt_static_statewide", f"{root}/{path.replace(chr(92), '/')}"))
        except Exception as e:
            print(f"resolver {url}: {type(e).__name__}: {e}", flush=True)
    for base in ncsbe:
        try:
            out.append(("ncsbe_county", f"{base}/county.txt"))
            if base not in _NCSBE_IDS:
                txt = (await web.get(f"{base}/county.txt")).text
                _NCSBE_IDS[base] = sorted({int(c) for c in re.findall(r'"cid"\s*:\s*"(\d+)"', txt)} - {0})
            out += [(f"ncsbe_results_{i:03d}", f"{base}/results_{i}.txt") for i in _NCSBE_IDS[base]]
        except Exception as e:
            print(f"resolver {base}: {type(e).__name__}: {e}", flush=True)
    return out


_NCSBE_IDS: dict[str, list[int]] = {}         # NCSBE ENR base -> county ids (stable per election)


def _pm_tokens(pm_events: list[str]) -> dict[str, str]:
    """Resolve event slugs off the asyncio loop; Gamma is synchronous and may be slow."""
    tokens = {}
    for ev in pm_events:
        for m in polymarket.event(ev)["markets"]:
            if m.get("closed"):
                continue
            ids = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds") or []
            if ids:
                tokens[ids[0]] = f"{ev}/{m['slug']}"
    return tokens


async def run(tag: str, urls: list[str], pm_events: list[str], interval: float = 30.0,
              until: datetime | None = None, clarity: list[str] = (), ga_enr: list[str] = (),
              date: str | None = None, out_root: Path | None = None, vt_static: list[str] = (),
              ncsbe: list[str] = ()) -> None:
    out = (out_root or SNAP) / tag
    out.mkdir(parents=True, exist_ok=True)
    tokens = await asyncio.to_thread(_pm_tokens, pm_events)
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
                sem = asyncio.Semaphore(8)                   # NC alone resolves 101 files; ≤ 8 requests at a time

                async def get(u: str) -> httpx.Response:
                    async with sem:
                        return await web.get(u)
                pairs = await _resolve(web, list(clarity), list(ga_enr), date, list(vt_static), list(ncsbe))
                rs = await asyncio.gather(*(get(u) for _, u in pairs), return_exceptions=True)
                for (key, _), r in zip(pairs, rs):           # one resolved URL failing must not stop the rest
                    if isinstance(r, httpx.Response) and r.status_code == 200:
                        save(key, r.content, "json", t)
                    elif isinstance(r, Exception):
                        print(f"{t:%H:%M:%S} {key}: {type(r).__name__}: {r}", flush=True)
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
