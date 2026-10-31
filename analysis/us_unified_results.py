"""Unified pre-count model, results slice: county x year x office results 2008-2024 and statewide aggregates.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_results.py [fetch] [extract] [build]

fetch    downloads the inputs that are not on disk yet (public GitHub files, one request each; no guestbook files):
         MEDSL 2016 precinct Senate / State offices (github.com/MEDSL/official-precinct-returns, MIT) and the 2020 House
         and President precinct files from the last commit of github.com/MEDSL/2020-elections-official that still held
         them (ad22f37, 2022-03-14; the repository now points to the Dataverse).
extract  reads every MEDSL precinct file once (duckdb, scratch copies under U/_scratch) and writes one compact
         candidate x county table per source to U/_cache/results/*.parquet.
build    county results (U/results_county.parquet + .csv.gz), statewide aggregates (U/results_state.csv),
         validation against official statewide totals (U/results_validation.csv), U/README_UniResults.md.

Conventions (docs: U/README_UniResults.md): fips = 5-digit string, st = lowercase postal code; dem / rep = votes of the
Democratic / Republican party's candidates (a candidate counts for the major party under which most of his votes were
cast: fusion lines), total_all = all candidates incl. write-ins (not under/over votes); s2 = dem/(dem+rep), v2 = dem+rep.
Vintage: results are certified or final precinct returns of the election itself (outcomes, never inputs to a forecast
of that election).
"""
from __future__ import annotations

import io
import os
import re
import sys
import zipfile
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

RAW = us.US_RAW
U = us.US_PROC / "unified"
CACHE = U / "_cache" / "results"
SCRATCH = U / "_scratch"
ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data_public"

MEDSL16 = "https://raw.githubusercontent.com/MEDSL/official-precinct-returns/master"
MEDSL20_GH = "https://raw.githubusercontent.com/MEDSL/2020-elections-official/ad22f37e3f1e3355f078ba5a1efce6c221333ef1"
FETCH = [(f"{MEDSL16}/2016-precinct-senate/2016-precinct-senate.zip", RAW / "medsl16" / "senate.zip"),
         (f"{MEDSL16}/2016-precinct-state/2016-precinct-state.zip", RAW / "medsl16" / "state.zip"),
         (f"{MEDSL20_GH}/HOUSE/HOUSE_precinct_general.zip", RAW / "medsl20" / "gh" / "HOUSE_precinct_general.zip"),
         (f"{MEDSL20_GH}/PRESIDENT/PRESIDENT_precinct_general.zip", RAW / "medsl20" / "gh" / "PRESIDENT_precinct_general.zip")]

STATES20 = "de in mo mt nc nd nh ut vt wa wv".split()      # 2020 state files on disk (governor + House); the rest of the 2020 House: GitHub file
STATES = ("al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok "
          "or pa ri sc sd tn tx ut va vt wa wv wi wy").split()
FIPS_RENAMED = {"51515": "51019", "46113": "46102", "02270": "02158"}   # as analysis/us_fundamentals.py
NOT_CAND = (r"(?:\bTOTAL\b|BALLOTS CAST|^UNDER ?VOTES?|^OVER ?VOTES?|^BLANKS?(?: VOTES?| BALLOTS?)?$|^SPOILED|^REGISTERED|^VOID|"
            r"^STRAIGHT|^SCATTERING$|^NO VOTE|^UNRESOLVED|^NONE OF)")


# ------------------------------------------------------------------ fetch

