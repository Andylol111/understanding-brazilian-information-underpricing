"""civicAPI (https://civicapi.org) as a SECONDARY live source for the US midterms, 2026-11-03.

Attribution (required by civicAPI's licence, "free for all personal, non-commercial, or commercial purposes,
attribution is required"): every display that shows its numbers says "civicAPI (civicapi.org)" and links
https://civicapi.org. Used for the states whose official feeds the recorder cannot reach (SC, IA, MI, WI, NH, NV,
NY, CO, KS, ME) and, to cross-validate, for states it records officially (TX, GA, NC, FL, OH, PA, AZ, MN), plus the
national US House race (all 435 districts).

API (no key, no stated rate limit; we send one request per race every 30 s from one identified User-Agent):
  GET /api/v2/race/search?query=<text>&date=YYYY-MM-DD   races[] (id, type, province, election_name, election_date)
  GET /api/v2/race/{id}      candidates[] (name, party, votes), percent_reporting (0-100), last_updated, test_data,
                             region_results {slug: {name, type, fips?, percent_reporting, candidates[]}}
                             (counties; NH/ME also towns, the US House race its 435 districts, keys "az-01", "ak-al")
Race names are "<State> US Senate" / "<State> Governor"; Ohio and Florida's Senate races are "Special".

Recorder (`brunder civic record`): GET every race each cycle, one request per interval/len(races) seconds (never faster
than the interval per race), and save a body only when it changed, in the layout of `brunder snap`:
<SNAP>/<tag>/civic_<race id>/<UTC %Y%m%dT%H%M%S.%fZ>.json[.gz] (gzip above 64 KB) + a line in <tag>/index.jsonl
(t = request start, fetched, http_ms, last_modified, etag, date, plus civic_last_updated / percent_reporting) and one
line per poll in <tag>/civic_polls.jsonl. Errors back the race off (doubling, ≤ 300 s, Retry-After honoured) without
delaying the others. After each cycle `snap.mark(out, "civic", next_cycle_start, interval)` tells `brunder us live`
that every snapshot before that instant is on disk.

Parser: `parse_feed("civic_<st>", {"sen": blob, "gov": blob})` → the long table of brunder.us (COLS), counties only
(FIPS → unit_id us-<st>-<FIPS>; name fallback when the race has no fips), "state" row from the race's own totals.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import snap
from .snap import SNAP

BASE = "https://civicapi.org/api/v2"
UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
SOURCE_NAME = "civicAPI (civicapi.org)"
SOURCE_URL = "https://civicapi.org"
SOURCE_LICENCE = "free to use (personal, non-commercial or commercial); attribution required"
ELECTION_DATE = "2026-11-03"
HOUSE_KEY = "us-house"
GZIP_ABOVE = 64_000
BACKOFF_CAP = 300.0
RESOLVE_EVERY = 600.0
TIMEOUT = 15.0
COMPLETE_AT = 99.0                            # a county counts as complete at this percent reporting (see parse_race)
MIN_INTERVAL = 30.0                           # never poll a race faster than this (civicAPI: polite use)

# race key "<st>-<sen|gov>" -> civicAPI race id (resolved with race/search on 2026-10-30; `brunder civic resolve`
# re-checks them). None = not listed yet; the recorder searches again every RESOLVE_EVERY seconds.
TARGET = {                                    # states without a reachable official feed (live model: civic configs)
    "sc-sen": 87409, "sc-gov": 87410, "ia-sen": 87151, "ia-gov": 87152, "mi-sen": 87214, "mi-gov": 87215,
    "wi-gov": 87491, "nh-sen": 87283, "nh-gov": 87284, "nv-gov": 87260, "ny-gov": 87329, "co-sen": 87022,
    "co-gov": 87018, "ks-sen": 87162, "ks-gov": 87163, "me-sen": 87188, "me-gov": 87189,
}
XVAL = {                                      # states recorded officially too: cross-validation (analysis/us_civic.py)
    "tx-sen": 87438, "tx-gov": 55857, "ga-sen": 87087, "ga-gov": 87088, "nc-sen": 87346, "fl-sen": 87068,
    "fl-gov": 87069, "oh-sen": 87527, "oh-gov": 87365, "pa-gov": 87393, "mn-sen": 87229, "mn-gov": 87230,
    "az-gov": None,                           # not listed on 2026-10-30
}
HOUSE_ID = 87524                              # "United States House of Representatives": 435 districts in region_results
RACES: dict[str, int | None] = {**TARGET, **XVAL, HOUSE_KEY: HOUSE_ID}

STATES = {"ia": "Iowa", "mi": "Michigan", "wi": "Wisconsin", "nh": "New Hampshire", "nv": "Nevada", "ny": "New York",
          "co": "Colorado", "ks": "Kansas", "me": "Maine"}               # the others are in brunder.us.STATES
_NAMES = {**STATES, "sc": "South Carolina", "tx": "Texas", "ga": "Georgia", "nc": "North Carolina", "fl": "Florida",
          "oh": "Ohio", "pa": "Pennsylvania", "az": "Arizona", "mn": "Minnesota"}
POLL_CLOSE = {"ia": "2026-11-04T03:00:00Z", "mi": "2026-11-04T01:00:00Z", "wi": "2026-11-04T02:00:00Z",
              "nh": "2026-11-04T00:00:00Z", "nv": "2026-11-04T03:00:00Z", "ny": "2026-11-04T02:00:00Z",
              "co": "2026-11-04T02:00:00Z", "ks": "2026-11-04T01:00:00Z", "me": "2026-11-04T01:00:00Z"}
POLL_CLOSE_LAST = {"mi": "2026-11-04T02:00:00Z",     # four Upper Peninsula counties on central time
                   "ks": "2026-11-04T02:00:00Z",     # western Kansas counties on mountain time
                   "nh": "2026-11-04T01:00:00Z"}     # towns may keep polls open until 8 p.m.
FEEDS = {f"civic_{st}": {off: f"civic_{i}" for k, i in RACES.items()
                         if k.startswith(f"{st}-") and (off := k[3:]) in ("sen", "gov") and i is not None}
         for st in sorted(_NAMES)}
SOURCE_INFO = {                               # prior source of the civic configs: MEDSL 2024 president, summed to counties
    st: ("MIT Election Data and Science Lab, U.S. President Precinct-Level Returns 2024 (summed to counties)",
         "https://github.com/MEDSL/2024-elections-official", "CC0 1.0") for st in STATES}
POLL_SOURCES = {
    "ia": {"name": "Iowa Secretary of State, voting in Iowa (polls 7 a.m.-9 p.m. central)",
           "url": "https://sos.iowa.gov/elections/voterinformation/index.html", "licence": "public record"},
    "mi": {"name": "Michigan Secretary of State, voting in Michigan (polls 7 a.m.-8 p.m.; four UP counties central)",
           "url": "https://mvic.sos.state.mi.us/", "licence": "public record"},
    "wi": {"name": "Wisconsin Elections Commission, voting in Wisconsin (polls 7 a.m.-8 p.m. central)",
           "url": "https://myvote.wi.gov/", "licence": "public record"},
    "nh": {"name": "New Hampshire Secretary of State, elections (polls close at 7 p.m. or later, by town)",
           "url": "https://www.sos.nh.gov/elections", "licence": "public record"},
    "nv": {"name": "Nevada Secretary of State, elections (polls 7 a.m.-7 p.m. Pacific)",
           "url": "https://www.nvsos.gov/sos/elections", "licence": "public record"},
    "ny": {"name": "New York State Board of Elections, voting (polls 6 a.m.-9 p.m.)",
           "url": "https://elections.ny.gov/", "licence": "public record"},
    "co": {"name": "Colorado Secretary of State, elections (polls close 7 p.m. mountain)",
           "url": "https://www.coloradosos.gov/pubs/elections/", "licence": "public record"},
    "ks": {"name": "Kansas Secretary of State, elections (polls 7 a.m.-7 p.m.; central, western counties mountain)",
           "url": "https://sos.ks.gov/elections/", "licence": "public record"},
    "me": {"name": "Maine Secretary of State, elections (polls close 8 p.m.)",
           "url": "https://www.maine.gov/sos/cec/elec/", "licence": "public record"},
}
SOURCE = {"name": SOURCE_NAME, "url": SOURCE_URL, "licence": SOURCE_LICENCE}


def race_office(key: str) -> tuple[str, str] | None:
    """('ia', 'sen') for 'ia-sen'; None for the House key."""
    st, _, off = key.partition("-")
    return (st, off) if off in ("sen", "gov") else None


def expected(key: str) -> tuple[str, str] | None:
    """(province, election_type) civicAPI lists for a race key, e.g. ('IA', 'US Senate')."""
    so = race_office(key)
    return (so[0].upper(), "US Senate" if so[1] == "sen" else "Governor") if so else None


# ------------------------------------------------------------------ resolver

async def search(web: httpx.AsyncClient, query: str, date: str = ELECTION_DATE) -> list[dict]:
    r = await web.get(f"{BASE}/race/search", params={"query": query, "date": date})
    r.raise_for_status()
    return r.json().get("races", [])


async def resolve(web: httpx.AsyncClient, keys: list[str] | None = None, date: str = ELECTION_DATE) -> dict[str, list[dict]]:
    """race key -> civicAPI general-election races on `date` for its state and office (normally one)."""
    out: dict[str, list[dict]] = {}
    for key in keys or [k for k in RACES if race_office(k)]:
        prov, typ = expected(key)
        found = []
        for r in await search(web, f"{_NAMES[key[:2]]} {typ}", date):
            if (r.get("province") == prov and r.get("election_type") == typ and
                    str(r.get("election_date", "")).startswith(date) and r.get("type") == typ
                    and "Primary" not in r.get("election_name", "")):
                found.append({k: r.get(k) for k in ("id", "election_name", "election_date", "province", "type")})
        out[key] = found
        await asyncio.sleep(0.3)
    return out


# ------------------------------------------------------------------ recorder

def _validate(key: str, body: bytes) -> dict:
    """The decoded race document, or ValueError if it is not one of ours."""
    d = json.loads(body)
    if not isinstance(d, dict) or "candidates" not in d:
        raise ValueError("not a race document")
    return d


def _wrong_race(key: str, d: dict) -> str | None:
    """Why this document is not the race `key` should be (a changed id), else None."""
    if key == HOUSE_KEY:
        return None if d.get("election_type") == "US House" or "House" in str(d.get("election_name")) else "not the House race"
    prov, typ = expected(key)
    bad = []
    if d.get("province") != prov:
        bad.append(f"province {d.get('province')}")
    if str(d.get("election_type")) != typ:
        bad.append(f"type {d.get('election_type')}")
    if not str(d.get("election_date", "")).startswith(ELECTION_DATE):
        bad.append(f"date {d.get('election_date')}")
    return ", ".join(bad) or None


class Recorder:
    """State of one `record` run (kept in a class so tests can drive single polls)."""

    def __init__(self, tag: str, races: dict[str, int | None], interval: float = 30.0, out_root: Path | None = None):
        self.tag, self.interval = tag, interval
        self.out = (out_root or SNAP) / tag
        self.races = dict(races)
        self.last: dict[str, str] = {}                    # key -> sha256 of the last saved body
        self.etag: dict[str, str] = {}
        self.fails: dict[str, int] = {}
        self.not_before: dict[str, float] = {}            # key -> monotonic time before which it is not polled
        self.warned: set[str] = set()
        self.index = self.out / "index.jsonl"
        self.polls = self.out / "civic_polls.jsonl"
        self.out.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, body: bytes, t: datetime, meta: dict, d: dict) -> bool:
        """Write the body if it differs from the last saved one; returns whether it did."""
        h = hashlib.sha256(body).hexdigest()
        if self.last.get(key) == h:
            return False
        self.last[key] = h
        rid = self.races[key]
        name = f"civic_{rid}"
        dd = self.out / name
        dd.mkdir(parents=True, exist_ok=True)
        gz = len(body) > GZIP_ABOVE
        f = dd / f"{t:%Y%m%dT%H%M%S.%fZ}.json{'.gz' if gz else ''}"
        tmp = self.out / ".tmp" / f"{name}.{f.name}"          # readers scanning dd only ever see complete files
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(gzip.compress(body, 6) if gz else body)
        os.replace(tmp, f)
        with self.index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": name, "file": str(f.relative_to(self.out)), "sha256": h,
                                 "bytes": len(body), "gzip": gz, **meta, "race": key,
                                 "civic_last_updated": d.get("last_updated"),
                                 "percent_reporting": d.get("percent_reporting"),
                                 "test_data": d.get("test_data")}) + "\n")
        return True

    async def poll(self, web: httpx.AsyncClient, key: str, t: datetime) -> str:
        """One GET of one race; returns 'saved' / 'same' / 'error: …' and logs the poll."""
        import time
        rid = self.races[key]
        headers = {"If-None-Match": self.etag[key]} if key in self.etag else {}
        t0 = time.monotonic()
        status, result, meta = None, "", {}
        try:
            r = await web.get(f"{BASE}/race/{rid}", headers=headers)
            ms = (time.monotonic() - t0) * 1000
            status = r.status_code
            fetched = datetime.now(timezone.utc)
            if status == 304:
                result = "same"
            else:
                if status in (429, 503):                  # polite: honour Retry-After, else back off
                    ra = r.headers.get("retry-after")
                    self.not_before[key] = time.monotonic() + (float(ra) if ra and ra.isdigit() else self._backoff(key))
                r.raise_for_status()
                d = _validate(key, r.content)
                h = r.headers
                meta = {k: v for k, v in (("fetched", fetched.isoformat()), ("http_ms", round(ms)),
                                          ("last_modified", h.get("last-modified")), ("etag", h.get("etag")),
                                          ("date", h.get("date"))) if v is not None}
                if h.get("etag"):
                    self.etag[key] = h["etag"]
                why = _wrong_race(key, d)
                if why and key not in self.warned:
                    self.warned.add(key)
                    print(f"{t:%H:%M:%S} civic {key}: race {rid} is not what we resolved ({why}); recording anyway",
                          flush=True)
                result = "saved" if self.save(key, r.content, t, meta, d) else "same"
            self.fails.pop(key, None)
        except Exception as e:                            # one race failing must not stop the others
            ms = (time.monotonic() - t0) * 1000
            n = self.fails[key] = self.fails.get(key, 0) + 1
            self.not_before[key] = max(self.not_before.get(key, 0.0), time.monotonic() + self._backoff(key))
            result = f"error: {type(e).__name__}: {e}"
            if n <= 3 or n % 20 == 0:
                print(f"{t:%H:%M:%S} civic {key} ({rid}): {result} (failure {n}, retry in "
                      f"{self.not_before[key] - time.monotonic():.0f}s)", flush=True)
        with self.polls.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "race": key, "id": rid, "status": status, "ms": round(ms),
                                 "result": result.split(":")[0]}) + "\n")
        return result

    def _backoff(self, key: str) -> float:
        return min(self.interval * 2.0 ** self.fails.get(key, 0), BACKOFF_CAP)


async def record(tag: str, races: dict[str, int | None] | None = None, interval: float = 30.0,
                 until: datetime | None = None, out_root: Path | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Poll every race each `interval` s (spread evenly over the interval) until `until`; see the module docstring."""
    import time
    rec = Recorder(tag, RACES if races is None else races, interval, out_root)
    print(f"civic record {tag}: {sum(v is not None for v in rec.races.values())} races + "
          f"{sum(v is None for v in rec.races.values())} unresolved, every {interval:.0f}s until {until}; "
          f"{SOURCE_NAME}, {SOURCE_URL}", flush=True)
    last_resolve = -RESOLVE_EVERY
    t_cycle = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True, transport=transport) as web:
        while until is None or datetime.now(timezone.utc) < until:
            if any(v is None for v in rec.races.values()) and time.monotonic() - last_resolve >= RESOLVE_EVERY:
                last_resolve = time.monotonic()
                try:
                    for key, found in (await resolve(web, [k for k, v in rec.races.items() if v is None and race_office(k)])).items():
                        if len(found) == 1:
                            rec.races[key] = found[0]["id"]
                            print(f"civic {key}: resolved to race {found[0]['id']} ({found[0]['election_name']})", flush=True)
                except Exception as e:
                    print(f"civic resolve error, continuing: {type(e).__name__}: {e}", flush=True)
            keys = [k for k, v in rec.races.items() if v is not None]
            slot = interval / max(len(keys), 1)
            t_cycle = datetime.now(timezone.utc)
            mono0 = time.monotonic()
            for i, key in enumerate(keys):
                wait = mono0 + i * slot - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                if time.monotonic() < rec.not_before.get(key, 0.0):
                    continue
                await rec.poll(web, key, datetime.now(timezone.utc))
            nxt = max(t_cycle.timestamp() + interval, datetime.now(timezone.utc).timestamp())
            nxt_dt = datetime.fromtimestamp(nxt, timezone.utc)
            snap.mark(rec.out, "civic", nxt_dt, interval)
            await asyncio.sleep(max(0.0, nxt - datetime.now(timezone.utc).timestamp()))


