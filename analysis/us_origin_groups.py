"""County table of Hispanic origin groups among citizens of voting age.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_groups.py [vintages]

Inputs  (processed/us2026/origin/): county_b03001_<end>.parquet (analysis/us_origin_data.py), pums_origin_ratio_<end>.csv
        (analysis/us_origin_pums.py).
Method  per county: origin population (B03001, 7 groups) x state citizen-adult ratio of that origin (PUMS, shrunk toward the
        national ratio with weight n/(n+60) on the state's sample count n), then rescaled so the seven groups add to the
        published county Hispanic CVAP (B05003I).  So county totals are the published ones; the split is an estimate.
Outputs county_origin_groups.csv     one row per county for the latest vintage (2024): fips, dominant origin, its share of
                                     Hispanic citizens of voting age, Hispanic share of CVAP (+ the seven group shares)
        county_origin_groups_all.csv every vintage, same columns plus vintage, group CVAP counts
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
OUT = DATA / "processed" / "us2026" / "origin"
GROUPS = ["mexican", "puerto_rican", "cuban", "dominican", "central_am", "south_am", "oth_hisp"]
LABEL = {"mexican": "Mexican", "puerto_rican": "Puerto Rican", "cuban": "Cuban", "dominican": "Dominican",
         "central_am": "Central American", "south_am": "South American", "oth_hisp": "Other Hispanic"}
SHRINK_N = 60.0


def pums_ratio(end: int) -> pd.DataFrame:
    """(state_fips int, group) -> shrunk citizen-adult ratio."""
    p = pd.read_csv(OUT / f"pums_origin_ratio_{end}.csv")
    nat = p[p["state_fips"] == 0].set_index("group")["ratio"]
    s = p[p["state_fips"] > 0].copy()
    w = s["n"] / (s["n"] + SHRINK_N)
    s["r"] = w * s["ratio"] + (1 - w) * s["group"].map(nat)
    return s.pivot(index="state_fips", columns="group", values="r").reindex(columns=GROUPS).fillna(nat.reindex(GROUPS))


def pums_end(vintage: int) -> list[tuple[int, float]]:
    """PUMS vintages (weights) used for an ACS vintage: 2022 has no PUMS download, so it averages 2020 and 2024."""
    return [(2020, .5), (2024, .5)] if vintage == 2022 else [(vintage, 1.0)]


def county_groups(end: int) -> pd.DataFrame:
    c = pd.read_parquet(OUT / f"county_b03001_{end}.parquet")
    c = c[c["geoid"].str[:2].astype(int) <= 56].copy()
    st = c["geoid"].str[:2].astype(int)
    r = sum(w * pums_ratio(e) for e, w in pums_end(end))
    pop = c[GROUPS].to_numpy(float)
    raw = pop * r.reindex(st).to_numpy()
    tot = raw.sum(axis=1)
    scale = np.where(tot > 0, c["h_cvap"].to_numpy(float) / np.where(tot > 0, tot, 1), 0.0)
    cv = raw * scale[:, None]
    out = pd.DataFrame(cv, columns=[f"cvap_{g}" for g in GROUPS])
    out.insert(0, "fips", c["geoid"].values)
    out["hisp_cvap"] = c["h_cvap"].values
    out["cvap"] = c["a_cvap"].values
    out["hisp_share_cvap"] = out["hisp_cvap"] / out["cvap"]
    sh = cv / np.where(cv.sum(axis=1) > 0, cv.sum(axis=1), 1)[:, None]
    for i, g in enumerate(GROUPS):
        out[f"share_{g}"] = sh[:, i]
    k = sh.argmax(axis=1)
    out["dominant_origin"] = np.where(out["hisp_cvap"] > 0, [LABEL[GROUPS[i]] for i in k], "")
    out["dominant_share_of_hisp_cvap"] = np.where(out["hisp_cvap"] > 0, sh.max(axis=1), np.nan)
    out["vintage"] = end
    return out


def main(ends: list[int]) -> None:
    allv = pd.concat([county_groups(e) for e in ends], ignore_index=True)
    allv.round(6).to_csv(OUT / "county_origin_groups_all.csv", index=False)
    last = allv[allv["vintage"] == max(ends)]
    cols = ["fips", "dominant_origin", "dominant_share_of_hisp_cvap", "hisp_share_cvap", "hisp_cvap", "cvap", "vintage"] + \
           [f"share_{g}" for g in GROUPS]
    last[cols].round(5).to_csv(OUT / "county_origin_groups.csv", index=False)
    print(last[cols].head(), len(last), last["dominant_origin"].value_counts().to_dict())


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [2016, 2020, 2022, 2024])
