// guide.html: the election-night entry point. Reads the US dashboard's files (us2026/meta.json, timeline.json + timeline_tail.json,
// overview.json, units/<race>.json) from data/ or, with ?live=<base>, from the live server every 15 s, plus data/us2026_groups,
// data/ev2026, data/us2026_context and data/us2026_guide (static). The rules (events, board, leads, disagreements) are in
// guide-rules.js; the group figures come from groups-agg.js.
import * as d3 from "./vendor/d3.js";
import { LIVE, STATIC, getJSON, esc, param, votesShort } from "./common.js";
import { prepRace, nationalPick } from "./groups-agg.js";
import { partialOf, addedLabel, createEvents, renderBanner, bindBanner, loadSaved, save, controlChance, SENATE_NEED, HOUSE_NEED } from "./dash-events.js";
import { seatProjection, hemicycleShell, renderHemicycle, bindHemicycle } from "./dash-bop.js";
import {
  GATE, LEVELS, GAP_OPEN, GAP_CLOSE, COUNTED_STEPS, SHIFT_MIN, TURNOUT_MIN, COOL_MIN, normRows, forSide, letter, createEngine, feedRows, feedGroups, feedOf,
  boardRow, overviewRow, sortBoard, disagreements, groupHeadlines, leadOf, clockET, raceLink,
} from "./guide-rules.js";

const EID = "us2026", GDIR = "us2026_groups";
const TICK_MS = 15000, UNITS_MS = 30000, OVERVIEW_MS = 60000;
const FEED_MAX = 30, MINI_MAX = 12;
const $ = (id) => document.getElementById(id);

const S = {
  meta: null, names: new Map(), races: [],
  rows: new Map(),            // race_id -> normalised rows
  E: createEngine(), overview: null, overviewAt: 0,
  groups: null, counties: null, index: null, preps: new Map(), stamp: new Map(), fetched: new Map(),
  pre: {},                    // static files of the "before the results" part
  sort: { key: param("sort") ?? "close", dir: 1 }, showAll: false, showMini: false, gstate: param("st"),
  districts: null, houseP: new Map(), houseC: new Map(), civicMeta: null, civicLast: new Map(), houseT: new Map(), houseRow: new Map(), extraMeta: new Map(), extraLast: new Map(), houseXMeta: new Map(), houseMainIds: new Set(), houseAdded: new Set(),
  liveGet: Object.fromEntries(["house", "civicMeta", "civicTail", "extraMeta", "extraTail", "extraHouseMeta", "extraHouseTail"].map((k) => [k, { miss: 0, skip: 0, at: 0 }])),
  ticks: 0, lastOk: null, failed: false, lastT: null, rowsFull: false, board: [],
};
window.__guide = { ready: false, live: !!LIVE, build: window.__BUILD, ticks: 0, events: 0, races: 0, counting: 0, leads: 0, lastT: null };

const pc = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const dash = "–";

/* ---------------------------------------------------------------- data */

async function loadMeta() {
  const meta = await getJSON(`${EID}/meta.json`);
  S.meta = meta;
  S.races = meta.races ?? [];
  for (const r of meta.regions ?? []) S.names.set(r.id, r.name);
}

// timeline rows: the whole timeline once (a live page, or a count that has started), then the tail file (each race's last 60 minutes)
// merged by time; the whole timeline again when the tail does not reach back to the rows held. Rows before the count are kept
// only as far as the rules need them (the last few before 1% counted).
function keepFrom(rows) {
  const i = rows.findIndex((r) => r.c != null && r.c > 0);
  return rows.slice(Math.max(0, (i < 0 ? rows.length : i) - 12));
}
function ingest(src, whole) {
  let gap = false, changed = false;
  for (const r of S.races) {
    const raw = src?.[r.race_id];
    if (!Array.isArray(raw) || !raw.length) continue;
    const add = normRows(raw, r.poll_model ?? "v2");
    const held = S.rows.get(r.race_id);
    if (whole || !held?.length) { S.rows.set(r.race_id, whole ? keepFrom(add) : add); changed = true; continue; }
    if (add[0].t > held.at(-1).t + 60000) { gap = true; continue; }
    const keep = held.filter((x) => x.t < add[0].t);
    const next = keep.concat(add);
    if (next.length !== held.length || next.at(-1).t !== held.at(-1).t || next.at(-1).c !== held.at(-1).c || next.at(-1).p !== held.at(-1).p || next.at(-1).pm !== held.at(-1).pm) changed = true;
    S.rows.set(r.race_id, next);
  }
  return { gap, changed };
}
async function loadRows(first) {
  if (first) {
    const whole = !!LIVE || S.meta.phase !== "pre-count";
    const src = await getJSON(`${EID}/${whole ? "timeline" : "timeline_tail"}.json`);
    S.rowsFull = whole;
    return ingest(src, true).changed;
  }
  const tail = await getJSON(`${EID}/timeline_tail.json`, { optional: true });
  let res = tail ? ingest(tail, false) : { gap: true, changed: false };
  if (res.gap) { res = ingest(await getJSON(`${EID}/timeline.json`), true); S.rowsFull = true; }
  return res.changed;
}

async function loadOverview() {
  if (Date.now() - S.overviewAt < OVERVIEW_MS - 500 && S.overview) return false;
  S.overviewAt = Date.now();
  const o = await getJSON(`${EID}/${S.meta.overview ?? "overview.json"}`, { optional: true });
  if (!o) return false;
  S.overview = o;
  return true;
}

const anyCounting = () => [...S.rows.values()].some((rows) => rows.some((r) => r.c != null && r.c >= GATE));
const raceStarted = (id) => S.rows.get(id)?.some((r) => r.c != null && r.c >= GATE) ?? false;

