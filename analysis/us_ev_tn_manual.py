"""Tennessee Secretary of State early-voting turnout (early in person + absentee), downloaded by hand in a browser (the file
host answers automated clients with a CloudFront 403; the Division of Elections sent the link by email on 2026-11-02),
loaded into the early-vote database as source `tn_early_voting_stats`.

Input: "<YYYYMMDD>Day<N>Turnout.xlsx", sheet EarlyAbsentee: one row per county (and "Statewide") per voting day with that
day's total. Stored as running totals: measure `total` (early in-person + absentee ballots, cumulative through each day),
as_of_date = the voting day. No party split (Tennessee has no party registration). The statewide rows must equal the
county sums each day.

    uv run python analysis/us_ev_tn_manual.py "<...Day14Turnout.xlsx>"
"""
from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import us_ev_db as db  # noqa: E402
import us_ev_src_d  # noqa: E402,F401  (registers tn_early_voting_stats)

SOURCE = "tn_early_voting_stats"
URL = "https://sos.tn.gov/elections/pages/early-voting-statistics"


def parse(body: bytes) -> tuple[list[dict], list[dict], str]:
    x = pd.read_excel(io.BytesIO(body), sheet_name="EarlyAbsentee")
    x.columns = [str(c).strip() for c in x.columns]
    if not {"County", "Date", "Day Total"} <= set(x.columns):
        raise ValueError(f"TN xlsx: unexpected columns {list(x.columns)}")
    x["County"] = x["County"].astype(str).str.strip()
    x["Date"] = pd.to_datetime(x["Date"]).dt.strftime("%Y-%m-%d")
    x = x.sort_values(["County", "Date"])
    run: dict[str, int] = defaultdict(int)
    county, state = [], []
    for r in x.itertuples(index=False):
        name, d, v = r.County, r.Date, db.num(r[2])
        run[name] += v
        cells = {("total", "ALL"): run[name]}
        if name.lower() == "statewide":
            state += db.long_rows("tn", d, cells)
        else:
            county += db.long_rows("tn", d, cells, county=db.county_fips("tn", name))
    # check: statewide running total = sum of the counties' running totals, every day
    by_day = defaultdict(int)
    for r in county:
        by_day[r["as_of_date"]] += r["count"]
    bad = [(r["as_of_date"], r["count"], by_day[r["as_of_date"]]) for r in state if r["count"] != by_day[r["as_of_date"]]]
    n = len({r["county_fips"] for r in county})
    if n != 95 or bad:
        raise ValueError(f"TN xlsx: {n} counties (expected 95); statewide vs county sums differ on {bad[:3]}")
    return county, state, max(r["as_of_date"] for r in state)


def main(argv: list[str]) -> int:
    body = Path(argv[0]).read_bytes()
    county, state, last = parse(body)
    st = db.Store()
    st.current_state = "tn"
    st.sync_sources()
    f = st.save_body(SOURCE, URL, body, ext="xlsx", part="manual_day_totals", state="tn")
    n = st.add_rows(county, source_id=SOURCE, snapshot_id=f.snapshot_id, level="county", mark_parsed=False)
    n += st.add_rows(state, source_id=SOURCE, snapshot_id=f.snapshot_id, level="state")
    st.close()
    tot = next(r["count"] for r in state if r["as_of_date"] == last)
    print(f"tn: 95 counties, {len({r['as_of_date'] for r in state})} voting days through {last}; {n} rows added; statewide {tot:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
