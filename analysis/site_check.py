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
    out = [Case("index", "index.html", "index"), Case("brazil-2026", "brazil-2026.html", "article"), Case("earlyvote", "earlyvote.html", "earlyvote"), Case("turnout", "turnout.html", "turnout"), Case("scorecard", "scorecard.html", "scorecard"), Case("midterms", "midterms.html", "midterms"),
           Case("groups", "groups.html", "groups"), Case("groups-ga-sen", "groups.html?race=ga-sen", "groups"), Case("guide", "guide.html", "guide"),
           Case("count", f"count.html?{urlencode({'live': COUNT_MOCK})}", "count"), Case("count-link", "count.html", "count")]
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
            tddReady: !!window.__tddReady,
            scReady: !!window.__scReady,
            mtReady: !!window.__mtReady,
            grReady: !!window.__groups?.ready,
            gdReady: !!window.__guide?.ready,
            cntReady: document.body.dataset.ready === "1" && document.querySelectorAll("#cnt-svg .u").length > 400 && ["live", "stale"].includes(document.querySelector("#status")?.dataset.state),
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
    if s["kind"] == "turnout":
        return s["tddReady"]
    if s["kind"] == "scorecard":
        return s["scReady"]
    if s["kind"] == "midterms":
        return s["mtReady"]
    if s["kind"] == "groups":
        return s["grReady"]
    if s["kind"] == "guide":
        return s["gdReady"]
    if s["kind"] == "count":
        return s["cntReady"]
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