// the group tables, and a race's county units (as groups.js reads them), only once a race is counting
async function loadGroups() {
  if (S.groups) return;
  const [groups, counties] = await Promise.all([getJSON(`${GDIR}/groups.json`, { root: STATIC }), getJSON(`${GDIR}/counties.json`, { root: STATIC })]);
  S.groups = groups;
  S.counties = counties;
  S.index = new Map(counties.fips.map((f, i) => [f, i]));
}
const unitRaces = () => S.races.filter((r) => r.units).map((r) => ({ ...r, path: `${EID}/${r.units}` }));
async function loadUnit(r) {
  const file = await getJSON(r.path, { optional: true });
  S.fetched.set(r.race_id, Date.now());
  if (!file?.unit_ids?.length) return false;
  const prep = prepRace(file, r, S.counties, S.index);
  const last = prep.frames.at(-1);
  const sig = `${prep.frames.length}|${last?.t}|${last?.f?.reduce?.((s, x) => s + x, 0)}`;
  if (S.preps.has(r.race_id) && S.stamp.get(r.race_id) === sig) return false;
  prep.mem = new Map();
  S.preps.set(r.race_id, prep);
  S.stamp.set(r.race_id, sig);
  return true;
}
async function refreshUnits() {
  if (!anyCounting()) return false;
  await loadGroups();
  const picks = nationalPick(unitRaces()).filter((r) => raceStarted(r.race_id));
  const due = picks.filter((r) => !S.preps.has(r.race_id) || Date.now() - (S.fetched.get(r.race_id) ?? 0) >= UNITS_MS - 500);
  const out = await Promise.allSettled(due.map(loadUnit));
  const bad = out.find((o) => o.status === "rejected");
  if (bad) console.warn("guide: units read failed", bad.reason?.message ?? bad.reason);
  return out.some((o) => o.status === "fulfilled" && o.value);
}
const groupCtx = () => ({ races: unitRaces(), preps: S.preps, groups: S.groups, counties: S.counties, names: S.names });

// the static files of "Before the results": read once, after the first draw
async function loadPre() {
  const get = (p) => getJSON(p, { root: STATIC, optional: true }).catch(() => null);
  const [ev, evg, tells, watch] = await Promise.all([get("ev2026/index.json"), get("us2026_context/ev_groups.json"), get(`${EID}/tells.json`), get("us2026_guide/watch.json")]);
  S.pre = { ev, evg, tells, watch };
}


/* ---------------------------------------------------------------- balance of power */

// The House district file (static, once), the House count models' latest chance and share counted (house_live/) and the civicAPI
// races' (civic/us2026/: secondary source, the states without a registered race): the last two exist only once the count runs
// (asked for only then), are read once a minute, and after a missing read back off (2, 4, 8, 10 minutes) so a page before
// those units start does not ask every tick.
const CIVIC = "civic/us2026", XTRA = "extra/us2026"; // XTRA: the added models (unregistered, secondary source), same schema as CIVIC
const BOP_MS = 60000;
async function liveGet(kind, path) {
  const g = S.liveGet[kind];
  if (!LIVE || !anyCounting()) return undefined; // asked for only once a race has votes (as groups.js does for civicAPI): no logged 404 before the night
  if (Date.now() - g.at < BOP_MS - 500) return undefined;
  if (g.skip > 0) { g.skip--; g.at = Date.now(); return undefined; }
  g.at = Date.now();
  const j = await getJSON(path, { optional: true }).catch(() => null);
  if (!j) { g.miss++; g.skip = Math.min(10, 2 ** g.miss) - 1; return null; }
  g.miss = 0;
  return j;
}
async function loadBop() {
  let changed = false;
  if (!S.districts) {
    S.districts = (await getJSON(`${EID}/house/districts.json`, { optional: true }).catch(() => null))?.districts ?? {};
    changed = true;
  }
  const tail = await liveGet("house", `${EID}/house_live/timeline_tail.json`);
  if (tail) {
    const src = tail.rows ?? tail;
    for (const [id, rows] of Object.entries(src)) {
      if (!id.startsWith("house:") || !Array.isArray(rows) || !rows.length) continue;
      const last = normRows(rows, "v2").at(-1);
      if (last?.p != null && (S.houseP.get(id) !== last.p || S.houseC.get(id) !== last.c)) { S.houseMainIds.add(id); S.houseAdded.delete(id); S.houseP.set(id, last.p); S.houseC.set(id, last.c); S.houseT.set(id, last.t); S.houseRow.set(id, last); changed = true; }
    }
  }
  if (!S.civicMeta) {
    const meta = await liveGet("civicMeta", `${CIVIC}/meta.json`);
    const list = (meta?.races ?? []).filter((c) => c.race_id && !c.race_id.startsWith("house:"));
    if (list.length) { S.civicMeta = new Map(list.map((c) => [c.race_id.replace(/-civic$/, ""), c])); changed = true; }
  }
  if (S.civicMeta) {
    const tail = await liveGet("civicTail", `${CIVIC}/timeline_tail.json`);
    for (const [id, rows] of Object.entries(tail?.rows ?? tail ?? {})) {
      const c = [...S.civicMeta.values()].find((x) => x.race_id === id);
      if (!c || !Array.isArray(rows) || !rows.length) continue;
      const last = normRows(rows, c.poll_model ?? "v2").at(-1);
      if (last && (S.civicLast.get(id)?.p !== last.p || S.civicLast.get(id)?.c !== last.c)) { S.civicLast.set(id, last); changed = true; }
    }
  }
  // The added models (extra/us2026/): statewide races and House districts, read like civicAPI's and the registered House files; a
  // registered race wins everywhere, then civicAPI's. The metas are read every time (coverage and stale flags change).
  const added = (c) => ({ ...c, added: true, source: { name: "added model", role: "secondary", ...c.source } });
  const xm = await liveGet("extraMeta", `${XTRA}/meta.json`);
  for (const c of xm?.races ?? []) {
    if (!c.race_id || c.race_id.startsWith("house:") || S.races.some((r) => r.race_id === c.race_id) || S.civicMeta?.has(c.race_id.replace(/-civic$/, ""))) continue;
    if (JSON.stringify(S.extraMeta.get(c.race_id)) !== JSON.stringify(added(c))) { S.extraMeta.set(c.race_id, added(c)); changed = true; }
  }
  if (S.extraMeta.size) {
    const tail = await liveGet("extraTail", `${XTRA}/timeline_tail.json`);
    for (const [id, rows] of Object.entries(tail?.rows ?? tail ?? {})) {
      const c = S.extraMeta.get(id);
      if (!c || !Array.isArray(rows) || !rows.length) continue;
      const last = normRows(rows, c.poll_model ?? "v2").at(-1);
      if (last && (S.extraLast.get(id)?.p !== last.p || S.extraLast.get(id)?.c !== last.c)) { S.extraLast.set(id, last); changed = true; }
    }
  }
  const hm = await liveGet("extraHouseMeta", `${XTRA}/house_live/meta.json`);
  for (const c of hm?.races ?? []) {
    if (c.race_id?.startsWith("house:") && JSON.stringify(S.houseXMeta.get(c.race_id)) !== JSON.stringify(added(c))) { S.houseXMeta.set(c.race_id, added(c)); changed = true; }
  }
  if (S.houseXMeta.size) {
    const tail = await liveGet("extraHouseTail", `${XTRA}/house_live/timeline_tail.json`);
    for (const [id, rows] of Object.entries(tail?.rows ?? tail ?? {})) {
      if (!id.startsWith("house:") || S.houseMainIds.has(id) || !Array.isArray(rows) || !rows.length) continue;
      const last = normRows(rows, "v2").at(-1);
      if (last?.p != null && (S.houseP.get(id) !== last.p || S.houseC.get(id) !== last.c)) { S.houseAdded.add(id); S.houseP.set(id, last.p); S.houseC.set(id, last.c); S.houseT.set(id, last.t); S.houseRow.set(id, last); changed = true; }
    }
  }
  return changed;
}

