"""US House districts on election night: priors, parser verification, replay calibration and configs
(brunder.us_house_night; docs/us_midterms.md#house).

    uv run python analysis/us_house_night.py priors    # data_public/us2026_house_priors.csv (county × 2026 district)
    uv run python analysis/us_house_night.py verify    # parsers on official 2022/2024 files vs certified totals
    uv run python analysis/us_house_night.py replay    # 2024 replay: coverage of the 90 % band at 5/25/50 % counted
    uv run python analysis/us_house_night.py configs   # configs/us2026_house/<st>-<nn>_v1/v2.toml

Prior: 2024 president (Harris v Trump) per county piece of each 2026 district, the same allocation as
site/data/us2026/house/districts.json (analysis/us_house.py: each county's official 2024 total split over its 2024
districts by the 2024 precinct results, then over 2026 districts by 2020 block population where lines changed).
"""

from __future__ import annotations

import itertools
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_house as H  # noqa: E402  (analysis/us_house.py: pieces, MEDSL precinct allocation)

from brunder import us  # noqa: E402
from brunder import us_house_night as hn  # noqa: E402

STATES = hn.HOUSE_STATES
OUT_PRIORS = us.ROOT_DIR / "data_public" / "us2026_house_priors.csv"
CACHE = us.US_RAW / "house_night"
PROC = us.US_PROC / "house_night"
HOUSE_CONFIGS = us.ROOT_DIR / "configs" / "us2026_house"   # <st>-<nn>_v1.toml / _v2.toml (outside configs/us2026_*_v1)


# ------------------------------------------------------------------ county pieces (2024 president)

def county_pieces(states=STATES) -> pd.DataFrame:
    """st, county (FIPS), cd (2024 district), cd26 (2026 district), d24, r24, t24: 2024 president per piece, exactly
    as analysis/us_house.data() allocates it (so district sums equal districts.json pres24)."""
    pcs = H.pieces()
    cp = H._county_pres()
    out = []
    for st in (s.upper() for s in states):
        p = pcs[pcs["st"] == st].copy()
        n = int(p["cd119"].nunique())
        p["cd"] = p["cd119"].astype(int)
        shares, _ = H._precincts(st, n)
        pc = p.groupby("county")["cd"].apply(lambda s: sorted(s.unique())).to_dict()
        w = p.groupby(["county", "cd"]).agg(pop=("pop", "sum"), blocks=("blocks", "sum")).reset_index()
        base = w["pop"] if p["pop"].notna().any() else w["blocks"]
        w["w"] = base / base.groupby(w["county"]).transform("sum")
        off = cp[2024]
        mine = [f for f in off.index if f[:2] == H.ABBR[st]]
        covered = sum(f in pc for f in mine) > 0.9 * max(1, len(mine))
        a24 = H._alloc(pc, shares, off if covered else None, w)
        pp = p.copy()
        pp["cd26"] = pp["cd120"].astype(int)
        pw = pp.groupby(["county", "cd", "cd26"]).agg(pop=("pop", "sum"), blocks=("blocks", "sum")).reset_index()
        tot = pw.groupby(["county", "cd"])["pop"].transform("sum")
        pw["w"] = np.where(tot > 0, pw["pop"] / tot, pw["blocks"] / pw.groupby(["county", "cd"])["blocks"].transform("sum"))
        x = a24.merge(pw[["county", "cd", "cd26", "w"]], on=["county", "cd"], how="left")
        x["w"] = x["w"].fillna(1.0)
        x["cd26"] = x["cd26"].fillna(x["cd"]).astype(int)
        for c in ("d", "r", "t"):
            x[c + "24"] = x[c] * x["w"]
        out.append(x.assign(st=st)[["st", "county", "cd", "cd26", "d24", "r24", "t24"]])
    return pd.concat(out, ignore_index=True)


def build_priors() -> Path:
    """data_public/us2026_house_priors.csv: source '<st>-<nn>_pres_2024', unit_id (county), dem, rep, total — 2024
    president in the county's part of the 2026 district. Checked against districts.json (pres24 dv/rv)."""
    pcs = county_pieces()
    PROC.mkdir(parents=True, exist_ok=True)
    pcs.to_csv(PROC / "pieces_pres24.csv", index=False)
    g = pcs.groupby(["st", "cd26", "county"])[["d24", "r24", "t24"]].sum().reset_index()
    g = g[g["t24"] > 0.5]
    dj = json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "house" / "districts.json").read_text())["districts"]
    worst = 0.0
    for (st, cd), x in g.groupby(["st", "cd26"]):
        ref = dj[H.did(st, cd)]["pres24"]
        worst = max(worst, abs(x["d24"].sum() - ref["dv"]) / ref["dv"], abs(x["r24"].sum() - ref["rv"]) / ref["rv"])
    assert worst < 1e-3, f"district sums differ from districts.json by {worst:.2%}"
    pri = pd.DataFrame({"source": [hn.prior_source(s.lower(), int(c)) for s, c in zip(g["st"], g["cd26"])],
                        "unit_id": "us-" + g["st"].str.lower() + "-" + g["county"],
                        "dem": g["d24"].round(1), "rep": g["r24"].round(1), "total": g["t24"].round(1)})
    head = ["# US midterms 2026 House district priors (brunder.us_house_night; analysis/us_house_night.py priors):",
            "# 2024 president (Harris, Trump, all candidates) in each county's part of each 2026 district. Source",
            f"# '<st>-<nn>_pres_2024'. Max deviation of district sums from site/data/us2026/house/districts.json: "
            f"{worst:.1e}.",
            "# Sources: MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024",
            "#   (doi:10.7910/DVN/VOQCHQ) and U.S. President Precinct-Level Returns 2024 (doi:10.7910/DVN/XDJYKC), CC0;",
            "#   US Census Bureau 119th/120th Congress block equivalency files and 2020 P.L. 94-171 population (public",
            "#   domain); allocation in analysis/us_house.py."]
    OUT_PRIORS.write_text("\n".join(head) + "\n" + pri.to_csv(index=False))
    print(f"{OUT_PRIORS}: {pri['source'].nunique()} districts, {len(pri)} county pieces; max dev {worst:.1e}")
    return OUT_PRIORS


