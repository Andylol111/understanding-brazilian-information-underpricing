"""US midterms 2026-11-03: official county feeds, county priors, election-night nowcast, dashboard export.

Feeds (recorded passively by `brunder snap us_midterms_2026`, deploy/brunder-snap-us.service; this module never
polls them on the night, it reads the recorder's snapshots):
  ca  California SoS API          /returns/governor/county/all (+ /returns/status)       58 counties
  tx  Texas SoS Civix ENR 53815   election/<id> (statewide) + countyInfo/<id>             254 counties
  oh  Ohio SoS                    NIST SP 1500-100 (1622) XML + election summary CSV      88 counties
  ak  Alaska Division of Elections enr26/results/statewide.js                            40 house districts
  ga  Georgia SoS Enhanced Voting  elections/Georgia/<id>/data/ballot-item/<item>         159 counties

Every parser turns snapshot bytes into the same long table (COLS): one row per unit × race × candidate with the
unit's progress fields (precincts/polling places reporting, ballots counted, registered, ballots known to be
outstanding). `level` is "county"/"district" for units and "state" for the feed's own statewide total.

Model: `brunder.nowcast` with units = counties (AK: house districts) and region = state, on the two-party
(Democrat vs Republican) share. The US-specific part is `f`, the share of a county's expected vote already
counted: precincts reporting says little about how many mail/provisional ballots remain, so f = n / E with E
an expected-vote estimate per county (prior-race votes × turnout ratio, re-scaled on the night by the counties
whose count is known to be complete). See docs/us_midterms.md for settings and their pre-election evidence.

Output: the dashboard data contract (meta.json, timeline.json, units/<race>.json) for every configured race,
from the snapshot archive up to a time (replay) or repeatedly up to now (live).
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import csv
import gzip
import io
import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import kalshi, live, nowcast
from .paths import PROCESSED, RAW
from .paths import ROOT as ROOT_DIR
from . import us_feeds_extra as _extra
from . import civic as _civic
from .snap import SNAP, _name

TAG = "us_midterms_2026"
EID = "us2026"
US_RAW = RAW / "us"
US_PROC = PROCESSED / "us2026"
UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
COLS = ["level", "unit_id", "name", "race", "candidate", "party", "votes", "early",
        "reporting", "units", "ballots", "registered", "outstanding"]

FEEDS = {           # snapshot keys (snap._name of the recorded URL); "*" = every key with that prefix
    "ca": {"results": _name("https://api.sos.ca.gov/returns/governor/county/all"),
           "status": _name("https://api.sos.ca.gov/returns/status")},
    "tx": {"election": _name("https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/53815"),
           "county": _name("https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/countyInfo/53815")},
    "oh": {"xml": _name("https://liveresults.ohiosos.gov/Api/v1/download?filename=VSSC1622XmlFileBlob"),
           "csv": _name("https://liveresults.ohiosos.gov/Api/v1/Contest/GetElectionSummaryCsv")},
    "ak": {"js": _name("https://www.elections.alaska.gov/enr26/results/statewide.js")},
    "ga": {"item": "ga_enr_Georgia_item_*"},
    "az": {"item": "ga_enr_Arizona_item_*"},           # Enhanced Voting, same resolver as GA (--ga-enr …/Arizona)
    "sc": {"all": "clarity_" + _name("https://www.enr-scvotes.org/SC"),          # Clarity, day-of eid (resolver)
           "summary": "clarity_" + _name("https://www.enr-scvotes.org/SC") + "_summary",
           "status": "clarity_" + _name("https://www.enr-scvotes.org/SC") + "_status"},
    "vt": {"main": "vt_static_main", "statewide": "vt_static_statewide"},          # resolver --vt-static
    "nc": {"county": "ncsbe_county", "results": "ncsbe_results_*"},                # resolver --ncsbe YYYYMMDD
    "fl": {"enight": _name("https://flelectionfiles.floridados.gov/enightfilespublic/20261103_ElecResultsFL.txt")},
}
FEEDS.update({f"{k}_house": FEEDS[k] for k in ("tx", "oh", "sc", "nc", "fl")})   # House districts: brunder.us_house_night
FEEDS.update({"ga_house": {"item": "ga_enr_Georgia_house_*"}, "az_house": {"item": "ga_enr_Arizona_house_*"}})
STATES = {"ca": "California", "tx": "Texas", "oh": "Ohio", "ga": "Georgia", "ak": "Alaska", "az": "Arizona",
          "sc": "South Carolina", "vt": "Vermont", "nc": "North Carolina", "fl": "Florida"}
FEEDS.update(_extra.FEEDS)       # PA, MN, NE, NM (brunder.us_feeds_extra, added 2026-10-30); parsed there
STATES.update(_extra.STATES)
FEEDS.update(_extra.HOUSE_FEEDS)  # PA, MN, NE, NM House districts (brunder.us_house_night; added 2026-10-30)
FEEDS.update(_civic.FEEDS)       # civicAPI, secondary source (brunder.civic): civic_<st> kinds, one per state
STATES.update(_civic.STATES)


# ------------------------------------------------------------------ units

def _norm(name: str) -> str:
    return re.sub(r"[^A-Z]", "", re.sub(r"\s+COUNTY\b", "", name.upper()))


def counties(st: str) -> pd.DataFrame:
    """Census 2020 county list for a state: unit_id (us-<st>-<5-digit FIPS>), name, key (normalised name).
    Texas rows are in the Texas SoS county-number order (ENR county id = row number), which is not FIPS order."""
    if st == "ak":
        return pd.DataFrame({"unit_id": [f"us-ak-hd{d:02d}" for d in range(1, 41)],
                             "name": [f"House District {d}" for d in range(1, 41)],
                             "key": [str(d) for d in range(1, 41)]})
    if st not in _COUNTIES:                          # other states: the dashboard's Census geometry, FIPS order
        g = sorted((uid, name) for uid, name in _geo() if uid.startswith(f"us-{st}-"))
        names = [re.sub(r"\s+(County|Parish|Borough|city|City and Borough|Census Area|Municipality)$", "",
                        re.sub(r",\s*[A-Z]{2}$", "", n)) for _, n in g]
        return pd.DataFrame({"unit_id": [u for u, _ in g], "name": names, "key": [_norm(n) for n in names]})
    fips = {"ca": "06", "tx": "48", "oh": "39", "ga": "13", "az": "04", "sc": "45", "vt": "50"}[st]
    items = [x.rsplit(":", 1) for x in _COUNTIES[st].split(",")]
    return pd.DataFrame({"unit_id": [f"us-{st}-{fips}{c}" for _, c in items], "name": [n for n, _ in items],
                         "key": [_norm(n) for n, _ in items]})


def _ids(st: str) -> dict[str, tuple[str, str]]:
    c = counties(st)
    return dict(zip(c["key"], zip(c["unit_id"], c["name"])))


def race_key(name: str) -> str | None:
    s = " ".join(re.sub(r"[^a-z]", " ", name.lower()).split())
    if s.startswith("governor"):
        return "gov"
    if re.match(r"(u s |us |united states )senat", s) and "district" not in s:
        return "sen"
    if s.startswith("president"):
        return "pres"
    return None


def party_code(s: str | None) -> str:
    s = (s or "").strip().upper()
    for full, code in (("DEM", "DEM"), ("REP", "REP"), ("LIB", "LIB"), ("GR", "GRN")):
        if s.startswith(full):
            return code
    return s[:3] if s else ""


def _int(x) -> float:
    if x is None or x == "":
        return np.nan
    return float(str(x).replace(",", "").replace("=", "").replace('"', "").strip() or "nan")


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    df["votes"] = df["votes"].astype(float)
    return df


# ------------------------------------------------------------------ parsers (snapshot bytes -> rows)

def parse_ca(results: bytes, status: bytes | None = None) -> pd.DataFrame:
    """CA SoS API: list of {raceTitle "Governor - <County> County Results" | "... - Statewide Results",
    Reporting "x% (k of n) precincts reporting", candidates[{Name, Party, Votes}]}. /returns/status adds per-county
    ballots counted (voterTurnout) and registration. Precincts reporting is NOT the share of ballots counted in CA
    (vote-by-mail and provisionals are counted for weeks)."""
    ids = _ids("ca")
    prog = {}
    if status:
        for k, v in json.loads(status).items():
            if isinstance(v, dict) and "countyName" in v:
                key = "STATE" if k == "statewide" else _norm(v["countyName"])
                prog[key] = (_int(v.get("voterTurnout")), _int(v.get("totalRegisteredVoters")))
    rows = []
    for r in json.loads(results):
        race_name, _, where = r["raceTitle"].partition(" - ")
        race = race_key(race_name) or race_name
        m = re.search(r"\(([\d,]+) of ([\d,]+)\)", r.get("Reporting", ""))
        rep, tot = (_int(m.group(1)), _int(m.group(2))) if m else (np.nan, np.nan)
        if where.startswith("Statewide"):
            level, uid, name, key = "state", "us-ca", "California", "STATE"
        else:
            key = _norm(where.replace(" Results", ""))
            uid, name = ids[key]
            level = "county"
        ballots, reg = prog.get(key, (np.nan, np.nan))
        for c in r["candidates"]:
            rows.append({"level": level, "unit_id": uid, "name": name, "race": race, "candidate": c["Name"],
                         "party": party_code(c.get("Party")), "votes": _int(c["Votes"]), "early": np.nan,
                         "reporting": rep, "units": tot, "ballots": ballots, "registered": reg,
                         "outstanding": np.nan})
    return _frame(rows)


def _b64json(s: str):
    return json.loads(base64.b64decode(s))


def parse_tx(election: bytes | None, county: bytes) -> pd.DataFrame:
    """TX SoS Civix ENR: payloads are base64 JSON. countyInfo: {county id: {N, Summary{PRR, PRP precincts,
    PLR, NPL polling places, VC ballots, RV registered}, Races{race id: {N, C{cand id: {N, P, V, EV}}}}}};
    the election file's StateWide/Federal races carry the statewide totals. EV = the feed's early-vote count.
    County id = Texas county number (row order of counties('tx'))."""
    c = counties("tx")
    rows = []
    for cid, x in _b64json(json.loads(county)["upload"]).items():
        uid, name = c.loc[int(cid) - 1, ["unit_id", "name"]]
        s = x.get("Summary") or {}
        if isinstance(s, str):
            s = json.loads(s.replace("'", '"'))
        rep, tot = (s.get("PLR"), s.get("NPL")) if (s.get("NPL") or 0) > 0 else (s.get("PRR"), s.get("PRP"))
        for r in x.get("Races", {}).values():
            race = race_key(r["N"])
            if race is None:
                continue
            for cand in r["C"].values():
                rows.append({"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": cand["N"],
                             "party": party_code(cand.get("P")), "votes": float(cand.get("V") or 0),
                             "early": float(cand.get("EV") or 0), "reporting": _int(rep), "units": _int(tot),
                             "ballots": _int(s.get("VC")), "registered": _int(s.get("RV")), "outstanding": np.nan})
    if election:
        e = json.loads(election)
        for k in ("StateWide", "Federal"):
            if not isinstance(e.get(k), str) or len(e[k]) < 40:
                continue
            for r in _b64json(e[k]).get("Races", []):
                race = race_key(r["N"])
                if race is None:
                    continue
                for cand in r["Candidates"]:
                    rows.append({"level": "state", "unit_id": "us-tx", "name": "Texas", "race": race,
                                 "candidate": cand["N"], "party": party_code(cand.get("P")),
                                 "votes": float(cand.get("V") or 0), "early": float(cand.get("EV") or 0),
                                 "reporting": np.nan, "units": np.nan, "ballots": np.nan, "registered": np.nan,
                                 "outstanding": np.nan})
    return _frame(rows)


_NIST = "{http://grouper.ieee.org/groups/1622/groups/2/V1/1622_2-election_results_reporting.xsd}"
_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"


def parse_oh_summary(data: bytes) -> pd.DataFrame:
    """OH election summary CSV: County, Registered Voters, Ballots Counted, ..., Outstanding UOCAVA Absentees,
    Outstanding Provisionals, Total Precincts, Election Day Precincts Reporting, ..."""
    ids = _ids("oh")
    rd = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    head = [h.strip() for h in next(rd)]
    out = []
    for row in rd:
        if not row:
            continue
        d = dict(zip(head, row))
        key = "STATE" if d["County"].strip() == "Totals" else _norm(d["County"])
        uid = "us-oh" if key == "STATE" else ids[key][0]
        out.append({"unit_id": uid, "registered": _int(d["Registered Voters"]), "ballots": _int(d["Ballots Counted"]),
                    "outstanding": _int(d["Outstanding UOCAVA Absentees"]) + _int(d["Outstanding Provisionals"]),
                    "reporting": _int(d["Election Day Precincts Reporting"]), "units": _int(d["Total Precincts"])})
    return pd.DataFrame(out)


def parse_oh(xml: bytes, summary: bytes | None = None) -> pd.DataFrame:
    """OH SoS NIST 1622 XML: GpUnit (Type County, not electoral district) carries SubUnitsReported/TotalSubUnits,
    VotersParticipated/VotersRegistered and SummaryCounts with BallotsOutstanding (absentee, provisional);
    each BallotSelection has VoteCounts (Type Total) per GpUnitId. The summary CSV, when given, overrides the
    progress fields (same quantities, published separately)."""
    root = ET.fromstring(xml)
    ids = _ids("oh")
    units = {}
    for g in root.iter(f"{_NIST}GpUnit"):
        if g.get("Type") == "State":
            units[g.get("ObjectId")] = ("state", "us-oh", "Ohio", {})
        elif g.get("Type") == "County" and g.get("IsElectoralDistrict") == "false":
            uid, name = ids[_norm(g.get("Name"))]
            out = sum(_int(s.get("BallotsOutstanding")) for s in g.findall(f"{_NIST}SummaryCounts")
                      if s.get("BallotsOutstanding") is not None)
            units[g.get("ObjectId")] = ("county", uid, name, {
                "reporting": _int(g.get("SubUnitsReported")), "units": _int(g.get("TotalSubUnits")),
                "ballots": _int(g.get("VotersParticipated")), "registered": _int(g.get("VotersRegistered")),
                "outstanding": float(out)})
    parties = {p.get("ObjectId"): p.get("Abbreviation") for p in root.iter(f"{_NIST}Party")}
    cands = {}
    for c in root.iter(f"{_NIST}Candidate"):
        cands[c.get("ObjectId")] = (c.findtext(f"{_NIST}BallotName/{_NIST}LanguageString") or "",
                                    parties.get(c.findtext(f"{_NIST}PartyId"), ""))
    if summary:
        prog = parse_oh_summary(summary).drop_duplicates("unit_id").set_index("unit_id").to_dict("index")
        for k, (lvl, uid, name, p) in units.items():
            if uid in prog:
                units[k] = (lvl, uid, name, {**p, **prog[uid]})
    rows = []
    for con in root.iter(f"{_NIST}Contest"):
        race = race_key(con.get("Name") or "")
        if race is None:
            continue
        for bs in con.findall(f"{_NIST}BallotSelection"):
            cid = (bs.findtext(f"{_NIST}CandidateId") or "").split()
            name, party = cands.get(cid[0], ("", "")) if cid else ("", "")
            for vc in bs.iter(f"{_NIST}VoteCounts"):
                if vc.get("Type") != "Total":
                    continue
                u = units.get(vc.findtext(f"{_NIST}GpUnitId"))
                if u is None:
                    continue
                lvl, uid, uname, p = u
                rows.append({"level": lvl, "unit_id": uid, "name": uname, "race": race, "candidate": name,
                             "party": party_code(party), "votes": _int(vc.get("Count")), "early": np.nan,
                             **{k: p.get(k, np.nan) for k in ("reporting", "units", "ballots", "registered",
                                                               "outstanding")}})
    return _frame(rows)


def parse_ak(js: bytes) -> pd.DataFrame:
    """AK Division of Elections statewide.js (JSON despite the name): contestNames, candidateNames
    ("Last, First (PTY)"), totals[contest] and districts[{number, contests}] with per contest b (ballots),
    t (votes), v (registered), p/r (precincts / reporting) and c[{n: candidate index, t: votes}]."""
    d = json.loads(js)
    names = d["candidateNames"]

    def rows_for(level, uid, name, contests):
        out = []
        for c in contests:
            race = race_key(d["contestNames"][c["n"]])
            if race is None:
                continue
            for x in c["c"]:
                cand = names[x["n"]]
                m = re.search(r"\(([A-Z]+)(?:/[A-Z]+)?\)\s*$", cand)
                out.append({"level": level, "unit_id": uid, "name": name, "race": race, "candidate": cand,
                            "party": party_code(m.group(1)) if m else "", "votes": float(x["t"]), "early": np.nan,
                            "reporting": float(c.get("r", np.nan)), "units": float(c.get("p", np.nan)),
                            "ballots": float(c.get("b", np.nan)), "registered": float(c.get("v", np.nan)),
                            "outstanding": np.nan})
        return out

    rows = rows_for("state", "us-ak", "Alaska", d["totals"])
    for dist in d["districts"]:
        k = int(dist["number"])
        rows += rows_for("district", f"us-ak-hd{k:02d}", f"House District {k}", dist["contests"])
    return _frame(rows)


_GA_EARLY = re.compile(r"^(advanced? voting|absentee by mail)", re.I)   # 2020/22: "... Votes" suffix
_EV_EARLY = {"ga": _GA_EARLY, "az": re.compile(r"^early (voting|a\.r\.s)", re.I)}   # AZ "Late Early*" excluded


def parse_ga(item: bytes, st: str = "ga") -> pd.DataFrame:
    """Enhanced Voting ballot item (…/elections/<Georgia|Arizona>/<id>/data/ballot-item/<item id>), GA SoS and AZ SoS:
    ballotItemWithBreakdown {name, summaryResults.ballotOptions (statewide), breakdownResults[{locality.name,
    precinctReportingStatus{reportingUnits,totalUnits}, ballotOptions[{name, party.abbreviation, voteCount,
    groupResults[{groupName, voteCount}]}]}]}. early = GA Advanced Voting + Absentee by Mail; AZ Early Voting +
    Early A.R.S. 16-579 (not the late earlies counted after Election Day)."""
    b = json.loads(item)["ballotItemWithBreakdown"]
    race = race_key(b["name"][0]["text"])
    ids, early_re = _ids(st), _EV_EARLY[st]

    def opts(level, uid, name, options, status):
        out = []
        for o in options:
            early = sum(g["voteCount"] for g in o.get("groupResults") or []
                        if early_re.match(g["groupName"][0]["text"].strip()))
            out.append({"level": level, "unit_id": uid, "name": name, "race": race,
                        "candidate": re.sub(r"\s*\((?:I\)\s*\()?[A-Za-z]+\)\s*$", "", o["name"][0]["text"]).strip(),
                        "party": party_code((o.get("party") or {}).get("abbreviation")),
                        "votes": float(o["voteCount"]), "early": float(early),
                        "reporting": _int((status or {}).get("reportingUnits")),
                        "units": _int((status or {}).get("totalUnits")), "ballots": np.nan,
                        "registered": np.nan, "outstanding": np.nan})
        return out

    rows = opts("state", f"us-{st}", STATES[st], b["summaryResults"]["ballotOptions"], b.get("reportingStatus"))
    for br in b.get("breakdownResults") or []:
        uid, name = ids[_norm(br["locality"]["name"][0]["text"])]
        rows += opts("county", uid, name, br["ballotOptions"], br.get("precinctReportingStatus"))
    return _frame(rows)


def parse_clarity(all_json: bytes, summary: bytes, status: bytes | None = None, st: str = "sc") -> pd.DataFrame:
    """Clarity/Scytl ENR (SC State Election Commission, enr-scvotes.org/SC/<eid>/<version>/json/...):
    en/summary.json = statewide contests [{K key, C name, CH choices "PTY Name", P parties, V votes}];
    ALL.json = {Contests: [{A county, C [contest keys], V [[votes per choice, in summary CH order]]}]} plus one
    statewide row; status.json = per county P names, PX precincts reporting, PY precincts, B ballots, R registered."""
    ids = _ids(st)
    contests = {}
    for c in json.loads(summary):
        race = race_key(c["C"])
        if race is not None:
            contests[c["K"]] = (race, c["CH"], c.get("P") or [""] * len(c["CH"]), c.get("V") or [])
    prog = {}
    if status:
        s = json.loads(status)
        for i, name in enumerate(s.get("P") or []):
            k = _norm(name)
            if k in ids:
                prog[ids[k][0]] = {"reporting": _int(s["PX"][i]), "units": _int(s["PY"][i]),
                                   "ballots": _int(s["B"][i]), "registered": _int(s["R"][i])}

    def rows_for(level, uid, name, key, votes, p):
        race, ch, parties, _ = contests[key]
        return [{"level": level, "unit_id": uid, "name": name, "race": race,
                 "candidate": re.sub(r"^[A-Z]{2,4} ", "", choice), "party": party_code(party), "votes": float(v),
                 "early": np.nan, "reporting": p.get("reporting", np.nan), "units": p.get("units", np.nan),
                 "ballots": p.get("ballots", np.nan), "registered": p.get("registered", np.nan),
                 "outstanding": np.nan} for choice, party, v in zip(ch, parties, votes)]

    rows = []
    for key, (race, ch, parties, v) in contests.items():
        rows += rows_for("state", f"us-{st}", STATES[st], key, v, {})
    for cty in json.loads(all_json)["Contests"]:
        k = _norm(cty["A"])
        if k not in ids:                              # the statewide row of ALL.json
            continue
        uid, name = ids[k]
        for key, votes in zip(cty["C"], cty["V"]):
            if key in contests:
                rows += rows_for("county", uid, name, key, votes, prog.get(uid, {}))
    return _frame(rows)


def parse_vt(main: bytes, statewide: bytes) -> pd.DataFrame:
    """Vermont SoS results (static.electionresults.vermont.gov/elections/<guid>.json and the statewide-offices file
    it points to): per office (GOVERNOR, …) per town 'cs' rows with candidate votes 'rc' [{cn, vc, pn}] and
    distReported "k/1". Towns are aggregated to Vermont's 14 counties with the main file's townDistricts map;
    reporting/units = towns reported / towns in the county."""
    town_cty = {_norm(t["townName"]): _norm(t["countyName"]) for t in json.loads(main).get("townDistricts") or []}
    ids = _ids("vt")
    acc: dict = {}
    for grp in json.loads(statewide)["d"]:
        for o in grp.get("o") or []:
            race = race_key(o["on"])
            if race is None:
                continue
            for town in o.get("cs") or []:
                cty = town_cty.get(_norm(town["tn"]))
                if cty not in ids:
                    continue
                done = str(town.get("distReported") or "0/1").split("/")[0] not in ("0", "")
                a = acc.setdefault((race, cty), {"votes": {}, "rep": 0, "units": 0})
                a["rep"] += int(done)
                a["units"] += 1
                for c in town.get("rc") or []:
                    key = (c["cn"], party_code((c.get("pn") or "").split("/")[0]))
                    a["votes"][key] = a["votes"].get(key, 0.0) + float(c.get("vc") or 0)
    rows = []
    for (race, cty), a in acc.items():
        uid, name = ids[cty]
        for (cand, party), v in a["votes"].items():
            rows.append({"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": cand,
                         "party": party, "votes": v, "early": np.nan, "reporting": float(a["rep"]),
                         "units": float(a["units"]), "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    return _frame(rows)


def _json_values(blob: bytes) -> list:
    """All top-level JSON values of a text body (NCSBE files hold one array; tolerate several)."""
    t, dec, i, out = blob.decode("utf-8-sig"), json.JSONDecoder(), 0, []
    while i < len(t):
        while i < len(t) and t[i] in " \r\n\t":
            i += 1
        if i >= len(t):
            break
        v, i = dec.raw_decode(t, i)
        out.append(v)
    return out


def parse_nc(county_txt: bytes, results: list[bytes]) -> pd.DataFrame:
    """NC State Board of Elections ENR (er.ncsbe.gov/enr/<YYYYMMDD>/data): county.txt (per county: precincts
    reported prt / total ptl, ballots cast bct, registered rtl) and results_<county id>.txt (per contest and
    candidate: total vct, one-stop early evc, Election Day ovc, absentee by mail avc, provisional pvc). early =
    one-stop + mail. Statewide rows are the county sums."""
    ids = _ids("nc")
    info = {}
    for c in (c for arr in _json_values(county_txt) for c in arr):
        if str(c.get("cid")) != "0":
            info[str(c["cid"])] = c
    rows = []
    for blob in results:
        for x in (x for arr in _json_values(blob) for x in arr):
            race = race_key(re.sub(r"\(VOTE FOR.*", "", x.get("cnm", "")))
            c = info.get(str(x.get("cid")))
            if race is None or c is None or _norm(c["cnm"]) not in ids:
                continue
            uid, name = ids[_norm(c["cnm"])]
            rows.append({"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": x["bnm"],
                         "party": party_code(x.get("pty")), "votes": float(x.get("vct") or 0),
                         "early": float(x.get("evc") or 0) + float(x.get("avc") or 0),
                         "reporting": float(x.get("prt") or 0), "units": float(x.get("ptl") or 0),
                         "ballots": float(c.get("bct") or 0), "registered": float(c.get("rtl") or 0),
                         "outstanding": np.nan})
    df = _frame(rows)
    if df.empty:
        return df
    st = df.groupby(["race", "candidate", "party"], as_index=False)[["votes", "early", "reporting", "units"]].sum()
    st = st.assign(level="state", unit_id="us-nc", name="North Carolina", ballots=np.nan, registered=np.nan,
                   outstanding=np.nan)
    return _frame(df.to_dict("records") + st.to_dict("records"))


_FL_RACES = {"USS": "sen", "GOV": "gov"}


def parse_fl(blob: bytes) -> pd.DataFrame:
    """Florida Division of Elections results text file (tab-separated; election-night file
    flelectionfiles.floridados.gov/enightfilespublic/<YYYYMMDD>_ElecResultsFL.txt, and the official extract of
    results.elections.myflorida.com): one row per county × candidate with RaceCode (USS, GOV), PartyCode, Precincts,
    PrecinctsReporting, CanVotes. No vote-type split (early and mail votes post at poll close with 0 precincts
    reporting). Statewide rows are the county sums."""
    try:                                             # election-night file UTF-8; official extract latin-1
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    d = pd.read_csv(io.StringIO(text), sep="\t", dtype=str, keep_default_na=False)
    d = d[d["RaceCode"].str.strip().isin(list(_FL_RACES))]
    ids = _ids("fl")
    rows = []
    for x in d.to_dict("records"):
        k = _norm(x["CountyName"])
        if k not in ids:
            continue
        uid, name = ids[k]
        rows.append({"level": "county", "unit_id": uid, "name": name, "race": _FL_RACES[x["RaceCode"].strip()],
                     "candidate": " ".join(f"{x['CanNameFirst']} {x['CanNameLast']}".split()),
                     "party": party_code(x["PartyCode"].strip()), "votes": float(x["CanVotes"] or 0),
                     "early": np.nan, "reporting": float(x["PrecinctsReporting"] or 0),
                     "units": float(x["Precincts"] or 0), "ballots": np.nan, "registered": np.nan,
                     "outstanding": np.nan})
    df = _frame(rows)
    if df.empty:
        return df
    st = df.groupby(["race", "candidate", "party"], as_index=False)[["votes", "reporting", "units"]].sum()
    st = st.assign(level="state", unit_id="us-fl", name="Florida", early=np.nan, ballots=np.nan, registered=np.nan,
                   outstanding=np.nan)
    return _frame(df.to_dict("records") + st.to_dict("records"))


def parse_feed(kind: str, blobs: dict[str, bytes | list[bytes] | None]) -> pd.DataFrame:
    """Rows for one feed from its latest snapshots. blobs keys as in FEEDS[kind]; '*' keys give a list."""
    match kind:
        case "ca":
            return parse_ca(blobs["results"], blobs.get("status")) if blobs.get("results") else _frame([])
        case "tx":
            return parse_tx(blobs.get("election"), blobs["county"]) if blobs.get("county") else _frame([])
        case "oh":
            return parse_oh(blobs["xml"], blobs.get("csv")) if blobs.get("xml") else _frame([])
        case "ak":
            return parse_ak(blobs["js"]) if blobs.get("js") else _frame([])
        case "ga":
            parts = [parse_ga(b) for b in blobs.get("item") or []]
            return pd.concat(parts, ignore_index=True) if parts else _frame([])
        case "az":
            parts = [parse_ga(b, "az") for b in blobs.get("item") or []]
            return pd.concat(parts, ignore_index=True) if parts else _frame([])
        case "sc":
            return parse_clarity(blobs["all"], blobs["summary"], blobs.get("status")) \
                if blobs.get("all") and blobs.get("summary") else _frame([])
        case "vt":
            return parse_vt(blobs["main"], blobs["statewide"]) if blobs.get("main") and blobs.get("statewide") \
                else _frame([])
        case "nc":
            return parse_nc(blobs["county"], blobs.get("results") or []) if blobs.get("county") else _frame([])
        case "fl":
            return parse_fl(blobs["enight"]) if blobs.get("enight") else _frame([])
    if kind in _extra.FEEDS:                         # PA, MN, NE, NM: brunder.us_feeds_extra
        return _extra.parse_feed(kind, blobs)
    if kind in _civic.FEEDS:                         # civicAPI (secondary source): brunder.civic
        return _civic.parse_feed(kind, blobs)
    if kind.endswith("_house"):                      # House districts (brunder.us_house_night); other kinds unchanged
        from .us_house_night import parse_house
        return parse_house(kind.removesuffix("_house"), blobs)
    raise ValueError(kind)


# ------------------------------------------------------------------ snapshot archive

def _check(body: bytes) -> None:
    """Raise if a snapshot body is not a complete document (JSON, XML, or UTF-8 text such as the OH CSV)."""
    head = body.lstrip()[:1]
    if not head:
        raise ValueError("empty snapshot")
    if head in (b"{", b"["):
        json.loads(body)
    elif head == b"<":
        ET.fromstring(body)
    else:
        body.decode("utf-8")


class Archive:
    """The recorder's output directory: <root>/<key>/<YYYYmmddTHHMMSS.ffffffZ>.<ext>[.gz] (+ index.jsonl;
    dot-directories such as the recorder's .tmp are ignored). Reads only what was recorded; never contacts the
    feeds. A snapshot that cannot be decompressed or is not a complete document is never cached and is skipped in
    favour of the previous valid snapshot of the same key."""

    CACHE = 32

    def __init__(self, root: Path):
        self.root = Path(root)
        self._files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        self._cache: dict[Path, bytes] = {}
        self.bad: dict[Path, int] = {}               # malformed snapshot -> its size when it failed

    def refresh(self) -> None:
        files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        if not self.root.exists():
            self._files = files
            return
        for d in self.root.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                lst = []
                for f in d.iterdir():
                    m = re.match(r"(\d{8}T\d{6}\.\d{6})Z", f.name)
                    if m:
                        lst.append((pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S.%f"), tz="UTC"), f))
                files[d.name] = sorted(lst)
        self._files = files

    def keys(self, pattern: str) -> list[str]:
        if pattern.endswith("*"):
            return sorted(k for k in self._files if k.startswith(pattern[:-1]))
        return [pattern] if pattern in self._files else []

    def times(self) -> list[pd.Timestamp]:
        return sorted({t for lst in self._files.values() for t, _ in lst})

    def at(self, key: str, t: pd.Timestamp) -> Path | None:
        """Latest valid snapshot of `key` at or before t (malformed ones are skipped)."""
        lst = self._files.get(key, [])
        i = bisect.bisect_right(lst, t, key=lambda x: x[0])
        while i:
            f = lst[i - 1][1]
            if self._load(f) is not None:
                return f
            i -= 1
        return None

    def _load(self, f: Path) -> bytes | None:
        if f in self._cache:
            return self._cache[f]
        size = -1
        try:
            size = f.stat().st_size
            if self.bad.get(f) == size:              # known bad and unchanged since
                return None
            b = f.read_bytes()
            b = gzip.decompress(b) if f.suffix == ".gz" else b
            _extra.CHECKS.get(f.parent.name, _check)(b)   # PA / MN keys: their own completeness check
        except Exception as e:                       # partial write, bad gzip, truncated JSON/XML
            if f not in self.bad:
                print(f"snapshot skipped: {f.parent.name}/{f.name}: {type(e).__name__}: {e}", flush=True)
            self.bad[f] = size
            return None
        self.bad.pop(f, None)
        if len(self._cache) >= self.CACHE:           # TX files are ~5 MB and change every poll
            self._cache.pop(next(iter(self._cache)))
        self._cache[f] = b
        return b

    def read(self, f: Path) -> bytes:
        """Bytes of a snapshot returned by at() (gunzipped, validated)."""
        b = self._load(f)
        if b is None:
            raise ValueError(f"malformed snapshot {f}")
        return b


def feed_files(arc: Archive, kind: str, t: pd.Timestamp) -> dict[str, Path | list[Path] | None]:
    """Latest valid snapshot ≤ t of every key of a feed (a list for '*' keys)."""
    out: dict[str, Path | list[Path] | None] = {}
    for role, pattern in FEEDS[kind].items():
        files = [f for f in (arc.at(k, t) for k in arc.keys(pattern)) if f is not None]
        out[role] = files if pattern.endswith("*") else (files[0] if files else None)
    return out


def feed_blobs(arc: Archive, files: dict[str, Path | list[Path] | None]) -> dict:
    return {role: [arc.read(x) for x in f] if isinstance(f, list) else (arc.read(f) if f else None)
            for role, f in files.items()}


def pm_prices(arc: Archive, f: Path | None) -> dict[str, float]:
    """YES price (mid if spread ≤ 10c, else last) per 'event/market' from one polymarket_books snapshot."""
    if f is None:
        return {}
    out = {}
    for k, book in json.loads(arc.read(f)).items():
        p = live.top(book)["price"]
        if np.isfinite(p):
            out[k] = float(p)
    return out


# ------------------------------------------------------------------ priors (official certified county results)

def _fetch(url: str, dest: Path) -> bytes:
    """Download once into data/raw/us (cached); polite single request."""
    if dest.exists():
        return dest.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
    dest.write_bytes(r.content)
    return r.content


def _xlsx_rows(data: bytes) -> list[list[str | None]]:
    """First worksheet of an .xlsx as rows of cell strings (no openpyxl dependency)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{ns}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    out = []
    for r in ET.fromstring(z.read(sheet)).iter(f"{ns}row"):
        row: dict[int, str | None] = {}
        for c in r.findall(f"{ns}c"):
            col = 0
            for ch in re.match(r"[A-Z]+", c.get("r")).group():
                col = col * 26 + ord(ch) - 64
            v = c.find(f"{ns}v")
            if v is None:
                isel = c.find(f"{ns}is")
                val = "".join(x.text or "" for x in isel.iter(f"{ns}t")) if isel is not None else None
            else:
                val = shared[int(v.text)] if c.get("t") == "s" else v.text
            row[col - 1] = val
        out.append([row.get(i) for i in range(max(row) + 1)] if row else [])
    return out


