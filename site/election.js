// Election dashboard controller. One shared time (S.t, or S.preview while hovering the chart)
// drives the map frame, the chart cursor, the readout and the race list. Reads only the
// dashboard contract files; `&live=<base>` re-fetches them every 15 s.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { C, LIVE, STATIC, param, getJSON, esc, pct, clock, tzName, tzOffsetMin, evidenceLabel, EVIDENCE, STATUS, fmtDate, plural, REPO, gatedSettle, leadMinutes } from "./common.js";
import { createMap } from "./dash-map.js";
import { drawTimeline } from "./dash-timeline.js";
import { createScrubber } from "./dash-scrubber.js";
import { renderReadout, createRaceList, renderStats, renderMethod } from "./dash-panels.js";

const EID = param("id");
const $ = (id) => document.getElementById(id);
const MIN = 60000;
const WIDE = window.matchMedia("(min-width: 1100px)");
const S = {
  mode: "margin", t: 0, preview: null, playing: false, speed: 1, followLatest: false,
  colourCache: new Map(), shownKey: null, hover: -1, pinned: -1,
  perf: { firstRenderMs: null, frameDrawMs: [], colourMs: [] },
};
window.__dashPerf = S.perf;

/* ---------------------------------------------------------------- data */

const races = () => S.meta.races ?? [];
const raceById = (id) => races().find((r) => r.race_id === id);
const national = () => races().find((r) => !r.region);
const mainRace = () => national() ?? races()[0];
const regionName = (id) => S.meta.regions?.find((r) => r.id === id)?.name ?? id?.toUpperCase();
const raceName = (r) => (r.region && !r.name.includes(regionName(r.region)) ? `${r.name}, ${regionName(r.region)}` : r.name);
const unitsPath = () => `${EID}/${S.race?.units ?? "units.json"}`;

async function loadAll(first) {
  const opt = { optional: true };
  if (first) {
    const [elections, meta] = await Promise.all([getJSON("elections.json", opt), getJSON(`${EID}/meta.json`, opt)]);
    S.elections = elections ?? [];
    S.entry = S.elections.find((e) => e.id === EID) ?? null;
    if (!meta) throw new Error(`${EID}/meta.json: not found`);
    S.meta = meta;
    S.countTimeline = meta.count_timeline !== false;
    S.tz = meta.tz ?? "UTC";
    S.race = raceById(param("race")) ?? mainRace() ?? null;
  }
  const path = unitsPath();
  const [timeline, units, stats, arb] = await Promise.all([
    getJSON(`${EID}/timeline.json`, opt), getJSON(path, opt), getJSON(`${EID}/stats.json`, opt), getJSON(`${EID}/arb.json`, opt),
  ]);
  S.rows = new Map(Object.entries(timeline ?? {}).map(([id, rows]) => [id, rows.map((d) => ({ ...d, time: new Date(d.t), tms: Date.parse(d.t) }))]));
  S.stats = stats;
  S.arb = arb;
  if (path === unitsPath()) setUnits(units);
  S.lastFetch = Date.now();
}

// Row of a race's timeline at time t (last row at or before t). Time arrays are built once per load.
const tIndex = new WeakMap();
function rowFast(raceId, t) {
  const rows = S.rows.get(raceId);
  if (!rows?.length) return null;
  let ts = tIndex.get(rows);
  if (!ts) { ts = Float64Array.from(rows, (d) => d.tms); tIndex.set(rows, ts); }
  const i = d3.bisectRight(ts, t) - 1;
  return i >= 0 ? rows[i] : null;
}

function setUnits(units) {
  S.units = units?.unit_ids?.length ? units : null;
  S.colourCache.clear();
  S.shownKey = null;
  if (!S.units) return;
  S.unitIndex = new Map(units.unit_ids.map((id, i) => [id, i]));
  const byRegion = new Map(races().filter((r) => r.region).map((r) => [r.region, r]));
  // Map sides: units.a/units.b when the frames are not one race's candidates (e.g. blocs), else
  // units.race_id, else the race for the unit's region, else the main race.
  const sides = units.a && units.b ? { race_id: null, name: units.label ?? "Map", a: units.a, b: units.b } : null;
  const fixed = sides ?? (units.race_id ? raceById(units.race_id) : null);
  S.unitRace = units.unit_ids.map((_, i) => fixed ?? byRegion.get(units.region?.[i]) ?? mainRace());
  S.unitLUT = S.unitRace.map(divLUT);
  S.mapRaces = [...new Set(S.unitRace)];
  S.frameTimes = (units.frames ?? []).map((f) => Date.parse(f.t));
  const gaps = S.frameTimes.slice(1).map((t, i) => t - S.frameTimes[i]);
  S.frameSpacing = gaps.length ? d3.median(gaps) : 5 * MIN;
  if (S.map) bindUnits();
}

