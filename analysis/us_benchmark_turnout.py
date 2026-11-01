"""Benchmark part C: is the map's expected county turnout (brunder.us.turnout_profile -> turnout_exp) reliable?

Recomputes, for every statewide governor / Senate race of 2022 and 2024 that the unified panel holds (MEDSL certified county
returns, CC0), the expected two-party votes per county the way the live page would have shown them BEFORE the count, from
pre-election inputs only, and compares with the certified county totals:

  v1 flat   : the registered rule. Midterm (2022): last same-office midterm race (2018) x turnout_ratio 1.0 (counties without it,
              or races where < 90 % of counties have it: 2020 president x 0.72, the config rule). Presidential year (2024):
              last president (2020) x 1.0.
  map       : brunder.us.turnout_profile applied to that prior: v1 x adj, adj = the county's own midterm top-race / president
              drop-off relative to its state (2018 top race / 2016 president for 2022 = the leave-one-cycle-out analogue of the
              published 2022/2020 index), rescaled so the state total is unchanged. Counties without an own ratio get adj = 1
              (the live index uses a demographic model there; not refitted here). 2024: the index is a midterm index, adj = 1.
  v4        : analysis/us_model_analysis.expected_votes: 2020 president two-party votes x own 2018/2016 drop-off (2022 only).
  unified   : brunder.us_unified.county_prior V with leave-one-cycle-out fits exactly as analysis/us_unified_backtest.county_backtest
              (state total predict_vstate, county shares fit_turnout(TURN_FEATS) fitted on the other cycles).

Only brunder.us.turnout_profile is imported from the registered code (called, not copied). Output: data_public/us_benchmark_turnout.csv
(county level) and tables on stdout (fragment in local://bench_C.md). Deterministic; no randomness.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_benchmark_turnout.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_model_analysis as uma                  # noqa: E402  (expected_votes: the v4 recipe)
import us_unified_backtest as bt                 # noqa: E402  (LOCO fits, state_base_votes / fit_vstate / predict_vstate)
from brunder import us, us_unified as uu         # noqa: E402

OUT_CSV = uu.ROOT / "data_public" / "us_benchmark_turnout.csv"
MODELS = ["v1", "map", "v4", "unified"]
LABEL = {"v1": "v1 flat rule", "map": "map (turnout_profile)", "v4": "v4 template", "unified": "unified (LOCO)"}
REG_STATES = {"az", "ca", "fl", "ga", "mn", "nc", "ne", "nm", "oh", "pa", "sc", "tx", "vt"}   # states of the registered v3 configs
# thresholds fixed in advance
T_MED, T_STATE = 10.0, 5.0     # median county |error| < 10 %, mean |statewide level error| < 5 %


def own_dev(rc: pd.DataFrame, top_year: int, pres_year: int) -> pd.Series:
    """unit fips -> log(top-race votes / president votes) minus the state's pres-weighted mean (the index recipe), capped +-0.5."""
    top = rc[(rc["year"] == top_year) & rc["contest"].isin(["sen", "gov"])].sort_values("contest", ascending=False)  # sen first
    top = top.drop_duplicates("fips").set_index("fips")["v2"]
    pr = rc[(rc["year"] == pres_year) & (rc["contest"] == "pres")].drop_duplicates("fips").set_index("fips")
    d = pd.DataFrame({"top": top, "pres": pr["v2"], "st": pr["st"]}).dropna()
    d = d[(d["top"] > 0) & (d["pres"] > 0)]
    lr = np.log(d["top"] / d["pres"])
    dev = lr - lr.groupby(d["st"]).transform(lambda x: np.average(x, weights=d.loc[x.index, "pres"]))
    return dev.clip(-0.5, 0.5), d


