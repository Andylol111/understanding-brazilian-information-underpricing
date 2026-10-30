"""civicAPI (civicapi.org, secondary US midterms source; src/brunder/civic.py): priors, configs, checks, and the
cross-validation against the official feeds after the night.

  uv run python analysis/us_civic.py priors     # data_public/us2026_civic_priors.csv (2024 president by county)
  uv run python analysis/us_civic.py check      # live: every race's counties map to ours, candidates vs the registry
  uv run python analysis/us_civic.py validate   # civicAPI past races vs certified results (BRUNDER_DATA=~/brunder-data-local)
  uv run python analysis/us_civic.py configs    # configs/us2026_civic/*_v1..v3.toml
  uv run python analysis/us_civic.py compare    # after the night: civicAPI vs the official feeds (totals + lag)
  uv run python analysis/us_civic.py history    # after the night: civicAPI's own snapshot history of each race

Attribution: civicAPI (civicapi.org), https://civicapi.org (free for any use; attribution required).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from brunder import civic, us

ROOT = us.ROOT_DIR
CIVIC_DIR = ROOT / "configs" / "us2026_civic"
PRIORS = us.CIVIC_PRIORS
BASELINE = us.COUNTY_BASELINE
UA = civic.UA


# ------------------------------------------------------------------ priors

def build_priors() -> Path:
    """Source <st>_pres_2024 for each civicAPI-only state: 2024 president (Harris, Trump, all) by county, copied from
    the committed county baseline (MEDSL precinct returns summed to Census counties, CC0; the baseline's own check
    against official statewide totals is in its header). Same rule as Ohio's prior (oh_pres_2024)."""
    b = pd.read_csv(BASELINE, comment="#")
    parts = []
    for st in civic.STATES:
        c = us.counties(st)
        d = b[b["state"] == st].set_index("unit_id").reindex(c["unit_id"])
        if d["pres24_total"].isna().any():
            raise SystemExit(f"{st}: counties without a 2024 president baseline: {list(d.index[d['pres24_total'].isna()])}")
        parts.append(pd.DataFrame({"source": f"{st}_pres_2024", "unit_id": d.index,
                                   "dem": d["pres24_dem"].astype(int).to_numpy(),
                                   "rep": d["pres24_rep"].astype(int).to_numpy(),
                                   "total": d["pres24_total"].astype(int).to_numpy()}))
    head = ["# US midterms 2026 county priors for the states recorded only through civicAPI (brunder.civic): 2024 president by",
            "# county (Harris, Trump, all candidates), copied from data_public/us2026_county_baseline.csv: MIT Election Data and",
            "# Science Lab, U.S. President Precinct-Level Returns 2024 (doi:10.7910/DVN/XDJYKC; CC0 1.0), summed to Census",
            "# county FIPS by this project. Columns as in us2026_priors.csv; source <st>_pres_2024.",
            "# Not the election results themselves: these only give the model each county's usual lean and size."]
    PRIORS.write_text("\n".join(head) + "\n" + pd.concat(parts).to_csv(index=False))
    return PRIORS


# ------------------------------------------------------------------ check (live)

def _fetch(web: httpx.Client, rid: int) -> dict:
    r = web.get(f"{civic.BASE}/race/{rid}")
    r.raise_for_status()
    return r.json()


def _plain(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s)


def same_person(a: str, b: str) -> bool:
    """Last names equal and first names equal or one a prefix of the other. "Robert Charles" ~ "Bobby Charles" is NOT
    matched: it is reported, for a human to confirm."""
    ta, tb = _plain(a).split(), _plain(b).split()
    if not ta or not tb:
        return False
    return ta[-1] == tb[-1] and (ta[0] == tb[0] or ta[0].startswith(tb[0]) or tb[0].startswith(ta[0]))


def registry_race(key: str) -> dict | None:
    reg = json.loads(us.RACES_REGISTRY.read_text())["races"]
    return next((r for r in reg if r["race_key"] == key), None)


def check_candidates(d: dict, reg: dict | None) -> list[str]:
    """Findings on the candidates civicAPI lists for a race against our race registry (data_public/us2026_races.json)."""
    out = []
    by_party: dict[str, list[str]] = {}
    for c in civic.candidates(d):
        by_party.setdefault(us.party_code(c["party"]), []).append(c["name"])
    for p in ("DEM", "REP"):
        if len(by_party.get(p, [])) != 1:
            out.append(f"{p}: {len(by_party.get(p, []))} candidates listed {by_party.get(p, [])}; the two-party model "
                       "sums every line of the party")
    if reg is None:
        return out + ["no registry race"]
    cands = reg["candidates"] or {}
    for letter, code in (("D", "DEM"), ("R", "REP")):
        want = cands.get(letter)
        got = by_party.get(code, [])
        if want and not any(same_person(want, g) for g in got):
            out.append(f"registry {letter} = {want!r} but civicAPI lists {got}")
    for letter, name in cands.items():
        if letter not in ("D", "R") and not any(same_person(name, c["name"]) for c in civic.candidates(d)):
            out.append(f"registry {letter} = {name!r} is not among civicAPI's candidates")
    return out


def check() -> int:
    """Live check of every race: ids, county mapping (FIPS -> our unit_id), candidates against the registry."""
    bad = 0
    with httpx.Client(headers=UA, timeout=30) as web:
        for key, rid in civic.RACES.items():
            if rid is None:
                print(f"{key:8} no race id (not listed in civicAPI)")
                continue
            d = _fetch(web, rid)
            time.sleep(0.5)
            if key == civic.HOUSE_KEY:
                print(f"{key:8} {rid:>6} {len(d['region_results'])} districts, test_data={d['test_data']}, "
                      f"last_updated {d['last_updated']}")
                continue
            st, _ = civic.race_office(key)
            mapped, unmapped = civic.county_rows(d, st)
            ours = set(us.counties(st)["unit_id"])
            missing = sorted(ours - {u for u, _ in mapped})
            by_fips = sum(1 for v in d["region_results"].values() if v.get("type") == "County" and v.get("fips"))
            wrong = civic._wrong_race(key, d)
            names = us.counties(st).set_index("unit_id")["key"]
            odd = [f"{v.get('name')}→{u}" for u, v in mapped if us._norm(v.get("name") or "") != names[u]]
            bad += bool(unmapped or missing or wrong or odd)
            print(f"{key:8} {rid:>6} {d['election_name']!r}: {len(mapped)}/{len(ours)} counties mapped ({by_fips} by FIPS), "
                  f"unmapped {unmapped or '-'}, missing {missing or '-'}, test_data={d['test_data']}, "
                  f"last_updated {d['last_updated'][:10]}{', WRONG RACE: ' + wrong if wrong else ''}"
                  f"{', NAME≠FIPS: ' + str(odd) if odd else ''}")
            for line in check_candidates(d, registry_race(key)):
                print(f"           candidates: {line}")
    print(f"{bad} races with a mapping problem")
    return bad


