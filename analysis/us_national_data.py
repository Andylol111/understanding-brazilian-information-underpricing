"""US national election-night backtest: historical tables for replaying the 2018, 2020, 2022 and 2024 nights.

  BRUNDER_DATA=<dir> uv run python analysis/us_national_data.py build [years...]      # default: 2018 2020 2022 2024
  BRUNDER_DATA=<dir> uv run python analysis/us_national_data.py verify [years...]     # printout of the checks

`load_cycle(year)` returns {name: DataFrame} read from $BRUNDER_DATA/processed/us_national_backtest/<year>/<name>.csv
(built on the first call, idempotent). Downloads are cached under $BRUNDER_DATA/raw/us/... .

Tables (two-party Democratic share = D/(D+R) everywhere; D-side = the Democratic nominee; the three exceptions below
are D-caucus or D-endorsed independents / the main independent of a D-less race and are named in `note`)
  races      one row per Senate / governor general election of the cycle (specials are `<st>-sen-s`): final_d2 and
             final_votes (D+R votes, first-round Nov D vs R: jungle states sum all candidates of a party), d_wins,
             poll prior (poll_avg / poll_sd / n_polls: our v3 rule as of the eve of the election), baseline_share
             (previous presidential two-party D share of the state), caucus_if_d_wins, d_caucus_win, inc_party, note.
  holdovers  D-caucus Senate seats not up that cycle (composition after the election minus seats the D caucus won in
             that cycle's races, first-round two-party result) and the resulting seat total.
  counties   county x race returns (d, r, total) with the county's previous presidential two-party share s0 and
             two-party votes V0; unit_id as in brunder.us (us-<st>-<5-digit FIPS>; AK: us-ak-hdNN, CT: planning regions).
  house      all 435 districts: final result, uncontested flag, prior_d2 / prior_src, inc_party.
  generic    final generic-ballot average (two-party D), n_polls, sd, and the actual House seats / national vote.
  poll_close state-level official poll-closing instants (UTC) per election.

Sources (all public domain / CC0 / CC BY unless noted):
  MIT Election Data and Science Lab (MEDSL): precinct returns 2018, 2020, 2022, 2024 (github.com/MEDSL, Dataverse
    doi:10.7910/DVN/ER9XTV, NT66Z3, YB60EJ), county presidential returns 2000-2024 (doi:10.7910/DVN/VOQCHQ), U.S. House
    1976-2024 (doi:10.7910/DVN/IG0UN2), U.S. Senate statewide 1976-2024 (doi:10.7910/DVN/PEJ5QU), 2016 precinct returns
    (github.com/MEDSL/official-precinct-returns): CC0 / MIT. Dataverse downloads answer the repository guestbook
    programmatically (POST /api/access/datafile/<id>, signed URL).
  FEC, Federal Elections 2018 / 2020 / 2022 (official House and Senate results, incumbent flags), 2024 congressional
    general-election candidates. FiveThirtyEight pollster-ratings raw_polls.csv 2018-2022 (CC BY 4.0) and, for 2024
    (not in raw_polls), FiveThirtyEight's own senate / governor / generic-ballot poll files as archived by the Internet
    Archive on 2024-11-18 (projects.fivethirtyeight.com/polls/data/*.csv; same poll database, different file layout).
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from brunder import us
from brunder.paths import PROCESSED, RAW

sys.path.insert(0, str(Path(__file__).resolve().parent))

RAW_US = RAW / "us"
OUT = PROCESSED / "us_national_backtest"
YEARS = (2018, 2020, 2022, 2024)
ELECTION = {2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
PREV_PRES = {2018: 2016, 2020: 2016, 2022: 2020, 2024: 2020}
TABLES = ("races", "holdovers", "counties", "house", "generic", "poll_close")
UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
DV = "https://dataverse.harvard.edu/api/access/datafile"
GUESTBOOK = {"guestbookResponse": {"name": "brunder research", "email": "research@example.org",
                                   "institution": "research", "position": "researcher", "answers": []}}


# ------------------------------------------------------------------ downloads (cached)

def _stream(c: httpx.Client, url: str, dest: Path) -> None:
    with c.stream("GET", url) as r:
        r.raise_for_status()
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as f:
            for ch in r.iter_bytes(1 << 20):
                f.write(ch)
    tmp.rename(dest)


def get(url: str, dest: Path) -> Path:
    """Download once (cached)."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(headers=UA, timeout=600, follow_redirects=True) as c:
            _stream(c, url, dest)
    return dest


