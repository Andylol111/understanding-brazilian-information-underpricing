"""Early-vote collectors, batch F: who has voted (race, age, gender), statewide.

Group counts are stored in `ev_state` with party='ALL' and the group in `age_band` as '<dimension>:<label>'
(race:White, age:26-40, gender:Female), so rows that carry no group (age_band='ALL') are untouched.

  sc_absentee_race       SC absentee statistics by race, parsed from the sc_absentee_county snapshots (no extra request)
  co_activity_groups     CO daily activity workbook, gender and age sheets, parsed from the co_ev_county snapshots (no extra request)
  uf_election_lab_groups UF Election Lab national CSV: race, age and gender columns for every state that reports them
  la_ev_stats            LA statewide early-vote statistics PDFs (race, sex): parser only, the host's robots.txt forbids fetching

Parsers are pure functions of bytes/text. Aggregates only; nothing here is voter-level.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

import us_ev_db as db

# ======================================================================================= shared

SRC_SC, SRC_CO, SRC_UF, SRC_LA = "sc_absentee_race", "co_activity_groups", "uf_election_lab_groups", "la_ev_stats"


def group_key(dim: str, label: str) -> str:
    return f"{dim}:{label}"


def _rows(state: str, as_of: str, measure: str, groups: dict[str, int]) -> list[dict]:
    return [{"state": state, "as_of_date": as_of, "measure": measure, "party": "ALL", "age_band": g, "count": int(n)}
            for g, n in groups.items()]


def _derive(st: db.Store, from_source: str, source_id: str, state: str, part_prefix: str, parse) -> None:
    """Parse every valid stored snapshot of another source (no request) into this source's rows, once per file."""
    todo = st.db.execute("SELECT part, file, url FROM snapshots WHERE source_id=? AND valid=1 AND file IS NOT NULL AND file<>'' "
                         "AND part LIKE ? ORDER BY snapshot_id", (from_source, part_prefix + "%")).fetchall()
    for part, rel, url in todo:
        path = st.root_path(rel)
        sid = st.register_existing(source_id, path, state=state, url=url or "", part=part)
        if sid is None or st.db.execute("SELECT parsed FROM snapshots WHERE snapshot_id=?", (sid,)).fetchone()[0]:
            continue
        try:
            rows = parse(path.read_bytes(), part)
        except Exception as e:                                   # noqa: BLE001 - one bad file must not stop the others
            st.log(source_id, "failed", state=state, part=part, message=f"{type(e).__name__}: {e}", snapshot_id=sid)
            continue
        st.add_rows(rows, source_id=source_id, snapshot_id=sid, level="state")


# ======================================================================================= SC

SC_RACE = {"black/aa": "Black", "white": "White", "asian": "Asian", "hispanic": "Hispanic", "native american": "Native American",
           "multiple": "Multiple", "other": "Other", "unknown": "Unknown"}


