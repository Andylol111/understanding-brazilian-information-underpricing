// Homepage world map: the way into each election, drawn like the election maps (flat fills, white
// borders, a light wash and an outline on hover) on one canvas. Countries with elections share one
// muted accent. Choosing a country animates the projection itself (~900 ms): the world's Equal
// Earth turns and morphs into the projection the country's own election map uses (US Albers,
// Canada's Lambert conformal conic, an equal-area conic for Brazil and Mexico, a conformal conic
// centred on New Zealand, Mercator elsewhere) while zooming in, every frame redrawn from the
// vectors; the country's own regions fade in, coloured by the leader of its race in the latest
// election (YAPMS bands), with a side panel of its elections and races. Back / Esc reverses the
// animation. A search box finds elections, races and regions. Small screens get the list first.
// Natural Earth 1:50m countries (public domain), geo/world.topo.json and the region outlines
// geo/<cc>_regions.topo.json from analysis/geo_build.py.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { C, esc, fmtDate, getJSON, modelP, num } from "./common.js";
import { bandColours, bandColour, bandOf } from "./dash-agg.js";

const LAND = "#e6e4dd", ACCENT = "#e3b8a0", UNDER = "#f4f2ec", NO_RACE = "#efede7";
const DEM = "#1f5fbf", REP = "#c8102e";
const STATUS_LABEL = { live: "Live", scheduled: "Before the count", replay: "Replay" };
const RANK = { live: 0, scheduled: 1, replay: 2 };
const ZOOM_MS = 900, FADE_MS = 350;
const REGION_MAPS = new Set(["US", "BR", "CA", "MX", "HU", "NZ", "IL"]); // geo/<cc>_regions.topo.json
const RAD = Math.PI / 180;
// a race's short label: long market questions ("Will the National Party win fewer than 25 seats
// in…") read by their first side's name ("National fewer than 25")
const shortName = (r) => r.short ?? (/^most-/.test(r.race_id) && r.a?.name ? `Most seats: ${r.a.name}` : r.name.length <= 40 ? r.name : r.a?.name ?? r.name);
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const fold = (s) => String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
// a chance as a pseudo-margin for the bands where a race has no projected share
const pseudo = (p) => { const a = Math.abs(p - 0.5); return Math.sign(p - 0.5) * (a < 0.1 ? 0.5 : a < 0.25 ? 3 : a < 0.4 ? 10 : 20); };

// The projection a country's election map uses (dash-engine.js projectionFor), as a raw projection
// and rotation (to blend from the world's) and as a full d3 projection (for the settled view).
// New Zealand's transverse Mercator is drawn as a conformal conic centred on it (the same shape
// there; a transverse projection cannot be blended from the world's upright one).
function targetFor(code, f) {
  const [[x0, y0], [x1, y1]] = d3.geoBounds(f);
  const lon = x1 < x0 ? ((x0 + x1 + 360) / 2) % 360 - 180 : (x0 + x1) / 2; // across the antimeridian
  if (code === "US") return { raw: d3.geoConicEqualAreaRaw(29.5 * RAD, 45.5 * RAD), rotate: [96, 0], make: () => d3.geoConicEqualArea().parallels([29.5, 45.5]).rotate([96, 0]) };
  if (code === "CA") return { raw: d3.geoConicConformalRaw(49 * RAD, 77 * RAD), rotate: [96, 0], make: () => d3.geoConicConformal().parallels([49, 77]).rotate([96, 0]) };
  if (code === "NZ") return { raw: d3.geoConicConformalRaw(-37 * RAD, -45 * RAD), rotate: [-173, 0], make: () => d3.geoConicConformal().parallels([-37, -45]).rotate([-173, 0]) };
  if (code === "BR" || code === "MX") {
    const pad = (y1 - y0) / 6;
    const p0 = y0 + pad, p1 = y1 - pad;
    return { raw: d3.geoConicEqualAreaRaw(p0 * RAD, p1 * RAD), rotate: [-lon, 0], make: () => d3.geoConicEqualArea().parallels([p0, p1]).rotate([-lon, 0]) };
  }
  // Mercator, its latitude clamped to ±85° (the poles would project to infinity)
  const merc = (λ, φ) => d3.geoMercatorRaw(λ, Math.max(-1.484, Math.min(1.484, φ)));
  return { raw: merc, rotate: [-lon, 0], make: () => d3.geoMercator().rotate([-lon, 0]) };
}
const blendRaw = (a, b, u) => (λ, φ) => { const p = a(λ, φ), q = b(λ, φ); return [p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u]; };


