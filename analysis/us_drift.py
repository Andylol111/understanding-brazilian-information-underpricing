"""Does a state's earlier partisan drift predict its polling miss? (analysis only; docs/us_drift.md)

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_drift.py fetch     # open data -> $BRUNDER_DATA/raw/us
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_drift.py panel     # miss x drift panel -> data_public
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_drift.py tests     # all tests -> results/us_drift, tables
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_drift.py predict   # 2026 predictions -> data_public, config
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_drift.py score results.csv   # after certification

Hypothesis: polls under-capture a state's long-run partisan drift, so a state that was trending Democratic before an
election has its certified two-party Democratic share *above* its final poll average, and a state trending Republican
below it. Miss = certified two-party D share - final poll average (v3 rule of analysis/us_poll_error.py, final 45 days,
races with a poll in the last 21 days), in points of two-party share; + = polls understated the Democrat.

Inputs (every predictor uses only data public before that election; see `acs_years` / `vote_features`)
  538 raw_polls 1998-2022 (CC BY 4.0) and 538's archived 2024 Senate/governor/president files (Wayback): polls and,
      through 2022, the certified result; the 2024 results are MEDSL (CC0).
  MEDSL state returns 1976-2024 (president, Senate, House; doi:10.7910/DVN/42MVDX, CC0): lean = state two-party D share
      minus the national two-party D share of the same election.
  ACS 1-year PUMS 2008-2024 (Census Bureau, public domain): non-Hispanic white adults 25+ without a bachelor's degree,
      Hispanic and 65+ shares of adults 18+ (state weighted counts); Census population estimates NETMIG 2011-2025.
Units: points of two-party share (lean, drift, miss).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from scipy import stats

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_poll_error as pe

RAW = us.US_RAW
ROOT = us.ROOT_DIR
PUB = ROOT / "data_public"
OUT = ROOT / "results" / "us_drift"
CACHE = us.PROCESSED / "us_drift"
POLLS = RAW / "polls"
DV = "https://dataverse.harvard.edu/api/access/datafile"
UA = {"User-Agent": "brunder-state-drift-research/1.0 (public-data analysis; no automation evasion)"}
PUMS = "https://www2.census.gov/programs-surveys/acs/data/pums/{y}/1-Year/csv_pus.zip"
PUMS_YEARS = (2008, 2009, 2011, 2013, 2015, 2017, 2019, 2021, 2023, 2024)
PUMS_AGG = RAW / "acs_pums_agg"
NST = {"2010-19": "https://www2.census.gov/programs-surveys/popest/datasets/2010-2019/national/totals/nst-est2019-alldata.csv",
       "2020-25": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/state/totals/NST-EST2025-ALLDATA.csv"}
MEDSL_PRES = (13887042, RAW / "medsl_hist" / "1976-2024-president-state.csv")
MEDSL_SEN = (13887039, RAW / "medsl_hist" / "1976-2024-senate-state.tab")
ELECTION_2024 = pd.Timestamp("2024-11-05")
MAJOR_MIN = 75.0             # minimum mean D + R poll share (%) of a race in the miss table
WB_2024 = "http://web.archive.org/web/20241118073720id_/https://projects.fivethirtyeight.com/polls/data/{}_polls.csv"
RACE_RE = re.compile(r"^(\d{4})_(Pres|Sen|Gov)-(GS?)_([A-Z]{2})$")
OFFICE = {"Pres": "pres", "Sen": "sen", "Gov": "gov"}
FIRST_TEST, LAST_TEST = 2006, 2024
ERAS = {"2006-14": (2006, 2014), "2016-24": (2016, 2024)}
Z90 = pe.Z90


# ------------------------------------------------------------------ open-data fetches (cached, one polite request each)

def _stream(url: str, dest: Path) -> Path:
    """Download once with an identifying client; verify cached/downloaded byte size when the server supplies it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=900, follow_redirects=True) as c:
        size = None
        try:
            h = c.head(url)
            if h.is_success and h.headers.get("content-length"):
                size = int(h.headers["content-length"])
        except httpx.HTTPError:
            pass
        if dest.exists() and (size is None or dest.stat().st_size == size):
            return dest
        if dest.exists():
            dest.unlink()
        tmp = dest.with_name(dest.name + ".part")
        tmp.unlink(missing_ok=True)
        n = 0
        with c.stream("GET", url) as r:
            r.raise_for_status()
            expected = int(r.headers["content-length"]) if r.headers.get("content-length") else size
            with open(tmp, "wb") as f:
                for ch in r.iter_bytes(1 << 20):
                    n += len(ch)
                    f.write(ch)
        if expected is not None and n != expected:
            tmp.unlink(missing_ok=True)
            raise OSError(f"short download {url}: {n} bytes, expected {expected}")
    tmp.rename(dest)
    return dest


def medsl(spec: tuple[int, Path]) -> Path:
    return _stream(f"{DV}/{spec[0]}?format=original", spec[1])


def pums_year(y: int, keep_zip: bool = False) -> Path:
    """Weighted state counts from the ACS 1-year person file of year y (the ~600 MB zip is reduced to ~50 rows)."""
    out = PUMS_AGG / f"pums_{y}.csv"
    if out.exists():
        q = pd.read_csv(out)
        if 51 <= len(q) <= 53 and q["pop"].sum() > 250_000_000:
            return out
        out.unlink()
    z = _stream(PUMS.format(y=y), RAW / "acs_pums" / f"{y}_csv_pus.zip")
    with zipfile.ZipFile(z) as test:
        bad = test.testzip()
        if bad:
            z.unlink(missing_ok=True)
            raise zipfile.BadZipFile(f"{z}: first bad member {bad}")
    want = {"ST", "STATE", "AGEP", "HISP", "RAC1P", "SCHL", "PWGTP"}      # state code: ST (2008-2018), STATE (2019+)
    tot = []
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            if not n.lower().endswith(".csv"):
                continue
            with zf.open(n) as fh:
                for ch in pd.read_csv(fh, usecols=lambda c: c.upper() in want, chunksize=500_000, low_memory=False):
                    ch.columns = ["ST" if c.upper() == "STATE" else c.upper() for c in ch.columns]
                    w = ch["PWGTP"].astype(float)
                    adult, a25 = ch["AGEP"] >= 18, ch["AGEP"] >= 25
                    white = (ch["HISP"] == 1) & (ch["RAC1P"] == 1)
                    nocol = ch["SCHL"].astype(float) < 21           # below a bachelor's degree (codes 1-20)
                    tot.append(pd.DataFrame({
                        "state_fips": ch["ST"], "pop": w, "adult": w * adult, "hisp_adult": w * (adult & (ch["HISP"] > 1)),
                        "a65": w * (ch["AGEP"] >= 65), "adult25": w * a25, "ncw25": w * (a25 & white & nocol),
                        "white25": w * (a25 & white)}).groupby("state_fips").sum())
    g = pd.concat(tot).groupby(level=0).sum().reset_index().assign(year=y)
    PUMS_AGG.mkdir(parents=True, exist_ok=True)
    g.to_csv(out, index=False)
    if not keep_zip:
        z.unlink()
    return out


