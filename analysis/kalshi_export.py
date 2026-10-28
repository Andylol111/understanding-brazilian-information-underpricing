"""Kalshi on the dashboard: download Kalshi market data for each election with paired markets and add
the Kalshi parts of the dashboard contract to the exported site data.

    uv run python analysis/kalshi_export.py download [eid ...]   # Kalshi + paired Polymarket data → $BRUNDER_DATA
    uv run python analysis/kalshi_export.py export [eid ...]     # site/data/<eid>/: kalshi keys + arb.json
    uv run python analysis/kalshi_export.py us2026               # pre-election history only (no site data)
    uv run python analysis/kalshi_export.py [eid ...]            # download, then export

RUN ORDER: after the election's own exporter (export_brazil.py, il_export.py, hu_export.py, ca_export.py)
and after edge_audit.py. This script only adds or replaces Kalshi keys, so re-running an exporter wipes
them and this script must run again:
  timeline.json  "kalshi" in every row of a race with a Kalshi market
  stats.json     settle.<race>.kalshi, settle_gated.<race>.kalshi, catchup.kalshi, capacity.kalshi,
                 arbitrage, reasons.* for Kalshi keys, evidence.arbitrage
  meta.json      Kalshi entries in races[].markets (ticker + our own wording), Kalshi source and notes
  arb.json       written whole (one entry per matched pair)

Inputs:
  - pairs: configs/pairs/<eid>.toml (analysis/arb_pairs.py), linked to the dashboard by Polymarket slug;
  - the election's exported meta/timeline (for race rows, window, model P and counted).

Raw data (never committed) is written under $BRUNDER_DATA/raw/kalshi/<eid>/ (candles/, trades/, markets/)
and $BRUNDER_DATA/raw/polymarket_window/<eid>/ (prices/, trades/). The window is the race rows' span,
widened 24 h before (so the first row has a quote) and 2 h after.

Definitions:
  - Kalshi P(YES) at a minute: the latest 1-min candle that closed at or before it; the bid/ask midpoint
    when both sides are quoted and the spread is at most 10¢, else the last trade price. P(A) = P(YES) of
    a market whose YES is side a, else 1 − P(YES) of one whose YES is side b.
  - settle.kalshi: first row time after which Kalshi's P(eventual winner) stays >= 0.9 to the race's last
    row (as for Polymarket); the winner is Kalshi's own settlement of that market, or, while Kalshi has not
    settled it, the side the Polymarket price settled on.
  - settle_gated.kalshi: the same time (the 1% gate applies to the model only).
  - catchup.kalshi: slope of Kalshi's 30-minute move on the gap (model P − Kalshi P(A)), rows with >= 1%
    counted and both P and price strictly inside (0.02, 0.98); cluster bootstrap over (race, 30-min block)
    (underpricing_figures.slope_ci). Only where the election has model P.
  - capacity.kalshi: the frozen favoured-side rule (min_edge, min_counted from the night config) applied to
    Kalshi's own quotes: first row per race with >= min_counted counted where the side the model favours
    costs (Kalshi ask + Kalshi taker fee) at least min_edge less than the model's probability; capacity =
    dollars of Kalshi taker fills on that side at or below that ask (+2c, +5c) within 30 min. Brazil only
    (the only elections with a frozen rule).
  - arb.json: per pair and minute of the race window: Polymarket asks approximated as the 1-min price ± 0.01
    (no Polymarket quote history), Kalshi asks from the candle; cost_a = the cheaper complementary
    combination incl. both fees, net_edge = 1 − cost_a; fill_usd = what both venues' real taker fills in that
    minute allowed at a combined cost below $1 (arb.fillable). Pair totals also at 10 s and 1 s buckets.
"""

from __future__ import annotations

import json
import os
import math
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import arb, kalshi, polymarket
from brunder.paths import RAW, ROOT

sys.path.insert(0, str(ROOT / "analysis"))
from arb_history import pm_trades_window  # noqa: E402

