"""US midterms 2026-11-03: official county feeds, county priors, election-night nowcast, dashboard export.

Feeds (recorded passively by `brunder snap us_midterms_2026`, deploy/brunder-snap-us.service; this module never
polls them on the night, it reads the recorder's snapshots):
  ca  California SoS API          /returns/governor/county/all (+ /returns/status)       58 counties
  tx  Texas SoS Civix ENR 53815   election/<id> (statewide) + countyInfo/<id>             254 counties
  oh  Ohio SoS                    NIST SP 1500-100 (1622) XML + election summary CSV      88 counties
  ak  Alaska Division of Elections enr26/results/statewide.js                            40 house districts
  ga  Georgia SoS Enhanced Voting  elections/Georgia/<id>/data/ballot-item/<item>         159 counties

Every parser turns snapshot bytes into the same long table (COLS): one row per unit × race × candidate with the
unit's progress fields (precincts/polling places reporting, ballots counted, registered, ballots known to be
outstanding). `level` is "county"/"district" for units and "state" for the feed's own statewide total.

Model: `brunder.nowcast` with units = counties (AK: house districts) and region = state, on the two-party
(Democrat vs Republican) share. The US-specific part is `f`, the share of a county's expected vote already
counted: precincts reporting says little about how many mail/provisional ballots remain, so f = n / E with E
an expected-vote estimate per county (prior-race votes × turnout ratio, re-scaled on the night by the counties
whose count is known to be complete). See docs/us_midterms.md for settings and their pre-election evidence.

Output: the dashboard data contract (meta.json, timeline.json, units/<race>.json) for every configured race,
from the snapshot archive up to a time (replay) or repeatedly up to now (live).
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import csv
import gzip
import io
import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import kalshi, live, nowcast
from .paths import PROCESSED, RAW
from .paths import ROOT as ROOT_DIR
from .snap import SNAP, _name

TAG = "us_midterms_2026"
EID = "us2026"
US_RAW = RAW / "us"
US_PROC = PROCESSED / "us2026"
UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
COLS = ["level", "unit_id", "name", "race", "candidate", "party", "votes", "early",
        "reporting", "units", "ballots", "registered", "outstanding"]

FEEDS = {           # snapshot keys (snap._name of the recorded URL); "*" = every key with that prefix
    "ca": {"results": _name("https://api.sos.ca.gov/returns/governor/county/all"),
           "status": _name("https://api.sos.ca.gov/returns/status")},
    "tx": {"election": _name("https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/53815"),
           "county": _name("https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/countyInfo/53815")},
    "oh": {"xml": _name("https://liveresults.ohiosos.gov/Api/v1/download?filename=VSSC1622XmlFileBlob"),
           "csv": _name("https://liveresults.ohiosos.gov/Api/v1/Contest/GetElectionSummaryCsv")},
    "ak": {"js": _name("https://www.elections.alaska.gov/enr26/results/statewide.js")},
    "ga": {"item": "ga_enr_Georgia_item_*"},
}
STATES = {"ca": "California", "tx": "Texas", "oh": "Ohio", "ga": "Georgia", "ak": "Alaska"}


# ------------------------------------------------------------------ units

def _norm(name: str) -> str:
    return re.sub(r"[^A-Z]", "", re.sub(r"\s+COUNTY\b", "", name.upper()))


def counties(st: str) -> pd.DataFrame:
    """Census 2020 county list for a state: unit_id (us-<st>-<5-digit FIPS>), name, key (normalised name).
    Texas rows are in the Texas SoS county-number order (ENR county id = row number), which is not FIPS order."""
    if st == "ak":
        return pd.DataFrame({"unit_id": [f"us-ak-hd{d:02d}" for d in range(1, 41)],
                             "name": [f"House District {d}" for d in range(1, 41)],
                             "key": [str(d) for d in range(1, 41)]})
    fips = {"ca": "06", "tx": "48", "oh": "39", "ga": "13"}[st]
    items = [x.rsplit(":", 1) for x in _COUNTIES[st].split(",")]
    return pd.DataFrame({"unit_id": [f"us-{st}-{fips}{c}" for _, c in items], "name": [n for n, _ in items],
                         "key": [_norm(n) for n, _ in items]})


def _ids(st: str) -> dict[str, tuple[str, str]]:
    c = counties(st)
    return dict(zip(c["key"], zip(c["unit_id"], c["name"])))


def race_key(name: str) -> str | None:
    s = " ".join(re.sub(r"[^a-z]", " ", name.lower()).split())
    if s.startswith("governor"):
        return "gov"
    if re.match(r"(u s |us |united states )senat", s) and "district" not in s:
        return "sen"
    if s.startswith("president"):
        return "pres"
    return None


def party_code(s: str | None) -> str:
    s = (s or "").strip().upper()
    for full, code in (("DEM", "DEM"), ("REP", "REP"), ("LIB", "LIB"), ("GR", "GRN")):
        if s.startswith(full):
            return code
    return s[:3] if s else ""


def _int(x) -> float:
    if x is None or x == "":
        return np.nan
    return float(str(x).replace(",", "").replace("=", "").replace('"', "").strip() or "nan")


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    df["votes"] = df["votes"].astype(float)
    return df


# ------------------------------------------------------------------ parsers (snapshot bytes -> rows)

def parse_ca(results: bytes, status: bytes | None = None) -> pd.DataFrame:
    """CA SoS API: list of {raceTitle "Governor - <County> County Results" | "... - Statewide Results",
    Reporting "x% (k of n) precincts reporting", candidates[{Name, Party, Votes}]}. /returns/status adds per-county
    ballots counted (voterTurnout) and registration. Precincts reporting is NOT the share of ballots counted in CA
    (vote-by-mail and provisionals are counted for weeks)."""
    ids = _ids("ca")
    prog = {}
    if status:
        for k, v in json.loads(status).items():
            if isinstance(v, dict) and "countyName" in v:
                key = "STATE" if k == "statewide" else _norm(v["countyName"])
                prog[key] = (_int(v.get("voterTurnout")), _int(v.get("totalRegisteredVoters")))
    rows = []
    for r in json.loads(results):
        race_name, _, where = r["raceTitle"].partition(" - ")
        race = race_key(race_name) or race_name
        m = re.search(r"\(([\d,]+) of ([\d,]+)\)", r.get("Reporting", ""))
        rep, tot = (_int(m.group(1)), _int(m.group(2))) if m else (np.nan, np.nan)
        if where.startswith("Statewide"):
            level, uid, name, key = "state", "us-ca", "California", "STATE"
        else:
            key = _norm(where.replace(" Results", ""))
            uid, name = ids[key]
            level = "county"
        ballots, reg = prog.get(key, (np.nan, np.nan))
        for c in r["candidates"]:
            rows.append({"level": level, "unit_id": uid, "name": name, "race": race, "candidate": c["Name"],
                         "party": party_code(c.get("Party")), "votes": _int(c["Votes"]), "early": np.nan,
                         "reporting": rep, "units": tot, "ballots": ballots, "registered": reg,
                         "outstanding": np.nan})
    return _frame(rows)


def _b64json(s: str):
    return json.loads(base64.b64decode(s))


def parse_tx(election: bytes | None, county: bytes) -> pd.DataFrame:
    """TX SoS Civix ENR: payloads are base64 JSON. countyInfo: {county id: {N, Summary{PRR, PRP precincts,
    PLR, NPL polling places, VC ballots, RV registered}, Races{race id: {N, C{cand id: {N, P, V, EV}}}}}};
    the election file's StateWide/Federal races carry the statewide totals. EV = the feed's early-vote count.
    County id = Texas county number (row order of counties('tx'))."""
    c = counties("tx")
    rows = []
    for cid, x in _b64json(json.loads(county)["upload"]).items():
        uid, name = c.loc[int(cid) - 1, ["unit_id", "name"]]
        s = x.get("Summary") or {}
        if isinstance(s, str):
            s = json.loads(s.replace("'", '"'))
        rep, tot = (s.get("PLR"), s.get("NPL")) if (s.get("NPL") or 0) > 0 else (s.get("PRR"), s.get("PRP"))
        for r in x.get("Races", {}).values():
            race = race_key(r["N"])
            if race is None:
                continue
            for cand in r["C"].values():
                rows.append({"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": cand["N"],
                             "party": party_code(cand.get("P")), "votes": float(cand.get("V") or 0),
                             "early": float(cand.get("EV") or 0), "reporting": _int(rep), "units": _int(tot),
                             "ballots": _int(s.get("VC")), "registered": _int(s.get("RV")), "outstanding": np.nan})
    if election:
        e = json.loads(election)
        for k in ("StateWide", "Federal"):
            if not isinstance(e.get(k), str) or len(e[k]) < 40:
                continue
            for r in _b64json(e[k]).get("Races", []):
                race = race_key(r["N"])
                if race is None:
                    continue
                for cand in r["Candidates"]:
                    rows.append({"level": "state", "unit_id": "us-tx", "name": "Texas", "race": race,
                                 "candidate": cand["N"], "party": party_code(cand.get("P")),
                                 "votes": float(cand.get("V") or 0), "early": float(cand.get("EV") or 0),
                                 "reporting": np.nan, "units": np.nan, "ballots": np.nan, "registered": np.nan,
                                 "outstanding": np.nan})
    return _frame(rows)


_NIST = "{http://grouper.ieee.org/groups/1622/groups/2/V1/1622_2-election_results_reporting.xsd}"
_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"


def parse_oh_summary(data: bytes) -> pd.DataFrame:
    """OH election summary CSV: County, Registered Voters, Ballots Counted, ..., Outstanding UOCAVA Absentees,
    Outstanding Provisionals, Total Precincts, Election Day Precincts Reporting, ..."""
    ids = _ids("oh")
    rd = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    head = [h.strip() for h in next(rd)]
    out = []
    for row in rd:
        if not row:
            continue
        d = dict(zip(head, row))
        key = "STATE" if d["County"].strip() == "Totals" else _norm(d["County"])
        uid = "us-oh" if key == "STATE" else ids[key][0]
        out.append({"unit_id": uid, "registered": _int(d["Registered Voters"]), "ballots": _int(d["Ballots Counted"]),
                    "outstanding": _int(d["Outstanding UOCAVA Absentees"]) + _int(d["Outstanding Provisionals"]),
                    "reporting": _int(d["Election Day Precincts Reporting"]), "units": _int(d["Total Precincts"])})
    return pd.DataFrame(out)


def parse_oh(xml: bytes, summary: bytes | None = None) -> pd.DataFrame:
    """OH SoS NIST 1622 XML: GpUnit (Type County, not electoral district) carries SubUnitsReported/TotalSubUnits,
    VotersParticipated/VotersRegistered and SummaryCounts with BallotsOutstanding (absentee, provisional);
    each BallotSelection has VoteCounts (Type Total) per GpUnitId. The summary CSV, when given, overrides the
    progress fields (same quantities, published separately)."""
    root = ET.fromstring(xml)
    ids = _ids("oh")
    units = {}
    for g in root.iter(f"{_NIST}GpUnit"):
        if g.get("Type") == "State":
            units[g.get("ObjectId")] = ("state", "us-oh", "Ohio", {})
        elif g.get("Type") == "County" and g.get("IsElectoralDistrict") == "false":
            uid, name = ids[_norm(g.get("Name"))]
            out = sum(_int(s.get("BallotsOutstanding")) for s in g.findall(f"{_NIST}SummaryCounts")
                      if s.get("BallotsOutstanding") is not None)
            units[g.get("ObjectId")] = ("county", uid, name, {
                "reporting": _int(g.get("SubUnitsReported")), "units": _int(g.get("TotalSubUnits")),
                "ballots": _int(g.get("VotersParticipated")), "registered": _int(g.get("VotersRegistered")),
                "outstanding": float(out)})
    parties = {p.get("ObjectId"): p.get("Abbreviation") for p in root.iter(f"{_NIST}Party")}
    cands = {}
    for c in root.iter(f"{_NIST}Candidate"):
        cands[c.get("ObjectId")] = (c.findtext(f"{_NIST}BallotName/{_NIST}LanguageString") or "",
                                    parties.get(c.findtext(f"{_NIST}PartyId"), ""))
    if summary:
        prog = parse_oh_summary(summary).drop_duplicates("unit_id").set_index("unit_id").to_dict("index")
        for k, (lvl, uid, name, p) in units.items():
            if uid in prog:
                units[k] = (lvl, uid, name, {**p, **prog[uid]})
    rows = []
    for con in root.iter(f"{_NIST}Contest"):
        race = race_key(con.get("Name") or "")
        if race is None:
            continue
        for bs in con.findall(f"{_NIST}BallotSelection"):
            cid = (bs.findtext(f"{_NIST}CandidateId") or "").split()
            name, party = cands.get(cid[0], ("", "")) if cid else ("", "")
            for vc in bs.iter(f"{_NIST}VoteCounts"):
                if vc.get("Type") != "Total":
                    continue
                u = units.get(vc.findtext(f"{_NIST}GpUnitId"))
                if u is None:
                    continue
                lvl, uid, uname, p = u
                rows.append({"level": lvl, "unit_id": uid, "name": uname, "race": race, "candidate": name,
                             "party": party_code(party), "votes": _int(vc.get("Count")), "early": np.nan,
                             **{k: p.get(k, np.nan) for k in ("reporting", "units", "ballots", "registered",
                                                               "outstanding")}})
    return _frame(rows)


def parse_ak(js: bytes) -> pd.DataFrame:
    """AK Division of Elections statewide.js (JSON despite the name): contestNames, candidateNames
    ("Last, First (PTY)"), totals[contest] and districts[{number, contests}] with per contest b (ballots),
    t (votes), v (registered), p/r (precincts / reporting) and c[{n: candidate index, t: votes}]."""
    d = json.loads(js)
    names = d["candidateNames"]

    def rows_for(level, uid, name, contests):
        out = []
        for c in contests:
            race = race_key(d["contestNames"][c["n"]])
            if race is None:
                continue
            for x in c["c"]:
                cand = names[x["n"]]
                m = re.search(r"\(([A-Z]+)(?:/[A-Z]+)?\)\s*$", cand)
                out.append({"level": level, "unit_id": uid, "name": name, "race": race, "candidate": cand,
                            "party": party_code(m.group(1)) if m else "", "votes": float(x["t"]), "early": np.nan,
                            "reporting": float(c.get("r", np.nan)), "units": float(c.get("p", np.nan)),
                            "ballots": float(c.get("b", np.nan)), "registered": float(c.get("v", np.nan)),
                            "outstanding": np.nan})
        return out

    rows = rows_for("state", "us-ak", "Alaska", d["totals"])
    for dist in d["districts"]:
        k = int(dist["number"])
        rows += rows_for("district", f"us-ak-hd{k:02d}", f"House District {k}", dist["contests"])
    return _frame(rows)


_GA_EARLY = re.compile(r"^(advanced? voting|absentee by mail)", re.I)   # 2020/22: "... Votes" suffix


def parse_ga(item: bytes) -> pd.DataFrame:
    """GA SoS Enhanced Voting ballot item (…/elections/Georgia/<id>/data/ballot-item/<item id>):
    ballotItemWithBreakdown {name, summaryResults.ballotOptions (statewide), breakdownResults[{locality.name,
    precinctReportingStatus{reportingUnits,totalUnits}, ballotOptions[{name, party.abbreviation, voteCount,
    groupResults[{groupName, voteCount}]}]}]}. early = Advanced Voting + Absentee by Mail."""
    b = json.loads(item)["ballotItemWithBreakdown"]
    race = race_key(b["name"][0]["text"])
    ids = _ids("ga")

    def opts(level, uid, name, options, status):
        out = []
        for o in options:
            early = sum(g["voteCount"] for g in o.get("groupResults") or []
                        if _GA_EARLY.match(g["groupName"][0]["text"].strip()))
            out.append({"level": level, "unit_id": uid, "name": name, "race": race,
                        "candidate": re.sub(r"\s*\((?:I\)\s*\()?[A-Za-z]+\)\s*$", "", o["name"][0]["text"]),
                        "party": party_code((o.get("party") or {}).get("abbreviation")),
                        "votes": float(o["voteCount"]), "early": float(early),
                        "reporting": _int((status or {}).get("reportingUnits")),
                        "units": _int((status or {}).get("totalUnits")), "ballots": np.nan,
                        "registered": np.nan, "outstanding": np.nan})
        return out

    rows = opts("state", "us-ga", "Georgia", b["summaryResults"]["ballotOptions"], b.get("reportingStatus"))
    for br in b.get("breakdownResults") or []:
        uid, name = ids[_norm(br["locality"]["name"][0]["text"])]
        rows += opts("county", uid, name, br["ballotOptions"], br.get("precinctReportingStatus"))
    return _frame(rows)


def parse_feed(kind: str, blobs: dict[str, bytes | list[bytes] | None]) -> pd.DataFrame:
    """Rows for one feed from its latest snapshots. blobs keys as in FEEDS[kind]; '*' keys give a list."""
    match kind:
        case "ca":
            return parse_ca(blobs["results"], blobs.get("status")) if blobs.get("results") else _frame([])
        case "tx":
            return parse_tx(blobs.get("election"), blobs["county"]) if blobs.get("county") else _frame([])
        case "oh":
            return parse_oh(blobs["xml"], blobs.get("csv")) if blobs.get("xml") else _frame([])
        case "ak":
            return parse_ak(blobs["js"]) if blobs.get("js") else _frame([])
        case "ga":
            parts = [parse_ga(b) for b in blobs.get("item") or []]
            return pd.concat(parts, ignore_index=True) if parts else _frame([])
    raise ValueError(kind)


# ------------------------------------------------------------------ snapshot archive

def _check(body: bytes) -> None:
    """Raise if a snapshot body is not a complete document (JSON, XML, or UTF-8 text such as the OH CSV)."""
    head = body.lstrip()[:1]
    if not head:
        raise ValueError("empty snapshot")
    if head in (b"{", b"["):
        json.loads(body)
    elif head == b"<":
        ET.fromstring(body)
    else:
        body.decode("utf-8")


class Archive:
    """The recorder's output directory: <root>/<key>/<YYYYmmddTHHMMSS.ffffffZ>.<ext>[.gz] (+ index.jsonl;
    dot-directories such as the recorder's .tmp are ignored). Reads only what was recorded; never contacts the
    feeds. A snapshot that cannot be decompressed or is not a complete document is never cached and is skipped in
    favour of the previous valid snapshot of the same key."""

    CACHE = 32

    def __init__(self, root: Path):
        self.root = Path(root)
        self._files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        self._cache: dict[Path, bytes] = {}
        self.bad: dict[Path, int] = {}               # malformed snapshot -> its size when it failed

    def refresh(self) -> None:
        files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        if not self.root.exists():
            self._files = files
            return
        for d in self.root.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                lst = []
                for f in d.iterdir():
                    m = re.match(r"(\d{8}T\d{6}\.\d{6})Z", f.name)
                    if m:
                        lst.append((pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S.%f"), tz="UTC"), f))
                files[d.name] = sorted(lst)
        self._files = files

    def keys(self, pattern: str) -> list[str]:
        if pattern.endswith("*"):
            return sorted(k for k in self._files if k.startswith(pattern[:-1]))
        return [pattern] if pattern in self._files else []

    def times(self) -> list[pd.Timestamp]:
        return sorted({t for lst in self._files.values() for t, _ in lst})

    def at(self, key: str, t: pd.Timestamp) -> Path | None:
        """Latest valid snapshot of `key` at or before t (malformed ones are skipped)."""
        lst = self._files.get(key, [])
        i = bisect.bisect_right(lst, t, key=lambda x: x[0])
        while i:
            f = lst[i - 1][1]
            if self._load(f) is not None:
                return f
            i -= 1
        return None

    def _load(self, f: Path) -> bytes | None:
        if f in self._cache:
            return self._cache[f]
        size = -1
        try:
            size = f.stat().st_size
            if self.bad.get(f) == size:              # known bad and unchanged since
                return None
            b = f.read_bytes()
            b = gzip.decompress(b) if f.suffix == ".gz" else b
            _check(b)
        except Exception as e:                       # partial write, bad gzip, truncated JSON/XML
            if f not in self.bad:
                print(f"snapshot skipped: {f.parent.name}/{f.name}: {type(e).__name__}: {e}", flush=True)
            self.bad[f] = size
            return None
        self.bad.pop(f, None)
        if len(self._cache) >= self.CACHE:           # TX files are ~5 MB and change every poll
            self._cache.pop(next(iter(self._cache)))
        self._cache[f] = b
        return b

    def read(self, f: Path) -> bytes:
        """Bytes of a snapshot returned by at() (gunzipped, validated)."""
        b = self._load(f)
        if b is None:
            raise ValueError(f"malformed snapshot {f}")
        return b


def feed_files(arc: Archive, kind: str, t: pd.Timestamp) -> dict[str, Path | list[Path] | None]:
    """Latest valid snapshot ≤ t of every key of a feed (a list for '*' keys)."""
    out: dict[str, Path | list[Path] | None] = {}
    for role, pattern in FEEDS[kind].items():
        files = [f for f in (arc.at(k, t) for k in arc.keys(pattern)) if f is not None]
        out[role] = files if pattern.endswith("*") else (files[0] if files else None)
    return out


def feed_blobs(arc: Archive, files: dict[str, Path | list[Path] | None]) -> dict:
    return {role: [arc.read(x) for x in f] if isinstance(f, list) else (arc.read(f) if f else None)
            for role, f in files.items()}


def pm_prices(arc: Archive, f: Path | None) -> dict[str, float]:
    """YES price (mid if spread ≤ 10c, else last) per 'event/market' from one polymarket_books snapshot."""
    if f is None:
        return {}
    out = {}
    for k, book in json.loads(arc.read(f)).items():
        p = live.top(book)["price"]
        if np.isfinite(p):
            out[k] = float(p)
    return out


# ------------------------------------------------------------------ priors (official certified county results)

def _fetch(url: str, dest: Path) -> bytes:
    """Download once into data/raw/us (cached); polite single request."""
    if dest.exists():
        return dest.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
    dest.write_bytes(r.content)
    return r.content


def _xlsx_rows(data: bytes) -> list[list[str | None]]:
    """First worksheet of an .xlsx as rows of cell strings (no openpyxl dependency)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{ns}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    out = []
    for r in ET.fromstring(z.read(sheet)).iter(f"{ns}row"):
        row: dict[int, str | None] = {}
        for c in r.findall(f"{ns}c"):
            col = 0
            for ch in re.match(r"[A-Z]+", c.get("r")).group():
                col = col * 26 + ord(ch) - 64
            v = c.find(f"{ns}v")
            if v is None:
                isel = c.find(f"{ns}is")
                val = "".join(x.text or "" for x in isel.iter(f"{ns}t")) if isel is not None else None
            else:
                val = shared[int(v.text)] if c.get("t") == "s" else v.text
            row[col - 1] = val
        out.append([row.get(i) for i in range(max(row) + 1)] if row else [])
    return out


