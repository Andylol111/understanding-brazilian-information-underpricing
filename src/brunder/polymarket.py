"""Polymarket public data: events/markets (Gamma), price history + books (CLOB), trades (data-api).

No auth needed. Prices are the YES-token price in [0, 1] = market-implied probability
(before fees and favourite-longshot adjustments).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from . import _http
from .paths import POLYMARKET

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"


def search_events(query: str, limit: int = 50) -> pd.DataFrame:
    with _http.client() as c:
        d = _http.get_json(c, f"{GAMMA}/public-search", {"q": query, "limit_per_type": limit})
    return pd.DataFrame([
        {"slug": e["slug"], "title": e["title"], "end": e.get("endDate"), "volume": float(e.get("volume") or 0),
         "closed": e.get("closed")}
        for e in d.get("events", [])
    ])


def event(slug: str) -> dict:
    with _http.client() as c:
        res = _http.get_json(c, f"{GAMMA}/events", {"slug": slug})
    if not res:
        raise KeyError(f"no Polymarket event with slug {slug!r}")
    return res[0]


def markets(ev: dict) -> pd.DataFrame:
    """One row per market in the event, with YES/NO token ids. Drops never-traded placeholders."""
    rows = []
    for m in ev["markets"]:
        if not m.get("clobTokenIds"):
            continue
        toks = json.loads(m["clobTokenIds"])
        outs = json.loads(m["outcomes"])
        prices = json.loads(m["outcomePrices"]) if m.get("outcomePrices") else [None] * len(outs)
        if float(m.get("volume") or 0) == 0:
            continue
        rows.append({
            "event": ev["slug"], "slug": m["slug"], "question": m["question"],
            "label": m.get("groupItemTitle") or m["question"],
            "condition_id": m["conditionId"],
            "yes_token": toks[outs.index("Yes")] if "Yes" in outs else toks[0],
            "no_token": toks[outs.index("No")] if "No" in outs else toks[1],
            "outcomes": outs,
            "last_prices": [float(p) for p in prices if p is not None],
            "volume": float(m.get("volume") or 0),
            "created": pd.Timestamp(m.get("createdAt") or m.get("startDate")),
            "closed": m["closed"],
            "closed_time": pd.Timestamp(m["closedTime"]) if m.get("closedTime") else pd.NaT,
        })
    return pd.DataFrame(rows)


def price_history(token_id: str, start: pd.Timestamp, end: pd.Timestamp, fidelity_min: int = 1,
                  chunk: pd.Timedelta = pd.Timedelta(days=3)) -> pd.DataFrame:
    """CLOB mid/last price series at `fidelity_min`-minute resolution, fetched in windows."""
    out = []
    with _http.client() as c:
        t = start
        while t < end:
            u = min(t + chunk, end)
            d = _http.get_json(c, f"{CLOB}/prices-history", {
                "market": token_id, "startTs": int(t.timestamp()), "endTs": int(u.timestamp()),
                "fidelity": fidelity_min})
            out += d.get("history", [])
            t = u
    df = pd.DataFrame(out, columns=["t", "p"]).drop_duplicates("t").sort_values("t")
    df["time"] = pd.to_datetime(df.pop("t"), unit="s", utc=True)
    return df.rename(columns={"p": "price"}).reset_index(drop=True)


def trades(condition_id: str, page: int = 500, sleep: float = 0.15, taker_only: bool = False) -> pd.DataFrame:
    """Every fill in a market, newest→oldest, paging by `end` timestamp (offset is capped server-side).

    taker_only=False returns one row per participant per fill (taker + each maker), so summed
    notional double-counts volume; taker_only=True returns only the aggressor rows. Use
    `tag_takers` to mark taker rows in the full set (signed order flow must use takers only)."""
    seen, rows = set(), []
    end, offset = None, 0
    with _http.client() as c:
        while True:
            params = {"market": condition_id, "limit": page, "offset": offset,
                      "takerOnly": "true" if taker_only else "false"}
            if end is not None:
                params["end"] = end
            batch = _http.get_json(c, f"{DATA_API}/trades", params)
            new = 0
            for t in batch:
                k = (t["transactionHash"], t["asset"], t["proxyWallet"], t["side"], t["size"], t["price"], t["timestamp"])
                if k not in seen:
                    seen.add(k)
                    rows.append(t)
                    new += 1
            if len(batch) < page:
                break
            min_ts = min(t["timestamp"] for t in batch)
            if min_ts == end or new == 0:      # a full page inside one second: step offset instead
                offset += page
            else:
                end, offset = min_ts, 0
            time.sleep(sleep)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    keep = ["time", "proxyWallet", "side", "asset", "outcome", "outcomeIndex", "price", "size",
            "transactionHash", "conditionId", "slug", "name", "pseudonym"]
    return df[keep].sort_values("time").reset_index(drop=True)


_FILL_KEY = ["transactionHash", "proxyWallet", "asset", "side", "size", "price"]


def tag_takers(all_fills: pd.DataFrame, taker_fills: pd.DataFrame) -> pd.DataFrame:
    """Adds boolean `is_taker` to the full fill set by matching the taker-only download."""
    k = taker_fills[_FILL_KEY].drop_duplicates().assign(is_taker=True)
    out = all_fills.merge(k, on=_FILL_KEY, how="left")
    out["is_taker"] = out["is_taker"].fillna(False).astype(bool)
    return out


def book(token_id: str) -> dict:
    with _http.client() as c:
        return _http.get_json(c, f"{CLOB}/book", {"token_id": token_id})


def download_event(slug: str, fidelity_min: int = 1, with_trades: bool = True, resume: bool = True) -> Path:
    """Archive an event: markets.parquet, prices/<slug>.parquet (YES token),
    trades/<slug>.parquet (every fill, both sides, with `is_taker`).

    resume=True skips files that already exist (closed markets don't change)."""
    ev = event(slug)
    out = POLYMARKET / slug
    (out / "prices").mkdir(parents=True, exist_ok=True)
    (out / "trades").mkdir(parents=True, exist_ok=True)
    (out / "event.json").write_text(json.dumps(ev))
    mk = markets(ev)
    mk.to_parquet(out / "markets.parquet")
    now = pd.Timestamp.now(tz="UTC")
    for m in mk.itertuples():
        pf, tf = out / "prices" / f"{m.slug}.parquet", out / "trades" / f"{m.slug}.parquet"
        if not (resume and pf.exists()):
            start = m.created if m.created.tzinfo else m.created.tz_localize("UTC")
            end = m.closed_time if pd.notna(m.closed_time) else now
            price_history(m.yes_token, start, end + pd.Timedelta(hours=1), fidelity_min).to_parquet(pf)
        if with_trades:
            have = pd.read_parquet(tf) if resume and tf.exists() else None
            if have is None or "is_taker" not in have:
                fills = have if have is not None else trades(m.condition_id)
                tag_takers(fills, trades(m.condition_id, taker_only=True)).to_parquet(tf)
        print(m.slug, flush=True)
    return out
