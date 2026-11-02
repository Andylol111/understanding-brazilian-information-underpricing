"""Blend 1: statewide pre-count blend of v3 (LOCO-refit sd) and unified, from data_public/us_benchmark_statewide_races.csv (repo only).

    uv run python -W ignore analysis/us_blend_statewide.py
Writes data_public/us_blend_tests_statewide.csv, us_blend_statewide_summary.csv, us_blend_statewide_races.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_blend_common as bc  # noqa: E402

PUB = Path(__file__).resolve().parent.parent / "data_public"


def main():
    d = pd.read_csv(PUB / "us_benchmark_statewide_races.csv")
    y = d["s2"].values
    win = (y > 0.5).astype(float)

    def crps_w(w, x):
        mu, sd = bc.mix(x["v3_mu"].values, x["v3_sd"].values, x["unified_mu"].values, x["unified_sd"].values, w)
        return (bc.crps_normal(x["s2"].values, mu, sd) * 100).mean()

    d["w_loco"] = bc.loco_weight(d, "t", crps_w)
    for name, w in (("eq", np.full(len(d), 0.5)), ("loco", d["w_loco"].values)):
        mu, sd = bc.mix(d["v3_mu"].values, d["v3_sd"].values, d["unified_mu"].values, d["unified_sd"].values, w)
        d[f"{name}_mu"], d[f"{name}_sd"] = mu, sd
    models = {"v3": ("v3_mu", "v3_sd"), "unified": ("unified_mu", "unified_sd"), "blend_eq": ("eq_mu", "eq_sd"), "blend_loco": ("loco_mu", "loco_sd")}
    for m, (a, b) in models.items():
        mu, sd = d[a].values, d[b].values
        p = np.clip(ndtr((mu - 0.5) / sd), 1e-4, 1 - 1e-4)
        d[f"{m}_crps"] = bc.crps_normal(y, mu, sd) * 100
        d[f"{m}_brier"] = (p - win) ** 2
        d[f"{m}_cov90"] = (np.abs(y - mu) < 1.645 * sd).astype(float)
        d[f"{m}_mae"] = np.abs(y - mu) * 100
    rng = np.random.default_rng(bc.SEED)
    rows = []
    classes = [("pooled", np.ones(len(d), bool))] + [(f"C1 {k}", (d["C1"] == k).values) for k in ("low", "medium", "high")]
    for cand in ("blend_eq", "blend_loco"):
        for par in ("v3", "unified"):
            for cl, m in classes:
                x = d[m]
                rows.append(bc.test_row("statewide", f"{cand} vs {par}", cl, (x[f"{par}_crps"] - x[f"{cand}_crps"]).values, x["t"].values, rng))
    T = pd.DataFrame(rows)
    T.to_csv(PUB / "us_blend_tests_statewide.csv", index=False)
    summ = []
    for cl, m in classes + [(f"cycle {c}", (d["t"] == c).values) for c in sorted(d["t"].unique())]:
        x = d[m]
        for mod in models:
            summ.append({"class": cl, "model": mod, "n": len(x), "crps_pts": x[f"{mod}_crps"].mean(), "mae_pts": x[f"{mod}_mae"].mean(),
                         "brier": x[f"{mod}_brier"].mean(), "cov90": x[f"{mod}_cov90"].mean()})
    S = pd.DataFrame(summ)
    S.round(5).to_csv(PUB / "us_blend_statewide_summary.csv", index=False)
    keep = ["race", "t", "st", "s2", "C1", "v3_mu", "v3_sd", "unified_mu", "unified_sd", "w_loco", "eq_mu", "eq_sd", "loco_mu", "loco_sd"]
    d[keep].round(5).to_csv(PUB / "us_blend_statewide_races.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 300, "display.float_format", lambda v: f"{v:.4g}")
    print(d.groupby("t")["w_loco"].first())
    print(S.to_string(index=False))
    print(T.to_string(index=False))


if __name__ == "__main__":
    main()
