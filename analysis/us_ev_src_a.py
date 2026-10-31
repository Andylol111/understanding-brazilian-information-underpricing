"""Early-vote collectors, batch A: CA, CO, CT, DC, DE, HI, ID (collected) and AL, AK, AZ, AR, GA (recorded as not collected).

Parsers are pure functions of bytes/text (no network); collectors only discover URLs, fetch through `db.Store` and hand the
bytes to the parsers. openpyxl and pypdf are imported lazily. Aggregates only: none of these sources is voter-level.
FL is collected by the core module.
"""
from __future__ import annotations

import csv
import html as _html
import io
import json
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import us_ev_db as db

UTC_TODAY = lambda: db.utcnow().date()          # noqa: E731


# ======================================================================================= helpers

_TAG = re.compile(r"<[^>]+>")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", s))).strip()


def _long_date(s: str) -> date:
    """'October 29, 2026' / 'Friday, October 30, 2026' -> date."""
    m = re.search(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", s)
    if not m or m[1].lower() not in _MONTHS:
        raise ValueError(f"bad date {s!r}")
    return date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))


def _through(ts: datetime, cutoff_hour: int = 9) -> str:
    """Date a timestamped report is cumulative through: a report generated before `cutoff_hour` local time reflects the
    previous day's close, otherwise the same day."""
    return (ts.date() - timedelta(days=1) if ts.hour < cutoff_hour else ts.date()).isoformat()


def _ampm(h: int, ap: str) -> int:
    return h % 12 + (12 if ap.upper() == "PM" else 0)


class _Tables(HTMLParser):
    """Collect <table>s as {'caption', 'rows': [[cell text]]} (no nested tables in the pages we read)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._t = self._row = self._cell = self._cap = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = {"caption": "", "rows": []}
            self.tables.append(self._t)
        elif tag == "caption" and self._t is not None:
            self._cap = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "caption" and self._cap is not None:
            self._t["caption"] = re.sub(r"\s+", " ", "".join(self._cap)).strip()
            self._cap = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._t["rows"].append(self._row)
            self._row = None
        elif tag == "table":
            self._t = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._cap is not None:
            self._cap.append(data)


def _tables(html: str) -> list[dict]:
    p = _Tables()
    p.feed(html)
    return p.tables


def _pdf_text(body: bytes) -> str:
    import pypdf
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(body)).pages)


def _settled(st: db.Store, source_id: str, part: str, day: date, *, need_parsed: bool = True) -> bool:
    """True when `day` is over (before yesterday, UTC) and this part is already parsed, or was absent (403/404) when
    looked for: an immutable past day is not fetched again (keeps expected-missing days out of the failure log)."""
    if day >= UTC_TODAY() - timedelta(days=1):
        return False
    row = st.last_valid(source_id, part)
    if row:
        return bool(row[5]) if need_parsed else True
    return st.db.execute("SELECT 1 FROM snapshots WHERE source_id=? AND part=? AND valid=0 AND http_status IN (403,404) LIMIT 1",
                         (source_id, part)).fetchone() is not None


def _cells(state: str, as_of: str, cells: dict, *, county: str | None = None, age: str | None = None) -> list[dict]:
    rows = db.long_rows(state, as_of, cells, county=county)
    if age:
        for r in rows:
            r["age_band"] = age
    return rows


# ======================================================================================= CO

CO_BASE = "https://www.coloradosos.gov/pubs/newsRoom/pressReleases/2026/"
CO_FIRST = date(2026, 10, 12)
CO_PARTY = {"DEM": "D", "REP": "R", "UAF": "U"}                       # every other code (ACN, APV, CTR, FWD, GRN, LBR/LIB, NOL, UNI) -> other
CO_AGE = {"<18": "<18", "18-24": "18-24", "25-34": "25-34", "35-44": "35-44", "45-54": "45-54", "55-64": "55-64",
          "65-74": "65-74", "75 AND OVER": "75+"}
CO_SHEETS = {"all_returned_ballots": "total", "returned_mail_ballots": "mail_returned", "in_person_ballots": "inperson_early"}


def parse_co_press(html: str) -> dict:
    """Daily 'General Election Ballots Returned' press release -> {'as_of': 'YYYY-MM-DD'|None, 'returned': int|None, 'xlsx': [names]}."""
    text = _text(html)
    m = re.search(r"All data as of [\d:]+\s*[AP]M,? ([A-Za-z]+ \d{1,2}, \d{4})", text)
    t = re.search(r"So far,? ([\d,]{4,}) ballots", text)
    return {"as_of": _long_date(m[1]).isoformat() if m else None,
            "returned": db.num(t[1]) if t else None,
            "xlsx": [a.rsplit("/", 1)[-1] for a in re.findall(r'href="([^"]+\.xlsx)"', html, re.I)]}


def _g(r, j):
    return int(r[j] or 0) if j < len(r) else 0


def _co_party(code: str) -> str:
    return CO_PARTY.get(code, "other")


def _co_rows(sheet, measure: str):
    """'<X>_By_County' sheet -> ({county: {party: n}}, {party: n} from the Grand Total row)."""
    rows = [r for r in sheet.iter_rows(values_only=True)]
    hi = next(i for i, r in enumerate(rows) if r and str(r[0]).strip().upper() == "COUNTY")
    hdr = rows[hi]
    last = max(j for j, c in enumerate(hdr) if c is not None)
    codes = {j: str(hdr[j]).strip().upper() for j in range(1, last)}       # 1..last-1 are party codes, last is the grand total
    counties, state = {}, None
    for r in rows[hi + 1:]:
        if not r or r[0] is None:
            continue
        lab = str(r[0]).strip()
        if lab.lower().startswith("end of"):
            break
        cell = {"D": 0, "R": 0, "U": 0, "other": 0}
        for j, code in codes.items():
            cell[_co_party(code)] += _g(r, j)
        grand = _g(r, last)
        if sum(cell.values()) != grand:
            raise ValueError(f"CO {measure} {lab}: party sum {sum(cell.values())} != grand total {grand}")
        cell["ALL"] = grand
        if lab.lower() == "grand total":
            state = cell
        else:
            counties[lab] = cell
    if state is None:
        raise ValueError(f"CO {measure}: no Grand Total row")
    return counties, state


def _co_age(sheet, measure: str) -> dict[str, dict[str, int]]:
    """'<X>_GenderAge' sheet -> {age band: {party: n}} summed over the gender blocks (no gender in the DB)."""
    rows = [r for r in sheet.iter_rows(values_only=True)]
    hi = next(i for i, r in enumerate(rows) if r and str(r[0] or "").strip().upper().startswith("GENDER"))
    hdr = [str(c).strip() if c is not None else "" for c in rows[hi]]
    cols: dict[int, str] = {}
    grand_col = None
    for j, c in enumerate(hdr):
        m = re.fullmatch(r"([A-Z]{3})(?: Total)?", c)
        if c.upper() == "GRAND TOTAL":
            grand_col = j
        elif m:
            cols[j] = m[1]
    out: dict[str, dict[str, int]] = {}
    grand_total = 0
    for r in rows[hi + 1:]:
        if not r or r[0] is None:
            continue
        lab = str(r[0]).strip().upper()
        if lab.startswith("END OF"):
            break
        if lab == "GRAND TOTAL":
            grand_total = _g(r, grand_col)
            break
        band = CO_AGE.get(lab)
        if band is None:                                                   # a gender header row
            continue
        cell = out.setdefault(band, {"D": 0, "R": 0, "U": 0, "other": 0, "ALL": 0})
        for j, code in cols.items():
            n = _g(r, j)
            cell[_co_party(code)] += n
            cell["ALL"] += n
        if sum(cell[p] for p in ("D", "R", "U", "other")) != cell["ALL"]:
            raise ValueError(f"CO {measure}: age {lab} party sum mismatch")
    if sum(c["ALL"] for c in out.values()) != grand_total:
        raise ValueError(f"CO {measure}: age bands sum {sum(c['ALL'] for c in out.values())} != grand total {grand_total}")
    return out


def parse_co_activity(body: bytes, as_of: str) -> dict[str, list[dict]]:
    """`<date>GenElectionActivity.xlsx` -> {'county': rows, 'state': rows} (party D/R/U/other/ALL; state also by age band)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    sheets = {ws.title.lower(): ws for ws in wb.worksheets}
    county_rows: list[dict] = []
    state_rows: list[dict] = []
    for stem, measure in CO_SHEETS.items():
        counties, state = _co_rows(sheets[f"{stem}_by_county"], measure)
        for name, cell in counties.items():
            county_rows += _cells("co", as_of, {(measure, p): n for p, n in cell.items()}, county=db.county_fips("co", name))
        state_rows += _cells("co", as_of, {(measure, p): n for p, n in state.items()})
        for band, cell in _co_age(sheets[f"{stem}_genderage"], measure).items():
            state_rows += _cells("co", as_of, {(measure, p): n for p, n in cell.items()}, age=band)
    return {"county": county_rows, "state": state_rows}


