"""End-to-end rehearsal of the US election-night pipeline on a SYNTHETIC night (never commit or publish its output).

1. Writes a synthetic snapshot archive in exactly the recorder's layout (brunder.snap: <key>/<UTC time>.<ext>[.gz],
   files >1 MB gzipped, every file written to .tmp/ and os.replace()d into place, index.jsonl appended): CA SoS
   API JSON, TX Civix base64 countyInfo (padded with non-modelled races to the real ~MB size, so it is gzipped),
   OH NIST 1622 XML + summary CSV, GA Enhanced Voting ga_enr_Georgia_data + ga_enr_Georgia_item_<id>, and
   polymarket_books. Counties post early + mail votes first and Election Day in chunks over ~6 h (us_dryrun.night).
2. The first 31 of 37 ten-minute steps are stamped in the recent past; `brunder us live` is then started as a
   subprocess (its own process, the production CLI) and the last steps are written in real time while it runs,
   together with a truncated (non-atomic) TX file and a well-formed but unparsable GA item, to exercise failure
   isolation. A reader thread re-reads the published files continuously to catch non-atomic rewrites.
3. Checks: every unit id in each units file joins site/data/geo/us_counties.topo.json; rows per race; frames;
   no NaN; pm present; the live log shows the corrupt files skipped and every race still publishing.

Ohio: the real Ohio prior (MEDSL) is not available, so the rehearsal's own priors file (inside the rehearsal data
root, never data/) adds SYNTHETIC oh_pres_2024 rows built from the Ohio SoS mock CSV's registered voters.

    uv run python analysis/us_rehearsal.py [--root results/us_rehearsal] [--step-seconds 20]
Output: <root>/site_data/us2026/ (dashboard contract), <root>/data/ (BRUNDER_DATA of the run), <root>/live.log
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_dryrun as dry  # noqa: E402  (payload builders + synthetic night)

from brunder import us  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted((ROOT / "configs").glob("us2026_*_v1.toml"))
STEPS, PAST = dry.STEPS, 31
NS = us._NIST
GA_ITEMS = {"ga-sen": "01000000-0000-0000-0000-00000000sen1", "ga-gov": "01000000-0000-0000-0000-00000000gov1"}


class Recorder:
    """brunder.snap.run's save(): dedupe by hash, gzip >1 MB, write to .tmp/ then os.replace, append index.jsonl."""

    def __init__(self, out: Path):
        self.out, self.last = out, {}
        out.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, body: bytes, ext: str, t: datetime) -> None:
        h = hashlib.sha256(body).hexdigest()
        if self.last.get(key) == h:
            return
        self.last[key] = h
        d = self.out / key
        d.mkdir(parents=True, exist_ok=True)
        big = len(body) > 1_000_000
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.{ext}{'.gz' if big else ''}"
        tmp = self.out / ".tmp" / f"{key}.{f.name}"
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(gzip.compress(body) if big else body)
        os.replace(tmp, f)
        with (self.out / "index.jsonl").open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(self.out)), "sha256": h,
                                 "bytes": len(body), "gzip": big}) + "\n")


def tx_payload(states: dict[str, pd.DataFrame]) -> bytes:
    """us_dryrun's TX countyInfo plus 40 non-modelled races per county (real files carry ~42; ~5 MB)."""
    up = json.loads(base64.b64decode(json.loads(dry.tx_payload(states))["upload"]))
    for entry in up.values():
        for k in range(40):
            entry["Races"][str(90000 + k)] = {"N": f"DISTRICT JUDGE, {k + 1}TH JUDICIAL DISTRICT", "C": {
                "1": {"N": "JUDGE CANDIDATE", "P": "REP", "V": 0, "EV": 0}}}
    return json.dumps({"upload": base64.b64encode(json.dumps(up).encode()).decode()}).encode()


