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
    "mx2024_qc",
    "ca2025",
    "nl2025",
    "qc2026",
    "us2026",
    "es2026",
    "au2025",
    "fr2022r1",
    "fr2022r2",
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
    out = [Case("index", "index.html", "index"), Case("brazil-2026", "brazil-2026.html", "article"), Case("earlyvote", "earlyvote.html", "earlyvote"), Case("scorecard", "scorecard.html", "scorecard"), Case("midterms", "midterms.html", "midterms"),
           Case("groups", "groups.html", "groups"), Case("groups-ga-sen", "groups.html?race=ga-sen", "groups"), Case("guide", "guide.html", "guide")]
    out.extend(Case(eid, f"election.html?{urlencode({'id': eid})}", "election") for eid in ELECTIONS)
    # Exercise the US county map as well as its default national state map.
    out.append(Case("us2026-ca-gov", f"election.html?{urlencode({'id': 'us2026', 'race': 'ca-gov'})}", "election"))
    # The US page's House office (the district layer) and an old hub address, now a redirect.
    out.append(Case("us2026-house", f"election.html?{urlencode({'id': 'us2026', 'office': 'house'})}", "election"))
    out.append(Case("us-hub-redirect", "us.html?tab=markets&race=tx-sen", "election"))
    # Map checks (desktop only): the homepage's maps, then every election's map
    out.append(Case("maps-home", "", "maps"))
    out.extend(Case(f"maps-{e['id']}", e["id"], "maps") for e in map_audit.elections() if not map_audit.mapless(e["id"]))
    out.extend(sens_cases())
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
            evReady: !!window.__evReady,
            scReady: !!window.__scReady,
            mtReady: !!window.__mtReady,
            grReady: !!window.__groups?.ready,
            gdReady: !!window.__guide?.ready,
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
    if s["kind"] == "earlyvote":
        return s["evReady"]
    if s["kind"] == "scorecard":
        return s["scReady"]
    if s["kind"] == "midterms":
        return s["mtReady"]
    if s["kind"] == "groups":
        return s["grReady"]
    if s["kind"] == "guide":
        return s["gdReady"]
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


# --- Model sensitivities (thin dashed lines: v2, mix, v3) and the homepage's original / recalibrated / mixture comparison ---
# Expectations are registered only for data that is published: an election with a mixture in its stats.json
# (br2026r1) must draw the v2 and mix lines, chips and table columns; an election with only the legacy v2 must
# still draw v2 (a real one if any exists, else br2026r1 with its new sensitivities hidden by route interception).
DATA = SITE / "data"
SENS_ELECTION = "br2026r1"
HOME_MIX_MIN_ROWS = 5
SENS_CASE_KEYS: dict[str, list[str]] = {}


def _read_json(path: Path) -> Any:
    try:
        import json

        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def mix_elections() -> list[str]:
    """Elections whose published stats.json carries the mixture sensitivity."""
    out = []
    for d in sorted(DATA.iterdir()) if DATA.is_dir() else []:
        stats = _read_json(d / "stats.json") if d.is_dir() else None
        if isinstance(stats, dict) and (stats.get("sensitivities") or {}).get("mix"):
            out.append(d.name)
    return out


def legacy_only_elections() -> list[str]:
    """Elections with the legacy single sensitivity (meta.sensitivity) and no meta.sensitivities."""
    out = []
    for d in sorted(DATA.iterdir()) if DATA.is_dir() else []:
        meta = _read_json(d / "meta.json") if d.is_dir() else None
        if isinstance(meta, dict) and meta.get("sensitivity") and not meta.get("sensitivities"):
            out.append(d.name)
    return out


SYNTH_LEGACY = f"legacy-v2-{SENS_ELECTION}"


def sens_cases() -> list[Case]:
    out = []
    if SENS_ELECTION in mix_elections():
        out.append(Case("br2026r1-sens", f"election.html?{urlencode({'id': SENS_ELECTION})}", "election"))
        meta = _read_json(DATA / SENS_ELECTION / "meta.json") or {}       # v2 (legacy), mix, v3 (poll start), ...: whatever is published
        SENS_CASE_KEYS["br2026r1-sens"] = (["v2"] if meta.get("sensitivity") else []) + [s["key"] for s in meta.get("sensitivities", [])]
    legacy = legacy_only_elections()
    if legacy:
        out.append(Case(f"legacy-v2-{legacy[0]}", f"election.html?{urlencode({'id': legacy[0]})}", "election"))
        SENS_CASE_KEYS[out[-1].name] = ["v2"]
    elif (_read_json(DATA / SENS_ELECTION / "meta.json") or {}).get("sensitivity"):
        out.append(Case(SYNTH_LEGACY, f"election.html?{urlencode({'id': SENS_ELECTION})}", "election"))
        SENS_CASE_KEYS[SYNTH_LEGACY] = ["v2"]
    return out


