"""North Carolina inputs for the midterms context tables: per-voter absentee / one-stop files (2018, 2020, 2022, 2024, 2026) and the
voter_stats registration counts. Only group counts leave this module. Multi-GB inputs are counted once and cached under /tmp."""
from __future__ import annotations

import csv
import hashlib
import io
import pickle
import re
import subprocess
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import us_ctx_tax as tx

CODE_VER = "nc-v3"
CACHE = Path("/tmp/us_ctx_cache")
EV_YEARS = (2018, 2020, 2022, 2024)
REG_FILES = {2018: "voter_stats_20181106.zip", 2020: "voter_stats_20201103.zip", 2022: "voter_stats_20221108.zip",
             2024: "voter_stats_20241105.zip"}
BANDS = tx.UF_BANDS                                 # NC voter_stats age bands = the own dim age_ncreg
EV_DIMS = ("race", "age", "gender", "party", "density", "age_ncreg")


# ------------------------------------------------------------------------------------------------ group coding
def race_group(race: str, eth: str) -> str:
    """NC race + ethnicity codes or words -> harmonised race group; Hispanic or Latino of any race is Hispanic."""
    e, r = eth.strip().upper(), race.strip().upper()
    if e in ("HL", "HISPANIC OR LATINO"):
        return "Hispanic"
    if r in ("W", "WHITE"):
        return "White"
    if r in ("B", "BLACK OR AFRICAN AMERICAN", "BLACK"):
        return "Black"
    if r in ("A", "ASIAN"):
        return "Asian"
    return "Other / unknown"


def _age_int(a) -> int | None:
    try:
        n = int(a)
    except (TypeError, ValueError):
        return None
    return n if n >= 18 else None


def age_group(a) -> str | None:
    """Exact age -> harmonised band 18-29 / 30-44 / 45-64 / 65+ (None for under 18 or not a number)."""
    n = _age_int(a)
    return None if n is None else "18-29" if n <= 29 else "30-44" if n <= 44 else "45-64" if n <= 64 else "65+"


def age_ncreg_group(a) -> str | None:
    """Exact age or a voter_stats band label -> the NC registration bands 18-25 / 26-40 / 41-65 / 66+."""
    if isinstance(a, str) and a.startswith("Age"):
        return {"Age 18 - 25": "18-25", "Age 26 - 40": "26-40", "Age 41 - 65": "41-65", "Age Over 66": "66+"}.get(a)
    n = _age_int(a)
    return None if n is None else "18-25" if n <= 25 else "26-40" if n <= 40 else "41-65" if n <= 65 else "66+"


def party_group(p: str) -> str:
    return {"DEM": "Democratic", "REP": "Republican", "UNA": "Unaffiliated"}.get(p.strip().upper(), "Other party")


def gender_group(g: str) -> str:
    return {"F": "Female", "M": "Male"}.get(g.strip().upper()[:1], "Undisclosed")


def density_tier(d: float) -> str:
    return tx.DENS[0] if d >= 500 else tx.DENS[1] if d >= 100 else tx.DENS[2]


def nc_density() -> dict[str, str]:
    """county NAME (normalised) -> density tier, from the latest ACS county file."""
    import us_ev_db as db
    cd = pd.read_parquet(db.data_root() / "processed" / "us2026" / "unified" / "county_demo.parquet")
    cd = cd[cd["st"] == "nc"].sort_values("year").groupby("fips").tail(1)
    by_fips = dict(zip(cd["fips"], cd["density"]))
    out = {}
    for name in sorted(db._geo()["nc"]):
        f = db.county_fips("nc", name)
        if f in by_fips:
            out[name] = density_tier(by_fips[f])
    return out


# --------------------------------------------------------------------------------------------- early vote counts
_COLS = ("county_desc", "ballot_rtn_dt", "voter_party_code", "ballot_rtn_status", "race", "gender", "age")


def nc_count(rows, tier: dict[str, str], eth_by_ncid: dict[str, str] | None = None, stats: Counter | None = None) -> Counter:
    """Rows of an NC absentee file (header first) -> Counter[(ISO date, race, age, gender, party, density, age_ncreg)] of ACCEPTED
    ballots. The 2018 file has no ethnicity column: `eth_by_ncid` supplies it (ncid -> ethnic_code) from the same-day voter snapshot."""
    import us_ev_db as db
    it = iter(rows)
    h = next(it)
    ix = {c: h.index(c) for c in _COLS}
    ei = h.index("ethnicity") if "ethnicity" in h else None
    ni = h.index("ncid") if "ncid" in h else None
    out: Counter = Counter()
    for r in it:
        if len(r) != len(h) or not r[ix["ballot_rtn_status"]].strip().upper().startswith("ACCEPTED"):
            continue
        m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", r[ix["ballot_rtn_dt"]].strip())
        age = age_group(r[ix["age"]])
        if not m or age is None:
            continue
        if ei is not None:
            eth = r[ei]
        elif eth_by_ncid is not None and ni is not None:
            eth = eth_by_ncid.get(r[ni].strip())
            if stats is not None:
                stats["matched" if eth is not None else "unmatched"] += 1
            eth = eth or ""
        else:
            eth = ""
        t = tier.get(db._norm(r[ix["county_desc"]]), tx.DENS[1])
        out[(f"{m.group(3)}-{m.group(1)}-{m.group(2)}", race_group(r[ix["race"]], eth), age, gender_group(r[ix["gender"]]),
             party_group(r[ix["voter_party_code"]]), t, age_ncreg_group(r[ix["age"]]))] += 1
    return out


