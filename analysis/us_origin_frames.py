"""Tract frames for the origin-group model: CVAP by group + two-party votes, one file per election year.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_frames.py fetch   # reallocated votes (CC0)
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_frames.py build

Votes: Fekrazad, "Reallocating U.S. Election Results (2016-2024) from Precincts to Census Geographies", Harvard Dataverse
doi:10.7910/DVN/Z8TSH3 (CC0 1.0).  Official precinct results (2016: VEST, 2020: MEDSL, 2024: New York Times precinct map)
spread to census tracts in proportion to household population (2016, 2020 'RLCR' main method; 2024 'landcover' method, the
closest published to RLCR).  Contiguous US only.  The tract numbers are model-allocated, not counted: a precinct that
straddles tracts gives each the same party share, which smooths real differences between neighbouring tracts.

Tract geography and ACS vintage (tract boundaries change in 2020, so the ACS vintage must share the votes' geography):
  2016 votes: 2010 tracts      <- ACS 2012-16 (ending 2016)
  2020 votes: 2020 tracts      <- ACS 2018-22 (ending 2022, centred on 2020)
  2024 votes: 2020 tracts      <- ACS 2020-24 (ending 2024)
Group CVAP of a tract (11 groups): Hispanic origin population (B03001) x state citizen-adult ratio of that origin (PUMS), raked to the
tract's published Hispanic CVAP (B05003I); non-Hispanic white / Black / Asian / other population (B03002) x state ratio of that race
(PUMS), raked to published total CVAP (B05003) minus Hispanic CVAP.
"""
from __future__ import annotations

import os
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
OUT = DATA / "processed" / "us2026" / "origin"
REAL = DATA / "raw" / "us" / "reallocated"
UA = "brunder/0.1 (research; recorder)"
ZIP_URL = "https://dataverse.harvard.edu/api/access/datafile/11111135"
T24_URL = "https://dataverse.harvard.edu/api/access/datafile/14308847?format=original"
HISP = ["mexican", "puerto_rican", "cuban", "dominican", "central_am", "south_am", "oth_hisp"]
NH = ["nh_white", "nh_black", "nh_asian", "nh_other"]
GROUPS = HISP + NH
ACS_END = {2016: 2016, 2020: 2022, 2024: 2024}
PUMS_ENDS = {2016: [(2016, 1.0)], 2020: [(2020, 1.0)], 2022: [(2020, .5), (2024, .5)], 2024: [(2024, 1.0)]}
SHRINK_N = 60.0

# analysis areas (counties, as 5-digit FIPS) inside a state; every other tract is "<st>-rest".  Area = pooling unit of the model.
AREAS = {
    "Miami-Dade": ["12086"], "Broward": ["12011"], "Palm Beach": ["12099"], "Orlando (Orange/Osceola)": ["12095", "12097"],
    "Tampa Bay (Hillsborough/Pinellas)": ["12057", "12103"], "Duval": ["12031"],
    "Harris": ["48201"], "Bexar": ["48029"], "Rio Grande Valley (Hidalgo/Cameron/Starr/Willacy)": ["48215", "48061", "48427", "48489"],
    "El Paso": ["48141"], "Webb (Laredo)": ["48479"], "Dallas/Tarrant": ["48113", "48439"], "Travis": ["48453"],
    "Maricopa": ["04013"], "Pima": ["04019"], "Clark": ["32003"],
    "New York City": ["36005", "36047", "36061", "36081", "36085"], "Long Island (Nassau/Suffolk)": ["36059", "36103"],
    "Hudson/Passaic/Essex/Union NJ": ["34017", "34031", "34013", "34039"], "Middlesex/Monmouth NJ": ["34023", "34025"],
    "Los Angeles": ["06037"], "San Diego": ["06073"], "Orange CA": ["06059"], "Inland Empire": ["06065", "06071"],
    "Central Valley (Fresno/Kern/Tulare)": ["06019", "06029", "06107"], "Bay Area": ["06001", "06075", "06085", "06081"],
    "Cook": ["17031"], "Philadelphia": ["42101"], "Lehigh Valley/Berks": ["42077", "42011", "42095"],
    "Bernalillo": ["35001"], "Denver metro": ["08031", "08001", "08005", "08059"],
    "Atlanta core (Fulton/Gwinnett/DeKalb/Cobb)": ["13121", "13135", "13089", "13067"], "Mecklenburg/Wake": ["37119", "37183"],
    "Suffolk/Middlesex MA": ["25025", "25017"], "Fairfax/Prince William": ["51059", "51153", "51600"],
    "Washington DC": ["11001"], "Providence": ["44007"], "Hartford/New Haven/Fairfield CT": ["09001", "09003", "09009"],
}


def area_of(fips5: pd.Series) -> pd.Series:
    m = {c: a for a, cs in AREAS.items() for c in cs}
    return fips5.map(m).fillna("rest-" + fips5.str[:2])


