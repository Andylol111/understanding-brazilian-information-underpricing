// Map for the election dashboard. Painting happens in dash-engine.js (in a worker on an
// OffscreenCanvas, or on the page with this canvas); this side owns the view, the transitions
// (zoom, dimming, the state layer's fade into units), hover hit-testing (a grid index over unit
// bounds, Path2D built only for the units under the pointer) and a second, page-side canvas for
// the hover / pin / region outlines, so pointer movement never repaints the map itself.
import * as d3 from "./vendor/d3.js";

const PAD = 14; // screen padding when fitting (CSS px)
const GRID = 48; // hit-test grid cells per side over the base space
const ZOOM_MS = [600, 750]; // transition length range
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// geo: output of the engine's "geo" job; post(msg, transfer): send a control message to the engine;
// offscreen: hand the canvas to a worker (else the engine draws on it in place).
// populatedFirst: open fitted to the units below the 90th percentile of area (e.g. Canada's south).
// onFrame(ms): called with each transition frame's interval (for measurements).
export function createMap(container, { geo, post, offscreen = true, populatedFirst = false, onHover, onClick, onFrame }) {
  const n = geo.ids.length;
  const ids = geo.ids;
  const regionOf = (i) => ids[i].split("-")[1];
  const isPoint = geo.isPoint, centres = geo.centres, bounds = geo.bounds, areas = geo.areas;
  const hitPaths = new Map(); // i -> Path2D, built on first hover
  const hitPath = (i) => {
    if (!hitPaths.has(i)) hitPaths.set(i, new Path2D(geo.paths[i]));
    return hitPaths.get(i);
  };
  const outlines = new Map(); // region -> Path2D of its outline
  const outlineOf = (r) => {
    if (!outlines.has(r)) outlines.set(r, new Path2D(geo.regionOutlines?.[r] ?? ""));
    return outlines.get(r);
  };
  // every feature of each region, from the geometry (a region can be zoomed to before its units load)
  const regionAll = new Map();
  for (let i = 0; i < n; i++) {
    const r = regionOf(i);
    if (!regionAll.has(r)) regionAll.set(r, []);
    regionAll.get(r).push(i);
  }

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
  const overlay = document.createElement("canvas"); // outlines, drawn here on the page
  overlay.className = "map-overlay";
  overlay.setAttribute("aria-hidden", "true");
  container.prepend(canvas, overlay);
  const octx = overlay.getContext("2d");
  if (offscreen) {
    const off = canvas.transferControlToOffscreen();
    post({ type: "canvas", canvas: off }, [off]);
  } else post({ type: "canvas", canvas });

  const st = {
    w: 0, h: 0, dpr: 1, view: { k: 1, x: 0, y: 0 }, anim: 0, fadeMix: 0, natMix: 0,
    inUnits: null, focus: null, focusToken: 0, hover: -1, pinned: -1, hoverRegion: null, nat: false, natHover: false,
    radius: new Float32Array(n).fill(3),
    extent: populatedFirst ? "populated" : "all", populated: null,
  };
  const frame = () => {
    post({ type: "frame", view: st.view, fadeMix: st.fadeMix, natMix: st.natMix });
    drawOverlay();
  };

  function drawOverlay() {
    const { k, x, y } = st.view;
    octx.setTransform(1, 0, 0, 1, 0, 0);
    octx.clearRect(0, 0, overlay.width, overlay.height);
    octx.setTransform(st.dpr * k, 0, 0, st.dpr * k, st.dpr * x, st.dpr * y);
    const px = 1 / k;
    octx.strokeStyle = "#1b1b1b";
    const hoverR = st.natHover && st.hover >= 0 ? regionOf(st.hover) : st.hoverRegion;
    if (hoverR && hoverR !== st.focus) {
      octx.lineWidth = (st.natHover ? 1.8 : 2.2) * px;
      if (!st.natHover) octx.setLineDash([4 * px, 3 * px]);
      octx.stroke(outlineOf(hoverR));
      octx.setLineDash([]);
    }
    if (st.focus) {
      octx.globalAlpha = st.fadeMix;
      octx.lineWidth = 1.6 * px;
      octx.stroke(outlineOf(st.focus));
      octx.globalAlpha = 1;
    }
    for (const [i, w] of [[st.pinned, 2.2], [st.natHover ? -1 : st.hover, 1.5]]) {
      if (i < 0) continue;
      octx.lineWidth = w * px;
      if (!isPoint[i]) octx.stroke(hitPath(i));
      else if (Number.isFinite(centres[2 * i])) { octx.beginPath(); octx.arc(centres[2 * i], centres[2 * i + 1], (st.radius[i] + 1) * px, 0, 2 * Math.PI); octx.stroke(); }
    }
  }

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
    if (st.focus && regionAll.has(st.focus)) return regionAll.get(st.focus);
    const all = !st.nat && st.inUnits?.length ? st.inUnits : Array.from({ length: n }, (_, i) => i);
    return st.extent === "populated" && st.populated?.length ? st.populated : all;
  }

  function resize() {
    const r = container.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (r.width === st.w && r.height === st.h && dpr === st.dpr) return;
    st.w = r.width;
    st.h = r.height;
    st.dpr = dpr;
    for (const c of [canvas, overlay]) { c.style.width = `${st.w}px`; c.style.height = `${st.h}px`; }
    overlay.width = Math.round(st.w * st.dpr);
    overlay.height = Math.round(st.h * st.dpr);
    post({ type: "size", w: st.w, h: st.h, dpr: st.dpr });
    // a transition in progress keeps running and refits when it ends
    if (st.animating) { st.refit = true; return; }
    st.view = fitView(currentFitIdx());
    frame();
  }

  // work the page wants done once the current transition is over (it would cost frames during it)
  const after = [];
  const flushAfter = () => { while (after.length) setTimeout(after.shift(), 0); };

  // One choreographed transition: the view along the van Wijk & Nuij zoom path (d3.interpolateZoom,
  // 600-750 ms, cubic in-out), the other regions dimming with it and, with a state layer, the focus
  // region's units fading in over the last 40% of the zoom (out: over the first 40%). The fade in
  // waits for `ready` (units bound); if they arrive late it runs as soon as they do.
  function transition(view, { fade, nat, ready } = {}) {
    cancelAnimationFrame(st.anim);
    st.animating = false;
    let natReady = !ready;
    ready?.then(() => { natReady = true; }, () => {});
    if (document.hidden || reducedMotion() || !st.w) {
      st.view = view; st.fadeMix = fade; st.natMix = nat > st.natMix && !natReady ? st.natMix : nat;
      frame();
      flushAfter();
      if (!natReady && nat > st.natMix) ready.then(() => { st.natMix = nat; frame(); }, () => {});
      return;
    }
    const toZ = (v) => [(st.w / 2 - v.x) / v.k, (st.h / 2 - v.y) / v.k, st.w / v.k];
    const interp = d3.interpolateZoom(toZ(st.view), toZ(view));
    const dur = Math.max(ZOOM_MS[0], Math.min(ZOOM_MS[1], interp.duration * 0.6));
    const from = { fade: st.fadeMix, nat: st.natMix };
    // the clock starts on the third animation frame: the first two carry the style and layout of
    // whatever the page changed in the same task (crumbs, lists), which would otherwise show as a
    // stalled step; 33 ms of stillness reads as the click's response, a stall mid-zoom does not
    let t0 = null, last = 0, settle = 2, natFrom = null;
    st.animating = true;
    const step = (now) => {
      if (settle-- > 0) { st.anim = requestAnimationFrame(step); return; }
      if (t0 == null) { t0 = now; last = now; } else onFrame?.(now - last);
      last = now;
      const lin = Math.min(1, (now - t0) / dur), u = d3.easeCubicInOut(lin);
      const [cx, cy, w] = interp(u);
      const k = st.w / w;
      st.view = { k, x: st.w / 2 - cx * k, y: st.h / 2 - cy * k };
      st.fadeMix = from.fade + (fade - from.fade) * u;
      if (nat < from.nat) st.natMix = from.nat * (1 - Math.min(1, lin / 0.4));
      else if (nat > from.nat) {
        // fade in over 40% of the duration, from 60% of the zoom or when the units are bound
        if (natReady && natFrom == null) natFrom = Math.max(now, t0 + 0.6 * dur);
        st.natMix = natFrom == null || now < natFrom ? from.nat : from.nat + (nat - from.nat) * d3.easeCubicOut(Math.min(1, (now - natFrom) / (0.4 * dur)));
      }
      frame();
      const natDone = nat <= from.nat || st.natMix >= nat;
      if (lin < 1 || !natDone) { st.anim = requestAnimationFrame(step); return; }
      st.animating = false;
      if (st.refit) { st.refit = false; st.view = fitView(currentFitIdx()); frame(); }
      flushAfter();
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
      if (!isPoint[i] || st.hidden?.[i]) continue;
      const d = Math.hypot(centres[2 * i] * st.view.k + st.view.x - sx, centres[2 * i + 1] * st.view.k + st.view.y - sy);
      if (d <= st.radius[i] + 2 && st.radius[i] < bestR) { best = i; bestR = st.radius[i]; }
    }
    if (best >= 0) return best;
    for (let j = cand.length - 1; j >= 0; j--) {
      const i = cand[j];
      if (isPoint[i] || st.hidden?.[i]) continue;
      if (bx < bounds[4 * i] || bx > bounds[4 * i + 2] || by < bounds[4 * i + 1] || by > bounds[4 * i + 3]) continue;
      if (hit.isPointInPath(hitPath(i), bx, by)) return i;
    }
    return -1;
  }

  // pointer events reach the overlay (it sits on top)
  overlay.addEventListener("pointermove", (ev) => {
    const i = pick(ev);
    const changed = st.natHover ? (i >= 0 ? regionOf(i) : null) !== (st.hover >= 0 ? regionOf(st.hover) : null) : i !== st.hover;
    st.hover = i;
    if (changed) drawOverlay();
    overlay.style.cursor = i >= 0 ? "pointer" : "default";
    onHover?.(i, ev);
  });
  overlay.addEventListener("pointerleave", () => {
    if (st.hover !== -1) { st.hover = -1; drawOverlay(); }
    onHover?.(-1, null);
  });
  overlay.addEventListener("click", (ev) => onClick?.(pick(ev), ev));

  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  return {
    n,
    ids,
    names: geo.names,
    regionOf,
    hasRegion: (r) => regionAll.has(r),
    // Zoom to a region (null: the whole map). ready: a promise for the region's units being bound;
    // with a state layer their fade-in waits for it.
    focus(region, { animate = true, ready = null } = {}) {
      const next = region && regionAll.has(region) ? region : null;
      const token = ++st.focusToken;
      if (next !== st.focus) {
        // a new region's units start hidden under its state fill and fade in when bound
        if (next && st.nat && st.focus) st.natMix = 0;
        if (next) post({ type: "state", state: { focus: next } });
        st.focus = next;
        st.natHover = st.nat && !next;
      }
      if (!st.w) {
        // not laid out yet: the first resize fits the view; the mixes take their end values now
        st.fadeMix = next ? 1 : 0;
        st.natMix = st.nat && next && !ready ? 1 : 0;
        if (st.nat && next && ready) ready.then(() => { if (token === st.focusToken) { st.natMix = 1; frame(); } }, () => {});
        if (!next) post({ type: "state", state: { focus: null } });
        return;
      }
      const view = fitView(currentFitIdx());
      const target = { fade: next ? 1 : 0, nat: st.nat && next ? 1 : 0, ready };
      if (!next) {
        // zooming out: the old focus stays drawn (fading) until the transition ends
        const done = () => { if (token === st.focusToken) post({ type: "state", state: { focus: null } }); };
        if (animate) { transition(view, target); setTimeout(done, ZOOM_MS[1] + 50); } else { transition(view, { ...target, ready: null }); done(); }
        return;
      }
      if (animate) transition(view, target);
      else {
        cancelAnimationFrame(st.anim);
        st.view = view; st.fadeMix = 1;
        if (st.nat) {
          if (ready) ready.then(() => { if (token === st.focusToken) { st.natMix = 1; frame(); } }, () => {}); else st.natMix = 1;
        }
        frame();
      }
    },
    // index[i] = position of feature i in the units arrays, or -1; votes sizes the dots;
    // colouring: { unitRace: per-unit index into raceColors, raceColors: [[a, b]], parties, countTimeline }
    setUnits(index, votes, colouring) {
      st.inUnits = [];
      st.hidden = colouring?.hidden ?? null; // features of a layer not shown: not drawn, not hit
      index.forEach((u, i) => { if (u >= 0 && !st.hidden?.[i]) st.inUnits.push(i); });
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
    // A state layer under the units (national maps): colours per region, rings on modelled regions.
    // With it, hover outlines whole regions until one is focused.
    setNational(colours, rings = []) {
      st.nat = !!colours;
      st.natHover = st.nat && !st.focus;
      post({ type: "nat", colours: colours ? [...colours] : null, rings });
      drawOverlay();
    },
    setNatHover(on) { st.natHover = st.nat && on; drawOverlay(); },
    highlightRegion(r) {
      if (r === st.hoverRegion) return;
      st.hoverRegion = r;
      drawOverlay();
    },
    pin(i) { st.pinned = i; drawOverlay(); },
    get extent() { return st.extent; },
    hasPopulatedView: populatedFirst,
    setExtent(ext) {
      st.extent = ext;
      if (st.w) transition(fitView(currentFitIdx()), { fade: st.fadeMix, nat: st.natMix });
    },
    resize,
    // run fn when the current transition has ended (at once when none is running)
    afterTransition(fn) { if (st.animating) after.push(fn); else fn(); },
  };
}