function bindUnits() {
  const key = S.meta.unit_key ?? "unit_id";
  S.featIdx = S.map.features.map((f) => S.unitIndex.get(f.properties[key]) ?? -1);
  S.map.setUnits(S.featIdx, S.units.V0);
}

/* ---------------------------------------------------------------- time */

function computeDomain() {
  let first = Infinity, end = -Infinity;
  for (const rows of S.rows.values()) {
    if (!rows.length) continue;
    first = Math.min(first, rows[0].tms);
    end = Math.max(end, rows[rows.length - 1].tms);
  }
  if (S.frameTimes?.length > 1) { first = Math.min(first, S.frameTimes[0]); end = Math.max(end, S.frameTimes[S.frameTimes.length - 1]); }
  if (!Number.isFinite(first)) { first = Date.parse(S.meta.poll_close_utc ?? `${S.meta.date}T12:00:00Z`); end = first + 60 * MIN; }
  // Start 15 minutes before the main race reached 1% counted: before that the count barely moves
  // (in the Brazil runoff 0.2-0.4% sat unchanged from 17:06 to 19:04).
  let start = first;
  S.gateCut = null;
  const gate = S.countTimeline ? S.rows.get(mainRace()?.race_id)?.find((d) => d.counted >= 0.01) : null;
  if (gate && gate.tms - 15 * MIN > first) {
    start = gate.tms - 15 * MIN;
    const before = S.rows.get(mainRace().race_id).filter((d) => d.tms < start);
    S.gateCut = { gate: gate.t, start, maxBefore: d3.max(before, (d) => d.counted) };
  }
  S.domain = [start, Math.max(end, start + MIN)];
  const off = tzOffsetMin(S.tz, start) * MIN;
  S.wall = (d) => new Date(+d + off);
}

const clampT = (t) => Math.max(S.domain[0], Math.min(S.domain[1], t));

function defaultTime() {
  if (LIVE || !S.countTimeline) return S.domain[1];
  const hit = S.rows.get(mainRace()?.race_id)?.find((d) => d.counted >= 0.25);
  if (!hit) return S.domain[1];
  return clampT(S.frameTimes?.find((t) => t >= hit.tms) ?? hit.tms);
}

function frameIndex(t) {
  const ft = S.frameTimes;
  if (!ft?.length) return -1;
  return Math.max(0, d3.bisectRight(ft, t) - 1);
}

function setTime(t) {
  S.t = clampT(t);
  S.followLatest = S.t >= S.domain[1];
  render();
}

/* ---------------------------------------------------------------- colours */

const NONE = C.none, NEUTRAL = "#f4f2ec";
const COUNTED_LUT = d3.range(101).map((i) => d3.interpolateLab("#e9e7e0", "#4f647d")(i / 100));
const lutCache = new Map();
function divLUT(race) {
  const key = `${race.a.color}|${race.b.color}`;
  if (!lutCache.has(key)) {
    lutCache.set(key, d3.range(201).map((j) => {
      const v = j / 100 - 1;
      return d3.interpolateLab(NEUTRAL, v >= 0 ? race.a.color : race.b.color)(Math.abs(v) ** 0.8);
    }));
  }
  return lutCache.get(key);
}
const SCALE = { swing: 0.1, margin: 0.4, prior: 0.4 };
const lutIdx = (v) => Math.round((Math.max(-1, Math.min(1, v)) + 1) * 100);

function modes() {
  if (!S.units) return [];
  if (!S.units.frames?.length) return ["prior"];
  return S.countTimeline ? ["margin", "swing", "counted"] : ["margin", "swing"];
}
const modeLabel = (m) => ({ margin: S.countTimeline ? "Projected margin" : "Final margin", swing: "Swing from prior", counted: "Counted", prior: "Comparison election" })[m];

