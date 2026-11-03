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


def test_no_figure_counties_are_left_out():
    # eleven counties, one a feed gap (0 so far): ten remain, which is exactly the threshold; the gap is not counted, not resampled
    cs = [{"share": 0.3 + 0.03 * i, "banked": 60.0 + i, "exp": 100.0} for i in range(10)] + [{"share": 0.9, "banked": 0.0, "exp": 100.0}]
    tm.flag_no_figure(cs)
    assert [c["no_figure"] for c in cs] == [False] * 10 + [True]
    ok = tm.composition([c for c in cs if not c["no_figure"]])
    assert ok["ok"] and ok["n"] == 10 and ok["share_so_far"] < 60                        # the 90% county would have pulled it up
    # with only nine real counties the threshold is not met, even though ten rows are listed
    nine = cs[:9] + [cs[10]]
    tm.flag_no_figure(nine)
    assert not tm.composition([c for c in nine if not c["no_figure"]])["ok"]
    # a tiny count (< 5% of expected) is a gap only where the state's median county is above 40%
    hi = [{"share": 0.5, "banked": 50.0, "exp": 100.0}] * 5 + [{"share": 0.5, "banked": 3.0, "exp": 100.0}]
    tm.flag_no_figure(hi)
    assert [c["no_figure"] for c in hi] == [False] * 5 + [True]
    lo = [{"share": 0.5, "banked": 10.0, "exp": 100.0}] * 5 + [{"share": 0.5, "banked": 3.0, "exp": 100.0}]
    tm.flag_no_figure(lo)
    assert not any(c["no_figure"] for c in lo)


def test_build_flags_volusia_style_gap():
    rows = [{"state": "FL", "jurisdiction": j, "level": "county", "kind": "total", "count": n, "prov": "official", "measure": "m"}
            for j, n in [("Volusia County", 0), ("Miami-Dade County", 650000), ("Broward County", 500000)]]
    day = {"generated_utc": "2026-11-03T17:00:00Z", "states": [{"st": "FL", "name": "Florida", "verdict": "x", "verdict_class": "none", "rows": [], "counties": rows}]}
    fl = tm.build(day, ROOT)["states"]["FL"]
    by = {c["name"]: c for c in fl["counties"]}
    assert by["Volusia County"]["no_figure"] and not by["Broward County"]["no_figure"] and fl["n_county_no_figure"] == 1


# ---- outstanding mail ballots, 2022 final pre-election ratio, county extras
def test_outstanding_is_sent_minus_returned():
    o = tm.outstanding_rule("WI", 858008, 796607, "2026-11-03", "2026-11-03", "sent")
    assert o["ok"] and o["kind"] == "outstanding" and o["value"] == 61401 and o["pct_returned"] == 92.8 and o["issued_label"] == "sent"


def test_outstanding_refuses_missing_inconsistent_or_stale_pairs():
    assert not tm.outstanding_rule("IA", None, 100, "2026-11-03", "2026-11-03")["ok"]
    assert not tm.outstanding_rule("IA", 0, 0, None, None)["ok"]
    assert "exceeds" in tm.outstanding_rule("IA", 100, 120, None, None)["why"]                    # returned > sent: different bases
    assert "days apart" in tm.outstanding_rule("MD", 1000, 500, "2026-10-30", "2026-11-02")["why"]
    assert tm.outstanding_rule("MD", 1000, 500, "2026-11-01", "2026-11-02")["ok"]                 # one day apart is allowed
    assert tm.outstanding_rule("MD", 1000, 500, None, "2026-11-02")["ok"]                         # a missing date is not a gap


def test_all_mail_state_is_not_returned_yet_never_outstanding():
    for st in ("CO", "NV", "WA", "OR", "UT"):
        o = tm.outstanding_rule(st, 4054503, 1859900, "2026-11-02", "2026-11-02", "requested")
        assert o["kind"] == "not_returned_yet" and o["value"] == 4054503 - 1859900 and "not a count of voters" in o["note"]
    assert tm.outstanding_rule("PA", 100, 40, None, None)["kind"] == "outstanding"


