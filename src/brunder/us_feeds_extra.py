"""More US 2026 election-night county feeds (added 2026-10-30, before any 2026 general-election count): Pennsylvania,
Minnesota, Nebraska and New Mexico, plus the county priors of these races and of Arizona governor (whose feed, AZ
Enhanced Voting, brunder.us already parses). Each feed is an open official endpoint that answers plain requests from
the recorder VM; it is recorded with `brunder snap --url <URL>` (URLS) and read back by key (FEEDS). brunder.us merges
FEEDS, STATES, PRIOR_SOURCES, SOURCE_INFO and POLL_SOURCES and dispatches parse_feed; existing states are untouched.

  pa  Pennsylvania DoS election returns API (www.electionreturns.pa.gov/api/ElectionReturn/GetCountyBreak): governor by
      county with Election Day / mail-in / provisional votes per candidate; no precinct counts in this file.
  mn  Minnesota SoS media file allracesbycounty.txt (electionresultsfiles.sos.mn.gov/<YYYYMMDD>/): every statewide race
      by county, semicolon-separated, with precincts reporting / total; ISO-8859-1 text.
  ne  Nebraska SoS results web service (neresultswebservice.azurewebsites.us/ResultsAjax.svc/GetMapData, the JSON
      behind electionresults.nebraska.gov's county maps): one race by county, precincts reporting / total.
  nm  New Mexico SoS results web service (nmresultswebservice.azurewebsites.us/NMResultsAjax.svc/GetMapData), same
      vendor and schema as Nebraska.
Statewide rows are the county sums. See docs/us_midterms.md ("More states")."""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable

import numpy as np
import pandas as pd

from .snap import _name

PA_API = "https://www.electionreturns.pa.gov/api/ElectionReturn"
PA_GOV = (f"{PA_API}/GetCountyBreak?officeId=3&districtId=1&methodName=GetCountyBreak&electionid=117&electiontype=G"
          "&isactive=1")                                            # 2026 general = election 117 (GetAllElections)
MN_FILES = "https://electionresultsfiles.sos.mn.gov"
MN_COUNTY = f"{MN_FILES}/20261103/allracesbycounty.txt"
NE_SVC = "https://neresultswebservice.azurewebsites.us/ResultsAjax.svc"
NM_SVC = "https://nmresultswebservice.azurewebsites.us/NMResultsAjax.svc"
# race ids of the 2026 general as listed by the states' results pages (resultsSW.aspx BuildCounty(type, race, osn))
NE_SEN = f"{NE_SVC}/GetMapData?type=SW&category=CTY&raceID=13224&osn=102&county=0&party=0"
NE_GOV = f"{NE_SVC}/GetMapData?type=SW&category=CTY&raceID=13508&osn=105&county=0&party=0"
NM_SEN = f"{NM_SVC}/GetMapData?type=FED&category=CTY&raceID=11185&osn=180&county=0&party=0"
NM_GOV = f"{NM_SVC}/GetMapData?type=SW&category=CTY&raceID=11530&osn=202&county=0&party=0"
URLS = [PA_GOV, MN_COUNTY, NE_SEN, NE_GOV, NM_SEN, NM_GOV]        # recorder: one `--url` each

