"""The cross-category research universe: resolved Polymarket binary markets, sampled by category × week.

Sampling (fixed, ex-ante neutral):
  for every category and ISO week from START to now, list every closed event whose endDate falls
  in that week under the category's tags; assign each event ONE primary category by PRIORITY over
  its own tags; keep events whose primary category is this category; draw up to PER_WEEK of them
  with a seed derived from (category, week); keep up to MARKETS_PER_EVENT of each event's resolved
  binary markets, drawn by seed. No volume filter (lifetime volume is an ex-post quantity).

Anchor E (scheduled information time, known ex ante): market gameStartTime → event startTime /
eventStartTime → market endDate → event endDate. Never closedTime (that is when resolution
happened, i.e. look-ahead).

Layout (data/raw/universe/): events.parquet, markets.parquet, prices/<condition_id>.parquet
(YES price, 5-min fidelity, [E − 14d, max(E, close) + 3d]), presignal.parquet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import numpy as np
import pandas as pd

from . import _http
from .paths import RAW

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
DIR = RAW / "universe"

PRIORITY: list[tuple[str, tuple[str, ...]]] = [
    ("elections", ("elections", "global-elections", "world-elections")),
    ("mentions", ("mention-markets",)),       # "what will X say": usually also tagged politics / culture
    ("geopolitics", ("geopolitics",)),
    ("politics", ("politics",)),
    ("economy", ("economy", "finance")),
    ("crypto", ("crypto",)),
    ("weather", ("weather",)),
    ("tech-science", ("tech", "science", "ai")),
    ("business", ("business",)),
    ("sports", ("sports",)),
    ("culture", ("pop-culture",)),
]
CATEGORIES = [c for c, _ in PRIORITY]
START = pd.Timestamp("2024-01-01", tz="UTC")
PER_WEEK = 12
MARKETS_PER_EVENT = 20
BEFORE, AFTER = pd.Timedelta(days=14), pd.Timedelta(days=3)
CHUNK = pd.Timedelta(days=7)          # prices-history rejects ranges of roughly two weeks or more


def _ts(x) -> pd.Timestamp:
    if not x:
        return pd.NaT
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def primary_category(tag_slugs: set[str]) -> str | None:
    for cat, tags in PRIORITY:
        if tag_slugs & set(tags):
            return cat
    return None


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def scheduled_time(m: dict, e: dict) -> pd.Timestamp:
    for x in (m.get("gameStartTime"), e.get("startTime"), e.get("eventStartTime"), m.get("eventStartTime"),
              m.get("endDate"), e.get("endDate")):
        t = _ts(x)
        if pd.notna(t):
            return t
    return pd.NaT


async def _get_json(client, url, params):
    for attempt in range(8):
        try:
            r = await client.get(url, params=params)
        except Exception:
            await asyncio.sleep(_http._delay(None, attempt))
            continue
        if r.status_code in _http.RETRY_STATUS:
            await asyncio.sleep(_http._delay(r, attempt))
            continue
        if r.status_code >= 400:
            return None
        return r.json()
    return None


async def _list_week(client, sem, tag: str, w0: pd.Timestamp, w1: pd.Timestamp) -> tuple[list[dict], bool]:
    out, off = [], 0
    async with sem:
        while off < 10_000:
            page = await _get_json(client, f"{GAMMA}/events", {
                "tag_slug": tag, "closed": "true", "limit": 100, "offset": off,
                "end_date_min": w0.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_date_max": w1.strftime("%Y-%m-%dT%H:%M:%SZ")})
            if not page:
                return out, False
            out += page
            if len(page) < 100:
                return out, False
            off += 100
    return out, True                     # truncated at the API's offset cap


async def catalogue(categories=CATEGORIES, per_week: int = PER_WEEK, concurrency: int = 8):
    weeks = pd.date_range(START, pd.Timestamp.now(tz="UTC"), freq="7D")
    sem = asyncio.Semaphore(concurrency)
    ev_rows, mk_rows, trunc = [], [], []
    async with _http.aclient() as client:
        for cat in categories:
            tags = dict(PRIORITY)[cat]
            jobs = [(t, w) for w in weeks for t in tags]
            res = await asyncio.gather(*(_list_week(client, sem, t, w, w + pd.Timedelta(days=7)) for t, w in jobs))
            by_week: dict[pd.Timestamp, dict[str, dict]] = {}
            for (t, w), (events, truncated) in zip(jobs, res):
                if truncated:
                    trunc.append((cat, t, w))
                for e in events:
                    if primary_category({x.get("slug") for x in e.get("tags") or []}) == cat:
                        by_week.setdefault(w, {})[e["id"]] = e
            for w, evs in by_week.items():
                ids = sorted(evs)
                rng = np.random.default_rng(_seed(cat, w.date()))
                for eid in rng.permutation(ids)[:per_week]:
                    e = evs[eid]
                    ev_rows.append(_event_row(e, cat, w))
                    mk_rows += _market_rows(e, cat)
            print(f"{cat}: {sum(len(v) for v in by_week.values())} events listed, "
                  f"{sum(r['category'] == cat for r in ev_rows)} sampled", flush=True)
    ev = pd.DataFrame(ev_rows).drop_duplicates("event_id")
    mk = pd.DataFrame(mk_rows).drop_duplicates("condition_id")
    DIR.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(DIR / "events.parquet")
    mk.to_parquet(DIR / "markets.parquet")
    pd.DataFrame(trunc, columns=["category", "tag", "week"]).to_parquet(DIR / "truncated_weeks.parquet")
    return ev, mk


def _event_row(e: dict, cat: str, week: pd.Timestamp) -> dict:
    return {"event_id": e["id"], "event_slug": e["slug"], "title": e["title"], "category": cat,
            "sample_week": week, "end": _ts(e.get("endDate")), "closed_time": _ts(e.get("closedTime")),
            "neg_risk": bool(e.get("negRisk")), "n_markets": len(e.get("markets") or []),
            "tags": ",".join(sorted(x.get("slug") or "" for x in e.get("tags") or []))}


def _market_rows(e: dict, cat: str) -> list[dict]:
    rows = []
    for m in e.get("markets") or []:
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if len(outs) != 2 or len(prices) != 2 or len(toks) != 2 or sorted(prices) != [0.0, 1.0]:
            continue                                  # unresolved, 50/50, or not binary
        yes = outs.index("Yes") if "Yes" in outs else 0
        fs = m.get("feeSchedule") or {}
        rows.append({
            "event_id": e["id"], "category": cat, "slug": m["slug"], "question": m["question"],
            "condition_id": m["conditionId"], "yes_token": toks[yes], "outcome": int(prices[yes] == 1.0),
            "volume": float(m.get("volume") or 0), "created": _ts(m.get("createdAt")),
            "scheduled": scheduled_time(m, e), "end": _ts(m.get("endDate") or e.get("endDate")),
            "closed_time": _ts(m.get("closedTime")),
            "fee_rate": float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0,
            "resolution_status": m.get("umaResolutionStatus"),
        })
    if len(rows) > MARKETS_PER_EVENT:
        rng = np.random.default_rng(_seed("markets", e["id"]))
        rows = [rows[i] for i in sorted(rng.choice(len(rows), MARKETS_PER_EVENT, replace=False))]
    return rows


async def _prices(client, sem, m) -> str:
    out = DIR / "prices" / f"{m.condition_id}.parquet"
    if out.exists():
        return "skip"
    if pd.isna(m.scheduled):
        return "no-anchor"
    start = m.scheduled - BEFORE
    if pd.notna(m.created):
        start = max(start, m.created)
    stop = max(m.scheduled, m.closed_time if pd.notna(m.closed_time) else m.scheduled) + AFTER
    hist = []
    async with sem:
        t = start
        while t < stop:
            u = min(t + CHUNK, stop)
            part = await _get_json(client, f"{CLOB}/prices-history", {
                "market": m.yes_token, "startTs": int(t.timestamp()), "endTs": int(u.timestamp()), "fidelity": 5})
            if part is None:
                return "failed"
            hist += part.get("history", [])
            t = u
    h = pd.DataFrame(hist, columns=["t", "p"])
    h["time"] = pd.to_datetime(h.pop("t"), unit="s", utc=True)
    h.rename(columns={"p": "price"}).drop_duplicates("time").sort_values("time").to_parquet(out)
    return "ok" if len(h) else "empty"


async def prices(concurrency: int = 8) -> pd.Series:
    mk = pd.read_parquet(DIR / "markets.parquet")
    (DIR / "prices").mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    status: list[str] = []
    async with _http.aclient() as client:
        rows = list(mk.itertuples())
        for i in range(0, len(rows), 1000):
            status += await asyncio.gather(*(_prices(client, sem, m) for m in rows[i:i + 1000]))
            print(f"prices {min(i + 1000, len(rows))}/{len(rows)}", pd.Series(status).value_counts().to_dict(), flush=True)
    return pd.Series(status).value_counts()


async def _trades_window(client, cid: str, lo: int, hi: int, max_pages: int) -> tuple[list[dict], bool]:
    """Taker fills with lo ≤ timestamp < hi, newest first; capped=True if max_pages was hit."""
    out, end = [], hi - 1
    for _ in range(max_pages):
        batch = await _get_json(client, f"{DATA_API}/trades",
                                {"market": cid, "takerOnly": "true", "limit": 500, "end": end})
        if batch is None:
            raise RuntimeError(f"trades fetch failed for {cid}")
        out += [x for x in batch if lo <= x["timestamp"] < hi]
        if len(batch) < 500 or min(x["timestamp"] for x in batch) < lo:
            return out, False
        end = min(x["timestamp"] for x in batch) - 1
    return out, True


async def _presignal(client, sem, cid: str, t: pd.Timestamp, window: pd.Timedelta, max_pages: int) -> dict:
    async with sem:
        try:
            fills, capped = await _trades_window(client, cid, int((t - window).timestamp()), int(t.timestamp()), max_pages)
        except RuntimeError:
            return {"condition_id": cid, "t": t, "pre_vol": np.nan, "pre_fills": 0, "capped": False, "failed": True}
    return {"condition_id": cid, "t": t, "pre_vol": sum(float(x["size"]) * float(x["price"]) for x in fills),
            "pre_fills": len(fills), "capped": capped, "failed": False}


async def presignal_volume(signals: pd.DataFrame, window: str = "7D", max_pages: int = 40,
                           concurrency: int = 8) -> pd.DataFrame:
    """Taker notional in [t − window, t) per (condition_id, t) — depth known before the signal.
    capped=True ⇒ pre_vol is a lower bound."""
    sem = asyncio.Semaphore(concurrency)
    w = pd.Timedelta(window)
    out = []
    async with _http.aclient() as client:
        rows = list(signals[["condition_id", "t"]].itertuples(index=False))
        for i in range(0, len(rows), 500):
            out += await asyncio.gather(*(_presignal(client, sem, c, t, w, max_pages) for c, t in rows[i:i + 500]))
            print(f"presignal {min(i + 500, len(rows))}/{len(rows)}", flush=True)
    return pd.DataFrame(out)


async def _fills_after(client, sem, cid, yes_token, t, side_yes: bool, limit_yes: float, horizon, max_pages):
    """Taker notional that traded on our side at a price at least as good as ours within horizon after t.
    Buying YES at ≤ limit_yes: YES buys at ≤ limit or NO sells at ≥ 1 − limit (same economic fill)."""
    async with sem:
        try:
            fills, capped = await _trades_window(client, cid, int(t.timestamp()), int((t + horizon).timestamp()), max_pages)
        except RuntimeError:
            return np.nan, False
    notional = 0.0
    for x in fills:
        p_yes = float(x["price"]) if x["asset"] == yes_token else 1 - float(x["price"])
        buys_yes = (x["side"] == "BUY") == (x["asset"] == yes_token)
        if buys_yes == side_yes and ((p_yes <= limit_yes) if side_yes else (p_yes >= limit_yes)):
            notional += float(x["size"]) * float(x["price"])
    return notional, capped


async def fills_after(entries: pd.DataFrame, horizon: str = "30min", max_pages: int = 20,
                      concurrency: int = 8) -> pd.DataFrame:
    """entries: condition_id, yes_token, t (entry time), side_yes (bool), limit_yes (YES-equivalent
    limit). Returns entries + fill_notional (taker $ that traded at our price or better)."""
    sem = asyncio.Semaphore(concurrency)
    h = pd.Timedelta(horizon)
    async with _http.aclient() as client:
        res = await asyncio.gather(*(_fills_after(client, sem, r.condition_id, r.yes_token, r.t, r.side_yes,
                                                  r.limit_yes, h, max_pages) for r in entries.itertuples()))
    return entries.assign(fill_notional=[r[0] for r in res], fill_capped=[r[1] for r in res])


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_parquet(DIR / "events.parquet"), pd.read_parquet(DIR / "markets.parquet")
