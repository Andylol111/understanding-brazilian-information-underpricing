"""site/dash-bop.js: the balance-of-power hemicycle's seat order, projected seat counts and dot layout, run in node on synthetic seats.
The chart is model chances only: the order is safest Democratic seat first, seats not up at the two ends, expected seats are the sum of the
chances rounded, and the markup has exactly one dot per seat."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def run_js(script: str):
    if not NODE:
        pytest.skip("node not installed")
    mod = (ROOT / "site" / "dash-bop.js").as_uri()
    # common.js (esc) reads the page's address when imported
    src = f"globalThis.location = {{ search: '', href: 'http://x/' }};\nconst B = await import({json.dumps(mod)});\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", src], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_tier_thresholds():
    out = run_js("console.log(JSON.stringify([0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.45, 0.2, 0.05, null].map((p) => B.tier(p))))")
    assert [(t["party"], t["conf"]) for t in out] == [("D", 3), ("D", 3), ("D", 2), ("D", 2), ("D", 1), ("T", 1), ("R", 1), ("R", 2), ("R", 3), ("T", 1)]


def test_seat_order_ends_and_sort():
    out = run_js("""
const seats = B.seatOrder({ D: 2, R: 3 }, [{ key: 'b', name: 'B', p: 0.4 }, { key: 'a', name: 'A', p: 0.9 }, { key: 'n', name: 'N', p: null }, { key: 'c', name: 'C', p: 0.1 }]);
console.log(JSON.stringify(seats.map((s) => [s.up, s.key, s.p])));""")
    assert out == [[False, None, 1], [False, None, 1], [True, "a", 0.9], [True, "n", 0.5], [True, "b", 0.4], [True, "c", 0.1],
                   [False, None, 0], [False, None, 0], [False, None, 0]]


def test_tally_expected_seats_is_the_rounded_sum_of_chances():
    out = run_js("""
const mk = (ps, notUp) => B.seatTally(B.seatOrder(notUp, ps.map((p, i) => ({ key: 'r' + i, name: 'R' + i, p }))));
console.log(JSON.stringify({ a: mk([0.5, 0.9, 0.2, 0.2], { D: 3, R: 4 }), b: mk([0.7, 0.7, 0.7], { D: 0, R: 0 }), c: mk([0.5, 0.5], { D: 0, R: 0 }) }));""")
    a, b, c = out["a"], out["b"], out["c"]
    assert a["total"] == 11 and a["expectedD"] == pytest.approx(3 + 0.5 + 0.9 + 0.2 + 0.2) and a["d"] == 5 and a["r"] == 6
    assert a["dLeads"] == 4 and a["rLeads"] == 6 and a["tied"] == 1 and a["majority"] == 6   # not-up seats lead for their side; 0.5 leads for no one
    assert b["d"] == 2 and b["r"] == 1 and b["dLeads"] == 3 and b["majority"] == 2          # 2.1 rounds to 2 although all three lead
    assert c["d"] == 1 and c["r"] == 1 and c["tied"] == 2


def test_projection_moves_with_the_chances_and_labels_the_phase():
    out = run_js("""
const races = (ps) => ps.map((p, i) => ({ key: 'r' + i, name: 'R' + i, p }));
const pre = B.seatProjection({ chamber: 'Senate', notUp: { D: 34, R: 31 }, races: races([0.5, 0.5, 0.2, 0.9]), majority: 51, counted: null });
const mid = B.seatProjection({ chamber: 'Senate', notUp: { D: 34, R: 31 }, races: races([0.97, 0.9, 0.9, 0.99]), majority: 51, counted: 0.37 });
const early = B.seatProjection({ chamber: 'House', notUp: { D: 0, R: 0 }, races: races([0.5]), counted: 0.004 });
console.log(JSON.stringify({ pre: [pre.d, pre.r, pre.majority, B.projectionLabel(pre)], mid: [mid.d, mid.r, B.projectionLabel(mid)], early: [early.majority, B.projectionLabel(early)] }));""")
    assert out["pre"] == [36, 33, 51, "Projection before votes"]                       # 34 + 2.1
    assert out["mid"] == [38, 31, "Live projection · 37% counted"]                    # 34 + 3.76
    assert out["early"] == [1, "Live projection · <1% counted"]


@pytest.mark.parametrize("n", [100, 435])
def test_layout_has_one_distinct_dot_per_seat_inside_the_box_in_left_to_right_order(n):
    out = run_js(f"console.log(JSON.stringify(B.hemicycleLayout({n})));")
    pts = out["pts"]
    assert len(pts) == n and out["rows"] >= 3
    angles = [math.atan2(out["cy"] - p["y"], p["x"] - out["cx"]) for p in pts]
    assert all(a >= b - 1e-9 for a, b in zip(angles, angles[1:]))                 # left (π) to right (0)
    assert all(out["dot"] <= p["x"] <= out["w"] - out["dot"] and 0 <= p["y"] <= out["cy"] + 1e-6 for p in pts)  # a semicircle: nothing below the baseline
    nearest = min(math.dist((p["x"], p["y"]), (q["x"], q["y"])) for i, p in enumerate(pts) for q in pts[i + 1:])
    assert nearest >= 2 * out["dot"] * 0.99                                       # dots never overlap


def test_markup_one_dot_per_seat_party_split_and_no_market_figure():
    out = run_js("""
const races = Array.from({ length: 35 }, (_, i) => ({ key: 'r' + i, name: 'Race ' + i, p: i / 34 }));
const proj = B.seatProjection({ chamber: 'Senate', notUp: { D: 34, R: 31 }, races, majority: 51, counted: null });
const { head, svg, key } = B.hemicycleParts(proj);
const dots = [...svg.matchAll(/<circle class="hs ([a-z])(\\d)( nu)?( go)?"/g)].map((m) => [m[1], +m[2], !!m[3], !!m[4]]);
console.log(JSON.stringify({ head, n: dots.length, nu: dots.filter((d) => d[2]).length, go: dots.filter((d) => d[3]).length, first: dots[0], last: dots.at(-1),
  dDots: dots.filter((d) => d[0] === 'd').length, rDots: dots.filter((d) => d[0] === 'r').length, tDots: dots.filter((d) => d[0] === 't').length, d: proj.d, all: head + svg + key }));""")
    assert out["n"] == 100 and out["nu"] == 65 and out["go"] == 35
    assert out["first"] == ["d", 3, True, False] and out["last"] == ["r", 3, True, False]
    assert (out["dDots"], out["rDots"], out["tDots"]) == (51, 48, 1)                # 34 not up + 17 races above 50%; 31 + 17 below; one at exactly 50%
    assert out["d"] == 52                                                           # 34 + sum(i/34) = 51.5
    assert f"Democrats <b>{out['d']}</b>" in out["head"] and "majority 51" in out["head"] and "Projection before votes" in out["head"]
    assert not any(w in out["all"].lower() for w in ("polymarket", "kalshi", "market"))
