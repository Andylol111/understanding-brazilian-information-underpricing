"""Electorate context for the midterms page (site/midterms.html): who is in the early vote and on the rolls now vs 2022,
registration over time by party, what voters say matters, poll results by group.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_context.py     # -> site/data/us2026_context/*.json

Inputs
  NC per-voter absentee files 2022 and 2026 (SSD, voter level; only group counts leave this module)
  NC voter_stats_<date>.zip registration counts (SSD, official)
  $BRUNDER_DATA/raw/us/ev_groups/ co_<date>.xlsx, sc_<date>.xlsx, sc_2022-GE-final.xlsx, uf_<date>.csv (aggregates copied from the server
    collector's raw/ directory: rsync -a --rsync-path="sudo -n rsync" oracle-slotbank:/srv/brunder/evdata/us_ev/raw/{co,sc,us} ...),
    raw/us/ev_party/co/att (CO 2022 daily workbooks), raw/us/ev_party/la (LA early-vote and registration PDFs)
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
from datetime import date, timedelta
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


# ----------------------------------------------------------------------------------- early vote by group, every state
# One table: group x state. Sources (all aggregates): NC per-voter files (above), CO official daily workbook, SC official absentee
# statistics by race, LA official early-vote statistics, UF Election Lab national CSV for every other state with a breakdown.
# Inputs live in $BRUNDER_DATA/raw/us/ev_groups/ (co_<date>.xlsx, sc_<date>.xlsx, sc_2022-GE-final.xlsx, uf_<date>.csv: copied from
# the server collector's raw/ directory) plus raw/us/ev_party/{co/att,la}.
G_RACE = ["White", "Black", "Hispanic", "Asian", "Other / unknown"]
G_GENDER = ["Female", "Male", "Undisclosed"]
G_CO_AGE = ["Under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"]
GROUP_ROWS = ([("race", "Race / ethnicity", g) for g in G_RACE] + [("age", "Age", g) for g in AGE_BANDS]
              + [("gender", "Gender", g) for g in G_GENDER]
              + [("age_co", "Age, Colorado's own bands", g) for g in G_CO_AGE[1:]])
UF_NAME = "UF Election Lab (compiled from official state sources), used with permission"
UF_LINK = "https://election.lab.ufl.edu/early-vote/2026-early-voting"
STATE_NAME = {"ga": "Georgia", "la": "Louisiana", "nc": "North Carolina", "sc": "South Carolina", "co": "Colorado", "mi": "Michigan",
              "nv": "Nevada", "pa": "Pennsylvania", "ks": "Kansas", "id": "Idaho", "ia": "Iowa", "ct": "Connecticut",
              "de": "Delaware", "ok": "Oklahoma", "ri": "Rhode Island", "vt": "Vermont", "wa": "Washington"}


def _share(counts: dict[str, int]) -> dict[str, float]:
    t = sum(counts.values())
    return {k: 100 * v / t for k, v in counts.items()} if t else {}


def _cell(now: dict, past: dict | None, reg: dict | None, g: str, n_now: dict | None = None) -> dict | None:
    if g not in now:
        return None
    c = {"share": _r(now[g]), "n": (n_now or {}).get(g)}
    if past is not None and g in past:
        c["share_2022"] = _r(past[g])
        c["change_pp"] = _r(now[g] - past[g])
    if reg and g in reg:
        c["reg_share"] = _r(reg[g])
    return c


def _entry(state, *, total, as_of, source, url, groups, coverage, basis=None, past_label=None, notes=""):
    return {"state": state, "name": STATE_NAME[state], "total": total, "as_of": as_of, "source": source, "url": url,
            "groups": groups, "coverage": {k: _r(v, 1) for k, v in coverage.items()}, "baseline_2022": basis,
            "baseline_label": past_label, "notes": notes}


def _latest(d: Path, pat: str) -> Path | None:
    f = sorted(d.glob(pat))
    return f[-1] if f else None


def uf_states(csv_path: Path) -> dict[str, dict]:
    """UF Election Lab CSV -> {state: {'as_of', 'total', 'race', 'age', 'gender' (counts of ALL ballots voted with the group recorded)}}."""
    import io

    import us_ev_src_f as sf
    out: dict[str, dict] = {}
    body = csv_path.read_bytes()
    for r in sf.parse_uf_groups(body):
        if r["measure"] != "total":
            continue
        dim, lab = r["age_band"].split(":", 1)
        d = out.setdefault(r["state"], {"as_of": r["as_of_date"], "race": {}, "age": {}, "gender": {}})
        d[dim][lab] = r["count"]
    tot = pd.read_csv(io.BytesIO(body))
    for _, r in tot.iterrows():
        if str(r["state_abbv"]).lower() in out:
            out[str(r["state_abbv"]).lower()]["total"] = int(r["voted_all"])
    return out


def _uf_groups(u: dict) -> tuple[dict, dict, dict, dict]:
    """UF counts -> (race, age, gender, coverage) in the page's group labels."""
    race = {"White": u["race"].get("White", 0), "Black": u["race"].get("Black", 0), "Hispanic": u["race"].get("Hispanic", 0),
            "Asian": u["race"].get("Asian", 0),
            "Other / unknown": u["race"].get("Native American", 0) + u["race"].get("Other", 0)}
    race = {k: v for k, v in race.items() if sum(u["race"].values())}
    age = dict(u["age"])
    gender = dict(u["gender"])
    cov = {k: 100 * sum(v.values()) / u["total"] for k, v in (("race", u["race"]), ("age", age), ("gender", gender)) if v}
    return race, age, gender, cov