def _validate_co_press(body: bytes) -> None:
    meta = parse_co_press(body.decode("utf8", "ignore"))
    if not meta["xlsx"] or meta["returned"] is None:
        raise ValueError("not a ballots-returned press release with a spreadsheet")


def _validate_co_xlsx(body: bytes) -> None:
    import openpyxl
    names = {ws.title.lower() for ws in openpyxl.load_workbook(io.BytesIO(body), read_only=True).worksheets}
    for stem in CO_SHEETS:
        if f"{stem}_by_county" not in names or f"{stem}_genderage" not in names:
            raise ValueError(f"missing sheet {stem}")


@db.source("co", "co_ev_county", "https://www.coloradosos.gov/pubs/newsRoom/pressReleases/index.html", kind="county",
           format="xlsx", cadence="weekdays, data through 11:59 PM of the previous day",
           notes="SoS daily 'General Election Ballots Returned' press release + <date>GenElectionActivity.xlsx. County x party "
                 "(DEM=D, REP=R, UAF=U, all minor parties=other) for total returned (mail+in-person), returned mail and in-person "
                 "ballots; statewide from the file's Grand Total row plus party x age band (summed over gender). 'Returned' = "
                 "ballots received by county clerks (includes ballots not yet accepted/cured). mail_returned = returned mail "
                 "ballots; inperson_early = in-person ballots. as_of = 'All data as of 11:59 PM <date>' of the release. "
                 "Releases are probed by weekday (index lists only the last three).")
