"""Mexico presidential count nights (2012, 2018, 2024) as `mr_common.Night`, real order (PREP acta verification times,
5-minute cuts), wrapping brunder.mexico (setup / count_states) exactly as analysis/mx_calibrate_multi.py does.

Prior: previous presidential cómputo by section (2006 -> 2012, 2012 -> 2018, 2018 -> 2024), see mexico.prior.
final = official cómputo share of A; alt_final = A/(A+B) over all counted actas with a visible time (end of PREP).
extra: lista_nominal, actas (per unit, catalogue order), median_visible (per unit), imputed (prior imputed flag).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brunder import mexico  # noqa: E402
from brunder.paths import ROOT  # noqa: E402
from mr_common import Night  # noqa: E402

_CONFIGS = {
    "mx2012": ("configs/mx2024_dev_2012.toml", "2012", "2006 presidential cómputos distritales by section"),
    "mx2018": ("configs/mx2024_dev_2018.toml", "2018", "2012 presidential cómputos by section"),
    "mx2024": ("configs/mx2024_v1.toml", "2024", "2018 presidential cómputos by casilla, aggregated by section"),
}


def _build(key: str) -> Night:
    cfg_path, year, prior_src = _CONFIGS[key]
    cfg = mexico.load_config(ROOT / cfg_path)
    s = mexico.setup(cfg)
    actas, cat = s["actas"], s["cat"]
    c = actas[actas["counted"] & actas["visible"].notna()]
    alt = float(c["a"].sum() / (c["a"].sum() + c["b"].sum()))
    med = c.groupby(["uf", "cd_mun"])["visible"].median()
    pri = s["prior"]
    extra_prior = pri.set_index(["uf", "cd_mun"])
    idx = pd.MultiIndex.from_frame(cat[["uf", "cd_mun"]])
    V = cfg["publication"]
    opening = pd.Timestamp(V["opening_utc"])
    cut = s["header"]["cut_utc"]

    def ticks(step_min: int = 1):
        times = pd.date_range(opening, cut + pd.Timedelta(minutes=10), freq=f"{int(step_min)}min")
        return mexico.count_states(actas, cat, times)

    return Night(
        name=key, date=str(cfg["election"]["date"]), country="mexico", order="real",
        unit=f"electoral section ({len(cat):,} units incl. zz pseudo-units)",
        prior_source=prior_src, prior=pri[["uf", "cd_mun", "s0", "V0"]].reset_index(drop=True),
        final=s["final_share"], alt_final=alt, ticks=ticks, unpooled=(mexico.ZZ,),
        notes="PREP verification times, 5-minute cuts, visible = cut + delay (mexico.visible_at); "
              "alt_final = end-of-PREP share of all counted actas",
        extra={"lista_nominal": cat.set_index(["uf", "cd_mun"])["ln"], "actas": cat.set_index(["uf", "cd_mun"])["k"],
               "median_visible": med.reindex(idx),
               "imputed": extra_prior["imputed"], "cfg": cfg_path, "opening_utc": opening, "cut_utc": cut},
    )


NIGHTS = {k: (lambda k=k: _build(k)) for k in _CONFIGS}
