"""Calibrate the nowcast's systematic-error parameter τ on the 2026 1st-round count, check on the runoff.

For each race (Brazil + each state) and checkpoint (first moment the reconstructed count passes
10/25/50/75% of votes) compute z = (final share − projected share) / projected sd.
A calibrated τ gives E[z²] ≈ 1 and ~95% of |z| < 1.96.

R1 race: Lula share of all valid votes; prior = Lula's 2022 R1 share per municipality.
R2 race: Lula share of the two-party vote; prior = 2026 R1 two-party share (as in r2_night.py).

Run: uv run python analysis/calibrate_tau.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import nowcast, tse_open
from brunder.paths import PROCESSED, ROOT

LULA, FLAVIO = "13", "22"
TAUS = (0.0, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03)
LEVELS = (0.10, 0.25, 0.50, 0.75)
OUT = ROOT / "results"


def load(ele: int):
    d = PROCESSED / "ele2026" / f"e{ele}_c1"
    return pd.read_parquet(d / "votes.parquet"), pd.read_parquet(d / "progress.parquet")


def checkpoints(final, completion, prior, release, races) -> list[tuple[str | None, float, pd.Timestamp]]:
    """(race uf, level, time) where the reconstructed counted share first reaches each level."""
    fin = final.merge(completion, on=["uf", "cd_mun"]).merge(prior[["uf", "cd_mun"]], on=["uf", "cd_mun"])
    fin["visible"] = fin["last_update_utc"].clip(lower=release)
    out = []
    for uf in races:
        g = fin if uf is None else fin[fin["uf"] == uf]
        g = g.sort_values("visible")
        cum = g["n"].cumsum() / g["n"].sum()
        for lv in LEVELS:
            out.append((uf, lv, g.loc[cum >= lv, "visible"].iloc[0]))
    return out


def evaluate(name, final, completion, prior, release, actual) -> pd.DataFrame:
    races = [None] + sorted(u for u in prior["uf"].unique() if u != "zz")
    cps = checkpoints(final, completion, prior, release, races)
    states = nowcast.reconstruct_states(final, completion, pd.DatetimeIndex(sorted({t for *_, t in cps})), release)
    rows = []
    for tau in TAUS:
        p = nowcast.Params(tau=tau)
        for uf, lv, t in cps:
            r = nowcast.nowcast(states[t], prior, p, uf)
            if not r["share_sd"] > 0:      # race fully visible at its checkpoint: nothing to forecast
                continue
            rows.append({"round": name, "tau": tau, "race": uf or "br", "level": lv, "time": t,
                         "counted": r["counted_frac"], "share_hat": r["share_hat"], "share_sd": r["share_sd"],
                         "z": (actual[uf or "br"] - r["share_hat"]) / r["share_sd"]})
    return pd.DataFrame(rows)


def actual_shares(final: pd.DataFrame) -> dict[str, float]:
    g = final.groupby("uf")[["a", "n"]].sum()
    out = (g["a"] / g["n"]).to_dict()
    out["br"] = final["a"].sum() / final["n"].sum()
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    r1_votes, r1_prog = load(6257)
    r2_votes, r2_prog = load(6258)
    comp = lambda prog: prog[prog["level"] == "mun"][["uf", "cd_mun", "last_update_utc"]].dropna()

    # R1: prior from 2022 R1, Lula / all valid
    y22 = tse_open.votes_by_municipality(2022)
    prior1 = nowcast.prior_from_final(y22[y22["round"] == 1], LULA)
    final1 = nowcast.state_from_votes(r1_votes, LULA)
    res1 = evaluate("R1", final1, comp(r1_prog), prior1, pd.Timestamp("2026-10-04 20:00", tz="UTC"),
                    actual_shares(final1))

    # R2: prior from 2026 R1 two-party share
    r1_mu = r1_votes[r1_votes["level"] == "mu"][["uf", "cd_mun", "cand_n", "votes"]]
    prior2 = nowcast.prior_from_final(r1_mu, LULA, FLAVIO)
    final2 = nowcast.state_from_votes(r2_votes, LULA)
    res2 = evaluate("R2", final2, comp(r2_prog), prior2, pd.Timestamp("2026-10-25 22:00", tz="UTC"),
                    actual_shares(final2))

    res = pd.concat([res1, res2])
    res.to_csv(OUT / "tau_calibration.csv", index=False)
    summ = res.groupby(["round", "tau"]).agg(
        mean_z2=("z", lambda z: float(np.mean(z**2))),
        cover95=("z", lambda z: float(np.mean(np.abs(z) < 1.96))),
        mean_z=("z", "mean"), n=("z", "size"))
    nat = res[res["race"] == "br"].pivot_table(index=["round", "tau"], columns="level", values="z")
    nat.columns = [f"z_br@{int(c * 100)}%" for c in nat.columns]
    summ = summ.join(nat)
    pd.set_option("display.width", 220)
    print(summ.round(3).to_string())
    best = summ.loc["R1"].assign(err=lambda d: (d["mean_z2"] - 1).abs())["err"].idxmin()
    print(f"\nR1-calibrated τ (E[z²]≈1): {best}")
    summ.to_csv(OUT / "tau_calibration_summary.csv")


if __name__ == "__main__":
    main()
