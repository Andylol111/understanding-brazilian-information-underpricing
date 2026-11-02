"""Clarity/Scytl ENR statewide sites, U.S. House contests (secondary source, added 2026-11-02, unregistered).

Iowa (https://electionresults.iowa.gov/IA, the Secretary of State's own ENR) and Colorado
(https://results.enr.clarityelections.com/CO, the Secretary of State's ENR on Clarity) both serve every contest of
the night, county by county, as static files behind CloudFront. Layout (the one brunder.us.parse_clarity reads for
SC):
  <base>/elections.json                           [{EID, ElectionName, Date "11/3/2026 12:00:00 AM", County ""}]
  <base>/<eid>/current_ver.txt                    version number, changes with every upload (Cache-Control max-age=0)
  <base>/<eid>/<ver>/json/en/summary.json         statewide contests: K key, C name, CH choices, P parties, V votes,
                                                  TP/PR precincts total/reporting
  <base>/<eid>/<ver>/json/ALL.json                {Contests: [{A county, C [contest keys], V [[votes per choice]]}]};
                                                  a county lists only the contests on its ballots, so a county split
                                                  between districts has only that district's votes (the last row,
                                                  A = "-1", is the statewide total)
  <base>/<eid>/<ver>/json/status.json             per county P name, PX precincts reporting, PY precincts, B ballots,
                                                  R registered voters
The three files of one version are recorded under <prefix>_all / _summary / _status and parsed together. The version
(and, until the 2026 general election exists, the election id) is only known at run time: each RECORD spec has a
`resolve(get)` that reads elections.json (once the election is found) and current_ver.txt (once per cycle).

Rows: level "county", unit_id us-<st>-<FIPS>, race "house-NN" (brunder.us_house_night.house_race layout);
reporting / units = the county's precincts reporting / total over its whole ballot (status.json has no per-contest
progress). Pure parse: bytes in, rows out.
"""
from __future__ import annotations

import gzip
import json
import re
import time

import numpy as np
import pandas as pd

from . import us

NAME = "clarity_mw"
ELECTION_DATE = "11/3/2026"
BASES = {"ia": "https://electionresults.iowa.gov/IA", "co": "https://results.enr.clarityelections.com/CO"}
SOURCE_INFO = {
    "ia": ("Iowa Secretary of State, Election Night Reporting (Clarity)", BASES["ia"] + "/", "public record"),
    "co": ("Colorado Secretary of State, Election Night Reporting (Clarity)", BASES["co"] + "/", "public record"),
}
STATES = {"ia": "Iowa", "co": "Colorado"}
POLL_CLOSE = {"ia": "2026-11-04T03:00:00Z", "co": "2026-11-04T02:00:00Z"}
INTERVAL = 60                                        # s; files change every few minutes at most
FILES = {"all": "json/ALL.json", "summary": "json/en/summary.json", "status": "json/status.json"}
KINDS = {f"{st}_house": st for st in BASES}          # kind -> state

_EID: dict[str, str] = {}                            # base -> election id of the 2026-11-03 general
_VER: dict[str, tuple[float, str]] = {}              # base -> (monotonic time, version): once per cycle
VER_TTL = 20.0


def _loads(b: bytes):
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return json.loads(b.decode("utf-8-sig"))


def election_id(listing: bytes) -> str | None:
    """EID of the election dated 2026-11-03 in a site's elections.json (a state site lists County "", a county site
    its own name; None if not listed yet)."""
    for e in _loads(listing):
        if str(e.get("Date", "")).startswith(ELECTION_DATE + " "):
            return str(e["EID"])
    return None


def _resolve(base: str, tail: str, get) -> str | None:
    try:
        if base not in _EID:
            raw = get(base + "/elections.json")
            eid = election_id(raw) if raw else None
            if eid is None:
                return None
            _EID[base] = eid
        now = time.monotonic()
        t, ver = _VER.get(base, (-1e9, ""))
        if now - t > VER_TTL:
            raw = get(f"{base}/{_EID[base]}/current_ver.txt")
            ver = raw.decode().strip() if raw else ""
            if not ver.isdigit():
                return None
            _VER[base] = (now, ver)
        return f"{base}/{_EID[base]}/{ver}/{tail}"
    except Exception:
        return None


def _spec(st: str, role: str) -> dict:
    base = BASES[st]
    return {"key": f"clarity_{st}_{role}", "url": base + "/elections.json", "interval": INTERVAL, "ext": "json",
            "host_class": "server-blocked",          # CloudFront/AWS WAF 202 challenge to the VM (2026-10-30)
            "resolve": lambda get, base=base, tail=FILES[role]: _resolve(base, tail, get)}


RECORD = [_spec(st, role) for st in BASES for role in FILES]
FEEDS = {f"{st}_house": {role: f"clarity_{st}_{role}" for role in FILES} for st in BASES}

_DIST = re.compile(r"district\s*(?:no\.?)?\s*0*(\d+)\b")


def house_race(name: str) -> str | None:
    """'United States Representative District 3' / 'Representative to the 119th United States Congress - District 3'
    -> 'house-03'; state legislature seats are not House races."""
    n = " ".join(str(name or "").lower().split())
    if re.search(r"\bstate\b|senat|county|judge|council|board", n):
        return None
    if not (("united states" in n or re.match(r"u\.?\s?s\.?\s", n)) and ("represent" in n or "congress" in n)):
        return None
    m = _DIST.search(n)
    return f"house-{int(m.group(1)):02d}" if m else None


def _empty() -> pd.DataFrame:
    return us._frame([])


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    st = KINDS[kind]
    all_json, summary, status = blobs.get("all"), blobs.get("summary"), blobs.get("status")
    if not all_json or not summary:
        return _empty()
    try:
        return parse_house(all_json, summary, status, st)
    except (ValueError, KeyError, IndexError, TypeError):    # half-written upload
        return _empty()


def parse_house(all_json: bytes, summary: bytes, status: bytes | None, st: str) -> pd.DataFrame:
    ids = us._ids(st)
    contests = {}
    for c in _loads(summary):
        race = house_race(c.get("C"))
        if race is not None:
            ch = c["CH"]
            contests[c["K"]] = (race, ch, c.get("P") or [""] * len(ch))
    prog = {}
    if status:
        s = _loads(status)
        for i, nm in enumerate(s.get("P") or []):
            k = us._norm(nm)
            if k in ids:
                prog[ids[k][0]] = (us._int(s["PX"][i]), us._int(s["PY"][i]), us._int(s["B"][i]), us._int(s["R"][i]))
    rows = []
    for cty in _loads(all_json)["Contests"]:
        k = us._norm(cty["A"])
        if k not in ids:                                     # the statewide row "-1"
            continue
        uid, name = ids[k]
        rep, tot, bal, reg = prog.get(uid, (np.nan,) * 4)
        for key, votes in zip(cty["C"], cty["V"]):
            if key not in contests:
                continue
            race, ch, parties = contests[key]
            for choice, party, v in zip(ch, parties, votes):
                rows.append({"level": "county", "unit_id": uid, "name": name, "race": race, "candidate": choice,
                             "party": us.party_code(party), "votes": float(v), "early": np.nan, "reporting": rep,
                             "units": tot, "ballots": bal, "registered": reg, "outstanding": np.nan})
    return us._frame(rows)
