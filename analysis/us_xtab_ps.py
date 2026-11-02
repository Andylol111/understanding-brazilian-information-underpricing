"""Crosstab poststratification county prior for Texas (docs/us_xtab_ps.md).

Pools the group vote shares printed in openly published poll crosstabs, fits an additive logit of two-party D share on
group indicators (poll-specific intercept, shared group effects, one white x college term), shrinks the group effects by
pooled subgroup n, poststratifies onto each county's L2/ACS composition (raked statewide to the polls' own weighted group
shares) and rakes the county shares to a fixed statewide target, so that only the county DISTRIBUTION differs from the v3
uniform swing. Backtests (2022 governor, 2024 Senate/president) use only compositions known before the election.

    uv run python analysis/us_xtab_ps.py backtest     # -> results/us_xtab_ps/backtest_*.csv
    uv run python analysis/us_xtab_ps.py predict      # -> results/us_xtab_ps/county_2026_*.csv
    uv run python analysis/us_xtab_ps.py build        # -> configs/us2026_xtab/, data_public/us2026_xtab_priors.csv (bar passed only)
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BRUNDER_DATA", Path.home() / "brunder-data-local"))
OUT = ROOT / "results" / "us_xtab_ps"
PUB = ROOT / "data_public"
SHRINK_K = 16.0          # n_eff / (n_eff + K), K = 4 / tau^2, tau = 0.5 logit points
DEFF = 1.75              # design effect (Siena 2022: MOE 5.1 at n = 649)
HALF_LIFE = 14.0         # days, the poll rule's recency half-life
SEED = 20261103
B_BOOT = 2000

# ----------------------------------------------------------------------------------------------- model grid
AGE = ["18-29", "30-44", "45-49", "50-64", "65+"]
RACE = ["white", "black", "hispanic", "other"]
GRID = (2, 5, 4, 2)      # gender (M, F) x age x race x edu (no BA, BA+)
G = np.indices(GRID).reshape(4, -1)   # cell -> (g, a, r, e)
NC = G.shape[1]


def cellset(dim: str, level: str) -> np.ndarray | None:
    """Boolean mask over the 80 model cells for a printed (dim, level); None when unmapped."""
    g, a, r, e = G
    m = {
        ("total", "all"): np.ones(NC, bool),
        ("gender", "M"): g == 0, ("gender", "F"): g == 1,
        ("age", "18-29"): a == 0, ("age", "30-44"): a == 1, ("age", "45-64"): (a == 2) | (a == 3),
        ("age", "65+"): a == 4, ("age", "18-49"): a <= 2, ("age", "50-64"): a == 3, ("age", "30-49"): (a == 1) | (a == 2),
        ("age", "50+"): a >= 3, ("age", "45-49"): a == 2, ("age", "18-44"): a <= 1, ("age", "45+"): a >= 2,
        ("race", "white"): r == 0, ("race", "black"): r == 1, ("race", "hispanic"): r == 2,
        ("race", "other"): r == 3, ("race", "nonwhite"): r > 0,
        ("edu", "ba_plus"): e == 1, ("edu", "no_ba"): e == 0,
        ("raceedu", "white_college"): (r == 0) & (e == 1), ("raceedu", "white_noncollege"): (r == 0) & (e == 0),
        ("raceedu", "nonwhite_college"): (r > 0) & (e == 1), ("raceedu", "nonwhite_noncollege"): (r > 0) & (e == 0),
    }
    return m.get((dim, level))


# ----------------------------------------------------------------------------------------------- poll rows
COLS = ["poll_id", "contest", "dim", "level", "d_pct", "r_pct", "share_pct", "n_resp", "source_url", "notes"]


def _pct(s: str) -> float:
    s = s.strip().replace(",", "")
    if s.startswith("<"):
        return 0.25
    return float(s.rstrip("%"))


def parse_siena_2022() -> tuple[pd.DataFrame, dict]:
    """Spectrum News / Siena Texas LV, 16-19 Oct 2022: sri.siena.edu/wp-content/uploads/2022/10/TX1022-Crosstabs.pdf."""
    import pypdf
    f = DATA / "raw/us/siena_tx/TX1022-Crosstabs.pdf"
    t = "\n".join(p.extract_text() for p in pypdf.PdfReader(f).pages)
    lines = t.splitlines()

    def row(prefix: str) -> list[float]:
        l = next(x for x in lines if x.startswith(prefix))
        return [_pct(v) for v in re.findall(r"<?\d+%", l)]
    d, r = row("Beto O"), row("Greg Abbott, the Republican")
    # columns: Total Dem Rep Ind M F Austin DFW Houston Minor Rural SA/South 18-49 50-64 65+ White Black Latino <BA BA+ <50K 50-100K 100K+
    cols = {("total", "all"): 0, ("gender", "M"): 4, ("gender", "F"): 5, ("age", "18-49"): 12, ("age", "50-64"): 13,
            ("age", "65+"): 14, ("race", "white"): 15, ("race", "black"): 16, ("race", "hispanic"): 17,
            ("edu", "no_ba"): 18, ("edu", "ba_plus"): 19}
    share = {("gender", "M"): 48, ("gender", "F"): 50, ("age", "18-49"): 36, ("age", "50-64"): 32, ("age", "65+"): 25,
             ("race", "white"): 54, ("race", "black"): 12, ("race", "hispanic"): 25, ("edu", "no_ba"): 60,
             ("edu", "ba_plus"): 38, ("total", "all"): 100}
    url = "https://sri.siena.edu/wp-content/uploads/2022/10/TX1022-Crosstabs.pdf"
    rows = [dict(poll_id="siena-20221019", contest="gov", dim=k[0], level=k[1], d_pct=d[i], r_pct=r[i],
                 share_pct=share[k], n_resp=np.nan, source_url=url, notes="Spectrum News/Siena, 649 LV") for k, i in cols.items()]
    man = dict(poll_id="siena-20221019", pollster="Spectrum News/Siena", start="2022-10-16", end="2022-10-19",
               population="lv", n_total=649, contests="gov", url=url, status="ok", notes="")
    return pd.DataFrame(rows), man


def parse_nyt_siena_2026() -> tuple[pd.DataFrame, dict]:
    """NYT/Siena Texas likely electorate, 27 Oct-1 Nov 2026, 1,215 LV (PDF of the NYT page on the SSD)."""
    import pypdf
    f = glob.glob(str(DATA / "raw/us/Cross-Tabs_*Texas*.pdf"))[0]
    pg = pypdf.PdfReader(f).pages[2].extract_text(extraction_mode="layout")
    lines = pg.splitlines()

    def toks(l: str) -> list[float]:
        return [_pct(v) for v in re.findall(r"<\.5%|<1%|\d[\d,]*%?", l.split("  ", 1)[-1] if False else l)]

    def pct_row(starts: str, after: str | None = None) -> list[float]:
        i0 = 0
        if after:
            i0 = next(i for i, l in enumerate(lines) if after in l)
        for l in lines[i0:]:
            if l.strip().startswith(starts):
                v = re.findall(r"<\.5%|<1%|\d+%", l)
                if len(v) >= 21:
                    return [_pct(x) for x in v[-21:]]
        # numbers on the following line (candidate name wrapped)
        for i in range(i0, len(lines)):
            if lines[i].strip().startswith(starts):
                for l in lines[i + 1:i + 3]:
                    v = re.findall(r"<\.5%|<1%|\d+%", l)
                    if len(v) >= 21:
                        return [_pct(x) for x in v[-21:]]
        raise ValueError(starts)
    sd, sr = pct_row("James Talarico"), pct_row("Ken Paxton")
    gd, gr = pct_row("Gina Hinojosa", after="Texas governor"), pct_row("Greg Abbott", after="Texas governor")
    nums = [l for l in lines if "Number of respondents" in l][0]
    ns = [float(v.replace(",", "")) for v in re.findall(r"\d[\d,]*", nums)]
    shr = [_pct(v) for v in re.findall(r"\d+%", [l for l in lines if "Percentage of total electorate" in l][0])]
    cols = [("total", "all"), ("gender", "M"), ("gender", "F"), ("age", "18-29"), ("age", "30-44"), ("age", "45-64"), ("age", "65+"),
            ("race", "white"), ("race", "black"), ("race", "hispanic"), ("race", "other"), (None, None), ("race", "nonwhite"),
            ("edu", "ba_plus"), ("edu", "no_ba"), ("raceedu", "white_college"), ("raceedu", "white_noncollege"),
            ("raceedu", "nonwhite_college"), ("raceedu", "nonwhite_noncollege"), ("region", "dallas_ft_worth"), ("region", "houston")]
    url = "https://www.nytimes.com/interactive/2026/11/01/polls/times-siena-tx-poll-crosstabs.html"
    rows = []
    for contest, d, r in (("sen", sd, sr), ("gov", gd, gr)):
        for i, (dim, lev) in enumerate(cols):
            if dim is None or dim == "region":
                continue
            rows.append(dict(poll_id="nyt-siena-20261101", contest=contest, dim=dim, level=lev, d_pct=d[i], r_pct=r[i],
                             share_pct=shr[i], n_resp=ns[i], source_url=url, notes="NYT/Siena 1,215 LV; leaners included"))
    man = dict(poll_id="nyt-siena-20261101", pollster="NYT/Siena", start="2026-10-27", end="2026-11-01", population="lv",
               n_total=1215, contests="sen;gov", url=url, status="ok", notes="PDF saved from the NYT page; regions after Houston cut off")
    return pd.DataFrame(rows), man


def load_polls(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """rows + manifest (end as Timestamp) for the year's pool: Siena parsed here + the collected CSVs (if present)."""
    rows, man = [], []
    if year == 2022:
        r, m = parse_siena_2022(); rows.append(r); man.append(m)
    if year == 2026:
        r, m = parse_nyt_siena_2026(); rows.append(r); man.append(m)
    mf, rf = PUB / f"us_xtab_ps_manifest_{year}.csv", PUB / f"us_xtab_ps_rows_{year}.csv"
    if mf.exists() and rf.exists():
        m = pd.read_csv(mf); r = pd.read_csv(rf)
        rows.append(r.reindex(columns=COLS)); man += m[m["status"] == "ok"].to_dict("records")
    rows = pd.concat(rows, ignore_index=True)
    man = pd.DataFrame(man)
    man["end"] = pd.to_datetime(man["end"].astype(str).str.replace(r"\.0$", "", regex=True), format="mixed")
    man = man.drop_duplicates("poll_id")
    man["n_total"] = pd.to_numeric(man["n_total"], errors="coerce")
    man["n_total"] = man["n_total"].fillna(man["n_total"].median())     # n not printed: the pool's median n
    rows = rows[rows["poll_id"].isin(man["poll_id"])]
    return rows, man


