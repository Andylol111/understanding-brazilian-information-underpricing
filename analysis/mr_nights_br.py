"""Brazil presidential count nights (municipal model) for the model-review harness: br2022r1, br2022r2, br2026r1, br2026r2.

Same construction as brunder.night.replay for kind = "municipal": BU-web receipts (+3h Brasilia -> UTC) are the
visibility times, state = (n = A + B, a, f = sections visible / sections of the municipality), prior from the repo's v2
configs (night.prior_mun). Ticks are incremental (sections sorted by visible time, cumulative per-municipality arrays),
not a groupby per tick. Abroad is uf "zz", kept as a unit (Night.unpooled = ("zz",)).
Night.extra: complete_time (Series indexed by (uf, cd_mun): time the municipality's last section became visible),
unit_size (= prior V0 by (uf, cd_mun)), n_sections.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from brunder import bweb, night as bn
from mr_common import Night

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs"
TZ = pd.Timedelta("3h")

_SPEC = {  # name: (config, date, prior description)
    "br2022r1": ("br2022r1_v2", "2022-10-02", "2018 round 1 by municipality (Haddad 13 vs Bolsonaro 17)"),
    "br2022r2": ("br2022r2_v2", "2022-10-30", "2022 round 1 by municipality (Lula 13 vs Bolsonaro 22)"),
    "br2026r1": ("br2026r1_v2", "2026-10-04", "2022 round 1 by municipality (Lula 13 vs Bolsonaro 22)"),
    "br2026r2": ("br2026r2_v2", "2026-10-25", "2026 round 1 by municipality, archived live feed (13 vs 22)"),
}


def build(name: str) -> Night:
    cfg_id, date, prior_src = _SPEC[name]
    cfg = bn.load_config(CFG / f"{cfg_id}.toml")
    E, R = cfg["election"], cfg["replay"]
    pri = bn.prior_mun(cfg)
    raw = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    raw["visible"] = (raw["received"] + TZ).dt.tz_localize("UTC")
    raw["n"] = raw["a"] + raw["b"]
    final = float(raw["a"].sum() / raw["n"].sum())

    cat = raw.groupby(["uf", "cd_mun"]).agg(k=("n", "size"), last=("visible", "max")).reset_index()
    idx = pd.MultiIndex.from_frame(cat[["uf", "cd_mun"]])
    c = raw[raw["visible"].notna()].sort_values("visible")
    pos = idx.get_indexer(pd.MultiIndex.from_frame(c[["uf", "cd_mun"]]))
    vis = c["visible"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]")
    av, nv = c["a"].to_numpy(float), c["n"].to_numpy(float)
    kk = cat["k"].to_numpy(float)
    base = cat[["uf", "cd_mun"]].reset_index(drop=True)
    # municipality is complete only if every one of its sections has a visible time
    nvis = c.groupby(["uf", "cd_mun"]).size().reindex(idx).fillna(0).to_numpy()
    complete = cat["last"].where(nvis == kk)
    complete_time = pd.Series(complete.to_numpy(), index=idx, name="complete_time")
    t0, t1 = raw["visible"].min(), raw["visible"].max()

    def ticks(step_min: int = 1):
        times = pd.date_range(t0.floor(f"{step_min}min"), t1.ceil(f"{step_min}min"), freq=f"{step_min}min")
        n, a, k = np.zeros(len(cat)), np.zeros(len(cat)), np.zeros(len(cat))
        j = 0
        for t in times:
            j2 = int(np.searchsorted(vis, np.datetime64(t.tz_convert("UTC").tz_localize(None), "ns"), side="right"))
            if j2 > j:
                sl = slice(j, j2)
                np.add.at(n, pos[sl], nv[sl])
                np.add.at(a, pos[sl], av[sl])
                np.add.at(k, pos[sl], 1.0)
                j = j2
            m = k > 0
            yield t, base[m].assign(n=n[m], a=a[m], f=k[m] / kk[m]).reset_index(drop=True)

    return Night(
        name=name, date=date, country="Brazil", order="real",
        unit=f"municipality ({len(pri):,}; abroad = uf zz)", prior_source=prior_src, prior=pri, final=final,
        ticks=ticks, unpooled=("zz",),
        notes="visible = BU-web DT_BU_RECEBIDO + 3h; f = sections visible / sections of the municipality",
        extra={"unit_size": pri.set_index(["uf", "cd_mun"])["V0"], "complete_time": complete_time,
               "n_sections": len(raw)},
    )


NIGHTS = {k: (lambda k=k: build(k)) for k in _SPEC}
