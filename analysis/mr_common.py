"""Shared contract for the model-review night loaders (analysis/mr_nights_*.py) and the leave-one-election-out harness.

A `Night` is one real-order count night replayed from raw data on the SSD ($BRUNDER_DATA, default ~/brunder-data-local):
  - prior   : DataFrame[uf, cd_mun, s0, V0] in the `brunder.nowcast` convention (A's two-way prior share, expected votes),
              built only from elections before the night;
  - ticks() : iterator over (t, state) with state = DataFrame[uf, cd_mun, n, a, f] (the `nowcast` count state: counted
              two-way votes, A votes, share of the unit's expected ballots/sections counted), one tick per `step_min`
              minutes, in the order the data became public;
  - final   : the official final two-way share of A (scoring target); alt_final: a second target if the count source
              stops short of or differs from it (Mexico: share of the PREP's own last cut), else None.
Nothing here edits `brunder.nowcast`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BRUNDER_DATA = Path(os.environ.get("BRUNDER_DATA", Path.home() / "brunder-data-local"))
OUT = BRUNDER_DATA / "results" / "model_review"          # intermediate outputs (SSD, never the Mac disk)

# counted-share targets at which calibration frames are taken (the repo's Brazil convention, analysis/br_calibrate.py)
FRACTIONS = (0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)


@dataclass
class Night:
    name: str                        # e.g. "br2026r1"
    date: str                        # ISO date of the count night (chronological ordering)
    country: str
    order: str                       # "real" (archived receipt/verification times) or "simulated"
    unit: str                        # granularity of the model's units, e.g. "municipality (5,708)"
    prior_source: str                # e.g. "2022 round 1 by municipality"
    prior: pd.DataFrame              # uf, cd_mun, s0, V0
    final: float                     # official final two-way share of A
    ticks: Callable[[int], Iterator[tuple[pd.Timestamp, pd.DataFrame]]]   # ticks(step_min) -> (t, state) iterator
    unpooled: tuple[str, ...] = ("zz",)   # regions kept out of the national swing (abroad etc.)
    alt_final: float | None = None   # second target (Mexico: end-of-PREP share), else None
    native: bool = False             # ticks are discrete archived releases (use all of them) rather than a minute clock
    notes: str = ""
    extra: dict = field(default_factory=dict)


def expected_counted(state: pd.DataFrame, prior: pd.DataFrame) -> float:
    """Cheap counted-share proxy used to pick calibration frames: sum(f V0) / sum(V0) over the prior's units."""
    m = prior.merge(state[["uf", "cd_mun", "f"]], on=["uf", "cd_mun"], how="left")
    return float((m["f"].fillna(0.0) * m["V0"]).sum() / m["V0"].sum())


def frames_at(night: Night, fractions=FRACTIONS, step_min: int = 1) -> list[tuple[float, pd.Timestamp, pd.DataFrame]]:
    """First tick at which the cheap counted share reaches each target fraction: [(fraction, t, state)]."""
    want = list(fractions)
    out = []
    for t, st in night.ticks(step_min):
        if not want:
            break
        c = expected_counted(st, night.prior)
        while want and c >= want[0]:
            out.append((want.pop(0), t, st.copy()))
    return out
