"""Arbitrage on past election nights, per configs/pairs/<election>.toml.

    uv run python analysis/arb_history.py br2026r2                       # Polymarket-only (default)
    uv run python analysis/arb_history.py --kalshi-permission br2026r1 br2026r2 il2026   # cross-venue

Polymarket-only (`pm_only`): every race of the pairs file with a Polymarket market for each of its two
sides (a, b), e.g. the runoff's "Lula wins X" / "Flávio wins X". YES_a + YES_b (or NO_a + NO_b) pays $1
when exactly one of the two wins, so a cost below $1 after fees is a within-Polymarket arbitrage. Per
minute: cost with asks approximated as price ± h (`arb.pm_complement`); per minute at traded prices:
units both legs actually traded below $1 (`arb.walk_trades`, no approximation). Only meaningful where the
race has exactly two candidates (the runoff); a third candidate or an annulment breaks it.

Cross-venue (`cross`, only with --kalshi-permission): needs stored Kalshi history, which Kalshi's
Developer Agreement §3/§3.1 forbids for anything but one's own trading (docs/kalshi.md). Run it only
with Kalshi's written permission. For every pair whose two markets both traded in the window:
Polymarket 1-min prices and taker fills, Kalshi 1-min candles (period-close bid/ask) and fills; per
minute `arb.minute_arb` (Polymarket asks = price ± h) and `arb.fillable` at traded prices.

Outputs (gitignored; nothing Kalshi-derived may be published):
  results/arb/<eid>/raw/…                     downloaded inputs (cached; reruns are offline)
  results/arb/<eid>/pm_only_{minutes.parquet,fills.parquet,summary.csv,stats.json}
  results/arb/<eid>/{minutes.parquet,fills.parquet,summary.csv,arb.json,stats_arbitrage.json}  (cross)

Evidence label: in-sample descriptive (nothing fitted; h ∈ HALF_SPREADS fixed before looking, all
reported). Fees: each market's CURRENT Polymarket feeSchedule — fee schedules can change after the
night (docs/kalshi.md); Kalshi's taker formula with the series multiplier.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from brunder import _http, arb, kalshi, polymarket

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "arb"
HALF_SPREADS = (0.005, 0.01, 0.02)
MAIN_H = 0.01
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


def cross(eid: str) -> dict:
    _, pairs = arb.load_pairs(arb.PAIRS_DIR / f"{eid}.toml")
    start, end = (pd.Timestamp(x) for x in WINDOWS[eid])
    out = OUT / eid
    raw = out / "raw"
    minutes, fills, summary, contract = [], [], [], []
    for p in pairs:
        kt = _cached(raw / "kalshi_trades" / f"{p.kalshi_ticker}.parquet",
                     lambda: kalshi.trades(p.kalshi_ticker, start, end))
        pt = _cached(raw / "pm_trades" / f"{p.pm_market}.parquet",
                     lambda: pm_trades_window(p.pm_condition_id, start, end))
        if kt.empty or pt.empty:
            continue
        kc = _cached(raw / "kalshi_candles" / f"{p.kalshi_ticker}.parquet",
                     lambda: kalshi.candlesticks(p.kalshi_series, p.kalshi_ticker, start - pd.Timedelta("6h"), end))
        pp = _cached(raw / "pm_prices" / f"{p.pm_market}.parquet",
                     lambda: polymarket.price_history(p.pm_yes_token, start - pd.Timedelta("6h"), end, 1))
        if kc.empty or pp.empty:
            continue
        f = arb.fillable(p, pt, kt, p.pm_yes_token)
        f.insert(0, "pair", p.id)
        fills.append(f)
        for h in HALF_SPREADS:
            m = arb.minute_arb(p, pp, kc, h, start, end - pd.Timedelta("1min"))
            m.insert(0, "h", h)
            m.insert(0, "pair", p.id)
            minutes.append(m)
            s = arb.summarize(m, f)
            summary.append({"pair": p.id, "race_id": p.race_id, "pm_market": p.pm_market, "kalshi_ticker": p.kalshi_ticker,
                            "same_side": p.same_side, "h": h, **s,
                            "pm_taker_usd": float((pt["price"] * pt["size"]).sum()),
                            "k_taker_usd": float((kt["price"].where(kt["taker_side"] == "yes", 1 - kt["price"]) * kt["count"]).sum())})
            if h == MAIN_H:
                rows = m.dropna(subset=["cost_a"])
                contract.append({"pair": p.id, "race_id": p.race_id, "pm_market": p.pm_market,
                                 "kalshi_ticker": p.kalshi_ticker,
                                 "rows": [{"t": r.t.isoformat().replace("+00:00", "Z"),
                                           **{k: (None if pd.isna(getattr(r, k)) else round(float(getattr(r, k)), 4))
                                              for k in ("pm_yes_ask", "pm_no_ask", "k_yes_ask", "k_no_ask", "cost_a", "net_edge")}}
                                          for r in rows.itertuples()]})
        print(f"{eid} {p.id}: {len(pt)} PM / {len(kt)} Kalshi fills; fillable ${f['cost_usd'].sum():,.0f}", flush=True)
    if not summary:
        print(f"{eid}: no pair traded on both venues in {start} → {end}")
        return {}
    out.mkdir(parents=True, exist_ok=True)
    M = pd.concat(minutes, ignore_index=True)
    F = pd.concat(fills, ignore_index=True)
    S = pd.DataFrame(summary)
    M.to_parquet(out / "minutes.parquet")
    F.to_parquet(out / "fills.parquet")
    S.to_csv(out / "summary.csv", index=False)
    (out / "arb.json").write_text(json.dumps(contract))
    main = S[S["h"] == MAIN_H]
    stats = {"pairs": int(len(main)), "minutes_with_net_arb": int(main["minutes_with_net_arb"].sum()),
             "max_net_edge": float(main["max_net_edge"].max()), "fillable_usd": round(float(F["cost_usd"].sum()), 2),
             "fillable_net_usd": round(float(F["net_usd"].sum()), 2),
             "window_utc": [start.isoformat(), end.isoformat()], "pm_half_spread": MAIN_H,
             "evidence": "in-sample (descriptive; nothing fitted)"}
    (out / "stats_arbitrage.json").write_text(json.dumps(stats, indent=1))
    by_h = S.groupby("h")[["minutes_with_net_arb"]].sum().join(S.groupby("h")["max_net_edge"].max())
    print(f"\n{eid}: {stats}\n{by_h.to_string()}\n"
          f"top fillable pairs:\n{F.groupby('pair')[['units', 'cost_usd', 'net_usd']].sum().sort_values('cost_usd', ascending=False).head(10).round(2).to_string()}",
          flush=True)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("elections", nargs="*", default=list(WINDOWS))
    ap.add_argument("--kalshi-permission", action="store_true",
                    help="also run the cross-venue analysis (needs Kalshi's written permission; docs/kalshi.md)")
    a = ap.parse_args()
    for e in a.elections:
        pm_only(e)
        if a.kalshi_permission:
            cross(e)
