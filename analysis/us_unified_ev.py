"""Unified pre-count model, slice "EV history": county x cycle early-vote history aligned in days-before-Election-Day.

Subcommands (run from the worktree: BRUNDER_DATA=~/brunder-data-local uv run --with openpyxl python analysis/us_unified_ev.py <cmd>)
  fetch   polite single requests for the Texas SoS cumulative early-voting day pages (2016, 2018 generals); cached
          under $BRUNDER_DATA/raw/us/ev_hist/tx. Everything else is read from files already on disk.
  nc      per-ballot NC absentee files -> daily county x kind x party x age x sex x race counts (cached parquet)
  build   assemble U/ev_hist_county.parquet, U/ev_hist_state.parquet, U/ev_hist_registration.parquet,
          U/ev_pace_diagnostic.csv (+ U/README_UniEV.md is hand-maintained from the diagnostics printed here)
  all     fetch + nc + build

Conventions (see README_UniEV.md): ev_* are CUMULATIVE ballots returned/accepted/cast by the end of the day that is
`days_before` days before Election Day (0 = Election Day itself, -1 = final canvass counts that postdate Election Day),
`kind` in {inperson, mail, total}; D/R = voter's registered party, O = everything else (incl. unaffiliated).
No look-ahead: a row only contains information dated `asof_date` <= Election Day - days_before (final-canvass rows,
is_final=True, are labelled and are targets/denominators, never features).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us"
EVP = RAW / "ev_party"
HIST = RAW / "ev_hist"
U = DATA / "processed" / "us2026" / "unified"
REPO = Path(__file__).resolve().parents[1]
YEARS = (2016, 2018, 2020, 2022, 2024)
ED = {2016: "2016-11-08", 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
ED = {k: pd.Timestamp(v) for k, v in ED.items()}
MAXD = 45
AGE_COLS = ["ev_age_18_29", "ev_age_30_44", "ev_age_45_64", "ev_age_65p", "ev_age_18_44"]
SEX_COLS = ["ev_sex_f", "ev_sex_m", "ev_sex_u"]
RACE_COLS = ["ev_race_white", "ev_race_black", "ev_race_asian", "ev_race_other"]
OUT_COLS = (["st", "fips", "year", "days_before", "kind", "asof_date", "is_final", "ev_total", "ev_D", "ev_R", "ev_O"]
            + AGE_COLS + SEX_COLS + RACE_COLS + ["source", "note"])
UA = "Mozilla/5.0 (research; brunder)"
ST_FIPS = {"37": "nc", "48": "tx", "12": "fl", "08": "co", "24": "md", "42": "pa", "40": "ok"}


# --------------------------------------------------------------------------------------------- county name -> FIPS
def _norm_name(s: str) -> str:
    s = str(s).upper().strip()
    s = re.sub(r"\s+COUNTY$", "", s)
    s = re.sub(r"^SAINT\b", "ST", s)
    return re.sub(r"[^A-Z]", "", s)


def county_fips() -> dict[tuple[str, str], str]:
    """(state fips, normalised county name) -> 5-digit FIPS, from the Census 2023 Gazetteer (public domain)."""
    g = pd.read_csv(RAW / "census" / "2023_Gaz_counties_national.txt", sep="\t", dtype=str)
    g.columns = [c.strip() for c in g.columns]
    return {(r.GEOID[:2], _norm_name(r.NAME)): r.GEOID for r in g.itertuples()}


_CF: dict | None = None


def fips_of(state_fips: str, name: str) -> str | None:
    global _CF
    if _CF is None:
        _CF = county_fips()
        # name variants used by state reports
        for k, v in {("24", "BALTIMORECITY"): "24510", ("24", "BALTIMORE"): "24005", ("24", "STMARYS"): "24037",
                     ("24", "STMARYSCOUNTY"): "24037", ("24", "QUEENANNES"): "24035", ("24", "PRINCEGEORGES"): "24033"}.items():
            _CF[k] = v
        _CF[("48", "DEWITT")] = "48123"
        _CF[("08", "BROOMFIELD")] = "08014"
        _CF[("24", "SOMERSET")] = "24039"
        _CF[("42", "LACKWANNA")] = "42069"      # sic, in the 2016-2020 PA DOS tables
    return _CF.get((state_fips, _norm_name(name)))


def _age_bucket_expr(col: str) -> str:
    return (f"case when {col} is null or {col}<18 then 'na' when {col}<30 then 'a1829' when {col}<45 then 'a3044' "
            f"when {col}<65 then 'a4564' else 'a65p' end")


# --------------------------------------------------------------------------------------------------------- fetch
def fetch() -> None:
    """Texas SoS 'Early Voting Cumulative Totals Thru Close of Business' pages (2016/2018 generals; 15/30 largest counties)."""
    d = HIST / "tx"
    d.mkdir(parents=True, exist_ok=True)
    for y in (2016, 2018):
        idx = d / f"idx{y}.html"
        if not idx.exists():
            subprocess.run(["curl", "-s", "-L", "-A", UA, f"https://www.sos.state.tx.us/elections/earlyvoting/{y}/index.shtml",
                            "-o", str(idx)], check=True)
        links = re.findall(r'href="((?:oct|nov)\d+\.shtml)"', idx.read_text(errors="ignore"))
        for l in links:
            fn = d / f"pg{y}_{l[:-6]}.html"
            if fn.exists():
                continue
            subprocess.run(["curl", "-s", "-L", "-A", UA, f"https://www.sos.state.tx.us/elections/earlyvoting/{y}/{l}",
                            "-o", str(fn)], check=True)
            time.sleep(0.5)


# ------------------------------------------------------------------------------------------------------------ NC
def nc_daily(year: int, force: bool = False) -> pd.DataFrame:
    """Accepted NC absentee/one-stop ballots by county, return date, kind, party, age bucket, sex, race (per-ballot
    NCSBE files, dl.ncsbe.gov, public record). Return date = accepted date for mail, vote date for one-stop.
    Parsed with the csv module (duckdb's parser drops ~0.8% of rows of these files with stray quote/backslash bytes)."""
    import csv
    from collections import Counter
    out = HIST / f"nc_daily_{year}.parquet"
    if out.exists() and not force:
        return pd.read_parquet(out)
    HIST.mkdir(parents=True, exist_ok=True)
    ed = ED[year].strftime("%Y%m%d")
    cnt: Counter = Counter()
    with zipfile.ZipFile(EVP / "nc" / f"absentee_{ed}.zip") as z, z.open(z.namelist()[0]) as raw:
        rd = csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline=""))
        h = next(rd)
        ix = {c: h.index(c) for c in ("county_desc", "ballot_rtn_dt", "ballot_req_type", "voter_party_code", "age", "gender",
                                       "race", "ballot_rtn_status")}
        for r in rd:
            if len(r) != len(h) or r[ix["ballot_rtn_status"]].strip().upper() != "ACCEPTED":
                continue
            dt = r[ix["ballot_rtn_dt"]].strip()
            if not re.fullmatch(r"\d\d/\d\d/\d{4}", dt):
                continue
            kind = "inperson" if r[ix["ballot_req_type"]].strip().upper() in ("ONE-STOP", "EARLY VOTING") else "mail"
            pc = r[ix["voter_party_code"]].strip().upper()
            party = "D" if pc == "DEM" else "R" if pc == "REP" else "O"
            try:
                a = int(r[ix["age"]])
            except ValueError:
                a = -1
            ageb = "na" if a < 18 else "a1829" if a < 30 else "a3044" if a < 45 else "a4564" if a < 65 else "a65p"
            g = r[ix["gender"]].strip().upper()
            sex = "f" if g == "F" else "m" if g == "M" else "u"
            rc = r[ix["race"]].strip().upper()
            race = "white" if rc == "WHITE" else "black" if rc.startswith("BLACK") else "asian" if rc == "ASIAN" else "other"
            cnt[(r[ix["county_desc"]].strip().upper(), dt, kind, party, ageb, sex, race)] += 1
    df = pd.DataFrame([(*k, n) for k, n in cnt.items()], columns=["county", "rtn", "kind", "party", "ageb", "sex", "race", "n"])
    df["rtn"] = pd.to_datetime(df["rtn"], format="%m/%d/%Y")
    df.to_parquet(out)
    return df


