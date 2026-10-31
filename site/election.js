// Election dashboard controller. One shared time (S.t, or S.preview while hovering the chart)
// drives the map, the chart cursor, the readout, the region picker and the race list. Reads only
// the dashboard contract files; `&live=<base>` re-fetches them every 15 s.
//
// Loading order: meta → timeline/stats (chart, readout, lists paint first) while a Web Worker
// (dash-worker.js) fetches, parses and projects the geometry and the map frames; the map appears
// when both arrive. While playing, colours are interpolated between map frames every animation frame.
import * as d3 from "./vendor/d3.js";
import { C, LIVE, STATIC, param, getJSON, absUrl, esc, pct, num, votesShort, modelP, probit, marginText, clock, tzName, tzOffsetMin, evidenceLabel, EVIDENCE, STATUS, fmtDate, plural, REPO, gatedSettle, leadMinutes, crossDocTransitions } from "./common.js";
import { createMap } from "./dash-map.js";
import { drawTimeline, timelineViews, drawTurnout } from "./dash-timeline.js";
import { createScrubber } from "./dash-scrubber.js";
import { renderReadout, createRaceList, renderStats, renderMethod } from "./dash-panels.js";
import { untilText, polymarketYes } from "./dash-precount.js";
import { createNational, stateName as usStateName } from "./dash-national.js";
import { createOverlay, houseList, houseRaceTable, districtReadout, districtChart, loadHouse, houseDistrictIds, ourModelHTML, CENSUS } from "./dash-house.js";
import { loadUsHub, renderUsTab } from "./dash-us-tabs.js";
import { regionAgg, regionWinner, BANDS, TIE, bandOf, bandColours, tieBreak } from "./dash-agg.js";
import { decidedBeforeCount, outcomeA, winnerName, winnerProbability, settledAt, staysAbove } from "./decided.js";

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
// Give style/layout/paint a turn between independent start-up stages. scheduler.yield preserves
// task priority where available; Firefox/Safari use a zero-delay task.
const yieldBrowser = () => globalThis.scheduler?.yield ? globalThis.scheduler.yield() : new Promise((resolve) => setTimeout(resolve, 0));

// Before the count (meta.phase "pre-count", and no &live=): the map shows the model's projection
// before any votes (past certified results), the chart the last 14 days of prices, and Polymarket's
// current price is read in the browser every minute. From phase "live" (or with &live=) the page
// is the election-night dashboard.
const PRE = () => S.meta?.phase === "pre-count" && !LIVE;
const closeOf = (reg) => Date.parse((reg && (S.meta.poll_close_by_region?.[reg] ?? S.meta.regions?.find((r) => r.id === reg)?.poll_close_utc ?? S.civic?.races.find((c) => c.region === reg && c.poll_close_utc)?.poll_close_utc)) || S.meta.poll_close_utc);
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
    E.worker = new Worker(new URL(`./dash-worker.js?v=${window.__V ?? ""}`, import.meta.url), { type: "module" });
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
  E.main ??= await import("./dash-engine.js"); // the import map adds ?v=
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
// races of the meta, then the US overview's races with only a county estimate (S.extra), then the
// House count models of house_live/ (S.houseLive)
const raceById = (id) => races().find((r) => r.race_id === id) ?? S.extra?.get(id) ?? S.houseLive?.races.get(id);
// House district count models (race_id "house:TX-23", region = state) are read from their own
// files (house_live/, loadHouseLive); a pre-count meta may also list them beside the statewide
// races. Either way they are shown through the House district selection, never as a state's race.
const isHouse = (r) => (typeof r === "string" ? r : r?.race_id ?? "").startsWith("house:");
const statewide = () => races().filter((r) => !isHouse(r));
const houseRaces = () => [...races().filter(isHouse), ...(S.houseLive?.races.values() ?? [])];
const national = () => statewide().find((r) => !r.region);
const mainRace = () => national() ?? statewide()[0];
const regionName = (id) => S.meta.regions?.find((r) => r.id === id)?.name ?? (countryCode() === "US" && id ? usStateName(id) : id?.toUpperCase());
const raceBase = (r) => (r.region && !r.name.includes(regionName(r.region)) ? `${r.name}, ${regionName(r.region)}` : r.name);
const raceName = (r) => raceBase(r) + (r.civic ? " · secondary source (civicAPI)" : "");
const replay = () => S.entry?.status === "replay" && !LIVE && !PRE();
const outcomeOf = (r) => replay() ? outcomeA(S.rows?.get(r.race_id), S.stats?.settle?.[r.race_id]) : null;
// The Races table's result tag: the main race names its winner; any other race gets ✓ only when its first-named outcome resolved YES.
const winnerOf = (r) => { const w = outcomeOf(r); return r === mainRace() ? (winnerName(r, w) ? `— ${winnerName(r, w)} won` : "") : w === true ? "✓" : ""; };
const decisionOf = (r) => replay() && S.meta.poll_close_utc
  ? decidedBeforeCount(r, S.rows?.get(r.race_id), S.meta.poll_close_utc, S.stats?.settle?.[r.race_id])
  : null;
const racesIn = (region) => [...statewide(), ...(S.extra?.values() ?? [])].filter((r) => r.region === region);
// US: a race with a live county model on election night is one of the election's own races
// (meta.races) or a civicAPI race (secondary source, civic: its county count); the overview's other
// races (S.extra, carrying their overview row as .ov) have the county estimate, prices and polls only
const modelled = (r) => !!r && (r.civic || !r.ov) && !isHouse(r);
// every statewide race of an office ("sen" | "gov"), modelled or not, by name; a civicAPI race after
// the registered race of the same name
const officeRaces = (off) => [...statewide(), ...(S.extra?.values() ?? [])].filter((r) => office(r) === off).sort((a, b) => raceBase(a).localeCompare(raceBase(b)) || !!a.civic - !!b.civic);
// a race's row at time t: its timeline, else (estimate-only race) the overview's figures now
const raceRow = (r, t) => (r ? rowAt(r.race_id, t) ?? (r.ov ? extraRow(r) : null) : null);
// a state's line on election-night coverage (site/data/us_night_feeds.json, from
// docs/us_midterms.md "States checked"): live model, configured, or why there is none
function feedLine(st) {
  const f = S.feeds?.states?.[st], name = regionName(st);
  const civic = civicIn(st).length ? ` A secondary-source county count (civicAPI, civicapi.org) is also shown for ${name}.` : "";
  if (f?.status === "model") return `${name}: live county model on election night, from the state's official results feed.${civic}`;
  if (f?.status === "added") return `${name}: ${f.why}.${civic}`;
  return `No live county model on election night for ${name}: ${f?.why ?? "its official results feed was not among those checked for this project"}. The night shows market prices and the official count only.${civic}`;
}
// The race's office without its region ("tx-sen" -> "sen"; "house:TX-23" -> "house"), to keep the
// same office across regions.
const office = (r) => (isHouse(r) ? "house" : r?.region ? r.race_id.replace(/-civic$/, "").split(/[-_]/).filter((p) => p !== r.region).join("-") : r?.race_id);
// a race's timeline rows: the election's own, else the civicAPI races' (kept apart from S.rows),
// else the House count models'
const rowsOf = (id) => S.rows?.get(id) ?? S.civic?.rows.get(id) ?? null;
const seriesOf = (id) => rowsOf(id) ?? S.houseLive?.rows.get(id) ?? null;
// a district's count model at time t (null: no count model for it); following the latest, its
// own latest row (the House files refresh on their own minute, ahead of or behind the page's)
const districtModel = (id, t = S.preview ?? (S.followLatest ? Infinity : S.t)) => (id && seriesOf(`house:${id}`)?.length ? { row: rowAt(`house:${id}`, t), race: raceById(`house:${id}`) } : null);
const shortRaceName = (r) => {
  const s = r.name.replace(regionName(r.region), "").replace(/(\s+(in|of|for|the))+\s*$/i, "").replace(/^[\s,:–-]+|[\s,:–-]+$/g, "") || r.name;
  return s[0].toUpperCase() + s.slice(1) + (r.civic ? " (civicAPI)" : "");
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
  const asked = isHouse(param("race")) ? null : raceById(param("race")); // a district: selected once the House layer is up
  // ?region=sp opens that region (its race in the main race's office); ?level=regions|units the level
  const askedRegion = (meta.regions ?? []).some((r) => r.id === param("region")) ? param("region") : null;
  S.race = asked ?? (askedRegion ? racesIn(askedRegion).find((r) => office(r) === office(mainRace())) ?? racesIn(askedRegion)[0] : null) ?? mainRace() ?? null;
  // Open on a region only when one was asked for; elections without a national race open on the
  // overview of all regions with races.
  S.region = asked?.region ?? askedRegion;
  // units by default; the US national map (market-coloured states) opens at the state level
  S.countryLevel = meta.overview ? "regions" : "units"; // the country's level, and the other regions' while one is open
  S.level = ["regions", "units"].includes(param("level")) ? param("level") : S.region ? "units" : S.countryLevel;
}

// The "with polls" model of a race is meta.races[].poll_model ("v2", "v3"): its P_/share_/sd_
// fields are read into P_v2/share_v2/sd_v2, which the readout and chart show as "with polls".
const pollModel = (id) => raceById(id)?.poll_model ?? "v2";
function withPollModel(d, v) {
  if (v === "v2") return d;
  const share = d[`share_${v}`] ?? null; // its margin: from its two-party share where the row has no margin_<v>
  return { ...d, P_v2: d[`P_${v}`] ?? null, share_v2: share, sd_v2: d[`sd_${v}`] ?? null, margin_v2: d[`margin_${v}`] ?? (share != null ? (2 * share - 1) * 100 : null) };
}
const withTime = (rows, v = "v2") => rows.map((d) => ({ ...withPollModel(d, v), time: new Date(d.t), tms: Date.parse(d.t) }));
const timelineRows = (timeline) => new Map(Object.entries(timeline ?? {}).filter(([, rows]) => Array.isArray(rows)).map(([id, rows]) => [id, withTime(rows, pollModel(id))]));

// Cross-venue arbitrage (arb.json) covers the race window: there is none before the count, and the
// file is not written then (a request would only log a 404).
const loadArb = (opt) => (S.meta?.phase === "pre-count" ? Promise.resolve(null) : getJSON(`${EID}/arb.json`, opt));

// First load (and every live refresh without a tail file): the whole timeline.
async function loadSeries() {
  const opt = { optional: true };
  const [timeline, stats, arb] = await Promise.all([getJSON(`${EID}/timeline.json`, opt), getJSON(`${EID}/stats.json`, opt), loadArb(opt)]);
  S.rows = timelineRows(timeline);
  S.stats = stats;
  S.arb = arb;
  S.lastFetch = Date.now();
}

