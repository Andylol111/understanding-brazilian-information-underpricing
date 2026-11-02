"""Election-night monitor for 2026-11-03 (read-only; runs on the Mac, never restarts anything).

Every run (default every 2 min with --loop, from 2026-11-03 17:30 UTC; --once for a single run):
  1. one `ssh oracle-slotbank` with read-only `sudo -n` commands: unit states + timer schedules, recorder poll markers
     (per-feed last_ok / status / fails), newest snapshot per feed, live model output freshness and per-race count
     progress, journal errors of the last 5 min, kernel OOM, disk, load, memory, the tunnel URL;
  2. the public tunnel URL: elections.json, summary.json, us2026 meta/timeline_tail/stats and a few race files
     (HTTP 200, valid JSON, Last-Modified age) and the GitHub Pages page;
  3. every 10 min a headless chromium load of election.html?id=us2026&live=<tunnel>: no console errors and
     "Live · updated" under 3 min old.
Output: one line per run in ~/brunder-night/monitor.log, ~/brunder-night/status.json, and `ALERT: <what>` lines on
stdout for everything stale/failed (`WARN: <what>` for the non-urgent). Alerts are time-aware: a unit that is
"waiting" for its timer is fine until the timer's start (+ grace).

    uv run python analysis/night_monitor.py --once
    uv run python analysis/night_monitor.py --loop --start 2026-11-03T17:30Z
    uv run python analysis/night_monitor.py --once --no-browser --tunnel-url http://127.0.0.1:8796/ --pages-url ...
Acknowledged known problems: one substring per line in ~/brunder-night/ack.txt downgrades matching ALERTs to WARN.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HOST = "oracle-slotbank"
PAGES = "https://andylol111.github.io/understanding-brazilian-information-underpricing/"
HOME = Path.home() / "brunder-night"
TAG = "us_midterms_2026"

# Thresholds (seconds unless noted)
TH = {
    "start_grace": 180,          # a unit may take this long after its timer fired
    "poll_warn": 120, "poll_alert": 300,          # .poll marker age (recorder cycle finished)
    "feed_ok_alert": 300,        # a feed's last_ok older than this (and it is failing)
    "feed_fails_alert": 3,       # consecutive failures
    "books_alert": 900,          # newest kalshi_books / polymarket_books / ws file
    "model_warn": 240, "model_alert": 360,         # model output file mtime / last row t
    "stall_warn": 1800,          # a race's counted unchanged this long while mid-count
    "disk_alert_pct": 85, "disk_warn_pct": 75,
    "load_alert_per_cpu": 2.0, "mem_alert_mb": 300,
    "journal_errors_warn": 1,    # error lines in the last 5 min per unit (Traceback / OOM always ALERT)
    "public_alert": 360,         # Last-Modified age of the public timeline_tail.json
    "browser_every": 600, "browser_max_age": 180,
    "http_timeout": 20,
}

NIGHT_TIMER = "brunder-election-night.timer"
# unit -> the timer that starts it
UNITS = {
    "brunder-snap-us": "brunder-snap-us.timer", "brunder-kalshi-us": "brunder-kalshi-us.timer",
    "brunder-pm-ws-us": "brunder-pm-ws-us.timer", "brunder-civic-us": "brunder-civic-us.timer",
    "brunder-us-live": NIGHT_TIMER, "brunder-us-house-live": NIGHT_TIMER, "brunder-us-national": NIGHT_TIMER,
    "brunder-us-civic-live": NIGHT_TIMER, "brunder-site": NIGHT_TIMER, "brunder-tunnel": NIGHT_TIMER,
    # added-source models (secondary, unregistered; docs/us_coverage_2026.md): own checkout /srv/brunder/usextra
    "brunder-us-extra-record": NIGHT_TIMER, "brunder-us-extra-live": NIGHT_TIMER,
}
# live model outputs under /srv/brunder/repo/site/data: name -> (path, is the race timeline, must be fresh)
OUTPUTS = {
    "us": "us2026/timeline_tail.json", "house": "us2026/house_live/timeline_tail.json",
    "national": "us2026/national_live/status.json", "civic": "civic/us2026/timeline_tail.json",
    "extra": "extra/us2026/timeline_tail.json",
}
MODEL_OF = {"us": "brunder-us-live", "house": "brunder-us-house-live", "national": "brunder-us-national",
            "civic": "brunder-us-civic-live", "extra": "brunder-us-extra-live"}
# the Mac-side recorder (deploy/mac_extra_rec_push.sh: Iowa/Colorado/Ottawa/Kenosha, whose hosts challenge the VM) pushes
# .poll/mac.json into the added archive with every push; it must be fresh while it is due to run
MAC_PUSH_WINDOW = ("2026-11-03T23:35:00Z", "2026-11-04T10:00:00Z")
MAC_PUSH_ALERT_S = 600

REMOTE = r"""
R=/srv/brunder/repo; S="sudo -n"; SN=$R/data/raw/snap; D=$R/site/data; T=$SN/__TAG__
ts() { [ -n "$1" ] && [ "$1" != "n/a" ] && date -d "$1" +%s || echo 0; }
echo "NOW $(date +%s)"
for u in __UNITS__; do
  eval "$($S systemctl show $u -p ActiveState -p SubState -p NRestarts -p Result -p ActiveEnterTimestamp | sed 's/=/="/;s/$/"/')"
  echo "U $u $ActiveState $SubState ${NRestarts:-0} $Result $(ts "$ActiveEnterTimestamp")"
  n=$($S journalctl -u $u --since '-5min' -o cat --no-pager 2>/dev/null | grep -c -E 'Traceback|MemoryError|Killed')
  e=$($S journalctl -u $u --since '-5min' -o cat --no-pager 2>/dev/null | grep -c -i -E 'error|exception')
  s=$($S journalctl -u $u --since '-5min' -o cat --no-pager 2>/dev/null | grep -i -E 'Traceback|error|exception|MemoryError' | tail -1 | cut -c1-160 | tr '\t' ' ')
  echo "J $u ${n:-0} ${e:-0} $s"