def nst_netmig() -> pd.DataFrame:
    """state_fips, year, pop, netmig (net international + domestic migration of the July-to-July year ending `year`)
    from the 2010-19 and 2020-25 vintages (the 2000-09 vintage publishes only a cumulative 2000-09 total by state; the
    2020-25 vintage wins for 2020, whose first period starts at the April 2020 census base)."""
    rows = []
    for tag, url in NST.items():
        d = pd.read_csv(io.BytesIO(_stream(url, RAW / "census" / Path(url).name).read_bytes()), encoding="latin-1")
        d = d[d["STATE"].astype(int) > 0]
        for col in d.columns:
            if col.startswith("NETMIG"):
                y = int(col[len("NETMIG"):])
                rows.append(pd.DataFrame({"state_fips": d["STATE"].astype(int), "year": y, "netmig": d[col].astype(float),
                                          "pop": d[f"POPESTIMATE{y}"].astype(float), "vintage": tag}))
    d = pd.concat(rows)
    return d.sort_values("vintage").drop_duplicates(["state_fips", "year"], keep="last").drop(columns="vintage")


def fetch_all() -> None:
    for y in PUMS_YEARS:
        print("pums", y, pums_year(y), flush=True)
    for spec in (MEDSL_PRES, MEDSL_SEN):
        print(medsl(spec), flush=True)
    print(len(nst_netmig()), "netmig rows", flush=True)


# ------------------------------------------------------------------ vote history (MEDSL state returns 1976-2024)