def dataverse(file_id: int, dest: Path, original: bool = False) -> Path:
    """Dataverse file through the guestbook API (POST the guestbook response, GET the signed URL). `original`: the
    uploaded CSV of an ingested .tab file."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(headers=UA, timeout=600, follow_redirects=True) as c:
            r = c.post(f"{DV}/{file_id}" + ("?format=original" if original else ""), json=GUESTBOOK)
            r.raise_for_status()
            _stream(c, r.json()["data"]["signedUrl"], dest)
    return dest


def _surname(name: str) -> str:
    w = us._person(str(name))
    return w[-1] if w else ""


# ------------------------------------------------------------------ poll closing times

# Official state-level closing times (statutes / Secretary of State pages; AP "poll closing times" and NCSL tabulate
# the same). EST = UTC-5 on all four election days (US DST ended the weekend before). Local closing hour -> UTC:
#   ET +5, CT +6, MT +7, PT +8, AKST +9, HST +10. `close` = first closing part of the state, `last` = last part.
#   Split states: FL panhandle (CT), IN north-west and KY west (CT, close 1 h later in UTC), KS west (MT), MI Upper
#   Peninsula west (CT), ND south-west (MT), NE panhandle (MT), SD west river (MT, 7 pm local like CT: 1 h later),
#   TX El Paso and Hudspeth (MT), ID panhandle (PT), AK Aleutians (HST), NH towns closing up to 8 pm (RSA 659:4),
#   OR: Malheur County (MT) also takes ballots to 8 pm local but results are released after 8 pm PT.
_CLOSE_LOCAL = {   # state: (first close "h:mm" local, zone, last close local, zone)
    "al": ("19:00", "CT"), "ak": ("20:00", "AK", "20:00", "HI"), "az": ("19:00", "MT"), "ar": ("19:30", "CT"),
    "ca": ("20:00", "PT"), "co": ("19:00", "MT"), "ct": ("20:00", "ET"), "de": ("20:00", "ET"),
    "fl": ("19:00", "ET", "19:00", "CT"), "ga": ("19:00", "ET"), "hi": ("19:00", "HI"),
    "id": ("20:00", "MT", "20:00", "PT"), "il": ("19:00", "CT"), "in": ("18:00", "ET", "18:00", "CT"),
    "ia": ("21:00", "CT"), "ks": ("19:00", "CT", "19:00", "MT"), "ky": ("18:00", "ET", "18:00", "CT"),
    "la": ("20:00", "CT"), "me": ("20:00", "ET"), "md": ("20:00", "ET"), "ma": ("20:00", "ET"),
    "mi": ("20:00", "ET", "20:00", "CT"), "mn": ("20:00", "CT"), "ms": ("19:00", "CT"), "mo": ("19:00", "CT"),
    "mt": ("20:00", "MT"), "ne": ("20:00", "CT", "20:00", "MT"), "nv": ("19:00", "PT"),
    "nh": ("19:00", "ET", "20:00", "ET"), "nj": ("20:00", "ET"), "nm": ("19:00", "MT"), "ny": ("21:00", "ET"),
    "nc": ("19:30", "ET"), "nd": ("19:00", "CT", "19:00", "MT"), "oh": ("19:30", "ET"), "ok": ("19:00", "CT"),
    "or": ("20:00", "PT"), "pa": ("20:00", "ET"), "ri": ("20:00", "ET"), "sc": ("19:00", "ET"),
    "sd": ("19:00", "CT", "19:00", "MT"), "tn": ("19:00", "CT"), "tx": ("19:00", "CT", "19:00", "MT"),
    "ut": ("20:00", "MT"), "vt": ("19:00", "ET"), "va": ("19:00", "ET"), "wa": ("20:00", "PT"),
    "wv": ("19:30", "ET"), "wi": ("20:00", "CT"), "wy": ("19:00", "MT"),
}
_UTC_OFF = {"ET": 5, "CT": 6, "MT": 7, "PT": 8, "AK": 9, "HI": 10}


def _close_utc(election: str, hhmm: str, zone: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    t = pd.Timestamp(election) + pd.Timedelta(hours=h + _UTC_OFF[zone], minutes=m)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def poll_close(year: int) -> pd.DataFrame:
    """state, year, close_utc (first closing part), last_close_utc (last closing part of a split state)."""
    rows = []
    for st, v in _CLOSE_LOCAL.items():
        a = _close_utc(ELECTION[year], v[0], v[1])
        b = _close_utc(ELECTION[year], v[2], v[3]) if len(v) == 4 else a
        rows.append({"state": st, "year": year, "close_utc": min(a, b), "last_close_utc": max(a, b)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ states

STATE_NAME = {"Alabama": "al", "Alaska": "ak", "Arizona": "az", "Arkansas": "ar", "California": "ca",
              "Colorado": "co", "Connecticut": "ct", "Delaware": "de", "Florida": "fl", "Georgia": "ga",
              "Hawaii": "hi", "Idaho": "id", "Illinois": "il", "Indiana": "in", "Iowa": "ia", "Kansas": "ks",
              "Kentucky": "ky", "Louisiana": "la", "Maine": "me", "Maryland": "md", "Massachusetts": "ma",
              "Michigan": "mi", "Minnesota": "mn", "Mississippi": "ms", "Missouri": "mo", "Montana": "mt",
              "Nebraska": "ne", "Nevada": "nv", "New Hampshire": "nh", "New Jersey": "nj", "New Mexico": "nm",
              "New York": "ny", "North Carolina": "nc", "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok",
              "Oregon": "or", "Pennsylvania": "pa", "Rhode Island": "ri", "South Carolina": "sc",
              "South Dakota": "sd", "Tennessee": "tn", "Texas": "tx", "Utah": "ut", "Vermont": "vt",
              "Virginia": "va", "Washington": "wa", "West Virginia": "wv", "Wisconsin": "wi", "Wyoming": "wy"}
STATES = sorted(STATE_NAME.values())
STATE_FIPS = {"al": "01", "ak": "02", "az": "04", "ar": "05", "ca": "06", "co": "08", "ct": "09", "de": "10",
              "fl": "12", "ga": "13", "hi": "15", "id": "16", "il": "17", "in": "18", "ia": "19", "ks": "20",
              "ky": "21", "la": "22", "me": "23", "md": "24", "ma": "25", "mi": "26", "mn": "27", "ms": "28",
              "mo": "29", "mt": "30", "ne": "31", "nv": "32", "nh": "33", "nj": "34", "nm": "35", "ny": "36",
              "nc": "37", "nd": "38", "oh": "39", "ok": "40", "or": "41", "pa": "42", "ri": "44", "sc": "45",
              "sd": "46", "tn": "47", "tx": "48", "ut": "49", "vt": "50", "va": "51", "wa": "53", "wv": "54",
              "wi": "55", "wy": "56"}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}

# D-side exceptions: candidate (surname) who stands in for the Democratic side. (year, state, office, special)
#  - King (ME) and Sanders (VT) are independents who caucus with the Democrats and whom the party backed (the party's
#    own nominee, if any, polled 3-11 %); the D-caucus seat is theirs.
#  - Gross (AK 2020) was the Democratic-endorsed independent (the party had no nominee); polls list him as DEM.
#  - Osborn (NE 2024) ran against Fischer with no Democrat on the ballot; he did not commit to a caucus.
D_SIDE = {(2018, "me", "sen", False): ("KING", "I-D"), (2018, "vt", "sen", False): ("SANDERS", "I-D"),
          (2024, "me", "sen", False): ("KING", "I-D"), (2024, "vt", "sen", False): ("SANDERS", "I-D"),
          (2020, "ak", "sen", False): ("GROSS", "I-D"), (2024, "ne", "sen", False): ("OSBORN", "I")}


# ------------------------------------------------------------------ poll priors (our v3 rule)

POLLS = RAW_US / "polls"
RAW_538 = "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/raw_polls.csv"
WB = "http://web.archive.org/web/20241118073720id_/https://projects.fivethirtyeight.com/polls/data/{}_polls.csv"
_RACE_RE = re.compile(r"^(\d{4})_(Sen|Gov)-(G|GS)_([A-Z]{2})$")
_POP_RANK = {"lv": 0, "v": 0, "rv": 1, "a": 2}


def _538_old() -> pd.DataFrame:
    p = pd.read_csv(get(RAW_538, POLLS / "538_raw_polls.csv"), low_memory=False)
    m = p["race"].str.extract(_RACE_RE)
    keep = m[0].notna() & p["type_simple"].isin(["Sen-G", "Gov-G"])
    p, m = p[keep].copy(), m[keep]
    p["cycle"] = m[0].astype(int)
    p["office"] = m[1].str.lower()
    p["special"] = m[2] == "GS"
    p["state"] = m[3].str.lower()
    p = p.dropna(subset=["cand1_pct", "cand2_pct", "samplesize", "polldate"])
    p = p[(p["cand1_pct"] + p["cand2_pct"] > 0) & (p["samplesize"] > 0)].drop_duplicates(["poll_id", "race_id"])
    return p


def _poll_rows_old(year: int) -> pd.DataFrame:
    """One row per poll x race of the cycle: key (state, office, special), pollster, end, n, s = D side / (D side + R)."""
    p = _538_old()
    p = p[p["cycle"] == year]
    rows = []
    for _, x in p.iterrows():
        key = (year, x["state"], x["office"], bool(x["special"]))
        ov = D_SIDE.get(key)
        if ov:
            a = [(x["cand1_name"], x["cand1_pct"], x["cand1_party"]), (x["cand2_name"], x["cand2_pct"], x["cand2_party"])]
            d = [c for c in a if _surname(c[0]).upper() == ov[0]]
            r = [c for c in a if c[2] == "REP"]
            if not d or not r:
                continue
            dem, opp = d[0][1], r[0][1]
        else:
            if not (x["cand1_party"] == "DEM" and x["cand2_party"] == "REP"):
                continue
            dem, opp = x["cand1_pct"], x["cand2_pct"]
        rows.append({"state": x["state"], "office": x["office"], "special": bool(x["special"]),
                     "pollster": x["pollster"], "end": pd.Timestamp(x["polldate"]), "n": float(x["samplesize"]),
                     "dem": dem, "opp": opp, "s": dem / (dem + opp)})
    return pd.DataFrame(rows, columns=["state", "office", "special", "pollster", "end", "n", "dem", "opp", "s"])


def _poll_rows_2024() -> pd.DataFrame:
    """2024 Senate / governor polls (538's own file, archived 2024-11-18): one row per poll x race with the D side and
    the Republican named. One question per poll and race: likely voters over registered voters over adults, then the
    ballot with the most candidates (the rule of brunder.us.poll_rows). Hypothetical matchups are dropped."""
    rows = []
    for office, f in (("sen", "senate"), ("gov", "governor")):
        d = pd.read_csv(get(WB.format(f), POLLS / f"wb_{f}.csv"), low_memory=False)
        d = d[(d["cycle"] == 2024) & (d["stage"] == "general") & (~d["hypothetical"].astype(bool))
              & d["state"].isin(STATE_NAME) & d["sample_size"].notna() & d["pct"].notna()].copy()
        d["end"] = pd.to_datetime(d["end_date"], format="%m/%d/%y")
        d["st"] = d["state"].map(STATE_NAME)
        d["special"] = (d["office_type"].str.contains("Senate") & (d["seat_name"] != "Class I")) if office == "sen" \
            else False
        d["sur"] = d["candidate_name"].map(lambda s: _surname(s).upper())
        for (poll, race), g in d.groupby(["poll_id", "race_id"]):
            qs = []
            for _, q in g.groupby("question_id"):
                key = (2024, q["st"].iat[0], office, bool(q["special"].iat[0]))
                ov = D_SIDE.get(key)
                dd = q[q["sur"] == ov[0]] if ov else q[q["party"] == "DEM"]
                rr = q[q["party"] == "REP"]
                if dd.empty or rr.empty:
                    continue
                dem, opp = float(dd["pct"].iat[0]), float(rr["pct"].max())
                if dem + opp <= 0:
                    continue
                qs.append((_POP_RANK.get(str(q["population"].iat[0]), 3), -len(q), q["question_id"].iat[0], dem, opp, q))
            if not qs:
                continue
            _, _, _, dem, opp, q = sorted(qs, key=lambda t: t[:3])[0]
            rows.append({"state": q["st"].iat[0], "office": office, "special": bool(q["special"].iat[0]),
                         "pollster": q["pollster"].iat[0], "end": q["end"].iat[0], "n": float(q["sample_size"].iat[0]),
                         "dem": dem, "opp": opp, "s": dem / (dem + opp)})
    return pd.DataFrame(rows, columns=["state", "office", "special", "pollster", "end", "n", "dem", "opp", "s"])


def poll_rows(year: int) -> pd.DataFrame:
    return _poll_rows_2024() if year == 2024 else _poll_rows_old(year)


def poll_average(rows: pd.DataFrame, election: str) -> dict | None:
    """v3 rule of analysis/us_poll_error.py / brunder.us.poll_average as of the eve of the election: polls ending 1-46
    days before it (45-day window from the eve), weight sqrt(n) x 0.5^(age / 14), age = days before the eve, no
    pollster above 25 %. One row per poll. None without polls."""
    if rows is None or rows.empty:
        return None
    tte = (pd.Timestamp(election) - pd.to_datetime(rows["end"])).dt.days
    r = rows[(tte >= 1) & (tte - 1 <= us.POLL_WINDOW_DAYS)].copy()
    if r.empty:
        return None
    age = (tte[r.index] - 1).to_numpy(float)
    w = pd.Series(np.sqrt(r["n"].to_numpy(float)) * 0.5 ** (age / us.POLL_HALF_LIFE), index=r.index)
    w = us._cap(w, r["pollster"], us.POLL_CAP)
    return {"avg": float((w * r["s"]).sum()), "n_polls": int(len(r)), "n_pollsters": int(r["pollster"].nunique())}


def poll_priors(year: int) -> pd.DataFrame:
    """state, office, special, poll_avg, n_polls, poll_sd for every race with at least one poll."""
    rows = poll_rows(year)
    out = []
    for (st, office, sp), g in rows.groupby(["state", "office", "special"]):
        a = poll_average(g, ELECTION[year])
        if a:
            out.append({"state": st, "office": office, "special": sp, "poll_avg": a["avg"], "n_polls": a["n_polls"],
                        "poll_sd": us.poll_sd_by_n(a["n_polls"])})
    return pd.DataFrame(out, columns=["state", "office", "special", "poll_avg", "n_polls", "poll_sd"])


# ------------------------------------------------------------------ generic ballot

def generic_polls(year: int) -> pd.DataFrame:
    """Generic-ballot polls of the cycle: pollster, date (field end), n, s (two-party D). 2018-2022: raw_polls type
    'House-G-US'; 2024: the archived 538 generic_ballot file."""
    if year == 2024:
        p = pd.read_csv(get(WB.format("generic_ballot"), POLLS / "wb_generic_ballot.csv"), low_memory=False)
        p = p[p["cycle"] == 2024].copy()
        p["date"] = pd.to_datetime(p["end_date"], format="%m/%d/%y")
        p["s"] = p["dem"] / (p["dem"] + p["rep"])
        p["n"] = pd.to_numeric(p["sample_size"], errors="coerce")
        return p.dropna(subset=["s", "n"])[["pollster", "date", "n", "s"]]
    p = pd.read_csv(get(RAW_538, POLLS / "538_raw_polls.csv"), low_memory=False)
    p = p[(p["cycle"] == year) & (p["type_simple"] == "House-G-US")].copy()
    p["date"] = pd.to_datetime(p["polldate"])
    p["s"] = np.where(p["cand1_party"] == "DEM", p["cand1_pct"], p["cand2_pct"]) / (p["cand1_pct"] + p["cand2_pct"])
    p["n"] = pd.to_numeric(p["samplesize"], errors="coerce")
    return p.dropna(subset=["s", "n"])[["pollster", "date", "n", "s"]]


def generic_average(year: int, polls: pd.DataFrame | None = None) -> dict:
    """analysis/us_fundamentals.generic_average(year) as of the eve of the election: polls ending in the 45 days up to
    the eve, one row per pollster and date (largest sample), weights sqrt(n) x 0.5^(age / 14), pollster cap 25 %."""
    p = generic_polls(year) if polls is None else polls
    eve = pd.Timestamp(ELECTION[year]) - pd.Timedelta(days=1)
    p = p[(p["date"] <= eve) & (p["date"] >= eve - pd.Timedelta(days=us.POLL_WINDOW_DAYS))]
    p = p.sort_values("n").drop_duplicates(["pollster", "date"], keep="last")
    if p.empty:
        return {"avg": np.nan, "n_polls": 0}
    w0 = np.sqrt(p["n"]) * 0.5 ** ((eve - p["date"]).dt.days / us.POLL_HALF_LIFE)
    w = us._cap(w0, p["pollster"], us.POLL_CAP)
    return {"avg": float((w * p["s"]).sum()), "n_polls": int(len(p))}


# ------------------------------------------------------------------ precinct returns -> county returns

CACHE = OUT / "_cache"
_SRC_COLS = ["office", "stage", "mode", "votes", "county_fips", "jurisdiction_fips", "party_simplified", "party_detailed", "party", "county", "candidate",
             "precinct", "special", "state_po", "year"]
# Senate / governor races per cycle (state, special). Used to check coverage and to name the races.
SEN = {2018: "az ca ct de fl hi in me md ma mi mn mn* ms ms* mo mt ne nv nj nm ny nd oh pa ri tn tx ut vt va wa wv wi wy",
       2020: "al ak ar az* co de ga ga* id il ia ks ky la me ma mi mn ms mt ne nh nj nm nc ok or ri sc sd tn tx va wv wy",
       2022: "al ak az ar ca ca* co ct fl ga hi id il in ia ks ky la md mo nv nh ny nc nd oh ok ok* or pa sc sd ut vt wa wi",
       2024: "az ca ca* ct de fl hi in me md ma mi mn ms mo mt ne ne* nv nj nm ny nd oh pa ri tn tx ut vt va wa wv wi wy"}
GOV = {2018: "al ak az ar ca co ct fl ga hi id il ia ks me md ma mi mn ne nv nh nm ny oh ok or pa ri sc sd tn tx vt wi wy",
       2020: "de in mo mt nh nc nd ut vt wa wv",
       2022: "al ak az ar ca co ct fl ga hi id il ia ks me md ma mi mn ne nv nh nm ny oh ok or pa ri sc sd tn tx vt wi wy",
       2024: "de in mo mt nh nc nd ut vt wa wv"}


def race_list(year: int) -> list[tuple[str, str, bool]]:
    """(state, office, special) of the cycle: '*' marks the special election of a state's Senate pair."""
    out = [(s.rstrip("*"), "sen", s.endswith("*")) for s in SEN[year].split()]
    return out + [(s, "gov", False) for s in GOV[year].split()]


def race_key(state: str, office: str, special: bool) -> str:
    return f"{state}-{office}" + ("-s" if special else "")


def _src(year: int, st: str, office: str) -> tuple[str, Path]:
    if year == 2018:
        return "zip", RAW_US / "medsl18" / f"2018-{st}-precinct-general.zip"
    if year == 2022:
        return "zip", RAW_US / "medsl22" / f"2022-{st}-local-precinct-general.zip"
    if year == 2024:
        return "zip", RAW_US / "medsl24" / f"{st}24.zip"
    if office == "sen":
        return "csv", RAW_US / "medsl20" / "2020-SENATE-precinct-general.csv"
    name = "2020_in_precinct_general.csv" if st == "in" else f"2020-{st}-precinct-general.csv"
    return "csv", RAW_US / "medsl20" / name


def _chunks(year: int, st: str, office: str):
    kind, path = _src(year, st, office)
    if not path.exists():
        raise FileNotFoundError(path)
    f = zipfile.ZipFile(path) if kind == "zip" else None
    fh = f.open([n for n in f.namelist() if n.endswith(".csv") and "__MACOSX" not in n][0]) if f else path
    yield from pd.read_csv(fh, dtype=str, chunksize=500_000, usecols=lambda c: c in _SRC_COLS, encoding_errors="replace")


def _office_class(office: pd.Series) -> pd.Series:
    o = office.fillna("").str.upper().str.strip()
    cls = pd.Series("", index=o.index)
    cls[o.str.startswith("US SENATE") | o.str.startswith("U.S. SENATE")] = "sen"
    cls[o.str.startswith("GOVERNOR") & ~o.str.startswith("GOVERNOR'S")] = "gov"
    return cls


def state_rows(year: int, st: str, office: str) -> pd.DataFrame:
    """The state's general-election rows of the office (both specials), trimmed columns; cached."""
    cache = CACHE / f"rows_v3_{year}_{st}_{office}.csv.gz"
    if cache.exists():
        return pd.read_csv(cache, dtype=str)
    parts = []
    for ch in _chunks(year, st, office):
        if "state_po" in ch and year == 2020 and office == "sen":
            ch = ch[ch["state_po"].str.upper() == st.upper()]
        ch = ch[(_office_class(ch["office"]) == office) & (ch["stage"].fillna("GEN").str.upper() == "GEN")]
        parts.append(ch)
    d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=_SRC_COLS)
    if "special" not in d:
        d["special"] = "FALSE"
    if "party_simplified" not in d:
        d["party_simplified"] = d.get("party", "").astype(str).str.upper().map(
            lambda x: "DEMOCRAT" if x.startswith("DEM") else "REPUBLICAN" if x.startswith("REP") else "OTHER")
    if "party_detailed" not in d: d["party_detailed"] = d["party_simplified"]
    if "county_fips" not in d and "county" in d:
        units = us.counties(st)
        fips = dict(zip(units["key"], units["unit_id"].str[-5:]))
        d["county_fips"] = d["county"].map(us._norm).map(fips)
    d["special"] = d["special"].fillna("FALSE").str.upper()
    cache.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(cache, index=False)
    return d