def nc_validate(year: int, df: pd.DataFrame) -> dict:
    """Compare with NCSBE's own aggregate (agg_absentee_*.csv, built by the earlier ev_gap collector from the same files)."""
    ed = ED[year].strftime("%Y%m%d")
    a = pd.read_csv(EVP / "nc" / f"agg_absentee_{ed}.csv")
    a = a[a["accepted"].astype(str) == "True"]
    return {"year": year, "mine": int(df["n"].sum()), "agg_file": int(a["n"].sum())}


def _cum_grid(inc: pd.DataFrame, keys: list[str], year: int, val_cols: list[str]) -> pd.DataFrame:
    """inc: one row per (keys..., rtn date) with increments in val_cols -> cumulative by day for days_before 45..0 and a
    final (-1) row = all accepted incl. returns after Election Day. Returns rows keyed by keys + days_before."""
    ed = ED[year]
    inc = inc.copy()
    inc["db"] = (ed - inc["rtn"]).dt.days
    final = inc.groupby(keys)[val_cols].sum().reset_index().assign(days_before=-1)
    pre = inc[inc["db"] >= 0]
    pre = pre.assign(db=pre["db"].clip(upper=MAXD))
    piv = pre.groupby(keys + ["db"])[val_cols].sum()
    # full grid per key tuple, cumulate from the oldest day
    flat = piv.reset_index()
    kt = flat[keys].drop_duplicates()
    grid = kt.merge(pd.DataFrame({"db": range(MAXD, -1, -1)}), how="cross")
    grid = grid.merge(flat, on=keys + ["db"], how="left").fillna(0)
    grid = grid.sort_values(keys + ["db"], ascending=[True] * len(keys) + [False])
    grid[val_cols] = grid.groupby(keys)[val_cols].cumsum()
    grid = grid.rename(columns={"db": "days_before"})
    return pd.concat([grid, final], ignore_index=True)


def nc_county_rows() -> pd.DataFrame:
    rows = []
    for y in YEARS:
        df = nc_daily(y)
        df["fips"] = df["county"].map(lambda n: fips_of("37", n))
        assert df["fips"].notna().all(), df[df["fips"].isna()]["county"].unique()
        # dimension columns -> wide increments
        for c, vals in (("party", ["D", "R", "O"]), ("ageb", ["a1829", "a3044", "a4564", "a65p"]),
                        ("sex", ["f", "m", "u"]), ("race", ["white", "black", "asian", "other"])):
            for v in vals:
                df[f"x_{v}"] = np.where(df[c] == v, df["n"], 0)
        df["x_a1844"] = df["x_a1829"] + df["x_a3044"]
        df["x_tot"] = df["n"]
        vals = [c for c in df.columns if c.startswith("x_")]
        base = df.groupby(["fips", "kind", "rtn"])[vals].sum().reset_index()
        both = base.groupby(["fips", "rtn"])[vals].sum().reset_index().assign(kind="total")
        base = pd.concat([base, both], ignore_index=True)
        g = _cum_grid(base, ["fips", "kind"], y, vals).assign(year=y)
        rows.append(g)
    g = pd.concat(rows, ignore_index=True)
    ren = {"x_tot": "ev_total", "x_D": "ev_D", "x_R": "ev_R", "x_O": "ev_O", "x_a1829": "ev_age_18_29", "x_a3044": "ev_age_30_44",
           "x_a4564": "ev_age_45_64", "x_a65p": "ev_age_65p", "x_a1844": "ev_age_18_44", "x_f": "ev_sex_f", "x_m": "ev_sex_m",
           "x_u": "ev_sex_u", "x_white": "ev_race_white", "x_black": "ev_race_black", "x_asian": "ev_race_asian",
           "x_other": "ev_race_other"}
    g = g.rename(columns=ren)
    g["st"] = "nc"
    g["source"] = "NCSBE per-ballot absentee files (accepted ballots, by return date)"
    g["note"] = "cumulative accepted ballots through end of day; age/sex/race from the voter record; days_before=-1: all accepted incl. returned after ED"
    return g


# ------------------------------------------------------------------------------------------------------------ MD
MD_START = {2016: "2016-10-27", 2018: "2018-10-25", 2020: "2020-10-26", 2022: "2022-10-27", 2024: "2024-10-24"}


