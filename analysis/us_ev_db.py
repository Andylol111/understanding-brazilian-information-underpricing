"""Early-vote database on the SSD (docs/us_ev_db.md).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_db.py collect [--states nc,fl] [--sources id,...]
  ...                                                         us_ev_db.py ingest-legacy | status | loop | sources

Layout (all under $BRUNDER_DATA/us_ev):
  raw/<state>/<YYYYMMDDTHHMMZ>_<source_id>[-<part>].<ext>   immutable dated snapshots (mode 0444), sha256 in MANIFEST.sha256
  ev.sqlite   sources, snapshots, fetch_log, ev_county, ev_state (+ views *_latest)
  logs/       collect / loop logs

Rules the code enforces: honest client (UA below, <= 1 request/s per host, robots.txt honoured), a response is saved
only if HTTP 200 *and* it passes the source's validation (magic bytes / parse / block-page markers); an error or
challenge body is never written to disk, only logged. Aggregate counts only: nothing here stores voter-level records.

Collectors are one function per source, registered with `@source(...)`, `fn(st: Store)`; they fetch with `st.fetch(...)`
and write aggregate rows with `st.add_rows(...)`. Extension modules `analysis/us_ev_src_*.py` register more of them.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import traceback
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

if __name__ == "__main__":                 # extension modules do `import us_ev_db`: make that this module, not a copy
    sys.modules["us_ev_db"] = sys.modules["__main__"]

HERE = Path(__file__).resolve().parent
UA = "brunder/0.1 (research; recorder)"
ROBOTS_TOKEN = "brunder"
MIN_INTERVAL = 1.05                          # seconds between requests to one host
TIMEOUT = 90.0
MEASURES = ("mail_requested", "mail_returned", "mail_accepted", "inperson_early", "total")
PARTIES = ("D", "R", "U", "other", "ALL")
BLOCK_MARKERS = (b"just a moment", b"cf-chl", b"_incapsula_", b"incapsula incident", b"access denied",
                 b"request unsuccessful", b"attention required", b"captcha", b"awswaf", b"enable javascript and cookies")
LOOP_END = "2026-11-04T06:00:00+00:00"


def data_root() -> Path:
    return Path(os.environ.get("BRUNDER_DATA") or "~/brunder-data-local").expanduser()


def ev_root() -> Path:
    """$BRUNDER_EV_ROOT (scratch/dev override) or $BRUNDER_DATA/us_ev."""
    return Path(os.environ["BRUNDER_EV_ROOT"]).expanduser() if os.environ.get("BRUNDER_EV_ROOT") else data_root() / "us_ev"


# --------------------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  state TEXT NOT NULL, source_id TEXT PRIMARY KEY, url TEXT, kind TEXT, format TEXT, cadence TEXT,
  access TEXT, licence TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, part TEXT NOT NULL DEFAULT '',
  state TEXT, url TEXT, fetched_utc TEXT NOT NULL, file TEXT, sha256 TEXT, http_status INTEGER, bytes INTEGER,
  valid INTEGER NOT NULL, etag TEXT, last_modified TEXT, parsed INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE INDEX IF NOT EXISTS snap_src ON snapshots(source_id, part, valid, snapshot_id);
CREATE TABLE IF NOT EXISTS fetch_log(
  ts TEXT NOT NULL, state TEXT, source_id TEXT NOT NULL, part TEXT NOT NULL DEFAULT '', url TEXT,
  outcome TEXT NOT NULL, http_status INTEGER, message TEXT, snapshot_id INTEGER);
CREATE INDEX IF NOT EXISTS log_src ON fetch_log(source_id, ts);
CREATE TABLE IF NOT EXISTS ev_county(
  state TEXT NOT NULL, county_fips TEXT NOT NULL, as_of_date TEXT NOT NULL, measure TEXT NOT NULL,
  party TEXT NOT NULL, age_band TEXT NOT NULL DEFAULT 'ALL', "count" INTEGER NOT NULL,
  source_id TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
  PRIMARY KEY(state, county_fips, as_of_date, measure, party, age_band, source_id, snapshot_id));
CREATE INDEX IF NOT EXISTS evc_key ON ev_county(state, county_fips, as_of_date, measure, party, age_band, source_id);
CREATE TABLE IF NOT EXISTS ev_state(
  state TEXT NOT NULL, as_of_date TEXT NOT NULL, measure TEXT NOT NULL, party TEXT NOT NULL,
  age_band TEXT NOT NULL DEFAULT 'ALL', "count" INTEGER NOT NULL, source_id TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
  PRIMARY KEY(state, as_of_date, measure, party, age_band, source_id, snapshot_id));
CREATE INDEX IF NOT EXISTS evs_key ON ev_state(state, as_of_date, measure, party, age_band, source_id);
CREATE VIEW IF NOT EXISTS ev_county_latest AS SELECT * FROM ev_county e WHERE snapshot_id =
  (SELECT MAX(snapshot_id) FROM ev_county x WHERE x.state=e.state AND x.county_fips=e.county_fips AND
   x.as_of_date=e.as_of_date AND x.measure=e.measure AND x.party=e.party AND x.age_band=e.age_band AND x.source_id=e.source_id);
CREATE VIEW IF NOT EXISTS ev_state_latest AS SELECT * FROM ev_state e WHERE snapshot_id =
  (SELECT MAX(snapshot_id) FROM ev_state x WHERE x.state=e.state AND x.as_of_date=e.as_of_date AND
   x.measure=e.measure AND x.party=e.party AND x.age_band=e.age_band AND x.source_id=e.source_id);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------------------- registry

@dataclass
class Source:
    state: str
    source_id: str
    url: str
    kind: str                       # county | state | county+state | none
    format: str
    cadence: str
    access: str = "open"
    licence: str = "public record (official state publication)"
    notes: str = ""
    fn: Callable | None = field(default=None, repr=False)


SOURCES: dict[str, Source] = {}
IMPORT_ERRORS: dict[str, str] = {}


def register(src: Source) -> Source:
    src.state = src.state.lower()
    SOURCES[src.source_id] = src
    return src


def source(state: str, source_id: str, url: str, kind: str, format: str, cadence: str, access: str = "open",
           licence: str = "public record (official state publication)", notes: str = ""):
    """Decorator: register `fn(st: Store)` as the collector of one source."""
    def deco(fn):
        register(Source(state, source_id, url, kind, format, cadence, access, licence, notes, fn))
        return fn
    return deco


def not_collected(state: str, source_id: str, url: str, access: str, notes: str, cadence: str = "",
                  format: str = "", kind: str = "none") -> None:
    """Record a source we looked at but do not collect (blocked, JS-only, not published); shown in `sources`/`status`."""
    register(Source(state, source_id, url, kind, format, cadence, access, "n/a", notes, None))


# --------------------------------------------------------------------------------------- fetch result

@dataclass
class Fetched:
    ok: bool                         # a valid body is available (new or the previous unchanged one)
    changed: bool                    # a new snapshot was written
    status: int | None
    snapshot_id: int | None
    path: Path | None
    parsed: bool                     # rows of this snapshot are already in the DB
    message: str = ""
    _body: bytes | None = field(default=None, repr=False)
    last_modified: str | None = None          # HTTP Last-Modified of a new snapshot
    etag: str | None = None

    @property
    def body(self) -> bytes:
        if self._body is None:
            self._body = self.path.read_bytes()
        return self._body

    @property
    def needs_parse(self) -> bool:
        return self.ok and not self.parsed


class FetchError(Exception):
    pass


class Skip(Exception):
    """Raised by a `validate` callback: the body is fine but not wanted (e.g. a day not yet reported). Not a failure."""


def sniff(body: bytes, ext: str) -> None:
    """Generic validity check; raises ValueError. Source-specific `validate` callbacks run after this."""
    if not body:
        raise ValueError("empty body")
    ext = ext.lower().lstrip(".")
    head = body[:2048].lstrip().lower()
    if ext == "pdf" and not body.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    if ext in ("zip", "xlsx", "xlsm") and not body.startswith(b"PK"):
        raise ValueError("not a zip/xlsx")
    if ext == "xls" and not body.startswith((b"\xd0\xcf\x11\xe0", b"PK")):
        raise ValueError("not an xls")
    if ext == "json":
        json.loads(body)
    if ext in ("csv", "txt", "tsv", "json") and head.startswith((b"<!doctype html", b"<html")):
        raise ValueError("HTML body where data was expected")
    if ext in ("html", "htm", "csv", "txt", "json", "tsv"):
        low = body[:200_000].lower() if ext in ("html", "htm") else body[:4096].lower()
        if any(m in low for m in BLOCK_MARKERS) and (ext not in ("html", "htm") or len(body) < 20_000):
            raise ValueError("block/challenge page")


# --------------------------------------------------------------------------------------- store

class Store:
    def __init__(self, root: Path | None = None, client: httpx.Client | None = None, min_interval: float = MIN_INTERVAL):
        self.root = Path(root) if root else ev_root()
        self.raw = self.root / "raw"
        self.logs = self.root / "logs"
        for d in (self.raw, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "ev.sqlite"
        self.db = sqlite3.connect(self.db_path, timeout=60)
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.client = client or httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.current_state: str | None = None

    # ---- bookkeeping
    def log(self, source_id: str, outcome: str, *, state=None, part="", url=None, status=None, message="", snapshot_id=None):
        self.db.execute("INSERT INTO fetch_log VALUES(?,?,?,?,?,?,?,?,?)",
                        (utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), state or self.current_state, source_id, part, url,
                         outcome, status, message[:500], snapshot_id))
        self.db.commit()

    def sync_sources(self) -> None:
        for s in SOURCES.values():
            self.db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                            "state=excluded.state,url=excluded.url,kind=excluded.kind,format=excluded.format,"
                            "cadence=excluded.cadence,access=excluded.access,licence=excluded.licence,notes=excluded.notes",
                            (s.state, s.source_id, s.url, s.kind, s.format, s.cadence, s.access, s.licence, s.notes))
        self.db.commit()

    def last_valid(self, source_id: str, part: str = "") -> sqlite3.Row | tuple | None:
        return self.db.execute("SELECT snapshot_id,file,sha256,etag,last_modified,parsed,fetched_utc FROM snapshots "
                               "WHERE source_id=? AND part=? AND valid=1 ORDER BY snapshot_id DESC LIMIT 1",
                               (source_id, part)).fetchone()

    # ---- network
    def _wait(self, host: str) -> None:
        dt = time.monotonic() - self._last.get(host, 0.0)
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last[host] = time.monotonic()

    def _robots_ok(self, url: str) -> bool:
        u = urlparse(url)
        host = f"{u.scheme}://{u.netloc}"
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            self._wait(u.netloc)
            try:
                r = self.client.get(f"{host}/robots.txt")
                if r.status_code == 200 and not r.content.lstrip().lower().startswith((b"<!doctype", b"<html")):
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
                elif r.status_code < 500:
                    self._robots[host] = None                    # RFC 9309: unavailable (4xx) = no restrictions
                else:
                    self._robots[host] = urllib.robotparser.RobotFileParser()
                    self._robots[host].parse(["User-agent: *", "Disallow: /"])
            except httpx.HTTPError:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(ROBOTS_TOKEN, url)

    def request(self, url: str, *, params=None, headers=None, method="GET", content=None):
        """One polite request (robots.txt, per-host spacing). Returns the httpx response; raises FetchError."""
        if not self._robots_ok(url):
            raise FetchError("disallowed by robots.txt")
        self._wait(urlparse(url).netloc)
        try:
            return self.client.request(method, url, params=params, headers=headers, content=content)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__}: {e}") from e

    def fetch(self, source_id: str, url: str, *, ext: str, part: str = "", state: str | None = None, params=None,
              headers=None, validate: Callable[[bytes], None] | None = None, conditional: bool = True,
              ok_status: tuple[int, ...] = (200,)) -> Fetched:
        """Fetch -> validate -> save as an immutable dated snapshot, unless unchanged (304 / same sha256).

        Failures (network, status, block page, invalid body) are logged and returned with ok=False; the body is
        never written to disk."""
        state = state or self.current_state or (SOURCES[source_id].state if source_id in SOURCES else None)
        prev = self.last_valid(source_id, part)
        hdr = dict(headers or {})
        if conditional and prev:
            if prev[3]:
                hdr["If-None-Match"] = prev[3]
            if prev[4]:
                hdr["If-Modified-Since"] = prev[4]
        try:
            r = self.request(url, params=params, headers=hdr)
        except FetchError as e:
            self._reject(source_id, part, state, url, None, str(e))
            return Fetched(False, False, None, None, None, False, str(e))
        if r.status_code == 304 and prev:
            self.log(source_id, "unchanged", state=state, part=part, url=url, status=304, snapshot_id=prev[0])
            return Fetched(True, False, 304, prev[0], self.root_path(prev[1]), bool(prev[5]), "304")
        if r.status_code not in ok_status:
            msg = f"HTTP {r.status_code}"
            self._reject(source_id, part, state, url, r.status_code, msg)
            return Fetched(False, False, r.status_code, None, None, False, msg)
        body = r.content
        try:
            sniff(body, ext)
            if validate:
                validate(body)
        except Skip as e:
            self.log(source_id, "skipped", state=state, part=part, url=url, status=r.status_code, message=str(e))
            return Fetched(False, False, r.status_code, None, None, False, f"skipped: {e}")
        except Exception as e:                                   # noqa: BLE001 - any parse failure = invalid body
            msg = f"invalid body: {type(e).__name__}: {e}"
            self._reject(source_id, part, state, url, r.status_code, msg, nbytes=len(body))
            return Fetched(False, False, r.status_code, None, None, False, msg)
        etag, lm = r.headers.get("etag"), r.headers.get("last-modified")
        return self._save(source_id, part, state, url, ext, body, r.status_code, etag, lm, prev)

    def save_body(self, source_id: str, url: str, body: bytes, *, ext: str, part: str = "", state: str | None = None,
                  status: int = 200, validate: Callable[[bytes], None] | None = None) -> Fetched:
        """Save an already-downloaded body (e.g. a derived download) under the same rules as `fetch`."""
        state = state or self.current_state
        try:
            sniff(body, ext)
            if validate:
                validate(body)
        except Exception as e:                                   # noqa: BLE001
            msg = f"invalid body: {type(e).__name__}: {e}"
            self._reject(source_id, part, state, url, status, msg, nbytes=len(body))
            return Fetched(False, False, status, None, None, False, msg)
        return self._save(source_id, part, state, url, ext, body, status, None, None, self.last_valid(source_id, part))

    def _reject(self, source_id, part, state, url, status, msg, nbytes=None):
        self.db.execute("INSERT INTO snapshots(source_id,part,state,url,fetched_utc,file,sha256,http_status,bytes,valid,error)"
                        " VALUES(?,?,?,?,?,NULL,NULL,?,?,0,?)",
                        (source_id, part, state, url, utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), status, nbytes, msg[:500]))
        self.log(source_id, "failed", state=state, part=part, url=url, status=status, message=msg)

    def root_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / rel if not rel.startswith("raw/us/") else data_root() / rel)

    def _save(self, source_id, part, state, url, ext, body, status, etag, lm, prev) -> Fetched:
        sha = sha256_bytes(body)
        if prev and prev[2] == sha:
            self.db.execute("UPDATE snapshots SET etag=COALESCE(?,etag), last_modified=COALESCE(?,last_modified) "
                            "WHERE snapshot_id=?", (etag, lm, prev[0]))
            self.log(source_id, "unchanged", state=state, part=part, url=url, status=status, snapshot_id=prev[0])
            return Fetched(True, False, status, prev[0], self.root_path(prev[1]), bool(prev[5]), "same sha256", body)
        now = utcnow()
        stem = f"{now.strftime('%Y%m%dT%H%MZ')}_{source_id}" + (f"-{part}" if part else "")
        d = self.raw / (state or "xx")
        d.mkdir(parents=True, exist_ok=True)
        path, k = d / f"{stem}.{ext}", 1
        while path.exists():                                     # two snapshots in one minute: never overwrite
            k += 1
            path = d / f"{stem}_{k}.{ext}"
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        os.chmod(path, 0o444)
        rel = str(path.relative_to(self.root))
        cur = self.db.execute("INSERT INTO snapshots(source_id,part,state,url,fetched_utc,file,sha256,http_status,bytes,"
                              "valid,etag,last_modified) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                              (source_id, part, state, url, now.strftime("%Y-%m-%dT%H:%M:%SZ"), rel, sha, status,
                               len(body), etag, lm))
        with open(self.root / "MANIFEST.sha256", "a") as fh:
            fh.write(f"{sha}  {rel}\n")
        self.log(source_id, "new", state=state, part=part, url=url, status=status, snapshot_id=cur.lastrowid,
                 message=f"{len(body)} bytes")
        return Fetched(True, True, status, cur.lastrowid, path, False, "new snapshot", body, lm, etag)

    def register_existing(self, source_id: str, path: Path, *, state: str, url: str = "", part: str = "",
                          max_bytes: int = 400_000_000) -> int | None:
        """Record an already-existing file (read-only, not copied) as a valid snapshot; idempotent by (source, path)."""
        path = Path(path)
        rel = os.path.relpath(path, data_root()) if str(path).startswith(str(data_root())) else str(path)
        row = self.db.execute("SELECT snapshot_id FROM snapshots WHERE source_id=? AND file=?", (source_id, rel)).fetchone()
        if row:
            return row[0]
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self.db.execute("INSERT INTO snapshots(source_id,part,state,url,fetched_utc,file,sha256,http_status,bytes,"
                              "valid,parsed) VALUES(?,?,?,?,?,?,?,200,?,1,0)",
                              (source_id, part, state, url, ts, rel, h.hexdigest(), path.stat().st_size))
        with open(self.root / "MANIFEST.sha256", "a") as fh:
            fh.write(f"{h.hexdigest()}  {rel}\n")
        return cur.lastrowid

    # ---- rows
    def add_rows(self, rows: pd.DataFrame | list[dict], *, source_id: str, snapshot_id: int, level: str,
                 mark_parsed: bool = True) -> int:
        """Insert aggregate rows; a row whose latest stored value (any earlier snapshot) is identical is skipped.

        county level columns: state, county_fips, as_of_date, measure, party, [age_band], count.
        state level columns:  state, as_of_date, measure, party, [age_band], count."""
        df = pd.DataFrame(rows)
        n = 0
        if not df.empty:
            tbl = {"county": "ev_county", "state": "ev_state"}[level]
            key = (["state", "county_fips"] if level == "county" else ["state"]) + ["as_of_date", "measure", "party", "age_band"]
            df["state"] = df["state"].str.lower()
            if "age_band" not in df:
                df["age_band"] = "ALL"
            df["age_band"] = df["age_band"].fillna("ALL")
            df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.strftime("%Y-%m-%d")
            bad = set(df["measure"]) - set(MEASURES)
            if bad:
                raise ValueError(f"unknown measure {bad}")
            bad = set(df["party"]) - set(PARTIES)
            if bad:
                raise ValueError(f"unknown party {bad}")
            df["count"] = df["count"].astype("int64")
            df = df.drop_duplicates(key, keep="last")
            df["source_id"], df["snapshot_id"] = source_id, snapshot_id
            cols = key + ["count", "source_id", "snapshot_id"]
            self.db.execute("DROP TABLE IF EXISTS temp.newrows")
            self.db.execute(f"CREATE TEMP TABLE newrows AS SELECT * FROM {tbl} WHERE 0")
            df[cols].to_sql("newrows", self.db, if_exists="append", index=False)
            on = " AND ".join(f"e.{k}=n.{k}" for k in key + ["source_id"])
            on2 = " AND ".join(f"x.{k}=n.{k}" for k in key + ["source_id"])
            cur = self.db.execute(
                f'INSERT INTO {tbl}({",".join(chr(34)+c+chr(34) for c in cols)}) SELECT {",".join("n."+chr(34)+c+chr(34) for c in cols)} '
                f'FROM newrows n WHERE NOT EXISTS (SELECT 1 FROM {tbl} e WHERE {on} AND e."count"=n."count" AND '
                f"e.snapshot_id=(SELECT MAX(snapshot_id) FROM {tbl} x WHERE {on2}))")
            n = cur.rowcount
            self.db.execute("DROP TABLE temp.newrows")
        if mark_parsed:
            self.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (snapshot_id,))
        self.db.commit()
        return n

    def close(self) -> None:
        self.db.commit()
        self.db.close()
        self.client.close()


# --------------------------------------------------------------------------------------- geography

_GEO: dict[str, dict[str, str]] = {}


def _norm(s: str) -> str:
    s = s.lower().replace("saint", "st").replace("ste.", "ste")
    s = re.sub(r"\b(county|parish|borough|census area|city and borough|planning region)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _geo() -> dict[str, dict[str, str]]:
    """state -> normalised county name -> county id (5-digit FIPS; CT planning regions and AK districts keep their ids)."""
    if not _GEO:
        f = HERE.parent / "site" / "data" / "geo" / "us_counties.topo.json"
        g = json.loads(f.read_text())
        for x in g["objects"]["counties"]["geometries"]:
            uid, name = x["properties"]["unit_id"], x["properties"]["name"]
            _, st, fips = uid.split("-", 2)
            nm = re.sub(r",\s*[A-Z]{2}$", "", name)
            d = _GEO.setdefault(st, {})
            d[_norm(nm)] = fips
            if st == "va" and nm.endswith(" city"):
                d[_norm(nm.replace(" city", "")) + "city"] = fips
    return _GEO


def county_fips(state: str, name: str) -> str:
    """5-digit county FIPS from a county name (Census geometry shipped with the repo); raises KeyError if unknown."""
    key = _norm(name)
    try:
        return _geo()[state.lower()][key]
    except KeyError:
        raise KeyError(f"{state}: unknown county {name!r}") from None


# --------------------------------------------------------------------------------------- helpers for collectors

def num(x) -> int:
    s = re.sub(r"[^\d.\-]", "", str(x))
    return int(float(s)) if s not in ("", "-", ".") else 0


def long_rows(state: str, as_of: str, cells: dict[tuple[str, str], int], *, county: str | None = None) -> list[dict]:
    """{(measure, party): count} -> long rows (county id when given)."""
    out = []
    for (m, p), c in cells.items():
        r = {"state": state, "as_of_date": as_of, "measure": m, "party": p, "count": c}
        if county is not None:
            r["county_fips"] = county
        out.append(r)
    return out


# --------------------------------------------------------------------------------------- collection driver

def load_extensions() -> None:
    """Import the collectors: the ones in this file (registered at import) and analysis/us_ev_src_*.py."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    for f in sorted(HERE.glob("us_ev_src_*.py")):
        try:
            importlib.import_module(f.stem)
        except Exception as e:                                   # noqa: BLE001 - one broken module must not stop the rest
            print(f"WARNING: cannot import {f.name}: {type(e).__name__}: {e}", file=sys.stderr)
            IMPORT_ERRORS[f.stem] = f"{type(e).__name__}: {e}"


