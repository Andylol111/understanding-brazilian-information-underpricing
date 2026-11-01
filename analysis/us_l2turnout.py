"""L2-informed expected votes for the unified challenger: out-of-sample validation (county, House piece) and the 2026 sensitivity.

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_l2turnout.py build      # derived county / piece frames (SSD)
    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_l2turnout.py county     # statewide-race county shares, 2022 + 2024
    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_l2turnout.py house      # House county x district pieces, 2024 lines
    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_l2turnout.py sens       # 2026: L2 shares vs the challenger's

Writes results/us_l2turnout/*.csv (gitignored) and data_public/us_l2turnout_*.csv (aggregate derived statistics only).
This analysis was conducted using data from the Redistricting Data Hub.

The bars (docs/us_l2turnout.md section 1): county component, written before any out-of-sample number was computed: adopt only if,
in the strict out-of-sample variant, the L2 expected votes beat the challenger's county turnout shares in BOTH 2022 and 2024 by
>= 10 % in mean total-variation distance with a state-clustered bootstrap interval of the difference below zero, and are not
worse at the statewide level and at the House-piece level. The redrawn-state piece split has its own bar (see split_section).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_unified_backtest as bt

from brunder import us_l2turnout as L
from brunder import us_unified as uu

OUT = uu.ROOT / "results" / "us_l2turnout"
PUB = uu.ROOT / "data_public"
TESTS = (2022, 2024)
NBOOT = 4000


def tv(p: np.ndarray, a: np.ndarray) -> float:
    """Total-variation distance between two shares (inputs are renormalised)."""
    p, a = np.asarray(p, float), np.asarray(a, float)
    return float(np.abs(p / p.sum() - a / a.sum()).sum() / 2)


def md(df: pd.DataFrame, nd: int = 4) -> str:
    d = df.reset_index()
    cols = list(d.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in d.iterrows():
        lines.append("| " + " | ".join(f"{v:.{nd}f}" if isinstance(v, (float, np.floating)) else str(v) for v in r) + " |")
    return "\n".join(lines)


# ------------------------------------------------------------------ baselines: the challenger and v1 out of sample


def baselines() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Per held-out cycle (2022, 2024): the challenger's county turnout model fitted on the OTHER cycles of 2016-2024 (as
    `us_unified_backtest.county_backtest`) and v1's expected votes, for every statewide race with a result. Returns the county rows
    (`V_ch`, `V_v1`, `vp_b` = last presidential two-party votes, `w` = actual two-party votes), the full turnout-feature table and
    the challenger's statewide level (V_state, vm) per race."""
    inp = uu.load_inputs()
    R = uu.race_table(inp)
    C = bt.prep_county(inp, R)
    gb = bt.state_base_votes(C)
    rows, lev = [], {}
    for c in TESTS:
        tr, te = C[(C["t"] != c) & (C["t"].isin(bt.TEST))], C[C["t"] == c]
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = bt.fit_vstate(gb[gb["t"] != c])
        lev[c] = vm
        for race, d in te.groupby("race"):
            vp = float(d["vp_b"].sum())
            vo = float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = bt.predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            sh = uu.predict_turnout_share(tm, d)
            V1 = d["vo_b"].fillna(d["vp_b"] * (0.72 if c % 4 == 2 else 1.0)).values
            rows.append(pd.DataFrame({"t": c, "race": race, "st": d["st"].str.upper().values, "fips": d["fips"].values, "w": d["v2"].values,
                                      "V_ch": Vs * sh.values, "V_v1": V1, "vp_b": d["vp_b"].values, "Vs_ch": Vs}))
    return pd.concat(rows, ignore_index=True), C, lev


# ------------------------------------------------------------------ L2 expected votes (county)


def model_set(fr: dict) -> dict:
    """Modelled ballots M(roll, rates) for the designs. Rates are the state-pooled group turnout rates of the 2020 presidential election
    of the c22 file (ethnicity and gender, snapshot 2023-02); the base roll is that file's own 2020 roll (same snapshot, so a county's
    growth 2020 -> 2022 is not distorted by two different snapshots), the target rolls are c22's 2022 roll and c24's 2024 roll."""
    c22, c24 = fr["c22"], fr["c24"]
    out = {}
    for dims in (("eth",), ("gen",), ("eth", "gen")):
        rates = L.pooled_rates(c22, 2020, dims)
        out[dims] = {"base": L.model_ballots(c22, 2020, rates, dims)["ballots"], "r22": L.model_ballots(c22, 2022, rates, dims)["ballots"],
                     "r24": L.model_ballots(c24, 2024, rates, dims)["ballots"]}
    return out


def design_table(B: pd.DataFrame, MT: dict[int, pd.Series], MB: pd.Series, lam: float) -> pd.DataFrame:
    """County rows with the L2 prediction V_l2 = M_T * (vp_b / M_B)^lam, for races where the L2 counties cover >= 98 % of the race's."""
    rows = []
    for (c, race), d in B.groupby(["t", "race"]):
        if d["st"].iloc[0] == "CT" or c not in MT:
            continue
        dd = d.set_index("fips")
        common = dd.index.intersection(MT[c].index).intersection(MB.index)
        if len(common) < 0.98 * len(dd):
            continue
        dd = dd.loc[common]
        vp = dd["vp_b"].to_numpy()
        ok = (vp > 0) & np.isfinite(vp)
        if not ok.all():
            vp = np.where(ok, vp, dd["V_v1"].to_numpy())
        V = MT[c].loc[common].to_numpy() * (vp / MB.loc[common].to_numpy()) ** lam
        rows.append(dd.assign(V_l2=V).reset_index())
    return pd.concat(rows, ignore_index=True)


def score_table(T: pd.DataFrame, cols=("V_ch", "V_v1", "vp_b", "V_l2")) -> pd.DataFrame:
    """Per-race TV distance of each column's county share to the actual share and the median / weighted mean absolute % error of the
    county's votes after scaling to the true state total."""
    r = []
    for (c, race), g in T.groupby(["t", "race"]):
        row = {"t": c, "race": race, "st": g["st"].iloc[0], "n": len(g)}
        for k in cols:
            v = g[k].fillna(g["V_v1"]).to_numpy()
            row["tv_" + k] = tv(v, g["w"].to_numpy())
            e = np.abs(v / v.sum() * g["w"].sum() / g["w"].to_numpy() - 1)
            row["med_" + k] = float(np.median(e))
        r.append(row)
    return pd.DataFrame(r)


