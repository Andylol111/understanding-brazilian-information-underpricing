"""Michigan's 10th district (MI-10) from the Macomb County Clerk's unofficial summary PDF (secondary source, added
2026-11-02, unregistered). Michigan has no reachable statewide night feed (mielections.us timed out; MI counties in
MI-04/07/08 report on Enhanced Voting, a host we never request). Macomb = 88 % of MI-10's 2024 presidential vote; the
Oakland part (12 %) has no source and stays uncounted.

Also MI-04's Ottawa County part (26 % of MI-04's 2024 presidential vote; Kalamazoo is on Enhanced Voting, never
requested; Allegan, Calhoun, Van Buren and Berrien have no machine-readable night source found): Ottawa's Clarity
county site https://results.enr.clarityelections.com/MI/Ottawa (2026 general EID 127391, 2024 general 123542),
json/en/summary.json = county totals per contest, "Representative in Congress 4th District", TP/PR = precincts
total/reporting of the contest. The version is resolved per poll as in brunder.extra_clarity.

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

from . import extra_clarity as _clarity
from . import us

NAME = "mi"
MACOMB = "https://macombgovdocs.org/clerk/pdfs/elect-2026-11-03-unofficial-summary-results.pdf"
OTTAWA = "https://results.enr.clarityelections.com/MI/Ottawa"
RECORD = [{"key": "mi_macomb", "url": MACOMB, "interval": 120, "ext": "pdf", "host_class": "server-unverified"},
          {"key": "mi_ottawa_summary", "url": OTTAWA + "/elections.json", "interval": 60, "ext": "json",
           "host_class": "server-blocked",            # Clarity CloudFront WAF: 202 challenge to the VM
           "resolve": lambda get: _clarity._resolve(OTTAWA, "json/en/summary.json", get)}]
FEEDS = {"mi_house": {"macomb": "mi_macomb", "ottawa": "mi_ottawa_summary"}}
STATES = {"mi": "Michigan"}
POLL_CLOSE = {"mi": "2026-11-04T01:00:00Z"}
SOURCE_INFO = {"mi": ("Macomb County Clerk, unofficial election results", "https://www.macombgov.org/departments/"
                      "clerk-register-deeds/elections", "public record")}
UNIT = ("us-mi-26099", "Macomb")
OTTAWA_UNIT = ("us-mi-26139", "Ottawa")
LIVE_UNITS = {"mi_house": {UNIT[0], OTTAWA_UNIT[0]}}      # partial coverage (brunder.us_extra_live coverage)
_CLARITY_RACE = re.compile(r"^\s*Representative in Congress\s+(\d+)(?:st|nd|rd|th)\s+District\s*$", re.I)
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


def parse_ottawa(summary: bytes) -> list[dict]:
    rows = []
    for c in _clarity._loads(summary):
        m = _CLARITY_RACE.match(str(c.get("C") or ""))
        if not m:
            continue
        ch = c.get("CH") or []
        for name, party, v in zip(ch, c.get("P") or [""] * len(ch), c.get("V") or [0] * len(ch)):
            rows.append({"level": "county", "unit_id": OTTAWA_UNIT[0], "name": OTTAWA_UNIT[1],
                         "race": f"house-{int(m.group(1)):02d}", "candidate": name, "party": us.party_code(party),
                         "votes": float(v), "early": np.nan, "reporting": float(c.get("PR") or 0),
                         "units": float(c.get("TP") or 0), "ballots": np.nan, "registered": np.nan,
                         "outstanding": np.nan})
    return rows


def parse_macomb(pdf: bytes) -> list[dict]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return parse_text([p.extract_text(extraction_mode="layout") or "" for p in reader.pages])


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    rows = []
    for role, fn in (("macomb", parse_macomb), ("ottawa", parse_ottawa)):
        b = blobs.get(role)
        if not b:
            continue
        try:
            rows += fn(b)
        except Exception:                                  # half-written / not-yet-posted file
            continue
    return us._frame(rows)
