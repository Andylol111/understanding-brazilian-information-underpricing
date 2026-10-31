"""Unified pre-count model for US statewide races (Senate / governor) and House districts.

One model instead of "with polls" / "without polls": it combines every signal available before the count and hands the
result to the count model (`brunder.nowcast`, unchanged) as the usual per-county prior (unit_id, s0, V) plus the
statewide swing-prior sd. Signals that do not help out of sample get (near) zero weight automatically; the weights are
learned leave-one-cycle-out (LOCO) on 2016-2024 and reported (docs/us_unified.md).

Components
  national   N_t: national House swing vs the last presidential election from the generic ballot (+ approval / economy
             terms, ridge-shrunk toward zero) — the "national environment" term.
  state      two-party D share of the race: fundamentals F (last presidential lean, last same-office lean, seat holder,
             national swing) blended with the poll average (weight k / (k + n^p) on F by number of polls n), plus a
             poll-lean correction; predictive sd by number of polls from out-of-fold residuals.
  county     share  s_c = S + Σ_j γ_j (x_cj − x̄_j): deviations of the county from its state in the last presidential
             result, last same-office and House results, trend, demographics and their shifts (core terms unpenalised,
             the rest ridge-shrunk), re-centred on the expected-vote weighted state share.
  turnout    V_c = V_state × share_c with the county's share of the state's votes from its past shares, population growth
             and (where official county early-vote history exists) the county's early-vote share.
Everything here is pure numpy / pandas; I/O of the panels is in `load_inputs`, `race_table`, `county_table`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from .paths import DATA, ROOT

U = DATA / "processed" / "us2026" / "unified"
PUBLIC = ROOT / "data_public"
PARAMS_PATH = PUBLIC / "us_unified_params.json"

OFFICES = ("sen", "gov")


# ------------------------------------------------------------------ small numerics


def wridge(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None, lam: float = 0.0,
           pen: np.ndarray | None = None) -> tuple[float, np.ndarray]:
    """Weighted ridge with an unpenalised intercept; features standardised by their weighted sd (penalty `lam` is on the
    standardised coefficients, `pen` ∈ {0,1} selects penalised columns). Returns (intercept, coefficients on raw X)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    w = np.ones(n) if w is None else np.asarray(w, float)
    w = w / w.sum()
    mx = (X * w[:, None]).sum(0)
    my = float((y * w).sum())
    Xc = X - mx
    sd = np.sqrt((w[:, None] * Xc**2).sum(0))
    sd[sd == 0] = 1.0
    Xs = Xc / sd
    pen = np.ones(p) if pen is None else np.asarray(pen, float)
    A = (Xs * w[:, None]).T @ Xs + lam * np.diag(pen) + 1e-12 * np.eye(p)
    b = np.linalg.solve(A, (Xs * w[:, None]).T @ (y - my)) if p else np.zeros(0)
    b = b / sd
    return float(my - mx @ b), b


def crps_normal(y, mu, sd) -> np.ndarray:
    z = (np.asarray(y) - mu) / sd
    return sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi))


def score(y: np.ndarray, mu: np.ndarray, sd: np.ndarray, label: str = "") -> dict:
    """Share-forecast metrics in points of two-party share; P(win) = Φ((μ−½)/sd) vs y > ½."""
    y, mu, sd = (np.asarray(a, float) for a in (y, mu, sd))
    p = np.clip(norm.cdf((mu - 0.5) / sd), 1e-4, 1 - 1e-4)
    win = (y > 0.5).astype(float)
    return {"label": label, "n": int(len(y)), "mae": float(np.abs(y - mu).mean() * 100),
            "rmse": float(np.sqrt(((y - mu) ** 2).mean()) * 100), "brier": float(((p - win) ** 2).mean()),
            "logloss": float(-(win * np.log(p) + (1 - win) * np.log(1 - p)).mean()),
            "cov90": float((np.abs(y - mu) < 1.645 * sd).mean()), "crps": float(crps_normal(y, mu, sd).mean() * 100),
            "sd_mean": float(sd.mean() * 100)}


# ------------------------------------------------------------------ inputs