# ----------------------------------------------------------------------------------------------- fit
@dataclass
class Fit:
    effects: dict          # (dim -> array of centred, shrunk effects over fine levels); "wxba" -> float
    raw: dict
    ref: dict              # consensus margins used (dim -> array)
    obs: pd.DataFrame      # observations with weights
    poll_w: pd.Series      # share of total LS weight by poll
    n_eff: dict


def consensus_margins(rows: pd.DataFrame, man: pd.DataFrame, asof: pd.Timestamp, prior_age: np.ndarray) -> dict:
    """Weighted (n_total x recency) mean of the polls' printed weighted group shares, mapped to the model's fine levels."""
    w = man.set_index("poll_id").assign(age=lambda d: (asof - d["end"]).dt.days.clip(lower=0))
    w["w"] = np.sqrt(w["n_total"].astype(float)) * 0.5 ** (w["age"] / HALF_LIFE)
    r = rows[rows["share_pct"].notna() & (rows["dim"] != "total")].copy()
    r["w"] = r["poll_id"].map(w["w"])
    r = r.drop_duplicates(["poll_id", "dim", "level"])

    def wm(dim, lev):
        x = r[(r.dim == dim) & (r.level == lev)]
        return np.average(x["share_pct"], weights=x["w"]) / 100 if len(x) else np.nan
    g = np.array([wm("gender", "M"), wm("gender", "F")]); g = g / g.sum()
    rc = np.array([wm("race", k) for k in ("white", "black", "hispanic")])
    other = wm("race", "other")
    if np.isnan(other):
        other = max(1 - np.nansum(rc), 0.03)
    rc = np.append(rc, other); rc = rc / rc.sum()
    e = wm("edu", "ba_plus"); e = 0.38 if np.isnan(e) else e
    # age: direct bands where printed, rest by the L2 prior split
    a = np.full(5, np.nan)
    a18, a30, a65, a50 = wm("age", "18-29"), wm("age", "30-44"), wm("age", "65+"), wm("age", "50-64")
    a1849, a4564 = wm("age", "18-49"), wm("age", "45-64")
    if not np.isnan(a65):
        pass
    else:
        a65 = 0.25
    if not np.isnan(a18) and not np.isnan(a30) and not np.isnan(a4564):
        mid = 1 - a18 - a30 - a65
        a = np.array([a18, a30, mid * prior_age[2] / (prior_age[2] + prior_age[3]),
                      mid * prior_age[3] / (prior_age[2] + prior_age[3]), a65])
    elif not np.isnan(a1849) and not np.isnan(a50):
        y = a1849
        a = np.array([y * prior_age[0] / prior_age[:3].sum(), y * prior_age[1] / prior_age[:3].sum(),
                      y * prior_age[2] / prior_age[:3].sum(), a50, a65])
        a = a / a.sum()
    else:
        a = prior_age.copy()
    return {"gender": g, "age": a / a.sum(), "race": rc, "edu": np.array([1 - e, e])}