NAV_EXPECT = ["Night guide", "Election nights", "US early vote", "Turnout today", "Electorate by state", "Live by place", "Track record"]


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
        mapPaths: document.querySelectorAll('#cov-map .ev-kb button[data-st]').length,
        c1: document.querySelectorAll('#c1 svg path').length, c2: document.querySelectorAll('#c2 svg rect').length,
        picks: document.querySelectorAll('#st-pick option').length, chips: document.querySelectorAll('#st-chips .chip').length })"""
    p = await page.evaluate(probe)
    # The map deliberately opens at a national summary; no state is preselected.
    if await page.evaluate("new URLSearchParams(location.search).has('st')"):
        out.append("early-vote page preselects a state instead of opening on the national summary")
    await page.select_option("#st-pick", "nc")
    await page.wait_for_function("new URLSearchParams(location.search).get('st') === 'nc'")
    p.update(await page.evaluate("() => ({ c1: document.querySelectorAll('#c1 svg path').length, c2: document.querySelectorAll('#c2 svg rect').length, chips: document.querySelectorAll('#st-chips .chip').length })"))
    if p["rows"] != 51 or p["picks"] != 52:
        out.append(f"coverage has {p['rows']} table rows and {p['picks']} picker entries, expected 51 states plus the picker placeholder")
    if p["mapPaths"] < 51:
        out.append(f"coverage map has {p['mapPaths']} states")
    if not p["c1"] or not p["c2"] or not p["chips"]:
        out.append(f"default state is missing charts or key figures: {p}")
    out.extend(await check_county_zoom(page, "#cov-map", "#cov-tip", "#cov-legend", "#cov-metric", "early-vote map"))
    # key findings: the outstanding-mail card and the 2022 final pre-election comparison, from turnout_model.json
    tm_ = await page.evaluate("fetch('data/ev2026/turnout_model.json').then((r) => r.ok ? r.json() : null)")
    kf = await page.evaluate("document.querySelector('#kf-cards')?.textContent ?? ''")
    if tm_ and tm_["summary"].get("outstanding_states") and "Mail ballots not yet returned" not in kf:
        out.append("key findings lack the 'Mail ballots not yet returned' card")
    if tm_ and tm_["summary"].get("vs_2022_final_states") and "final pre-election report (not same day)" not in kf:
        out.append("key findings lack the 2022 final pre-election comparison")
    ps = (tm_ or {}).get("proj_summary")
    if ps and (ps["above"] or ps["below"]):
        want = f"{len(ps['above'])} states above, {len(ps['below'])} below"
        if "Turnout heading vs the model" not in kf or want not in kf or "Unregistered" not in kf:
            out.append(f"key findings lack the 'Turnout heading vs the model: {want} (unregistered)' card")
    # Election Day turnout summary: counts match turnout_day.json, the history line and the link to the dashboard are there, the long table is gone
    td = await page.evaluate("""() => ({ ready: !!window.__tdReady, label: document.querySelector('#td-sec .td-label')?.textContent ?? '', asof: document.querySelector('#td-asof')?.textContent ?? '',
        sum: document.querySelector('#td-sum')?.textContent ?? '', hist: document.querySelector('#td-hist')?.textContent ?? '',
        link: document.querySelector('#td-sec a[href="turnout.html"]')?.textContent ?? '', tables: document.querySelectorAll('#td-sec table').length })"""
    )
    published = await page.evaluate("fetch('data/ev2026/turnout_day.json').then((r) => r.ok ? r.json() : null)")
    if not td["ready"] or not td["link"] or td["tables"]:
        out.append(f"turnout summary is not ready, lacks its link to turnout.html, or still has the long table: {td}")
    if "not used by any model" not in td["label"].lower():
        out.append("turnout section lacks its 'not used by any model' label")
    if published:
        with_rows = sum(1 for s_ in published["states"] if s_["rows"])
        with_cty = sum(1 for s_ in published["states"] if s_["counties"])
        want = f"{with_rows} of {len(published['states'])} states and DC have a statewide row; {with_cty} have county rows; {published['n_rows']} rows in all"
        if want not in td["sum"]:
            out.append(f"turnout summary does not match turnout_day.json (expected {want!r}): {td['sum']!r}")
    if not td["hist"]:
        out.append("turnout section has no history line")
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
    out.extend(await check_ev_history(page))
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


async def check_county_zoom(page: Page, box: str, tip: str, legend: str, metric: str, name: str) -> list[str]:
    """The early-vote and turnout maps (usMap in turnout.js) behave like the election watch: controls, a state click zooms into its counties
    (box data-level), a county hover shows the place card with name, value, as-of and source, Back to US returns, party measures exist with their
    registration wording, and the page never scrolls on any of it."""
    out: list[str] = []
    labels = await page.evaluate(f"[...document.querySelectorAll('{metric} button')].map((b) => b.textContent)")
    if not any(t.startswith("Party lead of banked ballots") for t in labels) or not any(t.startswith("Party shift vs 2022") for t in labels):
        out.append(f"{name}: the map lacks the party lead / party shift measures: {labels}")
    ctl = await page.evaluate(f"[...document.querySelectorAll('{box} .map-tools button')].map((b) => b.textContent)")
    if ctl != ["+", "−", "Fit"] or not await page.evaluate(f"!!document.querySelector('{box}').usMap && !!document.querySelector('{box} .map-national')"):
        out.append(f"{name}: map controls are {ctl}, expected + − Fit and a Back to US button")
    await page.evaluate(f"document.querySelector('{box}').scrollIntoView({{block: 'center'}})")
    await page.wait_for_timeout(300)
    if await page.evaluate(f"document.querySelector('{box}').dataset.level") == "counties":  # a state was opened from the picker
        await page.click(f"{box} .map-national")
        await page.wait_for_timeout(1000)
    y0 = await page.evaluate("scrollY")

    async def still(step: str) -> None:
        y = await page.evaluate("scrollY")
        if abs(y - y0) > 1:
            out.append(f"{name}: the page scrolled ({y0} -> {y}) on {step}")

    async def level() -> str:
        return await page.evaluate(f"document.querySelector('{box}').dataset.level")

    for label in ("Party lead of banked ballots", "Party shift vs 2022"):
        await page.click(f"{metric} button:has-text('{label}')")
        await page.wait_for_timeout(250)
        lg = await page.evaluate(f"document.querySelector('{legend}').textContent")
        if "Registration, not votes; unaffiliated and crossover voters are large." not in lg:
            out.append(f"{name}: the {label!r} legend lacks the registration wording: {lg!r}")
        await still(f"choosing {label!r}")
    await page.click(f"{metric} button:has-text('Party lead of banked ballots')")
    await page.wait_for_timeout(250)
    pt = await page.evaluate(f"document.querySelector('{box}').usMap.pointOf('nc')")
    await page.mouse.move(pt[0] - 5, pt[1] - 5)
    await page.mouse.move(*pt)
    await page.wait_for_timeout(300)
    card = await page.evaluate(f"(() => {{ const e = document.querySelector('{tip}'); return e.hidden ? '' : e.textContent; }})()")
    if "North Carolina" not in card:
        out.append(f"{name}: hovering a state shows no place card with its name: {card!r}")
    await page.mouse.click(*pt)
    try:
        await page.wait_for_function(f"document.querySelector('{box}').dataset.level === 'counties' && document.querySelector('{box}').dataset.focus === 'nc'", timeout=4000)
    except Exception:
        out.append(f"{name}: clicking a state did not zoom to its counties (level {await level()})")
        return out
    await page.wait_for_timeout(1300)
    await still("a state click")
    cp = await page.evaluate(f"document.querySelector('{box}').usMap.countyPoint('37183')")
    if not cp:
        out.append(f"{name}: no county point for Wake County")
    else:
        await page.mouse.move(cp[0] - 3, cp[1] - 3)
        await page.mouse.move(*cp)
        await page.wait_for_timeout(300)
        card = await page.evaluate(f"(() => {{ const e = document.querySelector('{tip}'); return e.hidden ? '' : e.textContent; }})()")
        if "Wake" not in card or "As of" not in card or "Source" not in card or "D minus R" not in card:
            out.append(f"{name}: a county hover card lacks name / value / as-of / source: {card!r}")
    await still("a county hover")
    await page.click(f"{box} .map-tools button[data-act='in']")
    await page.wait_for_timeout(500)
    await still("zoom in")
    await page.click(f"{box} .map-tools button[data-act='reset']")
    await page.wait_for_timeout(900)
    await still("Fit")
    if await level() != "counties":
        await page.mouse.click(*pt)
        await page.wait_for_timeout(1300)
    await page.click(f"{box} .map-national")
    await page.wait_for_timeout(1000)
    if await level() != "states":
        out.append(f"{name}: Back to US did not return to the states (level {await level()})")
    await still("Back to US")
    return out


async def check_ev_history(page: Page) -> list[str]:
    """'Has the early vote tracked the result?' section: file and page agree, picker syncs with the page's state choice in place (no scrolling), table sorts, chart toggles."""
    out: list[str] = []
    await page.wait_for_function("window.__ehReady === true", timeout=15000)
    h = await page.evaluate("fetch('data/ev2026/ev_history_all.json').then((r) => r.ok ? r.json() : null)")
    if not h:
        return ["ev_history_all.json is not published"]
    withp = [s_ for s_ in h["states"] if s_["n"] > 0]
    q = await page.evaluate("""() => ({ strip: document.querySelectorAll('#eh-strip .eh-s').length, cards: document.querySelectorAll('#eh-cards .kf-card').length, tiles: document.querySelectorAll('#eh-tiles .eh-tile').length,
        dots: document.querySelectorAll('#eh-tiles .eh-tile circle').length, rows: document.querySelectorAll('#eh-table tbody tr').length, picks: document.querySelectorAll('#eh-pick option').length,
        chart: document.querySelectorAll('#eh-chart circle.eh-dot').length, caveat: document.querySelector('#eh-caveat')?.textContent ?? '', lic: document.querySelector('#eh-lic')?.textContent ?? '',
        strip0: document.querySelector('#eh-strip')?.textContent ?? '', head: document.querySelector('#eh-h')?.textContent ?? '' })""")
    n_dots = sum(1 for r in h["rows"] if r.get("gap") is not None)
    n_pairs = sum(1 for p_ in h["pairs"]["party_same"] if "skipped" not in p_)
    if q["strip"] != 5 or q["cards"] != 6 or q["tiles"] != len(withp) or q["dots"] != n_dots or q["rows"] != len(withp) or q["picks"] != len(h["states"]) + 1 or q["chart"] != n_pairs:
        out.append(f"history section does not match ev_history_all.json (states with party {len(withp)}, dots {n_dots}, pairs {n_pairs}): {q}")
    if "Has the early vote tracked the result" not in q["head"] or "not used by any model" not in q["caveat"] or "permission" not in q["lic"]:
        out.append(f"history section lacks its heading, the registered-test caveat or the licence line: {q}")
    g = h["pooled"]["gap"]
    if f"{g['n']}" not in q["strip0"] or f"{g['right']} of {g['right_n']}" not in q["strip0"]:
        out.append(f"national strip does not show the pooled counts of the file ({g['n']}, {g['right']} of {g['right_n']}): {q['strip0'][:200]}")
    # picking a state here updates the page's state and the section in place, without scrolling; so does the reverse
    await page.evaluate("document.querySelector('#eh-sec').scrollIntoView({block: 'start'})")
    await page.wait_for_timeout(200)
    probe = """() => ({ url: new URLSearchParams(location.search).get('st'), pageSel: document.querySelector('#st-pick').value, mine: document.querySelector('#eh-pick').value,
        detail: document.querySelector('#eh-detail').textContent, pressed: [...document.querySelectorAll('#eh-tiles .eh-tile[aria-pressed=true]')].map((e) => e.dataset.st), y: window.scrollY, sel: document.querySelectorAll('#eh-table tbody tr.sel').length, dotsSel: document.querySelectorAll('#eh-chart circle.eh-dot.sel').length })"""
    pick_name = {s_["st"]: s_["name"] for s_ in h["states"]}
    for how, st in (("section picker", "nc"), ("tile", "pa"), ("table row", "co"), ("page picker", "az")):
        target = {"tile": f"#eh-tiles .eh-tile[data-st='{st}']", "table row": f"#eh-table tbody tr[data-st='{st}'] button.ev-name"}.get(how)
        if target:
            await page.locator(target).scroll_into_view_if_needed()  # the test's own scrolling comes first; only the page's reaction is measured
            await page.wait_for_timeout(150)
        anchor = target or ("#eh-pick" if how == "section picker" else "#eh-table tbody tr[data-st='co']")  # something in view: it must stay where it is
        topq = "(a) => document.querySelector(a).getBoundingClientRect().top"
        t0 = await page.evaluate(topq, anchor)
        if how == "section picker":
            await page.select_option("#eh-pick", st)
        elif target:
            await page.click(target)
        else:
            await page.evaluate("(s) => { const e = document.querySelector('#st-pick'); e.value = s; e.dispatchEvent(new Event('change')); }", st)
        await page.wait_for_function("(s) => new URLSearchParams(location.search).get('st') === s && document.querySelector('#st-pick').value === s && document.querySelector('#eh-pick').value === s", arg=st)
        await page.wait_for_timeout(600)
        r = await page.evaluate(probe)
        if r["pageSel"] != st or r["mine"] != st or pick_name[st] not in r["detail"] or r["pressed"] != [st]:
            out.append(f"history {how} {st}: page and section are not in step: {r}")
        t1 = await page.evaluate(topq, anchor)
        if abs(t1 - t0) > 2:  # what the viewer is looking at must not move (browser scroll anchoring keeps it still when the map panel above changes height)
            out.append(f"history {how} {st}: {anchor} jumped in the viewport from {t0:.0f} to {t1:.0f}")
        if r["sel"] != 1:
            out.append(f"history {how} {st}: {r['sel']} highlighted table rows")
    # the state view shows the early-vote share behind the gap and the 2026 context row
    az = next(s_ for s_ in h["states"] if s_["st"] == "az")
    row = next((r_ for r_ in h["rows"] if r_["st"] == "az" and r_.get("gap") is not None), None)
    det = await page.evaluate("document.querySelector('#eh-detail').textContent")
    if row and (f"{row['ev_d2']:.1f}%" not in det or "2026" not in det):
        out.append(f"state view lacks the early-vote share of {row['ev_d2']:.1f}% or the 2026 row: {det[:300]}")
    if (az.get("ev26") or {}).get("range") and "descriptive, not a projection" not in det:
        out.append("state view gives a range from the past gaps without the 'descriptive, not a projection' label")
    # sorting: mean |gap| high to low, then back to the full list of states
    await page.click("#eh-table thead button[data-k='abs']")
    first = await page.evaluate("[...document.querySelectorAll('#eh-table tbody tr')].map((r) => r.dataset.st)")
    order = [s_["st"] for s_ in sorted(withp, key=lambda s_: (-s_["mean_abs_gap"], s_["name"]))]
    if first != order:
        out.append(f"table sort by mean |gap| gives {first[:6]}, expected {order[:6]}")
    await page.click("#eh-filter button:nth-child(2)")
    n_all = await page.evaluate("document.querySelectorAll('#eh-table tbody tr').length")
    if n_all != len(h["states"]):
        out.append(f"'All states' shows {n_all} rows, expected {len(h['states'])}")
    await page.click("#eh-filter button:nth-child(1)")
    # the change chart: both measures, raw and cycle-adjusted
    for mi, key in ((2, "volume_same"), (1, "party_same")):
        await page.click(f"#eh-measure button:nth-child({mi})")
        for di in (2, 1):
            await page.click(f"#eh-dm button:nth-child({di})")
            c = await page.evaluate("document.querySelectorAll('#eh-chart circle.eh-dot').length")
            want = sum(1 for p_ in h["pairs"][key] if "skipped" not in p_)
            if c != want:
                out.append(f"change chart {key} has {c} dots, expected {want}")
    return out


