"""Live forward test: record signals and the order book at the moment they fire, before any outcome
exists. This is the cleanest out-of-sample test available (the future cannot leak into it) and
the only way to measure executable size: historical order books are not published.

Rules (spec kind "forward", e.g. prereg/forward_v1.toml):
  price      Polymarket's displayed price: book midpoint if spread ≤ 10¢, else last trade
             (the convention behind the historical price series).
  late       at the first poll in [E − h, E − h + 30 min) for h in params.horizons (in order), if
             the leading side's price is in [lead_lo, lead_hi] → buy the leading side.
  shock      first poll with |price_now − price_{≥1h ago}| ≥ threshold, price_now in [0.03, 0.97],
             within [E − 7d, E + 2d]; depth = taker $ in the 7 days before (data-api);
             depth < depth_split → fade (buy the side the move went against); else follow.
  fill       each signal walks the live book for stakes in params.stakes_usd: average price, shares,
             fraction filled, taker fee at the market's own rate.
Only paper signals are recorded; nothing is traded.

Files (data/live/): signals.jsonl, checks.jsonl, tops/<date>/<hhmm>.parquet (top of book per poll).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import _http, registry, stats, universe
from .paths import DATA, ROOT

LIVE = DATA / "live"
GAMMA, CLOB = universe.GAMMA, universe.CLOB
GRACE = pd.Timedelta("30min")
REDISCOVER = pd.Timedelta("30min")


@dataclass
class Tracked:
    condition_id: str
    event_id: str
    category: str
    question: str
    yes_token: str
    scheduled: pd.Timestamp
    fee_rate: float


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


async def discover(client, categories: list[str]) -> dict[str, Tracked]:
    now = _now()
    out: dict[str, Tracked] = {}
    for cat in categories:
        for tag in dict(universe.PRIORITY)[cat]:
            off = 0
            while off < 10_000:
                page = await universe._get_json(client, f"{GAMMA}/events", {
                    "tag_slug": tag, "closed": "false", "limit": 100, "offset": off,
                    "end_date_min": (now - pd.Timedelta("2D")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max": (now + pd.Timedelta("10D")).strftime("%Y-%m-%dT%H:%M:%SZ")})
                if not page:
                    break
                for e in page:
                    if universe.primary_category({x.get("slug") for x in e.get("tags") or []}) != cat:
                        continue
                    for m in e.get("markets") or []:
                        try:
                            outs = json.loads(m.get("outcomes") or "[]")
                            toks = json.loads(m.get("clobTokenIds") or "[]")
                        except (ValueError, TypeError):
                            continue
                        if outs != ["Yes", "No"] or len(toks) != 2 or m.get("closed") or not m.get("acceptingOrders"):
                            continue
                        E = universe.scheduled_time(m, e)
                        if pd.isna(E) or not (now - pd.Timedelta("2D") <= E <= now + pd.Timedelta("7D")):
                            continue
                        fs = m.get("feeSchedule") or {}
                        out[m["conditionId"]] = Tracked(m["conditionId"], e["id"], cat, m["question"], toks[0], E,
                                                        float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0)
                if len(page) < 100:
                    break
                off += 100
    return out


async def fetch_books(client, tokens: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(tokens), 100):
        body = [{"token_id": t} for t in tokens[i:i + 100]]
        for attempt in range(6):
            try:
                r = await client.post(f"{CLOB}/books", json=body)
            except Exception:
                await asyncio.sleep(_http._delay(None, attempt))
                continue
            if r.status_code in _http.RETRY_STATUS:
                await asyncio.sleep(_http._delay(r, attempt))
                continue
            if r.status_code < 400:
                for b in r.json():
                    out[b["asset_id"]] = b
            break
    return out


def top(book: dict) -> dict:
    bids = [(float(x["price"]), float(x["size"])) for x in book.get("bids") or []]
    asks = [(float(x["price"]), float(x["size"])) for x in book.get("asks") or []]
    bb = max(bids, default=(np.nan, 0.0))
    ba = min(asks, default=(np.nan, 0.0))
    last = float(book["last_trade_price"]) if book.get("last_trade_price") not in (None, "") else np.nan
    spread = ba[0] - bb[0]
    price = (bb[0] + ba[0]) / 2 if np.isfinite(spread) and spread <= 0.10 else last
    return {"best_bid": bb[0], "bid_size": bb[1], "best_ask": ba[0], "ask_size": ba[1], "last": last,
            "price": price, "spread": spread}


def walk(book: dict, side_yes: bool, stake_usd: float, fee_rate: float) -> dict:
    """Buy side (YES, or NO via YES bids at 1 − bid) with up to stake_usd including taker fees."""
    if side_yes:
        levels = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks") or [])
    else:
        levels = sorted(((1 - float(x["price"]), float(x["size"])) for x in book.get("bids") or []))
    spent = shares = fee = 0.0
    for q, size in levels:
        unit = q + fee_rate * q * (1 - q)
        take = min(size, (stake_usd - spent) / unit)
        if take <= 0:
            break
        shares += take
        spent += take * unit
        fee += take * fee_rate * q * (1 - q)
        if spent >= stake_usd - 1e-9:
            break
    return {"stake": stake_usd, "shares": shares, "spent": spent, "fee": fee,
            "avg_cost": spent / shares if shares else np.nan, "filled_fraction": spent / stake_usd}


def _append(path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _read(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


async def _record(client, spec, kind, m: Tracked, book, tp, side_yes, extra) -> None:
    P = spec["params"]
    rec = {"spec": spec["id"], "sha256": spec["_sha256"], "kind": kind, "t": _now(), **asdict(m), **tp,
           "side_yes": bool(side_yes), **extra,
           "fills": [walk(book, side_yes, s, m.fee_rate) for s in P["stakes_usd"]],
           "book": {"bids": (book.get("bids") or [])[-25:], "asks": (book.get("asks") or [])[-25:]}}
    _append(LIVE / "signals.jsonl", rec)
    print(f"{rec['t']:%m-%d %H:%M} {kind:5s} {m.category:12s} side={'YES' if side_yes else 'NO '} "
          f"p={tp['price']:.3f} fill$={rec['fills'][0]['spent']:.0f} {m.question[:60]}", flush=True)


async def run(spec_path, interval: float = 120.0) -> None:
    spec = registry.load_spec(spec_path)
    registry.assert_registered(spec)
    P = spec["params"]
    horizons = {h: pd.Timedelta(h) for h in P["horizons"]}
    signaled = {(s["kind"], s["condition_id"]) for s in _read(LIVE / "signals.jsonl")}
    checked = {(c["condition_id"], c["horizon"]) for c in _read(LIVE / "checks.jsonl")}
    hist: dict[str, deque] = {}
    tracked: dict[str, Tracked] = {}
    last_disc = pd.Timestamp(0, tz="UTC")
    buffer = []
    async with _http.aclient() as client:
        while True:
            now = _now()
            if now - last_disc >= REDISCOVER:
                tracked = await discover(client, spec["categories"])
                last_disc = now
                print(f"{now:%m-%d %H:%M} tracking {len(tracked)} markets", flush=True)
            books = await fetch_books(client, [m.yes_token for m in tracked.values()])
            now = _now()
            for cid, m in tracked.items():
                book = books.get(m.yes_token)
                if not book:
                    continue
                tp = top(book)
                buffer.append({"t": now, "condition_id": cid, **{k: tp[k] for k in ("best_bid", "bid_size", "best_ask", "ask_size", "last", "price")}})
                p = tp["price"]
                if not np.isfinite(p):
                    continue
                h = hist.setdefault(cid, deque())
                h.append((now, p))
                while h and now - h[0][0] > pd.Timedelta("2h"):
                    h.popleft()
                # late certainty
                if ("late", cid) not in signaled:
                    for name, hz in horizons.items():
                        if (cid, name) in checked or not (m.scheduled - hz <= now < m.scheduled - hz + GRACE):
                            continue
                        lead = max(p, 1 - p)
                        ok = P["lead_lo"] <= lead <= P["lead_hi"]
                        checked.add((cid, name))
                        _append(LIVE / "checks.jsonl", {"condition_id": cid, "horizon": name, "t": now, "price": p, "qualified": ok})
                        if ok:
                            signaled.add(("late", cid))
                            await _record(client, spec, "late", m, book, tp, p >= 0.5, {"horizon": name})
                            break
                # shock
                if ("shock", cid) not in signaled and m.scheduled - pd.Timedelta("7D") <= now <= m.scheduled + pd.Timedelta("2D") \
                        and 0.03 <= p <= 0.97:
                    past = [(t, v) for t, v in h if t <= now - pd.Timedelta("1h")]
                    if past and now - pd.Timedelta("1h") - past[-1][0] <= pd.Timedelta("10min"):
                        move = p - past[-1][1]
                        if abs(move) >= P["threshold"]:
                            signaled.add(("shock", cid))
                            try:
                                fills, capped = await universe._trades_window(
                                    client, cid, int((now - pd.Timedelta(P["depth_window"])).timestamp()), int(now.timestamp()), 40)
                                pre_vol = sum(float(x["size"]) * float(x["price"]) for x in fills)
                            except RuntimeError:
                                pre_vol, capped = np.nan, False
                            thin = bool(pre_vol < P["depth_split_usd"]) if np.isfinite(pre_vol) else None
                            side_yes = (move < 0) if thin else (move > 0)
                            await _record(client, spec, "shock", m, book, tp, side_yes,
                                          {"move": move, "pre_vol": pre_vol, "pre_vol_capped": capped, "thin": thin})
            if len(buffer) >= 20_000:
                _flush(buffer)
                buffer = []
            await asyncio.sleep(max(0.0, interval - (_now() - now).total_seconds()))


def _flush(rows: list[dict]) -> None:
    t = rows[0]["t"]
    out = LIVE / "tops" / f"{t:%Y-%m-%d}" / f"{t:%H%M%S}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out)


# ------------------------------------------------------------------ evaluation

async def _outcomes(cids: list[str]) -> dict[str, int]:
    out = {}
    async with _http.aclient() as client:
        for i in range(0, len(cids), 50):
            params = [("condition_ids", c) for c in cids[i:i + 50]] + [("closed", "true"), ("limit", "100")]
            r = await client.get(f"{GAMMA}/markets", params=params)
            r.raise_for_status()
            for m in r.json():
                p = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
                if sorted(p) == [0.0, 1.0]:
                    out[m["conditionId"]] = int(p[0] == 1.0)
    return out


def _signals(spec: dict) -> pd.DataFrame:
    s = pd.DataFrame([x for x in _read(LIVE / "signals.jsonl") if x["spec"] == spec["id"]])
    if s.empty:
        return s
    s["test"] = np.select([s["kind"] == "late", s["thin"] == True, s["thin"] == False],  # noqa: E712
                          ["late_certainty", "thin_fade", "deep_follow"], "depth_unknown")
    return s


def status(spec_path) -> pd.DataFrame:
    """Counts only — resolved or not, never win/loss — so checking progress is not a look."""
    spec = registry.load_spec(spec_path)
    s = _signals(spec)
    if s.empty:
        return pd.DataFrame()
    resolved = asyncio.run(_outcomes(s["condition_id"].unique().tolist()))
    s["resolved"] = s["condition_id"].isin(resolved)
    return s.groupby(["test", "category"]).agg(signals=("resolved", "size"), resolved=("resolved", "sum"),
                                               events=("event_id", "nunique")).reset_index()


def evaluate(spec_path, stake_index: int = 0) -> pd.DataFrame:
    """Final verdict. Opens only when the spec's stopping rule is met; logs the look."""
    spec = registry.load_spec(spec_path)
    stop = spec["stop"]
    s = _signals(spec)
    out = asyncio.run(_outcomes(s["condition_id"].unique().tolist()))
    s["y"] = s["condition_id"].map(out)
    s = s.dropna(subset=["y"])
    gate = s[(s["test"] == stop["test"]) & (s["category"] == stop["category"])]
    if len(gate) < stop["min_resolved_signals"] and _now() < pd.Timestamp(stop["deadline"], tz="UTC"):
        raise registry.HoldoutLocked(f"stopping rule not met: {len(gate)}/{stop['min_resolved_signals']} resolved "
                                     f"{stop['category']} {stop['test']} signals, deadline {stop['deadline']}")
    look = registry.open_holdout(spec)
    f = pd.DataFrame([x[stake_index] for x in s["fills"]], index=s.index)
    s = s.join(f)
    win = np.where(s["side_yes"], s["y"] == 1, s["y"] == 0)
    s["ret"] = np.where(win, 1 / s["avg_cost"], 0.0) - 1
    s = s[s["shares"] > 0]
    rows = []
    for t in spec["primary"]["tests"]:
        test, cat = t.split(":")
        d = s[(s["test"] == test) & (s["category"] == cat)]
        if d["event_id"].nunique() < 20:
            rows.append({"test": test, "category": cat, "n": len(d), "status": "insufficient"})
            continue
        r = stats.cluster_bootstrap(d, stats.mean_of("ret"), seed=11)
        rows.append({"test": test, "category": cat, "n": r["n"], "events": r["clusters"], "mean_ret": r["estimate"],
                     "lo95": r["lo95"], "hi95": r["hi95"], "p": r["p_greater"],
                     "avg_filled_usd": float(d["spent"].mean()), "share_fully_filled": float((d["filled_fraction"] > 0.99).mean()),
                     "status": "tested"})
    res = pd.DataFrame(rows)
    ok = res["status"] == "tested"
    res.loc[ok, "p_holm"] = stats.holm(res.loc[ok, "p"])
    res["look"] = look["look"]
    out_dir = ROOT / "results" / "live" / spec["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_dir / f"verdict_look{look['look']}.csv", index=False)
    s.drop(columns=["book", "fills"]).to_parquet(out_dir / f"signals_look{look['look']}.parquet")
    return res
