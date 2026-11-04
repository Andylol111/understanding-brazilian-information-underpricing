"""brunder.us_extra_inky: the early-signal file written from the recorded civicAPI House document (display only, unregistered).

Synthetic House documents on the repo's own district baselines: a known tilt must come back as the known swing; uncontested races,
partial counts and bad snapshots must not break the file; the loop must never raise."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from brunder import us_extra_inky as inky

NOW = datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bases():
    return inky.load_baselines()


def house_doc(bases, states, tilt=0.03, pct=40.0, turnout=1.0, skip=()):
    """Districts of `states` counted to `pct` percent at D share = 2024 president + tilt, and expected turnout x `turnout`."""
    reg = {}
    for k, b in bases["districts"].items():
        if b["st"] not in states or k in skip:
            continue
        n = int(inky.EST_TURNOUT * b["pres24_votes"] * pct / 100 * turnout)
        d = int(n * min(0.99, b["pres24_d2"] + tilt))
        reg[k.lower()] = {"name": k, "type": "Congressional District", "percent_reporting": pct,
                          "candidates": [{"name": "Dem", "party": "Democratic", "votes": d}, {"name": "Rep", "party": "Republican", "votes": n - d}]}
    return {"test_data": False, "last_updated": "t", "region_results": reg}


def test_known_tilt_comes_back_as_swing_and_expected_gap(bases):
    out = inky.build(house_doc(bases, {"IN", "KY"}, tilt=0.03), bases, NOW)
    a = out["aggregates"]["in_ky"]
    assert out["status"] == "counting" and out["label"].startswith("early signal, not validated")
    assert a["swing_pres"] == pytest.approx(3.0, abs=0.05)
    assert a["poll_implied"] == pytest.approx(5.5, abs=0.5)                    # generic ballot swing in the baseline
    assert a["swing_expected"] == pytest.approx(a["swing_pres"] - a["poll_implied"], abs=0.05)
    assert a["turnout_ratio"] == pytest.approx(1.0, abs=0.01) and a["reporting_share"] == pytest.approx(0.4, abs=0.01)
    assert {f"IN-0{i}" for i in range(1, 10)} | {f"KY-0{i}" for i in range(1, 7)} <= set(out["districts"])
    row = out["districts"]["IN-05"]
    assert row["pres24_d2"] == 0.4144 and row["house24_d2"] == 0.4018 and row["d2"] == pytest.approx(0.4144 + 0.03, abs=0.001)
    assert row["swing_pres"] == pytest.approx(3.0, abs=0.1) and row["swing_house24"] == pytest.approx(4.3, abs=0.1)


def test_turnout_ratio_follows_votes_counted_vs_expected(bases):
    out = inky.build(house_doc(bases, {"IN"}, turnout=0.5), bases, NOW)
    assert out["districts"]["IN-01"]["turnout_ratio"] == pytest.approx(0.5, abs=0.01)
    assert out["aggregates"]["in"]["turnout_ratio"] == pytest.approx(0.5, abs=0.01)


def test_uncontested_and_missing_pairs_carry_no_signal(bases):
    doc = house_doc(bases, {"IN", "KY"})
    doc["region_results"]["ky-04"]["candidates"] = [{"name": "Rep", "party": "Republican", "votes": 90000}]
    doc["region_results"]["in-01"]["candidates"] = [{"name": "A", "party": "Democratic", "votes": 5}, {"name": "B", "party": "Democratic", "votes": 9}]
    out = inky.build(doc, bases, NOW)
    ky4, in1 = out["districts"]["KY-04"], out["districts"]["IN-01"]
    assert ky4["uncontested"] and ky4["d2"] is None and ky4["swing_pres"] is None and "KY-04" not in out["aggregates"]["ky"]["districts"]
    assert in1["uncontested"] and "IN-01" not in out["aggregates"]["in"]["districts"]


def test_partial_counts_enter_from_ten_percent_and_districts_listed_from_twenty(bases):
    low = inky.build(house_doc(bases, {"IN", "OH"}, pct=8.0), bases, NOW)
    assert low["aggregates"]["in"]["n"] == 0 and low["aggregates"]["in"]["swing_expected"] is None
    assert "OH-01" not in low["districts"] and "IN-01" in low["districts"]          # IN / KY always listed
    mid = inky.build(house_doc(bases, {"IN", "OH"}, pct=15.0), bases, NOW)
    assert mid["aggregates"]["in"]["n"] == 9 and "OH-01" not in mid["districts"]
    hi = inky.build(house_doc(bases, {"IN", "OH"}, pct=30.0), bases, NOW)
    assert "OH-01" in hi["districts"] and hi["aggregates"]["national"]["n"] == 9 + 15


def test_waiting_before_any_vote_and_without_a_document(bases):
    for doc in (None, house_doc(bases, {"IN", "KY"}, pct=0.0)):
        out = inky.build(doc, bases, NOW)
        assert out["status"] == "waiting" and out["aggregates"]["in_ky"]["n"] == 0 and out["adjusted"]["applied"] is False
        json.dumps(out, allow_nan=False)                                             # strict JSON


def test_national_read_needs_twenty_districts_at_25_percent(bases):
    few = inky.build(house_doc(bases, {"IN", "KY"}, pct=40.0), bases, NOW)
    nat = few["aggregates"]["national"]
    assert nat["ready"] is False and nat["n_reporting_25"] == 15
    assert "too early" in nat["note"] and few["adjusted"]["applied"] is False and few["adjusted"]["basis"] is None   # IN / KY alone never move seats
    many = inky.build(house_doc(bases, set("IN KY OH TX FL GA NC".split()), pct=40.0), bases, NOW)
    assert many["aggregates"]["national"]["ready"] is True and many["adjusted"]["basis"] == "national"


def test_adjusted_projection_shrinks_and_moves_only_movable_races(bases):
    states = set("IN KY OH TX FL GA NC".split())
    out = inky.build(house_doc(bases, states, tilt=0.0), bases, NOW)                 # counted at 2024 president: 5.5 pts below expected
    adj = out["adjusted"]
    a = out["aggregates"]["national"]
    assert a["ready"] and adj["basis"] == "national"
    assert adj["applied"] and adj["n"] == a["n"] and adj["weight"] == pytest.approx(a["n"] / (a["n"] + 20), abs=0.001)
    assert adj["shift_pts"] == pytest.approx(adj["weight"] * a["swing_expected"], abs=0.02) and adj["shift_pts"] < 0
    for k, e in adj["house"].items():
        assert e["adj"] <= e["pre"] + 1e-9                                           # a negative shift never helps a Democrat
    seats = adj["seats"]
    assert seats["house"]["adj"]["expected_d"] < seats["house"]["pre"]["expected_d"]
    assert seats["senate"]["adj"]["expected_d"] <= seats["senate"]["pre"]["expected_d"]
    pres = [e for e in adj["house"].values() if e["pre"] in (0.0, 1.0)]
    assert all(e["adj"] == e["pre"] and e["dz"] == 0 for e in pres)                  # certain seats stay put
    small = inky.build(house_doc(bases, {"IN"}, tilt=0.0, skip={"IN-01", "IN-02", "IN-03", "IN-04", "IN-05"}), bases, NOW)
    assert small["adjusted"]["applied"] is False and small["adjusted"]["shift_pts"] == 0.0     # no national read yet


def test_senate_poll_comparison_line_is_derived_from_the_overview(bases):
    sw = {s["st"]: s["swing"] for s in bases["senate_swings"]}
    assert set(sw) == set(inky.P7) and sw["KS"] == pytest.approx(9.1, abs=0.2) and sw["MI"] == pytest.approx(3.2, abs=0.2)
    text = inky.build(None, bases, NOW)["polls"]["text"]
    assert "(KS)" in text and "vs 2024 in the 7 Senate states" in text


def test_in05_composition_uses_the_counties_that_reported(bases):
    import pandas as pd
    prior = bases["in05_prior"]
    units = sorted(prior)
    d0, r0 = prior[units[0]]
    rows = pd.DataFrame([{"race": "house-05", "unit_id": units[0], "party": "DEM", "votes": d0 / 2}, {"race": "house-05", "unit_id": units[0], "party": "REP", "votes": r0 / 2}])
    comp = inky.in05_composition(rows, prior, bases["districts"]["IN-05"])
    base = bases["districts"]["IN-05"]
    # the one county votes exactly like its 2024 president: the gap is the county's own prior share minus (that + the poll-implied swing)
    assert comp["swing_expected"] == pytest.approx(-100 * (base["share"] - base["pres24_d2"]), abs=0.05) and comp["counties_reporting"] == 1


def test_latest_snapshot_skips_a_corrupt_newest_file(tmp_path, bases):
    d = tmp_path / "civic_87524"
    d.mkdir()
    good = house_doc(bases, {"IN"})
    (d / "20261104T000000.000000Z.json.gz").write_bytes(gzip.compress(json.dumps(good).encode()))
    (d / "20261104T000100.000000Z.json").write_text('{"region_results": {"in-01": ')           # truncated: partial write
    (d / "20261104T000200.000000Z.json.gz").write_bytes(b"not gzip")
    t, doc, name = inky.latest_snapshot(d, inky._parse_house)
    assert name == "20261104T000000.000000Z.json.gz" and "in-01" in doc["region_results"]


def test_loop_writes_the_file_and_survives_errors(tmp_path, bases, capsys):
    shared = tmp_path / "shared"
    d = shared / "civic_87524"
    d.mkdir(parents=True)
    (d / "20261104T000000.000000Z.json").write_text(json.dumps(house_doc(bases, {"IN", "KY"})))
    out = tmp_path / "out"
    inky.run_loop(out, tmp_path / "own", shared, interval=0.0, cycles=2)
    doc = json.loads((out / "us2026" / "inky.json").read_text())
    assert doc["status"] == "counting" and doc["aggregates"]["in_ky"]["n"] > 5
    # a corrupt newest file and an unreadable baseline: logged and skipped, never raised
    (d / "20261104T000100.000000Z.json").write_text("{bad")
    inky.run_loop(out, tmp_path / "own", shared, interval=0.0, cycles=1)
    assert json.loads((out / "us2026" / "inky.json").read_text())["status"] == "counting"
    inky.run_loop(out, tmp_path / "own", shared, interval=0.0, cycles=2, root=tmp_path / "nowhere")
    assert "inky error, skipped" in capsys.readouterr().out
    # nothing recorded yet: the file says "waiting" (the page shows "waiting for 6 pm ET")
    inky.run_loop(tmp_path / "out2", tmp_path / "own", tmp_path / "none", interval=0.0, cycles=1)
    assert json.loads((tmp_path / "out2" / "us2026" / "inky.json").read_text())["status"] == "waiting"


def test_live_runner_isolates_the_inky_loop(monkeypatch, tmp_path):
    from brunder import us_extra_live

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(us_extra_live.us_extra_inky, "run_loop", boom)
    us_extra_live._inky(tmp_path, tmp_path, None, 30.0, None)                       # logs, returns: the models keep running


def test_in05_county_file_replaces_the_district_expectation(tmp_path, bases):
    """The added Indiana recorder's file (in_house): two counties of IN-05 report, the district's expectation becomes those counties' 2024 president."""
    from brunder import extra_in
    prior = bases["in05_prior"]
    races = []
    for fips, mult in (("18057", 1.0), ("18067", 1.0)):                               # Hamilton, Howard: counted exactly like 2024
        d, r = prior[f"us-in-{fips}"]
        races.append({"Jurisdiction": {"FIPS": fips, "JURISDICTION_NAME": fips}, "SubSortOrder": "United States Representative, (5) District",
                      "Candidates": {"Candidate": [{"NAME_ON_BALLOT": "Ford", "PARTY_ABBREV": "D", "TOTAL_VOTES": str(int(d * mult / 3))},
                                                   {"NAME_ON_BALLOT": "Spartz", "PARTY_ABBREV": "R", "TOTAL_VOTES": str(int(r * mult / 3))}]}})
    blob = json.dumps({"Root": {"OfficeCategory": {"Regions": {"Region": [{"Races": {"Race": races}}]}}}})
    d = tmp_path / "own" / "in_house"
    d.mkdir(parents=True)
    (d / "20261104T000000.000000Z.json").write_text(blob)
    comp = inky._in5(tmp_path / "own", bases, {})
    assert comp and comp["counties_reporting"] == 2
    gap = bases["districts"]["IN-05"]["share"] - bases["districts"]["IN-05"]["pres24_d2"]
    assert comp["swing_expected"] == pytest.approx(-100 * gap, abs=0.1)              # counted = county 2024 president; expected adds the poll swing
    doc = house_doc(bases, {"IN"}, tilt=0.0)
    row = inky.build(doc, bases, NOW, in5=comp)["districts"]["IN-05"]
    assert row["expected_basis"].startswith("county composition") and row["swing_expected"] == comp["swing_expected"]
