"""Early-vote collectors, batch E: county election offices that publish what the (blocked or silent) state does not.

Collected (county level only, no state rows: each file covers one county / city, never the whole state):
  NY  New York City Board of Elections: early-voting check-ins per borough per day (the five boroughs are the five counties).
  CA  Alameda County Registrar of Voters: vote-by-mail issued/returned by party (the statewide file has no party).
  NV  Clark County Election Department: early-voting turnout per day (Las Vegas area, ~70% of Nevada's voters).
  TN  Davidson County Election Commission (Nashville): early-voting turnout per day.
Secondary (own source_id, kind 'secondary', never merged into official rows): UF Election Lab tracker CSV, all states.
Recorded as not collected (each URL requested once with the project UA, nothing bypassed): the other county offices in
the `not_collected` calls at the bottom.

Parsers are pure functions of bytes; the collectors only fetch and hand the bytes to the parsers.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime

import us_ev_db as db

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)


def _text(html: bytes | str) -> str:
    """HTML -> single-line text (scripts/styles dropped, entities decoded)."""
    s = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", _BLOCK.sub(" ", s)))).strip()


def _n(x: str) -> int:
    return int(x.replace(",", ""))


# ================================================================================================ New York City
NYC_URL = "https://www.vote.nyc/page/early-voting-check-ins"
NYC_BOROUGHS = (("Manhattan", "36061"), ("Bronx", "36005"), ("Brooklyn", "36047"), ("Queens", "36081"),
                ("Staten Island", "36085"))               # New York, Bronx, Kings, Queens, Richmond counties
_NYC_DAY = re.compile(
    r"([A-Z][a-z]+ \d{1,2}, \d{4}) - Day \d+\s+Manhattan - ([\d,]+)\s+Bronx - ([\d,]+)\s+Brooklyn - ([\d,]+)\s+"
    r"Queens - ([\d,]+)\s+Staten Island - ([\d,]+)\s+[^\d]*?([\d,]+)")


def parse_nyc_checkins(body: bytes) -> list[dict]:
    """vote.nyc 'Early Voting Check-Ins' -> county rows: cumulative in-person early-voting check-ins per borough at the close of
    each early-voting day. A day whose five boroughs do not add up to the printed citywide figure is an error."""
    rows = []
    for m in _NYC_DAY.finditer(_text(body)):
        as_of = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        vals = [_n(m.group(i)) for i in range(2, 7)]
        if sum(vals) != _n(m.group(7)):
            raise ValueError(f"NYC {as_of}: boroughs {sum(vals)} != citywide {_n(m.group(7))}")
        for (_, fips), v in zip(NYC_BOROUGHS, vals):
            rows.append({"state": "NY", "county_fips": fips, "as_of_date": as_of, "measure": "inperson_early", "party": "ALL",
                         "count": v})
    if not rows:
        raise ValueError("NYC: no early-voting day blocks found")
    return rows


@db.source("NY", "ny_nyc_checkins", NYC_URL, kind="county", format="html", cadence="daily (close of polls)",
           notes="NYC Board of Elections 'Early Voting Check-Ins': cumulative unofficial check-ins per borough (= county: "
                 "Manhattan 36061, Bronx 36005, Brooklyn/Kings 36047, Queens 36081, Staten Island/Richmond 36085) as of close "
                 "of polls on each early-voting day -> inperson_early, party ALL. NYC only (not the other 57 NY counties); "
                 "no state rows. The state board (publicreporting.elections.ny.gov) is blocked, see ny_ev_public_reporting.")
def collect_ny_nyc(st):
    r = st.fetch("ny_nyc_checkins", NYC_URL, ext="html", conditional=False, validate=parse_nyc_checkins)
    if r.needs_parse:
        st.add_rows(parse_nyc_checkins(r.body), source_id="ny_nyc_checkins", snapshot_id=r.snapshot_id, level="county")


# ================================================================================================ Clark County NV
CLARK_URL = "https://elections.clarkcountynv.gov/ElectionResultsTV/evturnout.aspx"
CLARK_FIPS = "32003"
_ROW = re.compile(r"<tr[^>]*>((?:(?!<tr[\s>]).)*?)</tr>", re.S | re.I)    # innermost rows (tables are nested)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def parse_clark_daily(body: bytes) -> list[dict]:
    """Clark County 'Early Voting Turnout Summary': the 'Daily Turnout - Week N' tables (one column per day, row 'Daily Totals'
    = all early-voting sites) -> cumulative in-person early voters per day; the last 'Accumulated Total' must equal the sum."""
    html = body.decode("utf-8", "replace")
    year = re.search(r"(\d{4}) GENERAL ELECTION", _text(html))
    if not year:
        raise ValueError("Clark: election year missing")
    days: dict[str, int] = {}
    dates: list[str] = []
    for tr in _ROW.findall(html):
        cells = [_html.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(tr)]
        if cells and cells[0] == "Early Voting Site":
            dates = [c for c in cells[1:] if re.fullmatch(r"\d{1,2}/\d{1,2}", c)]
        elif cells and cells[0] == "Daily Totals" and dates:
            vals = [_n(c) for c in cells[1:1 + len(dates)]]
            for d, v in zip(dates, vals):
                days[datetime.strptime(f"{d}/{year.group(1)}", "%m/%d/%Y").strftime("%Y-%m-%d")] = v
            dates = []
    acc = re.search(r"Accumulated Total ([\d,]+)", _text(html))
    if not days or not acc:
        raise ValueError("Clark: daily tables or accumulated total missing")
    if sum(days.values()) != _n(acc.group(1)):
        raise ValueError(f"Clark: daily sum {sum(days.values())} != accumulated {acc.group(1)}")
    rows, cum = [], 0
    for d in sorted(days):
        if days[d] == 0:                                   # day not yet reported
            continue
        cum += days[d]
        rows.append({"state": "NV", "county_fips": CLARK_FIPS, "as_of_date": d, "measure": "inperson_early", "party": "ALL",
                     "count": cum})
    return rows


@db.source("NV", "nv_clark_ev", CLARK_URL, kind="county", format="html", cadence="nightly after 11 pm during early voting",
           notes="Clark County (32003) Election Department 'Early Voting Turnout Summary': in-person early voters at all early-voting "
                 "sites per day (unaudited), accumulated to a running total per day -> inperson_early, party ALL. In-person only "
                 "(mail ballots are only in a voter-level file, not collected). No state rows; the state dashboard (nvsos.gov/vivid) "
                 "is blocked, see nv_vivid.")
def collect_nv_clark(st):
    r = st.fetch("nv_clark_ev", CLARK_URL, ext="html", conditional=False, validate=parse_clark_daily)
    if r.needs_parse:
        st.add_rows(parse_clark_daily(r.body), source_id="nv_clark_ev", snapshot_id=r.snapshot_id, level="county")


# ================================================================================================ Davidson County TN
DAVIDSON_URL = "https://www.nashville.gov/departments/elections/voters/find-early-voting-information/early-voting-statistics"
DAVIDSON_FIPS = "47037"
_DAV = re.compile(r"Early Voting Totals by Date Totals((?: \d{2}/\d{2}/\d{2})+) Total Daily Total ([\d, ]+?) Cumulative Total "
                  r"([\d, ]+?) Percentage")


def parse_davidson(body: bytes) -> list[dict]:
    """nashville.gov 'Early Voting Statistics': the 'Early Voting Totals by Date' block (Daily Total / Cumulative Total rows,
    one column per day plus a Total column) -> cumulative in-person early voting per day for Davidson County."""
    m = _DAV.search(_text(body))
    if not m:
        raise ValueError("Davidson: totals-by-date block not found")
    dates = m.group(1).split()
    daily = [_n(x) for x in m.group(2).split()]
    cum = [_n(x) for x in m.group(3).split()]
    if len(daily) != len(dates) + 1 or len(cum) != len(dates) + 1 or cum[-2] != cum[-1] or sum(daily[:-1]) != daily[-1] \
            or sum(daily[:-1]) != cum[-1]:
        raise ValueError("Davidson: daily/cumulative rows are inconsistent")
    return [{"state": "TN", "county_fips": DAVIDSON_FIPS, "as_of_date": datetime.strptime(d, "%m/%d/%y").strftime("%Y-%m-%d"),
             "measure": "inperson_early", "party": "ALL", "count": c} for d, c in zip(dates, cum)]


@db.source("TN", "tn_davidson_ev", DAVIDSON_URL, kind="county", format="html", cadence="daily (morning, previous day's data)",
           notes="Davidson County (47037) Election Commission 'Early Voting Statistics': cumulative in-person early voting per day "
                 "(all locations) -> inperson_early, party ALL. In-person only; absentee-by-mail counts are not on the page. No "
                 "state rows; the state PDF (sos.tn.gov) is blocked, see tn_early_voting_stats.")
def collect_tn_davidson(st):
    r = st.fetch("tn_davidson_ev", DAVIDSON_URL, ext="html", conditional=False, validate=parse_davidson)
    if r.needs_parse:
        st.add_rows(parse_davidson(r.body), source_id="tn_davidson_ev", snapshot_id=r.snapshot_id, level="county")


# ================================================================================================ UF Election Lab (secondary)
UF_URL = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"
UF_MEASURE = {"request": "mail_requested", "accept": "mail_accepted", "inperson": "inperson_early", "voted": "total"}
UF_PARTY = {"all": "ALL", "dem": "D", "rep": "R", "none": "U"}
UF_SOURCE_ID = "uf_election_lab"


def parse_uf_election_lab(body: bytes, min_states: int = 40) -> list[dict]:
    """UF Election Lab / US Elections Project 2026 early-vote tracker (one CSV, one row per state) -> state rows.
    request_* = mail ballots requested (issued in AZ), accept_* = mail ballots accepted, inperson_*, voted_* = all methods.
    A 0 means 'not reported' for that state and is not stored. Party columns: dem/rep/none (= no party registration -> U);
    the figures are stored exactly as published (no derived values)."""
    import io

    import pandas as pd
    d = pd.read_csv(io.BytesIO(body))
    need = {"state_abbv", "last_update", "request_all", "accept_all", "inperson_all", "voted_all", "voted_dem", "voted_rep", "voted_none"}
    if not need <= set(d.columns) or len(d) < min_states:
        raise ValueError("unexpected UF Election Lab CSV layout")
    rows = []
    for _, r in d.iterrows():
        as_of = datetime.strptime(str(r["last_update"]).strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
        for pre, measure in UF_MEASURE.items():
            for suf, party in UF_PARTY.items():
                v = r.get(f"{pre}_{suf}")
                if pd.notna(v) and int(v) > 0:
                    rows.append({"state": str(r["state_abbv"]), "as_of_date": as_of, "measure": measure, "party": party,
                                 "count": int(v)})
    return rows


@db.source("us", UF_SOURCE_ID, UF_URL, kind="secondary", format="csv", cadence="daily (update stamp per state)",
           licence="CC BY-NC-ND 4.0: Michael McDonald, UF Election Lab / US Elections Project (election.lab.ufl.edu/early-vote); "
                   "attribution required, non-commercial use only, no adaptation. Source states' own data may not be public domain.",
           notes="SECONDARY compilation of the state figures (all states, one CSV), kept under its own source_id and never merged into "
                 "official rows: consumers must pick official rows first and use this only for states with none. Measures: "
                 "request->mail_requested (AZ: ballots issued), accept->mail_accepted, inperson->inperson_early, voted->total; party "
                 "dem/rep/none -> D/R/U; 0 = not reported = not stored. The Lab fills some states by estimate or from AP/county contacts "
                 "(see its per-state notes, e.g. MI in-person is estimated), so these are NOT official counts. robots.txt allows the path. "
                 "as_of = the state's own 'last_update'. Show with 'Source: UF Election Lab (CC BY-NC-ND 4.0)'.")
def collect_uf_election_lab(st):
    r = st.fetch(UF_SOURCE_ID, UF_URL, ext="csv", validate=parse_uf_election_lab)
    if r.needs_parse:
        st.add_rows(parse_uf_election_lab(r.body), source_id=UF_SOURCE_ID, snapshot_id=r.snapshot_id, level="state")


# ================================================================================================ county offices not collected
# Each URL was requested once on 2026-10-31 with the project UA (robots.txt read first); nothing was retried or bypassed.
_CF = "Cloudflare challenge page"
db.not_collected("AZ", "az_maricopa_turnout", "https://elections.maricopa.gov/results-and-data/election-results.html",
                 access="blocked: results page loads (HTTP 200) but its 'Turnout & Data Files' tab is a script app; robots.txt, the "
                        "script bundle and sub-pages answer HTTP 403 (Cloudflare)",
                 notes="Maricopa County Elections 'Turnout & Data Files'; Pima County has only Elections Department memos, no statistics file.",
                 format="script app")
db.not_collected("CA", "ca_county_rov_daily", "https://www.lavote.gov/", access="none published as data",
                 notes="LA RR/CC states ballots-returned counts only in Board of Supervisors election-update PDFs (prose, irregular); "
                       "San Diego, Orange, San Bernardino, Sacramento sites list no daily ballot-return file; Santa Clara "
                       f"(vote.santaclaracounty.gov) and Riverside (voteinfo.net) answer HTTP 403 ({_CF}). Alameda is collected "
                       "(ca_alameda_vbm). California itself is already covered at county level by ca_ev_county (SoS statistics PDF).")
db.not_collected("GA", "ga_county_offices", "https://www.fultoncountyga.gov/", access="none published",
                 notes="Fulton, Cobb, DeKalb and Gwinnett election pages (HTTP 200) link no early-vote turnout file or dashboard; "
                       "elections.fultoncountyga.gov has a TLS hostname mismatch. Statewide see ga_ev_hub (blocked).")
db.not_collected("OH", "oh_county_boards", "https://vote.franklincountyohio.gov/maps-and-data/voter-and-absentee-data-files",
                 access="voter-level only / blocked",
                 notes="Franklin County publishes only per-voter absentee and in-person lists (~25-29 MB, voter-level, not collected: "
                       "voter files stay off the collector); Cuyahoga and Hamilton list no early-vote statistics; Summit "
                       "(summitcountyboe.gov) answers HTTP 403 and now redirects to boe.ohio.gov/summit. Statewide see oh_ev_dashboard (blocked).")
db.not_collected("MI", "mi_local_clerks", "https://detroitmi.gov/departments/city-clerk",
                 access=f"blocked: HTTP 403 ({_CF}) on detroitmi.gov incl. robots.txt",
                 notes="Detroit City Clerk. Statewide see mi_voter_participation (dashboard only).")
db.not_collected("NY", "ny_other_counties", "https://www.westchestergov.com/boe",
                 access="blocked: HTTP 403 on www.westchestergov.com incl. robots.txt; Erie (elections.erie.gov) lists no statistics",
                 notes="Nassau/Suffolk/Monroe not located as statistics pages. NYC is collected (ny_nyc_checkins).")
db.not_collected("TN", "tn_shelby_ec", "https://www.shelbycountytn.gov/3003/Election-Commission",
                 access=f"blocked: HTTP 403 ({_CF}) on www.shelbycountytn.gov incl. robots.txt",
                 notes="Shelby County Election Commission. Davidson County is collected (tn_davidson_ev).")
db.not_collected("NV", "nv_county_mail_files", "https://www.clarkcountynv.gov/government/departments/elections/reports_data_maps/mb-data",
                 access="voter-level only",
                 notes="Clark County 'Mail Ballot Data Files' (mbreq26GE.zip) list every voter who returned a mail ballot; voter-level, "
                       "not collected. Washoe County's turnout tracker links only to the blocked state dashboard (nv_vivid).")
db.not_collected("WI", "wi_city_clerks", "https://city.milwaukee.gov/election", access="none published as data",
                 notes="Milwaukee and Madison clerk pages (HTTP 200) list no absentee-return statistics file; statewide see "
                       "wi_absentee_report (blocked).")
db.not_collected("MN", "mn_county_offices", "https://vote.minneapolismn.gov/results-data/turnout/",
                 access="none published as data",
                 notes="Minneapolis turnout page covers finished elections only; Hennepin host did not resolve from this client. "
                       "Statewide see mn_absentee_county (blocked).")


# ================================================================================================ Alameda County CA
ALAMEDA_URL = "https://alamedacountyca.gov/rov_app/edata?page=ballot&h=1"
ALAMEDA_FIPS = "06001"
_ALA_PARTY = {"Democratic": "D", "Republican": "R", "No Party Preference": "U"}      # American Independent, Green, Libertarian, P&F -> other
_ALA = re.compile(r"(\w+ \d{1,2}, (\d{4})) - General Election - Entire County Ballots Issued Returned Vote By Mail "
                  r"[\d.]+% ([\d,]+) [\d.]+% ([\d,]+) Military and Overseas .*? Ballots By Party((?: [A-Za-z ]+? [\d.]+% [\d,]+ [\d.]+% [\d,]+)+)")
_ALA_PARTY_ROW = re.compile(r"([A-Za-z ]+?) [\d.]+% ([\d,]+) [\d.]+% ([\d,]+)")


def parse_alameda_vbm(body: bytes, as_of: str) -> list[dict]:
    """Alameda County ROV eData 'Vote By Mail' tab (current general election, entire county): vote-by-mail ballots issued / returned,
    in total and by party. The page carries no timestamp, so `as_of` is the fetch date. Party rows must add up to the totals."""
    m = _ALA.search(_text(body))
    if not m:
        raise ValueError("Alameda: vote-by-mail block not found")
    if m.group(2) != as_of[:4]:
        raise ValueError(f"Alameda: page shows the {m.group(2)} election")
    issued, returned = _n(m.group(3)), _n(m.group(4))
    cells = {("mail_requested", "ALL"): issued, ("mail_returned", "ALL"): returned}
    tot_i = tot_r = 0
    for name, i, r in _ALA_PARTY_ROW.findall(m.group(5)):
        p = _ALA_PARTY.get(name.strip(), "other")
        for meas, v in (("mail_requested", _n(i)), ("mail_returned", _n(r))):
            cells[(meas, p)] = cells.get((meas, p), 0) + v
        tot_i, tot_r = tot_i + _n(i), tot_r + _n(r)
    if (tot_i, tot_r) != (issued, returned):
        raise ValueError(f"Alameda: party rows ({tot_i}, {tot_r}) != totals ({issued}, {returned})")
    return db.long_rows("CA", as_of, cells, county=ALAMEDA_FIPS)


@db.source("CA", "ca_alameda_vbm", ALAMEDA_URL, kind="county", format="html", cadence="continuous (page has no timestamp)",
           notes="Alameda County (06001) Registrar of Voters eData 'Vote By Mail': ballots issued (mail_requested) and returned "
                 "(mail_returned), total and by party (Democratic D, Republican R, No Party Preference U, American Independent / "
                 "Green / Libertarian / Peace and Freedom -> other). Gives the party split the statewide ca_ev_county lacks, "
                 "for one county. The page states no as-of time, so as_of = fetch date (Pacific). No state rows.")
def collect_ca_alameda(st):
    from zoneinfo import ZoneInfo
    as_of = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    r = st.fetch("ca_alameda_vbm", ALAMEDA_URL, ext="html", conditional=False, validate=lambda b: parse_alameda_vbm(b, as_of))
    if r.needs_parse:
        st.add_rows(parse_alameda_vbm(r.body, as_of), source_id="ca_alameda_vbm", snapshot_id=r.snapshot_id, level="county")
