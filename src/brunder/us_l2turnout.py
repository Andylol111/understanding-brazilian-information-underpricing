"""L2-informed expected votes (county, and House county x district pieces) for the unified challenger.

Inputs are the Redistricting Data Hub (RDH) L2 general-election turnout files (never committed; they stay on the SSD under
`$BRUNDER_DATA/raw/us/rdh/l2_turnout/<state>/`):

  b20   `<ST>_l2_turnout_2020blockAgg.zip`          2020 general by 2020 census block: registered / voted by (modelled) ethnicity;
                                                    L2 file of 2021-08 (survivorship: roll of 2021-08, not of 2020-11)
  c22   `<ST>_l2_2022stats_county.zip` (+ `_2020block`)   2020 and 2022 generals: registered / voted by ethnicity and gender;
                                                    L2 file of 2023-02
  c24   `<ST>_l2_2024_gen_stats_2020county.zip` (+ `_2020block`)   2024 general: registered / voted by gender, age band, party,
                                                    ethnicity and ethnicity x party; L2 file of 2024-12

Canonical column names: `<year>.<reg|voted>.<dim>.<grp>` with dim in all / gen (m f u) / eth (eur hisp aa esa oth unk) / age /
party (dem rep npp oth) / ep (<eth>_<party>).

The model (`expected_votes`) is a log-linear composition index: for each dimension k present in the rate file,
`I_k(c) = sum_g reg_g(c) r_g / (reg(c) R)` with r_g the STATE'S pooled group turnout rate in the rate year and R the state rate;
expected ballots of county c = reg(c) * R * prod_k I_k(c)^a_k * exp(lam * rho_c), rho_c = log(actual / modelled ballots) of the
county in the rate year (its own residual, shrunk by lam), then scaled to a statewide total. Every choice (a_k, lam, blend
weights) is fitted leave-one-cycle-out in `analysis/us_l2turnout.py`.

This analysis was conducted using data from the Redistricting Data Hub. Ethnicity / party are L2 modelled fields; each file is a
later snapshot of the rolls, so older elections inside it suffer survivorship (movers, deaths and purged non-voters are missing).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .paths import DATA

CREDIT = "This analysis was conducted using data from the Redistricting Data Hub."
RDH = DATA / "raw" / "us" / "rdh"
L2 = RDH / "l2_turnout"
OUT = DATA / "processed" / "us2026" / "l2turnout"

ETH = ("eur", "hisp", "aa", "esa", "oth", "unk")
GEN = ("m", "f", "u")
PARTY = ("dem", "rep", "npp", "oth")
AGE = ("18_19", "20_24", "25_29", "30_34", "35_44", "45_54", "55_64", "65_74", "75_84", "85over")
DIMS = {"gen": GEN, "eth": ETH, "age": AGE, "party": PARTY}

_GEN_MAP = {"m": "m", "male": "m", "f": "f", "female": "f", "unk": "u", "unknown": "u"}
_PARTY_MAP = {"democratic": "dem", "republican": "rep", "ind_npp": "npp", "others": "oth"}
_VOTE_RE = re.compile(r"^(?:g(?P<d>\d{8})_)?(?P<kind>voted|reg)_(?P<rest>.+)$")

STATE_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13",
              "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
              "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34",
              "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45",
              "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56"}


def fix_fips(raw: pd.Series, st: str) -> pd.Series:
    """County FIPS of a county file. ID, MA, MS, NH, WV c22 files lost the zero padding of the county part ('161' = 16001):
    a code shorter than 5 digits that starts with the state FIPS is state + county.zfill(3); any other short code is zero-padded."""
    sf = STATE_FIPS[st]
    raw = raw.astype(str).str.strip()
    bad = (raw.str.len() < 5) & raw.str.startswith(sf)
    return raw.where(~bad, sf + raw.str[2:].str.zfill(3)).str.zfill(5)


_SNAP_RE = re.compile(r"^(party_(dem|rep|npp|oth)|age_(18_19|20_24|25_29|30_34|35_44|45_54|55_64|65_74|75_84|85over))$")


def _canon(col: str, year_default: int | None = None, snap: bool = False) -> str | None:
    """Canonical `<year>.<kind>.<dim>.<grp>` for a file column, or None when the column is not a turnout count. With `snap` (the 2022
    files) the registrant counts by party / age of the 2023-02 voter file are kept as `snap.party.<p>` / `snap.age.<a>` (no turnout)."""
    if snap and _SNAP_RE.match(col):
        dim, grp = col.split("_", 1)
        return f"snap.{dim}.{grp}"
    m = _VOTE_RE.match(col)
    if not m:
        return None
    if m["d"]:
        if m["d"] not in ("20201103", "20221108"):
            return None
        year = int(m["d"][:4])
    elif year_default:
        year = year_default
    else:
        return None
    kind, rest = m["kind"], m["rest"]
    if rest == "all":
        return f"{year}.{kind}.all"
    if rest.startswith("gender_"):
        g = _GEN_MAP.get(rest[7:])
        return f"{year}.{kind}.gen.{g}" if g else None
    if rest in ETH:
        return f"{year}.{kind}.eth.{rest}"
    if rest.startswith("age_"):
        return f"{year}.{kind}.age.{rest[4:]}" if rest[4:] in AGE else None
    if rest.startswith("party_"):
        p = _PARTY_MAP.get(rest[6:])
        return f"{year}.{kind}.party.{p}" if p else None
    mm = re.match(r"^(eur|hisp|aa|esa|oth|unk)_party_(democratic|republican|ind_npp|others)$", rest)
    if mm:
        return f"{year}.{kind}.ep.{mm[1]}_{_PARTY_MAP[mm[2]]}"
    return None


def state_files() -> dict[str, dict[str, Path]]:
    """{ST: {"b20", "c22", "c24", "k22" (22 block), "k24" (24 block)} -> zip path} for every state directory."""
    out: dict[str, dict[str, Path]] = {}
    for d in sorted(L2.iterdir()):
        if not d.is_dir():
            continue
        fs = {}
        for z in d.glob("*.zip"):
            n = z.name
            st = n[:2]
            if n.endswith("l2_turnout_2020blockAgg.zip"):
                fs["b20"] = z
            elif n.endswith("l2_2022stats_county.zip"):
                fs["c22"] = z
            elif n.endswith("l2_2022stats_2020block.zip"):
                fs["k22"] = z
            elif n.endswith("2024_gen_stats_2020county.zip"):
                fs["c24"] = z
            elif n.endswith("2024_gen_stats_2020block.zip"):
                fs["k24"] = z
            else:
                continue
            fs["st"] = st                                   # type: ignore[assignment]
        if "st" in fs:
            out[str(fs.pop("st"))] = fs
    return out


def _member(zf: zipfile.ZipFile, suffix: str) -> str:
    names = [n for n in zf.namelist() if n.endswith(suffix) and "__MACOSX" not in n]
    if not names:
        raise FileNotFoundError(f"{suffix} not in {zf.filename}")
    return names[0]


def _read_zip_csv(zpath: Path, idc: str, default: int | None, tolerant: bool = False, snap: bool = False) -> tuple[pd.DataFrame, dict]:
    import io
    import subprocess
    with zipfile.ZipFile(zpath) as zf:
        name = _member(zf, ".csv")
        with zf.open(name) as fh:
            hdr = pd.read_csv(fh, nrows=0).columns if not tolerant else pd.read_csv(io.BytesIO(fh.read(1 << 20).split(b"\n")[0]), nrows=0).columns
        keep = {c: _canon(c, default, snap) for c in hdr}
        keep = {c: v for c, v in keep.items() if v}
        if tolerant:
            raw = subprocess.run(["unzip", "-p", str(zpath), name], capture_output=True, check=False).stdout
            return pd.read_csv(io.BytesIO(raw), usecols=[idc, *keep], dtype={idc: str}), keep
        with zf.open(name) as fh:
            return pd.read_csv(fh, usecols=[idc, *keep], dtype={idc: str}), keep


def read_l2(zpath: Path, kind: str, st: str) -> pd.DataFrame:
    """One L2 file as a frame indexed by `fips` (county files) or `geoid` (block files) with canonical count columns.
    kind: b20 / k22 / k24 (block) or c22 / c24 (county)."""
    block = kind in ("b20", "k22", "k24")
    default = 2024 if kind in ("c24", "k24") else None
    idc = "geoid20" if block else "countyfips"
    try:
        df, keep = _read_zip_csv(zpath, idc, default, snap=kind in ("c22", "k22"))
    except zipfile.BadZipFile:
        # TX_l2_turnout_2020blockAgg.zip has a bad CRC-32 in the RDH download; `unzip -p` streams the data anyway (checked against the
        # county file in analysis/us_l2turnout.py: the county sums agree to within survivorship)
        df, keep = _read_zip_csv(zpath, idc, default, tolerant=True, snap=kind in ("c22", "k22"))
    df = df.rename(columns=keep)
    df = df.loc[:, ~df.columns.duplicated()]
    if block:
        df["geoid"] = df.pop(idc).str.zfill(15)
        return df.set_index("geoid")
    df["fips"] = fix_fips(df.pop(idc), st)
    return df.set_index("fips")


def county_frame(kind: str) -> pd.DataFrame:
    """All states' county frame for c22 / c24; b20 is aggregated from blocks to counties (first 5 digits of the block GEOID)."""
    parts = []
    for st, fs in state_files().items():
        if kind not in fs:
            continue
        df = read_l2(fs[kind], kind, st)
        if kind == "b20":
            df = df.groupby(df.index.str[:5]).sum()
            df.index.name = "fips"
        parts.append(df.assign(st=st))
    return pd.concat(parts)


