"""Indiana U.S. House districts from the Indiana Election Division's ENR site (secondary source, added 2026-11-02,
unregistered).

https://enr.indianavoters.in.gov/site/ is an Angular page over static JSON (Azure blob behind Cloudflare):
  site/data/settings.json                  {Root: {VersionType "A"|"B", CurrentElection "11/03/2026", Certified, ...}};
                                           the results files carry the version suffix, which flips on each upload
  site/data/OffCatC_1005_<V>.json          office category 1005 = US Representative: Root.OfficeCategory.Regions.Region
                                           [one per district] .Races.Race[one per county part]: Jurisdiction {FIPS,
                                           JURISDICTION_NAME}, SubSortOrder "United States Representative, (5)
                                           District", Candidates.Candidate[NAME_ON_BALLOT, PARTY_ABBREV, TOTAL_VOTES]
The 2024 general is archived with the same layout under archive/2024General/data/. The file has no precinct
counts, so reporting/units stay NaN (as the PA feed: the model learns turnout from the votes).
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import pandas as pd

from . import us

NAME = "in"
BASE = "https://enr.indianavoters.in.gov/site/data"
STATES = {"in": "Indiana"}
POLL_CLOSE = {"in": "2026-11-03T23:00:00Z"}            # 6 p.m. local; the north-west and south-west (central) 00:00Z
SOURCE_INFO = {"in": ("Indiana Election Division, Election Night Reporting", "https://enr.indianavoters.in.gov/",
                      "public record")}
_VER: dict[str, tuple[float, str]] = {}
VER_TTL = 20.0


def _resolve(get) -> str | None:
    try:
        now = time.monotonic()
        t, v = _VER.get(BASE, (-1e9, ""))
        if now - t > VER_TTL:
            raw = get(f"{BASE}/settings.json")
            v = str(json.loads(raw.decode("utf-8-sig"))["Root"]["VersionType"]) if raw else ""
            if not re.fullmatch(r"[A-Za-z0-9]{1,3}", v):
                return None
            _VER[BASE] = (now, v)
        return f"{BASE}/OffCatC_1005_{v}.json"
    except Exception:
        return None


RECORD = [{"key": "in_house", "url": f"{BASE}/settings.json", "interval": 120, "ext": "json",
           "host_class": "server-unverified", "resolve": _resolve}]
FEEDS = {"in_house": {"offcat": "in_house"}}
_DIST = re.compile(r"\((\d+)\)\s*District", re.I)


def _list(x):
    return [] if x is None else (x if isinstance(x, list) else [x])


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    blob = blobs.get("offcat")
    if not blob:
        return us._frame([])
    try:
        root = json.loads(blob.decode("utf-8-sig") if isinstance(blob, bytes) else blob)["Root"]
        regions = _list(root["OfficeCategory"]["Regions"]["Region"])
    except (ValueError, KeyError, TypeError):
        return us._frame([])
    ids = {u[-5:]: (u, n) for u, n in zip(us.counties("in")["unit_id"], us.counties("in")["name"])}
    rows = []
    for reg in regions:
        for race in _list((reg.get("Races") or {}).get("Race")):
            m = _DIST.search(str(race.get("SubSortOrder") or ""))
            fips = str((race.get("Jurisdiction") or {}).get("FIPS") or "")
            if not m or fips not in ids:
                continue
            uid, name = ids[fips]
            for c in _list((race.get("Candidates") or {}).get("Candidate")):
                rows.append({"level": "county", "unit_id": uid, "name": name, "race": f"house-{int(m.group(1)):02d}",
                             "candidate": str(c.get("NAME_ON_BALLOT") or "").strip(),
                             "party": us.party_code({"D": "DEM", "R": "REP", "L": "LIB"}.get(
                                 str(c.get("PARTY_ABBREV") or ""), str(c.get("PARTY_ABBREV") or ""))),
                             "votes": float(c.get("TOTAL_VOTES") or 0), "early": np.nan, "reporting": np.nan,
                             "units": np.nan, "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    return us._frame(rows)
