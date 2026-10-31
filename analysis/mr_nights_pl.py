"""Poland 2025 count nights (cell model) for the model-review harness: pl2025r1, pl2025r2.

Units are the 128 voivodeship x gmina-size-class cells plus abroad (`brunder.poland.Replay`), rebuilt from the published
marginals of each archived PKW version (31 per round, real order). The archive is discrete: `ticks` yields one state per
archived version (the `step_min` argument is ignored) and `Night.native` is True so the harness uses every version with
1-95% counted instead of picking frames at fixed fractions. Prior = the 2020 election of the same round (Trzaskowski vs Duda).
Night.prior carries an extra column `size` = mean electorate of the gminas in the cell's size class (known before the
election): the reporting unit behind a cell is a polling district of a gmina of that size, V0 (the whole cell) is not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import poland as pl
from mr_common import Night

_DATE = {1: "2025-05-18", 2: "2025-06-01"}
_PRIOR = {1: "2020 round 1 by cell (Trzaskowski vs Duda)", 2: "2020 runoff by cell (Trzaskowski vs Duda)"}


def build(rnd: int) -> Night:
    R = pl.Replay(rnd)
    vs = pl.versions(rnd)
    g = pl.gminas(2025, rnd, cand_b="NAWROCKI")
    dom = g[g["unit"].isin(pl.WOJ)].copy()
    dom["k"] = R.classes.of(dom["key"], dom["E"])
    size = dom.groupby(["unit", "k"])["E"].mean()
    pri = R.prior.copy()
    key = pri["cd_mun"].str.split("-", n=1, expand=True)
    idx = pd.MultiIndex.from_arrays([key[0], key[1].astype(int)])
    pri["size"] = size.reindex(idx).fillna(size.median()).to_numpy()
    final = pl.national(R.state(vs[-1]))[0]

    def ticks(step_min: int = 1):
        for p in vs:
            yield pl.version_time(pl.blob_version(p)), R.state(p)

    return Night(name=f"pl2025r{rnd}", date=_DATE[rnd], country="Poland", order="real", unit="voivodeship x gmina-size class cells (128) + abroad",
                 prior_source=_PRIOR[rnd], prior=pri, final=final, ticks=ticks, unpooled=("zz",), native=True,
                 notes="31 archived versions; cell state rebuilt from marginals (IPF)", extra={"n_versions": len(vs)})


NIGHTS = {f"pl2025r{r}": (lambda r=r: build(r)) for r in (1, 2)}
