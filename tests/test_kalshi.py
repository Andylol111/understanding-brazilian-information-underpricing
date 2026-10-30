import asyncio
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from brunder import kalshi

BOOK = {"orderbook_fp": {"yes_dollars": [["0.0100", "500.00"], ["0.6200", "40.00"], ["0.6300", "102.50"]],
                         "no_dollars": [["0.3400", "393.00"], ["0.3600", "22.47"]]}}


def test_parse_book_implies_asks_from_the_other_sides_bids():
    b = kalshi.parse_book(BOOK)
    assert b["yes_bids"][0] == (0.63, 102.5)                           # best first
    assert b["no_bids"][0] == (0.36, 22.47)
    assert b["yes_asks"][0] == (0.64, 22.47)                           # 1 − best NO bid
    assert b["no_asks"][0] == (0.37, 102.5)                            # 1 − best YES bid
    assert [p for p, _ in b["yes_asks"]] == sorted(p for p, _ in b["yes_asks"])


def test_num_parses_dollar_and_fixed_point_strings():
    assert kalshi.num("0.6300") == 0.63 and kalshi.num("4000758.86") == 4000758.86
    assert pd.isna(kalshi.num(None)) and pd.isna(kalshi.num(""))


def test_candlesticks_chunks_requests_and_parses_close_quotes(monkeypatch):
    calls = []

    def fake_get(c, path, params=None):
        calls.append(params)
        t = params["end_ts"]
        return {"candlesticks": [{"end_period_ts": t, "volume_fp": "10.00", "open_interest_fp": "5.00",
                                  "price": {"close_dollars": "0.5000"},
                                  "yes_bid": {"close_dollars": "0.4900"}, "yes_ask": {"close_dollars": "0.5100"}}]}

    monkeypatch.setattr(kalshi, "_get", fake_get)
    s = pd.Timestamp("2026-10-25T00:00Z")
    df = kalshi.candlesticks("KXBRPRES", "KXBRPRES-26-LULA", s, s + pd.Timedelta(days=7))
    assert len(calls) == 3 and all(c["period_interval"] == 1 for c in calls)    # 3 + 3 + 1 days
    assert calls[0]["end_ts"] - calls[0]["start_ts"] == 3 * 86400
    assert list(df["yes_bid"]) == [0.49] * 3 and df["time"].is_monotonic_increasing


def test_trades_follow_the_cursor_and_dedupe(monkeypatch):
    pages = [{"trades": [{"trade_id": "b", "created_time": "2026-10-26T00:00:02Z", "yes_price_dollars": "0.9960",
                          "count_fp": "10.00", "taker_side": "no"}], "cursor": "next"},
             {"trades": [{"trade_id": "a", "created_time": "2026-10-26T00:00:01Z", "yes_price_dollars": "0.9950",
                          "count_fp": "5.00", "taker_side": "yes"},
                         {"trade_id": "b", "created_time": "2026-10-26T00:00:02Z", "yes_price_dollars": "0.9960",
                          "count_fp": "10.00", "taker_side": "no"}], "cursor": ""}]
    seen = []

    def fake_get(c, path, params=None):
        seen.append(params.get("cursor"))
        return pages[len(seen) - 1]

    monkeypatch.setattr(kalshi, "_get", fake_get)
    monkeypatch.setattr(kalshi, "_cutoff", {"trades_created_ts": pd.Timestamp("2026-08-29T00:00Z")})
    df = kalshi.trades("KXBRPRES-26-LULA", start=pd.Timestamp("2026-10-25T00:00Z"))
    assert seen == [None, "next"]
    assert list(df["trade_id"]) == ["a", "b"] and list(df["count"]) == [5.0, 10.0]


def test_trades_route_old_fills_to_the_historical_tier(monkeypatch):
    paths = []

    def fake_get(c, path, params=None):
        paths.append((path, params.get("min_ts"), params.get("max_ts")))
        return {"trades": [], "cursor": ""}

    monkeypatch.setattr(kalshi, "_get", fake_get)
    cut = pd.Timestamp("2026-08-29T00:00Z")
    monkeypatch.setattr(kalshi, "_cutoff", {"trades_created_ts": cut})
    kalshi.trades("T", pd.Timestamp("2026-08-01T00:00Z"), pd.Timestamp("2026-09-01T00:00Z"))
    assert [p for p, *_ in paths] == ["/historical/trades", "/markets/trades"]
    assert paths[0][2] == paths[1][1] == int(cut.timestamp())
    paths.clear()
    kalshi.trades("T", pd.Timestamp("2026-04-01T00:00Z"), pd.Timestamp("2026-04-02T00:00Z"))
    assert [p for p, *_ in paths] == ["/historical/trades"]


def test_historical_candles_use_the_short_field_names():
    assert kalshi._close({"close": "0.8500"}) == 0.85 and kalshi._close({"close_dollars": "0.4900"}) == 0.49


