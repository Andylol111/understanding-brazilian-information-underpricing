"""Hourly aggregate volume and target sentiment of US state and city subreddits, 2026-10-23 → 2026-11-04.

Pre-registered in configs/reddit_us2026_v1.toml; terms and safeguards in docs/us_reddit.md. App-only OAuth,
honest User-Agent, at most one request per second, X-Ratelimit headers honoured. Listings are held in memory
for one subreddit at a time and reduced to per-hour aggregates: counts, unique-author COUNTS, engagement,
and sentiment toward each race's sides (VADER baseline; optional target-based transformer). Usernames, text
and ids are never written anywhere, and nothing is inferred about individual users.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import platform
import re
import resource
import time
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from .paths import PROCESSED, ROOT

VERSION = "0.2.0"
APP_ID = "org.brunder.us2026reddit"
API = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SUBS_CONFIG = ROOT / "configs" / "reddit_subs.toml"
PREREG = ROOT / "configs" / "reddit_us2026_v1.toml"   # not us2026_*_v1.toml: that glob means race model configs
OUT = PROCESSED / "reddit" / "us2026"
ENV_FILE = Path.home() / ".config" / "brunder" / "reddit.env"
ENV_KEYS = ("REDDIT_ID", "REDDIT_SECRET", "REDDIT_USERNAME")

BACKFILL_FROM = int(datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp())
UNTIL = int(datetime(2026, 11, 4, 12, tzinfo=timezone.utc).timestamp())
HOUR = 3600
SETTLE = 120              # an hour is aggregated only once it ended this many seconds ago
MIN_INTERVAL = 1.0        # ≤ 60 requests/min (Reddit's free limit: 100 QPM per client, 10-min average)
LOW_BUDGET = 0.2          # wait for the window reset when < 20 % of it is left
LISTING_CAP = 900         # a listing that runs out after this many items hit Reddit's ~1000-item cap
CITY_MAX = 3              # city subreddits kept per state
MIN_POSTS_PER_DAY = 3.0   # city subreddits below this recent volume are not recorded
ABSA_MODEL = "yangheng/deberta-v3-base-absa-v1.1"          # MIT; checked 2026-10-29
ABSA_REVISION = "10c9dff335a44073e1352360c3a7bc54dc58eb01"
GONE = ("[deleted]", "[removed]", "")
TITLES = ("governor", "gov", "gov.", "senator", "sen", "sen.", "rep", "rep.", "representative",
          "congressman", "congresswoman", "mayor", "ag", "attorney general", "lt gov", "lt. gov.")


# ---------------------------------------------------------------- keywords

def fold(text: str) -> str:
    """Casefold, strip accents, straighten apostrophes."""
    text = unicodedata.normalize("NFKD", text.replace("\u2019", "'").replace("\u2018", "'"))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def name_terms(name: str, stoplist: set[str], aliases: list[str] = ()) -> list[str]:
    """Full name (with and without initials' dots), first + last, the last name alone unless stoplisted
    (then only after a title: 'Sen. Collins', 'Governor Moore'), plus registered aliases."""
    terms = {" ".join(fold(a).split()) for a in aliases}
    if name.strip():
        full = " ".join(fold(name).split())
        toks = full.replace(".", " ").split()
        terms |= {full, " ".join(toks)}
        if len(toks) > 2:
            terms.add(f"{toks[0]} {toks[-1]}")
        last = toks[-1]
        if last in stoplist:
            terms |= {f"{t} {last}" for t in TITLES}
        else:
            terms.add(last)
    return sorted(terms)


def _pattern(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    alt = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.compile(rf"(?<![\w])(?:{alt})(?![\w])")


@dataclass
class Targets:
    """Compiled keyword groups for one state. Names: 'party/D', 'party/R', '<race>/dem', '<race>/rep',
    '<race>/dem_cand', '<race>/rep_cand'."""
    patterns: dict[str, re.Pattern]
    names: list[str]

    def match(self, text: str) -> dict[str, str]:
        """target -> first matched term (used as the aspect for target-based scoring)."""
        t = fold(text)
        out = {}
        for g, p in self.patterns.items():
            m = p.search(t)
            if m:
                out[g] = m.group(0)
        return out


def state_targets(state: str, prereg: dict) -> Targets:
    kw = prereg["keywords"]
    stop = {fold(s) for s in kw["surname_stoplist"]}
    aliases = kw.get("aliases", {})
    party_d, party_r = [fold(t) for t in kw["party_D"]], [fold(t) for t in kw["party_R"]]
    pats: dict[str, re.Pattern | None] = {"party/D": _pattern(party_d), "party/R": _pattern(party_r)}
    names = ["party/D", "party/R"]
    for key, r in prereg["races"].items():
        if r["state"] != state:
            continue
        dem = name_terms(r["dem"], stop, aliases.get(f"{key}/dem", []))
        rep = name_terms(r["rep"], stop, aliases.get(f"{key}/rep", []))
        pats[f"{key}/dem_cand"] = _pattern(dem)
        pats[f"{key}/rep_cand"] = _pattern(rep)
        pats[f"{key}/dem"] = _pattern(dem + (party_d if r["dem_party"] == "D" else []))
        pats[f"{key}/rep"] = _pattern(rep + party_r)
        names += [f"{key}/{g}" for g in ("dem", "rep", "dem_cand", "rep_cand")]
    return Targets({g: p for g, p in pats.items() if p is not None}, names)


# ---------------------------------------------------------------- scorers

class Scorer(Protocol):
    """Sentiment in [-1, 1] for (text, target) pairs; target-blind scorers ignore the target. Inference
    only: no scorer is ever trained or tuned on Reddit content."""
    name: str

    def score(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class Vader:
    name = "vader"

    def __init__(self):
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        self._sia = SentimentIntensityAnalyzer()

    def score(self, pairs):
        return [self._sia.polarity_scores(text)["compound"] for text, _ in pairs]


class HFTarget:
    """Target-based sentiment from a Hugging Face sentence-pair classifier, (text, target) -> P(pos) - P(neg).
    Works for any checkpoint whose labels include positive/negative (the ABSA default, or a ModernBERT
    stance/sentiment checkpoint later). Batched CPU inference, inputs truncated to max_tokens."""
    name = "absa"

    def __init__(self, model: str = ABSA_MODEL, revision: str | None = ABSA_REVISION, batch: int = 16,
                 max_tokens: int = 512, threads: int = 1):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(threads)
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(model, revision=revision)
        self._model = AutoModelForSequenceClassification.from_pretrained(model, revision=revision).eval()
        labels = {v.lower(): int(k) for k, v in self._model.config.id2label.items()}
        self._pos, self._neg = labels["positive"], labels["negative"]
        self._batch, self._max = batch, max_tokens

    def score(self, pairs):
        out: list[float] = []
        with self._torch.inference_mode():
            for i in range(0, len(pairs), self._batch):
                chunk = pairs[i:i + self._batch]
                enc = self._tok([t for t, _ in chunk], [a for _, a in chunk], truncation="only_first",
                                max_length=self._max, padding=True, return_tensors="pt")
                p = self._model(**enc).logits.softmax(-1)
                out += (p[:, self._pos] - p[:, self._neg]).tolist()
        return out


class Budgeted:
    """Wraps a slow scorer: once `seconds` of scoring are used in a run, it returns None (not scored)."""

    def __init__(self, inner: Scorer, seconds: float, clock=time.monotonic):
        self.inner, self.name, self.left, self.clock = inner, inner.name, seconds, clock
        self.items, self.used = 0, 0.0

    def score(self, pairs):
        if self.left <= 0:
            return [None] * len(pairs)
        t0 = self.clock()
        out = self.inner.score(pairs)
        dt = self.clock() - t0
        self.left -= dt
        self.used += dt
        self.items += len(pairs)
        return out


# ---------------------------------------------------------------- aggregation

def _weight(reddit_score: int) -> float:
    """Engagement weight for score-weighted means: 1 + ln(1 + max(score, 0))."""
    return 1.0 + math.log1p(max(int(reddit_score or 0), 0))


def _stats(vals: list[tuple[float, float, str]]) -> dict | None:
    """vals: (sentiment, weight, author key). mean/sd unweighted; wmean score-weighted; amean = mean of
    per-author means, so one author counts once per subreddit-hour."""
    vals = [v for v in vals if v[0] is not None]
    n = len(vals)
    if not n:
        return None
    xs = [v[0] for v in vals]
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else None
    wm = sum(x * w for x, w, _ in vals) / sum(w for _, w, _ in vals)
    per: dict[str, list[float]] = {}
    for x, _, a in vals:
        per.setdefault(a, []).append(x)
    am = sum(sum(v) / len(v) for v in per.values()) / len(per)
    r = lambda x: None if x is None else round(x, 4)
    return {"n": n, "mean": r(m), "sd": r(sd), "wmean": r(wm), "amean": r(am)}


def _text(item: dict, kind: str) -> str:
    parts = [item.get("title") or "", item.get("selftext") or ""] if kind == "post" else [item.get("body") or ""]
    return "\n".join(p for p in parts if p.strip() not in GONE)


def hour_iso(h: int) -> str:
    return datetime.fromtimestamp(h, timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def aggregate(sub: str, state: str, role: str, posts: list[dict], comments: list[dict], hours: list[int],
              targets: Targets, scorers: list[Scorer], collected: int) -> list[dict]:
    """One row per requested hour (unix start). Only aggregates leave this function."""
    want = set(hours)
    acc = {h: {"posts": 0, "comments": 0, "authors": set(), "score_sum": 0, "post_comments": 0}
           for h in hours}
    mentions: list[tuple[int, str, str, str, float, str]] = []   # hour, target, text, aspect, weight, author key
    texts: list[tuple[int, str]] = []
    anon = 0
    for kind, items in (("post", posts), ("comment", comments)):
        for it in items:
            h = int(it["created_utc"]) // HOUR * HOUR
            if h not in want:
                continue
            a = acc[h]
            a["posts" if kind == "post" else "comments"] += 1
            a["score_sum"] += int(it.get("score") or 0)
            a["post_comments"] += int(it.get("num_comments") or 0) if kind == "post" else 0
            author = it.get("author")
            if author and author not in GONE:
                a["authors"].add(author)
            else:
                anon += 1
                author = f"\0{anon}"                   # deleted author: its own one-item author
            text = _text(it, kind)
            if not text:
                continue
            texts.append((h, text))
            w = _weight(it.get("score"))
            for g, term in targets.match(text).items():
                mentions.append((h, g, text, term, w, author))
    vader = next(s for s in scorers if s.name == "vader")
    overall = vader.score([(t, "") for _, t in texts])
    scores = {s.name: s.score([(t, term) for _, _, t, term, _, _ in mentions]) for s in scorers}
    by_target: dict[tuple[int, str], list[int]] = {}
    for i, m in enumerate(mentions):
        by_target.setdefault((m[0], m[1]), []).append(i)
    by_hour: dict[int, list[tuple[float, float, str]]] = {}
    for i, ((h, _), x) in enumerate(zip(texts, overall)):
        by_hour.setdefault(h, []).append((x, 1.0, str(i)))
    rows = []
    for h in sorted(want):
        a = acc[h]
        tg = {}
        for g in targets.names:
            idx = by_target.get((h, g), [])
            tg[g] = {"n": len(idx), "authors": len({mentions[i][5] for i in idx}),
                     **{s: _stats([(scores[s][i], mentions[i][4], mentions[i][5]) for i in idx]) for s in scores}}
        rows.append({
            "hour": hour_iso(h), "subreddit": sub, "state": state, "role": role,
            "posts": a["posts"], "comments": a["comments"], "authors": len(a["authors"]),
            "engagement": {"score_sum": a["score_sum"], "post_comments": a["post_comments"]},
            "vader_all": _stats(by_hour.get(h, [])),
            "targets": tg,
            "scorers": sorted(scores),
            "lag_h": round((collected - h - HOUR) / HOUR, 2),
            "spec": "reddit_us2026_v1",
        })
    return rows


# ---------------------------------------------------------------- API

def credentials(env_file: Path = ENV_FILE) -> dict[str, str]:
    """REDDIT_ID / REDDIT_SECRET / REDDIT_USERNAME from the environment (systemd EnvironmentFile), else
    from env_file. Values are never printed."""
    vals = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    if len(vals) < len(ENV_KEYS) and env_file.exists():
        for line in env_file.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() in ENV_KEYS and k.strip() not in vals:
                vals[k.strip()] = v.strip().strip("'\"")
    missing = [k for k in ENV_KEYS if not vals.get(k)]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)} (environment or {env_file})")
    return vals


def user_agent(username: str) -> str:
    return f"{platform.system().lower()}:{APP_ID}:v{VERSION} (by /u/{username})"


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


class Api:
    """App-only OAuth client. One token reused until shortly before expiry; ≥ MIN_INTERVAL between
    requests; waits for the window reset when X-Ratelimit-Remaining < 20 % of the window; exponential
    backoff on 429/5xx (Retry-After honoured)."""

    def __init__(self, cid: str, secret: str, username: str, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.monotonic):
        self._auth = (cid, secret)
        self._http = httpx.Client(headers={"User-Agent": user_agent(username)}, timeout=60, transport=transport)
        self._sleep, self._clock = sleep, clock
        self._token, self._token_exp, self._last = None, 0.0, -1e9
        self.requests = 0

    def close(self) -> None:
        self._http.close()

    def _pace(self) -> None:
        wait = self._last + MIN_INTERVAL - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()
        self.requests += 1

    def _bearer(self) -> str:
        if self._token is None or self._clock() > self._token_exp - 300:
            self._pace()
            r = self._http.post(TOKEN_URL, auth=self._auth, data={"grant_type": "client_credentials"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or "access_token" not in body:
                raise RuntimeError(f"token request failed: HTTP {r.status_code} {body.get('error', '')}".strip())
            self._token = body["access_token"]
            self._token_exp = self._clock() + float(body.get("expires_in", 3600))
        return self._token

    def get(self, path: str, params: dict | None = None, tries: int = 6) -> dict:
        for attempt in range(tries):
            bearer = self._bearer()
            self._pace()
            try:
                r = self._http.get(API + path, params={k: v for k, v in (params or {}).items() if v is not None},
                                   headers={"Authorization": f"bearer {bearer}"})
            except httpx.TransportError:
                if attempt == tries - 1:
                    raise
                self._sleep(min(2 ** attempt, 60))
                continue
            used, left = _float(r.headers.get("x-ratelimit-used")), _float(r.headers.get("x-ratelimit-remaining"))
            reset = _float(r.headers.get("x-ratelimit-reset")) or 0.0
            if left is not None and left < LOW_BUDGET * ((used or 0) + left):
                self._sleep(reset + 1)
            if r.status_code == 401 and attempt < tries - 1:
                self._token = None
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                self._sleep(max(_float(r.headers.get("retry-after")) or 0, reset if r.status_code == 429 else 0,
                                min(2 ** attempt, 60)))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")


def listing(api: Api, path: str, floor: int) -> tuple[list[dict], bool]:
    """Newest-first listing back past `floor` (unix). reached=False when the listing ran out first near
    Reddit's ~1000-item cap, so items older than the oldest returned are unknown."""
    items: list[dict] = []
    after = None
    while True:
        data = api.get(path, {"limit": 100, "after": after, "raw_json": 1})["data"]
        page = [c["data"] for c in data.get("children", [])]
        items += page
        if page and min(int(p["created_utc"]) for p in page) < floor:
            return items, True
        after = data.get("after")
        if not page or not after:
            return items, len(items) < LISTING_CAP


# ---------------------------------------------------------------- subreddit selection

def load_subs(path: Path = SUBS_CONFIG) -> list[tuple[str, str, str]]:
    """(subreddit, state, role) for every configured name; role 'state' for the first of each list."""
    with open(path, "rb") as f:
        subs = tomllib.load(f)["subs"]
    return [(s, st, "state" if i == 0 else "city") for st, names in subs.items() for i, s in enumerate(names)]


def load_prereg(path: Path = PREREG) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def posts_per_day(created: list[int], now: int) -> float:
    """Recent volume from up to 100 newest posts: count in the last 7 days / 7, or, when 100 posts span
    less than 7 days, 99 / span."""
    if not created:
        return 0.0
    week = now - 7 * 86400
    if len(created) >= 100 and min(created) > week:
        return round(99 / max((max(created) - min(created)) / 86400, 1 / 24), 2)
    return round(sum(c > week for c in created) / 7, 2)


def _about(api: Api, sub: str) -> dict:
    try:
        d = api.get(f"/r/{sub}/about", {"raw_json": 1})
        return d.get("data", {}) if d.get("kind") == "t5" else {"subreddit_type": "missing"}
    except httpx.HTTPStatusError as e:
        return {"subreddit_type": f"http_{e.response.status_code}"}


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, path)