def md_county_rows() -> pd.DataFrame:
    """Maryland SBE early-voting raw files (voters checked in at early-voting centres, by county of the centre,
    precinct, sex, age group, party, day 1..8). Early voting is 8 consecutive days ending ED-5. In-person only:
    Maryland publishes mail ballots only as final canvass totals."""
    rows = []
    for y in YEARS:
        f = EVP / "md" / f"ev{y}.csv"
        hdr = pd.read_csv(f, nrows=0, encoding="utf-8-sig").columns
        dcols = [c for c in hdr if re.fullmatch(r"(TOTAL_)?DAY\d", c, flags=re.I)]
        assert len(dcols) == 8, (y, hdr)
        raw = pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        d = pd.DataFrame({"county": raw["COUNTY_NAME"].str.strip(),
                          "party": np.select([raw["PARTY_CODE"].str.strip().str.upper() == "DEM", raw["PARTY_CODE"].str.strip().str.upper() == "REP"], ["D", "R"], "O"),
                          "ageb": raw["AgeGroup"].str.strip().str.upper().map({"18-24": "a1824", "25-44": "a2544", "45-64": "a4564",
                                                                              "65 AND OLDER": "a65p"}).fillna("na"),
                          "sex": raw["GENDER_CODE"].str.strip().str.upper().map({"F": "f", "M": "m"}).fillna("u")})
        for i, c in enumerate(dcols):
            d[f"d{i + 1}"] = pd.to_numeric(raw[c], errors="coerce").fillna(0)
        d["fips"] = d["county"].map(lambda n: fips_of("24", n))
        assert d["fips"].notna().all(), d[d["fips"].isna()]["county"].unique()
        start = pd.Timestamp(MD_START[y])
        long = d.melt(id_vars=["fips", "party", "ageb", "sex"], value_vars=[f"d{i}" for i in range(1, 9)],
                      var_name="day", value_name="n")
        long["rtn"] = start + pd.to_timedelta(long["day"].str[1:].astype(int) - 1, unit="D")
        for c, vals in (("party", ["D", "R", "O"]), ("ageb", ["a1824", "a2544", "a4564", "a65p"]), ("sex", ["f", "m", "u"])):
            for v in vals:
                long[f"x_{v}"] = np.where(long[c] == v, long["n"], 0)
        long["x_a1844"] = long["x_a1824"] + long["x_a2544"]
        long["x_tot"] = long["n"]
        vals = [c for c in long.columns if c.startswith("x_")]
        base = long.groupby(["fips", "rtn"])[vals].sum().reset_index().assign(kind="inperson")
        g = _cum_grid(base, ["fips", "kind"], y, vals).assign(year=y)
        # drop days before the first early-voting day (all zero) -> not published; keep 0.. after the last day (constant)
        first_db = (ED[y] - start).days
        g = g[(g["days_before"] <= first_db) & (g["days_before"] >= 0)]
        rows.append(g)
    g = pd.concat(rows, ignore_index=True)
    g = g.rename(columns={"x_tot": "ev_total", "x_D": "ev_D", "x_R": "ev_R", "x_O": "ev_O", "x_a4564": "ev_age_45_64",
                          "x_a65p": "ev_age_65p", "x_a1844": "ev_age_18_44", "x_f": "ev_sex_f", "x_m": "ev_sex_m", "x_u": "ev_sex_u"})
    g = g.drop(columns=[c for c in g.columns if c.startswith("x_")])
    g["st"] = "md"
    g["source"] = "MD SBE early-voting raw files (EarlyVoting RAW data.csv), in-person early voting only"
    g["note"] = ("cumulative check-ins by day; county = county of the early-voting centre; age bands 18-24/25-44 not splittable "
                 "to 18-29/30-44 (only ev_age_18_44); blank ages leave ev_age_* below ev_total; days after EV ends carry the final EV count; "
                 "no mail series")
    return g


# ------------------------------------------------------------------------------------------------------------ CO
_CO_SKIP = {"", "TOTAL", "GRAND TOTAL", "STATEWIDE", "COUNTY", "END OF WORKSHEET"}


def _co_parse_txt_2016(path: Path) -> pd.DataFrame:
    """2016 'Ballots Received to Date' text releases (county x ACN DEM GRN LBR REP UAF UNI + Grand Total). Blank cells
    collapse in the text, so columns cannot be read by position. DEM, REP and UAF are the three tokens >= 3% of the
    county's Grand Total (ACN/GRN/LBR/UNI are tiny); D = first, R = second, O = Grand Total - D - R. Each file's statewide
    'Total' row is used as a checksum (assert on D and R)."""
    from itertools import combinations
    out, tot_row = [], None
    lines = path.read_text(errors="ignore").splitlines()
    hdr = next(x for x in lines if x.strip().startswith("COUNTY"))
    hpos = np.array([re.search(c, hdr).end() for c in ("ACN", "DEM", "GRN", "LBR", "REP", "UAF", "UNI")])
    for l in lines:
        m = re.match(r"^([A-Za-z][A-Za-z .']+?)\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$", l)
        if not m:
            continue
        name = m.group(1).strip()
        vals = [int(x.replace(",", "")) for x in m.group(2).split()]
        if name.upper() in ("TOTAL", "GRAND TOTAL"):
            tot_row = vals
            continue
        if name.upper() in _CO_SKIP:
            continue
        total, toks = vals[-1], vals[:-1]
        assert sum(toks) == total, (path.name, name, vals)
        big = [v for v in toks if v >= 0.03 * total]
        if len(big) == 3:
            out.append({"county": name, "D": big[0], "R": big[1], "tot": total})
        else:                                   # tiny counties (total < ~40): place tokens by layout position
            ends = [m.start(2) + t.end() for t in re.finditer(r"[\d,]+", l[m.start(2):])][:-1]
            best = min(combinations(range(7), len(toks)),
                       key=lambda sub: float(np.var(np.array(ends) - np.array(hpos)[list(sub)])))
            dd = dict(zip(best, toks))
            out.append({"county": name, "D": dd.get(1, 0), "R": dd.get(4, 0), "tot": total})
    df = pd.DataFrame(out)
    df["O"] = df["tot"] - df["D"] - df["R"]
    if tot_row is not None:
        # tot_row = ACN DEM GRN LBR REP UAF UNI Grand (blanks collapse, but the statewide row is complete)
        assert len(tot_row) == 8 and df["tot"].sum() == tot_row[-1], (path.name, df["tot"].sum(), tot_row)
        assert abs(df["D"].sum() - tot_row[1]) <= 0.002 * tot_row[-1] and abs(df["R"].sum() - tot_row[4]) <= 0.002 * tot_row[-1], \
            (path.name, df["D"].sum(), df["R"].sum(), tot_row)
    return df


def _co_sheet(path: Path, sheet: str) -> pd.DataFrame | None:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        return None
    rows = list(wb[sheet].iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows[:5]) if r and r[0] == "COUNTY")
    hdr = rows[hi]
    pcols = [j for j, h in enumerate(hdr) if isinstance(h, str) and h.strip() and h not in ("COUNTY", "Grand Total")
             and not h.startswith("ACTIVE")]
    out = []
    for r in rows[hi + 1:]:
        if not r or not isinstance(r[0], str) or r[0].strip().upper() in _CO_SKIP:
            continue
        v = {hdr[j]: (r[j] if isinstance(r[j], (int, float)) else 0) for j in pcols}
        out.append({"county": r[0].strip(), "D": v.get("DEM", 0), "R": v.get("REP", 0), "tot": sum(v.values())})
    df = pd.DataFrame(out)
    df["O"] = df["tot"] - df["D"] - df["R"]
    return df


def _co_ip(path: Path) -> pd.DataFrame | None:
    a = _co_sheet(path, "In_Person_by_Party_County")
    return a if a is not None else _co_sheet(path, "In_Person_Ballots_By_County")