def _cand(s: pd.Series) -> pd.Series:
    return s.fillna("").str.upper().str.replace(r"\s+", " ", regex=True).str.strip()


def party_by_candidate(d: pd.DataFrame) -> pd.Series:
    """candidate -> DEMOCRAT / REPUBLICAN / OTHER: the major party under which most of the candidate's votes were cast
    (fusion lines: all votes count for the candidate's major party)."""
    v = pd.to_numeric(d["votes"], errors="coerce").fillna(0)
    ps = d["party_simplified"].fillna("").str.upper()
    pdetailed = d.get("party_detailed", pd.Series("", index=d.index)).fillna("").str.upper()
    ps = np.where(pdetailed.str.startswith("DEM"), "DEMOCRAT", np.where(pdetailed.str.startswith("REP"), "REPUBLICAN", ps))
    x = pd.DataFrame({"c": _cand(d["candidate"]), "p": ps, "v": v})
    m = x[x["p"].isin(["DEMOCRAT", "REPUBLICAN"])].groupby(["c", "p"])["v"].sum().reset_index()
    best = m.sort_values("v").drop_duplicates("c", keep="last").set_index("c")["p"]
    return pd.Series({c: best.get(c, "OTHER") for c in x["c"].unique()})


def _aggregate_county(d: pd.DataFrame, year: int, st: str, office: str, special: bool, rule: str = "overlap") -> pd.DataFrame:
    d = d[d["special"].str.upper().eq("TRUE") == special].copy()
    if d.empty:
        return pd.DataFrame(columns=["unit_id", "d", "r", "total"])
    pmap = party_by_candidate(d)
    key = (year, st, office, special)
    ov = D_SIDE.get(key)
    cand = _cand(d["candidate"])
    if ov:
        side = np.where(cand.map(lambda x: _surname(x).upper()) == ov[0], "DEMOCRAT",
                        np.where(cand.map(pmap) == "REPUBLICAN", "REPUBLICAN", "OTHER"))
    else:
        side = cand.map(pmap).fillna("OTHER")
    d["party_simplified"] = side
    if st == "ak":
        # MEDSL Alaska uses jurisdiction_fips 02001..02040 for state House districts (the live-system unit).
        d["county_fips"] = d["jurisdiction_fips"].where(d["jurisdiction_fips"].notna(),
                                                         d["precinct"].str.extract(r"^(\d{2})-")[0].map(lambda x: f"02{x}"))
    r = us._medsl_county(d, st, rule)
    if st == "ak":
        r["unit_id"] = r["unit_id"].str[-2:].map(lambda x: f"us-ak-hd{x}")
        r = r[r["unit_id"].str.match(r"^us-ak-hd(?:0[1-9]|[1-3][0-9]|40)$")]
    r = r.rename(columns={"dem": "d", "rep": "r"})
    r["total"] = r["d"] + r["r"]
    return r[["unit_id", "d", "r", "total"]]


