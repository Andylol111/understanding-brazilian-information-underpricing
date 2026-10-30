"""Build a recorder-format archive from real official 2022 Taipei station results.

This is an end-to-end software rehearsal, not calibration: real final station votes are released on a simulated
clock and mapped to the 2026 candidate slots (2022 KMT->Chiang, DPP->Shen, Huang->Kuo).

    uv run python analysis/tw_rehearsal.py --out /tmp/tw-rehearsal/snap
    uv run brunder taiwan export configs/tw2026_taipei_v1.toml --snap /tmp/tw-rehearsal/snap --out /tmp/tw-site
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from brunder import taiwan as tw

CLOSE = datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)


def build(out: Path, seed: int = 1, end_minutes: int = 180) -> dict:
    cfg = tw.load_config(Path("configs/tw2026_taipei_v1.toml"))
    d = tw.load_history(); d = d[d.year == 2022].copy().reset_index(drop=True)
    rng = np.random.default_rng(seed)
    # Small stations tend to finish first, with enough jitter to avoid treating this as an observed order.
    score = d["valid"].to_numpy() + rng.normal(0, 175, len(d))
    rank = np.empty(len(d), dtype=int); rank[np.argsort(score)] = np.arange(len(d))
    minute = 4 + np.rint((end_minutes - 4) * (rank / max(len(d) - 1, 1)) ** 1.25).astype(int)
    second = rng.integers(0, 60, len(d))
    d["released"] = [CLOSE + timedelta(minutes=int(m), seconds=int(s)) for m, s in zip(minute, second)]
    d = d.sort_values("released")
    totals: dict[str, dict[str, int]] = {}
    places: dict[str, int] = {}
    total_places = d.groupby("unit_id")["station_id"].nunique().to_dict()
    mapping = {"KMT": "chiang", "DPP": "shen", "TPP": "kuo", "OTHER": "lin"}
    snapshots = 0
    for row in d.itertuples():
        vote = totals.setdefault(row.unit_id, {c["id"]: 0 for c in cfg["candidates"]})
        for old, new in mapping.items(): vote[new] += int(getattr(row, old))
        places[row.unit_id] = places.get(row.unit_id, 0) + 1
        payload = {"places": places[row.unit_id], "places_total": total_places[row.unit_id],
                   "votes": vote.copy(), "stamp": row.released.strftime("%m/%d %H:%M")}
        tw._save_snapshot(out, f"results/{row.unit_id}",
                          json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(), row.released)
        snapshots += 1
    return {"snapshots": snapshots, "units": len(totals), "stations": len(d),
            "start": d.released.min().isoformat(), "end": d.released.max().isoformat()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", type=Path, required=True); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(); print(json.dumps(build(a.out, a.seed), indent=2))
