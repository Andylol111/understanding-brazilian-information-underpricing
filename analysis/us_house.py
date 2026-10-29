"""US House 2026 (display and analysis only; nothing here is registered) -> site/data/us2026/house/.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_house.py [geo] [data] [markets]   # default: all

geo      districts_2026.topo.json (the 435 districts in force for 2026-11-03) and districts_2024.topo.json (119th
         Congress lines), object `districts`, properties id ("TX-07", at-large "AK-AL"), st, redrawn.
data     districts.json (2026 lines), districts_2024.json (2024 lines), flows.json (redrawn states), swing.json
         (counties), earlyvote.json (TX/FL/NC by 2026 district).
markets  markets.json: Polymarket and Kalshi House district markets, VoteHub district polls, the generic-ballot
         average and the model-only district baseline (also written into districts.json).

Sources (all public domain or open licence; docs/us_midterms.md, "US House districts"):
  - Census Bureau 119th / 120th Congressional District block equivalency files (rdo/mapping-files), 2020 P.L.
    94-171 block populations (redrawn states only), TIGER/Line 2026 cd120 and 2024 cartographic boundary cd119 and
    state shapes: public domain.
  - MIT Election Data and Science Lab 2024 precinct returns (github.com/MEDSL/2024-elections-official; CC0 1.0) and
    County Presidential Returns 2000-2024 (CC0 1.0, already cached under raw/us/medsl).
  - FEC: 2022 official House results (federalelections2022.xlsx), 2024 congressional candidates on the general
    election ballot (incumbent flag), 2026 candidate master (cn26): public domain.
  - VoteHub Polling API (CC BY 4.0); Polymarket public data; Kalshi public API (© Kalshi).
Downloads are cached under $BRUNDER_DATA/raw/us/house (and raw/us/medsl24, shared with brunder.us).
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import kalshi, us

OUT = us.ROOT_DIR / "site" / "data" / "us2026" / "house"
CACHE = us.US_RAW / "house"
MEDSL_CACHE = us.US_RAW / "medsl24"
TODAY = pd.Timestamp.now(tz="UTC").normalize()
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
MAX_BYTES = 2_000_000

BEF = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files"
BEF119 = f"{BEF}/2025/119-congressional-district-befs/cd119.zip"
BEF120 = f"{BEF}/2027/120-congressional-district-befs/cd120.zip"
PL = "https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171"
CB_CD119 = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip"
CB_STATE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
TL_CD120 = "https://www2.census.gov/geo/tiger/TIGER2026/CD/tl_2026_{fips}_cd120.zip"
FEC22 = "https://www.fec.gov/documents/5676/federalelections2022.xlsx"
FEC24_CANDS = "https://www.fec.gov/documents/5548/2024congressgecands.xlsx"
FEC26_CN = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
COUNTYPRES = us.US_RAW / "medsl" / "countypres_2000-2024.csv"

FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "12": "FL",
        "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA",
        "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
        "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK",
        "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT",
        "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY"}
ABBR = {v: k for k, v in FIPS.items()}
STATE_NAME = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
              "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
              "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
              "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
              "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
              "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
              "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
              "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
              "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
              "Wisconsin": "WI", "Wyoming": "WY"}
PL_NAME = {"AL": "Alabama", "CA": "California", "FL": "Florida", "LA": "Louisiana", "NC": "North_Carolina",
           "OH": "Ohio", "TN": "Tennessee", "TX": "Texas", "UT": "Utah"}
# 2026 lines differ from the 119th Congress (2024) lines in exactly these states (Census CD120 BEF vs CD119 BEF;
# checked in data()). Missouri enacted a 2025 map but the 2022 map is used on 2026-11-03 (US Supreme Court stay of
# 2026-09-25); the Census 120th BEF has Missouri on its 2022 lines, so it is not redrawn here.
REDRAWN = ("AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT")
# states whose 2024 lines differ from 2022 (court-ordered or new maps before 2024): no 2022->2024 House comparison
LINES_CHANGED_2024 = ("AL", "GA", "LA", "NC", "NY")
NAT_PRES24_D2 = None   # computed in data() from the county file (two-party Harris share)

SRC = {
    "bef": {"name": "U.S. Census Bureau, 119th and 120th Congressional District block equivalency files",
            "url": "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/", "licence": "public domain"},
    "pl": {"name": "U.S. Census Bureau, 2020 Census P.L. 94-171 block populations (redrawn states)",
           "url": PL, "licence": "public domain"},
    "tiger": {"name": "U.S. Census Bureau, TIGER/Line 2026 cd120 and 2024 cartographic boundary cd119 / states",
              "url": "https://www2.census.gov/geo/tiger/", "licence": "public domain"},
    "medsl_prec": {"name": "MIT Election Data and Science Lab, 2024 precinct-level returns (president, U.S. House)",
                   "url": "https://github.com/MEDSL/2024-elections-official", "licence": "CC0 1.0"},
    "medsl_county": {"name": "MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024",
                     "url": "https://doi.org/10.7910/DVN/VOQCHQ", "licence": "CC0 1.0"},
    "fec": {"name": "FEC, Federal Elections 2022 (official House results); 2024 congressional general-election "
                    "candidates (incumbent flag); 2026 candidate master file",
            "url": "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/",
            "licence": "public domain"},
    "votehub": {"name": "VoteHub Polling API (House and generic-ballot polls)", "url": "https://votehub.com/polls/api/",
                "licence": "CC BY 4.0"},
    "polymarket": {"name": "Polymarket Gamma API (House election winner events)", "url": "https://polymarket.com",
                   "licence": "public market data"},
    "kalshi": {"name": "© Kalshi public API (House party series)", "url": "https://kalshi.com",
               "licence": "© Kalshi (see docs/kalshi.md)"},
}


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x) else round(float(x), nd)


def _write(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / name
    f.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"{f.relative_to(us.ROOT_DIR)}: {f.stat().st_size / 1e6:.2f} MB")
    assert f.stat().st_size < MAX_BYTES, f"{name} over {MAX_BYTES} bytes"
    return f


def _get(url: str, name: str | None = None, sub: str = "") -> Path:
    dest = CACHE / sub / (name or url.rsplit("/", 1)[1])
    us._fetch(url, dest)
    return dest


def did(st: str, cd) -> str:
    """District id "TX-07"; at-large seats "AK-AL"."""
    cd = int(cd)
    return f"{st}-AL" if cd == 0 else f"{st}-{cd:02d}"


# ------------------------------------------------------------------ blocks: county x 2024 district x 2026 district

def _bef(path: Path, member: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        return pd.read_csv(z.open(member), dtype=str, usecols=["GEOID", "CDFP"])


def _pl_pop(st: str) -> pd.DataFrame:
    """Block GEOID -> 2020 census population (POP100, field 91 of the P.L. 94-171 geographic header, SUMLEV 750)."""
    f = _get(f"{PL}/{PL_NAME[st]}/{st.lower()}2020.pl.zip", sub="pl")
    with zipfile.ZipFile(f) as z:
        geo = next(n for n in z.namelist() if "geo" in n)
        g = pd.read_csv(z.open(geo), sep="|", header=None, dtype=str, encoding="latin-1", usecols=[2, 9, 90])
    g = g[g[2] == "750"]
    return pd.DataFrame({"GEOID": g[9].values, "pop": g[90].astype(int).values})


def pieces() -> pd.DataFrame:
    """One row per (county, 2024 district, 2026 district): census blocks and 2020 population (population only in
    redrawn states). Water-only blocks (district "ZZ") are left out. Cached as raw/us/house/pieces.csv."""
    out = CACHE / "pieces.csv"
    if out.exists():
        p = pd.read_csv(out, dtype={"county": str, "cd119": str, "cd120": str})
        return p[(p["cd119"] != "ZZ") & (p["cd120"] != "ZZ")].reset_index(drop=True)
    f119, f120 = _get(BEF119), _get(BEF120)
    b119 = _bef(f119, "NationalCD119.txt")
    with zipfile.ZipFile(f119) as z:      # state files replace the national file where a 2024 map was redrawn
        for m in z.namelist():
            if re.match(r"\d\d_[A-Z]{2}_CD119\.txt$", m):
                b119 = pd.concat([b119[b119["GEOID"].str[:2] != m[:2]], _bef(f119, m)])
    b = b119.merge(_bef(f120, "NationalCD120.txt"), on="GEOID", suffixes=("119", "120"), how="outer")
    b = b.rename(columns={"CDFP119": "cd119", "CDFP120": "cd120"})
    b["st"] = b["GEOID"].str[:2].map(FIPS)
    b = b[b["st"].notna()]
    changed = sorted(b.loc[b["cd119"] != b["cd120"], "st"].unique())
    assert tuple(changed) == REDRAWN, f"states with new 2026 lines: {changed}"
    pops = pd.concat([_pl_pop(s) for s in REDRAWN])
    b = b.merge(pops, on="GEOID", how="left")
    b["county"] = b["GEOID"].str[:5]
    p = b.groupby(["st", "county", "cd119", "cd120"]).agg(blocks=("GEOID", "size"), pop=("pop", "sum")).reset_index()
    p.loc[~p["st"].isin(REDRAWN), "pop"] = np.nan
    p.to_csv(out, index=False)
    return pieces()


# ------------------------------------------------------------------ votes

def _not_cand(s: pd.Series) -> pd.Series:
    """Rows that are not candidates: brunder.us._NOT_A_CANDIDATE plus any "... TOTAL ..." / "BALLOTS CAST" label
    (the county file's TOTAL VOTES CAST, Maine's Total Ballots Cast)."""
    u = s.fillna("").astype(str).str.strip().str.upper()
    return u.str.match(us._NOT_A_CANDIDATE) | u.str.contains(r"\bTOTAL\b|BALLOTS CAST|UNDERVOTE|OVERVOTE|BLANK")


def _county_pres() -> dict[int, pd.DataFrame]:
    """2020 and 2024 president by county (MEDSL county file): fips -> d, r, t. TOTAL rows where a county has them."""
    d = pd.read_csv(COUNTYPRES, dtype={"county_fips": str})
    d = d[d["year"].isin([2020, 2024]) & d["county_fips"].notna() & ~_not_cand(d["candidate"])].copy()
    d["fips"] = d["county_fips"].str.replace(r"\.0$", "", regex=True).str.zfill(5).replace(us._FIPS_RENAMED)
    tot = d["mode"].fillna("TOTAL").str.upper() == "TOTAL"
    has = tot.groupby([d["year"], d["fips"]]).transform("any").astype(bool)
    d = d[(has & tot) | (~has & ~tot)]
    out = {}
    for y, g in d.groupby("year"):
        p = g.pivot_table(index="fips", columns="party", values="candidatevotes", aggfunc="sum").fillna(0)
        out[int(y)] = pd.DataFrame({"d": p["DEMOCRAT"], "r": p["REPUBLICAN"],
                                    "t": g.groupby("fips")["candidatevotes"].sum()})
    return out


def _dist_no(x, n_seats: int) -> int | None:
    if n_seats == 1:
        return 0
    m = re.match(r"^0*(\d+)$", str(x).strip())
    return int(m.group(1)) if m else None


def _precincts(st: str, n_seats: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """MEDSL 2024 precinct rows of one state -> (pres by county x 2024 district, House candidates by district).

    Mode rule per precinct and office: TOTAL rows where they carry votes, else the sum of the other modes. Each
    precinct's presidential votes are split over the House districts on its ballot in proportion to its House votes
    (almost always one district); precincts without a House contest are left out of the within-county shares."""
    z = zipfile.ZipFile(io.BytesIO(us._fetch(f"{us.MEDSL24}/individual_states/{st.lower()}24.zip",
                                             MEDSL_CACHE / f"{st.lower()}24.zip")))
    cols = ["precinct", "office", "party_simplified", "mode", "votes", "county_fips", "jurisdiction_fips",
            "candidate", "district", "stage", "special"]
    parts = []
    for ch in pd.read_csv(z.open(z.namelist()[0]), dtype=str, chunksize=500_000, usecols=lambda c: c in cols):
        ch = ch[ch["office"].isin(["US PRESIDENT", "US HOUSE"]) & (ch["stage"].fillna("GEN") == "GEN")]
        parts.append(ch)
    df = pd.concat(parts, ignore_index=True)
    # special elections are dropped, unless a district has no regular race in the file (MEDSL flags TX-18's 2024
    # regular race as special)
    sp = df["special"].fillna("FALSE").str.upper() == "TRUE"
    reg = (~sp).groupby([df["office"], df["district"].fillna("")]).transform("any").astype(bool)
    df = df[~sp | ((df["office"] == "US HOUSE") & ~reg)]
    df = df[~_not_cand(df["candidate"])]
    df = df[~df["precinct"].fillna("").str.upper().str.contains("COUNTY TOTAL")]
    df["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0.0)
    df["fips"] = df["county_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5) \
        .replace(us._FIPS_RENAMED)
    df["pk"] = df["fips"] + "|" + df["jurisdiction_fips"].fillna("") + "|" + df["precinct"].fillna("")
    tot = df["mode"].fillna("TOTAL").str.upper() == "TOTAL"
    has = (tot & (df["votes"] > 0)).groupby([df["pk"], df["office"]]).transform("any").astype(bool)
    df = df[(has & tot) | (~has & ~tot)]
    pres, house = df[df["office"] == "US PRESIDENT"].copy(), df[df["office"] == "US HOUSE"].copy()
    cand = pres["candidate"].astype(str).str.upper()
    pres["d"] = pres["votes"].where(cand.str.contains("HARRIS"), 0.0)
    pres["r"] = pres["votes"].where(cand.str.contains("TRUMP"), 0.0)
    pu = pres.groupby(["pk", "fips"])[["d", "r", "votes"]].sum().rename(columns={"votes": "t"}).reset_index()
    house["cd"] = house["district"].map(lambda x: _dist_no(x, n_seats))
    house = house[house["cd"].notna()]
    hw = house.groupby(["pk", "cd"])["votes"].sum()
    hw = (hw / hw.groupby(level=0).transform("sum")).rename("w").reset_index()
    hw = hw[hw["w"].notna()]
    a = pu.merge(hw, on="pk")
    for c in ("d", "r", "t"):
        a[c] = a[c] * a["w"]
    by = a.groupby(["fips", "cd"])[["d", "r", "t"]].sum().reset_index()
    # precincts without a House contest (e.g. a district whose race was not on the ballot): cd = -1 per county
    un = pu[~pu["pk"].isin(hw["pk"])].groupby("fips")[["d", "r", "t"]].sum().reset_index().assign(cd=-1)
    by = pd.concat([by, un[by.columns]], ignore_index=True)
    # House candidates: party by candidate (fusion lines count for the candidate's major party)
    p = house["party_simplified"].fillna("").str.upper()
    house["pd"], house["pr"] = p.eq("DEMOCRAT"), p.eq("REPUBLICAN")
    hc = house.groupby(["cd", "candidate"]).agg(votes=("votes", "sum"), isd=("pd", "any"), isr=("pr", "any")) \
        .reset_index()
    return by, hc


def _alloc(pc: pd.DataFrame, shares: pd.DataFrame, official: pd.DataFrame | None, pop_share: pd.DataFrame) \
        -> pd.DataFrame:
    """Votes of each county split over its 2024 districts. pc: county -> list of districts (from blocks);
    shares: precinct-allocated d, r, t by (fips, cd); official: county totals d, r, t (None -> use shares as is);
    pop_share: (county, cd, w) fallback weights (population, or blocks where no population). Returns county, cd,
    d, r, t, how ('whole' county inside one district | 'precinct' | 'population')."""
    rows = []
    sh = {f: g for f, g in shares.groupby("fips")}
    ps = {f: g for f, g in pop_share.groupby("county")}
    for c, dists in pc.items():
        tot = official.loc[c] if official is not None and c in official.index else None
        if len(dists) == 1:
            if tot is None:
                g = sh.get(c)
                if g is None:
                    continue
                tot = g[["d", "r", "t"]].sum()
            rows.append((c, dists[0], tot["d"], tot["r"], tot["t"], "whole"))
            continue
        g = sh.get(c)
        if g is not None:
            un = g[g["cd"] == -1]
            g = g[g["cd"].isin(dists)]
            miss = [k for k in dists if k not in set(g["cd"])]
            if miss and len(un) and un["t"].sum() > 0 and c in ps:
                wm = ps[c][ps[c]["cd"].isin(miss)]
                wm = wm.assign(w=wm["w"] / wm["w"].sum()) if wm["w"].sum() > 0 else wm.assign(w=1 / len(wm))
                u = un[["d", "r", "t"]].sum()
                g = pd.concat([g, pd.DataFrame({"fips": c, "cd": wm["cd"].values, "d": u["d"] * wm["w"].values,
                                                "r": u["r"] * wm["w"].values, "t": u["t"] * wm["w"].values})])
        if g is not None and g["t"].sum() > 0:
            if tot is None:
                tot = g[["d", "r", "t"]].sum()
            for _, x in g.iterrows():
                rows.append((c, int(x["cd"]), *(tot[k] * x[k] / g[k].sum() if g[k].sum() > 0 else 0.0
                                                 for k in ("d", "r", "t")), "precinct"))
        elif tot is not None and c in ps:
            for _, x in ps[c].iterrows():
                rows.append((c, int(x["cd"]), tot["d"] * x["w"], tot["r"] * x["w"], tot["t"] * x["w"], "population"))
    return pd.DataFrame(rows, columns=["county", "cd", "d", "r", "t", "how"])


def _house_result(hc: pd.DataFrame) -> dict | None:
    if hc.empty or hc["votes"].sum() <= 0:
        return None
    hc = hc.sort_values("votes", ascending=False)
    t = float(hc["votes"].sum())
    d = hc[hc["isd"]].head(1)
    r = hc[hc["isr"]].head(1)
    dv = float(d["votes"].iat[0]) if len(d) else 0.0
    rv = float(r["votes"].iat[0]) if len(r) else 0.0
    w = hc.iloc[0]
    real = hc[hc["votes"] >= 0.01 * t]
    return {"winner": str(w["candidate"]).title(), "winner_party": "D" if w["isd"] else "R" if w["isr"] else "O",
            "d_cand": str(d["candidate"].iat[0]).title() if len(d) else None,
            "r_cand": str(r["candidate"].iat[0]).title() if len(r) else None,
            "dv": int(dv), "rv": int(rv), "tv": int(t), "d2": _r(dv / (dv + rv)) if dv and rv else None,
            "margin": _r(100 * (dv - rv) / t, 2), "uncontested": len(real) < 2,
            "d_vs_r": bool(dv and rv)}


def _xlsx(path: Path, want=lambda name: True) -> dict[str, pd.DataFrame]:
    """Worksheets of an .xlsx (no openpyxl): name -> DataFrame with the row holding "STATE ABBREVIATION" as header.
    Each sheet is parsed by brunder.us._xlsx_rows (first-sheet reader) from a one-sheet copy of the workbook."""
    import xml.etree.ElementTree as ET
    z = zipfile.ZipFile(path)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{ns}sheet"):
        name = s.get("name")
        if not want(name):
            continue
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as one:
            if "xl/sharedStrings.xml" in z.namelist():
                one.writestr("xl/sharedStrings.xml", z.read("xl/sharedStrings.xml"))
            one.writestr("xl/worksheets/sheet1.xml", z.read("xl/" + targets[s.get(rel)].lstrip("/").removeprefix("xl/")))
        rows = us._xlsx_rows(buf.getvalue())
        h = next((i for i, r in enumerate(rows) if "STATE ABBREVIATION" in [str(c).strip() for c in r if c]), None)
        if h is None:
            continue
        head = [str(c).strip() if c else f"c{i}" for i, c in enumerate(rows[h])]
        body = [r[:len(head)] + [None] * (len(head) - len(r)) for r in rows[h + 1:]]
        out[name] = pd.DataFrame(body, columns=head)
    return out


def _fec24_cands() -> pd.DataFrame:
    """2024 House general-election ballot candidates (FEC): st, cd, name, party, incumbent."""
    x = _xlsx(_get(FEC24_CANDS), lambda n: "General" in n and "Spec" not in n)
    rows = []
    for name, df in x.items():
        if "General" not in name or "Spec" in name or "Special" in name:
            continue
        df = df.dropna(subset=["STATE ABBREVIATION", "DISTRICT"])
        for _, r in df.iterrows():
            st, dist = str(r["STATE ABBREVIATION"]).strip(), str(r["DISTRICT"]).strip()
            if st not in ABBR or not re.match(r"^\d+(\.0)?$", dist):
                continue
            rows.append({"st": st, "cd": int(float(dist)), "name": str(r.get("CANDIDATE NAME") or "").strip(),
                         "last": str(r.get("CANDIDATE NAME (Last)") or "").strip(),
                         "party": str(r.get("PARTY") or "").strip(), "inc": "(I)" in str(r.get("(I)") or "")})
    return pd.DataFrame(rows)


def _fec22_house() -> dict[str, dict]:
    """2022 House general results by district (FEC official): d2, margin, contested flags."""
    x = _xlsx(_get(FEC22), lambda n: "US House Results" in n)
    x = next(iter(x.values()))
    x = x[x["DISTRICT"].notna() & x["STATE ABBREVIATION"].isin(ABBR)]
    x = x[x["DISTRICT"].str.match(r"^\d+$") & ~x["CANDIDATE NAME"].fillna("").str.contains("Scattered|Total",
                                                                                           case=False)]
    x["v"] = pd.to_numeric(x["GENERAL VOTES"], errors="coerce")
    x["comb"] = pd.to_numeric(x["COMBINED GE PARTY TOTALS (when applicable)"], errors="coerce")
    x["party"] = x["PARTY"].fillna("").str.upper()
    x = x[x["v"].notna() | x["comb"].notna()]
    dem, rep = r"(?:^|[ /])(?:D|DEM|DFL)(?:$|[ /])|DEMOCRAT", r"(?:^|[ /])(?:R|REP)(?:$|[ /])|REPUBLICAN"
    out = {}
    for (st, dist), g in x.groupby(["STATE ABBREVIATION", "DISTRICT"]):
        # one row per candidate: fusion candidates (NY) have a row per ballot line plus the combined total
        c = g.groupby("CANDIDATE NAME").agg(v=("v", "sum"), comb=("comb", "max"), party=("party", " ".join))
        c["v"] = c["comb"].where(c["comb"].notna(), c["v"])
        t = float(c["v"].sum())
        dv = float(c.loc[c["party"].str.contains(dem), "v"].max()) if c["party"].str.contains(dem).any() else 0.0
        rv = float(c.loc[c["party"].str.contains(rep), "v"].max()) if c["party"].str.contains(rep).any() else 0.0
        out[did(st, int(dist))] = {"winner": str(c["v"].idxmax()), "dv": int(dv), "rv": int(rv), "tv": int(t),
                                   "d2": _r(dv / (dv + rv)) if dv and rv else None,
                                   "margin": _r(100 * (dv - rv) / t, 2) if t else None, "d_vs_r": bool(dv and rv)}
    return out


# ------------------------------------------------------------------ build

def _rec(d: float, r: float, t: float) -> dict:
    return {"d2": _r(d / (d + r)) if d + r > 0 else None, "margin": _r(100 * (d - r) / t, 2) if t else None,
            "dv": int(round(d)), "rv": int(round(r)), "tv": int(round(t))}


def data() -> None:
    pcs = pieces()
    cp = _county_pres()
    cands = _fec24_cands()
    h22 = _fec22_house()
    nat24 = cp[2024][["d", "r"]].sum()
    global NAT_PRES24_D2
    NAT_PRES24_D2 = float(nat24["d"] / (nat24["d"] + nat24["r"]))
    seats = pcs.groupby("st")["cd119"].nunique()
    d24_rows, house24, state_check = [], {}, {}
    for st in sorted(seats.index):
        n = int(seats[st])
        p = pcs[pcs["st"] == st].copy()
        p["cd"] = p["cd119"].astype(int)
        shares, hc = _precincts(st, n)
        pc = p.groupby("county")["cd"].apply(lambda s: sorted(s.unique())).to_dict()
        w = p.groupby(["county", "cd"]).agg(pop=("pop", "sum"), blocks=("blocks", "sum")).reset_index()
        base = w["pop"] if p["pop"].notna().any() else w["blocks"]
        w["w"] = base / base.groupby(w["county"]).transform("sum")
        off24 = cp[2024]

        def covered(off: pd.DataFrame, fp: str = ABBR[st], pc: dict = pc) -> bool:
            """CT 2024: the county file is on planning regions, the blocks on counties."""
            mine = [f for f in off.index if f[:2] == fp]
            return sum(f in pc for f in mine) > 0.9 * max(1, len(mine))
        if n == 1:       # at-large: the state's county-file totals (AK's file is by house district, not county)
            c0 = min(pc)
            a24, a20 = ([pd.DataFrame([{"county": c0, "cd": 0, **cp[y][cp[y].index.str[:2] == ABBR[st]].sum(),
                                        "how": "whole"}]) for y in (2024, 2020)])
        else:
            a24 = _alloc(pc, shares, off24 if covered(off24) else None, w)
            # 2020: each county's 2020 Democratic (Republican, total) votes split like its 2024 ones
            sh24 = a24.rename(columns={"county": "fips"})
            a20 = _alloc(pc, sh24, cp[2020], w) if covered(cp[2020]) else a24.iloc[:0]
        m = a24.merge(a20, on=["county", "cd"], suffixes=("24", "20"), how="left")
        m["st"] = st
        d24_rows.append(m)
        state_check[st] = {"d24_alloc": float(a24["d"].sum()), "r24_alloc": float(a24["r"].sum()),
                           "d24_county_file": float(off24.loc[off24.index.str[:2] == ABBR[st], "d"].sum()),
                           "county_file_2024": covered(off24), "county_file_2020": covered(cp[2020])}
        for cd, g in hc.groupby("cd"):
            house24[did(st, cd)] = _house_result(g)
    allp = pd.concat(d24_rows, ignore_index=True)
    # 2024 lines
    d2024 = {}
    for (st, cd), g in allp.groupby(["st", "cd"]):
        hows = set(g["how24"])
        d2024[did(st, cd)] = {
            "st": st, "pres24": {**_rec(g["d24"].sum(), g["r24"].sum(), g["t24"].sum()),
                                 "method": "county" if hows == {"whole"} else "population" if "population" in hows
                                 else "precinct"},
            "pres20": {**_rec(g["d20"].sum(), g["r20"].sum(), g["t20"].sum()),
                       "method": "county" if hows == {"whole"} else "county_split_by_2024"}}
    # House 2024 / 2022, incumbents
    for k, v in d2024.items():
        st, cd = k.split("-")
        cdn = 0 if cd == "AL" else int(cd)
        c = cands[(cands["st"] == st) & (cands["cd"] == cdn)]
        inc = c[c["inc"]]
        h = house24.get(k)
        if h is None and len(c) == 1:          # not on the ballot (Florida: unopposed candidates are not printed)
            x = c.iloc[0]
            h = {"winner": x["name"], "winner_party": "D" if x["party"].startswith("DEM") else
                 "R" if x["party"].startswith("REP") else "O", "d_cand": None, "r_cand": None, "dv": None,
                 "rv": None, "tv": None, "d2": None, "margin": None, "uncontested": True, "d_vs_r": False,
                 "not_on_ballot": True}
        if h is not None:
            h["incumbent"] = inc["name"].iat[0] if len(inc) else None
            h["incumbent_party"] = (inc["party"].iat[0][:1] if len(inc) else None)
        v["house24"] = h
        v["house22"] = h22.get(k)
        comp = (st not in LINES_CHANGED_2024 and h and h22.get(k) and h.get("d_vs_r") and h22[k]["d_vs_r"])
        v["swing_house"] = _r(h["margin"] - h22[k]["margin"], 2) if comp else None
        v["swing_pres"] = _r(v["pres24"]["margin"] - v["pres20"]["margin"], 2) \
            if v["pres24"]["margin"] is not None and v["pres20"]["margin"] is not None else None
    # residual sd of House vs president (2024 lines, contested D vs R): district-level baseline uncertainty
    res = pd.Series({k: v["house24"]["d2"] - v["pres24"]["d2"] for k, v in d2024.items()
                     if v["house24"] and v["house24"].get("d2") is not None and v["pres24"]["d2"] is not None})
    sigma = float(np.sqrt(((res - res.mean()) ** 2).mean()))
    # 2026 lines: (county, 2024 district) pieces split by 2020 block population
    pp = pcs.copy()
    pp["cd"], pp["cd26"] = pp["cd119"].astype(int), pp["cd120"].astype(int)
    pw = pp.groupby(["county", "cd", "cd26"]).agg(pop=("pop", "sum"), blocks=("blocks", "sum")).reset_index()
    tot_pop = pw.groupby(["county", "cd"])["pop"].transform("sum")
    pw["w"] = np.where(tot_pop > 0, pw["pop"] / tot_pop,
                       pw["blocks"] / pw.groupby(["county", "cd"])["blocks"].transform("sum"))
    x = allp.merge(pw, on=["county", "cd"], how="left")
    x["w"] = x["w"].fillna(1.0)
    x["cd26"] = x["cd26"].fillna(x["cd"]).astype(int)
    for c in ("d24", "r24", "t24", "d20", "r20", "t20"):
        x[c] = x[c] * x["w"]
    d2026 = {}
    for (st, cd), g in x.groupby(["st", "cd26"]):
        k = did(st, cd)
        redrawn = st in REDRAWN
        old = d2024.get(k) if not redrawn else None
        rec = {"st": st, "redrawn": redrawn,
               "pres24": {**_rec(g["d24"].sum(), g["r24"].sum(), g["t24"].sum()),
                          "method": "crosswalk" if redrawn else old["pres24"]["method"]},
               "pres20": {**_rec(g["d20"].sum(), g["r20"].sum(), g["t20"].sum()),
                          "method": "crosswalk" if redrawn else old["pres20"]["method"]}}
        m24, m20 = rec["pres24"]["margin"], rec["pres20"]["margin"]
        rec["swing_pres"] = _r(m24 - m20, 2) if m24 is not None and m20 is not None else None
        if redrawn:
            ps = pw[pw["county"].str[:2] == ABBR[st]]
            core = ps[ps["cd26"] == cd].groupby("cd")["pop"].sum()
            rec["pop2020"] = int(core.sum())
            rec["core_2024"] = did(st, int(core.idxmax()))
            rec["core_share"] = _r(core.max() / core.sum(), 3)
            rec["house24_core"] = d2024[rec["core_2024"]]["house24"]
        else:
            rec["house24"] = old["house24"]
            rec["house22"] = old["house22"]
            rec["swing_house"] = old["swing_house"]
        d2026[k] = rec
    assert len(d2026) == 435 and len(d2024) == 435, (len(d2026), len(d2024))
    # flows
    flows = {}
    for st in REDRAWN:
        ps = pw[pw["county"].str[:2] == ABBR[st]]
        f = ps.groupby(["cd", "cd26"])["pop"].sum().reset_index()
        f = f[f["pop"] > 0]
        old_pop, new_pop = f.groupby("cd")["pop"].sum(), f.groupby("cd26")["pop"].sum()
        xv = x[x["st"] == st].groupby(["cd", "cd26"])[["d24", "r24"]].sum()
        fl = [[did(st, a), did(st, b_), int(p_), _r(p_ / old_pop[a], 4), _r(p_ / new_pop[b_], 4),
               int(round(xv.loc[(a, b_), "d24"])) if (a, b_) in xv.index else 0,
               int(round(xv.loc[(a, b_), "r24"])) if (a, b_) in xv.index else 0]
              for a, b_, p_ in f[["cd", "cd26", "pop"]].itertuples(index=False)]
        new = {k: v for k, v in d2026.items() if v["st"] == st}
        old = {k: v for k, v in d2024.items() if v["st"] == st}

        def seats(ds):
            d2 = np.array([v["pres24"]["d2"] for v in ds.values()])
            return {"districts": len(d2), "harris_won": int((d2 > 0.5).sum()),
                    "expected_d": _r(float(norm.cdf((d2 - 0.5) / sigma).sum()), 2)}
        flows[st] = {
            "old_seats": seats(old), "new_seats": seats(new),
            "house24_won_d": sum(1 for v in old.values() if v["house24"] and v["house24"]["winner_party"] == "D"),
            "flows": fl,
            "districts": {k: {"pres24_margin": v["pres24"]["margin"], "core_2024": v["core_2024"],
                              "core_margin": d2024[v["core_2024"]]["pres24"]["margin"],
                              "lean_change": _r(v["pres24"]["margin"] - d2024[v["core_2024"]]["pres24"]["margin"],
                                                2)} for k, v in new.items()}}
    # counties: president 2020 -> 2024 margin (points)
    c20, c24 = cp[2020], cp[2024]
    cm = pd.DataFrame({"m20": 100 * (c20["d"] - c20["r"]) / c20["t"], "m24": 100 * (c24["d"] - c24["r"]) / c24["t"]})
    cm = cm.dropna()
    cm = cm[cm.index.str[:2].isin(list(FIPS))]
    counties = {f"us-{FIPS[f[:2]].lower()}-{f}": [_r(r.m20, 1), _r(r.m24, 1)] for f, r in cm.iterrows()}
    # early vote by 2026 district (TX, FL, NC)
    ev = earlyvote(pcs)
    for k, v in ev["districts"].items():
        d2026[k]["ev"] = {"total": v["ev_total"], "share_of_2022": v["share_of_2022_total"]}
    notes = {
        "pres24": "two-party Harris share (d2) and margin (D - R, % of all presidential votes). 2024 lines: each "
                  "county's official total split over its districts by the 2024 precinct results (each precinct "
                  "assigned to the House district on its ballot); counties inside one district are exact. method: "
                  "county (all counties whole), precinct, population (a split county without precinct-district "
                  "data, split by 2020 population).",
        "pres20": "2020 county totals split over districts like each county's 2024 votes of the same party "
                  "(approximation: assumes the within-county pattern of 2024; the district swing is a weighted mix "
                  "of county swings).",
        "crosswalk": "redrawn states: the (county x 2024 district) pieces above split to 2026 districts by 2020 "
                     "census block population (approximation: uniform vote within each piece).",
        "house24": "MEDSL 2024 precinct returns summed by district (votes; d2 = top D / (top D + top R)); winner, "
                   "uncontested (one candidate with >= 1 % of votes), incumbent from FEC's 2024 ballot list.",
        "house22": "FEC Federal Elections 2022, official.",
        "swing_house": f"2024 minus 2022 House margin, only where the lines did not change (not "
                       f"{', '.join(LINES_CHANGED_2024)}) and both races were D vs R.",
    }
    _write("districts_2024.json", {"built": TODAY.strftime("%Y-%m-%d"), "notes": notes,
                                   "sources": [SRC[k] for k in ("bef", "medsl_prec", "medsl_county", "fec")],
                                   "national": {"pres24_d2": _r(NAT_PRES24_D2)}, "districts": d2024})
    meta = {"built": TODAY.strftime("%Y-%m-%d"), "notes": notes, "redrawn": list(REDRAWN),
            "sources": [SRC[k] for k in ("bef", "pl", "medsl_prec", "medsl_county", "fec")],
            "national": {"pres24_d2": _r(NAT_PRES24_D2), "sigma_house_vs_pres": _r(sigma),
                         "house_vs_pres_mean": _r(float(res.mean())), "n_sigma": int(len(res))},
            "state_check": {k: {kk: (_r(vv, 0) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                            for k, v in state_check.items()},
            "districts": d2026}
    (CACHE / "districts_2026.json").write_text(json.dumps(meta, allow_nan=False))   # markets() adds the baseline
    _write("districts.json", meta)
    _write("flows.json", {"built": TODAY.strftime("%Y-%m-%d"), "sources": [SRC["bef"], SRC["pl"], SRC["medsl_prec"]],
                          "note": "flows: [2024 district, 2026 district, 2020 population, share of the 2024 "
                                  "district, share of the 2026 district, Harris 2024 votes, Trump 2024 votes] "
                                  "(votes approximate: uniform within each county x 2024-district piece). "
                                  "lean_change = 2026 district's 2024 margin minus the margin of the 2024 district "
                                  "that gave it most people (core_2024). Seats at the 2024 presidential vote: "
                                  "harris_won = districts with d2 > 0.5; expected_d = sum of Phi((d2 - 0.5) / "
                                  f"{sigma:.4f}), the sd of 2024 House vs president two-party shares.",
                          "states": flows})
    _write("swing.json", {"built": TODAY.strftime("%Y-%m-%d"), "sources": [SRC["medsl_county"]],
                          "note": "counties: [2020 margin, 2024 margin], president, D - R as % of all votes; "
                                  "district swings are in districts.json (swing_pres, swing_house).",
                          "counties": counties})
    _write("earlyvote.json", ev)


def earlyvote(pcs: pd.DataFrame) -> dict:
    """site/data/us2026/earlyvote.json (county ballots already cast) -> 2026 districts by 2020 population share of
    each county (approximation where a county is split)."""
    src = json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "earlyvote.json").read_text())
    p = pcs[pcs["st"].isin(["TX", "FL", "NC"])].groupby(["county", "cd120"])["pop"].sum().reset_index()
    p["w"] = p["pop"] / p.groupby("county")["pop"].transform("sum")
    p["did"] = [did(s, c) for s, c in zip(p["county"].str[:2].map(FIPS), p["cd120"].astype(int))]
    split = p.groupby("county")["did"].transform("nunique") > 1
    acc: dict[str, dict] = {}
    for uid, c in src["counties"].items():
        f = uid.rsplit("-", 1)[1]
        for _, x in p[p["county"] == f].iterrows():
            a = acc.setdefault(x["did"], {"ev_total": 0.0, "baseline_2022": 0.0, "D": 0.0, "R": 0.0, "O": 0.0,
                                          "party": True, "approx": False, "counties": 0})
            a["ev_total"] += (c.get("ev_total") or 0) * x["w"]
            a["baseline_2022"] += (c.get("baseline_2022") or 0) * x["w"]
            bp = c.get("by_party")
            if bp:
                for k in ("D", "R", "O"):
                    a[k] += (bp.get(k) or 0) * x["w"]
            else:
                a["party"] = False
            a["approx"] |= bool(split[x.name])
            a["counties"] += 1
    out = {}
    for k, a in sorted(acc.items()):
        out[k] = {"ev_total": int(round(a["ev_total"])), "baseline_2022": int(round(a["baseline_2022"])),
                  "share_of_2022_total": _r(a["ev_total"] / a["baseline_2022"]) if a["baseline_2022"] else None,
                  "by_party": {k2: int(round(a[k2])) for k2 in ("D", "R", "O")} if a["party"] else None,
                  "approx": a["approx"], "counties": a["counties"]}
    return {"built": TODAY.strftime("%Y-%m-%d"),
            "as_of": {s: src["states"][s].get("as_of") for s in ("tx", "fl", "nc") if s in src.get("states", {})},
            "sources": [src["states"][s]["source"] for s in ("tx", "fl", "nc") if s in src.get("states", {})] + [SRC["pl"]],
            "note": "county ballots already cast (site/data/us2026/earlyvote.json) summed to 2026 districts; a county "
                    "split between districts is divided by 2020 census population (approx = true). by_party = "
                    "party registration of the voters (FL, NC). Display only.",
            "districts": out}


# ------------------------------------------------------------------ geometry

def _mapshaper(*args: str) -> None:
    subprocess.run([*MAPSHAPER, *args], check=True, stdout=subprocess.DEVNULL)


def _unzip(path: Path, into: Path) -> Path:
    with zipfile.ZipFile(path) as z:
        z.extractall(into)
        return into / next(n for n in z.namelist() if n.endswith(".shp"))


def geo() -> None:
    """districts_2024.topo.json (cb 2024 cd119, 1:500k) and districts_2026.topo.json (same, with the redrawn states
    from TIGER/Line 2026 cd120 clipped to the cartographic state outlines so coasts match)."""
    states = json.dumps(sorted(FIPS))
    redrawn = json.dumps(sorted(ABBR[s] for s in REDRAWN))
    fips_js = json.dumps(FIPS)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        cd119 = _unzip(_get(CB_CD119, sub="geo"), t / "cd119")
        st_shp = _unzip(_get(CB_STATE, sub="geo"), t / "state")
        each = (f"st = {fips_js}[STATEFP], cdn = +CDFP, id = st + '-' + (cdn === 0 ? 'AL' : (cdn < 10 ? '0' : '') + "
                f"cdn), redrawn = {redrawn}.indexOf(STATEFP) > -1")
        _mapshaper("-i", str(cd119), "encoding=utf8", "-filter", f"{states}.indexOf(STATEFP) > -1 && CD119FP != 'ZZ'",
                   "-each", "CDFP = CD119FP", "-each", each, "-filter-fields", "id,st,redrawn",
                   "-o", str(t / "old.json"), "format=geojson")
        new = [str(_unzip(_get(TL_CD120.format(fips=ABBR[s]), sub="geo"), t / f"n{s}")) for s in REDRAWN]
        _mapshaper("-i", *new, "combine-files", "encoding=utf8", "-merge-layers", "force",
                   "-filter", "CD120FP != 'ZZ'", "-each", "CDFP = CD120FP", "-each", each,
                   "-filter-fields", "id,st,redrawn", "-clip", str(st_shp), "-o", str(t / "new.json"), "format=geojson")
        old = json.loads((t / "old.json").read_text())["features"]
        newf = json.loads((t / "new.json").read_text())["features"]
        keep = [f for f in old if not f["properties"]["redrawn"]]
        (t / "y2026.json").write_text(json.dumps({"type": "FeatureCollection", "features": keep + newf}))
        for src, name in ((t / "old.json", "districts_2024.topo.json"), (t / "y2026.json", "districts_2026.topo.json")):
            out = OUT / name
            OUT.mkdir(parents=True, exist_ok=True)
            # one distance tolerance for both maps, so the 2024 and 2026 lines are drawn at the same detail
            _mapshaper("-i", str(src), "name=districts", "-simplify", "interval=250", "keep-shapes",
                       "-o", str(out), "format=topojson", "quantization=100000", "id-field=id")
            doc = json.loads(out.read_text())
            ids = [g["properties"]["id"] for g in doc["objects"]["districts"]["geometries"]]
            assert len(ids) == len(set(ids)) == 435, (name, len(ids), len(set(ids)))
            print(f"{out.relative_to(us.ROOT_DIR)}: {len(ids)} districts, {out.stat().st_size / 1e6:.2f} MB")
            assert out.stat().st_size < MAX_BYTES


# ------------------------------------------------------------------ markets, polls, baseline

_ID_RX = re.compile(r"\b([A-Z]{2})-(\d{1,2}|AL)\b")


def _norm_id(st: str, n: str, at_large: set[str]) -> str | None:
    if st not in ABBR:
        return None
    if st in at_large:
        return f"{st}-AL"
    return None if n == "AL" else f"{st}-{int(n):02d}"


def _pm_house(at_large: set[str]) -> dict[str, dict]:
    from brunder import _http
    from brunder.polymarket import GAMMA
    evs, off = [], 0
    with _http.client() as c:
        while True:
            page = _http.get_json(c, f"{GAMMA}/events", {"tag_slug": "house-elections", "closed": "false",
                                                        "limit": 100, "offset": off})
            if not page:
                break
            evs += page
            off += len(page)
    out = {}
    for e in evs:
        slug = e["slug"]
        if "margin" in slug or not ("house-election-winner" in slug or "which-party-will-win-the-house-race" in slug):
            continue
        m = _ID_RX.search(e["title"].upper().replace(" AT-LARGE", "-AL"))
        k = _norm_id(m.group(1), m.group(2), at_large) if m else None
        if not k:
            continue
        rec = {"slug": slug, "volume": _r(float(e.get("volume") or 0), 0), "cands": {}}
        for mk in e.get("markets") or []:
            q = (mk.get("question") or "") + " " + (mk.get("groupItemTitle") or "")
            side = "D" if re.search(r"Democrat|\(D\)", q) else "R" if re.search(r"Republican|\(R\)", q) else None
            if not side or not mk.get("outcomePrices"):
                continue
            bid, ask = mk.get("bestBid"), mk.get("bestAsk")
            last = float(json.loads(mk["outcomePrices"])[0])
            p = (float(bid) + float(ask)) / 2 if bid not in (None, 0) and ask not in (None, 1) else last
            rec[f"p_{side.lower()}"] = _r(p)
            rec[f"spread_{side.lower()}"] = _r(float(ask) - float(bid)) if bid is not None and ask is not None else None
            name = re.sub(r"\s*\((D|R)\)\s*$", "", mk.get("groupItemTitle") or "").strip()
            if name and name not in ("Democratic", "Republican", "Democrat", "Republican Party", "Democratic Party"):
                rec["cands"][side] = name
        if "p_d" in rec or "p_r" in rec:
            pd_ = rec.get("p_d")
            rec["p"] = pd_ if pd_ is not None else _r(1 - rec["p_r"])
            out[k] = rec
    return out


def _kalshi_house(at_large: set[str]) -> dict[str, dict]:
    ser = [s for s in kalshi.all_series() if str(s.get("contract_terms_url", "")).endswith("/HOUSEPARTY.pdf")]
    out = {}
    for s in ser:
        try:
            evs = kalshi.events(s["ticker"])
        except Exception as ex:
            print(f"kalshi {s['ticker']}: {type(ex).__name__}: {ex}")
            continue
        for e in evs:
            if not re.search(r"-26$", e["event_ticker"]):
                continue
            m = _ID_RX.search((e.get("title") or "").upper().replace(" AT-LARGE", "-AL"))
            k = _norm_id(m.group(1), m.group(2), at_large) if m else None
            if not k:
                continue
            mk = kalshi.markets(e)
            if mk.empty:
                continue
            rec = {"event": e["event_ticker"], "cands": {}, "volume": _r(float(mk["volume"].sum()), 0)}
            for _, x in mk.iterrows():
                side = "D" if x["ticker"].endswith("-D") else "R" if x["ticker"].endswith("-R") else None
                if not side:
                    continue
                p = (x["yes_bid"] + x["yes_ask"]) / 2 if pd.notna(x["yes_bid"]) and pd.notna(x["yes_ask"]) \
                    and x["yes_ask"] > 0 else x["last"]
                rec[f"p_{side.lower()}"] = _r(p)
                rec[f"spread_{side.lower()}"] = _r(x["yes_ask"] - x["yes_bid"]) \
                    if pd.notna(x["yes_bid"]) and pd.notna(x["yes_ask"]) else None
                if x["label"] and x["label"] != x["ticker"]:
                    rec["cands"][side] = x["label"]
            if "p_d" in rec or "p_r" in rec:
                rec["p"] = rec.get("p_d") if rec.get("p_d") is not None else _r(1 - rec["p_r"])
                out[k] = rec
    return out


def _fec26() -> pd.DataFrame:
    with zipfile.ZipFile(_get(FEC26_CN)) as z:
        d = pd.read_csv(z.open("cn.txt"), sep="|", header=None, dtype=str, usecols=[1, 2, 3, 4, 5, 6])
    d.columns = ["name", "party", "year", "st", "office", "cd"]
    return d[(d["office"] == "H") & (d["year"] == "2026")]


def _party_lookup(names: dict[str, dict[str, str]], fec: pd.DataFrame):
    """(district id, poll answer) -> 'D' | 'R' | None: market candidate names of the district first, then the FEC
    2026 House candidates of the state with the same surname (only if all of them have one major party)."""
    fec = fec.assign(last=fec["name"].fillna("").str.split(",").str[0]
                     .map(lambda s: us._person(s)[-1] if us._person(s) else ""),
                     p=fec["party"].map({"DEM": "D", "DFL": "D", "REP": "R"}))

    def f(k: str, choice: str) -> str | None:
        for side, nm in names.get(k, {}).items():
            if us._same_person(choice, nm):
                return side
        last = us._person(choice)[-1] if us._person(choice) else None
        ps = set(fec.loc[(fec["st"] == k[:2]) & (fec["last"] == last), "p"].dropna())
        return ps.pop() if len(ps) == 1 else None
    return f


def markets() -> None:
    meta = json.loads((CACHE / "districts_2026.json").read_text())
    dists = meta["districts"]
    nseats = pd.Series([k[:2] for k in dists]).value_counts()
    at_large = set(nseats[nseats == 1].index)
    pm, ka = _pm_house(at_large), _kalshi_house(at_large)
    names = {}
    for src in (ka, pm):
        for k, v in src.items():
            for side, nm in v["cands"].items():
                names.setdefault(k, {}).setdefault(side, nm)
    raw = json.loads(us._fetch(f"{us.VOTEHUB}?poll_type=us-representative",
                               us.US_RAW / "polls" / f"votehub_us-representative_{TODAY.strftime('%Y%m%d')}.json"))
    gb = json.loads(us._fetch(f"{us.VOTEHUB}?poll_type=generic-ballot",
                              us.US_RAW / "polls" / f"votehub_generic-ballot_{TODAY.strftime('%Y%m%d')}.json"))
    party = _party_lookup(names, _fec26())
    rows, unmatched = {}, 0
    for p in raw:
        m = _ID_RX.search(str(p.get("seat_name") or "").upper())
        k = _norm_id(m.group(1), m.group(2), at_large) if m else None
        if not k or k not in dists or not p.get("sample_size"):
            unmatched += 1
            continue
        ans = [(a["choice"], float(a["pct"])) for a in p.get("answers") or [] if a.get("pct") is not None]
        d = [x for x in ans if party(k, x[0]) == "D"]
        r = [x for x in ans if party(k, x[0]) == "R"]
        if not d or not r:
            unmatched += 1
            continue
        rows.setdefault(k, []).append({"id": p["id"], "pollster": p.get("pollster") or "", "start": p.get("start_date"),
                                       "end": p["end_date"], "pop": us._POP_RANK.get(p.get("population"), 3),
                                       "k": len(ans), "n": float(p["sample_size"]), "dem": d[0][1], "opp": r[0][1],
                                       "s": d[0][1] / (d[0][1] + r[0][1]), "d_name": d[0][0], "r_name": r[0][0]})
    polls = {}
    for k, rs in rows.items():
        df = pd.DataFrame(rs).sort_values(["pop", "k", "id"], ascending=[True, False, True]) \
            .drop_duplicates(["pollster", "start", "end"]).sort_values("end").reset_index(drop=True)
        avg = us.poll_average(df, us.POLLS_ASOF)
        if avg:
            avg["sd"] = _r(us.poll_sd_by_n(avg["n_polls"]))
            avg["p_win"] = _r(float(norm.cdf((avg["avg"] - 0.5) / avg["sd"])))
        polls[k] = {"average": avg, "polls": [{"pollster": x.pollster, "end": x.end, "n": int(x.n), "pop": x.pop,
                                               "d": x.d_name, "d_pct": x.dem, "r": x.r_name, "r_pct": x.opp,
                                               "d2": _r(x.s)} for x in df.itertuples()]}
    # generic ballot (our averaging rule) and the model-only baseline
    gr = []
    for p in gb:
        a = {x["choice"]: x.get("pct") for x in p.get("answers") or []}
        if p.get("subject") == "2026" and a.get("Dem") is not None and a.get("Rep") is not None \
                and p.get("sample_size"):
            gr.append({"id": p["id"], "pollster": p.get("pollster") or "", "start": p.get("start_date"),
                       "end": p["end_date"], "pop": us._POP_RANK.get(p.get("population"), 3), "k": 2,
                       "n": float(p["sample_size"]), "dem": float(a["Dem"]), "opp": float(a["Rep"]),
                       "s": float(a["Dem"]) / (float(a["Dem"]) + float(a["Rep"]))})
    gdf = pd.DataFrame(gr).sort_values(["pop", "id"]).drop_duplicates(["pollster", "start", "end"]) \
        .sort_values("end").reset_index(drop=True)
    g = us.poll_average(gdf, us.POLLS_ASOF)
    g_sd = us.poll_sd_by_n(g["n_polls"])
    nat = meta["national"]
    sig = nat["sigma_house_vs_pres"]
    sd = float(np.sqrt(g_sd ** 2 + sig ** 2))
    swing = g["avg"] - nat["pres24_d2"]
    out = {}
    for k, v in dists.items():
        d2 = v["pres24"]["d2"]
        est = d2 + swing
        base = {"share": _r(est), "sd": _r(sd), "p_d": _r(float(norm.cdf((est - 0.5) / sd)))}
        v["baseline"] = base
        e = {"pm": pm.get(k), "kalshi": ka.get(k), "polls": polls.get(k), "baseline": base}
        if pm.get(k) or ka.get(k) or polls.get(k):
            out[k] = e
        if pm.get(k) or ka.get(k):
            v["market_p_d"] = {"pm": (pm.get(k) or {}).get("p"), "kalshi": (ka.get(k) or {}).get("p")}
        if polls.get(k) and polls[k]["average"]:
            v["poll_avg"] = {kk: polls[k]["average"][kk] for kk in ("avg", "sd", "n_polls", "last_date")}
    generic = {**g, "sd_bin": g_sd, "swing_vs_pres24": _r(swing), "n_polls_total": int(len(gdf))}
    meta["national"]["generic_ballot"] = generic
    meta["notes"]["baseline"] = (
        "MODEL-ONLY, display only, not registered: share = district's 2024 two-party Harris share on 2026 lines + "
        "(generic-ballot average - national 2024 two-party Harris share); sd = sqrt(poll-error bin of the generic "
        "ballot (statewide-race bins, brunder.us.POLL_SD_BINS)^2 + sd of 2024 House vs president shares^2); "
        "p_d = Phi((share - 0.5) / sd). Errors are correlated across districts (one national swing).")
    meta["sources"] = meta["sources"] + [SRC["votehub"], SRC["polymarket"], SRC["kalshi"]]
    _write("districts.json", meta)
    _write("markets.json", {
        "built": TODAY.strftime("%Y-%m-%d"), "asof": us.POLLS_ASOF,
        "sources": [SRC["polymarket"], SRC["kalshi"], SRC["votehub"], SRC["fec"]],
        "note": "p = P(Democratic party wins the seat): midpoint of best bid/ask where both exist, else last price. "
                "Polls: VoteHub district polls; D and R answers identified by the market candidate names, else by "
                "surname among FEC 2026 House candidates of the state; average by our Senate/governor rule "
                "(docs/us_midterms.md#polls), sd = poll-error bin by number of polls. baseline: see districts.json "
                "notes (model only).",
        "generic_ballot": generic,
        "counts": {"pm": len(pm), "kalshi": len(ka), "polls_districts": len(polls),
                   "polls_unmatched": unmatched},
        "districts": out})


STEPS = {"geo": geo, "data": data, "markets": markets}

if __name__ == "__main__":
    for s in sys.argv[1:] or list(STEPS):
        STEPS[s]()
