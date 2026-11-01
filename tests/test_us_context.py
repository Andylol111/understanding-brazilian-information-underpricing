"""Aggregation in analysis/us_context.py: group coding, same-day cumulation, shift tables, registration change, margins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import us_context as c  # noqa: E402

H = ["county_desc", "ballot_rtn_dt", "voter_party_code", "ballot_rtn_status", "race", "ethnicity", "gender", "age"]


def test_group_coding():
    assert c.race_group("WHITE", "HISPANIC or LATINO") == "Hispanic"
    assert c.race_group("B", "NL") == "Black"
    assert c.race_group("BLACK or AFRICAN AMERICAN", "NOT HISPANIC or NOT LATINO") == "Black"
    assert c.race_group("M", "UN") == "Other / unknown"
    assert [c.age_group(a) for a in (17, 18, 25, 26, 40, 41, 65, 66, "Age 41 - 65", "Age Over 66")] == \
        [None, "18-25", "18-25", "26-40", "26-40", "41-65", "41-65", "66+", "41-65", "66+"]
    assert c.party_group("LIB") == "Other party" and c.party_group("una") == "Unaffiliated"
    assert c.density_tier(500) == c.DENS_ORDER[0] and c.density_tier(99) == c.DENS_ORDER[2]


def test_count_cumulate_shift():
    rows = [H,
            ["WAKE", "10/30/2026", "DEM", "ACCEPTED", "BLACK", "NOT HISPANIC", "F", "70"],
            ["WAKE", "10/31/2026", "REP", "ACCEPTED - CURED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "11/01/2026", "REP", "ACCEPTED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "10/30/2026", "REP", "SPOILED", "WHITE", "NOT HISPANIC", "M", "30"],
            ["WAKE", "10/30/2026", "REP", "ACCEPTED", "WHITE", "NOT HISPANIC", "M", "12"]]
    cnt = c.nc_count(rows, {"WAKE": c.DENS_ORDER[0]})
    assert sum(cnt.values()) == 3                                   # bad status and under-18 dropped
    now = c.cumulate(cnt, "2026-10-31")
    assert now["_total"]["all"] == 2 and now["race"]["Black"] == 1 and now["party"]["Republican"] == 1
    past = c.cumulate(cnt, "2026-10-30")
    t = {(r["dim"], r["group"]): r for r in c.shift_table(now, past)}
    assert t[("race", "Black")]["share_now"] == 50.0 and t[("race", "Black")]["share_2022"] == 100.0
    assert t[("race", "Black")]["share_change_pp"] == -50.0
    assert t[("age", "66+")]["count_change_pct"] == 0.0


def test_registration_change_and_moe():
    s = [{"date": "2022-11-01", "D": 100, "R": 90, "O": 50, "U": None, "total": 240, "basis": "active"},
         {"date": "2026-10-01", "D": 110, "R": 120, "O": 70, "U": None, "total": 300, "basis": "active"}]
    r = c._reg_summary("XX", s, "u")
    assert r["change"]["D"]["n"] == 10 and r["change"]["R"]["pct"] == 33.33 and r["d_minus_r_change"] == -20
    assert c.moe(100) == 9.8 and c.moe(None) is None and c.moe(0) is None
