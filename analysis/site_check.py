"""Check every published site page in Chromium, Firefox, and WebKit.

The script serves site/ itself unless --base is supplied. It treats JavaScript page errors,
permanent loading text, empty race/readout UI, and an undrawn map as failures. Console errors,
failed requests, and HTTP errors are captured and printed with the affected page.

At the desktop size it also runs the map checks of analysis/map_audit.py on the homepage's world
map and country views and on every election's map (each level, mode, first and final frame):
fills, final-frame winners, hover-card names, stray outlines and the legend.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import functools
import http.server
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import Browser, Page, async_playwright

import map_audit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ELECTIONS = (
    "br2026r2",
    "br2026r1",
    "br2026r2gov",
    "br2022r1",
    "br2022r2",
    "il2026",
    "hu2026",
    "nz2023",
    "nz2020",
    "pl2025r2",
    "pl2025r1",
    "de2025",
    "mx2024",
    "ca2025",
    "nl2025",
    "qc2026",
    "us2026",
)
VIEWPORTS = {"desktop": {"width": 1512, "height": 900}, "mobile": {"width": 390, "height": 844}}


@dataclass(frozen=True)
class Case:
    name: str
    path: str
    kind: str


@dataclass
class Result:
    engine: str
    viewport: str
    case: Case
    seconds: float
    failures: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        pass


def cases() -> list[Case]:
    out = [Case("index", "index.html", "index"), Case("brazil-2026", "brazil-2026.html", "article")]
    out.extend(Case(eid, f"election.html?{urlencode({'id': eid})}", "election") for eid in ELECTIONS)
    # Exercise the US county map as well as its default national state map.
    out.append(Case("us2026-ca-gov", f"election.html?{urlencode({'id': 'us2026', 'race': 'ca-gov'})}", "election"))
    # The US page's House office (the district layer) and an old hub address, now a redirect.
    out.append(Case("us2026-house", f"election.html?{urlencode({'id': 'us2026', 'office': 'house'})}", "election"))
    out.append(Case("us-hub-redirect", "us.html?tab=markets&race=tx-sen", "election"))
    # Map checks (desktop only): the homepage's maps, then every election's map
    out.append(Case("maps-home", "", "maps"))
    out.extend(Case(f"maps-{e['id']}", e["id"], "maps") for e in map_audit.elections() if not map_audit.mapless(e["id"]))
    return out


@contextlib.contextmanager
def local_server() -> Any:
    handler = functools.partial(QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def state(page: Page, kind: str) -> dict[str, Any]:
    return await page.evaluate(
        """kind => {
          const text = (s) => document.querySelector(s)?.textContent.trim() ?? "";
          const visiblePaths = [...document.querySelectorAll("#map svg path, #national svg path")]
            .filter((p) => p.getAttribute("d") && p.getClientRects().length && getComputedStyle(p).visibility !== "hidden").length;
          const visibleCanvas = [...document.querySelectorAll("#map canvas")]
            .some((c) => c.getClientRects().length && c.width > 10 && c.height > 10 && getComputedStyle(c).visibility !== "hidden");
          return {
            loading: [...document.querySelectorAll("h1, p, td")].some((e) => /^(Loading…|Loading the map…)$/.test(e.textContent.trim())),
            loadError: text("#load-error"),
            title: text("#title"),
            rows: document.querySelectorAll("#overview tbody tr").length,
            homeCards: document.querySelectorAll("#money .money-cell").length,
            raceOptions: document.querySelectorAll("#race option").length,
            readout: text("#readout").length + text("#nat-summary").length,
            articlePaths: document.querySelectorAll("main svg path").length,
            visiblePaths,
            visibleCanvas,
            painted: window.__dashPerf?.firstRenderMs != null,
            kind,
          };
        }""",
        kind,
    )


def ready(s: dict[str, Any], mapless: bool = False) -> bool:
    if s["loadError"] or s["loading"]:
        return False
    if s["kind"] == "index":
        return s["rows"] >= len(ELECTIONS) and s["homeCards"] > 0
    if s["kind"] == "article":
        return s["articlePaths"] > 10
    return bool(s["title"] and s["raceOptions"] and s["readout"] and (mapless or s["visiblePaths"] or (s["visibleCanvas"] and s["painted"])))


async def canvas_has_drawing(page: Page) -> bool:
    canvases = page.locator("#map canvas")
    for i in range(await canvases.count()):
        canvas = canvases.nth(i)
        box = await canvas.bounding_box()
        if not box or box["width"] < 10 or box["height"] < 10 or not await canvas.is_visible():
            continue
        png = await canvas.screenshot(type="png")
        source = "data:image/png;base64," + base64.b64encode(png).decode()
        stats = await page.evaluate(
            """async source => {
              const image = new Image();
              image.src = source;
              await image.decode();
              const canvas = document.createElement("canvas");
              canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
              const context = canvas.getContext("2d");
              context.drawImage(image, 0, 0);
              const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
              const colours = new Set();
              for (let y = 0; y < canvas.height; y += 4) for (let x = 0; x < canvas.width; x += 4) {
                const j = 4 * (y * canvas.width + x);
                colours.add(`${pixels[j] >> 3},${pixels[j + 1] >> 3},${pixels[j + 2] >> 3},${pixels[j + 3] >> 5}`);
                if (colours.size >= 6) return colours.size;
              }
              return colours.size;
            }""",
            source,
        )
        if stats >= 6:
            return True
    return False


HOME_CHECK = r"""async () => {
  const fails = [];
  const list = await (await fetch("data/elections.json")).json();
  const rowIds = new Set([...document.querySelectorAll("#overview tbody tr[data-id]")].map((r) => r.dataset.id));
  const cardIds = new Set([...document.querySelectorAll("#money .money-cell")].map((r) => r.dataset.id));
  for (const e of list) {
    if (!rowIds.has(e.id)) fails.push(`no list row for ${e.id}`);
    if (!cardIds.has(e.id)) fails.push(`no $10,000 card for ${e.id}`);
  }
  // one list: every row has the same cells; descriptions fit on their lines and in 140 characters
  const cols = new Set([...document.querySelectorAll("#overview tbody tr[data-id]")].map((r) => r.children.length));
  if (cols.size !== 1) fails.push(`rows differ in cell count: ${[...cols]}`);
  if (document.querySelectorAll("#overview thead th").length !== [...cols][0]) fails.push("header and rows differ in column count");
  for (const c of document.querySelectorAll("#overview .e-head")) if (!c.textContent.trim()) fails.push(`no headline for ${c.parentElement.dataset.id}`);
  for (const th of document.querySelectorAll("#overview thead th[data-help]")) if (!th.querySelector(".help") || !th.querySelector(".help-pop").textContent.trim()) fails.push(`column ${th.textContent.trim()} has no help`);
  const mx = document.querySelector('#overview tr[data-id="mx2024"] .e-head')?.textContent ?? "";
  if (!/Decided before the count/i.test(mx) || !/market 0\.96 two days before the vote/i.test(mx)) fails.push(`Mexico lead was not replaced by the pre-count decision: ${mx}`);
  if (!/contested market/i.test(document.querySelector("#ev-lead-lead")?.textContent ?? "")) fails.push("lead chart does not say it is restricted to contested markets");
  for (const d of document.querySelectorAll("#overview .e-desc")) {
    const t = d.textContent.trim();
    if (t.length > 140) fails.push(`description over 140 characters: ${t.slice(0, 40)}`);
    if (/…|\.\.\.$/.test(t) || d.scrollHeight > d.clientHeight + 1) fails.push(`description is cut off: ${t.slice(0, 40)}`);
  }
  // the $10,000 cards sit on one grid: equal heights, and the same parts at the same height, within a row
  const cards = [...document.querySelectorAll("#money .money-cell")];
  const rows = new Map();
  for (const c of cards) { const k = Math.round(c.getBoundingClientRect().top + scrollY); rows.set(k, [...(rows.get(k) ?? []), c]); }
  for (const [top, group] of rows) {
    const hs = group.map((c) => Math.round(c.getBoundingClientRect().height));
    if (Math.max(...hs) - Math.min(...hs) > 1) fails.push(`cards in the row at ${top}px differ in height: ${hs}`);
    for (const sel of [".mc-main", ".money-chart", ".mc-facts"]) {
      const ys = group.map((c) => Math.round(c.querySelector(sel).getBoundingClientRect().top));
      if (Math.max(...ys) - Math.min(...ys) > 1) fails.push(`${sel} not aligned in the row at ${top}px: ${ys}`);
    }
  }
  if (innerWidth > 900 && cards.length > 1 && rows.size === cards.length) fails.push("the $10,000 cards are not in a multi-column grid");
  for (const id of ["chart-night", "chart-lead", "chart-error", "chart-coverage", "chart-reliability"]) {
    const el = document.getElementById(id);
    if (!el || !el.querySelector("svg")) fails.push(`${id} has no drawing`);
  }
  if (document.querySelectorAll("#chart-error .panel svg").length < 2) fails.push("projection chart has no per-election panels");
  if (document.documentElement.scrollWidth > innerWidth + 1) fails.push(`the page scrolls sideways (${document.documentElement.scrollWidth} > ${innerWidth})`);
  return fails;
}"""


async def check_home(page: Page) -> list[str]:
    # the one-night and lead charts draw once their files are read
    try:
        await page.wait_for_function(
            """() => document.querySelector('#chart-night svg') && document.querySelector('#chart-lead svg')
              && [...document.querySelectorAll('#overview .e-head')].every((c) => c.textContent.trim())""",
            timeout=15000,
        )
    except Exception:
        pass
    return await page.evaluate(HOME_CHECK)


async def check_home_card(page: Page) -> list[str]:
    """Hover New Zealand on the homepage map: a short card (at most 200 px tall, 280 px wide) that stays off the country."""
    if not await page.evaluate("document.querySelector('#wm-q') && [...document.querySelectorAll('.wm-list button')].some((b) => b.dataset.code === 'NZ')"):
        return []
    await page.wait_for_function("window.__world?.probe('NZ')", timeout=15000)
    probe = await page.evaluate("window.__world.probe('NZ')")
    await page.evaluate("document.querySelector('.wm-box').scrollIntoView({block: 'center'})")
    await page.wait_for_timeout(200)
    canvas = await page.evaluate("(() => { const r = document.querySelector('.wm-canvas').getBoundingClientRect(); return [r.left, r.top]; })()")
    x, y = canvas[0] + probe["at"][0], canvas[1] + probe["at"][1]
    await page.mouse.move(x - 40, y - 40)
    await page.mouse.move(x, y, steps=4)
    await page.wait_for_timeout(300)
    card = await page.evaluate(
        "(() => { const c = document.querySelector('.wm-card'); if (!c || c.hidden) return null; const r = c.getBoundingClientRect(); return { left: r.left, top: r.top, w: r.width, h: r.height, text: c.innerText, pe: getComputedStyle(c).pointerEvents }; })()"
    )
    fails: list[str] = []
    if not card:
        return ["no hover card over New Zealand on the homepage map"]
    if card["h"] > 200 or card["w"] > 280:
        fails.append(f"NZ hover card is {card['w']:.0f}x{card['h']:.0f} px (limit 280x200)")
    bx0, by0, bx1, by1 = probe["box"]
    box = (canvas[0] + bx0, canvas[1] + by0, canvas[0] + bx1, canvas[1] + by1)
    if card["left"] < box[2] and card["left"] + card["w"] > box[0] and card["top"] < box[3] and card["top"] + card["h"] > box[1]:
        fails.append(f"NZ hover card covers the country: card {card['left']:.0f},{card['top']:.0f},{card['w']:.0f}x{card['h']:.0f} over {box}")
    if card["pe"] != "none":
        fails.append("NZ hover card takes pointer events")
    if len(card["text"].strip().splitlines()) > 7:
        fails.append(f"NZ hover card has too many lines: {card['text'][:80]!r}")
    await page.mouse.move(5, 5)
    return fails


async def check_page(
    browser: Browser,
    engine: str,
    viewport_name: str,
    viewport: dict[str, int],
    case: Case,
    base: str,
    timeout: float,
    screenshots: Path | None,
    semaphore: asyncio.Semaphore,
) -> Result:
    async with semaphore:
        started = time.monotonic()
        result = Result(engine, viewport_name, case, 0)
        context = await browser.new_context(viewport=viewport)
        page = await context.new_page()
        page.on("pageerror", lambda error: result.failures.append(f"page error: {error}"))
        page.on("console", lambda message: result.diagnostics.append(f"console.{message.type}: {message.text}") if message.type == "error" else None)
        page.on("requestfailed", lambda request: result.diagnostics.append(f"request failed: {request.url} ({request.failure})"))
        page.on("response", lambda response: result.diagnostics.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
        try:
            if case.kind == "maps":
                entry = next((e for e in map_audit.elections() if e["id"] == case.path), None)
                rows = await (map_audit.audit_home(page, base, None, engine) if entry is None else map_audit.audit_election(page, base, entry, None, engine))
                result.failures.extend(f"{r.map} {r.check}: {r.detail}" for r in rows if not r.ok)
                return result
            await page.goto(base + case.path, wait_until="load", timeout=timeout * 1000)
            deadline = time.monotonic() + timeout
            current = await state(page, case.kind)
            while not ready(current, case.kind == "election" and map_audit.mapless(case.path.split("id=")[-1].split("&")[0])) and time.monotonic() < deadline:
                await page.wait_for_timeout(250)
                current = await state(page, case.kind)
            if current["loadError"]:
                result.failures.append(current["loadError"])
            if current["loading"]:
                result.failures.append("page remained on a Loading… placeholder")
            if case.kind == "index" and current["rows"] < len(ELECTIONS):
                result.failures.append(f"election list has only {current['rows']} rows")
            elif case.kind == "index":
                result.failures.extend(await check_home(page))
                if viewport_name == "desktop":
                    result.failures.extend(await check_home_card(page))
            elif case.kind == "article" and current["articlePaths"] <= 10:
                result.failures.append("article charts were not drawn")
            elif case.kind == "election":
                if not current["title"] or current["title"].startswith("Loading"):
                    result.failures.append("election title was not set")
                if not current["raceOptions"]:
                    result.failures.append("race picker is empty")
                if not current["readout"]:
                    result.failures.append("race/national readout is empty")
                if current["visiblePaths"] == 0 and not map_audit.mapless(case.path.split("id=")[-1].split("&")[0]) and not await canvas_has_drawing(page):
                    result.failures.append("map has neither visible SVG paths nor non-background canvas pixels")
                if case.path == "election.html?id=pl2025r2":
                    replay = await page.evaluate(
                        """() => ({ now: window.__dashAudit?.time?.(), domain: window.__dashAudit?.domain?.(),
                          readout: document.querySelector('#readout').innerText,
                          hint: document.querySelector('#replay-hint').innerText,
                          decidedTags: document.querySelectorAll('#pane-races .label.decided').length })"""
                    )
                    if not replay["domain"] or not (replay["domain"][0] < replay["now"] < replay["domain"][1]):
                        result.failures.append(f"pl2025r2 did not open mid-night: {replay}")
                    if not replay["hint"] or not re.search(r"Chance.*(?:0\.[1-9]|[1-9]\.\d)", replay["readout"], re.S):
                        result.failures.append(f"pl2025r2 replay hint/readout is missing or all-zero: {replay}")
                    if not replay["decidedTags"]:
                        result.failures.append("pl2025r2 Races table has no decided-before-count tag")
                if case.path == "election.html?id=us2026":
                    # no parameters: the national overview, no race preselected, no missing-file requests
                    nat = await page.evaluate(
                        """() => ({ value: document.querySelector('#race').value,
                          placeholder: document.querySelector('#race option[value=""]')?.textContent ?? "",
                          natOn: document.querySelector('#dash').classList.contains('nat-on'),
                          stateMap: !document.querySelector('#national').classList.contains('off'),
                          summary: document.querySelector('#nat-summary').innerText.trim().length,
                          control: /Control of the Senate/i.test(document.querySelector('#nat-summary').innerText) && /Pick a race/i.test(document.querySelector('#nat-summary').innerText),
                          readoutShown: document.querySelector('#readout').innerText.trim().length > 0,
                          chartShown: document.querySelector('#timeline').offsetParent !== null && document.querySelector('#timeline').innerText.trim().length > 0,
                          office: window.__dashAudit?.office?.() ?? null, region: window.__dashAudit?.region?.() ?? null,
                          url: location.search })"""
                    )
                    if nat["value"] != "" or nat["placeholder"] != "Choose a race":
                        result.failures.append(f"us2026 default preselects a race: {nat}")
                    if not nat["natOn"] or not nat["stateMap"] or not nat["summary"] or nat["region"] or nat["office"] != "senate":
                        result.failures.append(f"us2026 default is not the national Senate overview: {nat}")
                    if nat["readoutShown"] or nat["chartShown"] or not nat["control"]:
                        result.failures.append(f"us2026 default shows a race readout/chart or lacks the national summary: {nat}")
                    if "race=" in nat["url"]:
                        result.failures.append(f"us2026 default wrote a race to the address: {nat['url']}")
                    if any("house_live" in d for d in result.diagnostics):
                        result.failures.append("us2026 requested the missing house_live/ files")
                if "id=us2026" in case.path:
                    bad = await page.evaluate(
                        """() => { const re = /baseline|model only|not a forecast|not traded|rough/i;
                          const hits = new Set();
                          for (const el of document.body.querySelectorAll('*')) {
                            if (['SCRIPT', 'STYLE'].includes(el.tagName)) continue;
                            const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ');
                            for (const s of [own, el.getAttribute('title') || '', el.getAttribute('aria-label') || '']) { const m = s.match(re); if (m) hits.add(s.trim().slice(0, 80)); }
                          }
                          return [...hits]; }"""
                    )
                    if bad:
                        result.failures.append(f"US page shows retired model labels: {bad[:3]}")
            if screenshots and viewport_name == "desktop" and (
                (engine == "firefox" and case.name in {"index", "br2026r2gov"})
                or (engine == "webkit" and case.name == "us2026")
            ):
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=screenshots / f"{engine}-{case.name}-{viewport_name}.png", full_page=True)
        except Exception as error:  # Playwright navigation/evaluation failures are test failures.
            result.failures.append(f"check failed: {error}")
        finally:
            result.seconds = time.monotonic() - started
            await context.close()
        return result


async def run(args: argparse.Namespace, base: str) -> int:
    chosen_viewports = {name: VIEWPORTS[name] for name in args.viewports.split(",") if name in VIEWPORTS}
    if not chosen_viewports:
        raise SystemExit("--viewports must contain desktop and/or mobile")
    screenshots = Path(args.screenshots) if args.screenshots else None
    all_results: list[Result] = []
    async with async_playwright() as playwright:
        for engine in args.engines.split(","):
            engine = engine.strip()
            if engine not in {"chromium", "firefox", "webkit"}:
                raise SystemExit(f"unknown engine: {engine}")
            browser = await getattr(playwright, engine).launch()
            semaphore = asyncio.Semaphore(args.jobs)
            tasks = [
                check_page(browser, engine, viewport_name, viewport, case, base, args.timeout, screenshots, semaphore)
                for viewport_name, viewport in chosen_viewports.items()
                for case in cases()
                if case.kind != "maps" or viewport_name == "desktop"
            ]
            results = await asyncio.gather(*tasks)
            await browser.close()
            all_results.extend(results)
            for result in results:
                mark = "FAIL" if result.failures else "PASS"
                print(f"{mark:4} {result.engine:8} {result.viewport:7} {result.case.name:17} {result.seconds:5.1f}s")
                for failure in result.failures:
                    print(f"     ERROR {failure}")
                for diagnostic in result.diagnostics:
                    print(f"     INFO  {diagnostic}")
    failed = [result for result in all_results if result.failures]
    print(f"\n{len(all_results) - len(failed)}/{len(all_results)} checks passed")
    return bool(failed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="already-served site base URL; otherwise site/ is served locally")
    parser.add_argument("--engines", default="chromium,firefox,webkit", help="comma-separated browser engines")
    parser.add_argument("--viewports", default="desktop,mobile", help="desktop, mobile, or both comma-separated")
    parser.add_argument("--jobs", type=int, default=2, help="concurrent pages per browser")
    parser.add_argument("--timeout", type=float, default=45, help="seconds allowed for each page")
    parser.add_argument("--screenshots", help="directory for representative full-page screenshots")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.base:
        base = args.base.rstrip("/") + "/"
        return asyncio.run(run(args, base))
    with local_server() as base:
        return asyncio.run(run(args, base))


if __name__ == "__main__":
    raise SystemExit(main())