def cell_weights(ref: dict) -> np.ndarray:
    w = ref["gender"][G[0]] * ref["age"][G[1]] * ref["race"][G[2]] * ref["edu"][G[3]]
    return w / w.sum()


def _design(coef: np.ndarray, polls: list[str]) -> tuple[np.ndarray, dict]:
    """coef layout: poll intercepts | gender F | age 1..4 | race 1..3 | edu BA | white x BA."""
    k = len(polls)
    al = coef[:k]
    f, ag, rc, ed, wx = coef[k], coef[k + 1:k + 5], coef[k + 5:k + 8], coef[k + 8], coef[k + 9]
    lin = (np.where(G[0] == 1, f, 0) + np.concatenate([[0], ag])[G[1]] + np.concatenate([[0], rc])[G[2]]
           + np.where(G[3] == 1, ed, 0) + np.where((G[2] == 0) & (G[3] == 1), wx, 0))
    return al, lin


def fit_pool(rows: pd.DataFrame, man: pd.DataFrame, contest: str, asof: pd.Timestamp, prior_age: np.ndarray) -> Fit:
    ref = consensus_margins(rows, man, asof, prior_age)
    cw = cell_weights(ref)
    r = rows[(rows["contest"] == contest)].copy()
    r = r[(r["d_pct"] + r["r_pct"]) >= 20]
    r["mask"] = [cellset(d, l) for d, l in zip(r["dim"], r["level"])]
    r = r[r["mask"].notna()].copy()
    ntot = man.set_index("poll_id")["n_total"].astype(float)
    r["n_g"] = r["n_resp"].where(r["n_resp"].notna(), r["share_pct"] / 100 * r["poll_id"].map(ntot))
    # neither n nor share printed: the consensus share of the group's cells x the poll's n (approximation, flagged)
    imputed = r["n_g"].isna()
    r.loc[imputed, "n_g"] = [cw[m].sum() for m in r.loc[imputed, "mask"]] * r.loc[imputed, "poll_id"].map(ntot)
    r["imputed_n"] = imputed
    r.loc[r["dim"] == "total", "n_g"] = r["poll_id"].map(ntot)
    r = r[r["n_g"].notna() & (r["n_g"] > 0)].copy()
    r["d2"] = r["d_pct"] / (r["d_pct"] + r["r_pct"])
    r["n_eff"] = r["n_g"] / DEFF
    r["age_d"] = (asof - r["poll_id"].map(man.set_index("poll_id")["end"])).dt.days.clip(lower=0)
    pq = r["d2"].clip(0.05, 0.95)
    r["w"] = r["n_eff"] * 0.5 ** (r["age_d"] / HALF_LIFE) / (pq * (1 - pq))
    r = r[np.isfinite(r["w"]) & np.isfinite(r["d2"])].copy()
    polls = sorted(r["poll_id"].unique())
    pidx = {p: i for i, p in enumerate(polls)}
    masks = np.stack(r["mask"].to_numpy()).astype(float)
    cwm = masks * cw[None, :]
    den = cwm.sum(1)
    pi = r["poll_id"].map(pidx).to_numpy()
    y, sw = r["d2"].to_numpy(), np.sqrt(r["w"].to_numpy())
    k = len(polls)
    wbar = float(r["w"].mean())

    def resid(coef):
        al, lin = _design(coef, polls)
        p = expit(al[pi][:, None] + lin[None, :])
        pred = (cwm * p).sum(1) / den
        reg = np.sqrt(1e-4 * wbar) * coef[k:]                       # ridge: conditioning only (a 1-logit effect costs one 1-point miss of an average observation)
        smooth = np.sqrt(1e-3 * wbar) * np.array([coef[k + 2] - coef[k + 3]])   # 45-49 vs 50-64 are rarely separable
        return np.concatenate([sw * (pred - y), reg, smooth])
    x0 = np.zeros(k + 10)
    x0[:k] = logit(r.groupby("poll_id")["d2"].first().reindex(polls).clip(0.05, 0.95).to_numpy()) if False else 0.0
    sol = least_squares(resid, x0, method="trf", x_scale=1.0, max_nfev=2000)
    coef = sol.x
    # centred effects (on the consensus reference weights), then shrinkage
    _, lin = _design(coef, polls)
    raw = {}
    g_eff = np.array([0, coef[k]]); raw["gender"] = g_eff - (ref["gender"] * g_eff).sum()
    a_eff = np.concatenate([[0], coef[k + 1:k + 5]]); raw["age"] = a_eff - (ref["age"] * a_eff).sum()
    r_eff = np.concatenate([[0], coef[k + 5:k + 8]]); raw["race"] = r_eff - (ref["race"] * r_eff).sum()
    e_eff = np.array([0, coef[k + 8]]); raw["edu"] = e_eff - (ref["edu"] * e_eff).sum()
    raw["wxba"] = float(coef[k + 9])
    # pooled effective n of each fine level: sum over observations of n_eff x (share of the observation's set in the level)
    n_eff = {"gender": np.zeros(2), "age": np.zeros(5), "race": np.zeros(4), "edu": np.zeros(2)}
    dimidx = {"gender": 0, "age": 1, "race": 2, "edu": 3}
    for (dim, _lev), grp in r.groupby(["dim", "level"]):
        if dim not in dimidx:
            continue
        m = grp["mask"].iloc[0]
        ax = dimidx[dim]
        for lv in range(GRID[ax]):
            frac = cw[m & (G[ax] == lv)].sum() / cw[m].sum()
            n_eff[dim][lv] += grp["n_eff"].sum() * frac
    n_eff["wxba"] = float(r[r["level"] == "white_college"]["n_eff"].sum() + 0.0)
    eff = {d: raw[d] * n_eff[d] / (n_eff[d] + SHRINK_K) for d in ("gender", "age", "race", "edu")}
    eff["wxba"] = raw["wxba"] * n_eff["wxba"] / (n_eff["wxba"] + SHRINK_K)
    wt = r.groupby("poll_id")["w"].sum()
    return Fit(eff, raw, ref, r.drop(columns=["mask"]), wt / wt.sum(), n_eff)