// The civicAPI secondary-source races: a separate process writes civic/us2026/{meta,timeline,
// timeline_tail}.json and units/*.json (the us2026 schema), served beside the election's files
// (<live>/civic/us2026/, data/civic/us2026/). Optional: before they exist the page is as without
// them. Their races are shown (map, state list, selector, races list, chart) but are never in
// meta.races and their rows never enter S.rows, so no registered-race summary, statistic, chart
// domain gate or Senate-control figure counts them; a state that also has a registered race keeps
// that one as its default and offers the civic race as an alternate (a chip in the state list).
// civicAPI requires attribution: the race name carries "secondary source (civicAPI)" and the
// readout, the Races/Method tabs link civicapi.org.
const CIVIC = "civic/us2026";
const CIVIC_SOURCE = { name: "civicAPI (civicapi.org)", url: "https://civicapi.org", role: "secondary", licence: "free to use; attribution required" };
// Looked for on the night (?live=) and once the phase is no longer pre-count; before the night the
// static page makes no request (no 404 in the log). ?civic=1 asks for them anyway (a preview).
const civicWanted = () => EID === "us2026" && (!PRE() || !!param("civic"));
const civicGet = (f) => getJSON(`${CIVIC}/${f}`, { optional: true }); // null when absent (404); throws on any other failure
const civicRows = (timeline, list = S.civic?.races ?? []) => new Map(Object.entries(timeline ?? {}).filter(([, rows]) => Array.isArray(rows)).map(([id, rows]) => [id, withTime(rows, list.find((c) => c.race_id === id)?.poll_model ?? "v2")]));
let civicMiss = 0, civicSkip = 0; // live: after a miss the next tries wait 1, 2, 4, 8, 10, 10… refreshes
async function loadCivic() {
  if (S.civic || !civicWanted()) return false;
  if (civicSkip > 0) { civicSkip--; return false; }
  let meta;
  try { meta = await civicGet("meta.json"); } catch { return false; } // an outage: tried again next time
  const taken = new Set(statewide().map((r) => r.race_id)); // a registered race is never replaced
  const races = (meta?.races ?? []).filter((c) => c.race_id && !isHouse(c) && !taken.has(c.race_id)).map((c) => ({
    ...c, civic: true, source: { ...CIVIC_SOURCE, ...c.source },
    units: c.units ? `../${CIVIC}/${c.units}` : null, // relative to the election's folder
  }));
  if (!races.length) { civicMiss++; civicSkip = Math.min(10, 2 ** (civicMiss - 1)); return false; }
  civicMiss = 0;
  const timeline = await civicGet("timeline.json").catch(() => null);
  S.civic = { meta, races, rows: civicRows(timeline, races) };
  S.civicNew = true; // the selector, state list and Method tab are rebuilt
  return true;
}
// The civic races beside the overview's estimate-only races (S.extra): the overview's entry of a
// state without a registered race (same id, e.g. "mi-sen": the civic race fills it in place, so
// the selection and its units follow), else a new entry ("oh-gov-civic": the alternate beside the
// registered Ohio race).
function registerCivic() {
  if (!S.extra || !S.civic) return;
  for (const c of S.civic.races) {
    const had = S.extra.get(c.race_id);
    if (had) Object.assign(had, c); else S.extra.set(c.race_id, { ...c });
  }
}
// Live refresh of the civic rows: the tail merged by time, the whole timeline after a gap; the held
// rows stay when a file cannot be read.
async function refreshCivic() {
  if (!civicWanted()) return;
  if (!S.civic) { if (await loadCivic()) registerCivic(); return; }
  let tail;
  try { tail = await civicGet("timeline_tail.json"); } catch { return; }
  const merged = tail ? mergeTail(S.civic.rows, civicRows(tail)) : null;
  if (merged) { S.civic.rows = merged; return; }
  const full = await civicGet("timeline.json").catch(() => null);
  if (full) S.civic.rows = civicRows(full);
}
// the civic race entry of a state: for the readout, the state list and the notes
const civicIn = (region) => S.civic?.races.filter((c) => c.region === region) ?? [];