# ------------------------------------------------------------------ parser (snapshot bytes -> rows)

_warned_unmapped: set[tuple] = set()


def candidates(d: dict) -> list[dict]:
    return [{"name": c.get("name"), "party": c.get("party"), "votes": c.get("votes") or 0}
            for c in d.get("candidates") or []]


def unit_for(st: str, fips: str | None, name: str, by_key: dict, valid: set[str]) -> str | None:
    """Our unit_id of a county row: FIPS when the race gives one, else its normalised name; None if it is not one of
    the state's counties."""
    from .us import _norm
    if fips:
        uid = f"us-{st}-{str(fips).zfill(5)}"
        return uid if uid in valid else None
    hit = by_key.get(_norm(name))
    return hit[0] if hit else None


def county_rows(d: dict, st: str) -> tuple[list[tuple[str, dict]], list[str]]:
    """(unit_id, region) for the race's county rows that map to one of the state's counties, and the labels of the
    county rows that do not."""
    from .us import _ids, counties
    valid = set(counties(st)["unit_id"])
    by_key = _ids(st)
    mapped, unmapped = [], []
    for slug, v in (d.get("region_results") or {}).items():
        if v.get("type") != "County":
            continue                                       # NH / ME also list towns (their counties are listed too)
        uid = unit_for(st, v.get("fips"), v.get("name") or slug, by_key, valid)
        (mapped.append((uid, v)) if uid else unmapped.append(f"{slug} ({v.get('fips')})"))
    return mapped, unmapped