// The projected seat count (dash-bop.js hemicycles), model chances only, the same figures as the election page's: Senate, each
// race up at its registered live model's latest chance, else the civicAPI race's, else the overview's estimate, else the poll average; House, each
// district at its count model's latest chance, else its pre-count chance.
const meanCounted = (list) => {
  const c = list.filter((x) => x != null);
  return c.some((x) => x >= GATE) ? c.reduce((a, b) => a + b, 0) / c.length : null;
};
function senateProjection() {
  const sen = S.overview?.offices?.senate;
  if (!sen) return null;
  const hold = sen.holdovers ?? {}, ups = sen.races ?? [], counted = [], total = sen.seats_total ?? 100, notUpD = (hold.D ?? 0) + (hold.I ?? 0);
  const races = ups.map((o) => {
    // the registered race's live model, else the civicAPI race's (a state without a registered race)
    const reg = S.races.find((x) => x.race_id === (o.race_id ?? o.race_key)), civ = reg ? null : S.civicMeta?.get(o.race_key), xr = reg || civ ? null : S.extraMeta.get(o.race_key), r = reg ?? civ ?? xr;
    const last = reg ? S.rows.get(reg.race_id)?.at(-1) : civ ? S.civicLast.get(civ.race_id) : xr ? S.extraLast.get(xr.race_id) : null;
    let p = last?.p ?? null;
    if (p != null && r.b?.id === "dem") p = 1 - p;
    if (last?.c != null) counted.push(last.c);
    return { key: o.race_key, name: `${o.state_name ?? S.names.get(o.state) ?? o.state.toUpperCase()} Senate${o.special ? " (special)" : ""}`, p: p ?? o.estimate?.P ?? o.polls?.p_win ?? null };
  });
  return seatProjection({ chamber: "Senate", notUp: { D: notUpD, R: hold.R ?? total - notUpD - ups.length }, races, majority: sen.majority ?? null, counted: meanCounted(counted) });
}
function houseProjection() {
  const keys = Object.keys(S.districts ?? {}).sort();
  if (!keys.length) return null;
  const counted = [];
  const races = keys.map((k) => {
    if (S.houseC.has(`house:${k}`)) counted.push(S.houseC.get(`house:${k}`));
    return { key: `house:${k}`, name: `${k} (House)`, p: S.houseP.get(`house:${k}`) ?? S.districts[k]?.baseline?.p_d ?? null };
  });
  return seatProjection({ chamber: "House", notUp: { D: 0, R: 0 }, races, counted: meanCounted(counted) });
}
function renderBop() {
  const box = $("gd-bop"), projs = [senateProjection(), houseProjection()].filter(Boolean);
  if (box.children.length !== projs.length) box.innerHTML = projs.map(() => `<div class="hemi">${hemicycleShell("")}</div>`).join("");
  projs.forEach((proj, i) => { const el = box.children[i]; el.querySelector(".tab-h").textContent = proj.chamber; renderHemicycle(el, proj); });
  window.__guide.bop = Object.fromEntries(projs.map((p) => [p.chamber.toLowerCase(), { dots: p.seats.length, d: p.d, r: p.r, expectedD: p.expectedD, dLeads: p.dLeads, rLeads: p.rLeads, counted: p.counted }]));
}

/* ---------------------------------------------------------------- announcements (dash-events.js) */

