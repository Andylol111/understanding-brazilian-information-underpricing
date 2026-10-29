"""US midterms hub data (display / analysis only; registered configs untouched): site/data/us2026/hub/*.json.

  uv run python analysis/us_hub.py [polls pollsters markets backtest census]   # default: all

polls.json      every poll of each race's averaging window (VoteHub, CC BY 4.0) with its exact weight in our average,
                the polls left out and why, and the daily average over the last 60 days (as each day would have seen
                it: polls published by then).
pollsters.json  538 raw_polls 2016-2022 (CC BY 4.0), Senate/governor polls of the final 21 days: lean and RMSE by
                pollster; flag for pollsters in our 2026 averages.
markets.json    per race Polymarket daily price (CLOB prices-history; total volume from Gamma) and Kalshi daily candles
                (price + volume; © Kalshi), current spreads; Polymarket's resolved 2022 and 2024 Senate/governor
                markets: final-week price vs outcome (Brier, calibration bins).
backtest.json   our pre-count rule (poll average + v3 error bins, bins fit leaving the tested cycle out) on 2018 and
                2022 Senate/governor races (538 polls and results) vs a past-results-only baseline.
census.json     per county: 2020 estimates base and PEP vintage 2025 population and change; ACS 2020-2024 5-year
                shares (65+ from PEP 2025 age file; Bachelor's+, Hispanic, Black alone, median household income);
                state summary (statewide races are not affected by district maps).
Raw files are cached under data/raw/us (gitignored).
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import kalshi, polymarket, us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_polls  # noqa: E402

HUB = us.ROOT_DIR / "site" / "data" / "us2026" / "hub"
TODAY = pd.Timestamp.now(tz="UTC").normalize()
CRED_VOTEHUB = {"name": "VoteHub Polling API", "url": "https://votehub.com/polls/api/", "licence": "CC BY 4.0"}
CRED_538 = {"name": "FiveThirtyEight pollster ratings, raw_polls.csv",
            "url": "https://github.com/fivethirtyeight/data/tree/master/pollster-ratings", "licence": "CC BY 4.0"}


def _write(name: str, obj: dict) -> Path:
    HUB.mkdir(parents=True, exist_ok=True)
    f = HUB / name
    f.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"{f}: {f.stat().st_size / 1e6:.2f} MB")
    return f


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("university", "u").replace("college", ""))


# ------------------------------------------------------------------ polls

def _registry() -> list[dict]:
    return json.loads(us.RACES_REGISTRY.read_text())["races"]


def polls(day: str | None = None) -> Path:
    raw = us.fetch_polls(day)
    opp = {us.load_config(p)["election"]["race_id"]: us.load_config(p)["election"]["b"]["name"]
           for p in (us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")}
    created = {p["id"]: p.get("created_at") or p["end_date"] for p in raw}
    out = {}
    for r in _registry():
        dem = (r.get("dem_side") or {}).get("name")
        subj, ptype = f"2026 {r['state_name']}", us._POLL_TYPE[r["office"]]
        mine = [p for p in raw if p.get("subject") == subj and p.get("poll_type") == ptype]
        rows = us.poll_rows(raw, r["state_name"], r["office"], dem, opp.get(r["race_key"])) if dem else pd.DataFrame()
        w = us.poll_weights(rows, us.POLLS_ASOF)
        wmap = dict(zip(w["id"], w["weight"])) if len(w) else {}
        kept = set(rows["id"]) if len(rows) else set()
        listed = []
        for p in sorted(mine, key=lambda p: p["end_date"], reverse=True):
            ans = [(a["choice"], a.get("pct")) for a in p.get("answers") or []]
            d = next((x for x in ans if dem and us._same_person(x[0], dem)), None)
            if not p.get("sample_size"):
                why = "no sample size"
            elif d is None:
                why = "does not name the Democratic-side candidate"
            elif p["id"] not in kept:
                why = "another version of the same poll was used (likely voters > registered > adults; full ballot)"
            elif p["id"] not in wmap:
                age = (pd.Timestamp(us.POLLS_ASOF) - pd.Timestamp(p["end_date"])).days
                if age > us.POLL_WINDOW_DAYS + 60:      # long before the window: not listed
                    continue
                why = f"outside the {us.POLL_WINDOW_DAYS}-day window"
            else:
                why = None
            row = rows.set_index("id").loc[p["id"]] if p["id"] in kept else None
            listed.append({"id": p["id"], "pollster": p.get("pollster"), "start": p.get("start_date"),
                           "end": p["end_date"], "published": p.get("created_at"), "n": p.get("sample_size"),
                           "population": p.get("population"), "partisan": p.get("partisan"),
                           "dem": d[1] if d else None,
                           "opp": _r(row["opp"], 2) if row is not None else None,
                           "other": _r(100 - sum(a[1] or 0 for a in ans), 2) if ans else None,
                           "dem2": _r(row["s"]) if row is not None else None,
                           "weight": _r(wmap.get(p["id"])), "excluded": why, "url": p.get("url")})
        hist = []
        if len(rows):
            rows = rows.assign(created=rows["id"].map(created))
            last = min(pd.Timestamp(us.POLLS_ASOF), TODAY.tz_localize(None))
            for t in pd.date_range(last - pd.Timedelta(days=59), last):
                a = us.poll_average(rows[pd.to_datetime(rows["created"]) <= t], t.strftime("%Y-%m-%d"))
                hist.append([t.strftime("%Y-%m-%d"), a["avg"] if a else None, a["n_polls"] if a else 0])
        if listed:
            out[r["race_key"]] = {"dem": dem, "opp": opp.get(r["race_key"]), "polls": listed, "history": hist,
                                  "average": us.poll_average(rows, us.POLLS_ASOF) if len(rows) else None}
    return _write("polls.json", {
        "built": TODAY.strftime("%Y-%m-%d"), "asof": us.POLLS_ASOF, "source": CRED_VOTEHUB,
        "rule": "docs/us_midterms.md#polls; weight = sqrt(n) x 0.5^(age/14), pollster cap 25 %, normalised",
        "history_note": "average each day using only polls VoteHub had published by that day",
        "races": out})


# ------------------------------------------------------------------ pollsters

def pollsters() -> Path:
    f = us.US_RAW / "polls" / "538_raw_polls.csv"
    us._fetch(us_polls.RAW_538, f)
    d = pd.read_csv(f)
    d = d[d["type_simple"].isin(["Sen-G", "Gov-G"]) & d["cycle"].between(2016, 2022) & (d["time_to_election"] <= 21)
          & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")].drop_duplicates("poll_id")
    d["err"] = 100 * (d["cand1_pct"] / (d["cand1_pct"] + d["cand2_pct"])
                      - d["cand1_actual"] / (d["cand1_actual"] + d["cand2_actual"]))
    ours = set()
    if (HUB / "polls.json").exists():
        for r in json.loads((HUB / "polls.json").read_text())["races"].values():
            ours |= {_norm_name(p["pollster"]) for p in r["polls"] if p["weight"]}
    rows = []
    for name, g in d.groupby("pollster"):
        rows.append({"pollster": name, "polls": len(g), "races": int(g["race"].nunique()),
                     "cycles": sorted(int(c) for c in g["cycle"].unique()),
                     "lean": _r(g["err"].mean(), 2), "rmse": _r(float(np.sqrt((g["err"] ** 2).mean())), 2),
                     "in_2026": _norm_name(name) in ours})
    rows.sort(key=lambda x: -x["polls"])
    return _write("pollsters.json", {
        "source": CRED_538, "window": "Senate and governor general-election polls, final 21 days, 2016-2022",
        "units": "two-party Democratic share, points; lean = mean(poll - result), + = overstated the Democrat",
        "in_2026_rule": "pollster name (normalised) appears with weight > 0 in our 2026 averages (polls.json)",
        "pollsters": rows})


# ------------------------------------------------------------------ markets

def _pm_daily(token: str, start: pd.Timestamp, end: pd.Timestamp) -> list:
    """Daily CLOB prices (interval=max, fidelity 1 day; longer start/end ranges are refused) from `start`."""
    from brunder import _http
    with _http.client() as c:
        h = _http.get_json(c, f"{polymarket.CLOB}/prices-history",
                           {"market": token, "interval": "max", "fidelity": 1440}).get("history", [])
    s = pd.Series({pd.Timestamp(x["t"], unit="s", tz="UTC"): float(x["p"]) for x in h}).sort_index()
    s = s[(s.index >= start) & (s.index < end)]
    if s.empty:
        return []
    s = s.resample("1D").last().dropna()
    return [[t.strftime("%Y-%m-%d"), _r(v)] for t, v in s.items()]


def _pm_spread(token: str) -> dict | None:
    b = polymarket.book(token)
    bid = max((float(x["price"]) for x in b.get("bids") or []), default=None)
    ask = min((float(x["price"]) for x in b.get("asks") or []), default=None)
    return {"bid": bid, "ask": ask, "spread": _r(ask - bid) if bid is not None and ask is not None else None}


def _accuracy() -> dict:
    """Resolved Polymarket Senate/governor markets 2022 and 2024: mean daily price of the Democratic side over the
    final 7 days before Election Day vs the outcome."""
    rows = []
    ev = polymarket.event("2022-us-senate-elections-will-a-dem-or-rep-win-this-state")
    for _, m in polymarket.markets(ev).iterrows():
        rows.append(("sen", 2022, m["label"].split(" - ")[0].strip(), m["slug"], m["yes_token"], m["last_prices"][0]))
    names = sorted({r["state_name"] for r in _registry()} | {"Arizona", "Nevada", "Montana", "Pennsylvania",
                                                               "Wisconsin", "Michigan", "Ohio"})
    for office, suffix in (("sen", "us-senate-election-winner"), ("gov", "governor-election-winner")):
        for st in names:
            slug = f"{st.lower().replace(' ', '-')}-{suffix}"
            try:
                e = polymarket.event(slug)
            except Exception:
                continue
            if not e.get("closed") or not str(e.get("endDate", "")).startswith("2024"):
                continue
            m = polymarket.markets(e)
            dm = m[m["question"].str.contains("Democrat", case=False)]
            if len(dm):
                x = dm.iloc[0]
                rows.append((office, 2024, st, x["slug"], x["yes_token"], x["last_prices"][0]))
    out = []
    for office, year, st, slug, tok, final in rows:
        ed = pd.Timestamp("2022-11-08" if year == 2022 else "2024-11-05", tz="UTC")
        try:
            h = polymarket.price_history(tok, ed - pd.Timedelta(days=8), ed, fidelity_min=60,
                                         chunk=pd.Timedelta(days=9))
        except Exception as e:
            print(f"accuracy {slug}: {e}")
            continue
        if h.empty or final is None:
            continue
        p = float(h["price"].astype(float).mean())
        won = float(final > 0.5)
        out.append({"office": office, "year": year, "state": st, "slug": slug, "p_final_week": _r(p), "dem_won": won})
    df = pd.DataFrame(out)
    bins = []
    if len(df):
        df["bin"] = pd.cut(df["p_final_week"], np.linspace(0, 1, 11), include_lowest=True)
        for b, g in df.groupby("bin", observed=True):
            bins.append({"bin": [_r(b.left, 2), _r(b.right, 2)], "n": len(g), "mean_p": _r(g["p_final_week"].mean()),
                         "freq": _r(g["dem_won"].mean())})
    return {"markets": out, "n": len(out), "brier": _r(float(((df["p_final_week"] - df["dem_won"]) ** 2).mean()))
            if len(df) else None, "bins": bins,
            "coverage": "2024: <state>-us-senate-election-winner and <state>-governor-election-winner events found by "
                        "slug (closed, ended 2024). 2022: Polymarket's 14-state Senate event exists but the CLOB "
                        "returns no price history for it (pre-CLOB markets), so 2022 is not scored."}


def markets() -> Path:
    out = {}
    for r in _registry():
        e: dict = {"pm": None, "kalshi": None}
        pm = r.get("pm") or {}
        if pm.get("token_id"):
            try:
                ev = polymarket.event(pm["event"])
                m = next((m for m in ev["markets"] if m["slug"] == pm["slug"]), {})
                start = max(pd.Timestamp(m.get("createdAt") or m.get("startDate") or TODAY - pd.Timedelta(days=90)),
                            TODAY - pd.Timedelta(days=90))
                e["pm"] = {"slug": pm["slug"], "volume_total": _r(float(m.get("volume") or 0), 0),
                           "daily": _pm_daily(pm["token_id"], start, TODAY + pd.Timedelta(days=1)),
                           **(_pm_spread(pm["token_id"]) or {})}
            except Exception as ex:
                print(f"{r['race_key']} polymarket: {type(ex).__name__}: {ex}")
        k = r.get("kalshi") or {}
        if k.get("ticker"):
            try:
                c = kalshi.candlesticks(k["series"], k["ticker"], TODAY - pd.Timedelta(days=90), TODAY, 1440)
                ob = kalshi.orderbook(k["ticker"])
                bid = ob["yes_bids"][0][0] if ob["yes_bids"] else None
                ask = ob["yes_asks"][0][0] if ob["yes_asks"] else None
                mid = c.apply(lambda x: (x["yes_bid"] + x["yes_ask"]) / 2
                              if pd.notna(x["yes_bid"]) and pd.notna(x["yes_ask"]) else x["price"], axis=1) \
                    if len(c) else pd.Series(dtype=float)
                e["kalshi"] = {"ticker": k["ticker"],
                               "daily": [[t.strftime("%Y-%m-%d"), _r(p), _r(v, 0)]
                                         for t, p, v in zip(c["time"], mid, c["volume"])],
                               "bid": bid, "ask": ask, "spread": _r(ask - bid) if bid is not None and ask is not None
                               else None}
            except Exception as ex:
                print(f"{r['race_key']} kalshi: {type(ex).__name__}: {ex}")
        out[r["race_key"]] = e
    return _write("markets.json", {
        "built": TODAY.strftime("%Y-%m-%d"),
        "sources": [{"name": "Polymarket CLOB prices-history (daily) and Gamma market volume (total)",
                     "url": "https://polymarket.com", "licence": "public market data"},
                    {"name": "© Kalshi: daily candlesticks (bid/ask midpoint, else last trade; volume in contracts)",
                     "url": "https://kalshi.com", "licence": "© Kalshi (see docs/kalshi.md)"}],
        "note": "price = P(Democratic side YES). Polymarket publishes no daily volume: total volume only.",
        "races": out, "accuracy": _accuracy()})


# ------------------------------------------------------------------ backtest (pre-count only)

def backtest() -> Path:
    f = us.US_RAW / "polls" / "538_raw_polls.csv"
    us._fetch(us_polls.RAW_538, f)
    d = pd.read_csv(f)
    hist = us_polls.history_averages()
    pres = d[d["type_simple"] == "Pres-G"].drop_duplicates("race")
    pres = pres.assign(share=pres["cand1_actual"] / (pres["cand1_actual"] + pres["cand2_actual"]))
    pres_by = {(int(c), loc): s for c, loc, s in zip(pres["cycle"], pres["location"], pres["share"])}
    rows = []
    for cyc, prev in ((2018, 2016), (2022, 2020)):
        bins = us.poll_sd_bins(hist[hist["cycle"] != cyc])             # leave the tested cycle out
        for race, g in d[d["type_simple"].isin(["Sen-G", "Gov-G"]) & (d["cycle"] == cyc)
                         & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")].groupby("race"):
            g = g.drop_duplicates("poll_id")
            pr = pd.DataFrame({"pollster": g["pollster"], "end": g["polldate"], "n": g["samplesize"],
                               "dem": g["cand1_pct"], "opp": g["cand2_pct"],
                               "s": g["cand1_pct"] / (g["cand1_pct"] + g["cand2_pct"])}).dropna().reset_index(drop=True)
            a = us.poll_average(pr, (pd.Timestamp(g["electiondate"].iloc[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
            loc = g["location"].iloc[0]
            if not a or (prev, loc) not in pres_by:
                continue
            act = g["cand1_actual"].iloc[0] / (g["cand1_actual"].iloc[0] + g["cand2_actual"].iloc[0])
            sd = us.poll_sd_by_n(a["n_polls"], bins)
            b = pres_by[(prev, loc)]
            rows.append({"race": race, "cycle": cyc, "state": loc, "office": g["type_simple"].iloc[0][:3].lower(),
                         "n_polls": a["n_polls"], "pred_margin": _r(200 * (a["avg"] - 0.5), 2),
                         "sd_margin": _r(200 * sd, 2), "P": _r(norm.cdf((a["avg"] - 0.5) / sd)),
                         "actual_margin": _r(200 * (act - 0.5), 2), "error": _r(200 * (a["avg"] - act), 2),
                         "base_margin": _r(200 * (b - 0.5), 2), "base_P": _r(norm.cdf((b - 0.5) / us.EST_SD_NOPOLL)),
                         "dem_won": bool(act > 0.5)})
    df = pd.DataFrame(rows)

    def summ(pm, ps, pp):
        z = (df[pm] - df["actual_margin"]) / ps
        won = df["dem_won"].astype(float)
        p = df[pp].clip(1e-6, 1 - 1e-6)
        cal = []
        for lo in np.linspace(0, 0.9, 10):
            g = df[(df[pp] >= lo) & (df[pp] < lo + 0.1 + (1e-9 if lo >= 0.9 else 0))]
            if len(g):
                cal.append({"bin": [_r(lo, 1), _r(lo + 0.1, 1)], "n": len(g), "mean_p": _r(g[pp].mean()),
                            "freq": _r(g["dem_won"].mean())})
        return {"races": len(df), "cover68": _r(np.mean(np.abs(z) < 1)), "cover90": _r(np.mean(np.abs(z) < 1.645)),
                "mae_margin": _r(np.abs(df[pm] - df["actual_margin"]).mean(), 2),
                "brier": _r(((df[pp] - won) ** 2).mean()),
                "log_loss": _r(-np.mean(np.log(np.where(won == 1, p, 1 - p)))), "calibration": cal}
    return _write("backtest.json", {
        "label": "pre-count only: our poll-average rule + v3 error bins (fit without the tested cycle) on the day "
                 "before the election; no count-over-time data exist for these years",
        "sources": [CRED_538, {"name": "results: FiveThirtyEight raw_polls.csv cand1_actual/cand2_actual (official "
                                       "returns)", "url": CRED_538["url"], "licence": "CC BY 4.0"}],
        "baseline": f"past results only: the state's previous presidential two-party share, sd {us.EST_SD_NOPOLL}",
        "races": rows,
        "summary": {"model": summ("pred_margin", df["sd_margin"], "P"),
                    "baseline": summ("base_margin", 200 * us.EST_SD_NOPOLL, "base_P")}})


# ------------------------------------------------------------------ census

def _acs(table: str, cols: list[str]) -> pd.DataFrame:
    f = us.US_RAW / "census" / f"acsdt5y2024-{table}.dat"
    us._fetch("https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
              f"acsdt5y2024-{table}.dat", f)
    parts = []
    for ch in pd.read_csv(f, sep="|", dtype=str, usecols=["GEO_ID"] + cols, chunksize=500_000):
        parts.append(ch[ch["GEO_ID"].str.startswith("0500000US")])
    d = pd.concat(parts)
    d.index = d.pop("GEO_ID").str[-5:]
    return d.apply(pd.to_numeric, errors="coerce")


def census() -> Path:
    base = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties"
    pep = pd.read_csv(io.BytesIO(us._fetch(f"{base}/totals/co-est2025-alldata.csv",
                                           us.US_RAW / "census" / "co-est2025-alldata.csv")), dtype=str, encoding="latin1")
    pep[["ESTIMATESBASE2020", "POPESTIMATE2025"]] = pep[["ESTIMATESBASE2020", "POPESTIMATE2025"]].astype(int)
    cty = pep[pep["SUMLEV"] == "050"].assign(fips=lambda x: x["STATE"] + x["COUNTY"]).set_index("fips")
    sts = pep[pep["SUMLEV"] == "040"].set_index("STATE")
    age = pd.read_csv(io.BytesIO(us._fetch(f"{base}/asrh/cc-est2025-agesex-all.csv",
                                           us.US_RAW / "census" / "cc-est2025-agesex-all.csv")), dtype=str,
                      encoding="latin1")
    age = age[age["YEAR"] == age["YEAR"].astype(int).max().__str__()].assign(fips=lambda x: x["STATE"] + x["COUNTY"])
    age = age.set_index("fips")[["AGE65PLUS_TOT", "POPESTIMATE"]].astype(int)
    inc = _acs("b19013", ["B19013_E001"])
    his = _acs("b03003", ["B03003_E001", "B03003_E003"])
    blk = _acs("b02001", ["B02001_E001", "B02001_E003"])
    edu = _acs("b15003", ["B15003_E001", "B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"])
    st_code = {uid.split("-")[2][:2]: uid.split("-")[1] for uid, _ in us._geo()}
    counties = {}
    for uid, name in us._geo():
        f = uid.split("-")[2]
        if f not in cty.index:
            continue
        b20, p25 = int(cty.at[f, "ESTIMATESBASE2020"]), int(cty.at[f, "POPESTIMATE2025"])
        e = edu.loc[f] if f in edu.index else None
        counties[uid] = {
            "pop2020": b20, "pop2025": p25, "change_pct": _r(100 * (p25 / b20 - 1), 2) if b20 else None,
            "age65_pct": _r(100 * age.at[f, "AGE65PLUS_TOT"] / age.at[f, "POPESTIMATE"], 1) if f in age.index else None,
            "ba_plus_pct": _r(100 * e[["B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"]].sum()
                              / e["B15003_E001"], 1) if e is not None and e["B15003_E001"] else None,
            "hispanic_pct": _r(100 * his.at[f, "B03003_E003"] / his.at[f, "B03003_E001"], 1) if f in his.index else None,
            "black_pct": _r(100 * blk.at[f, "B02001_E003"] / blk.at[f, "B02001_E001"], 1) if f in blk.index else None,
            "median_income": None if f not in inc.index or pd.isna(inc.at[f, "B19013_E001"])
            else int(inc.at[f, "B19013_E001"])}
    states = {}
    for code, s in sts.iterrows():
        st = st_code.get(code)
        if not st:
            continue
        b20, p25 = int(s["ESTIMATESBASE2020"]), int(s["POPESTIMATE2025"])
        states[st] = {"name": s["STNAME"], "pop2020": b20, "pop2025": p25, "change_pct": _r(100 * (p25 / b20 - 1), 2),
                      "statewide_races_affected_by_maps": False}
    return _write("census.json", {
        "sources": [{"name": "US Census Bureau, Population Estimates Program, vintage 2025 (county totals; age by sex "
                              "for 65+)", "url": "https://www.census.gov/programs-surveys/popest.html",
                     "licence": "public domain"},
                    {"name": "US Census Bureau, American Community Survey 2020-2024 5-year, tables B19013, B03003, "
                              "B02001, B15003 (summary file)", "url": "https://www.census.gov/programs-surveys/acs",
                     "licence": "public domain"}],
        "fields": {"pop2020": "April 1, 2020 estimates base (Census 2020 based)", "pop2025": "July 1, 2025 estimate",
                   "age65_pct": "PEP 2025", "ba_plus_pct": "population 25+ with a bachelor's degree or higher",
                   "hispanic_pct": "Hispanic or Latino, any race", "black_pct": "Black or African American alone",
                   "median_income": "median household income, 2024 dollars"},
        "redistricting": "Senate and governor races are elected statewide: district maps (including any mid-decade "
                         "House redistricting since 2022) do not affect them. No reapportionment since the 2020 "
                         "census (the next follows 2030). Population change per state is shown for context only.",
        "counties": counties, "states": states})


STEPS = {"polls": polls, "pollsters": pollsters, "markets": markets, "backtest": backtest, "census": census}

if __name__ == "__main__":
    for s in sys.argv[1:] or list(STEPS):
        STEPS[s]()