done
for t in __TIMERS__; do
  eval "$($S systemctl show $t -p ActiveState -p NextElapseUSecRealtime -p LastTriggerUSec | sed 's/=/="/;s/$/"/')"
  echo "T $t $ActiveState $(ts "$NextElapseUSecRealtime") $(ts "$LastTriggerUSec")"
done
echo "OOM $($S journalctl -k --since '-5min' --no-pager 2>/dev/null | grep -c -i -E 'out of memory|oom-kill')"
echo "DF $(df -P / | tail -1 | awk '{print $5, $4}')"
echo "LOAD $(cut -d' ' -f1-3 /proc/loadavg) $(nproc) $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)"
echo "TUNNEL $($S journalctl -u brunder-tunnel --since '3 days ago' -o cat --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)"
for n in snap kalshi civic; do
  f=$T/.poll/$n.json
  [ -f $f ] && echo "PJ $n $($S stat -c %Y $f) $($S cat $f | tr -d '\n')"
done
X=$SN/__TAG___extra
for n in extra mac; do
  f=$X/.poll/$n.json
  [ -f $f ] && echo "PJX $n $($S stat -c %Y $f)"
done
if [ -d $T ]; then
  $S tail -n 6000 $T/index.jsonl 2>/dev/null | awk '{match($0,/"key": "[^"]*"/); k=substr($0,RSTART+8,RLENGTH-9); match($0,/"t": "[^"]*"/); t=substr($0,RSTART+6,RLENGTH-7); L[k]=t} END{for(k in L)print "F",k,L[k]}'
