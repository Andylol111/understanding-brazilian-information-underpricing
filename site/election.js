// Election dashboard controller. One shared time (S.t, or S.preview while hovering the chart)
// drives the map, the chart cursor, the readout, the region picker and the race list. Reads only
// the dashboard contract files; `&live=<base>` re-fetches them every 15 s.
//
// Loading order: meta → timeline/stats (chart, readout, lists paint first) while a Web Worker
// (dash-worker.js) fetches, parses and projects the geometry and the map frames; the map appears
// when both arrive. While playing, colours are interpolated between map frames every animation frame.
import * as d3 from "./vendor/d3.js";
import { C, LIVE, STATIC, param, getJSON, absUrl, esc, pct, num, votesShort, clock, tzName, tzOffsetMin, evidenceLabel, EVIDENCE, STATUS, fmtDate, plural, REPO, gatedSettle, leadMinutes } from "./common.js";
import { createMap } from "./dash-map.js";
import { drawTimeline } from "./dash-timeline.js";
import { createScrubber } from "./dash-scrubber.js";
import { renderReadout, createRaceList, renderStats, renderMethod } from "./dash-panels.js";
import { untilText, polymarketYes } from "./dash-precount.js";
import { createNational } from "./dash-national.js";

const EID = param("id");
const $ = (id) => document.getElementById(id);
const MIN = 60000;
const WIDE = window.matchMedia("(min-width: 1100px)");
const S = {
  mode: "margin", t: 0, preview: null, playing: false, speed: 1, followLatest: false, userMoved: false,
  region: null, shownKey: null, hover: -1, pinned: -1,
  perf: { firstPaintMs: null, firstRenderMs: null, tickMs: [], frameDrawMs: [], transitionFrames: [] },
};
window.__dashPerf = S.perf;

// Before the count (meta.phase "pre-count", and no &live=): the map shows the model's projection
// before any votes (past certified results), the chart the last 14 days of prices, and Polymarket's
// current price is read in the browser every minute. From phase "live" (or with &live=) the page
// is the election-night dashboard.
const PRE = () => S.meta?.phase === "pre-count" && !LIVE;
const closeOf = (reg) => Date.parse((reg && (S.meta.poll_close_by_region?.[reg] ?? S.meta.regions?.find((r) => r.id === reg)?.poll_close_utc)) || S.meta.poll_close_utc);
const dayOf = (t) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: S.tz }).format(t);
const closeText = (reg) => { const t = closeOf(reg); return Number.isFinite(t) ? `${clock(t, S.tz)} ${tzName(S.tz, t)} on ${dayOf(t)}` : "poll close"; };

/* ---------------------------------------------------------------- map engine: worker, else page */

// dash-engine.js normally runs in a Web Worker and paints an OffscreenCanvas. If the worker cannot
// start, reports an error, has not said "ready" within ENGINE_TIMEOUT, or has not delivered the
// geometry and frames within ENGINE_CAP, the same engine runs on the page's main thread instead.
const ENGINE_TIMEOUT = 3000, ENGINE_CAP = 20000;
const E = { kind: null, worker: null, seq: 0, pending: new Map(), main: null, failing: null, readyAt: null };

function onEngineEvent(data) {
  if (data.type === "painted") { if (S.perf.firstRenderMs == null) S.perf.firstRenderMs = Math.round(data.at - performance.timeOrigin); }
  else if (data.type === "drawStats") S.perf.frameDrawMs.push(...data.ms);
  else if (data.type === "error") engineFailed(new Error(data.error));
  else if (data.type === "ready") E.readyAt = performance.now();
}

function startWorker() {
  if (typeof Worker === "undefined" || typeof OffscreenCanvas === "undefined" || !HTMLCanvasElement.prototype.transferControlToOffscreen) return false;
  try {
    E.worker = new Worker(new URL("./dash-worker.js", import.meta.url), { type: "module" });
  } catch (err) {
    console.error("Map worker could not start:", err);
    return false;
  }
  E.worker.onmessage = ({ data }) => {
    if (data.id == null) { onEngineEvent(data); return; }
    const p = E.pending.get(data.id);
    if (!p) return;
    E.pending.delete(data.id);
    if (data.error) p.reject(new Error(data.error)); else p.resolve(data.out);
  };
  E.worker.onerror = (ev) => { ev.preventDefault?.(); engineFailed(new Error(ev.message || "the map worker failed to load")); };
  E.worker.onmessageerror = () => engineFailed(new Error("a message from the map worker could not be read"));
  E.kind = "worker";
  return true;
}

async function startMain() {
  E.main ??= await import("./dash-engine.js");
  E.main.setEmitter(onEngineEvent);
  E.kind = "main";
}

function work(type, payload) {
  if (E.kind === "main") return E.main[type](payload).then((o) => o.result);
  return new Promise((resolve, reject) => {
    const id = ++E.seq;
    E.pending.set(id, { resolve, reject });
    E.worker.postMessage({ id, type, ...payload });
  });
}
const enginePost = (msg, transfer) => (E.kind === "main" ? E.main.control(msg) : E.worker.postMessage(msg, transfer ?? []));

// Switch to the page's own thread and rebuild the map there (the worker may hold the canvas).
function engineFailed(err) {
  if (E.kind !== "worker") return E.failing ?? Promise.resolve();
  E.failing ??= (async () => {
    console.error(`Map worker failed (${err?.message ?? err}); drawing the map on the page instead.`);
    S.engineNote = err?.message ?? String(err);
    E.worker.terminate();
    for (const p of E.pending.values()) p.reject(err);
    E.pending.clear();
    await startMain();
    await buildMap();
  })();
  return E.failing;
}

/* ---------------------------------------------------------------- data */

const races = () => S.meta.races ?? [];
const raceById = (id) => races().find((r) => r.race_id === id);
const national = () => races().find((r) => !r.region);
const mainRace = () => national() ?? races()[0];
const regionName = (id) => S.meta.regions?.find((r) => r.id === id)?.name ?? id?.toUpperCase();
const raceName = (r) => (r.region && !r.name.includes(regionName(r.region)) ? `${r.name}, ${regionName(r.region)}` : r.name);
const racesIn = (region) => races().filter((r) => r.region === region);
// The race's office without its region ("tx-sen" -> "sen"), to keep the same office across regions.
const office = (r) => (r?.region ? r.race_id.split(/[-_]/).filter((p) => p !== r.region).join("-") : r?.race_id);
const shortRaceName = (r) => {
  const s = r.name.replace(regionName(r.region), "").replace(/(\s+(in|of|for|the))+\s*$/i, "").replace(/^[\s,:–-]+|[\s,:–-]+$/g, "") || r.name;
  return s[0].toUpperCase() + s.slice(1);
};
// Country code: the elections.json entry, else meta.country, else the geometry file's prefix ("geo/us_…").
const countryCode = () => (S.entry?.country ?? S.meta.country ?? (S.meta.geometry ?? "").match(/geo\/([a-z]{2})_/)?.[1] ?? "").toUpperCase();
const countryName = () => {
  try { return countryCode() ? new Intl.DisplayNames(["en"], { type: "region" }).of(countryCode()) : S.meta.name; } catch { return S.meta.name; }
};

