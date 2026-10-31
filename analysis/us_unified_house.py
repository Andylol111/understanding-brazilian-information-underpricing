"""Unified pre-count model, House slice: district x even-year panel 2012-2024, district polls, 2026-line priors.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_house.py [fetch] [build [step ...]]

fetch  checks that every local input exists (nothing is downloaded: all sources are already cached under
       $BRUNDER_DATA/raw/us) and prints the inventory.
build  steps (default: all, in this order; `pres16` caches its result under U/_cache_house/; `panel`, `priors`
       and `readme` must run in this order because each reads the previous output):
         pres16   precinct join of MEDSL 2016 president and House returns -> county x 2016-district shares
         panel    house_panel.parquet / .csv (results, lines, incumbency, swing, president, polls) and
                  house_polls.csv (every poll used, with its weight)
         priors   house_districts_2026.csv (2026 lines: pieces, pres24, house24 core, redrawn, 2026 poll average)
         readme   README_UniHouse.md (coverage matrix, same-lines backtest transitions, vintage rule, gaps)
Outputs go to U = $BRUNDER_DATA/processed/us2026/unified/.

Sources (all public domain / open): MIT Election Data and Science Lab "U.S. House 1976-2024" (CC0 1.0),
"U.S. President 1976-2020 / 2000-2024 county returns" (CC0 1.0), "2016 precinct-level returns, president and U.S.
House" (CC0 1.0); site/data/us2026/house/districts_2024.json and districts.json (this repo, analysis/us_house.py;
Census block equivalency files, MEDSL 2024 precinct returns, FEC: public domain / CC0); FiveThirtyEight
"house_polls_historical" 2018-2024 (CC BY 4.0); VoteHub polling API (CC BY 4.0).

Vintage rule (no look-ahead): the row of election year t contains (i) the result of t itself (target variables
dem, rep, total, d2, contested, winner), (ii) only information published before Election Day of t in the feature
columns: incumbency (prior-cycle winners; the previous presidential vote `pres_prev_*`, the previous House result
`*_prev`), `lines_changed`, and the poll average (field end <= Election Day - 1). Columns `pres_cur_*` are the
SAME-year presidential result (2012/2016/2020/2024 only) and are NOT a pre-count feature (diagnostics, coattails).
"""
from __future__ import annotations

import io
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

RAW = us.US_RAW
U = RAW.parent.parent / "processed" / "us2026" / "unified"
CACHE = U / "_cache_house"
ROOT = us.ROOT_DIR

HIST_HOUSE = RAW / "medsl_hist" / "1976-2024-house.tab"          # comma-separated despite the extension
HIST_PRES_STATE = RAW / "medsl_hist" / "1976-2024-president-state.csv"
COUNTYPRES = RAW / "medsl" / "countypres_2000-2024.csv"
MEDSL16_HOUSE = RAW / "medsl16" / "house.zip"
MEDSL16_PRES = RAW / "medsl16" / "president.zip"
POLLS538 = RAW / "polls" / "538_house_polls_historical.csv"
VOTEHUB_US_REP = RAW / "polls" / "votehub_us-representative_20261029.json"
FEC24 = RAW / "house" / "2024congressgecands.xlsx"
PIECES = RAW / "house" / "pieces.csv"
D24 = ROOT / "site" / "data" / "us2026" / "house" / "districts_2024.json"
D26 = ROOT / "site" / "data" / "us2026" / "house" / "districts.json"
MARKETS = ROOT / "site" / "data" / "us2026" / "house" / "markets.json"
PRIORS_CSV = ROOT / "data_public" / "us2026_house_priors.csv"

YEARS = list(range(2012, 2025, 2))
ELECTION_DAY = {2012: "2012-11-06", 2014: "2014-11-04", 2016: "2016-11-08", 2018: "2018-11-06",
                2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05", 2026: "2026-11-03"}
PRES_YEARS = (2012, 2016, 2020, 2024)

# ---- district map changes (year -> states whose lines differ from the previous cycle) -------------------------
# SPEC: the list given for this task (docs/us_midterms.md): 2012 and 2022 national, 2018 PA, 2024 AL GA LA NC NY.
# ADDED: court-ordered / enacted maps that also changed the lines for the cycle (checked with the incumbent-overlap
# diagnostic in the README): 2016 FL (Romo v. Detzner), NC (Harris v. McCrory), VA (Page v. Bethune-Hill);
# 2020 NC (2019 remedial map, Common Cause v. Lewis). Single-seat states never change lines.
LINES_SPEC = {2018: {"PA"}, 2024: {"AL", "GA", "LA", "NC", "NY"}}
LINES_ADDED = {2016: {"FL", "NC", "VA"}, 2020: {"NC"}}
NATIONAL_REDRAW = (2012, 2022)
JUNGLE_LA = (2012, 2014, 2016, 2018, 2020, 2022)     # Louisiana open (jungle) November primary

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
ABBR_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "FL": "12",
             "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22",
             "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
             "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40",
             "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50",
             "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56"}
FIPS_ST = {v: k for k, v in ABBR_FIPS.items()}


def did(st: str, cd) -> str:
    """District id "TX-07"; at-large seats "AK-AL" (same as analysis/us_house.py)."""
    cd = int(cd)
    return f"{st}-AL" if cd == 0 else f"{st}-{cd:02d}"


def _save(df: pd.DataFrame, name: str, parquet: bool = False) -> None:
    U.mkdir(parents=True, exist_ok=True)
    if parquet:
        df.to_parquet(U / f"{name}.parquet", index=False)
    df.to_csv(U / f"{name}.csv", index=False)
    print(f"{name}: {len(df)} rows x {df.shape[1]} cols -> {U}")


# ------------------------------------------------------------------ 1. district results (MEDSL 1976-2024 House)

_NONPERSON = re.compile(r"^(BLANK.*|VOID.*|OVER ?VOTES?|UNDER ?VOTES?|TOTAL.*|NONE.*|EXHAUSTED.*)$")   # not votes for a candidate
_OTHERS = re.compile(r"^(WRITE-?INS?|SCATTER(ING)?|OTHER|ALL OTHERS|OTHER WRITE-?INS?)$")   # votes, but nobody wins
_DEM = {"DEMOCRAT", "DEMOCRATIC", "DEMOCRATIC-FARMER-LABOR", "DEMOCRATIC-NONPARTISAN LEAGUE", "DEMOCRATIC-NPL"}
_REP = {"REPUBLICAN", "REPUBLICAN, LIBERTARIAN"}
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def _tokens(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z]+", s) if w not in _SUFFIX]


def _namekey(name: str) -> tuple[str, str]:
    t = _tokens(name)
    return (t[-1], t[0][0]) if t else ("", "")


def load_candidates() -> pd.DataFrame:
    """One row per (year, st, district, candidate) 2010-2024: votes summed over ballot lines (fusion candidates in NY
    etc.), party class D/R/O from the lines (write-ins are O), kind person|other (scattering, write-in totals)."""
    h = pd.read_csv(HIST_HOUSE, low_memory=False)
    h = h[(h["year"] >= 2010) & (h["stage"] == "GEN") & (h["state_po"] != "DC") & (h["office"] == "US HOUSE")].copy()
    h["cand"] = h["candidate"].fillna("").astype(str).str.strip().str.upper()
    h = h[~h["cand"].str.match(_NONPERSON)]
    h["party"] = h["party"].fillna("").astype(str).str.upper().str.strip()
    wi = h["writein"].astype(str).str.upper().eq("TRUE")
    h["isD"] = h["party"].isin(_DEM) & ~wi
    h["isR"] = h["party"].isin(_REP) & ~wi
    h["v"] = pd.to_numeric(h["candidatevotes"], errors="coerce").fillna(0.0)
    # unopposed races in OK / FL ... carry the sentinel candidatevotes = 1, totalvotes = -1: no count was published
    h.loc[pd.to_numeric(h["totalvotes"], errors="coerce") < 0, "v"] = 0.0
    h["vD"], h["vR"] = h["v"].where(h["isD"], 0.0), h["v"].where(h["isR"], 0.0)
    h["district"] = pd.to_numeric(h["district"], errors="coerce").astype(int)
    g = h.groupby(["year", "state_po", "district", "cand"], as_index=False).agg(
        v=("v", "sum"), vD=("vD", "sum"), vR=("vR", "sum"), anyD=("isD", "any"), anyR=("isR", "any"),
        writein=("writein", lambda s: s.astype(str).str.upper().eq("TRUE").all()), medsl_total=("totalvotes", "max"))
    cls = np.where(g["anyD"] & (~g["anyR"] | (g["vD"] >= g["vR"])), "D",
                   np.where(g["anyR"], "R", "O"))
    g["cls"] = pd.Series(cls, index=g.index)
    g["kind"] = np.where(g["cand"].str.match(_OTHERS), "other", "person")
    g.loc[g["kind"] == "other", "cls"] = "O"
    return g.rename(columns={"state_po": "st", "cand": "name"})