def collect_co_ev_county(st: db.Store):
    sid = "co_ev_county"
    day = CO_FIRST
    last = min(UTC_TODAY(), date(2026, 11, 13))
    while day <= last:
        if day.weekday() < 5:
            part = f"pr-{day:%Y%m%d}"
            if not _settled(st, sid, part, day):
                pr = st.fetch(sid, f"{CO_BASE}PR{day:%Y%m%d}BallotReturns.html", ext="html", part=part,
                              validate=_validate_co_press)
                if pr.ok:
                    meta = parse_co_press(pr.body.decode("utf8", "ignore"))
                    if pr.needs_parse:
                        st.add_rows([], source_id=sid, snapshot_id=pr.snapshot_id, level="state")
                    for name in meta["xlsx"]:
                        fm = re.match(r"(\d{4})(\d{2})(\d{2})", name)
                        if not fm:
                            continue
                        fday = date(int(fm[1]), int(fm[2]), int(fm[3]))
                        xpart = f"xl-{name.rsplit('.', 1)[0]}"
                        if _settled(st, sid, xpart, fday):
                            continue
                        r = st.fetch(sid, CO_BASE + name, ext="xlsx", part=xpart, validate=_validate_co_xlsx)
                        if r.needs_parse:
                            as_of = meta["as_of"] if (meta["as_of"] and len(meta["xlsx"]) == 1) else fday.isoformat()
                            res = parse_co_activity(r.body, as_of)
                            st.add_rows(res["county"], source_id=sid, snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
                            st.add_rows(res["state"], source_id=sid, snapshot_id=r.snapshot_id, level="state")
        day += timedelta(days=1)


# ======================================================================================= CT

CT_BASE = "https://portal.ct.gov/sots/press-releases/2026-press-releases/"
CT_FIRST = date(2026, 10, 19)                       # first day of early voting; earlier pages do not exist
CT_PARTY = {"all parties": "ALL", "democrats": "D", "republicans": "R", "unaffiliated": "U", "other": "other"}


def _ct_int(s: str) -> int:
    return db.num(s)


def parse_ct_page(html: str) -> dict:
    """SoTS 'Early Voting and Absentee Ballot Totals as of <Month D>' page -> {'as_of', 'rows' (statewide)}.

    Reconciliation is enforced: party columns must sum to 'All Parties' and the summary table must equal the party tables."""
    text = _text(html)
    m = re.search(r"Early Voting and Absentee Ballot Totals as of ([A-Za-z]+) (\d{1,2})", text)
    pub = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", html[html.find('class="date"'):][:200]) if 'class="date"' in html else None
    if not m or not pub:
        raise ValueError("CT: title or publication date not found")
    pubd = date(int(pub[3]), int(pub[1]), int(pub[2]))
    as_of = date(pubd.year, _MONTHS[m[1].lower()], int(m[2]))
    if as_of > pubd:
        as_of = date(pubd.year - 1, as_of.month, as_of.day)
    summary: dict[str, int] = {}
    early: dict[str, int] = {}
    absent: dict[str, int] = {}
    for t in _tables(html):
        cap = t["caption"].lower()
        if "totals" in cap and "statewide early voting and absentee" in cap:
            for r in t["rows"][1:]:
                lab = r[0].lower()
                key = ("total" if lab.startswith("votes cast") else "mail_requested" if "issued" in lab else
                       "mail_returned" if "returned" in lab else "inperson_early" if "early votes" in lab else None)
                if key:
                    summary[key] = _ct_int(r[1])
        elif "early voting daily and cumulative" in cap:
            for r in t["rows"][1:]:
                if r[0].lower() in CT_PARTY:
                    early[CT_PARTY[r[0].lower()]] = _ct_int(r[-1])
        elif "absentee ballot cumulative" in cap:
            for r in t["rows"][1:]:
                if r[0].lower() in CT_PARTY:
                    absent[CT_PARTY[r[0].lower()]] = _ct_int(r[-1])
    if set(summary) != {"total", "mail_requested", "mail_returned", "inperson_early"} or set(early) != set(CT_PARTY.values()) \
            or set(absent) != set(CT_PARTY.values()):
        raise ValueError(f"CT: incomplete tables {sorted(summary)} {sorted(early)} {sorted(absent)}")
    for name, d, key in (("early", early, "inperson_early"), ("absentee", absent, "mail_returned")):
        if sum(v for p, v in d.items() if p != "ALL") != d["ALL"] or d["ALL"] != summary[key]:
            raise ValueError(f"CT: {name} party columns / all-parties / summary do not reconcile")
    if summary["inperson_early"] + summary["mail_returned"] != summary["total"]:
        raise ValueError("CT: votes cast != early + absentee returned")
    cells = {("mail_requested", "ALL"): summary["mail_requested"]}
    for p in CT_PARTY.values():
        cells[("inperson_early", p)] = early[p]
        cells[("mail_returned", p)] = absent[p]
        cells[("total", p)] = early[p] + absent[p]                          # derived: early + absentee returned, same party
    if cells[("total", "ALL")] != summary["total"]:
        raise ValueError("CT: derived total mismatch")
    return {"as_of": as_of.isoformat(), "rows": db.long_rows("ct", as_of.isoformat(), cells)}


def _validate_ct_page(body: bytes) -> None:
    html = body.decode("utf8", "ignore")
    if "Early Voting and Absentee Ballot Totals as of" not in _text(html):
        raise db.Skip("page for this day not published yet (HTTP 200 without the report)")
    parse_ct_page(html)


@db.source("ct", "ct_ev_state", "https://portal.ct.gov/sots/press-releases/2026-press-releases", kind="state", format="html",
           cadence="daily (page for day D published D+1)",
           notes="Secretary of the State daily press release 'Early Voting and Absentee Ballot Totals as of <date>' (flagged "
                 "'unofficial and subject to change'). Statewide, cumulative through the titled date: absentee ballots issued "
                 "(mail_requested, ALL), absentee ballots RETURNED (mail_returned, by party), early votes (inperson_early, by party; "
                 "'to date' column). total = 'Votes cast through all methods' = early + absentee returned; per-party total is "
                 "derived as early + absentee returned of that party. U = Unaffiliated, other = Other. Municipal top-10 early "
                 "totals are not collected (towns do not map to counties).")
def collect_ct_ev_state(st: db.Store):
    sid = "ct_ev_state"
    day = CT_FIRST
    while day <= min(UTC_TODAY(), date(2026, 11, 3)):
        part = f"{day:%Y%m%d}"
        if not _settled(st, sid, part, day):
            slug = f"early-voting-and-absentee-ballot-totals-as-of-{day:%B}-{day.day}".lower()
            r = st.fetch(sid, CT_BASE + slug, ext="html", part=part, validate=_validate_ct_page)
            if r.needs_parse:
                res = parse_ct_page(r.body.decode("utf8", "ignore"))
                st.add_rows(res["rows"], source_id=sid, snapshot_id=r.snapshot_id, level="state")
        day += timedelta(days=1)


# ======================================================================================= DE

DE_URL = "https://elections.delaware.gov/voter/registrationtotals/reports/GE2026_ElectionVoterCountsByVotingMethod.html"


def parse_de_page(html: str) -> dict:
    """'2026 General Election Voter Counts by Voting Method' -> {'as_of', 'county': rows, 'state': rows}."""
    m = re.search(r"Last Updated:\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+ \d{1,2}, \d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)", _text(html))
    if not m:
        raise ValueError("DE: 'Last Updated' not found")
    ts = datetime.combine(_long_date(m[1]), datetime.min.time()).replace(hour=_ampm(int(m[2]), m[4]), minute=int(m[3]))
    as_of = _through(ts)
    tbl = next((t for t in _tables(html) if t["rows"] and t["rows"][0][0].lower().startswith("voting method")), None)
    if tbl is None:
        raise ValueError("DE: table not found")
    hdr = tbl["rows"][0]
    counties = {j: re.sub(r" County$", "", c) for j, c in enumerate(hdr) if c.endswith("County")}
    kind = {"absentee": "mail_returned", "early voting": "inperson_early", "grand totals": "total"}
    per_county: dict[str, dict[tuple, int]] = {c: {} for c in counties.values()}
    state: dict[tuple, int] = {}
    for r in tbl["rows"][1:]:
        meas = kind.get(r[0].lower())
        if meas is None:
            continue
        for j, c in counties.items():
            per_county[c][(meas, "ALL")] = db.num(r[j])
        state[(meas, "ALL")] = db.num(r[-1])
    if set(m for m, _ in state) != set(kind.values()):
        raise ValueError("DE: rows missing")
    for cells in [state, *per_county.values()]:
        if cells[("mail_returned", "ALL")] + cells[("inperson_early", "ALL")] != cells[("total", "ALL")]:
            raise ValueError("DE: absentee + early != grand total")
    if sum(c[("total", "ALL")] for c in per_county.values()) != state[("total", "ALL")]:
        raise ValueError("DE: county totals do not sum to the statewide total")
    return {"as_of": as_of, "state": db.long_rows("de", as_of, state),
            "county": [r for c, cells in per_county.items()
                       for r in db.long_rows("de", as_of, cells, county=db.county_fips("de", c))]}


@db.source("de", "de_ev_county", DE_URL, kind="county", format="html", cadence="daily (timestamped 'Last Updated')",
           notes="Department of Elections 'Voter Counts by Voting Method': county (Kent/New Castle/Sussex) and statewide counts "
                 "of voters who have voted by Absentee (mail_returned), Early Voting (inperson_early) and Grand Totals (total); "
                 "no party split. The page gives counts of voters counted, not 'requested'; whether absentee includes rejected is "
                 "not stated. as_of = date of 'Last Updated' (a timestamp before 09:00 is assigned to the previous day).")
def collect_de_ev_county(st: db.Store):
    r = st.fetch("de_ev_county", DE_URL, ext="html", validate=lambda b: parse_de_page(b.decode("utf8", "ignore")))
    if r.needs_parse:
        res = parse_de_page(r.body.decode("utf8", "ignore"))
        st.add_rows(res["county"], source_id="de_ev_county", snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
        st.add_rows(res["state"], source_id="de_ev_county", snapshot_id=r.snapshot_id, level="state")


# ======================================================================================= HI

HI_URL = "https://elections.hawaii.gov/resources/absentee-voting-report/"
HI_PDF = "https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconState-{d}.pdf"
_HI_COLS = ("elect_sent", "elect_voted", "elect_invalid", "ev_voted", "mail_sent", "mail_voted", "mail_invalid", "voted", "total")


def _hi_cells(v: dict[str, int]) -> dict[tuple, int]:
    """Hawaii is all-mail: ballots sent by mail or electronically (UOCAVA) = mail_requested; ELECT Voted + MAIL Voted =
    mail_returned; EV Voted = inperson_early; VOTED (b+d+f) = total."""
    if v["elect_voted"] + v["ev_voted"] + v["mail_voted"] != v["voted"]:
        raise ValueError("HI: VOTED != b + d + f")
    return {("mail_requested", "ALL"): v["elect_sent"] + v["mail_sent"],
            ("mail_returned", "ALL"): v["elect_voted"] + v["mail_voted"],
            ("inperson_early", "ALL"): v["ev_voted"], ("total", "ALL"): v["voted"]}


def _hi_rows(as_of: str, counties: dict[str, list[int]], state_row: list[int]) -> dict:
    out = {"county": [], "state": []}
    sums = [0] * 9
    for name, vals in counties.items():
        out["county"] += db.long_rows("hi", as_of, _hi_cells(dict(zip(_HI_COLS, vals))), county=db.county_fips("hi", name))
        sums = [a + b for a, b in zip(sums, vals)]
    if sums != state_row:
        raise ValueError(f"HI: county rows {sums} do not sum to the statewide row {state_row}")
    out["state"] = db.long_rows("hi", as_of, _hi_cells(dict(zip(_HI_COLS, state_row))))
    return out


def parse_hi_page(html: str) -> dict:
    """Voting Report table of the Office of Elections -> {'as_of', 'county', 'state'}; the last row (no county name) is the state."""
    m = re.search(r"Last Updated on ([A-Za-z]+ \d{1,2}, \d{4})", _text(html))
    tbl = next((t for t in _tables(html) if t["rows"] and t["rows"][0][0].strip().lower() == "county"
                and "ELECT Sent" in t["rows"][0][1]), None)
    if not m or tbl is None:
        raise ValueError("HI: Voting Report table not found")
    counties: dict[str, list[int]] = {}
    state = None
    for r in tbl["rows"][1:]:
        if len(r) >= 10:
            vals = [db.num(x) for x in r[1:10]]
        elif len(r) == 9 and not r[0].isalpha():
            vals = [db.num(x) for x in r]
            r = [""] + r
        else:
            continue
        if r[0]:
            counties[r[0]] = vals
        else:
            state = vals
    if state is None or len(counties) < 4:
        raise ValueError("HI: counties/state row missing")
    return {"as_of": _long_date(m[1]).isoformat(), **_hi_rows(_long_date(m[1]).isoformat(), counties, state)}


def parse_hi_recon_text(text: str) -> dict:
    """Text of AbsenteeReconState-<date>.pdf (county rows + unlabelled statewide row, stamped 'm/d/yyyy h:mm:ss AM')."""
    st_ = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2}):\d{2} ([AP]M)", text)
    if not st_:
        raise ValueError("HI recon: timestamp not found")
    ts = datetime(int(st_[3]), int(st_[1]), int(st_[2]), _ampm(int(st_[4]), st_[6]), int(st_[5]))
    toks = text.split()
    isnum = lambda t: re.fullmatch(r"[\d,]+", t) is not None             # noqa: E731
    nine = lambda i: len(toks) >= i + 9 and all(isnum(t) for t in toks[i:i + 9])   # noqa: E731
    counties: dict[str, list[int]] = {}
    state = None
    i = 0
    while i < len(toks):
        word = re.sub(r"['\u2019\u02bb\u2018`]", "", toks[i])
        if word.isalpha() and nine(i + 1):
            counties[toks[i]] = [db.num(t) for t in toks[i + 1:i + 10]]
            i += 10
        elif len(counties) >= 4 and state is None and nine(i):
            state = [db.num(t) for t in toks[i:i + 9]]
            i += 9
        else:
            i += 1
    if state is None or len(counties) < 4:
        raise ValueError("HI recon: counties/state row not found")
    as_of = _through(ts, 6)
    return {"as_of": as_of, "stamp": ts.isoformat(timespec="seconds"), **_hi_rows(as_of, counties, state)}