def _sen22_county(st: str, special: bool) -> pd.DataFrame:
    d = pd.read_csv(RAW_US / "medsl_hist" / "senate_county_2022.tab", dtype=str)
    d = d[(d["state_po"].str.lower() == st) & (d["special"].str.upper() == str(special).upper())].copy()
    if d.empty:
        return pd.DataFrame(columns=["unit_id", "d", "r", "total"])
    d = d.rename(columns={"candidatevotes": "votes"})
    d["precinct"] = ""
    return _aggregate_county(d, 2022, st, "sen", special)


def _ca22_special_county() -> pd.DataFrame:
    path = get("https://elections.cdn.sos.ca.gov/sov/2022-general/sov/csv-candidates.xlsx",
               RAW_US / "ca" / "2022_csv-candidates.xlsx")
    rows = us._xlsx_rows(path.read_bytes())
    head = [str(h).strip() if h else "" for h in rows[0]]
    d = pd.DataFrame([r + [None] * (len(head) - len(r)) for r in rows[1:]], columns=head)
    d = d[d["Contest Name"].eq("US Senate - 3 Partial/Unexpired Term")].copy()
    ids = dict(zip(us.counties("ca")["key"], us.counties("ca")["unit_id"]))
    d["unit_id"] = d["County Name"].map(lambda x: ids.get(us._norm(str(x))))
    d["party"] = d["Party Name"].map(us.party_code)
    d["votes"] = d["Vote Total"].map(us._int)
    g = d.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"d": g.get("DEM", 0), "r": g.get("REP", 0)}).assign(
        total=lambda x: x.d + x.r).rename_axis("unit_id").reset_index()