# ------------------------------------------------------------------ parser verification

FX = us.ROOT_DIR / "tests" / "fixtures" / "us"


def _cmp(name: str, got: pd.DataFrame, ref: dict[str, dict], st: str) -> dict:
    """Compare parsed district totals (race 'house-NN' -> dem, rep) with reference {'ST-NN': {dv, rv}}."""
    out = []
    for race, x in got.iterrows():
        k = H.did(st.upper(), int(race[6:]))
        r = ref.get(k) or {}
        if not r.get("dv") and not r.get("rv"):
            continue
        out.append({"district": k, "dem": x["dem"], "rep": x["rep"], "ref_dem": r.get("dv"), "ref_rep": r.get("rv")})
    df = pd.DataFrame(out)
    df["dev"] = np.maximum((df["dem"] - df["ref_dem"]).abs(), (df["rep"] - df["ref_rep"]).abs())
    print(f"{name}: {len(df)} districts, exact {int((df['dev'] < 0.5).sum())}, max |diff| {df['dev'].max():.0f} votes")
    bad = df[df["dev"] >= 0.5]
    if len(bad):
        print(bad.to_string(index=False))
    return {"check": name, "districts": len(df), "exact": int((df["dev"] < 0.5).sum()),
            "max_abs_diff": float(df["dev"].max())}


def _fl_extract(date: str) -> bytes:
    us._fl_official(date, "sen")           # one cached POST (official results extract)
    m, d, y = date.split("/")
    return (us.US_RAW / "fl" / f"results_{y}{int(m):02d}{int(d):02d}_official.txt").read_bytes()


def _ga_house_items(eid: str) -> list[bytes]:
    api = "https://results.sos.ga.gov/results/public/api/elections/Georgia"
    data = json.loads(us._fetch(f"{api}/{eid}/data", us.US_RAW / "ga" / f"{eid}_data.json"))
    ids = [b["id"] for b in data["ballotItems"] if hn.house_race(b["name"][0]["text"])]
    return [us._fetch(f"{api}/{eid}/data/ballot-item/{i}", CACHE / "ga" / f"{eid}_{i}.json") for i in ids]


def _az_house_items_2026() -> list[bytes]:
    api = "https://app.enhancedvoting.com/results/public/api/elections/Arizona/110326GeneralElection"
    data = json.loads((CACHE / "az" / "data26.json").read_bytes())
    ids = [b["id"] for b in data["ballotItems"] if hn.house_race(b["name"][0]["text"])]
    return [us._fetch(f"{api}/data/ballot-item/{i}", CACHE / "az" / f"2026_{i}.json") for i in ids]


def _county_sets(rows: pd.DataFrame, st: str) -> dict:
    """Counties per district in a 2026 file vs the 2026-line pieces of the prior."""
    pri = pd.read_csv(OUT_PRIORS, comment="#")
    pri = pri[pri["source"].str.startswith(f"{st}-")]
    want = {int(s[3:5]): set(g["unit_id"]) for s, g in pri.groupby("source")}
    got = {int(r[6:]): set(g["unit_id"]) for r, g in rows.groupby("race")}
    # pieces below 0.5 % of a district's 2024 votes may be missing (block-population crosswalk slivers)
    big = {int(s[3:5]): set(g.loc[g["total"] > 0.005 * g["total"].sum(), "unit_id"]) for s, g in pri.groupby("source")}
    miss = {k: sorted(big[k] - got.get(k, set())) for k in want if big[k] - got.get(k, set())}
    extra = {k: sorted(v - want.get(k, set())) for k, v in got.items() if v - want.get(k, set())}
    dr = {k: sorted(set(g["party"])) for k, g in rows.groupby("race")}
    no_dr = sorted(k for k, v in dr.items() if not {"DEM", "REP"} <= set(v))
    print(f"{st} 2026 file: {len(got)} districts / {len(want)}; big pieces missing {miss or 'none'}; "
          f"counties not in prior {extra or 'none'}; districts without D and R {no_dr or 'none'}")
    return {"check": f"{st} 2026 county sets", "districts": len(got), "of": len(want),
            "missing": {str(k): v for k, v in miss.items()}, "extra": {str(k): v for k, v in extra.items()},
            "no_d_and_r": no_dr}


