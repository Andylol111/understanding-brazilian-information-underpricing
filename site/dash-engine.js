// Map engine for the election dashboard, run inside dash-worker.js (normally) or on the page's
// main thread (fallback when a worker or OffscreenCanvas is unavailable or fails):
//  - geo(): fetch and parse the geometry, project it into SVG path strings (returned for
//    hit-testing) and keep Path2D objects here for drawing;
//  - units(): fetch and parse map frames, turn them into typed arrays and per-mode values
//    (normalised once, so colours are a table lookup);
//  - control(): canvas, size, view, interaction state and paint requests; every change is painted
//    right after the current task (a microtask: no timers, no requestAnimationFrame, which hidden
//    and headless pages throttle or never run), so the first frame appears once data is bound.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { unitsByRegion, regionAgg, regionWinner, bandColours, bandColour, tieBreak, TIE } from "./dash-agg.js";

const BASE = 1000; // base projection extent, shared with dash-map.js
const SCALE = { swing: 0.1, margin: 0.4, prior: 0.4, turnout: 0.2 }; // value at which a diverging colour is full
// turnout vs expected: brown below, teal above (not the parties' red and blue)
const TURN_LUT = d3.range(201).map((j) => d3.interpolateBrBG(j / 200));
const NONE = "#efede7", NEUTRAL = "#f4f2ec";
const XFADE_MS = 300; // a change of map colouring (mode) cross-fades over this long
const PAPER = "#fcfbf8", VEIL = 0.6; // regions outside the open one recede this far toward the paper
const WIN_FULL = 0.3; // margin (share points / 100) at which a party's colour is full
// leader colour of unit j for a margin in points (side a minus side b). A projection that reads
// exactly even (stored in thousandths) takes the side of the unit's finer figures (tieBreak) when
// `fine` (projection modes, frame R.curK); grey only for an exact tie.
const band = (j, pt, fine = false) => {
  const b = R.unitBand?.[j];
  if (!b) return NONE;
  let v = Math.abs(pt) < 1e-9 ? 0 : pt;
  if (v === 0 && fine && R.units) v = tieBreak(R.units, R.curK, j) * 1e-6;
  return bandColour(v, b[0], b[1]);
};

// static files are parsed once; live files are revalidated on every read (If-None-Match -> 304), and a
// live failure other than 404 throws so the page keeps its last good map
const parsed = new Map(); // url -> Promise<JSON>, static files only
function json(url, live) {
  if (!live && parsed.has(url)) return parsed.get(url);
  const p = fetch(url, { cache: live ? "no-cache" : "default" }).then((r) => {
    if (r.ok) return r.json();
    if (live && r.status !== 404) throw new Error(`${url}: HTTP ${r.status}`);
    return null;
  });
  if (!live) parsed.set(url, p);
  return p;
}

let emit = () => {}; // events back to the page: { type: "painted" | "drawStats", ... }
export const setEmitter = (fn) => { emit = fn; };

/* ---------------------------------------------------------------- geometry */

// Projection by country: Statistics Canada's Lambert conformal conic for Canada, Albers with insets
// for the US, an equal-area conic centred on the extent for large mid-latitude countries, else Mercator.
function projectionFor(country, fc) {
  if (country === "CA") return d3.geoConicConformal().parallels([49, 77]).rotate([96, 0]);
  if (country === "US") return d3.geoAlbersUsa();
  // New Zealand straddles the antimeridian (Chatham Islands): a transverse Mercator centred on
  // 173°E, like NZTM, keeps it in one piece
  if (country === "NZ") return d3.geoTransverseMercator().rotate([-173, 0]);
  if (country === "BR" || country === "MX") {
    const [[x0, y0], [x1, y1]] = d3.geoBounds(fc);
    const pad = (y1 - y0) / 6;
    return d3.geoConicEqualArea().parallels([y0 + pad, y1 - pad]).rotate([-(x0 + x1) / 2, 0]);
  }
  return d3.geoMercator();
}

const R = { // drawing state
  ctx: null, w: 0, h: 0, dpr: 1, view: { k: 1, x: 0, y: 0 },
  paths: [], isPoint: null, centres: null, n: 0, unitMesh: null, regionMesh: null, outline: null,
  regionOf: null, regionFill: new Map(), regionCentre: {}, regionFeat: new Map(), radius: null, pointOrder: [],
  // transitions, driven frame by frame from the page: focus region, dimming of the other regions
  // (fadeMix 0..1) and, with a state layer (natColours: region -> colour), the opacity of the
  // focus region's units over its state fill (natMix 0..1)
  focus: null, fadeMix: 0, natColours: null, natRings: null, natMix: 0,
  // map level: levelMix is the opacity of units outside the focus region over their region's fill
  // (0: state level, 1: unit level); with `aggregate` the region fills come from the units' totals
  levelMix: 1, aggregate: false,
  units: null, featIdx: null, unitLUT: null, parties: new Map(), partyLUTs: new Map(), countTimeline: true,
  mode: "margin", t: 0, tween: false, cache: new Map(), buf: null, lastCols: null, scheduled: false, painted: 0, drawMs: [],
};