def co_county_rows() -> pd.DataFrame:
    att = EVP / "co" / "att"
    snaps = []     # (year, asof, kind, df)
    for f in sorted(att.iterdir()):
        m = re.match(r"^(\d{4})_(\d{8})(BallotsReceivedToDate|ElectionActivity|GeneralElectionActivity|ElectionActivity1159PM|"
                     r"GeneralElectionActivity1159PM)\.(txt|xlsx)$", f.name)
        if not m:
            continue
        y, dstr, kind_s, ext = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        if y not in YEARS:
            continue
        dt = pd.Timestamp(dstr)
        if ext == "txt":
            if not f.name.endswith("BallotsReceivedToDate.txt"):
                continue
            asof = dt - pd.Timedelta(days=1)      # release dated D covers close of D-1 (same convention as data_public/us_ev_party_panel.csv)
            snaps.append((y, asof, "total", _co_parse_txt_2016(f), f.name))
        else:
            tot = _co_sheet(f, "All_Returned_Ballots_By_County")
            snaps.append((y, dt, "total", tot, f.name))
            ip = _co_ip(f)
            if ip is not None:
                snaps.append((y, dt, "inperson", ip, f.name))
                mail = tot.set_index("county")[["D", "R", "O", "tot"]] - ip.set_index("county")[["D", "R", "O", "tot"]].reindex(tot["county"]).fillna(0)
                snaps.append((y, dt, "mail", mail.reset_index(), f.name))
    # final canvass tables published after Election Day
    for y, fn in ((2020, "t2020.xlsx"), (2024, "t2024.xlsx")):
        p = EVP / "co" / fn
        tot = _co_sheet(p, "All_Returned_Ballots_By_County")
        ip = _co_ip(p)
        a = ED[y] + pd.Timedelta(days=1)
        snaps.append((y, a, "total", tot, fn))
        snaps.append((y, a, "inperson", ip, fn))
        mail = tot.set_index("county")[["D", "R", "O", "tot"]] - ip.set_index("county")[["D", "R", "O", "tot"]].reindex(tot["county"]).fillna(0)
        snaps.append((y, a, "mail", mail.reset_index(), fn))
    rows = []
    for y, asof, kind, df, fn in snaps:
        df = df.copy()
        df["fips"] = df["county"].map(lambda n: fips_of("08", n))
        assert df["fips"].notna().all(), (fn, df[df["fips"].isna()]["county"].tolist())
        db = (ED[y] - asof).days
        rows.append(pd.DataFrame({"fips": df["fips"], "year": y, "kind": kind, "asof_date": asof, "db_raw": db,
                                  "ev_total": df["tot"], "ev_D": df["D"], "ev_R": df["R"], "ev_O": df["O"], "src": fn}))
    g = pd.concat(rows, ignore_index=True)
    g = g[g["db_raw"] <= MAXD]
    # final: the latest snapshot after ED (db<0), per (year, kind) -> days_before -1; same-day duplicates: keep the last file
    g = g.sort_values(["year", "kind", "asof_date", "src"]).drop_duplicates(["fips", "year", "kind", "asof_date"], keep="last")
    post = g[g["db_raw"] < 0]
    post = post[post["asof_date"] == post.groupby(["year", "kind"])["asof_date"].transform("max")]
    pre = g[g["db_raw"] >= 0]
    g = pd.concat([pre, post], ignore_index=True)
    g["days_before"] = g["db_raw"].clip(lower=-1)
    g["is_final"] = g["db_raw"] < 0
    g["st"] = "co"
    g["source"] = "CO SoS daily ballots-returned releases (county x party); final canvass tables t2020/t2024.xlsx"
    g["note"] = ("cumulative ballots returned (mail + in person; all returned incl. later rejected); D/R by registration; O = all others; "
                 "2016 txt tables re-parsed (blank cells recovered by Grand Total + layout); weekday releases only; no age by county; "
                 "mail = all returned - in person")
    return g.drop(columns=["db_raw", "src"])


# ------------------------------------------------------------------------------------------------------------ FL
FL_FILES = {  # election number -> (year, compile kinds available)
    "10866": 2020, "26906": 2022, "43888": 2024}


def fl_county_rows() -> pd.DataFrame:
    """Florida DOE county vote-by-mail / early-voting compilations (Stats_<n>_VbmVoted/EarlyVoted.txt). Only one
    compilation per election is on disk or published (no past daily series): early voting compiled on Election Day
    morning, mail compiled on Election Day (2022) or 12-15 days after it (2024)."""
    rows = []
    for f in sorted((EVP / "fl").glob("Stats_*_*Voted.txt")):
        m = re.match(r"Stats_(\d+)_(Early|Vbm)Voted\.txt$", f.name)
        if not m or m.group(1) not in FL_FILES:
            continue
        y = FL_FILES[m.group(1)]
        kind = "inperson" if m.group(2) == "Early" else "mail"
        d = pd.read_csv(f, sep="\t", dtype=str)
        d = d[d["CountyName"].str.strip() != "State Totals"].copy()
        num = lambda c: pd.to_numeric(d[c].str.replace(",", ""), errors="coerce").fillna(0)
        asof = pd.to_datetime(d["CompileDate"].str.split().str[0], format="%m/%d/%Y")
        db = (ED[y] - asof).dt.days
        out = pd.DataFrame({"fips": d["CountyName"].str.strip().map(lambda n: fips_of("12", n)), "year": y, "kind": kind,
                            "asof_date": asof, "ev_total": num("GrandTotal"), "ev_D": num("TotalDem"), "ev_R": num("TotalRep"),
                            "ev_O": num("TotalOth") + num("TotalNpa"), "db_raw": db})
        assert out["fips"].notna().all(), d[out["fips"].isna()]["CountyName"].tolist()
        rows.append(out)
    g = pd.concat(rows, ignore_index=True)
    tot = g.groupby(["fips", "year"]).filter(lambda x: set(x["kind"]) == {"inperson", "mail"})
    both = tot.groupby(["fips", "year"]).agg({"ev_total": "sum", "ev_D": "sum", "ev_R": "sum", "ev_O": "sum", "db_raw": "min",
                                               "asof_date": "min"}).reset_index().assign(kind="total")
    g = pd.concat([g, both], ignore_index=True)
    g["days_before"] = g["db_raw"].clip(lower=-1)
    g["is_final"] = g["db_raw"] < 0
    g["st"] = "fl"
    g["source"] = "FL DOE county vote-by-mail / early-voting compilations (Stats_<election>_{VbmVoted,EarlyVoted}.txt)"
    g["note"] = ("single compilation per election (no past daily series is published); asof_date = compile date (for total: the older of the two); "
                 "days_before=-1/is_final: compiled after Election Day; 2016/2018 county files not available; "
                 "O = Other + NPA; early voting 2020 only in-person (2020 mail Stats file 404)")
    return g.drop(columns=["db_raw"])


