"""US House districts on election night 2026-11-03: U.S. Representative contests from the official feeds the recorder
already saves for the statewide races (brunder.us.FEEDS), one row per county × district × candidate.

Feed kinds "<st>_house" (brunder.us.FEEDS; same snapshot keys as "<st>", except GA/AZ, whose House ballot items are
recorded under ga_enr_<Georgia|Arizona>_house_<item>) are parsed here; brunder.us.parse_feed dispatches to
parse_house. Rows have the brunder.us.COLS layout with race = "house-<NN>" and level = "county": a county split
between districts appears once per district with only that district's votes, so the brunder.us.project model runs
unchanged on the district's counties (live: `project` below, its exact numpy form; prior:
data_public/us2026_house_priors.csv, analysis/us_house_night.py).

Per feed (verified against official 2022/2024 files and the 2026 pre-election files, docs/us_midterms.md#house):
  tx  Civix countyInfo: Races "U. S. REPRESENTATIVE DISTRICT <n>" per county, C{V votes, EV early}, PR/TP = precincts
      reported / total of that race in the county
  fl  DOS results file: RaceCode USR, Juris1num = district, Precincts/PrecinctsReporting per county × district
  nc  NCSBE results_<county>.txt: "US HOUSE OF REPRESENTATIVES DISTRICT <nn> (VOTE FOR 1)", prt/ptl per contest,
      early = one-stop + mail (evc + avc)
  sc  Clarity: "U.S. House of Representatives, District <n>" in summary.json; ALL.json per county; county-wide
      precincts from status.json
  oh  NIST 1622 XML: "Representative to Congress - District <nn>", VoteCounts per county GpUnit; county-wide progress
  ga, az  Enhanced Voting ballot items "US House of Representatives - District <n>" / "U.S. Representative in
      Congress - District No. <n>" (brunder.us.parse_ga layout)
  pa  DoS API GetCountyBreak office 11, one recorded document per district (brunder.us_feeds_extra.HOUSE_URLS);
      early = mail-in; no precinct counts
  mn  SoS allracesbycounty.txt (the statewide file): "U.S. Representative District <n>", precincts per county part
  ne, nm  SoS results web service GetMapData, one recorded document per district; county-wide precinct counts

Live: its own process (deploy/brunder-us-house-live.service, `python -m brunder.us_house_night live <configs> --out
site/data`), never inside `brunder us live`, so the registered Senate/governor run is isolated from House memory and
CPU. It reads the same recorder archive and writes only site/data/us2026/house_live/ (HouseExporter).
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from . import us

HOUSE_STATES = ("tx", "fl", "nc", "sc", "oh", "ga", "az", "pa", "mn", "ne", "nm")
_RX = re.compile(r"^\s*(?:u\.?\s*s\.?|united states|us)\s+(?:house of representatives|representative)(?:\s+in congress)?"
                 r"\s*[,-]?\s*district\s*(?:no\.?)?\s*0*(\d+)\b|^\s*representative (?:to|in) congress\s*[,-]?\s*"
                 r"district\s*(?:no\.?)?\s*0*(\d+)\b", re.I)


def house_race(name: str) -> str | None:
    """'U. S. REPRESENTATIVE DISTRICT 23' / 'Representative to Congress - District 09' / … -> 'house-23'."""
    m = _RX.match(" ".join(str(name or "").split()))
    if not m:
        return None
    return f"house-{int(m.group(1) or m.group(2)):02d}"


def race_id(st: str, n: int) -> str:
    """Dashboard race id of a district: 'house:TX-07'."""
    return f"house:{st.upper()}-{n:02d}"


def prior_source(st: str, n: int) -> str:
    """Prior source name in data_public/us2026_house_priors.csv (brunder.us.baseline_label reads '2024 president')."""
    return f"{st}-{n:02d}_pres_2024"


def _row(level, uid, name, race, cand, party, votes, early, rep, units, ballots=np.nan, registered=np.nan) -> dict:
    return {"level": level, "unit_id": uid, "name": name, "race": race, "candidate": cand, "party": party,
            "votes": float(votes), "early": early, "reporting": rep, "units": units, "ballots": ballots,
            "registered": registered, "outstanding": np.nan}


def parse_tx(county: bytes) -> pd.DataFrame:
    c = us.counties("tx")
    rows = []
    for cid, x in us._b64json(json.loads(county)["upload"]).items():
        uid, name = c.loc[int(cid) - 1, ["unit_id", "name"]]
        for r in (x.get("Races") or {}).values():
            race = house_race(r.get("N", ""))
            if race is None:
                continue
            for cand in r["C"].values():
                rows.append(_row("county", uid, name, race, cand["N"], us.party_code(cand.get("P")),
                                 cand.get("V") or 0, float(cand.get("EV") or 0), us._int(r.get("PR")),
                                 us._int(r.get("TP")), registered=us._int(r.get("OTRV"))))
    return us._frame(rows)


def parse_fl(blob: bytes) -> pd.DataFrame:
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    d = pd.read_csv(io.StringIO(text), sep="\t", dtype=str, keep_default_na=False)
    d = d[d["RaceCode"].str.strip() == "USR"]
    ids = us._ids("fl")
    rows = []
    for x in d.to_dict("records"):
        k = us._norm(x["CountyName"])
        if k not in ids or not x["Juris1num"].strip().isdigit():
            continue
        uid, name = ids[k]
        rows.append(_row("county", uid, name, f"house-{int(x['Juris1num']):02d}",
                         " ".join(f"{x['CanNameFirst']} {x['CanNameLast']}".split()),
                         us.party_code(x["PartyCode"].strip()), x["CanVotes"] or 0, np.nan,
                         float(x["PrecinctsReporting"] or 0), float(x["Precincts"] or 0)))
    return us._frame(rows)


def parse_nc(county_txt: bytes, results: list[bytes]) -> pd.DataFrame:
    ids = us._ids("nc")
    info = {str(c["cid"]): c for arr in us._json_values(county_txt) for c in arr if str(c.get("cid")) != "0"}
    rows = []
    for blob in results:
        for x in (x for arr in us._json_values(blob) for x in arr):
            race = house_race(re.sub(r"\(VOTE FOR.*", "", x.get("cnm", "")))
            c = info.get(str(x.get("cid")))
            if race is None or c is None or us._norm(c["cnm"]) not in ids:
                continue
            uid, name = ids[us._norm(c["cnm"])]
            rows.append(_row("county", uid, name, race, x["bnm"], us.party_code(x.get("pty")), x.get("vct") or 0,
                             float(x.get("evc") or 0) + float(x.get("avc") or 0), float(x.get("prt") or 0),
                             float(x.get("ptl") or 0), float(c.get("bct") or 0), float(c.get("rtl") or 0)))
    return us._frame(rows)


def parse_sc(all_json: bytes, summary: bytes, status: bytes | None = None, st: str = "sc") -> pd.DataFrame:
    ids = us._ids(st)
    contests = {}
    for c in json.loads(summary):
        race = house_race(c["C"])
        if race is not None:
            contests[c["K"]] = (race, c["CH"], c.get("P") or [""] * len(c["CH"]))
    prog = {}
    if status:
        s = json.loads(status)
        for i, nm in enumerate(s.get("P") or []):
            if us._norm(nm) in ids:
                prog[ids[us._norm(nm)][0]] = (us._int(s["PX"][i]), us._int(s["PY"][i]), us._int(s["B"][i]),
                                              us._int(s["R"][i]))
    rows = []
    for cty in json.loads(all_json)["Contests"]:
        k = us._norm(cty["A"])
        if k not in ids:
            continue
        uid, name = ids[k]
        rep, tot, bal, reg = prog.get(uid, (np.nan,) * 4)
        for key, votes in zip(cty["C"], cty["V"]):
            if key not in contests:
                continue
            race, ch, parties = contests[key]
            for choice, party, v in zip(ch, parties, votes):
                rows.append(_row("county", uid, name, race, re.sub(r"^[A-Z]{2,4} ", "", choice), us.party_code(party),
                                 v, np.nan, rep, tot, bal, reg))
    return us._frame(rows)


def parse_oh(xml: bytes, summary: bytes | None = None) -> pd.DataFrame:
    N = us._NIST
    root = ET.fromstring(xml)
    ids = us._ids("oh")
    units = {}
    for g in root.iter(f"{N}GpUnit"):
        if g.get("Type") == "County" and g.get("IsElectoralDistrict") == "false":
            uid, name = ids[us._norm(g.get("Name"))]
            units[g.get("ObjectId")] = (uid, name, {"reporting": us._int(g.get("SubUnitsReported")),
                                                    "units": us._int(g.get("TotalSubUnits")),
                                                    "ballots": us._int(g.get("VotersParticipated")),
                                                    "registered": us._int(g.get("VotersRegistered"))})
    if summary:
        prog = us.parse_oh_summary(summary).drop_duplicates("unit_id").set_index("unit_id").to_dict("index")
        units = {k: (uid, nm, {**p, **{kk: prog[uid][kk] for kk in ("reporting", "units", "ballots", "registered")}}
                     if uid in prog else p) for k, (uid, nm, p) in units.items()}
    parties = {p.get("ObjectId"): p.get("Abbreviation") for p in root.iter(f"{N}Party")}
    cands = {c.get("ObjectId"): (c.findtext(f"{N}BallotName/{N}LanguageString") or "",
                                 parties.get(c.findtext(f"{N}PartyId"), "")) for c in root.iter(f"{N}Candidate")}
    rows = []
    for con in root.iter(f"{N}Contest"):
        race = house_race(con.get("Name") or "")
        if race is None:
            continue
        for bs in con.findall(f"{N}BallotSelection"):
            cid = (bs.findtext(f"{N}CandidateId") or "").split()
            name, party = cands.get(cid[0], ("", "")) if cid else ("", "")
            for vc in bs.iter(f"{N}VoteCounts"):
                u = units.get(vc.findtext(f"{N}GpUnitId")) if vc.get("Type") == "Total" else None
                if u is None:
                    continue
                uid, uname, p = u
                rows.append(_row("county", uid, uname, race, name, us.party_code(party), us._int(vc.get("Count")),
                                 np.nan, p["reporting"], p["units"], p["ballots"], p["registered"]))
    return us._frame(rows)


def parse_ev(items: list[bytes], st: str) -> pd.DataFrame:
    parts = []
    for b in items:
        race = house_race(json.loads(b)["ballotItemWithBreakdown"]["name"][0]["text"])
        if race is None:
            continue
        rows = us.parse_ga(b, st)
        parts.append(rows[rows["level"] == "county"].assign(race=race))
    return pd.concat(parts, ignore_index=True) if parts else us._frame([])


def parse_pa(blobs: dict[str, bytes | None]) -> pd.DataFrame:
    """PA DoS GetCountyBreak, office 11, one document per district (a JSON string holding the JSON document):
    {"Election": {"<n>th Congressional District": [{COUNTY: [candidate rows]}]}}; a split county lists only its part
    of the district. early = MailInVotes; the API has no precinct counts (as brunder.us_feeds_extra.parse_pa)."""
    ids, rows = us._extra._county_ids("pa"), []
    for blob in blobs.values():
        if not blob:
            continue
        doc = json.loads(blob)
        doc = json.loads(doc) if isinstance(doc, str) else doc
        for title, blocks in doc.get("Election", {}).items():
            m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+Congressional District\b", title, re.I)
            if not m:
                continue
            race = f"house-{int(m.group(1)):02d}"
            for block in blocks:
                for county, cands in block.items():
                    key = us._extra._key(county)
                    if key not in ids:
                        continue
                    uid, name = ids[key]
                    for x in cands:
                        rows.append(_row("county", uid, name, race, " ".join(str(x["CandidateName"]).split()),
                                         us.party_code(x.get("PartyName")), float(x.get("Votes") or 0),
                                         float(x.get("MailInVotes") or 0), np.nan, np.nan))
    return us._frame(rows)


def parse_mn(blob: bytes) -> pd.DataFrame:
    """MN allracesbycounty.txt (layout as brunder.us_feeds_extra.parse_mn): "U.S. Representative District <n>" rows,
    one per county × district × candidate; a split county's row holds only its part of the district, and its
    precincts reporting / total are that part's. County ID = alphabetical index, FIPS = 2 × ID − 1; DFL = DEM."""
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    names = {u: n for u, n in us._extra._county_ids("mn").values()}
    rows = []
    for line in text.splitlines():
        f = line.split(";")
        if len(f) < 14 or not f[1].strip().isdigit() or (race := house_race(f[4])) is None:
            continue
        uid = f"us-mn-{27000 + 2 * int(f[1]) - 1}"
        if uid not in names:
            continue
        p = f[10].strip()
        rows.append(_row("county", uid, names[uid], race, " ".join(f[7].split()),
                         {"DFL": "DEM", "R": "REP"}.get(p, us.party_code(p)),
                         float(f[13] or 0), np.nan, float(f[11] or 0), float(f[12] or 0)))
    return us._frame(rows)


