"""Stage 2 of the model review: choose settings on other nights only and score the held-out night (frame level).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_loeo.py

Reads $BRUNDER_DATA/results/model_review/frames_*.parquet (analysis/model_review_frames.py). For a held-out night X the
settings are chosen on the training nights only:
  * "loeo"          : every other night (leave one election out; later nights are in the training set, so this tests the
                      method's robustness, it is not a forward test);
  * "chronological" : only nights strictly before X (no look-ahead; empty for the first night, which is then skipped).
Selection rule (fixed before the run, the same for every model family):
  feasible(config, tau) = pooled 90% coverage >= 0.90 (mean over training nights, each night weighted equally) and worst
                          training night >= 0.75 (the repo's rule, docs/brazil.md / docs/mexico.md);
  rule "repo"  : base family only, rho 0.8 / kappa 50,000: smallest feasible tau, then (rho, kappa) of lowest mean absolute
                 error at that tau (the rule the repo's v2 replays use);
  rule "sharp" : any family: among the feasible (config, tau) the one with the smallest mean projected-share sd (the
                 narrowest band that still covers); ties by lower MAE. If nothing is feasible: largest tau of the grid, the
                 config of lowest MAE (disclosed).
Coverage = share of frames with 1% < counted <= 95% whose 90% band (|final - projected| < 1.645 sd) holds the final share of
the official count. Frames: first minute at which 1, 2, 3, 5, ... 95% of the votes are counted (Poland: every archived
version). Output: results/model_review/loeo_summary.csv, loeo_choices.csv (SSD) and a markdown summary on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_common import OUT  # noqa: E402

TAUS = (0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
DATES = {"mx2012": "2012-07-01", "mx2018": "2018-07-01", "br2022r1": "2022-10-02", "br2022r2": "2022-10-30", "mx2024": "2024-06-02",
         "pl2025r1": "2025-05-18", "pl2025r2": "2025-06-01", "br2026r1": "2026-10-04", "br2026r2": "2026-10-25"}
KEY = ["cov", "slope_sd", "rho", "kappa"]
COVERAGE_MIN, WORST_MIN = 0.90, 0.75           # the repo's rule; main() also reports a sensitivity over WORST_MIN


def load() -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(p) for p in sorted(OUT.glob("frames_*.parquet"))], ignore_index=True)
    d = d[(d["counted"] > 0.01) & (d["counted"] <= 0.95)].copy()
    d["slope_sd"] = d["slope_sd"].fillna(0.0)
    d["total"] = d["margin_sd0"] / (2 * d["share_sd0"])
    d["twoR"] = 2 * d["total"] * (1 - d["counted"])
    return d


def at_tau(d: pd.DataFrame, tau: float, target: str = "final") -> pd.DataFrame:
    sd = np.sqrt(d["margin_sd0"] ** 2 + d["twoR"] ** 2 * tau**2) / (2 * d["total"])
    err = d[target] - d["share_hat"]
    return d.assign(tau=tau, sd=sd, z=err / sd, err=err)


def grid_stats(d: pd.DataFrame, target: str = "final") -> pd.DataFrame:
    """One row per night x config x tau: coverage (covg), MAE (share units), mean sd, mean z, frames."""
    parts = []
    for tau in TAUS:
        t = at_tau(d, tau, target)
        t["hit"] = (t["z"].abs() < 1.645).astype(float)
        t["abserr"] = t["err"].abs()
        g = t.groupby(["night"] + KEY).agg(covg=("hit", "mean"), mae=("abserr", "mean"), sd=("sd", "mean"), z=("z", "mean"),
                                           n=("z", "size")).reset_index()
        parts.append(g.assign(tau=tau))
    return pd.concat(parts, ignore_index=True)


def pooled(g: pd.DataFrame, nights: list[str]) -> pd.DataFrame:
    """Training statistics over `nights`, each night weighted equally."""
    s = g[g["night"].isin(nights)]
    return s.groupby(KEY + ["tau"]).agg(pcov=("covg", "mean"), worst=("covg", "min"), mae=("mae", "mean"), sd=("sd", "mean")).reset_index()


FAMILIES = {"none": ["none"], "size": ["size"], "lean": ["lean"], "order": ["size", "lean"]}


def pick(P: pd.DataFrame, family: str, rule: str, worst_min: float = WORST_MIN) -> pd.Series:
    Q = P[P["cov"].isin(FAMILIES[family])]
    feas = Q[(Q["pcov"] >= COVERAGE_MIN) & (Q["worst"] >= worst_min)]
    if rule == "repo":
        base = feas[(feas["rho"] == 0.8) & (feas["kappa"] == 50_000.0)]
        if len(base):
            return pd.concat([feas[feas["tau"] == base["tau"].min()].sort_values("mae").iloc[0], pd.Series({"feasible": True})])
    elif len(feas):
        return pd.concat([feas.sort_values(["sd", "mae"]).iloc[0], pd.Series({"feasible": True})])
    last = Q[Q["tau"] == Q["tau"].max()].sort_values("mae").iloc[0]
    return pd.concat([last, pd.Series({"feasible": False})])


def run(d: pd.DataFrame, worst_min: float):
    nights = sorted(d["night"].unique(), key=lambda n: DATES[n])
    G = {"final": grid_stats(d, "final")}
    G["alt_final"] = grid_stats(d[d["night"] == "mx2024"], "alt_final")
    summ, choices = [], []
    for target_name, target in (("final", "final"), ("prep_end", "alt_final")):
        for X in (nights if target == "final" else ["mx2024"]):
            for scheme in ("loeo", "chronological"):
                train = [n for n in nights if n != X] if scheme == "loeo" else [n for n in nights if DATES[n] < DATES[X]]
                if len(train) < 2:
                    continue
                P = pooled(G["final"], train)                # training is always scored against the official finals
                held = G[target][G[target]["night"] == X]
                for rule, family in (("repo", "none"), ("sharp", "none"), ("sharp", "size"), ("sharp", "lean"), ("sharp", "order")):
                    c = pick(P, family, rule, worst_min)
                    h = held[(held["cov"] == c["cov"]) & (held["slope_sd"] == c["slope_sd"]) & (held["rho"] == c["rho"]) &
                             (held["kappa"] == c["kappa"]) & (held["tau"] == c["tau"])].iloc[0]
                    choices.append({"target": target_name, "held_out": X, "scheme": scheme, "rule": rule, "family": family, "n_train": len(train),
                                    "covariate": c["cov"], "slope_sd": c["slope_sd"], "rho": c["rho"], "kappa": c["kappa"], "tau": c["tau"],
                                    "train_cov": c["pcov"], "train_worst": c["worst"], "train_mae_pt": 100 * c["mae"], "feasible": c["feasible"]})
                    summ.append({"target": target_name, "held_out": X, "scheme": scheme, "rule": rule, "family": family,
                                 "coverage": h["covg"], "mae_pt": 100 * h["mae"], "mean_sd_pt": 100 * h["sd"], "mean_z": h["z"], "n_frames": h["n"]})
    return pd.DataFrame(summ), pd.DataFrame(choices)


def main() -> None:
    d = load()
    nights = sorted(d["night"].unique(), key=lambda n: DATES[n])
    S, C = run(d, WORST_MIN)
    S.to_csv(OUT / "loeo_summary.csv", index=False)
    C.to_csv(OUT / "loeo_choices.csv", index=False)
    sens = []
    for w in (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9):
        Sw, _ = run(d, w)
        sens.append(Sw.assign(worst_min=w))
    SW = pd.concat(sens)
    SW.to_csv(OUT / "loeo_worst_sensitivity.csv", index=False)
    sw = SW[SW["target"] == "final"].groupby(["worst_min", "scheme", "family", "rule"]).agg(coverage=("coverage", "mean"), min_cov=("coverage", "min"), mae_pt=("mae_pt", "mean"), sd_pt=("mean_sd_pt", "mean")).round(3)
    print(sw.to_string())
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    m = S[S["target"] == "final"]
    agg = m.groupby(["scheme", "rule", "family"]).agg(nights=("held_out", "nunique"), coverage=("coverage", "mean"), min_cov=("coverage", "min"),
                                                      mae_pt=("mae_pt", "mean"), sd_pt=("mean_sd_pt", "mean"), z=("mean_z", "mean")).round(3)
    print(agg.to_string())
    for col in ("coverage", "mae_pt", "mean_sd_pt", "mean_z"):
        print(col)
        print(m.pivot_table(index=["scheme", "rule", "family"], columns="held_out", values=col)[[n for n in nights if n in set(m["held_out"])]].round(2).to_string())
    print("choices (tau)")
    cc = C[C["target"] == "final"]
    print(cc.pivot_table(index=["scheme", "rule", "family"], columns="held_out", values="tau")[[n for n in nights if n in set(cc["held_out"])]].to_string())
    print(cc[cc["family"].isin(["order", "none"]) & (cc["rule"] == "sharp")][["held_out", "scheme", "family", "covariate", "slope_sd", "rho", "kappa", "tau", "train_cov", "train_worst", "feasible"]].to_string(index=False))


if __name__ == "__main__":
    main()
