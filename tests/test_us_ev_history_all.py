from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

P = Path(__file__).parents[1] / "analysis" / "us_ev_history_all.py"
spec = importlib.util.spec_from_file_location("us_ev_history_all", P)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def test_two_party_share_and_gap():
    assert h.d2(60, 40) == pytest.approx(60.0)
    assert h.d2(827549, 676750) == pytest.approx(55.0, abs=0.05)  # NC 2022 early vote
    assert h.d2(0, 0) is None and h.d2(None, 5) is None
    assert h.gap(55.0, 48.4) == pytest.approx(6.6)      # early vote more Democratic than the result: positive
    assert h.gap(45.0, 50.0) == pytest.approx(-5.0)
    assert h.gap(None, 50.0) is None


def test_winner_right_wrong_and_ties():
    assert h.right_winner(55, 52) is True
    assert h.right_winner(45, 48) is True
    assert h.right_winner(55.0, 48.4) is False     # NC 2022: early vote D, result R
    assert h.right_winner(50, 52) is None and h.right_winner(None, 52) is None


def test_mean_sd_ignore_missing():
    assert h.mean([1, None, 3]) == 2 and h.mean([None]) is None
    assert h.sd([2, 4]) == pytest.approx(math.sqrt(2)) and h.sd([1]) is None


def test_demean_by_key_removes_group_means():
    vals = [10.0, 14.0, 1.0, 3.0, None]
    keys = ["a", "a", "b", "b", "b"]
    assert h.demean(vals, keys) == [-2.0, 2.0, -1.0, 1.0, None]
    out = h.demean([5.0, 7.0, 100.0], ["x", "x", "y"])
    assert out == [-1.0, 1.0, 0.0]                  # a key with one value has nothing left
    # a national swing common to every state of a cycle cannot create agreement once it is removed
    dx = h.demean([8.0, 9.0, 10.0], ["c"] * 3)
    assert sum(dx) == pytest.approx(0)


def test_agree_counts_only_moving_pairs():
    assert h.agree([1, -2, 0, 3, None], [2, -1, 5, -4, 1]) == (2, 3)
    assert h.agree([], []) == (0, 0)