_MAP_HOUSE = re.compile(r"^\s*(?:for\s+)?(?:representative in congress|united states representative)\b", re.I)


def parse_map_house(blobs: dict[str, bytes | None], st: str) -> pd.DataFrame:
    """NE / NM results web service GetMapData, one document per district (layout as
    brunder.us_feeds_extra.parse_mapdata); district from AreaNum ("District 2"), rows kept only if RaceName is the
    U.S. House race, so a wrong race id yields no rows. Precincts reporting / total are the county's."""
    ids, rows = us._extra._county_ids(st), []
    for blob in blobs.values():
        if not blob:
            continue
        doc = json.loads(blob)
        data = (doc.get("d") or []) if isinstance(doc, dict) else doc
        for x in data:
            m = re.search(r"\b(?:district\s*)?(\d+)\b", str(x.get("AreaNum") or ""), re.I)
            key = us._extra._key(str(x.get("CountyName") or ""))
            if not m or key not in ids or not _MAP_HOUSE.match(str(x.get("RaceName") or "")):
                continue
            uid, name = ids[key]
            rows.append(_row("county", uid, name, f"house-{int(m.group(1)):02d}",
                             " ".join(str(x["calcCandidate"]).split()), us.party_code(x.get("PartyCode")),
                             us._extra._votes(x.get("calcCandidateVotes")), np.nan,
                             float(x.get("PrecinctsReporting") or 0), float(x.get("TotalPrecincts") or 0)))
    return us._frame(rows)


