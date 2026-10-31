"""Unified pre-count model: county x even-election-year demographics from historical ACS 5-year vintages.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_demo.py [fetch] [build]

fetch  downloads (cached under $BRUNDER_DATA/raw/us/census/acs_hist, polite, one request at a time).
build  writes $BRUNDER_DATA/processed/us2026/unified/county_demo.parquet (+ .csv), county_demo_counts.parquet, README_UniDemo.md.

Vintage rule (no look-ahead): election year t uses the ACS 5-year estimate ENDING t-2 (published in December of t-1;
2012 -> 2006-2010 ... 2026 -> 2020-2024). Sources, all Census Bureau (public domain, 17 U.S.C. 105), keyless bulk files:
  ending 2010-2020: summary_file/<yr>/data/5_year_seq_by_state/<State>/All_Geographies_Not_Tracts_Block_Groups/
  ending 2022, 2024: summary_file/<yr>/table-based-SF/data/5YRData/acsdt5y<yr>-<table>.dat
  land area: Census Gazetteer counties 2023 (CT's pre-2022 counties: 2020 Gazetteer).
The Census API (api.census.gov) redirects to missing_key.html without a key and is NOT used.
"""
from __future__ import annotations

import io
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us" / "census"
HIST = RAW / "acs_hist"
U = DATA / "processed" / "us2026" / "unified"
ROOT = Path(__file__).resolve().parents[1]
SF = "https://www2.census.gov/programs-surveys/acs/summary_file"
ELECTION_YEARS = list(range(2012, 2028, 2))
LEGACY_ENDS = [2010, 2012, 2014, 2016, 2018, 2020]
TABLE_ENDS = [2022, 2024]
FIPS_MERGED = {"51515": "51019", "46113": "46102", "02270": "02158"}    # same as analysis/us_fundamentals.py
TABLES = ["b01001", "b02001", "b03002", "b03003", "b05002", "b15003", "b19013"]
STATES = ("Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware DistrictOfColumbia Florida Georgia "
          "Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota "
          "Mississippi Missouri Montana Nebraska Nevada NewHampshire NewJersey NewMexico NewYork NorthCarolina "
          "NorthDakota Ohio Oklahoma Oregon Pennsylvania RhodeIsland SouthCarolina SouthDakota Tennessee Texas Utah "
          "Vermont Virginia Washington WestVirginia Wisconsin Wyoming").split()
USPS = dict(zip(STATES, ("al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm "
                         "ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy").split()))
# cell lines (1-based line numbers within each table)
AGE65 = [20, 21, 22, 23, 24, 25, 44, 45, 46, 47, 48, 49]
AGE1829 = [7, 8, 9, 10, 11, 31, 32, 33, 34, 35]
BA_B15003 = [22, 23, 24, 25]
BA_B15002 = [15, 16, 17, 18, 32, 33, 34, 35]          # ACS 2006-2010 has no B15003; B15002 bachelor's+ rows


