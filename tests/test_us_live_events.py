"""brunder.us.run_live: event-driven passes gated by the recorders' poll markers; index tail; update log."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from brunder import snap, us


def test_index_tail_returns_complete_lines_once(tmp_path):
    f = tmp_path / "index.jsonl"
    tail = us.IndexTail(f)
    assert tail.read() == [] and not tail.changed()
    f.write_text('{"t": "a"}\n{"t": "b"}\n{"t": "c"')               # last line still being appended
    assert tail.changed() and [x["t"] for x in tail.read()] == ["a", "b"]
    assert tail.changed()                                            # the partial line is still pending
    with f.open("a") as fh:
        fh.write('}\n')
    assert [x["t"] for x in tail.read()] == ["c"] and not tail.changed() and tail.read() == []


def test_settled_until_is_earliest_fresh_marker(tmp_path):
    now = pd.Timestamp("2026-11-04T02:00:00Z")
    snap.mark(tmp_path, "snap", datetime(2026, 11, 4, 2, 0, 20, tzinfo=timezone.utc), 10)
    snap.mark(tmp_path, "kalshi", datetime(2026, 11, 4, 2, 0, 5, tzinfo=timezone.utc), 10)
    m = us.poll_markers(tmp_path)
    assert set(m) == {"snap", "kalshi"} and not list((tmp_path / ".poll").glob(".*.tmp"))
    for d in m.values():
        d["done"] = "2026-11-04T01:59:58+00:00"
    assert us.settled_until(m, now) == pd.Timestamp("2026-11-04T02:00:05Z")
    m["kalshi"]["done"] = "2026-11-04T01:58:00+00:00"                # 120 s > 3 × 10 + 60: a stopped recorder
    assert us.settled_until(m, now) == pd.Timestamp("2026-11-04T02:00:20Z")
    assert us.settled_until({}, now) is None


class FakeExporter:
    """Records each update's `until`; one row per minute up to min(until, latest snapshot + 1 min)."""

    def __init__(self, cfg_paths, snap_root):
        self.arc = us.Archive(snap_root)
        self.step = pd.Timedelta("1min")
        self.timeline = {"xx-sen": []}
        self.errors = {}
        self.calls: list[pd.Timestamp] = []
        FakeExporter.last = self

    def update(self, until):
        self.calls.append(until)
        self.arc.refresh()
        ts = self.arc.times()
        if not ts:
            return
        end = min(until, ts[-1] + self.step)
        rows = self.timeline["xx-sen"]
        start = pd.Timestamp(rows[-1]["t"]) + self.step if rows else ts[0].ceil(self.step)
        for t in pd.date_range(start, end, freq=self.step):
            p = 0.4 if t < FakeExporter.switch else 0.6
            rows.append({"t": us._iso(t), "counted": 0.1, "P": p, "P_v3": p, "pm": 0.5})

    def write(self, out):
        (Path(out) / "w").write_text(str(len(self.timeline["xx-sen"])))


def _save(root: Path, t: datetime, key: str = "k"):
    (root / key).mkdir(parents=True, exist_ok=True)
    (root / key / f"{t:%Y%m%dT%H%M%S.%fZ}.json").write_text("{}")
    with (root / "index.jsonl").open("a") as fh:
        fh.write(json.dumps({"t": t.isoformat(), "key": key, "fetched": (t + timedelta(seconds=1)).isoformat(),
                             "last_modified": "Wed, 04 Nov 2026 02:00:00 GMT"}) + "\n")


def test_run_live_waits_for_markers_and_logs_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(us, "Exporter", FakeExporter)
    monkeypatch.setattr(us, "LIVE_POLL", 0.02)
    root, out, upd = tmp_path / "snap", tmp_path / "out", tmp_path / "upd.jsonl"
    root.mkdir()
    out.mkdir()
    base = pd.Timestamp.now(tz="UTC").floor("1min") - pd.Timedelta(hours=2)   # back-dated: minutes are in the past
    t0 = (base + pd.Timedelta(seconds=30)).to_pydatetime()
    FakeExporter.switch = base + pd.Timedelta(minutes=3)
    _save(root, t0)
    snap.mark(root, "snap", t0 + timedelta(seconds=1), 10)            # snapshots before 02:00:31 are on disk

    async def recorder():
        for k in range(1, 5):                                         # one more minute every 0.3 s
            await asyncio.sleep(0.3)
            t = t0 + timedelta(minutes=k)
            _save(root, t)
            snap.mark(root, "snap", t + timedelta(seconds=1), 10)

    async def main():
        stop = datetime.now(timezone.utc) + timedelta(seconds=2.0)
        await asyncio.gather(recorder(), us.run_live([], out, root, interval=30, stop_at=stop, updates=upd))
    asyncio.run(main())
    ex = FakeExporter.last
    rows = [r["t"] for r in ex.timeline["xx-sen"]]
    # each minute row only after the snapshot of that minute exists; never ahead of the marker
    assert rows == [us._iso(base + pd.Timedelta(minutes=i)) for i in range(1, 5)]   # ungated: up to base + 5 min
    assert len(ex.calls) <= 8                                         # event-driven, not a busy loop
    lines = [json.loads(x) for x in upd.read_text().splitlines()]
    passes = [x for x in lines if x["kind"] == "pass"]
    shown = [s for p in passes for s in p["snaps"]]
    assert len(shown) >= 4 and all(s[2] and s[3] for s in shown)      # fetched + Last-Modified carried through
    races = [x for x in lines if x["kind"] == "race"]
    cross = [c for x in races for c in x["cross"]]
    assert {(c["v"], c["level"], c["dir"], c["row_t"]) for c in cross} == {
        ("P", 0.5, "up", us._iso(FakeExporter.switch)), ("P_v3", 0.5, "up", us._iso(FakeExporter.switch))}
    assert all("wall" in x and x["race"] == "xx-sen" for x in races)


def test_feed_status_marks_stale_feeds(tmp_path):
    now = pd.Timestamp("2026-11-04T02:00:00Z")
    snap.mark(tmp_path, "snap", now.to_pydatetime(), 30, {
        "oh": {"interval": 60, "last_ok": "2026-11-04T01:56:00+00:00", "last_status": 403, "fails": 3},
        "ga": {"interval": 30, "last_ok": "2026-11-04T01:59:40+00:00", "last_status": 304, "fails": 0},
        "nc": {"interval": 30, "last_ok": None, "last_status": 404, "fails": 0}})
    snap.mark(tmp_path, "kalshi", now.to_pydatetime(), 10, {"kalshi_books": {"interval": 30, "last_ok":
                                                                              "2026-11-04T01:59:55+00:00"}})
    st = {x["key"]: x for x in us.feed_status(us.poll_markers(tmp_path), now)}
    assert [k for k in st] == ["ga", "kalshi_books", "nc", "oh"]
    assert st["oh"]["stale"] and st["oh"]["last_status"] == 403 and st["oh"]["last_ok_utc"] == "2026-11-04T01:56:00Z"
    assert not st["ga"]["stale"] and not st["kalshi_books"]["stale"] and st["nc"]["stale"]
    assert st["kalshi_books"]["recorder"] == "kalshi" and st["oh"]["interval_s"] == 60
