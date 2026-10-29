"""US midterms 2026: analysis of model choices on past data only (no model or config changes).

  uv run python analysis/us_model_analysis.py        # writes results/us_model_analysis/*.csv and prints the tables

Data (all before 2026): MIT Election Data and Science Lab precinct returns 2018, 2022, 2024 (github.com/MEDSL,
CC0 1.0; 2016 and 2020 county files need a Dataverse guestbook and are not used), Georgia SoS official 2020/2024
county results (public record), FiveThirtyEight raw_polls 2016-2022 (CC BY 4.0), US Census ACS 2020-2024 5-year,
PEP vintage 2025 and the 2023 county gazetteer (public domain). Sections follow docs/us_model_analysis.md.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_hub  # noqa: E402
import us_polls  # noqa: E402

OUT = us.ROOT_DIR / "results" / "us_model_analysis"
RAW = us.US_RAW
STATES = [s for s in us.MEDSL24_STATES if s not in ("ak", "ct")]   # AK districts; CT changed county units in 2022


# ------------------------------------------------------------------ data

def medsl_county(year: int, st: str, office: str) -> pd.DataFrame:
    """unit_id, dem, rep, total for a statewide office (GOVERNOR / US SENATE / US PRESIDENT) from MEDSL precinct
    files. Party by candidate: all of a candidate's votes count for the major party under which most of them were
    cast (fusion lines). Regular elections only."""
    cache = us.US_PROC / "medsl_hist" / f"{year}_{st}_{office.replace(' ', '_')}.csv"
    if cache.exists():
        return pd.read_csv(cache)
    if year == 2024:
        z = zipfile.ZipFile(RAW / "medsl24" / f"{st}24.zip")
    elif year == 2022:
        z = zipfile.ZipFile(RAW / "medsl22" / f"2022-{st}-local-precinct-general.zip")
    else:
        z = zipfile.ZipFile(RAW / "medsl18" / f"2018-{st}-precinct-general.zip")
    cols = ["office", "stage", "mode", "votes", "county_fips", "party_simplified", "candidate", "precinct", "special"]
    parts = []
    for ch in pd.read_csv(z.open([n for n in z.namelist() if n.endswith(".csv")][0]), dtype=str, chunksize=500_000,
                          usecols=lambda c: c in cols):
        parts.append(ch[(ch["office"] == office) & (ch["stage"].fillna("GEN") == "GEN")
                        & (ch.get("special", pd.Series("FALSE", index=ch.index)).fillna("FALSE").str.upper() != "TRUE")])
    df = pd.concat(parts, ignore_index=True)
    if df.empty:
        res = pd.DataFrame(columns=["unit_id", "dem", "rep", "total"])
    else:
        v = pd.to_numeric(df["votes"], errors="coerce").fillna(0)
        major = df.assign(v=v)[df["party_simplified"].isin(["DEMOCRAT", "REPUBLICAN"])]
        main = major.groupby(["candidate", "party_simplified"])["v"].sum().reset_index()
        main = main.sort_values("v").drop_duplicates("candidate", keep="last").set_index("candidate")["party_simplified"]
        df["party_simplified"] = df["candidate"].map(main).astype(object).fillna("OTHER")
        res = us._medsl_county(df, st, "overlap")
    cache.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(cache, index=False)
    return res


def county_table() -> pd.DataFrame:
    """One row per county: two-party D share and total votes for gov18, gov22, top22 (Senate if any, else governor),
    pres24; and the covariates."""
    rows = []
    for st in STATES:
        recs = {}
        for key, year, office in (("gov18", 2018, "GOVERNOR"), ("sen18", 2018, "US SENATE"), ("gov22", 2022, "GOVERNOR"),
                                  ("sen22", 2022, "US SENATE")):
            try:
                recs[key] = medsl_county(year, st, office).set_index("unit_id")
            except Exception as e:                   # some states had no such race
                print(f"{st} {key}: {type(e).__name__}: {e}")
        p24 = us.medsl24_pres(st).set_index("unit_id")
        d = pd.DataFrame(index=p24.index)
        d["state"] = st
        for key, r in [*recs.items(), ("pres24", p24)]:
            if len(r) and (r["dem"] + r["rep"]).sum() > 0:
                r = r.reindex(d.index)
                d[f"s_{key}"] = r["dem"] / (r["dem"] + r["rep"])
                d[f"v_{key}"] = r["total"]
        rows.append(d)
    t = pd.concat(rows)
    t["s_top22"] = t["s_sen22"].where(t["s_sen22"].notna(), t.get("s_gov22"))
    t["v_top22"] = t["v_sen22"].where(t["v_sen22"].notna(), t.get("v_gov22"))
    return t.join(covariates())


def covariates() -> pd.DataFrame:
    """ACS 2020-2024 shares (Bachelor's+, Hispanic, Black alone, log median income), PEP 2025 65+, log density."""
    acs = us_hub._acs
    edu = acs("b15003", ["B15003_E001", "B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"])
    his = acs("b03003", ["B03003_E001", "B03003_E003"])
    blk = acs("b02001", ["B02001_E001", "B02001_E003"])
    inc = acs("b19013", ["B19013_E001"])
    age = pd.read_csv(RAW / "census" / "cc-est2025-agesex-all.csv", dtype=str, encoding="latin1")
    age = age[age["YEAR"] == str(age["YEAR"].astype(int).max())].assign(f=lambda x: x["STATE"] + x["COUNTY"])
    age = age.set_index("f")[["AGE65PLUS_TOT", "POPESTIMATE"]].astype(float)
    gaz = pd.read_csv(RAW / "census" / "2023_Gaz_counties_national.txt", sep="\t", dtype={"GEOID": str})
    gaz.columns = [c.strip() for c in gaz.columns]
    land = gaz.set_index("GEOID")["ALAND_SQMI"]
    c = pd.DataFrame({
        "ba": edu[["B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"]].sum(axis=1) / edu["B15003_E001"],
        "hisp": his["B03003_E003"] / his["B03003_E001"], "black": blk["B02001_E003"] / blk["B02001_E001"],
        "lninc": np.log(inc["B19013_E001"].where(inc["B19013_E001"] > 0))})
    c["age65"] = (age["AGE65PLUS_TOT"] / age["POPESTIMATE"]).reindex(c.index)
    c["lndens"] = np.log(age["POPESTIMATE"].reindex(c.index) / land.reindex(c.index).clip(lower=1))
    c.index = [f"us-{u}" for u in c.index]                   # placeholder, fixed below
    st = {uid.split("-")[2][:2]: uid.split("-")[1] for uid, _ in us._geo()}
    c.index = [f"us-{st.get(i[3:5], 'xx')}-{i[3:]}" for i in c.index]
    return c


FEATS = ["ba", "age65", "hisp", "black", "lninc", "lndens"]


# ------------------------------------------------------------------ 1. swing rules

def _shift(prev: np.ndarray, w: np.ndarray, target: float, rule: str) -> np.ndarray:
    if rule == "uniform":
        return np.clip(prev + target - np.average(prev, weights=w), 0.001, 0.999)
    lp = logit(np.clip(prev, 0.001, 0.999))
    if rule == "logit":
        d = brentq(lambda x: np.average(expit(lp + x), weights=w) - target, -15, 15)
        return expit(lp + d)
    return 0.5 * (_shift(prev, w, target, "uniform") + _shift(prev, w, target, "logit"))


def contested(d: pd.DataFrame, sa: str, va: str, sb: str, vb: str) -> pd.DataFrame:
    """States whose two races were both contested by both parties (statewide two-party share 15-85 %)."""
    ok = []
    for st, g in d.groupby("state"):
        a, b = np.average(g[sa], weights=g[va]), np.average(g[sb], weights=g[vb])
        if 0.15 < a < 0.85 and 0.15 < b < 0.85:
            ok.append(st)
    return d[d["state"].isin(ok)]


def swing_rules(t: pd.DataFrame) -> pd.DataFrame:
    tests = [("gov18->gov22", "s_gov18", "v_gov18", "s_gov22", "v_gov22"),
             ("top22->pres24", "s_top22", "v_top22", "s_pres24", "v_pres24")]
    rows = []
    pri = us.load_priors()
    ga = {}
    for y in (2020, 2024):
        g = pri[pri["source"] == f"ga_pres_{y}"].set_index("unit_id")
        ga[y] = (g["dem"] / (g["dem"] + g["rep"]), g["dem"] + g["rep"])
    extra = pd.DataFrame({"state": "ga", "s_a": ga[2020][0], "v_a": ga[2020][1], "s_b": ga[2024][0],
                          "v_b": ga[2024][1]}).dropna()
    for name, sa, va, sb, vb in tests + [("GA pres20->pres24", None, None, None, None)]:
        d = extra if sa is None else t[["state", sa, va, sb, vb]].dropna().set_axis(
            ["state", "s_a", "v_a", "s_b", "v_b"], axis=1)
        d = contested(d[(d["v_a"] > 0) & (d["v_b"] > 0)], "s_a", "v_a", "s_b", "v_b")
        for rule in ("uniform", "logit", "hybrid"):
            pred = []
            for st, g in d.groupby("state"):
                target = float(np.average(g["s_b"], weights=g["v_b"]))      # the statewide result is given
                pred.append(pd.Series(_shift(g["s_a"].to_numpy(), g["v_a"].to_numpy(), target, rule), index=g.index))
            p = pd.concat(pred).reindex(d.index)
            err = 100 * (p - d["s_b"])
            dec = pd.qcut(d["s_a"], 10, labels=False, duplicates="drop")
            row = {"test": name, "rule": rule, "counties": len(d), "states": d["state"].nunique(),
                   "rmse_pts": np.sqrt((err**2).mean()), "rmse_w_pts": np.sqrt(np.average(err**2, weights=d["v_b"]))}
            for k, b in err.groupby(dec).mean().items():
                row[f"bias_d{int(k) + 1}"] = b
            rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 2. demographics

def _ols(X: np.ndarray, y: np.ndarray, w: np.ndarray) -> np.ndarray:
    Xc = np.column_stack([np.ones(len(X)), X])
    W = np.sqrt(w)
    return np.linalg.lstsq(Xc * W[:, None], y * W, rcond=None)[0]


def _r2(y, p, w) -> float:
    return 1 - np.average((y - p) ** 2, weights=w) / np.average((y - np.average(y, weights=w)) ** 2, weights=w)


def demographics(t: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """County swing beyond the statewide (logit-or-uniform) shift, regressed on standardised covariates."""
    cyc = {"gov18->gov22": ("s_gov18", "v_gov18", "s_gov22"), "top22->pres24": ("s_top22", "v_top22", "s_pres24")}
    data, rows, coefs = {}, [], []
    for name, (sa, va, sb) in cyc.items():
        d = t[["state", sa, va, sb] + FEATS].dropna()
        d = contested(d[d[va] > 0], sa, va, sb, va)
        resid = []
        for st, g in d.groupby("state"):
            base = _shift(g[sa].to_numpy(), g[va].to_numpy(),
                          float(np.average(g[sb], weights=g[va])), "uniform")
            resid.append(pd.Series(g[sb].to_numpy() - base, index=g.index))
        d["y"] = 100 * pd.concat(resid)
        data[name] = d
    mu = pd.concat(data.values())[FEATS].mean()
    sd = pd.concat(data.values())[FEATS].std()
    for name, d in data.items():
        X, y, w = ((d[FEATS] - mu) / sd).to_numpy(), d["y"].to_numpy(), d[sa if False else d.columns[2]].to_numpy()
        w = np.sqrt(w)                                                    # dampen huge counties
        pred = np.zeros(len(d))
        for st in d["state"].unique():
            m = (d["state"] == st).to_numpy()
            b = _ols(X[~m], y[~m], w[~m])
            pred[m] = np.column_stack([np.ones(m.sum()), X[m]]) @ b
        b_all = _ols(X, y, w)
        coefs.append({"fit": name, **{f: c for f, c in zip(["const"] + FEATS, b_all)}})
        rows.append({"test": f"{name}: leave-one-state-out", "r2": _r2(y, pred, w),
                     "rmse_uniform_pts": np.sqrt(np.average(y**2, weights=w)),
                     "rmse_with_demog_pts": np.sqrt(np.average((y - pred) ** 2, weights=w)), "counties": len(d)})
    names = list(data)
    for a, b in ((names[0], names[1]), (names[1], names[0])):
        da, db = data[a], data[b]
        Xa, Xb = ((da[FEATS] - mu) / sd).to_numpy(), ((db[FEATS] - mu) / sd).to_numpy()
        wa, wb = np.sqrt(da.iloc[:, 2].to_numpy()), np.sqrt(db.iloc[:, 2].to_numpy())
        coef = _ols(Xa, da["y"].to_numpy(), wa)
        p = np.column_stack([np.ones(len(db)), Xb]) @ coef
        y = db["y"].to_numpy()
        rows.append({"test": f"fit {a} -> test {b}", "r2": _r2(y, p, wb),
                     "rmse_uniform_pts": np.sqrt(np.average(y**2, weights=wb)),
                     "rmse_with_demog_pts": np.sqrt(np.average((y - p) ** 2, weights=wb)), "counties": len(db)})
    return pd.DataFrame(rows), pd.DataFrame(coefs)


# ------------------------------------------------------------------ 3. turnout

TURNOUT_MODEL = us.ROOT_DIR / "data_public" / "us2026_turnout_model.json"


def turnout(t: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Midterm drop-off: r = top-of-ticket votes 2022 / president 2024 by county, on standardised covariates
    (leave-one-state-out R²). Expected-vote templates for a midterm, compared on the 2022 within-state distribution:
    (a) flat ratio × 2024 president (= the 2024 distribution), (b) the previous midterm (2018), (c) 2024 × the
    demographic ratio model fit on the other states."""
    d = t[["state", "v_top22", "v_pres24", "v_gov18", "v_sen18"] + FEATS].dropna(subset=["v_top22", "v_pres24"] + FEATS)
    d = d[(d["v_top22"] > 0) & (d["v_pres24"] > 0)]
    d["lr"] = np.log(d["v_top22"] / d["v_pres24"])
    d["lr_dev"] = d["lr"] - d.groupby("state")["lr"].transform(lambda s: np.average(s, weights=d.loc[s.index, "v_pres24"]))
    mu, sd = d[FEATS].mean(), d[FEATS].std()
    X, y, w = ((d[FEATS] - mu) / sd).to_numpy(), d["lr_dev"].to_numpy(), np.sqrt(d["v_pres24"].to_numpy())
    pred = np.zeros(len(d))
    for st in d["state"].unique():
        m = (d["state"] == st).to_numpy()
        pred[m] = np.column_stack([np.ones(m.sum()), X[m]]) @ _ols(X[~m], y[~m], w[~m])
    r2 = _r2(y, pred, w)
    d["pred_dev"] = pred
    rows = []
    v18 = d["v_sen18"].where(d["v_sen18"].notna(), d["v_gov18"])
    for name, base in (("flat ratio x 2024 president", d["v_pres24"]),
                       ("previous midterm (2018 top race)", v18),
                       ("2024 president x demographic ratio (LOSO)", d["v_pres24"] * np.exp(d["pred_dev"]))):
        ok = base.notna() & (base > 0)
        g = d[ok].assign(b=base[ok])
        share_p = g["b"] / g.groupby("state")["b"].transform("sum")
        share_a = g["v_top22"] / g.groupby("state")["v_top22"].transform("sum")
        e = np.log(share_p / share_a)
        rows.append({"template": name, "counties": len(g), "states": g["state"].nunique(),
                     "rmse_log_share": float(np.sqrt(np.average(e**2, weights=g["v_top22"]))),
                     "mae_pct_of_county_votes": float(100 * np.average(np.abs(np.expm1(-e)), weights=g["v_top22"]))})
    coef = _ols(X, y, w)
    model = {"feats": FEATS, "mean": mu.to_dict(), "sd": sd.to_dict(), "coef": dict(zip(["const"] + FEATS, coef)),
             "loso_r2": r2,
             "target": "log(2022 top-race votes / 2024 president votes) minus its state mean (vote-weighted)"}
    return pd.DataFrame(rows), model


# ------------------------------------------------------------------ 4. correlated polling error

def poll_correlation() -> dict:
    h = us_polls.history_averages()
    h = h[h["cycle"].isin([2016, 2018, 2020, 2022])]
    g = h.groupby("cycle")["err"]
    within = float(np.average(g.var(ddof=1), weights=g.size() - 1))
    means, n = g.mean(), g.size()
    common = max(float(means.var(ddof=1) - np.mean(within / n)), 0.0)
    rho = common / (common + within)
    return {"cycles": means.round(4).to_dict(), "races": n.to_dict(), "sd_common": np.sqrt(common),
            "sd_race": np.sqrt(within), "rho": rho}


def senate_control(rho: float, n_sim: int = 200_000) -> dict:
    """P(Democratic-caucus Senate majority, ≥ 51 seats with the Republican VP) from the 2026 Senate races: poll
    average and its v3 sd where polled, else the Polymarket price (mean 0.5 + Φ⁻¹(p)·0.039), races independent vs
    sharing a common error with correlation rho."""
    from scipy.stats import norm
    reg = json.loads(us.RACES_REGISTRY.read_text())
    polls = us.load_polls()
    ov = json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "overview.json").read_text())
    price = {r["race_key"]: (r.get("pm") or {}).get("price") for r in ov["offices"]["senate"]["races"]}
    mu, sd = [], []
    for r in reg["races"]:
        if r["office"] != "senate":
            continue
        pa = polls.get(r["race_key"])
        if pa:
            mu.append(pa["avg"]), sd.append(us.poll_sd_by_n(pa["n_polls"]))
        else:
            p = min(max(price.get(r["race_key"]) or 0.5, 0.005), 0.995)
            mu.append(0.5 + norm.ppf(p) * us.POLL_SD_HIST), sd.append(us.POLL_SD_HIST)
    mu, sd = np.array(mu), np.array(sd)
    hold = reg["holdovers"]["senate"]
    d_hold = hold.get("D", 0) + hold.get("I", 0)                    # independents caucus with the Democrats
    rng = np.random.default_rng(7)
    out = {"races": len(mu), "dem_holdovers_incl_ind": d_hold}
    for name, r in (("independent", 0.0), ("correlated", rho)):
        z0 = rng.standard_normal((n_sim, 1))
        z = np.sqrt(r) * z0 + np.sqrt(1 - r) * rng.standard_normal((n_sim, len(mu)))
        seats = d_hold + ((mu + sd * z) > 0.5).sum(axis=1)
        out[name] = {"p_control": float(np.mean(seats >= 51)), "mean_seats": float(seats.mean()),
                     "p10": int(np.percentile(seats, 10)), "p90": int(np.percentile(seats, 90))}
    return out


# ------------------------------------------------------------------ 5. drift (GA vote-type replays)

def drift(n_sim: int = 40) -> pd.DataFrame:
    import us_calibrate as cal
    priors = us.load_priors()
    rng = np.random.default_rng(20261101)
    ga = us.load_config(us.ROOT_DIR / "configs" / "us2026_ga_sen_v1.toml")
    rows = []
    for name, race, r538 in (("GA 2022 gov", "gov", "2022_Gov-G_GA"), ("GA 2022 sen", "sen", "2022_Sen-G_GA")):
        bt = cal.by_type("2022NovGen", race)
        cfg = {**ga, "election": {**ga["election"], "race": "x"}, "prior": {"share": "ga_pres_2020", "votes": "ga_pres_2020"},
               "count": {**ga["count"], "turnout_ratio": 0.80}}
        pri = us.race_prior(cfg, priors)
        pa = us_polls._poll_prior_538(r538)
        v2 = us_polls._v2(cfg, pri, pa["avg"], pa["sd"])
        v3 = {**v2, "model": {**v2["model"], "swing_prior_sd": us.poll_sd_by_n(pa["n_polls"]), "count_scaled_terms": True}}
        total = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        final = (bt["early_d"] + bt["late_d"]).sum() / total
        for s in range(n_sim):
            ev = cal.events(bt, rng)
            cum = ((ev["d"] + ev["r"]).cumsum() / total).to_numpy()
            for c in us_polls.CAL_CHECK:
                rows_ = cal.rows_at(ev, int(np.searchsorted(cum, c)) + 1)
                raw = float(rows_.loc[rows_["party"] == "DEM", "votes"].sum() / rows_["votes"].sum())
                for m, cm in (("v1", cfg), ("v2", v2), ("v3", v3)):
                    r = us.project(rows_, cm, pri)[0]
                    rows.append({"race": name, "model": m, "check": c, "err": 100 * 2 * (r["share_hat"] - final),
                                 "raw_err": 100 * 2 * (raw - final)})
    df = pd.DataFrame(rows)
    return df.groupby(["model", "check"]).agg(bias_pts=("err", "mean"), sd_pts=("err", "std"),
                                              raw_count_bias_pts=("raw_err", "mean")).round(2).reset_index()


# ------------------------------------------------------------------ 6. early vote

def early_vote() -> pd.DataFrame:
    h = json.loads(us.EV_HISTORY.read_text())["states"]["nc"]["by_days_before"]
    return pd.DataFrame([{"days_before": int(k), **{f"{e['year']}_{x}": e[x] for e in v["elections"]
                                                    for x in ("raw_margin", "final_margin", "bias")}}
                         for k, v in h.items()]).sort_values("days_before")


def write_turnout_index(t: pd.DataFrame, model: dict) -> Path:
    """data_public/us2026_turnout_index.csv for every county with covariates: adj = predicted midterm turnout
    relative to the state's average drop-off (exp of the model's log deviation; display only) and lowprop_index =
    standardised predicted drop-off (higher = larger midterm drop-off = lower-propensity electorate)."""
    c = t[FEATS].dropna()
    X = ((c - pd.Series(model["mean"])) / pd.Series(model["sd"])).to_numpy()
    b = np.array([model["coef"][k] for k in ["const"] + FEATS])
    dev = np.column_stack([np.ones(len(X)), X]) @ b
    out = pd.DataFrame({"unit_id": c.index, "adj": np.exp(dev), "lowprop_index": -(dev - dev.mean()) / dev.std()})
    f = us.ROOT_DIR / "data_public" / "us2026_turnout_index.csv"
    with open(f, "w") as fh:
        fh.write("# Predicted midterm turnout by county (display only): analysis/us_model_analysis.py turnout model "
                 f"(leave-one-state-out R2 {model['loso_r2']:.3f}); inputs MEDSL 2022/2024 (CC0) and Census ACS "
                 "2020-2024, PEP 2025, 2023 gazetteer (public domain)\n")
        out.to_csv(fh, index=False, float_format="%.4f")
    return f


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    t = county_table()
    t.to_csv(OUT / "county_table.csv")
    res = {}
    res["swing"] = swing_rules(t)
    res["demog"], coefs = demographics(t)
    res["demog_coefs"] = coefs
    res["turnout"], model = turnout(t)
    TURNOUT_MODEL.write_text(json.dumps({**model, "source": "analysis/us_model_analysis.py; MEDSL 2022/2024 (CC0), "
                                                            "Census ACS/PEP/gazetteer (public domain)"}, indent=1))
    write_turnout_index(t, model)
    corr = poll_correlation()
    ctrl = senate_control(corr["rho"])
    res["drift"] = drift()
    res["early_vote"] = early_vote()
    for k, v in res.items():
        v.to_csv(OUT / f"{k}.csv", index=False)
        print(f"\n== {k}\n{v.round(3).to_string(index=False)}")
    (OUT / "poll_correlation.json").write_text(json.dumps({"corr": corr, "control": ctrl}, indent=1, default=float))
    print("\n== poll correlation", json.dumps(corr, default=float), "\n== senate control", json.dumps(ctrl))
    print(f"\nturnout model LOSO R2 {model['loso_r2']:.3f}")


if __name__ == "__main__":
    main()
