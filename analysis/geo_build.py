"""Build the simplified TopoJSON boundary files the dashboard maps draw (site/data/geo/).

    python3 analysis/geo_build.py            # both files
    python3 analysis/geo_build.py us br      # pick
    python3 analysis/geo_build.py --cache /some/dir

Needs Node (for `npx mapshaper`, pinned below), which does the topology-preserving simplification
and the TopoJSON encoding; everything else is the standard library. Every feature carries
`unit_id`, the same id the exporters write into `<eid>/units.json` (see the dashboard data
contract), and `name`. Downloads are cached (default ~/.cache/brunder-geo) and never committed;
only the simplified outputs are. Each output must stay under 3 MB.

Sources and licences
--------------------
US: `geo/us_counties.topo.json`, object `counties`
  * U.S. Census Bureau, 2024 Cartographic Boundary Files, counties, 1:5,000,000
    https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_5m.zip
  * U.S. Census Bureau, 2024 Cartographic Boundary Files, Alaska state legislative districts
    (lower chamber), 1:500,000
    https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_02_sldl_500k.zip
  Works of the U.S. federal government: public domain (17 U.S.C. 105).
  50 states + DC; territories dropped. unit_id = `us-<state postal code, lower>-<5-digit county GEOID>`
  (e.g. us-ca-06001). Alaska reports results by state house district, not by borough, so its
  boroughs are replaced by the 40 house districts: unit_id = `us-ak-hd01` ... `us-ak-hd40`.

Brazil: `geo/br_municipalities.topo.json`, object `municipalities`
  * IBGE, Malha Municipal Digital 2025 (all municipalities)
    https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2025/Brasil/BR_Municipios_2025.zip
    Public files published by IBGE ("Todos os arquivos aqui disponíveis são públicos") under the
    federal open-data policy (Decreto 8.777/2016); reuse with attribution to IBGE.
  * Tribunal Superior Eleitoral, "Códigos oficiais de UF e municípios segundo o TSE e o IBGE",
    Portal de Dados Abertos do TSE, licence CC-BY
    https://dadosabertos.tse.jus.br/dataset/codigos-oficiais-de-uf-e-municipios-segundo-o-tse-e-o-ibge
    https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip
  The IBGE mesh is keyed by the 7-digit IBGE code; election results are keyed by the 5-digit TSE
  code. The TSE table maps one to the other. unit_id = `br-<uf, lower>-<TSE code, 5 digits>`
  (e.g. São Paulo, IBGE 3550308 -> br-sp-71072). Lagoa dos Patos and Lagoa Mirim (IBGE water
  polygons without a municipality) are dropped.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "data" / "geo"
MAX_BYTES = 3_000_000
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
UA = "brunder-geo-build/1.0 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"

US_COUNTIES = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_5m.zip"
AK_HOUSE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_02_sldl_500k.zip"
IBGE_MESH = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/"
             "municipio_2025/Brasil/BR_Municipios_2025.zip")
TSE_IBGE = "https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip"

# 50 states + DC by Census state FIPS (01-56 minus the unused codes); territories are not mapped.
US_STATE_FIPS = sorted({f"{i:02d}" for i in range(1, 57)} - {"03", "07", "14", "43", "52"})


def fetch(url: str, cache: Path) -> Path:
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"downloading {url}", file=sys.stderr)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=600) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.rename(path)
    return path


def unzip_shp(path: Path, into: Path) -> Path:
    with zipfile.ZipFile(path) as z:
        z.extractall(into)
        return into / next(n for n in z.namelist() if n.endswith(".shp"))


def mapshaper(*args: str) -> None:
    subprocess.run([*MAPSHAPER, *args], check=True)


def check(out: Path, obj: str) -> None:
    doc = json.loads(out.read_text())
    geoms = doc["objects"][obj]["geometries"]
    ids = [g["properties"]["unit_id"] for g in geoms]
    assert len(ids) == len(set(ids)), "duplicate unit ids"
    assert all(g.get("type") for g in geoms), "a feature lost its geometry in simplification"
    size = out.stat().st_size
    print(f"{out.relative_to(ROOT)}: {len(ids)} features, {size / 1e6:.2f} MB")
    if size >= MAX_BYTES:
        raise SystemExit(f"{out.name} is {size / 1e6:.2f} MB, over the 3 MB budget: simplify more")


def build_us(cache: Path, tmp: Path) -> None:
    counties = unzip_shp(fetch(US_COUNTIES, cache), tmp / "us")
    house = unzip_shp(fetch(AK_HOUSE, cache), tmp / "ak")
    out = OUT / "us_counties.topo.json"
    states = json.dumps(US_STATE_FIPS)
    mapshaper(
        "-i", str(counties), str(house), "combine-files", "encoding=utf8",
        # counties: 50 states + DC, Alaska boroughs out; house districts: drop "ZZZ" (water)
        "-filter", f"{states}.indexOf(STATEFP) > -1 && STUSPS != 'AK'", f"target={counties.stem}",
        "-each", "unit_id='us-' + STUSPS.toLowerCase() + '-' + GEOID, name=NAMELSAD + ', ' + STUSPS", f"target={counties.stem}",
        "-filter", "/^[0-9]+$/.test(SLDLST)", f"target={house.stem}",
        "-each", "unit_id='us-ak-hd' + ('0' + parseInt(SLDLST, 10)).slice(-2), name=NAMELSAD + ', AK'", f"target={house.stem}",
        "-merge-layers", "target=*", "force", "name=counties",
        "-filter-fields", "unit_id,name",
        "-simplify", "weighted", "4%", "keep-shapes",
        "-o", str(out), "format=topojson", "quantization=100000", "id-field=unit_id",
    )
    doc = json.loads(out.read_text())
    n_ak = sum(g["properties"]["unit_id"].startswith("us-ak-hd") for g in doc["objects"]["counties"]["geometries"])
    assert n_ak == 40, f"expected 40 Alaska house districts, got {n_ak}"
    check(out, "counties")


def tse_table(cache: Path, tmp: Path) -> Path:
    """CSV of IBGE 7-digit code -> unit_id, from the official TSE correspondence table."""
    with zipfile.ZipFile(fetch(TSE_IBGE, cache)) as z:
        raw = z.read("municipio_tse_ibge.csv")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    path = tmp / "tse_ibge.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ibge", "unit_id"])
        for row in csv.DictReader(io.StringIO(text), delimiter=";"):
            uf = row["SG_UF"].strip().lower()
            w.writerow([row["CD_MUNICIPIO_IBGE"].strip(), f"br-{uf}-{row['CD_MUNICIPIO_TSE'].strip().zfill(5)}"])
    return path


def build_br(cache: Path, tmp: Path) -> None:
    mesh = unzip_shp(fetch(IBGE_MESH, cache), tmp / "br")
    table = tse_table(cache, tmp)
    out = OUT / "br_municipalities.topo.json"
    mapshaper(
        "-i", str(mesh), "encoding=utf8",
        "-join", str(table), "keys=CD_MUN,ibge", "field-types=ibge:str,unit_id:str",
        "-filter", "unit_id != null && unit_id != ''",
        "-each", "name=NM_MUN + ', ' + SIGLA_UF",
        "-rename-layers", "municipalities",
        "-filter-fields", "unit_id,name",
        "-simplify", "weighted", "1.2%", "keep-shapes",
        "-o", str(out), "format=topojson", "quantization=100000", "id-field=unit_id",
    )
    check(out, "municipalities")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("which", nargs="*", choices=["us", "br"], help="default: both")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "brunder-geo")
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for w in a.which or ["us", "br"]:
            {"us": build_us, "br": build_br}[w](a.cache, Path(tmp))


if __name__ == "__main__":
    main()
