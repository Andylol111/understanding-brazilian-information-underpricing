"""Check the PREP publication rule (brunder.mexico.visible_at) against archived PREP 2024 cut files.

The Internet Archive holds a few copies of the PREP site's own JSON taken during the count: the national progress file
(presidencia/avanceNacional.json: cut label, actas counted) and state files (presidencia/entidad/<n>.json: votes per
candidate). Each copy shows one cut and was online by its capture time. Against the final PREP database:
  - reached: the latest verification time T such that the actas verified <= T reproduce the copy (national: the
    number of counted actas; state: Sheinbaum + Gálvez votes, flagged when the cumulative sum hits it exactly);
  - rule: what brunder.mexico.visible_at shows at the capture time (should be close to the copy, not above it).
Captures are fetched once, sequentially, with a pause, and cached in $BRUNDER_DATA/raw/mx2024/wayback/.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_publication_check.py configs/mx2024_v1.toml
Writes results/mexico/publication_check.csv.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from brunder import mexico
from brunder.paths import RAW, ROOT

WB = "https://web.archive.org/web/{ts}id_/https://prep2024.ine.mx/publicacion/nacional/assets/presidencia/{path}"
UA = {"User-Agent": "brunder-research/0.1 (academic election-count research; non-AI)"}
# (capture timestamp, file) from the Wayback CDX index of prep2024.ine.mx/publicacion/nacional/assets/*, 2-4 June 2024
CAPTURES = [("20240603023548", "avanceNacional.json"), ("20240603054115", "avanceNacional.json"),
            ("20240603173050", "avanceNacional.json"), ("20240603183533", "avanceNacional.json"),
            ("20240603023548", "entidad/15.json"), ("20240603024847", "entidad/19.json"),
            ("20240603034929", "entidad/10.json")]
MONTHS = {"Junio": 6}


def fetch(ts: str, path: str, cache: Path) -> dict:
    f = cache / f"{path.replace('/', '_').removesuffix('.json')}_{ts}.json"
    if not f.exists():
        r = httpx.get(WB.format(ts=ts, path=path), headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        f.write_bytes(r.content)
        time.sleep(2)
    b = f.read_bytes()
    return json.loads(gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)


def _n(s) -> int:
    return int(str(s).replace(",", ""))


def main(cfg_path: str) -> None:
    cfg = mexico.load_config(cfg_path)
    E, V = cfg["election"], cfg["publication"]
    actas, hdr = mexico.read_prep(RAW / E["prep"], E["a_cols"], E["b_cols"])
    off = pd.Timedelta(hours=-hdr["utc_offset_h"])
    actas["visible"] = mexico.visible_at(actas["t_verif"], pd.Timestamp(V["opening_utc"]), V["step_min"],
                                         V["include_min"], V["delay_min"])
    c = actas[actas["counted"]].sort_values("t_verif")
    cache = RAW / "mx2024" / "wayback"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for ts, path in CAPTURES:
        d = fetch(ts, path, cache)
        cap = pd.to_datetime(ts, format="%Y%m%d%H%M%S", utc=True)
        if path == "avanceNacional.json":
            day, mon, yr = d["ultimoCorte"]["fecha"].split()
            hh, mm = map(int, d["ultimoCorte"]["hora"].split(":"))
            label = (pd.Timestamp(int(yr), MONTHS[mon], int(day), hh, mm) + off).tz_localize("UTC")
            sub, measure = c, "actas counted, national"
            copy = _n(d["contabilizadas"]["total"])
            reached = sub["t_verif"].iloc[copy - 1]
            exact = True
            rule = int((sub["visible"] <= cap).sum())
        else:
            ent = int(d["entidad"])
            label = None
            sub = c[(c["ent"] == ent) & (c["uf"] != mexico.ZZ)]
            measure = f"Sheinbaum + Gálvez votes, state {ent}"
            v = {x["candidatoPropietario"]: _n(x["votos"]) for x in d["votosDistritos"]["votos"]}
            copy = next(n for k, n in v.items() if k.startswith("Claudia")) + \
                next(n for k, n in v.items() if k.startswith("Bertha"))
            cum = (sub["a"] + sub["b"]).cumsum().to_numpy()
            k = int(np.searchsorted(cum, copy))
            reached = sub["t_verif"].iloc[min(k, len(sub) - 1)]
            exact = bool(k < len(cum) and cum[k] == copy)
            seen = sub["visible"] <= cap
            rule = int((sub.loc[seen, "a"] + sub.loc[seen, "b"]).sum())
        rows.append({"capture_utc": cap, "file": path, "cut_label_utc": label, "measure": measure, "copy": copy,
                     "rule_at_capture": rule, "rule_minus_copy_pct": round(100 * (rule - copy) / copy, 1),
                     "reached_verification_utc": reached, "exact": exact,
                     "reached_min_after_label": None if label is None else round((reached - label).total_seconds() / 60, 1),
                     "capture_min_after_reached": round((cap - reached).total_seconds() / 60, 1)})
    out = pd.DataFrame(rows)
    res = ROOT / "results" / "mexico"
    res.mkdir(parents=True, exist_ok=True)
    out.to_csv(res / "publication_check.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(ROOT / "configs" / "mx2024_v1.toml")))