SITE = ROOT / "site" / "data"
RES = ROOT / "results" / "kalshi"
ELECTIONS = ("br2026r1", "br2026r2", "br2026r2gov", "il2026", "hu2026", "ca2025", "qc2026")
# exported elections where Kalshi listed nothing: only their Kalshi reasons are set
NO_KALSHI = {"br2022r1": "Kalshi listed no market on this election", "br2022r2": "Kalshi listed no market on this election",
             "mx2024": "Kalshi listed no market on this election"}
RULE_CFG = {"br2026r1": "configs/brazil_2026_r1_dev_mun.toml", "br2026r2": "configs/night_r2_v1.toml",
            "br2026r2gov": "configs/governor_r2/night_gov_r2_df_v1.toml"}
PM_HALF_SPREAD = 0.01
# Polymarket order books recorded on the night (brunder snap, 30 s): real asks instead of price ± 0.01
SNAP_TAG = {"il2026": "israel_2026"}
SNAP_ROOTS = (RAW / "snap", Path(os.environ.get("BRUNDER_IL_DATA", Path.home() / "brunder-data-local")) / "raw" / "snap")
FILL_WINDOWS = ("1min", "10s", "1s")
SLIPS = (0.0, 0.02, 0.05)
SRC_KALSHI = {"name": "Kalshi public market data (Trade API v2): 1-minute candlesticks, trades, settlements",
              "url": "https://kalshi.com/",
              "licence": "© Kalshi. Derived charts and aggregates shown with credit to Kalshi; raw data not redistributed. "
                         "Written exception requested from Kalshi on 2026-10-28 (pending)."}
LICENCE_REASON = "Kalshi data licence"


def _r(x, n=4):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) or pd.isna(x) else round(float(x), n)


def _iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------- links

def load(eid: str) -> tuple[dict, dict, dict]:
    d = SITE / eid
    return tuple(json.loads((d / f"{n}.json").read_text()) for n in ("meta", "timeline", "stats"))