def verify() -> list[dict]:
    fec22 = H._fec22_house()
    d24 = json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "house" / "districts_2024.json").read_text())
    m24 = {k: v["house24"] for k, v in d24["districts"].items() if v.get("house24")}
    res = []
    # Florida: official results extracts (same layout as the election-night file)
    res.append(_cmp("FL 2022 official extract vs FEC 2022",
                    hn.district_totals(hn.parse_fl(_fl_extract("11/8/2022"))), fec22, "fl"))
    res.append(_cmp("FL 2024 official extract vs MEDSL 2024",
                    hn.district_totals(hn.parse_fl(_fl_extract("11/5/2024"))), m24, "fl"))
    # South Carolina: certified Clarity files (2022 general 115412, 2024 general 122436)
    for eid, ver, ref, lab in ((115412, 313728, fec22, "FEC 2022"), (122436, 359624, m24, "MEDSL 2024")):
        a, s = (us.US_RAW / "sc" / f"{eid}_{ver}_{k}.json" for k in ("ALL", "summary"))
        res.append(_cmp(f"SC Clarity {eid} vs {lab}",
                        hn.district_totals(hn.parse_sc(a.read_bytes(), s.read_bytes())), ref, "sc"))
    # North Carolina: certified precinct file 2022 vs FEC; ENR county files (2022 general) vs the precinct file
    z = zipfile.ZipFile(io.BytesIO(us._fetch(
        "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2022_11_08/results_pct_20221108.zip",
        us.US_RAW / "nc" / "results_pct_20221108.zip")))
    p = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str)
    p["race"] = p["Contest Name"].map(hn.house_race)
    p = p[p["race"].notna()].assign(party=lambda x: x["Choice Party"].fillna("").str.strip().map(us.party_code),
                                    votes=lambda x: pd.to_numeric(x["Total Votes"], errors="coerce").fillna(0))
    res.append(_cmp("NC certified precincts 2022 vs FEC 2022", hn.district_totals(p), fec22, "nc"))
    ids = us._ids("nc")
    p["unit_id"] = p["County"].map(lambda n: ids[us._norm(n)][0])
    enr = hn.parse_nc(us.gzip.decompress((FX / "nc_20221108_county.txt.gz").read_bytes()),
                      [us.gzip.decompress((FX / f"nc_20221108_results_{i}.txt.gz").read_bytes()) for i in (1, 60, 92)])
    a = enr.groupby(["unit_id", "race", "party"])["votes"].sum()
    b = p[p["unit_id"].isin(set(enr["unit_id"]))].groupby(["unit_id", "race", "party"])["votes"].sum()
    j = pd.concat([a.rename("enr"), b.rename("cert")], axis=1).fillna(0)
    dev = float((j["enr"] - j["cert"]).abs().max())
    print(f"NC ENR 2022 county files (3 counties) vs certified precincts: {len(j)} county x district x party cells, "
          f"max |diff| {dev:.0f}")
    res.append({"check": "NC ENR 2022 county files vs certified precincts", "cells": len(j), "max_abs_diff": dev})
    # Georgia: certified 2024 House ballot items (Enhanced Voting, same API and format as the night)
    res.append(_cmp("GA 2024 certified ballot items vs MEDSL 2024",
                    hn.district_totals(hn.parse_ev(_ga_house_items("2024NovGen"), "ga")), m24, "ga"))
    # Texas, Ohio, Arizona: no past file in the night format saved; the 2026 pre-election files (zero votes): every
    # district present, D and R on the ballot, counties = the prior's counties on the 2026 lines
    res.append(_county_sets(hn.parse_tx((CACHE / "tx" / "c53815.json").read_bytes()), "tx"))
    res.append(_county_sets(hn.parse_oh((CACHE / "oh" / "vssc.xml").read_bytes()), "oh"))
    res.append(_county_sets(hn.parse_ev(_az_house_items_2026(), "az"), "az"))
    res.append(_county_sets(hn.parse_fl((us.US_RAW / "fl" / "20261103_ElecResultsFL.txt").read_bytes()), "fl"))
    PROC.mkdir(parents=True, exist_ok=True)
    (PROC / "verify.json").write_text(json.dumps(res, indent=1, default=str))
    return res


# ------------------------------------------------------------------ 2024 replay (calibration)
# Each 2024 district (2024 lines) with a D vs R race and a final two-party share in [0.35, 0.65] is counted again:
# per county piece its certified 2024 House votes, split into the early + mail batch and the rest where the
# certified data say (NC, SC, AZ precinct files by vote mode; GA certified ballot items by vote type); FL, TX and OH
# publish no vote-type split, so each county piece borrows (early fraction, early share − final share) from a
# random county piece of one donor state per night (NC, SC, AZ or GA 2024). Count order as analysis/us_calibrate.py
# (early batch first, Election Day in 4 chunks, larger counties later). Prior = 2024 president of the same piece
# moved by a national swing g ~ N(0, 0.04) the model does not know (2026: the generic-ballot shift is +0.055), so the
# swing the count must find is g + (House − president); expected votes = 2024 president two-party × the realised
# House/president ratio × exp(N(0, 0.08)) (turnout error). v2 gets swing_prior_mean = g + N(0, GB_SD) and
# swing_prior_sd = V2_SD (generic-ballot error and House-vs-president spread). 90 % band = share ± 1.645 sd.