def races_from_candidates(c: pd.DataFrame) -> pd.DataFrame:
    """Race level result: dem / rep = votes of the leading Democratic / Republican candidate (all his ballot lines),
    total = all candidate votes (excl. blank / void / over / under), d2 = dem / (dem + rep)."""
    rows = []
    for (y, st, cd), g in c.groupby(["year", "st", "district"]):
        p = g[g["kind"] == "person"].sort_values(["v", "name"], ascending=[False, True])
        d, r = p[p["cls"] == "D"], p[p["cls"] == "R"]
        dem = float(d["v"].iloc[0]) if len(d) else 0.0
        rep = float(r["v"].iloc[0]) if len(r) else 0.0
        total = float(g["v"].sum())
        win = p.iloc[0] if len(p) else None
        rows.append({
            "year": y, "st": st, "district": cd, "dem": dem, "rep": rep, "total": total,
            "total_medsl": float(g["medsl_total"].max()),
            "n_dem_cands": int((d["v"] > 0).sum()), "n_rep_cands": int((r["v"] > 0).sum()),
            "n_cands": int((p["v"] > 0).sum()),
            "on_ballot_D": bool(len(d)), "on_ballot_R": bool(len(r)),
            "dem_name": d["name"].iloc[0] if len(d) else None, "rep_name": r["name"].iloc[0] if len(r) else None,
            "winner": win["name"] if win is not None else None,
            "winner_party": win["cls"] if win is not None else None,
            "winner_votes": float(win["v"]) if win is not None else np.nan,
            "other_votes": float(g.loc[g["cls"] == "O", "v"].sum()),
        })
    r = pd.DataFrame(rows)
    r["d2"] = np.where(r["dem"] + r["rep"] > 0, r["dem"] / (r["dem"] + r["rep"]).replace(0, np.nan), np.nan)
    r["margin"] = np.where(r["total"] > 0, (r["dem"] - r["rep"]) / r["total"].replace(0, np.nan), np.nan)
    # contested = both a Democrat and a Republican with votes (one of them absent -> False)
    r["contested"] = (r["dem"] > 0) & (r["rep"] > 0)
    r["uncontested_side"] = np.select([r["contested"], r["on_ballot_D"] & ~r["on_ballot_R"],
                                       r["on_ballot_R"] & ~r["on_ballot_D"]], ["", "D", "R"], "none")
    r["no_votes_reported"] = r["total"] <= 0         # unopposed races in states that publish no vote count
    r["n_seats"] = r.groupby(["year", "st"])["district"].transform("nunique")
    r["jungle"] = (r["st"] == "LA") & r["year"].isin(JUNGLE_LA)
    r["did"] = [did(s, d) for s, d in zip(r["st"], r["district"])]
    return r


# ------------------------------------------------------------------ 2. lines changed, incumbency, swing

def lines_table(races: pd.DataFrame) -> pd.DataFrame:
    """(year, st) -> lines_changed vs the previous cycle (see LINES_* above); single-seat states never change."""
    seats = races.groupby(["year", "st"])["n_seats"].first()
    rows = []
    for (y, st), n in seats.items():
        prev = seats.get((y - 2, st), np.nan)
        spec = (y in NATIONAL_REDRAW) or st in LINES_SPEC.get(y, set())
        added = st in LINES_ADDED.get(y, set())
        single = (n == 1) and (prev == 1)
        note = ""
        if single:
            spec = added = False
            note = "single-seat state: statewide district"
        elif y in NATIONAL_REDRAW:
            note = "national redistricting"
        elif spec:
            note = "map changed (task list)"
        elif added:
            note = "map changed (added, not in task list)"
        rows.append({"year": y, "st": st, "lines_changed_spec": bool(spec), "lines_changed": bool(spec or added),
                     "lines_note": note, "n_seats_prev": prev})
    return pd.DataFrame(rows)


def add_prev_and_incumbency(r: pd.DataFrame, c: pd.DataFrame, lt: pd.DataFrame) -> pd.DataFrame:
    r = r.merge(lt, on=["year", "st"], how="left")
    prev = r[["year", "st", "district", "dem", "rep", "d2", "contested", "winner", "winner_party", "total"]].copy()
    prev["year"] += 2
    prev = prev.rename(columns={"dem": "dem_prev", "rep": "rep_prev", "d2": "d2_prev", "contested": "contested_prev",
                                "winner": "winner_prev", "winner_party": "winner_party_prev", "total": "total_prev"})
    r = r.merge(prev, on=["year", "st", "district"], how="left")
    same = ~r["lines_changed"].astype(bool)
    for col in ("dem_prev", "rep_prev", "d2_prev", "contested_prev", "winner_prev", "winner_party_prev", "total_prev"):
        r.loc[~same, col] = np.nan if col not in ("contested_prev",) else pd.NA
    r["contested_prev"] = r["contested_prev"].astype("boolean")
    r["swing_d2"] = np.where(same & r["contested"] & r["contested_prev"].fillna(False).astype(bool),
                             r["d2"] - r["d2_prev"], np.nan)
    # winners of every cycle by state for name matching
    wins = r[["year", "st", "district", "winner", "winner_party"]].dropna(subset=["winner"])
    wk = {}
    for (y, st), g in wins.groupby(["year", "st"]):
        wk[(y, st)] = [(row.district, _namekey(row.winner), row.winner, row.winner_party) for row in g.itertuples()]
    persons = c[c["kind"] == "person"]
    cand_by = {k: g for k, g in persons.groupby(["year", "st", "district"])}
    out = []
    for row in r.itertuples():
        prevw = wk.get((row.year - 2, row.st), [])
        cur = cand_by.get((row.year, row.st, row.district))
        keys = [] if cur is None else [(_namekey(n), n, p, v) for n, p, v in zip(cur["name"], cur["cls"], cur["v"])]
        rec = {"hold_party": None, "hold_name": None, "inc_ran": None, "inc_name": None, "inc_party_now": None,
               "inc_basis": "", "n_inc_ran": 0, "inc_won": None}
        if not prevw:
            rec["inc_basis"] = "no previous cycle"
            out.append(rec)
            continue
        lastcount = {}
        for _, (l, f), _, _ in prevw:
            lastcount[l] = lastcount.get(l, 0) + 1

        def is_prev_winner(k, pool):
            for (_, (l, f), nm, party) in pool:
                if k[0] == l and (k[1] == f or lastcount.get(l, 0) == 1):
                    return nm, party
            return None
        if not row.lines_changed and not pd.isna(row.winner_prev):
            hw = [w for w in prevw if w[0] == row.district]
            rec["hold_party"], rec["hold_name"] = (hw[0][3], hw[0][2]) if hw else (None, None)
            rec["inc_basis"] = "prev_winner_same_district"
            m = [(n, p, v) for k, n, p, v in keys if hw and k[0] == hw[0][1][0] and (k[1] == hw[0][1][1])] if hw else []
            if hw and not m:                      # nickname / first-name variants: same surname, unique in the district
                m = [(n, p, v) for k, n, p, v in keys if k[0] == hw[0][1][0]]
                m = m if len(m) == 1 else []
        else:
            rec["inc_basis"] = "name_match_state"
            m = []
            for k, n, p, v in keys:
                hit = is_prev_winner(k, prevw)
                if hit:
                    m.append((n, p, v))
                    rec["hold_party"], rec["hold_name"] = hit[1], hit[0]
        rec["n_inc_ran"] = len(m)
        rec["inc_ran"] = bool(m)
        if m:
            top = max(m, key=lambda x: x[2])
            rec["inc_name"], rec["inc_party_now"] = top[0], top[1]
            rec["inc_won"] = bool(row.winner == top[0])
        out.append(rec)
    inc = pd.DataFrame(out, index=r.index)
    return pd.concat([r, inc], axis=1)


