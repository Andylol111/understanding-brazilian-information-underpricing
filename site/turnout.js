// Election Day turnout summary on the early-vote page: reads data/ev2026/turnout_day.json (+ turnout_volume.json if present), both written by
// analysis/us_turnout_publish.py, and shows a compact summary that links to the turnout dashboard (turnout.html). Turnout only: who voted,
// not how. Not used by any model. Every number shown comes from the files; this module only formats it. Re-read every 5 minutes.
import { esc, STATIC } from "./common.js";

const $ = (s) => document.querySelector(s);
const KINDS = [["election_day", "Election Day check-ins"], ["early_in_person", "early in person"], ["mail_returned", "mail / absentee returned"], ["advance", "all pre-Election Day"], ["total", "total, all methods"]];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REFRESH_MS = 5 * 60e3;
const utc = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}, ${iso.slice(11, 16)} UTC` : "–");
const T = { cache: "" };
// The files as last read, for the key-findings cards of the early-vote page; onData() runs after each read.
export const data = { day: null, vol: null };
let onData = () => {};

function render(d) {
  $("#td-asof").innerHTML = `Data as of <b>${esc(utc(d.generated_utc))}</b> (checked ${esc(utc(d.published_utc))}); re-read every 5 minutes.`;
  const withRows = d.states.filter((s) => s.rows.length).length, withCounty = d.states.filter((s) => s.counties.length).length;
  const per = KINDS.map(([k, t]) => [t, d.states.filter((s) => [...s.rows, ...s.counties].some((r) => r.kind === k)).length]).filter(([, n]) => n);
  $("#td-sum").textContent = `${withRows} of ${d.states.length} states and DC have a statewide row; ${withCounty} have county rows; ${d.n_rows} rows in all. States with a count, by kind: ${per.map(([t, n]) => `${t} ${n}`).join(", ")}. Counts of different kinds are never added together.`;
  data.day = d;
  window.__tdReady = true;
  onData();
}

async function loadHistory() {
  const el = $("#td-hist");
  try {
    const r = await fetch(new URL("data/ev2026/turnout_volume.json", location.href), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const v = await r.json();
    data.vol = v;
    onData();
    el.innerHTML = `<b>Does Election Day volume tell us anything?</b> ${esc(v.verdict)}${v.summary ? ` ${esc(v.summary)}` : ""}${v.n_cases != null ? ` (${esc(String(v.n_cases))} past cases)` : ""}`;
  } catch {
    el.textContent = "The test of whether turnout volume says anything about the result has not been published.";
  }
}

async function load() {
  const r = await fetch(new URL("data/ev2026/turnout_day.json", location.href), { cache: "no-store" });
  if (!r.ok) throw new Error(`turnout_day.json: HTTP ${r.status}`);
  const d = await r.json();
  if (d.generated_utc === T.cache) return;
  T.cache = d.generated_utc;
  render(d);
}

export async function initTurnout(cb) {
  if (cb) onData = cb;
  loadHistory();
  try {
    await load();
  } catch (e) {
    $("#td-asof").textContent = "The turnout file is not published yet.";
    window.__tdReady = true;
    console.warn(e);
  }
  setInterval(() => load().catch(() => {}), REFRESH_MS);
}

// ----------------------------------------------------------------------------------------------------------------
// The US map of election.html, for the two early-vote pages: the dashboard's own engine (dash-engine.js: same Albers USA
// projection, borders and state layer) and map (dash-map.js: same hover outline, pan/zoom), drawn on the page's thread.
// colour(Map state -> colour) paints the state layer; the states are also reachable by keyboard through one hidden button each.
// Region ids are lower-case state codes ("tx"); onHover(state | null, event, county | null), onClick(state, event),
// onCounty(state, county, event), onZoom(state | null), onFocus(state | null). Clicking a state zooms into its counties; county = { id, fips, name }.
export async function usMap(box, { names, onHover, onClick, onCounty, onZoom, onFocus }) {
  const eng = await import("./dash-engine.js"); // the import map adds ?v=
  const { createMap } = await import("./dash-map.js");
  eng.setEmitter(() => {});
  const url = new URL("geo/us_counties.topo.json", new URL(STATIC, location.href)).href;
  const { result: geo } = await eng.geo({ url, objectName: "counties", unitKey: "unit_id", country: "US" });
  const feats = new Map();
  geo.ids.forEach((id, i) => { const r = id.split("-")[1]; if (!feats.has(r)) feats.set(r, []); feats.get(r).push(i); });
  const box0 = new Map(); // region -> union of its features' bounds, in the map's base space
  for (const [r, list] of feats) {
    const u = [Infinity, Infinity, -Infinity, -Infinity];
    for (const i of list) { const b = geo.bounds; if (!Number.isFinite(b[4 * i])) continue; u[0] = Math.min(u[0], b[4 * i]); u[1] = Math.min(u[1], b[4 * i + 1]); u[2] = Math.max(u[2], b[4 * i + 2]); u[3] = Math.max(u[3], b[4 * i + 3]); }
    box0.set(r, u);
  }
  const layer = document.createElement("div"); // value labels over the map; pointer-events none
  layer.className = "ev-labels"; layer.setAttribute("aria-hidden", "true");
  const text = new Map(), els = new Map();
  const place = () => {
    const v = map.view;
    for (const [r, el] of els) {
      const u = box0.get(r), t = text.get(r);
      const w = (u[2] - u[0]) * v.k, h = (u[3] - u[1]) * v.k;
      const show = !focus && t != null && layer.clientWidth >= 600 && w >= 34 && h >= 18;
      el.hidden = !show;
      if (show) { el.textContent = t; el.style.transform = `translate(${((u[0] + u[2]) / 2) * v.k + v.x}px, ${((u[1] + u[3]) / 2) * v.k + v.y}px) translate(-50%, -50%)`; }
    }
  };
  let sel = null; // the chosen state, outlined whole (the national map's region outline) when nothing is hovered
  let focus = null; // the state whose counties are shown (the election page's second map level), or null for the national level
  let countyFn = null; // state -> Map(county fips -> colour) | null; counties without a colour are grey
  const NOFIG = "#e6e4dd";
  const county = (i) => ({ id: geo.ids[i], fips: geo.ids[i].split("-")[2] ?? "", name: (geo.names[i] ?? "").replace(/,\s*[A-Z]{2}$/, "") });
  // Counties are painted through the engine's own "winner" colouring (one palette colour per county at full strength) on a units object
  // built here, so the fills, borders, fades and hatching are exactly the election page's. Only the focus state's counties are bound.
  const paintCounties = (r) => {
    const n = geo.ids.length, cols = r && countyFn ? countyFn(r) ?? new Map() : new Map();
    const index = new Int32Array(n).fill(-1), top = new Array(n).fill(null), pal = new Map();
    if (r) for (const i of feats.get(r) ?? []) {
      index[i] = i;
      const c = cols.get(county(i).fips) ?? NOFIG;
      if (!pal.has(c)) pal.set(c, `k${pal.size}`);
      top[i] = pal.get(c);
    }
    eng.control({ type: "state", state: { units: { n, K: 1, times: Float64Array.of(0), winner: { top, margin: new Array(n).fill(300) }, vals: { prior: new Float32Array(n), margin: new Float32Array(n) }, F: new Float32Array(n), A: new Float32Array(n), P: new Float32Array(n), P1: new Float32Array(n), prior: new Float32Array(n), V0: new Float32Array(n), byRegion: new Map() } } });
    map.setUnits(index, null, { unitRace: new Int16Array(n), raceColors: [["#000000", "#ffffff"]], parties: [...pal].map(([color, id]) => ({ id, color })) });
    map.paint("winner", 0, false);
  };
  const apply = (r, animate) => {
    focus = r && feats.has(r) ? r : null;
    paintCounties(focus);
    map.focus(focus, { animate, nat: focus ? 1 : 0, level: 0 });
    map.setNatHover(!focus);
    map.highlightRegion(sel);
    box.dataset.level = focus ? "counties" : "states";
    box.dataset.focus = focus ?? "";
    back.hidden = !focus;
    place();
    onZoom?.(focus);
  };
  const map = createMap(box, {
    geo, post: eng.control, offscreen: false, onView: () => place(),
    onHover: (i, ev) => {
      if (i < 0) { map.highlightRegion(sel); onHover?.(null, ev); return; }
      onHover?.(map.regionOf(i), ev, focus ? county(i) : null);
    },
    onClick: (i, ev) => {
      if (i < 0) return;
      const r = map.regionOf(i);
      if (focus) { if (r === focus) onCounty?.(r, county(i), ev); return; }
      apply(r, true);
      onClick?.(r, ev);
    },
  });
  map.setNational(new Map(), []);
  map.setLevel({ level: 0, nat: 1, animate: false });
  // the election page's map controls: zoom in / out / fit (.map-tools), and the way back from a state's counties
  const tools = document.createElement("div");
  tools.className = "map-ui map-tools";
  tools.setAttribute("role", "toolbar"); tools.setAttribute("aria-label", "Map view");
  tools.innerHTML = `<button type="button" data-act="in" title="Zoom in" aria-label="Zoom in">+</button><button type="button" data-act="out" title="Zoom out" aria-label="Zoom out">−</button><button type="button" data-act="reset" title="Reset the view" aria-label="Reset the view">Fit</button>`;
  const back = document.createElement("button");
  back.type = "button"; back.className = "map-ui map-national"; back.dataset.act = "back"; back.textContent = "Back to US"; back.hidden = true;
  back.onclick = () => apply(null, true);
  tools.addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (act === "in") map.zoomBy(1.5);
    else if (act === "out") map.zoomBy(1 / 1.5);
    else if (act === "reset") { if (map.viewState || !focus) map.resetView(); else back.click(); }
  });
  box.append(tools, back);
  const kb = document.createElement("div");
  kb.className = "ev-kb";
  for (const r of [...feats.keys()].sort((a, b) => names(a).localeCompare(names(b)))) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.st = r; b.textContent = names(r);
    b.onclick = (e) => { apply(r, true); onClick?.(r, e); };
    b.onfocus = () => { map.highlightRegion(r); onFocus?.(r); };
    b.onblur = () => { map.highlightRegion(sel); onFocus?.(null); };
    kb.append(b);
  }
  for (const r of feats.keys()) { const el = document.createElement("span"); el.hidden = true; els.set(r, el); layer.append(el); }
  box.append(layer, kb);
  new ResizeObserver(() => { requestAnimationFrame(place); setTimeout(place, 250); }).observe(box);
  box.dataset.level = "states";
  const api = {
    // values: Map state -> short text shown on the state at desktop widths (small states show it only when there is room)
    values: (m) => { text.clear(); for (const [r, t] of m) text.set(r, t); place(); },
    colour: (m) => { map.setNational(new Map(m), []); map.setNatHover(!focus); place(); },
    // fn(state) -> Map(county fips -> colour) | null, asked whenever a state is opened; repaint() asks again for the open one
    counties: (fn) => { countyFn = fn; if (focus) paintCounties(focus); },
    repaint: () => { if (focus) paintCounties(focus); },
    // the chosen state, outlined on the national map; open() also zooms into its counties (null: back to the whole country)
    select: (r) => { sel = r ?? null; map.highlightRegion(sel); },
    open: (r) => { sel = r ?? null; if (sel !== focus) apply(sel, true); else map.highlightRegion(sel); },
    focus: () => focus,
    // page coordinates of a state's middle / of a county of the open state (for the page checks)
    pointOf: (r) => { const u = box0.get(r), v = map.view, rc = box.getBoundingClientRect(); return u ? [rc.left + ((u[0] + u[2]) / 2) * v.k + v.x, rc.top + ((u[1] + u[3]) / 2) * v.k + v.y] : null; },
    countyPoint: (fips) => { const i = geo.ids.findIndex((id) => id.endsWith(`-${fips}`) && id.split("-")[1] === focus); return i < 0 ? null : map.pointOf(i); },
    label: (r, text) => { const b = kb.querySelector(`[data-st="${r}"]`); if (b) b.setAttribute("aria-label", text); },
  };
  box.usMap = api;
  return api;
}