def boot_diff(s: pd.DataFrame, a: str, b: str, nboot: int = NBOOT, seed: int = 1) -> tuple[float, float, float]:
    """Mean of tv_a - tv_b over races with a state-clustered bootstrap 90 % interval."""
    d = (s["tv_" + a] - s["tv_" + b]).groupby(s["st"]).agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(nboot, len(d)))
    num, den = d["sum"].to_numpy()[idx].sum(axis=1), d["count"].to_numpy()[idx].sum(axis=1)
    est = (num / den)
    return float(d["sum"].sum() / d["count"].sum()), float(np.quantile(est, 0.05)), float(np.quantile(est, 0.95))


def blend_shares(g: pd.DataFrame, w: float) -> np.ndarray:
    lp = (1 - w) * np.log(g["V_ch"] / g["V_ch"].sum()) + w * np.log(g["V_l2"] / g["V_l2"].sum())
    return np.exp(lp).to_numpy()


# ------------------------------------------------------------------ the county section


def county_section() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    fr = L.build_county()
    B, C, lev = baselines()
    MS = model_set(fr)
    res: dict = {}

    # survivorship / snapshot diagnostics: the L2 2020 ballots against the official presidential two-party votes
    cmp = C[C["t"] == 2024][["st", "fips", "vp_b"]].drop_duplicates("fips")
    sv = []
    for k, col in (("b20 (2021-08 roll)", fr["b20"]["2020.voted.all"]), ("c22 (2023-02 roll)", fr["c22"]["2020.voted.all"])):
        x = cmp.assign(l2=cmp["fips"].map(col)).dropna()
        x = x[(x["vp_b"] > 0) & (x["l2"] > 0) & (x["st"] != "ct")]
        lr = np.log(x["l2"] / x["vp_b"])
        lr_c = lr - lr.groupby(x["st"]).transform("mean")
        sv.append({"file": k, "counties": len(x), "state-total ratio (L2 voted / official two-party)": float(x["l2"].sum() / x["vp_b"].sum()),
                   "sd of county log ratio within state": float(lr_c.std()), "vote-weighted sd": float(np.sqrt(np.average(lr_c**2, weights=x["vp_b"])))})
    res["survivorship"] = pd.DataFrame(sv).set_index("file")

    designs = {}
    for dims in MS:
        for lam in (0.0, 0.5, 1.0):
            for nm, MT in (("2022 roll for 2022 (post-election snapshot)", {2022: MS[dims]["r22"]}),
                           ("2024: 2022 roll (two years stale)", {2024: MS[dims]["r22"]}),
                           ("2024: 2024 roll (Dec 2024 snapshot)", {2024: MS[dims]["r24"]})):
                T = design_table(B, MT, MS[dims]["base"], lam)
                if T.empty:
                    continue
                designs[(dims, lam, nm)] = T
    rows = []
    for (dims, lam, nm), T in designs.items():
        s = score_table(T)
        rows.append({"model": "+".join(dims), "lam": lam, "design": nm, "races": len(s), "tv_L2": s["tv_V_l2"].mean(), "tv_challenger": s["tv_V_ch"].mean(),
                     "tv_v1": s["tv_V_v1"].mean(), "tv_last_pres": s["tv_vp_b"].mean(), "med_L2": s["med_V_l2"].mean(), "med_challenger": s["med_V_ch"].mean()})
    res["variants"] = pd.DataFrame(rows)

    # main strict variant: ethnicity + gender, lam = 1; 2022 on the 2022 roll, 2024 on the two-year-stale roll
    dims = ("eth", "gen")
    T22 = designs[(dims, 1.0, "2022 roll for 2022 (post-election snapshot)")]
    T24s = designs[(dims, 1.0, "2024: 2022 roll (two years stale)")]
    T24f = designs[(dims, 1.0, "2024: 2024 roll (Dec 2024 snapshot)")]
    main = {"strict": pd.concat([T22, T24s], ignore_index=True), "fresh 2024 roll": pd.concat([T22, T24f], ignore_index=True)}
    boot = []
    per_cycle = []
    for nm, T in main.items():
        s = score_table(T)
        for c, g in s.groupby("t"):
            per_cycle.append({"design": nm, "t": c, "races": len(g), "tv_L2": g["tv_V_l2"].mean(), "tv_challenger": g["tv_V_ch"].mean(), "tv_v1": g["tv_V_v1"].mean(),
                              "tv_last_pres": g["tv_vp_b"].mean(), "med_ape_L2": g["med_V_l2"].mean(), "med_ape_challenger": g["med_V_ch"].mean(),
                              "races_L2_better": int((g["tv_V_l2"] < g["tv_V_ch"]).sum())})
        for tag, sub in (("pooled", s), ("2022", s[s["t"] == 2022]), ("2024", s[s["t"] == 2024])):
            m, lo, hi = boot_diff(sub, "V_l2", "V_ch")
            boot.append({"design": nm, "races": tag, "n": len(sub), "mean_tv_L2_minus_challenger": m, "ci90_lo": lo, "ci90_hi": hi,
                         "relative": m / sub["tv_V_ch"].mean()})
    res["per_cycle"] = pd.DataFrame(per_cycle)
    res["bootstrap"] = pd.DataFrame(boot)

    # blend, weight chosen on the OTHER cycle
    grid = np.linspace(0, 1, 11)
    bl = []
    for nm, T in main.items():
        tab = {}
        for w in grid:
            for (c, race), g in T.groupby(["t", "race"]):
                tab[(w, c, race)] = tv(blend_shares(g, w), g["w"].to_numpy())
        ser = pd.Series(tab)
        by = ser.groupby(level=[0, 1]).mean().unstack()
        for c in TESTS:
            other = [x for x in TESTS if x != c][0]
            w_star = float(by[other].idxmin())
            bl.append({"design": nm, "held-out": c, "weight chosen on": other, "w*": w_star, "tv_blend_heldout": float(by.loc[w_star, c]),
                       "tv_challenger": float(by.loc[0.0, c]), "tv_L2_alone": float(by.loc[1.0, c]), "best_w_in_hindsight": float(by[c].idxmin()),
                       "tv_best_in_hindsight": float(by[c].min())})
    res["blend"] = pd.DataFrame(bl)

    # diagnostic (not out of sample in time): 2022 group drop-off 2020 -> 2022 learned from the OTHER states' 2022
    dd = []
    c22 = fr["c22"]
    for dm in (("eth",), ("eth", "gen")):
        r22hat = rates_with_dropoff(c22, dm)
        MT = {2022: L.model_ballots(c22, 2022, r22hat, dm)["ballots"]}
        T = design_table(B, MT, MS[dm]["base"], 1.0)
        s = score_table(T)
        m, lo, hi = boot_diff(s, "V_l2", "V_ch")
        dd.append({"groups": "+".join(dm), "races": len(s), "tv_L2_dropoff": s["tv_V_l2"].mean(), "tv_challenger": s["tv_V_ch"].mean(), "tv_last_pres": s["tv_vp_b"].mean(),
                   "diff": m, "ci90_lo": lo, "ci90_hi": hi})
        if dm == ("eth", "gen"):
            Tdrop = T
    res["dropoff_diagnostic"] = pd.DataFrame(dd)
    # blend of the diagnostic with the challenger
    r = []
    for w in grid:
        r.append({"w": w, "tv": float(np.mean([tv(blend_shares(g, w), g["w"].to_numpy()) for _, g in Tdrop.groupby("race")]))})
    res["dropoff_blend"] = pd.DataFrame(r).set_index("w")

    # composition covariates inside the challenger's own turnout regression, leave-one-cycle-out over all five cycles
    res["feature_loco"] = feature_loco(C, fr)
    res["growth_feature_stale2y"] = growth_feature_loco(C, MS, fresh=False)
    res["growth_feature_fresh"] = growth_feature_loco(C, MS, fresh=True)

    # statewide level: the challenger's V_state against an L2 level (last presidential votes x roll growth x the challenger's type ratio)
    lv = []
    for nm, T in (("strict", main["strict"]), ("fresh 2024 roll", main["fresh 2024 roll"])):
        for (c, race), g in T.groupby(["t", "race"]):
            act = g["w"].sum()
            vm = lev[c]
            ratio = np.exp(vm["p_%d" % int(c % 4 == 2)])
            lv.append({"design": nm, "t": c, "race": race, "logerr_challenger": np.log(g["Vs_ch"].iloc[0] / act), "logerr_L2": np.log(g["V_l2"].sum() * ratio / act),
                       "logerr_v1": np.log(g["V_v1"].sum() / act)})
    lv = pd.DataFrame(lv)
    res["level"] = lv.groupby(["design", "t"]).agg(races=("race", "size"), abs_challenger=("logerr_challenger", lambda x: x.abs().mean()), abs_L2=("logerr_L2", lambda x: x.abs().mean()),
                                                    abs_v1=("logerr_v1", lambda x: x.abs().mean()), bias_challenger=("logerr_challenger", "mean"), bias_L2=("logerr_L2", "mean"))
    res["_races_strict"] = score_table(main["strict"])
    return res


