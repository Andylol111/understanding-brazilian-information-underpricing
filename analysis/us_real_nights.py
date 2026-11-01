"""Real (not simulated) US count nights: archived, timestamped county returns -> the snapshot format of the count replay.

Sources (all read from archived copies or public repositories; NO request ever goes to a live election-night host):
  nyt2020   NYT/Edison 2020 state pages (static01.nyt.com ... 2020-11-03/state-page/<state>.json) as saved by the Internet Archive
            (Senate / governor / president county returns, `last_updated` per county, absentee split), and the president-only
            county table of github.com/d0ntw84f8/election2020-timeseries-data (MIT licence on the code).
  nyt2022   NYT 2022 state / race pages as saved by the Internet Archive (county returns with `provider_updated_at`).
  nyt2024   NYT 2024 `pres|sen|gov-county-data` feeds as saved by the Internet Archive (county returns, `advance` split).
  mi2022    Michigan Secretary of State `2022GEN_MI_CENR_BY_COUNTY` files as saved by the Internet Archive (official, public records).
Terms: the NYT/Edison material is third-party copyrighted; it is kept on the SSD only ($BRUNDER_DATA/raw/us/real_nights/…, with a
LICENCE.txt), used for INTERNAL VALIDATION and never committed or published, and nothing derived from it is published.
Archive.org: honest UA, one request per 1.2 s, back-off on 429/5xx/connection errors, CDX API for the listings.

  uv run python analysis/us_real_nights.py fetch  [--only nyt2020 nyt2022 nyt2024 mi2022]   # raw copies -> SSD
  uv run python analysis/us_real_nights.py build                                              # snapshots (parquet) -> SSD
  uv run python analysis/us_real_nights.py replay [--nights …]                                # v1 / v3 / unified replay -> SSD
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import tomllib
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_unified_backtest as bt                     # LOCO unified models: panel, share / turnout / state-vote fits
from brunder import us
from brunder import us_unified as uu
from brunder.paths import DATA, PROCESSED

UA = "brunder/0.1 (research; recorder)"
RAW = us.US_RAW / "real_nights"
GAP = 1.2                                   # seconds between requests to archive.org (rule: <= 1 req/s)

# ------------------------------------------------------------------ archive.org client


class Wayback:
    """Rate-limited, retrying client for web.archive.org (CDX listings and `id_` raw captures). Original URLs are only ever
    passed to the archive as a path component; nothing here connects to the original host."""

    def __init__(self, gap: float = GAP):
        self.gap, self.t = gap, 0.0
        self.http = httpx.Client(headers={"User-Agent": UA}, timeout=90, follow_redirects=True)

    def _wait(self) -> None:
        d = self.t + self.gap - time.monotonic()
        if d > 0:
            time.sleep(d)
        self.t = time.monotonic()

    def get(self, url: str, params: dict | None = None, tries: int = 7) -> httpx.Response | None:
        assert url.startswith("https://web.archive.org/"), url
        for k in range(tries):
            self._wait()
            try:
                r = self.http.get(url, params=params)
                if r.status_code == 200:
                    return r
                if r.status_code in (404, 403, 410):
                    return None
                err = f"HTTP {r.status_code}"
            except (httpx.TransportError, httpx.TimeoutException) as e:
                err = type(e).__name__
            wait = min(15 * 2 ** k, 240)
            print(f"  retry {k + 1}/{tries} ({err}) in {wait}s: {url[-90:]}", flush=True)
            time.sleep(wait)
        return None

    def cdx(self, url: str, lo: str, hi: str, match: str = "exact", limit: int = 5000, ok_only: bool = True) -> list[tuple[str, str, int]]:
        """[(timestamp, original url, length)] of the captures of `url` between lo and hi (YYYYMMDD[hhmmss])."""
        p = {"url": url, "matchType": match, "output": "json", "fl": "timestamp,original,statuscode,length", "limit": str(limit),
             "from": lo, "to": hi}
        if ok_only:
            p["filter"] = "statuscode:200"
        r = self.get("https://web.archive.org/cdx/search/cdx", p)
        if r is None:
            raise RuntimeError(f"CDX query failed: {url}")
        rows = json.loads(r.text)[1:] if r.text.strip() else []
        return [(t, o, int(n)) for t, o, _, n in rows]

    def capture(self, ts: str, url: str, dest: Path) -> bool:
        """Raw bytes of the capture (`id_`: no archive rewriting) -> dest (gzip when it ends with .gz). True when stored."""
        if dest.exists() and dest.stat().st_size > 0:
            return True
        r = self.get(f"https://web.archive.org/web/{ts}id_/{url}")
        if r is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(gzip.compress(r.content, 6) if dest.suffix == ".gz" else r.content)
        tmp.rename(dest)
        return True


def write_licence(d: Path, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "LICENCE.txt").write_text(text)


NYT_LICENCE = """New York Times / Edison Research (National Election Pool) election-night results data, as preserved by the Internet Archive
(web.archive.org, `id_` raw captures) or by a third-party public repository. Copyright The New York Times Company / Edison Research;
the NYT terms of service restrict copying, scraping and redistribution. INTERNAL VALIDATION ONLY: keep on this SSD, never commit,
never publish, and publish nothing derived from it (counts, shares, error tables, plots). Underlying vote counts are public
records of the counties and states; this copy is not the official record.
"""

# ------------------------------------------------------------------ fetchers

NYT24 = "static01.nyt.com/elections-assets/pages/data/feeds"
FEEDS24 = {"sen": "40e74e91-42d6-4ff7-b4c3-d499273f7f04/sen-county-data",
           "gov": "996f66fc-5690-46c0-9ec6-6dbf1cacdb94/gov-county-data",
           "pres": "96c3e55a-4c13-4585-b5f6-78f9de0ec96e/pres-county-data"}
STATES_SLUG = {"ga": "georgia", "az": "arizona", "nv": "nevada", "pa": "pennsylvania", "wi": "wisconsin", "mi": "michigan",
               "nc": "north-carolina", "oh": "ohio", "fl": "florida", "tx": "texas"}


def fetch_nyt2024(wb: Wayback) -> None:
    write_licence(RAW / "nyt2024_feeds", NYT_LICENCE)
    for k, path in FEEDS24.items():
        url = f"https://{NYT24}/{path}/2024-11-05.json"
        caps = wb.cdx(url.removeprefix("https://"), "20241105", "20241112")
        print(f"nyt2024 {k}: {len(caps)} captures", flush=True)
        for i, (ts, orig, _) in enumerate(caps):
            ok = wb.capture(ts, orig, RAW / "nyt2024_feeds" / k / f"{ts}.json.gz")
            if i % 20 == 0 or not ok:
                print(f"  {k} {i + 1}/{len(caps)} {ts} {'ok' if ok else 'FAILED'}", flush=True)


def fetch_nyt2020(wb: Wayback, states=("ga", "az", "mi", "nc", "tx")) -> None:
    write_licence(RAW / "nyt2020_statepage", NYT_LICENCE)
    for st in states:
        url = f"static01.nyt.com/elections-assets/2020/data/api/2020-11-03/state-page/{STATES_SLUG[st]}.json"
        caps = wb.cdx(url, "20201103", "20201109")
        print(f"nyt2020 {st}: {len(caps)} captures", flush=True)
        for i, (ts, orig, _) in enumerate(caps):
            ok = wb.capture(ts, orig, RAW / "nyt2020_statepage" / st / f"{ts}.json.gz")
            if i % 20 == 0 or not ok:
                print(f"  {st} {i + 1}/{len(caps)} {ts} {'ok' if ok else 'FAILED'}", flush=True)



# ------------------------------------------------------------------ snapshot format
#
# One night = one race on one election night: `snapshots` DataFrame with one row per (ts, county):
#   ts (UTC, when the source published that frame), fips, d, r (cumulative Dem / Rep votes), tot (all candidates),
#   exp (the source's expected county vote, informational only), ed, er (early / absentee-advance Dem, Rep votes; NaN
#   when the source has no split), rep, units (precincts reporting / total; NaN when absent).
# The replay turns a frame into the feed rows brunder.us.project reads (same columns as analysis/us_calibrate.rows_at).

SNAP_COLS = ["ts", "fips", "d", "r", "tot", "exp", "ed", "er", "rep", "units"]
PROC = PROCESSED / "us_real_nights"                # snapshot parquet files (SSD)
OUT = DATA / "derived" / "us_real_nights"          # replay outputs (SSD; internal validation only)


def _party(p: str | None) -> str | None:
    p = (p or "").lower()
    return {"democrat": "d", "dem": "d", "republican": "r", "gop": "r", "rep": "r"}.get(p)


def _read_json(path: Path) -> dict | None:
    """The capture's JSON; None when the archived copy is truncated or not JSON (a few 2024 captures are cut mid-string)."""
    b = path.read_bytes()
    try:
        d = json.loads(gzip.decompress(b) if path.suffix == ".gz" else b)
        return json.loads(d) if isinstance(d, str) else d
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def parse_nyt2020_race(page: dict, race_id: str) -> tuple[pd.Timestamp, pd.DataFrame] | None:
    """One 2020 state-page capture -> (page time, county frame) of `race_id` (e.g. GA-G-S-2020-11-03); None if absent."""
    races = page.get("data", page)["races"]
    race = next((x for x in races if x["race_id"] == race_id), None)
    if race is None or not race.get("counties"):
        return None
    key = {}
    for c in race["candidates"]:
        k = _party(c["party_id"])
        if k:
            key[k] = c["candidate_key"]
    rows = []
    for c in race["counties"]:
        res, ab = c.get("results") or {}, c.get("results_absentee") or {}
        rows.append({"fips": str(c["fips"]).zfill(5), "d": res.get(key["d"], 0), "r": res.get(key["r"], 0), "tot": c.get("votes", 0),
                     "exp": c.get("tot_exp_vote"), "ed": ab.get(key["d"]) if ab else None, "er": ab.get(key["r"]) if ab else None,
                     "rep": c.get("reporting"), "units": c.get("precincts")})
    return pd.Timestamp(race["last_updated"]), pd.DataFrame(rows)


