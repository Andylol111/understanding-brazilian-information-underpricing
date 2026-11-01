"""Day-by-day 2026 ballot inflow from official files that carry a return date (docs/us_ev_site.md).

The collector's snapshots give one cumulative figure per fetch. Two states publish the date every ballot came back, so
their whole 2026 history can be rebuilt without waiting for fetches:

  NC  the State Board's per-voter absentee/one-stop file (dl.ncsbe.gov, public record): accepted ballots counted by
      county x return date x method (mail / in person) x party registration. The voter-level file stays on the SSD
      ($BRUNDER_DATA/raw/us/ev_party/nc/absentee_20261103.zip); only the aggregate leaves this module.
  PA  the 'bydate' block (county x party x ballot-returned date) that the pa_mail_socrata collector already stores in
      each snapshot (a SERVER-SIDE SoQL aggregate of data.pa.gov: no voter-level rows were ever downloaded).

Both return the same long frame: state, county_fips, date, method, party, n (ballots dated that day, NOT cumulative).
Dates are the day the ballot was returned/accepted, so a ballot that came back earlier but was cured or counted later
sits on its return day: the series is a reconstruction from today's file, like the past-cycle panels it is compared with.

  uv run python analysis/us_ev_daily.py refresh-nc     # conditional GET of the NC file (one request), then re-aggregate
  uv run python analysis/us_ev_daily.py nc | pa        # rebuild the aggregate parquet from the files on disk
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import us_ev_db as db  # noqa: E402

UA = db.UA
NC_ZIP_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2026_11_03/absentee_20261103.zip"
COLS = ["state", "county_fips", "date", "method", "party", "n"]
NC_PARTY = {"DEM": "D", "REP": "R", "UNA": "U"}
PA_PARTY = {"DEM": "D", "REP": "R"}
ONE_STOP = ("ONE-STOP", "EARLY VOTING")


def nc_zip_path() -> Path:
    return db.data_root() / "raw" / "us" / "ev_party" / "nc" / "absentee_20261103.zip"


def daily_dir() -> Path:
    d = db.ev_root() / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def uf_2024_path() -> Path:
    return daily_dir() / "src" / "uf_2024_US.csv"


def refresh_uf_2024(log=print) -> bool:
    """The UF Election Lab's 2024 early-vote tracker file (one snapshot per state), fetched once and kept: robots.txt of
    election.lab.ufl.edu allows it, one request, honest user agent. The 2020 and 2022 files answer 404."""
    import httpx
    f = uf_2024_path()
    if f.exists():
        return False
    f.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get("https://election.lab.ufl.edu/data-downloads/earlyvote/2024/US.csv", headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
    if r.status_code != 200 or not r.content.startswith(b"state,state_abbv"):
        raise RuntimeError(f"UF 2024 file HTTP {r.status_code}")
    f.write_bytes(r.content)
    log(f"UF 2024 file stored ({len(r.content)} bytes)")
    return True


def pa_snapshot_dir() -> Path:
    return db.ev_root() / "raw" / "pa"


# ------------------------------------------------------------------------------------------------------------ NC
def nc_count(rows: Iterable[list[str]]) -> Counter:
    """csv rows of the NC absentee file (header first) -> Counter[(county NAME, 'YYYY-MM-DD', method, party)] of ACCEPTED ballots."""
    it = iter(rows)
    h = next(it)
    ix = {c: h.index(c) for c in ("county_desc", "ballot_rtn_dt", "ballot_req_type", "voter_party_code", "ballot_rtn_status")}
    out: Counter = Counter()
    for r in it:
        # NCSBE's own 'accepted' count includes 'ACCEPTED - EXCEPTION' and 'ACCEPTED - CURED' (verified: 87,630 mail on 30 Oct)
        if len(r) != len(h) or not r[ix["ballot_rtn_status"]].strip().upper().startswith("ACCEPTED"):
            continue
        m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", r[ix["ballot_rtn_dt"]].strip())
        if not m:
            continue
        method = "inperson" if r[ix["ballot_req_type"]].strip().upper() in ONE_STOP else "mail"
        party = NC_PARTY.get(r[ix["voter_party_code"]].strip().upper(), "other")
        out[(r[ix["county_desc"]].strip().upper(), f"{m.group(3)}-{m.group(1)}-{m.group(2)}", method, party)] += 1
    return out


def nc_frame(counts: Counter) -> pd.DataFrame:
    df = pd.DataFrame([(db.county_fips("nc", c), d, m, p, n) for (c, d, m, p), n in counts.items()],
                      columns=["county_fips", "date", "method", "party", "n"])
    df.insert(0, "state", "nc")
    return df.groupby(["state", "county_fips", "date", "method", "party"], as_index=False)["n"].sum()[COLS]


def nc_aggregate(zip_path: Path | None = None) -> pd.DataFrame:
    """Aggregate the per-voter zip (cached as parquet next to the DB, keyed by the zip's size and mtime)."""
    z = zip_path or nc_zip_path()
    key = f"{z.stat().st_size}-{int(z.stat().st_mtime)}"
    out, stamp = daily_dir() / "nc_2026_daily.parquet", daily_dir() / "nc_2026_daily.key"
    if out.exists() and stamp.exists() and stamp.read_text() == key:
        return pd.read_parquet(out)
    with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as raw:
        # csv module in cp1252: duckdb's reader silently drops ~0.8% of these rows (stray quote/backslash bytes)
        counts = nc_count(csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline="")))
    df = nc_frame(counts)
    df.to_parquet(out)
    stamp.write_text(key)
    return df


