"""Choose the Poland 2025 model settings from earlier elections only.

Two sources, never the election being replayed:
  runoff (pl2025r2): the real 2025 round-1 count night (18 May; 31 archived PKW versions, analysis/pl_fetch.py), with
      the 2020 round-1 result as prior. Round 1 is an earlier election than the runoff.
  round 1 (pl2025r1): the 2020 runoff final result counted in a simulated reporting order (obwody smallest first with
      noise; the 2020 election night itself is not archived), with the 2020 round-1 result as prior.
Both use `brunder.nowcast` over the 16 voivodeships (group `pl`) and abroad with ships (group `zz`).

Grid and selection rule (fixed before the first run): tau x sigma_mun0 x kappa x rho. For every frame with 1-95% of
the votes counted, z = (final two-way share - projected share) / sd of the national share. sigma_mun0 is held at
0.06, the nowcast default (not tuned: 14 correlated night frames cannot separate it from tau, and the first run, which
let it float, picked 0.10 with tau = 0). tau = the smallest value whose 90% coverage (|z| < 1.645) is >= 0.90; then
kappa and rho = the lowest mean absolute error of the projected share at that tau. Writes
results/poland/calibration_<source>.csv and calibration.json and prints the chosen values.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_calibrate.py [--refit]
"""

from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

from brunder import nowcast as nc
from brunder import poland as pl
from brunder.paths import ROOT

OUT = ROOT / "results" / "poland"
TAUS = [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05]
SIG_MUN = [0.03, 0.06, 0.10]
SIGMA_FIXED = 0.06
KAPPAS = [10_000.0, 100_000.0]
RHOS = [0.5, 0.8]
SIM_FRACTIONS = [0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
SIM_DRAWS = 4


def night_frames_2025r1() -> tuple[list[pd.DataFrame], float, pd.DataFrame]:
    """Count states of the 31 archived round-1 versions, the final two-way share, and the round-1 prior."""
    R = pl.Replay(1)
    vs = pl.versions(1)
    frames = [R.state(p) for p in vs]
    return frames, pl.national(frames[-1])[0], R.prior


def sim_frames_2020r2() -> tuple[list[pd.DataFrame], float, pd.DataFrame]:
    """The 2020 runoff counted in simulated order (SIM_DRAWS draws x SIM_FRACTIONS), with the 2020 round-1 result as the
    prior; states are rebuilt from the marginals exactly as for a real night."""
    C = pl.Classes()
    obw = pl.obwody_2020(2, C)
    p1 = pl.cells(2020, 1, C)
    e1 = p1["E"].where(p1["E"] > 0)
    ra, rb = (p1["a"] / e1).fillna(0.0), (p1["b"] / e1).fillna(0.0)
    E2 = obw.groupby(["unit", "k"])["voters"].sum().reindex(p1.index, fill_value=0.0)
    n1 = p1["a"] + p1["b"]
    pri = pd.DataFrame({"uf": ["zz" if u == pl.ABROAD else u for u, _ in p1.index],
                        "cd_mun": [f"{u}-{k}" for u, k in p1.index],
                        "s0": (p1["a"] / n1.where(n1 > 0)).fillna(0.5).to_numpy(),
                        "V0": (n1 * (E2 / p1["E"].where(p1["E"] > 0)).fillna(1.0)).to_numpy()})
    pri = pri[pri["V0"] > 0].reset_index(drop=True)
    rng = np.random.default_rng(2020)
    frames = [pl.cell_state(pl.margins_of(pl.simulated_selection(obw, f, rng), obw), E2, ra, rb)
              for _ in range(SIM_DRAWS) for f in SIM_FRACTIONS]
    return frames, float(obw["a"].sum() / (obw["a"] + obw["b"]).sum()), pri


def _one(args) -> dict:
    (tau, sg, kp, rho), frames, prior, final = args
    p = nc.Params(tau=tau, sigma_mun0=sg, kappa=kp, rho=rho)
    z, err = [], []
    for st in frames:
        o = nc.nowcast(st, prior, p)
        if 0.01 < o["counted_frac"] <= 0.95:
            z.append((final - o["share_hat"]) / o["share_sd"])
            err.append(abs(final - o["share_hat"]))
    z = np.array(z)
    return {"tau": tau, "sigma_mun0": sg, "kappa": kp, "rho": rho, "n": len(z),
            "coverage90": float((np.abs(z) < 1.645).mean()), "mean_z": float(z.mean()), "mae_pt": 100 * float(np.mean(err))}


def grid(frames: list[pd.DataFrame], prior: pd.DataFrame, final: float) -> pd.DataFrame:
    from concurrent.futures import ProcessPoolExecutor
    jobs = [(c, frames, prior, final) for c in itertools.product(TAUS, SIG_MUN, KAPPAS, RHOS)]
    with ProcessPoolExecutor() as ex:
        return pd.DataFrame(list(ex.map(_one, jobs, chunksize=4)))


def choose(g: pd.DataFrame) -> dict:
    g = g[g["sigma_mun0"] == SIGMA_FIXED]
    ok = g[g["coverage90"] >= 0.90]
    tau = ok["tau"].min() if len(ok) else g["tau"].max()
    pool = ok[ok["tau"] == tau] if len(ok) else g[g["tau"] == tau]
    best = pool.sort_values("mae_pt").iloc[0]
    return {k: float(best[k]) for k in ("tau", "sigma_mun0", "kappa", "rho", "coverage90", "mean_z", "mae_pt", "n")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f1, f2 = OUT / "calibration_2025r1_night.csv", OUT / "calibration_2020r2_simulated.csv"
    fr, fin, pri1 = night_frames_2025r1()
    fr2, fin2, pri2 = sim_frames_2020r2()
    if "--refit" in sys.argv or not (f1.exists() and f2.exists()):
        g1 = grid(fr, pri1, fin)
        g1.to_csv(f1, index=False)
        g2 = grid(fr2, pri2, fin2)
        g2.to_csv(f2, index=False)
    else:
        g1, g2 = pd.read_csv(f1), pd.read_csv(f2)
    chosen = {"runoff_from_2025r1_night": choose(g1), "round1_from_2020r2_simulated": choose(g2),
              "final_2025r1_share_A": fin, "final_2020r2_share_A": fin2}
    (OUT / "calibration.json").write_text(json.dumps(chosen, indent=1))
    pd.set_option("display.width", 200)
    for name, g in (("2025 round-1 night", g1), ("2020 runoff, simulated order", g2)):
        print(name)
        print(g.sort_values(["tau", "mae_pt"]).groupby("tau").head(2).to_string(index=False))
    print(json.dumps(chosen, indent=1))


if __name__ == "__main__":
    main()
