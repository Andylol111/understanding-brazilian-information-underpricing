"""analysis/us_turnout_proj.py: estimator math, share clipping, the blend, backtest split integrity (no future data), the live projection on a toy state."""
import copy
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import us_turnout_proj as tp  # noqa: E402


def H(early, final, defn="mail + in-person"):
    return {"early": early, "final": final, "share": early / final, "defn": defn, "report": None}


def toy_hist(n=10):
    """n states, cycles 2018..2024; early share 0.5 in 2018, 0.6 in 2020, 0.5 in 2022, 0.6 in 2024; finals grow with the cycle."""
    h = {}
    for i in range(n):
        st = f"S{i}"
        base = 1000.0 * (i + 1)
        for c, sh, fin in ((2018, 0.5, base), (2020, 0.6, base * 1.4), (2022, 0.5, base * 1.0), (2024, 0.6, base * 1.4)):
            h[(st, c)] = H(sh * fin * (1 + 0.01 * i), fin)
    return h


def toy_pres(n=10):
    return {f"S{i}": {2016: 1000.0 * (i + 1) * 1.3, 2020: 1000.0 * (i + 1) * 1.4, 2024: 1000.0 * (i + 1) * 1.45} for i in range(n)}


def test_est_A_math_and_clipping():
    assert tp.est_A(500, 0.5) == pytest.approx(1000)
    assert tp.est_A(100, 0.05) == pytest.approx(100 / 0.15)      # share below the floor is clipped up to 0.15
    assert tp.est_A(990, 1.2) == pytest.approx(990 / 0.98)       # and above the ceiling down to 0.98
    assert tp.clip_share(0.5) == 0.5 and tp.clip_share(0.0) == 0.15 and tp.clip_share(2) == 0.98


def test_blend_weight_and_log_space():
    assert tp.blend_weight(0.01, 0.01) == pytest.approx(0.5)
    assert tp.blend_weight(0.01, 0.04) == pytest.approx(0.8)     # four times the variance of the other: a fifth of the weight
    assert tp.blend(100.0, 400.0, 0.5) == pytest.approx(200.0)   # geometric mean
    assert tp.blend(100.0, 400.0, 1.0) == pytest.approx(100.0) and tp.blend(100.0, 400.0, 0.0) == pytest.approx(400.0)


def test_prev_cycle_and_baseline_use_only_earlier_cycles():
    assert tp.prev_cycle(2022, "same") == 2018 and tp.prev_cycle(2024, "same") == 2020 and tp.prev_cycle(2026, "same") == 2022
    assert tp.prev_cycle(2022, "any") == 2020 and tp.prev_cycle(2024, "any") == 2022
    pres = {"A": {2020: 1000.0, 2016: 800.0}}
    assert tp.baseline("A", 2022, {}, pres) == pytest.approx(0.72 * 1000)
    assert tp.baseline("A", 2024, {("A", 2020): 900.0}, pres) == 900.0       # previous same-type total
    assert tp.baseline("A", 2024, {}, pres) == 1000.0                        # else the panel's total
    assert tp.baseline("B", 2022, {}, pres) is None


def test_records_split_integrity_no_future_data():
    hist, pres = toy_hist(), toy_pres()
    recs = tp.make_records(hist, pres, "same")
    assert {r["cycle"] for r in recs} == {2022, 2024}                         # 2020 needs 2016 early data: none
    assert all(r["share_cycle"] == r["cycle"] - 4 for r in recs)
    # change everything the projection of 2022 must not see: 2022 final, 2024 and later, 2020 data
    hist2 = copy.deepcopy(hist)
    for (st, c), v in hist2.items():
        if c >= 2022:
            v["final"] *= 3
        if c >= 2024 or c == 2020:
            v["early"] *= 5
    pres2 = copy.deepcopy(pres)
    for st in pres2:
        pres2[st][2024] *= 7
    a = {r["st"]: r for r in recs if r["cycle"] == 2022}
    b = {r["st"]: r for r in tp.make_records(hist2, pres2, "same") if r["cycle"] == 2022}
    for st in a:
        assert b[st]["A"] == pytest.approx(a[st]["A"]) and b[st]["M"] == pytest.approx(a[st]["M"])     # projection inputs unchanged
        assert b[st]["actual"] == pytest.approx(3 * a[st]["actual"])                                     # only the target moved


