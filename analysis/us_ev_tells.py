"""Count-night "tells": how far the raw count margin sits from the final certified margin as the vote comes in, and which
vote types / geographies report when. DESCRIPTIVE ONLY: it informs a later model version, it is not an input of any
registered 2026 model or hypothesis.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_tells.py [--md PATH]   # → site/data/us2026/tells.json

Sign convention: every margin is the two-party Democratic minus Republican margin, (D - R) / (D + R) in points.
gap(q) = raw count margin when q % of the final two-party vote is counted - final certified margin. gap > 0: the early
count is more Democratic than the final result ("blue mirage", later "red shift"); gap < 0: the early count is more
Republican than the final result ("red mirage", later "blue shift").

Timed sources (a state-level count series only exists where a feed was archived during the count):
  2020  New York Times / Edison county feed, Nov 3 - Dec 9 2020 (Harvard Dataverse doi:10.7910/DVN/LZXR5A "The Blue Shift
        in the 2020 Election", file nyt_counties.tab, id 4749507, CC0 1.0). The dataset sits behind a Dataverse guestbook;
        this script reads the copy already on disk and downloads nothing from that dataset.
  2024  CNN / Edison state feed mirrored by MEDSL (doi:10.7910/DVN/2CKSIK, file state_ENR_president.tab id 10883470, CC0 1.0;
        downloaded over plain HTTP from the Dataverse access API) for the state series; its county file
        county_ENR_president.csv (id 10988774; parquet copy on disk) for geography only (README: county file overcounts).
  final  MEDSL 1976-2024 president by state (certified), used as the denominator of "% counted" and for the final margin.
Vote-type sources (certified, by mode of voting): MEDSL county presidential returns 2000-2024 (doi:10.7910/DVN/VOQCHQ, CC0),
MEDSL precinct returns 2018 / 2022 / 2024 (github.com/MEDSL, CC0), Georgia SoS Enhanced Voting results 2020 / 2022 / 2024.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BRUNDER_DATA", ROOT / "data")).expanduser()
RAW = DATA / "raw" / "us"
TELLS = RAW / "tells"                      # cache of what this script downloaded
PROC = DATA / "processed" / "us" / "tells"
US2024 = Path(os.environ.get("US2024_DATA", "~/us2024_data")).expanduser() / "raw" / "us"
OUT = ROOT / "site" / "data" / "us2026" / "tells.json"
UA = "brunder/0.1 (research; ev-gap analysis)"
QS = (10, 25, 50, 75)
MIN_MODE_COVERAGE = 0.9                    # classified share of a state's two-party vote needed for a vote-type row
BIG_SHARE = 0.2                            # "large units" = top fifth of counties by final vote
MIN_UNITS = 5
SIGNAL = 2.0                               # points: |mean gap at 25-50 %| below this = "little early bias"

STATE_ENR_URL = "https://dataverse.harvard.edu/api/access/datafile/10883470"
FILES = {
    "nyt2020": ("nyt_counties.tab", US2024 / "nyt2020"),
    "enr24_state": ("state_ENR_president.tab", None),
    "enr24_county": ("county_ENR_president.parquet", US2024 / "enr2024"),
    "final": ("1976-2024-president.csv", US2024 / "medsl_state"),
}
SOURCES = [
    {"id": "nyt2020", "what": "2020 county count feed (NYT/Edison), timed", "file": "nyt_counties.tab",
     "doi": "10.7910/DVN/LZXR5A", "licence": "CC0 1.0"},
    {"id": "enr24_state", "what": "2024 state count feed (CNN/Edison mirrored by MEDSL), timed", "file": "state_ENR_president.tab",
     "doi": "10.7910/DVN/2CKSIK", "licence": "CC0 1.0"},
    {"id": "enr24_county", "what": "2024 county count feed, timed (geography only)", "file": "county_ENR_president.csv",
     "doi": "10.7910/DVN/2CKSIK", "licence": "CC0 1.0"},
    {"id": "final", "what": "certified president by state 1976-2024", "file": "1976-2024-president.csv",
     "doi": "10.7910/DVN/42MVDX", "licence": "CC0 1.0"},
    {"id": "medsl_county", "what": "certified county president by vote mode 2020, 2024", "file": "countypres_2000-2024.csv",
     "doi": "10.7910/DVN/VOQCHQ", "licence": "CC0 1.0"},
    {"id": "medsl_precinct", "what": "certified precinct returns by vote mode 2018, 2022, 2024",
     "file": "medsl18/2018-<st>-precinct-general.zip, medsl22/2022-<st>-local-precinct-general.zip, medsl24/<st>24.zip",
     "doi": "github.com/MEDSL", "licence": "CC0 1.0"},
    {"id": "ga_sos", "what": "Georgia SoS Enhanced Voting results by vote type 2020, 2022, 2024",
     "file": "ga/2020NovGen_pres.json, ga/2022NovGen_gov.json, ga/2024NovGen_pres.json",
     "doi": "results.sos.ga.gov", "licence": "public record (state of Georgia)"},
]
NOT_DOWNLOADED = {
    "10.7910/DVN/LZXR5A other files (nyt_states.tab, state_closing_times.tab, GA_precincts.csv, ...)":
        "Dataverse guestbook required (name, e-mail); not requested, not used",
    "10.7910/DVN/K7760H (MEDSL 2020 precinct returns)": "Dataverse guestbook required; not used",
    "robots.txt of dataverse.harvard.edu": "answered HTTP 202 (challenge) to plain requests; only the documented access API was used",
}
_G = ", ".join

# vote-type labels (upper case) -> class. Order matters: first match wins.
_MODE_CLASS = [
    (re.compile(r"PROVISIONAL|^PROV$|FAILSAFE|LATE VOTES|CURED"), "other"),
    (re.compile(r"ELECTION DAY|POLLING|VOTE CENTER|^EDAY$|^IN[- ]PERSON$|NOT ABSENTEE|^MACHINE$"), "eday"),
    (re.compile(r"ABSENTEE ?/ ?EARLY|EARLY ?/ ?ABSENTEE"), "mail"),          # one combined pre-Election-Day row
    (re.compile(r"EARLY|ADVANCE|ONE STOP|IN[- ]PERSON ABSENTEE"), "early"),
    (re.compile(r"MAIL|ABSENTEE"), "mail"),
]
CLASSES = ("early", "mail", "eday", "other")
# states whose 'absentee' label covers every pre-Election-Day ballot (in-person early included): no separate early row
MODE_CAVEATS = {"IA": "mail row = all ballots cast before Election Day (in-person early included); no separate early row",
                "VA": "mail row = all ballots cast before Election Day (in-person early included); no separate early row",
                "AZ": "early row = every ballot cast before Election Day, by mail or in person (AZ has no separate mail row)",
                "PA": "mail row = mail-in ballots; PA has no in-person early voting beside in-person mail-in at county offices",
                "NE": "2018 absentee and early are one combined row (counted as mail)"}
NYT_RELEASE = [
    (re.compile(r"first report|FIRST cumulative", re.I), "first"),
    (re.compile(r"Merged into voters' home precincts and released", re.I), "merged"),
    (re.compile(r"periodically|middle", re.I), "middle"),
    (re.compile(r"end of tabulation|AFTER election night|at end", re.I), "end"),
]
RELEASE_TEXT = {"first": "absentee/mail ballots in the first reports", "merged": "absentee ballots merged into precinct totals",
                "middle": "absentee ballots released mid-count", "end": "absentee ballots released last / after election night"}


def classify_mode(label) -> str | None:
    """'early' / 'mail' / 'eday' / 'other' for a MEDSL / state mode label; None for TOTAL and labels we do not understand."""
    s = str(label).strip().upper()
    if s in ("TOTAL", "NAN", ""):
        return None
    for rx, cls in _MODE_CLASS:
        if rx.search(s):
            return cls
    return None


def margin(d: float, r: float) -> float | None:
    """Two-party D - R margin in points."""
    return None if d + r <= 0 else 100.0 * (d - r) / (d + r)


def _r(x, nd=1):
    return None if x is None or not np.isfinite(float(x)) else round(float(x), nd)


# ------------------------------------------------------------------ timed count series

def count_series(rows: pd.DataFrame) -> pd.DataFrame:
    """Statewide cumulative d, r by time from unit observations (columns unit, time, d, r; cumulative counts as the feed
    showed them). An observation that lowers its unit's running maximum of d + r is a feed glitch and is dropped; each unit
    keeps its last accepted value until it reports again. Returns a frame indexed by time with columns d, r."""
    rows = rows.sort_values(["unit", "time"], kind="stable")
    tot = rows["d"] + rows["r"]
    top = tot.groupby(rows["unit"]).cummax()
    prev = top.groupby(rows["unit"]).shift(1).fillna(0.0)
    rows = rows[tot >= prev].drop_duplicates(["unit", "time"], keep="last")
    out = {}
    for col in ("d", "r"):
        out[col] = rows.pivot(index="time", columns="unit", values=col).sort_index().ffill().fillna(0.0).sum(axis=1)
    return pd.DataFrame(out)


def crossings(ser: pd.DataFrame, final_d: float, final_r: float, qs=QS) -> dict[int, dict]:
    """For each q (percent of the final two-party vote): the counted d, r and the hours since the first reported vote at
    the moment the count reached q, interpolated linearly in votes between the two snapshots around q. A q the series
    never reaches is absent."""
    tot = (ser["d"] + ser["r"]).to_numpy(float)
    f = np.maximum.accumulate(tot) / (final_d + final_r)
    pos = np.flatnonzero(tot > 0)
    if not len(pos):
        return {}
    t = ser.index.to_numpy("datetime64[s]").astype("int64") / 3600.0
    t0 = t[pos[0]]
    out = {}
    for q in qs:
        hit = np.flatnonzero(f >= q / 100.0)
        if not len(hit):
            continue
        i = int(hit[0])
        if i == 0:
            d, r, h = ser["d"].iat[0], ser["r"].iat[0], t[0]
            scale = (q / 100.0) / f[0]
            d, r = d * scale, r * scale
        else:
            w = (q / 100.0 - f[i - 1]) / (f[i] - f[i - 1])
            d = ser["d"].iat[i - 1] + w * (ser["d"].iat[i] - ser["d"].iat[i - 1])
            r = ser["r"].iat[i - 1] + w * (ser["r"].iat[i] - ser["r"].iat[i - 1])
            h = t[i - 1] + w * (t[i] - t[i - 1])
        out[q] = {"d": float(d), "r": float(r), "hours": float(max(h - t0, 0.0))}
    return out


def cycle_gaps(ser: pd.DataFrame, final_d: float, final_r: float, qs=QS) -> dict:
    """gaps (raw margin - final margin) and hours at each q, count-block margins (0-25, 25-50, 50-75, 75-100 % of the final
    vote), and the feed's own coverage (its last total / the certified total)."""
    fin = margin(final_d, final_r)
    cr = crossings(ser, final_d, final_r, sorted(set(qs) | {25, 50, 75}))
    gaps = {str(q): _r(margin(cr[q]["d"], cr[q]["r"]) - fin) for q in qs if q in cr and margin(cr[q]["d"], cr[q]["r"]) is not None}
    hours = {str(q): _r(cr[q]["hours"], 1) for q in qs if q in cr}
    blocks = []
    if all(q in cr for q in (25, 50, 75)):
        pts = [(0.0, 0.0)] + [(cr[q]["d"], cr[q]["r"]) for q in (25, 50, 75)] + [(final_d, final_r)]
        blocks = [_r(margin(b[0] - a[0], b[1] - a[1])) for a, b in zip(pts, pts[1:])]
    cov = float((ser["d"].iat[-1] + ser["r"].iat[-1]) / (final_d + final_r))
    return {"gaps": gaps, "hours": hours, "blocks": blocks, "feed_coverage": _r(cov, 3), "final_margin": _r(fin)}


