"""Build site/data/geo/il_localities.topo.json for the il2026 map: one GeometryCollection "localities" with
  - a neutral land base (properties base=true, layer "base"): the union of the region polygons;
  - regions (layer "regions", unit_id "il-sd<code>"): CBS sub-districts (נפות למפקד 2008, 15 polygons) plus the
    CBS Judea and Samaria area as one region (code 7); full land coverage incl. the Negev;
  - localities (layer "localities", unit_id "il-<locality code>"): CBS statistical areas 2022 dissolved by
    locality code; built-up areas only. Areas whose code has no 2022 ballot box (regional-council open land) are
    dropped.

Sources: Central Bureau of Statistics (CBS) feature services of the CBS ArcGIS organisation (owner ISRAEL_CBS_GIS):
statistical areas 2022 (item 372606908bcf49a69adaebc24fefc04a), sub-districts for the 2008 census (item
f0b8dd3d7cde4e999e34c93219c53aed), Judea and Samaria area (item 681b9d6076cf47eba38ce52c0f159bf3). Used under the CBS
open licence (https://www.cbs.gov.il/he/Pages/רישיון-שימוש.aspx): copying, redistribution and derivative works
allowed, commercial or not, with the attribution given in site/data/il2026/meta.json.

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

ORG = "https://services2.arcgis.com/xMRYm7cNgdR5RN6F/arcgis/rest/services"
SA = f"{ORG}/Statistical__Areas_2022/FeatureServer/0/query"
SUBDISTRICTS = f"{ORG}/SubDistrict_for_Census_2008/FeatureServer/0/query"
JUDEA_SAMARIA = f"{ORG}/Judea_Samaria/FeatureServer/0/query"
CACHE = RAW / "israel" / "cbs"
OUT = ROOT / "site" / "data" / "geo" / "il_localities.topo.json"
CFG = ROOT / "configs" / "il2026_v1.toml"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
UA = "brunder-geo-build/1.0 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"
MAX_BYTES = 2_000_000
PAGE = 500


def fetch(url: str, fields: str, cache: Path) -> list[dict]:
    """All features of a CBS layer as GeoJSON (WGS84, 5 decimals), one page request at a time, cached."""
    cache.mkdir(parents=True, exist_ok=True)
    feats, offset = [], 0
    while True:
        path = cache / f"page_{offset:05d}.geojson"
        if not path.exists():
            q = urllib.parse.urlencode({"where": "1=1", "outFields": fields, "outSR": 4326, "geometryPrecision": 5,
                                        "orderByFields": "OBJECTID", "f": "geojson",
                                        "resultOffset": offset, "resultRecordCount": PAGE})
            req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                path.write_bytes(r.read())
            time.sleep(2)
        page = [f for f in json.loads(path.read_text())["features"] if f.get("geometry")]
        if not page:
            path.unlink()
            return feats
        feats += page
        offset += PAGE


def mapshaper(*args: str) -> None:
    subprocess.run([*MAPSHAPER, *args], check=True)


def collection(path: Path, feats: list[dict]) -> Path:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return path


def main() -> None:
    prior = il.by_locality(il.read_expb(RAW / tomllib.loads(CFG.read_text())["prior"]["file"]))
    voting = {int(c) for c in prior.index[prior["valid"] > 0]}
    sa = fetch(SA, "SEMEL_YISHUV,STAT_2022", CACHE / "statistical_areas_2022")
    loc = [{"type": "Feature", "geometry": f["geometry"],
            "properties": {"unit_id": f"il-{int(f['properties']['SEMEL_YISHUV'])}", "layer": "localities"}}
           for f in sa if int(f["properties"]["SEMEL_YISHUV"]) in voting]
    sd = fetch(SUBDISTRICTS, "CODE", CACHE / "subdistricts_2008") + fetch(JUDEA_SAMARIA, "CODE", CACHE / "judea_samaria")
    reg = [{"type": "Feature", "geometry": f["geometry"],
            "properties": {"unit_id": f"il-sd{int(f['properties']['CODE'])}", "layer": "regions"}} for f in sd]
    print(f"{len(sa)} statistical areas, {len(loc)} in voting localities; {len(reg)} regions", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        mapshaper("-i", str(collection(t / "loc_in.json", loc)), "-clean", "-dissolve2", "unit_id", "copy-fields=layer",
                  "-simplify", "8%", "keep-shapes", "planar", "-o", str(t / "loc.json"), "format=geojson")
        mapshaper("-i", str(collection(t / "reg_in.json", reg)), "-clean", "-dissolve2", "unit_id", "copy-fields=layer",
                  "-simplify", "5%", "keep-shapes", "planar", "-o", str(t / "reg.json"), "format=geojson")
        mapshaper("-i", str(t / "reg.json"), "-dissolve2", "-each", "base=true, layer='base'",
                  "-o", str(t / "base.json"), "format=geojson")
        feats = [f for n in ("base", "reg", "loc") for f in json.loads((t / f"{n}.json").read_text())["features"]]
        mapshaper("-i", str(collection(t / "all.json", feats)), "name=localities",
                  "-o", str(OUT), "format=topojson", "quantization=100000")
    doc = json.loads(OUT.read_text())
    geoms = doc["objects"]["localities"]["geometries"]
    ids = [g["properties"]["unit_id"] for g in geoms if not g["properties"].get("base")]
    assert len(ids) == len(set(ids)), "duplicate unit ids"
    assert all(g.get("type") for g in geoms), "a feature lost its geometry"
    size = OUT.stat().st_size
    n_reg = sum(1 for g in geoms if g["properties"]["layer"] == "regions")
    print(f"{OUT.relative_to(ROOT)}: 1 base, {n_reg} regions, {len(ids) - n_reg} localities, {size / 1e6:.2f} MB")
    if size >= MAX_BYTES:
        raise SystemExit("over the 2 MB budget: simplify more")


if __name__ == "__main__":
    main()
