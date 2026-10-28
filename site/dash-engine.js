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

const BASE = 1000; // base projection extent, shared with dash-map.js
const SCALE = { swing: 0.1, margin: 0.4, prior: 0.4 }; // value at which a diverging colour is full
const NONE = "#efede7", NEUTRAL = "#f4f2ec";
const WIN_FULL = 0.3; // margin (share points / 100) at which a party's colour is full

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
  paths: [], isPoint: null, centres: null, n: 0, unitMesh: null, regionMesh: null, outline: null, regionOutlineStr: {}, regionOutlines: new Map(),
  regionOf: null, focus: null, fade: null, hover: -1, pinned: -1, hoverRegion: null, radius: null, pointOrder: [],
  units: null, featIdx: null, unitLUT: null, parties: new Map(), partyLUTs: new Map(), countTimeline: true,
  mode: "margin", t: 0, tween: false, cache: new Map(), buf: null, scheduled: false, painted: 0, drawMs: [],
};

export async function geo({ url, objectName, unitKey, country }) {
  const topo = await json(url, false);
  const obj = topo.objects[objectName];
  const features = topojson.feature(topo, obj).features;
  const n = features.length;
  const fc = { type: "FeatureCollection", features };
  const projection = projectionFor(country, fc).fitSize([BASE, BASE], fc);
  const path = d3.geoPath(projection);
  const ids = features.map((f) => String(f.properties[unitKey]));
  const regionOf = (f) => String(f.properties[unitKey]).split("-")[1];
  const isPoint = new Uint8Array(n);
  const centres = new Float64Array(2 * n).fill(NaN);
  const bounds = new Float64Array(4 * n);
  const areas = new Float64Array(n);
  const paths = new Array(n);
  features.forEach((f, i) => {
    const t = f.geometry?.type ?? "";
    if (/Point$/.test(t)) {
      isPoint[i] = 1;
      const c = projection(t === "Point" ? f.geometry.coordinates : f.geometry.coordinates[0]);
      if (c) { centres[2 * i] = c[0]; centres[2 * i + 1] = c[1]; bounds.set([c[0], c[1], c[0], c[1]], 4 * i); }
      paths[i] = "";
      return;
    }
    paths[i] = path(f) ?? "";
    const b = path.bounds(f);
    bounds.set([b[0][0], b[0][1], b[1][0], b[1][1]], 4 * i);
    areas[i] = path.area(f);
  });
  const mesh = (filter) => path(topojson.mesh(topo, obj, filter)) ?? "";
  const regions = [...new Set(features.map(regionOf))];
  const regionOutlines = Object.fromEntries(regions.map((r) => [r, mesh((a, b) => (a === b ? regionOf(a) === r : (regionOf(a) === r) !== (regionOf(b) === r)))]));
  const result = {
    ids, names: features.map((f) => f.properties.name ?? null), paths, isPoint, centres, bounds, areas,
    unitMesh: mesh((a, b) => a !== b), regionMesh: mesh((a, b) => a !== b && regionOf(a) !== regionOf(b)), outline: mesh((a, b) => a === b),
    regionOutlines,
  };
  // keep what drawing needs
  Object.assign(R, {
    n, isPoint: isPoint.slice(), centres: centres.slice(), paths: paths.map((p, i) => (isPoint[i] ? null : new Path2D(p))),
    unitMesh: new Path2D(result.unitMesh), regionMesh: new Path2D(result.regionMesh), outline: new Path2D(result.outline),
    regionOutlineStr: regionOutlines, regionOutlines: new Map(), regionOf: ids.map((id) => id.split("-")[1]),
    radius: new Float32Array(n).fill(3), pointOrder: Array.from({ length: n }, (_, i) => i).filter((i) => isPoint[i]),
  });
  return { result, transfer: [isPoint.buffer, centres.buffer, bounds.buffer, areas.buffer] };
}

/* ---------------------------------------------------------------- map frames */