def growth_feature_loco(C: pd.DataFrame, MS: dict, fresh: bool) -> pd.DataFrame:
    """Registrant growth as an extra regressor of the challenger's turnout-share model: g = log(M_T / M_base), the composition-adjusted
    growth of the roll since the base (2020) roll, centred on the race's baseline-vote weighted mean. Only 2022 and 2024 have it, so the
    coefficient of the held-out cycle is fitted on the other one (a two-fold leave-one-cycle-out); `fresh` uses the 2024 roll for 2024,
    otherwise the two-year-stale 2022 roll."""
    dims = ("eth", "gen")
    D = C[C["t"].isin(TESTS) & (C["st"] != "ct")].copy()
    M24 = MS[dims]["r24"] if fresh else MS[dims]["r22"]
    M = np.where(D["t"].to_numpy() == 2022, D["fips"].map(MS[dims]["r22"]).to_numpy(), D["fips"].map(M24).to_numpy())
    g = pd.Series(np.log(M / D["fips"].map(MS[dims]["base"]).to_numpy()), index=D.index).replace([np.inf, -np.inf], np.nan)
    ok = g.notna()
    w = D["w0"].where(ok, 0.0)
    D["g_c"] = (g - (g.fillna(0) * w).groupby(D["race"]).transform("sum") / w.groupby(D["race"]).transform("sum")).fillna(0.0)
    rows = []
    for nm, feats in (("challenger features (fitted on the other cycle)", ["x_o_c", "x_h_c", "pop_chg4_c"]),
                      ("+ L2 roll growth", ["x_o_c", "x_h_c", "pop_chg4_c", "g_c"])):
        for c in TESTS:
            tr = D[(D["t"] != c) & D["v2"].notna() & D["l_lp"].notna() & (D["v2"] > 0)]
            te = D[D["t"] == c]
            sh = tr["v2"] / tr["v2"].groupby(tr["race"]).transform("sum")
            b0, b = uu.wridge(tr[feats].to_numpy(), (np.log(sh) - tr["l_lp"]).to_numpy(), np.sqrt(tr["v2"].to_numpy()), 0.0, np.zeros(len(feats)))
            p = pd.Series(np.exp(te["l_lp"].to_numpy() + te[feats].to_numpy() @ b), index=te.index)
            for race, gg in te.groupby("race"):
                rows.append({"model": nm, "t": c, "tv": tv(p.loc[gg.index].to_numpy(), gg["v2"].to_numpy()), "coef_g": float(b[-1]) if "g_c" in feats else np.nan})
    r = pd.DataFrame(rows)
    out = r.groupby(["model", "t"]).agg(tv=("tv", "mean"), races=("tv", "size"), coef_g=("coef_g", "first")).unstack()
    out.columns = [f"{a}_{b}" for a, b in out.columns]
    return out


def rates_with_dropoff(df: pd.DataFrame, dims: tuple[str, ...], y0: int = 2020, y1: int = 2022) -> dict:
    """State group rates of y0 scaled by the group drop-off r_{y1,g} / r_{y0,g} pooled over the OTHER states (leave-one-state-out)."""
    r0 = L.pooled_rates(df, y0, dims)
    sts = sorted(df["st"].unique())
    agg = {}
    for st in sts:
        d = df[df["st"] == st]
        for dim in dims:
            for y in (y0, y1):
                agg[(st, dim, y)] = (L._sum_cols(d, y, "reg", dim, L.DIMS[dim]).sum(), L._sum_cols(d, y, "voted", dim, L.DIMS[dim]).sum())
        for y in (y0, y1):
            agg[(st, "all", y)] = (d[f"{y}.reg.all"].sum(), d[f"{y}.voted.all"].sum())
    out = {}
    for st in sts:
        oth = [s for s in sts if s != st]
        for dim in dims:
            tot = {y: (sum(agg[(s, dim, y)][0] for s in oth), sum(agg[(s, dim, y)][1] for s in oth)) for y in (y0, y1)}
            out[(st, dim)] = {g: r0[(st, dim)][g] * (tot[y1][1][g] / tot[y1][0][g]) / (tot[y0][1][g] / tot[y0][0][g]) if tot[y0][0][g] > 0 and tot[y1][0][g] > 0 else r0[(st, dim)][g]
                              for g in r0[(st, dim)]}
        a = {y: sum(agg[(s, "all", y)][1] for s in oth) / sum(agg[(s, "all", y)][0] for s in oth) for y in (y0, y1)}
        out[(st, "all")] = r0[(st, "all")] * a[y1] / a[y0]
    return out