def test_markets_parses_an_event():
    ev = {"event_ticker": "E", "series_ticker": "S", "markets": [
        {"ticker": "E-A", "yes_sub_title": "A", "status": "active", "yes_bid_dollars": "0.40",
         "yes_ask_dollars": "0.42", "volume_fp": "12.50", "open_time": "2026-01-01T00:00:00Z",
         "close_time": "2026-11-04T00:00:00Z", "rules_primary": "If A wins…"}]}
    m = kalshi.markets(ev)
    assert m.loc[0, "label"] == "A" and m.loc[0, "yes_ask"] == 0.42 and m.loc[0, "volume"] == 12.5


def test_record_writes_snap_layout_only_on_change(monkeypatch, tmp_path):
    books = iter([{"K-1": {"yes_dollars": [["0.50", "1"]]}}, {"K-1": {"yes_dollars": [["0.50", "1"]]}},
                  {"K-1": {"yes_dollars": [["0.51", "1"]]}}])

    async def fake_books(client, tickers, chunk=50):
        try:
            return next(books)
        except StopIteration:
            return {}

    monkeypatch.setattr(kalshi, "_books_async", fake_books)
    until = datetime.now(timezone.utc) + timedelta(seconds=0.5)
    asyncio.run(kalshi.record("t", ["K-1"], interval=0.1, until=until, out_root=tmp_path))
    idx = [json.loads(l) for l in (tmp_path / "t" / "index.jsonl").read_text().splitlines()]
    assert [r["key"] for r in idx] == ["kalshi_books", "kalshi_books"]        # the repeat was skipped
    first = json.loads((tmp_path / "t" / idx[0]["file"]).read_text())
    assert first == {"K-1": {"yes_dollars": [["0.50", "1"]]}}


def test_record_polls_modelled_tickers_faster(monkeypatch, tmp_path):
    calls = []

    async def fake_books(client, tickers, chunk=50):
        calls.append(list(tickers))
        return {t: {"yes_dollars": [[f"0.{len(calls):02d}", "1"]]} for t in tickers}

    monkeypatch.setattr(kalshi, "_books_async", fake_books)
    monkeypatch.setattr(kalshi, "MIN_FAST", 0.1)
    until = datetime.now(timezone.utc) + timedelta(seconds=1.0)
    asyncio.run(kalshi.record("t", ["K-1", "K-2", "K-3"], interval=0.5, until=until, out_root=tmp_path,
                              fast_tickers=["K-2", "X-9"], fast_interval=0.1))
    full = [c for c in calls if len(c) == 3]
    assert 2 <= len(full) <= 3 and len(calls) - len(full) >= 4 and all(c == ["K-2"] for c in calls if len(c) != 3)
    idx = [json.loads(l) for l in (tmp_path / "t" / "index.jsonl").read_text().splitlines()]
    last = json.loads((tmp_path / "t" / idx[-1]["file"]).read_text())
    assert set(last) == {"K-1", "K-2", "K-3"} and "fetched" in idx[-1] and any(r["fast"] for r in idx)


def test_pacing_spaces_requests(monkeypatch):
    t = [0.0]
    monkeypatch.setattr(kalshi.time, "monotonic", lambda: t[0])
    slept = []
    monkeypatch.setattr(kalshi.time, "sleep", lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    kalshi._last = 0.0
    t[0] = 1.0
    kalshi._pace()
    kalshi._pace()
    assert slept == [pytest.approx(kalshi.MIN_INTERVAL)]


def test_prob_from_book_is_the_mid_when_tight_else_last():
    tight = {"orderbook_fp": {"yes_dollars": [["0.6200", "10"]], "no_dollars": [["0.3600", "10"]]}}
    assert kalshi.prob_from_book(tight) == pytest.approx(0.63)                  # bid 0.62, ask 0.64
    wide = {"orderbook_fp": {"yes_dollars": [["0.4000", "10"]], "no_dollars": [["0.4000", "10"]]}}
    assert kalshi.prob_from_book(wide, last=0.5) == 0.5                          # 20¢ spread → last trade
    assert kalshi.prob_from_book({"orderbook_fp": {}}) is None


def test_prob_from_candles_uses_the_latest_closed_candle():
    t0 = pd.Timestamp("2026-10-25T21:00Z")
    c = pd.DataFrame({"time": [t0, t0 + pd.Timedelta("2min")], "yes_bid": [0.40, 0.10], "yes_ask": [0.42, 0.90],
                      "price": [0.41, 0.55], "volume": [1.0, 1.0], "open_interest": [1.0, 1.0]})
    at = pd.date_range(t0 - pd.Timedelta("1min"), t0 + pd.Timedelta("3min"), freq="min")
    p = kalshi.prob_from_candles(c, at)
    assert pd.isna(p.iloc[0])                                                    # before the first candle
    assert p.iloc[1] == pytest.approx(0.41) and p.iloc[2] == pytest.approx(0.41)  # mid, carried forward
    assert p.iloc[3] == pytest.approx(0.55) and p.iloc[4] == pytest.approx(0.55)  # 80¢ spread → last trade