// Live refresh: poll timeline_tail.json (each race's last 60 minutes) and merge it by `t`. The whole
// timeline is fetched again when the tail file is missing (404) or does not reach back to the last
// row held for some race (a gap longer than the tail window, e.g. after a long disconnect).
async function loadLiveSeries() {
  const opt = { optional: true };
  const [tail, stats, arb] = await Promise.all([getJSON(`${EID}/timeline_tail.json`, opt), getJSON(`${EID}/stats.json`, opt), loadArb(opt)]);
  const civic = refreshCivic();
  const merged = tail ? mergeTail(S.rows ?? new Map(), timelineRows(tail)) : null;
  // which file the last refresh used (window.__dashPerf.timelineFetch, for checks)
  S.perf.timelineFetch = merged ? "tail" : tail ? "full: gap longer than the tail" : "full: no tail file";
  S.rows = merged ?? timelineRows(await getJSON(`${EID}/timeline.json`, opt));
  S.perf.timelineHeld = Object.fromEntries([...S.rows].map(([id, rows]) => [id, [rows.length, rows[rows.length - 1]?.t ?? null]]));
  await civic;
  S.stats = stats;
  S.arb = arb;
  if (S.civicNew) civicShown();
  if (S.ev) await loadEarlyVote();
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

// The House district count models run in their own process and publish to <eid>/house_live/:
// meta.json (House races only), timeline.json + timeline_tail.json (keys "house:TX-23", the
// standard row fields), status.json. Read on the US page every HOUSE_LIVE_MS; before the House
// unit starts the files are absent and the House view stays the pre-count one. True when held
// rows changed.
const HOUSE_LIVE_MS = 60000;
let houseLiveMiss = 0, houseLiveSkip = 0; // consecutive missing reads; ticks still to skip
async function loadHouseLive() {
  // The folder does not exist before election night: no request while the election is pre-count (as
  // loadArb), and after a missing meta.json the next reads back off (1, 2, 4 ... 10 ticks) instead of
  // asking every minute.
  if (S.meta?.phase === "pre-count") return false;
  if (houseLiveSkip > 0) { houseLiveSkip--; return false; }
  const get = async (f) => {
    try { const r = await fetch(absUrl(`${EID}/house_live/${f}`), { cache: "no-cache" }); return r.ok ? await r.json() : null; } catch { return null; }
  };
  const meta = await get("meta.json");
  if (!meta) { houseLiveMiss++; houseLiveSkip = Math.min(10, 2 ** (houseLiveMiss - 1)); return false; }
  houseLiveMiss = 0;
  const list = (meta.races ?? []).filter(isHouse);
  if (!list.length) return false;
  const had = S.houseLive;
  S.houseLive = { races: new Map(list.map((r) => [r.race_id, r])), rows: had?.rows ?? new Map(), status: had?.status ?? null };
  // the tail merged onto the held rows; the whole timeline on the first read or after a gap
  const tail = had?.rows.size ? await get("timeline_tail.json") : null;
  const merged = tail ? mergeTail(had.rows, timelineRows(tail)) : null;
  const rows = merged ?? timelineRows(await get("timeline.json"));
  if (rows.size) S.houseLive.rows = rows;
  S.houseLive.status = await get("status.json");
  return rows.size > 0;
}
// after a House read: the district's readout and chart, the House races and statistics
function houseLiveShown() {
  if (houseShown()) { $("readout").innerHTML = districtReadout(S.houseSel, districtModel(S.houseSel)); drawChart(); }
  S.ov?.resize(); // refresh the House map's registered-model colours and hover figures
  if (S.office === "house" && S.rows) {
    if (!$("pane-races").querySelector(".hr-filter")?.value) buildRaceList(); // not under a reader's filter
    renderStatsTab();
  }
}

// Which units files the map shows: the selected race's own file, or units.json. With no national
// race and per-race files, the overview (no region chosen, or a race without its own file, e.g.
// before the count where a state has no county baseline) merges one race per region, keeping the
// selected race's office where a region has it.
function unitsPlan() {
  // the merged layer: no region open, or (before the count, and on the US page) a race without its
  // own county file
  if ((!S.region || ((PRE() || S.nat) && !S.race?.units)) && !national() && statewide().some((r) => r.units)) {
    const byRegion = d3.group(statewide().filter((r) => r.region && r.units), (r) => r.region);
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

const tIndex = new WeakMap();
// A readout row for an estimate-only race (S.extra), from the overview: the estimate, prices
// (Polymarket's live price where read) and margins.
function extraRow(r) {
  const o = r?.ov;
  if (!o) return null;
  const e = r.estimate ?? {}, m = o.margins ?? {};
  const pm = S.nat?.price(o.race_key) ?? o.pm?.price ?? null, kx = o.kalshi?.price ?? null;
  const mk = (p) => (m.market_sd_used && p > 0 && p < 1 ? probit(p) * m.market_sd_used : null);
  return {
    estimate: true, share: e.share ?? null, sd: e.sd ?? null, P: e.P ?? null, pm, kalshi: kx,
    margin_model: m.estimate ?? e.margin ?? null, margin_model_sd: e.margin_sd ?? null, margin_polls: m.polls ?? null,
    margin_market: mk(pm) ?? m.market ?? null, margin_market_kalshi: mk(kx) ?? m.market_kalshi ?? null,
  };
}
// Polls in the overview's poll average for a race (US only; null without a poll average).
function nPollsOf(r) {
  const id = r?.race_id;
  for (const off of Object.values(S.overview?.offices ?? {})) {
    const o = (off.races ?? []).find((x) => x.race_key === id || x.race_id === id);
    if (o) return o.polls?.n_polls ?? null;
  }
  return null;
}
// Row of a race's timeline at time t (last row at or before t). Time arrays are built once per load.
function rowAt(raceId, t) {
  const rows = seriesOf(raceId);
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
  const byRegion = new Map(statewide().filter((r) => r.region).map((r) => [r.region, r]));
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
  if (!S.levelInit) initLevel(); // the map level is set before the first frame of units
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
  const byRegion = d3.group(statewide().filter((r) => r.region), (r) => r.region);
  return [...byRegion].filter(([, rs]) => rs.every((r) => !r.units)).map(([reg]) => reg);
}

/* ---------------------------------------------------------------- map levels: regions <-> units */

// Every map with regions has two levels: whole regions coloured by their units' totals (or, on the
// US national map, by the market) and the units themselves. The way down is one path: the country
// at the region level -> click a region (zoom) -> that region's units; Back goes up one step.
const LEVEL_NAMES = {
  US: ["States", "Counties"], BR: ["States", "Municipalities"], CA: ["Provinces", "Ridings"],
  MX: ["States", "Districts"], HU: ["Counties", "Districts"], IL: ["Districts", "Localities"], NL: ["Provinces", "Municipalities"],
};
const levelNames = () => LEVEL_NAMES[countryCode()] ?? ["Regions", "Local units"];
const levelsOn = () => !!S.map && (!!S.nat || pickerRegions().length >= 2);

// Opacity targets for the map: nat = the open region's units over its fill, level = the other
// regions' units over theirs (see dash-map.js focus()).
function levelTargets(reg = S.region) {
  if (!levelsOn()) return { level: 1 };
  const u = S.level === "units";
  return reg ? { nat: u ? 1 : 0, level: S.countryLevel === "units" ? 1 : 0 } : { nat: 0, level: u ? 1 : 0 };
}

function initLevel() {
  S.levelInit = true;
  const on = levelsOn();
  S.map.setAggregate(on && !S.nat);
  if (on) S.map.setLevel({ ...levelTargets(), animate: false });
  setupLevels();
}

// The level switch in the toolbar above the map.
function setupLevels() {
  const box = $("map-level");
  box.hidden = !levelsOn();
  if (box.hidden) return;
  const [rn, un] = levelNames();
  box.innerHTML = [["regions", rn], ["units", un]].map(([l, name]) => `<button type="button" data-level="${l}" aria-pressed="${l === S.level}">${name}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setLevel(b.dataset.level)));
}

async function setLevel(l, { push = true } = {}) {
  if (l === S.level || !levelsOn()) return;
  S.level = l;
  if (!S.region) S.countryLevel = l;
  for (const b of $("map-level").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.level === l));
  if (push) syncUrl(true);
  S.hover = -1; S.hoverReg = null; placeCard();
  // the units shown at the unit level may need loading first (they fade in when bound)
  const ready = unitsPlan().key !== S.unitsKey ? reloadUnitsIfNeeded() : null;
  S.map.setLevel({ ...levelTargets(), ready });
  S.map.setNatHover(regionHover());
  showNational();
  S.legendKey = null;
  afterZoom(render);
  await ready;
}

// Hover and click pick whole regions at the region level of the whole country.
const regionHover = () => levelsOn() && !S.region && S.level === "regions";

// S.region with the level path: entering a region shows its units, leaving shows regions again.
function setRegionState(reg) {
  if (reg && S.overviewDefault) { S.overviewDefault = false; fillRaceSelect(); $("race").value = S.race?.race_id ?? ""; }
  if (reg && !S.region) { S.countryLevel = S.level; S.level = "units"; }
  else if (!reg && S.region) {
    S.level = S.countryLevel; // back to the country at the level it had
    // the US national view has no race chosen: the readout and chart return to the summary
    if (S.nat && !S.houseSel) { S.overviewDefault = true; fillRaceSelect(); $("readout").replaceChildren(); drawChart(); }
  }
  S.region = reg ?? null;
  for (const b of $("map-level").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.level === S.level));
}

// The address follows the view (race, region, level), so back/forward and shared links work.
function syncUrl(push) {
  if (S.fromPop) return;
  const url = new URL(location.href);
  // the one selection: a House district ("house:TX-07") or the race
  if (S.houseSel) url.searchParams.set("race", `house:${S.houseSel}`); else if (S.race && !S.overviewDefault) url.searchParams.set("race", S.race.race_id);
  if (S.region) url.searchParams.set("region", S.region); else url.searchParams.delete("region");
  if (levelsOn()) url.searchParams.set("level", S.level); else url.searchParams.delete("level");
  if (S.office === "house") url.searchParams.set("office", "house"); else url.searchParams.delete("office");
  if (url.href === location.href) return;
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
}

async function onPopState() {
  const q = new URLSearchParams(location.search);
  S.fromPop = true;
  try {
    const race = raceById(q.get("race"));
    const reg = (S.meta.regions ?? []).some((r) => r.id === q.get("region")) || racesIn(q.get("region")).length ? q.get("region") : null;
    if (race && race !== S.race) await selectRace(race);
    if (reg !== S.region) { if (reg) await selectRegion(reg); else await backToNational(); }
    const lvl = q.get("level");
    if ((lvl === "regions" || lvl === "units") && lvl !== S.level) await setLevel(lvl, { push: false });
  } finally { S.fromPop = false; }
}

// One region's totals for the hover card at the region level.
function regionCardHTML(reg) {
  const U = S.units, js = U && S.regionUnits?.get(reg)?.filter(layerOn);
  if (!js?.length) return `<p class="note">No results for this ${levelNames()[0].toLowerCase().replace(/s$/, "")} in this race.</p>`;
  const r = S.unitRace[js[0]];
  const k = frameIndex(S.preview ?? S.t);
  const g = regionAgg(U, k, js);
  const swing = g.raw != null && g.prior != null ? g.raw - g.prior : null;
  const sw = swing == null ? "–" : `${swing >= 0 ? "+" : "−"}${Math.abs(100 * swing).toFixed(1)} pt`;
  const an = esc(r.a.name);
  const rows = S.countTimeline
    ? [["Counted", pct(g.counted, 0)], [`${an}, votes counted`, pct(g.raw)], [`${an}, model projection`, pct(g.proj)], [`${an}, comparison election`, pct(g.prior)], ["Swing", sw]]
    : [[`${an}, result`, pct(g.raw)], [`${an}, comparison election`, pct(g.prior)], ["Swing", sw]];
  return `<table class="kv"><tbody>${rows.map(([a, x]) => `<tr><td>${a}</td><td class="n">${x}</td></tr>`).join("")}</tbody></table>
    <p class="pc-hint">${js.length.toLocaleString("en-US")} ${levelNames()[1].toLowerCase()}, weighted by expected vote. Click to open.</p>`;
}

// Legend counts at the current time: regions (region level) or units by side and, for the margin
// modes, by band (the cells of the band legend).
function legendCount() {
  const el = document.querySelector("#legend .legend-count");
  if (!el || !S.units) return;
  // margin1: counted on the past-results projection
  const U = S.mode === "margin1" ? { ...S.units, P: S.units.P1 } : S.units, k = frameIndex(S.preview ?? S.t);
  const atRegions = levelsOn() && S.level === "regions" && !S.region;
  const key = `${S.mode}|${k}|${atRegions}|${S.region}|${S.layer}`;
  if (key === S.countKey) return;
  S.countKey = key;
  if (!["margin", "margin1", "prior", "swing"].includes(S.mode)) { el.textContent = ""; return; }
  const r = S.mapRaces?.includes(S.race) ? S.race : S.unitRace[0];
  const half = (s) => (s == null ? null : s - 0.5);
  const val = (g) => (S.mode === "swing" ? (g.raw != null && g.prior != null ? g.raw - g.prior : null)
    : S.mode === "prior" || k < 0 ? half(g.prior) : half(S.countTimeline ? g.proj : g.raw ?? g.proj));
  let a = 0, b = 0, tie = 0;
  const cells = { a: [0, 0, 0, 0], b: [0, 0, 0, 0] };
  // a unit's projection reading exactly even takes the side of its finer figures (tieBreak, as
  // on the map); only an exact tie counts as tied
  const fine = S.mode === "margin" || S.mode === "margin1";
  const add = (v, j = -1) => {
    if (v == null || Number.isNaN(v)) return;
    let pt = 200 * v; // margin in points (side a minus side b)
    if (pt === 0 && fine && j >= 0) pt = tieBreak(U, k, j) * 1e-6;
    if (pt === 0) { tie++; return; }
    if (pt > 0) a++; else b++;
    cells[pt > 0 ? "a" : "b"][bandOf(pt)]++;
  };
  if (atRegions) for (const [, js] of S.regionUnits) { const g = regionAgg(U, k, js.filter(layerOn)); add(g.weight ? val(g) : null); }
  else for (let j = 0; j < U.n; j++) {
    if (!layerOn(j) || (S.region && U.region[j] !== S.region)) continue;
    add(val(regionAgg(U, k, [j])), j);
  }
  for (const c of document.querySelectorAll("#legend [data-band]")) c.textContent = cells[c.dataset.side][+c.dataset.band] || "";
  const what = (atRegions ? levelNames()[0] : layers().find((l) => l.id === S.layer)?.name ?? levelNames()[1]).toLowerCase();
  const verb = S.mode === "swing" ? "swing towards" : "ahead";
  // units with no data inside regions that have results (hatched on the map)
  let none = 0;
  if (!atRegions && S.featIdx) for (let i = 0; i < S.featIdx.length; i++) {
    if (S.featIdx[i] >= 0) continue;
    const reg = S.map.regionOf(i);
    if (S.regionUnits?.has(reg) && (!S.region || reg === S.region)) none++;
  }
  const noneText = none ? ` · <span class="lg-none"></span><b>${none}</b> ${what} without data` : "";
  el.innerHTML = a + b ? `<span style="color:${esc(r.a.color)}">${esc(r.a.name)}</span> ${verb} in <b>${a}</b> · <span style="color:${esc(r.b.color)}">${esc(r.b.name)}</span> in <b>${b}</b> ${what}${tie ? ` · tied in <b>${tie}</b>` : ""}${noneText}` : "";
}
// one unit, for "No data for this county"
const unitNoun = () => ({ Counties: "county", Municipalities: "municipality", Ridings: "riding", Districts: "district", Localities: "locality" })[levelNames()[1]] ?? "area";

// Margin modes: who is ahead, in four bands per side (as on YAPMS); counts filled by legendCount().
function bandLegend(root) {
  const r = S.mapRaces?.includes(S.race) ? S.race : S.unitRace[0];
  const row = (nm, col, s) => `<tr><th title="${esc(nm)}">${esc(nm)}</th>${bandColours(col).map((c, b) => `<td><i style="background:${c}"></i><b data-side="${s}" data-band="${b}"></b></td>`).join("")}</tr>`;
  root.html(`<table class="band-legend"><thead><tr><th></th>${BANDS.map((b) => `<th>${b}</th>`).join("")}</tr></thead><tbody>${row(r.a.name, r.a.color, "a")}${row(r.b.name, r.b.color, "b")}</tbody></table>
    <p class="legend-note">Who is ahead, by margin: tilt under 1 point, lean 1–5, likely 5–15, safe 15 or more. <i class="tie" style="background:${TIE}"></i> tied.</p>
    <p class="legend-count"></p>`);
}

/* ---------------------------------------------------------------- time */

function computeDomain() {
  let first = Infinity, end = -Infinity;
  for (const rows of [...S.rows.values(), ...(S.civic?.rows.values() ?? [])]) { // the civicAPI races move the time axis only
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
  // US, once counting has started: the election night (an hour before the first poll close, to now)
  // or the whole series ("Last 14 days")
  S.counting = !!S.nat && S.countTimeline && statewide().some((r) => (S.rows.get(r.race_id)?.at(-1)?.counted ?? 0) > 0);
  if (S.counting && S.range !== "all") {
    const closes = [S.meta.poll_close_utc, ...Object.values(S.meta.poll_close_by_region ?? {})].map(Date.parse).filter(Number.isFinite);
    if (closes.length) start = Math.min(end - MIN, Math.min(...closes) - 60 * MIN);
  }
  const old = S.domain;
  S.domain = [start, Math.max(end, start + MIN)];
  const off = tzOffsetMin(S.tz, start) * MIN;
  S.wall = (d) => new Date(+d + off);
  return !old || old[0] !== S.domain[0] || old[1] !== S.domain[1];
}

const clampT = (t) => Math.max(S.domain[0], Math.min(S.domain[1], t));

// Replays open where either the model or Polymarket first reached 90% on the
// eventual winner. A shared ?at=<ISO time> link takes precedence.
function defaultTime() {
  const explicit = Date.parse(param("at") ?? param("time") ?? "");
  if (Number.isFinite(explicit)) return clampT(explicit);
  if (!replay() || !S.countTimeline) return S.domain[1];
  const r = mainRace(), rows = S.rows.get(r?.race_id) ?? [], aWon = outcomeOf(r);
  const g = rows.findIndex((d) => d.counted >= 0.01);
  if (g < 0) return S.domain[1];
  // from 1% counted, the first minute from which the model or Polymarket stays at 0.90+ on the eventual winner
  const hits = ["model", "pm"].map((k) => staysAbove(rows, aWon, k, g)).filter(Boolean);
  return clampT(hits.length ? Math.min(...hits.map((d) => d.tms)) : rows[g].tms);
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
  if (PRE()) return [...(hasWinner() ? ["winner"] : []), "margin", ...pollModes(), ...remModes(), "counted", ...turnoutModes(), ...evModes()];
  const w = hasWinner() ? ["winner"] : [];
  if (!S.units.K) return [...w, "prior", ...evModes()];
  return [...w, ...(S.countTimeline ? ["margin", ...pollModes(), "swing", ...remModes(), "counted", ...turnoutModes()] : ["margin", "swing"]), ...evModes()];
}
// outstanding-vote modes, when the units carry rem / rem_margin
const remModes = () => (S.units?.rem ? ["rem", "remlean"] : []);
// turnout against the turnout model's expectation, where the units carry it (US modelled races)
const turnoutModes = () => (S.units?.turnout ? ["turnout"] : []);
// units with a poll model's projection (proj_poll): "margin" shows it, "margin1" the past results only
const pollModes = () => (S.units?.hasPoll ? ["margin1"] : []);
// early vote by county (earlyvote.json): display only, not used by the model
const evModes = () => (S.ev?.counties ? ["ev"] : []);
const modeLabel = (m) => ({
  winner: "Winning party", margin: PRE() ? `Before any votes${S.units?.hasPoll ? " (with polls)" : ""}` : S.countTimeline ? `Projected margin${S.units?.hasPoll ? " (with polls)" : ""}` : "Final margin",
  margin1: "Past results only", swing: "Swing from prior", rem: "Votes left", remlean: "Where the remaining votes lean", counted: "Counted", prior: "Comparison election", ev: "Early vote", turnout: "Turnout vs expected",
})[m];

/* ---------------------------------------------------------------- early vote (earlyvote.json) */

const stateName = (st) => S.meta.regions?.find((r) => r.id === st)?.name ?? usStateName(st);
// The file is published for the US only (other elections would only log a 404).
async function loadEarlyVote() {
  if (countryCode() !== "US") return;
  S.ev = await getJSON(`${EID}/earlyvote.json`, { optional: true }).catch(() => null);
  if (S.map) sendEarlyVote();
}
// per-feature shares for the engine's "ev" colours
function sendEarlyVote() {
  if (!S.ev?.counties || !S.map) return;
  S.map.setEarlyVote(Float32Array.from(S.map.ids, (id) => S.ev.counties[id]?.share_of_2022_total ?? NaN));
}
const evDay = (d) => (d ? fmtDate(d).replace(/ \d{4}$/, "") : "");
// "5.7M ballots, 70% of the 2022 total; by party registration D 39% · R 40% · other 21%"
function evText(x) {
  const bp = x.by_party, tot = bp ? (bp.D ?? 0) + (bp.R ?? 0) + (bp.O ?? 0) : 0;
  return `${votesShort(x.ev_total)} ballots, ${pct(x.share_of_2022_total, 0)} of the 2022 total${tot ? `; by party registration D ${pct(bp.D / tot, 0)} · R ${pct(bp.R / tot, 0)} · other ${pct(bp.O / tot, 0)}` : ""}`;
}
// The readout chip for a race's state ({ short, full }, plain text); null where the file says nothing.
function evLine(reg) {
  if (!S.ev || !reg) return null;
  const s = S.ev.states?.[reg];
  if (s?.totals) return { short: `${votesShort(s.totals.ev_total)} · ${pct(s.totals.share_of_2022_total, 0)} of 2022`, full: `Early vote so far in ${stateName(reg)} (as of ${evDay(s.as_of)}${s.stale ? ", not updated" : ""}): ${evText(s.totals)}. Display only; not used by the model.` };
  if (S.ev.not_available?.[reg]) return { short: "not available", full: `Early vote in ${stateName(reg)}: not available. ${S.ev.not_available[reg]}` };
  return null;
}

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
    const tween = S.playing && S.preview == null && S.mode !== "winner" && S.mode !== "ev" && k >= 0 && k + 1 < S.units.K;
    const key = `${S.mode}|${k}`;
    if (tween || key !== S.shownKey) {
      S.map.paint(S.mode, tt, tween);
      S.shownKey = tween ? null : key;
    }
    const lk = `${S.mode}|${S.mode === "winner" ? k : ""}|${S.race?.race_id}|${S.unitsKey}|${S.layer}|${S.mode === "winner" ? `${S.level}|${S.region}` : ""}`;
    if (lk !== S.legendKey) { S.legendKey = lk; S.countKey = null; renderLegend(); }
    legendCount();
    const nb = PRE() ? noBaseline().filter((reg) => !S.region || reg === S.region).map(regionName) : [];
    const remNote = remModes().includes(S.mode) ? `Circles: the model's estimate of the votes still to count${PRE() ? " (before the count: the whole expected vote)" : ""}.` : "";
    $("frame-label").textContent = S.mode === "ev" ? "Early vote: ballots cast before election day, from the states' daily reports. Display only; not used by the model."
      : S.mode === "turnout" ? `Turnout against the turnout model's expectation${k >= 0 && S.units.times[k] > 0 ? ` at the ${clock(S.units.times[k], S.tz)} frame` : ""}; hatched: under 95% reported, projected. Display only.`
      : PRE()
      ? remNote || (S.mode === "counted" ? `Nothing counted yet: counting starts at ${closeText(S.region)}`
        : S.mapRaces?.length === 1 && S.mapRaces[0].estimate ? `Before any votes. ${S.mapRaces[0].estimate.note}` // the data's own wording
        : S.units?.hasPoll && S.mode === "margin" ? "Before any votes: past certified results shifted toward the recent polls (the race's model with polls)."
        : `Before any votes: past certified results, not polls.${nb.length ? ` ${nb.join(", ")}: no county model yet; prices shown.` : ""}`)
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
  if (houseShown() && seriesOf(`house:${S.houseSel}`)) $("readout").innerHTML = districtReadout(S.houseSel, districtModel(S.houseSel));
  if (S.race && !houseShown() && !S.overviewDefault) {
    const pre = PRE() ? { day: dayOf(tt), note: S.race.model_note, live: S.liveNote } : null;
    const row = rowAt(S.race.race_id, tt) ?? extraRow(S.race);
    if (pre && S.race.estimate) pre.basis = S.race.estimate.note;
    const aWon = outcomeOf(S.race);
    const priced = [...(rowsOf(S.race.race_id) ?? [])].reverse().find((d) => d.tms <= tt && d.pm != null);
    renderReadout($("readout"), { race: S.race, row, t: tt, preview: S.preview != null, tz: S.tz, countTimeline: S.countTimeline, frameT: S.playing ? null : frameT, pre, ev: evLine(S.race.region), turnout: turnoutChip(row), nPolls: nPollsOf(S.race), settled: replay() && settledAt(priced ?? row, aWon), winner: replay() ? winnerName(S.race, aWon) : null });
    // the turnout chart follows the map's frame
    if (S.tlView === "turnout" && S.tlK !== k) drawChart();
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
    S.list.update(new Map([...races(), ...(S.extra?.values() ?? [])].map((r) => {
      const row = raceRow(r, t);
      return [r.race_id, { P: modelP(row), pm: row?.pm ?? null, k: row?.kalshi ?? null }];
    })));
  }
  updatePicker(t);
}

function renderLegend() {
  const root = d3.select("#legend").html("");
  if (!S.units) return;
  if (PRE() && S.region && noBaseline().includes(S.region)) return; // nothing coloured there yet
  // every legend ends with the hatch: units and regions without a figure
  const noData = () => root.append("p").attr("class", "legend-note").html(`<span class="lg-none"></span> no data: outside the race, or no figure yet`);
  if (S.mode === "winner") { partyLegend(root); noData(); return; }
  if (S.mode === "rem" || S.mode === "remlean") { remLegend(root); noData(); return; }
  if (S.mode === "ev") { evLegend(root); noData(); return; }
  if (S.mode === "turnout") { turnoutLegend(root); noData(); return; }
  if (S.mode === "margin" || S.mode === "margin1" || S.mode === "prior") { bandLegend(root); noData(); return; }
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
    root.append("p").attr("class", "legend-count"); // filled by legendCount()
  }
  noData();
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
  const pre = PRE() ? "Before any votes: all expected votes. " : "";
  root.append("p").attr("class", "legend-note").html(lean
    ? `${pre}Projected margin of the votes still to count: <span style="color:${esc(r.a.color)}">${esc(r.a.name)}</span> / <span style="color:${esc(r.b.color)}">${esc(r.b.name)}</span> ahead. Model estimate.`
    : `${pre}Votes still to count, a model estimate (not an official count of outstanding ballots).`);
}
// Turnout vs expected: brown below, teal above (as TURN_LUT in dash-engine.js), ±20% full colour;
// hatched while a county is under 95% reported (its figure is then projected).
function turnoutLegend(root) {
  const w = 200;
  root.append("svg").attr("viewBox", `0 0 ${w} 24`).attr("width", w).attr("height", 24).html(
    `<defs><linearGradient id="lg-turn">${d3.range(0, 11).map((i) => `<stop offset="${i / 10}" stop-color="${d3.interpolateBrBG(i / 10)}"/>`).join("")}</linearGradient></defs>
    <rect width="${w}" height="8" fill="url(#lg-turn)"/><text class="legend-text" x="0" y="20">−20%</text><text class="legend-text" x="${w / 2}" y="20" text-anchor="middle">as expected</text><text class="legend-text" x="${w}" y="20" text-anchor="end">+20%</text>`);
  root.append("p").attr("class", "legend-note").html(`Votes against the turnout model's expectation. <span class="lg-none"></span> under 95% reported: projected. Display only; not used by the model.`);
}
// hover rows for a unit's turnout (frame k)
function turnoutRows(U, k, j) {
  const T = U.turnout;
  if (!T || k < 0) return [];
  const r = T.ratio[k * U.n + j], e = T.exp[j], lp = T.lowprop[j];
  return [
    ...(r >= 0 ? [[`Turnout vs expected${U.F[k * U.n + j] < 950 ? " (projected)" : ""}`, `${r >= 1000 ? "+" : "−"}${Math.abs(r / 10 - 100).toFixed(1)}%`]] : []),
    ...(e > 0 ? [["Expected two-party votes", votesShort(10 * e)]] : []), // turnout_exp is in tens
    ...(Number.isFinite(lp) ? [["Low-propensity index", lp.toFixed(2)]] : []),
  ];
}
// Early vote: the share scale and, per state, the source and its date; states without figures.
function evLegend(root) {
  const w = 200, ramp = d3.interpolateLab("#ece9e1", "#3d5a40"); // as EV_LUT in dash-engine.js
  root.append("svg").attr("viewBox", `0 0 ${w} 24`).attr("width", w).attr("height", 24).html(
    `<defs><linearGradient id="lg-ev">${d3.range(0, 11).map((i) => `<stop offset="${i / 10}" stop-color="${ramp(i / 10)}"/>`).join("")}</linearGradient></defs>
    <rect width="${w}" height="8" fill="url(#lg-ev)"/><text class="legend-text" x="0" y="20">0%</text><text class="legend-text" x="${w}" y="20" text-anchor="end">100% of 2022 votes</text>`);
  const credits = Object.entries(S.ev.states ?? {}).map(([st, s]) =>
    `${esc(stateName(st))}: <a href="${esc(s.source?.url ?? "#")}">${esc((s.source?.name ?? "").split(",")[0])}</a>, ${esc(evDay(s.as_of))}${s.stale ? " (not updated)" : ""}`);
  const na = Object.keys(S.ev.not_available ?? {}).map(stateName);
  root.append("p").attr("class", "legend-note").html(`Ballots cast before election day as a share of the county's 2022 total. ${credits.join("; ")}.${na.length ? ` Not available: ${esc(na.join(", "))}.` : ""}`);
}
function partyLegend(root) {
  const k = frameIndex(S.preview ?? S.t);
  const counts = new Map();
  const atRegions = levelsOn() && S.level === "regions" && !S.region;
  if (atRegions) {
    // the region level: regions by the party leading most of their expected vote
    for (const [, js] of S.regionUnits) {
      const lead = regionWinner(S.units, js.filter(layerOn), (j) => winnerAt(k, j)[0]);
      if (lead) counts.set(lead.party, (counts.get(lead.party) ?? 0) + 1);
    }
  } else {
    for (let j = 0; j < S.units.n; j++) {
      if (!layerOn(j)) continue;
      const [p] = winnerAt(k, j);
      if (p != null) counts.set(p, (counts.get(p) ?? 0) + 1);
    }
  }
  const rows = [...counts].sort((a, b) => b[1] - a[1]);
  const party = (id) => S.meta.parties.find((p) => p.id === id) ?? { name: id, color: "#8a8a8a" };
  root.html(`<ul class="party-legend">${rows.map(([id, n]) => `<li><i style="background:${esc(party(id).color)}"></i>${esc(party(id).name)} <b>${n}</b></li>`).join("")}</ul>
    <p class="legend-note">${atRegions ? `${levelNames()[0]}: the party winning most of the expected vote in their ${levelNames()[1].toLowerCase()}.` : "Units won."} Paler: narrower win (full colour at a ${WIN_FULL * 100}-point margin).</p>`);
}

/* ---------------------------------------------------------------- map */

function startGeo() {
  if (!S.meta.geometry) return null;
  const key = S.meta.unit_key ?? "unit_id";
  const country = countryCode();
  S.country = country;
  return work("geo", { url: absUrl(S.meta.geometry, STATIC), objectName: S.meta.geometry_object, unitKey: key, country });
}

// Build geometry first and attach its neutral map for a frame; fetch/bind colour data after that.
// A slow data file therefore never holds the first map shape off screen. A worker that errors, has
// not started within ENGINE_TIMEOUT, or has not delivered within ENGINE_CAP hands over to the page.
async function buildMap() {
  let geo = null;
  if (S.meta.geometry) {
    const kind = E.kind;
    const timers = [];
    try {
      const job = startGeo();
      if (kind === "worker") {
        const limit = new Promise((_, reject) => {
          timers.push(setTimeout(() => { if (!E.readyAt) reject(new Error(`the map worker did not start within ${ENGINE_TIMEOUT / 1000} s`)); }, ENGINE_TIMEOUT));
          timers.push(setTimeout(() => reject(new Error(`the map worker did not deliver within ${ENGINE_CAP / 1000} s`)), ENGINE_CAP));
        });
        geo = await Promise.race([job, limit]);
      } else geo = await job;
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
      initMap(geo); // ResizeObserver sends a neutral geometry frame before units exist
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await loadUnits();
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
        const rr = reg ? S.nat.raceFor(reg) : null;
        if (rr) prefetchUnits([raceById(rr.race_id ?? rr.race_key)]);
        return;
      }
      // the region level: the whole region's card
      S.hoverReg = regionHover() ? reg : null;
      S.hover = S.hoverReg ? -1 : i;
      S.hoverPos = ev ? pointIn(ev) : null;
      placeCard();
      highlightPickerRow(reg);
    },
    onClick: (i, ev) => mapClick(i, ev),
    onFrame: (ms) => S.perf.transitionFrames.push(ms),
  });
  if (S.natLayer) S.map.setNational(...S.natLayer);
  sendEarlyVote();
  if (S.units) bindUnits();
}

// the US national map's own state layer and controls (at the state level)
const natShown = () => !!S.nat && !S.region && S.level !== "units";
const pointIn = (ev) => { const r = $("map").getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };

function mapClick(i, ev) {
  if (natShown()) { S.nat.pick(i >= 0 ? S.map.regionOf(i) : null); return; }
  if (regionHover()) {
    const reg = i >= 0 ? S.map.regionOf(i) : null;
    if (reg && pickerRegions().some((r) => r.id === reg)) { S.hoverReg = null; placeCard(); selectRegion(reg); }
    return;
  }
  // a click outside the open region (background, or a unit of another region that cannot be opened):
  // back to the whole country
  const reg0 = i >= 0 ? S.map.regionOf(i) : null;
  if (S.region && (i < 0 || (reg0 !== S.region && !(S.featIdx?.[i] >= 0 && pickerRegions().some((r) => r.id === reg0))))) {
    S.pinned = -1; S.map.pin(-1); placeCard(); backToNational(); return;
  }
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
  const reg = S.region && S.map?.hasRegion(S.region) && (pickerRegions().some((r) => r.id === S.region) || noBaseline().includes(S.region) || S.race?.region === S.region) ? S.region : null;
  S.map?.focus(reg, { animate, ready, ...levelTargets(reg) });
  S.map?.setNatHover(regionHover() || natShown());
  const ext = $("extent");
  ext.hidden = !S.map?.hasPopulatedView || !!reg;
  ext.textContent = S.map?.extent === "populated" ? "Whole map" : "Populated areas";
  renderCrumbs();
  showNational();
  for (const row of document.querySelectorAll(".rp-row")) row.classList.toggle("sel", row.dataset.region === S.region);
}

// Release the pinned unit and clear the hover state (office or layer changes, Esc, the card's ×).
function releasePin() {
  S.pinned = -1; S.hover = -1; S.hoverReg = null;
  S.map?.pin(-1);
  placeCard();
}

function placeCard() {
  const el = $("place");
  // the House / census layer covers the county map and has its own cards: this one never shows over it
  if (S.ov?.kind) { el.hidden = true; el.classList.remove("pinned"); return; }
  el.classList.toggle("pinned", S.pinned >= 0);
  const i = S.pinned >= 0 ? S.pinned : S.hover;
  if (S.hoverReg && S.pinned < 0 && S.map) {
    el.innerHTML = `<p class="pc-name"><b>${esc(regionName(S.hoverReg))}</b></p>${regionCardHTML(S.hoverReg)}`;
    el.hidden = false;
    positionCard(el, S.hoverPos);
    return;
  }
  if (i < 0 || !S.map) { el.hidden = true; return; }
  const j = S.featIdx?.[i] ?? -1;
  let body;
  const U = S.units;
  if (S.mode === "ev" && S.ev) {
    const x = S.ev.counties?.[S.map.ids[i]], st = S.map.regionOf(i);
    const bp = x?.by_party, tot = bp ? (bp.D ?? 0) + (bp.R ?? 0) + (bp.O ?? 0) : 0;
    body = x ? `<table class="kv"><tbody><tr><td>Early vote so far</td><td class="n">${votesShort(x.ev_total)}</td></tr><tr><td>Share of the 2022 total</td><td class="n">${pct(x.share_of_2022_total, 0)}</td></tr>${x.mail_returned != null ? `<tr><td>By mail / in person</td><td class="n">${votesShort(x.mail_returned)} / ${votesShort(x.in_person)}</td></tr>` : ""}${tot ? `<tr><td>By party registration</td><td class="n">D ${pct(bp.D / tot, 0)} · R ${pct(bp.R / tot, 0)} · other ${pct(bp.O / tot, 0)}</td></tr>` : ""}</tbody></table><p class="pc-hint">As of ${esc(evDay(S.ev.states?.[st]?.as_of))}; display only, not used by the model.</p>`
      : `<p class="note">${S.ev.not_available?.[st] ? "Early vote: not available for this state." : "No early-vote figures for this county."}</p>`;
  } else if (j < 0 || !U) body = `<p class="note">${U && S.regionUnits?.has(S.map.regionOf(i)) ? `No data for this ${unitNoun()}.` : "Not part of this election's results."}</p>`;
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
      ? [...winRow, ["Counted", pct(counted, 0)], [`${an}, votes counted`, pct(a)], [`${an}, model projection${U.hasPoll ? " (with polls)" : ""}`, pct(proj)], ...(U.hasPoll ? [[`${an}, past results only`, pct(at(U.P1))]] : []), [`${an}, comparison election`, pct(prior)], ["Swing", sw], ...remRows, ...turnoutRows(U, k, j)]
      : [...winRow, [`${an}, result`, pct(a)], [`${an}, comparison election`, pct(prior)], ["Swing", sw]];
    body = `<table class="kv"><tbody>${rows.map(([k2, x]) => `<tr><td>${k2}</td><td class="n">${x}</td></tr>`).join("")}</tbody></table>`;
  }
  const name = (j >= 0 && U?.names[j]) || S.map.names[i] || S.map.ids[i];
  const reg = S.map.regionOf(i);
  el.innerHTML = `<p class="pc-name"><b>${esc(name)}</b>${reg && !String(name).includes(`, ${reg.toUpperCase()}`) ? `, ${esc(regionName(reg))}` : ""}</p>${body}
    <p class="pc-hint">${S.pinned >= 0 ? "Pinned. Click it again, press Esc or use × to release." : "Click to pin."}</p>${S.pinned >= 0 ? `<button type="button" class="pc-close" aria-label="Release the pinned card">×</button>` : ""}`;
  el.hidden = false;
  positionCard(el, S.pinned >= 0 ? S.pinPos : S.hoverPos);
}