# ------------------------------------------------------------------------------------------------------------ TX
def tx_county_rows() -> pd.DataFrame:
    """Texas SoS 'Early Voting Cumulative Totals Thru Close of Business' day pages, November 2016 and 2018 generals:
    15 (2016) / 30 (2018) largest counties only, in-person and mail cumulative voters, registered voters. No party/age.
    2020-2024: the SoS tool (goelect ... getFile EVR_ELECTION) lists only 2025-2026 elections; older EVR files are not served."""
    d = HIST / "tx"
    rows = []
    for f in sorted(d.glob("pg20*_*.html")):
        m = re.match(r"pg(\d{4})_(oct|nov)(\d+)\.html", f.name)
        y, mon, day = int(m.group(1)), m.group(2), int(m.group(3))
        asof = pd.Timestamp(year=y, month=10 if mon == "oct" else 11, day=day)
        t = f.read_text(errors="ignore")
        for tr in re.findall(r"<tr>(.*?)</tr>", t, flags=re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
            if len(cells) != 9 or cells[0] in ("County", "Total"):
                continue
            n = [int(re.sub(r"[^\d]", "", c) or 0) for c in cells]
            fp = fips_of("48", cells[0])
            if fp is None:
                raise ValueError(cells[0])
            rows.append({"fips": fp, "year": y, "asof_date": asof, "reg": n[1], "ip": n[3], "mail": n[5], "tot": n[7]})
    df = pd.DataFrame(rows).drop_duplicates(["fips", "year", "asof_date"], keep="last")
    out = []
    for kind, c in (("inperson", "ip"), ("mail", "mail"), ("total", "tot")):
        o = df.assign(kind=kind, ev_total=df[c])
        out.append(o)
    g = pd.concat(out, ignore_index=True)
    g["days_before"] = g.apply(lambda r: (ED[r["year"]] - r["asof_date"]).days, axis=1)
    g["is_final"] = False
    for c in ("ev_D", "ev_R", "ev_O"):
        g[c] = np.nan
    g["st"] = "tx"
    g["source"] = "TX SoS Early Voting Totals archive day pages (sos.state.tx.us/elections/earlyvoting/<year>/)"
    g["note"] = "cumulative voters through close of business; only the 15 (2016) / 30 (2018) largest counties are listed; no party/age"
    return g.drop(columns=["ip", "mail", "tot"])


# ------------------------------------------------------------------------------------------------------ registration
def reg_nc() -> pd.DataFrame:
    """NCSBE voter_stats_<ED>.zip: registered voters by county/party on Election Day (all statuses listed in the file)."""
    rows = []
    for y in YEARS:
        ed = ED[y].strftime("%Y%m%d")
        with zipfile.ZipFile(EVP / "nc" / f"voter_stats_{ed}.zip") as z:
            d = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str, encoding="latin-1")
        d["n"] = pd.to_numeric(d["total_voters"], errors="coerce").fillna(0)
        d["p"] = d["party_cd"].str.strip().map({"DEM": "D", "REP": "R"}).fillna("O")
        g = d.pivot_table(index="county_desc", columns="p", values="n", aggfunc="sum", fill_value=0)
        g["reg_total"] = g.sum(axis=1)
        g = g.reset_index().rename(columns={"D": "reg_D", "R": "reg_R", "O": "reg_O", "county_desc": "county"})
        g["fips"] = g["county"].map(lambda n: fips_of("37", n))
        assert g["fips"].notna().all(), g[g["fips"].isna()]["county"].tolist()
        rows.append(g.assign(st="nc", year=y, reg_asof=ED[y], reg_def="all registered incl. inactive/temporary as listed in voter_stats",
                             source="NCSBE voter_stats_<ED>.zip (county x party counts as of Election Day)"))
    return pd.concat(rows, ignore_index=True).drop(columns="county")


def reg_pa() -> pd.DataFrame:
    rows = []
    for y in YEARS:
        lines = (EVP / "pa" / "reg" / f"pa_vr_{y}.pdf.layout.txt").read_text(errors="ignore").splitlines()
        hdr = next(l for l in lines if "County" in l and ("Democratic" in l or "Parties" in l))
        for l in lines:
            m = re.match(r"^\s*([A-Za-z][A-Za-z .']+?)\s{2,}([\d,]+(?:\s+[\d,]+)+)\s*$", l)
            if not m or m.group(1).strip().lower() in ("county", "total", "totals", "statewide", "state total"):
                continue
            nums = [int(x.replace(",", "")) for x in m.group(2).split()]
            f = fips_of("42", m.group(1).strip())
            if f is None:
                continue
            rows.append({"st": "pa", "fips": f, "year": y, "reg_total": nums[-1], "reg_D": nums[0], "reg_R": nums[1],
                         "reg_O": nums[-1] - nums[0] - nums[1], "reg_asof": ED[y],
                         "reg_def": "all registered (official Department of State county report for the November election)",
                         "source": "PA DOS Voter Registration Statistics by county (pa_vr_<year>.pdf)"})
    d = pd.DataFrame(rows)
    if d.groupby("year")["fips"].nunique().min() != 67:
        allf = {f for (s_, _), f in county_fips().items() if s_ == "42"}
        print({y: sorted(allf - set(g["fips"])) for y, g in d.groupby("year")})
    assert d.groupby("year")["fips"].nunique().min() == 67, d.groupby("year")["fips"].nunique()
    return d


def reg_co() -> pd.DataFrame:
    import openpyxl
    files = {2016: "2016_OctoberStatistics.xlsx", 2018: "2018_OctoberStatistics.xlsx", 2020: "2020_OctoberStatistics.xlsx",
             2022: "2022_OctoberStatistics.xlsx", 2024: "2024_20241101statistics.xlsx"}
    rows = []
    for y, fn in files.items():
        wb = openpyxl.load_workbook(EVP / "co" / "reg" / fn, read_only=True, data_only=True)
        data = list(wb["Party & Status"].iter_rows(values_only=True))
        top = data[:5]
        r_tot = next(r for r in top if "Active Total" in r)
        j_tot = r_tot.index("Active Total")
        r_par = next(r for r in top if "DEM" in r)
        jd, jr = r_par.index("DEM"), r_par.index("REP")
        for r in data:
            if not r or not isinstance(r[0], str) or r[0].strip().upper() in _CO_SKIP or not isinstance(r[j_tot], (int, float)):
                continue
            f = fips_of("08", r[0])
            if f is None:
                continue
            tot = r[j_tot]
            rows.append({"st": "co", "fips": f, "year": y, "reg_total": tot, "reg_D": r[jd], "reg_R": r[jr], "reg_O": tot - r[jd] - r[jr],
                         "reg_asof": pd.Timestamp(f"{y}-11-01"), "reg_def": "active registered voters (excl. inactive, pre-registered)",
                         "source": "CO SoS voter registration statistics by county (as of ~Nov 1)"})
    d = pd.DataFrame(rows)
    assert d.groupby("year")["fips"].nunique().min() == 64, d.groupby("year")["fips"].nunique()
    return d


def reg_fl() -> pd.DataFrame:
    import openpyxl
    names = {2018: "Party-Affilation-by-County-2018.xlsx", 2020: "Party Affilation by County 2020.xlsx",
             2022: "party-affiliation-by-county-2022.xlsx", 2024: "party-affiliation-by-county-2024.xlsx"}
    rows = []
    for y, n in names.items():
        with zipfile.ZipFile(EVP / "fl" / f"reg_archive_{y}.zip") as z:
            wb = openpyxl.load_workbook(io.BytesIO(z.read(n)), read_only=True, data_only=True)
        for r in wb["October"].iter_rows(values_only=True):
            if not r or not isinstance(r[0], str) or not isinstance(r[5], (int, float)) or r[0].strip().upper() in _CO_SKIP:
                continue
            f = fips_of("12", r[0])
            if f is None:
                continue
            rows.append({"st": "fl", "fips": f, "year": y, "reg_total": r[5], "reg_D": r[2], "reg_R": r[1], "reg_O": r[3] + r[4],
                         "reg_asof": pd.Timestamp(f"{y}-10-31"), "reg_def": "active registered voters by party (end of October)",
                         "source": "FL DOE Voter Registration by Party Affiliation by County, October report"})
    d = pd.DataFrame(rows)
    assert d.groupby("year")["fips"].nunique().min() == 67, d.groupby("year")["fips"].nunique()
    return d


