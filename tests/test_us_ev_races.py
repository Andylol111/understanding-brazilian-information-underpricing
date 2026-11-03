from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

P = Path(__file__).parents[1] / "analysis" / "us_ev_races.py"
spec = importlib.util.spec_from_file_location("us_ev_races", P)
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)


def _race(key, state, office, modeled=True, share=0.5, p=0.5):
    return {"race_key": key, "race_id": key, "state": state, "state_name": state.upper(), "office": office, "modeled": modeled,
            "dem_side": {"party": "D"}, "precount": {"share": share, "P": p} if modeled else None, "estimate": None,
            "polls": {"avg": 0.52, "p_win": 0.6, "n_polls": 3}, "pm": {"price": 0.55}, "kalshi": {"price": 0.5}}


def _tm(st, d, r, o, as_of="2026-11-02"):
    return {"generated_utc": "t", "states": {st: {"name": st, "banked": {"value": 1000, "basis": "b", "total_2022": 900}, "expected": {"votes": 2000},
            "banked_pct_expected": 50.0, "banked_pct_2022": 111.1, "party": [{"kind": "pre_election_day", "as_of": as_of, "D": d, "R": r, "other": o, "source": "s"}],
            "order": {"text": "first", "class": "early_first", "config": "c.toml"}}}}


def _panel(st, d, r, o, date="2022-11-07"):
    return [{"state": st, "year": 2022, "date": date, "kind": "total", "D": d, "R": r, "O": o}]


def _miss(st, act, t="Sen-G"):
    return [{"race": f"2022_{t}_{st.upper()}", "cycle": 2022, "state": st, "act": act}]


def test_offset_math_toy():
    # 2022: registered D share of D+R = 60, certified D two-party = 52 -> offset -8
    assert er.ev_dshare(60, 40) == pytest.approx(60.0)
    assert er.offset(52.0, 60.0) == pytest.approx(-8.0)
    assert er.offset(None, 60.0) is None and er.offset(52.0, None) is None
    # 2026 registered D share 55 -> EV-implied 47
    assert 55.0 + er.offset(52.0, 60.0) == pytest.approx(47.0)


def test_verdict_threshold_and_sign():
    assert er.verdict(52.1, 51.6)["text"] == "agrees"
    assert er.verdict(53.6, 51.6)["text"] == "agrees"          # exactly 2.0 is within
    v = er.verdict(54.0, 51.0)
    assert v["text"] == "EV leans D by 3.0" and v["side"] == "D"
    v = er.verdict(40.0, 51.0)
    assert v["text"] == "EV leans R by 11.0" and v["side"] == "R"
    assert er.verdict(None, 51.0) is None


def test_build_implied_on_toy_data():
    ov = {"as_of": "x", "offices": {"senate": {"races": [_race("aa-sen", "aa", "senate", share=0.47)]}, "governor": {"races": []}}}
    out = er.build(ov, _tm("AA", 550, 450, 300), _panel("aa", 600, 400, 200), _miss("aa", 52.0), now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    r = out["races"][0]
    assert r["implied"]["ok"] and r["implied"]["offset"] == pytest.approx(-8.0)
    assert r["implied"]["implied"] == pytest.approx(47.0)
    assert r["implied"]["vs_model"]["text"] == "agrees"
    assert out["n_states_with_estimate"] == 1 and out["states_with_estimate"] == ["AA"]
    st = out["states"]["AA"]
    assert st["party"]["d_pct"] == pytest.approx(100 * 550 / 1300)
    # 2022 mix 600/400/200 of 1200 -> D 50 %: shift = 42.3 - 50
    assert st["shift"]["d_pt"] == pytest.approx(100 * 550 / 1300 - 50.0)
    assert out["test"]["direction_pct"] == 61


def test_missing_2022_party_figure_gives_no_estimate():
    ov = {"as_of": "x", "offices": {"senate": {"races": [_race("aa-sen", "aa", "senate")]}, "governor": {"races": []}}}
    out = er.build(ov, _tm("AA", 550, 450, 300), [], _miss("aa", 52.0))
    imp = out["races"][0]["implied"]
    assert imp == {"ok": False, "why": "no 2022 party figure at the same stage for this state"}
    assert out["n_states_with_estimate"] == 0
    assert out["states"]["AA"]["party_2022"] is None and out["states"]["AA"]["shift"] is None


def test_2022_stage_must_match_days_before():
    # the 2026 figure is as of ED-1 (2026-11-02); a 2022 row from ED-3 must not be used, a later one never
    assert er.match_2022(_panel("aa", 6, 4, 2, "2022-11-05"), "aa", 1) is None
    assert er.match_2022(_panel("aa", 6, 4, 2, "2022-11-08"), "aa", 1) is None
    assert er.match_2022(_panel("aa", 6, 4, 2, "2022-11-07"), "aa", 1)["D"] == 6
    assert er.match_2022(_panel("aa", 6, 4, 2, "2022-11-07"), "aa", None) is None


def test_missing_2022_result_and_state_without_ev():
    ov = {"as_of": "x", "offices": {"senate": {"races": [_race("aa-sen", "aa", "senate"), _race("bb-sen", "bb", "senate", modeled=False)]}, "governor": {"races": []}}}
    out = er.build(ov, _tm("AA", 550, 450, 300), _panel("aa", 600, 400, 200), [])
    assert out["races"][0]["implied"]["why"] == "no 2022 certified two-party result for this office"
    b = out["races"][1]
    assert b["ev_state"] is None and b["implied"]["why"] == "no early-vote party figure for this state"
    assert b["model"]["model_share"] is None


def test_other_office_result_is_flagged():
    ov = {"as_of": "x", "offices": {"senate": {"races": [_race("aa-sen", "aa", "senate", share=0.47)]}, "governor": {"races": []}}}
    out = er.build(ov, _tm("AA", 550, 450, 300), _panel("aa", 600, 400, 200), _miss("aa", 52.0, "Gov-G"))
    imp = out["races"][0]["implied"]
    assert imp["act22_office"] == "governor" and "2022 governor result used" in imp["note"]


def test_secondary_2022_only_at_matching_stage():
    ia = er.found_2022("IA", 1)
    assert ia["prov"] == "secondary" and ia["D"] == 51.4
    assert er.found_2022("IA", 3) is None and er.found_2022("ZZ", 1) is None