// root: the section to fill; elections: elections.json entries; href(id): the dashboard link;
// countryName(code): display name; dataRoot(id): where an election's files live.
// frames (optional): array receiving rAF intervals during the animations.
export function createWorldMap(root, { topo, elections, href, countryName, dataRoot = () => "data/", frames = null }) {
  const byCountry = d3.group(elections.filter((e) => e.country), (e) => e.country.toUpperCase());
  const features = topojson.feature(topo, topo.objects.countries).features;
  const borders = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
  const sorted = (code) => [...(byCountry.get(code) ?? [])].sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) || b.date.localeCompare(a.date));
  const featureOf = (code) => features.find((x) => x.properties.iso2 === code);
  const active = features.filter((f) => byCountry.has(f.properties.iso2));
  const bounds = new Map(features.map((f) => [f, d3.geoBounds(f)]));
  const sphere = { type: "Sphere" };

  root.innerHTML = `
    <div class="wm-search"><label class="visually-hidden" for="wm-q">Search elections, races and regions</label>
      <input id="wm-q" type="search" autocomplete="off" placeholder="Search: Texas Senate, São Paulo, Hungary…" role="combobox" aria-expanded="false" aria-controls="wm-hits">
      <ul id="wm-hits" class="wm-hits" role="listbox" hidden></ul></div>
    <ul class="wm-list" aria-label="Elections by country">${[...byCountry].sort((a, b) => countryName(a[0]).localeCompare(countryName(b[0]))).map(([code, list]) =>
      `<li><button type="button" data-code="${esc(code)}">${esc(countryName(code))}${list.length > 1 ? ` <span>${list.length}</span>` : ""}</button></li>`).join("")}</ul>
    <div class="wm-stage">
      <div class="wm-box">
        <canvas class="wm-canvas" role="img" aria-label="World map: countries with elections are highlighted; use the list above to choose one"></canvas>
        <div class="place-card wm-card" hidden></div>
      </div>
      <div class="wm-chooser" hidden></div>
    </div>`;
  const box = root.querySelector(".wm-box"), canvas = root.querySelector("canvas"), ctx = canvas.getContext("2d");
  const card = root.querySelector(".wm-card"), chooser = root.querySelector(".wm-chooser");
  // t: 0 the world, 1 the chosen country in its own projection; regionMix: its regions' opacity
  const st = { w: 0, h: 0, dpr: 1, t: 0, code: null, target: null, proj: null, anim: 0, token: 0, hover: null, regions: null, data: null, regionMix: 0, fadeT0: null };

  /* ------------------------------------------------ the projection at any point of the animation */

  // the area the country fits in: left of the side panel (over the map from 700 px wide), within
  // the part of the box on screen (st.vis, taken when the zoom starts), padded
  function countryBox() {
    const panel = st.w > 700 ? Math.min(380, st.w * 0.45) : 0;
    const [top, bottom] = st.vis ?? [0, st.h];
    const pad = Math.min(st.w, bottom - top) * 0.07;
    return [[pad, top + pad], [st.w - panel - pad, bottom - pad]];
  }
  // the rows of the box inside the window (24 px clear of its bottom edge); when under 240 px of
  // it shows, the page first scrolls the box to the top of the window
  function onScreen() {
    let r = box.getBoundingClientRect();
    const vis = (top) => [Math.max(0, -top), Math.min(r.height, innerHeight - 24 - top)];
    let [a, b] = vis(r.top);
    if (b - a < 240) {
      window.scrollBy({ top: r.top - 12 });
      r = box.getBoundingClientRect();
      [a, b] = vis(r.top);
    }
    st.vis = [a, b];
  }
  // the country's extent without far-flung parts (Alaska, Hawaii): polygons at least a quarter of the largest
  function mainland(f) {
    if (f.geometry.type !== "MultiPolygon") return f;
    const parts = f.geometry.coordinates.map((c) => [c, d3.geoArea({ type: "Polygon", coordinates: c })]);
    const big = d3.max(parts, (p) => p[1]);
    return { type: "MultiPolygon", coordinates: parts.filter((p) => p[1] >= 0.25 * big).map((p) => p[0]) };
  }
  // t in [0, 1]: the projection morphs from the world's to the country's (raw blend and rotation,
  // eased) while the scale moves geometrically from the world fit to the country fit and the
  // country's centre slides from its world spot to the centre of its box. The world fit is the
  // Equal Earth one throughout: a conic's whole sphere is unbounded (a pole at infinity), so a
  // sphere fit of the blend collapses the world to a dot.
  function projAt(t) {
    const all = [[6, 6], [st.w - 6, st.h - 6]];
    if (!st.target || t <= 0) return d3.geoEqualEarth().fitExtent(all, sphere);
    const g = st.target.g;
    // clipped to the view: the far side of the world projects to huge coordinates in a conic,
    // which are costly to fill
    const view = [[-20, -20], [st.w + 20, st.h + 20]];
    if (t >= 1) return st.target.make().fitExtent(countryBox(), g).clipExtent(view);
    const u = d3.easeCubicInOut(t);
    const rot = st.target.rotate.map((r) => r * u);
    // no adaptive resampling while it moves (the 1:50m outlines are dense enough): ~3× cheaper
    const p = d3.geoProjection(blendRaw(d3.geoEqualEarthRaw, st.target.raw, u)).rotate(rot).precision(0);
    const w0 = d3.geoEqualEarth().fitExtent(all, sphere);
    const cGeo = st.target.c, sw = w0.scale(), atW = w0(cGeo);
    p.fitExtent(countryBox(), g);
    const sc = p.scale(), atC = p(cGeo);
    const s = sw * Math.pow(sc / sw, u);
    // the country's centre on the straight path from its world spot to its box spot
    const want = [atW[0] + (atC[0] - atW[0]) * u, atW[1] + (atC[1] - atW[1]) * u];
    const at = p.scale(s).translate([0, 0])(cGeo);
    p.translate([want[0] - at[0], want[1] - at[1]]).clipExtent(view);
    // how much of the view the country spans (the outline detail follows it)
    const xs = (st.target.s ??= sampleOf(g)).map((s0) => p(s0)?.[0]).filter(Number.isFinite);
    p.zoom = xs.length ? (d3.max(xs) - d3.min(xs)) / st.w : 1;
    return p;
  }
  // skip a country whose bounding box (8 sample points) projects entirely to one side of the view:
  // zoomed in, most of the world is off screen and need not be projected and clipped every frame
  const sampleOf = (f) => {
    const [[x0, y0], [x1, y1]] = d3.geoBounds(f);
    const xe = x1 < x0 ? x1 + 360 : x1, xm = (x0 + xe) / 2, ym = (y0 + y1) / 2;
    return [[x0, y0], [x0, y1], [xe, y0], [xe, y1], [xm, y0], [xm, y1], [x0, ym], [xe, ym], [xm, ym]];
  };
  const samples = new Map(features.map((f) => [f, sampleOf(f)]));
  // the 1:110m outlines (same countries, fewer points) for frames with most of the world in view
  const lo = { features: null, borders: null };
  getJSON("geo/world_110.topo.json", { optional: true, root: "data/" }).then((t) => {
    if (!t) return;
    lo.features = topojson.feature(t, t.objects.countries).features;
    lo.borders = topojson.mesh(t, t.objects.countries, (a, b) => a !== b);
    for (const f of lo.features) samples.set(f, sampleOf(f));
    draw();
  }).catch(() => {});
  function offscreen(p, f) {
    if (st.t <= 0) return false;
    let l = 0, r = 0, u = 0, d = 0;
    const pts = samples.get(f);
    for (const s of pts) {
      const xy = p(s);
      if (!xy) return false;
      if (xy[0] < -40) l++; else if (xy[0] > st.w + 40) r++;
      if (xy[1] < -40) u++; else if (xy[1] > st.h + 40) d++;
    }
    const n = pts.length;
    return l === n || r === n || u === n || d === n;
  }

  /* ------------------------------------------------ drawing */

  function draw() {
    const p = st.proj;
    if (!p || !st.w) return;
    ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
    ctx.clearRect(0, 0, st.w, st.h);
    const path = d3.geoPath(p, ctx);
    const chosen = st.code, dim = chosen ? Math.min(1, st.t * 2) : 0;
    const under = st.regions && st.regionMix > 0;
    // the world's outlines: 1:110m while most of the world is in view (fewer points), 1:50m closer
    const useLo = lo.features && (st.t <= 0 || (p.zoom ?? 1) < 0.3);
    const set = useLo ? lo.features : features, mesh = useLo ? lo.borders : borders;
    // fills grouped by colour (one fill per colour)
    const groups = new Map();
    for (const f of set) {
      if (offscreen(p, f)) continue;
      const code = f.properties.iso2;
      const fill = code === chosen ? (under ? UNDER : C.model) : byCountry.has(code) ? ACCENT : LAND;
      const alpha = code === chosen ? 1 : 1 - 0.55 * dim;
      const key = `${fill}|${alpha}`;
      if (!groups.has(key)) groups.set(key, { fill, alpha, list: [] });
      groups.get(key).list.push(f);
    }
    for (const { fill, alpha, list } of groups.values()) {
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      for (const f of list) path(f);
      ctx.fillStyle = fill;
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    // borders: the shared mesh while most of the world is in view; zoomed in, the outlines of the
    // few countries drawn (the mesh would be projected whole every frame)
    ctx.beginPath();
    const shown = d3.sum(groups.values(), (g) => g.list.length);
    if (shown > 60) path(mesh); else for (const g of groups.values()) for (const f of g.list) path(f);
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 0.8; ctx.stroke();
    if (under) {
      // the country's own regions, coloured by their races
      ctx.globalAlpha = st.regionMix;
      const rg = new Map();
      for (const f of st.regions.features) {
        const c = st.data?.colours.get(f.id) ?? NO_RACE;
        if (!rg.has(c)) rg.set(c, []);
        rg.get(c).push(f);
      }
      for (const [c, list] of rg) { ctx.beginPath(); for (const f of list) path(f); ctx.fillStyle = c; ctx.fill(); }
      ctx.beginPath(); path(st.regions.mesh);
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // hover: a light wash and a dark outline, as on the election maps
    if (st.hover && !st.anim) {
      ctx.beginPath(); path(st.hover);
      ctx.fillStyle = "rgba(255,255,255,0.25)"; ctx.fill();
      ctx.strokeStyle = "#1b1b1b"; ctx.lineWidth = 1.4; ctx.stroke();
    }
    // election counts on the world view (small countries stay findable)
    if (!chosen && st.t === 0) {
      ctx.font = "600 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const f of active) {
        const n = byCountry.get(f.properties.iso2).length;
        const c = p(d3.geoCentroid(mainland(f)));
        if (!c) continue;
        if (n > 1) { ctx.fillStyle = "#fff"; ctx.fillText(String(n), c[0], c[1]); }
        else if (path.area(f) < 30) { ctx.beginPath(); ctx.arc(c[0], c[1], 4, 0, 2 * Math.PI); ctx.fillStyle = ACCENT; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke(); }
      }
    }
  }

  function layout() {
    const r = box.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || (r.width === st.w && r.height === st.h)) return;
    st.w = r.width; st.h = r.height; st.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(st.w * st.dpr); canvas.height = Math.round(st.h * st.dpr);
    canvas.style.width = `${st.w}px`; canvas.style.height = `${st.h}px`;
    st.proj = projAt(st.t);
    draw();
  }

  // run t to `to` over ZOOM_MS (cubic in-out inside projAt), redrawing every frame
  function animate(to, done) {
    cancelAnimationFrame(st.anim);
    const from = st.t;
    if (reducedMotion() || document.hidden || !st.w) { st.t = to; st.proj = projAt(to); st.anim = 0; draw(); done?.(); return; }
    let t0 = null, last = 0, settle = 1; // the first frame carries the style changes made on the click
    const step = (now) => {
      if (settle-- > 0) { st.anim = requestAnimationFrame(step); return; }
      if (t0 == null) { t0 = now; last = now; } else frames?.push(now - last);
      last = now;
      const lin = Math.min(1, (now - t0) / ZOOM_MS);
      st.t = from + (to - from) * lin;
      fadeStep(now);
      st.proj = projAt(st.t);
      draw();
      if (lin < 1) { st.anim = requestAnimationFrame(step); return; }
      st.anim = 0;
      st.proj = projAt(st.t); // the settled projection (with invert, for the pointer)
      draw();
      done?.();
    };
    st.anim = requestAnimationFrame(step);
  }
  // the regions fade in once loaded (during the zoom where possible)
  function fadeStep(now) {
    if (!st.regions || st.regionMix >= 1) return;
    st.fadeT0 ??= now;
    st.regionMix = Math.min(1, (now - st.fadeT0) / FADE_MS);
  }
  function fadeRegions() {
    if (st.anim) return; // the zoom's frames carry the fade
    const step = (now) => { fadeStep(now); draw(); if (st.regionMix < 1 && !st.anim) requestAnimationFrame(step); };
    if (reducedMotion()) { st.regionMix = 1; draw(); } else requestAnimationFrame(step);
  }

  /* ------------------------------------------------ pointer */

  const pointIn = (ev) => { const b = canvas.getBoundingClientRect(); return [ev.clientX - b.left, ev.clientY - b.top]; };
  const inBounds = (f, ll) => { const [[x0, y0], [x1, y1]] = bounds.get(f); return ll[1] >= y0 && ll[1] <= y1 && (x0 <= x1 ? ll[0] >= x0 && ll[0] <= x1 : ll[0] >= x0 || ll[0] <= x1); };
  // what is under the pointer (settled views only): a region of the chosen country, else a country
  function hit(ev) {
    if (st.anim || !st.proj?.invert) return null;
    const xy = pointIn(ev), ll = st.proj.invert(xy);
    if (!ll || !Number.isFinite(ll[0])) return null;
    if (st.code && st.regions) {
      const r = st.regions.features.find((f) => d3.geoContains(f, ll));
      if (r) return { kind: "region", f: r };
    }
    const c = features.find((f) => byCountry.has(f.properties.iso2) && inBounds(f, ll) && d3.geoContains(f, ll));
    if (c) return { kind: "country", f: c };
    // small countries: near their marker
    if (!st.code) for (const f of active) { const m = st.proj(d3.geoCentroid(mainland(f))); if (m && Math.hypot(m[0] - xy[0], m[1] - xy[1]) < 10) return { kind: "country", f }; }
    return null;
  }
  canvas.addEventListener("pointermove", (ev) => {
    const h = hit(ev);
    const f = h && (h.kind === "region" || h.f.properties.iso2 !== st.code) ? h.f : null;
    canvas.style.cursor = f ? "pointer" : "default";
    if (f !== st.hover) { st.hover = f; draw(); }
    if (!f) { card.hidden = true; return; }
    if (h.kind === "region") showCard(`<p class="pc-name"><b>${st.data?.regionInfo.get(f.id) ?? esc(f.id)}</b></p>${st.data?.regionLink.has(f.id) ? `<p class="pc-hint">Click to open its map.</p>` : ""}`, ev);
    else showCard(cardHTML(f.properties.iso2), ev);
  });
  canvas.addEventListener("pointerleave", () => { if (st.hover) { st.hover = null; draw(); } card.hidden = true; });
  canvas.addEventListener("click", (ev) => {
    const h = hit(ev);
    if (!h) return;
    if (h.kind === "region") leave(st.data?.regionLink.get(h.f.id));
    else if (h.f.properties.iso2 !== st.code) choose(h.f.properties.iso2);
  });
  root.querySelectorAll(".wm-list button").forEach((b) => b.addEventListener("click", () => choose(b.dataset.code)));
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && st.code) back(); });

  function cardHTML(code) {
    return `<p class="pc-name"><b>${esc(countryName(code))}</b></p>
      <ul class="wm-elections">${sorted(code).map((e) => `<li><b>${esc(e.name)}</b> <span class="wm-meta">${esc(fmtDate(e.date))} · ${esc(STATUS_LABEL[e.status] ?? e.status)}</span>${e.summary ? `<br><span class="wm-sum">${esc(e.summary)}</span>` : ""}</li>`).join("")}</ul>`;
  }
  function showCard(html, ev) {
    card.innerHTML = html;
    card.hidden = false;
    const b = box.getBoundingClientRect();
    const x = ev.clientX - b.left, y = ev.clientY - b.top;
    card.style.left = `${Math.max(8, Math.min(b.width - card.offsetWidth - 8, x + 14))}px`;
    card.style.top = `${Math.max(8, Math.min(b.height - card.offsetHeight - 8, y + 14))}px`;
  }

  /* ------------------------------------------------ a country: its regions and races */

  // The country's main election for the map: the first (live, scheduled, then newest) with a map by
  // region (an overview or two or more regions), e.g. Canada's federal election rather than
  // Quebec's; its races (name, leader, chance, link) and a colour per region. Race links carry the
  // race only (the election page opens on it); region clicks on the map carry the region.
  async function countryData(code) {
    let e = sorted(code)[0], meta = null;
    for (const x of sorted(code)) {
      const m = await getJSON(`${x.id}/meta.json`, { optional: true, root: dataRoot(x.id) }).catch(() => null);
      if (m && (m.overview || (m.regions?.length ?? 0) >= 2)) { e = x; meta = m; break; }
      if (x === e) meta = m;
    }
    const opt = { optional: true, root: dataRoot(e.id) };
    const link = (q) => `${href(e.id)}${q}`;
    const races = [], colours = new Map(), regionLink = new Map(), regionInfo = new Map();
    if (meta?.overview) {
      // US: every race of the overview (Senate first), coloured by the model's margin (with polls),
      // else the polls, else the market-implied margin
      const ov = await getJSON(`${e.id}/${meta.overview}`, opt);
      for (const [o, off] of Object.entries(ov?.offices ?? {})) for (const r of off.races ?? []) {
        const mg = r.margins ?? {}, p = r.pm?.price ?? r.kalshi?.price ?? null;
        const m = mg.model_polls ?? mg.model_v2 ?? mg.model ?? mg.estimate ?? mg.polls ?? mg.market ?? null;
        const name = `${r.state_name} ${o === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`;
        const lead = p == null ? null : p >= 0.5 ? r.dem_side?.name ?? "Democratic side" : r.candidates?.R ?? "Republican side";
        const q = `&race=${encodeURIComponent(r.race_id ?? r.race_key)}&region=${r.state}&level=units`;
        races.push({ name, office: o, lead, chance: p == null ? null : Math.max(p, 1 - p), href: link(`&race=${encodeURIComponent(r.race_id ?? r.race_key)}`), region: r.state });
        if (o === "senate" || !colours.has(r.state)) {
          if (m != null) colours.set(r.state, bandColour(m, bandColours(DEM), bandColours(REP)));
          regionLink.set(r.state, r.units ? link(q) : link(""));
          regionInfo.set(r.state, `${esc(name)}: ${lead ? `${esc(lead)} ${num(Math.max(p, 1 - p))}` : "no price"}`);
        }
      }
    } else if (meta) {
      // other countries: each region's race (the main race's office first), at its last row: the
      // tail file where there is one (small), else the whole timeline
      const tl = (await getJSON(`${e.id}/timeline_tail.json`, opt)) ?? (await getJSON(`${e.id}/timeline.json`, opt));
      const last = (id) => tl?.[id]?.at?.(-1) ?? null;
      for (const r of meta.races ?? []) {
        const row = last(r.race_id), P = modelP(row) ?? row?.pm ?? row?.kalshi ?? null;
        const lead = P == null ? null : P >= 0.5 ? r.a.name : r.b.name;
        const q = `&race=${encodeURIComponent(r.race_id)}${r.region ? `&region=${r.region}&level=units` : ""}`;
        races.push({ name: shortName(r), full: r.name, lead, chance: P == null ? null : Math.max(P, 1 - P), href: link(`&race=${encodeURIComponent(r.race_id)}`), region: r.region });
        if (r.region && !colours.has(r.region) && P != null) {
          const pt = row?.share != null ? (2 * row.share - 1) * 100 : pseudo(P);
          colours.set(r.region, bandColour(pt, bandColours(r.a.color), bandColours(r.b.color)));
          regionLink.set(r.region, link(q));
          regionInfo.set(r.region, `${esc(r.name)}: ${esc(lead)} ${num(Math.max(P, 1 - P))}`);
        }
      }
      // no regional races (New Zealand, Israel): colour each unit of the map by the comparison
      // election, by the party that won it (with its margin) or the side ahead
      if (!colours.size) {
        const U = await getJSON(`${e.id}/units.json`, opt);
        (U?.unit_ids ?? []).forEach((id, j) => {
          const nm = U.names?.[j] ?? id;
          if (U.winner && meta.parties) {
            const p = meta.parties.find((x) => x.id === U.winner[j]);
            if (!p) return;
            const pt = (U.margin?.[j] ?? 0) / 10; // tenths of a point
            colours.set(id, bandColours(p.color)[bandOf(pt || 1)]);
            regionInfo.set(id, `${esc(nm)}: won by ${esc(p.name)} last time (by ${pt.toFixed(1)} pt)`);
          } else if (U.a && U.b && U.prior?.[j] >= 0) {
            const pt = (2 * U.prior[j] / 1000 - 1) * 100;
            colours.set(id, bandColour(pt, bandColours(U.a.color), bandColours(U.b.color)));
            regionInfo.set(id, `${esc(nm)}: ${esc(pt >= 0 ? U.a.name : U.b.name)} ahead by ${Math.abs(pt).toFixed(1)} pt last time`);
          } else return;
          regionLink.set(id, link(""));
        });
      }
    }
    return { e, meta, races, colours, regionLink, regionInfo };
  }

  function choose(code) {
    if (!byCountry.has(code)) return;
    card.hidden = true;
    const token = ++st.token;
    // from another country: back to the world first, then on
    if (st.code && st.code !== code) { back(() => { if (st.token === token + 1) choose(code); }); return; }
    st.code = code;
    st.target = targetFor(code, featureOf(code));
    st.target.g = mainland(featureOf(code)); st.target.c = d3.geoCentroid(st.target.g);
    onScreen();
    st.hover = null;
    st.regions = null; st.data = null; st.regionMix = 0; st.fadeT0 = null;
    root.classList.add("wm-zoomed");
    chooser.hidden = true;
    // the country's figures and outlines load during the zoom; its regions fade in when here
    const data = countryData(code).catch(() => null);
    const shapes = REGION_MAPS.has(code) ? getJSON(`geo/${code.toLowerCase()}_regions.topo.json`, { optional: true }).catch(() => null) : Promise.resolve(null);
    Promise.all([data, shapes]).then(([d, t]) => {
      if (st.token !== token || !t || !d) return;
      st.data = d;
      st.regions = { features: topojson.feature(t, t.objects.regions).features, mesh: topojson.mesh(t, t.objects.regions, (a, b) => a !== b) };
      fadeRegions();
    });
    animate(1, async () => {
      const d = await data;
      if (st.token !== token) return;
      panel(code, d);
    });
  }

  // the side panel: the country's elections; the latest one's races (chance, leader), linked
  function panel(code, d) {
    const list = sorted(code);
    const races = d?.races ?? [];
    const officeName = (o) => (o === "governor" ? "Governor" : o === "senate" ? "Senate" : "");
    const groups = d3.groups(races, (r) => r.office ?? "");
    chooser.innerHTML = `<p class="wm-ch-head"><b>${esc(countryName(code))}</b> <button type="button" class="crumb-back">‹ All countries</button></p>
      <ul class="wm-el">${list.map((e, i) => `<li><a href="${href(e.id)}"><b>${esc(e.name)}</b><span class="wm-meta">${esc(fmtDate(e.date))} · ${esc(STATUS_LABEL[e.status] ?? e.status)}${i === 0 && races.length ? " · races below" : ""}</span></a></li>`).join("")}</ul>
      ${races.length ? groups.map(([o, rs]) => `<p class="wm-ch-sub">${esc(officeName(o) || d.e.name)}: chance the leader wins</p>
        <ul class="wm-races">${rs.map((r) => `<li><a href="${r.href}" title="${esc(r.full ?? r.name)}"><span class="wm-rn">${esc(r.name)}</span><span class="wm-lead">${r.lead ? `${esc(r.lead)} <b>${num(r.chance)}</b>` : "<small>no price</small>"}</span></a></li>`).join("")}</ul>`).join("") : ""}`;
    chooser.hidden = false;
    chooser.querySelector(".crumb-back").addEventListener("click", () => back());
    chooser.querySelectorAll("a").forEach((a) => a.addEventListener("click", (ev) => { if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); leave(a.href); } }));
    chooser.querySelector("a")?.focus({ preventScroll: true });
  }

  // the reverse animation: the regions fade with the zoom, the projection morphs back to the world's
  function back(done) {
    if (!st.code) { done?.(); return; }
    st.token++;
    chooser.hidden = true;
    card.hidden = true;
    st.hover = null;
    root.classList.remove("wm-zoomed");
    st.regions = null; st.data = null; st.regionMix = 0;
    animate(0, () => { st.code = null; st.target = null; st.proj = projAt(0); draw(); done?.(); });
  }
  // a short fade before the dashboard loads (its map opens framed on the same country)
  function leave(url) {
    if (!url) return;
    if (reducedMotion()) { location.href = url; return; }
    document.body.classList.add("leaving");
    setTimeout(() => { location.href = url; }, 220);
  }
  window.addEventListener("pageshow", (ev) => { if (ev.persisted) { document.body.classList.remove("leaving"); back(); } });
  // back from an election (index.html?from=<id>): start framed on its country, then morph out
  function arrive(code) {
    if (!byCountry.has(code) || !st.w) return;
    st.code = code;
    st.target = targetFor(code, featureOf(code));
    st.target.g = mainland(featureOf(code)); st.target.c = d3.geoCentroid(st.target.g);
    onScreen();
    st.t = 1;
    st.proj = projAt(1);
    draw();
    requestAnimationFrame(() => requestAnimationFrame(() => back()));
  }

  /* ------------------------------------------------ search */

  // index: elections, countries, every election's regions and races, the US overview's races;
  // loaded on first use. Fuzzy: each query word must start a word of the entry (accents ignored).
  const q = root.querySelector("#wm-q"), hits = root.querySelector("#wm-hits");
  let index = null, results = [], cur = -1;
  async function buildIndex() {
    const out = [];
    for (const [code] of byCountry) out.push({ text: countryName(code), kind: "Country", go: () => { q.blur(); hits.hidden = true; choose(code); } });
    await Promise.all(elections.map(async (e) => {
      out.push({ text: e.name, kind: `Election · ${fmtDate(e.date)}`, url: href(e.id) });
      const meta = await getJSON(`${e.id}/meta.json`, { optional: true, root: dataRoot(e.id) }).catch(() => null);
      if (!meta) return;
      for (const r of meta.regions ?? []) out.push({ text: r.name, kind: `Region · ${e.name}`, url: `${href(e.id)}&region=${r.id}&level=units` });
      for (const r of meta.races ?? []) out.push({ text: r.name, kind: `Race · ${e.name}`, url: `${href(e.id)}&race=${encodeURIComponent(r.race_id)}` });
      if (meta.overview) {
        const ov = await getJSON(`${e.id}/${meta.overview}`, { optional: true, root: dataRoot(e.id) }).catch(() => null);
        for (const [o, off] of Object.entries(ov?.offices ?? {})) for (const r of off.races ?? []) {
          out.push({ text: `${r.state_name} ${o === "governor" ? "governor" : "Senate"}${r.special ? " special" : ""}`, kind: `Race · ${e.name}`, url: `${href(e.id)}&race=${encodeURIComponent(r.race_id ?? r.race_key)}` });
        }
      }
    }));
    // one entry per text and destination
    const seen = new Set();
    return out.filter((x) => { const k = `${x.text}|${x.url ?? x.kind}`; if (seen.has(k)) return false; seen.add(k); return true; }).map((x) => ({ ...x, words: fold(x.text).split(/[^a-z0-9]+/).filter(Boolean) }));
  }
  const score = (x, qw, qs) => {
    if (!qw.every((w) => x.words.some((v) => v.startsWith(w)))) return fold(x.text).includes(qs) ? 1 : 0;
    return 2 + (x.words[0]?.startsWith(qw[0]) ? 1 : 0) + (x.kind.startsWith("Race") ? 0.5 : 0) - x.words.length * 0.01;
  };
  function search() {
    const qs = fold(q.value.trim());
    if (!qs || !index) { hits.hidden = true; q.setAttribute("aria-expanded", "false"); results = []; return; }
    const qw = qs.split(/[^a-z0-9]+/).filter(Boolean);
    results = index.map((x) => [x, score(x, qw, qs)]).filter(([, s]) => s > 0).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([x]) => x);
    cur = results.length ? 0 : -1;
    hits.innerHTML = results.length ? results.map((x, i) => `<li role="option" id="wm-hit-${i}" aria-selected="${i === cur}"><b>${esc(x.text)}</b> <span class="wm-meta">${esc(x.kind)}</span></li>`).join("") : `<li class="wm-none">No match</li>`;
    hits.hidden = false;
    q.setAttribute("aria-expanded", "true");
    hits.querySelectorAll("[role=option]").forEach((li, i) => li.addEventListener("mousedown", (ev) => { ev.preventDefault(); open(results[i]); }));
  }
  const open = (x) => { if (!x) return; if (x.go) x.go(); else leave(x.url); };
  q.addEventListener("focus", async () => { index ??= await buildIndex(); search(); });
  q.addEventListener("input", async () => { index ??= await buildIndex(); search(); });
  q.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!results.length) return;
      cur = (cur + (ev.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
      hits.querySelectorAll("[role=option]").forEach((li, i) => li.setAttribute("aria-selected", String(i === cur)));
      q.setAttribute("aria-activedescendant", `wm-hit-${cur}`);
    } else if (ev.key === "Enter") { ev.preventDefault(); open(results[cur >= 0 ? cur : 0]); }
    else if (ev.key === "Escape") { hits.hidden = true; ev.stopPropagation(); }
  });
  q.addEventListener("blur", () => setTimeout(() => { hits.hidden = true; q.setAttribute("aria-expanded", "false"); }, 150));

  new ResizeObserver(layout).observe(box);
  layout();
  // state for checks (frame and screenshot scripts)
  const info = () => ({ t: st.t, code: st.code, regions: st.regions?.features.length ?? 0, coloured: st.regions ? st.regions.features.filter((f) => st.data?.colours.has(f.id)).length : 0, bounds: st.target && st.proj ? d3.geoPath(st.proj).bounds(st.target.g) : null });
  return { choose, back, arrive, info };
}
