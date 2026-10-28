"""Build ``site/data/geo/nz_electorates.topo.json`` from official Stats NZ boundaries (CC BY 4.0).

The 2025 electorate polygons extend to the 12-nautical-mile limit. For a readable dashboard coastline,
they are intersected with Stats NZ's official land classes from Urban Rural 2026, the successor to its
Urban Rural 2025 Clipped layer 120964. Chatham Islands are excluded rather than distorting the main-islands map.

    uv run python analysis/nz_geo.py
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

from brunder import newzealand as nz
from brunder.paths import ROOT

GEN = ("https://services2.arcgis.com/vKb0s8tBIA3bdocZ/ArcGIS/rest/services/"
       "General_Electorates_2025/FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson")
MAORI = ("https://services2.arcgis.com/vKb0s8tBIA3bdocZ/ArcGIS/rest/services/"
         "M%C4%81ori_Electorates_2025/FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson")
LAND = ("https://services2.arcgis.com/vKb0s8tBIA3bdocZ/ArcGIS/rest/services/"
        "Urban_Rural_Areas_2026/FeatureServer/0/query?where=IUR2026_V1_00%3C30"
        "&outFields=IUR2026_V1_00&returnGeometry=true&outSR=4326&f=geojson")
CACHE = Path.home() / ".cache" / "brunder-geo"
OUT = ROOT / "site" / "data" / "geo" / "nz_electorates.topo.json"
UA = {"User-Agent": "brunder-research/0.1 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}


def cached(url: str, name: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists():
        r = httpx.get(url, headers=UA, timeout=120, follow_redirects=True); r.raise_for_status()
        p.write_bytes(r.content)
    return p


def main() -> None:
    gen = json.loads(cached(GEN, "nz_general_electorates_2025.geojson").read_text())
    mao = json.loads(cached(MAORI, "nz_maori_electorates_2025.geojson").read_text())
    land_path = cached(LAND, "nz_land_urban_rural_2026.geojson")
    _, elect, _ = nz._fetch_references()
    by_name = {r["name"]: (r["unit_id"], r["region"]) for _, r in elect.iterrows()}
    features = []
    for doc, name_field, layer in ((gen, "GED2025_V1_00_NAME", "general"),
                                    (mao, "MED2025_V1_00_NAME", "maori")):
        for f in doc["features"]:
            name = f["properties"][name_field]
            if name not in by_name:
                raise ValueError(f"boundary name absent from XML feed: {name!r}")
            f["properties"] = {"unit_id": by_name[name][0], "name": name, "layer": layer}
            features.append(f)
    if len(features) != 71:
        raise ValueError(f"expected 71 electorates, got {len(features)}")
    work = CACHE / "electorates.geojson"
    work.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    exe = os.environ.get("MAPSHAPER")
    mapshaper = [exe] if exe else ["npx", "--yes", "mapshaper@0.6.121"]
    # General and Māori electorates overlap. Clipping them together lets mapshaper resolve the overlap and
    # drops most of Te Tai Tonga, so each complete national layer is clipped on its own.
    clipped = {}
    for layer in ("general", "maori"):
        clipped[layer] = CACHE / f"nz_{layer}_clipped.geojson"
        subprocess.run(mapshaper + [
            "-i", str(work), str(land_path), "combine-files",
            "-filter", f'layer=="{layer}"', "target=electorates",
            "-dissolve", "target=nz_land_urban_rural_2026",
            "-clip", "nz_land_urban_rural_2026", "target=electorates",
            "-clip", "bbox=166,-48,179,-33", "target=electorates",
            "-o", "target=electorates", "format=geojson", str(clipped[layer])], check=True)
    areas = {k: _area(json.loads(p.read_text())) for k, p in clipped.items()}
    if abs(areas["general"] - areas["maori"]) > .02 * areas["general"]:
        raise ValueError(f"general and Māori layers cover different land areas: {areas}")
    subprocess.run(mapshaper + [
        "-i", str(clipped["general"]), str(clipped["maori"]), "combine-files",
        "-merge-layers", "target=*", "name=electorates", "force",
        "-filter-fields", "unit_id,name,layer",
        "-simplify", "weighted", "3%", "keep-shapes",
        "-o", "target=electorates", "format=topojson", "quantization=100000",
        "id-field=unit_id", str(OUT)], check=True)
    doc = json.loads(OUT.read_text())
    obj = next(iter(doc["objects"].values()))
    ids = [g["properties"]["unit_id"] for g in obj["geometries"]]
    if len(ids) != 71 or len(set(ids)) != 71 or OUT.stat().st_size >= 1_500_000:
        raise ValueError(f"bad output: {len(ids)} features, {OUT.stat().st_size} bytes")
    print(f"{OUT}: {len(ids)} clipped electorates, {OUT.stat().st_size:,} bytes; "
          f"layer areas {areas['general']:.3f}/{areas['maori']:.3f} deg²; Stats NZ CC BY 4.0")


def _area(doc: dict) -> float:
    """Planar lon/lat area, used only to prove both overlapping layers cover the same land."""
    def ring(r):
        return abs(sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1]
                       for i in range(len(r)))) / 2
    total = 0.0
    for f in doc["features"]:
        g = f.get("geometry")
        if not g:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        total += sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in polys)
    return total


if __name__ == "__main__":
    main()
