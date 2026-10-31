"""Early-vote collectors, group B: IL IN IA KS KY LA ME MD MA MI MN MS MO.

collected : MD (county x party: in-person EV raw CSV + mail-in sent/received/accepted XLSX)
            MS (statewide, weekly news post) · ME (state x party from the absentee voter file)
not collected (verified reasons, see the not_collected() calls at the bottom): LA MN IA MI KS KY IL IN MA MO.

Parsers are pure functions of bytes/text; the voter-level Maine file is only kept as a raw snapshot on the SSD and
reduced to state x party counts in memory.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

import pandas as pd

import us_ev_db as db
from us_ev_db import not_collected, source

# ================================================================================================ Mississippi
# The weekly SEMS absentee report (county PDF) is hosted on mcusercontent.com, whose robots.txt is `Disallow: /`, so only
# the statewide figures in the sos.ms.gov news post (allowed) are collected.

MS_NEWS = "https://www.sos.ms.gov/news/2026-november-general-election-absentee-report-{slug}"
_MS_TXT = re.compile(r"As of\s+\w+,\s+([A-Za-z]+ \d{1,2}, \d{4}),.{0,200}?reported a total of\s+([\d,]+)\s+absentee ballots\s+requested"
                     r".{0,40}?([\d,]+)\s+absentee ballots\s+sent.{0,60}?([\d,]+)\s+absentee ballots\s+received", re.S)


def parse_ms_release(html: str) -> list[dict]:
    """Statewide absentee counts from the sos.ms.gov weekly news post -> state rows (requested, returned=received)."""
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"\s+", " ", t.replace("&nbsp;", " "))
    m = _MS_TXT.search(t)
    if not m:
        raise ValueError("MS: no absentee sentence")
    as_of = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
    req, _sent, rec = (int(m.group(i).replace(",", "")) for i in (2, 3, 4))
    return db.long_rows("MS", as_of, {("mail_requested", "ALL"): req, ("mail_returned", "ALL"): rec, ("total", "ALL"): rec})


def _ms_validate(body: bytes) -> None:
    parse_ms_release(body.decode("utf-8", "ignore"))


def ms_slugs(today: date, n: int = 6) -> list[str]:
    """Reports are posted on Mondays: slugs like 'october-26' for the last n Mondays."""
    out, d = [], today
    while len(out) < n and d >= date(2026, 9, 1):
        if d.weekday() == 0:
            out.append(f"{d.strftime('%B').lower()}-{d.day}")
        d -= timedelta(days=1)
    return out


@source("ms", "ms_absentee_state", "https://www.sos.ms.gov/news", kind="state", format="html", cadence="weekly (Mondays)",
        notes="Mississippi SoS weekly news post 'November General Election Absentee Report' (SEMS): statewide absentee ballots "
              "requested / sent / received as of the Monday. mail_requested = requested, mail_returned = received, total = "
              "received (incl. possibly rejected; MS in-person absentee is part of absentee). Sent not stored. State level only: "
              "the county PDF is on mcusercontent.com (robots.txt Disallow: /) and is not collected.")
def collect_ms_absentee_state(st):
    for slug in ms_slugs(datetime.now().date()):
        r = st.fetch("ms_absentee_state", MS_NEWS.format(slug=slug), ext="html", part=slug, conditional=False,
                     validate=_ms_validate)
        if r.needs_parse:
            st.add_rows(parse_ms_release(r.body.decode("utf-8", "ignore")), source_id="ms_absentee_state",
                        snapshot_id=r.snapshot_id, level="state")


# ================================================================================================ Maryland
MD_BASE = "https://elections.maryland.gov/press_room/2026_stats/GG26/"
MD_EV_FIRST_DAY = date(2026, 10, 22)           # early voting Oct 22-29 2026 (8 days; SBE press releases)


def _md_party(code: str) -> str:
    return {"DEM": "D", "REP": "R", "UNA": "U"}.get(str(code).strip().upper(), "other")


def parse_md_early_raw(body: bytes, first_day: date = MD_EV_FIRST_DAY) -> tuple[list[dict], list[dict]]:
    """EarlyVoting_RAW_data.csv: one row per precinct x voter demographic x party x vote center with Day1..DayN counts of
    in-person early votes. -> cumulative county x party rows per day (as_of = that day's date) + state rows (sum of
    counties; the source has no state figure)."""
    df = pd.read_csv(io.BytesIO(body), encoding="utf-8-sig", low_memory=False)
    days = sorted((c for c in df.columns if re.fullmatch(r"(?i)day\d+", c)), key=lambda c: int(c[3:]))
    df["party"] = df["PARTY_CODE"].map(_md_party)
    df[days] = df[days].fillna(0)
    g = df.groupby(["COUNTY_NAME", "party"])[days].sum()
    last = max((i for i, c in enumerate(days) if df[c].sum() > 0), default=-1)
    if last < 0:
        raise ValueError("MD: no early votes in file")
    cum = g[days[: last + 1]].cumsum(axis=1)
    crow, srow = [], []
    for k, c in enumerate(days[: last + 1]):
        d = (first_day + timedelta(days=k)).isoformat()
        s = cum[c].groupby(level="party").sum()
        for (cty, p), v in cum[c].items():
            crow.append({"state": "MD", "county_fips": db.county_fips("MD", cty), "as_of_date": d,
                         "measure": "inperson_early", "party": p, "count": int(v)})
        for p, v in s.items():
            srow.append({"state": "MD", "as_of_date": d, "measure": "inperson_early", "party": p, "count": int(v)})
        srow.append({"state": "MD", "as_of_date": d, "measure": "inperson_early", "party": "ALL", "count": int(s.sum())})
        for cty, v in cum[c].groupby(level="COUNTY_NAME").sum().items():
            crow.append({"state": "MD", "county_fips": db.county_fips("MD", cty), "as_of_date": d,
                         "measure": "inperson_early", "party": "ALL", "count": int(v)})
    return crow, srow


def _md_as_of(text) -> str:
    m = re.search(r"As of:\s*([A-Za-z]+ \d{1,2}, \d{4})", str(text))
    if not m:
        raise ValueError("MD: no 'As of' line")
    return datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")


def parse_md_sent_returned(body: bytes) -> tuple[list[dict], list[dict]]:
    """Absentees_Sent_and_Returned_by_County.xlsx, first block (CATEGORY 'ALL' = all voter categories) -> county x party
    (D/R/other=OTH) rows: sent -> mail_requested, received -> mail_returned, accepted -> mail_accepted, plus ALL party.
    'Accepted' lags 'received' (counties canvass after Oct 13; counties still at 0 have not started)."""
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(body), data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    as_of = _md_as_of(next(r[4] for r in rows[:6] if r[4] and "As of" in str(r[4])))
    crow, srow = [], []
    meas = {"SENT": "mail_requested", "RECEIVED": "mail_returned", "ACCEPTED": "mail_accepted"}
    hdr = None
    for r in rows:
        if r[0] == "CATEGORY":
            if hdr is not None:
                break                                    # second block (Civilian Overseas ...) starts
            hdr = [str(x).replace("_", " ").upper() if x else "" for x in r]
            continue
        if hdr is None or not r[1] or str(r[0]).upper() != "ALL" and r[1] != "Total":
            continue
        cells = {}
        for i, h in enumerate(hdr):
            m = re.fullmatch(r"(DEM|REP|OTH|TOTAL) (SENT|RECEIVED|ACCEPTED)", h)
            if m and r[i] not in (None, ""):
                p = {"DEM": "D", "REP": "R", "OTH": "other", "TOTAL": "ALL"}[m.group(1)]
                cells[(meas[m.group(2)], p)] = int(r[i])
        if r[1] == "Total":
            srow += db.long_rows("MD", as_of, cells)
        else:
            crow += db.long_rows("MD", as_of, cells, county=db.county_fips("MD", r[1]))
    if len({r["county_fips"] for r in crow}) != 24 or not srow:
        raise ValueError("MD: expected 24 counties + total")
    return crow, srow


def parse_md_requests(body: bytes) -> tuple[list[dict], list[dict]]:
    """Mail-in_Ballot_Request_Counts_with_Chart.xlsx: requests by county (TOTAL column) -> mail_requested ALL."""
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(body), data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    as_of = _md_as_of(next(x for r in rows[:6] for x in r if x and "As of" in str(x)))
    crow, srow = [], []
    for r in rows:
        if not r[0] or r[0] in ("COUNTY",) or not isinstance(r[11], (int, float)):
            continue
        if r[0] == "TOTAL":
            srow += db.long_rows("MD", as_of, {("mail_requested", "ALL"): int(r[11])})
        else:
            crow += db.long_rows("MD", as_of, {("mail_requested", "ALL"): int(r[11])}, county=db.county_fips("MD", r[0]))
    if len(crow) != 24 or not srow:
        raise ValueError("MD: expected 24 counties + total")
    return crow, srow


def _md_csv_validate(body: bytes) -> None:
    head = body[:600].decode("utf-8-sig", "ignore")
    if not (head.startswith("COUNTY_CODE,COUNTY_NAME") and "PARTY_CODE" in head and "Day1" in head):
        raise ValueError("unexpected MD early-voting header")


@source("md", "md_ev_inperson", MD_BASE + "EarlyVoting_RAW_data.csv", kind="county", format="csv",
        cadence="once early voting has ended (Oct 22-29, 2026); file may be republished",
        notes="Maryland SBE EarlyVoting RAW data: in-person early votes by county x party x day (precinct x demographic "
              "rows, aggregate). Rows = cumulative through each early-voting day (as_of = that day, day 1 = 2026-10-22). "
              "Party: DEM->D, REP->R, UNA->U, all other codes->other. State rows are SUMS OF COUNTIES (no state figure in file).")
def collect_md_ev_inperson(st):
    r = st.fetch("md_ev_inperson", MD_BASE + "EarlyVoting_RAW_data.csv", ext="csv", validate=_md_csv_validate)
    if r.needs_parse:
        crow, srow = parse_md_early_raw(r.body)
        st.add_rows(crow, source_id="md_ev_inperson", snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
        st.add_rows(srow, source_id="md_ev_inperson", snapshot_id=r.snapshot_id, level="state")


@source("md", "md_mail_county", MD_BASE + "Absentees_Sent_and_Returned_by_County.xlsx", kind="county", format="xlsx",
        cadence="daily (timestamped 'As of ... 6 PM')",
        notes="Maryland SBE Mail-in ballots sent / received / accepted by county x party (DEM/REP/OTH), all categories "
              "(domestic+military+overseas), incl. print-at-home and replacement ballots (originally misprinted ballots "
              "excluded). mail_requested = ballots SENT (fulfilled requests; party rows) and, from the separate "
              "Mail-in_Ballot_Request_Counts workbook, REQUESTS with party ALL (slightly higher: includes requests not yet "
              "sent); mail_returned = RECEIVED; mail_accepted = ACCEPTED (county canvass lags; 0 = not yet canvassed). "
              "Statewide figures are the source's Total row.")
def collect_md_mail_county(st):
    for sid, fn, parser in (("md_mail_county", "Absentees_Sent_and_Returned_by_County.xlsx", parse_md_sent_returned),
                            ("md_mail_requests", "Mail-in_Ballot_Request_Counts_with_Chart.xlsx", parse_md_requests)):
        r = st.fetch(sid, MD_BASE + fn, ext="xlsx", validate=lambda b: parser(b))
        if r.needs_parse:
            crow, srow = parser(r.body)
            st.add_rows(crow, source_id=sid, snapshot_id=r.snapshot_id, level="county", mark_parsed=False)
            st.add_rows(srow, source_id=sid, snapshot_id=r.snapshot_id, level="state")


# ================================================================================================ Maine
ME_PAGE = "https://www.maine.gov/sos/elections-voting/voter-data"
ME_HEADER = "RES MUNICIPALITY|DES|Voter Record #|P|"


def me_file_url(html: str) -> str | None:
    m = re.search(r'href="(/sos/sites/maine\.gov\.sos/files/inline-files/[^"]*AB%20Voter%20File[^"]*\.txt)"', html, re.I)
    return "https://www.maine.gov" + m.group(1) if m else None


def me_file_as_of(url: str) -> str:
    m = re.search(r"as%20of%20(\d{1,2})-(\d{1,2})-(\d{2})\.txt", url, re.I)
    if not m:
        raise ValueError("ME: no as-of date in file name")
    return date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()


def parse_me_absentee(body: bytes, as_of: str) -> list[dict]:
    """Voter-level absentee file ('|' delimited) -> STATE x party aggregate counts. The file has municipality but no county.
    Columns used: P (enrolled party), Req Type, Rec Type, Status.  Req/Rec type 'VP' = ballot requested/received in person
    at the clerk's office (Maine counts in-person early voting as absentee voting).
      mail_requested = records whose Req Type is not VP        mail_returned = Rec Type present and not VP
      mail_accepted  = mail_returned with Status AC*           inperson_early = Rec Type VP, Status not REJ
      total          = all records with a Rec Type (ballots received, in person + by mail, incl. rejected)"""
    df = pd.read_csv(io.BytesIO(body), sep="|", dtype=str, keep_default_na=False, encoding="latin-1",
                     usecols=["P", "Req Type", "Rec Type", "Status"])
    p = df["P"].str.strip().str.upper().map({"D": "D", "R": "R", "U": "U"}).fillna("other")
    rec = df["Rec Type"].str.strip()
    masks = {
        "mail_requested": df["Req Type"].str.strip() != "VP",
        "mail_returned": (rec != "") & (rec != "VP"),
        "mail_accepted": (rec != "") & (rec != "VP") & df["Status"].str.upper().str.startswith("AC"),
        "inperson_early": (rec == "VP") & (df["Status"].str.strip().str.upper() != "REJ"),
        "total": rec != "",
    }
    cells = {}
    for meas, mk in masks.items():
        by = p[mk].value_counts()
        for party in ("D", "R", "U", "other"):
            cells[(meas, party)] = int(by.get(party, 0))
        cells[(meas, "ALL")] = int(mk.sum())
    return db.long_rows("ME", as_of, cells)


def _me_validate(body: bytes) -> None:
    if not body.startswith(ME_HEADER.encode()):
        raise ValueError("unexpected ME voter-file header")


@source("me", "me_absentee_file", ME_PAGE, kind="state", format="txt", cadence="periodic (several times a week near the election)",
        notes="Maine SoS Statewide Absentee Voter Data File (voter-level, open download; raw snapshot stays on the SSD, never "
              "published). Reduced to STATE x party counts (file has municipality, no county). Party = enrolled party "
              "(D/R/U; Green/Libertarian/blank -> other). Semantics (codes inferred; layout page returned 403 to us): "
              "mail_requested = absentee requests with Req Type != VP; mail_returned = ballots received by mail/drop box/etc "
              "(Rec Type not VP, incl. rejected); mail_accepted = those with Status AC*; inperson_early = received in person "
              "at clerk (Rec Type VP) not rejected; total = every ballot received. as_of = date in the file name.")
def collect_me_absentee_file(st):
    page = st.fetch("me_absentee_page", ME_PAGE, ext="html", conditional=False)
    if not page.ok:
        return
    url = me_file_url(page.body.decode("utf-8", "ignore"))
    if not url:
        st.log("me_absentee_file", "failed", message="no voter-file link on page", url=ME_PAGE)
        return
    as_of = me_file_as_of(url)
    r = st.fetch("me_absentee_file", url, ext="txt", part=as_of, validate=_me_validate)
    if r.needs_parse:
        st.add_rows(parse_me_absentee(r.body, as_of), source_id="me_absentee_file", snapshot_id=r.snapshot_id, level="state")


# ================================================================================================ not collected (verified)
_MN = "https://sos.mn.gov/election-administration-campaigns/data-maps/absentee-data"
not_collected("la", "la_ev_parish", "https://electionstatistics.sos.la.gov/default.aspx?Stats=Early_Voting_Statistics&Type=Statewide",
              access="blocked: robots.txt 'User-agent: * Disallow: /' on electionstatistics.sos.la.gov",
              notes="Parish-level early-voting report PDFs (in-person early / absentee / party) exist but the host forbids "
                    "automated access, so they are not collected.", format="pdf")
not_collected("mn", "mn_absentee_county", _MN, access="blocked: Radware bot-manager challenge (302 -> validate.perfdrive.com) "
              "for our research UA and for a plain request, 2026-10-31",
              notes="SoS publishes county absentee tables (applications, accepted by mail / in person) near-daily and a "
                    "2014-2026 XLSX; not retrievable without passing the bot challenge, so not collected.", format="html")
not_collected("ia", "ia_absentee_county", "https://sos.iowa.gov/elections/pdf/2026/general/AbsenteeCounty2026.pdf",
              access="blocked: HTTP 403 (bot wall) on the county PDF, the congressional PDF and the stats page, 2026-10-31",
              notes="Daily county + congressional absentee PDFs exist but are 403 to our honest UA.", format="pdf")
not_collected("mi", "mi_voter_participation", "https://www.michigan.gov/sos/elections/election-results-and-data/voter-participation-dashboard",
              access="dashboard-only", format="dashboard",
              notes="Power BI voter-participation dashboard (requested/returned/cast, daily); page offers no export/download.")
not_collected("ks", "ks_advance_voting", "https://www.sos.ks.gov/elections/advance-voting-data.html",
              access="dashboard-only", format="dashboard",
              notes="Advance voting data are two Power BI embeds (app.powerbigov.us) on the page; no CSV/XLSX export linked.")
not_collected("ky", "ky_absentee", "https://elect.ky.gov/Resources/Pages/Turnout.aspx", access="none published",
              notes="Only post-election turnout PDFs (county/precinct/age-gender-party; 2026 primary latest) and a per-voter "
                    "absentee tracker; no pre-election aggregate absentee/early statistics.", format="pdf")
not_collected("il", "il_vbm", "https://www.elections.il.gov/electionoperations/votingbymail.aspx", access="none published",
              notes="State board page has dates/rules only; downloads.elections.il.gov holds campaign-finance files; "
                    "mail-ballot counts live with county clerks.")
not_collected("in", "in_early_voting", "https://www.in.gov/sos/elections/voter-information/ways-to-vote/absentee-voting",
              access="none published",
              notes="Absentee/early pages carry rules; the statistics page has registration and post-election turnout only.")
not_collected("ma", "ma_early_mail", "https://www.sec.state.ma.us/ele/eleev/early-voting-by-mail.htm",
              access="blocked: Incapsula (HTTP 307/x-iinfo challenge) on sec.state.ma.us; no statewide dashboard found",
              notes="Only a per-voter ballot tracker is public; no aggregate early/mail statistics located.")
not_collected("mo", "mo_absentee", "https://www.sos.mo.gov/elections/absentee", access="none published",
              notes="SoS site has election results/statistics PDFs (post-election) only; no absentee return stats.")