def build_county(force: bool = False) -> dict[str, pd.DataFrame]:
    """Write / read the derived county frames (b20, c22, c24) under `OUT` (SSD only; never committed)."""
    OUT.mkdir(parents=True, exist_ok=True)
    res = {}
    for kind in ("b20", "c22", "c24"):
        p = OUT / f"county_{kind}.parquet"
        if force or not p.exists():
            county_frame(kind).to_parquet(p)
        res[kind] = pd.read_parquet(p)
    return res


# ------------------------------------------------------------------ composition-index model


def _sum_cols(df: pd.DataFrame, year: int, kind: str, dim: str, grps) -> pd.DataFrame:
    cols = {g: f"{year}.{kind}.{dim}.{g}" for g in grps}
    cols = {g: c for g, c in cols.items() if c in df.columns}
    return df[list(cols.values())].rename(columns={c: g for g, c in cols.items()})


def pooled_rates(rate_df: pd.DataFrame, year: int, dims: tuple[str, ...], by_state: bool = True, grps=DIMS) -> dict:
    """State-pooled group turnout rates r[(st, dim)][g] = sum voted / sum registered in the rate file (a pooled national rate when
    by_state is False). Groups with no registrants get the state's overall rate."""
    out = {}
    key = rate_df["st"] if by_state else pd.Series("US", index=rate_df.index)
    for st, idx in rate_df.groupby(key).groups.items():
        d = rate_df.loc[idx]
        tot = d[f"{year}.voted.all"].sum() / max(d[f"{year}.reg.all"].sum(), 1)
        for dim in dims:
            reg = _sum_cols(d, year, "reg", dim, grps[dim]).sum()
            vot = _sum_cols(d, year, "voted", dim, grps[dim]).sum()
            out[(st, dim)] = {g: float(vot[g] / reg[g]) if reg[g] > 0 else float(tot) for g in reg.index}
        out[(st, "all")] = float(tot)
    return out


