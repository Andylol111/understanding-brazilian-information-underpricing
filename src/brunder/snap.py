"""Passive election-night recorder: snapshot URLs and Polymarket order books on change.

Used for counts whose timing would otherwise be lost (a live results feed only shows its current
state). Every `interval` seconds it fetches each URL (a URL matching a --feed-interval prefix at that interval
instead, never below 10 s) and each market's book; a copy is written only when the content changed, named by the poll's start time
(UTC, microseconds), with a JSONL index of every change that also records when the response arrived and the
server's Last-Modified / ETag. GETs are conditional on the validators a feed last returned (an unchanged file is a
304, no body); a blocked / throttled / failing feed backs off on its own (exponential, jittered, ≤ 10 min). Before
each sleep it writes <tag>/.poll/snap.json (see `mark`), including every feed's last success.
Nothing is modelled or traded; this only builds development data for later replays.

    brunder snap israel_2026 --url https://votes26.bechirot.gov.il/ --pm-event <slug> --until 2026-10-28T12:00Z
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
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


_GA_HOUSE = ("us house", "u.s. house", "u.s. representative", "us representative", "united states representative")
_GA_HOUSE_ITEMS: dict[str, list[str]] = {}  # election data URL -> US House ballot-item ids (brunder.us_house_night)


def _ga_house_ids(data: dict) -> list[str]:
    """US House ballot items (recorded under their own key prefix, ga_enr_<name>_house_<id>)."""
    ids = []
    for item in data.get("ballotItems") or []:
        names = item.get("name") or []
        text = " ".join(n.get("text", "") for n in names if isinstance(n, dict)).lower()
        if any(text.startswith(r) for r in _GA_HOUSE):
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
                if base not in _GA_HOUSE_ITEMS:
                    _GA_HOUSE_ITEMS[base] = _ga_house_ids((await web.get(f"{base}/data")).json())
                out += [(f"ga_enr_{name}_house_{i}", f"{base}/data/ballot-item/{i}") for i in _GA_HOUSE_ITEMS[base]]
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


FULL_EVERY = 20                  # an unconditional GET at least every 20th fetch of a feed: a validator that lies cannot hide a change
BACKOFF_CAP = 600.0              # s; failure backoff: the feed's interval doubled per consecutive failure, jittered, ≤ 10 min
MIN_INTERVAL = 10.0              # s; no feed is ever polled faster than this
FAIL_STATUS = {403, 429}         # besides 5xx and transport errors: blocked / throttled, back off


class Feed:
    """Poll state of one URL: its own interval, the validators the server gave (ETag / Last-Modified: the next GET is
    conditional, an unchanged file costs a 304 and no body), failure backoff, last success (for stale status)."""

    def __init__(self, key: str, url: str, interval: float, bust: bool = False):
        self.key, self.url, self.interval, self.bust = key, url, interval, bust
        self.etag = self.modified = None
        self.due, self.fails, self.n = 0.0, 0, 0
        self.last_ok: datetime | None = None
        self.last_status: int | str | None = None

    def request(self, t: datetime) -> tuple[httpx.URL, dict[str, str]]:
        u = httpx.URL(self.url)
        if self.bust:                        # merge the cache-buster into any existing query (params= would replace it)
            u = u.copy_merge_params({"t": int(t.timestamp())})
        h = {}
        if (self.etag or self.modified) and self.n % FULL_EVERY:
            if self.etag:
                h["If-None-Match"] = self.etag
            if self.modified:
                h["If-Modified-Since"] = self.modified
        return u, h

    def done(self, m: float, tick: float, r: httpx.Response | None = None) -> None:
        """Schedule the next fetch at the normal interval; a 200 / 304 resets the backoff and counts as success."""
        self.due = m + self.interval - 0.25 * tick
        if r is not None:
            self.last_status = r.status_code
        if r is not None and r.status_code in (200, 304):
            self.fails = 0
            self.last_ok = datetime.now(timezone.utc)
            self.etag = r.headers.get("etag") or self.etag
            self.modified = r.headers.get("last-modified") or self.modified

    def failed(self, m: float, tick: float, r: httpx.Response | None = None, err: str | None = None) -> float:
        """Exponential backoff with jitter: interval × 2^fails × U(0.5, 1), at least the interval, ≤ BACKOFF_CAP,
        never shorter than a Retry-After."""
        self.fails += 1
        self.last_status = r.status_code if r is not None else err
        ra = r.headers.get("retry-after", "") if r is not None else ""
        base = min(BACKOFF_CAP, self.interval * 2 ** self.fails)
        wait = max(float(ra) if ra.isdigit() else 0.0, self.interval, base * random.uniform(0.5, 1.0))
        self.due = m + wait - 0.25 * tick
        return wait

    def status(self) -> dict:
        return {"interval": self.interval, "last_ok": self.last_ok.isoformat() if self.last_ok else None,
                "last_status": self.last_status, "fails": self.fails}


def mark(out: Path, name: str, nxt: datetime, interval: float, feeds: dict[str, dict] | None = None) -> None:
    """<out>/.poll/<name>.json: the recorder is idle until `nxt` (its next poll's start), so every snapshot it names
    before then is on disk. brunder.us.run_live computes a minute row only once that holds for every recorder.
    `feeds`: per snapshot key {interval, last_ok, last_status, fails} (the live model's stale-feed status)."""
    d = out / ".poll"
    d.mkdir(exist_ok=True)
    tmp = d / f".{name}.tmp"
    tmp.write_text(json.dumps({"next": nxt.isoformat(), "done": datetime.now(timezone.utc).isoformat(),
                               "interval": interval, **({"feeds": feeds} if feeds is not None else {})}))
    os.replace(tmp, d / f"{name}.json")


def feed_intervals(specs: list[str]) -> list[tuple[str, float]]:
    """--feed-interval "URL_PREFIX=SECONDS" -> [(prefix, seconds)]; never below MIN_INTERVAL."""
    out = []
    for x in specs:
        pre, _, sec = x.rpartition("=")
        if not pre:
            raise ValueError(f"--feed-interval {x!r}: expected URL_PREFIX=SECONDS")
        out.append((pre, max(MIN_INTERVAL, float(sec))))
    return out


def _meta(r: httpx.Response, fetched: datetime, ms: float) -> dict:
    h = r.headers
    return {k: v for k, v in (("fetched", fetched.isoformat()), ("http_ms", round(ms)),
                              ("last_modified", h.get("last-modified")), ("etag", h.get("etag")),
                              ("date", h.get("date"))) if v is not None}


async def run(tag: str, urls: list[str], pm_events: list[str], interval: float = 30.0,
              until: datetime | None = None, clarity: list[str] = (), ga_enr: list[str] = (),
              date: str | None = None, out_root: Path | None = None, vt_static: list[str] = (),
              ncsbe: list[str] = (), intervals: list[tuple[str, float]] = ()) -> None:
    """Record every feed each `interval` s; a feed whose URL starts with a prefix in `intervals` ([(prefix, s)],
    feed_intervals) uses that interval instead (≥ MIN_INTERVAL). Each feed's GET is conditional on the ETag /
    Last-Modified it last returned; the index line of every saved snapshot also carries `fetched` (UTC, when the
    response arrived; `t` stays the poll's start), `http_ms`, `last_modified`, `etag` and the server's `date`. A 403 /
    429 / 5xx / transport error backs only that feed off (Feed.failed); a 200 / 304 resets it. The poll marker carries
    every feed's last success and status."""
    out = (out_root or SNAP) / tag
    out.mkdir(parents=True, exist_ok=True)
    intervals = [(p, max(MIN_INTERVAL, sec)) for p, sec in intervals]

    def every(u: str) -> float:
        return next((sec for p, sec in intervals if u.startswith(p)), interval)
    tick = min([interval] + [sec for _, sec in intervals])
    tokens = await asyncio.to_thread(_pm_tokens, pm_events)
    print(f"snap {tag}: {len(urls)} urls, {len(tokens)} markets, every {interval:.0f}s "
          f"({', '.join(f'{p} every {sec:.0f}s' for p, sec in intervals) or 'no per-feed intervals'}) until {until}",
          flush=True)
    last: dict[str, str] = {}
    index = out / "index.jsonl"
    url_feeds = [Feed(_name(u), u, every(u), bust=True) for u in urls]
    books_feed = Feed("polymarket_books", "", interval)
    res_feeds: dict[str, Feed] = {}
    stats = {"req": 0, "304": 0, "changed": 0, "err": 0}

    def save(key: str, body: bytes, ext: str, t: datetime, meta: dict | None = None) -> bool:
        h = hashlib.sha256(body).hexdigest()
        if last.get(key) == h:
            return False
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
                                 "bytes": len(body), "gzip": big, **(meta or {})}) + "\n")
        return True

    async def fetch(web: httpx.AsyncClient, f: Feed, t: datetime, m: float, sem: asyncio.Semaphore | None,
                    ext: str | None) -> None:
        u, h = f.request(t)
        t0 = time.monotonic()
        try:
            if sem:
                async with sem:
                    r = await web.get(u, headers=h)
            else:
                r = await web.get(u, headers=h)
        except Exception as e:
            stats["err"] += 1
            wait = f.failed(m, tick, err=type(e).__name__)
            print(f"{t:%H:%M:%S} {f.key}: {type(e).__name__}: {e}; backing off {wait:.0f}s", flush=True)
            return
        f.n += 1
        stats["req"] += 1
        fetched, ms = datetime.now(timezone.utc), (time.monotonic() - t0) * 1000
        if r.status_code == 304:
            stats["304"] += 1
            f.done(m, tick, r)
        elif r.status_code == 200:
            f.done(m, tick, r)
            kind = ext or ("json" if "json" in r.headers.get("content-type", "") or f.url.endswith(".json") else "html")
            if save(f.key, r.content, kind, t, _meta(r, fetched, ms)):
                stats["changed"] += 1
        elif r.status_code in FAIL_STATUS or r.status_code >= 500:
            stats["err"] += 1
            print(f"{t:%H:%M:%S} {f.key}: HTTP {r.status_code}; backing off {f.failed(m, tick, r):.0f}s", flush=True)
        else:
            f.done(m, tick, r)                           # 404 …: not published (yet); retry at the normal interval

    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as web, _http.aclient() as pm:
        pairs: list[tuple[str, str]] = []
        resolve_due = books_due = stat_due = 0.0
        while until is None or datetime.now(timezone.utc) < until:
            t = datetime.now(timezone.utc)
            m = time.monotonic()
            # each stage isolated: a failing feed or resolver must not cost the other sources this poll
            try:
                await asyncio.gather(*(fetch(web, f, t, m, None, None) for f in url_feeds if f.due <= m))
            except Exception as e:
                print(f"{t:%H:%M:%S} url error, continuing: {type(e).__name__}: {e}", flush=True)
            if resolve_due <= m:
                try:
                    sem = asyncio.Semaphore(8)               # NC alone resolves 101 files; ≤ 8 requests at a time
                    pairs = await _resolve(web, list(clarity), list(ga_enr), date, list(vt_static), list(ncsbe))
                    for key, u in pairs:
                        f = res_feeds.get(key)
                        if f is None or f.url != u:          # new feed, or a new version URL: its validators are void
                            res_feeds[key] = Feed(key, u, every(u))
                    await asyncio.gather(*(fetch(web, res_feeds[k], t, m, sem, "json") for k, _ in pairs
                                           if res_feeds[k].due <= m))
                except Exception as e:
                    print(f"{t:%H:%M:%S} resolver error, continuing: {type(e).__name__}: {e}", flush=True)
                resolve_due = m + interval - 0.25 * tick
            if books_due <= m and tokens:
                try:
                    books = await live.fetch_books(pm, list(tokens))
                    if books:
                        save("polymarket_books", json.dumps({tokens[k]: v for k, v in books.items()}, sort_keys=True).encode(),
                             "json", t, {"fetched": datetime.now(timezone.utc).isoformat()})
                        books_feed.last_ok, books_feed.last_status = datetime.now(timezone.utc), 200
                except Exception as e:                                   # never stop on the night
                    books_feed.last_status = type(e).__name__
                    print(f"{t:%H:%M:%S} books error, continuing: {type(e).__name__}: {e}", flush=True)
                books_due = m + interval - 0.25 * tick
            if stat_due <= m:
                if stat_due:
                    print(f"{t:%H:%M:%S} snap: {stats['req']} requests, {stats['304']} not modified, {stats['changed']} "
                          f"changed, {stats['err']} errors/throttled", flush=True)
                stat_due = m + 300
            wait = max(0.0, tick - (datetime.now(timezone.utc) - t).total_seconds())
            feeds = {f.key: f.status() for f in (*url_feeds, *res_feeds.values(), *([books_feed] if tokens else []))}
            mark(out, "snap", datetime.now(timezone.utc) + timedelta(seconds=wait - 0.1), tick, feeds)
            await asyncio.sleep(wait)