def load_inputs(root: Path | None = None) -> dict:
    """Panels written by the Phase 1 builders (analysis/us_unified_*.py) and the shared public tables."""
    u = root or U
    rs = pd.read_csv(u / "results_state.csv")
    rc = pd.read_parquet(u / "results_county.parquet")
    rc["fips"] = rc["fips"].astype(str).str.zfill(5)
    demo = pd.read_parquet(u / "county_demo.parquet")
    demo["fips"] = demo["fips"].astype(str).str.zfill(5)
    nat = pd.read_csv(u / "nat_panel.csv")
    dr = pd.read_csv(PUBLIC / "us_state_drift_panel.csv", comment="#")
    hist = DATA / "raw" / "us" / "medsl_hist"
    ss = pd.read_csv(hist / "1976-2024-senate-state.tab", low_memory=False)
    hs = pd.read_csv(hist / "1976-2024-house.tab", low_memory=False)
    return {"rs": rs, "rc": rc, "demo": demo, "nat": nat, "drift": dr, "sen_state": ss, "house": hs}


def _two_party(df: pd.DataFrame, keys: list[str], party: str, votes: str) -> pd.DataFrame:
    g = df[df[party].isin(["DEMOCRAT", "REPUBLICAN"])].groupby(keys + [party])[votes].sum().unstack(fill_value=0).reset_index()
    g["s2"] = g["DEMOCRAT"] / (g["DEMOCRAT"] + g["REPUBLICAN"])
    g["v2"] = g["DEMOCRAT"] + g["REPUBLICAN"]
    return g


def national_house_contested(inp: dict) -> pd.Series:
    """National two-party D share of contested House races (both parties on the ballot), by year."""
    hs = inp["house"]
    g = hs[(hs["stage"].str.upper() == "GEN") & (~hs["special"]) & hs["party"].isin(["DEMOCRAT", "REPUBLICAN"])]
    q = g.groupby(["year", "state_po", "district", "party"])["candidatevotes"].sum().unstack(fill_value=0).reset_index()
    q = q[(q["DEMOCRAT"] > 0) & (q["REPUBLICAN"] > 0)]
    return q.groupby("year").apply(lambda x: x["DEMOCRAT"].sum() / (x["DEMOCRAT"].sum() + x["REPUBLICAN"].sum()),
                                   include_groups=False)


def pres_tables(inp: dict) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(state two-party D share indexed (year, st), state two-party votes, national D share by year) of president."""
    rs = inp["rs"]
    p = rs[rs["contest"] == "pres"].dropna(subset=["s2"]).set_index(["year", "st"])
    nat = p.groupby(level=0).apply(lambda g: g["dem"].sum() / (g["dem"].sum() + g["rep"].sum()), include_groups=False)
    return p["s2"], p["v2"], nat


def office_history(inp: dict) -> dict[str, pd.DataFrame]:
    """Every past statewide contest (year, st, s2, v2): Senate 1976-2024 (MEDSL state file), governor 2016+ (results) and,
    before 2016, the polled races of the drift panel (s2 only)."""
    rs, ss, dr = inp["rs"], inp["sen_state"], inp["drift"]
    s = ss[ss["stage"].isin(["gen", "GEN"]) & ss["party_simplified"].isin(["DEMOCRAT", "REPUBLICAN"])]
    s = _two_party(s, ["year", "state_po", "special"], "party_simplified", "candidatevotes")
    s = s[(s["DEMOCRAT"] > 0) & (s["REPUBLICAN"] > 0)].assign(st=lambda d: d["state_po"].str.lower())[["year", "st", "s2", "v2"]]
    s2 = rs[rs["contest"].isin(["sen", "sen_special"]) & rs["s2"].notna()][["year", "st", "s2", "v2"]]
    sen = pd.concat([s, s2]).drop_duplicates(["year", "st"], keep="last")
    g = dr[dr["office"] == "gov"][["cycle", "state", "act"]].rename(columns={"cycle": "year", "state": "st"})
    g = g.assign(s2=g["act"] / 100, v2=np.nan)[["year", "st", "s2", "v2"]]
    g2 = rs[rs["contest"].isin(["gov", "gov_special"]) & rs["s2"].notna()][["year", "st", "s2", "v2"]]
    gov = pd.concat([g, g2]).drop_duplicates(["year", "st"], keep="last")
    return {"sen": sen, "gov": gov}


def seat_holder_2026(inp: dict, st: str, office: str) -> int:
    """+1 / −1 / 0: party of the winner of the seat's previous regular election (D / R / other), for a 2026 race."""
    rs = inp["rs"]
    if office == "sen":
        special = {"oh": -1, "fl": -1}                         # OH (Vance→Husted), FL (Rubio→Moody) specials: R seats
        if st in special:
            return special[st]
        for contest, yr in (("sen_runoff", 2020), ("sen", 2020), ("sen_special_runoff", 2020), ("sen_special", 2020)):
            r = rs[(rs["year"] == yr) & (rs["st"] == st) & (rs["contest"] == contest) & rs["s2"].notna()]
            if len(r):
                return 1 if r["s2"].iloc[0] > 0.5 else -1
        return 0
    yr = 2024 if st in ("nh", "vt") else 2022
    r = rs[(rs["year"] == yr) & (rs["st"] == st) & (rs["contest"] == "gov") & rs["s2"].notna()]
    if len(r):
        return 1 if r["s2"].iloc[0] > 0.5 else -1
    h = office_history(inp)["gov"]
    h = h[(h["st"] == st) & (h["year"] <= yr)].sort_values("year")
    return 0 if h.empty else (1 if h["s2"].iloc[-1] > 0.5 else -1)


