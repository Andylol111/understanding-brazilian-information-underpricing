"""Early-vote collectors, group C: MT NE NV NH NJ NM NY ND OH OK OR PA.

Collected: PA (DOS daily mail-ballot xlsx + data.pa.gov per-voter extract aggregated server side), OK (State
Election Board daily PDFs by affiliation, county level), OR (SoS unofficial ballot-return PDF, county x party and
county x day), ND (SoS absentee / early-voting page, statewide + one POST per county).
Recorded as not collected (blocked or not published): MT NE NV NH NJ NM NY OH.

Parsers are pure functions of bytes; collectors follow the pattern in us_ev_db.py.
"""
from __future__ import annotations

import io
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import us_ev_db as db

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def _asof_from_run(day: date, hour: int) -> str:
    """A report run in the morning (before noon) covers ballots through the close of the previous day."""
    return (day if hour >= 12 else day - timedelta(days=1)).isoformat()


def _pdf_pages(body: bytes, layout: bool = True) -> list[str]:
    from pypdf import PdfReader          # lazy: only needed when parsing PDFs
    r = PdfReader(io.BytesIO(body))
    return [(p.extract_text(extraction_mode="layout") if layout else p.extract_text()) or "" for p in r.pages]


# =====================================================================================================================
# PA - Department of State daily mail ballot report (xlsx) -> county x party
# =====================================================================================================================
PA_XLSX = "https://www.pavoterservices.pa.gov/2026%20General%20Daily%20Mail%20Ballot%20Report.xlsx"
_PA_PARTY = {"Dem": "D", "Rep": "R", "Oth": "other", "Total": "ALL"}


