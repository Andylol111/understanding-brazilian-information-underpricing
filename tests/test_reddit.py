import json
from datetime import datetime, timezone

import httpx
import pytest

from brunder import reddit

PREREG = reddit.load_prereg()
H0 = int(datetime(2026, 10, 30, 14, tzinfo=timezone.utc).timestamp())   # an hour start
SECRET, CID, TOKEN = "s3cr3t-value", "client-id-xyz", "tok-abc-123"


class TextScorer:
    """Deterministic stand-in for VADER: looks the text up."""
    name = "vader"

    def __init__(self, table, default=0.25):
        self.table, self.default = table, default

    def score(self, pairs):
        return [self.table.get(t, self.default) for t, _ in pairs]


class TargetScorer:
    """Stand-in for a target-based model: positive toward Talarico, negative toward anything else."""
    name = "absa"

    def score(self, pairs):
        return [0.8 if a == "talarico" else -0.6 for _, a in pairs]


def targets(state):
    return reddit.state_targets(state, PREREG)


def test_keywords_names_titles_aliases_and_boundaries():
    tx = targets("tx")
    assert tx.match("Talarico rallied in El Paso") == {"tx-sen/dem_cand": "talarico", "tx-sen/dem": "talarico"}
    assert "tx-sen/rep_cand" in tx.match("Paxton's ad")                     # possessive still a whole word
    assert set(tx.match("the GOP turnout")) == {"party/R", "tx-sen/rep", "tx-gov/rep"}
    assert "tx-gov/rep_cand" in tx.match("Greg Abbott said")               # stoplisted surname: full name ...
    assert "tx-gov/rep_cand" in tx.match("Gov. Abbott said")               # ... or after a title
    assert not tx.match("Abbott Elementary was funny")
    assert not tx.match("talaricofan99 and the gopher")                   # no substring matches
    nm = targets("nm")
    assert "nm-sen/dem_cand" in nm.match("BEN RAY LUJAN") and "nm-sen/dem_cand" in nm.match("Ben Ray Luján")
    assert "nm-sen/dem_cand" not in nm.match("Lujan Grisham signed it")    # outgoing governor, not the candidate
    mt = targets("mt")                                                     # dem side is an independent
    assert set(mt.match("Democrats are quiet")) == {"party/D"}
    assert {"mt-sen/dem", "mt-sen/dem_cand"} <= set(mt.match("Bodnar town hall"))
    assert "oh-gov/rep_cand" in targets("oh").match("Vivek was in Dayton")  # registered nickname
    assert "nh-sen/rep_cand" in targets("nh").match("John E. Sununu spoke")
    assert not any(g.startswith("tx-") for g in targets("ca").match("Talarico"))   # other states' races not matched


def test_aggregate_counts_authors_engagement_and_target_sentiment():
    vader = TextScorer({"Talarico is good": 0.5, "Paxton is bad": -0.5, "Talarico ok": 0.1, "meh": 0.0})
    posts = [{"created_utc": H0 + 10, "author": "alice", "title": "Talarico is good", "selftext": "", "score": 10,
              "num_comments": 4},
             {"created_utc": H0 + 20, "author": "[deleted]", "title": "[deleted]", "selftext": "[removed]", "score": 1},
             {"created_utc": H0 - 5, "author": "zed", "title": "meh", "selftext": ""}]          # earlier hour
    comments = [{"created_utc": H0 + 30, "author": "bob", "body": "Paxton is bad", "score": -3},
                {"created_utc": H0 + 40, "author": "alice", "body": "Talarico ok", "score": 0},
                {"created_utc": H0 + 50, "author": "alice", "body": "Talarico ok", "score": 0},
                {"created_utc": H0 + 3600 + 1, "author": "carol", "body": "meh"}]
    rows = reddit.aggregate("testsub", "tx", "state", posts, comments, [H0, H0 + 3600], targets("tx"),
                            [vader, TargetScorer()], H0 + 3 * 3600)
    r0, r1 = rows
    assert r0["hour"] == "2026-10-30T14:00Z" and r1["hour"] == "2026-10-30T15:00Z"
    assert (r0["posts"], r0["comments"], r0["authors"]) == (2, 3, 2)    # deleted post counted, its author not
    assert r0["engagement"] == {"score_sum": 8, "post_comments": 4}
    assert r0["vader_all"]["n"] == 4
    dem = r0["targets"]["tx-sen/dem_cand"]
    assert (dem["n"], dem["authors"]) == (3, 1)                          # alice three times
    assert dem["vader"]["mean"] == pytest.approx((0.5 + 0.1 + 0.1) / 3, abs=1e-4)
    assert dem["vader"]["amean"] == dem["vader"]["mean"]                # one author: author mean = item mean
    assert dem["vader"]["wmean"] > dem["vader"]["mean"]                 # the upvoted post weighs more
    assert dem["absa"]["mean"] == 0.8
    rep = r0["targets"]["tx-sen/rep_cand"]
    assert (rep["n"], rep["vader"]["mean"], rep["vader"]["sd"], rep["absa"]["mean"]) == (1, -0.5, None, -0.6)
    assert r0["targets"]["tx-gov/rep"] == {"n": 0, "authors": 0, "vader": None, "absa": None}
    assert (r1["posts"], r1["comments"], r1["authors"], r1["lag_h"]) == (0, 1, 1, 1.0)
    assert r1["targets"]["tx-sen/dem"]["n"] == 0