def parse_house(st: str, blobs: dict) -> pd.DataFrame:
    """Rows of every U.S. House contest in one state's feed (blobs keyed as brunder.us.FEEDS[st])."""
    match st:
        case "tx":
            return parse_tx(blobs["county"]) if blobs.get("county") else us._frame([])
        case "fl":
            return parse_fl(blobs["enight"]) if blobs.get("enight") else us._frame([])
        case "nc":
            return parse_nc(blobs["county"], blobs.get("results") or []) if blobs.get("county") else us._frame([])
        case "sc":
            return parse_sc(blobs["all"], blobs["summary"], blobs.get("status")) \
                if blobs.get("all") and blobs.get("summary") else us._frame([])
        case "oh":
            return parse_oh(blobs["xml"], blobs.get("csv")) if blobs.get("xml") else us._frame([])
        case "ga" | "az":
            return parse_ev(blobs.get("item") or [], st)
        case "pa":
            return parse_pa(blobs)
        case "mn":
            return parse_mn(blobs["county"]) if blobs.get("county") else us._frame([])
        case "ne" | "nm":
            return parse_map_house(blobs, st)
    raise ValueError(f"no House parser for {st}")


def district_totals(rows: pd.DataFrame) -> pd.DataFrame:
    """race, dem, rep, total: the parsed rows summed over counties (verification against certified totals)."""
    g = rows.pivot_table(index="race", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"dem": g.get("DEM", 0), "rep": g.get("REP", 0), "total": g.sum(axis=1)})