def race_table(inp: dict, specs_2026: list[dict] | None = None, polls_2026: dict | None = None) -> pd.DataFrame:
    """One row per statewide race, 2016-2024 (with results) and, when `specs_2026` = [{"st","office","race_id"}], 2026.

    Columns: t, st, office, special, s2, v2 (results; NaN for 2026), P, n_polls (poll average, final-week rule of
    brunder.us; 2026: `polls_2026[race_id]` = {"avg","n_polls"}), L (last presidential D share of the state), N0 (its
    national share), Lc = L − N0, O / Oy (last same-office contest), NO (national House D share that year), Oc,
    Oc_f (Oc, else Lc), inc (+1/−1 seat holder party), swing_gb (generic ballot − last national presidential D share),
    pres_party, and cycle macro columns."""
    rs, nat, dr = inp["rs"], inp["nat"], inp["drift"]
    pres_s, _, pres_nat = pres_tables(inp)
    hist = office_history(inp)
    nhc = national_house_contested(inp)
    t = rs[rs["contest"].isin(["sen", "gov", "sen_special", "gov_special"]) & rs["s2"].notna() & (rs["year"] >= 2016)].copy()
    t["office"] = t["contest"].str[:3]
    t["special"] = t["contest"].str.contains("special")
    t["q"] = t["v2"] / t["total_all"]
    t["inc"] = t["incumbent_party"].map({"D": 1, "R": -1, "I": 0}).fillna(0).astype(int)
    t = t.rename(columns={"year": "t"})[["t", "st", "office", "special", "s2", "v2", "q", "inc"]]
    t = t[(t["q"] >= 0.75) & t["s2"].between(0.08, 0.92)]
    if specs_2026:
        rows = [{"t": 2026, "st": s["st"], "office": s["office"], "special": False, "s2": np.nan, "v2": np.nan, "q": np.nan,
                 "inc": seat_holder_2026(inp, s["st"], s["office"]), "race_id": s["race_id"]} for s in specs_2026]
        t = pd.concat([t, pd.DataFrame(rows)], ignore_index=True)
    pl = dr[dr["office"].isin(OFFICES)].rename(columns={"cycle": "t", "state": "st"})[["t", "st", "office", "special", "avg", "n_polls"]]
    pl = pl.assign(P=pl["avg"] / 100).drop(columns="avg")
    t = t.merge(pl, on=["t", "st", "office", "special"], how="left")
    if polls_2026:
        for i in t.index[t["t"] == 2026]:
            p = polls_2026.get(t.at[i, "race_id"])
            if p:
                t.at[i, "P"], t.at[i, "n_polls"] = p["avg"], p["n_polls"]
    t["n_polls"] = t["n_polls"].fillna(0).astype(int)
    t["bp"] = t["t"].map(lambda y: y - 2 if y % 4 == 2 else y - 4)
    t["L"] = [pres_s.get((b, s), np.nan) for b, s in zip(t["bp"], t["st"])]
    t["N0"] = t["bp"].map(pres_nat)
    prev = []
    for r in t.itertuples():
        h = hist[r.office]
        h = h[(h["st"] == r.st) & (h["year"] < r.t)].sort_values("year")
        prev.append((h["s2"].iloc[-1], h["year"].iloc[-1]) if len(h) else (np.nan, np.nan))
    t["O"], t["Oy"] = zip(*prev) if prev else ([], [])
    t["NO"] = t["Oy"].map(nhc)
    t["Lc"] = t["L"] - t["N0"]
    t["Oc"] = t["O"] - t["NO"]
    t["Oc_f"] = t["Oc"].fillna(t["Lc"])
    n = nat.set_index("cycle")
    t["swing_gb"] = t["t"].map(n["gb_avg"] - n["pres_d2_last"])
    t["pres_party"] = t["t"].map(n["pres_party"])
    t["app"] = t["t"].map(n["pres_party"] * n["net_approve"] / 100)
    return t[t["L"].notna()].reset_index(drop=True)