def add_fec24(r: pd.DataFrame) -> pd.DataFrame:
    """FEC 2024 general-election ballot list: the (I) flag of the candidate list, kept as a cross-check column only
    (it flags e.g. both candidates in DE-AL 2024, so it is not a reliable incumbency source)."""
    sys.path.insert(0, str(ROOT / "analysis"))
    import us_house
    f = us_house._fec24_cands()
    fl = f[f["inc"]].groupby(["st", "cd"]).size().rename("fec24_inc_flags").reset_index()
    fl["year"] = 2024
    r = r.merge(fl.rename(columns={"cd": "district"}), on=["year", "st", "district"], how="left")
    r["fec24_inc_flags"] = r["fec24_inc_flags"].where(r["year"] == 2024)
    return r


# ------------------------------------------------------------------ 3. president by district

def _not_cand(s: pd.Series) -> pd.Series:
    u = s.fillna("").astype(str).str.strip().str.upper()
    return u.str.match(us._NOT_A_CANDIDATE) | u.str.contains(r"\bTOTAL\b|BALLOTS CAST|UNDERVOTE|OVERVOTE|BLANK")


def _county_pres(years=(2012, 2016, 2020)) -> dict[int, pd.DataFrame]:
    """MEDSL county president file: year -> fips -> d, r, t (candidate votes; TOTAL-mode rows where a county has them)."""
    d = pd.read_csv(COUNTYPRES, dtype={"county_fips": str})
    d = d[d["year"].isin(years) & d["county_fips"].notna() & ~_not_cand(d["candidate"])].copy()
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


def _pres_state(years=(2012, 2016, 2020)) -> dict[int, pd.DataFrame]:
    """State president totals (MEDSL 1976-2024): year -> st -> d, r, t."""
    s = pd.read_csv(HIST_PRES_STATE)
    s = s[s["year"].isin(years) & (s["office"] == "US PRESIDENT")]
    out = {}
    for y, g in s.groupby("year"):
        p = g.pivot_table(index="state_po", columns="party_simplified", values="candidatevotes", aggfunc="sum").fillna(0)
        out[int(y)] = pd.DataFrame({"d": p["DEMOCRAT"], "r": p["REPUBLICAN"],
                                    "t": g.groupby("state_po")["totalvotes"].max()})
    return out


def pres16_shares() -> pd.DataFrame:
    """2016 president votes by (county, 2016 House district): every precinct's presidential votes are given to the
    House districts on the precinct's ballot in proportion to its House votes (precinct key: state, county FIPS,
    jurisdiction, precinct; precincts without a county FIPS or without a House match are left out of the shares).
    Mode rule per precinct and office: TOTAL-mode rows where they carry votes, else the sum of the other modes.
    Returns st, fips, cd, d, r, t, plus st-level join rates in the attrs of the cached file next to it."""
    out = CACHE / "pres16_shares.csv"
    if out.exists():
        return pd.read_csv(out, dtype={"fips": str})
    CACHE.mkdir(parents=True, exist_ok=True)
    cols = ["state_postal", "county_fips", "jurisdiction", "precinct", "office", "district", "stage", "special",
            "party", "mode", "votes", "writein", "candidate_normalized"]
    kw = dict(usecols=cols, dtype=str, encoding="latin-1", na_filter=False, chunksize=600_000)
    key = ["state_postal", "county_fips", "jurisdiction", "precinct"]

    def agg(zpath: Path, member: str, office: str, house: bool) -> pd.DataFrame:
        parts = []
        with zipfile.ZipFile(zpath) as z, z.open(member) as fh:
            for ch in pd.read_csv(fh, **kw):
                ch = ch[(ch["office"] == office) & (ch["stage"] == "gen") & (ch["special"] != "TRUE")]
                v = pd.to_numeric(ch["votes"], errors="coerce").fillna(0.0)
                ch = ch.assign(v=v, istot=ch["mode"].str.lower().eq("total"),
                               pd_=ch["candidate_normalized"].str.lower().eq("clinton"),
                               pr=ch["candidate_normalized"].str.lower().eq("trump"))
                ch["vd"], ch["vr"] = ch["v"].where(ch["pd_"], 0.0), ch["v"].where(ch["pr"], 0.0)
                k = key + ["istot"] + (["district"] if house else [])
                parts.append(ch.groupby(k, as_index=False)[["v", "vd", "vr"]].sum())
        a = pd.concat(parts).groupby(key + ["istot"] + (["district"] if house else []), as_index=False).sum()
        # TOTAL rows where the precinct has any, else the other modes
        kk = key + (["district"] if False else [])
        tot_has = a[a["istot"]].groupby(key)["v"].sum()
        tot_has = tot_has[tot_has > 0].index
        a = a.set_index(key)
        use_tot = a.index.isin(tot_has)
        a = a[(use_tot & a["istot"].to_numpy()) | (~use_tot & ~a["istot"].to_numpy())].reset_index()
        return a.drop(columns="istot")
    ph = agg(MEDSL16_HOUSE, "2016-precinct-house.csv", "US House", True)
    pp = agg(MEDSL16_PRES, "2016-precinct-president.csv", "US President", False)
    pp = pp.groupby(key, as_index=False)[["v", "vd", "vr"]].sum().rename(columns={"v": "t", "vd": "d", "vr": "r"})
    ph["cd"] = pd.to_numeric(ph["district"], errors="coerce")
    ph = ph[ph["cd"].notna()]
    hw = ph.groupby(key + ["cd"], as_index=False)["v"].sum()
    tot = hw.groupby(key)["v"].transform("sum")
    # precincts whose House race published no counts (unopposed): weight by presence, equally over listed districts
    hw["w"] = np.where(tot > 0, hw["v"] / tot.replace(0, np.nan), 1.0 / hw.groupby(key)["v"].transform("size"))
    hw = hw[hw["w"].notna()]
    m = pp.merge(hw[key + ["cd", "w"]], on=key, how="left")
    rate = m.groupby("state_postal").apply(lambda g: g.loc[g["w"].notna(), "t"].sum() / max(g["t"].sum(), 1), include_groups=False)
    rate.rename("pres_votes_matched_to_house_precinct").to_csv(CACHE / "pres16_join_rate.csv")
    m = m[m["w"].notna() & (m["county_fips"] != "")]
    for c in ("d", "r", "t"):
        m[c] = m[c] * m["w"]
    m["fips"] = m["county_fips"].str.replace(r"\.0$", "", regex=True).str.zfill(5).replace(us._FIPS_RENAMED)
    s = m.groupby(["state_postal", "fips", "cd"], as_index=False)[["d", "r", "t"]].sum() \
        .rename(columns={"state_postal": "st"})
    s["cd"] = s["cd"].astype(int)
    s.to_csv(out, index=False)
    return s


def _alloc(shares: pd.DataFrame, official: pd.DataFrame) -> pd.DataFrame:
    """Official county totals (d, r, t) split over the county's districts by `shares` (d, r, t each by its own
    column). Counties absent from `shares` are unallocated; returns st, cd, d, r, t, n_counties, and per state the
    unallocated share of votes in `attrs`."""
    sh = shares.set_index(["fips", "cd"])
    csum = sh.groupby(level=0).sum()
    rows = []
    for (f, cd), x in sh.iterrows():
        if f not in official.index:
            continue
        o = official.loc[f]
        rows.append((FIPS_ST.get(f[:2]), cd, f, *(o[k] * x[k] / csum.loc[f, k] if csum.loc[f, k] > 0 else 0.0
                                                   for k in ("d", "r", "t"))))
    a = pd.DataFrame(rows, columns=["st", "cd", "fips", "d", "r", "t"])
    un = official[~official.index.isin(csum.index)]
    un = un.assign(st=[FIPS_ST.get(f[:2]) for f in un.index]).groupby("st")["t"].sum()
    tot = official.assign(st=[FIPS_ST.get(f[:2]) for f in official.index]).groupby("st")["t"].sum()
    a.attrs["unallocated"] = (un / tot).fillna(0.0).to_dict()
    return a


