"""Leave-one-cycle-out backtest of the unified pre-count model (2016-2024) and the final fit for 2026.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_backtest.py [state|county|turnout|nat|final|all]

Writes results/us_unified/*.csv (gitignored), $BRUNDER_DATA/processed/us2026/unified/backtest_*.csv and
data_public/us_unified_params.json (`final`). Every hyper-parameter (national model, blend k/p, correction, sd bins, county
ridge λ) is chosen on the training cycles only (nested out-of-fold), then applied to the held-out cycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us_unified as uu

OUT = uu.ROOT / "results" / "us_unified"
UOUT = uu.U
TEST = (2016, 2018, 2020, 2022, 2024)
V3_BINS = ((2, 0.0429), (10, 0.0334), (10**6, 0.0228))      # brunder.us.POLL_SD_BINS (registered)


def v3_sd(n: np.ndarray) -> np.ndarray:
    return np.array([next(sd for hi, sd in V3_BINS if k <= hi) for k in n])


def loco_bin_sd(df: pd.DataFrame, pred: np.ndarray, floor: float = 0.015) -> np.ndarray:
    """sd per race from RMSE of the residuals of `pred` by n-poll bin over the OTHER cycles (best case for a baseline)."""
    res = df["s2"].values - pred
    b = uu.n_bin(df["n_polls"].values)
    out = np.zeros(len(df))
    for c in df["t"].unique():
        te = (df["t"] == c).values
        for bb in np.unique(b[te]):
            tr = ~te & (b == bb)
            sd = np.sqrt((res[tr] ** 2).mean()) if tr.sum() >= 5 else np.sqrt((res[~te] ** 2).mean())
            out[te & (b == bb)] = max(sd, floor)
    return out


# ------------------------------------------------------------------ national


def nat_backtest(n: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """LOCO RMSE of the national swing for the generic ballot alone, with extras and ridge λ; returns the table and the
    chosen specification (smallest LOCO RMSE; ties → fewer terms)."""
    rows = []
    specs = [((), 0.0)] + [((e,), lam) for e in uu.NAT_EXTRAS for lam in (0.1, 1.0)] + [(tuple(uu.NAT_EXTRAS), lam) for lam in (0.1, 1.0, 10.0)]
    base = n[n["cycle"] < 2026]
    for ex, lam in specs:
        pr = uu.nat_oof(base, (), ex, lam)
        e = np.array([pr[int(c)] - y for c, y in zip(base["cycle"], base["y_c"])])
        rows.append({"extras": "+".join(ex) or "-", "lam": lam, "loco_rmse": float(np.sqrt((e**2).mean()) * 100),
                     "loco_mae": float(np.abs(e).mean() * 100)})
    raw = (base["gb_d"] - base["y_c"]).values
    rows.append({"extras": "raw generic ballot (a=0,b=1)", "lam": np.nan, "loco_rmse": float(np.sqrt((raw**2).mean()) * 100),
                 "loco_mae": float(np.abs(raw).mean() * 100)})
    mean_only = np.array([base[base.cycle != c]["y_c"].mean() - y for c, y in zip(base["cycle"], base["y_c"])])
    rows.append({"extras": "mean swing only", "lam": np.nan, "loco_rmse": float(np.sqrt((mean_only**2).mean()) * 100),
                 "loco_mae": float(np.abs(mean_only).mean() * 100)})
    t = pd.DataFrame(rows)
    cand = t[t["lam"].notna()].sort_values(["loco_rmse"]).iloc[0]
    ex = tuple(e for e in cand["extras"].split("+") if e != "-")
    # prefer the generic-ballot-only model unless an extra improves LOCO RMSE by >= 10 %
    gb_only = t[(t["extras"] == "-")].iloc[0]
    if cand["loco_rmse"] > 0.90 * gb_only["loco_rmse"]:
        ex, lam = (), 0.0
    else:
        lam = float(cand["lam"])
    return t, {"extras": ex, "lam": lam}


# ------------------------------------------------------------------ state


def state_backtest(R: pd.DataFrame, n: pd.DataFrame, spec: dict, chrono: bool = False) -> pd.DataFrame:
    base = n[n["cycle"] < 2026]
    rows = []
    for c in TEST:
        train_cycles = [x for x in TEST if (x < c if chrono else x != c)]
        if len(train_cycles) < 2:
            continue
        tr = R[R["t"].isin(train_cycles)]
        te = R[R["t"] == c]
        sw_train = {x: uu.nat_oof(base, (c,), spec["extras"], spec["lam"])[x] for x in train_cycles}
        # nat_oof excluded cycle x itself too; the test cycle is never in the fit
        m = uu.fit_state(tr, sw_train)
        nmask = (base["cycle"] < c) if chrono else (base["cycle"] != c)
        nm = uu.fit_nat(base[nmask], spec["extras"], spec["lam"])
        sw_te = float(uu.predict_nat(nm, base[base["cycle"] == c].iloc[0])[0])
        p = uu.predict_state(m, te, sw_te)
        rows.append(te.reset_index(drop=True).assign(mu=p["mu"].values, sd=p["sd"].values, F=p["F"].values, sw_hat=sw_te,
                                                     k=m["k"], p_=m["p"]))
    return pd.concat(rows, ignore_index=True)


def state_tables(res: pd.DataFrame) -> pd.DataFrame:
    d = res.assign(v1=res["O"].fillna(res["L"]))
    d["sd_v1"] = loco_bin_sd(d, d["v1"].values)
    d["sd_v3"] = v3_sd(d["n_polls"].values)
    d["sd_v3l"] = loco_bin_sd(d.assign(), d["P"].fillna(0.5).values)
    out = []
    for name, m in (("all races", np.ones(len(d), bool)), ("polled", (d["n_polls"] > 0).values), ("unpolled", (d["n_polls"] == 0).values),
                    ("polled n<=5", ((d["n_polls"] > 0) & (d["n_polls"] <= 5)).values), ("polled n>5", (d["n_polls"] > 5).values)):
        x = d[m]
        out.append({"subset": name, **uu.score(x["s2"], x["mu"], x["sd"], "unified")})
        out.append({"subset": name, **uu.score(x["s2"], x["v1"], x["sd_v1"], "v1 last race (sd refit)")})
        if name != "unpolled" and (x["n_polls"] > 0).all():
            out.append({"subset": name, **uu.score(x["s2"], x["P"], x["sd_v3"], "v3 poll avg (registered sd)")})
            out.append({"subset": name, **uu.score(x["s2"], x["P"], x["sd_v3l"], "v3 poll avg (sd refit)")})
        if name == "unpolled":
            out.append({"subset": name, **uu.score(x["s2"], x["F"], x["sd"], "fundamentals only")})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ county


def prep_county(inp: dict, R: pd.DataFrame) -> pd.DataFrame:
    C = uu.county_table(inp, R)
    C = uu.add_dev(C, uu.SHARE_FEATS)
    C = uu.turnout_features(C)
    C["S_act"] = uu.state_share_actual(C)
    return C


def state_base_votes(C: pd.DataFrame) -> pd.DataFrame:
    g = C.groupby("race").agg(vp=("vp_b", "sum"), vo=("vo_b", lambda x: x.sum(min_count=1)), vact=("v2", "sum"), t=("t", "first"),
                              st=("st", "first"), office=("office", "first"), nvo=("vo_b", lambda x: x.notna().mean()), oy=("oy", "max"))
    g["mid"] = g["t"] % 4 == 2
    g["omid"] = g["oy"] % 4 == 2
    g["lr_p"] = np.log(g["vact"] / g["vp"])
    g["lr_o"] = np.log(g["vact"] / g["vo"].where(g["nvo"] > 0.9))
    return g


def fit_vstate(g: pd.DataFrame) -> dict:
    """Mean log ratios of a race's two-party votes to its two bases: the last presidential vote (by race type) and the last
    same-office vote (by race type and base type: midterm→midterm, president→midterm, ...)."""
    out = {}
    for mid in (True, False):
        s = g[(g["mid"] == mid) & (g["vact"] > 0)]
        out[f"p_{int(mid)}"] = float(s["lr_p"].mean()) if len(s) else 0.0
        o = s["lr_o"].dropna()
        out[f"o_{int(mid)}"] = float(o.mean()) if len(o) else None
        for omid in (True, False):
            oo = s[s["omid"] == omid]["lr_o"].dropna()
            out[f"o_{int(mid)}{int(omid)}"] = float(oo.mean()) if len(oo) else out[f"o_{int(mid)}"]
    return out


def predict_vstate(vm: dict, vp: float, vo: float | None, mid: bool, omid: bool = True) -> float:
    a = np.log(vp) + vm[f"p_{int(mid)}"]
    mo = vm.get(f"o_{int(mid)}{int(omid)}")
    if vo and vo > 0 and mo is not None:
        return float(np.exp(0.5 * a + 0.5 * (np.log(vo) + mo)))
    return float(np.exp(a))


def county_backtest(C: pd.DataFrame, state_res: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Per held-out cycle: county deviation model fitted on the other cycles (λ chosen inside them), scored (a) given the true
    state share and (b) as deployed with the predicted state share; turnout shares and statewide votes alike."""
    key = state_res.assign(race=lambda d: d["t"].astype(str) + "_" + d["st"] + "_" + d["office"] + d["special"].astype(int).astype(str)).set_index("race")
    rows_a, rows_b, coef = [], [], {}
    gb = state_base_votes(C)
    for c in TEST:
        tr, te = C[(C["t"] != c) & (C["t"].isin(TEST))], C[C["t"] == c]
        lam = uu.select_lam(tr)
        sm = uu.fit_share(tr, lam)
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        tm_full = uu.fit_turnout(tr, 0.0)
        vm = fit_vstate(gb[gb["t"] != c])
        coef[c] = {"lam": lam, "share": sm["b"], "turn": tm["b"]}
        for race, d in te.groupby("race"):
            if race not in key.index or d["s2"].isna().any():
                continue
            S_act = float(d["S_act"].iloc[0])
            k = key.loc[race]
            S_hat, polled = float(k["mu"]), k["n_polls"] > 0
            y = d["s2"].values
            w = d["v2"].values
            # (a) county deviation given the true state share
            dev_u = uu.predict_dev(sm, d) + sm["b0"]
            dev_v1 = d["so_f_dv"].values
            dev_p = d["sp_b_dv"].values
            for nm, dv in (("unified", dev_u), ("v1 (last race, shifted)", dev_v1), ("last president, shifted", dev_p)):
                e = (S_act + dv) - y
                rows_a.append({"t": c, "race": race, "model": nm, "n": len(d), "w_sqerr": (e**2 * w).sum(), "w_abserr": (np.abs(e) * w).sum(),
                               "w": w.sum(), "sqerr": (e**2).sum(), "abserr": np.abs(e).sum()})
            # (b) deployed priors: predicted state share, county V
            vp, vo = float(d["vp_b"].sum()), float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            pr = uu.county_prior(d, sm, tm, S_hat, Vs)
            pr_core = uu.county_prior(d, sm, tm_full, S_hat, Vs)
            # v1: last same-office result unshifted, V = last same-office votes (pres base: x0.72 for midterms)
            V1 = d["vo_b"].fillna(d["vp_b"] * (0.72 if c % 4 == 2 else 1.0)).values
            s1 = d["so_f"].values
            base_share = np.average(s1, weights=V1)
            s3 = np.clip(s1 + (k["P"] - base_share), 0.005, 0.995) if polled else s1
            tot_true = w.sum()
            for nm, s0, V in (("unified", pr["s0"].values, pr["V"].values), ("unified (+demographic turnout terms)", pr["s0"].values, pr_core["V"].values),
                              ("v1", s1, V1), ("v3 (polls, uniform shift)", s3, V1)):
                if nm == "v3 (polls, uniform shift)" and not polled:
                    continue
                shp = V / V.sum()
                tv = float(np.abs(shp - w / tot_true).sum() / 2)
                rows_b.append({"t": c, "race": race, "model": nm, "polled": bool(polled), "n": len(d),
                               "w_abserr": (np.abs(s0 - y) * w).sum(), "w": tot_true, "w_sqerr": ((s0 - y) ** 2 * w).sum(),
                               "tv_share": tv, "v_state_logerr": float(np.log(V.sum() / tot_true))})
    return pd.DataFrame(rows_a), pd.DataFrame(rows_b), coef