# ------------------------------------------------------------------ national swing


NAT_EXTRAS = {"app": "president-party net approval / 100", "ums": "president-party × 12-month change of consumer sentiment / 100",
              "unr": "−president-party × 12-month change of unemployment / 100", "dpi": "president-party × real DPI growth / 100"}


def nat_table(nat: pd.DataFrame) -> pd.DataFrame:
    n = nat.copy()
    n["y_c"] = n["house_d2_nat"] - n["pres_d2_last"]          # realised national House swing (contested seats)
    n["gb_d"] = n["gb_avg"] - n["pres_d2_last"]
    n["app"] = n["pres_party"] * n["net_approve"] / 100
    n["ums"] = n["pres_party"] * n["d_umcsent12"] / 10000
    n["unr"] = -n["pres_party"] * n["d_unrate12"] / 100
    n["dpi"] = n["pres_party"] * n["dpi_g12"] / 100
    return n


def fit_nat(n: pd.DataFrame, extras: tuple[str, ...] = (), lam: float = 0.0) -> dict:
    """y_c ~ gb_d (unpenalised) + extras (ridge λ on standardised terms)."""
    tr = n[n["y_c"].notna() & n["gb_d"].notna()]
    feats = ["gb_d", *extras]
    b0, b = wridge(tr[feats].values, tr["y_c"].values, None, lam, np.array([0.0] + [1.0] * len(extras)))
    res = tr["y_c"].values - (b0 + tr[feats].values @ b)
    return {"feats": feats, "b0": b0, "b": dict(zip(feats, map(float, b))), "lam": lam,
            "resid_sd": float(np.sqrt((res**2).sum() / max(len(tr) - len(feats) - 1, 1))), "n": int(len(tr))}


def predict_nat(m: dict, row: pd.Series | pd.DataFrame) -> np.ndarray:
    X = np.atleast_2d(row[m["feats"]].astype(float).values) if isinstance(row, pd.Series) else row[m["feats"]].astype(float).values
    return m["b0"] + X @ np.array([m["b"][f] for f in m["feats"]])


def nat_oof(n: pd.DataFrame, exclude: tuple[int, ...] = (), extras: tuple[str, ...] = (), lam: float = 0.0) -> dict[int, float]:
    """Out-of-fold predicted national swing for each cycle: model fitted without that cycle (and without `exclude`)."""
    out = {}
    for c in n["cycle"]:
        tr = n[(n["cycle"] != c) & ~n["cycle"].isin(exclude) & n["y_c"].notna()]
        m = fit_nat(tr, extras, lam)
        out[int(c)] = float(predict_nat(m, n[n["cycle"] == c].iloc[0])[0])
    return out


# ------------------------------------------------------------------ state model

F_FEATS = ["Lc", "Oc_f", "swing_hat", "inc"]          # fundamentals regression
CORR_FEATS = ["Lc", "inc"]                            # correction of the poll blend (poll-lean / seat holder)
SD_EDGES = (0, 2, 5, 10)                               # n-poll bins: 0 | 1-2 | 3-5 | 6-10 | 11+


def n_bin(n) -> np.ndarray:
    n = np.asarray(n)
    return np.select([n == 0, n <= 2, n <= 5, n <= 10], [0, 1, 2, 3], 4)


def _blend_w(n: np.ndarray, k: float, p: float) -> np.ndarray:
    """Weight on fundamentals: k / (k + n^p); 1 where there are no polls."""
    return np.where(n > 0, k / (k + np.maximum(n, 1).astype(float) ** p), 1.0)


def _f_fit(tr: pd.DataFrame, lam: float) -> dict:
    b0, b = wridge(tr[F_FEATS].fillna(0).values, tr["s2"].values, None, lam, np.zeros(len(F_FEATS)))
    return {"b0": b0, "b": dict(zip(F_FEATS, map(float, b)))}


def _f_pred(m: dict, df: pd.DataFrame) -> np.ndarray:
    return m["b0"] + df[F_FEATS].fillna(0).values @ np.array([m["b"][f] for f in F_FEATS])