async def check_brief(page: Page, viewport_name: str) -> list[str]:
    """'What the early data says' (ev-brief.js), first section of turnout.html and earlyvote.html: four generated answers with a chart each, a bottom line,
    the numbers agree with the data files, no 'forecast' wording, and opening the 'how computed' details never scrolls the page."""
    out: list[str] = []
    ready = False
    for _ in range(40):
        if await page.evaluate("!!window.__briefReady"):
            ready = True
            break
        await page.wait_for_timeout(250)
    if not ready:
        return ["the 'What the early data says' section did not finish loading"]
    q = await page.evaluate("""() => ({ h: document.querySelector('#brief-h')?.textContent ?? '', first: document.querySelector('main > nav + section')?.id ?? '',
        bottom: document.querySelector('#brief-bottom')?.textContent ?? '', label: document.querySelector('.brief-label')?.textContent ?? '', text: document.querySelector('#brief')?.textContent ?? '',
        cards: ['model', 'history', 'turnout', 'returns'].map((k) => ({ k, q: document.querySelector(`#brief-${k} h3`)?.textContent ?? '', a: document.querySelector(`#brief-${k} [data-brief=answer]`)?.textContent ?? '',
          dots: document.querySelectorAll(`#brief-${k} .brief-svg circle, #brief-${k} .brief-svg rect`).length, how: !!document.querySelector(`#brief-${k} details.brief-how`) })),
        wide: document.documentElement.scrollWidth - document.documentElement.clientWidth })""")
    if q["h"] != "What the early data says" or q["first"] != "brief":
        out.append(f"the brief is not the first section titled 'What the early data says': {q['h']!r} first={q['first']!r}")
    if len(q["bottom"]) < 60 or "not used by any model" not in q["label"] or "registration" not in q["label"].lower() or "failed its registered test" not in q["label"]:
        out.append(f"brief lacks its bottom line or its registration / failed-test / not-used labels: {q['bottom']!r} {q['label']!r}")
    if re.search(r"forecast", q["text"], re.I):
        out.append("brief uses the word 'forecast'")
    if q["wide"] > 1:
        out.append(f"brief makes the page scroll sideways by {q['wide']}px on {viewport_name}")
    for c in q["cards"]:
        if len(c["q"]) < 12 or len(c["a"].strip()) < 40 or not re.search(r"\d", c["a"]) or not c["how"]:
            out.append(f"brief answer {c['k']!r} is missing its question, generated answer or 'how computed' details: {c}")
        if c["k"] != "returns" and c["dots"] < 3:
            out.append(f"brief answer {c['k']!r} has no chart ({c['dots']} marks)")
    # the numbers agree with the files
    races = await page.evaluate("fetch('data/ev2026/ev_races.json').then((r) => r.json())")
    diffs = [r["implied"]["vs_model"]["diff"] for r in races["races"] if r["implied"].get("ok") and r["implied"].get("vs_model", {}).get("diff") is not None]
    marks = await page.evaluate("document.querySelectorAll('#brief-model .brief-svg circle').length")
    if diffs and marks != len(diffs):
        out.append(f"model dot plot has {marks} dots, expected {len(diffs)}")
    if diffs:
        m = sum(diffs) / len(diffs)
        want = f"average {'even' if abs(m) < 0.05 else ('D' if m > 0 else 'R') + '+' + format(abs(m), '.1f')}"
        if want not in q["cards"][0]["a"]:
            out.append(f"model answer does not quote the average {want!r}: {q['cards'][0]['a']!r}")
    hist = await page.evaluate("fetch('data/ev2026/ev_history_all.json').then((r) => r.json())")
    rows = [r for r in hist["rows"] if r.get("gap") is not None and r.get("act") is not None]
    right = sum(1 for r in rows if r["right"])
    if f"Right on the winner in {right} of {len(rows)} state-cycles" not in q["cards"][1]["a"]:
        out.append(f"history answer does not say right {right} of {len(rows)}: {q['cards'][1]['a']!r}")
    # opening every 'how computed' details never moves the page
    y0 = await page.evaluate("scrollY")
    for sel in ["#brief-model .brief-how summary", "#brief-history .brief-how summary", "#brief-turnout .brief-how summary", "#brief-returns .brief-how summary"]:
        await page.evaluate(f"document.querySelector('{sel}').click()")
    y1 = await page.evaluate("scrollY")
    if y1 != y0:
        out.append(f"opening the brief's details scrolled the page ({y0} -> {y1})")
    return out


