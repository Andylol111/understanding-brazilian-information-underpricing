"""Parsers for the extra_recorder snapshots: Virginia Enhanced Voting JSON and Iowa Clarity detail.xml.

Both return flat row lists / DataFrames `county x candidate x vote type`. No network, no dependency on the frozen
election modules.

VA  main document (…/2026-November-General/data): statistics (absentee applications / ballots returned by method),
    voterRegistration by locality -> `va_statistics`, `va_registration`.
    ballot item (…/data/ballot-item/<id>): ballotItemWithBreakdown.breakdownResults[].ballotOptions[].groupResults
    (groupName = vote method: Early Voting, Election Day, Mailed Absentee, Provisional, Post-Election)
    -> `va_item_rows`. Each candidate also gets a vote_type "Total" row (voteCount) at county and state level.
IA  Clarity reports/detailxml.zip: Contest > Choice > VoteType (Election Day, Absentee) > County -> `ia_rows`.
"""
from __future__ import annotations

import io
import json
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

COLUMNS = ["source", "level", "contest", "county", "candidate", "party", "vote_type", "votes"]


def _en(names) -> str:
    """Text of the English entry of an Enhanced Voting [{languageId, text}] list (first entry as fallback)."""
    for n in names or []:
        if n.get("languageId") == "en":
            return n["text"].strip()
    return (names[0]["text"].strip() if names else "")


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


def va_item_rows(item: bytes | str, source: str = "va") -> pd.DataFrame:
    """One Enhanced Voting ballot item -> rows (state + county) x candidate x vote type, plus a "Total" type."""
    b = json.loads(item)["ballotItemWithBreakdown"]
    contest = _en(b["name"])
    rows: list[dict] = []

    def add(level, county, options):
        for o in options or []:
            cand = _en(o["name"])
            party = (o.get("party") or {}).get("abbreviation") or ""
            rows.append(dict(source=source, level=level, contest=contest, county=county, candidate=cand,
                             party=party, vote_type="Total", votes=float(o.get("voteCount") or 0)))
            for g in o.get("groupResults") or []:
                rows.append(dict(source=source, level=level, contest=contest, county=county, candidate=cand,
                                 party=party, vote_type=_en(g["groupName"]), votes=float(g.get("voteCount") or 0)))

    add("state", "VIRGINIA", (b.get("summaryResults") or {}).get("ballotOptions"))
    for br in b.get("breakdownResults") or []:
        add("county", _en(br["locality"]["name"]), br.get("ballotOptions"))
    return _frame(rows)


def va_statistics(doc: bytes | str) -> pd.DataFrame:
    """Main document `statistics` -> rows (statistic, group, item, value, last_updated); includes the absentee
    ballots returned by method (Mail, In Person, Drop Off, Mail (Non-USPS), Designated Representative)."""
    d = json.loads(doc)
    rows = []
    for st in d.get("statistics") or []:
        for g in st.get("groups") or []:
            for c in g.get("children") or []:
                rows.append(dict(statistic=_en(st["name"]), group=_en(g["name"]), item=_en(c["name"]),
                                 value=c.get("value"), last_updated=c.get("lastUpdated")))
    return pd.DataFrame(rows, columns=["statistic", "group", "item", "value", "last_updated"])


def va_registration(doc: bytes | str) -> pd.DataFrame:
    d = json.loads(doc)
    return pd.DataFrame([dict(county=_en(r["localityName"]), active=r.get("activeVoters"),
                              inactive=r.get("inactiveVoters"), total=r.get("total"))
                         for r in d.get("voterRegistration") or []],
                        columns=["county", "active", "inactive", "total"])


def ia_rows(zip_or_xml: bytes, source: str = "ia") -> pd.DataFrame:
    """Clarity detail.xml (raw bytes, or the detailxml.zip holding it) -> county x candidate x vote type rows,
    plus a "Total" row per candidate at state level (Choice totalVotes)."""
    if zip_or_xml[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(zip_or_xml)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
            xml = z.read(name)
    else:
        xml = zip_or_xml
    rows: list[dict] = []
    for _, el in ET.iterparse(io.BytesIO(xml), events=("end",)):
        if el.tag != "Contest":
            continue
        contest = el.get("text", "")
        for ch in el.findall("Choice"):
            cand, party = ch.get("text", ""), ch.get("party", "")
            rows.append(dict(source=source, level="state", contest=contest, county="IOWA", candidate=cand,
                             party=party, vote_type="Total", votes=float(ch.get("totalVotes") or 0)))
            for vt in ch.findall("VoteType"):
                for c in vt.findall("County"):
                    rows.append(dict(source=source, level="county", contest=contest, county=c.get("name", ""),
                                     candidate=cand, party=party, vote_type=vt.get("name", ""),
                                     votes=float(c.get("votes") or 0)))
        el.clear()
    return _frame(rows)