// The strip at the top: "Model projects ..." items (a race's model at 99% or more on 2 refreshes in a row with 20% counted; a chamber's
// chance of control at 99% or more, seats independent), each once, kept in sessionStorage. The guide holds the whole timeline of the
// registered races, only the latest row of the civicAPI races and the House districts, and decides "decided" by the count
// rule only (dash-events.js countDecided).
const EV_KEY = `ubiu.events.${EID}.guide.${window.__BUILD}`;
let EV = null;
function feedEvents() {
  if (!anyCounting()) return;
  EV ??= createEvents(loadSaved(EV_KEY));
  for (const race of S.races) {
    const rows = S.rows.get(race.race_id) ?? [], from = EV.lastT(`race:${race.race_id}`);
    const fresh = rows.filter((r) => r.t > from);
    if (fresh.length) EV.feedRace({ id: race.race_id, label: race.name, a: race.a, b: race.b }, fresh);
  }
  const registered = new Set(S.races.map((r) => r.race_id));
  for (const c of S.civicMeta?.values() ?? []) {
    const last = S.civicLast.get(c.race_id);
    if (last && !registered.has(c.race_id.replace(/-civic$/, ""))) EV.feedRace({ id: c.race_id, label: c.name ?? c.race_id, a: c.a, b: c.b, secondary: true }, [last]);
  }
  for (const [id, c] of S.extraMeta) {
    const last = S.extraLast.get(id);
    if (last && !c.stale) EV.feedRace({ id, label: c.name ?? id, a: c.a, b: c.b, secondary: addedLabel(c), partial: partialOf(c) }, [last]);
  }
  for (const [id, p] of S.houseP) {
    const k = id.replace(/^house:/, ""), x = S.houseAdded.has(id) ? S.houseXMeta.get(id) : null;
    if (x?.stale) continue;
    EV.feedRace({ id, label: x?.name ?? `${k} House`, a: x?.a ?? { id: "dem", name: "Democratic candidate" }, b: x?.b ?? { id: "rep", name: "Republican candidate" }, ...(x ? { secondary: addedLabel(x), partial: partialOf(x) } : {}) }, [{ ...(S.houseRow.get(id) ?? {}), t: S.houseT.get(id), p, c: S.houseC.get(id) ?? null }]);
  }
  for (const [name, proj] of [["senate", senateProjection()], ["house", houseProjection()]]) {
    if (!proj) continue;
    const t = name === "senate" ? S.lastT : Math.max(0, ...S.houseT.values());
    if (t) EV.feedChamber(name, [{ t, p: controlChance(proj.seats.map((x) => x.p), proj.majority), c: proj.counted ?? 0 }]);
  }
  // Chamber decided: seats not up + races decided by the count rule (civicAPI and House rows included where their fields allow)
  const sen = S.overview?.offices?.senate;
  if (sen) {
    const hold = sen.holdovers ?? {}, total = sen.seats_total ?? 100, notUpD = (hold.D ?? 0) + (hold.I ?? 0), done = [];
    for (const o of sen.races ?? []) {
      const d = EV.decidedOf(o.race_id ?? o.race_key) ?? EV.decidedOf(`${o.race_key}-civic`) ?? EV.decidedOf(o.race_key);
      if (d?.party) done.push(d);
    }
    EV.feedDecided("senate", { notUp: { D: notUpD, R: hold.R ?? total - notUpD - (sen.races ?? []).length }, decided: done, need: SENATE_NEED });
  }
  const hdone = [...S.houseP.keys()].map((id) => EV.decidedOf(id)).filter((d) => d?.party);
  if (hdone.length) EV.feedDecided("house", { notUp: { D: 0, R: 0 }, decided: hdone, need: HOUSE_NEED });
  save(EV_KEY, EV);
}
function renderEvents() {
  const host = $("ev-strip");
  if (host && EV) renderBanner(host, EV.items(), { counting: anyCounting() });
}

/* ---------------------------------------------------------------- board */

function boardRows() {
  const have = new Set(S.races.map((r) => r.race_id));
  const rows = S.races.map((r) => boardRow(r, S.rows.get(r.race_id) ?? [], S.names));
  for (const off of ["senate", "governor"]) for (const o of S.overview?.offices?.[off]?.races ?? []) if (!have.has(o.race_key)) rows.push(overviewRow(o, S.names));
  return rows;
}

const leanHTML = (r) => {
  if (!r.side) return dash;
  const side = r.race[r.side], color = side?.color ?? "inherit";
  return `<span class="gd-lean"><b style="color:${esc(color)}">${esc(r.leaderLetter)}</b><span>${esc(String(r.leaderName ?? "").split(" ").slice(-1)[0])}</span></span>`;
};
const CHIP = { "not started": "", counting: "counting", "model 90%": "m90", decided: "decided", "market only": "" };
const chip = (s) => `<span class="gd-chip ${CHIP[s] ?? ""}">${esc(s)}</span>`;
const leadText = (m) => (m == null ? "—" : `${m > 0 ? "+" : m < 0 ? "−" : ""}${Math.abs(m)} min`);

const COLS = [
  ["race", "Race", "", "Race, linked to its view on the election page"],
  [null, "Leads", "", "The side the race leans to: the model's where it has one, else Polymarket's, else Kalshi's. The chances below are for this side"],
  ["model", "Model", "n", "The model's chance that this side wins"],
  ["pm", "Polymarket", "n", "Polymarket's price for this side"],
  ["kalshi", "Kalshi", "n", "Kalshi's price for this side"],
  ["counted", "Counted", "n", "Share of the race's expected vote counted"],
  ["status", "Status", "", `Not started: under ${pc(GATE)} counted. Counting. Model 90%: the model gives its leader 90% or more. Decided: the model gives its leader 99% or more and every market 95% or more. Market only: no county count for this race`],
  ["lead", "Lead", "n", "Minutes between the model reaching 90% and Polymarket reaching 90% on the same side, counted from 1% of the vote. Positive: the model was first. — until both have"],
];
function renderBoard() {
  const rows = sortBoard(S.board, S.sort.key, S.sort.dir);
  const shown = S.showAll ? rows : rows.filter((r) => r.modeled);
  const head = COLS.map(([key, label, cls, tip]) => {
    const on = key && key === S.sort.key;
    const inner = key ? `<button type="button" data-sort="${key}" title="${esc(tip)}">${esc(label)}</button>` : `<span title="${esc(tip)}">${esc(label)}</span>`;
    return `<th scope="col" class="${cls}"${on ? ` aria-sort="${S.sort.dir > 0 ? "ascending" : "descending"}"` : ""}>${inner}</th>`;
  }).join("");
  const body = shown.map((r) => {
    const lead = r.lead;
    return `<tr data-race="${esc(r.id)}"><td class="gd-race"><a href="${esc(r.link)}">${esc(r.label)}</a></td><td>${leanHTML(r)}</td>` +
      `<td class="n m">${pc(r.model)}</td><td class="n gd-pm">${pc(r.pm)}</td><td class="n gd-kx">${pc(r.kalshi)}</td><td class="n">${r.counted == null || !r.started ? dash : pc(r.counted)}</td>` +
      `<td>${chip(r.status)}</td><td class="n ${lead > 0 ? "lead-pos" : lead < 0 ? "lead-neg" : ""}" title="${esc(leadTip(r))}">${leadText(lead)}</td></tr>`;
  }).join("");
  $("gd-board").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}${houseRow()}</tbody>`;
  const n = rows.length, nm = rows.filter((r) => r.modeled).length;
  $("gd-board-note").innerHTML = (n > nm ? `<button type="button" class="gd-more" id="gd-board-more">${S.showAll ? `Only the ${nm} races with a county count` : `Add the other ${n - nm} races (prices only)`}</button> ` : "") +
    `Closest races first. A county count is read from the state's official feed; the other races show market prices only.`;
}
const leadTip = (r) => (r.lead == null ? "Needs the model and Polymarket both at 90% on the same side" : `Model 90% ${r.lead > 0 ? "first" : r.lead < 0 ? "second" : "at the same time as Polymarket"}${r.leadKalshi != null ? `; against Kalshi ${leadText(r.leadKalshi)}` : ""}`);