def parse_sc_race(body: bytes, as_of: str) -> list[dict]:
    """`<date>-Absentee-Statistics-by-Race-for-Election.xlsx` (9 sheets: ALL + 8 races) -> statewide absentee ballots issued
    (mail_requested) and returned (mail_returned) by race. The races must add up to the 'Race ALL' sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    out: dict[str, tuple[int, int]] = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        title = next((str(r[1]).strip() for r in rows if len(r) > 1 and isinstance(r[1], str) and r[1].strip().startswith("Race ")), None)
        tot = next((r for r in rows if len(r) > 11 and r[1] == "Total"), None)
        if title is None or tot is None:
            continue
        out[title[5:].strip().lower()] = (db.num(tot[8]), db.num(tot[11]))
    if "all" not in out or not set(SC_RACE) <= set(out):
        raise ValueError("SC absentee xlsx: race sheets missing")
    for i, what in ((0, "issued"), (1, "returned")):
        if sum(out[k][i] for k in SC_RACE) != out["all"][i]:
            raise ValueError(f"SC absentee xlsx: races do not add up to the ALL sheet ({what})")
    return (_rows("sc", as_of, "mail_requested", {group_key("race", SC_RACE[k]): out[k][0] for k in SC_RACE})
            + _rows("sc", as_of, "mail_returned", {group_key("race", SC_RACE[k]): out[k][1] for k in SC_RACE}))


db.register(db.Source("sc", SRC_SC, "https://scvotes.gov/elections-statistics/voter-participation/", "state", "xlsx",
                      "daily (business days)", notes=(
    "Derived from the stored sc_absentee_county workbooks (the 'Race <name>' sheets, Total row): absentee ballots issued "
    "(mail_requested) and returned (mail_returned) by race, group key race:<White|Black|Asian|Hispanic|Native American|Multiple|"
    "Other|Unknown>. Mail absentee only: the in-person early-vote file (sc_ev_county) has no race. as_of = date in the file name "
    "[INFERENCE: through that day]. 2022 comparison: scvotes.gov '2022-Historical-Absentee-EV-Reports.zip' holds only the final "
    "post-election 'GE' workbook (same layout, parse_sc_race), not a same-day series."),
    fn=lambda st: _derive(st, "sc_absentee_county", SRC_SC, "sc", "20", lambda b, part: parse_sc_race(b, part))))


# ======================================================================================= CO

CO_GA_SHEETS = {"all_returned_ballots_genderage": "total", "returned_mail_ballots_genderage": "mail_returned",
                "in_person_ballots_genderage": "inperson_early"}
CO_AGES = ("<18", "18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75 AND OVER")
CO_AGE_LABEL = {"<18": "Under 18", "75 AND OVER": "75+"}
CO_GENDER = {"female": "Female", "male": "Male", "not disclosed": "Other / undisclosed", "unknown": "Other / undisclosed",
             "x": "Other / undisclosed"}


def parse_co_genderage(ws) -> tuple[dict[str, int], dict[str, int]]:
    """One '<...>_GenderAge' sheet -> (gender counts, age counts), both from the GRAND TOTAL column (all parties)."""
    rows = [r for r in ws.iter_rows(values_only=True)]
    hi = next(i for i, r in enumerate(rows) if r and str(r[0]).strip().upper() == "GENDER/AGE RANGE")
    gcol = next(j for j, c in enumerate(rows[hi]) if str(c).strip().upper() == "GRAND TOTAL")
    gender: dict[str, int] = {}
    age: dict[str, int] = {a: 0 for a in CO_AGES}
    grand = None
    for r in rows[hi + 1:]:
        lab = str(r[0]).strip() if r and r[0] is not None else ""
        if not lab or r[gcol] is None:
            continue
        if lab.lower() in CO_GENDER:
            g = CO_GENDER[lab.lower()]
            gender[g] = gender.get(g, 0) + db.num(r[gcol])
        elif lab.upper() in CO_AGES:
            age[lab.upper()] += db.num(r[gcol])
        elif lab.lower() == "grand total":
            grand = db.num(r[gcol])
            break
    if grand is None or sum(gender.values()) != grand or sum(age.values()) != grand:
        raise ValueError(f"CO gender/age sheet: gender {sum(gender.values())} / age {sum(age.values())} != grand total {grand}")
    return gender, age


def parse_co_groups(body: bytes, as_of: str) -> list[dict]:
    """`<date>GenElectionActivity.xlsx` -> statewide gender and age-band counts of returned ballots (total, mail, in person)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    sheets = {ws.title.lower(): ws for ws in wb.worksheets}
    out: list[dict] = []
    for stem, measure in CO_GA_SHEETS.items():
        gender, age = parse_co_genderage(sheets[stem])
        out += _rows("co", as_of, measure, {group_key("gender", g): n for g, n in gender.items()})
        out += _rows("co", as_of, measure, {group_key("age", CO_AGE_LABEL.get(a, a.title())): n for a, n in age.items()})
    return out


def _co_part_date(part: str) -> str:
    m = re.fullmatch(r"xl-(\d{4})(\d{2})(\d{2}).*", part)
    if not m:
        raise ValueError(f"CO part {part!r}")
    return f"{m[1]}-{m[2]}-{m[3]}"


db.register(db.Source("co", SRC_CO, "https://www.coloradosos.gov/pubs/newsRoom/pressReleases/index.html", "state", "xlsx",
                      "weekdays", notes=(
    "Derived from the stored co_ev_county workbooks (sheets *_GenderAge, GRAND TOTAL column, all parties): statewide returned "
    "ballots (total, mail_returned, inperson_early) by gender (Female, Male, Other / undisclosed = Not Disclosed + X) and age band "
    "(Under 18 = pre-registrants, 18-24, 25-34, 35-44, 45-54, 55-64, 65-74, 75+), group keys gender:<> and age:<>. as_of = date in "
    "the file name ('data through 11:59 PM'). Same workbook exists for 2022 (data under $BRUNDER_DATA/raw/us/ev_party/co/att)."),
    fn=lambda st: _derive(st, "co_ev_county", SRC_CO, "co", "xl-", lambda b, part: parse_co_groups(b, _co_part_date(part)))))


# ======================================================================================= UF Election Lab

UF_URL = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"
UF_MEASURE = {"request": "mail_requested", "accept": "mail_accepted", "inperson": "inperson_early", "voted": "total"}
UF_GROUPS = {"nh_white": ("race", "White"), "nh_black": ("race", "Black"), "hispanic": ("race", "Hispanic"),
             "nh_asian": ("race", "Asian"), "nh_native_american": ("race", "Native American"), "nh_other": ("race", "Other"),
             "age_1": ("age", "18-25"), "age_2": ("age", "26-40"), "age_3": ("age", "41-65"), "age_4": ("age", "66+"),
             "male": ("gender", "Male"), "female": ("gender", "Female"), "gender_unknown": ("gender", "Undisclosed")}