def county_tables(a: pd.DataFrame, b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ta = a.groupby("model").apply(lambda g: pd.Series({"races": g["race"].nunique(), "wRMSE_pts": 100 * np.sqrt(g["w_sqerr"].sum() / g["w"].sum()),
                                                      "wMAE_pts": 100 * g["w_abserr"].sum() / g["w"].sum(),
                                                      "RMSE_pts_unweighted": 100 * np.sqrt(g["sqerr"].sum() / g["n"].sum())}), include_groups=False)
    tb = b.groupby("model").apply(lambda g: pd.Series({"races": g["race"].nunique(), "county_share_wMAE_pts": 100 * g["w_abserr"].sum() / g["w"].sum(),
                                                      "county_share_wRMSE_pts": 100 * np.sqrt(g["w_sqerr"].sum() / g["w"].sum()),
                                                      "turnout_share_TV_pct": 100 * g["tv_share"].mean(),
                                                      "state_votes_abs_logerr": g["v_state_logerr"].abs().mean(),
                                                      "state_votes_logerr_bias": g["v_state_logerr"].mean()}), include_groups=False)
    return ta, tb


# ------------------------------------------------------------------ final fit


def final_fit(R: pd.DataFrame, C: pd.DataFrame, n: pd.DataFrame, spec: dict) -> dict:
    base = n[n["cycle"] < 2026]
    sw_train = uu.nat_oof(base, (), spec["extras"], spec["lam"])
    sw_train = {int(c): sw_train[int(c)] for c in TEST}
    state = uu.fit_state(R[R["t"].isin(TEST)], sw_train)
    nm = uu.fit_nat(base, spec["extras"], spec["lam"])
    Ct = C[C["t"].isin(TEST)]
    lam = uu.select_lam(Ct)
    share = uu.fit_share(Ct, lam)
    turn = uu.fit_turnout(Ct, 0.0, uu.TURN_FEATS)
    vm = fit_vstate(state_base_votes(Ct))
    return {"nat": nm, "state": state, "share": share, "turnout": turn, "vstate": vm,
            "built_from_cycles": list(TEST), "note": "fitted on 2016-2024 only; 2026 uses no 2026 outcome"}


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    OUT.mkdir(parents=True, exist_ok=True)
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    R = uu.race_table(inp)
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda x: f"{x:.4g}")
    nt, spec = nat_backtest(n)
    nt.to_csv(OUT / "nat_loco.csv", index=False)
    print("== national swing, leave-one-cycle-out (points of two-party share)\n", nt.to_string(index=False), "\nchosen:", spec)
    if what in ("state", "all"):
        res = state_backtest(R, n, spec)
        res.to_csv(OUT / "state_loco_races.csv", index=False)
        tab = state_tables(res)
        tab.to_csv(OUT / "state_loco_summary.csv", index=False)
        print("\n== statewide race share/P(win), LOCO 2016-2024\n", tab.to_string(index=False))
        byc = pd.concat([state_tables(res[res["t"] == c]).assign(cycle=c) for c in TEST if c in set(res["t"])])
        byc[byc["subset"].isin(["polled", "unpolled"])].to_csv(OUT / "state_loco_by_cycle.csv", index=False)
        ch = state_backtest(R, n, spec, chrono=True)
        ct = state_tables(ch)
        ct.to_csv(OUT / "state_chrono_summary.csv", index=False)
        print("\n== same, chronological (train on earlier cycles only; 2020/2022/2024)\n", ct.to_string(index=False))
        m = uu.fit_state(R[R["t"].isin(TEST)], {c: v for c, v in uu.nat_oof(n[n.cycle < 2026], (), spec["extras"], spec["lam"]).items() if c in TEST})
        print("\nfinal state fit:", {k: m[k] for k in ("k", "p", "g", "sd", "f", "cycle_common_sd", "oof_mae_polled", "oof_mae_unpolled")})
    if what in ("county", "turnout", "all"):
        res = pd.read_csv(OUT / "state_loco_races.csv") if what != "all" else res
        C = prep_county(inp, R)
        a, b, coef = county_backtest(C, res)
        a.to_csv(OUT / "county_given_state_races.csv", index=False)
        b.to_csv(OUT / "county_deployed_races.csv", index=False)
        ta, tb = county_tables(a, b)
        ta.to_csv(OUT / "county_given_state_summary.csv")
        tb.to_csv(OUT / "county_deployed_summary.csv")
        print("\n== county share deviation given the TRUE state share (vote-weighted, points)\n", ta.to_string())
        print("\n== deployed county priors (predicted state share), vote-weighted points; turnout share TV distance in % of state votes\n", tb.to_string())
        print("\nchosen county λ per held-out cycle:", {c: v["lam"] for c, v in coef.items()})
        bp = b[b["polled"]]
        if len(bp):
            _, tbp = county_tables(a, bp)
            print("\n== deployed, polled races only\n", tbp.to_string())
    if what in ("final", "all"):
        C = prep_county(inp, R) if what == "final" else C
        params = final_fit(R, C, n, spec)
        params["share"]["coef_std_note"] = "b = coefficient on the within-state deviation of each feature (units of the feature)"
        uu.save_params(params)
        sh = pd.Series(params["share"]["b"])
        sd = C[[f + "_dv" for f in uu.SHARE_FEATS]].std().values
        w = (sh.values * sd).round(4)
        print("\nfinal county-share weights (coefficient × sd of the deviation, share points):\n", dict(zip(uu.SHARE_FEATS, w)), "lam", params["share"]["lam"])
        print("final turnout log-share coefficients:", params["turnout"]["b"])
        print("vstate:", params["vstate"], "\nnat:", params["nat"])
        print("params ->", uu.PARAMS_PATH)


if __name__ == "__main__":
    main()
