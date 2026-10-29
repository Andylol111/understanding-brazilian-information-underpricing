"""US 2026: county fundamentals (population, migration, unemployment, income) as predictors of county swing and
turnout, and their use for House districts. Analysis and display only: nothing here writes configs or the registry.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_fundamentals.py [fetch] [panel] [tests] [house]

fetch   downloads (cached under $BRUNDER_DATA/raw/us/fundamentals, raw/us/medsl16|18|22, raw/us/census).
panel   data_public/us_county_panel.csv: one row per county x even year 2008-2026 (features as known before that
        year's election; results where the year has them).
tests   leave-future-out tests of the hypotheses (fit on cycles before t, predict t), tables to
        $BRUNDER_DATA/processed/us2026/fundamentals/*.csv; docs/us_fundamentals.md quotes them.
house   2018 / 2022 House backtest (lean + generic ballot, with and without the feature adjustment) and the 2026
        district baseline vs Polymarket / Kalshi (site/data/us2026/house/markets.json, read only).

Sources (public domain unless noted; docs/us_fundamentals.md):
  - U.S. Census Bureau Population Estimates Program, county totals and components of change, vintages 2007-2025
    (each election uses the vintage published in March of its year).
  - IRS Statistics of Income, county-to-county migration data (inflow and outflow), 2011-12 to 2022-23.
  - BLS Local Area Unemployment Statistics, county annual averages 2005-2025.
  - BEA CAINC1 county personal income (per capita), 1969-2024.
  - ACS 2020-2024 5-year summary file (static shares, context only).
  - MIT Election Data and Science Lab (CC0 / MIT licence): County Presidential Returns 2000-2024, 2016 precinct
    president and House returns (github.com/MEDSL/official-precinct-returns, MIT), 2018 and 2022 precinct returns,
    constituency returns 1976-2018 (House district results, from the Clerk of the House).
  - FEC Federal Elections 2022 (official House results); FiveThirtyEight raw_polls (generic ballot, CC BY 4.0).
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))

RAW = us.US_RAW
FUND = RAW / "fundamentals"
PROC = us.US_PROC / "fundamentals"
PANEL = us.ROOT_DIR / "data_public" / "us_county_panel.csv"
HOUSE_SITE = us.ROOT_DIR / "site" / "data" / "us2026" / "house"

POPEST = "https://www2.census.gov/programs-surveys/popest/datasets"
PEP_URL = {2007: f"{POPEST}/2000-2007/counties/totals/co-est2007-alldata.csv",
           2009: f"{POPEST}/2000-2009/counties/totals/co-est2009-alldata.csv",
           2011: f"{POPEST}/2010-2011/counties/totals/co-est2011-alldata.csv",
           2013: f"{POPEST}/2010-2013/counties/totals/co-est2013-alldata.csv",
           2015: f"{POPEST}/2010-2015/counties/totals/co-est2015-alldata.csv",
           2017: f"{POPEST}/2010-2017/counties/totals/co-est2017-alldata.csv",
           2019: f"{POPEST}/2010-2019/counties/totals/co-est2019-alldata.csv",
           2021: f"{POPEST}/2020-2021/counties/totals/co-est2021-alldata.csv",
           2023: f"{POPEST}/2020-2023/counties/totals/co-est2023-alldata.csv",
           2025: f"{POPEST}/2020-2025/counties/totals/co-est2025-alldata.csv"}
ACS_TABLES = {"b15003": ["B15003_E001", "B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"],
              "b03003": ["B03003_E001", "B03003_E003"], "b02001": ["B02001_E001", "B02001_E003"],
              "b19013": ["B19013_E001"]}
IRS = "https://www.irs.gov/pub/irs-soi/county{kind}{y1:02d}{y2:02d}.csv"
IRS_PAIRS = [(y, y + 1) for y in range(11, 23)]                  # 2011-12 ... 2022-23 (CSV format)
LAUS = "https://www.bls.gov/lau/laucnty{yy:02d}.xlsx"
LAUS_YEARS = range(2005, 2026)
BEA = "https://apps.bea.gov/regional/zip/CAINC1.zip"
MEDSL16 = "https://raw.githubusercontent.com/MEDSL/official-precinct-returns/master"
MEDSL18 = "https://raw.githubusercontent.com/MEDSL/2018-elections-official/master/individual_states"
MEDSL22 = "https://raw.githubusercontent.com/MEDSL/2022-elections-official/main/individual_states"
HOUSE_1976_2018 = "https://raw.githubusercontent.com/MEDSL/constituency-returns/master/1976-2018-house.csv"

YEARS = list(range(2008, 2028, 2))                                # panel years (2026: features only)
# party of the president on election day (+1 Democratic, -1 Republican)
INC = {2008: -1, 2010: 1, 2012: 1, 2014: 1, 2016: 1, 2018: -1, 2020: -1, 2022: 1, 2024: 1, 2026: -1}
FIPS_MERGED = {"51515": "51019", "46113": "46102", "02270": "02158"}   # Bedford city, Shannon, Wade Hampton


# ------------------------------------------------------------------ downloads

def _get(url: str, dest: Path) -> Path:
    us._fetch(url, dest)
    return dest


def _jobs() -> list[tuple[str, Path]]:
    j = [(u, FUND / "pep" / u.rsplit("/", 1)[1]) for u in PEP_URL.values()]
    j += [(IRS.format(kind=k, y1=a, y2=b), FUND / "irs" / f"county{k}{a:02d}{b:02d}.csv")
          for a, b in IRS_PAIRS for k in ("inflow", "outflow")]
    j += [(LAUS.format(yy=y % 100), FUND / "laus" / f"laucnty{y % 100:02d}.xlsx") for y in LAUS_YEARS]
    j += [(BEA, FUND / "bea" / "CAINC1.zip"),
          (f"{MEDSL16}/2016-precinct-president/2016-precinct-president.zip", RAW / "medsl16" / "president.zip"),
          (f"{MEDSL16}/2016-precinct-house/2016-precinct-house.zip", RAW / "medsl16" / "house.zip"),
          (HOUSE_1976_2018, RAW / "house" / "1976-2018-house.csv")]
    for st in us.MEDSL24_STATES:
        j.append((f"{MEDSL18}/2018-{st}-precinct-general.zip", RAW / "medsl18" / f"2018-{st}-precinct-general.zip"))
        j.append((f"{MEDSL22}/2022-{st}-local-precinct-general.zip",
                  RAW / "medsl22" / f"2022-{st}-local-precinct-general.zip"))
    return j


def fetch() -> None:
    jobs = _jobs()
    bls = [j for j in jobs if "bls.gov" in j[0]]

    def one(job):
        try:
            _get(*job)
        except Exception as e:                       # report and continue; missing inputs fail loudly later
            print(f"{job[0]}: {type(e).__name__}: {e}")
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, [j for j in jobs if j not in bls]))
    import time
    for j in bls:                                    # BLS refuses parallel requests: one at a time
        if not j[1].exists():
            one(j)
            time.sleep(2)
    import us_hub
    for t, cols in ACS_TABLES.items():
        us_hub._acs(t, cols)


# ------------------------------------------------------------------ county results

EXCL_ST = ("02", "09")       # AK reports by state house district; CT changed county units (planning regions) in 2022


def _fips(s: pd.Series) -> pd.Series:
    f = s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(5)
    return f.replace(FIPS_MERGED)


def _keep(f: pd.Series) -> pd.Series:
    import us_house
    return (f.str.len() == 5) & f.str[:2].isin(list(us_house.FIPS)) & ~f.str[:2].isin(EXCL_ST) & (f.str[2:] != "000")


def pres_results() -> pd.DataFrame:
    """President 2008-2024 by county (MEDSL County Presidential Returns, CC0): year, fips, dem, rep, total.
    TOTAL rows where a county has them, else the sum of its modes; merged counties summed."""
    import us_house
    d = pd.read_csv(RAW / "medsl" / "countypres_2000-2024.csv", dtype={"county_fips": str})
    d = d[d["year"].between(2008, 2024) & d["county_fips"].notna() & ~us_house._not_cand(d["candidate"])].copy()
    d["fips"] = _fips(d["county_fips"])
    d = d[_keep(d["fips"])]
    tot = d["mode"].fillna("TOTAL").str.upper() == "TOTAL"
    has = tot.groupby([d["year"], d["county_fips"]]).transform("any").astype(bool)
    d = d[(has & tot) | (~has & ~tot)]
    g = d.groupby(["year", "fips"])
    out = pd.DataFrame({"dem": d[d["party"] == "DEMOCRAT"].groupby(["year", "fips"])["candidatevotes"].sum(),
                        "rep": d[d["party"] == "REPUBLICAN"].groupby(["year", "fips"])["candidatevotes"].sum(),
                        "total": g["candidatevotes"].sum()}).fillna(0).reset_index()
    return out.assign(race="pres")


def office_results() -> pd.DataFrame:
    """All cached 2018/2022/2024 U.S. House, U.S. Senate and governor results by county."""
    import us_model_analysis as ma
    rows = []
    for year in (2018, 2022, 2024):
        for st in us.MEDSL24_STATES:
            if st in ("ak", "ct"):
                continue
            for race, office in (("house", "US HOUSE"), ("sen", "US SENATE"), ("gov", "GOVERNOR")):
                r = ma.medsl_county(year, st, office)
                if len(r) and (r["dem"] + r["rep"]).sum() > 0:
                    rows.append(r.assign(year=year, race=race, fips=r["unit_id"].str[-5:].pipe(_fips)))
    d = pd.concat(rows, ignore_index=True)
    return d.groupby(["year", "fips", "race"])[["dem", "rep", "total"]].sum().reset_index()


def midterm_results(offices: pd.DataFrame | None = None) -> pd.DataFrame:
    """Top statewide race of 2018 and 2022: Senate where both parties contested, else governor."""
    d = offices if offices is not None else office_results()
    rows = []
    for year in (2018, 2022):
        y = d[d["year"] == year]
        for st in y["fips"].str[:2].unique():
            z = y[y["fips"].str.startswith(st)]
            for race in ("sen", "gov"):
                r = z[z["race"] == race]
                if len(r) and (r["dem"] > 0).any() and (r["rep"] > 0).any():
                    rows.append(r)
                    break
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------ features (as published before each election)

def _pep(v: int) -> pd.DataFrame:
    d = pd.read_csv(FUND / "pep" / PEP_URL[v].rsplit("/", 1)[1], dtype=str, encoding="latin1")
    d.columns = [c.upper().replace("INTERNALMIG", "DOMESTICMIG") for c in d.columns]
    d = d[d["SUMLEV"] == "050"]
    d.index = _fips(d["STATE"] + d["COUNTY"])
    num = d[[c for c in d.columns if re.match(r"(POPESTIMATE|DOMESTICMIG|INTERNATIONALMIG)\d{4}$", c)]]
    return num.apply(pd.to_numeric, errors="coerce").groupby(level=0).sum(min_count=1)


def pep_features(t: int) -> pd.DataFrame:
    """Vintage t-1 (published in March of t): pop = July 1 estimate of t-1; pop_g = annual % growth over the
    vintage's years since t-5 (at most 4, starting after the census base); ndm / imig = net domestic /
    international migration, % of population per year over the same years."""
    d = _pep(t - 1)
    y0 = max(t - 4, (t - 1) // 10 * 10 + 1)
    ys = range(y0, t)
    pop = d[[f"POPESTIMATE{y}" for y in ys]].sum(axis=1)
    return pd.DataFrame({
        "pop": d[f"POPESTIMATE{t - 1}"],
        "pop_g": 100 * np.log(d[f"POPESTIMATE{t - 1}"] / d[f"POPESTIMATE{y0 - 1}"]) / len(ys),
        "ndm": 100 * d[[f"DOMESTICMIG{y}" for y in ys]].sum(axis=1) / pop,
        "imig": 100 * d[[f"INTERNATIONALMIG{y}" for y in ys]].sum(axis=1) / pop})


def laus() -> pd.DataFrame:
    """BLS LAUS county annual averages: fips x year unemployment rate (%), from unemployed / labour force."""
    rows = []
    for y in LAUS_YEARS:
        x = us._xlsx_rows((FUND / "laus" / f"laucnty{y % 100:02d}.xlsx").read_bytes())
        h = next(i for i, r in enumerate(x) if r and "Labor Force" in [str(c).strip() for c in r])
        n = len(x[h])
        df = pd.DataFrame([(r + [None] * n)[:n] for r in x[h + 1:]], columns=[str(c).strip() for c in x[h]])
        df = df[df["State FIPS Code"].fillna("").str.match(r"^\d+$") & df["County FIPS Code"].fillna("").str.match(r"^\d+$")]
        df = pd.DataFrame({"fips": _fips(df["State FIPS Code"].str.zfill(2) + df["County FIPS Code"].str.zfill(3)),
                           "lf": pd.to_numeric(df["Labor Force"], errors="coerce"),
                           "un": pd.to_numeric(df["Unemployed"], errors="coerce"), "year": y})
        rows.append(df)
    d = pd.concat(rows).groupby(["fips", "year"])[["lf", "un"]].sum(min_count=1)
    return (100 * d["un"] / d["lf"]).unstack("year")


def bea_pcinc() -> pd.DataFrame:
    """BEA CAINC1 line 3 (per capita personal income, $) fips x year. Virginia's combined areas (county + independent
    cities) give their value to every member county / city."""
    b = pd.read_csv(zipfile.ZipFile(FUND / "bea" / "CAINC1.zip").open("CAINC1__ALL_AREAS_1969_2024.csv"), dtype=str,
                    encoding="latin1")
    b = b[b["LineCode"].fillna("").str.strip() == "3"].copy()
    b["fips"] = b["GeoFIPS"].str.replace('"', "").str.strip()
    yrs = [c for c in b.columns if re.match(r"^\d{4}$", c)]
    v = b.set_index("fips")[yrs].apply(pd.to_numeric, errors="coerce")
    v.columns = v.columns.astype(int)
    names = _pep(2025).index.to_series()
    pep = pd.read_csv(FUND / "pep" / PEP_URL[2025].rsplit("/", 1)[1], dtype=str, encoding="latin1")
    va = pep[(pep["SUMLEV"] == "050") & (pep["STATE"] == "51")].set_index("CTYNAME")["COUNTY"]
    extra = {}
    for f, name in b.set_index("fips")["GeoName"].items():
        if f.startswith("519") and "+" in name:
            parts = [p.strip() for p in re.split(r"[,+]", name.replace(", VA*", "").replace(", VA", ""))]
            for i, p in enumerate(parts):
                key = f"{p} County" if i == 0 else f"{p.replace(' City', '')} city"
                if key in va.index:
                    extra["51" + va[key]] = v.loc[f]
    v = pd.concat([v, pd.DataFrame(extra).T])
    v = v[~v.index.duplicated(keep="last")]
    return v[v.index.isin(names.index)]


def _irs(kind: str, a: int) -> pd.DataFrame:
    d = pd.read_csv(FUND / "irs" / f"county{kind}{a:02d}{a + 1:02d}.csv", dtype=str, encoding="latin1")
    d["n2"] = pd.to_numeric(d["n2"], errors="coerce").clip(lower=0)
    return d


def irs_pairs(t: int) -> list[int]:
    """SOI pair starts usable before election t: pairs ending t-4 / t-3 (about a two-year release lag)."""
    return [a for a in (t - 2005, t - 2004) if (a, a + 1) in IRS_PAIRS]


def irs_features(t: int, share: pd.Series) -> pd.DataFrame:
    """IRS SOI county-to-county migration, the two year-pairs ending t-4 and t-3 (published by t; ~2-year lag).
    irs_in / irs_out: US in- / out-migrants (exemptions) as % of population per year; irs_net = in - out;
    inflow_d = population-weighted two-party D share (`share`: previous presidential election) of the origin
    counties of in-migrants with a known origin county; mig_comp = irs_in x (inflow_d - own share), the D-share
    points per year the newcomers add if they vote like their origin counties."""
    pairs = irs_pairs(t)
    if len(pairs) < 2:
        return pd.DataFrame()
    ins, outs, known = [], [], []
    for a in pairs:
        i = _irs("inflow", a)
        i["dest"] = _fips(i["y2_statefips"] + i["y2_countyfips"])
        tot = i[(i["y1_statefips"] == "97") & (i["y1_countyfips"] == "000")]
        ins.append(tot.groupby("dest")["n2"].sum())
        k = i[i["y1_statefips"].str.match(r"^\d\d$") & (i["y1_statefips"].astype(int) <= 56)].copy()
        k["orig"] = _fips(k["y1_statefips"] + k["y1_countyfips"])
        k = k[(k["orig"] != k["dest"]) & (k["y2_countyfips"] != "000")]
        k["s"] = k["orig"].map(share)
        known.append(k[k["s"].notna()][["dest", "n2", "s"]])
        o = _irs("outflow", a)
        o["orig"] = _fips(o["y1_statefips"] + o["y1_countyfips"])
        outs.append(o[(o["y2_statefips"] == "97") & (o["y2_countyfips"] == "000")].groupby("orig")["n2"].sum())
    k = pd.concat(known)
    inflow_d = (k["n2"] * k["s"]).groupby(k["dest"]).sum() / k.groupby("dest")["n2"].sum()
    return pd.DataFrame({"in_n": pd.concat(ins).groupby(level=0).sum() / len(pairs),
                         "out_n": pd.concat(outs).groupby(level=0).sum() / len(pairs), "inflow_d": inflow_d})


def acs_static() -> pd.DataFrame:
    """ACS 2020-2024 5-year shares (context only; static, so not tested out of time here; see
    docs/us_model_analysis.md section 2)."""
    import us_hub
    a = {t: us_hub._acs(t, c) for t, c in ACS_TABLES.items()}
    e = a["b15003"]
    c = pd.DataFrame({"ba": e[ACS_TABLES["b15003"][1:]].sum(axis=1) / e["B15003_E001"],
                      "hisp": a["b03003"]["B03003_E003"] / a["b03003"]["B03003_E001"],
                      "black": a["b02001"]["B02001_E003"] / a["b02001"]["B02001_E001"],
                      "lninc": np.log(a["b19013"]["B19013_E001"].where(a["b19013"]["B19013_E001"] > 0))})
    c.index = _fips(c.index.to_series())
    return c[~c.index.duplicated()]


def prev_pres(t: int) -> int:
    """The last presidential election before year t."""
    return t - 4 if t % 4 == 0 else t - 2


def build_panel() -> pd.DataFrame:
    pres, offices = pres_results(), office_results()
    res = pd.concat([pres, midterm_results(offices)], ignore_index=True)
    for d in (res, offices, pres):
        d["s"] = d["dem"] / (d["dem"] + d["rep"])
    res = res.set_index(["year", "fips"])
    pres_s = res[res["race"] == "pres"]["s"]
    # Wide result fields retain every cached office even though `s` / `votes` select the top target race.
    allres = pd.concat([pres, offices], ignore_index=True).set_index(["year", "fips", "race"])
    wide = pd.concat({"s": allres["s"], "v": allres["total"]}, axis=1).unstack("race")
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    ur, inc = laus(), bea_pcinc()
    acs = acs_static()
    rows = []
    for t in YEARS:
        f = pep_features(t)
        f = f[_keep(f.index.to_series())]
        f["ur"] = ur[t - 1].reindex(f.index)
        f["dur"] = (ur[t - 1] - ur[t - 3]).reindex(f.index)
        f["pcinc"] = inc[t - 2].reindex(f.index)
        f["ginc"] = (100 * np.log(inc[t - 2] / inc[t - 4]) / 2).reindex(f.index)
        own = pres_s.xs(prev_pres(t), level="year") if prev_pres(t) >= 2008 else pd.Series(dtype=float)
        irs = irs_features(t, own)
        if len(irs):
            irs = irs.reindex(f.index)
            f["irs_in"] = 100 * irs["in_n"] / f["pop"]
            f["irs_out"] = 100 * irs["out_n"] / f["pop"]
            f["irs_net"] = f["irs_in"] - f["irs_out"]
            f["inflow_d"] = irs["inflow_d"]
            f["mig_comp"] = f["irs_in"] * (irs["inflow_d"] - own.reindex(f.index))
        if t in res.index.get_level_values("year"):
            r = res.xs(t, level="year").reindex(f.index)
            f["race"], f["s"], f["votes"] = r["race"], r["s"], r["total"]
        if t in wide.index.get_level_values("year"):
            f = f.join(wide.xs(t, level="year").reindex(f.index))
        f = f.join(acs)
        f["year"], f["inc"] = t, INC[t]
        f["st"] = f.index.str[:2]
        rows.append(f.rename_axis("fips").reset_index())
    p = pd.concat(rows, ignore_index=True)
    cols = ["fips", "st", "year", "inc", "race", "s", "votes",
            "s_pres", "v_pres", "s_house", "v_house", "s_sen", "v_sen", "s_gov", "v_gov",
            "pop", "pop_g", "ndm", "imig", "ur", "dur", "pcinc", "ginc", "irs_in", "irs_out", "irs_net",
            "inflow_d", "mig_comp", "ba", "hisp", "black", "lninc"]
    return p.reindex(columns=cols)


def panel() -> Path:
    p = build_panel()
    rnd = {"s": 4, "s_pres": 4, "s_house": 4, "s_sen": 4, "s_gov": 4, "inflow_d": 4,
           "ba": 3, "hisp": 3, "black": 3, "lninc": 3}
    p = p.round({c: rnd.get(c, 3) for c in p.columns if p[c].dtype.kind == "f"})
    for c in ("votes", "v_pres", "v_house", "v_sen", "v_gov", "pop", "pcinc"):
        p[c] = p[c].round().astype("Int64")
    PANEL.write_text("# US county panel 2008-2026 (analysis/us_fundamentals.py; sources and columns in "
                     "docs/us_fundamentals.md)\n" + p.to_csv(index=False))
    print(f"{PANEL.relative_to(us.ROOT_DIR)}: {len(p)} rows, {PANEL.stat().st_size / 1e6:.2f} MB")
    return PANEL


def load_panel() -> pd.DataFrame:
    return pd.read_csv(PANEL, comment="#", dtype={"fips": str, "st": str})


# ------------------------------------------------------------------ tests: fit on cycles before t, predict t

# (target year, previous presidential election); the target is the top race (president, or 2018/2022 Senate else
# governor), the base is the county's previous presidential two-party share.
CYCLES = [(2012, 2008), (2016, 2012), (2018, 2016), (2020, 2016), (2022, 2020), (2024, 2020)]
TESTS = (2016, 2018, 2020, 2022, 2024)
# x_dur / x_ginc: unemployment change and income growth signed by the president's party (+ = good for Democrats if
# a Democrat is president), so one coefficient means "economy moves votes toward / away from the incumbent party".
HYP = {"a_econ": ["x_dur", "x_ginc"], "b_mig_pep": ["ndm"], "b_mig_irs": ["mig_comp", "irs_net"],
       "c_popg": ["pop_g"], "all": ["x_dur", "x_ginc", "ndm", "mig_comp", "irs_net", "pop_g"]}
TURNOUT_HYP = {"c_popg": ["popgap"], "b_mig_pep": ["ndmgap"], "pop+mig": ["popgap", "ndmgap", "irs_net"]}
B_BOOT = 1000
Z90 = 1.645


def cycle_frame(P: pd.DataFrame, t: int, p: int) -> pd.DataFrame:
    """Counties with results in both t and p, in states where both races were contested (statewide two-party
    share 15-85 %). Adds swing, signed and gap-scaled features; missing features are later set to the group mean."""
    a = P[P["year"] == t].set_index("fips")
    b = P[P["year"] == p].set_index("fips")[["s", "votes"]].rename(columns={"s": "s_p", "votes": "v_p"})
    d = a.join(b, how="inner")
    d = d[d["s"].notna() & d["s_p"].notna() & (d["votes"] > 0) & (d["v_p"] > 0)].copy()
    for c in ("s", "s_p"):
        st = (d[c] * d["votes"]).groupby(d["st"]).sum() / d["votes"].groupby(d["st"]).sum()
        d = d[d["st"].map(st).between(0.15, 0.85)]
    d["t"], d["p"], d["mid"] = t, p, t % 4 != 0
    d["x_dur"], d["x_ginc"] = d["inc"] * d["dur"], d["inc"] * d["ginc"]
    d["popgap"], d["ndmgap"] = d["pop_g"] * (t - p), d["ndm"] * (t - p)
    d["lt"] = np.log(d["votes"] / d["v_p"])
    for c in ("mig_comp", "irs_net"):
        if c in d and d[c].isna().all():
            d[c] = np.nan
    return d


def prediction_frame(P: pd.DataFrame, t: int, p: int) -> pd.DataFrame:
    """Pre-election county frame for applying a fitted model: features at t plus share/votes at p; unlike
    cycle_frame it needs no result at t and therefore covers states without a contested statewide target race."""
    a = P[P["year"] == t].set_index("fips").copy()
    b = P[P["year"] == p].set_index("fips")[["s", "votes"]].rename(columns={"s": "s_p", "votes": "v_p"})
    d = a.join(b, how="inner")
    d = d[d["s_p"].notna() & (d["v_p"] > 0)].copy()
    d["t"], d["p"], d["mid"] = t, p, t % 4 != 0
    d["x_dur"], d["x_ginc"] = d["inc"] * d["dur"], d["inc"] * d["ginc"]
    d["popgap"], d["ndmgap"] = d["pop_g"] * (t - p), d["ndm"] * (t - p)
    return d


def _wmean(x: pd.Series, w: pd.Series, g: pd.Series | None) -> pd.Series:
    if g is None:
        return pd.Series(np.average(x, weights=w), index=x.index)
    return (x * w).groupby(g).transform("sum") / w.groupby(g).transform("sum")


def _design(d: pd.DataFrame, feats: list[str], by: str | None, lo: pd.Series, hi: pd.Series) -> np.ndarray:
    """Features clipped to the training range (0.5-99.5 %), missing -> group mean, then demeaned within `by`
    (state) or nationally; weights = votes at the previous election (known before the election)."""
    g, w = (d[by] if by else None), d["v_p"]
    cols = []
    for f in feats:
        x = d[f].clip(lo[f], hi[f])
        x = x.fillna(_wmean(x.fillna(0), w.where(x.notna(), 0) + 1e-12, g))
        cols.append((x - _wmean(x, w, g)).to_numpy())
    return np.column_stack(cols) if cols else np.zeros((len(d), 0))


def _target(d: pd.DataFrame, kind: str, by: str | None) -> tuple[pd.Series, pd.Series]:
    """(y, w): swing in two-party D share (kind 'swing', weights votes at t) or log turnout ratio (kind 'turnout',
    weights votes at p), minus its weighted mean within `by` (state) or nationally."""
    if kind == "swing":
        y, w = d["s"] - d["s_p"], d["votes"]
    else:
        y, w = d["lt"], d["v_p"]
    return y - _wmean(y, w, d[by] if by else None), w


def _has(d: pd.DataFrame, feats: list[str]) -> bool:
    return all(d[f].notna().any() for f in feats)


class Fit:
    """WLS (weights sqrt(votes)) of the demeaned target on demeaned features, pooled over the training cycles that
    have every feature."""

    def __init__(self, train: list[pd.DataFrame], feats: list[str], kind: str, by: str | None):
        self.feats, self.kind, self.by = feats, kind, by
        train = [d for d in train if _has(d, feats)]
        self.n_cycles = len(train)
        self.beta, self.sd_x = np.zeros(len(feats)), np.ones(len(feats))
        if not train:
            return
        allx = pd.concat([d[feats] for d in train])
        self.lo, self.hi = allx.quantile(0.005), allx.quantile(0.995)
        X, Y, W = [], [], []
        for d in train:
            y, w = _target(d, kind, by)
            X.append(_design(d, feats, by, self.lo, self.hi))
            Y.append(y.to_numpy())
            W.append(np.sqrt(w.to_numpy()))
        X, Y, W = np.vstack(X), np.concatenate(Y), np.concatenate(W)
        self.beta = np.linalg.lstsq(X * W[:, None], Y * W, rcond=None)[0]
        self.sd_x = np.sqrt(np.average(X ** 2, axis=0, weights=W ** 2))

    def adj(self, d: pd.DataFrame) -> np.ndarray:
        """Feature part of the prediction (demeaned within state / nationally) for frame d."""
        return _design(d, self.feats, self.by, self.lo, self.hi) @ self.beta


def _rmse(e: np.ndarray, w: np.ndarray) -> float:
    return float(np.sqrt(np.average(e ** 2, weights=w)))


def _boot_delta(cyc: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], seed: int = 0) \
        -> tuple[np.ndarray, np.ndarray]:
    """Cluster (state) bootstrap of RMSE(model) - RMSE(base) per cycle. cyc: (e_base, e_model, w, state) per cycle.
    Returns (draws per cycle [B x n_cycles], point deltas)."""
    rng = np.random.default_rng(seed)
    out = np.empty((B_BOOT, len(cyc)))
    pt = np.array([_rmse(m, w) - _rmse(b, w) for b, m, w, _ in cyc])
    for j, (b, m, w, s) in enumerate(cyc):
        u, inv = np.unique(s, return_inverse=True)
        idx = [np.flatnonzero(inv == k) for k in range(len(u))]
        for i in range(B_BOOT):
            take = np.concatenate([idx[k] for k in rng.integers(0, len(u), len(u))])
            out[i, j] = _rmse(m[take], w[take]) - _rmse(b[take], w[take])
    return out, pt


def _state_err(d: pd.DataFrame, pred: np.ndarray) -> pd.Series:
    """Statewide two-party margin error (points) from county predictions, votes at t as weights."""
    w = d["votes"]
    return 200 * ((pred - d["s"]) * w).groupby(d["st"]).sum() / w.groupby(d["st"]).sum()


def frames(P: pd.DataFrame) -> dict[int, pd.DataFrame]:
    return {t: cycle_frame(P, t, p) for t, p in CYCLES}


def _prev_cycle(t: int) -> int | None:
    ts = [c for c, _ in CYCLES]
    i = ts.index(t)
    return ts[i - 1] if i else None


def _recentre(pred: pd.Series, d: pd.DataFrame, by: str | None) -> pd.Series:
    """Shift county shares so their vote-weighted mean (votes at t) matches the actual result within `by` (state)
    or nationally: the oracle statewide / national swing every model gets."""
    g = d[by] if by else None
    return (pred + _wmean(d["s"], d["votes"], g) - _wmean(pred, d["votes"], g)).clip(0.001, 0.999)


def _state_resid(d: pd.DataFrame, fit: Fit) -> pd.Series:
    """State mean (votes at t) of the national-mode residual: swing - national swing - feature part."""
    y, w = _target(d, "swing", None)
    r = y - fit.adj(d)
    return (r * w).groupby(d["st"]).sum() / w.groupby(d["st"]).sum()


def _rho(F: dict[int, pd.DataFrame], years: list[int], fit: Fit) -> float:
    """Persistence of state effects: WLS slope (state votes) of a state's residual on its residual in the previous
    cycle, over the training cycles; nan when no pair exists."""
    xs, ys, ws = [], [], []
    for c in years:
        pc = _prev_cycle(c)
        if pc is None or not _has(F[c], fit.feats) or not _has(F[pc], fit.feats):
            continue
        rc, rp = _state_resid(F[c], fit), _state_resid(F[pc], fit)
        both = rc.index.intersection(rp.index)
        xs.append(rp[both])
        ys.append(rc[both])
        ws.append(F[c]["votes"].groupby(F[c]["st"]).sum()[both])
    if not xs:
        return np.nan
    x, y, w = pd.concat(xs), pd.concat(ys), pd.concat(ws)
    return float((w * x * y).sum() / (w * x * x).sum())


def predict_swing(F: dict[int, pd.DataFrame], d: pd.DataFrame, t: int, fit: Fit, hier: bool) -> tuple[pd.Series, float]:
    """Feature part (+ state effect if hier) of the predicted share change for frame d (cycle t)."""
    a = pd.Series(fit.adj(d), index=d.index)
    rho = np.nan
    if hier:
        rho = _rho(F, [c for c, _ in CYCLES if c < t], fit)
        pc = _prev_cycle(t) if t in dict(CYCLES) else max(c for c, _ in CYCLES)
        if np.isfinite(rho) and _has(F[pc], fit.feats):
            a = a + rho * d["st"].map(_state_resid(F[pc], fit)).fillna(0)
    return a, rho


def _train_sd(train: list[pd.DataFrame], fit: Fit, by: str | None, model: bool) -> float:
    """In-sample margin RMSE (points) of the baseline / model over the training cycles."""
    e, w = [], []
    for d in train:
        if model and not _has(d, fit.feats):
            continue
        pred = _recentre(d["s_p"] + (fit.adj(d) if model else 0), d, by)
        e.append(200 * (pred - d["s"]).to_numpy())
        w.append(d["votes"].to_numpy())
    return _rmse(np.concatenate(e), np.concatenate(w)) if e else np.nan


def _attach_boot(rows: list[dict], cyc: list, boots: dict, key) -> None:
    draws, pt = _boot_delta(cyc)
    k = len(rows) - len(cyc)
    for j in range(len(cyc)):
        rows[k + j]["d_rmse"] = pt[j]
        rows[k + j]["d_lo"], rows[k + j]["d_hi"] = np.quantile(draws[:, j], [0.05, 0.95])
    boots[key] = (draws, pt, [r["test"] for r in rows[k:]])


def swing_tests(F: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Margin tests. 'state': baseline = uniform swing to the actual statewide result (the county model's rule);
    'nat': baseline = uniform national swing to the actual national result (the House / unpolled-race situation);
    d_hier (nat only): national swing + persistent state effect (rho x last cycle's state residual) + all features.
    Returns per-cycle rows, pooled rows, coefficients."""
    rows, coefs, boots = [], [], {}
    models = [(h, f, by) for by in ("st", None) for h, f in HYP.items()] + [("d_hier", HYP["all"], None)]
    for h, feats, by in models:
        mode = "state" if by else "nat"
        cyc = []
        for t in TESTS:
            years = [c for c, _ in CYCLES if c < t]
            train, d = [F[c] for c in years], F[t]
            fit = Fit(train, feats, "swing", by)
            if fit.n_cycles == 0 or not _has(d, feats):
                continue
            a, rho = predict_swing(F, d, t, fit, h == "d_hier")
            if h == "d_hier" and not np.isfinite(rho):
                continue
            base, pred = _recentre(d["s_p"], d, by), _recentre(d["s_p"] + a, d, by)
            eb, em = 200 * (base - d["s"]).to_numpy(), 200 * (pred - d["s"]).to_numpy()
            sb, sm = _train_sd(train, fit, by, False), _train_sd(train, fit, by, True)
            wv = d["votes"].to_numpy()
            cyc.append((eb, em, wv, d["st"].to_numpy()))
            row = {"hyp": h, "mode": mode, "test": t, "train": "+".join(map(str, years)), "fit_cycles": fit.n_cycles,
                   "counties": len(d), "states": d["st"].nunique(), "rmse_base": _rmse(eb, wv),
                   "rmse_model": _rmse(em, wv),
                   "cov90_base": float(np.average(np.abs(eb) <= Z90 * sb, weights=wv)),
                   "cov90_model": float(np.average(np.abs(em) <= Z90 * sm, weights=wv)), "rho": rho}
            if mode == "nat":
                row["state_rmse_base"] = float(np.sqrt((_state_err(d, base.to_numpy()) ** 2).mean()))
                row["state_rmse_model"] = float(np.sqrt((_state_err(d, pred.to_numpy()) ** 2).mean()))
            rows.append(row)
            for f, b, sx in zip(feats, fit.beta, fit.sd_x):
                coefs.append({"hyp": h, "mode": mode, "test": t, "feature": f, "beta_pts_per_sd": 200 * b * sx})
        if cyc:
            _attach_boot(rows, cyc, boots, (h, mode))
    return pd.DataFrame(rows), _pooled(boots), pd.DataFrame(coefs)


