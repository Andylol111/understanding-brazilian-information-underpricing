"""Early-vote collectors, batch D: SC, SD, WV (collected) and WI, WA, TN, VT, UT, VA, WY, RI (recorded as not collected).

Parsers are pure functions of bytes; the collectors only discover URLs, fetch and hand the bytes to the parsers.
openpyxl is imported lazily.
"""
from __future__ import annotations

import csv
import html as _html
import io
import re
from datetime import date, datetime

import us_ev_db as db

# ======================================================================================= helpers

_TAG = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", s))).strip()


def _us_date(s: str, default_year: int | None = None) -> str:
    """'10/30/2026', '7/28/26', ' 10/19/2026' -> '2026-10-30'."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*", s)
    if not m:
        raise ValueError(f"bad date {s!r}")
    mo, d, y = int(m[1]), int(m[2]), m[3]
    yr = default_year if y is None else (int(y) + 2000 if len(y) == 2 else int(y))
    return date(yr, mo, d).isoformat()


# ======================================================================================= SC

SC_PAGE = "https://scvotes.gov/elections-statistics/voter-participation/"
SC_EV_RE = re.compile(r'href="(https://scvotes\.gov/wp-content/uploads/\d{4}/\d{2}/EV-Totals-(\d{2})-(\d{2})-(\d{2})\.csv)"', re.I)
SC_ABS_RE = re.compile(r'href="(https://scvotes\.gov/wp-content/uploads/\d{4}/\d{2}/(\d{4})-(\d{2})-(\d{2})-Absentee-Statistics-by-Race-for-Election\.xlsx)"', re.I)


def parse_sc_ev_csv(body: bytes, min_counties: int = 40) -> tuple[list[dict], list[dict]]:
    """SC daily early-voting CSV: one row per county (summary line carries the election name; indented lines are
    poll places), one column per voting day (daily counts). Returns (county_rows, state_rows): CUMULATIVE in-person
    early votes through each day column, as_of_date = that day."""
    text = body.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    hi = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Jurisdiction"), None)
    if hi is None:
        raise ValueError("SC EV csv: header row 'Jurisdiction' not found")
    hdr = rows[hi]
    days = [(j, _us_date(c)) for j, c in enumerate(hdr) if j >= 3 and re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{4}\s*", c)]
    if not days:
        raise ValueError("SC EV csv: no date columns")
    county, state = [], []

    def cum(r):
        run, out = 0, []
        for j, d in days:
            run += db.num(r[j]) if j < len(r) else 0
            out.append((d, run))
        return out

    for r in rows[hi + 1:]:
        name = r[0].strip()
        if not name:
            continue
        if name == "South Carolina":
            for d, v in cum(r):
                state.append({"state": "sc", "as_of_date": d, "measure": "inperson_early", "party": "ALL", "count": v})
        elif len(r) > 1 and r[1].strip():
            fips = db.county_fips("sc", name)
            for d, v in cum(r):
                county.append({"state": "sc", "county_fips": fips, "as_of_date": d, "measure": "inperson_early",
                               "party": "ALL", "count": v})
    if len({r["county_fips"] for r in county}) < min_counties:
        raise ValueError("SC EV csv: too few counties")
    return county, state


def _sc_xlsx_validate(body: bytes) -> None:
    parse_sc_absentee_xlsx(body, "2026-01-01")


def parse_sc_absentee_xlsx(body: bytes, as_of: str, min_counties: int = 40) -> tuple[list[dict], list[dict]]:
    """SC 'Absentee Statistics for Election By Race' workbook, sheet 'Race ALL' (the other sheets are per race and not
    used). Per county: Ballots Issued -> mail_requested (absentee ballots issued, incl. UOCAVA), Ballots Total
    Returned (before + after the deadline, not all necessarily accepted) -> mail_returned."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
    ws = None
    for s in wb.worksheets:
        for r in s.iter_rows(min_row=1, max_row=10, values_only=True):
            if any(isinstance(c, str) and c.strip() == "Race ALL" for c in r):
                ws = s
                break
        if ws is not None:
            break
    if ws is None:
        raise ValueError("SC absentee xlsx: sheet 'Race ALL' not found")
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if len(r) > 11 and r[1] == "County"), None)
    if hi is None:
        raise ValueError("SC absentee xlsx: header not found")
    h = rows[hi]
    if not (str(h[8]).strip() == "Issued" and "Total" in str(h[11]) and "Returned" in str(h[11])):
        raise ValueError("SC absentee xlsx: unexpected column layout")
    county, state = [], []
    for r in rows[hi + 1:]:
        if len(r) <= 11 or not isinstance(r[1], str) or not r[1].strip():
            continue
        name = r[1].strip()
        cells = {("mail_requested", "ALL"): db.num(r[8]), ("mail_returned", "ALL"): db.num(r[11])}
        if name == "Total":
            state += db.long_rows("sc", as_of, cells)
            continue
        m = re.fullmatch(r"\d+-(.+)", name)
        if m:
            county += db.long_rows("sc", as_of, cells, county=db.county_fips("sc", m[1].title()))
    if len({r["county_fips"] for r in county}) < min_counties or not state:
        raise ValueError("SC absentee xlsx: incomplete")
    return county, state


