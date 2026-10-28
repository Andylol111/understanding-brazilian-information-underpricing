"""Kalshi public market data (Trade API v2): series/events/markets, order books, trades, candlesticks.

No auth needed for these GET endpoints (docs/kalshi.md). Prices come as dollar strings ("0.6300") and
sizes as fractional contract counts ("102275.95"); both are parsed to floats here. A contract pays $1.
Order books list resting BIDS only, for YES and for NO, ascending: the YES ask is 1 − best NO bid
(a NO bid at q is an offer to sell YES at 1 − q) and vice versa.

Requests are spaced at least `MIN_INTERVAL` seconds apart (process-wide), well under Kalshi's Basic
read limit, and retried with backoff on 429/5xx via `brunder._http`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from . import _http
from .paths import RAW

API = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI = RAW / "kalshi"
MIN_INTERVAL = 0.1                      # s between requests: ≤ 10 req/s (Basic tier allows 20 reads/s)
CANDLE_CHUNK = pd.Timedelta(days=3)     # 4 320 one-minute candles per request

_lock = threading.Lock()
_last = 0.0


def _pace() -> None:
    global _last
    with _lock:
        wait = _last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def _get(c: httpx.Client, path: str, params=None) -> dict:
    _pace()
    return _http.get_json(c, f"{API}{path}", params)


def _paged(c: httpx.Client, path: str, key: str, params: dict, limit: int) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        d = _get(c, path, p)
        out += d.get(key) or []
        cursor = d.get("cursor")
        if not cursor or not d.get(key):
            return out


def num(x) -> float:
    """Kalshi dollar / fixed-point string → float (NaN for None or '')."""
    return float(x) if x not in (None, "") else float("nan")


# ---------------------------------------------------------------------------------------------- catalogue

def all_series(category: str | None = None) -> list[dict]:
    with _http.client() as c:
        return _get(c, "/series", {"category": category} if category else None).get("series") or []


def search_series(pattern: str, category: str | None = "Elections") -> pd.DataFrame:
    """Kalshi's API has no free-text search: filter the series list by a regex on ticker + title."""
    rx = re.compile(pattern, re.I)
    return pd.DataFrame([
        {"series": s["ticker"], "title": s["title"], "fee_type": s.get("fee_type"),
         "fee_multiplier": s.get("fee_multiplier"), "contract_terms_url": s.get("contract_terms_url")}
        for s in all_series(category) if rx.search(f"{s['ticker']} {s['title']}")])


def series(ticker: str) -> dict:
    with _http.client() as c:
        return _get(c, f"/series/{ticker}")["series"]


def events(series_ticker: str, status: str | None = None, nested: bool = True) -> list[dict]:
    """Events of a series (status: unopened|open|closed|settled), each with its markets if `nested`."""
    p = {"series_ticker": series_ticker, "with_nested_markets": str(nested).lower()}
    if status:
        p["status"] = status
    with _http.client() as c:
        return _paged(c, "/events", "events", p, 200)


def cutoff() -> dict[str, pd.Timestamp]:
    """Historical-tier cutoffs (GET /historical/cutoff): markets settled before `market_settled_ts` and trades
    before `trades_created_ts` are only served by /historical/*. The cutoff moves forward over time."""
    global _cutoff
    if _cutoff is None:
        with _http.client() as c:
            _cutoff = {k: pd.Timestamp(v) for k, v in _get(c, "/historical/cutoff").items()}
    return _cutoff


_cutoff: dict | None = None


def event(event_ticker: str) -> dict:
    """Event with its markets (`markets` key), as one dict. Events whose markets settled before the
    historical cutoff come back without nested markets; those are read from /historical/markets."""
    with _http.client() as c:
        d = _get(c, f"/events/{event_ticker}", {"with_nested_markets": "true"})
        ev = d["event"]
        ev["markets"] = ev.get("markets") or d.get("markets") or \
            _paged(c, "/historical/markets", "markets", {"event_ticker": event_ticker}, 200)
    return ev


