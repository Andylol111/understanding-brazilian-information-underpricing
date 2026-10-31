"""analysis/sens_export.py: a sensitivity is added next to v1 without changing any v1 field (synthetic election, no network)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

import sens_export as se  # noqa: E402


def make_election(tmp_path: Path) -> Path:
    d = tmp_path / "xx2030"
    d.mkdir()
    times = pd.date_range("2030-01-01T20:00:00Z", periods=60, freq="1min")
    counted = np.clip(np.arange(60) / 55, 0, 1)
    rows = [{"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "counted": round(float(c), 4), "raw": 0.5, "share": 0.52, "sd": 0.01,
             "P": 0.8, "pm": 0.5 if i < 30 else 0.95, "kalshi": None} for i, (t, c) in enumerate(zip(times, counted))]
    (d / "timeline.json").write_text(json.dumps({"main": rows, "other": [{k: (None if k in ("share", "sd", "raw") else v) for k, v in r.items()} for r in rows]}))
    (d / "meta.json").write_text(json.dumps({"id": "xx2030", "races": [{"race_id": "main"}, {"race_id": "other"}]}))
    (d / "stats.json").write_text(json.dumps({"calibration": {"main": {"coverage90_1_95": 0.5}}, "settle": {"main": {"polymarket": "2030-01-01T20:40:00Z"}},
                                              "market_classification": {"by_race": {"main": {"decided_before_count": False, "outcome_a": True},
                                                                                    "other": {"decided_before_count": False, "outcome_a": True}}}}))
    return d


def series(times, p_late=0.97):
    t = pd.DatetimeIndex(times)
    return pd.DataFrame({"t": t, "P": np.where(np.arange(len(t)) < 20, 0.6, p_late), "share": 0.515, "lo": 0.50, "hi": 0.53})


def test_settle_needs_to_stay_up():
    t = pd.Series(pd.date_range("2030-01-01", periods=5, freq="1min"))
    assert se.settle(t, pd.Series([False, True, False, True, True])) == t.iloc[3]
    assert pd.isna(se.settle(t, pd.Series([True, True, True, True, False])))


def test_write_sensitivity_adds_only_new_fields(tmp_path):
    d = make_election(tmp_path)
    old = json.loads((d / "timeline.json").read_text())
    times = pd.date_range("2030-01-01T20:00:00Z", periods=60, freq="1min")
    block = se.write_sensitivity("xx2030", "mix", "label", "analysis/x.py", "note", {"main": series(times), "other": series(times)[["t", "P"]]},
                                 {"main": 0.52, "other": None}, "main", data_dir=tmp_path)
    new = json.loads((d / "timeline.json").read_text())
    assert {k: [{a: b for a, b in r.items() if "_sens_" not in a} for r in rows] for k, rows in new.items()} == old
    assert new["main"][5]["P_sens_mix"] == 0.6 and new["main"][30]["P_sens_mix"] == 0.97
    assert new["main"][5]["share_sens_mix"] == 0.515 and abs(new["main"][5]["sd_sens_mix"] - 0.03 / (2 * se.Z90)) < 1e-5
    assert "share_sens_mix" not in new["other"][5]
    meta = json.loads((d / "meta.json").read_text())
    assert meta["sensitivities"] == [{"key": "mix", "label": "label", "config": "analysis/x.py", "note": "note"}]
    m = block["by_race"]["main"]
    assert m["coverage90_1_95"] == 1.0 and m["contested"] is True
    assert m["settle_90"] == "2030-01-01T20:20:00Z"
    assert m["market_settle_90"] == "2030-01-01T20:40:00Z" and m["lead_90_min"] == 20.0
    assert block["summary"]["median_lead_90_min_gate1pct"] is not None
    assert "coverage90_1_95" not in block["by_race"]["other"] and block["by_race"]["other"]["brier_1_95"] is not None
    stats = json.loads((d / "stats.json").read_text())
    assert stats["calibration"] == {"main": {"coverage90_1_95": 0.5}} and "mix" in stats["sensitivities"]


def test_write_is_idempotent_and_replaces_the_entry(tmp_path):
    d = make_election(tmp_path)
    times = pd.date_range("2030-01-01T20:00:00Z", periods=60, freq="1min")
    for _ in range(2):
        se.write_sensitivity("xx2030", "mix", "label", "c", "n", {"main": series(times)}, {"main": 0.52}, "main", data_dir=tmp_path)
    meta = json.loads((d / "meta.json").read_text())
    assert len(meta["sensitivities"]) == 1
    assert sum("P_sens_mix" in r for r in json.loads((d / "timeline.json").read_text())["main"]) == 60