function coloursFor(mode, k) {
  const key = `${mode}|${k}`;
  let out = S.colourCache.get(key);
  if (out) return out;
  const t0 = performance.now();
  const u = S.units, fr = k >= 0 ? u.frames[k] : null, prior = u.prior, idx = S.featIdx, luts = S.unitLUT;
  out = new Array(idx.length);
  for (let i = 0; i < idx.length; i++) {
    const j = idx[i];
    if (j < 0) { out[i] = NONE; continue; }
    let c = NONE;
    if (mode === "counted") {
      const f = fr.f[j];
      if (f > 0) c = COUNTED_LUT[Math.min(100, Math.round(f / 10))];
    } else if (mode === "swing") {
      const a = fr.a[j], p = prior[j];
      if (a >= 0 && p >= 0) c = luts[j][lutIdx((a - p) / 1000 / SCALE.swing)];
    } else if (mode === "margin") {
      const v = S.countTimeline ? fr.proj?.[j] ?? -1 : fr.a[j] >= 0 ? fr.a[j] : fr.proj?.[j] ?? -1;
      if (v >= 0) c = luts[j][lutIdx((2 * v / 1000 - 1) / SCALE.margin)];
    } else if (mode === "prior") {
      const p = prior[j];
      if (p >= 0) c = luts[j][lutIdx((2 * p / 1000 - 1) / SCALE.prior)];
    }
    out[i] = c;
  }
  S.colourCache.set(key, out);
  S.perf.colourMs.push(performance.now() - t0);
  return out;
}

// Fill the colour cache for every frame of the current mode while the page is idle, so playing
// never computes colours on the critical path.
function warmColours() {
  if (!S.units?.frames?.length) return;
  const mode = S.mode;
  let k = 0;
  const idle = window.requestIdleCallback ?? ((f) => setTimeout(() => f({ timeRemaining: () => 8 }), 30));
  const work = (deadline) => {
    while (k < S.units.frames.length && deadline.timeRemaining() > 3) { if (S.mode !== mode) return; coloursFor(mode, k++); }
    if (k < S.units.frames.length) idle(work);
  };
  idle(work);
}

/* ---------------------------------------------------------------- render */

let listTimer = 0, lastList = 0;

function render() {
  const tt = S.preview ?? S.t;
  const k = frameIndex(tt);
  if (S.map && S.units) {
    const key = `${S.mode}|${k}`;
    if (key !== S.shownKey) {
      S.map.setColours(coloursFor(S.mode, k));
      S.shownKey = key;
      if (S.playing) S.perf.frameDrawMs.push(S.map.lastDrawMs);
      if (S.perf.firstRenderMs == null) S.perf.firstRenderMs = Math.round(performance.now());
      renderLegend();
    }
    $("frame-label").textContent = k < 0 ? "Map: comparison election" : !S.countTimeline ? "Map: final results" : `Map frame ${clock(S.units.frames[k].t, S.tz)}`;
    placeCard();
  }
  S.tl?.setCursor(S.t, S.preview);
  const label = `${clock(S.t, S.tz)} ${tzName(S.tz, S.t)}`;
  S.scrub.setTime(S.t, label);
  $("scrub-time").innerHTML = S.preview != null
    ? `<b>${clock(S.preview, S.tz)}</b> preview · scrubber at ${clock(S.t, S.tz)}`
    : `<b>${clock(S.t, S.tz)}</b> ${esc(tzName(S.tz, S.t))}${k >= 0 && S.countTimeline ? ` · map frame ${clock(S.units.frames[k].t, S.tz)}` : ""}`;
  if (S.race) {
    renderReadout($("readout"), { race: S.race, row: rowFast(S.race.race_id, tt), t: tt, preview: S.preview != null, tz: S.tz, countTimeline: S.countTimeline, frameT: k >= 0 ? S.units?.frames[k].t : null });
  }
  // race list: at most every 200 ms while playing
  const now = performance.now();
  clearTimeout(listTimer);
  if (now - lastList > 200) updateList(tt);
  else listTimer = setTimeout(() => updateList(S.preview ?? S.t), 200);
}

function updateList(t) {
  lastList = performance.now();
  if (!S.list) return;
  const vals = new Map(races().map((r) => {
    const row = rowFast(r.race_id, t);
    return [r.race_id, { P: row?.P ?? null, m: row?.pm ?? row?.kalshi ?? null }];
  }));
  S.list.update(vals);
}