@db.source("hi", "hi_voting_report", HI_URL, kind="county", format="html", cadence="daily",
           notes="Office of Elections 'Voting Report' (unofficial, subject to change): by county, statewide in the last row. "
                 "Hawaii is all-mail, no party registration. mail_requested = ballots sent (MAIL Sent + ELECT Sent); mail_returned = "
                 "MAIL Voted (envelopes received) + ELECT Voted (electronic UOCAVA ballots received); inperson_early = EV Voted; "
                 "total = VOTED (b+d+f). 'Voted' counts ballots received including those later found invalid (Invalid columns "
                 "are not subtracted). as_of = the date after 'Last Updated on' (no time of day is given).")
def collect_hi_voting_report(st: db.Store):
    r = st.fetch("hi_voting_report", HI_URL, ext="html", validate=lambda b: parse_hi_page(b.decode("utf8", "ignore")),
                 conditional=False)
    if r.needs_parse:
        res = parse_hi_page(r.body.decode("utf8", "ignore"))
        st.add_rows(res["county"], source_id="hi_voting_report", snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
        st.add_rows(res["state"], source_id="hi_voting_report", snapshot_id=r.snapshot_id, level="state")


def _validate_hi_pdf(body: bytes) -> None:
    parse_hi_recon_text(_pdf_text(body))


@db.source("hi", "hi_absentee_recon", "https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconState-<yyyymmdd>.pdf",
           kind="county", format="pdf", cadence="daily (one PDF per date, early-morning stamp)",
           notes="'Absentee Reconciliation' state report PDFs linked from the Voting Report page (same columns/semantics as "
                 "hi_voting_report, county + statewide). Each PDF is stamped ~3:12 AM and reflects the previous day's close, so "
                 "as_of = stamp date - 1 (stamps before 06:00). Gives the history that the HTML page lacks.")
def collect_hi_absentee_recon(st: db.Store):
    sid = "hi_absentee_recon"
    page = st.request(HI_URL)
    if page.status_code != 200:
        st._reject(sid, "index", "hi", HI_URL, page.status_code, f"HTTP {page.status_code}")
        return
    stamps = sorted(set(re.findall(r"AbsenteeReconState-(\d{8})\.pdf", page.text)))[-8:]
    for s in stamps:
        day = date(int(s[:4]), int(s[4:6]), int(s[6:]))
        if _settled(st, sid, s, day):
            continue
        r = st.fetch(sid, HI_PDF.format(d=s), ext="pdf", part=s, validate=_validate_hi_pdf)
        if r.needs_parse:
            res = parse_hi_recon_text(_pdf_text(r.body))
            st.add_rows(res["county"], source_id=sid, snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
            st.add_rows(res["state"], source_id=sid, snapshot_id=r.snapshot_id, level="state")


# ======================================================================================= ID

ID_TOTALS = "https://archive.voteidaho.gov/data/absentee/absentee_totals_2026_general.json"
ID_PAGE = "https://voteidaho.gov/data-and-dashboards/absentee-tracker/"
ID_CHARTS = {"party": ("Jgr2z", "PartyDesc"), "age": ("lYjhf", "AgeBin"), "county": ("3EDnv", "ResCountyDesc")}
ID_PARTY = {"Democratic": "D", "Republican": "R", "Unaffiliated": "U"}      # Constitution, Libertarian -> other


def _id_totals(js: bytes, year: int) -> tuple[str, dict[tuple, int]]:
    d = {x["name"]: x["value"] for x in json.loads(js)}
    m = re.match(r"(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2}) ([AP]M)", d["update_time"])
    if not m:
        raise ValueError(f"ID: bad update_time {d['update_time']!r}")
    ts = datetime(year, int(m[1]), int(m[2]), _ampm(int(m[3]), m[5]), int(m[4]))
    cells = {("mail_requested", "ALL"): db.num(d["total_issued"]), ("mail_returned", "ALL"): db.num(d["returned"]),
             ("inperson_early", "ALL"): db.num(d["early_voting"]), ("total", "ALL"): db.num(d["total_voted"])}
    return _through(ts), cells


def parse_id(totals_json: bytes, party_csv: bytes | None, age_csv: bytes | None, county_csv: bytes | None,
             year: int = 2026) -> dict:
    """VoteIdaho absentee tracker -> {'as_of', 'state', 'county', 'warnings'}. The JSON totals are the anchor: a Datawrapper
    table is used only when it sums exactly to them (it may be republished at a different time)."""
    as_of, totals = _id_totals(totals_json, year)
    state = db.long_rows("id", as_of, totals)
    county: list[dict] = []
    warn: list[str] = []

    def table(b: bytes) -> list[dict]:
        return list(csv.DictReader(io.StringIO(b.decode("utf-8-sig"))))

    def ok(sums: dict[str, int], where: str) -> bool:
        want = {"mail_requested": "mail_requested", "mail_returned": "mail_returned", "inperson_early": "inperson_early",
                "total": "total"}
        for k in want:
            if sums[k] != totals[(k, "ALL")]:
                warn.append(f"{where}: {k} sums to {sums[k]} but the JSON total is {totals[(k, 'ALL')]}; table skipped")
                return False
        return True

    if party_csv:
        rows = table(party_csv)
        cells: dict[str, dict[str, int]] = {}
        for r in rows:
            p = ID_PARTY.get(r["PartyDesc"], "other")
            c = cells.setdefault(p, dict.fromkeys(("mail_requested", "mail_returned", "inperson_early", "total"), 0))
            c["mail_requested"] += db.num(r["Abs. Issued"])
            c["mail_returned"] += db.num(r["Abs. Returned"])
            c["inperson_early"] += db.num(r["Early Voted"])
            c["total"] += db.num(r["Total Voted"])
        if ok({k: sum(c[k] for c in cells.values()) for k in ("mail_requested", "mail_returned", "inperson_early", "total")}, "party"):
            for p, c in cells.items():
                state += db.long_rows("id", as_of, {(k, p): v for k, v in c.items()})
    if age_csv:
        rows = table(age_csv)
        # age table has no 'issued': returned (abs), early (ev), total (n)
        sums = {"mail_returned": sum(db.num(r["abs"]) for r in rows), "inperson_early": sum(db.num(r["ev"]) for r in rows),
                "total": sum(db.num(r["n"]) for r in rows)}
        if all(sums[k] == totals[(k, "ALL")] for k in sums):
            for r in rows:
                state += _cells("id", as_of, {("mail_returned", "ALL"): db.num(r["abs"]), ("inperson_early", "ALL"): db.num(r["ev"]),
                                              ("total", "ALL"): db.num(r["n"])}, age=r["AgeBin"])
        else:
            warn.append(f"age: sums {sums} differ from the JSON totals; table skipped")
    if county_csv:
        rows = table(county_csv)
        sums = {"mail_requested": sum(db.num(r["total_issued"]) for r in rows), "mail_returned": sum(db.num(r["Returned"]) for r in rows),
                "inperson_early": sum(db.num(r["early_voting"]) for r in rows), "total": sum(db.num(r["total_voted"]) for r in rows)}
        if ok(sums, "county"):
            for r in rows:
                county += db.long_rows("id", as_of, {("mail_requested", "ALL"): db.num(r["total_issued"]),
                                                      ("mail_returned", "ALL"): db.num(r["Returned"]),
                                                      ("inperson_early", "ALL"): db.num(r["early_voting"]),
                                                      ("total", "ALL"): db.num(r["total_voted"])},
                                       county=db.county_fips("id", r["ResCountyDesc"]))
    return {"as_of": as_of, "state": state, "county": county, "warnings": warn}


def _dw_csv(st: db.Store, key: str):
    cid, col = ID_CHARTS[key]
    page = st.request(f"https://datawrapper.dwcdn.net/{cid}/")
    m = re.search(rf"/{cid}/(\d+)/", page.text) if page.status_code == 200 else None
    if not m:
        st._reject("id_ev_tracker", key, "id", f"https://datawrapper.dwcdn.net/{cid}/", page.status_code, "chart version not found")
        return None

    def validate(b: bytes):
        if not b.decode("utf-8-sig").splitlines()[0].startswith(col):
            raise ValueError(f"unexpected header for chart {cid}")
    r = st.fetch("id_ev_tracker", f"https://datawrapper.dwcdn.net/{cid}/{m[1]}/dataset.csv", ext="csv", part=key,
                 validate=validate, conditional=False)
    return r


@db.source("id", "id_ev_tracker", ID_PAGE, kind="county", format="json+csv", cadence="daily (~06:00 MT)",
           notes="VoteIdaho absentee tracker: BAN totals JSON (archive.voteidaho.gov, 'update_time' MT) + the Datawrapper tables "
                 "behind the dashboard (by party, by age bin, by county). mail_requested = absentee ballots issued; mail_returned = "
                 "absentee ballots returned; inperson_early = early voting voted; total = total voted. Party = registered party "
                 "(Constitution + Libertarian = other, Unaffiliated = U); age bands as published (18-29, 30-44, 45-59, 60-74, 75+). "
                 "A table is stored only if it sums to the JSON totals. as_of = update_time date (a stamp before 09:00 MT is "
                 "assigned to the previous day).")
def collect_id_ev_tracker(st: db.Store):
    sid = "id_ev_tracker"
    js = st.fetch(sid, ID_TOTALS, ext="json", part="totals", conditional=False,
                  validate=lambda b: _id_totals(b, UTC_TODAY().year))
    if not js.ok:
        return
    got = {k: _dw_csv(st, k) for k in ID_CHARTS}
    fetched = [js] + [r for r in got.values() if r is not None and r.ok]
    if any(r.needs_parse for r in fetched):
        body = {k: (r.body if r is not None and r.ok else None) for k, r in got.items()}
        res = parse_id(js.body, body["party"], body["age"], body["county"], UTC_TODAY().year)
        for w in res["warnings"]:
            st.log(sid, "warning", state="id", message=w)
        snap = max(r.snapshot_id for r in fetched)                     # newest input: rows of an unchanged JSON stay distinct
        st.add_rows(res["county"], source_id=sid, snapshot_id=snap, level="county", mark_parsed=False)
        st.add_rows(res["state"], source_id=sid, snapshot_id=snap, level="state")
        for r in fetched:
            st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (r.snapshot_id,))
        st.db.commit()


# ======================================================================================= CA

CA_URL = "https://elections.cdn.sos.ca.gov/statewide-elections/2026-general/bsr-statistics-{d}.pdf"
CA_FIRST = date(2026, 10, 26)
CA_TYPES = ("VCA County", "All Mail", "Polling Place")


def parse_ca_bsr_text(text: str) -> dict:
    """VoteCal 'Ballot Return Statistics' PDF text -> {'as_of', 'county': rows, 'state': rows}. Reconciles every row
    (return channels = returned; in-person parts = in-person; returned + in-person = total) and the 58 counties vs the state row."""
    m = re.search(r"Ballot Return Statistics as of:\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+ \d{1,2}, \d{4})", text)
    if not m:
        raise ValueError("CA: as-of date not found")
    as_of = _long_date(m[1]).isoformat()

    def cells(v: list[int]) -> dict[tuple, int]:
        issued, chan, returned, accepted, ip_parts, ip_total, total = v[0], v[1:7], v[7], v[8], v[9:13], v[13], v[15]
        if sum(chan) != returned or sum(ip_parts) != ip_total or returned + ip_total != total:
            raise ValueError(f"CA: row does not reconcile {v}")
        return {("mail_requested", "ALL"): issued, ("mail_returned", "ALL"): returned, ("mail_accepted", "ALL"): accepted,
                ("inperson_early", "ALL"): ip_total, ("total", "ALL"): total}

    county_rows: list[dict] = []
    state_cells = None
    n = 0
    sums: dict[tuple, int] = {}
    for line in text.splitlines():
        toks = line.split()
        if len(toks) < 19:
            continue
        lead = next((i for i, t in enumerate(toks) if re.fullmatch(r"[\d,]+|-", t)), None)
        if lead is None or any(not re.fullmatch(r"[\d,]+|-|[\d.]+%", t) for t in toks[lead:]):
            continue
        vals = [0 if t == "-" else db.num(t) for t in toks[lead:] if not t.endswith("%")]
        if len(vals) != 16:
            continue
        if lead == 0:
            state_cells = cells(vals)
            continue
        name = toks[:lead]
        typ = " ".join(name[-2:])
        if typ not in CA_TYPES:
            raise ValueError(f"CA: unknown county type in {line[:60]!r}")
        c = cells(vals)
        county_rows += db.long_rows("ca", as_of, c, county=db.county_fips("ca", " ".join(name[:-2])))
        n += 1
        for k, v in c.items():
            sums[k] = sums.get(k, 0) + v
    if state_cells is None or n != 58:
        raise ValueError(f"CA: expected 58 counties and a state row, got {n} / {state_cells is not None}")
    if any(sums[k] != state_cells[k] for k in sums):
        raise ValueError("CA: county rows do not sum to the statewide row")
    return {"as_of": as_of, "county": county_rows, "state": db.long_rows("ca", as_of, state_cells)}


def _validate_ca_pdf(body: bytes) -> None:
    parse_ca_bsr_text(_pdf_text(body))


@db.source("ca", "ca_ev_county", "https://elections.cdn.sos.ca.gov/statewide-elections/2026-general/bsr-statistics-MM-DD-YY.pdf",
           kind="county", format="pdf", cadence="daily (file per date; missing dates answer 403 AccessDenied from S3)",
           notes="Secretary of State VoteCal 'Ballot Return Statistics' by county (no party). mail_requested = voters issued a "
                 "VBM ballot (every county now mails a ballot to every registered voter, so this is ~registration); mail_returned = Total Returned VBM Ballots (drop box + "
                 "drop-off + vote-center drop-off + mail + fax + other, excl. undeliverable/deceased/cancelled); mail_accepted = "
                 "Total Accepted VBM; inperson_early = Total Cast In-Person Ballots (regular + provisional + CVR, vote centers "
                 "and county offices); total = TOTAL BALLOTS CAST. as_of = the 'as of' date printed in the report; statewide from "
                 "the report's own total row (equals the county sum).")
def collect_ca_ev_county(st: db.Store):
    sid = "ca_ev_county"
    day = CA_FIRST
    while day <= min(UTC_TODAY(), date(2026, 11, 13)):
        part = f"{day:%Y%m%d}"
        if not _settled(st, sid, part, day):
            r = st.fetch(sid, CA_URL.format(d=f"{day:%m-%d-%y}"), ext="pdf", part=part, validate=_validate_ca_pdf)
            if r.needs_parse:
                res = parse_ca_bsr_text(_pdf_text(r.body))
                st.add_rows(res["county"], source_id=sid, snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
                st.add_rows(res["state"], source_id=sid, snapshot_id=r.snapshot_id, level="state")
        day += timedelta(days=1)


# ======================================================================================= DC

_DC_FILES = {"db": "Ballots-by-DB", "usps": "Ballots-by-USPS", "evc": "Ballots-by-EVC"}
_DC_DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})\s*(.*)$")