def contest_county(year: int, st: str, office: str, special: bool) -> pd.DataFrame:
    cache = CACHE / f"county_v7_{year}_{race_key(st, office, special)}.csv"
    if cache.exists():
        return pd.read_csv(cache)
    d = state_rows(year, st, office)
    if office == "sen" and not d.empty:
        ref = certified_senate(year)
        z = ref[(ref["state"] == st) & (ref["special"] == special)]
        target = float(z[["cert_d", "cert_r"]].sum(axis=1).iat[0]) if len(z) else np.nan
        candidates = [_aggregate_county(d, year, st, office, special, rule) for rule in ("overlap", "total_first", "all")]
        r = min(candidates, key=lambda q: abs(q["total"].sum() / target - 1) if target else float("inf"))
    else:
        r = _aggregate_county(d, year, st, office, special)
    if r.empty and year == 2022 and office == "sen":
        r = _sen22_county(st, special)
    if r.empty and (year, st, office, special) == (2022, "ca", "sen", True):
        r = _ca22_special_county()
    if office == "sen" and not r.empty:
        ref = certified_senate(year)
        z = ref[(ref["state"] == st) & (ref["special"] == special)]
        if len(z):
            # Some MEDSL state files mix county-total pseudo-precincts or omit county rows. Reconcile each major-party
            # column to the certified candidate total while preserving its within-state county distribution.
            for col, target_col in (("d", "cert_d"), ("r", "cert_r")):
                raw, target = r[col].sum(), float(z[target_col].iat[0])
                if raw > 0 and target > 0 and abs(raw / target - 1) > .005:
                    r[col] = r[col] * target / raw
            r["total"] = r["d"] + r["r"]
    cache.parent.mkdir(parents=True, exist_ok=True)
    r.to_csv(cache, index=False)
    return r


def presidential_counties(year: int) -> pd.DataFrame:
    """Previous presidential D/R county totals. Alaska and Connecticut are intentionally absent: Alaska reports by
    state-house district (lines changed after 2020), and CT's county file uses the former counties rather than the
    live system's nine planning regions. Their county baselines remain NaN rather than silently mismatching units."""
    p = pd.read_csv(RAW_US / "medsl" / "countypres_2000-2024.csv", dtype={"county_fips": str})
    p = p[(p["year"] == year) & p["party"].isin(["DEMOCRAT", "REPUBLICAN"])].copy()
    p["fips"] = p["county_fips"].str.replace(r"\.0$", "", regex=True).str.zfill(5).replace({"46113": "46102"})
    p["v"] = pd.to_numeric(p["candidatevotes"], errors="coerce").fillna(0)
    # This MEDSL release is county-level and its rows are TOTAL; guard against a future mixed-mode release.
    tot = p["mode"].fillna("TOTAL").str.upper() == "TOTAL"
    have = tot.groupby([p["year"], p["fips"]]).transform("any").astype(bool)
    p = p[(have & tot) | (~have & ~tot)]
    g = p.pivot_table(index="fips", columns="party", values="v", aggfunc="sum", fill_value=0)
    for c in ("DEMOCRAT", "REPUBLICAN"):
        if c not in g: g[c] = 0
    g = g.rename(columns={"DEMOCRAT": "d0", "REPUBLICAN": "r0"}).reset_index()
    g["state"] = g["fips"].str[:2].map(FIPS_STATE)
    g = g[~g["state"].isin(["ak", "ct"])]
    g["unit_id"] = "us-" + g["state"] + "-" + g["fips"]
    g["V0"] = g["d0"] + g["r0"]
    g["s0"] = g["d0"] / g["V0"]
    return g[["unit_id", "state", "s0", "V0", "d0", "r0"]]


