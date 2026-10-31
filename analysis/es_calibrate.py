"""Choose and score the Spain nowcast settings on EARLIER elections only (simulated count orders).

    uv run python analysis/es_calibrate.py --out /tmp/es_calibrate.json

The model is run on three earlier Congress elections, each from the previous election's province results:
2016->2019-04, 2019-04->2019-11 and 2019-11->2023. Settings are chosen on the first two ("fit") and scored on 2023
("holdout"). The frozen 2026 settings in configs/es2026_v1.toml are the ones this script picks on all three.

A real count order is not available for past nights (the Ministry keeps no timestamped record), so counts are
simulated: provinces report at different speeds, and the counted part of each province is a biased sample of it
(early tables lean one way). The size of that bias is set from the one real snapshot of an earlier night that the
Internet Archive holds: the Ministry's public site at 20:03 UTC on 2023-07-23, with 69% of tables counted (PSOE
ahead of PP in the counted votes, PP ahead in the end). Those shares are in data_public/es_2023_night_snapshot.json.
Everything here is a retrospective replay, not pre-registered.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brunder import spain as es

H = es.load_history()
LANES = es.HIST_LANES
TRANSITIONS = [("2016-06-26", "2019-04-28"), ("2019-04-28", "2019-11-10"), ("2019-11-10", "2023-07-23")]
MILESTONES = (0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 0.95)
TOP = ("PP", "PSOE", "VOX")
SNAP = Path(__file__).resolve().parents[1] / "data_public" / "es_2023_night_snapshot.json"


def make_case(prior_date: str, target_date: str):
    prior, target = es.history_units(prior_date, H), es.history_units(target_date, H)
    units = prior[["name", "region", "blank_frac"]].copy()
    units["V0"] = prior["V"]
    units[list(LANES)] = prior[list(LANES)]
    units["seats"] = target["seats"]
    final = target[list(LANES)].to_numpy() * target["V"].to_numpy()[:, None]
    return units, final, target


def truth(final: np.ndarray, target: pd.DataFrame) -> dict:
    skip = (LANES.index("OTHER"),)
    seats = np.zeros(len(LANES))
    valid = 0.0
    for i in range(len(final)):
        v = final[i:i + 1]
        vl = v.sum() / (1 - target["blank_frac"].iloc[i])
        valid += vl
        seats += es.dhondt(v, int(target["seats"].iloc[i]), np.array([vl]), skip)[0]
    votes = final.sum(axis=0)
    key = seats * 1e10 + votes
    rank = {k: int((key > key[i]).sum() + 1) for i, k in enumerate(LANES)}
    return {"share": votes / valid, "rank": rank, "seats": seats}


def simulate_counts(final, milestone, rng, bias_nat, bias_prov, cera, speed_sd=0.35):
    P, K = final.shape
    N = final * (1 - cera)
    f = np.clip(milestone * np.exp(rng.normal(0, speed_sd, P)), 0.001, 1.0)
    f = np.minimum(f * milestone / np.average(f, weights=N.sum(axis=1)), 1.0)
    eps = (rng.normal(0, bias_nat, (1, K)) + rng.normal(0, bias_prov, (P, K))) * np.sqrt(4 * f * (1 - f))[:, None]
    counted = np.clip(f[:, None] * N * (1 + eps), 0, N)
    return counted, f


def evaluate(st: es.Settings, cases, seeds, bias_nat, bias_prov, draws=300, milestones=MILESTONES) -> pd.DataFrame:
    rows = []
    for ci, (prior_date, target_date) in enumerate(cases):
        units, final, target = make_case(prior_date, target_date)
        tr = truth(final, target)
        for seed in range(seeds):
            rng = np.random.default_rng(1000 * ci + seed)
            for m in milestones:
                counted, f = simulate_counts(final, m, rng, bias_nat, bias_prov, st.cera_frac)
                cnt = pd.DataFrame(counted, index=units.index, columns=list(LANES))
                cnt["f"] = f
                p = es.project(units, cnt, st, LANES)
                d = es.simulate(p, es.Settings(**{**st.__dict__, "draws": draws}), 7 + seed)
                share = d.share
                key = d.seats * 1e10 + d.votes
                for i, lane in enumerate(LANES):
                    if lane not in TOP and lane != "LEFT":
                        continue
                    lo, hi = np.quantile(share[:, i], [.05, .95])
                    rank = (key > key[:, i:i + 1]).sum(axis=1) + 1
                    rows.append({"prior": prior_date, "target": target_date, "seed": seed, "milestone": m, "lane": lane,
                                 "counted": p.counted_frac, "err": share[:, i].mean() - tr["share"][i],
                                 "cover": float(lo <= tr["share"][i] <= hi), "width": float(hi - lo),
                                 "rank_true": tr["rank"][lane],
                                 **{f"P{r}": float(np.mean(rank == r)) for r in (1, 2, 3)}})
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> dict:
    top = df[df["lane"].isin(TOP)]
    brier = np.mean([(r[f"P{int(r.rank_true)}"] - 1) ** 2 if r.rank_true <= 3 else sum(r[f"P{k}"] ** 2 for k in (1, 2, 3))
                     for _, r in top.iterrows()])
    late = top[top["milestone"] >= 0.2]
    return {"mae_pts": float(100 * top["err"].abs().mean()), "mae_pts_from_20pct": float(100 * late["err"].abs().mean()),
            "coverage90": float(top["cover"].mean()), "coverage90_from_20pct": float(late["cover"].mean()),
            "width90_pts_from_20pct": float(100 * late["width"].mean()),
            "rank_brier": float(brier), "n": int(len(top))}


def carry_forward_sd(cases) -> float:
    """RMS log error of a lane's national share when the previous election is carried forward (lanes >= 5% before)."""
    errs = []
    for prior_date, target_date in cases:
        units, final, _ = make_case(prior_date, target_date)
        a = (units[list(LANES)].to_numpy() * units["V0"].to_numpy()[:, None]).sum(axis=0)
        b = final.sum(axis=0)
        for k in (LANES.index(x) for x in ("PP", "PSOE", "VOX", "LEFT")):
            if a[k] / a.sum() >= 0.05 and b[k] > 0:
                errs.append(np.log(b[k] / b.sum()) - np.log(a[k] / a.sum()))
    return float(np.sqrt(np.mean(np.square(errs))))


