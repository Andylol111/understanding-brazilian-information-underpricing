"""Election-night check of site/groups.html against a replayed night (analysis/us_night_replay.py run ... --live-port 8797).

  PLAYWRIGHT_BROWSERS_PATH=/tmp/pw-browsers uv run --no-project --with playwright python analysis/groups_check.py \
      --site http://127.0.0.1:8792/ --live http://127.0.0.1:8797/ --minutes 35 --shots ~/Desktop/election_night_preview

Chromium and Firefox keep groups.html?live=... open while the count runs and sample window.__groups every 15 s:
  - no console errors, page errors or failed requests; the page's update counter rises (live updates flowing)
  - count-order rule: every displayed headline shift is blank when its group has no fully counted county, and otherwise
    rests only on counties at 95%+ counted. Each basis is re-derived here from the live units files and counties.json
    (the group's counties at the page's frame with f >= 950); the partial margin is shown only at 50%+ counted
  - the early frames (< 20% counted nationally): each group's displayed headline and its basis are recorded
Then election.html?id=us2026&live=... is opened in the same browser session (it must still load without errors).
Screenshots: 48_groups_<engine>_<stage>.png. Report: --report (JSON).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
GDIR = ROOT / "site" / "data" / "us2026_groups"
COMPLETE_AT = 950
MIN_N, MIN_EV = 3, 0.15        # a group's shift is shown only on 3+ complete counties holding 15%+ of its expected vote


def fetch(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=20) as r:
        return json.load(r)


class Truth:
    """Independent re-derivation of each group's complete counties from the live units files."""

    def __init__(self, live: str):
        self.live = live
        self.groups = json.loads((GDIR / "groups.json").read_text())
        c = json.loads((GDIR / "counties.json").read_text())
        self.cls = dict(zip(c["fips"], c["cls"]))
        self.dims = {d["id"]: (k, [g["id"] for g in d["groups"]]) for k, d in enumerate(self.groups["dimensions"])}

    def members(self, key: str, fips: list[str]) -> list[int]:
        parts = key.split(".")
        if parts[0] == "reg":
            st, rid = parts[1], parts[2]
            want = set(next(r["fips"] for r in self.groups["regions"][st] if r["id"] == rid))
            return [j for j, f in enumerate(fips) if f in want]
        k, ids = self.dims[parts[0]]
        gi = ids.index(parts[1])
        return [j for j, f in enumerate(fips) if (self.cls.get(f) or [None] * 5)[k] == gi]

    def load(self, races: dict[str, dict]) -> dict[str, tuple[list[str], dict]]:
        """{race: (county FIPS, the frame the page holds: the latest at or before its time t)} from the live files."""
        out = {}
        for rid, held in races.items():
            if held.get("t") is None:
                continue
            u = fetch(self.live + held["path"])
            frs = [f for f in u["frames"] if f.get("t") and _ms(f["t"]) <= held["t"]]
            if frs:
                out[rid] = ([x.rsplit("-", 1)[-1] for x in u["unit_ids"]], frs[-1])
        return out

    def complete(self, key: str, files: dict[str, tuple[list[str], dict]]) -> set[tuple[str, str]]:
        """{(race, fips)} of the group's counties at 95%+ counted (with a count) in those frames."""
        out = set()
        for rid, (fips, fr) in files.items():
            for j in self.members(key, fips):
                a = fr["a"][j]
                if (fr["f"][j] or 0) >= COMPLETE_AT and a is not None and a >= 0:
                    out.add((rid, fips[j]))
        return out


def build_of(page: str) -> str | None:
    """The window.__BUILD of a page in this checkout."""
    m = re.search(r'window\.__BUILD = "([^"]+)"', (ROOT / "site" / page).read_text())
    return m.group(1) if m else None


