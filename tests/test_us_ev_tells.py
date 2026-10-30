"""Count-night tells (analysis/us_ev_tells.py): gap computation and vote-type table on tiny synthetic fixtures, no network."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_P = Path(__file__).resolve().parents[1] / "analysis" / "us_ev_tells.py"
_spec = importlib.util.spec_from_file_location("us_ev_tells", _P)
T = importlib.util.module_from_spec(_spec)
sys.modules["us_ev_tells"] = T
_spec.loader.exec_module(T)


def _rows(spec):
    """spec: [(unit, 'HH:MM', d, r)] on one day -> unit/time/d/r frame."""
    return pd.DataFrame([{"unit": u, "time": pd.Timestamp(f"2024-11-05 {t}"), "d": d, "r": r} for u, t, d, r in spec])


def test_margin_and_classify():
    assert T.margin(60, 40) == pytest.approx(20.0)
    assert T.margin(0, 0) is None
    assert [T.classify_mode(x) for x in ("EARLY VOTING", "ADVANCED VOTING", "ONE STOP", "IN-PERSON ABSENTEE", "ABSENTEE BY MAIL", "MAIL-IN",
                                         "ELECTION DAY", "VOTE CENTER", "PROVISIONAL", "FAILSAFE PROVISIONAL", "LATE EARLY VOTING",
                                         "IN-PERSON", "NOT ABSENTEE", "ABSENTEE/EARLY", "MAIL-IN CANVASS 1")] == \
        ["early", "early", "early", "early", "mail", "mail", "eday", "eday", "other", "other", "early", "eday", "eday", "mail", "mail"]
    assert T.classify_mode("TOTAL") is None and T.classify_mode("EARLY TSX") == "early" and T.classify_mode("XYZ") is None


def test_count_series_carries_forward_and_drops_glitches():
    rows = _rows([("a", "19:00", 10, 10), ("b", "19:30", 30, 10), ("a", "20:00", 20, 20),
                  ("a", "20:30", 5, 5),            # glitch: lowers a's running maximum
                  ("b", "21:00", 40, 20)])
    s = T.count_series(rows)
    assert list(s.index.strftime("%H:%M")) == ["19:00", "19:30", "20:00", "21:00"]
    assert s.loc[pd.Timestamp("2024-11-05 19:30"), ["d", "r"]].tolist() == [40, 20]       # a carried forward, b new
    assert s.iloc[-1].tolist() == [60, 40]                                                  # a = 20/20, b = 40/20


def test_crossings_interpolate_and_gap():
    # final 600 D / 400 R (margin +20); the count reaches 20 % / 50 % / 100 % of the final vote at 20:00 / 22:00 / 00:00
    ser = pd.DataFrame({"d": [0.0, 100.0, 300.0, 600.0], "r": [0.0, 100.0, 200.0, 400.0]},
                       index=pd.to_datetime(["2024-11-05 19:00", "2024-11-05 20:00", "2024-11-05 22:00", "2024-11-06 00:00"]))
    cr = T.crossings(ser, 600, 400, (10, 25, 50, 75))
    assert (cr[10]["d"], cr[10]["r"]) == pytest.approx((50, 50))             # halfway between the empty and the 20 % snapshot
    assert cr[10]["hours"] == 0.0                                              # before the first snapshot with votes: clamped
    assert (cr[25]["d"], cr[25]["r"]) == pytest.approx((100 + 200 / 6, 100 + 100 / 6))
    assert cr[25]["hours"] == pytest.approx(1 / 6 * 2)                         # 1/6 of the way from 20:00 to 22:00, in hours
    assert (cr[50]["d"], cr[50]["r"]) == pytest.approx((300, 200))
    assert (cr[75]["d"], cr[75]["r"]) == pytest.approx((450, 300))
    g = T.cycle_gaps(ser, 600, 400, (10, 25, 50, 75))
    assert g["final_margin"] == pytest.approx(20.0)
    assert g["gaps"]["10"] == pytest.approx(-20.0)                             # raw margin 0 vs final +20
    assert g["gaps"]["25"] == pytest.approx(T.margin(100 + 200 / 6, 100 + 100 / 6) - 20, abs=0.06)
    assert g["gaps"]["50"] == pytest.approx(0.0) and g["gaps"]["75"] == pytest.approx(0.0)
    assert g["feed_coverage"] == 1.0
    assert len(g["blocks"]) == 4 and g["blocks"][2] == pytest.approx(20.0)     # votes between 50 % and 75 %: 150 D / 100 R


def test_crossings_first_report_already_past_q():
    # one jump 0 -> 50 % of the final vote: the 10 % crossing scales the first report, its margin is the report's margin
    ser = pd.DataFrame({"d": [0.0, 400.0, 600.0], "r": [0.0, 100.0, 400.0]},
                       index=pd.to_datetime(["2024-11-05 19:00", "2024-11-05 20:00", "2024-11-05 21:00"]))
    cr = T.crossings(ser, 600, 400, (10, 75))
    assert T.margin(cr[10]["d"], cr[10]["r"]) == pytest.approx(T.margin(400, 100))
    assert cr[10]["d"] + cr[10]["r"] == pytest.approx(100)
    assert 75 in cr and cr[75]["d"] + cr[75]["r"] == pytest.approx(750)


def test_unreached_q_is_absent():
    ser = pd.DataFrame({"d": [0.0, 50.0], "r": [0.0, 50.0]}, index=pd.to_datetime(["2024-11-05 19:00", "2024-11-05 20:00"]))
    g = T.cycle_gaps(ser, 600, 400, (10, 25, 50, 75))
    assert set(g["gaps"]) == {"10"} and g["blocks"] == []


def test_summarise_over_cycles():
    s = T.summarise({2020: {"10": -10.0, "25": -4.0, "50": None}, 2024: {"10": -2.0, "25": 2.0, "50": -1.0}})
    assert s["10"] == {"mean": -6.0, "min": -10.0, "max": -2.0, "n": 2}
    assert s["25"]["mean"] == pytest.approx(-1.0) and s["25"]["n"] == 2
    assert s["50"]["n"] == 1 and s["50"]["mean"] == -1.0
    assert s["75"] == {"mean": None, "min": None, "max": None, "n": 0}


def test_label():
    assert T.label({2020: {"25": -8.0, "50": -4.0}, 2024: {"25": -3.0, "50": -1.0}}).startswith("red mirage")
    assert T.label({2020: {"25": 6.0, "50": 4.0}}).startswith("blue mirage")
    assert T.label({2020: {"25": -9.0, "50": -5.0}, 2024: {"25": 8.0, "50": 4.0}}).startswith("cycle-dependent")
    assert T.label({2020: {"25": 0.5, "50": -0.5}}).startswith("little early bias")
    assert T.label({}) == "no timed data"


def test_geography_large_units_early():
    # 5 units; the biggest (a) reports first and is most Democratic
    spec = [("a", "19:00", 600, 200), ("b", "20:00", 60, 40), ("c", "20:00", 30, 30), ("d", "20:00", 20, 30), ("e", "20:00", 10, 20),
            ("a", "21:00", 650, 250)]
    g = T.geography(_rows(spec), q=25)
    assert g["units"] == 5 and g["big_units"] == 1
    assert g["big_at_q"] > g["big_final"]
    assert g["big_margin"] > g["rest_margin"]
    assert T.geography(_rows(spec[:3])) is None       # fewer than MIN_UNITS units


def _modes():
    rows = []

    def add(st, fips, mode, d, r):
        rows.extend([{"state": st, "fips": fips, "mode": mode, "party": "DEMOCRAT", "votes": d},
                     {"state": st, "fips": fips, "mode": mode, "party": "REPUBLICAN", "votes": r},
                     {"state": st, "fips": fips, "mode": mode, "party": "LIBERTARIAN", "votes": 5}])
    # XX: two counties, full mode split + matching TOTAL rows
    for f, (e, m, d_) in {"001": ((300, 100), (400, 100), (100, 400)), "002": ((100, 100), (200, 50), (50, 300))}.items():
        add("XX", f, "EARLY VOTING", *e)
        add("XX", f, "ABSENTEE BY MAIL", *m)
        add("XX", f, "ELECTION DAY", *d_)
        tot = [e[0] + m[0] + d_[0], e[1] + m[1] + d_[1]]
        add("XX", f, "TOTAL", *tot)
    # YY: only TOTAL rows -> no vote-type table
    add("YY", "001", "TOTAL", 500, 500)
    # ZZ: one big county with modes, one small TOTAL-only county (coverage 90 % cut)
    add("ZZ", "001", "ELECTION DAY", 450, 450)
    add("ZZ", "001", "EARLY", 50, 50)
    add("ZZ", "002", "TOTAL", 500, 500)
    return pd.DataFrame(rows)


def test_mode_table():
    t = T.mode_table(_modes())
    assert set(t) == {"XX"}                           # YY has no modes; ZZ is only 50 % classified
    x = t["XX"]
    assert x["coverage"] == 1.0 and x["counties"] == 2
    # early D 400 / R 200, mail D 600 / R 150, Election Day D 150 / R 700 (the Libertarian rows are not two-party)
    assert x["early"]["margin"] == pytest.approx(T.margin(400, 200), abs=0.05)
    assert x["mail"]["margin"] == pytest.approx(T.margin(600, 150), abs=0.05)
    assert x["eday"]["margin"] == pytest.approx(T.margin(150, 700), abs=0.05)
    assert (x["early"]["share"], x["mail"]["share"], x["eday"]["share"]) == pytest.approx((600 / 2200, 750 / 2200, 850 / 2200), abs=0.001)
    assert "other" not in x
    low = T.mode_table(_modes(), min_coverage=0.4)
    assert "ZZ" in low and low["ZZ"]["coverage"] == pytest.approx(0.5)


def test_mode_table_needs_a_real_split():
    rows = [{"state": "WW", "fips": f, "mode": "ELECTION DAY", "party": p, "votes": v} for f in ("001", "002")
            for p, v in (("DEMOCRAT", 100), ("REPUBLICAN", 200))]
    assert T.mode_table(pd.DataFrame(rows)) == {}      # everything labelled Election Day
    rows.append({"state": "WW", "fips": None, "mode": "ABSENTEE", "party": "DEMOCRAT", "votes": 100})   # county-less pseudo-precinct kept
    assert "mail" in T.mode_table(pd.DataFrame(rows))["WW"]


def test_mode_table_rejects_modes_that_do_not_add_up():
    d = _modes()
    d = d[d["state"] == "XX"].copy()
    d.loc[(d["mode"] == "TOTAL") & (d["fips"] == "001"), "votes"] *= 3     # TOTAL far above the modes: county not classified
    t = T.mode_table(d, min_coverage=0.0)
    assert t["XX"]["counties"] == 1


def _ga(groups):
    opt = lambda name, p, vals: {"name": [{"text": name}], "party": {"abbreviation": p}, "voteCount": sum(vals.values()),
                                 "groupResults": [{"groupName": [{"text": g}], "voteCount": v} for g, v in vals.items()]}
    return json.dumps({"ballotItemWithBreakdown": {"name": [{"text": "President"}], "breakdownResults": [{}, {}],
                                                    "summaryResults": {"ballotOptions": [opt("D", "Dem", groups[0]), opt("R", "Rep", groups[1]),
                                                                                           opt("L", "Lib", {"Election Day": 999})]}}}).encode()


def test_parse_ga_modes():
    d = {"Election Day Votes": 100, "Absentee by Mail Votes": 300, "Advanced Voting Votes": 500, "Provisional Votes": 10}
    r = {"Election Day Votes": 300, "Absentee by Mail Votes": 100, "Advanced Voting Votes": 500, "Provisional Votes": 10}
    row = T.parse_ga_modes(_ga((d, r)))
    assert row["mail"]["margin"] == pytest.approx(50.0) and row["eday"]["margin"] == pytest.approx(-50.0)
    assert row["early"]["margin"] == pytest.approx(0.0)
    assert row["early"]["share"] == pytest.approx(1000 / 1820, abs=0.001)
    assert row["counties"] == 2
    d2 = {"Election Day": 1, "Advance Voting": 2}
    assert T.parse_ga_modes(_ga((d2, d2)))["early"]["share"] == pytest.approx(4 / 6, abs=0.001)    # 2022 / 2024 label spellings


def test_best_modes_prefers_coverage_then_president():
    cands = [("XX", 2022, "US SENATE", {"coverage": 0.95}), ("XX", 2022, "GOVERNOR", {"coverage": 0.95}),
             ("YY", 2022, "GOVERNOR", {"coverage": 0.91}), ("YY", 2022, "US SENATE", {"coverage": 1.0})]
    b = T.best_modes(cands)
    assert b[("XX", 2022)]["race"] == "GOVERNOR" and b[("YY", 2022)]["race"] == "US SENATE"


def test_release_mix():
    df = pd.DataFrame({"unit": list("abc"), "final": [60, 30, 10],
                       "method": ["Released as a precinct as first report of the night", "Merged into voters' home precincts and released with cumulative results",
                                  "Released separately AFTER election night"]})
    assert T.release_mix(df) == {"first": 0.6, "merged": 0.3, "end": 0.1}