def registration(tx: pd.DataFrame) -> pd.DataFrame:
    parts = [reg_nc(), reg_pa(), reg_co(), reg_fl()]
    t = tx.groupby(["fips", "year"]).agg(reg_total=("reg", "max"), reg_asof=("asof_date", "min")).reset_index().assign(
        st="tx", reg_D=np.nan, reg_R=np.nan, reg_O=np.nan, reg_def="registered voters shown on the SoS early-voting day pages (only listed counties)",
        source="TX SoS Early Voting Totals archive day pages")
    parts.append(t)
    d = pd.concat(parts, ignore_index=True)
    d = d.sort_values(["fips", "year"])
    d["reg_prev"] = d.groupby("fips")["reg_total"].shift(1)
    d["prev_year"] = d.groupby("fips")["year"].shift(1)
    ok = d["prev_year"] == d["year"] - 2
    d["reg_growth"] = np.where(ok, d["reg_total"] / d["reg_prev"] - 1, np.nan)
    d["reg_D_share"] = d["reg_D"] / d["reg_total"]
    d["reg_R_share"] = d["reg_R"] / d["reg_total"]
    d["reg_net_D_minus_R"] = (d["reg_D"] - d["reg_R"]) / d["reg_total"]
    d["reg_net_growth"] = np.where(ok, d["reg_net_D_minus_R"] - d.groupby("fips")["reg_net_D_minus_R"].shift(1), np.nan)
    return d.drop(columns=["reg_prev", "prev_year"])[
        ["st", "fips", "year", "reg_asof", "reg_total", "reg_D", "reg_R", "reg_O", "reg_growth", "reg_D_share", "reg_R_share",
         "reg_net_D_minus_R", "reg_net_growth", "reg_def", "source"]]


# ------------------------------------------------------------------------------------------------- final votes panel
STATE_POSTAL = {"01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca", "08": "co", "09": "ct", "10": "de", "11": "dc",
                "12": "fl", "13": "ga", "15": "hi", "16": "id", "17": "il", "18": "in", "19": "ia", "20": "ks", "21": "ky",
                "22": "la", "23": "me", "24": "md", "25": "ma", "26": "mi", "27": "mn", "28": "ms", "29": "mo", "30": "mt",
                "31": "ne", "32": "nv", "33": "nh", "34": "nj", "35": "nm", "36": "ny", "37": "nc", "38": "nd", "39": "oh",
                "40": "ok", "41": "or", "42": "pa", "44": "ri", "45": "sc", "46": "sd", "47": "tn", "48": "tx", "49": "ut",
                "50": "vt", "51": "va", "53": "wa", "54": "wv", "55": "wi", "56": "wy"}


def final_votes() -> pd.DataFrame:
    """County x even-year final total votes from data_public/us_county_panel.csv (v_* = total votes cast for that office
    in the county, all candidates). v_ref = v_pres in presidential years; in midterm years the maximum of the statewide
    / House office totals present (a county's ballots cast ~ its largest office total; the House total covers every
    county of a state, Senate/governor only where contested). Years 2016-2024; AK and CT are not in the panel."""
    p = pd.read_csv(REPO / "data_public" / "us_county_panel.csv", comment="#", dtype={"fips": str, "st": str})
    p = p[p["year"].isin(YEARS)].copy()
    p["st"] = p["fips"].str[:2].map(STATE_POSTAL)
    v = p[["v_pres", "v_sen", "v_gov", "v_house"]]
    pres = p["year"] % 4 == 0
    p["v_ref"] = np.where(pres, p["v_pres"], v[["v_sen", "v_gov", "v_house"]].max(axis=1))
    p["v_ref_src"] = np.where(pres, "v_pres", np.where(v[["v_sen", "v_gov", "v_house"]].max(axis=1).notna(), "max(v_sen,v_gov,v_house)", ""))
    return p[["st", "fips", "year", "v_pres", "v_sen", "v_gov", "v_house", "v_ref", "v_ref_src"]].reset_index(drop=True)


# ------------------------------------------------------------------------------------------------------- state table
def co_state_age_rows() -> pd.DataFrame:
    """CO daily xlsx releases, sheet All_Returned_Ballots_GenderAge: state totals by age band and gender (all parties)."""
    import openpyxl
    att = EVP / "co" / "att"
    out = []
    for f in sorted(att.iterdir()):
        m = re.match(r"^(\d{4})_(\d{8})(ElectionActivity|GeneralElectionActivity|ElectionActivity1159PM|GeneralElectionActivity1159PM)\.xlsx$", f.name)
        if not m or int(m.group(1)) not in YEARS:
            continue
        y, asof = int(m.group(1)), pd.Timestamp(m.group(2))
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        if "All_Returned_Ballots_GenderAge" not in wb.sheetnames:
            continue
        rows = list(wb["All_Returned_Ballots_GenderAge"].iter_rows(values_only=True))
        hi = next(i for i, r in enumerate(rows[:5]) if r and isinstance(r[0], str) and r[0].upper().startswith("GENDER"))
        hdr = rows[hi]
        gt = max(j for j, h in enumerate(hdr) if isinstance(h, str) and h.strip().upper() == "GRAND TOTAL")
        acc = {}
        gender = None
        for r in rows[hi + 1:]:
            lab = r[0].strip() if r and isinstance(r[0], str) else None
            if lab is None or not isinstance(r[gt], (int, float)):
                continue
            if lab.upper() == "GRAND TOTAL":
                acc["total"] = r[gt]
            elif re.match(r"^\d+-\d+$|^75 AND OVER$|^<18$", lab.upper()):
                acc[lab.upper()] = acc.get(lab.upper(), 0) + r[gt]
            else:
                gender = lab.upper()
                acc["sex_" + ("f" if gender == "FEMALE" else "m" if gender == "MALE" else "u")] = acc.get("sex_" + ("f" if gender == "FEMALE" else "m" if gender == "MALE" else "u"), 0) + r[gt]
        g = lambda *ks: sum(acc.get(k, 0) for k in ks)
        out.append({"year": y, "asof_date": asof, "ev_total_age_file": acc.get("total"),
                    "ev_age_18_44": g("18-24", "25-34", "35-44"), "ev_age_45_64": g("45-54", "55-64"),
                    "ev_age_65p": g("65-74", "75 AND OVER"), "ev_sex_f": g("sex_f"), "ev_sex_m": g("sex_m"), "ev_sex_u": g("sex_u")})
    d = pd.DataFrame(out).drop_duplicates(["year", "asof_date"], keep="last")
    d["days_before"] = (d["year"].map(ED) - d["asof_date"]).dt.days
    return d[d["days_before"].between(0, MAXD)]


