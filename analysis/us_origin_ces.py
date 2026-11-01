"""Validated-voter vote choice by Hispanic origin from the Cooperative Election Study (CES, CC0, Harvard Dataverse).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_ces.py fetch
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_ces.py build   # -> processed/us2026/origin/ces_origin_vote.csv

CES Common Content 2016 (GDF6Z0), 2020 (doi:10.7910/DVN/E9N6PH), 2022 (PR4L8P), 2024 (X11EP6): an opt-in online panel matched to voter files.
Respondents with a vote-validated record carry `vvweight_post`.  Hispanic respondents (race == Hispanic or hispanic == Yes) are assigned
to one origin from the 'Latin heritage' check-all question (CC<yy>_hisp_1..12, 1 = ticked, 2 = not: 3 Mexico, 4 Puerto Rico, 5 Cuba, 6 Dominican Republic,
7 South America, 8 Central America); a respondent who ticks two or more of these, or none, goes to 'Hispanic other / multiple'.
Presidential vote (CC16_410a, CC20_410, CC24_410: Democratic nominee = 1 in 2020 and 2024) gives the two-party Democratic share; the standard error uses
the Kish effective sample size of the weights.  This is a survey with its own error (n of a few hundred for Cubans, Dominicans): it is
validation material for the ecological estimates, never an input of them.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(os.environ.get("BRUNDER_DATA", "~/brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us" / "ces"
OUT = DATA / "processed" / "us2026" / "origin"
FILES = {2016: "3123547?format=original", 2020: 4949558, 2022: 10140882, 2024: 12050325}
UA = "brunder/0.1 (research; recorder)"
ORIGIN_COLS = {3: "Mexican", 4: "Puerto Rican", 5: "Cuban", 6: "Dominican", 7: "South American", 8: "Central American"}
VOTE = {2016: "CC16_410a", 2020: "CC20_410", 2024: "CC24_410"}


def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for y, i in FILES.items():
        dest = RAW / (f"ces{y}.dta" if y == 2016 else f"ces{y}.csv")
        if not dest.exists():
            req = urllib.request.Request(f"https://dataverse.harvard.edu/api/access/datafile/{i}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r:
                dest.write_bytes(r.read())


def _load_2016() -> pd.DataFrame:
    """CES 2016 (Stata): voter validation CL_E2016GVM, weight commonweight_vv_post, Hispanic_origin_k check-all (same coding of k)."""
    cols = ["commonweight_vv_post", "inputstate", "race", "hispanic", "CC16_410a", "CL_E2016GVM"] + [f"Hispanic_origin_{k}" for k in ORIGIN_COLS]
    d = pd.read_stata(RAW / "ces2016.dta", columns=cols, convert_categoricals=True)
    d["inputstate"] = pd.read_stata(RAW / "ces2016.dta", columns=["inputstate"], convert_categoricals=False)["inputstate"]   # FIPS
    d = d[d["CL_E2016GVM"].astype(str).str.len().gt(0) & d["CL_E2016GVM"].notna() & d["commonweight_vv_post"].gt(0)]
    d = d[(d["race"] == "Hispanic") | (d["hispanic"] == "Yes")].copy()
    tick = pd.DataFrame({name: d[f"Hispanic_origin_{k}"] == "Yes" for k, name in ORIGIN_COLS.items()})
    d["vvweight_post"] = d["commonweight_vv_post"].astype(float)
    d["dem"] = (d["CC16_410a"] == "Hillary Clinton (Democrat)").astype(float)
    d["two"] = d["CC16_410a"].isin(["Hillary Clinton (Democrat)", "Donald Trump (Republican)"])
    d["state"] = d["inputstate"].astype(int)
    one = tick.sum(axis=1) == 1
    d["origin"] = np.where(one, tick.idxmax(axis=1), "Other / multiple")
    return d[d["two"]]


def load(year: int) -> pd.DataFrame:
    if year == 2016:
        return _load_2016()
    yy = str(year)[2:]
    cols = ["inputstate", "vvweight_post", "race", "hispanic", VOTE[year]] + [f"CC{yy}_hisp_{k}" for k in ORIGIN_COLS]
    d = pd.read_csv(RAW / f"ces{year}.csv", usecols=cols, encoding="latin1", na_values=["NA", ""])
    d = d[d["vvweight_post"].notna() & ((d["race"] == 3) | (d["hispanic"] == 1))].copy()
    tick = pd.DataFrame({name: d[f"CC{yy}_hisp_{k}"] == 1 for k, name in ORIGIN_COLS.items()})
    one = tick.sum(axis=1) == 1
    d["origin"] = np.where(one, tick.idxmax(axis=1), "Other / multiple")
    d["dem"] = (d[VOTE[year]] == 1).astype(float)
    d["two"] = d[VOTE[year]].isin([1, 2])
    d["state"] = d["inputstate"].astype(int)
    return d[d["two"]]


def share(g: pd.DataFrame) -> pd.Series:
    w = g["vvweight_post"].to_numpy()
    p = float((w * g["dem"]).sum() / w.sum())
    neff = w.sum() ** 2 / (w ** 2).sum()
    return pd.Series({"n": len(g), "n_eff": neff, "dem2p": p, "se": float(np.sqrt(p * (1 - p) / neff))})


def build() -> None:
    rows = []
    for y in VOTE:
        d = load(y)
        for name, g in [("Hispanic (all)", d)] + list(d.groupby("origin")):
            rows.append({"year": y, "origin": name, "state_fips": 0, **share(g)})
        for (st, name), g in d.groupby(["state", "origin"]):
            if len(g) >= 30:
                rows.append({"year": y, "origin": name, "state_fips": st, **share(g)})
        for st, g in d.groupby("state"):
            if len(g) >= 30:
                rows.append({"year": y, "origin": "Hispanic (all)", "state_fips": st, **share(g)})
    t = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "ces_origin_vote.csv", index=False)
    print(t[t["state_fips"] == 0].round(3).to_string())


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
