#!/usr/bin/env python3
"""Election-night public live log of model readings (hash-chained JSONL).

  backfill : one-off rebuild from the full timeline.json of the five model sources (rows tagged "recorded":"backfilled"),
             plus the VA-01 rows hand-transcribed from what the page served before fb24c61 ("recorded":"transcribed_as_published").
  live     : every 2 min fetch each source's timeline_tail.json, append rows whose published fields changed
             ("recorded":"live"); every 10 min commit + pull --rebase + push the log files only.

All work happens in a dedicated clone (--repo) so other processes' edits in the main checkout cannot collide.
Only the project's own tunnel (site/data/live_link.json) is requested, at most once per file per 2 minutes.

Line format: canonical JSON (sorted keys, no spaces, ASCII) whose LAST key is "hash":
  hash = sha256(prev_hash + body), body = the line with the trailing ',"hash":"<64 hex>"}' replaced by '}'.
The first line of a file has prev_hash = 64 zeros. Verifiers re-hash the raw line text; no re-serialising needed.
"""
import argparse, gzip, hashlib, json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

SOURCES = [  # (source, base path on tunnel, output file)
    ("registered", "us2026/", "registered.jsonl"),
    ("registered-house", "us2026/house_live/", "registered-house.jsonl"),
    ("added", "extra/us2026/", "added.jsonl"),
    ("added-house", "extra/us2026/house_live/", "added-house.jsonl"),
    ("civic", "civic/us2026/", "civic.jsonl"),
]
FIELDS = ["counted", "raw", "share", "sd", "P", "P_v2", "pm", "kalshi", "votes_proj", "stale"]
CMP = ["counted", "raw", "share", "sd", "P", "P_v2", "pm", "kalshi", "stale"]
EXTRA_KEEP = ["stale_since"]  # kept only when the feed supplies it
GENESIS = "0" * 64
LOOP_SECS = 120
PUSH_SECS = 600
UNTIL = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)   # extended (user, Nov 4 morning): late count
UA = "brunder-livelog/1.0 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"
TRANSCRIBED = "hand-transcribed from a printout of extra/us2026/house_live/timeline_tail.json fetched via the live tunnel at ~03:46Z, before the 03:48Z restart; times to the minute"
CAPTURED = os.path.expanduser("~/brunder-night/va01_as_published_pre_fb24c61.json")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(*a):
    print(now_iso(), *a, flush=True)


def parse_t(s):
    s = s.replace("Z", "")
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return 0.0