def _two_party(rows: pd.DataFrame) -> pd.DataFrame:
    """unit_id, dem, rep, total (all candidates) from parser-style rows (one race, unit level)."""
    u = rows[rows["level"] != "state"]
    g = u.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"dem": g.get("DEM", 0), "rep": g.get("REP", 0), "total": g.sum(axis=1)}).reset_index()


def _ca_sov(year: int, race: str) -> pd.DataFrame:
    """CA SoS Statement of Vote, county × candidate long file (csv-candidates / csv-all-candidates .xlsx)."""
    name = {2022: "csv-candidates.xlsx", 2024: "csv-all-candidates.xlsx"}[year]
    data = _fetch(f"https://elections.cdn.sos.ca.gov/sov/{year}-general/sov/{name}", US_RAW / "ca" / f"{year}_{name}")
    rows = _xlsx_rows(data)
    head = [str(h).strip() if h else "" for h in rows[0]]
    df = pd.DataFrame([r + [None] * (len(head) - len(r)) for r in rows[1:]], columns=head).fillna("").astype(str)
    df = df[df["Contest Name"].map(lambda s: isinstance(s, str) and race_key(s) == race)]
    ids = dict(zip(counties("ca")["key"], counties("ca")["unit_id"]))
    df["unit_id"] = df["County Name"].map(lambda s: ids.get(_norm(s or "")))
    df["party"] = df["Party Name"].map(party_code)
    df["votes"] = df["Vote Total"].map(_int)
    df["level"] = "county"
    return _two_party(df.dropna(subset=["unit_id"]))


def _tx_tlc(election: int, office: int) -> pd.DataFrame:
    """Texas Legislative Council, Capitol Data Portal (CC BY): VTD-level returns. CNTYVTD[:3] = county FIPS;
    candidate columns '<Name><party letter>_<yy>G_<Office>'."""
    data = _fetch(f"https://ted.capitol.texas.gov/api/Offices/{election}/{office}/vtd",
                  US_RAW / "tx" / f"tlc_{election}_{office}_vtd.csv")
    df = pd.read_csv(io.BytesIO(data), dtype={"CNTYVTD": str})
    cand = [c for c in df.columns if c not in ("CNTYVTD", "VTDKEY")]
    g = df.assign(cty=df["CNTYVTD"].str[:3]).groupby("cty")[cand].sum()
    party = {c: c.split("_")[0][-1] for c in cand}
    return pd.DataFrame({"unit_id": "us-tx-48" + g.index.to_series(),
                         "dem": g[[k for k, p in party.items() if p == "D"]].sum(axis=1),
                         "rep": g[[k for k, p in party.items() if p == "R"]].sum(axis=1),
                         "total": g.sum(axis=1)}).reset_index(drop=True)


def _ga_enr(eid: str, race: str) -> pd.DataFrame:
    """GA SoS Enhanced Voting certified results: find the ballot item, fetch its county breakdown."""
    api = "https://results.sos.ga.gov/results/public/api/elections/Georgia"
    summary = json.loads(_fetch(f"{api}/{eid}/data", US_RAW / "ga" / f"{eid}_data.json"))
    item = next(b["id"] for b in summary["ballotItems"] if race_key(b["name"][0]["text"]) == race)
    data = _fetch(f"{api}/{eid}/data/ballot-item/{item}", US_RAW / "ga" / f"{eid}_{race}.json")
    return _two_party(parse_ga(data))


def _ak_primary(race: str) -> pd.DataFrame:
    """AK 2026 primary (certified 2026-08-31), by house district, from the same official ENR file the night feed
    uses. Pick-one top-four primary open to all voters: dem/rep = sums over candidates labelled DEM / REP."""
    dest = US_RAW / "ak" / "enr26_primary_statewide.js"
    data = _fetch("https://www.elections.alaska.gov/enr26/results/statewide.js", dest)
    if "Primary" not in json.loads(data)["name"]:
        dest.unlink()
        raise RuntimeError("enr26/statewide.js no longer holds the 2026 primary; restore the saved copy "
                           "(tests/fixtures/us/ak_statewide.js.gz is the same certified file)")
    rows = parse_ak(data)
    return _two_party(rows[rows["race"] == race])


MEDSL24 = "https://raw.githubusercontent.com/MEDSL/2024-elections-official/main"
MEDSL24_CITE = ("MIT Election Data and Science Lab, U.S. President / U.S. Senate Precinct-Level Returns 2024 "
                "(doi:10.7910/DVN/XDJYKC, doi:10.7910/DVN/ZCM3BN; CC0 1.0), github.com/MEDSL/2024-elections-official")
MEDSL24_STATES = ("ak al ar az ca co ct de fl ga hi ia id il in ks ky la ma md me mi mn mo ms mt nc nd ne nh nj nm "
                  "nv ny oh ok or pa ri sc sd tn tx ut va vt wa wi wv wy").split()


_NOT_A_CANDIDATE = re.compile(r"^(CONTEST TOTAL|TOTAL VOTES?|TOTAL BALLOTS|BALLOTS CAST|REGISTERED VOTERS|"
                               r"UNDER ?VOTES?|OVER ?VOTES?|BLANK( VOTES?)?|SPOILED|TIMES (CAST|BLANK(ED)?|OVER ?VOTED))$")
_CT_REGIONS = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_gaz_cousubs_09.txt"
_FIPS_RENAMED = {"46113": "46102"}                   # SD: Shannon County renamed Oglala Lakota (2015)


def _ct_town_region() -> dict[str, str]:
    """CT town (county-subdivision code) -> planning-region county FIPS (Census 2023 gazetteer, public domain); the
    dashboard geometry uses the planning regions, MEDSL the former counties."""
    t = pd.read_csv(io.BytesIO(_fetch(_CT_REGIONS, US_RAW / "medsl24" / "ct_cousubs_2023.txt")), sep="\t", dtype=str)
    return {g[-5:]: g[:5] for g in t["GEOID"].str.strip()}


def _medsl_county(df: pd.DataFrame, st: str, rule: str = "auto") -> pd.DataFrame:
    """unit_id, dem, rep, total from MEDSL rows (county_fips, party_simplified, mode, votes[, candidate,
    jurisdiction_fips]). Rows that are not candidates (CONTEST TOTAL, under/over votes, …) are dropped. States report
    by vote mode; a county's totals are its TOTAL-mode candidate rows when those carry Democratic or Republican
    votes, else the sum of its other modes (MEDSL README warning; NJ mixes both). CT is mapped from towns to the
    2022 planning regions; renamed counties are mapped to their current code."""
    if "candidate" in df:
        df = df[~df["candidate"].fillna("").str.strip().str.upper().str.match(_NOT_A_CANDIDATE)]
    fips = df["county_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5).replace(_FIPS_RENAMED)
    if st == "ct" and "jurisdiction_fips" in df:
        fips = df["jurisdiction_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str[-5:].map(_ct_town_region())
    df = df.assign(votes=pd.to_numeric(df["votes"], errors="coerce").fillna(0.0), fips=fips).dropna(subset=["fips"])
    if "candidate" in df:                            # OR: some counties leave party_simplified empty; take the party
        known = df.dropna(subset=["party_simplified"])  # the same candidate has elsewhere in the file
        cand_party = known.groupby("candidate")["party_simplified"].agg(lambda s: s.mode().iat[0])
        df = df.assign(party_simplified=df["party_simplified"].fillna(df["candidate"].map(cand_party)))
    party = df["party_simplified"].astype(str).str.upper()
    is_total = df["mode"].astype(str).str.upper() == "TOTAL"
    rule = rule if rule != "auto" else ("overlap" if "precinct" in df else "total_first")
    if "precinct" in df:
        keep = ~df["precinct"].fillna("").str.upper().str.contains("COUNTY TOTAL")   # county-total pseudo-precincts
        df, is_total, party = df[keep], is_total[keep], party[keep]
    if rule == "overlap":
        # where TOTAL rows and mode rows describe the same precincts they duplicate each other (keep the modes);
        # otherwise they are disjoint (e.g. NJ vote-by-mail pseudo-precincts beside precinct TOTALs) and summed
        prec = df["precinct"].fillna("")
        dup = {f for f, g in df.groupby("fips") if
               (set(prec[g.index][is_total[g.index]]) & set(prec[g.index][~is_total[g.index]]))}
        df = df[~(df["fips"].isin(dup) & is_total)]
    elif rule == "total_first":
        major = (is_total & party.isin(["DEMOCRAT", "REPUBLICAN"]) & (df["votes"] > 0))
        use_total = major.groupby(df["fips"]).transform("any").astype(bool)
        df = df[(use_total & is_total) | (~use_total & ~is_total)]
    party = df["party_simplified"].astype(str).str.upper()
    g = pd.DataFrame({"dem": df["votes"].where(party == "DEMOCRAT", 0.0), "rep": df["votes"].where(party == "REPUBLICAN", 0.0),
                      "total": df["votes"], "fips": df["fips"]}).groupby("fips").sum()
    g.index = f"us-{st}-" + g.index
    return g.rename_axis("unit_id").reset_index()


def _medsl24_state_ref() -> pd.DataFrame:
    """Official 2024 president statewide totals (MEDSL 2024-president-state.csv): state, dem, rep."""
    s = pd.read_csv(io.BytesIO(_fetch(f"{MEDSL24}/2024-president-state.csv", US_RAW / "medsl24" / "president_state.csv")))
    tot = s["mode"].fillna("").str.upper() == "TOTAL"
    s = s[tot | ~tot.groupby(s["state_po"]).transform("any").astype(bool)]   # TOTAL rows where a state has them
    p = s.pivot_table(index="state_po", columns="party_simplified", values="votes", aggfunc="sum")
    return pd.DataFrame({"dem": p["DEMOCRAT"], "rep": p["REPUBLICAN"]}).rename(index=str.lower)


def medsl24_pres(st: str) -> pd.DataFrame:
    """2024 president by county from MEDSL's precinct file of the state (cached aggregate in data/processed). The
    vote-mode rule (overlap / total_first / all, see _medsl_county) is the one whose state sums are closest to the
    official statewide totals; the chosen rule and its deviation are kept in pres_<st>.json."""
    out = US_PROC / "medsl24" / f"pres_{st}.csv"
    if out.exists():
        return pd.read_csv(out)
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"{MEDSL24}/individual_states/{st}24.zip", US_RAW / "medsl24" / f"{st}24.zip")))
    parts = []
    for ch in pd.read_csv(z.open(z.namelist()[0]), dtype=str, chunksize=500_000,
                          usecols=["office", "stage", "mode", "votes", "county_fips", "party_simplified", "candidate",
                                   "precinct", "jurisdiction_fips"]):
        parts.append(ch[(ch["office"] == "US PRESIDENT") & (ch["stage"].fillna("GEN") == "GEN")])
    df = pd.concat(parts, ignore_index=True)
    # party by candidate, not ballot line: NY fusion lines (Working Families, Conservative) and files with an empty or
    # NONPARTISAN party_simplified (NE, parts of OR) all count for Harris / Trump
    cand = df["candidate"].astype(str).str.upper()
    df["party_simplified"] = np.where(cand.str.contains("HARRIS"), "DEMOCRAT",
                                      np.where(cand.str.contains("TRUMP"), "REPUBLICAN", "OTHER"))
    ref = _medsl24_state_ref().loc[st]
    best = None
    for rule in ("overlap", "total_first", "all"):
        res = _medsl_county(df, st, rule)
        dev = max(abs(res["dem"].sum() / ref["dem"] - 1), abs(res["rep"].sum() / ref["rep"] - 1))
        if best is None or dev < best[2] - 1e-9:
            best = (rule, res, dev)
    rule, res, dev = best
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False)
    out.with_suffix(".json").write_text(json.dumps({"rule": rule, "max_abs_dev_vs_state": round(float(dev), 5)}))
    return res


def medsl24_senate() -> pd.DataFrame:
    """2024 regular US Senate by county, all states (MEDSL 2024-senate-county.csv): state, unit_id, dem, rep, total."""
    d = pd.read_csv(io.BytesIO(_fetch(f"{MEDSL24}/2024-senate-county.csv", US_RAW / "medsl24" / "senate_county.csv")),
                    dtype=str)
    d = d[(d["stage"] == "GEN") & (d["special"].str.upper() == "FALSE")]
    return pd.concat([_medsl_county(g, st.lower()).assign(state=st.lower()) for st, g in d.groupby("state_po")],
                     ignore_index=True)


def _medsl_pres(st: str, year: int = 2024) -> pd.DataFrame:
    """Prior source <st>_pres_2024 from MEDSL's 2024 precinct returns (see medsl24_pres)."""
    if year != 2024:
        raise ValueError("MEDSL GitHub precinct returns: 2024 only")
    return medsl24_pres(st)


def _sc_clarity(eid: int, race: str) -> pd.DataFrame:
    """SC State Election Commission certified results (Clarity ENR, enr-scvotes.org/SC/<eid>): county ALL.json +
    en/summary.json at the election's current version."""
    base = "https://www.enr-scvotes.org/SC"
    ver = _fetch(f"{base}/{eid}/current_ver.txt", US_RAW / "sc" / f"{eid}_current_ver.txt").decode().strip()
    allj = _fetch(f"{base}/{eid}/{ver}/json/ALL.json", US_RAW / "sc" / f"{eid}_{ver}_ALL.json")
    summ = _fetch(f"{base}/{eid}/{ver}/json/en/summary.json", US_RAW / "sc" / f"{eid}_{ver}_summary.json")
    rows = parse_clarity(allj, summ)
    return _two_party(rows[rows["race"] == race])


def _vt_static(guid: str, race: str) -> pd.DataFrame:
    """Vermont SoS certified results (static.electionresults.vermont.gov), towns aggregated to counties."""
    base = "https://static.electionresults.vermont.gov"
    main = _fetch(f"{base}/elections/{guid}.json", US_RAW / "vt" / f"{guid}.json")
    path = json.loads(main)["stateWide"]["path"].replace("\\", "/")
    sw = _fetch(f"{base}/{path}", US_RAW / "vt" / Path(path).name)
    rows = parse_vt(main, sw)
    return _two_party(rows[rows["race"] == race])


