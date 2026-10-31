"""Parsers of analysis/us_ev_src_c.py (PA, OK, OR, ND): pure functions of bytes/text; fixtures are aggregate excerpts."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_ev_db as db  # noqa: E402
import us_ev_src_c as c  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "us_ev"


def _by(rows, **kw):
    out = [r for r in rows if all(r.get(k) == v for k, v in kw.items())]
    assert len(out) == 1, (kw, out)
    return out[0]["count"]


# ------------------------------------------------------------------------------------------------------------ PA xlsx
def _pa_xlsx(stamp="Information as of 10/31/2026 08:00 AM "):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Total"
    ws.append([stamp])
    ws.append(["CountyName", "Total Applications Approved", "Dem Applications Approved", "Rep Applications Approved",
               "Oth Applications Approved", "Total Ballots Returned", "Dem Ballots Returned", "Rep Ballots Returned",
               "Oth Ballots Returned"])
    ws.append(["ADAMS", "11233", "5746", "4078", "1409", "9675", "5007", "3536", "1132"])
    ws.append(["ALLEGHENY", "161347", "117243", "27852", "16252", "124284", "91461", "21928", "10895"])
    ws.append(["TOTAL", "172580", "122989", "31930", "17661", "133959", "96468", "25464", "12027"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_pa_daily_xlsx():
    as_of, county, state = c.parse_pa_daily_xlsx(_pa_xlsx(), expect_counties=2)
    assert as_of == "2026-10-30"                      # stamped 08:00 -> data through the previous day
    assert _by(county, county_fips="42001", measure="mail_returned", party="D") == 5007
    assert _by(county, county_fips="42003", measure="mail_requested", party="other") == 16252
    assert _by(state, measure="mail_returned", party="ALL") == 133959
    assert len(county) == 16 and len(state) == 8


def test_pa_daily_xlsx_afternoon_stamp_keeps_date():
    as_of, _, _ = c.parse_pa_daily_xlsx(_pa_xlsx("Information as of 10/31/2026 03:00 PM"), expect_counties=2)
    assert as_of == "2026-10-31"


def test_pa_daily_xlsx_rejects_inconsistent_parties():
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(_pa_xlsx()))
    wb["Total"]["G3"] = "5000"                        # D returned no longer adds up
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError):
        c.parse_pa_daily_xlsx(buf.getvalue(), expect_counties=2)


# -------------------------------------------------------------------------------------------------------- PA Socrata
def test_pa_socrata_aggregate():
    body = json.dumps({
        "rows_updated_utc": "2026-10-31T08:52:14+00:00",
        "status": [
            {"countyname": "ADAMS", "party": "DEM", "n": "5894", "req": "5746", "ret": "5007", "acc": "5007"},
            {"countyname": "ADAMS", "party": "GRN", "n": "9", "req": "9", "ret": "3", "acc": "3"},
            {"countyname": "ADAMS", "party": "OTH", "n": "1413", "req": "1368", "ret": "1107", "acc": "1107"},
            {"countyname": "ADAMS", "party": "REP", "n": "4126", "req": "4080", "ret": "3537", "acc": "3535"}],
        "bydate": [
            {"countyname": "ADAMS", "party": "DEM", "ballotreturneddate": "2026-10-29", "n": "1000"},
            {"countyname": "ADAMS", "party": "DEM", "ballotreturneddate": "2026-10-30", "n": "4007"},
            {"countyname": "ADAMS", "party": "OTH", "ballotreturneddate": "2026-10-30", "n": "1107"}]}).encode()
    as_of, county, state = c.parse_pa_socrata(body)
    assert as_of == "2026-10-30"                      # extract at 04:52 ET -> through the previous day
    assert _by(county, county_fips="42001", as_of_date=as_of, measure="mail_returned", party="other") == 1110
    assert _by(county, county_fips="42001", as_of_date=as_of, measure="mail_returned", party="ALL") == 9654
    assert _by(county, county_fips="42001", as_of_date=as_of, measure="mail_accepted", party="R") == 3535
    assert _by(county, county_fips="42001", as_of_date="2026-10-29", measure="mail_returned", party="D") == 1000
    assert _by(county, county_fips="42001", as_of_date="2026-10-29", measure="mail_returned", party="other") == 0
    assert _by(state, as_of_date=as_of, measure="mail_requested", party="ALL") == 5746 + 9 + 1368 + 4080


# ------------------------------------------------------------------------------------------------------------------ OK
def _ok(kind):
    """First two county pages of a real report (ADAIR, ALFALFA) + a STATE page equal to their sum."""
    pages = json.loads((FIX / f"ok_{kind}_pages.json").read_text())[:2]
    head = pages[0].splitlines()[0]
    if kind == "mail":
        state = head + "\nElection Date:    11/3/2026\nCounty  Affiliation  Sent  Undel.  Received\n" \
            "99 STATE                        Republican                    89               0             42\n" \
            "                                Democrat                      49               0             26\n" \
            "                                Independent                   18               0              9"
    else:
        state = head + "\nElection Date:    11/03/2026\nCounty:           99  STATE\n" \
            "Affiliation   In-person\nRepublican                    264\nDemocrat                      120\nIndependent                   29"
    return pages + [state]


def test_ok_mail_report_and_inperson_total():
    mail = c.parse_ok_pages(_ok("mail"), expect_counties=2)
    assert mail["kind"] == "mail" and mail["as_of"] == "2026-10-29"      # run 10/30 07:42 AM
    assert mail["counties"]["ADAIR"]["Republican"] == (63, 0, 24)
    inp = c.parse_ok_pages(_ok("inperson"), expect_counties=2)
    assert inp["counties"]["ALFALFA"]["Republican"] == (94,) and inp["as_of"] == "2026-10-29"
    county, state = c.ok_rows(mail, inp)
    assert _by(county, county_fips="40001", measure="mail_requested", party="D") == 45
    assert _by(county, county_fips="40001", measure="total", party="R") == 24 + 170
    assert _by(county, county_fips="40003", measure="inperson_early", party="ALL") == 107
    assert _by(state, measure="mail_returned", party="ALL") == 77
    assert _by(state, measure="mail_returned", party="U") == 9
    assert _by(state, measure="inperson_early", party="ALL") == 413
    assert _by(state, measure="total", party="ALL") == 77 + 413


def test_ok_state_page_must_equal_county_sum():
    bad = _ok("mail")
    bad[-1] = bad[-1].replace("             42", "             43")
    with pytest.raises(ValueError):
        c.parse_ok_pages(bad, expect_counties=2)


def test_ok_links_general_accordion_only():
    html = ('<h3 class="cmp-accordion__header"><span class="cmp-accordion__title">June 16 Primary Election</span></h3>'
            '<a href="/content/dam/ok/x/2026/ballot-totals-by-affiliation-06142026.pdf">a</a>'
            '<h3 class="cmp-accordion__header"><span class="cmp-accordion__title">November 3 General Election</span></h3>'
            '<a href="/content/dam/ok/x/2026/ballot-totals-by-affiliation-10292026.pdf">a</a>'
            '<h3 class="cmp-accordion__header"><span class="cmp-accordion__title">November 5 General  Election</span></h3>'
            '<a href="/content/dam/ok/x/2024/ballot-totals-by-affiliation-11022024.pdf">a</a>')
    assert c.ok_links(html) == ["https://oklahoma.gov/content/dam/ok/x/2026/ballot-totals-by-affiliation-10292026.pdf"]


# ------------------------------------------------------------------------------------------------------------------ OR
def test_or_pages_party_and_day_tables():
    pages = json.loads((FIX / "or_pages.json").read_text())        # [by-day page, by-party page] (excerpts of real tables)
    res = c.parse_or_pages(pages, expect_counties=4)
    assert res["as_of"] == "2026-10-30"
    g = res["county"]["GILLIAM"]                     # blank Pacific Green / Progressive cells must not shift columns
    assert g == {"ALL": 758, "D": 142, "R": 397, "U": 169, "other": 50}
    s = res["county"]["SHERMAN"]                     # blank No Labels and Working Families cells
    assert s["R"] == 437 and s["U"] == 141 and s["other"] == 43 + 4 + 4 and s["ALL"] == 741
    assert res["state"] == {"ALL": 1181637, "D": 492019, "R": 368003, "U": 239604, "other": 82011}
    col = res["bydate"]["COLUMBIA"]                  # blank first day column -> 0 then cumulative
    assert col["2026-10-16"] == 0 and col["2026-10-19"] == 0 and col["2026-10-20"] == 935 and col["2026-10-30"] == 16002
    assert res["state_bydate"]["2026-10-16"] == 9792
    county, state = c.or_rows(res)
    assert _by(county, county_fips="41021", as_of_date="2026-10-30", measure="total", party="R") == 397
    assert _by(county, county_fips="41021", as_of_date="2026-10-29", measure="mail_returned", party="ALL") == 758 - 29
    assert _by(state, as_of_date="2026-10-30", measure="mail_returned", party="D") == 492019
    assert _by(state, as_of_date="2026-10-29", measure="total", party="ALL") == 1084711


def test_or_party_columns_must_sum():
    pages = json.loads((FIX / "or_pages.json").read_text())
    pages[1] = pages[1].replace("3,187", "3,188")
    with pytest.raises(ValueError):
        c.parse_or_pages(pages, expect_counties=4)


# ------------------------------------------------------------------------------------------------------------------ ND
def test_nd_state_and_counties():
    rows = c.parse_nd_state((FIX / "nd_state.html").read_text(), "2026-10-31")
    assert _by(rows, measure="mail_requested", party="ALL") == 57347
    assert _by(rows, measure="mail_returned", party="ALL") == 46328
    assert _by(rows, measure="inperson_early", party="ALL") == 46098
    assert _by(rows, measure="total", party="ALL") == 92426
    name, cass = c.parse_nd_county((FIX / "nd_cass.html").read_text(), "2026-10-31")
    assert name == "Cass" and _by(cass, county_fips="38017", measure="total", party="ALL") == 4610 + 19994
    name, adams = c.parse_nd_county((FIX / "nd_adams.html").read_text(), "2026-10-31")
    assert name == "Adams" and _by(adams, county_fips="38001", measure="inperson_early", party="ALL") == 0
    assert _by(adams, county_fips="38001", measure="mail_requested", party="ALL") == 445


def test_nd_rejects_bad_state_total():
    html = (FIX / "nd_state.html").read_text().replace("92,426", "92,000")
    with pytest.raises(ValueError):
        c.parse_nd_state(html, "2026-10-31")


def test_registry_has_all_states():
    states = {s.state for s in db.SOURCES.values() if s.source_id in {
        "pa_mail_daily_xlsx", "pa_mail_socrata", "ok_ev_county", "or_ballot_returns", "nd_abev", "mt_absentee_count",
        "oh_ev_dashboard", "nv_vivid", "nh_absentee", "ny_ev_public_reporting", "ne_early_voting", "nm_ev_results_portal",
        "nj_periodic_reports"}}
    assert states == {"pa", "ok", "or", "nd", "mt", "oh", "nv", "nh", "ny", "ne", "nm", "nj"}
    assert db.SOURCES["mt_absentee_count"].fn is None