async function loadMeta() {
  const opt = { optional: true };
  const [elections, meta] = await Promise.all([getJSON("elections.json", opt), getJSON(`${EID}/meta.json`, opt)]);
  S.elections = elections ?? [];
  S.entry = S.elections.find((e) => e.id === EID) ?? null;
  if (!meta) throw new Error(`${EID}/meta.json: not found`);
  S.meta = meta;
  S.countTimeline = meta.count_timeline !== false;
  S.tz = meta.tz ?? "UTC";
  S.layer = (meta.layers?.find((l) => l.default) ?? meta.layers?.[0])?.id ?? null;
  const asked = raceById(param("race"));
  S.race = asked ?? mainRace() ?? null;
  // Open on a region only when one was asked for; elections without a national race open on the
  // overview of all regions with races.
  S.region = asked?.region ?? null;
}

const withTime = (rows) => rows.map((d) => ({ ...d, time: new Date(d.t), tms: Date.parse(d.t) }));
const timelineRows = (timeline) => new Map(Object.entries(timeline ?? {}).filter(([, rows]) => Array.isArray(rows)).map(([id, rows]) => [id, withTime(rows)]));

// First load (and every live refresh without a tail file): the whole timeline.
async function loadSeries() {
  const opt = { optional: true };
  const [timeline, stats, arb] = await Promise.all([getJSON(`${EID}/timeline.json`, opt), getJSON(`${EID}/stats.json`, opt), getJSON(`${EID}/arb.json`, opt)]);
  S.rows = timelineRows(timeline);
  S.stats = stats;
  S.arb = arb;
  S.lastFetch = Date.now();
}

// Live refresh: poll timeline_tail.json (each race's last 60 minutes) and merge it by `t`. The whole
// timeline is fetched again when the tail file is missing (404) or does not reach back to the last
// row held for some race (a gap longer than the tail window, e.g. after a long disconnect).
async function loadLiveSeries() {
  const opt = { optional: true };
  const [tail, stats, arb] = await Promise.all([getJSON(`${EID}/timeline_tail.json`, opt), getJSON(`${EID}/stats.json`, opt), getJSON(`${EID}/arb.json`, opt)]);
  const merged = tail ? mergeTail(S.rows ?? new Map(), timelineRows(tail)) : null;
  // which file the last refresh used (window.__dashPerf.timelineFetch, for checks)
  S.perf.timelineFetch = merged ? "tail" : tail ? "full: gap longer than the tail" : "full: no tail file";
  S.rows = merged ?? timelineRows(await getJSON(`${EID}/timeline.json`, opt));
  S.perf.timelineHeld = Object.fromEntries([...S.rows].map(([id, rows]) => [id, [rows.length, rows[rows.length - 1]?.t ?? null]]));
  S.stats = stats;
  S.arb = arb;
  if (S.nat) {
    const overview = await getJSON(`${EID}/${S.meta.overview}`, opt);
    if (overview) { S.overview = overview; updateNational(); }
  }
  S.lastFetch = Date.now();
}

// Rows of `tail` replace held rows with the same t and newer rows are appended; null when some
// race's tail starts after the last held row (minutes would be missing in between).
function mergeTail(have, tail) {
  const out = new Map(have);
  for (const [id, rows] of tail) {
    if (!rows.length) continue;
    const held = have.get(id) ?? [];
    if (!held.length || rows[0].tms > held[held.length - 1].tms + 60000) return null;
    const byT = new Map(held.filter((d) => d.tms >= rows[0].tms).map((d) => [d.tms, d]));
    for (const d of rows) byT.set(d.tms, d);
    const keep = held.filter((d) => d.tms < rows[0].tms);
    out.set(id, keep.concat([...byT.values()].sort((a, b) => a.tms - b.tms)));
  }
  return out;
}

// Which units files the map shows: the selected race's own file, or units.json. With no national
// race and per-race files, the overview (no region chosen, or a race without its own file, e.g.
// before the count where a state has no county baseline) merges one race per region, keeping the
// selected race's office where a region has it.
function unitsPlan() {
  if ((!S.region || (PRE() && !S.race?.units)) && !national() && races().some((r) => r.units)) {
    const byRegion = d3.group(races().filter((r) => r.region && r.units), (r) => r.region);
    const pick = [...byRegion.values()].map((list) => list.find((r) => office(r) === office(S.race)) ?? list[0]);
    return { key: pick.map((r) => r.units).join("|"), urls: pick.map((r) => `${EID}/${r.units}`), races: pick };
  }
  const p = S.race?.units ?? "units.json";
  return { key: p, urls: [`${EID}/${p}`], races: [S.race?.units ? S.race : null] };
}

async function loadUnits() {
  const plan = unitsPlan();
  const out = await work("units", { urls: plan.urls.map((u) => absUrl(u)), live: !!LIVE, countTimeline: S.countTimeline });
  if (plan.key !== unitsPlan().key) return false; // the selection moved on while loading
  setUnits(out, plan);
  return true;
}

// Row of a race's timeline at time t (last row at or before t). Time arrays are built once per load.
const tIndex = new WeakMap();
function rowAt(raceId, t) {
  const rows = S.rows.get(raceId);
  if (!rows?.length) return null;
  let ts = tIndex.get(rows);
  if (!ts) { ts = Float64Array.from(rows, (d) => d.tms); tIndex.set(rows, ts); }
  const i = d3.bisectRight(ts, t) - 1;
  return i >= 0 ? rows[i] : null;
}

function setUnits(U, plan) {
  S.units = U?.n ? U : null;
  S.unitsKey = plan.key;
  S.shownKey = null;
  if (!S.units) return;
  S.unitIndex = new Map(U.unit_ids.map((id, i) => [id, i]));
  // Map sides: units.a/b when the values are not one race's candidates (e.g. blocs); a merged
  // overview: each unit's own file's race; else units.race_id, the planned race, the region's race,
  // or the main race.
  const byRegion = new Map(races().filter((r) => r.region).map((r) => [r.region, r]));
  const sides = U.a && U.b ? { race_id: null, name: U.label ?? "Map", a: U.a, b: U.b } : null;
  const fixed = sides ?? (U.race_id ? raceById(U.race_id) : plan.races.length === 1 ? plan.races[0] : null);
  S.unitRace = U.unit_ids.map((_, j) => (plan.races.length > 1 ? plan.races[U.source[j]] : fixed) ?? byRegion.get(U.region[j]) ?? mainRace());
  S.mapRaces = [...new Set(S.unitRace)];
  S.frameTimes = U.times;
  const gaps = Array.from(U.times.slice(1), (t, i) => t - U.times[i]);
  S.frameSpacing = gaps.length ? d3.median(gaps) : 5 * MIN;
  // expected votes per region, for the picker's share counted
  S.regionUnits = d3.group(d3.range(U.n), (j) => U.region[j]);
  if (S.map) bindUnits();
}

// Overlapping unit sets (meta.layers, e.g. New Zealand's general and Māori electorates): one is
// shown at a time; units of the others are neither drawn nor hit.
const layers = () => S.meta.layers ?? [];
const layerOn = (j) => !S.layer || (S.units?.layer?.[j] ?? S.layer) === S.layer;

function bindUnits() {
  S.featIdx = Int32Array.from(S.map.ids, (id) => S.unitIndex.get(id) ?? -1);
  // colours are built in the worker from each unit's race colours
  const raceList = [...new Set(S.unitRace)];
  const at = new Map(raceList.map((r, i) => [r, i]));
  const hidden = S.layer ? Uint8Array.from(S.featIdx, (j) => (j >= 0 && !layerOn(j) ? 1 : 0)) : null;
  S.map.setUnits(S.featIdx, S.units.V0, {
    unitRace: Int16Array.from(S.unitRace, (r) => at.get(r)),
    raceColors: raceList.map((r) => [r.a.color, r.b.color]),
    parties: S.meta.parties ?? [],
    countTimeline: S.countTimeline,
    hatch: noBaseline(),
    hidden,
  });
  S.shownKey = null;
}