def test_baseline_of_a_cycle_does_not_read_that_cycles_final():
    hist, pres = toy_hist(), toy_pres()
    r24 = {r["st"]: r for r in tp.make_records(hist, pres, "same") if r["cycle"] == 2024}
    for i in range(10):
        assert r24[f"S{i}"]["M"] == pytest.approx(1000.0 * (i + 1) * 1.4)      # the 2020 final total


def test_evaluate_fits_only_on_earlier_cycles():
    recs = tp.make_records(toy_hist(), toy_pres(), "same")
    rows = {r["cycle"]: r for r in tp.evaluate(recs)}
    assert rows[2020]["n"] == 0
    assert "mae_B" not in rows[2022] and rows[2022]["fit_n"] == 0              # nothing earlier to fit the blend on
    assert rows[2024]["fit_n"] == rows[2022]["n"] and "mae_B" in rows[2024] and "cov_B" in rows[2024]
    # editing 2024 records leaves the 2022 row and the 2024 weight untouched
    recs2 = copy.deepcopy(recs)
    for r in recs2:
        if r["cycle"] == 2024:
            r["eA"] += 1.0
            r["eM"] -= 1.0
    rows2 = {r["cycle"]: r for r in tp.evaluate(recs2)}
    assert rows2[2022] == rows[2022] and rows2[2024]["w"] == rows[2024]["w"]


def test_fit_needs_min_n():
    assert tp.fit([{"eA": 0.1, "eM": 0.1}] * (tp.MIN_N - 1)) is None
    f = tp.fit([{"eA": 0.1, "eM": 0.2}] * tp.MIN_N)
    assert f["vA"] == pytest.approx(0.01) and f["vM"] == pytest.approx(0.04) and f["w"] == pytest.approx(0.8)
    assert f["sdB"] == pytest.approx(abs(0.8 * 0.1 + 0.2 * 0.2))


def test_select_variant_lowest_out_of_sample_mae():
    rows = [{"cycle": 2022, "n": 30, "mae_A": 30, "mae_M": 9}, {"cycle": 2024, "n": 40, "mae_A": 25, "mae_M": 5, "mae_B": 6}]
    s = tp.select_variant(rows)
    assert s["variant"] == "B" and s["n"] == 40 and s["cycles"] == [2024] and s["beats_baseline"] is False and "does not beat" in s["verdict"]
    assert tp.select_variant([{"cycle": 2022, "n": 30, "mae_A": 30, "mae_M": 9}])["variant"] == "A"
    rows[1]["mae_B"] = 30
    assert tp.select_variant(rows)["variant"] == "A"


def test_pick_share_prefers_same_type_then_any_type_and_never_future():
    hist = {("X", 2018): H(500, 1000), ("X", 2024): H(800, 1000), ("Y", 2024): H(800, 1000), ("Z", 2022): H(300, 1000), ("Z", 2024): H(900, 1000), ("W", 2027): H(1, 2)}
    assert tp.pick_share("X", hist)["cycle"] == 2018 and tp.pick_share("X", hist)["kind"] == "same"
    assert tp.pick_share("Z", hist)["cycle"] == 2022
    y = tp.pick_share("Y", hist)
    assert y["cycle"] == 2024 and y["kind"] == "any"
    assert tp.pick_share("W", hist) is None
    c = tp.pick_share("Z", {("Z", 2022): H(1, 1000)})
    assert c["clipped"] and c["used"] == 0.15