# ------------------------------------------------------------------ validation of civicAPI against certified results

TOL = 0.02                                  # a major candidate's statewide total may differ by at most 2 %
CLARITY = "https://www.enr-scvotes.org/SC"


def civic_long(d: dict, st: str, by: str = "party") -> pd.DataFrame:
    """County × key votes of a civicAPI race document; key = party code ("party") or the candidate's last name ("name")."""
    mapped, _ = civic.county_rows(d, st)
    rows = []
    for uid, v in mapped:
        for c in v.get("candidates") or []:
            k = us.party_code(c.get("party")) if by == "party" else _plain(c["name"]).split()[-1]
            rows.append((uid, k, float(c.get("votes") or 0)))
    return pd.DataFrame(rows, columns=["unit_id", "key", "votes"]).groupby(["unit_id", "key"], as_index=False)["votes"].sum()


def cert_two_party(df: pd.DataFrame) -> pd.DataFrame:
    """(unit_id, dem, rep) frame -> long form with party keys."""
    return pd.concat([df[["unit_id"]].assign(key=k, votes=df[c].astype(float)) for k, c in (("DEM", "dem"), ("REP", "rep"))])


def cert_medsl22(st: str, office: str, fusion: dict | None = None) -> pd.DataFrame:
    """2022 governor / U.S. Senate by county from MEDSL's certified 2022 precinct returns (CC0), D and R."""
    import io
    import zipfile
    f = us.US_RAW / "medsl22" / f"2022-{st}-local-precinct-general.zip"
    z = zipfile.ZipFile(f if f.exists() else io.BytesIO(us._fetch(
        f"https://raw.githubusercontent.com/MEDSL/2022-elections-official/main/individual_states/{f.name}", f)))
    df = pd.read_csv(z.open([n for n in z.namelist() if n.endswith(".csv")][0]), dtype=str,
                     usecols=["office", "mode", "votes", "county_fips", "party_simplified", "candidate", "precinct",
                              "jurisdiction_fips", "stage"])
    df = df[(df["office"].str.strip().str.upper() == office) & (df["stage"].fillna("GEN") == "GEN")]
    if fusion:                                      # NY: a candidate's votes on every party line (Working Families, Conservative)
        cand = df["candidate"].astype(str).str.upper()
        df = df.assign(party_simplified=np.select([cand.str.contains(n) for n in fusion.values()], list(fusion), "OTHER"))
    return cert_two_party(us._medsl_county(df, st))


def cert_pres24(st: str) -> pd.DataFrame:
    """2024 president by county, MEDSL County Presidential Election Returns 2000-2024 (certified county results, CC0)."""
    d = pd.read_csv(us.US_RAW / "medsl" / "countypres_2000-2024.csv", dtype=str)
    d = d[(d["year"] == "2024") & (d["state_po"] == st.upper())]
    d = d[d["mode"].str.upper().isin(["TOTAL"]) | ~d.groupby("county_fips")["mode"].transform(lambda s: (s.str.upper() == "TOTAL").any())]
    d = d.assign(votes=pd.to_numeric(d["candidatevotes"]), unit_id=f"us-{st}-" + d["county_fips"].str.zfill(5))
    p = d.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return cert_two_party(pd.DataFrame({"unit_id": p.index, "dem": p["DEMOCRAT"].to_numpy(), "rep": p["REPUBLICAN"].to_numpy()}))


def cert_prior(source: str) -> pd.DataFrame:
    p = pd.read_csv(us.PUBLIC_PRIORS, comment="#")
    return cert_two_party(p[p["source"] == source])


def cert_clarity(eid: int, contest: str) -> pd.DataFrame:
    """SC Election Commission certified county results (Clarity ENR) of the contest named `contest` (e.g. 'U.S.  Senate  - REP'):
    county × candidate last name."""
    ver = us._fetch(f"{CLARITY}/{eid}/current_ver.txt", us.US_RAW / "sc" / f"{eid}_current_ver.txt").decode().strip()
    summ = json.loads(us._fetch(f"{CLARITY}/{eid}/{ver}/json/en/summary.json", us.US_RAW / "sc" / f"{eid}_{ver}_summary.json"))
    allj = json.loads(us._fetch(f"{CLARITY}/{eid}/{ver}/json/ALL.json", us.US_RAW / "sc" / f"{eid}_{ver}_ALL.json"))
    c = next(x for x in summ if x["C"] == contest)
    ids = us._ids("sc")
    rows = []
    for cty in allj["Contests"]:
        if us._norm(cty["A"]) in ids and c["K"] in cty["C"]:
            votes = cty["V"][cty["C"].index(c["K"])]
            rows += [(ids[us._norm(cty["A"])][0], _plain(re.sub(r"^[A-Z]{2,4} ", "", ch)).split()[-1], float(v))
                     for ch, v in zip(c["CH"], votes)]
    return pd.DataFrame(rows, columns=["unit_id", "key", "votes"])