def _dc_series(text: str) -> dict[str, int | None]:
    """Daily rows 'October 13, 2026 680 ... 4692' (drop box: ward columns + total) or 'October 8, 2026 1,094' -> {iso date: daily total or None if blank}."""
    out: dict[str, int | None] = {}
    for line in text.splitlines():
        m = _DC_DATE.match(line.strip())
        if m:
            nums = re.findall(r"[\d,]+", m[2])
            out[_long_date(m[1]).isoformat()] = db.num(nums[-1]) if nums else None
    return out


def _dc_evc_series(text: str) -> dict[str, int]:
    """Early-vote-centre report: header 'WARD 26-Oct 27-Oct ... TOTAL', row 'TOTAL n n n ... grand' -> {iso date: daily total}."""
    year = re.search(r"GENERAL ELECTION (\d{4})|(\d{4}) GENERAL ELECTION", text)
    y = int(next(g for g in year.groups() if g)) if year else 2026
    hdr = next((l for l in text.splitlines() if l.strip().startswith("WARD") and re.search(r"\d-[A-Z][a-z]{2}", l)), None)
    days = re.findall(r"(\d{1,2})-([A-Z][a-z]{2})", hdr or "")
    tot = next((l for l in text.splitlines() if l.strip().startswith("TOTAL ")), None)
    if not days or tot is None:
        raise ValueError("DC EVC: header/total row not found")
    nums = [db.num(x) for x in re.findall(r"[\d,]+", tot)]
    if len(nums) != len(days) + 1 or sum(nums[:-1]) != nums[-1]:
        raise ValueError("DC EVC: total row does not match header or grand total")
    mon = {m[:3]: i for m, i in _MONTHS.items()}
    return {date(y, mon[mname.lower()], int(d)).isoformat(): n for (d, mname), n in zip(days, nums[:-1])}