def summarise(per_cycle: dict[int, dict[str, float]], qs=QS) -> dict:
    """{q: {mean, min, max, n}} over cycles (a cycle with no value at q does not count)."""
    out = {}
    for q in qs:
        v = [g[str(q)] for g in per_cycle.values() if g.get(str(q)) is not None]
        out[str(q)] = ({"mean": _r(np.mean(v)), "min": _r(min(v)), "max": _r(max(v)), "n": len(v)} if v
                       else {"mean": None, "min": None, "max": None, "n": 0})
    return out


def geography(units: pd.DataFrame, q: int = 25) -> dict | None:
    """Which units report early. units: unit, time, d, r (cumulative, as count_series). Large units = the top fifth of units by
    the feed's final vote. big_final = their share of the final vote; big_at_q = their share of the vote counted when the
    count reached q % of the feed's own final total (above big_final = large units report disproportionately early);
    big_margin / rest_margin = final feed margins of large units and of the rest."""
    rows = units.sort_values(["unit", "time"], kind="stable")
    tot = rows["d"] + rows["r"]
    top = tot.groupby(rows["unit"]).cummax()
    rows = rows[tot >= top.groupby(rows["unit"]).shift(1).fillna(0.0)].drop_duplicates(["unit", "time"], keep="last")
    v = (rows.assign(v=rows["d"] + rows["r"]).pivot(index="time", columns="unit", values="v").sort_index().ffill().fillna(0.0))
    if v.shape[1] < MIN_UNITS or v.iloc[-1].sum() <= 0:
        return None
    last = v.iloc[-1]
    n_big = max(1, int(round(BIG_SHARE * len(last))))
    big = last.sort_values(ascending=False).index[:n_big]
    f = v.sum(axis=1).to_numpy() / last.sum()
    hit = np.flatnonzero(f >= q / 100.0)
    if not len(hit):
        return None
    at = v.iloc[int(hit[0])]
    dr = {c: rows.pivot(index="time", columns="unit", values=c).sort_index().ffill().fillna(0.0).iloc[-1] for c in ("d", "r")}
    isbig = last.index.isin(big)
    return {"units": int(len(last)), "big_units": int(n_big), "big_final": _r(last[isbig].sum() / last.sum(), 3),
            "big_at_q": _r(at[isbig].sum() / at.sum(), 3),
            "big_margin": _r(margin(dr["d"][isbig].sum(), dr["r"][isbig].sum())),
            "rest_margin": _r(margin(dr["d"][~isbig].sum(), dr["r"][~isbig].sum()))}