def state_rows(county: pd.DataFrame) -> pd.DataFrame:
    num = ["ev_total", "ev_D", "ev_R", "ev_O"] + AGE_COLS + SEX_COLS + RACE_COLS
    key = ["st", "year", "days_before", "kind", "asof_date", "is_final"]
    c = county.copy()
    c["asof_date"] = c["asof_date"].fillna(pd.Timestamp("1900-01-01"))
    g = c.groupby(key, dropna=False).agg({**{n: (lambda s: s.sum(min_count=1)) for n in num}, "fips": "nunique"}).reset_index()
    g = g.rename(columns={"fips": "n_counties"})
    g.loc[g["asof_date"] == pd.Timestamp("1900-01-01"), "asof_date"] = pd.NaT
    g["source"] = "sum of county rows (see ev_hist_county)"
    g["note"] = ""
    tx = g["st"] == "tx"
    g.loc[tx, "note"] = "TX: sum of the 15 (2016) / 30 (2018) largest counties only, not statewide"
    # CO: add state age/sex from the CO daily files (state totals only; counties have no age)
    age = co_state_age_rows()
    age_cols = ["ev_age_18_44", "ev_age_45_64", "ev_age_65p", "ev_sex_f", "ev_sex_m", "ev_sex_u"]
    m = g[(g["st"] == "co") & (g["kind"] == "total")].drop(columns=age_cols).merge(
        age.drop(columns="asof_date"), on=["year", "days_before"], how="left")
    mask = (g["st"] == "co") & (g["kind"] == "total")
    for col in age_cols:
        g.loc[mask, col] = m[col].to_numpy()
    g["ev_total_age_file"] = np.nan
    g.loc[mask, "ev_total_age_file"] = m["ev_total_age_file"].to_numpy()
    g.loc[(g["st"] == "co") & g["ev_age_18_44"].notna(), "note"] = "CO age/sex: state totals from the daily GenderAge sheet (counties publish none)"
    # states without county files: the existing state-level panel (cumulative D/R/O by date)
    pan = pd.read_csv(REPO / "data_public" / "us_ev_party_panel.csv", comment="#")
    pan = pan[pan["year"].isin(YEARS)]
    have = set(zip(g["st"], g["year"]))
    pan = pan[[ (s, y) not in have for s, y in zip(pan["state"], pan["year"])]].copy()
    pan["asof_date"] = pd.to_datetime(pan["date"])
    pan["days_before"] = (pan["year"].map(ED) - pan["asof_date"]).dt.days
    pan = pan[pan["days_before"] <= MAXD].copy()
    pan["days_before"] = pan["days_before"].clip(lower=-1)
    pan = pan.rename(columns={"state": "st", "D": "ev_D", "R": "ev_R", "O": "ev_O"})
    pan["ev_total"] = pan[["ev_D", "ev_R", "ev_O"]].sum(axis=1)
    pan["is_final"] = pan["days_before"] < 0
    pan["n_counties"] = np.nan
    pan["source"] = "data_public/us_ev_party_panel.csv (state totals; see docs/us_ev_gap.md)"
    pan = pan.rename(columns={"note": "note_src"})
    pan["note"] = "state total only (no county detail): " + pan["note_src"].astype(str).str.slice(0, 90)
    out = pd.concat([g, pan[g.columns.intersection(pan.columns)]], ignore_index=True)
    for c_ in num:
        if c_ not in out:
            out[c_] = np.nan
    dev = (out["ev_total_age_file"] / out["ev_total"] - 1).abs()
    print("CO state GenderAge file total vs sum of counties: max abs rel diff", float(dev.max()), "n", int(dev.notna().sum()))
    out = out.drop(columns=["ev_total_age_file"])
    cols = ["st", "year", "days_before", "kind", "asof_date", "is_final", "n_counties"] + num + ["source", "note"]
    return out[cols].sort_values(["st", "year", "kind", "days_before"], ascending=[True, True, True, False]).reset_index(drop=True)


# ------------------------------------------------------------------------------------------------------ diagnostic
D_TARGETS = (21, 14, 10, 7, 5, 4, 3, 2, 1, 0)


def _pick(c: pd.DataFrame, d: int) -> pd.DataFrame:
    """For one (st, kind, year): the county rows of the observation day nearest to (and not later than) days_before=d,
    within 2 days earlier. Day must cover >= 90% of the largest county set of that (st, kind, year)."""
    c = c[(~c["is_final"]) & c["days_before"].between(d, d + 2)]
    if c.empty:
        return c
    n = c.groupby("days_before")["fips"].nunique()
    full = n[n >= 0.9 * n.max()]
    if full.empty:
        return c.iloc[0:0]
    return c[c["days_before"] == full.index.min()]


def _wape(p: pd.Series, a: pd.Series) -> float:
    return float((p - a).abs().sum() / a.sum())