def verify(api: Api, subs: list[tuple[str, str, str]], out: Path = OUT, now: int | None = None) -> dict:
    """/r/<sub>/about and /new (100) for each candidate; per state keep the state subreddit and the CITY_MAX
    city subreddits with most subscribers among those with ≥ MIN_POSTS_PER_DAY. Merged into out/subs.json."""
    now = int(now if now is not None else time.time())
    out.mkdir(parents=True, exist_ok=True)
    path = out / "subs.json"
    res = json.loads(path.read_text()) if path.exists() else {}
    for sub, state, role in subs:
        d = _about(api, sub)
        typ = d.get("subreddit_type", "missing")
        ok = typ == "public" and not d.get("over18") and not d.get("quarantine")
        ppd = None
        if ok:
            new = api.get(f"/r/{sub}/new", {"limit": 100, "raw_json": 1})["data"].get("children", [])
            ppd = posts_per_day([int(c["data"]["created_utc"]) for c in new], now)
        res[sub] = {"state": state, "role": role, "type": typ, "subscribers": d.get("subscribers"),
                    "over18": bool(d.get("over18")), "quarantine": bool(d.get("quarantine")),
                    "posts_per_day": ppd, "usable": ok, "checked": hour_iso(now // HOUR * HOUR)}
    for state in {s[1] for s in subs}:
        mine = {k: v for k, v in res.items() if v["state"] == state}
        cities = sorted((k for k, v in mine.items() if v["role"] == "city" and v["usable"]
                         and (v["posts_per_day"] or 0) >= MIN_POSTS_PER_DAY),
                        key=lambda k: -(mine[k]["subscribers"] or 0))[:CITY_MAX]
        for k, v in mine.items():
            v["selected"] = v["usable"] and (v["role"] == "state" or k in cities)
    _write_json(path, res)
    return {sub: res[sub] for sub, _, _ in subs}


# ---------------------------------------------------------------- collection

def done_hours(path: Path) -> set[tuple[str, str]]:
    out = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.add((r["subreddit"], r.get("hour") or r.get("date")))
    return out


def _append(path: Path, rows: list[dict]) -> None:
    with path.open("a") as f:
        f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
        f.flush()
        os.fsync(f.fileno())


def hours_needed(sub: str, floor: int, last_end: int, done: set[tuple[str, str]]) -> list[int]:
    start = max(floor, BACKFILL_FROM) // HOUR * HOUR
    return [h for h in range(start, last_end, HOUR) if (sub, hour_iso(h)) not in done]


def collect(api: Api, prereg: dict, subs: list[tuple[str, str, str]], scorers: list[Scorer], out: Path = OUT,
            now: float | None = None, until: int = UNTIL, only: list[str] | None = None,
            max_hours: int | None = None, log=print) -> dict:
    """Aggregate every finished, not-yet-recorded hour for each selected subreddit into hourly.jsonl, and
    once per UTC day its subscriber / active-user counts into subs_daily.jsonl. Idempotent per
    subreddit-hour; hours older than what the listings still return are skipped and not retried."""
    out.mkdir(parents=True, exist_ok=True)
    lock = open(out / ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)        # one collector at a time
    try:
        now = int(now if now is not None else time.time())
        last_end = min(until, (now - SETTLE) // HOUR * HOUR)
        status_path, state_path = out / "subs.json", out / "state.json"
        hourly, daily = out / "hourly.jsonl", out / "subs_daily.jsonl"
        req0 = api.requests
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        states = {st for s, st, _ in subs if not only or s in only}
        unchecked = [s for s in subs if s[1] in states and s[0] not in status]
        if unchecked:
            status.update(verify(api, [s for s in subs if s[1] in {u[1] for u in unchecked}], out, now))
        floors = json.loads(state_path.read_text()) if state_path.exists() else {}
        done, days_done = done_hours(hourly), done_hours(daily)
        today = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        summary = {"rows": 0, "subs": 0, "skipped": [], "failed": [], "lost_hours": 0, "mentions": 0}
        for sub, state, role in subs:
            if only and sub not in only:
                continue
            if not status.get(sub, {}).get("selected"):
                summary["skipped"].append(sub)
                continue
            need = hours_needed(sub, int(floors.get(sub, BACKFILL_FROM)), last_end, done)
            if max_hours:
                need = need[-max_hours:]
            try:
                if (sub, today) not in days_done and now < until + 2 * HOUR:
                    d = _about(api, sub)
                    _append(daily, [{"date": today, "subreddit": sub, "state": state,
                                     "subscribers": d.get("subscribers"),
                                     "active_users": d.get("active_user_count", d.get("accounts_active"))}])
                if not need:
                    continue
                first = need[0]
                posts, p_ok = listing(api, f"/r/{sub}/new", first)
                comments, c_ok = listing(api, f"/r/{sub}/comments", first)
            except httpx.HTTPError as e:
                code = getattr(getattr(e, "response", None), "status_code", type(e).__name__)
                log(f"{sub}: request failed ({code}); retried next run")
                summary["failed"].append(sub)
                continue
            covered = first
            for items, ok in ((posts, p_ok), (comments, c_ok)):
                if not ok and items:
                    oldest = min(int(i["created_utc"]) for i in items)
                    covered = max(covered, -(-oldest // HOUR) * HOUR)
            keep = [h for h in need if h >= covered]
            rows = aggregate(sub, state, role, posts, comments, keep, state_targets(state, prereg), scorers,
                             now) if keep else []
            del posts, comments
            if rows:
                _append(hourly, rows)
            if covered > first:
                floors[sub] = max(int(floors.get(sub, BACKFILL_FROM)), covered)
                _write_json(state_path, floors)
            summary["rows"] += len(rows)
            summary["subs"] += 1
            summary["lost_hours"] += len(need) - len(keep)
            summary["mentions"] += sum(t["n"] for r in rows for t in r["targets"].values())
        summary["requests"] = api.requests - req0
        return summary
    finally:
        lock.close()


# ---------------------------------------------------------------- CLI

BENCH_TEXTS = [  # synthetic, not Reddit content
    "The governor's plan for the budget is a disaster and everyone I know is angry about it.",
    "I think the senator did a solid job at the debate last night, much better than expected.",
    "Turnout looked heavy at my polling place this morning; the line went around the block.",
    "Neither candidate has said anything concrete about housing costs, which is frustrating.",
]


def scorers_for(a) -> list[Scorer]:
    out: list[Scorer] = [Vader()]
    if getattr(a, "absa", None):
        model, _, rev = a.absa.partition("@")
        out.append(Budgeted(HFTarget(model, rev or (ABSA_REVISION if model == ABSA_MODEL else None),
                                     batch=a.batch), a.absa_budget))
    return out


def bench(a) -> None:
    pairs = [(t + " " + "More detail follows here. " * a.pad, "senator") for t in BENCH_TEXTS] * (a.n // 4)
    for s in scorers_for(a):
        inner = getattr(s, "inner", s)
        inner.score(pairs[:2])                                     # warm-up / lazy init
        t0 = time.monotonic()
        inner.score(pairs)
        dt = time.monotonic() - t0
        print(f"{s.name}: {len(pairs)} items in {dt:.1f}s = {len(pairs) / dt:.1f} items/s")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"peak RSS {rss / (1 << 20 if platform.system() == 'Darwin' else 1 << 10):.0f} MB")


def cli(a) -> None:
    if a.step == "bench":
        bench(a)
        return
    creds = credentials()
    api = Api(creds["REDDIT_ID"], creds["REDDIT_SECRET"], creds["REDDIT_USERNAME"])
    del creds
    subs = load_subs()
    try:
        if a.step == "verify":
            res = verify(api, subs)
            sel = [s for s, v in res.items() if v["selected"]]
            bad = sorted(s for s, v in res.items() if not v["usable"])
            low = sorted(s for s, v in res.items() if v["usable"] and not v["selected"])
            print(f"{len(sel)} selected of {len(res)} candidates in {len({v['state'] for v in res.values()})} states; "
                  f"{api.requests} requests\nnot usable: {', '.join(f'{s} ({res[s]['type']})' for s in bad) or 'none'}\n"
                  f"usable but not selected (volume < {MIN_POSTS_PER_DAY}/day or beyond {CITY_MAX} largest): "
                  f"{', '.join(low) or 'none'}")
            return
        until = int(datetime.fromisoformat(a.until.replace("Z", "+00:00")).timestamp()) if a.until else UNTIL
        scorers = scorers_for(a)
        t0 = time.monotonic()
        s = collect(api, load_prereg(), subs, scorers, until=until, only=a.sub or None, max_hours=a.max_hours)
        extra = "".join(f"; {x.name}: {x.items} items in {x.used:.0f}s" + (" (budget spent, rest unscored)" if x.left <= 0 else "")
                        for x in scorers if isinstance(x, Budgeted))
        print(f"{s['rows']} row(s) from {s['subs']} subreddit(s), {s['mentions']} target mentions, "
              f"{s['requests']} requests in {time.monotonic() - t0:.0f}s; {s['lost_hours']} hour(s) beyond the "
              f"listing cap; not selected: {len(s['skipped'])}; failed: {', '.join(s['failed']) or 'none'}{extra}")
        if s["failed"]:
            raise SystemExit(1)
    finally:
        api.close()