def fetch() -> None:
    import httpx
    for url, dest in FETCH:
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(headers=us.UA, timeout=600, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
        dest.write_bytes(r.content)
        print(f"{dest} {len(r.content):,} bytes")


# ------------------------------------------------------------------ extract

def _csv(zpath: Path, member: str | None, out: Path) -> Path:
    """Scratch copy of a zip member (duckdb cannot read inside a zip)."""
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        z = zipfile.ZipFile(zpath)
        m = member or [n for n in z.namelist() if n.endswith(".csv") and "__MACOSX" not in n][0]
        out.write_bytes(z.read(m))
    return out


def sources() -> list[dict]:
    """Every precinct file used: path, schema ('new' = MEDSL 2018+ layout, 'old' = MEDSL 2016 layout), year, state."""
    s = []
    s.append(dict(key="2016_senate", path=partial(_csv, RAW / "medsl16" / "senate.zip", "2016-precinct-senate.csv",
                                               SCRATCH / "m16" / "2016-precinct-senate.csv"), schema="old", year=2016))
    s.append(dict(key="2016_house", path=partial(_csv, RAW / "medsl16" / "house.zip", "2016-precinct-house.csv",
                                              SCRATCH / "m16" / "2016-precinct-house.csv"), schema="old", year=2016))
    s.append(dict(key="2016_state", path=partial(_csv, RAW / "medsl16" / "state.zip", "2016-precinct-state.csv",
                                              SCRATCH / "m16" / "2016-precinct-state.csv"), schema="old", year=2016,
                  only=("gov",)))
    s.append(dict(key="2016_pres_ct", path=partial(_csv, RAW / "medsl16" / "president.zip", "2016-precinct-president.csv",
                                                SCRATCH / "m16" / "2016-precinct-president.csv"), schema="old",
                  year=2016, only=("pres",), states=("CT",)))
    s.append(dict(key="2020_senate", path=RAW / "medsl20" / "2020-SENATE-precinct-general.csv", schema="new", year=2020,
                  only=("sen",)))
    for st in STATES20:
        name = "2020_in_precinct_general.csv" if st == "in" else f"2020-{st}-precinct-general.csv"
        s.append(dict(key=f"2020_gov_{st}", path=RAW / "medsl20" / name, schema="new", year=2020, only=("gov", "house")))
    s.append(dict(key="2020_house", path=partial(_csv, RAW / "medsl20" / "gh" / "HOUSE_precinct_general.zip", None,
                                              SCRATCH / "m20" / "HOUSE_precinct_general.csv"), schema="new", year=2020,
                  only=("house",)))
    s.append(dict(key="2020_pres_ct", path=partial(_csv, RAW / "medsl20" / "gh" / "PRESIDENT_precinct_general.zip", None,
                                                SCRATCH / "m20" / "PRESIDENT_precinct_general.csv"), schema="new",
                  year=2020, only=("pres",), states=("CT",)))
    for y, d, pat in ((2018, "medsl18", "2018-{st}-precinct-general.zip"), (2022, "medsl22", "2022-{st}-local-precinct-general.zip"),
                      (2024, "medsl24", "{st}24.zip")):
        for st in STATES:
            z = RAW / d / pat.format(st=st)
            if not z.exists():
                continue
            s.append(dict(key=f"{y}_{st}", path=partial(_csv, z, None, SCRATCH / "medsl" / f"{y}_{st}.csv"), schema="new", year=y,
                          only=("sen", "gov", "house") if y != 2024 else ("sen", "gov", "house", "pres")))
    return s


_CLS = """case
  when regexp_matches(O, '^US SENATE') then 'sen'
  when regexp_matches(O, '^GOVERNOR( AND | ?/ ?|$)') then 'gov'
  when O = 'US HOUSE' then 'house'
  when O = 'US PRESIDENT' then 'pres'
  when regexp_matches(O, '^STRAIGHT (PARTY|TICKET)$') then 'straight'
  end"""


def _select(src: dict) -> str:
    """SQL producing normalised rows (st, cls, special, stage, district, fips, cname, jur, jname, mode, prec, cand, party,
    votes) from one raw file; columns a file lacks become NULL (WY 2018 has no county_fips)."""
    import duckdb
    path = src["path"]() if callable(src["path"]) else src["path"]
    f = str(path).replace("'", "''")
    rd = f"read_csv('{f}', all_varchar=true, ignore_errors=true, strict_mode=false)"
    have = set(duckdb.connect().execute(f"select * from {rd} limit 0").df().columns)

    def col(*names, expr="{c}", default="NULL"):
        for n in names:
            if n in have:
                return expr.format(c=n)
        return default
    if src["schema"] == "new":
        ps, pd_ = col("party_simplified", expr="upper(trim({c}))"), col("party_detailed", expr="upper(trim({c}))")
        # party_simplified is wrong or empty in places (IL 2022 'DEMOCRATIC', UT / WY / VT 2020-22): the detailed party decides
        party = (f"case when {ps} in ('DEMOCRAT', 'REPUBLICAN') then {ps} when {pd_} like 'DEMOCRAT%' then 'DEMOCRAT' "
                 f"when {pd_} like 'REPUBLICAN%' then 'REPUBLICAN' else {ps} end")
    else:
        party = """case when lower(party) like 'democrat%' then 'DEMOCRAT' when lower(party) like 'republican%'
                   then 'REPUBLICAN' when party is null or party in ('', 'NA') then NULL else 'OTHER' end"""
    cols = dict(O=col("office", expr="upper(trim({c}))"), st=col("state_po", "state_postal", expr="upper({c})"),
                stage=col("stage", expr="upper(trim({c}))"), sp=col("special", expr="upper(trim({c}))", default="'FALSE'"),
                dist=col("district", expr="upper(trim(coalesce({c},'')))", default="''"), fips=col("county_fips"),
                cname=col("county_name", "county", expr="upper(replace(trim({c}),' County',''))"),
                jur=col("jurisdiction_fips"), jname=col("jurisdiction_name", "jurisdiction", expr="upper(trim({c}))"),
                mode=col("mode", expr="upper(trim({c}))", default="'TOTAL'"), prec=col("precinct"),
                cand=col("candidate", expr="upper(trim({c}))"), party=party, votes=col("votes"))
    sel = ", ".join(f"{v} as {k}" for k, v in cols.items())
    return f"select {sel} from {rd}"


def extract_one(src: dict) -> pd.DataFrame:
    import duckdb
    con = duckdb.connect()
    CLS_IN = ", ".join(f"'{c}'" for c in list(src.get("only") or ["sen", "gov", "house"]) + ["straight"])
    sql = f"""
    with r0 as ({_select(src)}),
    r1 as (select *, {_CLS} as cls from r0),
    r2 as (
      select cls, st, {src['year']} as year,
             case when sp in ('TRUE','T','1') then 1 else 0 end as special,
             case when stage like '%RUNOFF%' then 'RUNOFF' when stage in ('GEN','GENERAL') then 'GEN' else stage end as stage,
             case when cls = 'house' and not regexp_matches(dist, '^(AT[- ]LARGE|STATEWIDE|NA|0+)$')
                  then regexp_replace(dist, '^0+', '') else '' end as district,
             case when fips is null or fips in ('', 'NA') then NULL else lpad(regexp_replace(fips, '\\.0$', ''), 5, '0') end as fips,
             cname, jur, jname,
             case when mode = 'TOTAL' then 1 else 0 end as is_total,
             prec, cand, party, try_cast(replace(votes, ',', '') as double) as votes
      from r1 where cls in ({CLS_IN})
        and not regexp_matches(coalesce(prec, ''), 'COUNTY TOTAL')
        and (cls <> 'pres' or st = 'CT')
    ),
    r3 as (select * from r2 where stage in ('GEN','RUNOFF'))
    select * from r3"""
    return con.execute(sql).df()


def _clean_extract(df: pd.DataFrame, src: dict) -> pd.DataFrame:
    if src.get("states"):
        df = df[df["st"].isin(src["states"])]
    return df


def _apply_straight(df: pd.DataFrame) -> pd.DataFrame:
    """MEDSL 2016 (AL, IN, KY, SC) lists party-line ballots as separate rows (office 'Straight Party'/'Straight ticket',
    candidate '<State> Democratic Party'). Copy the Democratic / Republican ones into every contest of the file as
    candidate 'PARTYLINE (D|R)'; House rows get the district of the precinct (precincts in several districts are
    skipped). build() keeps the party-line votes where they bring the state total closer to the official one."""
    s = df[df["cls"] == "straight"]
    rest = df[df["cls"] != "straight"]
    s = s[s["party"].isin(["DEMOCRAT", "REPUBLICAN"])]
    if s.empty:
        return rest
    out = [rest]
    for cls in sorted(rest["cls"].unique()):
        base = rest[rest["cls"] == cls]
        ss = s[s["st"].isin(base["st"].unique())]
        if cls == "house":
            dm = base[["st", "fips", "prec", "district"]].drop_duplicates()
            one = dm.groupby(["st", "fips", "prec"])["district"].transform("nunique") == 1
            ss = ss.drop(columns="district").merge(dm[one], on=["st", "fips", "prec"], how="inner")
        else:
            ss = ss.assign(district="")
        out.append(ss.assign(cls=cls, special=0, stage="GEN", cand="PARTYLINE (" + ss["party"].str[0] + ")"))
    return pd.concat(out, ignore_index=True)


def _mode_overlap(df: pd.DataFrame) -> pd.DataFrame:
    """Per contest x county: do TOTAL-mode rows and mode rows describe the same precincts (then TOTAL rows duplicate the
    modes)? Same rule as brunder.us._medsl_county('overlap')."""
    key = ["cls", "st", "year", "special", "stage", "district", "fips", "jur"]
    k = df.assign(jur=df["jur"].fillna(""), fips=df["fips"].fillna(""))
    p = k.groupby(key + ["prec"], dropna=False)["is_total"].agg(["max", "min"]).reset_index()
    p["both"] = (p["max"] == 1) & (p["min"] == 0)
    ov = p.groupby(key)["both"].any().rename("overlap").reset_index()
    return k.merge(ov, on=key, how="left")


def aggregate_extract(df: pd.DataFrame) -> pd.DataFrame:
    """Candidate x county table with the mode-overlap flag (precincts are gone after this)."""
    df = df.copy()
    df["jur_keep"] = np.where((df["st"] == "CT") | df["fips"].isna(), df["jur"].fillna(df["jname"]), "")
    df = _mode_overlap(df.assign(jur=df["jur_keep"]))
    gk = ["cls", "st", "year", "special", "stage", "district", "fips", "cname", "jur", "is_total", "cand", "party",
          "overlap"]
    return df.groupby(gk, dropna=False, as_index=False)["votes"].sum()


def extract(force: bool = False) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for src in sources():
        out = CACHE / f"{src['key']}.parquet"
        if out.exists() and not force:
            continue
        lazy = callable(src["path"])
        path = src["path"]() if lazy else src["path"]
        df = _clean_extract(extract_one({**src, "path": path}), src)
        if lazy:
            Path(path).unlink()                                       # scratch copy of a zip member
        if not len(df):
            print(f"{src['key']}: no rows")
            continue
        agg = aggregate_extract(_apply_straight(df)).assign(src=src["key"])
        agg.to_parquet(out, index=False)
        print(f"{src['key']}: {len(df):,} rows -> {len(agg):,}", flush=True)


# ------------------------------------------------------------------ county identifiers

DEM_SIDE = {(2024, "NE", "sen", 0): "OSBORN"}      # independent running without a Democratic opponent (as us2026_priors ne_sen_2024)
LA_JUNGLE = {(2020, "LA", "sen"), (2022, "LA", "sen")}   # open-primary Senate races without a two-party final
CK = ["year", "st", "cls", "special", "stage", "district"]
FIPS_ST = {"al": "01", "ak": "02", "az": "04", "ar": "05", "ca": "06", "co": "08", "ct": "09", "de": "10", "dc": "11",
           "fl": "12", "ga": "13", "hi": "15", "id": "16", "il": "17", "in": "18", "ia": "19", "ks": "20", "ky": "21",
           "la": "22", "me": "23", "md": "24", "ma": "25", "mi": "26", "mn": "27", "ms": "28", "mo": "29", "mt": "30",
           "ne": "31", "nv": "32", "nh": "33", "nj": "34", "nm": "35", "ny": "36", "nc": "37", "nd": "38", "oh": "39",
           "ok": "40", "or": "41", "pa": "42", "ri": "44", "sc": "45", "sd": "46", "tn": "47", "tx": "48", "ut": "49",
           "vt": "50", "va": "51", "wa": "53", "wv": "54", "wi": "55", "wy": "56"}
ST_OF = {v: k.upper() for k, v in FIPS_ST.items()}


def _nm(s: pd.Series) -> pd.Series:
    return (s.fillna("").astype(str).str.upper().str.replace(r"[.'`]", "", regex=True)
            .str.replace(r"\s+(COUNTY|PARISH|CITY AND BOROUGH|BOROUGH|CENSUS AREA)$", "", regex=True).str.strip())


def _countypres() -> pd.DataFrame:
    d = pd.read_csv(RAW / "medsl" / "countypres_2000-2024.csv", dtype={"county_fips": str})
    return d[d["year"].between(2008, 2024)].copy()


def _county_names() -> dict[tuple[str, str], str]:
    d = _countypres().dropna(subset=["county_fips"])
    d["fips"] = d["county_fips"].str.replace(r"\.0$", "", regex=True).str.zfill(5).replace(FIPS_RENAMED)
    return {(a, b): c for a, b, c in d[["state_po", "county_name", "fips"]].assign(county_name=lambda x: _nm(x["county_name"]))
            .drop_duplicates().itertuples(index=False)}


def _ct_maps() -> tuple[dict[str, str], dict[str, str]]:
    """CT town -> planning region (the 2022+ county equivalents): by Census cousub code and by upper-case town name."""
    t = pd.read_csv(io.BytesIO(us._fetch(us._CT_REGIONS, RAW / "medsl24" / "ct_cousubs_2023.txt")), sep="\t", dtype=str)
    t.columns = [c.strip() for c in t.columns]
    g = t["GEOID"].str.strip()
    names = t["NAME"].str.strip().str.upper().str.replace(r"\s+(TOWN|CITY|BOROUGH)$", "", regex=True)
    return {a[-5:]: a[:5] for a in g}, dict(zip(names, g.str[:5]))


def assign_units(d: pd.DataFrame) -> pd.DataFrame:
    """Final unit key per row: 5-digit county FIPS; CT: planning region from the town; AK: house district ('02' + code)
    kept as its own unit kind; rows without a county identifier get one from (state, county name)."""
    d = d.copy()
    names = _county_names()
    f = d["fips"].astype("string")
    miss = f.isna()
    f[miss] = [names.get((st, n)) for st, n in zip(d.loc[miss, "st"], _nm(d.loc[miss, "cname"]))]
    f = f.replace(FIPS_RENAMED)
    ct = d["st"] == "CT"
    by_code, by_name = _ct_maps()
    jur = d["jur"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    reg = jur.where(jur.str.fullmatch(r"\d{10}"), "").str[-5:].map(by_code)
    alias = {"NEW MILLFORD": "NEW MILFORD"}                     # spelling in the MEDSL 2016 files
    reg = reg.fillna(d["jur"].fillna("").map(lambda x: by_name.get(alias.get(str(x).upper(), str(x).upper()))))
    f[ct] = reg[ct]
    d["unit"] = f.astype(object)
    d["kind"] = np.where(d["st"] == "AK", "ak_district", "county")
    u = d["unit"].astype(str)
    sfx = d["st"].str.lower().map(FIPS_ST)
    lost = d["unit"].isna() | ~(u.str.fullmatch(r"\d{5}") & (u.str[:2] == sfx) & (u.str[2:] != "000"))   # e.g. Kansas City MO (36000)
    d.loc[lost, "kind"] = "other"
    d.loc[lost, "unit"] = d.loc[lost, "st"].str.lower() + "-other"
    return d


def _ga_runoff_2021() -> pd.DataFrame:
    """Georgia January 5, 2021 federal runoffs (GA SoS Enhanced Voting, certified; ballot items cached in raw/us/ga)."""
    import json
    rows = []
    names = _county_names()
    for fn, special in (("2021JanFedRun_sen.json", 0), ("2021JanFedRun_sens.json", 1)):
        p = RAW / "ga" / fn
        if not p.exists():
            continue
        b = json.loads(p.read_text())["ballotItemWithBreakdown"]
        for br in b["breakdownResults"]:
            fips = names[("GA", _nm(pd.Series([br["locality"]["name"][0]["text"]])).iat[0])]
            for o in br["ballotOptions"]:
                party = ((o.get("party") or {}).get("abbreviation") or "").upper()
                rows.append(dict(cls="sen", st="GA", year=2020, special=special, stage="RUNOFF", district="", fips=fips,
                                 cname=None, jur="", is_total=1, cand=o["name"][0]["text"].upper(),
                                 party={"DEM": "DEMOCRAT", "REP": "REPUBLICAN"}.get(party, "OTHER"),
                                 overlap=False, votes=float(o["voteCount"])))
    return pd.DataFrame(rows)


def _ca_senate_2022() -> pd.DataFrame:
    """California 2022 US Senate (regular 'US Senate - 1' and unexpired term 'US Senate - 3'): MEDSL's 2022 California file has no
    Senate rows; CA Secretary of State Statement of Vote (county x candidate, certified), cached by brunder.us._ca_sov."""
    p = RAW / "ca" / "2022_csv-candidates.xlsx"
    if not p.exists():
        return pd.DataFrame()
    rows = us._xlsx_rows(p.read_bytes())
    head = [str(h).strip() if h else "" for h in rows[0]]
    df = pd.DataFrame([r + [None] * (len(head) - len(r)) for r in rows[1:]], columns=head)
    df = df[df["Contest Name"].astype(str).str.startswith("US Senate")]
    names = _county_names()
    party = df["Party Name"].fillna("").str.upper()
    return pd.DataFrame(dict(
        cls="sen", st="CA", year=2022, special=np.where(df["Contest Name"].str.contains("Partial"), 1, 0), stage="GEN", district="",
        fips=[names[("CA", n)] for n in _nm(df["County Name"])], cname=None, jur="", is_total=1,
        cand=df["Candidate Name"].str.upper().str.strip(),
        party=np.where(party.str.startswith("DEM"), "DEMOCRAT", np.where(party.str.startswith("REP"), "REPUBLICAN", "OTHER")),
        overlap=False, votes=pd.to_numeric(df["Vote Total"], errors="coerce").fillna(0.0), src="ca_sov_2022")).reset_index(drop=True)


def load_raw() -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(p) for p in sorted(CACHE.glob("*.parquet"))] + [_ga_runoff_2021(), _ca_senate_2022()],
                  ignore_index=True)
    d = d[d["cls"] != "straight"]                                  # unallocated party-line rows (pres) are not used
    d = d[~((d["src"] == "2020_house") & d["st"].isin([x.upper() for x in STATES20]))]   # the state files are the better source
    d = d[~d["cand"].fillna("").str.contains(NOT_CAND, regex=True)]
    d["src"] = d["src"].fillna("ga_runoff_2021")
    d["fips"] = d["fips"].replace("", np.nan)
    d.loc[(d["year"] == 2016) & (d["st"] == "OR") & (d["cls"] == "gov"), "special"] = 1   # 2016 Oregon: election for the unexpired term (MEDSL flags it FALSE)
    d["votes"] = d["votes"].fillna(0.0)
    d["overlap"] = d["overlap"].fillna(False).astype(bool)
    d["district"] = d["district"].fillna("")
    return assign_units(d)


