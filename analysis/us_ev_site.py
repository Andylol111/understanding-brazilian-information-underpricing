"""Builds site/data/ev2026/*.json for site/earlyvote.html from the early-vote database (docs/us_ev_site.md).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_site.py [--db ev.sqlite] [--out site/data/ev2026] [--strict]

Inputs (read only): the database copy (`--db`, default $BRUNDER_DATA/us_ev/ev.sqlite), the return-date files of
analysis/us_ev_daily.py (NC, PA) when present, and the 2016-2024 panels in $BRUNDER_DATA/processed/us2026/unified.
Outputs (aggregates only, nothing voter-level):
  index.json            coverage of all 51 (data level, party detail, as-of, last fetch, status, links) + latest figures
  state_<st>.json       per state: cumulative ballots by day, ballots added per day (mail / in person), party lead by
                        registration, comparison with the same day before 2018/2020/2022/2024
  counties_<st>.json    per county latest figures (states with county data)
One series per state and measure (us_ev_canon.py): sources are never added to each other.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import us_ev_canon as canon  # noqa: E402
import us_ev_daily as daily  # noqa: E402
import us_ev_db as db  # noqa: E402
import us_ev_pace as pace  # noqa: E402

ELECTION_DAY = date(2026, 11, 3)
WINDOW_DAYS = 60                     # days before Election Day shown for 2026
COMPARE_DAYS = 45                    # the past-cycle panels fold everything earlier than this into day 45
YEARS = (2018, 2020, 2022, 2024)
MIDTERMS = (2018, 2022)
FIRST_2026, LAST_2026 = "2026-06-01", "2026-11-10"
STALE_DAYS = 3                       # latest as-of older than this many days before the newest in the database: flagged
PARTIES = ("D", "R", "U", "other")
UF_ID = "uf_election_lab"
UF_PAGE = "https://election.lab.ufl.edu/early-vote/2026-early-voting"
UF_LABEL = "UF Election Lab (compiled from official state sources), used with permission"
UF_LICENCE = "CC BY-NC-ND 4.0; displayed with the permission of Dr. Michael McDonald, granted to the project (recorded 2026-11-01)"
UF_BASIS = ("Compiled by the UF Election Lab from official state sources, as the Lab publishes it (figures are not adjusted here). "
            "The Lab fills some states by estimate or from county contacts, so this is not an official count.")
NAMES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "dc": "District of Columbia", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho", "il": "Illinois",
    "in": "Indiana", "ia": "Iowa", "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri", "mt": "Montana",
    "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania",
    "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
}
# Why a state has no same-day comparison with past cycles (only where the panels hold something that cannot be used).
HIST_CAVEAT = {
    "tx": "the past file holds only the largest counties, so it cannot be compared with a statewide count",
    "fl": "Florida's past files are one end-of-period compilation per election, not a day-by-day series",
}
UF2022_LABEL = "UF Election Lab / US Elections Project 2022 tracker page, archived by the Internet Archive"
BASIS = {
    "nc": "Accepted ballots by the day they were returned or voted (mail ballots including those accepted after a cure, plus one-stop early voting), rebuilt from the State Board's per-voter file.",
    "pa": "Mail ballots returned by the day they were returned (Pennsylvania has no in-person early voting), rebuilt from the Department of State's open-data extract; ballots later cancelled are still counted.",
}
MAIL_TEXT = {"mail_returned": "Mail ballots returned", "mail_accepted": "Mail ballots accepted"}


# ------------------------------------------------------------------------------------------------------------- load
def default_db() -> Path:
    return db.ev_root() / "ev.sqlite"


def read_db(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = lambda sql: pd.read_sql(sql, con)    # noqa: E731
    where = f"age_band='ALL' AND as_of_date BETWEEN '{FIRST_2026}' AND '{LAST_2026}'"
    out = {
        "state": q(f"SELECT state, as_of_date, measure, party, count, source_id FROM ev_state_latest WHERE {where}"),
        "county": q(f"SELECT state, county_fips, as_of_date, measure, party, count, source_id FROM ev_county_latest WHERE {where}"),
        "sources": q("SELECT state, source_id, url, kind, format, cadence, access, notes FROM sources WHERE source_id NOT LIKE 'legacy%'"),
        "snapshots": q("SELECT state, source_id, fetched_utc, valid FROM snapshots"),
        "fetch_log": q("SELECT ts, state, source_id, outcome FROM fetch_log WHERE outcome IN ('new','unchanged')"),
    }
    con.close()
    for k in ("state", "county", "sources", "snapshots", "fetch_log"):
        out[k]["state"] = out[k]["state"].str.lower()
    return out


def split_uf(frames: dict) -> tuple[dict, pd.DataFrame, str | None]:
    """The UF Election Lab tracker (kind 'secondary', CC BY-NC-ND, shown with the project's permission) is kept apart from the
    official rows: it never enters an official series, only (a) fills states with no official count, labelled as compiled,
    and (b) is shown next to the official figure of the other states as a cross-check.
    -> (frames without UF rows, UF state rows, time of the last UF fetch)."""
    src = frames["sources"]
    ids = set(src.loc[src["kind"] == "secondary", "source_id"])
    uf = frames["state"][frames["state"]["source_id"] == UF_ID]
    last = frames["fetch_log"].loc[frames["fetch_log"]["source_id"] == UF_ID, "ts"].max()
    for k in ("state", "county"):
        frames[k] = frames[k][~frames[k]["source_id"].isin(ids)]
    frames["snapshots"] = frames["snapshots"][~frames["snapshots"]["source_id"].isin(ids)]
    frames["fetch_log"] = frames["fetch_log"][~frames["fetch_log"]["source_id"].isin(ids)]
    frames["sources"] = src[src["kind"] != "secondary"]
    return frames, uf, (None if pd.isna(last) else last)


def add_daily_sources(frames: dict, log=print) -> dict:
    """NC and PA day-by-day series from return dates (pseudo sources 'daily_*', ranked before the collector's snapshots)."""
    start = (ELECTION_DAY - timedelta(days=WINDOW_DAYS)).isoformat()
    parts = []
    if daily.nc_zip_path().exists() and daily.nc_as_of():
        parts.append(("daily_nc_voter_file", daily.nc_aggregate(), daily.nc_as_of(), {"mail": "mail_accepted", "inperson": "inperson_early"}))
    got = daily.pa_aggregate()
    if got:
        parts.append(("daily_pa_open_data", got[0], got[1], {"mail": "mail_returned"}))
    for sid, df, as_of, measures in parts:
        county, state = daily.cumulative(df, as_of, start, measures)
        frames["county"] = pd.concat([frames["county"], county.assign(source_id=sid)], ignore_index=True)
        frames["state"] = pd.concat([frames["state"], state.assign(source_id=sid)], ignore_index=True)
        log(f"daily source {sid}: {state['as_of_date'].nunique()} days to {as_of}")
    return frames


def geo_names() -> dict[str, str]:
    g = json.loads((ROOT / "site" / "data" / "geo" / "us_counties.topo.json").read_text())
    return {x["properties"]["unit_id"].split("-", 2)[2]: re.sub(r",\s*[A-Z]{2}$", "", x["properties"]["name"]) for x in g["objects"]["counties"]["geometries"]}


def load_history() -> dict | None:
    base = db.data_root() / "processed" / "us2026" / "unified"
    try:
        hs, cy = pd.read_parquet(base / "ev_hist_state.parquet"), pd.read_parquet(base / "ev_hist_county_year.parquet")
    except FileNotFoundError:
        return None
    cy = cy[cy["year"].isin(YEARS + (2022,))]
    fin = cy.groupby(["st", "year"]).agg(votes=("v_ref", "sum"), missing=("v_ref", lambda s: int(s.isna().sum())), counties=("v_ref", "size"))
    return {"state": hs[hs["year"].isin(YEARS)], "final": fin, "county22": cy[cy["year"] == 2022].set_index("fips")["v_ref"]}


# ------------------------------------------------------------------------------------------------------------ series
def wide(c: pd.DataFrame, unit: list[str], mail_measure: str, has_inperson_ever: bool) -> pd.DataFrame:
    """Canonical long rows of one state -> one row per unit x date: total, mail, inperson, requested and the party columns of
    the total (D R U other). Total = the source's own 'total' if it gives one, else mail + in person on that date, else mail
    alone for a state with no in-person early voting."""
    p = c.pivot_table(index=unit + ["as_of_date"], columns=["measure", "party"], values="count", aggfunc="first")
    nan = pd.Series(np.nan, index=p.index)
    g = lambda m, party: p[(m, party)] if (m, party) in p.columns else nan    # noqa: E731
    out = pd.DataFrame(index=p.index)
    for party in ("ALL",) + PARTIES:
        mail, ip = g(mail_measure, party), g("inperson_early", party)
        tot = g("total", party).fillna(mail + ip)
        if not has_inperson_ever:
            tot = tot.fillna(mail)
        col = "total" if party == "ALL" else party
        out[col] = tot
        if party == "ALL":
            out["mail"], out["inperson"] = mail, ip
            out["requested"] = g("mail_requested", "ALL")
    return out


def lead_pp(d, r, u, o):
    """Democratic minus Republican share of returned ballots by party registration, percentage points."""
    den = pd.concat([d, r, u.fillna(0), o.fillna(0)], axis=1).sum(axis=1, min_count=2)
    return 100 * (d - r) / den.where(den > 0)


def clean(x):
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else (int(x) if float(x).is_integer() else round(float(x), 3))
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def col(s: pd.Series) -> list:
    return [clean(v) for v in s.tolist()]


def source_labels(sources: pd.DataFrame, st: str) -> dict[str, dict]:
    out = {}
    for r in sources[sources["state"] == st].itertuples():
        first = re.split(r"(?<=[a-z0-9)])\.\s", (r.notes or "").strip())[0][:200]
        out[r.source_id] = {"id": r.source_id, "url": r.url if str(r.url).startswith("http") and "<" not in str(r.url) else None,
                            "what": first, "cadence": r.cadence or ""}
    for sid in set(sources.loc[sources["state"] == st, "source_id"]) - set(out):
        out[sid] = {"id": sid, "url": None, "what": sid, "cadence": ""}
    for sid, label in (("daily_nc_voter_file", "NC State Board of Elections per-voter absentee file: accepted ballots counted by return date."),
                       ("daily_pa_open_data", "Pennsylvania open-data extract of mail ballots, aggregated by ballot-returned date.")):
        out[sid] = {"id": sid, "url": {"daily_nc_voter_file": "https://www.ncsbe.gov/results-data/absentee-data", "daily_pa_open_data": "https://data.pa.gov/Government-Efficiency-Citizen-Engagement/2026-General-Election-Mail-Ballot-Requests/nbwd-pfn4"}[sid],
                    "what": label, "cadence": "daily"}
    return out


def build_state(st: str, rows: pd.DataFrame, srcs: dict, hist: dict | None, compiled: bool = False, uf_hist: dict | None = None) -> dict | None:
    mail_m = "mail_returned" if (rows["measure"] == "mail_returned").any() else "mail_accepted"
    has_ip = (rows["measure"] == "inperson_early").any()
    w = wide(rows, ["state"], mail_m, has_ip).droplevel("state")
    w = w[w["total"].notna() | w["mail"].notna() | w["inperson"].notna() | w["requested"].notna()].sort_index()
    t = w[w["total"].notna()]
    if t.empty:
        return None
    dates = pd.to_datetime(pd.Series(t.index))
    keep = (ELECTION_DAY - dates.dt.date).map(lambda x: x.days).to_numpy() <= WINDOW_DAYS
    t = t[keep]
    if t.empty:
        return None
    db_days = np.array([(ELECTION_DAY - date.fromisoformat(d)).days for d in t.index])
    span = np.r_[np.nan, -np.diff(db_days)]                         # days since the previous point
    has_party = bool(t[["D", "R"]].notna().all(axis=1).any())
    lead = lead_pp(t["D"], t["R"], t["U"], t["other"])
    added = {k: t[k].diff() for k in ("total", "mail", "inperson")}
    src_by_date = rows[rows["party"] == "ALL"].sort_values("measure", key=lambda m: m.map({"total": 0, mail_m: 1, "inperson_early": 2}).fillna(3)) \
        .drop_duplicates("as_of_date").set_index("as_of_date")["source_id"]
    used = list(dict.fromkeys(src_by_date.reindex(t.index).dropna().tolist()))
    last = t.iloc[-1]
    prev = t.iloc[-2] if len(t) > 1 else None
    req = w["requested"].dropna()
    series = {"date": list(t.index), "db": [int(x) for x in db_days], "span": [None if np.isnan(x) else int(x) for x in span],
              "total": col(t["total"]), "mail": col(t["mail"]), "inperson": col(t["inperson"]),
              "added_total": col(added["total"]), "added_mail": col(added["mail"]), "added_inperson": col(added["inperson"]),
              "src": [used.index(s) if s in used else None for s in src_by_date.reindex(t.index)]}
    if has_party:
        series.update({p: col(t[p]) for p in PARTIES}); series["lead"] = col(lead)
    out = {
        "st": st, "name": NAMES[st], "as_of": t.index[-1], "election_day": ELECTION_DAY.isoformat(),
        "methods": {"mail": mail_m if t["mail"].notna().any() else None, "inperson": "inperson_early" if t["inperson"].notna().any() else None},
        "mail_label": MAIL_TEXT[mail_m], "has_party": has_party,
        "compiled": compiled,
        "basis": UF_BASIS if compiled else BASIS.get(st) or ("; ".join(x for x in (MAIL_TEXT[mail_m] if t["mail"].notna().any() else "", "in-person early votes" if t["inperson"].notna().any() else "") if x) or "Ballots returned") + ".",
        "daily_file": any(s.startswith("daily_") for s in used),
        "series": series, "sources": [srcs.get(s, {"id": s, "url": None, "what": "Earlier file compiled from official publications before the database started", "cadence": ""}) for s in used],
        "latest": {"date": t.index[-1], "db": int(db_days[-1]), "total": clean(last["total"]), "mail": clean(last["mail"]), "inperson": clean(last["inperson"]),
                   "added": clean(last["total"] - prev["total"]) if prev is not None else None,
                   "span": None if np.isnan(span[-1]) else int(span[-1]),
                   "lead": clean(lead.iloc[-1]) if has_party else None,
                   "party": {p: clean(last[p]) for p in PARTIES} if has_party else None},
        "requested": {"date": req.index[-1], "value": clean(req.iloc[-1])} if len(req) else None,
    }
    out["compare"] = compare(st, t, lead, hist, uf_hist if compiled else None) if hist is not None else {"cycles": [], "why": "past-cycle panels not available in this build"}
    return out


# --------------------------------------------------------------------------------------------------------- compare
def compare(st: str, t: pd.DataFrame, lead: pd.Series, hist: dict, uf_hist: dict | None = None) -> dict:
    """Same day before Election Day in 2018-2024: ballots then, as a share of that cycle's final total votes, party lead then."""
    d0 = (ELECTION_DAY - date.fromisoformat(t.index[-1])).days
    kind = "total"
    hs = hist["state"][hist["state"]["st"] == st]
    if st == "md" or (hs["kind"].eq("inperson").any() and not hs["kind"].isin(["total", "mail"]).any()):
        kind, now_total, now_lead = "inperson", t["inperson"].iloc[-1], None      # past panel: in-person only
    else:
        now_total, now_lead = t["total"].iloc[-1], lead.iloc[-1] if t[["D", "R"]].notna().all(axis=1).iloc[-1] else None
    cycles = []
    for y in YEARS:
        base = {"year": y, "midterm": y in MIDTERMS}
        if uf_hist is not None and hs.empty:                # a state with no official past series: the Lab's own past file, if it fits
            cycles.append(uf_cycle(st, y, base, d0, now_total, now_lead, uf_hist, hist)); continue
        h = hs[(hs["year"] == y) & (hs["kind"] == kind) & (~hs["is_final"])]
        fin = hist["final"].loc[(st, y)] if (st, y) in hist["final"].index else None
        votes = float(fin["votes"]) if fin is not None and fin["missing"] == 0 and fin["counties"] > 0 else None
        past = hs[(hs["year"] == y) & (~hs["is_final"])]
        if st in HIST_CAVEAT and not past.empty and (st == "tx" or past["days_before"].nunique() <= 1):
            cycles.append({**base, "ok": False, "why": HIST_CAVEAT[st]}); continue
        if h.empty:
            cycles.append({**base, "ok": False, "why": "no day-by-day count for this cycle in the official files" if not hs.empty else "this state published no past day-by-day series"}); continue
        h = h.sort_values("days_before")
        near = h.iloc[(h["days_before"] - d0).abs().argsort()[:1]].iloc[0]
        if abs(int(near["days_before"]) - d0) > 1:
            lo, hi = int(h["days_before"].min()), int(h["days_before"].max())
            cycles.append({**base, "ok": False, "why": f"that cycle's series covers {lo} to {hi} days before Election Day, not {d0}"}); continue
        den = near[["ev_D", "ev_R", "ev_O"]].astype(float)
        then_lead = 100 * (den["ev_D"] - den["ev_R"]) / den.sum() if den.notna().all() and den.sum() > 0 else None
        cum = h[h["days_before"] <= COMPARE_DAYS]
        dens = cum[["ev_D", "ev_R", "ev_O"]].astype(float)
        cl = 100 * (dens["ev_D"] - dens["ev_R"]) / dens.sum(axis=1).where(dens.sum(axis=1) > 0)
        ballots = float(near["ev_total"])
        cycles.append({**base, "ok": True, "days_used": int(near["days_before"]), "date": str(pd.Timestamp(near["asof_date"]).date()),
                       "ballots": clean(ballots), "final_votes": clean(votes), "pct_final": clean(100 * ballots / votes) if votes else None,
                       "lead": clean(then_lead), "now_pct_final": clean(100 * now_total / votes) if votes else None,
                       "now_vs_then": clean(now_total / ballots) if ballots > 0 else None,
                       "partial": bool(near.get("n_counties") and st == "tx"),
                       "curve": {"db": [int(x) for x in cum["days_before"]], "cum": col(cum["ev_total"]),
                                 "lead": col(cl) if cl.notna().any() else None}})
    return {"kind": kind, "db": d0, "now": {"ballots": clean(now_total), "lead": clean(now_lead)}, "cycles": cycles,
            "votes_basis": "Final total votes cast in that election: the largest office total in each county of the official county panel, summed."}


def uf_cycle(st: str, y: int, base: dict, d0: int, now_total, now_lead, uf_hist: dict, hist: dict) -> dict:
    """The Lab's past tracker file holds ONE snapshot per state (dated 0-5 days before Election Day), only for 2024; the 2020 and 2022
    files do not exist. It is a same-day comparison only when that snapshot falls within a day of this year's day."""
    r = uf_hist.get(st) if y == 2024 else None
    if r is None:
        return {**base, "ok": False, "why": "the UF Election Lab publishes no past file for this cycle" if y != 2024 else "the Lab's 2024 file has no count for this state"}
    if abs(r["days_before"] - d0) > 1:
        return {**base, "ok": False, "why": f"the Lab's 2024 file is one snapshot taken {r['days_before']} days before Election Day, not {d0}"}
    fin = hist["final"].loc[(st, y)] if (st, y) in hist["final"].index else None
    votes = float(fin["votes"]) if fin is not None and fin["missing"] == 0 and fin["counties"] > 0 else None
    den = r["D"] + r["R"] + r["U"] if r["D"] and r["R"] else None
    lead = 100 * (r["D"] - r["R"]) / den if den else None
    return {**base, "ok": True, "days_used": r["days_before"], "date": r["date"], "ballots": clean(r["total"]), "final_votes": clean(votes),
            "pct_final": clean(100 * r["total"] / votes) if votes else None, "lead": clean(lead),
            "now_pct_final": clean(100 * now_total / votes) if votes else None, "now_vs_then": clean(now_total / r["total"]),
            "partial": False, "uf": True, "curve": {"db": [r["days_before"]], "cum": [clean(r["total"])], "lead": None}}


# --------------------------------------------------------------------------------------------------------- counties
def build_counties(st: str, rows: pd.DataFrame, names: dict, hist: dict | None) -> dict | None:
    mail_m = "mail_returned" if (rows["measure"] == "mail_returned").any() else "mail_accepted"
    has_ip = (rows["measure"] == "inperson_early").any()
    w = wide(rows, ["state", "county_fips"], mail_m, has_ip).droplevel("state")
    measure = "total"
    if not w["total"].notna().any():          # county offices that publish one method only (e.g. Clark NV in-person): say so, no total
        measure = next((m for m in ("inperson", "mail") if w[m].notna().any()), None)
        if measure is None:
            return None
        w = w.assign(total=w[measure])
    t = w[w["total"].notna()]
    latest = t.index.get_level_values("as_of_date").max()
    cur = t.xs(latest, level="as_of_date")
    dates = sorted(t.index.get_level_values("as_of_date").unique())
    prev = t.xs(dates[-2], level="as_of_date")["total"] if len(dates) > 1 else pd.Series(dtype=float)
    has_party = bool(cur[["D", "R"]].notna().all(axis=1).any())
    lead = lead_pp(cur["D"], cur["R"], cur["U"], cur["other"]) if has_party else pd.Series(np.nan, index=cur.index)
    v22 = hist["county22"] if hist is not None else pd.Series(dtype=float)
    recs = []
    for fips, r in cur.iterrows():
        base = v22.get(fips)
        recs.append({"fips": fips, "name": names.get(fips, fips), "total": clean(r["total"]), "mail": clean(r["mail"]), "inperson": clean(r["inperson"]),
                     "added": clean(r["total"] - prev.get(fips)) if fips in prev.index else None,
                     **({p: clean(r[p]) for p in PARTIES} | {"lead": clean(lead.get(fips))} if has_party else {}),
                     "votes22": clean(base) if base is not None and np.isfinite(base) else None,
                     "pct22": clean(100 * r["total"] / base) if measure == "total" and base is not None and np.isfinite(base) and base > 0 else None})
    recs.sort(key=lambda x: -(x["total"] or 0))
    return {"st": st, "name": NAMES[st], "measure": measure, "as_of": latest, "prev_as_of": dates[-2] if len(dates) > 1 else None, "has_party": has_party,
            "n": len(recs), "counties": recs}


# ----------------------------------------------------------------------------------------------------------- coverage
STATUS_TEXT = {"collected": "collected", "blocked": "blocked", "not_published": "not published"}


def registry_status(reg: pd.DataFrame) -> tuple[str, str]:
    """(status, detail) from the registry rows of a state that has no collected data."""
    acc = " | ".join(x for x in reg["access"].tolist() if x and x != "open")
    low = acc.lower()
    if low.startswith("blocked") or "blocked:" in low:
        return "blocked", acc
    return "not_published", acc or "nothing found"


def state_pace(st: str, s: dict, hist: dict | None, cert: dict, uf22: pd.DataFrame) -> dict:
    """The 'Pace vs 2022' entry of one state with data (analysis/us_ev_pace.py): this year's count against the same day before
    Election Day in 2022 (official past series where the state has one, else the archived UF 2022 tracker pages)."""
    lat = s["latest"]
    d0 = lat["db"]
    hs = hist["state"][hist["state"]["st"] == st] if hist is not None else pd.DataFrame(columns=["st", "year", "kind", "is_final"])
    inperson_only = st == "md" or (hs["kind"].eq("inperson").any() and not hs["kind"].isin(["total", "mail"]).any())
    kind = "inperson" if inperson_only else "total"
    value = lat["inperson"] if inperson_only else lat["total"]
    compiled = s.get("compiled", False)
    now = {"value": value, "date": lat["date"], "db": d0, "source": "compiled" if compiled else "official",
           "measure": "in-person early votes" if inperson_only else ("total ballots" + ("" if compiled else ": " + s["basis"].rstrip(".").lower()))}
    then, why = None, ""
    if hist is not None and not hs.empty:
        past = hs[(hs["year"] == 2022) & (~hs["is_final"])]
        if st in HIST_CAVEAT and not past.empty and (st == "tx" or past["days_before"].nunique() <= 1):
            why = HIST_CAVEAT[st]
        else:
            then, why = pace.official_then(hs, kind, d0)
    elif hist is not None:
        why = "this state published no past day-by-day series"
    if then is None:
        m = s["methods"]
        got, why2 = pace.uf_then(uf22[uf22["st"] == st], d0, bool(m["mail"] and m["inperson"]), bool(m["mail"] and not m["inperson"]))
        then, why = (got, "") if got else (None, (why + "; " if why else "") + why2)
    return pace.build(now, then, cert.get(st), why)


def build_coverage(frames: dict, canon_state: pd.DataFrame, canon_county: pd.DataFrame, states: dict, counties: dict,
                   hist: dict | None, uf_fetch: str | None = None, pace_map: dict | None = None) -> list[dict]:
    reg, snaps, flog = frames["sources"], frames["snapshots"], frames["fetch_log"]
    sec: set = set()
    newest = max((s["latest"]["date"] for s in states.values()), default=None)
    cov = []
    for st, name in sorted(NAMES.items(), key=lambda kv: kv[1]):
        r = reg[(reg["state"] == st) & ~reg["source_id"].isin(sec)]
        sid = set(r["source_id"])
        s = states.get(st)
        has_county_rows = (not canon_county.empty) and (canon_county["state"] == st).any()
        fl = flog[flog["state"] == st]
        fl = fl[fl["source_id"].isin(sid) | fl["source_id"].str.startswith("daily_")]
        sn = snaps[(snaps["state"] == st) & (snaps["valid"] == 1)]
        links, seen = [], set()
        for u, what in zip(r["url"], r["notes"].fillna("")):
            if str(u).startswith("http") and "<" not in u and u not in seen:
                seen.add(u)
                links.append({"label": urlparse(u).netloc.removeprefix("www."), "url": u})
        entry = {"st": st, "name": name, "links": links[:4], "sources": sorted(sid), "has_state": st in states, "has_counties": st in counties,
                 "last_fetch": fl["ts"].max() if len(fl) else None, "last_new": sn["fetched_utc"].max() if len(sn) else None}
        level = "county" if (has_county_rows and (canon_county["state"] == st).any() and st in counties) else ("state" if s else "none")
        if s:
            lat = s["latest"]
            stale = newest is not None and (date.fromisoformat(newest) - date.fromisoformat(lat["date"])).days > STALE_DAYS
            pc = (pace_map or {}).get(st)
            compiled = s.get("compiled", False)
            reg_status, reg_detail = registry_status(r) if len(r) and compiled else ("", "")
            entry.update({"status": "compiled" if compiled else "collected", "detail": "", "level": "state" if compiled else level, "party": s["has_party"], "as_of": lat["date"], "stale": bool(stale),
                          "total": lat["total"], "mail": lat["mail"], "inperson": lat["inperson"], "added": lat["added"], "lead": lat["lead"],
                          "pct22": clean(pc["pct22"]) if pc and pc["pct22"] is not None else None, "pace": pc,
                          "days": len(s["series"]["date"]), "hist": [c["year"] for c in s["compare"]["cycles"] if c.get("ok")],
                          })
            if compiled:
                county_only = st in counties
                entry.update({"detail": UF_LABEL, "partial": False, "county_only_official": county_only, "last_fetch": uf_fetch, "last_new": uf_fetch,
                              "official": {"status": "county_only" if county_only else (reg_status or "not_published"),
                                           "detail": "only some county offices publish: the county table shows them" if county_only else (reg_detail or "no official count collected")},
                              "links": [{"label": "UF Election Lab", "url": UF_PAGE}] + entry["links"][:3]})
            elif all(x["id"].startswith("legacy_") for x in s["sources"]):
                entry["detail"] = "one earlier file; the collector does not refresh it"
        elif has_county_rows:                  # county offices only (EVGaps' batch E): never shown as a state total
            cs = counties.get(st)
            entry.update({"status": "collected", "detail": "county only: some counties, not a state total", "level": "county", "partial": True, "party": bool(cs and cs["has_party"]),
                          "as_of": cs["as_of"] if cs else None, "stale": False, "total": None, "mail": None, "inperson": None, "added": None, "lead": None,
                          "pct22": None, "days": 0, "hist": []})
        else:
            status, detail = registry_status(r) if len(r) else ("not_published", "no source registered")
            entry.update({"status": status, "detail": detail, "level": "none", "party": False, "as_of": None, "stale": False, "total": None,
                          "mail": None, "inperson": None, "added": None, "lead": None, "pct22": None, "days": 0, "hist": []})
        cov.append(entry)
    return cov


# ------------------------------------------------------------------------------------------------------------- main
def write_json(path: Path, obj) -> bool:
    new = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if path.exists() and path.read_text() == new:
        return False
    path.write_text(new)
    return True


def cross_check(official: dict, uf: dict) -> dict | None:
    """The Lab's latest count next to the official count on the same day (or the latest official day not after it)."""
    u = uf["latest"]
    od = [d for d in official["series"]["date"] if d <= u["date"]]
    if not od or u["total"] is None:
        return None
    i = official["series"]["date"].index(od[-1])
    o = official["series"]["total"][i]
    if not o:
        return None
    return {"uf_date": u["date"], "uf_total": u["total"], "official_date": od[-1], "official_total": o, "diff": clean(u["total"] - o),
            "diff_pct": clean(100 * (u["total"] - o) / o), "label": UF_LABEL, "url": UF_PAGE}


def load_uf_2024() -> dict | None:
    """The Lab's 2024 tracker file (one snapshot per state), cached by `us_ev_daily.py refresh-uf2024`."""
    f = daily.uf_2024_path()
    if not f.exists():
        return None
    d = pd.read_csv(f)
    out = {}
    for r in d.itertuples():
        total = getattr(r, "voted_all", 0)
        if not total or pd.isna(total):
            continue
        day = datetime.strptime(str(r.last_update).strip(), "%m/%d/%Y").date()
        v = lambda x: int(x) if pd.notna(x) else 0     # noqa: E731
        out[str(r.state_abbv).lower()] = {"date": day.isoformat(), "days_before": (date(2024, 11, 5) - day).days, "total": int(total),
                                           "D": v(r.voted_dem), "R": v(r.voted_rep), "U": v(r.voted_none)}
    return out


def build(db_path: Path, out: Path, use_daily: bool = True, log=print) -> dict:
    frames = read_db(db_path)
    frames, uf_rows, uf_fetch = split_uf(frames)
    tracker = {"label": UF_LABEL, "url": UF_PAGE, "licence": UF_LICENCE} if len(uf_rows) else None
    if use_daily:
        frames = add_daily_sources(frames, log)
    srows, scr, rank = canon.prepare(frames["state"])
    crows, ccr, _ = canon.prepare(frames["county"], ("state", "county_fips")) if len(frames["county"]) else (frames["county"],) * 2 + ({},)
    cstate, ccounty = canon.canonical(frames["state"]), (canon.canonical(frames["county"], ("state", "county_fips")) if len(frames["county"]) else frames["county"])
    dropped = canon.dropped_sources(srows, scr) + (canon.dropped_sources(crows, ccr) if len(crows) else [])
    # every kept source must tie out: counties add up to the state row; sources that cover one date agree
    both = set(scr["source_id"]) & set(ccr["source_id"]) if len(ccr) else set()
    audit = canon.audit(scr[scr["source_id"].isin(both)], ccr[ccr["source_id"].isin(both)], cstate)
    hist = load_history()
    names = geo_names()
    cur_days = 0
    states, counties = {}, {}
    # UF Election Lab days AFTER a state's last official day extend that state's official statewide series (the
    # official file is the base and keeps its county detail; the Lab, collected automatically, carries the series on
    # when an official file stops arriving, e.g. one published only behind a bot check and loaded by hand). Only when
    # the two measure the same thing: on the latest day both report, the Lab's total is within 2 % of the official one.
    ufc = canon.canonical(uf_rows) if len(uf_rows) else None
    uf_src = {UF_ID: {"id": UF_ID, "url": UF_PAGE, "what": UF_LABEL, "cadence": "daily"}}

    def _tot(f):
        t = f[f["measure"] == "total"]
        if "party" in t:
            t = t[t["party"] == "ALL"]
        return t.groupby("as_of_date")["count"].sum()
    for st in sorted(NAMES):
        srcs = source_labels(frames["sources"], st)
        x = cstate[cstate["state"] == st]
        if len(x) and ufc is not None:
            u = ufc[ufc["state"] == st]
            later = u[u["as_of_date"] > x["as_of_date"].max()]
            if len(later):
                both = _tot(x).align(_tot(u), join="inner")
                ok = len(both[0]) and both[1].iloc[-1] > 0 and abs(both[0].iloc[-1] / both[1].iloc[-1] - 1) <= 0.02
                if ok:
                    x = pd.concat([x, later], ignore_index=True)
                    srcs = {**srcs, **uf_src}
        if len(x):
            res = build_state(st, x, srcs, hist, cur_days)
            if res:
                states[st] = res
        if len(ccounty):
            y = ccounty[ccounty["state"] == st]
            if len(y):
                c = build_counties(st, y, names, hist)
                if c:
                    counties[st] = c
    uf_hist = load_uf_2024()
    if ufc is not None:
        for st in sorted(NAMES):
            x = ufc[ufc["state"] == st]
            if not len(x):
                continue
            res = build_state(st, x, uf_src, hist, compiled=True, uf_hist=uf_hist)
            if not res:
                continue
            if st in states:                                   # official wins; the Lab's figure is a cross-check
                states[st]["uf_check"] = cross_check(states[st], res)
            else:
                states[st] = res
    cert, uf22 = pace.read_certified(db.data_root()), pace.read_uf2022(db.data_root())
    pace_map = {st: state_pace(st, s, hist, cert, uf22) for st, s in states.items()}
    cov = build_coverage(frames, cstate, ccounty, states, counties, hist, uf_fetch, pace_map)
    snaps = frames["snapshots"]
    index = {"built": snaps["fetched_utc"].max(), "election_day": ELECTION_DAY.isoformat(), "window_days": WINDOW_DAYS, "states": cov,
             "years": list(YEARS), "midterms": list(MIDTERMS), "tracker": tracker,
             "pace": {"max_gap_days": pace.MAX_GAP, "flag_gap_days": pace.EXACT_GAP, "uf2022_found": sorted(set(uf22["st"])),
                      "uf2022_used": sorted(k for k, v in pace_map.items() if v["then"] and v["then"]["source"] == "uf2022"), "uf2022_label": UF2022_LABEL,
                      "certified_basis": "Certified 2022 total votes: the largest statewide office total (governor, U.S. Senate, or all U.S. House votes)."},
             "audit": {"county_not_equal_state": len(audit["county_not_equal_state"]), "canonical_has_two_sources": len(audit["canonical_has_two_sources"]),
                       "dropped": dropped},
             "counts": {"collected": sum(1 for c in cov if c["status"] == "collected"), "compiled": sum(1 for c in cov if c["status"] == "compiled"), "blocked": sum(1 for c in cov if c["status"] == "blocked"),
                        "not_published": sum(1 for c in cov if c["status"] == "not_published"), "with_party": sum(1 for c in cov if c.get("party")),
                        "with_county": sum(1 for c in cov if c.get("level") == "county")}}
    out.mkdir(parents=True, exist_ok=True)
    changed = int(write_json(out / "index.json", index))
    keep = {"index.json"}
    for st, s in states.items():
        changed += write_json(out / f"state_{st}.json", s); keep.add(f"state_{st}.json")
    for st, c in counties.items():
        changed += write_json(out / f"counties_{st}.json", c); keep.add(f"counties_{st}.json")
    for f in out.glob("*.json"):
        if f.name not in keep:
            f.unlink()
    log(f"wrote {len(keep)} files ({changed} changed) to {out}")
    return {"index": index, "audit": audit, "states": states, "counties": counties}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data" / "ev2026")
    ap.add_argument("--no-daily", action="store_true", help="skip the NC/PA return-date files (database snapshots only)")
    ap.add_argument("--strict", action="store_true", help="exit 1 if the audit finds a county/state mismatch or a double-counted source")
    a = ap.parse_args(argv)
    res = build(a.db or default_db(), a.out, use_daily=not a.no_daily)
    ix = res["index"]
    print(json.dumps({"counts": ix["counts"], "audit": ix["audit"]}, indent=1))
    # --strict: a county sum more than 0.5 % off its state row, or a double-counted source, fails the build. Smaller gaps come
    # from a state's own file (county and state tables taken at slightly different moments, e.g. North Dakota 2026-11-02:
    # 0.1 %); they stay counted in the published audit but do not stop the page.
    big = [r for r in res["audit"]["county_not_equal_state"] if abs(r["county_sum"] - r["state_total"]) > 0.005 * max(r["state_total"], 1)]
    bad = big or res["audit"]["canonical_has_two_sources"]
    return 1 if (a.strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
