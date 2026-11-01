"""Hispanic origin groups by county and census tract: ACS 5-year B03001 + citizen voting-age population (B05003I, B05003).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_data.py fetch [vintages]
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_data.py build [vintages]
(county_origin_groups.csv is written by analysis/us_origin_groups.py)

Vintages (ACS 5-year ending year): 2016, 2020, 2022, 2024. The Census API (api.census.gov) needs a key, so the keyless bulk
files under www2.census.gov/programs-surveys/acs/summary_file are used (public domain, one request at a time):
  ending 2016, 2020 : <yr>/data/5_year_seq_by_state/<State>/{All_Geographies_Not_Tracts_Block_Groups,Tracts_Block_Groups_Only}
  ending 2022, 2024 : <yr>/table-based-SF/data/5YRData/acsdt5y<yr>-<table>.dat   (all geographies in one file)
Raw files go to $BRUNDER_DATA/raw/us/census/b03001/<vintage>/.  Outputs: $BRUNDER_DATA/processed/us2026/origin/.

Origin cells (B03001 lines): 4 Mexican, 5 Puerto Rican, 6 Cuban, 7 Dominican, 8 Central American (9-15 by country),
16 South American (17-26 by country), 27 Other Hispanic (28-31).  Non-Hispanic white / Black / Asian counts come from B03002 (lines 3, 4, 6).  Citizens of voting age (CVAP) = native + naturalized,
18 years and over, lines 9, 11, 20, 22 of B05003 / B05003I.  ACS does not publish citizenship by origin, so the
origin-level CVAP is the origin population times a state x origin citizen-adult ratio from public ACS PUMS (analysis/us_origin_pums.py);
that step is applied in analysis/us_origin_groups.py, never here: this file keeps the published cells.
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us" / "census" / "b03001"
OUT = DATA / "processed" / "us2026" / "origin"
HIST = DATA / "raw" / "us" / "census" / "acs_hist"            # lookup tables already cached by us_unified_demo.py
SF = "https://www2.census.gov/programs-surveys/acs/summary_file"
UA = "brunder/0.1 (research; recorder)"
LEGACY = [2016, 2020]
TABLE_BASED = [2022, 2024]
TABLES = ["B03001", "B05003", "B05003I", "B03002"]

STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "DistrictOfColumbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "NewHampshire", "NewJersey", "NewMexico", "NewYork", "NorthCarolina", "NorthDakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "RhodeIsland", "SouthCarolina", "SouthDakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "WestVirginia", "Wisconsin", "Wyoming"]
USPS = dict(zip(STATES, ["al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy"]))

# B03001 line -> column name
ORIGIN_LINES = {1: "pop", 3: "hisp", 4: "mexican", 5: "puerto_rican", 6: "cuban", 7: "dominican", 8: "central_am",
                9: "costa_rican", 10: "guatemalan", 11: "honduran", 12: "nicaraguan", 13: "panamanian", 14: "salvadoran",
                15: "oth_central_am", 16: "south_am", 17: "argentinean", 18: "bolivian", 19: "chilean", 20: "colombian",
                21: "ecuadorian", 22: "paraguayan", 23: "peruvian", 24: "uruguayan", 25: "venezuelan",
                26: "oth_south_am", 27: "oth_hisp"}
# CVAP lines of B05003 / B05003I: 18+ native and naturalized, male then female
CVAP_LINES = [9, 11, 20, 22]
VAP_LINES = [8, 19]
# five reporting groups used by the model (Central/South American are kept as sums; the country columns stay in the files)
GROUPS = ["mexican", "puerto_rican", "cuban", "dominican", "central_am", "south_am", "oth_hisp"]


# ------------------------------------------------------------------ download

def get(url: str, dest: Path, tries: int = 6) -> bool:
    """Cached polite download (<= ~1 request/s); False on 404."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            if dest.suffix == ".zip" and body[:2] != b"PK":
                raise ValueError("not a zip")
            dest.write_bytes(body)
            time.sleep(1.0)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            time.sleep(5 * (k + 1))
        except (OSError, ValueError):
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"download failed: {url}")


def lookup(end: int) -> dict[str, tuple[int, int]]:
    """table -> (sequence number, 1-based start position of cell 1) from the cached ACS lookup file."""
    dest = HIST / f"lookup{end}.txt"
    if not dest.exists():
        get(f"{SF}/{end}/documentation/user_tools/ACS_5yr_Seq_Table_Number_Lookup.txt", dest)
    lk = pd.read_csv(dest, dtype=str, encoding="latin1").fillna("")
    lk = lk[(lk["Start Position"].str.strip() != "") & lk["Table ID"].isin(TABLES)]
    return {r["Table ID"]: (int(r["Sequence Number"]), int(r["Start Position"])) for _, r in lk.iterrows()}


GEODIR = {"cty": "All_Geographies_Not_Tracts_Block_Groups", "trt": "Tracts_Block_Groups_Only"}


def legacy_jobs(end: int) -> list[tuple[str, Path]]:
    lk = lookup(end)
    jobs = []
    for state, st in USPS.items():
        for g, d in GEODIR.items():
            seqs = sorted({s for s, _ in lk.values()})
            base = f"{SF}/{end}/data/5_year_seq_by_state/{state}/{d}"
            jobs.append((f"{base}/g{end}5{st}.csv", RAW / str(end) / g / f"g{end}5{st}.csv"))
            for s in seqs:
                jobs.append((f"{base}/{end}5{st}{s:04d}000.zip", RAW / str(end) / g / f"{end}5{st}{s:04d}000.zip"))
    return jobs


