"""Early vote by group, one entry per state, in the harmonised taxonomy (us_ctx_tax) with past-year baselines.

Sources (all aggregates): NC per-voter files (us_ctx_nc), CO official daily workbooks / 2018 PDF report, SC official absentee
statistics by race, LA official early-vote statistics, UF Election Lab national CSV for every other state with a breakdown.
Inputs live under $BRUNDER_DATA/raw/us/: ev_groups/ (co_<date>.xlsx, sc_<date>.xlsx, sc_2022-GE-final.xlsx, uf_<date>.csv),
ev_party/co/att (CO daily workbooks 2020-2024, 2018 PDF text), ev_party/la (LA PDFs). A past year is listed only when its file exists."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import us_ctx_nc as nc
import us_ctx_tax as tx
from us_ctx_tax import r2, share  # noqa: F401

UF_NAME = "UF Election Lab (compiled from official state sources), used with permission"
UF_LINK = "https://election.lab.ufl.edu/early-vote/2026-early-voting"
STATE_NAME = {"ga": "Georgia", "la": "Louisiana", "nc": "North Carolina", "sc": "South Carolina", "co": "Colorado", "mi": "Michigan",
              "nv": "Nevada", "pa": "Pennsylvania", "ks": "Kansas", "id": "Idaho", "ia": "Iowa", "ct": "Connecticut",
              "de": "Delaware", "ok": "Oklahoma", "ri": "Rhode Island", "vt": "Vermont", "wa": "Washington"}
PAST_YEARS = [y["year"] for y in tx.YEARS]
SAME_DAY = "same number of days before Election Day"


def _entry(state, *, total, as_of, source, url, now, past=None, reg=None, coverage, baselines=None, notes=""):
    groups, own = tx.cells(now, past, reg)
    return {"state": state, "name": STATE_NAME[state], "source": source, "url": url, "total": int(total), "as_of": as_of,
            "coverage": {k: r2(v, 1) for k, v in coverage.items()}, "groups": groups, "own": own,
            "baselines": dict(sorted((baselines or {}).items(), reverse=True)), "notes": notes}


def _base(year, basis, label, total, note=None):
    b = {"basis": basis, "label": label, "total": int(total)}
    if note:
        b["note"] = note
    return str(year), b


def _same_day_label(d: str) -> str:
    return f"{d}, {SAME_DAY}"


# ------------------------------------------------------------------------------------------------------------- NC
def nc_entry(c: dict, stats: dict, reg_shares: dict, reg_asof: str, log=print) -> dict:
    now_cnt = c[2026]
    last = max(d for d in {k[0] for k in now_cnt} if d <= tx.ELECTION_DAY[2026])      # ignore mistyped dates after Election Day
    now = nc.cumulate(now_cnt, last)
    past, bases = {}, {}
    for y in nc.EV_YEARS:
        through = nc.same_day(y, last)
        past[y] = nc.cumulate(c[y], through)
        tot = past[y]["_total"]["all"]
        note = None
        if y == 2018:
            m, u = stats[2018].get("matched", 0), stats[2018].get("unmatched", 0)
            note = (f"the 2018 file has no ethnicity column; ethnicity joined from the 2018-11-06 voter snapshot by voter id "
                    f"({100 * m / (m + u):.2f}% of ballots matched; the rest count by race alone)")
        k, b = _base(y, "same day", _same_day_label(through), tot, note)
        bases[k] = b
        log(f"  nc {y}: through {through}: {tot:,}" + (f" ({note})" if note else ""))
    dims = {d: dict(now[d]) for d in nc.EV_DIMS}
    pdims = {y: {d: dict(p[d]) for d in nc.EV_DIMS} for y, p in past.items()}
    return _entry("nc", total=now["_total"]["all"], as_of=last, source="NC State Board of Elections, per-voter absentee and "
                  "one-stop files (public record)", url="https://www.ncsbe.gov/results-data/absentee-data", now=dims, past=pdims,
                  reg=reg_shares, coverage={"race": 100, "age": 100, "gender": 100}, baselines=bases,
                  notes=f"Accepted ballots by return day. Registered share: NC voter_stats file of {reg_asof}.")


# ------------------------------------------------------------------------------------------------------------- UF
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


def uf_groups(u: dict) -> tuple[dict, dict, dict, dict]:
    """UF counts -> (race in harmonised groups, age in UF's own bands, gender, coverage); native American + other = Other / unknown."""
    r = u["race"]
    race = {"White": r.get("White", 0), "Black": r.get("Black", 0), "Hispanic": r.get("Hispanic", 0), "Asian": r.get("Asian", 0),
            "Other / unknown": r.get("Native American", 0) + r.get("Other", 0)}
    race = {k: v for k, v in race.items() if sum(r.values())}
    age, gender = dict(u["age"]), dict(u["gender"])
    cov = {k: 100 * sum(v.values()) / u["total"] for k, v in (("race", r), ("age", age), ("gender", gender)) if v}
    return race, age, gender, cov


def uf_entry(st: str, u: dict) -> dict:
    race, age, gender, cov = uf_groups(u)
    now = {k: v for k, v in (("race", race), ("age_uf", age), ("gender", gender)) if v}
    cov = {("age_uf" if k == "age" else k): v for k, v in cov.items()}
    return _entry(st, total=u["total"], as_of=u["as_of"], source=UF_NAME, url=UF_LINK, now=now, coverage=cov,
                  notes="No same-day past-year breakdown exists for this state.")


# ------------------------------------------------------------------------------------------------------------- CO
def _co_workbook(path: Path):
    import openpyxl

    import us_ev_src_f as sf
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    gender, age = sf.parse_co_genderage({w.title.lower(): w for w in wb.worksheets}["all_returned_ballots_genderage"])
    gmap = {"Female": "Female", "Male": "Male", "Other / undisclosed": "Undisclosed"}
    return ({gmap[k]: v for k, v in gender.items()}, {sf.CO_AGE_LABEL.get(a, a.title()): n for a, n in age.items()})


def _co_2018_report(txt: Path) -> dict[str, int]:
    """CO 2018 'Ballots Received to Date by Party and Gender' report (text) -> {Female, Male, Undisclosed} statewide totals."""
    want = {"FEMALE TOTAL": "Female", "MALE TOTAL": "Male", "UNKNOWN TOTAL": "Undisclosed", "GRAND TOTAL": "_all"}
    out: dict[str, int] = {}
    for line in txt.read_text(errors="replace").splitlines():
        s = re.sub(r"\s+", " ", line).strip()                     # the PDF text uses non-breaking spaces
        for head, g in want.items():
            if s.upper().startswith(head) and g not in out:
                nums = re.findall(r"\d[\d,]*", s[len(head):])
                if nums:
                    out[g] = int(nums[-1].replace(",", ""))
    if set(out) != set(want.values()) or out["Female"] + out["Male"] + out["Undisclosed"] != out["_all"]:
        raise ValueError(f"CO 2018 report {txt.name}: gender totals do not add up: {out}")
    return out


def _co_dims(gender: dict, age: dict) -> dict[str, dict]:
    """CO counts -> {gender, age (harmonised: only the groups that union exactly), age_co (all bands)}; age shares are of ballots
    with an age of 18 or more."""
    a = {b: age.get(b, 0) for b in tx.CO_BANDS}
    den = sum(a.values())
    return {"gender": dict(gender), "age": {"45-64": a["45-54"] + a["55-64"], "65+": a["65-74"] + a["75+"], "_den": den},
            "age_co": a}


def co_entry(raw: Path, uf: dict, log=print) -> dict | None:
    gdir, att = raw / "ev_groups", raw / "ev_party" / "co" / "att"
    files = sorted(gdir.glob("co_20*.xlsx"))
    if not files:
        return None
    path = files[-1]
    d26 = date.fromisoformat(path.stem[3:])
    k = (date.fromisoformat(tx.ELECTION_DAY[2026]) - d26).days
    g26, a26 = _co_workbook(path)
    tot26 = sum(g26.values())
    now = _co_dims(g26, a26)
    past, bases = {}, {}
    for y in PAST_YEARS:
        d = date.fromisoformat(tx.ELECTION_DAY[y]) - timedelta(days=k)
        xl = sorted(att.glob(f"{y}_{d:%Y%m%d}*Activity*.xlsx"))
        if xl:
            g, a = _co_workbook(xl[-1])
            past[y] = _co_dims(g, a)
            kk, b = _base(y, "same day", _same_day_label(d.isoformat()), sum(g.values()))
            bases[kk] = b
            log(f"  co {y}: {xl[-1].name}: {sum(g.values()):,}")
            continue
        pdf = sorted(att.glob(f"{y}_{d:%Y%m%d}BallotsReturned*.txt"))
        if pdf:
            g = _co_2018_report(pdf[-1])
            tot = g.pop("_all")
            past[y] = {"gender": g}
            kk, b = _base(y, "same day", _same_day_label(d.isoformat()), tot,
                          "gender only: that year's report has other age bands (18-25, 26-40, 41-60, 61-70, 71+), and its counts "
                          "include rejected ballots")
            bases[kk] = b
            log(f"  co {y}: {pdf[-1].name}: {tot:,}")
    cov = {"gender": 100, "age_co": 100 * sum(now["age_co"].values()) / tot26}
    cov["age"] = cov["age_co"]
    u = uf.get("co")
    if u:
        _, age, _, _ = uf_groups(u)
        now["age_uf"] = age
        cov["age_uf"] = 100 * sum(age.values()) / u["total"]
    return _entry("co", total=tot26, as_of=d26.isoformat(), source="Colorado Secretary of State daily ballot-return workbook",
                  url="https://www.coloradosos.gov/pubs/newsRoom/pressReleases/index.html", now=now, past=past, coverage=cov,
                  baselines=bases, notes="All returned ballots, mail and in person. Pre-registered voters under 18 are left out of "
                  "age shares. Race is not published." + (f" Own bands age_uf: {UF_NAME}, counted through {u['as_of']}." if u else ""))


# ------------------------------------------------------------------------------------------------------------- SC
def _sc_race(path: Path, as_of: str) -> dict[str, int]:
    import us_ev_src_f as sf
    rows = sf.parse_sc_race(path.read_bytes(), as_of)
    c = {r["age_band"].split(":", 1)[1]: r["count"] for r in rows if r["measure"] == "mail_returned"}
    return {"White": c["White"], "Black": c["Black"], "Hispanic": c["Hispanic"], "Asian": c["Asian"],
            "Other / unknown": sum(c[k] for k in ("Native American", "Multiple", "Other", "Unknown"))}


def sc_entry(raw: Path, uf: dict, log=print) -> dict | None:
    gdir = raw / "ev_groups"
    cur = [f for f in sorted(gdir.glob("sc_20*-*.xlsx")) if not f.name.startswith("sc_2022")]
    f22 = gdir / "sc_2022-GE-final.xlsx"
    if not cur:
        return None
    d = cur[-1].stem[3:]
    r26 = _sc_race(cur[-1], d)
    past, bases = {}, {}
    if f22.exists():
        r22 = _sc_race(f22, "2022-12-20")
        past[2022] = {"race": r22}
        k, b = _base(2022, "final", f"2022 final count of returned absentee ballots ({sum(r22.values()):,}); no same-day file is "
                     "published", sum(r22.values()))
        bases[k] = b
        log(f"  sc 2022 final: {sum(r22.values()):,}")
    u = uf.get("sc")
    tot = (u or {}).get("total", sum(r26.values()))
    return _entry("sc", total=tot, as_of=d, source="South Carolina Election Commission, absentee statistics by race",
                  url="https://scvotes.gov/elections-statistics/voter-participation/", now={"race": r26}, past=past,
                  coverage={"race": 100 * sum(r26.values()) / tot}, baselines=bases,
                  notes="Race is published for mail absentee ballots only (incl. military / overseas); the in-person early-vote "
                        "file has no race. No other past year's file is available.")


# ------------------------------------------------------------------------------------------------------------- LA
def la_entry(raw: Path, log=print) -> dict | None:
    import us_ev_src_f as sf
    la = raw / "ev_party" / "la"
    now_pdf = la / "2026_1103_StatewideStats.pdf"
    if not now_pdf.exists():
        return None
    a = sf.parse_la_ev_stats(sf.pdf_text(now_pdf))
    lreg = sf.parse_la_registration(sf.pdf_text(la / "reg" / "2026_1001_sta_comb.pdf"))

    def dims(x):
        g = x["groups"]
        return {"race_la": {k: g[f"race:{k}"] for k in tx.LA_RACE},
                "gender": {k: g[f"gender:{k}"] for k in tx.GENDER}}

    past, bases = {}, {}
    for y in PAST_YEARS:
        ed = tx.ELECTION_DAY[y]
        f = la / f"{y}_{ed[5:7]}{ed[8:]}_StatewideStats.pdf"
        if not f.exists():
            continue
        b = sf.parse_la_ev_stats(sf.pdf_text(f))
        past[y] = dims(b)
        k, bb = _base(y, "complete", f"{y} complete early-voting report, election of {ed} (through {b['end_date']})", b["votes"])
        bases[k] = bb
        log(f"  la {y}: {b['votes']:,} through {b['end_date']}")
    g = dims(a)["gender"]
    rreg = share({k: lreg[k] for k in tx.LA_RACE})
    return _entry("la", total=a["votes"], as_of=a["end_date"], source="Louisiana Secretary of State, statewide early-voting reports",
                  url="https://electionstatistics.sos.la.gov/default.aspx?Stats=Early_Voting_Statistics&Type=Statewide",
                  now=dims(a), past=past, reg={"race_la": rreg}, coverage={"race_la": 100, "gender": 100 * sum(g.values()) / a["votes"]},
                  baselines=bases, notes="Early voting is over in every year listed, so the totals compare whole early-voting periods. "
                  "The state reports White, Black and Other only. Registered share: Louisiana registration statistics, 2026-10-01.")
