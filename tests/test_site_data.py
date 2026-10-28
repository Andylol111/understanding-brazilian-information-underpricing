"""Every published data file must parse: a broken site/data JSON blanks the public dashboard."""

import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "site" / "data"
FILES = sorted(DATA.rglob("*.json"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(DATA)))
def test_site_json_parses(path: Path) -> None:
    json.loads(path.read_text(encoding="utf-8"))


def test_elections_have_data_or_are_scheduled() -> None:
    elections = json.loads((DATA / "elections.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in elections]
    assert len(ids) == len(set(ids)), "duplicate election ids"
    for e in elections:
        if e["status"] != "scheduled":
            assert (DATA / e["id"] / "meta.json").exists(), f"{e['id']} has no meta.json"