def model_ballots(reg_df: pd.DataFrame, reg_year: int, rates: dict, dims: tuple[str, ...], expo: dict | None = None,
                  by_state: bool = True, grps=DIMS) -> pd.DataFrame:
    """Modelled ballots per county under the log-linear composition index: columns `reg` (registrants, `reg_year`), `rate` (state
    rate), `idx_<dim>` and `ballots` = reg * R * prod idx_k^a_k. Registrant counts of a dimension are rescaled to sum to
    reg.all (unmodelled `unk` groups keep their share), so every index is a pure composition effect."""
    expo = expo or {}
    out = pd.DataFrame(index=reg_df.index)
    out["reg"] = reg_df[f"{reg_year}.reg.all"].astype(float)
    st = reg_df["st"] if by_state else pd.Series("US", index=reg_df.index)
    out["rate"] = [rates[(s, "all")] for s in st]
    logb = np.log(out["reg"].clip(lower=1e-9)) + np.log(out["rate"])
    for dim in dims:
        regs = _sum_cols(reg_df, reg_year, "reg", dim, grps[dim]).astype(float)
        tot = regs.sum(axis=1).replace(0, np.nan)
        r = np.zeros(len(reg_df))
        for g in regs.columns:
            rg = np.array([rates[(s, dim)].get(g, rates[(s, "all")]) for s in st])
            r += regs[g].to_numpy() * rg
        idx = pd.Series(r, index=reg_df.index) / tot / out["rate"]
        idx = idx.fillna(1.0)
        out[f"idx_{dim}"] = idx
        logb = logb + expo.get(dim, 1.0) * np.log(idx.clip(lower=1e-6))
    out["ballots"] = np.exp(logb)
    return out


