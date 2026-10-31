import io
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import us_ev_src_d as d  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "us_ev"


def xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_sc_ev_csv_cumulative():
    county, state = d.parse_sc_ev_csv((FX / "sc_ev_totals_excerpt.csv").read_bytes(), min_counties=2)
    g = {(r["county_fips"], r["as_of_date"]): r["count"] for r in county}
    assert g[("45001", "2026-10-29")] == 338 and g[("45001", "2026-10-30")] == 795
    assert g[("45003", "2026-10-30")] == 2816 + 4334
    assert {r["as_of_date"]: r["count"] for r in state} == {"2026-10-29": 5000, "2026-10-30": 11000}
    assert all(r["measure"] == "inperson_early" and r["party"] == "ALL" for r in county + state)


def test_sc_ev_csv_bad():
    with pytest.raises(ValueError):
        d.parse_sc_ev_csv(b"nothing,here\n")


def test_sc_absentee_xlsx():
    hdr = [None, "County", None, None, "Issued", "Returned Before Deadline", "Returned After Deadline", "Total \nReturned",
           "Issued", "Returned Before Deadline", "Returned After Deadline", "Total \nReturned"]
    body = xlsx([[None, None, "RP0007"], [None, "", "Race ALL"], hdr,
                 [None, "01-ABBEVILLE", None, None, 145, 124, 0, 124, 124, 77, 0, 77],
                 [None, "02-AIKEN", None, None, 1472, 1248, 26, 1274, 1244, 1004, 0, 1004],
                 [None, "Total", None, None, 1617, 1372, 26, 1398, 1368, 1081, 0, 1081]])
    county, state = d.parse_sc_absentee_xlsx(body, "2026-10-30", min_counties=2)
    got = {(r["county_fips"], r["measure"]): r["count"] for r in county}
    assert got[("45001", "mail_requested")] == 124 and got[("45003", "mail_returned")] == 1004
    assert {r["measure"]: r["count"] for r in state} == {"mail_requested": 1368, "mail_returned": 1081}
    assert county[0]["as_of_date"] == "2026-10-30"


def test_sd_absentee():
    rows = d.parse_sd_absentee((FX / "sd_absentee_excerpt.html").read_bytes())
    g = {(r["as_of_date"], r["measure"], r["party"]): r["count"] for r in rows}
    assert g[("2026-10-30", "mail_requested", "R")] == 43718
    assert g[("2026-10-30", "total", "other")] == 9839 + 129 + 59
    assert g[("2026-10-30", "inperson_early", "ALL")] == 20315 + 8623 + 107 + 2760 + 45 + 37323   # party sum fixes the date row
    assert g[("2026-10-30", "mail_returned", "D")] == 23233 - 20315
    assert g[("2026-10-23", "total", "ALL")] == 49438 and g[("2026-10-23", "inperson_early", "ALL")] == 42396
    assert not any(k[0].startswith("2026-07") for k in g)             # primary tables ignored


def test_wv_article():
    as_of, links = d.parse_wv_article((FX / "wv_article_excerpt.html").read_bytes())
    assert as_of == "2026-10-28"
    assert links["mail_returned"].endswith("546aa5e4-c764-4eeb-890b-7fe0ff8d575e.xlsx")
    assert set(links) == {"inperson_early", "mail_requested", "mail_returned"}


H = ["COUNTY", "CONSTITUTION PARTY", "DEMOCRATIC PARTY", "REPUBLICAN PARTY", "MOUNTAIN PARTY", "NO PARTY",
     "LIBERTARIAN PARTY", "OTHER PARTY", "TOTAL VOTERS"]


def test_wv_xlsx_and_rows():
    ip = xlsx([H, ["BARBOUR", 0, 198, 310, 1, 117, 4, 41, 671], ["BERKELEY", 8, 2971, 3983, 16, 2099, 55, 144, 9276],
               ["WEST VIRGINIA", 8, 3169, 4293, 17, 2216, 59, 185, 9947]])
    rt = xlsx([H, ["BARBOUR", 0, 20, 21, 0, 8, 1, 5, 55], ["BERKELEY", 0, 167, 117, 0, 84, 2, 7, 377]])
    pi, si = d.parse_wv_xlsx(ip, min_counties=2)
    pr, sr = d.parse_wv_xlsx(rt, min_counties=2)
    assert pi["BARBOUR"] == {"other": 46, "D": 198, "R": 310, "U": 117, "ALL": 671} and sr is None
    county, state = d.wv_rows("2026-10-28", {"inperson_early": (pi, si), "mail_returned": (pr, sr)})
    s = {(r["measure"], r["party"]): r["count"] for r in state}
    assert s[("inperson_early", "ALL")] == 9947 and s[("mail_returned", "ALL")] == 432 and s[("total", "ALL")] == 9947 + 432
    c = {(r["county_fips"], r["measure"], r["party"]): r["count"] for r in county}
    assert c[("54001", "total", "D")] == 218 and c[("54003", "mail_returned", "U")] == 84
    with pytest.raises(ValueError):
        d.parse_wv_xlsx(rt)                      # < 50 counties


def test_not_collected_registered():
    import us_ev_db as db
    for st in ("wi", "wa", "tn", "vt", "ut", "va", "wy", "ri"):
        assert any(s.state == st and s.fn is None for s in db.SOURCES.values()), st