def pres_lines16() -> pd.DataFrame:
    """President 2012 / 2016 / 2020 by 2016-line House district (valid for 2016, 2018, 2020 lines, minus PA 2018-20
    and NC 2020): st, cd, year, d, r, t, d2, method, share_unallocated (state level)."""
    shares = pres16_shares()
    cp = _county_pres()
    ps = _pres_state()
    seats = load_races_cached().query("year == 2016").groupby("st")["n_seats"].first()
    rows, unalloc = [], {}
    for y in (2012, 2016, 2020):
        a = _alloc(shares, cp[y])
        a = a[a["st"].map(seats).fillna(0) > 1]
        g = a.groupby(["st", "cd"], as_index=False)[["d", "r", "t"]].sum()
        g["year"], g["method"] = y, ("precinct_house_split" if y == 2016 else "county_split_by_2016")
        rows.append(g)
        un = a.attrs["unallocated"]
        unalloc[y] = un
        for st in seats[seats == 1].index:              # single-seat states: the state total
            if st in ps[y].index:
                rows.append(pd.DataFrame([{"st": st, "cd": 0, "d": ps[y].loc[st, "d"], "r": ps[y].loc[st, "r"],
                                           "t": ps[y].loc[st, "t"], "year": y, "method": "state"}]))
    p = pd.concat(rows, ignore_index=True)
    p["d2"] = p["d"] / (p["d"] + p["r"])
    have = p[p["year"] == 2016].groupby("st")["cd"].nunique()
    missing = {st for st, n in seats.items() if n > 1 and have.get(st, 0) < n}
    rate = pd.read_csv(CACHE / "pres16_join_rate.csv", index_col=0).iloc[:, 0]
    p["share_unmatched"] = [0.0 if m == "state" else
                            1 - (1 - float(unalloc[y].get(st, 0.0))) * float(rate.get(st, 1.0))
                            for y, st, m in zip(p["year"], p["st"], p["method"])]
    # partial: more than 5 % of the state's presidential votes could not be tied to a House precinct / county, or a
    # district of the state has no House precinct returns at all (unopposed race without a count): that district is
    # absent and the neighbours' county splits are biased
    p["partial"] = [bool(sm > 0.05 or st in missing) for st, sm in zip(p["st"], p["share_unmatched"])]
    return p


def pres_lines24() -> pd.DataFrame:
    """2020 and 2024 president on the 2024 (119th Congress) lines: districts_2024.json (analysis/us_house.py)."""
    d = json.loads(D24.read_text())["districts"]
    rows = []
    for k, v in d.items():
        st, cd = k[:2], 0 if k.endswith("AL") else int(k[3:])
        for y, key in ((2020, "pres20"), (2024, "pres24")):
            p = v.get(key)
            if p and p.get("d2") is not None:
                rows.append({"st": st, "cd": cd, "year": y, "d": p["dv"], "r": p["rv"], "t": p["tv"], "d2": p["d2"],
                             "method": p["method"], "share_unmatched": np.nan, "partial": False})
    return pd.DataFrame(rows)


def pres_lines_special() -> pd.DataFrame:
    """President 2016 and 2020 on lines that differ from the 2016 map: PA 2018-2020 (vintage 'PA18') and NC 2020
    ('NC20'). NC: MEDSL 2020 precinct returns (president and House of the same precinct) -> 2020 president by 2020
    district from precincts ('precinct_house_split'); 2016 president = county totals split like the 2020 votes
    ('county_split_by_2020'). PA: no precinct president file on the 2018 lines exists in the raw data and the 2016
    and 2018 precinct names do not join, so each county's 2016 and 2020 president votes are split over its 2018
    districts like the county's total 2018 U.S. House votes (all candidates): 'county_split_by_house18'."""
    cp = _county_pres(years=(2016, 2020))
    rows = []
    # ---- NC 2020 lines
    f = RAW / "medsl20" / "2020-nc-precinct-general.csv"
    parts = []
    use = ["precinct", "office", "party_simplified", "votes", "county_fips", "jurisdiction_fips", "district", "stage",
           "special"]
    for ch in pd.read_csv(f, usecols=use, dtype=str, na_filter=False, chunksize=500_000):
        ch = ch[ch["office"].isin(["US PRESIDENT", "US HOUSE"]) & (ch["stage"] == "GEN") & (ch["special"] != "TRUE")]
        ch = ch.assign(v=pd.to_numeric(ch["votes"], errors="coerce").fillna(0.0))
        parts.append(ch.groupby(["county_fips", "jurisdiction_fips", "precinct", "office", "district", "party_simplified"],
                                as_index=False)["v"].sum())
    n = pd.concat(parts).groupby(["county_fips", "jurisdiction_fips", "precinct", "office", "district",
                                  "party_simplified"], as_index=False)["v"].sum()
    key = ["county_fips", "jurisdiction_fips", "precinct"]
    pres = n[n["office"] == "US PRESIDENT"]
    pp = pres.assign(d=pres["v"].where(pres["party_simplified"] == "DEMOCRAT", 0.0),
                     r=pres["v"].where(pres["party_simplified"] == "REPUBLICAN", 0.0)) \
        .groupby(key, as_index=False)[["d", "r", "v"]].sum().rename(columns={"v": "t"})
    hw = n[n["office"] == "US HOUSE"].groupby(key + ["district"], as_index=False)["v"].sum()
    hw["w"] = hw["v"] / hw.groupby(key)["v"].transform("sum")
    m = pp.merge(hw[key + ["district", "w"]], on=key, how="inner")
    for c in ("d", "r", "t"):
        m[c] = m[c] * m["w"]
    m["fips"] = m["county_fips"].str.zfill(5)
    m["cd"] = m["district"].astype(int)
    s = m.groupby(["fips", "cd"], as_index=False)[["d", "r", "t"]].sum()
    a20 = _alloc(s, cp[2020])
    a16 = _alloc(s, cp[2016])
    for y, a, meth in ((2020, a20, "precinct_house_split"), (2016, a16, "county_split_by_2020")):
        g = a[a["st"] == "NC"].groupby(["st", "cd"], as_index=False)[["d", "r", "t"]].sum()
        g["year"], g["method"], g["vint"] = y, meth, "NC20"
        g["share_unmatched"] = float(a.attrs["unallocated"].get("NC", 0.0))
        rows.append(g)
    # ---- PA 2018 lines (House votes 2018 by county x district, MEDSL 2018 precinct file)
    with zipfile.ZipFile(RAW / "medsl18" / "2018-pa-precinct-general.zip") as z:
        h = pd.read_csv(z.open(z.namelist()[0]), usecols=["office", "party_simplified", "votes", "county_fips", "district",
                        "stage", "special", "mode"], dtype=str, na_filter=False)
    h = h[(h["office"] == "US HOUSE") & (h["stage"] == "GEN") & (h["special"] != "TRUE")]
    h = h.assign(v=pd.to_numeric(h["votes"], errors="coerce").fillna(0.0), fips=h["county_fips"].str.zfill(5),
                 cd=h["district"].astype(int))
    s = h.groupby(["fips", "cd"], as_index=False)["v"].sum().rename(columns={"v": "t"})
    s["d"] = s["r"] = s["t"]       # all House votes: PA-18 2018 had no Republican, so party-specific shares would be 0
    for y in (2016, 2020):
        a = _alloc(s, cp[y])
        g = a[a["st"] == "PA"].groupby(["st", "cd"], as_index=False)[["d", "r", "t"]].sum()
        g["year"], g["method"], g["vint"] = y, "county_split_by_house18", "PA18"
        g["share_unmatched"] = float(a.attrs["unallocated"].get("PA", 0.0))
        rows.append(g)
    p = pd.concat(rows, ignore_index=True)
    p["d2"] = p["d"] / (p["d"] + p["r"])
    p["partial"] = p["share_unmatched"] > 0.05
    return p


def pres_lines_ok(st: str, y: int) -> str:
    """Which presidential vintage matches the House lines of (year, state): '16' (2016 lines), '24' (2024 lines) or ''."""
    if y in (2012, 2014):
        return "16" if st not in {"FL", "NC", "VA"} else ""   # 2012 lines = 2016 lines except FL, NC, VA
    if y == 2016:
        return "16"
    if y == 2018:
        return "16" if st != "PA" else "PA18"
    if y == 2020:
        return {"PA": "PA18", "NC": "NC20"}.get(st, "16")
    if y == 2022:
        return "24" if st not in {"AL", "GA", "LA", "NC", "NY"} else ""   # 2022 lines = 2024 lines elsewhere
    if y == 2024:
        return "24"
    return ""


