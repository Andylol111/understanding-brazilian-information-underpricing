"""State x Hispanic-origin citizen-adult ratios from the public ACS 5-year PUMS (Census Bureau, public domain).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_pums.py [fetch] [build] [vintages]

ACS publishes Hispanic origin (B03001) and Hispanic citizens of voting age (B05003I) but not their cross-tabulation, and the
CVAP special tabulation has no origin split.  The PUMS person file has both (HISP detailed origin, CIT, AGEP, PWGTP), so for each
state x origin group g the ratio  r = weighted citizens aged 18+ / weighted persons  is estimated, with the sample count.
analysis/us_origin_groups.py multiplies county origin populations by r and rescales the seven groups to the published county
Hispanic CVAP (B05003I), so county totals stay exactly the published ones and only the split between origins comes from PUMS.

Raw: $BRUNDER_DATA/raw/us/census/pums/pus<end>.zip (csv_pus.zip, ~2 GB each, one request each).  Output:
$BRUNDER_DATA/processed/us2026/origin/pums_origin_ratio_<end>.csv
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.csv as pacsv

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us" / "census" / "pums"
OUT = DATA / "processed" / "us2026" / "origin"
UA = "brunder/0.1 (research; recorder)"
# PUMS HISP code -> model group (01 = not Hispanic)
HISP_GROUP = {2: "mexican", 3: "puerto_rican", 4: "cuban", 5: "dominican", 6: "central_am", 7: "central_am", 8: "central_am",
              9: "central_am", 10: "central_am", 11: "central_am", 12: "central_am", 13: "south_am", 14: "south_am",
              15: "south_am", 16: "south_am", 17: "south_am", 18: "south_am", 19: "south_am", 20: "south_am",
              21: "south_am", 22: "south_am", 23: "oth_hisp", 24: "oth_hisp"}
# non-Hispanic persons by RAC1P: 1 white alone, 2 Black alone, 6 Asian alone, anything else "nh_other"
NH_GROUP = {1: "nh_white", 2: "nh_black", 6: "nh_asian"}


def fetch(end: int) -> None:
    import urllib.request
    dest = RAW / f"pus{end}.zip"
    if dest.exists() and dest.stat().st_size > 1e9:
        return
    RAW.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"https://www2.census.gov/programs-surveys/acs/data/pums/{end}/5-Year/csv_pus.zip",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 22):
            f.write(chunk)


def build(end: int) -> pd.DataFrame:
    STCOL = "STATE" if end >= 2022 else "ST"
    parts = []
    with zipfile.ZipFile(RAW / f"pus{end}.zip") as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".csv")):
            opts = pacsv.ConvertOptions(include_columns=[STCOL, "AGEP", "CIT", "HISP", "RAC1P", "PWGTP"])
            with z.open(name) as f:
                t = pacsv.open_csv(f, convert_options=opts).read_all().to_pandas()
            t = t.rename(columns={STCOL: "ST"})
            t["group"] = np.where(t["HISP"] > 1, t["HISP"].map(HISP_GROUP),
                                  t["RAC1P"].map(NH_GROUP).fillna("nh_other"))
            t = t.drop(columns=["HISP", "RAC1P"])
            parts.append(t)
            print(end, name, len(t), flush=True)
    d = pd.concat(parts)
    d["cvap"] = ((d["AGEP"] >= 18) & (d["CIT"] != 5)) * d["PWGTP"]
    d["adult"] = (d["AGEP"] >= 18) * d["PWGTP"]
    d["citizen"] = (d["CIT"] != 5) * d["PWGTP"]
    g = d.groupby(["ST", "group"]).agg(pop=("PWGTP", "sum"), adult=("adult", "sum"), cvap=("cvap", "sum"),
                                       n=("PWGTP", "size")).reset_index()
    nat = d.groupby("group").agg(pop=("PWGTP", "sum"), adult=("adult", "sum"), cvap=("cvap", "sum"),
                                 n=("PWGTP", "size")).reset_index()
    nat["ST"] = 0
    out = pd.concat([g, nat]).rename(columns={"ST": "state_fips"})
    out["ratio"] = out["cvap"] / out["pop"]
    out["vintage"] = end
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / f"pums_origin_ratio_{end}.csv", index=False)
    return out


if __name__ == "__main__":
    ends = [int(x) for x in sys.argv[2:]] or [2016, 2020, 2024]
    for e in ends:
        if sys.argv[1] == "fetch":
            fetch(e)
        else:
            build(e)