// One or more units files (several when an overview merges one race per region). Frames are
// aligned on the union of their times; each file contributes its latest frame at or before each time.
export async function units({ urls, live, countTimeline }) {
  const files = await Promise.all(urls.map((u) => json(u, live)));
  const ok = files.map((f, s) => [f, s]).filter(([f]) => f?.unit_ids?.length);
  if (!ok.length) { R.units = null; schedule(); return { result: null, transfer: [] }; }
  const times = [...new Set(ok.flatMap(([f]) => (f.frames ?? []).map((fr) => Date.parse(fr.t))))].sort((a, b) => a - b);
  const n = ok.reduce((s, [f]) => s + f.unit_ids.length, 0);
  const K = times.length;
  const unit_ids = [], region = [], names = [];
  const source = new Uint8Array(n), prior = new Int16Array(n).fill(-1), V0 = new Float64Array(n);
  const F = new Int16Array(K * n), A = new Int16Array(K * n).fill(-1), P = new Int16Array(K * n).fill(-1);
  const hasWinner = ok.some(([f]) => f.winner || f.frames?.[0]?.winner);
  const perFrame = hasWinner && ok.some(([f]) => f.frames?.[0]?.winner);
  const W = perFrame ? new Array(K * n).fill(null) : null, M = perFrame ? new Int16Array(K * n) : null;
  const top = hasWinner && !perFrame ? { winner: new Array(n).fill(null), margin: new Int16Array(n) } : null;
  let off = 0;
  for (const [f, s] of ok) {
    const m = f.unit_ids.length;
    for (let j = 0; j < m; j++) {
      unit_ids.push(f.unit_ids[j]); region.push(f.region?.[j] ?? null); names.push(f.names?.[j] ?? null);
      source[off + j] = s;
      prior[off + j] = f.prior?.[j] ?? -1;
      V0[off + j] = f.V0?.[j] ?? 0;
      if (top) { top.winner[off + j] = f.winner?.[j] ?? null; top.margin[off + j] = f.margin?.[j] ?? 0; }
    }
    const ft = (f.frames ?? []).map((fr) => Date.parse(fr.t));
    let fk = -1;
    for (let k = 0; k < K; k++) {
      while (fk + 1 < ft.length && ft[fk + 1] <= times[k]) fk++;
      if (fk < 0) continue;
      const fr = f.frames[fk], base = k * n + off;
      for (let j = 0; j < m; j++) {
        F[base + j] = fr.f?.[j] ?? 0;
        A[base + j] = fr.a?.[j] ?? -1;
        P[base + j] = fr.proj?.[j] ?? -1;
        if (W && fr.winner) { W[base + j] = fr.winner[j]; M[base + j] = fr.margin?.[j] ?? 0; }
      }
    }
    off += m;
  }
  // per-mode values, normalised to [-1, 1] (diverging) or [0, 1] (counted); NaN = no data
  const counted = new Float32Array(K * n), swing = new Float32Array(K * n), margin = new Float32Array(K * n);
  const clamp = (v) => Math.max(-1, Math.min(1, v));
  for (let k = 0; k < K; k++) {
    for (let j = 0; j < n; j++) {
      const i = k * n + j, a = A[i], p = prior[j];
      counted[i] = F[i] / 1000;
      swing[i] = a >= 0 && p >= 0 ? clamp((a - p) / 1000 / SCALE.swing) : NaN;
      const v = countTimeline ? P[i] : a >= 0 ? a : P[i];
      margin[i] = v >= 0 ? clamp((2 * v / 1000 - 1) / SCALE.margin) : NaN;
    }
  }
  const priorVals = new Float32Array(n);
  for (let j = 0; j < n; j++) priorVals[j] = prior[j] >= 0 ? clamp((2 * prior[j] / 1000 - 1) / SCALE.prior) : NaN;
  const first = ok[0][0];
  const winner = !hasWinner ? null : W ? { perFrame: W, margin: M } : { top: top.winner, margin: top.margin };
  const result = {
    n, K, unit_ids, region, names, source, prior, V0, times: Float64Array.from(times), F, A, P, winner,
    race_id: ok.length === 1 ? first.race_id ?? null : null,
    a: ok.length === 1 ? first.a ?? null : null, b: ok.length === 1 ? first.b ?? null : null, label: first.label ?? null,
  };
  // the drawing side keeps its own values; the page gets copies of the arrays it reads
  R.units = { n, K, times: result.times, winner, vals: { counted, swing, margin, prior: priorVals } };
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
    case "state": Object.assign(R, msg.state); break;
    case "bind": {
      // per-unit colour tables from the races' two colours; party colours for "winner"
      const luts = msg.raceColors.map(([a, b]) => lutFor(a, b));
      R.unitLUT = Array.from(msg.unitRace, (r) => luts[r]);
      R.featIdx = msg.featIdx;
      R.parties = new Map((msg.parties ?? []).map((p) => [p.id, p.color]));
      R.hatch = new Set(msg.hatch ?? []);
      R.partyLUTs.clear();
      R.countTimeline = msg.countTimeline;
      if (msg.radius) R.radius = msg.radius;
      if (msg.pointOrder) R.pointOrder = msg.pointOrder;
      R.cache.clear();
      break;
    }
    case "paint": Object.assign(R, { mode: msg.mode, t: msg.t, tween: msg.tween }); break;
    default: return;
  }
  schedule();
}

// Paint right after the current task (a microtask): no timers and no animation frames, which
// headless and hidden pages throttle or never run. Changes within one task share one paint.
function schedule() {
  if (R.scheduled) return;
  R.scheduled = true;
  queueMicrotask(() => { R.scheduled = false; draw(); });
}

function frameIndex(t) {
  const ft = R.units?.times;
  if (!ft?.length) return -1;
  return Math.max(0, d3.bisectRight(ft, t) - 1);
}