export async function geo({ url, objectName, unitKey, country }) {
  const topo = await json(url, false);
  const obj = topo.objects[objectName];
  const features = topojson.feature(topo, obj).features;
  const n = features.length;
  const fc = { type: "FeatureCollection", features };
  const projection = projectionFor(country, fc).fitSize([BASE, BASE], fc);
  const path = d3.geoPath(projection);
  // features without a unit id (a base layer of land under the units, as in Israel's map): "" (ground)
  const ids = features.map((f) => (f.properties[unitKey] == null ? "" : String(f.properties[unitKey])));
  const regionOf = (f) => (f.properties[unitKey] == null ? undefined : String(f.properties[unitKey]).split("-")[1]);
  const isPoint = new Uint8Array(n);
  const centres = new Float64Array(2 * n).fill(NaN);
  const bounds = new Float64Array(4 * n);
  const areas = new Float64Array(n);
  const paths = new Array(n);
  const labels = new Float64Array(2 * n).fill(NaN); // label point per feature (points: the point)
  features.forEach((f, i) => {
    const t = f.geometry?.type ?? "";
    if (/Point$/.test(t)) {
      isPoint[i] = 1;
      const c = projection(t === "Point" ? f.geometry.coordinates : f.geometry.coordinates[0]);
      if (c) { centres[2 * i] = c[0]; centres[2 * i + 1] = c[1]; bounds.set([c[0], c[1], c[0], c[1]], 4 * i); labels[2 * i] = c[0]; labels[2 * i + 1] = c[1]; }
      paths[i] = "";
      return;
    }
    paths[i] = path(f) ?? "";
    const b = path.bounds(f);
    bounds.set([b[0][0], b[0][1], b[1][0], b[1][1]], 4 * i);
    areas[i] = path.area(f);
    // label point (circles of the outstanding-vote modes): the centroid of the largest part
    const main = t === "MultiPolygon" ? { type: "Polygon", coordinates: d3.greatest(f.geometry.coordinates, (c) => path.area({ type: "Polygon", coordinates: c })) } : f;
    const c = path.centroid(main);
    labels[2 * i] = c[0]; labels[2 * i + 1] = c[1];
  });
  const mesh = (filter) => path(topojson.mesh(topo, obj, filter)) ?? "";
  const regions = [...new Set(features.map(regionOf))];
  const regionOutlines = Object.fromEntries(regions.map((r) => [r, mesh((a, b) => (a === b ? regionOf(a) === r : (regionOf(a) === r) !== (regionOf(b) === r)))]));
  // each region as one shape (the state layer of a national map) and its label point
  const byRegion = d3.group(obj.geometries.map((g, i) => [g, i]), ([, i]) => regionOf(features[i]));
  const regionFill = new Map(), regionCentre = {}, regionFeat = new Map(), regionShapes = {};
  for (const [r, list] of byRegion) {
    const merged = topojson.merge(topo, list.map(([g]) => g));
    regionShapes[r] = path(merged) ?? "";
    regionFill.set(r, new Path2D(regionShapes[r]));
    regionCentre[r] = path.centroid(merged);
    regionFeat.set(r, list.map(([, i]) => i));
  }
  const result = {
    ids, names: features.map((f) => f.properties.name ?? null), paths, isPoint, centres, bounds, areas,
    unitMesh: mesh((a, b) => a !== b), regionMesh: mesh((a, b) => a !== b && regionOf(a) !== regionOf(b)), outline: mesh((a, b) => a === b),
    regionOutlines,
    regionShapes, // each region's merged shape, for the page's hover highlight at the state level
  };
  // keep what drawing needs
  Object.assign(R, {
    n, isPoint: isPoint.slice(), centres: centres.slice(), paths: paths.map((p, i) => (isPoint[i] ? null : new Path2D(p))),
    unitMesh: new Path2D(result.unitMesh), regionMesh: new Path2D(result.regionMesh), outline: new Path2D(result.outline),
    ids, regionOf: ids.map((id) => id.split("-")[1]), regionFill, regionCentre, regionFeat, labels,
    radius: new Float32Array(n).fill(3), pointOrder: Array.from({ length: n }, (_, i) => i).filter((i) => isPoint[i]),
  });
  return { result, transfer: [isPoint.buffer, centres.buffer, bounds.buffer, areas.buffer] };
}

// Warm the parse cache for units files the page is likely to open next (static files only).
export async function prefetch({ urls }) {
  await Promise.all(urls.map((u) => json(u, false).catch(() => null)));
  return { result: null, transfer: [] };
}

