"""site/guide-rules.js: the rules behind guide.html's event feed, race board and lead times, run in node on synthetic frames.
Each rule fires once, held-back events fire later, feeding the same rows again adds nothing, and a group that is not fully counted
(under 3 complete counties, or under 15% of its expected vote) never makes an event."""
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
    rules = (ROOT / "site" / "guide-rules.js").as_uri()
    src = f"import * as R from {json.dumps(rules)};\nconst names = new Map([['ga', 'Georgia'], ['al', 'Alabama']]);\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", src], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


RACE = {"race_id": "ga-sen", "region": "ga", "a": {"id": "dem", "name": "Ossoff", "color": "#1f5fbf"}, "b": {"id": "rep", "name": "Collins", "color": "#c8102e"}}
GOV = {**RACE, "race_id": "ga-gov"}


def row(minute: int, c, p, pm=None, k=None, raw=None):
    return {"t": f"2026-11-04T{minute // 60:02d}:{minute % 60:02d}:00Z", "counted": c, "P_v3": p, "P": p, "pm": pm, "kalshi": k, "raw": raw}


# One night for one race (minutes after 00:00 UTC); the comments say what each row is for.
NIGHT = [
    row(0, 0, 0.5, 0.5, 0.5), row(1, 0.02, 0.55, 0.5, 0.5),
    row(2, 0.10, 0.92, 0.55, 0.5, 0.53),          # model 90%; the gap opens; 10% counted; the count's leader (D) is noted
    row(3, 0.12, 0.96, 0.60, 0.5, 0.53),          # model 95%
    row(4, 0.15, 0.99, 0.95, 0.93, 0.53),         # model 99%; Polymarket and Kalshi 90%; the gap is closed, but 2 minutes after it opened: held back
    row(10, 0.30, 0.99, 0.96, 0.95, 0.49),        # the count's leader changes to R; the gap close is still held back (8 minutes)
    row(11, 0.31, 0.99, 0.96, 0.95, 0.51),        # ... and back to D at once: held back (15 minutes)
    row(12, 0.50, 0.99, 0.97, 0.95, 0.51),        # 50% counted; the gap close fires (10 minutes after the opening)
    row(14, 0.55, 0.99, 0.985, 0.97, 0.51),
    row(20, 0.60, 0.40, 0.40, 0.40, 0.51),        # the model favours R
    row(21, 0.61, 0.60, 0.60, 0.60, 0.51),        # ... and D again at once: held back (10 minutes)
    row(30, 0.80, 0.60, 0.60, 0.60, 0.51),        # both changes of leader now fire
    row(40, 0.90, 0.60, 0.60, 0.60, 0.51),        # 90% counted
]


def test_each_rule_fires_once_and_dedup():
    r = run_js(f"""
const mk = (race, rows) => ({{ race, rows: R.normRows(rows, "v3") }});
const sen = {json.dumps(NIGHT)};
const gov = sen.map((x) => ({{ ...x, counted: x.counted == null ? null : x.counted * 0.98 }}));
const spec = [mk({json.dumps(RACE)}, sen), mk({json.dumps(GOV)}, gov)];
const E = R.createEngine();
const n1 = R.feedRows(E, spec, names);
const keys = E.events.map((e) => e.key).sort();
const n2 = R.feedRows(E, spec, names);                 // the same rows again: nothing is new
const kinds = {{}};
for (const e of E.events) (kinds[e.kind] ??= []).push([new Date(e.t).toISOString().slice(11, 16), e.race, e.text]);
const E2 = R.createEngine();                           // the night in two parts gives the same events
R.feedRows(E2, spec.map((s) => ({{ ...s, rows: s.rows.slice(0, 7) }})), names);
R.feedRows(E2, spec, names);
console.log(JSON.stringify({{ n2, keys, keys2: E2.events.map((e) => e.key).sort(), n: E.events.length, kinds }}));
""")
    assert r["n2"] == 0 and r["n"] == len(r["keys"]) == len(set(r["keys"]))
    assert r["keys"] == r["keys2"]
    k = {kind: sorted((x for x in v if x[1] == "ga-sen"), key=lambda x: x[0]) for kind, v in r["kinds"].items()}
    # the model reaches 90, 95 and 99%: one line each
    assert [x[2] for x in k["model"]] == [
        "Georgia Senate: the model reaches 90% for D (10% counted).",
        "Georgia Senate: the model reaches 95% for D (12% counted).",
        "Georgia Senate: the model reaches 99% for D (15% counted).",
    ]
    # each market reaches 90% once
    assert sorted(x[2] for x in k["market"]) == ["Georgia Senate: Kalshi reaches 90% for D (15% counted).", "Georgia Senate: Polymarket reaches 90% for D (15% counted)."]
    # the gap opens at minute 2; its close, held back by the rate limit, fires at minute 12
    assert [(x[0], "points apart" in x[2]) for x in k["gap"]] == [("00:02", True), ("00:12", False)]
    assert "37 points apart" in k["gap"][0][2] and "are back within" in k["gap"][1][2]
    # the model's favourite: R at minute 20; the change back at minute 21 waits for minute 30
    assert [(x[0], x[2].split("now favours ")[1][0]) for x in k["leader"]] == [("00:20", "R"), ("00:30", "D")]
    # the count's leader: R at minute 10; the change back at 11 waits out its 15 minutes and fires at 30
    assert [(x[0], x[2].split(": ")[1][0]) for x in k["count"]] == [("00:10", "R"), ("00:30", "D")]
    # Georgia passes 10, 50 and 90% once, not once per race
    assert [x[2] for x in r["kinds"]["counted"]] == [
        "Georgia Senate: 10% of the expected vote counted.", "Georgia Senate: 50% of the expected vote counted.", "Georgia Senate: 90% of the expected vote counted."]
    assert {e for e in r["kinds"]} == {"model", "market", "gap", "leader", "count", "counted"}