async def install_sens_routes(page: Page, case: Case) -> None:
    """The synthesised legacy case: serve br2026r1's meta.json without its new `sensitivities` list."""
    if case.name != SYNTH_LEGACY:
        return

    async def drop_new(route: Any) -> None:
        response = await route.fetch()
        body = await response.json()
        body.pop("sensitivities", None)
        await route.fulfill(response=response, json=body)

    await page.route(f"**/data/{SENS_ELECTION}/meta.json", drop_new)


SENS_CHECK = r"""async ({ id, keys }) => {
  const fails = [];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (f, n = 60) => { for (let i = 0; i < n; i++) { const v = f(); if (v) return v; await wait(100); } return f(); };
  const btn = document.querySelector('#tl-view button[data-view="chance"]');
  if (btn && btn.getAttribute("aria-pressed") !== "true") btn.click();
  const svg = await until(() => document.querySelector("#timeline svg"));
  if (!svg) return ["no timeline drawing"];
  await until(() => document.querySelector("#timeline svg path.sens-line"));
  const raceId = document.querySelector("#race").value;
  const tl = await (await fetch(`data/${id}/timeline.json`)).json();
  const rows = tl[raceId] ?? [];
  const col = (k) => (k === "v2" ? "P_sensitivity" : `P_sens_${k}`);
  // (a) one thin dashed path per sensitivity, each with its own pattern, and a direct end label
  const lines = [...document.querySelectorAll("#timeline svg path.sens-line")];
  const got = lines.map((p) => [...p.classList].find((c) => c.startsWith("sens-") && c !== "sens-line")?.slice(5));
  for (const k of keys) if (!got.includes(k)) fails.push(`${id}: no dashed ${k} path in the timeline (got ${got})`);
  for (const k of got) if (!keys.includes(k)) fails.push(`${id}: unexpected dashed ${k} path`);
  if (lines.length !== keys.length) fails.push(`${id}: ${lines.length} dashed sensitivity paths, expected ${keys.length}`);
  const dashes = lines.map((p) => p.getAttribute("stroke-dasharray"));
  if (new Set(dashes).size !== dashes.length || dashes.some((d) => !d)) fails.push(`${id}: sensitivity dash patterns are not distinct: ${dashes}`);
  for (const p of lines) {
    if (!(p.getAttribute("d") ?? "").includes("L") && !(p.getAttribute("d") ?? "").includes("H") && !(p.getAttribute("d") ?? "").includes("V")) fails.push(`${id}: a sensitivity path is empty`);
    if (+p.getAttribute("stroke-width") > 1.2) fails.push(`${id}: a sensitivity path is not thin`);
  }
  const labels = [...document.querySelectorAll("#timeline svg text.end-label")].map((t) => (t.firstChild?.textContent ?? "").trim());
  for (const k of keys) if (!labels.includes(k)) fails.push(`${id}: no end label "${k}" (labels ${labels})`);
  // (b) the headline line is unchanged: a solid, 2 px model path
  const solid = [...document.querySelectorAll("#timeline svg path")].filter((p) => p.getAttribute("stroke") === "#c4501b" && p.getAttribute("stroke-width") === "2" && !p.getAttribute("stroke-dasharray"));
  if (!solid.length) fails.push(`${id}: no solid model path`);
  // scrub to a minute and compare the chip with the published timeline row
  const ok = (d) => keys.every((k) => d[col(k)] != null) && (d.P_v2 ?? d.P) != null;
  const open = (x) => x != null && x > 0.05 && x < 0.95; // a minute where nothing is settled, so the chip shows values
  const early = rows.filter((d) => ok(d) && d.counted >= 0.05 && open(d.P_v2 ?? d.P) && (d.pm == null || open(d.pm)));
  // a minute where no market is settled; an election whose market was settled from the start shows no values chip, so only the lines, table and text are checked
  const row = early[0] ?? rows.find((d) => ok(d) && open(d.P_v2 ?? d.P) && (d.pm == null || open(d.pm)));
  if (!rows.some((d) => keys.every((k) => d[col(k)] != null))) return [...fails, `${id}: no timeline row of ${raceId} carries ${keys.map(col)}`];
  if (row) {
    window.__dashAudit.setTime(Date.parse(row.t));
    const chip = await until(() => { const c = document.querySelector("#readout .chip.ro-ch"); return c && /^Chance of winning$/.test(c.querySelector("small")?.textContent ?? "") ? c : null; });
    if (!chip) return [...fails, `${id}: no chance split bars at ${row.t} (readout: ${document.querySelector("#readout").innerText.slice(0, 120)})`];
    const byName = Object.fromEntries([...chip.querySelectorAll("b > span[data-source]")].map((x) => [x.dataset.source, Number(x.dataset.p).toFixed(2)]));
    const names = Object.keys(byName);
    const f2 = (x) => x.toFixed(2);
    if (byName.model !== f2(row.P_v2 ?? row.P)) fails.push(`${id}: chance bar model ${byName.model} is not the timeline's ${f2(row.P_v2 ?? row.P)} at ${row.t}`);
    for (const k of keys) {
      if (!(k in byName)) fails.push(`${id}: chance bars have no ${k} (${names})`);
      else if (byName[k] !== f2(row[col(k)])) fails.push(`${id}: chance bar ${k} ${byName[k]} is not the timeline's ${f2(row[col(k)])} at ${row.t}`);
    }
    if (keys.length === 1 && names.includes("mix")) fails.push(`${id}: chance bars show mix for an election without it`);
  } else if (keys.includes("mix")) fails.push(`${id}: no unsettled minute to compare the chip with the timeline`);
  // races table: one sortable column per key, headed by the key
  const heads = [...document.querySelectorAll("#pane-races table.races thead th button")];
  for (const k of keys) {
    const b = heads.find((h) => h.dataset.key === `sens_${k}`);
    if (!b || b.textContent.trim() !== k) { fails.push(`${id}: races table has no "${k}" column`); continue; }
    const idx = heads.indexOf(b);
    const cellsOf = () => [...document.querySelectorAll("#pane-races table.races tbody tr")].map((tr) => tr.children[idx]?.querySelector(".chance-est")?.dataset.p);
    await until(() => cellsOf().some((c) => c != null && Number.isFinite(+c)), 30); // the list refreshes at most every 200 ms
    const cells = cellsOf();
    if (!cells.some((c) => c != null && Number.isFinite(+c))) fails.push(`${id}: the ${k} column has no probability bars (${cells.slice(0, 4)})`);
    b.click();
    if (b.parentElement.getAttribute("aria-sort") === "none") fails.push(`${id}: the ${k} column does not sort`);
  }
  // method tab: one sentence per sensitivity
  document.querySelector("#tab-method").click();
  const method = document.querySelector("#pane-method").innerText;
  if (!/thin dashed lines are sensitivities/i.test(method)) fails.push(`${id}: method tab has no sensitivities sentence`);
  for (const k of keys) if (!new RegExp(`${k}\\s*=`).test(method)) fails.push(`${id}: method tab does not explain ${k}`);
  document.querySelector("#tab-races").click();
  return fails;
}"""