def _csv_rows(zf: zipfile.ZipFile):
    raw = zf.open(zf.namelist()[0])
    return csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline=""))


def snapshot_ethnicity(snap: Path, ncids: set[str]) -> dict[str, str]:
    """ncid -> ethnic_code from a VR_Snapshot file (13 GB text): stream (UTF-16 text) with unzip | iconv | cut, keep the ids asked for. A voter listed more
    than once keeps the row of an active / inactive status."""
    p = subprocess.Popen(f"unzip -p '{snap}' | iconv -c -f UTF-16 -t UTF-8 | LC_ALL=C cut -f5,6,38", shell=True, stdout=subprocess.PIPE)
    out: dict[str, str] = {}
    live: set[str] = set()
    hdr = None
    for line in io.TextIOWrapper(p.stdout, encoding="utf-8", errors="replace"):
        f = line.rstrip("\n").split("\t")
        if hdr is None:
            hdr = [x.strip("\ufeff").strip() for x in f]
            assert hdr == ["ncid", "status_cd", "ethnic_code"], hdr
            continue
        if len(f) != 3 or f[0] not in ncids:
            continue
        isl = f[1].strip() in ("A", "I")
        if f[0] not in out or (isl and f[0] not in live):
            out[f[0]] = f[2].strip()
            if isl:
                live.add(f[0])
    p.wait()
    return out


def _file_key(path: Path) -> str:
    s = path.stat()
    return hashlib.md5(f"{CODE_VER}|{path.name}|{s.st_size}|{int(s.st_mtime)}".encode()).hexdigest()[:10]


def _cached(path: Path, tag: str, build):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{tag}_{_file_key(path)}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    v = build()
    f.write_bytes(pickle.dumps(v))
    return v


def _ev_job(args) -> tuple[int, Counter, dict]:
    year, root = args
    root = Path(root)
    z = root / f"absentee_{tx.ELECTION_DAY[year].replace('-', '')}.zip"
    tier = nc_density()

    def build():
        stats: Counter = Counter()
        with zipfile.ZipFile(z) as zf:
            if year == 2018:
                ids = set()
                for r in _csv_rows(zf):                      # pass 1: the ncids of the file
                    ids.add(r[2].strip())
                eth = snapshot_ethnicity(root / "VR_Snapshot_20181106.zip", ids)
            else:
                eth = None
        with zipfile.ZipFile(z) as zf:
            cnt = nc_count(_csv_rows(zf), tier, eth, stats)
        if year == 2018 and stats["matched"] < 0.9 * (stats["matched"] + stats["unmatched"]):
            raise RuntimeError(f"2018 ethnicity join matched too few ballots: {dict(stats)}")
        return cnt, dict(stats)

    cnt, stats = _cached(z, f"ev{year}", build)
    return year, cnt, stats


def ev_counts(root: Path, years=(*EV_YEARS, 2026)) -> tuple[dict[int, Counter], dict[int, dict]]:
    """Counter of accepted ballots per year (parallel, cached)."""
    jobs = [(y, str(root)) for y in years]
    with ProcessPoolExecutor(max_workers=min(5, len(jobs))) as ex:
        res = list(ex.map(_ev_job, jobs))
    return {y: c for y, c, _ in res}, {y: s for y, _, s in res}


def cumulate(counts: Counter, through: str) -> dict[str, Counter]:
    """counts of ballots dated <= through, per dimension (plus '_total')."""
    res = {k: Counter() for k in EV_DIMS}
    tot = 0
    for (d, race, age, gen, party, dens, agereg), n in counts.items():
        if d > through:
            continue
        tot += n
        for k, v in zip(EV_DIMS, (race, age, gen, party, dens, agereg)):
            res[k][v] += n
    res["_total"] = Counter({"all": tot})
    return res


def same_day(year: int, last_2026: str) -> str:
    """ISO date `year` with the same number of days before Election Day as `last_2026` has in 2026."""
    k = (date.fromisoformat(tx.ELECTION_DAY[2026]) - date.fromisoformat(last_2026)).days
    return (date.fromisoformat(tx.ELECTION_DAY[year]) - timedelta(days=k)).isoformat()


# ---------------------------------------------------------------------------------------- registration (voter_stats)
def reg_counts(z: Path, tier: dict[str, str]) -> dict[str, Counter]:
    import us_ev_db as db

    def build():
        res = {k: Counter() for k in ("race", "gender", "party", "density", "age_ncreg")}
        with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as raw:
            rd = csv.reader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline=""), delimiter="\t")
            h = next(rd)
            ix = {c: h.index(c) for c in ("county_desc", "party_cd", "race_code", "ethnic_code", "sex_code", "age", "total_voters")}
            for r in rd:
                if len(r) != len(h):
                    continue
                n = int(r[ix["total_voters"]])
                vals = (race_group(r[ix["race_code"]], r[ix["ethnic_code"]]), gender_group(r[ix["sex_code"]]),
                        party_group(r[ix["party_cd"]]), tier.get(db._norm(r[ix["county_desc"]]), tx.DENS[1]),
                        age_ncreg_group(r[ix["age"]]))
                for kk, v in zip(res, vals):            # a row with an invalid birth date still counts for every other dim
                    if v is not None:
                        res[kk][v] += n
        return res
    return _cached(z, "reg4", build)


def reg_file_date(z: Path) -> str:
    m = re.search(r"(\d{4})(\d\d)(\d\d)", z.name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