def xlsx_sheets(data: bytes) -> dict[str, list[list[str | None]]]:
    """Every worksheet of an .xlsx as {name: rows of cell strings} (stdlib only; brunder.us._xlsx_rows reads just the first)."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    shared = ["".join(t.text or "" for t in si.iter(f"{ns}t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{ns}si")] if "xl/sharedStrings.xml" in z.namelist() else []
    targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{ns}sheet"):
        path = "xl/" + targets[sh.get(rel)].lstrip("/").removeprefix("xl/")
        rows = []
        for r in ET.fromstring(z.read(path)).iter(f"{ns}row"):
            row: dict[int, str | None] = {}
            for c in r.findall(f"{ns}c"):
                col = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group():
                    col = col * 26 + ord(ch) - 64
                v = c.find(f"{ns}v")
                if v is None:
                    isel = c.find(f"{ns}is")
                    val = "".join(x.text or "" for x in isel.iter(f"{ns}t")) if isel is not None else None
                else:
                    val = shared[int(v.text)] if c.get("t") == "s" else v.text
                row[col - 1] = val
            rows.append([row.get(i) for i in range(max(row) + 1)] if row else [])
        out[sh.get("name")] = rows
    return out


def validate_pair(label: str, st: str, d: dict, cert: pd.DataFrame, by: str, major: float = 0.05) -> dict:
    """civicAPI race document `d` against certified results: counties present, and for every key with at least
    `major` of the certified total (Democrat and Republican in a general election, each real candidate in a primary) the
    statewide total difference and the county-level differences."""
    a = civic_long(d, st, by)
    ours = set(us.counties(st)["unit_id"])
    miss = sorted(ours - set(a["unit_id"]))
    sc, sa = cert.groupby("key")["votes"].sum(), a.groupby("key")["votes"].sum()
    keys = [k for k in sc.index if sc[k] >= major * sc.sum()] if by == "name" else ["DEM", "REP"]
    diffs = {k: float(sa.get(k, 0) / sc[k] - 1) for k in keys}
    j = a.merge(cert, on=["unit_id", "key"], how="outer", suffixes=("_civic", "_cert")).fillna({"votes_civic": 0, "votes_cert": 0})
    j = j[j["key"].isin(keys)]
    j["rel"] = (j["votes_civic"] - j["votes_cert"]).abs() / j["votes_cert"].where(j["votes_cert"] >= 50)
    sd = (j["votes_civic"] - j["votes_cert"]).abs().sum() / j["votes_cert"].sum()
    return {"race": label, "state": st, "counties": f"{len(ours) - len(miss)}/{len(ours)}",
            "statewide_diff_pct": {k: round(100 * v, 3) for k, v in diffs.items()},
            "max_abs_statewide_pct": round(100 * max(abs(v) for v in diffs.values()), 3),
            "counties_off_gt2pct": int((j["rel"] > TOL).sum()), "county_abs_diff_share_pct": round(100 * sd, 3),
            "ok": not miss and all(abs(v) <= TOL for v in diffs.values())}


def internal_check(label: str, st: str, d: dict) -> dict:
    """No certified file: the county rows add up to the race's own statewide totals, and every county is present."""
    a = civic_long(d, st, "name").groupby("key")["votes"].sum()
    tot = {c["name"]: c["votes"] or 0 for c in d["candidates"]}
    top = {_plain(k).split()[-1]: v for k, v in tot.items()}
    big = [k for k, v in top.items() if v >= 0.05 * max(sum(top.values()), 1)]
    diff = {k: float(a.get(k, 0) / top[k] - 1) for k in big if top[k]}
    miss = sorted(set(us.counties(st)["unit_id"]) - set(civic_long(d, st, "name")["unit_id"]))
    return {"race": label, "state": st, "counties": f"{len(us.counties(st)) - len(miss)}/{len(us.counties(st))}",
            "county_sum_vs_race_total_pct": {k: round(100 * v, 3) for k, v in diff.items()}, "total_votes": sum(top.values()),
            "ok": not miss and all(abs(v) <= TOL for v in diff.values()) and sum(top.values()) > 0}


def validate_statewide(label: str, st: str, d: dict, official: dict[str, int]) -> dict:
    """civicAPI race `d` against official statewide candidate totals only (no county file at hand): counties present,
    county rows adding up to the race total, and each candidate's statewide total against the official one."""
    a = civic_long(d, st, "name").groupby("key")["votes"].sum()
    diffs = {k: round(100 * (float(a.get(k, 0)) / v - 1), 3) for k, v in official.items()}
    r = internal_check(label, st, d)
    return {**r, "statewide_diff_pct": diffs, "max_abs_statewide_pct": max(abs(v) for v in diffs.values()),
            "ok": r["ok"] and all(abs(v) <= 100 * TOL for v in diffs.values())}


# Official statewide candidate totals of counted 2026 primaries (civicAPI has no past general election for these states):
# (civicAPI race id, state, {last name: votes}, source, official?). "official" = the state's own certified / canvassed
# figure; the NV and NH rows are Wikipedia's tables (which cite the state's results page; the state sites were not
# reachable from the research agents), so they are secondary.
STATEWIDE = [
    (85787, "wi", {"crowley": 315413, "hong": 311740, "roys": 59394, "brennan": 37084, "rodriguez": 32674, "barnes": 32435},
     "WI Elections Commission canvass results, 2026 partisan primary (2026-08-27): elections.wi.gov/sites/default/files/documents/"
     "August%202026%20Partisan%20Primary_Summary%20Results.pdf", True),
    (85788, "wi", {"tiffany": 468019}, "same WEC canvass", True),
    (84841, "ks", {"marshall": 270632, "naramore": 68100}, "KS SoS, 2026 Primary Election Official Vote Totals (certified "
     "2026-09-01): sos.ks.gov/elections/26elec/2026-Primary-Election-Official-Vote-Totals.pdf", True),
    (84840, "ks", {"hamilton": 77607, "davis": 31857, "taylor": 30362}, "same KS SoS file", True),
    (84780, "ks", {"holscher": 110407, "corson": 87601, "skoog": 28399}, "same KS SoS file", True),
    (84286, "co", {"weiser": 505459, "bennet": 383138}, "CO SoS, 2026 Primary State Abstract of Votes Cast (certified): "
     "coloradosos.gov/pubs/elections/Results/2026/2026PrimaryStateAbstractofResultsCast.pdf", True),
    (84322, "co", {"hickenlooper": 472870, "gonzales": 422488}, "same CO abstract", True),
    (83110, "nv", {"ford": 118831, "hill": 44849}, "Wikipedia 2026 Nevada gubernatorial election (cites NV SoS official statewide "
     "primary results; secondary)", False),
    (83111, "nv", {"lombardo": 172150}, "same Wikipedia table", False),
    (96678, "nh", {"pappas": 100088, "manzur": 50321}, "Wikipedia 2026 US Senate election in New Hampshire (secondary)", False),
    (96679, "nh", {"sununu": 75839, "brown": 29517}, "same Wikipedia table", False),
    (96676, "nh", {"warmington": 126223}, "Wikipedia 2026 New Hampshire gubernatorial election (secondary)", False),
]