// House control: the two markets' price for a Democratic House (overview.json); the district counts have their own page
function houseRow() {
  const cm = S.overview?.offices?.house?.control_markets ?? [];
  const price = (p) => cm.find((c) => c.platform === p)?.price ?? null;
  const pm = price("polymarket"), k = price("kalshi");
  const ref = pm ?? k;
  if (ref == null) return "";
  const dem = ref >= 0.5;
  const lead = (x) => (x == null ? null : dem ? x : 1 - x);
  return `<tr class="gd-house"><td class="gd-race"><a href="election.html?id=us2026&amp;office=house">House control</a></td><td><span class="gd-lean"><b style="color:${dem ? "#1f5fbf" : "#c8102e"}">${dem ? "D" : "R"}</b></span></td>` +
    `<td class="n m">${dash}</td><td class="n gd-pm">${pc(lead(pm))}</td><td class="n gd-kx">${pc(lead(k))}</td><td class="n">${dash}</td><td><a href="election.html?id=us2026&amp;office=house">House view</a></td><td class="n">—</td></tr>`;
}

/* ---------------------------------------------------------------- the feed */

function renderFeed() {
  const list = feedOf(S.E, FEED_MAX);
  const n = S.E.events.length;
  if (!list.length) {
    $("gd-feed").innerHTML = "";
    $("gd-feed-note").textContent = anyCounting() ? "Nothing has changed enough yet to report." : "Nothing yet. Lines appear here once a race has 1% of its vote counted.";
    return;
  }
  $("gd-feed").innerHTML = list.map((e) => `<li><time datetime="${new Date(e.t).toISOString()}">${esc(clockET(e.t))}</time><span class="gd-tag ${esc(String(e.tag).toLowerCase())}">${esc(e.tag)}</span><span class="gd-txt">${esc(e.text)}</span><a class="gd-go" href="${esc(e.link)}">${e.link.startsWith("groups") ? "Places" : "Race"} →</a></li>`).join("");
  $("gd-feed-note").innerHTML = `${n > list.length ? `The latest ${list.length} of ${n}. ` : ""}Times are Eastern. <details class="gd-how"><summary>How a line is chosen</summary>${rulesHTML()}</details>`;
}
function rulesHTML() {
  const m = (x) => `${Math.round(100 * x)}%`;
  const items = [
    `The model, Polymarket or Kalshi first reaches ${LEVELS.map(m).join(", ")} for a side (Kalshi and Polymarket: ${m(LEVELS[0])} only).`,
    `The model and Polymarket (Kalshi where there is none) are more than ${Math.round(100 * GAP_OPEN)} points apart, and later back within ${Math.round(100 * GAP_CLOSE)}.`,
    "The model's favourite changes, or the lead in the count changes.",
    `A state passes ${COUNTED_STEPS.map(m).join(", ")} of its expected vote counted.`,
    `A group of counties moves ${Math.round(100 * SHIFT_MIN)} points or more against the 2024 presidential vote, or its turnout is ${Math.round(100 * TURNOUT_MIN)}% or more off what was expected: only from fully counted counties, at least 3 of them and 15% of the group's expected vote.`,
    `Nothing is reported before a race has ${pc(GATE)} counted. Each line appears once; a race gets at most one gap line per ${COOL_MIN.gap} minutes, one leader line per ${COOL_MIN.leader} and one count-lead line per ${COOL_MIN.count}; groups, one line per ${COOL_MIN.group} minutes.`,
  ];
  return `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

/* ---------------------------------------------------------------- model and market */

function miniChart(row, rows, x0, x1) {
  const W = 300, H = 132, m = { l: 30, r: 6, t: 6, b: 18 };
  const lead = leadOf(rows), side = lead.side ?? row.side ?? "a";
  const x = d3.scaleLinear([x0, x1], [m.l, W - m.r]), y = d3.scaleLinear([0, 1], [H - m.b, m.t]);
  const thin = (a) => (a.length > 220 ? a.filter((_, i) => i % Math.ceil(a.length / 220) === 0 || i === a.length - 1) : a);
  const series = (key) => thin(rows.filter((r) => r.t >= x0 && r[key] != null).map((r) => [r.t, forSide(r[key], side)]));
  const line = d3.line().x((d) => x(d[0])).y((d) => y(d[1]));
  const mdl = series("p"), pm = series("pm");
  const tc = lead.times.model != null && lead.times.polymarket != null ? Math.max(lead.times.model, lead.times.polymarket) : null;
  const ring = tc != null ? pm.reduce((best, d) => (best == null || Math.abs(d[0] - tc) < Math.abs(best[0] - tc) ? d : best), null) : null;
  const ticks = [x0, (x0 + x1) / 2, x1];
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(row.label)}: the model's chance and Polymarket's price over the night">`;
  for (const v of [0, 0.5, 1]) svg += `<line class="${v === 0.5 ? "gd-mid" : "gd-gridline"}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 4}" y="${y(v) + 3}" text-anchor="end" class="gd-ax" style="font:10px var(--sans);fill:var(--muted)">${100 * v}</text>`;
  svg += `<line class="gd-gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(0.9)}" y2="${y(0.9)}"/>`;
  ticks.forEach((t, i) => (svg += `<text x="${x(t)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === 2 ? "end" : "middle"}" style="font:10px var(--sans);fill:var(--muted)">${esc(clockET(t))}</text>`));
  if (lead.times.model != null) svg += `<line class="gd-mark-model" x1="${x(lead.times.model)}" x2="${x(lead.times.model)}" y1="${m.t}" y2="${H - m.b}"/>`;
  if (lead.times.polymarket != null) svg += `<line class="gd-mark-pm" x1="${x(lead.times.polymarket)}" x2="${x(lead.times.polymarket)}" y1="${m.t}" y2="${H - m.b}"/>`;
  if (pm.length) svg += `<path class="gd-l-pm" d="${line(pm)}"/>`;
  if (mdl.length) svg += `<path class="gd-l-model" d="${line(mdl)}"/>`;
  if (ring) svg += `<circle class="gd-conv" cx="${x(ring[0])}" cy="${y(ring[1])}" r="4"/>`;
  svg += "</svg>";
  const who = row.race[side];
  const caption = lead.minutes != null
    ? (lead.minutes > 0 ? `Model reached 90% ${lead.minutes} min before Polymarket` : lead.minutes < 0 ? `Polymarket reached 90% ${-lead.minutes} min before the model` : "Both reached 90% together")
    : lead.times.model != null ? "Model at 90%, Polymarket not yet" : lead.times.polymarket != null ? "Polymarket at 90%, model not yet" : "Neither at 90% yet";
  return `<figure class="gd-mini"><a href="${esc(row.link)}">${esc(row.label)}</a>${svg}<figcaption>Chance ${esc(row.leaderLetter && side === row.side ? row.leaderLetter : letter(who, row.race[side === "a" ? "b" : "a"]))} wins · ${esc(caption)}</figcaption></figure>`;
}
function renderMM() {
  const live = sortBoard(S.board.filter((r) => r.modeled && r.started), "close", 1);
  const sec = $("gd-mm-sec");
  if (!live.length) {
    $("gd-multiples").innerHTML = `<p class="gd-empty">Charts appear once a race has 1% of its vote counted.</p>`;
    $("gd-dis").innerHTML = "";
    $("gd-mm-legend").innerHTML = "";
    sec.dataset.state = "empty";
    return;
  }
  sec.dataset.state = "live";
  let x0 = Infinity, x1 = -Infinity;
  for (const r of live) {
    const rows = S.rows.get(r.id), g = rows.find((x) => x.c != null && x.c >= GATE);
    x0 = Math.min(x0, g.t - 30 * 60000);
    x1 = Math.max(x1, rows.at(-1).t);
  }
  const list = S.showMini ? live : live.slice(0, MINI_MAX);
  $("gd-multiples").innerHTML = list.map((r) => miniChart(r, S.rows.get(r.id), x0, x1)).join("") +
    (live.length > MINI_MAX ? `<p class="gd-note"><button type="button" class="gd-more" id="gd-mini-more">${S.showMini ? `Show the ${MINI_MAX} closest` : `Show all ${live.length} races with a count`}</button></p>` : "");
  $("gd-mm-legend").innerHTML = `<span><i style="background:var(--model)"></i>Model</span><span><i style="background:var(--market)"></i>Polymarket</span><span>Dashed line: reached 90%</span><span>Ring: both at 90%</span><span>Chance for the side the model favours now, from 1% counted</span>`;
  const dis = disagreements(live).filter((d) => d.gap >= 1).slice(0, 5);
  $("gd-dis").innerHTML = dis.length
    ? dis.map((d) => `<li><a href="${esc(d.row.link)}">${esc(d.row.label)}</a> <b>${Math.round(d.gap)} pts</b><small>${esc(letter(d.row.race[d.side], d.row.race[d.side === "a" ? "b" : "a"]))}: model ${pc(d.model)}, ${esc(d.source)} ${pc(d.market)}</small></li>`).join("")
    : `<li class="gd-empty">None: the model and the markets agree within a point.</li>`;
}

