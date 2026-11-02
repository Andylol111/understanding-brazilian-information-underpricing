"""Pennsylvania U.S. House PA-11 from the Department of State election returns API (same endpoint and answers as the
registered recorder's PA House documents, brunder.us_feeds_extra.HOUSE_URLS, which covers PA-01/07/08/10/16 only).
SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED (docs/us_coverage_2026_ne.md).

GetCountyBreak, officeId 11 (U.S. Representative), districtId = district + 1 (12 for PA-11), electionid 117: a JSON string
holding {"Election": {"11th Congressional District": [{COUNTY: [candidate rows]}]}}; Lancaster and York (a county
listed with its part of the district). Parsing is brunder.us_house_night.parse_pa (county rows, early = MailInVotes, no
precinct counts). The endpoint was NOT requested while building this (host on the do-not-request list); the URL is
the registered recorder's pattern with district 11 and was never fetched, so the district 12 id is [INFERENCE] until
the first recorded document (a 404/empty answer = no data). Test: the registered PA 2024 House fixture of PA-10.
"""
from __future__ import annotations

import pandas as pd

from brunder import us, us_house_night as hn

NAME = "pa"
RECORD = [dict(key="pa_house_d11", ext="json", interval=60,
               url="https://www.electionreturns.pa.gov/api/ElectionReturn/GetCountyBreak?officeId=11&districtId=12"
                   "&methodName=GetCountyBreak&electionid=117&electiontype=G&isactive=1")]
FEEDS = {"pa_house_x": {"d11": "pa_house_d11"}}
STATES: dict[str, str] = {}          # Pennsylvania is already a brunder.us state (registered feeds)
POLL_CLOSE: dict[str, str] = {}


def parse(kind: str, blobs: dict) -> pd.DataFrame:
    """County rows of the 11th district; an empty or unparsable document gives an empty frame."""
    docs = {}
    for k, v in blobs.items():
        for i, b in enumerate(v if isinstance(v, list) else [v]):
            if b:
                docs[f"{k}{i}"] = b
    try:
        df = hn.parse_pa(docs)
    except (ValueError, KeyError, TypeError, AttributeError):
        return us._frame([])
    return df if len(df) and df["votes"].sum() > 0 else us._frame([])