# ----------------------------------------------------------------------------------------------- composition
def _l2(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"countyfips": str}).assign(fips=lambda d: d["countyfips"].str.zfill(5))


def l2_composition(vote_col: str, acs_year: int, l2_dir: str = "/tmp/l2") -> pd.DataFrame:
    """Per county: V (L2 votes of the snapshot election) and shares for gender / age / race / edu (model fine levels).
    vote_col is 'g20201103' or 'g20221108' (c22 file). Age bands are the roll's (registrants, file snapshot 2023-02)."""
    zf = zipfile.ZipFile(DATA / "raw/us/rdh/l2_turnout/texas/TX_l2_2022stats_county.zip")
    a = pd.read_csv(zf.open("TX_l2_2022stats_county.csv"), dtype={"countyfips": str})
    a = a.set_index(a["countyfips"].str.zfill(5)).rename_axis("fips")
    V = a[f"{vote_col}_voted_all"].astype(float)
    gm, gf = a[f"{vote_col}_voted_gender_m"], a[f"{vote_col}_voted_gender_f"]
    gen = np.stack([gm / (gm + gf), gf / (gm + gf)], 1)
    eur, hisp, aa = a[f"{vote_col}_voted_eur"], a[f"{vote_col}_voted_hisp"], a[f"{vote_col}_voted_aa"]
    oth = a[f"{vote_col}_voted_esa"] + a[f"{vote_col}_voted_oth"]
    tot = eur + hisp + aa + oth
    race = np.stack([eur / tot, aa / tot, hisp / tot, oth / tot], 1)
    b = lambda c: a[c].astype(float)
    y = b("age_18_19") + b("age_20_24") + b("age_25_29")
    m = b("age_30_34") + b("age_35_44")
    a45, a50 = 0.5 * b("age_45_54"), 0.5 * b("age_45_54") + b("age_55_64")
    o = b("age_65_74") + b("age_75_84") + b("age_85over")
    age = np.stack([y, m, a45, a50, o], 1)
    age = age / age.sum(1, keepdims=True)
    d = pd.read_parquet(DATA / "processed/us2026/unified/county_demo.parquet")
    ba = d[(d.st == "tx") & (d.year == acs_year)].set_index("fips")["ba"].reindex(a.index)
    edu = np.stack([1 - ba, ba], 1)
    return pd.DataFrame({"V": V}, index=a.index), dict(gender=gen, age=age, race=race, edu=edu.to_numpy() if hasattr(edu, "to_numpy") else edu)