/* ---------------------------------------------------------------- where the electorate went */

const shiftLine = (g) => `<li><span>${esc(g.label)}</span><b>${esc(g.who)} +${(100 * Math.abs(g.swing)).toFixed(1)}</b><small>shift against the 2024 president · ${g.n} fully counted ${g.n === 1 ? "county" : "counties"}${g.turnout != null ? ` · turnout ${Math.round(100 * Math.abs(g.turnout - 1))}% ${g.turnout >= 1 ? "above" : "below"} expected` : ""}</small></li>`;
function renderGroups() {
  const el = $("gd-grp");
  const ctx = S.groups && S.preps.size ? groupCtx() : null;
  if (!ctx) {
    el.innerHTML = `<p class="gd-empty">Appears once counties are fully counted: a group is shown when at least 3 of its counties, with 15% of its expected vote, are complete.</p>`;
    return;
  }
  const states = nationalPick(ctx.races).filter((r) => S.preps.has(r.race_id)).map((r) => r.region).filter((s, i, a) => a.indexOf(s) === i);
  const best = states.map((s) => ({ s, h: groupHeadlines(ctx, s) }));
  const pick = states.includes(S.gstate) ? S.gstate : best.sort((a, b) => b.h.length - a.h.length)[0]?.s ?? states[0];
  S.gstate = pick;
  const nat = groupHeadlines(ctx, "us").slice(0, 3), st = groupHeadlines(ctx, pick).slice(0, 3);
  const none = `<p class="gd-empty">No group has enough fully counted counties yet.</p>`;
  el.innerHTML = `<div class="gd-card"><h3>Across the states with a count</h3>${nat.length ? `<ol class="gd-list">${nat.map(shiftLine).join("")}</ol>` : none}</div>` +
    `<div class="gd-card"><h3>In <select id="gd-gstate" aria-label="State">${states.map((s) => `<option value="${esc(s)}"${s === pick ? " selected" : ""}>${esc(S.names.get(s) ?? s.toUpperCase())}</option>`).join("")}</select></h3>${st.length ? `<ol class="gd-list">${st.map(shiftLine).join("")}</ol>` : none}</div>`;
}

/* ---------------------------------------------------------------- before the results */