def parse_uf_groups(body: bytes, min_states: int = 40) -> list[dict]:
    """UF Election Lab national CSV -> statewide group counts (race / age / gender columns, as published).
    A 0 means 'not reported' for that state and is not stored. nh_other = non-Hispanic other races (LA: all non-White, non-Black)."""
    import pandas as pd
    d = pd.read_csv(io.BytesIO(body))
    need = {"state_abbv", "last_update", "voted_all", "voted_nh_white", "voted_age_1", "voted_male"}
    if not need <= set(d.columns) or len(d) < min_states:
        raise ValueError("unexpected UF Election Lab CSV layout")
    rows = []
    for _, r in d.iterrows():
        as_of = datetime.strptime(str(r["last_update"]).strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
        for pre, measure in UF_MEASURE.items():
            cells = {}
            for suf, (dim, lab) in UF_GROUPS.items():
                v = r.get(f"{pre}_{suf}")
                if pd.notna(v) and int(v) > 0:
                    cells[group_key(dim, lab)] = int(v)
            rows += _rows(str(r["state_abbv"]).lower(), as_of, measure, cells)
    return rows


@db.source("us", SRC_UF, UF_URL, kind="secondary", format="csv", cadence="daily (update stamp per state)",
           licence="UF Election Lab (compiled from official state sources), used with permission of Dr. Michael McDonald; "
                   "link https://election.lab.ufl.edu/early-vote/2026-early-voting",
           notes="SECONDARY compilation, same file as uf_election_lab (own source_id, group columns only): race (nh_white, nh_black, "
                 "hispanic, nh_asian, nh_native_american, nh_other), age (age_1..4 = 18-25, 26-40, 41-65, 66+) and gender (male, "
                 "female, unknown) for voted (total), accept (mail_accepted), inperson and request (mail_requested); group key "
                 "<dim>:<label> in age_band, party ALL; 0 = not reported = not stored. Many states report a group only for part of "
                 "the ballots (e.g. SC race = mail only, CT age = mail only): compare the group sum with the state's 'ALL' total "
                 "before reading a share. NC figures equal the official NC per-voter files. as_of = the state's own 'last_update'.")
def collect_uf_groups(st):
    r = st.fetch(SRC_UF, UF_URL, ext="csv", validate=parse_uf_groups)
    if r.needs_parse:
        st.add_rows(parse_uf_groups(r.body), source_id=SRC_UF, snapshot_id=r.snapshot_id, level="state")


# ======================================================================================= LA

_NUM = re.compile(r"\d[\d,]*")


def _la_after(lines: list[str], label: str, n: int) -> list[int]:
    i = next(k for k, x in enumerate(lines) if x.strip().upper() == label)
    vals = []
    for x in lines[i + 1:]:
        if _NUM.fullmatch(x.strip()):
            vals.append(db.num(x))
            if len(vals) == n:
                return vals
        else:
            break
    raise ValueError(f"LA stats: {label} row incomplete")


def parse_la_ev_stats(text: str) -> dict:
    """Louisiana 'Statewide Early Voting Statistical Report' (pypdf text) -> {'end_date', 'rows' (STATE TOTAL: votes, race, sex)}.
    Race = White / Black / Other (the state reports no more); sex = Male / Female. Race must add up to the votes; the few ballots with no sex on file are reported as gender:Undisclosed (votes minus male minus female)."""
    lines = text.splitlines()
    votes, white, black, other, male, female = _la_after(lines, "STATE TOTAL", 6)
    if white + black + other != votes or male + female > votes:
        raise ValueError("LA stats: race does not add up to the votes, or sex exceeds them")
    m = re.search(r"End Date (\d{1,2})/(\d{1,2})/(\d{4})", text)
    end = f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}" if m else None
    g = {group_key("race", "White"): white, group_key("race", "Black"): black, group_key("race", "Other"): other,
         group_key("gender", "Male"): male, group_key("gender", "Female"): female,
         group_key("gender", "Undisclosed"): votes - male - female}
    return {"end_date": end, "votes": votes, "groups": g}


def parse_la_registration(text: str) -> dict:
    """Louisiana registration statistics (pypdf text, 'STATE' row) -> total, White, Black, Other registered voters."""
    lines = text.splitlines()
    total, white, black, other = _la_after(lines, "STATE", 4)
    if white + black + other != total:
        raise ValueError("LA registration: races do not add up")
    return {"total": total, "White": white, "Black": black, "Other": other}


def pdf_text(path: Path) -> str:
    import pypdf
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages)


db.not_collected("la", SRC_LA, "https://electionstatistics.sos.la.gov/default.aspx?Stats=Early_Voting_Statistics&Type=Statewide",
                 access="blocked: robots.txt 'User-agent: * Disallow: /' on electionstatistics.sos.la.gov (see la_ev_parish)",
                 notes="Statewide Early Voting Statistical Report PDFs (race White/Black/Other, sex, party) and the registration "
                       "'sta_comb' PDFs already on the SSD (raw/us/ev_party/la); parse_la_ev_stats / parse_la_registration read them. "
                       "The 2026 file runs to End Date 10/29/2026 (early voting over), the 2022 file to 11/15/2022: both complete "
                       "early-vote totals.", cadence="per election", format="pdf", kind="state")