def _fill0(x: np.ndarray) -> np.ndarray:
    """pandas fillna(0) / NaN-skipping sums on arrays (inf stays inf, unlike np.nan_to_num)."""
    return np.where(np.isnan(x), 0.0, x)


def _post_var(x: np.ndarray, w: np.ndarray, s0: float, nu: float) -> float:
    """brunder.nowcast._post_var on arrays."""
    n = len(x)
    v = 0.0
    if w.sum() > 0 and n >= 2:
        m = np.average(x, weights=w)
        v = float(np.average((x - m) ** 2, weights=w))
    return (nu * s0**2 + max(n - 1, 0) * v) / (nu + max(n - 1, 0))


def project(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """brunder.us.project (count_state → _model_units → brunder.nowcast.nowcast → per-county projection) in numpy for
    one district: the same formulas, in the same order, on arrays instead of DataFrames (≈ 20× faster). Configs with
    [count] expected_votes use brunder.us.project. Equality is tested on replayed counts (tests/test_us_house_night.py)."""
    R, p = us.rules(cfg), us.params(cfg)
    if R.expected_votes:
        return us.project(rows, cfg, pri)
    E_ = cfg["election"]
    uid = pri["unit_id"].to_numpy()
    s0p, V = pri["s0"].to_numpy(dtype=float), pri["V"].to_numpy(dtype=float)
    m = len(uid)
    # count_state
    u = rows[(rows["level"] != "state") & (rows["race"] == E_["race"])]
    a = np.zeros(m)
    b, ea, eb = a.copy(), a.copy(), a.copy()
    prog = {k: np.full(m, np.nan) for k in ("reporting", "units", "ballots", "outstanding")}
    if len(u):
        ix = pd.Index(uid).get_indexer(u["unit_id"])
        ok = ix >= 0
        party, votes = u["party"].to_numpy(), u["votes"].to_numpy(dtype=float)
        early = _fill0(u["early"].to_numpy(dtype=float))
        for side, cnt, ear in ((E_["a"]["party"], a, ea), (E_["b"]["party"], b, eb)):
            k = ok & (party == side)
            cnt += np.bincount(ix[k], votes[k], minlength=m)
            ear += np.bincount(ix[k], early[k], minlength=m)
        for col, out in prog.items():                # groupby.first: the first non-null value per unit
            x = u[col].to_numpy(dtype=float)
            k = ok & ~np.isnan(x)
            first, at = np.unique(ix[k], return_index=True)
            out[first] = x[k][at]
    n = a + b
    E0 = V * R.turnout_ratio
    with np.errstate(invalid="ignore", divide="ignore"):
        complete = (n > 0) & (prog["units"] > 0) & (prog["reporting"] >= prog["units"])
        E_done = np.full(m, np.nan)
        if R.use_outstanding:
            k = complete & (prog["ballots"] > 0) & ~np.isnan(prog["outstanding"])
            E_done[k] = (n + prog["outstanding"] * n / prog["ballots"])[k]
        if R.f_complete > 0:
            E_done = np.where(np.isnan(E_done) & complete, n / R.f_complete, E_done)
        done = ~np.isnan(E_done)
        T = 1.0
        if R.learn_turnout and done.any():
            T = (E_done[done].sum() + R.turnout_prior_votes) / (E0[done].sum() + R.turnout_prior_votes)
        E = np.where(done, E_done, np.maximum(E0 * T, n))
        f = np.clip(np.where(done, n / E, np.minimum(n / E, R.f_max)), 0, 1)
    ne, ae = ea + eb, ea
    # _model_units: region 0 = the district (or early + mail), region 1 = the rest (vote_type)
    county = np.arange(m)
    if not R.vote_type or (n.sum() > 0 and ne.sum() == 0):
        mu = {"uf": np.zeros(m, dtype=int), "n": n, "a": a, "f": f, "V0": E, "s0": s0p, "county": county}
        n_uf = 1
    else:
        posted = ne > 0
        Ee = np.where(posted, ne, R.early_share * E)
        El = np.maximum(E - Ee, 0.02 * E)
        nl, al = n - ne, a - ae
        with np.errstate(invalid="ignore", divide="ignore"):
            fl = np.where(done, np.minimum(nl / El, 1.0), np.minimum(nl / El, R.f_max))
        mu = {"uf": np.r_[np.zeros(m, dtype=int), np.ones(m, dtype=int)], "n": np.r_[ne, nl], "a": np.r_[ae, al],
              "f": np.r_[posted.astype(float), fl], "V0": np.r_[Ee, El], "s0": np.r_[s0p, s0p],
              "county": np.r_[county, county]}
        n_uf = 2
    res = _nowcast(mu, n_uf, p)
    # project: the remaining-share projection per unit, summed per county
    mn, ma, ms0, muf = mu["n"], mu["a"], mu["s0"], mu["uf"]
    counted = mn > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        d = np.where(counted, ma / np.where(counted, mn, np.nan) - ms0, np.nan)
    w = np.where(counted, mn, 0.0)
    wd = np.bincount(muf, np.nan_to_num(d) * w, minlength=n_uf)
    W = np.bincount(muf, w, minlength=n_uf)
    obs = res.get("swing_obs", res["swing_nat"])
    delta_u = ((wd + p.kappa * obs) / (W + p.kappa) + (res["swing_nat"] - obs))[muf]
    s_hat = np.clip(ms0 + delta_u + p.rho * (np.nan_to_num(d) - delta_u) * counted, 0, 1)
    Rm = np.clip(1 - mu["f"], 0, None) * mu["V0"] * res["turnout_k"]
    ra, rr = _fill0(Rm * s_hat), _fill0(Rm)            # groupby().sum() skips NaN
    cnt = mu["county"]
    agg_a, agg_n = np.bincount(cnt, ma, minlength=m), np.bincount(cnt, mn, minlength=m)
    agg_ra, agg_r = np.bincount(cnt, ra, minlength=m), np.bincount(cnt, rr, minlength=m)
    den = agg_n + agg_r
    with np.errstate(invalid="ignore", divide="ignore"):
        proj = np.where(den > 0, (agg_a + agg_ra) / np.where(den > 0, den, np.nan), np.nan)
    res["raw"] = float(a.sum() / n.sum()) if n.sum() > 0 else np.nan
    res["turnout_T"] = float(T) if m else 1.0
    rem, rem_margin = agg_r, 2 * agg_ra - agg_r
    res["rem_total"], res["rem_margin_total"] = float(np.nansum(rem)), float(np.nansum(rem_margin))
    units = pd.DataFrame({"unit_id": uid, "n": n, "a": a, "ne": ne, "ae": ae, "f": f, "E": E, "complete": done,
                          "turnout_T": T, "proj": proj, "s0": s0p, "V": V, "rem": rem, "rem_margin": rem_margin})
    return res, units


def _nowcast(mu: dict, n_uf: int, p) -> dict:
    """brunder.nowcast.nowcast on the arrays of `project` (regions 0..n_uf-1, none unpooled)."""
    from scipy.stats import norm
    keep = mu["V0"] > 0
    uf, s0, V0 = mu["uf"][keep], mu["s0"][keep], mu["V0"][keep]
    n, a = _fill0(mu["n"][keep]), _fill0(mu["a"][keep])
    f = _fill0(mu["f"][keep])
    counted = n > 0
    expected_counted = (f * V0).sum()
    k = n.sum() / expected_counted if expected_counted > 0 else 1.0
    R = np.clip(1 - f, 0, None) * V0 * k
    with np.errstate(invalid="ignore", divide="ignore"):
        d = np.where(counted, a / np.where(counted, n, np.nan) - s0, np.nan)
    delta = float(np.average(d[counted], weights=n[counted])) if counted.any() else 0.0
    present = np.bincount(uf, minlength=n_uf) > 0              # regions of the kept units (reindex(uf.unique()))
    W = np.bincount(uf[counted], n[counted], minlength=n_uf)
    wd = np.bincount(uf[counted], (n * np.nan_to_num(d))[counted], minlength=n_uf)
    W2 = np.bincount(uf[counted], (n**2)[counted], minlength=n_uf)
    W, wd, W2 = W[present], wd[present], W2[present]
    code = np.cumsum(present) - 1                                # region -> row of the present regions
    ufc = code[uf]
    delta_u_r = (wd + p.kappa * delta) / (W + p.kappa)
    delta_u = delta_u_r[ufc]
    resid = _fill0(d - delta_u)
    s_hat = np.clip(s0 + delta_u + p.rho * resid * counted, 0, 1)
    sigma_mun2 = _post_var(resid[counted], n[counted], p.sigma_mun0, p.nu_mun)
    pooled = W > 0
    sigma_between2 = _post_var(wd[pooled] / W[pooled], W[pooled], p.sigma_uf0, p.nu_uf)
    lam = W / (W + p.kappa)
    with np.errstate(invalid="ignore", divide="ignore"):
        var_u = _fill0(lam**2 * sigma_mun2 * W2 / np.where(W > 0, W, np.nan) ** 2) + sigma_between2 * (1 - lam)
    Wp, W2p = W[pooled], W2[pooled]
    var_delta = (sigma_mun2 * W2p.sum() + sigma_between2 * (Wp**2).sum()) / Wp.sum() ** 2 if Wp.sum() > 0 else 0.0
    twoR = 2 * R
    twoR_u = np.bincount(ufc, twoR, minlength=len(W))
    var_mun = (twoR**2 * sigma_mun2 * np.where(counted, 1 - f, 1.0)).sum()
    var_uf = (twoR_u**2 * var_u).sum()
    var_nat = twoR.sum() ** 2 * p.tau**2 + (twoR_u * (1 - lam)).sum() ** 2 * var_delta
    extra = {}
    if p.swing_prior_sd is not None:
        var_obs = np.inf
        if Wp.sum() > 0:
            var_obs = sigma_mun2 * W2p.sum() / Wp.sum() ** 2
            if len(W) > 1:
                var_obs += sigma_between2 * (Wp**2).sum() / Wp.sum() ** 2
        s2 = p.swing_prior_sd**2
        w_obs = s2 / (s2 + var_obs) if np.isfinite(var_obs) else 0.0
        delta_post = w_obs * delta + (1 - w_obs) * (p.swing_prior_mean or 0.0)
        var_post = (1 - w_obs) * s2
        delta_u = delta_u + (delta_post - delta)
        resid_post = _fill0(d - delta_u)
        s_hat = np.clip(s0 + delta_u + p.rho * resid_post * counted, 0, 1)
        scale = (w_obs if p.count_scale_mode == "weight" else float(np.isfinite(var_obs))) \
            if p.count_scaled_terms else 1.0
        var_uf = var_uf * scale
        var_nat = twoR.sum() ** 2 * p.tau**2 * scale + twoR_u.sum() ** 2 * var_post
        extra = {"swing_obs": delta, "swing_prior_weight": float(1 - w_obs)}
        delta = delta_post
    sd = float(np.sqrt(var_mun + var_uf + var_nat))
    m = float((2 * a - n).sum()) + float((R * (2 * s_hat - 1)).sum())
    total = n.sum() + R.sum()
    return {"p_win": float(norm.cdf(m / sd)) if sd > 0 else float(m > 0),
            "share_hat": 0.5 + m / (2 * total) if total else np.nan,
            "share_sd": sd / (2 * total) if total else np.nan,
            "margin_hat": m, "margin_sd": sd,
            "counted_frac": float(n.sum() / total) if total else np.nan,
            "swing_nat": delta, "turnout_k": float(k), **extra}


# ------------------------------------------------------------------ live: own process, own files

HOUSE_DIR = "house_live"                  # <out>/us2026/house_live/: the only files this process writes


class IncrementalArchive(us.Archive):
    """brunder.us.Archive with the same listing, built incrementally: each snapshot file name is parsed once and a
    key's sorted list is rebuilt only when its files changed. The recorder adds a file per key every 30 s for two
    weeks, so re-parsing every name each cycle (Archive.refresh) grows without bound."""

    _NAME = re.compile(r"(\d{8}T\d{6}\.\d{6})Z")

    def __init__(self, root: Path):
        super().__init__(root)
        self._ts: dict[str, pd.Timestamp | None] = {}     # file name -> its time (None: not a snapshot)
        self._names: dict[str, set[str]] = {}             # key -> snapshot names in the current listing
        self._times: list[pd.Timestamp] | None = None

    def refresh(self) -> None:
        import os
        from datetime import datetime
        files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        names: dict[str, set[str]] = {}
        if self.root.exists():
            for d in os.scandir(self.root):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                cur = set()
                for f in os.scandir(d.path):
                    if f.name not in self._ts:
                        m = self._NAME.match(f.name)
                        self._ts[f.name] = pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S.%f"),
                                                        tz="UTC") if m else None
                    if self._ts[f.name] is not None:
                        cur.add(f.name)
                names[d.name] = cur
                if self._names.get(d.name) == cur:
                    files[d.name] = self._files[d.name]
                else:
                    base = self.root / d.name
                    files[d.name] = sorted((self._ts[n], base / n) for n in cur)
        if names != self._names:
            self._times = None
        self._files, self._names = files, names

    def times(self) -> list[pd.Timestamp]:
        if self._times is None:
            self._times = super().times()
        return self._times