# ------------------------------------------------------------------ House pieces (county x district) from 2020-block data

HOUSE = DATA / "raw" / "us" / "house"
BLOCK_RESULTS = RDH / "block_results"
PIECES_OUT = OUT / "pieces_l2.parquet"
STATE_DIR = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado", "CT": "connecticut",
             "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
             "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
             "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire",
             "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york", "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma",
             "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee",
             "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin",
             "WY": "wyoming"}


def read_dbf(buf: bytes, want) -> pd.DataFrame:
    """Minimal dBase III reader (RDH block-results shapefiles): columns for which `want(name)` is true; numeric fields as float."""
    import struct
    nrec, hlen, rlen = struct.unpack("<IHH", buf[4:12])
    fields, off, i = [], 1, 32
    while buf[i] != 0x0D:
        fields.append((buf[i:i + 11].split(b"\x00")[0].decode("latin1"), chr(buf[i + 11]), buf[i + 16], off))
        off += buf[i + 16]
        i += 32
    arr = np.frombuffer(buf, dtype=np.uint8, count=nrec * rlen, offset=hlen).reshape(nrec, rlen)
    out = {}
    for name, ftype, ln, o in fields:
        if not want(name):
            continue
        col = np.ascontiguousarray(arr[:, o:o + ln]).view(f"S{ln}").ravel()
        s = pd.Series(np.char.strip(col).astype(str))
        out[name] = pd.to_numeric(s.replace("", np.nan), errors="coerce").to_numpy() if ftype in "NF" else s.to_numpy()
    return pd.DataFrame(out)