def _share(d: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = d.pivot_table(index=keys, columns="party_simplified", values="candidatevotes", aggfunc="sum").reset_index()
    for p in ("DEMOCRAT", "REPUBLICAN"):
        if p not in g:
            g[p] = 0.0
    g["d"], g["r"] = g["DEMOCRAT"].fillna(0.0), g["REPUBLICAN"].fillna(0.0)
    return g


def pres_state(path: Path | None = None) -> pd.DataFrame:
    """state, state_fips, year, share (two-party D, points), lean (share - national two-party D share, points)."""
    d = pd.read_csv(path or medsl(MEDSL_PRES), low_memory=False)
    g = _share(d[d["office"] == "US PRESIDENT"], ["state_po", "state_fips", "year"])
    g["share"] = 100 * g["d"] / (g["d"] + g["r"])
    nat = g.groupby("year")[["d", "r"]].sum()
    g["lean"] = g["share"] - g["year"].map(100 * nat["d"] / (nat["d"] + nat["r"]))
    return g.assign(state=g["state_po"].str.lower())[["state", "state_fips", "year", "share", "lean"]]


def house_national() -> pd.Series:
    """year -> national two-party D share of the House vote (points), 1998-2022: the certified result 538 attaches to its
    generic-ballot polls (raw_polls `House-G-US`). It is the reference of the Senate/governor leans, so office leans exist
    for 1998-2022 contests. (MEDSL's House file needs a Dataverse guestbook, so it is not used; the 2024 national
    House vote is therefore missing and 2024 Senate/governor contests do not enter an office trend.)"""
    f = RAW / "polls" / "538_raw_polls.csv"
    us._fetch(pe.us_polls.RAW_538, f)
    d = pd.read_csv(f, low_memory=False)
    d = d[d["type_simple"] == "House-G-US"].groupby("cycle")[["cand1_actual", "cand2_actual"]].first()
    return 100 * d["cand1_actual"] / (d["cand1_actual"] + d["cand2_actual"])


def senate_state(path: Path | None = None) -> pd.DataFrame:
    """state, year, special, share (two-party D, points). Only contests where the two major parties took >= 70 % of
    the vote (an independent who caucuses with a party, e.g. King in Maine 2012, makes the Democratic line meaningless)."""
    d = pd.read_csv(path or medsl(MEDSL_SEN), low_memory=False)
    d = d[(d["stage"].str.upper() == "GEN") & (d["mode"].str.lower() == "total")]
    tv = d.groupby(["state_po", "year", "special"])["totalvotes"].max().rename("tv").reset_index()
    g = _share(d, ["state_po", "year", "special"]).merge(tv, on=["state_po", "year", "special"])
    g = g[(g["d"] > 0) & (g["r"] > 0) & ((g["d"] + g["r"]) >= 0.7 * g["tv"])]
    g["share"] = 100 * g["d"] / (g["d"] + g["r"])
    return g.assign(state=g["state_po"].str.lower())[["state", "year", "special", "share"]]


def gov2024() -> pd.DataFrame:
    """2024 governor results (11 states): two-party D share from MEDSL 2024 precinct returns (CC0), via the county
    aggregation of analysis/us_national_data.py."""
    f = CACHE / "gov2024.csv"
    if f.exists():
        return pd.read_csv(f)
    import us_national_data as nd
    rows = []
    for st in nd.GOV[2024].split():
        _stream(f"{nd._MEDSL24}/{st}24.zip", RAW / "medsl24" / f"{st}24.zip")
        c = nd._aggregate_county(nd.state_rows(2024, st, "gov"), 2024, st, "gov", False)
        rows.append({"state": st, "year": 2024, "share": 100 * c["d"].sum() / (c["d"].sum() + c["r"].sum())})
    f.parent.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame(rows)
    out.to_csv(f, index=False)
    return out


class History:
    """Office-specific leans by state and year: lean = state two-party D share - national House D share (points)."""

    def __init__(self, polls538: pd.DataFrame | None = None):
        self.pres = pres_state()
        self.p = {(s, y): v for s, y, v in zip(self.pres["state"], self.pres["year"], self.pres["lean"])}
        house = house_national()
        sen = senate_state()
        sen = sen.assign(lean=sen["share"] - sen["year"].map(house)).dropna(subset=["lean"])
        sen = sen.groupby(["state", "year"])["lean"].mean()
        # governors: MEDSL publishes no state governor file; the polled races of 538's file carry their certified
        # result (polled races only)
        g = polls538[polls538["race"].str.contains("_Gov-G_")] if polls538 is not None else pd.DataFrame()
        gov = g.groupby(["state", "cycle"])["act"].first().mul(100).rename("share").reset_index().rename(
            columns={"cycle": "year"}) if len(g) else pd.DataFrame(columns=["state", "year", "share"])
        gov = gov.assign(lean=gov["share"] - gov["year"].map(house)).dropna(subset=["lean"])
        gov = gov.set_index(["state", "year"])["lean"]
        self.sen, self.gov = sen.to_dict(), gov.to_dict()

    def lean(self, state: str, year: int) -> float:
        return self.p.get((state, year), np.nan)

    def pres_drift(self, state: str, t: int, cycles: int) -> float:
        """Change of the state's presidential lean over the `cycles` presidential cycles before election t."""
        last = pe.prev_pres_year(t)
        return self.lean(state, last) - self.lean(state, last - 4 * cycles)

    def office_trend(self, office: str, state: str, t: int) -> tuple[float, int, int]:
        """(trend per 4 years, latest year, earlier year) between the state's two latest same-office contests before t
        (Senate: any seat, within 6 years; governor: within 4); president: the 1-cycle presidential drift."""
        if office == "pres":
            return self.pres_drift(state, t, 1), pe.prev_pres_year(t), pe.prev_pres_year(t) - 4
        hist, reach = (self.sen, 6) if office == "sen" else (self.gov, 4)
        ys = sorted((y for (s, y) in hist if s == state and y < t), reverse=True)
        if len(ys) < 2 or ys[0] < t - reach:
            return np.nan, 0, 0
        y1, y2 = ys[0], ys[1]
        return (hist[(state, y1)] - hist[(state, y2)]) * 4 / (y1 - y2), y1, y2


# ------------------------------------------------------------------ electorate change (ACS PUMS, population estimates)

def acs_table() -> pd.DataFrame:
    """state_fips, year, ncw (non-Hispanic white without BA, % of adults 25+), hisp (% of adults), a65 (% of adults)."""
    d = pd.concat([pd.read_csv(pums_year(y)) for y in PUMS_YEARS])
    return pd.DataFrame({"state_fips": d["state_fips"], "year": d["year"], "ncw": 100 * d["ncw25"] / d["adult25"],
                         "hisp": 100 * d["hisp_adult"] / d["adult"], "a65": 100 * d["a65"] / d["adult"]})


def acs_end(t: int) -> int:
    """Latest 1-year ACS public before election t: the previous year (released in September of t); 2026 uses 2024
    (2025 is not yet released)."""
    return 2024 if t == 2026 else t - 1


def acs_start(end: int, target: int, lo: int, hi: int) -> int | None:
    """The ACS year `lo..hi` years before `end` closest to `target` years (ties: the longer window)."""
    c = [(abs(end - y - target), -(end - y), y) for y in PUMS_YEARS if lo <= end - y <= hi]
    return min(c)[2] if c else None


class Electorate:
    def __init__(self):
        self.acs = acs_table().set_index(["state_fips", "year"])
        nm = nst_netmig()
        self.nm = (100 * nm["netmig"] / nm["pop"]).groupby([nm["state_fips"], nm["year"]]).first()

    def change(self, fips: int, t: int, var: str, kind: str) -> float:
        """Change of `var` per 4 years between the ACS year before t and an earlier year (short: 3-5 years, long: 7-9)."""
        end = acs_end(t)
        start = acs_start(end, *((4, 3, 5) if kind == "s" else (8, 7, 9)))
        if start is None or (fips, end) not in self.acs.index:
            return np.nan
        return (self.acs.at[(fips, end), var] - self.acs.at[(fips, start), var]) * 4 / (end - start)

    def netmig(self, fips: int, t: int, years: int) -> float:
        """Mean annual net migration (% of population) over the `years` July-to-July years ending before election t."""
        v = [self.nm.get((fips, y), np.nan) for y in range(t - years, t)]
        return float(np.mean(v)) if not np.isnan(v).any() else np.nan


# ------------------------------------------------------------------ polls and the miss table

def polls_2024() -> pd.DataFrame:
    """538's archived 2024 Senate/governor/president general-election polls in load_538's layout (without `act`).
    One question per poll and race: likely voters, then registered, then adults; then the ballot with the most
    candidates. Republican = the highest Republican in that question."""
    codes = pd.read_csv(medsl(MEDSL_PRES), usecols=["state", "state_po"]).drop_duplicates()
    name = dict(zip(codes["state"].str.title(), codes["state_po"].str.lower()))
    pop_rank = {"lv": 0, "v": 0, "rv": 1, "a": 2}
    rows = []
    for f, typ in (("senate", "Sen"), ("governor", "Gov"), ("president", "Pres")):
        local = POLLS / f"538_{f}_polls_historical.csv"
        src = local if local.exists() else _stream(WB_2024.format(f), POLLS / f"wb_{f}.csv")
        d = pd.read_csv(src, low_memory=False)
        d = d[(d["cycle"] == 2024) & (d["stage"] == "general") & (~d["hypothetical"].astype(bool))
              & d["state"].isin(name) & d["sample_size"].notna() & d["pct"].notna()].copy()
        d["st"] = d["state"].map(name)
        d["special"] = (d["seat_name"].fillna("") != "Class I") if typ == "Sen" else False
        for (poll, race), g in d.groupby(["poll_id", "race_id"]):
            best = None
            for qid, q in g.groupby("question_id"):
                dd, rr = q[q["party"] == "DEM"], q[q["party"] == "REP"]
                if dd.empty or rr.empty or dd["pct"].iat[0] + rr["pct"].max() <= 0:
                    continue
                key = (pop_rank.get(str(q["population"].iat[0]).lower(), 3), -len(q), qid)
                if best is None or key < best[0]:
                    best = (key, q, float(dd["pct"].iat[0]), float(rr["pct"].max()))
            if best is None:
                continue
            _, q, dem, opp = best
            st, sp = q["st"].iat[0], bool(q["special"].iat[0])
            end = pd.to_datetime(q["end_date"].iat[0], format="%m/%d/%y")
            pid = q["pollster_rating_id"].iat[0]
            rows.append({"cycle": 2024, "race": f"2024_{typ}-G{'S' if sp else ''}_{st.upper()}", "race_id": int(race),
                         "type": f"{typ}-G", "state": st, "pollster": q["pollster"].iat[0],
                         "pid": f"r{int(pid)}" if pd.notna(pid) else "n" + str(q["pollster"].iat[0]),
                         "partisan": q["partisan"].iat[0] if q["partisan"].iat[0] in ("DEM", "REP") else None,
                         "tte": int((ELECTION_2024 - end).days), "n": float(q["sample_size"].iat[0]), "dem": dem,
                         "opp": opp, "s": dem / (dem + opp)})
    return pd.DataFrame(rows)


def results_2024() -> pd.DataFrame:
    """race -> certified two-party D share (fraction) for the 2024 races: MEDSL president, Senate and (above) governor."""
    out = {}
    for s, v in zip(*[pres_state().query("year == 2024")[c] for c in ("state", "share")]):
        out[f"2024_Pres-G_{s.upper()}"] = v / 100
    sen = pd.read_csv(medsl(MEDSL_SEN), low_memory=False)
    sen = sen[(sen["year"] == 2024) & (sen["stage"].str.upper() == "GEN") & (sen["mode"].str.lower() == "total")]
    for (st, sp), g in _share(sen, ["state_po", "special"]).groupby(["state_po", "special"]):
        if g["d"].iat[0] > 0 and g["r"].iat[0] > 0:
            out[f"2024_Sen-G{'S' if sp else ''}_{st}"] = float(g["d"].iat[0] / (g["d"].iat[0] + g["r"].iat[0]))
    for st, v in zip(gov2024()["state"], gov2024()["share"]):
        out[f"2024_Gov-G_{st.upper()}"] = v / 100
    return pd.Series(out, name="act")


def all_polls() -> pd.DataFrame:
    """Every Senate/governor/president state poll of 538's file (1998-2022, with certified results) and 2024."""
    p = pe.load_538()
    m = p["race"].str.extract(RACE_RE)
    p = p[m[0].notna()].copy()
    p["state"] = m[3].str.lower()
    p["office"] = m[1].map(OFFICE)
    p["special"] = m[2] == "GS"
    p = p[p["state"] != "us"]
    q = polls_2024()
    q = q.assign(act=q["race"].map(results_2024()), err=np.nan)
    q["err"] = q["s"] - q["act"]
    q["office"] = q["race"].str.extract(RACE_RE)[1].map(OFFICE).to_numpy()
    q["special"] = q["race"].str.contains("-GS_")
    return pd.concat([p, q.dropna(subset=["act"])], ignore_index=True)


def miss_table(polls: pd.DataFrame) -> pd.DataFrame:
    """Race, cycle, office, state, avg (v3 average, points), act (points), n_polls, miss = act - avg. Universe: races of
    even cycles >= 2002 with a poll in the final 21 days (as in docs/us_poll_error.md), excluding races where the two
    major-party candidates average under 75 % in the polls (a strong independent, e.g. Maine with King: the D-R share
    is then not the contest). The rule was fixed before any measure was tested."""
    rows = []
    v3 = pe.SCHEMES["v3"]
    for race, g in polls[(polls["cycle"] % 2 == 0) & (polls["cycle"] >= 2002)].groupby("race"):
        if not ((g["tte"] >= 1) & (g["tte"] <= pe.STATS_DAYS)).any() or (g["dem"] + g["opp"]).mean() < MAJOR_MIN:
            continue
        a = pe.average(g, v3, None)
        if a:
            rows.append({"race": race, "cycle": int(g["cycle"].iat[0]), "office": g["office"].iat[0],
                         "state": g["state"].iat[0], "special": bool(g["special"].iat[0]), "avg": 100 * a["avg"],
                         "act": 100 * float(g["act"].iat[0]), "n_polls": a["n_polls"], "n_pollsters": a["n_pollsters"]})
    d = pd.DataFrame(rows)
    d["miss"] = d["act"] - d["avg"]
    return d


# ------------------------------------------------------------------ the drift measures

VOTE = ["pres_lean", "pres_d1", "pres_d2", "pres_d3", "office_trend", "drift_combo"]
DEMO = ["ncw_s", "ncw_l", "hisp_s", "hisp_l", "a65_s", "a65_l", "nm_s", "nm_l"]
MEASURES = VOTE + DEMO
LABEL = {"pres_lean": "latest pre-election presidential two-party D lean vs nation",
         "pres_d1": "presidential lean change, previous cycle", "pres_d2": "... previous 2 cycles",
         "pres_d3": "... previous 3 cycles", "office_trend": "Senate/governor/president own-office lean trend",
         "drift_combo": "pre-specified combination: mean of d1, d2/2 and d3/3 (points per presidential cycle)",
         "ncw_s": "non-college white share, ~4-year change", "ncw_l": "non-college white share, ~8-year change",
         "hisp_s": "Hispanic share of adults, ~4-year change", "hisp_l": "Hispanic share of adults, ~8-year change",
         "a65_s": "65+ share of adults, ~4-year change", "a65_l": "65+ share of adults, ~8-year change",
         "nm_s": "net migration, mean of previous 4 years", "nm_l": "net migration, mean of previous 8 years"}
DIRECTIONAL = {"pres_d1", "pres_d2", "pres_d3", "office_trend", "drift_combo"}
# Measures eligible to carry the 2026 drift prediction: the drift measures with full 2006-2024 coverage. The lean level
# (pres_lean) is the confounder the drift must beat, not a drift measure (it was in this list on the first run and won
# it; removed afterwards, see docs/us_drift.md). Electorate measures cover 37-70 % of races and failed anyway.
FORECAST_CANDIDATES = ["pres_d1", "pres_d2", "pres_d3", "office_trend", "drift_combo"]
FORMULA_COMBO = ("drift = mean(L[p] - L[p-4], (L[p] - L[p-8]) / 2, (L[p] - L[p-12]) / 3), p = last presidential election "
                 "before the race (2024 for 2026); L = state minus national two-party D share, presidential, points")


def features(races: pd.DataFrame, hist: History, elec: Electorate | None, fips: dict[str, int]) -> pd.DataFrame:
    """races: any frame with state, office, cycle (the election year t). All inputs are public before t."""
    out = []
    for st, off, t in zip(races["state"], races["office"], races["cycle"]):
        t = int(t)
        f = {"pres_lean": hist.lean(st, pe.prev_pres_year(t))}
        for k in (1, 2, 3):
            f[f"pres_d{k}"] = hist.pres_drift(st, t, k)
        tr, y1, y2 = hist.office_trend(off, st, t)
        f["office_trend"], f["office_y1"], f["office_y2"] = tr, y1, y2
        parts = [f["pres_d1"], f["pres_d2"] / 2, f["pres_d3"] / 3]
        f["drift_combo"] = float(np.mean(parts)) if not np.isnan(parts).any() else np.nan
        if elec is not None and st in fips:
            fp = fips[st]
            for v, nm in (("ncw", "ncw"), ("hisp", "hisp"), ("a65", "a65")):
                f[f"{nm}_s"], f[f"{nm}_l"] = elec.change(fp, t, v, "s"), elec.change(fp, t, v, "l")
            f["nm_s"], f["nm_l"] = elec.netmig(fp, t, 4), elec.netmig(fp, t, 8)
        out.append(f)
    return pd.DataFrame(out, index=races.index)


def build_panel() -> pd.DataFrame:
    polls = all_polls()
    hist = History(polls[polls["cycle"] <= 2022])
    elec = Electorate()
    fips = dict(zip(hist.pres["state"], hist.pres["state_fips"].astype(int)))
    m = miss_table(polls)
    m = m[(m["cycle"] >= FIRST_TEST) & (m["cycle"] <= LAST_TEST) & (m["state"] != "dc")].reset_index(drop=True)
    return pd.concat([m, features(m, hist, elec, fips)], axis=1)


# ------------------------------------------------------------------ tests

def demean(d: pd.DataFrame, x: str, fe: list[str]) -> pd.DataFrame:
    d = d.dropna(subset=[x, "miss"]).copy()
    d = d[d.groupby(fe)["miss"].transform("size") >= 4]
    d["y"] = d["miss"] - d.groupby(fe)["miss"].transform("mean")
    d["x"] = d[x] - d.groupby(fe)[x].transform("mean")
    return d


def slope(d: pd.DataFrame) -> float:
    den = float((d["x"] ** 2).sum())
    return float((d["x"] * d["y"]).sum() / den) if den > 0 else 0.0


def predict(d: pd.DataFrame, mode: str = "loco") -> pd.DataFrame:
    """Out-of-cycle predictions of the cycle-demeaned miss: beta fitted with no intercept on the other cycles
    ('loco'), only earlier cycles ('past'; needs 2), the other era ('cross': the fixed split ERAS)."""
    out = []
    for c in sorted(d["cycle"].unique()):
        if mode == "loco":
            tr = d[d["cycle"] != c]
        elif mode == "past":
            tr = d[d["cycle"] < c]
            if tr["cycle"].nunique() < 2:
                continue
        else:
            era = next(k for k, (a, b) in ERAS.items() if a <= c <= b)
            tr = d[~d["cycle"].between(*ERAS[era])]
        if tr.empty:
            continue
        b = slope(tr)
        out.append(d[d["cycle"] == c].assign(beta=b, pred=b * d.loc[d["cycle"] == c, "x"]))
    return pd.concat(out) if out else d.iloc[:0].assign(beta=np.nan, pred=np.nan)


def cycle_stats(P: pd.DataFrame, fixed_sign: bool = False) -> pd.DataFrame:
    """Per cycle: races, sum of squared y (baseline: cycle mean only), of y - pred, sign agreements and signed races."""
    pred = P["x"] if fixed_sign else P["pred"]
    sg = (np.sign(pred) != 0) & (np.sign(P["y"]) != 0)
    d = pd.DataFrame({"cycle": P["cycle"], "n": 1, "sse0": P["y"] ** 2, "sse1": (P["y"] - P["pred"]) ** 2,
                      "agree": (np.sign(pred) == np.sign(P["y"])) & sg, "ns": sg.astype(int)})
    return d.groupby("cycle").sum()


def aggregate(cs: pd.DataFrame) -> dict:
    n, ns = cs["n"].sum(), cs["ns"].sum()
    return {"races": int(n), "cycles": len(cs), "rmse0": float(np.sqrt(cs["sse0"].sum() / n)),
            "rmse1": float(np.sqrt(cs["sse1"].sum() / n)), "agree": float(cs["agree"].sum() / ns) if ns else np.nan,
            "k": int(cs["agree"].sum()), "ns": int(ns)}


def boot(cs: pd.DataFrame, B: int = 4000, seed: int = 0) -> dict:
    """95 % cycle-cluster bootstrap intervals of the pooled RMSE difference (model - baseline) and sign agreement."""
    rng = np.random.default_rng(seed)
    a = cs[["n", "sse0", "sse1", "agree", "ns"]].to_numpy(float)
    ix = rng.integers(0, len(a), (B, len(a)))
    s = a[ix].sum(axis=1)
    d = np.sqrt(s[:, 2] / s[:, 0]) - np.sqrt(s[:, 1] / s[:, 0])
    ag = s[:, 3] / np.where(s[:, 4] > 0, s[:, 4], np.nan)
    return {"d_lo": float(np.nanpercentile(d, 2.5)), "d_hi": float(np.nanpercentile(d, 97.5)),
            "agree_lo": float(np.nanpercentile(ag, 2.5)), "agree_hi": float(np.nanpercentile(ag, 97.5))}


def binom_p(k: int, n: int) -> float:
    return float(stats.binomtest(k, n, 0.5, alternative="greater").pvalue) if n else np.nan


def cluster_sign_p(P: pd.DataFrame, fixed_sign: bool = False) -> tuple[int, int, float]:
    """One-sided binomial p treating every state x cycle (the Senate and governor races of a state share an error:
    correlation 0.55 in docs/us_poll_error.md) as one observation: it agrees when most of its signed races agree."""
    pred = P["x"] if fixed_sign else P["pred"]
    ok = (np.sign(pred) != 0) & (np.sign(P["y"]) != 0)
    a = pd.DataFrame({"c": P["cycle"].astype(str) + P["state"], "a": (np.sign(pred) == np.sign(P["y"])).astype(float)})[ok]
    m = a.groupby("c")["a"].mean()
    m = m[m != 0.5]
    k, n = int((m > 0.5).sum()), len(m)
    return k, n, binom_p(k, n)


def evaluate(panel: pd.DataFrame, x: str, fe: list[str] | None = None, mode: str = "loco", rows: pd.Series | None = None) -> dict:
    if fe is None:
        fe = ["cycle"]
    d = demean(panel if rows is None else panel[rows], x, fe)
    if d["cycle"].nunique() < 2:
        return {}
    P = predict(d, mode)
    if P.empty:
        return {}
    cs = cycle_stats(P)
    r = {**aggregate(cs), **boot(cs), "beta": slope(d), "cycles_improved": int((cs["sse1"] < cs["sse0"]).sum())}
    r["d_rmse"] = r["rmse1"] - r["rmse0"]
    r["p_sign"] = binom_p(r["k"], r["ns"])
    r["k_cl"], r["n_cl"], r["p_cl"] = cluster_sign_p(P)
    if x in DIRECTIONAL:
        cf = cycle_stats(P, fixed_sign=True)
        a = aggregate(cf)
        r.update({"fixed_agree": a["agree"], "fixed_k": a["k"], "fixed_ns": a["ns"], "fixed_p": binom_p(a["k"], a["ns"])})
        r["fixed_k_cl"], r["fixed_n_cl"], r["fixed_p_cl"] = cluster_sign_p(P, True)
    r["cycle_betas"] = {int(c): slope(g) for c, g in d.groupby("cycle")}
    return r


def test_all(panel: pd.DataFrame) -> pd.DataFrame:
    """Every measure x sample (all, by office, by era) x scheme (loco, past, cross) on cycle fixed effects."""
    rows = []
    eras = {k: panel["cycle"].between(*v) for k, v in ERAS.items()}
    samples = {"all": None, **{f"era {k}": v for k, v in eras.items()},
               **{f"office {o}": panel["office"] == o for o in ("sen", "gov", "pres")}}
    for x in MEASURES:
        for name, rows_ in samples.items():
            for mode in ("loco", "past") if name == "all" else ("loco",):
                r = evaluate(panel, x, rows=rows_, mode=mode)
                if r:
                    rows.append({"measure": x, "sample": name, "mode": mode, **{k: v for k, v in r.items() if k != "cycle_betas"}})
        r = evaluate(panel, x, mode="cross")
        if r:
            rows.append({"measure": x, "sample": "all", "mode": "cross-era", **{k: v for k, v in r.items() if k != "cycle_betas"}})
        r = evaluate(panel, x, fe=["cycle", "office"])
        if r:
            rows.append({"measure": x, "sample": "all", "mode": "loco, cycle x office FE",
                         **{k: v for k, v in r.items() if k != "cycle_betas"}})
    return pd.DataFrame(rows)


def coverage(panel: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({x: {"races": int(panel[x].notna().sum()), "share": float(panel[x].notna().mean()),
                             "first_cycle": int(panel.loc[panel[x].notna(), "cycle"].min()) if panel[x].notna().any() else 0}
                         for x in MEASURES}).T


def era_betas(panel: pd.DataFrame) -> pd.DataFrame:
    """Slope (points of miss per point of cycle-demeaned measure) by cycle and by era, with the lean-level control."""
    rows = []
    for x in MEASURES:
        d = demean(panel, x, ["cycle"])
        row = {"measure": x, **{str(c): slope(g) for c, g in d.groupby("cycle")}}
        for k, (a, b) in ERAS.items():
            e = d[d["cycle"].between(a, b)]
            row[f"era {k}"] = slope(e) if len(e) > 10 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def lean_control(panel: pd.DataFrame, x: str) -> dict:
    """Coefficient of x with the (cycle-demeaned) presidential lean level as a control, cycle-cluster bootstrap 95 %;
    and the leave-one-cycle-out RMSE of (lean + x) minus that of lean alone (does the drift add to the level?)."""
    d = demean(panel.dropna(subset=["pres_lean"]), x, ["cycle"])
    d["lv"] = d["pres_lean"] - d.groupby("cycle")["pres_lean"].transform("mean")

    def fit(g, cols=("x", "lv")):
        return np.linalg.lstsq(np.column_stack([g[c] for c in cols]), g["y"].to_numpy(), rcond=None)[0]

    b = fit(d)
    rng = np.random.default_rng(1)
    cyc = [g for _, g in d.groupby("cycle")]
    bs = [fit(pd.concat([cyc[i] for i in rng.integers(0, len(cyc), len(cyc))]))[0] for _ in range(1000)]
    rows = []
    for c, te in d.groupby("cycle"):
        tr = d[d["cycle"] != c]
        p2 = np.column_stack([te["x"], te["lv"]]) @ fit(tr)
        p1 = te["lv"].to_numpy() * fit(tr, ("lv",))[0]
        rows.append({"n": len(te), "sse0": float(((te["y"] - p1) ** 2).sum()), "sse1": float(((te["y"] - p2) ** 2).sum()),
                     "agree": 0, "ns": 0})
    cs = pd.DataFrame(rows)
    a, bb = aggregate(cs), boot(cs)
    return {"measure": x, "beta_x": float(b[0]), "beta_lean": float(b[1]), "lo": float(np.percentile(bs, 2.5)),
            "hi": float(np.percentile(bs, 97.5)), "rmse_lean": a["rmse0"], "rmse_lean_x": a["rmse1"],
            "d_rmse_vs_lean": a["rmse1"] - a["rmse0"], "d_lo": bb["d_lo"], "d_hi": bb["d_hi"]}


def decide(res: pd.DataFrame) -> pd.DataFrame:
    """Include rule (fixed before the results were read): pooled leave-one-cycle-out RMSE improves with the 95 %
    cycle-cluster interval below zero, sign agreement interval above 0.5, and the two eras (fitted on the other era)
    both improve with the same slope sign. A measure is 'exploratory' if only the pooled criteria hold."""
    out = []
    for x in MEASURES:
        a = res[(res["measure"] == x) & (res["sample"] == "all") & (res["mode"] == "loco")]
        c = res[(res["measure"] == x) & (res["mode"] == "cross-era")]
        e = res[(res["measure"] == x) & res["sample"].str.startswith("era")]
        if a.empty:
            continue
        a = a.iloc[0]
        pooled = bool(a["d_hi"] < 0 and a["agree_lo"] > 0.5)
        stable = bool(len(c) and c.iloc[0]["d_rmse"] < 0 and len(e) == 2 and np.sign(e["beta"]).nunique() == 1
                      and (e["d_rmse"] < 0).all())
        out.append({"measure": x, "pooled_pass": pooled, "era_stable": stable,
                    "decision": "include" if pooled and stable else "exploratory only" if pooled else "don't include"})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ 2026

def polled_2026() -> list[dict]:
    """Polled 2026 Senate/governor races (hub polls with a v3 average) with their state and office."""
    hub = json.loads((ROOT / "site" / "data" / "us2026" / "hub" / "polls.json").read_text())
    out = []
    for key, race in hub["races"].items():
        rows = pe.rows_2026(race, {}) if race else pd.DataFrame()
        a = pe.average(rows, pe.SCHEMES["v3"], None) if not rows.empty else None
        if a and (rows["dem"] + rows["opp"]).mean() >= MAJOR_MIN:
            out.append({"race": key, "state": key[:2], "office": key.split("-")[1], "cycle": 2026,
                        "avg": 100 * a["avg"], "n_polls": a["n_polls"], "asof": hub["asof"]})
    return out


def predict_2026(panel: pd.DataFrame, measure: str, fe: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Predicted miss of each polled 2026 race relative to the mean miss of the polled races, from `measure`:
    beta (all cycles 2006-2024, cycle fixed effects) x (measure - its mean over the polled races). Interval: the
    leave-one-cycle-out residual sd plus the slope's cycle-bootstrap spread."""
    if fe is None:
        fe = ["cycle"]
    d = demean(panel, measure, fe)
    P = predict(d, "loco")
    beta = slope(d)
    sd = float(np.sqrt(((P["y"] - P["pred"]) ** 2).mean()))
    rng = np.random.default_rng(7)
    cyc = [g for _, g in d.groupby("cycle")]
    bs = np.array([slope(pd.concat([cyc[i] for i in rng.integers(0, len(cyc), len(cyc))])) for _ in range(2000)])
    hist = History(all_polls().query("cycle <= 2022"))
    elec = Electorate()
    fips = dict(zip(hist.pres["state"], hist.pres["state_fips"].astype(int)))
    r = pd.DataFrame(polled_2026())
    r = pd.concat([r, features(r, hist, elec, fips)], axis=1)
    r["x"] = r[measure] - r[measure].mean()
    ok = r[measure].notna()
    r["pred"] = np.where(ok, beta * r["x"], np.nan)
    r["sd"] = np.sqrt(sd**2 + (r["x"] * bs.std()) ** 2)
    r["lo90"], r["hi90"] = r["pred"] - Z90 * r["sd"], r["pred"] + Z90 * r["sd"]
    r["direction"] = np.where(ok, np.where(r["pred"] > 0, "D", "R"), "")
    meta = {"measure": measure, "beta": beta, "beta_boot_sd": float(bs.std()), "beta_boot_95": [float(np.percentile(bs, 2.5)),
            float(np.percentile(bs, 97.5))], "loco_resid_sd": sd, "races_fitted": len(d), "cycles": int(d["cycle"].nunique()),
            "polled": len(r), "with_measure": int(ok.sum())}
    return r, meta


FAMILY = ["us2026_drift_v1", "us2026_lean_v1"]
KINDS = {"drift_combo": {"key": "drift", "id": "us2026_drift_v1", "kind": "us_state_polling_miss_drift",
                         "csv": "us_state_drift_2026.csv"},
         "pres_lean": {"key": "lean", "id": "us2026_lean_v1", "kind": "us_state_polling_miss_lean_level",
                       "csv": "us_state_drift_2026_lean.csv"}}


def write_prereg_2026(r: pd.DataFrame, meta: dict, choice: dict, res: pd.DataFrame, lean: pd.DataFrame) -> Path:
    """Write the standalone, unregistered hypothesis (registration is the user's). Race universe, poll averages and
    point predictions are frozen here. `weak_historical_signal` is true unless the measure improves out of cycle with
    a 95 % interval below zero in the pooled test and across eras (fitted on the other era); a drift measure must also
    improve over the lean level."""
    x = meta["measure"]
    k = KINDS[x]
    path = ROOT / "configs" / "hypotheses" / f"{k['id']}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = lambda s, m: res[(res["measure"] == x) & (res["sample"] == s) & (res["mode"] == m)].iloc[0]
    cross, e1, e2 = row("all", "cross-era"), row("era 2006-14", "loco"), row("era 2016-24", "loco")
    if r["pred"].isna().any():
        raise ValueError("a polled race lacks the measure: the frozen universe must be complete")
    is_lean = x == "pres_lean"
    ok = choice["d_hi"] < 0 and cross["d_hi"] < 0
    if not is_lean:
        lv = lean[lean["measure"] == x].iloc[0]
        ok = ok and lv["d_hi"] < 0
    weak = not ok
    if is_lean:
        hyp = ['hypothesis = """Polls understate the dominant party of lopsided states: relative to the cycle-wide polling',
               'miss, races in states whose presidential lean is more Democratic have a more Democratic',
               'certified-minus-poll-average miss, and more Republican states a more Republican one."""']
        select = ('selection_note = "Chosen AFTER seeing the 2006-2024 results. It was in the first candidate list of '
                  'the drift analysis, won it, and was then removed from the drift candidates because it is a level, not '
                  'a drift; it is registered separately here as its own hypothesis. Its historical numbers are therefore '
                  'in-sample selected."')
        mcut = 'measure_data_cutoff = "MEDSL state presidential return 2024 (certified; CC0): the last election before 2026"'
        formula = 'formula = "lean = L[2024]; L = state two-party D share minus national two-party D share, presidential, points"'
        pred = f'prediction = "historical_beta x (lean - mean lean of the {len(r)} races); cycle-fixed-effects slope, 2006-2024"'
        extra = []
        caveat = ('caveat = "Small: predicted relative misses reach about +-1.4 points in the most lopsided states against a '
                  '2.3-point race residual sd; the gain is 0.1 point of RMSE. Registered as a direction test only. Lean '
                  'and drift are uncorrelated among these 48 races (r = -0.01), so the two tests are nearly independent."')
        dep = ('dependence_note = "Senate and governor races of one state share the same lean and about half their error '
               '(corr 0.55): the per-race binomial is anti-conservative; the state-collapsed test below is the robustness '
               'check."')
    else:
        hyp = ['hypothesis = """Polls under-capture long-run state partisan drift: relative to the cycle-wide polling miss,',
               'races in states whose presidential lean has been moving toward the Democrats have a more Democratic',
               'certified-minus-poll-average miss, and states moving toward the Republicans a more Republican one."""']
        select = None
        mcut = 'measure_data_cutoff = "MEDSL state presidential returns 2012, 2016, 2020, 2024 (certified; CC0)"'
        formula = 'formula = "' + FORMULA_COMBO + '"'
        pred = f'prediction = "historical_beta x (drift - mean drift of the {len(r)} races); cycle-fixed-effects slope, 2006-2024"'
        extra = [f'delta_rmse_over_lean_level = {lv["d_rmse_vs_lean"]:.4f}',
                 f'delta_rmse_over_lean_level_ci95 = [{lv["d_lo"]:.4f}, {lv["d_hi"]:.4f}]']
        caveat = ('caveat = "Weak: predicted relative misses are +-0.6 points against a 2.3-point race residual sd; the '
                  'gain is concentrated in 2016-24 and mostly shared with the lean level. Registered as a direction test '
                  'only."')
        dep = ('dependence_note = "Senate and governor races of one state share drift and about half their error '
               '(corr 0.55): the per-race binomial is anti-conservative; the state-collapsed test below is the robustness '
               'check."')
    lines = [
        f'id = "{k["id"]}"',
        f'kind = "{k["kind"]}"',
        'status = "unregistered"',
        'origin = "analysis/us_drift.py; docs/us_drift.md. Written 2026-10-30 from data public before 2026-10-30."',
        f'weak_historical_signal = {str(weak).lower()}',
        *hyp,
        *([select] if select else []),
        "",
        "[data]",
        'poll_source = "site/data/us2026/hub/polls.json (VoteHub Polling API, CC BY 4.0), built 2026-10-29"',
        'poll_average = "v3 rule (sqrt n weight, 14-day half-life, 45-day window, 25 % pollster cap) as of 2026-11-02"',
        mcut,
        'historical_fit = "538 raw_polls 2006-2022 + 538 2024 poll files (CC BY 4.0); MEDSL certified results (CC0)"',
        f'race_universe = "the {len(r)} hub Senate/governor races below: a poll in the 45-day window, D + R >= 75 % of the poll"',
        "",
        "[measure]",
        f'name = "{x}"',
        f'description = "{LABEL[x]}"',
        formula,
        'target = "miss = certified two-party D share - poll_avg_d2 (points); relative miss = miss - mean miss of scored races"',
        pred,
        'units = "points of two-party Democratic share; positive = polls understated the Democrat"',
        f'historical_beta = {meta["beta"]:.6f}',
        f'historical_beta_ci95_cycle_bootstrap = [{meta["beta_boot_95"][0]:.6f}, {meta["beta_boot_95"][1]:.6f}]',
        f'historical_loco_residual_sd = {meta["loco_resid_sd"]:.6f}',
        f'historical_races = {meta["races_fitted"]}',
        f'historical_cycles = {meta["cycles"]}',
        f'loco_delta_rmse = {choice["d_rmse"]:.4f}',
        f'loco_delta_rmse_ci95 = [{choice["d_lo"]:.4f}, {choice["d_hi"]:.4f}]',
        f'loco_sign_agreement = {choice["agree"]:.4f}',
        f'cross_era_delta_rmse = {cross["d_rmse"]:.4f}',
        f'cross_era_delta_rmse_ci95 = [{cross["d_lo"]:.4f}, {cross["d_hi"]:.4f}]',
        f'era_2006_14_loco_delta_rmse = {e1["d_rmse"]:.4f}',
        f'era_2016_24_loco_delta_rmse = {e2["d_rmse"]:.4f}',
        *extra,
        caveat,
        "",
        "[primary]",
        'test = "sign agreement, one-sided exact binomial (H0 p = 0.5, H1 p > 0.5) over scored polled races"',
        'success = "sign(miss_points) == sign(relative miss)"',
        'ties = "exclude a race whose relative miss is exactly 0.00 points at two decimals"',
        ('scored_races = "every race below with a certified result. d2 = certified votes of the hub D-side candidate /'
         ' (D-side + hub opponent), the same pair as poll_avg_d2 (e.g. NE Senate: Osborn vs Ricketts); a race whose'
         ' pair is not on the certified ballot is dropped and the drop is reported"'),
        'report = "agreements k, signed races n, one-sided binomial p-value P(X >= k | n, 0.5)"',
        f'family = {json.dumps(FAMILY)}',
        'family_correction = "Holm"',
        'family_alpha = 0.05',
        'scored_after = "certification of every scored race"',
        f'score_command = "uv run python analysis/us_drift.py score certified.csv --measure {x}  # columns race, d2"',
        dep,
        "",
        "[secondary]",
        'tests = ["same sign test with each state counted once (majority of its races; split states dropped)",',
        '         "RMSE of miss_points versus zero against the relative miss", "coverage of the 90 % intervals"]',
    ]
    for q in r.itertuples():
        if pd.isna(q.pred):
            continue
        lines += ["", "[[predictions]]", f'race = "{q.race}"', f'direction = "{q.direction}"',
                  f'miss_points = {q.pred:.3f}', f'lo90 = {q.lo90:.3f}', f'hi90 = {q.hi90:.3f}',
                  f'{k["key"]} = {getattr(q, x):.3f}', f'poll_avg_d2 = {q.avg:.3f}', f'n_polls = {int(q.n_polls)}']
    path.write_text("\n".join(lines) + "\n")
    return path


def score_2026(pred: pd.DataFrame, certified: pd.DataFrame) -> dict:
    """pred: race, pred; certified: race, d2 (certified two-party D share, fraction or points). Relative miss = certified
    - poll average, minus its mean over the scored races; sign agreement with the prediction's sign; exact one-sided
    binomial (p0 = 0.5) over scored races with a nonzero prediction and nonzero relative miss, and the same with the
    Senate and governor race of a state counted once."""
    d = pred.merge(certified, on="race")
    d = d[d["pred"].notna()].copy()
    d["d2"] = np.where(d["d2"] <= 1.0, 100 * d["d2"], d["d2"])
    d["miss"] = d["d2"] - d["avg"]
    d["rel"] = (d["miss"] - d["miss"].mean()).round(2)
    ok = (np.sign(d["pred"]) != 0) & (np.sign(d["rel"]) != 0)
    d["agree"] = np.sign(d["pred"]) == np.sign(d["rel"])
    k, n = int(d.loc[ok, "agree"].sum()), int(ok.sum())
    by = d[ok].groupby("state")["agree"].mean()
    by = by[by != 0.5]
    kc, nc = int((by > 0.5).sum()), len(by)
    return {"scored": len(d), "signed": n, "agree": k, "p_binom": binom_p(k, n), "states": nc, "states_agree": kc,
            "p_state_binom": binom_p(kc, nc), "mean_miss": float(d["miss"].mean())}


# ------------------------------------------------------------------ report

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "panel", "tests", "predict", "score"])
    ap.add_argument("results", nargs="?", help="score: CSV with columns race, d2 (certified two-party D share)")
    ap.add_argument("--measure", default=None)
    a = ap.parse_args(argv)
    if a.step == "fetch":
        return fetch_all()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.step == "panel":
        d = build_panel()
        PUB.mkdir(exist_ok=True)
        d.round(4).to_csv(PUB / "us_state_drift_panel.csv", index=False)
        print(d.groupby(["cycle", "office"]).size().unstack().to_string())
        return
    panel = pd.read_csv(PUB / "us_state_drift_panel.csv")
    if a.step == "tests":
        res = test_all(panel)
        res.round(4).to_csv(PUB / "us_state_drift_tests.csv", index=False)
        cov = coverage(panel)
        cov.to_csv(OUT / "coverage.csv")
        era_betas(panel).to_csv(OUT / "betas.csv", index=False)
        dec = decide(res)
        dec.to_csv(OUT / "decision.csv", index=False)
        pd.DataFrame([lean_control(panel, x) for x in VOTE if x != "pres_lean"]).to_csv(
            OUT / "lean_control.csv", index=False)
        eligible = res[(res["sample"] == "all") & (res["mode"] == "loco") & res["measure"].isin(FORECAST_CANDIDATES)]
        best = eligible.sort_values(["d_rmse", "measure"]).iloc[0]
        choice = {k: (v.item() if isinstance(v, np.generic) else v) for k, v in best.items()
                  if k in {"measure", "d_rmse", "d_lo", "d_hi", "agree", "agree_lo", "agree_hi", "races", "cycles"}}
        choice["selection_rule"] = "lowest pooled leave-one-cycle-out delta RMSE among FORECAST_CANDIDATES"
        (OUT / "choice.json").write_text(json.dumps(choice, indent=1))
        print(cov.to_string(), "\n", dec.to_string(index=False), "\nchoice:", json.dumps(choice))
        return
    if a.step == "predict":
        tests = pd.read_csv(PUB / "us_state_drift_tests.csv")
        for measure in [a.measure] if a.measure else list(KINDS):
            if measure == "drift_combo":
                choice = json.loads((OUT / "choice.json").read_text())
                if choice["measure"] != measure:
                    raise ValueError(f"choice.json selects {choice['measure']}, not drift_combo")
            else:
                choice = tests[(tests["measure"] == measure) & (tests["sample"] == "all") & (tests["mode"] == "loco")
                               ].iloc[0][["d_rmse", "d_lo", "d_hi", "agree"]].to_dict()
            r, meta = predict_2026(panel, measure)
            r.round(4).to_csv(PUB / KINDS[measure]["csv"], index=False)
            (OUT / f"prediction_meta_{KINDS[measure]['key']}.json").write_text(json.dumps(meta, indent=1))
            print(write_prereg_2026(r, meta, choice, tests, pd.read_csv(OUT / "lean_control.csv")))
            print(json.dumps(meta, indent=1))
        return
    pred = pd.read_csv(PUB / KINDS[a.measure or "drift_combo"]["csv"])
    print(json.dumps(score_2026(pred, pd.read_csv(a.results)), indent=1))


if __name__ == "__main__":
    main()
