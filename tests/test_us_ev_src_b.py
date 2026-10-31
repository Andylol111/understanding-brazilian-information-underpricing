"""Group B early-vote collectors (analysis/us_ev_src_b.py): pure parsers, no network. Fixtures hold aggregate rows only."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))
import us_ev_db as db  # noqa: E402
import us_ev_src_b as b  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "us_ev"
MD_COUNTIES = ["Allegany", "Anne Arundel", "Baltimore City", "Baltimore County", "Calvert", "Caroline", "Carroll", "Cecil",
               "Charles", "Dorchester", "Frederick", "Garrett", "Harford", "Howard", "Kent", "Montgomery",
               "Prince George's", "Queen Anne's", "Saint Mary's", "Somerset", "Talbot", "Washington", "Wicomico", "Worcester"]


def _get(rows, **kw):
    out = [r for r in rows if all(r.get(k) == v for k, v in kw.items())]
    assert len(out) == 1, (kw, out)
    return out[0]["count"]


def test_md_early_raw_cumulative_by_county_party():
    crow, srow = b.parse_md_early_raw((FIX / "md_early_raw_excerpt.csv").read_bytes())
    allegany = db.county_fips("MD", "Allegany")
    # Allegany DEM: Day6=3, Day7=4, Day8=1 -> cumulative 3, 7, 8 on Oct 27, 28, 29
    assert _get(crow, county_fips=allegany, party="D", as_of_date="2026-10-27", measure="inperson_early") == 3
    assert _get(crow, county_fips=allegany, party="D", as_of_date="2026-10-28") == 7
    assert _get(crow, county_fips=allegany, party="D", as_of_date="2026-10-29") == 8
    assert _get(crow, county_fips=allegany, party="U", as_of_date="2026-10-29") == 1
    assert _get(crow, county_fips=allegany, party="other", as_of_date="2026-10-29") == 2      # OTH 1+1
    assert _get(crow, county_fips=allegany, party="ALL", as_of_date="2026-10-29") == 8 + 3 + 1 + 2
    # state = sum of counties
    assert _get(srow, party="D", as_of_date="2026-10-29") == 8 + 5
    assert _get(srow, party="ALL", as_of_date="2026-10-29") == sum(r["count"] for r in crow
                                                                    if r["party"] == "ALL" and r["as_of_date"] == "2026-10-29")


def _md_sent_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, None, None, "Mail-in Sent and Returned "])
    ws.append([None, None, None, None, "Election: 2026 Gubernatorial General Election"])
    ws.append([None, None, None, None, "As of: October 30, 2026 6 PM\nNote: ..."])
    hdr = ["CATEGORY", "COUNTY NAME", "DISTRICT", None, None, "DEM SENT", "REP SENT", "OTH SENT", "TOTAL SENT", "DEM RECEIVED",
           "REP RECEIVED", "OTH RECEIVED", "TOTAL RECEIVED", None, None, None, "DEM ACCEPTED", "REP ACCEPTED", "OTH ACCEPTED",
           "TOTAL ACCEPTED"]
    ws.append(hdr)
    for c in MD_COUNTIES:
        ws.append(["ALL", c.upper(), "Statewide", None, None, 10, 5, 2, 17, 6, 3, 1, 10, None, None, None, 4, 2, 1, 7])
    ws.append(["", "Total", "", None, None, 240, 120, 48, 408, 144, 72, 24, 240, None, None, None, 96, 48, 24, 168])
    ws.append(hdr)
    ws.append(["Civilian Overseas", "ALLEGANY", "Statewide", None, None, 99, 99, 99, 297, 99, 99, 99, 297, None, None, None, 1, 1, 1, 3])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_md_sent_returned_county_party():
    crow, srow = b.parse_md_sent_returned(_md_sent_xlsx())
    f = db.county_fips("MD", "Prince George's")
    assert _get(crow, county_fips=f, measure="mail_requested", party="D") == 10
    assert _get(crow, county_fips=f, measure="mail_returned", party="other") == 1
    assert _get(crow, county_fips=f, measure="mail_accepted", party="ALL") == 7
    assert _get(srow, measure="mail_returned", party="R") == 72
    assert {r["as_of_date"] for r in crow + srow} == {"2026-10-30"}
    assert len({r["county_fips"] for r in crow}) == 24                       # second block (overseas) not mixed in


def test_md_requests_by_county():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, None, None, "Mail-in Ballot Requests by County"])
    ws.append([None, None, None, None, "Election: 2026 Gubernatorial General Election"])
    ws.append([None, None, None, None, None, None, "As of: October 30, 2026 6 PM"])
    ws.append(["COUNTY", None, "HOW THE BALLOTS WILL BE DELIVERED"])
    ws.append([None, None, "AGENT", None, None, "FAXED", None, "IN PERSON", "MAILED", "OTHERS", "PRINT-AT-HOME", "TOTAL"])
    for i, c in enumerate(MD_COUNTIES):
        ws.append([c, None, 1, None, None, 0, None, 2, 90, 0, 7, 100 + i])
    ws.append(["TOTAL", None, 24, None, None, 0, None, 48, 2160, 0, 168, sum(100 + i for i in range(24))])
    out = io.BytesIO()
    wb.save(out)
    crow, srow = b.parse_md_requests(out.getvalue())
    assert _get(crow, county_fips=db.county_fips("MD", "Kent")) == 114
    assert _get(srow) == sum(100 + i for i in range(24))
    assert srow[0]["as_of_date"] == "2026-10-30"


def test_md_csv_validate_rejects_html():
    b._md_csv_validate((FIX / "md_early_raw_excerpt.csv").read_bytes())
    with pytest.raises(ValueError):
        b._md_csv_validate(b"<html>blocked</html>")


ME_HDR = ("RES MUNICIPALITY|DES|Voter Record #|P|IB|W-P|CG|SS|SR|DA|CC|Ballot Type|Req Type|Req Date|Issued Type|Issued Date|"
          "Rec Type|Rec Date|Rec Time|DUP|Status|CH|REJ\n")


def _me_line(party, req, rec, status):
    return f"Town||000000000|{party}||1-1|2|4|30|5|1|RB|{req}|10/01/2026|X|10/02/2026|{rec}|10/03/2026||| {status}||\n".replace("| ", "|")


def test_me_absentee_aggregates():
    lines = [ME_HDR,
             _me_line("D", "EL", "MA", "ACT"),      # mail, returned, accepted
             _me_line("D", "EL", "DB", "REJ"),      # mail, returned, rejected
             _me_line("R", "EL", "", ""),           # mail, not yet returned
             _me_line("U", "VP", "VP", "ACT"),      # in person
             _me_line("G", "WV", "MA", "ACT"),      # other party, mail accepted
             _me_line("", "VP", "VP", "REJ")]       # in person rejected, no party
    rows = b.parse_me_absentee("".join(lines).encode("latin-1"), "2026-10-30")
    g = lambda m, p: _get(rows, measure=m, party=p)  # noqa: E731
    assert g("mail_requested", "ALL") == 4 and g("mail_requested", "D") == 2 and g("mail_requested", "R") == 1
    assert g("mail_returned", "D") == 2 and g("mail_returned", "other") == 1
    assert g("mail_accepted", "D") == 1 and g("mail_accepted", "ALL") == 2
    assert g("inperson_early", "U") == 1 and g("inperson_early", "ALL") == 1
    assert g("total", "ALL") == 5
    assert rows[0]["state"] == "ME" and rows[0]["as_of_date"] == "2026-10-30"


def test_me_url_and_validate():
    html = ('<a href="/sos/sites/maine.gov.sos/files/inline-files/11-3-26%20AB%20Voter%20File%20as%20of%2010-30-26.txt">'
            "Statewide Absentee Voter Data File (Text)</a>")
    url = b.me_file_url(html)
    assert url.startswith("https://www.maine.gov/sos/sites/") and b.me_file_as_of(url) == "2026-10-30"
    b._me_validate(ME_HDR.encode())
    with pytest.raises(ValueError):
        b._me_validate(b"<html>x</html>")


MS_HTML = ("<div><p>JACKSON, Miss. - As of Monday, October 26, 2026, the Statewide Election Management System (SEMS) "
           "reported a total of <strong>64,537 absentee ballots requested</strong>, <strong>64,031 absentee ballots sent</strong>, "
           "and <strong>56,049 absentee ballots received</strong>.</p></div>")


def test_ms_release():
    rows = b.parse_ms_release(MS_HTML)
    assert _get(rows, measure="mail_requested") == 64537
    assert _get(rows, measure="mail_returned") == 56049
    assert {r["as_of_date"] for r in rows} == {"2026-10-26"}
    with pytest.raises(ValueError):
        b.parse_ms_release("<p>nothing</p>")


def test_ms_slugs_are_mondays():
    from datetime import date
    assert b.ms_slugs(date(2026, 10, 31), 3) == ["october-26", "october-19", "october-12"]


def test_registry():
    for sid in ("md_ev_inperson", "md_mail_county", "ms_absentee_state", "me_absentee_file"):
        assert db.SOURCES[sid].fn is not None
    for sid in ("la_ev_parish", "mn_absentee_county", "ia_absentee_county", "mi_voter_participation", "ks_advance_voting",
                "ky_absentee", "il_vbm", "in_early_voting", "ma_early_mail", "mo_absentee"):
        assert db.SOURCES[sid].fn is None and db.SOURCES[sid].access != "open"
