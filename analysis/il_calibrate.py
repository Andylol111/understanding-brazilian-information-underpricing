"""Choose the Israel nowcast settings on past counts only (2021 and 2022), before any 2026 scoring.

Data: the CEC's official ballot-box files (expb.csv) as captured by the Wayback Machine during the
24th (2021-03-23) and 25th (2022-11-01) Knesset counts, with the previous Knesset's final file as the
prior (analysis/il_fetch.py). The number of ballot boxes per locality is taken from the final file: the
polling-station list is published before election day, so this is not look-ahead.

Protocol (the chosen values are copied into configs/il2026_v1.toml; nothing from 2026 is used):
  1. prior_votes x own_votes grid: pick the pair with the lowest mean absolute error of the projected
     final share (lists with >= 1% of the final vote, captures with 1-95% of boxes counted, both
     elections pooled, every capture weighted equally).
  2. tau: the smallest value on the grid whose 90% interval coverage is >= 0.90 both in the first half of
     the count (1-50% of boxes) and in the second (50-95%), each pooled over the two elections.
     (Revision made on the 2021/2022 results, before any 2026 run: a single pooled coverage was first
     used, but the late captures outnumber the early ones 2:1 and alone picked tau = 0 while coverage in
     the 5-50% range of 2021 was ~0.8. Early is when markets settle, so each half must be covered.)
  3. turnout_sd: root mean square of log(actual / projected uncounted valid votes) over the same
     captures (RMS, not sd: the projection was biased low in both elections).
  Other settings stay at their defaults (not tuned).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_calibrate.py
Writes results/israel/calibration_grid.csv, calibration_z.csv, calibration_choice.json.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import israel as il
from brunder.paths import RAW, ROOT

WB = RAW / "israel" / "cec_wayback"
OUT = ROOT / "results" / "israel"
ELECTIONS = {"k25": "k24", "k24": "k23"}               # replayed count -> prior
PRIOR_GRID = (100.0, 250.0, 500.0, 1_000.0, 2_500.0, 5_000.0, 20_000.0, 80_000.0)
OWN_GRID = (25.0, 100.0, 250.0, 1_000.0, 4_000.0)
TAU_GRID = (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5)
WINDOW = (0.01, 0.95)


def captures(k: str) -> list[tuple[str, Path]]:
    idx = json.loads((WB / k / "index.json").read_text())
    return [(r["timestamp"], WB / k / r["file"]) for r in idx]


def load(k: str):
    caps = captures(k)
    final = il.by_locality(il.read_expb(caps[-1][1]))
    prior = il.by_locality(il.read_expb(captures(ELECTIONS[k])[-1][1]))
    units = il.units_from(prior, final["boxes"])
    counts = []
    for ts, f in caps:
        c = il.by_locality(il.read_expb(f))
        if len(c) and c["boxes"].sum() / final["boxes"].sum() < 0.999:
            counts.append((ts, c))
    return units, final, counts


def evaluate(units, final, counts, st: il.Settings, k: str) -> pd.DataFrame:
    fl = il.lists_of(final)
    fv = final[fl].sum()
    rows = []
    for ts, c in counts:
        p = il.project(units, c, st)
        share, sd = p.shares()
        big = [x for x in p.lists if x != il.OTHER]
        truth = np.array([fv.get(x, 0.0) for x in big] + [fv.sum() - fv.reindex(big).fillna(0).sum()]) / fv.sum()
        rem_true = fv.sum() - p.counted.sum()
        for x, s, d, t in zip(p.lists, share, sd, truth):
            if x == il.OTHER or t < 0.01:
                continue
            rows.append({"election": k, "ts": ts, "counted": p.frac_counted, "list": x, "share": s, "sd": d,
                         "final": t, "err": s - t, "z": (t - s) / d if d > 0 else np.nan,
                         "log_rem_ratio": np.log(rem_true / p.rem_total) if p.rem_total > 0 and rem_true > 0 else np.nan})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {k: load(k) for k in ELECTIONS}
    base = il.Settings()
    grid = []
    for pv in PRIOR_GRID:
        for ov in OWN_GRID:
            st = replace(base, prior_votes=pv, own_votes=ov, tau=0.0)
            d = pd.concat([evaluate(*data[k], st, k) for k in data])
            w = d[d["counted"].between(*WINDOW)]
            mae = w.groupby(["election", "ts"])["err"].apply(lambda e: e.abs().mean()).mean()
            grid.append({"prior_votes": pv, "own_votes": ov, "mae_pt": 100 * mae, "n": len(w)})
    grid = pd.DataFrame(grid).sort_values("mae_pt")
    best = grid.iloc[0]
    taus = []
    for tau in TAU_GRID:
        st = replace(base, prior_votes=float(best.prior_votes), own_votes=float(best.own_votes), tau=tau)
        d = pd.concat([evaluate(*data[k], st, k) for k in data])
        w = d[d["counted"].between(*WINDOW)]
        early, late = w[w["counted"] < 0.5], w[w["counted"] >= 0.5]
        taus.append({"tau": tau, "coverage90": float((w["z"].abs() < 1.645).mean()),
                     "cov90_early": float((early["z"].abs() < 1.645).mean()),
                     "cov90_late": float((late["z"].abs() < 1.645).mean()), "mean_z": float(w["z"].mean()),
                     "sd_z": float(w["z"].std()), "n": len(w)})
    taus = pd.DataFrame(taus)
    ok = taus[(taus["cov90_early"] >= 0.90) & (taus["cov90_late"] >= 0.90)]
    tau = float(ok["tau"].iloc[0]) if len(ok) else float(TAU_GRID[-1])
    st = replace(base, prior_votes=float(best.prior_votes), own_votes=float(best.own_votes), tau=tau)
    z = pd.concat([evaluate(*data[k], st, k) for k in data])
    w = z[z["counted"].between(*WINDOW)]
    per_cap = w.groupby(["election", "ts"])["log_rem_ratio"].first()
    turnout_sd = float(np.sqrt((per_cap ** 2).mean()))
    choice = {"prior_votes": float(best.prior_votes), "own_votes": float(best.own_votes), "tau": tau,
              "turnout_sd": round(turnout_sd, 4), "mae_pt": float(best.mae_pt),
              "coverage90": float((w["z"].abs() < 1.645).mean()), "mean_z": float(w["z"].mean()),
              "captures": {k: int(w[w.election == k]["ts"].nunique()) for k in data}, "n_z": len(w)}
    grid.to_csv(OUT / "calibration_grid.csv", index=False)
    taus.to_csv(OUT / "calibration_tau.csv", index=False)
    z.to_csv(OUT / "calibration_z.csv", index=False)
    (OUT / "calibration_choice.json").write_text(json.dumps(choice, indent=1))
    print(grid.to_string(index=False))
    print(taus.to_string(index=False))
    print(json.dumps(choice, indent=1))
    by = w.assign(bucket=pd.cut(w["counted"], [0.01, 0.05, 0.25, 0.5, 0.75, 0.95])).groupby(["election", "bucket"], observed=True)
    print(by.agg(mae_pt=("err", lambda e: 100 * e.abs().mean()), cov90=("z", lambda q: (q.abs() < 1.645).mean()), n=("z", "size")))


if __name__ == "__main__":
    main()