def run_collect(states: list[str] | None = None, source_ids: list[str] | None = None, store: Store | None = None,
                log: Callable[[str], None] = print) -> dict[str, str]:
    """Run every selected collector once; per-source failure never stops the rest. Returns {source_id: outcome}."""
    load_extensions()
    st = store or Store()
    lock = open(st.root / "collect.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another collect run holds the lock; exiting")
        return {}
    st.sync_sources()
    out: dict[str, str] = {}
    try:
        for s in sorted(SOURCES.values(), key=lambda s: (s.state, s.source_id)):
            if s.fn is None or (states and s.state not in states) or (source_ids and s.source_id not in source_ids):
                continue
            st.current_state = s.state
            t0 = time.monotonic()
            mark = st.db.execute("SELECT COALESCE(MAX(rowid),0) FROM fetch_log").fetchone()[0]
            before = st.db.execute("SELECT COUNT(*) FROM ev_county").fetchone()[0] + \
                st.db.execute("SELECT COUNT(*) FROM ev_state").fetchone()[0]
            try:
                s.fn(st)
                after = st.db.execute("SELECT COUNT(*) FROM ev_county").fetchone()[0] + \
                    st.db.execute("SELECT COUNT(*) FROM ev_state").fetchone()[0]
                cnt = dict(st.db.execute("SELECT outcome, COUNT(*) FROM fetch_log WHERE rowid>? AND source_id=? GROUP BY 1",
                                         (mark, s.source_id)).fetchall())
                nbad, ngood = cnt.get("failed", 0), cnt.get("new", 0) + cnt.get("unchanged", 0)
                kind = "collector_ok" if not nbad else ("collector_partial" if ngood else "collector_failed")
                out[s.source_id] = f"{kind.removeprefix('collector_')} +{after - before} rows (fetch ok {ngood}, failed {nbad})"
                st.log(s.source_id, kind, state=s.state, message=f"+{after - before} rows; fetch ok {ngood}, failed {nbad}")
            except Exception as e:                               # noqa: BLE001
                out[s.source_id] = f"ERROR {type(e).__name__}: {e}"
                st.log(s.source_id, "collector_error", state=s.state, message=f"{type(e).__name__}: {e}\n"
                       + traceback.format_exc(limit=4))
            log(f"{utcnow():%H:%M:%S} {s.state} {s.source_id}: {out[s.source_id]} ({time.monotonic() - t0:.1f}s)")
    finally:
        st.current_state = None
        if store is None:
            st.close()
        lock.close()
    return out