def get(url: str, dest: Path, tries: int = 6) -> bool:
    """Cached polite download; False if the file does not exist (404)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "brunder-research (public-domain census data)"})
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            if dest.suffix == ".zip" and body[:2] != b"PK":
                raise ValueError("not a zip")
            dest.write_bytes(body)
            time.sleep(0.15)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            time.sleep(3 * (k + 1))
        except Exception:
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"download failed: {url}")


# ------------------------------------------------------------------ sequence lookup (legacy vintages)

def lookup(end: int) -> dict[str, tuple[int, int]]:
    """table -> (sequence number, 1-based start position of cell 1), first occurrence."""
    if end <= 2012:
        url = f"{SF}/{end}/documentation/5_year/user_tools/Sequence_Number_and_Table_Number_Lookup.txt"
    else:
        url = f"{SF}/{end}/documentation/user_tools/ACS_5yr_Seq_Table_Number_Lookup.txt"
    dest = HIST / f"lookup{end}.txt"
    get(url, dest)
    lk = pd.read_csv(dest, dtype=str, encoding="latin1").fillna("")
    lk = lk[(lk["Start Position"].str.strip() != "") & lk["Table ID"].isin([t.upper() for t in TABLES + ["b15002"]])]
    out: dict[str, tuple[int, int]] = {}
    for _, r in lk.iterrows():
        out.setdefault(r["Table ID"], (int(r["Sequence Number"]), int(r["Start Position"])))
    return out


def legacy_files(end: int) -> list[tuple[str, Path]]:
    lk = lookup(end)
    seqs = sorted({s for t, (s, _) in lk.items()})
    jobs = []
    for state, st in USPS.items():
        base = f"{SF}/{end}/data/5_year_seq_by_state/{state}/All_Geographies_Not_Tracts_Block_Groups"
        jobs.append((f"{base}/g{end}5{st}.csv", HIST / str(end) / f"g{end}5{st}.csv"))
        for s in seqs:
            f = f"{end}5{st}{s:04d}000.zip"
            jobs.append((f"{base}/{f}", HIST / str(end) / f))
    return jobs


def fetch() -> None:
    for end in LEGACY_ENDS:
        miss = []
        for u, d in legacy_files(end):
            try:
                ok = get(u, d)
            except RuntimeError:                      # WAF "Request Rejected" on this file: use the tract-level file
                tu = u.replace("All_Geographies_Not_Tracts_Block_Groups", "Tracts_Block_Groups_Only")
                ok = get(tu, d.with_name("tr_" + d.name)) and get(
                    tu.rsplit("/", 1)[0] + "/g" + d.name.split("5")[0] + "5" + d.name.split("5")[1][:2] + ".csv",
                    d.with_name(f"tr_g{end}5{d.name.split('5')[1][:2]}.csv"))
                print("tract fallback:", d.name, ok)
            if not ok:
                miss.append(u)
        print(end, "missing files:", len(miss), miss[:3])
    for end in TABLE_ENDS:
        for t in TABLES:
            ok = get(f"{SF}/{end}/table-based-SF/data/5YRData/acsdt5y{end}-{t}.dat", HIST / f"acsdt5y{end}-{t}.dat")
            print(end, t, "ok" if ok else "MISSING")
    gz = RAW / "2023_Gaz_counties_national.txt"
    get("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip",
        RAW / "gaz_counties_2023.zip")
    get("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_counties_national.zip",
        RAW / "gaz_counties_2020.zip")
    print("gazetteer", gz.exists())


# ------------------------------------------------------------------ extraction -> one counts frame per vintage

def _cells(table: str, lines: list[int], df: pd.DataFrame, start: int) -> pd.Series:
    """Sum of the given 1-based table lines from a sequence frame (columns positional)."""
    idx = [start - 1 + (ln - 1) for ln in lines]
    return df.iloc[:, idx].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)


def _geo(path: Path, levels: tuple[str, ...]) -> pd.Series:
    """LOGRECNO -> county fips ('S'+state fips for state rows) from a by-state geography csv."""
    g = pd.read_csv(path, header=None, dtype=str, encoding="latin1", usecols=[2, 3, 4, 9, 10])
    g.columns = ["sumlev", "comp", "logrec", "state", "county"]
    g = g[(g["comp"] == "00") & g["sumlev"].isin(levels)]
    g["fips"] = np.where(g["sumlev"] == "040", "S" + g["state"], g["state"] + g["county"])
    return g.set_index("logrec")["fips"]


def _read_seq(zf: Path, end: int) -> pd.DataFrame:
    with zipfile.ZipFile(zf) as z:
        name = [n for n in z.namelist() if n.lower().startswith("e")][0]
        return pd.read_csv(z.open(name), header=None, dtype=str, encoding="latin1", index_col=5)


def read_legacy(end: int) -> pd.DataFrame:
    """County (and state, key 'S'+fips) counts for an ACS 5-year ending `end`, from the by-state sequence files."""
    lk = lookup(end)
    use = {"B01001": [[1], AGE65, AGE1829], "B02001": [[1], [3]], "B03002": [[1], [3]], "B03003": [[1], [3]],
           "B05002": [[1], [13]], "B19013": [[1]]}
    edu = "B15003" if "B15003" in lk else "B15002"
    use[edu] = [[1], BA_B15003 if edu == "B15003" else BA_B15002]
    # B15002 total is line 1
    rows = []
    for state, st in USPS.items():
        d = HIST / str(end)
        g = _geo(d / f"g{end}5{st}.csv", ("040", "050"))
        gt = None
        cols: dict[str, pd.Series] = {}
        seq_cache: dict[int, pd.DataFrame] = {}
        for t, groups in use.items():
            seq, start = lk[t]
            if seq not in seq_cache:
                zf = d / f"{end}5{st}{seq:04d}000.zip"
                if zf.exists():
                    df, gg, agg = _read_seq(zf, end), g, False
                else:                                  # all-geography file rejected by the Census WAF: sum tracts
                    if gt is None:
                        gt = _geo(d / f"tr_g{end}5{st}.csv", ("140",))
                    df, gg, agg = _read_seq(d / f"tr_{zf.name}", end), gt, True
                df = df.loc[df.index.intersection(gg.index)].apply(pd.to_numeric, errors="coerce")
                df.index = gg.loc[df.index].values
                seq_cache[seq] = df.groupby(level=0).sum(min_count=1) if agg else df
            df = seq_cache[seq]
            # positional columns after dropping LOGRECNO (col 5): original position p>5 -> p-1
            sh = lambda lines: [start - 1 + (ln - 1) - 1 for ln in lines]
            names = {"B01001": ["pop", "a65", "a1829"], "B02001": ["race_tot", "black"], "B03002": ["nh_tot", "white_nh"],
                     "B03003": ["hisp_tot", "hisp"], "B05002": ["nat_tot", "foreign"], "B19013": ["medinc"],
                     "B15003": ["edu_tot", "ba"], "B15002": ["edu_tot", "ba"]}[t]
            for nm, lines in zip(names, groups):
                cols[nm] = df.iloc[:, sh(lines)].sum(axis=1, min_count=1)
        c = pd.DataFrame(cols)
        rows.append(c)
    out = pd.concat(rows)
    out["edu_table"] = edu
    return out


def read_table_based(end: int) -> pd.DataFrame:
    def cols(t: str, lines: dict[str, list[int]]) -> pd.DataFrame:
        T = t.upper()
        need = sorted({l for v in lines.values() for l in v})
        use = ["GEO_ID"] + [f"{T}_E{l:03d}" for l in need]
        parts = []
        for ch in pd.read_csv(HIST / f"acsdt5y{end}-{t}.dat", sep="|", dtype=str, usecols=use, chunksize=500_000):
            parts.append(ch[ch["GEO_ID"].str.startswith(("0500000US", "0400000US"))])
        d = pd.concat(parts)
        key = np.where(d["GEO_ID"].str.startswith("0500000US"), d["GEO_ID"].str[-5:], "S" + d["GEO_ID"].str[-2:])
        d = d.drop(columns="GEO_ID").apply(pd.to_numeric, errors="coerce")
        d.index = key
        out = pd.DataFrame(index=d.index)
        for nm, ls in lines.items():
            out[nm] = d[[f"{T}_E{l:03d}" for l in ls]].sum(axis=1, min_count=1)
        return out
    parts = [cols("b01001", {"pop": [1], "a65": AGE65, "a1829": AGE1829}),
             cols("b02001", {"race_tot": [1], "black": [3]}),
             cols("b03002", {"nh_tot": [1], "white_nh": [3]}),
             cols("b03003", {"hisp_tot": [1], "hisp": [3]}),
             cols("b05002", {"nat_tot": [1], "foreign": [13]}),
             cols("b15003", {"edu_tot": [1], "ba": BA_B15003}),
             cols("b19013", {"medinc": [1]})]
    out = pd.concat(parts, axis=1)
    out["edu_table"] = "B15003"
    return out


def merge_fips(c: pd.DataFrame) -> pd.DataFrame:
    """Apply FIPS_MERGED; counts add, median income is population-weighted."""
    c = c.copy()
    keys = pd.Series(c.index, index=c.index).replace(FIPS_MERGED)
    changed = keys != pd.Series(c.index, index=c.index)
    if not changed.any():
        c["note"] = ""
        return c
    num = [x for x in c.columns if x not in ("medinc", "edu_table")]
    c["_w"] = c["pop"]
    c["_mw"] = c["medinc"] * c["pop"]
    g = c.groupby(keys.values)
    out = g[num].sum(min_count=1)
    out["medinc"] = g["_mw"].sum(min_count=1) / g["_w"].sum(min_count=1)
    out["edu_table"] = g["edu_table"].first()
    note = pd.Series("", index=out.index)
    for old, new in FIPS_MERGED.items():
        if old in c.index:
            note[new] = f"{old} merged/renamed into {new}" + ("; medinc pop-weighted" if new in c.index else "")
    out["note"] = note
    out.loc[c.index[changed].map(FIPS_MERGED).unique(), "medinc"] = out["medinc"]
    return out.drop(columns=[x for x in out.columns if x.startswith("_")], errors="ignore")


def vintage_counts(end: int) -> pd.DataFrame | None:
    try:
        raw = read_legacy(end) if end in LEGACY_ENDS else read_table_based(end)
    except Exception as e:                                # unobtainable: build() falls back to an earlier vintage
        print(f"vintage {end} unobtainable: {type(e).__name__}: {e}")
        return None
    raw["medinc"] = raw["medinc"].where(raw["medinc"] > 0)          # Census null codes (-666666666 ...) -> NaN
    st = raw[raw.index.str.startswith("S") & (raw.index.str[1:].astype(int) <= 56)]
    cty = merge_fips(raw[~raw.index.str.startswith("S")])
    cty = cty[cty.index.str[:2].astype(int) <= 56]         # 50 states + DC (table-based files also carry Puerto Rico)
    cty.attrs["states"] = st
    return cty


# ------------------------------------------------------------------ build

def gazetteer() -> pd.DataFrame:
    a = pd.read_csv(RAW / "2023_Gaz_counties_national.txt", sep="\t", dtype=str, encoding="latin1")
    a.columns = a.columns.str.strip()
    a = a.set_index("GEOID")
    a["ALAND_SQMI"] = pd.to_numeric(a["ALAND_SQMI"])
    land = a["ALAND_SQMI"].copy()
    st = a["USPS"].str.lower().copy()
    z = RAW / "gaz_counties_2020.zip"
    if z.exists():
        with zipfile.ZipFile(z) as zz:
            b = pd.read_csv(zz.open(zz.namelist()[0]), sep="\t", dtype=str, encoding="latin1")
        b.columns = b.columns.str.strip()
        b = b.set_index("GEOID")
        b["ALAND_SQMI"] = pd.to_numeric(b["ALAND_SQMI"])
        extra = b.index.difference(land.index)
        land = pd.concat([land, b.loc[extra, "ALAND_SQMI"]])
        st = pd.concat([st, b.loc[extra, "USPS"].str.lower()])
    land = land.groupby(level=0).first()
    return pd.DataFrame({"land_sqmi": land, "st": st.groupby(level=0).first()})


def build() -> None:
    U.mkdir(parents=True, exist_ok=True)
    ends = LEGACY_ENDS + TABLE_ENDS
    vint = {e: vintage_counts(e) for e in ends}
    gaz = gazetteer()
    fips_state = {f[:2]: s for f, s in gaz["st"].items()}
    cov = []
    frames, counts = [], []
    for t in ELECTION_YEARS:
        want = t - 2
        use = max(e for e in ends if e <= want and vint[e] is not None) if want >= min(ends) else None
        if use is None:
            raise SystemExit(f"no vintage available for {t}")
        c = vint[use].copy()
        sd = vint[use].attrs["states"]
        c.attrs = {}
        c["year"], c["vintage_end"] = t, use
        c["fips"] = c.index
        counts.append(c.reset_index(drop=True))
        pos = lambda n, d: n / d.where(d > 0)
        d = pd.DataFrame({
            "fips": c["fips"].values, "year": t, "vintage_end": use,
            "ba": pos(c["ba"], c["edu_tot"]).values, "hisp": pos(c["hisp"], c["hisp_tot"]).values,
            "black": pos(c["black"], c["race_tot"]).values, "white_nh": pos(c["white_nh"], c["nh_tot"]).values,
            "age65": pos(c["a65"], c["pop"]).values, "age18_29": pos(c["a1829"], c["pop"]).values,
            "foreign_born": pos(c["foreign"], c["nat_tot"]).values, "medinc": c["medinc"].values,
            "pop_total": c["pop"].values, "note": c["note"].values})
        frames.append(d)
        # validation: county counts summed per state vs the state's own ACS row
        s = c.assign(sf="S" + c["fips"].str[:2]).groupby("sf")[["ba", "edu_tot", "hisp", "hisp_tot", "pop"]].sum()
        s = s.join(sd[["ba", "edu_tot", "hisp", "hisp_tot", "pop"]], lsuffix="", rsuffix="_st")
        cov.append({"year": t, "vintage_end": use, "rows": len(c), "states_check_max_abs_ba": float(
            (s["ba"] / s["edu_tot"] - s["ba_st"] / s["edu_tot_st"]).abs().max()),
            "states_check_max_abs_hisp": float((s["hisp"] / s["hisp_tot"] - s["hisp_st"] / s["hisp_tot_st"]).abs().max()),
            "us_ba": float(s["ba"].sum() / s["edu_tot"].sum()), "us_hisp": float(s["hisp"].sum() / s["hisp_tot"].sum()),
            "us_foreign": float(c["foreign"].sum() / c["nat_tot"].sum()), "pop_sum": float(c["pop"].sum())})
    D = pd.concat(frames, ignore_index=True)
    D = D.merge(gaz[["land_sqmi"]], left_on="fips", right_index=True, how="left")
    D["st"] = D["fips"].str[:2].map(fips_state)
    D["density"] = D["pop_total"] / D["land_sqmi"]
    D["lndens"] = np.log(D["density"].where(D["density"] > 0))
    D["lninc"] = np.log(D["medinc"].where(D["medinc"] > 0))
    # PEP population columns from the shared county panel (AK and CT absent there)
    P = pd.read_csv(ROOT / "data_public" / "us_county_panel.csv", comment="#", dtype={"fips": str, "st": str})
    P = P[["fips", "year", "pop", "pop_g", "ndm", "imig"]]
    D = D.merge(P, on=["fips", "year"], how="left")
    D["pop_chg4"] = np.nan
    pk = P.set_index(["fips", "year"])["pop"]
    prev = pd.Series(list(zip(D["fips"], D["year"] - 4)))
    D["pop_chg4"] = (D["pop"].values / pk.reindex(pd.MultiIndex.from_tuples(prev)).values - 1) * 100
    # changes vs t-4 (previous presidential-cycle row, d_*) and vs t-2 (d2_*)
    base = ["ba", "hisp", "age65", "age18_29", "black", "white_nh", "lndens"]
    idx = D.set_index(["fips", "year"])
    for lag, pre in ((4, "d_"), (2, "d2_")):
        key = pd.MultiIndex.from_arrays([D["fips"], D["year"] - lag])
        for b in base:
            D[f"{pre}{b}"] = D[b].values - idx[b].reindex(key).values
    cols = ["fips", "st", "year", "vintage_end", "ba", "hisp", "black", "white_nh", "age65", "age18_29", "foreign_born",
            "medinc", "lninc", "pop_total", "land_sqmi", "density", "lndens"] + \
           [f"d_{b}" for b in base] + [f"d2_{b}" for b in base] + ["pop", "pop_g", "pop_chg4", "ndm", "imig", "note"]
    D = D[cols].sort_values(["year", "fips"]).reset_index(drop=True)
    D.to_parquet(U / "county_demo.parquet", index=False)
    D.to_csv(U / "county_demo.csv", index=False, float_format="%.6g")
    pd.concat(counts, ignore_index=True).to_parquet(U / "county_demo_counts.parquet", index=False)
    write_readme(D, pd.DataFrame(cov), vint)
    print(D.groupby("year").size())


def write_readme(D: pd.DataFrame, cov: pd.DataFrame, vint: dict) -> None:
    per = []
    ref = set(vint[2024].index) if vint.get(2024) is not None else set()
    for t, g in D.groupby("year"):
        v = int(g["vintage_end"].iloc[0])
        miss = sorted(ref - set(g["fips"]))
        per.append(f"| {t} | {v - 4}-{v} (ending {v}) | {len(g)} | {(g['ba'].notna()).sum()} | "
                   f"{g['medinc'].isna().sum()} | {g['land_sqmi'].isna().sum()} | {g['pop'].isna().sum()} | "
                   f"{(g['pop_total'] / g['pop']).median():.3f} | {len(miss)} {', '.join(miss[:6])} |")
    cv = "\n".join(f"| {r.year} | {r.vintage_end} | {r.rows} | {r.us_ba:.4f} | {r.us_hisp:.4f} | {r.us_foreign:.4f} | "
                   f"{r.states_check_max_abs_ba:.2e} | {r.states_check_max_abs_hisp:.2e} | {r.pop_sum:,.0f} |"
                   for r in cov.itertuples())
    txt = f"""# County demographics by election-year ACS vintage (UniDemo)

