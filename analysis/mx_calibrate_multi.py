"""Choose the Mexico model settings on every earlier night (rule in docs/mexico.md, "v2", fixed before this ran).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_calibrate_multi.py configs/mx2024_calibration_v2.toml

For each night in the config: replay the PREP count minute by minute at tau = 0 for every (rho, kappa) of the grid
(the tau term is additive in the margin variance, brunder.mexico.sd_at_tau, so every tau is a re-scaling), then
  1. tau = smallest grid value with pooled (mean over nights) 90% coverage >= pooled_min and worst night >= worst_min,
     at the default rho / kappa;
  2. (rho, kappa) = lowest mean absolute projection error at that tau (nights weighted equally; ties -> defaults).
Coverage and error use the minutes with window[0]..window[1] of the expected two-way votes counted. Coverage against
the end-of-PREP share (A/(A+B) of all actas the PREP counted) is reported next to the cómputo one (not in the rule).
Writes results/mexico/<id>/{summary.json, coverage.csv, grid.csv}.
"""

from __future__ import annotations

import json
import sys
import tomllib
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import mexico
from brunder.paths import ROOT

_CACHE: dict = {}


def _night(cfg_path: str):
    if cfg_path not in _CACHE:
        cfg = mexico.load_config(ROOT / cfg_path)
        s = mexico.setup(cfg)
        c = s["actas"][s["actas"]["counted"] & s["actas"]["visible"].notna()]
        s["prep_end_share"] = float(c["a"].sum() / (c["a"].sum() + c["b"].sum()))
        _CACHE[cfg_path] = (cfg, s)
    return _CACHE[cfg_path]


def run_path(job):
    cfg_path, rho, kappa = job
    cfg, s = _night(cfg_path)
    V = cfg["publication"]
    times = pd.date_range(pd.Timestamp(V["opening_utc"]), s["header"]["cut_utc"] + pd.Timedelta(minutes=10),
                          freq="1min")
    path = mexico.national_path(s, mexico.params(cfg, tau=0.0, rho=rho, kappa=kappa), times)
    return job, path, s["final_share"], s["prep_end_share"]


def cover(path, final, tau, window):
    sd = mexico.sd_at_tau(path, 0.0, tau)
    w = path["counted"].between(*window, inclusive="right") & np.isfinite(path["share"]) & (sd > 0)
    z = (final - path["share"]) / sd
    return float((z[w].abs() < 1.645).mean()), float((path["share"] - final).abs()[w].mean()), int(w.sum())


def main(cfg_path: str) -> None:
    C = tomllib.loads(Path(cfg_path).read_text())
    R = C["rule"]
    out = ROOT / "results" / "mexico" / C["id"]
    out.mkdir(parents=True, exist_ok=True)
    combos = list(product(R["rho_grid"], R["kappa_grid"]))
    if (R["default_rho"], R["default_kappa"]) not in combos:
        raise SystemExit("default (rho, kappa) must be in the grid")
    jobs = [(n, rho, k) for n in C["nights"] for rho, k in combos]
    with ProcessPoolExecutor(max_workers=min(len(jobs), 6)) as ex:
        res = list(ex.map(run_path, jobs))

    rows = []
    for (n, rho, kappa), path, fin, prep_end in res:
        for tau in R["tau_grid"]:
            cv, mae, nmin = cover(path, fin, tau, R["window"])
            cvp, maep, _ = cover(path, prep_end, tau, R["window"])
            rows.append({"night": n, "rho": rho, "kappa": kappa, "tau": tau, "coverage": cv, "mae": mae,
                         "coverage_prep_end": cvp, "mae_prep_end": maep, "minutes": nmin, "final": fin,
                         "prep_end": prep_end})
    df = pd.DataFrame(rows)
    df.to_csv(out / "grid.csv", index=False)

    d = df[(df["rho"] == R["default_rho"]) & (df["kappa"] == R["default_kappa"])]
    by = d.pivot(index="tau", columns="night", values="coverage")
    cov = pd.DataFrame({"pooled": by.mean(axis=1), "worst": by.min(axis=1)}).join(by)
    cov["pooled_prep_end"] = d.pivot(index="tau", columns="night", values="coverage_prep_end").mean(axis=1)
    cov.to_csv(out / "coverage.csv")
    ok = cov[(cov["pooled"] >= R["pooled_min"]) & (cov["worst"] >= R["worst_min"])]
    tau = float(ok.index.min()) if len(ok) else None
    print(cov.round(3).to_string())
    if tau is None:
        (out / "summary.json").write_text(json.dumps({"tau": None, "reason": "no grid value meets the rule"}))
        raise SystemExit("no tau on the grid satisfies the rule")

    g = df[df["tau"] == tau].groupby(["rho", "kappa"]).agg(
        mae=("mae", "mean"), coverage=("coverage", "mean"), worst=("coverage", "min"),
        mae_prep_end=("mae_prep_end", "mean"), coverage_prep_end=("coverage_prep_end", "mean")).reset_index()
    base = g[(g["rho"] == R["default_rho"]) & (g["kappa"] == R["default_kappa"])]["mae"].iloc[0]
    best = g.sort_values("mae").iloc[0]
    pick = best if best["mae"] < base - 1e-12 else g[(g["rho"] == R["default_rho"]) &
                                                     (g["kappa"] == R["default_kappa"])].iloc[0]
    print(g.round(4).to_string())
    per_night = df[(df["tau"] == tau) & (df["rho"] == pick["rho"]) & (df["kappa"] == pick["kappa"])]
    summary = {"tau": tau, "rho": float(pick["rho"]), "kappa": float(pick["kappa"]),
               "mean_mae_pt": float(pick["mae"] * 100), "default_mean_mae_pt": float(base * 100),
               "pooled_coverage": float(pick["coverage"]), "worst_coverage": float(pick["worst"]),
               "pooled_coverage_prep_end": float(pick["coverage_prep_end"]),
               "nights": {r.night: {"coverage": r.coverage, "coverage_prep_end": r.coverage_prep_end,
                                    "mae_pt": r.mae * 100, "mae_prep_end_pt": r.mae_prep_end * 100,
                                    "final": r.final, "prep_end": r.prep_end, "minutes": r.minutes}
                          for r in per_night.itertuples()}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "configs" / "mx2024_calibration_v2.toml"))
