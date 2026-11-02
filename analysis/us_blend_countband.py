"""Component 5 of docs/us_blend.md: one multiplier m on the v3 count-stage sd (Normal CRPS of the projected final share).

Real nights (NYT/Edison-derived, internal): fitted on one year, tested on the other. Only the test summary rows go to data_public;
per-night detail goes to $BRUNDER_DATA/derived/us_blend/. Georgia simulated nights (official results, publishable): before/after.
Inputs: $BRUNDER_DATA/derived/us_benchmark/{real_frames,ga_sim_raw}.csv (the projection's share_hat / sd / err per frame).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

import us_blend_common as bc

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
SRC = DATA / "derived" / "us_benchmark"
OUT_PRIV = DATA / "derived" / "us_blend"
CHECKS = (0.10, 0.25, 0.50, 0.75)
TOL = 0.05
Z90 = 1.645
VARIANT = "v3"


def nearest(g: pd.DataFrame, c: float):
    g = g[(g["counted"] > 0) & (g["counted"] <= 1.0)]
    if g.empty:
        return None
    i = (g["counted"] - c).abs().idxmin()
    return g.loc[i] if abs(g.at[i, "counted"] - c) <= TOL else None


def real_points() -> pd.DataFrame:
    d = pd.read_csv(SRC / "real_frames.csv")
    d = d[(d["variant"] == VARIANT) & (~d["interp"].astype(bool)) & (d["t"].isin([2020, 2024]))]
    rows = []
    for night, g in d.groupby("night"):
        h = g[(g["minute"] >= 0) & (g["counted"] <= 0.995)].sort_values("minute")
        for c in CHECKS:
            r = nearest(h, c)
            if r is not None and r["sd"] > 0:
                rows.append({"night": night, "t": int(r["t"]), "office": r["office"], "check": c, "err": r["err"], "sd": r["sd"]})
    return pd.DataFrame(rows)


def crps(err, sd, m=1.0):
    return bc.crps_normal(0.0, np.asarray(err, float), np.asarray(sd, float) * m)  # symmetric in (y - mu)


def cover(err, sd, m=1.0):
    return (np.abs(np.asarray(err, float)) / (np.asarray(sd, float) * m) < Z90).astype(float)


def fit_m(err, sd) -> float:
    return float(minimize_scalar(lambda m: crps(err, sd, m).mean(), bounds=(0.2, 30), method="bounded", options={"xatol": 1e-6}).x)


def cluster_boot(delta, night, rng):
    u, inv = np.unique(night, return_inverse=True)
    s = np.bincount(inv, delta, len(u))
    k = np.bincount(inv, minlength=len(u)).astype(float)
    idx = rng.integers(0, len(u), (bc.B, len(u)))
    m = s[idx].sum(1) / k[idx].sum(1)
    p = 2 * min((m <= 0).mean(), (m >= 0).mean())
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975)), float(min(max(p, 1 / bc.B), 1.0))


def main() -> None:
    OUT_PRIV.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(bc.SEED)
    P = real_points()
    tests, detail = [], []
    m_full = fit_m(P["err"], P["sd"])
    for train, test in ((2024, 2020), (2020, 2024)):
        tr, te = P[P.t == train], P[P.t == test]
        m = fit_m(tr["err"], tr["sd"])
        delta = np.asarray(crps(te["err"], te["sd"]) - crps(te["err"], te["sd"], m), float)
        lo, hi, p = cluster_boot(delta, te["night"].to_numpy(), rng)
        cb, ca = cover(te["err"], te["sd"]).mean(), cover(te["err"], te["sd"], m).mean()
        tests.append({"component": "countband", "test": f"m fitted {train}, held-out {test}", "class": "real nights pooled checkpoints",
                      "n": len(te), "mean_delta": delta.mean(), "lo": lo, "hi": hi, "p": p, "cycles_pos": int(delta.mean() > 0), "cycles_eval": 1})
        detail.append({"fit_year": train, "test_year": test, "m": m, "n_train": len(tr), "n_test": len(te), "nights_test": te.night.nunique(),
                       "crps_before": crps(te["err"], te["sd"]).mean(), "crps_after": crps(te["err"], te["sd"], m).mean(),
                       "cover_before": cb, "cover_after": ca, "mean_delta": delta.mean(), "lo": lo, "hi": hi, "p": p,
                       "train_cover_before": cover(tr["err"], tr["sd"]).mean(), "train_cover_after": cover(tr["err"], tr["sd"], m).mean()})
    D = pd.DataFrame(detail)
    D["m_full"] = m_full
    D.to_csv(OUT_PRIV / "countband_folds.csv", index=False)
    P.to_csv(OUT_PRIV / "countband_points.csv", index=False)
    pd.DataFrame(tests)[bc.TEST_COLS].to_csv(ROOT / "data_public" / "us_blend_tests_countband.csv", index=False)

    # Georgia simulated nights (official certified results by vote type): v3, check = simulated counted-share grid point
    g = pd.read_csv(SRC / "ga_sim_raw.csv")
    g = g[(g["version"] == VARIANT) & g["check"].round(2).isin(CHECKS)]
    ms = {"1.0 (registered)": 1.0, f"fit 2024 ({D.m[0]:.3f})": D.m[0], f"fit 2020 ({D.m[1]:.3f})": D.m[1], f"full sample ({m_full:.3f})": m_full}
    rows = []
    for lab, m in ms.items():
        for scope in list(g.scope.unique()) + ["pooled"]:
            for ck in list(CHECKS) + ["all"]:
                s = g if scope == "pooled" else g[g.scope == scope]
                if ck != "all":
                    s = s[s["check"].round(2) == ck]
                rows.append({"m": lab, "m_value": m, "scope": scope, "check": ck, "n": len(s), "coverage90": cover(s["err"], s["sd"], m).mean(),
                             "crps_share_pts": 100 * crps(s["err"], s["sd"], m).mean(), "mean_sd_pts": 100 * (s["sd"] * m).mean()})
    pd.DataFrame(rows).to_csv(ROOT / "data_public" / "us_blend_countband_ga.csv", index=False)
    print("ok")


if __name__ == "__main__":
    main()
