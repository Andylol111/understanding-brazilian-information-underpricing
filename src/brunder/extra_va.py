"""Virginia U.S. House (VA-01, VA-05) from the Virginia Department of Elections night site (Enhanced Voting, served from
enr.elections.virginia.gov). SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED (docs/us_coverage_2026_ne.md).

One JSON document per House contest: .../2026-November-General/data/ballot-item/<item id>, answered with
ballotItemWithBreakdown {name, reportingStatus, summaryResults, breakdownResults[{locality{name, mapFeatureId},
precinctReportingStatus{reportingUnits,totalUnits}, ballotOptions[{name, party.abbreviation, voteCount,
groupResults[{groupName, voteCount}]}]}]}. A split locality lists only its part of the district. Item ids are fixed in
the election document (…/2026-November-General/data, ballotItems), read 2026-11-02. Past-cycle check: the same API
for 2024NovemberGeneral (1st and 5th District items; tests/fixtures/us/extra_va_2024_cd01/cd05.json.gz).

early = Early Voting + Mailed Absentee (Election Day, Provisional, Post-Election are the late part). The configs do not use
the vote-type split (vote_type = false), like the feeds replayed without one.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from brunder import us

NAME = "va"
_BASE = "https://enr.elections.virginia.gov/results/public/api/elections/virginia/2026-November-General/data/ballot-item"
_ITEMS = {1: "01000000-f030-c43d-abd6-08df1fee5328", 5: "01000000-f030-c43d-608c-08df1fee52fa"}
RECORD = [dict(key=f"va_house_d{n:02d}", url=f"{_BASE}/{i}", interval=60, ext="json") for n, i in _ITEMS.items()]
FEEDS = {"va_house": {f"d{n:02d}": f"va_house_d{n:02d}" for n in _ITEMS}}
STATES = {"va": "Virginia"}
POLL_CLOSE = {"va": "2026-11-04T00:00:00Z"}

_DISTRICT = re.compile(r"House of Representatives \((\d+)(?:st|nd|rd|th) District\)", re.I)
_PARTY = {"D": "DEM", "R": "REP", "L": "LIB", "G": "GRN", "I": "IND"}      # Enhanced Voting party.abbreviation
_EARLY = re.compile(r"^(Early Voting|Mailed Absentee)$", re.I)


def _en(names) -> str:
    for n in names or []:
        if n.get("languageId") == "en":
            return n["text"].strip()
    return (names[0]["text"].strip() if names else "")


def _int(x) -> float:
    return np.nan if x is None else float(x)


def _party(abbr: str | None) -> str:
    a = (abbr or "").strip()
    return _PARTY.get(a.upper()) or us.party_code(a)


def parse_item(blob: bytes) -> pd.DataFrame:
    """One House ballot item -> county rows (race "house-NN"). Empty frame for a file without a House contest, an
    unparsable body or a contest with no vote yet."""
    try:
        b = json.loads(blob)["ballotItemWithBreakdown"]
        m = _DISTRICT.search(_en(b["name"]))
    except (ValueError, KeyError, TypeError):
        return us._frame([])
    if not m:
        return us._frame([])
    race, rows = f"house-{int(m.group(1)):02d}", []
    for br in b.get("breakdownResults") or []:
        fips = (br["locality"].get("mapFeatureId") or "").strip()
        if not fips:
            continue
        st = br.get("precinctReportingStatus") or {}
        uid, name = f"us-va-51{fips}", _en(br["locality"]["name"]).title().replace(" And ", " and ")
        for o in br.get("ballotOptions") or []:
            early = sum(g.get("voteCount") or 0 for g in o.get("groupResults") or []
                        if _EARLY.match(_en(g["groupName"])))
            rows.append({"level": "county", "unit_id": uid, "name": name, "race": race,
                         "candidate": re.sub(r"\s*\((?:I\)\s*\()?[A-Za-z]+\)\s*$", "", _en(o["name"])).strip(),
                         "party": _party((o.get("party") or {}).get("abbreviation")),
                         "votes": float(o.get("voteCount") or 0), "early": float(early),
                         "reporting": _int(st.get("reportingUnits")), "units": _int(st.get("totalUnits")),
                         "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    df = us._frame(rows)
    return df if len(df) and df["votes"].sum() > 0 else us._frame([])


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    parts = []
    for blob in blobs.values():
        for b in (blob if isinstance(blob, list) else [blob]):
            if b:
                parts.append(parse_item(b))
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else us._frame([])