async def check_sens(page: Page, case: Case, keys: list[str]) -> list[str]:
    eid = case.path.split("id=")[-1].split("&")[0]
    try:
        return await page.evaluate(SENS_CHECK, {"id": eid, "keys": keys})
    except Exception as error:
        return [f"{case.name}: sensitivity check failed: {error}"]


HOME_MIX_CHECK = r"""async (minRows) => {
  const fails = [];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 100 && !(document.querySelector("#chart-mix-cov svg") && document.querySelector("#chart-mix-lead svg")); i++) await wait(100);
  const sec = document.querySelector("#ev-mix");
  if (!sec || sec.hidden) return ["homepage mixture section is hidden"];
  if (!/^Original vs recalibrated vs mixture: 90% band coverage and lead over the market$/.test(sec.querySelector("h2").textContent.trim())) fails.push("mixture section title changed");
  const rows = [...document.querySelectorAll("#mix-table tbody tr")];
  if (rows.length < minRows) fails.push(`mixture table has ${rows.length} rows, expected at least ${minRows}`);
  for (const tr of rows) {
    const id = tr.dataset.election;
    const cells = [...tr.children];
    if (cells.length !== 7) { fails.push(`${id}: ${cells.length} cells in the mixture row`); continue; }
    const st = await (await fetch(`data/${id}/stats.json`)).json();
    const noV2 = !st.sensitivities?.v2; // an election with only a mixture (no recalibrated version) shows – for v2
    for (const [i, c] of cells.slice(1, 4).entries()) {
      if (noV2 && i === 1 && c.textContent.trim() === "–") continue;
      const v = Number(c.dataset.v);
      if (c.dataset.v == null || !Number.isFinite(v) || v < 0 || v > 1) fails.push(`${id}: coverage cell "${c.textContent}" is not a number in [0, 1]`);
      else if (c.textContent.trim() !== `${Math.round(100 * v)}%`) fails.push(`${id}: coverage cell shows ${c.textContent} for ${v}`);
    }
    for (const c of cells.slice(4)) {
      const t = c.textContent.trim();
      if (noV2 && c === cells[5] && t === "–") continue;
      if (t !== "–" && !(Number.isFinite(Number(c.dataset.v)) && /^[+−]?\d+ \(n \d+\)$/.test(t))) fails.push(`${id}: lead cell "${t}" is neither – nor minutes`);
    }
  }
  const gov = document.querySelector('#mix-table tr[data-election="br2026r2gov"]');
  if (gov && !/\(mean of \d+ governor races\)/.test(gov.children[0].textContent)) fails.push(`br2026r2gov row lacks "(mean of N governor races)": ${gov.children[0].textContent}`);
  if (document.querySelectorAll("#chart-mix-cov g.row").length !== rows.length) fails.push("coverage chart rows differ from the table rows");
  if (document.querySelectorAll("#chart-mix-lead g.row").length !== rows.length) fails.push("lead chart rows differ from the table rows");
  if (document.querySelectorAll("#chart-mix-cov circle").length < rows.length) fails.push("coverage chart has fewer dots than rows");
  const intro = sec.querySelector(".ev-text > p:not(.note):not(.mix-key)")?.textContent.trim() ?? "";
  if ((intro.match(/[.!?](\s|$)/g) ?? []).length !== 2) fails.push(`mixture explanation is not two sentences: ${intro.slice(0, 80)}`);
  const cav = document.querySelector("#ev-mix-caveat");
  if (!cav || !cav.offsetParent || !/retrospective/i.test(cav.textContent) || !/after the result was known/i.test(cav.textContent)) fails.push("mixture caveat is missing or hidden");
  return fails;
}"""


