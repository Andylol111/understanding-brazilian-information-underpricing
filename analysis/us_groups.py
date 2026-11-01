"""Builds site/data/us2026_groups/{groups,counties}.json for site/groups.html: counties grouped by what the places are like.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_groups.py [--out site/data/us2026_groups]

Inputs (read only): $BRUNDER_DATA/processed/us2026/unified/county_demo.parquet (ACS 5-year, 2020-2024 vintage; Connecticut's
planning regions as the 2026 units use them), results_county.parquet (certified county results 2020-2024) and
processed/us2026/origin/county_origin_groups.csv (largest Hispanic origin among citizen adults, an ACS/PUMS estimate).
Aggregates only: nothing voter-level. Outputs:
  groups.json    dimensions and their groups (with the rule of each), tercile cut points, key regions per state (county FIPS)
  counties.json  one column per field, one entry per county: makeup, the group of each dimension, 2020/2024 president and
                 2022 governor / Senate two-party margins (D - R) / (D + R)

Five dimensions, each a split of all counties into groups that never overlap inside a dimension:
  place   big cities / suburbs / small cities and towns / rural        (population and density)
  edu     top / middle / bottom third by the share of adults with a bachelor's degree
  age     oldest / middle / youngest third by the share of residents aged 65+
  inc     top / middle / bottom third by median household income
  race    majority white (non-Hispanic) / majority Black / majority Hispanic by largest origin (Mexican, Cuban, Puerto Rican,
          other) / no majority group
Terciles are over all US counties (cut points in groups.json). Plus key regions per state (REGIONS). The live aggregation
(share counted, swing in complete counties, projected margin, turnout) runs in the page: site/groups-agg.js.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

BIG_CITY_POP, BIG_CITY_DENSITY = 500_000, 1_000
SUBURB_POP, SUBURB_DENSITY = 100_000, 250
RURAL_POP, RURAL_DENSITY = 50_000, 100
MAJORITY = 0.5

DIMENSIONS = [
    {"id": "place", "label": "Urban to rural", "groups": [
        {"id": "big_city", "label": "Big cities", "rule": f"{BIG_CITY_POP:,}+ residents and {BIG_CITY_DENSITY:,}+ per sq mi"},
        {"id": "suburb", "label": "Suburbs", "rule": f"{SUBURB_POP:,}+ residents and {SUBURB_DENSITY}+ per sq mi, not a big city"},
        {"id": "town", "label": "Small cities and towns", "rule": "neither of the others"},
        {"id": "rural", "label": "Rural", "rule": f"under {RURAL_POP:,} residents and under {RURAL_DENSITY} per sq mi"}]},
    {"id": "edu", "label": "Education", "groups": [
        {"id": "high", "label": "Most college graduates", "rule": "top third of counties by adults with a bachelor's degree"},
        {"id": "mid", "label": "Middle", "rule": "middle third"},
        {"id": "low", "label": "Fewest college graduates", "rule": "bottom third"}]},
    {"id": "age", "label": "Age profile", "groups": [
        {"id": "old", "label": "Older", "rule": "top third of counties by share of residents aged 65+"},
        {"id": "mid", "label": "Middle", "rule": "middle third"},
        {"id": "young", "label": "Younger", "rule": "bottom third"}]},
    {"id": "inc", "label": "Income", "groups": [
        {"id": "high", "label": "Higher income", "rule": "top third of counties by median household income"},
        {"id": "mid", "label": "Middle", "rule": "middle third"},
        {"id": "low", "label": "Lower income", "rule": "bottom third"}]},
    {"id": "race", "label": "Race and ethnicity", "groups": [
        {"id": "white", "label": "Majority white", "rule": "half or more of residents white, not Hispanic"},
        {"id": "black", "label": "Majority Black", "rule": "half or more of residents Black"},
        {"id": "hisp_mex", "label": "Majority Hispanic, mostly Mexican origin",
         "rule": "half or more of residents Hispanic or Latino; most Hispanic citizens of voting age are of Mexican origin"},
        {"id": "hisp_cub", "label": "Majority Hispanic, mostly Cuban origin",
         "rule": "half or more of residents Hispanic or Latino; most Hispanic citizens of voting age are of Cuban origin"},
        {"id": "hisp_pr", "label": "Majority Hispanic, mostly Puerto Rican origin",
         "rule": "half or more of residents Hispanic or Latino; most Hispanic citizens of voting age are of Puerto Rican origin"},
        {"id": "hisp_oth", "label": "Majority Hispanic, mostly of another origin",
         "rule": "half or more of residents Hispanic or Latino; most Hispanic citizens of voting age are of one other origin "
                 "(today: the ACS 'other Hispanic or Latino' group, in northern New Mexico)"},
        {"id": "hisp_mix", "label": "Majority Hispanic, mixed origin",
         "rule": "half or more of residents Hispanic or Latino; no origin is most Hispanic citizens of voting age"},
        {"id": "none", "label": "No majority group", "rule": "no group is half or more of residents"}]},
]
DIM_IDS = [d["id"] for d in DIMENSIONS]
GROUP_IDS = {d["id"]: [g["id"] for g in d["groups"]] for d in DIMENSIONS}
NOTE = "Each row is a group of places, not of people: a county's result says nothing about how any one resident voted."

# key regions per state (county FIPS). "rural" is resolved to the state's rural-class counties.
REGIONS: dict[str, list[tuple[str, str, list[str] | str]]] = {
    "ga": [("atl", "Metro Atlanta", ["13121", "13089", "13067", "13135", "13063", "13057", "13117", "13151", "13097", "13113"]),
           ("cities", "Savannah, Augusta, Macon, Columbus, Athens", ["13051", "13245", "13021", "13215", "13059"]),
           ("rural", "Rural Georgia", "rural")],
    "nc": [("triangle", "Triangle (Wake, Durham, Orange)", ["37183", "37063", "37135"]),
           ("charlotte", "Charlotte (Mecklenburg)", ["37119"]),
           ("triad", "Triad (Guilford, Forsyth)", ["37081", "37067"]),
           ("rural", "Rural North Carolina", "rural")],
    "tx": [("houston", "Houston (Harris)", ["48201"]),
           ("dfw", "Dallas-Fort Worth (Dallas, Tarrant, Collin, Denton)", ["48113", "48439", "48085", "48121"]),
           ("austin", "Austin (Travis)", ["48453"]), ("sanantonio", "San Antonio (Bexar)", ["48029"]),
           ("elpaso", "El Paso", ["48141"]),
           ("rgv", "Rio Grande Valley", ["48061", "48215", "48427", "48489"]),
           ("rural", "Rural Texas", "rural")],
    "fl": [("miami", "Miami-Dade", ["12086"]), ("southfl", "Broward and Palm Beach", ["12011", "12099"]),
           ("tampa", "Tampa Bay (Hillsborough, Pinellas)", ["12057", "12103"]),
           ("orlando", "Orlando (Orange, Osceola, Seminole)", ["12095", "12097", "12117"]),
           ("jax", "Jacksonville (Duval)", ["12031"]), ("rural", "Rural Florida", "rural")],
    "az": [("maricopa", "Phoenix area (Maricopa)", ["04013"]), ("pima", "Tucson area (Pima)", ["04019"]),
           ("rural", "Rural Arizona", "rural")],
    "pa": [("phl", "Philadelphia", ["42101"]), ("phlsub", "Philadelphia suburbs (Bucks, Chester, Delaware, Montgomery)",
                                                ["42017", "42029", "42045", "42091"]),
           ("pit", "Pittsburgh area (Allegheny)", ["42003"]), ("rural", "Rural Pennsylvania", "rural")],
    "mi": [("wayne", "Detroit / Wayne", ["26163"]), ("detsub", "Detroit suburbs (Oakland, Macomb)", ["26125", "26099"]),
           ("kent", "Grand Rapids (Kent)", ["26081"]), ("rural", "Rural Michigan", "rural")],
    "wi": [("milw", "Milwaukee", ["55079"]), ("dane", "Madison (Dane)", ["55025"]), ("rural", "Rural Wisconsin", "rural")],
    "nv": [("clark", "Las Vegas (Clark)", ["32003"]), ("washoe", "Reno (Washoe)", ["32031"]), ("rural", "Rural Nevada", "rural")],
    "mn": [("twin", "Twin Cities (Hennepin, Ramsey)", ["27053", "27123"]),
           ("twinsub", "Twin Cities suburbs", ["27003", "27037", "27163", "27139", "27019"]),
           ("rural", "Rural Minnesota", "rural")],
    "oh": [("cle", "Cleveland (Cuyahoga)", ["39035"]), ("col", "Columbus (Franklin)", ["39049"]),
           ("cin", "Cincinnati (Hamilton)", ["39061"]), ("rural", "Rural Ohio", "rural")],
    "nm": [("abq", "Albuquerque (Bernalillo)", ["35001"]), ("rural", "Rural New Mexico", "rural")],
    "ne": [("omaha", "Omaha (Douglas)", ["31055"]), ("lincoln", "Lincoln (Lancaster)", ["31109"]),
           ("rural", "Rural Nebraska", "rural")],
    "sc": [("charleston", "Charleston", ["45019"]), ("greenville", "Greenville", ["45045"]),
           ("columbia", "Columbia (Richland)", ["45079"]), ("rural", "Rural South Carolina", "rural")],
    "ca": [("la", "Los Angeles", ["06037"]),
           ("bay", "Bay Area", ["06001", "06013", "06041", "06055", "06075", "06081", "06085", "06095", "06097"]),
           ("ie", "Inland Empire (Riverside, San Bernardino)", ["06065", "06071"]), ("sd", "San Diego", ["06073"]),
           ("rural", "Rural California", "rural")],
    "va": [("nova", "Northern Virginia", ["51059", "51013", "51107", "51153", "51600", "51610", "51510", "51683", "51685"]),
           ("rural", "Rural Virginia", "rural")],
    "co": [("denver", "Denver area", ["08031", "08001", "08005", "08059"]), ("rural", "Rural Colorado", "rural")],
    "ia": [("polk", "Des Moines (Polk)", ["19153"]), ("rural", "Rural Iowa", "rural")],
    "ny": [("nyc", "New York City", ["36005", "36047", "36061", "36081", "36085"]), ("rural", "Rural New York", "rural")],
}

# elections whose two-party margins are kept per county: key -> (year, office)
ELECTIONS = {"p20": (2020, "pres"), "p24": (2024, "pres"), "g22": (2022, "gov"), "s22": (2022, "sen")}


# ------------------------------------------------------------------------------------------------------ grouping
def tercile_cuts(values: pd.Series) -> tuple[float, float]:
    """Lower and upper tercile cut points of the non-null values."""
    v = values.dropna()
    return float(v.quantile(1 / 3)), float(v.quantile(2 / 3))


def third(value: float, cuts: tuple[float, float], names: tuple[str, str, str]) -> str | None:
    """names = (top, middle, bottom): top is at or above the upper cut, bottom at or below the lower one."""
    if value is None or pd.isna(value):
        return None
    lo, hi = cuts
    return names[0] if value >= hi else names[2] if value <= lo else names[1]


def place_class(pop: float, density: float) -> str:
    if pop >= BIG_CITY_POP and density >= BIG_CITY_DENSITY:
        return "big_city"
    if pop >= SUBURB_POP and density >= SUBURB_DENSITY:
        return "suburb"
    if pop < RURAL_POP and density < RURAL_DENSITY:
        return "rural"
    return "town"


ORIGIN_GROUP = {"Mexican": "hisp_mex", "Cuban": "hisp_cub", "Puerto Rican": "hisp_pr"}
ORIGIN_MAJORITY = 0.5   # an origin names the county only when it is half or more of Hispanic citizens of voting age


def race_class(white: float, black: float, hisp: float, origin: str | None = None, origin_share: float | None = None) -> str:
    """origin, origin_share: the largest origin among the county's Hispanic citizens of voting age and its share of them
    (ACS composition columns of county_origin_groups.csv: dominant_origin, dominant_share_of_hisp_cvap)."""
    if hisp >= MAJORITY:
        if origin is None or origin_share is None or not origin_share >= ORIGIN_MAJORITY:
            return "hisp_mix"
        return ORIGIN_GROUP.get(origin, "hisp_oth")
    if black >= MAJORITY:
        return "black"
    if white >= MAJORITY:
        return "white"
    return "none"


def classify(demo: pd.DataFrame, cuts: dict[str, tuple[float, float]] | None = None) -> tuple[pd.DataFrame, dict]:
    """demo: one row per county (fips, pop_total, density, ba, age65, medinc, white_nh, black, hisp; optional origin). Returns
    the frame with one class column per dimension (place, edu, age, inc, race; None where an input is missing) and the cuts."""
    d = demo.copy()
    cuts = cuts or {"edu": tercile_cuts(d["ba"]), "age": tercile_cuts(d["age65"]), "inc": tercile_cuts(d["medinc"])}
    d["place"] = [place_class(p, x) if pd.notna(p) and pd.notna(x) else None for p, x in zip(d["pop_total"], d["density"])]
    d["edu"] = [third(v, cuts["edu"], ("high", "mid", "low")) for v in d["ba"]]
    d["age"] = [third(v, cuts["age"], ("old", "mid", "young")) for v in d["age65"]]
    d["inc"] = [third(v, cuts["inc"], ("high", "mid", "low")) for v in d["medinc"]]
    origin = d["origin"] if "origin" in d else [None] * len(d)
    oshare = d["origin_share"] if "origin_share" in d else [None] * len(d)
    d["race"] = [race_class(w, b, h, o if isinstance(o, str) else None, s if pd.notna(s) else None)
                 if pd.notna(w) and pd.notna(b) and pd.notna(h) else None
                 for w, b, h, o, s in zip(d["white_nh"], d["black"], d["hisp"], origin, oshare)]
    return d, cuts


def resolve_regions(classes: pd.DataFrame, regions=REGIONS) -> dict[str, list[dict]]:
    """{state: [{id, label, fips}]}; "rural" = the state's rural-class counties. Regions without a county in the table are dropped."""
    have = set(classes["fips"])
    out: dict[str, list[dict]] = {}
    for st, defs in regions.items():
        rows = []
        for rid, label, spec in defs:
            if spec == "rural":
                fips = sorted(classes.loc[(classes["st"] == st) & (classes["place"] == "rural"), "fips"])
            else:
                fips = [f for f in spec if f in have]
            if fips:
                rows.append({"id": rid, "label": label, "fips": fips})
        if rows:
            out[st] = rows
    return out


