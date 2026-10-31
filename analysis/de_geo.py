"""Build site/data/geo/de_wahlkreise.topo.json: the 299 Wahlkreise of the 2025 Bundestag election, from the
Bundeswahlleiterin's generalised constituency geometry (btw25_geometrie_wahlkreise_shp_geo.zip, WGS84).

Licence: (c) Die Bundeswahlleiterin, Statistisches Bundesamt, Wiesbaden 2024, Wahlkreiskarte fuer die Wahl zum
21. Deutschen Bundestag; Grundlage der Geoinformationen (c) Geobasis-DE / BKG 2024; Datenlizenz Deutschland -
Namensnennung 2.0 (https://www.govdata.de/dl-de/by-2-0).

Object `wahlkreise`; properties unit_id = de-<land>-<Wahlkreis number> (same ids as site/data/de2025/units.json) and
name. Simplified with mapshaper (pinned as in analysis/geo_build.py); output must stay under 3 MB.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_geo.py
"""

from __future__ import annotations

import json
import subprocess
import zipfile

import httpx

from brunder.germany import LANDS, RAW_DE
from brunder.paths import ROOT

URL = ("https://www.bundeswahlleiterin.de/dam/jcr/556bec9c-be80-4818-a368-fe6596f15f08/"
       "btw25_geometrie_wahlkreise_shp_geo.zip")
OUT = ROOT / "site" / "data" / "geo" / "de_wahlkreise.topo.json"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
MAX_BYTES = 3_000_000


def main() -> None:
    d = RAW_DE / "geo"
    d.mkdir(parents=True, exist_ok=True)
    z = d / "btw25_geometrie_wahlkreise_shp_geo.zip"
    if not z.exists():
        r = httpx.get(URL, headers={"User-Agent": "brunder/0.1 (research; recorder)"}, timeout=120)
        r.raise_for_status()
        z.write_bytes(r.content)
    zipfile.ZipFile(z).extractall(d)
    land = json.dumps({f"{k:02d}": v.lower() for k, v in LANDS.items()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*MAPSHAPER, "-i", str(d / "btw25_geometrie_wahlkreise_shp_geo.shp"), "-each",
                    f"unit_id='de-'+({land})[LAND_NR]+'-'+WKR_NR, name=WKR_NAME", "-filter-fields", "unit_id,name",
                    "-simplify", "weighted", "12%", "keep-shapes", "-rename-layers", "wahlkreise",
                    "-o", "format=topojson", "quantization=100000", str(OUT)], check=True, stdout=subprocess.DEVNULL)
    ids = [g["properties"]["unit_id"] for g in json.loads(OUT.read_text())["objects"]["wahlkreise"]["geometries"]]
    assert len(ids) == len(set(ids)) == 299, len(ids)
    print(f"{OUT.relative_to(ROOT)}: {len(ids)} features, {OUT.stat().st_size / 1e6:.2f} MB")
    if OUT.stat().st_size >= MAX_BYTES:
        raise SystemExit("over the 3 MB budget: simplify more")


if __name__ == "__main__":
    main()