def fill_party(raw: pd.DataFrame, names: pd.DataFrame) -> pd.DataFrame:
    """Rows without a party label (WI 2018, GA / KY / ND 2016, NY ...): party of the same candidate (first initial + surname) in
    the official statewide Senate / House files (MEDSL 1976-2024 Senate, House constituency returns)."""
    raw = raw.copy()
    m = raw["party"].isna() & raw["cls"].isin(["sen", "house"])
    key = raw.loc[m, ["cls", "year", "st", "district"]].assign(ck=ckey(raw.loc[m, "cand"]))
    got = key.merge(names.rename(columns={"_bp": "p2"}), on=["cls", "year", "st", "district", "ck"], how="left")["p2"]
    raw.loc[m, "party"] = got.to_numpy()
    raw.loc[m & raw["party"].notna(), "psrc"] = "ref_name"
    return raw


# ------------------------------------------------------------------ resolve: mode rule, party of each candidate

def resolve(d: pd.DataFrame, rule: str, straight: bool) -> pd.DataFrame:
    """Candidate x unit votes of every contest under one vote-mode rule (brunder.us._medsl_county): 'overlap' drops TOTAL rows
    where they duplicate mode rows, 'total_first' uses TOTAL rows where they carry D/R votes, 'all' sums every row."""
    if not straight:
        d = d[~d["cand"].str.startswith("PARTYLINE")]
    tot = d["is_total"] == 1
    if rule == "overlap":
        k = d[~(tot & d["overlap"])]
    elif rule == "total_first":
        major = (tot & d["party"].isin(["DEMOCRAT", "REPUBLICAN"]) & (d["votes"] > 0))
        use = major.groupby([d[c] for c in CK + ["unit"]]).transform("any").astype(bool)
        k = d[(use & tot) | (~use & ~tot)]
    else:
        k = d
    return k.groupby(CK + ["unit", "kind", "cand", "party"], dropna=False, as_index=False)["votes"].sum()


def assign_party(r: pd.DataFrame, ovr: pd.DataFrame | None = None) -> pd.DataFrame:
    """bp = the major party a candidate's votes were mostly cast under (fusion lines); DEM_SIDE independents count as D.
    `ovr` (CK + cand + obp) replaces the labelling of whole contests (see relabel_gov)."""
    cp = r[r["party"].isin(["DEMOCRAT", "REPUBLICAN"])].groupby(CK + ["cand", "party"], as_index=False)["votes"].sum()
    tn = r[r["party"].notna()].groupby(CK + ["cand"], as_index=False)["votes"].sum().rename(columns={"votes": "tn"})
    cp = cp.merge(tn, on=CK + ["cand"])
    cp = cp[cp["votes"] >= 0.5 * cp["tn"]]                       # a D / R line must carry at least half of the candidate's labelled votes
    cp = cp.sort_values("votes").drop_duplicates(CK + ["cand"], keep="last")[CK + ["cand", "party"]].rename(columns={"party": "bp"})
    r = r.merge(cp, on=CK + ["cand"], how="left")
    r["bp"] = r["bp"].fillna("OTHER")
    for (y, st, cls, sp), name in DEM_SIDE.items():
        m = (r["year"] == y) & (r["st"] == st) & (r["cls"] == cls) & (r["special"] == sp) & r["cand"].str.contains(name)
        r.loc[m & (r["bp"] == "OTHER"), "bp"] = "DEMOCRAT"
    if ovr is not None and len(ovr):
        flag = r[CK].merge(ovr[CK].drop_duplicates().assign(_o=1), on=CK, how="left")["_o"].notna().to_numpy()
        r.loc[flag, "bp"] = "OTHER"
        obp = r[CK + ["cand"]].merge(ovr[CK + ["cand", "obp"]], on=CK + ["cand"], how="left")["obp"].to_numpy()
        r["bp"] = np.where(pd.notna(obp), obp, r["bp"])
    return r


def unit_table(r: pd.DataFrame) -> pd.DataFrame:
    g = r.assign(dem=r["votes"].where(r["bp"] == "DEMOCRAT", 0.0), rep=r["votes"].where(r["bp"] == "REPUBLICAN", 0.0))
    return g.groupby(CK + ["unit", "kind"], as_index=False).agg(dem=("dem", "sum"), rep=("rep", "sum"), tot=("votes", "sum"))


def contested(dem, rep):
    """A House district counts when both parties' candidates drew at least 2% of the two-party vote (drops write-in lines)."""
    v = dem + rep
    return (v > 0) & (np.minimum(dem, rep) >= 0.02 * v)