def test_corr():
    assert h.corr([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert h.corr([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert h.corr([1, 2], [1, 2]) is None            # fewer than three pairs
    assert h.corr([1, 1, 1], [1, 2, 3]) is None      # no variance


def test_wilson_interval():
    lo, hi = h.wilson(14, 23)
    assert (lo, hi) == (pytest.approx(0.41, abs=0.01), pytest.approx(0.78, abs=0.01))  # the registered test's own interval
    assert h.wilson(0, 0) is None


def test_prev_cycles_and_components():
    assert h.same_type_prev(2024, {2020, 2022}) == 2020 and h.same_type_prev(2022, {2020}) is None
    assert h.any_prev(2024, {2018, 2022}) == 2022 and h.any_prev(2018, {2018}) is None
    assert h.components(100, 900, 1000) == "mail + in-person"
    assert h.components(1000, 0, 1000) == "mail only"
    assert h.components(None, 1000, 1000) == "in-person only"
    assert h.components(None, None, 1000) == "total only"
    assert h.components(300, 300, 1000) == "total only"     # parts do not make up the total


def _row(st, cy, ev, act, defn="mail only", pct=None, age=None, label=None):
    return {"st": st, "cycle": cy, "report": f"{cy}-11-01", "ev_d2": ev, "act": act, "defn": defn, "early_pct": pct, "age_old": age, "age_label": label}


def test_change_pairs_same_type_demeaned_and_definition_skip():
    rows = [
        _row("aa", 2020, 50.0, 48.0), _row("aa", 2024, 54.0, 49.0),     # ev +4, act +1
        _row("bb", 2020, 60.0, 55.0), _row("bb", 2024, 58.0, 57.0),     # ev -2, act +2
        _row("cc", 2020, 40.0, 40.0), _row("cc", 2024, 41.0, 39.0),     # ev +1, act -1
        _row("dd", 2020, 40.0, 40.0, defn="mail only"), _row("dd", 2024, 45.0, 45.0, defn="mail + in-person"),
        _row("aa", 2022, 52.0, 47.0),                                       # no 2018: no same-type pair for 2022
    ]
    pairs = h.change_pairs(rows, "ev_d2", "same")
    used = {p["st"]: p for p in pairs if "skipped" not in p}
    assert set(used) == {"aa", "bb", "cc"}
    assert used["aa"]["dx"] == pytest.approx(4.0) and used["aa"]["dact"] == pytest.approx(1.0)
    assert [p for p in pairs if p["st"] == "dd"][0]["skipped"].startswith("definition changed")
    assert sum(p["dx_dm"] for p in used.values()) == pytest.approx(0) and sum(p["dact_dm"] for p in used.values()) == pytest.approx(0)
    s = h.pair_summary(pairs)
    assert (s["n"], s["skipped_definition"]) == (3, 1)
    assert (s["agree"], s["agree_n"]) == (1, 3)          # only aa moved with its result


def test_change_pairs_any_uses_previous_available_cycle():
    rows = [_row("aa", 2018, 50.0, 50.0), _row("aa", 2022, 55.0, 52.0), _row("aa", 2024, 53.0, 55.0)]
    pairs = h.change_pairs(rows, "ev_d2", "any")
    assert [(p["c0"], p["c1"]) for p in pairs] == [(2018, 2022), (2022, 2024)]
    assert pairs[1]["dx"] == pytest.approx(-2.0) and pairs[1]["dact"] == pytest.approx(3.0)


def test_age_pairs_need_the_same_age_group():
    rows = [_row("aa", 2020, 50, 50, age=40.0, label="65 and up"), _row("aa", 2022, 50, 51, age=45.0, label="Over 65"),
            _row("bb", 2020, 50, 50, age=40.0, label="65 and up"), _row("bb", 2022, 50, 49, age=38.0, label="65 and up")]
    pairs = [p for p in h.change_pairs(rows, "age_old", "any", need_defn=False)]
    assert [p["st"] for p in pairs] == ["bb"]


def _csv(**kw):
    base = {"state": "nc", "cycle": "2022", "report_date": "2022-11-07", "days_before": "1", "total": "1000", "mail": "100", "inperson": "900", "party_D": "550", "party_R": "450",
            "party_O": "0", "party_basis": "total", "party_ok": "1", "age_old_n": "", "age_all_n": "", "age_old_label": "", "partial": "", "source": "s", "url": "u", "note": ""}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def test_build_rows_gap_share_and_exclusions():
    res = {("nc", 2022): [{"race": "2022_Sen-G_NC", "office": "Senate", "act": 48.4, "special": False}]}
    votes = {("nc", 2022): 2000.0}
    r = h.build_rows([_csv()], res, votes, {})[0]
    assert r["ev_d2"] == pytest.approx(55.0) and r["gap"] == pytest.approx(6.6) and r["right"] is False
    assert r["early_pct"] == pytest.approx(50.0) and r["defn"] == "mail + in-person"
    assert h.build_rows([_csv(partial="only Ada County")], res, votes, {})[0]["ev_d2"] is None        # partial: no share, no party gap
    assert h.build_rows([_csv(partial="only Ada County")], res, votes, {})[0]["early_pct"] is None
    stale = h.build_rows([_csv(days_before=30)], res, votes, {})[0]
    assert stale["early_pct"] is None and "30 days" in stale["volume_why"] and stale["gap"] is not None
    big = h.build_rows([_csv(total=5000)], res, votes, {})[0]
    assert big["early_pct"] is None and "exceed" in big["volume_why"]
    nopolls = h.build_rows([_csv()], {}, votes, {})[0]
    assert nopolls["act"] is None and nopolls["gap"] is None and nopolls["ev_d2"] == pytest.approx(55.0)
    missing = h.build_rows([{"state": "al", "cycle": "2022", "report_date": "", "note": "no page"}], res, votes, {})[0]
    assert missing["report"] is None


def test_county_panel_fills_only_states_the_table_misses():
    pan = {("nc", 2022): [{"race": "x", "office": "Senate", "act": 40.0, "special": False, "from_panel": True}],
           ("nc", 2020): [{"race": "p", "office": "President", "act": 50.0, "special": False, "from_panel": True}, {"race": "s", "office": "Senate", "act": 48.0, "special": False, "from_panel": True}]}
    # no race at all in the table: every panel race is used
    assert h.build_rows([_csv()], {}, {}, pan)[0]["act"] == pytest.approx(40.0)
    # the table has the Senate race: only the president may be added from the panel, never a second Senate
    res = {("nc", 2020): [{"race": "2020_Sen-G_NC", "office": "Senate", "act": 46.0, "special": False}]}
    r = h.build_rows([_csv(cycle=2020, report_date="2020-11-02", days_before=1)], res, {}, pan)[0]
    assert sorted(x["office"] for x in r["races"]) == ["President", "Senate"] and r["act"] == pytest.approx(48.0)


def test_demeaning_removes_a_national_swing_in_the_published_summary():
    # every state's early vote and result both move +5 (a national swing): raw direction agrees everywhere, demeaned it carries no information
    rows = []
    for i, st in enumerate(["aa", "bb", "cc", "dd"]):
        rows += [_row(st, 2020, 50.0 + i, 50.0 + i), _row(st, 2024, 55.0 + i, 55.0 + i)]
    s = h.pair_summary(h.change_pairs(rows, "ev_d2", "same"))
    assert (s["agree"], s["agree_n"]) == (4, 4)
    assert s["agree_dm_n"] == 0
