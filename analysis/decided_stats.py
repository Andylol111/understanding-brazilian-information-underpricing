#!/usr/bin/env python3
"""Annotate published stats with pre-count market classification.

This is deliberately a post-export step: every replay exporter can keep its own model
pipeline, while this one scoring rule is applied identically to their public contract files.
It reads only site/data/{meta,timeline,stats}.json; no raw data or network is needed.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def outcome_a(rows: list[dict], settle: dict | None) -> bool | None:
    if settle and settle.get("outcome_a") is not None:
        return bool(settle["outcome_a"])
    final = next((r for r in reversed(rows) if any(r.get(k) is not None for k in ("P_v2", "P", "raw", "share"))), None)
    if final:
        p = final.get("P_v2")
        if p is None:
            p = final.get("P")
        if p is not None and (p <= .005 or p >= .995):
            return p > .5
        share = final.get("raw")
        if share is None:
            share = final.get("share")
        if share is not None and share != .5:
            return share > .5
    p = next((r["pm"] for r in reversed(rows) if r.get("pm") is not None), None)
    return None if p is None or p == .5 else p > .5


def classify(rows: list[dict], poll_close: str, settle: dict | None) -> dict:
    a_won = outcome_a(rows, settle)
    close = instant(poll_close)
    priced = [r for r in rows if r.get("pm") is not None and instant(r["t"]) >= close]
    first = priced[0] if priced else None
    win_price = lambda r: None if not r or a_won is None else (r["pm"] if a_won else 1 - r["pm"])
    at_close = win_price(first)
    floor = min((win_price(r) for r in priced), default=None)
    decided = at_close is not None and at_close >= .90 and floor is not None and floor >= .80
    return {"decided_before_count": decided, "outcome_a": a_won,
            "price_at_poll_close": None if at_close is None else round(at_close, 4),
            "minimum_price_after_poll_close": None if floor is None else round(floor, 4)}


def annotate(election_dir: Path) -> bool:
    paths = {name: election_dir / f"{name}.json" for name in ("meta", "timeline", "stats")}
    if not all(p.exists() for p in paths.values()):
        return False
    meta, timeline, stats = (json.loads(paths[n].read_text()) for n in ("meta", "timeline", "stats"))
    close = meta.get("poll_close_utc")
    if not close or not isinstance(timeline, dict):
        return False
    by_race: dict[str, dict] = {}
    for race in meta.get("races", []):
        rid = race["race_id"]
        rows = timeline.get(rid, [])
        has_pm = any(m.get("platform") == "polymarket" for m in race.get("markets", [])) and any(r.get("pm") is not None for r in rows)
        if has_pm:
            by_race[rid] = classify(rows, close, (stats.get("settle") or {}).get(rid))
    contested = {rid for rid, d in by_race.items() if not d["decided_before_count"]}
    gated_leads: list[float] = []
    raw_leads: list[float] = []
    by_lead: dict[str, float] = {}
    for rid in contested:
        g = (stats.get("settle_gated") or {}).get(rid, {})
        private = ((stats.get("lead") or {}).get("by_race") or {}).get(rid, {})
        gated = g.get("lead") if g.get("lead_vs", "polymarket") == "polymarket" else None
        if gated is None:
            gated = private.get("lead_min_gate1pct")
        if isinstance(gated, (int, float)):
            gated_leads.append(float(gated)); by_lead[rid] = float(gated)
        s = (stats.get("settle") or {}).get(rid, {})
        raw = s.get("lead_min", private.get("lead_min"))
        if raw is None and s.get("model") and s.get("polymarket"):
            raw = (instant(s["polymarket"]) - instant(s["model"])).total_seconds() / 60
        if isinstance(raw, (int, float)):
            raw_leads.append(float(raw))
    n_decided = sum(d["decided_before_count"] for d in by_race.values())
    summary = {
        "n": len(gated_leads), "n_contested_markets": len(contested),
        "median_lead_min": statistics.median(raw_leads) if raw_leads else None,
        "median_lead_min_gate1pct": statistics.median(gated_leads) if gated_leads else None,
        "model_first": sum(x > 0 for x in raw_leads), "market_first": sum(x < 0 for x in raw_leads),
        "model_first_gate1pct": sum(x > 0 for x in gated_leads), "market_first_gate1pct": sum(x < 0 for x in gated_leads),
        "by_race": by_lead,
    }
    stats["n_markets"] = len(by_race)
    stats["n_decided_before_count"] = n_decided
    stats["market_classification"] = {"definition": "eventual outcome >= 0.90 at the first minute at/after official poll close and never below 0.80 afterwards", "by_race": by_race}
    stats["lead_contested"] = summary
    if isinstance(stats.get("settle_summary"), dict):
        stats["settle_summary"].update({**{k: v for k, v in summary.items() if k != "by_race"},
                                        "contested_markets": len(contested), "decided_before_count": n_decided})
    if isinstance(stats.get("lead"), dict) and isinstance(stats["lead"].get("by_race"), dict):
        stats["lead"].update({**{k: v for k, v in summary.items() if k != "by_race"},
                              "contested_markets": len(contested), "decided_before_count": n_decided})
    paths["stats"].write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")))
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()
    dirs = [DATA / x for x in args.ids] if args.ids else sorted(p.parent for p in DATA.glob("*/meta.json"))
    changed = [p.name for p in dirs if annotate(p)]
    print(f"annotated {len(changed)} elections: {' '.join(changed)}")


if __name__ == "__main__":
    main()