class HouseExporter(us.Exporter):
    """brunder.us.Exporter on the House configs, isolated from `brunder us live`: writes only
    <out>/us2026/house_live/{meta,timeline,timeline_tail,status}.json and units/<race_id>.json (never the statewide
    meta/timeline/overview). Timeline rows are thinned by age before a race's last row: older than 6 h to one per
    10 minutes, older than 48 h to one per hour, so memory and file size stay bounded over the two-week run.

    Incremental: a feed is parsed only when one of its snapshot files changes (brunder.us.Exporter); its rows are then
    split by district once, and a district is re-projected only when its own rows changed (content digest), with the
    numpy `project` above. A units file is rewritten only when the district gained a frame; meta.json is built once."""

    # (age, kept ISO prefix): '…THH:M' = one row per 10 minutes, '…THH' = one per hour (ISO times sort as strings)
    THIN = ((pd.Timedelta("48h"), 13), (pd.Timedelta("6h"), 15))
    project = staticmethod(project)

    def __init__(self, cfg_paths: list, snap_root=None, step: str = "1min", **kw):
        super().__init__(cfg_paths, snap_root, step=step, frame_every="10min", **kw)
        self._race: str | None = None                              # district of the race being ticked
        self._split: dict[str, tuple[pd.DataFrame, dict]] = {}     # feed -> (its parsed rows, rows per district)
        self._written: dict[str, tuple] = {}                       # race -> frames written to its units file
        self._meta: dict | None = None
        self._precount: dict[str, dict] = {}                       # race -> its (constant) pre-count frame
        self._feed_at: dict[str, tuple[pd.Timestamp, pd.DataFrame]] = {}   # feed -> (minute, its rows then)
        self.arc = IncrementalArchive(self.arc.root)

    def _tick(self, c: dict, t: pd.Timestamp, prices: dict[str, float], kprices: dict[str, float]) -> None:
        self._race = c["election"]["race"]
        super()._tick(c, t, prices, kprices)

    def _rows_for(self, kind: str, t: pd.Timestamp) -> tuple[pd.DataFrame, tuple]:
        """The district's rows of the feed and their content digest (the projection memo key). The feed's snapshot
        files are looked up once per minute for all its districts."""
        at = self._feed_at.get(kind)
        if at is None or at[0] != t:
            at = (t, super()._rows_for(kind, t)[0])
            self._feed_at[kind] = at
        rows = at[1]
        split = self._split.get(kind)
        if split is None or split[0] is not rows:
            split = (rows, self.by_race(rows))
            self._split[kind] = split
        return split[1].get(self._race) or (us._frame([]), (self._race, b""))

    @staticmethod
    def by_race(rows: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, tuple]]:
        import hashlib
        u = rows[rows["level"] != "state"]
        out = {}
        for race, g in u.groupby("race", sort=False):
            h = pd.util.hash_pandas_object(g, index=False).to_numpy().tobytes()
            out[race] = (g, (race, hashlib.blake2b(h, digest_size=16).digest()))
        return out

    def update(self, until: pd.Timestamp | None = None) -> None:
        super().update(until)
        for rows in self.timeline.values():
            rows[:] = self.thin(rows)

    @classmethod
    def thin(cls, rows: list[dict]) -> list[dict]:
        if len(rows) < 2:
            return rows
        last = pd.Timestamp(rows[-1]["t"])
        cuts = [(us._iso(last - age), k) for age, k in cls.THIN]
        kept, seen = [], set()
        for i, r in enumerate(rows):
            k = next((k for cut, k in cuts if r["t"] < cut), None)
            if k is None:                            # rows are in time order: the rest is recent
                return kept + rows[i:]
            if r["t"][:k] not in seen:
                seen.add(r["t"][:k])
                kept.append(r)
        return kept

    def write(self, out) -> Path:
        d = Path(out) / us.EID / HOUSE_DIR
        ok = {rid: p is not None and not p.empty for rid, p in self.pri.items()}
        if self._meta is None:
            self._meta = us.meta(self.cfgs, ok, self.kalshi_mk, phase=self.phase, tokens=self.tokens)
        us._write_json(d / "meta.json", self._meta)
        us._write_json(d / "timeline.json", self.timeline)
        us._write_json(d / "timeline_tail.json", self.tail())
        now = us._iso(pd.Timestamp.now(tz="UTC").floor("1min"))
        for c in self.cfgs:
            rid = c["election"]["race_id"]
            if not ok[rid]:
                continue
            try:
                fr = self.frames[rid]
                key = (len(fr), fr[-1]["t"]) if fr else None
                if key is not None and self._written.get(rid) == key and (d / "units" / f"{rid}.json").exists():
                    continue                         # no new frame since the last write
                if not fr:
                    if rid not in self._precount:
                        self._precount[rid] = us.precount_frame(c, self.pri[rid], now, *self._poll_cfg(rid))
                    fr = [{**self._precount[rid], "t": now}]
                if len(fr) > 60:
                    idx = np.unique(np.linspace(0, len(fr) - 1, 60).round().astype(int))
                    fr = [fr[i] for i in idx]
                us._write_json(d / "units" / f"{rid}.json", us.units_object(c, self.pri[rid], fr))
                if key is not None:
                    self._written[rid] = key
            except Exception as e:                   # one race's map must not block the others
                self.errors[rid] = f"write {type(e).__name__}: {e}"
        us._write_json(d / "status.json", {"t": now, "errors": self.errors})
        return d