function positionCard(el, at) {
  const box = $("map").getBoundingClientRect();
  const pos = at ?? [box.width / 2, box.height / 2];
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
  S.mapRegions ??= S.map ? new Set(S.map.ids.map((id) => id.split("-")[1])) : null;
  if (S.nat) {
    // US: every state with a race of the office shown, those with a live county model first
    const off = OFFICE_ID[S.office] ?? office(S.race) ?? "sen";
    const by = d3.group(officeRaces(off), (r) => r.region);
    const list = [...by].filter(([id]) => !S.mapRegions || S.mapRegions.has(id))
      .map(([id, rs]) => ({ id, name: regionName(id), live: rs.some((r) => modelled(r) && !r.civic) ? 2 : rs.some((r) => r.civic) ? 1 : 0 }))
      .sort((a, b) => b.live - a.live || a.name.localeCompare(b.name));
    return list.length >= 2 ? list : [];
  }
  const withRaces = new Set(statewide().filter((r) => r.region).map((r) => r.region));
  const list = (S.meta.regions ?? []).filter((r) => (withRaces.has(r.id) || S.units?.region?.includes(r.id)) && (!S.mapRegions || S.mapRegions.has(r.id)));
  return list.length >= 2 ? list : [];
}

// Breadcrumb "World › Brazil › São Paulo" and a Back button one step up (Esc does the same
// within the election).
function renderCrumbs() {
  const el = $("crumbs");
  const top = countryName();
  const world = `<a class="crumb-link" href="./?from=${encodeURIComponent(EID)}" id="crumb-world">World</a> <span aria-hidden="true">›</span>`;
  // the House or census layer zoomed to a state: back to the whole country on that layer
  const ovState = S.ov?.kind ? S.ov.state : null;
  const inside = S.region ?? ovState;
  const up = () => (ovState ? S.ov.focus(null) : backToNational());
  const back = `<button type="button" class="crumb-back" id="crumb-back" title="${inside ? "Esc" : "Back to the world map"}">‹ Back</button>`;
  el.innerHTML = inside
    ? `${back} ${world} <button type="button" class="crumb-link" id="crumb-top">${esc(top)}</button> <span aria-hidden="true">›</span> <b>${esc(regionName(inside))}</b>${S.houseSel ? ` <span aria-hidden="true">›</span> <b>${esc(S.houseSel)}</b>` : ""}`
    : `${back} ${world} <b>${esc(top)}</b>`;
  $("crumb-top")?.addEventListener("click", up);
  $("crumb-back").addEventListener("click", () => (inside ? up() : toWorld()));
  $("crumb-world").addEventListener("click", (ev) => { if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); toWorld(); } });
}