SPLIT_FEEDS = ("tx", "nc", "ga", "az")        # night feeds with an early-vote split (TX EV, NC evc+avc, GA/AZ groups)
DONORS = ("nc", "sc", "az", "ga")             # certified 2024 data with a vote-type split
_EARLY_MODE = re.compile(r"EARLY|ABSENTEE|MAIL|ONE ?STOP|ADVANCE", re.I)
_LATE_MODE = re.compile(r"ELECTION DAY|PROVISIONAL|FAILSAFE", re.I)
TURNOUT = us.EST_TURNOUT                     # 2026 midterm / 2024 president two-party votes (brunder.us)
GB_SHIFT = 0.0552    # generic-ballot average − national 2024 Harris two-party share (site/data/us2026/house/markets.json)
GB_SD = 0.0429       # error of a 2-poll average (brunder.us.POLL_SD_BINS), as markets.json generic_ballot.sd_bin
HVP_SD = 0.0294      # sd of 2024 House − president two-party shares, 395 contested districts (districts.json)
V2_SD = round(float(np.hypot(GB_SD, HVP_SD)), 4)
COUNT = {"tx": {"vote_type": True, "early_share": 0.6}, "nc": {"vote_type": True, "early_share": 0.46},
         "ga": {"vote_type": True, "early_share": 0.64}, "az": {"vote_type": True, "early_share": 0.75},
         "fl": {"vote_type": False}, "sc": {"vote_type": False}, "oh": {"vote_type": False}}
COUNT_BASE = {"turnout_ratio": TURNOUT, "f_max": 0.95, "f_complete": 0.98, "use_outstanding": False,
              "learn_turnout": True}
CHECK = (0.0, 0.05, 0.25, 0.50)
GRID_TAU, GRID_RHO, GRID_UF = (0.01, 0.02, 0.04), (0.3, 0.6), (0.03, 0.06, 0.10)   # sigma_uf0: swing of a region
SIGMA_MUN0 = 0.08
N_SIM = 6


