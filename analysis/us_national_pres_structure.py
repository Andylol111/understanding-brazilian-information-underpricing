"""Presidential poll-error structure from 2000-2020 (never 2024): cycle-common, region x cycle, persistent state and
state x cycle sds of the state poll-average error (two-party Democratic share, points) — the presidential analogue of
docs/us_poll_error.md's Senate/governor decomposition, used by analysis/us_national_2024.py (variant 'presidential').

  BRUNDER_DATA=… uv run python analysis/us_national_pres_structure.py
Poll average: weight sqrt(n) x 0.5^(age/14 d), polls within 45 days, certified result = the file's cand*_actual."""
from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import us_national as N
from brunder.paths import RAW

POLLS = RAW / "us" / "polls" / "538_raw_polls.csv"


def state_errors() -> pd.DataFrame:
    d = pd.read_csv(POLLS, low_memory=False)
    d = d[(d["type_simple"] == "Pres-G") & (d["location"] != "US") & d["location"].str.len().eq(2)].copy()
    d["polldate"], d["electiondate"] = pd.to_datetime(d["polldate"]), pd.to_datetime(d["electiondate"])
    dem1 = d["cand1_party"] == "DEM"
    dem = np.where(dem1, d["cand1_pct"], d["cand2_pct"])
    rep = np.where(dem1, d["cand2_pct"], d["cand1_pct"])
    act_d = np.where(dem1, d["cand1_actual"], d["cand2_actual"])
    act_r = np.where(dem1, d["cand2_actual"], d["cand1_actual"])
    ok = ((d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")) | ((d["cand1_party"] == "REP") & (d["cand2_party"] == "DEM"))
    d = d.assign(p=dem / (dem + rep), a=act_d / (act_d + act_r), age=(d["electiondate"] - d["polldate"]).dt.days)[ok]
    d = d[(d["age"] >= 0) & (d["age"] <= 45) & d["p"].notna() & d["a"].notna()]
    d["w"] = np.sqrt(d["samplesize"].fillna(400).clip(lower=100)) * 0.5 ** (d["age"] / 14)
    d["cycle"] = d["electiondate"].dt.year
    g = d.groupby(["cycle", "location"]).apply(
        lambda x: pd.Series({"avg": np.average(x["p"], weights=x["w"]), "act": x["a"].iloc[0], "n": len(x)}),
        include_groups=False).reset_index()
    g["err"] = g["act"] - g["avg"]
    g["state"] = g["location"].str.lower()
    return g[(g["n"] >= 2) & (g["cycle"] <= 2020)]


def structure(g: pd.DataFrame | None = None) -> dict:
    g = state_errors() if g is None else g
    g = g.assign(region=g["state"].map(N.REGION))
    c = g.groupby("cycle")["err"].mean()
    g["r1"] = g["err"] - g["cycle"].map(c)
    rg = g.groupby(["cycle", "region"])["r1"].transform("mean")
    g["r2"] = g["r1"] - rg
    # persistent state component: mean of a state's residual across cycles, bias-corrected for its sampling noise
    sm = g.groupby("state")["r2"].agg(["mean", "count", "var"]).dropna()
    within = float(g["r2"].var())
    persist = max(float((sm["mean"] ** 2).mean() - (sm["var"] / sm["count"]).mean()), 0.0)
    n_reg_cells = g.groupby(["cycle", "region"]).size()
    region_var = max(float((g.groupby(["cycle", "region"])["r1"].mean() ** 2).mean()
                           - (g["r2"].var() / n_reg_cells.mean())), 0.0)
    return {"cycles": sorted(map(int, c.index)), "n_state_cycles": int(len(g)), "cycle_means_pts": (100 * c).round(2).to_dict(),
            "sd_common": float(np.sqrt((c ** 2).mean())), "sd_region": float(np.sqrt(region_var)),
            "sd_state_persistent": float(np.sqrt(persist)), "sd_state_cycle_resid": float(np.sqrt(max(within - persist, 0))),
            "sd_total": float(np.sqrt((g["err"] ** 2).mean()))}


if __name__ == "__main__":
    import json
    print(json.dumps(structure(), indent=1))