// The layer buttons beside the map modes (hidden when the election has no layers).
function setupLayers() {
  const box = $("map-layer");
  const ls = layers();
  box.hidden = ls.length < 2;
  if (ls.length < 2) return;
  box.innerHTML = ls.map((l) => `<button type="button" data-layer="${esc(l.id)}" aria-pressed="${l.id === S.layer}">${esc(l.name)}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.layer === S.layer) return;
    S.layer = b.dataset.layer;
    box.querySelectorAll("button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    S.pinned = -1;
    S.map?.pin(-1);
    placeCard();
    if (S.map && S.units) bindUnits();
    S.legendKey = null;
    render();
  }));
}

// Regions whose races all lack a county file (before the count: no county baseline yet, e.g. Ohio):
// hatched on the map and named in the map note.
function noBaseline() {
  if (!PRE()) return [];
  const byRegion = d3.group(races().filter((r) => r.region), (r) => r.region);
  return [...byRegion].filter(([, rs]) => rs.every((r) => !r.units)).map(([reg]) => reg);
}

/* ---------------------------------------------------------------- time */

function computeDomain() {
  let first = Infinity, end = -Infinity;
  for (const rows of S.rows.values()) {
    if (!rows.length) continue;
    first = Math.min(first, rows[0].tms);
    end = Math.max(end, rows[rows.length - 1].tms);
  }
  const ft = S.frameTimes;
  if (ft?.length > 1) { first = Math.min(first, ft[0]); end = Math.max(end, ft[ft.length - 1]); }
  if (!Number.isFinite(first)) { first = Date.parse(S.meta.poll_close_utc ?? `${S.meta.date}T12:00:00Z`); end = first + 60 * MIN; }
  // Start 15 minutes before the main race reached 1% counted: before that the count barely moves.
  let start = first;
  S.gateCut = null;
  const gate = S.countTimeline ? S.rows.get(mainRace()?.race_id)?.find((d) => d.counted >= 0.01) : null;
  if (gate && gate.tms - 15 * MIN > first) {
    start = gate.tms - 15 * MIN;
    const before = S.rows.get(mainRace().race_id).filter((d) => d.tms < start);
    S.gateCut = { gate: gate.t, start, maxBefore: d3.max(before, (d) => d.counted) };
  }
  const old = S.domain;
  S.domain = [start, Math.max(end, start + MIN)];
  const off = tzOffsetMin(S.tz, start) * MIN;
  S.wall = (d) => new Date(+d + off);
  return !old || old[0] !== S.domain[0] || old[1] !== S.domain[1];
}

const clampT = (t) => Math.max(S.domain[0], Math.min(S.domain[1], t));

// Replays open at the first map frame after the main race reached 25% counted; live pages and
// elections without a count open at the end.
function defaultTime() {
  if (LIVE || PRE() || !S.countTimeline) return S.domain[1];
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
  S.userMoved = true;
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
const SCALE = { swing: 0.1, margin: 0.4, prior: 0.4 }; // same as dash-worker.js

// Multi-party elections: meta.parties + units winner/margin give a "Winning party" map mode.
const hasWinner = () => !!(S.meta.parties?.length && S.units?.winner);
function winnerAt(k, j) {
  const w = S.units.winner;
  if (w.perFrame) return k >= 0 ? [w.perFrame[k * S.units.n + j], w.margin[k * S.units.n + j]] : [null, 0];
  return [w.top[j], w.margin[j]];
}

function modes() {
  if (!S.units) return [];
  if (PRE()) return [...(hasWinner() ? ["winner"] : []), "margin", ...remModes(), "counted"];
  const w = hasWinner() ? ["winner"] : [];
  if (!S.units.K) return [...w, "prior"];
  return [...w, ...(S.countTimeline ? ["margin", "swing", ...remModes(), "counted"] : ["margin", "swing"])];
}
// outstanding-vote modes, when the units carry rem / rem_margin
const remModes = () => (S.units?.rem ? ["rem", "remlean"] : []);
const modeLabel = (m) => ({ winner: "Winning party", margin: PRE() ? "Before any votes" : S.countTimeline ? "Projected margin" : "Final margin", swing: "Swing from prior", rem: "Votes left", remlean: "Where the remaining votes lean", counted: "Counted", prior: "Comparison election" })[m];

const WIN_FULL = 0.3; // margin (share points / 100) at which a party's colour is at full strength (dash-worker.js)

/* ---------------------------------------------------------------- render */

let listTimer = 0, lastList = 0;

function render() {
  const t0 = performance.now();
  const tt = S.preview ?? S.t;
  const k = frameIndex(tt);
  if (S.map && S.units) {
    // the worker paints: every animation frame while playing (colours tweened between map
    // frames), otherwise only when the frame or the mode changes
    const tween = S.playing && S.preview == null && S.mode !== "winner" && k >= 0 && k + 1 < S.units.K;
    const key = `${S.mode}|${k}`;
    if (tween || key !== S.shownKey) {
      S.map.paint(S.mode, tt, tween);
      S.shownKey = tween ? null : key;
    }
    const lk = `${S.mode}|${S.mode === "winner" ? k : ""}|${S.race?.race_id}|${S.layer}`;
    if (lk !== S.legendKey) { S.legendKey = lk; renderLegend(); }
    const nb = PRE() ? noBaseline().filter((reg) => !S.region || reg === S.region).map(regionName) : [];
    const remNote = remModes().includes(S.mode) ? `Circles: the model's estimate of the votes still to count${PRE() ? " (before the count: the whole expected vote)" : ""}.` : "";
    $("frame-label").textContent = PRE()
      ? remNote || (S.mode === "counted" ? `Nothing counted yet: counting starts at ${closeText(S.region)}` : `Before any votes: past certified results, not polls.${nb.length ? ` ${nb.join(", ")}: no county baseline yet; prices shown.` : ""}`)
      : k < 0 ? "Map: comparison election" : !S.countTimeline ? "Map: final results"
        : `${remNote ? `${remNote} ` : ""}${tween ? `Map ${clock(tt, S.tz)}, between the ${clock(S.units.times[k], S.tz)} and ${clock(S.units.times[k + 1], S.tz)} frames` : `Map frame ${clock(S.units.times[k], S.tz)}`}`;
    if (S.hover >= 0 || S.pinned >= 0) placeCard();
  }
  S.tl?.setCursor(S.t, S.preview);
  S.scrub.setTime(S.t, `${PRE() ? `${dayOf(S.t)} ` : ""}${clock(S.t, S.tz)} ${tzName(S.tz, S.t)}`);
  const frameT = k >= 0 && S.units ? S.units.times[k] : null;
  $("scrub-time").innerHTML = S.preview != null
    ? `<b>${clock(S.preview, S.tz)}</b> preview · scrubber at ${clock(S.t, S.tz)}`
    : `${PRE() ? `${esc(dayOf(S.t))} ` : ""}<b>${clock(S.t, S.tz)}</b> ${esc(tzName(S.tz, S.t))}${frameT != null && S.countTimeline && !S.playing && !PRE() ? ` · map frame ${clock(frameT, S.tz)}` : ""}`;
  if (S.race) {
    const pre = PRE() ? { day: dayOf(tt), note: S.race.model_note, live: S.liveNote } : null;
    renderReadout($("readout"), { race: S.race, row: rowAt(S.race.race_id, tt), t: tt, preview: S.preview != null, tz: S.tz, countTimeline: S.countTimeline, frameT: S.playing ? null : frameT, pre });
  }
  // race list and region picker: at most every 200 ms while playing
  const now = performance.now();
  clearTimeout(listTimer);
  if (now - lastList > 200) updateLists(tt);
  else listTimer = setTimeout(() => updateLists(S.preview ?? S.t), 200);
  if (S.playing) S.perf.tickMs.push(performance.now() - t0);
}