// Up from the country: a short fade, then the world map opens framed on this country and zooms out.
function toWorld() {
  const url = `./?from=${encodeURIComponent(EID)}`; // the world map sits right under the header
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || crossDocTransitions()) { location.href = url; return; }
  document.body.classList.add("leaving");
  setTimeout(() => { location.href = url; }, 220);
}
window.addEventListener("pageshow", (ev) => { if (ev.persisted) document.body.classList.remove("leaving"); });

function buildPicker() {
  const el = $("regions");
  const list = pickerRegions();
  el.hidden = !list.length;
  if (!list.length) return;
  el.innerHTML = `<p class="rp-head"><span>${S.nat ? "State" : "Region"}</span><span>${PRE() ? "Polls close" : S.countTimeline ? "Counted" : ""}</span><span title="Model probability or county estimate (orange dot), Polymarket price (black line) and Kalshi price (green line) for the region's race, 0 to 1">Chance</span></p>
    <div class="rp-list" role="listbox" aria-label="Regions">${list.map((r, i) => {
      const rs = racesIn(r.id);
      const chips = rs.length > 1 ? `<span class="rp-races">${rs.map((x) => `<button type="button" class="rp-chip" data-race="${esc(x.race_id)}">${esc(shortRaceName(x))}</button>`).join("")}</span>` : "";
      // US: the states with a live county model, then those with a civicAPI count only, then those with the estimate only
      const group = r.live != null && (i === 0 || list[i - 1].live !== r.live)
        ? `<div class="rp-group" role="presentation">${r.live === 2 ? `<span class="mk-live" aria-hidden="true"></span> Live county model` : r.live === 1 ? `<span class="mk-live" aria-hidden="true"></span> County count, secondary source <small>(<a href="${CIVIC_SOURCE.url}" target="_blank" rel="noopener">civicAPI</a>)</small>` : "Estimate only <small>(prices, polls, county estimate)</small>"}</div>` : "";
      return `${group}<div class="rp-row" role="option" tabindex="0" data-region="${esc(r.id)}" aria-selected="${r.id === S.region}">
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
    const race = regionRace(reg), r = raceRow(race, t);
    if (S.countTimeline && r?.counted != null) c = r.counted; // the region's own race, when it has one
    row.children[1].textContent = PRE() ? untilText(closeOf(reg) - Date.now(), true) ?? "closed" : c == null ? "" : pct(c, 0);
    const ind = row.children[2];
    const m = modelP(r), pm = r?.pm ?? null, kx = r?.kalshi ?? null;
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
  // a national race whose units cover the region stays selected (its units there); else the
  // region's own race in the same office
  const keep = S.race && !S.race.region && S.units?.region?.includes(reg);
  const race = keep ? null : regionRace(reg);
  if (race) return selectRace(race);
  setRegionState(reg);
  syncUrl(true);
  await moveMap();
}

