"""Dry run of the US election-night pipeline on a SYNTHETIC night (never publish its output).

Builds a fake snapshot archive in the recorder's layout — feed payloads in the real formats of CA, TX and GA
(templates: tests/fixtures/us), with county results drawn around the priors and posted in US-style batches
(early + mail first, Election Day in chunks) — plus Polymarket books, then runs brunder.us.Exporter over every
config and checks the dashboard contract files. Ohio is included only if its prior (MEDSL) is present; Alaska is
not modelled (docs/us_midterms.md).

    BRUNDER_DATA=… uv run python analysis/us_dryrun.py [--out /tmp/us2026_dryrun]
"""

from __future__ import annotations

import argparse
import base64
import copy
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us"
T0 = pd.Timestamp("2026-11-04T00:00:00Z")
STEPS = 37                                         # every 10 min for 6 h


def _fix(name: str) -> bytes:
    b = (FIX / name).read_bytes()
    return gzip.decompress(b) if name.endswith(".gz") else b


def night(pri: pd.DataFrame, swing: float, rng: np.random.Generator, trng: np.random.Generator) -> pd.DataFrame:
    """Cumulative counted two-party votes per unit and step: d, r, ed, er (early part), done flag.
    trng: count timing / vote-type mix, shared by the races of one state (one county count, several races)."""
    n = len(pri)
    truth = np.clip(pri["s0"].to_numpy() + swing + rng.normal(0, 0.02, n), 0.02, 0.98)
    total = pri["V"].to_numpy() * rng.uniform(0.9, 1.1, n)
    early = trng.uniform(0.5, 0.7, n)
    gap = 0.08                                     # early votes lean Democratic by 8 points (as in GA 2022)
    s_e = np.clip(truth + gap * (1 - early), 0, 1)
    s_l = np.clip((truth - early * s_e) / (1 - early), 0, 1)
    t_early = trng.integers(1, 6, n)
    t_done = t_early + trng.integers(6, 30, n)
    rows = []
    for k in range(STEPS):
        late_frac = np.clip((k - t_early) / (t_done - t_early), 0, 1)
        ed = np.where(k >= t_early, total * early * s_e, 0)
        er = np.where(k >= t_early, total * early * (1 - s_e), 0)
        ld, lr = total * (1 - early) * s_l * late_frac, total * (1 - early) * (1 - s_l) * late_frac
        rows.append(pd.DataFrame({"step": k, "unit_id": pri["unit_id"], "d": ed + ld, "r": er + lr, "ed": ed, "er": er,
                                  "done": k >= t_done}))
    out = pd.concat(rows, ignore_index=True)
    out[["d", "r", "ed", "er"]] = out[["d", "r", "ed", "er"]].round()
    return out


def ca_payload(s: pd.DataFrame, pri: pd.DataFrame) -> bytes:
    tpl = json.loads(_fix("ca_governor_county_all.json"))
    name = dict(zip(pri["unit_id"], pri["name"]))
    by = s.set_index("unit_id")
    out = []
    for r in tpl:
        r = copy.deepcopy(r)
        where = r["raceTitle"].split(" - ", 1)[1].replace(" County Results", "")
        if where.startswith("Statewide"):
            d, rep = by["d"].sum(), by["r"].sum()
        else:
            uid = next(u for u, nm in name.items() if us._norm(nm) == us._norm(where))
            d, rep = by.at[uid, "d"], by.at[uid, "r"]
        r["candidates"][0]["Votes"], r["candidates"][1]["Votes"] = f"{int(d):,}", f"{int(rep):,}"
        out.append(r)
    return json.dumps(out).encode()


def tx_payload(states: dict[str, pd.DataFrame]) -> bytes:
    c = us.counties("tx")
    races = {"sen": ("5075", "U. S. SENATOR "), "gov": ("3579", "GOVERNOR")}
    up = {}
    for i, uid in enumerate(c["unit_id"], start=1):
        entry = {"N": c["name"].iat[i - 1].upper(), "Races": {}}
        for race, s in states.items():
            row = s.set_index("unit_id").loc[uid]
            rid, rname = races[race]
            entry["Races"][rid] = {"N": rname, "C": {
                "1": {"N": "DEM CANDIDATE", "P": "DEM", "V": int(row["d"]), "EV": int(row["ed"])},
                "2": {"N": "REP CANDIDATE", "P": "REP", "V": int(row["r"]), "EV": int(row["er"])}}}
            done = bool(row["done"])
        entry["Summary"] = {"PLR": 10.0 if done else 3.0, "NPL": 10.0, "PRR": 0.0, "PRP": 10.0, "VC": 0.0, "RV": 0.0}
        up[str(i)] = entry
    return json.dumps({"upload": base64.b64encode(json.dumps(up).encode()).decode()}).encode()