def state_rec(adv=500.0, banked=None, exp=1100.0, partial=False, outstanding=None):
    return {"st": "S1", "name": "S one", "by_method": {"advance": {"count": adv, "partial": partial, "as_of": "2026-11-02"}} if adv else {},
            "banked": {"value": banked if banked is not None else adv, "parts": []}, "expected": {"votes": exp}, "outstanding": outstanding}


def toy_bt():
    hist, pres = toy_hist(), toy_pres()
    return hist, tp.run_backtest(hist, pres)


def test_project_state_toy():
    hist, bt = toy_bt()
    hist[("S1", 2022)] = H(500, 1000)
    out = tp.project_state(state_rec(adv=600, exp=1100), hist, bt, 1.0, [], "2026-11-03T12:00:00Z")
    assert out["ok"] and out["A"] == 1200 and out["s_hat"]["cycle"] == 2022 and out["s_hat"]["kind"] == "same"
    assert out["low"] <= out["proj_total"] <= out["high"] and out["level"] == 80
    assert out["variant"] in ("A", "B") and min(1100, 1200) <= out["proj_total"] <= max(1100, 1200)
    assert out["vs_expected"] == pytest.approx(out["proj_total"] / 1100, abs=1e-3) and "heading" in out["heading"]
    assert out["history"] == [{"t": "2026-11-03T12:00:00Z", "v": out["proj_total"], "b": 600, "e": 1100}]
    assert out["ed"]["available"] is False and out["r"]["value"] == 0.5


def test_project_state_floor_delta_and_blank_cases():
    hist, bt = toy_bt()
    hist[("S1", 2022)] = H(500, 1000)
    # the ballots already banked (incl. Election Day check-ins) are a floor
    out = tp.project_state(state_rec(adv=100, banked=5000, exp=1100), hist, bt, 1.0, [], None)
    assert out["proj_total"] == 5000 and out["floor_binding"]
    # outstanding mail floor: banked + 0.5 x outstanding
    o = {"ok": True, "kind": "outstanding", "value": 4000}
    out = tp.project_state(state_rec(adv=100, exp=300, outstanding=o), hist, bt, 1.0, [], None)
    assert out["proj_total"] >= 100 + 0.5 * 4000
    # delta against the previous refresh
    prev = [{"t": "2026-11-03T11:50:00Z", "v": 1000, "b": 500, "e": 1100}]
    out = tp.project_state(state_rec(adv=600, exp=1100), hist, bt, 1.0, prev, "2026-11-03T12:00:00Z")
    assert out["delta"]["d_banked"] == 100 and out["delta"]["d_total"] == out["proj_total"] - 1000 and len(out["history"]) == 2
    # no history, no count, partial count
    nh = tp.project_state({**state_rec(), "st": "NOPE"}, hist, bt, 1.0, [], None)
    assert not nh["ok"] and nh["why"] == "no history for this state"
    assert not tp.project_state(state_rec(adv=None), hist, bt, 1.0, [], None)["ok"]
    assert "part of the state" in tp.project_state(state_rec(partial=True), hist, bt, 1.0, [], None)["why"]


def test_real_backtest_shape_and_label():
    pytest.importorskip("csv")
    hist, pres = tp.load_hist(ROOT), None
    pres, k = tp.load_pres(ROOT)
    bt = tp.run_backtest(hist, pres)
    assert bt["selection"]["variant"] in ("A", "B")
    r24 = next(r for r in bt["same"]["rows"] if r["cycle"] == 2024)
    assert r24["n"] > 20 and r24["mae_A"] > 0 and r24["mae_M"] > 0 and 0 <= r24["cov_B"] <= 100
    assert all(0.9 < v < 1.2 for v in k.values())
    assert "Unregistered" in tp.LABEL and "do not use it" in tp.LABEL
    assert math.isfinite(bt["same"]["fit"]["w"])
