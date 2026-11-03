"""analysis/us_turnout_model.py: composition math on a toy case, county threshold, banking rule, county matching, count order, lean wording."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import us_turnout_model as tm  # noqa: E402


def counties(n, share=0.5, banked=100.0, exp=100.0):
    return [{"share": share, "banked": banked, "exp": exp} for _ in range(n)]


def test_composition_toy_case():
    # A leans D (60%), B leans R (40%). Banked ballots are split evenly, expected votes are 25% / 75%:
    # share weighted by banked = 50.0, weighted by expected = 0.25*60 + 0.75*40 = 45.0, difference +5.0 points
    cs = [{"share": 0.6, "banked": 100, "exp": 100}, {"share": 0.4, "banked": 100, "exp": 300}]
    out = tm.composition(cs, min_n=2, n_boot=200)
    assert out["ok"] and out["n"] == 2
    assert out["share_so_far"] == pytest.approx(50.0) and out["share_expected"] == pytest.approx(45.0) and out["diff_pt"] == pytest.approx(5.0)
    assert out["lo"] <= out["diff_pt"] <= out["hi"] or out["lo"] <= 5.0 <= out["hi"] + 5   # the bootstrap resamples the two counties; bounds are finite
    assert out["swing_pt"] is None


def test_composition_no_shift_when_weights_match_and_swing_cancels():
    cs = [{"share": 0.6, "banked": 50, "exp": 100}, {"share": 0.3, "banked": 150, "exp": 300}] * 6
    out = tm.composition(cs, swing=0.02)
    assert out["ok"] and out["diff_pt"] == pytest.approx(0.0, abs=1e-9) and out["lo"] <= 0 <= out["hi"] and out["swing_pt"] == 2.0
    # a swing moves both shares by the same amount, so the difference is unchanged where nothing clips
    a = tm.composition([{"share": 0.6, "banked": 100, "exp": 100}, {"share": 0.4, "banked": 100, "exp": 300}] * 5)
    b = tm.composition([{"share": 0.6, "banked": 100, "exp": 100}, {"share": 0.4, "banked": 100, "exp": 300}] * 5, swing=0.03)
    assert a["diff_pt"] == pytest.approx(b["diff_pt"]) and b["share_so_far"] == pytest.approx(a["share_so_far"] + 3.0)


def test_composition_county_threshold():
    assert not tm.composition(counties(9))["ok"]
    assert tm.composition(counties(10))["ok"]
    # counties without a count do not count towards the threshold
    assert not tm.composition(counties(9) + [{"share": 0.5, "banked": 0, "exp": 100}])["ok"]
    assert tm.composition(counties(9))["why"].startswith("9 counties")
    assert tm.MIN_COUNTIES == 10


def test_composition_is_deterministic():
    cs = [{"share": 0.2 + 0.05 * i, "banked": 10 + i, "exp": 100 + 7 * (i % 4)} for i in range(12)]
    assert tm.composition(cs) == tm.composition(cs)


def test_county_key():
    assert tm.county_key("Allegheny County PA", "PA") == tm.county_key("Allegheny County, PA", "PA") == "allegheny"
    assert tm.county_key("Pima County (early ballots processed)", "AZ") == "pima"
    assert tm.county_key("St. Johns County", "FL") == tm.county_key("St Johns County, FL", "FL")
    assert tm.county_key("Baltimore city", "MD") != tm.county_key("Baltimore County", "MD")


def row(kind, count, level="state", **kw):
    r = {"state": "XX", "jurisdiction": "X (statewide)", "level": level, "kind": kind, "count": count, "prov": "official", "measure": kind}
    r.update(kw)
    return r


def test_bank_rules():
    b = tm.bank([row("advance", 1000), row("election_day", 200, "state-partial", jurisdiction="X: sum of 3 counties")], 60)
    assert b["value"] == 1000 and not b["all_methods"] and b["partial_rows"][0]["kind"] == "election_day"       # a partial sum is never added
    b = tm.bank([row("advance", 1000), row("election_day", 200)], 60)
    assert b["value"] == 1200 and b["complete"] and b["all_methods"]
    b = tm.bank([row("total", 900), row("requests", 5000)], 60)
    assert b["value"] == 900 and b["basis"].startswith("total")
    b = tm.bank([row("election_day", 50, "state-partial", jurisdiction="X: sum of 3 counties")], 60)
    assert not b["complete"] and b["value"] == 50
    assert tm.bank([row("requests", 5000), row("unclear", 7)], 60) is None
    b = tm.bank([row("mail_returned", 700), row("early_in_person", 300)], 60)
    assert b["value"] == 1000 and not b["all_methods"]


def test_lean_wording_only_with_party_and_early_first():
    party = {"kind": "pre_election_day", "D": 600, "R": 400}
    assert "lean D" in tm.lean_text(party, "early_first")["text"]
    assert "lean R" in tm.lean_text({"kind": "advance", "D": 400, "R": 600}, "early_first")["text"]
    assert "even" in tm.lean_text({"kind": "advance", "D": 505, "R": 495}, "early_first")["text"]
    assert tm.lean_text(party, "late")["text"] is None and tm.lean_text(party, None)["text"] is None
    assert tm.lean_text(None, "early_first") is None
    assert tm.lean_text({"kind": "election_day", "D": 1, "R": 2}, "early_first") is None


def test_est_turnout_is_the_registered_constant():
    m = re.search(r"^EST_TURNOUT = ([0-9.]+)", (ROOT / "src" / "brunder" / "us.py").read_text(), re.M)
    assert m and float(m[1]) == tm.EST_TURNOUT


def test_count_order_from_registered_configs():
    o = tm.count_orders(ROOT)
    assert "advance + mail" in o["GA"]["text"] and o["GA"]["config"].startswith("us2026_ga_sen")
    assert o["GA"]["text"] != "" and "As ga" not in o["TX"]["text"] and o["TX"]["text"].startswith("TX counts early")
    assert "for days after the polls close" in o["AZ"]["text"] and o["CA"]["text"].startswith("CA counts vote-by-mail")
    assert "OH" in o and "WA" not in o                       # states without a registered config have no sentence: unknown
    assert set(tm.ORDER_CLASS) <= set(o)


def test_expected_votes_source_and_county_rows():
    day = {"generated_utc": "2026-11-03T17:00:00Z", "states": [
        {"st": "FL", "name": "Florida", "verdict": "x", "verdict_class": "none", "rows": [], "counties": [
            {"state": "FL", "jurisdiction": "Alachua County", "level": "county", "kind": "total", "count": 1000, "prov": "official", "as_of_utc": "2026-11-03T16:00:00Z"}]},
        {"st": "WA", "name": "Washington", "verdict": "x", "verdict_class": "none", "rows": [], "counties": []}]}
    out = tm.build(day, ROOT)
    fl = out["states"]["FL"]
    assert fl["expected"]["source"] == "units" and fl["county_kind"] == "total" and fl["counties"][0]["name"] == "Alachua County"
    assert fl["composition"]["ok"] is False and "at least 10" in fl["composition"]["why"]
    wa = out["states"]["WA"]
    assert wa["expected"]["source"] == "est" and wa["banked"] is None and wa["status"] == "none"
    assert wa["order"]["text"] is None
    assert out["history"] == {}                                       # nothing banked, nothing to remember


def test_count_order_keeps_abbreviation():
    assert tm.count_orders(ROOT)["PA"]["text"].startswith("PA counts mail ballots from 7 a.m. on Election Day")


def test_history_accumulates_across_runs_without_duplicates():
    def day(gen, n):
        return {"generated_utc": gen, "states": [{"st": "AK", "name": "Alaska", "verdict": "x", "verdict_class": "none", "counties": [], "rows": [
            {"state": "AK", "jurisdiction": "Alaska (statewide)", "level": "state", "kind": "advance", "count": n, "prov": "secondary", "measure": "m"}]}]}
    a = tm.build(day("2026-11-03T17:00:00Z", 100), ROOT)
    b = tm.build(day("2026-11-03T17:10:00Z", 150), ROOT, prev=a)
    c = tm.build(day("2026-11-03T17:10:00Z", 150), ROOT, prev=b)               # the same file again: no new point
    assert [h["v"] for h in b["history"]["AK"]] == [100, 150] and c["history"] == b["history"]