def fetch(base, path):
    req = urllib.request.Request(base + path, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def tunnel(repo):
    with open(os.path.join(repo, "site/data/live_link.json")) as f:
        u = json.load(f)["url"]
    return u if u.endswith("/") else u + "/"


def make_line(obj, prev):
    obj = dict(obj, prev_hash=prev)
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    h = hashlib.sha256((prev + body).encode()).hexdigest()
    return body[:-1] + ',"hash":"' + h + '"}', h


def row_obj(r, race, source, recorded, **extra):
    o = {"t": r.get("t"), "race": race, "source": source}
    for k in FIELDS:
        o[k] = r.get(k)
    for k in EXTRA_KEEP:
        if k in r:
            o[k] = r[k]
    o["logged_at"] = now_iso()
    o["recorded"] = recorded
    o.update(extra)
    return o


def sig(r):
    return tuple(r.get(k) for k in CMP)


class Chain:
    def __init__(self, path):
        self.path = path
        self.rows = 0
        self.last_hash = GENESIS
        self.last = {}  # race -> (t, sig) of last backfilled/live row
        self.races = {}
        self.first_live_t = None
        self.first_live_logged = None
        if os.path.exists(path):
            with open(path) as f:
                for ln in f:
                    ln = ln.rstrip("\n")
                    if not ln:
                        continue
                    o = json.loads(ln)
                    self._note(o)
                    self.last_hash = o["hash"]

    def _note(self, o):
        self.rows += 1
        self.races[o["race"]] = self.races.get(o["race"], 0) + 1
        if o.get("recorded") in ("backfilled", "live"):
            self.last[o["race"]] = (o["t"], sig(o))
        if o.get("recorded") == "live" and self.first_live_t is None:
            self.first_live_t, self.first_live_logged = o["t"], o["logged_at"]

    def append(self, objs):
        if not objs:
            return 0
        with open(self.path, "a") as f:
            for o in objs:
                line, h = make_line(o, self.last_hash)
                f.write(line + "\n")
                self.last_hash = h
                self._note(dict(o, hash=h))
        return len(objs)

    def new_rows(self, race, source, rows, recorded, **extra):
        """Rows (ordered) that differ from the last kept row of this race; same/older-than-last t are skipped unless changed at t == last t."""
        out = []
        prev = self.last.get(race)
        for r in sorted((x for x in rows if isinstance(x, dict) and x.get("t")), key=lambda x: parse_t(x["t"])):
            if prev and parse_t(r["t"]) < parse_t(prev[0]):
                continue
            if prev is None or sig(r) != prev[1]:
                out.append(row_obj(r, race, source, recorded, **extra))
                prev = (r["t"], sig(r))
        self.last[race] = prev if prev else self.last.get(race)
        return out


def backfill(repo, outdir, base):
    for source, path, fn in SOURCES:
        ch = Chain(os.path.join(outdir, fn))
        if ch.rows:
            log("backfill skip (exists):", fn, ch.rows)
            continue
        url_file = path + "timeline.json"
        tl = fetch(base, url_file)
        stamp = now_iso()
        rebuilt = f"{url_file} fetched {stamp}"
        objs = []
        for race, rows in tl.items():
            if not isinstance(rows, list):
                continue
            ch.last.pop(race, None)
            objs += ch.new_rows(race, source, rows, "backfilled", rebuilt_from=rebuilt)
        if source == "added-house" and os.path.exists(CAPTURED):
            cap = json.load(open(CAPTURED))
            prevsig = None
            for r in cap["rows"]:
                s = sig(r)
                if s != prevsig:
                    objs.append(row_obj(r, cap["race"], source, "transcribed_as_published", read_at=cap["read_at"], sourced=TRANSCRIBED))
                    prevsig = s
        objs.sort(key=lambda o: (parse_t(o["t"]), o["race"], o["recorded"] == "transcribed_as_published"))
        ch.last = {}
        ch.rows = 0
        ch.races = {}
        ch.last_hash = GENESIS
        open(ch.path, "w").close()
        ch.append(objs)
        log("backfill", fn, ch.rows, "rows from", url_file)


def write_manifest(outdir, chains, checked):
    files = []
    for source, path, fn in SOURCES:
        ch = chains[source]
        files.append({
            "source": source, "file": fn, "rows": ch.rows, "last_hash": ch.last_hash,
            "bytes": os.path.getsize(ch.path) if os.path.exists(ch.path) else 0,
            "first_live_t": ch.first_live_t, "first_live_logged_at": ch.first_live_logged,
            "races": dict(sorted(ch.races.items())),
        })
    m = {"genesis_hash": GENESIS, "checked_at": checked, "total_rows": sum(f["rows"] for f in files),
         "until": UNTIL.strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    tmp = os.path.join(outdir, "manifest.json.tmp")
    with open(tmp, "w") as f:
        json.dump(m, f, separators=(",", ":"))
    os.replace(tmp, os.path.join(outdir, "manifest.json"))
    return m


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()[:400]}")
    return r


def push(repo, rel, m):
    git(repo, "add", rel)
    if not git(repo, "status", "--porcelain", "--", rel).stdout.strip():
        return False
    msg = f"livelog: {m['total_rows']} rows, last {m['checked_at']}"
    git(repo, "commit", "-m", msg, "--", rel)
    for attempt in range(3):
        try:
            git(repo, "pull", "--rebase", "origin", "main")
            git(repo, "push", "origin", "HEAD:main")
            log("pushed:", msg)
            return True
        except RuntimeError as e:
            log("push retry", attempt, e)
            time.sleep(5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="dedicated clone to write into and push from")
    ap.add_argument("--backfill-only", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    rel = "site/data/livelog/us2026"
    outdir = os.path.join(a.repo, rel)
    os.makedirs(outdir, exist_ok=True)
    base = tunnel(a.repo)
    backfill(a.repo, outdir, base)
    chains = {s: Chain(os.path.join(outdir, fn)) for s, _, fn in SOURCES}
    m = write_manifest(outdir, chains, now_iso())
    if a.backfill_only:
        return
    last_push = 0.0
    dirty = True
    while True:
        t0 = time.time()
        nowdt = datetime.now(timezone.utc)
        try:
            base = tunnel(a.repo)
        except Exception as e:
            log("live_link read failed, keeping", base, e)
        added = 0
        for source, path, fn in SOURCES:
            try:
                tail = fetch(base, path + "timeline_tail.json")
            except Exception as e:
                log("fetch failed", path, repr(e)[:200])
                continue
            ch = chains[source]
            objs = []
            for race, rows in tail.items():
                if isinstance(rows, list):
                    objs += ch.new_rows(race, source, rows, "live")
            n = ch.append(objs)
            added += n
        m = write_manifest(outdir, chains, now_iso())
        if added:
            log("appended", added, "rows; total", m["total_rows"])
        done = nowdt >= UNTIL
        if not a.no_push and (done or time.time() - last_push >= PUSH_SECS):
            try:
                push(a.repo, rel, m)
                last_push = time.time()
                # pull may bring a newer incidents.json / live_link.json; nothing else to reload
            except Exception as e:
                log("push failed", repr(e)[:300])
        if done:
            log("reached", UNTIL, "- stopping")
            return
        time.sleep(max(1, LOOP_SECS - (time.time() - t0)))


if __name__ == "__main__":
    main()