# --------------------------------------------------------------------------------------- status

def status_table(st: Store) -> pd.DataFrame:
    """Per state: latest as_of_date, measures, party breakdown yes/no, county level yes/no, last fetch ok/failed."""
    load_extensions()
    st.sync_sources()
    q = lambda sql: pd.read_sql(sql, st.db)       # noqa: E731
    ev = q("SELECT state, as_of_date, measure, party, 'county' lvl, source_id FROM ev_county_latest "
           "UNION ALL SELECT state, as_of_date, measure, party, 'state', source_id FROM ev_state_latest")
    srcs = q("SELECT state, source_id, access, kind FROM sources")
    ev = ev[~ev["source_id"].isin(srcs.loc[srcs["kind"] == "secondary", "source_id"])]     # secondary compilations are never official coverage
    last = q("SELECT l.state, l.source_id, l.ts, l.outcome, l.message FROM fetch_log l JOIN "
             "(SELECT source_id, MAX(rowid) r FROM fetch_log WHERE outcome LIKE 'collector%' "
             "GROUP BY source_id) m ON l.rowid=m.r")
    rows = []
    for state in sorted((set(srcs["state"]) | set(ev["state"])) - {"us"}):
        e = ev[ev["state"] == state]
        s = srcs[srcs["state"] == state]
        lg = last[last["source_id"].isin(s["source_id"])]
        bad = lg[lg["outcome"].isin(["collector_failed", "collector_error"])]
        part = lg[lg["outcome"] == "collector_partial"]
        if len(lg):                              # each source's latest collector run
            fetch = ("FAILED " + ",".join(bad["source_id"]) + " " if len(bad) else "") + \
                ("partial " + ",".join(part["source_id"]) + " " if len(part) else "") + \
                (f"ok {lg['ts'].max()}" if not len(bad) else f"(last run {lg['ts'].max()})")
        elif len(s) and not (srcs[srcs["state"] == state].merge(pd.DataFrame({"source_id": [x.source_id for x in SOURCES.values() if x.fn]}))).empty:
            fetch = "never run"
        else:
            fetch = "no collector"
        cur = e[e["as_of_date"].str[:4] == "2026"]
        rows.append({"state": state, "latest_as_of": cur["as_of_date"].max() if len(cur) else "",
                     "measures": ",".join(sorted(set(cur["measure"]))), "party": "yes" if set(cur["party"]) - {"ALL"} else
                     ("no" if len(cur) else ""), "level": "+".join(sorted(set(cur["lvl"]))),
                     "last_fetch": fetch, "access": "; ".join(sorted(set(s.loc[~s["source_id"].str.startswith("legacy_"), "access"].str.slice(0, 40))))})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------- collectors: NC, FL, TX