def _stage2(df: pd.DataFrame, F: np.ndarray, k: float, p: float, g: dict | None) -> np.ndarray:
    w = _blend_w(df["n_polls"].values, k, p)
    u = np.where(df["n_polls"].values > 0, w * F + (1 - w) * df["P"].fillna(0).values, F)
    if g:
        corr = g["b0"] + df[CORR_FEATS].fillna(0).values @ np.array([g["b"][f] for f in CORR_FEATS])
        u = u + np.where(df["n_polls"].values > 0, corr, 0.0)
    return u


def fit_state(tr: pd.DataFrame, sw_train: dict[int, float], f_lam: float = 0.0, g_lam: float = 4.0, g_center: bool = True,
              grid_k=(0.25, 0.5, 1.0, 2.0), grid_p=(0.5, 1.0)) -> dict:
    """Fit the state model on `tr` (race_table rows with results; `swing_hat` taken from `sw_train[t]` = the out-of-fold
    national swing of each training cycle). Hyper-parameters (k, p, g) and the predictive-sd table are chosen / estimated on
    out-of-fold predictions, rotating over the training cycles (so none of them sees the race it is applied to)."""
    tr = tr.assign(swing_hat=tr["t"].map(sw_train)).reset_index(drop=True)
    cyc = sorted(tr["t"].unique())
    oof_F = np.full(len(tr), np.nan)
    for c in cyc:
        m = _f_fit(tr[tr["t"] != c], f_lam)
        oof_F[(tr["t"] == c).values] = _f_pred(m, tr[tr["t"] == c])
    # blend hyper-parameters: minimise MAE of the out-of-fold blend over polled races (ties → smaller k)
    polled = (tr["n_polls"] > 0).values
    best = None
    for k in grid_k:
        for p in grid_p:
            u = _stage2(tr, oof_F, k, p, None)
            e = np.abs(u - tr["s2"].values)[polled].mean()
            if best is None or e < best[0] - 1e-9:
                best = (e, k, p)
    _, k, p = best
    # poll-lean correction on out-of-fold residuals of polled races (ridge λ = g_lam on standardised terms)
    u0 = _stage2(tr, oof_F, k, p, None)
    res = (tr["s2"].values - u0)
    g = None
    if polled.sum() > 30 and g_lam is not None:
        sub = tr[polled]
        b0, b = wridge(sub[CORR_FEATS].fillna(0).values, res[polled], None, g_lam, np.ones(len(CORR_FEATS)))
        if g_center:     # no constant shift: the correction has mean zero on the training polled races (a constant "polls overstate D" bet flips by era)
            b0 = float(-sub[CORR_FEATS].fillna(0).values.mean(0) @ b)
        g = {"b0": b0, "b": dict(zip(CORR_FEATS, map(float, b)))}
    r1 = tr["s2"].values - _stage2(tr, oof_F, k, p, g)
    if g is not None:       # sd from residuals where the correction itself is also out-of-fold (rotating the cycles)
        r1 = tr["s2"].values - u0
        for c in cyc:
            tm = (tr["t"] != c).values & polled
            b0_, b_ = wridge(tr.loc[tm, CORR_FEATS].fillna(0).values, res[tm], None, g_lam, np.ones(len(CORR_FEATS)))
            if g_center:
                b0_ = float(-tr.loc[tm, CORR_FEATS].fillna(0).values.mean(0) @ b_)
            te_ = (tr["t"] == c).values & polled
            corr = b0_ + tr.loc[te_, CORR_FEATS].fillna(0).values @ b_
            r1[te_] = res[te_] - corr
    bins = n_bin(tr["n_polls"].values)
    sd = {}
    for b_ in range(5):
        m_ = bins == b_
        sd[b_] = float(np.sqrt((r1[m_] ** 2).mean())) if m_.sum() >= 8 else float(np.sqrt((r1**2).mean()))
    # pool adjacent bins so the sd never grows with more polls (monotone), as brunder.us.poll_sd_bins does
    for b_ in range(1, 5):
        if sd[b_] > sd[b_ - 1]:
            sd[b_] = sd[b_ - 1]
    # cycle-wide common error (shared by all races of a cycle) vs state-specific part: from out-of-fold residuals
    cm = pd.Series(r1).groupby(tr["t"].values).mean()
    common = float(np.sqrt(max((cm**2).mean() - (np.var(r1) / (len(tr) / len(cm))), 0.0)))
    return {"f": _f_fit(tr, f_lam), "k": float(k), "p": float(p), "g": g, "sd": {str(a): v for a, v in sd.items()},
            "f_lam": f_lam, "g_lam": g_lam, "n_train": int(len(tr)), "n_polled": int(polled.sum()),
            "cycle_common_sd": common, "oof_mae_polled": float(np.abs(r1)[polled].mean()),
            "oof_mae_unpolled": float(np.abs(r1)[~polled].mean()) if (~polled).any() else None}