def parse_pa_daily_xlsx(body: bytes, expect_counties: int = 67) -> tuple[str, list[dict], list[dict]]:
    """-> (as_of, county rows, state rows) from the 'Total' tab (mail-in + absentee) of the daily report.

    Header A1 'Information as of 10/31/2026 08:00 AM'; per county: applications approved and ballots returned by
    party of the applicant (Dem / Rep / Oth = all other incl. no affiliation)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
    rows = list(wb["Total"].iter_rows(values_only=True))
    m = re.search(r"as of\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)", str(rows[0][0]), re.I)
    if not m:
        raise ValueError("no 'Information as of' stamp")
    hour = int(m.group(4)) % 12 + (12 if m.group(6).upper() == "PM" else 0)
    as_of = _asof_from_run(date(int(m.group(3)), int(m.group(1)), int(m.group(2))), hour)
    hdr = [str(c).strip() for c in rows[1]]
    cols = {}
    for j, h in enumerate(hdr):
        mm = re.fullmatch(r"(Total|Dem|Rep|Oth) (Applications Approved|Ballots Returned)", h)
        if mm:
            cols[j] = (_PA_PARTY[mm.group(1)], "mail_requested" if mm.group(2).startswith("App") else "mail_returned")
    if len(cols) != 8:
        raise ValueError(f"unexpected PA header {hdr}")
    county, state = [], []
    for r in rows[2:]:
        name = (r[0] or "").strip() if r else ""
        if not name:
            continue
        cells = {(meas, p): db.num(r[j]) for j, (p, meas) in cols.items()}
        if name.upper() == "TOTAL":
            state += db.long_rows("PA", as_of, cells)
        else:
            county += db.long_rows("PA", as_of, cells, county=db.county_fips("PA", name))
    if not state or len({r["county_fips"] for r in county}) != expect_counties:
        raise ValueError(f"PA xlsx: expected {expect_counties} counties and a TOTAL row")
    for meas in ("mail_requested", "mail_returned"):         # party parts must add up
        for scope in (county, state):
            tot = sum(r["count"] for r in scope if r["measure"] == meas and r["party"] == "ALL")
            parts = sum(r["count"] for r in scope if r["measure"] == meas and r["party"] != "ALL")
            if tot != parts:
                raise ValueError(f"PA {meas}: parties {parts} != total {tot}")
    for meas in ("mail_requested", "mail_returned"):         # the 67 county rows must add up to the report's own TOTAL row
        got = sum(r["count"] for r in county if r["measure"] == meas and r["party"] == "ALL")
        want = sum(r["count"] for r in state if r["measure"] == meas and r["party"] == "ALL")
        if got != want:
            raise ValueError(f"PA {meas}: the county rows add up to {got}, the TOTAL row says {want}")
    _pa_check_tabs(wb, rows)
    return as_of, county, state


def _pa_total_row(rows) -> list[int]:
    for r in rows:
        if r and str(r[0] or "").strip().upper() == "TOTAL":
            return [db.num(c) for c in r[1:9]]
    raise ValueError("PA xlsx: a tab has no TOTAL row")


def _pa_check_tabs(wb, total_rows) -> None:
    """The 'Total' tab already IS the 'Mail-In' tab plus the 'Absentee' tab. The parser reads Total only, once; this fails the
    parse if the workbook stops saying so. Adding the three tabs up would count every ballot twice. (The 2,086,905 'returned'
    seen once was not this: it was this xlsx's 1,041,756 plus data.pa.gov's 1,045,149, two sources for the same ballots, see
    us_ev_canon.py.)"""
    if "Mail-In" not in wb.sheetnames or "Absentee" not in wb.sheetnames:
        return
    a = _pa_total_row(wb["Mail-In"].iter_rows(values_only=True))
    b = _pa_total_row(wb["Absentee"].iter_rows(values_only=True))
    t = _pa_total_row(total_rows)
    if [x + y for x, y in zip(a, b)] != t:
        raise ValueError(f"PA xlsx: Total tab {t} != Mail-In + Absentee tabs {[x + y for x, y in zip(a, b)]}")


def _pa_validate(body: bytes) -> None:
    parse_pa_daily_xlsx(body)


@db.source("PA", "pa_mail_daily_xlsx", PA_XLSX, kind="county", format="xlsx", cadence="daily ~08:00 ET",
           notes="DOS 'General Daily Mail Ballot Report', tab Total (mail-in + absentee). County x party of the applicant "
                 "(Oth = every party other than Dem/Rep incl. no affiliation -> party 'other'). mail_requested = applications "
                 "APPROVED; mail_returned = ballots returned (any later status: includes ballots still pending/cancelled). "
                 "No in-person early voting in PA. State rows are the report's own TOTAL row. A report stamped before noon "
                 "is dated the previous day (data through close of that day).")
def collect_pa_daily(st):
    r = st.fetch("pa_mail_daily_xlsx", PA_XLSX, ext="xlsx", validate=_pa_validate)
    if r.needs_parse:
        _, county, state = parse_pa_daily_xlsx(r.body)
        st.add_rows(county, source_id="pa_mail_daily_xlsx", snapshot_id=r.snapshot_id, level="county")
        st.add_rows(state, source_id="pa_mail_daily_xlsx", snapshot_id=r.snapshot_id, level="state", mark_parsed=False)


# ---- PA data.pa.gov (Socrata) aggregation: history by ballot-returned date, plus accepted ballots --------------------
PA_SOCRATA_ID = "nbwd-pfn4"
PA_SOCRATA_URL = f"https://data.pa.gov/resource/{PA_SOCRATA_ID}.json"
_PA_SOC_PARTY = {"DEM": "D", "REP": "R"}
PA_HISTORY_DAYS = 14


def _soc_party(code: str) -> str:
    return _PA_SOC_PARTY.get((code or "").upper(), "other")      # OTH, LIB, GRN, ... -> other


def parse_pa_socrata(body: bytes) -> tuple[str, list[dict], list[dict]]:
    """Wrapper JSON {rows_updated_utc, status:[...], bydate:[...]} -> (as_of, county rows, state rows).

    status rows: countyname, party, n (all applications), req (approved), ret (ballot returned date set), acc
    (ballot_status 'Vote Recorded').  bydate rows: countyname, party, ballotreturneddate, n."""
    d = json.loads(body)
    upd = datetime.fromisoformat(d["rows_updated_utc"]).astimezone(ET)
    as_of = _asof_from_run(upd.date(), upd.hour)
    cells: dict[tuple[str, str], dict[tuple[str, str], int]] = {}          # county -> (measure, party) -> n

    def add(cty, key, n):
        c = cells.setdefault(cty, {})
        c[key] = c.get(key, 0) + n
        k2 = (key[0], "ALL")
        c[k2] = c.get(k2, 0) + n

    for r in d["status"]:
        p = _soc_party(r["party"])
        add(r["countyname"], ("mail_requested", p), int(r["req"]))
        add(r["countyname"], ("mail_returned", p), int(r["ret"]))
        add(r["countyname"], ("mail_accepted", p), int(r["acc"]))
    county = []
    for name, c in cells.items():
        county += db.long_rows("PA", as_of, c, county=db.county_fips("PA", name))
    # history: cumulative returned by returned-date for the last PA_HISTORY_DAYS days up to as_of
    per: dict[tuple[str, str], dict[str, int]] = {}
    for r in d["bydate"]:
        k = (r["countyname"], _soc_party(r["party"]))
        day = r["ballotreturneddate"][:10]
        per.setdefault(k, {})
        per[k][day] = per[k].get(day, 0) + int(r["n"])
    end = date.fromisoformat(as_of)
    days = [(end - timedelta(days=i)).isoformat() for i in range(PA_HISTORY_DAYS, 0, -1)]
    hist: dict[tuple[str, str, str], int] = {}
    for (cty, p), byday in per.items():
        for dd in days:
            n = sum(v for k, v in byday.items() if k <= dd)
            for pp in (p, "ALL"):
                hist[(cty, dd, pp)] = hist.get((cty, dd, pp), 0) + n
    for (cty, dd, p), n in hist.items():
        county += db.long_rows("PA", dd, {("mail_returned", p): n}, county=db.county_fips("PA", cty))
    state = _sum_to_state(county, "PA")
    return as_of, county, state


def _sum_to_state(county_rows: list[dict], st_code: str) -> list[dict]:
    acc: dict[tuple, int] = {}
    for r in county_rows:
        k = (r["as_of_date"], r["measure"], r["party"])
        acc[k] = acc.get(k, 0) + r["count"]
    return [{"state": st_code, "as_of_date": a, "measure": m, "party": p, "count": n} for (a, m, p), n in acc.items()]


def _soql(st, query: str) -> list[dict]:
    r = st.request(PA_SOCRATA_URL, params={"$query": query})
    if r.status_code != 200:
        raise db.FetchError(f"data.pa.gov HTTP {r.status_code}")
    return r.json()


@db.source("PA", "pa_mail_socrata", "https://data.pa.gov/resource/nbwd-pfn4.json", kind="county", format="json",
           cadence="daily ~05:00 ET (extract of the SURE system)",
           notes="data.pa.gov '2026 General Election Mail Ballot Requests' (per-voter file) aggregated SERVER-SIDE with SoQL "
                 "(no voter-level data downloaded). County x party (DEM/REP/other incl. LIB, GRN, no affiliation). mail_requested = "
                 "applications with disposition Approved; mail_returned = ballot returned date present (any later status); "
                 "mail_accepted = ballot_status 'Vote Recorded'. as_of = extract date, previous day when extracted before noon ET. "
                 "Also cumulative mail_returned for the last 14 days by ballot-returned date (reconstructed from the current "
                 "extract, so later cancellations are included). State rows = sums of counties (source gives no state figure).")
def collect_pa_socrata(st):
    meta = st.request(f"https://data.pa.gov/api/views/{PA_SOCRATA_ID}.json")
    if meta.status_code != 200:
        st.log("pa_mail_socrata", "failed", state="PA", url=str(meta.url), status=meta.status_code, message="metadata")
        return
    m = meta.json()
    if "2026 General" not in m.get("name", ""):
        st.log("pa_mail_socrata", "failed", state="PA", message=f"dataset {PA_SOCRATA_ID} is now {m.get('name')!r}")
        return
    upd = datetime.fromtimestamp(m["rowsUpdatedAt"], timezone.utc).isoformat()
    status = _soql(st, "select countyname, party, count(*) as n, "
                       "sum(case(ballot_application_disposition='Approved',1,true,0)) as req, "
                       "count(ballotreturneddate) as ret, sum(case(ballot_status='Vote Recorded',1,true,0)) as acc "
                       "group by countyname, party limit 50000")
    bydate = _soql(st, "select countyname, party, ballotreturneddate, count(*) as n where ballotreturneddate is not null "
                       "group by countyname, party, ballotreturneddate limit 200000")
    body = json.dumps({"rows_updated_utc": upd, "status": status, "bydate": bydate}).encode()
    r = st.save_body("pa_mail_socrata", PA_SOCRATA_URL, body, ext="json", state="PA", validate=lambda b: parse_pa_socrata(b))
    if r.needs_parse:
        _, county, state = parse_pa_socrata(r.body)
        st.add_rows(county, source_id="pa_mail_socrata", snapshot_id=r.snapshot_id, level="county")
        st.add_rows(state, source_id="pa_mail_socrata", snapshot_id=r.snapshot_id, level="state", mark_parsed=False)


# =====================================================================================================================
# OK - State Election Board daily PDFs by affiliation (county pages + STATE page)
# =====================================================================================================================
OK_INDEX = "https://oklahoma.gov/elections/voters/absentee-voting-statistics/absentee-voting-statistics.html"
_OK_AFF = {"Republican": "R", "Democrat": "D", "Independent": "U", "Libertarian": "other"}
_OK_MAIL = re.compile(r"^\s*(?:(\d{2})\s+([A-Z][A-Z .'\-]*?)\s{2,})?(Republican|Democrat|Libertarian|Independent)\s+"
                      r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")
_OK_IP_COUNTY = re.compile(r"^\s*County:\s+(\d{2})\s+([A-Z][A-Z .'\-]*?)\s*$")
_OK_IP = re.compile(r"^\s*(Republican|Democrat|Libertarian|Independent)\s+([\d,]+)\s*$")


def _ok_run(page0: str) -> str:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):\d{2}\s*([AP]M)", page0)
    if not m:
        raise ValueError("no run timestamp")
    hour = int(m.group(4)) % 12 + (12 if m.group(5) == "PM" else 0)
    return _asof_from_run(date(int(m.group(3)), int(m.group(1)), int(m.group(2))), hour)


def parse_ok_pdf(body: bytes) -> dict:
    return parse_ok_pages(_pdf_pages(body))


def parse_ok_pages(pages: list[str], expect_counties: int = 77) -> dict:
    """-> {'kind': 'mail'|'inperson', 'as_of', 'counties': {name: {party: tuple}}, 'state': {party: tuple}}.

    mail tuples = (sent, undeliverable, received); inperson tuples = (n,). The STATE page is checked against the sum of
    the county pages."""
    head = pages[0]
    kind = "inperson" if "In-person Totals" in head else "mail" if "Ballot Totals by Affiliation" in head else None
    if kind is None:
        raise ValueError("unrecognised OK report")
    as_of = _ok_run(head)
    out: dict[str, dict] = {}
    cur = None
    for pg in pages:
        for line in pg.splitlines():
            if kind == "mail":
                m = _OK_MAIL.match(line)
                if not m:
                    continue
                if m.group(2):
                    cur = m.group(2).strip()
                out.setdefault(cur, {})[m.group(3)] = tuple(db.num(m.group(i)) for i in (4, 5, 6))
            else:
                m = _OK_IP_COUNTY.match(line)
                if m:
                    cur = m.group(2).strip()
                    continue
                m = _OK_IP.match(line)
                if m and cur:
                    out.setdefault(cur, {})[m.group(1)] = (db.num(m.group(2)),)
    state = out.pop("STATE", None)
    if state is None or len(out) != expect_counties:
        raise ValueError(f"OK: expected {expect_counties} counties + STATE, got {len(out)} counties, state={state is not None}")
    for aff, vals in state.items():
        for i in range(len(vals)):
            if sum(c.get(aff, (0,) * len(vals))[i] for c in out.values()) != vals[i]:
                raise ValueError(f"OK {kind}: county sum != STATE for {aff}[{i}]")
    return {"kind": kind, "as_of": as_of, "counties": out, "state": state}


def ok_rows(mail: dict | None, inperson: dict | None) -> tuple[list[dict], list[dict]]:
    """Long rows (county, state) from the parsed mail and in-person reports of the same as_of date."""
    def units(parsed):
        return {"STATE": parsed["state"], **parsed["counties"]}

    cells: dict[str, dict[tuple[str, str], int]] = {}

    def put(unit, meas, aff, n):
        c = cells.setdefault(unit, {})
        p = _OK_AFF[aff]
        c[(meas, p)] = c.get((meas, p), 0) + n
        c[(meas, "ALL")] = c.get((meas, "ALL"), 0) + n
    if mail:
        for unit, affs in units(mail).items():
            for aff, (sent, _undel, recv) in affs.items():
                put(unit, "mail_requested", aff, sent)
                put(unit, "mail_returned", aff, recv)
                put(unit, "total", aff, recv)
    if inperson:
        for unit, affs in units(inperson).items():
            for aff, (n,) in affs.items():
                put(unit, "inperson_early", aff, n)
                put(unit, "total", aff, n)
    as_of = (mail or inperson)["as_of"]
    county, state = [], []
    for unit, c in cells.items():
        if unit == "STATE":
            state += db.long_rows("OK", as_of, c)
        else:
            county += db.long_rows("OK", as_of, c, county=db.county_fips("OK", unit))
    return county, state


def ok_links(index_html: str) -> list[str]:
    """PDF links of the 'November 3 General Election' accordion of the absentee statistics page."""
    import html as _html
    out = []
    for p in re.split(r'<h3 class="cmp-accordion__header">', index_html)[1:]:
        t = re.search(r'cmp-accordion__title">([^<]*)<', p)
        if t and re.match(r"\s*November \d+ General", t.group(1)) and "2026" in "".join(re.findall(r'href="([^"]*\.pdf)"', p)):
            out += [_html.unescape(u) for u in re.findall(r'href="([^"]*\.pdf)"', p) if "2026" in u]
    return sorted({("https://oklahoma.gov" + u if u.startswith("/") else u).replace(" ", "%20") for u in out})


def _ok_validate(body: bytes) -> None:
    parse_ok_pdf(body)


@db.source("OK", "ok_ev_county", OK_INDEX, kind="county", format="pdf", cadence="daily ~07:30 CT (weekdays + weekend)",
           notes="State Election Board daily 'Ballot Totals by Affiliation' (absentee mail: Sent / Undeliverable / Received) and "
                 "'Absentee In-person Totals by Affiliation' (early in-person), one page per county + STATE page, by party "
                 "registration (Independent -> U; Libertarian -> other). mail_requested = ballots SENT; mail_returned = "
                 "Received (includes any not yet accepted); total = received + in-person. A report run before noon is dated the "
                 "previous day. STATE rows are the report's STATE page (verified == sum of county pages).")
def collect_ok(st):
    r = st.request(OK_INDEX)
    if r.status_code != 200:
        st.log("ok_ev_county", "failed", state="OK", url=OK_INDEX, status=r.status_code, message="index page")
        return
    links = ok_links(r.text)
    byname = {u.rsplit("/", 1)[-1]: u for u in links}
    # group by date stem: ...-MMDDYYYY[-pm].pdf  (mail 'ballot-totals-by-affiliation-', in-person 'absentee-inperson-totals-')
    groups: dict[str, dict[str, str]] = {}
    for name, u in byname.items():
        m = re.search(r"-(\d{8})(-pm)?\.pdf$", name)
        if not m:
            continue
        kind = "inperson" if "inperson" in name else "mail"
        groups.setdefault(m.group(1) + (m.group(2) or ""), {})[kind] = u
    parsed_all = {}
    fetched = {}
    for stem in sorted(groups, key=lambda s: (s[4:8], s[:4], s[8:])):
        for kind, u in groups[stem].items():
            f = st.fetch("ok_ev_county", u, ext="pdf", part=f"{stem}-{kind}", validate=_ok_validate)
            if f.ok:
                fetched[(stem, kind)] = f
    for stem in sorted({s for s, _ in fetched}):
        fm, fi = fetched.get((stem, "mail")), fetched.get((stem, "inperson"))
        if not any(f.needs_parse for f in (fm, fi) if f):
            continue
        mail = parse_ok_pdf(fm.body) if fm else None
        inp = parse_ok_pdf(fi.body) if fi else None
        county, state = ok_rows(mail, inp)
        anchor = (fi or fm)
        st.add_rows(county, source_id="ok_ev_county", snapshot_id=anchor.snapshot_id, level="county")
        st.add_rows(state, source_id="ok_ev_county", snapshot_id=anchor.snapshot_id, level="state")
        for f in (fm, fi):
            if f and f is not anchor:
                st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (f.snapshot_id,))
        st.db.commit()


# =====================================================================================================================
# OR - SoS unofficial ballot returns (Power BI export PDF): county, county x party, county x day
# =====================================================================================================================
OR_PDF = "https://sos.oregon.gov/elections/Documents/2026-general-election-unofficial-ballot-returns.pdf"
_OR_PARTY = {"Democrat": "D", "Republican": "R", "Nonaffiliated": "U"}      # everything else -> other
_NUMTOK = re.compile(r"\d[\d,]*")


def _or_table(page: str, ncols_hint: int | None = None) -> tuple[list[tuple[str, list[tuple[int, int]]]], list[int]]:
    """Rows of a right-aligned layout table: -> ([(name, [(token_end, value), ...])], total-row token ends)."""
    rows, total_ends = [], None
    for line in page.splitlines():
        m = re.match(r"^\s*([A-Z][A-Za-z .]*?)\s{2,}(\d.*)$", line)
        if not m:
            continue
        name = m.group(1).strip()
        if name == "County":                       # the header line of the by-day table
            continue
        toks = [(m.start(2) + t.end(), db.num(t.group())) for t in _NUMTOK.finditer(m.group(2))]
        if name == "Total":
            total_ends = [e for e, _ in toks]
        else:
            rows.append((name, toks))
    if total_ends is None:
        raise ValueError("OR table without Total row")
    return rows, total_ends


def _or_assign(toks: list[tuple[int, int]], ends: list[int]) -> list[int]:
    out = [0] * len(ends)
    for e, v in toks:
        j = min(range(len(ends)), key=lambda k: abs(ends[k] - e))
        out[j] = v
    return out


def parse_or_pdf(body: bytes) -> dict:
    return parse_or_pages(_pdf_pages(body))


def parse_or_pages(pages: list[str], expect_counties: int = 36) -> dict:
    """-> {'as_of', 'county': {name: {party: n}}, 'bydate': {name: {date: cumulative}}, 'state': {...}} (see code).

    'party' keys: D R U other ALL; 'bydate' values are cumulative ballots received through that day."""
    head = "\n".join(pages)
    m = re.search(r"Report Generated:\s*(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):\d{2}\s*([AP]M)", head)
    if not m:
        raise ValueError("OR: no Report Generated stamp")
    gen = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    year = int(re.search(r"Election Date:\s*\d{1,2}/\d{1,2}/(\d{4})", head).group(1))
    res = {"as_of": gen.isoformat(), "county": {}, "bydate": {}, "state": {}, "state_bydate": {}}
    for pg in pages:
        if "Returns by County by Party" in pg:
            hdr = next(l for l in pg.splitlines() if re.match(r"^\s*County\s+Democrat", l))
            names = re.findall(r"Democrat|Independent|Libertarian|No Labels|Nonaffiliated|Other|Pacific Green|Progressive|"
                               r"Republican|Working Families|Total", hdr)
            rows, ends = _or_table(pg)
            if len(ends) != len(names):
                raise ValueError(f"OR party table: {len(ends)} columns vs header {names}")
            for name, toks in rows + [("STATE", list(zip(ends, _or_total_vals(pg))))]:
                vals = dict(zip(names, _or_assign(toks, ends)))
                out = {"ALL": vals.pop("Total")}
                if sum(vals.values()) != out["ALL"]:
                    raise ValueError(f"OR {name}: party columns do not add to the total")
                for k in ("D", "R", "U", "other"):
                    out[k] = 0
                for pn, n in vals.items():
                    out[_OR_PARTY.get(pn, "other")] += n
                (res["state"] if name == "STATE" else res["county"].setdefault(name, {})).update(out)
        elif "Returns by County by Day" in pg:
            hdr = next(l for l in pg.splitlines() if re.match(r"^\s*County\s+\d{1,2}/\d{1,2}", l))
            dates = re.findall(r"\d{1,2}/\d{1,2}", hdr.split("Total")[0])
            rows, ends = _or_table(pg)
            if len(ends) != len(dates) + 1:
                raise ValueError(f"OR day table: {len(ends)} columns vs {len(dates)} dates")
            dts = [date(year, int(d.split("/")[0]), int(d.split("/")[1])).isoformat() for d in dates]
            for name, toks in rows + [("STATE", list(zip(ends, _or_total_vals(pg))))]:
                vals = _or_assign(toks, ends)
                cum, run = {}, 0
                for d_, v in zip(dts, vals[:-1]):
                    run += v
                    cum[d_] = run
                if abs(run - vals[-1]) > 1:                    # the report's own Total row can differ by 1 (rounding of the Power BI sum)
                    raise ValueError(f"OR {name}: daily counts {run} != Total {vals[-1]}")
                (res["state_bydate"] if name == "STATE" else res["bydate"].setdefault(name, {})).update(cum)
    if len(res["county"]) != expect_counties or not res["state"]:
        raise ValueError(f"OR: expected {expect_counties} counties, got {len(res['county'])}")
    return res


def _or_total_vals(page: str) -> list[int]:
    for line in page.splitlines():
        m = re.match(r"^\s*Total\s{2,}(\d.*)$", line)
        if m:
            return [db.num(t.group()) for t in _NUMTOK.finditer(m.group(1))]
    raise ValueError("no Total row")


def or_rows(res: dict) -> tuple[list[dict], list[dict]]:
    as_of = res["as_of"]
    county, state = [], []

    def cells(parties):
        c = {}
        for p, n in parties.items():
            c[("mail_returned", p)] = n
            c[("total", p)] = n                                    # all-mail state: ballots received == all ballots cast
        return c
    for name, parties in res["county"].items():
        county += db.long_rows("OR", as_of, cells(parties), county=db.county_fips("OR", name))
    state += db.long_rows("OR", as_of, cells(res["state"]))
    for name, cum in res["bydate"].items():
        for d_, n in cum.items():
            if d_ != as_of:
                county += db.long_rows("OR", d_, cells({"ALL": n}), county=db.county_fips("OR", name))
    for d_, n in res["state_bydate"].items():
        if d_ != as_of:
            state += db.long_rows("OR", d_, cells({"ALL": n}))
    return county, state


def _or_validate(body: bytes) -> None:
    parse_or_pdf(body)


@db.source("OR", "or_ballot_returns", OR_PDF, kind="county", format="pdf", cadence="daily (Power BI export, ~afternoon PT)",
           notes="SoS OregonVotes 'Unofficial Ballot Returns' PDF: ballots RECEIVED (envelopes marked returned; unofficial, not "
                 "necessarily accepted) by county x party (Democrat -> D, Republican -> R, Nonaffiliated -> U, all minor parties incl. "
                 "Independent Party -> other) and by county x received-day (cumulative, ALL; the first day column includes all "
                 "earlier days). All-mail state: mail_returned == total. as_of = date of 'Report Generated' (partial day). "
                 "State rows = the report's own Total rows.")
def collect_or(st):
    r = st.fetch("or_ballot_returns", OR_PDF, ext="pdf", validate=_or_validate)
    if r.needs_parse:
        county, state = or_rows(parse_or_pdf(r.body))
        st.add_rows(county, source_id="or_ballot_returns", snapshot_id=r.snapshot_id, level="county")
        st.add_rows(state, source_id="or_ballot_returns", snapshot_id=r.snapshot_id, level="state", mark_parsed=False)


# =====================================================================================================================
# ND - SoS absentee / vote-by-mail / early voting numbers (ASP.NET page; one POST per county)
# =====================================================================================================================
ND_URL = "https://vip.sos.nd.gov/abev.aspx?eid=348"
ND_COUNTY_FIELD = "ctl00$ContentPlaceHolder1$ddlCounty"


def _nd_text(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).replace("&nbsp;", " ")


def parse_nd_state(html: str, as_of: str) -> list[dict]:
    """Statewide block: absentee + vote-by-mail sent/returned, early voting turnout, total cast before Election Day."""
    t = _nd_text(html)

    def g(label):
        m = re.search(re.escape(label) + r"\s+([\d,]+)", t)
        if not m:
            raise ValueError(f"ND: missing {label!r}")
        return db.num(m.group(1))
    sent = g("Total Absentee / Vote-By-Mail Ballots Sent (Statewide)")
    ret = g("Total Absentee / Vote-By-Mail Ballots Returned (Statewide)")
    early = g("Early Voting Turnout")
    total = g("Total Ballots Cast prior to Election Day")
    if ret + early != total:
        raise ValueError(f"ND: returned {ret} + early {early} != total {total}")
    return db.long_rows("ND", as_of, {("mail_requested", "ALL"): sent, ("mail_returned", "ALL"): ret,
                                      ("inperson_early", "ALL"): early, ("total", "ALL"): total})


def parse_nd_county(html: str, as_of: str) -> tuple[str, list[dict]]:
    """County block 'Cass County Absentee and Vote-By-Mail Ballots N Ballots Sent N Ballots Returned N Early Voters'."""
    t = _nd_text(html)
    m = re.search(r"County Results (.+?) County Absentee and Vote-By-Mail Ballots ([\d,]+) Ballots Sent ([\d,]+) "
                  r"Ballots Returned(?: ([\d,]+) Early Voters)?", t)
    if not m:
        raise ValueError("ND: no county block")
    name, sent, ret = m.group(1), db.num(m.group(2)), db.num(m.group(3))
    early = db.num(m.group(4)) if m.group(4) else 0            # the 'Early Voters' line is omitted for counties with none
    cells = {("mail_requested", "ALL"): sent, ("mail_returned", "ALL"): ret, ("inperson_early", "ALL"): early,
             ("total", "ALL"): ret + early}
    return name, db.long_rows("ND", as_of, cells, county=db.county_fips("ND", name))


def nd_counties(html: str) -> list[tuple[str, str]]:
    sel = re.search(r'<select name="ctl00\$ContentPlaceHolder1\$ddlCounty".*?</select>', html, flags=re.S)
    if not sel:
        raise ValueError("ND: county dropdown missing")
    return [(v, n) for v, n in re.findall(r'<option value="(\d+)">([^<]+)</option>', sel.group(0))]


def _nd_form(html: str, county: str) -> bytes:
    d = {k: re.search(r'name="%s" id="%s" value="([^"]*)"' % (k, k), html).group(1)
         for k in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")}
    d.update({"__EVENTTARGET": ND_COUNTY_FIELD, "__EVENTARGUMENT": "", ND_COUNTY_FIELD: county})
    return urllib.parse.urlencode(d).encode()


@db.source("ND", "nd_abev", ND_URL, kind="county", format="html", cadence="continuous (page has no timestamp)",
           notes="SoS 'Absentee / Vote-By-Mail Ballot and Early Voting Numbers'. No party registration in ND -> party ALL. "
                 "State: absentee + vote-by-mail ballots sent (mail_requested), returned, early voters (inperson_early) and "
                 "'Total ballots cast prior to Election Day' (= returned + early). Counties: one dropdown POST each (sent, returned, "
                 "early voters, 0 when the page omits the line; total = returned + early, derived). The page states no as-of time, so as_of = fetch date (Central).")
def collect_nd(st):
    r = st.fetch("nd_abev", ND_URL, ext="html", part="state", conditional=False,
                 validate=lambda b: parse_nd_state(b.decode("utf8", "replace"), "2000-01-01"))
    if not r.ok:
        return
    as_of = datetime.now(CT).date().isoformat()
    html = r.body.decode("utf8", "replace")
    if r.needs_parse:
        st.add_rows(parse_nd_state(html, as_of), source_id="nd_abev", snapshot_id=r.snapshot_id, level="state")
    rows, snaps = [], []
    for code, name in nd_counties(html):
        try:
            resp = st.request(ND_URL, method="POST", content=_nd_form(html, code),
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        except db.FetchError as e:
            st.log("nd_abev", "failed", state="ND", part=code, message=str(e))
            continue
        if resp.status_code != 200:
            st.log("nd_abev", "failed", state="ND", part=code, status=resp.status_code, message=name)
            continue
        f = st.save_body("nd_abev", ND_URL, resp.content, ext="html", part=f"c{code}", state="ND",
                         validate=lambda b: parse_nd_county(b.decode("utf8", "replace"), "2000-01-01"))
        if f.needs_parse:
            rows += parse_nd_county(f.body.decode("utf8", "replace"), as_of)[1]
            snaps.append(f.snapshot_id)
    if rows:
        st.add_rows(rows, source_id="nd_abev", snapshot_id=snaps[0], level="county")
        for s in snaps[1:]:
            st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (s,))
        st.db.commit()


# =====================================================================================================================
# Verified not collected (see notes: each URL was requested once with the project UA; nothing was bypassed)
# =====================================================================================================================
db.not_collected("MT", "mt_absentee_count", "https://sosmt.gov/elections/absentee-ballot-count/",
                 access="blocked: HTTP 403 (bot wall) for sosmt.gov and electionresults.mt.gov to the project UA (2026-10-31)",
                 notes="SoS 'Montana Absentee Ballots By County' tracker (sent/returned by county, no party). Not retrievable "
                       "without bypassing the 403; press cites 60,329 returned of 520,035 sent as of 10/19 only.",
                 format="html", kind="county")
db.not_collected("OH", "oh_ev_dashboard", "https://data.ohiosos.gov/portal/election-dashboards",
                 access="blocked: HTTP 403 to the project UA on data.ohiosos.gov and www.ohiosos.gov (2026-10-31)",
                 notes="SoS election dashboard (absentee requests/returns, early in-person by county). Not retrievable honestly.",
                 format="html", kind="county")
db.not_collected("NV", "nv_vivid", "https://www.nvsos.gov/vivid/",
                 access="blocked: Incapsula challenge page (JS shell, 932 bytes) on nvsos.gov (2026-10-31)",
                 notes="VIVID mail-in ballot tracker / early-voting turnout dashboard; behind an Imperva challenge.",
                 format="dashboard", kind="county")
db.not_collected("NH", "nh_absentee", "https://www.sos.nh.gov/elections/absentee-ballots",
                 access="blocked: HTTP 403 to the project UA on www.sos.nh.gov (2026-10-31)",
                 notes="No absentee count dataset identified; only a press release (absentee ballots available from 10/14).")
db.not_collected("NY", "ny_ev_public_reporting", "https://publicreporting.elections.ny.gov/",
                 access="blocked: HTTP 403 to the project UA on publicreporting.elections.ny.gov and elections.ny.gov (2026-10-31)",
                 notes="NYSBOE early-voting turnout by county is only in the public reporting portal. Not retrievable honestly.")
db.not_collected("NE", "ne_early_voting", "https://sos.nebraska.gov/elections/early-voting",
                 access="not published: page is voter guidance only (no counts, no data files; 2026-10-31)",
                 notes="Nebraska SoS publishes no statewide early/absentee counts; county election offices hold the data.")
db.not_collected("NM", "nm_ev_results_portal", "https://electionresults.sos.nm.gov/resultsSW.aspx?map=CTY&type=EDX",
                 access="not published: results portal is election-night only (shows 'Results last updated 10/22/2026', turnout 0); "
                        "no early/absentee tally page or file on sos.nm.gov (data-and-maps, voter-data-information, early-voting pages checked 2026-10-31)",
                 notes="Press-cited early-vote totals (e.g. ~160,000 by 10/21) come from SoS news releases / SERVIS, not a data publication.")
db.not_collected("NJ", "nj_periodic_reports", "https://nj.gov/state/elections/election-results-information.shtml",
                 access="not published pre-election: 2026 'Periodic Election Reporting' lists only election-night/post-election "
                        "county files (2026-1103 .. 2026-1112 links, not yet posted); no pre-election VBM/early-vote statistics (2026-10-31)",
                 notes="Re-check on/after 2026-11-03: periodic reports by county. Press figures on NJ VBM by party come from a "
                       "third-party analysis of the per-voter file, not an official aggregate.",
                 format="pdf", kind="county")