async def check_turnout(page: Page, viewport_name: str) -> list[str]:
    """Turnout dashboard: header strip, map of all 51, state panel with its cards / bar / party / model section, county scatter and table, keyboard, wording."""
    out: list[str] = []
    probe = """() => ({ ready: !!window.__tddReady, chips: document.querySelectorAll('#tdd-chips .chip').length, picks: document.querySelectorAll('#tdd-pick option').length,
        map: document.querySelectorAll('#tdd-map .ev-kb button[data-st]').length, dc: document.querySelectorAll("#tdd-map .ev-kb button[data-st='dc']").length, metrics: document.querySelectorAll('#tdd-metric button').length,
        cards: document.querySelectorAll('#tdd-kpis .td-card').length, bar: document.querySelectorAll('#tdd-bar .td-seg').length || !!document.querySelector('#tdd-bar .note'),
        model: [...document.querySelectorAll('#tdd-model li')].map((e) => e.textContent.trim().slice(0, 3)), hist: document.querySelector('#tdd-hist')?.textContent ?? '',
        label: document.querySelector('.td-label')?.textContent ?? '', title: document.querySelector('#tdd-title')?.textContent ?? '', st: document.querySelector('#tdd-pick')?.value ?? '',
        ctx: document.querySelectorAll('#tdd-model .td-ctx').length, sources: document.querySelectorAll('#tdd-sources tbody tr').length })"""
    p = await page.evaluate(probe)
    model = await page.evaluate("fetch('data/ev2026/turnout_model.json').then((r) => r.ok ? r.json() : null)")
    if not model:
        return ["turnout_model.json is not published"]
    # on load no state is chosen: no ?st=, the picker is on its placeholder, the side panel is the national summary of the measure, the dashboard below a prompt
    first = await page.evaluate("""() => ({ hasSt: new URLSearchParams(location.search).has('st'), pick: document.querySelector('#tdd-pick').value, none: document.querySelector('#tdd-panel').classList.contains('td-none'),
        prompt: document.querySelector('#tdd-prompt')?.offsetParent !== null, title: document.querySelector('#tdd-title').offsetParent !== null, side: document.querySelector('#tdd-sel').textContent,
        names: document.querySelectorAll('#tdd-sel li button[data-st]').length })""")
    if first["hasSt"] or first["pick"] or not first["none"] or not first["prompt"] or first["title"]:
        out.append(f"turnout dashboard opens with a state chosen or without its prompt: {first}")
    if "Highest" not in first["side"] or "Lowest" not in first["side"] or "Click a state for details" not in first["side"] or first["names"] != 10:
        out.append(f"the side panel is not the national summary (highest / lowest five, click a state): {first['side'][:200]!r}")
    await page.select_option("#tdd-pick", "TX")
    await page.wait_for_function("() => document.querySelector('#tdd-title').textContent === 'Texas'")
    await page.wait_for_timeout(500)
    p = await page.evaluate(probe)
    if not p["ready"] or p["chips"] != 7 or p["picks"] != 52 or p["map"] != 51 or p["dc"] != 1 or p["metrics"] < 6 or p["cards"] != 10:
        out.append(f"turnout dashboard is missing its header strip, picker, map of 51 or the ten cards: {p}")
    if p["model"] != ["(a)", "(b)", "(c)", "(d)"] or not p["ctx"]:
        out.append(f"'how the model sees this' must have items (a) to (d) and the unvalidated-context label: {p}")
    if "not used by any model" not in p["label"].lower() or not p["hist"]:
        out.append("turnout dashboard lacks its label or the history verdict line")
    if p["st"] not in model["states"] or model["states"][p["st"]]["name"] != p["title"]:
        out.append(f"panel title {p['title']!r} does not match the selected state {p['st']}")
    # projected final turnout: the map measure, the panel block (number, interval, against the model, sparkline, inputs, backtest table), the no-history wording
    ps = model.get("proj_summary")
    if not ps:
        out.append("turnout_model.json has no proj_summary")
    else:
        if not await page.evaluate("[...document.querySelectorAll('#tdd-metric button')].some((b) => b.textContent === 'Projected final turnout vs model expected')"):
            out.append("map has no 'Projected final turnout vs model expected' measure")
        ok_st = next((s_ for s_ in model["states"].values() if s_["proj"]["ok"]), None)
        bad_st = next((s_ for s_ in model["states"].values() if not s_["proj"]["ok"] and s_["proj"]["why"] == "no history for this state"), None)
        for st, want_ok in ((ok_st, True), (bad_st, False)):
            if not st:
                continue
            await page.select_option("#tdd-pick", st["st"])
            await page.wait_for_function("(n) => document.querySelector('#tdd-title').textContent === n", arg=st["name"])
            await page.wait_for_timeout(300)
            q = await page.evaluate("""() => ({ t: document.querySelector('#tdd-proj')?.textContent ?? '', spark: !!document.querySelector('#tdd-proj svg.td-proj-spark') || /One refresh so far/.test(document.querySelector('#tdd-proj')?.textContent ?? ''),
                rows: document.querySelectorAll('#tdd-proj .td-proj-bt tbody tr').length, cards: document.querySelectorAll('#tdd-proj .td-card').length, items: document.querySelectorAll('#tdd-proj .td-model li').length })""")
            if "Projected final turnout" not in q["t"] or "Unregistered, updates with incoming data; the registered models do not use it" not in q["t"]:
                out.append(f"{st['st']}: projection block lacks its title or the 'Unregistered' label: {q['t'][:120]!r}")
            if want_ok:
                p_ = st["proj"]
                if f"{p_['proj_total']:,}" not in q["t"] or f"{p_['low']:,}" not in q["t"] or f"{p_['high']:,}" not in q["t"] or "the model" not in q["t"] or not q["spark"] or q["cards"] != 4 or not q["items"]:
                    out.append(f"{st['st']}: projection block does not show the number, interval, comparison with the model, sparkline and inputs of turnout_model.json: {q}")
                if q["rows"] != len(ps["backtest"]["rows"]):
                    out.append(f"{st['st']}: backtest table has {q['rows']} rows, expected {len(ps['backtest']['rows'])}")
            elif "no history for this state" not in q["t"]:
                out.append(f"{st['st']}: a state without history must say 'no history for this state'")
    # every state can be opened; a state with counties draws the scatter and a table with one row per county; (b) says why when it is not computed
    plan = [s_ for s_ in model["states"].values() if s_["counties"]][:2] + [s_ for s_ in model["states"].values() if s_["composition"]["ok"]][:1]
    plan += [s_ for s_ in model["states"].values() if not s_["banked"] and not s_["counties"]][:1]
    for st in {s_["st"]: s_ for s_ in plan}.values():
        await page.select_option("#tdd-pick", st["st"])
        await page.wait_for_function("(n) => document.querySelector('#tdd-title').textContent === n", arg=st["name"])
        await page.wait_for_timeout(400)
        q = await page.evaluate("""() => ({ bubbles: document.querySelectorAll('#tdd-scatter circle.td-bub').length, rows: document.querySelectorAll('#tdd-cty-table tbody tr').length,
            ctyShown: !document.querySelector('#tdd-cty-sec').hidden, b: document.querySelectorAll('#tdd-model li')[1]?.textContent ?? '',
            series: document.querySelectorAll('#tdd-series svg path.td-line').length, seriesNote: document.querySelector('#tdd-series .note')?.textContent ?? '' })""")
        n = len(st["counties"])
        if q["ctyShown"] != bool(n) or (n and (q["rows"] != n or q["bubbles"] != sum(1 for c in st["counties"] if c["exp"] and not c.get("no_figure")))):
            out.append(f"{st['st']}: county panel shows {q['rows']} rows and {q['bubbles']} bubbles, expected {n}")
        if st["composition"]["ok"] and "Partial-day figure" not in q["b"]:
            out.append(f"{st['st']}: composition shift has no partial-day caption: {q['b']!r}")
        if not st["composition"]["ok"] and "Not computed" not in q["b"]:
            out.append(f"{st['st']}: composition shift is not computed, and the panel must say why: {q['b']!r}")
        w = await page.evaluate("""() => ({ kpis: document.querySelector('#tdd-kpis').textContent, who: document.querySelector('#tdd-who')?.textContent ?? '', head: [...document.querySelectorAll('#tdd-cty-table th')].map((e) => e.textContent) })""")
        if "ballots not yet returned" not in w["kpis"] and "Not returned yet" not in w["kpis"]:
            out.append(f"{st['st']}: no 'Mail ballots not yet returned' card")
        if "Vs 2022 final pre-election" not in w["kpis"] or "New voters" not in w["who"]:
            out.append(f"{st['st']}: lacks the 'vs 2022 final pre-election' card or the 'Who is voting' block")
        if (st["outstanding"] or {}).get("ok") and f"{st['outstanding']['value']:,}" not in w["kpis"]:
            out.append(f"{st['st']}: the outstanding-mail card does not show {st['outstanding']['value']:,}")
        if st["counties"] and (any(c.get("mail") for c in st["counties"]) != any("Mail not yet returned" in h for h in w["head"]) or any(c.get("party") for c in st["counties"]) != any("Party of banked" in h for h in w["head"])):
            out.append(f"{st['st']}: county table columns do not match the model file (mail / party)")
        if st["banked"] is None and p["cards"] and "no count" not in await page.evaluate("document.querySelector('#tdd-kpis').textContent"):
            out.append(f"{st['st']}: a state with no count should say so")
    # every map measure redraws 51 shapes; the map is keyboard operable
    for i in range(p["metrics"]):
        await page.click(f"#tdd-metric button:nth-child({i + 1})")
        n = await page.evaluate("document.querySelectorAll('#tdd-map .ev-kb button[data-st]').length")
        if n != 51:
            out.append(f"map measure {i + 1} drew {n} shapes")
    out.extend(await check_county_zoom(page, "#tdd-map", "#tdd-tip", "#tdd-legend", "#tdd-metric", "turnout map"))
    await page.focus("#tdd-map .ev-kb button[data-st='tx']")
    await page.keyboard.press("Enter")
    await page.wait_for_function("() => document.querySelector('#tdd-title').textContent === 'Texas'")
    out.extend(await check_evr_table(page, viewport_name))
    text = await page.evaluate("document.body.innerText")
    for bad in ("NaN", "undefined", "null", "[object"):
        if bad in text:
            out.append(f"page text contains {bad!r}")
    if re.search(r"\b(forecast(?!\.)|probabilit|chance of winning|odds)\b", text, re.I):
        out.append("page text mentions a forecast, probability or odds")
    if viewport_name == "mobile" and await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"):
        out.append("page scrolls sideways on mobile")
    return out