def main() -> None:
    inp = uu.load_inputs()
    rc = inp["rc"]
    R = uu.race_table(inp)
    C = bt.prep_county(inp, R)
    gb = bt.state_base_votes(C)
    dev18, d18 = own_dev(rc, 2018, 2016)
    top18, pres16 = d18["top"], d18["pres"]
    tmp = Path(tempfile.mkdtemp())
    idx = pd.DataFrame({"unit_id": [uu.unit_id(s, f) for f, s in zip(dev18.index, d18.loc[dev18.index, "st"])],
                        "adj": np.exp(dev18.to_numpy()), "lowprop_index": 0.0})
    idx.to_csv(tmp / "idx2022.csv", index=False)
    rows = []
    for c in (2022, 2024):
        tr = C[(C["t"] != c) & (C["t"].isin(bt.TEST))]
        te = C[C["t"] == c]
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = bt.fit_vstate(gb[gb["t"] != c])
        us.TURNOUT_INDEX = tmp / "idx2022.csv" if c == 2022 else tmp / "none.csv"
        for race, d in te.groupby("race", sort=True):
            d = d.copy()
            st, office, mid = d["st"].iloc[0], d["office"].iloc[0], c % 4 == 2
            ids = [uu.unit_id(st, f) for f in d["fips"]]
            act = d["v2"].to_numpy(float)
            vp = d["vp_b"].to_numpy(float)
            # v1 flat rule
            if mid:
                prev = rc[(rc["year"] == 2018) & (rc["contest"] == d["office"].iloc[0]) & (rc["st"] == st)].drop_duplicates("fips").set_index("fips")["v2"]
                vo = d["fips"].map(prev).to_numpy(float)
                if np.isfinite(vo).mean() >= 0.9:
                    v1 = np.where(np.isfinite(vo), vo, vp * 0.72)
                else:
                    v1 = vp * 0.72
            else:
                v1 = vp.copy()
            ok = np.isfinite(v1) & np.isfinite(vp) & (act > 0)
            # map: turnout_profile on the v1 prior
            pri = pd.DataFrame({"unit_id": ids, "V": v1})
            tp = us.turnout_profile({"count": {"turnout_ratio": 1.0}}, pri)
            mp = tp["turnout_exp"].to_numpy(float)
            # v4 (midterm only): 2020 president x own 2018/2016 drop-off, state flat ratio otherwise
            if mid:
                f = d["fips"]
                ev = uma.expected_votes(pd.Series(vp, index=f), f.map(top18).reset_index(drop=True).set_axis(f), 
                                        f.map(pres16).reset_index(drop=True).set_axis(f), pd.Series(st, index=f))
                v4 = ev.to_numpy(float)
            else:
                v4 = np.full(len(d), np.nan)
            # unified (LOCO)
            Vs = bt.predict_vstate(vm, float(np.nansum(vp)), float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None,
                                   mid, bool(d["oy"].max() % 4 == 2))
            un = (Vs * uu.predict_turnout_share(tm, d)).to_numpy(float)     # = county_prior(...)["V"]: the share model does not enter V
            for i in range(len(d)):
                if not ok[i]:
                    continue
                rows.append({"race": race, "year": c, "cycle_type": "midterm" if mid else "presidential", "state": st, "office": office,
                             "fips": d["fips"].iloc[i], "actual": act[i], "v1": v1[i], "map": mp[i], "v4": v4[i], "unified": un[i]})
    D = pd.DataFrame(rows)
    # QC: drop races whose county certified total disagrees with the panel's statewide total by > 2 % (county panel defects)
    rs = inp["rs"]
    rs = rs[rs["year"].isin([2022, 2024])].assign(race=lambda x: x["year"].astype(str) + "_" + x["st"] + "_" + x["contest"].str[:3] + x["contest"].str.contains("special").astype(int).astype(str))
    stv = rs.drop_duplicates("race").set_index("race")["v2"]
    chk = D.groupby("race")["actual"].sum() / D["race"].map(stv).groupby(D["race"]).first()
    bad = chk[(chk - 1).abs() > 0.02].index.union(chk[chk.isna()].index)
    print("QC: excluded races (county sum vs statewide v2 off by > 2 % or no statewide total):", sorted(bad))
    D = D[~D["race"].isin(bad)].reset_index(drop=True)
    # Level of each prediction on the same county set; state totals are what each model's own total is on that set.
    out = D.copy()
    for m in MODELS:
        out["err_" + m] = 100 * (out[m] / out["actual"] - 1)
        out["rank_worst_" + m] = out.assign(a=out["err_" + m].abs()).groupby("race")["a"].rank(ascending=False, method="first")
    for m in MODELS:
        out[m] = out[m].round(0)
        out["err_" + m] = out["err_" + m].round(2)
    out["actual"] = out["actual"].round(0)
    with open(OUT_CSV, "w") as fh:
        fh.write("# Expected two-party votes per county before the count vs MEDSL certified county returns (CC0), 2022 and 2024 governor/Senate races; "
                 "pre-election inputs only, unified fitted leave-one-cycle-out. err_* = predicted / actual - 1 in %. analysis/us_benchmark_turnout.py\n")
        out.to_csv(fh, index=False)
    report(D)


