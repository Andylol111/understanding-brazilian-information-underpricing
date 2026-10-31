"""Unified pre-count model for US House districts: leave-one-cycle-out backtest and the 2026 district priors / configs.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_house_2026.py backtest
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_house_2026.py build

District share (two-party D) = fundamentals F blended with the district poll average (weight k/(k+n^p) on F):
F = b0 + b1·(district presidential lean) + b2·(last House lean, else the presidential one) + b3·(party holding the seat)
+ b4·(national swing from the generic ballot, out-of-fold). Cycles 2014-2024 on same-line transitions (UniHouse panel:
`pres_prev_d2`, `d2_prev` exist on the cycle's own lines; 2022 has the 2020 presidential vote on 2022 lines but no lagged
House result). Registered comparators: v1 = 2024 president by district, no shift (sd refit); v2 = + generic-ballot shift
(0.052 sd). Redrawn 2026 districts (crosswalk estimates) get an extra sd judged, not calibrated (REDRAWN_SD).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brunder import us, us_unified as uu      # noqa: E402

ROOT = uu.ROOT
U = uu.U
RES = ROOT / "results" / "us_unified"
HOUSE_PRIORS_OUT = ROOT / "data_public" / "us2026_unified_house_priors.csv"
CFG_OUT = ROOT / "configs" / "us2026_unified" / "house"
HP = ROOT / "data_public" / "us_unified_house_params.json"
TEST = (2014, 2016, 2018, 2020, 2022, 2024)
FEATS = ["Pc", "Hc_f", "hold", "swing_hat"]
REDRAWN_SD = 0.02        # extra sd (two-party share) of a crosswalk-estimated district prior; judgment, no same-lines data to calibrate
V2_SD = 0.052            # registered v2 prior sd
LOWSD = 0.015


def house_table(nat: pd.DataFrame) -> pd.DataFrame:
    hp = pd.read_parquet(U / "house_panel.parquet")
    n = nat.set_index("cycle")
    d = hp[hp["year"].isin(TEST) & hp["contested"] & hp["d2"].notna() & hp["pres_prev_d2"].notna()].copy()
    d["t"] = d["year"]
    d["pres_last"] = d["t"].map(n["pres_d2_last"])
    d["Pc"] = d["pres_prev_d2"] - d["pres_last"]
    d["Hc"] = d["d2_prev"] - (d["t"] - 2).map(n["house_d2_nat"])
    d["Hc_f"] = d["Hc"].fillna(d["Pc"])
    d["hold"] = d["hold_party"].map({"D": 1, "R": -1, "DEMOCRAT": 1, "REPUBLICAN": -1}).fillna(0)
    d["P"] = d["poll_avg"]
    d["n_polls"] = d["poll_n"].fillna(0).astype(int)
    d["gb_d"] = d["t"].map(n["gb_avg"] - n["pres_d2_last"])
    d["s2"] = d["d2"]
    d["v2"] = d["dem"] + d["rep"]
    return d.reset_index(drop=True)


def fit_house(tr: pd.DataFrame, sw: dict[int, float], lam: float = 0.0) -> dict:
    tr = tr.assign(swing_hat=tr["t"].map(sw))
    cyc = sorted(tr["t"].unique())
    oof = np.full(len(tr), np.nan)
    for c in cyc:
        te = (tr["t"] == c).values
        b0, b = uu.wridge(tr.loc[~te, FEATS].fillna(0).values, tr.loc[~te, "s2"].values, None, lam, np.zeros(len(FEATS)))
        oof[te] = b0 + tr.loc[te, FEATS].fillna(0).values @ b
    polled = (tr["n_polls"] > 0).values
    best = None
    for k in (0.25, 0.5, 1.0, 2.0, 4.0):
        for p in (0.5, 1.0):
            w = uu._blend_w(tr["n_polls"].values, k, p)
            u = np.where(polled, w * oof + (1 - w) * tr["P"].fillna(0).values, oof)
            e = np.abs(u - tr["s2"].values)[polled].mean()
            if best is None or e < best[0] - 1e-9:
                best = (e, k, p)
    _, k, p = best
    w = uu._blend_w(tr["n_polls"].values, k, p)
    u = np.where(polled, w * oof + (1 - w) * tr["P"].fillna(0).values, oof)
    r = tr["s2"].values - u
    sd = {"0": float(np.sqrt((r[~polled] ** 2).mean())), "1": float(np.sqrt((r[polled] ** 2).mean()))}
    b0, b = uu.wridge(tr[FEATS].fillna(0).values, tr["s2"].values, None, lam, np.zeros(len(FEATS)))
    return {"b0": b0, "b": dict(zip(FEATS, map(float, b))), "k": float(k), "p": float(p), "sd": sd,
            "n_train": int(len(tr)), "n_polled": int(polled.sum())}


def predict_house(m: dict, d: pd.DataFrame, swing: float | dict, extra_sd: np.ndarray | float = 0.0) -> pd.DataFrame:
    d = d.assign(swing_hat=(d["t"].map(swing) if isinstance(swing, dict) else swing))
    F = m["b0"] + d[FEATS].fillna(0).values @ np.array([m["b"][f] for f in FEATS])
    n = d["n_polls"].values
    w = uu._blend_w(n, m["k"], m["p"])
    mu = np.where(n > 0, w * F + (1 - w) * d["P"].fillna(0).values, F)
    sd = np.where(n > 0, m["sd"]["1"], m["sd"]["0"])
    sd = np.sqrt(np.maximum(sd, LOWSD) ** 2 + np.asarray(extra_sd) ** 2)
    return pd.DataFrame({"F": F, "mu": mu, "sd": sd, "p_win": norm.cdf((mu - 0.5) / sd)}, index=d.index)


def refit_sd(df: pd.DataFrame, pred: np.ndarray) -> np.ndarray:
    res = df["s2"].values - pred
    out = np.zeros(len(df))
    for c in df["t"].unique():
        te = (df["t"] == c).values
        out[te] = max(np.sqrt((res[~te] ** 2).mean()), LOWSD)
    return out


def backtest(inp: dict) -> pd.DataFrame:
    n = uu.nat_table(inp["nat"])
    base = n[n["cycle"] < 2026]
    H = house_table(n)
    rows = []
    for c in TEST:
        tr, te = H[H["t"] != c], H[H["t"] == c]
        sw = {x: uu.nat_oof(base, (c,), (), 0.0)[x] for x in tr["t"].unique()}
        m = fit_house(tr, sw)
        nm = uu.fit_nat(base[base["cycle"] != c], (), 0.0)
        s = float(uu.predict_nat(nm, base[base["cycle"] == c].iloc[0])[0])
        p = predict_house(m, te, s)
        rows.append(te.assign(mu=p["mu"], sd=p["sd"], F=p["F"], k=m["k"]))
    d = pd.concat(rows, ignore_index=True)
    d["v1"] = d["pres_prev_d2"]
    d["v2"] = np.clip(d["pres_prev_d2"] + d["gb_d"], 0.01, 0.99)
    d["sd_v1"] = refit_sd(d, d["v1"].values)
    d["sd_v2r"] = refit_sd(d, d["v2"].values)
    return d


def tables(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for name, m in (("all contested", np.ones(len(d), bool)), ("polled", (d["n_polls"] > 0).values), ("unpolled", (d["n_polls"] == 0).values),
                    ("close (2-party share 0.40-0.60)", d["s2"].between(0.4, 0.6).values)):
        x = d[m]
        out.append({"subset": name, **uu.score(x["s2"], x["mu"], x["sd"], "unified")})
        out.append({"subset": name, **uu.score(x["s2"], x["v1"], x["sd_v1"], "v1 president, no shift (sd refit)")})
        out.append({"subset": name, **uu.score(x["s2"], x["v2"], np.full(len(x), V2_SD), "v2 + generic ballot shift (sd 0.052)")})
        out.append({"subset": name, **uu.score(x["s2"], x["v2"], x["sd_v2r"], "v2 shift (sd refit)")})
        if name == "polled":
            out.append({"subset": name, **uu.score(x["s2"], x["P"], x["sd"].clip(lower=0.03), "district poll average alone (sd 0.0429 bin)")})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ 2026


def build_house(inp: dict | None = None) -> pd.DataFrame:
    inp = inp or uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    base = n[n["cycle"] < 2026]
    H = house_table(n)
    sw = uu.nat_oof(base, (), (), 0.0)
    m = fit_house(H, {c: sw[c] for c in TEST})
    nm = uu.fit_nat(base, (), 0.0)
    swing = float(uu.predict_nat(nm, n[n["cycle"] == 2026].iloc[0])[0])
    D = pd.read_csv(U / "house_districts_2026.csv")
    hp = pd.read_parquet(U / "house_panel.parquet")
    w24 = hp[hp["year"] == 2024].set_index("did")
    r = n.set_index("cycle").loc[2026]
    d = pd.DataFrame({"did": D["did"], "st": D["st"].str.lower(), "redrawn": D["redrawn"]})
    d["pres_prev_d2"] = D["pres24_d2"]
    d["Pc"] = D["pres24_d2"] - r["pres_d2_last"]
    d["Hc"] = D["house24_core_d2"] - n.set_index("cycle").loc[2024, "house_d2_nat"]
    d["Hc_f"] = d["Hc"].fillna(d["Pc"])
    wp = D["house24_core_winner_party"].map({"D": 1, "R": -1, "DEMOCRAT": 1, "REPUBLICAN": -1}).fillna(0)
    d["hold"] = wp
    d["P"], d["n_polls"] = D["poll_avg"], D["poll_n"].fillna(0).astype(int)
    d["t"] = 2026
    p = predict_house(m, d, swing, np.where(d["redrawn"], REDRAWN_SD, 0.0))
    d = pd.concat([d, p], axis=1)
    # county pieces -> priors
    hp_ = pd.read_csv(ROOT / "data_public" / "us2026_house_priors.csv", comment="#")
    gam = float(sum(uu.load_params()["share"]["b"][f] for f in uu.SHARE_CORE))
    # House vote vs the presidential vote of the same lines: log ratio of two-party votes in midterm cycles (2018, 2022)
    mids = H[H["t"].isin([2018, 2022]) & H["pres_prev_dem"].notna()]
    ratio = float(np.exp(np.mean(np.log(mids["v2"] / (mids["pres_prev_dem"] + mids["pres_prev_rep"])))))
    parts, cfgs = [], {}
    for p_, c in [(p, tomllib.loads(p.read_text())) for p in sorted((ROOT / "configs" / "us2026_house").glob("*_v1.toml"))]:
        src_old = c["prior"]["share"]
        did = src_old.split("_")[0].upper()
        row = d[d["did"] == did]
        if row.empty:
            print("no district", did)
            continue
        row = row.iloc[0]
        g = hp_[hp_["source"] == src_old]
        v = g["dem"] + g["rep"]
        sp = g["dem"] / v
        sp_d = g["dem"].sum() / v.sum()
        s0 = np.clip(row["mu"] + gam * (sp - sp_d), 0.02, 0.98)
        s0 = np.clip(s0 + row["mu"] - np.average(s0, weights=v), 0.02, 0.98)
        V = v * ratio
        src = f"uni_house_{did.lower()}_2026"
        parts.append(pd.DataFrame({"source": src, "unit_id": g["unit_id"], "dem": (s0 * V).round(2), "rep": ((1 - s0) * V).round(2), "total": V.round(2)}))
        cfgs[did] = (p_, c, row, src)
    pri = pd.concat(parts, ignore_index=True)
    header = ("# Unified pre-count priors for the 2026 House districts with a configured night feed (analysis/us_unified_house_2026.py).\n"
              "# source uni_house_<st>-<nn>_2026: unit = the county's part of the district (as data_public/us2026_house_priors.csv);\n"
              "# dem + rep = expected two-party votes, dem / (dem + rep) = prior two-party Democratic share. MIT Election Data and\n"
              "# Science Lab (CC0), Census block equivalency files (public domain), VoteHub district polls (CC BY 4.0), 538 history\n"
              "# (CC BY 4.0), parameters fitted on 2014-2024 same-line cycles. No 2026 count data.\n")
    HOUSE_PRIORS_OUT.write_text(header + pri.to_csv(index=False))
    from us_unified_2026 import unified_config
    CFG_OUT.mkdir(parents=True, exist_ok=True)
    for did, (p_, c, row, src) in cfgs.items():
        r_ = pd.Series({"mu": row["mu"], "sd": row["sd"], "p_win": row["p_win"], "n_polls": row["n_polls"], "poll_avg": row["P"]})
        txt = unified_config(p_, c, r_, uid=f"us2026_unified_house_{did.lower()}_v1", src=src,
                             priors_name=HOUSE_PRIORS_OUT.name, fitted="2014-2024 same-line cycles")
        (CFG_OUT / f"us2026_unified_house_{did.lower()}_v1.toml").write_text(txt)
    RES.mkdir(parents=True, exist_ok=True)
    d.to_csv(RES / "2026_house_districts.csv", index=False)
    HP.write_text(__import__("json").dumps({"house": m, "swing_2026": swing, "gamma_piece": gam, "house_to_pres_votes": ratio,
                                            "redrawn_sd": REDRAWN_SD}, indent=1, default=float))
    print(f"swing {swing:.4f}; {len(cfgs)} House configs; {len(d)} districts forecast; mean P(D) sum = {d['p_win'].sum():.1f} of {len(d)}")
    print(d[d["did"].isin(list(cfgs))][["did", "n_polls", "P", "F", "mu", "sd", "p_win"]].round(3).head(30).to_string(index=False))
    return d


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    inp = uu.load_inputs()
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda x: f"{x:.4g}")
    if what == "backtest":
        d = backtest(inp)
        RES.mkdir(parents=True, exist_ok=True)
        d.to_csv(RES / "house_loco_districts.csv", index=False)
        t = tables(d)
        t.to_csv(RES / "house_loco_summary.csv", index=False)
        print(t.to_string(index=False))
        by = pd.concat([tables(d[d["t"] == c]).assign(cycle=c) for c in TEST])
        by[by["subset"] == "all contested"].to_csv(RES / "house_loco_by_cycle.csv", index=False)
        print(by[by["subset"] == "all contested"][["cycle", "label", "n", "mae", "brier", "cov90"]].to_string(index=False))
    else:
        build_house(inp)


if __name__ == "__main__":
    main()