async def check_evr_table(page: Page, viewport_name: str) -> list[str]:
    """turnout.html 'Races: early vote vs the model' (ev-races.js): one row per race of ev_races.json, sortable, wording, a click selects the state panel in place."""
    out: list[str] = []
    ev = await page.evaluate("fetch('data/ev2026/ev_races.json').then((r) => r.ok ? r.json() : null).catch(() => null)")
    if not ev:
        if not await page.evaluate("document.getElementById('evr-table').hidden"):
            out.append("ev_races.json is missing but the races table is shown")
        return out
    await page.wait_for_function("window.__evrTableReady === true", timeout=15000)
    p = await page.evaluate("""() => ({ rows: document.querySelectorAll('.evr-races tbody tr').length, heads: document.querySelectorAll('.evr-races thead th').length,
        text: document.getElementById('evr-table').innerText, hidden: document.getElementById('evr-table').hidden })""")
    low = p["text"].lower()
    if p["hidden"] or p["rows"] != len(ev["races"]) or p["heads"] != 8:
        out.append(f"races table has {p['rows']} rows of {len(ev['races'])} and {p['heads']} columns: {p['hidden']=}")
    for want in ("registration-based", "not used by any model", "failed the project's registered test (right on direction 61 %, made estimates worse)", f"{ev['n_states_with_estimate']} states"):
        if want not in low:
            out.append(f"races table lacks the wording {want!r}")
    if re.search(r"forecast", low):
        out.append("races table uses the word 'forecast'")
    await page.click(".evr-races thead th:nth-child(3) button")
    sort1 = await page.evaluate("document.querySelector('.evr-races thead th:nth-child(3)').getAttribute('aria-sort')")
    await page.click(".evr-races thead th:nth-child(3) button")
    sort2 = await page.evaluate("document.querySelector('.evr-races thead th:nth-child(3)').getAttribute('aria-sort')")
    if {sort1, sort2} != {"ascending", "descending"}:
        out.append(f"races table sort does not toggle: {sort1} / {sort2}")
    await page.click(".evr-filter button[data-only='1']")
    n_est = await page.evaluate("document.querySelectorAll('.evr-races tbody tr').length")
    if n_est != sum(1 for r in ev["races"] if r["implied"]["ok"]):
        out.append(f"'with an EV-implied value' shows {n_est} rows")
    await page.click(".evr-filter button[data-only='0']")
    # a click on a race selects that state's panel in place: the dashboard's picker and title change, the page is not scrolled down
    current = await page.evaluate("document.getElementById('tdd-pick').value")
    want = next(r for r in ev["races"] if r["state"] != current)
    name = await page.evaluate("(st) => [...document.querySelectorAll('#tdd-pick option')].find((o) => o.value === st)?.textContent.replace(/ \\(.*\\)$/, '') ?? ''", want["state"])
    y0 = await page.evaluate("scrollY")
    row_top = f"document.querySelector(\".evr-races tbody tr[data-st='{want['state']}']\").getBoundingClientRect().top"
    await page.evaluate(f"(document.querySelector(\".evr-races tbody tr[data-st='{want['state']}']\")).scrollIntoView({{ block: 'center' }})")
    y0 = await page.evaluate("scrollY")
    t0 = await page.evaluate(row_top)
    await page.click(f".evr-races tbody tr[data-st='{want['state']}'] .evr-pick")
    await page.wait_for_function("(n) => document.querySelector('#tdd-title').textContent === n", arg=name, timeout=10000)
    y1 = await page.evaluate("scrollY")
    t1 = await page.evaluate(row_top)
    sel = await page.evaluate("document.querySelectorAll('.evr-races tbody tr.sel').length")
    # the panel above may change height (the browser's scroll anchoring then moves scrollY with it); a scroll is a row that left its place
    await page.wait_for_timeout(2800)  # the panel's series finish drawing and the position is held
    y2 = await page.evaluate("scrollY")
    t2 = await page.evaluate(row_top)
    if abs(t1 - t0) > 1 or abs(t2 - t0) > 1:
        out.append(f"clicking a race moved it on screen (scrollY {y0} -> {y1} -> {y2}; the row at {t0:.1f} -> {t1:.1f} -> {t2:.1f})")
    if not sel:
        out.append("the clicked race's row is not marked selected")
    return out