def state_baselines(year: int) -> pd.Series:
    # Aggregate the raw presidential file directly so AK and CT retain statewide priors even though their live-system
    # substate units (AK House districts / CT planning regions) cannot be joined to MEDSL counties.
    py = PREV_PRES[year]
    p = pd.read_csv(RAW_US / "medsl" / "countypres_2000-2024.csv")
    p = p[(p["year"] == py) & p["party"].isin(["DEMOCRAT", "REPUBLICAN"])].copy()
    p["v"] = pd.to_numeric(p["candidatevotes"], errors="coerce").fillna(0)
    tot = p["mode"].fillna("TOTAL").str.upper().eq("TOTAL")
    have = tot.groupby([p["state_po"], p["county_fips"]]).transform("any").astype(bool)
    p = p[(have & tot) | (~have & ~tot)]
    x = p.pivot_table(index="state_po", columns="party", values="v", aggfunc="sum", fill_value=0)
    return (x["DEMOCRAT"] / (x["DEMOCRAT"] + x["REPUBLICAN"])).rename(index=str.lower)


def certified_senate(year: int) -> pd.DataFrame:
    s = pd.read_csv(RAW_US / "medsl_hist" / "1976-2024-senate-state.tab")
    s = s[(s["year"] == year) & s["stage"].str.upper().eq("GEN")].copy()
    s["office"], s["state"], s["special"] = "sen", s["state_po"].str.lower(), s["special"].astype(str).str.upper().eq("TRUE")
    s["v"] = pd.to_numeric(s["candidatevotes"], errors="coerce").fillna(0)
    s["candidate"] = _cand(s["candidate"])
    out = []
    for (st, sp), g in s.groupby(["state", "special"]):
        ov = D_SIDE.get((year, st, "sen", sp))
        ps = g["party_simplified"].fillna("").str.upper()
        pdetailed = g["party_detailed"].fillna("").str.upper()
        ps = pd.Series(np.where(pdetailed.str.startswith("DEM"), "DEMOCRAT", np.where(pdetailed.str.startswith("REP"), "REPUBLICAN", ps)), index=g.index)
        major = g.assign(_party=ps)[ps.isin(["DEMOCRAT", "REPUBLICAN"])].groupby(
            ["candidate", "_party"])["v"].sum().reset_index().rename(columns={"_party": "party_simplified"})
        pmap = major.sort_values("v").drop_duplicates("candidate", keep="last").set_index("candidate")["party_simplified"]
        side = g["candidate"].map(pmap).astype(object).fillna("OTHER")
        if ov:
            d = g.loc[g["candidate"].map(lambda x: _surname(x).upper()) == ov[0], "v"].sum()
        else:
            d = g.loc[side.eq("DEMOCRAT"), "v"].sum()
        r = g.loc[side.eq("REPUBLICAN"), "v"].sum()
        out.append({"state": st, "special": sp, "cert_d": d, "cert_r": r})
    return pd.DataFrame(out)


_INC24 = {("sen", s): p for p, ss in {"D": "ct hi ma mn mt nv nm ny oh pa ri va wa wi", "R": "fl ms mo nd tn tx wy",
                                      "I": "me vt"}.items() for s in ss.split()}
_INC24.update({("sen-s", "ne"): "R", ("gov", "mt"): "R", ("gov", "ut"): "R", ("gov", "vt"): "R"})


def incumbent_parties(year: int) -> dict[tuple[str, str, bool], str]:
    if year == 2024:
        return {(st, o.replace("-s", ""), o.endswith("-s")): p for (o, st), p in _INC24.items()}
    # Existing 538 rule: candidate won the same office in the state 2/4/6 (Senate) or 2/4 (governor) years earlier.
    p = _538_old()
    x = p.groupby("race").agg(cycle=("cycle", "first"), state=("state", "first"), office=("office", "first"),
                               special=("special", "first"), c1=("cand1_name", "first"), c2=("cand2_name", "first"),
                               a1=("cand1_actual", "first"), a2=("cand2_actual", "first"))
    x["winner"] = np.where(x["a1"] > x["a2"], x["c1"].map(_surname), x["c2"].map(_surname))
    won = {(o, st): [] for o, st in zip(x["office"], x["state"])}
    for _, z in x.iterrows(): won[(z["office"], z["state"])].append((z["cycle"], z["winner"]))
    out = {}
    for _, z in x[x["cycle"] == year].iterrows():
        back = (2, 4, 6) if z["office"] == "sen" else (2, 4)
        prev = {w for c, w in won[(z["office"], z["state"])] if year - c in back}
        out[(z["state"], z["office"], bool(z["special"]))] = "D" if _surname(z["c1"]) in prev else \
            "R" if _surname(z["c2"]) in prev else ""
    return out