def _ms(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000


PROBE = """() => {
  const g = window.__groups;
  const cells = [...document.querySelectorAll('td.gr-hl')].map((td) => ({ key: td.closest('tr').dataset.g, n: +td.dataset.complete,
    text: td.firstChild?.textContent ?? '' }));
  const partial = document.querySelectorAll('tr.gr-partial').length;
  return { g: { ...g, headlines: g.headlines }, cells, partial };
}"""


async def engine_run(pw, eng: str, a, rep: dict, truth: Truth) -> None:
    r = rep.setdefault(eng, {"errors": [], "samples": [], "violations": [], "early": [], "shots": []})
    br = await getattr(pw, eng).launch()
    ctx = await br.new_context(viewport={"width": 1512, "height": 1000})
    pg = await ctx.new_page()
    pg.on("pageerror", lambda e: r["errors"].append(f"pageerror: {e}"))
    pg.on("console", lambda m: r["errors"].append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    pg.on("requestfailed", lambda q: r["errors"].append(f"requestfailed: {q.url} {q.failure}"))
    pg.on("response", lambda q: r["errors"].append(f"HTTP {q.status}: {q.url}") if q.status >= 400 else None)
    shots = Path(a.shots).expanduser()
    shots.mkdir(parents=True, exist_ok=True)

    async def shot(stage: str, page=pg):
        p = shots / f"48_groups_{eng}_{stage}.png"
        await page.screenshot(path=str(p), full_page=True)
        r["shots"].append(str(p))

    # before the night: the static page (baselines and makeup)
    await pg.goto(a.site + "groups.html")
    await pg.wait_for_function("window.__groups?.ready", timeout=60000)
    st = await pg.evaluate(PROBE)
    r["precount"] = {"tab": st["g"]["tab"], "cards": await pg.locator(".gr-card").count()}
    r["build"], r["build_expected"] = await pg.evaluate("window.__BUILD"), build_of("groups.html")
    if r["build"] != r["build_expected"]:   # another checkout's server on the port
        r["violations"].append(f"groups.html served build {r['build']}, this checkout has {r['build_expected']}")
    await shot("precount")

    url = a.site + "groups.html?live=" + a.live
    await pg.goto(url)
    await pg.wait_for_function("window.__groups?.ready", timeout=60000)
    t_end = time.time() + 60 * a.minutes
    stages = {"early": False, "mid": False}
    last_updates, last_counted, steady = -1, None, 0
    while time.time() < t_end:
        st = await pg.evaluate(PROBE)
        g = st["g"]
        counted = g.get("counted") or 0
        sample = {"t": round(time.time()), "counted": counted, "updates": g["updates"], "frameT": g["frameT"], "partialRows": st["partial"]}
        r["samples"].append(sample)
        # the displayed headline cells against the page's own figures
        heads = {h["key"]: h for h in g["headlines"]}
        for c in st["cells"]:
            h = heads.get(c["key"])
            if h is None:
                continue
            covered = h["nComplete"] >= MIN_N and (h.get("evShare") or 0) >= MIN_EV
            if not covered and c["text"] not in ("—", ""):
                r["violations"].append(f"{c['key']}: headline {c['text']!r} on {h['nComplete']} complete counties, "
                                       f"{(h.get('evShare') or 0):.0%} of expected vote (under {MIN_N} / {MIN_EV:.0%})")
            if covered and h["value"] is None:
                r["violations"].append(f"{c['key']}: no headline although {h['nComplete']} counties are complete")
            if any(b[2] < COMPLETE_AT for b in h["basis"]):
                r["violations"].append(f"{c['key']}: basis has a county under 95%: {[b for b in h['basis'] if b[2] < COMPLETE_AT][:3]}")
            if h["partialShown"] and (h["counted"] or 0) < 0.5:
                r["violations"].append(f"{c['key']}: partial margin shown at {h['counted']:.0%} counted")
        # independent re-derivation of the basis (every sample while early, else every 4th)
        if counted < 0.2 or len(r["samples"]) % 4 == 0:
            try:
                files = await asyncio.to_thread(truth.load, g.get("races") or {})
            except OSError as e:          # the replay's server has stopped: the count is over
                r["notes"] = r.get("notes", []) + [f"live files unavailable at {counted:.0%}: {e}"]
                break
            r["rederived"] = r.get("rederived", 0) + 1
            for h in g["headlines"]:
                truth_set = truth.complete(h["key"], files)
                page_set = {(b[0], b[1]) for b in h["basis"]}
                if page_set != truth_set:
                    r["violations"].append(f"{h['key']} at {counted:.0%}: page basis {len(page_set)} counties, live files {len(truth_set)}")
            if counted < 0.2 and counted > 0:
                r["early"].append({"counted": counted, "frameT": g["frameT"], "heads": [
                    {"key": h["key"], "shown": h["shown"], "value": h["value"], "nComplete": h["nComplete"], "n": h["n"],
                     "evShare": h.get("evShare"), "basis": [f"{b[0]}:{b[1]}@{b[2] / 10:.1f}%" for b in h["basis"]]} for h in g["headlines"]]})
        if counted > 0.03 and counted < 0.2 and not stages["early"]:
            stages["early"] = True
            await shot("early")
        if 0.4 <= counted < 0.75 and not stages["mid"]:
            stages["mid"] = True
            await shot("mid")
            # one race and the 2022 baseline, then back
            await pg.select_option("#gr-race", "ga-sen")
            await pg.wait_for_timeout(1500)
            await shot("mid_ga-sen")
            await pg.click("#gr-vs button[data-vs='o22']")
            await pg.click("#gr-dim button[data-dim='reg']")
            await pg.wait_for_timeout(800)
            await shot("mid_ga-sen_2022")
            await pg.goto(url)
            await pg.wait_for_function("window.__groups?.ready", timeout=60000)
        last_updates = g["updates"]
        steady = steady + 1 if counted == last_counted else 0
        last_counted = counted
        if counted >= 0.985 or (counted > 0.5 and steady >= 6):   # count finished, or no change for 90 s
            break
        await pg.wait_for_timeout(15000)
    r["updates_final"] = last_updates
    await shot("end")
    m = await br.new_context(viewport={"width": 390, "height": 844}, is_mobile=eng == "chromium", device_scale_factor=2)
    mp = await m.new_page()
    mp.on("pageerror", lambda e: r["errors"].append(f"mobile pageerror: {e}"))
    mp.on("console", lambda msg: r["errors"].append(f"mobile console.{msg.type}: {msg.text}") if msg.type == "error" else None)
    await mp.goto(url)
    await mp.wait_for_function("window.__groups?.ready", timeout=60000)
    await shot("end_mobile", mp)
    await m.close()
    # the US dashboard in the same session, with the same live base
    dp = await ctx.new_page()
    derr: list[str] = []
    dp.on("pageerror", lambda e: derr.append(f"pageerror: {e}"))
    dp.on("console", lambda msg: derr.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)
    dp.on("response", lambda q: derr.append(f"HTTP {q.status}: {q.url}") if q.status >= 400 else None)
    await dp.goto(a.site + "election.html?id=us2026&live=" + a.live)
    try:   # ready as site_check.py has it: title, race picker, readout (race or national)
        await dp.wait_for_function("""() => { const t = (s) => document.querySelector(s)?.textContent.trim() ?? '';
            return t('#title') && !t('#title').startsWith('Loading') && document.querySelectorAll('#race option').length > 0
              && (t('#readout').length + t('#nat-summary').length) > 0; }""", timeout=90000)
    except Exception as e:  # noqa: BLE001
        derr.append(f"dashboard not ready: {e}".splitlines()[0])
    await dp.wait_for_timeout(20000)  # one live refresh
    r["dashboard"] = {"errors": derr, "title": await dp.evaluate("document.querySelector('#title')?.textContent"),
                      "summary": await dp.evaluate("((document.querySelector('#nat-summary')?.textContent ?? '') + ' | ' + (document.querySelector('#readout')?.textContent ?? '')).trim().slice(0, 160)"),
                      "build": await dp.evaluate("window.__BUILD"), "build_expected": build_of("election.html")}
    if r["dashboard"]["build"] != r["dashboard"]["build_expected"]:
        r["violations"].append(f"election.html served build {r['dashboard']['build']}, this checkout has {r['dashboard']['build_expected']}")
    await dp.screenshot(path=str(shots / f"48_groups_{eng}_dashboard_same_session.png"))
    await br.close()


async def main_async(a) -> dict:
    rep: dict = {}
    truth = Truth(a.live)
    async with async_playwright() as pw:
        res = await asyncio.gather(*(engine_run(pw, e, a, rep, truth) for e in a.engines.split(",")), return_exceptions=True)
    for e, x in zip(a.engines.split(","), res):
        if isinstance(x, BaseException):
            rep.setdefault(e, {"errors": [], "samples": [], "violations": [], "early": [], "shots": []})["errors"].append(f"check aborted: {x!r}"[:400])
    return rep


def summary(rep: dict) -> dict:
    out = {}
    for eng, r in rep.items():
        early = [h for s in r["early"] for h in s["heads"] if h["value"] is not None]
        top = max(early, key=lambda h: abs(h["value"]), default=None)
        ups = [s["updates"] for s in r["samples"]]
        out[eng] = {"errors": r["errors"][:10], "violations": len(r["violations"]), "violation_examples": r["violations"][:5],
                    "samples": len(r["samples"]), "counted_first_last": [r["samples"][0]["counted"], r["samples"][-1]["counted"]] if r["samples"] else None,
                    "updates": [min(ups), max(ups)] if ups else None, "early_samples": len(r["early"]),
                    "early_heads_blank": sum(1 for s in r["early"] for h in s["heads"] if h["value"] is None),
                    "early_heads_shown": len(early),
                    "largest_early_headline": top and {k: top[k] for k in ("key", "shown", "nComplete", "n", "evShare")} | {"basis": top["basis"][:12]},
                    "precount": r.get("precount"), "dashboard": r.get("dashboard"), "shots": len(r["shots"]),
                    "rederived_samples": r.get("rederived", 0), "notes": r.get("notes", []),
                    "build": [r.get("build"), r.get("build_expected")]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default="http://127.0.0.1:8792/")
    ap.add_argument("--live", default="http://127.0.0.1:8797/")
    ap.add_argument("--minutes", type=float, default=35)
    ap.add_argument("--engines", default="chromium,firefox")
    ap.add_argument("--shots", default="~/Desktop/election_night_preview")
    ap.add_argument("--report", default="/tmp/groups_check.json")
    a = ap.parse_args()
    rep = asyncio.run(main_async(a))
    Path(a.report).write_text(json.dumps(rep, indent=1))
    s = summary(rep)
    print(json.dumps(s, indent=1))
    bad = any(v["errors"] or v["violations"] or not v["updates"] or v["updates"][1] <= v["updates"][0] or (v["dashboard"] or {}).get("errors")
              for v in s.values())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