def dc_rows(db_text: str, usps_text: str, evc_text: str) -> list[dict]:
    """Cumulative statewide rows per date from the three daily PDFs of the Board of Elections (DC = one jurisdiction).

    mail_returned = ballots received by drop box (incl. vote-centre boxes) + by USPS; inperson_early = early vote centres;
    total = their sum. Rows are emitted for every date from the first date both mail series exist through the last date
    with a non-zero entry in both (the PDFs list future dates as 0 or blank)."""
    series = {"db": _dc_series(db_text), "usps": _dc_series(usps_text)}
    evc = _dc_evc_series(evc_text)
    def last_pos(s): return max((d for d, v in s.items() if v), default=None)
    def first_pos(s): return min((d for d, v in s.items() if v is not None), default=None)
    end = min(last_pos(series["db"]), last_pos(series["usps"]))
    start = max(first_pos(series["db"]), first_pos(series["usps"]))
    evc_first, evc_last = min(evc), max(d for d, v in evc.items() if v) if any(evc.values()) else None
    dates = sorted(d for d in set(series["db"]) | set(series["usps"]) if start <= d <= end)
    rows = []
    for d in dates:
        mail = sum(v or 0 for s in series.values() for dd, v in s.items() if dd <= d)
        cells = {("mail_returned", "ALL"): mail}
        if evc_last is None or d < evc_first:
            cells[("inperson_early", "ALL")] = 0
            cells[("total", "ALL")] = mail
        elif d <= evc_last:
            ip = sum(v for dd, v in evc.items() if dd <= d)
            cells[("inperson_early", "ALL")] = ip
            cells[("total", "ALL")] = mail + ip
        rows += db.long_rows("dc", d, cells)
    return rows


