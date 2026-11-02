"""Montana U.S. House district results web service (secondary source, added 2026-11-02, unregistered).

The public electionresults.mt.gov page calls https://esersws.mt.gov/ResultsAjax.svc/GetMapData. The 2026 general's
pre-count test/result page lists race 450000000004067, OfficeSeqNo 2001, for the 1st Congressional District. Its
GetMapData document is one row per county x candidate with county precincts reporting/total. The 2024 archive (race
450000000001603, election 450002785) has the same candidates and certified totals but, after completion, repeats the
district-wide reporting count on every county; votes remain county-level.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from . import us

NAME = "mt"
BASE = "https://esersws.mt.gov/ResultsAjax.svc"
MT01 = (f"{BASE}/GetMapData?type=FED&category=CTY&raceID=450000000004067&osn=2001&county=0&party=0")
RECORD = [{"key": "mt_house_01", "url": MT01, "interval": 60, "ext": "json",
           "host_class": "server-unverified"}]
FEEDS = {"mt_house": {"d01": "mt_house_01"}}
STATES = {"mt": "Montana"}
POLL_CLOSE = {"mt": "2026-11-04T03:00:00Z"}
SOURCE_INFO = {"mt": ("Montana Secretary of State election results web service", MT01, "public record")}


def _empty() -> pd.DataFrame:
    return us._frame([])


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    rows = []
    ids = us._extra._county_ids("mt")
    for blob in blobs.values():
        if not blob:
            continue
        try:
            doc = json.loads(blob)
            data = (doc.get("d") or []) if isinstance(doc, dict) else doc
        except (ValueError, TypeError):
            continue
        for x in data:
            race_name = str(x.get("RaceName") or "")
            m = re.search(r"REPRESENTATIVE\s+(\d+)(?:ST|ND|RD|TH)?\s+CONGRESSIONAL", race_name, re.I)
            key = us._extra._key(str(x.get("CountyName") or ""))
            if not m or key not in ids:
                continue
            uid, name = ids[key]
            try:
                votes = us._extra._votes(x.get("calcCandidateVotes"))
                reporting = float(x.get("PrecinctsReporting") or 0)
                units = float(x.get("TotalPrecincts") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({"level": "county", "unit_id": uid, "name": name,
                         "race": f"house-{int(m.group(1)):02d}",
                         "candidate": " ".join(str(x.get("calcCandidate") or "").split()),
                         "party": us.party_code(x.get("PartyCode")), "votes": votes, "early": np.nan,
                         "reporting": reporting, "units": units, "ballots": np.nan, "registered": np.nan,
                         "outstanding": np.nan})
    return us._frame(rows)
