"""Build site/data/geo/hu_districts.topo.json: the 106 single-member districts (OEVK) of the 2026 Hungarian
parliamentary election, from the official district polygons the National Election Office publishes with the
results site (https://vtr.valasztas.hu/ogy2026/data/04112100/ver/OevkPoligonok.json; "lat lon,lat lon,..."
one ring per district, WGS84). Official communication of a public body: not protected by copyright in Hungary (Act LXXVI of 1999,
s. 1(4)); attribution: Nemzeti Választási Iroda.

Object `districts`; properties unit_id = hu-<county code>-<district no.> (same ids as site/data/hu2026/units.json)
and name. Simplified with mapshaper (pinned as in analysis/geo_build.py); output must stay under 3 MB.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_geo.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from brunder import hungary as hu
from brunder.paths import ROOT

OUT = ROOT / "site" / "data" / "geo" / "hu_districts.topo.json"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
MAX_BYTES = 3_000_000


def ring(s: str) -> list[list[float]]:
    pts = []
    for p in s.split(","):
        lat, lon = (float(x) for x in p.split())
        pts.append([lon, lat])
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def main() -> None:
    year = 2026
    E = hu.ELECTIONS[year]
    poly = hu._list(hu._j(year, f"{E['ver']}/ver/OevkPoligonok.json"))
    names = {hu.unit_id(r["maz"], r["evk"]): r.get("evk_nev_en") or r["evk_nev"]
             for r in hu._list(hu._j(year, f"{E['ver']}/ver/OevkAdatok.json"))}
    feats = []
    for r in poly:
        u = hu.unit_id(r["maz"], r["evk"])
        feats.append({"type": "Feature", "properties": {"unit_id": u, "name": names.get(u, u)},
                      "geometry": {"type": "Polygon", "coordinates": [ring(r["poligon"])]}})
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "districts.json"
        src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([*MAPSHAPER, "-i", str(src), "name=districts", "-clean", "-simplify", "weighted", "8%", "keep-shapes",
                        "-o", str(OUT), "format=topojson", "quantization=100000", "id-field=unit_id"], check=True)
    doc = json.loads(OUT.read_text())
    ids = [g["properties"]["unit_id"] for g in doc["objects"]["districts"]["geometries"]]
    assert len(ids) == len(set(ids)) == hu.SMD_SEATS, f"expected {hu.SMD_SEATS} districts, got {len(ids)}"
    size = OUT.stat().st_size
    print(f"{OUT.relative_to(ROOT)}: {len(ids)} features, {size / 1e6:.2f} MB")
    if size >= MAX_BYTES:
        raise SystemExit("over the 3 MB budget: simplify more")


if __name__ == "__main__":
    main()
