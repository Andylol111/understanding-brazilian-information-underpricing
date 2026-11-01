"""Cross-race information: how much does one race's county count tell about another race on the same ballot?

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_crossrace.py panel     # LOCO county priors, all sen/gov races
    ... us_crossrace.py corr                                                                      # swing correlations between races
    ... us_crossrace.py replay [--n 20] [--workers 4]                                             # separate vs joint replay

`panel` builds, for every Senate / governor race of 2016-2024 with county results, the unified county prior as it would have
been published before the count (leave-one-cycle-out, exactly the deployed county priors of analysis/us_unified_backtest.py:
predicted state share, predicted expected votes), next to the result. Output: results/us_crossrace/panel.parquet.
Everything downstream (correlations, replay) uses only this panel, so no number sees the cycle it is evaluated on in a fit.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_unified_backtest as bt  # noqa: E402
from brunder import us, us_unified as uu, us_unified_xrace as xr_  # noqa: E402
from scipy.stats import norm  # noqa: E402

OUT = uu.ROOT / "results" / "us_crossrace"
PANEL = OUT / "panel.parquet"


def build_panel() -> pd.DataFrame:
    """One row per county x race: fips, st, t, office, special, race, s0, V (LOCO unified prior), s2, v2 (result), S_hat, sd (statewide
    forecast and its predictive sd), n_polls, S_act."""
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    R = uu.race_table(inp)
    res = bt.state_backtest(R, n, {"extras": (), "lam": 0.0})
    C = bt.prep_county(inp, R)
    key = res.assign(race=lambda d: d["t"].astype(str) + "_" + d["st"] + "_" + d["office"] + d["special"].astype(int).astype(str)).set_index("race")
    gb = bt.state_base_votes(C)
    parts = []
    for c in bt.TEST:
        tr, te = C[(C["t"] != c) & (C["t"].isin(bt.TEST))], C[C["t"] == c]
        lam = uu.select_lam(tr)
        sm = uu.fit_share(tr, lam)
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = bt.fit_vstate(gb[gb["t"] != c])
        for race, d in te.groupby("race"):
            if race not in key.index or d["s2"].isna().any():
                continue
            k = key.loc[race]
            vp, vo = float(d["vp_b"].sum()), float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = bt.predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            pr = uu.county_prior(d, sm, tm, float(k["mu"]), Vs)
            parts.append(pd.DataFrame({
                "race": race, "t": c, "st": d["st"].values, "office": d["office"].values, "special": d["special"].values,
                "fips": d["fips"].values, "s0": pr["s0"].values, "V": pr["V"].values, "s2": d["s2"].values, "v2": d["v2"].values,
                "S_hat": float(k["mu"]), "sd": float(k["sd"]), "n_polls": int(k["n_polls"]), "S_act": float(d["S_act"].iloc[0]),
                "sp_b": d["sp_b"].values, "vp_b": d["vp_b"].values}))
    P = pd.concat(parts, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    P.to_parquet(PANEL, index=False)
    return P


def load_panel() -> pd.DataFrame:
    return pd.read_parquet(PANEL) if PANEL.exists() else build_panel()


# ------------------------------------------------------------------ correlations of the swing between races


def md(df: pd.DataFrame) -> str:
    """Markdown table (no tabulate dependency)."""
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        rows.append("| " + " | ".join("" if (isinstance(x, float) and np.isnan(x)) else (f"{x:.3f}" if isinstance(x, float) else str(x)) for x in r) + " |")
    return "\n".join(rows)


def wcorr(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    mx, my = np.average(x, weights=w), np.average(y, weights=w)
    c = np.average((x - mx) * (y - my), weights=w)
    vx, vy = np.average((x - mx) ** 2, weights=w), np.average((y - my) ** 2, weights=w)
    return float(c / np.sqrt(vx * vy)) if vx > 0 and vy > 0 else np.nan


def with_house(P: pd.DataFrame) -> pd.DataFrame:
    """Add the county's total House two-party result of the same year (all districts of the county pooled; sh, vh)."""
    rc = uu.load_inputs()["rc"]
    H = rc[rc["contest"] == "house_total"][["year", "fips", "s2", "v2"]].rename(columns={"year": "t", "s2": "sh", "v2": "vh"})
    return P.merge(H, on=["t", "fips"], how="left")