def parse_nyt_race(race: dict, when: str | None = None) -> tuple[pd.Timestamp, pd.DataFrame] | None:
    """One race object of the 2022 pages / 2024 feeds -> (time, county frame). Dem/Rep from `candidate_metadata`."""
    units = [u for u in race.get("reporting_units") or [] if u.get("level") == "county"]
    if not units:
        return None
    meta = race.get("candidate_metadata") or {}
    party = {k: _party((v.get("party") or {}).get("nyt_id")) for k, v in meta.items()}
    rows = []
    for u in units:
        v = {"d": 0, "r": 0, "ed": None, "er": None}
        for c in u["candidates"]:
            k = party.get(c["nyt_id"])
            if k:
                v[k] = c["votes"]["total"] or 0
                if "advance" in c["votes"]:
                    v["e" + k] = c["votes"]["advance"]
        rows.append({"fips": f'{u["fips_state"]}{u["fips_county"]}', **v, "tot": u.get("total_votes", 0), "exp": u.get("total_expected_vote"),
                     "rep": u.get("precincts_reporting"), "units": u.get("precincts_total")})
    ts = when or race.get("updated_at")
    return pd.Timestamp(ts), pd.DataFrame(rows)


def _finish(frames: list[tuple[pd.Timestamp, pd.DataFrame]], valid: set[str]) -> pd.DataFrame:
    out = []
    for ts, f in frames:
        f = f[f["fips"].isin(valid)].copy()
        f.insert(0, "ts", ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC"))
        out.append(f)
    d = pd.concat(out, ignore_index=True).reindex(columns=SNAP_COLS)
    for c in ("d", "r", "tot", "exp", "ed", "er", "rep", "units"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[SNAP_COLS].drop_duplicates(["ts", "fips"]).sort_values(["ts", "fips"], ignore_index=True)


def snapshots_nyt2020_pres(st: str) -> pd.DataFrame:
    """President, county frames of the d0ntw84f8 table (NYT/Edison copies on the Internet Archive; one frame per file stamp)."""
    d = pd.read_csv(RAW / "d0ntw_nyt2020" / "county_presidential.csv", index_col=0, dtype={"fips": str})
    d = d[d["state"] == st.upper()].copy()
    d["fips"] = d["fips"].str.zfill(5)
    d["ts"] = pd.to_datetime(d["timestamp"], utc=True)
    out = d.rename(columns={"votes_dem": "d", "votes_rep": "r", "votes2020": "tot", "absentee_dem": "ed", "absentee_rep": "er"})
    out["exp"], out["rep"], out["units"] = np.nan, np.nan, np.nan
    return out[SNAP_COLS].drop_duplicates(["ts", "fips"]).sort_values(["ts", "fips"], ignore_index=True)


def snapshots_nyt2020_page(st: str, race_id: str, slug: str | None = None) -> pd.DataFrame:
    """A 2020 race from the state-page captures and, when given, the race-page captures (`race-page/<state>/<slug>.json`, same layout)."""
    frames = []
    files = sorted((RAW / "nyt2020_statepage" / st).glob("*.json.gz"))
    if slug:
        files += sorted((RAW / "nyt2020_racepage" / st / slug).glob("*.json.gz"))
    for f in files:
        page = _read_json(f)
        r = parse_nyt2020_race(page, race_id) if page else None
        if r:
            frames.append(r)
    valid = set(us_unified_ids(st))
    return _finish(frames, valid)


def snapshots_nyt2024(kind: str, st: str) -> pd.DataFrame:
    """2024 feeds: `kind` pres | sen | gov; one frame per capture of the race `<ST>-G-<P|S|G>-2024-11-05`."""
    oid = {"pres": "P", "sen": "S", "gov": "G"}[kind]
    nid = f"{st.upper()}-G-{oid}-2024-11-05"
    frames = []
    for f in sorted((RAW / "nyt2024_feeds" / kind).glob("*.json.gz")):
        page = _read_json(f)
        for race in (page or {}).get("data", []):
            if race["nyt_id"] == nid:
                r = parse_nyt_race(race)
                if r:
                    frames.append(r)
    return _finish(frames, set(us_unified_ids(st)))


def snapshots_nyt2022(st: str, race: str) -> pd.DataFrame:
    """2022 race pages (race = us-senate | governor): one frame per capture."""
    frames = []
    for f in sorted((RAW / "nyt2022_pages" / st / race).glob("*.json.gz")):
        page = _read_json(f)
        r = parse_nyt_race(page["races"][0]) if page and page.get("races") else None
        if r:
            frames.append(r)
    return _finish(frames, set(us_unified_ids(st))) if frames else pd.DataFrame(columns=SNAP_COLS)


def snapshots_mi2022() -> pd.DataFrame:
    """Michigan Department of State county file (2022GEN_MI_CENR_BY_COUNTY), governor, one frame per archive capture (time = capture time)."""
    key = us.counties("mi").assign(fips=lambda d: d["unit_id"].str[-5:]).set_index("key")["fips"]
    frames = []
    for f in sorted((RAW / "mi2022_official").glob("*.tsv")):
        t = pd.read_csv(f, sep="\t", dtype=str, encoding="latin-1", on_bad_lines="skip")
        t = t[t["OfficeDescription"].str.upper().str.startswith("GOVERNOR 4 YEAR", na=False)]
        t["votes"] = pd.to_numeric(t["CandidateVotes"], errors="coerce").fillna(0)
        t["fips"] = t["CountyName"].map(lambda n: key.get(us._norm(n)))
        g = t.pivot_table(index="fips", columns="PartyName", values="votes", aggfunc="sum", fill_value=0)
        ts = pd.to_datetime(f.stem, format="%Y%m%d%H%M%S", utc=True)
        frames.append((ts, pd.DataFrame({"fips": g.index, "d": g.get("DEM", 0).to_numpy(), "r": g.get("REP", 0).to_numpy(), "tot": g.sum(axis=1).to_numpy()})))
    return _finish(frames, set(us_unified_ids("mi"))) if frames else pd.DataFrame(columns=SNAP_COLS)


def _asof_matrix(f: pd.DataFrame, times: list, fips: list[str], col: str) -> np.ndarray:
    """Rows = `times`, columns = `fips`: the county's value in the last frame at or before each time (0 before the first frame)."""
    pv = f.pivot_table(index="ts", columns="fips", values=col, aggfunc="last").reindex(columns=fips).sort_index()
    return pv.reindex(pv.index.union(times)).ffill().fillna(0.0).loc[times].to_numpy(float)


def interpolate_race(sen: pd.DataFrame, pres: pd.DataFrame, extra: list | None = None) -> pd.DataFrame:
    """Dense frames of a race that was captured only a handful of times, on the clock of a densely captured race of the same night.

    Between two real captures j < j+1 of the sparse race, each county's cumulative Dem / Rep (and early) votes move from their value
    at j to their value at j+1 in proportion to that county's progress in the dense race (president: two-party votes counted at t
    minus at j, over the same difference to j+1); before the first capture the move starts from zero. At every real capture time
    the real values are reproduced exactly; precinct counts come from the dense race. This is a timing reconstruction, not a
    recording: the in-between mix of batches (early / Election Day) is assumed linear in the president's progress."""
    fips = sorted(set(sen["fips"]) & set(pres["fips"]))
    ks = sorted(sen["ts"].unique())
    pt = sorted(pres["ts"].unique())
    T = sorted(set(pt) | set(ks) | set(extra or []))
    pres = pres.assign(p=pres["d"] + pres["r"])
    Pt = _asof_matrix(pres, T, fips, "p")
    kidx = [T.index(k) for k in ks]
    PK = Pt[kidx]

    def knots(col):
        pv = sen.pivot_table(index="ts", columns="fips", values=col, aggfunc="last").reindex(index=ks, columns=fips)
        return pv.to_numpy(float)

    cols = {c: knots(c) for c in ("d", "r", "tot", "ed", "er")}
    tsec = np.array([t.value for t in T], float)
    ksec = np.array([k.value for k in ks], float)
    out = {c: np.zeros((len(T), len(fips))) for c in cols}
    for i, t in enumerate(T):
        j = int(np.searchsorted(ksec, tsec[i], side="right") - 1)             # last real capture at or before t
        if j >= len(ks) - 1 and tsec[i] >= ksec[-1]:
            for c, K in cols.items():
                out[c][i] = K[-1]
            continue
        lo_p = PK[j] if j >= 0 else np.zeros(len(fips))
        den = PK[j + 1] - lo_p
        t_lo = ksec[j] if j >= 0 else tsec[0]
        w_time = np.clip((tsec[i] - t_lo) / max(ksec[j + 1] - t_lo, 1.0), 0, 1)
        frac = np.where(den > 0, np.clip((Pt[i] - lo_p) / np.where(den > 0, den, 1.0), 0, 1), w_time)
        for c, K in cols.items():
            a = K[j] if j >= 0 else np.where(np.isnan(K[j + 1]), np.nan, 0.0)
            out[c][i] = a + frac * (K[j + 1] - a)
    rep = _asof_matrix(pres.assign(rep=pres["rep"].fillna(0)), T, fips, "rep")
    units = _asof_matrix(pres.assign(units=pres["units"].fillna(0)), T, fips, "units")
    frames = []
    kset = set(ks)
    for i, t in enumerate(T):
        frames.append(pd.DataFrame({"ts": t, "fips": fips, "d": out["d"][i], "r": out["r"][i], "tot": out["tot"][i], "exp": np.nan,
                                    "ed": out["ed"][i], "er": out["er"][i], "rep": np.where(units[i] > 0, rep[i], np.nan),
                                    "units": np.where(units[i] > 0, units[i], np.nan), "real": t in kset}))
    return pd.concat(frames, ignore_index=True)[SNAP_COLS + ["real"]]


def holdout_knots(sen: pd.DataFrame, pres: pd.DataFrame, v2: float) -> pd.DataFrame:
    """Leave-one-capture-out check of `interpolate_race`: rebuild each interior real capture from its neighbours and the dense race
    and compare statewide Dem two-party share and counted share with the real capture."""
    ks = sorted(sen["ts"].unique())
    first = next((j for j, k in enumerate(ks) if (sen.loc[sen["ts"] == k, "d"] + sen.loc[sen["ts"] == k, "r"]).sum() > 0), None)
    rows = []
    for j in range(1, len(ks) - 1):
        keep = sen[sen["ts"] != ks[j]]
        f = interpolate_race(keep, pres, extra=[ks[j]])
        g = f[f["ts"] == ks[j]]
        real = sen[sen["ts"] == ks[j]]
        rd, rr, gd, gr = real["d"].sum(), real["r"].sum(), g["d"].sum(), g["r"].sum()
        if rd + rr > 0 and gd + gr > 0:
            rows.append({"ts": ks[j], "which": "first capture (mix assumed constant before it)" if j == first else "interior", "counted_real": (rd + rr) / v2, "counted_interp": (gd + gr) / v2,
                         "share_err_pts": 100 * (gd / (gd + gr) - rd / (rd + rr))})
    return pd.DataFrame(rows)


def us_unified_ids(st: str) -> list[str]:
    return [u[-5:] for u in us.counties(st)["unit_id"]]




# ------------------------------------------------------------------ nights

ROOT = uu.ROOT
OFFICE_ID = {"pres": "P", "sen": "S", "gov": "G"}
# source, kind: where the snapshots of a night come from. NYT = NYT/Edison copy (internal validation only); OFFICIAL = state
# publication saved by the archive (publishable as derived statistics).
NYT, OFFICIAL = "nyt", "official"
TARGET = ("ga", "pa", "az", "nv", "wi", "mi", "nc", "oh", "fl", "tx")


def night_specs() -> list[dict]:
    """Every race-night with raw copies on the SSD: dict(key, t, st, office, special, source, kind, loader)."""
    out = []
    for st in TARGET:                                              # 2020 president: the d0ntw county table (all ten states)
        out.append(dict(t=2020, st=st, office="pres", special=False, source="nyt2020_county_table", kind=NYT,
                        loader=lambda st=st: snapshots_nyt2020_pres(st)))
    for st, rid, office, special in (("ga", "GA-G-S-2020-11-03", "sen", False), ("az", "AZ-S-S-2020-11-03", "sen", True),
                                     ("mi", "MI-G-S-2020-11-03", "sen", False), ("nc", "NC-G-S-2020-11-03", "sen", False),
                                     ("nc", "NC-G-G-2020-11-03", "gov", False), ("tx", "TX-G-S-2020-11-03", "sen", False)):
        if (RAW / "nyt2020_statepage" / st).exists():
            slug = {"sen": "senate", "gov": "governor"}[office] + ("-special" if special else "")
            out.append(dict(t=2020, st=st, office=office, special=special, source="nyt2020_statepage+racepage", kind=NYT,
                            loader=lambda st=st, rid=rid, slug=slug: snapshots_nyt2020_page(st, rid, slug)))
    for st in TARGET:                                              # 2022 Senate / governor race pages
        for race, office in (("us-senate", "sen"), ("governor", "gov")):
            if (RAW / "nyt2022_pages" / st / race).exists() and any((RAW / "nyt2022_pages" / st / race).glob("*.json.gz")):
                out.append(dict(t=2022, st=st, office=office, special=False, source="nyt2022_page", kind=NYT,
                                loader=lambda st=st, race=race: snapshots_nyt2022(st, race)))
    if (RAW / "mi2022_official").exists() and any((RAW / "mi2022_official").glob("*.tsv")):
        out.append(dict(t=2022, st="mi", office="gov", special=False, source="mi2022_official", kind=OFFICIAL,
                        loader=snapshots_mi2022))
    for kind in ("pres", "sen", "gov"):
        d = RAW / "nyt2024_feeds" / kind
        if not d.exists():
            continue
        last = next((p for p in sorted(d.glob("*.json.gz"), reverse=True) if _read_json(p)), None)
        if last is None:
            continue
        for race in _read_json(last)["data"]:
            nid = race["nyt_id"]
            parts = nid.split("-")
            if len(parts) != 6 or parts[1] != "G" or (kind == "pres" and parts[0].lower() not in TARGET):
                continue
            st = parts[0].lower()
            out.append(dict(t=2024, st=st, office=kind, special=False, source="nyt2024_feed", kind=NYT, interp=False,
                            loader=lambda kind=kind, st=st: snapshots_nyt2024(kind, st)))
            if kind != "pres" and st in TARGET:          # the same night on the president's county clock (dense; see interpolate_race)
                out.append(dict(t=2024, st=st, office=kind, special=False, source="nyt2024_feed+pres_clock", kind=NYT, interp=True,
                                loader=lambda kind=kind, st=st: interpolate_race(snapshots_nyt2024(kind, st), snapshots_nyt2024("pres", st))))
    for sp in out:
        sp.setdefault("interp", False)
        sp["key"] = f'{sp["t"]}_{sp["st"]}_{sp["office"]}{"_sp" if sp["special"] else ""}{"_i" if sp["interp"] else ""}{"_off" if sp["kind"] == OFFICIAL else ""}'
    return out


# ------------------------------------------------------------------ priors (v1, v3, unified) for a night


class Ctx:
    """Panels and leave-one-cycle-out models, built once."""

    def __init__(self):
        self.inp = uu.load_inputs()
        self.R = uu.race_table(self.inp)
        n = uu.nat_table(self.inp["nat"])
        self.res = bt.state_backtest(self.R, n, {"extras": (), "lam": 0.0})
        self.C = bt.prep_county(self.inp, self.R)
        gb = bt.state_base_votes(self.C)
        self.gb = gb
        self._models: dict[int, tuple] = {}
        self.priors = us.load_priors()

    def models(self, t: int):
        """(share model, turnout model, state-vote model) fitted without cycle t."""
        if t not in self._models:
            tr = self.C[(self.C["t"] != t) & (self.C["t"].isin(bt.TEST))]
            self._models[t] = (uu.fit_share(tr, uu.select_lam(tr)), uu.fit_turnout(tr, 0.0, uu.TURN_FEATS), bt.fit_vstate(self.gb[self.gb["t"] != t]))
        return self._models[t]

    def truth(self, t, st, office, special) -> dict | None:
        """Certified statewide two-party Dem share and votes."""
        if office == "pres":
            r = self.inp["rs"]
            r = r[(r["year"] == t) & (r["st"] == st) & (r["contest"] == "pres")]
            return {"s2": float(r["s2"].iloc[0]), "v2": float(r["v2"].iloc[0])} if len(r) else None
        r = self.R[(self.R["t"] == t) & (self.R["st"] == st) & (self.R["office"] == office) & (self.R["special"] == special)]
        return {"s2": float(r["s2"].iloc[0]), "v2": float(r["v2"].iloc[0]), "P": r["P"].iloc[0], "n_polls": int(r["n_polls"].iloc[0])} if len(r) else None


def names_of(st: str) -> pd.Series:
    return us.counties(st).set_index("unit_id")["name"]


def count_cfg(st: str, office: str) -> tuple[dict, dict]:
    """The registered v1 [model] / [count] settings of the state's feed (configs/us2026_<st>_<office>_v1.toml, else the state's other
    office, else the civic template) with the two feed-dependent switches the NYT copies cannot honour reset: OH's `outstanding
    ballots` field does not exist (f_complete = 0.98 instead) and the prior's expected votes already carry the cycle-type ratio."""
    cdir = ROOT / "configs"
    cand = [cdir / f"us2026_{st}_{office}_v1.toml", cdir / f"us2026_{st}_{'gov' if office != 'gov' else 'sen'}_v1.toml",
            cdir / f"us2026_{st}_gov_v1.toml", cdir / f"us2026_{st}_sen_v1.toml"]
    p = next((x for x in cand if x.exists()), None)
    if p:
        with open(p, "rb") as fh:
            c = tomllib.load(fh)
    else:
        c = {"model": {"tau": 0.06, "rho": 0.3, "sigma_mun0": 0.08}, "count": {"f_max": 0.95, "f_complete": 0.98, "learn_turnout": True}}
    m, k = dict(c["model"]), dict(c["count"])
    for x in ("swing_prior_mean", "swing_prior_sd", "count_scaled_terms"):
        m.pop(x, None)
    k["turnout_ratio"] = 1.0
    if k.pop("use_outstanding", False):
        k["f_complete"], k["learn_turnout"] = 0.98, True
    return {"tau": m["tau"], "rho": m["rho"], "sigma_mun0": m.get("sigma_mun0", 0.08)}, k


def base_cfg(st: str, k: dict) -> dict:
    return {"election": {"state": st, "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {}, "count": k}


def v1_prior(ctx: Ctx, t: int, st: str, office: str, special: bool) -> pd.DataFrame:
    """Registered v1: last same-office result (else last president, shifted by the state lean as in the backtest), votes = its votes
    (president base x 0.72 when no same-office base and the race is a midterm). President races: the last president."""
    nm = names_of(st)
    if office == "pres":
        rc = ctx.inp["rc"]
        b = rc[(rc["year"] == t - 4) & (rc["st"] == st) & (rc["contest"] == "pres")].dropna(subset=["s2", "v2"])
        uid = [uu.unit_id(st, f) for f in b["fips"]]
        return pd.DataFrame({"unit_id": uid, "name": nm.reindex(uid).values, "s0": b["s2"].values, "V": b["v2"].values}).dropna()
    d = ctx.C[(ctx.C["t"] == t) & (ctx.C["st"] == st) & (ctx.C["office"] == office) & (ctx.C["special"] == special)]
    V1 = d["vo_b"].fillna(d["vp_b"] * (0.72 if t % 4 == 2 else 1.0))
    uid = [uu.unit_id(st, f) for f in d["fips"]]
    return pd.DataFrame({"unit_id": uid, "name": nm.reindex(uid).values, "s0": d["so_f"].values, "V": V1.values}).dropna()


def unified_prior(ctx: Ctx, t: int, st: str, office: str, special: bool) -> tuple[pd.DataFrame, dict]:
    key = ctx.res[(ctx.res["t"] == t) & (ctx.res["st"] == st) & (ctx.res["office"] == office) & (ctx.res["special"] == special)].iloc[0]
    d = ctx.C[(ctx.C["t"] == t) & (ctx.C["st"] == st) & (ctx.C["office"] == office) & (ctx.C["special"] == special)]
    sm, tm, vm = ctx.models(t)
    vo = float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
    Vs = bt.predict_vstate(vm, float(d["vp_b"].sum()), vo, t % 4 == 2, bool(d["oy"].max() % 4 == 2))
    pr = uu.county_prior(d, sm, tm, float(key["mu"]), Vs)
    uid = [uu.unit_id(st, f) for f in pr["fips"]]
    nm = names_of(st)
    pri = pd.DataFrame({"unit_id": uid, "name": nm.reindex(uid).values, "s0": pr["s0"].values, "V": pr["V"].values}).dropna()
    return pri, {"mu": float(key["mu"]), "sd": float(key["sd"]), "P": key["P"], "n_polls": int(key["n_polls"])}


def pres_poll(ctx: Ctx, t: int, st: str) -> tuple[float, int] | None:
    dr = ctx.inp["drift"]
    r = dr[(dr["office"] == "pres") & (dr["cycle"] == t) & (dr["state"] == st)]
    return (float(r["avg"].iloc[0]) / 100, int(r["n_polls"].iloc[0])) if len(r) and r["n_polls"].iloc[0] > 0 else None


def variants(ctx: Ctx, sp: dict) -> dict[str, tuple[dict, pd.DataFrame]]:
    """name -> (config for brunder.us.project, prior frame). unified exists for Senate / governor only (the fitted panel)."""
    t, st, office, special = sp["t"], sp["st"], sp["office"], sp["special"]
    m, k = count_cfg(st, office)
    cfg = base_cfg(st, k)
    out: dict[str, tuple[dict, pd.DataFrame]] = {}
    pri1 = v1_prior(ctx, t, st, office, special)
    out["v1"] = ({**cfg, "model": dict(m)}, pri1)
    if sp["source"] != "nyt2020_county_table":        # sensitivity: v1 as if the feed had no precinct-completeness flag (f_complete off, no learned turnout)
        out["v1 (no flag)"] = ({**cfg, "count": {**k, "f_complete": 0.0, "learn_turnout": False}, "model": dict(m)}, pri1)
    poll = pres_poll(ctx, t, st) if office == "pres" else ((ctx.truth(t, st, office, special) or {}).get("P"), (ctx.truth(t, st, office, special) or {}).get("n_polls", 0))
    if poll and poll[0] is not None and not pd.isna(poll[0]) and poll[1] > 0:
        base1 = us.baseline_share(pri1)
        out["v3"] = ({**cfg, "model": {**m, "swing_prior_mean": float(poll[0]) - base1, "swing_prior_sd": us.poll_sd_by_n(int(poll[1])),
                                       "count_scaled_terms": True}}, pri1)
    if office in ("sen", "gov") and ctx.truth(t, st, office, special) is not None and t in bt.TEST:
        pri_u, info = unified_prior(ctx, t, st, office, special)
        out["unified"] = ({**cfg, "model": {**m, "swing_prior_mean": 0.0, "swing_prior_sd": info["sd"], "count_scaled_terms": True}}, pri_u)
    return out


# ------------------------------------------------------------------ replay


def feed_rows(fr: pd.DataFrame, st: str) -> pd.DataFrame:
    """The frame as brunder.us feed rows (two-party votes per county; early = the source's absentee / advance split when present)."""
    uid = [uu.unit_id(st, f) for f in fr["fips"]]
    base = {"level": "county", "unit_id": uid, "name": "", "race": "x", "candidate": "", "ballots": np.nan, "registered": np.nan,
            "outstanding": np.nan, "reporting": fr["rep"].to_numpy(float), "units": fr["units"].to_numpy(float)}
    has_early = fr["ed"].notna().any()
    return pd.concat([pd.DataFrame({**base, "party": p, "votes": fr[c].to_numpy(float), "early": (fr[e].to_numpy(float) if has_early else np.nan)})
                      for p, c, e in (("DEM", "d", "ed"), ("REP", "r", "er"))], ignore_index=True)[us.COLS]


def replay_night(ctx: Ctx, sp: dict, snaps: pd.DataFrame, vs: dict, truth: dict) -> pd.DataFrame:
    final, v2, win = truth["s2"], truth["v2"], truth["s2"] > 0.5
    frames = []
    last_n = -1.0
    for ts, fr in snaps.groupby("ts", sort=True):
        n = float((fr["d"] + fr["r"]).sum())
        if n <= 0 or n == last_n:
            continue
        frames.append((ts, fr, n))
        last_n = n
    if not frames:
        return pd.DataFrame()
    t0 = frames[0][0]
    if "real" in snaps.columns:                       # interpolated night: score only from the first real capture on (earlier frames assume its mix)
        anchor = snaps.loc[snaps["real"] & ((snaps["d"] + snaps["r"]) > 0), "ts"].min()
        frames = [f for f in frames if f[0] >= anchor]
    rows = []
    for name, (cfg, pri) in vs.items():
        c0 = cfg
        r0, _ = us.project(us._frame([]), c0, pri)
        rows.append({"variant": name, "ts": pd.NaT, "minute": -1.0, "counted": 0.0, "share_hat": r0["share_hat"], "sd": r0["share_sd"], "P": r0["p_win"],
                     "raw": np.nan})
        for ts, fr, n in frames:
            res, _ = us.project(feed_rows(fr, sp["st"]), cfg, pri)
            rows.append({"variant": name, "ts": ts, "minute": (ts - t0).total_seconds() / 60, "counted": n / v2, "share_hat": res["share_hat"],
                         "sd": res["share_sd"], "P": res["p_win"], "raw": res["raw"]})
    d = pd.DataFrame(rows)
    d["night"], d["final"], d["win"] = sp["key"], final, win
    d["t0"] = t0
    d["err"] = d["share_hat"] - final
    d["z"] = d["err"] / d["sd"]
    d["cover90"] = d["z"].abs() < 1.645
    d["p_correct"] = np.where(win, d["P"], 1 - d["P"])
    d["raw_err"] = d["raw"] - final
    return d



# ------------------------------------------------------------------ summaries

CHECKS = (0.10, 0.25, 0.50, 0.75)
GRID = tuple(round(c, 2) for c in np.arange(0.05, 0.901, 0.05))


def _nearest(g: pd.DataFrame, c: float, tol: float) -> pd.Series | None:
    g = g[(g["counted"] > 0) & (g["counted"] <= 1.0)]
    if g.empty:
        return None
    i = (g["counted"] - c).abs().idxmin()
    return g.loc[i] if abs(g.at[i, "counted"] - c) <= tol else None


def night_summary(d: pd.DataFrame, tol: float = 0.05) -> pd.DataFrame:
    """Per (night, variant): pre-count error / P(correct); minutes to P(correct winner) >= 0.9 that then stays so (counted <= 0.995
    frames; minutes since the first county report; resolution = the source's frame spacing); |error| in points at the frame nearest
    to 10/25/50/75 % counted (within `tol`), its z and 90 % coverage over the 5..90 % grid (frames within 0.03)."""
    out = []
    for (night, var), g in d.groupby(["night", "variant"]):
        pre = g[g["minute"] < 0].iloc[0]
        h = g[(g["minute"] >= 0) & (g["counted"] <= 0.995)].sort_values("minute").reset_index(drop=True)
        ok = (h["p_correct"] >= 0.9).to_numpy()
        first = next((i for i in range(len(ok)) if ok[i:].all()), None) if len(ok) else None
        row = {"night": night, "variant": var, "frames": len(h), "n_mid": int(((h["counted"] >= 0.05) & (h["counted"] <= 0.9)).sum()), "pre_err_pts": 100 * pre["err"], "pre_sd_pts": 100 * pre["sd"], "pre_p_correct": pre["p_correct"],
               "minutes_to_90": h["minute"].iat[first] if first is not None else np.nan,
               "counted_at_90": h["counted"].iat[first] if first is not None else np.nan,
               "decided": first is not None,
               "censored": bool(first == 0 and h["minute"].iat[0] > 5)}       # decided at the first scored frame: minutes_to_90 is an upper bound
        for c in CHECKS:
            r = _nearest(h, c, tol)
            row[f"abs_err_{int(c * 100)}_pts"] = 100 * abs(r["err"]) if r is not None else np.nan
            row[f"at_counted_{int(c * 100)}"] = r["counted"] if r is not None else np.nan
        cov = [r["cover90"] for c in GRID if (r := _nearest(h, c, 0.03)) is not None]
        row["cover90"], row["cover_n"] = (float(np.mean(cov)) if cov else np.nan), len(cov)
        out.append(row)
    return pd.DataFrame(out)


DENSE_MID = 10


def night_class(ns: pd.DataFrame) -> pd.Series:
    """dense: real captures with >= 10 frames between 5 and 90 % counted; interpolated: sparse real captures spread over the
    president's county clock; sparse: the rest (a handful of captures per night: error at the captured points only)."""
    return pd.Series(np.where(ns["interp"], "interpolated (pres clock)", np.where(ns["n_mid"] >= DENSE_MID, "dense real", "sparse real")), index=ns.index)


def variant_summary(ns: pd.DataFrame) -> pd.DataFrame:
    g = ns.groupby("variant")
    return g.agg(nights=("night", "nunique"), decided=("decided", "mean"), minutes_to_90_median=("minutes_to_90", "median"),
                 counted_at_90_median=("counted_at_90", "median"), pre_abs_err_pts=("pre_err_pts", lambda x: x.abs().mean()),
                 pre_p_correct=("pre_p_correct", "mean"), err10=("abs_err_10_pts", "mean"), err25=("abs_err_25_pts", "mean"),
                 err50=("abs_err_50_pts", "mean"), err75=("abs_err_75_pts", "mean"), cover90=("cover90", "mean")).reset_index()


# ------------------------------------------------------------------ commands


def build(only: list[str] | None) -> None:
    PROC.mkdir(parents=True, exist_ok=True)
    for sp in night_specs():
        if only and not any(o in sp["key"] for o in only):
            continue
        d = sp["loader"]()
        if d.empty:
            print(f'{sp["key"]}: no frames')
            continue
        d.to_parquet(PROC / f'{sp["key"]}.parquet', index=False)
        (PROC / f'{sp["key"]}.json').write_text(json.dumps({k: v for k, v in sp.items() if k != "loader"} | {"frames": int(d["ts"].nunique()),
                                                                 "first": str(d["ts"].min()), "last": str(d["ts"].max())}, default=str))
        print(f'{sp["key"]}: {d["ts"].nunique()} frames, {d["ts"].min()} .. {d["ts"].max()}', flush=True)


def md(df: pd.DataFrame) -> str:
    """Markdown table (no tabulate dependency)."""
    cell = lambda x: "" if pd.isna(x) else (f"{x:.3g}" if isinstance(x, float) else str(x))
    rows = [[cell(x) for x in r] for r in df.itertuples(index=False)]
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns)
    return "\n".join([head] + ["| " + " | ".join(r) + " |" for r in rows])


def ga_sim_reference() -> pd.DataFrame | None:
    """Georgia 2022 simulated replay (analysis/us_unified_replay.py sim, 30 nights): per variant, median minutes to P(correct) >= 0.9, share of
    nights decided, |error| at 10/25/50/75 % counted (mean over the two races; the sim grid has 5 % steps -> 10/25/50/75 exist) and
    90 % coverage over the grid. Variants are renamed to v1 / v3 / unified."""
    d = ROOT / "results" / "us_unified"
    f1, f2, f3 = d / "ga2022_count_replay_summary.csv", d / "ga2022_count_replay_by_checkpoint.csv", d / "ga2022_count_replay_raw.csv"
    if not (f1.exists() and f2.exists() and f3.exists()):
        return None
    ren = {"unified": "unified", "v1 (2020 president x0.8)": "v1", "v3-style (v1 + poll swing)": "v3"}
    s = pd.read_csv(f1)
    s["variant"] = s["variant"].map(ren)
    raw = pd.read_csv(f3)
    raw["variant"] = raw["variant"].map(ren)
    cnt = raw[raw["sim"] >= 0]
    out = []
    for v, g in cnt.groupby("variant"):
        row = {"variant": v, "decided": s[s["variant"] == v]["decided"].mean(), "minutes_to_90_median": s[s["variant"] == v]["minutes_to_90_median"].median(),
               "pre_abs_err_pts": 100 * raw[(raw["sim"] == -1) & (raw["variant"] == v)]["err"].abs().mean(),
               "pre_p_correct": raw[(raw["sim"] == -1) & (raw["variant"] == v)]["p_correct"].mean() if "p_correct" in raw else np.nan}
        for c in CHECKS:
            row[f"err{int(c * 100)}"] = 100 * g[np.isclose(g["check"], c)]["err"].abs().mean()
        gg = g[(g["check"] >= 0.05) & (g["check"] <= 0.9)]
        row["cover90"] = gg["cover90"].mean() if "cover90" in gg else (gg["z"].abs() < 1.645).mean()
        out.append(row)
    return pd.DataFrame(out)


def interp_validation(ctx: Ctx, only: list[str] | None) -> pd.DataFrame:
    """Leave-one-capture-out error of the pres-clock interpolation for every interpolated night on the SSD."""
    out = []
    for sp in night_specs():
        if not sp["interp"] or sp["st"] not in TARGET or (only and not any(o in sp["key"] for o in only)):
            continue
        real, pres = PROC / f'{sp["key"][:-2]}.parquet', PROC / f'2024_{sp["st"]}_pres.parquet'
        truth = ctx.truth(sp["t"], sp["st"], sp["office"], sp["special"])
        if not (real.exists() and pres.exists() and truth):
            continue
        h = holdout_knots(pd.read_parquet(real), pd.read_parquet(pres), truth["v2"])
        out.append(h.assign(night=sp["key"]))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


PAIR_METRICS = (("pre_abs_err_pts", "abs pre-count error, pts"), ("abs_err_25_pts", "abs error at ~25 % counted, pts"),
                ("abs_err_50_pts", "abs error at ~50 % counted, pts"), ("abs_err_75_pts", "abs error at ~75 % counted, pts"),
                ("minutes_to_90", "minutes to P(winner) >= 0.9 (both decided)"), ("cover90", "90 % coverage"))


def paired_table(ns: pd.DataFrame, a: str = "unified", b: str = "v3") -> pd.DataFrame:
    """Per-night paired differences a - b over the nights that have both variants and the metric (negative = a better, except coverage)."""
    x = ns.assign(pre_abs_err_pts=ns["pre_err_pts"].abs())
    out = []
    for col, lab in PAIR_METRICS:
        w = x.pivot_table(index="night", columns="variant", values=col)
        if a not in w or b not in w:
            continue
        d = (w[a] - w[b]).dropna()
        better = (d > 0) if col == "cover90" else (d < 0)
        out.append({"metric": lab, "a": a, "b": b, "nights": len(d), "mean_diff": d.mean() if len(d) else np.nan, "median_diff": d.median() if len(d) else np.nan,
                    "a_better": int(better.sum()), "b_better": int((d != 0).sum() - better.sum())})
    return pd.DataFrame(out)


def write_report(raw: pd.DataFrame, ns: pd.DataFrame, vs: pd.DataFrame, ho: pd.DataFrame | None = None) -> Path:
    """Internal report (SSD only): every derived number of the NYT-sourced nights lives here, never in the repository."""
    L = ["# US real count nights: replay of v1 / v3 / unified (INTERNAL VALIDATION: derived from NYT/Edison copies, do not publish)", ""]
    L += [f"frames file: {OUT / 'replay_frames.csv'}; nights: {ns['night'].nunique()}", ""]
    ref = ga_sim_reference()
    cols = ["variant", "nights", "decided", "minutes_to_90_median", "counted_at_90_median", "pre_abs_err_pts", "pre_p_correct", "err10", "err25", "err50", "err75", "cover90"]
    ns = ns.assign(cls=night_class(ns), grp=np.where(ns["office"] == "pres", "president", "senate/governor"))
    L += ["## races per class", "", md(ns.drop_duplicates("night").groupby(["cls", "grp"]).agg(nights=("night", "nunique"), frames_median=("frames", "median"), n_mid_median=("n_mid", "median")).reset_index()), ""]
    for (cls, grp, yr), g in ns.groupby(["cls", "grp", "t"]):
        t = variant_summary(g)[cols]
        if cls == "sparse real":
            t = t.assign(minutes_to_90_median=np.nan, counted_at_90_median=np.nan, decided=np.nan)     # frame spacing of hours: no minutes
        L += [f"## {cls}: {grp} {yr} ({g['night'].nunique()} nights: {', '.join(sorted(g['night'].unique()))})" if g["night"].nunique() <= 12
              else f"## {cls}: {grp} {yr} ({g['night'].nunique()} nights)", "", md(t.round(3)), ""]
    sg = ns[(ns["office"] != "pres") & ns["cls"].isin(["dense real", "interpolated (pres clock)"])]
    if len(sg):
        for b in ("v3", "v1"):
            L += [f"## paired differences unified - {b}: dense real + interpolated senate/governor nights", "", md(paired_table(sg, "unified", b).round(3)), ""]
    ga = ns[ns["st"] == "ga"]
    if len(ga):
        L += ["## Georgia real nights (same metrics as the Georgia 2022 simulated replay below)", "", md(ga[["night", "variant", "cls", "frames", "n_mid", "pre_err_pts", "minutes_to_90", "counted_at_90",
                                                                                                "abs_err_10_pts", "abs_err_25_pts", "abs_err_50_pts", "abs_err_75_pts", "cover90"]].round(3)), ""]
    off = ns[ns["kind"] == "official"]
    if len(off):
        L += ["## official (state-published) captures only", "", md(variant_summary(off)[cols].round(3)), ""]
    if ho is not None and len(ho):
        L += ["## interpolation check: leave-one-real-capture-out, statewide two-party Dem share error (points)", "",
              md(ho.assign(abs=ho["share_err_pts"].abs()).groupby(["night", "which"]).agg(captures=("ts", "size"), mean_abs_pts=("abs", "mean"), max_abs_pts=("abs", "max")).reset_index().round(3)), ""]
    if ref is not None:
        L += ["## Georgia 2022 simulated replay (analysis/us_unified_replay.py sim --n 30), same metrics", "", md(ref.round(3)), ""]
    L += ["## per night", "", md(ns.sort_values(["night", "variant"]).round(3)), ""]
    p = OUT / "REPORT.md"
    p.write_text("\n".join(L))
    return p


def replay(only: list[str] | None) -> None:
    global OUT
    if only:                                           # a partial run never overwrites the full replay's tables
        OUT = OUT / "partial"
    ctx = Ctx()
    OUT.mkdir(parents=True, exist_ok=True)
    allr = []
    for sp in night_specs():
        if only and not any(o in sp["key"] for o in only):
            continue
        if not only and sp["st"] not in TARGET:                    # default: the ten target states (others via --only <key>)
            continue
        f = PROC / f'{sp["key"]}.parquet'
        if not f.exists():
            continue
        truth = ctx.truth(sp["t"], sp["st"], sp["office"], sp["special"])
        if truth is None:
            print(f'{sp["key"]}: no certified result in the panel, skipped')
            continue
        try:
            d = replay_night(ctx, sp, pd.read_parquet(f), variants(ctx, sp), truth)
        except (KeyError, ValueError, IndexError, RuntimeError) as e:   # a night the model cannot take (e.g. town-level units) is reported, not hidden
            print(f'{sp["key"]}: FAILED {type(e).__name__}: {e}')
            continue
        if d.empty:
            continue
        d["kind"], d["source"], d["office"], d["st"], d["t"], d["interp"] = sp["kind"], sp["source"], sp["office"], sp["st"], sp["t"], sp["interp"]
        allr.append(d)
        print(f'{sp["key"]}: {len(d)} rows', flush=True)
    raw = pd.concat(allr, ignore_index=True)
    raw.to_csv(OUT / "replay_frames.csv", index=False)
    ns = night_summary(raw).merge(raw.drop_duplicates("night")[["night", "kind", "source", "office", "st", "t", "interp", "final"]], on="night")
    ns.to_csv(OUT / "replay_nights.csv", index=False)
    vs = variant_summary(ns)
    vs.to_csv(OUT / "replay_variants.csv", index=False)
    ho = interp_validation(ctx, only)
    if len(ho):
        ho.to_csv(OUT / "interp_holdout.csv", index=False)
    print(write_report(raw, ns, vs, ho))



def _grab(wb: Wayback, label: str, url: str, lo: str, hi: str, dest: Path, tmin: str = "0", tmax: str = "9") -> int:
    caps = [c for c in wb.cdx(url, lo, hi) if tmin <= c[0] <= tmax]
    print(f"{label}: {len(caps)} captures", flush=True)
    for i, (ts, orig, _) in enumerate(caps):
        if not wb.capture(ts, orig, dest / f"{ts}.json.gz"):
            print(f"  {label} {ts} FAILED", flush=True)
    return len(caps)


RACEPAGE20 = {"ga": ["senate"], "az": ["senate-special"], "mi": ["senate"], "nc": ["senate", "governor"], "tx": ["senate"]}


def fetch_nyt2020_racepages(wb: Wayback) -> None:
    """The per-race pages (`race-page/<state>/<race>.json`) are captured far more often than the state pages."""
    write_licence(RAW / "nyt2020_racepage", NYT_LICENCE)
    for st, races in RACEPAGE20.items():
        for r in races:
            u = f"static01.nyt.com/elections-assets/2020/data/api/2020-11-03/race-page/{STATES_SLUG[st]}/{r}.json"
            _grab(wb, f"nyt2020 race-page {st}/{r}", u, "20201103", "20201109", RAW / "nyt2020_racepage" / st / r)


OFFICIAL_LICENCE = """Michigan Department of State, Bureau of Elections: unofficial election-night county returns (MI_CENR_BY_COUNTY), as preserved by the
Internet Archive (`id_` raw captures). Official publication of a US state government (public record); no use restriction is asserted on
the data files. Derived statistics may be published with attribution; keep the raw copy on the SSD (it is not redistributed here).
"""


def fetch_mi2022(wb: Wayback) -> None:
    """Michigan 2022 general, official county file captures (the only state-published election-night series found in the archive)."""
    d = RAW / "mi2022_official"
    d.mkdir(parents=True, exist_ok=True)
    write_licence(d, OFFICIAL_LICENCE)
    u = "mielections.us/election/results/DATA/2022GEN_MI_CENR_BY_COUNTY.xls"
    caps = wb.cdx(u, "20221108", "20221112")
    print(f"mi2022: {len(caps)} captures", flush=True)
    for ts, orig, _ in caps:
        if not wb.capture(ts, orig, d / f"{ts}.tsv"):
            print(f"  mi2022 {ts} FAILED", flush=True)


def fetch_nyt2022(wb: Wayback) -> None:
    """2022 race pages (Senate, governor) of the ten states, captures from the evening of 8 Nov to noon UTC on 10 Nov."""
    write_licence(RAW / "nyt2022_pages", NYT_LICENCE)
    for st, slug in STATES_SLUG.items():
        for r in ("us-senate", "governor"):
            u = f"static01.nyt.com/elections-assets/2022/data/2022-11-08/results-{slug}-{r}.json"
            _grab(wb, f"nyt2022 {st}/{r}", u, "20221108", "20221111", RAW / "nyt2022_pages" / st / r, "20221108220000", "20221110120000")

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "build", "replay", "report"])
    ap.add_argument("--only", nargs="*", default=None, help="fetch: nyt2020 nyt2020rp nyt2022 nyt2024 mi2022; build/replay: night key substrings")
    a = ap.parse_args()
    if a.cmd == "fetch":
        wb = Wayback()
        todo = a.only or ["nyt2020", "nyt2020rp", "nyt2024", "nyt2022", "mi2022"]
        for name, fn in (("nyt2020", fetch_nyt2020), ("nyt2020rp", fetch_nyt2020_racepages), ("nyt2024", fetch_nyt2024),
                         ("nyt2022", fetch_nyt2022), ("mi2022", fetch_mi2022)):
            if name in todo:
                fn(wb)
    elif a.cmd == "build":
        build(a.only)
    elif a.cmd == "report":                              # rewrite REPORT.md from the CSVs of the last replay
        ns = pd.read_csv(OUT / "replay_nights.csv")
        ho = pd.read_csv(OUT / "interp_holdout.csv") if (OUT / "interp_holdout.csv").exists() else None
        print(write_report(None, ns, variant_summary(ns), ho))
    else:
        replay(a.only)


if __name__ == "__main__":
    main()
