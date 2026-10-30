"""US House districts on election night 2026-11-03: U.S. Representative contests from the official feeds the recorder
already saves for the statewide races (brunder.us.FEEDS), one row per county × district × candidate.

Feed kinds "<st>_house" (brunder.us.FEEDS; same snapshot keys as "<st>", except GA/AZ, whose House ballot items are
recorded under ga_enr_<Georgia|Arizona>_house_<item>) are parsed here; brunder.us.parse_feed dispatches to
parse_house. Rows have the brunder.us.COLS layout with race = "house-<NN>" and level = "county": a county split
between districts appears once per district with only that district's votes, so brunder.us.project runs unchanged
on the district's counties (prior: data_public/us2026_house_priors.csv, analysis/us_house_night.py).

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

HOUSE_STATES = ("tx", "fl", "nc", "sc", "oh", "ga", "az")
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
    raise ValueError(f"no House parser for {st}")


def district_totals(rows: pd.DataFrame) -> pd.DataFrame:
    """race, dem, rep, total: the parsed rows summed over counties (verification against certified totals)."""
    g = rows.pivot_table(index="race", columns="party", values="votes", aggfunc="sum", fill_value=0)
    return pd.DataFrame({"dem": g.get("DEM", 0), "rep": g.get("REP", 0), "total": g.sum(axis=1)})


# ------------------------------------------------------------------ live: own process, own files

HOUSE_DIR = "house_live"                  # <out>/us2026/house_live/: the only files this process writes


class HouseExporter(us.Exporter):
    """brunder.us.Exporter on the House configs, isolated from `brunder us live`: writes only
    <out>/us2026/house_live/{meta,timeline,timeline_tail,status}.json and units/<race_id>.json (never the statewide
    meta/timeline/overview). Timeline rows are thinned by age before a race's last row: older than 6 h to one per
    10 minutes, older than 48 h to one per hour, so memory and file size stay bounded over the two-week run."""

    # (age, kept ISO prefix): '…THH:M' = one row per 10 minutes, '…THH' = one per hour (ISO times sort as strings)
    THIN = ((pd.Timedelta("48h"), 13), (pd.Timedelta("6h"), 15))

    def __init__(self, cfg_paths: list, snap_root=None, step: str = "1min", **kw):
        super().__init__(cfg_paths, snap_root, step=step, frame_every="10min", **kw)

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
        us._write_json(d / "meta.json", us.meta(self.cfgs, ok, self.kalshi_mk, phase=self.phase, tokens=self.tokens))
        us._write_json(d / "timeline.json", self.timeline)
        us._write_json(d / "timeline_tail.json", self.tail())
        now = us._iso(pd.Timestamp.now(tz="UTC").floor("1min"))
        for c in self.cfgs:
            rid = c["election"]["race_id"]
            if not ok[rid]:
                continue
            try:
                fr = self.frames[rid] or [us.precount_frame(c, self.pri[rid], now, *self._poll_cfg(rid))]
                if len(fr) > 60:
                    idx = np.unique(np.linspace(0, len(fr) - 1, 60).round().astype(int))
                    fr = [fr[i] for i in idx]
                us._write_json(d / "units" / f"{rid}.json", us.units_object(c, self.pri[rid], fr))
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