def _ncsbe_pct(date: str, contest: str) -> pd.DataFrame:
    """NC State Board of Elections certified precinct results (dl.ncsbe.gov ENRS/<date>/results_pct_<date>.zip),
    summed to counties for one contest."""
    d = f"{date[:4]}_{date[4:6]}_{date[6:]}"
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{d}/results_pct_{date}.zip",
                                          US_RAW / "nc" / f"results_pct_{date}.zip")))
    r = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str)
    r = r[r["Contest Name"].str.strip() == contest]
    ids = _ids("nc")
    rows = pd.DataFrame({"level": "county", "unit_id": r["County"].map(lambda n: ids[_norm(n)][0]),
                         "party": r["Choice Party"].fillna("").str.strip().map(party_code),
                         "votes": pd.to_numeric(r["Total Votes"], errors="coerce").fillna(0)})
    return _two_party(rows)


def _fl_official(date: str, race: str) -> pd.DataFrame:
    """Florida Division of Elections official results extract (results.elections.myflorida.com: Download, official
    results; one POST, cached), county two-party votes for one race. date as M/D/YYYY."""
    m, d, y = date.split("/")
    dest = US_RAW / "fl" / f"results_{y}{int(m):02d}{int(d):02d}_official.txt"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as c:
            r = c.post("https://results.elections.myflorida.com/ResultsExtract.Asp",
                       data={"ElectionDate": date, "OfficialResults": "Y", "PartyRaces": "N", "DataMode": "",
                             "FormsButton2": "Download"})
            r.raise_for_status()
        dest.write_bytes(r.content)
    rows = parse_fl(dest.read_bytes())
    return _two_party(rows[rows["race"] == race])


PRIOR_SOURCES = {
    "ca_gov_2022": lambda: _ca_sov(2022, "gov"),
    "ca_pres_2024": lambda: _ca_sov(2024, "pres"),
    "tx_pres_2024": lambda: _tx_tlc(510, 1),
    "tx_sen_2024": lambda: _tx_tlc(510, 2),
    "tx_gov_2022": lambda: _tx_tlc(497, 37),
    "ga_pres_2024": lambda: _ga_enr("2024NovGen", "pres"),
    "ga_gov_2022": lambda: _ga_enr("2022NovGen", "gov"),
    "ga_sen_2022": lambda: _ga_enr("2022NovGen", "sen"),
    "ga_pres_2020": lambda: _ga_enr("2020NovGen", "pres"),
    "ak_sen_p2026": lambda: _ak_primary("sen"),
    "ak_gov_p2026": lambda: _ak_primary("gov"),
    "oh_pres_2024": lambda: _medsl_pres("oh"),
    "sc_pres_2024": lambda: _sc_clarity(122436, "pres"),
    "sc_gov_2022": lambda: _sc_clarity(115412, "gov"),
    "sc_sen_2022": lambda: _sc_clarity(115412, "sen"),
    "vt_gov_2024": lambda: _vt_static("67a35c79-05a2-4ae3-a77d-97812256188b", "gov"),
    "nc_sen_2022": lambda: _ncsbe_pct("20221108", "US SENATE"),
    "nc_pres_2024": lambda: _medsl_pres("nc"),
    "fl_sen_2022": lambda: _fl_official("11/8/2022", "sen"),
    "fl_gov_2022": lambda: _fl_official("11/8/2022", "gov"),
}
PRIOR_SOURCES.update(_extra.PRIOR_SOURCES)       # pa_*, mn_*, ne_*, nm_*, az_gov_2022 (brunder.us_feeds_extra)
SOURCE_INFO = {
    "ca": ("California Secretary of State, Statement of Vote", "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results", "public record"),
    "tx": ("Texas Legislative Council, Capitol Data Portal", "https://data.capitol.texas.gov/", "CC BY"),
    "ga": ("Georgia Secretary of State, election results", "https://results.sos.ga.gov/results/public/Georgia", "public record"),
    "ak": ("Alaska Division of Elections, election results", "https://www.elections.alaska.gov/", "public record"),
    "oh": ("MIT Election Data and Science Lab, U.S. President Precinct-Level Returns 2024 (summed to counties)", "https://github.com/MEDSL/2024-elections-official", "CC0 1.0"),
    "sc": ("South Carolina Election Commission, election results (enr-scvotes.org)", "https://scvotes.gov/elections-statistics/election-results/", "public record"),
    "vt": ("Vermont Secretary of State, election results", "https://electionresults.vermont.gov/", "public record"),
    "nc": ("NC State Board of Elections, election results", "https://www.ncsbe.gov/results-data", "public record"),
    "fl": ("Florida Division of Elections, election results", "https://results.elections.myflorida.com/", "public record"),
    "az": ("Arizona Secretary of State, election results (Enhanced Voting)", "https://app.enhancedvoting.com/results/public/Arizona", "public record"),
}
SOURCE_INFO.update(_extra.SOURCE_INFO)
SOURCE_INFO.update(_civic.SOURCE_INFO)


def build_priors(sources: list[str] | None = None) -> Path:
    """Fetch (cached under data/raw/us) and build data/processed/us2026/priors.csv: source, unit_id, dem, rep, total.
    Sources that cannot be built are reported and skipped."""
    parts = []
    for s in sources or list(PRIOR_SOURCES):
        try:
            parts.append(PRIOR_SOURCES[s]().assign(source=s))
            print(f"prior {s}: {len(parts[-1])} units", flush=True)
        except Exception as e:                       # one missing source must not block the others
            print(f"prior {s}: skipped ({type(e).__name__}: {e})", flush=True)
    US_PROC.mkdir(parents=True, exist_ok=True)
    out = US_PROC / "priors.csv"
    old = pd.read_csv(out) if out.exists() else pd.DataFrame(columns=["source"])
    new = pd.concat(parts, ignore_index=True)[["source", "unit_id", "dem", "rep", "total"]] if parts else old.iloc[:0]
    pd.concat([old[~old["source"].isin(new["source"].unique())], new], ignore_index=True).to_csv(out, index=False)
    return out


PUBLIC_PRIORS = ROOT_DIR / "data_public" / "us2026_priors.csv"
HOUSE_PRIORS = ROOT_DIR / "data_public" / "us2026_house_priors.csv"
CIVIC_PRIORS = ROOT_DIR / "data_public" / "us2026_civic_priors.csv"


def load_priors(path: Path | None = None, processed: Path | None = None, public: Path | None = None) -> pd.DataFrame:
    """County priors: the committed data_public/us2026_priors.csv (the registered input) plus any source that only a
    local build (data/processed/us2026/priors.csv, `brunder us priors`) has. A source in both whose rows differ is
    taken from the public file, with a warning: a stale local build must never drop or change a registered prior.
    `path`: read exactly that file instead."""
    if path is not None:
        return pd.read_csv(path, comment="#")
    pub = pd.read_csv(public or PUBLIC_PRIORS, comment="#")
    if public is None and HOUSE_PRIORS.exists():    # House district priors (sources "<st>-<nn>_pres_2024"), own file
        pub = pd.concat([pub, pd.read_csv(HOUSE_PRIORS, comment="#")], ignore_index=True)
    if public is None and CIVIC_PRIORS.exists():    # 2024 president by county of the civicAPI-only states, own file
        pub = pd.concat([pub, pd.read_csv(CIVIC_PRIORS, comment="#")], ignore_index=True)
    proc_path = processed or US_PROC / "priors.csv"
    if not proc_path.exists():
        return pub
    proc = pd.read_csv(proc_path, comment="#")

    def same(s: str) -> bool:
        a, b = (d[d["source"] == s].sort_values("unit_id") for d in (proc, pub))
        return (len(a) == len(b) and (a["unit_id"].to_numpy() == b["unit_id"].to_numpy()).all()
                and np.allclose(a[["dem", "rep", "total"]].to_numpy(float), b[["dem", "rep", "total"]].to_numpy(float)))
    differ = [s for s in sorted(set(proc["source"]) & set(pub["source"])) if not same(s)]
    if differ:
        print(f"priors: {proc_path} differs from {public or PUBLIC_PRIORS} for {', '.join(differ)}; "
              "using the public (registered) rows", flush=True)
    extra = proc[~proc["source"].isin(set(pub["source"]))]
    return pd.concat([pub, extra[[c for c in pub.columns if c in extra.columns]]], ignore_index=True)