function renderLegend() {
  const root = d3.select("#legend").html("");
  if (!S.units) return;
  const w = 200, h = 8;
  const r = S.mapRaces?.includes(S.race) ? S.race : S.unitRace[0];
  const svg = root.append("svg").attr("viewBox", `0 0 ${w} 24`).attr("width", w).attr("height", 24);
  const id = `lg-${S.mode}`;
  const grad = svg.append("defs").append("linearGradient").attr("id", id);
  const stops = S.mode === "counted" ? COUNTED_LUT.filter((_, i) => i % 10 === 0).map((c, i) => [i / 10, c])
    : d3.range(0, 201, 10).map((j) => [j / 200, divLUT(r)[200 - j]]);
  grad.selectAll("stop").data(stops).join("stop").attr("offset", (d) => d[0]).attr("stop-color", (d) => d[1]);
  svg.append("rect").attr("width", w).attr("height", h).attr("fill", `url(#${id})`);
  const max = (SCALE[S.mode] ?? 1) * 100;
  const ticks = S.mode === "counted" ? [[0, "0%"], [0.5, "50%"], [1, "100% counted"]] : [[0, `+${max}`], [0.5, "0"], [1, `+${max} pt`]];
  svg.selectAll("text").data(ticks).join("text").attr("x", (d) => d[0] * w).attr("y", 20)
    .attr("text-anchor", (d) => (d[0] === 0 ? "start" : d[0] === 1 ? "end" : "middle")).attr("class", "legend-text").text((d) => d[1]);
  if (S.mode !== "counted") {
    const lab = (n) => (S.mode === "swing" ? `towards ${esc(n)}` : `${esc(n)} ahead`);
    root.append("div").attr("class", "legend-names").html(`<span>${lab(r.a.name)}</span><span>${lab(r.b.name)}</span>`);
  }
}

/* ---------------------------------------------------------------- map */

async function initMap() {
  if (!S.meta.geometry) return;
  const topo = await getJSON(S.meta.geometry, { root: STATIC });
  const key = S.meta.unit_key ?? "unit_id";
  S.map = createMap($("map"), {
    topo, objectName: S.meta.geometry_object, unitKey: key,
    isUS: String(Object.values(topo.objects)[0]?.geometries?.[0]?.properties?.[key] ?? "").startsWith("us-"),
    onHover: (i, ev) => { S.hover = i; S.hoverPos = ev ? pointIn(ev) : null; placeCard(); },
    onClick: (i, ev) => mapClick(i, ev),
  });
  if (S.units) bindUnits();
}

const pointIn = (ev) => { const r = $("map").getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };

function mapClick(i, ev) {
  if (i < 0) { S.pinned = -1; S.map.pin(-1); placeCard(); return; }
  S.pinned = S.pinned === i ? -1 : i;
  S.pinPos = pointIn(ev);
  S.map.pin(S.pinned);
  placeCard();
  const rr = races().find((r) => r.region && r.region === S.map.regionOf(i));
  if (rr && rr !== S.race && S.featIdx?.[i] >= 0) selectRace(rr);
}