def ga_payload(s: pd.DataFrame, title: str) -> bytes:
    c = us.counties("ga").set_index("unit_id")

    def opt(party, votes, early):
        return {"name": [{"languageId": "en", "text": f"{party} Candidate ({party[:3]})"}], "voteCount": int(votes),
                "party": {"abbreviation": party[:3]},
                "groupResults": [{"groupName": [{"text": "Advanced Voting"}], "voteCount": int(early)},
                                 {"groupName": [{"text": "Election Day"}], "voteCount": int(votes - early)}]}

    br = [{"locality": {"name": [{"text": f"{c.at[r.unit_id, 'name']} County"}]},
           "precinctReportingStatus": {"reportingUnits": 10 if r.done else 3, "totalUnits": 10},
           "ballotOptions": [opt("Dem", r.d, r.ed), opt("Rep", r.r, r.er)]} for r in s.itertuples()]
    tot = s[["d", "r", "ed", "er"]].sum()
    return json.dumps({"ballotItemWithBreakdown": {
        "name": [{"text": title}], "reportingStatus": {"reportingUnits": 0, "totalUnits": 159},
        "summaryResults": {"ballotOptions": [opt("Dem", tot["d"], tot["ed"]), opt("Rep", tot["r"], tot["er"])]},
        "breakdownResults": br}}).encode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("/tmp/us2026_dryrun"))
    a = ap.parse_args()
    rng = np.random.default_rng(1103)
    priors = us.load_priors()
    cfg_paths = sorted((ROOT / "configs").glob("us2026_*_v1.toml"))
    cfgs = {p: us.load_config(p) for p in cfg_paths}
    sims, truth = {}, {}
    for p, c in cfgs.items():
        pri = us.race_prior(c, priors)
        if pri is None:
            print(f"{c['election']['race_id']}: prior missing, skipped")
            continue
        pri = pri.assign(V=pri["V"] * us.rules(c).turnout_ratio)
        swing = rng.normal(0, 0.03)
        trng = np.random.default_rng(sum(map(ord, c["election"]["state"])))
        sims[c["election"]["race_id"]] = night(pri, swing, rng, trng)
        fin = sims[c["election"]["race_id"]].query(f"step == {STEPS - 1}")
        truth[c["election"]["race_id"]] = fin["d"].sum() / (fin["d"] + fin["r"]).sum()
    snap = a.out / "snap"
    for k in range(STEPS):
        t = T0 + pd.Timedelta(minutes=10 * k)
        stamp = f"{t:%Y%m%dT%H%M%S}.000000Z"
        at = {rid: s[s["step"] == k] for rid, s in sims.items()}
        files = {}
        if "ca-gov" in at:
            files[us.FEEDS["ca"]["results"]] = ca_payload(at["ca-gov"], us.race_prior(cfgs[ROOT / "configs" / "us2026_ca_gov_v1.toml"], priors))
        if {"tx-sen", "tx-gov"} <= set(at):
            files[us.FEEDS["tx"]["county"]] = tx_payload({"sen": at["tx-sen"], "gov": at["tx-gov"]})
        for rid, title in (("ga-sen", "US Senate"), ("ga-gov", "Governor")):
            if rid in at:
                files[f"ga_enr_Georgia_item_{rid}"] = ga_payload(at[rid], title)
        books = {}
        for c in cfgs.values():
            pm = c["markets"]["polymarket"]
            p = float(np.clip(0.5 + 0.4 * np.tanh(5 * (truth.get(c["election"]["race_id"], 0.5) - 0.5)) * k / STEPS, .02, .98))
            books[f"{pm['event']}/{pm['market']}"] = {"bids": [{"price": f"{p - .01:.2f}", "size": "100"}],
                                                       "asks": [{"price": f"{p + .01:.2f}", "size": "100"}]}
        files["polymarket_books"] = json.dumps(books).encode()
        for key, body in files.items():
            (snap / key).mkdir(parents=True, exist_ok=True)
            (snap / key / f"{stamp}.json").write_bytes(body)
    ex = us.Exporter(cfg_paths, snap, priors=priors)
    ex.update()
    d = ex.write(a.out / "site")
    meta = json.loads((d / "meta.json").read_text())
    tl = json.loads((d / "timeline.json").read_text())
    print(f"wrote {d}: {len(meta['races'])} races in meta")
    for r in meta["races"]:
        rid = r["race_id"]
        rows = tl.get(rid) or []
        if not rows:
            print(f"  {rid}: no rows (prior missing)")
            continue
        u = json.loads((d / r["units"]).read_text())
        n = len(u["unit_ids"])
        assert all(len(u[k]) == n for k in ("region", "names", "prior", "V0"))
        assert all(len(fr[k]) == n for fr in u["frames"] for k in ("f", "a", "proj")) and len(u["frames"]) <= 60
        for row in rows:
            assert set(row) == {"t", "counted", "raw", "share", "sd", "P", "pm", "kalshi"}
        by = {x["t"]: x for x in rows}
        snaps = [by.get(f"{T0 + pd.Timedelta(minutes=m):%Y-%m-%dT%H:%M:%SZ}") for m in (30, 90, 180, 359)]
        desc = "  ".join(f"c={x['counted']:.2f} raw={x['raw']:.3f} sh={x['share']:.3f}±{x['sd']:.3f} P={x['P']:.2f}"
                         for x in snaps if x and x["raw"] is not None)
        print(f"  {rid:7s} truth={truth[rid]:.3f} rows={len(rows)} frames={len(u['frames'])} units={n}\n      {desc}")


if __name__ == "__main__":
    main()