def build_groups(nc_ev: dict | None, nc_reg: dict | None, log=print) -> dict | None:
    import openpyxl

    import us_ev_db as db
    import us_ev_src_f as sf
    raw = db.data_root() / "raw" / "us"
    gdir = raw / "ev_groups"
    uf_file = _latest(gdir, "uf_*.csv")
    if uf_file is None:
        return None
    uf = uf_states(uf_file)
    entries: dict[str, dict] = {}

    # ---- NC: official per-voter files, same day 2022; registered share from the voter_stats files
    if nc_ev:
        reg = {(r["dim"], r["group"]): r["share_now"] for r in (nc_reg or {}).get("rows", [])}
        rows = {(r["dim"], r["group"]): r for r in nc_ev["rows"]}
        groups = {}
        for dim, _, g in GROUP_ROWS:
            r = rows.get((dim, g))
            if r and r["share_now"] is not None:
                groups[f"{dim}:{g}"] = {"share": r["share_now"], "n": r["n_now"], "share_2022": r["share_2022"],
                                        "change_pp": r["share_change_pp"], "reg_share": reg.get((dim, g))}
        entries["nc"] = _entry("nc", total=nc_ev["total_now"], as_of=nc_ev["through_2026"], source="NC State Board of Elections "
                               "per-voter absentee and one-stop files (public record)", url=nc_ev["url"], groups=groups,
                               coverage={"race": 100, "age": 100, "gender": 100}, basis="same day",
                               past_label=f"{nc_ev['through_2022']} (same number of days before Election Day)",
                               notes="Registered share: NC voter_stats file " + (nc_reg or {}).get("asof_now", ""))
        u = uf.get("nc")
        if u:
            rc, ag, gd, _ = _uf_groups(u)
            entries["nc"]["check_uf"] = {"white_share_uf": _r(_share(rc)["White"]), "white_share_official": rows[("race", "White")]["share_now"],
                                         "uf_total": u["total"], "official_total": nc_ev["total_now"]}

    # ---- CO: official workbook, gender + age bands, same day 2022; four-band age and total from UF
    co26 = _latest(gdir, "co_20*.xlsx")
    if co26:
        d26 = date.fromisoformat(co26.stem[3:])
        k = (ED26 - d26).days
        d22 = ED22 - timedelta(days=k)
        f22 = next(iter(sorted((raw / "ev_party" / "co" / "att").glob(f"2022_{d22:%Y%m%d}ElectionActivity*.xlsx"))), None)

        def co_counts(path):
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            gender, age = sf.parse_co_genderage({w.title.lower(): w for w in wb.worksheets}["all_returned_ballots_genderage"])
            return gender, {sf.CO_AGE_LABEL.get(a, a.title()): n for a, n in age.items()}
        g26, a26 = co_counts(co26)
        g22, a22 = co_counts(f22) if f22 else ({}, {})
        tot26 = sum(g26.values())
        gmap = {"Female": "Female", "Male": "Male", "Other / undisclosed": "Undisclosed"}
        gn, gp = _share({gmap[k]: v for k, v in g26.items()}), _share({gmap[k]: v for k, v in g22.items()})
        an, ap = _share({k: v for k, v in a26.items() if k != "Under 18"}), _share({k: v for k, v in a22.items() if k != "Under 18"})
        groups = {}
        for g in G_GENDER:
            c = _cell(gn, gp or None, None, g, {gmap[k]: v for k, v in g26.items()})
            if c:
                groups[f"gender:{g}"] = c
        for g in G_CO_AGE[1:]:
            c = _cell(an, ap or None, None, g, a26)
            if c:
                groups[f"age_co:{g}"] = c
        cov = {"gender": 100, "age": 100 * sum(v for k, v in a26.items() if k != "Under 18") / tot26}
        u = uf.get("co")
        if u:
            _, ag, _, _ = _uf_groups(u)
            for g, c in ((g, _cell(_share(ag), None, None, g, ag)) for g in AGE_BANDS):
                if c:
                    groups[f"age:{g}"] = c
        entries["co"] = _entry("co", total=tot26, as_of=d26.isoformat(), source="Colorado Secretary of State daily ballot-return "
                               "workbook (all returned ballots, mail and in person)" + ("; 4-band age: " + UF_NAME if u else ""),
                               url="https://www.coloradosos.gov/pubs/newsRoom/pressReleases/index.html", groups=groups,
                               coverage=cov, basis="same day" if f22 else None,
                               past_label=f"{d22.isoformat()} (same number of days before Election Day)" if f22 else None,
                               notes="Under-18 pre-registrants are left out of age shares. Race: not published.")

    # ---- SC: official absentee statistics by race (mail ballots returned); 2022 only as a final count
    sc26 = _latest(gdir, "sc_20*-*.xlsx")
    sc22 = gdir / "sc_2022-GE-final.xlsx"
    if sc26 and sc22.exists() and not sc26.name.startswith("sc_2022"):
        def sc_race(path, as_of):
            rows = sf.parse_sc_race(path.read_bytes(), as_of)
            c = {r["age_band"].split(":", 1)[1]: r["count"] for r in rows if r["measure"] == "mail_returned"}
            return {"White": c["White"], "Black": c["Black"], "Hispanic": c["Hispanic"], "Asian": c["Asian"],
                    "Other / unknown": sum(c[k] for k in ("Native American", "Multiple", "Other", "Unknown"))}
        d = sc26.stem[3:]
        r26, r22 = sc_race(sc26, d), sc_race(sc22, "2022-12-20")
        sn, sp = _share(r26), _share(r22)
        groups = {f"race:{g}": _cell(sn, sp, None, g, r26) for g in G_RACE}
        u = uf.get("sc")
        entries["sc"] = _entry("sc", total=(u or {}).get("total", sum(r26.values())), as_of=d, source="South Carolina Election Commission "
                               "absentee statistics by race (mail ballots returned, incl. military/overseas)",
                               url="https://scvotes.gov/elections-statistics/voter-participation/", groups=groups,
                               coverage={"race": 100 * sum(r26.values()) / (u or {"total": sum(r26.values())})["total"]},
                               basis="final", past_label=f"2022 final count of returned absentee ballots ({sum(r22.values()):,}); no same-day file is published",
                               notes="Race is published for mail absentee ballots only; the in-person early-vote file has no race.")

    # ---- LA: official early-vote statistics (complete early-voting period) vs 2022; registered share by race
    la = raw / "ev_party" / "la"
    if (la / "2026_1103_StatewideStats.pdf").exists() and (la / "2022_1108_StatewideStats.pdf").exists():
        a = sf.parse_la_ev_stats(sf.pdf_text(la / "2026_1103_StatewideStats.pdf"))
        b = sf.parse_la_ev_stats(sf.pdf_text(la / "2022_1108_StatewideStats.pdf"))
        lreg = sf.parse_la_registration(sf.pdf_text(la / "reg" / "2026_1001_sta_comb.pdf"))

        def la_counts(x, dim, labs):
            return {lb: x["groups"][f"{dim}:{lb}"] for lb in labs}
        rn = la_counts(a, "race", ["White", "Black", "Other"])
        rp = la_counts(b, "race", ["White", "Black", "Other"])
        ren = lambda c: {"White": c["White"], "Black": c["Black"], "Other / unknown": c["Other"]}   # noqa: E731
        gl = ["Female", "Male", "Undisclosed"]
        gnow, gpast = la_counts(a, "gender", gl), la_counts(b, "gender", gl)
        sr, sp2 = _share(ren(rn)), _share(ren(rp))
        rg = _share({"White": lreg["White"], "Black": lreg["Black"], "Other / unknown": lreg["Other"]})
        sg, sgp = _share(gnow), _share(gpast)
        groups = {}
        for g in ("White", "Black", "Other / unknown"):
            groups[f"race:{g}"] = _cell(sr, sp2, rg, g, ren(rn))
        for g in gl:
            groups[f"gender:{g}"] = _cell(sg, sgp, None, g, gnow)
        entries["la"] = _entry("la", total=a["votes"], as_of=a["end_date"], source="Louisiana Secretary of State statewide early-voting "
                               "statistical reports (PDF); registered share: Louisiana registration statistics, 2026-10-01",
                               url="https://electionstatistics.sos.la.gov/default.aspx?Stats=Early_Voting_Statistics&Type=Statewide",
                               groups=groups, coverage={"race": 100, "gender": 100 * sum(gnow.values()) / a["votes"]},
                               basis="complete early vote", past_label=f"2022 complete early-voting report ({b['votes']:,} ballots, through {b['end_date']})",
                               notes="Louisiana's early voting is over in both years, so the two totals compare whole early-voting periods. "
                                     "The state reports White, Black and Other only.")

    # ---- every other state with a breakdown: UF Election Lab, share now only
    for st in ("ga", "mi", "nv", "pa", "ks", "id", "ia", "ct", "de", "ok", "ri", "vt", "wa"):
        u = uf.get(st)
        if not u:
            continue
        race, age, gender, cov = _uf_groups(u)
        rn, an, gn = _share(race), _share(age), _share(gender)
        groups = {}
        for g in G_RACE:
            c = _cell(rn, None, None, g, race)
            if c:
                groups[f"race:{g}"] = c
        for g in AGE_BANDS:
            c = _cell(an, None, None, g, age)
            if c:
                groups[f"age:{g}"] = c
        for g in G_GENDER:
            c = _cell(gn, None, None, g, gender)
            if c:
                groups[f"gender:{g}"] = c
        entries[st] = _entry(st, total=u["total"], as_of=u["as_of"], source=UF_NAME, url=UF_LINK, groups=groups, coverage=cov,
                             notes="No same-day 2022 breakdown is published for this state.")
    order = sorted(entries.values(), key=lambda e: (e["baseline_2022"] is None, -e["total"]))
    return {"built": date.today().isoformat(), "rows": [{"dim": d, "dim_label": dl, "group": g, "key": f"{d}:{g}"} for d, dl, g in GROUP_ROWS],
            "states": order, "uf_file": uf_file.name,
            "note": "Share = the group's share of the ballots that have the attribute recorded; coverage = those ballots as a share of all "
                    "ballots counted. Change = percentage points vs the 2022 baseline named per state."}


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
    grp = build_groups(ev, reg)
    write("ev_groups.json", grp)
    write("ev_nc.json", ev)
    write("reg_nc_mix.json", reg)
    write("registration.json", load_registration())
    write("issues.json", load_issues())
    write("crosstabs.json", load_crosstabs())
    write("meta.json", {"built": date.today().isoformat(), "ev_nc": bool(ev), "reg_mix": bool(reg), "ev_groups": bool(grp)})
    print("ok", bool(ev), bool(reg), bool(grp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
