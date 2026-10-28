"""Pre-election calibration of the US county model on Georgia's certified results by vote type.

The US problem the Brazil model never faced: a county's votes arrive in batches of different vote types (early
in-person + mail first, Election Day precincts later) whose partisan lean differs, so early county returns are
biased estimates of the county's final share. Georgia's Secretary of State publishes every certified county
result split by vote type (Advanced Voting, Absentee by Mail, Election Day, Provisional), which lets us replay
realistic count orders for past Georgia races and check the model's uncertainty against the known final result.

Simulated night (per county): the early batch (Advanced + Absentee) is posted first, at a random time in the
first ~90 minutes (larger counties later); the Election Day + provisional vote follows in 4 chunks spread over
the next ~5 hours. The model sees only the posted votes and the "all precincts reported" flag after the last
chunk, exactly as count_state reads the GA feed. Checkpoints at 5/10/25/50/75/90 % of the final two-party vote.

Races (prior = most recent earlier statewide race, turnout ratio = the typical midterm↔presidential ratio,
not the realised one):
  2024 president  ← prior 2022 Senate,    turnout ratio 1.30
  2022 governor   ← prior 2020 president, turnout ratio 0.80
  2022 Senate     ← prior 2020 president, turnout ratio 0.80

Grid over the vote-type split (CountRules.vote_type; the early-vote split is in the feed rows), tau and rho, with
sigma_mun0 = 0.08; 30 simulated nights per race. Reports 90 % coverage (overall and the worst checkpoint), mean |z|,
mean absolute error of the projected share and the raw count's error. All data predate 2026-11-03 (no look-ahead).
Run after `brunder us priors` (about 15 minutes):
    uv run python analysis/us_calibrate.py
"""

from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

from brunder import us

RACES = [("ga_pres_2024", "2024NovGen", "pres", "ga_sen_2022", 1.30),
         ("ga_gov_2022", "2022NovGen", "gov", "ga_pres_2020", 0.80),
         ("ga_sen_2022", "2022NovGen", "sen", "ga_pres_2020", 0.80)]
CHECK = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90)
GRID = {"vote_type": (False, True), "tau": (0.02, 0.03, 0.04, 0.05, 0.06), "rho": (0.3, 0.6)}
SIGMA_MUN0 = 0.08
N_SIM = 30


def by_type(eid: str, race: str) -> pd.DataFrame:
    """unit_id, early_d, early_r, late_d, late_r (two-party votes by batch) from the cached GA ballot item."""
    rows = us.parse_ga((us.US_RAW / "ga" / f"{eid}_{race}.json").read_bytes())
    rows = rows[(rows["level"] == "county") & rows["party"].isin(["DEM", "REP"])]
    g = rows.pivot_table(index="unit_id", columns="party", values=["votes", "early"], aggfunc="sum")
    return pd.DataFrame({"early_d": g[("early", "DEM")], "early_r": g[("early", "REP")],
                         "late_d": g[("votes", "DEM")] - g[("early", "DEM")],
                         "late_r": g[("votes", "REP")] - g[("early", "REP")]}).reset_index()