async function backToNational() {
  if (!S.region) return;
  if (national() && S.race !== national()) return selectRace(national());
  setRegionState(null);
  if (S.rows) buildRaceList();
  syncUrl(true);
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

// keepRegion: a race without a region (national) stays on the open region (its units there).
async function selectRace(r, { keepRegion = false } = {}) {
  if (!r) return;
  if (isHouse(r)) { selectKey(r.race_id); return; } // a district's count model: the district selection
  const wasDefault = S.overviewDefault;
  if (S.overviewDefault) { S.overviewDefault = false; fillRaceSelect(); $("race").value = r.race_id; } // a race is chosen: the placeholder goes
  // the one selection: a race chosen anywhere (selector, list, map, tab) replaces a selected district
  clearDistrict();
  // a Senate or governor race chosen while the House layer shows: back to that office first
  if (S.ov?.kind) { S.censusLayer = null; const sel = document.querySelector(".nat-layer select"); if (sel) sel.value = "races"; }
  if (S.office === "house") S.nat.setOffice(office(r) === "gov" ? "governor" : "senate");
  else if (S.ov?.kind) { S.ov.hide(); showNational(); }
  const sameRace = r === S.race && !wasDefault;
  S.race = r;
  const reg = r.region ?? (keepRegion ? S.region : null);
  const moved = reg !== S.region;
  setRegionState(reg);
  if (S.rows) buildRaceList();
  if (!sameRace || moved) syncUrl(moved);
  if (!sameRace) {
    $("race").value = r.race_id;
    S.list?.select(r.race_id);
    S.legendKey = null;
    // the chart and statistics redraw when the map's zoom leaves the main thread idle
    afterZoom(() => { if (S.race !== r) return; drawChart(); updateScrubber(); renderStatsTab(); });
  }
  await moveMap();
}

function chartHeight() {
  return WIDE.matches ? 220 : 240;
}

// Turnout of the selected race's counties at the map's frame (units with turnout fields, US
// modelled races): reported counties as points, a sentence on the high low-propensity third, and
// before any county reports, the expected turnout. null where the race has no turnout figures.
function turnoutView() {
  const U = S.units, T = U?.turnout;
  if (!T || !S.unitRace?.includes(S.race)) return null;
  const k = frameIndex(S.preview ?? S.t);
  const row = rowAt(S.race.race_id, S.preview ?? S.t);
  const pts = [];
  if (k >= 0) for (let j = 0; j < U.n; j++) {
    if (S.unitRace[j] !== S.race) continue;
    const i = k * U.n + j, r = T.ratio[i], e = T.exp[j], lp = T.lowprop[j];
    if (U.F[i] < 950 || !(r > 0) || !(e > 0) || !Number.isFinite(lp)) continue;
    pts.push({ x: lp, y: Math.log(r / 1000), w: e, lean: T.priorMargin[j], name: U.names[j] ?? U.unit_ids[j] });
  }
  let sentence = null;
  if (pts.length >= 6) {
    const hi = [...pts].sort((p, q) => q.x - p.x).slice(0, Math.ceil(pts.length / 3));
    const W = d3.sum(hi, (p) => p.w), t = d3.sum(hi, (p) => p.w * Math.exp(p.y)) / W - 1;
    const lean = d3.sum(hi.filter((p) => Number.isFinite(p.lean)), (p) => p.w * p.lean) / d3.sum(hi.filter((p) => Number.isFinite(p.lean)), (p) => p.w) / 10;
    sentence = `High low-propensity counties are turning out ${Math.abs(100 * t).toFixed(0)}% ${t >= 0 ? "above" : "below"} expected${Number.isFinite(lean) ? `; they lean ${marginText(lean)}` : ""}.`;
  } else if (pts.length) sentence = "Too few counties have reported to compare yet.";
  const exp = row?.turnout_exp_total, vs22 = S.race.precount?.turnout_exp_vs_2022;
  const pre = `Expected turnout: ${exp != null ? `${votesShort(exp)} two-party votes` : "not available"}${vs22 != null ? ` (${vs22 >= 0 ? "+" : "−"}${Math.abs(vs22).toFixed(0)}% vs 2022)` : ""}. The chart fills in as counties report.`;
  return { pts, k, row, sentence, pre };
}

// The strip's turnout chip: the race's projected turnout against expected, with counties reported.
function turnoutChip(row) {
  // turnout_vs_expected: projected ÷ expected two-party votes, in % (100 = as expected)
  const v = row?.turnout_vs_expected == null ? null : row.turnout_vs_expected - 100;
  if (v == null) return null;
  // before the count the figure only compares two expectations: show the expected turnout instead
  if (PRE()) return row.turnout_exp_total != null ? { text: `${votesShort(row.turnout_exp_total)} expected`, title: `The turnout model expects ${votesShort(row.turnout_exp_total)} two-party votes. Turnout against it fills in as counties report.` } : null;
  const U = S.units, k = frameIndex(S.preview ?? S.t);
  let n = 0, rep = 0;
  if (U?.turnout && k >= 0) for (let j = 0; j < U.n; j++) if (S.unitRace[j] === S.race) { n++; if (U.F[k * U.n + j] >= 950) rep++; }
  return { text: `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}% vs expected`, title: `Projected ${votesShort(row.turnout_proj_total)} two-party votes against ${votesShort(row.turnout_exp_total)} expected${n ? `; ${rep} of ${n} counties reported (95% or more counted)` : ""}${row.margin_hot != null ? `. Counties above expected turnout lean ${marginText(row.margin_hot)}, those below ${marginText(row.margin_cold)}` : ""}.` };
}
// An estimate-only race (no live county model): its history from the overview (Polymarket and
// Kalshi every 30 minutes, read as margins through the estimate's sd) with the poll average by day
// (hub polls.json, once loaded) and the county estimate as the last point.
function extraSeries(r) {
  const o = r.ov, ph = S.hubPolls?.races?.[r.race_id]?.history ?? [];
  if (!o?.history?.length) return [];
  const sd = o.margins?.market_sd_used ?? null;
  const imp = (p) => (sd != null && p > 0 && p < 1 ? probit(p) * sd : null);
  let j = -1;
  const rows = o.history.map(([t, pm, k]) => {
    const tms = Date.parse(t);
    while (j + 1 < ph.length && Date.parse(ph[j + 1][0]) <= tms) j++;
    return { t, time: new Date(tms), tms, pm: pm ?? null, kalshi: k ?? null, margin_market: imp(pm), margin_market_kalshi: imp(k), margin_polls: j >= 0 && ph[j][1] != null ? (2 * ph[j][1] - 1) * 100 : null };
  });
  const e = o.estimate;
  if (e) rows[rows.length - 1] = { ...rows[rows.length - 1], margin_estimate: e.margin ?? null, margin_estimate_sd: e.margin_sd ?? null, P_estimate: e.P ?? null };
  return rows;
}

// One chart with a Margin | Chance (| Turnout) toggle (Margin by default where the race has a
// model share or margin); the explanation sits behind the "?" button.
function drawChart() {
  if (S.overviewDefault && !houseShown()) { // no race chosen: the national summary shows, no race chart
    S.tl = null;
    $("timeline").replaceChildren();
    $("tl-view").hidden = true; $("tl-feed").hidden = true;
    return;
  }
  if (houseShown()) { $("tl-feed").hidden = true; districtChart($("timeline"), S.houseSel, districtModel(S.houseSel)); return; } // a House district: its chances
  const extra = !!S.race.ov && !rowsOf(S.race.race_id)?.length;
  if (extra && S.nat && !S.hubPolls) {
    // the poll average's history: from the hub's polls file, then this chart again
    const r = S.race;
    (S.hub ??= loadUsHub()).then((H) => { S.hubPolls = H.polls ?? { races: {} }; if (S.race === r && !houseShown()) drawChart(); }).catch(() => { S.hubPolls = { races: {} }; });
  }
  const rows = extra ? extraSeries(S.race) : rowsOf(S.race.race_id) ?? [];
  const tv = turnoutView();
  // live: the Turnout view is offered from page load (disabled until the county turnout data arrives)
  const tvWait = !tv && LIVE && !PRE() && !!S.race.units;
  const views = [...timelineViews(rows), ...(tv || tvWait ? ["turnout"] : [])];
  if (!views.includes(S.tlView) || (S.tlView === "turnout" && !tv)) S.tlView = views[0];
  const rb = $("tl-range");
  rb.hidden = !S.counting;
  if (S.counting) {
    rb.innerHTML = [["night", "Night"], ["all", "Last 14 days"]].map(([k, l]) => `<button type="button" data-range="${k}" aria-pressed="${(S.range ?? "night") === k}">${l}</button>`).join("");
    rb.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      S.range = b.dataset.range;
      const follow = S.followLatest;
      computeDomain();
      S.t = follow ? S.domain[1] : clampT(S.t);
      drawChart(); updateScrubber(); render();
    }));
  }
  const box = $("tl-view");
  box.hidden = views.length < 2;
  box.innerHTML = views.map((v) => `<button type="button" data-view="${v}" aria-pressed="${v === S.tlView}"${v === "turnout" && !tv ? ` disabled title="Fills in at the first refresh"` : ""}>${{ margin: "Margin", chance: "Chance", turnout: "Turnout" }[v]}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { S.tlView = b.dataset.view; drawChart(); }));
  S.tlK = tv?.k;
  S.tl = S.tlView === "turnout"
    ? drawTurnout($("timeline"), { pts: tv.pts, height: chartHeight(), slope: tv.row?.turnout_surprise_slope ?? null, corr: tv.row?.turnout_surprise_corr ?? null, pre: tv.pre, sentence: tv.sentence })
    : drawTimeline($("timeline"), {
      race: S.race, rows, domain: S.domain, tz: S.tz, wall: S.wall, height: chartHeight(), countPanel: S.countTimeline,
      view: S.tlView, us: countryCode() === "US",
      onPreview: (t) => { S.preview = t; render(); },
      onSet: (t) => { pause(); S.preview = null; setTime(t); },
    });
  const g = S.gateCut;
  const a = esc(S.race.a.name), b = esc(S.race.b?.name ?? "");
  const what = extra
    ? (S.tlView === "margin"
      ? "Margin, Democratic minus Republican, in points: the poll average, the market-implied margins (Polymarket, Kalshi: the price read through the estimate's uncertainty) and the county estimate now (dot, with its 90% range)."
      : `Chance that ${a} wins: Polymarket and Kalshi prices, and the county estimate now (dot).`)
    : S.tlView === "margin"
    ? `Margin, ${countryCode() === "US" ? "Democratic minus Republican" : `${a} minus ${b}`}, in points: the model (shaded: its 90% band), the raw count once counting starts${countryCode() === "US" ? ", the polls" : ""} and the market-implied margin (the margin at which the model's uncertainty gives the market's price).`
    : S.tlView === "turnout"
      ? "Turnout: each county that has reported (95% or more counted) as a dot, its votes against the turnout model's expectation (up: more than expected), by its low-propensity index (right: a bigger expected midterm drop-off), sized by expected votes; the line is the weighted trend at the map's frame. Display only; the model's projection does not use it."
      : `Chance that ${a} wins: the model and the market prices.`;
  const axis = PRE()
    ? "Before the count: prices every 30 minutes over the last 14 days, and Polymarket's current price as the last point."
    : g ? `Axis starts at ${clock(g.start, S.tz)}: from ${S.meta.poll_close_utc ? `poll close (${clock(S.meta.poll_close_utc, S.tz)})` : "the first row"} to ${clock(g.gate, S.tz)} under 1% of the vote was counted${g.maxBefore != null ? ` (at most ${pct(g.maxBefore)})` : ""}.` : "";
  $("tl-note").innerHTML = `${what} ${axis}`;
  // a race without a live county model: one plain line under the chart on why
  const feed = extra && S.race.region ? feedLine(S.race.region) : "";
  $("tl-feed").hidden = !feed;
  $("tl-feed").textContent = feed;
  S.tl.setCursor(S.t, S.preview);
}

const gateOf = (id) => (S.countTimeline ? rowsOf(id)?.find((d) => d.counted >= 0.01)?.t ?? null : null);
const settleOf = (id) => gatedSettle(S.stats, id, gateOf(id));

function updateScrubber() {
  const id = S.race?.race_id;
  const rows = rowsOf(id) ?? [];
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
    // a pinned unit is released first, whatever else is open
    if (S.pinned >= 0) releasePin();
    // the House or census layer zoomed to a state: back to the country
    if (S.ov?.kind && S.ov.state) { S.ov.focus(null); return; }
    // inside a region the same press also returns to the whole country
    backToNational();
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

function usTitle() {
  if (!S.meta.overview || countryCode() !== "US") return null;
  const n = new Set(statewide().filter((r) => r.region && r.units).map((r) => r.region)).size;
  return `US midterms ${S.meta.date?.slice(0, 4) ?? ""}: every Senate and governor race${n ? `, live county models in ${n} state${n === 1 ? "" : "s"}` : ""}, House districts live`.replace("midterms : ", "midterms: ");
}
function renderTopbar() {
  const m = S.meta, e = S.entry;
  // the US title is derived from the data (how many states have a live county model), not from elections.json
  const title = usTitle() ?? m.name;
  document.title = `${title}: election night`;
  $("title").textContent = title;
  $("date").textContent = fmtDate(m.date);
  // the evidence label lives in the Statistics and Method & sources tabs; the bar only says live/scheduled
  // the chip follows the meta's phase (elections.json may still say "scheduled")
  const phase = { "pre-count": ["pre", "Before the count"], live: ["live", "Counting"], final: ["final", "Final"] }[m.phase];
  $("evidence").innerHTML = phase ? `<span class="status ${phase[0]}">${phase[1]}</span>${m.phase === "pre-count" ? ` <span id="countdown"></span>` : ""}` : PRE() ? `<span class="status pre">Before the count</span> <span id="countdown"></span>`
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

// The tab set under the chart. For the US the polls, pollsters, markets and track-record tabs
// (dash-us-tabs.js) join it, drawn for the selected race or state (all races at the country level).
const US_TABS = ["polls", "pollsters", "markets", "track"];
function initTabs() {
  if (S.nat) for (const t of US_TABS) $(`tab-${t}`).hidden = false;
  const tabs = [...document.querySelectorAll('[role="tab"]')].filter((t) => !t.hidden);
  // a tab change keeps the page where it is: the panes hold their height until the new content is
  // in, and the window's scroll is put back
  const show = (t) => {
    const panes = document.querySelector(".tab-panes"), y = window.scrollY;
    if (panes) panes.style.minHeight = `${panes.offsetHeight}px`;
    tabs.forEach((x) => {
      const on = x === t;
      x.setAttribute("aria-selected", String(on));
      x.tabIndex = on ? 0 : -1;
      $(x.getAttribute("aria-controls")).hidden = !on;
    });
    S.tab = t.id.replace(/^tab-/, "");
    Promise.resolve(refreshUsTab()).finally(() => requestAnimationFrame(() => {
      if (panes) panes.style.minHeight = "";
      if (window.scrollY !== y) window.scrollTo(window.scrollX, y);
    }));
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
  show(tabs.find((t) => t.id === `tab-${param("tab")}`) ?? tabs[0]);
}

// the open US tab, for the selected House district, race or state (null: the whole country)
function refreshUsTab() {
  if (!S.nat || !US_TABS.includes(S.tab)) return null;
  const el = $(`pane-${S.tab}`);
  const house = S.office === "house";
  const state = house ? S.ov?.state ?? null : S.region;
  // (a civicAPI race is the same contest as its registered or overview race: the polls and markets tabs key on that race)
  const race = house ? (S.houseSel ? `house:${S.houseSel}` : null) : S.region && S.race ? S.race.ov?.race_key ?? S.race.race_id.replace(/-civic$/, "") : null;
  const key = `${S.tab}|${race}|${state}`;
  if (key === S.usTabKey && el.childElementCount) return null;
  S.usTabKey = key;
  return (S.hub ??= loadUsHub()).then((H) => {
    if (S.usTabKey !== key) return;
    el.innerHTML = "";
    renderUsTab(H, S.tab, el, { race, state, onRace: (k) => openUsRace(k) });
  }).catch((err) => { el.innerHTML = `<p class="note">Could not load: ${esc(err.message)}</p>`; });
}
// a race clicked in a US tab: the same selection as the selector ("house:TX-07" or a race id)
const openUsRace = (k) => selectKey(k);

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
  // House: the district count models' statistics (the selected district's, the others listed);
  // before any district has a count model, a note
  if (S.office === "house") {
    const hr = houseRaces();
    if (!hr.length) { $("pane-stats").innerHTML = `<p class="note">The statistics (accuracy, calibration, market comparison, profit) are computed per race once the count is over, for the races with a county count model. No House district has one in this election's files yet: their markets, polls and the model are in the Races, Polls and Markets tabs.</p>`; return; }
    const race = raceById(`house:${S.houseSel}`) ?? hr[0];
    renderStats($("pane-stats"), { stats: S.stats, race, races: hr, raceName, tz: S.tz, arb: S.arb, settleOf, profitOf });
    return;
  }
  // a civicAPI race has no statistics: they are the registered races' (calibration, settling, profit)
  if (S.race?.civic) { $("pane-stats").innerHTML = `<p class="note">${esc(raceName(S.race))}: the statistics (accuracy, calibration, market comparison, profit) are computed for the registered races only, which use the states' official feeds. This race's count comes from <a href="${CIVIC_SOURCE.url}" target="_blank" rel="noopener">${esc(CIVIC_SOURCE.name)}</a>, a secondary source, and is kept out of them.</p>`; return; }
  renderStats($("pane-stats"), { stats: S.stats, race: S.race, races: statewide(), raceName, tz: S.tz, arb: S.arb, settleOf, profitOf });
}