function updateLists(t) {
  lastList = performance.now();
  if (S.list) {
    S.list.update(new Map(races().map((r) => {
      const row = rowAt(r.race_id, t);
      return [r.race_id, { P: row?.P ?? null, pm: row?.pm ?? null, k: row?.kalshi ?? null }];
    })));
  }
  updatePicker(t);
}

function renderLegend() {
  const root = d3.select("#legend").html("");
  if (!S.units) return;
  if (PRE() && S.region && noBaseline().includes(S.region)) return; // nothing coloured there yet
  if (S.mode === "winner") { partyLegend(root); return; }
  if (S.mode === "rem" || S.mode === "remlean") { remLegend(root); return; }
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
    const lab = (nm) => (S.mode === "swing" ? `towards ${esc(nm)}` : `${esc(nm)} ahead`);
    root.append("div").attr("class", "legend-names").html(`<span>${lab(r.a.name)}</span><span>${lab(r.b.name)}</span>`);
  }
}

// Circle sizes (same scale as the engine: radius 22 px at the file's largest value) and, for the
// lean, the two sides' colours.
function remLegend(root) {
  const lean = S.mode === "remlean";
  const max = lean ? S.units.remScale?.maxLean : S.units.remScale?.maxRem;
  if (!(max > 0)) return;
  const r = S.mapRaces?.includes(S.race) ? S.race : S.unitRace[0];
  const nice = (v) => { const p = 10 ** Math.floor(Math.log10(v)); return [1, 2, 5, 10].map((m) => m * p).reverse().find((x) => x <= v) ?? p; };
  const sizes = [...new Set([nice(max), nice(max / 6), nice(max / 40)])].filter((v) => v > 0);
  const rad = (v) => Math.max(0.8, 22 * Math.sqrt(v / max));
  const w = 200, h = 2 * rad(sizes[0]) + 4;
  let x = 4;
  const items = sizes.map((v) => { const c = { v, cx: x + rad(v), r: rad(v) }; x += 2 * rad(v) + 26; return c; });
  const fill = lean ? r.a.color : "#4f647d";
  root.append("svg").attr("viewBox", `0 0 ${w} ${h + 14}`).attr("width", w).attr("height", h + 14).html(items.map((c) =>
    `<circle cx="${c.cx}" cy="${h - c.r - 2}" r="${c.r}" fill="${fill}" fill-opacity="0.72" stroke="#fcfbf8"/><text class="legend-text" x="${c.cx}" y="${h + 12}" text-anchor="middle">${votesShort(c.v)}</text>`).join(""));
  root.append("p").attr("class", "legend-note").html(lean
    ? `Projected margin of the votes still to count: <span style="color:${esc(r.a.color)}">${esc(r.a.name)}</span> / <span style="color:${esc(r.b.color)}">${esc(r.b.name)}</span> ahead. Model estimate.`
    : "Votes still to count, a model estimate (not an official count of outstanding ballots).");
}
function partyLegend(root) {
  const k = frameIndex(S.preview ?? S.t);
  const counts = new Map();
  for (let j = 0; j < S.units.n; j++) {
    if (!layerOn(j)) continue;
    const [p] = winnerAt(k, j);
    if (p != null) counts.set(p, (counts.get(p) ?? 0) + 1);
  }
  const rows = [...counts].sort((a, b) => b[1] - a[1]);
  const party = (id) => S.meta.parties.find((p) => p.id === id) ?? { name: id, color: "#8a8a8a" };
  root.html(`<ul class="party-legend">${rows.map(([id, n]) => `<li><i style="background:${esc(party(id).color)}"></i>${esc(party(id).name)} <b>${n}</b></li>`).join("")}</ul>
    <p class="legend-note">Units won. Paler: narrower win (full colour at a ${WIN_FULL * 100}-point margin).</p>`);
}

/* ---------------------------------------------------------------- map */

function startGeo() {
  if (!S.meta.geometry) return null;
  const key = S.meta.unit_key ?? "unit_id";
  const country = countryCode();
  S.country = country;
  return work("geo", { url: absUrl(S.meta.geometry, STATIC), objectName: S.meta.geometry_object, unitKey: key, country });
}

// Load geometry and frames with the current engine and attach the map once the rest of the page is
// up. A worker that errors, has not started within ENGINE_TIMEOUT, or has not delivered within
// ENGINE_CAP hands over to the page (engineFailed calls buildMap again with the main-thread engine).
async function buildMap() {
  let geo = null;
  if (S.meta.geometry) {
    const kind = E.kind;
    const timers = [];
    try {
      const job = Promise.all([startGeo(), loadUnits()]);
      if (kind === "worker") {
        const limit = new Promise((_, reject) => {
          timers.push(setTimeout(() => { if (!E.readyAt) reject(new Error(`the map worker did not start within ${ENGINE_TIMEOUT / 1000} s`)); }, ENGINE_TIMEOUT));
          timers.push(setTimeout(() => reject(new Error(`the map worker did not deliver within ${ENGINE_CAP / 1000} s`)), ENGINE_CAP));
        });
        [geo] = await Promise.race([job, limit]);
      } else [geo] = await job;
    } catch (err) {
      if (kind === "worker") { await engineFailed(err); return; }
      S.mapError = err.message;
      console.error("Map:", err);
    } finally {
      timers.forEach(clearTimeout);
    }
  }
  await S.ui;
  if (geo) {
    $("map").querySelectorAll(".map-canvas, .map-overlay").forEach((c) => c.remove()); // a worker that failed may still hold the old canvas
    S.map = null;
    S.mapRegions = null;
    try {
      initMap(geo);
    } catch (err) {
      if (E.kind === "worker") { await engineFailed(err); return; }
      S.mapError = err.message;
      console.error("Map:", err);
    }
  }
  if (computeDomain()) { drawChart(); updateScrubber(); }
  if (!S.userMoved) S.t = defaultTime();
  S.followLatest = S.t >= S.domain[1];
  buildPicker();
  showMapState();
  render();
}

function initMap(geo) {
  S.shownKey = null;
  S.legendKey = null;
  S.map = createMap($("map"), {
    geo, post: enginePost, offscreen: E.kind === "worker", populatedFirst: S.country === "CA",
    onHover: (i, ev) => {
      const reg = i >= 0 ? S.map.regionOf(i) : null;
      if (natShown()) {
        // the national map: a state's card, and its county file preloaded for a click
        S.nat.hover(reg, ev);
        if (reg) prefetchUnits([S.nat.raceFor(reg)?.race_id && raceById(S.nat.raceFor(reg).race_id)]);
        return;
      }
      S.hover = i;
      S.hoverPos = ev ? pointIn(ev) : null;
      placeCard();
      highlightPickerRow(reg);
    },
    onClick: (i, ev) => mapClick(i, ev),
    onFrame: (ms) => S.perf.transitionFrames.push(ms),
  });
  if (S.natLayer) S.map.setNational(...S.natLayer);
  if (S.units) bindUnits();
}

const natShown = () => !!S.nat && !S.region;
const pointIn = (ev) => { const r = $("map").getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };

function mapClick(i, ev) {
  if (natShown()) { S.nat.pick(i >= 0 ? S.map.regionOf(i) : null); return; }
  if (i < 0) { S.pinned = -1; S.map.pin(-1); placeCard(); return; }
  S.pinned = S.pinned === i ? -1 : i;
  S.pinPos = pointIn(ev);
  S.map.pin(S.pinned);
  placeCard();
  const reg = S.map.regionOf(i);
  if (reg !== S.region && S.featIdx?.[i] >= 0 && pickerRegions().some((r) => r.id === reg)) selectRegion(reg);
}

