"""Build the Mexico federal-district map (site/data/geo/mx_districts.topo.json, object `districts`).

    python3 analysis/mx_geo.py [--cache DIR]

Source and licence
------------------
  * Instituto Nacional Electoral, PREP 2024 "distritos.geojson" (the 300 federal electoral districts of the
    2023 distritación, WGS84), served by the PREP publication site:
    https://prep2024.ine.mx/publicacion/nacional/assets/GeoJson/distritos/distritos.geojson
    INE "Términos de libre uso" (sicee.ine.mx): copy, publish, adapt and redistribute, citing the source, without
    implying INE endorsement. Accessed 2026-10-28.
unit_id = `mx-<state abbreviation>-d<district, 2 digits>` (brunder.mexico.STATES), e.g. mx-mex-d18; the same ids
analysis/mx_export.py writes into site/data/mx2024/units.json. Simplified with mapshaper (analysis/geo_build.py's
pinned version and checks) to stay under 3 MB. The download is cached (default ~/.cache/brunder-geo).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from geo_build import OUT, check, fetch, mapshaper

from brunder.mexico import STATES

DISTRICTS = "https://prep2024.ine.mx/publicacion/nacional/assets/GeoJson/distritos/distritos.geojson"


def unit_id(ent: int, district: int) -> str:
    return f"mx-{STATES[ent][0]}-d{district:02d}"


def build(cache: Path, tmp: Path) -> None:
    src = json.loads(fetch(DISTRICTS, cache).read_text())
    feats = []
    for f in src["features"]:
        e, d = int(f["properties"]["entidad"]), int(f["properties"]["distrito"])
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"unit_id": unit_id(e, d), "name": f"{STATES[e][1]}, district {d}"}})
    assert len(feats) == 300, len(feats)
    inp = tmp / "districts.geojson"
    inp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = OUT / "mx_districts.topo.json"
    mapshaper("-i", str(inp), "name=districts", "-proj", "wgs84", "-simplify", "25%", "keep-shapes",
              "-o", str(out), "format=topojson", "quantization=100000", "id-field=unit_id")
    check(out, "districts")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "brunder-geo")
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        build(a.cache, Path(t))


if __name__ == "__main__":
    main()
