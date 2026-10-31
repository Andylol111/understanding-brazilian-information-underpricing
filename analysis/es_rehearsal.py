"""Software rehearsal of the Spain recorder and live exporter on earlier nights (retrospective, not pre-registered).

Real FINAL official province results of an earlier election (2019-11-10 or 2023-07-23) are released on a simulated
clock in the feed's own JSON format, as if they were the 2026 count. The count order is simulated (provinces report
at different speeds; the counted part of each province is a slightly biased sample of it, the bias size taken from
analysis/es_calibrate.py). The prior for the replayed night is the PREVIOUS election, never the night itself.
This tests the software end to end; it is not a calibration and not evidence about 2026.

    # 1. write a recorder-format archive straight to disk and export it
    uv run python analysis/es_rehearsal.py build --night 2023-07-23 --out $BRUNDER_DATA/es2026/rehearsal/2023
    uv run brunder spain export $BRUNDER_DATA/es2026/rehearsal/2023/cfg.toml \
        --snap $BRUNDER_DATA/es2026/rehearsal/2023/snap --out /tmp/es-site

    # 2. serve the same night over HTTP and let the real recorder fetch it (needs the clock factor)
    uv run python analysis/es_rehearsal.py serve --night 2019-11-10 --port 8765 --step-seconds 3
    uv run brunder spain record $BRUNDER_DATA/es2026/rehearsal/2019/cfg.toml --snap /tmp/es-rec --until ...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brunder import spain as es
from brunder.paths import DATA, ROOT

CLOSE = datetime(2026, 11, 29, 20, 0, tzinfo=timezone.utc)
PRIOR_OF = {"2019-11-10": "2019-04-28", "2023-07-23": "2019-11-10"}
ACRONYM = {"PP": ("PP", "PARTIDO POPULAR", "pp"), "PSOE": ("PSOE", "PARTIDO SOCIALISTA OBRERO ESPAÑOL", "psoe"),
           "VOX": ("VOX", "VOX", "vox"), "CS": ("Cs", "CIUDADANOS-PARTIDO DE LA CIUDADANÍA", None),
           "LEFT": ("FA", "FRENTE AMPLIO (rehearsal label)", "sumar"),
           "ERC": ("ERC", "ESQUERRA REPUBLICANA DE CATALUNYA", "erc"), "JUNTS": ("JxCAT - JUNTS", "JUNTS PER CATALUNYA", "junts"),
           "BILDU": ("EH Bildu", "EUSKAL HERRIA BILDU", "eh-bildu"), "PNV": ("EAJ-PNV", "EUZKO ALDERDI JELTZALEA", "eaj-pnv"),
           "BNG": ("B.N.G.", "BLOQUE NACIONALISTA GALEGO", "bng"), "CCA": ("CCa", "COALICION CANARIA", "cca"),
           "UPN": ("U.P.N.", "UNION DEL PUEBLO NAVARRO", "upn"), "CUP": ("CUP-PR", "CANDIDATURA D'UNITAT POPULAR", None),
           "OTHER": ("OTROS", "OTRAS CANDIDATURAS", None)}
STEP_MIN = 3
N_STEPS = 50
TABLE_SIZE = 621          # electors per table, 2023 national average (37.47M electors / 60,314 tables)


def rehearsal_config(night: str, out: Path) -> Path:
    """Copy configs/es2026_v1.toml with the prior moved to the election before `night`."""
    text = (ROOT / "configs" / "es2026_v1.toml").read_text()
    block = (f'[prior]\ndate = "{PRIOR_OF[night]}"\npoll_rake = false\nseats_date = "{night}"\n'
             f'public_file = "data_public/es_congreso_province_2016_2023.csv"\n\n')
    text = re.sub(r"\[prior\].*?(?=\[poll\])", lambda m: block, text, count=1, flags=re.S)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "cfg.toml"
    path.write_text(text)
    return path


def schedule(night: str, seed: int = 7, bias_nat: float = 0.035, bias_prov: float = 0.035, cera: float = 0.0083):
    """Per publication: {province code: JSON dict} plus a national dict, with the simulated release time."""
    h = es.load_history()
    d = h[h["election"] == night].set_index("prov").sort_index()
    rng = np.random.default_rng(seed)
    lanes = list(es.HIST_LANES)
    P, K = len(d), len(lanes)
    final = d[lanes].to_numpy(float) * (1 - cera)
    blank = d["blank"].to_numpy(float) * (1 - cera)
    total_tables = np.maximum(1, np.rint(d["electors"].to_numpy(float) / TABLE_SIZE)).astype(int)
    speed = np.exp(rng.normal(0, 0.35, P))
    e_nat = rng.normal(0, bias_nat, (1, K))
    e_prov = rng.normal(0, bias_prov, (P, K))
    steps = []
    for s in range(1, N_STEPS + 1):
        minute = 2 + (s - 1) * STEP_MIN
        tau = min(1.0, (1 - np.exp(-minute / 38)) / (1 - np.exp(-(2 + (N_STEPS - 1) * STEP_MIN) / 38)))
        f = np.clip(tau * speed, 0, 1)
        if s == N_STEPS:
            f = np.ones(P)
        eps = (e_nat + e_prov) * np.sqrt(4 * f * (1 - f))[:, None]
        counted = np.rint(np.clip(f[:, None] * final * (1 + eps), 0, final)).astype(int)
        if s == N_STEPS:
            counted = np.rint(final).astype(int)
        tables = np.rint(f * total_tables).astype(int)
        blank_c = np.rint(np.where(final.sum(axis=1) > 0, counted.sum(axis=1) * blank / final.sum(axis=1), 0)).astype(int)
        provs = {}
        for i, code in enumerate(d.index):
            results = []
            for j, lane in enumerate(lanes):
                acr, name, fam = ACRONYM[lane]
                results.append({"candidacy": f"{j:06d}", "acronym": acr, "name": name, "family": fam, "votes": int(counted[i, j])})
            provs[code] = {"scope": {"type": "prov", "code": code, "name": d.loc[code, "name"]},
                           "seq": s, "counted": {"tables": int(tables[i]), "total_tables": int(total_tables[i]),
                                                  "pct": round(100 * tables[i] / total_tables[i], 2)},
                           "totals": {"valid": int(counted[i].sum() + blank_c[i]), "blank": int(blank_c[i]), "null": 0},
                           "results": results}
        counted_pct = round(100 * tables.sum() / total_tables.sum(), 2)
        nat = {"scope": {"type": "es", "code": "00", "name": "España"}, "seq": s,
               "counted": {"tables": int(tables.sum()), "total_tables": int(total_tables.sum()), "pct": counted_pct},
               "totals": {"valid": int(sum(p["totals"]["valid"] for p in provs.values())),
                          "blank": int(blank_c.sum()), "null": 0}, "results": []}
        steps.append({"seq": s, "t": CLOSE + timedelta(minutes=minute), "counted_pct": counted_pct, "prov": provs, "es": nat})
    return steps


def manifest(step: dict, base: str) -> dict:
    return {"version": 1, "election": "congreso-2026-11", "seq": step["seq"],
            "generated_at": step["t"].strftime("%Y-%m-%dT%H:%M:%SZ"), "phase": "live", "counted_pct": step["counted_pct"],
            "stale": False, "embargo": False, "base": f"{base.rstrip('/')}/{step['seq']}/",
            "source": "REHEARSAL: final official results of an earlier election on a simulated clock", "source_url": None,
            "official": False}


def build(night: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    cfg = rehearsal_config(night, out)
    snap = out / "snap"
    n = 0
    for step in schedule(night):
        t = step["t"]
        es._save_snapshot(snap, "manifest", json.dumps(manifest(step, "http://rehearsal.invalid")).encode(), t)
        es._save_snapshot(snap, "es", json.dumps(step["es"], sort_keys=True).encode(), t)
        for code, doc in step["prov"].items():
            es._save_snapshot(snap, f"prov/{es.uid_of(code)}", json.dumps(doc, sort_keys=True).encode(), t)
            n += 1
    return {"night": night, "config": str(cfg), "snap": str(snap), "province_files": n,
            "first": CLOSE.isoformat(), "last": step["t"].isoformat()}


def serve(night: str, port: int, step_seconds: float) -> None:
    steps = schedule(night)
    started = time.time()
    base = f"http://127.0.0.1:{port}/v1/congreso-2026-11"

    def current() -> dict:
        i = min(len(steps) - 1, int((time.time() - started) / step_seconds))
        return steps[i]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            body = None
            if path == "/v1/congreso-2026-11/manifest.json":
                body = manifest(current(), base)
            else:
                m = re.fullmatch(r"/v1/congreso-2026-11/(\d+)/(es|prov/(\d\d))\.json", path)
                if m:
                    st = steps[int(m.group(1)) - 1]
                    body = st["es"] if m.group(2) == "es" else st["prov"].get(m.group(3))
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"serving {night} on {base} ({len(steps)} publications, one per {step_seconds}s)", flush=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(len(steps) * step_seconds + 5)
    srv.shutdown()


def score(night: str, site: Path) -> dict:
    """Last P for each race of an exported replay, against what the final official results say."""
    h = es.load_history()
    d = h[h["election"] == night].set_index("prov").sort_index()
    votes = d[list(es.HIST_LANES)].to_numpy(float)
    seat_by = dict(zip(es.HIST_LANES, d[[f"seats_{k}" for k in es.HIST_LANES]].sum().to_numpy()))
    key = {k: seat_by[k] * 1e10 + votes[:, i].sum() for i, k in enumerate(es.HIST_LANES)}
    rank = {k: 1 + sum(key[j] > key[k] for j in key) for k in key}
    valid = d["valid"].sum()
    share = {k: 100 * votes[:, i].sum() / valid for i, k in enumerate(es.HIST_LANES)}
    tl = json.loads((site / es.EID / "timeline.json").read_text())
    cfg = es.load_config(ROOT / "configs" / "es2026_v1.toml")
    rows = []
    for r in cfg["races"]:
        last = tl[r["id"]][-1]
        lane = {"VOX": "VOX"}.get(r.get("lane"), r.get("lane"))
        if r["kind"] == "rank":
            truth = rank[lane] == r["rank"]
        else:
            truth = sum(share[{"VOX": "VOX"}.get(x, x)] for x in r["lanes"]) >= r["threshold"]
        rows.append({"race": r["id"], "P_final": last["P"], "truth": bool(truth), "counted": last["counted"]})
    return {"night": night, "rank": {k: int(rank[k]) for k in ("PP", "PSOE", "VOX")},
            "share": {k: round(float(share[k]), 2) for k in ("PP", "PSOE", "VOX")}, "races": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "serve", "score"])
    ap.add_argument("--night", choices=sorted(PRIOR_OF), required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--site", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--step-seconds", type=float, default=3.0)
    a = ap.parse_args()
    if a.cmd == "build":
        print(json.dumps(build(a.night, a.out or DATA / "es2026" / "rehearsal" / a.night[:4]), indent=1))
    elif a.cmd == "serve":
        rehearsal_config(a.night, a.out or DATA / "es2026" / "rehearsal" / a.night[:4])
        serve(a.night, a.port, a.step_seconds)
    else:
        print(json.dumps(score(a.night, a.site), indent=1))
