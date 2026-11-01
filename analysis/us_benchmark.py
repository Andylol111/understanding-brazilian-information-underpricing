"""Consolidated US benchmark, parts A (pre-count LOCO), D (consistency audit) and E (recommendation).

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_benchmark.py [pre|audit|all]

Selection rule: docs/us_benchmark.md section 0 (committed before any number). Everything is leave-one-cycle-out 2016-2024; the
unified model reuses the nested fits of analysis/us_unified_backtest.py / us_unified_house_2026.py. Outputs (aggregates only):
data_public/us_benchmark_{statewide_races,summary,county_races,comparisons,recommendation,audit}.csv.
Parts B (count) and C (turnout map) live in analysis/us_benchmark_count.py / us_benchmark_turnout.py.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brunder import us_unified as uu                       # noqa: E402
import us_unified_backtest as ub                           # noqa: E402
import us_unified_house_2026 as uh                         # noqa: E402

ROOT = uu.ROOT
PUB = ROOT / "data_public"
POLL_SD_HIST = 0.039           # brunder.us.POLL_SD_HIST (registered)
POLL_N_TYPICAL = 700           # two-party respondents per poll assumed for v2's sampling term (historic sample sizes not in the panel)
MIN_CLASS = 30
B = 20000
SEED = 20261102
MID = (2018, 2022)             # midterm cycles: registered turnout rules apply
STATEWIDE_MODELS = ["v1", "v2", "v3", "v3_reg", "unified"]
COUNTY_MODELS = ["v1", "v3", "v4", "unified"]


# ------------------------------------------------------------------ statewide


def crps(y, mu, sd):
    return uu.crps_normal(y, mu, sd) * 100


def statewide_panel(inp: dict) -> pd.DataFrame:
    n = uu.nat_table(inp["nat"])
    R = uu.race_table(inp)
    nt, spec = ub.nat_backtest(n)
    res = ub.state_backtest(R, n, spec)
    d = res.copy()
    d["race"] = d["t"].astype(str) + "_" + d["st"] + "_" + d["office"] + d["special"].astype(int).astype(str)
    polled = (d["n_polls"] > 0).values
    v1 = d["O"].fillna(d["L"])
    sd_v1 = ub.loco_bin_sd(d.assign(), v1.values)
    d["v1_mu"], d["v1_sd"] = v1, sd_v1
    k = d["n_polls"].clip(lower=1)
    d["v2_mu"] = np.where(polled, d["P"], v1)
    d["v2_sd"] = np.where(polled, np.sqrt(POLL_SD_HIST**2 + 0.25 / (POLL_N_TYPICAL * k)), sd_v1)
    sd_loco = ub.loco_bin_sd(d, d["P"].fillna(0.5).values)
    d["v3_mu"] = d["v2_mu"]
    d["v3_sd"] = np.where(polled, sd_loco, sd_v1)
    d["v3_reg_mu"] = d["v2_mu"]
    d["v3_reg_sd"] = np.where(polled, ub.v3_sd(d["n_polls"].values), sd_v1)
    d["unified_mu"], d["unified_sd"] = d["mu"], d["sd"]
    for m in STATEWIDE_MODELS:
        mu, sd = d[f"{m}_mu"].values, d[f"{m}_sd"].values
        d[f"{m}_crps"] = crps(d["s2"].values, mu, sd)
        d[f"{m}_abserr"] = np.abs(d["s2"].values - mu) * 100
        d[f"{m}_err"] = (mu - d["s2"].values) * 100
        p = np.clip(norm.cdf((mu - 0.5) / sd), 1e-4, 1 - 1e-4)
        win = (d["s2"].values > 0.5).astype(float)
        d[f"{m}_p"] = p
        d[f"{m}_brier"] = (p - win) ** 2
        d[f"{m}_logloss"] = -(win * np.log(p) + (1 - win) * np.log(1 - p))
        d[f"{m}_cov90"] = (np.abs(d["s2"].values - mu) < 1.645 * sd).astype(float)
    # volatility observables (pre-count only)
    d["swing_abs"] = np.where(polled, np.abs(d["P"] - v1) * 100, np.nan)
    dr = inp["drift"]
    dr = dr[dr["office"].isin(uu.OFFICES) & dr["miss"].notna()]
    hist = []
    for r in d.itertuples():
        h = dr[(dr["state"] == r.st) & (dr["cycle"] < r.t)]
        hist.append(h["miss"].abs().mean() if len(h) >= 2 else np.nan)
    d["hist_miss"] = hist
    f1 = (d["n_polls"] <= 3).astype(int)
    f2 = (d["swing_abs"] >= 6).astype(int)
    f3 = (d["hist_miss"].isna() | (d["hist_miss"] >= 3)).astype(int)
    d["flags"] = f1 + f2 + f3
    d["C1"] = np.where(d["flags"] == 0, "low", np.where(d["flags"] == 1, "medium", "high"))
    d["C2"] = np.where(d["n_polls"] == 0, "unpolled", np.where(d["n_polls"] <= 5, "1-5 polls", "6+ polls"))
    d["C3"] = np.where(d["swing_abs"].isna(), "unpolled", np.where(d["swing_abs"] < 3, "<3 pts", np.where(d["swing_abs"] < 6, "3-6 pts", ">=6 pts")))
    d["C4"] = np.where(d["hist_miss"].isna(), ">=4 pts or none", np.where(d["hist_miss"] < 2, "<2 pts", np.where(d["hist_miss"] < 4, "2-4 pts", ">=4 pts or none")))
    return d


# ------------------------------------------------------------------ county


def v4_votes(inp: dict, d: pd.DataFrame, t: int) -> pd.Series | None:
    """Analogue of data_public/us2026_expected_votes_v4.csv for a past midterm t: last-president two-party votes x the county's own
    (t-4 top race / t-6 president) ratio relative to its state, capped x0.61-1.65; state ratio where missing. Only t=2022 has the data
    (2018 top race, 2016 president); 2018 would need 2014 county returns (not in the panel)."""
    if t != 2022:
        return None
    rc = inp["rc"]
    top = rc[(rc["year"] == t - 4) & rc["contest"].isin(["sen", "gov"])]
    top = top[top["st"] == d["st"].iloc[0]]
    pick = "sen" if (top["contest"] == "sen").any() else "gov"
    top = top[top["contest"] == pick].set_index("fips")["v2"]
    pres = rc[(rc["year"] == t - 6) & (rc["contest"] == "pres")].set_index("fips")["v2"]
    num = top.reindex(d["fips"]).values
    den = pres.reindex(d["fips"]).values
    ok = (num > 0) & (den > 0)
    if ok.sum() < 2:
        return None
    Rs = num[ok].sum() / den[ok].sum()
    rel = np.where(ok, num / np.where(ok, den, 1) / Rs, np.nan)
    r = np.where(np.isnan(rel), Rs, Rs * np.clip(rel, 0.61, 1.65))
    return pd.Series(d["vp_b"].values * r, index=d.index)


def county_panel(inp: dict, sw: pd.DataFrame) -> pd.DataFrame:
    R = uu.race_table(inp)
    C = ub.prep_county(inp, R)
    key = sw.set_index("race")
    gb = ub.state_base_votes(C)
    rows = []
    for c in ub.TEST:
        tr, te = C[(C["t"] != c) & (C["t"].isin(ub.TEST))], C[C["t"] == c]
        lam = uu.select_lam(tr)
        sm = uu.fit_share(tr, lam)
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = ub.fit_vstate(gb[gb["t"] != c])
        for race, d in te.groupby("race"):
            if race not in key.index or d["s2"].isna().any():
                continue
            k = key.loc[race]
            y, w = d["s2"].values, d["v2"].values
            tot = w.sum()
            polled = k["n_polls"] > 0
            vp = float(d["vp_b"].sum())
            vo = float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = ub.predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            pr = uu.county_prior(d, sm, tm, float(k["unified_mu"]), Vs)
            s1 = d["so_f"].values
            mid = c % 4 == 2
            # registered level rule: last same-office race if it was a midterm (x1.0), else last president x0.72 (midterm targets)
            if mid:
                V1 = np.where((d["oy"].values == c - 4) & d["vo_b"].notna().values, d["vo_b"].values, d["vp_b"].values * 0.72)
            else:
                V1 = d["vo_b"].fillna(d["vp_b"]).values
            V1 = np.nan_to_num(V1, nan=0.0) + 1e-9
            base1 = np.average(s1, weights=V1)
            shift = (k["P"] - base1) if polled else 0.0
            s3 = np.clip(s1 + shift, 0.005, 0.995)
            V4 = v4_votes(inp, d, c)
            models = {"v1": (s1, V1), "v3": (s3, V1), "unified": (pr["s0"].values, pr["V"].values)}
            if V4 is not None:
                V4v = np.nan_to_num(V4.values, nan=0.0) + 1e-9
                sh4 = np.clip(s1 + ((k["P"] - np.average(s1, weights=V4v)) if polled else 0.0), 0.005, 0.995)
                models["v4"] = (sh4, V4v)
            for nm, (s0, V) in models.items():
                ape = np.abs(V / w - 1)
                rows.append({"race": race, "t": c, "st": d["st"].iloc[0], "model": nm, "n_counties": len(d), "votes": tot,
                             "share_wmae": float((np.abs(s0 - y) * w).sum() / tot * 100),
                             "ev_wmape": float((np.abs(V - w)).sum() / tot * 100),
                             "ev_medape": float(np.median(ape) * 100),
                             "tv_share": float(np.abs(V / V.sum() - w / tot).sum() / 2 * 100),
                             "level_logerr": float(np.log(V.sum() / tot))})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ bootstrap and tests


def boot_delta(delta: np.ndarray, cyc: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float, float]:
    """mean delta, 95 % interval, two-sided p; resampling races within cycle."""
    N = len(delta)
    tot = np.zeros(B)
    for c in np.unique(cyc):
        x = delta[cyc == c]
        idx = rng.integers(0, len(x), (B, len(x)))
        tot += x[idx].sum(1)
    m = tot / N
    p = 2 * min((m <= 0).mean(), (m >= 0).mean())
    return float(delta.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975)), float(min(max(p, 1 / B), 1.0))


def cycle_consistency(delta: np.ndarray, cyc: np.ndarray) -> tuple[int, int]:
    pos = tot = 0
    sign = np.sign(delta.mean())
    for c in np.unique(cyc):
        x = delta[cyc == c]
        if len(x) >= 5:
            tot += 1
            pos += int(np.sign(x.mean()) == sign and sign != 0)
    return pos, tot


def holm(p: np.ndarray) -> np.ndarray:
    o = np.argsort(p)
    m = len(p)
    adj = np.empty(m)
    run = 0.0
    for i, j in enumerate(o):
        run = max(run, (m - i) * p[j])
        adj[j] = min(run, 1.0)
    return adj


SCHEMES = {"pooled": None, "C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4"}
TIER_ORDER = {"C1": ["low", "medium", "high"]}


def class_sets(df: pd.DataFrame, schemes: dict) -> list[tuple[str, str, np.ndarray]]:
    out = [("pooled", "all races", np.ones(len(df), bool))]
    for sch, col in schemes.items():
        if col is None:
            continue
        for lvl in sorted(df[col].dropna().unique()):
            out.append((sch, str(lvl), (df[col] == lvl).values))
    return out


def run_tests(frames: list[dict]) -> pd.DataFrame:
    """frames: dict(layer, df (one row per race, columns delta_<cand> = primary metric − candidate metric and cycle t), cands, schemes,
    extra per-candidate columns). Returns the log of every comparison."""
    rng = np.random.default_rng(SEED)
    rows = []
    for fr in frames:
        df = fr["df"]
        for sch, lvl, m in class_sets(df, fr["schemes"]):
            for cand in fr["cands"]:
                col = f"delta_{cand}"
                sub = df[m & df[col].notna().values]
                row = {"layer": fr["layer"], "primary": fr["primary"], "candidate": cand, "scheme": sch, "class": lvl, "n": len(sub),
                       "cycles": ",".join(map(str, sorted(sub["t"].unique())))}
                if len(sub) < MIN_CLASS:
                    row["status"] = "not tested (n<30)"
                    rows.append(row)
                    continue
                dl, c = sub[col].values, sub["t"].values
                mean, lo, hi, p = boot_delta(dl, c, rng)
                pos, tot = cycle_consistency(dl, c)
                row.update({"status": "tested", "primary_metric": float(sub["prim"].mean()), "cand_metric": float(sub["prim"].mean() - mean),
                            "improvement": mean, "ci_lo": lo, "ci_hi": hi, "p": p, "cycles_same_sign": pos, "cycles_eval": tot})
                for extra in fr.get("extras", []):
                    row[extra] = float(sub[f"{extra}_{cand}"].mean()) if f"{extra}_{cand}" in sub else np.nan
                if f"delta_reg_{cand}" in sub:
                    dr_, c_ = sub[f"delta_reg_{cand}"].values, sub["t"].values
                    mr, lr, hr, pr_ = boot_delta(dr_, c_, rng)
                    row.update({"improvement_vs_registered_sd": mr, "reg_ci_lo": lr, "reg_ci_hi": hr})
                rows.append(row)
    L = pd.DataFrame(rows)
    ok = L["status"] == "tested"
    L["p_holm"] = np.nan
    L.loc[ok, "p_holm"] = holm(L.loc[ok, "p"].values)
    return L


def descriptive(d: pd.DataFrame, primary: str, cands: list[str], schemes: list[str], family: str) -> pd.DataFrame:
    """Bootstrap intervals of the other win-probability / point metrics (Brier, log loss, MAE) for the headline schemes. Descriptive only: NOT part of
    the Holm family and never used by the recommendation rule (section 0 fixes CRPS as the tested metric)."""
    rng = np.random.default_rng(SEED + 1)
    rows = []
    for sch, lvl, m in class_sets(d, {k: k for k in schemes}):
        x = d[m]
        if len(x) < MIN_CLASS:
            continue
        for cand in cands:
            for met in ("brier", "logloss", "abserr"):
                dl = (x[f"{primary}_{met}"] - x[f"{cand}_{met}"]).values
                mean, lo, hi, _ = boot_delta(dl, x["t"].values, rng)
                rows.append({"family": family, "scheme": sch, "class": lvl, "n": len(x), "primary": primary, "candidate": cand, "metric": met,
                             "improvement": mean, "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows)


def recommend(L: pd.DataFrame) -> pd.DataFrame:
    """Apply section 0 rule 2 to every tested comparison; recommendation per (layer, scheme, class)."""
    L = L.copy()
    t = L["status"] == "tested"
    c_ok = t & (L["p_holm"] < 0.05) & (L["improvement"] > 0)
    cyc = t & (L["cycles_eval"] > 0) & (L["cycles_same_sign"] >= np.ceil(2 * L["cycles_eval"] / 3))
    d_ok = pd.Series(True, index=L.index)
    if "brier_diff" in L:
        d_ok = (L["brier_diff"].fillna(0) <= 0) & (L["cov90_cand"].fillna(1) >= 0.8)
    e_ok = pd.Series(True, index=L.index)
    if "reg_ci_lo" in L:
        e_ok = L["reg_ci_lo"].isna() | (L["reg_ci_lo"] > 0)
    L["rule_p_holm"], L["rule_cycles"], L["rule_brier_cov"], L["rule_registered_sd"] = c_ok, cyc, d_ok, e_ok
    L["recommended"] = c_ok & cyc & d_ok & e_ok
    return L


def statewide_frame(d: pd.DataFrame) -> dict:
    cands = ["v1", "v2", "unified"]
    df = d[["race", "t", "C1", "C2", "C3", "C4"]].copy()
    df["prim"] = d["v3_crps"]
    ex = {}
    for c in cands:
        df[f"delta_{c}"] = d["v3_crps"] - d[f"{c}_crps"]
        df[f"delta_reg_{c}"] = d["v3_reg_crps"] - d[f"{c}_crps"]
        df[f"brier_diff_{c}"] = d[f"{c}_brier"] - d["v3_brier"]
        df[f"cov90_cand_{c}"] = d[f"{c}_cov90"]
    return {"layer": "statewide share CRPS (pts)", "primary": "v3 (LOCO-refit sd)", "df": df, "cands": cands,
            "schemes": {"C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4"}, "extras": ["brier_diff", "cov90_cand"]}


def county_frames(cp: pd.DataFrame, sw: pd.DataFrame) -> list[dict]:
    cls = sw.set_index("race")[["C1", "C2", "C3", "C4"]]
    out = []
    # v1 has v3's expected votes (identical by construction), so it is not a candidate on the two turnout layers
    for layer, metric, cands, cycles in (("county share wMAE (pts)", "share_wmae", ["v1", "v4", "unified"], None),
                                         ("county expected-vote wMAPE (%)", "ev_wmape", ["v4", "unified"], MID),
                                         ("county turnout-share TV (%)", "tv_share", ["v4", "unified"], MID)):
        x = cp if cycles is None else cp[cp["t"].isin(cycles)]
        wide = x.pivot(index="race", columns="model", values=metric)
        df = wide.join(cls, how="left").join(x.drop_duplicates("race").set_index("race")["t"])
        df["prim"] = wide["v3"]
        for c in cands:
            df[f"delta_{c}"] = wide["v3"] - wide[c] if c in wide else np.nan
        out.append({"layer": layer, "primary": "v3", "df": df.reset_index(), "cands": cands,
                    "schemes": {"C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4"}})
    return out


# ------------------------------------------------------------------ House


def house_panel(inp: dict) -> pd.DataFrame:
    d = uh.backtest(inp).reset_index(drop=True)
    d["race"] = d["t"].astype(str) + "_" + d.index.astype(str)
    cand = {"v1": (d["v1"], d["sd_v1"]), "v2_reg": (d["v2"], np.full(len(d), uh.V2_SD)), "v2": (d["v2"], d["sd_v2r"]),
            "unified": (d["mu"], d["sd"])}
    for m, (mu, sd) in cand.items():
        mu, sd = np.asarray(mu, float), np.asarray(sd, float)
        y = d["s2"].values
        p = np.clip(norm.cdf((mu - 0.5) / sd), 1e-4, 1 - 1e-4)
        win = (y > 0.5).astype(float)
        d[f"{m}_crps"] = crps(y, mu, sd)
        d[f"{m}_abserr"] = np.abs(y - mu) * 100
        d[f"{m}_brier"] = (p - win) ** 2
        d[f"{m}_logloss"] = -(win * np.log(p) + (1 - win) * np.log(1 - p))
        d[f"{m}_cov90"] = (np.abs(y - mu) < 1.645 * sd).astype(float)
    d["H1"] = np.where(d["n_polls"] > 0, "polled", "unpolled")
    d["H2"] = np.where(np.abs(d["pres_prev_d2"] - 0.5) < 0.05, "close (<0.05)", np.where(np.abs(d["pres_prev_d2"] - 0.5) < 0.15, "lean (0.05-0.15)", "safe (>0.15)"))
    return d


def house_frame(d: pd.DataFrame) -> dict:
    cands = ["v1", "unified"]
    df = d[["race", "t", "H1", "H2"]].copy()
    df["prim"] = d["v2_crps"]
    for c in cands:
        df[f"delta_{c}"] = d["v2_crps"] - d[f"{c}_crps"]
        df[f"delta_reg_{c}"] = d["v2_reg_crps"] - d[f"{c}_crps"]
        df[f"brier_diff_{c}"] = d[f"{c}_brier"] - d["v2_brier"]
        df[f"cov90_cand_{c}"] = d[f"{c}_cov90"]
    return {"layer": "House district share CRPS (pts)", "primary": "v2 (LOCO-refit sd)", "df": df, "cands": cands,
            "schemes": {"H1": "H1", "H2": "H2"}, "extras": ["brier_diff", "cov90_cand"]}


# ------------------------------------------------------------------ summaries


def summarise(d: pd.DataFrame, models: list[str], keys: list[tuple[str, str, np.ndarray]], family: str) -> pd.DataFrame:
    rows = []
    for sch, lvl, m in keys:
        x = d[m]
        for mod in models:
            rows.append({"family": family, "scheme": sch, "class": lvl, "model": mod, "n": len(x),
                         "mae_pts": x[f"{mod}_abserr"].mean(), "bias_pts": x[f"{mod}_err"].mean() if f"{mod}_err" in x else np.nan,
                         "crps_pts": x[f"{mod}_crps"].mean(), "brier": x[f"{mod}_brier"].mean(), "logloss": x[f"{mod}_logloss"].mean(),
                         "cov90": x[f"{mod}_cov90"].mean()})
    return pd.DataFrame(rows)


def county_summary(cp: pd.DataFrame) -> pd.DataFrame:
    """Models are compared on the races every listed model has: v4 exists for 2022 only, so it gets its own subset."""
    rows = []
    x3 = cp[cp["model"] != "v4"]
    for name, x in (("all cycles (v1, v3, unified)", x3), ("midterm 2018+2022 (v1, v3, unified)", x3[x3["t"].isin(MID)]),
                    ("presidential cycles 2016/20/24 (v1, v3, unified)", x3[~x3["t"].isin(MID)]),
                    ("2022 only (v1, v3, v4, unified)", cp[cp["t"] == 2022])):
        for mod, g in x.groupby("model"):
            rows.append({"subset": name, "model": mod, "races": len(g), "share_wMAE_pts": g["share_wmae"].mean(),
                         "ev_wMAPE_pct": g["ev_wmape"].mean(), "ev_median_county_APE_pct": g["ev_medape"].mean(),
                         "turnout_share_TV_pct": g["tv_share"].mean(), "state_level_abs_logerr": g["level_logerr"].abs().mean(),
                         "state_level_bias_logerr": g["level_logerr"].mean()})
    return pd.DataFrame(rows)


def recommendation(L: pd.DataFrame) -> pd.DataFrame:
    """Per layer / scheme / class: the benchmark pick (best tested candidate, secondary, with its interval) and whether the rule changes the primary."""
    rows = []
    for (layer, sch, cl), g in L.groupby(["layer", "scheme", "class"], sort=False):
        t = g[g["status"] == "tested"]
        if t.empty:
            rows.append({"layer": layer, "scheme": sch, "class": cl, "n": int(g["n"].max()), "primary": g["primary"].iloc[0], "benchmark_pick": "n<30: not tested",
                         "recommendation": "keep primary"})
            continue
        b = t.sort_values("improvement", ascending=False).iloc[0]
        pick = b["candidate"] if b["improvement"] > 0 else g["primary"].iloc[0] + " (primary best)"
        rec = t[t["recommended"]].sort_values("improvement", ascending=False)
        rows.append({"layer": layer, "scheme": sch, "class": cl, "n": int(b["n"]), "primary": g["primary"].iloc[0], "benchmark_pick": pick,
                     "pick_improvement": b["improvement"], "pick_ci_lo": b["ci_lo"], "pick_ci_hi": b["ci_hi"], "pick_p_holm": b["p_holm"],
                     "recommendation": (f"switch to {rec.iloc[0]['candidate']}" if len(rec) else "keep primary"),
                     "recommended_margin": (rec.iloc[0]["improvement"] if len(rec) else np.nan)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ D. consistency audit


def _collect() -> dict[str, dict[str, dict[str, dict]]]:
    """family -> race -> version -> parsed config (registered versions and the unified challenger)."""
    fam: dict[str, dict] = {"statewide": {}, "civic": {}, "house": {}}
    cdir = ROOT / "configs"
    for p in sorted([*cdir.glob("us2026_*_v*.toml"), *(cdir / "us2026_civic").glob("us2026_civic_*_v*.toml")]):
        stem = p.stem
        name, v = stem.rsplit("_v", 1)
        f = "civic" if stem.startswith("us2026_civic_") else "statewide"
        fam[f].setdefault(name.replace("us2026_", "").replace("civic_", ""), {})["v" + v] = tomllib.loads(p.read_text())
    for p in sorted((cdir / "us2026_house").glob("*_v*.toml")):
        name, v = p.stem.rsplit("_v", 1)
        fam["house"].setdefault(name, {})["v" + v] = tomllib.loads(p.read_text())
    for p in sorted((cdir / "us2026_unified").glob("us2026_unified_*_v1.toml")):
        n = p.stem.replace("us2026_unified_", "").rsplit("_v", 1)[0]
        f = "civic" if n.startswith("civic_") else "statewide"
        fam[f].setdefault(n.replace("civic_", ""), {})["unified"] = tomllib.loads(p.read_text())
    for p in sorted((cdir / "us2026_unified" / "house").glob("us2026_unified_house_*_v1.toml")):
        n = p.stem.replace("us2026_unified_house_", "").rsplit("_v", 1)[0]
        fam["house"].setdefault(n, {})["unified"] = tomllib.loads(p.read_text())
    return fam


def audit() -> pd.DataFrame:
    """Same feed / race key / candidates / poll close / market mapping / count settings / unit ids and county set across the versions of every
    race (configs and the prior files only; frozen files are read, never written)."""
    from brunder import us
    fam = _collect()
    pri = us.load_priors()
    upri = pd.concat([pd.read_csv(PUB / f, comment="#") for f in ("us2026_unified_priors.csv", "us2026_unified_house_priors.csv")], ignore_index=True)
    ev4 = pd.read_csv(PUB / "us2026_expected_votes_v4.csv", comment="#")
    rows = []

    def units(df, src):
        return set(df.loc[df["source"] == src, "unit_id"])

    for fname, races in fam.items():
        for race, vs in races.items():
            ref_name = "v1" if "v1" in vs else sorted(vs)[0]
            ref = vs[ref_name]
            issues, info = [], []
            for v, c in vs.items():
                if v == ref_name:
                    continue
                for k in ("race_id", "state", "race", "feed", "poll_close_utc", "name"):
                    if c["election"].get(k) != ref["election"].get(k):
                        issues.append(f"{v}: election.{k} {c['election'].get(k)!r} != {ref['election'].get(k)!r}")
                for k in ("a", "b"):
                    if c["election"].get(k) != ref["election"].get(k):
                        issues.append(f"{v}: election.{k} {c['election'].get(k)!r} != {ref['election'].get(k)!r}")
                for mk in ("polymarket", "kalshi"):
                    a, b = c.get("markets", {}).get(mk, {}), ref.get("markets", {}).get(mk, {})
                    for k in sorted(set(a) | set(b)):
                        if a.get(k) != b.get(k):
                            issues.append(f"{v}: markets.{mk}.{k} {a.get(k)!r} != {b.get(k)!r}")
                for sec, keys in (("model", ("tau", "rho", "sigma_mun0")), ("count", ("f_max", "f_complete", "learn_turnout", "vote_type", "early_share", "use_outstanding"))):
                    for k in keys:
                        if c.get(sec, {}).get(k) != ref.get(sec, {}).get(k):
                            issues.append(f"{v}: {sec}.{k} {c.get(sec, {}).get(k)!r} != {ref.get(sec, {}).get(k)!r}")
                if v != "unified" and c["prior"].get("share") != ref["prior"].get("share"):
                    issues.append(f"{v}: prior.share {c['prior'].get('share')!r} != {ref['prior'].get('share')!r}")
            polls = {v: (c["prior"].get("poll_avg"), c["prior"].get("poll_n")) for v, c in vs.items() if v not in ("v1", "unified") and c["prior"].get("poll_avg") is not None}
            if len(set(polls.values())) > 1:
                issues.append(f"poll average / n differ between versions: {polls}")
            if "unified" in vs:
                u = vs["unified"]["prior"]
                if polls and u.get("poll_n") is not None and u.get("poll_n") != next(iter(polls.values()))[1]:
                    info.append(f"unified poll_n {u.get('poll_n')} vs registered {next(iter(polls.values()))[1]} (different window/pollster rules)")
                tr = {v: c["count"].get("turnout_ratio") for v, c in vs.items()}
                if len(set(tr.values())) > 1:
                    info.append("turnout_ratio " + ",".join(f"{k}={v}" for k, v in tr.items()))
            base_units = units(pri, ref["prior"]["share"]) if ref["prior"].get("share") else set()
            u_units = units(upri, vs["unified"]["prior"]["share"]) if "unified" in vs and vs["unified"]["prior"].get("share") else set()
            v4_units = set()
            if "v4" in vs:
                st = ref["election"]["state"]
                v4_units = set(ev4["unit_id"][ev4["unit_id"].str.startswith(f"us-{st}-")])
            if base_units and u_units and base_units != u_units:
                issues.append(f"county set: registered prior {len(base_units)} units vs unified {len(u_units)} (only-registered {len(base_units - u_units)}, only-unified {len(u_units - base_units)})")
            if base_units and v4_units and base_units != v4_units:
                issues.append(f"county set: registered prior {len(base_units)} vs v4 file {len(v4_units)} (only-registered {len(base_units - v4_units)}, only-v4 {len(v4_units - base_units)})")
            if not base_units:
                issues.append("registered prior source has no units in the loaded priors")
            if "unified" in vs and not u_units:
                issues.append("unified prior source has no units in the unified prior files")
            rows.append({"family": fname, "race": race, "versions": ",".join(sorted(vs)), "feed": ref["election"].get("feed"), "state": ref["election"].get("state"),
                         "n_units_registered_prior": len(base_units), "n_units_unified": len(u_units), "n_units_v4": len(v4_units),
                         "consistent": not issues, "issues": " | ".join(issues), "info": " | ".join(info)})
    # the same race on two feeds (official statewide feed vs civicAPI): market mapping, candidates and race key must agree
    for race, cv in fam["civic"].items():
        if race not in fam["statewide"]:
            continue
        a, b = cv.get("v1") or next(iter(cv.values())), fam["statewide"][race].get("v1") or next(iter(fam["statewide"][race].values()))
        iss = []
        for k in ("race_id", "state", "a", "b", "name"):
            if a["election"].get(k) != b["election"].get(k):
                iss.append(f"election.{k} civic {a['election'].get(k)!r} != statewide {b['election'].get(k)!r}")
        for mk in ("polymarket", "kalshi"):
            for k in sorted(set(a.get("markets", {}).get(mk, {})) | set(b.get("markets", {}).get(mk, {}))):
                if a.get("markets", {}).get(mk, {}).get(k) != b.get("markets", {}).get(mk, {}).get(k):
                    iss.append(f"markets.{mk}.{k} civic {a['markets'][mk].get(k)!r} != statewide {b['markets'][mk].get(k)!r}")
        rows.append({"family": "cross-feed", "race": race, "versions": "civic v1 vs statewide v1", "feed": f"{a['election'].get('feed')} vs {b['election'].get('feed')}",
                     "state": a["election"].get("state"), "consistent": not iss, "issues": " | ".join(iss)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 400, "display.float_format", lambda x: f"{x:.4g}")
    if what in ("audit", "all"):
        a = audit()
        a.to_csv(PUB / "us_benchmark_audit.csv", index=False)
        print(a.groupby("family").agg(races=("race", "size"), consistent=("consistent", "sum")).to_string())
        print(a[~a["consistent"]][["family", "race", "versions", "issues"]].to_string(index=False, max_colwidth=300))
    if what not in ("pre", "all"):
        return
    inp = uu.load_inputs()
    sw = statewide_panel(inp)
    keep = ["race", "t", "st", "office", "special", "s2", "n_polls", "P", "swing_abs", "hist_miss", "flags", "C1", "C2", "C3", "C4"]
    for m in STATEWIDE_MODELS:
        keep += [f"{m}_mu", f"{m}_sd", f"{m}_p", f"{m}_crps"]
    sw[keep].round(5).to_csv(PUB / "us_benchmark_statewide_races.csv", index=False)
    cp = county_panel(inp, sw)
    cp.round(5).to_csv(PUB / "us_benchmark_county_races.csv", index=False)
    hp = house_panel(inp)
    # summaries
    sk = class_sets(sw, {"C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4"})
    S = summarise(sw, STATEWIDE_MODELS, sk, "statewide")
    hk = class_sets(hp, {"H1": "H1", "H2": "H2"})
    H = summarise(hp, ["v1", "v2_reg", "v2", "unified"], hk, "house")
    cyc_rows = []
    for c in ub.TEST:
        x = sw[sw["t"] == c]
        cyc_rows.append(summarise(x, STATEWIDE_MODELS, [("cycle", str(c), np.ones(len(x), bool))], "statewide"))
    for c in uh.TEST:
        x = hp[hp["t"] == c]
        cyc_rows.append(summarise(x, ["v1", "v2_reg", "v2", "unified"], [("cycle", str(c), np.ones(len(x), bool))], "house"))
    pd.concat([S, H, *cyc_rows]).round(5).to_csv(PUB / "us_benchmark_summary.csv", index=False)
    CS = county_summary(cp)
    CS.round(5).to_csv(PUB / "us_benchmark_county_summary.csv", index=False)
    frames = [statewide_frame(sw), *county_frames(cp, sw), house_frame(hp)]
    L = run_tests(frames)
    L = recommend(L)
    L.round(6).to_csv(PUB / "us_benchmark_comparisons.csv", index=False)
    D = pd.concat([descriptive(sw, "v3", ["v1", "v2", "unified"], ["C1", "C2"], "statewide"),
                   descriptive(hp, "v2", ["v1", "unified"], ["H1", "H2"], "house")])
    D.round(6).to_csv(PUB / "us_benchmark_descriptive.csv", index=False)
    RC = recommendation(L)
    RC.round(5).to_csv(PUB / "us_benchmark_recommendation.csv", index=False)
    print(S[S["scheme"].isin(["C1", "C2"])].to_string(index=False))
    print(CS.to_string(index=False))
    print(H[H["scheme"] == "H1"].to_string(index=False))
    print(L[L["status"] == "tested"][["layer", "candidate", "scheme", "class", "n", "improvement", "ci_lo", "ci_hi", "p_holm", "recommended"]].to_string(index=False))
    print("tests:", (L["status"] == "tested").sum(), "recommended:", int(L["recommended"].sum()), "untested(n<30):", (L["status"] != "tested").sum())


if __name__ == "__main__":
    main()