def test_before_the_count_is_the_starting_point():
    # at 90% and 95% for D before the first votes, both markets at 90%: nothing to report until the 99%
    pre = [row(0, None, 0.96, 0.95, 0.94), row(1, 0.0, 0.96, 0.95, 0.94), row(2, 0.02, 0.97, 0.96, 0.95), row(3, 0.05, 0.995, 0.97, 0.95)]
    r = run_js(f"""
const E = R.createEngine();
R.feedRows(E, [{{ race: {json.dumps(RACE)}, rows: R.normRows({json.dumps(pre)}, "v3") }}], names);
console.log(JSON.stringify(E.events.map((e) => [e.kind, e.text])));
""")
    assert r == [["model", "Georgia Senate: the model reaches 99% for D (5% counted)."]]


LEAD_ROWS = [row(m, 0.02 + m / 100, p, pm) for m, p, pm in [(0, 0.5, 0.5), (5, 0.7, 0.6), (10, 0.91, 0.7), (20, 0.95, 0.8), (25, 0.96, 0.92), (30, 0.97, 0.95)]]


def test_lead_minutes_board_and_disagreements():
    r = run_js(f"""
const rows = R.normRows({json.dumps(LEAD_ROWS)}, "v3");
const dip = R.normRows({json.dumps(LEAD_ROWS + [row(31, 0.4, 0.8, 0.95), row(33, 0.4, 0.93, 0.95)])}, "v3");   // the model dips under 90% and is back
const board = R.boardRow({json.dumps(RACE)}, rows, names);
const pre = R.boardRow({json.dumps(RACE)}, R.normRows({json.dumps([row(0, None, 0.4, 0.45, 0.46)])}, "v3"), names);
const some = [{{ label: "B", close: 0.3, lead: null }}, {{ label: "A", close: 0.1, lead: 5 }}, {{ label: "C", close: 0.2, lead: -3 }}];
console.log(JSON.stringify({{
  lead: R.leadOf(rows), early: R.leadOf(rows.slice(0, 4)).minutes, dip: R.leadOf(dip).minutes,
  board: [board.status, board.lead, board.model, board.pm, board.counted, board.side, board.leaderLetter],
  pre: [pre.status, pre.lead, pre.side, pre.model, pre.pm, pre.kalshi],
  status: [R.statusOf({{ c: 0.5, p: 0.995, pm: 0.97, k: 0.96 }}), R.statusOf({{ c: 0.5, p: 0.93, pm: 0.97, k: null }}), R.statusOf({{ c: 0.5, p: 0.7, pm: 0.97, k: null }}), R.statusOf({{ c: 0.005, p: 0.99, pm: 0.99, k: null }})],
  asc: R.sortBoard(some, "lead", 1).map((x) => x.label), desc: R.sortBoard(some, "lead", -1).map((x) => x.label), close: R.sortBoard(some, "close", 1).map((x) => x.label),
  dis: R.disagreements([board, pre]).map((d) => [d.row.id, Math.round(d.gap), d.source]),
}}));
""")
    # the model's current 90% stretch began at minute 10, Polymarket's at 25 (it was at 80% at minute 20): the model was 15 minutes first
    assert r["lead"]["minutes"] == 15 and r["lead"]["side"] == "a" and r["lead"]["kalshi"] is None
    assert r["early"] is None                  # Polymarket not at 90% yet: no lead
    assert r["dip"] == -8                      # the model's stretch starts over at minute 33; Polymarket's began at 25
    assert r["board"] == ["model 90%", 15, 0.97, 0.95, pytest.approx(0.32), "a", "D"]
    assert r["pre"][:3] == ["not started", None, "b"] and r["pre"][3:] == [pytest.approx(0.6), pytest.approx(0.55), pytest.approx(0.54)]
    assert r["status"] == ["decided", "model 90%", "counting", "not started"]
    assert r["asc"] == ["A", "C", "B"] and r["desc"] == ["C", "A", "B"] and r["close"] == ["A", "C", "B"]   # no lead sorts last either way
    assert r["dis"] == [["ga-sen", 2, "Polymarket"]]


