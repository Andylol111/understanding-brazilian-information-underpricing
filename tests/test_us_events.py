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



def test_close_race_needs_three_rows_at_999_and_95_counted():
    # within 3 points (counted 50.8 % D, projected 51.0 %): 99.5 % on many rows and 99.9 % at 90 % counted never fire
    close = {"raw": 0.508, "sh": 0.51}
    assert feed([{"m": i * 5, "p": 0.995, "c": 0.97, **close} for i in range(5)]) == []
    assert feed([{"m": i * 5, "p": 0.9995, "c": 0.9, **close} for i in range(5)]) == []
    # fires once the qualifying readings span 30 minutes with a further count update
    out = feed([{"m": i * 15, "p": 0.9995, "c": 0.96 + 0.005 * i, **close} for i in range(3)])
    assert [i[0] for i in out] == ["call"] and "(close race)" in out[0][1]
    assert feed([{"m": i * 15, "p": 0.9995, "c": 0.96 + 0.005 * i, **close} for i in range(2)]) == []


def test_narrow_race_tier_and_the_smaller_margin_decides():
    # projected 54 % (8 pts) but counted 51.5 % (3 pts): the narrow tier applies (99.5 %, 3 rows, 80 % counted)
    row = {"raw": 0.515, "sh": 0.54}
    assert feed([{"m": i * 5, "p": 0.993, "c": 0.9, **row} for i in range(4)]) == []
    out = feed([{"m": i * 15, "p": 0.996, "c": 0.85 + 0.01 * i, **row} for i in range(3)])
    assert [i[0] for i in out] == ["call"] and "(narrow race)" in out[0][1]
    # a wide race keeps the standard rule
    assert [i[0] for i in feed([{"m": i * 5, "p": 0.992, "c": 0.3, "raw": 0.6, "sh": 0.6} for i in range(2)])] == ["call"]


def test_feed_fault_with_nothing_counted_never_withdraws():
    # projected at 50 % counted; the feed then reads 0 % counted and the model falls back to its pre-count chance (Iowa,
    # Nov 4): the projection stands; a real drop with votes counted still withdraws
    rows = [{"m": 0, "p": 0.995, "c": 0.5}, {"m": 5, "p": 0.995, "c": 0.5}, {"m": 10, "p": 0.15, "c": 0}, {"m": 15, "p": 0.15, "c": 0}]
    assert [i[0] for i in feed(rows)] == ["call"]
    assert [i[0] for i in feed(rows + [{"m": 20, "p": 0.9, "c": 0.6}])] == ["withdrawn", "call"]


def test_close_race_needs_thirty_minutes_and_a_further_count_update():
    # AZ-02, Nov 4: three qualifying readings minutes apart from one batch fired a projection; now they must span 30 min
    # and include a new count after the first
    close = {"raw": 0.498, "sh": 0.497}
    one_batch = [{"m": i, "p": 0.0005, "c": 0.976, **close} for i in range(40)]           # 40 min, but no new count
    assert feed(one_batch) == []
    quick = [{"m": 0, "p": 0.0005, "c": 0.976, **close}, {"m": 2, "p": 0.0005, "c": 0.977, **close}, {"m": 4, "p": 0.0005, "c": 0.978, **close}]
    assert feed(quick) == []                                                                # new counts, but only 4 minutes
    held = [{"m": 0, "p": 0.0005, "c": 0.976, **close}, {"m": 15, "p": 0.0005, "c": 0.976, **close}, {"m": 31, "p": 0.0005, "c": 0.981, **close}]
    out = feed(held)
    assert [i[0] for i in out] == ["call"] and "(close race)" in out[0][1]

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
    before = feed([{"m": 0, "p": 0.6, "c": 0.5, "pm": 0.5}, {"m": 2, "p": 0.6, "c": 0.5, "pm": 0.92}, {"m": 5, "p": 0.995, "c": 0.5, "pm": 0.92}, {"m": 10, "p": 0.995, "c": 0.5}])
    assert any(i[1] == "Polymarket reaches 90% on Texas Senate: 8 min before the model" for i in before)
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


