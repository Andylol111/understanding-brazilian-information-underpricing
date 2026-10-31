"""Stage 1 of the model review: run the base nowcast and the order-aware nowcast on every real-order count night.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_frames.py [night ...]

For each night (analysis/mr_nights_*.py: real archived order, raw data on the SSD) take the frames at the first minute the
counted share reaches each of mr_common.FRACTIONS (Poland's archive is discrete: every version) and run
`brunder.nowcast_order.order_nowcast` for every config of the grid at tau = 0 (the tau term is additive in the margin
variance, `nowcast_order.with_tau`, so stage 2 re-scales to every tau). One row per night x frame x config goes to
$BRUNDER_DATA/results/model_review/frames_<night>.parquet (SSD).

Grid: covariate in {none, size, lean}; slope_sd in {0.005, 0.01, 0.02, 0.04} (for none: irrelevant);
rho in {0.5, 0.8}; kappa in {1e4, 5e4, 1e5}. Nothing here looks at an outcome; the targets are used only in stage 2.
"""

from __future__ import annotations

import itertools
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
import mr_nights_pl  # noqa: E402
from mr_common import FRACTIONS, OUT, expected_counted, frames_at  # noqa: E402

from brunder import nowcast as nc  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402

NIGHTS = {**mr_nights_br.NIGHTS, **mr_nights_mx.NIGHTS, **mr_nights_pl.NIGHTS}
SLOPE_SD = (0.005, 0.01, 0.02, 0.04)
RHO = (0.5, 0.8)
KAPPA = (10_000.0, 50_000.0, 100_000.0)


def configs() -> list[dict]:
    out = []
    for rho, kappa in itertools.product(RHO, KAPPA):
        out.append({"cov": "none", "slope_sd": np.nan, "rho": rho, "kappa": kappa})
        for cov, sd in itertools.product(("size", "lean"), SLOPE_SD):
            out.append({"cov": cov, "slope_sd": sd, "rho": rho, "kappa": kappa})
    return out


def run_night(name: str) -> str:
    t0 = time.time()
    night = NIGHTS[name]()
    if night.native:
        frames = [(np.nan, t, st) for t, st in night.ticks(1) if expected_counted(st, night.prior) > 0.005]
    else:
        frames = frames_at(night, FRACTIONS)
    rows = []
    for cfg in configs():
        p = nc.Params(tau=0.0, rho=cfg["rho"], kappa=cfg["kappa"], unpooled=night.unpooled)
        o = no.OrderParams(cfg["cov"], cfg["slope_sd"] if cfg["cov"] != "none" else 0.02)
        for i, (fr, t, st) in enumerate(frames):
            r = no.order_nowcast(st, night.prior, p, o)
            rows.append({"night": name, "frame": i, "target_frac": fr, "t": t, **cfg,
                         "counted": r["counted_frac"], "share_hat": r["share_hat"], "share_sd0": r["share_sd"],
                         "margin_hat": r["margin_hat"], "margin_sd0": r["margin_sd"],
                         "beta": r.get("hisp_beta", np.nan), "beta_sd": r.get("hisp_beta_sd", np.nan),
                         "imbalance": r.get("order_imbalance", np.nan), "corr": r.get("order_corr", np.nan),
                         "final": night.final, "alt_final": night.alt_final if night.alt_final is not None else np.nan})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"frames_{name}.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return f"{name}: {len(frames)} frames x {len(configs())} configs in {time.time() - t0:.0f}s -> {path}"


def main(names: list[str]) -> None:
    names = names or list(NIGHTS)
    with ProcessPoolExecutor(max_workers=min(len(names), 9)) as ex:
        for msg in ex.map(run_night, names):
            print(msg, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