// What the last paint showed, for the map checks (analysis/site_check.py, via the page's
// window.__dashAudit): how many units (and regions, at the region level) were filled with a data
// colour, the explicit no-data hatch, or a plain ground ("none": the default ground, which no
// unit should show; "ground": the outstanding-vote modes' plain ground under their circles), the
// first few units on the default ground, and per region at the current frame the side its fill
// shows against the side ahead in its counted votes.
export async function audit() {
  const U = R.units, cols = R.lastCols;
  const atRegions = R.drawnRegion?.size > 0 && R.levelMix < 0.5;
  const kind = (s) => (s == null || s === NONE ? "none" : s === "hatch" ? "hatch" : s === "ground" ? "base" : /^(#fff(fff)?|white)$/i.test(s) ? "white" : s === REM_GROUND ? "ground" : "colour");
  const units = { colour: 0, hatch: 0, none: 0, white: 0, ground: 0, base: 0 }, plain = [];
  for (let i = 0; i < R.n; i++) {
    if (R.hidden?.[i]) continue;
    if (atRegions && !(R.focus && R.regionOf[i] === R.focus && R.natMix >= 0.5)) continue;
    const s = R.isPoint[i] ? cols?.[i] : R.drawn?.[i];
    if (s === undefined && !R.isPoint[i]) continue; // not drawn (under a region fill)
    const c = kind(s);
    units[c]++;
    if ((c === "none" || c === "white") && plain.length < 12) plain.push(R.ids[i]);
  }
  const regions = {};
  for (const [r, s] of R.drawnRegion ?? []) if (r != null) regions[r] = kind(s);
  // the side each region's fill shows (margin mode, the units' region totals) and the side ahead
  // in the counted votes at this frame
  const sides = {};
  const k = frameIndex(R.t);
  if (U && R.aggregate && k >= 0 && R.mode === "margin") {
    for (const [r, js] of U.byRegion) {
      const g = regionAgg(U, k, js);
      const c = R.drawnRegion?.get(r);
      if (c == null || c === "hatch" || g.raw == null) continue;
      const [ba, bb] = R.unitBand[js[0]] ?? [[], []];
      const shown = ba.includes(c) ? "a" : bb.includes(c) ? "b" : c === TIE ? "tie" : "other";
      sides[r] = { shown, ahead: Math.abs(g.raw - 0.5) < 1e-9 ? "tie" : g.raw > 0.5 ? "a" : "b", counted: g.counted ?? null };
    }
  }
  // the same per unit, for units fully counted at this frame (their fill is the model's projection,
  // which equals the count once everything is counted)
  const unitSides = { checked: 0, wrong: [] };
  if (U && k >= 0 && R.mode === "margin" && R.featIdx) {
    for (let i = 0; i < R.n; i++) {
      const j = R.featIdx[i], c = R.drawn?.[i];
      if (j < 0 || !c || c === "hatch" || U.F[k * U.n + j] < 999 || U.A[k * U.n + j] < 0) continue;
      const a = U.A[k * U.n + j];
      if (a === 500) continue;
      const [ba, bb] = R.unitBand[j] ?? [[], []];
      const shown = ba.includes(c) ? "a" : bb.includes(c) ? "b" : "other";
      unitSides.checked++;
      if (shown !== (a > 500 ? "a" : "b") && unitSides.wrong.length < 12) unitSides.wrong.push(`${R.ids[i]} shows ${shown}`);
    }
  }
  return { result: { mode: R.mode, k, K: U?.K ?? 0, atRegions, units, plain, regions, sides, unitSides }, transfer: [] };
}

/* ---------------------------------------------------------------- map frames */

// One or more units files (several when an overview merges one race per region). Frames are
// aligned on the union of their times; each file contributes its latest frame at or before each time.
export async function units({ urls, live, countTimeline }) {
  const files = await Promise.all(urls.map((u) => json(u, live)));
  const ok = files.map((f, s) => [f, s]).filter(([f]) => f?.unit_ids?.length);
  if (!ok.length) { R.units = null; schedule(); return { result: null, transfer: [] }; }
  // a frame without a time (a pre-count estimate, t: null) holds from the start
  const frameT = (fr) => (fr.t ? Date.parse(fr.t) : -8.64e15);
  const times = [...new Set(ok.flatMap(([f]) => (f.frames ?? []).map(frameT)))].sort((a, b) => a - b);
  const n = ok.reduce((s, [f]) => s + f.unit_ids.length, 0);
  const K = times.length;
  const unit_ids = [], region = [], names = [], layer = []; // layer: overlapping unit sets (NZ electorates)
  const source = new Uint8Array(n), prior = new Int16Array(n).fill(-1), V0 = new Float64Array(n);
  const F = new Int16Array(K * n), A = new Int16Array(K * n).fill(-1), P = new Int16Array(K * n).fill(-1);
  // the projection of the race's poll model (proj_poll) where a file has one: P holds it (falling
  // back to proj unit by unit) and P1 the past-results projection (proj), shown as "margin1"
  const P1 = new Int16Array(K * n).fill(-1);
  let hasPoll = false;
  // outstanding vote (display only): expected remaining votes and their projected margin (side a
  // minus side b, in votes); NaN where a file has none
  const REM = new Float32Array(K * n).fill(NaN), REMM = new Float32Array(K * n).fill(NaN);
  let hasRem = false;
  // turnout (modelled races): expected two-party votes, the low-propensity index and the county's
  // past margin per unit; per frame, the turnout ratio (counted or projected / expected, ×1000)
  const TE = new Float64Array(n).fill(NaN), LP = new Float32Array(n).fill(NaN), PMG = new Float32Array(n).fill(NaN);
  const TR = new Int16Array(K * n).fill(-1);
  let hasTurnout = false;
  const hasWinner = ok.some(([f]) => f.winner || f.frames?.[0]?.winner);
  const perFrame = hasWinner && ok.some(([f]) => f.frames?.[0]?.winner);
  const W = perFrame ? new Array(K * n).fill(null) : null, M = perFrame ? new Int16Array(K * n) : null;
  const top = hasWinner && !perFrame ? { winner: new Array(n).fill(null), margin: new Int16Array(n) } : null;
  let off = 0;
  for (const [f, s] of ok) {
    const m = f.unit_ids.length;
    for (let j = 0; j < m; j++) {
      unit_ids.push(f.unit_ids[j]); region.push(f.region?.[j] ?? null); names.push(f.names?.[j] ?? null); layer.push(f.layer?.[j] ?? null);
      source[off + j] = s;
      prior[off + j] = f.prior?.[j] ?? -1;
      V0[off + j] = f.V0?.[j] ?? 0;
      if (top) { top.winner[off + j] = f.winner?.[j] ?? null; top.margin[off + j] = f.margin?.[j] ?? 0; }
      TE[off + j] = f.turnout_exp?.[j] ?? NaN;
      LP[off + j] = f.lowprop_index?.[j] ?? NaN;
      PMG[off + j] = f.prior_margin?.[j] ?? NaN;
    }
    const ft = (f.frames ?? []).map(frameT);
    let fk = -1;
    for (let k = 0; k < K; k++) {
      while (fk + 1 < ft.length && ft[fk + 1] <= times[k]) fk++;
      if (fk < 0) continue;
      const fr = f.frames[fk], base = k * n + off;
      for (let j = 0; j < m; j++) {
        F[base + j] = fr.f?.[j] ?? 0;
        A[base + j] = fr.a?.[j] ?? -1;
        P1[base + j] = fr.proj?.[j] ?? -1;
        const pp = fr.proj_poll?.[j];
        P[base + j] = pp != null ? pp : P1[base + j];
        if (pp != null) hasPoll = true;
        if (W && fr.winner) { W[base + j] = fr.winner[j]; M[base + j] = fr.margin?.[j] ?? 0; }
        if (fr.rem) { REM[base + j] = fr.rem[j] ?? NaN; REMM[base + j] = fr.rem_margin_poll?.[j] ?? fr.rem_margin?.[j] ?? NaN; hasRem = true; }
        const tr = fr.turnout_ratio_live?.[j];
        if (tr != null) { TR[base + j] = Math.min(32767, tr); hasTurnout = true; }
      }
    }
    off += m;
  }
  // per-mode values, normalised to [-1, 1] (diverging) or [0, 1] (counted); NaN = no data
  const counted = new Float32Array(K * n), swing = new Float32Array(K * n), margin = new Float32Array(K * n), margin1 = hasPoll ? new Float32Array(K * n) : null;
  const turnout = hasTurnout ? new Float32Array(K * n) : null; // ratio to expected, ±20% full colour
  const clamp = (v) => Math.max(-1, Math.min(1, v));
  for (let k = 0; k < K; k++) {
    for (let j = 0; j < n; j++) {
      const i = k * n + j, a = A[i], p = prior[j];
      counted[i] = F[i] / 1000;
      swing[i] = a >= 0 && p >= 0 ? clamp((a - p) / 1000 / SCALE.swing) : NaN;
      // a fully counted unit shows its count (the projection can differ from it by rounding or by
      // votes the model still expected, e.g. Hungary's 49.8% counted, 50.2% projected)
      const done = F[i] >= 1000 && a >= 0;
      const v = countTimeline && !done ? P[i] : a >= 0 ? a : P[i];
      margin[i] = v >= 0 ? clamp((2 * v / 1000 - 1) / SCALE.margin) : NaN;
      if (margin1) { const w = countTimeline && !done ? P1[i] : a >= 0 ? a : P1[i]; margin1[i] = w >= 0 ? clamp((2 * w / 1000 - 1) / SCALE.margin) : NaN; }
      if (turnout) turnout[i] = TR[i] >= 0 ? clamp((TR[i] / 1000 - 1) / SCALE.turnout) : NaN;
    }
  }
  const priorVals = new Float32Array(n);
  for (let j = 0; j < n; j++) priorVals[j] = prior[j] >= 0 ? clamp((2 * prior[j] / 1000 - 1) / SCALE.prior) : NaN;
  const first = ok[0][0];
  const winner = !hasWinner ? null : W ? { perFrame: W, margin: M } : { top: top.winner, margin: top.margin };
  const result = {
    n, K, unit_ids, region, names, layer, source, prior, V0, times: Float64Array.from(times), F, A, P, P1, hasPoll, winner,
    turnout: hasTurnout ? { exp: TE, lowprop: LP, priorMargin: PMG, ratio: TR } : null,
    rem: hasRem ? { votes: REM, margin: REMM } : null,
    race_id: ok.length === 1 ? first.race_id ?? null : null,
    a: ok.length === 1 ? first.a ?? null : null, b: ok.length === 1 ? first.b ?? null : null, label: first.label ?? null,
  };
  // circle scales for the outstanding-vote modes: fixed per file (the largest value of any frame),
  // so circles shrink as the count goes on
  let maxRem = 0, maxLean = 0;
  if (hasRem) for (let i = 0; i < K * n; i++) { if (REM[i] > maxRem) maxRem = REM[i]; if (Math.abs(REMM[i]) > maxLean) maxLean = Math.abs(REMM[i]); }
  result.remScale = { maxRem, maxLean };
  // the drawing side keeps its own values; the page gets copies of the arrays it reads
  R.units = {
    n, K, times: result.times, winner, vals: { counted, swing, margin, margin1, turnout, prior: priorVals }, rem: result.rem, remScale: result.remScale,
    F, A, P, P1, prior, V0, TE, TR, byRegion: unitsByRegion(region),
  };
  R.cache.clear();
  R.featIdx = null; // waits for "bind" with this file's feature index
  return { result, transfer: [] };
}

/* ---------------------------------------------------------------- drawing */

function lutFor(a, b) {
  return d3.range(201).map((j) => {
    const v = j / 100 - 1;
    return d3.interpolateLab(NEUTRAL, v >= 0 ? a : b)(Math.abs(v) ** 0.8);
  });
}
const COUNTED_LUT = d3.range(101).map((i) => d3.interpolateLab("#e9e7e0", "#4f647d")(i / 100));
function partyLUT(id) {
  if (!R.partyLUTs.has(id)) {
    const color = R.parties.get(id) ?? "#8a8a8a";
    R.partyLUTs.set(id, d3.range(21).map((i) => d3.interpolateLab(NEUTRAL, color)(0.3 + 0.7 * (i / 20))));
  }
  return R.partyLUTs.get(id);
}

export function control(msg) {
  switch (msg.type) {
    case "canvas": R.ctx = msg.canvas.getContext("2d"); R.canvas = msg.canvas; break;
    case "size": Object.assign(R, { w: msg.w, h: msg.h, dpr: msg.dpr }); if (R.canvas) { R.canvas.width = Math.round(msg.w * msg.dpr); R.canvas.height = Math.round(msg.h * msg.dpr); } break;
    case "view": R.view = msg.view; break;
    case "frame": R.view = msg.view ?? R.view; R.fadeMix = msg.fadeMix ?? R.fadeMix; R.natMix = msg.natMix ?? R.natMix; R.levelMix = msg.levelMix ?? R.levelMix; break;
    case "state": Object.assign(R, msg.state); break;
    case "nat": R.natColours = msg.colours ? new Map(msg.colours) : null; R.natRings = new Set(msg.rings ?? []); break;
    case "bind": {
      // per-unit colour tables from the races' two colours; party colours for "winner"
      const luts = msg.raceColors.map(([a, b]) => lutFor(a, b));
      R.unitLUT = Array.from(msg.unitRace, (r) => luts[r]);
      // leader colouring for margins: each side's four band shades (dash-agg.js)
      const bands = msg.raceColors.map(([a, b]) => [bandColours(a, NEUTRAL), bandColours(b, NEUTRAL)]);
      R.unitBand = Array.from(msg.unitRace, (r) => bands[r]);
      R.featIdx = msg.featIdx;
      R.parties = new Map((msg.parties ?? []).map((p) => [p.id, p.color]));
      R.hatch = new Set(msg.hatch ?? []);
      R.hidden = msg.hidden ?? null; // per feature: 1 = in a layer not shown
      R.partyLUTs.clear();
      R.countTimeline = msg.countTimeline;
      if (msg.radius) R.radius = msg.radius;
      if (msg.pointOrder) R.pointOrder = msg.pointOrder;
      R.cache.clear();
      break;
    }
    case "paint":
      // a new colouring (mode): the old fills fade out over the new ones for XFADE_MS
      if (msg.mode !== R.mode && R.drawn && !msg.tween) R.xfade = { units: R.drawn, regions: R.drawnRegion, t0: performance.now() };
      Object.assign(R, { mode: msg.mode, t: msg.t, tween: msg.tween });
      break;
    default: return;
  }
  schedule();
}

// Paint once the queued messages are handled: on the page right after the current task (a
// microtask), in a worker after the message backlog (a zero-delay timer, which lets a burst of
// "frame"/"paint" messages share one paint when a paint is slower than the frames arrive). No
// animation frames: headless and hidden pages throttle or never run them.
const IN_WORKER = typeof WorkerGlobalScope !== "undefined" && self instanceof WorkerGlobalScope;
function schedule() {
  if (R.scheduled) return;
  R.scheduled = true;
  const run = () => { R.scheduled = false; draw(); };
  if (IN_WORKER) setTimeout(run, 0); else queueMicrotask(run);
}

function frameIndex(t) {
  const ft = R.units?.times;
  if (!ft?.length) return -1;
  return Math.max(0, d3.bisectRight(ft, t) - 1);
}

const REM_MODES = new Set(["rem", "remlean"]);
const REM_GROUND = "#f1efe9", REM_R = 22; // largest circle radius, CSS px
// "ev": ballots cast before election day as a share of the 2022 total vote, per feature (R.ev,
// sent by the page; NaN = no figure), 0 to 100% on one sequential scale. Any county with a
// figure is coloured, whether or not the race has units there.
const EV_LUT = d3.range(101).map((i) => d3.interpolateLab("#ece9e1", "#3d5a40")(i / 100)); // same ramp in election.js
function evColours() {
  const ev = R.ev, out = new Array(R.n);
  for (let i = 0; i < R.n; i++) out[i] = ev && Number.isFinite(ev[i]) ? EV_LUT[Math.max(0, Math.min(100, Math.round(ev[i] * 100)))] : NONE;
  return (R.lastCols = out);
}
// Per-feature colours for the current mode and time. Between a new units file and its "bind" (the
// feature index arrives a moment later) the last colours stay on screen, so nothing blinks.
function colours() {
  if (R.mode === "ev") return evColours();
  const U = R.units, idx = R.featIdx, nf = R.n;
  if (!U || !idx || idx.length !== nf) return R.lastCols;
  const k = frameIndex(R.t);
  R.curK = k; // the frame for tie-breaks in band()
  const tween = R.tween && R.mode !== "winner" && k >= 0 && k + 1 < U.K;
  const key = `${R.mode}|${k}`;
  if (!tween && R.cache.has(key)) return (R.lastCols = R.cache.get(key));
  const out = tween ? (R.buf && R.buf.length === nf ? R.buf : (R.buf = new Array(nf))) : new Array(nf);
  const n = U.n, luts = R.unitLUT;
  if (REM_MODES.has(R.mode)) {
    // outstanding-vote modes: a plain ground under the circles (drawn in draw())
    for (let i = 0; i < nf; i++) out[i] = idx[i] < 0 ? NONE : REM_GROUND;
  } else if (R.mode === "winner" && U.winner) {
    const w = U.winner;
    for (let i = 0; i < nf; i++) {
      const j = idx[i];
      if (j < 0) { out[i] = NONE; continue; }
      const p = w.perFrame ? (k >= 0 ? w.perFrame[k * n + j] : null) : w.top[j];
      const m = w.perFrame ? (k >= 0 ? w.margin[k * n + j] : 0) : w.margin[j];
      out[i] = p == null ? NONE : partyLUT(p)[Math.round(Math.min(1, m / 1000 / WIN_FULL) * 20)];
    }
  } else if (R.mode === "prior" || k < 0) {
    const vals = U.vals.prior;
    for (let i = 0; i < nf; i++) {
      const j = idx[i], v = j < 0 ? NaN : vals[j];
      out[i] = Number.isNaN(v) ? NONE : band(j, v * SCALE.prior * 100);
    }
  } else {
    const vals = U.vals[R.mode] ?? U.vals.margin, b0 = k * n;
    const k1 = tween ? k + 1 : k;
    const s = tween ? Math.max(0, Math.min(1, (R.t - U.times[k]) / (U.times[k1] - U.times[k]))) : 0;
    const b1 = k1 * n;
    const counted = R.mode === "counted";
    for (let i = 0; i < nf; i++) {
      const j = idx[i];
      if (j < 0) { out[i] = NONE; continue; }
      const v0 = vals[b0 + j], v1 = vals[b1 + j];
      let v = v0;
      if (s > 0) {
        if (Number.isNaN(v0)) v = s >= 0.5 ? v1 : NaN;
        else if (!Number.isNaN(v1)) v = v0 + (v1 - v0) * s;
      }
      out[i] = counted ? (v >= 0 ? COUNTED_LUT[Math.min(100, Math.round(v * 100))] : NONE) : Number.isNaN(v) ? NONE
        : R.mode === "margin" || R.mode === "margin1" ? band(j, v * SCALE.margin * 100, true)
        : R.mode === "turnout" ? TURN_LUT[Math.round((v + 1) * 100)] : luts[j][Math.round((v + 1) * 100)];
    }
  }
  if (!tween) R.cache.set(key, out);
  return (R.lastCols = out);
}

// Region fills from the units' totals (the state level): one colour per region for the current
// mode and frame, on the region's race colours. Cached per mode and frame like the unit colours.
const lutAt = (lut, v) => lut[Math.round((Math.max(-1, Math.min(1, v)) + 1) * 100)];
function aggColours() {
  const U = R.units;
  if (!U || !R.unitLUT) return null;
  const k = frameIndex(R.t);
  const key = `agg|${R.mode}|${k}`;
  if (R.cache.has(key)) return R.cache.get(key);
  const out = new Map(), w = U.winner;
  for (const [r, js] of U.byRegion) {
    const lut = R.unitLUT[js[0]];
    let c = NONE;
    if (REM_MODES.has(R.mode)) c = REM_GROUND;
    else if (R.mode === "winner" && w) {
      const lead = regionWinner(U, js, (j) => (w.perFrame ? (k >= 0 ? w.perFrame[k * U.n + j] : null) : w.top[j]));
      if (lead) c = partyLUT(lead.party)[Math.round(lead.share * 20)];
    } else {
      const g = regionAgg(R.mode === "margin1" ? { ...U, P: U.P1 } : U, k, js); // margin1: the past-results projection
      if (R.mode === "counted") c = g.counted >= 0 ? COUNTED_LUT[Math.min(100, Math.round(g.counted * 100))] : NONE;
      else if (R.mode === "prior" || k < 0) c = g.prior != null ? band(js[0], (2 * g.prior - 1) * 100) : NONE;
      else if (R.mode === "swing") c = g.raw != null && g.prior != null ? lutAt(lut, (g.raw - g.prior) / SCALE.swing) : NONE;
      else if (R.mode === "turnout") {
        // the region's turnout ratio, weighted by expected votes
        let w = 0, s = 0;
        for (const j of js) { const r = k >= 0 ? U.TR[k * U.n + j] : -1, e = U.TE[j]; if (r >= 0 && e > 0) { w += e; s += e * r / 1000; } }
        c = w > 0 ? TURN_LUT[Math.round((Math.max(-1, Math.min(1, (s / w - 1) / SCALE.turnout)) + 1) * 100)] : NONE;
      }
      else {
        const v = R.countTimeline ? g.proj : g.raw ?? g.proj;
        c = v != null ? band(js[0], (2 * v - 1) * 100) : NONE;
      }
    }
    out.set(r, c);
  }
  R.cache.set(key, out);
  return out;
}

// Light diagonal hatch (regions without a baseline), 6 CSS px apart at any zoom.
function hatchPattern(ctx, px) {
  if (!R.hatchTile) {
    const c = new OffscreenCanvas(6, 6), g = c.getContext("2d");
    g.fillStyle = NEUTRAL;
    g.fillRect(0, 0, 6, 6);
    g.strokeStyle = "#dcd9d1"; // light: no-data areas recede behind the coloured ones
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-1, 1); g.lineTo(1, -1); g.moveTo(5, 7); g.lineTo(7, 5); g.stroke();
    R.hatchTile = c;
  }
  const pat = ctx.createPattern(R.hatchTile, "repeat");
  pat.setTransform(new DOMMatrix().scaleSelf(px, px));
  return pat;
}
// the same lines on a clear ground, laid over coloured units (turnout projected, under 95% reported)
function hatchOverlay(ctx, px) {
  if (!R.hatchLines) {
    const c = new OffscreenCanvas(6, 6), g = c.getContext("2d");
    g.strokeStyle = "rgba(27,27,27,0.35)";
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-1, 1); g.lineTo(1, -1); g.moveTo(5, 7); g.lineTo(7, 5); g.stroke();
    R.hatchLines = c;
  }
  const pat = ctx.createPattern(R.hatchLines, "repeat");
  pat.setTransform(new DOMMatrix().scaleSelf(px, px));
  return pat;
}