WI_CERT = Path("~/brunder-data-local/raw/us/wi_certified/county.xlsx").expanduser()   # WEC county-by-county report (plain HTTP)


def cert_wi_county(title: str, path: Path = WI_CERT) -> pd.DataFrame:
    """County × candidate last name votes of one contest ('GOVERNOR - Republican') in the WEC County by County Report."""
    sheets = xlsx_sheets(path.read_bytes())
    name = next(n for n, rows in sheets.items() if n != "Document map" and any(r and r[0] == title for r in rows[:6]))
    rows = sheets[name]
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "County")
    cands = rows[hdr + 1][3:]
    ids = us._ids("wi")
    out = []
    for r in rows[hdr + 2:]:
        if not r or not r[0] or us._norm(r[0]) not in ids:
            continue
        for c, v in zip(cands, r[3:]):
            if c and c.upper() != "SCATTERING" and v not in (None, ""):
                out.append((ids[us._norm(r[0])][0], _plain(re.sub(r"\(write-in\)", "", c, flags=re.I)).split()[-1], float(v)))
    return pd.DataFrame(out, columns=["unit_id", "key", "votes"])


def county_deviation(d: dict, st: str, cert: pd.DataFrame, min_votes: int = 50) -> dict:
    """Largest county deviations of civicAPI race `d` from a certified county × candidate table (keys: last names present
    in both): votes, % of the county's certified total of those candidates, counties over 2 % of that total."""
    a = civic_long(d, st, "name")
    keys = sorted(set(a["key"]) & set(cert["key"]))
    c, a = cert[cert["key"].isin(keys)], a[a["key"].isin(keys)]
    j = c.merge(a, on=["unit_id", "key"], how="outer", suffixes=("_cert", "_civic")).fillna({"votes_cert": 0, "votes_civic": 0})
    j["diff"] = j["votes_civic"] - j["votes_cert"]
    tot = j.groupby("unit_id")["votes_cert"].transform("sum")
    j["pct_county"] = j["diff"].abs() / tot.where(tot >= min_votes)
    byc = j.groupby("unit_id").agg(diff=("diff", lambda x: x.abs().max()), p=("pct_county", "max"))
    worst = j.loc[j["diff"].abs().idxmax()]
    wp = j.loc[j["pct_county"].idxmax()]
    names = us.counties(st).set_index("unit_id")["name"]
    return {"counties_certified": int(c["unit_id"].nunique()), "counties_civic": int(a["unit_id"].nunique()),
            "max_abs_votes": [names[worst["unit_id"]], worst["key"], float(worst["diff"])],
            "max_pct_of_county_total": [names[wp["unit_id"]], wp["key"], round(100 * float(wp["pct_county"]), 2)],
            "counties_over_2pct": int((byc["p"] > TOL).sum()), "any_difference": int((byc["diff"] > 0).sum()),
            "sum_abs_diff_pct_of_statewide": round(100 * float(j["diff"].abs().sum() / j["votes_cert"].sum()), 3)}


def wi_county(path: Path = WI_CERT) -> dict:
    """WI governor primaries, civicAPI vs the WEC county file, county level."""
    out = {}
    with httpx.Client(headers=UA, timeout=60) as web:
        for rid, title in ((85788, "GOVERNOR - Republican"), (85787, "GOVERNOR - Democratic")):
            out[rid] = county_deviation(_fetch(web, rid), "wi", cert_wi_county(title, path))
            print(rid, title, json.dumps(out[rid]))
            time.sleep(0.5)
    return out


MI_CERT = Path("~/brunder-data-local/raw/us/mi_certified/2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt").expanduser()
# Michigan Department of State, official county results of the 2026-08-04 state primary (mvic.sos.state.mi.us, "STATE PRIMARY,
# OFFICIAL", 2026-08-24). The site answers 403 to our client: this file was downloaded BY HAND by the user in a normal browser.


def cert_mi_county(office: str, party: str, path: Path = MI_CERT) -> pd.DataFrame:
    """County × candidate last name votes of one party's contest (office description starting 'GOVERNOR' / 'UNITED STATES SENATOR') in the MI county file
    (tab-separated, one title line, CRLF); write-ins and scattering are dropped."""
    d = pd.read_csv(path, sep="\t", skiprows=1, dtype=str, keep_default_na=False)
    d = d[d["OfficeDescription"].str.upper().str.startswith(office) & (d["PartyDescription"].str.upper() == party.upper())]
    d = d[d["WriteIn(W)/Uncommitted(Z)"] == ""]
    ids = us._ids("mi")
    d = d.assign(unit_id=d["CountyName"].map(lambda n: ids[us._norm(n)][0]), votes=pd.to_numeric(d["CandidateVotes"]),
                 key=d["CandidateLastName"].map(lambda n: _plain(n).split()[-1]))
    return d[["unit_id", "key", "votes"]]


MI_TURNOUT = MI_CERT.with_name("2026STATE_PRIMARY_MI_CENR_TURNOUT.txt")


