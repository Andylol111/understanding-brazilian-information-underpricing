"""Build the Spain Congress-constituency map (site/data/geo/es_provinces.topo.json, object `provinces`).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/es_geo.py

Spain elects the Congress by province, so the 52 constituencies are the 50 provinces plus Ceuta and Melilla.

Source and licence
------------------
Natural Earth 1:10m Admin 1 - States, Provinces (ne_10m_admin_1_states_provinces), filtered to adm0_a3 = ESP,
https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/
(download: https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip).
Public domain (https://www.naturalearthdata.com/about/terms-of-use/); attribution is not required, but we
credit "Natural Earth" on the map. It has all 52 units, so no Eurostat GISCO / CNIG fallback is needed.
The download is cached under $BRUNDER_DATA/es2026/geo/, never in the repo.

Each feature carries `unit_id` = "es-<2-digit INE province code>" (matched through the ISO 3166-2 code in the
Natural Earth data) and `name`.

Canary Islands inset
--------------------
geo_build.py has no inset convention (Alaska/Hawaii are drawn in place, nz clips the Chathams off), and the
Canaries lie ~1,700 km south-west of Madrid, which would leave a huge empty ocean. So Las Palmas and Santa
Cruz de Tenerife are shifted by (+8.0 deg lon, +5.5 deg lat) with mapshaper, into a box south-west of the
Iberian mainland, off Cadiz/Huelva. They keep their size, so the inset is a translation only, not a rescale.
Because of the shift, their coordinates are NOT real-world positions: the dashboard must not use this file
for anything geographic besides drawing.
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from geo_build import OUT, mapshaper

from brunder.paths import RAW

URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
CACHE = RAW / "es2026" / "geo"
UA = "brunder/0.1 (research; recorder)"
MAX_BYTES = 1_000_000
CANARY_SHIFT = "8,5.5"

# INE province code -> (display name, Natural Earth iso_3166_2)
PROVINCES = {
    "01": ("Araba/Álava", "ES-VI"), "02": ("Albacete", "ES-AB"), "03": ("Alicante/Alacant", "ES-A"),
    "04": ("Almería", "ES-AL"), "05": ("Ávila", "ES-AV"), "06": ("Badajoz", "ES-BA"),
    "07": ("Illes Balears", "ES-PM"), "08": ("Barcelona", "ES-B"), "09": ("Burgos", "ES-BU"),
    "10": ("Cáceres", "ES-CC"), "11": ("Cádiz", "ES-CA"), "12": ("Castellón/Castelló", "ES-CS"),
    "13": ("Ciudad Real", "ES-CR"), "14": ("Córdoba", "ES-CO"), "15": ("A Coruña", "ES-C"),
    "16": ("Cuenca", "ES-CU"), "17": ("Girona", "ES-GI"), "18": ("Granada", "ES-GR"),
    "19": ("Guadalajara", "ES-GU"), "20": ("Gipuzkoa", "ES-SS"), "21": ("Huelva", "ES-H"),
    "22": ("Huesca", "ES-HU"), "23": ("Jaén", "ES-J"), "24": ("León", "ES-LE"), "25": ("Lleida", "ES-L"),
    "26": ("La Rioja", "ES-LO"), "27": ("Lugo", "ES-LU"), "28": ("Madrid", "ES-M"), "29": ("Málaga", "ES-MA"),
    "30": ("Murcia", "ES-MU"), "31": ("Navarra", "ES-NA"), "32": ("Ourense", "ES-OR"),
    "33": ("Asturias", "ES-O"), "34": ("Palencia", "ES-P"), "35": ("Las Palmas", "ES-GC"),
    "36": ("Pontevedra", "ES-PO"), "37": ("Salamanca", "ES-SA"), "38": ("Santa Cruz de Tenerife", "ES-TF"),
    "39": ("Cantabria", "ES-S"), "40": ("Segovia", "ES-SG"), "41": ("Sevilla", "ES-SE"),
    "42": ("Soria", "ES-SO"), "43": ("Tarragona", "ES-T"), "44": ("Teruel", "ES-TE"),
    "45": ("Toledo", "ES-TO"), "46": ("Valencia/València", "ES-V"), "47": ("Valladolid", "ES-VA"),
    "48": ("Bizkaia", "ES-BI"), "49": ("Zamora", "ES-ZA"), "50": ("Zaragoza", "ES-Z"),
    "51": ("Ceuta", "ES-CE"), "52": ("Melilla", "ES-ML"),
}


def fetch() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / URL.rsplit("/", 1)[1]
    if not path.exists():
        req = urllib.request.Request(URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r:
            if r.status != 200:
                raise RuntimeError(f"{URL}: HTTP {r.status}")
            body = r.read()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(body)
        try:
            with zipfile.ZipFile(tmp) as z:
                assert z.testzip() is None
        except Exception:
            tmp.unlink()
            raise
        tmp.rename(path)
    return path


def build(tmp: Path) -> Path:
    with zipfile.ZipFile(fetch()) as z:
        z.extractall(tmp)
    shp = next(tmp.glob("*.shp"))
    codes = {iso: code for code, (_, iso) in PROVINCES.items()}
    names = {code: name for code, (name, _) in PROVINCES.items()}
    out = OUT / "es_provinces.topo.json"
    each = (f"unit_id = 'es-' + {json.dumps(codes)}[iso_3166_2], "
            f"name = {json.dumps(names)}[unit_id.slice(3)]")
    mapshaper("-i", str(shp), "encoding=utf8", "-filter", "adm0_a3 == 'ESP'", "-each", each,
              "-filter-fields", "unit_id,name", "-rename-layers", "provinces",
              "-simplify", "weighted", "25%", "keep-shapes",
              # Canary inset: translate the two Canarian provinces next to the mainland
              "-affine", "where=unit_id == 'es-35' || unit_id == 'es-38'", f"shift={CANARY_SHIFT}",
              "-o", str(out), "format=topojson", "quantization=100000", "id-field=unit_id")
    return out


def check(out: Path) -> None:
    doc = json.loads(out.read_text())
    geoms = doc["objects"]["provinces"]["geometries"]
    ids = [g["properties"]["unit_id"] for g in geoms]
    expected = {f"es-{c}" for c in PROVINCES}
    assert len(ids) == len(set(ids)) == 52, (len(ids), len(set(ids)))
    assert set(ids) == expected, (sorted(set(ids) - expected), sorted(expected - set(ids)))
    assert all(g.get("type") and g["properties"].get("name") for g in geoms)
    assert out.stat().st_size <= MAX_BYTES, out.stat().st_size
    print(f"{out}: {len(ids)} provinces, {out.stat().st_size / 1e6:.2f} MB")


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        check(build(Path(t)))


if __name__ == "__main__":
    main()