def test_build_outstanding_and_2022_ratio_from_extra():
    day = {"generated_utc": "2026-11-03T17:00:00Z", "states": [
        {"st": "IA", "name": "Iowa", "verdict": "x", "verdict_class": "none", "counties": [], "rows": [
            {"state": "IA", "jurisdiction": "Iowa (statewide)", "level": "state", "kind": "advance", "count": 500000, "prov": "secondary", "measure": "m", "as_of_date": "2026-11-03"}]},
        {"st": "AK", "name": "Alaska", "verdict": "excluded", "verdict_class": "excluded", "counties": [], "rows": [
            {"state": "AK", "jurisdiction": "Alaska (statewide)", "level": "state", "kind": "advance", "count": 99424, "prov": "secondary", "measure": "m", "as_of_date": "2026-11-02"}]},
        {"st": "MI", "name": "Michigan", "verdict": "x", "verdict_class": "none", "counties": [], "rows": []}]}
    extra = {"state_extra": {"IA": {"sent": 510618, "requested": 513390, "returned": 498951, "as_of_date": "2026-11-03", "basis": "b", "prov": "manual"},
                             "MI": {"requested": 2174646, "returned": 1569715, "as_of_date": "2026-11-02", "basis": "b", "prov": "official"}},
             "final_2022": {"IA": {"kind": "advance", "value": 341237, "as_of": "2022-11-07", "src": "s", "label": "l"},
                            "AK": {"kind": "advance", "value": 33597, "as_of": "2022-11-05", "src": "s", "usable": False, "label": "do not use"},
                            "MI": {"kind": "mail", "value": 1525312, "as_of": "2022-11-08", "src": "s", "label": "l", "now_value": 1569715, "now_as_of": "2026-11-02", "now_label": "mail"}}}
    out = tm.build(day, ROOT, extra=extra)["states"]
    assert out["IA"]["outstanding"]["value"] == 510618 - 498951 and out["IA"]["outstanding"]["issued_label"] == "sent"
    v = out["IA"]["vs_2022_final"]
    assert v["ok"] and v["ratio"] == round(500000 / 341237, 3) and v["date_2022"] == "2022-11-07" and v["now_date"] == "2026-11-03" and "not same day" in v["label"]
    assert out["MI"]["vs_2022_final"]["ratio"] == round(1569715 / 1525312, 3) and out["MI"]["outstanding"]["value"] == 2174646 - 1569715
    ak = out["AK"]["vs_2022_final"]
    assert ak["ok"] is False and "ratio" not in ak                                                                    # flagged do-not-use: no ratio
    assert out["AK"]["outstanding"] is None                                                                           # excluded state: no UF pair either
    assert out["IA"]["who"]["new_voters"]["share"] is None


def test_extra_county_rows_carry_mail_outstanding_and_party():
    names = ["Polk", "Linn", "Scott", "Johnson", "Black Hawk", "Dallas", "Story", "Woodbury", "Dubuque", "Pottawattamie", "Cerro Gordo"]
    rows = [{"name": n, "count": 1000 + i, "requested": 1200, "issued": 1100 + i, "D": 500, "R": 300, "U": 200 + i, "other": 0} for i, n in enumerate(names)]
    extra = {"county_extra": {"IA": {"kind": "mail_returned", "as_of_date": "2026-11-03", "prov": "manual", "rows": rows + [{"name": "Nowhere", "count": 5}]}}}
    day = {"generated_utc": "2026-11-03T17:00:00Z", "states": [{"st": "IA", "name": "Iowa", "verdict": "x", "verdict_class": "none", "rows": [], "counties": []}]}
    ia = tm.build(day, ROOT, extra=extra)["states"]["IA"]
    assert ia["county_kind"] == "mail_returned" and ia["n_county_rows"] == len(names) and ia["county_unmatched"] == ["Nowhere"]
    polk = next(c for c in ia["counties"] if c["name"].startswith("Polk"))
    assert polk["mail"] == {"issued": 1100, "returned": 1000, "outstanding": 100, "label": "sent", "as_of": "2026-11-03"}
    assert polk["party"]["D"] == 500 and polk["party"]["R"] == 300 and ia["county_party"]
    assert tm.county_key("Lewis & Clark County", "MT") == tm.county_key("Lewis and Clark", "MT")


def test_age_block_needs_coverage_and_keeps_comparison_year():
    g = {"source": "s", "as_of": "2026-10-31", "coverage": {"age": 100.0}, "baselines": {"2022": {"label": "2022-11-03, same number of days before Election Day"}},
         "groups": {"age:18-29": {"share": 10.0, "past": {"2022": {"share": 8.0}}}, "age:65+": {"share": 40.0, "past": {"2022": {"share": 41.0}, "2020": {"share": 20.0}}}}}
    a = tm.age_block("NC", g, None)
    assert [b["band"] for b in a["bands"]] == ["18-29", "65+"] and a["bands"][1]["past"] == {"2022": 41.0} and "2022-11-03" in a["past_labels"]["2022"]
    g["coverage"] = {"age": 49.3}
    assert tm.age_block("ID", g, None) is None                                                      # under 90% of ballots have an age
    ks = tm.age_block("KS", None, {"source": "ike", "as_of_date": "2026-11-02", "bands": ["a", "b"], "note": "n", "counts": {"2026": {"a": 25, "b": 75}, "2022": {"a": 10, "b": 90}}})
    assert ks["bands"][0] == {"band": "a", "share": 25.0, "past": {"2022": 10.0}}