def pair_stats(P: pd.DataFrame, base: str = "s0") -> pd.DataFrame:
    """Per state-cycle with a Senate and a governor race (regular or special): the statewide swing from the prior of each (vote-weighted
    mean of s2 - base over the counties of both races), the within-state county deviation correlation and sds (points), vote weights = geometric
    mean of the two races' county votes."""
    rows = []
    for (t, st), g in P.groupby(["t", "st"]):
        for rs, s in g[g["office"] == "sen"].groupby("race"):
            for rg, v in g[g["office"] == "gov"].groupby("race"):
                m = s.merge(v, on="fips", suffixes=("_s", "_g"))
                if len(m) < 5:
                    continue
                w = np.sqrt(m["v2_s"] * m["v2_g"]).to_numpy()
                ds = (m["s2_s"] - m[f"{base}_s"]).to_numpy() if base != "pres" else (m["s2_s"] - m["sp_b_s"]).to_numpy()
                dg = (m["s2_g"] - m[f"{base}_g"]).to_numpy() if base != "pres" else (m["s2_g"] - m["sp_b_g"]).to_numpy()
                ms, mg = np.average(ds, weights=m["v2_s"]), np.average(dg, weights=m["v2_g"])
                es, eg = ds - ms, dg - mg
                rows.append({"t": t, "st": st, "sen": rs, "gov": rg, "n": len(m), "votes": float(m["v2_g"].sum()), "sw_s": 100 * ms, "sw_g": 100 * mg,
                             "sd_es": 100 * float(np.sqrt(np.average(es**2, weights=w))), "sd_eg": 100 * float(np.sqrt(np.average(eg**2, weights=w))),
                             "rho_e": wcorr(es, eg, w), "S_hat_s": 100 * m["S_hat_s"].iloc[0], "S_hat_g": 100 * m["S_hat_g"].iloc[0],
                             "n_polls_s": m["n_polls_s"].iloc[0], "n_polls_g": m["n_polls_g"].iloc[0]})
    return pd.DataFrame(rows)