function setupModes() {
  const ms = modes();
  if (!ms.includes(S.mode)) S.mode = ms[0];
  const box = $("map-mode");
  box.hidden = ms.length < 2;
  box.innerHTML = ms.map((m) => `<button type="button" data-mode="${m}" aria-pressed="${m === S.mode}">${modeLabel(m)}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    S.mode = b.dataset.mode;
    box.querySelectorAll("button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    S.shownKey = null;
    render();
    warmColours();
  }));
}

function showMapState() {
  const has = !!(S.map && S.units);
  $("map-empty").hidden = has;
  for (const id of ["map-mode", "legend", "frame-label"]) $(id).style.visibility = has ? "" : "hidden";
  if (!has) return;
  setupModes();
  focusMap(false);
}

function focusMap(animate = true) {
  const reg = S.race?.region && S.units?.region?.includes(S.race.region) ? S.race.region : null;
  S.map?.focus(reg, animate);
  $("national").hidden = !reg;
  $("national").textContent = national() ? "Back to national race" : "Whole map";
  $("reset-race").hidden = !(national() && S.race !== national());
}

function placeCard() {
  const el = $("place");
  const i = S.pinned >= 0 ? S.pinned : S.hover;
  if (i < 0 || !S.map) { el.hidden = true; return; }
  const f = S.map.features[i];
  const j = S.featIdx?.[i] ?? -1;
  let body;
  if (j < 0) body = `<p class="note">Not part of this election's results.</p>`;
  else {
    const u = S.units, r = S.unitRace[j];
    const k = frameIndex(S.preview ?? S.t);
    const fr = k >= 0 ? u.frames[k] : null;
    const v = (x) => (x != null && x >= 0 ? x / 1000 : null);
    const counted = fr ? v(fr.f[j]) : null, a = fr ? v(fr.a[j]) : null, proj = fr ? v(fr.proj?.[j]) : null, prior = v(u.prior?.[j]);
    const swing = a != null && prior != null ? a - prior : null;
    const sw = swing == null ? "–" : `${swing >= 0 ? "+" : "−"}${Math.abs(100 * swing).toFixed(1)} pt`;
    const an = esc(r.a.name);
    const rows = S.countTimeline
      ? [["Counted", pct(counted, 0)], [`${an}, votes counted`, pct(a)], [`${an}, model projection`, pct(proj)], [`${an}, comparison election`, pct(prior)], ["Swing", sw]]
      : [[`${an}, result`, pct(a)], [`${an}, comparison election`, pct(prior)], ["Swing", sw]];
    body = `<table class="kv"><tbody>${rows.map(([k2, x]) => `<tr><td>${k2}</td><td class="n">${x}</td></tr>`).join("")}</tbody></table>`;
  }
  const name = (j >= 0 && S.units.names?.[j]) || f.properties.name || f.properties[S.meta.unit_key ?? "unit_id"];
  const reg = S.map.regionOf(i);
  el.innerHTML = `<p class="pc-name"><b>${esc(name)}</b>${reg && !String(name).includes(`, ${reg.toUpperCase()}`) ? `, ${esc(regionName(reg))}` : ""}</p>${body}
    <p class="pc-hint">${S.pinned >= 0 ? "Pinned. Click it again to release." : "Click to pin."}</p>`;
  el.hidden = false;
  const box = $("map").getBoundingClientRect();
  const pos = (S.pinned >= 0 ? S.pinPos : S.hoverPos) ?? [box.width / 2, box.height / 2];
  if (!WIDE.matches) { el.style.left = ""; el.style.top = ""; return; }
  const cw = el.offsetWidth, ch = el.offsetHeight;
  let x = pos[0] + 16, y = pos[1] + 16;
  if (x + cw > box.width - 8) x = pos[0] - cw - 16;
  if (y + ch > box.height - 8) y = Math.max(8, box.height - ch - 8);
  el.style.left = `${Math.max(8, x)}px`;
  el.style.top = `${Math.max(8, y)}px`;
}

/* ---------------------------------------------------------------- race, chart, scrubber */

async function selectRace(r) {
  if (!r) return;
  if (r === S.race) { focusMap(); return; }
  const before = unitsPath();
  S.race = r;
  $("race").value = r.race_id;
  const url = new URL(location.href);
  url.searchParams.set("race", r.race_id);
  history.replaceState(null, "", url);
  S.list?.select(r.race_id);
  drawChart();
  updateScrubber();
  renderStatsTab();
  if (unitsPath() !== before) {
    const path = unitsPath();
    const units = await getJSON(path, { optional: true });
    if (path !== unitsPath()) return;
    setUnits(units);
    showMapState();
    warmColours();
  } else focusMap();
  render();
}

function chartHeight() {
  if (!WIDE.matches) return 300;
  return Math.max(190, Math.min(320, Math.round(innerHeight * 0.28)));
}

function drawChart() {
  S.tl = drawTimeline($("timeline"), {
    race: S.race, rows: S.rows.get(S.race.race_id) ?? [], domain: S.domain, tz: S.tz, wall: S.wall, height: chartHeight(), countPanel: S.countTimeline,
    onPreview: (t) => { S.preview = t; render(); },
    onSet: (t) => { pause(); S.preview = null; setTime(t); },
  });
  const g = S.gateCut;
  $("tl-note").textContent = g
    ? `Axis starts at ${clock(g.start, S.tz)}: from ${S.meta.poll_close_utc ? `poll close (${clock(S.meta.poll_close_utc, S.tz)})` : "the first row"} to ${clock(g.gate, S.tz)} under 1% of the ${esc(mainRace().name.toLowerCase())} vote was counted (at most ${pct(g.maxBefore)}).`
    : "";
  S.tl.setCursor(S.t, S.preview);
}

const gateOf = (id) => (S.countTimeline ? S.rows.get(id)?.find((d) => d.counted >= 0.01)?.t ?? null : null);
const settleOf = (id) => gatedSettle(S.stats, id, gateOf(id));

function updateScrubber() {
  const id = S.race?.race_id;
  const rows = S.rows.get(id) ?? [];
  const settle = S.stats?.settle?.[id] ?? {};
  const ev = (iso, label, color) => {
    if (!iso) return null;
    const t = Date.parse(iso);
    const out = t < S.domain[0] || t > S.domain[1];
    return { t, label: out ? `${label} (${clock(iso, S.tz)})` : label, color };
  };
  const gate = gateOf(id);
  // The model is never shown as settled before the 1% gate: if it was already at 0.9 then, its
  // marker sits on the gate.
  const gatedModel = settleOf(id)?.model ?? settle.model;
  const modelAtGate = gate && settle.model && Date.parse(settle.model) <= Date.parse(gate);
  S.scrub.set({
    domain: S.domain, rows: S.countTimeline ? rows : [], wall: S.wall, tz: S.tz,
    events: [
      gate && !modelAtGate && ev(gate, "1% counted", C.muted),
      modelAtGate ? ev(gate, "model 90% sure from 1% counted", C.model) : ev(gatedModel, "model 90% sure", C.model),
      ev(settle.polymarket, "Polymarket 90% sure", C.market),
      ev(settle.raw, "raw count settles", C.raw),
    ].filter(Boolean),
  });
  const playable = (S.frameTimes?.length ?? 0) > 1 || (S.countTimeline && rows.length > 1);
  $("play").hidden = !playable;
  $("speed").hidden = !playable;
}

/* ---------------------------------------------------------------- playback, keys */

function play() {
  if (S.playing) return;
  if (S.t >= S.domain[1]) S.t = S.domain[0];
  S.playing = true;
  S.perf.frameDrawMs = [];
  $("play").textContent = "Pause";
  $("play").setAttribute("aria-pressed", "true");
  let last = performance.now();
  const step = (now) => {
    if (!S.playing) return;
    const dt = Math.min(100, now - last);
    last = now;
    S.t = Math.min(S.domain[1], S.t + (dt * S.speed * S.frameSpacing) / 1000);
    S.followLatest = S.t >= S.domain[1];
    render();
    if (S.t >= S.domain[1]) { pause(); return; }
    S.raf = requestAnimationFrame(step);
  };
  S.raf = requestAnimationFrame(step);
}

function pause() {
  S.playing = false;
  cancelAnimationFrame(S.raf);
  $("play").textContent = "Play";
  $("play").setAttribute("aria-pressed", "false");
}

function stepFrame(dir) {
  pause();
  const ft = S.frameTimes ?? [];
  let t;
  if (ft.length > 1) {
    t = dir > 0 ? ft.find((x) => x > S.t) : [...ft].reverse().find((x) => x < S.t);
    t ??= dir > 0 ? S.domain[1] : S.domain[0];
  } else t = S.t + dir * 15 * MIN;
  setTime(t);
}

document.addEventListener("keydown", (e) => {
  const tag = e.target.tagName;
  if (tag === "SELECT" || tag === "TEXTAREA" || (tag === "INPUT" && e.target.type !== "range") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === " ") {
    if (tag === "BUTTON" && e.target.id !== "play") return; // let buttons handle their own Space
    e.preventDefault();
    if (e.target.id === "play") return; // the button's click handler toggles
    S.playing ? pause() : play();
  } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    if (tag === "BUTTON" || e.target.getAttribute("role") === "tab") return;
    e.preventDefault();
    stepFrame(e.key === "ArrowRight" ? 1 : -1);
  }
});