def write_public_priors(priors: pd.DataFrame | None = None, dest: Path = PUBLIC_PRIORS) -> Path:
    """The derived county priors (two-party votes by county per source race) as a public file with an attribution
    header. Only official public-record or CC BY sources; the MEDSL Ohio file (CC0) is included when built."""
    pr = (priors if priors is not None else pd.read_csv(US_PROC / "priors.csv"))[["source", "unit_id", "dem", "rep", "total"]]
    head = [
        "# US midterms 2026 county priors: certified results by county (Alaska: state house district), derived by",
        "# brunder.us.build_priors (src/brunder/us.py). Columns: source race, unit_id (us-<st>-<county FIPS>), Democratic",
        "# votes, Republican votes, all candidates' votes.",
        "# Sources: ca_* California Secretary of State, Statement of Vote (public record);",
        "#   tx_* Texas Legislative Council, Capitol Data Portal, https://data.capitol.texas.gov/ (CC BY; VTD returns",
        "#   summed to counties by this project); ga_* Georgia Secretary of State, results.sos.ga.gov (public record);",
        "#   ak_* Alaska Division of Elections (public record); oh_* MIT Election Data and Science Lab, U.S. President",
        "#   Precinct-Level Returns 2024, doi:10.7910/DVN/XDJYKC (CC0), summed to counties; sc_* South Carolina Election",
        "#   Commission certified results, enr-scvotes.org (public record); vt_* Vermont Secretary of State certified",
        "#   results, electionresults.vermont.gov, towns summed to counties by this project (public record);",
        "#   nc_* NC State Board of Elections; fl_* Florida Division of Elections; pa_* Pennsylvania Department of State",
        "#   (electionreturns.pa.gov); mn_* Minnesota Secretary of State results files; nm_* New Mexico Secretary of",
        "#   State (public record); ne_*, az_* and nc_pres_2024 MIT Election Data and Science Lab 2022/2024 returns (CC0),",
        "#   summed to counties (ne_sen_2024: Osborn, independent, in the dem column).",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text("\n".join(head) + "\n" + pr.to_csv(index=False))
    os.replace(tmp, dest)
    return dest


# ------------------------------------------------------------------ config + model

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


_VERSION = re.compile(r"_v(\d+)$")


def config_version(c: dict) -> int:
    m = _VERSION.search(str(c.get("id", "")))
    return int(m.group(1)) if m else 1


def _sibling_versions(c: dict) -> dict[str, Path]:
    """Later versions of a v1 config next to it (…_v2.toml, …_v3.toml), in version order."""
    p = Path(c["_path"])
    found = {int(m.group(1)): q for q in p.parent.glob(p.name.replace("_v1.toml", "_v*.toml"))
             if (m := re.search(r"_v(\d+)\.toml$", q.name)) and int(m.group(1)) > 1}
    return {f"v{n}": found[n] for n in sorted(found)}


def load_races(cfg_paths: list[Path]) -> tuple[list[dict], dict[str, dict[str, dict]]]:
    """(race configs = the v1 configs, later model versions per race_id {"v2": cfg, "v3": cfg, …}). Later versions
    passed explicitly are attached to their race, not run as races of their own; a v1 config's sibling *_vN.toml
    files are attached too. Every version runs (each registered config is scored)."""
    loaded = [load_config(Path(p)) for p in cfg_paths]
    cfgs = [c for c in loaded if config_version(c) == 1]
    versions: dict[str, dict[str, dict]] = {}
    for c in loaded:
        if config_version(c) > 1:
            versions.setdefault(c["election"]["race_id"], {})[f"v{config_version(c)}"] = c
    for c in cfgs:
        have = versions.setdefault(c["election"]["race_id"], {})
        for v, q in _sibling_versions(c).items():
            have.setdefault(v, load_config(q))
    return cfgs, {rid: dict(sorted(vs.items(), key=lambda kv: int(kv[0][1:]))) for rid, vs in versions.items() if vs}


def poll_model(versions: dict[str, dict]) -> str | None:
    """The race's primary poll model: the latest version with a swing prior that is not marked role = "secondary"
    (e.g. Ohio: v4; its v2 has no prior and v5 is secondary)."""
    act = [v for v, c in versions.items() if c.get("model", {}).get("swing_prior_sd") is not None
           and c.get("role") != "secondary"]
    return act[-1] if act else None


_EXPECTED: dict[str, pd.Series] = {}


def _expected_votes(path: str) -> pd.Series:
    """unit_id -> expected two-party votes from a repo-relative CSV (cached)."""
    if path not in _EXPECTED:
        d = pd.read_csv(ROOT_DIR / path, comment="#")
        _EXPECTED[path] = d.set_index("unit_id")["exp_votes"].astype(float)
    return _EXPECTED[path]


_HISP: dict[str, pd.Series] = {}


def _hisp_share(path: str) -> pd.Series:
    """unit_id -> Hispanic share of the population (0-1) from a repo-relative CSV (cached)."""
    if path not in _HISP:
        d = pd.read_csv(ROOT_DIR / path, comment="#")
        _HISP[path] = d.set_index("unit_id")["hisp"].astype(float)
    return _HISP[path]


@dataclass(frozen=True)
class CountRules:
    """How much of a county's vote is in (f), given what US feeds report. See docs/us_midterms.md."""
    turnout_ratio: float = 1.0         # expected 2026 two-party votes = ratio × prior-race two-party votes
    f_max: float = 0.95                # cap on f from vote counts alone (turnout above expectation)
    f_complete: float = 0.0            # f assumed once every precinct/polling place reported (0 = ignore flag)
    use_outstanding: bool = False      # OH: remaining = reported outstanding ballots once precincts are in
    learn_turnout: bool = False        # re-scale E of unfinished counties by complete counties' turnout
    turnout_prior_votes: float = 200_000.0   # pseudo-votes holding the learned ratio at 1
    vote_type: bool = False            # split counties into early+mail / rest units (feeds reporting early votes)
    early_share: float = 0.6           # expected early+mail share of a county's vote before it posts any
    # Model v4: expected two-party votes per county from a file (unit_id, exp_votes; repo-relative path) instead of
    # turnout_ratio × the prior race's votes; counties missing from the file keep turnout_ratio × V. Empty = off.
    expected_votes: str = ""
    # "prior": the file only redistributes the statewide expected total of turnout_ratio × V across counties;
    # "file": the file's own totals (statewide level from the previous midterm's drop-off).
    expected_votes_level: str = "prior"
    # Model v5/v6 (nowcast.Params.hisp_slope / hisp_blend): county Hispanic share from a repo-relative CSV (unit_id,
    # hisp; data_public/us2026_hispanic_share.csv, Census ACS 2020-24 B03003). Counties missing from it get the
    # state's expected-vote weighted mean (no slope effect). Only read when the model's slope is on.
    hisp_share: str = ""


def rules(cfg: dict) -> CountRules:
    C = cfg.get("count", {})
    return CountRules(**{f.name: C[f.name] for f in fields(CountRules) if f.name in C})


def params(cfg: dict) -> nowcast.Params:
    M = cfg.get("model", {})
    keys = ("kappa", "rho", "tau", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf", "swing_prior_mean", "swing_prior_sd",
            "count_scaled_terms", "count_scale_mode", "hisp_slope", "hisp_slope_tau", "hisp_blend")
    return nowcast.Params(**{k: M[k] for k in keys if k in M})


def race_prior(cfg: dict, priors: pd.DataFrame) -> pd.DataFrame | None:
    """unit_id, name, s0 (two-party Dem share of the share race), V (two-party votes of the votes race).
    None when a source is missing."""
    P, st = cfg["prior"], cfg["election"]["state"]
    have = set(priors["source"])
    if P["share"] not in have or P["votes"] not in have:
        return None
    sh = priors[priors["source"] == P["share"]].set_index("unit_id")
    vo = priors[priors["source"] == P["votes"]].set_index("unit_id")
    c = counties(st).set_index("unit_id")
    out = pd.DataFrame({"name": c["name"],
                        "s0": sh["dem"] / (sh["dem"] + sh["rep"]),
                        "V": (vo["dem"] + vo["rep"])}).reindex(c.index)
    return out.dropna().reset_index()


def baseline_share(pri: pd.DataFrame) -> float:
    """The prior's statewide two-party Dem share, weighted like the model's expected votes (V)."""
    return float(np.average(pri["s0"], weights=pri["V"]))


def count_state(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> pd.DataFrame:
    """Per unit: n (two-party votes counted), a (Dem votes), ne/ae (of which early + mail, when the feed says),
    f (share of expected two-party vote in), E (expected two-party votes), complete flag. Units absent from the
    feed are uncounted."""
    E_, R = cfg["election"], rules(cfg)
    u = rows[(rows["level"] != "state") & (rows["race"] == E_["race"])]
    pa, pb = E_["a"]["party"], E_["b"]["party"]
    cols = ["a", "b", "ea", "eb", "reporting", "units", "ballots", "outstanding"]
    if len(u):
        g = u.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
        e = u.assign(early=u["early"].fillna(0)).pivot_table(index="unit_id", columns="party", values="early",
                                                             aggfunc="sum", fill_value=0)
        prog = u.groupby("unit_id")[["reporting", "units", "ballots", "outstanding"]].first()
        st = pd.DataFrame({"a": g.get(pa, 0), "b": g.get(pb, 0), "ea": e.get(pa, 0), "eb": e.get(pb, 0)}).join(prog)
    else:
        st = pd.DataFrame(columns=cols)
    df = pri.set_index("unit_id").join(st).reset_index()
    df[["a", "b", "ea", "eb"]] = df[["a", "b", "ea", "eb"]].astype(float).fillna(0.0)
    df["n"] = df["a"] + df["b"]
    E0 = df["V"] * R.turnout_ratio
    if R.expected_votes:
        ev = df["unit_id"].map(_expected_votes(R.expected_votes))
        m = ev.notna() & (ev > 0)
        if m.any():
            scale = E0[m].sum() / ev[m].sum() if R.expected_votes_level == "prior" else 1.0
            E0 = E0.where(~m, ev * scale)
    complete = (df["n"] > 0) & (df["units"] > 0) & (df["reporting"] >= df["units"])
    E_done = pd.Series(np.nan, index=df.index)
    if R.use_outstanding:
        ok = complete & (df["ballots"] > 0) & df["outstanding"].notna()
        E_done[ok] = df["n"] + df["outstanding"] * df["n"] / df["ballots"]
    if R.f_complete > 0:
        E_done = E_done.fillna(df["n"].where(complete) / R.f_complete)
    done = E_done.notna()
    T = 1.0
    if R.learn_turnout and done.any():
        T = (E_done[done].sum() + R.turnout_prior_votes) / (E0[done].sum() + R.turnout_prior_votes)
    E = np.where(done, E_done, np.maximum(E0 * T, df["n"]))
    f = np.where(done, df["n"] / E, np.minimum(df["n"] / E, R.f_max))
    return pd.DataFrame({"unit_id": df["unit_id"], "n": df["n"], "a": df["a"], "ne": df["ea"] + df["eb"],
                         "ae": df["ea"], "f": np.clip(f, 0, 1), "E": E, "complete": done.to_numpy(),
                         "turnout_T": T})


def _model_units(cs: pd.DataFrame, pri: pd.DataFrame, st: str, R: CountRules) -> pd.DataFrame:
    """nowcast units. Default: one per county, region = state. vote_type: two per county — the early + mail
    batch (posted whole: f = 1 once any early vote is in) and the rest (Election Day, late mail, provisional) —
    with region = vote type, so the Election Day swing is learned from Election Day returns instead of being
    extrapolated from early batches (the main count-order bias in GA/TX, docs/us_midterms.md). Falls back to one
    unit per county while the feed reports votes but no early-vote split."""
    base = pd.DataFrame({"county": cs["unit_id"], "s0": pri["s0"].to_numpy()})
    if "h" in pri:
        base["h"] = pri["h"].to_numpy()
    if not R.vote_type or (cs["n"].sum() > 0 and cs["ne"].sum() == 0):
        return base.assign(uf=st, cd_mun=cs["unit_id"], n=cs["n"], a=cs["a"], f=cs["f"], V0=cs["E"])
    posted = cs["ne"] > 0
    Ee = np.where(posted, cs["ne"], R.early_share * cs["E"])
    El = np.maximum(cs["E"] - Ee, 0.02 * cs["E"])
    nl, al = cs["n"] - cs["ne"], cs["a"] - cs["ae"]
    fl = np.where(cs["complete"], np.minimum(nl / El, 1.0), np.minimum(nl / El, R.f_max))
    early = base.assign(uf=f"{st}-early", cd_mun=cs["unit_id"] + "|e", n=cs["ne"], a=cs["ae"],
                        f=posted.astype(float), V0=Ee)
    late = base.assign(uf=f"{st}-late", cd_mun=cs["unit_id"] + "|l", n=nl, a=al, f=fl, V0=El)
    return pd.concat([early, late], ignore_index=True)


def _county_projection(mu: pd.DataFrame, res: dict, p: nowcast.Params, cs: pd.DataFrame) -> pd.DataFrame:
    """Per county: counted (a, n), projected remaining Dem votes (ra) and votes (r), by the same remaining-share
    projection nowcast uses per unit. A slope model (p.hisp_slope) uses the deviations net of β·x and adds β·x."""
    counted = mu["n"] > 0
    d = np.where(counted, mu["a"] / mu["n"].where(counted) - mu["s0"], np.nan)
    bx = 0.0
    if p.hisp_slope:
        x = nowcast.hisp_dev(mu.assign(V0=mu["V0"]))
        bx = res["hisp_beta"] * x.to_numpy()
        d = d - bx
    w = mu["n"].where(counted, 0.0)
    wd = pd.Series(np.nan_to_num(d) * w).groupby(mu["uf"]).sum()
    W = w.groupby(mu["uf"]).sum()
    obs = res.get("swing_obs", res["swing_nat"])                   # = swing_nat unless a swing prior is set
    delta_u = ((wd + p.kappa * obs) / (W + p.kappa) + (res["swing_nat"] - obs)).reindex(mu["uf"]).to_numpy()
    s_hat = np.clip(mu["s0"] + delta_u + p.rho * (np.nan_to_num(d) - delta_u) * counted + bx, 0, 1)
    Rm = (1 - mu["f"]).clip(lower=0) * mu["V0"] * res["turnout_k"]
    agg = pd.DataFrame({"a": mu["a"], "n": mu["n"], "ra": Rm * s_hat, "r": Rm}).groupby(mu["county"].to_numpy()).sum()
    return agg.reindex(cs["unit_id"])


def project(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Race-level nowcast (brunder.nowcast over all units of the state) + per-county projected final Dem share.
    Model v2 sets [model] swing_prior_mean (poll average − baseline_share) and swing_prior_sd (poll error): the
    statewide swing is then the poll prior combined with the swing seen in counted votes (brunder.nowcast).
    Model v5 sets [model] hisp_slope = true (+ [count] hisp_share): a county's swing also depends on its Hispanic
    share through a slope learned from the night's counted counties; v6 hisp_blend = w mixes the v5 and v3 forecasts."""
    st, R, p = cfg["election"]["state"], rules(cfg), params(cfg)
    slope = p.hisp_slope or p.hisp_blend is not None
    if slope and "h" not in pri:
        if not R.hisp_share:
            raise ValueError("hisp_slope / hisp_blend need [count] hisp_share = <csv of unit_id, hisp>")
        pri = pri.assign(h=pri["unit_id"].map(_hisp_share(R.hisp_share)).to_numpy())
    cs = count_state(rows, cfg, pri)
    mu = _model_units(cs, pri, st, R)
    pcols = ["uf", "cd_mun", "s0", "V0"] + (["h"] if slope else [])
    res = nowcast.nowcast(mu[["uf", "cd_mun", "n", "a", "f"]], mu[pcols], p)
    if p.hisp_blend is None:
        agg = _county_projection(mu, res, p, cs)
    else:                                  # the mixture's mean per county: w·(v5 projection) + (1−w)·(v3 projection)
        res5 = nowcast.nowcast(mu[["uf", "cd_mun", "n", "a", "f"]], mu[pcols], replace(p, hisp_blend=None, hisp_slope=True))
        res3 = nowcast.nowcast(mu[["uf", "cd_mun", "n", "a", "f"]], mu[pcols], replace(p, hisp_blend=None, hisp_slope=False))
        a5 = _county_projection(mu, res5, replace(p, hisp_blend=None, hisp_slope=True), cs)
        a3 = _county_projection(mu, res3, replace(p, hisp_blend=None, hisp_slope=False), cs)
        agg = a5 * p.hisp_blend + a3 * (1 - p.hisp_blend)
    proj = ((agg["a"] + agg["ra"]) / (agg["n"] + agg["r"]).where(agg["n"] + agg["r"] > 0)).to_numpy()
    res["raw"] = float(cs["a"].sum() / cs["n"].sum()) if cs["n"].sum() > 0 else np.nan
    res["turnout_T"] = float(cs["turnout_T"].iloc[0]) if len(cs) else 1.0
    # display only: the model's remaining two-party votes per county (expected vote minus counted, turnout-scaled
    # as in nowcast) and their projected Democrat-minus-Republican margin
    rem, rem_margin = agg["r"].to_numpy(), (2 * agg["ra"] - agg["r"]).to_numpy()
    res["rem_total"], res["rem_margin_total"] = float(np.nansum(rem)), float(np.nansum(rem_margin))
    units = cs.assign(proj=proj, s0=pri["s0"].to_numpy(), V=pri["V"].to_numpy(), rem=rem, rem_margin=rem_margin)
    return res, units


def _tens(x) -> int | None:
    """Votes rounded to tens (display fields; keeps the files small)."""
    return None if x is None or not np.isfinite(x) else int(round(float(x) / 10.0)) * 10


def _tens_list(v) -> list[int]:
    return (np.round(np.nan_to_num(np.asarray(v, dtype=float)) / 10.0) * 10).astype(int).tolist()


# ------------------------------------------------------------------ dashboard export

def _iso(t: pd.Timestamp) -> str:
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, path)                            # atomic: the live site never reads a half-written file


def _pm_market(cfg: dict) -> tuple[str, str] | None:
    m = cfg.get("markets", {}).get("polymarket", {})
    return (f"{m['event']}/{m['market']}", m.get("yes_is", "a")) if m.get("market") else None


def _pairs(pairs_path: Path | None = None) -> list:
    from .arb import load_pairs
    path = pairs_path or ROOT_DIR / "configs" / "pairs" / f"{EID}.toml"
    return load_pairs(path)[1] if path.exists() else []


def kalshi_markets(cfgs: list[dict], pairs_path: Path | None = None) -> dict[str, dict]:
    """Per race the Kalshi market for its timeline line, from configs/pairs/us2026.toml (Kalshi workstream):
    a market whose YES is side a, preferring the one paired with the race's Polymarket market, first listed wins.
    {race_id: {ticker, series, yes_is, question (our paraphrase)}}; {} when the pairs file is absent."""
    pairs = _pairs(pairs_path)
    out = {}
    for c in cfgs:
        rid, pm = c["election"]["race_id"], c.get("markets", {}).get("polymarket", {}).get("market")
        cand = [p for p in pairs if p.race_id == rid and p.kalshi_yes_is == "a" and p.kalshi_ticker]
        cand.sort(key=lambda p: p.pm_market != pm)                    # stable: listed order within each group
        if cand:
            p = cand[0]
            out[rid] = {"ticker": p.kalshi_ticker, "series": p.kalshi_series, "yes_is": "a",
                        "question": p.kalshi_resolution or f"{c['election']['name']}: {c['election']['a']['name']} "
                                                           "(Democrat) wins"}
    return out


def pm_tokens(cfgs: list[dict], pairs_path: Path | None = None, lookup: bool = False) -> dict[str, str]:
    """YES CLOB token id of each race's configured Polymarket market (pairs file; Gamma lookup if missing)."""
    by_market = {p.pm_market: p.pm_yes_token for p in _pairs(pairs_path) if p.pm_yes_token}
    out = {}
    for c in cfgs:
        pm = c.get("markets", {}).get("polymarket", {})
        if not pm.get("market"):
            continue
        tok = by_market.get(pm["market"])
        if tok is None and not lookup:
            continue
        if tok is None:
            from . import polymarket
            m = next(m for m in polymarket.event(pm["event"])["markets"] if m["slug"] == pm["market"])
            ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
            tok = ids[0]
        out[c["election"]["race_id"]] = tok
    return out


# Polls close (UTC) on 2026-11-03, after US daylight saving time ended on 2026-11-01. Official hours: GA 7 a.m.-7 p.m.
# ET (georgia.gov/vote-in-person-election-day), OH 6:30 a.m.-7:30 p.m. ET (Ohio SoS 2026 elections calendar), TX
# 7 a.m.-7 p.m. local, central and mountain time (Texas SoS news release 2026-10-27), CA 7 a.m.-8 p.m. PT (California
# SoS, Elections Officers' Digest: Election Day), SC 7 a.m.-7 p.m. ET (scvotes.gov upcoming elections), VT close at
# 7 p.m. ET (Vermont SoS polling-place information). Added 2026-10-30: AZ 6 a.m.-7 p.m. MST (no DST), PA 7 a.m.-8 p.m.
# ET, MN 7 a.m.-8 p.m. CT, NE 8 a.m.-8 p.m. local (central; the western counties mountain), NM 7 a.m.-7 p.m. MT.
POLL_CLOSE = {"ga": "2026-11-04T00:00:00Z", "oh": "2026-11-04T00:30:00Z", "tx": "2026-11-04T01:00:00Z",
              "ca": "2026-11-04T04:00:00Z", "ak": "2026-11-04T05:00:00Z", "sc": "2026-11-04T00:00:00Z",
              "vt": "2026-11-04T00:00:00Z", "nc": "2026-11-04T00:30:00Z", "fl": "2026-11-04T00:00:00Z",
              "az": "2026-11-04T02:00:00Z", "pa": "2026-11-04T01:00:00Z", "mn": "2026-11-04T02:00:00Z",
              "ne": "2026-11-04T02:00:00Z", "nm": "2026-11-04T02:00:00Z"}
POLL_CLOSE.update(_civic.POLL_CLOSE)
POLL_CLOSE_LAST = {"tx": "2026-11-04T02:00:00Z", "ak": "2026-11-04T06:00:00Z",   # El Paso/Hudspeth (MT); Aleutians
                   "fl": "2026-11-04T01:00:00Z",                                  # FL panhandle (central time)
                   "ne": "2026-11-04T03:00:00Z"}                                  # NE panhandle (mountain time)
POLL_CLOSE_LAST.update(_civic.POLL_CLOSE_LAST)
POLL_SOURCES = [
    {"name": "Georgia.gov, Vote in person on Election Day (polls 7 a.m.-7 p.m.)",
     "url": "https://georgia.gov/vote-in-person-election-day", "licence": "public record"},
    {"name": "Ohio Secretary of State, 2026 Ohio elections calendar (polls 6:30 a.m.-7:30 p.m.)",
     "url": "https://www.ohiosos.gov/assets/election-calendar-2026.pdf", "licence": "public record"},
    {"name": "Texas Secretary of State, news release 2026-10-27 (polls 7 a.m.-7 p.m.)",
     "url": "https://www.sos.state.tx.us/about/newsreleases/2026/102726.shtml", "licence": "public record"},
    {"name": "California Secretary of State, Elections Officers' Digest: Election Day (polls 7 a.m.-8 p.m.)",
     "url": "https://www.sos.ca.gov/elections/publications-and-resources/elections-officers-digest/election-day",
     "licence": "public record"},
    {"name": "South Carolina Election Commission, upcoming elections (Nov 3, 7:00 am - 7:00 pm)",
     "url": "https://scvotes.gov/elections-statistics/upcoming-elections", "licence": "public record"},
    {"name": "Vermont Secretary of State, polling location information (all polls close at 7 p.m.)",
     "url": "https://sos.vermont.gov/elections/voters/polling-places", "licence": "public record"},
    {"name": "NC State Board of Elections, Vote on Election Day (polls 6:30 a.m.-7:30 p.m.)",
     "url": "https://www.ncsbe.gov/voting/vote-person-election-day", "licence": "public record"},
    {"name": "Florida Division of Elections, Election Day voting (polls 7 a.m.-7 p.m. local time; panhandle central)",
     "url": "https://dos.fl.gov/elections/for-voters/voting/election-day-voting/", "licence": "public record"},
]
NO_PRIOR = "county prior not available yet"


def kalshi_prices(arc: Archive, f: Path | None) -> dict[str, float]:
    """P(YES) per ticker (mid if spread ≤ 10c, brunder.kalshi.prob_from_book) from one kalshi_books snapshot."""
    if f is None:
        return {}
    out = {}
    for ticker, book in json.loads(arc.read(f)).items():
        p = kalshi.prob_from_book(book)
        if p is not None and np.isfinite(p):
            out[ticker] = float(p)
    return out


_BASE_OFFICE = {"gov": "governor", "sen": "Senate", "pres": "president"}
ESTIMATE_BASELINE = "2024 president (certified)"     # races without a county model (race_estimate)


def baseline_label(source: str) -> str:
    """The county baseline the model shifts, for display: prior source 'ga_gov_2022' -> '2022 governor (certified)'
    ('ak_sen_p2026' -> '2026 Senate primary (certified)')."""
    _, office, year = source.split("_", 2)
    primary = year.startswith("p")
    return f"{year.lstrip('p')} {_BASE_OFFICE.get(office, office)}{' primary' if primary else ''} (certified)"


def meta(cfgs: list[dict], priors_ok: dict[str, bool], kalshi_mk: dict[str, dict] | None = None,
         phase: str = "live", precount: dict[str, dict | None] | None = None,
         tokens: dict[str, str] | None = None, overview: str | None = None) -> dict:
    """phase: "pre-count" (brunder us preelection), "live" (brunder us live) or "replay" (brunder us export)."""
    races = []
    kalshi_mk, tokens, precount = kalshi_mk or {}, tokens or {}, precount or {}
    for c in cfgs:
        E = c["election"]
        rid = E["race_id"]
        mk = []
        pm = c.get("markets", {}).get("polymarket", {})
        if pm.get("market"):
            mk.append({"platform": "polymarket", "id": pm["market"], "question": pm.get("question", ""),
                       "yes_is": pm.get("yes_is", "a"), "token_yes": tokens.get(rid)})
        k = kalshi_mk.get(rid)
        if k:
            mk.append({"platform": "kalshi", "id": k["ticker"], "series": k["series"], "question": k["question"],
                       "yes_is": k["yes_is"]})
        race = {"race_id": rid, "name": E["name"], "region": E["state"],
                "a": {"id": E["a"]["party"].lower(), "name": E["a"]["name"], "color": E["a"]["color"]},
                "b": {"id": E["b"]["party"].lower(), "name": E["b"]["name"], "color": E["b"]["color"]},
                "markets": mk, "units": f"units/{rid}.json" if priors_ok[rid] else None,
                "poll_close_utc": POLL_CLOSE.get(E["state"]), "config": Path(c["_path"]).name,
                "baseline": baseline_label(c["prior"]["share"]),
                "config_v2": (_sibling_versions(c).get("v2") or Path("")).name or None,
                "versions": {v: q.name for v, q in _sibling_versions(c).items()},
                "poll_model": poll_model({v: load_config(q) for v, q in _sibling_versions(c).items()}),
                "secondary_models": [v for v, q in _sibling_versions(c).items()
                                     if load_config(q).get("role") == "secondary"]}
        if E.get("source"):                          # secondary source (civicAPI): attribution on every display
            race["source"] = {"name": E["source"], "url": E.get("source_url", _civic.SOURCE_URL), "role": "secondary"}
        if phase == "pre-count":
            race["precount"] = precount.get(rid)
        if not priors_ok[rid]:
            race["model_note"] = NO_PRIOR
        races.append(race)
    states = sorted({c["election"]["state"] for c in cfgs})
    official = sorted({c["election"]["state"] for c in cfgs if not c["election"].get("source")})
    srcs = [{"name": "Official results feeds: " + ", ".join(STATES[s] for s in official) + " (election night)",
             "url": "https://www.sos.ca.gov/elections", "licence": "public record"}] if official else []
    srcs += [{"name": i[0], "url": i[1], "licence": i[2]} for i in dict.fromkeys(SOURCE_INFO[s] for s in states)]
    civic = sorted({c["election"]["state"] for c in cfgs if c["election"].get("source")})
    if civic:
        srcs.append({"name": f"{_civic.SOURCE_NAME}: election-night county results, secondary source ("
                             + ", ".join(STATES[s] for s in civic) + ")",
                     "url": _civic.SOURCE_URL, "licence": _civic.SOURCE_LICENCE})
    srcs.append({"name": "US Census Bureau county codes / cartographic boundaries",
                 "url": "https://www.census.gov/geographies/reference-files.html", "licence": "public domain"})
    srcs.append({"name": "Polymarket: market prices (CLOB)", "url": "https://polymarket.com",
                 "licence": "public market data"})
    if kalshi_mk:
        srcs.append({"name": "© Kalshi: market prices (order-book midpoints)", "url": "https://kalshi.com",
                     "licence": "© Kalshi; shown per the project's decision and pending exception request, "
                                "see docs/kalshi.md"})
    srcs += POLL_SOURCES + [_extra.POLL_SOURCES[s] for s in states if s in _extra.POLL_SOURCES]
    srcs += [_civic.POLL_SOURCES[s] for s in states if s in _civic.POLL_SOURCES]
    srcs.append({"name": "VoteHub Polling API: Senate and governor polls (model v2 prior; our averages only)",
                 "url": "https://votehub.com/polls/api/", "licence": "CC BY 4.0"})
    notes = [
        "counted = share of the expected two-party vote already counted (model estimate; US feeds report "
        "precincts, not remaining ballots).",
        "raw and share are the Democratic share of the Democrat + Republican vote.",
        "Alaska's races are not modelled: under top-four ranked-choice voting several candidates share a party, "
        "so party totals do not answer the candidate-specific markets.",
        "kalshi = Kalshi P(Democrat): midpoint of the market's order book when the spread is at most 10c, else "
        "empty; market per race from configs/pairs/us2026.toml. Prices © Kalshi.",
        "Model frozen before polls closed (configs/us2026_*_v1.toml); see docs/us_midterms.md.",
        "P = model v1 (county prior = past certified results); P_v2 = model v2, the same model plus a prior on the "
        "statewide swing from the poll average frozen in configs/us2026_*_v2.toml (VoteHub polls, CC BY 4.0): the "
        "polls lead before and early in the count, and the counted votes take over as the count grows.",
        "Polls close (UTC, Nov 4): " + ", ".join(f"{STATES[s]} {POLL_CLOSE[s][11:16]}" for s in states)
        + (" (Texas 02:00 in El Paso and Hudspeth counties)" if "tx" in states else "")
        + (" (Florida 01:00 in the central-time panhandle)" if "fl" in states else "") + ".",
    ]
    if civic:
        notes.append(f"Races with a source label use {_civic.SOURCE_NAME}, {_civic.SOURCE_URL}: a secondary, unofficial "
                     "county feed (its percent reporting is taken as the county's precincts reporting).")
    if phase == "pre-count":
        notes.insert(0, "Before the count model v1's estimate is the past certified results by county (the race "
                        "configs name which) with its uncertainty; model v2 adds the poll average.")
        notes.insert(1, "Pre-count timeline: every 30 minutes over the last 14 days; pm = Polymarket CLOB price "
                        "history, kalshi = Kalshi 1-minute candles sampled at each point (mid if spread ≤ 10c, else "
                        "last trade), P / P_v2 = model v1 / v2 chance before any votes are counted (constant).")
    missing = [c["election"]["name"] for c in cfgs if not priors_ok[c["election"]["race_id"]]]
    if missing:
        notes.append(f"{', '.join(missing)}: {NO_PRIOR}; prices are shown, model fields are empty.")
    return {
        "id": EID, "name": "US midterm elections", "date": "2026-11-03", "tz": "America/New_York",
        "phase": phase, "poll_close_utc": min(POLL_CLOSE[s] for s in states),
        "poll_close_by_region": {s: POLL_CLOSE[s] for s in states},
        "geometry": "geo/us_counties.topo.json", "geometry_object": "counties", "unit_key": "unit_id",
        "regions": [{"id": s, "name": STATES[s], "poll_close_utc": POLL_CLOSE[s],
                     **({"poll_close_last_utc": POLL_CLOSE_LAST[s]} if s in POLL_CLOSE_LAST else {})}
                    for s in states],
        "races": races, "sources": srcs, "notes": notes, "baseline_estimate": ESTIMATE_BASELINE,
        **({"overview": overview} if overview else {}),
    }


class Exporter:
    """Builds timeline rows and unit frames for every configured race from the snapshot archive.
    Incremental: each call processes only minutes after each race's own cursor, so the live loop can call it every
    ~30 s. Failures are isolated: a feed whose latest snapshot does not parse falls back to its last parsed state;
    a race that still fails keeps its cursor (retried next pass, no duplicate rows) while the others publish."""

    def project(self, rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
        """The module's `project` (looked up per call); subclasses may supply an exact faster equivalent
        (brunder.us_house_night.HouseExporter)."""
        return project(rows, cfg, pri)

    def __init__(self, cfg_paths: list[Path], snap_root: Path | None = None, priors: pd.DataFrame | None = None,
                 step: str = "1min", frame_every: str = "5min", max_retries: int = 3,
                 pairs_path: Path | None = None, phase: str = "live"):
        """max_retries: passes a failing minute is retried before it is skipped (live: the archive may still
        change; replay: use 1, the archive is final)."""
        self.max_retries, self.phase = max_retries, phase
        self._retries: dict[str, tuple[pd.Timestamp, int]] = {}
        self.cfgs, self.versions = load_races(cfg_paths)
        self.arc = Archive(snap_root or SNAP / TAG)
        pr = priors if priors is not None else load_priors()
        self.pri = {c["election"]["race_id"]: race_prior(c, pr) for c in self.cfgs}
        self.pri_v = {rid: {v: race_prior(cv, pr) for v, cv in vs.items()} for rid, vs in self.versions.items()}
        self.polls = load_polls()
        self.turnout = {rid: turnout_profile(c, self.pri[rid]) for c in self.cfgs
                        if (rid := c["election"]["race_id"]) and self.pri[rid] is not None and not self.pri[rid].empty}
        self.step, self.frame_every = pd.Timedelta(step), pd.Timedelta(frame_every)
        rids = [c["election"]["race_id"] for c in self.cfgs]
        self.timeline: dict[str, list[dict]] = {r: [] for r in rids}
        self.frames: dict[str, list[dict]] = {r: [] for r in rids}
        self.cursor: dict[str, pd.Timestamp | None] = {r: None for r in rids}   # last minute published per race
        self.errors: dict[str, str] = {}                                         # latest error per race / feed
        self._memo: dict[str, tuple[tuple, tuple[dict, pd.DataFrame]]] = {}    # race -> (sig, result), latest only
        self._good: dict[str, tuple[tuple, pd.DataFrame]] = {}                 # feed -> last parsed (sig, rows)
        self._failed: dict[str, tuple] = {}                                    # feed -> sig that failed to parse
        self._pm: tuple[Path | None, dict[str, float]] = (None, {})
        self.kalshi_mk = kalshi_markets(self.cfgs, pairs_path)
        try:
            self.tokens = pm_tokens(self.cfgs, pairs_path)
        except Exception as e:                       # token ids are for the page's own price polling only
            self.tokens = {}
            self.errors["tokens"] = f"{type(e).__name__}: {e}"
        self._ks: tuple[Path | None, dict[str, float]] = (None, {})
        self._pre: dict[str, dict] = {}                                         # race -> its pre-count frame
        self.feeds: list[dict] | None = None                                    # live: feed_status, into meta.json

    def _rows_for(self, kind: str, t: pd.Timestamp) -> tuple[pd.DataFrame, tuple]:
        files = feed_files(self.arc, kind, t)
        sig = tuple(x for f in files.values() for x in (f if isinstance(f, list) else [f]) if x is not None)
        good = self._good.get(kind)
        if good and good[0] == sig:
            return good[1], sig
        if self._failed.get(kind) != sig:
            try:
                rows = parse_feed(kind, feed_blobs(self.arc, files))
                self._good[kind] = (sig, rows)
                self._failed.pop(kind, None)
                self.errors.pop(f"feed:{kind}", None)
                return rows, sig
            except Exception as e:                   # valid document, unexpected content
                self._failed[kind] = sig
                self.errors[f"feed:{kind}"] = f"{_iso(t)} {type(e).__name__}: {e}"
                print(f"feed {kind}: parse failed at {_iso(t)}, using its last parsed state: "
                      f"{type(e).__name__}: {e}", flush=True)
        if good is None:                             # nothing parsable yet: same as no returns
            return _frame([]), ("unparsed",) + sig
        return good[1], good[0]

    def _precount(self, rid: str, c: dict, pri: pd.DataFrame, t: str) -> dict:
        """precount_frame at minute t; its content does not depend on t, so it is computed once per race."""
        if rid not in self._pre:
            self._pre[rid] = precount_frame(c, pri, t, *self._poll_cfg(rid))
        return {**self._pre[rid], "t": t}

    def _poll_cfg(self, rid: str) -> tuple[dict | None, pd.DataFrame | None]:
        """(config, prior) of the race's poll-model version, or (None, None)."""
        pm = poll_model(self.versions.get(rid, {}))
        return (self.versions[rid][pm], self.pri_v[rid][pm]) if pm else (None, None)

    def _tick(self, c: dict, t: pd.Timestamp, prices: dict[str, float], kprices: dict[str, float]) -> None:
        rid, pri = c["election"]["race_id"], self.pri[c["election"]["race_id"]]
        has_model = pri is not None and not pri.empty
        res, units = {}, None
        if has_model:
            rows, sig = self._rows_for(c["election"]["feed"], t)
            memo = self._memo.get(rid)
            if memo is None or memo[0] != sig:
                memo = (sig, self.project(rows, c, pri))
                self._memo[rid] = memo
            res, units = memo[1]
            upoll, pmod = None, poll_model(self.versions.get(rid, {}))
            for v, cv in self.versions.get(rid, {}).items():       # every later model version (v2, v3, …)
                pv = self.pri_v[rid][v]
                if pv is None or pv.empty:
                    continue
                mv = self._memo.get(f"{rid}|{v}")
                if mv is None or mv[0] != sig:
                    mv = (sig, self.project(rows, cv, pv))
                    self._memo[f"{rid}|{v}"] = mv
                rv = mv[1][0]
                res = {**res, f"p_win_{v}": rv["p_win"], f"share_{v}": rv["share_hat"], f"sd_{v}": rv["share_sd"]}
                if v == pmod:
                    upoll = mv[1][1]
        pm = _pm_market(c)
        p_yes = prices.get(pm[0]) if pm else None
        p_a = None if p_yes is None else (p_yes if pm[1] == "a" else 1 - p_yes)
        km = self.kalshi_mk.get(rid)
        k_yes = kprices.get(km["ticker"]) if km else None
        k_a = None if k_yes is None else (k_yes if km["yes_is"] == "a" else 1 - k_yes)
        row = {"t": _iso(t), "counted": _r(res.get("counted_frac")), "raw": _r(res.get("raw")),
               "share": _r(res.get("share_hat")), "sd": _r(res.get("share_sd")), "P": _r(res.get("p_win")),
               "P_v2": _r(res.get("p_win_v2")), "share_v2": _r(res.get("share_v2")), "sd_v2": _r(res.get("sd_v2")),
               **{f"{k}_{v}": _r(res.get(f"{n}_{v}")) for v in self.versions.get(rid, {}) if v != "v2"
                  for k, n in (("P", "p_win"), ("share", "share"), ("sd", "sd"))},
               "pm": _r(p_a), "kalshi": _r(k_a), "rem_total": _tens(res.get("rem_total")),
               "rem_margin_total": _tens(res.get("rem_margin_total")),
               "votes_proj": _tens(units["n"].sum() + res["rem_total"]) if units is not None else None,
               **margin_fields(res.get("share_hat"), res.get("share_sd"), res.get("share_v2"), p_a, k_a,
                               self.polls.get(rid)),
               **turnout_row(units, self.turnout.get(rid), res.get("rem_total"))}
        if not has_model:                             # prices only (e.g. Ohio without its county prior)
            self.timeline[rid].append(row)
            self.cursor[rid] = t
            return
        fr = self.frames[rid]
        frame = None
        if units["n"].sum() > 0 and (not fr or t - pd.Timestamp(fr[-1]["t"]) >= self.frame_every):
            frame = {"t": _iso(t),
                     "f": (units["f"] * 1000).round().astype(int).tolist(),
                     "a": np.where(units["n"] > 0, (units["a"] / units["n"].where(units["n"] > 0) * 1000)
                                   .fillna(-1), -1).round().astype(int).tolist(),
                     "proj": (units["proj"].fillna(units["s0"]) * 1000).round().astype(int).tolist(),
                     "rem": _tens_list(units["rem"]), "rem_margin": _tens_list(units["rem_margin"]),
                     **poll_frame_cols(units, upoll), **turnout_frame(units, self.turnout[rid])}
        self.timeline[rid].append(row)                # only after everything for this minute succeeded
        if frame:
            fr.append(frame)
            if len(fr) > 300:                        # bounded memory: thin older frames, keep the latest
                fr[:] = fr[::-1][::2][::-1]
        self.cursor[rid] = t

    def update(self, until: pd.Timestamp | None = None) -> None:
        self.arc.refresh()
        ts = self.arc.times()
        if not ts:
            return
        end = min(until, ts[-1] + self.step) if until is not None else ts[-1]
        active = self.cfgs                            # races without a prior still publish their prices
        first = ts[0].ceil(self.step)
        starts = [first if self.cursor[c["election"]["race_id"]] is None
                  else self.cursor[c["election"]["race_id"]] + self.step for c in active]
        if not starts:
            return
        failed: set[str] = set()                     # races that failed this pass: retried next pass
        for t in pd.date_range(min(starts), end, freq=self.step):
            try:
                books = self.arc.at("polymarket_books", t)
                if books != self._pm[0]:
                    self._pm = (books, pm_prices(self.arc, books))
            except Exception as e:                   # market data must not block the model
                self.errors["polymarket"] = f"{_iso(t)} {type(e).__name__}: {e}"
            try:
                kb = self.arc.at("kalshi_books", t)
                if kb != self._ks[0]:
                    self._ks = (kb, kalshi_prices(self.arc, kb))
            except Exception as e:
                self.errors["kalshi"] = f"{_iso(t)} {type(e).__name__}: {e}"
            for c in active:
                rid = c["election"]["race_id"]
                cur = self.cursor[rid]
                if rid in failed or (cur is not None and t <= cur):
                    continue
                try:
                    self._tick(c, t, self._pm[1], self._ks[1])
                    self.errors.pop(rid, None)
                    self._retries.pop(rid, None)
                except Exception as e:
                    prev = self._retries.get(rid)
                    n = prev[1] + 1 if prev and prev[0] == t else 1
                    self._retries[rid] = (t, n)
                    self.errors[rid] = f"{_iso(t)} {type(e).__name__}: {e}"
                    if n >= self.max_retries:        # a minute that keeps failing is left out, not retried forever
                        self.cursor[rid] = t
                        self._retries.pop(rid)
                        print(f"race {rid}: {_iso(t)} failed {n} passes, minute skipped: {type(e).__name__}: {e}",
                              flush=True)
                    else:
                        failed.add(rid)
                        print(f"race {rid}: failed at {_iso(t)}, retry next pass: {type(e).__name__}: {e}",
                              flush=True)

    def tail(self, minutes: int = 60, timeline: dict[str, list[dict]] | None = None) -> dict:
        return timeline_tail(timeline if timeline is not None else self.timeline, minutes)

    def merged_timeline(self, d: Path) -> dict[str, list[dict]]:
        """The count timeline with the pre-count rows (us2026/timeline_precount.json, written by `brunder us
        preelection`) in front of each race's first count row, so the page shows one continuous line."""
        f = d / "timeline_precount.json"
        try:
            pre = json.loads(f.read_text()) if f.exists() else {}
        except Exception as e:                       # a damaged pre-count file must not block the live files
            self.errors["precount"] = f"{type(e).__name__}: {e}"
            pre = {}
        out = {}
        for rid, rows in self.timeline.items():
            first = rows[0]["t"] if rows else None
            out[rid] = [r for r in pre.get(rid, []) if first is None or r["t"] < first] + rows
        return out

    def write(self, out: Path) -> Path:
        d = Path(out) / EID
        ok = {rid: p is not None and not p.empty for rid, p in self.pri.items()}
        ov = refresh_overview(d, self.arc, self.errors)
        m = meta(self.cfgs, ok, self.kalshi_mk, phase=self.phase, tokens=self.tokens,
                 overview="overview.json" if ov else None)
        if self.feeds is not None:                   # live: the recorders' per-feed status (feed_status)
            m["feeds"] = self.feeds
        _write_json(d / "meta.json", m)
        tl = self.merged_timeline(d)
        _write_json(d / "timeline.json", tl)
        _write_json(d / "timeline_tail.json", self.tail(timeline=tl))
        st = d / "stats.json"                        # never overwrite real statistics written by the analysis
        try:
            placeholder = not st.exists() or all(json.loads(st.read_text()).get(k) is None for k in STATS_KEYS)
        except Exception:
            placeholder = True
        if placeholder:
            _write_json(st, empty_stats("computed after the count" if self.phase == "live"
                                        else "not computed by the exporter"))
        default = None
        now = _iso(pd.Timestamp.now(tz="UTC").floor("1min"))
        for c in self.cfgs:
            rid = c["election"]["race_id"]
            pri = self.pri[rid]
            if not ok[rid]:
                continue
            try:
                fr = self.frames[rid] or [self._precount(rid, c, pri, now)]                      # before the count
                if len(fr) > 60:                      # keep ~60 frames, always including the latest
                    idx = np.unique(np.linspace(0, len(fr) - 1, 60).round().astype(int))
                    fr = [fr[i] for i in idx]
                obj = units_object(c, pri, fr)
                _write_json(d / "units" / f"{rid}.json", obj)
                default = default or obj
            except Exception as e:                   # one race's map must not block the others
                self.errors[rid] = f"write {type(e).__name__}: {e}"
                print(f"race {rid}: units file not written: {type(e).__name__}: {e}", flush=True)
        if default:
            _write_json(d / "units.json", default)
        return d


def timeline_tail(timeline: dict[str, list[dict]], minutes: int = 60) -> dict:
    """timeline.json's shape restricted to each race's last `minutes` minutes (rows with t > its last row's
    t − minutes), plus "tail": true and "since" (ISO time of the earliest row included, null if none). The live
    page polls this small file; timeline.json stays complete for the first load."""
    out: dict = {}
    for rid, rows in timeline.items():
        if rows:
            cut = pd.Timestamp(rows[-1]["t"]) - pd.Timedelta(minutes=minutes)
            i = len(rows)
            while i and pd.Timestamp(rows[i - 1]["t"]) > cut:
                i -= 1
            out[rid] = rows[i:]
        else:
            out[rid] = []
    firsts = [r[0]["t"] for r in out.values() if r]
    return {**out, "tail": True, "since": min(firsts) if firsts else None}


STATS_KEYS = ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage", "evidence")


def empty_stats(reason: str) -> dict:
    """stats.json with the standard keys all null and the reason (the standard metrics need the finished count)."""
    return {**{k: None for k in STATS_KEYS}, "reasons": {k: reason for k in STATS_KEYS}}


def poll_frame_cols(units: pd.DataFrame, upoll: pd.DataFrame | None) -> dict:
    """proj_poll / rem_margin_poll: the race's poll-model version per county, aligned to the v1 units (×1000 /
    tens like proj / rem_margin); null without a poll model."""
    if upoll is None:
        return {"proj_poll": None, "rem_margin_poll": None}
    u = upoll.set_index("unit_id").reindex(units["unit_id"])
    return {"proj_poll": (u["proj"].fillna(u["s0"]) * 1000).round().fillna(-1).astype(int).tolist(),
            "rem_margin_poll": _tens_list(u["rem_margin"].fillna(0.0))}


def precount_frame(c: dict, pri: pd.DataFrame, t: str, cpoll: dict | None = None,
                   pri_poll: pd.DataFrame | None = None) -> dict:
    """A units frame before any returns: nothing counted, projection = the prior, remaining = all expected votes;
    proj_poll = the poll model's projection (cpoll, its prior) when the race has one."""
    _, u = project(_frame([]), c, pri)
    up = project(_frame([]), cpoll, pri_poll)[1] if cpoll is not None and pri_poll is not None and not pri_poll.empty \
        else None
    n = len(pri)
    return {"t": t, "f": [0] * n, "a": [-1] * n, "proj": (u["proj"].fillna(u["s0"]) * 1000).round().astype(int).tolist(),
            "rem": _tens_list(u["rem"]), "rem_margin": _tens_list(u["rem_margin"]), **poll_frame_cols(u, up),
            **turnout_frame(u, turnout_profile(c, pri))}


TURNOUT_INDEX = ROOT_DIR / "data_public" / "us2026_turnout_index.csv"   # analysis/us_model_analysis.py


def turnout_profile(c: dict, pri: pd.DataFrame) -> pd.DataFrame:
    """Per county (aligned to pri): turnout_exp = the race's expected two-party votes (V × turnout_ratio) spread
    across counties by the turnout model's relative midterm propensity (statewide total unchanged; display only),
    and lowprop_index (standardised predicted drop-off; NaN where the model has no covariates)."""
    v0 = (pri["V"] * rules(c).turnout_ratio).to_numpy()
    ti = pd.read_csv(TURNOUT_INDEX, comment="#").set_index("unit_id") if TURNOUT_INDEX.exists() else pd.DataFrame(
        columns=["adj", "lowprop_index"])
    adj = ti["adj"].reindex(pri["unit_id"]).fillna(1.0).to_numpy()
    exp = v0 * adj
    exp = exp * v0.sum() / exp.sum() if exp.sum() > 0 else exp
    return pd.DataFrame({"unit_id": pri["unit_id"].to_numpy(), "turnout_exp": exp,
                         "lowprop": ti["lowprop_index"].reindex(pri["unit_id"]).to_numpy()})


def _turnout_ratio(units: pd.DataFrame, prof: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(ratio, reported): counted ÷ expected where a county is ≥ 95 % in, else projected total ÷ expected."""
    n, rem, exp = units["n"].to_numpy(float), units["rem"].to_numpy(float), prof["turnout_exp"].to_numpy(float)
    rep = ((units["f"].to_numpy(float) >= 0.95) | units["complete"].to_numpy(bool)) & (n > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(rep, n, n + np.nan_to_num(rem)) / np.where(exp > 0, exp, np.nan)
    return ratio, rep


def turnout_frame(units: pd.DataFrame, prof: pd.DataFrame) -> dict:
    ratio, _ = _turnout_ratio(units, prof)
    return {"turnout_so_far": _tens_list(units["n"]),
            "turnout_ratio_live": np.where(np.isfinite(ratio), np.round(ratio * 1000), -1).astype(int).tolist()}


def turnout_row(units: pd.DataFrame | None, prof: pd.DataFrame | None, rem_total: float | None) -> dict:
    """Race-level turnout columns: expected and projected two-party totals, projected ÷ expected (%), and across
    counties ≥ 95 % reported the correlation and slope of turnout surprise (log counted ÷ expected) on the
    low-propensity index, plus the D − R margin (points) of counties running above / below expected turnout."""
    keys = ("turnout_exp_total", "turnout_proj_total", "turnout_vs_expected", "turnout_surprise_corr",
            "turnout_surprise_slope", "margin_hot", "margin_cold", "counties_reported")
    if units is None or prof is None:
        return dict.fromkeys(keys)
    exp_total = float(prof["turnout_exp"].sum())
    proj = float(units["n"].sum() + (rem_total or 0.0))
    out = dict.fromkeys(keys)
    out.update(turnout_exp_total=_tens(exp_total), turnout_proj_total=_tens(proj),
               turnout_vs_expected=_r(100 * proj / exp_total, 2) if exp_total else None)
    ratio, rep = _turnout_ratio(units, prof)
    ok = rep & np.isfinite(ratio) & (ratio > 0)
    out["counties_reported"] = int(ok.sum())
    if ok.sum() >= 3:
        s = np.log(ratio[ok])
        a, n = units["a"].to_numpy(float)[ok], units["n"].to_numpy(float)[ok]
        hot, cold = s > 0, s <= 0
        out["margin_hot"] = _r(100 * (2 * a[hot].sum() - n[hot].sum()) / n[hot].sum(), 2) if hot.any() else None
        out["margin_cold"] = _r(100 * (2 * a[cold].sum() - n[cold].sum()) / n[cold].sum(), 2) if cold.any() else None
        x = prof["lowprop"].to_numpy(float)[ok]
        m = np.isfinite(x)
        if m.sum() >= 3 and np.std(x[m]) > 0 and np.std(s[m]) > 0:
            out["turnout_surprise_corr"] = _r(float(np.corrcoef(x[m], s[m])[0, 1]))
            out["turnout_surprise_slope"] = _r(float(np.polyfit(x[m], s[m], 1)[0]))
    return out


def units_object(c: dict, pri: pd.DataFrame, frames: list[dict]) -> dict:
    prof = turnout_profile(c, pri)
    return {"race_id": c["election"]["race_id"], "unit_ids": pri["unit_id"].tolist(),
            "region": [c["election"]["state"]] * len(pri), "names": pri["name"].tolist(),
            "prior": (pri["s0"] * 1000).round().astype(int).tolist(),
            "prior_margin": ((2 * pri["s0"] - 1) * 1000).round().astype(int).tolist(),
            "V0": (pri["V"] * rules(c).turnout_ratio).round().astype(int).tolist(),
            "turnout_exp": _tens_list(prof["turnout_exp"]),
            "lowprop_index": [None if not np.isfinite(x) else round(float(x), 2) for x in prof["lowprop"]],
            "frames": frames}


def export(cfg_paths: list[Path], out: Path, snap_root: Path | None = None, until: str | None = None) -> Path:
    """Replay: everything recorded up to `until` (default: the end of the archive)."""
    ex = Exporter(cfg_paths, snap_root, max_retries=1, phase="replay")
    ex.update(pd.Timestamp(until).tz_convert("UTC") if until else None)
    return ex.write(out)


def _asof(series: pd.Series, grid: pd.DatetimeIndex) -> list[float | None]:
    """Last value at or before each grid time (None before the first)."""
    if series is None or series.empty:
        return [None] * len(grid)
    s = series.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    v = s.reindex(s.index.union(grid)).ffill().reindex(grid)
    return [None if not np.isfinite(x) else float(x) for x in v.to_numpy(dtype=float)]


def margin_fields(share: float | None, sd: float | None, share_v2: float | None, p_pm: float | None,
                  p_k: float | None, poll: dict | None) -> dict:
    """Timeline margin columns, D − R in points of the two-party vote: the model's projected margin and its sd, model
    v2's, the poll average's, and the margins the Polymarket / Kalshi prices imply at the model's margin sd
    (Φ⁻¹(p) × σ; the poll sd when the model has none)."""
    msd = 2 * sd * 100 if sd else (2 * poll["sd"] * 100 if poll else None)
    return {"margin_model": _margin(share), "margin_model_sd": _r(msd, 2) if sd else None,
            "margin_v2": _margin(share_v2), "margin_polls": _margin(poll["avg"]) if poll else None,
            "margin_market": market_margin(p_pm, msd), "margin_market_kalshi": market_margin(p_k, msd)}


def preelection(cfg_paths: list[Path], out: Path, days: float = 14, step: str = "30min",
                now: pd.Timestamp | None = None, priors: pd.DataFrame | None = None,
                pairs_path: Path | None = None, force: bool = False) -> Path:
    """Dashboard files before the count (phase "pre-count"): per race the model's chance with zero votes counted
    (constant; the frozen config on the county prior), Polymarket CLOB price history and Kalshi 1-minute candles
    sampled every `step` over the last `days`, and a map frame = the prior. Network: Polymarket and Kalshi public
    APIs only. Refuses to overwrite files of the live or replay phase unless force."""
    from . import polymarket
    cfgs, versions = load_races(cfg_paths)
    d = Path(out) / EID
    if (d / "meta.json").exists() and not force:
        phase = json.loads((d / "meta.json").read_text()).get("phase")
        if phase in ("live", "replay"):
            print(f"{d}: phase is {phase!r}, pre-count files not written (use --force)", flush=True)
            return d
    pr = priors if priors is not None else load_priors()
    pri = {c["election"]["race_id"]: race_prior(c, pr) for c in cfgs}
    pri_v = {rid: {v: race_prior(cv, pr) for v, cv in vs.items()} for rid, vs in versions.items()}
    polls = load_polls()
    ok = {rid: p is not None and not p.empty for rid, p in pri.items()}
    now = (now or pd.Timestamp.now(tz="UTC")).floor(step)
    grid = pd.date_range(now - pd.Timedelta(days=days), now, freq=step)
    kmk, tokens = kalshi_markets(cfgs, pairs_path), pm_tokens(cfgs, pairs_path, lookup=True)
    pre: dict[str, dict | None] = {}
    timeline: dict[str, list[dict]] = {}
    for c in cfgs:
        rid = c["election"]["race_id"]
        pre[rid] = None
        if ok[rid]:
            res, u0 = project(_frame([]), c, pri[rid])
            pre[rid] = {"P": _r(res["p_win"]), "share": _r(res["share_hat"]), "sd": _r(res["share_sd"]),
                        "rem_total": _tens(res["rem_total"]), "rem_margin_total": _tens(res["rem_margin_total"]),
                        "P_v2": None, "share_v2": None, "sd_v2": None,
                        "poll_model": poll_model(versions.get(rid, {})),
                        "turnout": turnout_row(u0, turnout_profile(c, pri[rid]), res["rem_total"])}
            for v, cv in versions.get(rid, {}).items():
                if pri_v[rid][v] is not None and not pri_v[rid][v].empty:
                    rv, _ = project(_frame([]), cv, pri_v[rid][v])
                    pre[rid].update({f"P_{v}": _r(rv["p_win"]), f"share_{v}": _r(rv["share_hat"]),
                                     f"sd_{v}": _r(rv["share_sd"])})
        pm_vals = [None] * len(grid)
        pm = c.get("markets", {}).get("polymarket", {})
        if tokens.get(rid):
            try:
                h = polymarket.price_history(tokens[rid], grid[0] - pd.Timedelta(hours=6), now, fidelity_min=30)
                s = h.set_index("time")["price"].astype(float)
                s = s if pm.get("yes_is", "a") == "a" else 1 - s
                pm_vals = _asof(s, grid)
            except Exception as e:
                print(f"{rid}: Polymarket history failed: {type(e).__name__}: {e}", flush=True)
        k_vals = [None] * len(grid)
        km = kmk.get(rid)
        if km:
            try:
                cnd = kalshi.candlesticks(km["series"], km["ticker"], grid[0] - pd.Timedelta(hours=6), now, 1)
                p = kalshi.prob_from_candles(cnd, grid) if len(cnd) else pd.Series(np.nan, index=grid)
                p = p if km["yes_is"] == "a" else 1 - p
                k_vals = [None if x is None or not np.isfinite(x) else float(x) for x in p.to_numpy(dtype=float)]
            except Exception as e:
                print(f"{rid}: Kalshi history failed: {type(e).__name__}: {e}", flush=True)
        m = pre[rid] or {}
        timeline[rid] = [{"t": _iso(t), "counted": None, "raw": None, "share": m.get("share"), "sd": m.get("sd"),
                          "P": m.get("P"), "P_v2": m.get("P_v2"), "share_v2": m.get("share_v2"),
                          "sd_v2": m.get("sd_v2"), "pm": _r(pv), "kalshi": _r(kv),
                          **{f"{k}_{v}": m.get(f"{k}_{v}") for v in versions.get(rid, {}) if v != "v2"
                             for k in ("P", "share", "sd")},
                          "rem_total": m.get("rem_total"),
                          "rem_margin_total": m.get("rem_margin_total"), "votes_proj": m.get("rem_total"),
                          **margin_fields(m.get("share"), m.get("sd"), m.get("share_v2"), pv, kv, polls.get(rid)),
                          **(m.get("turnout") or turnout_row(None, None, None))}
                         for t, pv, kv in zip(grid, pm_vals, k_vals)]
    _write_json(d / "meta.json", meta(cfgs, ok, kmk, phase="pre-count", precount=pre, tokens=tokens,
                                      overview="overview.json" if RACES_REGISTRY.exists() else None))
    _write_json(d / "timeline_precount.json", timeline)    # kept: the live exporter puts these rows first
    _write_json(d / "timeline.json", timeline)
    _write_json(d / "timeline_tail.json", timeline_tail(timeline))
    _write_json(d / "stats.json", empty_stats("count has not started"))
    default = None
    for c in cfgs:
        rid = c["election"]["race_id"]
        if ok[rid]:
            pm = poll_model(versions.get(rid, {}))
            obj = units_object(c, pri[rid], [precount_frame(c, pri[rid], _iso(now), versions[rid][pm] if pm else None,
                                                             pri_v[rid][pm] if pm else None)])
            _write_json(d / "units" / f"{rid}.json", obj)
            default = default or obj
    if default:
        _write_json(d / "units.json", default)
    if RACES_REGISTRY.exists():
        _write_json(d / "overview.json", add_estimates(d, overview(now, days=days, step=step), pre))
    return d


# ------------------------------------------------------------------ polls (model v2 prior; overview display)
# Fixed rule, written in docs/us_midterms.md before any average was computed. Raw polls are cached under
# data/raw/us/polls (gitignored); only our averages are published (data_public/us2026_polls.json).

VOTEHUB = "https://api.votehub.com/polls"          # VoteHub Polling API, CC BY 4.0 (votehub.com/polls/api)
POLLS_ASOF = "2026-11-02"                         # weights and window counted back from this day
POLL_WINDOW_DAYS = 45                             # field end date within the 45 days up to POLLS_ASOF
POLL_HALF_LIFE = 14.0                             # weight ∝ sqrt(n) × 0.5^(age_days / 14)
POLL_CAP = 0.25                                   # no pollster above 25 % of the weight (1/k if only k < 4)
POLL_SD_HIST = 0.039   # RMSE of final-week poll averages, two-party Dem share, Senate + governor 2016-2022 (167
#                        races, FiveThirtyEight pollster-ratings raw_polls.csv, CC BY 4.0; analysis/us_polls.py)
_SD_EDGES = (2, 5, 10, 10**6)                     # bins of polls in the window: 1-2, 3-5, 6-10, 11+


def poll_sd_bins(hist: pd.DataFrame) -> tuple[tuple[int, float], ...]:
    """((max polls, RMSE), …) of past poll averages (columns n_polls, err) by bin; adjacent bins are pooled while a
    bin with more polls is not more accurate (monotone: more polls never means a larger sd)."""
    groups, lo = [], 0
    for hi in _SD_EDGES:
        e = hist.loc[(hist["n_polls"] > lo) & (hist["n_polls"] <= hi), "err"].to_numpy()
        groups.append([hi, float((e**2).sum()), len(e)])
        lo = hi
    i = 1
    while i < len(groups):
        a, b = groups[i - 1], groups[i]
        if b[2] and a[2] and b[1] / b[2] >= a[1] / a[2]:
            groups[i - 1] = [b[0], a[1] + b[1], a[2] + b[2]]
            del groups[i]
            i = max(i - 1, 1)
        else:
            i += 1
    return tuple((hi, round(float(np.sqrt(s / n)), 4)) for hi, s, n in groups if n)


# analysis/us_polls.py errorbins: our rule on 538 raw_polls 2016-2022, 192 races (CC BY 4.0). Bins 3-5 and 6-10
# pooled (6-10 was not more accurate). Fit on 2016-2020 only, the 2022 races fell inside ±1 sd 70 % and ±1.645 sd 92 %.
POLL_SD_BINS = ((2, 0.0429), (10, 0.0334), (10**6, 0.0228))


def poll_sd_by_n(n_polls: int, bins: tuple[tuple[int, float], ...] = POLL_SD_BINS) -> float:
    """Error sd of a poll average with `n_polls` polls in the window (the historical RMSE of its bin)."""
    return next(sd for hi, sd in bins if n_polls <= hi)


POLLS_PUBLIC = ROOT_DIR / "data_public" / "us2026_polls.json"
_POP_RANK = {"lv": 0, "rv": 1, "a": 2}
_POLL_TYPE = {"senate": "us-senator", "sen": "us-senator", "governor": "governor", "gov": "governor"}


def fetch_polls(day: str | None = None) -> list[dict]:
    """VoteHub Senate and governor polls, cached once per day under data/raw/us/polls."""
    day = day or pd.Timestamp.now(tz="UTC").strftime("%Y%m%d")
    out = []
    for t in ("us-senator", "governor"):
        out += json.loads(_fetch(f"{VOTEHUB}?poll_type={t}", US_RAW / "polls" / f"votehub_{t}_{day}.json"))
    return out


def _person(s: str) -> list[str]:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z]+", s) if w not in ("jr", "sr", "ii", "iii")]


def _same_person(choice: str, name: str) -> bool:
    """Same surname (last word, suffixes dropped)."""
    a, b = _person(choice), _person(name)
    return bool(a and b) and a[-1] == b[-1]


def poll_rows(polls: list[dict], state_name: str, office: str, dem: str, opp: str | None = None) -> pd.DataFrame:
    """One row per poll of the race (subject "2026 <State name>", poll_type of the office) that names the
    Democratic-side candidate: s = dem / (dem + opponent), opponent = `opp` if named in the poll, else the
    highest other candidate. One version per poll (same pollster and field dates): likely voters over registered
    voters over adults, then the version with the most candidates (the full ballot). Polls without a sample size
    are skipped."""
    subj, ptype = f"2026 {state_name}", _POLL_TYPE[office]
    rows = []
    for p in polls:
        if p.get("subject") != subj or p.get("poll_type") != ptype or not p.get("sample_size"):
            continue
        ans = [(a["choice"], float(a["pct"])) for a in p.get("answers") or [] if a.get("pct") is not None]
        d = [x for x in ans if _same_person(x[0], dem)]
        if not d:
            continue
        others = [x for x in ans if x is not d[0]]
        r = [x for x in others if opp and _same_person(x[0], opp)] or sorted(others, key=lambda x: -x[1])[:1]
        if not r or d[0][1] + r[0][1] <= 0:
            continue
        rows.append({"id": p["id"], "pollster": p.get("pollster") or "", "start": p.get("start_date"),
                     "end": p["end_date"], "pop": _POP_RANK.get(p.get("population"), 3), "k": len(ans),
                     "n": float(p["sample_size"]), "dem": d[0][1], "opp": r[0][1],
                     "s": d[0][1] / (d[0][1] + r[0][1])})
    df = pd.DataFrame(rows, columns=["id", "pollster", "start", "end", "pop", "k", "n", "dem", "opp", "s"])
    if df.empty:
        return df
    df = df.sort_values(["pop", "k", "id"], ascending=[True, False, True])
    return df.drop_duplicates(["pollster", "start", "end"]).sort_values("end").reset_index(drop=True)


def _cap(w: pd.Series, by: pd.Series, cap: float) -> pd.Series:
    """Scale weights so no group exceeds `cap` of the total (iterated; excess goes to the others pro rata)."""
    w = w / w.sum()
    cap = max(cap, 1.0 / by.nunique())
    for _ in range(50):
        g = w.groupby(by).transform("sum")
        over = g > cap + 1e-12
        if not over.any():
            break
        w = w.where(~over, w * cap / g)
        free = ~over & (g < cap - 1e-12)
        w[free] *= (1 - w[~free].sum()) / w[free].sum()
    return w / w.sum()


def poll_weights(rows: pd.DataFrame, asof: str = POLLS_ASOF) -> pd.DataFrame:
    """The polls inside the window with their age (days) and normalised weight after the pollster cap (the exact
    weights of poll_average); empty when none."""
    if rows is None or rows.empty:
        return pd.DataFrame()
    t = pd.Timestamp(asof)
    age = (t - pd.to_datetime(rows["end"])).dt.days
    r = rows[(age >= 0) & (age <= POLL_WINDOW_DAYS)].copy()
    if r.empty:
        return r
    r["age"] = age[r.index]
    r["weight"] = _cap(np.sqrt(r["n"]) * 0.5 ** (r["age"] / POLL_HALF_LIFE), r["pollster"], POLL_CAP)
    return r


def poll_average(rows: pd.DataFrame, asof: str = POLLS_ASOF) -> dict | None:
    """Weighted two-party Dem share of the polls ending in the window; None without any. sd = sqrt(POLL_SD_HIST²
    + sampling variance of the weighted mean, Σ w² s(1-s)/n_2party); p_win = Φ((avg - 0.5) / sd)."""
    from scipy.stats import norm
    r = poll_weights(rows, asof)
    if r.empty:
        return None
    w = r["weight"]
    avg = float((w * r["s"]).sum())
    n2 = r["n"] * (r["dem"] + r["opp"]) / 100
    samp = float((w**2 * r["s"] * (1 - r["s"]) / n2).sum())
    sd = float(np.sqrt(POLL_SD_HIST**2 + samp))
    return {"avg": round(avg, 4), "sd": round(sd, 4), "p_win": round(float(norm.cdf((avg - 0.5) / sd)), 4),
            "n_polls": int(len(r)), "n_pollsters": int(r["pollster"].nunique()), "last_date": str(r["end"].max()),
            "asof": asof}


def build_polls(polls: list[dict] | None = None, asof: str = POLLS_ASOF, registry: Path | None = None) -> dict:
    """Poll average per race of the overview registry (all Senate and governor races), Democratic side vs the
    modeled configs' Republican where there is one. Writes data_public/us2026_polls.json (averages only)."""
    polls = fetch_polls() if polls is None else polls
    reg = json.loads((registry or RACES_REGISTRY).read_text())
    opp = {}
    for p in (ROOT_DIR / "configs").glob(f"{EID}_*_v1.toml"):
        E = load_config(p)["election"]
        opp[E["race_id"]] = E["b"]["name"]
    races = {}
    for r in reg["races"]:
        dem = (r.get("dem_side") or {}).get("name")
        a = (poll_average(poll_rows(polls, r["state_name"], r["office"], dem, opp.get(r["race_key"])), asof)
             if dem else None)
        races[r["race_key"]] = a
    out = {"asof": asof, "built": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rule": "docs/us_midterms.md#polls", "source": {"name": "VoteHub Polling API", "url": VOTEHUB,
                                                            "licence": "CC BY 4.0"},
           "error_source": {"name": "FiveThirtyEight pollster ratings, raw_polls.csv (2016-2022 Senate/governor)",
                            "url": "https://github.com/fivethirtyeight/data/tree/master/pollster-ratings",
                            "licence": "CC BY 4.0"},
           "races": races}
    _write_json(POLLS_PUBLIC, out)
    return out


def load_polls() -> dict:
    return json.loads(POLLS_PUBLIC.read_text()).get("races", {}) if POLLS_PUBLIC.exists() else {}


# ------------------------------------------------------------------ early vote by county (display only)
# Official state publications of ballots already cast, by county; not used by the model. Raw files are cached under
# data/raw/us/earlyvote (gitignored); site/data/us2026/earlyvote.json holds county counts only (no voter records).

EV_OUT = "earlyvote.json"
EV_BASELINE = ROOT_DIR / "data_public" / "us2026_ev_baseline.csv"     # 2022 county totals (official), built once
_TX_EVR = "https://goelect.txelections.civixapps.com/api-ivis-system/api/v1/getFile"
_FL_STATS = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats"
_FL_FILES = "https://electionfiles.floridados.gov/countyballotreportfiles"
_NC_S3 = "https://s3.amazonaws.com/dl.ncsbe.gov"
EV_SOURCES = {
    "tx": {"name": "Texas Secretary of State, Early Voting Turnout by county (cumulative in person and by mail)",
           "url": "https://goelect.txelections.civixapps.com/ivis-evr-ui/evr", "licence": "public record",
           "cadence": "daily, close of business of each early-voting day"},
    "fl": {"name": "Florida Division of Elections, vote-by-mail and early voting statistics (voted, by party)",
           "url": _FL_STATS, "licence": "public record", "cadence": "daily, compiled from county reports"},
    "nc": {"name": "NC State Board of Elections, absentee counts by county (mail ballots returned and early "
                   "voting ballots cast, by party)",
           "url": "https://www.ncsbe.gov/results-data/absentee-and-provisional-data", "licence": "public record",
           "cadence": "daily, about 08:40 UTC"},
}
EV_BASE_SOURCES = {
    "tx": "2022 governor, total votes (Texas Legislative Council, data_public/us2026_priors.csv tx_gov_2022)",
    "fl": "2022 governor, total votes (Florida Division of Elections, official results extract)",
    "nc": "2022 US Senate, total votes (NC State Board of Elections, results_pct_20221108)",
}
EV_BLOCKED = {
    "ga": "Georgia SoS absentee voter file (sos.ga.gov / elections.sos.ga.gov): Cloudflare challenge, not circumvented",
    "az": "Arizona SoS (azsos.gov): Cloudflare challenge, not circumvented",
}


_GEO: list[tuple[str, str]] = []


def _geo() -> list[tuple[str, str]]:
    """(unit_id, "Name County, ST") of every county in the dashboard's Census geometry (public domain)."""
    if not _GEO:
        g = json.loads((ROOT_DIR / "site" / "data" / "geo" / "us_counties.topo.json").read_text())
        _GEO.extend((x["properties"]["unit_id"], x["properties"]["name"]) for x in g["objects"]["counties"]["geometries"])
    return _GEO


def geo_units(st: str) -> dict[str, str]:
    """Normalised county name -> unit_id for any state, from the dashboard's Census county geometry."""
    return {_norm(re.sub(r",\s*[A-Z]{2}$", "", name).replace(" Parish", "")): uid
            for uid, name in _geo() if uid.startswith(f"us-{st}-")}


COUNTY_BASELINE = ROOT_DIR / "data_public" / "us2026_county_baseline.csv"
STATE_PRES24 = ROOT_DIR / "data_public" / "us2026_state_pres24.csv"     # official statewide totals (validation)


BASELINE_EXEMPT = {
    "ak": "Alaska reports by state house district, not by borough",
    "us-hi-15005": "Kalawao County: its votes are reported with Maui County",
}
BASELINE_PARTIAL = {
    "la": "MEDSL's Louisiana precinct file holds Election Day votes only (about half the state's votes): parish "
          "shares exclude early and absentee votes",
}
BASELINE_TOLERANCE = 0.02           # county sums must be within 2 % of the official statewide totals


def baseline_state_check() -> dict[str, float]:
    """Per state, max |county-sum / official statewide total − 1| over Harris and Trump (2024)."""
    ref, d = pd.read_csv(STATE_PRES24, comment="#", index_col="state"), load_county_baseline()
    g = d.groupby("state")[["pres24_dem", "pres24_rep"]].sum()
    dev = np.maximum((g["pres24_dem"] / ref["dem"].reindex(g.index) - 1).abs(),
                     (g["pres24_rep"] / ref["rep"].reindex(g.index) - 1).abs())
    return {k: round(float(v), 4) for k, v in dev.items()}


def baseline_gaps(d: pd.DataFrame, geo: dict[str, str]) -> dict[str, list[str]]:
    """Per state, geometry counties without 2024 two-party votes in the baseline (exemptions excluded)."""
    have = set(d.loc[(d["pres24_dem"] + d["pres24_rep"]) > 0, "unit_id"])
    out: dict[str, list[str]] = {}
    for uid in geo:
        st = uid.split("-")[1]
        if st in MEDSL24_STATES and st not in BASELINE_EXEMPT and uid not in BASELINE_EXEMPT and uid not in have:
            out.setdefault(st, []).append(uid)
    return out


def build_county_baseline() -> Path:
    """data_public/us2026_county_baseline.csv: state, unit_id, name, pres24_dem/rep/total, sen24_dem/rep/total
    (2024 regular Senate races only) for every county of the geometry. Fails if any county of a covered state lacks
    2024 two-party votes (except BASELINE_EXEMPT), so a county can never silently drop out of a race map."""
    geo = {uid: name for uid, name in _geo()}
    sen = medsl24_senate().set_index("unit_id")
    parts = []
    for st in MEDSL24_STATES:
        p = medsl24_pres(st).set_index("unit_id")
        units = [u for u in geo if u.startswith(f"us-{st}-")]
        d = pd.DataFrame(index=pd.Index(units, name="unit_id"))
        d["state"], d["name"] = st, [geo[u] for u in units]
        d[["pres24_dem", "pres24_rep", "pres24_total"]] = p.reindex(units)[["dem", "rep", "total"]].to_numpy()
        d[["sen24_dem", "sen24_rep", "sen24_total"]] = sen.reindex(units)[["dem", "rep", "total"]].to_numpy()
        parts.append(d.dropna(subset=["pres24_total"]).reset_index())
    out = pd.concat(parts, ignore_index=True)
    race_states = {r["state"] for r in json.loads(RACES_REGISTRY.read_text())["races"]}
    gaps = {k: v for k, v in baseline_gaps(out, geo).items() if k in race_states}   # states with a 2026 race
    if gaps:
        raise ValueError("county baseline incomplete: " + "; ".join(f"{k}: {len(v)} ({', '.join(v[:4])})"
                                                                   for k, v in gaps.items()))
    ref = _medsl24_state_ref()
    with open(STATE_PRES24, "w") as fh:
        fh.write("# 2024 president, official statewide totals for Harris (dem) and Trump (rep): MIT Election Data and "
                 "Science Lab 2024-president-state.csv (CC0 1.0); used to validate the county sums.\n")
        ref.rename_axis("state").to_csv(fh, float_format="%.0f")
    g = out.groupby("state")[["pres24_dem", "pres24_rep"]].sum()
    dev = np.maximum((g["pres24_dem"] / ref["dem"].reindex(g.index) - 1).abs(),
                     (g["pres24_rep"] / ref["rep"].reindex(g.index) - 1).abs())
    bad = {k: round(float(v), 4) for k, v in dev.items()
           if v > BASELINE_TOLERANCE and k in race_states and k not in BASELINE_PARTIAL}
    if bad:
        raise ValueError(f"county sums differ from the official statewide totals by more than "
                         f"{BASELINE_TOLERANCE:.0%}: {bad}")
    with open(COUNTY_BASELINE, "w") as fh:
        fh.write(f"# County baselines (all states): {MEDSL24_CITE}. Precinct rows (candidates only; Harris and Trump "
                 "by candidate across ballot lines) summed to Census county FIPS by this project, TOTAL/mode rows "
                 "de-duplicated per county; Connecticut towns mapped to the 2022 planning regions (Census 2023 "
                 "gazetteer). Every county of the geometry is present except: "
                 + "; ".join(f"{k} ({v})" for k, v in BASELINE_EXEMPT.items())
                 + ". County sums vs official statewide totals, largest deviations: "
                 + ", ".join(f"{k} {v:.1%}" for k, v in dev.sort_values(ascending=False).items() if v > 0.005)
                 + "; partial: " + "; ".join(f"{k} ({v})" for k, v in BASELINE_PARTIAL.items()) + "\n")
        out.to_csv(fh, index=False, float_format="%.0f")
    return COUNTY_BASELINE


def load_county_baseline() -> pd.DataFrame:
    return pd.read_csv(COUNTY_BASELINE, comment="#")


def _ev_frame(df: pd.DataFrame, st: str) -> pd.DataFrame:
    """name, in_person, mail_returned[, D, R, O] -> unit_id indexed counts (unknown names raise)."""
    units = geo_units(st)
    df = df.assign(unit_id=df["name"].map(lambda n: units.get(_norm(n))))
    if df["unit_id"].isna().any():
        raise ValueError(f"{st}: unknown counties {sorted(df.loc[df['unit_id'].isna(), 'name'])[:5]}")
    df = df.drop(columns="name").groupby("unit_id").sum()
    df["ev_total"] = df["in_person"] + df["mail_returned"]
    return df


def parse_ev_tx(blob: bytes) -> tuple[pd.DataFrame, str]:
    """Texas EVR_EARLYVOTING getFile response ({"upload": base64 JSON}). Cumulative counts at close of business."""
    j = json.loads(blob)
    j = json.loads(base64.b64decode(j["upload"])) if "upload" in j else j
    rows = [r for r in j["turnout_by_county"] if r["name"] != "TOTAL"]
    df = pd.DataFrame({"name": [r["name"] for r in rows],
                       "in_person": [float(r.get("total_in_person_votes_for_election") or 0) for r in rows],
                       "mail_returned": [float(r.get("total_mail_votes_for_election") or 0) for r in rows]})
    return _ev_frame(df, "tx"), j["early_voting_date"]


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)


def parse_ev_fl(vbm_voted: bytes, early_voted: bytes) -> tuple[pd.DataFrame, str]:
    """Florida Stats_<n>_VbmVoted.txt and Stats_<n>_EarlyVoted.txt (tab-separated, per county, by party)."""
    parts = []
    for blob, col in ((vbm_voted, "mail_returned"), (early_voted, "in_person")):
        d = pd.read_csv(io.BytesIO(blob), sep="\t", dtype=str)
        d = d[d["CountyName"].str.strip() != "State Totals"]
        parts.append(pd.DataFrame({"name": d["CountyName"].str.strip(), "in_person": 0.0, "mail_returned": 0.0,
                                   "D": _num(d["TotalDem"]), "R": _num(d["TotalRep"]),
                                   "O": _num(d["TotalOth"]) + _num(d["TotalNpa"])}).assign(**{col: _num(d["GrandTotal"])}))
        stamp = pd.to_datetime(d["CompileDate"].str.split().str[0], format="%m/%d/%Y").max()
        parts[-1]["_t"] = stamp
    as_of = min(p["_t"].iloc[0] for p in parts).strftime("%Y-%m-%d")      # the older of the two compilations
    df = pd.concat([p.drop(columns="_t") for p in parts], ignore_index=True)
    return _ev_frame(df, "fl"), as_of


def parse_ev_nc(counts_csv: bytes, as_of: str) -> tuple[pd.DataFrame, str]:
    """NCSBE absentee_counts_county_<election>.csv: county_name, abs_request_type, party_cd, …, count. Early
    voting = EARLY VOTING + ONE-STOP; mail = CIVILIAN + MILITARY + OVERSEAS."""
    d = pd.read_csv(io.BytesIO(counts_csv), dtype={"county_name": str})
    n = d.columns[-1]
    kind = d["abs_request_type"].str.upper()
    early = kind.isin(["EARLY VOTING", "ONE-STOP"])
    d = pd.DataFrame({"name": d["county_name"], "in_person": d[n].where(early, 0).astype(float),
                      "mail_returned": d[n].where(~early, 0).astype(float),
                      "D": d[n].where(d["party_cd"] == "DEM", 0).astype(float),
                      "R": d[n].where(d["party_cd"] == "REP", 0).astype(float),
                      "O": d[n].where(~d["party_cd"].isin(["DEM", "REP"]), 0).astype(float)})
    return _ev_frame(d, "nc"), as_of


def _get(url: str, **kw) -> bytes:
    with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as c:
        r = c.get(url, **kw)
        r.raise_for_status()
        return r.content


def fetch_ev_tx(today: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    """The latest early-voting day with county counts for the 2026 November general election (a few requests).
    The file of a day not yet reported repeats the previous cumulative counts with zero votes on that day: skipped."""
    lst = json.loads(base64.b64decode(json.loads(_get(_TX_EVR, params={"type": "EVR_ELECTION"}))["upload"]))
    el = next(e for e in lst["elections"] if e["election_date"] == "11/03/2026" and "GENERAL" in e["election_name"])
    days = sorted((pd.Timestamp(d["date"]) for d in el["early_voting_dates"]), reverse=True)
    local = today.tz_convert("America/Chicago").tz_localize(None).normalize()
    for day in [d for d in days if d <= local][:4]:
        blob = _get(_TX_EVR, params={"type": "EVR_EARLYVOTING", "electionId": el["id"],
                                      "electionDate": day.strftime("%m/%d/%Y")})
        j = json.loads(blob)
        if not j.get("upload"):
            continue
        rows = json.loads(base64.b64decode(j["upload"])).get("turnout_by_county") or []
        if sum(r.get("in_person_votes_on_date") or 0 for r in rows) > 0:
            return parse_ev_tx(blob)
    raise ValueError("no Texas early-voting day with county counts yet")


def fetch_ev_fl() -> tuple[pd.DataFrame, str]:
    page = _get(_FL_STATS).decode("utf-8", "replace")
    n = re.search(r"Stats_(\d+)_VbmVoted\.txt", page).group(1)
    return parse_ev_fl(_get(f"{_FL_FILES}/Stats_{n}_VbmVoted.txt"), _get(f"{_FL_FILES}/Stats_{n}_EarlyVoted.txt"))


def fetch_ev_nc() -> tuple[pd.DataFrame, str]:
    """The 23 MB daily counts file is downloaded once per publication (cached by its S3 LastModified)."""
    key = "ENRS/2026_11_03/absentee_counts_county_20261103.csv"
    lst = _get(_NC_S3, params={"list-type": "2", "prefix": key}).decode()
    mod = re.search(r"<LastModified>([^<]+)</LastModified>", lst).group(1)
    f = US_RAW / "earlyvote" / f"nc_counts_{re.sub(r'[^0-9]', '', mod)[:14]}.csv"
    blob = _fetch(f"{_NC_S3}/{key}", f)
    return parse_ev_nc(blob, mod[:10])


def build_ev_baseline() -> Path:
    """data_public/us2026_ev_baseline.csv: state, unit_id, total (2022 county total votes, official sources)."""
    parts = []
    pr = load_priors()
    tx = pr[pr["source"] == "tx_gov_2022"]
    parts.append(pd.DataFrame({"state": "tx", "unit_id": tx["unit_id"], "total": tx["total"]}))
    fl = pd.read_csv(io.BytesIO(_fetch(
        "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=11/8/2022&OfficialResults=Y&DATAMODE=",
        US_RAW / "earlyvote" / "fl_results_20221108.tsv")), sep="\t", dtype=str, encoding="latin1")
    fl = fl[fl["RaceCode"] == "GOV"].assign(v=lambda x: _num(x["CanVotes"])).groupby("CountyName")["v"].sum()
    u = geo_units("fl")
    parts.append(pd.DataFrame({"state": "fl", "unit_id": [u[_norm(n)] for n in fl.index], "total": fl.to_numpy()}))
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"{_NC_S3}/ENRS/2022_11_08/results_pct_20221108.zip",
                                          US_RAW / "earlyvote" / "nc_results_pct_20221108.zip")))
    nc = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str)
    nc = nc[nc["Contest Name"].str.strip() == "US SENATE"].assign(v=lambda x: _num(x["Total Votes"]))
    nc = nc.groupby("County")["v"].sum()
    u = geo_units("nc")
    parts.append(pd.DataFrame({"state": "nc", "unit_id": [u[_norm(n)] for n in nc.index], "total": nc.to_numpy()}))
    out = pd.concat(parts, ignore_index=True)
    with open(EV_BASELINE, "w") as fh:
        fh.write("# 2022 county total votes, the denominator of earlyvote.json share_of_2022_total. Sources (public "
                 "record): " + "; ".join(f"{k}: {v}" for k, v in EV_BASE_SOURCES.items()) + "\n")
        out.to_csv(fh, index=False)
    return EV_BASELINE