def build_races_counties(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    pri = poll_priors(year).set_index(["state", "office", "special"])
    base = state_baselines(year)
    p0 = presidential_counties(PREV_PRES[year]).set_index("unit_id")
    cert = certified_senate(year).set_index(["state", "special"])
    inc = incumbent_parties(year)
    rr, cc = [], []
    for st, office, sp in race_list(year):
        key = race_key(st, office, sp)
        c = contest_county(year, st, office, sp)
        d, r = c["d"].sum(), c["r"].sum()
        note = ""
        if office == "sen" and (st, sp) in cert.index:
            z = cert.loc[(st, sp)]
            cd, cr = float(z["cert_d"]), float(z["cert_r"])
        else:
            cd, cr = d, r
        if c.empty:
            note = "county returns unavailable"
        if (year, st, office, sp) in D_SIDE:
            sur, lab = D_SIDE[(year, st, office, sp)]
            note = f"D-side is {sur.title()} ({lab})"
        if r == 0 and d > 0:
            note = (note + "; " if note else "") + "D-vs-D / no Republican: final_d2=1"
        a = pri.loc[(st, office, sp)] if (st, office, sp) in pri.index else None
        caucus = D_SIDE.get((year, st, office, sp), ("", "D"))[1]
        dw = bool(cd > cr) if cd + cr else False
        rr.append({"year": year, "race_key": key, "office": office, "state": st, "special": sp,
                   "final_d2": cd / (cd + cr) if cd + cr else np.nan, "final_votes": cd + cr,
                   "d_wins": dw, "poll_avg": a["poll_avg"] if a is not None else np.nan,
                   "poll_sd": a["poll_sd"] if a is not None else np.nan,
                   "n_polls": int(a["n_polls"]) if a is not None else 0, "baseline_share": base.get(st, np.nan),
                   "caucus_if_d_wins": caucus, "d_caucus_win": bool(dw and caucus != "I"),
                   "inc_party": inc.get((st, office, sp), ""), "note": note,
                   "county_d": d, "county_r": r})
        if not c.empty:
            c = c.copy(); c["year"], c["race_key"], c["state"] = year, key, st
            c["s0"] = c["unit_id"].map(p0["s0"]); c["V0"] = c["unit_id"].map(p0["V0"])
            cc.append(c[["year", "race_key", "unit_id", "state", "d", "r", "total", "s0", "V0"]])
    return pd.DataFrame(rr), pd.concat(cc, ignore_index=True) if cc else pd.DataFrame()


FINAL_SENATE = {2018: 47, 2020: 48, 2022: 51, 2024: 47}

def holdovers(year: int, races: pd.DataFrame) -> pd.DataFrame:
    # CA's simultaneous full-term and unexpired-term ballots elect one seat; count the regular result once.
    s = races[(races["office"] == "sen") & races["d_caucus_win"] & ~((races["state"] == "ca") & races["special"])]
    return pd.DataFrame([{"year": year, "d_caucus_holdover": FINAL_SENATE[year] - len(s),
                          "d_seats_final_two_party": FINAL_SENATE[year]}])


# ------------------------------------------------------------------ House

def _did(st: str, cd) -> str:
    n = int(float(cd))
    return f"{st.upper()}-AL" if n == 0 else f"{st.upper()}-{n:02d}"


def _house_all() -> pd.DataFrame:
    h = pd.read_csv(RAW_US / "medsl_hist" / "1976-2024-house.tab", low_memory=False)
    h = h[(h["stage"].str.upper() == "GEN") & ~h["special"].astype(str).str.upper().eq("TRUE") & h["state_po"].str.lower().isin(STATES)].copy()
    h = h[~h["candidate"].fillna("").str.upper().str.match(r"^(EXHAUSTED|UNDERVOTE|OVERVOTE|BLANK|TOTAL |BALLOTS )")]
    h["id"] = [_did(s, d) for s, d in zip(h["state_po"], h["district"])]
    h["v"] = pd.to_numeric(h["candidatevotes"], errors="coerce").fillna(0)
    h["p"] = h["party"].fillna("").str.upper()
    return h


def house_results(year: int) -> pd.DataFrame:
    h = _house_all(); h = h[h["year"] == year]
    rows = []
    for did, g in h.groupby("id"):
        d = g.loc[g["p"].str.contains("DEMOCRAT"), "v"].sum()
        r = g.loc[g["p"].str.contains("REPUBLICAN"), "v"].sum()
        cand = g.groupby("candidate").agg(v=("v", "sum"), p=("p", " ".join)).reset_index()
        winner = cand.sort_values("v").iloc[-1]
        wp = "D" if "DEMOCRAT" in winner["p"] else "R" if "REPUBLICAN" in winner["p"] else "I"
        # A same-party top-two contest has no opposing major-party vote but is not "uncontested" in the ordinary sense;
        # the contract's definition is one major party absent, so it is flagged here and the note is implicit in d/r.
        rows.append({"year": year, "district": did, "state": did[:2].lower(),
                     "final_d2": d / (d + r) if d + r else np.nan, "final_votes": d + r,
                     "d_wins": wp == "D", "uncontested": bool(d == 0 or r == 0), "winner_party": wp,
                     "d": d, "r": r})
    return pd.DataFrame(rows)


def _pres_lean_2018() -> pd.Series:
    cache = CACHE / "house_pres16_on_2018_lines.csv"
    if cache.exists():
        return pd.read_csv(cache).set_index("district")["prior_d2"]
    import us_fundamentals
    x, _ = us_fundamentals.districts_2018()
    out = x["lean"].rename("prior_d2").rename_axis("district").reset_index()
    cache.parent.mkdir(parents=True, exist_ok=True); out.to_csv(cache, index=False)
    return out.set_index("district")["prior_d2"]


def _house_prev_share(year: int) -> pd.Series:
    x = house_results(year)
    return x.set_index("district")["final_d2"]


def _fec_inc(year: int) -> dict[str, str]:
    if year == 2024:
        site = json.loads((us.ROOT_DIR / "site/data/us2026/house/districts_2024.json").read_text())["districts"]
        return {k: (v.get("house24") or {}).get("incumbent_party", "") or "" for k, v in site.items()}
    # Match each current candidate to a same-state winner two years before. This remains valid through redistricting,
    # unlike district-number matching, and avoids making the FEC xlsx parser a runtime dependency.
    h = _house_all()
    prev = h[h["year"] == year - 2]
    winners = set()
    for _, g in prev.groupby("id"):
        cand = g.groupby("candidate").agg(v=("v", "sum"), p=("p", " ".join), state_po=("state_po", "first")).reset_index()
        z = cand.sort_values("v").iloc[-1]
        p = "D" if "DEMOCRAT" in z["p"] else "R" if "REPUBLICAN" in z["p"] else "I"
        winners.add((z["state_po"], _surname(z["candidate"]), p))
    out = {}
    for did, g in h[h["year"] == year].groupby("id"):
        for _, z in g.iterrows():
            hit = [(st, sur, p) for st, sur, p in winners if st == z["state_po"] and sur == _surname(z["candidate"])]
            if hit: out[did] = hit[0][2]; break
    return out


def build_house(year: int) -> pd.DataFrame:
    x = house_results(year)
    state = state_baselines(year)
    x["prior_d2"], x["prior_src"] = np.nan, ""
    if year in (2018, 2020):
        lean = _pres_lean_2018()
        x["prior_d2"] = x["district"].map(lean)
        x.loc[x["prior_d2"].notna(), "prior_src"] = "pres_lean"
        # The PA map was enacted after the 2016 precinct ballots. Other gaps in the precinct crosswalk use the
        # previous House result on unchanged lines; PA uses the state presidential mean in 2018 and House 2018 in 2020.
        prev = _house_prev_share(2016 if year == 2018 else 2018)
        m = x["prior_d2"].isna() & x["district"].map(prev).notna() & ((x["state"] != "pa") | (year == 2020))
        if m.any():
            x.loc[m, "prior_d2"] = x.loc[m, "district"].map(prev).astype(float)
            x.loc[m, "prior_src"] = "prev_house"
    else:
        site = json.loads((us.ROOT_DIR / "site/data/us2026/house/districts_2024.json").read_text())["districts"]
        lean = pd.Series({k: (v.get("pres20") or {}).get("d2") for k, v in site.items()}, dtype=float)
        if year == 2022:
            # These states' 2024 court / legislative lines differ from their 2022 lines; do not attach the wrong district.
            lean = lean[~lean.index.str[:2].isin(["AL", "GA", "LA", "NC", "NY"])]
        x["prior_d2"] = x["district"].map(lean)
        x.loc[x["prior_d2"].notna(), "prior_src"] = "pres_lean"
    m = x["prior_d2"].isna()
    if m.any():
        x.loc[m, "prior_d2"] = x.loc[m, "state"].map(state).astype(float)
        x.loc[m, "prior_src"] = "state_mean"
    inc = _fec_inc(year); x["inc_party"] = x["district"].map(inc).fillna("")
    return x[["year", "district", "state", "final_d2", "final_votes", "d_wins", "uncontested", "winner_party",
              "prior_d2", "prior_src", "inc_party"]]


HOUSE_D_SEATS = {2018: 235, 2020: 222, 2022: 213, 2024: 215}

def build_generic(year: int, house: pd.DataFrame) -> pd.DataFrame:
    a = generic_average(year)
    d_votes = float((house["final_d2"] * house["final_votes"]).sum())
    total = float(house["final_votes"].sum())
    return pd.DataFrame([{"year": year, "avg": a["avg"], "n_polls": a["n_polls"],
                          "sd": us.poll_sd_by_n(max(1, a["n_polls"])),
                          "house_d_seats_final": int((house["winner_party"] == "D").sum()),
                          "house_d2_national": d_votes / total if total else np.nan}])


# ------------------------------------------------------------------ build / load / checks

_DV20 = {"de": 6100418, "in": 13582892, "mo": 6100413, "mt": 6100404, "nh": 6100419, "nc": 6100444,
         "nd": 6100438, "ut": 6100430, "vt": 6100442, "wa": 6100434, "wv": 6100431}
_MEDSL18 = "https://raw.githubusercontent.com/MEDSL/2018-elections-official/master/individual_states"
_MEDSL22 = "https://raw.githubusercontent.com/MEDSL/2022-elections-official/main/individual_states"
_MEDSL24 = "https://raw.githubusercontent.com/MEDSL/2024-elections-official/main/individual_states"
_MEDSL16 = "https://raw.githubusercontent.com/MEDSL/official-precinct-returns/master"


def fetch_inputs(year: int) -> None:
    states = sorted({st for st, _, _ in race_list(year)})
    if year == 2018:
        for st in states: get(f"{_MEDSL18}/2018-{st}-precinct-general.zip", RAW_US/"medsl18"/f"2018-{st}-precinct-general.zip")
    elif year == 2022:
        for st in states: get(f"{_MEDSL22}/2022-{st}-local-precinct-general.zip", RAW_US/"medsl22"/f"2022-{st}-local-precinct-general.zip")
        dataverse(7412054, RAW_US/"medsl_hist"/"senate_county_2022.tab", original=True)
    elif year == 2024:
        for st in states: get(f"{_MEDSL24}/{st}24.zip", RAW_US/"medsl24"/f"{st}24.zip")
    else:
        dataverse(6100391, RAW_US/"medsl20"/"2020-SENATE-precinct-general.csv")
        for st in GOV[2020].split():
            name = "2020_in_precinct_general.csv" if st == "in" else f"2020-{st}-precinct-general.csv"
            dataverse(_DV20[st], RAW_US/"medsl20"/name, original=st != "nc")
    dataverse(13573089, RAW_US/"medsl"/"countypres_2000-2024.csv", original=True)
    dataverse(13887039, RAW_US/"medsl_hist"/"1976-2024-senate-state.tab", original=True)
    dataverse(13592823, RAW_US/"medsl_hist"/"1976-2024-house.tab", original=True)
    get(RAW_538, POLLS/"538_raw_polls.csv")
    if year == 2024:
        for f in ("senate", "governor", "generic_ballot"): get(WB.format(f), POLLS/f"wb_{f}.csv")
    if year in (2018, 2020):
        get(f"{_MEDSL16}/2016-precinct-president/2016-precinct-president.zip", RAW_US/"medsl16"/"president.zip")
        get(f"{_MEDSL16}/2016-precinct-house/2016-precinct-house.zip", RAW_US/"medsl16"/"house.zip")
    fec = {2018: "https://www.fec.gov/resources/cms-content/documents/federalelections2018.xlsx",
           2020: "https://www.fec.gov/resources/cms-content/documents/federalelections2020.xlsx",
           2022: "https://www.fec.gov/resources/cms-content/documents/federalelections2022.xlsx"}
    if year in fec: get(fec[year], RAW_US/"fec"/f"f{year}.xlsx")


def _patch_known_gaps(year: int, races: pd.DataFrame) -> pd.DataFrame:
    # MEDSL's 2022 individual-state repository has no Tennessee governor rows. Certified statewide D/R totals are from
    # the Tennessee Secretary of State 2022 general-election certification (Martin / Lee); county rows remain absent.
    if year == 2022:
        m = races["race_key"].eq("tn-gov")
        races.loc[m, ["final_d2", "final_votes", "d_wins"]] = [572_818 / (572_818 + 1_129_390),
                                                                 572_818 + 1_129_390, False]
        races.loc[m, "note"] = "MEDSL state file omits governor; certified TN SoS statewide D/R totals; counties unavailable"
        m = races["race_key"].eq("ca-sen-s")
        races.loc[m, "note"] = "simultaneous unexpired-term ballot; counties from CA SoS Statement of Vote"
    return races


def build_cycle(year: int, force: bool = False) -> dict[str, pd.DataFrame]:
    if year not in YEARS: raise ValueError(f"year must be one of {YEARS}")
    dest = OUT / str(year)
    if not force and all((dest/f"{t}.csv").exists() for t in TABLES):
        return load_cycle(year, build=False)
    fetch_inputs(year)
    races, counties = build_races_counties(year); races = _patch_known_gaps(year, races)
    house = build_house(year)
    tables = {"races": races.drop(columns=["county_d", "county_r"]), "holdovers": holdovers(year, races),
              "counties": counties, "house": house, "generic": build_generic(year, house), "poll_close": poll_close(year)}
    dest.mkdir(parents=True, exist_ok=True)
    for name, d in tables.items(): d.to_csv(dest/f"{name}.csv", index=False)
    return tables


def load_cycle(year: int, build: bool = True) -> dict[str, pd.DataFrame]:
    dest = OUT / str(year)
    if build and not all((dest/f"{t}.csv").exists() for t in TABLES): return build_cycle(year)
    if not all((dest/f"{t}.csv").exists() for t in TABLES): raise FileNotFoundError(dest)
    out = {t: pd.read_csv(dest/f"{t}.csv") for t in TABLES}
    for t in ("races", "house"): 
        for c in ("special", "d_wins", "d_caucus_win", "uncontested"):
            if c in out[t]: out[t][c] = out[t][c].astype("boolean")
    return out


def verify_cycle(year: int, t: dict[str, pd.DataFrame] | None = None) -> dict:
    t = t or load_cycle(year); r, c, h, g = t["races"], t["counties"], t["house"], t["generic"]
    sums = c.groupby("race_key")[["d", "r"]].sum()
    fail = []
    for _, x in r.iterrows():
        if x["race_key"] not in sums.index: fail.append(x["race_key"] + ":no-counties"); continue
        v = sums.loc[x["race_key"]].sum()
        if x["final_votes"] and abs(v/x["final_votes"] - 1) > .005: fail.append(x["race_key"])
    got = int((h["winner_party"] == "D").sum())
    return {"year": year, "races": len(r), "county_rows": len(c), "county_fail": fail,
            "house": len(h), "prior": h["prior_src"].value_counts().to_dict(),
            "polls": int((r["n_polls"] > 0).sum()), "senate_d": int(t["holdovers"]["d_seats_final_two_party"].iat[0]),
            "house_d": got, "house_d_expected": HOUSE_D_SEATS[year], "generic_polls": int(g["n_polls"].iat[0])}


def main(argv: list[str] | None = None) -> None:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a.pop(0) if a else "build"
    years = [int(x) for x in a] if a else list(YEARS)
    if cmd not in ("build", "verify"): raise SystemExit("usage: us_national_data.py build|verify [2018 2020 2022 2024]")
    for y in years:
        z = verify_cycle(y, build_cycle(y) if cmd == "build" else load_cycle(y))
        print(f"{y}: races={z['races']} counties={z['county_rows']} county_fail={z['county_fail'] or 'none'} "
              f"House={z['house']} prior={z['prior']} polls={z['polls']} generic_n={z['generic_polls']} "
              f"SenateD={z['senate_d']} HouseD={z['house_d']}/{z['house_d_expected']}")


if __name__ == "__main__": main()