def test_author_mean_caps_a_prolific_author():
    vader = TextScorer({"Paxton great": 0.9, "Paxton awful": -0.9})
    comments = [{"created_utc": H0 + i, "author": "spammer", "body": "Paxton great"} for i in range(9)]
    comments.append({"created_utc": H0 + 100, "author": "other", "body": "Paxton awful"})
    (row,) = reddit.aggregate("s", "tx", "state", [], comments, [H0], targets("tx"), [vader], H0 + 7200)
    st = row["targets"]["tx-sen/rep_cand"]["vader"]
    assert st["mean"] == pytest.approx(0.72) and st["amean"] == pytest.approx(0.0)


class FakeReddit:
    """Mock Reddit: token endpoint, /about, /new and /comments pages (newest first, 100 per page)."""

    def __init__(self, posts, comments, about=None):
        self.lists = {"new": posts, "comments": comments}
        self.about = about or {}
        self.calls, self.user_agents = [], set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.user_agents.add(req.headers["user-agent"])
        if req.url.host == "www.reddit.com":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86400})
        assert req.headers["authorization"] == f"bearer {TOKEN}"
        parts = req.url.path.strip("/").split("/")
        sub, kind = parts[1], parts[-1]
        self.calls.append(kind)
        if kind == "about":
            d = {"subreddit_type": "public", "subscribers": 5, "active_user_count": 2, **self.about.get(sub, {})}
            return httpx.Response(200, json={"kind": "t5", "data": d})
        items = sorted(self.lists[kind], key=lambda i: -i["created_utc"])
        start = int(req.url.params.get("after") or 0)
        page = items[start:start + int(req.url.params.get("limit", 100))]
        after = str(start + 100) if start + 100 < len(items) else None
        return httpx.Response(200, json={"data": {"after": after, "children": [{"kind": "t1", "data": i} for i in page]}},
                              headers={"x-ratelimit-used": "10", "x-ratelimit-remaining": "90", "x-ratelimit-reset": "300"})


def items(n, start, step, kind):
    out = []
    for i in range(n):
        d = {"created_utc": start - i * step, "author": f"user_{i % 7}_unique", "id": f"zz{i:05d}",
             "name": f"t3_zz{i:05d}", "score": i % 5}
        d.update({"title": f"Talarico secretphrase{i}", "selftext": "great"} if kind == "post"
                 else {"body": f"Paxton secretphrase{i}"})
        out.append(d)
    return out


def run(tmp_path, fake, now, subs=(("testsub", "tx", "state"),), logs=None):
    api = reddit.Api(CID, SECRET, "researcher", transport=httpx.MockTransport(fake), sleep=lambda s: None)
    try:
        return reddit.collect(api, PREREG, list(subs), [TextScorer({})], out=tmp_path, now=now,
                              until=H0 + 48 * 3600, log=(logs.append if logs is not None else lambda m: None))
    finally:
        api.close()


def test_collect_paginates_to_floor_is_idempotent_and_stores_no_pii(tmp_path, monkeypatch):
    monkeypatch.setattr(reddit, "BACKFILL_FROM", H0)
    now = H0 + 3 * 3600 + 600                                       # hours H0, H0+1h, H0+2h are finished
    fake = FakeReddit(items(250, now - 60, 60, "post"), items(320, now - 30, 45, "comment"))
    logs = []
    s = run(tmp_path, fake, now, logs=logs)
    assert s["rows"] == 3 and not s["failed"]
    assert s["requests"] == 1 + 2 + 1 + 2 + 3                       # token, verify (about+new), daily about, pages
    assert fake.calls.count("comments") == 3 and fake.calls.count("new") == 1 + 2   # stopped once past H0
    assert fake.user_agents == {f"{reddit.platform.system().lower()}:org.brunder.us2026reddit:v{reddit.VERSION} (by /u/researcher)"}
    rows = [json.loads(l) for l in (tmp_path / "hourly.jsonl").read_text().splitlines()]
    assert [r["hour"] for r in rows] == ["2026-10-30T14:00Z", "2026-10-30T15:00Z", "2026-10-30T16:00Z"]
    assert all(r["targets"]["tx-sen/dem_cand"]["n"] == r["posts"] for r in rows)
    daily = [json.loads(l) for l in (tmp_path / "subs_daily.jsonl").read_text().splitlines()]
    assert daily == [{"date": "2026-10-30", "subreddit": "testsub", "state": "tx", "subscribers": 5, "active_users": 2}]

    blob = "\n".join(p.read_text() for p in tmp_path.iterdir() if p.is_file()) + "\n".join(logs)
    for needle in ("user_", "secretphrase", "zz000", "t3_", SECRET, CID, TOKEN, "Talarico", "talarico", "Paxton"):
        assert needle not in blob

    fake.calls.clear()
    assert run(tmp_path, fake, now)["rows"] == 0 and fake.calls == []          # same hour: nothing refetched
    assert run(tmp_path, fake, now + 3600)["rows"] == 1                       # next hour: one new row
    assert len((tmp_path / "hourly.jsonl").read_text().splitlines()) == 4
    assert len((tmp_path / "subs_daily.jsonl").read_text().splitlines()) == 1  # once per day