async def check_evr_card(page: Page, viewport_name: str) -> list[str]:
    """election.html race panel (ev-races.js): a collapsed one-line 'Early vote' card for the selected race, details on expand, no panel changes height, no scrolling."""
    out: list[str] = []
    ev = await page.evaluate("fetch('data/ev2026/ev_races.json').then((r) => r.ok ? r.json() : null).catch(() => null)")
    race = await page.evaluate("document.querySelector('#race').value")
    rec = next((r for r in ev["races"] if race in (r["race_id"], r["key"])), None) if ev else None
    if not rec:
        await page.wait_for_timeout(500)
        if not await page.evaluate("document.getElementById('evr').hidden"):
            out.append(f"early-vote card is shown for {race!r}, which has no ev_races.json entry")
        return out
    await page.wait_for_function("() => !document.getElementById('evr').hidden && !!document.querySelector('#evr .evr-sum')", timeout=15000)
    await page.wait_for_timeout(1500)  # the readout and chart settle before the heights are taken
    geo = """() => { const r = (s) => document.querySelector(s)?.getBoundingClientRect().height ?? null; return [r('.p-readout'), r('.p-timeline'), r('.p-tabs'), r('.tab-panes'), r('.p-map')]; }"""
    before = await page.evaluate(geo)
    y0 = await page.evaluate("scrollY")
    sumry = await page.evaluate("({ t: document.querySelector('#evr .evr-sum').innerText, open: document.querySelector('#evr .evr-sum').getAttribute('aria-expanded'), body: !document.querySelector('#evr .evr-body').hidden })")
    if not sumry["t"].strip().lstrip("▸▾ ").startswith("Early vote:") or sumry["open"] != "false" or sumry["body"]:
        out.append(f"early-vote card is not a collapsed 'Early vote:' line: {sumry}")
    if rec["implied"]["ok"] and "failed its registered test" not in sumry["t"]:
        out.append("an EV-implied figure is shown without the registered-test flag")
    await page.evaluate("document.querySelector('#evr .evr-sum').click()")
    await page.wait_for_timeout(300)
    body = await page.evaluate("({ t: document.querySelector('#evr .evr-body').innerText, hidden: document.querySelector('#evr .evr-body').hidden })")
    after = await page.evaluate(geo)
    y1 = await page.evaluate("scrollY")
    low = body["t"].lower()
    if body["hidden"] or "registration-based" not in low or "not used by any model" not in low or "live count" not in low:
        out.append(f"expanded card lacks its wording or live section: {body['t'][:200]!r}")
    if rec["implied"]["ok"] and "failed the project's registered test (right on direction 61 %, made estimates worse)" not in low:
        out.append("expanded card has an EV-implied figure without the registered-test caveat")
    for bad in ("nan", "undefined", "[object"):
        if bad in low:
            out.append(f"expanded card contains {bad!r}")
    # no existing panel changes height (the page's own flow below the card may, on a narrow screen)
    if viewport_name == "desktop" and any(abs((a or 0) - (b or 0)) > 0.5 for a, b in zip(before, after)):
        out.append(f"expanding the early-vote card changed a panel height: {before} -> {after}")
    if viewport_name != "desktop" and any(abs((a or 0) - (b or 0)) > 0.5 for a, b in zip(before[:2], after[:2])):
        out.append(f"expanding the early-vote card changed the readout or timeline height: {before} -> {after}")
    if abs(y1 - y0) > 2:
        out.append(f"expanding the early-vote card scrolled the page ({y0} -> {y1})")
    await page.evaluate("document.querySelector('#evr .evr-sum').click()")
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