def _two_party(rows: pd.DataFrame) -> pd.DataFrame:
    """unit_id, dem, rep, total (all candidates) from parser-style rows (one race, unit level)."""
    u = rows[rows["level"] != "state"]
    g = u.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"dem": g.get("DEM", 0), "rep": g.get("REP", 0), "total": g.sum(axis=1)}).reset_index()


def _ca_sov(year: int, race: str) -> pd.DataFrame:
    """CA SoS Statement of Vote, county × candidate long file (csv-candidates / csv-all-candidates .xlsx)."""
    name = {2022: "csv-candidates.xlsx", 2024: "csv-all-candidates.xlsx"}[year]
    data = _fetch(f"https://elections.cdn.sos.ca.gov/sov/{year}-general/sov/{name}", US_RAW / "ca" / f"{year}_{name}")
    rows = _xlsx_rows(data)
    head = [str(h).strip() if h else "" for h in rows[0]]
    df = pd.DataFrame([r + [None] * (len(head) - len(r)) for r in rows[1:]], columns=head).fillna("").astype(str)
    df = df[df["Contest Name"].map(lambda s: isinstance(s, str) and race_key(s) == race)]
    ids = dict(zip(counties("ca")["key"], counties("ca")["unit_id"]))
    df["unit_id"] = df["County Name"].map(lambda s: ids.get(_norm(s or "")))
    df["party"] = df["Party Name"].map(party_code)
    df["votes"] = df["Vote Total"].map(_int)
    df["level"] = "county"
    return _two_party(df.dropna(subset=["unit_id"]))