def release_mix(df: pd.DataFrame) -> dict[str, float]:
    """2020 NYT feed: how the county's absentee ballots were released (first / merged / middle / end), as a share of the
    state's final two-party vote. df: unit, final, method."""
    def cls(s):
        for rx, c in NYT_RELEASE:
            if rx.search(str(s)):
                return c
        return "other"
    g = df.assign(c=df["method"].map(cls)).groupby("c")["final"].sum()
    return {k: round(float(v / g.sum()), 3) for k, v in g.items()} if g.sum() > 0 else {}


# ------------------------------------------------------------------ vote types

def mode_table(agg: pd.DataFrame, min_coverage: float = MIN_MODE_COVERAGE) -> dict[str, dict]:
    """Vote-type table per state from county x mode rows. agg: state, fips, mode, party (DEMOCRAT / REPUBLICAN), votes. A
    county is "classified" when its understood mode rows add up to 0.9-1.1 of its TOTAL row (or it has no TOTAL row); other
    counties only have a TOTAL. A state is reported when its classified counties hold >= min_coverage of its two-party
    vote. Result per state: {early, mail, eday, other: {share (of the classified two-party vote), margin (D-R pts)},
    coverage, counties}. A class with no votes is omitted."""
    a = agg[agg["party"].isin(["DEMOCRAT", "REPUBLICAN"])].copy()
    a["fips"] = a["fips"].fillna("_none")          # rows without a county (e.g. Alaska absentee pseudo-precincts) stay in
    a["cls"] = a["mode"].map(classify_mode)
    a["is_total"] = a["mode"].astype(str).str.strip().str.upper() == "TOTAL"
    out = {}
    for st, g in a.groupby("state"):
        tot = g[g["is_total"]].groupby("fips")["votes"].sum()
        cl = g[g["cls"].notna()]
        mod = cl.groupby(["fips", "cls", "party"])["votes"].sum().unstack(["cls", "party"]).fillna(0.0)
        msum = cl.groupby("fips")["votes"].sum()
        idx = tot.index.union(msum.index)
        msum, t = msum.reindex(idx).fillna(0.0), tot.reindex(idx).fillna(0.0)
        ratio = msum / t.where(t > 0)
        ok = (msum > 0) & (t.le(0) | ratio.between(0.9, 1.1))
        state_total = float(np.where(ok, msum, t).sum())
        if state_total <= 0:
            continue
        cov = float(msum[ok].sum() / state_total)
        if cov < min_coverage:
            continue
        row = {"coverage": _r(cov, 3), "counties": int(ok.sum())}
        sel = mod.loc[mod.index.isin(ok[ok].index)]
        den = float(sel.to_numpy().sum())
        row["votes"] = int(round(den))
        for cls in CLASSES:
            d = float(sel[(cls, "DEMOCRAT")].sum()) if (cls, "DEMOCRAT") in sel else 0.0
            r = float(sel[(cls, "REPUBLICAN")].sum()) if (cls, "REPUBLICAN") in sel else 0.0
            if d + r > 0:
                row[cls] = {"share": _r((d + r) / den, 3), "margin": _r(margin(d, r))}
        if max((row[c]["share"] for c in CLASSES if c in row), default=1.0) >= 0.99:
            continue                                  # one class holds everything: the file does not really split by mode
        out[st] = row
    return out