def mi_turnout_check(path: Path = MI_TURNOUT) -> dict:
    """Reconcile the county file with the turnout file (ballots cast per county, one title line skipped): in every county the
    two parties' votes in the governor (and Senate) primaries cannot exceed the ballots cast (a primary voter takes one
    party's ballot), and civicAPI's county totals cannot either. Not used: the title lines' 'TOTAL VOTER TURNOUT' figures
    (the county file's is votes summed over all offices)."""
    t = pd.read_csv(path, sep="\t", skiprows=1, dtype=str)
    ids = us._ids("mi")
    t = t[t["County Name"].fillna("").str.upper().str.endswith(" COUNTY") & t["County Voters"].fillna("").str.fullmatch(r"\d+")]
    ballots = pd.Series({ids[us._norm(re.sub(r" COUNTY$", "", n, flags=re.I))][0]: float(v)
                         for n, v in zip(t["County Name"], t["County Voters"])})
    res = {"counties": int(len(ballots)), "ballots_total": int(ballots.sum())}
    with httpx.Client(headers=UA, timeout=60) as web:
        for office, ids_ in (("GOVERNOR", (84668, 84669)), ("UNITED STATES SENATOR", (84778,))):
            off = sum(cert_mi_county(office, p).groupby("unit_id")["votes"].sum() for p in ("Democratic", "Republican")
                      ) if office == "GOVERNOR" else cert_mi_county(office, "Democratic").groupby("unit_id")["votes"].sum()
            civ = sum(civic_long(_fetch(web, i), "mi", "name").groupby("unit_id")["votes"].sum() for i in ids_)
            res[office] = {"official_over_ballots": int((off > ballots).sum()), "civic_over_ballots": int((civ > ballots).sum()),
                           "max_official_share_of_ballots": round(float((off / ballots).max()), 3)}
            time.sleep(0.5)
    return res


OH_DIR = Path("~/brunder-data-local/raw/us/oh_certified").expanduser()
# Ohio Secretary of State, "Summary Level Official Results for 2026 Primary Election" (official canvass, certified 2026-06-03),
# one workbook per party; sheet "Master" has one row per county. The site answers 403 to our client: these files were
# downloaded BY HAND by the user in a normal browser.


def cert_oh_county(party: str, contest: str, path: Path | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """(county × candidate last name votes, ballots counted per county) of one contest ('Governor', 'U.S. Senator') in the
    Ohio canvass workbook of `party` ('democratic' / 'republican'): the "Master" sheet, columns grouped by contest under a
    title row; (WI) write-in columns are dropped; a governor ticket "Amy Acton and David Pepper (D)" is named by its first
    candidate. The Total and Percentage rows are not used."""
    f = path or OH_DIR / f"summary-level-official-results-2026-primary---{party}.xlsx"
    rows = xlsx_sheets(f.read_bytes())["Master"]
    titles, head = rows[0], rows[1]
    cur, group = None, {}
    for i in range(6, len(head)):
        if i < len(titles) and titles[i]:
            cur = titles[i].strip()
        group[i] = cur
    cols = [i for i, g in group.items() if g and g.startswith(contest) and head[i] and "(WI)" not in head[i]]
    ids = us._ids("oh")
    out, ballots = [], {}
    for r in rows[4:]:
        if not r or not r[0] or us._norm(r[0]) not in ids:
            continue
        uid = ids[us._norm(r[0])][0]
        ballots[uid] = float(r[4])
        for i in cols:
            name = re.sub(r"\s*\([A-Z]+\)\s*$", "", head[i]).split(" and ")[0]
            if i < len(r) and r[i] not in (None, ""):
                out.append((uid, _plain(name).split()[-1], float(r[i])))
    return pd.DataFrame(out, columns=["unit_id", "key", "votes"]), pd.Series(ballots)


OH_RACES = [(68682, "republican", "Governor"), (68681, "democratic", "Governor"), (68678, "democratic", "U.S. Senator")]


def oh_county(path_dir: Path = OH_DIR) -> list[dict]:
    """OH primaries, civicAPI vs the official county canvass: statewide bar, 88 counties present, county deviations, and
    each county's contest votes against its ballots counted."""
    out = []
    with httpx.Client(headers=UA, timeout=60) as web:
        for rid, party, contest in OH_RACES:
            d = _fetch(web, rid)
            cert, ballots = cert_oh_county(party, contest, path_dir / f"summary-level-official-results-2026-primary---{party}.xlsx")
            r = {**validate_pair(f"{d['election_name']} {d['election_date'][:10]} ({rid})", "oh", d, cert, "name"),
                 "county_deviation": county_deviation(d, "oh", cert), "civic_percent_reporting": d["percent_reporting"],
                 "official_over_ballots": int((cert.groupby("unit_id")["votes"].sum() > ballots).sum()),
                 "source": "OH SoS official canvass workbook, downloaded by hand by the user", "official_source": True}
            out.append(r)
            print(json.dumps(r, default=str))
            time.sleep(0.5)
    return out


MI_RACES = [(84668, "GOVERNOR", "Democratic"), (84669, "GOVERNOR", "Republican"), (84778, "UNITED STATES SENATOR", "Democratic")]


def mi_county(path: Path = MI_CERT) -> list[dict]:
    """MI primaries, civicAPI vs the official county file: statewide bar (each candidate >= 5 % within 2 %), completeness of
    the 83 counties, county deviations."""
    out = []
    with httpx.Client(headers=UA, timeout=60) as web:
        for rid, office, party in MI_RACES:
            d = _fetch(web, rid)
            cert = cert_mi_county(office, party, path)
            r = {**validate_pair(f"{d['election_name']} {d['election_date'][:10]} ({rid})", "mi", d, cert, "name"),
                 "county_deviation": county_deviation(d, "mi", cert), "civic_percent_reporting": d["percent_reporting"],
                 "source": "MI Dept of State official county file, downloaded by hand by the user", "official_source": True}
            out.append(r)
            print(json.dumps(r, default=str))
            time.sleep(0.5)
    tc = mi_turnout_check()
    out.append({"race": "MI county file vs turnout file", "state": "mi", **tc,
                "ok": tc["counties"] == 83 and not any(tc[o]["official_over_ballots"] for o in ("GOVERNOR", "UNITED STATES SENATOR"))})
    print(json.dumps(out[-1]))
    return out


# civicAPI past races with a certified counterpart we hold (ids found with race/search?province=&type=)
CERT_RACES = [
    ("IA gov 2022", "ia", 18547, lambda: cert_medsl22("ia", "GOVERNOR"), "party"),
    ("IA sen 2022", "ia", 19605, lambda: cert_medsl22("ia", "US SENATE"), "party"),
    ("IA pres 2024", "ia", 18725, lambda: cert_pres24("ia"), "party"),
    ("NY gov 2022", "ny", 495, lambda: cert_medsl22("ny", "GOVERNOR", {"DEMOCRAT": "HOCHUL", "REPUBLICAN": "ZELDIN"}), "party"),
    ("AZ gov 2022", "az", 28651, lambda: cert_prior("az_gov_2022"), "party"),
    ("AZ pres 2024", "az", 29114, lambda: cert_pres24("az"), "party"),
    # SC 2026 primaries: the only counted SC races civicAPI holds (SC Election Commission Clarity ENR)
    ("SC sen special primary 2026-08-11 (REP)", "sc", 86330, lambda: cert_clarity(127017, "U.S.  Senate  - REP"), "name"),
    ("SC sen special runoff 2026-08-25 (REP)", "sc", 87534, lambda: cert_clarity(127179, "U.S.  Senate  - REP"), "name"),
    ("SC gov runoff 2026-06-23 (REP)", "sc", 84105, lambda: cert_clarity(126718, "Governor - REP"), "name"),
]


def validate(out: Path | None = None) -> list[dict]:
    """Certified comparisons (CERT_RACES), then for each of the ten civicAPI states the internal-consistency check of its
    counted 2026 primary races (statewide Governor / U.S. Senate)."""
    rows = []
    with httpx.Client(headers=UA, timeout=60) as web:
        for label, st, rid, cert, by in CERT_RACES:
            d = _fetch(web, rid)
            time.sleep(0.5)
            try:
                rows.append(validate_pair(label, st, d, cert(), by))
            except Exception as e:
                rows.append({"race": label, "state": st, "error": f"{type(e).__name__}: {e}", "ok": False})
        for rid, st, official, source, is_official in STATEWIDE:
            d = _fetch(web, rid)
            time.sleep(0.5)
            rows.append({**validate_statewide(f"{d['election_name']} {d['election_date'][:10]} ({rid})", st, d, official),
                         "civic_percent_reporting": d["percent_reporting"], "source": source, "official_source": is_official})
        if MI_CERT.exists():
            rows += mi_county()
        if OH_DIR.exists():
            rows += oh_county()
        for st in ("sc", "mi", "wi", "nh", "nv", "co", "ks", "me", "oh"):                # no past statewide general race in civicAPI
            races = {r["id"]: r for r in all_races(web, "%s" % st.upper()) if r["election_date"].startswith("2026")
                     and "Primary" in r["election_name"] and r["type"] in ("Governor", "US Senate", "Senate")
                     and r["percent_reporting"] >= 50 and r["has_breakdown"]}
            for r in (r for r in races.values() if r['id'] not in {x[0] for x in STATEWIDE}):
                d = _fetch(web, r["id"])
                time.sleep(0.4)
                rows.append(internal_check(f"{r['election_name']} {r['election_date'][:10]} ({r['id']})", st, d))
    for r in rows:
        print(json.dumps(r, default=str))
    if out:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rows, indent=1, default=str))
    return rows