def oh_payloads(states: dict[str, pd.DataFrame]) -> tuple[bytes, bytes]:
    """OH NIST 1622 XML (fixture template, counts replaced) and the matching summary CSV."""
    ET.register_namespace("", NS[1:-1])
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root = ET.fromstring(dry._fix("oh_vssc1622.xml.gz"))
    ids = {k: v[0] for k, v in us._ids("oh").items()}
    gp = {}
    any_state = next(iter(states.values())).set_index("unit_id")
    rows_csv = []
    for g in root.iter(f"{NS}GpUnit"):
        if g.get("Type") == "County" and g.get("IsElectoralDistrict") == "false":
            uid = ids[us._norm(g.get("Name"))]
            gp[g.get("ObjectId")] = uid
            r = any_state.loc[uid]
            ballots = int((r["d"] + r["r"]) * 1.02)
            total = int(g.get("TotalSubUnits"))
            rep = total if r["done"] else int(total * min(0.9, (r["d"] + r["r"] - r["ed"] - r["er"]) /
                                                          max(r["d"] + r["r"], 1)))
            out_abs, out_prov = (int(ballots * 0.02), int(ballots * 0.01)) if r["done"] else (int(ballots * .2), 0)
            g.set("SubUnitsReported", str(rep))
            g.set("VotersParticipated", str(ballots))
            for s in g.findall(f"{NS}SummaryCounts"):
                if s.get("Type") == "Absentee":
                    s.set("BallotsOutstanding", str(out_abs))
                elif s.get("Type") == "Provisional":
                    s.set("BallotsOutstanding", str(out_prov))
            rows_csv.append((g.get("Name"), g.get("VotersRegistered"), ballots, out_abs, out_prov, total, rep))
        elif g.get("Type") == "State":
            gp[g.get("ObjectId")] = "STATE"
    parties = {p.get("ObjectId"): p.get("Abbreviation") for p in root.iter(f"{NS}Party")}
    cand_party = {c.get("ObjectId"): parties.get(c.findtext(f"{NS}PartyId")) for c in root.iter(f"{NS}Candidate")}
    for con in root.iter(f"{NS}Contest"):
        s = states.get(us.race_key(con.get("Name") or "")).set_index("unit_id")
        for bs in con.findall(f"{NS}BallotSelection"):
            party = cand_party.get((bs.findtext(f"{NS}CandidateId") or "-").split()[0], "")
            col = {"DEM": "d", "REP": "r"}.get(party)
            for vc in bs.iter(f"{NS}VoteCounts"):
                uid = gp.get(vc.findtext(f"{NS}GpUnitId"))
                if uid is None:
                    continue
                v = 0 if col is None else (s[col].sum() if uid == "STATE" else s.at[uid, col])
                vc.set("Count", str(int(v)))
    xml = ET.tostring(root, xml_declaration=True, encoding="utf-8")
    head = ("County, Registered Voters, Ballots Counted, Unofficial Voter Turnout, Outstanding UOCAVA Absentees, "
            "Outstanding Provisionals, Total Precincts,  Election Day Precincts Reporting, Election Day % Precincts "
            "Reporting")
    lines = [head] + [f'{n},"{reg}","{b}",="0.00",{a},{p},{t},{r},="0.00"' for n, reg, b, a, p, t, r in rows_csv]
    return xml, ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def ga_data_payload() -> bytes:
    return json.dumps({"ballotItems": [{"id": GA_ITEMS["ga-sen"], "name": [{"languageId": "en", "text": "US Senate"}]},
                                       {"id": GA_ITEMS["ga-gov"], "name": [{"languageId": "en", "text": "Governor"}]}]
                       }).encode()