def house_filter(u: pd.DataFrame) -> pd.DataFrame:
    """House: keep districts with Democratic and Republican votes (contested), sum them to unit level; votes of the other
    districts are returned as `unc`."""
    h = u[u["cls"] == "house"]
    c = h.groupby(CK)[["dem", "rep"]].transform("sum")
    ok = contested(c["dem"], c["rep"])
    keep = h[ok]
    unc = h[~ok].groupby(["year", "st", "special", "stage", "unit"])["tot"].sum().rename("unc")
    a = keep.groupby(["year", "st", "cls", "special", "stage", "unit", "kind"], as_index=False)[["dem", "rep", "tot"]].sum()
    a["district"] = ""
    a = a.merge(unc.reset_index(), on=["year", "st", "special", "stage", "unit"], how="outer")
    for c_ in ("cls", "kind"):
        a[c_] = a[c_].fillna({"cls": "house", "kind": "county"}[c_])
    a["district"] = ""
    a[["dem", "rep", "tot", "unc"]] = a[["dem", "rep", "tot", "unc"]].fillna(0.0)
    nu = u[u["cls"] != "house"].assign(unc=0.0)
    return pd.concat([nu, a], ignore_index=True)


# ------------------------------------------------------------------ official references

def _cand_party(df: pd.DataFrame, keys: list[str], votes: str, party: str) -> pd.DataFrame:
    """Candidate -> D / R / OTHER by the same rule as assign_party, then statewide dem / rep / total per `keys`."""
    df = df.copy()
    df["_v"] = pd.to_numeric(df[votes], errors="coerce").fillna(0.0)
    ck = keys + ["candidate"]
    m = df[df[party].isin(["DEMOCRAT", "REPUBLICAN"])].groupby(ck + [party], as_index=False)["_v"].sum()
    tn = df.groupby(ck, as_index=False)["_v"].sum().rename(columns={"_v": "_tn"})
    m = m.merge(tn, on=ck)
    m = m[m["_v"] >= 0.5 * m["_tn"]]
    m = m.sort_values("_v").drop_duplicates(ck, keep="last")[ck + [party]].rename(columns={party: "_bp"})
    df = df.merge(m, on=ck, how="left")
    df["_bp"] = df["_bp"].fillna("OTHER")
    return df


SUFFIX = {"JR", "SR", "II", "III", "IV", "V"}


def ckey(name: pd.Series) -> pd.Series:
    """Match key of a candidate name across files: first initial + surname ('GWEN S. MOORE' / 'Moore, Gwen' -> 'G MOORE')."""
    def one(x: str) -> str:
        x = str(x).upper().replace(".", " ").replace("-", "")
        x = re.sub(r"[^A-Z, ]", "", x)
        if "," in x:
            last, _, first = x.partition(",")
            toks = first.split() + last.split()
        else:
            toks = x.split()
        toks = [t for t in toks if t not in SUFFIX]
        return f"{toks[0][0]} {toks[-1]}" if len(toks) > 1 else (toks[0] if toks else "")
    return name.map(one)


def _pfix(simplified: pd.Series, detailed: pd.Series) -> pd.Series:
    """Party of a reference row: party_simplified, corrected by party_detailed where that one names D / R."""
    sp, de = simplified.fillna("").str.upper(), detailed.fillna("").str.upper()
    return pd.Series(np.where(sp.isin(["DEMOCRAT", "REPUBLICAN"]), sp, np.where(de.str.startswith("DEMOCRAT"), "DEMOCRAT",
                     np.where(de.str.startswith("REPUBLICAN"), "REPUBLICAN", "OTHER"))), index=simplified.index)


def refs() -> dict[str, pd.DataFrame]:
    out = {}
    s = pd.read_csv(RAW / "medsl_hist" / "1976-2024-senate-state.tab", dtype=str)
    s["y"] = s["year"].astype(int)
    s = s[s["y"] >= 2008]
    s["stage"] = np.where(s["stage"].str.upper().str.contains("RUNOFF"), "RUNOFF", "GEN")
    s["year"] = np.where(s["y"] == 2021, 2020, s["y"])
    s["special"] = s["special"].str.upper().eq("TRUE").astype(int)
    s["st"] = s["state_po"]
    s["candidate"] = s["candidate"].str.upper()
    s["party_simplified"] = _pfix(s["party_simplified"], s["party_detailed"])
    k = ["year", "st", "special", "stage"]
    s = _cand_party(s, k, "candidatevotes", "party_simplified")
    for (y, st, cls, sp), name in DEM_SIDE.items():
        s.loc[(s["year"] == y) & (s["st"] == st) & (s["special"] == sp) & s["candidate"].str.contains(name), "_bp"] = "DEMOCRAT"
    s["dem"] = s["_v"].where(s["_bp"] == "DEMOCRAT", 0.0)
    s["rep"] = s["_v"].where(s["_bp"] == "REPUBLICAN", 0.0)
    out["sen"] = (s[s["year"] >= 2016].groupby(k, as_index=False)
                  .agg(rdem=("dem", "sum"), rrep=("rep", "sum"), rtot=("_v", "sum")).assign(cls="sen"))
    top = s.groupby(k + ["candidate"], as_index=False).agg(v=("_v", "sum"), bp=("_bp", "first"))
    top = top.sort_values("v").drop_duplicates(k, keep="last")
    top["party"] = top["bp"].map({"DEMOCRAT": "D", "REPUBLICAN": "R"}).fillna("I")
    out["sen_winner"] = top[k + ["party"]]
    nm = s[s["_bp"].isin(["DEMOCRAT", "REPUBLICAN"]) & (s["year"] >= 2016)].assign(ck=lambda x: ckey(x["candidate"]), cls="sen", district="")
    names = [nm[["cls", "year", "st", "district", "ck", "_bp"]]]
    p = pd.read_csv(RAW / "medsl_hist" / "1976-2024-president-state.csv", dtype=str)
    p["year"] = p["year"].astype(int)
    p = p[p["year"] >= 2008]
    p["st"] = p["state_po"]
    p["candidate"] = p["candidate"].str.upper()
    p["party_simplified"] = _pfix(p["party_simplified"], p["party_detailed"])
    k = ["year", "st"]
    p = _cand_party(p, k, "candidatevotes", "party_simplified")
    p["dem"] = p["_v"].where(p["_bp"] == "DEMOCRAT", 0.0)
    p["rep"] = p["_v"].where(p["_bp"] == "REPUBLICAN", 0.0)
    out["pres"] = p.groupby(k, as_index=False).agg(rdem=("dem", "sum"), rrep=("rep", "sum"), rtot=("_v", "sum")).assign(
        cls="pres", special=0, stage="GEN")
    def _party(x: pd.Series) -> pd.Series:
        u = x.fillna("").str.upper()
        return pd.Series(np.where(u.str.startswith("DEMOCRAT"), "DEMOCRAT", np.where(u.str.startswith("REPUBLICAN"), "REPUBLICAN", "OTHER")),
                         index=x.index)
    h18 = pd.read_csv(RAW / "house" / "1976-2018-house.csv", dtype=str, encoding="latin1")
    h18 = h18[h18["year"].astype(int).isin([2016, 2018])].rename(columns={"state_po": "st"})
    h18["party"] = _party(h18["party"])
    h20 = pd.read_csv(RAW / "medsl_hist" / "1976-2024-house.tab", dtype=str).rename(columns={"state_po": "st"})
    h20 = h20[h20["year"].astype(int) >= 2020]
    h20["party"] = _party(h20["party"])
    h = pd.concat([h18, h20], ignore_index=True)
    h = h[h["stage"].str.upper().eq("GEN") & ~h["runoff"].str.upper().eq("TRUE") & h["special"].str.upper().ne("TRUE")]
    h["year"] = h["year"].astype(int)
    h["district"] = (h["district"].astype(str).str.upper().str.replace(r"\.0$", "", regex=True).str.lstrip("0")
                     .replace({"AT-LARGE": "", "AT LARGE": "", "STATEWIDE": "", "NAN": ""}))
    h = h.drop_duplicates(["year", "st", "district", "candidate", "party", "candidatevotes"])
    h["candidate"] = h["candidate"].str.upper()
    k = ["year", "st", "district"]
    h = _cand_party(h, k, "candidatevotes", "party")
    names.append(h[h["_bp"].isin(["DEMOCRAT", "REPUBLICAN"])].assign(ck=lambda x: ckey(x["candidate"]), cls="house")
                 [["cls", "year", "st", "district", "ck", "_bp"]])
    h["dem"] = h["_v"].where(h["_bp"] == "DEMOCRAT", 0.0)
    h["rep"] = h["_v"].where(h["_bp"] == "REPUBLICAN", 0.0)
    dd = h.groupby(k, as_index=False).agg(dem=("dem", "sum"), rep=("rep", "sum"), tot=("_v", "sum"))
    out["names"] = pd.concat(names, ignore_index=True).drop_duplicates(["cls", "year", "st", "district", "ck"], keep=False)
    out["house_dist"] = dd
    cont = contested(dd["dem"], dd["rep"])
    out["house"] = dd[cont].groupby(["year", "st"], as_index=False).agg(rdem=("dem", "sum"), rrep=("rep", "sum"), rtot=("tot", "sum")).assign(
        cls="house", special=0, stage="GEN")
    g = pd.read_csv(PUBLIC / "us_state_drift_panel.csv")
    g = g[(g["office"] == "gov") & g["act"].notna() & (g["cycle"] >= 2016)]
    out["gov"] = pd.DataFrame({"year": g["cycle"], "st": g["state"].str.upper(), "special": g["special"].astype(int),
                               "stage": "GEN", "ref_s2": g["act"] / 100.0, "cls": "gov"})
    return out


