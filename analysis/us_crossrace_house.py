"""Cross-race information, House districts: does a statewide race's county count improve the projection of a House district's race?

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_crossrace_house.py panel     # whole-county House districts, LOCO prior
    ... us_crossrace_house.py replay [--n 12] [--workers 9]                                          # separate vs joint, House lagging the top of the ticket
    ... us_crossrace_house.py tables

Districts made of whole counties only (every county they touch lies >= 90 % in the district; 98 % of the district's votes in such
counties), so the county pieces are counties and the statewide races' county deviations apply to them unchanged. Prior of a district:
the votes-weighted last presidential share of its counties plus the national House swing of the generic-ballot model fitted without the
held-out cycle (the registered House v2 idea; no district model, no polls), pieces s0 = county presidential share + swing, V = 0.78 (midterm)
or 1.0 (presidential year) times the county's last presidential two-party votes. The prior sd of the district share is the RMSE of that
rule on the other cycles' districts (>= 2 points). A deliberately simple, weak prior: it tests whether the statewide count carries
information about the district beyond what the district's own count has, not whether the House prior is good.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_crossrace as cr  # noqa: E402
import us_unified_backtest as bt  # noqa: E402
from brunder import us, us_unified as uu, us_unified_xrace as xr_  # noqa: E402

OUT = cr.OUT
HP = OUT / "house_panel.parquet"
CACHE = uu.U / "_cache" / "results"


def house_pieces() -> pd.DataFrame:
    """(t, st, district, fips): dem, rep votes of the November general House race from the MEDSL cache of analysis/us_unified_results.py
    (rule 'overlap': TOTAL rows dropped where they duplicate mode rows)."""
    parts = []
    for f in sorted(CACHE.glob("20*.parquet")):
        d = pd.read_parquet(f)
        d = d[(d["cls"] == "house") & (d["special"] == 0) & (d["stage"] == "GEN") & d["party"].isin(["DEMOCRAT", "REPUBLICAN"]) & d["fips"].notna()]
        if d.empty:
            continue
        d = d[~((d["is_total"] == 1) & d["overlap"])]
        parts.append(d.groupby(["year", "st", "district", "fips", "party"], as_index=False)["votes"].sum())
    h = pd.concat(parts, ignore_index=True)
    h = h.pivot_table(index=["year", "st", "district", "fips"], columns="party", values="votes", aggfunc="sum", fill_value=0.0).reset_index()
    return h.rename(columns={"DEMOCRAT": "dem", "REPUBLICAN": "rep", "year": "t"}).assign(st=lambda x: x["st"].str.lower())


def national_swing_loco() -> dict[int, float]:
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    base = n[n["cycle"] < 2026]
    out = {}
    for c in bt.TEST:
        nm = uu.fit_nat(base[base["cycle"] != c], (), 0.0)
        out[c] = float(uu.predict_nat(nm, base[base["cycle"] == c].iloc[0])[0])
    return out


def build_panel() -> pd.DataFrame:
    P = cr.load_panel()
    cty = P.drop_duplicates(["t", "st", "fips"])[["t", "st", "fips", "sp_b", "vp_b"]]
    h = house_pieces()
    h["fips"] = h["fips"].astype(str).str.zfill(5)
    h = h[(h["dem"] + h["rep"]) > 0]
    tot = h.groupby(["t", "st", "fips"])[["dem", "rep"]].transform("sum").sum(axis=1)
    h["cshare"] = (h["dem"] + h["rep"]) / tot
    h = h.merge(cty, on=["t", "st", "fips"], how="inner")                    # counties with a Senate / governor race and a presidential base
    rows = []
    for (t, st, dist), g in h.groupby(["t", "st", "district"]):
        if len(g) < 3 or (g["cshare"] < 0.9).any():
            continue
        v = g["dem"] + g["rep"]
        s2 = g["dem"] / v
        S = g["dem"].sum() / v.sum()
        if not 0.03 < S < 0.97 or v.sum() < 50_000:
            continue
        rows.append(g.assign(race=f"{t}_{st}_h{dist}", district=dist, s2=s2.values, v2=v.values, S_act=S))
    H = pd.concat(rows, ignore_index=True)
    sw = national_swing_loco()
    mid = {c: (c % 4 == 2) for c in bt.TEST}
    H["swing"] = H["t"].map(sw)
    H["Lc"] = H.groupby("race").apply(lambda g: np.average(g["sp_b"], weights=g["vp_b"]), include_groups=False).reindex(H["race"]).to_numpy()
    H["S_hat"] = H["Lc"] + H["swing"]
    H["s0"] = np.clip(H["sp_b"] + H["swing"], 0.005, 0.995)
    H["V"] = H["vp_b"] * np.where(H["t"].map(mid), 0.78, 1.0)
    # sd: RMSE of S_act - S_hat over the other cycles' districts (LOCO), floor 2 points
    err = H.drop_duplicates("race")[["race", "t", "S_act", "S_hat"]].assign(e=lambda x: x["S_act"] - x["S_hat"])
    sd = {c: max(float(np.sqrt((err[err["t"] != c]["e"] ** 2).mean())), 0.02) for c in bt.TEST}
    H["sd"] = H["t"].map(sd)
    H["office"] = "house"
    H["special"] = 0
    H["n_polls"] = 0
    OUT.mkdir(parents=True, exist_ok=True)
    H.to_parquet(HP, index=False)
    return H


def load_house() -> pd.DataFrame:
    return pd.read_parquet(HP) if HP.exists() else build_panel()


def house_pairs(P: pd.DataFrame, H: pd.DataFrame, t: int | None = None, exclude: int | None = None):
    """(partner statewide race frame, district frame, partner office, key) for every district with a regular Senate / governor race."""
    out = []
    for (tt, st, race), d in H.groupby(["t", "st", "race"]):
        if (t is not None and tt != t) or (exclude is not None and tt == exclude):
            continue
        for off in ("sen", "gov"):
            x = P[(P["t"] == tt) & (P["st"] == st) & (P["special"] == 0) & (P["office"] == off)]
            if len(x) and d["fips"].isin(x["fips"]).mean() > 0.95:
                out.append((x, d, off, (tt, st, race)))
    return out


def xr_house(P: pd.DataFrame, H: pd.DataFrame, held_out: int) -> dict:
    pr = [(x, d) for x, d, _, _ in house_pairs(P, H, exclude=held_out)]
    e = xr_.estimate_crossrace_pairs(pr)
    return {**e, "beta_ab": e["beta_a_on_b"], "beta_ba": e["beta_b_on_a"], "rho_n": e["rho_e"]}       # A = statewide race, B = House district


def corr_table(P: pd.DataFrame, H: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for lab, sel in (("all", None), ("2018", 2018), ("2022", 2022)):
        for off in ("sen", "gov", "both"):
            pr = [(x, d) for x, d, o, k in house_pairs(P, H, t=sel) if off in ("both", o)]
            if len(pr) >= 3:
                e = xr_.estimate_crossrace_pairs(pr)
                rows.append({"cycle": lab, "partner": off, "pairs": e["n_pairs"], "counties": e["n_counties"], "rho_s_raw": e["rho_s_raw"], "rho_e": e["rho_e"],
                             "beta_house_on_state": e["beta_b_on_a"], "beta_state_on_house": e["beta_a_on_b"], "sd_e_state_pts": 100 * e["sd_e_a"], "sd_e_house_pts": 100 * e["sd_e_b"]})
    return pd.DataFrame(rows)


def run_house(args: tuple) -> pd.DataFrame:
    """Nights of one (statewide race, House district) pair: both counted by county, House counties following the statewide count in the
    'lag' scenario; both races are evaluated, the House race is the one of interest."""
    x, d, off, key, xr, n_sim, seed, model = args
    t, st, race = key
    u = sorted(set(x["fips"]))
    ia = {f: i for i, f in enumerate(u)}
    xa = x.set_index("fips").loc[u]
    db = d.set_index("fips")
    uid = [uu.unit_id(st, f) for f in u]
    priA = pd.DataFrame({"unit_id": uid, "name": uid, "s0": xa["s0"].to_numpy(), "V": xa["V"].to_numpy()})
    priB = pd.DataFrame({"unit_id": [uu.unit_id(st, f) for f in db.index], "name": "", "s0": db["s0"].to_numpy(), "V": db["V"].to_numpy()})
    finA = (xa["s2"].to_numpy() * xa["v2"].to_numpy(), xa["v2"].to_numpy())
    finB = (db["s2"].to_numpy() * db["v2"].to_numpy(), db["v2"].to_numpy())
    idxB = np.array([ia[f] for f in db.index if f in ia])
    infoA = {"sd": float(xa["sd"].iloc[0])}
    infoB = {"sd": float(d["sd"].iloc[0])}
    cfgs = [{"election": {"state": st, "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {}, "count": dict(cr.COUNT),
             "model": {**model, "swing_prior_mean": 0.0, "swing_prior_sd": i["sd"]}} for i in (infoA, infoB)]
    rng = np.random.default_rng([seed, t, sum(map(ord, st)), int(d["district"].iloc[0] or 0) if str(d["district"].iloc[0]).isdigit() else 0])
    size = xa["v2"].to_numpy()
    totA, totB = finA[1].sum(), finB[1].sum()
    shareA, shareB = finA[0].sum() / totA, finB[0].sum() / totB
    out = []
    for scn in cr.SCENARIOS:
        for s in range(n_sim):
            ta = cr.event_times(size, rng)
            tbs = {"same": ta, "indep": cr.event_times(size, rng), "lag": ta + rng.uniform(30, 150, len(ta))}[scn]
            T = (ta, tbs[idxB])
            for k, race_nm, (fin, tot, share) in ((1, "house", (finB, totB, shareB)), (0, "state", (finA, totA, shareA))):
                order = np.argsort(T[k])
                cum = np.cumsum(fin[1][order]) / tot
                for c in cr.CHECKS:
                    i = int(np.searchsorted(cum, c))
                    if i >= len(order):
                        continue
                    tmin = float(T[k][order][i])
                    rows = [cr.feed_rows(uid, finA[0], finA[1], T[0] <= tmin),
                            cr.feed_rows(list(priB["unit_id"]), finB[0], finB[1], T[1] <= tmin)]
                    r0 = [us.project(rows[0], cfgs[0], priA), us.project(rows[1], cfgs[1], priB)]
                    models = [("separate", r0[k][0])]
                    for name, kw, xv in cr.variants(xr):
                        models.append((name, xr_.joint_project(rows[0], cfgs[0], priA, rows[1], cfgs[1], priB, xv, r0=r0, **kw)[k][0]))
                    for model, r in models:
                        out.append({"t": t, "st": st, "district": d["district"].iloc[0], "partner": off, "scn": scn, "sim": s, "race": race_nm, "check": c,
                                    "minute": tmin, "model": model, "err": r["share_hat"] - share, "sd": r["share_sd"], "P": r["p_win"], "win": share > 0.5,
                                    "final": share, "counted": r["counted_frac"]})
    return pd.DataFrame(out)


def replay(n_sim: int, workers: int, seed: int = 20261103, registered: bool = False, name: str = "house_replay") -> pd.DataFrame:
    P, H = cr.load_panel(), load_house()
    xrs = {int(t): xr_house(P, H, int(t)) for t in sorted(H["t"].unique())}
    model = cr.REGISTERED if registered else cr.MODEL
    jobs = [(x, d, off, key, xrs[key[0]], n_sim, seed, model) for x, d, off, key in house_pairs(P, H)]
    print(f"{len(jobs)} (statewide race, district) pairs x {len(cr.SCENARIOS)} scenarios x {n_sim} nights", flush=True)
    with ProcessPoolExecutor(workers) as ex:
        df = pd.concat(list(ex.map(run_house, jobs)), ignore_index=True)
    df.to_parquet(OUT / f"{name}.parquet", index=False)
    pd.Series({str(k): v for k, v in xrs.items()}).to_json(OUT / "house_replay_params_by_heldout_cycle.json", indent=1)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["panel", "corr", "replay", "tables"])
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--registered", action="store_true")
    ap.add_argument("--name", default="house_replay")
    a = ap.parse_args()
    if a.what == "panel":
        H = build_panel()
        print(H.drop_duplicates("race").groupby("t").size(), "districts ->", HP)
    elif a.what == "corr":
        P, H = cr.load_panel(), load_house()
        print(cr.md(corr_table(P, H)))
    elif a.what == "replay":
        df = replay(a.n, a.workers, registered=a.registered, name=a.name)
        print(len(df), "rows")
    else:
        for k, v in cr.replay_tables(a.name, race_keys=("t", "st", "district", "partner"), race_filter="house").items():
            print(f"\n== {k}\n" + cr.md(v))


if __name__ == "__main__":
    main()
