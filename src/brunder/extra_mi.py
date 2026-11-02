"""Michigan's 10th district (MI-10) from the Macomb County Clerk's unofficial summary PDF (secondary source, added
2026-11-02, unregistered). Michigan has no reachable statewide night feed (mielections.us timed out; MI counties in
MI-04/07/08 report on Enhanced Voting, a host we never request). Macomb = 88 % of MI-10's 2024 presidential vote; the
Oakland part (12 %) has no source and stays uncounted.

  https://macombgovdocs.org/clerk/pdfs/elect-2026-11-03-unofficial-summary-results.pdf (posted 2026-10-27 as a
  blank template; 2024: elect-2024-11-05-unofficial-summary-results.pdf). A multi-column one-page-per-section report;
  pypdf's layout extraction keeps the columns, and the U.S. House block is the left column: "REPRESENTATIVE IN
  CONGRESS", "10th District (Multi-County)", then "Name   PARTY   votes" lines until the next header. The report
  carries no precinct counts (only "NUMBER VOTED"), so reporting/units stay NaN and the model learns turnout from
  the votes.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

from . import us

NAME = "mi"
MACOMB = "https://macombgovdocs.org/clerk/pdfs/elect-2026-11-03-unofficial-summary-results.pdf"
RECORD = [{"key": "mi_macomb", "url": MACOMB, "interval": 120, "ext": "pdf", "host_class": "server-unverified"}]
FEEDS = {"mi_house": {"macomb": "mi_macomb"}}
STATES = {"mi": "Michigan"}
POLL_CLOSE = {"mi": "2026-11-04T01:00:00Z"}
SOURCE_INFO = {"mi": ("Macomb County Clerk, unofficial election results", "https://www.macombgov.org/departments/"
                      "clerk-register-deeds/elections", "public record")}
UNIT = ("us-mi-26099", "Macomb")
_PARTIES = "DEM|REP|LIB|UST|GRN|WC|NLP|NPA|IND"
_CAND = re.compile(rf"^\s{{0,3}}(\S.*?)\s{{2,}}({_PARTIES})\b(?:\s{{1,24}}([\d,]+)(?=\s|$))?")
_DIST = re.compile(r"^\s{0,30}(\d+)(?:st|nd|rd|th) District\b")
_HEAD = re.compile(r"^\s*([A-Z][A-Z .()/&'-]{5,}?)\s*$")        # an upper-case contest header in the left column


def parse_text(pages: list[str]) -> list[dict]:
    rows = []
    for text in pages:
        inside, race = False, None
        for line in text.splitlines():
            left = re.split(r"\s{4,}(?=\S)", line.strip(), maxsplit=1)[0] if line.strip() else ""
            lead = len(line) - len(line.lstrip())
            if left == "REPRESENTATIVE IN CONGRESS" and lead <= 30:
                inside, race = True, None
                continue
            if not inside or not left:
                continue
            d = _DIST.match(line)
            if d:
                race = f"house-{int(d.group(1)):02d}"
                continue
            c = _CAND.match(line)
            if c and race:
                rows.append({"level": "county", "unit_id": UNIT[0], "name": UNIT[1], "race": race,
                             "candidate": " ".join(c.group(1).split()), "party": us.party_code(c.group(2)),
                             "votes": float((c.group(3) or "0").replace(",", "")), "early": np.nan,
                             "reporting": np.nan, "units": np.nan, "ballots": np.nan, "registered": np.nan,
                             "outstanding": np.nan})
            elif lead <= 30 and _HEAD.match(left):
                inside = False                                   # the next contest of the left column
    return rows


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    pdf = blobs.get("macomb")
    if not pdf:
        return us._frame([])
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf))
        return us._frame(parse_text([p.extract_text(extraction_mode="layout") or "" for p in reader.pages]))
    except Exception:
        return us._frame([])