/* ---------------------------------------------------------------- top bar, tabs */

function renderTopbar() {
  const m = S.meta, e = S.entry;
  document.title = `${m.name}: election night`;
  $("title").textContent = m.name;
  $("date").textContent = fmtDate(m.date);
  const ev = e?.evidence;
  const long = EVIDENCE[ev]?.long;
  $("evidence").innerHTML = ev
    ? `<span class="tip" tabindex="0" data-tip="${esc(long ? long[0].toUpperCase() + long.slice(1) + "." : ev)}">${evidenceLabel(ev) || `<span class="label mo">${esc(ev)}</span>`}</span>${e?.status && e.status !== "replay" ? ` <span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span>` : ""}`
    : "";
  const names = new Intl.DisplayNames(["en"], { type: "region" });
  const groups = d3.groups(S.elections, (x) => x.country ?? "");
  groups.sort((a, b) => (names.of(a[0] || "ZZ") ?? a[0]).localeCompare(names.of(b[0] || "ZZ") ?? b[0]));
  const sel = $("switcher");
  sel.innerHTML = groups.map(([c, list]) => `<optgroup label="${esc(c ? names.of(c) ?? c : "Other")}">${
    [...list].sort((a, b) => b.date.localeCompare(a.date)).map((x) => `<option value="${esc(x.id)}"${x.id === EID ? " selected" : ""}>${esc(x.name)} (${esc(x.date.slice(0, 4))})</option>`).join("")}</optgroup>`).join("");
  if (!S.elections.some((x) => x.id === EID)) sel.insertAdjacentHTML("afterbegin", `<option value="${esc(EID)}" selected>${esc(m.name)}</option>`);
  sel.addEventListener("change", () => {
    const u = new URL("election.html", location.href);
    u.searchParams.set("id", sel.value);
    if (S.race) u.searchParams.set("race", S.race.race_id);
    if (LIVE) u.searchParams.set("live", LIVE);
    location.href = u.toString();
  });
  if (LIVE) $("home").href = `./?live=${encodeURIComponent(LIVE)}`;
  if (!S.countTimeline) {
    $("dash-note").hidden = false;
    $("dash-note").textContent = m.count_note ?? "No minute-by-minute count is published for this election. The map shows final results (or the comparison election while they are pending) and the chart shows market prices only.";
  }
}

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const show = (t) => {
    tabs.forEach((x) => {
      const on = x === t;
      x.setAttribute("aria-selected", String(on));
      x.tabIndex = on ? 0 : -1;
      $(x.getAttribute("aria-controls")).hidden = !on;
    });
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => show(t));
    t.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      n.focus();
      show(n);
    });
  });
  show(tabs[0]);
}