def books(cfgs: dict, truth: dict, k: int, rng: np.random.Generator) -> bytes:
    out = {}
    for c in cfgs.values():
        rid, pm = c["election"]["race_id"], c["markets"]["polymarket"]
        target = 0.97 if truth.get(rid, 0.5) > 0.5 else 0.03
        p = float(np.clip(0.5 + (target - 0.5) * (k / (STEPS - 1)) ** 1.5 + rng.normal(0, 0.02), 0.02, 0.98))
        out[f"{pm['event']}/{pm['market']}"] = {"bids": [{"price": f"{p - .01:.3f}", "size": "500"}],
                                                "asks": [{"price": f"{p + .01:.3f}", "size": "500"}],
                                                "last_trade_price": f"{p:.3f}"}
    return json.dumps(out, sort_keys=True).encode()


def kalshi_books(cfgs: dict, truth: dict, k: int, rng: np.random.Generator) -> bytes:
    """kalshi_books as `brunder kalshi record` saves them: {ticker: orderbook_fp} (YES and NO bid ladders), for
    each race's side-a market from configs/pairs/us2026.toml; prices lag Polymarket's a little."""
    out = {}
    for rid, km in us.kalshi_markets(list(cfgs.values())).items():
        target = 0.97 if truth.get(rid, 0.5) > 0.5 else 0.03
        p = float(np.clip(0.5 + (target - 0.5) * (k / (STEPS - 1)) ** 2 + rng.normal(0, 0.02), 0.03, 0.97))
        out[km["ticker"]] = {"yes_dollars": [[f"{p - .01:.4f}", "300.00"], [f"{p - .03:.4f}", "900.00"]],
                             "no_dollars": [[f"{1 - p - .01:.4f}", "300.00"], [f"{1 - p - .03:.4f}", "900.00"]]}
    return json.dumps(out, sort_keys=True).encode()


def build_priors(data: Path) -> pd.DataFrame:
    """Real priors (data/processed/us2026/priors.csv) + SYNTHETIC oh_pres_2024 for the rehearsal only."""
    real = us.load_priors(ROOT / "data" / "processed" / "us2026" / "priors.csv")
    oh = us.parse_oh_summary(dry._fix("oh_summary.csv")).drop_duplicates("unit_id")
    oh = oh[oh["unit_id"] != "us-oh"]
    rng = np.random.default_rng(39)
    s0 = rng.uniform(0.3, 0.65, len(oh))
    V = oh["registered"].to_numpy() * 0.7
    synth = pd.DataFrame({"source": "oh_pres_2024", "unit_id": oh["unit_id"], "dem": V * s0, "rep": V * (1 - s0),
                          "total": V})
    pri = pd.concat([real[real["source"] != "oh_pres_2024"], synth], ignore_index=True)
    (data / "processed" / "us2026").mkdir(parents=True, exist_ok=True)
    pri.to_csv(data / "processed" / "us2026" / "priors.csv", index=False)
    return pri


def write_step(rec: Recorder, k: int, t: datetime, at: dict, cfgs: dict, priors, truth, rng) -> None:
    if "ca-gov" in at:
        rec.save(us.FEEDS["ca"]["results"], dry.ca_payload(at["ca-gov"], us.race_prior(
            cfgs["ca-gov"], priors)), "json", t)
    if {"tx-sen", "tx-gov"} <= set(at):
        rec.save(us.FEEDS["tx"]["county"], tx_payload({"sen": at["tx-sen"], "gov": at["tx-gov"]}), "json", t)
    if {"oh-sen", "oh-gov"} <= set(at):
        xml, csv_ = oh_payloads({"sen": at["oh-sen"], "gov": at["oh-gov"]})
        rec.save(us.FEEDS["oh"]["xml"], xml, "html", t)
        rec.save(us.FEEDS["oh"]["csv"], csv_, "html", t)
    rec.save("ga_enr_Georgia_data", ga_data_payload(), "json", t)
    for rid, title in (("ga-sen", "US Senate"), ("ga-gov", "Governor")):
        if rid in at:
            rec.save(f"ga_enr_Georgia_item_{GA_ITEMS[rid]}", dry.ga_payload(at[rid], title), "json", t)
    rec.save("polymarket_books", books(cfgs, truth, k, rng), "json", t)
    rec.save("kalshi_books", kalshi_books(cfgs, truth, k, rng), "json", t)


