"""Within-Polymarket arbitrage on past election nights, per configs/pairs/<election>.toml.

    uv run python analysis/arb_history.py br2026r2 br2026r1

Every race of the pairs file with a Polymarket market for each of its two sides (a, b), e.g. the runoff's
"Lula wins X" / "Flávio wins X". YES_a + YES_b (or NO_a + NO_b) pays $1 when exactly one of the two
wins, so a cost below $1 after fees is a within-Polymarket arbitrage. Per minute: cost with asks
approximated as price ± h (`arb.pm_complement`); per minute at traded prices: units both legs actually
traded below $1 (`arb.walk_trades`, no approximation). Only meaningful where the race has exactly two
candidates (the runoff); a third candidate or an annulment breaks it. Cross-venue (Polymarket ↔ Kalshi)
arbitrage is analysis/kalshi_export.py.

Outputs (gitignored): results/arb/<eid>/raw/… (cached inputs) and
results/arb/<eid>/pm_only_{minutes.parquet,fills.parquet,summary.csv,stats.json}.

Evidence label: in-sample descriptive (nothing fitted; h ∈ HALF_SPREADS fixed before looking, all
reported). Fees: each market's CURRENT Polymarket feeSchedule — fee schedules can change after the
night (docs/kalshi.md).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from brunder import _http, arb, polymarket

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "arb"
HALF_SPREADS = (0.005, 0.01, 0.02)
FILL_WINDOWS = ("1min", "10s", "1s")   # how close in time two legs' fills must be to count as one trade
MAIN_FILL_WINDOW = "1s"
TWO_WAY = {"br2026r2"}                 # elections whose paired races have exactly two candidates
WINDOWS = {   # UTC; polls close BR 20:00 UTC (17:00 Brasília), IL 20:00 UTC (22:00 IST)
    "br2026r1": ("2026-10-04T12:00Z", "2026-10-05T12:00Z"),
    "br2026r2": ("2026-10-25T12:00Z", "2026-10-26T12:00Z"),
    "il2026": ("2026-10-27T12:00Z", "2026-10-28T12:00Z"),
}


def pm_trades_window(condition_id: str, start: pd.Timestamp, end: pd.Timestamp, page: int = 500) -> pd.DataFrame:
    """Polymarket taker fills inside [start, end), paging backwards from `end` (data-api)."""
    rows, seen, cursor, offset = [], set(), int(end.timestamp()), 0
    with _http.client() as c:
        while True:
            batch = _http.get_json(c, f"{polymarket.DATA_API}/trades", {
                "market": condition_id, "limit": page, "offset": offset, "takerOnly": "true", "end": cursor})
            new = 0
            for t in batch:
                k = (t["transactionHash"], t["asset"], t["side"], t["size"], t["price"], t["timestamp"])
                if k not in seen:
                    seen.add(k)
                    rows.append(t)
                    new += 1
            if len(batch) < page:
                break
            lo = min(t["timestamp"] for t in batch)
            if lo < start.timestamp():
                break
            if lo == cursor or new == 0:
                offset += page
            else:
                cursor, offset = lo, 0
            time.sleep(0.15)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["time", "asset", "side", "price", "size"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df[(df["time"] >= start) & (df["time"] < end)]
    return df[["time", "asset", "side", "price", "size"]].astype({"price": float, "size": float}).sort_values("time")


def _cached(path: Path, fn):
    if path.exists():
        return pd.read_parquet(path)
    df = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def pm_only(eid: str) -> dict:
    """Within-Polymarket complement on every two-sided race of the pairs file (see module doc).
    NO_a + NO_b pays ≥ $1 in any race; YES_a + YES_b pays $1 only where exactly two can win (TWO_WAY)."""
    head, pairs = arb.load_pairs(arb.PAIRS_DIR / f"{eid}.toml")
    start, end = (pd.Timestamp(x) for x in WINDOWS[eid])
    out, raw = OUT / eid, OUT / eid / "raw"
    sides = ("yes", "no") if eid in TWO_WAY else ("no",)
    legs: dict[str, dict[str, arb.Pair]] = {}
    for p in pairs:
        if p.pm_yes_is in ("a", "b"):
            legs.setdefault(p.race_id, {}).setdefault(p.pm_yes_is, p)    # one PM market per side
    minutes, fills, summary = [], [], []
    for race, side in legs.items():
        if set(side) != {"a", "b"}:
            continue
        a, b = side["a"], side["b"]
        px = {s: _cached(raw / "pm_prices" / f"{q.pm_market}.parquet",
                         lambda q=q: polymarket.price_history(q.pm_yes_token, start - pd.Timedelta("6h"), end, 1))
              for s, q in side.items()}
        tr = {s: _cached(raw / "pm_trades" / f"{q.pm_market}.parquet",
                         lambda q=q: pm_trades_window(q.pm_condition_id, start, end))
              for s, q in side.items()}
        if px["a"].empty or px["b"].empty:
            continue
        fa, fb = a.pm_fee_model(), b.pm_fee_model()
        f = pd.concat([arb.walk_trades(arb.trade_levels_pm(tr["a"], s, a.pm_yes_token),
                                       arb.trade_levels_pm(tr["b"], s, b.pm_yes_token), fa, fb, w)
                       .assign(combo=f"{s.upper()} a + {s.upper()} b", window=w)
                       for s in sides for w in FILL_WINDOWS], ignore_index=True).assign(race_id=race)
        fills.append(f)
        main = f[f["window"] == MAIN_FILL_WINDOW]
        for h in HALF_SPREADS:
            m = arb.pm_complement(px["a"], px["b"], fa, fb, h, start, end - pd.Timedelta("1min"), two_way=len(sides) == 2)
            minutes.append(m.assign(race_id=race, h=h))
            summary.append({"race_id": race, "pm_a": a.pm_market, "pm_b": b.pm_market, "h": h, **arb.summarize(m, main),
                            "pm_taker_usd": float(sum((t["price"] * t["size"]).sum() for t in tr.values()))})
        print(f"{eid} {race}: fills a/b {len(tr['a'])}/{len(tr['b'])}; within {MAIN_FILL_WINDOW} at traded prices "
              f"{main['units'].sum():,.0f} units, ${main['cost_usd'].sum():,.2f} cost, ${main['net_usd'].sum():,.2f} net",
              flush=True)
    if not summary:
        print(f"{eid}: no race with Polymarket markets on both sides")
        return {}
    out.mkdir(parents=True, exist_ok=True)
    M, F, S = pd.concat(minutes, ignore_index=True), pd.concat(fills, ignore_index=True), pd.DataFrame(summary)
    M.to_parquet(out / "pm_only_minutes.parquet")
    F.to_parquet(out / "pm_only_fills.parquet")
    S.to_csv(out / "pm_only_summary.csv", index=False)
    stats = {"races": int(S["race_id"].nunique()), "combinations": [f"{s.upper()} a + {s.upper()} b" for s in sides],
             "window_utc": [start.isoformat(), end.isoformat()],
             "by_half_spread": {str(h): {"minutes_with_net_arb": int(g["minutes_with_net_arb"].sum()),
                                         "max_net_edge": round(float(g["max_net_edge"].max()), 4)}
                                for h, g in S.groupby("h")},
             "at_traded_prices": {w: {"units": round(float(g["units"].sum()), 2), "cost_usd": round(float(g["cost_usd"].sum()), 2),
                                      "net_usd": round(float(g["net_usd"].sum()), 2), "buckets": int(g["t"].nunique())}
                                  for w, g in F.groupby("window", sort=False)},
             "evidence": "in-sample (descriptive; nothing fitted)"}
    (out / "pm_only_stats.json").write_text(json.dumps(stats, indent=1))
    by_race = F.pivot_table(index="race_id", columns="window", values="net_usd", aggfunc="sum").round(2)
    print(f"\n{eid} Polymarket-only: {json.dumps(stats)}\nnet USD at traded prices by race and window:\n{by_race.to_string()}",
          flush=True)
    return stats



if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("elections", nargs="*", default=list(WINDOWS))
    for e in ap.parse_args().elections:
        pm_only(e)