def all_races(web: httpx.Client, province: str) -> list[dict]:
    out, off = [], 0
    while True:
        r = web.get(f"{civic.BASE}/race/search", params={"province": province, "offset": off})
        r.raise_for_status()
        out += r.json()["races"]
        if len(r.json()["races"]) < 20:
            return out
        off += 20
        time.sleep(0.2)


# ------------------------------------------------------------------ configs

# States that also have registered official-feed configs (race ids sc-sen, oh-gov, ...): their civic races carry "-civic".
OFFICIAL_ALSO = ("sc", "oh")
CONFIG_KEYS = [*civic.TARGET, "oh-sen", "oh-gov"]


def _template(key: str) -> str:
    """The v1 text of a civicAPI config. SC: the registered sc config's settings (its feed is unreachable from the
    recorder host; Ohio's now answers 403) under a race id with a "-civic" suffix; the other states have no same-office county prior, so they
    use Ohio's rule: 2024 president, turnout ratio 0.72."""
    reg = registry_race(key)
    st, off = civic.race_office(key)
    name = reg["state_name"] + (" Senate" if off == "sen" else " Governor") + (" (special)" if reg["special"] else "")
    rep = (reg["candidates"] or {}).get("R") or ""
    pm = reg["pm"]
    rid = f"{key}-civic" if st in OFFICIAL_ALSO else key
    if key == "sc-sen":
        prior = ('share = "sc_sen_2022"        # 2022 Senate (Matthews v Scott): same office, last midterm (SC Election Commission)\n'
                 'votes = "sc_gov_2022"        # 2022 governor two-party votes (midterm turnout pattern)')
    elif key == "sc-gov":
        prior = ('share = "sc_gov_2022"        # 2022 governor: same office, last midterm (SC Election Commission)\n'
                 'votes = "sc_gov_2022"')
    else:
        prior = (f'share = "{st}_pres_2024"        # 2024 president (MEDSL county returns, CC0): no same-office county prior built\n'
                 f'votes = "{st}_pres_2024"        # its two-party votes x 0.72 (mean 2022/2024 two-party ratio in CA, TX, GA official data)')
    close = us.POLL_CLOSE[st]
    stem = f"us2026_civic_{key.replace('-', '_')}"
    pending = ("# VALIDATION PENDING: the certified %s county / statewide files are not openly downloadable by script (the state "
               "site answers 403), so civicAPI's %s counts have NOT been compared with certified results. Do not rely on "
               "this config until that comparison is made.\n" % (reg["state_name"], reg["state_name"])) if st in PENDING else ""
    return f'''# US midterms 2026-11-03, {name}, results from civicAPI (civicapi.org): model v1. Same model and count rules as the registered
# official-feed configs; only the feed differs. civicAPI is a SECONDARY source (unofficial aggregator; free, attribution
# required: civicAPI, https://civicapi.org). Frozen before polls close (first close {close[:10]} {close[11:16]} UTC); the
# integrator registers it (prereg/registry.jsonl). Nothing here was chosen on 2026 count data. Settings and evidence:
# docs/us_midterms.md. Live/replay: uv run brunder us live|export configs/us2026_civic/{stem}_v1.toml
# SECONDARY SOURCE, LIVE BEHAVIOUR UNREHEARSED: civicAPI's counts were checked against certified results (docs/us_midterms.md,
# results/us_civic_validation.json) but its update cadence and lag are unknown: GET /race/{{id}}/history/ is 404 for every past
# race, so no timed snapshots of a past count exist to replay.
{pending}id = "{stem}_v1"

[election]
eid = "us2026"
race_id = "{rid}"
name = "{name}"
state = "{st}"
race = "{off}"                   # race key in the parsed feed (gov / sen)
feed = "civic_{st}"                # brunder.us.FEEDS: civicAPI race {civic.RACES[key]} (brunder.civic)
source = "civicAPI (civicapi.org)"      # secondary source; shown with its link on every display
source_url = "https://civicapi.org"
poll_close_utc = "{close}"
a = {{ party = "DEM", name = "{reg["dem_side"]["name"]}", color = "#1f5fbf" }}
b = {{ party = "REP", name = "{rep}", color = "#c8102e" }}

[prior]                    # data_public/us2026_priors.csv or us2026_civic_priors.csv
{prior}

[model]                    # brunder.nowcast.Params
tau = 0.06
rho = 0.3
sigma_mun0 = 0.08

[count]                    # brunder.us.CountRules
# civicAPI gives each county's votes and its percent reporting (0-100) and nothing else: once a county shows 99 or more
# (brunder.civic.COMPLETE_AT; its percent stays at 99 or lower in counted primaries), 98% of its vote is assumed in;
# those counties re-scale the expected vote of the rest. No early/late split, no outstanding-ballot count.
turnout_ratio = {"1.0" if st == "sc" else "0.72"}
f_max = 0.95
f_complete = 0.98
use_outstanding = false
learn_turnout = true

[markets.polymarket]
event = "{pm["event"]}"
market = "{pm["slug"]}"
question = "Will the Democrats win the {reg["state_name"]} {"Senate" if off == "sen" else "governor"} race in 2026?"
yes_is = "a"

[markets.kalshi]           # filled by the Kalshi workstream in configs/pairs/us2026.toml
ticker = ""
'''