def add_pres(r: pd.DataFrame) -> pd.DataFrame:
    p16, p24 = pres_lines16(), pres_lines24()
    p16["vint"], p24["vint"] = "16", "24"
    psp = pres_lines_special()
    pv = pd.concat([p16, p24, psp], ignore_index=True)
    lookup = {(row.vint, row.st, row.cd, row.year): row for row in pv.itertuples()}
    cols = {k: [] for k in ("pres_cur_year", "pres_cur_d2", "pres_cur_method", "pres_prev_year", "pres_prev_d2",
                            "pres_prev_dem", "pres_prev_rep", "pres_prev_total", "pres_prev_method",
                            "pres_prev_unmatched", "pres_prev_partial", "pres_vintage")}
    for row in r.itertuples():
        vint = pres_lines_ok(row.st, row.year)
        cur_y = row.year if row.year in PRES_YEARS else None
        prev_y = row.year - 4 if row.year in PRES_YEARS else row.year - 2
        cc = lookup.get((vint, row.st, row.district, cur_y)) if vint and cur_y else None
        pc = lookup.get((vint, row.st, row.district, prev_y)) if vint else None
        cols["pres_vintage"].append(vint or None)
        cols["pres_cur_year"].append(cur_y if cc is not None else np.nan)
        cols["pres_cur_d2"].append(cc.d2 if cc is not None else np.nan)
        cols["pres_cur_method"].append(cc.method if cc is not None else None)
        cols["pres_prev_year"].append(prev_y if pc is not None else np.nan)
        cols["pres_prev_d2"].append(pc.d2 if pc is not None else np.nan)
        cols["pres_prev_dem"].append(pc.d if pc is not None else np.nan)
        cols["pres_prev_rep"].append(pc.r if pc is not None else np.nan)
        cols["pres_prev_total"].append(pc.t if pc is not None else np.nan)
        cols["pres_prev_method"].append(pc.method if pc is not None else None)
        cols["pres_prev_unmatched"].append(getattr(pc, "share_unmatched", np.nan) if pc is not None else np.nan)
        cols["pres_prev_partial"].append(None if pc is None or pd.isna(getattr(pc, "partial", np.nan))
                                         else bool(pc.partial))
    r = pd.concat([r, pd.DataFrame(cols, index=r.index)], axis=1)
    r["pres_swing"] = np.nan   # filled below where both pres years exist on the same vintage
    sw = []
    for row in r.itertuples():
        vint = row.pres_vintage
        if vint and not pd.isna(row.pres_cur_d2) and not pd.isna(row.pres_prev_d2):
            sw.append(row.pres_cur_d2 - row.pres_prev_d2)
        else:
            sw.append(np.nan)
    r["pres_swing"] = sw
    r["house_minus_pres_prev"] = r["d2"] - r["pres_prev_d2"]
    return r


# ------------------------------------------------------------------ 4. polls

def _poll_rows_538() -> pd.DataFrame:
    p = pd.read_csv(POLLS538, low_memory=False)
    p = p[(p["office_type"] == "U.S. House") & (p["stage"] == "general") & ~p["hypothetical"].astype(bool)
          & ~p["ranked_choice_reallocated"].astype(bool) & p["cycle"].isin([2018, 2020, 2022, 2024])].copy()
    p["ed"] = pd.to_datetime(p["election_date"], format="%m/%d/%y")
    p = p[(p["ed"].dt.month == 11) & (p["ed"].dt.year == p["cycle"])]       # regular November election of the cycle
    p["st"] = p["state"].map(STATE_NAME)
    p = p[p["st"].notna() & p["sample_size"].notna() & p["pct"].notna()]
    p["end"] = pd.to_datetime(p["end_date"], format="%m/%d/%y")
    p["start"] = pd.to_datetime(p["start_date"], format="%m/%d/%y")
    rows = []
    for qid, g in p.groupby("question_id"):
        d, r = g[g["party"] == "DEM"], g[g["party"] == "REP"]
        if d.empty or r.empty:
            continue
        d, r = d.sort_values("pct", ascending=False).iloc[0], r.sort_values("pct", ascending=False).iloc[0]
        rows.append({"id": qid, "poll_id": g["poll_id"].iat[0], "year": int(g["cycle"].iat[0]), "st": g["st"].iat[0],
                     "seat": int(g["seat_number"].iat[0]), "pollster": g["pollster"].iat[0],
                     "start": g["start"].iat[0], "end": g["end"].iat[0],
                     "pop": us._POP_RANK.get(str(g["population"].iat[0]), 3), "k": len(g),
                     "n": float(g["sample_size"].iat[0]), "dem": float(d["pct"]), "opp": float(r["pct"]),
                     "s": float(d["pct"]) / (float(d["pct"]) + float(r["pct"])),
                     "d_name": d["candidate_name"], "r_name": r["candidate_name"], "source": "538"})
    return pd.DataFrame(rows)


def _poll_rows_2026() -> pd.DataFrame:
    """VoteHub district polls already matched to D / R candidates by analysis/us_house.py (markets.json)."""
    m = json.loads(MARKETS.read_text())["districts"]
    rows = []
    for k, v in m.items():
        for x in (v.get("polls") or {}).get("polls", []):
            rows.append({"id": f"{k}|{x['pollster']}|{x['end']}", "year": 2026, "st": k[:2],
                         "seat": 0 if k.endswith("AL") else int(k[3:]), "pollster": x["pollster"], "start": pd.NaT,
                         "end": pd.Timestamp(x["end"]), "pop": x["pop"], "k": 2, "n": float(x["n"]),
                         "dem": x["d_pct"], "opp": x["r_pct"], "s": x["d2"], "d_name": x["d"], "r_name": x["r"],
                         "source": "votehub"})
    return pd.DataFrame(rows)


