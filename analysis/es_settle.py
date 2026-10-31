"""Settle the Spain races from the official result and annotate stats.json (run AFTER the count).

    # final national file of the results feed (or the Interior Ministry's published final result, same schema)
    uv run python analysis/es_settle.py --national results.json [--provisional] [--site site/data]
    uv run python analysis/decided_stats.py es2026        # run by this script too

For every race in meta.json it decides the real outcome the way the markets do: the party with the most seats wins;
seat ties go to the party with more votes; a vote-share line is reached when the party's (or parties') share of valid
votes, blank included, rounded to two decimals, is at or above the line. It writes stats.json["settle"][race] =
{"outcome_a": bool}, which analysis/decided_stats.py reads, then scores the model's last probability and its 90%
share interval. Votes cast abroad arrive in the general count (4-7 December); until the final result is given, the
settle is labelled provisional.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brunder import spain as es

import decided_stats

ROOT = Path(__file__).resolve().parents[1]


def national_lanes(national: dict, cfg: dict) -> tuple[dict, int]:
    """Votes and seats per 2026 lane from a national results file: results[].{acronym,name,family,votes,seats}."""
    lanes = {x: {"votes": 0, "seats": 0} for x in es.LANES}
    for r in national["results"]:
        lane = es.lane_2026(f"{r.get('acronym') or ''} {r.get('name') or ''} {r.get('family') or ''}", cfg.get("lanes"))
        lanes[lane]["votes"] += int(r["votes"])
        lanes[lane]["seats"] += int(r.get("seats") or 0)
    seats = sum(v["seats"] for v in lanes.values())
    if seats not in (0, 350):
        raise ValueError(f"seats add up to {seats}, not 350")
    return lanes, int(national["totals"]["valid"])


def outcomes(national: dict, cfg: dict) -> dict:
    """{race_id: bool}: did the race's Yes outcome happen?"""
    lanes, valid = national_lanes(national, cfg)
    key = {k: v["seats"] * 1e10 + v["votes"] for k, v in lanes.items() if k != "OTHER"}
    out = {}
    for r in cfg["races"]:
        if r["kind"] == "rank":
            out[r["id"]] = 1 + sum(key[k] > key[r["lane"]] for k in key) == r["rank"]
        else:
            share = round(100 * sum(lanes[x]["votes"] for x in r["lanes"]) / valid, 2)
            out[r["id"]] = share >= r["threshold"]
    return out


def settle(national: dict, site: Path, cfg_path: Path, provisional: bool) -> dict:
    cfg = es.load_config(cfg_path)
    won = outcomes(national, cfg)
    lanes, valid = national_lanes(national, cfg)
    d = site / es.EID
    stats = json.loads((d / "stats.json").read_text())
    timeline = json.loads((d / "timeline.json").read_text())
    stats["settle"] = {rid: {"outcome_a": bool(v)} for rid, v in won.items()}
    brier, cover = [], []
    for r in cfg["races"]:
        last = next((x for x in reversed(timeline[r["id"]]) if x.get("P") is not None), None)
        if not last:
            continue
        brier.append((last["P"] - float(won[r["id"]])) ** 2)
        if r["kind"] == "share" and r["threshold"] == 50.0 and last.get("share") is not None and last.get("sd"):
            actual = sum(lanes[x]["votes"] for x in r["lanes"]) / valid
            cover.append(abs(actual - last["share"]) <= 1.645 * last["sd"])
    stats["settle_summary"] = {"n_races": len(won), "provisional": provisional,
                               "mean_brier_last_P": sum(brier) / len(brier) if brier else None,
                               "share_interval_coverage_90": sum(cover) / len(cover) if cover else None, "n_share_races": len(cover)}
    (d / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")))
    decided_stats.annotate(d)
    return stats["settle_summary"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--national", type=Path, required=True)
    ap.add_argument("--site", type=Path, default=ROOT / "site" / "data")
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "es2026_v1.toml")
    ap.add_argument("--provisional", action="store_true", help="night count, before the general count adds votes from abroad")
    a = ap.parse_args()
    print(json.dumps(settle(json.loads(a.national.read_text()), a.site, a.config, a.provisional), indent=1))


if __name__ == "__main__":
    main()