function colours() {
  const U = R.units, idx = R.featIdx, nf = R.n;
  if (!U || !idx || idx.length !== nf) return null;
  const k = frameIndex(R.t);
  const tween = R.tween && R.mode !== "winner" && k >= 0 && k + 1 < U.K;
  const key = `${R.mode}|${k}`;
  if (!tween && R.cache.has(key)) return R.cache.get(key);
  const out = tween ? (R.buf && R.buf.length === nf ? R.buf : (R.buf = new Array(nf))) : new Array(nf);
  const n = U.n, luts = R.unitLUT;
  if (R.mode === "winner" && U.winner) {
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
      out[i] = Number.isNaN(v) ? NONE : luts[j][Math.round((v + 1) * 100)];
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
      out[i] = counted ? (v > 0 ? COUNTED_LUT[Math.min(100, Math.round(v * 100))] : NONE) : Number.isNaN(v) ? NONE : luts[j][Math.round((v + 1) * 100)];
    }
  }
  if (!tween) R.cache.set(key, out);
  return out;
}

function regionOutline(r) {
  if (!R.regionOutlines.has(r)) R.regionOutlines.set(r, new Path2D(R.regionOutlineStr[r] ?? ""));
  return R.regionOutlines.get(r);
}

// Light diagonal hatch (regions without a baseline), 6 CSS px apart at any zoom.
function hatchPattern(ctx, px) {
  if (!R.hatchTile) {
    const c = new OffscreenCanvas(6, 6), g = c.getContext("2d");
    g.fillStyle = NEUTRAL;
    g.fillRect(0, 0, 6, 6);
    g.strokeStyle = "#c9c6bd";
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-1, 1); g.lineTo(1, -1); g.moveTo(5, 7); g.lineTo(7, 5); g.stroke();
    R.hatchTile = c;
  }
  const pat = ctx.createPattern(R.hatchTile, "repeat");
  pat.setTransform(new DOMMatrix().scaleSelf(px, px));
  return pat;
}

function draw() {
  const ctx = R.ctx;
  if (!ctx || !R.w || !R.n) return;
  const t0 = performance.now();
  const { k, x, y } = R.view;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, R.canvas.width, R.canvas.height);
  ctx.setTransform(R.dpr * k, 0, 0, R.dpr * k, R.dpr * x, R.dpr * y);
  const px = 1 / k;
  const cols = colours(), fade = R.fade, paths = R.paths;
  let alpha = 1;
  const hatch = R.hatch?.size ? hatchPattern(ctx, px) : null;
  for (let i = 0; i < R.n; i++) {
    const p = paths[i];
    if (!p) continue;
    const a = fade && !fade[i] ? 0.3 : 1;
    if (a !== alpha) { ctx.globalAlpha = a; alpha = a; }
    ctx.fillStyle = hatch && R.hatch.has(R.regionOf[i]) ? hatch : cols ? cols[i] : NONE;
    ctx.fill(p);
  }
  ctx.globalAlpha = 1;
  ctx.strokeStyle = "#fcfbf8";
  ctx.lineWidth = Math.min(0.6, 0.12 * k) * px;
  ctx.stroke(R.unitMesh);
  if (hatch) {
    // county lines inside hatched regions, where the paper-coloured mesh would not show
    ctx.strokeStyle = "#bdb9ae";
    ctx.lineWidth = 0.6 * px;
    for (let i = 0; i < R.n; i++) if (paths[i] && R.hatch.has(R.regionOf[i])) ctx.stroke(paths[i]);
  }
  ctx.strokeStyle = "rgba(27,27,27,0.6)";
  ctx.lineWidth = 0.8 * px;
  ctx.stroke(R.regionMesh);
  ctx.strokeStyle = "#8d8a82";
  ctx.lineWidth = 0.7 * px;
  ctx.stroke(R.outline);
  if (R.pointOrder.length) {
    ctx.strokeStyle = "#fcfbf8";
    ctx.lineWidth = 0.6 * px;
    for (const i of R.pointOrder) {
      if (!Number.isFinite(R.centres[2 * i])) continue;
      ctx.globalAlpha = fade && !fade[i] ? 0.3 : 1;
      ctx.beginPath();
      ctx.arc(R.centres[2 * i], R.centres[2 * i + 1], R.radius[i] * px, 0, 2 * Math.PI);
      ctx.fillStyle = cols ? cols[i] : NONE;
      ctx.fill();
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  if (R.hoverRegion && R.hoverRegion !== R.focus) {
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 2.2 * px;
    ctx.setLineDash([4 * px, 3 * px]);
    ctx.stroke(regionOutline(R.hoverRegion));
    ctx.setLineDash([]);
  }
  if (R.focus) {
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.6 * px;
    ctx.stroke(regionOutline(R.focus));
  }
  for (const [i, w] of [[R.pinned, 2.2], [R.hover, 1.5]]) {
    if (i < 0) continue;
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = w * px;
    if (paths[i]) ctx.stroke(paths[i]);
    else if (Number.isFinite(R.centres[2 * i])) { ctx.beginPath(); ctx.arc(R.centres[2 * i], R.centres[2 * i + 1], (R.radius[i] + 1) * px, 0, 2 * Math.PI); ctx.stroke(); }
  }
  R.drawMs.push(performance.now() - t0);
  if (cols && !R.painted++) emit({ type: "painted", at: performance.timeOrigin + performance.now() });
  if (R.drawMs.length >= 30) { emit({ type: "drawStats", ms: R.drawMs }); R.drawMs = []; }
}