def _tx_tlc(election: int, office: int) -> pd.DataFrame:
    """Texas Legislative Council, Capitol Data Portal (CC BY): VTD-level returns. CNTYVTD[:3] = county FIPS;
    candidate columns '<Name><party letter>_<yy>G_<Office>'."""
    data = _fetch(f"https://ted.capitol.texas.gov/api/Offices/{election}/{office}/vtd",
                  US_RAW / "tx" / f"tlc_{election}_{office}_vtd.csv")
    df = pd.read_csv(io.BytesIO(data), dtype={"CNTYVTD": str})
    cand = [c for c in df.columns if c not in ("CNTYVTD", "VTDKEY")]
    g = df.assign(cty=df["CNTYVTD"].str[:3]).groupby("cty")[cand].sum()
    party = {c: c.split("_")[0][-1] for c in cand}
    return pd.DataFrame({"unit_id": "us-tx-48" + g.index.to_series(),
                         "dem": g[[k for k, p in party.items() if p == "D"]].sum(axis=1),
                         "rep": g[[k for k, p in party.items() if p == "R"]].sum(axis=1),
                         "total": g.sum(axis=1)}).reset_index(drop=True)


def _ga_enr(eid: str, race: str) -> pd.DataFrame:
    """GA SoS Enhanced Voting certified results: find the ballot item, fetch its county breakdown."""
    api = "https://results.sos.ga.gov/results/public/api/elections/Georgia"
    summary = json.loads(_fetch(f"{api}/{eid}/data", US_RAW / "ga" / f"{eid}_data.json"))
    item = next(b["id"] for b in summary["ballotItems"] if race_key(b["name"][0]["text"]) == race)
    data = _fetch(f"{api}/{eid}/data/ballot-item/{item}", US_RAW / "ga" / f"{eid}_{race}.json")
    return _two_party(parse_ga(data))


