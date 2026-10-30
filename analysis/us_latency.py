"""US midterms 2026: when did our probability cross a threshold, and when did the market's price cross it (to the second).

Read by analysis/us_score.py (display-only block `latency`); runnable alone:

    uv run python analysis/us_latency.py [--out site/data] [--snap DIR] [--ws DIR] [--updates FILE]

Inputs
  <out>/us2026/timeline.json   the model output: per race and minute P (v1) / P_<v> and the Polymarket P(a)
  <ws>/ws_crossings.jsonl      brunder.pm_ws: every move of a YES token's price (mid of best bid/ask when the spread
                               ≤ 10c, else the last trade) across LEVELS, with the server's timestamp `ts` (ms) and
                               its second `t`; default <snap>_ws (data/raw/snap/us_midterms_2026_ws)
  logs/us2026_updates.jsonl    optional, written by `brunder us live --updates` (brunder.us.UpdateLog): per model pass
                               and race `wall` (UTC, µs: when that pass wrote the race's files), `row_t` (its last
                               minute row), `P` / `P_<v>` and `cross` = [{"v","level","dir","row_t"}], the levels our
                               probability crossed in that update

A crossing is a move of the probability of side a to the other side of a level L in LEVELS (>= L is "up"), between two
consecutive observations. Our time = `wall` of the update (to the second; the update log) or, without a log, the row's
minute `t` (resolution: minute). The market's time = the server's timestamp of the event that moved it, floored to the
second; a YES price is converted to side a through the config's yes_is (level L on YES ↔ 1 − L on a, direction flipped).
Pairing: each model crossing takes the unused market crossing of the same level and direction nearest in time within
±WINDOW_MIN; `lead_s` = market − model, positive when the model crossed first. Unpaired crossings are counted.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

LEVELS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)     # brunder.pm_ws.LEVELS
WINDOW_MIN = 30


def _t(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _sec(ts: float) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def side_crossings(points: list[tuple[float, float]], levels=LEVELS) -> list[dict]:
    """[(time s, p)] in time order -> the crossings between consecutive points (the first point only sets the side)."""
    out, prev = [], None
    for t, p in points:
        if p is None:
            continue
        side = tuple(p >= L for L in levels)
        if prev is not None:
            out += [{"level": L, "dir": "up" if now else "down", "t": t, "p": p}
                    for L, was, now in zip(levels, prev, side) if was != now]
        prev = side
    return out


def model_crossings(rows: list[dict], col: str, updates: list[dict] | None = None) -> tuple[list[dict], str]:
    """(crossings of rows[col], resolution): from the update log's `cross` (wall time, second) when given, else from
    the minute rows (minute)."""
    if updates:
        out = [{"level": c["level"], "dir": c["dir"], "t": _t(u["wall"]), "p": u.get(col)}
               for u in updates for c in u.get("cross", []) if c.get("v") == col]
        return sorted(out, key=lambda x: x["t"]), "second"
    pts = [(_t(r["t"]), r.get(col)) for r in rows if r.get(col) is not None]
    return side_crossings(pts), "minute"


def market_crossings(ws: list[dict], slug: str, yes_is: str = "a") -> list[dict]:
    """The WebSocket recorder's crossings of one market, as crossings of side a."""
    out = []
    for c in ws:
        if c.get("slug") != slug:
            continue
        L, d = c["level"], c["dir"]
        if yes_is != "a":
            L, d = round(1 - L, 2), ("down" if d == "up" else "up")
        out.append({"level": L, "dir": d, "t": c["ts"] / 1000 if c.get("ts") else _t(c["t"]), "p": c.get("price")})
    return sorted(out, key=lambda x: x["t"])


def pair(model: list[dict], market: list[dict], window_s: float = WINDOW_MIN * 60) -> dict:
    used, rows = set(), []
    for m in model:
        best, bd = None, None
        for i, x in enumerate(market):
            if i in used or (x["level"], x["dir"]) != (m["level"], m["dir"]) or abs(x["t"] - m["t"]) > window_s:
                continue
            if bd is None or abs(x["t"] - m["t"]) < bd:
                best, bd = i, abs(x["t"] - m["t"])
        if best is not None:
            used.add(best)
        x = market[best] if best is not None else None
        rows.append({"level": m["level"], "dir": m["dir"], "model_t": _sec(m["t"]),
                     "market_t": _sec(x["t"]) if x else None,
                     "lead_s": int(int(x["t"]) - int(m["t"])) if x else None})
    return {"crossings": rows, "market_only": len(market) - len(used)}


def summary(leads: list[int]) -> dict:
    if not leads:
        return {"n": 0, "median_s": None, "p25_s": None, "p75_s": None, "model_first": None}
    s = sorted(leads)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]   # noqa: E731
    return {"n": len(s), "median_s": statistics.median(s), "p25_s": q(0.25), "p75_s": q(0.75),
            "model_first": sum(x > 0 for x in s)}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:                                  # a line cut by a crash
            pass
    return out


def block(tl: dict[str, list[dict]], cfgs: list[dict], ws_dir: Path, updates_path: Path | None = None,
          cols: dict[str, tuple[str, ...]] | None = None) -> dict:
    """Per race and probability column (P, P_v2, …): the paired crossings and their lead summary.
    cols: race -> probability columns to use (default P and P_v2 when present)."""
    from brunder import us
    ws = load_jsonl(Path(ws_dir) / "ws_crossings.jsonl")
    if not ws:
        return {"status": "none", "reason": f"no WebSocket crossings in {Path(ws_dir) / 'ws_crossings.jsonl'}"}
    ups: dict[str, list[dict]] = {}
    for u in load_jsonl(updates_path) if updates_path else []:
        if u.get("kind") == "race":
            ups.setdefault(u["race"], []).append(u)
    races, pooled = {}, []
    for c in cfgs:
        rid = c["election"]["race_id"]
        pm = us._pm_market(c)
        rows = tl.get(rid) or []
        if not pm or not rows:
            continue
        mk = market_crossings(ws, pm[0], pm[1])
        per = {}
        for col in (cols or {}).get(rid) or [k for k in ("P", "P_v2") if any(r.get(k) is not None for r in rows)]:
            mc, res = model_crossings(rows, col, ups.get(rid))
            p = pair(mc, mk)
            leads = [x["lead_s"] for x in p["crossings"] if x["lead_s"] is not None]
            per[col] = {"resolution": res, "model_crossings": len(mc), "market_crossings": len(mk),
                        "paired": len(leads), "model_only": len(mc) - len(leads), "market_only": p["market_only"],
                        "lead": summary(leads), "crossings": p["crossings"]}
            pooled += leads if col == "P" else []
        races[rid] = per
    return {"status": "ok", "levels": list(LEVELS), "window_min": WINDOW_MIN,
            "lead_definition": "market − model crossing time, seconds, positive = our probability crossed first",
            "pooled_P": summary(pooled), "races": races}


def main(argv: list[str] | None = None) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import us_score
    from brunder import us
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=us.ROOT_DIR / "site" / "data")
    ap.add_argument("--snap", type=Path, default=us.SNAP / us.TAG)
    ap.add_argument("--ws", type=Path)
    ap.add_argument("--updates", type=Path)
    a = ap.parse_args(argv)
    d26 = a.out / us.EID
    cfgs, _, _ = us_score.version_cfgs(us_score.default_configs())
    tl = json.loads((d26 / "timeline.json").read_text())
    upd = a.updates or us.UPDATES
    res = block(tl, cfgs, a.ws or a.snap.parent / (a.snap.name + "_ws"), upd if upd.exists() else None)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
