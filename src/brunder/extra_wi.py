"""Wisconsin's 1st district (WI-01) from two county clerks' results pages (secondary source, added 2026-11-02,
unregistered). Wisconsin has no statewide night feed (elections.wi.gov: Cloudflare 403). Kenosha (22 % of the
district's 2024 presidential vote) and Rock (16 %, its WI-01 part) are covered; Racine (27 %, site 403), Milwaukee
(21 %, Cloudflare challenge) and Walworth (14 %, PDFs under changing DocumentCenter ids) are not and stay uncounted.

  Kenosha County: https://apps.kenoshacounty.org/ElectionResults_v2/Default.aspx?eid=70 (70 = 2026-11-03 general,
      64 = 2024 general); <div class="contestBox"><h1>Representative in Congress</h1><h2>(N polls reporting out of
      M)</h2> rows "&nbsp;REP Bryan Steil" ... "47584 (53.38%)". All of Kenosha County is in WI-01.
  Rock County: http://elections.co.rock.wi.us/20261103.html (20241105.html = 2024); contest tables with
      titlebox "Representative in Congress District 1", candidate "Name (Party)", votes, then "Precincts Reporting:
      X of Y" of that contest.

Rows: level "county", race "house-NN", reporting/units = polls / precincts reporting of the contest.
"""
from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd

from . import us

NAME = "wi"
KENOSHA = "https://apps.kenoshacounty.org/ElectionResults_v2/Default.aspx?eid=70"
ROCK = "http://elections.co.rock.wi.us/20261103.html"
RECORD = [
    {"key": "wi_kenosha", "url": KENOSHA, "interval": 120, "ext": "html", "host_class": "server-unverified"},
    {"key": "wi_rock", "url": ROCK, "interval": 120, "ext": "html", "host_class": "server-unverified"},
]
FEEDS = {"wi_house": {"kenosha": "wi_kenosha", "rock": "wi_rock"}}
STATES = {"wi": "Wisconsin"}
POLL_CLOSE = {"wi": "2026-11-04T02:00:00Z"}
SOURCE_INFO = {"wi": ("Kenosha County and Rock County clerks, unofficial election results",
                      "https://apps.kenoshacounty.org/ElectionResults_v2/", "public record")}
UNITS = {"kenosha": ("us-wi-55059", "Kenosha"), "rock": ("us-wi-55105", "Rock")}
LIVE_UNITS = {"wi_house": {u for u, _ in UNITS.values()}}   # partial coverage (brunder.us_extra_live coverage)
_PARTY = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "LIBERTARIAN": "LIB", "GREEN": "GRN"}


def _row(unit, race, cand, party, votes, rep, tot):
    uid, name = UNITS[unit]
    return {"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": cand, "party": party,
            "votes": float(votes), "early": np.nan, "reporting": rep, "units": tot, "ballots": np.nan,
            "registered": np.nan, "outstanding": np.nan}


def _int(s: str) -> float:
    return float(s.replace(",", "").strip() or 0)


def parse_kenosha(page: bytes) -> list[dict]:
    t = page.decode("utf-8", "replace")
    rows = []
    for box in re.split(r'<div class="contestBox">', t)[1:]:
        h = re.search(r"<h1>(.*?)</h1>", box, re.S)
        title = re.sub(r"<[^>]+>", " ", h.group(1)).strip() if h else ""
        if not re.fullmatch(r"Representative in Congress(?:\s+District\s+\d+)?", " ".join(title.split()), re.I):
            continue
        d = re.search(r"District\s+(\d+)", title)
        race = f"house-{int(d.group(1)) if d else 1:02d}"           # Kenosha County lies wholly in WI-01
        p = re.search(r"\(([\d,]+) polls reporting out of ([\d,]+)\)", box)
        rep, tot = (_int(p.group(1)), _int(p.group(2))) if p else (np.nan, np.nan)
        for c in re.finditer(r'<td class="candtd"\s*>(.*?)</td>.*?z-index: 1\'>([\d,]+)\s*[(<]', box, re.S):
            label = html.unescape(re.sub(r"<[^>]+>", "", c.group(1))).replace("\xa0", " ").strip()
            m = re.match(r"([A-Z]{2,4})\s+(.+)", label)
            party, cand = (m.group(1), m.group(2)) if m else ("", label)
            rows.append(_row("kenosha", race, cand, us.party_code(party), _int(c.group(2)), rep, tot))
    return rows


def parse_rock(page: bytes) -> list[dict]:
    t = page.decode("utf-8", "replace")
    rows = []
    for m in re.finditer(r'class="titlebox" style="width: 75%;";>\s*Representative in Congress District (\d+)', t):
        race = f"house-{int(m.group(1)):02d}"
        end = t.find("Precincts Reporting:", m.end())
        block = t[m.end(): end if end > 0 else len(t)]
        p = re.match(r"Precincts Reporting:\s*([\d,]+)\s+of\s+([\d,]+)", t[end:end + 80]) if end > 0 else None
        rep, tot = (_int(p.group(1)), _int(p.group(2))) if p else (np.nan, np.nan)
        for c in re.finditer(r'<td class="candidate">\s*([^<]*?)\s*<span class="party">\s*\(([^)]*)\)</span></td>'
                             r'.*?<td class="votes">\s*([\d,]+)\s*</td>', block, re.S):
            cand, party = html.unescape(c.group(1)).strip(), c.group(2).strip().upper()
            code = _PARTY.get(party, us.party_code(party) if party not in ("NONPARTISAN",) else "")
            rows.append(_row("rock", race, cand, code, _int(c.group(3)), rep, tot))
    return rows


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    rows = []
    for role, fn in (("kenosha", parse_kenosha), ("rock", parse_rock)):
        b = blobs.get(role)
        if not b:
            continue
        try:
            rows += fn(b)
        except Exception:
            continue
    return us._frame(rows)