def feature_loco(C: pd.DataFrame, fr: dict) -> pd.DataFrame:
    """Does the L2 ethnic composition of the roll (share of registrants: Hispanic, African-American, Asian; b20, 2021-08; a slow-moving
    covariate that is NOT a pre-2022 observation for 2016 / 2018) improve the challenger's turnout-share regression? Leave-one-cycle-out
    over 2016-2024, challenger features x_o, x_h, pop_chg4 (unpenalised) plus the composition terms."""
    D = C[C["st"] != "ct"].copy()
    b20 = fr["b20"]
    reg = b20["2020.reg.all"].replace(0, np.nan)
    for e in ("hisp", "aa", "esa"):
        D["eth_" + e] = D["fips"].map(b20[f"2020.reg.eth.{e}"] / reg)

    def center(col):
        x = D[col].replace([np.inf, -np.inf], np.nan)
        ok = x.notna()
        w = D["w0"].where(ok, 0.0)
        return (x - (x.fillna(0) * w).groupby(D["race"]).transform("sum") / w.groupby(D["race"]).transform("sum")).fillna(0.0)

    for e in ("hisp", "aa", "esa"):
        D[f"e_{e}_c"] = center("eth_" + e)
    base = ["x_o", "x_h", "pop_chg4"]
    sets = {"challenger (x_o, x_h, pop_chg4)": base, "+ L2 ethnic composition (hisp, aa, esa)": base + ["e_hisp", "e_aa", "e_esa"]}
    rows = []
    cyc = bt.TEST
    for nm, feats in sets.items():
        Xc = [f + "_c" for f in feats]
        for c in cyc:
            tr = D[(D["t"] != c) & D["t"].isin(cyc) & D["v2"].notna() & D["l_lp"].notna() & (D["v2"] > 0)]
            te = D[D["t"] == c]
            sh = tr["v2"] / tr["v2"].groupby(tr["race"]).transform("sum")
            b0, b = uu.wridge(tr[Xc].to_numpy(), (np.log(sh) - tr["l_lp"]).to_numpy(), np.sqrt(tr["v2"].to_numpy()), 0.0, np.zeros(len(Xc)))
            p = pd.Series(np.exp(te["l_lp"].to_numpy() + te[Xc].to_numpy() @ b), index=te.index)
            for race, g in te.groupby("race"):
                rows.append({"model": nm, "t": c, "race": race, "st": g["st"].iloc[0], "tv": tv(p.loc[g.index].to_numpy(), g["v2"].to_numpy())})
    r = pd.DataFrame(rows)
    t = r.groupby(["model", "t"])["tv"].mean().unstack()
    t["all"] = r.groupby("model")["tv"].mean()
    ch = r[r["model"] == list(sets)[0]].set_index(["t", "race"])["tv"]
    nw = r[r["model"] == list(sets)[1]].set_index(["t", "race"])["tv"]
    d = pd.DataFrame({"tv_a": nw, "tv_b": ch}).reset_index()
    d["st"] = d["race"].str.split("_").str[1]
    m, lo, hi = boot_diff(d, "a", "b")
    t.loc["difference (composition - challenger), 90 % interval", "all"] = m
    t.loc["  lo", "all"] = lo
    t.loc["  hi", "all"] = hi
    return t


# ------------------------------------------------------------------ House county x district pieces


def piece_frame(lines: str = "cd119") -> pd.DataFrame:
    """Pieces (county x district on the lines of the 2024 election `cd119`, or of 2026 `cd120`) with the L2 counts of the k22 / k24 block
    files (canonical `<year>.<kind>...` names), the 2020 / 2024 presidential two-party votes `p20` / `p24` and `st`, `cd`."""
    P = L.build_pieces()
    num = [c for c in P.columns if c not in ("st", "county", "cd119", "cd120")]
    g = P.groupby(["st", "county", lines])[num].sum().reset_index().rename(columns={lines: "cd"})
    g = g.rename(columns={c: c.split("|", 1)[1] for c in g.columns if c.startswith(("k22|", "k24|"))})
    g = g.drop(columns=[c for c in g.columns if c.startswith("b20|")])
    g["p20"], g["p24"] = g["p20_d"] + g["p20_r"], g["p24_d"] + g["p24_r"]
    g["district"] = g["st"] + "-" + g["cd"]
    return g


def trended_roll(G: pd.DataFrame, phi: float) -> pd.DataFrame:
    """Stale 2022 roll extrapolated two more years along the piece's own 2020 -> 2022 growth, group by group: reg22 * ((reg22 + 1) / (reg20 + 1))^phi,
    written as a 2024 roll so that `model_ballots(.., 2024, ..)` reads it."""
    out = G.copy()
    for c in [c for c in G.columns if c.startswith("2022.reg.")]:
        c20 = c.replace("2022", "2020")
        out[c.replace("2022", "2024")] = G[c] * ((G[c] + 1) / (G[c20] + 1)) ** phi
    return out


def house_section() -> dict:
    G = piece_frame("cd119")
    G = G[~G["st"].isin(["CA", "LA"]) & (G["p24"] > 0) & (G["p20"] > 0)].reset_index(drop=True)
    dims = ("eth", "gen")
    rates = L.pooled_rates(G, 2020, dims)
    MB = L.model_ballots(G, 2020, rates, dims)["ballots"]
    G["M22"] = L.model_ballots(G, 2022, rates, dims)["ballots"]
    G["M24"] = L.model_ballots(G, 2024, rates, dims)["ballots"]
    G["MB"] = MB
    for phi in (0.5, 1.0):
        G[f"Mtr{phi}"] = L.model_ballots(trended_roll(G, phi), 2024, rates, dims)["ballots"]
    pred = {"last presidential piece votes (the registered House method's split)": G["p20"]}
    for lam in (0.5, 1.0):
        pred[f"L2 roll 2022 (stale), lam={lam}"] = G["M22"] * (G["p20"] / G["MB"]) ** lam
        pred[f"L2 roll 2024 (fresh), lam={lam}"] = G["M24"] * (G["p20"] / G["MB"]) ** lam
        for phi in (0.5, 1.0):
            pred[f"L2 roll 2022 + trend phi={phi} (strict), lam={lam}"] = G[f"Mtr{phi}"] * (G["p20"] / G["MB"]) ** lam
    pred["L2 composition only (no official base), roll 2024"] = G["M24"]
    rows = []
    multi = G.groupby("district")["p24"].transform("size") >= 2
    for nm, v in pred.items():
        d = G.assign(v=v.to_numpy())[multi]
        for dist, g in d.groupby("district"):
            a = g["p24"].to_numpy()
            p = g["v"].to_numpy()
            e = np.abs(p / p.sum() * a.sum() / a - 1)
            rows.append({"model": nm, "district": dist, "st": g["st"].iloc[0], "pieces": len(g), "votes": a.sum(), "tv": tv(p, a),
                         "wmape": float(np.sum(np.abs(p / p.sum() * a.sum() - a)) / a.sum()), "med_ape": float(np.median(e))})
    r = pd.DataFrame(rows)
    base = list(pred)[0]
    out = r.groupby("model").apply(lambda g: pd.Series({"districts": len(g), "mean_tv": g["tv"].mean(), "vote_weighted_tv": np.average(g["tv"], weights=g["votes"]),
                                                       "median_piece_ape": g["med_ape"].mean()}), include_groups=False).reindex(list(pred))
    b = r[r["model"] == base].set_index("district")
    ci = []
    for nm in pred:
        x = r[r["model"] == nm].set_index("district")
        d = pd.DataFrame({"st": x["st"], "tv_a": x["tv"], "tv_b": b["tv"].reindex(x.index)})
        m, lo, hi = boot_diff(d, "a", "b")
        ci.append({"model": nm, "mean_tv_minus_last_pres": m, "ci90_lo": lo, "ci90_hi": hi, "relative": m / b["tv"].mean()})
    out = out.join(pd.DataFrame(ci).set_index("model"))
    # also the pieces that are whole counties vs split ones, as a plain size diagnostic
    return {"house_pieces": out, "_house_districts": r, "house_info": pd.DataFrame({"pieces": [len(G)], "districts_multi": [int(G[multi]["district"].nunique())],
                                                                                 "states": [G["st"].nunique()], "excluded": ["CA, LA (no 2024 block results)"]})}


