"""site/dash-events.js: election-night announcement rules, run in node on synthetic rows.
A projection needs >= 99% on 2 consecutive row times with >= 20% counted; a drop under 95% withdraws it; market follow-ups; chamber
control chance; "decided" only from the caller's decided races."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
RACE = "{ id: 'tx-sen', label: 'Texas Senate', a: { id: 'dem', name: 'A Dem' }, b: { id: 'rep', name: 'B Rep' } }"


def run_js(script: str):
    if not NODE:
        pytest.skip("node not installed")
    mod = (ROOT / "site" / "dash-events.js").as_uri()
    src = f"globalThis.location = {{ search: '', href: 'http://x/' }};\nconst E = await import({json.dumps(mod)});\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", src], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def feed(rows: list[dict], extra: str = "") -> list[dict]:
    return run_js(f"""
const ev = E.createEvents();
const T = (m) => Date.UTC(2026, 10, 4, 1, m);
const rows = {json.dumps(rows)}.map((r) => ({{ ...r, t: T(r.m) }}));
ev.feedRace({RACE}, rows);
{extra}
console.log(JSON.stringify(ev.items().map((i) => [i.kind, i.text, i.struck])));""")


def test_call_needs_two_rows_and_twenty_percent():
    out = feed([{"m": 0, "p": 0.995, "c": 0.5}, {"m": 5, "p": 0.995, "c": 0.5}])
    assert [i[0] for i in out] == ["call"] and out[0][1].startswith("Model projects A Dem (D) wins Texas Senate")


def test_one_frame_spike_is_no_call():
    assert feed([{"m": 0, "p": 0.6, "c": 0.5}, {"m": 5, "p": 0.999, "c": 0.5}, {"m": 10, "p": 0.7, "c": 0.5}, {"m": 15, "p": 0.999, "c": 0.5}]) == []


def test_under_twenty_percent_counted_is_no_call():
    assert feed([{"m": i * 5, "p": 0.999, "c": 0.19} for i in range(6)]) == []


def test_call_fires_once_after_counted_reaches_twenty():
    out = feed([{"m": 0, "p": 0.999, "c": 0.1}, {"m": 5, "p": 0.999, "c": 0.2}, {"m": 10, "p": 0.999, "c": 0.3}, {"m": 15, "p": 0.999, "c": 0.4}])
    assert [i[0] for i in out] == ["call"]


def test_side_b_names_second_side():
    out = feed([{"m": 0, "p": 0.004, "c": 0.5}, {"m": 5, "p": 0.003, "c": 0.5}])
    assert out[0][1].startswith("Model projects B Rep (R) wins")


def test_drop_below_95_withdraws_and_strikes():
    out = feed([{"m": 0, "p": 0.995, "c": 0.5}, {"m": 5, "p": 0.995, "c": 0.5}, {"m": 10, "p": 0.97, "c": 0.5}, {"m": 15, "p": 0.9, "c": 0.5}])
    assert [i[0] for i in out] == ["withdrawn", "call"]
    assert out[0][1] == "Projection withdrawn: Texas Senate back to 90%" and out[1][2] is True
    # 97% is between the two lines: the call stands


def test_refire_after_withdrawal():
    rows = [{"m": 0, "p": 0.995, "c": 0.5}, {"m": 5, "p": 0.995, "c": 0.5}, {"m": 10, "p": 0.9, "c": 0.5}, {"m": 15, "p": 0.995, "c": 0.5}, {"m": 20, "p": 0.995, "c": 0.5}]
    assert [i[0] for i in feed(rows)] == ["call", "withdrawn", "call"]


def test_market_after_and_before_model_and_reload_idempotent():
    rows = [{"m": 0, "p": 0.995, "c": 0.5, "pm": 0.5}, {"m": 5, "p": 0.995, "c": 0.5, "pm": 0.6}, {"m": 17, "p": 0.995, "c": 0.5, "pm": 0.91, "k": 0.95}]
    out = feed(rows)
    texts = [i[1] for i in out]
    assert "Polymarket reaches 90% on Texas Senate: 12 min after the model" in texts
    before = feed([{"m": 0, "p": 0.6, "c": 0.5, "pm": 0.92}, {"m": 5, "p": 0.995, "c": 0.5, "pm": 0.92}, {"m": 10, "p": 0.995, "c": 0.5}])
    assert any(i[1] == "Polymarket reaches 90% on Texas Senate: 10 min before the model" for i in before)
    again = run_js(f"""
const T = (m) => Date.UTC(2026, 10, 4, 1, m);
const rows = {json.dumps(rows)}.map((r) => ({{ ...r, t: T(r.m) }}));
const a = E.createEvents(); a.feedRace({RACE}, rows);
const b = E.createEvents(JSON.parse(JSON.stringify(a.toJSON()))); b.feedRace({RACE}, rows);
console.log(JSON.stringify([a.items().length, b.items().length]));""")
    assert again[0] == again[1] == 3


def test_control_chance_and_chamber_call():
    out = run_js("""
const ps = [...Array(34).fill(1), ...Array(31).fill(0), ...Array(35).fill(0.8)];
const ev = E.createEvents();
const T = (m) => Date.UTC(2026, 10, 4, 1, m);
ev.feedChamber('senate', [{ t: T(0), p: 0.995, c: 0.3 }, { t: T(5), p: 0.995, c: 0.3 }, { t: T(10), p: 0.9, c: 0.3 }]);
console.log(JSON.stringify([E.controlChance([1, 1, 0], 2), E.controlChance([0.5, 0.5], 1), E.controlChance(ps, 51), ev.items().map((i) => i.text)]));""")
    assert out[0] == 1 and out[1] == 0.75 and out[2] > 0.999
    assert out[3] == ["Projection withdrawn: Democratic control of the Senate back to 90%", "Model projects Democrats control the Senate"]


def test_decided_only_from_decided_races():
    out = run_js("""
const ev = E.createEvents(); const t = 1;
ev.feedDecided('senate', { notUp: { D: 34, R: 31 }, decided: { D: 16, R: 0 }, need: E.SENATE_NEED, t });
ev.feedDecided('senate', { notUp: { D: 34, R: 31 }, decided: { D: 0, R: 19 }, need: E.SENATE_NEED, t });
console.log(JSON.stringify(ev.items().map((i) => i.text)));""")
    assert out == ["Senate control decided: Republicans reach 50 seats with the Vice President"]