def events(bt: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """One simulated night: (time, unit_id, d, r, early, last) batch postings."""
    size = (bt[["early_d", "early_r", "late_d", "late_r"]].sum(axis=1)).to_numpy()
    rank = size.argsort().argsort() / max(len(size) - 1, 1)            # 0 small … 1 large
    out = []
    for i, u in enumerate(bt["unit_id"]):
        t0 = rng.uniform(0, 60) + 30 * rank[i]
        out.append((t0, u, bt["early_d"].iat[i], bt["early_r"].iat[i], True, False))
        ts = np.sort(t0 + rng.uniform(30, 240 + 120 * rank[i], 4))
        w = rng.dirichlet(np.ones(4))
        for k, (t, wk) in enumerate(zip(ts, w)):
            out.append((t, u, bt["late_d"].iat[i] * wk, bt["late_r"].iat[i] * wk, False, k == 3))
    return pd.DataFrame(out, columns=["t", "unit_id", "d", "r", "early", "last"]).sort_values("t", ignore_index=True)


def rows_at(ev: pd.DataFrame, upto: int) -> pd.DataFrame:
    """Feed-style rows (as parse_ga gives them) for the batches posted so far."""
    e = ev.iloc[:upto].assign(ed=lambda x: x["d"] * x["early"], er=lambda x: x["r"] * x["early"])
    g = e.groupby("unit_id").agg(d=("d", "sum"), r=("r", "sum"), ed=("ed", "sum"), er=("er", "sum"),
                                 done=("last", "max"))
    base = {"level": "county", "race": "x", "ballots": np.nan, "registered": np.nan,
            "outstanding": np.nan, "units": 1.0, "name": "", "candidate": ""}
    rows = [pd.DataFrame({**base, "unit_id": g.index, "party": "DEM", "votes": g["d"], "early": g["ed"],
                          "reporting": g["done"] * 1.0}),
            pd.DataFrame({**base, "unit_id": g.index, "party": "REP", "votes": g["r"], "early": g["er"],
                          "reporting": g["done"] * 1.0})]
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    priors = us.load_priors()
    # the production Georgia [count] settings (configs/us2026_ga_*_v1.toml) except turnout_ratio, set per race
    cfg_base = {"election": {"state": "ga", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}},
                "prior": {}, "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95,
                                       "early_share": 0.64}}
    rng = np.random.default_rng(20261103)
    results = []
    for name, eid, race, prior_src, ratio in RACES:
        bt = by_type(eid, race)
        final = (bt["early_d"] + bt["late_d"]).sum() / bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        early_share = bt["early_d"].sum() / (bt["early_d"] + bt["early_r"]).sum()
        late_share = bt["late_d"].sum() / (bt["late_d"] + bt["late_r"]).sum()
        print(f"{name}: final D two-party {final:.4f}; early batch {early_share:.4f}, Election Day {late_share:.4f}")
        cfg = {**cfg_base, "prior": {"share": prior_src, "votes": prior_src},
               "count": {**cfg_base["count"], "turnout_ratio": ratio}}
        pri = us.race_prior(cfg, priors)
        total = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        for s in range(N_SIM):
            ev = events(bt, rng)
            cum = (ev["d"] + ev["r"]).cumsum() / total
            for c in CHECK:
                upto = int(np.searchsorted(cum.to_numpy(), c)) + 1
                rows = rows_at(ev, upto)
                for vt, tau, rho in itertools.product(*GRID.values()):
                    cfg_m = {**cfg, "model": {"tau": tau, "rho": rho, "sigma_mun0": SIGMA_MUN0},
                             "count": {**cfg["count"], "vote_type": vt}}
                    res, _ = us.project(rows, cfg_m, pri)
                    results.append({"race": name, "sim": s, "check": c, "vote_type": vt, "tau": tau, "rho": rho,
                                    "err": res["share_hat"] - final, "sd": res["share_sd"],
                                    "raw_err": res["raw"] - final, "P": res["p_win"], "win": final > 0.5})
    df = pd.DataFrame(results)
    df["z"] = df["err"] / df["sd"]
    df["cover90"] = df["z"].abs() < 1.645
    df["brier"] = (df["P"] - df["win"]) ** 2
    keys = list(GRID)
    summ = df.groupby(keys).agg(cover90=("cover90", "mean"), min_cover_check=("cover90", lambda s: s.groupby(
        df.loc[s.index, "check"]).mean().min()), mean_abs_z=("z", lambda z: z.abs().mean()),
        mae=("err", lambda e: e.abs().mean()), brier=("brier", "mean")).reset_index()
    pd.set_option("display.width", 200)
    print(summ.sort_values(["min_cover_check", "mae"], ascending=[False, True]).to_string(index=False))
    by_check = df.groupby(keys + ["check"]).agg(cover90=("cover90", "mean"), mae=("err", lambda e: e.abs().mean()),
                                                raw_mae=("raw_err", lambda e: e.abs().mean())).reset_index()
    out = us.US_PROC / "calibration_ga.csv"
    by_check.to_csv(out, index=False)
    print(f"per-checkpoint table: {out}")
    for vt, s in summ.groupby("vote_type"):
        ok = s[s["min_cover_check"] >= 0.85]
        best = (ok if len(ok) else s).sort_values(["mae", "tau"]).iloc[0]
        print(f"vote_type={vt}: chosen (smallest MAE, then smallest tau, among settings with ≥85% coverage at every "
              "checkpoint):", json.dumps({k: float(best[k]) for k in keys + ["cover90", "min_cover_check", "mae"]}))
        print(by_check[(by_check["vote_type"] == vt) & (by_check["tau"] == best["tau"])
                       & (by_check["rho"] == best["rho"])].to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