# ------------------------------------------------------------------ redrawn states: splitting a 2024 piece over the 2026 lines
#
# Bar for this component (written after an exploratory look at the pop-vs-L2 split error, which is disclosed in docs/us_l2turnout.md):
#   split of votes (B2): key = the L2 2020 votes of the 2023-02 file (known before the 2024 election) must cut the misallocated share
#     of 2024 votes by >= 50 % against the registered key (block population), better in >= 5 of the 7 validation states, and the
#     county-clustered bootstrap 90 % interval of the difference must exclude 0;
#   Democratic-share shifts (B3): leave-one-state-out RMSE of the sub-piece share deviation (vs the registered "no deviation") cut by
#     >= 25 % pooled and in >= 5 of the 7 states.

VAL_STATES = ("AL", "FL", "NC", "OH", "TN", "TX", "UT")
KEYS = {"block population (registered method)": "popc", "block count": "blocks", "L2 roll 2020 (2023-02 file)": "2020.reg.all",
        "L2 roll 2022 (2023-02 file)": "2022.reg.all", "L2 votes 2020 (2023-02 file)": "2020.voted.all",
        "L2 votes 2022 (2023-02 file)": "2022.voted.all", "L2 roll 2024 (Dec-2024 file)": "2024.reg.all", "L2 votes 2024 (Dec-2024 file)": "2024.voted.all"}
BEFORE_2024 = {"block population (registered method)", "block count", "L2 roll 2020 (2023-02 file)", "L2 roll 2022 (2023-02 file)",
               "L2 votes 2020 (2023-02 file)", "L2 votes 2022 (2023-02 file)"}