# States whose civicAPI county data passed the validation bar (`validate`): every county present and every major candidate's
# statewide total within 2 % of the certified / official figure. Others get no config until they do.
VALIDATED = ("sc", "ia", "ny", "wi", "mi", "co", "ks", "oh")
# Configs are written but flagged "VALIDATION PENDING" while the certified comparison cannot be made (none now: MI and OH were
# validated from the official files the user downloaded by hand, their sites answering 403 to our client).
PENDING: tuple[str, ...] = ()


def write_configs(states: tuple[str, ...] = VALIDATED + PENDING) -> list[Path]:
    """v1 for every target race, then v2 and v3 by the rules of analysis/us_polls.py (its own functions, run on a
    scratch copy of the v1 files, so no other race's file is touched). SC's race ids carry "-civic": it reuses the
    poll average of the registry race."""
    import tempfile
    sys.path.insert(0, str(ROOT / "analysis"))
    import us_polls
    CIVIC_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for key in (k for k in CONFIG_KEYS if k[:2] in states):
        p = CIVIC_DIR / f"us2026_civic_{key.replace('-', '_')}_v1.toml"
        p.write_text(_template(key))
        out.append(p)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "configs").mkdir()
        (root / "prereg").mkdir()                       # no registry: nothing is frozen in the scratch root
        for d in ("data_public", "site", "data"):
            if (ROOT / d).exists():
                (root / d).symlink_to(ROOT / d)
        for p in out:
            (root / "configs" / p.name).write_text(p.read_text())
        old_root, old_polls = us.ROOT_DIR, us.load_polls

        def polls() -> dict:
            d = old_polls()
            return {**d, **{f"{k}-civic": d[k] for k in ("sc-sen", "sc-gov", "oh-sen", "oh-gov") if k in d}}
        us.ROOT_DIR, us.load_polls = root, polls
        try:
            us_polls.configs()
            us_polls.next_versions()
        finally:
            us.ROOT_DIR, us.load_polls = old_root, old_polls
        for q in sorted((root / "configs").glob("us2026_civic_*_v[23].toml")):
            key = next(k for k in CONFIG_KEYS if q.name.startswith(f"us2026_civic_{k.replace('-', '_')}_v"))
            # the v1 header's flags (live behaviour unrehearsed, validation pending) go on every version
            flags = "".join(line + "\n" for line in _template(key).split("\n# docs/us_midterms.md.", 1)[1].split("\nid = ", 1)[0]
                            .split("\n")[1:] if line)
            body = q.read_text()
            (CIVIC_DIR / q.name).write_text(body.replace("\nid = ", "\n" + flags + "id = ", 1))
            out.append(CIVIC_DIR / q.name)
    return out


# ------------------------------------------------------------------ cross-validation (after the night)

SNAP_DIR = us.SNAP / us.TAG
EMPTY = ["t", "unit_id", "dem", "rep"]


def _two_party(df: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    g = df.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"t": t, "unit_id": g.index, "dem": g.get("DEM", 0).to_numpy(), "rep": g.get("REP", 0).to_numpy()})


def civic_series(arc: us.Archive, key: str) -> pd.DataFrame:
    """Every recorded civicAPI snapshot of a race: t (the request's start), unit_id, dem, rep (two-party votes)."""
    st, off = civic.race_office(key)
    rows = []
    for t, f in arc._files.get(f"civic_{civic.RACES[key]}", []):
        df = civic.parse_race(arc.read(f), st, off)
        rows.append(_two_party(df[df["level"] == "county"], t))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=EMPTY)


def official_series(arc: us.Archive, st: str, off: str) -> pd.DataFrame:
    """The same table from the official feed's snapshots (brunder.us parsers; one parse per snapshot time)."""
    times = sorted({t for pattern in us.FEEDS[st].values() for k in arc.keys(pattern) for t, _ in arc._files[k]})
    rows = []
    for t in times:
        df = us.parse_feed(st, us.feed_blobs(arc, us.feed_files(arc, st, t)))
        df = df[(df["level"] == "county") & (df["race"] == off)]
        if not df.empty:
            rows.append(_two_party(df, t))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=EMPTY)