def parse_ga_modes(item: bytes) -> dict | None:
    """Georgia SoS Enhanced Voting ballot item (…NovGen_<race>.json) -> {early, mail, eday, other: {share, margin}} from the
    statewide D / R group results. Advance(d) Voting = early, Absentee by Mail = mail, Election Day, Provisional = other."""
    b = json.loads(item)["ballotItemWithBreakdown"]
    votes = {c: {"D": 0.0, "R": 0.0} for c in CLASSES}
    for o in b["summaryResults"]["ballotOptions"]:
        p = ((o.get("party") or {}).get("abbreviation") or "").upper()
        if p not in ("DEM", "REP"):
            continue
        for g in o.get("groupResults") or []:
            cls = classify_mode(g["groupName"][0]["text"])
            if cls:
                votes[cls]["D" if p == "DEM" else "R"] += float(g["voteCount"])
    den = sum(v["D"] + v["R"] for v in votes.values())
    if den <= 0:
        return None
    row = {"coverage": 1.0, "counties": len(b.get("breakdownResults") or []), "votes": int(round(den))}
    for cls, v in votes.items():
        if v["D"] + v["R"] > 0:
            row[cls] = {"share": _r((v["D"] + v["R"]) / den, 3), "margin": _r(margin(v["D"], v["R"]))}
    return row


def label(by_cycle: dict[int, dict[str, float]]) -> str:
    """Classify a state from its mean gap at 25 % and 50 % counted: red mirage (early count more Republican, later blue shift),
    blue mirage (early count more Democratic, later red shift), cycle-dependent (the cycles disagree beyond +-SIGNAL) or
    little early bias."""
    vals = []
    for g in by_cycle.values():
        v = [g[k] for k in ("25", "50") if g.get(k) is not None]
        if v:
            vals.append(float(np.mean(v)))
    if not vals:
        return "no timed data"
    if len(vals) > 1 and min(vals) < -SIGNAL and max(vals) > SIGNAL:
        return "cycle-dependent (red mirage in one cycle, blue mirage in the other)"
    m = float(np.mean(vals))
    if m <= -SIGNAL:
        return "red mirage -> blue shift (early count more Republican than the final)"
    if m >= SIGNAL:
        return "blue mirage -> red shift (early count more Democratic than the final)"
    return "little early bias (|mean gap at 25-50 %| < 2 pts)"


# ------------------------------------------------------------------ loading

def find(key: str) -> Path:
    name, alt = FILES[key]
    for d in (TELLS, alt):
        if d is not None and (d / name).exists():
            return d / name
    raise FileNotFoundError(f"{name}: not in {TELLS}" + (f" or {alt}" if alt else ""))