FEEDS = {
    "pa": {"gov": _name(PA_GOV)},
    "mn": {"county": _name(MN_COUNTY)},
    "ne": {"sen": _name(NE_SEN), "gov": _name(NE_GOV)},
    "nm": {"sen": _name(NM_SEN), "gov": _name(NM_GOV)},
}
# U.S. House districts with a competitive market (brunder.us_house_night; configs/us2026_house), one document per
# district: PA office 11 (districtId = district + 1), NE type CG / osn 103, NM type FED / osn 190 (race ids from the
# 2026 results pages); MN's House contests are in its county file above.
HOUSE_URLS = {
    "pa": {n: f"{PA_API}/GetCountyBreak?officeId=11&districtId={n + 1}&methodName=GetCountyBreak&electionid=117"
              "&electiontype=G&isactive=1" for n in (1, 7, 8, 10, 16)},
    "ne": {n: f"{NE_SVC}/GetMapData?type=CG&category=CTY&raceID={r}&osn=103&county=0&party=0"
           for n, r in ((1, 13165), (2, 13166))},
    "nm": {2: f"{NM_SVC}/GetMapData?type=FED&category=CTY&raceID=11187&osn=190&county=0&party=0"},
}
HOUSE_FEEDS = {f"{st}_house": {f"d{n:02d}": _name(u) for n, u in urls.items()} for st, urls in HOUSE_URLS.items()}
HOUSE_FEEDS["mn_house"] = FEEDS["mn"]
STATES = {"pa": "Pennsylvania", "mn": "Minnesota", "ne": "Nebraska", "nm": "New Mexico"}
SOURCE_INFO = {
    "pa": ("Pennsylvania Department of State, election returns", "https://www.electionreturns.pa.gov/", "public record"),
    "mn": ("Minnesota Secretary of State, election results files", "https://electionresults.sos.mn.gov/", "public record"),
    "ne": ("Nebraska Secretary of State, election results", "https://electionresults.nebraska.gov/", "public record"),
    "nm": ("New Mexico Secretary of State, election results", "https://electionresults.sos.nm.gov/", "public record"),
}
POLL_SOURCES = {           # shown in the dashboard sources when the state has a configured race
    "az": {"name": "Arizona Secretary of State, Election Day operations (polls close at 7:00 p.m., MST)",
           "url": "https://azsos.gov/elections/about-elections/elections-procedures/election-day-operations",
           "licence": "public record"},
    "pa": {"name": "Pennsylvania Department of State, elections FAQ (polls 7 a.m.-8 p.m.)",
           "url": "https://www.pa.gov/agencies/vote/voter-support/elections-faqs", "licence": "public record"},
    "mn": {"name": "Minnesota Secretary of State, voting hours (polls close at 8 p.m.)",
           "url": "https://www.sos.mn.gov/elections-voting/election-day-voting/voting-hours", "licence": "public record"},
    "ne": {"name": "Nebraska Secretary of State, 2026 election calendar (polls 8 a.m.-8 p.m. CT, 7 a.m.-7 p.m. MT)",
           "url": "https://sos.nebraska.gov/sites/default/files/doc/elections/2026/2026_Election_Calendar.pdf",
           "licence": "public record"},
    "nm": {"name": "New Mexico Secretary of State, 2026 general election important dates (polls 7 a.m.-7 p.m.)",
           "url": "https://www.sos.nm.gov/wp-content/uploads/2026/06/2026-Important-General-Election-Dates.pdf",
           "licence": "public record"},
}


# ------------------------------------------------------------------ snapshot checks (brunder.us.Archive)

def _check_pa(body: bytes) -> None:
    """The PA API answers a JSON string holding the JSON document: both layers must parse."""
    d = json.loads(body)
    if isinstance(d, str):
        json.loads(d)


def _check_latin1_text(body: bytes) -> None:
    """MN media files are ISO-8859-1 text written whole; a complete file ends with a line break."""
    if not body.rstrip(b" ").endswith(b"\n"):
        raise ValueError("text snapshot without a final line break (truncated?)")


CHECKS: dict[str, Callable[[bytes], None]] = {FEEDS["pa"]["gov"]: _check_pa, FEEDS["mn"]["county"]: _check_latin1_text}
CHECKS.update({k: _check_pa for k in HOUSE_FEEDS["pa_house"].values()})   # same JSON-in-a-string answers


# ------------------------------------------------------------------ parsers

def _key(name: str) -> str:
    """County name key: accents folded (Doña Ana), then brunder.us._norm (upper case letters only, no 'County')."""
    from .us import _norm
    return _norm(unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())


def _county_ids(st: str) -> dict[str, tuple[str, str]]:
    from .us import counties
    c = counties(st)
    return {_key(n): (u, n) for u, n in zip(c["unit_id"], c["name"])}


def _votes(x) -> float:
    """A vote count; NM shows '*' for a county that has not reported (IsReported false), read as 0."""
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return 0.0


def _with_state(rows: list[dict], st: str) -> pd.DataFrame:
    """County rows plus statewide rows (county sums per race and candidate)."""
    from .us import _frame
    df = _frame(rows)
    if df.empty:
        return df
    num = ["votes", "early", "reporting", "units"]
    s = df.groupby(["race", "candidate", "party"], as_index=False)[num].sum(min_count=1)
    s = s.assign(level="state", unit_id=f"us-{st}", name=STATES[st], ballots=np.nan, registered=np.nan,
                 outstanding=np.nan)
    return _frame(df.to_dict("records") + s.to_dict("records"))