COUNT_MOCK = "https://count-mock.trycloudflare.com/"
COUNT_FIXTURE = ROOT / "tests" / "fixtures" / "count_live"


async def install_count_routes(page: Page) -> None:
    """count.html against a mock live dir (tests/fixtures/count_live, the tunnel's layout with a few rows); live_link.json points at it."""

    async def live(route: Any) -> None:
        rel = route.request.url.split("trycloudflare.com/", 1)[1].split("?", 1)[0]
        f = COUNT_FIXTURE / rel
        if not f.is_file():
            await route.fulfill(status=404, body="missing", headers={"access-control-allow-origin": "*"})
            return
        await route.fulfill(status=200, body=f.read_text(), content_type="application/json", headers={"access-control-allow-origin": "*"})

    async def link(route: Any) -> None:
        await route.fulfill(status=200, json={"url": COUNT_MOCK, "id": "us2026"})

    await page.route(re.compile(r"https://count-mock\.trycloudflare\.com/.*"), live)
    await page.route(re.compile(r".*/data/live_link\.json.*"), link)


async def check_count(page: Page, viewport_name: str) -> list[str]:
    """The live count map: it draws, its three modes and tabs switch, a click or a table row selects without scrolling, search and the replay slider work."""
    out: list[str] = []
    info = await page.evaluate(
        """() => ({ paths: document.querySelectorAll('#cnt-svg .lay-house .u').length, states: document.querySelectorAll('#cnt-svg .lay-state .u').length,
          fills: new Set([...document.querySelectorAll('#cnt-svg .lay-house .u')].map((p) => p.style.fill)).size,
          caveats: !!document.querySelector('#caveats'), summary: document.querySelector('#summary').innerText.length, rows: document.querySelectorAll('#race-table tbody tr[data-key]').length })"""
    )
    if info["paths"] != 435 or info["states"] < 50 or info["fills"] < 3:
        out.append(f"count map is not drawn (districts, states, distinct fills): {info}")
    if not info["caveats"] or not info["summary"] or not info["rows"]:
        out.append(f"count page lacks its caveats, summary or table: {info}")
    legends = set()
    for mode in ("proj", "gap", "count"):
        await page.click(f"#modes [data-mode={mode}]")
        await page.wait_for_timeout(600)
        pressed = await page.evaluate("(m) => document.querySelector(`#modes [data-mode=${m}]`).getAttribute('aria-pressed')", mode)
        legends.add(await page.inner_text("#legend"))
        fills = await page.evaluate("() => new Set([...document.querySelectorAll('#cnt-svg .lay-house .u')].map((p) => p.style.fill)).size")
        if pressed != "true" or fills < 2:
            out.append(f"mode {mode}: pressed={pressed}, fills={fills}")
    if len(legends) != 3:
        out.append("the three colour modes do not change the legend")
    for tab in ("senate", "governor", "house"):
        await page.click(f"#tabs [data-tab={tab}]")
        await page.wait_for_timeout(500)
        shown = await page.evaluate("(t) => { const h = document.querySelector('#cnt-svg .lay-house').classList.contains('off'); return t === 'house' ? !h : h; }", tab)
        if not shown:
            out.append(f"tab {tab} did not switch the map layer")
    # click a district: the panel fills in and the page does not scroll
    await page.evaluate("window.scrollTo(0, 120)")
    y0 = await page.evaluate("window.scrollY")
    await page.evaluate("document.querySelector('#cnt-svg .u[data-key=\"house:FL-02\"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))")
    await page.wait_for_timeout(900)
    panel = await page.inner_text("#panel")
    y1 = await page.evaluate("window.scrollY")
    if "FL-02" not in panel or "Counted" not in panel:
        out.append(f"clicking a district did not fill the panel: {panel[:120]!r}")
    if abs(y1 - y0) > 1:
        out.append(f"clicking a district scrolled the page ({y0} -> {y1})")
    # hover tooltip
    box = await page.evaluate("() => { const r = document.querySelector('#cnt-svg .u[data-key=\"house:FL-02\"]').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
    if viewport_name == "desktop" and 0 <= box[1] <= 900:
        await page.mouse.move(box[0] - 3, box[1] - 3)
        await page.mouse.move(box[0], box[1], steps=4)
        await page.wait_for_timeout(500)
        if not await page.evaluate("() => !document.querySelector('#tip').hidden && document.querySelector('#tip').innerText.includes('FL-02')"):
            out.append("hovering a district shows no tooltip")
    # table row selects in place
    await page.evaluate("() => document.querySelector('#race-table tbody tr[data-key=\"house:GA-12\"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))")
    await page.wait_for_timeout(300)
    if "GA-12" not in await page.inner_text("#panel") or abs(await page.evaluate("window.scrollY") - y0) > 1:
        out.append("a table row did not select its race in place")
    # search selects and zooms
    await page.fill("#q", "NC-05")  # typing scrolls the field into view; the selection itself must not scroll
    y2 = await page.evaluate("window.scrollY")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(900)
    if "NC-05" not in await page.inner_text("#panel") or abs(await page.evaluate("window.scrollY") - y2) > 1:
        out.append("search did not select NC-05 in place")
    # projections banner: the mock's projected race is listed with a reliability tag; clicking it selects the race without scrolling
    ban = await page.evaluate("() => ({ n: document.querySelectorAll('#proj-list .pb-item').length, tags: document.querySelectorAll('#proj-list .pb-tag').length, key: document.querySelector('#proj-list .pb-item')?.dataset.key ?? '' })")
    if ban["n"] < 1 or ban["tags"] < 1:
        out.append(f"projections banner shows no projected race with a reliability tag: {ban}")
    else:
        y3 = await page.evaluate("window.scrollY")
        await page.evaluate("document.querySelector('#proj-list .pb-item').dispatchEvent(new MouseEvent('click', { bubbles: true }))")
        await page.wait_for_timeout(700)
        selected = await page.evaluate("window.__cnt.S.sel")
        if selected != ban["key"] or abs(await page.evaluate("window.scrollY") - y3) > 1:
            out.append(f"clicking a projection did not select {ban['key']} in place (selected {selected})")
    # official-source count-only race (aux_counts.json): a count-only row with its totals and tag, not in the projections banner
    await page.evaluate("document.querySelector('#cnt-svg .u[data-key=\"house:OK-01\"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))")
    await page.wait_for_timeout(500)
    aux = await page.inner_text("#panel")
    if "count only, no model" not in aux or "310" not in aux or "Oklahoma State Election Board" not in aux:
        out.append(f"the official-count race OK-01 shows no totals, precincts, source or tag: {aux[:200]!r}")
    if "OK-01" in await page.inner_text("#proj-list"):
        out.append("a count-only race appears in the projections banner")
    # replay slider
    await page.evaluate("() => { const r = document.querySelector('#t-range'); r.value = '0'; r.dispatchEvent(new Event('input', { bubbles: true })); }")
    await page.wait_for_timeout(400)
    if not (await page.inner_text("#t-label")).startswith("As of"):
        out.append("the time slider did not enter replay")
    await page.click("#t-live")
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
        if case.kind == "count":
            await install_count_routes(page)
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
                result.failures.extend(await check_brief(page, viewport_name))
                result.failures.extend(await check_earlyvote(page, viewport_name))
                result.failures.extend(await check_site_nav(page))
            elif case.kind == "turnout":
                result.failures.extend(await check_brief(page, viewport_name))
                result.failures.extend(await check_turnout(page, viewport_name))
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
            elif case.kind == "count":
                result.failures.extend(await check_count(page, viewport_name))
                result.failures.extend(d for d in result.diagnostics if d.startswith(("console.error", "HTTP", "request failed")))
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
                if case.name == "us2026-ca-gov":
                    result.failures.extend(await check_evr_card(page, viewport_name))
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
            if screenshots and case.name == "turnout":
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=screenshots / f"{engine}-turnout-{viewport_name}.png", full_page=True)
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