def test_listing_cap_limits_backfill_and_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(reddit, "BACKFILL_FROM", H0)
    now = H0 + 10 * 3600 + 600
    # 1000 comments every 20 s reach back only ~5.6 h: older hours cannot be rebuilt
    fake = FakeReddit(items(5, now - 100, 600, "post"), items(1000, now - 10, 20, "comment"))
    s = run(tmp_path, fake, now)
    hours = [json.loads(l)["hour"] for l in (tmp_path / "hourly.jsonl").read_text().splitlines()]
    assert s["rows"] == len(hours) == 5 and hours[0] == "2026-10-30T19:00Z" and s["lost_hours"] == 5
    assert reddit.hour_iso(json.loads((tmp_path / "state.json").read_text())["testsub"]) == "2026-10-30T19:00Z"
    fake.calls.clear()
    assert run(tmp_path, fake, now)["rows"] == 0 and fake.calls == []


def test_selection_by_visibility_volume_and_size(tmp_path):
    now = H0
    busy = [{"created_utc": now - i * 3600} for i in range(100)]       # 24 posts/day
    fake = FakeReddit(busy, [], about={"st": {}, "big": {"subscribers": 900}, "mid": {"subscribers": 500},
                                       "small": {"subscribers": 100}, "tiny": {"subscribers": 950},
                                       "priv": {"subreddit_type": "private", "subscribers": 10_000},
                                       "nsfw": {"over18": True, "subscribers": 10_000}})
    quiet = {"tiny"}

    def handler(req):
        if req.url.path.endswith("/new") and req.url.path.split("/")[2] in quiet:
            return httpx.Response(200, json={"data": {"after": None, "children": [
                {"data": {"created_utc": now - 86400}}]}})
        return fake(req)

    api = reddit.Api(CID, SECRET, "researcher", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    subs = [("st", "zz", "state")] + [(s, "zz", "city") for s in ("big", "mid", "small", "tiny", "priv", "nsfw")]
    res = reddit.verify(api, subs, tmp_path, now)
    api.close()
    assert {s for s, v in res.items() if v["selected"]} == {"st", "big", "mid", "small"}
    assert res["tiny"]["posts_per_day"] < reddit.MIN_POSTS_PER_DAY and res["big"]["posts_per_day"] >= 23
    assert not res["priv"]["usable"] and not res["nsfw"]["usable"]


def test_pacing_and_ratelimit_headers():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    def handler(req):
        if req.url.host == "www.reddit.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86400})
        return httpx.Response(200, json={"data": {}}, headers={"x-ratelimit-used": "85", "x-ratelimit-remaining": "15",
                                                               "x-ratelimit-reset": "40"})

    api = reddit.Api(CID, SECRET, "researcher", transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: t[0])
    api.get("/r/x/new")
    api.get("/r/x/new")
    api.close()
    assert slept.count(41) == 2                         # < 20 % of the window left: waited for the reset
    assert api.requests == 3                            # one token reused + 2 listings
    assert all(s >= 0 for s in slept) and sum(s for s in slept if s != 41) >= 1.0   # ≥ 1 s between requests


def test_backoff_on_429_then_success():
    n = [0]
    slept = []

    def handler(req):
        if req.url.host == "www.reddit.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86400})
        n[0] += 1
        if n[0] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={"data": {"ok": 1}})

    api = reddit.Api(CID, SECRET, "researcher", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert api.get("/r/x/new") == {"data": {"ok": 1}}
    api.close()
    assert 7 in slept


def test_credentials_from_env_file_never_echoed(tmp_path, monkeypatch, capsys):
    for k in reddit.ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "reddit.env"
    f.write_text(f"REDDIT_ID={CID}\nREDDIT_SECRET='{SECRET}'\n")
    with pytest.raises(SystemExit) as e:
        reddit.credentials(f)
    assert "REDDIT_USERNAME" in str(e.value) and SECRET not in str(e.value) and CID not in str(e.value)
    f.write_text(f.read_text() + "REDDIT_USERNAME=researcher\n")
    assert reddit.credentials(f) == {"REDDIT_ID": CID, "REDDIT_SECRET": SECRET, "REDDIT_USERNAME": "researcher"}
    assert SECRET not in capsys.readouterr().out
