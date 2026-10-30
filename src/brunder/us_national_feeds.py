"""County feeds for the national election-night estimate, states the passive recorder (brunder.us.FEEDS) does not cover:
South Dakota, Virginia, Idaho, Maryland, Massachusetts (checked 2026-10-30; the states checked and not usable are listed
in docs/us_national_feeds.md and in NOT_USABLE below).

The national process fetches these itself (`fetch`, one polite request per key) and parses them (`parse_national`) into
the table of brunder.us (`us.COLS`: one row per county x race x candidate, plus `level="state"` rows for the statewide
total; `reporting`/`units` are the feed's own progress figures where it has them). Race keys are "sen" / "gov"; party
codes DEM / REP; unit ids `us-<st>-<5-digit FIPS>` of the dashboard geometry (the ids of the county baseline,
data_public/us2026_county_baseline.csv).

Platforms (all open, no login, no bot challenge; reachable from the project VM and from a home connection, 2026-10-30):
  sd  Civix ENR "ResultsAjax.svc" (same vendor as NE/NM in brunder.us_feeds_extra), county GetMapData for each race;
      the race ids of the 2026 general come from the statewide-races page (`BuildCounty('SWR', race, osn)`).
  va  Enhanced Voting ENR (enr.elections.virginia.gov), ballot item with per-locality breakdown (133 localities).
  id  Enhanced Voting ENR (results.voteidaho.gov), ballot items "Governor" / "United States Senator" (44 counties).
  md  MD State Board of Elections static results pages, one "County Break Down" table per race.
  ma  MA Secretary of the Commonwealth ElectionStats (electionstats.state.ma.us), county table of an election's page.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Generator
from typing import Any

import httpx
import numpy as np
import pandas as pd

from . import us

UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
TIMEOUT = 30.0
ELECTION_DATE = "2026-11-03"

STATES_NATIONAL = {"sd": "South Dakota", "va": "Virginia", "id": "Idaho", "md": "Maryland", "ma": "Massachusetts"}
RACES_NATIONAL = {           # races on the 2026-11-03 ballot (Senate: class 2 seats; Virginia and Massachusetts have no governor race in 2026)
    "sd": ("sen", "gov"), "va": ("sen",), "id": ("sen", "gov"), "md": ("gov",), "ma": ("sen", "gov"),
}

SD_SVC = "https://sdresws.azurewebsites.us/ResultsAjax.svc"
SD_PAGE = "https://electionresults.sd.gov/resultsSW.aspx?type=SWR&map=CTY"
VA_API = "https://enr.elections.virginia.gov/results/public/api"
ID_API = "https://results.voteidaho.gov/results/public/api"
MD_RESULTS = "https://elections.maryland.gov/elections/2026/general_results"
MA_ES = "https://electionstats.state.ma.us/elections"
_MA_OFFICE = {"sen": 6, "gov": 3}                     # ElectionStats office_id (the search page's Office list)

FEEDS_NATIONAL: dict[str, dict[str, str]] = {
    # key -> URL; {braces} are filled from the discovery step of `fetch` (cached per process)
    "sd": {"discover": SD_PAGE,
           "sen": f"{SD_SVC}/GetMapData?type=SWR&category=CTY&raceID={{race_id}}&osn={{osn}}&county=0&party=0",
           "gov": f"{SD_SVC}/GetMapData?type=SWR&category=CTY&raceID={{race_id}}&osn={{osn}}&county=0&party=0"},
    "va": {"discover": f"{VA_API}/jurisdictions/virginia",
           "items": f"{VA_API}/elections/virginia/{{election}}/data",
           "sen": f"{VA_API}/elections/virginia/{{election}}/data/ballot-item/{{item}}"},
    "id": {"discover": f"{ID_API}/jurisdictions/id",
           "items": f"{ID_API}/elections/id/{{election}}/data",
           "sen": f"{ID_API}/elections/id/{{election}}/data/ballot-item/{{item}}",
           "gov": f"{ID_API}/elections/id/{{election}}/data/ballot-item/{{item}}"},
    "md": {"gov": f"{MD_RESULTS}/gen_detail_results_2026_1_1.html"},
    "ma": {"discover_sen": f"{MA_ES}/search/year_from:2026/year_to:2026/office_id:6/stage:General",
           "discover_gov": f"{MA_ES}/search/year_from:2026/year_to:2026/office_id:3/stage:General",
           "sen": f"{MA_ES}/view/{{election_id}}/", "gov": f"{MA_ES}/view/{{election_id}}/"},
}

# Poll closing, UTC (DST ended 2026-11-01: ET = UTC-5, CT = UTC-6, MT = UTC-7, PT = UTC-8). POLL_CLOSE = first poll
# closing of the state, POLL_CLOSE_LAST = last (time-zone splits).
#   sd  7 p.m. local: SD Secretary of State https://sdsos.gov/elections-voting/voting/default.aspx ("Polls are open from
#       7 a.m. to 7 p.m. local time"): Central 19:00 CST = 01:00Z; the western (Mountain) counties 19:00 MST = 02:00Z.
#   va  7 p.m. ET: Code of Virginia 24.2-603 https://law.lis.virginia.gov/vacode/title24.2/chapter6/section24.2-603.
#   id  8 p.m. local: https://voteidaho.gov/idaho-general-election ("Polling locations are open from 8:00 a.m. - 8:00
#       p.m."): Mountain 20:00 MST = 03:00Z; the ten northern counties on Pacific time 20:00 PST = 04:00Z [the time-zone
#       split is Idaho law, not read from that page].
#   md  8 p.m. ET: https://elections.maryland.gov/voting/ ("open continuously from 7 am until 8 pm on November 3, 2026").
#   ma  8 p.m. ET: MA Elections Division, 2026 Information for Voters
#       https://www.sec.state.ma.us/divisions/elections/download/information-for-voters/archive/IFV_2026%20English.pdf
#       ("all polling places will be open from 7 a.m. until 8 p.m.").
POLL_CLOSE_NATIONAL = {"sd": "2026-11-04T01:00:00Z", "va": "2026-11-04T00:00:00Z", "id": "2026-11-04T03:00:00Z",
                       "md": "2026-11-04T01:00:00Z", "ma": "2026-11-04T01:00:00Z"}
POLL_CLOSE_LAST_NATIONAL = {"sd": "2026-11-04T02:00:00Z", "id": "2026-11-04T04:00:00Z"}   # states with one closing time are absent

NOT_USABLE = {            # checked 2026-10-30, reason (details and evidence: docs/us_national_feeds.md)
    "or": "results.oregonvotes.gov answers 404 outside election week; its web service orresws.azurewebsites.us returns 'Web App - Unavailable'",
    "la": "voterportal.sos.la.gov robots.txt disallows /ElectionResults/ElectionResults/Data* (the only results JSON); the xlsx export is not published before the night",
    "wy": "no statewide election-night results site: the Secretary of State posts official PDFs after the canvass, counties report on their own",
    "nj": "the Division of Elections posts no results on election night; 21 county platforms",
    "ia": "Clarity behind an AWS WAF challenge for the VM (HTTP 202, x-amzn-waf-action: challenge)",
    "co": "Clarity behind an AWS WAF challenge for the VM",
    "ks": "sos.ks.gov robots.txt disallows the results pages and API (decision of the earlier survey, docs/us_midterms.md)",
    "mi": "mielections.us does not answer (timeout) from the Mac and the VM, miboecfr.nictusa.com is behind Cloudflare",
    "wi": "elections.wi.gov: Cloudflare challenge (403); no statewide election-night feed",
    "nv": "silverstateelection.nv.gov: Incapsula redirect from the Mac, 403 from the VM",
    "nh": "sos.nh.gov / nh.electionstats.com: 403 (Akamai/Cloudflare) from the Mac, 404 shell from the VM",
    "me": "maine.gov: 403 from the Mac and the VM; no election-night feed",
    "mt": "electionresults.mt.gov / sosmt.gov: 403 (WAF) from the Mac; no answer from the VM",
    "il": "elections.il.gov: 403 Cloudflare from the VM; no statewide election-night feed",
    "ny": "nyenr.elections.ny.gov: Cloudflare challenge (403) from the Mac and the VM",
    "ct": "ctemspublic (CT Election Management System public reporting) is town level and open, but no 2026 general election is published yet and its JSON could not be checked against a certified general",
    "ri": "ri.gov results are one JSON per town (39 + statewide); the 2026 general is not published yet; elections.ri.gov is 403",
    "de": "elections.delaware.gov/results: F5 'Request Rejected'; the 2026 report page is not published yet (the 2024 report is a 5 MB page)",
    "hi": "statewide summary.txt only (no county split); county summaries are PDFs",
    "ky": "results.enr.clarityelections.com/KY: 403/202 challenge; elect.ky.gov publishes certified recap sheets only",
    "ar": "results.enr.clarityelections.com/AR: 403/202 challenge",
    "ms": "sos.ms.gov publishes PDF/page results after the night; no election-night data feed found",
    "al": "no election-night data feed found (sos.alabama.gov publishes canvass PDFs; alabamavotes.gov fails TLS verification)",
    "ok": "results.okelections.gov / .us: 403 from the Mac and the VM",
    "tn": "elections.tn.gov: 403 (CloudFront) from the Mac and the VM",
    "wv": "no election-night results service found (apps.sos.wv.gov results center only holds 2008-2017; the current page is a 404)",
}


# ------------------------------------------------------------------ fetch

_IDS: dict[str, Any] = {}          # per-process discovery cache: SD race ids, Enhanced Voting election/item ids, MA election ids


def _get_checked(resp: httpx.Response) -> bytes:
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.request.url}: HTTP {resp.status_code}")
    return resp.content


def _drive(plan: Generator[str, bytes, dict[str, bytes]], get) -> dict[str, bytes]:
    """Run a plan: it yields the next URL and receives its body; its return value is the result."""
    try:
        url = next(plan)
        while True:
            url = plan.send(get(url))
    except StopIteration as stop:
        return stop.value


def fetch(st: str, client: httpx.Client) -> dict[str, bytes]:
    """Blobs for `parse_national(st, ...)`: one GET per key (HTTP 200 required, else RuntimeError). The first call of a
    process also fetches the discovery pages (race / election ids), which are then cached in `_IDS`."""
    plan = _PLANS[st]()
    return _drive(plan, lambda url: _get_checked(client.get(url, headers=UA, timeout=TIMEOUT)))


async def afetch(st: str, client: httpx.AsyncClient) -> dict[str, bytes]:
    """`fetch` for an httpx.AsyncClient."""
    plan = _PLANS[st]()
    try:
        url = next(plan)
        while True:
            url = plan.send(_get_checked(await client.get(url, headers=UA, timeout=TIMEOUT)))
    except StopIteration as stop:
        return stop.value


def sd_discover(page: bytes) -> dict[str, tuple[str, str]]:
    """Race -> (race id, office sequence number) of the Senate and governor contests on the SD statewide-races page:
    each contest is `<div class="display-results-box-a"><h1>NAME</h1>` followed by its `BuildCounty('SWR', race, osn, '')`."""
    text = page.decode("utf-8", "replace")
    out: dict[str, tuple[str, str]] = {}
    for m in re.finditer(r"""<div class="display-results-box-a">\s*<h1>(.*?)</h1>.*?BuildCounty\('SWR',\s*'(\d+)',\s*'(\d+)'""",
                         text, re.S):
        race = us.race_key(html.unescape(m.group(1)))
        if race in ("sen", "gov"):
            out[race] = (m.group(2), m.group(3))
    return out


def _plan_sd() -> Generator[str, bytes, dict[str, bytes]]:
    for attempt in (1, 2):
        ids = _IDS.get("sd")
        if ids is None:
            ids = sd_discover((yield SD_PAGE))
            missing = set(RACES_NATIONAL["sd"]) - set(ids)
            if missing:
                raise RuntimeError(f"sd: contests not found on the statewide-races page: {sorted(missing)}")
            _IDS["sd"] = ids
        out = {}
        for race in RACES_NATIONAL["sd"]:
            rid, osn = ids[race]
            out[race] = yield (f"{SD_SVC}/GetMapData?type=SWR&category=CTY&raceID={rid}&osn={osn}&county=0&party=0")
        if all(json.loads(b).get("d") for b in out.values()):
            return out
        _IDS.pop("sd", None)                       # ids changed (e.g. test data replaced): discover once more
    raise RuntimeError("sd: GetMapData returned no rows for the discovered race ids")


_EV = {"va": (VA_API, "virginia"), "id": (ID_API, "id")}
_EV_TITLE = re.compile(r"^(?:member,\s*)?(governor|united states senat(?:e|or)|u\.?\s?s\.? senat(?:e|or))"
                       r"(?:\s+and\s+lieutenant governor)?$", re.I)


def ev_items(data: bytes) -> dict[str, str]:
    """Race -> ballot item id for the statewide Governor / US Senate items of an Enhanced Voting election's `/data`
    (titles without a party suffix: primaries have "Governor - Republican"). More than one item per race raises."""
    out: dict[str, str] = {}
    for it in json.loads(data)["ballotItems"]:
        title = it["name"][0]["text"].strip()
        if _EV_TITLE.match(title):
            race = _race(title)
            if race in out:
                raise ValueError(f"two ballot items for {race}: {title!r}")
            out[race] = it["id"]
    return out


def ev_election_id(jurisdiction: bytes, date: str = ELECTION_DATE) -> str:
    ids = [e["publicElectionId"] for e in json.loads(jurisdiction).get("elections", []) if e.get("electionDate") == date]
    if len(ids) != 1:
        raise RuntimeError(f"Enhanced Voting: {len(ids)} elections dated {date}")
    return ids[0]


def _plan_ev(st: str):
    def plan() -> Generator[str, bytes, dict[str, bytes]]:
        api, jur = _EV[st]
        ids = _IDS.get(st)
        if ids is None:
            election = ev_election_id((yield f"{api}/jurisdictions/{jur}"))
            items = ev_items((yield f"{api}/elections/{jur}/{election}/data"))
            missing = set(RACES_NATIONAL[st]) - set(items)
            if missing:
                raise RuntimeError(f"{st}: ballot items not found: {sorted(missing)}")
            ids = _IDS[st] = (election, items)
        election, items = ids
        out = {}
        for race in RACES_NATIONAL[st]:
            out[race] = yield f"{api}/elections/{jur}/{election}/data/ballot-item/{items[race]}"
        return out
    return plan


def _plan_md() -> Generator[str, bytes, dict[str, bytes]]:
    gov = yield FEEDS_NATIONAL["md"]["gov"]
    return {"gov": gov}


def ma_discover(search: bytes) -> str:
    """Election id of the one statewide general election on an ElectionStats search page: rows
    `<tr id="election-id-<id>">` with the cells year, office, district ('Statewide'), stage ('General Election',
    not 'Special General Election' or a primary)."""
    ids = []
    for block in search.decode("utf-8", "replace").split('<tr id="election-id-')[1:]:
        cells = [" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)[:4]]
        if len(cells) == 4 and cells[2] == "Statewide" and cells[3] == "General Election":
            ids.append(re.match(r"\d+", block).group())
    if len(ids) != 1:
        raise RuntimeError(f"ma: {len(ids)} statewide general elections on the search page")
    return ids[0]


def _plan_ma() -> Generator[str, bytes, dict[str, bytes]]:
    out = {}
    for race in RACES_NATIONAL["ma"]:
        eid = _IDS.get(("ma", race))
        if eid is None:
            eid = _IDS[("ma", race)] = ma_discover((yield FEEDS_NATIONAL["ma"][f"discover_{race}"]))
        out[race] = yield f"{MA_ES}/view/{eid}/"
    return out


_PLANS = {"sd": _plan_sd, "va": _plan_ev("va"), "id": _plan_ev("id"), "md": _plan_md, "ma": _plan_ma}


# ------------------------------------------------------------------ units

_SUFFIX = re.compile(r"\s+(County|Parish|Borough|Census Area|Municipality|City and Borough)$", re.I)


def _fold(name: str) -> str:
    """Name key: accents folded, '&' read as 'and', 'St.' as 'Saint', upper case letters only."""
    s = unicodedata.normalize("NFKD", name.replace("&", " and ")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z]", "", re.sub(r"\bST\b\.?", "SAINT", s))


def _units(st: str) -> dict[str, tuple[str, str]]:
    """Name key -> (unit_id, name) of the state's counties in the dashboard geometry. Independent cities keep their
    'city' (Virginia and Maryland have a Fairfax/Baltimore county and city), a county keeps no suffix."""
    out = {}
    for uid, full in us._geo():
        if uid.startswith(f"us-{st}-"):
            base = _SUFFIX.sub("", re.sub(r",\s*[A-Z]{2}$", "", full))
            out[_fold(base)] = (uid, base)
    return out


def _unit(units: dict[str, tuple[str, str]], name: str) -> tuple[str, str] | None:
    return units.get(_fold(_SUFFIX.sub("", html.unescape(name).strip())))


def _race(title: str) -> str | None:
    """sen / gov from a contest title ('Member, United States Senate', 'Governor and Lieutenant Governor', …)."""
    return us.race_key(re.sub(r"^\s*(member|for),?\s+", "", title.strip(), flags=re.I))


_PARTY = {"DEMOCRATIC": "DEM", "DEMOCRAT": "DEM", "D": "DEM", "DEM": "DEM", "DFL": "DEM",
          "REPUBLICAN": "REP", "R": "REP", "REP": "REP"}


def _party(name: str | None, abbreviation: str | None = None) -> str:
    for s in (name, abbreviation):
        if s and s.strip().upper() in _PARTY:
            return _PARTY[s.strip().upper()]
    s = (name or abbreviation or "").strip()
    return us.party_code(s) if s else ""


def _row(level: str, uid: str, name: str, race: str, cand: str, party: str, votes: float,
         reporting: float = np.nan, units: float = np.nan) -> dict:
    return {"level": level, "unit_id": uid, "name": name, "race": race, "candidate": cand, "party": party,
            "votes": votes, "early": np.nan, "reporting": reporting, "units": units, "ballots": np.nan,
            "registered": np.nan, "outstanding": np.nan}


def _with_state(rows: list[dict], st: str, reporting: dict[str, tuple[float, float]] | None = None,
                totals: list[dict] | None = None) -> pd.DataFrame:
    """County rows plus statewide rows: the feed's own `totals` rows if it has them, else county sums per race and
    candidate; reporting/units = `reporting` (race -> (reporting, units)) or, if not given, the sums of the counties'
    own figures."""
    df = pd.DataFrame(rows, columns=us.COLS)
    if df.empty:
        return us._frame([])
    if totals:
        return us._frame(rows + totals)
    s = (df.groupby(["race", "candidate", "party"], sort=False, as_index=False)["votes"].sum()
         .assign(level="state", unit_id=f"us-{st}", name=STATES_NATIONAL[st]))
    for col in us.COLS:
        if col not in s:
            s[col] = np.nan
    if not reporting:
        per = df.drop_duplicates(["race", "unit_id"]).groupby("race")[["reporting", "units"]].sum(min_count=1)
        reporting = {r: (v["reporting"], v["units"]) for r, v in per.iterrows()}
    s["reporting"] = s["race"].map(lambda r: reporting.get(r, (np.nan, np.nan))[0])
    s["units"] = s["race"].map(lambda r: reporting.get(r, (np.nan, np.nan))[1])
    return us._frame(rows + s[us.COLS].to_dict("records"))


# ------------------------------------------------------------------ parsers

def parse_sd(blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """SD Civix ENR GetMapData (one race by county; the service and fields of NE/NM, brunder.us_feeds_extra):
    {"d": [{CountyName, RaceName, calcCandidate, PartyCode, calcCandidateVotes, PrecinctsReporting, TotalPrecincts}]}.
    The race is read from RaceName, so a wrong race id yields no rows instead of a wrong race. Precinct counts per
    county are the feed's; the statewide rows are the county sums."""
    units, rows = _units("sd"), []
    for role in ("sen", "gov"):
        blob = blobs.get(role)
        if not blob:
            continue
        for x in json.loads(blob).get("d") or []:
            race = _race(str(x.get("RaceName") or ""))
            u = _unit(units, str(x.get("CountyName") or ""))
            if race not in ("sen", "gov"):
                continue
            if u is None:
                raise ValueError(f"sd: unknown county {x.get('CountyName')!r}")
            rows.append(_row("county", u[0], u[1], race, " ".join(str(x["calcCandidate"]).split()),
                             us.party_code(x.get("PartyCode")), us._int(x.get("calcCandidateVotes") or 0),
                             float(x.get("PrecinctsReporting") or 0), float(x.get("TotalPrecincts") or 0)))
    return _with_state(rows, "sd")


def _ev_name(o: dict) -> str:
    return re.sub(r"\s*\((?:I\)\s*\()?[A-Za-z]+\)\s*$", "", o["name"][0]["text"]).strip()


def parse_ev(item: bytes, st: str) -> pd.DataFrame:
    """Enhanced Voting ballot item (…/api/elections/<jurisdiction>/<election>/data/ballot-item/<id>), VA and ID:
    ballotItemWithBreakdown {name, reportingStatus, summaryResults.ballotOptions (statewide), breakdownResults[
    {locality.name, precinctReportingStatus{reportingUnits,totalUnits}, ballotOptions[{name, party, voteCount}]}]}.
    The statewide rows are the feed's own summary; reporting/units = precincts (localities) reporting."""
    b = json.loads(item)["ballotItemWithBreakdown"]
    race = _race(b["name"][0]["text"])
    if race not in ("sen", "gov"):
        return us._frame([])
    units = _units(st)

    def opts(level, uid, name, options, status):
        return [_row(level, uid, name, race, _ev_name(o),
                     _party(((o.get("party") or {}).get("name") or [{}])[0].get("text"), (o.get("party") or {}).get("abbreviation")),
                     float(o["voteCount"]), us._int((status or {}).get("reportingUnits")),
                     us._int((status or {}).get("totalUnits"))) for o in options]

    rows = opts("state", f"us-{st}", STATES_NATIONAL[st], b["summaryResults"]["ballotOptions"], b.get("reportingStatus"))
    for br in b.get("breakdownResults") or []:
        u = _unit(units, br["locality"]["name"][0]["text"])
        if u is None:
            raise ValueError(f"{st}: unknown locality {br['locality']['name'][0]['text']!r}")
        rows += opts("county", u[0], u[1], br["ballotOptions"], br.get("precinctReportingStatus"))
    return us._frame(rows)


def _cells(row: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]


def _int_cell(s: str) -> float:
    """Votes in a results-table cell: '12,345', '12,345 (54.2%)'; 'NR' (not reported) and blanks read as 0."""
    m = re.match(r"\s*([\d,]+)", s)
    return float(m.group(1).replace(",", "")) if m else 0.0


def parse_md(blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """MD State Board of Elections 'County Break Down' page of one race (gen_detail_results_<year>_<race>_1.html): the
    race in <h2>, '(k of n election day precincts reported)', then one or more tables (a table holds only some of
    the candidates) with a row per jurisdiction (24: 23 counties and Baltimore City) and a Totals row. Header cells
    are 'Name <br> Party <br>'; 'NR' = not reported."""
    units, rows, reporting = _units("md"), [], {}
    for blob in (blobs.get("gov"), blobs.get("sen")):
        if not blob:
            continue
        text = blob.decode("utf-8-sig", "replace")
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", text, re.S)
        race = _race(html.unescape(re.sub(r"<[^>]+>", " ", h2.group(1))).strip()) if h2 else None
        if race not in ("sen", "gov"):
            raise ValueError("md: not a statewide Senate / governor page")
        pr = re.search(r"\(\s*([\d,]+)\s+of\s+([\d,]+)\s+election day precincts reported\)", text)
        if pr:
            reporting[race] = (float(pr.group(1).replace(",", "")), float(pr.group(2).replace(",", "")))
        for table in re.findall(r"<table.*?</table>", text, re.S):
            trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
            head = re.findall(r"<th[^>]*>(.*?)</th>", trs[0], re.S)
            cands = []
            for h in head[1:]:
                parts = [html.unescape(p).strip() for p in re.split(r"<br\s*/?>", re.sub(r"<img[^>]*>", "", h)) if p.strip()]
                parts = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p)).strip() for p in parts]
                cands.append((parts[0], _party(parts[-1]) if len(parts) > 1 else ""))
            for tr in trs[1:]:
                c = _cells(tr)
                if not c or c[0].lower().startswith("total"):
                    continue
                u = _unit(units, c[0])
                if u is None:
                    raise ValueError(f"md: unknown jurisdiction {c[0]!r}")
                for (cand, party), v in zip(cands, c[1:]):
                    rows.append(_row("county", u[0], u[1], race, cand, party, _int_cell(v)))
    return _with_state(rows, "md", reporting)


