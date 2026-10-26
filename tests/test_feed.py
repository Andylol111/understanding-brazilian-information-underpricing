import asyncio
import json

import httpx

from brunder import feed

URL = "https://resultados.tse.jus.br/oficial/ele2026/6258/dados/sp/sp-c0001-e006258-u.json"


def _body(idg, hg):
    return json.dumps({"idg": str(idg), "dg": "25/10/2026", "hg": hg}).encode()


def test_newest_copy_across_edges_wins_and_reports_its_age(tmp_path):
    copies = {"10.0.0.1": _body(5, "20:00:00"), "10.0.0.2": _body(7, "20:00:40")}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=copies[req.url.host],
                                                               headers={"etag": req.url.host}))

    async def run():
        async with feed.FastFeed(edges=list(copies), transport=transport, log=tmp_path / "lat.jsonl") as f:
            return await f.get(URL, unit="sp")

    c = asyncio.run(run())
    assert c.idg == 7 and c.edge == "10.0.0.2"
    assert c.generated_utc.isoformat().startswith("2026-10-25T23:00:40")       # 20:00:40 BRT
    rec = json.loads((tmp_path / "lat.jsonl").read_text().splitlines()[0])
    assert rec["edges_ok"] == 2 and rec["idg_spread"] == 2


def test_fanout_rotates_so_every_edge_is_sampled():
    seen = []
    transport = httpx.MockTransport(lambda req: (seen.append(req.url.host), httpx.Response(200, content=_body(1, "20:00:00")))[1])
    edges = [f"10.0.0.{i}" for i in range(1, 6)]

    async def run():
        async with feed.FastFeed(edges=edges, fanout=2, transport=transport) as f:
            for _ in range(3):
                await f.get(URL, "sp")

    asyncio.run(run())
    assert len(seen) == 6 and set(seen) == set(edges)          # 3 polls × 2 edges cover all 5


def test_429_halves_the_rate_and_pauses_instead_of_hammering_other_edges():
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, content=_body(3, "19:00:00"))

    async def run():
        async with feed.FastFeed(edges=["10.0.0.1", "10.0.0.2"], rps=20, cooldown_s=0.05,
                                 transport=httpx.MockTransport(handler)) as f:
            try:
                await f.get_one(URL, "sp", edge="10.0.0.1")
                raised = False
            except feed.FeedUnavailable:
                raised = True
            c = await f.get_one(URL, "sp", edge="10.0.0.1")
            return raised, c, f.limiter

    raised, c, lim = asyncio.run(run())
    assert raised and calls[:1] == ["10.0.0.1"] and len(calls) == 2   # no fallback burst after the 429
    assert c.idg == 3 and lim.throttles == 1 and lim.rate < 20


def test_not_modified_reuses_the_edge_copy():
    def handler(req):
        if req.headers.get("If-None-Match") == "v1":
            return httpx.Response(304)
        return httpx.Response(200, content=_body(3, "19:00:00"), headers={"etag": "v1"})

    async def run():
        async with feed.FastFeed(edges=["10.0.0.1"], transport=httpx.MockTransport(handler)) as f:
            return await f.get(URL, "sp"), await f.get(URL, "sp")

    a, b = asyncio.run(run())
    assert a.idg == b.idg == 3 and a.body == b.body