def block_president(st: str, year: int) -> pd.DataFrame:
    """Two-party presidential votes (d, r) by 2020 census block from the RDH general-election results disaggregated to blocks
    (2020: shapefile dbf, 2024: csv; empty for 2024 in CA and LA). Index = 15-digit block GEOID."""
    d = BLOCK_RESULTS / STATE_DIR[st]
    yy = str(year)[2:]
    pat = re.compile(rf"^G{yy}PRE[A-Z]")
    if year == 2024:
        z = next(d.glob("*_2024_gen_2020_blocks_csv.zip"), None)
        if z is None:                      # CA and LA have no 2024 block results in the RDH download
            return pd.DataFrame(columns=["d", "r"], dtype=float)
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            with zf.open(name) as fh:
                hdr = pd.read_csv(fh, nrows=0).columns
            cols = [c for c in hdr if pat.match(c)]
            wi = [c for c in hdr if c in (f"PREDEM{yy}", f"PREREP{yy}")]          # Wisconsin's own field names
            with zf.open(name) as fh:
                df = pd.read_csv(fh, usecols=["GEOID20", *cols, *wi], dtype={"GEOID20": str})
            if wi:
                df = df.rename(columns={f"PREDEM{yy}": f"G{yy}PREDWIS", f"PREREP{yy}": f"G{yy}PRERWIS"})[["GEOID20", f"G{yy}PREDWIS", f"G{yy}PRERWIS"]]
                cols = [f"G{yy}PREDWIS", f"G{yy}PRERWIS"]
    else:
        z = next(d.glob(f"*_{year}_gen_2020_blocks.zip"))
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".dbf"))
            df = read_dbf(zf.read(name), lambda n: n == "GEOID20" or bool(pat.match(n)))
        cols = [c for c in df.columns if c != "GEOID20"]
    out = pd.DataFrame({"d": df[[c for c in cols if c[6] == "D"]].sum(axis=1), "r": df[[c for c in cols if c[6] == "R"]].sum(axis=1)})
    out.index = df["GEOID20"].str.zfill(15).values
    return out.groupby(level=0).sum()


def block_districts(st: str) -> pd.DataFrame:
    """Block -> district on the lines of the 2024 election (`cd119`) and of 2026 (`cd120`) for the blocks of `st` (state file of the
    119th-Congress equivalency replaces the national file where the 2024 map was redrawn: AL GA LA NY NC)."""
    sf = STATE_FIPS[st]
    with zipfile.ZipFile(HOUSE / "cd119.zip") as z:
        m = [n for n in z.namelist() if re.match(rf"{sf}_{st}_CD119\.txt$", n)]
        b119 = pd.read_csv(z.open(m[0] if m else "NationalCD119.txt"), dtype=str, usecols=["GEOID", "CDFP"])
    with zipfile.ZipFile(HOUSE / "cd120.zip") as z:
        b120 = pd.read_csv(z.open("NationalCD120.txt"), dtype=str, usecols=["GEOID", "CDFP"])
    b = b119[b119["GEOID"].str[:2] == sf].merge(b120[b120["GEOID"].str[:2] == sf], on="GEOID", suffixes=("119", "120"), how="outer")
    b = b.rename(columns={"CDFP119": "cd119", "CDFP120": "cd120"}).set_index("GEOID")
    return b[(b["cd119"] != "ZZ") & (b["cd120"] != "ZZ")]


def state_pieces(st: str) -> pd.DataFrame:
    """One state's (county, cd119, cd120) pieces: presidential votes 2020 / 2024 (block results) and the L2 registered / voted counts
    of the b20 / k22 / k24 block files (canonical columns prefixed b20_ / k22_ / k24_ for the columns that differ by file)."""
    fs = state_files()[st]
    bd = block_districts(st)
    p24 = block_president(st, 2024)
    frames = [bd, block_president(st, 2020).add_prefix("p20_"), p24.add_prefix("p24_")]
    for kind in ("b20", "k22", "k24"):
        if kind in fs:
            frames.append(read_l2(fs[kind], kind, st).add_prefix(kind + "|"))
    b = bd
    for f in frames[1:]:
        b = b.join(f, how="left")
    num = [c for c in b.columns if c not in ("cd119", "cd120")]
    b[num] = b[num].fillna(0.0)
    b["county"] = b.index.str[:5]
    p = b.groupby(["county", "cd119", "cd120"])[num].sum()
    p["blocks"] = b.groupby(["county", "cd119", "cd120"]).size()
    p = p.reset_index()
    p.insert(0, "st", st)
    return p