def calibrate(V: np.ndarray, comp: dict, target: dict, iters: int = 200) -> dict:
    """Tilt every county's group shares by county-constant factors until the V-weighted statewide shares equal target."""
    c = {k: v.copy() for k, v in comp.items()}
    wv = V / V.sum()
    for _ in range(iters):
        worst = 0.0
        for k in c:
            st = (wv[:, None] * c[k]).sum(0)
            f = target[k] / st
            worst = max(worst, np.abs(st - target[k]).max())
            c[k] = c[k] * f[None, :]
            c[k] = c[k] / c[k].sum(1, keepdims=True)
        if worst < 1e-10:
            break
    return c


def poststratify(fit: Fit, comp: dict, alpha: float = 0.0) -> np.ndarray:
    """County D share = sum over the 80 cells of (product of county margins) x expit(alpha + effects)."""
    e = fit.effects
    lin = (e["gender"][G[0]] + e["age"][G[1]] + e["race"][G[2]] + e["edu"][G[3]]
           + np.where((G[2] == 0) & (G[3] == 1), e["wxba"], 0.0))
    p = expit(alpha + lin)
    cellw = (comp["gender"][:, G[0]] * comp["age"][:, G[1]] * comp["race"][:, G[2]] * comp["edu"][:, G[3]])
    return (cellw * p[None, :]).sum(1) / cellw.sum(1)