def _is_404(e: Exception) -> bool:
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404


def market(ticker: str) -> dict:
    """Market detail, from the live tier or (settled before the cutoff) the historical tier."""
    with _http.client() as c:
        try:
            return _get(c, f"/markets/{ticker}")["market"]
        except httpx.HTTPStatusError as e:
            if not _is_404(e):
                raise
            return _get(c, f"/historical/markets/{ticker}")["market"]


def markets(ev: dict) -> pd.DataFrame:
    """One row per market of an event dict (from `event`/`events`), numeric fields parsed."""
    rows = []
    for m in ev.get("markets") or []:
        rows.append({
            "event": ev["event_ticker"], "series": ev.get("series_ticker"), "ticker": m["ticker"],
            "title": m.get("title"), "label": m.get("yes_sub_title") or m.get("subtitle") or m["ticker"],
            "status": m.get("status"), "result": m.get("result") or None,
            "rules_primary": m.get("rules_primary") or "", "rules_secondary": m.get("rules_secondary") or "",
            "yes_bid": num(m.get("yes_bid_dollars")), "yes_ask": num(m.get("yes_ask_dollars")),
            "last": num(m.get("last_price_dollars")), "volume": num(m.get("volume_fp")),
            "open_interest": num(m.get("open_interest_fp")),
            "open_time": pd.Timestamp(m["open_time"]) if m.get("open_time") else pd.NaT,
            "close_time": pd.Timestamp(m["close_time"]) if m.get("close_time") else pd.NaT,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- books

def parse_book(ob: dict) -> dict:
    """API order book → {"yes_bids", "no_bids", "yes_asks", "no_asks"}: lists of (price, size), best first.

    Asks are implied: selling YES at p is the same order as a NO bid at 1 − p."""
    b = ob.get("orderbook_fp") or ob
    yb = sorted(((num(p), num(s)) for p, s in b.get("yes_dollars") or []), reverse=True)
    nb = sorted(((num(p), num(s)) for p, s in b.get("no_dollars") or []), reverse=True)
    return {"yes_bids": yb, "no_bids": nb,
            "yes_asks": [(round(1 - p, 4), s) for p, s in nb], "no_asks": [(round(1 - p, 4), s) for p, s in yb]}


def orderbook(ticker: str, depth: int = 0) -> dict:
    """Parsed book (see `parse_book`); depth 0 = all levels."""
    with _http.client() as c:
        return parse_book(_get(c, f"/markets/{ticker}/orderbook", {"depth": depth} if depth else None))


def orderbooks_raw(c: httpx.Client, tickers: list[str], chunk: int = 50) -> dict[str, dict]:
    """Raw books for many markets via the bulk endpoint, {ticker: orderbook_fp}."""
    out = {}
    for i in range(0, len(tickers), chunk):
        d = _get(c, "/markets/orderbooks", [("tickers", t) for t in tickers[i:i + chunk]])
        for b in d.get("orderbooks") or []:
            out[b["ticker"]] = b.get("orderbook_fp") or {}
    return out


MAX_SPREAD = 0.10       # wider quotes say little: fall back to the last trade


def prob_from_book(ob: dict, last: float | None = None) -> float | None:
    """Market-implied P(YES) from one order book: bid/ask midpoint when both sides are quoted and the spread
    is at most MAX_SPREAD, else `last` (e.g. the latest trade), else None."""
    b = parse_book(ob)
    if b["yes_bids"] and b["yes_asks"]:
        bid, ask = b["yes_bids"][0][0], b["yes_asks"][0][0]
        if ask - bid <= MAX_SPREAD + 1e-9:
            return (bid + ask) / 2
    return last


def prob_from_candles(candles: pd.DataFrame, at: pd.DatetimeIndex) -> pd.Series:
    """P(YES) as of each time in `at` from 1-min candles (`candlesticks` output): the latest candle that
    closed at or before it; its bid/ask midpoint if both sides are quoted and the spread is at most
    MAX_SPREAD, else its last trade price (carried forward)."""
    c = candles.sort_values("time").set_index("time")
    bid, ask = c["yes_bid"].where(c["yes_bid"] > 0), c["yes_ask"].where(c["yes_ask"] < 1)
    mid = ((bid + ask) / 2).where((ask - bid) <= MAX_SPREAD + 1e-9)
    p = mid.fillna(c["price"].ffill())
    return p.reindex(at.union(p.index)).ffill().reindex(at)


# ---------------------------------------------------------------------------------------------- history

def trades(ticker: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Every fill (one row per trade, taker side given), oldest first. Columns: time, price (YES dollars),
    count (contracts), taker_side ('yes'|'no'), trade_id. Fills before the trades cutoff come from
    /historical/trades, later ones from /markets/trades."""
    cut = cutoff()["trades_created_ts"]
    lo = pd.Timestamp(start) if start is not None else None
    hi = pd.Timestamp(end) if end is not None else None
    parts = []
    if lo is None or lo < cut:
        parts.append(("/historical/trades", lo, cut if hi is None or hi > cut else hi))
    if hi is None or hi > cut:
        parts.append(("/markets/trades", cut if lo is None or lo < cut else lo, hi))
    rows = []
    with _http.client() as c:
        for path, a, b in parts:
            p = {"ticker": ticker}
            if a is not None:
                p["min_ts"] = int(a.timestamp())
            if b is not None:
                p["max_ts"] = int(b.timestamp())
            rows += _paged(c, path, "trades", p, 1000)
    df = pd.DataFrame([{"time": pd.Timestamp(t["created_time"]), "price": num(t.get("yes_price_dollars")),
                        "count": num(t.get("count_fp")), "taker_side": t.get("taker_side"),
                        "trade_id": t.get("trade_id")} for t in rows])
    if df.empty:
        return pd.DataFrame(columns=["time", "price", "count", "taker_side", "trade_id"])
    return df.drop_duplicates("trade_id").sort_values("time").reset_index(drop=True)


def _close(x: dict | None):
    """A candle field's close: live tier `close_dollars`, historical tier `close`."""
    x = x or {}
    return num(x.get("close_dollars", x.get("close")))


def candlesticks(series_ticker: str, ticker: str, start: pd.Timestamp, end: pd.Timestamp,
                 period_min: int = 1) -> pd.DataFrame:
    """OHLC candles (period 1, 60 or 1440 min), one row per period that had a quote or trade.
    `time` is the period END. yes_bid/yes_ask are the period-close best quotes; price is the last trade.
    Markets settled before the historical cutoff are served by /historical/markets/{ticker}/candlesticks."""
    out = []
    step = CANDLE_CHUNK * period_min
    path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
    with _http.client() as c:
        t = pd.Timestamp(start)
        while t < end:
            u = min(t + step, pd.Timestamp(end))
            p = {"start_ts": int(t.timestamp()), "end_ts": int(u.timestamp()), "period_interval": period_min}
            try:
                d = _get(c, path, p)
            except httpx.HTTPStatusError as e:
                if not _is_404(e) or path.startswith("/historical"):
                    raise
                path = f"/historical/markets/{ticker}/candlesticks"
                d = _get(c, path, p)
            out += d.get("candlesticks") or []
            t = u
    rows = [{"time": pd.Timestamp(k["end_period_ts"], unit="s", tz="UTC"),
             "yes_bid": _close(k.get("yes_bid")), "yes_ask": _close(k.get("yes_ask")), "price": _close(k.get("price")),
             "volume": num(k.get("volume_fp", k.get("volume"))),
             "open_interest": num(k.get("open_interest_fp", k.get("open_interest")))} for k in out]
    df = pd.DataFrame(rows, columns=["time", "yes_bid", "yes_ask", "price", "volume", "open_interest"])
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def download_event(event_ticker: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
                   with_trades: bool = True, resume: bool = True) -> Path:
    """Archive an event: event.json, markets.parquet, candles/<ticker>.parquet (1-min),
    trades/<ticker>.parquet. Window defaults to each market's open → close (+1 h)."""
    ev = event(event_ticker)
    out = KALSHI / event_ticker
    (out / "candles").mkdir(parents=True, exist_ok=True)
    (out / "trades").mkdir(parents=True, exist_ok=True)
    (out / "event.json").write_text(json.dumps(ev))
    mk = markets(ev)
    mk.to_parquet(out / "markets.parquet")
    now = pd.Timestamp.now(tz="UTC")
    for m in mk.itertuples():
        s = start if start is not None else m.open_time
        e = end if end is not None else min(m.close_time + pd.Timedelta(hours=1), now)
        cf, tf = out / "candles" / f"{m.ticker}.parquet", out / "trades" / f"{m.ticker}.parquet"
        if not (resume and cf.exists()):
            candlesticks(ev["series_ticker"], m.ticker, s, e).to_parquet(cf)
        if with_trades and not (resume and tf.exists()):
            trades(m.ticker, s, e).to_parquet(tf)
        print(m.ticker, flush=True)
    return out


# ---------------------------------------------------------------------------------------------- recording

async def _books_async(client: httpx.AsyncClient, tickers: list[str], chunk: int = 50) -> dict[str, dict]:
    out = {}
    for i in range(0, len(tickers), chunk):
        params = [("tickers", t) for t in tickers[i:i + chunk]]
        for attempt in range(6):
            try:
                r = await client.get(f"{API}/markets/orderbooks", params=params)
            except httpx.TransportError:
                await asyncio.sleep(_http._delay(None, attempt))
                continue
            if r.status_code in _http.RETRY_STATUS:
                await asyncio.sleep(_http._delay(r, attempt))
                continue
            if r.status_code < 400:
                for b in r.json().get("orderbooks") or []:
                    out[b["ticker"]] = b.get("orderbook_fp") or {}
            break
        await asyncio.sleep(MIN_INTERVAL)
    return out


def expand(tickers: list[str], events_: list[str] = ()) -> list[str]:
    """Market tickers plus every not-yet-settled market of the given event tickers."""
    out = list(dict.fromkeys(tickers))
    for e in events_:
        out += [m["ticker"] for m in event(e)["markets"]
                if m.get("status") in ("active", "open", "initialized", "inactive") and m["ticker"] not in out]
    return out


async def record(tag: str, tickers: list[str], interval: float = 30.0, until: datetime | None = None,
                 out_root: Path | None = None) -> None:
    """Poll Kalshi books for `tickers` every `interval` s; save on change into the `brunder snap` layout:
    <SNAP>/<tag>/kalshi_books/<UTC time>.json ({ticker: orderbook_fp}) + a line in <tag>/index.jsonl.
    Can run alongside `brunder snap <tag>` (same index format; appends are line-atomic)."""
    from .snap import SNAP
    out = (out_root or SNAP) / tag
    d = out / "kalshi_books"
    d.mkdir(parents=True, exist_ok=True)
    index = out / "index.jsonl"
    print(f"kalshi record {tag}: {len(tickers)} markets, every {interval:.0f}s until {until}", flush=True)
    last = None
    async with _http.aclient() as client:
        while until is None or datetime.now(timezone.utc) < until:
            t = datetime.now(timezone.utc)
            try:
                books = await _books_async(client, tickers)
                if books:
                    body = json.dumps(books, sort_keys=True).encode()
                    h = hashlib.sha256(body).hexdigest()
                    if h != last:
                        last = h
                        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.json"
                        f.write_bytes(body)
                        with index.open("a") as fh:
                            fh.write(json.dumps({"t": t.isoformat(), "key": "kalshi_books",
                                                 "file": str(f.relative_to(out)), "sha256": h,
                                                 "bytes": len(body), "gzip": False}) + "\n")
            except Exception as e:                                   # never stop on the night
                print(f"{t:%H:%M:%S} kalshi books error, continuing: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(max(0.0, interval - (datetime.now(timezone.utc) - t).total_seconds()))