def margins(res: pd.DataFrame, year: int, office: str) -> dict[str, float]:
    """County two-party margin (D - R) / (D + R) of one election, from results_county (s2 = D / (D + R))."""
    r = res[(res["year"] == year) & (res["office"] == office) & res["s2"].notna()]
    return dict(zip(r["fips"], (2 * r["s2"] - 1).round(4)))


def county_table(d: pd.DataFrame, res: pd.DataFrame) -> dict:
    """Column-wise county table for the page: makeup, the class index of each dimension (cls[i][k]: index into
    DIMENSIONS[k].groups, null where an input is missing) and the two-party margins of ELECTIONS."""
    el = {k: margins(res, y, o) for k, (y, o) in ELECTIONS.items()}
    r4 = lambda s: [None if pd.isna(x) else round(float(x), 4) for x in s]  # noqa: E731
    return {"fips": list(d["fips"]), "st": list(d["st"]),
            "pop": [int(x) for x in d["pop_total"]], "density": [round(float(x), 1) for x in d["density"]],
            "ba": r4(d["ba"]), "age65": r4(d["age65"]), "medinc": [None if pd.isna(x) else int(x) for x in d["medinc"]],
            "white": r4(d["white_nh"]), "black": r4(d["black"]), "hisp": r4(d["hisp"]),
            "cls": [[GROUP_IDS[DIM_IDS[i]].index(c) if isinstance(c, str) else None for i, c in enumerate(row)]
                    for row in zip(*[d[k] for k in DIM_IDS])],
            **{k: [v.get(f) for f in d["fips"]] for k, v in el.items()}}