# ------------------------------------------------------------------ main assembly

RULES = [("overlap", False), ("total_first", False), ("all", False), ("overlap", True), ("total_first", True), ("all", True)]


def _statewide(u: pd.DataFrame) -> pd.DataFrame:
    return u.groupby(["year", "st", "cls", "special", "stage"], as_index=False)[["dem", "rep", "tot"]].sum()


def _ref_for(sw: pd.DataFrame, R: dict) -> pd.DataFrame:
    parts = []
    for cls, g in sw.groupby("cls"):
        r = R.get(cls)
        if r is None:
            continue
        on = ["year", "st", "special", "stage"]
        m = g.merge(r.drop(columns="cls"), on=on, how="left")
        parts.append(m)
    m = pd.concat(parts, ignore_index=True)
    s2 = m["dem"] / (m["dem"] + m["rep"]).replace(0, np.nan)
    if "rdem" in m:
        m["ref_s2"] = m["ref_s2"] if "ref_s2" in m else np.nan
        rs2 = m["rdem"] / (m["rdem"] + m["rrep"]).replace(0, np.nan)
        m["ref_s2"] = m["ref_s2"].where(m["ref_s2"].notna(), rs2)
    m["s2"] = s2
    m["dev"] = (m["s2"] - m["ref_s2"]).abs() * 100
    return m


def pres_units() -> pd.DataFrame:
    """President 2008-2024 by county from the MEDSL county file (as analysis/us_fundamentals.pres_results): TOTAL rows where a
    county has them, else the sum of its modes. AK: house districts; CT 2008/2012: the former counties (state totals only);
    CT 2016+ comes from the precinct files (planning regions); Kansas City MO, Maine UOCAVA, RI federal precinct and statewide
    write-ins are reported outside the counties ('other')."""
    d = _countypres()
    d = d[~d["candidate"].fillna("").str.upper().str.contains(NOT_CAND, regex=True)].copy()
    d["st"] = d["state_po"]
    d = d[~((d["st"] == "CT") & (d["year"] >= 2016))]
    cf = d["county_fips"].fillna("").str.replace(r"\.0$", "", regex=True)
    cf = cf.where(cf.str.len() > 5, cf.str.zfill(5)).where(cf != "", "")           # the file drops leading zeros
    other = (cf.str.len() != 5) | d["county_name"].fillna("").str.contains("KANSAS CITY|UOCAVA|FEDERAL PRECINCT|STATEWIDE", regex=True)
    d["unit"] = np.where(other, d["st"].str.lower() + "-other", cf.replace(FIPS_RENAMED))
    d["kind"] = np.where(other, "other", np.where(d["st"] == "AK", "ak_district", np.where(d["st"] == "CT", "ct_old_county", "county")))
    tot = d["mode"].fillna("TOTAL").str.upper() == "TOTAL"
    has = tot.groupby([d["year"], d["unit"]]).transform("any").astype(bool)
    d = d[(has & tot) | (~has & ~tot)]
    d["cand"] = d["candidate"].str.upper()
    cp = d[d["party"].isin(["DEMOCRAT", "REPUBLICAN"])].groupby(["year", "st", "cand", "party"], as_index=False)["candidatevotes"].sum()
    cp = cp.sort_values("candidatevotes").drop_duplicates(["year", "st", "cand"], keep="last")[["year", "st", "cand", "party"]]
    d = d.drop(columns="party").merge(cp, on=["year", "st", "cand"], how="left")        # fusion lines count for the candidate's party
    d["dem"] = d["candidatevotes"].where(d["party"] == "DEMOCRAT", 0.0)
    d["rep"] = d["candidatevotes"].where(d["party"] == "REPUBLICAN", 0.0)
    u = d.groupby(["year", "st", "unit", "kind"], as_index=False).agg(dem=("dem", "sum"), rep=("rep", "sum"), tot=("candidatevotes", "sum"))
    return u.assign(cls="pres", special=0, stage="GEN", district="", unc=0.0)


def choose(sw: pd.DataFrame) -> pd.DataFrame:
    """One rule variant per contest group: the one whose statewide two-party share is closest to the official one (ties, and
    groups without an official reference: the default 'overlap' without party-line votes; party-line votes follow the state's
    other contests of the year when those needed them)."""
    g = ["year", "st", "cls", "special", "stage"]
    sw = sw.copy()
    sw["order"] = [RULES.index((r, st)) for r, st in zip(sw["rule"], sw["straight"])]
    sw["d"] = np.where(sw["ref_s2"].notna(), sw["dev"].fillna(1e9).round(3), 0.0)      # a variant without any D/R votes never wins
    best = sw.sort_values(g + ["d", "order"]).drop_duplicates(g)
    need = best[best["straight"] & best["dev"].notna()].groupby(["year", "st"]).size().rename("sn").reset_index()
    nodev = best[best["dev"].isna()].merge(need, on=["year", "st"], how="left")
    nodev = nodev[nodev["sn"].notna()]
    if len(nodev):
        alt = sw.merge(nodev[g], on=g)
        alt = alt[(alt["rule"] == "overlap") & alt["straight"]]
        best = pd.concat([best.merge(nodev[g], on=g, how="left", indicator=True).query("_merge == 'left_only'").drop(columns="_merge"), alt])
    return best.drop(columns=["d", "order"])


def relabel_gov(best: pd.DataFrame, resolved: dict, tol: float = 0.15) -> pd.DataFrame:
    """Governor contests whose statewide two-party share misses the reference `act` (data_public/us_state_drift_panel.csv): MEDSL
    sometimes has no or a wrong party label (VT 2022 Siegel, WY 2022 Livingston ...). Try every ordered pair among the six
    largest candidates as (D, R); keep the pair when it reproduces `act` within `tol` points (precinct returns differ from the
    canvass by a few hundredths of a point) and no other pair is within 1 point of `act`; else leave the contest as labelled."""
    rows = []
    bad = best[(best["cls"] == "gov") & best["ref_s2"].notna() & ((best["dev"] > 0.3) | best["dev"].isna())]
    for _, b in bad.iterrows():
        r = resolved[(b["rule"], b["straight"])]
        r = r[(r["year"] == b["year"]) & (r["st"] == b["st"]) & (r["cls"] == "gov") & (r["special"] == b["special"]) & (r["stage"] == b["stage"])]
        c = r.groupby("cand")["votes"].sum().sort_values(ascending=False).head(6)
        pairs = sorted((abs(c[a] / (c[a] + c[z]) - b["ref_s2"]) * 100, a, z) for a in c.index for z in c.index if a != z)
        top = pairs[0] if pairs else None
        if top and top[0] < tol and (len(pairs) < 2 or pairs[1][0] > 1.0):
            key = {k: b[k] for k in ("year", "st", "cls", "special", "stage")} | {"district": ""}
            rows += [key | {"cand": top[1], "obp": "DEMOCRAT"}, key | {"cand": top[2], "obp": "REPUBLICAN"}]
    return pd.DataFrame(rows)


def assemble() -> dict:
    R = refs()
    raw = fill_party(load_raw(), R["names"])
    g = ["year", "st", "cls", "special", "stage"]
    ovr = None
    for _pass in range(2):
        resolved = {k: resolve(raw, *k) for k in RULES}
        variants = {k: house_filter(unit_table(assign_party(r, ovr))) for k, r in resolved.items()}
        sw = pd.concat([_ref_for(_statewide(u), R).assign(rule=k[0], straight=k[1]) for k, u in variants.items()], ignore_index=True)
        best = choose(sw)
        if _pass == 0:
            ovr = relabel_gov(best, resolved)
    best["fix"] = ""
    if ovr is not None and len(ovr):
        fx = ovr.groupby(g)["cand"].agg(lambda x: "party relabelled: " + ", ".join(x)).rename("fix").reset_index()
        best = best.drop(columns="fix").merge(fx, on=g, how="left").fillna({"fix": ""})
    parts = []
    for (rule, straight), grp in best.groupby(["rule", "straight"]):
        parts.append(variants[(rule, straight)].merge(grp[g], on=g))
    units = pd.concat(parts + [pres_units()], ignore_index=True)
    pres_sw = _ref_for(_statewide(units[units["cls"] == "pres"]), R).assign(rule="county_file", straight=False, fix="")
    pres_sw = pres_sw.merge(best[g].assign(_k=1), on=g, how="left")                  # CT 2016+ comes from the precinct files
    pres_sw = pres_sw[pres_sw["_k"].isna()].drop(columns="_k")
    return dict(units=units, best=pd.concat([best, pres_sw], ignore_index=True), sw=sw, raw=raw, R=R, ovr=ovr)


# ------------------------------------------------------------------ final tables

