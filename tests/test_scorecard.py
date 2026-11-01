"""Aggregation of analysis/scorecard.py on small synthetic elections."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

import scorecard as sc  # noqa: E402


def row(minute, counted, p, share=None, sd=0.01, pm=None, raw=None, kalshi=None):
    return {"t": f"2030-01-01T00:{minute:02d}:00Z", "counted": counted, "P": p, "share": share, "sd": sd, "pm": pm, "raw": raw,
            "kalshi": kalshi}


def night(winner_a=True):
    """One race, a wins. Model passes 0.9 at minute 2, the market at minute 6; final share 0.60."""
    w = 1.0 if winner_a else 0.0
    rows = [row(0, 0.0, 0.5, 0.5, pm=0.5), row(1, 0.01, 0.6, 0.55, pm=0.5, raw=0.5),
            row(2, 0.05, 0.95, 0.59, pm=0.6, raw=0.52), row(4, 0.10, 0.97, 0.60, pm=0.7, raw=0.55),
            row(6, 0.25, 0.99, 0.60, pm=0.92, raw=0.58), row(8, 0.50, 0.99, 0.60, pm=0.97, raw=0.59),
            row(9, 0.99, w, 0.60, pm=w, raw=0.60)]
    return {"race": rows}


def test_outcome_from_final_probability_and_settle():
    assert sc.outcome_a(night()["race"]) is True
    assert sc.outcome_a(night(False)["race"]) is False
    assert sc.outcome_a(night()["race"], {"outcome_a": False}) is False
    assert sc.outcome_a([{"pm": 0.3}]) is False and sc.outcome_a([]) is None


def test_stays_above_skips_unpriced_and_resets_after_a_dip():
    rows = [row(0, 0.0, 0.95), row(1, 0.0, 0.5), row(2, 0.0, None), row(3, 0.0, 0.95)]
    assert sc.stays_above(rows, True, "P", 0) == rows[3]["t"]
    assert sc.stays_above(rows, False, "P", 0) is None  # a lost side never reaches 0.9


def test_race_times_and_lead_counted_from_the_1pct_gate():
    t = sc.race_times(night()["race"], True, "P")
    assert t["model"].endswith("00:02:00Z") and t["polymarket"].endswith("00:06:00Z") and t["kalshi"] is None
    assert sc.minutes_between(t["model"], t["polymarket"]) == 4


def test_decided_before_count():
    rows = [row(0, None, None, pm=0.95), row(1, None, None, pm=0.93), row(2, None, None, pm=0.99)]
    assert sc.decided_before_count(rows, "2030-01-01T00:00:00Z", True)
    rows[1]["pm"] = 0.7  # fell below 0.8 after close: contested
    assert not sc.decided_before_count(rows, "2030-01-01T00:00:00Z", True)
    assert not sc.decided_before_count(rows, "2030-01-01T00:00:00Z", None)


def test_share_error_and_official_final_from_published_early_error():
    rows = night()["race"]
    assert sc.final_share(rows) == 0.60
    # published: the raw count (0.52 at 5% counted) was 8 points under the official final, so the final is 0.60
    assert sc.official_final(rows, {"5%": {"model": -1.0, "raw": -8.0}}) == pytest.approx(0.60)
    err = sc.share_errors(rows, 0.60, "share")
    assert err[0.05] == (pytest.approx(-1.0), pytest.approx(-8.0)) and err[0.10][0] == pytest.approx(0.0)
    assert err[0.75][0] == pytest.approx(0.0)  # the first row with 75% counted is the 99% row
    assert sc.share_errors(rows[:5], 0.60, "share").get(0.75) is None  # no row at all


def test_band_coverage_uses_1_to_95_counted():
    rows = night()["race"]
    cov, n = sc.band_coverage(rows, 0.60, "share", "sd")
    assert n == 5  # counted 0.01 .. 0.50; the 0.99 row and the 0.0 row are outside
    assert cov == pytest.approx(4 / 5)  # only the 0.55 projection (5 points off, band +-1.6 points) misses
    assert sc.band_coverage(rows, None, "share", "sd") is None


def entry(i, evidence="not-preregistered", status="replay"):
    return {"id": i, "name": i, "date": "2030-01-01", "country": "XX", "evidence": evidence, "status": status}


def test_election_calibration_weights_each_minute_equally_within_the_election():
    meta = {"races": [{"race_id": "race", "name": "Race"}], "poll_close_utc": "2030-01-01T00:00:00Z"}
    rec, cal = sc.score_election(entry("e1"), meta, {}, night())
    v = rec["versions"]["v1"]
    assert v["n_minutes"] == 5 and sum(b["n"] for b in cal["v1"]) == 5  # counted .01 .05 .10 .25 .50; the 99% row is out
    assert cal["v1"][6]["n"] == 1 and cal["v1"][9]["n"] == 4  # P .6 -> bin 6; .95 .97 .99 .99 -> bin 9
    assert sum(b["w"] for b in cal["v1"]) == pytest.approx(1.0)
    assert v["market"]["n_markets"] == 1 and v["market"]["median_lead_min"] == 4 and v["market"]["model_first"] == 1


def test_pool_gives_each_election_equal_weight():
    meta = {"races": [{"race_id": "race", "name": "Race"}], "poll_close_utc": "2030-01-01T00:00:00Z"}
    short = {"race": night()["race"][:3]}  # fewer minutes, same total weight
    a = sc.score_election(entry("a"), meta, {}, night())
    b = sc.score_election(entry("b"), meta, {}, night(False))  # same forecasts, other side wins
    pooled = sc.pool([a, b])["v1"]
    top = pooled["calibration"][9]
    assert pooled["n_elections"] == 2
    assert top["observed"] == pytest.approx(0.5)  # one election won, one lost, equal weight
    assert top["n_elections"] == 2
    c = sc.score_election(entry("c"), meta, {}, short)
    assert sum(x["w"] for x in c[1]["v1"]) == pytest.approx(1.0)


def test_election_without_a_count_timeline_has_no_model_scores():
    rows = [{"t": "2030-01-01T00:00:00Z", "counted": None, "P": 0.3, "pm": 0.4}, {"t": "2030-01-01T01:00:00Z", "counted": None, "P": 0.3, "pm": 0.9}]
    rec, cal = sc.score_election(entry("n"), {"races": []}, {}, {"r": rows})
    assert rec["versions"] == {} and cal == {} and rec["has_market"]


def test_post_hoc_versions_are_kept_apart_from_headline_and_forward_versions_are_registered():
    rows = night()["race"]
    for d in rows:
        d["P_sens_mix"] = d["P"]
        d["P_v2"] = d["P"]
    meta = {"races": [], "poll_close_utc": "2030-01-01T00:00:00Z"}
    rec, _ = sc.score_election(entry("r"), meta, {}, {"race": rows})
    assert set(rec["versions"]) == {"v1", "mix"}
    rec, _ = sc.score_election(entry("f", "forward", "finished"), meta, {}, {"race": rows})
    assert set(rec["versions"]) == {"v1", "v2"}


def test_groups():
    assert sc.group_of(entry("x", "forward")) == "forward"
    assert sc.group_of(entry("x", "pre-specified")) == "replay_prespecified"
    assert sc.group_of(entry("x", "in-sample")) == "replay_in_sample"
    assert sc.group_of(entry("x", "model-only")) == "replay_other"
    assert sc.group_of(entry("mx2024_qc")) == "sensitivity"


def test_parse_md_tables():
    text = "Intro line\n\n| subset | label | n | mae |\n|---|---|---|---|\n| all | unified | 264 | 2.821 |\n| all | v3 | 219 | 2.5 |\n\nNext\n\n| a |\n|---|\n| x |\n"
    t = sc.parse_md_tables(text)
    assert t[0]["title"] == "Intro line" and t[0]["rows"][0] == {"subset": "all", "label": "unified", "n": 264, "mae": 2.821}
    assert t[1]["title"] == "Next"


def test_backtest_reads_the_documented_tables():
    bt = sc.backtest()
    sections = {s["id"]: s["tables"] for s in bt["sections"]}
    labels = {r["label"] for t in sections["statewide"] for r in t["rows"] if "label" in r}
    assert {"unified"} <= labels and any(l.startswith("v1") for l in labels) and any(l.startswith("v3") for l in labels)
    assert sections["house"] and sections["county"]


def test_scheduled_elections_are_pending_not_scored(tmp_path):
    import json
    (tmp_path / "elections.json").write_text(json.dumps([entry("f1", "forward", "scheduled")]))
    index, records, _ = sc.build(tmp_path)
    assert records == [] and index["pending_forward"][0]["id"] == "f1"