def _dc_links(home_html: str) -> dict[str, str]:
    out = {}
    for key, stem in _DC_FILES.items():
        m = re.search(rf'href="(/getContentAsset/[^"]*2026-General-{stem}-\d+\.pdf[^"]*)"', home_html)
        if m:
            out[key] = "https://www.dcboe.org" + _html.unescape(m[1])
    return out


@db.source("dc", "dc_ev_state", "https://www.dcboe.org/", kind="state", format="pdf", cadence="daily",
           notes="DC Board of Elections daily 'Voted Ballots Received' PDFs linked from the home page: by mail-ballot drop box "
                 "(by ward; incl. drop boxes at vote centres), by USPS, and in-person early vote centres (by centre, from Oct 26). "
                 "Each PDF lists daily counts; the collector stores CUMULATIVE statewide values for every listed date: "
                 "mail_returned = drop-box + USPS ballots RECEIVED (not yet verified/accepted), inperson_early = early vote centre "
                 "ballots, total = sum. No party split. DC is one jurisdiction: state rows only (ward detail not mapped). The "
                 "PDF URLs contain asset GUIDs and are re-discovered from the home page on each run.")
def collect_dc_ev_state(st: db.Store):
    sid = "dc_ev_state"
    home = st.request("https://www.dcboe.org/")
    if home.status_code != 200:
        st._reject(sid, "home", "dc", "https://www.dcboe.org/", home.status_code, f"HTTP {home.status_code}")
        return
    links = _dc_links(home.text)
    if set(links) != set(_DC_FILES):
        st._reject(sid, "home", "dc", "https://www.dcboe.org/", 200, f"PDF links not found on the home page: have {sorted(links)}")
        return
    got = {k: st.fetch(sid, u, ext="pdf", part=k, conditional=False,
                       validate=lambda b: _dc_pdf_ok(b)) for k, u in links.items()}
    if all(r.ok for r in got.values()) and any(r.needs_parse for r in got.values()):
        texts = {k: _pdf_text(r.body) for k, r in got.items()}
        rows = dc_rows(texts["db"], texts["usps"], texts["evc"])
        st.add_rows(rows, source_id=sid, snapshot_id=max(r.snapshot_id for r in got.values()), level="state")
        for r in got.values():
            st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (r.snapshot_id,))
        st.db.commit()


