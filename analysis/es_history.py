"""Build data_public/es_congreso_province_2016_2023.csv from the Interior Ministry's open Congress dataset.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/es_history.py

Source: Ministerio del Interior, "Elecciones Congreso" (final results by province, 1977-2023)
https://descargas.interior.gob.es/datasets/resultados_electorales/Elecciones-Congreso.xlsx
Listed on https://infoelectoral.interior.gob.es/es/elecciones-celebradas/datos-abiertos/ with licence CC BY 4.0
(http://publications.europa.eu/resource/authority/licence/CC_BY_4_0). Attribution: "Fuente: Ministerio del Interior".

Every candidacy ("Votos (NAME)" / "Escaños (NAME)" column) is grouped into a lane by brunder.spain.hist_lane. Votes and
seats are summed per province and lane. The file is a derived series: one row per election and province.
The download is validated (HTTP 200, size, workbook opens, 52 provinces, national totals) before it is kept.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brunder import spain as es
from brunder.paths import DATA, ROOT

URL = "https://descargas.interior.gob.es/datasets/resultados_electorales/Elecciones-Congreso.xlsx"
UA = "brunder/0.1 (research; recorder)"
RAW = DATA / "es2026" / "raw" / "Elecciones-Congreso.xlsx"
OUT = ROOT / "data_public" / "es_congreso_province_2016_2023.csv"
ELECTIONS = ("2016-06-26", "2019-04-28", "2019-11-10", "2023-07-23")
# Official national totals used as a check (valid votes incl. blank; Congress seats).
CHECK = {"2016-06-26": (24_053_755, 350), "2019-04-28": (26_201_371, 350), "2019-11-10": (24_258_228, 350),
         "2023-07-23": (24_688_087, 350)}


def download() -> Path:
    if RAW.exists() and RAW.stat().st_size > 500_000:
        return RAW
    RAW.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        body = r.read()
    if not body.startswith(b"PK") or len(body) < 500_000:
        raise RuntimeError(f"not an xlsx workbook: {body[:80]!r}")
    tmp = RAW.with_suffix(".part")
    tmp.write_bytes(body)
    pd.read_excel(tmp, header=3, nrows=2)       # must open
    tmp.replace(RAW)
    return RAW


def build() -> pd.DataFrame:
    d = pd.read_excel(download(), header=3)
    prov_cols = [c for c in d.columns if re.match(r"^\d+ - ", c)]
    assert len(prov_cols) == 52, len(prov_cols)
    rows = []
    for date in ELECTIONS:
        g = d[d["Fecha"] == date].reset_index(drop=True)
        desc = g["Descripción"].astype(str)
        sing = {k: g.loc[desc == k].iloc[0] for k in ("Electores", "Votos válidos", "Votos en blanco")}
        kinds = []
        for i, s in enumerate(desc):
            m = re.match(r"^(Votos|Escaños) \((.*)\)$", s)
            if m:
                kinds.append((i, m.group(1), es.hist_lane(m.group(2))))
        for col in prov_cols:
            code, name = col.split(" - ", 1)
            code = f"{int(code):02d}"
            rec = {"election": date, "prov": code, "name": name, "ccaa": es.PROV_CCAA[code],
                   "electors": int(sing["Electores"][col]), "valid": int(sing["Votos válidos"][col]),
                   "blank": int(sing["Votos en blanco"][col])}
            votes = {x: 0 for x in es.HIST_LANES}
            seats = {x: 0 for x in es.HIST_LANES}
            for i, kind, lane in kinds:
                v = g.at[i, col]
                if pd.notna(v):
                    (votes if kind == "Votos" else seats)[lane] += int(v)
            rec.update(votes)
            rec.update({f"seats_{k}": v for k, v in seats.items()})
            assert sum(votes.values()) + rec["blank"] == rec["valid"], (date, code, sum(votes.values()), rec["blank"], rec["valid"])
            rows.append(rec)
    out = pd.DataFrame(rows)
    for date, (valid, seats) in CHECK.items():
        x = out[out["election"] == date]
        assert x["valid"].sum() == valid, (date, x["valid"].sum())
        assert x[[f"seats_{k}" for k in es.HIST_LANES]].to_numpy().sum() == seats, date
    return out


def main() -> None:
    out = build()
    head = ("# Congress of Deputies results by province, 2016-2023, grouped into party lanes (see brunder.spain.hist_lane).\n"
            "# Source: Ministerio del Interior, Elecciones-Congreso.xlsx (Infoelectoral open data, CC BY 4.0). Fuente: Ministerio del Interior.\n"
            "# Final results incl. votes cast abroad (CERA). valid includes blank votes. seats_<lane> = seats won by the lane in the province.\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(head + out.to_csv(index=False))
    print(OUT, len(out), "rows")


if __name__ == "__main__":
    main()