def build_polls(seats: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (polls, averages). One version per (pollster, start, end, district): likely voters over registered over
    adults, then the version with the most candidates (brunder.us.poll_rows rule); average by brunder.us.poll_average
    (sqrt(n) weight, 14-day half-life, 45-day window, 25 % pollster cap) as of Election Day - 1."""
    allp = pd.concat([_poll_rows_538(), _poll_rows_2026()], ignore_index=True)
    # 538 numbers an at-large seat 1; the 2026 rows already use 0 for at-large (markets.json)
    allp["district"] = [0 if seats.get((y, st), 0) == 1 else s for y, st, s in zip(allp["year"], allp["st"], allp["seat"])]
    allp["did"] = [did(st, d) for st, d in zip(allp["st"], allp["district"])]
    allp = allp.sort_values(["pop", "k", "id"], ascending=[True, False, True]) \
        .drop_duplicates(["year", "did", "pollster", "start", "end"]).sort_values(["year", "did", "end"])
    avgs, weighted = [], []
    for (y, d), g in allp.groupby(["year", "did"]):
        asof = (pd.Timestamp(ELECTION_DAY[y]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        g = g.reset_index(drop=True)
        w = us.poll_weights(g, asof)
        g["in_window"], g["weight"], g["age_days"], g["asof"] = False, np.nan, np.nan, asof
        if not w.empty:
            g.loc[w.index, "in_window"] = True
            g.loc[w.index, "weight"] = w["weight"]
            g.loc[w.index, "age_days"] = w["age"]
        weighted.append(g)
        a = us.poll_average(g.drop(columns=["in_window", "weight", "age_days", "asof"]), asof)
        avgs.append({"year": y, "did": d, "st": g["st"].iat[0], "district": int(g["district"].iat[0]),
                     "polls_total": len(g), "poll_avg": a["avg"] if a else np.nan,
                     "poll_sd": a["sd"] if a else np.nan, "poll_n": a["n_polls"] if a else 0,
                     "poll_n_pollsters": a["n_pollsters"] if a else 0, "poll_last_end": a["last_date"] if a else None,
                     "poll_asof": asof})
    polls = pd.concat(weighted, ignore_index=True)
    polls["d2"] = polls["s"]
    polls = polls[["year", "st", "district", "did", "source", "pollster", "start", "end", "n", "pop", "dem", "opp",
                   "d2", "d_name", "r_name", "in_window", "weight", "age_days", "asof"]] \
        .rename(columns={"opp": "rep", "pop": "pop_rank"})
    return polls, pd.DataFrame(avgs)


# ------------------------------------------------------------------ build steps

_RACES = None


def load_races_cached() -> pd.DataFrame:
    global _RACES
    if _RACES is None:
        c = load_candidates()
        _RACES = races_from_candidates(c)
    return _RACES


def step_pres16() -> None:
    s = pres16_shares()
    print("pres16_shares", s.shape, "states", s["st"].nunique())


def step_panel() -> None:
    U.mkdir(parents=True, exist_ok=True)
    c = load_candidates()
    r = races_from_candidates(c)
    global _RACES
    _RACES = r
    lt = lines_table(r)
    r = add_prev_and_incumbency(r, c, lt)
    r = add_fec24(r)
    r = add_pres(r)
    r = r[r["year"].isin(YEARS)].copy()
    polls, avgs = build_polls(r.groupby(["year", "st"])["n_seats"].first())
    _save(polls, "house_polls")
    r = r.merge(avgs.drop(columns=["district", "poll_asof"]), on=["year", "did", "st"], how="left")
    r["poll_n"] = r["poll_n"].fillna(0).astype(int)
    r["poll_n_pollsters"] = r["poll_n_pollsters"].fillna(0).astype(int)
    r["polls_total"] = r["polls_total"].fillna(0).astype(int)
    r["poll_asof"] = [(pd.Timestamp(ELECTION_DAY[y]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d") for y in r["year"]]
    for col in ("inc_won", "pres_prev_partial"):
        r[col] = r[col].astype("boolean")
    # same-lines backtest flags
    r["same_lines"] = ~r["lines_changed"].astype(bool) & (r["year"] > 2012)
    r["swing_ok"] = r["same_lines"] & r["swing_d2"].notna()
    r["backtest_ok"] = r["swing_ok"] & r["pres_prev_d2"].notna()
    order = ["year", "st", "district", "did", "n_seats", "dem", "rep", "total", "d2", "margin", "contested",
             "uncontested_side", "n_dem_cands", "n_rep_cands", "n_cands", "on_ballot_D", "on_ballot_R", "jungle",
             "no_votes_reported", "dem_name", "rep_name", "winner", "winner_party", "winner_votes", "other_votes",
             "total_medsl", "lines_changed", "lines_changed_spec", "lines_note",
             "hold_party", "hold_name", "inc_ran", "inc_name", "inc_party_now", "inc_won", "n_inc_ran", "inc_basis",
             "fec24_inc_flags", "dem_prev", "rep_prev", "d2_prev", "contested_prev", "winner_prev",
             "winner_party_prev", "swing_d2", "pres_vintage", "pres_cur_year", "pres_cur_d2", "pres_cur_method",
             "pres_prev_year", "pres_prev_d2", "pres_prev_dem", "pres_prev_rep", "pres_prev_total",
             "pres_prev_method", "pres_prev_unmatched", "pres_prev_partial", "pres_swing", "house_minus_pres_prev",
             "poll_avg", "poll_sd", "poll_n", "poll_n_pollsters", "poll_last_end", "poll_asof", "polls_total",
             "same_lines", "swing_ok", "backtest_ok"]
    r = r[order].sort_values(["year", "st", "district"]).reset_index(drop=True)
    _save(r, "house_panel", parquet=True)


def step_priors() -> None:
    d26 = json.loads(D26.read_text())["districts"]
    pieces = pd.read_csv(PIECES, dtype={"county": str, "cd119": str, "cd120": str})
    pieces = pieces[(pieces["cd119"] != "ZZ") & (pieces["cd120"] != "ZZ")]
    pieces["did"] = [did(s, int(x)) for s, x in zip(pieces["st"], pieces["cd120"])]
    npieces = pieces.groupby("did").agg(n_county_pieces=("county", "size"), n_counties=("county", "nunique"))
    pri = pd.read_csv(PRIORS_CSV, comment="#")
    pri["did"] = pri["source"].str.replace(r"_pres_2024$", "", regex=True).str.upper()
    pri["did"] = [x if x[3:] != "00" else x[:2] + "-AL" for x in pri["did"]]
    pa = pri.groupby("did").agg(n_priors_units=("unit_id", "size"), priors_dem=("dem", "sum"), priors_rep=("rep", "sum"))
    pa["priors_pres24_d2"] = pa["priors_dem"] / (pa["priors_dem"] + pa["priors_rep"])
    avgs = pd.read_csv(U / "house_polls.csv")
    avgs = avgs[(avgs["year"] == 2026) & avgs["in_window"]]
    pavg = {}
    for dd, g in avgs.groupby("did"):
        a = us.poll_average(pd.DataFrame({"end": g["end"], "n": g["n"], "pollster": g["pollster"], "s": g["d2"],
                                          "dem": g["dem"], "opp": g["rep"]}).reset_index(drop=True), "2026-11-02")
        pavg[dd] = a
    p24 = pd.read_parquet(U / "house_panel.parquet").query("year == 2024").set_index("did")
    rows = []
    for k, v in d26.items():
        core = v.get("house24_core") if v["redrawn"] else v.get("house24")
        cid = v.get("core_2024") if v["redrawn"] else k
        h = p24.loc[cid] if cid in p24.index else None
        rows.append({
            "did": k, "st": v["st"], "district": 0 if k.endswith("AL") else int(k[3:]), "redrawn": bool(v["redrawn"]),
            "pres24_d2": v["pres24"]["d2"], "pres24_method": v["pres24"]["method"],
            "pres20_d2": v["pres20"]["d2"], "pres20_method": v["pres20"]["method"],
            "house24_core_d2": h["d2"] if h is not None and h["contested"] else np.nan,
            "house24_core_contested": None if h is None else bool(h["contested"]),
            "house24_core_winner_party": None if h is None else h["winner_party"],
            "house24_core_d2_site": core["d2"] if core and core.get("d2") is not None else np.nan,
            "core_2024": v.get("core_2024", k if not v["redrawn"] else None),
            "core_share": v.get("core_share", 1.0 if not v["redrawn"] else np.nan),
            "pop2020": v.get("pop2020", np.nan),
            "poll_avg": pavg[k]["avg"] if k in pavg and pavg[k] else np.nan,
            "poll_n": pavg[k]["n_polls"] if k in pavg and pavg[k] else 0,
            "poll_last_end": pavg[k]["last_date"] if k in pavg and pavg[k] else None})
    p = pd.DataFrame(rows).merge(npieces, left_on="did", right_index=True, how="left") \
        .merge(pa[["n_priors_units", "priors_pres24_d2"]], left_on="did", right_index=True, how="left")
    p["n_priors_units"] = p["n_priors_units"].fillna(0).astype(int)
    _save(p.sort_values("did").reset_index(drop=True), "house_districts_2026")


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        out.append("| " + " | ".join("" if (isinstance(x, float) and np.isnan(x)) else str(x) for x in row) + " |")
    return "\n".join(out)


def step_readme() -> None:
    p = pd.read_parquet(U / "house_panel.parquet")
    pl = pd.read_csv(U / "house_polls.csv")
    q = pd.read_csv(U / "house_districts_2026.csv")
    g = p.groupby("year")
    cov = pd.DataFrame({
        "districts": g.size(),
        "contested (D and R)": g["contested"].sum(),
        "same lines as previous": g["same_lines"].sum(),
        "same lines + both contested (swing)": g["swing_ok"].sum(),
        "pres_prev on cycle lines": g["pres_prev_d2"].apply(lambda s: int(s.notna().sum())),
        "same lines + pres_prev (backtest_ok)": g["backtest_ok"].sum(),
        "... and pres not partial": g.apply(lambda d: int((d["backtest_ok"] & ~d["pres_prev_partial"].fillna(True)).sum()),
                                            include_groups=False),
        "pres_cur (same-year pres)": g["pres_cur_d2"].apply(lambda s: int(s.notna().sum())),
        "incumbent ran": g["inc_ran"].sum(),
        "open, holder party known": g.apply(lambda d: int((~d["inc_ran"] & d["hold_party"].notna()).sum()),
                                            include_groups=False),
        "holder party unknown": g["hold_party"].apply(lambda s: int(s.isna().sum())),
        "poll avg in window": g["poll_n"].apply(lambda s: int((s > 0).sum())),
        "polls (any)": g["polls_total"].apply(lambda s: int((s > 0).sum())),
    }).reset_index()
    cov["year"] = cov["year"].astype(str)
    pl_cov = pl.groupby("year").agg(polls=("did", "size"), districts=("did", "nunique"),
                                    in_window_polls=("in_window", "sum")).reset_index()
    pl_cov["districts_with_in_window_avg"] = pl_cov["year"].map(
        pl[pl["in_window"]].groupby("year")["did"].nunique()).fillna(0).astype(int)
    pl_cov["in_window_polls"] = pl_cov["in_window_polls"].astype(int)
    chg_rows = []
    for y in YEARS[1:]:
        sub = p[p["year"] == y]
        chg_rows.append({"cycle": f"{y - 2} -> {y}", "districts on same lines": int(sub["same_lines"].sum()),
                         "swing usable": int(sub["swing_ok"].sum()),
                         "swing + previous pres on same lines": int(sub["backtest_ok"].sum()),
                         "lines changed in": ("all multi-seat states" if y in NATIONAL_REDRAW
                                              else " ".join(sorted(sub[sub["lines_changed"]]["st"].unique())))})
    p16 = p[(p["year"] == 2016) & p["pres_cur_d2"].notna()]
    miss16 = sorted(set(p[p["year"] == 2016]["did"]) - set(p16["did"]))
    p20 = p[(p["year"] == 2020) & p["pres_cur_d2"].notna()]
    p24 = p[(p["year"] == 2024) & p["pres_cur_d2"].notna()]
    norace = p[p["no_votes_reported"]]
    d24 = json.loads(D24.read_text())["districts"]
    x = p[p["year"] == 2024].set_index("did")
    nd = nr = 0
    for k, v in d24.items():
        h = v["house24"]
        nd += int(k in x.index and bool(h["dv"]) and abs(x.loc[k, "dem"] - h["dv"]) / h["dv"] > 0.02)
        nr += int(k in x.index and bool(h["rv"]) and abs(x.loc[k, "rep"] - h["rv"]) / h["rv"] > 0.02)
    unmatched_p = pl[(pl["year"] < 2026) & ~pl["did"].isin(p["did"])]
    only_spec = int((p["lines_changed"] & ~p["lines_changed_spec"]).sum())
    nobody = ", ".join(f"{r.did} {r.year}" for r in norace.itertuples())
    txt = f"""# README_UniHouse: House-district history panel and polls (2012-2024) for the unified pre-count model

Built by `analysis/us_unified_house.py` (`build panel priors readme`; caches in `_cache_house/`). Files in this folder:

| file | rows x cols | content |
|---|---|---|
| `house_panel.parquet` / `.csv` | {len(p)} x {p.shape[1]} | one row per (year 2012..2024 even, state `st`, `district`; 0 = at-large; `did` like "TX-07", "AK-AL") |
| `house_polls.csv` | {len(pl)} x {pl.shape[1]} | every district poll used (538 2018-2024, VoteHub 2026) with in-window flag, weight, age |
| `house_districts_2026.csv` | {len(q)} x {q.shape[1]} | the 435 districts on 2026 lines: county pieces, 2024 president, 2024 House core, redrawn flag, 2026 poll average |

Sources: MIT Election Data and Science Lab (MEDSL) "U.S. House 1976-2024" (`raw/us/medsl_hist/1976-2024-house.tab`, CC0 1.0, comma-separated
despite the extension, official, version 20250910, contains NO special elections); MEDSL 2016 precinct returns president + House
(`raw/us/medsl16`, CC0), MEDSL 2020 NC precinct file (`medsl20`), MEDSL 2018 PA precinct file (`medsl18`), MEDSL county presidential returns
2000-2024 and president-state file (CC0); `site/data/us2026/house/{{districts_2024,districts,markets}}.json` and `raw/us/house/pieces.csv`
(this repo, `analysis/us_house.py`: Census block equivalency files, public domain; MEDSL 2024 precincts); `data_public/us2026_house_priors.csv`;
FiveThirtyEight `house_polls_historical` (CC BY 4.0); VoteHub `us-representative` polls fetched 2026-10-29 (CC BY 4.0).

## Vintage rule (no look-ahead)
The row of election year t holds the result of t (`dem rep total d2 margin contested winner*`; `swing_d2 = d2 - d2_prev` is also a target)
and, as features, only information published before Election Day of t: `lines_changed`; incumbency (`hold_*`, `inc_*`: previous-cycle winners
and the candidate list of the t ballot, known after the primaries); previous House result (`*_prev`); previous presidential result on the
cycle's own lines (`pres_prev_*`: the last presidential election before t, i.e. t-2 in midterm years and t-4 in presidential years);
poll average (`poll_*`: field end <= Election Day - 1, 45-day window). `pres_cur_*`, `pres_swing`, `house_minus_pres_prev` use the
SAME-year presidential vote (2012, 2016, 2020, 2024) and are diagnostics (coattails), NOT pre-count features. `fec24_inc_flags` is the
FEC "(I)" count of the 2024 ballot list and a cross-check only (it flags e.g. both candidates of DE-AL: not reliable).

## Definitions
- `dem`, `rep`: votes of the leading Democratic / Republican candidate (all ballot lines of that candidate: NY fusion lines summed; DFL and
  Democratic-NPL count as Democratic; write-ins are never D/R). `total`: all candidate votes incl. scattering and write-ins, excluding blank, void,
  over/under votes and Maine's "exhausted ballots" (`total_medsl` = the file's total, for comparison). `d2 = dem/(dem+rep)` is 1 or 0 when one party is
  absent: always read it with `contested`. `margin = (dem-rep)/total`.