def build_pieces(force: bool = False, workers: int = 4) -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    if PIECES_OUT.exists() and not force:
        return pd.read_parquet(PIECES_OUT)
    from concurrent.futures import ProcessPoolExecutor
    sts = sorted(state_files())
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(state_pieces, sts))
    out = pd.concat(parts, ignore_index=True)
    out.to_parquet(PIECES_OUT)
    return out


# ------------------------------------------------------------------ splitting a 2024 piece over the 2026 lines

REDRAWN = ("AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT")       # states with a new 2026 map (analysis/us_house.REDRAWN)
GROUP = ["st", "county", "cd119"]


def pop_pieces() -> pd.DataFrame:
    """raw/us/house/pieces.csv (census blocks and 2020 population of each county x cd119 x cd120 piece; population only in the redrawn
    states): the weights the registered House priors use to split a 2024 county piece over the 2026 districts."""
    p = pd.read_csv(HOUSE / "pieces.csv", dtype={"county": str, "cd119": str, "cd120": str})
    return p.rename(columns={"pop": "popc"})[["st", "county", "cd119", "cd120", "popc"]]


def split_shares(df: pd.DataFrame, key: str, fallback: str = "blocks") -> pd.Series:
    """Share of each sub-piece (row) in its (state, county, cd119) group by the column `key`; groups whose key sums to zero fall back to
    `fallback`."""
    k = df[key].astype(float).clip(lower=0)
    tot = k.groupby([df[g] for g in GROUP]).transform("sum")
    fb = df[fallback].astype(float)
    fbt = fb.groupby([df[g] for g in GROUP]).transform("sum")
    return pd.Series(np.where(tot > 0, k / tot.where(tot > 0, 1.0), fb / fbt), index=df.index)


SHIFT_FEATS = ("dr", "aa", "hisp", "esa")


def shift_features(df: pd.DataFrame, wkey: str, snap: bool) -> pd.DataFrame:
    """Within-group centred composition of each sub-piece's roll: Democratic minus Republican registration share (`dr`), and the
    African-American / Hispanic / East-Asian shares (modelled L2 ethnicity). `snap`: the 2023-02 snapshot (`k22`, party counts of the
    whole roll, ethnicity of the 2022 roll), else the Dec-2024 roll (`k24`). Centred on the group's `wkey`-weighted mean so that the
    shifts sum to zero over the group."""
    if snap:
        tot, dem, rep = df["2022.reg.all"], df["snap.party.dem"], df["snap.party.rep"]
        eth = {e: df[f"2022.reg.eth.{e}"] for e in ("aa", "hisp", "esa")}
    else:
        tot, dem, rep = df["2024.reg.all"], df["2024.reg.party.dem"], df["2024.reg.party.rep"]
        eth = {e: df[f"2024.reg.eth.{e}"] for e in ("aa", "hisp", "esa")}
    t = tot.replace(0, np.nan)
    raw = pd.DataFrame({"dr": (dem - rep) / t, **{e: v / t for e, v in eth.items()}})
    w = df[wkey].astype(float).clip(lower=0)
    w = w.where(w > 0, 1e-9)
    g = [df[c] for c in GROUP]
    mu = (raw.fillna(0).mul(w, axis=0)).groupby(g).transform("sum").div(w.groupby(g).transform("sum"), axis=0)
    return (raw - mu).fillna(0.0)


