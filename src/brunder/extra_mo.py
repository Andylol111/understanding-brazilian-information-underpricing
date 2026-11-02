"""Missouri's 2nd district (MO-02) from two county election authorities (secondary source, added 2026-11-02,
unregistered). Together 84 % of the district's 2024 presidential vote; Franklin (13 %) and Warren (3 %) counties have
no machine-readable night source and stay uncounted (the model projects them from the prior and the observed swing).

  St. Louis County Board of Elections (70 %): the "Cumulative Results Report" PDF
      https://extcontent.stlouisco.com/BOE/eResults/el<yymmdd>/Results.pdf (el241105 = 2024 general, el260804 = 2026
      primary; el261103 appears when results are posted). Page 1 header "Polling Places Reporting N of M"; each contest
      "U.S. REPRESENTATIVE - DISTRICT 2 - ..." then per choice: NAME, PARTY (absent for write-ins), votes, %, votes, %.
      The county's part of the district is the whole contest (only MO-02 voters vote in it).
  St. Charles County Election Authority (14 %): Live Voter Turnout ENR page
      https://www.livevoterturnout.com/ENR/stcharlesmoenr/33/en/Index_33.html (33 = 2026 general; 29 = 2024 general),
      one static HTML page with every contest: aria-label "Contest: US REPRESENTATIVE DISTRICT 2", "Precincts
      Reported: X of Y", rows "NAME, PARTY" (sr-only label) with data-votes.

Rows: level "county", unit_id us-mo-<FIPS>, race "house-NN", reporting/units = polling places (St. Louis County, whole
county) or precincts of the contest (St. Charles). Pure parse; empty frame on a missing or half-written file.
"""
from __future__ import annotations

import html
import io
import re

import numpy as np
import pandas as pd

from . import us

NAME = "mo"
STL_PDF = "https://extcontent.stlouisco.com/BOE/eResults/el261103/Results.pdf"
STC_HTML = "https://www.livevoterturnout.com/ENR/stcharlesmoenr/33/en/Index_33.html"
RECORD = [
    {"key": "mo_stlouis_county", "url": STL_PDF, "interval": 120, "ext": "pdf", "host_class": "server-unverified"},
    {"key": "mo_stcharles", "url": STC_HTML, "interval": 120, "ext": "html", "host_class": "server-unverified"},
]
FEEDS = {"mo_house": {"stlouis": "mo_stlouis_county", "stcharles": "mo_stcharles"}}
STATES = {"mo": "Missouri"}
POLL_CLOSE = {"mo": "2026-11-04T01:00:00Z"}
SOURCE_INFO = {"mo": ("St. Louis County Board of Elections and St. Charles County Election Authority, unofficial "
                      "results", "https://stlouiscountymo.gov/st-louis-county-government/board-of-elections/",
                      "public record")}
UNITS = {"stlouis": ("us-mo-29189", "St. Louis"), "stcharles": ("us-mo-29183", "St. Charles")}

_REP = re.compile(r"^\s*(?:U\.?\s*S\.?|US|UNITED STATES)\s+REPRESENTATIVE\s*[-,]?\s*DISTRICT\s*0*(\d+)\b", re.I)
_PARTY = {"REPUBLICAN": "REP", "DEMOCRATIC": "DEM", "LIBERTARIAN": "LIB", "GREEN": "GRN"}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _row(unit: str, race: str, cand: str, party: str, votes: float, rep: float, tot: float) -> dict:
    uid, name = UNITS[unit]
    return {"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": cand, "party": party,
            "votes": votes, "early": np.nan, "reporting": rep, "units": tot, "ballots": np.nan, "registered": np.nan,
            "outstanding": np.nan}


def parse_stlouis_text(text: str) -> list[dict]:
    """Rows from the extracted text of St. Louis County's Cumulative Results Report."""
    m = re.search(r"Polling Places Reporting\s*([\d,]+)\s+of\s+([\d,]+)", text)
    rep, tot = (_num(m.group(1)), _num(m.group(2))) if m else (np.nan, np.nan)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    labels = {"Choice", "Party", "Consolidated", "Results", "Total"}
    rows, race, i = [], None, 0
    while i < len(lines):
        ln = lines[i]
        h = _REP.match(ln)
        if h:
            race, i = f"house-{int(h.group(1)):02d}", i + 1
            continue
        if race is None or ln in labels:
            i += 1
            continue
        if ln.startswith("Cast Votes:") or re.search(r"Vote for", ln, re.I):   # end of the contest / next contest
            race, i = None, i + 1
            continue
        name, j = ln, i + 1                                # NAME [PARTY] votes pct votes pct (name may wrap)
        while j < len(lines) and not re.fullmatch(r"[A-Z]{2,4}|[\d,]+", lines[j]):
            name, j = name + " " + lines[j], j + 1
        party = ""
        if j < len(lines) and re.fullmatch(r"[A-Z]{2,4}", lines[j]):
            party, j = lines[j], j + 1
        if j < len(lines) and re.fullmatch(r"[\d,]+", lines[j]):
            rows.append(_row("stlouis", race, name, us.party_code(party), _num(lines[j]), rep, tot))
            j += 4
        i = max(j, i + 1)
    return rows


def parse_stlouis(pdf: bytes) -> list[dict]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return parse_stlouis_text("\n".join(p.extract_text() or "" for p in reader.pages))


_STC_RACE = re.compile(r'aria-label="Contest: ([^"]+)"')


def parse_stcharles(page: bytes) -> list[dict]:
    t = page.decode("utf-8", "replace")
    rows = []
    starts = [m for m in _STC_RACE.finditer(t)]
    for k, m in enumerate(starts):
        h = _REP.match(html.unescape(m.group(1)))
        if not h:
            continue
        race = f"house-{int(h.group(1)):02d}"
        block = t[m.end(): starts[k + 1].start() if k + 1 < len(starts) else len(t)]
        p = re.search(r"Precincts Reported:\s*([\d,]+)\s+of\s+([\d,]+)", block)
        rep, tot = (_num(p.group(1)), _num(p.group(2))) if p else (np.nan, np.nan)
        for c in re.finditer(r'data-contestant="(\d+)" data-candidate="true"[^>]*>\s*<span[^>]*>[^<]*</span>'
                             r'<span[^>]*class="sr-only">([^<]*)</span>', block):
            label = html.unescape(c.group(2)).strip()
            name, _, party = label.rpartition(",") if "," in label else (label, "", "")
            v = re.search(rf'data-contestant="{c.group(1)}" data-votes="true"[^>]*>([\d,]*)<', block)
            if not v:
                continue
            code = next((cd for full, cd in _PARTY.items() if full in party.upper()), us.party_code(party.strip()))
            rows.append(_row("stcharles", race, name.strip(), code, _num(v.group(1) or "0"), rep, tot))
    return rows


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    rows = []
    for role, fn in (("stlouis", parse_stlouis), ("stcharles", parse_stcharles)):
        b = blobs.get(role)
        if not b:
            continue
        try:
            rows += fn(b)
        except Exception:                                 # half-written / not-yet-posted file: no rows from it
            continue
    return us._frame(rows)