def _ak_primary(race: str) -> pd.DataFrame:
    """AK 2026 primary (certified 2026-08-31), by house district, from the same official ENR file the night feed
    uses. Pick-one top-four primary open to all voters: dem/rep = sums over candidates labelled DEM / REP."""
    dest = US_RAW / "ak" / "enr26_primary_statewide.js"
    data = _fetch("https://www.elections.alaska.gov/enr26/results/statewide.js", dest)
    if "Primary" not in json.loads(data)["name"]:
        dest.unlink()
        raise RuntimeError("enr26/statewide.js no longer holds the 2026 primary; restore the saved copy "
                           "(tests/fixtures/us/ak_statewide.js.gz is the same certified file)")
    rows = parse_ak(data)
    return _two_party(rows[rows["race"] == race])


def _medsl_pres(st: str, year: int = 2024) -> pd.DataFrame:
    """MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024 (CC0,
    doi:10.7910/DVN/VOQCHQ). Dataverse requires a guestbook, so it is not auto-downloaded: save the original CSV
    as data/raw/us/medsl/countypres_2000-2024.csv."""
    f = US_RAW / "medsl" / "countypres_2000-2024.csv"
    if not f.exists():
        raise FileNotFoundError(f"{f} missing (manual download, see docstring)")
    df = pd.read_csv(f, dtype={"county_fips": str})
    df = df[(df["year"] == year) & (df["state_po"] == st.upper())].copy()
    df["unit_id"] = f"us-{st}-" + df["county_fips"].str.zfill(5)
    df["party"] = df["party"].map(party_code)
    # some states report by vote mode; county totals are the TOTAL rows when present, else the sum of modes
    if (df["mode"] == "TOTAL").any():
        df = df[df["mode"] == "TOTAL"]
    df["votes"] = df["candidatevotes"].astype(float)
    df["level"] = "county"
    return _two_party(df)


PRIOR_SOURCES = {
    "ca_gov_2022": lambda: _ca_sov(2022, "gov"),
    "ca_pres_2024": lambda: _ca_sov(2024, "pres"),
    "tx_pres_2024": lambda: _tx_tlc(510, 1),
    "tx_sen_2024": lambda: _tx_tlc(510, 2),
    "tx_gov_2022": lambda: _tx_tlc(497, 37),
    "ga_pres_2024": lambda: _ga_enr("2024NovGen", "pres"),
    "ga_gov_2022": lambda: _ga_enr("2022NovGen", "gov"),
    "ga_sen_2022": lambda: _ga_enr("2022NovGen", "sen"),
    "ga_pres_2020": lambda: _ga_enr("2020NovGen", "pres"),
    "ak_sen_p2026": lambda: _ak_primary("sen"),
    "ak_gov_p2026": lambda: _ak_primary("gov"),
    "oh_pres_2024": lambda: _medsl_pres("oh"),
}
SOURCE_INFO = {
    "ca": ("California Secretary of State, Statement of Vote", "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results", "public record"),
    "tx": ("Texas Legislative Council, Capitol Data Portal", "https://data.capitol.texas.gov/", "CC BY"),
    "ga": ("Georgia Secretary of State, election results", "https://results.sos.ga.gov/results/public/Georgia", "public record"),
    "ak": ("Alaska Division of Elections, election results", "https://www.elections.alaska.gov/", "public record"),
    "oh": ("MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024", "https://doi.org/10.7910/DVN/VOQCHQ", "CC0 1.0"),
}


def build_priors(sources: list[str] | None = None) -> Path:
    """Fetch (cached under data/raw/us) and build data/processed/us2026/priors.csv: source, unit_id, dem, rep, total.
    Sources that cannot be built are reported and skipped."""
    parts = []
    for s in sources or list(PRIOR_SOURCES):
        try:
            parts.append(PRIOR_SOURCES[s]().assign(source=s))
            print(f"prior {s}: {len(parts[-1])} units", flush=True)
        except Exception as e:                       # one missing source must not block the others
            print(f"prior {s}: skipped ({type(e).__name__}: {e})", flush=True)
    US_PROC.mkdir(parents=True, exist_ok=True)
    out = US_PROC / "priors.csv"
    old = pd.read_csv(out) if out.exists() else pd.DataFrame(columns=["source"])
    new = pd.concat(parts, ignore_index=True)[["source", "unit_id", "dem", "rep", "total"]] if parts else old.iloc[:0]
    pd.concat([old[~old["source"].isin(new["source"].unique())], new], ignore_index=True).to_csv(out, index=False)
    return out


def load_priors(path: Path | None = None) -> pd.DataFrame:
    return pd.read_csv(path or US_PROC / "priors.csv")


# ------------------------------------------------------------------ config + model

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


@dataclass(frozen=True)
class CountRules:
    """How much of a county's vote is in (f), given what US feeds report. See docs/us_midterms.md."""
    turnout_ratio: float = 1.0         # expected 2026 two-party votes = ratio × prior-race two-party votes
    f_max: float = 0.95                # cap on f from vote counts alone (turnout above expectation)
    f_complete: float = 0.0            # f assumed once every precinct/polling place reported (0 = ignore flag)
    use_outstanding: bool = False      # OH: remaining = reported outstanding ballots once precincts are in
    learn_turnout: bool = False        # re-scale E of unfinished counties by complete counties' turnout
    turnout_prior_votes: float = 200_000.0   # pseudo-votes holding the learned ratio at 1
    vote_type: bool = False            # split counties into early+mail / rest units (feeds reporting early votes)
    early_share: float = 0.6           # expected early+mail share of a county's vote before it posts any


def rules(cfg: dict) -> CountRules:
    C = cfg.get("count", {})
    return CountRules(**{f.name: C[f.name] for f in fields(CountRules) if f.name in C})