CONTEST = {"pres": "pres", "sen": "sen", "gov": "gov", "house": "house_total"}
OFFICE = {"pres": "pres", "sen": "sen", "gov": "gov", "house": "house_total"}
PRES_INC = {2008: "R", 2012: "D", 2016: "D", 2020: "R", 2024: "D"}     # party of the president on election day
# party holding the seat when it differs from the winner of the seat's previous regular election (changes of party or of
# officeholder between two elections; public record): Doug Jones (D) won the 2017 Alabama special, Mark Kelly (D) the 2020
# Arizona and Raphael Warnock (D) the January 2021 Georgia special runoffs; Kyrsten Sinema left the Democratic Party in
# December 2022 and Joe Manchin registered independent in May 2024; Jim Justice (D, elected 2016) became a Republican in 2017.
INC_OVERRIDE = {("sen", 2020, "AL"): "D", ("sen", 2022, "AZ"): "D", ("sen", 2022, "GA"): "D", ("sen", 2024, "AZ"): "I",
                ("sen", 2024, "WV"): "I", ("gov", 2020, "WV"): "R", ("gov", 2016, "OR"): "D"}
# Senate specials: party of the seat's holder (appointee / predecessor) before the special
SPECIAL_HOLDER = {(2018, "MN"): "D", (2018, "MS"): "R", (2020, "AZ"): "R", (2020, "GA"): "R", (2022, "CA"): "D",
                  (2022, "OK"): "R", (2024, "CA"): "D", (2024, "NE"): "R"}
GOV_TERM2 = {"NH", "VT"}                                              # two-year terms
GOV_IND = {(2014, "AK"): "I"}                                         # independent winners before 2016 (panel `act` is two-party D %)


def _winners(R: dict, units: pd.DataFrame) -> dict:
    """Winner party (D / R / I) of each past contest: senate 1976-2024 state file (candidate with most votes, party of the
    candidate by the usual rule), governor 2016-2024 from the county tables, governor before 2016 from the drift panel `act`."""
    w = {}
    sw = R["sen_winner"]
    for r in sw.itertuples(index=False):
        w[("sen", r.year, r.st, r.special, r.stage)] = r.party
    g = units[units["cls"] == "gov"].groupby(["year", "st", "special", "stage"])[["dem", "rep", "tot"]].sum().reset_index()
    for r in g.itertuples(index=False):
        if r.dem + r.rep > 0:
            w[("gov", r.year, r.st, r.special, r.stage)] = "D" if r.dem > r.rep else "R"
    pan = pd.read_csv(PUBLIC / "us_state_drift_panel.csv")
    pan = pan[(pan["office"] == "gov") & pan["act"].notna()]
    for r in pan.itertuples(index=False):
        k = ("gov", int(r.cycle), r.state.upper(), int(r.special), "GEN")
        if k not in w:
            w[k] = GOV_IND.get((int(r.cycle), r.state.upper()), "D" if r.act > 50 else "R")
    return w


def incumbent(office: str, year: int, st: str, special: int, stage: str, w: dict) -> tuple[str, str]:
    if office == "pres":
        return PRES_INC.get(year, ""), "president on election day"
    if office == "sen":
        if (year, st) in SPECIAL_HOLDER and special:
            return SPECIAL_HOLDER[(year, st)], "manual: holder of the vacated seat"
        if special:
            return "", ""
        if ("sen", year, st) in INC_OVERRIDE:
            return INC_OVERRIDE[("sen", year, st)], "manual: party change / special winner"
        if year == 2022 and st == "GA" and stage == "RUNOFF":
            return "D", "manual: Warnock"
        prev = year - 6
        for sp, stg in ((0, "RUNOFF"), (0, "GEN"), (1, "GEN")):      # 2010 Illinois: the regular race was the special's twin
            k = ("sen", prev, st, sp, stg)
            if k in w:
                return w[k], "winner of the seat's previous regular election"
        return "", ""
    if office == "gov":
        if ("gov", year, st) in INC_OVERRIDE:
            return INC_OVERRIDE[("gov", year, st)], "manual: party change / succession"
        prev = year - (2 if st in GOV_TERM2 else 4)
        k = ("gov", prev, st, 0, "GEN")
        return (w[k], "winner of the previous regular election") if k in w else ("", "")
    return "", ""


def _multi(r: pd.DataFrame) -> set:
    """Contest groups with more than one D- or R-labelled candidate (jungle / multi-candidate pooling)."""
    c = r[(r["bp"].isin(["DEMOCRAT", "REPUBLICAN"])) & (r["votes"] > 0) & ~r["cand"].str.startswith("PARTYLINE")].groupby(CK + ["bp"])["cand"].nunique().reset_index()
    c = c[(c["cand"] > 1) & (c["cls"] != "house")]
    return set(zip(c["year"], c["st"], c["cls"], c["special"], c["stage"]))


def finalize(res: dict) -> dict:
    units, best, raw, R = res["units"], res["best"], res["raw"], res["R"]
    g = ["year", "st", "cls", "special", "stage"]
    w = _winners(R, units)
    # ---- contest-level metadata
    best = best.copy()
    best["s2"] = best["dem"] / (best["dem"] + best["rep"]).replace(0, np.nan)
    best["v2"] = best["dem"] + best["rep"]
    best["ref_v2"] = best["rdem"] + best["rrep"]
    best["cov"] = best["v2"] / best["ref_v2"]
    best["dev_dem_pct"] = (best["dem"] / best["rdem"].replace(0, np.nan) - 1) * 100
    best["dev_rep_pct"] = (best["rep"] / best["rrep"].replace(0, np.nan) - 1) * 100
    best["excluded"] = [(y, st, c) in LA_JUNGLE or (y == 2016 and st == "LA" and c == "sen")
                        for y, st, c in zip(best["year"], best["st"], best["cls"])]
    best.loc[best["excluded"], "reason"] = "excluded: Louisiana open (jungle) primary on the November ballot, no two-party final in the county data"
    best["status"] = np.select(
        [best["excluded"], best["s2"].isna() | (best["dem"] == 0) | (best["rep"] == 0), best["ref_s2"].isna(), best["dev"] <= 0.3],
        ["excluded", "no_two_party", "no_ref", "pass"], "fail")
    best = best[~((best["cls"] == "house") & (best["special"] == 1))]
    expl = {(2018, "MS", "sen", 1): "reference row is the Nov 27 runoff; county data is the Nov 6 first round (jungle, candidates pooled by party)",
            (2012, "NY", "pres", 0): "county file reports the Working Families / Independence fusion lines under OTHER",
            (2018, "ME", "house", 0): "ME-2 (ranked-choice race) is missing from the MEDSL 2018 Maine file",
            (2022, "AK", "gov", 0): "ranked-choice: MEDSL carries first-choice votes, the reference is the final round"}
    best["reason"] = best.get("reason", pd.Series("", index=best.index)).fillna("")
    for k, v in expl.items():
        m_ = (best["year"] == k[0]) & (best["st"] == k[1]) & (best["cls"] == k[2]) & (best["special"] == k[3])
        best.loc[m_ & (best["status"] == "fail"), "reason"] = v
    auto = (best["status"] == "fail") & (best["reason"] == "")
    best.loc[auto & (best["cov"] < 0.97), "reason"] = "incomplete: county data covers less of the official vote than the reference"
    best.loc[auto & (best["cov"] > 1.03), "reason"] = "county data exceeds the reference (double counting or incomplete reference)"
    best.loc[(best["status"] == "fail") & (best["reason"] == ""), "reason"] = "two-party share differs while vote totals agree (party labels or candidate pooling)"
    # ---- notes
    pr = raw[raw["psrc"].notna()] if "psrc" in raw else raw.iloc[0:0]
    from_ref = set(zip(pr["year"], pr["st"], pr["cls"], pr["special"], pr["stage"]))
    multi = _multi(assign_party(resolve(raw, "overlap", False)))
    best["note"] = ""
    for i, r in best.iterrows():
        key = (r["year"], r["st"], r["cls"], r["special"], r["stage"])
        n = []
        if r["rule"] not in ("overlap", "county_file"):
            n.append(f"mode_rule={r['rule']}")
        if r["straight"]:
            n.append("party_line_votes_added")
        if key in from_ref:
            n.append("party_from_ref_name")
        if key in multi:
            n.append("several_candidates_per_party_pooled")
        if (r["year"], r["st"], r["cls"], r["special"]) == (2024, "NE", "sen", 0):
            n.append("dem_side=Osborn(independent)")
        if r["st"] == "CT" and r["year"] >= 2016:
            n.append("ct_planning_regions")
        if r["st"] == "LA" and r["cls"] == "house":
            n.append("la_jungle_pooled")
        if r["st"] == "AK":
            n.append("by_state_house_district")
        if r["st"] == "MO" and r["cls"] == "pres":
            n.append("excludes_kansas_city_reported_outside_counties")
        if isinstance(r["fix"], str) and r["fix"]:
            n.append(r["fix"])
        if r["status"] == "no_two_party":
            n.append("no_contested_district" if r["cls"] == "house" else "no_dem_labelled" if r["dem"] == 0 else "no_rep_labelled")
        best.at[i, "note"] = ";".join(n)
    # ---- unit tables
    u = units.merge(best[g + ["status", "note", "excluded"]], on=g, how="inner")
    u = u[~u["excluded"]]
    u["office"] = u["cls"].map(OFFICE)
    base = u["cls"].map(CONTEST)
    u["contest"] = np.where(u["cls"] == "sen", "sen" + np.where(u["special"] == 1, "_special", "") + np.where(u["stage"] == "RUNOFF", "_runoff", ""),
                            np.where(u["cls"] == "gov", "gov" + np.where(u["special"] == 1, "_special", ""), base))
    u["race_type"] = np.where((u["stage"] == "RUNOFF") & (u["special"] == 1), "special_runoff", np.where(u["stage"] == "RUNOFF", "runoff",
                              np.where(u["special"] == 1, "special", "regular")))
    one_party = (u["status"] == "no_two_party")
    u["total_all"] = u["tot"]
    u["s2"] = np.where(one_party, np.nan, u["dem"] / (u["dem"] + u["rep"]).replace(0, np.nan))
    u["v2"] = u["dem"] + u["rep"]
    u["val_status"] = u["status"]
    u["fips"] = u["unit"]
    u["year"] = u["year"].astype(int)
    county = u[u["kind"] == "county"].copy()
    county["st"] = county["st"].str.lower()
    cols = ["fips", "st", "year", "office", "contest", "dem", "rep", "total_all", "s2", "v2", "note", "val_status", "unc_votes"]
    county["unc_votes"] = np.where(county["cls"] == "house", county["unc"], np.nan)
    county = county[cols].sort_values(["office", "contest", "year", "st", "fips"]).reset_index(drop=True)
    # state table
    ss = u.groupby(["st", "year", "office", "contest", "race_type", "cls", "special", "stage"], as_index=False).agg(
        dem=("dem", "sum"), rep=("rep", "sum"), total_all=("tot", "sum"), n_units=("unit", "nunique"),
        n_counties=("kind", lambda k: int((k == "county").sum())), val_status=("val_status", "first"), note=("note", "first"))
    ss["s2"] = np.where(ss["val_status"] == "no_two_party", np.nan, ss["dem"] / (ss["dem"] + ss["rep"]).replace(0, np.nan))
    ss["v2"] = ss["dem"] + ss["rep"]
    inc = [incumbent(o, y, st, sp, stg, w) for o, y, st, sp, stg in zip(ss["office"], ss["year"], ss["st"], ss["special"], ss["stage"])]
    ss["incumbent_party"] = [a for a, _ in inc]
    ss["incumbent_src"] = [b for _, b in inc]
    ss["st"] = ss["st"].str.lower()
    state = ss[["st", "year", "office", "contest", "dem", "rep", "s2", "v2", "total_all", "n_counties", "n_units", "race_type",
                "incumbent_party", "incumbent_src", "val_status", "note"]].sort_values(["office", "contest", "year", "st"]).reset_index(drop=True)
    # validation table
    v = best.copy()
    v["office"] = v["cls"].map(OFFICE)
    v["contest"] = np.where(v["cls"] == "sen", "sen" + np.where(v["special"] == 1, "_special", "") + np.where(v["stage"] == "RUNOFF", "_runoff", ""),
                            np.where(v["cls"] == "gov", "gov" + np.where(v["special"] == 1, "_special", ""), v["cls"].map(CONTEST)))
    v["st"] = v["st"].str.lower()
    v["dev_pts"] = v["dev"]
    v["flag_gt_0_3"] = v["dev"] > 0.3
    valid = v[["year", "st", "office", "contest", "rule", "straight", "dem", "rep", "rdem", "rrep", "s2", "ref_s2", "dev_pts", "dev_dem_pct",
               "dev_rep_pct", "cov", "status", "flag_gt_0_3", "reason", "fix", "note"]].rename(
        columns={"straight": "party_line_added", "rdem": "ref_dem", "rrep": "ref_rep", "cov": "coverage_v2"})
    valid = valid.sort_values(["office", "contest", "year", "st"]).reset_index(drop=True)
    return dict(county=county, state=state, validation=valid)