def run_live(cfg_paths: list, out, snap_root=None, interval: float = 60.0, stop_at: pd.Timestamp | None = None,
             cycles: int | None = None) -> None:
    """Every `interval` seconds: extend the House timelines with what the recorder saved, rewrite house_live/."""
    import time
    ex = HouseExporter(cfg_paths, snap_root)
    n = 0
    while (stop_at is None or pd.Timestamp.now(tz="UTC") < stop_at) and (cycles is None or n < cycles):
        t0 = time.monotonic()
        now = pd.Timestamp.now(tz="UTC")
        try:
            ex.update(now)
            ex.write(out)
            print(f"{now:%H:%M:%S} house cycle {time.monotonic() - t0:.1f}s, {len(ex.cfgs)} races"
                  + (f", open errors: {ex.errors}" if ex.errors else ""), flush=True)
        except Exception as e:                       # never stop on the night
            print(f"{now:%H:%M:%S} house export error, continuing: {type(e).__name__}: {e}", flush=True)
        n += 1
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))


def main(argv: list[str] | None = None) -> None:
    """python -m brunder.us_house_night live <configs/us2026_house/<st>-<nn>_v1.toml …> --out site/data [--snap DIR]
    [--interval 60] [--until ISO]"""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m brunder.us_house_night", description=main.__doc__)
    ap.add_argument("cmd", choices=["live"])
    ap.add_argument("configs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--snap", type=Path, default=None, help="recorder directory (default data/raw/snap/us_midterms_2026)")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--until", default=None)
    a = ap.parse_args(argv)
    run_live(a.configs, a.out, a.snap, a.interval, pd.Timestamp(a.until) if a.until else None)


if __name__ == "__main__":
    main()