def _ev_state(df: pd.DataFrame, base: pd.Series, as_of: str, st: str) -> dict:
    party = {"D", "R", "O"} <= set(df.columns)

    def rec(r: pd.Series, b: float | None) -> dict:
        o = {"ev_total": int(r["ev_total"]), "mail_returned": int(r["mail_returned"]), "in_person": int(r["in_person"]),
             "by_party": {k: int(r[k]) for k in ("D", "R", "O")} if party else None,
             "baseline_2022": None if b is None or not np.isfinite(b) else int(b),
             "share_of_2022_total": None if not b or not np.isfinite(b) else round(float(r["ev_total"]) / b, 4)}
        return o
    counties = {uid: rec(r, base.get(uid)) for uid, r in df.iterrows()}
    tot = df.sum()
    return {"as_of": as_of, "source": EV_SOURCES[st], "baseline": EV_BASE_SOURCES[st],
            "totals": rec(tot, float(base.reindex(df.index).sum())), "counties": counties}


def earlyvote(out: Path, today: pd.Timestamp | None = None, fetchers: dict | None = None) -> Path:
    """site/data/us2026/earlyvote.json. A state that fails keeps its previous section (stale: true)."""
    today = today or pd.Timestamp.now(tz="UTC")
    fetchers = fetchers or {"tx": lambda: fetch_ev_tx(today), "fl": fetch_ev_fl, "nc": fetch_ev_nc}
    if not EV_BASELINE.exists():
        build_ev_baseline()
    b = pd.read_csv(EV_BASELINE, comment="#")
    dest = Path(out) / EID / EV_OUT
    old = json.loads(dest.read_text()) if dest.exists() else {}
    prev = {st: {**s, "counties": {u: {k: v for k, v in c.items() if k != "state"}
                                   for u, c in old.get("counties", {}).items() if c.get("state") == st}}
            for st, s in old.get("states", {}).items()}
    states = {}
    for st, fn in fetchers.items():
        try:
            df, as_of = fn()
            states[st] = _ev_state(df, b[b["state"] == st].set_index("unit_id")["total"], as_of, st)
            states[st].update(ev_implied(st, states[st]))
        except Exception as e:                       # one state failing must not stop the others
            print(f"early vote {st}: {type(e).__name__}: {e}", flush=True)
            if st in prev:
                states[st] = {**prev[st], "stale": True, "error": f"{type(e).__name__}: {e}"}
    _write_json(dest, {
        "built": _iso(today.floor("1min")),
        "note": "Ballots already cast by county from official state publications (display only; not used by the "
                "model). share_of_2022_total = ev_total / the county's 2022 total votes (see baseline). by_party = "
                "party registration of the voters, where the state publishes it (D, R, O = all others). "
                "margin_ev_raw = 100 × (D − R) / all EV ballots (registration is not a vote: loyalty 1, others split "
                "evenly); ev_bias_hist = mean (raw − certified margin) at the same day before Election Day in past "
                "generals (data_public/us2026_ev_history.json), + = early vote overstated the Democrat.",
        "states": {st: {k: v for k, v in s.items() if k != "counties"} for st, s in states.items()},
        "counties": {uid: {"state": st, **c} for st, s in states.items() for uid, c in s["counties"].items()},
        "not_available": EV_BLOCKED,
    })
    return dest