fi
W=$SN/__TAG___ws
[ -d $W/polymarket_ws ] && echo "WS $($S find $W/polymarket_ws -maxdepth 1 -type f -printf '%T@\n' | sort -n | tail -1)"
for kv in __OUTPUTS__; do
  n=${kv%%=*}; p=$D/${kv#*=}
  if $S test -f $p; then echo "O $n $($S stat -c %Y $p)"; echo "OJ $n $($S head -c 4000000 $p | tr -d '\n')"; fi
done
for kv in house=$D/us2026/house_live/status.json national=$D/us2026/national_live/status.json; do
  n=${kv%%=*}; p=${kv#*=}
  if $S test -f $p; then echo "OS $n $($S stat -c %Y $p) $($S head -c 20000 $p | tr -d '\n')"; fi
done
"""


def remote_script() -> str:
    timers = sorted(set(UNITS.values()))
    return (REMOTE.replace("__TAG__", TAG).replace("__UNITS__", " ".join(UNITS)).replace("__TIMERS__", " ".join(timers))
            .replace("__OUTPUTS__", " ".join(f"{k}={v}" for k, v in OUTPUTS.items())))


def parse_remote(text: str) -> dict:
    """Remote output -> dict (pure; tested on synthetic text)."""
    r: dict = {"units": {}, "journal": {}, "timers": {}, "poll": {}, "files": {}, "out_mtime": {}, "out": {},
               "status": {}, "status_mtime": {}, "feed_t": {}}
    for ln in text.splitlines():
        p = ln.split(" ", 2)
        tag = p[0]
        try:
            if tag == "NOW":
                r["now"] = int(p[1])
            elif tag == "U":
                u = ln.split()
                r["units"][u[1]] = {"active": u[2], "sub": u[3], "restarts": int(u[4] or 0), "result": u[5], "enter": int(u[6])}
            elif tag == "J":
                u = ln.split(" ", 4)
                r["journal"][u[1]] = {"traceback": int(u[2]), "errors": int(u[3]), "sample": u[4] if len(u) > 4 else ""}
            elif tag == "T":
                u = ln.split()
                r["timers"][u[1]] = {"active": u[2], "next": int(u[3]), "last": int(u[4])}
            elif tag == "OOM":
                r["oom"] = int(p[1])
            elif tag == "DF":
                u = ln.split()
                r["disk_pct"], r["disk_avail_kb"] = int(u[1].rstrip("%")), int(u[2])
            elif tag == "LOAD":
                u = ln.split()
                r["load"], r["cpus"], r["mem_mb"] = float(u[1]), int(u[4]), int(u[5])
            elif tag == "TUNNEL":
                r["tunnel"] = ln.split(" ", 1)[1].strip() if " " in ln else ""
            elif tag == "PJ":
                u = ln.split(" ", 3)
                d = json.loads(u[3])
                d["mtime"] = int(u[2])
                r["poll"][u[1]] = d
            elif tag == "PJX":
                u = ln.split()
                r.setdefault("poll_extra", {})[u[1]] = int(u[2])
            elif tag == "F":
                u = ln.split()
                r["feed_t"][u[1]] = u[2]
            elif tag == "WS":
                r["ws_mtime"] = float(p[1])
            elif tag == "O":
                u = ln.split()
                r["out_mtime"][u[1]] = int(u[2])
            elif tag == "OJ":
                u = ln.split(" ", 2)
                r["out"][u[1]] = json.loads(u[2])
            elif tag == "OS":
                u = ln.split(" ", 3)
                r["status_mtime"][u[1]] = int(u[2])
                r["status"][u[1]] = json.loads(u[3])
        except (ValueError, IndexError, json.JSONDecodeError):
            r.setdefault("parse_errors", []).append(ln[:80])
    return r


def iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def unit_expectation(unit: str, r: dict, th: dict = TH) -> str:
    """'waiting' (timer not yet due), 'grace' (just due), 'run' (must be active), 'unknown'."""
    tm = r["timers"].get(UNITS[unit])
    if not tm or (tm["active"] != "active" and not tm["last"]):
        return "unknown"
    now = r["now"]
    if tm["next"] and tm["next"] > now:
        return "waiting"
    fired = tm["last"] or tm["next"]
    return "grace" if fired and now - fired < th["start_grace"] else "run"


def races_of(doc) -> dict[str, dict]:
    """timeline_tail.json -> {race: last row}; the doc's non-list keys (tail, since, rows) are not races."""
    if not isinstance(doc, dict):
        return {}
    if isinstance(doc.get("rows"), list):
        doc = {"national": doc["rows"]}
    return {k: v[-1] for k, v in doc.items() if isinstance(v, list) and v and isinstance(v[-1], dict)}


def evaluate(r: dict, prev: dict, th: dict = TH) -> tuple[list[str], list[str], dict]:
    """Remote data + previous status -> (alerts, warns, summary). Pure."""
    A: list[str] = []
    W: list[str] = []
    now = r["now"]
    info: dict = {"units": {}, "races": {}, "feeds": {}}
    for u in UNITS:
        s = r["units"].get(u)
        if s is None:
            A.append(f"{u}: no state from the VM")
            continue
        exp = unit_expectation(u, r, th)
        info["units"][u] = f"{s['active']}/{s['sub']}" + ("" if exp == "run" else f" ({exp})")
        tm = r["timers"].get(UNITS[u])
        if s["active"] == "failed" or s["result"] not in ("success", ""):
            A.append(f"{u} failed (result {s['result']})")
        elif s["sub"] == "auto-restart":
            A.append(f"{u} is crash-looping (auto-restart)")
        elif s["active"] not in ("active", "activating"):
            if exp == "run":
                A.append(f"{u} is {s['active']} but its timer fired")
            elif exp == "unknown" and not (tm and tm["next"] > now):
                A.append(f"{u}: timer {UNITS[u]} not armed")
        elif s["active"] == "activating" and exp == "run" and s["sub"] != "start":
            A.append(f"{u} stuck activating")
        p = (prev.get("units") or {}).get(u)
        if p and s["restarts"] > p.get("restarts", 0):
            A.append(f"{u} restarted ({p['restarts']}→{s['restarts']})")
        elif p and p.get("enter") and s["enter"] and s["enter"] != p["enter"] and p.get("active") == "active":
            A.append(f"{u} restarted (new start time)")
        j = r["journal"].get(u)
        if j:
            if j["traceback"]:
                A.append(f"{u} journal: {j['traceback']} traceback/OOM line(s) in 5 min: {j['sample']}")
            elif j["errors"] >= th["journal_errors_warn"]:
                W.append(f"{u} journal: {j['errors']} error line(s) in 5 min: {j['sample']}")
    if r.get("oom"):
        A.append(f"kernel OOM kill in the last 5 min ({r['oom']} line(s))")
    if "disk_pct" in r:
        if r["disk_pct"] >= th["disk_alert_pct"]:
            A.append(f"VM disk {r['disk_pct']}% full")
        elif r["disk_pct"] >= th["disk_warn_pct"]:
            W.append(f"VM disk {r['disk_pct']}% full")
        if r["load"] > th["load_alert_per_cpu"] * r["cpus"]:
            A.append(f"VM load {r['load']} on {r['cpus']} CPU(s)")
        if r["mem_mb"] < th["mem_alert_mb"]:
            A.append(f"VM free memory {r['mem_mb']} MB")
        info["vm"] = {"disk_pct": r["disk_pct"], "load": r["load"], "mem_mb": r["mem_mb"]}

    # recorders
    snap_state = {u: unit_expectation(u, r, th) for u in ("brunder-snap-us", "brunder-kalshi-us", "brunder-civic-us", "brunder-pm-ws-us")}
    for name, u in (("snap", "brunder-snap-us"), ("kalshi", "brunder-kalshi-us"), ("civic", "brunder-civic-us")):
        if snap_state[u] != "run":
            continue
        pj = r["poll"].get(name)
        if not pj:
            A.append(f"{name} recorder: no poll marker")
            continue
        age = now - max(pj["mtime"], iso(pj["done"]) if pj.get("done") else 0)
        info["feeds"][f"poll_{name}_age_s"] = int(age)
        if age > th["poll_alert"]:
            A.append(f"{name} recorder poll marker {int(age)} s old")
        elif age > th["poll_warn"]:
            W.append(f"{name} recorder poll marker {int(age)} s old")
    # added-source recorder (server) and the Mac push (IA/CO/Ottawa/Kenosha): marker mtimes in the added archive
    px = r.get("poll_extra") or {}
    if unit_expectation("brunder-us-extra-record", r, th) == "run":
        if "extra" not in px:
            A.append("added-source recorder: no poll marker")
        else:
            age = now - px["extra"]
            info["feeds"]["poll_extra_age_s"] = int(age)
            if age > th["poll_alert"]:
                A.append(f"added-source recorder poll marker {int(age)} s old")
    if iso(MAC_PUSH_WINDOW[0]) <= now <= iso(MAC_PUSH_WINDOW[1]):
        if "mac" not in px:
            A.append("Mac push (IA/CO/Ottawa/Kenosha): no marker on the server; is deploy/mac_extra_rec_push.sh running?")
        else:
            age = now - px["mac"]
            info["feeds"]["mac_push_age_s"] = int(age)
            if age > MAC_PUSH_ALERT_S:
                A.append(f"Mac push (IA/CO/Ottawa/Kenosha) marker {int(age)} s old: Mac asleep/offline or the push loop stopped")
    pj = r["poll"].get("snap") if snap_state["brunder-snap-us"] == "run" else None
    if pj:
        bad, unpub, ok = [], [], 0
        for k, f in (pj.get("feeds") or {}).items():
            st, fails = f.get("last_status"), f.get("fails", 0)
            last = iso(f["last_ok"]) if f.get("last_ok") else None
            if not fails:
                ok += 1
            elif st in (404, 410):
                unpub.append(k)
            elif fails >= th["feed_fails_alert"] or (last and now - last > th["feed_ok_alert"]):
                bad.append(f"{k} [{st}, {fails} fails" + (f", last ok {int(now - last)} s ago" if last else ", never ok") + "]")
        info["feeds"].update(ok=ok, not_published=len(unpub), failing=len(bad))
        for b in bad:
            A.append(f"feed {b}")
        if unpub:
            W.append(f"{len(unpub)} feed(s) not published yet (404): {', '.join(sorted(unpub)[:6])}" + (" …" if len(unpub) > 6 else ""))
    for k, u in (("kalshi_books", "brunder-kalshi-us"), ("polymarket_books", "brunder-snap-us")):
        if snap_state[u] == "run" and k in r["feed_t"]:
            age = now - iso(r["feed_t"][k])
            if age > th["books_alert"]:
                A.append(f"{k}: newest snapshot {int(age // 60)} min old")
    if snap_state["brunder-pm-ws-us"] == "run":
        if "ws_mtime" not in r:
            A.append("pm-ws: no polymarket_ws file")
        elif now - r["ws_mtime"] > th["books_alert"]:
            A.append(f"pm-ws: newest file {int((now - r['ws_mtime']) // 60)} min old")
    if r["feed_t"]:
        info["feeds"]["newest_snapshot_age_s"] = int(now - max(iso(t) for t in r["feed_t"].values()))

    # live models
    for name, path in OUTPUTS.items():
        u = MODEL_OF[name]
        exp = unit_expectation(u, r, th)
        if exp != "run":
            continue
        mt = r["out_mtime"].get(name)
        if mt is None:
            A.append(f"{u}: output {path} missing")
            continue
        mt = max(mt, r["status_mtime"].get(name, 0))
        age = now - mt
        doc = r["out"].get(name)
        rows = races_of(doc)
        newest = max((iso(v["t"]) for v in rows.values() if v.get("t")), default=None)
        info["races"][f"{name}_file_age_s"] = int(age)
        if age > th["model_alert"]:
            A.append(f"{u}: {path} not written for {int(age)} s")
        elif age > th["model_warn"]:
            W.append(f"{u}: {path} not written for {int(age)} s")
        if newest is not None and now - newest > th["model_alert"] + 120:
            A.append(f"{u}: newest timeline row is {int((now - newest) // 60)} min old")
        errs = (r["status"].get(name) or {}).get("errors")
        if errs:
            A.append(f"{u}: status.json errors: {str(errs)[:120]}")
        pr = (prev.get("races") or {}).get(name, {})
        cur = {}
        for race, row in rows.items():
            c = row.get("counted")
            key = [c, row.get("counties_reported")]
            old = pr.get(race)
            since = old["since"] if old and old["key"] == key else now
            cur[race] = {"key": key, "since": since}
            if c is not None and now - since > th["stall_warn"] and key[0] not in (0,):
                W.append(f"{race}: counted unchanged for {int((now - since) // 60)} min ({c})")
        info["races"][name] = cur
        counting = [v["counted"] for v in rows.values() if v.get("counted") is not None]
        info["races"][f"{name}_summary"] = {"n": len(rows), "counting": len(counting),
                                           "median_counted": statistics.median(counting) if counting else None}
    return A, W, info


def evaluate_public(pub: dict | None, th: dict = TH) -> tuple[list[str], list[str]]:
    """pub: {url: {status, age_s|None, json_ok}} + 'pages'. Pure."""
    A: list[str] = []
    W: list[str] = []
    for url, d in (pub or {}).items():
        if d.get("status") != 200:
            A.append(f"public {url}: HTTP {d.get('status')} {d.get('error', '')}".strip())
        elif not d.get("json_ok", True):
            A.append(f"public {url}: not valid JSON")
        elif d.get("fresh") and d.get("age_s") is not None and d["age_s"] > th["public_alert"]:
            A.append(f"public {url}: Last-Modified {int(d['age_s'])} s old")
    return A, W


def evaluate_browser(b: dict | None, th: dict = TH) -> tuple[list[str], list[str]]:
    A: list[str] = []
    if b is None:
        return A, []
    if b.get("error"):
        return [f"browser check failed: {b['error'][:200]}"], []
    for e in (b.get("console_errors") or [])[:5]:
        A.append(f"election.html console error: {e[:160]}")
    if b.get("live_age_s") is None:
        A.append(f"election.html: no 'Live · updated' text (shows {b.get('live_text')!r})")
    elif b["live_age_s"] > th["browser_max_age"]:
        A.append(f"election.html: 'Live · updated {b['live_age_s']} s ago'")
    return A, []


# ---------------------------------------------------------------- collectors (I/O)

def ssh_collect(timeout: int = 90) -> dict:
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST, "sh -s"],
                       input=remote_script(), capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 and "NOW" not in p.stdout:
        raise RuntimeError(f"ssh failed ({p.returncode}): {p.stderr.strip()[:200]}")
    return parse_remote(p.stdout)


def http_get(url: str, timeout: float) -> tuple[int | None, bytes, dict, str]:
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "brunder-night-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            body = f.read()
            if f.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return f.status, body, dict(f.headers), ""
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(e.headers or {}), ""
    except Exception as e:
        return None, b"", {}, f"{type(e).__name__}: {str(e)[:80]}"


def fetch_public(base: str, races: list[str], now: float, th: dict = TH) -> dict:
    base = base if base.endswith("/") else base + "/"
    files = [("elections.json", False), ("summary.json", False), ("us2026/meta.json", False),
             ("us2026/timeline_tail.json", True), ("us2026/stats.json", False)] + [(f"us2026/units/{x}.json", False) for x in races]
    out = {}
    for path, fresh in files:
        st, body, h, err = http_get(base + path, th["http_timeout"])
        ok = True
        if st == 200:
            try:
                json.loads(body)
            except ValueError:
                ok = False
        age = None
        if h.get("Last-Modified") or h.get("last-modified"):
            try:
                age = now - parsedate_to_datetime(h.get("Last-Modified") or h["last-modified"]).timestamp()
            except (TypeError, ValueError):
                pass
        out[path] = {"status": st, "json_ok": ok, "age_s": age, "fresh": fresh, "error": err}
    return out


def pages_check(local_site: Path, th: dict = TH) -> tuple[list[str], list[str]]:
    A, W = [], []
    for page in ("election.html?id=us2026", "data/elections.json"):
        st, body, h, err = http_get(PAGES + page, th["http_timeout"])
        if st != 200:
            A.append(f"GitHub Pages {page}: HTTP {st} {err}".strip())
    st, body, h, err = http_get(PAGES + "election.js", th["http_timeout"])
    loc = local_site / "election.js"
    if st == 200 and loc.exists() and hashlib.sha256(body).digest() != hashlib.sha256(loc.read_bytes()).digest():
        W.append("GitHub Pages election.js differs from this checkout")
    return A, W


BROWSER_JS = r"""
import json, re, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {"console_errors": [], "live_text": None, "live_age_s": None}
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        # a 404 is a missing optional file by design (the page treats only 404 as optional); other failures are errors
        pg.on("console", lambda m: out["console_errors"].append(m.text) if m.type == "error" and "status of 404" not in m.text else None)
        pg.on("response", lambda r: out["console_errors"].append(f"HTTP {r.status} {r.url[:110]}") if r.status >= 400 and r.status != 404 else None)
        pg.on("pageerror", lambda e: out["console_errors"].append(str(e)))
        pg.goto(url, wait_until="load", timeout=60000)
        try:
            pg.wait_for_function("document.querySelector('#live') && /updated/.test(document.querySelector('#live').textContent)", timeout=45000)
        except Exception:
            pass
        t = pg.evaluate("document.querySelector('#live') ? document.querySelector('#live').textContent : null")
        out["live_text"] = t
        out["build"] = pg.evaluate("window.__BUILD || null")
        m = t and re.search(r"Live · updated (\d+) (s|min) ago", t)
        if m:
            out["live_age_s"] = int(m.group(1)) * (60 if m.group(2) == "min" else 1)
        b.close()
except Exception as e:
    out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
print(json.dumps(out))
"""


def browser_check(url: str) -> dict:
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/tmp/pw-browsers")
    p = subprocess.run(["uv", "run", "--no-project", "--with", "playwright", "python", "-c", BROWSER_JS, url],
                       capture_output=True, text=True, timeout=150, env=env)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": (p.stderr or p.stdout or "no output").strip()[-200:]}


# ---------------------------------------------------------------- run

def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def run_once(args, now_dt: datetime | None = None) -> int:
    global HOME
    if args.dry_run:
        HOME = Path.home() / "brunder-night" / "dry"
    HOME.mkdir(parents=True, exist_ok=True)
    now_dt = now_dt or datetime.now(timezone.utc)
    prev = load_json(HOME / "status.json")
    A: list[str] = []
    W: list[str] = []
    status: dict = {"t": now_dt.isoformat(timespec="seconds")}
    r = None
    if not args.dry_run:
        try:
            r = ssh_collect()
        except Exception as e:
            A.append(f"VM unreachable / ssh failed: {str(e)[:160]}")
    pub = None
    browser = prev.get("browser")
    if r:
        a, w, info = evaluate(r, prev.get("eval") or {})
        A += a
        W += w
        status["eval"] = {"units": {u: {"restarts": s["restarts"], "enter": s["enter"], "active": s["active"]}
                                    for u, s in r["units"].items()}, "races": info["races"]}
        status["info"] = {"units": info["units"], "feeds": info["feeds"], "vm": info.get("vm"),
                          "races": {k: v for k, v in info["races"].items() if k.endswith(("_summary", "_age_s"))}}
        tunnel = args.tunnel_url or r.get("tunnel") or ""
        status["tunnel"] = tunnel
        site_run = unit_expectation("brunder-site", r) == "run" and unit_expectation("brunder-tunnel", r) == "run"
        if site_run and not tunnel:
            A.append("no tunnel URL in the brunder-tunnel journal")
        if prev.get("tunnel") and tunnel and prev["tunnel"] != tunnel:
            A.append(f"tunnel URL changed: {prev['tunnel']} → {tunnel} (re-publish the link)")
        if tunnel and (site_run or args.tunnel_url):
            races = [k for k in (races_of(r["out"].get("us")) or {})][:3] or ["ga-sen", "tx-sen", "ca-gov"]
            pub = fetch_public(tunnel, races, now_dt.timestamp())
            a, w = evaluate_public(pub)
            A += a
            W += w
            status["public"] = {k: {"status": v["status"], "age_s": v["age_s"]} for k, v in pub.items()}
            due = not prev.get("browser_t") or now_dt.timestamp() - prev["browser_t"] >= TH["browser_every"]
            if due and not args.no_browser:
                browser = browser_check(f"{args.pages_url}election.html?id=us2026&live={tunnel}")
                status["browser_t"] = now_dt.timestamp()
                a, w = evaluate_browser(browser)
                A += a
                status["browser_alerts"] = a
            else:
                status["browser_t"] = prev.get("browser_t")
                status["browser_alerts"] = prev.get("browser_alerts") or []
                A += status["browser_alerts"]
            status["browser"] = browser
        else:
            status["browser_t"] = prev.get("browser_t")
    if args.dry_run:                       # local replay: the live data server + a local page only, no VM, no Pages
        if not args.tunnel_url:
            sys.exit("--dry-run needs --tunnel-url (the local replay's live server)")
        st, body, _, err = http_get(args.tunnel_url.rstrip("/") + "/us2026/timeline_tail.json", TH["http_timeout"])
        races = [k for k, v in (json.loads(body) if st == 200 else {}).items() if isinstance(v, list)][:3]
        pub = fetch_public(args.tunnel_url, races, now_dt.timestamp())
        a, w = evaluate_public(pub)
        A += a
        status["public"] = {k: {"status": v["status"], "age_s": v["age_s"]} for k, v in pub.items()}
        if not args.no_browser:
            browser = browser_check(f"{args.pages_url}election.html?id=us2026&live={args.tunnel_url}")
            status["browser"] = browser
            A += evaluate_browser(browser)[0]
    else:
        try:
            a, w = pages_check(Path(__file__).resolve().parent.parent / "site")
            A += a
            W += w
        except Exception as e:
            W.append(f"pages check failed: {e}")
    ack = [x.strip() for x in (HOME / "ack.txt").read_text().splitlines() if x.strip()] if (HOME / "ack.txt").exists() else []
    acked = [x for x in A if any(k in x for k in ack)]
    A = [x for x in A if x not in acked]
    W = acked + W
    status["alerts"], status["warns"] = A, W
    summary = ""
    if r:
        i = status["info"]
        summary = (f"units {sum(1 for s in r['units'].values() if s['active'] == 'active')}/{len(UNITS)} active"
                   f" | feeds ok {i['feeds'].get('ok', '-')} fail {i['feeds'].get('failing', '-')}"
                   f" | vm disk {r.get('disk_pct', '-')}% load {r.get('load', '-')}")
        us = i["races"].get("us_summary")
        if us:
            summary += f" | us races {us['n']} counting {us['counting']}"
    line = f"{status['t']} {'ALERT' if A else 'OK'} alerts={len(A)} warns={len(W)} {summary}"
    if A:
        line += " | " + "; ".join(A)[:600]
    with (HOME / "monitor.log").open("a") as f:
        f.write(line + "\n")
    tmp = HOME / ".status.tmp"
    tmp.write_text(json.dumps(status, indent=1, default=str))
    os.replace(tmp, HOME / "status.json")
    for a in A:
        print(f"ALERT: {a}", flush=True)
    for w in W:
        print(f"WARN: {w}", flush=True)
    print(line, flush=True)
    return 1 if A else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--start", default="2026-11-03T17:30Z", help="--loop waits for this UTC time")
    ap.add_argument("--every", type=int, default=120)
    ap.add_argument("--tunnel-url", help="override the tunnel URL read from the VM journal (e.g. a local replay server)")
    ap.add_argument("--pages-url", default=PAGES, help="where election.html is served for the browser check")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="against a local replay: --tunnel-url + --pages-url only (no ssh, no Pages); writes to HOME/dry/")
    args = ap.parse_args()
    if not (args.once or args.loop):
        ap.error("--once or --loop")
    if args.once:
        sys.exit(run_once(args))
    start = iso(args.start)
    while True:
        wait = start - time.time()
        if wait > 0:
            time.sleep(min(wait, 60))
            continue
        t0 = time.time()
        try:
            run_once(args)
        except Exception as e:
            print(f"ALERT: monitor itself crashed: {type(e).__name__}: {e}", flush=True)
        time.sleep(max(1.0, args.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