def predict_state(m: dict, df: pd.DataFrame, swing_hat: float | dict) -> pd.DataFrame:
    """Mean, sd and P(win) per race; `swing_hat` = predicted national swing (scalar or {cycle: value})."""
    d = df.assign(swing_hat=(df["t"].map(swing_hat) if isinstance(swing_hat, dict) else swing_hat))
    F = _f_pred(m["f"], d)
    mu = _stage2(d, F, m["k"], m["p"], m["g"])
    sd = np.array([m["sd"][str(b)] for b in n_bin(d["n_polls"].values)])
    out = d[["t", "st", "office"]].copy()
    out["F"], out["mu"], out["sd"] = F, mu, sd
    out["p_win"] = norm.cdf((mu - 0.5) / sd)
    return out


# ------------------------------------------------------------------ county table

CTY_COLS = ["ba", "hisp", "black", "age65", "age18_29", "lninc", "lndens", "d_ba", "d_hisp", "d_age65", "d_age18_29",
            "d_black", "d_lndens", "d2_ba", "d2_hisp", "pop_chg4", "pop_g"]


def _getv(tab: pd.DataFrame, year, fips, col: str) -> np.ndarray:
    return tab[col].reindex(pd.MultiIndex.from_arrays([np.asarray(year), np.asarray(fips)])).to_numpy()


def county_table(inp: dict, races: pd.DataFrame) -> pd.DataFrame:
    """County rows for every race in `races`: current result (s2, v2; NaN in 2026), last presidential (sp_b, vp_b, sp_bb:
    the one before), last same-office contest at county level (so_b, vo_b), House (sh_b, vh_b, t−2) and the county
    demographics of year t (vintage ending t−2) with their changes. Column `race` identifies the race."""
    rc, demo = inp["rc"], inp["demo"].set_index(["year", "fips"])
    contest = lambda o, sp: o + ("_special" if sp else "")
    pres = rc[rc["contest"] == "pres"].set_index(["year", "fips"])[["s2", "v2"]]
    offs = {}
    for o in OFFICES:
        d = rc[rc["contest"].isin([o, o + "_special"])].assign(sp=lambda x: x["contest"].str.contains("special"))
        offs[o] = d.sort_values("sp").drop_duplicates(["year", "fips"]).set_index(["year", "fips"])[["s2", "v2"]]
    house = rc[rc["contest"] == "house_total"].set_index(["year", "fips"])[["s2", "v2"]]
    cur_idx = rc.set_index(["year", "st", "contest"])
    parts = []
    for r in races.itertuples():
        if r.t == 2026:
            base = pres.loc[2024]
            base = base[base.index.str[:2].isin(demo.loc[2026].loc[lambda d: d["st"] == r.st].index.str[:2])] if False else base
            fips = demo.loc[2026].loc[lambda d: d["st"] == r.st].index
            cur = pd.DataFrame({"fips": fips, "s2": np.nan, "v2": np.nan})
        else:
            c = rc[(rc["year"] == r.t) & (rc["st"] == r.st) & (rc["contest"] == contest(r.office, r.special))]
            cur = c[["fips", "s2", "v2"]]
        if cur.empty:
            continue
        parts.append(cur.assign(t=r.t, st=r.st, office=r.office, special=r.special, bp=r.bp))
    C = pd.concat(parts, ignore_index=True)
    C["race"] = C["t"].astype(str) + "_" + C["st"] + "_" + C["office"] + C["special"].astype(int).astype(str)
    C["sp_b"], C["vp_b"] = _getv(pres, C["bp"], C["fips"], "s2"), _getv(pres, C["bp"], C["fips"], "v2")
    C["sp_bb"] = _getv(pres, C["bp"] - 4, C["fips"], "s2")
    so, vo, oy = (np.full(len(C), np.nan) for _ in range(3))
    for o in OFFICES:
        m = (C["office"] == o).to_numpy()
        for yr in (2024, 2022, 2020, 2018, 2016):
            pend = m & (C["t"].to_numpy() > yr) & np.isnan(so)
            if not pend.any():
                continue
            fi = C["fips"].to_numpy()[pend]
            s, v = _getv(offs[o], np.full(len(fi), yr), fi, "s2"), _getv(offs[o], np.full(len(fi), yr), fi, "v2")
            idx, ok = np.where(pend)[0], ~np.isnan(s)
            so[idx[ok]], vo[idx[ok]], oy[idx[ok]] = s[ok], v[ok], yr
    C["so_b"], C["vo_b"], C["oy"] = so, vo, oy
    C["sh_b"], C["vh_b"] = _getv(house, C["t"] - 2, C["fips"], "s2"), _getv(house, C["t"] - 2, C["fips"], "v2")
    for c in CTY_COLS:
        C[c] = _getv(demo, C["t"], C["fips"], c)
    C = C[C["sp_b"].notna() & ((C["t"] == 2026) | (C["s2"].notna() & (C["v2"] > 0) & C["s2"].between(0.01, 0.99)))].copy()
    C["so_f"] = C["so_b"].fillna(C["sp_b"])
    C["sh_f"] = C["sh_b"].fillna(C["sp_b"])
    C["trend"] = C["sp_b"] - C["sp_bb"]
    C["w0"] = C["vp_b"].fillna(C["v2"])
    return C.reset_index(drop=True)