# ------------------------------------------------------------------ national overview (all Senate/governor races)

RACES_REGISTRY = ROOT_DIR / "data_public" / "us2026_races.json"   # analysis/us_overview_races.py


def _market_history(m: dict, grid: pd.DatetimeIndex, days: float) -> tuple[list, list]:
    """(Polymarket, Kalshi) P(YES) on the grid for a registry entry {pm: {token_id}, kalshi: {series, ticker}}.
    Polymarket: CLOB prices-history at 30-min fidelity; Kalshi: hourly candles (mid if spread ≤ 10c, else last
    trade). A market that fails is left empty; it never stops the others."""
    from . import polymarket
    pm_v, k_v = [None] * len(grid), [None] * len(grid)
    pm = m.get("pm") or {}
    if pm.get("token_id"):
        try:
            h = polymarket.price_history(pm["token_id"], grid[0] - pd.Timedelta(hours=6), grid[-1], fidelity_min=30,
                                         chunk=pd.Timedelta(days=days + 1))
            pm_v = _asof(h.set_index("time")["price"].astype(float), grid)
        except Exception as e:
            print(f"overview {m.get('race_key') or pm.get('slug')}: Polymarket history failed: {e}", flush=True)
    km = m.get("kalshi") or {}
    if km.get("ticker"):
        try:
            c = kalshi.candlesticks(km["series"], km["ticker"], grid[0] - pd.Timedelta(hours=6), grid[-1], 60)
            if len(c):
                k_v = [None if not np.isfinite(x) else float(x)
                       for x in kalshi.prob_from_candles(c, grid).to_numpy(dtype=float)]
        except Exception as e:
            print(f"overview {m.get('race_key') or km.get('ticker')}: Kalshi history failed: {e}", flush=True)
    return pm_v, k_v