def fetch() -> None:
    REAL.mkdir(parents=True, exist_ok=True)
    for url, dest in ((ZIP_URL, REAL / "main_method.zip"), (T24_URL, REAL / "tracts2024_landcover.tab")):
        if not dest.exists():
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r:
                dest.write_bytes(r.read())
    with zipfile.ZipFile(REAL / "main_method.zip") as z:
        for y in (2016, 2020):
            n = f"Contiguous USA - Main Method/Census Tracts/tracts-{y}-RLCR.csv"
            if not (REAL / n).exists():
                z.extract(n, REAL)


def votes(year: int) -> pd.DataFrame:
    """geoid, votes_d, votes_r (presidential, tract-reallocated)."""
    if year == 2024:
        t = pd.read_csv(REAL / "tracts2024_landcover.tab", dtype={"tract_GEOID": str}, encoding="utf-8-sig")
        return t.rename(columns={"tract_GEOID": "geoid", "votes_dem": "votes_d", "votes_rep": "votes_r"})[
            ["geoid", "votes_d", "votes_r"]]
    t = pd.read_csv(REAL / f"Contiguous USA - Main Method/Census Tracts/tracts-{year}-RLCR.csv", dtype={"tract_GEOID": str},
                    encoding="utf-8-sig")
    yy = str(year)[2:]
    d_cols = [c for c in t.columns if c.upper() in (f"G{yy}PREDCLI", f"G{yy}PREDBID")]       # 2016 repeats names in mixed case
    r_cols = [c for c in t.columns if c.upper() == f"G{yy}PRERTRU"]
    return pd.DataFrame({"geoid": t["tract_GEOID"], "votes_d": t[d_cols].sum(axis=1), "votes_r": t[r_cols].sum(axis=1)})


def ratios(vintage: int) -> pd.DataFrame:
    """(state_fips) x group shrunk citizen-adult ratio, PUMS."""
    tot = None
    for e, w in PUMS_ENDS[vintage]:
        p = pd.read_csv(OUT / f"pums_origin_ratio_{e}.csv")
        nat = p[p["state_fips"] == 0].set_index("group")["ratio"]
        s = p[p["state_fips"] > 0].copy()
        wt = s["n"] / (s["n"] + SHRINK_N)
        s["r"] = wt * s["ratio"] + (1 - wt) * s["group"].map(nat)
        r = s.pivot(index="state_fips", columns="group", values="r").reindex(columns=GROUPS)
        r = r.fillna(nat.reindex(GROUPS)) * w
        tot = r if tot is None else tot.add(r, fill_value=0)
    return tot


def group_cvap(tr: pd.DataFrame, vintage: int) -> pd.DataFrame:
    r = ratios(vintage)
    st = tr["geoid"].str[:2].astype(int)
    rr = r.reindex(st).reset_index(drop=True)
    out = pd.DataFrame(index=tr.index)
    hpop = tr[HISP].to_numpy(float)
    raw = hpop * rr[HISP].to_numpy()
    tot = raw.sum(axis=1)
    sc = np.where(tot > 0, tr["h_cvap"].to_numpy(float) / np.where(tot > 0, tot, 1), 0.0)
    for i, g in enumerate(HISP):
        out[g] = raw[:, i] * sc
    nh_pop = pd.DataFrame({"nh_white": tr["nh_white"], "nh_black": tr["nh_black"], "nh_asian": tr["nh_asian"]})
    nh_pop["nh_other"] = (tr["pop"] - tr["hisp"] - nh_pop.sum(axis=1)).clip(lower=0)
    rawn = nh_pop.to_numpy(float) * rr[NH].to_numpy()
    totn = rawn.sum(axis=1)
    nh_cvap = (tr["a_cvap"] - tr["h_cvap"]).clip(lower=0).to_numpy(float)
    scn = np.where(totn > 0, nh_cvap / np.where(totn > 0, totn, 1), 0.0)
    for i, g in enumerate(NH):
        out[g] = rawn[:, i] * scn
    return out


def build_year(year: int) -> pd.DataFrame:
    vint = ACS_END[year]
    tr = pd.read_parquet(OUT / f"tract_b03001_{vint}.parquet")
    tr = tr.dropna(subset=["pop", "a_cvap", "h_cvap", "nh_white"]).reset_index(drop=True)
    cv = group_cvap(tr, vint)
    f = pd.concat([tr[["geoid"]], cv.add_prefix("c_")], axis=1)
    f["cvap"] = cv.sum(axis=1)
    v = votes(year)
    f = f.merge(v, on="geoid", how="inner")
    f = f[(f["cvap"] >= 50) & f["votes_d"].notna() & f["votes_r"].notna()].copy()
    f["state_fips"] = f["geoid"].str[:2]
    f["county"] = f["geoid"].str[:5]
    f["area"] = area_of(f["county"])
    f["year"] = year
    return f.reset_index(drop=True)


def build() -> None:
    for y in (2016, 2020, 2024):
        f = build_year(y)
        f.to_parquet(OUT / f"tract_frame_{y}.parquet", index=False)
        print(y, len(f), "tracts; D", int(f["votes_d"].sum()), "R", int(f["votes_r"].sum()), "CVAP", int(f["cvap"].sum()),
              "areas", f["area"].nunique(), flush=True)


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