# ------------------------------------------------------------------ county share model

SHARE_CORE = ["sp_b", "so_f", "sh_f"]
SHARE_EXTRA = ["trend", "ba", "hisp", "black", "age65", "lndens", "lninc", "d_ba", "d_hisp", "d_age65", "d_age18_29",
               "d_black", "d_lndens", "pop_chg4", "pop_g", "d2_ba", "d2_hisp"]
SHARE_FEATS = SHARE_CORE + SHARE_EXTRA


def add_dev(C: pd.DataFrame, cols: list[str], by: str = "race") -> pd.DataFrame:
    """Add <col>_dv = county value minus the baseline-vote weighted mean over its race's counties (missing → 0)."""
    out = {}
    for c in cols:
        ok = C[c].notna()
        w = C["w0"].where(ok, 0.0)
        num = (C[c].fillna(0) * w).groupby(C[by]).transform("sum")
        den = w.groupby(C[by]).transform("sum")
        out[c + "_dv"] = (C[c] - num / den.where(den > 0)).fillna(0.0)
    return C.assign(**out)


def state_share_actual(C: pd.DataFrame) -> pd.Series:
    return (C["s2"] * C["v2"]).groupby(C["race"]).transform("sum") / C["v2"].groupby(C["race"]).transform("sum")


def fit_share(C: pd.DataFrame, lam: float, feats: list[str] = SHARE_FEATS, core: list[str] = SHARE_CORE) -> dict:
    """WLS (weights √v2) of the county's deviation from its state's actual share on the deviations of the features; core
    features unpenalised, others ridge λ on standardised terms."""
    tr = C[C["s2"].notna()]
    y = (tr["s2"] - state_share_actual(C)[tr.index]).values
    X = tr[[f + "_dv" for f in feats]].values
    pen = np.array([0.0 if f in core else 1.0 for f in feats])
    b0, b = wridge(X, y, np.sqrt(tr["v2"].values), lam, pen)
    res = y - (b0 + X @ b)
    w = tr["v2"].values
    return {"feats": feats, "core": core, "lam": lam, "b0": b0, "b": dict(zip(feats, map(float, b))),
            "resid_rmse_w": float(np.sqrt(np.average(res**2, weights=w))), "resid_sd": float(res.std()), "n": int(len(tr))}


def predict_dev(m: dict, C: pd.DataFrame) -> np.ndarray:
    return C[[f + "_dv" for f in m["feats"]]].values @ np.array([m["b"][f] for f in m["feats"]])


def select_lam(C: pd.DataFrame, grid=(0.0, 1e-3, 1e-2, 1e-1, 1.0, 10.0)) -> float:
    """λ by leave-one-cycle-out vote-weighted RMSE of the county deviation (given the actual state share) on `C`."""
    y = C["s2"] - state_share_actual(C)
    best = None
    for lam in grid:
        e2 = 0.0
        for c in sorted(C["t"].unique()):
            m = fit_share(C[C["t"] != c], lam)
            te = C[C["t"] == c]
            e = (y[te.index] - predict_dev(m, te)).values
            e2 += (e**2 * te["v2"].values).sum()
        if best is None or e2 < best[0]:
            best = (e2, lam)
    return best[1]


# ------------------------------------------------------------------ county turnout shares

TURN_FEATS = ["x_o", "x_h", "pop_chg4"]
TURN_EXTRA = ["ba", "age65", "lninc"]