def lag_table(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Timing of civicAPI (a) against the official feed (b). For every county and every two-party total the official
    feed showed, the first snapshot of each source with at least that total; lag_s = civic − official, seconds
    (+: civicAPI later). A total civicAPI never reached has no row."""
    out = []
    for uid in sorted(set(a["unit_id"]) & set(b["unit_id"])):
        sa, sb = (x[x["unit_id"] == uid].sort_values("t") for x in (a, b))
        na, nb = (sa["dem"] + sa["rep"]).to_numpy(), (sb["dem"] + sb["rep"]).to_numpy()
        ta, tb = sa["t"].to_numpy(), sb["t"].to_numpy()
        for level in np.unique(nb[nb > 0]):
            ha, hb = na >= level, nb >= level
            if ha.any():
                ia, ib = int(np.argmax(ha)), int(np.argmax(hb))
                out.append({"unit_id": uid, "votes": level, "t_official": tb[ib], "t_civic": ta[ia],
                            "lag_s": (ta[ia] - tb[ib]) / np.timedelta64(1, "s")})
    return pd.DataFrame(out, columns=["unit_id", "votes", "t_official", "t_civic", "lag_s"])


def compare_race(key: str, a: pd.DataFrame, b: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """(summary row, county table of the final totals, lag table) for one race."""
    fa = a[a["t"] == a["t"].max()].set_index("unit_id")
    fb = b[b["t"] == b["t"].max()].set_index("unit_id")
    j = fa[["dem", "rep"]].merge(fb[["dem", "rep"]], left_index=True, right_index=True, how="outer",
                                suffixes=("_civic", "_official")).fillna(0)
    j["civic"], j["official"] = j["dem_civic"] + j["rep_civic"], j["dem_official"] + j["rep_official"]
    j["gap"] = j["civic"] - j["official"]
    j["gap_pct"] = j["gap"] / j["official"].where(j["official"] > 0)
    lag = lag_table(a, b)
    q = (lambda p: float(lag["lag_s"].quantile(p)) if len(lag) else np.nan)
    dem_c, dem_o = j["dem_civic"].sum() / max(j["civic"].sum(), 1), j["dem_official"].sum() / max(j["official"].sum(), 1)
    row = {"race": key, "civic_snapshots": a["t"].nunique(), "official_snapshots": b["t"].nunique(),
           "final_civic": j["civic"].sum(), "final_official": j["official"].sum(),
           "final_gap_pct": j["civic"].sum() / j["official"].sum() - 1 if j["official"].sum() else np.nan,
           "dem_share_civic": dem_c, "dem_share_official": dem_o, "dem_share_gap_pt": 100 * (dem_c - dem_o),
           "counties": len(j), "counties_exact": int((j["gap"] == 0).sum()),
           "max_abs_county_gap_pct": float(j["gap_pct"].abs().max()) if len(j) else np.nan,
           "lag_p10_s": q(0.1), "lag_median_s": q(0.5), "lag_p90_s": q(0.9)}
    return row, j.sort_values("gap", key=np.abs, ascending=False), lag


def compare(snap: Path | None = None, out_dir: Path | None = None) -> pd.DataFrame:
    """For each race of an officially recorded state: final county totals and the two-party share, civicAPI against the
    official feed, and the timing lag at every county total (lag_table). Writes results/us_civic_compare*.csv."""
    arc = us.Archive(snap or SNAP_DIR)
    arc.refresh()
    out_dir = out_dir or ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    summ = []
    for key in civic.XVAL:
        st, off = civic.race_office(key)
        rid = civic.RACES.get(key)
        if rid is None or f"civic_{rid}" not in arc._files:
            print(f"{key}: no civicAPI snapshots")
            continue
        a, b = civic_series(arc, key), official_series(arc, st, off)
        if b.empty:
            print(f"{key}: no official snapshots")
            continue
        row, counties, lag = compare_race(key, a, b)
        summ.append(row)
        counties.to_csv(out_dir / f"us_civic_compare_{key}_counties.csv")
        lag.to_csv(out_dir / f"us_civic_compare_{key}_lag.csv", index=False)
    s = pd.DataFrame(summ)
    s.to_csv(out_dir / "us_civic_compare.csv", index=False)
    print(f"Data: {civic.SOURCE_NAME}, {civic.SOURCE_URL}; official state feeds")
    print(s.round(4).to_string(index=False) if len(s) else "nothing to compare")
    return s


def history() -> None:
    """civicAPI's own snapshot history (GET /race/{id}/history/) of every race, saved raw under
    data/raw/us/civic_history/<id>.json for the timing analysis (404 "snapshot not found" = none)."""
    d = us.US_RAW / "civic_history"
    d.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=60) as web:
        for key, rid in civic.RACES.items():
            if rid is None:
                continue
            r = web.get(f"{civic.BASE}/race/{rid}/history/")
            print(f"{key:8} {rid}: HTTP {r.status_code}, {len(r.content)} bytes")
            if r.status_code == 200:
                (d / f"{rid}.json").write_bytes(r.content)
            time.sleep(1.0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["priors", "check", "validate", "wi-county", "mi-county", "oh-county", "configs", "compare", "history"])
    ap.add_argument("--states", nargs="*", default=list(VALIDATED + PENDING), help="configs: states to write (default: validated and pending ones)")
    ap.add_argument("--snap", type=Path, help="compare: recorder directory (default data/raw/snap/us_midterms_2026)")
    a = ap.parse_args()
    match a.step:
        case "priors":
            print(build_priors())
        case "check":
            sys.exit(1 if check() else 0)
        case "validate":
            validate(ROOT / "results" / "us_civic_validation.json")
        case "wi-county":
            wi_county()
        case "mi-county":
            mi_county()
        case "oh-county":
            oh_county()
        case "configs":
            for p in write_configs(tuple(a.states)):
                print(p.relative_to(ROOT))
        case "compare":
            compare(a.snap)
        case "history":
            history()