File: `county_demo.parquet` (+ `county_demo.csv`), one row per county x even election year 2012-2026 ({len(D)} rows, {D.shape[1]} columns).
`county_demo_counts.parquet`: the underlying ACS counts (pop, a65, a1829, black, hisp, ...). Code: `analysis/us_unified_demo.py`.

## Vintage rule (no look-ahead)
Election year t uses the ACS 5-year estimate ENDING t-2 (published December t-1): 2012 -> 2006-2010, 2014 -> 2008-2012,
2016 -> 2010-2014, 2018 -> 2012-2016, 2020 -> 2014-2018, 2022 -> 2016-2020, 2024 -> 2018-2022, 2026 -> 2020-2024. Column
`vintage_end` shows the vintage actually used (always t-2 in this build unless a row of the table below says otherwise).
Differences vs t-4 / t-2 therefore compare non-adjacent 5-year windows (they overlap by at most one survey year for t-4).

## Columns
fips (5-digit string, FIPS_MERGED of us_fundamentals: 51515->51019 Bedford city, 46113->46102 Shannon->Oglala Lakota,
02270->02158 Wade Hampton->Kusilvak; counts summed, medinc population-weighted), st (lowercase postal), year, vintage_end.
Shares (0-1): ba = bachelor's+ of 25+ (B15003 lines 22-25 / 1; the 2006-2010 vintage has no B15003, B15002 bachelor's/master's/
professional/doctorate rows of both sexes / total used), hisp = B03003 Hispanic / total, black = B02001 Black alone / total,
white_nh = B03002 white alone not Hispanic / total, age65 = B01001 65+ / total, age18_29 = B01001 ages 18-29 / total
(18-19, 20, 21, 22-24, 25-29 both sexes), foreign_born = B05002 foreign born / total, medinc = B19013 (dollars of the
vintage's own final year, NOT inflation-adjusted across vintages; lninc = ln), pop_total = ACS B01001 total.
land_sqmi = Census Gazetteer 2023 (2020 Gazetteer for Connecticut's pre-2022 counties, absent from 2023) - land area is
static; density = pop_total/land_sqmi; lndens = ln density.
d_<x> = x(t) - x(t-4) (previous presidential-cycle row); d2_<x> = x(t) - x(t-2) for x in ba, hisp, age65, age18_29, black,
white_nh, lndens. NaN where the earlier row does not exist (t-4 for 2012, 2014; t-2 for 2012) - not filled.
pop, pop_g, ndm, imig copied from data_public/us_county_panel.csv (Census PEP, vintage known before that election; see
docs/us_fundamentals.md); pop_chg4 = % change of PEP pop vs the panel row 4 years earlier. These are NaN for Alaska and
Connecticut (excluded from that panel). Rows for AK and CT ARE kept here (CT: old 9 counties through the 2018-2022 vintage,
the 2020-2024 vintage (election 2026) uses the 9 planning regions 09110-09190, so CT d_ columns and pop are not comparable
across 2024->2026 and CT fips differ; downstream should exclude CT/AK as us_fundamentals does (EXCL_ST = 02, 09).

## Coverage per election year
`rows` = counties; missing = counties of the 2020-2024 vintage absent from that year (CT region/county changes, boundary changes).
| year | ACS window | rows | ba non-null | medinc null | land_sqmi null | PEP pop null | median ACS pop / PEP pop | counties missing vs 2026 vintage |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(per)}

## Validation
County counts summed within state vs the state's own ACS row (max abs difference of the share across states) and national shares
(sum of counties):
| year | vintage end | rows | US ba | US hisp | US foreign | max |dba| | max |dhisp| | pop sum |
|---|---|---|---|---|---|---|---|---|
{cv}

## Gaps and special handling
- Median income is null where the Census reports a null code (rows with `medinc` NaN); lninc NaN there. Not filled.
- NH, vintage ending 2020 (election 2022), table B02001 (Black alone): the Census file server returned "Request Rejected" for the
  all-geographies file `20205nh0004000.zip` on every request; the tract-level file of the same sequence was used and tracts summed to
  counties (exact additive counts; NH state check unaffected). Nothing was bypassed.
- Alaska: ACS 2006-2010 .. 2016-2020 use the pre-2019 census areas (02261 Valdez-Cordova has no land area in the 2023 Gazetteer -> land_sqmi NaN
  2012-2022); 2022-2024 vintages use the new areas (02063, 02066).
- 2020 election and 2022 election rows differ by vintage (ending 2018 vs 2020); PEP columns are NaN for AK and CT.
- Land area is a single (2023/2020 Gazetteer) value, so d_lndens only reflects population change, not boundary change.
- Puerto Rico and the territories are excluded.

## Sources and licence
U.S. Census Bureau ACS 5-year summary files, keyless bulk files under https://www2.census.gov/programs-surveys/acs/summary_file/
(by-state sequence files for vintages ending 2010-2020, table-based files for 2022 and 2024) and the Census Gazetteer
(https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html). U.S. Government works: public domain.
The Census API requires a key (redirects to missing_key.html) and was not used.
"""
    (U / "README_UniDemo.md").write_text(txt)


if __name__ == "__main__":
    for cmd in (sys.argv[1:] or ["fetch", "build"]):
        {"fetch": fetch, "build": build}[cmd]()