def links(eid: str, meta: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """(matched pairs on dashboard races, Kalshi markets per race). A pair belongs to a race when its
    Polymarket market is listed in that race; its Kalshi side relative to the race follows from same_side.
    Kalshi markets per race also include Kalshi entries the exporter already listed (Brazil)."""
    _, pairs = arb.load_pairs(arb.PAIRS_DIR / f"{eid}.toml")
    by_ticker = {p.kalshi_ticker: p for p in pairs}
    matched, kmk = [], {}
    for r in meta["races"]:
        rid = r["race_id"]
        for m in r.get("markets") or []:
            if m["platform"] == "polymarket":
                for p in pairs:
                    if p.pm_market == m["id"] and m.get("yes_is") in ("a", "b"):
                        ks = m["yes_is"] if p.same_side else {"a": "b", "b": "a"}[m["yes_is"]]
                        matched.append({"race_id": rid, "pair": p, "pm_side": m["yes_is"], "k_side": ks})
                        kmk.setdefault(rid, {})[p.kalshi_ticker] = ks
            elif m["platform"] == "kalshi" and m["id"] in by_ticker and m.get("yes_is") in ("a", "b"):
                kmk.setdefault(rid, {})[m["id"]] = m["yes_is"]
    return matched, {rid: [{"ticker": t, "yes_is": s, "pair": by_ticker[t]} for t, s in v.items()] for rid, v in kmk.items()}


def window(timeline: dict, races) -> tuple[pd.Timestamp, pd.Timestamp]:
    ts = pd.to_datetime([row["t"] for rid in races for row in timeline.get(rid, [])], utc=True)
    return ts.min() - pd.Timedelta("24h"), ts.max() + pd.Timedelta("2h")


# ---------------------------------------------------------------------------------------------- download

def _cached(path: Path, fn) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    df = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def kalshi_data(eid: str, p: arb.Pair, lo, hi) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    d = RAW / "kalshi" / eid
    c = _cached(d / "candles" / f"{p.kalshi_ticker}.parquet",
                lambda: kalshi.candlesticks(p.kalshi_series, p.kalshi_ticker, lo, hi))
    t = _cached(d / "trades" / f"{p.kalshi_ticker}.parquet", lambda: kalshi.trades(p.kalshi_ticker, lo, hi))
    f = d / "markets" / f"{p.kalshi_ticker}.json"
    if not f.exists() or json.loads(f.read_text()).get("result") not in ("yes", "no"):   # refresh until settled
        f.parent.mkdir(parents=True, exist_ok=True)
        m = kalshi.market(p.kalshi_ticker)
        f.write_text(json.dumps({k: m.get(k) for k in ("ticker", "event_ticker", "status", "result", "open_time",
                                                       "close_time", "settlement_ts", "expiration_value")}))
    return c, t, json.loads(f.read_text())


def pm_data(eid: str, p: arb.Pair, lo, hi) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = RAW / "polymarket_window" / eid
    px = _cached(d / "prices" / f"{p.pm_market}.parquet", lambda: polymarket.price_history(p.pm_yes_token, lo, hi, 1))
    tr = _cached(d / "trades" / f"{p.pm_market}.parquet", lambda: pm_trades_window(p.pm_condition_id, lo, hi))
    return px, tr


def download(eid: str) -> None:
    meta, timeline, _ = load(eid)
    matched, kmk = links(eid, meta)
    lo, hi = window(timeline, kmk)
    for rid, ks in kmk.items():
        for k in ks:
            c, t, m = kalshi_data(eid, k["pair"], lo, hi)
            print(f"{eid} {rid} {k['ticker']}: {len(c)} candles, {len(t)} trades, result {m.get('result')!r}", flush=True)
    for x in matched:
        px, tr = pm_data(eid, x["pair"], lo, hi)
        print(f"{eid} {x['race_id']} {x['pair'].pm_market[:60]}: {len(px)} PM prices, {len(tr)} PM taker fills", flush=True)


# ---------------------------------------------------------------------------------------------- Kalshi P

def race_kalshi(eid: str, ks: list[dict], at: pd.DatetimeIndex, lo, hi) -> tuple[pd.Series, dict]:
    """Kalshi P(A) on the race's rows from its preferred market (YES = a first) and that market's record."""
    k = sorted(ks, key=lambda x: x["yes_is"] != "a")[0]
    c, _, m = kalshi_data(eid, k["pair"], lo, hi)
    p = kalshi.prob_from_candles(c, at) if len(c) else pd.Series(np.nan, index=at)
    return (p if k["yes_is"] == "a" else 1 - p), {**m, "yes_is": k["yes_is"]}


def settle_time(t: pd.Series, p_win: pd.Series):
    """First time after which p_win stays >= 0.9 to the end (export_brazil.settle)."""
    ok = p_win >= 0.9
    known = p_win.notna()
    t, ok = t[known], ok[known]
    if t.empty:
        return None
    bad = t[~ok.to_numpy()]
    if bad.empty:
        return t.iloc[0]
    after = t[t > bad.iloc[-1]]
    return after.iloc[0] if len(after) else None


# ---------------------------------------------------------------------------------------------- stats

def catchup(frames: list[pd.DataFrame]) -> dict | None:
    import underpricing_figures as U
    parts = []
    for f in frames:
        g = f.dropna(subset=["P", "k"]).set_index("t").sort_index()
        g = g[~g.index.duplicated()]
        if g.empty:
            continue
        fut = g["k"].reindex(g.index + pd.Timedelta("30min"), method="ffill", tolerance=pd.Timedelta("5min")).to_numpy()
        d = pd.DataFrame({"scope": f["race_id"].iloc[0], "t": g.index, "gap": g["P"] - g["k"], "move": fut - g["k"].to_numpy(),
                          "p": g["k"], "P": g["P"], "c": g["counted"]})
        parts.append(d)
    if not parts:
        return None
    d = pd.concat(parts)
    d = d[(d["c"] >= 0.01) & d["p"].between(0.02, 0.98, inclusive="neither") & d["P"].between(0.02, 0.98, inclusive="neither")]
    d = d.dropna(subset=["move"])
    if len(d) < 4:
        return None
    d["cluster"] = d["scope"] + "_" + d["t"].dt.floor("30min").astype(str)
    s, lo, hi, _, k = U.slope_ci(d, b=300)
    return {"slope30": _r(s, 3), "lo": _r(lo, 3), "hi": _r(hi, 3), "markets": int(d["scope"].nunique()), "n": int(len(d)),
            "clusters": int(k)}


def capacity(eid: str, frames: list[pd.DataFrame], ks_by_race: dict, lo, hi) -> dict | None:
    """Favoured-side rule on Kalshi quotes (module doc); None where no frozen rule exists."""
    if eid not in RULE_CFG:
        return None
    T = tomllib.loads((ROOT / RULE_CFG[eid]).read_text())["trade"]
    tot = dict.fromkeys(SLIPS, 0.0)
    for f in frames:
        rid = f["race_id"].iloc[0]
        k = sorted(ks_by_race[rid], key=lambda x: x["yes_is"] != "a")[0]
        c, tr, _ = kalshi_data(eid, k["pair"], lo, hi)
        if c.empty or tr.empty:
            continue
        cc = c.sort_values("time").set_index("time")
        at = pd.DatetimeIndex(f["t"])
        yes_ask = cc["yes_ask"].where(cc["yes_ask"] < 1).reindex(at.union(cc.index)).ffill().reindex(at)
        no_ask = (1 - cc["yes_bid"].where(cc["yes_bid"] > 0)).reindex(at.union(cc.index)).ffill().reindex(at)
        mult = k["pair"].kalshi_fee_multiplier
        P_yes = f["P"].to_numpy() if k["yes_is"] == "a" else 1 - f["P"].to_numpy()
        ya, na = yes_ask.to_numpy(), no_ask.to_numpy()
        e_yes = P_yes - (ya + arb.kalshi_unit_fee(ya, mult))
        e_no = (1 - P_yes) - (na + arb.kalshi_unit_fee(na, mult))
        gate = f["counted"].to_numpy() >= T["min_counted"]
        yes_ok = gate & (P_yes >= 0.5) & (e_yes >= T["min_edge"])
        no_ok = gate & (P_yes < 0.5) & (e_no >= T["min_edge"])
        hit = np.flatnonzero(yes_ok | no_ok)
        if not len(hit):
            continue
        i = hit[0]
        side, limit = ("yes", ya[i]) if yes_ok[i] else ("no", na[i])
        t0 = at[i]
        w = tr[(tr["time"] >= t0) & (tr["time"] < t0 + pd.Timedelta("30min")) & (tr["taker_side"] == side)]
        px = w["price"] if side == "yes" else 1 - w["price"]
        for s in SLIPS:
            ok = px <= limit + s + 1e-9
            tot[s] += float((w.loc[ok, "count"] * px[ok]).sum())
    return {"signal": _r(tot[0.0], 2), "slip2c": _r(tot[0.02], 2), "slip5c": _r(tot[0.05], 2)}


def pm_book_tops(eid: str) -> pd.DataFrame | None:
    """Best YES ask and NO ask (1 − best YES bid) per recorded snapshot and market slug, or None."""
    tag = SNAP_TAG.get(eid)
    d = next((r / tag / "polymarket_books" for r in SNAP_ROOTS if tag and any((r / tag / "polymarket_books").glob("*.json"))), None)
    if d is None:
        return None
    out = RAW / "polymarket_window" / eid / "book_tops.parquet"
    if out.exists():
        return pd.read_parquet(out)
    rows = []
    for f in sorted(d.glob("*.json")):
        t = pd.Timestamp(f.name.split(".json")[0].replace("Z", ""), tz="UTC")
        for key, b in json.loads(f.read_text()).items():
            asks = [float(x["price"]) for x in b.get("asks") or []]
            bids = [float(x["price"]) for x in b.get("bids") or []]
            rows.append({"time": t, "slug": key.split("/")[-1], "yes_ask": min(asks) if asks else np.nan,
                         "no_ask": 1 - max(bids) if bids else np.nan})
    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return df


def pair_arb(eid: str, x: dict, rows_t: pd.DatetimeIndex, lo, hi) -> tuple[dict, dict]:
    p = x["pair"]
    c, kt, _ = kalshi_data(eid, p, lo, hi)
    px, pt = pm_data(eid, p, lo, hi)
    start, end = rows_t.min().floor("min"), rows_t.max().floor("min")
    if c.empty or px.empty:
        return None, None
    tops = pm_book_tops(eid)
    tops = None if tops is None else tops[tops["slug"] == p.pm_market]
    tops = tops if tops is not None and len(tops) else None
    m = arb.minute_arb(p, px, c, PM_HALF_SPREAD, start, end, pm_asks=tops)
    fills = {w: arb.fillable(p, pt, kt, p.pm_yes_token, w) if len(pt) and len(kt) else pd.DataFrame(columns=["t", "cost_usd", "net_usd"])
             for w in FILL_WINDOWS}
    f1 = fills["1min"].groupby("t")[["cost_usd", "net_usd"]].sum() if len(fills["1min"]) else pd.DataFrame(columns=["cost_usd", "net_usd"])
    m = m.join(f1, on="t")
    rows = [{"t": _iso(r.t), **{k: _r(getattr(r, k)) for k in ("pm_yes_ask", "pm_no_ask", "k_yes_ask", "k_no_ask", "cost_a", "net_edge")},
             "fill_usd": _r(r.cost_usd, 2) if pd.notna(r.cost_usd) else 0.0}
            for r in m.itertuples()]
    tot = {w: {"usd": _r(float(f["cost_usd"].sum()), 2) if len(f) else 0.0,
               "net_usd": _r(float(f["net_usd"].sum()), 2) if len(f) else 0.0} for w, f in fills.items()}
    entry = {"pair": p.id, "race_id": x["race_id"], "pm_market": p.pm_market, "kalshi_ticker": p.kalshi_ticker,
             "same_side": p.same_side, "risk_free": p.risk_free,
             "pm_quotes": "recorded order books (30 s)" if tops is not None else f"1-min price ± {PM_HALF_SPREAD}",
             "fillable": tot, "rows": rows}
    pos = m["net_edge"] > 0
    summ = {"pair": p.id, "race_id": x["race_id"], "pm_quotes": entry["pm_quotes"], "minutes": int(m["cost_a"].notna().sum()),
            "minutes_with_net_arb": int(pos.sum()), "max_net_edge": _r(m["net_edge"].max()),
            **{f"fill_{w}_usd": tot[w]["usd"] for w in FILL_WINDOWS}, **{f"fill_{w}_net": tot[w]["net_usd"] for w in FILL_WINDOWS}}
    return entry, summ


# ---------------------------------------------------------------------------------------------- export

def mark_no_kalshi(eid: str) -> None:
    """Elections Kalshi never listed: replace any licence wording in their Kalshi reasons with the real one."""
    f = SITE / eid / "stats.json"
    s = json.loads(f.read_text())
    why = NO_KALSHI[eid]
    r = s.setdefault("reasons", {})
    for k in [k for k in r if "kalshi" in k or k == "arbitrage"]:
        del r[k]
    r.update({"settle.kalshi": why, "catchup.kalshi": why, "capacity.kalshi": why, "arbitrage": why})
    f.write_text(json.dumps(s, ensure_ascii=False, separators=(",", ":")))
    print(f"{eid}: {why}")


def export(eid: str) -> dict:
    if eid in NO_KALSHI:
        mark_no_kalshi(eid)
        return {}
    meta, timeline, stats = load(eid)
    matched, kmk = links(eid, meta)
    if not kmk:
        print(f"{eid}: no Kalshi market on a dashboard race")
        return {}
    lo, hi = window(timeline, kmk)
    ev = (stats.get("evidence") or {}).get("settle") or "not-preregistered"
    frames, settle_rows = [], []
    for r in meta["races"]:
        rid = r["race_id"]
        rows = timeline.get(rid) or []
        if rid not in kmk or not rows:
            for row in rows:
                row["kalshi"] = None
            continue
        t = pd.to_datetime([row["t"] for row in rows], utc=True)
        pa, mk = race_kalshi(eid, kmk[rid], t, lo, hi)
        for row, v in zip(rows, pa.to_numpy()):
            row["kalshi"] = _r(v)
        num = lambda key: pd.to_numeric(pd.Series([row.get(key) for row in rows], dtype=object), errors="coerce").to_numpy()
        frames.append(pd.DataFrame({"race_id": rid, "t": t, "k": pa.to_numpy(), "P": num("P"), "counted": num("counted")}))
        # settle on Kalshi's own settlement of the market used; while Kalshi has not settled, on the side the
        # Polymarket price settled on (the outcome behind settle.polymarket)
        res = mk.get("result")
        a_won = None if res not in ("yes", "no") else ((res == "yes") == (mk["yes_is"] == "a"))
        pm_last = next((row["pm"] for row in reversed(rows) if row.get("pm") is not None), None)
        if a_won is None and (stats.get("settle", {}).get(rid) or {}).get("polymarket") and pm_last is not None:
            a_won = pm_last >= 0.5
        st = settle_time(pd.Series(t), pd.Series(pa.to_numpy() if a_won else 1 - pa.to_numpy())) if a_won is not None else None
        # settle is keyed by race (Hungary keys it by market and keeps races in settle_gated)
        if rid in stats.get("settle", {}) or rid not in (stats.get("settle_gated") or {}):
            stats.setdefault("settle", {}).setdefault(rid, {})["kalshi"] = _iso(st)
        if rid in (stats.get("settle_gated") or {}):
            stats["settle_gated"][rid]["kalshi"] = _iso(st)
        pm_st = (stats["settle"].get(rid) or {}).get("polymarket") or \
            ((stats.get("settle_gated") or {}).get(rid) or {}).get("polymarket")
        settle_rows.append({"race_id": rid, "kalshi_ticker": mk.get("ticker"), "kalshi_result": res, "kalshi": _iso(st),
                            "polymarket": pm_st,
                            "kalshi_minus_pm_min": _r((pd.Timestamp(st) - pd.Timestamp(pm_st)).total_seconds() / 60, 0)
                            if st is not None and pm_st else None})
    # catch-up / capacity
    has_model = any(f["P"].notna().any() for f in frames)
    cu = catchup(frames) if has_model else None
    stats.setdefault("catchup", {})["kalshi"] = cu
    cap = capacity(eid, frames, kmk, lo, hi) if has_model else None
    stats.setdefault("capacity", {})["kalshi"] = cap
    # arbitrage
    entries, summ = [], []
    rows_t = {r: pd.to_datetime([x["t"] for x in timeline[r]], utc=True) for r in kmk if timeline.get(r)}
    for x in matched:
        if x["race_id"] not in rows_t:
            continue
        e, s = pair_arb(eid, x, rows_t[x["race_id"]], lo, hi)
        if e:
            entries.append(e)
            summ.append(s)
    S = pd.DataFrame(summ)
    stats["arbitrage"] = None if S.empty else {
        "pairs": int(len(S)), "minutes_with_net_arb": int(S["minutes_with_net_arb"].sum()),
        "max_net_edge": _r(S["max_net_edge"].max()), "fillable_usd": _r(S["fill_1min_usd"].sum(), 2),
        "fillable_net_usd": _r(S["fill_1min_net"].sum(), 2),
        "fillable_usd_10s": _r(S["fill_10s_usd"].sum(), 2), "fillable_usd_1s": _r(S["fill_1s_usd"].sum(), 2),
        "pm_half_spread": PM_HALF_SPREAD, "pm_quotes": sorted(set(S["pm_quotes"])),
        "note": "net of both venues' taker fees; Polymarket asks from recorded books where pm_quotes says so, else the "
                "1-min price ± 0.01; fillable = both venues' "
                "real taker fills in the same minute (10 s / 1 s buckets also given) at a combined cost below $1; no "
                "pair is risk-free (resolution rules differ: configs/pairs)"}
    # reasons / evidence
    # every Kalshi reason is ours to recompute (the exporters' defaults predate the Kalshi data)
    reasons = {k: v for k, v in (stats.get("reasons") or {}).items() if not ("kalshi" in k or k == "arbitrage")}
    if cu is None:
        reasons["catchup.kalshi"] = "no model probability during the count" if not has_model else \
            "too few minutes with both the model and Kalshi strictly between 2% and 98%"
    if cap is None:
        reasons["capacity.kalshi"] = ("computed only where a frozen night config defines the trading rule (Brazil)"
                                      if eid not in RULE_CFG else "no model probability")
    if stats["arbitrage"] is None:
        reasons["arbitrage"] = "no matched pair with both venues' prices in the window"
    missing = [rid for rid in timeline if rid not in kmk]
    if missing:
        reasons["settle.kalshi"] = f"Kalshi lists no market on {len(missing)} of {len(timeline)} races: " + ", ".join(missing)
    reasons = {k: v for k, v in reasons.items() if v}
    stats["reasons"] = reasons
    stats.setdefault("evidence", {})["arbitrage"] = ev
    # meta: Kalshi listings in our own words, source, note
    for r in meta["races"]:
        keep = [m for m in r.get("markets") or [] if m["platform"] != "kalshi"]
        for k in kmk.get(r["race_id"], []):
            keep.append({"platform": "kalshi", "id": k["ticker"], "yes_is": k["yes_is"],
                         "question": f"{k['pair'].kalshi_resolution} (Kalshi {k['ticker']})"})
        r["markets"] = keep
    meta["sources"] = [s for s in meta.get("sources", []) if not s["name"].startswith("Kalshi")] + [SRC_KALSHI]
    note = ("kalshi = Kalshi P(candidate a) from 1-minute candles: bid/ask midpoint when the spread is at most 10¢, else "
            "the last trade; YES = b markets are inverted. Prices © Kalshi, shown as derived charts with credit.")
    meta["notes"] = [n for n in meta.get("notes", []) if not n.startswith("kalshi = ")] + [note]
    d = SITE / eid
    for name, obj in (("meta", meta), ("timeline", timeline), ("stats", stats)):
        (d / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    (d / "arb.json").write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")))
    RES.mkdir(parents=True, exist_ok=True)
    S.to_csv(RES / f"{eid}_arb_pairs.csv", index=False)
    pd.DataFrame(settle_rows).to_csv(RES / f"{eid}_settle.csv", index=False)
    out = {"eid": eid, "races_with_kalshi": len(kmk), "settle": settle_rows, "catchup": cu, "capacity": cap,
           "arbitrage": stats["arbitrage"]}
    print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return out


# ---------------------------------------------------------------------------------------------- US pre-election

US_HOURLY_FROM, US_MINUTE_FROM, US_TRADES_FROM = "open", pd.Timestamp("2026-10-01T00:00Z"), pd.Timestamp("2026-10-21T00:00Z")


def us2026() -> pd.DataFrame:
    """Pre-election history for us2026 pairs (no dashboard data exists yet): hourly candles from each market's
    open, 1-min candles from 2026-10-01, Kalshi and Polymarket taker fills from 2026-10-21; per pair the
    minute arbitrage since 2026-10-21 (same definitions as arb.json)."""
    _, pairs = arb.load_pairs(arb.PAIRS_DIR / "us2026.toml")
    now = pd.Timestamp.now(tz="UTC").floor("min")
    d = RAW / "kalshi" / "us2026"
    out = []
    for p in pairs:
        m = kalshi.market(p.kalshi_ticker)
        opened = pd.Timestamp(m["open_time"])
        _cached(d / "candles_1h" / f"{p.kalshi_ticker}.parquet",
                lambda: kalshi.candlesticks(p.kalshi_series, p.kalshi_ticker, opened, now, 60))
        c = _cached(d / "candles" / f"{p.kalshi_ticker}.parquet",
                    lambda: kalshi.candlesticks(p.kalshi_series, p.kalshi_ticker, max(opened, US_MINUTE_FROM), now))
        kt = _cached(d / "trades" / f"{p.kalshi_ticker}.parquet", lambda: kalshi.trades(p.kalshi_ticker, US_TRADES_FROM, now))
        dp = RAW / "polymarket_window" / "us2026"
        px = _cached(dp / "prices" / f"{p.pm_market}.parquet",
                     lambda: polymarket.price_history(p.pm_yes_token, US_TRADES_FROM, now, 1))
        pt = _cached(dp / "trades" / f"{p.pm_market}.parquet", lambda: pm_trades_window(p.pm_condition_id, US_TRADES_FROM, now))
        if c.empty or px.empty:
            continue
        a = arb.minute_arb(p, px, c, PM_HALF_SPREAD, US_TRADES_FROM, now - pd.Timedelta("1h"))
        f = arb.fillable(p, pt, kt, p.pm_yes_token) if len(pt) and len(kt) else pd.DataFrame(columns=["cost_usd", "net_usd"])
        out.append({"pair": p.id, "race_id": p.race_id, "minutes": int(a["cost_a"].notna().sum()),
                    "minutes_with_net_arb": int((a["net_edge"] > 0).sum()), "max_net_edge": _r(a["net_edge"].max()),
                    "fill_1min_usd": _r(float(f["cost_usd"].sum()), 2), "fill_1min_net": _r(float(f["net_usd"].sum()), 2)})
        print(out[-1], flush=True)
    S = pd.DataFrame(out)
    RES.mkdir(parents=True, exist_ok=True)
    S.to_csv(RES / "us2026_preelection_arb.csv", index=False)
    return S


def write_summary() -> dict:
    """summary.json["kalshi"] (only that key): per election, Kalshi vs Polymarket settle times and arbitrage."""
    out = []
    for eid in ELECTIONS:
        f = RES / f"{eid}_settle.csv"
        if not f.exists():
            continue
        st = pd.read_csv(f)
        d = st["kalshi_minus_pm_min"].dropna()
        a = json.loads((SITE / eid / "stats.json").read_text()).get("arbitrage") or {}
        out.append({"id": eid, "races": int(len(st)), "pairs": a.get("pairs", 0),
                    "settle": {"compared": int(len(d)), "kalshi_first": int((d < 0).sum()), "polymarket_first": int((d > 0).sum()),
                               "same_minute": int((d == 0).sum()),
                               "median_kalshi_minus_pm_min": _r(float(d.median()), 0) if len(d) else None},
                    "arbitrage": {k: a.get(k) for k in ("minutes_with_net_arb", "max_net_edge", "fillable_usd",
                                                         "fillable_net_usd", "fillable_usd_1s")}})
    block = {"source": SRC_KALSHI["name"], "licence": SRC_KALSHI["licence"], "doc": "docs/kalshi.md",
             "settle_note": "minutes Kalshi settled (stayed >= 0.9 on the winner) minus Polymarket; negative = Kalshi first",
             "elections": out}
    p = SITE / "summary.json"
    s = json.loads(p.read_text()) if p.exists() else {}
    s["kalshi"] = block
    p.write_text(json.dumps(s, ensure_ascii=False, separators=(",", ":")))
    return block


if __name__ == "__main__":
    args = sys.argv[1:]
    step = args.pop(0) if args and args[0] in ("download", "export") else None
    if args == ["us2026"]:
        print(us2026().to_string())
        sys.exit()
    for e in args or (*ELECTIONS, *NO_KALSHI):
        if step in (None, "download") and e not in NO_KALSHI:
            download(e)
        if step in (None, "export"):
            export(e)
    if step in (None, "export"):
        print(json.dumps(write_summary(), ensure_ascii=False))