def parse_race(blob: bytes | dict, st: str, race: str) -> "pd.DataFrame":
    """Rows (brunder.us.COLS) of one civicAPI race: one per county × candidate, plus the race's own statewide totals
    (level 'state'). percent_reporting (0-100) is the county's progress: reporting = that percent, units = COMPLETE_AT
    (99): in counted 2026 primaries civicAPI's county percent stays at 99 or lower for good (WI 99 in all 72 counties
    two days after, KS 83-99, NV 72-90), so 100 would never mark a county complete. A race flagged test_data has no rows. Counties of the document that are not
    ours are skipped (printed once); a county of ours that the document lacks is simply uncounted."""
    from .us import STATES, _frame, party_code
    d = blob if isinstance(blob, dict) else json.loads(blob)
    if d.get("test_data"):
        return _frame([])
    rows = []
    mapped, unmapped = county_rows(d, st)
    if unmapped and (st, race, tuple(unmapped)) not in _warned_unmapped:
        _warned_unmapped.add((st, race, tuple(unmapped)))
        print(f"civic {st} {race}: {len(unmapped)} county rows not mapped to a {st.upper()} county: "
              f"{', '.join(unmapped[:8])}", flush=True)
    nan = float("nan")

    def add(level: str, uid: str, name: str, cands: list[dict], pr) -> None:
        pr = float(pr) if pr is not None else 0.0
        for c in cands:
            rows.append({"level": level, "unit_id": uid, "name": name, "race": race, "candidate": c.get("name"),
                         "party": party_code(c.get("party")), "votes": float(c.get("votes") or 0), "early": nan,
                         "reporting": pr, "units": COMPLETE_AT, "ballots": nan, "registered": nan, "outstanding": nan})
    for uid, v in mapped:
        add("county", uid, v.get("name"), v.get("candidates") or [], v.get("percent_reporting"))
    add("state", f"us-{st}", STATES.get(st) or _NAMES[st], d.get("candidates") or [], d.get("percent_reporting"))
    return _frame(rows)