def rake(s: np.ndarray, W: np.ndarray, target: float) -> np.ndarray:
    """Additive constant so that the W-weighted mean equals target (v3's uniform-swing form); clipped to (0.005, 0.995)."""
    out = s.copy()
    for _ in range(50):
        out = np.clip(out + (target - np.average(out, weights=W)), 0.005, 0.995)
        if abs(np.average(out, weights=W) - target) < 1e-12:
            break
    return out


# ----------------------------------------------------------------------------------------------- results
def results() -> pd.DataFrame:
    r = pd.read_parquet(DATA / "processed/us2026/unified/results_county.parquet")
    r = r[r.st == "tx"]
    return r.assign(two=lambda d: d["dem"] + d["rep"])


def res_share(year: int, office: str) -> pd.DataFrame:
    r = results()
    x = r[(r.year == year) & (r.office == office)].set_index("fips")
    return pd.DataFrame({"s": x["dem"] / (x["dem"] + x["rep"]), "V": x["dem"] + x["rep"]})


RGV = ["48061", "48215", "48427", "48489"]
BORDER = ["48141", "48479", "48323", "48505", "48465", "48271", "48229", "48377", "48043", "48443"]


def score(est: pd.Series, act: pd.DataFrame, idx: np.ndarray | None = None) -> dict:
    e = est.reindex(act.index)
    err = (e - act["s"]).abs()
    V = act["V"]
    sel = np.ones(len(act), bool) if idx is None else idx
    return dict(mae_w=float(np.average(err[sel], weights=V[sel])), mae_margin_pts=float(2 * 100 * err[sel].mean()),
                rmse_w=float(np.sqrt(np.average((e - act["s"])[sel] ** 2, weights=V[sel]))))


def boot_diff(uni: pd.Series, ps: pd.Series, act: pd.DataFrame, rng: np.random.Generator, B: int = B_BOOT) -> dict:
    """County-cluster bootstrap of (uniform - PS) for the vote-weighted share MAE and the unweighted margin MAE."""
    u = (uni.reindex(act.index) - act["s"]).abs().to_numpy()
    p = (ps.reindex(act.index) - act["s"]).abs().to_numpy()
    V = act["V"].to_numpy()
    n = len(u)
    dw, dm = [], []
    for _ in range(B):
        i = rng.integers(0, n, n)
        dw.append(np.average(u[i] - p[i], weights=V[i]))
        dm.append(200 * np.mean(u[i] - p[i]))
    q = lambda x: [float(np.quantile(x, 0.025)), float(np.quantile(x, 0.975))]
    return dict(d_mae_w=float(np.average(u - p, weights=V)), d_mae_w_lo=q(dw)[0], d_mae_w_hi=q(dw)[1],
                d_margin_pts=float(200 * np.mean(u - p)), d_margin_lo=q(dm)[0], d_margin_hi=q(dm)[1])


# ----------------------------------------------------------------------------------------------- drivers
def _prior_age_and_comp(vote_col: str, acs_year: int):
    base, comp = l2_composition(vote_col, acs_year)
    V = base["V"].to_numpy()
    prior_age = (V[:, None] * comp["age"]).sum(0) / V.sum()
    return base, comp, V, prior_age


def pipeline(rows, man, contest, asof, vote_col, acs_year):
    """-> (county index, raw PS shares, fit, calibrated composition)."""
    base, comp, V, prior_age = _prior_age_and_comp(vote_col, acs_year)
    fit = fit_pool(rows, man, contest, asof, prior_age)
    comp_c = calibrate(V, comp, fit.ref)
    ps = poststratify(fit, comp_c)
    return base.index, pd.Series(ps, index=base.index), fit, comp_c


BACKTESTS = [
    # label, poll year, contest, result year, office, baseline (year, office), L2 vote col, ACS year, asof
    dict(label="gov2022", pool=2022, contest="gov", year=2022, office="gov", base=(2018, "gov"), vote="g20201103", acs=2022, asof="2022-11-07"),
    dict(label="sen2024", pool=2024, contest="sen", year=2024, office="sen", base=(2020, "sen"), vote="g20221108", acs=2024, asof="2024-11-04"),
    dict(label="pres2024", pool=2024, contest="pres", year=2024, office="pres", base=(2020, "pres"), vote="g20221108", acs=2024, asof="2024-11-04"),
]