def fetch_state_enr() -> Path:
    """state_ENR_president.tab over plain HTTP (Dataverse access API, follows the documented 303 to storage), cached."""
    dest = TELLS / "state_ENR_president.tab"
    if dest.exists():
        return dest
    import httpx
    TELLS.mkdir(parents=True, exist_ok=True)
    r = httpx.get(STATE_ENR_URL, headers={"User-Agent": UA}, follow_redirects=True, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def load_final() -> tuple[dict[tuple[int, str], tuple[float, float]], dict[str, str]]:
    d = pd.read_csv(find("final"), usecols=["year", "state", "state_po", "party_simplified", "candidatevotes"])
    d = d[d["year"].isin([2020, 2024])]
    p = d.pivot_table(index=["year", "state_po"], columns="party_simplified", values="candidatevotes", aggfunc="sum")
    fin = {(int(y), st): (float(r["DEMOCRAT"]), float(r["REPUBLICAN"])) for (y, st), r in p.iterrows()}
    names = {s.upper(): po for s, po in d[["state", "state_po"]].drop_duplicates().itertuples(index=False)}
    return fin, names


def nyt_units(names: dict[str, str]) -> pd.DataFrame:
    d = pd.read_csv(find("nyt2020"), sep="\t", low_memory=False,
                    usecols=["fips", "state", "time", "trumpd", "bidenj", "absentee_method"])
    d["st"] = d["state"].str.upper().map(names)
    d["unit"] = d["fips"].astype(str)
    return d.assign(time=pd.to_datetime(d["time"]), d=d["bidenj"].astype(float), r=d["trumpd"].astype(float))[
        ["st", "unit", "time", "d", "r", "absentee_method"]]


def enr_state() -> pd.DataFrame:
    s = pd.read_csv(fetch_state_enr(), sep="\t")
    s = s[s["candidate"].isin(["Harris", "Trump"])]
    p = s.pivot_table(index=["state", "time"], columns="candidate", values="votes", aggfunc="last").reset_index()
    return p.rename(columns={"state": "st", "Harris": "d", "Trump": "r"}).assign(time=lambda x: pd.to_datetime(x["time"], format="ISO8601"), unit=lambda x: x["st"])


def enr_county() -> pd.DataFrame:
    c = pd.read_parquet(find("enr24_county"), columns=["state", "cnn_code", "candidate", "votes", "datetime"])
    c = c[c["candidate"].isin(["Harris", "Trump"])]
    p = c.pivot_table(index=["state", "cnn_code", "datetime"], columns="candidate", values="votes", aggfunc="last", observed=True).reset_index()
    return p.rename(columns={"state": "st", "cnn_code": "unit", "datetime": "time", "Harris": "d", "Trump": "r"}).astype({"st": str})


def _county_key(df: pd.DataFrame) -> pd.Series:
    f = df["county_fips"].astype(str).str.replace(r"\.0$", "", regex=True)
    f = f.where(~df["county_fips"].isna(), df["county_name"].astype(str))
    return f.where(~f.str.fullmatch(r"\d+"), f.str.zfill(5))


def medsl_county_modes() -> pd.DataFrame:
    """countypres_2000-2024.csv rows for 2020 and 2024 as state, year, office, fips, mode, party, votes."""
    d = pd.read_csv(RAW / "medsl" / "countypres_2000-2024.csv", dtype=str)
    d = d[d["year"].isin(["2020", "2024"])]
    return pd.DataFrame({"state": d["state_po"], "year": d["year"].astype(int), "office": "US PRESIDENT", "fips": _county_key(d),
                         "mode": d["mode"], "party": d["party"].str.upper(), "votes": pd.to_numeric(d["candidatevotes"], errors="coerce")})


def _precinct_zip(path: Path, year: int, offices: tuple[str, ...], by_candidate_party: bool = False) -> pd.DataFrame:
    z = zipfile.ZipFile(path)
    name = [n for n in z.namelist() if n.endswith(".csv") and "MACOSX" not in n][0]
    use = {"office", "stage", "special", "mode", "votes", "county_fips", "county_name", "party_simplified", "candidate", "county", "party"}
    parts = []
    for ch in pd.read_csv(z.open(name), usecols=lambda c: c in use, dtype=str, chunksize=500_000):
        for want, alt in (("county_name", "county"), ("party_simplified", "party"), ("county_fips", None), ("candidate", None)):
            if want not in ch:                       # older files (WI 2018) name these differently
                ch[want] = ch[alt] if alt in ch else np.nan
        ch = ch[ch["office"].isin(offices) & (ch["stage"].fillna("GEN").str.upper() == "GEN") & (ch["special"].fillna("FALSE").str.upper() != "TRUE")]
        if by_candidate_party:
            cand = ch["candidate"].astype(str).str.upper()
            party = np.where(cand.str.contains("HARRIS"), "DEMOCRAT", np.where(cand.str.contains("TRUMP"), "REPUBLICAN", "OTHER"))
        else:
            party = ch["party_simplified"].astype(str).str.upper()
        parts.append(pd.DataFrame({"office": ch["office"], "fips": _county_key(ch), "mode": ch["mode"], "party": party,
                                   "votes": pd.to_numeric(ch["votes"], errors="coerce")}).groupby(["office", "fips", "mode", "party"], dropna=False)["votes"].sum().reset_index())
    if not parts:
        return pd.DataFrame(columns=["office", "fips", "mode", "party", "votes"])
    return pd.concat(parts).groupby(["office", "fips", "mode", "party"], dropna=False)["votes"].sum().reset_index()


def precinct_modes(states: list[str]) -> pd.DataFrame:
    """MEDSL precinct files (2018 Governor / Senate, 2022 Governor / Senate, 2024 President) summed to county x mode x party,
    cached in PROC."""
    cache = PROC / "precinct_modes.csv.gz"
    if cache.exists():
        return pd.read_csv(cache, dtype={"fips": str, "mode": str})
    rows = []
    for st in states:
        s = st.lower()
        for year, f, offs, bycand in [(2018, RAW / "medsl18" / f"2018-{s}-precinct-general.zip", ("GOVERNOR", "US SENATE"), False),
                                      (2022, RAW / "medsl22" / f"2022-{s}-local-precinct-general.zip", ("GOVERNOR", "US SENATE"), False),
                                      (2024, RAW / "medsl24" / f"{s}24.zip", ("US PRESIDENT",), True)]:
            if f.exists():
                rows.append(_precinct_zip(f, year, offs, bycand).assign(state=st, year=year))
    out = pd.concat(rows, ignore_index=True)
    PROC.mkdir(parents=True, exist_ok=True)
    out.to_csv(cache, index=False)
    return out


def best_modes(cands: list[tuple[str, int, str, dict]]) -> dict[tuple[str, int], dict]:
    """cands: (state, year, race, table_row). One row per (state, year): highest coverage; ties -> president, governor, senate."""
    pref = {"US PRESIDENT": 0, "GOVERNOR": 1, "US SENATE": 2}
    best = {}
    for st, yr, race, row in cands:
        key = (st, yr)
        score = (-round(row["coverage"], 2), pref.get(race, 3))
        if key not in best or score < best[key][0]:
            best[key] = (score, {**row, "race": race})
    return {k: v[1] for k, v in best.items()}


# ------------------------------------------------------------------ build

def build() -> tuple[dict, dict]:
    fin, names = load_final()
    po_all = sorted(set(names.values()))
    states: dict[str, dict] = {st: {"cycles": [], "order": "", "gaps": {}, "gaps_by_cycle": {}, "modes": {}, "caveats": []} for st in po_all}
    detail: dict[tuple[str, int], dict] = {}

    # --- 2020: NYT county feed
    nyt = nyt_units(names)
    for st, g in nyt.groupby("st"):
        if (2020, st) not in fin:
            continue
        fd, fr = fin[(2020, st)]
        ser = count_series(g[["unit", "time", "d", "r"]])
        res = cycle_gaps(ser, fd, fr)
        last = g.sort_values("time").groupby("unit").tail(1)
        res["release"] = release_mix(last.assign(final=last["d"] + last["r"]).rename(columns={"absentee_method": "method"}))
        res["geo"] = geography(g[["unit", "time", "d", "r"]])
        detail[(st, 2020)] = res
    # --- 2024: state feed (series), county feed (geography)
    es = enr_state()
    ec = enr_county()
    for st, g in es.groupby("st"):
        if (2024, st) not in fin:
            continue
        fd, fr = fin[(2024, st)]
        res = cycle_gaps(count_series(g[["unit", "time", "d", "r"]]), fd, fr)
        cg = ec[ec["st"] == st]
        res["geo"] = geography(cg[["unit", "time", "d", "r"]]) if len(cg) else None
        detail[(st, 2024)] = res

    for (st, yr), res in sorted(detail.items()):
        s = states[st]
        s["cycles"].append(yr)
        s["gaps_by_cycle"][str(yr)] = {"gaps": res["gaps"], "hours": res["hours"], "blocks": res["blocks"],
                                       "final_margin": res["final_margin"], "feed_coverage": res["feed_coverage"]}
        if res.get("geo"):
            s["gaps_by_cycle"][str(yr)]["geo"] = res["geo"]
        if res.get("release"):
            s["gaps_by_cycle"][str(yr)]["release"] = res["release"]
        if res["feed_coverage"] is not None and res["feed_coverage"] < 0.97:
            s["caveats"].append(f"{yr}: the feed's last snapshot holds {res['feed_coverage']:.1%} of the certified two-party vote")
    # vote types
    cands = []
    cm = medsl_county_modes()
    for (yr, off), g in cm.groupby(["year", "office"]):
        for st, row in mode_table(g[["state", "fips", "mode", "party", "votes"]]).items():
            cands.append((st, int(yr), off, {**row, "src": "medsl_county"}))
    pm = precinct_modes(sorted(names.values()))
    for (yr, off), g in pm.groupby(["year", "office"]):
        for st, row in mode_table(g[["state", "fips", "mode", "party", "votes"]]).items():
            cands.append((st, int(yr), off, {**row, "src": "medsl_precinct"}))
    # presidential rows must add up to the certified two-party vote (+-3 %): a mode file that misses a county is not a vote-type table
    kept = []
    for st, yr, race, row in cands:
        if race == "US PRESIDENT" and (yr, st) in fin:
            ratio = row["votes"] / sum(fin[(yr, st)])
            row = {**row, "vs_certified": _r(ratio, 3)}
            if abs(ratio - 1) > 0.03:
                continue
        kept.append((st, yr, race, row))
    modes = best_modes(kept)
    ga_files = {2020: ("2020NovGen_pres.json", "US PRESIDENT"), 2022: ("2022NovGen_gov.json", "GOVERNOR"), 2024: ("2024NovGen_pres.json", "US PRESIDENT")}
    for yr, (f, race) in ga_files.items():
        p = RAW / "ga" / f
        if p.exists():
            row = parse_ga_modes(p.read_bytes())
            if row:
                chk = {"vs_certified": _r(row["votes"] / sum(fin[(yr, "GA")]), 3)} if race == "US PRESIDENT" and (yr, "GA") in fin else {}
                modes[("GA", yr)] = {**row, **chk, "race": race, "src": "ga_sos"}
    for (st, yr), row in sorted(modes.items()):
        if st in states:
            states[st]["modes"][str(yr)] = row
    for st, s in states.items():
        s["cycles"] = sorted(s["cycles"])
        s["gaps"] = summarise({y: s["gaps_by_cycle"][str(y)]["gaps"] for y in s["cycles"]})
        if not s["cycles"]:
            s["gaps"] = {}
        s["order"] = describe(st, s)
        if st in MODE_CAVEATS and s["modes"]:
            s["caveats"].append(MODE_CAVEATS[st])
        if not s["modes"]:
            s["caveats"].append("no vote-type table: the local certified files (MEDSL county / precinct, state SoS files) carry only TOTAL rows for this state")
        if 2020 in s["cycles"]:
            s["caveats"].append("2020 is a COVID cycle: far more mail voting, and a strongly partisan split of mail vs in-person ballots")
        miss = [y for y in (2020, 2024) if y not in s["cycles"]]
        if miss and s["cycles"]:
            s["caveats"].append(f"no timed feed for {', '.join(map(str, miss))}")
    for st in po_all:
        states[st]["caveats"] = list(dict.fromkeys(states[st]["caveats"]))
    not_avail = {st: "no timed count series and no vote-type table in the local sources" for st, s in states.items()
                 if not s["cycles"] and not s["modes"]}
    for st in not_avail:
        del states[st]
    cov = {"timed": {str(y): sorted(st for st, s in states.items() if y in s["cycles"]) for y in (2020, 2024)},
           "modes": {str(y): sorted(st for st, s in states.items() if str(y) in s["modes"]) for y in (2018, 2020, 2022, 2024)}}
    cov["timed_missing"] = {y: sorted(set(po_all) - set(v)) for y, v in cov["timed"].items()}
    cov["modes_missing"] = {y: sorted(set(po_all) - set(v)) for y, v in cov["modes"].items()}
    cov["missing_parts"] = {st.lower(): {"timed": [y for y in (2020, 2024) if y not in s["cycles"]],
                                        "modes": "none in 2018-2024" if not s["modes"] else sorted(s["modes"])}
                           for st, s in states.items() if not s["modes"] or len(s["cycles"]) < 2}
    cov["cycles_without_timed_data"] = ["2016", "2018", "2022 (no archived count feed in the local data)"]
    out = {"built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "note": ("Descriptive only: how raw count margins related to final certified margins in past cycles. It is not an input "
                    "of any registered 2026 model or hypothesis. Margins are two-party D minus R in points; gap = raw count margin "
                    "minus final certified margin at 10/25/50/75 % of the final two-party vote counted (positive = early count more "
                    "Democratic, negative = more Republican). Cycles are 2020 and 2024 presidential only; n is the number of cycles."),
           "sources": SOURCES, "not_downloaded": NOT_DOWNLOADED, "states": {st.lower(): s for st, s in sorted(states.items())},
           "not_available": {st.lower(): v for st, v in sorted(not_avail.items())}, "coverage": cov}
    return out, {"detail": detail}


