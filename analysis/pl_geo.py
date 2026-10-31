"""Build site/data/geo/pl_voivodeships.topo.json: the 16 voivodeships of Poland, from Natural Earth 1:10m
admin-1 (public domain, https://www.naturalearthdata.com/about/terms-of-use/), filtered to iso_a2 == PL and simplified with
mapshaper (pinned as in analysis/geo_build.py). Object `voivodeships`; unit_id = pl-<TERYT voivodeship code>
(02 ... 32, the same ids as site/data/pl2025r*/units.json) and name = the Polish name.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_geo.py
"""

from __future__ import annotations

import json
import subprocess
import zipfile

import httpx

from brunder import poland as pl
from brunder.paths import ROOT

OUT = ROOT / "site" / "data" / "geo" / "pl_voivodeships.topo.json"
NE = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
ISO = {"PL-DS": "02", "PL-KP": "04", "PL-LU": "06", "PL-LB": "08", "PL-LD": "10", "PL-MA": "12", "PL-MZ": "14",
       "PL-OP": "16", "PL-PK": "18", "PL-PD": "20", "PL-PM": "22", "PL-SL": "24", "PL-SK": "26", "PL-WN": "28",
       "PL-WP": "30", "PL-ZP": "32"}


def main() -> None:
    cache = pl.RAW_PL / "geo"
    cache.mkdir(parents=True, exist_ok=True)
    zf = cache / "ne_admin1.zip"
    if not zf.exists():
        zf.write_bytes(httpx.get(NE, headers={"User-Agent": "brunder/0.1 (research; recorder)"}, timeout=300,
                                 follow_redirects=True).content)
    shp = cache / "ne_admin1"
    if not shp.exists():
        zipfile.ZipFile(zf).extractall(shp)
    names = {f"pl-{c}": n for c, n in pl.VOIVODESHIPS.items()}
    js = ("const m=" + json.dumps({k: f"pl-{v}" for k, v in ISO.items()}, separators=(",", ":")) + ";"
          "const n=" + json.dumps(names, ensure_ascii=False, separators=(",", ":")) + ";"
          "unit_id=m[iso_3166_2]; name=n[unit_id]")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*MAPSHAPER, "-i", str(next(shp.glob("*.shp"))), "-filter", 'iso_a2=="PL"', "-each", js,
                    "-filter-fields", "unit_id,name", "-rename-layers", "voivodeships", "-clean", "-simplify", "weighted", "25%",
                    "keep-shapes", "-o", str(OUT), "format=topojson", "quantization=100000", "id-field=unit_id"], check=True)
    doc = json.loads(OUT.read_text())
    ids = sorted(g["properties"]["unit_id"] for g in doc["objects"]["voivodeships"]["geometries"])
    assert ids == sorted(names), ids
    print(f"{OUT.relative_to(ROOT)}: {len(ids)} features, {OUT.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