def _last(v: list) -> float | None:
    return next((x for x in reversed(v) if x is not None), None)


def overview(now: pd.Timestamp, days: float = 14, step: str = "30min", registry: Path | None = None) -> dict:
    """overview.json: every 2026 Senate and governor race (registry data_public/us2026_races.json) with its
    Polymarket and Kalshi P(dem_side), 14 days of history every 30 minutes, Senate holdovers and the chamber control
    markets. Prices only; the county model covers the races in configs/us2026_*_v1.toml (modeled = true)."""
    reg = json.loads((registry or RACES_REGISTRY).read_text())
    modeled = {load_config(p)["election"]["race_id"] for p in (ROOT_DIR / "configs").glob(f"{EID}_*_v1.toml")}
    polls = load_polls()                                          # brunder us polls (data_public/us2026_polls.json)
    grid = pd.date_range(now - pd.Timedelta(days=days), now, freq=step)

    def entry(r: dict) -> dict:
        pm_v, k_v = _market_history(r, grid, days)
        out = {k: v for k, v in r.items() if k not in ("pm", "kalshi")}
        out["modeled"] = r["race_key"] in modeled                 # configs with the county model, as of this run
        out["race_id"] = r["race_key"] if out["modeled"] else None
        pa = polls.get(r["race_key"])
        out["polls"] = None if not pa else {k: pa[k] for k in ("avg", "sd", "n_polls", "last_date", "p_win")}
        out["pm"] = None if not r.get("pm") else {**r["pm"], "price": _r(_last(pm_v))}
        out["kalshi"] = None if not r.get("kalshi") else {**r["kalshi"], "price": _r(_last(k_v))}
        out["history"] = [[_iso(t), _r(p), _r(k)] for t, p, k in zip(grid, pm_v, k_v)]
        return out

    def control(chamber: str) -> list[dict]:
        res = []
        for m in reg["control_markets"][chamber]:
            spec = {"pm": {"token_id": m["token_id"]}} if m["platform"] == "polymarket" else \
                   {"kalshi": {"series": m["series"], "ticker": m["ticker"]}}
            pm_v, k_v = _market_history(spec, grid, days)
            v = pm_v if m["platform"] == "polymarket" else k_v
            res.append({**m, "price": _r(_last(v)), "history": [[_iso(t), _r(x)] for t, x in zip(grid, v)]})
        return res

    races = [entry(r) for r in reg["races"]]
    return {
        "as_of": _iso(now), "p_is": "P(dem_side wins): the Democratic candidate, or the flagged independent",
        "offices": {
            "senate": {"holdovers": reg["holdovers"]["senate"], "seats_total": 100, "majority": 51,
                       "control_markets": control("senate"), "races": [r for r in races if r["office"] == "senate"]},
            "governor": {"races": [r for r in races if r["office"] == "governor"]},
            "house": {"control_markets": control("house")}},
        "sources": reg["sources"] + [{"name": "© Kalshi: market prices", "url": "https://kalshi.com",
                                      "licence": "© Kalshi; shown per the project's decision (docs/kalshi.md)"}],
        "notes": [
            "Prices are P(dem_side wins): the Democratic candidate's chance; in races without a competitive Democrat "
            "the main independent is the dem_side (flagged in dem_side.aligned).",
            "history: [time, Polymarket, Kalshi] every 30 minutes over the last 14 days; Polymarket = CLOB price "
            "history, Kalshi = hourly candles (mid if spread ≤ 10c, else last trade). Kalshi party markets pay on "
            "who is sworn in.",
            "Senate control needs 51 seats, or 50 plus the Vice President's tie-breaking vote. holdovers = seats "
            "not up in 2026 (senate.gov); independents caucusing with Democrats are counted under I.",
            "modeled = the race has the county-level election-night model (meta.races[].race_id).",
        ],
    }


def refresh_overview(d: Path, arc: Archive, errors: dict | None = None) -> bool:
    """On the night: update overview.json prices from the recorder's polymarket_books / kalshi_books and add a history
    point at every 30-minute mark since the last one. Returns whether an overview exists. Never raises."""
    f = d / "overview.json"
    if not f.exists():
        return False
    try:
        ov = json.loads(f.read_text())
        times = arc.times()
        if not times:
            return True
        last = max((pd.Timestamp(h[0]) for o in ov["offices"].values() for r in o.get("races", [])
                    for h in r["history"][-1:]), default=None)
        end, now_t = times[-1].floor("30min"), times[-1]
        marks = list(pd.date_range(last + pd.Timedelta("30min"), end, freq="30min")) if last is not None else []
        cache: dict = {}

        def prices(t):
            if t not in cache:
                cache[t] = (pm_prices(arc, arc.at("polymarket_books", t)), kalshi_prices(arc, arc.at("kalshi_books", t)))
            return cache[t]

        def value(m: dict, t, platform: str | None = None):
            pp, kp = prices(t)
            if (platform or "polymarket") == "polymarket":
                return pp.get(f"{m.get('event')}/{m.get('slug')}")
            return kp.get(m.get("ticker"))

        for o in ov["offices"].values():
            for r in o.get("races", []):
                pm, km = r.get("pm") or {}, r.get("kalshi") or {}
                for t in marks:
                    r["history"].append([_iso(t), _r(value(pm, t)), _r(value(km, t, "kalshi"))])
                if pm and value(pm, now_t) is not None:
                    pm["price"] = _r(value(pm, now_t))
                if km and value(km, now_t, "kalshi") is not None:
                    km["price"] = _r(value(km, now_t, "kalshi"))
                if "margins" in r:                   # market-implied margins follow the live prices
                    r["margins"] = race_margins(r)
                    ev = r.get("ev")
                    if ev and None not in (ev.get("margin_ev_implied"), ev.get("margin_ev_corrected"),
                                           r["margins"]["market"]):
                        mk = r["margins"]["market"]
                        ev["market_closer_to_raw_ev"] = bool(abs(mk - ev["margin_ev_implied"])
                                                             < abs(mk - ev["margin_ev_corrected"]))
            for m in o.get("control_markets", []):
                for t in marks:
                    m["history"].append([_iso(t), _r(value(m, t, m["platform"]))])
                if value(m, now_t, m["platform"]) is not None:
                    m["price"] = _r(value(m, now_t, m["platform"]))
        ov["as_of"] = _iso(now_t)
        if "senate" in ov.get("offices", {}):
            ov["offices"]["senate"].update(senate_control(ov))
        _write_json(f, ov)
    except Exception as e:                           # the overview must never block the race files
        if errors is not None:
            errors["overview"] = f"{type(e).__name__}: {e}"
        print(f"overview refresh failed: {type(e).__name__}: {e}", flush=True)
    return True


LIVE_POLL = 0.25          # s between stat() calls of the recorder's index.jsonl and poll markers (no reads unless changed)
NO_MARKER_GRACE = 5.0     # s after a minute boundary before computing it when no recorder publishes a poll marker
UPDATES = ROOT_DIR / "logs" / "us2026_updates.jsonl"
CROSS_LEVELS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)    # brunder.pm_ws.LEVELS


class IndexTail:
    """New complete lines of the recorder's index.jsonl since the last read (a partial last line waits)."""

    def __init__(self, path: Path):
        self.path, self.off, self.stamp = Path(path), 0, None

    def changed(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            return False
        return (st.st_size, st.st_ino) != self.stamp

    def read(self) -> list[dict]:
        try:
            st = self.path.stat()
            if self.stamp and st.st_ino != self.stamp[1]:
                self.off = 0                         # replaced file: start over
            with self.path.open("rb") as fh:
                fh.seek(self.off)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1
        self.off += end
        self.stamp = (self.off, st.st_ino) if end == len(data) else None
        out = []
        for ln in data[:end].splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out


def poll_markers(root: Path) -> dict[str, dict]:
    """<archive>/.poll/<recorder>.json, written by brunder.snap / brunder.kalshi before each sleep:
    {"next": the next poll's start (UTC), "done": when the last poll finished, "interval": s}."""
    out = {}
    for f in (Path(root) / ".poll").glob("*.json"):
        try:
            out[f.stem] = json.loads(f.read_text())
        except (OSError, ValueError):
            pass
    return out


def settled_until(markers: dict[str, dict], now: pd.Timestamp) -> pd.Timestamp | None:
    """Every snapshot named before this time is on disk: the earliest `next` poll start over the recorders. A marker
    older than 3 intervals + 60 s (recorder stopped or stuck) is ignored, so a dead recorder never stalls the model."""
    vals = []
    for d in markers.values():
        try:
            nxt, done = pd.Timestamp(d["next"]), pd.Timestamp(d["done"])
        except (KeyError, ValueError, TypeError):
            continue
        if (now - done).total_seconds() <= 3 * float(d.get("interval", 30)) + 60:
            vals.append(nxt)
    return min(vals) if vals else None


def feed_status(markers: dict[str, dict], now: pd.Timestamp) -> list[dict]:
    """meta.json `feeds`: every feed the recorders report (poll markers), sorted by key: {key, recorder, interval_s,
    last_ok_utc, last_status, stale}; stale = no successful fetch yet, or the last one is older than 3 × its interval
    (a blocked feed then shows as stale instead of silently holding old counts)."""
    out = []
    for rec, d in markers.items():
        for key, f in (d.get("feeds") or {}).items():
            ok = f.get("last_ok")
            iv = float(f.get("interval") or d.get("interval") or 30)
            stale = ok is None or (now - pd.Timestamp(ok)).total_seconds() > 3 * iv
            out.append({"key": key, "recorder": rec, "interval_s": iv, "last_ok_utc": _iso(pd.Timestamp(ok)) if ok else None,
                        "last_status": f.get("last_status"), "stale": bool(stale)})
    return sorted(out, key=lambda x: x["key"])


class UpdateLog:
    """JSONL log of every live pass (analysis/us_latency.py, analysis/us_night_replay.py ab):
      {"kind": "pass", "t0", "written", "compute_s", "end", "snaps": [[key, t, fetched, last_modified], …]}
        snapshots whose minute row was first written by this pass (t = name / poll start, fetched = response
        received, last_modified = the server's header; null where the recorder did not record it)
      {"kind": "race", "race", "wall", "rows", "row_t", "P", "P_v2", …, "pm", "kalshi",
       "cross": [{"v": "P", "level": 0.5, "dir": "up", "row_t"}]}
        per race with new rows: `wall` = when its files were written, the last row's values, and the levels
        (CROSS_LEVELS) each probability column crossed between consecutive rows."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.last: dict[tuple[str, str], tuple[bool, ...]] = {}
        self.seen: dict[str, int] = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, ex: "Exporter", t0: datetime, written: datetime, end: pd.Timestamp | None,
               snaps: list[dict]) -> None:
        if not self.path:
            return
        lines = [{"kind": "pass", "t0": t0.isoformat(), "written": written.isoformat(),
                  "compute_s": round((written - t0).total_seconds(), 3), "end": _iso(end) if end is not None else None,
                  "snaps": [[s.get("key"), s.get("t"), s.get("fetched"), s.get("last_modified")] for s in snaps]}]
        for rid, rows in ex.timeline.items():
            new = rows[self.seen.get(rid, 0):]
            self.seen[rid] = len(rows)
            if not new:
                continue
            cross = []
            for r in new:
                for col in [k for k in r if k == "P" or (k.startswith("P_") and k[2:3] == "v")]:
                    p = r.get(col)
                    if p is None:
                        continue
                    side = tuple(p >= L for L in CROSS_LEVELS)
                    prev = self.last.get((rid, col))
                    self.last[(rid, col)] = side
                    if prev is not None:
                        cross += [{"v": col, "level": L, "dir": "up" if now else "down", "row_t": r["t"]}
                                  for L, was, now in zip(CROSS_LEVELS, prev, side) if was != now]
            last = new[-1]
            lines.append({"kind": "race", "race": rid, "wall": written.isoformat(), "rows": len(new), "row_t": last["t"],
                          **{k: v for k, v in last.items() if k == "P" or k.startswith("P_") or k in ("pm", "kalshi")},
                          "cross": cross})
        with self.path.open("a") as fh:
            fh.write("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines))


async def run_live(cfg_paths: list[Path], out: Path, snap_root: Path | None = None, interval: float = 30.0,
                   stop_at: datetime | None = None, updates: Path | None = UPDATES) -> None:
    """Live, event-driven: a pass (extend the timeline with what the recorder saved, rewrite the contract files) runs
    as soon as a new minute row can be computed: its minute has passed, the recorder's index has a snapshot at or
    after it, and every recorder that publishes a poll marker (<archive>/.poll/) has finished the polls started
    before it, so a row is computed once, from the same snapshots `brunder us export` would use. Without markers a
    minute is computed NO_MARKER_GRACE s after it ends. At least one pass every `interval` s regardless (prices,
    retries). Reads the recorder's archive only."""
    ex = Exporter(cfg_paths, snap_root)
    tail = IndexTail(ex.arc.root / "index.jsonl")
    log = UpdateLog(updates)
    step = ex.step
    pending: list[dict] = []                     # index lines whose minute row is not written yet
    latest: pd.Timestamp | None = None           # newest snapshot name in the index
    done_end: pd.Timestamp | None = None         # the last minute a pass could compute

    def target() -> tuple[pd.Timestamp, pd.Timestamp]:
        """(until for Exporter.update, the last minute row it allows)."""
        now = pd.Timestamp.now(tz="UTC")
        settled = settled_until(poll_markers(ex.arc.root), now)
        until = now - pd.Timedelta(seconds=NO_MARKER_GRACE) if settled is None else min(now, settled - pd.Timedelta(1, "us"))
        end = until if latest is None else min(until, latest + step)
        return until, end.floor(step)

    def take(lines: list[dict]) -> None:
        nonlocal latest
        for x in lines:
            try:
                t = pd.Timestamp(x["t"])
            except (KeyError, ValueError):
                continue
            latest = t if latest is None or t > latest else latest
            pending.append(x)

    take(tail.read())
    last_pass = 0.0
    while stop_at is None or datetime.now(timezone.utc) < stop_at:
        until, end = target()
        if done_end is not None and end <= done_end and asyncio.get_running_loop().time() - last_pass < interval:
            await asyncio.sleep(LIVE_POLL)
            if tail.changed():
                take(tail.read())
            continue
        t0 = datetime.now(timezone.utc)
        last_pass = asyncio.get_running_loop().time()
        try:
            ex.update(until)
            ex.feeds = feed_status(poll_markers(ex.arc.root), pd.Timestamp.now(tz="UTC"))
            ex.write(out)
            written = datetime.now(timezone.utc)
            done_end = end if done_end is None else max(done_end, end)
            shown = [x for x in pending if pd.Timestamp(x["t"]).ceil(step) <= done_end]
            pending[:] = [x for x in pending if pd.Timestamp(x["t"]).ceil(step) > done_end]
            log.record(ex, t0, written, done_end, shown)
            last = {rid: rows[-1] for rid, rows in ex.timeline.items() if rows}
            print(f"{t0:%H:%M:%S} " + "  ".join(f"{rid} c={r['counted']} P={r['P']} pm={r['pm']}"
                                               for rid, r in last.items()), flush=True)
            if ex.errors:
                print(f"{t0:%H:%M:%S} open errors: {ex.errors}", flush=True)
        except Exception as e:                       # never stop on the night
            print(f"{t0:%H:%M:%S} export error, continuing: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(1.0)


# ------------------------------------------------------------------ county estimates for every race (display only)
# Races without a county model get a pre-count county map: 2024 president two-party share per county (MEDSL) moved by
# a uniform swing to the statewide poll average (or, without polls, to the share the market price implies), expected
# votes = EST_TURNOUT × 2024 total. Registered configs and their model are not involved.

EST_TURNOUT = 0.72        # 2026 midterm / 2024 presidential two-party votes, mean of CA/TX/GA official data (docs)
EST_SD_NOPOLL = 0.06      # statewide sd of the 2024-only estimate (no polls, no price)


def county_resid_sd(priors: pd.DataFrame | None = None) -> float:
    """sd of county swings around the statewide swing, Georgia 2020 → 2024 president (official, by county;
    weighted by 2024 two-party votes): the county-level spread of a uniform-swing estimate."""
    pr = load_priors() if priors is None else priors
    a = pr[pr["source"] == "ga_pres_2020"].set_index("unit_id")
    b = pr[pr["source"] == "ga_pres_2024"].set_index("unit_id")
    w = (b["dem"] + b["rep"]).reindex(a.index)
    sw = b["dem"] / (b["dem"] + b["rep"]) - a["dem"] / (a["dem"] + a["rep"])
    ok = sw.notna() & w.notna()
    m = np.average(sw[ok], weights=w[ok])
    return float(np.sqrt(np.average((sw[ok] - m) ** 2, weights=w[ok])))


def _margin(share: float | None) -> float | None:
    return None if share is None else _r((2 * share - 1) * 100, 2)


MARKET_MARGIN_RANGE = (0.05, 0.95)   # outside it a price says who wins, not by how much


def market_margin(p: float | None, margin_sd: float | None) -> float | None:
    """The D−R margin (points) the market price implies at our uncertainty: Φ⁻¹(p) × σ_margin. None outside
    MARKET_MARGIN_RANGE: near 0 or 1 the probit saturates (0.99 at σ 8 reads as D+19 whatever the real margin),
    which would show safe seats as false swings or false model-market gaps."""
    from scipy.stats import norm
    if p is None or margin_sd is None or not np.isfinite(p) or not MARKET_MARGIN_RANGE[0] <= p <= MARKET_MARGIN_RANGE[1]:
        return None
    return _r(float(norm.ppf(p)) * margin_sd, 2)


def race_estimate(r: dict, base: pd.DataFrame, poll: dict | None, resid: float) -> tuple[dict, dict] | None:
    """(estimate summary, units object) for an overview race from the county baseline; None without baseline.
    With polls: the 2024 county shares shifted uniformly to the poll average. Without polls: the 2024 shares
    unshifted ("past results (no recent polls)"); never a market-implied share (that would show the price back)."""
    from scipy.stats import norm
    b = base[(base["state"] == r["state"]) & ((base["pres24_dem"] + base["pres24_rep"]) > 0)]   # '*' (suppressed)
    if b.empty:
        return None
    s0 = (b["pres24_dem"] / (b["pres24_dem"] + b["pres24_rep"])).to_numpy()
    V = (EST_TURNOUT * b["pres24_total"]).to_numpy()
    base_state = float(np.average(s0, weights=V))
    if poll:
        target, sd, basis = poll["avg"], poll["sd"], "polls"
    else:
        target, sd, basis = base_state, EST_SD_NOPOLL, "past results (no recent polls)"
    share = np.clip(s0 + target - base_state, 0.01, 0.99)
    csd = float(np.sqrt(sd**2 + resid**2))
    note = {"polls": "estimate: 2024 president by county shifted uniformly to the poll average",
            "past results (no recent polls)": "past results (no recent polls): 2024 president by county"}[basis]
    if r["state"] in BASELINE_PARTIAL:
        note += "; caveat: " + BASELINE_PARTIAL[r["state"]]
    est = {"basis": basis, "share": _r(target), "sd": _r(sd), "county_sd": _r(csd),
           "P": _r(float(norm.cdf((target - 0.5) / sd))), "margin": _margin(target), "margin_sd": _r(2 * sd * 100, 2),
           "baseline_share": _r(base_state), "turnout": _tens(V.sum()), "note": note}
    names = b["name"].str.replace(r",\s*[A-Z]{2}$", "", regex=True).tolist()
    units = {"race_id": r["race_key"], "estimate": est, "unit_ids": b["unit_id"].tolist(),
             "region": [r["state"]] * len(b), "names": names,
             "prior": (s0 * 1000).round().astype(int).tolist(),
             "prior_margin": ((2 * s0 - 1) * 1000).round().astype(int).tolist(),
             "V0": V.round().astype(int).tolist(),
             "sd": [int(round(csd * 1000))] * len(b),
             "frames": [{"t": None, "f": [0] * len(b), "a": [-1] * len(b),
                         "proj": (share * 1000).round().astype(int).tolist(),
                         "rem": _tens_list(V), "rem_margin": _tens_list(V * (2 * share - 1))}]}
    return est, units


def race_margins(r: dict) -> dict:
    """Margin-scale comparison for an overview race, D − R in points of the two-party vote. σ for the market's
    implied margin = the model's pre-count margin sd (modeled races), else the poll average's, else the estimate's."""
    pa, est, pre = r.get("polls"), r.get("estimate"), r.get("precount") or {}
    sd = (2 * pre["sd"] * 100 if pre.get("sd") else None) or (2 * pa["sd"] * 100 if pa else None) \
        or (est or {}).get("margin_sd")
    pm, km = (r.get("pm") or {}).get("price"), (r.get("kalshi") or {}).get("price")
    return {"model": _margin(pre.get("share")), "model_sd": _r(2 * pre["sd"] * 100, 2) if pre.get("sd") else None,
            "model_v2": _margin(pre.get("share_v2")),
            "model_polls": _margin(pre.get(f"share_{pre['poll_model']}")) if pre.get("poll_model") else None,
            "poll_model": pre.get("poll_model"), "polls": _margin(pa["avg"]) if pa else None,
            "polls_sd": _r(2 * pa["sd"] * 100, 2) if pa else None,
            "estimate": (est or {}).get("margin"), "market": market_margin(pm, sd),
            "market_kalshi": market_margin(km, sd), "market_sd_used": _r(sd, 2) if sd else None}


def add_estimates(d: Path, ov: dict, pre: dict[str, dict | None]) -> dict:
    """Overview races: units file + estimate for races without a county model, margins for all, early-vote block."""
    base, polls = load_county_baseline(), load_polls()
    resid = county_resid_sd()
    ev = json.loads((d / EV_OUT).read_text()) if (d / EV_OUT).exists() else {}
    for o in ov["offices"].values():
        for r in o.get("races", []):
            rk = r["race_key"]
            r["precount"] = pre.get(rk) if r.get("modeled") else None
            if r.get("modeled"):
                r["units"] = f"units/{rk}.json" if pre.get(rk) else None
            else:
                res = race_estimate(r, base, polls.get(rk), resid)
                r["estimate"], r["units"] = (res[0], f"units/{rk}.json") if res else (None, None)
                if res:
                    _write_json(d / "units" / f"{rk}.json", res[1])
            r["margins"] = race_margins(r)
            r["ev"] = ev_block(ev.get("states", {}).get(r["state"]), r["margins"])
    if "senate" in ov.get("offices", {}):
        ov["offices"]["senate"].update(senate_control(ov))
    return ov


SENATE_RHO = 0.21   # cross-race correlation of poll-average errors, 538 raw_polls 2016-2022 (docs/us_model_analysis.md)


def senate_control(ov: dict, rho: float = SENATE_RHO, n_sim: int = 100_000) -> dict:
    """P(Democratic-caucus Senate majority: ≥ 51 seats, the VP being Republican) from this cycle's races — poll
    average with the v3 error for its number of polls where polled, else the Polymarket price's implied share
    (0.5 + Φ⁻¹(p)·0.039) — with races independent and with a common error (correlation rho). Display only."""
    from scipy.stats import norm
    sen = ov["offices"]["senate"]
    hold = sen.get("holdovers") or {}
    d_hold = int(hold.get("D", 0) + hold.get("I", 0))
    mu, sd = [], []
    for r in sen.get("races", []):
        pa = r.get("polls")
        if pa:
            mu.append(pa["avg"]), sd.append(poll_sd_by_n(int(pa["n_polls"])))
        else:
            p = (r.get("pm") or {}).get("price")
            p = 0.5 if p is None else min(max(float(p), 0.005), 0.995)
            mu.append(0.5 + float(norm.ppf(p)) * POLL_SD_HIST), sd.append(POLL_SD_HIST)
    if not mu:
        return {}
    mu, sd = np.array(mu), np.array(sd)
    rng = np.random.default_rng(2026)
    out = {}
    for key, r in (("indep", 0.0), ("corr", rho)):
        z = np.sqrt(r) * rng.standard_normal((n_sim, 1)) + np.sqrt(1 - r) * rng.standard_normal((n_sim, len(mu)))
        seats = d_hold + ((mu + sd * z) > 0.5).sum(axis=1)
        out[f"control_p_{key}"] = _r(float(np.mean(seats >= 51)), 3)
        out[f"seats_80_{key}"] = [int(np.percentile(seats, 10)), int(np.percentile(seats, 90))]
    out["control_note"] = (f"Chance the Democratic caucus (with independents) holds at least 51 seats: {d_hold} seats "
                           f"not up plus each race's poll average (v3 error by number of polls) or, without polls, the "
                           f"Polymarket price. 'corr' adds a polling error shared by all races (correlation {rho}, "
                           "from 2016-2022 races); 'indep' treats races as independent.")
    return out


# ------------------------------------------------------------------ early-vote implied margin and its history
# Registration ≠ vote: the raw EV-implied margin assumes every registered Democrat/Republican votes their party and
# unaffiliated/other voters split evenly; the historical bias (same day before Election Day, past elections) absorbs
# both loyalty and the unaffiliated split, which two past elections cannot identify separately.

EV_HISTORY = ROOT_DIR / "data_public" / "us2026_ev_history.json"
EV_ELECTION_DAY = pd.Timestamp("2026-11-03")


def ev_raw_margin(by_party: dict | None) -> float | None:
    if not by_party:
        return None
    n = by_party["D"] + by_party["R"] + by_party["O"]
    return _r(100 * (by_party["D"] - by_party["R"]) / n, 2) if n else None


def nc_ev_by_date(zip_path: Path) -> pd.DataFrame:
    """NC absentee file (one row per ballot): accepted mail + one-stop ballots by return date and party (D, R, O)."""
    z = zipfile.ZipFile(zip_path)
    parts = []
    for ch in pd.read_csv(z.open(z.namelist()[0]), dtype=str, chunksize=1_000_000, encoding="latin1",
                          usecols=["voter_party_code", "ballot_rtn_dt", "ballot_rtn_status"]):
        ch = ch[ch["ballot_rtn_status"].fillna("").str.upper().str.startswith("ACCEPTED")]
        p = ch["voter_party_code"].fillna("").map({"DEM": "D", "REP": "R"}).fillna("O")
        dt = pd.to_datetime(ch["ballot_rtn_dt"], format="%m/%d/%Y", errors="coerce")
        parts.append(pd.crosstab(dt, p))
    return pd.concat(parts).groupby(level=0).sum().reindex(columns=["D", "R", "O"], fill_value=0).sort_index()


def build_ev_history(max_days: int = 21) -> Path:
    """data_public/us2026_ev_history.json: NC raw EV-implied margin k = 0…max_days days before Election Day (ballots
    returned by ED − k) in the 2022 and 2024 generals vs the certified two-party margin of the top race (2022 US
    Senate, 2024 president). bias = raw − final (points; + = EV overstated the Democrat)."""
    pres = medsl24_pres("nc")
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"{_NC_S3}/ENRS/2022_11_08/results_pct_20221108.zip",
                                          US_RAW / "earlyvote" / "nc_results_pct_20221108.zip")))
    r22 = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str)
    r22 = r22[r22["Contest Name"].str.strip() == "US SENATE"].assign(v=lambda x: _num(x["Total Votes"]))
    by = r22.groupby(r22["Choice Party"].str.strip())["v"].sum()
    finals = {2022: (by.get("DEM", 0), by.get("REP", 0), "2022 US Senate"),
              2024: (pres["dem"].sum(), pres["rep"].sum(), "2024 president")}
    hist: dict[str, dict] = {}
    for year, ed, key in ((2022, "2022-11-08", "2022_11_08/absentee_20221108.zip"),
                          (2024, "2024-11-05", "2024_11_05/absentee_20241105.zip")):
        f = US_RAW / "earlyvote" / f"nc_{Path(key).name}"
        _fetch(f"{_NC_S3}/ENRS/{key}", f)
        cum = nc_ev_by_date(f).cumsum()
        d, rr, race = finals[year]
        final = _r(100 * (d - rr) / (d + rr), 2)
        for k in range(max_days + 1):
            through = pd.Timestamp(ed) - pd.Timedelta(days=k)
            row = cum[cum.index <= through]
            if row.empty:
                continue
            bp = {c: int(row[c].iloc[-1]) for c in ("D", "R", "O")}
            raw = ev_raw_margin(bp)
            hist.setdefault(str(k), {"elections": []})["elections"].append(
                {"year": year, "through": through.strftime("%Y-%m-%d"), "by_party": bp, "raw_margin": raw,
                 "final_race": race, "final_margin": final, "bias": _r(raw - final, 2)})
    for h in hist.values():
        h["bias_mean"] = _r(float(np.mean([e["bias"] for e in h["elections"]])), 2)
    out = {"rule": "raw EV-implied margin = 100 × (D − R) / all accepted EV ballots, by the voters' party "
                   "registration; bias = raw − certified two-party margin of the top race; key = days before "
                   "Election Day of the data's last return date",
           "states": {"nc": {"source": "NC State Board of Elections, per-ballot absentee files 2022 and 2024 "
                                       "(dl.ncsbe.gov, public record); finals: NCSBE 2022 results, MEDSL 2024",
                             "by_days_before": hist}},
           "not_available": {"fl": "Florida's daily statistics files are overwritten (only the latest compilation is "
                                   "published); no dated history found",
                             "tx": "Texas publishes no party registration: volume only"}}
    _write_json(EV_HISTORY, out)
    return EV_HISTORY