def fit_shift(X: pd.DataFrame, y: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """No-intercept WLS (weights = sub-piece votes) of the sub-piece share deviation on the centred composition."""
    sw = np.sqrt(weight)
    return np.linalg.lstsq(X.to_numpy() * sw[:, None], y * sw, rcond=None)[0]


def sub_pieces() -> pd.DataFrame:
    """(state, county, cd119, cd120) sub-pieces of the redrawn states with the L2 block counts (prefix-free canonical names), the
    registered split key (block population `popc`) and the 2024 presidential two-party votes `v`, `d`, `r` of the RDH block results
    (no 2024 block results in CA, LA: zeros there)."""
    P = build_pieces()
    Q = P[P["st"].isin(REDRAWN)].copy()
    Q = Q.rename(columns={c: c.split("|", 1)[1] for c in Q.columns if c.startswith(("k22|", "k24|"))})
    Q = Q.drop(columns=[c for c in Q.columns if c.startswith("b20|")])
    Q = Q.merge(pop_pieces(), on=["st", "county", "cd119", "cd120"], how="left")
    Q["v"], Q["d"], Q["r"] = Q["p24_d"] + Q["p24_r"], Q["p24_d"], Q["p24_r"]
    Q["n_sub"] = Q.groupby(GROUP)["v"].transform("size")
    return Q


def resplit_pieces(old: pd.DataFrame, sub: pd.DataFrame, beta: np.ndarray | None = None) -> pd.DataFrame:
    """Re-split the 2024 presidential votes of each (state, county, 2024 district) piece over the 2026 districts with L2 weights.

    old: `analysis/us_house_night.county_pieces` rows (st, county, cd, cd26, d24, r24, t24; population split). sub: `sub_pieces()`.
    Weights: the mean of the sub-piece's shares of its group's L2 votes in 2022 (2023-02 file) and 2024 (Dec-2024 file). With `beta`
    (coefficients on `shift_features`, Dec-2024 roll) the Democratic two-party share of a sub-piece also departs from its parent's
    by the centred composition shift (the weights keep the parent's Democratic votes fixed). Groups whose set of 2026 districts
    differs between `old` and `sub`, or whose L2 votes are all zero, keep the old split (column `how` = 'pop')."""
    key = ["st", "county", "cd", "cd26"]
    s = sub.assign(cd=sub["cd119"].astype(int), cd26=sub["cd120"].astype(int))
    par = old.groupby(["st", "county", "cd"])[["d24", "r24", "t24"]].sum()
    s["sh22"] = split_shares(s, "2022.voted.all")
    s["sh24"] = split_shares(s, "2024.voted.all")
    ok22 = s.groupby(GROUP)["2022.voted.all"].transform("sum") > 0
    ok24 = s.groupby(GROUP)["2024.voted.all"].transform("sum") > 0
    s["ws"] = np.where(ok22 & ok24, (s["sh22"] + s["sh24"]) / 2, np.where(ok24, s["sh24"], np.where(ok22, s["sh22"], np.nan)))
    s["shift"] = 0.0
    if beta is not None:
        s["shift"] = shift_features(s.assign(w=s["ws"].fillna(0.0)), "w", snap=False).to_numpy() @ np.asarray(beta)
    s = s[s["st"].isin(old["st"].unique())][key + ["ws", "shift"]]
    want = old.groupby(["st", "county", "cd"])["cd26"].apply(lambda x: tuple(sorted(x))).rename("want")
    have = s.groupby(["st", "county", "cd"])["cd26"].apply(lambda x: tuple(sorted(x))).rename("have")
    chk = pd.concat([want, have], axis=1)
    good = chk[(chk["want"] == chk["have"])].index
    m = old.merge(s, on=key, how="left")
    m = m.merge(par.rename(columns={"d24": "pd", "r24": "pr", "t24": "pt"}).reset_index(), on=["st", "county", "cd"])
    use = pd.MultiIndex.from_frame(m[["st", "county", "cd"]]).isin(good) & m["ws"].notna().to_numpy()
    use = pd.Series(use, index=m.index)
    v2 = m["pd"] + m["pr"]
    ps = (m["pd"] / v2.where(v2 > 0)).fillna(0.5)
    sh = (ps + m["shift"].fillna(0.0)).clip(0.01, 0.99)
    new_v2, new_t = v2 * m["ws"], m["pt"] * m["ws"]
    out = m[key].copy()
    out["d24"] = np.where(use, new_v2 * sh, m["d24"])
    out["r24"] = np.where(use, new_v2 * (1 - sh), m["r24"])
    out["t24"] = np.where(use, new_t, m["t24"])
    out["how"] = np.where(use, "L2", "pop")
    return out