def _share_of(C: pd.DataFrame, col: str) -> pd.Series:
    x = C[col]
    tot = x.fillna(0).groupby(C["race"]).transform("sum")
    return x / tot.where(tot > 0)


def turnout_features(C: pd.DataFrame) -> pd.DataFrame:
    """Log-share ratios of the last same-office / House result to the last presidential result (x_o, x_h) and the demographic
    columns, each centred on the baseline-vote weighted mean of the race (…_c); l_lp = log of the county's share of its
    state's presidential votes. Counties with a missing term get the race mean (0)."""
    D = C.copy()
    for c, nm in (("vp_b", "lp"), ("vo_b", "lo"), ("vh_b", "lh")):
        D["l_" + nm] = np.log(_share_of(D, c)).replace([np.inf, -np.inf], np.nan)
    D["x_o"] = D["l_lo"].fillna(D["l_lp"]) - D["l_lp"]
    D["x_h"] = D["l_lh"].fillna(D["l_lp"]) - D["l_lp"]
    for c in TURN_FEATS + TURN_EXTRA:
        x = D[c].replace([np.inf, -np.inf], np.nan)
        ok = x.notna()
        w = D["w0"].where(ok, 0.0)
        D[c + "_c"] = (x - (x.fillna(0) * w).groupby(D["race"]).transform("sum") / w.groupby(D["race"]).transform("sum")).fillna(0.0)
    return D


def fit_turnout(D: pd.DataFrame, lam: float = 0.0, feats: list[str] | None = None) -> dict:
    """log(county share of state votes) − l_lp ~ centred features (WLS √v2)."""
    feats = feats or TURN_FEATS + TURN_EXTRA
    tr = D[D["v2"].notna() & D["l_lp"].notna()]
    tr = tr[tr["v2"] > 0]
    sh = tr["v2"] / tr["v2"].groupby(tr["race"]).transform("sum")
    y = (np.log(sh) - tr["l_lp"]).values
    pen = np.array([0.0 if f in TURN_FEATS else 1.0 for f in feats])
    b0, b = wridge(tr[[f + "_c" for f in feats]].values, y, np.sqrt(tr["v2"].values), lam, pen)
    return {"feats": feats, "lam": lam, "b0": b0, "b": dict(zip(feats, map(float, b)))}


def predict_turnout_share(m: dict, D: pd.DataFrame) -> pd.Series:
    """Predicted county share of its race's two-party votes (renormalised over the race's counties)."""
    lg = D["l_lp"] + D[[f + "_c" for f in m["feats"]]].values @ np.array([m["b"][f] for f in m["feats"]])
    sh = np.exp(lg)
    return sh / sh.groupby(D["race"]).transform("sum")


# ------------------------------------------------------------------ assembling the county prior


def county_prior(C: pd.DataFrame, share_model: dict, turnout_model: dict, S_hat: float, V_state: float,
                 ev_blend: pd.Series | None = None, ev_w: float = 0.0) -> pd.DataFrame:
    """Per-county prior of ONE race (rows of `county_table` for it, with deviation columns added by `add_dev` and the
    turnout features of `turnout_features`): s0 = S_hat + deviation, re-centred so that the expected-vote weighted mean is
    exactly S_hat; V = V_state × predicted county share. `ev_blend` (county early-vote share of the state's early votes,
    same index) shifts the vote share: share = (1−ev_w)·model + ev_w·ev_blend."""
    sh = predict_turnout_share(turnout_model, C)
    if ev_blend is not None and ev_w > 0:
        e = ev_blend.reindex(C.index)
        ok = e.notna()
        sh = sh.where(~ok, (1 - ev_w) * sh + ev_w * e)
        sh = sh / sh.sum()
    s0 = S_hat + predict_dev(share_model, C) + share_model["b0"]
    s0 = np.clip(s0, 0.005, 0.995)
    shift = S_hat - float(np.average(s0, weights=sh))
    s0 = np.clip(s0 + shift, 0.005, 0.995)
    return pd.DataFrame({"fips": C["fips"].values, "s0": s0, "share_v": sh.values, "V": V_state * sh.values}, index=C.index)


def save_params(params: dict, path: Path = PARAMS_PATH) -> None:
    path.write_text(json.dumps(params, indent=1, sort_keys=True, default=float))


def load_params(path: Path = PARAMS_PATH) -> dict:
    return json.loads(path.read_text())


def fips_of(unit_id: str) -> str:
    return unit_id[-5:]


def unit_id(st: str, fips: str) -> str:
    return f"us-{st}-{fips}"
