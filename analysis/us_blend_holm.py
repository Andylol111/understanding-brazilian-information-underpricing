"""Merge every data_public/us_blend_tests_<component>.csv, apply ONE Holm correction over the whole family (docs/us_blend.md 0.5)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PUB = Path(__file__).resolve().parent.parent / "data_public"


def holm(p):
    o = np.argsort(p)
    m = len(p)
    adj = np.empty(m)
    run = 0.0
    for i, j in enumerate(o):
        run = max(run, (m - i) * p[j])
        adj[j] = min(run, 1.0)
    return adj


def main():
    fs = sorted(f for f in PUB.glob("us_blend_tests_*.csv") if f.name != "us_blend_tests.csv")
    T = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    ok = T["p"].notna()
    T["p_holm"] = np.nan
    T.loc[ok, "p_holm"] = holm(T.loc[ok, "p"].values)
    T["passed_test"] = ok & (T["p_holm"] < 0.05) & (T["lo"] > 0) & (T["mean_delta"] > 0)
    T.round(6).to_csv(PUB / "us_blend_tests.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 400, "display.float_format", lambda v: f"{v:.4g}")
    print("files:", [f.name for f in fs], "family size:", int(ok.sum()))
    print(T.to_string(index=False))


if __name__ == "__main__":
    main()
