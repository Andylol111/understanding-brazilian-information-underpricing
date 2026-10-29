"""Map checks: the homepage world map, its country views and every election page map.

For each map, level, mode and frame (first and final) the page's own check hooks report what was
painted (window.__world.info() on the homepage, window.__dashAudit on the election page):

  a. fills: every region/unit a data colour or the explicit no-data hatch (no plain default ground);
  b. winners: at the final frame of a finished election, each region's fill shows the side ahead in
     its counted votes; the homepage's Canada view against the certified seats by province;
  c. cards: hover cards carry names (no raw codes, "undefined", "NaN");
  d. outlines: no stray dark strokes (near-black pixels) on the settled map;
  e. legend: present, no "undefined"/"NaN", and a key for the hatch wherever it is drawn;
  f. back: into a region and back to the country (Esc with a unit pinned, the breadcrumb, a click
     outside) returns to the initial whole-country fit, with region= gone from the URL.

Used by analysis/site_check.py (every run) and on its own:
    uv run --no-project --with playwright --with pillow python analysis/map_audit.py --base http://127.0.0.1:8813/ [--engines chromium,firefox] [--shots /tmp/map_audit]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
COUNTRIES = ("US", "BR", "CA", "MX", "HU", "IL", "NZ")
BAD_TEXT = re.compile(r"\bundefined\b|\bNaN\b|\[object")
RAW_CODE = re.compile(r"^\s*([a-z]{2,3}|[a-z]{2}-[a-z0-9]{2,3}-\S+)\s*$")
GENERIC_LEAD = re.compile(r":\s*(Another party|Not \w+|Other margin|No)\s+[\d.]+\s*$")
COLOUR = re.compile(r"^(#[0-9a-f]{6}|rgb\(\d+, ?\d+, ?\d+\))$", re.I)
DARK_SHARE = 0.002  # near-black pixels allowed on a settled map (labels, rings)


@dataclass
class Row:
    map: str
    check: str
    ok: bool
    detail: str = ""


def dark_share(png: bytes) -> float:
    """Share of near-black pixels (all channels under 60) in a screenshot; 0 without Pillow (the
    outline check is then skipped: run with --with pillow)."""
    try:
        from PIL import Image
    except ImportError:
        return 0.0
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((600, 600))
    px = im.tobytes()
    n = len(px) // 3
    return sum(1 for i in range(0, len(px), 3) if px[i] < 60 and px[i + 1] < 60 and px[i + 2] < 60) / max(1, n)


def stats(eid: str) -> dict[str, Any]:
    p = DATA / eid / "stats.json"
    return json.loads(p.read_text()) if p.exists() else {}


def elections() -> list[dict[str, Any]]:
    doc = json.loads((DATA / "elections.json").read_text())
    return doc if isinstance(doc, list) else doc["elections"]


async def settle(page: Page, ms: int = 350) -> None:
    await page.wait_for_timeout(ms)


async def audit_home(page: Page, base: str, shots: Path | None, tag: str) -> list[Row]:
    rows: list[Row] = []
    await page.goto(base, wait_until="load")
    await page.wait_for_function("window.__world", timeout=30000)
    await settle(page, 1200)
    box = page.locator(".wm-box")
    await box.scroll_into_view_if_needed()
    png = await box.screenshot()
    if shots:
        (shots / f"{tag}-home-world.png").write_bytes(png)
    d = dark_share(png)
    rows.append(Row("home: world", "d outlines", d < DARK_SHARE, f"dark {d:.4f}"))
    for code in COUNTRIES:
        name = f"home: {code}"
        await page.click(f".wm-list button[data-code={code}]")
        await page.wait_for_function("(() => { const i = window.__world.info(); return i.t === 1 && i.fills.length > 0; })()", timeout=20000)
        await settle(page, 700)
        info = await page.evaluate("window.__world.info()")
        fills = info["fills"]
        bad = [f["id"] for f in fills if f["fill"] != "hatch" and not COLOUR.match(f["fill"])]
        n_col = sum(1 for f in fills if f["fill"] != "hatch")
        rows.append(Row(name, "a fills", not bad and n_col > 0, f"{n_col} coloured, {len(fills) - n_col} no-market hatch" + (f"; bad {bad[:5]}" if bad else "")))
        # b: no region led by a market's "no" side; Canada against the certified seats by province
        generic = [f["id"] for f in fills if GENERIC_LEAD.search(f["card"].splitlines()[0] if f["card"] else "")]
        wrong: list[str] = []
        if code == "CA" and info.get("election") == "ca2025":
            by_prov = (stats("ca2025").get("outcome") or {}).get("seats_by_province") or {}
            for f in fills:
                seats = by_prov.get(f["id"])
                if not seats or f["fill"] == "hatch":
                    continue
                top = max(seats, key=seats.get)
                if top.lower() not in f["card"].lower():
                    wrong.append(f"{f['id']}: card '{f['card'].splitlines()[0]}', certified {top}")
        rows.append(Row(name, "b winners", not generic and not wrong, "; ".join(wrong[:4]) + (f" generic leaders {generic}" if generic else "") or "ok"))
        raw = [f["id"] for f in fills if not f["card"] or RAW_CODE.match(f["card"].splitlines()[0]) or BAD_TEXT.search(f["card"])]
        rows.append(Row(name, "c cards", not raw, f"raw/bad {raw[:6]}" if raw else f"{len(fills)} cards named"))
        png = await box.screenshot()
        if shots:
            (shots / f"{tag}-home-{code}.png").write_bytes(png)
        d = dark_share(png)
        rows.append(Row(name, "d outlines", d < DARK_SHARE, f"dark {d:.4f}"))
        rows.append(Row(name, "e legend", True, "n/a (cards and panel)"))
        await page.click(".wm-chooser .crumb-back")
        await page.wait_for_function("window.__world.info().t === 0", timeout=10000)
        await settle(page, 300)
    return rows


async def audit_election(page: Page, base: str, e: dict[str, Any], shots: Path | None, tag: str) -> list[Row]:
    eid = e["id"]
    rows: list[Row] = []
    await page.goto(f"{base}election.html?id={eid}", wait_until="load")
    try:
        await page.wait_for_function("window.__dashAudit && window.__dashPerf && window.__dashPerf.firstRenderMs != null", timeout=45000)
    except Exception:
        return [Row(f"{eid}", "load", False, "map never painted")]
    await settle(page, 800)
    A = "window.__dashAudit"
    levels = await page.evaluate(f"{A}.levels()") or [None]
    modes = await page.evaluate(f"{A}.modes()")
    lo, hi = await page.evaluate(f"{A}.domain()")
    finished = e.get("status") == "replay"
    fails: dict[str, list[str]] = {"a fills": [], "b winners": [], "c cards": [], "d outlines": [], "e legend": [], "f back": []}
    # f: into a region and back to the country by Esc (with a unit pinned), the breadcrumb, and a
    # click outside: the view must return to the initial whole-country fit and region= leave the URL
    back_paths = 0
    regs = await page.evaluate(f"{A}.pickable()")
    if regs:
        v0 = await page.evaluate(f"{A}.view()")
        unit_in = f"(r) => {A}.units().ids.findIndex((id) => id.split('-')[1] === r)"
        for n, how in enumerate(("escape", "crumb", "outside")):
            reg = regs[(len(regs) // 2 + n) % len(regs)]
            i = await page.evaluate(f"({unit_in})({json.dumps(reg)})")
            await page.evaluate(f"{A}.selectRegion({json.dumps(reg)})")
            if how == "escape" and i >= 0:
                await settle(page, 1200)
                await page.evaluate(f"{A}.clickUnit({i})")  # a unit pinned inside the region
            await settle(page, 1500)
            if not await page.evaluate(f"{A}.region()"):
                fails["f back"].append(f"{how}: region {reg} did not open")
                continue
            if how == "escape":
                await page.keyboard.press("Escape")
            elif how == "crumb":
                await page.click("#crumb-top")
            else:
                await page.evaluate(f"{A}.clickUnit(-1)")
            await settle(page, 1800)
            v = await page.evaluate(f"{A}.view()")
            url = await page.evaluate(f"{A}.url()")
            back_paths += 1
            if abs(v["k"] - v0["k"]) > 0.01 * v0["k"] or abs(v["x"] - v0["x"]) > 2 or abs(v["y"] - v0["y"]) > 2 or "region=" in url:
                fails["f back"].append(f"{how} from {reg}: view k {v['k']:.3f} vs {v0['k']:.3f}, url {url}")
    n_states = compared = K = 0
    thumbs: list[tuple[str, bytes]] = []
    for level in levels:
        if level:
            await page.evaluate(f"{A}.setLevel({json.dumps(level)})")
            await settle(page, 1100)
        for mode in modes:
            await page.evaluate(f"{A}.setMode({json.dumps(mode)})")
            for frame, t in (("first", lo), ("final", hi)):
                await page.evaluate(f"{A}.setTime({t})")
                await settle(page, 300)
                p = await page.evaluate(f"{A}.painted()")
                n_states += 1
                where = f"{level or 'map'}/{mode}/{frame}"
                u = p["units"]
                plain = u["none"] + u["white"]
                region_plain = [r for r, c in p["regions"].items() if c in ("none", "white")] if p["atRegions"] else []
                if plain or region_plain:
                    fails["a fills"].append(f"{where}: {plain} plain units {p['plain'][:3]}" + (f", plain regions {region_plain[:4]}" if region_plain else ""))
                if finished and frame == "final" and mode == "margin":
                    K = max(K, p["K"])
                    done = [s for s in p["sides"].values() if (s["counted"] or 0) >= 0.99]
                    compared += len(done) + p["unitSides"]["checked"]
                    wrong = [f"{r} shows {s['shown']}, ahead {s['ahead']}" for r, s in p["sides"].items()
                             if s["shown"] != s["ahead"] and s["ahead"] != "tie" and (s["counted"] or 0) >= 0.99]
                    wrong += p["unitSides"]["wrong"]
                    if wrong:
                        fails["b winners"].append(f"{where}: {'; '.join(wrong[:4])}")
                legend = await page.evaluate(f"{A}.legend()")
                hatched = u["hatch"] > 0 or any(c == "hatch" for c in p["regions"].values())
                if not legend.strip() or BAD_TEXT.search(legend) or (hatched and not re.search(r"no data|without data|no figure|no \w+ race", legend)):
                    fails["e legend"].append(f"{where}: {legend[:60]!r}" + (" (hatch drawn, no key)" if hatched else ""))
                png = await page.locator("#map").screenshot()
                d = dark_share(png)
                if d >= DARK_SHARE:
                    fails["d outlines"].append(f"{where}: dark {d:.4f}")
                if frame == "final":
                    thumbs.append((where, png))
        # cards: three units and one region at this level
        un = await page.evaluate(f"{A}.units()")
        real = [i for i, x in enumerate(un["ids"]) if x]  # not a base layer
        for i in sorted({real[0], real[len(real) // 2], real[-1]}) if real else []:
            text = await page.evaluate(f"{A}.card({i})")
            first = text.strip().splitlines()[0] if text.strip() else ""
            if not first or RAW_CODE.match(first) or BAD_TEXT.search(text):
                fails["c cards"].append(f"{level or 'map'} unit {i}: {text[:70]!r}")
        if un["regions"]:
            text = await page.evaluate(f"{A}.card(-1, {json.dumps(un['regions'][0])})")
            first = text.strip().splitlines()[0] if text.strip() else ""
            if not first or RAW_CODE.match(first) or BAD_TEXT.search(text):
                fails["c cards"].append(f"{level or 'map'} region {un['regions'][0]}: {text[:70]!r}")
    for check, items in fails.items():
        if check == "b winners" and not finished:
            rows.append(Row(eid, check, True, "n/a (not finished)"))
            continue
        ok_detail = (f"{compared} fully counted regions/units match their counted leader" if check == "b winners"
                     else (f"{back_paths} returns (Esc, breadcrumb, click outside) to the initial fit" if back_paths else "n/a (no regions to open)") if check == "f back"
                     else f"{n_states} states")
        if check == "b winners" and finished and not compared:
            note = "no count frames: the map shows the comparison election" if not K else "the count in our data never reaches 100% in any region or unit (provisional count)"
            rows.append(Row(eid, check, True, f"n/a ({note})"))
            continue
        rows.append(Row(eid, check, not items, "; ".join(items[:3]) + (f" (+{len(items) - 3})" if len(items) > 3 else "") if items else ok_detail))
    if shots and thumbs:
        grid(thumbs, shots / f"{tag}-{eid}.png")
    return rows


def grid(items: list[tuple[str, bytes]], out: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    ims = [(label, Image.open(io.BytesIO(b)).convert("RGB")) for label, b in items]
    w, h = 360, 240
    cols = 4
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 18)), "white")
    draw = ImageDraw.Draw(sheet)
    for n, (label, im) in enumerate(ims):
        im.thumbnail((w, h))
        x, y = (n % cols) * w, (n // cols) * (h + 18)
        sheet.paste(im, (x, y + 18))
        draw.text((x + 4, y + 3), label, fill="black")
    sheet.save(out)


async def run(base: str, engines: list[str], shots: Path | None, only: set[str] | None = None) -> list[tuple[str, Row]]:
    out: list[tuple[str, Row]] = []
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        for engine in engines:
            browser = await getattr(pw, engine).launch()
            page = await browser.new_page(viewport={"width": 1512, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            if not only or "home" in only:
                out += [(engine, r) for r in await audit_home(page, base, shots, engine)]
            for e in elections():
                if only and e["id"] not in only:
                    continue
                out += [(engine, r) for r in await audit_election(page, base, e, shots, engine)]
            if errors:
                out.append((engine, Row("all", "page errors", False, "; ".join(errors[:3]))))
            await browser.close()
    return out


def table(results: list[tuple[str, Row]]) -> str:
    lines = [f"{'engine':9} {'map':16} {'check':11} {'':4} detail"]
    for engine, r in results:
        lines.append(f"{engine:9} {r.map:16} {r.check:11} {'PASS' if r.ok else 'FAIL':4} {r.detail}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--engines", default="chromium")
    ap.add_argument("--shots", default="/tmp/map_audit")
    ap.add_argument("--only", help="comma-separated election ids and/or 'home'")
    a = ap.parse_args()
    res = asyncio.run(run(a.base.rstrip("/") + "/", a.engines.split(","), Path(a.shots) if a.shots else None, set(a.only.split(",")) if a.only else None))
    print(table(res))
    failed = sum(1 for _, r in res if not r.ok)
    print(f"\n{len(res) - failed}/{len(res)} map checks passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