// One frame, always drawn from the projected paths under the current view (no cached bitmaps, so
// a zoom or level change never shows two pictures): units (or, with a state layer, region fills
// plus the units faded in over them), then the meshes and the open region's outline. Hover and pin
// outlines are drawn by the page on its own overlay canvas (dash-map.js), so pointer movement
// never repaints this one.
function draw() {
  const ctx = R.ctx;
  if (!ctx || !R.w || !R.n) return;
  const t0 = performance.now();
  paint(ctx);
  // a cross-fade in progress: the next frame in ~16 ms until it ends
  if (R.xfade && !R.xfadeTimer) R.xfadeTimer = setTimeout(() => { R.xfadeTimer = 0; R.scheduled = false; draw(); }, 16);
  R.drawMs.push(performance.now() - t0);
  if (R.lastCols && !R.painted++) emit({ type: "painted", at: performance.timeOrigin + performance.now() });
  if (R.drawMs.length >= 30) { emit({ type: "drawStats", ms: R.drawMs }); R.drawMs = []; }
}

function paint(ctx) {
  const { k, x, y } = R.view;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.setTransform(R.dpr * k, 0, 0, R.dpr * k, R.dpr * x, R.dpr * y);
  const px = 1 / k;
  const cols = colours(), paths = R.paths, focus = R.focus;
  const inFocus = focus ? R.regionFeat.get(focus) ?? [] : null;
  // the other regions stay but recede as the view closes in on one: greyed (saturation blend) and
  // 60% toward the paper, with the zoom (fadeMix); drawn over their fills, under their borders
  const veil = () => {
    if (!focus || !(R.fadeMix > 0)) return;
    const ga = ctx.globalAlpha;
    ctx.globalCompositeOperation = "saturation";
    ctx.globalAlpha = 0.8 * R.fadeMix;
    ctx.fillStyle = "#808080";
    for (const [r, p] of R.regionFill) if (r != null && r !== focus) ctx.fill(p);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = VEIL * R.fadeMix;
    ctx.fillStyle = PAPER;
    for (const [r, p] of R.regionFill) if (r != null && r !== focus) ctx.fill(p);
    ctx.globalAlpha = ga;
  };
  const dim = focus ? 1 - VEIL * R.fadeMix : 1; // points, circles and labels of the other regions
  const hatch = R.hatch?.size ? hatchPattern(ctx, px) : null;
  let hatchFill = null; // the same hatch for units and regions without data, made on first use
  const noDataFill = () => hatch ?? (hatchFill ??= hatchPattern(ctx, px));
  // the cross-fade from the previous colouring: its fills over the new ones at 1 - progress
  const xf = R.xfade ? Math.min(1, (performance.now() - R.xfade.t0) / XFADE_MS) : 1;
  const old = xf < 1 ? R.xfade : null;
  if (xf >= 1) R.xfade = null;
  const styleOf = (s) => (s === "hatch" ? noDataFill() : s === "ground" ? NONE : s);
  const hidden = R.hidden;
  // what each unit and region was last filled with ("hatch", "ground" or a colour), for audit()
  const drawn = (R.drawn = new Array(R.n)), drawnRegion = (R.drawnRegion = new Map());
  // fills grouped by colour: one fillStyle per colour instead of one per unit (costly in Firefox).
  // Every unit without a figure (outside the race, not reporting yet, no result) is hatched, so
  // it never passes for a colour on the map's scale; a base layer (no unit id) is plain ground.
  const fillUnits = (list) => {
    const groups = new Map();
    for (const i of list) {
      if (!paths[i] || hidden?.[i]) continue;
      const c = cols ? cols[i] : NONE;
      const base = !R.ids[i];
      const noData = !base && ((hatch && R.hatch.has(R.regionOf[i])) || (R.mode !== "ev" && R.featIdx?.[i] < 0) || c === NONE);
      const s = base ? NONE : noData ? noDataFill() : c;
      drawn[i] = base ? "ground" : noData ? "hatch" : c;
      const g = groups.get(s);
      if (g) g.push(i); else groups.set(s, [i]);
    }
    for (const [s, g] of groups) {
      ctx.fillStyle = s;
      for (const i of g) ctx.fill(paths[i]);
    }
    if (old) {
      const ga = ctx.globalAlpha, prev = new Map();
      for (const i of list) { const s = old.units[i]; if (s !== undefined && s !== drawn[i]) { const g = prev.get(s); if (g) g.push(i); else prev.set(s, [i]); } }
      ctx.globalAlpha = ga * (1 - xf);
      for (const [s, g] of prev) { ctx.fillStyle = styleOf(s); for (const i of g) ctx.fill(paths[i]); }
      ctx.globalAlpha = ga;
    }
  };
  const nat = R.natColours;
  // a region layer: the national map's state colours (nat) or the units' region totals (aggregate)
  const regionCols = R.mode === "ev" ? null : nat ?? (R.aggregate ? aggColours() : null);
  const unitW = Math.min(1, 0.3 + 0.12 * k) * px; // unit borders: up to 1 CSS px, thinner zoomed out
  if (regionCols) {
    // state level under the units: the focus region's units show at natMix (or the level's mix),
    // the other regions' units at levelMix (0: state level, 1: unit level)
    const uO = R.levelMix, uF = inFocus ? Math.max(R.levelMix, R.natMix) : uO;
    if (uO < 1 || uF < 1) {
      for (const [r, p] of R.regionFill) {
        ctx.globalAlpha = 1;
        const c = regionCols.get(r);
        const noData = r != null && ((hatch && R.hatch.has(r)) || c == null || c === NONE);
        ctx.fillStyle = r == null ? NONE : noData ? noDataFill() : c;
        drawnRegion.set(r, r == null ? "ground" : noData ? "hatch" : c);
        ctx.fill(p);
        const was = old?.regions?.get(r);
        if (was !== undefined && was !== drawnRegion.get(r)) { const ga = ctx.globalAlpha; ctx.globalAlpha = ga * (1 - xf); ctx.fillStyle = styleOf(was); ctx.fill(p); ctx.globalAlpha = ga; }
      }
    }
    if (!(uO > 0)) veil();
    const others = [];
    if (uO > 0) for (let i = 0; i < R.n; i++) if (!focus || R.regionOf[i] !== focus) others.push(i);
    if (uO > 0) {
      ctx.globalAlpha = uO;
      fillUnits(others);
      veil();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = unitW;
      if (hidden) { for (const i of others) if (paths[i] && !hidden[i]) ctx.stroke(paths[i]); } else ctx.stroke(R.unitMesh);
    }
    if (inFocus && uF > 0) {
      ctx.globalAlpha = uF;
      fillUnits(inFocus);
      ctx.strokeStyle = hatch && R.hatch.has(focus) ? "#bdb9ae" : "#ffffff";
      ctx.lineWidth = hatch && R.hatch.has(focus) ? 0.6 * px : unitW;
      for (const i of inFocus) if (paths[i] && !hidden?.[i]) ctx.stroke(paths[i]);
    }
  } else {
    // units: the other regions first (veiled as the view closes in), then the focus region
    ctx.globalAlpha = 1;
    const others = [];
    for (let i = 0; i < R.n; i++) if (!focus || R.regionOf[i] !== focus) others.push(i);
    fillUnits(others);
    veil();
    ctx.globalAlpha = 1;
    if (inFocus) fillUnits(inFocus);
    ctx.strokeStyle = "#ffffff";
    if (hidden) {
      // overlapping layers: the shared meshes would draw the other layer's lines; outline each unit
      ctx.lineWidth = Math.min(1, 0.4 + 0.2 * k) * px;
      for (let i = 0; i < R.n; i++) if (paths[i] && !hidden[i]) ctx.stroke(paths[i]);
    } else {
      ctx.lineWidth = unitW;
      ctx.stroke(R.unitMesh);
    }
    if (hatch) {
      // county lines inside hatched regions, where the white mesh would not show
      ctx.strokeStyle = "#bdb9ae";
      ctx.lineWidth = 0.6 * px;
      for (let i = 0; i < R.n; i++) if (paths[i] && R.hatch.has(R.regionOf[i])) ctx.stroke(paths[i]);
    }
  }
  ctx.globalAlpha = 1;
  if (R.mode === "turnout" && R.units?.TR && cols) {
    // turnout still projected (under 95% reported): hatched over its colour
    const U = R.units, k = R.curK, idx = R.featIdx;
    if (k >= 0 && idx) {
      ctx.fillStyle = hatchOverlay(ctx, px);
      for (let i = 0; i < R.n; i++) {
        const j = idx[i];
        if (j < 0 || !paths[i] || U.TR[k * U.n + j] < 0 || U.F[k * U.n + j] >= 950) continue;
        // only where units are shown (not under a region fill at the state level)
        if (regionCols && R.levelMix < 1 && !(focus && R.regionOf[i] === focus && R.natMix >= 1)) continue;
        ctx.fill(paths[i]);
      }
    }
  }
  // region borders: white, 2 px at the state level, 1.5 px with a fine dark core at the unit level
  // (so state lines stay visible among county lines)
  const unitLevel = regionCols ? R.levelMix : 1;
  if (!hidden) {
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = (2 - 0.5 * unitLevel) * px;
    ctx.stroke(R.regionMesh);
    if (unitLevel > 0) {
      ctx.strokeStyle = `rgba(27,27,27,${(0.45 * unitLevel).toFixed(3)})`;
      ctx.lineWidth = 0.6 * px;
      ctx.stroke(R.regionMesh);
    }
  }
  ctx.strokeStyle = "#8d8a82";
  ctx.lineWidth = 0.7 * px;
  ctx.stroke(R.outline);
  const pointMix = regionCols ? R.levelMix : 1;
  if (pointMix > 0 && R.pointOrder.length) {
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 0.6 * px;
    for (const i of R.pointOrder) {
      if (!Number.isFinite(R.centres[2 * i])) continue;
      ctx.globalAlpha = pointMix * (focus && R.regionOf[i] !== focus ? dim : 1);
      ctx.beginPath();
      ctx.arc(R.centres[2 * i], R.centres[2 * i + 1], R.radius[i] * px, 0, 2 * Math.PI);
      ctx.fillStyle = cols ? cols[i] : NONE;
      ctx.fill();
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  if (REM_MODES.has(R.mode)) remCircles(ctx, px, dim, nat && R.levelMix < 1);
  if (nat && R.natRings?.size && R.levelMix < 1) {
    // regions with a unit-level model: a ring at the region's centre, gone once its units show
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.6 * px;
    for (const r of R.natRings) {
      const c = R.regionCentre[r];
      if (!c || !Number.isFinite(c[0])) continue;
      ctx.globalAlpha = (1 - R.levelMix) * (r === focus ? 1 - R.natMix : dim);
      ctx.beginPath();
      ctx.arc(c[0], c[1], 5 * px, 0, 2 * Math.PI);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  // the open region's outline, in the same frame as the map under it (never a frame apart)
  if (focus && R.fadeMix > 0 && R.regionFill.has(focus)) {
    ctx.globalAlpha = R.fadeMix;
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.6 * px;
    ctx.stroke(R.regionFill.get(focus));
    ctx.globalAlpha = 1;
  }
}

// Outstanding-vote modes: a circle per unit at its label point, area proportional to the votes
// left ("rem") or to the projected margin of those votes ("remlean", coloured by the side it
// favours). Screen-size circles on a fixed scale per file; biggest drawn first.
function remCircles(ctx, px, dim, nat) {
  const U = R.units, idx = R.featIdx;
  if (!U?.rem || !idx || idx.length !== R.n) return;
  const k = frameIndex(R.t);
  if (k < 0) return;
  const lean = R.mode === "remlean";
  const vals = lean ? U.rem.margin : U.rem.votes;
  const max = lean ? U.remScale.maxLean : U.remScale.maxRem;
  if (!(max > 0)) return;
  const k1 = R.tween && k + 1 < U.K ? k + 1 : k;
  const s = k1 > k ? Math.max(0, Math.min(1, (R.t - U.times[k]) / (U.times[k1] - U.times[k]))) : 0;
  const items = [];
  for (let i = 0; i < R.n; i++) {
    const j = idx[i];
    if (j < 0 || R.hidden?.[i] || !Number.isFinite(R.labels[2 * i])) continue;
    if (nat && R.regionOf[i] !== R.focus) continue;
    const v0 = vals[k * U.n + j], v1 = vals[k1 * U.n + j];
    const v = Number.isNaN(v1) ? v0 : v0 + (v1 - v0) * s;
    if (!v || Number.isNaN(v)) continue;
    items.push([i, j, v]);
  }
  items.sort((a, b) => Math.abs(b[2]) - Math.abs(a[2]));
  ctx.strokeStyle = "#fcfbf8";
  ctx.lineWidth = 0.7 * px;
  for (const [i, j, v] of items) {
    const r = Math.max(0.8, REM_R * Math.sqrt(Math.abs(v) / max)) * px;
    const base = nat ? R.natMix : R.focus && R.regionOf[i] !== R.focus ? dim : 1;
    ctx.globalAlpha = 0.72 * base;
    ctx.fillStyle = lean ? R.unitLUT[j][v > 0 ? 200 : 0] : "#4f647d";
    ctx.beginPath();
    ctx.arc(R.labels[2 * i], R.labels[2 * i + 1], r, 0, 2 * Math.PI);
    ctx.fill();
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}