def house24(st: str) -> pd.DataFrame:
    """county, cd, ed, er, ld, lr, split: certified 2024 House two-party votes per county piece (2024 lines), early +
    mail batch (e) and the rest (l). split = the source has a vote-type split (else all votes in l)."""
    if st == "ga":
        rows = hn.parse_ev(_ga_house_items("2024NovGen"), "ga")
        rows = rows[rows["party"].isin(["DEM", "REP"])]
        g = rows.pivot_table(index=["unit_id", "race"], columns="party", values=["votes", "early"], aggfunc="sum",
                             fill_value=0)
        out = pd.DataFrame({"ed": g[("early", "DEM")], "er": g[("early", "REP")],
                            "ld": g[("votes", "DEM")] - g[("early", "DEM")],
                            "lr": g[("votes", "REP")] - g[("early", "REP")]}).reset_index()
        return out.assign(county=out["unit_id"].str[-5:], cd=out["race"].str[6:].astype(int), split=True)[
            ["county", "cd", "ed", "er", "ld", "lr", "split"]]
    z = zipfile.ZipFile(us.US_RAW / "medsl24" / f"{st}24.zip")
    cols = ["office", "party_simplified", "mode", "votes", "county_fips", "district", "stage", "special", "candidate"]
    df = pd.concat([ch[(ch["office"] == "US HOUSE") & (ch["stage"].fillna("GEN") == "GEN")]
                    for ch in pd.read_csv(z.open(z.namelist()[0]), dtype=str, chunksize=500_000,
                                          usecols=lambda c: c in cols)], ignore_index=True)
    sp = df["special"].fillna("FALSE").str.upper() == "TRUE"
    reg = (~sp).groupby(df["district"].fillna("")).transform("any").astype(bool)
    df = df[~sp | ~reg]
    df = df[~H._not_cand(df["candidate"])]
    df["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0.0)
    df["county"] = df["county_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    df["cd"] = df["district"].map(lambda x: H._dist_no(x, 2))
    df["p"] = df["party_simplified"].fillna("").str.upper().map({"DEMOCRAT": "d", "REPUBLICAN": "r"})
    df = df[df["cd"].notna() & df["p"].notna()]
    mode = df["mode"].fillna("TOTAL").str.upper()
    df["batch"] = np.where(mode.str.match(_EARLY_MODE), "e", "l")
    split = bool((df["batch"] == "e").any() and mode.str.match(_LATE_MODE).any())
    g = df.pivot_table(index=["county", "cd"], columns=["batch", "p"], values="votes", aggfunc="sum", fill_value=0)
    g.columns = [b + p for b, p in g.columns]
    g = g.reindex(columns=["ed", "er", "ld", "lr"], fill_value=0.0).reset_index()
    g["cd"] = g["cd"].astype(int)
    return g.assign(split=split)


def replay_data() -> dict[str, pd.DataFrame]:
    """Per state: county pieces of 2024 districts with House votes by batch and the 2024 president prior."""
    pp = PROC / "pieces_pres24.csv"
    pcs = pd.read_csv(pp, dtype={"county": str}) if pp.exists() else county_pieces()
    out = {}
    for st in STATES:
        pres = pcs[pcs["st"] == st.upper()].groupby(["county", "cd"])[["d24", "r24"]].sum().reset_index()
        h = house24(st).merge(pres, on=["county", "cd"], how="inner")
        h["unit_id"] = "us-" + st + "-" + h["county"]
        out[st] = h[(h["d24"] + h["r24"] > 0)]
    return out


def _donor_pool(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """(e = early two-party fraction, delta = early D share − final D share) of every donor county piece."""
    pool = {}
    for st in DONORS:
        h = data[st]
        n = h[["ed", "er", "ld", "lr"]].sum(axis=1)
        ne = h["ed"] + h["er"]
        ok = (ne > 0) & (n - ne > 0)
        pool[st] = pd.DataFrame({"e": ne[ok] / n[ok],
                                 "delta": h["ed"][ok] / ne[ok] - (h["ed"] + h["ld"])[ok] / n[ok]})
    return pool


def _batches(x: pd.DataFrame, donor: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """unit_id, early_d, early_r, late_d, late_r for one district: the certified split where the piece has one
    (both batches non-empty and the state's source splits), else a synthetic split drawn from the donor pool."""
    d, r = (x["ed"] + x["ld"]).to_numpy(float), (x["er"] + x["lr"]).to_numpy(float)
    own = (x["split"] & (x["ed"] + x["er"] > 0) & (x["ld"] + x["lr"] > 0)).to_numpy()
    s = donor.iloc[rng.integers(0, len(donor), len(x))]
    n, sh = d + r, d / np.maximum(d + r, 1)
    ne = n * s["e"].to_numpy()
    she = np.clip(sh + s["delta"].to_numpy(), 0, 1)
    ed = np.minimum(ne * she, d)
    er = np.minimum(ne - ed, r)
    return pd.DataFrame({"unit_id": x["unit_id"].to_numpy(), "early_d": np.where(own, x["ed"], ed),
                         "early_r": np.where(own, x["er"], er), "late_d": np.where(own, x["ld"], d - ed),
                         "late_r": np.where(own, x["lr"], r - er)})


def _cfg(st: str, tau: float, rho: float, uf: float, vt: bool, ratio: float, swing: tuple[float, float] | None) -> dict:
    m = {"tau": tau, "rho": rho, "sigma_mun0": SIGMA_MUN0, "sigma_uf0": uf}
    if swing is not None:
        m |= {"swing_prior_mean": swing[0], "swing_prior_sd": swing[1]}
    return {"election": {"state": st, "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
            "model": m, "count": {**COUNT_BASE, **COUNT[st], "vote_type": vt, "turnout_ratio": ratio}}


def replay(n_sim: int = N_SIM) -> pd.DataFrame:
    from us_calibrate import events, rows_at
    data = replay_data()
    pool = _donor_pool(data)
    cert = {k: v["house24"] for k, v in json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "house" /
                                                    "districts_2024.json").read_text())["districts"].items()}
    rng = np.random.default_rng(20261103)
    res, dropped = [], []
    for st, h in data.items():
        for cd, x in h.groupby("cd"):
            d, r = (x["ed"] + x["ld"]).sum(), (x["er"] + x["lr"]).sum()
            if d <= 0 or r <= 0 or not 0.35 <= d / (d + r) <= 0.65:
                continue
            final = d / (d + r)
            ref = cert.get(H.did(st.upper(), cd)) or {}
            if ref.get("d2") is None or abs(final - ref["d2"]) > 0.003:      # incomplete precinct data (AZ Pima)
                dropped.append(H.did(st.upper(), cd))
                continue
            x = x.reset_index(drop=True)
            ratio_true = (d + r) / (x["d24"] + x["r24"]).sum()
            for s in range(n_sim):
                donor = pool[st] if st in pool else pool[DONORS[rng.integers(len(DONORS))]]
                bt = _batches(x, donor, rng)
                g = rng.normal(0, 0.04)
                pri = pd.DataFrame({"unit_id": x["unit_id"], "name": x["unit_id"],
                                    "s0": np.clip(x["d24"] / (x["d24"] + x["r24"]) - g, 0.01, 0.99),
                                    "V": x["d24"] + x["r24"]})
                ratio = ratio_true * float(np.exp(rng.normal(0, 0.08)))
                v2 = (g + rng.normal(0, GB_SD), V2_SD)
                ev = events(bt, rng)
                cum = ((ev["d"] + ev["r"]).cumsum() / (d + r)).to_numpy()
                for c in CHECK:
                    rows = us._frame([]) if c == 0 else rows_at(ev, int(np.searchsorted(cum, c)) + 1)
                    vts = (False, True) if st in SPLIT_FEEDS else (False,)
                    for vt, tau, uf, rho in itertools.product(vts, GRID_TAU, GRID_UF, GRID_RHO):
                        for ver, sw in (("v1", None), ("v2", v2)):
                            out, _ = us.project(rows, _cfg(st, tau, rho, uf, vt, ratio, sw), pri)
                            res.append({"st": st, "district": H.did(st.upper(), cd), "sim": s, "check": c,
                                        "version": ver, "vote_type": vt, "tau": tau, "rho": rho, "sigma_uf0": uf,
                                        "err": out["share_hat"] - final, "sd": out["share_sd"],
                                        "P": out["p_win"], "win": final > 0.5})
        print(f"replay {st}: {len({r['district'] for r in res if r['st'] == st})} districts", flush=True)
    print(f"dropped (replay data ≠ certified 2024 share): {dropped}")
    df = pd.DataFrame(res)
    df["z"] = df["err"] / df["sd"]
    df["cover90"] = df["z"].abs() < 1.645
    df["group"] = np.where(df["st"].isin(SPLIT_FEEDS), "split", "nosplit")
    PROC.mkdir(parents=True, exist_ok=True)
    df.to_csv(PROC / "replay.csv.gz", index=False)
    choose(df)
    return df


def choose(df: pd.DataFrame | None = None) -> dict:
    """Per feed group (split / no split) and version: among settings covering ≥ 85 % at each of 5/25/50 % counted,
    the smallest mean |error| over those checkpoints, then the smallest tau (analysis/us_calibrate.py's rule)."""
    df = pd.read_csv(PROC / "replay.csv.gz") if df is None else df
    keys = ["group", "version", "vote_type", "tau", "rho", "sigma_uf0"]
    live = df[df["check"] > 0]
    by = live.groupby(keys + ["check"]).agg(cover90=("cover90", "mean"), mae=("err", lambda e: e.abs().mean())) \
        .reset_index()
    summ = by.groupby(keys).agg(min_cover=("cover90", "min"), mae=("mae", "mean")).reset_index()
    chosen = {}
    for (grp, ver), s in summ.groupby(["group", "version"]):
        ok = s[s["min_cover"] >= 0.85]
        best = (ok if len(ok) else s).sort_values(["mae", "tau", "sigma_uf0"]).iloc[0]
        sel = df[(df["group"] == grp) & (df["version"] == ver) & (df["vote_type"] == best["vote_type"])
                 & (df["tau"] == best["tau"]) & (df["rho"] == best["rho"]) & (df["sigma_uf0"] == best["sigma_uf0"])]
        tab = sel.groupby("check").agg(cover90=("cover90", "mean"), mae=("err", lambda e: e.abs().mean()),
                                       bias=("err", "mean"), n=("err", "size"))
        by_state = sel[sel["check"] > 0].groupby("st").agg(cover90=("cover90", "mean"),
                                                           mae=("err", lambda e: e.abs().mean()),
                                                           districts=("district", "nunique"))
        chosen[f"{grp}|{ver}"] = {"vote_type": bool(best["vote_type"]), "tau": float(best["tau"]),
                                  "rho": float(best["rho"]), "sigma_uf0": float(best["sigma_uf0"]),
                                  "min_cover": round(float(best["min_cover"]), 3),
                                  "by_check": {str(k): {kk: round(float(vv), 4) for kk, vv in v.items()}
                                               for k, v in tab.to_dict("index").items()},
                                  "by_state": {k: {kk: round(float(vv), 4) for kk, vv in v.items()}
                                               for k, v in by_state.to_dict("index").items()}}
        print(f"{grp} {ver}: vote_type={bool(best['vote_type'])} tau={best['tau']} rho={best['rho']} "
              f"sigma_uf0={best['sigma_uf0']} (min coverage {best['min_cover']:.2f})")
        print(tab.round(4).to_string())
        print(by_state.round(4).to_string())
    (PROC / "replay_choice.json").write_text(json.dumps(chosen, indent=1))
    PUBLIC_CAL.write_text(json.dumps({"built": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "rule": choose.__doc__.split("\n")[0] + " " + " ".join(
                                          choose.__doc__.split("\n")[1:]).strip(),
                                      "n_sim": int(df["sim"].max() + 1), "districts": sorted(df["district"].unique()),
                                      "chosen": chosen}, indent=1))
    return chosen


PUBLIC_CAL = us.ROOT_DIR / "data_public" / "us2026_house_calibration.json"
MIN_P, MAX_P = 0.05, 0.95
GROUP_LABEL = {"split": "feeds with an early-vote split (TX, NC, GA, AZ), {n} replayed districts",
               "nosplit": "feeds without a vote-type split (FL, SC, OH), {n} replayed districts"}


def targets() -> tuple[pd.DataFrame, str, str]:
    """Districts of the covered states with a Polymarket or Kalshi P(D) in [0.05, 0.95] (site/data/us2026/house/
    markets.json)."""
    m = json.loads((us.ROOT_DIR / "site" / "data" / "us2026" / "house" / "markets.json").read_text())
    rows = []
    for k, v in m["districts"].items():
        st, n = k.split("-")
        if st.lower() not in STATES or n == "AL":
            continue
        pm, ka = (v.get("pm") or {}), (v.get("kalshi") or {})
        ps = [p for p in (pm.get("p"), ka.get("p")) if p is not None]
        if any(MIN_P <= p <= MAX_P for p in ps):
            rows.append({"district": k, "st": st.lower(), "n": int(n), "pm_slug": pm.get("slug"), "pm_p": pm.get("p"),
                         "kalshi_event": ka.get("event"), "kalshi_p": ka.get("p"),
                         "cand_d": (pm.get("cands") or {}).get("D") or (ka.get("cands") or {}).get("D"),
                         "cand_r": (pm.get("cands") or {}).get("R") or (ka.get("cands") or {}).get("R")})
    return pd.DataFrame(rows), m["asof"], m["built"]


def _feed_candidates() -> dict[str, dict[str, str]]:
    """'TX-23' -> {'DEM': name, 'REP': name} from the 2026 pre-election files (TX, FL, OH, AZ)."""
    out: dict[str, dict[str, str]] = {}
    parts = {"tx": hn.parse_tx((CACHE / "tx" / "c53815.json").read_bytes()),
             "fl": hn.parse_fl((us.US_RAW / "fl" / "20261103_ElecResultsFL.txt").read_bytes()),
             "oh": hn.parse_oh((CACHE / "oh" / "vssc.xml").read_bytes()),
             "az": hn.parse_ev(_az_house_items_2026(), "az")}
    for st, rows in parts.items():
        for (race, party), g in rows[rows["party"].isin(["DEM", "REP"])].groupby(["race", "party"]):
            out.setdefault(f"{st.upper()}-{race[6:]}", {})[party] = re.sub(r"\s*\(I\)\s*$", "", g["candidate"].iat[0])
    return out


def _title(name: str | None) -> str | None:
    return None if not name else (name.title() if name.isupper() else name)


def _pm_market(slug: str) -> dict | None:
    from brunder import polymarket
    for mk in polymarket.event(slug)["markets"]:
        if re.search(r"Democratic Party win|Democrats win|\(D\)", mk.get("question") or "") and mk.get("active"):
            return {"market": mk["slug"], "question": mk["question"]}
    return None


_CFG = """# US midterms 2026-11-03, {did} U.S. House: model {ver}. {head}
# Counted units = the district's counties (a split county: only its part in the district, as the feed reports it).
# Prior = 2024 president in each county's part of the 2026 district (data_public/us2026_house_priors.csv,
# analysis/us_house_night.py priors; same allocation as site/data/us2026/house/districts.json). Settings from the
# 2024 replay of the same feed's House contests (analysis/us_house_night.py replay; data_public/us2026_house_calibration.json):
# {cal}
# Frozen before polls close; the integrator registers it (prereg/registry.jsonl). Nothing here was chosen on 2026 count
# data. Market selection: P(D) {p_txt} in markets.json built {built} (rule: Polymarket or Kalshi in [0.05, 0.95]).
id = "us2026_house_{st}-{nn}_{ver}"

[election]
eid = "us2026"
race_id = "{rid}"
name = "{did} U.S. House"
state = "{st}"
race = "house-{nn}"             # race key in the parsed feed (brunder.us_house_night)
feed = "{st}_house"             # brunder.us.FEEDS: the state's recorded feed, House contests
poll_close_utc = "{close}"
a = {{ party = "DEM", name = "{a}", color = "#1f5fbf" }}
b = {{ party = "REP", name = "{b}", color = "#c8102e" }}

[prior]
share = "{src}"     # 2024 president (Harris v Trump), county parts of the 2026 district
votes = "{src}"     # its two-party votes × turnout_ratio = expected 2026 two-party votes
{prior_extra}
[model]                    # brunder.nowcast.Params
tau = {tau}
rho = {rho}
sigma_mun0 = {sigma}
sigma_uf0 = {uf}                # prior sd of a region's swing (one district = one region; early/late with vote_type)
{model_extra}
[count]                    # brunder.us.CountRules
turnout_ratio = {turnout}         # 2026 midterm / 2024 president two-party votes (brunder.us.EST_TURNOUT)
f_max = 0.95
f_complete = 0.98
use_outstanding = false
learn_turnout = true
vote_type = {vt}
{early}
[markets.polymarket]
event = "{pm_event}"
market = "{pm_market}"
question = "{pm_question}"
yes_is = "a"

[markets.kalshi]           # timeline line needs a pair in configs/pairs/us2026.toml (race_id "{rid}")
ticker = ""                # Kalshi {k_txt}
"""


def write_configs() -> list[Path]:
    tg, asof, built = targets()
    calj = json.loads(PUBLIC_CAL.read_text())
    cal = calj["chosen"]
    n_grp = {g: sum((d[:2].lower() in SPLIT_FEEDS) == (g == "split") for d in calj["districts"]) for g in GROUP_LABEL}
    feed_c = _feed_candidates()
    out = []
    for t in tg.itertuples():
        st, nn = t.st, f"{t.n:02d}"
        fc = feed_c.get(t.district, {})
        a, b = _title(t.cand_d) or _title(fc.get("DEM")) or "Democratic candidate", \
            _title(t.cand_r) or _title(fc.get("REP")) or "Republican candidate"
        pm = _pm_market(t.pm_slug) if t.pm_slug else None
        grp = "split" if st in SPLIT_FEEDS else "nosplit"
        p_txt = ", ".join(f"{k} {v:.3f}" for k, v in (("Polymarket", t.pm_p), ("Kalshi", t.kalshi_p))
                          if v is not None and np.isfinite(v))
        for ver in ("v1", "v2"):
            c = cal[f"{grp}|{ver}"]
            bc = c["by_check"]
            cal_txt = (f"{GROUP_LABEL[grp].format(n=n_grp[grp])}; "
                       f"90 % coverage {bc['0.05']['cover90']:.2f}/{bc['0.25']['cover90']:.2f}/{bc['0.5']['cover90']:.2f} "
                       f"at 5/25/50 % counted, mean |error| {bc['0.05']['mae']:.3f}/{bc['0.25']['mae']:.3f}/{bc['0.5']['mae']:.3f}.")
            if ver == "v1":
                head = "No swing: the count alone moves the 2024 president baseline."
                prior_extra, model_extra = "", ""
            else:
                head = ("Identical to v1 except a prior on the district swing = the generic-ballot shift "
                        "(markets.json generic_ballot: average − national 2024 Harris two-party share) with its error "
                        "and the spread of House vs president results.")
                prior_extra = (f"generic_ballot_shift = {GB_SHIFT}   # VoteHub generic-ballot average 0.5477 − 0.4925 "
                               f"(asof {asof})\n")
                model_extra = (f"swing_prior_mean = {GB_SHIFT}   # = generic_ballot_shift\n"
                               f"swing_prior_sd = {V2_SD}     # sqrt({GB_SD}^2 generic-ballot error + {HVP_SD}^2 House vs "
                               "president, 2024)\n")
            vt = bool(c["vote_type"]) and st in SPLIT_FEEDS
            text = _CFG.format(did=t.district, ver=ver, head=head, cal=cal_txt, p_txt=p_txt, built=built, st=st,
                               nn=nn, rid=hn.race_id(st, t.n), close=us.POLL_CLOSE.get(st, "2026-11-04T02:00:00Z"),
                               a=a.replace('"', "'"), b=b.replace('"', "'"), src=hn.prior_source(st, t.n),
                               prior_extra=prior_extra, tau=c["tau"], rho=c["rho"], sigma=SIGMA_MUN0, uf=c["sigma_uf0"],
                               model_extra=model_extra, turnout=TURNOUT, vt=str(vt).lower(),
                               early=f"early_share = {COUNT[st]['early_share']}\n" if vt else "",
                               pm_event=t.pm_slug or "", pm_market=(pm or {}).get("market", ""),
                               pm_question=(pm or {}).get("question", "").replace('"', "'"),
                               k_txt=f"event {t.kalshi_event}, YES = {t.kalshi_event}-D" if t.kalshi_event
                               else "market: none")
            HOUSE_CONFIGS.mkdir(exist_ok=True)
            f = HOUSE_CONFIGS / f"{st}-{nn}_{ver}.toml"
            f.write_text(text)
            out.append(f)
    print(f"{len(out)} configs for {len(tg)} districts")
    return out


# ------------------------------------------------------------------ live cost (the house-live unit's budget)

def by_competitiveness() -> list[Path]:
    """House v1 configs, most competitive market first (|P(D) − 0.5| of the mean of Polymarket and Kalshi)."""
    tg, _, _ = targets()
    tg["p"] = tg[["pm_p", "kalshi_p"]].mean(axis=1)
    tg = tg.assign(k=(tg["p"] - 0.5).abs()).sort_values(["k", "district"])
    return [HOUSE_CONFIGS / f"{t.st}-{t.n:02d}_v1.toml" for t in tg.itertuples()]


def bench(night: float = 240.0, n: int | None = None) -> dict:
    """The house-live process on a rehearsal archive (analysis/us_night_replay.feed, House bodies included, written
    by a separate process): one update+write per simulated minute = one live cycle (the recorder saves every 30 s;
    the exporter steps per minute). CPU seconds per cycle, peak RSS, timeline size, non-null output at the end.
    n: only the n most competitive districts."""
    import resource
    import shutil
    import subprocess
    import time
    base = Path.home() / "brunder-data-local" / "tmp"
    w = (base if base.exists() else PROC) / "hn_bench"
    shutil.rmtree(w, ignore_errors=True)
    subprocess.run([sys.executable, "-c", "from us_night_replay import feed; from pathlib import Path; "
                    f"feed(Path({str(w / 'snap')!r}), minutes=0.3, night={night})"],
                   cwd=Path(__file__).resolve().parent, check=True)
    cfgs = by_competitiveness()[:n]
    ex = hn.HouseExporter(cfgs, w / "snap", max_retries=1)
    arc = us.Archive(w / "snap")
    arc.refresh()
    ts = arc.times()
    grid = pd.date_range(ts[0].ceil("1min"), ts[-1], freq="1min")
    cpu, wall = [], []
    for i, t in enumerate(grid):
        c0, w0 = time.process_time(), time.monotonic()
        ex.update(t)
        ex.write(w / "out")
        cpu.append(time.process_time() - c0)
        wall.append(time.monotonic() - w0)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e6 if sys.platform == "darwin" else 1e3)
    d = w / "out" / us.EID / hn.HOUSE_DIR
    tl = json.loads((d / "timeline.json").read_text())
    null = [rid for rid, rows in tl.items() if not rows or any(rows[-1].get(k) is None for k in
                                                               ("counted", "share", "sd", "P", "P_v2"))]
    out = {"races": len(cfgs), "cycles": len(grid), "cpu_s_median": round(float(np.median(cpu[10:])), 2),
           "cpu_s_p95": round(float(np.percentile(cpu[10:], 95)), 2), "cpu_s_max": round(float(max(cpu)), 2),
           "wall_s_median": round(float(np.median(wall[10:])), 2), "peak_rss_mb": round(rss, 0),
           "timeline_mb": round((d / "timeline.json").stat().st_size / 1e6, 2), "null_races": null,
           "final_counted_min": min((rows[-1]["counted"] or 0) for rows in tl.values()),
           "order": [p.name for p in cfgs]}
    print(json.dumps({k: v for k, v in out.items() if k != "order"}, indent=1))
    (PROC / "bench.json").write_text(json.dumps(out, indent=1))
    return out


STEPS = {"priors": build_priors, "verify": verify, "replay": replay, "choose": choose, "configs": write_configs,
         "bench": bench}

if __name__ == "__main__":
    for step in sys.argv[1:] or list(STEPS):
        STEPS[step]()