async def check_home_mix(page: Page) -> list[str]:
    """The homepage table lists every election whose stats.json has a mixture (at least HOME_MIX_MIN_ROWS)."""
    expected = mix_elections()
    if not expected:
        return []
    try:
        fails = await page.evaluate(HOME_MIX_CHECK, HOME_MIX_MIN_ROWS)
        shown = await page.evaluate("[...document.querySelectorAll('#mix-table tbody tr')].map((r) => r.dataset.election)")
    except Exception as error:
        return [f"homepage mixture check failed: {error}"]
    missing = sorted(set(expected) - set(shown))
    if missing and "hidden" not in " ".join(fails):
        fails.append(f"homepage mixture table lacks {missing}")
    return fails


NAV_EXPECT = ["Night guide", "Election nights", "US early vote", "Electorate by state", "Live by place", "Track record"]


async def check_site_nav(page: Page) -> list[str]:
    """The US context pages share one top navigation: same labels in the same order, the current page not a link."""
    labels = await page.evaluate("() => [...document.querySelectorAll('nav.sitenav a, nav.sitenav span[aria-current]')].map((e) => e.textContent.trim())")
    cur = await page.evaluate("() => document.querySelectorAll('nav.sitenav [aria-current=page]').length")
    out = []
    if sorted(labels) != sorted(NAV_EXPECT) or cur != 1:
        out.append(f"site nav differs from {NAV_EXPECT}: {labels}, current={cur}")
    return out