def _ma_race(text: str) -> str | None:
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    if title is None:
        return None
    name = re.sub(r"^.*?\d{4}\s+", "", html.unescape(title.group(1))).replace("U.S. Senate", "United States Senate")
    return _race(name)


def parse_ma(blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """MA ElectionStats election page (/elections/view/<id>/): the county table (14 counties; rows
    `<tr id="locality-id-…">` with the county in a.label and one cell per named candidate, then All Others, Blanks,
    Total Votes Cast) and its `<tr class="total">` row (the statewide total, taken as the state rows). Header cells
    carry the candidate's name (title) and party (css class `democratic_party`, `republican_party`, …). The race is
    read from the page title."""
    units, rows, totals = _units("ma"), [], []
    for blob in (blobs.get("sen"), blobs.get("gov")):
        if not blob:
            continue
        text = blob.decode("utf-8", "replace")
        race = _ma_race(text)
        if race not in ("sen", "gov"):
            raise ValueError("ma: not a statewide Senate / governor election page")
        table = re.search(r"<table[^>]*>(?:(?!</table>).)*?locality-id-.*?</table>", text, re.S)
        if table is None:
            raise ValueError("ma: no county table")
        head = [(html.unescape(name), _party(cls) if cls.lower() in ("democratic", "republican") else cls.upper()[:3])
                for cls, name in re.findall(r"<th class=\"candidate_key_reference (\w+?)(?:_party)? candidate-id-\d+\"[^>]*>.*?title=\"([^\"]*)\"",
                                            table.group(0), re.S)]

        def numbers(tr: str) -> list[float]:
            return [_int_cell(html.unescape(re.sub(r"<[^>]+>", " ", v))) for v in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]

        for tr in re.findall(r'<tr id="locality-id-\d+"[^>]*>(.*?)</tr>', table.group(0), re.S):
            label = re.search(r'class="label"[^>]*>(.*?)</a>', tr, re.S)
            u = _unit(units, label.group(1)) if label else None
            if u is None:
                raise ValueError(f"ma: unknown county {label.group(1) if label else tr[:60]!r}")
            for (cand, party), v in zip(head, numbers(tr.split("</td>", 1)[1])):
                rows.append(_row("county", u[0], u[1], race, cand, party, v))
        total = re.search(r'<tr class="total">(.*?)</tr>', table.group(0), re.S)
        if total:
            for (cand, party), v in zip(head, numbers(total.group(1).split("</td>", 1)[1])):
                totals.append(_row("state", "us-ma", STATES_NATIONAL["ma"], race, cand, party, v))
    return _with_state(rows, "ma", totals=totals)


def parse_national(st: str, blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """Rows (us.COLS) of one state from `fetch(st, …)` blobs; missing / empty blobs give no rows."""
    if st == "sd":
        return parse_sd(blobs)
    if st in _EV:
        parts = [parse_ev(b, st) for k in RACES_NATIONAL[st] if (b := blobs.get(k))]
        return pd.concat(parts, ignore_index=True) if parts else us._frame([])
    if st == "md":
        return parse_md(blobs)
    if st == "ma":
        return parse_ma(blobs)
    raise KeyError(f"{st}: no national feed (see NOT_USABLE)")
