"""Aggregation in analysis/us_context.py and us_ctx_*: harmonised group coding, same-day cumulation, cells with past years, the
source-to-taxonomy mapping, registration change, margins, poll group keys."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import us_ctx_ev as ev  # noqa: E402
import us_ctx_nc as nc  # noqa: E402
import us_ctx_tax as tx  # noqa: E402
import us_context as c  # noqa: E402

H = ["county_desc", "ballot_rtn_dt", "voter_party_code", "ballot_rtn_status", "race", "ethnicity", "gender", "age"]


def test_group_coding():
    assert c.race_group("WHITE", "HISPANIC or LATINO") == "Hispanic"
    assert c.race_group("B", "NL") == "Black"
    assert c.race_group("BLACK or AFRICAN AMERICAN", "NOT HISPANIC or NOT LATINO") == "Black"
    assert c.race_group("M", "UN") == "Other / unknown"
    assert [c.age_group(a) for a in (17, 18, 29, 30, 44, 45, 64, 65, "x")] == [None, "18-29", "18-29", "30-44", "30-44", "45-64", "45-64", "65+", None]
    assert [c.age_ncreg_group(a) for a in (17, 18, 25, 26, 40, 41, 65, 66, "Age 41 - 65", "Age Over 66", "Invalid Birth Dates")] == \
        [None, "18-25", "18-25", "26-40", "26-40", "41-65", "41-65", "66+", "41-65", "66+", None]
    assert c.party_group("LIB") == "Other party" and c.party_group("una") == "Unaffiliated"
    assert c.density_tier(500) == tx.DENS[0] and c.density_tier(99) == tx.DENS[2]


def test_count_cumulate_cells_and_past():
    rows = [H,
            ["WAKE", "10/30/2026", "DEM", "ACCEPTED", "BLACK", "NOT HISPANIC", "F", "70"],
            ["WAKE", "10/31/2026", "REP", "ACCEPTED - CURED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "11/01/2026", "REP", "ACCEPTED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "10/30/2026", "REP", "SPOILED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "10/30/2026", "REP", "ACCEPTED", "WHITE", "NOT HISPANIC", "M", "12"]]
    cnt = c.nc_count(rows, {"WAKE": tx.DENS[0]})
    assert sum(cnt.values()) == 3                                   # bad status and under-18 dropped
    now = c.cumulate(cnt, "2026-10-31")
    assert now["_total"]["all"] == 2 and now["race"]["Black"] == 1 and now["party"]["Republican"] == 1
    assert now["age"]["65+"] == 1 and now["age_ncreg"]["66+"] == 1 and now["age"]["30-44"] == 1
    past = c.cumulate(cnt, "2026-10-30")
    cell = tx.cell(dict(now["race"]), "Black", {2022: dict(past["race"])}, {"Black": 12.5})
    assert cell == {"share": 50.0, "n": 1, "reg_share": 12.5, "past": {"2022": {"share": 100.0, "n": 1}}}
    assert tx.cell(dict(now["race"]), "Asian") is None


def test_same_day():
    assert nc.same_day(2022, "2026-10-31") == "2022-11-05" and nc.same_day(2020, "2026-10-31") == "2020-10-31"
    assert nc.same_day(2018, "2026-10-29") == "2018-11-01"


def test_cells_partial_dim_keeps_denominator_and_own_dims():
    now = {"age": {"45-64": 30, "65+": 40, "_den": 100}, "age_co": {"18-24": 10, "45-54": 30}, "gender": {"Female": 3, "Male": 1}}
    groups, own = tx.cells(now, {2022: {"age": {"45-64": 10, "_den": 50}, "gender": {"Female": 1, "Male": 1}}})
    assert groups["age:45-64"]["share"] == 30.0 and groups["age:45-64"]["past"]["2022"]["share"] == 20.0
    assert "age:18-29" not in groups and own["age_co:18-24"]["share"] == 25.0 and "age_co:18-24" not in groups
    assert groups["gender:Female"]["past"]["2022"] == {"share": 50.0, "n": 1}


def test_taxonomy_and_mapping_table():
    assert [t["dim"] for t in tx.TAXONOMY] == ["race", "age", "gender", "party", "density"]
    assert tx.AGE == ["18-29", "30-44", "45-64", "65+"]
    rows = tx.mapping_rows()
    ok_groups = {(t["dim"], g) for t in tx.TAXONOMY for g in t["groups"]}
    assert all(r["status"] == "mapped" or r["status"].startswith("not mapped") for r in rows)
    assert all((r["dim"], r["group"]) in ok_groups for r in rows if r["status"] == "mapped")
    assert any(r["source"] == tx.S_UF and r["dim"] == "age" and r["group"] is None and r["status"].startswith("not mapped") for r in rows)
    assert {r["group"] for r in rows if r["source"] == tx.S_CO and r["dim"] == "age" and r["status"] == "mapped"} == {"45-64", "65+"}


def test_co_dims_and_2018_report(tmp_path):
    d = ev._co_dims({"Female": 3, "Male": 2, "Undisclosed": 1},
                    {"Under 18": 9, "18-24": 1, "25-34": 2, "35-44": 3, "45-54": 4, "55-64": 5, "65-74": 6, "75+": 7})
    assert d["age"] == {"45-64": 9, "65+": 13, "_den": 28} and d["age_co"]["25-34"] == 2 and "Under 18" not in d["age_co"]
    f = tmp_path / "r.txt"
    f.write_text("FEMALE\n 18-25 1\nFEMALE\u00a0Total 749 9 563,685\nMALE Total 1 528,652\nUNKNOWN Total 8,353\nGrand\u00a0Total 1,100,690\n")
    assert ev._co_2018_report(f) == {"Female": 563685, "Male": 528652, "Undisclosed": 8353, "_all": 1100690}


def test_registration_change_and_moe():
    s = [{"date": "2022-11-01", "D": 100, "R": 90, "O": 50, "U": None, "total": 240, "basis": "active"},
         {"date": "2026-10-01", "D": 110, "R": 120, "O": 70, "U": None, "total": 300, "basis": "active"}]
    r = c._reg_summary("XX", s, "u")
    assert r["change"]["D"]["n"] == 10 and r["change"]["R"]["pct"] == 33.33 and r["d_minus_r_change"] == -20
    assert c.moe(100) == 9.8 and c.moe(None) is None and c.moe(0) is None


def test_group_share():
    assert tx.share({"a": 1, "b": 3}) == {"a": 25.0, "b": 75.0} and tx.share({}) == {}


def test_uf_groups_labels():
    u = {"total": 100, "race": {"White": 50, "Black": 30, "Hispanic": 5, "Asian": 5, "Native American": 2, "Other": 3},
         "age": {"18-25": 10, "26-40": 20, "41-65": 40, "66+": 30}, "gender": {}}
    race, age, gender, cov = ev.uf_groups(u)
    assert race["Other / unknown"] == 5 and sum(race.values()) == 95 and cov == {"race": 95.0, "age": 100.0} and gender == {}
    assert age == u["age"]


def test_poll_group_to_taxonomy():
    assert c.tax_key("race", "AfrAm/Black") == "race:Black" and c.tax_key("race", "White or Caucasian") == "race:White"
    assert c.tax_key("race", "Hisp/Lat") == "race:Hispanic" and c.tax_key("age", "18-29 years") == "age:18-29"
    assert c.tax_key("age", "65+") == "age:65+" and c.tax_key("age", "Under 45") is None and c.tax_key("age", "18-34") is None
    assert c.tax_key("race", "Non-white") is None and c.tax_key("education", "College") is None