NC_S3 = "https://s3.amazonaws.com/dl.ncsbe.gov"
NC_COUNTS_KEY = "ENRS/2026_11_03/absentee_counts_county_20261103.csv"


def _nc_validate(body: bytes) -> None:
    head = body[:300].decode("utf-8", "replace").splitlines()[0]
    if not head.startswith("county_name,abs_request_type,party_cd"):
        raise ValueError(f"unexpected NC header {head!r}")


def parse_nc_counts(body: bytes, as_of: str) -> tuple[list[dict], list[dict]]:
    """NCSBE absentee_counts_county_<election>.csv (ACCEPTED ballots by county x request type x party x demographics).
    Returns (county rows, state rows). Mail = CIVILIAN + MILITARY + OVERSEAS; in person = EARLY VOTING + ONE-STOP."""
    d = pd.read_csv(io.BytesIO(body), dtype={"county_name": str}, usecols=["county_name", "abs_request_type", "party_cd"]
                    + [pd.read_csv(io.BytesIO(body), nrows=0).columns[-1]])
    n = d.columns[-1]
    kind = d["abs_request_type"].str.upper()
    d["measure"] = kind.map(lambda k: "inperson_early" if k in ("EARLY VOTING", "ONE-STOP") else "mail_accepted")
    d["party"] = d["party_cd"].map({"DEM": "D", "REP": "R", "UNA": "U"}).fillna("other")
    g = d.groupby(["county_name", "measure", "party"])[n].sum().reset_index()
    cells: dict[str, dict[tuple[str, str], int]] = {}
    for c, m, p, v in g.itertuples(index=False):
        cells.setdefault(c, {})[(m, p)] = int(v)
    crow, srow = [], {}
    for c, cell in cells.items():
        for m in ("inperson_early", "mail_accepted"):
            cell[(m, "ALL")] = sum(v for (mm, p), v in cell.items() if mm == m and p != "ALL")
        for p in PARTIES:
            cell[("total", p)] = cell.get(("inperson_early", p), 0) + cell.get(("mail_accepted", p), 0)
        fips = county_fips("nc", c)
        crow += long_rows("nc", as_of, cell, county=fips)
        for k, v in cell.items():
            srow[k] = srow.get(k, 0) + v
    return crow, long_rows("nc", as_of, srow)