def backtest() -> pd.DataFrame:
    global SHRINK_K
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    out, wrows = [], []
    for bt in BACKTESTS:
        rows, man = load_polls(bt["pool"])
        if not (rows["contest"] == bt["contest"]).any():
            continue
        asof = pd.Timestamp(bt["asof"])
        man = man[man["end"] <= asof]
        rows = rows[rows["poll_id"].isin(man["poll_id"])]
        act = res_share(bt["year"], bt["office"])
        s0 = res_share(*bt["base"])["s"]
        target = float(np.average(act["s"], weights=act["V"]))
        for k_, tag in ((16.0, "primary"), (0.0, "noshrink"), (64.0, "shrink64")):
            SHRINK_K = k_
            idx, ps_raw, fit, _ = pipeline(rows, man, bt["contest"], asof, bt["vote"], bt["acs"])
            ps_raw = ps_raw.reindex(act.index)
            uni = pd.Series(rake(s0.reindex(act.index).to_numpy(), act["V"].to_numpy(), target), index=act.index)
            ps = pd.Series(rake(ps_raw.to_numpy(), act["V"].to_numpy(), target), index=act.index)
            hyb = pd.Series(rake(0.5 * uni.to_numpy() + 0.5 * ps.to_numpy(), act["V"].to_numpy(), target), index=act.index)
            if tag == "primary":
                for pid, w in fit.poll_w.items():
                    wrows.append(dict(label=bt["label"], poll_id=pid, weight=w))
                pd.DataFrame({"actual": act["s"], "V": act["V"], "uniform": uni, "ps": ps, "hybrid": hyb}).to_csv(OUT / f"backtest_counties_{bt['label']}.csv")
                pd.Series({**{f"eff_{k}": list(np.round(v, 3)) for k, v in fit.effects.items() if k != "wxba"}, "wxba": fit.effects["wxba"]}).to_json(OUT / f"backtest_effects_{bt['label']}.json")
            for sub, sel in (("all", None), ("rgv", act.index.isin(RGV)), ("border_other", act.index.isin(BORDER)),
                             ("non_border", ~act.index.isin(RGV + BORDER))):
                for name, est in (("uniform", uni), ("ps", ps), ("hybrid", hyb)):
                    out.append(dict(label=bt["label"], variant=tag, subset=sub, method=name, **score(est, act, sel)))
            if tag in ("primary", "noshrink", "shrink64"):
                for name, est in (("ps", ps), ("hybrid", hyb)):
                    b = boot_diff(uni, est, act, np.random.default_rng(SEED))
                    out.append(dict(label=bt["label"], variant=tag, subset="boot_all", method=f"uniform_minus_{name}", **b))
    SHRINK_K = 16.0
    res = pd.DataFrame(out)
    res.to_csv(OUT / "backtest_scores.csv", index=False)
    pd.DataFrame(wrows).to_csv(OUT / "backtest_poll_weights.csv", index=False)
    return res


SWING_BT = [  # post-hoc (written after the PS-only bar failed): baseline = 2022 governor, swing = PS(2024 pool) - PS(2022 pool)
    dict(label="sen2024_swing", contest="sen", office="sen", year=2024), dict(label="pres2024_swing", contest="pres", office="pres", year=2024)]


def swing_backtest() -> pd.DataFrame:
    """Exploratory, NOT part of the pre-declared bar: county share = 2022-governor share + [PS(2024 polls) - PS(2022 polls)] + constant,
    both PS on the same composition (L2 2022 voted, ACS 2024), versus the uniform swing from the same 2022 baseline."""
    out = []
    rows22, man22 = load_polls(2022)
    rows24, man24 = load_polls(2024)
    s0 = res_share(2022, "gov")["s"]
    for bt in SWING_BT:
        act = res_share(bt["year"], bt["office"])
        target = float(np.average(act["s"], weights=act["V"]))
        idx, p24, f24, _ = pipeline(rows24, man24[man24["end"] <= "2024-11-04"], bt["contest"], pd.Timestamp("2024-11-04"), "g20221108", 2024)
        idx, p22, f22, _ = pipeline(rows22, man22[man22["end"] <= "2022-11-07"], "gov", pd.Timestamp("2022-11-07"), "g20221108", 2024)
        sw = (p24 - p22).reindex(act.index)
        uni = pd.Series(rake(s0.reindex(act.index).to_numpy(), act["V"].to_numpy(), target), index=act.index)
        est = pd.Series(rake((s0.reindex(act.index) + sw).to_numpy(), act["V"].to_numpy(), target), index=act.index)
        for sub, sel in (("all", None), ("rgv", act.index.isin(RGV)), ("border_other", act.index.isin(BORDER)),
                         ("non_border", ~act.index.isin(RGV + BORDER))):
            for name, e in (("uniform", uni), ("ps_swing", est)):
                out.append(dict(label=bt["label"], subset=sub, method=name, **score(e, act, sel)))
        out.append(dict(label=bt["label"], subset="boot_all", method="uniform_minus_ps_swing", **boot_diff(uni, est, act, np.random.default_rng(SEED))))
        pd.DataFrame({"actual": act["s"], "V": act["V"], "uniform": uni, "ps_swing": est}).to_csv(OUT / f"swing_counties_{bt['label']}.csv")
    res = pd.DataFrame(out)
    res.to_csv(OUT / "swing_scores.csv", index=False)
    return res