def describe(st: str, s: dict) -> str:
    """One-sentence count order from the data: sign class, mean gap at 10/25/50/75, 2020 absentee release method, which
    units report first."""
    if not s["cycles"]:
        return "no timed count series; vote-type table only"
    by = {y: s["gaps_by_cycle"][str(y)]["gaps"] for y in s["cycles"]}
    parts = [label(by)]
    g = s["gaps"]
    if g.get("25", {}).get("mean") is not None:
        parts.append("mean gap at 10/25/50/75 % counted: " + " / ".join(
            "n/a" if g[str(q)]["mean"] is None else f"{g[str(q)]['mean']:+.1f}" for q in QS) + " pts")
    rel = s["gaps_by_cycle"].get("2020", {}).get("release")
    if rel:
        top = max(rel, key=rel.get)
        if top in RELEASE_TEXT:
            parts.append(f"2020: {RELEASE_TEXT[top]} ({rel[top]:.0%} of the vote)")
    geo = [(y, s["gaps_by_cycle"][str(y)].get("geo")) for y in s["cycles"]]
    geo = [(y, x) for y, x in geo if x and x["big_at_q"] is not None]
    if geo:
        d = np.mean([x["big_at_q"] - x["big_final"] for _, x in geo])
        parts.append("largest units report " + ("early" if d > 0.03 else "late" if d < -0.03 else "in proportion") +
                     f" (their share of the first 25 % counted minus their final share: {100 * d:+.0f} pts)")
    return "; ".join(parts)