- `contested` = a Democrat and a Republican, both with votes. `uncontested_side`: "D" / "R" = only that party's candidate(s) on the ballot (CA
  D-vs-D races give "D"), "none" = neither. `no_votes_reported`: unopposed races where the state published no count (MEDSL sentinel
  candidatevotes 1 / totalvotes -1; votes set to 0): {len(norace)} races: {nobody}.
- `jungle`: Louisiana 2012-2022, open November primary: several same-party candidates, `dem`/`rep` are the leading D / R; December runoffs are not in the file.
- `lines_changed`: the state's district map differs from the previous cycle (single-seat states never change). `lines_changed_spec` is the list of the task
  (national 2012 and 2022; PA 2018; AL GA LA NC NY 2024). `lines_changed` ADDS the maps that changed in between, found with the incumbent-overlap
  check below: 2016 FL, NC, VA and 2020 NC (court-ordered or remedial maps); {only_spec} district rows differ between the two flags.
  `*_prev` columns and `swing_d2` are NaN when the lines changed. `same_lines` = not changed and year > 2012.
- Incumbency: `hold_party` = party that held the seat before the election. Lines unchanged: the previous cycle's winner of the same district
  (`inc_basis` = prev_winner_same_district). Lines changed: only known if an incumbent runs (`inc_basis` = name_match_state: a candidate on the
  ballot who won a House seat in the same state in the previous cycle; surname + first initial, or a surname unique among the state's winners), else NaN.
  `inc_ran`, `inc_name`, `inc_party_now` (party label of that candidate now; differs from `hold_party` for party switchers), `inc_won`, `n_inc_ran`
  (2 = paired incumbents). LIMITATION: the MEDSL file has no special elections: a seat that changed hands between elections (death, resignation,
  special) is treated as held by the last regular winner, and a special-election winner who runs shows as an open seat. Incumbents who lost their primary or
  ran for another office are correctly "not running". Cross-check: in every unflagged multi-seat state-cycle the share of seats whose previous winner is on the ballot
  again is >= 0.5 except NM 2018 (0.33; two of three seats open); in the states flagged for a map change in 2016-2024 it is 0.61-0.93, so this overlap alone does not separate redrawn from
  stable maps; the extra flags (2016 FL NC VA, 2020 NC) come from the known court-ordered maps, not from this check.
- President by district (`pres_*`; d2 = Democratic share of D+R). `pres_vintage` = which map: '16' = 2016-line districts (valid for 2012/2014 except FL NC VA, 2016,
  2018 except PA, 2020 except PA NC), 'PA18' = PA 2018 lines, 'NC20' = NC 2020 lines, '24' = 2024 lines (valid for 2022 except AL GA LA NC NY, and 2024).
  * 2016 on '16': `precinct_house_split`. MEDSL 2016 precinct president and House returns are joined on (state, county FIPS, jurisdiction, precinct); each precinct's
    presidential votes go to the House districts on its ballot in proportion to its House votes, each county's official total (MEDSL county file) is split by these
    shares. Clinton wins {int((p16['pres_cur_d2'] > 0.5).sum())}, Trump wins {int((p16['pres_cur_d2'] < 0.5).sum())} of the {len(p16)} districts with a result; no result for
    {", ".join(miss16)} (unopposed House races without a count leave the district without House precinct rows).
  * 2012 and 2020 on '16': `county_split_by_2016`: the same county totals split like the county's 2016 votes (approximation: the 2016 within-county pattern;
    misses within-county change). 2020: Biden wins {int((p20['pres_cur_d2'] > 0.5).sum())} of {len(p20)} districts with a result.
  * NC 2020 lines: 2020 president from MEDSL 2020 NC precincts (`precinct_house_split`); 2016 on NC 2020 lines `county_split_by_2020`.
  * PA 2018 lines: the 2016 and 2018 precinct names do not join and no 2018-line president file exists, so each county's 2016 and 2020 president votes are split by
    the county's total 2018 House votes (`county_split_by_house18`, the roughest method).
  * '24': districts_2024.json `pres20` (`county_split_by_2024`, approximation, doc 'US House districts') and `pres24` (`precinct` / `county`): Harris wins
    {int((p24['pres_cur_d2'] > 0.5).sum())} of {len(p24)}.
  * `pres_prev_unmatched`: share of the state's presidential votes not tied to a House precinct or county (2016 vintage). `pres_prev_partial` = True when it exceeds 5 % or
    a district of the state has no House precinct rows at all (affects OK, TX, MO, FL in the 2016 vintage; OK has 21 % unmatched). Values are kept, filter on it for a strict
    backtest. District sums reach at least 95 % of the state totals in every state but OK (79 %).