const dayET = (t) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" }).format(new Date(t));
const GROUP_NAME = { "race:White": "White voters", "race:Black": "Black voters", "race:Hispanic": "Hispanic voters", "race:Asian": "Asian voters", "age:18-29": "Voters aged 18-29", "age:30-44": "Voters aged 30-44", "age:45-64": "Voters aged 45-64", "age:65+": "Voters aged 65+", "gender:Female": "Women", "gender:Male": "Men", "party:Democratic": "Registered Democrats", "party:Republican": "Registered Republicans", "party:Unaffiliated": "Unaffiliated voters" };

function earlyHTML() {
  const ev = S.pre.ev;
  if (!ev) return "";
  const states = ev.states.filter((s) => typeof s.total === "number");
  const total = states.reduce((s, x) => s + x.total, 0);
  const paced = states.filter((s) => s.pace?.pace != null).sort((a, b) => a.pace.pace - b.pace.pace);
  const med = paced.length ? d3.quantile(paced.map((s) => s.pace.pace), 0.5) : null;
  const hi = paced.at(-1), lo = paced[0];
  const x2 = (v) => `${v.toFixed(2)}×`;
  const chips = [
    `<span class="chip"><small>Ballots already cast</small><b>${votesShort(total)}</b><i>${states.length} states, latest official counts</i></span>`,
    med != null ? `<span class="chip"><small>Pace against 2022</small><b>${x2(med)}</b><i>median of ${paced.length} states, same day</i></span>` : "",
    paced.length > 1 ? `<span class="chip"><small>Fastest against 2022</small><b>${x2(hi.pace.pace)}</b><i>${esc(hi.name)}</i></span><span class="chip"><small>Slowest against 2022</small><b>${x2(lo.pace.pace)}</b><i>${esc(lo.name)}</i></span>` : "",
  ].join("");
  // biggest composition shifts: a group's share of the early ballots now against the same day in 2022
  const shifts = [];
  for (const s of S.pre.evg?.states ?? []) {
    for (const [k, g] of Object.entries(s.groups ?? {})) {
      const past = g.past?.["2022"], dim = k.split(":")[0];
      if (!past || !GROUP_NAME[k] || (s.coverage?.[dim] ?? s.coverage?.[`${dim}_uf`] ?? 0) < 90 || g.n < 20000) continue;
      shifts.push({ state: s.name, label: GROUP_NAME[k], share: g.share, diff: g.share - past.share });
    }
  }
  const seen = new Set(), top = [];
  for (const x of shifts.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))) if (!seen.has(x.state) && top.length < 3) { seen.add(x.state); top.push(x); }
  const list = top.length
    ? `<ol class="gd-list">${top.map((x) => `<li><span>${esc(x.state)}: ${esc(x.label)}</span><b>${x.diff > 0 ? "+" : "−"}${Math.abs(x.diff).toFixed(1)} pts</b><small>${x.share.toFixed(1)}% of early ballots, against the same day before the 2022 election</small></li>`).join("")}</ol>`
    : "";
  return `<h3>Early vote</h3><div class="chips">${chips}</div>${top.length ? `<h3>Biggest changes in who has voted early</h3>${list}` : ""}<p class="gd-note"><a href="earlyvote.html">All states</a> · <a href="midterms.html">Who votes, by state</a></p>`;
}

function scheduleHTML() {
  const regions = (S.meta.regions ?? []).filter((r) => r.poll_close_utc);
  if (!regions.length) return "";
  const tells = S.pre.tells?.states ?? {};
  const rows = regions.slice().sort((a, b) => Date.parse(a.poll_close_utc) - Date.parse(b.poll_close_utc) || a.name.localeCompare(b.name)).map((r) => {
    const races = S.races.filter((x) => x.region === r.id).map((x) => `<a href="${raceLink(x.race_id)}">${x.race_id.endsWith("sen") ? "Senate" : "Governor"}</a>`).join(", ");
    const g = tells[r.id]?.gaps?.["25"];
    const close = r.poll_close_last_utc && r.poll_close_last_utc !== r.poll_close_utc ? `${clockET(r.poll_close_utc)}–${clockET(r.poll_close_last_utc)}` : clockET(r.poll_close_utc);
    const gap = g?.mean == null ? dash : `${Math.abs(g.mean).toFixed(1)} pts more ${g.mean > 0 ? "D" : "R"}`;
    return `<tr><td class="n">${esc(close)}</td><td>${esc(r.name)}</td><td>${races}</td><td class="n" title="${g ? `Range across ${g.n} past counts: ${g.min} to ${g.max}` : ""}">${gap}</td></tr>`;
  }).join("");
  const day = dayET(Date.parse(regions[0].poll_close_utc) - 3600000);
  return `<h3>When polls close, ${esc(day)} (Eastern)</h3><div class="table-scroll"><table class="gd-t"><thead><tr><th class="n">Closes</th><th>State</th><th>Races</th><th class="n" title="Average of the 2020 and 2024 counts: how far the count at 25% counted was from the final result. More D means the early count leaned Democratic and the rest of the vote was redder">Early count vs final</th></tr></thead><tbody>${rows}</tbody></table></div><p class="gd-note">Early count vs final: how far the count stood from the final result when 25% was counted, average of 2020 and 2024.</p>`;
}

function watchHTML() {
  const w = S.pre.watch?.races;
  if (!w) return "";
  const rows = S.races.filter((r) => w[r.race_id]).map((r) => ({ r, list: w[r.race_id] }));
  if (!rows.length) return "";
  const line = (c) => `${esc(c.name)} <span class="gd-note">D ${Math.round(100 * c.s0)}% (${Math.round(100 * c.lo)}–${Math.round(100 * c.hi)})</span>`;
  const body = rows.map(({ r, list }) => `<tr><td class="gd-race"><a href="${raceLink(r.race_id)}">${esc(`${S.names.get(r.region) ?? r.region} ${r.race_id.endsWith("sen") ? "Senate" : "governor"}`)}</a></td><td>${list.map(line).join(" · ")}</td></tr>`).join("");
  return `<h3>Counties to watch first</h3><div class="table-scroll"><table class="gd-t"><thead><tr><th>Race</th><th>Early-reporting counties whose result moves the statewide projection most</th></tr></thead><tbody>${body}</tbody></table></div><p class="gd-note">Expected Democratic share of the two-party vote in each county, with the 5–95% band if the state is on benchmark. Frozen ${esc(S.pre.watch.frozen ?? "")}.</p>`;
}

