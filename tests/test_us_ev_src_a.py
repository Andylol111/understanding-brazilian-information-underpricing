"""Parsers of analysis/us_ev_src_a.py (CA, CO, CT, DC, DE, HI, ID) on trimmed real excerpts of the official files (aggregate rows only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))
import us_ev_db as db  # noqa: E402
import us_ev_src_a as a  # noqa: E402

F = Path(__file__).parent / "fixtures" / "us_ev"


def cell(rows, measure, party="ALL", age=None, county=None):
    got = [r["count"] for r in rows if r["measure"] == measure and r["party"] == party and r.get("age_band") == age
           and r.get("county_fips") == county]
    assert len(got) == 1, (measure, party, age, county, got)
    return got[0]


def test_registry():
    ids = {s.source_id: s for s in db.SOURCES.values() if s.fn is not None}
    for sid in ("ca_ev_county", "co_ev_county", "ct_ev_state", "dc_ev_state", "de_ev_county", "hi_voting_report",
                "hi_absentee_recon", "id_ev_tracker"):
        assert sid in ids
        assert ids[sid].state == sid[:2]
    miss = {s.state: s for s in db.SOURCES.values() if s.fn is None and s.state in {"ak", "az", "ga", "al", "ar"}}
    assert set(miss) == {"ak", "az", "ga", "al", "ar"}
    assert all(s.access for s in miss.values())


# ------------------------------------------------------------------------------------------------ CO
def test_co_press():
    m = a.parse_co_press((F / "co_press.html").read_text())
    assert m == {"as_of": "2026-10-29", "returned": 1335298, "xlsx": ["20261029GenElectionActivity.xlsx"]}


def test_co_activity():
    r = a.parse_co_activity((F / "co_activity_trim.xlsx").read_bytes(), "2026-10-29")
    adams = db.county_fips("co", "Adams")
    assert adams == "08001"
    assert cell(r["county"], "total", "D", county=adams) == 33141
    assert cell(r["county"], "total", "R", county=adams) == 18806
    assert cell(r["county"], "total", "U", county=adams) == 38777
    assert cell(r["county"], "total", "other", county=adams) == 1475
    assert cell(r["county"], "total", "ALL", county=adams) == 92199
    assert cell(r["county"], "mail_returned", "ALL", county=adams) == 91197
    assert cell(r["county"], "inperson_early", "ALL", county=adams) == 1002
    # mail + in-person = all returned, per party, county and state
    for p in ("D", "R", "U", "other", "ALL"):
        assert (cell(r["county"], "mail_returned", p, county=adams) + cell(r["county"], "inperson_early", p, county=adams)
                == cell(r["county"], "total", p, county=adams))
        assert (cell(r["state"], "mail_returned", p) + cell(r["state"], "inperson_early", p) == cell(r["state"], "total", p))
    assert cell(r["state"], "total", "ALL") == 1335298
    assert cell(r["state"], "total", "D") == 422326
    assert cell(r["state"], "total", "other") == 1335298 - 422326 - 328816 - 567398
    # age bands: summed over genders, every band set adds up to the statewide count
    bands = {x["age_band"] for x in r["state"] if "age_band" in x}
    assert bands == {"<18", "18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"}
    assert cell(r["state"], "mail_returned", "ALL", age="18-24") == 31619 + 27369 + 886 + 384
    assert sum(cell(r["state"], "total", "ALL", age=b) for b in bands) == 1335298
    assert {x["as_of_date"] for x in r["county"] + r["state"]} == {"2026-10-29"}


def test_co_rejects_inconsistent_party_sum(tmp_path):
    import openpyxl
    wb = openpyxl.load_workbook(F / "co_activity_trim.xlsx")
    ws = wb["All_Returned_Ballots_By_County"]
    ws.cell(row=3, column=6).value += 7                     # Adams DEM no longer adds up to its grand total
    p = tmp_path / "bad.xlsx"
    wb.save(p)
    with pytest.raises(ValueError, match="party sum"):
        a.parse_co_activity(p.read_bytes(), "2026-10-29")


# ------------------------------------------------------------------------------------------------ CT
def test_ct_page():
    r = a.parse_ct_page((F / "ct_page.html").read_text())
    assert r["as_of"] == "2026-10-29"                       # title date, page published 10/30
    assert {x["state"] for x in r["rows"]} == {"ct"}
    got = {(x["measure"], x["party"]): x["count"] for x in r["rows"]}
    assert got[("total", "ALL")] == 394407
    assert got[("mail_requested", "ALL")] == 70842
    assert got[("mail_returned", "ALL")] == 47128 and got[("inperson_early", "ALL")] == 347279
    assert got[("inperson_early", "D")] == 169667 and got[("mail_returned", "R")] == 8637
    assert got[("mail_returned", "U")] == 13625 and got[("inperson_early", "other")] == 4579
    assert got[("total", "D")] == 169667 + 24234
    assert sum(got[("total", p)] for p in ("D", "R", "U", "other")) == got[("total", "ALL")]


def test_ct_rejects_unreconciled_party_table():
    html = (F / "ct_page.html").read_text().replace("169,667", "169,668", 1)
    with pytest.raises(ValueError, match="reconcile"):
        a.parse_ct_page(html)


def test_ct_validate_skips_unpublished_page():
    with pytest.raises(db.Skip):
        a._validate_ct_page(b"<html><body><h1>Press Releases</h1><p>Hello</p></body></html>")


# ------------------------------------------------------------------------------------------------ DE
def test_de_page():
    r = a.parse_de_page((F / "de_page.html").read_text())
    assert {x["as_of_date"] for x in r["state"] + r["county"]} == {"2026-10-30"}      # 'Last Updated ... 3:12 PM'
    assert cell(r["state"], "mail_returned") == 18452 and cell(r["state"], "inperson_early") == 109559
    assert cell(r["state"], "total") == 128011
    nc, kent, sussex = (db.county_fips("de", n) for n in ("New Castle", "Kent", "Sussex"))
    assert (nc, kent, sussex) == ("10003", "10001", "10005")
    assert cell(r["county"], "mail_returned", county=nc) == 9269
    assert cell(r["county"], "inperson_early", county=sussex) == 52879
    assert cell(r["county"], "total", county=kent) == 19631
    assert len(r["county"]) == 9 and len(r["state"]) == 3


def test_de_rejects_non_additive_table():
    with pytest.raises(ValueError):
        a.parse_de_page((F / "de_page.html").read_text().replace("48,953", "48,954"))


# ------------------------------------------------------------------------------------------------ HI
def test_hi_page():
    r = a.parse_hi_page((F / "hi_page.html").read_text())
    assert r["as_of"] == "2026-10-31"
    assert cell(r["state"], "total") == 230805
    assert cell(r["state"], "inperson_early") == 4523
    assert cell(r["state"], "mail_returned") == 225237 + 1045          # MAIL Voted + ELECT Voted
    assert cell(r["state"], "mail_requested") == 725606 + 3779         # MAIL Sent + ELECT Sent
    hon = db.county_fips("hi", "Honolulu")
    assert hon == "15003"
    assert cell(r["county"], "total", county=hon) == 145962
    assert cell(r["county"], "mail_returned", county=hon) == 142487 + 670
    assert cell(r["county"], "total", county=db.county_fips("hi", "Kaua'i")) == 15518
    assert {x["county_fips"] for x in r["county"]} == {"15001", "15003", "15007", "15009"}
    for k in ("total", "inperson_early", "mail_returned"):
        assert sum(x["count"] for x in r["county"] if x["measure"] == k) == cell(r["state"], k)


def test_hi_recon_pdf_text():
    r = a.parse_hi_recon_text((F / "hi_recon.txt").read_text())
    assert r["stamp"] == "2026-10-31T03:12:00"
    assert r["as_of"] == "2026-10-30"                                  # stamped 3:12 AM: previous day's close
    assert cell(r["state"], "total") == 230724
    assert cell(r["state"], "inperson_early") == 4443
    assert cell(r["county"], "total", county="15001") == 41703
    assert len(r["county"]) == 16


def test_hi_recon_rejects_bad_sum():
    t = (F / "hi_recon.txt").read_text().replace("230724", "230725")
    with pytest.raises(ValueError):
        a.parse_hi_recon_text(t)


# ------------------------------------------------------------------------------------------------ ID
ID_PARTY = ("PartyDesc,Abs. Issued,Abs. Returned,Abs. Return Rate,Early Voted,Total Voted\n"
            "Constitution,242,149,61.6,254,403\nDemocratic,29096,21488,73.9,21086,42574\nLibertarian,914,539,59,695,1234\n"
            "Republican,93699,67888,72.5,72070,139958\nUnaffiliated,32928,21763,66.1,28267,50030\n").encode()
ID_AGE = ("AgeBin,abs,ev,n\n18-29,5246,8023,13269\n30-44,12088,14910,26998\n45-59,19126,23312,42438\n"
          "60-74,41957,49446,91403\n75+,33410,26681,60091\n").encode()


def test_id_totals_only():
    r = a.parse_id((F / "id_totals.json").read_bytes(), None, None, None)
    assert r["as_of"] == "2026-10-29"                                  # '10/30 06:14 AM MDT' = overnight run
    assert {(x["measure"]): x["count"] for x in r["state"]} == {"mail_requested": 156879, "mail_returned": 111827,
                                                                "inperson_early": 122372, "total": 234199}
    assert r["county"] == [] and r["warnings"] == []


def test_id_party_age_county():
    county_csv = ("ResCountyDesc,Returned,total_issued,return_rate,early_voting,total_voted\n"
                  "Ada,38670,57297,67.5,47357,86027\nBear Lake,576,830,69.4,0,576\n").encode()
    js = json.loads((F / "id_totals.json").read_bytes())
    r = a.parse_id(json.dumps(js).encode(), ID_PARTY, ID_AGE, county_csv)
    assert any("county" in w for w in r["warnings"])                  # two counties do not sum to the statewide totals
    assert r["county"] == []
    assert cell(r["state"], "total", "D") == 42574 and cell(r["state"], "total", "R") == 139958
    assert cell(r["state"], "total", "U") == 50030
    assert cell(r["state"], "total", "other") == 403 + 1234           # Constitution + Libertarian
    assert cell(r["state"], "mail_returned", "R") == 67888
    assert cell(r["state"], "mail_requested", "D") == 29096
    assert cell(r["state"], "inperson_early", "ALL", age="75+") == 26681
    assert cell(r["state"], "mail_returned", "ALL", age="18-29") == 5246
    assert sum(cell(r["state"], "total", "ALL", age=b) for b in ("18-29", "30-44", "45-59", "60-74", "75+")) == 234199


def test_id_county_when_it_sums_to_totals():
    js = [{"name": "total_issued", "value": "865"}, {"name": "returned", "value": "400"}, {"name": "return_rate", "value": "46"},
          {"name": "update_time", "value": "10/30 02:00 PM MDT"}, {"name": "early_voting", "value": "100"},
          {"name": "total_voted", "value": "500"}]
    county_csv = ("ResCountyDesc,Returned,total_issued,return_rate,early_voting,total_voted\n"
                  "Ada,300,600,50,70,370\nBear Lake,100,265,37.7,30,130\n").encode()
    r = a.parse_id(json.dumps(js).encode(), None, None, county_csv)
    assert r["as_of"] == "2026-10-30"                                  # afternoon stamp: same day
    assert r["warnings"] == []
    ada = db.county_fips("id", "Ada")
    assert ada == "16001"
    assert cell(r["county"], "total", county=ada) == 370 and cell(r["county"], "mail_requested", county=ada) == 600
    assert cell(r["county"], "inperson_early", county="16007") == 30


def test_id_party_table_that_disagrees_is_skipped():
    bad = ID_PARTY.replace(b"139958", b"139959")
    r = a.parse_id((F / "id_totals.json").read_bytes(), bad, None, None)
    assert any("party" in w for w in r["warnings"])
    assert {x["party"] for x in r["state"]} == {"ALL"}


# ------------------------------------------------------------------------------------------------ CA
def test_ca_bsr():
    r = a.parse_ca_bsr_text((F / "ca_bsr.txt").read_text())
    assert r["as_of"] == "2026-10-30"
    assert len(r["county"]) == 58 * 5
    la = db.county_fips("ca", "Los Angeles")
    assert la == "06037"
    assert cell(r["county"], "mail_requested", county=la) == 5949471
    assert cell(r["county"], "mail_returned", county=la) == 1046228
    assert cell(r["county"], "mail_accepted", county=la) == 1037110
    assert cell(r["county"], "inperson_early", county=la) == 66848
    assert cell(r["county"], "total", county=la) == 1113076
    assert cell(r["county"], "total", county=db.county_fips("ca", "San Luis Obispo")) == 62132
    assert cell(r["state"], "total") == 5545910 and cell(r["state"], "mail_returned") == 5396096
    assert cell(r["state"], "mail_accepted") == 5345579
    for k in ("mail_returned", "mail_accepted", "inperson_early", "total"):
        assert sum(x["count"] for x in r["county"] if x["measure"] == k) == cell(r["state"], k)


def test_ca_rejects_row_that_does_not_reconcile():
    t = (F / "ca_bsr.txt").read_text().replace("235,137", "235,138")
    with pytest.raises(ValueError, match="reconcile"):
        a.parse_ca_bsr_text(t)


def test_ca_rejects_missing_county():
    t = "\n".join(l for l in (F / "ca_bsr.txt").read_text().splitlines() if not l.startswith("Yuba "))
    with pytest.raises(ValueError, match="58 counties"):
        a.parse_ca_bsr_text(t)


# ------------------------------------------------------------------------------------------------ DC
DC_DB = """DC Board of Elections
2026 General Election
Ballots Received by Drop Box/Ward
WARD 1 WARD 2 WARD 3 WARD 4 WARD 5 WARD 6 WARD 7 WARD 8 TOTAL
October 13, 2026 680 823 999 457 386 861 323 163 4692
October 14, 2026 164 225 210 107 120 222 59 55 1162
October 15, 2026 133 206 258 135 167 227 93 75 1294
October 16, 2026 0 0 0 0 0 0 0 0 0
October 17, 2026 0
TOTAL 977 1254 1467 699 673 1310 475 293 7148
"""
DC_USPS = """GENERAL ELECTION 2026
BALLOTS RECEIVED BY USPS
October 8, 2026 1,094
October 13, 2026 6,557
October 14, 2026 0
October 15, 2026 673
October 16, 2026
October 17, 2026
TOTAL 8324
"""
DC_EVC = """EARLY VOTE CENTERS - 2026 GENERAL ELECTION
WARD 14-Oct 15-Oct 16-Oct TOTAL
1 COLUMBIA HEIGHTS COMMUNITY CENTER 209 147 356
TOTAL 3361 3165 0 6526
"""


def test_dc_series():
    s = a._dc_series(DC_DB)
    assert s["2026-10-13"] == 4692 and s["2026-10-17"] == 0
    u = a._dc_series(DC_USPS)
    assert u["2026-10-08"] == 1094 and u["2026-10-16"] is None
    assert a._dc_evc_series(DC_EVC) == {"2026-10-14": 3361, "2026-10-15": 3165, "2026-10-16": 0}


def test_dc_evc_rejects_bad_grand_total():
    with pytest.raises(ValueError):
        a._dc_evc_series(DC_EVC.replace("6526", "6527"))


def test_dc_rows_are_cumulative_and_stop_at_the_last_reported_day():
    rows = a.dc_rows(DC_DB, DC_USPS, DC_EVC)
    mail = {r["as_of_date"]: r["count"] for r in rows if r["measure"] == "mail_returned"}
    assert sorted(mail) == ["2026-10-13", "2026-10-14", "2026-10-15"]     # Oct 16/17: zero or blank = not reported yet
    assert mail["2026-10-13"] == 4692 + 1094 + 6557
    assert mail["2026-10-14"] == mail["2026-10-13"] + 1162 + 0
    assert mail["2026-10-15"] == mail["2026-10-14"] + 1294 + 673
    ip = {r["as_of_date"]: r["count"] for r in rows if r["measure"] == "inperson_early"}
    assert ip == {"2026-10-13": 0, "2026-10-14": 3361, "2026-10-15": 3361 + 3165}
    tot = {r["as_of_date"]: r["count"] for r in rows if r["measure"] == "total"}
    assert all(tot[d] == mail[d] + ip[d] for d in mail)
    assert {r["party"] for r in rows} == {"ALL"} and {r["state"] for r in rows} == {"dc"}


def test_dc_links_found_on_home_page():
    html = ('<a href="/getContentAsset/8a93/5ebb/2026-General-Ballots-by-DB-10132026.pdf?language=en">DB</a>'
            '<a href="/getContentAsset/727b/5ebb/2026-General-Ballots-by-EVC-10262026.pdf?language=en">EVC</a>'
            '<a href="/getContentAsset/07b7/5ebb/2026-General-Ballots-by-USPS-10082026.pdf?language=en">USPS</a>')
    links = a._dc_links(html)
    assert set(links) == {"db", "evc", "usps"}
    assert links["usps"].startswith("https://www.dcboe.org/getContentAsset/07b7/") and links["usps"].endswith("language=en")