def test_decided_chamber_from_decided_races():
    out = run_js("""
const ev = E.createEvents();
const mk = (n, party, t0) => Array.from({ length: n }, (_, i) => ({ party, t: t0 + i }));
ev.feedDecided('senate', { notUp: { D: 34, R: 31 }, decided: mk(16, 'D', 1), need: E.SENATE_NEED });
ev.feedDecided('senate', { notUp: { D: 34, R: 31 }, decided: mk(19, 'R', 1), need: E.SENATE_NEED });
ev.feedDecided('senate', { notUp: { D: 34, R: 31 }, decided: [...mk(17, 'D', 1)], need: E.SENATE_NEED });
console.log(JSON.stringify(ev.items().map((i) => [i.text, i.ts])));""")
    assert out == [["Senate control decided: Republicans reach 50 with the Republican Vice President", 19], ["Senate control decided: Democrats reach 51", 17]]


# row: 1,000,000 expected; counted = vp - rem
def dec(**kw) -> str | None:
    row = {"c": 0.85, "raw": 0.6, "vp": 1_000_000, "rem": 150_000, "exp": 1_000_000} | kw
    return run_js(f"console.log(JSON.stringify(E.countDecided({json.dumps(row)})))")


def test_count_decided_lead_vs_remaining_upper():
    # counted 850k, upper = 1.5M - 850k = 650k; lead = 0.2 * 850k = 170k -> not decided
    assert dec() is None
    # raw 0.9: lead 0.8 * 850k = 680k > 650k -> decided for side a; raw 0.1 -> side b
    assert dec(raw=0.9) == "a" and dec(raw=0.1) == "b"
    # just below / above the line: lead = |2raw-1| * 850k vs 650k  (raw 0.88235 -> 0.7647 * 850k = 650k)
    assert dec(raw=0.88) is None and dec(raw=0.89) == "a"


def test_count_decided_counted_threshold_and_missing_fields():
    # same lead and upper (counted 790k / 810k, expected 1M): only the counted share differs
    assert dec(c=0.79, raw=0.95, vp=1_000_000, rem=210_000) is None
    assert dec(c=0.81, raw=0.95, vp=1_000_000, rem=190_000) == "a"
    for f in ("c", "raw", "vp", "rem", "exp"):
        assert dec(**({"raw": 0.95} | {f: None})) is None
    assert dec(raw=0.5) is None


def test_race_decided_item_and_registry():
    out = feed([{"m": 0, "p": 0.5, "c": 0.5, "raw": 0.6, "vp": 1_000_000, "rem": 500_000, "exp": 1_000_000},
                {"m": 5, "p": 0.5, "c": 0.85, "raw": 0.92, "vp": 1_000_000, "rem": 150_000, "exp": 1_000_000}],
               "console.error(JSON.stringify(ev.decidedOf('tx-sen')));")
    assert out == [["decided", "Decided by the count: A Dem (D) wins Texas Senate — lead larger than any plausible remaining vote", False]]


def test_market_already_above_90_before_count_is_no_item():
    rows = [{"m": 0, "p": 0.5, "c": 0, "pm": 0.95, "k": 0.96}, {"m": 5, "p": 0.6, "c": 0.1, "pm": 0.95, "k": 0.96},
            {"m": 10, "p": 0.995, "c": 0.5, "pm": 0.95, "k": 0.96}, {"m": 15, "p": 0.995, "c": 0.5, "pm": 0.95, "k": 0.96}]
    assert [i[0] for i in feed(rows)] == ["call"]
    # a market that crosses 90% only after the first counted votes still gets its minutes item
    rows2 = [{"m": 0, "p": 0.5, "c": 0.1, "pm": 0.5}, {"m": 5, "p": 0.995, "c": 0.5, "pm": 0.6}, {"m": 10, "p": 0.995, "c": 0.5, "pm": 0.6}, {"m": 15, "p": 0.995, "c": 0.5, "pm": 0.93}]
    assert any("5 min after the model" in i[1] for i in feed(rows2))


def test_partial_race_never_projects_or_decides():
    rows = [{"m": i * 5, "p": 0.999, "c": 1.0, "raw": 0.95, "vp": 1_000_000, "rem": 0, "exp": 1_000_000} for i in range(6)]
    out = run_js(f"""
const T = (m) => Date.UTC(2026, 10, 4, 1, m);
const rows = {json.dumps(rows)}.map((r) => ({{ ...r, t: T(r.m) }}));
const mk = (partial) => {{ const ev = E.createEvents(); ev.feedRace({{ ...{RACE}, partial }}, rows); return [ev.items().map((i) => i.kind), ev.decidedOf('tx-sen')]; }};
console.log(JSON.stringify([mk(true), mk(false)]));""")
    assert out[0] == [[], None]
    assert sorted(out[1][0]) == ["call", "decided"] and out[1][1]["party"] == "D"
