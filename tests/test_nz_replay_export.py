"""Retrospective NZ night replays (site/data/nz2023, nz2020): labelled simulated + model only, small, well-formed."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DATA = ROOT / "site" / "data"
sys.path.insert(0, str(ROOT / "analysis"))


@pytest.mark.parametrize("year,evidence", [(2023, "model-only"), (2020, "in-sample")])
def test_published_replay(year, evidence):
    d = DATA / f"nz{year}"
    meta = json.loads((d / "meta.json").read_text())
    entry = json.loads((d / "entry.json").read_text())
    listed = {e["id"]: e for e in json.loads((DATA / "elections.json").read_text())}[f"nz{year}"]
    assert listed == entry and entry["status"] == "replay" and entry["evidence"] == evidence
    assert "retrospective" in entry["name"] and "simulated" in entry["name"]
    assert any("SIMULATED COUNT ORDER" in n for n in meta["notes"]) and any("NO MARKET" in n for n in meta["notes"])
    assert meta["geometry"] is None and not any(r["markets"] for r in meta["races"])
    tl = json.loads((d / "timeline.json").read_text())
    assert set(tl) == {r["race_id"] for r in meta["races"]}
    for rows in tl.values():
        assert all(r["pm"] is None and r["kalshi"] is None and 0 <= r["P"] <= 1 for r in rows)
        assert rows[0]["counted"] == 0 and rows[-1]["counted"] == 1
    assert sum(f.stat().st_size for f in d.iterdir()) < 15e6


def test_export_builds(tmp_path):
    import nz_history as H
    if not (H.HIST / "2023").exists() or not (H.HIST / "2020").exists():
        pytest.skip("historical voting-place files not downloaded")
    import nz_export_replay as X
    head = X.build(2023, tmp_path, draws=100)
    assert head["winner_market"] == "most-national" and head["final_seats"]["NAT"] == 48
    assert (tmp_path / "nz2023" / "units.json").exists()