def parse_house(blob: bytes | dict) -> "pd.DataFrame":
    """The US House race: one row per district × candidate (district = civicAPI key, 'az-01', 'ak-al')."""
    import pandas as pd
    d = blob if isinstance(blob, dict) else json.loads(blob)
    rows = []
    for key, v in (d.get("region_results") or {}).items():
        for c in v.get("candidates") or []:
            rows.append({"district": key, "name": v.get("name"), "candidate": c.get("name"), "party": c.get("party"),
                         "votes": float(c.get("votes") or 0), "percent_reporting": v.get("percent_reporting") or 0.0})
    return pd.DataFrame(rows, columns=["district", "name", "candidate", "party", "votes", "percent_reporting"])


def parse_feed(kind: str, blobs: dict[str, bytes | list[bytes] | None]) -> "pd.DataFrame":
    """Rows for feed kind 'civic_<st>': blobs {"sen": bytes, "gov": bytes} (each optional)."""
    import pandas as pd
    from .us import _frame
    st = kind.removeprefix("civic_")
    parts = [parse_race(b, st, off) for off, b in blobs.items() if b]
    return pd.concat(parts, ignore_index=True) if parts else _frame([])


def cli(a) -> None:
    """`brunder civic record|resolve` (see src/brunder/cli.py)."""
    import pandas as pd
    match a.step:
        case "record":
            until = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
            asyncio.run(record(a.tag, None, max(a.interval, MIN_INTERVAL), until))
        case "resolve":
            async def run() -> None:
                async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA) as web:
                    for key, found in (await resolve(web)).items():
                        known = RACES.get(key)
                        print(f"{key:8} known={known} civicAPI={[(f['id'], f['election_name']) for f in found]}"
                              + ("" if found and known in [f["id"] for f in found] else "   <-- CHECK"))
            asyncio.run(run())