// Method & sources tab: the election's own notes and sources, plus (civicAPI races loaded) the
// civic meta's, with civicAPI's attribution; entries already listed are not repeated.
function drawMethod() {
  const twoModels = [...S.rows.values()].some((rows) => rows.some((d) => d.P_v2 != null)) || races().some((r) => r.precount?.[`P_${r.poll_model ?? "v2"}`] != null);
  const margins = [...S.rows.values()].some((rows) => rows.some((d) => d.margin_market != null || d.margin_model != null));
  let meta = S.meta;
  if (S.civic) {
    const cm = S.civic.meta ?? {};
    const names = new Set((meta.sources ?? []).map((s) => s.name)), notes = new Set(meta.notes ?? []);
    const own = (cm.sources ?? []).filter((s) => /civicapi/i.test(s.name));
    meta = {
      ...meta,
      sources: [...(meta.sources ?? []), ...(own.length ? [] : [{ ...CIVIC_SOURCE, name: `${CIVIC_SOURCE.name}: election-night county results, secondary source (${[...new Set(S.civic.races.map((c) => regionName(c.region)))].join(", ")})` }]),
        ...(cm.sources ?? []).filter((s) => !names.has(s.name))],
      notes: [...(meta.notes ?? []), "Races labelled secondary source (civicAPI) are counted from civicAPI, an unofficial county feed, apart from the registered races: they are not in the statistics, the Senate-control figures or the registered models' summaries; where a state has a registered race too, that one is shown first.",
        ...(cm.notes ?? []).filter((n) => !notes.has(n))],
    };
  }
  const keep = $("our-model")?.outerHTML;
  renderMethod($("pane-method"), { meta, entry: S.entry, geometryCredit: geometryCredit(), twoModels, margins });
  if (keep) $("pane-method").insertAdjacentHTML("afterbegin", keep);
}
// civicAPI races that arrived after the page was built (the night's process started later)
function civicShown() {
  S.civicNew = false;
  fillRaceSelect();
  if (S.map) buildPicker();
  if (S.rows) { buildRaceList(); drawMethod(); }
  updateLists(S.preview ?? S.t);
}

function geometryCredit() {
  const g = S.meta.geometry ?? "";
  if (g.includes("us_")) return "Map: U.S. Census Bureau cartographic boundary files, 2024 (public domain), built by <code>analysis/geo_build.py</code>.";
  if (g.includes("br_")) return "Map: IBGE Malha Municipal Digital 2025 (public open data, attribution IBGE), joined to TSE municipality codes with the TSE table of TSE and IBGE codes (CC-BY), built by <code>analysis/geo_build.py</code>.";
  if (g.includes("nz_")) return "Map: Stats NZ, General Electorates 2025, Māori Electorates 2025 and Urban Rural land classes, CC BY 4.0.";
  return "";
}