def fetch(vintages: list[int]) -> None:
    for end in vintages:
        if end in LEGACY:
            miss = []
            for u, d in legacy_jobs(end):
                try:
                    if not get(u, d):
                        miss.append(u)
                except RuntimeError:                          # WAF "Request Rejected" on some all-geography files
                    miss.append(u)
                    print("failed:", u, flush=True)
            print(end, "legacy missing:", len(miss), miss[:3], flush=True)
        else:
            for t in TABLES:
                ok = get(f"{SF}/{end}/table-based-SF/data/5YRData/acsdt5y{end}-{t.lower()}.dat",
                         RAW / str(end) / f"acsdt5y{end}-{t.lower()}.dat")
                print(end, t, "ok" if ok else "MISSING", flush=True)


# ------------------------------------------------------------------ extraction

def _geo_ids(path: Path) -> pd.Series:
    """LOGRECNO -> GEOID ('<state><county>' or '<state><county><tract>') for county (050) / tract (140) summary rows."""
    g = pd.read_csv(path, header=None, dtype=str, encoding="latin1")
    g = g[(g[2].isin(["050", "140"])) & (g[3] == "00")]
    geoid = g.apply(lambda r: next((str(v).split("US")[1] for v in r.values if isinstance(v, str)
                                    and re.fullmatch(r"(05000|14000)US\d+", v)), None), axis=1)
    return pd.Series(geoid.values, index=g[4].values).dropna()


def _legacy_state(end: int, geo: str, st: str, seq: int, start: int, lines: dict[int, str]) -> pd.DataFrame:
    d = RAW / str(end) / geo
    ids = _geo_ids(d / f"g{end}5{st}.csv")
    with zipfile.ZipFile(d / f"{end}5{st}{seq:04d}000.zip") as z:
        name = next(n for n in z.namelist() if n.lower().startswith("e"))
        df = pd.read_csv(z.open(name), header=None, dtype=str, encoding="latin1", index_col=5)
    df = df.loc[df.index.intersection(ids.index)]
    # positional columns after dropping LOGRECNO (col 5): original position p>5 -> p-1
    cols = {nm: start - 1 + (ln - 1) - 1 for ln, nm in lines.items()}
    sub = pd.DataFrame({nm: pd.to_numeric(df.iloc[:, c], errors="coerce") for nm, c in cols.items()})
    sub.index = ids.loc[df.index].values
    return sub


def _legacy_table(end: int, geo: str, table: str, lines: dict[int, str]) -> pd.DataFrame:
    seq, start = lookup(end)[table]
    frames = []
    for st in USPS.values():
        if geo == "cty" and not (RAW / str(end) / "cty" / f"{end}5{st}{seq:04d}000.zip").exists():
            t = _legacy_state(end, "trt", st, seq, start, lines)          # file refused by the WAF: sum the state's tracts
            frames.append(t.groupby(t.index.str[:5]).sum(min_count=1))
        else:
            frames.append(_legacy_state(end, geo, st, seq, start, lines))
    return pd.concat(frames)


def _table_based(end: int, geo: str, table: str, lines: dict[int, str]) -> pd.DataFrame:
    T = table.upper()
    prefix = "0500000US" if geo == "cty" else "1400000US"
    use = ["GEO_ID"] + [f"{T}_E{l:03d}" for l in lines]
    parts = []
    for ch in pd.read_csv(RAW / str(end) / f"acsdt5y{end}-{table.lower()}.dat", sep="|", dtype=str, usecols=use,
                          chunksize=400_000):
        parts.append(ch[ch["GEO_ID"].str.startswith(prefix)])
    d = pd.concat(parts)
    idx = d["GEO_ID"].str.split("US").str[1].values
    d = d.drop(columns="GEO_ID").apply(pd.to_numeric, errors="coerce")
    d.columns = [lines[int(c[-3:])] for c in d.columns]
    d.index = idx
    return d


def _cvap_cells() -> dict[int, str]:
    return {8: "vap_a", 9: "nat_m", 11: "nz_m", 19: "vap_b", 20: "nat_f", 22: "nz_f"}


def build_one(end: int, geo: str) -> pd.DataFrame:
    rd = _legacy_table if end in LEGACY else _table_based
    org = rd(end, geo, "B03001", ORIGIN_LINES)
    out = org.copy()
    nh = rd(end, geo, "B03002", {3: "nh_white", 4: "nh_black", 6: "nh_asian", 1: "nh_tot"}).reindex(out.index)
    out[["nh_white", "nh_black", "nh_asian"]] = nh[["nh_white", "nh_black", "nh_asian"]]
    for tab, pre in (("B05003I", "h"), ("B05003", "a")):
        c = rd(end, geo, tab, _cvap_cells()).reindex(out.index)
        out[f"{pre}_vap"] = c["vap_a"] + c["vap_b"]
        out[f"{pre}_cvap"] = c["nat_m"] + c["nz_m"] + c["nat_f"] + c["nz_f"]
    out.index.name = "geoid"
    out = out.reset_index()
    out["vintage"] = end
    # derived sums used for validation only
    return out


def build(vintages: list[int]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for end in vintages:
        for geo, nm in (("cty", "county"), ("trt", "tract")):
            df = build_one(end, geo)
            df.to_parquet(OUT / f"{nm}_b03001_{end}.parquet", index=False)
            chk = (df[["mexican", "puerto_rican", "cuban", "dominican", "central_am", "south_am", "oth_hisp"]].sum(axis=1)
                   - df["hisp"]).abs().max()
            print(end, nm, df.shape, "origin sum vs hisp max abs diff", chk, "hisp total", int(df["hisp"].sum()),
                  "h_cvap", int(df["h_cvap"].sum()), flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    vint = [int(x) for x in sys.argv[2:]] or LEGACY + TABLE_BASED
    {"fetch": fetch, "build": build}[cmd](vint)