def nc_file_stamp() -> str | None:
    """Last-Modified of the NC file as ISO UTC (what the server said when it was downloaded)."""
    f = nc_zip_path().with_suffix(".zip.lastmodified")
    return f.read_text().strip() if f.exists() else None


def refresh_nc(log=print) -> bool:
    """Download the NC per-voter zip if the server's copy is newer than ours (one conditional GET; honest UA; the file is a
    public S3 object, no robots.txt). -> True if a new file was stored."""
    import httpx
    z = nc_zip_path()
    lm = z.with_suffix(".zip.lastmodified")
    headers = {"User-Agent": UA}
    if z.exists() and lm.exists():
        have = datetime.fromisoformat(lm.read_text().strip().replace("Z", "+00:00"))
        headers["If-Modified-Since"] = have.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    tmp = z.with_suffix(".zip.part")
    with httpx.stream("GET", NC_ZIP_URL, headers=headers, timeout=600, follow_redirects=True) as r:
        if r.status_code == 304:
            log("NC file unchanged (304)")
            return False
        if r.status_code != 200:
            raise RuntimeError(f"NC absentee zip HTTP {r.status_code}")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
        stamp = r.headers.get("last-modified")
    with zipfile.ZipFile(tmp) as zf:                       # a truncated download must not replace the good file
        if zf.testzip() is not None:
            raise RuntimeError("NC absentee zip is corrupt")
    os.replace(tmp, z)
    if stamp:
        t = datetime.strptime(stamp, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        lm.write_text(t.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    log(f"NC file replaced ({z.stat().st_size} bytes, Last-Modified {stamp})")
    return True


# ------------------------------------------------------------------------------------------------------------ PA
def pa_frame(body: bytes) -> pd.DataFrame:
    """pa_mail_socrata snapshot JSON ({rows_updated_utc, status, bydate}) -> long frame, method 'mail', party D/R/other."""
    d = json.loads(body)
    acc: Counter = Counter()
    for r in d["bydate"]:
        acc[(db.county_fips("pa", r["countyname"]), r["ballotreturneddate"][:10], PA_PARTY.get((r["party"] or "").upper(), "other"))] += int(r["n"])
    df = pd.DataFrame([(k[0], k[1], k[2], n) for k, n in acc.items()], columns=["county_fips", "date", "party", "n"])
    df.insert(0, "state", "pa")
    df.insert(4, "method", "mail")
    return df[COLS]


def pa_latest_snapshot() -> Path | None:
    """The newest pa_mail_socrata snapshot: the copy the publish script pulls from the server, else the SSD's own collector dir."""
    pulled = daily_dir() / "src" / "pa_mail_socrata_latest.json"
    if pulled.exists():
        return pulled
    snaps = sorted(pa_snapshot_dir().glob("*_pa_mail_socrata.json"))
    return snaps[-1] if snaps else None


def pa_aggregate() -> tuple[pd.DataFrame, str] | None:
    """-> (long frame of daily returns, as_of date) or None without a snapshot."""
    s = pa_latest_snapshot()
    if not s:
        return None
    body = s.read_bytes()
    return pa_frame(body), pa_as_of(body)


# ---------------------------------------------------------------------------------------------- cumulative states
def nc_as_of() -> str | None:
    """The NC file is built before 6 a.m. Eastern: it holds ballots through the previous day."""
    lm = nc_file_stamp()
    if not lm:
        return None
    t = datetime.fromisoformat(lm.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    return (t.date() - timedelta(days=1 if t.hour < 12 else 0)).isoformat()


def pa_as_of(body: bytes) -> str:
    """data.pa.gov extract date (Eastern); an extract before noon holds ballots through the previous day."""
    t = datetime.fromisoformat(json.loads(body)["rows_updated_utc"]).astimezone(ZoneInfo("America/New_York"))
    return (t.date() - timedelta(days=1 if t.hour < 12 else 0)).isoformat()


def cumulative(df: pd.DataFrame, as_of: str, start: str, measures: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily inflow -> (county rows, state rows) in the database's long layout, one row per county x day x measure x party,
    cumulative through each day from `start` to `as_of` (earlier days folded into `start`). `measures` maps method -> measure;
    'total' = all methods. Party ALL = sum of the parties."""
    df = df[df["date"] <= as_of]
    first = min(df["date"].min(), start)
    days = pd.date_range(first, as_of).strftime("%Y-%m-%d")
    out = []
    for method, meas in {**measures, "*": "total"}.items():
        d = df if method == "*" else df[df["method"] == method]
        w = d.groupby(["state", "county_fips", "party", "date"])["n"].sum().unstack("date").reindex(columns=days).fillna(0)
        c = w.cumsum(axis=1)
        c = c[[x for x in days if x >= start]]
        c.columns.name = "date"
        allp = c.groupby(level=["state", "county_fips"]).sum().assign(party="ALL").set_index("party", append=True)
        c = pd.concat([c, allp]).stack().rename("count").reset_index().rename(columns={"date": "as_of_date"})
        out.append(c.assign(measure=meas))
    county = pd.concat(out, ignore_index=True)
    county["count"] = county["count"].astype("int64")
    state = county.groupby(["state", "as_of_date", "measure", "party"], as_index=False)["count"].sum()
    return county, state


# ------------------------------------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    cmd = (argv if argv is not None else sys.argv[1:] or ["nc"])[0]
    if cmd == "refresh-nc":
        refresh_nc()
        cmd = "nc"
    if cmd == "refresh-uf2024":
        refresh_uf_2024()
        return 0
    if cmd == "nc":
        df = nc_aggregate()
        print(f"NC {len(df)} rows, {df['n'].sum()} accepted ballots, {df['date'].min()}..{df['date'].max()}")
    elif cmd == "pa":
        got = pa_aggregate()
        print("PA no snapshot" if got is None else f"PA {len(got[0])} rows, {got[0]['n'].sum()} returned ballots, {got[0]['date'].min()}..{got[0]['date'].max()}, as_of {got[1]}")
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
