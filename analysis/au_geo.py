"""Build ``site/data/geo/au_divisions.topo.json`` (+ ``au_regions.topo.json``) for the 2025 Australian federal election.

Source: Geoscience Australia's Digital Atlas of Australia copy of the AEC "Federal Electoral Divisions (March 2025)"
layer (reflects the 2024 NSW/VIC/WA and 2025 NT redistributions, i.e. the 150 divisions of the 2025 election).
Licence: Creative Commons Attribution 4.0 (item licenceInfo); (c) Commonwealth of Australia (Australian Electoral
Commission). The AEC's own download sits behind a click-through licence form, so it is not scraped.

Each division is joined by normalised name to the AEC media feed's PollingDistrictIdentifier (id + state); the build
fails if any of the 150 feed divisions lacks a geometry or any geometry lacks a feed id.

    BRUNDER_DATA=/Users/andreh./brunder-data-local uv run python analysis/au_geo.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from brunder.paths import ROOT

LAYER = ("https://services-ap1.arcgis.com/ypkPEy1AmwPKGNNv/ArcGIS/rest/services/"
         "Federal_Electoral_Boundaries_2025/FeatureServer/0")
DATA = Path(os.environ.get("BRUNDER_DATA", Path.home() / "brunder-data-local"))
PRELOAD = DATA / "au" / "31496" / "Detailed-Preload"
STATES = {"NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA": "Western Australia",
          "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory",
          "NT": "Northern Territory"}
RAW = DATA / "raw" / "australia" / "geo"
OUT = ROOT / "site" / "data" / "geo" / "au_divisions.topo.json"
OUT_REG = ROOT / "site" / "data" / "geo" / "au_regions.topo.json"
UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
NS = {"m": "http://www.aec.gov.au/xml/schema/mediafeed"}
N = 150
MAX_BYTES = 1_000_000


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def download() -> Path:
    path = RAW / "federal_electoral_divisions_2025.geojson"
    if path.exists():
        return path
    RAW.mkdir(parents=True, exist_ok=True)
    feats: list[dict] = []
    while True:  # <= 1 request/s; the layer holds 150 features, 1000 per page
        r = httpx.get(f"{LAYER}/query", headers=UA, timeout=180, params={
            "where": "1=1", "outFields": "elect_div,e_div_numb", "returnGeometry": "true", "outSR": "4326",
            "f": "geojson", "resultOffset": len(feats), "resultRecordCount": 1000, "orderByFields": "objectid"})
        r.raise_for_status()
        page = r.json()
        if page.get("type") != "FeatureCollection" or "error" in page:
            raise ValueError(f"unexpected response: {str(page)[:200]}")
        feats += page["features"]
        if not page["features"] or len(feats) >= N:
            break
        time.sleep(1)
    if len(feats) != N:
        raise ValueError(f"expected {N} divisions, got {len(feats)}")
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    return path


# Outlying islands are left off the map, so they do not stretch the fitted view (as NZ leaves off the Chatham Islands):
# Christmas and Cocos (Keeling) Islands (Lingiari), Norfolk Island (Bean), Lord Howe Island (NSW), Macquarie and Heard
# Islands, and the far northern Torres Strait islands (Leichhardt). Every division keeps its mainland or main-island
# polygons and its results; only these polygons are dropped (about 40 of ~600 rings).
LON, LAT = (112.5, 154.0), (-44.0, -10.4)


def keep_polygon(poly: list) -> bool:
    ring = poly[0]
    lon = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    return LON[0] <= lon <= LON[1] and LAT[0] <= lat <= LAT[1]


def drop_far_islands(geom: dict) -> dict:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    kept = [p for p in polys if keep_polygon(p)]
    if not kept:
        raise ValueError("every polygon of a division is outside the map window")
    return {"type": "MultiPolygon", "coordinates": kept}


LAND_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"


def land_json(ms: list[str]) -> Path:
    """Natural Earth 1:10m land (public domain, https://www.naturalearthdata.com/about/terms-of-use/), cut to the
    Australian window and lightly simplified: the coastline the divisions are clipped to."""
    out = RAW / "ne_10m_land_au.json"
    if out.exists():
        return out
    import io
    import zipfile
    RAW.mkdir(parents=True, exist_ok=True)
    r = httpx.get(LAND_URL, headers=UA, timeout=180, follow_redirects=True)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        if z.testzip() is not None:
            raise ValueError("bad Natural Earth zip")
        z.extractall(RAW / "ne_10m_land")
    subprocess.run(ms + ["-i", str(RAW / "ne_10m_land" / "ne_10m_land.shp"), "name=land", "-clip", "bbox=105,-45,160,-8",
                         "-simplify", "weighted", "20%", "keep-shapes", "-o", "format=geojson", str(out)], check=True)
    return out


def feed_ids() -> dict[str, tuple[int, str, str, str]]:
    from brunder import australia as au
    zips = sorted(PRELOAD.glob("*.zip"))
    if not zips:
        raise FileNotFoundError(f"no Preload zip in {PRELOAD}")
    z = zips[-1]
    divs = au.parse_preload(au.read_zip(z))["divs"]
    dem = au.parse_districts(au.read_zip(z, "pollingdistricts"))
    out = {norm(d["name"]): (int(i), d["name"], d["state"], au.map_region(d["state"], dem.get(int(i), ""), d["name"]))
           for i, d in divs.items()}
    if len(out) != N:
        raise ValueError(f"expected {N} feed divisions, got {len(out)}")
    return out


def main() -> None:
    ids = feed_ids()
    doc = json.loads(download().read_text())
    seen = set()
    for f in doc["features"]:
        key = norm(f["properties"]["elect_div"])
        if key not in ids:
            raise ValueError(f"boundary division absent from AEC feed: {f['properties']['elect_div']!r}")
        if not f.get("geometry") or not f["geometry"].get("coordinates"):
            raise ValueError(f"no geometry: {key}")
        aec_id, name, state, region = ids[key]
        f["geometry"] = drop_far_islands(f["geometry"])
        f["properties"] = {"unit_id": f"au-{region}-{aec_id}", "aec_id": aec_id, "name": name, "state": state, "region": region}
        seen.add(key)
    if seen != set(ids):
        raise ValueError(f"feed divisions without geometry: {sorted(set(ids) - seen)}")
    work = RAW / "au_divisions_joined.geojson"
    work.write_text(json.dumps(doc, ensure_ascii=False))

    exe = os.environ.get("MAPSHAPER")
    ms = [exe] if exe else ["npx", "--yes", "mapshaper@0.6.121"]
    # Topology-preserving, area-weighted; keep-shapes retains every ring of every division (Tasmania, islands).
    # The source's state borders are separate copies that differ by about 0.001 degree, which leaves hairline gaps and
    # overlaps between states: -clean (500 km2 threshold) closes them so adjacent divisions share their border arcs.
    subprocess.run(ms + ["-i", str(work), "name=divisions", "-simplify", "weighted", "4%", "keep-shapes",
                         "-o", "format=topojson", "quantization=50000", "id-field=unit_id", str(OUT)], check=True)
    subprocess.run(ms + ["-i", str(OUT), "name=divisions", "-clean", "gap-fill-area=5e8", "sliver-control=0",
                         "-o", "format=topojson", "quantization=50000", "id-field=unit_id", str(OUT)], check=True)
    doc = json.loads(OUT.read_text())
    for g in doc["objects"]["divisions"]["geometries"]:
        g["properties"].pop("FID", None)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    # Electoral divisions run out over the sea (the Gulf of Carpentaria, Torres Strait, Moreton Bay...): clip every division to
    # the land polygon (Natural Earth 10 m land, public domain) and clean again so the shared arcs stay shared.
    land = land_json(ms)
    subprocess.run(ms + ["-i", str(OUT), "name=divisions", "-clip", str(land), "-clean", "gap-fill-area=5e8",
                         "sliver-control=0", "-o", "format=topojson", "quantization=50000", "id-field=unit_id", str(OUT)],
                   check=True)
    doc = json.loads(OUT.read_text())
    for g in doc["objects"]["divisions"]["geometries"]:
        g["properties"].pop("FID", None)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    # The regions are dissolved from the cleaned divisions topology, so region borders coincide with division borders
    # and with each other (no gaps between regions on the maps).
    subprocess.run(ms + ["-i", str(OUT), "name=divisions", "-dissolve", "region", "-simplify", "weighted", "25%",
                         "keep-shapes", "-rename-layers", "regions", "-o", "format=topojson", "quantization=50000",
                         "id-field=region", str(OUT_REG)], check=True)

    from brunder import australia as au
    reg = json.loads(OUT_REG.read_text())
    for g in reg["objects"]["regions"]["geometries"]:
        code = g["properties"]["region"]
        g["id"] = code
        g["properties"] = {"region": code, "name": au.REGION_NAMES[code]}
    OUT_REG.write_text(json.dumps(reg, separators=(",", ":")))

    topo = json.loads(OUT.read_text())
    geoms = topo["objects"]["divisions"]["geometries"]
    got = {g["properties"]["unit_id"] for g in geoms}
    if len(geoms) != N or got != {f"au-{v[3]}-{v[0]}" for v in ids.values()} \
            or any(g["id"] != g["properties"]["unit_id"] for g in geoms) or not all(g.get("arcs") for g in geoms):
        raise ValueError("bad output: divisions missing or emptied by simplification")
    if OUT.stat().st_size >= MAX_BYTES:
        raise ValueError(f"{OUT.stat().st_size} bytes exceeds {MAX_BYTES}")
    print(f"{OUT}: {len(geoms)}/{N} divisions matched to feed ids, {OUT.stat().st_size:,} bytes; "
          f"{OUT_REG}: {OUT_REG.stat().st_size:,} bytes; CC BY 4.0, (c) Commonwealth of Australia (AEC)")


if __name__ == "__main__":
    main()
