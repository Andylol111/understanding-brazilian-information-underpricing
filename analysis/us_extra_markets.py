"""Market mapping of the added-source races (configs/us2026_extra/*_v1.toml) -> configs/us2026_extra/markets.json.

For each race: the Polymarket market whose YES is the Democratic side (Gamma event -> market; YES token = first
clobTokenId) and the Kalshi market whose YES is the Democratic side (statewide: the ticker of site/data/us2026/
overview.json; House: the event of site/data/us2026/house/markets.json, market chosen by its yes_sub_title / ticker
suffix). yes_is "a" = config side a (the Democrat). Each mapping is printed with the market title and its current
price (Polymarket book midpoint, Kalshi yes bid/ask midpoint) as a check. Honest User-Agent, >= 1.1 s per host.

    uv run --frozen python analysis/us_extra_markets.py
"""
from __future__ import annotations

import json
import re
import time
import tomllib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "configs" / "us2026_extra" / "markets.json"
UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_last: dict[str, float] = {}


def get(c: httpx.Client, url: str, **params):
    host = url.split("/")[2]
    w = _last.get(host, 0) + 1.1 - time.monotonic()
    if w > 0:
        time.sleep(w)
    _last[host] = time.monotonic()
    r = c.get(url, params=params or None)
    r.raise_for_status()
    return r.json()


def _dem_market(markets: list[dict], dem_name: str) -> dict | None:
    """The open market whose YES = the Democratic side: 'Democratic Party/Democrats win' or the Democrat's name."""
    last = dem_name.split()[-1].lower()
    for m in markets:
        q = (m.get("question") or "").lower()
        if m.get("closed") or not m.get("active", True):
            continue
        if re.search(r"democrat", q) or (last and last in q and "republican" not in q):
            return m
    return None


def build() -> dict:
    ov = json.loads((ROOT / "site/data/us2026/overview.json").read_text())["offices"]
    state_races = {r["race_key"]: r for off in ("senate", "governor") for r in ov[off]["races"]}
    house = json.loads((ROOT / "site/data/us2026/house/markets.json").read_text())["districts"]
    out = {}
    with httpx.Client(headers=UA, timeout=30) as c:
        for f in sorted((ROOT / "configs" / "us2026_extra").glob("*_v1.toml")):
            cfg = tomllib.loads(f.read_text())
            E = cfg["election"]
            rid, dem = E["race_id"], E["a"]["name"]
            if rid.startswith("house:"):
                h = house[rid[6:]]
                pm_event, k_event, k_ticker = (h.get("pm") or {}).get("slug"), (h.get("kalshi") or {}).get("event"), None
            else:
                r = state_races[rid]
                pm_event, k_event = (r.get("pm") or {}).get("event"), None
                k_ticker = (r.get("kalshi") or {}).get("ticker") if (r.get("kalshi") or {}).get("yes_is") == "dem_side" else None
            entry: dict = {}
            if pm_event:
                ev = get(c, f"{GAMMA}/events", slug=pm_event)[0]
                m = _dem_market(ev["markets"], dem)
                if m:
                    tok = json.loads(m["clobTokenIds"])[0] if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"][0]
                    book = get(c, f"{CLOB}/book", token_id=tok)
                    bb = max((float(x["price"]) for x in book.get("bids") or []), default=None)
                    ba = min((float(x["price"]) for x in book.get("asks") or []), default=None)
                    entry["pm"] = {"event": pm_event, "slug": m["slug"], "token": tok, "yes_is": "a",
                                   "question": m["question"]}
                    print(f"{rid:12} PM  {m['question']!r} bid {bb} ask {ba}")
            if k_event and not k_ticker:
                kev = get(c, f"{KALSHI}/events/{k_event}", with_nested_markets="true")["event"]
                for km in kev.get("markets") or []:
                    sub = (km.get("yes_sub_title") or "").lower()
                    if km["ticker"].endswith("-D") or "democrat" in sub:
                        k_ticker = km["ticker"]
                        break
            if k_ticker:
                km = get(c, f"{KALSHI}/markets/{k_ticker}")["market"]
                entry["kalshi"] = {"ticker": k_ticker, "series": km.get("event_ticker", "").split("-")[0],
                                   "event": km.get("event_ticker"), "yes_is": "a",
                                   "question": f"{km.get('title')} — YES: {km.get('yes_sub_title')}"}
                print(f"{rid:12} KAL {km.get('title')!r} YES={km.get('yes_sub_title')!r} "
                      f"bid {km.get('yes_bid_dollars', km.get('yes_bid'))} ask {km.get('yes_ask_dollars', km.get('yes_ask'))}")
            out[rid] = entry
    return out


def main() -> None:
    m = build()
    OUT.write_text(json.dumps({"_note": "added-source races: Polymarket / Kalshi market whose YES is config side a "
                               "(the Democrat); analysis/us_extra_markets.py, looked up 2026-11-02", **m}, indent=1) + "\n")
    print(OUT, len(m), "races;", sum("pm" in v for v in m.values()), "PM,", sum("kalshi" in v for v in m.values()), "Kalshi")


if __name__ == "__main__":
    main()
