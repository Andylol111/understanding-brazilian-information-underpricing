"""Capacity of the night engine's gated entries: how many dollars traded at the signal's price or better.

Replays the config's count (1-minute ticks), takes night.evaluate's first gated entry per market, and
sums taker notional on the entry's side at a price at least as good as the market price at entry,
within 5 / 30 / 120 minutes after entry (no look-ahead: only [entry, entry + horizon)).

Trade semantics (Polymarket data API, taker rows): `asset` is the token the taker traded, `side` is the
taker's BUY/SELL of that token, `price` is that token's price, `size` is shares. Buying YES at ≤ L is the
same economic fill as selling NO at ≥ 1 − L, so a fill counts for a YES entry if the taker was long YES
(BUY YES or SELL NO) at YES-equivalent price ≤ L; for a NO entry, long NO at NO price ≤ 1 − L. Notional is
shares × the entry side's price (what a buyer of our side would have paid for those shares).

Trades come from the event archive (data/raw/polymarket/<event>/trades/<slug>.parquet, `is_taker` rows)
when present, else from the data API for the window only, cached under results/night/<config>/capacity_trades/.

Outputs (results/night/<config>/): capacity.csv, plus a markdown summary on stdout.

Run: uv run python analysis/night_capacity.py configs/brazil_2026_r1_dev_mun.toml [--favoured-only]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import _http, bweb, night
from brunder.paths import POLYMARKET, ROOT
from brunder.polymarket import DATA_API

HORIZONS = (5, 30, 120)                 # minutes after entry
STAKES = (25, 100, 500, 2000)           # dollars per entry
REPORT_HORIZON = 30
# The minute price history can lag trades in a fast market (2026 runoff: signal price 0.382, takers already
# paying 0.405–0.42 in the same minute), so capacity is also measured `slip` worse than the signal price,
# with the return recomputed at that price.
SLIPS = (0.02, 0.05)


def outcomes(cfg: dict) -> dict[str, int]:
    """Final result per scope (1 = YES won), as in analysis/night_replay.py."""
    E, R = cfg["election"], cfg["replay"]
    sec = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    g = sec.groupby("uf")[["a", "b", "valid"]].sum()
    if E["race"] == "margin":
        final = {u: (r.a - r.b) / r.valid for u, r in g.iterrows()}
        final["br"] = (sec["a"].sum() - sec["b"].sum()) / sec["valid"].sum()
        return {u: int(v > 0) for u, v in final.items()}
    final = {u: r.a / (r.a + r.b) for u, r in g.iterrows()}
    final["br"] = sec["a"].sum() / (sec["a"].sum() + sec["b"].sum())
    return {u: int(v > 0.5) for u, v in final.items()}


def _fetch_takers(cid: str, lo: int, hi: int, page: int = 500) -> pd.DataFrame:
    """Taker fills with lo ≤ timestamp < hi from the data API, paging back from hi by `end`."""
    seen, rows = set(), []
    end, offset = hi - 1, 0
    with _http.client() as c:
        while True:
            batch = _http.get_json(c, f"{DATA_API}/trades", {"market": cid, "takerOnly": "true", "limit": page,
                                                              "offset": offset, "end": end})
            new = 0
            for x in batch:
                k = (x["transactionHash"], x["asset"], x["proxyWallet"], x["side"], x["size"], x["price"], x["timestamp"])
                if k not in seen:
                    seen.add(k)
                    new += 1
                    if lo <= x["timestamp"] < hi:
                        rows.append(x)
            if len(batch) < page or not batch:
                break
            min_ts = min(x["timestamp"] for x in batch)
            if min_ts < lo:
                break
            if min_ts == end or new == 0:      # a full page inside one second: step offset instead
                offset += page
            else:
                end, offset = min_ts, 0
    cols = ["time", "proxyWallet", "side", "asset", "price", "size", "transactionHash"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df[cols].sort_values("time").reset_index(drop=True)


def taker_trades(event: str, slug: str, cid: str, lo: pd.Timestamp, hi: pd.Timestamp, cache: Path) -> tuple[pd.DataFrame, str]:
    archived = POLYMARKET / event / "trades" / f"{slug}.parquet"
    if archived.exists():
        t = pd.read_parquet(archived)
        if "is_taker" in t:
            t = t[t["is_taker"] & (t["time"] >= lo) & (t["time"] < hi)]
            return t.reset_index(drop=True), "archive"
    f = cache / f"{slug}_{int(lo.timestamp())}_{int(hi.timestamp())}.parquet"
    if f.exists():
        return pd.read_parquet(f), "api (cached)"
    t = _fetch_takers(cid, int(lo.timestamp()), int(hi.timestamp()))
    cache.mkdir(parents=True, exist_ok=True)
    t.to_parquet(f)
    return t, "api"


def same_side_fills(t: pd.DataFrame, yes_token: str, side_yes: bool, limit_yes: float) -> pd.DataFrame:
    """Taker fills long our side at a price at least as good as ours, with notional at our side's price."""
    is_yes = t["asset"] == yes_token
    p_yes = np.where(is_yes, t["price"].astype(float), 1 - t["price"].astype(float))
    long_yes = (t["side"] == "BUY") == is_yes
    ok = (long_yes & (p_yes <= limit_yes + 1e-9)) if side_yes else (~long_yes & (p_yes >= limit_yes - 1e-9))
    p_side = p_yes if side_yes else 1 - p_yes
    return t.assign(notional=t["size"].astype(float) * p_side)[ok]