// Run when the page is idle, at the latest after `wait` ms (a plain timer where requestIdleCallback
// is missing).
const idle = (fn, wait = 4000) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: wait }) : setTimeout(fn, Math.min(wait, 800)));
// Run once the map's current zoom has finished (work during it would cost frames). Queued after the
// current task, so a zoom started later in the same handler counts.
const afterZoom = (fn) => queueMicrotask(() => (S.map ? S.map.afterTransition(fn) : fn()));

function setupModes() {
  const ms = modes();
  if (!ms.includes(S.mode) || (!S.modeChosen && ms.includes("winner"))) S.mode = ms.includes("winner") && !S.modeChosen ? "winner" : ms[0];
  const box = $("map-mode");
  box.hidden = ms.length < 2;
  box.innerHTML = ms.map((m) => `<button type="button" data-mode="${m}" aria-pressed="${m === S.mode}">${modeLabel(m)}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    S.mode = b.dataset.mode;
    S.modeChosen = true;
    box.querySelectorAll("button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    S.shownKey = null;
    render();
  }));
}

function showMapState() {
  const has = !!(S.map && S.units);
  $("map-empty").hidden = has;
  $("map-empty").textContent = has ? "" : S.mapError ? `The map could not be drawn: ${S.mapError}` : S.map ? "No map for this race." : S.meta.geometry ? "Loading the map…" : "No map for this election.";
  $("engine-note").hidden = !S.engineNote;
  $("engine-note").textContent = S.engineNote ? `Map drawn without the background worker (${S.engineNote}); playback may be less smooth.` : "";
  for (const id of ["map-mode", "legend", "frame-label"]) $(id).style.visibility = has ? "" : "hidden";
  if (!has) return;
  setupModes();
  setupLayers();
  if (!S.mapShown) { S.mapShown = true; focusMap(false); }
}

// Zoom the map to the chosen region (or out), then sync the controls. ready: a promise for the
// region's units being bound (the zoom starts at once; with a state layer the units fade in when
// they are ready).
function focusMap(animate = true, ready = null) {
  const reg = S.region && S.map?.hasRegion(S.region) && (pickerRegions().some((r) => r.id === S.region) || noBaseline().includes(S.region)) ? S.region : null;
  S.map?.focus(reg, { animate, ready });
  const ext = $("extent");
  ext.hidden = !S.map?.hasPopulatedView || !!reg;
  ext.textContent = S.map?.extent === "populated" ? "Whole map" : "Populated areas";
  renderCrumbs();
  showNational();
  for (const row of document.querySelectorAll(".rp-row")) row.classList.toggle("sel", row.dataset.region === S.region);
}

function placeCard() {
  const el = $("place");
  const i = S.pinned >= 0 ? S.pinned : S.hover;
  if (i < 0 || !S.map) { el.hidden = true; return; }
  const j = S.featIdx?.[i] ?? -1;
  let body;
  const U = S.units;
  if (j < 0 || !U) body = `<p class="note">Not part of this election's results.</p>`;
  else {
    const r = S.unitRace[j];
    const k = frameIndex(S.preview ?? S.t);
    const at = (arr) => (k >= 0 && arr[k * U.n + j] >= 0 ? arr[k * U.n + j] / 1000 : null);
    const counted = k >= 0 ? U.F[k * U.n + j] / 1000 : null, a = at(U.A), proj = at(U.P), prior = U.prior[j] >= 0 ? U.prior[j] / 1000 : null;
    const swing = a != null && prior != null ? a - prior : null;
    const sw = swing == null ? "–" : `${swing >= 0 ? "+" : "−"}${Math.abs(100 * swing).toFixed(1)} pt`;
    const an = esc(r.a.name);
    let winRow = [];
    if (hasWinner()) {
      const [p, m] = winnerAt(k, j);
      if (p != null) winRow = [["Winner", `${esc(S.meta.parties.find((x) => x.id === p)?.name ?? p)}, by ${(m / 10).toFixed(1)} pt`]];
    }
    const remRows = [];
    if (U.rem && k >= 0) {
      const left = U.rem.votes[k * U.n + j], lean = U.rem.margin[k * U.n + j];
      if (!Number.isNaN(left)) remRows.push(["Votes left (model estimate)", `≈ ${votesShort(left)}`]);
      if (!Number.isNaN(lean) && left > 0) remRows.push(["They lean", `${esc(lean >= 0 ? r.a.name : r.b.name)} +${(100 * Math.abs(lean) / left).toFixed(1)} pt`]);
    }
    const rows = S.countTimeline
      ? [...winRow, ["Counted", pct(counted, 0)], [`${an}, votes counted`, pct(a)], [`${an}, model projection`, pct(proj)], [`${an}, comparison election`, pct(prior)], ["Swing", sw], ...remRows]
      : [...winRow, [`${an}, result`, pct(a)], [`${an}, comparison election`, pct(prior)], ["Swing", sw]];
    body = `<table class="kv"><tbody>${rows.map(([k2, x]) => `<tr><td>${k2}</td><td class="n">${x}</td></tr>`).join("")}</tbody></table>`;
  }
  const name = (j >= 0 && U?.names[j]) || S.map.names[i] || S.map.ids[i];
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

/* ---------------------------------------------------------------- regions: breadcrumb and picker */

// Regions shown in the picker: those with their own races or units, and with shapes on the map.
function pickerRegions() {
  const withRaces = new Set(races().filter((r) => r.region).map((r) => r.region));
  S.mapRegions ??= S.map ? new Set(S.map.ids.map((id) => id.split("-")[1])) : null;
  const list = (S.meta.regions ?? []).filter((r) => (withRaces.has(r.id) || S.units?.region?.includes(r.id)) && (!S.mapRegions || S.mapRegions.has(r.id)));
  return list.length >= 2 ? list : [];
}

function renderCrumbs() {
  const el = $("crumbs");
  const top = countryName();
  if (!S.region) {
    const n = pickerRegions().filter((r) => racesIn(r.id).length).length;
    el.innerHTML = `<b>${esc(top)}</b>${S.nat ? ` <span class="crumb-note">· every race this year by state; ringed states have a county model (click to open)</span>`
      : !national() && n ? ` <span class="crumb-note">· ${n} ${n === 1 ? "region" : "regions"} with races; pick one</span>` : ""}`;
    return;
  }
  el.innerHTML = `<button type="button" class="crumb-link" id="crumb-top">${esc(top)}</button> <span aria-hidden="true">›</span> <b>${esc(regionName(S.region))}</b>
    <button type="button" class="crumb-back" id="crumb-back" title="Esc">Back to ${national() ? "national" : S.nat ? "the national map" : "all regions"}</button>`;
  $("crumb-top").addEventListener("click", backToNational);
  $("crumb-back").addEventListener("click", backToNational);
}

function buildPicker() {
  const el = $("regions");
  const list = pickerRegions();
  el.hidden = !list.length;
  if (!list.length) return;
  el.innerHTML = `<p class="rp-head"><span>Region</span><span>${PRE() ? "Polls close" : S.countTimeline ? "Counted" : ""}</span><span title="Model probability (orange dot), Polymarket price (black line) and Kalshi price (green line) for the region's race, 0 to 1">Chance</span></p>
    <div class="rp-list" role="listbox" aria-label="Regions">${list.map((r) => {
      const rs = racesIn(r.id);
      const chips = rs.length > 1 ? `<span class="rp-races">${rs.map((x) => `<button type="button" class="rp-chip" data-race="${esc(x.race_id)}">${esc(shortRaceName(x))}</button>`).join("")}</span>` : "";
      return `<div class="rp-row" role="option" tabindex="0" data-region="${esc(r.id)}" aria-selected="${r.id === S.region}">
        <span class="rp-name">${esc(r.name)}</span><span class="rp-counted"></span><span class="rp-ind" aria-hidden="true"><i class="rp-m"></i><i class="rp-k"></i><i class="rp-x"></i></span>${chips}</div>`;
    }).join("")}</div>`;
  el.querySelectorAll(".rp-row").forEach((row) => {
    const reg = row.dataset.region;
    row.addEventListener("click", (e) => { if (!e.target.closest(".rp-chip")) selectRegion(reg); });
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectRegion(reg); } });
    row.addEventListener("mouseenter", () => S.map?.highlightRegion(reg));
    row.addEventListener("mouseleave", () => S.map?.highlightRegion(null));
  });
  el.querySelectorAll(".rp-chip").forEach((b) => b.addEventListener("click", () => selectRace(raceById(b.dataset.race))));
}