def metrics(g: pd.DataFrame, m: str) -> dict:
    g = g[g[m].notna()]
    if g.empty:
        return {}
    e = (g[m] / g["actual"] - 1).abs() * 100
    per = g.groupby("race").agg(p=(m, "sum"), a=("actual", "sum"))
    lev = 100 * (per["p"] / per["a"] - 1)
    sh = g[m] / g.groupby("race")[m].transform("sum") - g["actual"] / g.groupby("race")["actual"].transform("sum")
    tv = 100 * sh.abs().groupby(g["race"]).sum() / 2
    rel = (sh / (g["actual"] / g.groupby("race")["actual"].transform("sum"))).abs() * 100
    return {"races": g["race"].nunique(), "counties": len(g), "median_ape": e.median(), "mean_ape": e.mean(),
            "vw_mape": 100 * (g[m] - g["actual"]).abs().sum() / g["actual"].sum(), "state_level_mae": lev.abs().mean(),
            "state_level_bias": lev.mean(), "share_median_rel_err": rel.median(), "share_TV": tv.mean(),
            "pass": bool(e.median() < T_MED and lev.abs().mean() < T_STATE)}


def report(D: pd.DataFrame) -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda x: f"{x:.2f}")
    subsets = {"pooled, all": D, "pooled, registered states": D[D["state"].isin(REG_STATES)],
               "2022 midterm": D[D["year"] == 2022], "2022 midterm, registered states": D[(D["year"] == 2022) & D["state"].isin(REG_STATES)],
               "2024 presidential year": D[D["year"] == 2024], "2024, registered states": D[(D["year"] == 2024) & D["state"].isin(REG_STATES)]}
    for name, g in subsets.items():
        t = pd.DataFrame({LABEL[m]: metrics(g, m) for m in MODELS}).T
        print(f"\n== {name} ({g['race'].nunique()} races, {len(g)} counties)\n", t.drop(columns=["pass"], errors="ignore").to_string())
    # worst 10 counties of the map model per race (registered states, 2022), compact: race, county, error
    w = D.assign(e=(D["map"] / D["actual"] - 1) * 100)
    print("\n== worst 10 counties of the map number, pooled over races (by |error|, counties with >= 5,000 actual votes)")
    ww = w[w["actual"] >= 5000].assign(a=lambda x: x["e"].abs()).sort_values("a", ascending=False).head(10)
    print(ww[["race", "fips", "actual", "map", "e"]].to_string(index=False))
    print("\n== by race: statewide level error % of the map / v1 / v4 / unified and median |county error| %")
    rows = []
    for race, g in D.groupby("race"):
        r = {"race": race}
        for m in MODELS:
            if g[m].notna().all():
                r["lvl_" + m] = 100 * (g[m].sum() / g["actual"].sum() - 1)
                r["med_" + m] = ((g[m] / g["actual"] - 1).abs() * 100).median()
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
