"""Build the Taipei li map (site/data/geo/tw_taipei_li.topo.json, object `li`).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/tw_geo.py

Source and licence
------------------
Ministry of the Interior, National Land Surveying and Mapping Center, village boundary map (村里界圖),
WFS layer `WFS:VILLAGE_NLSC`, https://wfs.nlsc.gov.tw/WFS; dataset https://data.gov.tw/dataset/7438.
Open Government Data License, version 1.0: https://data.gov.tw/license.

The WFS limits village requests to 10 km squares, so the script requests 16 overlapping official squares that
cover Taipei, one second apart, then keeps COUNTYCODE 63000 and de-duplicates by official VILLCODE.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from geo_build import OUT, mapshaper

from brunder import taiwan as tw
from brunder.paths import RAW

WFS = "https://wfs.nlsc.gov.tw/WFS"
CACHE = RAW / "taiwan" / "nlsc_village"
UA = "brunder/0.1 (research; recorder)"
X0 = (121.42, 121.50, 121.58, 121.66)
Y0 = (24.94, 25.02, 25.10, 25.18)
STEP = 0.08
MAX_BYTES = 1_000_000


def fetch_tiles() -> list[Path]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = []
    for x in X0:
        for y in Y0:
            path = CACHE / f"village_{x:.2f}_{y:.2f}.zip"
            if not path.exists():
                q = urllib.parse.urlencode({
                    "SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
                    "TYPENAME": "WFS:VILLAGE_NLSC", "SRSNAME": "EPSG:4326",
                    "BBOX": f"{x:.2f},{y:.2f},{x + STEP:.2f},{y + STEP:.2f}", "OUTPUTFORMAT": "SHAPE-ZIP"})
                req = urllib.request.Request(f"{WFS}?{q}", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=120) as r:
                    body = r.read()
                if not body.startswith(b"PK"):
                    raise RuntimeError(f"NLSC WFS returned non-ZIP data for {x},{y}: {body[:200]!r}")
                path.write_bytes(body)
                time.sleep(1)
            out.append(path)
    return out


def build(tmp: Path) -> Path:
    shapes = []
    for i, z in enumerate(fetch_tiles()):
        d = tmp / f"tile{i}"
        with zipfile.ZipFile(z) as f:
            f.extractall(d)
        shapes.append(str(next(d.glob("*.shp"))))
    out = OUT / "tw_taipei_li.topo.json"
    each = ("unit_id = 'tw-' + VILLCODE.slice(5, 8) + '-0' + VILLCODE.slice(8, 11), "
            "name = TOWNNAME + ' ' + VILLNAME")
    mapshaper("-i", *shapes, "combine-files", "encoding=big5", "-merge-layers", "force",
              "-filter", "COUNTYCODE == '63000'", "-uniq", "VILLCODE", "-each", each,
              "-filter-fields", "unit_id,name", "-rename-layers", "li", "-simplify", "weighted", "20%", "keep-shapes",
              "-o", str(out), "format=topojson", "quantization=100000", "id-field=unit_id")
    return out


def check(out: Path) -> None:
    doc = json.loads(out.read_text())
    ids = [g["properties"]["unit_id"] for g in doc["objects"]["li"]["geometries"]]
    expected = set(tw.historical_units(2022).index)
    assert len(ids) == len(set(ids)) == 456, (len(ids), len(set(ids)))
    assert set(ids) == expected, (sorted(set(ids) - expected)[:5], sorted(expected - set(ids))[:5])
    assert all(g.get("type") for g in doc["objects"]["li"]["geometries"])
    assert out.stat().st_size <= MAX_BYTES, out.stat().st_size
    print(f"{out}: {len(ids)} li, {out.stat().st_size / 1e6:.2f} MB")


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        check(build(Path(t)))


if __name__ == "__main__":
    main()