function highlightPickerRow(reg) {
  if (reg === S.pickerHover) return;
  S.pickerHover = reg;
  for (const row of document.querySelectorAll(".rp-row")) row.classList.toggle("hl", row.dataset.region === reg);
}

function regionRace(reg) {
  const rs = racesIn(reg);
  return rs.find((r) => office(r) === office(S.race)) ?? rs[0] ?? null;
}

function updatePicker(t) {
  const rows = document.querySelectorAll(".rp-row");
  if (!rows.length) return;
  const U = S.units, k = U ? frameIndex(t) : -1;
  for (const row of rows) {
    const reg = row.dataset.region;
    // share counted: expected-vote weighted over the region's units at the map frame
    let c = null;
    if (U && k >= 0 && S.countTimeline) {
      let w = 0, f = 0, m = 0, cnt = 0;
      for (const j of S.regionUnits.get(reg) ?? []) { const v = U.V0[j] || 0; w += v; f += v * U.F[k * U.n + j]; m += U.F[k * U.n + j]; cnt++; }
      c = w > 0 ? f / w / 1000 : cnt ? m / cnt / 1000 : null;
    }
    const race = regionRace(reg), r = race ? rowAt(race.race_id, t) : null;
    if (S.countTimeline && r?.counted != null) c = r.counted; // the region's own race, when it has one
    row.children[1].textContent = PRE() ? untilText(closeOf(reg) - Date.now(), true) ?? "closed" : c == null ? "" : pct(c, 0);
    const ind = row.children[2];
    const m = r?.P ?? null, pm = r?.pm ?? null, kx = r?.kalshi ?? null;
    [m, pm, kx].forEach((v, i) => {
      const mark = ind.children[i];
      mark.hidden = v == null;
      if (v != null) mark.style.left = `${v * 100}%`;
    });
    ind.title = race ? `${race.name}: model ${num(m)}, Polymarket ${num(pm)}${kx != null ? `, Kalshi ${num(kx)}` : ""}` : "No race for this region";
    ind.style.visibility = race ? "" : "hidden";
    row.querySelectorAll(".rp-chip").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.race === S.race?.race_id)));
  }
}

async function selectRegion(reg) {
  const race = regionRace(reg);
  if (race) return selectRace(race);
  S.region = reg;
  await moveMap();
}

async function backToNational() {
  if (!S.region) return;
  if (national()) return selectRace(national());
  S.region = null;
  await moveMap();
}

// The zoom starts right away; the units for the new view load meanwhile and fade in when bound.
// Zooming in, the readout and lists update first (their layout lands before the zoom's frames);
// zooming out, they update when the zoom is over (the layout would cost one of its frames).
async function moveMap() {
  if (!S.region) {
    // back out: with a state map the counties are hidden by the end of the zoom and reload after it
    const ready = S.nat ? null : reloadUnitsIfNeeded();
    focusMap(true, ready);
    afterZoom(async () => { if (S.nat) await reloadUnitsIfNeeded(); render(); });
    await ready;
    return;
  }
  const ready = reloadUnitsIfNeeded();
  render();
  focusMap(true, ready);
  await ready;
  afterZoom(render); // legend and lists for the new units
}

async function reloadUnitsIfNeeded() {
  if (unitsPlan().key === S.unitsKey) return;
  if (await loadUnits()) {
    if (S.map) bindUnits();
    // mode buttons and legend for the new units: rebuilt once a running zoom is over
    if (S.mapShown) afterZoom(showMapState); else showMapState();
  }
}

/* ---------------------------------------------------------------- race, chart, scrubber */

async function selectRace(r) {
  if (!r) return;
  const sameRace = r === S.race;
  S.race = r;
  S.region = r.region ?? null;
  if (!sameRace) {
    $("race").value = r.race_id;
    const url = new URL(location.href);
    url.searchParams.set("race", r.race_id);
    history.replaceState(null, "", url);
    S.list?.select(r.race_id);
    S.legendKey = null;
    // the chart and statistics redraw when the map's zoom leaves the main thread idle
    afterZoom(() => { if (S.race !== r) return; drawChart(); updateScrubber(); renderStatsTab(); });
  }
  await moveMap();
}

function chartHeight() {
  if (!WIDE.matches) return 300;
  return Math.max(190, Math.min(320, Math.round(innerHeight * 0.28)));
}

function drawChart() {
  S.tl = drawTimeline($("timeline"), {
    race: S.race, rows: S.rows.get(S.race.race_id) ?? [], domain: S.domain, tz: S.tz, wall: S.wall, height: chartHeight(), countPanel: S.countTimeline,
    placeholder: PRE() ? `Fills in from ${closeText(S.race.region)}` : null,
    onPreview: (t) => { S.preview = t; render(); },
    onSet: (t) => { pause(); S.preview = null; setTime(t); },
  });
  const g = S.gateCut;
  $("tl-note").textContent = PRE()
    ? "Before the count: prices every 30 minutes over the last 14 days, and Polymarket's current price as the last point. The orange line, where the model has a figure, is its chance before any votes."
    : g
      ? `Axis starts at ${clock(g.start, S.tz)}: from ${S.meta.poll_close_utc ? `poll close (${clock(S.meta.poll_close_utc, S.tz)})` : "the first row"} to ${clock(g.gate, S.tz)} under 1% of the vote was counted${g.maxBefore != null ? ` (at most ${pct(g.maxBefore)})` : ""}.`
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
      ev(settle.kalshi, "Kalshi 90% sure", C.kalshi),
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
  S.userMoved = true;
  S.perf.tickMs = [];
  S.perf.frameDrawMs = [];
  $("play").textContent = "Pause";
  $("play").setAttribute("aria-pressed", "true");
  let last = performance.now();
  const step = (now) => {
    if (!S.playing) return;
    const dt = Math.min(100, now - last);
    last = now;
    S.t = Math.min(S.domain[1], S.t + (dt * S.speed * (S.frameSpacing ?? 5 * MIN)) / 1000);
    S.followLatest = S.t >= S.domain[1];
    render();
    if (S.t >= S.domain[1]) { pause(); return; }
    S.raf = requestAnimationFrame(step);
  };
  S.raf = requestAnimationFrame(step);
}