function renderStatsTab() {
  renderStats($("pane-stats"), { stats: S.stats, race: S.race, races: races(), raceName, tz: S.tz, arb: S.arb, settleOf });
}

function geometryCredit() {
  const g = S.meta.geometry ?? "";
  if (g.includes("us_")) return "Map: U.S. Census Bureau cartographic boundary files, 2024 (public domain), built by <code>analysis/geo_build.py</code>.";
  if (g.includes("br_")) return "Map: IBGE Malha Municipal Digital 2025 (public open data, attribution IBGE), joined to TSE municipality codes with the TSE table of TSE and IBGE codes (CC-BY), built by <code>analysis/geo_build.py</code>.";
  return "";
}

function initRaceSelect() {
  const sel = $("race");
  const nat = races().filter((r) => !r.region), reg = races().filter((r) => r.region);
  const opt = (r) => `<option value="${esc(r.race_id)}">${esc(raceName(r))}</option>`;
  sel.innerHTML = nat.map(opt).join("") + (reg.length ? `<optgroup label="By region">${reg.map(opt).join("")}</optgroup>` : "");
  sel.value = S.race?.race_id ?? "";
  sel.addEventListener("change", () => selectRace(raceById(sel.value)));
  $("reset-race").addEventListener("click", () => selectRace(national()));
  $("national").addEventListener("click", () => (national() ? selectRace(national()) : (S.map?.focus(null), ($("national").hidden = true))));
}

/* ---------------------------------------------------------------- live */

async function refresh() {
  try {
    await loadAll(false);
  } catch {
    return; // keep the last good data; the next tick retries
  }
  const follow = S.followLatest;
  computeDomain();
  S.t = follow ? S.domain[1] : clampT(S.t);
  S.followLatest = S.t >= S.domain[1];
  if (S.map && S.units) { bindUnits(); showMapState(); }
  drawChart();
  updateScrubber();
  renderStatsTab();
  buildRaceList();
  render();
}

function buildRaceList() {
  S.list = createRaceList($("pane-races"), { races: races(), raceName, leadOf: (r) => leadMinutes(settleOf(r.race_id)), onSelect: selectRace });
  S.list.select(S.race.race_id);
}

function liveTicker() {
  const el = $("live");
  el.hidden = false;
  const tick = () => {
    const s = Math.round((Date.now() - S.lastFetch) / 1000);
    el.innerHTML = `<span class="dot" aria-hidden="true"></span>Live · updated ${s} s ago`;
  };
  tick();
  setInterval(tick, 1000);
}

/* ---------------------------------------------------------------- main */