def _dc_pdf_ok(body: bytes) -> None:
    t = _pdf_text(body)
    if "DC Board of Elections" not in t and "BALLOTS RECEIVED" not in t.upper():
        raise ValueError("not a DCBOE ballot report")


# ======================================================================================= recorded, not collected

db.not_collected("ak", "ak_absentee_stats", "https://www.elections.alaska.gov/research/statistics/",
                 access="blocked: Combined Ballot Report (htm) files return HTTP 405 'Human Verification' CAPTCHA page",
                 notes="The statistics page itself is open (TLS fine with the certifi CA bundle) but links only to the daily "
                       "'Combined Ballot Report' HTML files under /wp-content/uploads/2026/10/, and every one of them (2026 and "
                       "older ones tried) answers a JavaScript CAPTCHA page; not bypassed. Press reports quote the numbers.",
                 cadence="daily", format="html")
db.not_collected("az", "az_sos_ev", "https://azsos.gov/elections/election-information/voter-registration-statistics",
                 access="blocked: azsos.gov and countingballots.maricopa.vote answer a Cloudflare 'Just a moment' challenge (403)",
                 notes="No statewide early-ballot file on the SoS site in any case (registration statistics only). Maricopa's "
                       "election-results page offers only the zero-report; its ballots-received dashboard is behind Cloudflare. "
                       "Pima publishes batch PDF memos behind GUID URLs (county only, not collected).",
                 format="html")
db.not_collected("ga", "ga_ev_hub", "https://sos.ga.gov/page/election-data-hub-unofficial-turnout",
                 access="blocked: sos.ga.gov and mvp.sos.ga.gov answer a Cloudflare 'Just a moment' challenge (403)",
                 notes="Election Data Hub / absentee voter files (Salesforce app) not reachable without a browser challenge; not bypassed.",
                 format="html")
db.not_collected("al", "al_absentee", "https://www.sos.alabama.gov/alabama-votes/voter/absentee-voting",
                 access="none published: the SoS absentee page (HTTP 200) has rules and forms only, no counts",
                 notes="No statewide or county absentee/early statistics found on the Secretary of State site (Alabama has no "
                       "early in-person voting).", format="html")
db.not_collected("ar", "ar_absentee", "https://www.sos.arkansas.gov/elections/voter-information/absentee-voting",
                 access="none published: the SoS absentee page (HTTP 200) has rules only, no counts",
                 notes="No statewide early/absentee counts on the Secretary of State site; county clerks only.", format="html")