function pause() {
  if (!S.playing) return;
  S.playing = false;
  cancelAnimationFrame(S.raf);
  $("play").textContent = "Play";
  $("play").setAttribute("aria-pressed", "false");
  render(); // back to the frame at or before the scrubber time
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
  if (e.key === "Escape") {
    if (S.pinned >= 0) { S.pinned = -1; S.map?.pin(-1); placeCard(); }
    else backToNational();
    return;
  }
  if (tag === "SELECT" || tag === "TEXTAREA" || (tag === "INPUT" && e.target.type !== "range") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === " ") {
    if ((tag === "BUTTON" && e.target.id !== "play") || e.target.classList?.contains("rp-row")) return; // their own Space
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
  // the evidence label lives in the Statistics and Method & sources tabs; the bar only says live/scheduled
  $("evidence").innerHTML = PRE() ? `<span class="status pre">Before the count</span> <span id="countdown"></span>`
    : e?.status && e.status !== "replay" ? `<span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span>` : "";
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
    // the live server carries only the elections being counted; the rest come from the static site
    const target = S.elections.find((x) => x.id === sel.value);
    if (LIVE && (target?.status === "live" || target?.status === "scheduled")) u.searchParams.set("live", LIVE);
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

// The race's entry in stats.profit_model_limit.by_race: keyed by race_id, or (Hungary) by market id,
// in which case the entries of all the race's markets are added up.
function profitOf(r) {
  const by = S.stats?.profit_model_limit?.by_race;
  if (!by || !r) return null;
  if (by[r.race_id]) return by[r.race_id];
  const ids = new Set((r.markets ?? []).map((m) => m.id));
  const list = Object.values(by).filter((p) => ids.has(p.market));
  if (list.length <= 1) return list[0] ?? null;
  const sum = (k) => d3.sum(list, (p) => p[k] ?? 0);
  const firsts = list.map((p) => p.first_eligible).filter(Boolean).sort((a, b) => a.t.localeCompare(b.t));
  return {
    markets: list.length, eligible_trades: sum("eligible_trades"), eligible_volume: sum("eligible_volume"), eligible_pnl: sum("eligible_pnl"),
    bankroll_stake: sum("bankroll_stake"), bankroll_pnl: sum("bankroll_pnl"), first_eligible: firsts[0] ?? null,
    reason: list.every((p) => !p.eligible_trades) ? list[0].reason : null,
  };
}

function renderStatsTab() {
  renderStats($("pane-stats"), { stats: S.stats, race: S.race, races: races(), raceName, tz: S.tz, arb: S.arb, settleOf, profitOf });
}

function geometryCredit() {
  const g = S.meta.geometry ?? "";
  if (g.includes("us_")) return "Map: U.S. Census Bureau cartographic boundary files, 2024 (public domain), built by <code>analysis/geo_build.py</code>.";
  if (g.includes("br_")) return "Map: IBGE Malha Municipal Digital 2025 (public open data, attribution IBGE), joined to TSE municipality codes with the TSE table of TSE and IBGE codes (CC-BY), built by <code>analysis/geo_build.py</code>.";
  if (g.includes("nz_")) return "Map: Stats NZ, General Electorates 2025, Māori Electorates 2025 and Urban Rural land classes, CC BY 4.0.";
  return "";
}

// Race picker: national races first, then one group per region (region first, then its races).
function initRaceSelect() {
  const sel = $("race");
  const nat = races().filter((r) => !r.region);
  const byRegion = d3.groups(races().filter((r) => r.region), (r) => r.region);
  sel.innerHTML = nat.map((r) => `<option value="${esc(r.race_id)}">${esc(r.name)}</option>`).join("")
    + byRegion.map(([reg, list]) => `<optgroup label="${esc(regionName(reg))}">${list.map((r) => `<option value="${esc(r.race_id)}">${esc(raceName(r))}</option>`).join("")}</optgroup>`).join("");
  sel.value = S.race?.race_id ?? "";
  sel.addEventListener("change", () => selectRace(raceById(sel.value)));
  $("extent").addEventListener("click", () => {
    if (!S.map) return;
    S.map.setExtent(S.map.extent === "populated" ? "all" : "populated");
    $("extent").textContent = S.map.extent === "populated" ? "Whole map" : "Populated areas";
  });
}

/* ---------------------------------------------------------------- live */

// One live refresh; false when a file could not be fetched (the last good data stays on screen).
async function refresh() {
  try {
    await Promise.all([loadLiveSeries(), loadUnits()]);
  } catch {
    return false;
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
  return true;
}

function buildRaceList() {
  const hasKalshi = [...(S.rows?.values() ?? [])].some((rows) => rows.some((d) => d.kalshi != null));
  S.list = createRaceList($("pane-races"), { races: races(), raceName, leadOf: (r) => leadMinutes(settleOf(r.race_id)), onSelect: selectRace, hasKalshi, profitOf });
  S.list.select(S.race.race_id);
}

// Live polling: every 15 s while the tab is visible. After a failed refresh the wait doubles up to
// 2 min (15 → 30 → 60 → 120 s) and resets on the next success. A hidden tab does not poll; when it
// is shown again the next refresh runs as soon as its wait has passed (at once after a long absence).
// Requests revalidate (If-None-Match), so an unchanged file costs a 304.
const POLL_MS = [15000, 30000, 60000, 120000];
function startLive() {
  const el = $("live");
  el.hidden = false;
  let fails = 0, timer = 0, busy = false, lastTry = Date.now(), lastOk = S.lastFetch ?? Date.now();
  const wait = () => POLL_MS[Math.min(fails, POLL_MS.length - 1)];
  const show = () => {
    const s = Math.max(0, Math.round((Date.now() - lastOk) / 1000));
    const ago = s < 120 ? `${s} s` : `${Math.round(s / 60)} min`;
    el.innerHTML = fails ? `Reconnecting… · updated ${ago} ago` : `<span class="dot" aria-hidden="true"></span>Live · updated ${ago} ago`;
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = document.hidden ? 0 : setTimeout(poll, Math.max(0, lastTry + wait() - Date.now()));
  };
  async function poll() {
    timer = 0;
    if (busy || document.hidden) return;
    busy = true;
    lastTry = Date.now();
    const ok = await refresh();
    busy = false;
    if (ok) lastOk = Date.now();
    fails = ok ? 0 : fails + 1;
    show();
    schedule();
  }
  document.addEventListener("visibilitychange", () => { if (!busy) schedule(); });
  show();
  setInterval(show, 1000);
  schedule();
}

/* ---------------------------------------------------------------- main */

// Polymarket's current prices, read in the browser once a minute while the tab is visible, in one
// batched request for every market on the page: the meta races (before the count: added as each
// race's latest row, model chance and Kalshi carried from the last row the scheduled build wrote)
// and the national map's races. Before the count, every 10 minutes the meta, timeline and overview
// are read again; when the phase is no longer "pre-count" the page reloads as the election-night
// dashboard. On the night (&live=) only the national map uses these prices.
const PRE_POLL_MS = 60000, PRE_FILES_MS = 10 * 60000;
const pmOf = (r) => (r.markets ?? []).find((m) => m.platform === "polymarket" && m.id);
function startPrices() {
  const pre = PRE();
  if (!pre && !S.nat) return;
  const el = $("live");
  if (pre) {
    // nothing to play before the count (one map frame); the scrubber still moves through the prices
    $("play").style.display = "none";
    $("speed").style.display = "none";
    el.hidden = false;
  }
  let lastOk = 0, lastFiles = Date.now(), failed = false, timer = 0, busy = false;
  let yes = new Map(); // slug -> YES price
  const fresh = async (path) => {
    const r = await fetch(absUrl(path), { cache: "no-cache" });
    return r.ok ? r.json() : null;
  };
  const applyRows = () => {
    const now = Date.now(), iso = new Date(now).toISOString();
    for (const r of races()) {
      const mk = pmOf(r), y = mk ? yes.get(mk.id) : null;
      const p = y == null ? null : mk.yes_is === "b" ? 1 - y : y;
      const rows = (S.rows.get(r.race_id) ?? []).filter((d) => !d.live);
      const last = rows[rows.length - 1];
      if (p != null) rows.push({ ...(last ?? {}), t: iso, time: new Date(now), tms: now, pm: p, live: true });
      S.rows.set(r.race_id, rows);
    }
    const kRow = [...(S.rows.get(S.race.race_id) ?? [])].reverse().find((d) => !d.live && d.kalshi != null);
    S.liveNote = `Polymarket price live${kRow ? `; Kalshi as of ${dayOf(kRow.tms)} ${clock(kRow.tms, S.tz)}` : ""}`;
    const follow = S.followLatest || S.t >= S.domain[1];
    computeDomain();
    S.t = follow ? S.domain[1] : clampT(S.t);
    S.followLatest = S.t >= S.domain[1];
    drawChart();
    updateScrubber();
    render();
  };
  const show = () => {
    if (!pre) return; // on the night the top bar shows the live count's status
    const left = untilText(closeOf(null) - Date.now());
    const cd = $("countdown");
    if (cd) cd.textContent = left ? `· first polls close in ${left}` : "· polls are closing";
    const s = Math.round((Date.now() - lastOk) / 1000);
    el.innerHTML = lastOk
      ? `<span class="dot" aria-hidden="true"></span>Polymarket live · ${s < 120 ? `${s} s` : `${Math.round(s / 60)} min`} ago${failed ? " (last try failed)" : ""}`
      : failed ? "Polymarket price unavailable; showing the last recorded one" : "Reading Polymarket prices…";
  };
  async function poll() {
    timer = 0;
    if (busy || document.hidden) return;
    busy = true;
    if (pre && Date.now() - lastFiles >= PRE_FILES_MS) {
      lastFiles = Date.now();
      const [meta, timeline, overview] = await Promise.all([fresh(`${EID}/meta.json`), fresh(`${EID}/timeline.json`), S.meta.overview ? fresh(`${EID}/${S.meta.overview}`) : null]).catch(() => [null, null, null]);
      if (meta && meta.phase !== "pre-count") { location.reload(); return; }
      if (timeline) S.rows = timelineRows(timeline);
      if (overview) S.overview = overview;
    }
    try {
      yes = await polymarketYes([...races().map((r) => pmOf(r)?.id), ...(S.nat?.slugs() ?? [])]);
      failed = !yes.size;
      if (yes.size) lastOk = Date.now();
    } catch (err) {
      failed = true;
      console.warn("Polymarket prices:", err.message);
    }
    busy = false;
    if (pre) applyRows();
    updateNational(yes);
    show();
    timer = setTimeout(poll, PRE_POLL_MS);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(timer); timer = 0; return; }
    if (!timer && !busy) timer = setTimeout(poll, Math.max(0, lastOk + PRE_POLL_MS - Date.now()));
  });
  show();
  if (pre) setInterval(show, 1000);
  poll();
}

/* ---------------------------------------------------------------- national map (meta.overview) */

// The national view shows while no region is chosen; modeled states drill into the county view.
let natYes = new Map();
function updateNational(yes = natYes) {
  natYes = yes;
  if (!S.nat) return;
  const live = new Map();
  for (const o of Object.values(S.overview?.offices ?? {})) {
    for (const r of o.races ?? []) {
      const y = r.pm?.slug ? yes.get(r.pm.slug) : null;
      if (y != null) live.set(r.race_key, r.pm.yes_is === "rep" ? 1 - y : y);
    }
    for (const c of o.control_markets ?? []) {
      const y = c.platform === "polymarket" && c.slug ? yes.get(c.slug) : null;
      if (y != null) live.set(`ctl:${c.slug}`, c.yes_is === "rep" ? 1 - y : y);
    }
  }
  const model = new Map(races().map((r) => [r.race_id, rowAt(r.race_id, S.t)?.P ?? null]).filter(([, p]) => p != null));
  S.nat.update({ overview: S.overview, live, model });
}

// The overview's office ("senate" | "governor") as the race-id suffix of the modelled races.
const OFFICE_ID = { senate: "sen", governor: "gov" };

async function initNational() {
  if (!S.meta.overview || !S.meta.geometry) return;
  const overview = await getJSON(`${EID}/${S.meta.overview}`, { optional: true }).catch(() => null);
  if (!overview) return;
  S.overview = overview;
  S.nat = createNational($("national"), {
    overview, office: "senate", summary: $("nat-summary"),
    onDrill: (id) => { const r = raceById(id); if (r) selectRace(r); },
    onOffice: (o) => syncOffice(o),
    onColours: (colours, rings) => { S.natLayer = [colours, rings]; S.map?.setNational(colours, rings); },
    onHighlight: (r) => S.map?.highlightRegion(r),
  });
  // the merged county layer under the state map follows the office shown
  if (!param("race")) syncOffice("senate", false);
  S.nat.draw(); // the state layer is ready before the map's first frame
}

// Keep the selected race on the office the national map shows, so the counties that fade in on
// a click are that office's (and the county files to preload are known).
function syncOffice(o, reload = true) {
  const r = races().find((x) => x.units && office(x) === OFFICE_ID[o]);
  if (!r || r === S.race || S.region) return;
  S.race = r;
  $("race").value = r.race_id;
  if (reload) reloadUnitsIfNeeded();
}

// While the national map shows, the right column shows the office summary instead of one race,
// and the national controls replace the county map's (both fade; see style.css).
function showNational() {
  const on = !!S.nat && !S.region;
  $("national").classList.toggle("off", !on);
  $("dash").classList.toggle("nat-on", on);
  for (const id of ["map-mode", "legend", "frame-label"]) $(id).classList.toggle("nat-hidden", on);
  if (on) afterZoom(() => { if (natShown()) S.nat.draw(); });
  else S.nat?.hover(null);
}

// Preload the county file of a modelled state's race (on hover, and for all of them once idle).
const prefetched = new Set();
function prefetchUnits(rs) {
  if (LIVE) return; // live files are revalidated on every read; nothing to warm
  const urls = rs.filter((r) => r?.units).map((r) => absUrl(`${EID}/${r.units}`)).filter((u) => !prefetched.has(u));
  if (!urls.length) return;
  urls.forEach((u) => prefetched.add(u));
  work("prefetch", { urls }).catch(() => urls.forEach((u) => prefetched.delete(u)));
}

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
    await loadMeta();
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
  // the map engine starts on the big files while the page draws everything else
  S.ui = new Promise((resolve) => { S.uiReady = resolve; });
  // Headless browsers (screenshot tools, crawlers) get the page engine: they judge the page finished
  // from the page's own activity and do not wait for a worker's parsing, so a worker-drawn map can be
  // missing from their capture. ?engine=worker or ?engine=page overrides.
  const engine = param("engine") ?? (/HeadlessChrome/.test(navigator.userAgent) ? "page" : "worker");
  if (engine === "page" || !startWorker()) await startMain();
  await initNational(); // before the map: it sets the state layer and the office's county files
  S.mapDone = buildMap();
  await loadSeries();
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
  renderCrumbs();
  render();
  S.perf.firstPaintMs = Math.round(performance.now());
  startPrices();
  if (S.nat) idle(() => prefetchUnits(races()));

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

  S.uiReady(); // the map (loading in parallel since the top bar) can now attach
  await S.mapDone;
  if (LIVE) startLive();
}

main();