def ev_implied(st: str, s: dict, history: dict | None = None) -> dict:
    """margin_ev_raw from the state's EV party split; ev_bias_hist and days_before from the history at the same day
    before Election Day (the state's data run through the day before its as_of date)."""
    raw = ev_raw_margin(s["totals"].get("by_party"))
    hist = history if history is not None else (json.loads(EV_HISTORY.read_text()) if EV_HISTORY.exists() else {})
    k = (EV_ELECTION_DAY - (pd.Timestamp(s["as_of"]) - pd.Timedelta(days=1))).days
    h = hist.get("states", {}).get(st, {}).get("by_days_before", {}).get(str(k))
    return {"margin_ev_raw": raw, "days_before": k, "ev_bias_hist": h["bias_mean"] if h and raw is not None else None,
            "ev_hist": h["elections"] if h and raw is not None else None}


def ev_block(s: dict | None, margins: dict) -> dict | None:
    """Per race: early vote of its state (volume, raw EV-implied margin, historical bias, corrected margin) and the
    over-reliance flag: the market-implied margin is closer to the raw than to the bias-corrected EV margin."""
    if not s:
        return None
    t = s.get("totals", {})
    raw, bias = s.get("margin_ev_raw"), s.get("ev_bias_hist")
    corr = None if raw is None or bias is None else _r(raw - bias, 2)
    mk = margins.get("market")
    flag = None if None in (raw, corr, mk) else bool(abs(mk - raw) < abs(mk - corr))
    return {"as_of": s.get("as_of"), "ev_total": t.get("ev_total"), "share_of_2022_total": t.get("share_of_2022_total"),
            "margin_ev_implied": raw, "ev_bias_hist": bias, "margin_ev_corrected": corr,
            "market_closer_to_raw_ev": flag}


# ------------------------------------------------------------------ scoring (after the count): three separate scores

def score_race(rows: list[dict], final_share: float, final_votes: float) -> dict:
    """Score a race's timeline rows against the certified result, never mixing the three questions:
    margin (model margin − final, points), turnout (projected total votes − final, relative) and probability
    (Brier and log loss of P against the outcome). Rows without a value are skipped per score."""
    fm, won = (2 * final_share - 1) * 100, float(final_share > 0.5)
    m = [(r.get("counted"), r["margin_model"] - fm) for r in rows if r.get("margin_model") is not None]
    t = [(r.get("counted"), r["votes_proj"] / final_votes - 1) for r in rows if r.get("votes_proj")]
    p = [min(max(r["P"], 1e-6), 1 - 1e-6) for r in rows if r.get("P") is not None]

    def summ(x):
        if not x:
            return None
        e = np.array([v for _, v in x])
        return {"n": len(e), "mean": _r(float(e.mean())), "mae": _r(float(np.abs(e).mean())), "last": _r(float(e[-1]))}
    return {"margin_pts": summ(m), "turnout_rel": summ(t),
            "probability": None if not p else {
                "n": len(p), "brier": _r(float(np.mean([(q - won) ** 2 for q in p]))),
                "log_loss": _r(float(-np.mean([np.log(q if won else 1 - q) for q in p])))}}


def cli(a) -> None:
    """`brunder us priors|preelection|export|live …` (see src/brunder/cli.py)."""
    match a.step:
        case "priors":
            print(build_priors(a.source or None))
            print(write_public_priors())
        case "preelection":
            print(preelection(a.configs, a.out, days=a.days, force=a.force))
            # early-vote files change about daily and NC's is ~23 MB: refresh only when asked (the CI workflow
            # sets BRUNDER_EARLYVOTE=1 four times a day); `us earlyvote` always refreshes
            if __import__("os").environ.get("BRUNDER_EARLYVOTE") == "1":
                try:
                    print(earlyvote(a.out))
                except Exception as e:
                    print(f"early vote not refreshed: {type(e).__name__}: {e}", flush=True)
        case "earlyvote":
            print(earlyvote(a.out))
        case "export":
            print(export(a.configs, a.out, a.snap, a.until))
        case "score":
            import sys
            sys.path.insert(0, str(ROOT_DIR / "analysis"))
            import us_score
            print(us_score.run(a.out, a.snap, a.configs or None))
        case "live":
            stop = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
            asyncio.run(run_live(a.configs, a.out, a.snap, a.interval, stop, a.updates or UPDATES))


# ------------------------------------------------------------------ Census 2020 county codes (public domain)
# "<name>:<county FIPS>", Texas in Texas SoS county-number order; generated from
# https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt (tests check it against the file).

_COUNTIES = {
    "az": (
        "Apache:001,Cochise:003,Coconino:005,Gila:007,Graham:009,Greenlee:011,La Paz:012,Maricopa:013,"
        "Mohave:015,Navajo:017,Pima:019,Pinal:021,Santa Cruz:023,Yavapai:025,Yuma:027"
    ),
    "sc": (
        "Abbeville:001,Aiken:003,Allendale:005,Anderson:007,Bamberg:009,Barnwell:011,Beaufort:013,"
        "Berkeley:015,Calhoun:017,Charleston:019,Cherokee:021,Chester:023,Chesterfield:025,Clarendon:027,"
        "Colleton:029,Darlington:031,Dillon:033,Dorchester:035,Edgefield:037,Fairfield:039,Florence:041,"
        "Georgetown:043,Greenville:045,Greenwood:047,Hampton:049,Horry:051,Jasper:053,Kershaw:055,"
        "Lancaster:057,Laurens:059,Lee:061,Lexington:063,McCormick:065,Marion:067,Marlboro:069,Newberry:071,"
        "Oconee:073,Orangeburg:075,Pickens:077,Richland:079,Saluda:081,Spartanburg:083,Sumter:085,Union:087,"
        "Williamsburg:089,York:091"
    ),
    "vt": (
        "Addison:001,Bennington:003,Caledonia:005,Chittenden:007,Essex:009,Franklin:011,Grand Isle:013,"
        "Lamoille:015,Orange:017,Orleans:019,Rutland:021,Washington:023,Windham:025,Windsor:027"
    ),
    "ca": (
        "Alameda:001,Alpine:003,Amador:005,Butte:007,Calaveras:009,Colusa:011,Contra Costa:013,Del Norte:015,"
        "El Dorado:017,Fresno:019,Glenn:021,Humboldt:023,Imperial:025,Inyo:027,Kern:029,Kings:031,Lake:033,"
        "Lassen:035,Los Angeles:037,Madera:039,Marin:041,Mariposa:043,Mendocino:045,Merced:047,Modoc:049,"
        "Mono:051,Monterey:053,Napa:055,Nevada:057,Orange:059,Placer:061,Plumas:063,Riverside:065,"
        "Sacramento:067,San Benito:069,San Bernardino:071,San Diego:073,San Francisco:075,San Joaquin:077,"
        "San Luis Obispo:079,San Mateo:081,Santa Barbara:083,Santa Clara:085,Santa Cruz:087,Shasta:089,"
        "Sierra:091,Siskiyou:093,Solano:095,Sonoma:097,Stanislaus:099,Sutter:101,Tehama:103,Trinity:105,"
        "Tulare:107,Tuolumne:109,Ventura:111,Yolo:113,Yuba:115"
    ),
    "tx": (
        "Anderson:001,Andrews:003,Angelina:005,Aransas:007,Archer:009,Armstrong:011,Atascosa:013,Austin:015,"
        "Bailey:017,Bandera:019,Bastrop:021,Baylor:023,Bee:025,Bell:027,Bexar:029,Blanco:031,Borden:033,"
        "Bosque:035,Bowie:037,Brazoria:039,Brazos:041,Brewster:043,Briscoe:045,Brooks:047,Brown:049,"
        "Burleson:051,Burnet:053,Caldwell:055,Calhoun:057,Callahan:059,Cameron:061,Camp:063,Carson:065,"
        "Cass:067,Castro:069,Chambers:071,Cherokee:073,Childress:075,Clay:077,Cochran:079,Coke:081,"
        "Coleman:083,Collin:085,Collingsworth:087,Colorado:089,Comal:091,Comanche:093,Concho:095,Cooke:097,"
        "Coryell:099,Cottle:101,Crane:103,Crockett:105,Crosby:107,Culberson:109,Dallam:111,Dallas:113,"
        "Dawson:115,Deaf Smith:117,Delta:119,Denton:121,DeWitt:123,Dickens:125,Dimmit:127,Donley:129,"
        "Duval:131,Eastland:133,Ector:135,Edwards:137,Ellis:139,El Paso:141,Erath:143,Falls:145,Fannin:147,"
        "Fayette:149,Fisher:151,Floyd:153,Foard:155,Fort Bend:157,Franklin:159,Freestone:161,Frio:163,"
        "Gaines:165,Galveston:167,Garza:169,Gillespie:171,Glasscock:173,Goliad:175,Gonzales:177,Gray:179,"
        "Grayson:181,Gregg:183,Grimes:185,Guadalupe:187,Hale:189,Hall:191,Hamilton:193,Hansford:195,"
        "Hardeman:197,Hardin:199,Harris:201,Harrison:203,Hartley:205,Haskell:207,Hays:209,Hemphill:211,"
        "Henderson:213,Hidalgo:215,Hill:217,Hockley:219,Hood:221,Hopkins:223,Houston:225,Howard:227,"
        "Hudspeth:229,Hunt:231,Hutchinson:233,Irion:235,Jack:237,Jackson:239,Jasper:241,Jeff Davis:243,"
        "Jefferson:245,Jim Hogg:247,Jim Wells:249,Johnson:251,Jones:253,Karnes:255,Kaufman:257,Kendall:259,"
        "Kenedy:261,Kent:263,Kerr:265,Kimble:267,King:269,Kinney:271,Kleberg:273,Knox:275,Lamar:277,Lamb:279,"
        "Lampasas:281,La Salle:283,Lavaca:285,Lee:287,Leon:289,Liberty:291,Limestone:293,Lipscomb:295,"
        "Live Oak:297,Llano:299,Loving:301,Lubbock:303,Lynn:305,Madison:313,Marion:315,Martin:317,Mason:319,"
        "Matagorda:321,Maverick:323,McCulloch:307,McLennan:309,McMullen:311,Medina:325,Menard:327,"
        "Midland:329,Milam:331,Mills:333,Mitchell:335,Montague:337,Montgomery:339,Moore:341,Morris:343,"
        "Motley:345,Nacogdoches:347,Navarro:349,Newton:351,Nolan:353,Nueces:355,Ochiltree:357,Oldham:359,"
        "Orange:361,Palo Pinto:363,Panola:365,Parker:367,Parmer:369,Pecos:371,Polk:373,Potter:375,"
        "Presidio:377,Rains:379,Randall:381,Reagan:383,Real:385,Red River:387,Reeves:389,Refugio:391,"
        "Roberts:393,Robertson:395,Rockwall:397,Runnels:399,Rusk:401,Sabine:403,San Augustine:405,"
        "San Jacinto:407,San Patricio:409,San Saba:411,Schleicher:413,Scurry:415,Shackelford:417,Shelby:419,"
        "Sherman:421,Smith:423,Somervell:425,Starr:427,Stephens:429,Sterling:431,Stonewall:433,Sutton:435,"
        "Swisher:437,Tarrant:439,Taylor:441,Terrell:443,Terry:445,Throckmorton:447,Titus:449,Tom Green:451,"
        "Travis:453,Trinity:455,Tyler:457,Upshur:459,Upton:461,Uvalde:463,Val Verde:465,Van Zandt:467,"
        "Victoria:469,Walker:471,Waller:473,Ward:475,Washington:477,Webb:479,Wharton:481,Wheeler:483,"
        "Wichita:485,Wilbarger:487,Willacy:489,Williamson:491,Wilson:493,Winkler:495,Wise:497,Wood:499,"
        "Yoakum:501,Young:503,Zapata:505,Zavala:507"
    ),
    "oh": (
        "Adams:001,Allen:003,Ashland:005,Ashtabula:007,Athens:009,Auglaize:011,Belmont:013,Brown:015,"
        "Butler:017,Carroll:019,Champaign:021,Clark:023,Clermont:025,Clinton:027,Columbiana:029,"
        "Coshocton:031,Crawford:033,Cuyahoga:035,Darke:037,Defiance:039,Delaware:041,Erie:043,Fairfield:045,"
        "Fayette:047,Franklin:049,Fulton:051,Gallia:053,Geauga:055,Greene:057,Guernsey:059,Hamilton:061,"
        "Hancock:063,Hardin:065,Harrison:067,Henry:069,Highland:071,Hocking:073,Holmes:075,Huron:077,"
        "Jackson:079,Jefferson:081,Knox:083,Lake:085,Lawrence:087,Licking:089,Logan:091,Lorain:093,Lucas:095,"
        "Madison:097,Mahoning:099,Marion:101,Medina:103,Meigs:105,Mercer:107,Miami:109,Monroe:111,"
        "Montgomery:113,Morgan:115,Morrow:117,Muskingum:119,Noble:121,Ottawa:123,Paulding:125,Perry:127,"
        "Pickaway:129,Pike:131,Portage:133,Preble:135,Putnam:137,Richland:139,Ross:141,Sandusky:143,"
        "Scioto:145,Seneca:147,Shelby:149,Stark:151,Summit:153,Trumbull:155,Tuscarawas:157,Union:159,"
        "Van Wert:161,Vinton:163,Warren:165,Washington:167,Wayne:169,Williams:171,Wood:173,Wyandot:175"
    ),
    "ga": (
        "Appling:001,Atkinson:003,Bacon:005,Baker:007,Baldwin:009,Banks:011,Barrow:013,Bartow:015,"
        "Ben Hill:017,Berrien:019,Bibb:021,Bleckley:023,Brantley:025,Brooks:027,Bryan:029,Bulloch:031,"
        "Burke:033,Butts:035,Calhoun:037,Camden:039,Candler:043,Carroll:045,Catoosa:047,Charlton:049,"
        "Chatham:051,Chattahoochee:053,Chattooga:055,Cherokee:057,Clarke:059,Clay:061,Clayton:063,Clinch:065,"
        "Cobb:067,Coffee:069,Colquitt:071,Columbia:073,Cook:075,Coweta:077,Crawford:079,Crisp:081,Dade:083,"
        "Dawson:085,Decatur:087,DeKalb:089,Dodge:091,Dooly:093,Dougherty:095,Douglas:097,Early:099,"
        "Echols:101,Effingham:103,Elbert:105,Emanuel:107,Evans:109,Fannin:111,Fayette:113,Floyd:115,"
        "Forsyth:117,Franklin:119,Fulton:121,Gilmer:123,Glascock:125,Glynn:127,Gordon:129,Grady:131,"
        "Greene:133,Gwinnett:135,Habersham:137,Hall:139,Hancock:141,Haralson:143,Harris:145,Hart:147,"
        "Heard:149,Henry:151,Houston:153,Irwin:155,Jackson:157,Jasper:159,Jeff Davis:161,Jefferson:163,"
        "Jenkins:165,Johnson:167,Jones:169,Lamar:171,Lanier:173,Laurens:175,Lee:177,Liberty:179,Lincoln:181,"
        "Long:183,Lowndes:185,Lumpkin:187,McDuffie:189,McIntosh:191,Macon:193,Madison:195,Marion:197,"
        "Meriwether:199,Miller:201,Mitchell:205,Monroe:207,Montgomery:209,Morgan:211,Murray:213,Muscogee:215,"
        "Newton:217,Oconee:219,Oglethorpe:221,Paulding:223,Peach:225,Pickens:227,Pierce:229,Pike:231,"
        "Polk:233,Pulaski:235,Putnam:237,Quitman:239,Rabun:241,Randolph:243,Richmond:245,Rockdale:247,"
        "Schley:249,Screven:251,Seminole:253,Spalding:255,Stephens:257,Stewart:259,Sumter:261,Talbot:263,"
        "Taliaferro:265,Tattnall:267,Taylor:269,Telfair:271,Terrell:273,Thomas:275,Tift:277,Toombs:279,"
        "Towns:281,Treutlen:283,Troup:285,Turner:287,Twiggs:289,Union:291,Upson:293,Walker:295,Walton:297,"
        "Ware:299,Warren:301,Washington:303,Wayne:305,Webster:307,Wheeler:309,White:311,Whitfield:313,"
        "Wilcox:315,Wilkes:317,Wilkinson:319,Worth:321"
    ),
}