@db.source("sc", "sc_ev_county", SC_PAGE, kind="county+state", format="csv", cadence="daily ~10:00 ET",
           notes="SC State Election Commission EV-Totals-<MM-DD-YY>.csv (link discovered on the voter-participation page). "
                 "In-person early voting only, per county and statewide, one column per voting day; the collector stores the "
                 "running sum, as_of_date = each column's day (report date is the next morning). No party split. Mail "
                 "absentee is in sc_absentee_county.")
def collect_sc_ev_county(st):
    page = st.request(SC_PAGE).text
    m = SC_EV_RE.search(page)
    if not m:
        raise RuntimeError("SC: no EV-Totals link on the voter-participation page")
    url, label = m[1], f"20{m[4]}-{m[2]}-{m[3]}"
    r = st.fetch("sc_ev_county", url, ext="csv", part=label, validate=lambda b: parse_sc_ev_csv(b))
    if r.needs_parse:
        county, state = parse_sc_ev_csv(r.body)
        st.add_rows(county, source_id="sc_ev_county", snapshot_id=r.snapshot_id, level="county")
        st.add_rows(state, source_id="sc_ev_county", snapshot_id=r.snapshot_id, level="state")


@db.source("sc", "sc_absentee_county", SC_PAGE, kind="county+state", format="xlsx", cadence="daily (business days) by ~10:00 ET",
           notes="SC '<date>-Absentee-Statistics-by-Race-for-Election.xlsx', sheet 'Race ALL' (race sheets ignored). "
                 "mail_requested = absentee ballots ISSUED (Ballots/Issued, incl. UOCAVA); mail_returned = ballots total "
                 "returned before+after the deadline (receipt, not acceptance). as_of_date = date in the file name "
                 "[INFERENCE: through that day]. Statewide row = the report's own Total row. No party.")
def collect_sc_absentee_county(st):
    page = st.request(SC_PAGE).text
    m = SC_ABS_RE.search(page)
    if not m:
        raise RuntimeError("SC: no absentee-by-race link on the voter-participation page")
    url0, d0 = m[1], date(int(m[2]), int(m[3]), int(m[4]))
    for back in range(0, 6):                                     # newest on the page, then the previous days
        d = date.fromordinal(d0.toordinal() - back)
        url = url0.replace(d0.isoformat(), d.isoformat()) if back else url0
        if back:
            url = re.sub(r"/uploads/\d{4}/\d{2}/", f"/uploads/{d:%Y/%m}/", url)
        r = st.fetch("sc_absentee_county", url, ext="xlsx", part=d.isoformat(), validate=_sc_xlsx_validate)
        if r.needs_parse:
            county, state = parse_sc_absentee_xlsx(r.body, d.isoformat())
            st.add_rows(county, source_id="sc_absentee_county", snapshot_id=r.snapshot_id, level="county")
            st.add_rows(state, source_id="sc_absentee_county", snapshot_id=r.snapshot_id, level="state")


# ======================================================================================= SD

SD_URL = "https://www.sdsos.gov/elections-voting/upcoming-elections/general-information/2026-Election-Absentee-Data.aspx"
SD_PARTY = {"DEM": "D", "REP": "R", "NPA": "U", "IND": "other", "LIB": "other", "OTH": "other"}


