"""Build the Netherlands map files: site/data/geo/nl_gemeenten.topo.json (the 342 gemeenten of 2025, object
`gemeenten`, unit_id = nl-<province>-<CBS code>) and site/data/geo/nl_regions.topo.json (the 12 provinces).

Source: CBS / Kadaster "Gebiedsindelingen 2025", layer gemeente_gegeneraliseerd (land only, generalised), PDOK WFS
https://service.pdok.nl/cbs/gebiedsindelingen/2025/wfs/v1_0, licence CC BY 4.0 (attribution: CBS, Kadaster). The province of
each gemeente comes from the Kiesraad 2025 results file. Bonaire, Sint Eustatius, Saba and the Hague's "kiezers
buitenland" (NBSB) vote but have no polygon here.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_geo.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from brunder import netherlands as nl
from brunder.paths import RAW, ROOT

OUT = ROOT / "site" / "data" / "geo"
NLRAW = RAW / "netherlands"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
UA = "brunder/0.1 (research; recorder)"
WFS = "https://service.pdok.nl/cbs/gebiedsindelingen/2025/wfs/v1_0"
MAX_BYTES = 3_000_000


def fetch_geojson() -> Path:
    p = NLRAW / "gemeente_2025.geojson"
    if not p.exists():
        q = urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                                    "typeName": "gebiedsindelingen:gemeente_gegeneraliseerd",
                                    "outputFormat": "application/json", "srsName": "EPSG:4326", "count": 500})
        with urllib.request.urlopen(urllib.request.Request(f"{WFS}?{q}", headers={"User-Agent": UA}), timeout=120) as r:
            p.write_bytes(r.read())
        time.sleep(1)
    return p


def main() -> None:
    _, _, info = nl.read_uitslag(NLRAW / "x25" / "TK2025_uitslag.csv")
    geo = json.loads(fetch_geojson().read_text())
    feats = []
    for f in geo["features"]:
        code = int(f["properties"]["statcode"][2:])
        row = info.loc[f"G{code:04d}"]
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"unit_id": nl.unit_id(row["province"], code), "name": row["name"],
                                     "region": nl.PROVINCES[row["province"]][0]}})
    assert len(feats) == 342 and len({f["properties"]["unit_id"] for f in feats}) == 342
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "gemeenten.geojson"
        src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        units = OUT / "nl_gemeenten.topo.json"
        subprocess.run([*MAPSHAPER, "-i", str(src), "name=gemeenten", "-simplify", "weighted", "12%", "keep-shapes",
                        "-filter-fields", "unit_id,name", "-o", str(units), "format=topojson", "quantization=100000",
                        "id-field=unit_id"], check=True)
        regions = OUT / "nl_regions.topo.json"
        subprocess.run([*MAPSHAPER, "-i", str(src), "name=gemeenten", "-dissolve", "region", "-rename-layers", "regions",
                        "-simplify", "weighted", "5%", "keep-shapes", "-o", str(regions), "format=topojson",
                        "quantization=100000", "id-field=region"], check=True)
    for p, obj, n in ((units, "gemeenten", 342), (regions, "regions", 12)):
        doc = json.loads(p.read_text())
        geoms = doc["objects"][obj]["geometries"]
        assert len(geoms) == n and all(g.get("type") for g in geoms), (p, len(geoms))
        print(f"{p.relative_to(ROOT)}: {len(geoms)} features, {p.stat().st_size / 1e3:.0f} kB")
        if p.stat().st_size >= MAX_BYTES:
            raise SystemExit(f"{p.name} over 3 MB: simplify more")


if __name__ == "__main__":
    main()
