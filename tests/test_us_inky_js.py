"""site/inky.js: the early-signal panel and the adjusted seat line, run in node on synthetic files. The adjusted tally shifts only the
seats not yet counted (by the file's probit shift), never a counted or fixed seat; the panel says "waiting for 6 pm ET" before data and
labels every figure "early signal, not validated"."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def run_js(script: str):
    if not NODE:
        pytest.skip("node not installed")
    inky, bop = ((ROOT / "site" / f).as_uri() for f in ("inky.js", "dash-bop.js"))
    src = f"globalThis.location = {{ search: '', href: 'http://x/' }};\nconst I = await import({json.dumps(inky)});\nconst B = await import({json.dumps(bop)});\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", src], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_adjusted_tally_moves_only_uncounted_races_by_dz():
    out = run_js("""
const proj = B.seatProjection({ chamber: 'House', notUp: { D: 0, R: 0 }, races: [
  { key: 'house:IN-05', name: 'IN-05', p: 0.5 }, { key: 'house:KY-03', name: 'KY-03', p: 0.5, live: true }, { key: 'house:TX-01', name: 'TX-01', p: 0.0 }, { key: 'house:OH-09', name: 'OH-09', p: 0.5 }] });
const inky = { adjusted: { applied: true, house: { 'IN-05': { dz: -1 }, 'KY-03': { dz: -1 }, 'TX-01': { dz: 0 }, 'OH-09': { dz: 1 } } } };
const t = I.adjustedTally(proj, inky, 'house');
console.log(JSON.stringify({ before: proj.expectedD, after: t.expectedD, none: I.adjustedTally(proj, { adjusted: { applied: false } }, 'house'), html: I.adjustedHTML(proj, inky, 'house') }));""")
    assert out["before"] == pytest.approx(1.5)
    # 0.5 -> Phi(-1) = 0.1587 (IN-05); the live seat stays 0.5; 0.0 stays; 0.5 -> Phi(1) = 0.8413 (OH-09)
    assert out["after"] == pytest.approx(0.1587 + 0.5 + 0 + 0.8413, abs=1e-3)
    assert out["none"] is None
    assert "Early-signal adjusted (unregistered, not validated)" in out["html"] and "D <b>" in out["html"]


def test_senate_keys_are_race_keys_and_fixed_seats_do_not_move():
    out = run_js("""
const proj = B.seatProjection({ chamber: 'Senate', notUp: { D: 34, R: 31 }, races: [{ key: 'tx-sen', name: 'TX', p: 0.3 }, { key: 'ks-sen', name: 'KS', p: 0.6 }], majority: 51 });
const t = I.adjustedTally(proj, { adjusted: { applied: true, senate: { 'tx-sen': { dz: 0.5 }, 'ks-sen': { dz: 0.5 } } } }, 'senate');
console.log(JSON.stringify({ before: proj.expectedD, after: t.expectedD, total: t.total }));""")
    assert out["total"] == 67 and out["after"] > out["before"] + 0.3 and out["after"] < out["before"] + 0.5


def test_panel_waits_before_data_and_labels_everything():
    out = run_js("""
const waiting = I.panelHTML(null);
const d = { status: 'counting', polls: { text: 'polls imply swings of +3.2 (MI) to +9.1 (KS) vs 2024 in the 7 Senate states' }, snapshot: { t: '2026-11-04T00:10:00Z', age_s: 30 }, focus_missing: ['KY-06'],
  aggregates: { in: { n: 2, reporting_share: 0.3, swing_pres: 2.5, swing_expected: -3, poll_implied: 5.5, turnout_ratio: 0.9, se_expected: null }, ky: { n: 0 }, in_ky: { n: 2, reporting_share: 0.3, swing_pres: 2.5, swing_expected: -3, poll_implied: 5.5, turnout_ratio: 0.9 },
    national: { n: 2, ready: false, note: 'too early for a national read: 2 of 20 districts have 25% reporting' } },
  districts: { 'IN-05': { id: 'IN-05', st: 'IN', pct: 40, counted: 100000, d2: 0.44, pres24_d2: 0.4144, house24_d2: 0.4018, swing_pres: 2.6, swing_house24: 3.8, swing_expected: -2.9, turnout_ratio: 0.95, status: 'counting', expected_basis: 'county composition (official county file)' },
    'KY-04': { id: 'KY-04', st: 'KY', pct: 50, counted: 90000, d2: null, uncontested: true, status: 'uncontested (no D-R pair)' }, 'OH-01': { id: 'OH-01', st: 'OH', pct: 30, counted: 5000, d2: 0.5, swing_pres: 1, swing_expected: -4 } },
  adjusted: { applied: true, shift_pts: -0.27, aggregate_swing_expected: -3, weight: 0.091, n: 2, basis: 'in_ky', seats: { senate: { pre: { d: 51 }, adj: { d: 50 } }, house: { pre: { d: 230 }, adj: { d: 228 } } } } };
console.log(JSON.stringify({ waiting, counting: I.panelHTML(d), fmt: [I.fmt(-3), I.fmt(2.55), I.fmt(null)] }));""")
    assert "Waiting for 6 pm ET" in out["waiting"] and "early signal, not validated; partial counts can mislead" in out["waiting"]
    c = out["counting"]
    assert "early signal, not validated; partial counts can mislead" in c and "composition-biased" in c
    assert "too early for a national read" in c and "uncontested" in c and "Other districts with at least 20% reporting (1)" in c
    assert "polls imply swings of +3.2 (MI) to +9.1 (KS)" in c and "Not in the feed yet: KY-06" in c and "Early-signal adjusted" in c
    assert out["fmt"][0] == "\u22123.0" and out["fmt"][2] == "–" and out["fmt"][1] in ("+2.5", "+2.6")