# ------------------------------------------------------------------ reporting

def fmt(x, nd=1) -> str:
    return "–" if x is None else f"{x:+.{nd}f}"


def gap_table(out: dict) -> str:
    lines = ["| state | cycles | 10 % | 25 % | 50 % | 75 % | count order |", "|---|---|---|---|---|---|---|"]
    for st, s in out["states"].items():
        if not s["cycles"]:
            continue
        cells = []
        for q in QS:
            g = s["gaps"][str(q)]
            cells.append("–" if g["mean"] is None else f"{g['mean']:+.1f} ({g['min']:+.1f} … {g['max']:+.1f}), n={g['n']}")
        lines.append(f"| {st.upper()} | {'/'.join(map(str, s['cycles']))} | " + " | ".join(cells) + f" | {s['order'].split(';')[0]} |")
    return "\n".join(lines)


def mode_table_md(out: dict) -> str:
    lines = ["| state | year (race) | early share / margin | mail share / margin | Election Day share / margin | other share / margin | coverage | source |",
             "|---|---|---|---|---|---|---|---|"]
    for st, s in out["states"].items():
        for y, m in sorted(s["modes"].items()):
            def c(k):
                return "–" if k not in m else f"{m[k]['share']:.0%} / {m[k]['margin']:+.1f}"
            lines.append(f"| {st.upper()} | {y} ({m['race'].lower()}) | {c('early')} | {c('mail')} | {c('eday')} | {c('other')} | {m['coverage']:.0%} | {m['src']} |")
    return "\n".join(lines)


def georgia_paragraph(out: dict) -> str:
    """Georgia as the vote-type example: certified early / mail / Election Day margins next to the count gaps of the same cycle."""
    ga = out["states"].get("ga")
    if not ga or not ga["modes"]:
        return ""
    bits = []
    for y, m in sorted(ga["modes"].items()):
        parts = ", ".join(f"{k} {m[k]['share']:.0%} of votes at {m[k]['margin']:+.1f}" for k in ("early", "mail", "eday") if k in m)
        g = ga["gaps_by_cycle"].get(y, {}).get("gaps")
        gap = f"; count gap at 25 % / 50 % counted {g['25']:+.1f} / {g['50']:+.1f}" if g and "25" in g and "50" in g else ""
        bits.append(f"- {y} ({m['race'].lower()}): {parts}{gap}")
    return ("### Georgia: vote types against the count\n\nCertified Georgia SoS results by vote type (advance voting = early, absentee by mail, Election Day; "
            "margins D - R in points):\n\n" + "\n".join(bits) +
            "\n\nIn every cycle the mail ballots are the most Democratic vote type and Election Day the most Republican. "
            "So a count that reports Election Day and advance votes before the mail ballots runs more Republican than the final result, a count that adds mail early "
            "narrows that; the sign and size of the gap in the table depend on when each county added its mail ballots (the 2020 order is in `gaps_by_cycle.2020.release`). "
            "That link is an inference from the two tables; the feeds do not label which vote types a snapshot contains.\n\n")


