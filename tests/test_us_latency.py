"""analysis/us_latency.py: model vs market threshold crossings, paired to the second."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_latency as ul  # noqa: E402

T = 1793000000        # a whole second


def test_side_crossings_first_point_sets_the_side():
    pts = [(T, 0.40), (T + 60, 0.52), (T + 120, 0.49), (T + 180, 0.97)]
    got = [(c["level"], c["dir"], c["t"]) for c in ul.side_crossings(pts)]
    assert got == [(0.5, "up", T + 60), (0.5, "down", T + 120), (0.5, "up", T + 180), (0.75, "up", T + 180),
                   (0.9, "up", T + 180), (0.95, "up", T + 180)]


def test_market_crossings_flip_yes_to_side_a():
    ws = [{"slug": "e/m", "level": 0.25, "dir": "up", "ts": (T + 5) * 1000 + 321, "t": "x", "price": 0.26},
          {"slug": "other", "level": 0.5, "dir": "up", "ts": 1, "t": "x"}]
    a = ul.market_crossings(ws, "e/m", "a")
    b = ul.market_crossings(ws, "e/m", "b")
    assert [(x["level"], x["dir"], x["t"]) for x in a] == [(0.25, "up", T + 5.321)]
    assert [(x["level"], x["dir"]) for x in b] == [(0.75, "down")]


def test_pair_nearest_unused_within_window():
    model = [{"level": 0.5, "dir": "up", "t": T}, {"level": 0.5, "dir": "down", "t": T + 600},
             {"level": 0.9, "dir": "up", "t": T + 700}]
    market = [{"level": 0.5, "dir": "up", "t": T + 45.9}, {"level": 0.5, "dir": "up", "t": T + 90},
              {"level": 0.5, "dir": "down", "t": T + 550}, {"level": 0.9, "dir": "up", "t": T + 700 + 4000}]
    p = ul.pair(model, market)
    assert [x["lead_s"] for x in p["crossings"]] == [45, -50, None]       # the market's crossing came after / before ours
    assert p["market_only"] == 2 and p["crossings"][0]["model_t"] == ul._sec(T)
    s = ul.summary([45, -50, 10, 20])
    assert s["n"] == 4 and s["model_first"] == 3 and s["median_s"] == 15


def test_block_uses_update_log_seconds_else_minute_rows(tmp_path):
    cfg = {"election": {"race_id": "xx-sen"}, "markets": {"polymarket": {"event": "e", "market": "m", "yes_is": "a"}}}
    iso = lambda t: ul._sec(t)   # noqa: E731
    rows = [{"t": iso(T), "P": 0.40}, {"t": iso(T + 60), "P": 0.62}, {"t": iso(T + 120), "P": 0.66}]
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ws_crossings.jsonl").write_text(json.dumps(
        {"slug": "e/m", "level": 0.5, "dir": "up", "ts": (T + 100) * 1000, "t": iso(T + 100), "price": 0.51}) + "\n" + "not json{\n")
    r = ul.block({"xx-sen": rows}, [cfg], ws)
    pp = r["races"]["xx-sen"]["P"]
    assert r["status"] == "ok" and pp["resolution"] == "minute" and pp["model_crossings"] == 1
    assert pp["crossings"][0]["model_t"] == iso(T + 60) and pp["crossings"][0]["lead_s"] == 40
    upd = tmp_path / "updates.jsonl"
    upd.write_text(json.dumps({"kind": "pass", "snaps": []}) + "\n" + json.dumps({"kind": "race", "race": "xx-sen", "wall": "2026-10-26T07:33:20.500000+00:00", "row_t": iso(T + 60),
                               "cross": [{"v": "P", "level": 0.5, "dir": "up"}]}) + "\n")
    r = ul.block({"xx-sen": rows}, [cfg], ws, upd)
    pp = r["races"]["xx-sen"]["P"]
    assert pp["resolution"] == "second" and pp["crossings"][0]["model_t"] == "2026-10-26T07:33:20Z"
    assert r["pooled_P"]["n"] == 1
    assert ul.block({}, [cfg], tmp_path / "nowhere")["status"] == "none"