- `swing_d2 = d2 - d2_prev` only when lines are unchanged AND both cycles contested. `d2_prev`, `dem_prev`, `rep_prev`, `winner_prev`: unchanged lines only.
- Polls (`poll_*`): the rule of `brunder.us` (`poll_weights`, `poll_average`: sqrt(n) weight, 14-day half-life, 45-day window, 25 % pollster cap) as of
  Election Day - 1. One version per (pollster, dates, district): likely voters over registered over adults, then the version with most candidates; two-party
  share D/(D+R) of the leading named Democrat and Republican; 538 rows with `hypothetical`, ranked-choice reallocation, specials and non-November
  elections are dropped. `poll_n` = polls in the window (0: `poll_avg` NaN), `polls_total` = polls found for the district. 538's file has the cycles
  2018, 2020, 2022 and 2024 only: NO district polls for 2012-2016. 2026: VoteHub polls (file of 2026-10-29) as matched to D / R candidates by
  `analysis/us_house.py` (markets.json), averaged as of 2026-11-02 (`us.POLLS_ASOF`). `poll_sd` = sqrt(0.039^2 + sampling variance) (`us.poll_average`).

## Coverage by year
{_md_table(cov)}

District polls (`house_polls.csv`; {len(unmatched_p)} polls of 2018-2024 have no panel district):

{_md_table(pl_cov)}

## Which transitions allow a SAME-LINES backtest (these define the House backtest of the later phases)
A same-lines transition (t-2 -> t) means the previous House result, the swing and the previous presidential vote are on the same lines as the target.

{_md_table(pd.DataFrame(chg_rows))}

Statement: same-lines House backtests are possible for 2012 -> 2014 (all 435 districts), 2014 -> 2016 (all but FL, NC, VA), 2016 -> 2018 (all but PA),
2018 -> 2020 (all but NC; PA is on its 2018 map in both) and 2022 -> 2024 (all but AL, GA, LA, NC, NY). 2020 -> 2022 has NO usable same-lines House
transition (national redistricting; only the single-seat states AK, DE, ND, SD, VT, WY keep their lines): for 2022 a lagged House result on the cycle's lines does not
exist; the 2020 presidential vote on 2022 lines exists for 45 states (`pres_prev_d2`, vintage '24', not AL GA LA NC NY). District polls exist only for 2018-2024
(and 2026) and cover few districts: {int(p[p['year']==2018]['poll_n'].gt(0).sum())}, {int(p[p['year']==2020]['poll_n'].gt(0).sum())}, {int(p[p['year']==2022]['poll_n'].gt(0).sum())}, {int(p[p['year']==2024]['poll_n'].gt(0).sum())} districts
with an in-window average in 2018, 2020, 2022, 2024. Presidential results on the cycle's lines come from approximations in 2012, 2020 (county splits)
and PA (2018-20); 2016 and NC 2020 are precinct based. For the 2026 forecast the 2024 -> 2026 transition is same-lines for {int((~q['redrawn']).sum())} districts; the
{int(q['redrawn'].sum())} redrawn districts (AL CA FL LA NC OH TN TX UT) have only the crosswalk values (2024 president and House of the "core" 2024 district).
The strict feature set for a backtest (previous House, swing, previous president, no partial president split) is `backtest_ok & ~pres_prev_partial` (column "... and pres not partial").

## 2026-line district priors (`house_districts_2026.csv`)
The 435 districts of `site/data/us2026/house/districts.json`: `redrawn` (AL CA FL LA NC OH TN TX UT), `n_county_pieces` (county x district pieces of the
2026 district in the Census block equivalency files, `raw/us/house/pieces.csv`; `n_counties` = distinct counties), `pres24_d2` / `pres20_d2` (Harris / Biden
two-party share on 2026 lines, `*_method`), `house24_core_d2` (2024 House two-party share from the MEDSL official file of the 2024 district that gave the 2026
district most people (`core_2024`, `core_share`), the district itself when not redrawn; NaN when that race was uncontested, see `house24_core_contested`;
`house24_core_d2_site` is the districts.json value, computed from precinct sums, which differs by more than 2 % from the MEDSL official file in {nd} (D votes) and
{nr} (R votes) of 435 districts, e.g. AK, CT, IN, LA, NJ: use the MEDSL column), `n_priors_units` / `priors_pres24_d2` (units and Harris share in
`data_public/us2026_house_priors.csv`, the election-night priors: {int((q['n_priors_units'] > 0).sum())} districts), `poll_avg` / `poll_n` / `poll_last_end` (VoteHub, as of
2026-11-02; {int((q['poll_n'] > 0).sum())} districts with an average in the window).

## Known gaps and cautions
- Special elections are not in the MEDSL file (see incumbency); DC and the territories are excluded.
- Louisiana 2012-2022 jungle primary and California / Washington top-two: `contested` is False where two same-party candidates meet.
- AK 2024: ranked-choice first round with two Democratic-labelled candidates (`dem` = the leading Democrat); Maine: first round, exhausted ballots excluded.
- Unopposed races without counts ({len(norace)}) have no `d2`; their district has no 2016 president split where the precinct rows are missing.
- The `house24` numbers of `districts_2024.json` (precinct sums) are NOT used: they differ from the MEDSL official file by more than 2 % in {nd} districts.
- 2012 has no previous-cycle columns on the same lines (new maps everywhere); its `hold_party` comes from name matches with the 2010 winners.
- District polls are sparse and the pollster mix is unrepresentative; 2026 poll parties are matched by candidate surname (`analysis/us_house.py::_party_lookup`).
"""
    (U / "README_UniHouse.md").write_text(txt)
    print(f"README_UniHouse.md written ({len(txt)} chars)")


def fetch() -> None:
    need = {"MEDSL House 1976-2024": HIST_HOUSE, "MEDSL president state": HIST_PRES_STATE,
            "MEDSL county president": COUNTYPRES, "MEDSL 2016 House precincts": MEDSL16_HOUSE,
            "MEDSL 2016 president precincts": MEDSL16_PRES, "538 House polls": POLLS538,
            "VoteHub US representative 20261029": VOTEHUB_US_REP, "FEC 2024 candidates": FEC24,
            "block pieces": PIECES, "districts_2024.json": D24, "districts.json": D26, "markets.json": MARKETS,
            "house priors csv": PRIORS_CSV}
    for k, f in need.items():
        print(f"{'ok ' if f.exists() else 'MISSING'} {k}: {f}")
    if not all(f.exists() for f in need.values()):
        raise SystemExit("missing input")


STEPS = {"pres16": step_pres16, "panel": step_panel, "priors": step_priors, "readme": step_readme}

if __name__ == "__main__":
    args = sys.argv[1:] or ["fetch", "build"]
    if args[0] == "fetch":
        fetch()
        args = args[1:]
    if args and args[0] == "build":
        for s in args[1:] or list(STEPS):
            if s not in STEPS:
                raise SystemExit(f"unknown step {s}; steps: {', '.join(STEPS)}")
            STEPS[s]()