async def check_midterms(page: Page, viewport_name: str = "desktop") -> list[str]:
    """Midterms page: overview charts, state picker and map, one state panel whose every sub-section has content, compare view, no horizontal scroll."""
    q = await page.evaluate("""() => ({ evrows: document.querySelectorAll('#ov-ev .dp-row').length, regrows: document.querySelectorAll('#ov-reg .rb-row').length,
        paths: document.querySelectorAll('#mt-map path[data-st]').length, chips: document.querySelectorAll('#mt-list button[data-st]').length,
        tiles: document.querySelectorAll('#cmp-grid .cmp-tile').length, tx: document.querySelectorAll('#tx-table tbody tr').length,
        hasPanel: !!document.querySelector('#state-panel h2'), base: document.querySelectorAll('#base-tog button').length,
        loading: [...document.querySelectorAll('main p, main td')].some((e) => /^Loading…$/.test(e.textContent.trim())) })""")
    out = []
    if q["evrows"] < 3 or q["regrows"] < 3:
        out.append(f"midterms overview charts are short: {q}")
    if q["paths"] < 50 or q["chips"] < 40 or not q["hasPanel"]:
        out.append(f"midterms state picker is short: {q}")
    if q["tiles"] < 3 or q["tx"] < 10 or q["base"] < 4:
        out.append(f"midterms compare view, mapping table or baseline toggle is short: {q}")
    if q["loading"]:
        out.append("midterms page still shows Loading")
    # every state: the panel has sub-sections and none of them is an empty box
    for st in ("nc", "co", "ga", "tx", "wy", "us"):
        await page.evaluate("(s) => { location.hash = '#st=' + s; }", st)
        await page.wait_for_function("(s) => document.querySelector('#state-panel h2') && document.querySelector('#state-panel').dataset.st === s", arg=st, timeout=10000)
        r = await page.evaluate("""() => { const figs = [...document.querySelectorAll('#state-panel .mt-fig')];
            return { n: figs.length, empty: figs.filter((f) => !f.querySelector('.dp-row, .ib-row, svg, .rb-row')).length, heads: figs.map((f) => f.querySelector('h3')?.textContent) }; }""")
        if r["empty"]:
            out.append(f"midterms {st}: empty sub-section(s) {r}")
        if st in ("nc", "us") and r["n"] < 2:
            out.append(f"midterms {st}: expected at least two sub-sections, got {r}")
    nc = await page.evaluate("() => { location.hash = '#st=nc'; return 0; }")
    await page.wait_for_function("() => document.querySelector('#state-panel').dataset.st === 'nc'", timeout=10000)
    heads = await page.evaluate("() => [...document.querySelectorAll('#state-panel .mt-fig h3')].map((h) => h.textContent)")
    if heads[:1] != ["Early vote by group"]:
        out.append(f"midterms nc: sub-sections out of order: {heads}")
    over = await page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if over > 1:
        out.append(f"midterms page scrolls sideways by {over}px on {viewport_name}")
    del nc
    return out


async def check_earlyvote(page: Page, viewport_name: str) -> list[str]:
    """US early-vote page: coverage of all 51, map, charts for the default state and for others, county map, wording."""
    out: list[str] = []
    probe = """() => ({ rows: document.querySelectorAll('#cov-table tbody tr[data-st]').length,
        mapPaths: document.querySelectorAll('#cov-map path.ev-st').length,
        c1: document.querySelectorAll('#c1 svg path').length, c2: document.querySelectorAll('#c2 svg rect').length,
        picks: document.querySelectorAll('#st-pick option').length, chips: document.querySelectorAll('#st-chips .chip').length })"""
    p = await page.evaluate(probe)
    if p["rows"] != 51 or p["picks"] != 51:
        out.append(f"coverage has {p['rows']} table rows and {p['picks']} picker entries, expected 51")
    if p["mapPaths"] < 51:
        out.append(f"coverage map has {p['mapPaths']} states")
    if not p["c1"] or not p["c2"] or not p["chips"]:
        out.append(f"default state is missing charts or key figures: {p}")
    # a state with party detail, daily files and history; one with county data only; a compiled one and a blocked one if there are any
    index = await page.evaluate("fetch('data/ev2026/index.json').then((r) => r.json())")
    by = {c["status"]: c for c in reversed(index["states"]) if c.get("has_state") or c["status"] in ("blocked", "not_published")}
    plan = [("nc", {"party": True, "county": True, "compare": True}), ("tx", {"party": False, "county": True, "compare": False})]
    if "compiled" in by:
        plan.append((by["compiled"]["st"], {"compiled": True}))
    for k in ("blocked", "not_published"):
        if k in by:
            plan.append((by[k]["st"], {"blocked": True}))
    for st, want in plan:
        await page.select_option("#st-pick", st)
        await page.wait_for_function("(s) => new URLSearchParams(location.search).get('st') === s", arg=st)
        await page.wait_for_timeout(700)
        q = await page.evaluate("""() => ({ title: document.querySelector('#st-h').textContent,
            c1: document.querySelectorAll('#c1 svg path').length, partyShown: !document.querySelector('#party-fig').hidden && document.querySelectorAll('#c3 svg path').length > 0,
            ctyShown: !document.querySelector('#cty-fig').hidden && document.querySelectorAll('#cty-map svg path').length > 3,
            cmpRows: document.querySelectorAll('#cmp-table tbody tr').length, cmpCharts: document.querySelectorAll('#c4 svg path').length,
            uf: document.querySelector('#st-uf').textContent, basis: document.querySelector('#st-basis').textContent.length })""")
        if want.get("blocked"):
            if q["c1"] or not q["basis"]:
                out.append(f"{st}: a state with no data should show its reason and no charts: {q}")
            continue
        if want.get("compiled"):
            if not q["c1"] or "UF Election Lab" not in q["uf"] or "permission" not in q["uf"]:
                out.append(f"{st}: a compiled state must show charts and the UF label with the permission line: {q}")
            continue
        if not q["c1"] or q["partyShown"] != want["party"] or q["ctyShown"] != want["county"] or bool(q["cmpCharts"]) != want["compare"]:
            out.append(f"{st}: unexpected panels {q}")
    text = await page.evaluate("document.body.innerText")
    for bad in ("NaN", "undefined", "null", "[object"):
        if bad in text:
            out.append(f"page text contains {bad!r}")
    if re.search(r"\b(forecast(?!\.)|probabilit|chance of winning|odds)\b", text, re.I):
        out.append("page text mentions a forecast, probability or odds")
    fw = await page.evaluate("""() => [...document.querySelectorAll('body *')].filter((e) => !e.closest('script,style') && [...e.childNodes].some((n) => n.nodeType === 3 && /firewall/i.test(n.textContent))).map((e) => !!e.closest('.ev-explain'))""")
    if not all(fw):
        out.append("the word 'firewall' appears outside the explainer")
    if viewport_name == "mobile":
        wide = await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        if wide:
            out.append("page scrolls sideways on mobile")
    return out