def params(cfg: dict) -> nowcast.Params:
    M = cfg.get("model", {})
    keys = ("kappa", "rho", "tau", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf")
    return nowcast.Params(**{k: M[k] for k in keys if k in M})


def race_prior(cfg: dict, priors: pd.DataFrame) -> pd.DataFrame | None:
    """unit_id, name, s0 (two-party Dem share of the share race), V (two-party votes of the votes race).
    None when a source is missing."""
    P, st = cfg["prior"], cfg["election"]["state"]
    have = set(priors["source"])
    if P["share"] not in have or P["votes"] not in have:
        return None
    sh = priors[priors["source"] == P["share"]].set_index("unit_id")
    vo = priors[priors["source"] == P["votes"]].set_index("unit_id")
    c = counties(st).set_index("unit_id")
    out = pd.DataFrame({"name": c["name"],
                        "s0": sh["dem"] / (sh["dem"] + sh["rep"]),
                        "V": (vo["dem"] + vo["rep"])}).reindex(c.index)
    return out.dropna().reset_index()


def count_state(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> pd.DataFrame:
    """Per unit: n (two-party votes counted), a (Dem votes), ne/ae (of which early + mail, when the feed says),
    f (share of expected two-party vote in), E (expected two-party votes), complete flag. Units absent from the
    feed are uncounted."""
    E_, R = cfg["election"], rules(cfg)
    u = rows[(rows["level"] != "state") & (rows["race"] == E_["race"])]
    pa, pb = E_["a"]["party"], E_["b"]["party"]
    cols = ["a", "b", "ea", "eb", "reporting", "units", "ballots", "outstanding"]
    if len(u):
        g = u.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
        e = u.assign(early=u["early"].fillna(0)).pivot_table(index="unit_id", columns="party", values="early",
                                                             aggfunc="sum", fill_value=0)
        prog = u.groupby("unit_id")[["reporting", "units", "ballots", "outstanding"]].first()
        st = pd.DataFrame({"a": g.get(pa, 0), "b": g.get(pb, 0), "ea": e.get(pa, 0), "eb": e.get(pb, 0)}).join(prog)
    else:
        st = pd.DataFrame(columns=cols)
    df = pri.set_index("unit_id").join(st).reset_index()
    df[["a", "b", "ea", "eb"]] = df[["a", "b", "ea", "eb"]].astype(float).fillna(0.0)
    df["n"] = df["a"] + df["b"]
    E0 = df["V"] * R.turnout_ratio
    complete = (df["n"] > 0) & (df["units"] > 0) & (df["reporting"] >= df["units"])
    E_done = pd.Series(np.nan, index=df.index)
    if R.use_outstanding:
        ok = complete & (df["ballots"] > 0) & df["outstanding"].notna()
        E_done[ok] = df["n"] + df["outstanding"] * df["n"] / df["ballots"]
    if R.f_complete > 0:
        E_done = E_done.fillna(df["n"].where(complete) / R.f_complete)
    done = E_done.notna()
    T = 1.0
    if R.learn_turnout and done.any():
        T = (E_done[done].sum() + R.turnout_prior_votes) / (E0[done].sum() + R.turnout_prior_votes)
    E = np.where(done, E_done, np.maximum(E0 * T, df["n"]))
    f = np.where(done, df["n"] / E, np.minimum(df["n"] / E, R.f_max))
    return pd.DataFrame({"unit_id": df["unit_id"], "n": df["n"], "a": df["a"], "ne": df["ea"] + df["eb"],
                         "ae": df["ea"], "f": np.clip(f, 0, 1), "E": E, "complete": done.to_numpy(),
                         "turnout_T": T})


def _model_units(cs: pd.DataFrame, pri: pd.DataFrame, st: str, R: CountRules) -> pd.DataFrame:
    """nowcast units. Default: one per county, region = state. vote_type: two per county — the early + mail
    batch (posted whole: f = 1 once any early vote is in) and the rest (Election Day, late mail, provisional) —
    with region = vote type, so the Election Day swing is learned from Election Day returns instead of being
    extrapolated from early batches (the main count-order bias in GA/TX, docs/us_midterms.md). Falls back to one
    unit per county while the feed reports votes but no early-vote split."""
    base = pd.DataFrame({"county": cs["unit_id"], "s0": pri["s0"].to_numpy()})
    if not R.vote_type or (cs["n"].sum() > 0 and cs["ne"].sum() == 0):
        return base.assign(uf=st, cd_mun=cs["unit_id"], n=cs["n"], a=cs["a"], f=cs["f"], V0=cs["E"])
    posted = cs["ne"] > 0
    Ee = np.where(posted, cs["ne"], R.early_share * cs["E"])
    El = np.maximum(cs["E"] - Ee, 0.02 * cs["E"])
    nl, al = cs["n"] - cs["ne"], cs["a"] - cs["ae"]
    fl = np.where(cs["complete"], np.minimum(nl / El, 1.0), np.minimum(nl / El, R.f_max))
    early = base.assign(uf=f"{st}-early", cd_mun=cs["unit_id"] + "|e", n=cs["ne"], a=cs["ae"],
                        f=posted.astype(float), V0=Ee)
    late = base.assign(uf=f"{st}-late", cd_mun=cs["unit_id"] + "|l", n=nl, a=al, f=fl, V0=El)
    return pd.concat([early, late], ignore_index=True)


def project(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Race-level nowcast (brunder.nowcast over all units of the state) + per-county projected final Dem share."""
    st, R, p = cfg["election"]["state"], rules(cfg), params(cfg)
    cs = count_state(rows, cfg, pri)
    mu = _model_units(cs, pri, st, R)
    res = nowcast.nowcast(mu[["uf", "cd_mun", "n", "a", "f"]], mu[["uf", "cd_mun", "s0", "V0"]], p)
    # the same remaining-share projection nowcast uses, per unit, for the map
    counted = mu["n"] > 0
    d = np.where(counted, mu["a"] / mu["n"].where(counted) - mu["s0"], np.nan)
    w = mu["n"].where(counted, 0.0)
    wd = pd.Series(np.nan_to_num(d) * w).groupby(mu["uf"]).sum()
    W = w.groupby(mu["uf"]).sum()
    delta_u = ((wd + p.kappa * res["swing_nat"]) / (W + p.kappa)).reindex(mu["uf"]).to_numpy()
    s_hat = np.clip(mu["s0"] + delta_u + p.rho * (np.nan_to_num(d) - delta_u) * counted, 0, 1)
    Rm = (1 - mu["f"]).clip(lower=0) * mu["V0"] * res["turnout_k"]
    agg = pd.DataFrame({"a": mu["a"], "n": mu["n"], "ra": Rm * s_hat, "r": Rm}).groupby(mu["county"].to_numpy()).sum()
    agg = agg.reindex(cs["unit_id"])
    proj = ((agg["a"] + agg["ra"]) / (agg["n"] + agg["r"]).where(agg["n"] + agg["r"] > 0)).to_numpy()
    res["raw"] = float(cs["a"].sum() / cs["n"].sum()) if cs["n"].sum() > 0 else np.nan
    res["turnout_T"] = float(cs["turnout_T"].iloc[0]) if len(cs) else 1.0
    units = cs.assign(proj=proj, s0=pri["s0"].to_numpy(), V=pri["V"].to_numpy())
    return res, units


# ------------------------------------------------------------------ dashboard export

def _iso(t: pd.Timestamp) -> str:
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, path)                            # atomic: the live site never reads a half-written file


def _pm_market(cfg: dict) -> tuple[str, str] | None:
    m = cfg.get("markets", {}).get("polymarket", {})
    return (f"{m['event']}/{m['market']}", m.get("yes_is", "a")) if m.get("market") else None


def kalshi_markets(cfgs: list[dict], pairs_path: Path | None = None) -> dict[str, dict]:
    """Per race the Kalshi market for its timeline line, from configs/pairs/us2026.toml (Kalshi workstream):
    a market whose YES is side a, preferring the one paired with the race's Polymarket market, first listed wins.
    {race_id: {ticker, yes_is, question (our paraphrase)}}; {} when the pairs file is absent."""
    from .arb import load_pairs
    path = pairs_path or ROOT_DIR / "configs" / "pairs" / f"{EID}.toml"
    if not path.exists():
        return {}
    _, pairs = load_pairs(path)
    out = {}
    for c in cfgs:
        rid, pm = c["election"]["race_id"], c.get("markets", {}).get("polymarket", {}).get("market")
        cand = [p for p in pairs if p.race_id == rid and p.kalshi_yes_is == "a" and p.kalshi_ticker]
        cand.sort(key=lambda p: p.pm_market != pm)                    # stable: listed order within each group
        if cand:
            p = cand[0]
            out[rid] = {"ticker": p.kalshi_ticker, "yes_is": "a",
                        "question": p.kalshi_resolution or f"{c['election']['name']}: {c['election']['a']['name']} "
                                                           "(Democrat) wins"}
    return out


def kalshi_prices(arc: Archive, f: Path | None) -> dict[str, float]:
    """P(YES) per ticker (mid if spread ≤ 10c, brunder.kalshi.prob_from_book) from one kalshi_books snapshot."""
    if f is None:
        return {}
    out = {}
    for ticker, book in json.loads(arc.read(f)).items():
        p = kalshi.prob_from_book(book)
        if p is not None and np.isfinite(p):
            out[ticker] = float(p)
    return out


def meta(cfgs: list[dict], priors_ok: dict[str, bool], kalshi_mk: dict[str, dict] | None = None) -> dict:
    races = []
    kalshi_mk = kalshi_mk or {}
    for c in cfgs:
        E = c["election"]
        mk = []
        pm = c.get("markets", {}).get("polymarket", {})
        if pm.get("market"):
            mk.append({"platform": "polymarket", "id": pm["market"], "question": pm.get("question", ""),
                       "yes_is": pm.get("yes_is", "a")})
        k = kalshi_mk.get(E["race_id"])
        if k:
            mk.append({"platform": "kalshi", "id": k["ticker"], "question": k["question"], "yes_is": k["yes_is"]})
        races.append({"race_id": E["race_id"], "name": E["name"], "region": E["state"],
                      "a": {"id": E["a"]["party"].lower(), "name": E["a"]["name"], "color": E["a"]["color"]},
                      "b": {"id": E["b"]["party"].lower(), "name": E["b"]["name"], "color": E["b"]["color"]},
                      "markets": mk, "units": f"units/{E['race_id']}.json" if priors_ok[E["race_id"]] else None,
                      "config": Path(c["_path"]).name})
    states = sorted({c["election"]["state"] for c in cfgs})
    srcs = [{"name": "Official results feeds: " + ", ".join(STATES[s] for s in states) + " (election night)",
             "url": "https://www.sos.ca.gov/elections", "licence": "public record"}]
    srcs += [{"name": SOURCE_INFO[s][0], "url": SOURCE_INFO[s][1], "licence": SOURCE_INFO[s][2]} for s in states]
    srcs.append({"name": "US Census Bureau county codes / cartographic boundaries",
                 "url": "https://www.census.gov/geographies/reference-files.html", "licence": "public domain"})
    if kalshi_mk:
        srcs.append({"name": "© Kalshi: market prices (order-book midpoints)", "url": "https://kalshi.com",
                     "licence": "© Kalshi; shown per the project's decision and pending exception request, "
                                "see docs/kalshi.md"})
    return {
        "id": EID, "name": "US midterm elections", "date": "2026-11-03", "tz": "America/New_York",
        "poll_close_utc": "2026-11-03T23:00:00Z", "geometry": "geo/us_counties.topo.json",
        "geometry_object": "counties", "unit_key": "unit_id",
        "regions": [{"id": s, "name": STATES[s]} for s in states], "races": races, "sources": srcs,
        "notes": [
            "counted = share of the expected two-party vote already counted (model estimate; US feeds report "
            "precincts, not remaining ballots).",
            "raw and share are the Democratic share of the Democrat + Republican vote.",
            "Alaska's races are not modelled: under top-four ranked-choice voting several candidates share a party, "
            "so party totals do not answer the candidate-specific markets.",
            "kalshi = Kalshi P(Democrat): midpoint of the market's order book when the spread is at most 10c, else "
            "empty; market per race from configs/pairs/us2026.toml. Prices © Kalshi.",
            "Model frozen before polls closed (configs/us2026_*_v1.toml); see docs/us_midterms.md.",
        ],
    }


class Exporter:
    """Builds timeline rows and unit frames for every configured race from the snapshot archive.
    Incremental: each call processes only minutes after each race's own cursor, so the live loop can call it every
    ~30 s. Failures are isolated: a feed whose latest snapshot does not parse falls back to its last parsed state;
    a race that still fails keeps its cursor (retried next pass, no duplicate rows) while the others publish."""

    def __init__(self, cfg_paths: list[Path], snap_root: Path | None = None, priors: pd.DataFrame | None = None,
                 step: str = "1min", frame_every: str = "5min", max_retries: int = 3,
                 pairs_path: Path | None = None):
        """max_retries: passes a failing minute is retried before it is skipped (live: the archive may still
        change; replay: use 1, the archive is final)."""
        self.max_retries = max_retries
        self._retries: dict[str, tuple[pd.Timestamp, int]] = {}
        self.cfgs = [load_config(Path(p)) for p in cfg_paths]
        self.arc = Archive(snap_root or SNAP / TAG)
        pr = priors if priors is not None else load_priors()
        self.pri = {c["election"]["race_id"]: race_prior(c, pr) for c in self.cfgs}
        self.step, self.frame_every = pd.Timedelta(step), pd.Timedelta(frame_every)
        rids = [c["election"]["race_id"] for c in self.cfgs]
        self.timeline: dict[str, list[dict]] = {r: [] for r in rids}
        self.frames: dict[str, list[dict]] = {r: [] for r in rids}
        self.cursor: dict[str, pd.Timestamp | None] = {r: None for r in rids}   # last minute published per race
        self.errors: dict[str, str] = {}                                         # latest error per race / feed
        self._memo: dict[str, tuple[tuple, tuple[dict, pd.DataFrame]]] = {}    # race -> (sig, result), latest only
        self._good: dict[str, tuple[tuple, pd.DataFrame]] = {}                 # feed -> last parsed (sig, rows)
        self._failed: dict[str, tuple] = {}                                    # feed -> sig that failed to parse
        self._pm: tuple[Path | None, dict[str, float]] = (None, {})
        self.kalshi_mk = kalshi_markets(self.cfgs, pairs_path)
        self._ks: tuple[Path | None, dict[str, float]] = (None, {})

    def _rows_for(self, kind: str, t: pd.Timestamp) -> tuple[pd.DataFrame, tuple]:
        files = feed_files(self.arc, kind, t)
        sig = tuple(x for f in files.values() for x in (f if isinstance(f, list) else [f]) if x is not None)
        good = self._good.get(kind)
        if good and good[0] == sig:
            return good[1], sig
        if self._failed.get(kind) != sig:
            try:
                rows = parse_feed(kind, feed_blobs(self.arc, files))
                self._good[kind] = (sig, rows)
                self._failed.pop(kind, None)
                self.errors.pop(f"feed:{kind}", None)
                return rows, sig
            except Exception as e:                   # valid document, unexpected content
                self._failed[kind] = sig
                self.errors[f"feed:{kind}"] = f"{_iso(t)} {type(e).__name__}: {e}"
                print(f"feed {kind}: parse failed at {_iso(t)}, using its last parsed state: "
                      f"{type(e).__name__}: {e}", flush=True)
        if good is None:                             # nothing parsable yet: same as no returns
            return _frame([]), ("unparsed",) + sig
        return good[1], good[0]

    def _tick(self, c: dict, t: pd.Timestamp, prices: dict[str, float], kprices: dict[str, float]) -> None:
        rid, pri = c["election"]["race_id"], self.pri[c["election"]["race_id"]]
        rows, sig = self._rows_for(c["election"]["feed"], t)
        memo = self._memo.get(rid)
        if memo is None or memo[0] != sig:
            memo = (sig, project(rows, c, pri))
            self._memo[rid] = memo
        res, units = memo[1]
        pm = _pm_market(c)
        p_yes = prices.get(pm[0]) if pm else None
        p_a = None if p_yes is None else (p_yes if pm[1] == "a" else 1 - p_yes)
        km = self.kalshi_mk.get(rid)
        k_yes = kprices.get(km["ticker"]) if km else None
        k_a = None if k_yes is None else (k_yes if km["yes_is"] == "a" else 1 - k_yes)
        row = {"t": _iso(t), "counted": _r(res["counted_frac"]), "raw": _r(res["raw"]),
               "share": _r(res["share_hat"]), "sd": _r(res["share_sd"]), "P": _r(res["p_win"]),
               "pm": _r(p_a), "kalshi": _r(k_a)}
        fr = self.frames[rid]
        frame = None
        if units["n"].sum() > 0 and (not fr or t - pd.Timestamp(fr[-1]["t"]) >= self.frame_every):
            frame = {"t": _iso(t),
                     "f": (units["f"] * 1000).round().astype(int).tolist(),
                     "a": np.where(units["n"] > 0, (units["a"] / units["n"].where(units["n"] > 0) * 1000)
                                   .fillna(-1), -1).round().astype(int).tolist(),
                     "proj": (units["proj"].fillna(units["s0"]) * 1000).round().astype(int).tolist()}
        self.timeline[rid].append(row)                # only after everything for this minute succeeded
        if frame:
            fr.append(frame)
            if len(fr) > 300:                        # bounded memory: thin older frames, keep the latest
                fr[:] = fr[::-1][::2][::-1]
        self.cursor[rid] = t

    def update(self, until: pd.Timestamp | None = None) -> None:
        self.arc.refresh()
        ts = self.arc.times()
        if not ts:
            return
        end = min(until, ts[-1] + self.step) if until is not None else ts[-1]
        active = [c for c in self.cfgs if self.pri[c["election"]["race_id"]] is not None
                  and not self.pri[c["election"]["race_id"]].empty]
        first = ts[0].ceil(self.step)
        starts = [first if self.cursor[c["election"]["race_id"]] is None
                  else self.cursor[c["election"]["race_id"]] + self.step for c in active]
        if not starts:
            return
        failed: set[str] = set()                     # races that failed this pass: retried next pass
        for t in pd.date_range(min(starts), end, freq=self.step):
            try:
                books = self.arc.at("polymarket_books", t)
                if books != self._pm[0]:
                    self._pm = (books, pm_prices(self.arc, books))
            except Exception as e:                   # market data must not block the model
                self.errors["polymarket"] = f"{_iso(t)} {type(e).__name__}: {e}"
            try:
                kb = self.arc.at("kalshi_books", t)
                if kb != self._ks[0]:
                    self._ks = (kb, kalshi_prices(self.arc, kb))
            except Exception as e:
                self.errors["kalshi"] = f"{_iso(t)} {type(e).__name__}: {e}"
            for c in active:
                rid = c["election"]["race_id"]
                cur = self.cursor[rid]
                if rid in failed or (cur is not None and t <= cur):
                    continue
                try:
                    self._tick(c, t, self._pm[1], self._ks[1])
                    self.errors.pop(rid, None)
                    self._retries.pop(rid, None)
                except Exception as e:
                    prev = self._retries.get(rid)
                    n = prev[1] + 1 if prev and prev[0] == t else 1
                    self._retries[rid] = (t, n)
                    self.errors[rid] = f"{_iso(t)} {type(e).__name__}: {e}"
                    if n >= self.max_retries:        # a minute that keeps failing is left out, not retried forever
                        self.cursor[rid] = t
                        self._retries.pop(rid)
                        print(f"race {rid}: {_iso(t)} failed {n} passes, minute skipped: {type(e).__name__}: {e}",
                              flush=True)
                    else:
                        failed.add(rid)
                        print(f"race {rid}: failed at {_iso(t)}, retry next pass: {type(e).__name__}: {e}",
                              flush=True)

    def tail(self, minutes: int = 60) -> dict:
        """timeline.json's shape restricted to each race's last `minutes` minutes (rows with t > its last row's
        t − minutes), plus "tail": true and "since" (ISO time of the earliest row included, null if none). The live
        page polls this small file; timeline.json stays complete for the first load."""
        out: dict = {}
        for rid, rows in self.timeline.items():
            if rows:
                cut = pd.Timestamp(rows[-1]["t"]) - pd.Timedelta(minutes=minutes)
                i = len(rows)
                while i and pd.Timestamp(rows[i - 1]["t"]) > cut:
                    i -= 1
                out[rid] = rows[i:]
            else:
                out[rid] = []
        firsts = [r[0]["t"] for r in out.values() if r]
        return {**out, "tail": True, "since": min(firsts) if firsts else None}

    def write(self, out: Path) -> Path:
        d = Path(out) / EID
        ok = {rid: p is not None and not p.empty for rid, p in self.pri.items()}
        _write_json(d / "meta.json", meta(self.cfgs, ok, self.kalshi_mk))
        _write_json(d / "timeline.json", self.timeline)
        _write_json(d / "timeline_tail.json", self.tail())
        default = None
        for c in self.cfgs:
            rid = c["election"]["race_id"]
            pri = self.pri[rid]
            if not ok[rid]:
                continue
            try:
                fr = self.frames[rid]
                if len(fr) > 60:                      # keep ~60 frames, always including the latest
                    idx = np.unique(np.linspace(0, len(fr) - 1, 60).round().astype(int))
                    fr = [fr[i] for i in idx]
                obj = {"race_id": rid, "unit_ids": pri["unit_id"].tolist(),
                       "region": [c["election"]["state"]] * len(pri), "names": pri["name"].tolist(),
                       "prior": (pri["s0"] * 1000).round().astype(int).tolist(),
                       "V0": (pri["V"] * rules(c).turnout_ratio).round().astype(int).tolist(), "frames": fr}
                _write_json(d / "units" / f"{rid}.json", obj)
                default = default or obj
            except Exception as e:                   # one race's map must not block the others
                self.errors[rid] = f"write {type(e).__name__}: {e}"
                print(f"race {rid}: units file not written: {type(e).__name__}: {e}", flush=True)
        if default:
            _write_json(d / "units.json", default)
        return d


def export(cfg_paths: list[Path], out: Path, snap_root: Path | None = None, until: str | None = None) -> Path:
    """Replay: everything recorded up to `until` (default: the end of the archive)."""
    ex = Exporter(cfg_paths, snap_root, max_retries=1)
    ex.update(pd.Timestamp(until).tz_convert("UTC") if until else None)
    return ex.write(out)


async def run_live(cfg_paths: list[Path], out: Path, snap_root: Path | None = None, interval: float = 30.0,
                   stop_at: datetime | None = None) -> None:
    """Live: every `interval` seconds, extend the timeline with what the recorder saved since the last pass and
    rewrite the contract files. Reads the recorder's archive only."""
    ex = Exporter(cfg_paths, snap_root)
    while stop_at is None or datetime.now(timezone.utc) < stop_at:
        t0 = datetime.now(timezone.utc)
        try:
            ex.update(pd.Timestamp(t0))
            ex.write(out)
            last = {rid: rows[-1] for rid, rows in ex.timeline.items() if rows}
            print(f"{t0:%H:%M:%S} " + "  ".join(f"{rid} c={r['counted']} P={r['P']} pm={r['pm']}"
                                               for rid, r in last.items()), flush=True)
            if ex.errors:
                print(f"{t0:%H:%M:%S} open errors: {ex.errors}", flush=True)
        except Exception as e:                       # never stop on the night
            print(f"{t0:%H:%M:%S} export error, continuing: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(max(0.0, interval - (datetime.now(timezone.utc) - t0).total_seconds()))


def cli(a) -> None:
    """`brunder us priors|export|live …` (see src/brunder/cli.py)."""
    match a.step:
        case "priors":
            print(build_priors(a.source or None))
        case "export":
            print(export(a.configs, a.out, a.snap, a.until))
        case "live":
            stop = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
            asyncio.run(run_live(a.configs, a.out, a.snap, a.interval, stop))


# ------------------------------------------------------------------ Census 2020 county codes (public domain)
# "<name>:<county FIPS>", Texas in Texas SoS county-number order; generated from
# https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt (tests check it against the file).

_COUNTIES = {
    "ca": (
        "Alameda:001,Alpine:003,Amador:005,Butte:007,Calaveras:009,Colusa:011,Contra Costa:013,Del Norte:015,"
        "El Dorado:017,Fresno:019,Glenn:021,Humboldt:023,Imperial:025,Inyo:027,Kern:029,Kings:031,Lake:033,"
        "Lassen:035,Los Angeles:037,Madera:039,Marin:041,Mariposa:043,Mendocino:045,Merced:047,Modoc:049,"
        "Mono:051,Monterey:053,Napa:055,Nevada:057,Orange:059,Placer:061,Plumas:063,Riverside:065,"
        "Sacramento:067,San Benito:069,San Bernardino:071,San Diego:073,San Francisco:075,San Joaquin:077,"
        "San Luis Obispo:079,San Mateo:081,Santa Barbara:083,Santa Clara:085,Santa Cruz:087,Shasta:089,"
        "Sierra:091,Siskiyou:093,Solano:095,Sonoma:097,Stanislaus:099,Sutter:101,Tehama:103,Trinity:105,"
        "Tulare:107,Tuolumne:109,Ventura:111,Yolo:113,Yuba:115"
    ),
    "tx": (
        "Anderson:001,Andrews:003,Angelina:005,Aransas:007,Archer:009,Armstrong:011,Atascosa:013,Austin:015,"
        "Bailey:017,Bandera:019,Bastrop:021,Baylor:023,Bee:025,Bell:027,Bexar:029,Blanco:031,Borden:033,"
        "Bosque:035,Bowie:037,Brazoria:039,Brazos:041,Brewster:043,Briscoe:045,Brooks:047,Brown:049,"
        "Burleson:051,Burnet:053,Caldwell:055,Calhoun:057,Callahan:059,Cameron:061,Camp:063,Carson:065,"
        "Cass:067,Castro:069,Chambers:071,Cherokee:073,Childress:075,Clay:077,Cochran:079,Coke:081,"
        "Coleman:083,Collin:085,Collingsworth:087,Colorado:089,Comal:091,Comanche:093,Concho:095,Cooke:097,"
        "Coryell:099,Cottle:101,Crane:103,Crockett:105,Crosby:107,Culberson:109,Dallam:111,Dallas:113,"
        "Dawson:115,Deaf Smith:117,Delta:119,Denton:121,DeWitt:123,Dickens:125,Dimmit:127,Donley:129,"
        "Duval:131,Eastland:133,Ector:135,Edwards:137,Ellis:139,El Paso:141,Erath:143,Falls:145,Fannin:147,"
        "Fayette:149,Fisher:151,Floyd:153,Foard:155,Fort Bend:157,Franklin:159,Freestone:161,Frio:163,"
        "Gaines:165,Galveston:167,Garza:169,Gillespie:171,Glasscock:173,Goliad:175,Gonzales:177,Gray:179,"
        "Grayson:181,Gregg:183,Grimes:185,Guadalupe:187,Hale:189,Hall:191,Hamilton:193,Hansford:195,"
        "Hardeman:197,Hardin:199,Harris:201,Harrison:203,Hartley:205,Haskell:207,Hays:209,Hemphill:211,"
        "Henderson:213,Hidalgo:215,Hill:217,Hockley:219,Hood:221,Hopkins:223,Houston:225,Howard:227,"
        "Hudspeth:229,Hunt:231,Hutchinson:233,Irion:235,Jack:237,Jackson:239,Jasper:241,Jeff Davis:243,"
        "Jefferson:245,Jim Hogg:247,Jim Wells:249,Johnson:251,Jones:253,Karnes:255,Kaufman:257,Kendall:259,"
        "Kenedy:261,Kent:263,Kerr:265,Kimble:267,King:269,Kinney:271,Kleberg:273,Knox:275,Lamar:277,Lamb:279,"
        "Lampasas:281,La Salle:283,Lavaca:285,Lee:287,Leon:289,Liberty:291,Limestone:293,Lipscomb:295,"
        "Live Oak:297,Llano:299,Loving:301,Lubbock:303,Lynn:305,Madison:313,Marion:315,Martin:317,Mason:319,"
        "Matagorda:321,Maverick:323,McCulloch:307,McLennan:309,McMullen:311,Medina:325,Menard:327,"
        "Midland:329,Milam:331,Mills:333,Mitchell:335,Montague:337,Montgomery:339,Moore:341,Morris:343,"
        "Motley:345,Nacogdoches:347,Navarro:349,Newton:351,Nolan:353,Nueces:355,Ochiltree:357,Oldham:359,"
        "Orange:361,Palo Pinto:363,Panola:365,Parker:367,Parmer:369,Pecos:371,Polk:373,Potter:375,"
        "Presidio:377,Rains:379,Randall:381,Reagan:383,Real:385,Red River:387,Reeves:389,Refugio:391,"
        "Roberts:393,Robertson:395,Rockwall:397,Runnels:399,Rusk:401,Sabine:403,San Augustine:405,"
        "San Jacinto:407,San Patricio:409,San Saba:411,Schleicher:413,Scurry:415,Shackelford:417,Shelby:419,"
        "Sherman:421,Smith:423,Somervell:425,Starr:427,Stephens:429,Sterling:431,Stonewall:433,Sutton:435,"
        "Swisher:437,Tarrant:439,Taylor:441,Terrell:443,Terry:445,Throckmorton:447,Titus:449,Tom Green:451,"
        "Travis:453,Trinity:455,Tyler:457,Upshur:459,Upton:461,Uvalde:463,Val Verde:465,Van Zandt:467,"
        "Victoria:469,Walker:471,Waller:473,Ward:475,Washington:477,Webb:479,Wharton:481,Wheeler:483,"
        "Wichita:485,Wilbarger:487,Willacy:489,Williamson:491,Wilson:493,Winkler:495,Wise:497,Wood:499,"
        "Yoakum:501,Young:503,Zapata:505,Zavala:507"
    ),
    "oh": (
        "Adams:001,Allen:003,Ashland:005,Ashtabula:007,Athens:009,Auglaize:011,Belmont:013,Brown:015,"
        "Butler:017,Carroll:019,Champaign:021,Clark:023,Clermont:025,Clinton:027,Columbiana:029,"
        "Coshocton:031,Crawford:033,Cuyahoga:035,Darke:037,Defiance:039,Delaware:041,Erie:043,Fairfield:045,"
        "Fayette:047,Franklin:049,Fulton:051,Gallia:053,Geauga:055,Greene:057,Guernsey:059,Hamilton:061,"
        "Hancock:063,Hardin:065,Harrison:067,Henry:069,Highland:071,Hocking:073,Holmes:075,Huron:077,"
        "Jackson:079,Jefferson:081,Knox:083,Lake:085,Lawrence:087,Licking:089,Logan:091,Lorain:093,Lucas:095,"
        "Madison:097,Mahoning:099,Marion:101,Medina:103,Meigs:105,Mercer:107,Miami:109,Monroe:111,"
        "Montgomery:113,Morgan:115,Morrow:117,Muskingum:119,Noble:121,Ottawa:123,Paulding:125,Perry:127,"
        "Pickaway:129,Pike:131,Portage:133,Preble:135,Putnam:137,Richland:139,Ross:141,Sandusky:143,"
        "Scioto:145,Seneca:147,Shelby:149,Stark:151,Summit:153,Trumbull:155,Tuscarawas:157,Union:159,"
        "Van Wert:161,Vinton:163,Warren:165,Washington:167,Wayne:169,Williams:171,Wood:173,Wyandot:175"
    ),
    "ga": (
        "Appling:001,Atkinson:003,Bacon:005,Baker:007,Baldwin:009,Banks:011,Barrow:013,Bartow:015,"
        "Ben Hill:017,Berrien:019,Bibb:021,Bleckley:023,Brantley:025,Brooks:027,Bryan:029,Bulloch:031,"
        "Burke:033,Butts:035,Calhoun:037,Camden:039,Candler:043,Carroll:045,Catoosa:047,Charlton:049,"
        "Chatham:051,Chattahoochee:053,Chattooga:055,Cherokee:057,Clarke:059,Clay:061,Clayton:063,Clinch:065,"
        "Cobb:067,Coffee:069,Colquitt:071,Columbia:073,Cook:075,Coweta:077,Crawford:079,Crisp:081,Dade:083,"
        "Dawson:085,Decatur:087,DeKalb:089,Dodge:091,Dooly:093,Dougherty:095,Douglas:097,Early:099,"
        "Echols:101,Effingham:103,Elbert:105,Emanuel:107,Evans:109,Fannin:111,Fayette:113,Floyd:115,"
        "Forsyth:117,Franklin:119,Fulton:121,Gilmer:123,Glascock:125,Glynn:127,Gordon:129,Grady:131,"
        "Greene:133,Gwinnett:135,Habersham:137,Hall:139,Hancock:141,Haralson:143,Harris:145,Hart:147,"
        "Heard:149,Henry:151,Houston:153,Irwin:155,Jackson:157,Jasper:159,Jeff Davis:161,Jefferson:163,"
        "Jenkins:165,Johnson:167,Jones:169,Lamar:171,Lanier:173,Laurens:175,Lee:177,Liberty:179,Lincoln:181,"
        "Long:183,Lowndes:185,Lumpkin:187,McDuffie:189,McIntosh:191,Macon:193,Madison:195,Marion:197,"
        "Meriwether:199,Miller:201,Mitchell:205,Monroe:207,Montgomery:209,Morgan:211,Murray:213,Muscogee:215,"
        "Newton:217,Oconee:219,Oglethorpe:221,Paulding:223,Peach:225,Pickens:227,Pierce:229,Pike:231,"
        "Polk:233,Pulaski:235,Putnam:237,Quitman:239,Rabun:241,Randolph:243,Richmond:245,Rockdale:247,"
        "Schley:249,Screven:251,Seminole:253,Spalding:255,Stephens:257,Stewart:259,Sumter:261,Talbot:263,"
        "Taliaferro:265,Tattnall:267,Taylor:269,Telfair:271,Terrell:273,Thomas:275,Tift:277,Toombs:279,"
        "Towns:281,Treutlen:283,Troup:285,Turner:287,Twiggs:289,Union:291,Upson:293,Walker:295,Walton:297,"
        "Ware:299,Warren:301,Washington:303,Wayne:305,Webster:307,Wheeler:309,White:311,Whitfield:313,"
        "Wilcox:315,Wilkes:317,Wilkinson:319,Worth:321"
    ),
}