@source("nc", "nc_abs_counts", f"{NC_S3}/{NC_COUNTS_KEY}", kind="county+state", format="csv",
        cadence="daily ~08:40 UTC (S3 object, conditional GET)", licence="public record (NCSBE, ReadMe_PUBLIC_DATA)",
        notes="ACCEPTED absentee ballots by county x type x party; mail_accepted = civilian+military+overseas, "
              "inperson_early = early voting + one-stop; party UNA->U, others->other. As-of = day before the object's "
              "Last-Modified (cumulative through the previous day's returns, verified against the per-ballot file). "
              "State rows = sum of counties.")
def collect_nc_abs_counts(st: Store) -> None:
    r = st.fetch("nc_abs_counts", f"{NC_S3}/{NC_COUNTS_KEY}", ext="csv", validate=_nc_validate)
    if r.changed or r.needs_parse:
        lm = r.last_modified or st.db.execute("SELECT last_modified FROM snapshots WHERE snapshot_id=?",
                                              (r.snapshot_id,)).fetchone()[0]
        as_of = (pd.Timestamp(lm).tz_convert("UTC") - pd.Timedelta(days=1)).strftime("%Y-%m-%d") if lm else \
            (datetime.fromtimestamp(r.path.stat().st_mtime, timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        crow, srow = parse_nc_counts(r.body, as_of)
        st.add_rows(crow, source_id="nc_abs_counts", snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
        st.add_rows(srow, source_id="nc_abs_counts", snapshot_id=r.snapshot_id, level="state")


FL_STATS = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats"
FL_FILES = "https://electionfiles.floridados.gov/countyballotreportfiles"
FL_PARTY = {"TotalDem": "D", "TotalRep": "R", "TotalNpa": "U", "TotalOth": "other", "GrandTotal": "ALL"}


def _fl_validate(body: bytes) -> None:
    head = body[:200].decode("utf-8-sig", "replace")
    if not head.startswith("ElectionNumber\tElectionDate\tElectionName\tCountyName\tStatType"):
        raise ValueError("unexpected FL header")


def parse_fl_stats(body: bytes) -> tuple[str, dict[str, dict[str, int]]]:
    """Stats_<n>_<Vbm*|Early*>.txt -> (as_of date = day before the latest CompileDate, {county or 'STATE': {party: n}})."""
    d = pd.read_csv(io.BytesIO(body), sep="\t", dtype=str, encoding="utf-8-sig")
    stamp = pd.to_datetime(d["CompileDate"].str.split().str[0], format="%m/%d/%Y").max()
    out: dict[str, dict[str, int]] = {}
    for _, r in d.iterrows():
        k = "STATE" if r["CountyName"].strip() == "State Totals" else r["CountyName"].strip()
        out[k] = {p: num(r[c]) for c, p in FL_PARTY.items()}
    return (stamp - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), out


def fl_rows(parts: dict[str, tuple[str, dict]]) -> tuple[list[dict], list[dict]]:
    """parts: {'vbm_voted'|'early_voted'|'vbm_provided': parse_fl_stats(...)} -> (county rows, state rows).
    total = mail + early and mail_requested = returned + provided-not-yet-returned only when both as-of dates agree."""
    crow, srow = [], []
    def emit(as_of: str, units: dict[str, dict[tuple[str, str], int]]):
        for k, cell in units.items():
            if k == "STATE":
                srow.extend(long_rows("fl", as_of, cell))
            else:
                crow.extend(long_rows("fl", as_of, cell, county=county_fips("fl", k)))
    meas = {"vbm_voted": "mail_returned", "early_voted": "inperson_early"}
    for key, m in meas.items():
        if key in parts:
            as_of, units = parts[key]
            emit(as_of, {k: {(m, p): v for p, v in c.items()} for k, c in units.items()})
    if "vbm_voted" in parts and "early_voted" in parts and parts["vbm_voted"][0] == parts["early_voted"][0]:
        a, b = parts["vbm_voted"][1], parts["early_voted"][1]
        emit(parts["vbm_voted"][0], {k: {("total", p): a[k][p] + b[k][p] for p in a[k]} for k in a if k in b})
    if "vbm_voted" in parts and "vbm_provided" in parts and parts["vbm_voted"][0] == parts["vbm_provided"][0]:
        a, b = parts["vbm_voted"][1], parts["vbm_provided"][1]
        emit(parts["vbm_voted"][0], {k: {("mail_requested", p): a[k][p] + b[k][p] for p in a[k]} for k in a if k in b})
    return crow, srow


@source("fl", "fl_county_stats", FL_STATS, kind="county+state", format="txt (tab-separated)",
        cadence="daily (county compilations ~08:00 ET)", licence="public record (FL Division of Elections)",
        notes="County Vote-by-Mail / Early Voting statistics by party: 'Voted Vote-by-Mail' -> mail_returned, "
              "'Voted Early' -> inperson_early, total = both; mail_requested = voted + 'Provided (not yet returned)'. "
              "As-of = day before the file's CompileDate. Party: Npa->U, Oth->other. Past elections have only final files.")
def collect_fl_county_stats(st: Store) -> None:
    page = st.request(FL_STATS)
    if page.status_code != 200:
        raise FetchError(f"FL stats page HTTP {page.status_code}")
    ids = [int(x) for x in re.findall(r"Stats_(\d+)_VbmVoted\.txt", page.text)]
    if not ids:
        raise FetchError("FL stats page lists no Stats_<n>_VbmVoted.txt")
    n = max(ids)
    fetched, parts = {}, {}
    for key, name in (("vbm_voted", "VbmVoted"), ("early_voted", "EarlyVoted"), ("vbm_provided", "VbmProvided")):
        r = st.fetch("fl_county_stats", f"{FL_FILES}/Stats_{n}_{name}.txt", ext="txt", part=key, validate=_fl_validate)
        if r.ok:
            fetched[key] = r
            parts[key] = parse_fl_stats(r.body)
    if not fetched or not any(r.changed or r.needs_parse for r in fetched.values()):
        return
    crow, srow = fl_rows(parts)
    last = max(fetched.values(), key=lambda r: r.snapshot_id)
    st.add_rows(crow, source_id="fl_county_stats", snapshot_id=last.snapshot_id, level="county", mark_parsed=False)
    st.add_rows(srow, source_id="fl_county_stats", snapshot_id=last.snapshot_id, level="state", mark_parsed=False)
    for r in fetched.values():
        st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (r.snapshot_id,))
    st.db.commit()


TX_API = "https://goelect.txelections.civixapps.com/api-ivis-system/api/v1/getFile"


def _tx_payload(body: bytes) -> dict:
    import base64
    j = json.loads(body)
    return json.loads(base64.b64decode(j["upload"])) if "upload" in j else j


def _tx_validate(body: bytes) -> None:
    j = _tx_payload(body)
    rows = j["turnout_by_county"]
    if not rows or not j.get("early_voting_date"):
        raise ValueError("no turnout_by_county / early_voting_date")
    if sum(r.get("in_person_votes_on_date") or 0 for r in rows) == 0:       # day not reported yet: repeats the last day
        raise Skip("no in-person votes on this date yet")


def parse_tx_evr(body: bytes) -> tuple[list[dict], list[dict]]:
    """EVR_EARLYVOTING getFile response -> county rows (cumulative at close of business of early_voting_date) + state sums."""
    j = _tx_payload(body)
    as_of = j["early_voting_date"]
    crow, tot = [], {}
    for r in j["turnout_by_county"]:
        if r["name"] == "TOTAL":
            continue
        ip, ml = int(r.get("total_in_person_votes_for_election") or 0), int(r.get("total_mail_votes_for_election") or 0)
        cell = {("inperson_early", "ALL"): ip, ("mail_returned", "ALL"): ml, ("total", "ALL"): ip + ml}
        crow += long_rows("tx", as_of, cell, county=county_fips("tx", r["name"]))
        for k, v in cell.items():
            tot[k] = tot.get(k, 0) + v
    return crow, long_rows("tx", as_of, tot)


@source("tx", "tx_evr", "https://goelect.txelections.civixapps.com/ivis-evr-ui/evr", kind="county+state", format="json (base64 payload)",
        cadence="daily, close of business of each early-voting day (counties report ~11:00 CT next morning)",
        licence="public record (TX SOS; robots.txt allows /)",
        notes="Cumulative early voting by county: in-person (inperson_early) and mail ballots (mail_returned); unofficial. "
              "No party/age breakdown is published. The API is the JSON the public EVR page itself loads; "
              "a day whose in-person count for the date is zero (not yet reported) is skipped.")
def collect_tx_evr(st: Store) -> None:
    import base64
    lst = st.request(TX_API, params={"type": "EVR_ELECTION"})
    if lst.status_code != 200:
        raise FetchError(f"TX election list HTTP {lst.status_code}")
    el = next(e for e in json.loads(base64.b64decode(lst.json()["upload"]))["elections"]
              if e["election_date"] == "11/03/2026" and "GENERAL" in e["election_name"])
    days = sorted((pd.Timestamp(d["date"]) for d in el["early_voting_dates"]), reverse=True)
    local = utcnow().astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    past = [d for d in days if d <= pd.Timestamp(local).normalize()]
    # every day never fetched (full history on the first run) + the two newest days (counties amend late)
    for day in [d for i, d in enumerate(past[::-1]) if i >= len(past) - 2 or not st.last_valid("tx_evr", d.strftime("%Y%m%d"))][::-1]:
        r = st.fetch("tx_evr", TX_API, ext="json", part=day.strftime("%Y%m%d"), validate=_tx_validate,
                     params={"type": "EVR_EARLYVOTING", "electionId": el["id"], "electionDate": day.strftime("%m/%d/%Y")})
        if r.needs_parse:
            crow, srow = parse_tx_evr(r.body)
            st.add_rows(crow, source_id="tx_evr", snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
            st.add_rows(srow, source_id="tx_evr", snapshot_id=r.snapshot_id, level="state")


# --------------------------------------------------------------------------------------- legacy ingest

def ingest_legacy(st: Store) -> str:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import us_ev_legacy
    return us_ev_legacy.ingest_legacy(st)


# --------------------------------------------------------------------------------------- CLI

def loop(every_s: float, until: str, states, log) -> None:
    """Run `collect` in a fresh subprocess every `every_s` seconds until `until` (so new/edited collector modules are
    picked up and a crash or hang never kills the schedule)."""
    import subprocess
    end = datetime.fromisoformat(until)
    cmd = [sys.executable, str(Path(__file__).resolve()), "collect"] + (["--states", ",".join(states)] if states else [])
    while utcnow() < end:
        t0 = time.monotonic()
        log(f"run: {' '.join(cmd)}")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
            for line in (p.stdout + p.stderr).splitlines():
                log("  " + line)
            log(f"collect exit {p.returncode}")
        except Exception as e:                                   # noqa: BLE001 - the loop must survive anything
            log(f"collect crashed: {type(e).__name__}: {e}")
        wait = max(60.0, every_s - (time.monotonic() - t0))
        if utcnow() + timedelta(seconds=wait) >= end:
            break
        log(f"sleeping {wait / 60:.0f} min")
        time.sleep(wait)
    log(f"loop finished (until {until})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="us_ev_db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "loop"):
        p = sub.add_parser(name)
        p.add_argument("--states", default="")
        p.add_argument("--sources", default="")
        if name == "loop":
            p.add_argument("--every-hours", type=float, default=3.0)
            p.add_argument("--until", default=LOOP_END)
    sub.add_parser("status")
    sub.add_parser("sources")
    sub.add_parser("ingest-legacy")
    a = ap.parse_args(argv)
    sl = lambda s: [x.strip().lower() for x in s.split(",") if x.strip()] or None   # noqa: E731
    if a.cmd in ("collect", "loop"):
        logf = ev_root() / "logs" / f"{a.cmd}_{utcnow():%Y%m%d}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)

        def log(msg: str) -> None:
            line = f"{utcnow():%Y-%m-%dT%H:%M:%SZ} {msg}"
            print(line, flush=True)
            with open(logf, "a") as fh:
                fh.write(line + "\n")
        if a.cmd == "collect":
            res = run_collect(sl(a.states), sl(a.sources), log=log)
            return 1 if any(v.startswith(("ERROR", "failed")) for v in res.values()) else 0
        loop(a.every_hours * 3600, a.until, sl(a.states), log)
        return 0
    st = Store()
    try:
        if a.cmd == "status":
            with pd.option_context("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 60):
                print(status_table(st).to_string(index=False))
        elif a.cmd == "sources":
            load_extensions()
            st.sync_sources()
            with pd.option_context("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 70):
                print(pd.read_sql("SELECT state, source_id, kind, format, cadence, access, url FROM sources ORDER BY 1,2", st.db).to_string(index=False))
        elif a.cmd == "ingest-legacy":
            print(ingest_legacy(st))
    finally:
        st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
