// Canvas map for the election dashboard. Polygons are projected once into a fixed base space
// (one Path2D per unit) and zoom is a canvas transform, so playing a night only refills paths.
// Point geometry (countries without open polygon boundaries) is drawn as dots sized by votes.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import * as topojson from "https://cdn.jsdelivr.net/npm/topojson-client@3/+esm";

const BASE = 1000; // base projection extent
const PAD = 14; // screen padding when fitting (CSS px)
const NONE = "#efede7";

export function createMap(container, { topo, objectName, unitKey, isUS, onHover, onClick }) {
  const obj = topo.objects[objectName];
  const features = topojson.feature(topo, obj).features;
  const regionOf = (f) => String(f.properties[unitKey]).split("-")[1];
  const projection = (isUS ? d3.geoAlbersUsa() : d3.geoMercator()).fitSize([BASE, BASE], { type: "FeatureCollection", features });
  const geo = d3.geoPath(projection);
  const isPoint = features.map((f) => /Point$/.test(f.geometry?.type ?? ""));
  const centre = features.map((f, i) => {
    if (!isPoint[i]) return null;
    const c = f.geometry.type === "Point" ? f.geometry.coordinates : f.geometry.coordinates[0];
    return projection(c);
  });
  const paths = features.map((f, i) => (isPoint[i] ? null : new Path2D(geo(f) ?? "")));
  const bounds = features.map((f, i) => (isPoint[i] && centre[i] ? [centre[i], centre[i]] : geo.bounds(f)));
  const mesh = (filter) => new Path2D(geo(topojson.mesh(topo, obj, filter)) ?? "");
  const unitMesh = mesh((a, b) => a !== b);
  const regionMesh = mesh((a, b) => a !== b && regionOf(a) !== regionOf(b));
  const outline = mesh((a, b) => a === b);
  const regionOutlines = new Map();
  const regionOutline = (r) => {
    if (!regionOutlines.has(r)) regionOutlines.set(r, mesh((a, b) => (a === b ? regionOf(a) === r : (regionOf(a) === r) !== (regionOf(b) === r))));
    return regionOutlines.get(r);
  };

  const canvas = document.createElement("canvas");
  canvas.className = "map-canvas";
  container.prepend(canvas);
  const ctx = canvas.getContext("2d");
  const hit = document.createElement("canvas").getContext("2d"); // identity transform for isPointInPath

  const st = {
    w: 0, h: 0, dpr: 1, view: { k: 1, x: 0, y: 0 }, anim: 0,
    colours: null, inUnits: null, regionIdx: null, focus: null, fade: null, hover: -1, pinned: -1,
    radius: features.map(() => 3), // CSS px, for point features
    pointOrder: features.map((_, i) => i).filter((i) => isPoint[i]),
  };

  function fitView(idx) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of idx) {
      const b = bounds[i];
      if (!b || !Number.isFinite(b[0][0])) continue;
      x0 = Math.min(x0, b[0][0]); y0 = Math.min(y0, b[0][1]); x1 = Math.max(x1, b[1][0]); y1 = Math.max(y1, b[1][1]);
    }
    if (!Number.isFinite(x0)) { x0 = 0; y0 = 0; x1 = BASE; y1 = BASE; }
    const k = Math.min((st.w - 2 * PAD) / Math.max(x1 - x0, 1), (st.h - 2 * PAD) / Math.max(y1 - y0, 1));
    return { k, x: (st.w - k * (x0 + x1)) / 2, y: (st.h - k * (y0 + y1)) / 2 };
  }

  function draw() {
    const t0 = performance.now();
    const { k, x, y } = st.view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(st.dpr * k, 0, 0, st.dpr * k, st.dpr * x, st.dpr * y);
    const px = 1 / k; // one CSS pixel in base units
    const cols = st.colours, fade = st.fade;
    for (let i = 0; i < paths.length; i++) {
      if (!paths[i]) continue;
      ctx.globalAlpha = fade && !fade[i] ? 0.3 : 1;
      ctx.fillStyle = cols ? cols[i] : NONE;
      ctx.fill(paths[i]);
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#fcfbf8";
    ctx.lineWidth = Math.min(0.6, 0.12 * k) * px;
    ctx.stroke(unitMesh);
    ctx.strokeStyle = "rgba(27,27,27,0.6)";
    ctx.lineWidth = 0.8 * px;
    ctx.stroke(regionMesh);
    ctx.strokeStyle = "#8d8a82";
    ctx.lineWidth = 0.7 * px;
    ctx.stroke(outline);
    if (st.pointOrder.length) {
      ctx.strokeStyle = "#fcfbf8";
      ctx.lineWidth = 0.6 * px;
      for (const i of st.pointOrder) {
        const c = centre[i];
        if (!c) continue;
        ctx.globalAlpha = fade && !fade[i] ? 0.3 : 1;
        ctx.beginPath();
        ctx.arc(c[0], c[1], st.radius[i] * px, 0, 2 * Math.PI);
        ctx.fillStyle = cols ? cols[i] : NONE;
        ctx.fill();
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    if (st.focus) {
      ctx.strokeStyle = "#1b1b1b";
      ctx.lineWidth = 1.6 * px;
      ctx.stroke(regionOutline(st.focus));
    }
    for (const [i, w] of [[st.pinned, 2.2], [st.hover, 1.5]]) {
      if (i < 0) continue;
      ctx.strokeStyle = "#1b1b1b";
      ctx.lineWidth = w * px;
      if (paths[i]) ctx.stroke(paths[i]);
      else if (centre[i]) { ctx.beginPath(); ctx.arc(centre[i][0], centre[i][1], (st.radius[i] + 1) * px, 0, 2 * Math.PI); ctx.stroke(); }
    }
    api.lastDrawMs = performance.now() - t0;
  }

  function currentFitIdx() {
    if (st.focus && st.regionIdx?.has(st.focus)) return st.regionIdx.get(st.focus);
    return st.inUnits?.length ? st.inUnits : features.map((_, i) => i);
  }

  function resize() {
    const r = container.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    st.w = r.width;
    st.h = r.height;
    st.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(st.w * st.dpr);
    canvas.height = Math.round(st.h * st.dpr);
    canvas.style.width = `${st.w}px`;
    canvas.style.height = `${st.h}px`;
    cancelAnimationFrame(st.anim);
    st.view = fitView(currentFitIdx());
    draw();
  }

  function animateTo(view) {
    cancelAnimationFrame(st.anim);
    const from = st.view, t0 = performance.now(), dur = 650;
    const step = (now) => {
      const u = d3.easeCubicInOut(Math.min(1, (now - t0) / dur));
      const k = Math.exp(Math.log(from.k) + (Math.log(view.k) - Math.log(from.k)) * u);
      st.view = { k, x: from.x + (view.x - from.x) * u, y: from.y + (view.y - from.y) * u };
      draw();
      if (u < 1) st.anim = requestAnimationFrame(step);
    };
    st.anim = requestAnimationFrame(step);
  }

  function pick(ev) {
    const r = canvas.getBoundingClientRect();
    const sx = ev.clientX - r.left, sy = ev.clientY - r.top;
    const bx = (sx - st.view.x) / st.view.k, by = (sy - st.view.y) / st.view.k;
    // dots first (they sit on top), smallest first so small places stay reachable
    for (let j = st.pointOrder.length - 1; j >= 0; j--) {
      const i = st.pointOrder[j], c = centre[i];
      if (c && Math.hypot(c[0] * st.view.k + st.view.x - sx, c[1] * st.view.k + st.view.y - sy) <= st.radius[i] + 2) return i;
    }
    for (let i = paths.length - 1; i >= 0; i--) {
      if (!paths[i]) continue;
      const b = bounds[i];
      if (bx < b[0][0] || bx > b[1][0] || by < b[0][1] || by > b[1][1]) continue;
      if (hit.isPointInPath(paths[i], bx, by)) return i;
    }
    return -1;
  }

  canvas.addEventListener("pointermove", (ev) => {
    const i = pick(ev);
    if (i !== st.hover) { st.hover = i; draw(); }
    canvas.style.cursor = i >= 0 ? "pointer" : "default";
    onHover?.(i, ev);
  });
  canvas.addEventListener("pointerleave", () => {
    if (st.hover !== -1) { st.hover = -1; draw(); }
    onHover?.(-1, null);
  });
  canvas.addEventListener("click", (ev) => onClick?.(pick(ev), ev));

  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  const api = {
    features,
    hasPoints: st.pointOrder.length > 0,
    lastDrawMs: 0,
    regionOf: (i) => regionOf(features[i]),
    // index[i] = position of feature i in units.json, or -1; votes[i] sizes the dots
    setUnits(index, votes) {
      st.inUnits = [];
      st.regionIdx = new Map();
      index.forEach((u, i) => {
        if (u < 0) return;
        st.inUnits.push(i);
        const r = regionOf(features[i]);
        if (!st.regionIdx.has(r)) st.regionIdx.set(r, []);
        st.regionIdx.get(r).push(i);
      });
      if (st.pointOrder.length) {
        const max = d3.max(st.pointOrder, (i) => (index[i] >= 0 ? votes?.[index[i]] ?? 0 : 0)) || 1;
        const r = d3.scaleSqrt().domain([0, max]).range([1.5, 16]).clamp(true);
        st.pointOrder.forEach((i) => { st.radius[i] = index[i] >= 0 ? r(Math.max(0, votes?.[index[i]] ?? 0)) : 1.5; });
        st.pointOrder.sort((a, b) => st.radius[b] - st.radius[a]); // big dots under small ones
      }
    },
    setColours(cols) { st.colours = cols; draw(); },
    focus(region, animate = true) {
      st.focus = region && st.regionIdx?.has(region) ? region : null;
      st.fade = st.focus ? features.map((f) => regionOf(f) === st.focus) : null;
      if (!st.w) return; // the first resize fits the view
      const view = fitView(currentFitIdx());
      if (animate) animateTo(view); else { st.view = view; draw(); }
    },
    pin(i) { st.pinned = i; draw(); },
    resize,
  };
  return api;
}