def md(df: pd.DataFrame) -> str:
    cells = [[f"{v:,.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v) for v in r] for r in df.itertuples(index=False)]
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns),
                      *("| " + " | ".join(c) + " |" for c in cells)])


def main(cfg_path: str, favoured_only: bool | None) -> None:
    pd.set_option("display.width", 220)
    cfg = night.load_config(Path(cfg_path))
    T = cfg.get("trade", {})
    fav = T.get("favoured_only", False) if favoured_only is None else favoured_only
    out = ROOT / "results" / "night" / cfg["_id"]
    out.mkdir(parents=True, exist_ok=True)

    ticks = night.replay(Path(cfg_path), lag="0min", step="1min", save=False)
    ev = night.evaluate(ticks, outcomes(cfg), T.get("min_edge", 0.05), T.get("min_counted", 0.01), fav)
    ent = (ev.dropna(subset=["entry"]).rename(columns={"return_per_$": "ret"})
           .merge(ticks, left_on=["scope", "entry"], right_on=["scope", "t"], how="left"))
    mk = {m["scope"]: m for m in night._markets(cfg)}

    rows, sources = [], {}
    for r in ent.itertuples():
        m = mk[r.scope]
        info = pd.read_parquet(POLYMARKET / m["event"] / "markets.parquet").set_index("slug").loc[m["market"]]
        side_yes = r.side == "YES"
        lo, hi = r.entry, r.entry + pd.Timedelta(minutes=max(HORIZONS))
        t, sources[r.scope] = taker_trades(m["event"], m["market"], info["condition_id"], lo, hi, out / "capacity_trades")
        f = same_side_fills(t, info["yes_token"], side_yes, r.price)
        row = {"scope": r.scope, "market": m["market"], "side": r.side, "entry_utc": r.entry, "price": r.price,
               "cost": r.yes_cost if side_yes else r.no_cost, "return_per_$": r.ret}
        for h in HORIZONS:
            row[f"fill_{h}m"] = float(f.loc[f["time"] < r.entry + pd.Timedelta(minutes=h), "notional"].sum())
        won = r.ret > -1
        for s in SLIPS:
            fs = same_side_fills(t, info["yes_token"], side_yes, r.price + s if side_yes else r.price - s)
            row[f"fill_{REPORT_HORIZON}m_slip{int(s * 100)}c"] = float(
                fs.loc[fs["time"] < r.entry + pd.Timedelta(minutes=REPORT_HORIZON), "notional"].sum())
            row[f"return_slip{int(s * 100)}c"] = (1 / (row["cost"] + s) if won else 0.0) - 1
        row["n_trades_30m"] = int((t["time"] < r.entry + pd.Timedelta(minutes=30)).sum())
        rows.append(row)
    cap = pd.DataFrame(rows, columns=["scope", "market", "side", "entry_utc", "price", "cost", "return_per_$",
                                      *(f"fill_{h}m" for h in HORIZONS),
                                      *(c for s in SLIPS for c in (f"fill_{REPORT_HORIZON}m_slip{int(s * 100)}c",
                                                                   f"return_slip{int(s * 100)}c")),
                                      "n_trades_30m"])
    name = "capacity.csv" if fav == T.get("favoured_only", False) else f"capacity_favoured_only_{str(fav).lower()}.csv"
    cap.to_csv(out / name, index=False)

    print(f"{cfg['_id']}: {len(cap)} gated entries (min_edge {T.get('min_edge', 0.05)}, "
          f"min_counted {T.get('min_counted', 0.01)}, favoured_only {fav}); trades from {set(sources.values())}\n")
    show = cap.drop(columns="market").assign(entry_utc=cap["entry_utc"].dt.strftime("%Y-%m-%d %H:%M"))
    print(md(show))
    fill = cap[f"fill_{REPORT_HORIZON}m"]
    print(f"\ntotal fillable within {REPORT_HORIZON} min: ${fill.sum():,.0f}"
          f" | entries with no taker trades in {REPORT_HORIZON} min: {list(cap.loc[cap['n_trades_30m'] == 0, 'scope'])}")
    pnl = pd.DataFrame([{"stake_$": s, "filled_$": np.minimum(s, fill).sum(),
                         "pnl_$": (cap["return_per_$"] * np.minimum(s, fill)).sum(),
                         "pnl_unlimited_$": (cap["return_per_$"] * s).sum()} for s in STAKES])
    print(f"\nP&L at stake per entry, capped by {REPORT_HORIZON}-min fill (return_per_$ × min(stake, fill)):")
    print(md(pnl))
    for s in SLIPS:
        k = int(s * 100)
        fs, rs = cap[f"fill_{REPORT_HORIZON}m_slip{k}c"], cap[f"return_slip{k}c"]
        print(f"\naccepting {k}¢ worse than the signal price: fillable within {REPORT_HORIZON} min ${fs.sum():,.0f}")
        print(md(pd.DataFrame([{"stake_$": st, "filled_$": np.minimum(st, fs).sum(),
                                "pnl_$": (rs * np.minimum(st, fs)).sum()} for st in STAKES])))
    print(out / name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--favoured-only", action=argparse.BooleanOptionalAction, default=None,
                    help="override the config's [trade] favoured_only")
    a = ap.parse_args()
    main(a.config, a.favoured_only)
