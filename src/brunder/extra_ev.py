"""Additional Enhanced Voting election-night feeds.

The public ballot-item endpoint returns statewide totals plus a breakdown by county.  Washington's
2026 House items are recorded individually so the large all-contest election document is not polled.
This is a secondary, unregistered source added 2026-11-02.
"""
from __future__ import annotations

import json
import re

import numpy as np

NAME = "Washington VoteWA Enhanced Voting"
BASE = "https://results.votewa.gov/results/public/api/elections/washington/20261103/data/ballot-item"
RECORD = [
    {"key": "wa_house_03", "url": f"{BASE}/54c40000-9064-bbec-4779-08df15c0a82a", "interval": 60, "ext": "json"},
    {"key": "wa_house_04", "url": f"{BASE}/54c40000-9064-bbec-5bf3-08df15c0a82a", "interval": 60, "ext": "json"},
    {"key": "wa_house_05", "url": f"{BASE}/54c40000-9064-bbec-6f41-08df15c0a82a", "interval": 60, "ext": "json"},
]
FEEDS = {"wa_house": {"items": "wa_house_*"}}
STATES = {"wa": "Washington"}
POLL_CLOSE = {"wa": "2026-11-04T04:00:00Z"}
SOURCE_URL = "https://results.votewa.gov/results/public/washington"
_RX_HOUSE = re.compile(r"(?:Congressional District\s*(\d+).*U\.?S\.? Representative|U\.?S\.? Representative.*Congressional District\s*(\d+))", re.I)


def _en(names) -> str:
    for x in names or []:
        if x.get("languageId") == "en":
            return str(x.get("text") or "").strip()
    return str((names or [{}])[0].get("text") or "").strip()


def parse_item(blob: bytes | dict):
    """One ballot item -> brunder.us rows, aggregated to one district unit.

    A district is deliberately one model unit: the committed House-prior file did not contain
    Washington county pieces when this source was added.  The feed's county breakdown is still used
    to sum precinct progress, while candidate votes use the ballot item's exact district totals.
    """
    from . import us

    d = blob if isinstance(blob, dict) else json.loads(blob)
    b = d.get("ballotItemWithBreakdown") or {}
    title = _en(b.get("name"))
    m = _RX_HOUSE.search(title)
    if not m:
        return us._frame([])
    n = int(m.group(1) or m.group(2))
    reporting = units = 0.0
    have_progress = False
    for x in b.get("breakdownResults") or []:
        p = x.get("precinctReportingStatus") or {}
        if p.get("totalUnits") is not None:
            reporting += float(p.get("reportingUnits") or 0)
            units += float(p.get("totalUnits") or 0)
            have_progress = True
    if not have_progress:
        reporting = units = np.nan
    rows = []
    for o in (b.get("summaryResults") or {}).get("ballotOptions") or []:
        party = us.party_code((o.get("party") or {}).get("abbreviation"))
        rows.append({"level": "county", "unit_id": f"us-wa-cd{n:02d}",
                     "name": f"Washington Congressional District {n}", "race": f"house-{n:02d}",
                     "candidate": _en(o.get("name")), "party": party, "votes": float(o.get("voteCount") or 0),
                     "early": np.nan, "reporting": reporting, "units": units, "ballots": np.nan,
                     "registered": np.nan, "outstanding": np.nan})
    return us._frame(rows)


def parse(kind: str, blobs: dict):
    from . import us
    import pandas as pd

    if kind != "wa_house":
        raise ValueError(kind)
    parts = [parse_item(x) for x in blobs.get("items") or []]
    parts = [x for x in parts if not x.empty]
    return pd.concat(parts, ignore_index=True) if parts else us._frame([])
