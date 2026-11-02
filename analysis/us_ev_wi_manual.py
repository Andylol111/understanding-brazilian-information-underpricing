"""Wisconsin Elections Commission absentee reports, downloaded by hand in a browser (the page answers automated
clients with a Cloudflare challenge; WEC pointed us to it by email on 2026-11-02) and loaded into the early-vote
database as source `wi_absentee_report`.

Input: the ZIP "All Daily Absentee Ballot Reports - <election>_through_<date>.zip". Its daily county files
("AbsenteeCounts_County_<election>_<YYYY-MM-DD>.csv": one row per county plus TOTAL) are WEC's own county totals
(municipalities spanning counties are split by WEC). Measures: mail_requested = BallotsSent; total = BallotsReturned
(includes in-person absentee); inperson_early = InPersonAbsentee; mail_returned = total - in person. as_of_date = the
date in the file name (WEC posts each report at 7:30 a.m.). No party split (Wisconsin has no party registration).

    uv run python analysis/us_ev_wi_manual.py "<All Daily Absentee Ballot Reports ...zip>"
"""
from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import us_ev_db as db  # noqa: E402
import us_ev_src_d  # noqa: E402,F401  (registers wi_absentee_report)

SOURCE = "wi_absentee_report"
URL = "https://elections.wi.gov/resources/statistics/absentee-ballot-report-november-3-2026-general-election-0"
DAY = re.compile(r"AbsenteeCounts_County_.*_(\d{4}-\d{2}-\d{2})\.csv$")


def _cells(r: dict) -> dict[tuple[str, str], int]:
    sent, ret, ip = db.num(r["BallotsSent"]), db.num(r["BallotsReturned"]), db.num(r["InPersonAbsentee"])
    return {("mail_requested", "ALL"): sent, ("total", "ALL"): ret, ("inperson_early", "ALL"): ip,
            ("mail_returned", "ALL"): max(ret - ip, 0)}


def parse_county_day(body: bytes, as_of: str) -> tuple[list[dict], list[dict]]:
    """One daily county file -> (county rows, state rows). The TOTAL row must equal the county sum."""
    county, state, ret_sum, total = [], [], 0, None
    for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        name = r["Jurisdiction"].strip()
        if name.upper() == "TOTAL":
            state, total = db.long_rows("wi", as_of, _cells(r)), db.num(r["BallotsReturned"])
            continue
        if not name.upper().endswith(" COUNTY"):
            raise ValueError(f"{as_of}: unexpected jurisdiction {name!r}")
        county += db.long_rows("wi", as_of, _cells(r), county=db.county_fips("wi", name[: -len(" COUNTY")].title()))
        ret_sum += db.num(r["BallotsReturned"])
    n = len({r["county_fips"] for r in county})
    if n != 72 or total is None or total != ret_sum:
        raise ValueError(f"{as_of}: {n} counties, TOTAL {total} vs county sum {ret_sum}")
    return county, state


def main(argv: list[str]) -> int:
    zbody = Path(argv[0]).read_bytes()
    days = []
    with zipfile.ZipFile(io.BytesIO(zbody)) as z:
        for name in z.namelist():
            m = DAY.search(name)
            if m:
                days.append((m[1], z.read(name)))
    if not days:
        raise SystemExit("no daily county files in the ZIP")
    days.sort()
    st = db.Store()
    st.current_state = "wi"
    st.sync_sources()
    f = st.save_body(SOURCE, URL, zbody, ext="zip", part="daily_zip", state="wi")
    county, state = [], []
    for d, body in days:
        c, s = parse_county_day(body, d)
        county += c; state += s
    n = st.add_rows(county, source_id=SOURCE, snapshot_id=f.snapshot_id, level="county", mark_parsed=False)
    n += st.add_rows(state, source_id=SOURCE, snapshot_id=f.snapshot_id, level="state")
    st.close()
    last = days[-1][0]
    ret = next(r["count"] for r in state if r["as_of_date"] == last and r["measure"] == "total")
    print(f"wi: 72 counties, {len(days)} daily reports {days[0][0]} .. {last}; {n} rows added; statewide returned {ret:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