def county_pairs(P: pd.DataFrame, a: str, b: str, base: str) -> pd.DataFrame:
    """Pooled county table of two offices (a, b in sen / gov / house) of the same state-cycle, deviations from the state's weighted mean."""
    rows = []
    for (t, st), g in P.groupby(["t", "st"]):
        def one(o: str) -> pd.DataFrame | None:
            if o == "house":
                x = g.drop_duplicates("fips")
                return pd.DataFrame({"fips": x["fips"], "s2": x["sh"], "v2": x["vh"], "b": x["sp_b"]}).dropna() if base == "pres" else None
            x = g[(g["office"] == o) & (g["special"] == 0)]
            if x.empty:
                return None
            return pd.DataFrame({"fips": x["fips"], "s2": x["s2"], "v2": x["v2"], "b": x["sp_b"] if base == "pres" else x["s0"]})
        A, B = one(a), one(b)
        if A is None or B is None:
            continue
        m = A.merge(B, on="fips", suffixes=("_a", "_b"))
        if len(m) < 5:
            continue
        da, db = m["s2_a"] - m["b_a"], m["s2_b"] - m["b_b"]
        w = np.sqrt(m["v2_a"] * m["v2_b"])
        rows.append(pd.DataFrame({"t": t, "st": st, "fips": m["fips"], "w": w, "ea": da - np.average(da, weights=m["v2_a"]),
                                  "eb": db - np.average(db, weights=m["v2_b"]), "sw_a": np.average(da, weights=m["v2_a"]),
                                  "sw_b": np.average(db, weights=m["v2_b"])}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def corr_report() -> str:
    P = with_house(load_panel())
    lines = []
    pd.set_option("display.width", 200)
    for base in ("s0", "pres"):
        ps = pair_stats(P, base)
        ps.to_csv(OUT / f"pairs_{base}.csv", index=False)
        nm = "unified county prior s0" if base == "s0" else "last presidential county result"
        lines.append(f"\n### Senate x governor, swing from the {nm} ({len(ps)} state-cycle pairs, {ps['t'].nunique()} cycles)\n")
        lines.append(md(ps[["t", "st", "n", "sw_s", "sw_g", "sd_es", "sd_eg", "rho_e"]]))
        w = ps["votes"]
        lines.append(f"\nstate level: corr(swing_sen, swing_gov) = {np.corrcoef(ps.sw_s, ps.sw_g)[0, 1]:.2f}; "
                     f"per cycle: " + ", ".join(f"{t}: {np.corrcoef(g.sw_s, g.sw_g)[0, 1]:.2f} (n={len(g)})" for t, g in ps.groupby('t') if len(g) > 3))
        lines.append(f"county level (deviation from each race's statewide swing): median rho_e {ps.rho_e.median():.2f}, vote-weighted mean "
                     f"{np.average(ps.rho_e.fillna(0), weights=w):.2f}, 10-90% {ps.rho_e.quantile(.1):.2f}..{ps.rho_e.quantile(.9):.2f}; "
                     f"median sd of county deviation: sen {ps.sd_es.median():.2f}, gov {ps.sd_eg.median():.2f} points")
    lines.append("\n### Pooled county correlations of the deviation (swing from the last presidential result, weights sqrt(v_a v_b)), by cycle\n")
    out = []
    for a, b in (("sen", "gov"), ("sen", "house"), ("gov", "house")):
        cp = county_pairs(P, a, b, "pres")
        if cp.empty:
            continue
        for t, g in [("all", cp)] + list(cp.groupby("t")):
            out.append({"pair": f"{a}-{b}", "cycle": t, "states": g["st"].nunique() if t != "all" else cp.groupby("t")["st"].nunique().sum(),
                        "counties": len(g), "rho_e_pooled": wcorr(g.ea.to_numpy(), g.eb.to_numpy(), g.w.to_numpy()),
                        "sd_a": 100 * float(np.sqrt(np.average(g.ea**2, weights=g.w))), "sd_b": 100 * float(np.sqrt(np.average(g.eb**2, weights=g.w)))})
        # state level swing correlation
        sw = cp.groupby(["t", "st"])[["sw_a", "sw_b"]].first()
        out.append({"pair": f"{a}-{b} (state swing)", "cycle": "all", "states": len(sw), "counties": np.nan,
                    "rho_e_pooled": float(np.corrcoef(sw.sw_a, sw.sw_b)[0, 1]), "sd_a": 100 * float(sw.sw_a.std()), "sd_b": 100 * float(sw.sw_b.std())})
    tab = pd.DataFrame(out)
    tab.to_csv(OUT / "pooled_county_corr.csv", index=False)
    lines.append(md(tab))
    return "\n".join(lines)



# ------------------------------------------------------------------ count-night replay: separate vs joint model

COUNT = {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "turnout_ratio": 1.0}
REGISTERED = {"tau": 0.06, "rho": 0.3, "sigma_mun0": 0.08, "count_scaled_terms": True}   # the registered civic v1 count settings (OH gov)
# The registered tau / sigma_mun0 describe real feeds (partial-county order bias, early vs Election Day). The replay posts whole counties, so
# the same settings give 90 % intervals that cover ~100 %; tau and sigma_mun0 are set so the SEPARATE model's 90 % coverage is ~90 % (pilot of
# 22 state-cycles, grid sigma_mun0 in {.015,.02,.03} x tau in {.005,.01}: .02/.005 -> 0.903). The comparison separate vs joint is then at honest
# widths; the registered settings are kept as a robustness run (--registered).
MODEL = {**REGISTERED, "tau": 0.005, "sigma_mun0": 0.02}
CHECKS = (0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.70, 0.90)
SCENARIOS = ("same", "indep", "lag")


def pair_data(P: pd.DataFrame, t: int, st: str, a: str = "sen", b: str = "gov") -> dict | None:
    """Common counties of the regular races a and b of one state-cycle: prior frames (unit_id, name, s0, V), final (two-party) results."""
    g = P[(P["t"] == t) & (P["st"] == st) & (P["special"] == 0)]
    x, y = g[g["office"] == a], g[g["office"] == b]
    if x.empty or y.empty:
        return None
    m = x.merge(y, on="fips", suffixes=("_a", "_b"))
    if len(m) < 5:
        return None
    uid = [uu.unit_id(st, f) for f in m["fips"]]
    pri = [pd.DataFrame({"unit_id": uid, "name": uid, "s0": m[f"s0_{k}"].to_numpy(), "V": m[f"V_{k}"].to_numpy()}) for k in "ab"]
    fin = [(m[f"s2_{k}"].to_numpy() * m[f"v2_{k}"].to_numpy(), m[f"v2_{k}"].to_numpy()) for k in "ab"]
    info = [{"mu": float(m[f"S_hat_{k}"].iloc[0]), "sd": float(m[f"sd_{k}"].iloc[0]), "n_polls": int(m[f"n_polls_{k}"].iloc[0])} for k in "ab"]
    return {"t": t, "st": st, "uid": uid, "pri": pri, "final": fin, "info": info}


def event_times(size: np.ndarray, rng: np.random.Generator, kappa: float = 0.5) -> np.ndarray:
    """Minutes at which each county posts (whole county at once): lateness rises with county size (rank correlation ~kappa, as the
    size-dependent order of analysis/us_calibrate.py), 20..420 minutes after poll close."""
    z = (np.log(size) - np.log(size).mean()) / max(np.log(size).std(), 1e-9)
    u = norm.cdf(kappa * z + np.sqrt(1 - kappa**2) * rng.standard_normal(len(size)))
    return 20.0 + 400.0 * u


def feed_rows(uid: list[str], d: np.ndarray, n: np.ndarray, done: np.ndarray) -> pd.DataFrame:
    """Feed-style rows (as brunder.us parsers give them) for the counties in `done`; a posted county is complete."""
    idx = np.where(done)[0]
    base = {"level": "county", "race": "x", "ballots": np.nan, "registered": np.nan, "outstanding": np.nan, "units": 1.0, "name": "",
            "candidate": "", "reporting": 1.0, "early": np.nan}
    u = np.asarray(uid)[idx]
    return pd.concat([pd.DataFrame({**base, "unit_id": u, "party": "DEM", "votes": d[idx]}),
                      pd.DataFrame({**base, "unit_id": u, "party": "REP", "votes": (n - d)[idx]})], ignore_index=True)


def cfgs(pd_: dict, model: dict | None = None) -> list[dict]:
    out = []
    for k in range(2):
        out.append({"election": {"state": pd_["st"], "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {}, "count": dict(COUNT),
                    "model": {**(model or MODEL), "swing_prior_mean": 0.0, "swing_prior_sd": pd_["info"][k]["sd"]}})
    return out


def variants(xr: dict) -> list[tuple[str, dict, dict]]:
    """(name, joint_project kwargs, cross-race parameters) compared with the separate model. joint: state + county coupling with the sampling
    errors' correlation taken as the county deviations' (rho_e); joint_n: the same with the empirical rho_n; county_only: no statewide
    coupling (rho_s = 0, uncorrelated sampling errors), only a county's prior moved by the other race's count of it; state_only_n: statewide coupling only."""
    return [("joint", {}, xr), ("joint_n", {}, xr | {"rho_n": xr["rho_n"]}), ("county_only", {}, xr | {"rho_s": 0.0, "rho_n": 0.0}),
            ("state_only_n", {"county_info": False}, xr | {"rho_n": xr["rho_n"]})]


def run_pair(args: tuple) -> pd.DataFrame:
    """All simulated nights of one state-cycle: separate vs joint at each race's own count checkpoints, for each schedule scenario."""
    P, t, st, xr, n_sim, seed, model, scns = args
    pdta = pair_data(P, t, st)
    cfg = cfgs(pdta, model)
    uid, pri, fin = pdta["uid"], pdta["pri"], pdta["final"]
    rng = np.random.default_rng([seed, t, sum(map(ord, st))])
    size = (fin[0][1] + fin[1][1]) / 2
    tot = [f[1].sum() for f in fin]
    share_final = [f[0].sum() / f[1].sum() for f in fin]
    out = []
    for scn in scns:
        for s in range(n_sim):
            ta = event_times(size, rng)
            tb = {"same": ta, "indep": event_times(size, rng), "lag": ta + rng.uniform(30, 150, len(ta))}[scn]
            T = (ta, tb)
            for k, race in enumerate(("sen", "gov")):
                order = np.argsort(T[k])
                cum = np.cumsum(fin[k][1][order]) / tot[k]
                for c in CHECKS:
                    i = int(np.searchsorted(cum, c))
                    if i >= len(order):
                        continue
                    tmin = float(T[k][order][i])
                    done = [T[0] <= tmin, T[1] <= tmin]
                    rows = [feed_rows(uid, fin[j][0], fin[j][1], done[j]) for j in range(2)]
                    r0 = [us.project(rows[j], cfg[j], pri[j]) for j in range(2)]
                    models = [("separate", r0[k][0])]
                    for name, kw, xv in variants(xr):
                        models.append((name, xr_.joint_project(rows[0], cfg[0], pri[0], rows[1], cfg[1], pri[1], xv, r0=r0, **kw)[k][0]))
                    for model, r in models:
                        out.append({"t": t, "st": st, "scn": scn, "sim": s, "race": race, "check": c, "minute": tmin, "model": model,
                                    "err": r["share_hat"] - share_final[k], "sd": r["share_sd"], "P": r["p_win"], "win": share_final[k] > 0.5,
                                    "final": share_final[k], "counted": r["counted_frac"]})
    return pd.DataFrame(out)


def noise_corr(P: pd.DataFrame, cycles: list[int], n_nights: int = 8, seed: int = 11) -> float:
    """Correlation of the two races' sampling errors of the observed swing (counted counties' vote-weighted mean of s2 − s0 minus the
    full-count value, standardised by the nominal sd sigma·sqrt(Σn²)/W) when the same size-ordered counties are counted, at 5-50 % counted,
    over the regular Senate x governor pairs of `cycles` (simulated nights)."""
    rng = np.random.default_rng(seed)
    ea, eb = [], []
    for (t, st), _ in P[(P["special"] == 0) & P["t"].isin(cycles)].groupby(["t", "st"]):
        pdta = pair_data(P, t, st)
        if pdta is None:
            continue
        g = P[(P["t"] == t) & (P["st"] == st) & (P["special"] == 0)]
        x, y = g[g["office"] == "sen"], g[g["office"] == "gov"]
        m = x.merge(y, on="fips", suffixes=("_a", "_b"))
        da, db = (m["s2_a"] - m["s0_a"]).to_numpy(), (m["s2_b"] - m["s0_b"]).to_numpy()
        wa, wb = m["v2_a"].to_numpy(), m["v2_b"].to_numpy()
        ma, mb = np.average(da, weights=wa), np.average(db, weights=wb)
        s2a, s2b = np.average((da - ma) ** 2, weights=wa), np.average((db - mb) ** 2, weights=wb)
        size = (wa + wb) / 2
        for _ in range(n_nights):
            T = event_times(size, rng)
            order = np.argsort(T)
            cum = np.cumsum(wa[order]) / wa.sum()
            for c in (0.05, 0.10, 0.25, 0.50):
                i = int(np.searchsorted(cum, c))
                sel = order[: i + 1]
                ya, yb = np.average(da[sel], weights=wa[sel]), np.average(db[sel], weights=wb[sel])
                va, vb = s2a * (wa[sel] ** 2).sum() / wa[sel].sum() ** 2, s2b * (wb[sel] ** 2).sum() / wb[sel].sum() ** 2
                ea.append((ya - ma) / np.sqrt(va))
                eb.append((yb - mb) / np.sqrt(vb))
    return float(np.corrcoef(ea, eb)[0, 1])


def xr_for(P: pd.DataFrame, held_out: int) -> dict:
    cyc = [c for c in sorted(P["t"].unique()) if c != held_out]
    e = xr_.estimate_crossrace(P, cyc)
    return {**e, "beta_ab": e["beta_s_on_g"], "beta_ba": e["beta_g_on_s"], "rho_n": float(max(noise_corr(P, cyc), e["rho_e"]))}


def replay(n_sim: int, workers: int, seed: int = 20261103, cycles: tuple[int, ...] | None = None, name: str = "replay", registered: bool = False) -> pd.DataFrame:
    model = REGISTERED if registered else MODEL
    P = load_panel()
    jobs = []
    xrs = {int(t): xr_for(P, int(t)) for t in sorted(P["t"].unique())}
    for (t, st), g in P[P["special"] == 0].groupby(["t", "st"]):
        if cycles and t not in cycles:
            continue
        if pair_data(P, t, st) is None:
            continue
        jobs.append((P[(P["t"] == t) & (P["st"] == st)], int(t), st, xrs[int(t)], n_sim, seed, model, SCENARIOS))
    print(f"{len(jobs)} state-cycle pairs x {len(SCENARIOS)} scenarios x {n_sim} nights", flush=True)
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(run_pair, jobs))
    df = pd.concat(parts, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / f"{name}.parquet", index=False)
    pd.Series({str(k): v for k, v in xrs.items()}).to_json(OUT / "replay_params_by_heldout_cycle.json", indent=1)
    return df


# ------------------------------------------------------------------ replay tables


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ae"] = 100 * d["err"].abs()
    d["cover90"] = (d["err"].abs() / d["sd"]) < 1.645
    d["p_correct"] = np.where(d["win"], d["P"], 1 - d["P"])
    d["brier"] = (d["p_correct"] - 1) ** 2
    d["logloss"] = -np.log(d["p_correct"].clip(1e-6, 1))
    return d


def minutes_to_90(d: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per night x race x model: minutes (and % of the race's vote counted) from which P(true winner) >= 0.9 at every later checkpoint."""
    rows = []
    for k, g in d.sort_values("check").groupby(keys):
        ok = (g["p_correct"] >= 0.9).to_numpy()
        first = next((i for i in range(len(ok)) if ok[i:].all()), None)
        rows.append({**dict(zip(keys, k)), "decided": first is not None, "minutes": g["minute"].iloc[first] if first is not None else np.nan,
                     "counted": g["check"].iloc[first] if first is not None else np.nan, "close": bool(abs(g["final"].iloc[0] - 0.5) < 0.05)})
    return pd.DataFrame(rows)


def boot_ci(x: np.ndarray, n: int = 4000, seed: int = 1) -> tuple[float, float, float]:
    """Mean and 95% bootstrap interval over independent units (state-cycles)."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float)
    m = rng.choice(x, (n, len(x))).mean(axis=1)
    return float(x.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def replay_tables(name: str = "replay", base: str = "separate", race_keys: tuple[str, ...] = ("t", "st"), race_filter: str | None = None) -> dict[str, pd.DataFrame]:
    """Tables of a replay file. `race_keys`: columns identifying an independent race-cycle (clusters of the bootstrap); `race_filter`: keep only
    rows of this `race` column value (e.g. the House race of a House replay)."""
    d = pd.read_parquet(OUT / f"{name}.parquet")
    if race_filter:
        d = d[d["race"] == race_filter]
    d = _prep(d)
    models = [m for m in d["model"].unique() if m != base]
    out = {}
    # 1. accuracy by checkpoint
    chk = [0.05, 0.10, 0.20, 0.50, 0.70, 0.90]
    a = d[d["check"].isin(chk)].groupby(["scn", "model", "check"]).agg(mae_pts=("ae", "mean"), cover90=("cover90", "mean"), brier=("brier", "mean")).reset_index()
    out["accuracy"] = a
    # 2. paired differences by state-cycle (mean over nights, checkpoints <= 0.5 and all), cluster bootstrap
    rows = []
    rk = list(race_keys)
    key = rk + ["scn", "race", "check"]
    for m in models:
        p = d[d["model"] == base].set_index(key + ["sim"])[["ae", "brier", "cover90", "p_correct"]].join(
            d[d["model"] == m].set_index(key + ["sim"])[["ae", "brier", "cover90", "p_correct"]], rsuffix="_m", how="inner")
        p["d_ae"] = p["ae_m"] - p["ae"]
        p["d_brier"] = p["brier_m"] - p["brier"]
        p = p.reset_index()
        for scn in SCENARIOS:
            for lab, sel in (("all cycles", None), ("2018", 2018), ("2022", 2022)):
                for part, cond in (("<= 50% counted", p["check"] <= 0.5), ("all checkpoints", p["check"] > 0)):
                    q = p[(p["scn"] == scn) & cond & ((p["t"] == sel) if sel else True)]
                    if q.empty:
                        continue
                    per = q.groupby(rk + ["race"])[["d_ae", "d_brier"]].mean()
                    me, lo, hi = boot_ci(per["d_ae"].to_numpy())
                    mb, lb, hb = boot_ci(per["d_brier"].to_numpy())
                    base_mae = q["ae"].mean()
                    rows.append({"model": m, "scn": scn, "cycles": lab, "checkpoints": part, "n_race_cycles": len(per), "mae_sep": base_mae,
                                 "d_mae_pts": me, "lo": lo, "hi": hi, "rel_%": 100 * me / base_mae, "d_brier": mb, "b_lo": lb, "b_hi": hb})
    out["paired"] = pd.DataFrame(rows)
    # 3. minutes to 90 % (per race, nights), all and close races
    m90 = minutes_to_90(d, rk + ["scn", "race", "sim", "model"])
    rows = []
    for (scn, model), g in m90.groupby(["scn", "model"]):
        for lab, h in (("all races", g), ("close (|final-0.5| < 0.05)", g[g["close"]])):
            rows.append({"scn": scn, "model": model, "subset": lab, "n_nights": len(h), "decided": h["decided"].mean(),
                         "minutes_to_90_median": h["minutes"].median(), "counted_at_90_median": h["counted"].median()})
    out["minutes"] = pd.DataFrame(rows)
    ik = rk + ["scn", "race", "sim"]
    pm = m90[m90["model"] == base].set_index(ik)
    rows = []
    for m in models:
        q = m90[m90["model"] == m].set_index(ik).join(pm, rsuffix="_b", how="inner")
        q["dmin"] = q["minutes"] - q["minutes_b"]
        for scn in SCENARIOS:
            h = q[(q.index.get_level_values("scn") == scn) & q["decided"] & q["decided_b"]]
            for lab, hh in (("all", h), ("close", h[h["close"]])):
                if len(hh):
                    per = hh.groupby(rk + ["race"])["dmin"].mean()
                    me, lo, hi = boot_ci(per.to_numpy())
                    rows.append({"model": m, "scn": scn, "subset": lab, "race_cycles": len(per), "d_minutes_mean": me, "lo": lo, "hi": hi,
                                 "more_decided_%": 100 * (q[q.index.get_level_values('scn') == scn]["decided"].mean() - q[q.index.get_level_values('scn') == scn]["decided_b"].mean())})
    out["d_minutes"] = pd.DataFrame(rows)
    for k, v in out.items():
        v.to_csv(OUT / f"{name}_{k}.csv", index=False)
    return out


def cycle_table(name: str = "replay", base: str = "separate", race_keys: tuple[str, ...] = ("t", "st"), race_filter: str | None = None) -> dict[str, pd.DataFrame]:
    """Per held-out cycle and schedule scenario, every variant against the separate model on the integrator's bar: MAE by % counted (<= 50 %
    and all checkpoints), 90 % coverage, minutes to 90 % on the winner (nights decided in both), and the variant chosen leave-one-cycle-out
    (the variant with the smallest mean paired MAE difference on the OTHER cycles' replays; 'separate' when none is below zero)."""
    d = pd.read_parquet(OUT / f"{name}.parquet")
    if race_filter:
        d = d[d["race"] == race_filter]
    d = _prep(d)
    rk = list(race_keys)
    ik = rk + ["scn", "race", "sim"]
    m90 = minutes_to_90(d, rk + ["scn", "race", "sim", "model"])
    b = d[d["model"] == base].set_index(rk + ["scn", "race", "check", "sim"])
    rows = []
    for m in [x for x in d["model"].unique() if x != base]:
        q = d[d["model"] == m].set_index(rk + ["scn", "race", "check", "sim"])
        p = q[["ae", "cover90", "brier"]].join(b[["ae", "cover90", "brier"]], rsuffix="_b", how="inner").reset_index()
        p["d_ae"], p["d_cov"], p["d_brier"] = p["ae"] - p["ae_b"], p["cover90"].astype(float) - p["cover90_b"].astype(float), p["brier"] - p["brier_b"]
        a = m90[m90["model"] == m].set_index(ik).join(m90[m90["model"] == base].set_index(ik), rsuffix="_b", how="inner").reset_index()
        a["dmin"] = a["minutes"] - a["minutes_b"]
        for (t, scn), g in p.groupby(["t", "scn"]):
            early = g[g["check"] <= 0.5]
            ga = a[(a["t"] == t) & (a["scn"] == scn) & a["decided"] & a["decided_b"]]
            rows.append({"cycle": t, "scn": scn, "variant": m, "race_cycles": g.groupby(rk).ngroups,
                         "d_mae_pts_le50": early.groupby(rk + ["race"])["d_ae"].mean().mean(), "mae_sep_le50": early["ae_b"].mean(),
                         "d_mae_pts_all": g.groupby(rk + ["race"])["d_ae"].mean().mean(), "d_cover90": g["d_cov"].mean(), "cover90_sep": g["cover90_b"].astype(float).mean(),
                         "d_brier": g["d_brier"].mean(), "d_minutes_to_90": ga["dmin"].mean() if len(ga) else np.nan, "nights_decided_both": len(ga)})
    per = pd.DataFrame(rows)
    # strict: ties (identical results, e.g. no county coupling possible under a common order) do not pass
    per["pass_bar"] = (per["d_mae_pts_le50"] < -0.0005) & (per["d_minutes_to_90"] < -0.05) & (per["d_cover90"] >= -0.005)
    per["pass_mae_cover"] = (per["d_mae_pts_le50"] < -0.0005) & (per["d_cover90"] >= -0.005)
    # leave-one-cycle-out choice
    ch = []
    for scn in list(SCENARIOS) + ["any"]:
        for c in sorted(per["cycle"].unique()):
            tr = per[(per["cycle"] != c) & ((per["scn"] == scn) if scn != "any" else True)].groupby("variant")["d_mae_pts_le50"].mean()
            best = tr.idxmin() if len(tr) and tr.min() < 0 else "separate"
            te = per[(per["cycle"] == c) & ((per["scn"] == scn) if scn != "any" else True) & (per["variant"] == best)]
            ch.append({"held_out": c, "scn": scn, "chosen": best, **({k: te[k].mean() for k in ("d_mae_pts_le50", "d_mae_pts_all", "d_cover90", "d_brier", "d_minutes_to_90")} if len(te) else {})})
    out = {"cycle_variants": per, "loco_choice": pd.DataFrame(ch)}
    for k, v in out.items():
        v.to_csv(OUT / f"{name}_{k}.csv", index=False)
    return out


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "panel"
    if what == "panel":
        P = build_panel()
        print(P.groupby(["t", "office"])["race"].nunique().unstack(), "->", PANEL)
    elif what == "corr":
        print(corr_report())
    elif what == "tables":
        name = sys.argv[2] if len(sys.argv) > 2 else "replay"
        for k, v in replay_tables(name).items():
            print(f"\n== {k}\n" + md(v))
    elif what == "cycles":
        name = sys.argv[2] if len(sys.argv) > 2 else "replay"
        for k, v in cycle_table(name).items():
            print(f"\n== {k}\n" + md(v))
    elif what == "replay":
        ap = argparse.ArgumentParser()
        ap.add_argument("what")
        ap.add_argument("--n", type=int, default=12)
        ap.add_argument("--workers", type=int, default=6)
        ap.add_argument("--cycles", type=int, nargs="*")
        ap.add_argument("--registered", action="store_true")
        ap.add_argument("--name", default="replay")
        a = ap.parse_args()
        df = replay(a.n, a.workers, cycles=tuple(a.cycles) if a.cycles else None, name=a.name, registered=a.registered)
        print(len(df), "rows ->", OUT / f"{a.name}.parquet")


if __name__ == "__main__":
    main()