async def check_groups(page: Page, viewport_name: str) -> list[str]:
    """Places page (groups.html): five dimension cards plus key regions, each with rows; past results; the night chart toggles."""
    out: list[str] = []
    p = await page.evaluate("""() => ({ cards: [...document.querySelectorAll('.gr-card')].map((c) => [c.dataset.card, c.querySelectorAll('tbody tr').length]),
        dims: document.querySelectorAll('#gr-dim button').length, races: document.querySelectorAll('#gr-race option').length,
        margins: [...document.querySelectorAll('.gr-t tbody td.n')].filter((td) => /^[A-Z]\\+\\d/.test(td.textContent.trim())).length,
        note: document.querySelector('#gr-note')?.textContent ?? '' })""")
    ids = [c[0] for c in p["cards"]]
    for want in ("place", "edu", "age", "inc", "race", "reg"):
        if want not in ids:
            out.append(f"groups page has no '{want}' card (cards: {ids})")
    if any(n == 0 for _, n in p["cards"]):
        out.append(f"groups page has an empty card: {p['cards']}")
    if p["races"] < 2 or p["dims"] < 6:
        out.append(f"groups page controls are incomplete: {p}")
    if p["margins"] < 20:
        out.append(f"groups page shows only {p['margins']} margins")
    if "not of voters" not in p["note"]:
        out.append("groups page lacks its places-not-voters label")
    if viewport_name == "mobile":
        wide = await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        if wide:
            out.append("page scrolls sideways on mobile")
    return out


async def check_guide(page: Page, viewport_name: str) -> list[str]:
    """Night guide (guide.html), before the count: the race board with every counted race and the House row, sortable; the empty
    event feed says why; the before-the-results part has early-vote numbers, the poll-closing schedule and counties to watch."""
    out: list[str] = []
    try:
        await page.wait_for_function("document.querySelectorAll('#gd-pre-body table').length >= 2", timeout=15000)
    except Exception:
        out.append("guide page has no schedule and counties-to-watch tables")
    p = await page.evaluate("""() => ({ rows: document.querySelectorAll('#gd-board tbody tr[data-race]').length, house: !!document.querySelector('#gd-board tr.gd-house'),
        heads: document.querySelectorAll('#gd-board thead th').length, feedNote: document.querySelector('#gd-feed-note')?.textContent ?? '', feed: document.querySelectorAll('#gd-feed li').length,
        chips: document.querySelectorAll('#gd-pre-body .chip').length, sched: document.querySelectorAll('#gd-pre-body table')[0]?.querySelectorAll('tbody tr').length ?? 0,
        watch: document.querySelectorAll('#gd-pre-body table')[1]?.querySelectorAll('tbody tr').length ?? 0, build: window.__guide?.build === window.__BUILD,
        links: [...document.querySelectorAll('#gd-board tbody tr[data-race] a')].every((a) => a.getAttribute('href').startsWith('election.html?id=us2026&race=')) })""")
    if p["rows"] < 20 or not p["house"] or p["heads"] != 8:
        out.append(f"guide race board is incomplete: {p}")
    if p["feed"] or "Nothing yet" not in p["feedNote"]:
        out.append(f"guide feed should be empty with a reason before the count: {p}")
    if p["chips"] < 3 or p["sched"] < 10 or p["watch"] < 15:
        out.append(f"guide before-the-results part is short: {p}")
    if not p["build"] or not p["links"]:
        out.append(f"guide build stamp or race links are wrong: {p}")
    names = "() => [...document.querySelectorAll('#gd-board tbody tr[data-race] td.gd-race')].map((td) => td.textContent)"
    before = await page.evaluate(names)
    await page.click("#gd-board thead button[data-sort='race']")
    head = await page.evaluate("() => document.querySelector('#gd-board thead th[aria-sort]')?.textContent ?? ''")
    after = await page.evaluate(names)
    in_order = await page.evaluate("(a) => a.every((x, i) => i === 0 || a[i - 1].localeCompare(x) <= 0)", after)
    if head != "Race" or not in_order or after == before:
        out.append(f"guide board does not sort by race: {head!r}")
    if viewport_name == "mobile":
        wide = await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        if wide:
            out.append("page scrolls sideways on mobile")
    return out