def parse_pa(blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """PA GetCountyBreak (one office): {"Election": {"Statewide": [{COUNTY: [candidate rows]}]}} sent as a JSON string;
    per candidate Votes, ElectionDayVotes, MailInVotes, ProvisionalVotes, PartyName. early = mail-in. No precincts."""
    from .us import party_code
    ids, rows = _county_ids("pa"), []
    for race in ("gov", "sen"):
        blob = blobs.get(race)
        if not blob:
            continue
        d = json.loads(blob)
        d = json.loads(d) if isinstance(d, str) else d
        for block in d["Election"]["Statewide"]:
            for cname, cands in block.items():
                if _key(cname) not in ids:
                    continue
                uid, name = ids[_key(cname)]
                for x in cands:
                    rows.append({"level": "county", "unit_id": uid, "name": name, "race": race,
                                 "candidate": " ".join(str(x["CandidateName"]).split()),
                                 "party": party_code(x.get("PartyName")), "votes": float(x.get("Votes") or 0),
                                 "early": float(x.get("MailInVotes") or 0), "reporting": np.nan, "units": np.nan,
                                 "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    return _with_state(rows, "pa")


_MN_PARTY = {"DFL": "DEM", "R": "REP"}


def parse_mn(blob: bytes) -> pd.DataFrame:
    """MN allracesbycounty.txt: State;County ID;Precinct;Office ID;Office;District;Candidate order;Candidate;Suffix;
    Incumbent;Party;Precincts reporting;Precincts;Votes;Percent;Office total. County ID = alphabetical index 1-87,
    Census FIPS = 2 × ID − 1. DFL is the Democratic party, R the Republican."""
    from .us import party_code, race_key
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    names = {u: n for u, n in _county_ids("mn").values()}
    rows = []
    for line in text.splitlines():
        f = line.split(";")
        if len(f) < 14 or not f[1].strip().isdigit():
            continue
        race = race_key(f[4])
        if race not in ("gov", "sen"):
            continue
        uid = f"us-mn-{27000 + 2 * int(f[1]) - 1}"
        if uid not in names:
            continue
        p = f[10].strip()
        rows.append({"level": "county", "unit_id": uid, "name": names[uid], "race": race,
                     "candidate": " ".join(f[7].split()), "party": _MN_PARTY.get(p, party_code(p)),
                     "votes": float(f[13] or 0), "early": np.nan, "reporting": float(f[11] or 0),
                     "units": float(f[12] or 0), "ballots": np.nan, "registered": np.nan, "outstanding": np.nan})
    return _with_state(rows, "mn")


def parse_mapdata(st: str, blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """NE / NM results web service GetMapData (one race by county): [{CountyName, RaceName, calcCandidate, PartyCode,
    calcCandidateVotes, PrecinctsReporting, TotalPrecincts}] (NE wraps the list in {"d": …}). The race is read from
    RaceName, so a wrong race id yields no rows instead of a wrong race."""
    from .us import party_code, race_key
    ids, rows = _county_ids(st), []
    for role in ("sen", "gov"):
        blob = blobs.get(role)
        if not blob:
            continue
        d = json.loads(blob)
        for x in (d.get("d") or []) if isinstance(d, dict) else d:
            race = race_key(re.sub(r"^\s*For\s+", "", str(x.get("RaceName") or ""), flags=re.I))
            k = _key(str(x.get("CountyName") or ""))
            if race not in ("gov", "sen") or k not in ids:
                continue
            uid, name = ids[k]
            rows.append({"level": "county", "unit_id": uid, "name": name, "race": race,
                         "candidate": " ".join(str(x["calcCandidate"]).split()),
                         "party": party_code(x.get("PartyCode")), "votes": _votes(x.get("calcCandidateVotes")),
                         "early": np.nan, "reporting": float(x.get("PrecinctsReporting") or 0),
                         "units": float(x.get("TotalPrecincts") or 0), "ballots": np.nan, "registered": np.nan,
                         "outstanding": np.nan})
    return _with_state(rows, st)


def parse_feed(kind: str, blobs: dict[str, bytes | list[bytes] | None]) -> pd.DataFrame:
    """Rows for one of FEEDS' kinds from its latest snapshots (keys as in FEEDS[kind])."""
    from .us import _frame
    if not any(blobs.get(r) for r in FEEDS[kind]):
        return _frame([])
    match kind:
        case "pa":
            return parse_pa(blobs)
        case "mn":
            return parse_mn(blobs["county"])
        case "ne" | "nm":
            return parse_mapdata(kind, blobs)
    raise ValueError(kind)


# ------------------------------------------------------------------ county priors (certified results)

def _pa_prior(election: int, office: int, race: str, tag: str) -> pd.DataFrame:
    """PA certified county returns from the same API (isactive=0 = a finished election)."""
    from .us import US_RAW, _fetch, _two_party
    url = (f"{PA_API}/GetCountyBreak?officeId={office}&districtId=1&methodName=GetCountyBreak&electionid={election}"
           "&electiontype=G&isactive=0")
    rows = parse_pa({race: _fetch(url, US_RAW / "pa" / f"countybreak_{tag}.json")})
    return _two_party(rows[rows["race"] == race])


def _mn_prior(date: str, race: str) -> pd.DataFrame:
    """MN SoS media file of a finished election (official results by county)."""
    from .us import US_RAW, _fetch, _two_party
    rows = parse_mn(_fetch(f"{MN_FILES}/{date}/allracesbycounty.txt", US_RAW / "mn" / f"{date}_allracesbycounty.txt"))
    return _two_party(rows[rows["race"] == race])


def _mapdata_prior(st: str, typ: str, race_id: int, osn: int, election: int, race: str) -> pd.DataFrame:
    """NE / NM certified county results from the archive endpoint (GetMapDataArchive, electionID = archived eid)."""
    from .us import US_RAW, _fetch, _two_party
    svc = {"ne": NE_SVC, "nm": NM_SVC}[st]
    url = (f"{svc}/GetMapDataArchive?type={typ}&category=CTY&raceID={race_id}&osn={osn}&county=0&party=0"
           f"&electionID={election}")
    rows = parse_mapdata(st, {race: _fetch(url, US_RAW / st / f"mapdata_{election}_{race_id}.json")})
    return _two_party(rows[rows["race"] == race])


MEDSL22 = "https://raw.githubusercontent.com/MEDSL/2022-elections-official/main/individual_states"


def _medsl22_gov(st: str) -> pd.DataFrame:
    """2022 governor by county from MEDSL's 2022 precinct returns (CC0, built from the states' certified precinct
    results), vote modes combined as brunder.us._medsl_county does for 2024."""
    from .us import US_RAW, _fetch, _medsl_county
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"{MEDSL22}/2022-{st}-local-precinct-general.zip",
                                          US_RAW / "medsl22" / f"2022-{st}-local-precinct-general.zip")))
    df = pd.read_csv(z.open([n for n in z.namelist() if n.endswith(".csv")][0]), dtype=str,
                     usecols=["office", "mode", "votes", "county_fips", "party_simplified", "candidate", "precinct",
                              "jurisdiction_fips", "stage"])
    df = df[(df["office"].str.strip().str.upper() == "GOVERNOR") & (df["stage"].fillna("GEN") == "GEN")]
    return _medsl_county(df, st)


def _ne_sen_2024() -> pd.DataFrame:
    """NE 2024 regular Senate (Fischer v Osborn) by county, MEDSL 2024-senate-county.csv (CC0, from the certified
    canvass). Osborn (independent) is the non-Republican side and is put in the `dem` column."""
    from .us import MEDSL24, US_RAW, _fetch, _medsl_county
    d = pd.read_csv(io.BytesIO(_fetch(f"{MEDSL24}/2024-senate-county.csv", US_RAW / "medsl24" / "senate_county.csv")),
                    dtype=str)
    d = d[(d["state_po"] == "NE") & (d["stage"] == "GEN") & (d["special"].str.upper() == "FALSE")]
    d = d.assign(party_simplified=np.where(d["candidate"].str.upper().str.contains("OSBORN"), "DEMOCRAT",
                                           d["party_simplified"]))
    return _medsl_county(d, "ne")


PRIOR_SOURCES = {
    "pa_gov_2022": lambda: _pa_prior(94, 3, "gov", "2022_gov"),
    "mn_gov_2022": lambda: _mn_prior("20221108", "gov"),
    "ne_sen_2024": _ne_sen_2024,
    "ne_gov_2022": lambda: _medsl22_gov("ne"),
    "nm_gov_2022": lambda: _mapdata_prior("nm", "SW", 8745, 202, 2838, "gov"),
    "nm_sen_2024": lambda: _mapdata_prior("nm", "SW", 9792, 180, 2882, "sen"),
    "az_gov_2022": lambda: _medsl22_gov("az"),
}
# certified statewide totals (Democratic or non-Republican side, Republican) each built prior must reproduce
CERTIFIED = {
    "pa_gov_2022": (3031137, 2238477),   # Shapiro, Mastriano (PA DoS)
    "mn_gov_2022": (1312349, 1119941),   # Walz, Jensen (MN SoS canvass)
    "ne_sen_2024": (436493, 499124),     # Osborn, Fischer (NE SoS canvass)
    "ne_gov_2022": (242006, 398334),     # Blood, Pillen
    "nm_gov_2022": (370168, 324701),     # Lujan Grisham, Ronchetti (NM SoS canvass)
    "nm_sen_2024": (497333, 405978),     # Heinrich, Domenici
    "az_gov_2022": (1287891, 1270774),   # Hobbs, Lake (AZ SoS canvass)
}
