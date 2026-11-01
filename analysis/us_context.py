"""Electorate context for the midterms page (site/midterms.html): who is in the early vote and on the rolls now vs 2022,
registration over time by party, what voters say matters, poll results by group.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_context.py     # -> site/data/us2026_context/*.json

Inputs
  NC per-voter absentee files 2022 and 2026 (SSD, voter level; only group counts leave this module)
  NC voter_stats_<date>.zip registration counts (SSD, official)
  data_public/us2026_context/reg_*.csv, issues_*.csv, crosstabs_groups.csv (collected from official / public sources)
Group results are counts of ballots or registrations by the group's recorded attribute, never how anyone voted. Counties
are grouped by population density (a place attribute), not by who lives there.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
OUT = ROOT / "site" / "data" / "us2026_context"
PUB = ROOT / "data_public" / "us2026_context"
ED22, ED26 = date(2022, 11, 8), date(2026, 11, 3)
AGE_BANDS = ["18-25", "26-40", "41-65", "66+"]
RACE_ORDER = ["White", "Black", "Hispanic", "Asian", "Other / unknown"]
PARTY_ORDER = ["Democratic", "Republican", "Unaffiliated", "Other party"]
GENDER_ORDER = ["Female", "Male", "Undisclosed"]
DENS_ORDER = ["Dense counties (500+ people/sq mi)", "Middle counties (100-500)", "Sparse counties (under 100)"]
DIMS = [("race", "Race / ethnicity", RACE_ORDER), ("age", "Age", AGE_BANDS), ("gender", "Gender", GENDER_ORDER),
        ("party", "Party registration", PARTY_ORDER), ("density", "Kind of county", DENS_ORDER)]


# ------------------------------------------------------------------------------------------------ group coding
def race_group(race: str, eth: str) -> str:
    """NC race + ethnicity codes or words -> one group; Hispanic or Latino of any race is Hispanic."""
    e, r = eth.strip().upper(), race.strip().upper()
    if e in ("HL", "HISPANIC OR LATINO"):
        return "Hispanic"
    if r in ("W", "WHITE"):
        return "White"
    if r in ("B", "BLACK OR AFRICAN AMERICAN", "BLACK"):
        return "Black"
    if r in ("A", "ASIAN"):
        return "Asian"
    return "Other / unknown"


def age_group(a) -> str | None:
    if isinstance(a, str) and a.startswith("Age"):
        return {"Age 18 - 25": "18-25", "Age 26 - 40": "26-40", "Age 41 - 65": "41-65", "Age Over 66": "66+"}.get(a)
    try:
        n = int(a)
    except (TypeError, ValueError):
        return None
    return None if n < 18 else "18-25" if n <= 25 else "26-40" if n <= 40 else "41-65" if n <= 65 else "66+"


def party_group(p: str) -> str:
    return {"DEM": "Democratic", "REP": "Republican", "UNA": "Unaffiliated"}.get(p.strip().upper(), "Other party")


def gender_group(g: str) -> str:
    return {"F": "Female", "M": "Male"}.get(g.strip().upper()[:1], "Undisclosed")


def density_tier(d: float) -> str:
    return DENS_ORDER[0] if d >= 500 else DENS_ORDER[1] if d >= 100 else DENS_ORDER[2]


def nc_density() -> dict[str, str]:
    """county NAME (upper) -> density tier, from the latest ACS county file."""
    import us_ev_db as db
    cd = pd.read_parquet(db.data_root() / "processed" / "us2026" / "unified" / "county_demo.parquet")
    cd = cd[cd["st"] == "nc"].sort_values("year").groupby("fips").tail(1)
    by_fips = dict(zip(cd["fips"], cd["density"]))
    out = {}
    for name in sorted(db._geo()["nc"]):          # normalised names -> fips
        f = db.county_fips("nc", name)
        if f in by_fips:
            out[name] = density_tier(by_fips[f])
    return out


# --------------------------------------------------------------------------------------------- NC early vote
def nc_count(rows, tier: dict[str, str]) -> Counter:
    """Rows of an NC absentee file (header first) -> Counter[(ISO date, race, age, gender, party, density)] of ACCEPTED ballots."""
    import us_ev_db as db
    it = iter(rows)
    h = next(it)
    ix = {c: h.index(c) for c in ("county_desc", "ballot_rtn_dt", "voter_party_code", "ballot_rtn_status", "race", "ethnicity",
                                  "gender", "age")}
    out: Counter = Counter()
    for r in it:
        if len(r) != len(h) or not r[ix["ballot_rtn_status"]].strip().upper().startswith("ACCEPTED"):
            continue
        m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", r[ix["ballot_rtn_dt"]].strip())
        age = age_group(r[ix["age"]])
        if not m or age is None:
            continue
        t = tier.get(db._norm(r[ix["county_desc"]]), DENS_ORDER[1])
        out[(f"{m.group(3)}-{m.group(1)}-{m.group(2)}", race_group(r[ix["race"]], r[ix["ethnicity"]]), age,
             gender_group(r[ix["gender"]]), party_group(r[ix["voter_party_code"]]), t)] += 1
    return out


def nc_file_counts(z: Path, tier) -> Counter:
    with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as raw:
        return nc_count(csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline="")), tier)


def cumulate(counts: Counter, through: str) -> dict[str, Counter]:
    """counts of ballots dated <= through, per dimension."""
    res = {k: Counter() for k, _, _ in DIMS}
    tot = 0
    for (d, race, age, gen, party, dens), n in counts.items():
        if d > through:
            continue
        tot += n
        for k, v in zip(("race", "age", "gender", "party", "density"), (race, age, gen, party, dens)):
            res[k][v] += n
    res["_total"] = Counter({"all": tot})
    return res


def shift_table(now: dict, past: dict, orders: dict | None = None) -> list[dict]:
    """share of the early vote by group, now vs the same day in 2022, + change in points (all groups of every dimension)."""
    rows = []
    for key, label, order in DIMS:
        tn, tp = sum(now[key].values()), sum(past[key].values())
        for g in order:
            n, p = now[key].get(g, 0), past[key].get(g, 0)
            sn, sp = (100 * n / tn if tn else None), (100 * p / tp if tp else None)
            rows.append({"dim": key, "dim_label": label, "group": g, "n_now": n, "n_2022": p,
                         "share_now": _r(sn), "share_2022": _r(sp),
                         "share_change_pp": _r(sn - sp) if sn is not None and sp is not None else None,
                         "count_change_pct": _r(100 * (n / p - 1)) if p else None})
    return rows


def _r(x, nd=2):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def build_nc_ev(log=print) -> dict | None:
    import us_ev_db as db
    raw = db.data_root() / "raw" / "us" / "ev_party" / "nc"
    z26, z22 = raw / "absentee_20261103.zip", raw / "absentee_20221108.zip"
    if not (z26.exists() and z22.exists()):
        return None
    tier = nc_density()
    c26, c22 = nc_file_counts(z26, tier), nc_file_counts(z22, tier)
    last = max(k[0] for k in c26)                       # last return date in the 2026 file
    k = (ED26 - date.fromisoformat(last)).days
    through22 = (ED22 - pd.Timedelta(days=k)).isoformat()[:10]
    now, past = cumulate(c26, last), cumulate(c22, through22)
    return {"state": "nc", "through_2026": last, "through_2022": through22, "days_before_election_day": k,
            "total_now": now["_total"]["all"], "total_2022": past["_total"]["all"],
            "rows": shift_table(now, past),
            "source": "NC State Board of Elections per-voter absentee and one-stop files, 2026 and 2022 "
                      "(dl.ncsbe.gov, public record); accepted ballots dated by return day",
            "url": "https://www.ncsbe.gov/results-data/absentee-data"}


# ----------------------------------------------------------------------------------------- NC registration mix
def reg_counts(z: Path, tier: dict[str, str]) -> dict[str, Counter]:
    import us_ev_db as db
    res = {k: Counter() for k, _, _ in DIMS}
    with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as raw:
        rd = csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline=""), delimiter="\t")
        h = next(rd)
        ix = {c: h.index(c) for c in ("county_desc", "party_cd", "race_code", "ethnic_code", "sex_code", "age", "total_voters")}
        for r in rd:
            if len(r) != len(h):
                continue
            age = age_group(r[ix["age"]])
            if age is None:
                continue
            n = int(r[ix["total_voters"]])
            vals = (race_group(r[ix["race_code"]], r[ix["ethnic_code"]]), age, gender_group(r[ix["sex_code"]]),
                    party_group(r[ix["party_cd"]]), tier.get(db._norm(r[ix["county_desc"]]), DENS_ORDER[1]))
            for kk, v in zip(("race", "age", "gender", "party", "density"), vals):
                res[kk][v] += n
    return res


def build_nc_reg(log=print) -> dict | None:
    import us_ev_db as db
    raw = db.data_root() / "raw" / "us" / "ev_party" / "nc"
    files = sorted(raw.glob("voter_stats_20*.zip"))
    f22 = raw / "voter_stats_20221108.zip"
    if not f22.exists() or len(files) < 2:
        return None
    new = files[-1]
    tier = nc_density()
    a, b = reg_counts(f22, tier), reg_counts(new, tier)
    rows = []
    for key, label, order in DIMS:
        ta, tb = sum(a[key].values()), sum(b[key].values())
        for g in order:
            na, nb = a[key].get(g, 0), b[key].get(g, 0)
            rows.append({"dim": key, "dim_label": label, "group": g, "n_2022": na, "n_now": nb,
                         "share_2022": _r(100 * na / ta), "share_now": _r(100 * nb / tb),
                         "share_change_pp": _r(100 * nb / tb - 100 * na / ta), "count_change": nb - na,
                         "count_change_pct": _r(100 * (nb / na - 1)) if na else None})
    m = re.search(r"(\d{4})(\d\d)(\d\d)", new.name)
    return {"state": "nc", "asof_2022": "2022-11-08", "asof_now": f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "rows": rows,
            "source": "NC State Board of Elections voter registration statistics (voter_stats files)",
            "url": "https://www.ncsbe.gov/results-data/voter-registration-data"}


# --------------------------------------------------------------------------------- registration over time (CSVs)
def moe(n: float | None, p: float = 0.5) -> float | None:
    """Approximate 95% margin of error in points for a proportion in a simple random sample of n (before design effect)."""
    return None if not n or n <= 0 else _r(196 * math.sqrt(p * (1 - p) / n), 1)


def load_registration() -> dict:
    states, blocked = {}, []
    for f in sorted(PUB.glob("reg_*.csv")):
        if f.name.startswith("reg_blocked"):
            blocked += _records(pd.read_csv(f).fillna(""))
            continue
        d = pd.read_csv(f)
        if d.empty:
            continue
        st = str(d["st"].iloc[0]).upper()
        d = d.sort_values("date")
        for c in ("D", "R", "O", "total", "U"):
            if c not in d:
                d[c] = np.nan
        series = [{"date": r.date, "D": _i(r.D), "R": _i(r.R), "O": _i(r.O), "U": _i(r.U), "total": _i(r.total),
                   "basis": getattr(r, "basis", "")} for r in d.itertuples()]
        states[st] = _reg_summary(st, series, str(d["source_url"].dropna().iloc[-1]) if "source_url" in d and d["source_url"].notna().any() else "")
    return {"states": states, "blocked": blocked}


def _i(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else int(x)


def _reg_summary(st: str, series: list[dict], url: str) -> dict:
    """series -> first report on/after Nov 2022 (the base) and the latest, net change by party."""
    base = next((s for s in series if s["date"] >= "2022-10-01"), series[0])
    last = series[-1]
    ch = {}
    for k in ("D", "R", "O", "U", "total"):
        a, b = base.get(k), last.get(k)
        ch[k] = None if a is None or b is None else {"n": b - a, "pct": _r(100 * (b / a - 1)) if a else None}
    return {"series": series, "base_date": base["date"], "latest_date": last["date"], "change": ch,
            "d_minus_r_change": None if None in (base["D"], base["R"], last["D"], last["R"])
            else (last["D"] - last["R"]) - (base["D"] - base["R"]), "url": url}


# ------------------------------------------------------------------------------------------------- polls: issues
def _records(d: pd.DataFrame) -> list[dict]:
    return [{k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in r.items()} for r in d.to_dict("records")]


def load_issues() -> dict:
    out = {"gallup": [], "polls": []}
    g, p = PUB / "issues_gallup.csv", PUB / "issues_polls.csv"
    if g.exists():
        out["gallup"] = _records(pd.read_csv(g))
    if p.exists():
        out["polls"] = _records(pd.read_csv(p))
    return out


def load_crosstabs() -> dict:
    f = PUB / "crosstabs_groups.csv"
    if not f.exists():
        return {"rows": []}
    d = pd.read_csv(f)
    rows = []
    for r in d.to_dict("records"):
        r = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in r.items()}
        gn = r.get("group_n")
        if gn is None and r.get("group_share") is not None and r.get("sample_n"):
            gn = r["sample_n"] * r["group_share"] / 100
            r["group_n_est"] = True
        r["group_n_used"] = None if gn is None else int(round(gn))
        r["moe_group"] = moe(gn)
        r["moe_poll"] = moe(r.get("sample_n"))
        if r.get("dem_pct") is not None and r.get("rep_pct") is not None:
            r["margin"] = _r(r["dem_pct"] - r["rep_pct"], 1)
        rows.append(r)
    return {"rows": rows}


# ---------------------------------------------------------------------------------------------------------- main
def write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, allow_nan=False, default=str) + "\n")


def main() -> int:
    ev, reg = build_nc_ev(), build_nc_reg()
    write("ev_nc.json", ev)
    write("reg_nc_mix.json", reg)
    write("registration.json", load_registration())
    write("issues.json", load_issues())
    write("crosstabs.json", load_crosstabs())
    write("meta.json", {"built": date.today().isoformat(), "ev_nc": bool(ev), "reg_mix": bool(reg)})
    print("ok", bool(ev), bool(reg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