# ------------------------------------------------------------------------------------------------ groups
COUNTIES = {"fips": ["01001", "01003", "01005", "01007"], "pop": [100, 300, 100, 100], "ba": [0.2, 0.4, 0.1, 0.3], "age65": [0.2, 0.1, 0.3, 0.2],
            "medinc": [50000, 70000, 40000, None], "white": [0.6, 0.7, 0.4, 0.5], "black": [0.3, 0.2, 0.55, 0.4], "hisp": [0.05, 0.05, 0.03, 0.05],
            "cls": [[0, 0, 0, 0, 0]] * 4, "p20": [0.1, 0.2, -0.3, 0.0], "p24": [0.0, 0.1, -0.4, -0.1], "g22": [0.05, 0.15, -0.35, None], "s22": [0.0, 0.0, 0.0, 0.0]}
GROUPS = {"dimensions": [{"id": "place", "label": "Urban to rural", "groups": [{"id": "big_city", "label": "Big cities"}]}],
          "regions": {"al": [{"id": "south", "label": "South Alabama", "fips": ["01001", "01003", "01005"]}]}}
AL = {"race_id": "al-gov", "region": "al", "a": {"id": "dem", "name": "A", "color": "#1f5fbf"}, "b": {"id": "rep", "name": "B", "color": "#c8102e"}}


def frame(minute, f, a, so=None):
    return {"t": f"2026-11-04T{minute // 60:02d}:{minute % 60:02d}:00Z", "f": f, "a": a, "proj": [500] * 4, **({"turnout_so_far": so} if so else {})}


def group_run(frames, v0=(1000, 3000, 1000, 1000)):
    units = {"race_id": "al-gov", "unit_ids": [f"us-al-{f}" for f in COUNTIES["fips"]], "V0": list(v0), "turnout_exp": list(v0), "frames": frames}
    return run_js(f"""
const C = {json.dumps(COUNTIES)}, G = {json.dumps(GROUPS)}, U = {json.dumps(units)}, RACE = {json.dumps(AL)};
const A = await import({json.dumps((ROOT / "site" / "groups-agg.js").as_uri())});
const prep = A.prepRace(U, RACE, C, A.countyIndex(C));
const E = R.createEngine();
const ctx = {{ races: [RACE], preps: new Map([["al-gov", prep]]), groups: G, counties: C, names }};
R.feedGroups(E, ctx);
const once = E.events.map((e) => e.text);
R.feedGroups(E, ctx);                                   // again: nothing new
console.log(JSON.stringify({{ once, all: E.events.map((e) => e.text), kinds: E.events.map((e) => e.kind), us: R.groupHeadlines(ctx, "us").length, al: R.groupHeadlines(ctx, "al").map((h) => [h.label, h.n, h.who]) }}));
""")


def test_group_events_from_complete_groups_once_and_rate_limited():
    done = [960] * 4
    # three Alabama counties and the fourth, all complete, are 5 to 7 points more Democratic than in 2024: the kind-of-place group
    # (all four) and the key region (three) both qualify. One line per 5 minutes, the stronger (the region's 6.8 points) first,
    # the other at the next evaluation 6 minutes later
    a = [540, 590, 310, 440]
    so = [960, 2880, 960, 960]
    r = group_run([frame(0, [0] * 4, [-1] * 4), frame(10, done, a, so), frame(16, done, a, so)])
    assert r["once"] == r["all"] and set(r["kinds"]) == {"group"}
    assert r["all"] == [
        "South Alabama: D shift of 6.8 points against the 2024 presidential vote, 3 fully counted counties.",
        "Big cities, nationally: D shift of 5.3 points against the 2024 presidential vote, 4 fully counted counties.",
    ]
    assert r["us"] == 1 and r["al"] == [["South Alabama", 3, "D"], ["Big cities", 4, "D"]]


def test_group_turnout_event():
    # every county exactly at its 2024 margin (no shift); votes 40% above what was expected in complete counties
    a = [int(500 + 500 * m) for m in COUNTIES["p24"]]
    r = group_run([frame(10, [960] * 4, a, [1400, 4200, 1400, 1400])])
    assert r["all"] == ["Big cities, nationally: turnout 40% above what was expected, 4 fully counted counties."]


def test_incomplete_groups_make_no_event():
    a = [540, 590, 310, 440]
    # two complete counties (the floor is three); the others 60% counted with a huge early swing
    r = group_run([frame(10, [960, 960, 600, 600], [540, 590, 900, 900], [960, 2880, 600, 600])])
    assert r["all"] == [] and r["us"] == 0 and r["al"] == []
    # three complete counties with under 15% of the group's expected vote (the big county is not complete)
    r = group_run([frame(10, [960, 100, 960, 960], a, [96, 0, 96, 96])], v0=(100, 30000, 100, 100))
    assert r["all"] == [] and r["us"] == 0
    # nothing counted
    r = group_run([frame(10, [0] * 4, [-1] * 4)])
    assert r["all"] == []