def _pooled(boots: dict) -> pd.DataFrame:
    """Mean ΔRMSE over the test cycles with a 90 % cluster-bootstrap interval; include requires at least two tests,
    improvement in every test, and the interval below zero."""
    out = []
    for (h, mode), (draws, pt, tests) in boots.items():
        lo, hi = np.quantile(draws.mean(axis=1), [0.05, 0.95])
        wins = int((pt < 0).sum())
        out.append({"hyp": h, "mode": mode, "tests": ",".join(map(str, tests)), "d_rmse_mean": pt.mean(),
                    "d_lo": lo, "d_hi": hi, "wins": f"{wins}/{len(pt)}",
                    "include": bool(len(pt) >= 2 and wins == len(pt) and hi < 0)})
    return pd.DataFrame(out)


def turnout_tests(F: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turnout: log(votes_t / votes_p) within state. Baseline: the state's actual ratio for every county ('flat');
    for 2022 also the county's own 2018/2016 drop-off ('own', the v4 rule), features added on top. Error:
    100 x log(pred / actual) (~ % of county votes), vote-weighted RMSE; statewide totals are the actual ones."""
    rows, boots = [], {}
    for h, feats in TURNOUT_HYP.items():
        for base_kind in ("flat", "own"):
            cyc = []
            for t in TESTS:
                if base_kind == "own" and t != 2022:
                    continue
                d = F[t]
                fit = Fit([F[c] for c, _ in CYCLES if c < t], feats, "turnout", "st")
                if fit.n_cycles == 0 or not _has(d, feats):
                    continue
                b = pd.Series(0.0, index=d.index)
                if base_kind == "own":
                    d = d.join(F[2018][["lt"]].rename(columns={"lt": "own"}), how="inner")
                    b = d["own"] - _wmean(d["own"], d["v_p"], d["st"])
                a = fit.adj(d)

                def err(pr: np.ndarray, d: pd.DataFrame = d) -> np.ndarray:
                    lv = pd.Series(np.log(d["v_p"].to_numpy()) + pr, index=d.index)
                    c = np.log(d["votes"].groupby(d["st"]).transform("sum")) - \
                        np.log(np.exp(lv).groupby(d["st"]).transform("sum"))
                    return (100 * (lv + c - np.log(d["votes"]))).to_numpy()
                eb, em = err(b.to_numpy()), err(b.to_numpy() + a)
                wv = d["votes"].to_numpy()
                cyc.append((eb, em, wv, d["st"].to_numpy()))
                rows.append({"hyp": h, "base": base_kind, "test": t, "counties": len(d),
                             "rmse_base": _rmse(eb, wv), "rmse_model": _rmse(em, wv),
                             "mae_base": float(np.average(np.abs(eb), weights=wv)),
                             "mae_model": float(np.average(np.abs(em), weights=wv)),
                             "beta": ";".join(f"{f}={b_:.3f}" for f, b_ in zip(feats, fit.beta))})
            if cyc:
                _attach_boot(rows, cyc, boots, (h, base_kind))
    return pd.DataFrame(rows), _pooled(boots).rename(columns={"mode": "base"})


# ------------------------------------------------------------------ House district backtest and 2026 market gaps

def generic_average(year: int) -> dict:
    """Final 45-day FiveThirtyEight generic-ballot average. One row per pollster/date (largest sample), weights
    sqrt(n) x a 14-day half-life; no pollster above 25 %. Fields were available before Election Day."""
    p = pd.read_csv(RAW / "polls" / "538_raw_polls.csv", low_memory=False)
    p = p[(p["cycle"] == year) & (p["type_simple"] == "House-G-US")].copy()
    p["date"] = pd.to_datetime(p["polldate"])
    election = pd.to_datetime(p["electiondate"]).max()
    p = p[(p["date"] <= election) & (p["date"] >= election - pd.Timedelta(days=45))]
    p["s"] = np.where(p["cand1_party"] == "DEM", p["cand1_pct"], p["cand2_pct"]) / \
        (p["cand1_pct"] + p["cand2_pct"])
    p["n"] = pd.to_numeric(p["samplesize"], errors="coerce")
    p = p.dropna(subset=["s", "n"]).sort_values("n").drop_duplicates(["pollster", "date"], keep="last")
    p["w0"] = np.sqrt(p["n"]) * 0.5 ** ((election - p["date"]).dt.days / 14)
    w = p["w0"] / p["w0"].sum()
    # iterative pollster cap (the same cap as brunder.us.poll_weights)
    for _ in range(20):
        pw = w.groupby(p["pollster"]).transform("sum")
        fac = np.minimum(1, 0.25 / pw)
        w = w * fac
        w /= w.sum()
    return {"avg": float((w * p["s"]).sum()), "n_polls": len(p), "election": election.date().isoformat()}


def _total_mode(d: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """For every keys group: TOTAL-mode rows where present with positive votes, else all non-total modes."""
    d = d.copy()
    d["votes"] = pd.to_numeric(d["votes"], errors="coerce").fillna(0)
    tot = d["mode"].fillna("total").str.lower() == "total"
    has = (tot & (d["votes"] > 0)).groupby([d[k] for k in keys]).transform("any").astype(bool)
    return d[(has & tot) | (~has & ~tot)]


def districts_2018() -> tuple[pd.DataFrame, pd.DataFrame]:
    """2016 presidential two-party share by 2018 House district and county x district weights. Precinct presidential
    votes are allocated to the House district(s) on that precinct's 2016 ballot in proportion to House votes. PA is
    excluded because its map changed in 2018. Returns (district lean, county x district prior-vote weight)."""
    z = zipfile.ZipFile(RAW / "medsl16" / "president.zip")
    cols = ["state_postal", "county_fips", "jurisdiction", "precinct", "candidate", "party", "mode", "votes"]
    p = pd.read_csv(z.open("2016-precinct-president.csv"), dtype=str, usecols=cols, encoding="latin1")
    z = zipfile.ZipFile(RAW / "medsl16" / "house.zip")
    hc = ["state_postal", "county_fips", "jurisdiction", "precinct", "district", "party", "mode", "votes"]
    h = pd.read_csv(z.open("2016-precinct-house.csv"), dtype=str, usecols=hc, encoding="latin1")
    for d in (p, h):
        d["fips"] = _fips(d["county_fips"])
        d["pk"] = d["state_postal"] + "|" + d["fips"] + "|" + d["jurisdiction"].fillna("") + "|" + \
            d["precinct"].fillna("")
    p, h = _total_mode(p, ["pk"]), _total_mode(h, ["pk", "district"])
    p["d"] = p["votes"].where(p["party"].str.lower() == "democratic", 0)
    p["r"] = p["votes"].where(p["party"].str.lower() == "republican", 0)
    pv = p.groupby(["pk", "state_postal", "fips"])[["d", "r"]].sum().reset_index()
    h["votes"] = pd.to_numeric(h["votes"], errors="coerce").fillna(0)
    hw = h.groupby(["pk", "district"])["votes"].sum()
    hw = (hw / hw.groupby(level=0).transform("sum")).rename("w").reset_index()
    a = pv.merge(hw, on="pk")
    a = a[(a["state_postal"] != "PA") & a["district"].str.match(r"^\d+$", na=False)]
    a["district"] = a["district"].astype(int)
    for c in ("d", "r"):
        a[c] *= a["w"]
    x = a.groupby(["state_postal", "district"])[["d", "r"]].sum().reset_index()
    x["id"] = [f"{s}-{'AL' if int(cd) == 0 else f'{int(cd):02d}'}" for s, cd in zip(x["state_postal"], x["district"])]
    x["lean"] = x["d"] / (x["d"] + x["r"])
    cw = a.groupby(["state_postal", "district", "fips"])[["d", "r"]].sum().reset_index()
    cw["w"] = cw["d"] + cw["r"]
    cw["id"] = [f"{s}-{'AL' if int(cd) == 0 else f'{int(cd):02d}'}" for s, cd in zip(cw["state_postal"], cw["district"])]
    return x.set_index("id"), cw[["id", "fips", "w"]]


def outcome_2018() -> pd.DataFrame:
    """MEDSL constituency returns (Clerk of House): 2018 D/R winner and D two-party share. Fusion-line votes count
    for the candidate's major party."""
    h = pd.read_csv(RAW / "house" / "1976-2018-house.csv", encoding="latin1")
    h = h[(h["year"] == 2018) & (h["stage"] == "gen") & ~h["special"]].copy()
    h["v"] = pd.to_numeric(h["candidatevotes"], errors="coerce").fillna(0)
    h["party"] = h["party"].fillna("").str.lower()
    h["id"] = [f"{s}-{'AL' if int(cd) == 0 else f'{int(cd):02d}'}" for s, cd in zip(h["state_po"], h["district"])]
    c = h.groupby(["id", "candidate"]).agg(v=("v", "sum"), party=("party", " ".join)).reset_index()
    d = c[c["party"].str.contains(r"democrat|dfl")].groupby("id")["v"].sum()
    r = c[c["party"].str.contains(r"republican")].groupby("id")["v"].sum()
    x = pd.DataFrame({"d": d, "r": r}).fillna(0)
    x["y"], x["actual"] = (x["d"] > x["r"]).astype(int), x["d"] / (x["d"] + x["r"])
    return x[x["d"] + x["r"] > 0]


LINES_CHANGED_2024 = ("AL", "GA", "LA", "NC", "NY")


def districts_2022() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """2020 presidential share and 2022 House outcome on unchanged 2022/2024 lines, from us_house's public outputs;
    county x district weights use Census block counts (population in the redrawn states)."""
    import us_house
    d = json.loads((HOUSE_SITE / "districts_2024.json").read_text())["districts"]
    rows, out = [], []
    for k, v in d.items():
        if v["st"] in LINES_CHANGED_2024 or v["pres20"]["d2"] is None:
            continue
        rows.append({"id": k, "lean": v["pres20"]["d2"], "st": v["st"]})
        h = v.get("house22")
        if h and h.get("dv") is not None and h.get("rv") is not None and h["dv"] + h["rv"] > 0:
            out.append({"id": k, "d": h["dv"], "r": h["rv"], "y": int(h["dv"] > h["rv"]),
                        "actual": h["dv"] / (h["dv"] + h["rv"])})
    lean, outcome = pd.DataFrame(rows).set_index("id"), pd.DataFrame(out).set_index("id")
    p = pd.read_csv(RAW / "house" / "pieces.csv", dtype={"county": str, "cd119": str})
    p = p[~p["st"].isin(LINES_CHANGED_2024) & p["cd119"].str.match(r"^\d+$")]
    p["id"] = [us_house.did(s, c) for s, c in zip(p["st"], p["cd119"].astype(int))]
    p["w0"] = p["pop"].where(p["pop"].notna(), p["blocks"])
    # block/pop share of a county x previous-president county vote = district aggregation weight
    p["share"] = p["w0"] / p.groupby("county")["w0"].transform("sum")
    v20 = load_panel().query("year == 2020").set_index("fips")["votes"]
    p["w"] = p["share"] * p["county"].map(v20)
    return lean, outcome, p.rename(columns={"county": "fips"})[["id", "fips", "w"]]


def _dist_adj(cw: pd.DataFrame, county: pd.Series) -> pd.Series:
    x = cw.copy()
    x["a"] = x["fips"].map(county)
    x = x.dropna(subset=["a", "w"])
    return (x["a"] * x["w"]).groupby(x["id"]).sum() / x["w"].groupby(x["id"]).sum()


def _calibration(p: pd.Series, y: pd.Series) -> str:
    bins = pd.cut(p, [-0.001, .2, .4, .6, .8, 1.001], labels=False)
    return ";".join(f"{a*.2:.1f}-{(a+1)*.2:.1f}:n{len(g)},p{g['p'].mean():.2f},y{g['y'].mean():.2f}"
                    for a, g in pd.DataFrame({"p": p, "y": y}).groupby(bins, observed=True))


def _brier_ci(d: pd.DataFrame) -> tuple[float, float]:
    """90 % state-cluster bootstrap interval of Brier(model) - Brier(base)."""
    rng, z = np.random.default_rng(106), []
    gs = [g for _, g in d.groupby("st")]
    for _ in range(B_BOOT):
        s = pd.concat([gs[i] for i in rng.integers(0, len(gs), len(gs))])
        z.append(np.mean((s["p_model"] - s["y"]) ** 2 - (s["p_base"] - s["y"]) ** 2))
    return tuple(np.quantile(z, [.05, .95]))


def house_backtest(F: dict[int, pd.DataFrame], P: pd.DataFrame) -> pd.DataFrame:
    """Plain prior presidential lean + final generic ballot vs each OOS county feature adjustment. Probabilities
    use the same uncertainty for plain/model: sd fitted from the plain share residual in 2018, then used for both
    cycles (an outcome-free common scale for the within-cycle Brier comparison)."""
    l18, cw18 = districts_2018()
    o18 = outcome_2018()
    l22, o22, cw22 = districts_2022()
    dat = {2018: (l18, o18, cw18), 2022: (l22, o22, cw22)}
    av = {y: generic_average(y) for y in dat}
    nat = {2016: np.average(F[2016]["s_p"], weights=F[2016]["v_p"]),
           2020: np.average(F[2020]["s_p"], weights=F[2020]["v_p"])}
    plain = {}
    for y, (l, o, _) in dat.items():
        k = l.index.intersection(o.index)
        base = l.loc[k, "lean"] + av[y]["avg"] - nat[2016 if y == 2018 else 2020]
        plain[y] = pd.DataFrame({"share": base, "actual": o.loc[k, "actual"], "y": o.loc[k, "y"]})
        plain[y]["st"] = plain[y].index.str[:2]
    sd = float(np.sqrt(np.mean((plain[2018]["share"] - plain[2018]["actual"]) ** 2)))
    rows = []
    apply = {2018: prediction_frame(P, 2018, 2016), 2022: prediction_frame(P, 2022, 2020)}
    for h, feats in HYP.items():
        for y, (_, _, cw) in dat.items():
            fit = Fit([F[c] for c, _ in CYCLES if c < y], feats, "swing", None)
            app = apply[y]
            if fit.n_cycles == 0 or not _has(app, feats):
                continue
            ca = pd.Series(fit.adj(app), index=app.index)
            adj = _dist_adj(cw, ca)
            d = plain[y].join(adj.rename("adj"), how="inner")
            d["p_base"] = norm.cdf((d["share"] - .5) / sd)
            d["p_model"] = norm.cdf((d["share"] + d["adj"] - .5) / sd)
            db = float(np.mean((d["p_base"] - d["y"]) ** 2))
            dm = float(np.mean((d["p_model"] - d["y"]) ** 2))
            lo, hi = _brier_ci(d)
            rows.append({"hyp": h, "test": y, "districts": len(d), "generic_d2": av[y]["avg"],
                         "generic_n": av[y]["n_polls"], "sd_share": sd, "brier_plain": db, "brier_model": dm,
                         "d_brier": dm - db, "d_lo": lo, "d_hi": hi,
                         "cal_plain": _calibration(d["p_base"], d["y"]),
                         "cal_model": _calibration(d["p_model"], d["y"])})
    return pd.DataFrame(rows)


def market_gaps() -> pd.DataFrame:
    """2026 plain model-only district baseline vs the mean of available PM/Kalshi prices (no feature won OOS)."""
    m = json.loads((HOUSE_SITE / "markets.json").read_text())["districts"]
    rows = []
    for k, v in m.items():
        ps = [(v.get(s) or {}).get("p") for s in ("pm", "kalshi")]
        ps = [x for x in ps if x is not None]
        b = (v.get("baseline") or {}).get("p_d")
        if ps and b is not None:
            p = float(np.mean(ps))
            rows.append({"district": k, "model_p_d": b, "market_p_d": p, "gap": b - p,
                         "pm": (v.get("pm") or {}).get("p"), "kalshi": (v.get("kalshi") or {}).get("p"),
                         "model_only": True})
    return pd.DataFrame(rows).sort_values("gap", key=abs, ascending=False)


def house() -> dict[str, pd.DataFrame]:
    P = load_panel()
    F = frames(P)
    b, g = house_backtest(F, P), market_gaps()
    PROC.mkdir(parents=True, exist_ok=True)
    b.round(5).to_csv(PROC / "house_backtest.csv", index=False)
    g.round(5).to_csv(PROC / "market_gaps.csv", index=False)
    print("\n== House backtest\n", b.round(4).to_string(index=False))
    print("\n== 2026 market gaps\n", g.head(20).round(3).to_string(index=False))
    return {"house_backtest": b, "market_gaps": g}


def tests() -> dict[str, pd.DataFrame]:
    P = load_panel()
    F = frames(P)
    sw, sw_pool, coefs = swing_tests(F)
    tu, tu_pool = turnout_tests(F)
    PROC.mkdir(parents=True, exist_ok=True)
    out = {"swing": sw, "swing_pooled": sw_pool, "coefs": coefs, "turnout": tu, "turnout_pooled": tu_pool}
    for k, v in out.items():
        v.round(4).to_csv(PROC / f"{k}.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_rows", 200):
        for k, v in out.items():
            print(f"\n== {k}\n{v.round(3).to_string(index=False)}")
    return out


STEPS = {"fetch": fetch, "panel": panel, "tests": tests, "house": house}

if __name__ == "__main__":
    for step in sys.argv[1:] or list(STEPS):
        STEPS[step]()
