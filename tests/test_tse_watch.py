import asyncio
import json

import httpx

from brunder import feed, tse_live

BASE = "/oficial/ele2026/6258"


def _feed(fail_counts: dict[str, int]):
    """Fake TSE: one state (ac) with two municipalities; the count is already final.
    Paths in fail_counts return 503 that many times before succeeding."""
    cm = {"abr": [{"cd": "AC", "ds": "ACRE", "mu": [{"cd": "00001", "nm": "A"}, {"cd": "00002", "nm": "B"}]}]}
    unit = lambda idg: {"idg": idg, "tf": "s", "s": {"pst": "100,00"}}
    files = {
        f"{BASE}/config/mun-e006258-cm.json": cm,
        f"{BASE}/dados/br/br-c0001-e006258-u.json": unit("1"),
        f"{BASE}/dados/ac/ac-c0001-e006258-u.json": unit("1"),
        f"{BASE}/dados/ac/ac-e006258-ab.json": {"abr": [{"tpabr": "mun", "cdabr": "00001", "s": {"st": "10"}},
                                                         {"tpabr": "mun", "cdabr": "00002", "s": {"st": "20"}}]},
        f"{BASE}/dados/ac/ac00001-c0001-e006258-u.json": unit("m1"),
        f"{BASE}/dados/ac/ac00002-c0001-e006258-u.json": unit("m2"),
    }
    left = dict(fail_counts)

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if left.get(p, 0) > 0:
            left[p] -= 1
            return httpx.Response(503)
        return httpx.Response(200, json=files[p]) if p in files else httpx.Response(404)

    return handler


def test_watch_survives_failures_and_retries_the_failed_municipality(tmp_path, monkeypatch):
    muni2 = f"{BASE}/dados/ac/ac00002-c0001-e006258-u.json"
    transport = httpx.MockTransport(_feed({muni2: 10}))          # fails every retry of the first poll
    real = feed.FastFeed
    monkeypatch.setattr(feed, "FastFeed", lambda **kw: real(edges=["10.0.0.1"], transport=transport, **kw))
    monkeypatch.setattr(feed, "_bench_s", lambda strikes: 0)
    monkeypatch.setattr(tse_live, "BASE", "https://x/oficial")
    monkeypatch.setattr(tse_live, "TSE_LIVE", tmp_path)

    asyncio.run(tse_live.watch("ele2026", 6258, interval=0, concurrency=4))

    saved = {p.name for p in tmp_path.rglob("ac/*.json")}
    assert {"u.json", "ab.json", "00001.json", "00002.json"} <= saved   # the failed file arrived on a later poll
    snaps = sorted(p for p in (tmp_path / "ele2026" / "e6258" / "c1").iterdir())
    assert len(snaps) >= 2
    assert json.loads((snaps[-1] / "ac" / "00002.json").read_text())["idg"] == "m2"
