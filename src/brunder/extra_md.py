"""Maryland U.S. House (MD-01) from the State Board of Elections night pages (elections.maryland.gov, static HTML
rewritten by the board during the count). SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED (docs/us_coverage_2026_ne.md).

detail page  gen_detail_results_2026_4_<n>_District <n>.html: one table, rows = jurisdiction (county, only its part of a
             split district), columns = candidates, cells = total votes ("NR" = not reported). Source of the county rows.
summary page gen_results_2026_4_<n>.html: district totals by vote type (Early Voting / Election Day / Mail-In /
             Provisional) and "(r of N election day precincts reported)"; recorded for the record, not parsed here
             (the county table has neither a vote-type split nor county precinct counts, so early / reporting / units
             are NaN, as for the Pennsylvania feed).
Past-cycle check: 2024 general detail page (archive/2024/General_Results/gen_detail_results_2024_3_1_District 1.html,
Congress was contest 3 in 2024, 4 in 2026): tests/fixtures/us/extra_md_2024_cd01_detail.html.gz; both page layouts
(2024 and the 2026 template) are handled.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import numpy as np
import pandas as pd

from brunder import us

NAME = "md"
_B = "https://elections.maryland.gov/elections/2026/general_results"
RECORD = [dict(key="md_house_d01_detail", url=f"{_B}/gen_detail_results_2026_4_1_District%201.html", interval=60,
               ext="html"),
          dict(key="md_house_d01_summary", url=f"{_B}/gen_results_2026_4_1.html", interval=120, ext="html")]
FEEDS = {"md_house": {"d01": "md_house_d01_detail"}}
STATES = {"md": "Maryland"}
POLL_CLOSE = {"md": "2026-11-04T01:00:00Z"}

_FIPS = {"allegany": "001", "annearundel": "003", "baltimore": "005", "calvert": "009",
         "caroline": "011", "carroll": "013", "cecil": "015", "charles": "017", "dorchester": "019",
         "frederick": "021", "garrett": "023", "harford": "025", "howard": "027", "kent": "029",
         "montgomery": "031", "princegeorges": "033", "queenannes": "035", "stmarys": "037",
         "saintmarys": "037", "somerset": "039", "talbot": "041", "washington": "043", "wicomico": "045",
         "worcester": "047", "baltimorecity": "510"}
_NAMES = {"001": "Allegany", "003": "Anne Arundel", "005": "Baltimore County", "009": "Calvert", "011": "Caroline",
          "013": "Carroll", "015": "Cecil", "017": "Charles", "019": "Dorchester", "021": "Frederick",
          "023": "Garrett", "025": "Harford", "027": "Howard", "029": "Kent", "031": "Montgomery",
          "033": "Prince George's", "035": "Queen Anne's", "037": "Saint Mary's", "039": "Somerset",
          "041": "Talbot", "043": "Washington", "045": "Wicomico", "047": "Worcester", "510": "Baltimore City"}
_PARTIES = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "LIBERTARIAN": "LIB", "GREEN": "GRN", "UNAFFILIATED": "IND"}
_DISTRICT = re.compile(r"Representative in Congress\s+District\s+(\d+)", re.I)


class _Tables(HTMLParser):
    """Tables as lists of rows of cells; a cell = its text segments split at <br>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[list[str]]]] = []
        self._cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables and self.tables[-1]:
            self._cell = [""]
        elif tag == "br" and self._cell is not None:
            self._cell.append("")
        if tag in ("br", "div", "h1", "h2", "h3", "h4", "p", "tr", "li"):
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.tables[-1][-1].append([s.strip() for s in self._cell if s.strip()])
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[-1] += data
        self.text.append(data)


def _votes(s: str) -> float | None:
    s = s.replace(",", "").strip()
    return float(s) if re.fullmatch(r"\d+", s) else None


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"\bcounty\b", "", name.lower()))


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    """MD county table of one congressional district -> county rows (race "house-NN"); NR cells are skipped, so an
    uncounted page gives an empty frame. Never raises on an unexpected page."""
    parts = []
    for blob in blobs.values():
        for b in (blob if isinstance(blob, list) else [blob]):
            if b:
                parts.append(_parse_page(b))
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else us._frame([])


def _parse_page(blob: bytes) -> pd.DataFrame:
    try:
        p = _Tables()
        p.feed(blob.decode("utf-8", "replace"))
        m = _DISTRICT.search(re.sub(r"\s+", " ", "".join(p.text)))
    except Exception:
        return us._frame([])
    if not m:
        return us._frame([])
    race, rows = f"house-{int(m.group(1)):02d}", []
    for t in p.tables:
        if len(t) < 2 or not t[0] or not t[0][0] or t[0][0][0].lower() != "jurisdiction":
            continue
        cands = []
        for c in t[0][1:]:
            segs = [s for s in c if s]
            party = _PARTIES.get(segs[-1].upper(), "") if len(segs) > 1 else ""
            cands.append((segs[0] if segs else "", party))
        for r in t[1:]:
            if not r or not r[0]:
                continue
            fips = _FIPS.get(_key(r[0][0]))
            if fips is None:
                continue
            for (cand, party), cell in zip(cands, r[1:]):
                v = _votes(cell[0] if cell else "")
                if v is None:
                    continue
                rows.append({"level": "county", "unit_id": f"us-md-24{fips}", "name": _NAMES[fips], "race": race,
                             "candidate": cand, "party": party, "votes": v, "early": np.nan, "reporting": np.nan,
                             "units": np.nan, "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    df = us._frame(rows)
    return df if len(df) and df["votes"].sum() > 0 else us._frame([])
