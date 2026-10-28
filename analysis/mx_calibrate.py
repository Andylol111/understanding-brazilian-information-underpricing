"""Choose the Mexico model settings on the 2018 count only (development set, in-sample for 2018).

Replays the 2018 PREP count minute by minute (section-level nowcast, prior = 2012 by section) once with tau = 0 and
re-scales the sd to every tau of the grid (the tau term is additive in the margin variance, brunder.mexico.sd_at_tau).
Selection criterion, fixed in the dev config before this script existed (the Brazil tool's, docs/edge_tool.md):
the smallest tau whose 90% intervals contain the official final share in >= 90% of the ticks between 1% and 95%
counted. Also reports the 1-50% / 50-95% split and the projection and raw-count error at 1/5/10/25/50% counted.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_calibrate.py configs/mx2024_dev_2018.toml
Writes results/mexico/<id>/{path.parquet, calibration.csv, summary.json}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import mexico
from brunder.paths import ROOT


def main(cfg_path: str) -> None:
    cfg = mexico.load_config(cfg_path)
    out = ROOT / "results" / "mexico" / cfg["id"]
    out.mkdir(parents=True, exist_ok=True)
    s = mexico.setup(cfg)
    final = s["final_share"]
    V = cfg["publication"]
    times = pd.date_range(pd.Timestamp(V["opening_utc"]), s["header"]["cut_utc"] + pd.Timedelta(minutes=10), freq="1min")
    path = mexico.national_path(s, mexico.params(cfg, tau=0.0), times)
    path.to_parquet(out / "path.parquet")

    C = cfg["calibration"]
    lo, hi = C["window"]
    rows = []
    for tau in C["tau_grid"]:
        sd = mexico.sd_at_tau(path, 0.0, tau)
        z = (final - path["share"]) / sd
        row = {"tau": tau}
        for name, a, b in (("all", lo, hi), ("early", lo, 0.5), ("late", 0.5, hi)):
            w = path["counted"].between(a, b, inclusive="right") & np.isfinite(z)
            row[f"coverage90_{name}"] = float((z[w].abs() < 1.645).mean()) if w.any() else None
            row[f"mean_z_{name}"] = float(z[w].mean()) if w.any() else None
            row[f"n_{name}"] = int(w.sum())
        rows.append(row)
    cal = pd.DataFrame(rows)
    cal.to_csv(out / "calibration.csv", index=False)
    ok = cal[cal["coverage90_all"] >= C["target"]]
    chosen = float(ok["tau"].min()) if len(ok) else None

    err = {}
    for q in (0.01, 0.05, 0.10, 0.25, 0.50):
        x = path[path["counted"] >= q]
        if len(x):
            x = x.iloc[0]
            err[f"{int(q * 100)}%"] = {"t": x["t"].isoformat(), "model_pt": round(100 * (x["share"] - final), 3),
                                       "raw_pt": round(100 * (x["raw"] - final), 3)}
    last = path.iloc[-1]
    summary = {"id": cfg["id"], "final_share": final, "chosen_tau": chosen, "criterion": C,
               "max_counted": float(path["counted"].max()), "max_actas": float(path["actas"].max()),
               "end_share": float(last["share"]), "end_raw": float(last["raw"]), "error_at": err,
               "imputed_prior_units": int(s["prior"]["imputed"].sum()), "units": len(s["prior"]),
               "counted_actas": int(s["actas"]["counted"].sum()), "expected_actas": len(s["actas"])}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    pd.set_option("display.width", 200)
    print(cal.round(3).to_string(index=False))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(ROOT / "configs" / "mx2024_dev_2018.toml")))
