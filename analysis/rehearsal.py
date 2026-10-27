"""Rehearse an election night through the simulated CDN and compare the live engine with the ideal replay.

    uv run python analysis/rehearsal.py configs/brazil_2026_r1_dev_mun.toml --speed 10
    uv run python analysis/rehearsal.py configs/brazil_2026_r1_dev_mun.toml --speed 1 --limit-rps 15

Writes results/rehearsal/<config>_<speed>x/: ticks.jsonl, latency.jsonl (live engine), rehearsal.json
(run + CDN stats), summary.json and printed metrics:
  - data delay: for each live tick, how long ago the ideal replay (every section visible the moment TSE
    received it) had counted what the live engine had counted;
  - |P_live − P_replay| at the same simulated instant, per market;
  - gated signals and settle leads under the config's trade rules (favoured_only forced on, as frozen
    in the registered runoff configs), live vs replay;
  - request budget: municipality backlog, limiter rate, CDN 429s/lockouts.
Development set only (the first round); this is an engineering check, not an edge estimate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import night, rehearsal

ROOT = Path(__file__).resolve().parents[1]


def outcomes(count: rehearsal.SimCount, race: str) -> dict[str, int]:
    s = count.sections
    g = s.groupby("uf")[["a", "b", "valid"]].sum()
    if race == "margin":
        out = {u: int(r.a - r.b > 0) for u, r in g.iterrows()}
        out["br"] = int(s["a"].sum() > s["b"].sum())
    else:
        out = {u: int(r.a / (r.a + r.b) > 0.5) for u, r in g.iterrows()}
        out["br"] = int(s["a"].sum() / (s["a"].sum() + s["b"].sum()) > 0.5)
    return out


def data_delay(live: pd.DataFrame, rep: pd.DataFrame) -> pd.Series:
    """Seconds between each live tick and the moment the replay had reached the live counted fraction."""
    r = rep.drop_duplicates("t").set_index("t")["counted"].sort_index().cummax()
    rt = r.index.as_unit("ns").astype("int64").to_numpy()
    out = []
    for t, c in zip(live["t"], live["counted"]):
        i = int(np.searchsorted(r.to_numpy(), c, side="left"))
        if i >= len(r) or not np.isfinite(c) or c <= 0:
            continue
        out.append((pd.Timestamp(t).as_unit("ns").value - rt[i]) / 1e9)
    return pd.Series(out, dtype=float)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", type=Path)
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--interval", type=float, default=5.0, help="live tick, wall seconds")
    ap.add_argument("--ttl", type=float, default=60.0, help="simulated CDN cache life, simulated seconds")
    ap.add_argument("--limit-rps", type=float, default=20.0, help="simulated CDN per-client limit")
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args()
    cfg = night.load_config(a.config)
    T = cfg.get("trade", {})
    out = ROOT / "results" / "rehearsal" / f"{cfg['_id']}_{a.speed:g}x"

    meta = asyncio.run(rehearsal.run(a.config, out, speed=a.speed, interval=a.interval, start=a.start, end=a.end,
                                     cdn={"ttl_s": a.ttl, "limit_rps": a.limit_rps}))
    live = pd.DataFrame([json.loads(x) for x in (out / "ticks.jsonl").open()])
    live["t"] = pd.to_datetime(live["t"], utc=True)
    rep = night.replay(a.config, lag="0min", step="1min", save=False)
    rep["t"] = pd.to_datetime(rep["t"], utc=True)
    rep = rep[(rep["t"] >= live["t"].min() - pd.Timedelta("1min")) & (rep["t"] <= live["t"].max())]

    nat_live = live[live["scope"] == "br"].sort_values("t")
    delay = data_delay(nat_live, rep[rep["scope"] == "br"])

    gaps = []
    for scope, g in live.groupby("scope"):
        r = rep[rep["scope"] == scope].set_index("t")["P"].sort_index()
        if r.empty:
            continue
        idx = r.index.searchsorted(g["t"], side="right") - 1
        ok = idx >= 0
        gaps.append(pd.DataFrame({"scope": scope, "gap": np.abs(g["P"].to_numpy()[ok] - r.to_numpy()[idx[ok]])}))
    gaps = pd.concat(gaps)

    oc = outcomes(rehearsal.SimCount(cfg), cfg["election"]["race"])
    rules = (T.get("min_edge", 0.05), T.get("min_counted", 0.01), True)
    # rehearsal books are built from the archived price series, so the market price at a live tick is
    # that series as of the tick (the replay carries it per minute)
    px = rep[["t", "scope", "price"]].sort_values("t")
    live_px = pd.merge_asof(live.sort_values("t"), px, on="t", by="scope", direction="backward")
    ev_live = night.evaluate(live_px.dropna(subset=["price"]), oc, *rules)
    ev_rep = night.evaluate(rep, oc, *rules)
    both = ev_rep.merge(ev_live, on="scope", suffixes=("_replay", "_live"))
    both["entry_delay_min"] = (both["entry_live"] - both["entry_replay"]).dt.total_seconds() / 60

    summary = {
        "run": meta,
        "ticks": int(nat_live.shape[0]),
        "data_delay_s": {q: round(float(delay.quantile(p)), 1) for q, p in
                         (("median", 0.5), ("p90", 0.9), ("max", 1.0))} if len(delay) else None,
        "P_gap": {"median": round(float(gaps["gap"].median()), 4), "p90": round(float(gaps["gap"].quantile(0.9)), 4),
                  "max": round(float(gaps["gap"].max()), 4)},
        "mun_backlog_max": int(live["mun_backlog"].max()),
        "feed_rps": [float(live["feed_rps"].min()), float(live["feed_rps"].max())],
        "signals": {"replay": int(ev_rep["return_per_$"].notna().sum()), "live": int(ev_live["return_per_$"].notna().sum()),
                    "replay_mean_ret": round(float(ev_rep["return_per_$"].mean()), 3) if ev_rep["return_per_$"].notna().any() else None,
                    "live_mean_ret": round(float(ev_live["return_per_$"].mean()), 3) if ev_live["return_per_$"].notna().any() else None},
        "entry_delay_min": both["entry_delay_min"].dropna().round(1).tolist(),
        "lead_min_median": {"replay": float(ev_rep["lead_min"].median()), "live": float(ev_live["lead_min"].median())},
    }
    both.to_csv(out / "live_vs_replay.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
