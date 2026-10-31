"""Poland runoff settings pooled over every earlier night that can be scored (v2, a SENSITIVITY to the v1 headline).

CHOSEN AFTER THE 2025 RUNOFF RESULT WAS KNOWN: v1 (configs/pl2025r2_v1.toml, one night: 2025 round 1) was overconfident
on the runoff (90% band held the final share in 46% of minutes), and this pooling was asked for because of it. The inputs
are still only nights before 2025-06-01; the outcome of the choice is not independent of the runoff, so it is not a
forward test and v1 stays the headline.

Search of the Internet Archive (docs/poland.md, "Earlier nights"): PKW's count-night data with several timestamped
versions exists only for 2025 (round 1 and the runoff). For 2020, 2019 and 2015 nothing was captured between poll close
and the next morning; 2023 (Sejm), 2024 (European) and 2024 (local) have one or two snapshots per night, of other election
types (party lists) at 0.3-41% counted. So the nights pooled here are the two the cell model can be scored on:
  night 1: 2025 round 1, the real count (31 archived versions, the frames with 1-95% counted), prior 2020 round 1;
  night 2: 2020 runoff counted in simulated order (smallest obwody first with noise; 4 draws x 16 fractions), prior 2020 round 1.

Rule, written before the run. Grid as pl_calibrate.py (tau x kappa x rho; sigma_mun0 held at 0.06). For each setting and
night: coverage = share of frames with 1-95% counted whose 90% band holds the final two-way share. Pooled coverage =
the mean over nights (each night weighted equally, whatever its number of frames). tau = the smallest value for which
some (kappa, rho) has pooled coverage >= 0.90; then kappa and rho = the lowest pooled mean absolute error (mean over
nights) at that tau. Writes results/poland/calibration_pooled.{csv,json}.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_calibrate_pooled.py
"""

from __future__ import annotations

import json

import pandas as pd

import pl_calibrate as pc
from brunder.paths import ROOT

OUT = ROOT / "results" / "poland"
KEY = ["tau", "sigma_mun0", "kappa", "rho"]


def pooled(g1: pd.DataFrame, g2: pd.DataFrame) -> pd.DataFrame:
    m = g1.merge(g2, on=KEY, suffixes=("_night", "_sim"))
    m["coverage90"] = (m["coverage90_night"] + m["coverage90_sim"]) / 2
    m["mae_pt"] = (m["mae_pt_night"] + m["mae_pt_sim"]) / 2
    return m


def choose(m: pd.DataFrame) -> dict:
    m = m[m["sigma_mun0"] == pc.SIGMA_FIXED]
    ok = m[m["coverage90"] >= 0.90]
    tau = ok["tau"].min() if len(ok) else m["tau"].max()
    best = (ok if len(ok) else m)[lambda d: d["tau"] == tau].sort_values("mae_pt").iloc[0]
    keys = ("tau", "sigma_mun0", "kappa", "rho", "coverage90", "coverage90_night", "coverage90_sim", "mae_pt",
            "mean_z_night", "mean_z_sim", "n_night", "n_sim")
    return {k: float(best[k]) for k in keys}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fr1, fin1, pri1 = pc.night_frames_2025r1()
    fr2, fin2, pri2 = pc.sim_frames_2020r2()
    g1, g2 = pc.grid(fr1, pri1, fin1), pc.grid(fr2, pri2, fin2)
    m = pooled(g1, g2)
    m.to_csv(OUT / "calibration_pooled.csv", index=False)
    chosen = choose(m)
    (OUT / "calibration_pooled.json").write_text(json.dumps(chosen, indent=1))
    pd.set_option("display.width", 200)
    print(m[m["sigma_mun0"] == pc.SIGMA_FIXED].sort_values(["tau", "mae_pt"]).groupby("tau").head(1)[
        ["tau", "kappa", "rho", "coverage90_night", "coverage90_sim", "coverage90", "mae_pt"]].to_string(index=False))
    print(json.dumps(chosen, indent=1))


if __name__ == "__main__":
    main()