function renderPre() {
  const started = anyCounting();
  const det = $("gd-pre");
  if (S.preShown !== started) { det.open = !started; S.preShown = started; }
  $("gd-pre-h").textContent = started ? "Before the results: early vote, poll closing, counties to watch" : "Before the results";
  const key = `${started}|${Object.keys(S.pre).filter((k) => S.pre[k]).join()}`;
  if (key === S.preKey) return;
  S.preKey = key;
  $("gd-pre-body").innerHTML = `${earlyHTML()}<div class="gd-two"><div>${scheduleHTML()}</div><div>${watchHTML()}</div></div>`;
}

/* ---------------------------------------------------------------- page */

function renderStatus() {
  const n = S.board.filter((r) => r.started).length, last = S.lastT;
  const t = last ? `${dayET(last)}, ${clockET(last)} ET` : null;
  let s;
  if (!anyCounting()) {
    const close = Date.parse(S.meta.poll_close_utc);
    s = `No race has votes counted yet. First polls close ${Number.isFinite(close) ? `${dayET(close)} at ${clockET(close)} ET` : "on election night"}.${t ? ` Latest prices ${t}.` : ""}`;
  } else s = `${n} of ${S.board.filter((r) => r.modeled).length} races with a county count have votes counted. Latest data ${t}.`;
  $("gd-status").textContent = `${s}${LIVE ? ` Updated every ${TICK_MS / 1000} seconds${S.failed ? "; the last update failed, showing the previous data" : ""}.` : ""}`;
}

function renderAll() {
  S.board = boardRows();
  S.lastT = Math.max(0, ...[...S.rows.values()].map((r) => r.at(-1)?.t ?? 0)) || null;
  renderStatus();
  feedEvents();
  renderEvents();
  renderBop();
  renderFeed();
  renderBoard();
  renderMM();
  renderGroups();
  renderPre();
  const g = window.__guide;
  g.events = S.E.events.length;
  g.races = S.board.length;
  g.counting = S.board.filter((r) => r.started).length;
  g.leads = S.board.filter((r) => r.lead != null).length;
  const done = S.board.filter((r) => r.started).map((r) => r.counted).sort((x, y) => x - y);
  g.medianCounted = done.length ? done[Math.floor(done.length / 2)] : 0;
  g.lastT = S.lastT;
  g.eventKeys = S.E.events.map((e) => e.key);
  g.ticks = S.ticks;
  g.leadsOf = Object.fromEntries(S.board.filter((r) => r.lead != null).map((r) => [r.id, r.lead]));
}

function feedAll() {
  feedRows(S.E, S.races.map((race) => ({ race, rows: S.rows.get(race.race_id) ?? [] })), S.names);
  if (S.groups && S.preps.size) feedGroups(S.E, groupCtx());
}

async function tick(first = false) {
  const t0 = performance.now();
  S.ticks++;
  let changed = first;
  try {
    changed = (await loadRows(first)) || changed;
    changed = (await loadOverview()) || changed;
    changed = (await refreshUnits()) || changed;
    changed = (await loadBop().catch((e) => { console.warn("guide: balance of power read failed", e?.message ?? e); return false; })) || changed;
    S.failed = false;
    S.lastOk = Date.now();
  } catch (e) {
    S.failed = true;
    console.warn("guide: update failed", e?.message ?? e);
  }
  if (changed || S.failed) { feedAll(); renderAll(); }
  else window.__guide.ticks = S.ticks;
  window.__guide.tickMs = Math.round(performance.now() - t0);
}

// on a live page every link to the election page and to groups.html carries the live address, so they open on the same data
function liveLinks() {
  for (const a of document.querySelectorAll('main a[href^="election.html"], main a[href^="groups.html"]')) {
    if (!a.getAttribute("href").includes("live=")) a.setAttribute("href", `${a.getAttribute("href")}${a.getAttribute("href").includes("?") ? "&" : "?"}live=${encodeURIComponent(LIVE)}`);
  }
}

function wire() {
  bindBanner($("ev-strip"));
  bindHemicycle($("gd-bop"), (key) => { location.href = `${raceLink(key)}${LIVE ? `&live=${encodeURIComponent(LIVE)}` : ""}`; });
  $("gd-board").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    const key = b.dataset.sort;
    S.sort = key === S.sort.key ? { key, dir: -S.sort.dir } : { key, dir: 1 };
    renderBoard();
  });
  $("gd-board-note").addEventListener("click", (e) => { if (e.target.closest("#gd-board-more")) { S.showAll = !S.showAll; renderBoard(); } });
  $("gd-multiples").addEventListener("click", (e) => { if (e.target.closest("#gd-mini-more")) { S.showMini = !S.showMini; renderMM(); } });
  $("gd-grp").addEventListener("change", (e) => { if (e.target.id === "gd-gstate") { S.gstate = e.target.value; renderGroups(); } });
}

function foot() {
  $("gd-foot").innerHTML = `<p>Counts: the official results feeds listed on the <a href="election.html?id=us2026">election page</a>. Prices: Polymarket public price API; Kalshi public market data (Kalshi data: Kalshi Inc.). Groups of counties, not of voters: shifts are from fully counted counties only. Every line on this page is computed from those files.</p>`;
}

async function init() {
  const live = LIVE ? `&live=${encodeURIComponent(LIVE)}` : "";
  $("dash-link").href = `election.html?id=${EID}${live}`;
  await loadMeta();
  wire();
  if (LIVE) new MutationObserver(liveLinks).observe(document.querySelector("main"), { childList: true, subtree: true });
  foot();
  await tick(true);
  window.__guide.ready = true;
  loadPre().then(() => renderPre()).catch((e) => console.warn("guide: static files failed", e));
  if (LIVE) {
    const loop = async () => {
      if (!document.hidden) await tick();
      setTimeout(loop, TICK_MS);
    };
    setTimeout(loop, TICK_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  }
}

init().catch((e) => { $("gd-status").textContent = `The page could not load: ${e.message}`; console.error(e); });