GOV_EXPECTED = {2016: "de in mo mt nh nc nd or ut vt wa wv", 2020: "de in mo mt nh nc nd ut vt wa wv",
                2024: "de in mo mt nh nc nd ut vt wa wv",
                2018: "al ak az ar ca co ct fl ga hi id il ia ks me md ma mi mn ne nv nh nm ny oh ok or pa ri sc sd tn tx vt wi wy",
                2022: "al ak az ar ca co ct fl ga hi id il ia ks me md ma mi mn ne nv nh nm ny oh ok or pa ri sc sd tn tx vt wi wy"}


def gaps(county: pd.DataFrame, state: pd.DataFrame, valid: pd.DataFrame) -> pd.DataFrame:
    """Missing contests (expected race list vs table) and missing counties (counties of the 2020 county file that have no row)."""
    rows = []
    have = set(zip(state["office"], state["year"], state["st"], state["contest"]))
    sen = refs()["sen"]
    sen = sen[(sen["year"].between(2016, 2024))]
    for r in sen.itertuples(index=False):
        c = "sen" + ("_special" if r.special else "") + ("_runoff" if r.stage == "RUNOFF" else "")
        if ("sen", r.year, r.st.lower(), c) not in have:
            why = "Louisiana open primary on the November ballot (no two-party final in the county data)" if r.st == "LA" else \
                  "not in MEDSL precinct files / no county data in the sources used"
            rows.append(("contest", "sen", c, r.year, r.st.lower(), "", why))
    for y, sts in GOV_EXPECTED.items():
        for st in sts.split():
            if ("gov", y, st, "gov") not in have and ("gov", y, st, "gov_special") not in have:
                rows.append(("contest", "gov", "gov", y, st, "", "MEDSL file has no governor rows (Tennessee 2022)" if (y, st) == (2022, "tn") else "missing"))
    cp = _countypres()
    cp = cp[(cp["year"] == 2020) & cp["county_fips"].notna()]
    cp = cp[cp["county_fips"].str.len() <= 5]                                       # Kansas City MO (2938000) is not a county
    allc = cp.assign(f=cp["county_fips"].str.zfill(5).replace(FIPS_RENAMED)).groupby("state_po")["f"].apply(lambda s: sorted(set(s)))
    allc.index = allc.index.str.lower()
    allc = allc[~allc.index.isin(["ak", "ct"])]
    for (off, con, y, st), g in county.groupby(["office", "contest", "year", "st"]):
        if st not in allc.index:
            continue
        miss = sorted(set(allc[st]) - set(g["fips"]))
        if miss and len(miss) < len(allc[st]):
            rows.append(("counties", off, con, y, st, " ".join(miss), f"{len(miss)} of {len(allc[st])} counties without a row"))
    return pd.DataFrame(rows, columns=["kind", "office", "contest", "year", "st", "missing_fips", "why"]).sort_values(["kind", "office", "year", "st"])


def coverage_matrix(county: pd.DataFrame, state: pd.DataFrame) -> pd.DataFrame:
    g = state.groupby(["office", "year"]).agg(states=("st", "nunique"), contests=("contest", "size"),
                                              pass_=("val_status", lambda s: int((s == "pass").sum())),
                                              fail=("val_status", lambda s: int((s == "fail").sum())),
                                              no_ref=("val_status", lambda s: int((s == "no_ref").sum())),
                                              one_party=("val_status", lambda s: int((s == "no_two_party").sum())))
    cty = county.groupby(["office", "year"]).agg(county_rows=("fips", "size"), counties=("fips", "nunique"))
    return g.join(cty).rename(columns={"pass_": "pass"}).reset_index()


def build():
    U.mkdir(parents=True, exist_ok=True)
    res = assemble()
    out = finalize(res)
    county, state, valid = out["county"], out["state"], out["validation"]
    county["note"] = county["note"].fillna("")
    county.to_parquet(U / "results_county.parquet", index=False)
    county.to_csv(U / "results_county.csv.gz", index=False, compression="gzip")
    state.to_csv(U / "results_state.csv", index=False)
    valid.to_csv(U / "results_validation.csv", index=False)
    gp = gaps(county, state, valid)
    gp.to_csv(U / "results_gaps.csv", index=False)
    cm = coverage_matrix(county, state)
    cm.to_csv(U / "results_coverage.csv", index=False)
    write_readme(county, state, valid, gp, cm)
    print(cm.to_string())
    return out


