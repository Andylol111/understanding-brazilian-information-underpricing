"""France: settings of the count-night model, chosen from EARLIER French nights only.

For each replayed night (2022 round 1, 2022 round 2) this fixes `kappa`, `sigma_u` and the reporting-order error `tau`
from the archived national-page states and final results of the nights before it (chronological order:
2017 round 1, 2017 round 2, 2022 round 1, 2022 round 2; no 2012 night page was archived). Nothing here reads the night
being replayed. Output: data_public/fr_calibration_v1.json.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/fr_calibrate.py

`tau` per earlier night = `nowcast_order.needed_tau` on the grid below (the smallest value whose 90% bands cover 90% of
that night's frames at 1% to 95% counted). `tau_mixture` = those values, equal weights, one per earlier night (the repo's
rule since 2026-10-31); `tau_point` = the repo's single-value rule: the smallest grid value with pooled coverage >= 0.90
and every earlier night >= 0.75 (the largest grid value if none).
"""

from __future__ import annotations

import json

import numpy as np
from scipy.stats import norm

from brunder import france as fr
from brunder import nowcast_order as no
from brunder.paths import ROOT

GRID = no.TAU_GRID + (0.075, 0.10)
KAPPAS = (0.0, 1.0e5, 1.0e6, 1.0e7)
OUT = ROOT / "data_public" / "fr_calibration_v1.json"
TARGETS = ("pr22r1", "pr22r2")


def coverage(a: dict, tau: float) -> float:
    sd = np.sqrt((a["margin_sd0"] / 2) ** 2 + ((a["two_r"] / 2) * tau) ** 2)
    return float((np.abs(a["err"]) < no.Z90 * sd).mean()) if len(a["err"]) else float("nan")


def settings_for(target: str) -> dict:
    earlier = fr.ORDER[: fr.ORDER.index(target)]
    sigma_by = {n: fr.unit_noise(n) for n in earlier}
    sigma_u = float(np.mean(list(sigma_by.values())))
    mae = {}
    for k in KAPPAS:
        p = fr.Params(kappa=k, sigma_u=sigma_u)
        per = []
        for n in earlier:
            a = fr.night_arrays(fr.night_frames(n, p), fr.target_cats(n))
            if len(a["err"]):
                per.append(float(np.abs(a["err"]).mean()))
        mae[k] = float(np.mean(per))
    kappa = min(mae, key=lambda k: (round(mae[k], 5), k))        # lowest mean absolute error; ties -> smaller kappa
    p = fr.Params(kappa=kappa, sigma_u=sigma_u)
    arr = {n: fr.night_arrays(fr.night_frames(n, p), fr.target_cats(n)) for n in earlier}
    tau = {n: no.needed_tau(a["err"], a["margin_sd0"], a["two_r"], a["total"], GRID) for n, a in arr.items() if len(a["err"])}
    pooled = {g: float(np.mean(np.concatenate([np.abs(a["err"]) < no.Z90 * np.sqrt((a["margin_sd0"] / 2) ** 2 + ((a["two_r"] / 2) * g) ** 2)
                                               for a in arr.values() if len(a["err"])]))) for g in GRID}
    worst = {g: min(coverage(a, g) for a in arr.values() if len(a["err"])) for g in GRID}
    ok = [g for g in GRID if pooled[g] >= 0.90 and worst[g] >= 0.75]
    return {"earlier_nights": earlier, "sigma_u_by_night": sigma_by, "sigma_u": sigma_u,
            "kappa_mae_by_candidate": {str(k): v for k, v in mae.items()}, "kappa": kappa,
            "tau_needed": tau, "n_frames_by_night": {n: int(len(a["err"]) / max(len(fr.target_cats(n)), 1)) for n, a in arr.items()},
            "tau_mixture": [tau[n] for n in earlier if n in tau], "tau_point": float(ok[0] if ok else GRID[-1]),
            "point_rule": "smallest grid value with pooled coverage >= 0.90 and worst earlier night >= 0.75",
            "coverage_pooled_by_tau": {str(g): round(pooled[g], 3) for g in GRID}}


def main() -> None:
    out = {"grid": list(GRID), "kappa_grid": list(KAPPAS), "note": "settings of each night come from earlier French nights only",
           "targets": {t: settings_for(t) for t in TARGETS}}
    OUT.write_text(json.dumps(out, indent=1))
    for t, s in out["targets"].items():
        print(t, "earlier", s["earlier_nights"], "sigma_u", round(s["sigma_u"], 4), "kappa", s["kappa"], "tau_needed", s["tau_needed"],
              "mixture", s["tau_mixture"], "point", s["tau_point"], "frames", s["n_frames_by_night"])


if __name__ == "__main__":
    main()
