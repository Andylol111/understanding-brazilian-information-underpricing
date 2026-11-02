"""Market mapping of the added-source races: configs/us2026_extra/markets.json (analysis/us_extra_markets.py).

race_id -> {"pm": {event, slug, token, yes_is, question}, "kalshi": {ticker, series, event, yes_is, question}};
yes_is "a" = the YES contract pays if config side a (the Democrat) wins. The added recorder (brunder.us_extra_rec)
saves the books of these markets in the registered recorders' formats under its own archive keys
`polymarket_books` ({"<event>/<slug>": CLOB book}) and `kalshi_books` ({ticker: orderbook_fp}), so the unchanged
brunder.us exporter turns them into the rows' `pm` / `kalshi` (chance of side a) exactly as for the registered races.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .paths import ROOT

PATH = ROOT / "configs" / "us2026_extra" / "markets.json"
CLOB_BOOKS = "https://clob.polymarket.com/books"
KALSHI_BOOKS = "https://api.elections.kalshi.com/trade-api/v2/markets/orderbooks"
PM_KEY, KALSHI_KEY = "polymarket_books", "kalshi_books"
INTERVAL = 60.0


@lru_cache(maxsize=4)
def load(path: Path = PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}


def pm_tokens(path: Path = PATH) -> dict[str, str]:
    """YES token -> "<event>/<slug>" (the key brunder.us.pm_prices / _pm_market use)."""
    return {v["pm"]["token"]: f"{v['pm']['event']}/{v['pm']['slug']}" for v in load(path).values() if v.get("pm")}


def kalshi_tickers(path: Path = PATH) -> list[str]:
    return sorted({v["kalshi"]["ticker"] for v in load(path).values() if v.get("kalshi")})


def kalshi_markets(cfgs: list[dict], path: Path = PATH) -> dict[str, dict]:
    """brunder.us.kalshi_markets layout for the added races: {race_id: {ticker, series, yes_is, question}}."""
    m = load(path)
    out = {}
    for c in cfgs:
        k = (m.get(c["election"]["race_id"]) or {}).get("kalshi")
        if k:
            out[c["election"]["race_id"]] = {"ticker": k["ticker"], "series": k.get("series", ""),
                                             "yes_is": k.get("yes_is", "a"), "question": k.get("question", "")}
    return out


def race_tokens(cfgs: list[dict], path: Path = PATH) -> dict[str, str]:
    """brunder.us.pm_tokens layout: {race_id: YES token}."""
    m = load(path)
    return {c["election"]["race_id"]: m[c["election"]["race_id"]]["pm"]["token"]
            for c in cfgs if (m.get(c["election"]["race_id"]) or {}).get("pm")}