// The race selector: the office shown first (US: Senate, governor or the House districts), then
// the rest; elsewhere the national race and each region's races.
// US: "How the model works" at the top of the Method & sources tab (written once; idempotent)
function addOurModel() {
  const pm = $("pane-method");
  if (pm && !pm.querySelector("#our-model")) pm.insertAdjacentHTML("afterbegin", `<div id="our-model">${ourModelHTML()}</div>`);
}
function initRaceSelect() {
  const sel = $("race");
  fillRaceSelect();
  sel.addEventListener("change", () => selectKey(sel.value));
  // House data is needed now for a House deep link; otherwise keep it out of the navigation's
  // critical first second and add the selector group when the browser is idle.
  if (S.ov) {
    if (S.houseSel || S.office === "house") loadHouse().then(() => { fillRaceSelect(); addOurModel(); });
    else setTimeout(() => idle(() => loadHouse().then(() => { fillRaceSelect(); addOurModel(); })), 3500);
  }
  initExtent();
}
function fillRaceSelect() {
  const sel = $("race");
  const opt = (r) => `<option value="${esc(r.race_id)}">${esc(raceName(r))}</option>`;
  if (S.nat) {
    // every Senate and governor race (● = live county model on election night), then the House
    const live = (r) => `<option value="${esc(r.race_id)}">${modelled(r) ? "● " : ""}${esc(raceName(r))}</option>`;
    const groups = [["senate", "Senate", officeRaces("sen")], ["governor", "Governor", officeRaces("gov")],
      ["house", "House", houseDistrictIds().map((id) => ({ race_id: `house:${id}`, name: `${id} (House)` }))]];
    groups.sort((a, b) => (b[0] === S.office) - (a[0] === S.office));
    sel.innerHTML = groups.filter(([, , list]) => list.length).map(([k, label, list]) => `<optgroup label="${label}${k === "house" ? "" : " (● live county model)"}">${list.map((r) => (k === "house" ? `<option value="${esc(r.race_id)}">${esc(r.name)}</option>` : live(r))).join("")}</optgroup>`).join("");
  } else {
    const nat = statewide().filter((r) => !r.region);
    const byRegion = d3.groups(statewide().filter((r) => r.region), (r) => r.region);
    sel.innerHTML = nat.map((r) => `<option value="${esc(r.race_id)}">${esc(r.name)}</option>`).join("")
      + byRegion.map(([reg, list]) => `<optgroup label="${esc(regionName(reg))}">${list.map(opt).join("")}</optgroup>`).join("");
  }
  if (S.overviewDefault && !S.houseSel) sel.insertAdjacentHTML("afterbegin", `<option value="" disabled hidden>Choose a race</option>`);
  sel.value = S.houseSel ? `house:${S.houseSel}` : S.overviewDefault ? "" : S.race?.race_id ?? "";
}
function initExtent() {
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

// The Races tab follows the office: the House districts (dash-house.js) or the office's races
// (US), every race elsewhere.
function buildRaceList() {
  if (S.office === "house") {
    S.list = null;
    houseRaceTable($("pane-races"), { state: S.ov?.state ?? null, selected: S.houseSel, onPick: (id) => selectDistrict(id), modelOf: (id) => districtModel(id) });
    return;
  }
  const hasKalshi = [...(S.rows?.values() ?? [])].some((rows) => rows.some((d) => d.kalshi != null));
  // US: every race of the office (● live county model), in the open state if one is open
  const list = S.nat ? officeRaces(OFFICE_ID[S.office ?? "senate"] ?? "sen").filter((r) => !S.region || r.region === S.region) : statewide();
  const name = S.nat ? (r) => `${modelled(r) ? "● " : ""}${raceName(r)}` : raceName;
  S.list = createRaceList($("pane-races"), { races: list, raceName: name, leadOf: (r) => decisionOf(r)?.decided ? null : leadMinutes(settleOf(r.race_id)), onSelect: selectRace, hasKalshi, profitOf, nPollsOf, decisionOf, winnerOf });
  S.list.select(S.race?.race_id);
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
    const kRow = [...(rowsOf(S.race.race_id) ?? [])].reverse().find((d) => !d.live && d.kalshi != null);
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
  const model = new Map(races().map((r) => [r.race_id, modelP(rowAt(r.race_id, S.t))]).filter(([, p]) => p != null));
  S.nat.update({ overview: S.overview, live, model });
}

// The overview's office ("senate" | "governor") as the race-id suffix of the modelled races.
const OFFICE_ID = { senate: "sen", governor: "gov" };

async function initNational() {
  if (!S.meta.overview || !S.meta.geometry) return;
  const [overview, feeds] = await Promise.all([getJSON(`${EID}/${S.meta.overview}`, { optional: true }).catch(() => null), getJSON("us_night_feeds.json", { optional: true }).catch(() => null)]);
  if (!overview) return;
  S.overview = overview;
  S.feeds = feeds;
  S.nat = createNational($("national"), {
    overview, office: "senate", summary: $("nat-summary"),
    onDrill: (id) => { const r = raceById(id); if (r) selectRace(r); },
    onOffice: (o) => setOffice(o),
    onColours: (colours, rings) => { S.natLayer = [colours, rings]; S.map?.setNational(colours, rings); },
    onHighlight: (r) => S.map?.highlightRegion(r),
    layers: [["arrows", "Swing arrows"], ...CENSUS.map(([k, l]) => [k, l])], onLayer: (k) => setLayer(k),
    // a live county model: the race is in the election's own files (meta.races), whatever the overview says
    modelled: (r) => !!(r.modeled || statewide().some((x) => x.race_id === (r.race_id ?? r.race_key))),
    feedNote: (s) => feedLine(s),
  });
  // the House office and the census map modes: a layer over the county map, in the same map box
  S.office = "senate";
  // the layer covers the map box and the region list beside it (their layout never changes under it)
  const body = document.querySelector(".map-body");
  S.ov = createOverlay(body, {
    onSelect: (id) => showDistrict(id), onState: () => houseState(), modelOf: (id) => districtModel(id),
    // the layer's controls go under the office toggle and map select
    top: () => { const n = document.querySelector("#national .nat-top"); return n ? n.getBoundingClientRect().bottom - body.getBoundingClientRect().top + 2 : 52; },
  });
  // the overview's other races (no live county model): they open like the modelled ones, with the
  // county estimate (past results shifted by polls) where they have a county file, their price
  // history and polls. A race that joins meta.races (a new county model) leaves this list by itself.
  S.extra = new Map();
  for (const [o, off] of Object.entries(overview.offices ?? {})) {
    for (const r of off.races ?? []) {
      if (statewide().some((x) => x.race_id === (r.race_id ?? r.race_key))) continue;
      S.extra.set(r.race_key, {
        race_id: r.race_key, region: r.state, units: r.units ?? null, estimate: r.estimate ?? null, ov: r,
        name: `${r.state_name ?? r.state.toUpperCase()} ${o === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`,
        a: { name: r.dem_side?.name ?? r.candidates?.D ?? "Democratic side", color: "#1f5fbf" },
        b: { name: r.candidates?.R ?? "Republican side", color: "#c8102e" },
      });
    }
  }
  // the civicAPI races: the overview's entry of a state without a registered race (same id), or an
  // alternate beside the registered one ("oh-gov-civic")
  registerCivic();
  const asked = S.extra.get(param("race"));
  if (asked) { S.race = asked; S.region = asked.region; S.level = "units"; }
  // ?region=mi without a race: that state's race (a modelled one first), not another state's
  else if (S.region && S.race?.region !== S.region) S.race = regionRace(S.region) ?? S.race;
  // the merged county layer under the state map follows the office shown
  // us2026 and its kind open on the national overview with no race chosen: S.race stays a Senate race
  // internally (the county layer under the state map), but the selector shows its placeholder
  S.overviewDefault = !param("race") && !S.region;
  if (S.overviewDefault) { $("dash").classList.add("nat-on", "us-on"); $("readout").replaceChildren(); } // the summary from the first paint, never a race
  if (!param("race")) syncOffice("senate", false);
  S.nat.draw(); // the state layer is ready before the map's first frame
  if (param("office") === "house") S.nat.setOffice("house");
  if (param("race")?.startsWith("house:")) selectKey(param("race")); // a shared link to a district
  const lay = CENSUS.find(([k]) => k === param("layer"));
  if (lay) { const sel = document.querySelector(".nat-layer select"); if (sel) sel.value = lay[0]; setLayer(lay[0]); }
}

// Keep the selected race on the office the national map shows, so the counties that fade in on
// a click are that office's (and the county files to preload are known).
function syncOffice(o, reload = true) {
  const r = races().find((x) => x.units && office(x) === OFFICE_ID[o]);
  if (!r || r === S.race || S.region) return;
  S.race = r;
  $("race").value = S.overviewDefault ? "" : r.race_id;
  if (reload) reloadUnitsIfNeeded();
}
// The office shown: Senate or governor (the county map with its state layer), or the House (the
// district layer). Census map modes replace either until "Races" is chosen again.
function setOffice(o) {
  S.office = o;
  clearDistrict(); // a change of office ends a district selection
  if (o === "house") {
    if (S.region) backToNational();
    S.censusLayer = null;
    const sel = document.querySelector(".nat-layer select");
    if (sel) sel.value = "races";
    S.ov.show("house").then(() => {
      houseSummary();
      // If House had not yet been needed, its data arrived with the layer: populate the selector
      // and the tab now rather than waiting for the deferred warm-up.
      fillRaceSelect();
      if (S.rows) buildRaceList();
    });
  } else {
    if (!S.censusLayer) S.ov.hide();
    else if (S.censusLayer === "arrows") S.ov.show("arrows", { office: o, state: null });
    syncOffice(o);
  }
  showNational();
  renderCrumbs();
  refreshUsTab();
  syncUrl(false);
  // the selector, the Races and Statistics tabs follow the office
  fillRaceSelect();
  if (S.rows) { buildRaceList(); renderStatsTab(); }
  // the state list beside the map: the office's states
  if (S.map) { buildPicker(); updateLists(S.preview ?? S.t); }
}
function setLayer(k) {
  S.censusLayer = k === "races" ? null : k;
  if (S.censusLayer) {
    if (S.region) backToNational();
    // swing arrows by county for the office shown; or a census figure
    if (k === "arrows") S.ov.show("arrows", { office: S.office === "governor" ? "governor" : "senate", state: null });
    else S.ov.show("census", { census: k, state: null });
  }
  else if (S.office === "house") S.ov.show("house");
  else S.ov.hide();
  showNational();
  renderCrumbs();
}
// House: the district list beside the map (the whole country, or the open state)
function houseSummary() {
  if (S.office !== "house") return;
  houseList($("nat-summary"), { state: S.ov.state, onPick: (id) => selectDistrict(id) });
}
// pick a district from a list or a tab: open its state, then select it
function selectDistrict(id) {
  const st = id.slice(0, 2).toLowerCase();
  if (S.ov.state !== st) S.ov.focus(st);
  S.ov.select(id);
}
// a district selected on the House layer: the readout and chart show it
function showDistrict(id) {
  if (S.ov.kind !== "house" || !id) return;
  S.houseSel = id;
  $("race").value = `house:${id}`;
  S.list?.select(null);
  $("readout").innerHTML = districtReadout(id, districtModel(id));
  $("tl-view").hidden = true;
  $("tl-feed").hidden = true;
  districtChart($("timeline"), id, districtModel(id));
  showNational();
  renderCrumbs();
  refreshUsTab();
  syncUrl(true);
  buildRaceList(); // the district highlighted in the Races tab
  renderStatsTab(); // its count model's statistics, where it has one
}
// End a district selection (a race chosen, the office changed, back to the country): the
// selector, list, readout and chart return to the race. True when a district was selected.
function clearDistrict() {
  if (!S.houseSel) return false;
  S.houseSel = null;
  S.ov?.deselect();
  if (S.race) {
    $("race").value = S.overviewDefault ? "" : S.race.race_id;
    S.list?.select(S.race.race_id);
    drawChart();
    render();
  }
  return true;
}
// a selection by key, from the selector or a tab: a House district ("house:TX-07") or a race id
function selectKey(key) {
  if (key?.startsWith("house:")) {
    const id = key.slice(6);
    if (S.office !== "house") S.nat.setOffice("house");
    S.ov.show("house").then(() => selectDistrict(id));
    return;
  }
  selectRace(raceById(key) ?? races().find((x) => x.ov?.race_key === key) ?? [...(S.extra?.values() ?? [])].find((x) => x.race_id === key));
}
// the House layer zoomed to a state or back to the country (which ends a district selection)
function houseState() {
  if (!S.ov.state) clearDistrict();
  houseSummary();
  if (S.office === "house" && S.rows) buildRaceList(); // the open state's districts first
  showNational();
  renderCrumbs();
  refreshUsTab();
}
const houseShown = () => S.ov?.kind === "house" && !!S.houseSel;

// While no state is open, the right column shows the office summary instead of one race; at the
// state level the national controls (office toggle, legend) replace the county map's (both fade;
// see style.css). At the county level the county map's modes and legend show. A selected House
// district shows like a race.
function showNational() {
  if (S.ov?.kind && (S.pinned >= 0 || S.hover >= 0 || S.hoverReg)) releasePin(); // the county map's pin/hover is not part of the layer shown
  const on = !!S.nat && !S.region && !houseShown(), map = natShown();
  $("national").classList.toggle("off", !map);
  $("dash").classList.toggle("nat-on", on);
  $("dash").classList.toggle("us-on", !!S.nat);
  $("dash").classList.toggle("ov-on", !!S.ov?.kind);
  for (const id of ["map-mode", "legend", "frame-label"]) $(id).classList.toggle("nat-hidden", map || !!S.ov?.kind);
  if (map) afterZoom(() => { if (natShown()) S.nat.draw(); });
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
  const civicP = loadCivic(); // optional, in parallel with the engine and the first files
  renderTopbar();
  // The worker keeps geometry parsing/projection and first-frame painting off the page's main
  // thread in every browser (including screenshot/headless browsers). ?engine=page remains the
  // explicit diagnostic fallback.
  S.ui = new Promise((resolve) => { S.uiReady = resolve; });
  const engine = param("engine") ?? "worker";
  if (engine === "page" || !startWorker()) await startMain();
  await civicP;
  await initNational(); // before the map: it sets the state layer and the office's county files
  S.civicNew = false; // the selector and lists below are built with the civic races
  await yieldBrowser();
  S.mapDone = buildMap();
  await Promise.all([loadSeries(), loadEarlyVote()]);
  await yieldBrowser();
  computeDomain();
  S.t = defaultTime();
  $("replay-hint").hidden = !replay();
  S.followLatest = S.t >= S.domain[1];
  S.scrub = createScrubber($("scrubber"), { onInput: (t) => { pause(); S.preview = null; setTime(t); } });
  initRaceSelect();
  initTabs();
  await yieldBrowser();
  buildRaceList();
  drawMethod();
  if (S.ov) loadHouse().then(addOurModel);
  renderStatsTab();
  await yieldBrowser();
  drawChart();
  updateScrubber();
  renderCrumbs();
  render();
  S.perf.firstPaintMs = Math.round(performance.now());
  startPrices();
  if (S.nat) setTimeout(() => idle(() => prefetchUnits(statewide())), 1500);
  // the House count models' own files: first read after the page's first second, then every minute
  if (S.nat) {
    const tick = () => { if (!document.hidden) loadHouseLive().then((ok) => { if (ok) houseLiveShown(); }); };
    setTimeout(tick, 1200);
    setInterval(tick, HOUSE_LIVE_MS);
  }

  $("place").addEventListener("click", (ev) => { if (ev.target.closest(".pc-close")) releasePin(); });
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
  window.addEventListener("popstate", onPopState);
  // for the map checks (analysis/site_check.py): the modes and levels, setting them, the time
  // domain, what the engine painted, and the hover card of one unit or region
  if (S.ov) new ResizeObserver(() => S.ov.resize()).observe(document.querySelector(".map-body"));
  // Build non-visible overlay layers later, not during navigation. They are still idle work; the
  // real delay keeps requestIdleCallback from running them immediately in the first quiet frame.
  if (S.ov) setTimeout(() => idle(() => S.ov.prepare("house").then(() => setTimeout(() => idle(() => S.ov.prepare("counties")), 1200))), 3500);
  window.__dashAudit = {
    // the House / census layer (dash-house.js): office, what it drew, a card, zoom to a state
    setOffice: (o) => S.nat?.setOffice(o), office: () => S.office ?? null, layer: () => S.ov?.audit() ?? null,
    layerCard: (id) => S.ov?.cardText(id) ?? "", layerFocus: (s) => S.ov?.focus(s), setLayer: (k) => setLayer(k),
    readout: () => $("readout").innerText,
    // the one selection and what shows it: selector value, readout, chart, open tab, address
    selectKey: (k) => selectKey(k),
    // read the House count models' files now (as the minute poll does): districts with rows
    houseLive: () => loadHouseLive().then((ok) => { if (ok) houseLiveShown(); return S.houseLive ? S.houseLive.rows.size : 0; }),
    selection: () => ({ selector: $("race").value, readout: $("readout").innerText, chart: $("timeline").innerText, tab: document.querySelector(".tab-panes [role=tabpanel]:not([hidden])")?.innerText ?? "", url: location.search }),
    modes, levels: () => (levelsOn() ? ["regions", "units"] : []), level: () => S.level, region: () => S.region,
    domain: () => S.domain, time: () => S.t, frameTimes: () => S.frameTimes ?? [],
    setMode: (m) => { S.mode = m; S.modeChosen = true; S.shownKey = null; setupModes(); render(); },
    setTime, setLevel: (l) => setLevel(l, { push: false }),
    painted: () => work("audit", {}),
    legend: () => [$("legend"), document.querySelector(".nat-legend")].map((el) => (el?.offsetParent ? el.innerText : "")).join("\n"),
    card: (i, reg = null) => {
      S.hover = i; S.hoverReg = reg; S.pinned = -1; placeCard();
      const t = $("place").innerText; S.hover = -1; S.hoverReg = null; $("place").hidden = true; return t;
    },
    units: () => ({ n: S.map?.ids?.length ?? 0, ids: S.map?.ids ?? [], regions: [...new Set((S.map?.ids ?? []).map((id) => id.split("-")[1]))] }),
    view: () => S.map?.view ?? null,
    unitPoint: (i) => S.map?.pointOf(i) ?? null, unitAt: (x, y) => S.map?.pickAt(x, y) ?? -1,
    pinned: () => S.pinned, placeText: () => ($("place").hidden ? "" : $("place").innerText),
    pickable: () => pickerRegions().map((r) => r.id).filter((r) => S.map?.hasRegion(r)),
    selectRegion, backToNational, clickUnit: (i) => mapClick(i, { clientX: 0, clientY: 0 }),
    races: () => races().map((r) => [r.race_id, r.region ?? null]), selectRace: (id) => selectRace(raceById(id)),
    // the civicAPI races held (secondary source): id, region, rows, the readout's attribution
    civic: () => (S.civic?.races ?? []).map((c) => [c.race_id, c.region ?? null, S.civic.rows.get(c.race_id)?.length ?? 0, c.source?.url ?? null]),
    url: () => location.search,
  };
  await S.mapDone;
  if (LIVE) startLive();
}

main();