// A scheduled election whose data folder does not exist yet: say what will appear on the night
// and where the frozen model settings are, from its elections.json entry alone.
function renderPlaceholder() {
  const e = S.entry;
  S.meta ??= { name: e.name, date: e.date, races: [] };
  S.tz = S.meta.tz ?? "UTC";
  S.countTimeline = true;
  renderTopbar();
  const ev = EVIDENCE[e.evidence];
  const close = S.meta.poll_close_utc ?? e.poll_close_utc;
  const tz = S.meta.tz ?? e.tz;
  const units = e.n_units != null ? `${e.n_units.toLocaleString("en-US")} ${plural(e.unit_level, e.n_units)}` : "";
  const level = e.unit_level ? plural(e.unit_level, 2) : "reporting units";
  $("dash").classList.add("placeholder-mode");
  $("dash").innerHTML = `<section class="placeholder">
    <p class="kicker">${esc(STATUS[e.status] ?? e.status)} · ${esc(fmtDate(e.date))}</p>
    <h2>${esc(e.name)}</h2>
    <p class="dek">${esc(e.summary ?? "")}</p>
    <dl class="defs">
      ${close ? `<dt>Polls close</dt><dd>${esc(clock(close, "UTC"))} UTC${tz ? ` (${esc(clock(close, tz))} ${esc(tzName(tz, close))})` : ""}</dd>` : ""}
      ${units ? `<dt>Reporting units</dt><dd>${esc(units)}</dd>` : ""}
      ${ev ? `<dt>Evidence</dt><dd>${evidenceLabel(e.evidence)} ${esc(ev.long[0].toUpperCase() + ev.long.slice(1))}.${e.evidence === "forward" ? " Results from this night can test whether the model is ahead of the market, because nothing about the outcome was known when the settings were fixed." : ""}</dd>` : ""}
    </dl>
    <h3>What will appear here on election night</h3>
    <ul>
      <li>A map of the count by ${esc(e.unit_level ?? "reporting unit")}, refreshed as results come in: the model's projected margin in each of the ${esc(level)}, the swing from the comparison election, and the share counted.</li>
      <li>For each race, the raw count against the model's projected final share with its 90% band, and the model's probability of winning against the Polymarket price, minute by minute.</li>
      <li>A list of all races with the gap between model and market at any moment and, once the count is complete, the standard statistics for this night. The replay stays here afterwards.</li>
    </ul>
    <h3>Frozen model settings</h3>
    <p>The frozen settings for this night are in <a href="${REPO}/tree/main/configs">configs/${esc(EID)}_*_v1.toml</a> on GitHub; the registration notes, with the date each file was frozen, are in <a href="${REPO}/blob/main/prereg/NOTES.md">prereg/NOTES.md</a>.</p>
  </section>`;
}

async function main() {
  if (!EID) {
    $("title").textContent = "No election chosen";
    $("dash").innerHTML = `<p class="dash-note">Pick one from the <a href="./">list of elections</a>.</p>`;
    return;
  }
  try {
    await loadAll(true);
  } catch (err) {
    if (S.entry?.status === "scheduled") { renderPlaceholder(); return; }
    $("title").textContent = "Election not found";
    $("dash").innerHTML = `<p class="dash-note">Could not load data for "${esc(EID)}" (${esc(err.message)}).</p>`;
    return;
  }
  if (!races().length) {
    if (S.entry?.status === "scheduled") { renderPlaceholder(); return; }
    renderTopbar();
    $("dash").innerHTML = `<p class="dash-note">No races listed for this election yet.</p>`;
    return;
  }
  document.body.classList.toggle("no-count", !S.countTimeline);
  renderTopbar();
  computeDomain();
  S.t = defaultTime();
  S.followLatest = S.t >= S.domain[1];
  S.scrub = createScrubber($("scrubber"), { onInput: (t) => { pause(); S.preview = null; setTime(t); } });
  initRaceSelect();
  initTabs();
  buildRaceList();
  renderMethod($("pane-method"), { meta: S.meta, entry: S.entry, geometryCredit: geometryCredit() });
  renderStatsTab();
  drawChart();
  updateScrubber();
  await initMap();
  showMapState();
  render();
  warmColours();

  $("play").addEventListener("click", () => (S.playing ? pause() : play()));
  document.querySelectorAll("#speed button").forEach((b) => b.addEventListener("click", () => {
    S.speed = +b.dataset.speed;
    document.querySelectorAll("#speed button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  }));
  let lastW = $("timeline").clientWidth, lastWide = WIDE.matches, rz = 0;
  new ResizeObserver(() => {
    const w = $("timeline").clientWidth;
    if (w === lastW && WIDE.matches === lastWide) return;
    lastW = w; lastWide = WIDE.matches;
    clearTimeout(rz);
    rz = setTimeout(() => { drawChart(); placeCard(); }, 120);
  }).observe($("timeline"));
  if (LIVE) { liveTicker(); setInterval(refresh, 15000); }
}

main();
