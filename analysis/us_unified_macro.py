"""National environment panel by election cycle for the unified pre-count US model (owner: UniMacro).

    uv run python analysis/us_unified_macro.py fetch   # cached downloads under $BRUNDER_DATA/raw/us/{polls,macro/hist}
    uv run python analysis/us_unified_macro.py build   # writes $BRUNDER_DATA/processed/us2026/unified/{nat_panel,nat_series,midterm_fundamentals}.csv

Cycles 2006..2024 (even) + 2026 (forecast). Everything is evaluated "as of" T = Election Day - 4 days using only data
dated <= T (poll end dates, additionally created_at/publication <= T where the source has it; sources without a
publication date get a 2-day publication lag). Macro series come from ALFRED real-time vintages dated T.
Realised outcomes (house_d2_nat, pres_d2_nat, ...) are targets/context and are NOT as-of features.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from brunder.us import POLL_WINDOW_DAYS, poll_weights

DATA = Path(os.environ.get("BRUNDER_DATA", Path.home() / "brunder-data-local")).expanduser()
RAW = DATA / "raw" / "us"
HIST = RAW / "macro" / "hist"
POLLS = RAW / "polls"
U = DATA / "processed" / "us2026" / "unified"
UA = "brunder-research/0.1 (academic research; polite single requests)"
FRESH = "20261031"          # vintage tag of the VoteHub downloads made by `fetch`
CYCLES = list(range(2006, 2027, 2))
PUB_LAG = 2                 # days: polls without a publication date must have ended <= T - PUB_LAG
APP_KEY = "1iEl565M1mICTubTtoxXMdxzaHzAcPTnb3kpRndsrfyY"   # American Presidency Project / Gallup sheet
APP_GID = {"Obama": "0", "Trump1": "671375968", "Bush43": "1067240630", "Clinton": "148211037",
           "Bush41": "1868734071", "Reagan": "1957483948", "Carter": "566304079"}
# party of the President (+1 D, -1 R) by election year; name for the Gallup/APP sheet
PRES = {1978: ("Carter", 1), 1982: ("Reagan", -1), 1986: ("Reagan", -1), 1990: ("Bush41", -1), 1994: ("Clinton", 1),
        1998: ("Clinton", 1), 2002: ("Bush43", -1), 2006: ("Bush43", -1), 2008: ("Bush43", -1), 2010: ("Obama", 1),
        2012: ("Obama", 1), 2014: ("Obama", 1), 2016: ("Obama", 1), 2018: ("Trump1", -1), 2020: ("Trump1", -1),
        2022: ("Biden", 1), 2024: ("Biden", 1), 2026: ("Trump2", -1)}
ALFRED = {"umcsent": "UMCSENT", "unrate": "UNRATE", "dpi": "A229RX0", "cpi": "CPIAUCSL"}

# Ballotpedia generic-ballot index page of 2026-10-30 (poll list transcribed: pollster, field end, D %, R %, n, pop).
# Facts only; used for the 2026 cross-check gb_avg_bp because VoteHub's newest generic-ballot poll ends 2026-10-04.
BP2026 = [("Reuters/Ipsos", "2026-10-26", 53, 44, 1164, "lv"), ("Harvard-Harris", "2026-10-26", 52, 48, 2700, "rv"),
          ("Economist/YouGov", "2026-10-26", 50, 41, 1263, "lv"), ("Emerson College", "2026-10-26", 52, 42, 1000, "lv"),
          ("Morning Consult", "2026-10-25", 50, 43, 17491, "lv"), ("Cygnal", "2026-10-24", 52, 43, 1500, "lv"),
          ("CBS News", "2026-10-23", 53, 45, 2355, "a"), ("Rasmussen Reports", "2026-10-21", 48, 43, 1755, "lv"),
          ("Financial Times", "2026-10-20", 53, 44, 1594, "lv"), ("Marist", "2026-10-20", 52, 42, 1289, "rv"),
          ("Echelon Insights", "2026-10-19", 50, 45, 1010, "lv"), ("NY Times/Siena", "2026-10-19", 52, 41, 3494, "lv"),
          ("CNBC", "2026-10-19", 52, 45, 1000, "rv"), ("CNN", "2026-10-18", 49, 39, 1206, "rv"),
          ("ABC/Washington Post/Ipsos", "2026-10-13", 52, 44, 2032, "rv"), ("Fox News", "2026-10-12", 54, 45, 1202, "rv"),
          ("NBC News Decision Desk", "2026-10-12", 55, 45, 17649, "rv"), ("NewsNation", "2026-10-09", 49, 42, 974, "lv"),
          ("Pew Research", "2026-10-04", 44, 36, 3917, "rv")]


SEAT_NOTE = {2006: "MEDSL plurality winners give D 231 / R 204; official 233 / 202 (Dec-2006 Texas jungle runoffs; MEDSL has no runoff rows)",
             1996: "Texas runoff rows partly absent; not used in the midterm table"}


def election_day(y: int) -> date:
    d = date(y, 11, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d + timedelta(days=1)


def T_of(y: int) -> pd.Timestamp:
    return pd.Timestamp(election_day(y) - timedelta(days=4))


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _cache(path: Path, url: str, sleep: float = 1.0) -> Path:
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(url))
        time.sleep(sleep)
    return path


# ------------------------------------------------------------------ fetch
def fetch() -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    for sid in ("UMCSENT", "UNRATE", "CPIAUCSL", "A229RX0"):
        _cache(HIST / f"fred_{sid}.csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
    for y in CYCLES:                                    # ALFRED real-time vintages dated T (keyless graph CSV)
        t = T_of(y).strftime("%Y-%m-%d")
        for sid in list(ALFRED.values()) + ["DSPIC96", "POPTHM"]:
            if sid in ("DSPIC96", "POPTHM") and y >= 2010:      # A229RX0 has ALFRED vintages only from ~2010
                continue
            try:
                _cache(HIST / "alfred" / f"{sid}_{t}.csv",
                       f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&vintage_date={t}")
            except urllib.error.HTTPError as e:
                if sid != "A229RX0" or e.code != 404:
                    raise
    for nm, gid in APP_GID.items():
        _cache(HIST / f"app_gallup_{nm}.csv",
               f"https://docs.google.com/spreadsheets/d/{APP_KEY}/export?format=csv&gid={gid}")
    _cache(POLLS / f"votehub_generic-ballot_{FRESH}.json", "https://api.votehub.com/polls?poll_type=generic-ballot&subject=2026")
    _cache(HIST / f"votehub_approval_trump_{FRESH}.json",
           "https://api.votehub.com/polls?poll_type=approval&subject=Donald%20Trump&limit=5000")
    _cache(POLLS / "538_generic_topline_historical.csv",
           "https://raw.githubusercontent.com/fivethirtyeight/data/master/congress-generic-ballot/generic_topline_historical.csv")
    for yr, title in ((2022, "2022 opinion polling on the Biden administration"),
                      (2024, "2024–2025 opinion polling on the Biden administration")):
        q = urllib.parse.urlencode(dict(action="parse", page=title, prop="wikitext", format="json", redirects=1))
        _cache(HIST / f"wiki_biden_{yr}.json", f"https://en.wikipedia.org/w/api.php?{q}", sleep=2)
    print("fetched ->", HIST)


# ------------------------------------------------------------------ polls
def _agg(rows: pd.DataFrame, asof: pd.Timestamp) -> dict:
    """Weighted average (brunder.us.poll_weights rule) of rows[end, n, s, pollster] as of `asof`."""
    if rows.empty:
        return dict(avg=np.nan, n=0, last=pd.NaT)
    r = poll_weights(rows, asof.strftime("%Y-%m-%d"))
    if r is None or r.empty:
        return dict(avg=np.nan, n=0, last=pd.NaT)
    return dict(avg=float((r["weight"] * r["s"]).sum()), n=int(len(r)), last=pd.Timestamp(r["end"].max()))


def _pref(df: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """One row per (pollster, start, end): the preferred population."""
    rank = {p: i for i, p in enumerate(order)}
    df = df.assign(_r=df["pop"].map(rank).fillna(9)).sort_values("_r")
    return df.drop_duplicates(["pollster", "start", "end"]).drop(columns="_r")


def gb_polls_538(cycle: int) -> pd.DataFrame:
    d = pd.read_csv(POLLS / "538_generic_ballot_polls_historical.csv", low_memory=False)
    d = d[(d.cycle == cycle) & (d.office_type == "U.S. House")].copy()
    d["end"] = pd.to_datetime(d["end_date"], format="%m/%d/%y")
    d["start"] = pd.to_datetime(d["start_date"], format="%m/%d/%y")
    d["pub"] = pd.to_datetime(d["created_at"], format="%m/%d/%y %H:%M").dt.normalize()
    out = pd.DataFrame({"pollster": d["pollster"], "start": d["start"], "end": d["end"], "pub": d["pub"],
                        "n": d["sample_size"].astype(float), "dem": d["dem"], "opp": d["rep"], "pop": d["population"]})
    out = out.dropna(subset=["dem", "opp", "n"])
    out["s"] = out["dem"] / (out["dem"] + out["opp"])
    return _pref(out, ["lv", "rv", "v", "a"])


def gb_polls_votehub() -> pd.DataFrame:
    d = json.load(open(POLLS / f"votehub_generic-ballot_{FRESH}.json"))
    rows = []
    for p in d:
        a = {x["choice"]: x["pct"] for x in p["answers"]}
        if "Dem" in a and "Rep" in a:
            rows.append(dict(pollster=p["pollster"], start=p["start_date"], end=p["end_date"], pub=p["created_at"],
                             n=p["sample_size"], dem=a["Dem"], opp=a["Rep"], pop=p["population"] or "a"))
    out = pd.DataFrame(rows)
    for c in ("start", "end", "pub"):
        out[c] = pd.to_datetime(out[c])
    out["n"] = out["n"].astype(float)
    out["s"] = out["dem"] / (out["dem"] + out["opp"])
    return _pref(out.dropna(subset=["n"]), ["lv", "rv", "a"])


def gb_polls_bp() -> pd.DataFrame:
    out = pd.DataFrame(BP2026, columns=["pollster", "end", "dem", "opp", "n", "pop"])
    out["end"] = pd.to_datetime(out["end"])
    out["pub"] = out["end"]
    out["n"] = out["n"].astype(float)
    out["s"] = out["dem"] / (out["dem"] + out["opp"])
    return out


def avail(polls: pd.DataFrame, T: pd.Timestamp) -> pd.DataFrame:
    return polls[(polls["end"] <= T) & (polls["pub"] <= T)]


def gb_topline() -> pd.Series:
    d = pd.read_csv(POLLS / "538_generic_topline_historical.csv")
    d["dt"] = pd.to_datetime(d["modeldate"], format="%m/%d/%Y")
    return d.assign(s=d.dem_estimate / (d.dem_estimate + d.rep_estimate)).set_index("dt")["s"]


def gb_at(cycle: int, T: pd.Timestamp, cache: dict) -> dict:
    if cycle <= 2016:
        s = cache.setdefault("top", gb_topline())
        if T not in s.index:
            return dict(avg=np.nan, n=np.nan, last=pd.NaT, src="538 topline (historical)")
        return dict(avg=float(s[T]), n=np.nan, last=T, src="538 topline (historical)")
    if cycle == 2026:
        p = cache.setdefault("vh", gb_polls_votehub())
        r = _agg(avail(p, T), T)
        return {**r, "src": f"VoteHub API {FRESH}"}
    p = cache.setdefault(f"p{cycle}", gb_polls_538(cycle))
    return {**_agg(avail(p, T), T), "src": "538 polls_historical"}


# ------------------------------------------------------------------ approval
def gallup(name: str) -> pd.DataFrame:
    d = pd.read_csv(HIST / f"app_gallup_{name}.csv")
    out = pd.DataFrame({"pollster": "Gallup", "start": pd.to_datetime(d["Start Date"], format="mixed"),
                        "end": pd.to_datetime(d["End Date"], format="mixed"), "a": d["Approving"].astype(float),
                        "dis": d["Disapproving"].astype(float)})
    out["s"] = out["a"]
    out["n"] = 1.0                                   # APP sheets carry no sample size: weight = 0.5^(age/14) only
    out["pub"] = out["end"] + pd.Timedelta(days=PUB_LAG)
    return out


def _clean(c: str) -> str:
    c = re.sub(r"<ref[^>]*/>|<ref.*?</ref>|<!--.*?-->", "", c, flags=re.S)
    prev = None
    while prev != c:
        prev, c = c, re.sub(r"\{\{[^{}]*\}\}", "", c)
    c = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", c)
    c = re.sub(r"<[^>]+>", "", c).replace("''", "")
    if "|" in c:
        c = c.split("|")[-1]
    return c.strip()


MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}


def _wdate(txt: str, year: int) -> pd.Timestamp | None:
    t = txt.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    parts = [p.strip() for p in t.split("-")]
    if not parts or not parts[-1]:
        return None
    m = re.match(r"(?:([A-Za-z]+)\.?\s+)?(\d{1,2})(?:,?\s*(\d{4}))?", parts[-1])
    if not m:
        return None
    mon, day, yr = m.group(1), int(m.group(2)), m.group(3)
    if mon is None:
        m0 = re.match(r"([A-Za-z]+)", parts[0])
        mon = m0.group(1) if m0 else None
    if mon is None or mon.lower()[:3] not in [k[:3] for k in MONTHS]:
        return None
    mi = [i + 1 for i, k in enumerate(MONTHS) if k[:3] == mon.lower()[:3]][0]
    try:
        return pd.Timestamp(int(yr) if yr else year, mi, day)
    except ValueError:
        return None


def wiki_biden(year: int) -> pd.DataFrame:
    w = json.load(open(HIST / f"wiki_biden_{year}.json"))["parse"]["wikitext"]["*"]
    rows = []
    for blk in w.split("\n|-")[1:]:
        cells = []
        for ln in blk.split("\n"):
            if ln.startswith("|") and not ln.startswith(("|}", "|+", "|-")):
                cells.append(_clean(ln[1:]))
        if len(cells) < 5:
            continue
        try:
            a, dis = float(cells[2].rstrip("%")), float(cells[3].rstrip("%"))
        except ValueError:
            continue
        end = _wdate(cells[1], year)
        if end is None:
            continue
        try:
            n = float(cells[5].replace(",", ""))
        except (ValueError, IndexError):
            n = 1000.0
        pol = re.split(r"\s*\(for", cells[0])[0].strip()
        rows.append(dict(pollster=pol, end=end, start=end, a=a, dis=dis, n=n))
    d = pd.DataFrame(rows)
    d["s"] = d["a"]
    d["pub"] = d["end"] + pd.Timedelta(days=PUB_LAG)
    return d


def votehub_trump() -> pd.DataFrame:
    d = json.load(open(HIST / f"votehub_approval_trump_{FRESH}.json"))
    rows = []
    for p in d:
        a = {x["choice"]: x["pct"] for x in p["answers"]}
        if "Approve" in a and "Disapprove" in a:
            rows.append(dict(pollster=p["pollster"], start=p["start_date"], end=p["end_date"], pub=p["created_at"],
                             n=p["sample_size"], a=a["Approve"], dis=a["Disapprove"], pop=p["population"] or "a"))
    out = pd.DataFrame(rows)
    for c in ("start", "end", "pub"):
        out[c] = pd.to_datetime(out[c])
    out["n"] = out["n"].astype(float)
    out["s"] = out["a"]
    return _pref(out.dropna(subset=["n"]), ["a", "rv", "lv"])


def approve_at(year: int, T: pd.Timestamp, cache: dict) -> dict:
    name = PRES[year][0]
    if name in APP_GID or name == "Trump1":
        p = cache.setdefault(name, gallup(name)); src = "Gallup via American Presidency Project"
    elif name == "Biden":
        p = cache.setdefault(f"biden{year}", wiki_biden(year)); src = "Wikipedia poll tables (multi-pollster)"
    else:
        p = cache.setdefault("trump2", votehub_trump()); src = f"VoteHub API {FRESH} (multi-pollster)"
    r = poll_weights(avail(p, T), T.strftime("%Y-%m-%d"))
    if r is None or r.empty:
        return dict(approve=np.nan, disapprove=np.nan, n=0, last=pd.NaT, src=src)
    w = r["weight"]
    return dict(approve=float((w * r["a"]).sum()), disapprove=float((w * r["dis"]).sum()), n=int(len(r)),
                last=pd.Timestamp(r["end"].max()), src=src)


# ------------------------------------------------------------------ macro (ALFRED vintages)
def alfred(sid: str, T: pd.Timestamp) -> pd.Series:
    f = HIST / "alfred" / f"{sid}_{T:%Y-%m-%d}.csv"
    d = pd.read_csv(f, na_values=["."])
    s = pd.Series(pd.to_numeric(d.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(d.iloc[:, 0]))
    return s.dropna()


def macro_at(T: pd.Timestamp) -> tuple[dict, list]:
    out, ser = {}, []
    for k, sid in ALFRED.items():
        if k == "dpi" and not (HIST / "alfred" / f"{sid}_{T:%Y-%m-%d}.csv").exists():
            s = (alfred("DSPIC96", T) / alfred("POPTHM", T)).dropna()      # real DPI / population, same vintage
            out["dpi_src"] = "ALFRED DSPIC96/POPTHM (A229RX0 has no vintage before 2010)"
        else:
            s = alfred(sid, T)
            if k == "dpi":
                out["dpi_src"] = "ALFRED A229RX0"
        last = s.index[-1]
        prev = last - pd.DateOffset(years=1)
        out[f"{k}_asof"] = last.strftime("%Y-%m-%d")
        if k == "umcsent":
            out.update(umcsent=s[last], d_umcsent12=s[last] - s[prev])
        elif k == "unrate":
            out.update(unrate=s[last], d_unrate12=s[last] - s[prev])
        elif k == "dpi":
            out.update(dpi_g12=100 * (s[last] / s[prev] - 1))
        else:
            out.update(cpi_12=100 * (s[last] / s[prev] - 1))
        for dt, v in s[s.index >= last - pd.DateOffset(months=14)].items():
            ser.append((sid, dt, v))
    return out, ser


# ------------------------------------------------------------------ outcomes
DEM = {"DEMOCRAT", "DEMOCRATIC", "DEMOCRATIC-FARMER-LABOR", "DEMOCRATIC-FARM-LABOR", "DEMOCRATIC-NPL"}
REP = {"REPUBLICAN"}


def house_outcomes() -> pd.DataFrame:
    """Per year: D/R seats by plurality, two-party votes (contested districts / all districts)."""
    h = pd.read_csv(RAW / "medsl_hist" / "1976-2024-house.tab", low_memory=False)
    # special elections are dropped except Texas-2006 (Nov jungle general, flagged special by MEDSL); DC delegate excluded
    h = h[(h.stage == "GEN") & ((h.special == False) | ((h.year == 2006) & (h.state_po == "TX")))  # noqa: E712
          & (h.writein == False) & (h.runoff != True) & (h.state_po != "DC")].copy()  # noqa: E712
    h["district"] = h["district"].astype(str)
    h["cls"] = np.where(h.party.isin(DEM), "D", np.where(h.party.isin(REP), "R", "O"))
    key = ["year", "state_po", "district"]
    # fusion lines: a candidate's votes are summed over all lines; the candidate takes the D/R line if it has one
    cand = h.groupby(key + ["candidate"]).agg(v=("candidatevotes", "sum"),
                                              isD=("cls", lambda s: (s == "D").any()),
                                              isR=("cls", lambda s: (s == "R").any())).reset_index()
    cand["cls"] = np.where(cand.isD, "D", np.where(cand.isR, "R", "O"))
    cand = cand[~cand.candidate.isin(["BLANK", "VOID", "SCATTERING", "WRITEIN", "OVER VOTES", "UNDER VOTES", "EXHAUSTED BALLOT", "EXHAUSTED BALLOTS"])]
    dist = cand.pivot_table(index=key, columns="cls", values="v", aggfunc="sum", fill_value=0).reset_index()
    for c in "DRO":
        if c not in dist:
            dist[c] = 0
    top = cand.sort_values("v", ascending=False).drop_duplicates(key)[key + ["cls"]].rename(columns={"cls": "win"})
    dist = dist.merge(top, on=key)
    dist["contested"] = (dist.D > 0) & (dist.R > 0)
    g = dist.groupby("year")
    out = pd.DataFrame({
        "d_seats": g.apply(lambda x: int((x.win == "D").sum())), "r_seats": g.apply(lambda x: int((x.win == "R").sum())),
        "o_seats": g.apply(lambda x: int((x.win == "O").sum())), "n_districts": g.size(),
        "n_uncontested": g.apply(lambda x: int((~x.contested).sum())),
        "house_d2_nat": g.apply(lambda x: x.loc[x.contested, "D"].sum() / x.loc[x.contested, ["D", "R"]].sum().sum()),
        "house_votes_contested": g.apply(lambda x: float(x.loc[x.contested, ["D", "R"]].sum().sum())),
        "house_d2_all": g.apply(lambda x: x.D.sum() / (x.D.sum() + x.R.sum())),
        "house_votes": g.apply(lambda x: float(x.D.sum() + x.R.sum())),
        "house_votes_all_cands": g.apply(lambda x: float(x.D.sum() + x.R.sum() + x.O.sum())),
    })
    return out


def pres_outcomes() -> pd.DataFrame:
    p = pd.read_csv(RAW / "medsl_hist" / "1976-2024-president-state.csv")
    p = p[p.writein == False]  # noqa: E712
    g = p.groupby(["year", "party_simplified"]).candidatevotes.sum().unstack().fillna(0)
    out = pd.DataFrame({"pres_d2_nat": g.DEMOCRAT / (g.DEMOCRAT + g.REPUBLICAN), "pres_votes": g.DEMOCRAT + g.REPUBLICAN,
                        "pres_votes_all": g.sum(axis=1)})
    return out


# ------------------------------------------------------------------ build
def build() -> None:
    U.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    ho, po = house_outcomes(), pres_outcomes()
    rows, series = [], []
    for y in CYCLES:
        T = T_of(y)
        r = dict(cycle=y, election_day=election_day(y).isoformat(), asof=T.strftime("%Y-%m-%d"),
                 pres_party=PRES[y][1])
        g, g30 = gb_at(y, T, cache), gb_at(y, T - pd.Timedelta(days=30), cache)
        r.update(gb_avg=g["avg"], gb_n=g["n"], gb_asof=g["last"], gb_avg_m30=g30["avg"], gb_n_m30=g30["n"],
                 gb_m30_asof=g30["last"], gb_src=g["src"])
        a, a30 = approve_at(y, T, cache), approve_at(y, T - pd.Timedelta(days=30), cache)
        r.update(approve=a["approve"], disapprove=a["disapprove"], net_approve=a["approve"] - a["disapprove"],
                 net_approve_d=(a["approve"] - a["disapprove"]) * PRES[y][1], approve_n=a["n"], approve_asof=a["last"],
                 net_approve_m30=a30["approve"] - a30["disapprove"], approve_src=a["src"])
        m, ser = macro_at(T)
        r.update(m)
        for sid, dt, v in ser:
            series.append(dict(series=sid, cycle=y, kind="macro_vintage_T", date=dt.strftime("%Y-%m-%d"), value=v))
        # daily traces of the two polling averages over the 60 days to T (same as-of rule at each day)
        for k in range(61):
            t = T - pd.Timedelta(days=60 - k)
            gg, aa = gb_at(y, t, cache), approve_at(y, t, cache)
            series.append(dict(series="gb_avg", cycle=y, kind="daily_asof", date=t.strftime("%Y-%m-%d"), value=gg["avg"]))
            series.append(dict(series="net_approve", cycle=y, kind="daily_asof", date=t.strftime("%Y-%m-%d"),
                               value=aa["approve"] - aa["disapprove"]))
        if y == 2026:
            bp = gb_polls_bp()
            b = _agg(bp[bp["end"] <= T], T)
            r.update(gb_avg_bp=b["avg"], gb_n_bp=b["n"], gb_bp_asof=b["last"])
        if y in ho.index:
            r.update({k: ho.loc[y, k] for k in ("house_d2_nat", "house_votes", "house_d2_all", "house_votes_contested",
                                                "house_votes_all_cands", "n_uncontested", "d_seats", "r_seats")})
        if y in po.index:
            r.update({k: po.loc[y, k] for k in ("pres_d2_nat", "pres_votes", "pres_votes_all")})
        lp = [q for q in po.index if q < y]
        if lp:
            r["pres_d2_last"] = po.loc[lp[-1], "pres_d2_nat"]
        rows.append(r)
    P = pd.DataFrame(rows)
    first = ["cycle", "pres_party", "gb_avg", "gb_n", "gb_avg_m30", "approve", "disapprove", "net_approve", "umcsent",
             "d_umcsent12", "unrate", "d_unrate12", "dpi_g12", "cpi_12", "house_d2_nat", "pres_d2_nat", "house_votes",
             "pres_votes"]
    P = P[first + [c for c in P.columns if c not in first]]
    for c in P.columns:
        if c.endswith("asof") and c not in ("asof",):
            P[c] = pd.to_datetime(P[c]).dt.strftime("%Y-%m-%d")
    P.to_csv(U / "nat_panel.csv", index=False)
    S = pd.DataFrame(series)
    # the monthly macro rows repeat per vintage on purpose (ALFRED vintage T of the cycle)
    S.to_csv(U / "nat_series.csv", index=False)
    midterms(ho, po, cache)
    print(P.T.to_string())


def midterms(ho: pd.DataFrame, po: pd.DataFrame, cache: dict) -> None:
    rows = []
    top = gb_topline()
    for y in [1978, 1982, 1986, 1990, 1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022, 2026]:
        T = T_of(y)
        name, party = PRES[y]
        prev = y - 2
        r = dict(year=y, president=name, pres_party=party, asof=T.strftime("%Y-%m-%d"))
        r["seat_note"] = SEAT_NOTE.get(y, "")
        if name in APP_GID or name in ("Trump1", "Biden", "Trump2"):
            a = approve_at(y, T, cache)
            r.update(approve=a["approve"], net_approve=a["approve"] - a["disapprove"], approve_n=a["n"],
                     approve_asof=a["last"].strftime("%Y-%m-%d") if pd.notna(a["last"]) else "")
        if y >= 1998:
            g = gb_at(y, T, cache)
            r.update(gb_avg=g["avg"], gb_src=g["src"])
        if y in ho.index:
            r.update(d_seats=ho.loc[y, "d_seats"], r_seats=ho.loc[y, "r_seats"], house_d2_nat=ho.loc[y, "house_d2_nat"],
                     house_votes=ho.loc[y, "house_votes"], n_uncontested=ho.loc[y, "n_uncontested"])
        if prev in ho.index:
            r.update(d_seats_prev=ho.loc[prev, "d_seats"], house_d2_prev=ho.loc[prev, "house_d2_nat"],
                     pres_d2_prev=po.loc[prev, "pres_d2_nat"] if prev in po.index else np.nan)
        if y in ho.index and prev in ho.index:
            r["d_seat_change"] = r["d_seats"] - r["d_seats_prev"]
            r["inparty_seat_change"] = r["d_seat_change"] * party
            r["d2_swing"] = r["house_d2_nat"] - r["house_d2_prev"]
            r["inparty_vote_swing"] = r["d2_swing"] * party
        rows.append(r)
    M = pd.DataFrame(rows)
    M.to_csv(U / "midterm_fundamentals.csv", index=False)
    print(M.to_string())


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