def markdown(out: dict) -> str:
    cov = out["coverage"]
    miss = lambda d: ", ".join(s.upper() for s in d) or "none"
    return f"""## Count-night tells: raw count margin vs final margin (descriptive; informs a later model version, not 2026's)

**What this is.** For each state, how far the two-party D - R margin of the votes counted so far sat from the final certified margin when 10, 25, 50 and 75 % of the final two-party vote was counted, and which vote types reported when. It is a description of 2020 and 2024; it feeds no registered 2026 configuration or hypothesis.

**Method.** `analysis/us_ev_tells.py` builds a statewide cumulative D / R count series from the county (2020) or state (2024) count feeds, carrying each county's last value forward and dropping observations that lower a county's running maximum (feed glitches). "% counted" is counted two-party votes / certified two-party votes (MEDSL certified state totals). The raw margin at q % is interpolated linearly in votes between the two snapshots around q. gap(q) = raw margin - final margin, in points of D - R; positive means the early count was more Democratic than the final result ("blue mirage", later red shift), negative more Republican ("red mirage", later blue shift). Cells show the mean over the available cycles, (min … max) and the number of cycles n (at most 2). Classification: mean gap at 25 and 50 % beyond +-2 points. All numbers are in `site/data/us2026/tells.json` (per cycle in `gaps_by_cycle`, with hours since the first reported vote, margins of each 25 % count block, and the feed's coverage of the certified vote).

### Gap table (raw count margin minus final margin, points of D - R)

{gap_table(out)}

### Vote types: share of the two-party vote and D - R margin by mode of voting (certified)

Share = mode's share of the classified two-party vote; margin = two-party D - R in points. Early = in-person early / advance / one-stop; mail = absentee by mail; other = provisional and similar. `coverage` = share of the state's two-party vote that sits in counties with a mode breakdown (rows below 90 % are not reported).

{mode_table_md(out)}

{georgia_paragraph(out)}### Coverage

- Timed 2020 (NYT/Edison county feed): {len(cov['timed']['2020'])} states + DC; missing: {miss(cov['timed_missing']['2020'])}.
- Timed 2024 (CNN/Edison state feed): {len(cov['timed']['2024'])} states; missing: {miss(cov['timed_missing']['2024'])}.
- No timed count data for 2016, 2018 or 2022 in the local sources.
- Vote-type tables: 2018 {len(cov['modes']['2018'])} states, 2020 {len(cov['modes']['2020'])}, 2022 {len(cov['modes']['2022'])}, 2024 {len(cov['modes']['2024'])}; states without a table in 2020: {miss(cov['modes_missing']['2020'])}; in 2024: {miss(cov['modes_missing']['2024'])}.
- States with neither: {miss(list(out['not_available']))}.

### Caveats

- **2020 is a COVID cycle.** Mail voting was several times its usual share and very Democratic, so the 2020 gaps mostly reflect when each state counted mail ballots; 2024 is closer to a normal presidential cycle. With only two cycles, mean and range are not a forecast; where the cycles disagree the state is labelled cycle-dependent.
- **State reporting rules differ.** Some states may process mail ballots before Election Day (reported first or merged into precinct totals); others count them last or after Election Day (2020 release methods are in `gaps_by_cycle.2020.release`, from the NYT feed). Counting order inside a state also changed between 2020 and 2024 in several states (e.g. early processing laws).
- **Timed series are county granularity.** A feed snapshot shows what a county had reported, not which vote types; the vote-type tables come from certified returns and say nothing about timing. 2024 county feed overcounts relative to certified (MEDSL README); it is used only for the geography columns, the 2024 gaps use the state feed. New England, Alaska and Louisiana-style units are towns / districts, not counties. "Large units" = top fifth of units by final vote.
- **Feeds are unofficial.** Counts are news-agency feeds (NYT/Edison, CNN/Edison), not official counts; the 2024 state feed stopped on 2024-12-03 (Alaska and New York are at 95-96 % of the certified vote then) and DC is not in it.
- **Vote-type labels differ by state** (MEDSL labels mapped to early / mail / Election Day / other; per-state notes in `caveats`). Iowa and Virginia report one absentee row that includes in-person early voting, Arizona one early row that includes mail, Nebraska 2018 one combined absentee/early row. Presidential tables add up to the certified two-party vote within 3 % (`vs_certified` in tells.json); midterm tables have no certified reference in the local data and rely on the county totals. Files that put every vote into one mode (e.g. West Virginia, Kentucky 2020) are not shown.
- **Most states have no vote-type table.** MEDSL's county and precinct files carry only TOTAL rows for them and no other local source splits certified votes by mode; the NYT 2020 feed's absentee columns exist for a subset of counties only and mean different things by state (Georgia's include advance voting), so they are not used.
- **Georgia** is the full example: the SoS Enhanced Voting files split 2020 / 2022 / 2024 results into advance voting, mail and Election Day statewide and by county.

### Sources and licences

""" + "\n".join(f"- {s['what']}: `{s['file']}`, {s['doi']}, {s['licence']}" for s in out["sources"]) + """
- Not downloaded / not used: """ + "; ".join(f"{k} ({v})" for k, v in out["not_downloaded"].items()) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--md", help="also write the documentation fragment to this path")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    out, _ = build()
    txt = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(txt)
    print(gap_table(out), "\n")
    print(mode_table_md(out), "\n")
    print(f"states with data: {len(out['states'])}; not available: {sorted(out['not_available'])}; tells.json {len(txt) / 1024:.0f} kB")
    if a.md:
        Path(a.md).write_text(markdown(out))


if __name__ == "__main__":
    main()