def _md(df: pd.DataFrame) -> str:
    df = df.astype(object).where(df.notna(), "")
    rows = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    rows += ["| " + " | ".join(str(x) for x in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def write_readme(county, state, valid, gp, cm) -> None:
    f = valid[valid["status"] == "fail"]
    md = ["# Unified results panel: county x year x office (UniResults)", "",
          "Built by `analysis/us_unified_results.py` (`fetch`, `extract`, `build`). Files in this directory:", "",
          "| file | content |", "|---|---|",
          f"| `results_county.parquet` / `.csv.gz` | {len(county):,} rows: fips, st, year, office, contest, dem, rep, total_all, s2, v2, note, val_status, unc_votes |",
          f"| `results_state.csv` | {len(state):,} rows: statewide aggregates + race type + incumbent party |",
          f"| `results_validation.csv` | {len(valid):,} rows: one per state-year-contest, deviation of the statewide two-party share from the official total |",
          "| `results_coverage.csv`, `results_gaps.csv` | coverage matrix and the gap list below |", "",
          "## Conventions", "",
          "- `fips` 5-digit string, `st` lowercase postal code, `year` election year. `dem` / `rep`: votes of the Democratic / Republican party's candidates",
          "  (a candidate counts for the major party under which at least half of his labelled votes were cast, so fusion lines such as NY Working Families",
          "  count for the candidate's party; party label from `party_simplified`, corrected by `party_detailed`). `total_all`: all candidates incl. write-ins,",
          "  without under/over votes. `s2 = dem/(dem+rep)`, `v2 = dem+rep`; `s2` is empty where one party had no candidate (`val_status = no_two_party`:",
          "  e.g. CA 2018 Senate D vs D, AR 2020 Senate; `note` = `no_dem_labelled` / `no_rep_labelled` / `no_contested_district`) or `v2 = 0`. `no_dem_labelled`",
          "  also covers races where the Democratic-aligned candidate carries no party label in the source (Vermont 2020 governor, Sanders VT 2018/2024, McMullin UT 2022).",
          "- `office` in pres, sen, gov, house_total. `contest`: pres, sen, sen_special, sen_runoff, sen_special_runoff, gov, gov_special, house_total. Specials are kept separate",
          "  from the regular race. The November general is kept apart from runoffs: Georgia 2020 November (regular `sen`, special `sen_special` = open",
          "  November special election, all candidates pooled by party), the 5 Jan 2021 runoffs (`sen_runoff` = Perdue/Ossoff, `sen_special_runoff` = Loeffler/Warnock,",
          "  election year 2020, county data from the Georgia Secretary of State Enhanced Voting results) and 6 Dec 2022 (`sen_runoff`, MEDSL stage GEN RUNOFF).",
          "- `house_total`: D and R votes of all contested House districts summed to the county. A district counts when both parties drew at least 2 % of its",
          "  two-party vote (drops write-in lines); votes of the other districts are in `unc_votes` (all candidates, county level) and not in dem/rep/total_all.",
          "  Special House elections are excluded. Louisiana 2018/2022/2024 (and 2016) House: November open primary, candidates pooled by party (`note = la_jungle_pooled`).",
          "- Nebraska 2024 regular Senate: Osborn (independent, no Democratic nominee) is on the dem side (as `data_public/us2026_priors.csv` `ne_sen_2024`).",
          "  Other independents are NOT moved (King ME, Sanders VT, McMullin UT 2022, Gross AK 2020): `s2` for those races is the D/R share only.",
          "- Counties: MEDSL county identifiers; merged/renamed counties as `analysis/us_fundamentals.py` (Bedford city, Shannon -> Oglala Lakota, Wade Hampton).",
          "  Connecticut: towns summed to the 9 planning regions (09110-09190, the county equivalents since 2022) for 2016-2024; CT 2008/2012 president exists only",
          "  for the 8 former counties and appears in `results_state.csv` only. Alaska: reported by state house district, so it is in `results_state.csv`",
          "  (n_counties = 0, `n_units` = districts) and not in `results_county`. Maine, Massachusetts, New Hampshire, Vermont, Rhode Island: towns summed to the counties given",
          "  in the files. Kansas City MO (reported outside the counties by MEDSL: president 2008-2024, and `36000` in the 2018+ files) and Maine UOCAVA / RI federal precinct /",
          "  CT statewide write-ins are in `results_state.csv` (`n_units` counts them) but not in a county row, so Jackson/Clay/Platte MO and some RI/ME county rows are slightly short.",
          "- Vote modes: MEDSL states report TOTAL rows and/or mode rows; for every state-year-office the rule (`overlap` = default, `total_first`, `all`; as",
          "  `brunder.us._medsl_county`) with the statewide result closest to the official one is used; `note` has `mode_rule=...` when it is not the default.",
          "  2016 Alabama / South Carolina / Indiana / Kentucky list party-line ballots as separate rows: they are added to the party's candidate",
          "  where that brings the state total closer to the official one (`party_line_votes_added`).",
          "- Party labels missing in the MEDSL rows (WI 2018, GA/KY/ND 2016 ...) are filled from the same candidate (first initial + surname) in the official",
          "  statewide Senate / House files (`party_from_ref_name`); governor contests failing validation are relabelled only when one (D, R) candidate pair among the six",
          "  largest reproduces the reference two-party share within 0.15 points and no other pair is within 1 point (`results_validation.csv` column `fix`,",
          "  e.g. Vermont 2022 Siegel, Wyoming 2022 Livingston, Oregon 2018 Starnes).", "",
          "## Sources and vintage", "",
          "- President 2008-2024: MEDSL County Presidential Returns 2000-2024 (CC0), CT 2016-2024 from MEDSL precinct files.",
          "- Senate / governor / House 2018, 2022, 2024: MEDSL precinct returns (`raw/us/medsl18|22|24`, CC0 / MIT); California 2022 Senate (missing in MEDSL): CA Secretary of State",
          "  Statement of Vote. 2020 Senate: `raw/us/medsl20/2020-SENATE-precinct-general.csv`; 2020 governor and House: the state files in `raw/us/medsl20` (DE, MO, MT, NC, ND, NH, UT, VT, WA, WV, IN);",
          "  2020 House elsewhere: MEDSL `2020-elections-official` GitHub repository at commit ad22f37 (2022-03-14, the last that still held the precinct files; MIT).",
          "- 2016 Senate, governor, House: MEDSL `official-precinct-returns` 2016 (`2016-precinct-senate`, `-state`, `-house`; MIT). Public GitHub files only, no Dataverse guestbook files were",
          "  downloaded by this builder (the 2020 state files on disk were downloaded earlier by another module).",
          "- Validation references: `1976-2024-senate-state.tab`, `1976-2024-president-state.csv`, `1976-2018-house.csv` / `1976-2024-house.tab` (district totals), governor: `act`",
          "  of `data_public/us_state_drift_panel.csv` (two-party D %, only the polled races). Vintage: results of the election itself (certified or final),",
          "  stamped with the election year; nothing here is an input known before Election Day of that year.", "",
          "## Incumbent party (`results_state.csv`)", "",
          "MEDSL state files carry no incumbent flag. `incumbent_party` is the party of the winner of the seat's previous regular election",
          "(Senate: 6 years earlier, from the MEDSL 1976-2024 Senate file incl. runoffs; governor: 4 years earlier, 2 for NH/VT, 2016+ from this table, earlier from the drift panel `act` > 50)",
          "with a short documented override list for seats that changed party or holder in between (`INC_OVERRIDE`, `SPECIAL_HOLDER` in the code: AL 2020 Jones, AZ 2022 Kelly,",
          "GA 2022 Warnock, AZ 2024 Sinema (I), WV 2024 Manchin (I), WV 2020 Justice (R), Senate specials). `incumbent_src` says which. President: party of the president on election day.",
          "House (`house_total`) and years/states without a prior result: blank.", "",
          "## Coverage matrix", "",
          "`states` / `contests`: rows of `results_state.csv`; `counties`: distinct county FIPS with a row; validation columns count contests in `results_state` by status",
          "(`pass` = |two-party share - official| <= 0.3 points, `fail`, `no_ref` = no official reference found, `one_party` = no D or no R candidate).", "",
          _md(cm), "",
          "## Validation failures (> 0.3 points)", "",
          f"{len(f)} contests. `coverage_v2` = (our dem+rep)/(official dem+rep).", "",
          _md(f[["year", "st", "office", "dem", "rep", "s2", "ref_s2", "dev_pts", "coverage_v2", "reason"]].round(4)), "",
          "Reference caveats: the 1976-2018 House file understates Maryland and doubles/omits some states' 2018 votes, so House failures with `coverage_v2` far above 1 are mostly reference problems;",
          "the governor reference `act` is 538's number, which differs from the certified count in a few races (CT 2018/2022, NY 2022).", "",
          "## Gaps", "",
          _md(gp.assign(missing_fips=gp["missing_fips"].map(lambda x: x if len(x) < 70 else x[:66] + " ... (full list: results_gaps.csv)"))), "",
          "Not available at all:", "",
          "- Louisiana Senate 2016, 2020, 2022: open (jungle) November ballot. 2016's two-party final was the 10 Dec runoff, which is not in the MEDSL 2016 Senate file (stage `gen` only);",
          "  2020 and 2022 were decided in November with several Democrats / Republicans on the ballot: excluded from both tables (`results_validation.csv` status `excluded`).",
          "- Governor validation covers only races of `data_public/us_state_drift_panel.csv` (`act`); the other governor contests are `val_status = no_ref` (see `results_validation.csv`).",
          "- Texas House 2018: uncontested districts (D or R only) are excluded from `house_total` by design, so `house_total` differs from `s_house` of `us_county_panel.csv` there.",
          "- Indiana 2020 (governor, House): the MEDSL 2020 state file on disk covers only 53 counties; Indiana 2022 Senate/House: the MEDSL 2022 file covers 38-39 counties:",
          "  the rows exist for those counties but the statewide totals fail validation.",
          "- House 2020 in Florida (districts 1-8, 25), Connecticut (4, 5), parts of other states: missing in MEDSL's 2020 GitHub House file; Maine 2018 ME-2 (ranked choice) is missing in MEDSL.",
          "- Tennessee 2022 governor: MEDSL's 2022 file has no governor rows (certified statewide totals exist elsewhere, counties not). Alaska: state house districts only.",
          "- Mississippi 2018 special Senate: the county data is the 6 Nov first round (all candidates pooled by party), the official reference is the 27 Nov runoff.",
          "- 2016 governor/Senate come from MEDSL's 2016 file; Georgia 2016 has no party labels in the file (filled by name).", ""]
    (U / "README_UniResults.md").write_text("\n".join(md))


if __name__ == "__main__":
    for cmd in sys.argv[1:] or ["fetch", "extract", "build"]:
        globals()[cmd]()
