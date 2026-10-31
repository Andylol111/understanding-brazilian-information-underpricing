"""Turn two Internet Archive copies of the Ministry's 2023 results site into data_public/es_2023_night_snapshot.json.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/es_snapshot.py

The Ministry of the Interior's public site for the 2023 general election (resultados.generales23j.es, now offline)
served JSON from /backend-difu/. The Internet Archive captured the national Congress file twice:
  * 2023-07-23 20:04:39 UTC (data stamped 20:03:13 UTC, 22:03 in Madrid): 41,711 of 60,314 tables counted;
  * 2023-07-24 08:00:04 UTC (data stamped 00:24:13 UTC): 100% of tables counted (night count; abroad votes not yet in).
These are the only election-night states of that site in the archive, so they are the only real count-order evidence
we have for an earlier Spanish night. Party votes are grouped into the model's lanes with brunder.spain.hist_lane.
Raw files: $BRUNDER_DATA/es2026/raw/wayback2023/ (fetched from web.archive.org with `id_` raw replay, 1 request/s).
Licence: Ministry of the Interior public results (reusable with the source named, Ley 37/2007).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brunder import spain as es
from brunder.paths import DATA, ROOT

RAW = DATA / "es2026" / "raw" / "wayback2023"
OUT = ROOT / "data_public" / "es_2023_night_snapshot.json"
FILES = {"partial": "s_20230723T200439_es.json", "final_night": "s_20230724T080004_es.json"}
ARCHIVE = {"partial": "https://web.archive.org/web/20230723200439id_/https://resultados.generales23j.es/backend-difu/scope/data/getScopeDataTopPartidosGanadores/00000000000000000000000b/1/2/1/1/20",
           "final_night": "https://web.archive.org/web/20230724080004id_/https://resultados.generales23j.es/backend-difu/scope/data/getScopeDataTopPartidosGanadores/00000000000000000000000b/1/2/1/1/20"}


def lanes(doc: dict) -> dict:
    e = doc["scope"]["escrutinio"]
    out = {k: 0 for k in es.HIST_LANES}
    for p in e["partidos"]:
        out[es.hist_lane(f"{p['siglas']} {p['nombre']}")] += int(p["votos"]["pro"])
    total = int(e["votosPartidos"]["pro"])
    assert sum(out.values()) == total, (sum(out.values()), total)
    return {**out, "total_parties": total, "tables_counted": int(e["mesasEscrutadas"]["pro"]),
            "tables_total": int(doc["scope"]["numMesas"]), "stamp": e["fecha"], "envio": e["envio"]}


def main() -> None:
    res = {}
    for k, name in FILES.items():
        res[k] = lanes(json.loads((RAW / name).read_text()))
    res["tables_counted_share"] = res["partial"]["tables_counted"] / res["partial"]["tables_total"]
    res["source"] = ARCHIVE
    res["note"] = ("Counted party votes by lane at the two archived states of the Ministry's 2023 results site. "
                   "The 20:03 UTC state had PSOE ahead of PP in the counted votes; PP finished ahead.")
    OUT.write_text(json.dumps(res, indent=1))
    print(OUT, {k: res[k]["PP"] for k in ("partial", "final_night")}, res["tables_counted_share"])


if __name__ == "__main__":
    main()