async def check_scorecard(page: Page, viewport_name: str) -> list[str]:
    """Track-record page: every section drawn from the generated files, calibration charts and tables present, wording."""
    out: list[str] = []
    p = await page.evaluate("""() => ({ charts: document.querySelectorAll('.sc-chart svg').length, pts: document.querySelectorAll('.sc-chart circle').length,
        elections: document.querySelectorAll('#rep-body table.sc-t tbody tr').length, backtest: document.querySelectorAll('#bt-body table').length,
        sens: document.querySelectorAll('#sens-body table').length, fwd: document.querySelectorAll('#fwd-body li').length,
        explain: document.querySelectorAll('#sc-defs li').length })""")
    if not p["charts"] or not p["pts"]:
        out.append(f"calibration charts are missing: {p}")
    if not p["elections"] or not p["sens"] or not p["backtest"] or not p["explain"]:
        out.append(f"a section is empty: {p}")
    text = await page.evaluate("document.body.innerText")
    for bad in ("NaN", "undefined", "null", "[object", "our model"):
        if bad in text:
            out.append(f"page text contains {bad!r}")
    if viewport_name == "mobile":
        wide = await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        if wide:
            out.append("page scrolls sideways on mobile")
    return out


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
        await install_sens_routes(page, case)
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
                result.failures.extend(await check_home_mix(page))
                if viewport_name == "desktop":
                    result.failures.extend(await check_home_card(page))
            elif case.kind == "article" and current["articlePaths"] <= 10:
                result.failures.append("article charts were not drawn")
            elif case.kind == "earlyvote":
                result.failures.extend(await check_earlyvote(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "scorecard":
                result.failures.extend(await check_scorecard(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "midterms":
                result.failures.extend(await check_midterms(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "groups":
                result.failures.extend(await check_groups(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "guide":
                result.failures.extend(await check_guide(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "election":
                if not current["title"] or current["title"].startswith("Loading"):
                    result.failures.append("election title was not set")
                if not current["raceOptions"]:
                    result.failures.append("race picker is empty")
                if not current["readout"]:
                    result.failures.append("race/national readout is empty")
                if current["visiblePaths"] == 0 and not map_audit.mapless(case.path.split("id=")[-1].split("&")[0]) and not await canvas_has_drawing(page):
                    result.failures.append("map has neither visible SVG paths nor non-background canvas pixels")
                # Layer controls and the no-market cutover.
                if case.path == "election.html?id=fr2022r2":
                    polymarket_text = await page.evaluate(
                        """() => [...document.body.querySelectorAll('*')]
                          .filter((e) => !['SCRIPT', 'STYLE'].includes(e.tagName))
                          .some((e) => [...e.childNodes].some((n) => n.nodeType === 3 && /Polymarket/i.test(n.textContent)))"""
                    )
                    if polymarket_text:
                        result.failures.append("fr2022r2 contains Polymarket text despite having no market data")
                if case.path == "election.html?id=br2026r2":
                    layer = await page.evaluate(
                        """async () => {
                          const wait = (ms) => new Promise((r) => setTimeout(r, ms));
                          const state = () => ({
                            column: [...document.querySelectorAll('#pane-races th')].some((h) => h.textContent.trim() === 'Polymarket'),
                            line: [...document.querySelectorAll('#timeline path')].some((p) => p.getAttribute('stroke') === '#1b1b1b'),
                            pressed: document.querySelector('[data-layer="polymarket"]')?.getAttribute('aria-pressed'),
                          });
                          const button = document.querySelector('[data-layer="polymarket"]');
                          if (!button) return { error: 'no Polymarket toggle' };
                          if (button.getAttribute('aria-pressed') === 'true') button.click();
                          await wait(200);
                          const off = state(), url = location.search;
                          return { off, url };
                        }"""
                    )
                    if layer.get("error"):
                        result.failures.append(layer["error"])
                    else:
                        try:
                            await page.reload(wait_until="load", timeout=timeout * 1000)
                            await page.wait_for_function(
                                """() => document.querySelector('[data-layer="polymarket"]')
                                  && document.querySelector('#pane-races table.races')""",
                                timeout=timeout * 1000,
                            )
                            persisted = await page.evaluate(
                                """() => ({
                                  column: [...document.querySelectorAll('#pane-races th')].some((h) => h.textContent.trim() === 'Polymarket'),
                                  line: [...document.querySelectorAll('#timeline path')].some((p) => p.getAttribute('stroke') === '#1b1b1b'),
                                  pressed: document.querySelector('[data-layer="polymarket"]')?.getAttribute('aria-pressed'),
                                  stored: JSON.parse(localStorage.getItem('ubiu.layers.hidden') ?? '[]'),
                                })"""
                            )
                            if layer["off"]["column"] or layer["off"]["line"] or layer["off"]["pressed"] != "false":
                                result.failures.append(f"br2026r2 Polymarket layer did not switch off: {layer}")
                            if "show=" not in layer["url"]:
                                result.failures.append(f"br2026r2 layer state is absent from the URL: {layer['url']}")
                            if persisted["column"] or persisted["line"] or persisted["pressed"] != "false" or "polymarket" not in persisted["stored"]:
                                result.failures.append(f"br2026r2 Polymarket layer did not survive reload: {persisted}")
                        except Exception as error:
                            result.failures.append(f"br2026r2 layer reload check failed: {error}")
                if case.path == "election.html?id=pl2025r2":
                    replay = await page.evaluate(
                        """() => ({ now: window.__dashAudit?.time?.(), domain: window.__dashAudit?.domain?.(),
                          readout: document.querySelector('#readout').innerText,
                          hint: document.querySelector('#replay-hint').innerText,
                          decidedTags: document.querySelectorAll('#pane-races .label.decided').length })"""
                    )
                    if not replay["domain"] or not (replay["domain"][0] < replay["now"] < replay["domain"][1]):
                        result.failures.append(f"pl2025r2 did not open mid-night: {replay}")
                    if not replay["hint"] or not re.search(r"(?:Chance of winning|leads).*[0-9]+%", replay["readout"], re.S):
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
                          control: /Control of the Senate/i.test(document.querySelector('#nat-summary').innerText),
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
                if case.name == "us2026-ca-gov":
                    await page.evaluate("async () => window.__dashAudit.selectRace('fl-gov')")
                    await page.wait_for_function(
                        """() => document.querySelector('[data-layer="early"]')
                          && document.querySelector('#readout .chip')""",
                        timeout=timeout * 1000,
                    )
                    us_layers = await page.evaluate(
                        """() => ({
                          layers: [...document.querySelectorAll('#layer-bar [data-layer]')].map((b) => b.textContent.trim()),
                          chips: [...document.querySelectorAll('#readout .chip small')].map((c) => c.textContent.trim()),
                          allOn: [...document.querySelectorAll('#layer-bar [data-layer]')].every((b) => b.getAttribute('aria-pressed') === 'true'),
                        })"""
                    )
                    expected_layers = {"Model", "Polls", "Polymarket", "Kalshi", "Turnout", "Early vote"}
                    if not expected_layers.issubset(set(us_layers["layers"])) or not us_layers["allOn"]:
                        result.failures.append(f"us2026 pre-count layer chips are incomplete: {us_layers}")
                    expected_chips = ("Model, polls", "Polls", "Market-implied", "Chance of winning", "Turnout", "Early vote")
                    if any(not any(label.startswith(want) for label in us_layers["chips"]) for want in expected_chips):
                        result.failures.append(f"us2026 pre-count readout chips are incomplete: {us_layers}")
                if "id=us2026" in case.path:
                    bad = await page.evaluate(
                        """() => { const re = /\\b(?:baseline|model only|not a forecast|not traded|rough)\\b/i;
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
            if case.name in SENS_CASE_KEYS:
                result.failures.extend(await check_sens(page, case, SENS_CASE_KEYS[case.name]))
            if screenshots and case.name == "midterms":
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=screenshots / f"47_context_{engine}_{viewport_name}.png", full_page=True)
            if screenshots and case.name == "earlyvote":
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=screenshots / f"{engine}-earlyvote-{viewport_name}.png", full_page=True)
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
                if (case.kind != "maps" or viewport_name == "desktop") and (not args.only or any(o in case.name for o in args.only.split(",")))
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
    parser.add_argument("--only", help="comma-separated substrings: run only the cases whose name contains one (e.g. earlyvote)")
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