def build(demo: pd.DataFrame, res: pd.DataFrame) -> tuple[dict, dict]:
    """demo: one county_demo row per county; res: results_county. Returns (groups.json, counties.json)."""
    d, cuts = classify(demo.sort_values("fips").reset_index(drop=True))
    groups_json = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE,
                   "source": "ACS 5-year estimates 2020-2024 (US Census Bureau; Hispanic origin of citizen adults estimated from ACS "
                             "and PUMS); certified county results 2020-2024",
                   "elections": {k: {"year": y, "office": o} for k, (y, o) in ELECTIONS.items()},
                   "dimensions": DIMENSIONS, "cuts": {k: [round(v, 4) for v in c] for k, c in cuts.items()},
                   "regions": resolve_regions(d)}
    return groups_json, county_table(d, res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data" / "us2026_groups")
    ap.add_argument("--data", type=Path, default=None, help="folder with county_demo.parquet and results_county.parquet")
    ap.add_argument("--origin", type=Path, default=None, help="county_origin_groups.csv (default $BRUNDER_DATA/processed/us2026/origin)")
    a = ap.parse_args(argv)
    root = Path(os.path.expanduser(os.environ.get("BRUNDER_DATA", "~/brunder-data-local"))) / "processed" / "us2026"
    base = a.data or root / "unified"
    demo = pd.read_parquet(base / "county_demo.parquet")
    res = pd.read_parquet(base / "results_county.parquet")
    # 2026 vintage (ACS 2020-2024); a county missing from it (none today) falls back to the 2024 row
    d26 = demo[demo["year"] == 2026]
    demo = pd.concat([d26, demo[(demo["year"] == 2024) & ~demo["fips"].isin(d26["fips"])]])
    origin = pd.read_csv(a.origin or root / "origin" / "county_origin_groups.csv", dtype={"fips": str})
    cols = {"dominant_origin": "origin", "dominant_share_of_hisp_cvap": "origin_share"}   # ACS composition columns only
    demo = demo.merge(origin[["fips", *cols]].rename(columns=cols), on="fips", how="left")
    g, c = build(demo, res)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, obj in (("groups.json", g), ("counties.json", c)):
        (a.out / name).write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"{len(c['fips'])} counties, {sum(map(len, g['regions'].values()))} regions -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
