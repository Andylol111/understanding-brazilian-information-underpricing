"""District-level adapter for the already-recorded civicAPI national U.S. House race.

No network code lives here.  It reads ``civic_87524`` snapshots saved by the existing civic recorder
and turns each district into one model unit.  Secondary source; added and unregistered 2026-11-02.
"""
from __future__ import annotations

import re

import numpy as np

NAME = "civicAPI national U.S. House race (recorded snapshots only)"
RECORD: list[dict] = []
FEEDS = {"civic_house": {"house": "civic_87524"}}
STATES: dict[str, str] = {}
POLL_CLOSE: dict[str, str] = {}
SOURCE_URL = "https://civicapi.org"
_RX = re.compile(r"^([a-z]{2})-(\d{2}|al)$", re.I)


def parse(kind: str, blobs: dict):
    """``civic_87524`` -> one brunder.us row per district x candidate.

    civicAPI gives district totals and a district percent-reporting value, not county parts.  The
    district is therefore one model unit (``reporting`` out of 100).  Ranked-choice AK-AL is parsed
    for archive completeness but intentionally has no model config.
    """
    from . import civic, us

    if kind != "civic_house":
        raise ValueError(kind)
    blob = blobs.get("house")
    if not blob:
        return us._frame([])
    src = civic.parse_house(blob)
    rows = []
    for x in src.to_dict("records"):
        key = str(x.get("district") or "").lower()
        m = _RX.match(key)
        if not m:
            continue
        st, nn = m.groups()
        race = "house-AL" if nn.lower() == "al" else f"house-{int(nn):02d}"
        uid = f"us-{st}-cd{nn.lower()}"
        rows.append({"level": "county", "unit_id": uid, "name": x.get("name") or key.upper(), "race": race,
                     "candidate": x.get("candidate"), "party": us.party_code(x.get("party")),
                     "votes": float(x.get("votes") or 0), "early": np.nan,
                     "reporting": float(x.get("percent_reporting") or 0), "units": 100.0,
                     "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    return us._frame(rows)