def watch_atomic(out: Path, stop: threading.Event, stats: dict) -> None:
    """Re-read every published JSON file continuously; any parse failure means a reader saw a partial file."""
    while not stop.is_set():
        for f in out.rglob("*.json"):
            try:
                json.loads(f.read_text())
                stats["reads"] += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                stats["bad"].append(f"{f.name}: {type(e).__name__}")
        time.sleep(0.05)


def _no_nan(c: str):
    raise ValueError(f"{c} in published JSON")


def check(out: Path, log: str) -> dict:
    geo = json.loads((ROOT / "site" / "data" / "geo" / "us_counties.topo.json").read_text())
    geo_ids = {g["properties"]["unit_id"] for g in geo["objects"]["counties"]["geometries"]}
    d = out / "us2026"
    meta = json.loads((d / "meta.json").read_text())
    tl = json.loads((d / "timeline.json").read_text(), parse_constant=_no_nan)
    rep = {"races": {}, "geo_misses": {}}
    for r in meta["races"]:
        rid = r["race_id"]
        u = json.loads((d / r["units"]).read_text(), parse_constant=_no_nan)
        miss = sorted(set(u["unit_ids"]) - geo_ids)
        rep["geo_misses"].setdefault(r["region"], set()).update(miss)
        rows = tl[rid]
        ts = [x["t"] for x in rows]
        gaps = int((pd.to_datetime(ts).diff().dropna() != pd.Timedelta("1min")).sum())
        rep["races"][rid] = {"rows": len(rows), "dup": len(ts) - len(set(ts)), "gaps": gaps,
                             "frames": len(u["frames"]), "units": len(u["unit_ids"]),
                             "pm_rows": sum(x["pm"] is not None for x in rows),
                             "kalshi_rows": sum(x["kalshi"] is not None for x in rows),
                             "kalshi_market": [m["id"] for m in r["markets"] if m["platform"] == "kalshi"],
                             "last": {k: rows[-1][k] for k in ("t", "counted", "raw", "share", "sd", "P", "pm",
                                                               "kalshi")}}
    rep["geo_misses"] = {k: sorted(v) for k, v in rep["geo_misses"].items()}
    tail = json.loads((d / "timeline_tail.json").read_text(), parse_constant=_no_nan)
    rep["tail"] = {"since": tail.get("since"), "flag": tail.get("tail"),
                   "rows": {rid: len(tail.get(rid, [])) for rid in tl},
                   "matches_full": all(tail.get(rid) == [x for x in rows if pd.Timestamp(x["t"]) >
                                                         pd.Timestamp(rows[-1]["t"]) - pd.Timedelta("60min")]
                                       for rid, rows in tl.items() if rows)}
    rep["sizes_kb"] = {str(f.relative_to(d)): round(f.stat().st_size / 1024, 1) for f in sorted(d.rglob("*.json"))}
    rep["log"] = {"snapshot_skipped": log.count("snapshot skipped"), "feed_parse_failed": log.count("parse failed"),
                  "race_failed": log.count("retry next pass"), "export_error": log.count("export error")}
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT / "results" / "us_rehearsal")
    ap.add_argument("--step-seconds", type=float, default=20, help="wall seconds between the real-time steps")
    a = ap.parse_args()
    if a.root.exists():
        shutil.rmtree(a.root)
    data, out = a.root / "data", a.root / "site_data"
    snap = data / "raw" / "snap" / us.TAG
    rec = Recorder(snap)
    priors = build_priors(data)
    cfgs = {us.load_config(p)["election"]["race_id"]: us.load_config(p) for p in CONFIGS}
    rng = np.random.default_rng(1103)
    sims, truth = {}, {}
    for rid, c in cfgs.items():
        pri = us.race_prior(c, priors).assign(V=lambda x, c=c: x["V"] * us.rules(c).turnout_ratio)
        trng = np.random.default_rng(sum(map(ord, c["election"]["state"])))
        sims[rid] = dry.night(pri, rng.normal(0, 0.03), rng, trng)
        fin = sims[rid][sims[rid]["step"] == STEPS - 1]
        truth[rid] = fin["d"].sum() / (fin["d"] + fin["r"]).sum()
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=10 * PAST)
    for k in range(PAST):                                         # the night so far, stamped in the past
        write_step(rec, k, t0 + timedelta(minutes=10 * k), {r: s[s["step"] == k] for r, s in sims.items()},
                   cfgs, priors, truth, rng)
    print(f"archive: {sum(1 for _ in snap.rglob('*Z.*'))} snapshot files, steps 0-{PAST - 1} "
          f"({t0:%H:%M}-{now:%H:%M} UTC)", flush=True)

    env = {**os.environ, "BRUNDER_DATA": str(data), "PYTHONUNBUFFERED": "1"}
    log_f = (a.root / "live.log").open("w")
    proc = subprocess.Popen(["uv", "run", "--frozen", "brunder", "us", "live", *map(str, CONFIGS), "--out", str(out),
                             "--interval", "5"], cwd=ROOT, env=env, stdout=log_f, stderr=subprocess.STDOUT)
    stop, stats = threading.Event(), {"reads": 0, "bad": []}
    watcher = threading.Thread(target=watch_atomic, args=(out, stop, stats), daemon=True)
    watcher.start()
    try:
        t_wait = time.time()                                      # first pass = the 5 h backfill
        while "c=" not in (a.root / "live.log").read_text() and time.time() - t_wait < 900:
            time.sleep(1)
        print(f"live process published its first pass after {time.time() - t_wait:.0f} s", flush=True)
        for k in range(PAST, STEPS):                              # the rest of the night, in real time
            t = datetime.now(timezone.utc)
            if k == PAST + 1:                                     # corrupt files, as a crashed writer would leave
                tx = snap / us.FEEDS["tx"]["county"] / f"{t:%Y%m%dT%H%M%S.%fZ}.json.gz"
                tx.write_bytes(gzip.compress(tx_payload({"sen": sims["tx-sen"][sims["tx-sen"]["step"] == k],
                                                         "gov": sims["tx-gov"][sims["tx-gov"]["step"] == k]}))[:5000])
                rec.save(f"ga_enr_Georgia_item_{GA_ITEMS['ga-sen']}", b'{"ballotItemWithBreakdown": {"name": []}}',
                         "json", t)
                kb = snap / "kalshi_books" / f"{t:%Y%m%dT%H%M%S.%fZ}.json"
                kb.write_bytes(kalshi_books(cfgs, truth, k, rng)[:200])
                print(f"step {k}: wrote a truncated TX file, an unparsable GA Senate item and a truncated Kalshi "
                      f"books file at {t:%H:%M:%S}; holding them as the latest snapshots across a full model minute",
                      flush=True)
                time.sleep(75)
                continue
            write_step(rec, k, t, {r: s[s["step"] == k] for r, s in sims.items()}, cfgs, priors, truth, rng)
            print(f"step {k} written at {t:%H:%M:%S}", flush=True)
            time.sleep(a.step_seconds)
        time.sleep(70)                                            # let the last minute tick and be published
    finally:
        proc.terminate()
        proc.wait(timeout=60)
    stop.set()
    watcher.join()
    log_f.close()
    log = (a.root / "live.log").read_text()
    rep = check(out, log)
    rep["atomic"] = {"reads": stats["reads"], "partial_reads": stats["bad"][:5], "n_partial": len(stats["bad"])}
    rep["truth"] = {k: round(v, 4) for k, v in truth.items()}
    rep["output"] = str(out / "us2026")
    print(json.dumps(rep, indent=1, default=str))
    (a.root / "report.json").write_text(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