def _html_tables(page: str) -> list[list[list[str]]]:
    out = []
    for t in re.findall(r"<table.*?</table>", page, re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", t, re.S | re.I):
            cells = [_text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
            if any(cells):
                rows.append(cells)
        out.append(rows)
    return out


def parse_sd_absentee(body: bytes) -> list[dict]:
    """SD weekly absentee page. First 'Date' table = statewide cumulative by week; first 'Party' table = party
    breakout for the page's latest week ('Party Breakout ... as of <date>'). Columns: Ballots Sent, Ballots Received
    (INCLUDES walk-in ballots, per the page's footnote), Walk-in Voters, UOCAVA (not stored).
    Mapping: mail_requested = sent; total = received; inperson_early = walk-in; mail_returned = received - walk-in.
    The latest date row's walk-in cell has been seen to duplicate the REP walk-in; when a party breakout exists for
    that date the party sums (which reconcile to sent/received) replace the date row's walk-in."""
    page = body.decode("utf-8", errors="replace")
    tabs = _html_tables(page)
    date_t = next((t for t in tabs if t and t[0][:1] == ["Date"]), None)
    party_t = next((t for t in tabs if t and t[0][:1] == ["Party"]), None)
    if date_t is None:
        raise ValueError("SD: date table not found")
    byd: dict[str, tuple[int, int, int]] = {}
    for r in date_t[1:]:
        if len(r) >= 4 and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", r[0]):
            byd[_us_date(r[0])] = (db.num(r[1]), db.num(r[2]), db.num(r[3]))
    rows: list[dict] = []
    if party_t is not None:
        ctx = _text(page[: page.find("<table", page.find("Party Breakout"))]) if "Party Breakout" in page else ""
        m = re.findall(r"Party Breakout[^<]*?as of\s*(\d{1,2}/\d{1,2}/\d{2,4})", ctx)
        if not m:
            raise ValueError("SD: party breakout date not found")
        pd_ = _us_date(m[0] if len(m) == 1 else m[0])
        acc: dict[str, list[int]] = {}
        for r in party_t[1:]:
            if len(r) >= 4 and r[0] in SD_PARTY:
                a = acc.setdefault(SD_PARTY[r[0]], [0, 0, 0])
                for k in range(3):
                    a[k] += db.num(r[1 + k])
        if not acc:
            raise ValueError("SD: no party rows")
        sums = [sum(v[k] for v in acc.values()) for k in range(3)]
        if pd_ in byd and byd[pd_][:2] == (sums[0], sums[1]):
            byd[pd_] = (sums[0], sums[1], sums[2])
        for p, (s, rc, w) in acc.items():
            rows += db.long_rows("sd", pd_, {("mail_requested", p): s, ("total", p): rc, ("inperson_early", p): w,
                                              ("mail_returned", p): max(rc - w, 0)})
    for d, (s, rc, w) in byd.items():
        rows += db.long_rows("sd", d, {("mail_requested", "ALL"): s, ("total", "ALL"): rc, ("inperson_early", "ALL"): w,
                                        ("mail_returned", "ALL"): max(rc - w, 0)})
    return rows


def _sd_validate(body: bytes) -> None:
    parse_sd_absentee(body)


@db.source("sd", "sd_absentee", SD_URL, kind="state", format="html", cadence="weekly (Fridays)",
           notes="SD SoS 2026 absentee page, statewide only. Weekly cumulative rows (all parties) plus party breakout "
                 "(D, R, NPA->U, IND/LIB/OTH->other) for the latest week. mail_requested = Ballots Sent (may include "
                 "walk-in ballots issued); total = Ballots Received incl. walk-in; inperson_early = Walk-in voters; "
                 "mail_returned = Received - Walk-in (derived). UOCAVA column not stored. No county data.")
def collect_sd_absentee(st):
    r = st.fetch("sd_absentee", SD_URL, ext="html", validate=_sd_validate)
    if r.needs_parse:
        st.add_rows(parse_sd_absentee(r.body), source_id="sd_absentee", snapshot_id=r.snapshot_id, level="state")


# ======================================================================================= WV

WV_NEWS = "https://sos.wv.gov/news"
WV_KINDS = (("In-person early votes cast", "inperson_early"), ("Absentee ballots requested", "mail_requested"),
            ("Absentee ballots returned", "mail_returned"))
WV_PARTY = {"DEMOCRATIC PARTY": "D", "REPUBLICAN PARTY": "R", "NO PARTY": "U", "CONSTITUTION PARTY": "other",
            "MOUNTAIN PARTY": "other", "LIBERTARIAN PARTY": "other", "OTHER PARTY": "other"}
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def parse_wv_article(body: bytes) -> tuple[str, dict[str, str]]:
    """WV SoS early/absentee press release -> (as_of_date, {measure: xlsx url}). 'as of end of day Wednesday, October 28th'."""
    page = body.decode("utf-8", errors="replace")
    m = re.search(r"as of end of day\s+\w+,\s+([A-Za-z]+)\s+(\d{1,2})", page)
    if not m or m[1].lower() not in _MONTHS:
        raise ValueError("WV: as-of date not found")
    ym = re.search(r"(20\d\d) General Election", page)
    as_of = date(int(ym[1]) if ym else 2026, _MONTHS[m[1].lower()], int(m[2])).isoformat()
    links = {}
    for label, meas in WV_KINDS:
        mm = re.search(re.escape(label) + r":?\s*(?:<[^>]*>\s*)*<a [^>]*href=\"(https://files\.constantcontact\.com/[^\"]+\.xlsx)\"", page)
        if mm:
            links[meas] = mm[1]
    if len(links) != 3:
        raise ValueError(f"WV: expected 3 xlsx links, found {sorted(links)}")
    return as_of, links


def parse_wv_xlsx(body: bytes, min_counties: int = 50) -> tuple[dict[str, dict[str, int]], dict[str, int] | None]:
    """WV county x party xlsx -> ({COUNTY: {party: n}}, statewide row or None). Party columns (Constitution, Mountain,
    Libertarian, Other -> 'other', No Party -> U), TOTAL VOTERS -> ALL."""
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True).active
    rows = [r for r in ws.iter_rows(values_only=True)]
    if not rows or str(rows[0][0]).strip().upper() != "COUNTY":
        raise ValueError("WV xlsx: header not found")
    cols = [(j, str(c).strip().upper()) for j, c in enumerate(rows[0]) if j > 0 and c]
    if "TOTAL VOTERS" not in [c for _, c in cols]:
        raise ValueError("WV xlsx: TOTAL VOTERS column missing")
    out: dict[str, dict[str, int]] = {}
    state = None
    for r in rows[1:]:
        if not r[0]:
            continue
        cells: dict[str, int] = {}
        for j, c in cols:
            p = "ALL" if c == "TOTAL VOTERS" else WV_PARTY.get(c)
            if p is None:
                raise ValueError(f"WV xlsx: unknown column {c}")
            cells[p] = cells.get(p, 0) + db.num(r[j])
        nm = str(r[0]).strip().upper()
        if nm == "WEST VIRGINIA":
            state = cells
        else:
            out[nm] = cells
    if len(out) < min_counties:
        raise ValueError("WV xlsx: too few counties")
    return out, state


def wv_rows(as_of: str, parsed: dict[str, tuple[dict, dict | None]]) -> tuple[list[dict], list[dict]]:
    """parsed {measure: parse_wv_xlsx(...)} -> (county rows, state rows) including derived total = in-person + returned.
    Statewide figure = the file's WEST VIRGINIA row when present, otherwise the sum of counties."""
    county, state = [], []
    per_c: dict[str, dict[tuple[str, str], int]] = {}
    per_s: dict[tuple[str, str], int] = {}
    for meas, (cnt, st_row) in parsed.items():
        for nm, cells in cnt.items():
            for p, v in cells.items():
                per_c.setdefault(nm, {})[(meas, p)] = v
        tot = st_row or {p: sum(c[p] for c in cnt.values()) for p in next(iter(cnt.values()))}
        for p, v in tot.items():
            per_s[(meas, p)] = v
    for d in list(per_c.values()) + [per_s]:
        for (meas, p), v in list(d.items()):
            if meas == "inperson_early" and ("mail_returned", p) in d:
                d[("total", p)] = v + d[("mail_returned", p)]
    for nm, cells in per_c.items():
        county += db.long_rows("wv", as_of, cells, county=db.county_fips("wv", nm.title()))
    state += db.long_rows("wv", as_of, per_s)
    return county, state


def _wv_xlsx_validate(body: bytes) -> None:
    parse_wv_xlsx(body)


@db.source("wv", "wv_absentee_county", WV_NEWS, kind="county+state", format="xlsx", cadence="irregular (press releases ~every 2-3 days)",
           notes="WV SoS press releases 'Secretary Warner announces ... ballots have been cast' (found via sos.wv.gov/news) "
                 "with three county x party XLSX attachments on files.constantcontact.com: in-person early votes cast, "
                 "absentee ballots requested, absentee ballots returned; cumulative as of end of the day named in the release. "
                 "Parties: D, R, NO PARTY->U, Constitution/Mountain/Libertarian/Other->other. State row = file's WEST VIRGINIA "
                 "row (in-person file only); for requested/returned it is the sum of counties. total = in-person + returned "
                 "(derived; matches the release headline).")
def collect_wv_absentee_county(st):
    news = st.request(WV_NEWS).text
    slugs = sorted(set(re.findall(r'href="(/article/[a-z0-9-]*ballots-have-been-cast[a-z0-9-]*)"', news)))
    for slug in slugs:
        a = st.fetch("wv_absentee_county", "https://sos.wv.gov" + slug, ext="html", part="article-" + slug[-12:],
                     validate=lambda b: parse_wv_article(b))
        if not a.ok:
            continue
        as_of, links = parse_wv_article(a.body)
        parsed, snaps = {}, []
        for meas, url in links.items():
            r = st.fetch("wv_absentee_county", url, ext="xlsx", part=f"{as_of}-{meas}", validate=_wv_xlsx_validate)
            if not r.ok:
                break
            parsed[meas] = parse_wv_xlsx(r.body)
            snaps.append(r)
        else:
            if any(r.needs_parse for r in snaps):
                county, state = wv_rows(as_of, parsed)
                sid = max(r.snapshot_id for r in snaps)
                st.add_rows(county, source_id="wv_absentee_county", snapshot_id=sid, level="county")
                st.add_rows(state, source_id="wv_absentee_county", snapshot_id=sid, level="state")
                for r in snaps:                       # all three snapshots are now represented in the DB
                    st.db.execute("UPDATE snapshots SET parsed=1 WHERE snapshot_id=?", (r.snapshot_id,))
                st.db.commit()


# ======================================================================================= not collected

db.not_collected(
    "wi", "wi_absentee_report",
    "https://elections.wi.gov/resources/statistics/absentee-ballot-report-november-3-2026-general-election-0",
    access="blocked: Cloudflare managed challenge (HTTP 403, cf-mitigated: challenge) for non-browser clients",
    notes="WEC daily absentee report (county/municipal CSV + ZIP of daily reports) exists on the page but the page and "
          "hence the file links are behind a Cloudflare challenge; verified 2026-10-31 with the honest UA. Not circumvented.",
    cadence="daily 7:30 a.m.", format="csv")
db.not_collected(
    "wa", "wa_ballot_return_stats",
    "https://www.sos.wa.gov/elections/data-research/election-data-and-maps/ballot-return-statistics",
    access="dashboard-only: Power BI publish-to-web embed (app.powerbigov.us); ballot-status-reports page returns HTTP 403",
    notes="Ballot Return Statistics page (posted after 5 p.m. business days) embeds a Power BI report; no CSV/XLSX export link "
          "on the page (only PDFs unrelated to the data). /ballot-status-reports answers 403 to our client. Not collected.",
    cadence="business days after 5 p.m.", format="powerbi")
db.not_collected(
    "tn", "tn_early_voting_stats", "https://sos.tn.gov/elections/pages/early-voting-statistics",
    access="blocked: HTTP 403 (CloudFront/WAF) on sos.tn.gov, tnsos.org/elections/ElectionData and sos-tn-gov-files.tnsosfiles.com PDF",
    notes="County early-vote PDF and the tnsos.org early-voting-turnout pages all return 403 to the honest client "
          "(verified 2026-10-31, incl. robots.txt); no open alternative file found. Not circumvented.",
    cadence="daily", format="pdf")
db.not_collected(
    "vt", "vt_ballot_return_press", "https://sos.vermont.gov/secretarys-desk/commentary-press/ballot-return-options-and-early-voting-stats",
    access="press release only (no dataset)",
    notes="Only a 2026-10-22 press release ('over 88,000 returned', top-10 towns as % of active voters); no town/county "
          "table or file published, and 'over N' is a lower bound. No party registration. Not collected.",
    format="html")
db.not_collected(
    "ut", "ut_ev_stats", "https://vote.utah.gov/election-results-data-historical-information/",
    access="not published (in progress)",
    notes="Lt. Governor posts only post-canvass aggregates (Master-Aggregated-Numbers-2023-2026.xlsx, Dec 2025); no "
          "in-progress ballots-returned file or page found 2026-10-31.",
    format="xlsx")
db.not_collected(
    "va", "va_absentee_stats", "https://www.elections.virginia.gov/resultsreports/registrationturnout-statistics/",
    access="not published (in progress); CSV host apps.elections.virginia.gov has robots.txt Disallow: /",
    notes="Department of Elections publishes turnout only after certification (CSV dir under apps.elections.virginia.gov, "
          "which disallows crawlers); no in-progress absentee/early statistics page. Not collected.",
    format="csv")
db.not_collected(
    "wy", "wy_absentee_stats", "https://sos.wyo.gov/elections/state/absenteevoting.aspx",
    access="not published",
    notes="SoS absentee page is instructions and forms only; no statewide or county early/absentee statistics found "
          "(counties report to the SoS only at canvass).",
    format="html")
db.not_collected(
    "ri", "ri_ev_stats", "https://elections.ri.gov/voter-resources/person-early-voting-emergency-mail-ballots",
    access="not published; elections.ri.gov answers HTTP 403 to the honest client",
    notes="Board of Elections publishes no running early/mail statistics (press cites figures 'shared with' reporters); "
          "results-only PDFs after the election. Not circumvented.",
    format="html")