def real_snapshot_bias() -> dict:
    """Relative error of the counted shares at 69% of tables vs the night's final (excl. abroad), from the real snapshot."""
    s = json.loads(SNAP.read_text())
    part, fin = s["partial"], s["final_night"]
    out = {}
    for lane in ("PP", "PSOE", "VOX", "LEFT"):
        out[lane] = (part[lane] / part["total_parties"]) / (fin[lane] / fin["total_parties"]) - 1
    f = s["tables_counted_share"]
    rms = float(np.sqrt(np.mean(np.square(list(out.values())))))
    return {"relative_bias": out, "tables_counted_share": f, "rms": rms, "bias_nat": rms / float(np.sqrt(4 * f * (1 - f)))}


def choose(cases, seeds, bias_nat, bias_prov):
    """Pick settings on `cases` only: first how much to trust the counted votes (smallest counted-share error),
    then the error model (narrowest 90% interval that still covers >= 90% from 20% counted)."""
    pre = carry_forward_sd(cases)
    base = es.Settings(precount_sd=pre)
    grid = []
    for own, swing in itertools.product((1e4, 3e4, 1e5, 3e5, 1e6), (2.5e4, 6e4, 1.5e5, 4e5, 1e6)):
        st = es.Settings(**{**base.__dict__, "own_votes": own, "swing_votes": swing, "sd_votes": swing})
        s = summary(evaluate(st, cases, seeds, bias_nat, bias_prov))
        grid.append({"own_votes": own, "swing_votes": swing, **s})
        print("grid", {k: round(v, 4) if isinstance(v, float) else v for k, v in grid[-1].items()}, flush=True)
    best = min(grid, key=lambda g: g["mae_pts_from_20pct"])
    st = es.Settings(**{**base.__dict__, "own_votes": best["own_votes"], "swing_votes": best["swing_votes"]})
    errs = []
    for mult, sdv in itertools.product((0.5, 1, 2), (1e4, 3e4, 1e5, 3e5, 1e6)):
        st2 = es.Settings(**{**st.__dict__, "tau_nat": 0.03 * mult, "tau_prov": 0.06 * mult, "sd_votes": sdv})
        s = summary(evaluate(st2, cases, seeds, bias_nat, bias_prov))
        errs.append({"mult": mult, "tau_nat": st2.tau_nat, "tau_prov": st2.tau_prov, "sd_votes": sdv, **s})
        print("error model", {k: round(v, 4) if isinstance(v, float) else v for k, v in errs[-1].items()}, flush=True)
    ok = [t for t in errs if t["coverage90_from_20pct"] >= 0.9]
    chosen = min(ok, key=lambda t: t["width90_pts_from_20pct"]) if ok else max(errs, key=lambda t: t["coverage90_from_20pct"])
    final = es.Settings(**{**st.__dict__, "tau_nat": chosen["tau_nat"], "tau_prov": chosen["tau_prov"], "sd_votes": chosen["sd_votes"]})
    return final, {"carry_forward_sd": pre, "grid": grid, "best_trust": best, "error_models": errs, "chosen": chosen}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seeds", type=int, default=6)
    a = ap.parse_args()
    real = real_snapshot_bias()
    bias_nat = bias_prov = real["bias_nat"]
    print("real-order bias:", json.dumps(real, indent=1), flush=True)
    fit, hold = TRANSITIONS[:2], TRANSITIONS[2:]
    st_fit, log_fit = choose(fit, a.seeds, bias_nat, bias_prov)
    holdout = {}
    for name, bn in (("bias_x0.5", 0.5), ("bias_x1", 1.0), ("bias_x2", 2.0)):
        holdout[name] = summary(evaluate(st_fit, hold, a.seeds * 2, bias_nat * bn, bias_prov * bn, draws=600))
        print("holdout 2023", name, holdout[name], flush=True)
    infit = summary(evaluate(st_fit, fit, a.seeds * 2, bias_nat, bias_prov, draws=600))
    st_all, log_all = choose(TRANSITIONS, a.seeds, bias_nat, bias_prov)
    out = {"real_snapshot_bias": real, "fit_2016_2019": {"settings": st_fit.__dict__, **log_fit},
           "holdout_2023": holdout, "in_sample_2019": infit,
           "frozen_2026": {"settings": st_all.__dict__, **log_all}}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
