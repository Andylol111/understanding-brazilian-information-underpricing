"""Oregon Governor from the Oregon Secretary of State results web service. SECONDARY SOURCE, ADDED 2026-11-02,
UNREGISTERED (docs/us_coverage_2026.md).

results.oregonvotes.gov (statewide partisan page, BuildCounty('SWPAR', '300038702', '0')) loads its county map from
ServiceRoot https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData: the same vendor JSON as the NE/NM
services of brunder.us_feeds_extra ({"d": [{CountyName, RaceName, calcCandidate, PartyCode, PartyName,
calcCandidateVotes, PrecinctsReporting, TotalPrecincts}]}), 36 counties x 4 options. Oregon gives every candidate
PartyCode "NON" and lists fusion lines in PartyName ("Democrat, Working Families", "Republican, Libertarian"), so the
party is read from the first PartyName entry. The CDN host fronts orresws.azurewebsites.us (its cookies name that
domain); the integrator decides whether that falls under the *.azurewebsites.us do-not-request rule.
"""
from __future__ import annotations

import json
import re

import numpy as np

NAME = "Oregon Secretary of State election results"
GOV_URL = ("https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData?type=SWPAR&category=CTY"
           "&raceID=300038702&osn=0&county=0&party=0")
RECORD = [{"key": "or_gov_mapdata", "url": GOV_URL, "interval": 60, "ext": "json", "host_class": "server-unverified"}]
FEEDS = {"or_extra": {"gov": "or_gov_mapdata"}}
STATES = {"or": "Oregon"}
POLL_CLOSE = {"or": "2026-11-04T04:00:00Z"}       # ballots due 8 p.m. local; Malheur (mountain) 03:00Z
SOURCE_INFO = {"or": ("MIT Election Data and Science Lab, U.S. President Precinct-Level Returns 2024 (summed to "
                      "counties; data_public/us2026_extra_priors_or.csv)",
                      "https://github.com/MEDSL/2024-elections-official", "CC0 1.0")}


def _party(x: dict) -> str:
    from .us import party_code
    code = str(x.get("PartyCode") or "").strip().upper()
    if code and code != "NON":
        return party_code(code)
    first = re.split(r"[,/]", str(x.get("PartyName") or ""))[0].strip()
    return party_code(first) if first else ""


def parse(kind: str, blobs: dict):
    """County x candidate rows of the governor race (plus statewide sums); empty frame for an empty/odd document."""
    from . import us
    from .us_feeds_extra import _county_ids, _key, _votes

    if kind != "or_extra":
        raise ValueError(kind)
    blob = blobs.get("gov")
    if not blob:
        return us._frame([])
    try:
        d = json.loads(blob)
    except ValueError:
        return us._frame([])
    ids, rows = _county_ids("or"), []
    for x in (d.get("d") or []) if isinstance(d, dict) else (d if isinstance(d, list) else []):
        if us.race_key(str(x.get("RaceName") or "")) != "gov":
            continue
        k = _key(str(x.get("CountyName") or ""))
        if k not in ids or str(x.get("calcCandidate") or "").strip().lower() == "write-in":
            continue
        uid, name = ids[k]
        rows.append({"level": "county", "unit_id": uid, "name": name, "race": "gov",
                     "candidate": " ".join(str(x["calcCandidate"]).split()), "party": _party(x),
                     "votes": _votes(x.get("calcCandidateVotes")), "early": np.nan,
                     "reporting": float(x.get("PrecinctsReporting") or 0),
                     "units": float(x.get("TotalPrecincts") or 0), "ballots": np.nan, "registered": np.nan,
                     "outstanding": np.nan})
    df = us._frame(rows)
    if df.empty:
        return df
    s = df.groupby(["race", "candidate", "party"], as_index=False)[["votes", "reporting", "units"]].sum()
    s = s.assign(level="state", unit_id="us-or", name="Oregon", early=np.nan, ballots=np.nan, registered=np.nan,
                 outstanding=np.nan)
    return us._frame(df.to_dict("records") + s.to_dict("records"))
