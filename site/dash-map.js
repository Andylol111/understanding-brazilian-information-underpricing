// Map for the election dashboard. Painting happens in dash-engine.js (in a worker on an
// OffscreenCanvas, or on the page with this canvas); this side owns the view (fit, smooth zoom),
// hover hit-testing (a grid index over unit bounds, Path2D built only for the units under the
// pointer) and the interaction state it forwards to the engine.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";

const PAD = 14; // screen padding when fitting (CSS px)
const GRID = 48; // hit-test grid cells per side over the base space

// geo: output of the engine's "geo" job; post(msg, transfer): send a control message to the engine;
// offscreen: hand the canvas to a worker (else the engine draws on it in place).
// populatedFirst: open fitted to the units below the 90th percentile of area (e.g. Canada's south).
export function createMap(container, { geo, post, offscreen = true, populatedFirst = false, onHover, onClick }) {
  const n = geo.ids.length;
  const ids = geo.ids;
  const regionOf = (i) => ids[i].split("-")[1];
  const isPoint = geo.isPoint, centres = geo.centres, bounds = geo.bounds, areas = geo.areas;
  const hitPaths = new Map(); // i -> Path2D, built on first hover
  const hitPath = (i) => {
    if (!hitPaths.has(i)) hitPaths.set(i, new Path2D(geo.paths[i]));
    return hitPaths.get(i);
  };

  // hit-test grid over the base-space extent
  let gx0 = Infinity, gy0 = Infinity, gx1 = -Infinity, gy1 = -Infinity;
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(bounds[4 * i])) continue;
    gx0 = Math.min(gx0, bounds[4 * i]); gy0 = Math.min(gy0, bounds[4 * i + 1]); gx1 = Math.max(gx1, bounds[4 * i + 2]); gy1 = Math.max(gy1, bounds[4 * i + 3]);
  }
  const cw = (gx1 - gx0) / GRID || 1, ch = (gy1 - gy0) / GRID || 1;
  const cells = Array.from({ length: GRID * GRID }, () => []);
  const cellOf = (v, v0, s) => Math.max(0, Math.min(GRID - 1, Math.floor((v - v0) / s)));
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(bounds[4 * i])) continue;
    const pad = isPoint[i] ? 20 : 0; // dots extend past their point
    for (let cx = cellOf(bounds[4 * i] - pad, gx0, cw); cx <= cellOf(bounds[4 * i + 2] + pad, gx0, cw); cx++) {
      for (let cy = cellOf(bounds[4 * i + 1] - pad, gy0, ch); cy <= cellOf(bounds[4 * i + 3] + pad, gy0, ch); cy++) cells[cy * GRID + cx].push(i);
    }
  }
  const hit = document.createElement("canvas").getContext("2d"); // identity transform for isPointInPath

  const canvas = document.createElement("canvas");
  canvas.className = "map-canvas";
  container.prepend(canvas);
  if (offscreen) {
    const off = canvas.transferControlToOffscreen();
    post({ type: "canvas", canvas: off }, [off]);
  } else post({ type: "canvas", canvas });

  const st = {
    w: 0, h: 0, dpr: 1, view: { k: 1, x: 0, y: 0 }, anim: 0,
    inUnits: null, regionIdx: null, focus: null, hover: -1, pinned: -1, hoverRegion: null,
    radius: new Float32Array(n).fill(3),
    extent: populatedFirst ? "populated" : "all", populated: null,
  };
  const setView = (v) => { st.view = v; post({ type: "view", view: v }); };

  function fitView(idx) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of idx) {
      if (!Number.isFinite(bounds[4 * i])) continue;
      x0 = Math.min(x0, bounds[4 * i]); y0 = Math.min(y0, bounds[4 * i + 1]); x1 = Math.max(x1, bounds[4 * i + 2]); y1 = Math.max(y1, bounds[4 * i + 3]);
    }
    if (!Number.isFinite(x0)) { x0 = gx0; y0 = gy0; x1 = gx1; y1 = gy1; }
    const k = Math.min((st.w - 2 * PAD) / Math.max(x1 - x0, 1), (st.h - 2 * PAD) / Math.max(y1 - y0, 1));
    return { k, x: (st.w - k * (x0 + x1)) / 2, y: (st.h - k * (y0 + y1)) / 2 };
  }

  function currentFitIdx() {
    if (st.focus && st.regionIdx?.has(st.focus)) return st.regionIdx.get(st.focus);
    const all = st.inUnits?.length ? st.inUnits : Array.from({ length: n }, (_, i) => i);
    return st.extent === "populated" && st.populated?.length ? st.populated : all;
  }

  function resize() {
    const r = container.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    st.w = r.width;
    st.h = r.height;
    st.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.style.width = `${st.w}px`;
    canvas.style.height = `${st.h}px`;
    post({ type: "size", w: st.w, h: st.h, dpr: st.dpr });
    cancelAnimationFrame(st.anim);
    setView(fitView(currentFitIdx()));
  }

  // Smooth zoom along the van Wijk & Nuij path (d3.interpolateZoom), in base-space centre/width.
  function animateTo(view) {
    cancelAnimationFrame(st.anim);
    if (document.hidden) { setView(view); return; } // no animation frames in a hidden page
    const toZ = (v) => [(st.w / 2 - v.x) / v.k, (st.h / 2 - v.y) / v.k, st.w / v.k];
    const interp = d3.interpolateZoom(toZ(st.view), toZ(view));
    const dur = Math.max(450, Math.min(1100, interp.duration * 0.6));
    const t0 = performance.now();
    const step = (now) => {
      const u = d3.easeCubicInOut(Math.min(1, (now - t0) / dur));
      const [cx, cy, w] = interp(u);
      const k = st.w / w;
      setView({ k, x: st.w / 2 - cx * k, y: st.h / 2 - cy * k });
      if (u < 1) st.anim = requestAnimationFrame(step);
    };
    st.anim = requestAnimationFrame(step);
  }

  function pick(ev) {
    const r = canvas.getBoundingClientRect();
    const sx = ev.clientX - r.left, sy = ev.clientY - r.top;
    const bx = (sx - st.view.x) / st.view.k, by = (sy - st.view.y) / st.view.k;
    if (bx < gx0 - 20 || bx > gx1 + 20 || by < gy0 - 20 || by > gy1 + 20) return -1;
    const cand = cells[cellOf(by, gy0, ch) * GRID + cellOf(bx, gx0, cw)];
    let best = -1, bestR = Infinity;
    for (const i of cand) { // dots: the smallest dot under the pointer wins
      if (!isPoint[i]) continue;
      const d = Math.hypot(centres[2 * i] * st.view.k + st.view.x - sx, centres[2 * i + 1] * st.view.k + st.view.y - sy);
      if (d <= st.radius[i] + 2 && st.radius[i] < bestR) { best = i; bestR = st.radius[i]; }
    }
    if (best >= 0) return best;
    for (let j = cand.length - 1; j >= 0; j--) {
      const i = cand[j];
      if (isPoint[i]) continue;
      if (bx < bounds[4 * i] || bx > bounds[4 * i + 2] || by < bounds[4 * i + 1] || by > bounds[4 * i + 3]) continue;
      if (hit.isPointInPath(hitPath(i), bx, by)) return i;
    }
    return -1;
  }

  canvas.addEventListener("pointermove", (ev) => {
    const i = pick(ev);
    if (i !== st.hover) { st.hover = i; post({ type: "state", state: { hover: i } }); }
    canvas.style.cursor = i >= 0 ? "pointer" : "default";
    onHover?.(i, ev);
  });
  canvas.addEventListener("pointerleave", () => {
    if (st.hover !== -1) { st.hover = -1; post({ type: "state", state: { hover: -1 } }); }
    onHover?.(-1, null);
  });
  canvas.addEventListener("click", (ev) => onClick?.(pick(ev), ev));

  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  return {
    n,
    ids,
    names: geo.names,
    regionOf,
    // index[i] = position of feature i in the units arrays, or -1; votes sizes the dots;
    // colouring: { unitRace: per-unit index into raceColors, raceColors: [[a, b]], parties, countTimeline }
    setUnits(index, votes, colouring) {
      st.inUnits = [];
      st.regionIdx = new Map();
      index.forEach((u, i) => {
        if (u < 0) return;
        st.inUnits.push(i);
        const r = regionOf(i);
        if (!st.regionIdx.has(r)) st.regionIdx.set(r, []);
        st.regionIdx.get(r).push(i);
      });
      let pointOrder = null;
      const pts = Array.from({ length: n }, (_, i) => i).filter((i) => isPoint[i]);
      if (pts.length) {
        const max = d3.max(pts, (i) => (index[i] >= 0 ? votes?.[index[i]] ?? 0 : 0)) || 1;
        const r = d3.scaleSqrt().domain([0, max]).range([1.5, 16]).clamp(true);
        pts.forEach((i) => { st.radius[i] = index[i] >= 0 ? r(Math.max(0, votes?.[index[i]] ?? 0)) : 1.5; });
        pointOrder = pts.sort((a, b) => st.radius[b] - st.radius[a]); // big dots under small ones
      }
      // "populated" view: units up to the 90th percentile of projected area
      const inA = st.inUnits.filter((i) => !isPoint[i]).map((i) => areas[i]).sort(d3.ascending);
      const p90 = d3.quantileSorted(inA, 0.9);
      st.populated = p90 != null ? st.inUnits.filter((i) => isPoint[i] || areas[i] <= p90) : null;
      post({ type: "bind", featIdx: index, radius: st.radius, pointOrder, ...colouring });
    },
    paint(mode, t, tween) { post({ type: "paint", mode, t, tween }); },
    focus(region, animate = true) {
      st.focus = region && st.regionIdx?.has(region) ? region : null;
      post({ type: "state", state: { focus: st.focus, fade: st.focus ? Uint8Array.from({ length: n }, (_, i) => regionOf(i) === st.focus) : null } });
      if (!st.w) return; // the first resize fits the view
      const view = fitView(currentFitIdx());
      if (animate) animateTo(view); else setView(view);
    },
    highlightRegion(r) {
      if (r === st.hoverRegion) return;
      st.hoverRegion = r;
      post({ type: "state", state: { hoverRegion: r } });
    },
    pin(i) { st.pinned = i; post({ type: "state", state: { pinned: i } }); },
    get extent() { return st.extent; },
    hasPopulatedView: populatedFirst,
    setExtent(ext) {
      st.extent = ext;
      if (st.w) animateTo(fitView(currentFitIdx()));
    },
    resize,
  };
}