def predict() -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, man = load_polls(2026)
    asof = pd.Timestamp("2026-11-02")
    pj = json.load(open(PUB / "us2026_polls.json"))["races"]
    zf = zipfile.ZipFile(DATA / "raw/us/rdh/l2_turnout/texas/TX_l2_2022stats_county.zip")
    nm = pd.read_csv(zf.open("TX_l2_2022stats_county.csv"), usecols=["countyfips", "countyname"], dtype={"countyfips": str})
    nm = nm.assign(fips=nm["countyfips"].str.zfill(5)).set_index("fips")["countyname"]
    votes = res_share(2022, "gov")
    NYT = {"sen": (50, 43), "gov": (48, 47)}
    allr, wrows = [], []
    for contest, key, base in (("sen", "tx-sen", (2024, "sen")), ("gov", "tx-gov", (2022, "gov"))):
        target = float(pj[key]["avg"])
        idx, ps_raw, fit, comp_c = pipeline(rows, man, contest, asof, "g20221108", 2026)
        V = votes["V"].reindex(idx).to_numpy()
        s0 = res_share(*base)["s"].reindex(idx).to_numpy()
        uni = rake(s0, V, target)
        ps = rake(ps_raw.to_numpy(), V, target)
        hyb = rake(0.5 * uni + 0.5 * ps, V, target)
        # exploratory swing form: baseline + [PS(2026 polls) - PS(reference-year polls)] on the same composition
        rows_r, man_r = load_polls(2024 if contest == "sen" else 2022)
        asof_r = pd.Timestamp("2024-11-04" if contest == "sen" else "2022-11-07")
        _, ps_ref, _, _ = pipeline(rows_r, man_r[man_r["end"] <= asof_r], "sen" if contest == "sen" else "gov", asof_r, "g20221108", 2026)
        sw = rake(s0 + (ps_raw - ps_ref).to_numpy(), V, target)
        t_nyt = NYT[contest][0] / sum(NYT[contest])
        ps_n = rake(ps_raw.to_numpy(), V, t_nyt)
        uni_n = rake(s0, V, t_nyt)
        df = pd.DataFrame({"county": nm.reindex(idx).to_numpy(), "V_expected": V, "base_share": s0, "v3_prior": uni, "xtab_ps": ps,
                           "hybrid": hyb, "ps_swing": sw, "ps_swing_minus_v3": sw - uni, "ps_minus_v3": ps - uni, "ps_siena_topline": ps_n, "v3_siena_topline": uni_n}, index=idx)
        df["contest"] = contest
        allr.append(df)
        for pid, w in fit.poll_w.items():
            wrows.append(dict(contest=contest, poll_id=pid, weight=w))
        json.dump({"ref": {k: list(map(float, v)) for k, v in fit.ref.items()},
                   "effects": {k: (list(map(float, v)) if k != "wxba" else float(v)) for k, v in fit.effects.items()},
                   "raw": {k: (list(map(float, v)) if k != "wxba" else float(v)) for k, v in fit.raw.items()},
                   "n_eff": {k: (list(map(float, v)) if k != "wxba" else float(v)) for k, v in fit.n_eff.items()},
                   "target": target}, open(OUT / f"effects_2026_{contest}.json", "w"), indent=1)
    res = pd.concat(allr)
    res.to_csv(OUT / "county_2026.csv", index_label="fips")
    pd.DataFrame(wrows).to_csv(OUT / "poll_weights_2026.csv", index=False)
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if cmd == "backtest":
        print(backtest().to_string())
    elif cmd == "swing":
        print(swing_backtest().to_string())
    elif cmd == "predict":
        r = predict()
        print(r.groupby("contest")[["v3_prior", "xtab_ps"]].describe().T)
    else:
        raise SystemExit(cmd)