def split_errors(S: pd.DataFrame, shares: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for nm, sh in shares.items():
        pred = sh * S.groupby(L.GROUP)["v"].transform("sum")
        e = (pred - S["v"]).abs()
        g = pd.DataFrame({"st": S["st"], "county": S["county"], "cd119": S["cd119"], "err": e, "v": S["v"]}).groupby(L.GROUP).sum().reset_index()
        rows.append(g.assign(key=nm))
    return pd.concat(rows, ignore_index=True)


def split_section() -> dict:
    Q = L.sub_pieces()
    S = Q[Q["st"].isin(VAL_STATES) & (Q["n_sub"] >= 2)].copy()
    S = S[S.groupby(L.GROUP)["v"].transform("sum") > 0].reset_index(drop=True)
    res: dict = {}
    shares = {nm: L.split_shares(S, col) for nm, col in KEYS.items()}
    # the two most recent elections' vote shares averaged: the analogue (2020 + 2022 -> 2024) of the production key (2022 + 2024 -> 2026)
    shares["mean of L2 vote shares 2020 and 2022 (2023-02 file)"] = (shares["L2 votes 2020 (2023-02 file)"] + shares["L2 votes 2022 (2023-02 file)"]) / 2
    keys = list(shares)
    E = split_errors(S, shares)
    base = E[E["key"] == "block population (registered method)"].set_index(L.GROUP)
    out, per_state = [], []
    rng = np.random.default_rng(3)
    for nm in keys:
        x = E[E["key"] == nm].set_index(L.GROUP)
        tot = x["v"].sum()
        misal = x["err"].sum() / tot
        d = (x["err"] - base["err"]).groupby(level=[0, 1]).agg(["sum", "count"])   # cluster = county
        idx = rng.integers(0, len(d), size=(NBOOT, len(d)))
        rel = d["sum"].to_numpy()[idx].sum(axis=1) / base["err"].groupby(level=[0, 1]).sum().to_numpy()[idx].sum(axis=1)
        out.append({"key": nm, "known before 2024": nm in BEFORE_2024 or nm.startswith("mean of"), "misallocated share of votes (|err| / votes)": misal,
                    "relative to population": misal / (base["err"].sum() / tot), "rel ci90_lo": float(np.quantile(rel, 0.05)), "rel ci90_hi": float(np.quantile(rel, 0.95))})
        ps = x.groupby(level="st").apply(lambda g: g["err"].sum() / g["v"].sum(), include_groups=False)
        per_state.append(ps.rename(nm))
    res["split_error"] = pd.DataFrame(out).set_index("key")
    ps = pd.concat(per_state, axis=1).T
    ps["better_than_population"] = np.nan
    res["split_error_by_state"] = ps.drop(columns="better_than_population")
    res["split_info"] = pd.DataFrame({"sub-pieces": [len(S)], "(county, cd119) groups": [S.groupby(L.GROUP).ngroups], "2024 votes in split groups": [S["v"].sum()],
                                      "share of the 7 states' votes": [S["v"].sum() / Q[Q["st"].isin(VAL_STATES)]["v"].sum()]})
    # B3: Democratic share deviation of a sub-piece from its parent piece
    par = S.groupby(L.GROUP)[["d", "v"]].transform("sum")
    S["y"] = (S["d"] / S["v"].where(S["v"] > 0)).fillna(par["d"] / par["v"]) - par["d"] / par["v"]
    sh_rows = []
    for nm, snap, wkey in (("2023-02 roll (party registration, ethnicity), strict in time", True, "2022.voted.all"),
                           ("Dec-2024 roll (party registration, ethnicity)", False, "2024.voted.all")):
        X = L.shift_features(S, wkey, snap)
        pred = np.zeros(len(S))
        for st in VAL_STATES:
            te = (S["st"] == st).to_numpy()
            if te.sum() == 0 or (~te).sum() == 0:
                continue
            b = L.fit_shift(X[~te], S.loc[~te, "y"].to_numpy(), S.loc[~te, "v"].to_numpy())
            pred[te] = X[te].to_numpy() @ b
        w = S["v"].to_numpy()
        sh_rows.append({"model": nm, "scope": "pooled", "rmse_no_shift": float(np.sqrt(np.average(S["y"] ** 2, weights=w))),
                        "rmse_L2_loso": float(np.sqrt(np.average((S["y"] - pred) ** 2, weights=w))), "states": len(VAL_STATES)})
        for st in VAL_STATES:
            te = (S["st"] == st).to_numpy()
            if te.sum():
                sh_rows.append({"model": nm, "scope": st, "rmse_no_shift": float(np.sqrt(np.average(S.loc[te, "y"] ** 2, weights=w[te]))),
                                "rmse_L2_loso": float(np.sqrt(np.average((S.loc[te, "y"] - pred[te]) ** 2, weights=w[te]))), "states": 1})
        b_all = L.fit_shift(X, S["y"].to_numpy(), S["v"].to_numpy())
        sh_rows.append({"model": nm, "scope": "coefficients (dr, aa, hisp, esa), all 7 states", "rmse_no_shift": np.nan, "rmse_L2_loso": np.nan, "states": len(VAL_STATES),
                        **dict(zip(L.SHIFT_FEATS, b_all))})
    res["share_shift"] = pd.DataFrame(sh_rows).set_index(["model", "scope"])
    return res


def district_check() -> dict:
    """2026 districts of FL NC OH TX (the redrawn states with a night feed): the 2024 presidential two-party Democratic share and votes of
    each district as the registered pieces give them (population split of a county piece over the 2026 lines), after the L2 vote split
    and after the split + share shift (coefficients fitted on the OTHER validation states), against the RDH block results aggregated
    onto the 2026 lines. The truth is itself a block disaggregation of precinct results (population-like inside a precinct), so its own
    error floor is not zero (NC, whose registered split is exact, differs from it by 0.9 points)."""
    import us_house_night as hn
    Q = L.sub_pieces()
    sts = ["FL", "NC", "OH", "TX"]
    old = hn.county_pieces([x.lower() for x in sts])
    S = Q[Q["st"].isin(VAL_STATES) & (Q["n_sub"] >= 2)].copy()
    S = S[S.groupby(L.GROUP)["v"].transform("sum") > 0]
    par = S.groupby(L.GROUP)[["d", "v"]].transform("sum")
    y = ((S["d"] / S["v"].where(S["v"] > 0)).fillna(par["d"] / par["v"]) - par["d"] / par["v"]).to_numpy()
    X = L.shift_features(S, "2024.voted.all", snap=False)
    tr = Q[Q["st"].isin(sts)].assign(cd26=lambda d: d["cd120"].astype(int)).groupby(["st", "cd26"])[["d", "r"]].sum()
    truth = pd.DataFrame({"d2_truth": tr["d"] / (tr["d"] + tr["r"]), "v2_truth": tr["d"] + tr["r"]})

    def dist(pc):
        g = pc.groupby(["st", "cd26"])[["d24", "r24"]].sum()
        return pd.DataFrame({"d2": g["d24"] / (g["d24"] + g["r24"]), "v2": g["d24"] + g["r24"]})
    loso = []
    for st in sts:
        m = (S["st"] != st).to_numpy()
        b = L.fit_shift(X[m], y[m], S["v"].to_numpy()[m])
        loso.append(dist(L.resplit_pieces(old[old["st"] == st], Q, b)))
    o, a, ls = dist(old), dist(L.resplit_pieces(old, Q, None)), pd.concat(loso)
    r = truth.join(o.add_suffix("_registered")).join(a.add_suffix("_L2split")).join(ls.add_suffix("_L2split_shift_loso"))
    for k in ("registered", "L2split", "L2split_shift_loso"):
        r["err_d2_" + k] = 100 * (r["d2_" + k] - r["d2_truth"])
        r["err_v2_" + k] = 100 * (r["v2_" + k] / r["v2_truth"] - 1)
    e = r[[c for c in r.columns if c.startswith("err_d2_")]]
    v = r[[c for c in r.columns if c.startswith("err_v2_")]]
    tab = pd.DataFrame({"D2 rmse (pts)": np.sqrt((e**2).mean()).rename(lambda x: x[7:]), "D2 mean |err| (pts)": e.abs().mean().rename(lambda x: x[7:]),
                        "votes mean |err| (%)": v.abs().mean().rename(lambda x: x[7:]), "votes max |err| (%)": v.abs().max().rename(lambda x: x[7:])})
    bys = e.groupby(level=0).apply(lambda x: np.sqrt((x**2).mean())).rename(columns=lambda x: x[7:])
    bys["districts"] = e.groupby(level=0).size()
    return {"district_check": tab, "district_check_by_state": bys, "_district_check_rows": r.reset_index()}


# ------------------------------------------------------------------ option House priors for the redrawn districts

PRI_IN = PUB / "us2026_unified_house_priors.csv"
PRI_OUT = PUB / "us2026_unified_house_priors_l2.csv"
PRI_OUT_V = PUB / "us2026_unified_house_priors_l2votes.csv"
PARAMS_OUT = PUB / "us_l2turnout_params.json"
HEAD = ("# Option: unified pre-count priors for the 2026 House districts with L2-informed county pieces (analysis/us_l2turnout.py priors).\n"
        "# Same schema, sources and district forecasts (mean, sd) as us2026_unified_house_priors.csv; only the redrawn-state districts\n"
        "# (FL NC OH TX of the configured 75) change: the 2024 presidential votes of a (county, 2024 district) piece are split over the\n"
        "# 2026 districts by the county piece's L2 votes (mean of the 2022 and 2024 shares) instead of block population{shift}.\n"
        "# This analysis was conducted using data from the Redistricting Data Hub. L2 ethnicity and party are modelled fields.\n"
        "# Sources as us2026_unified_house_priors.csv plus Redistricting Data Hub L2 turnout statistics (derived aggregates only).\n")


def piece_priors(old: pd.DataFrame, pieces: pd.DataFrame, mu: dict, gam: float, ratio: float, tag: str) -> pd.DataFrame:
    """The registered unified House prior (analysis/us_unified_house_2026.build_house) for districts given (county, district) piece votes
    `pieces` (st, county, cd26, d24, r24): V = ratio x piece votes, s0 = mu + gam (piece share - district share), re-centred."""
    rows = []
    pieces = pieces.groupby(["st", "cd26", "county"])[["d24", "r24"]].sum().reset_index()
    for (st, cd), g in pieces.groupby(["st", "cd26"]):
        did = f"{st.lower()}-{cd:02d}"
        src = f"uni_house_{did}_2026"
        if src not in mu:
            continue
        g = g[(g["d24"] + g["r24"]) > 0.05]
        v = g["d24"] + g["r24"]
        sp = g["d24"] / v
        sp_d = g["d24"].sum() / v.sum()
        s0 = np.clip(mu[src] + gam * (sp - sp_d), 0.02, 0.98)
        s0 = np.clip(s0 + mu[src] - np.average(s0, weights=v), 0.02, 0.98)
        V = v * ratio
        rows.append(pd.DataFrame({"source": src, "unit_id": "us-" + st.lower() + "-" + g["county"], "dem": (s0 * V).round(2), "rep": ((1 - s0) * V).round(2),
                                  "total": V.round(2)}))
    return pd.concat(rows, ignore_index=True)


def priors_section() -> dict:
    import json

    import us_house_night as hn
    cur = pd.read_csv(PRI_IN, comment="#")
    cur["did"] = cur["source"].str.extract(r"uni_house_(.+)_2026")[0]
    mu = (cur.groupby("source")["dem"].sum() / (cur.groupby("source")["dem"].sum() + cur.groupby("source")["rep"].sum())).to_dict()
    gam = float(json.loads((PUB / "us_unified_house_params.json").read_text())["gamma_piece"])
    ratio = float(json.loads((PUB / "us_unified_house_params.json").read_text())["house_to_pres_votes"])
    conf = sorted(cur["did"].unique())
    red = sorted({d.split("-")[0].upper() for d in conf} & set(L.REDRAWN))
    old = hn.county_pieces([x.lower() for x in red])
    # reproduce the registered file from the registered pieces (checks the reconstruction before anything is changed)
    rep = piece_priors(old, old, mu, gam, ratio, "old")
    chk = rep.merge(cur, on=["source", "unit_id"], suffixes=("", "_cur"))
    worst = float(max((chk["dem"] - chk["dem_cur"]).abs().max(), (chk["rep"] - chk["rep_cur"]).abs().max()))
    n_units = int((cur["source"].str[10:12].str.upper().isin(red)).sum())
    assert len(chk) == n_units and worst < 0.5, (len(chk), n_units, worst)
    # coefficients of the share shift: all 7 validation states, Dec-2024 roll (the best roll known for 2026)
    Q = L.sub_pieces()
    S = Q[Q["st"].isin(VAL_STATES) & (Q["n_sub"] >= 2)].copy()
    S = S[S.groupby(L.GROUP)["v"].transform("sum") > 0]
    par = S.groupby(L.GROUP)[["d", "v"]].transform("sum")
    y = ((S["d"] / S["v"].where(S["v"] > 0)).fillna(par["d"] / par["v"]) - par["d"] / par["v"]).to_numpy()
    beta = L.fit_shift(L.shift_features(S, "2024.voted.all", snap=False), y, S["v"].to_numpy())
    new_v = L.resplit_pieces(old, Q, None)
    new_vs = L.resplit_pieces(old, Q, beta)
    out_v = piece_priors(old, new_v, mu, gam, ratio, "L2 votes")
    out_vs = piece_priors(old, new_vs, mu, gam, ratio, "L2 votes + share")
    keep = cur[~cur["source"].isin(set(out_vs["source"]))][["source", "unit_id", "dem", "rep", "total"]]
    for df_, path, shift in ((out_vs, PRI_OUT, " and the pieces' Democratic shares move by the L2 party / ethnicity shift"), (out_v, PRI_OUT_V, "")):
        full = pd.concat([keep, df_], ignore_index=True).sort_values(["source", "unit_id"]).reset_index(drop=True)
        path.write_text(HEAD.format(shift=shift) + full.to_csv(index=False))
    PARAMS_OUT.write_text(json.dumps({"share_shift_beta": dict(zip(L.SHIFT_FEATS, map(float, beta))), "split_key": "mean of the 2022 and 2024 L2 vote shares of the piece",
                                      "fitted_on": "2024 presidential results by block (RDH) in AL FL NC OH TN TX UT; coefficients on the Dec-2024 roll: Democratic minus "
                                                   "Republican registration share, African-American, Hispanic, East-Asian share (all centred on the registered piece, weights = piece votes)",
                                      "credit": L.CREDIT, "districts_changed": sorted(out_vs["source"].unique().tolist())}, indent=1, sort_keys=True))
    # district-level effect of the re-split (aggregate derived statistics)
    def dist(pc):
        g = pc.groupby(["st", "cd26"])[["d24", "r24"]].sum()
        g["d2"] = g["d24"] / (g["d24"] + g["r24"])
        g["v2"] = g["d24"] + g["r24"]
        return g
    o, a, b = dist(old), dist(new_v), dist(new_vs)
    eff = pd.DataFrame({"d2_registered": o["d2"], "d2_L2split": a["d2"], "d2_L2split_shift": b["d2"], "v2_registered": o["v2"], "v2_L2split": a["v2"]})
    eff["dd2_split_pts"] = 100 * (eff["d2_L2split"] - eff["d2_registered"])
    eff["dd2_split_shift_pts"] = 100 * (eff["d2_L2split_shift"] - eff["d2_registered"])
    eff["dv2_pct"] = 100 * (eff["v2_L2split"] / eff["v2_registered"] - 1)
    eff = eff.reset_index()
    eff["district"] = eff["st"] + "-" + eff["cd26"].map("{:02d}".format)
    eff = eff[eff["district"].str.lower().isin(conf)]
    sm = pd.DataFrame({"districts": [len(eff)], "mean |dD2| split only (pts)": [eff["dd2_split_pts"].abs().mean()], "max |dD2| split only (pts)": [eff["dd2_split_pts"].abs().max()],
                       "mean |dD2| split + shift (pts)": [eff["dd2_split_shift_pts"].abs().mean()], "max |dD2| split + shift (pts)": [eff["dd2_split_shift_pts"].abs().max()],
                       "mean |dV| (%)": [eff["dv2_pct"].abs().mean()], "max |dV| (%)": [eff["dv2_pct"].abs().max()],
                       "districts with |dD2| >= 0.5 pt": [int((eff["dd2_split_shift_pts"].abs() >= 0.5).sum())],
                       "reproduction of the registered file, max |diff| votes": [worst],
                       "pieces re-split": [int((new_vs["how"] == "L2").sum())], "pieces kept (population)": [int((new_vs["how"] == "pop").sum())]})
    # implied move of the district forecasts if the district presidential share is taken from the L2 pieces (informational; the option
    # priors keep the registered district means): F is linear in Pc = pres24 share - const, so d mu = w * b_Pc * d D2 with w the weight
    # on the fundamentals (1 without polls); the redrawn districts' extra sd of the registered model is kept.
    from scipy.stats import norm
    hpar = json.loads((PUB / "us_unified_house_params.json").read_text())["house"]
    hd = pd.read_csv(uu.U / "house_districts_2026.csv").set_index("did")
    eff["n_polls"] = eff["district"].map(hd["poll_n"]).fillna(0).astype(int)
    w = uu._blend_w(eff["n_polls"].to_numpy(), hpar["k"], hpar["p"])
    eff["w_F"] = w
    eff["dmu_pts"] = w * hpar["b"]["Pc"] * eff["dd2_split_shift_pts"]
    eff["mu_registered"] = eff["district"].str.lower().map(lambda d: mu[f"uni_house_{d}_2026"])
    eff["mu_implied"] = eff["mu_registered"] + eff["dmu_pts"] / 100
    sd = np.sqrt(np.maximum(np.where(eff["n_polls"] > 0, hpar["sd"]["1"], hpar["sd"]["0"]), 0.015) ** 2 + 0.02**2)
    eff["p_win_registered"] = norm.cdf((eff["mu_registered"] - 0.5) / sd)
    eff["p_win_implied"] = norm.cdf((eff["mu_implied"] - 0.5) / sd)
    sm["mean |d mu| (pts)"] = float(eff["dmu_pts"].abs().mean())
    sm["max |d mu| (pts)"] = float(eff["dmu_pts"].abs().max())
    sm["max |d P(D)|"] = float((eff["p_win_implied"] - eff["p_win_registered"]).abs().max())
    sm["sum d P(D) over districts"] = float((eff["p_win_implied"] - eff["p_win_registered"]).sum())
    eff[["district", "d2_registered", "d2_L2split_shift", "dd2_split_shift_pts", "dv2_pct", "n_polls", "w_F", "dmu_pts", "mu_registered", "mu_implied", "p_win_registered",
         "p_win_implied"]].round(4).to_csv(PUB / "us2026_l2turnout_house_mu_shift.csv", index=False)
    return {"priors_effect_summary": sm, "_priors_effect_districts": eff[["district", "d2_registered", "d2_L2split", "d2_L2split_shift", "dd2_split_pts", "dd2_split_shift_pts",
                                                                          "v2_registered", "v2_L2split", "dv2_pct"]]}


# ------------------------------------------------------------------ 2026: how different would the L2 county shares be?


def sens_section() -> dict:
    """Production form of the (not adopted) county component for 2026: expected ballots of county c = its 2022 L2 ballots x
    M(roll 2024) / M(roll 2022) under the state's 2022 group rates (ethnicity + gender), against the challenger's expected votes of
    data_public/us2026_unified_priors.csv. Only the total-variation distance per race is reported."""
    fr = L.build_county()
    c22, c24 = fr["c22"], fr["c24"]
    dims = ("eth", "gen")
    rates = L.pooled_rates(c22, 2022, dims)
    M22 = L.model_ballots(c22, 2022, rates, dims)["ballots"]
    M24 = L.model_ballots(c24, 2024, rates, dims)["ballots"]
    l2 = c22["2022.voted.all"] * (M24.reindex(c22.index) / M22)
    pri = pd.read_csv(PUB / "us2026_unified_priors.csv", comment="#")
    pri["fips"] = pri["unit_id"].str[-5:]
    pri["V"] = pri["dem"] + pri["rep"]
    rows = []
    for src, g in pri.groupby("source"):
        x = g.assign(l2=g["fips"].map(l2)).dropna(subset=["l2"])
        if len(x) < 0.95 * len(g) or src.split("_")[1] == "ct":
            continue
        ratio = (x["l2"] / x["l2"].sum()) / (x["V"] / x["V"].sum())
        rows.append({"race": src, "counties": len(x), "tv_L2_vs_challenger": tv(x["l2"].to_numpy(), x["V"].to_numpy()),
                     "share_of_votes_in_counties_L2_raises_by_>10%": float((x["V"] / x["V"].sum())[ratio > 1.1].sum()),
                     "share_of_votes_in_counties_L2_cuts_by_>10%": float((x["V"] / x["V"].sum())[ratio < 0.9].sum())})
    t = pd.DataFrame(rows).set_index("race")
    return {"sens_2026": t, "sens_2026_summary": t.describe().loc[["count", "mean", "min", "max"]]}


# ------------------------------------------------------------------ main


def export_option(argv: list[str]) -> None:
    """Replay / export the unified House configs with the L2 option priors (the registered `us_unified_replay.export`, only its House
    priors file differs). `--votes-only` uses the file where only the piece votes move (shares unchanged)."""
    import argparse

    import us_unified_replay as r
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--until")
    ap.add_argument("--votes-only", action="store_true")
    a = ap.parse_args(argv)
    r.HOUSE_PRIORS = PRI_OUT_V if a.votes_only else PRI_OUT
    print(r.export(a.snap, a.out, r.ROOT / "configs" / "us2026_unified", True, a.until))


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "county"
    pd.set_option("display.width", 220)
    if what == "build":
        fr = L.build_county(force=True)
        print({k: v.shape for k, v in fr.items()})
        p = L.build_pieces(force=True)
        print("pieces", p.shape)
    elif what == "county":
        res = county_section()
        for k, v in res.items():
            if k.startswith("_"):
                v.to_csv(OUT / f"county{k}.csv", index=False)
                continue
            v.to_csv(OUT / f"county_{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    elif what == "split":
        res = split_section()
        for k, v in res.items():
            v.to_csv(OUT / f"{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    elif what == "export":
        export_option(sys.argv[2:])
    elif what == "sens":
        res = sens_section()
        for k, v in res.items():
            v.to_csv(OUT / f"{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    elif what == "dcheck":
        res = district_check()
        for k, v in res.items():
            if k.startswith("_"):
                v.to_csv(OUT / f"{k[1:]}.csv", index=False)
                continue
            v.to_csv(OUT / f"{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    elif what == "priors":
        res = priors_section()
        for k, v in res.items():
            if k.startswith("_"):
                v.to_csv(OUT / f"{k[1:]}.csv", index=False)
                continue
            v.to_csv(OUT / f"{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    elif what == "house":
        res = house_section()
        for k, v in res.items():
            if k.startswith("_"):
                v.to_csv(OUT / f"{k[1:]}.csv", index=False)
                continue
            v.to_csv(OUT / f"{k}.csv")
            print(f"\n== {k}\n{md(v)}")
    else:
        raise SystemExit(f"unknown section {what}")


if __name__ == "__main__":
    main()
