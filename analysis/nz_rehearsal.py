"""Build a rehearsal archive: the 2023 election-night count replayed in the 2026 official-feed format.

The archive has the layout the recorder writes (``<root>/<key>/<time>.xml`` + ``index.jsonl``), so the live exporter
(``brunder nz live``) can be run end to end on it.  Contents:

* the REAL 2026 reference files (parties.xml, electorates.xml, candidates.xml, election.xml, statistics.xml) fetched
  from the Commission's public media feed with an honest User-Agent (or copied from ``--ref``);
* one ``eNN/eNN.xml`` per 2026 electorate, mapped to its 2023 predecessor (``brunder.newzealand.CROSSWALK``), whose
  official 2023 voting-place results are released in the simulated order ``nz_replay.Order(..., "night")``: all advance
  places first (06:04-06:14Z), then election-day places (06:20-09:40Z).  Party/candidate columns of parties that do not
  stand in 2026 are dropped, and 2026 parties absent in 2023 stay at zero.

Simulated clock, official place results: this is a software rehearsal, not calibration data.

    uv run python analysis/nz_rehearsal.py build --out ~/nz_v3_local/rehearsal/snap [--ref DIR] [--seed 1]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import nz_replay as R            # noqa: E402
from brunder import newzealand as nz   # noqa: E402

UA = {"User-Agent": "brunder-research/0.1 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}
REFS = ("parties", "electorates", "candidates", "election", "statistics")
RENAME = {"NEWZEALAND": "NLY", "WOMENSRIGH": "WRP"}       # 2023 key slugs -> 2026 feed abbreviations
CLOSE = datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc)


def fetch_refs(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        for r in REFS:
            if not (dest / f"{r}.xml").exists():
                (dest / f"{r}.xml").write_bytes(c.get(f"{nz.BASE}/{r}.xml").raise_for_status().content)


def release_minutes(order: R.Order) -> np.ndarray:
    """Minutes after poll close at which each ordinary voting place's result is published."""
    adv = order.kind == "advance"
    n_adv, n_ed = int(adv.sum()), int((~adv).sum())
    rank = order.rank
    t = np.empty(order.N)
    t[adv] = 4 + 10 * rank[adv] / max(n_adv, 1)
    t[~adv] = 20 + 200 * ((rank[~adv] - n_adv) / max(n_ed, 1)) ** 0.8
    return t


def build(out: Path, ref: Path, seed: int = 1, cadence_min: int = 2, end_min: int = 225) -> dict:
    E = R.election(2023)
    order = R.Order(E, "night", seed)
    t_rel = release_minutes(order)
    fetch_refs(ref)
    out.mkdir(parents=True, exist_ok=True)
    index = out / "index.jsonl"
    index.write_text("")
    parties = nz.parse_parties((ref / "parties.xml").read_bytes())
    electorates = nz.parse_electorates((ref / "electorates.xml").read_bytes())
    candidates = nz.parse_candidates((ref / "candidates.xml").read_bytes())
    registered = [int(n) for n, r in parties.iterrows() if r["registered"]]
    keys23 = [RENAME.get(k, k) for k in E.party_keys]
    col = {k: j for j, k in enumerate(keys23)}
    K = len(keys23)
    src = {e_no: nz.CROSSWALK.get(r["name"], r["name"]) for e_no, r in electorates.iterrows()}
    idx23 = {e: i for i, e in enumerate(E.electorates)}
    missing = [n for n in src.values() if n not in idx23]
    if missing:
        raise ValueError(f"no 2023 predecessor for {missing}")

    def write(key: str, body: bytes, t: datetime) -> None:
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.xml"
        f.write_bytes(body)
        with index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(out)), "gzip": False}) + "\n")

    t0 = CLOSE - timedelta(hours=1)
    for r in REFS:
        write(nz._key(f"{nz.BASE}/{r}.xml"), (ref / f"{r}.xml").read_bytes(), t0)

    n_e = len(E.electorates)
    cand_by_e = {e: candidates[candidates["e_no"] == e].copy() for e in electorates.index}
    abbrev = {int(n): r["abbrev"] for n, r in parties.iterrows()}
    last: dict[int, tuple] = {}

    def xml(e_no: int, upd: datetime, places: int, total: int, pv: np.ndarray, cv: np.ndarray) -> bytes:
        root = ET.Element("electorate", {"e_no": str(e_no), "final": "true" if places >= total else "false",
                                         "updated": upd.strftime("%Y-%m-%dT%H:%M:%S")})
        st = ET.SubElement(root, "statistics")
        for tag, v in (("total_voting_places", total), ("total_voting_places_counted", places),
                       ("percent_voting_places_counted", round(100 * places / max(total, 1), 1)),
                       ("total_votes_cast", int(pv.sum())), ("percent_votes_cast", 0.0),
                       ("total_party_informals", 0), ("total_candidate_informals", 0),
                       ("total_registered_parties", len(registered)), ("total_candidates", len(cand_by_e[e_no]))):
            ET.SubElement(st, tag).text = str(v)
        pe = ET.SubElement(root, "partyvotes")
        for n in registered:
            p = ET.SubElement(pe, "party", {"p_no": str(n)})
            ET.SubElement(p, "votes").text = str(int(pv[col[abbrev[n]]]) if abbrev[n] in col else 0)
        ce = ET.SubElement(root, "candidatevotes")
        ind_given = False
        for c_no, c in cand_by_e[e_no].iterrows():
            ab = abbrev.get(int(c["p_no"]))
            if ab in col and ab in nz.PARTY_KEYS:
                v = int(cv[col[ab]])
            elif not ind_given:
                v, ind_given = int(cv[K]), True        # 2023 independents / other parties go to one 2026 candidate
            else:
                v = 0
            c_el = ET.SubElement(ce, "candidate", {"c_no": str(c_no)})
            ET.SubElement(c_el, "votes").text = str(v)
        return ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    steps = 0
    for m in range(-2, end_min + 1, cadence_min):
        t = CLOSE + timedelta(minutes=m)
        done = t_rel <= m
        for e_no, name in src.items():
            i = idx23[name]
            sel = done & (order.ei == i)
            places, total = int(sel.sum()), int((order.ei == i).sum())
            sig = (places, total)
            if last.get(e_no) == sig:
                continue
            last[e_no] = sig
            pv, cv = order.P[sel].sum(0), order.C[sel].sum(0)
            pv2 = np.zeros(K)
            for j, k in enumerate(E.party_keys):
                pv2[col[RENAME.get(k, k)]] += pv[j]
            cv2 = np.zeros(K + 1)
            for j, k in enumerate(E.party_keys):
                cv2[col[RENAME.get(k, k)]] += cv[j]
            cv2[K] = cv[K]
            write(nz._key(f"{nz.BASE}/e{e_no:02d}/e{e_no:02d}.xml"), xml(e_no, t, places, total, pv2, cv2), t)
        steps += 1
    return {"snapshots": steps, "electorates": len(src), "places": int(order.N), "end": (CLOSE + timedelta(minutes=end_min)).isoformat()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--out", type=Path, default=Path.home() / "nz_v3_local" / "rehearsal" / "snap")
    ap.add_argument("--ref", type=Path, default=Path.home() / "nz_v3_local" / "rehearsal" / "ref")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    if a.out.exists():
        shutil.rmtree(a.out)
    print(json.dumps(build(a.out, a.ref, a.seed)))
