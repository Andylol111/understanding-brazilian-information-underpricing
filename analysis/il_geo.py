"""Build site/data/geo/il_localities.topo.json: Israeli locality polygons for the il2026 map.

Source: Central Bureau of Statistics (CBS), "אזורים סטטיסטיים 2022" (statistical areas 2022), feature service
published by the CBS ArcGIS organisation (owner ISRAEL_CBS_GIS, item 372606908bcf49a69adaebc24fefc04a). Used under
the CBS open licence (https://www.cbs.gov.il/he/Pages/רישיון-שימוש.aspx): copying, redistribution and derivative
works allowed, commercial or not, with the attribution given in site/data/il2026/meta.json.
Statistical areas are dissolved by locality code (SEMEL_YISHUV); unit_id = "il-<locality code>", the CEC/CBS code
used in units.json. Statistical areas of regional-council open land carry the council's code, not a voting
locality's, and are dropped with any other code that has no 2022 ballot box.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_geo.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from brunder import israel as il
from brunder.paths import RAW, ROOT

SERVICE = ("https://services2.arcgis.com/xMRYm7cNgdR5RN6F/arcgis/rest/services/Statistical__Areas_2022/"
           "FeatureServer/0/query")
ITEM = "https://www.arcgis.com/home/item.html?id=372606908bcf49a69adaebc24fefc04a"
CACHE = RAW / "israel" / "cbs" / "statistical_areas_2022"
OUT = ROOT / "site" / "data" / "geo" / "il_localities.topo.json"
CFG = ROOT / "configs" / "il2026_v1.toml"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
UA = "brunder-geo-build/1.0 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"
MAX_BYTES = 3_000_000
PAGE = 500


def fetch() -> list[Path]:
    """All statistical areas as GeoJSON pages (WGS84, 5 decimals), one request at a time, cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    pages, offset = [], 0
    while True:
        path = CACHE / f"page_{offset:05d}.geojson"
        if not path.exists():
            q = urllib.parse.urlencode({"where": "1=1", "outFields": "SEMEL_YISHUV,STAT_2022", "outSR": 4326,
                                        "geometryPrecision": 5, "orderByFields": "OBJECTID", "f": "geojson",
                                        "resultOffset": offset, "resultRecordCount": PAGE})
            req = urllib.request.Request(f"{SERVICE}?{q}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                path.write_bytes(r.read())
            time.sleep(2)
        doc = json.loads(path.read_text())
        if not doc["features"]:
            path.unlink()
            break
        pages.append(path)
        offset += PAGE
    return pages


def main() -> None:
    pages = fetch()
    feats = [f for p in pages for f in json.loads(p.read_text())["features"] if f.get("geometry")]
    prior = il.by_locality(il.read_expb(RAW / tomllib.loads(CFG.read_text())["prior"]["file"]))
    voting = {int(c) for c in prior.index[prior["valid"] > 0]}
    keep = [f for f in feats if int(f["properties"]["SEMEL_YISHUV"]) in voting]
    for f in keep:
        f["properties"] = {"unit_id": f"il-{int(f['properties']['SEMEL_YISHUV'])}"}
    print(f"{len(feats)} statistical areas, {len(keep)} in voting localities", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sa.geojson"
        src.write_text(json.dumps({"type": "FeatureCollection", "features": keep}))
        subprocess.run([*MAPSHAPER, "-i", str(src), "name=localities", "-clean",
                        "-dissolve2", "unit_id", "-simplify", "8%", "keep-shapes", "planar",
                        "-o", str(OUT), "format=topojson", "quantization=100000", "id-field=unit_id"], check=True)
    doc = json.loads(OUT.read_text())
    ids = [g["properties"]["unit_id"] for g in doc["objects"]["localities"]["geometries"]]
    assert len(ids) == len(set(ids)), "duplicate unit ids"
    size = OUT.stat().st_size
    print(f"{OUT.relative_to(ROOT)}: {len(ids)} localities, {size / 1e6:.2f} MB")
    if size >= MAX_BYTES:
        raise SystemExit("over the 3 MB budget: simplify more")


if __name__ == "__main__":
    main()
