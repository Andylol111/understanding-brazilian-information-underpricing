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


def feed_ids() -> dict[str, tuple[int, str, str]]:
    from brunder import australia as au
    zips = sorted(PRELOAD.glob("*.zip"))
    if not zips:
        raise FileNotFoundError(f"no Preload zip in {PRELOAD}")
    divs = au.parse_preload(au.read_zip(zips[-1]))["divs"]
    out = {norm(d["name"]): (int(i), d["name"], d["state"]) for i, d in divs.items()}
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
        aec_id, name, state = ids[key]
        f["properties"] = {"unit_id": f"au-{state.lower()}-{aec_id}", "aec_id": aec_id, "name": name, "state": state}
        seen.add(key)
    if seen != set(ids):
        raise ValueError(f"feed divisions without geometry: {sorted(set(ids) - seen)}")
    work = RAW / "au_divisions_joined.geojson"
    work.write_text(json.dumps(doc, ensure_ascii=False))

    exe = os.environ.get("MAPSHAPER")
    ms = [exe] if exe else ["npx", "--yes", "mapshaper@0.6.121"]
    # Topology-preserving, area-weighted; keep-shapes retains every ring of every division (Tasmania, islands).
    subprocess.run(ms + ["-i", str(work), "name=divisions", "-simplify", "weighted", "4%", "keep-shapes",
                         "-o", "format=topojson", "quantization=50000", "id-field=unit_id", str(OUT)], check=True)
    subprocess.run(ms + ["-i", str(work), "name=regions", "-dissolve", "state", "-simplify", "weighted", "0.5%",
                         "keep-shapes", "-o", "format=topojson", "quantization=50000", "id-field=state",
                         str(OUT_REG)], check=True)

    reg = json.loads(OUT_REG.read_text())
    for g in reg["objects"]["regions"]["geometries"]:
        code = g["properties"]["state"]
        g["id"] = code.lower()
        g["properties"] = {"region": code.lower(), "name": STATES[code]}
    OUT_REG.write_text(json.dumps(reg, separators=(",", ":")))

    topo = json.loads(OUT.read_text())
    geoms = topo["objects"]["divisions"]["geometries"]
    got = {g["properties"]["unit_id"] for g in geoms}
    if len(geoms) != N or got != {f"au-{v[2].lower()}-{v[0]}" for v in ids.values()} \
            or any(g["id"] != g["properties"]["unit_id"] for g in geoms) or not all(g.get("arcs") for g in geoms):
        raise ValueError("bad output: divisions missing or emptied by simplification")
    if OUT.stat().st_size >= MAX_BYTES:
        raise ValueError(f"{OUT.stat().st_size} bytes exceeds {MAX_BYTES}")
    print(f"{OUT}: {len(geoms)}/{N} divisions matched to feed ids, {OUT.stat().st_size:,} bytes; "
          f"{OUT_REG}: {OUT_REG.stat().st_size:,} bytes; CC BY 4.0, (c) Commonwealth of Australia (AEC)")


if __name__ == "__main__":
    main()
