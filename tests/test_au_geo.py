"""analysis/au_geo.py: the command line must not build or write anything unless asked to (`build`)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "site" / "data" / "geo"
SCRIPT = ROOT / "analysis" / "au_geo.py"


def snapshot(d: Path) -> dict:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in d.glob("au_*.topo.json")}


def test_help_writes_nothing(tmp_path):
    before = snapshot(GEO)
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, env={"BRUNDER_DATA": str(tmp_path), "PATH": ""})
    assert r.returncode == 0 and "build" in r.stdout
    assert snapshot(GEO) == before                       # the committed geometry is untouched
    assert list(tmp_path.iterdir()) == []                # no temp, raw or cache files


def test_no_command_is_an_error_not_a_build(tmp_path):
    before = snapshot(GEO)
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env={"BRUNDER_DATA": str(tmp_path), "PATH": ""})
    assert r.returncode != 0
    assert snapshot(GEO) == before and list(tmp_path.iterdir()) == []