def diagnostic(county: pd.DataFrame, fv: pd.DataFrame) -> pd.DataFrame:
    """EV pace diagnostic. ratio(t,d) = EV(t,d)/v_ref(t) for EV states with >= 2 cycles at the same days_before."""
    base = county[county["kind"].isin(["inperson", "mail", "total"])].merge(fv[["fips", "year", "v_ref", "st"]], on=["fips", "year", "st"], how="inner")
    base = base[base["v_ref"] > 0]
    res = []
    for (st, kind), grp in base.groupby(["st", "kind"]):
        for d in D_TARGETS:
            obs = {}
            for y, cy in grp.groupby("year"):
                o = _pick(cy, d)
                if len(o) >= 5 or (st == "tx" and len(o) >= 5):
                    obs[y] = o.set_index("fips")[["ev_total", "ev_D", "ev_R", "v_ref"]].assign(db=o["days_before"].min())
            yrs = sorted(obs)
            if len(yrs) < 2:
                continue
            rec = {"st": st, "kind": kind, "d_target": d, "n_cycles": len(yrs), "years": " ".join(map(str, yrs)),
                   "d_used": "/".join(str(int(obs[y]["db"].iloc[0])) for y in yrs),
                   "d_spread": int(max(obs[y]["db"].iloc[0] for y in yrs) - min(obs[y]["db"].iloc[0] for y in yrs))}
            # state-level ratio
            rat = {y: obs[y]["ev_total"].sum() / obs[y]["v_ref"].sum() for y in yrs}
            rv = np.array(list(rat.values()))
            rec.update(ratio_state_mean=rv.mean(), ratio_state_sd=rv.std(ddof=1), ratio_state_cv=rv.std(ddof=1) / rv.mean() if rv.mean() > 0 else np.nan,
                       ratio_by_year=json.dumps({int(y): round(float(v), 4) for y, v in rat.items()}))
            for lab, sel in (("pres", [y for y in yrs if y % 4 == 0]), ("mid", [y for y in yrs if y % 4 == 2])):
                v = np.array([rat[y] for y in sel])
                rec[f"ratio_cv_{lab}"] = v.std(ddof=1) / v.mean() if len(v) >= 2 and v.mean() > 0 else np.nan
                rec[f"n_{lab}"] = len(sel)
            # county-level variation of the ratio
            cv_c, sd_c, persist, wapes = [], [], [], []
            for y in yrs:
                r = obs[y]["ev_total"] / obs[y]["v_ref"]
                r = r[np.isfinite(r)]
                sd_c.append(r.std(ddof=1))
                w = obs[y]["v_ref"].reindex(r.index)
                m = np.average(r, weights=w)
                cv_c.append(float(np.sqrt(np.average((r - m) ** 2, weights=w)) / m) if m > 0 else np.nan)
            rec.update(county_ratio_sd=float(np.mean(sd_c)), county_ratio_wcv=float(np.nanmean(cv_c)) if np.isfinite(cv_c).any() else np.nan)
            # persistence: corr across counties of ratio(t) with ratio(t-2)
            pc = []
            for y in yrs:
                if y - 2 in obs:
                    a = (obs[y]["ev_total"] / obs[y]["v_ref"]).rename("a")
                    b = (obs[y - 2]["ev_total"] / obs[y - 2]["v_ref"]).rename("b")
                    j = pd.concat([a, b], axis=1).dropna()
                    j = j[np.isfinite(j).all(axis=1)]
                    if len(j) >= 8:
                        pc.append(j["a"].corr(j["b"]))
            rec["county_ratio_corr_prev_cycle"] = float(np.mean(pc)) if pc else np.nan
            rec["degenerate"] = bool(rv.mean() < 0.005)       # EV channel (almost) not open yet at this days_before
            rec["n_corr_pairs"] = len(pc)
            # share prediction: county share of the state final total (v_ref); counties common to cycle t and t-2
            e_prev, e_ev, e_rat, e_blend, pairs = [], [], [], [], []
            for y in yrs:
                if y - 2 not in obs:
                    continue
                a, b = obs[y], obs[y - 2]
                idx = a.index.intersection(b.index)
                if len(idx) < 8:
                    continue
                a, b = a.loc[idx], b.loc[idx]
                s_true = a["v_ref"] / a["v_ref"].sum()
                s_prev = b["v_ref"] / b["v_ref"].sum()
                ev_t = a["ev_total"] / a["ev_total"].sum()
                ev_p = b["ev_total"] / b["ev_total"].sum()
                s_rat = s_prev * (ev_t / ev_p.replace(0, np.nan))
                s_rat = (s_rat / s_rat.sum()).fillna(s_prev)
                pairs.append((s_true, s_prev, ev_t, s_rat))
                e_prev.append(_wape(s_prev, s_true)); e_ev.append(_wape(ev_t, s_true)); e_rat.append(_wape(s_rat, s_true))
            if pairs:
                # blend weight on the EV share, fitted by least squares on the other pairs (leave-one-pair-out)
                def _fit(ps):
                    nu = de = 0.0
                    for s_true, s_prev, ev_t, _ in ps:
                        dv = (ev_t - s_prev).to_numpy()
                        nu += float((dv * (s_true - s_prev).to_numpy()).sum()); de += float((dv * dv).sum())
                    return float(np.clip(nu / de, 0, 1)) if de > 0 else np.nan
                w = _fit(pairs)
                for i, (s_true, s_prev, ev_t, _) in enumerate(pairs):
                    wi = _fit(pairs[:i] + pairs[i + 1:]) if len(pairs) >= 2 else np.nan
                    e_blend.append(_wape((1 - wi) * s_prev + wi * ev_t, s_true) if np.isfinite(wi) else np.nan)
                rec.update(n_pred=len(pairs), wape_prev_share=np.mean(e_prev), wape_ev_share=np.mean(e_ev), wape_ratio_est=np.mean(e_rat),
                           blend_w_ev=w, wape_blend_loo=np.nanmean(e_blend) if np.isfinite(e_blend).any() else np.nan,
                           wape_gain_blend_loo_vs_prev=1 - np.nanmean(e_blend) / np.mean(e_prev) if np.isfinite(e_blend).any() else np.nan,
                           wape_gain_ratio_vs_prev=1 - np.mean(e_rat) / np.mean(e_prev))
            res.append(rec)
    return pd.DataFrame(res)


# ------------------------------------------------------------------------------------------------------------ build
def build() -> None:
    U.mkdir(parents=True, exist_ok=True)
    tx = tx_county_rows()
    parts = [nc_county_rows(), md_county_rows(), co_county_rows(), fl_county_rows(), tx]
    for q in parts:
        for c in OUT_COLS:
            if c not in q:
                q[c] = np.nan
    county = pd.concat([q[OUT_COLS + (["reg"] if "reg" in q else [])].drop(columns=["reg"], errors="ignore") for q in parts], ignore_index=True)
    # asof_date for the per-ballot sources: the end of the day that is days_before before ED
    m = county["asof_date"].isna() & (county["days_before"] >= 0)
    county.loc[m, "asof_date"] = county.loc[m, "year"].map(ED) - pd.to_timedelta(county.loc[m, "days_before"], unit="D")
    county["asof_date"] = pd.to_datetime(county["asof_date"])
    county["is_final"] = county["is_final"].where(county["is_final"].notna(), county["days_before"] < 0).astype(bool)
    county["days_before"] = county["days_before"].astype("int16")
    county["year"] = county["year"].astype("int16")
    for c in ["ev_total", "ev_D", "ev_R", "ev_O"] + AGE_COLS + SEX_COLS + RACE_COLS:
        county[c] = pd.to_numeric(county[c], errors="coerce").astype("float64")
    county = county.sort_values(["st", "fips", "year", "kind", "days_before"], ascending=[True, True, True, True, False]).reset_index(drop=True)
    fv = final_votes()
    reg = registration(tx)
    # attach final votes and registration to the county rows (denominators; final = target, never a feature)
    county = county.merge(fv[["fips", "year", "v_ref"]].rename(columns={"v_ref": "v_final_ref"}), on=["fips", "year"], how="left")
    county = county.merge(reg[["fips", "year", "reg_total", "reg_growth"]], on=["fips", "year"], how="left")
    county.to_parquet(U / "ev_hist_county.parquet", index=False)
    cy = fv.merge(reg.drop(columns=["st"]), on=["fips", "year"], how="left")
    cy.to_parquet(U / "ev_hist_county_year.parquet", index=False)
    reg.to_parquet(U / "ev_hist_registration.parquet", index=False)
    st = state_rows(county.drop(columns=["v_final_ref", "reg_total", "reg_growth"]))
    st.to_parquet(U / "ev_hist_state.parquet", index=False)
    dg = diagnostic(county, fv)
    dg.to_csv(U / "ev_pace_diagnostic.csv", index=False, float_format="%.5g")
    print("county", county.shape, "state", st.shape, "county_year", cy.shape, "reg", reg.shape, "diag", dg.shape)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd in ("fetch", "all"):
        fetch()
    if cmd in ("nc", "all"):
        for y in YEARS:
            nc_daily(y, force=(cmd == "nc"))
            print(y, nc_validate(y, nc_daily(y)), flush=True)
    if cmd in ("build", "all"):
        build()
