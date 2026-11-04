// Live count map: every 2026 House district, Senate and governor race, from the night's live files.
// Live files come from the tunnel (?live= or data/live_link.json); geometry and pre-count numbers are static (data/).
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { createEvents, loadSaved, save, partialOf, partyOf, addedLabel, etClock } from "./dash-events.js";

const STATIC = "data/";
const TUNNEL = /^https:\/\/[a-z0-9-]+\.trycloudflare\.com\/?$/;
const REFRESH_MS = 30000;
const NIGHT_START = Date.parse("2026-11-03T23:00:00Z");
const D = "#1f5fbf", R = "#c8102e", GREY = "#dcdad3", PAPER = "#fcfbf8", NEUTRAL = "#f1efe9", ORANGE = "#d9661f", PURPLE = "#6b4fa8";
const OFFICES = { house: "House", senate: "Senate", governor: "Governor" };
const NOSPLIT = { FL: "Florida", SC: "South Carolina", OH: "Ohio" };
const ONESIDED = "unavailable (one-sided book)";

// ---- live files: one fetch per file per cycle ("no-cache": an unchanged file costs a 304) ----
const FILES = [
  { k: "reg", p: "us2026/timeline_tail.json" }, { k: "regMeta", p: "us2026/meta.json" },
  { k: "hreg", p: "us2026/house_live/timeline_tail.json" }, { k: "hregMeta", p: "us2026/house_live/meta.json" },
  { k: "add", p: "extra/us2026/timeline_tail.json" }, { k: "addMeta", p: "extra/us2026/meta.json" },
  { k: "hadd", p: "extra/us2026/house_live/timeline_tail.json" }, { k: "haddMeta", p: "extra/us2026/house_live/meta.json" },
  { k: "civ", p: "civic/us2026/timeline_tail.json" }, { k: "civMeta", p: "civic/us2026/meta.json" },
  { k: "inky", p: "extra/us2026/inky.json" },
];
const HISTORY = ["reg", "hreg", "add", "hadd", "civ"]; // full-night files, fetched once when the replay is first used

const S = {
  base: null, errs: 0, lastOk: 0, txt: {}, json: {}, hist: null, histState: "idle",
  tab: "house", mode: "count", sel: null, t: null, playing: false, focusSt: null,
  units: { house: new Map(), senate: new Map(), governor: new Map() },
  stateName: new Map(), sort: { col: "counted", dir: -1 }, filter: "counting", tRange: [0, 0],
};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pc = (x, d = 0) => (x == null || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(d)}%`);
const fmtVotes = (n) => (n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : Math.round(n).toLocaleString("en-US"));
const ET = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const clockUTC = (ms) => new Date(ms).toISOString().slice(11, 16);
const clockText = (ms) => `${clockUTC(ms)} UTC · ${ET.format(ms)} ET`;
const tierLabel = (t) => (t === "civicAPI" ? "civicAPI (secondary)" : t);
const lean = (P) => (P == null ? null : P >= 0.65 ? "D" : P <= 0.35 ? "R" : "T");
const marginText = (raw) => (raw == null ? "–" : Math.abs(raw - 0.5) < 0.0005 ? "even" : `${raw > 0.5 ? "D" : "R"}+${(Math.abs(raw - 0.5) * 200).toFixed(1)}`);

// ---- loading ----
async function getStatic(path) {
  const r = await fetch(STATIC + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
async function resolveBase() {
  const live = new URLSearchParams(location.search).get("live");
  if (live && TUNNEL.test(live) && S.errs < 2) return (S.base = live.endsWith("/") ? live : `${live}/`);
  try {
    const r = await fetch(`${STATIC}live_link.json?t=${Math.floor(Date.now() / 60000)}`, { cache: "no-store" });
    if (!r.ok) throw new Error("no live_link");
    const j = await r.json();
    if (typeof j?.url !== "string" || !TUNNEL.test(j.url)) throw new Error("bad url");
    const next = j.url.endsWith("/") ? j.url : `${j.url}/`;
    if (next !== S.base) { S.base = next; S.txt = {}; S.hist = null; S.histState = "idle"; S.auxNext = 0; }
  } catch (_) { if (live && TUNNEL.test(live)) S.base = live.endsWith("/") ? live : `${live}/`; }
  return S.base;
}
async function fetchText(f) {
  const r = await fetch(S.base + f.p, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${f.p}: HTTP ${r.status}`);
  return r.text();
}
// the official-source count recorder (extra/us2026/aux_counts.json): may not exist yet, so a miss is retried only every 5 minutes
async function fetchAux() {
  if (Date.now() < (S.auxNext ?? 0)) return false;
  try {
    const r = await fetch(`${S.base}extra/us2026/aux_counts.json`, { cache: "no-cache" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const text = await r.text();
    S.auxNext = 0;
    if (text === S.txt.aux) return false;
    S.json.aux = JSON.parse(text); S.txt.aux = text;
    return true;
  } catch (_) { S.auxNext = Date.now() + 300000; return false; }
}
async function cycle() {
  try {
    if (!S.base || S.errs >= 2) await resolveBase();
    if (!S.base) S.errs++;
    else {
      const res = await Promise.allSettled(FILES.map(fetchText));
      let ok = 0, changed = false;
      res.forEach((r, i) => {
        if (r.status !== "fulfilled") return;
        const f = FILES[i];
        if (r.value === S.txt[f.k]) { ok++; return; }
        try { S.json[f.k] = JSON.parse(r.value); S.txt[f.k] = r.value; changed = true; ok++; } catch (_) { /* keep the last good file */ }
      });
      if (ok === 0) S.errs++; else { S.errs = 0; S.lastOk = Date.now(); }
      if (ok > 0 && (await fetchAux())) changed = true;
      if (changed) rebuild();
    }
  } catch (_) { S.errs++; }
  renderStatus();
  setTimeout(cycle, S.errs ? Math.min(REFRESH_MS * 2 ** (S.errs - 1), 240000) : REFRESH_MS);
}

// ---- units ----
const slim = (rows, pmv) => rows.map((r) => ({
  t: Date.parse(r.t), counted: r.counted, raw: r.raw, share: r[`share_${pmv}`] ?? r.share, sd: r[`sd_${pmv}`] ?? r.sd, P: r[`P_${pmv}`] ?? r.P,
  pm: r.pm, kalshi: r.kalshi, vp: r.votes_proj, rem: r.rem_total, exp: r.turnout_exp_total,
}));
function rowsOf(tailKey, key, pmv) {
  const tail = S.json[tailKey]?.[key];
  const t = Array.isArray(tail) ? slim(tail, pmv) : [];
  const h = S.hist?.[tailKey]?.[key];
  if (!Array.isArray(h) || !h.length) return t;
  const hs = slim(h, pmv).filter((r) => r.t >= NIGHT_START), last = hs.length ? hs[hs.length - 1].t : 0;
  return hs.concat(t.filter((r) => r.t > last));
}
function metaRaces(metaKey) {
  const m = new Map();
  for (const r of S.json[metaKey]?.races ?? []) m.set(r.race_id, r);
  return m;
}
const mkUnit = (office, id, st, name) => ({ key: `${office}:${id}`, office, id, st, name, tier: "estimate only", rows: null, inky: null, aux: null, est: null, mkt0: null, cand: { d: null, r: null }, model: null, alt: null, v: null });

function rebuild() {
  const { dist, markets, overview } = S;
  // House: all 435 districts, estimate only unless a live source names them
  for (const [id, d] of Object.entries(dist.districts)) {
    let u = S.units.house.get(id);
    if (!u) { u = mkUnit("house", id, d.st, id); S.units.house.set(id, u); }
    u.est = d.baseline ? { P: d.baseline.p_d, share: d.baseline.share, sd: d.baseline.sd } : null;
    u.mkt0 = d.market_p_d ?? null;
    const mk = markets.districts[id];
    u.cand = { d: mk?.pm?.cands?.D ?? mk?.kalshi?.cands?.D ?? null, r: mk?.pm?.cands?.R ?? mk?.kalshi?.cands?.R ?? null };
    u.tier = "estimate only"; u.rows = null; u.inky = null; u.aux = null; u.model = null;
  }
  const inky = S.json.inky;
  S.inkyT = inky?.t ? Date.parse(inky.t) : null;
  for (const [id, k] of Object.entries(inky?.districts ?? {})) {
    const u = S.units.house.get(id);
    if (!u) continue;
    u.inky = k; u.tier = "count only";
    u.cand = { d: k.d_cand ?? u.cand.d, r: k.r_cand ?? u.cand.r };
  }
  const evSrc = new Map(); // race id -> { id, race (meta), tier, rows }: what the projection rules are fed
  for (const [tail, meta, tier] of [["hadd", "haddMeta", "added"], ["hreg", "hregMeta", "registered"]]) {
    const mr = metaRaces(meta);
    for (const [key, r] of mr) {
      const u = S.units.house.get(key.replace(/^house:/, ""));
      if (!u) continue;
      const rows = rowsOf(tail, key, r.poll_model ?? "v2");
      if (!rows.length) continue;
      u.rows = rows; u.tier = tier; u.model = r.poll_model ?? "v2";
      u.cand = { d: r.a?.name ?? u.cand.d, r: r.b?.name ?? u.cand.r };
      evSrc.set(key, { id: key, race: tier === "added" ? { ...r, added: true } : r, tier, rows, unitKey: u.key });
    }
  }
  // Senate and governor: every race of overview.json; live model from registered > added > civicAPI
  for (const [office, list] of [["senate", overview.offices.senate.races], ["governor", overview.offices.governor.races]]) {
    for (const r of list) {
      const st = r.state.toUpperCase();
      S.stateName.set(st, r.state_name);
      let u = S.units[office].get(st);
      if (!u) { u = mkUnit(office, st, st, `${r.state_name} ${OFFICES[office].toLowerCase()}${r.special ? " (special)" : ""}`); S.units[office].set(st, u); }
      const pm0 = r.precount ? { P: r.precount[`P_${r.precount.poll_model ?? "v2"}`] ?? r.precount.P, share: r.precount[`share_${r.precount.poll_model ?? "v2"}`] ?? r.precount.share, sd: r.precount[`sd_${r.precount.poll_model ?? "v2"}`] ?? r.precount.sd } : r.estimate ? { P: r.estimate.P, share: r.estimate.share, sd: r.estimate.sd } : null;
      u.est = pm0;
      u.mkt0 = { pm: r.pm?.price ?? null, kalshi: r.kalshi?.price ?? null };
      u.cand = { d: r.candidates?.D ?? r.dem_side?.name ?? null, r: r.candidates?.R ?? (r.incumbent_party === "R" ? r.incumbent : null) };
      u.tier = "estimate only"; u.rows = null; u.model = null; u.alt = null; u.inky = null; u.aux = null;
    }
  }
  // live model per statewide race: registered > added > civicAPI; the next-ranked model of the same race is kept for the panel
  const sfx = { sen: "senate", gov: "governor" };
  const found = new Map();
  for (const [tail, meta, tier, rank] of [["reg", "regMeta", "registered", 3], ["add", "addMeta", "added", 2], ["civ", "civMeta", "civicAPI", 1]]) {
    for (const [key, r] of metaRaces(meta)) {
      const m = /^([a-z]{2})-(sen|gov)(-civic)?$/.exec(key);
      const u = m && S.units[sfx[m[2]]].get(m[1].toUpperCase());
      if (!u) continue;
      const rows = rowsOf(tail, key, r.poll_model ?? "v2");
      if (!rows.length) continue;
      const list = found.get(u) ?? [];
      list.push({ tier, rank, rows, model: r.poll_model ?? "v2", cand: { d: r.a?.name ?? null, r: r.b?.name ?? null }, id: key, race: tier === "added" ? { ...r, added: true } : r });
      found.set(u, list);
    }
  }
  for (const [u, list] of found) {
    list.sort((a, b) => b.rank - a.rank);
    // the highest-ranked source that is counting; the civicAPI race stands in where the registered / added one has no count (feed blocked)
    const best = list.find((c) => (c.rows[c.rows.length - 1]?.counted ?? 0) > 0) ?? list[0], next = list.find((c) => c !== best);
    u.rows = best.rows; u.tier = best.tier; u.model = best.model;
    u.cand = { d: best.cand.d ?? u.cand.d, r: best.cand.r ?? u.cand.r };
    u.alt = next ? { tier: next.tier, rows: next.rows } : null;
    // projections: registered and added races always; a civicAPI race only while it is the source the page shows (the others have no count)
    for (const c of list) evSrc.set(c.id, { id: c.id, race: c.race, tier: c.tier, rows: c.rows, unitKey: u.key, feed: c.tier !== "civicAPI" || c === best });
  }
  S.evSrc = evSrc;
  applyAux();
  feedEvents();
  computeAll();
  refreshAll();
}

// Official-source counts (aux_counts.json) for a race nothing else counts: no live model and no civicAPI count. Never feeds projections.
function applyAux() {
  const aux = S.json.aux;
  if (!aux?.races) return;
  for (const [key, a] of Object.entries(aux.races)) {
    let u = null;
    const h = /^house:(.+)$/.exec(key), m = /^([a-z]{2})-(sen|gov)$/.exec(key);
    if (h) u = S.units.house.get(h[1]);
    else if (m) u = S.units[m[2] === "sen" ? "senate" : "governor"].get(m[1].toUpperCase());
    if (!u || u.tier !== "estimate only" || !(a?.D >= 0) || !(a?.R >= 0)) continue;
    const prec = Array.isArray(a.precincts) && a.precincts[1] > 0 ? a.precincts[0] / a.precincts[1] : null;
    let c = typeof a.pct_reporting === "number" ? a.pct_reporting : null;
    if (c != null) c = c > 1 ? c / 100 : prec != null && Math.abs(c / 100 - prec) < Math.abs(c - prec) ? c / 100 : c;
    c ??= prec;
    const stKey = a.st ?? u.st;
    u.aux = { ...a, counted: c, src: aux.sources?.[stKey] ?? aux.sources?.[String(stKey).toLowerCase()] ?? null, t: Date.parse(a.as_of) };
    u.tier = "count only (official)";
  }
}
function rowAt(rows, T) {
  if (!rows?.length) return null;
  if (T == null) return rows[rows.length - 1];
  let lo = 0, hi = rows.length - 1, ans = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (rows[m].t <= T) { ans = m; lo = m + 1; } else hi = m - 1; }
  return ans < 0 ? null : rows[ans];
}
function usable(v, ref) {
  if (v == null) return { v: null, why: "none" };
  if (ref != null && ((v <= 0.01 && ref >= 0.95) || (v >= 0.99 && ref <= 0.05))) return { v: null, why: "one-sided" };
  return { v, why: null };
}
function compute(u) {
  const row = rowAt(u.rows, S.t);
  const v = { row, live: !!row, counted: null, raw: null, votes: null, share: null, sd: null, P: null, pm: null, kalshi: null, t: null };
  if (row) {
    Object.assign(v, { counted: row.counted, raw: row.raw, share: row.share, sd: row.sd, P: row.P, pm: row.pm, kalshi: row.kalshi, t: row.t });
    if (row.vp != null && row.rem != null) v.votes = Math.max(0, row.vp - row.rem);
  }
  const k = u.inky;
  if (k && S.t == null) {
    if (v.raw == null && k.pct > 0) { v.counted = k.pct / 100; v.raw = k.d2; }
    if (k.counted != null) v.votes = k.counted;
    v.t ??= S.inkyT;
  }
  const x = u.aux;
  // votes in while the source says 0 % reporting (CA counts mail before any precinct is complete): the reporting share is unknown, not zero
  if (x && S.t == null && x.D + x.R > 0) { v.counted = x.counted > 0 ? x.counted : null; v.raw = x.D / (x.D + x.R); v.votes = x.D + x.R + (x.other ?? 0); v.t = Number.isFinite(x.t) ? x.t : null; }
  if (v.counted != null && v.counted <= 0) { v.counted = 0; v.raw = null; }
  const ref = v.P ?? u.est?.P ?? null;
  const pre = !v.live;
  const mpm = usable(pre ? u.mkt0?.pm : v.pm, ref), mk = usable(pre ? u.mkt0?.kalshi : v.kalshi, ref);
  v.pmU = mpm; v.kU = mk;
  v.mkt = mpm.v ?? mk.v;
  v.mktSrc = mpm.v != null ? "Polymarket" : mk.v != null ? "Kalshi" : null;
  v.onesided = mpm.why === "one-sided" || mk.why === "one-sided";
  v.projP = v.P ?? u.est?.P ?? null;
  v.gap = v.P != null && v.live && v.mkt != null ? v.P - v.mkt : null;
  u.v = v;
  return v;
}
function computeAll() { for (const m of Object.values(S.units)) for (const u of m.values()) compute(u); }

// ---- colour ----
const mixTo = (c, base, t) => d3.interpolateRgb(base, c)(t);
function fillOf(u) {
  const v = u.v;
  if (S.mode === "count") {
    if (v.raw == null || (v.counted != null && !(v.counted > 0))) return { fill: GREY, hatch: 0 };
    const cc = v.counted ?? 0.25;
    const m = Math.abs(v.raw - 0.5) * 2, c = v.raw >= 0.5 ? D : R;
    const strong = mixTo(c, NEUTRAL, 0.45 + 0.55 * Math.min(1, m / 0.2));
    const f = 0.45 + 0.55 * Math.min(1, cc / 0.4);
    return { fill: mixTo(strong, GREY, f), hatch: cc < 0.4 ? 0.7 * (1 - cc / 0.4) : 0 };
  }
  if (S.mode === "proj") {
    const P = v.projP;
    if (P == null) return { fill: GREY, hatch: 0 };
    const t = 0.18 + 0.82 * Math.pow(Math.min(1, Math.abs(P - 0.5) * 2), 0.9), c = mixTo(P >= 0.5 ? D : R, NEUTRAL, t);
    return { fill: v.P == null ? mixTo(c, PAPER, 0.5) : c, hatch: 0 };
  }
  if (v.gap == null) return { fill: GREY, hatch: 0, dim: true };
  return { fill: mixTo(v.gap >= 0 ? ORANGE : PURPLE, NEUTRAL, 0.15 + 0.85 * Math.min(1, Math.abs(v.gap) / 0.5)), hatch: 0 };
}

// ---- map ----
let svg, gz, zoom, proj, path, houseSel, stateSel, houseHatch, stateHatch, feats = { house: [], state: [] }, curK = 1;
const W = 975, H = 610;
async function initMap() {
  const [hTopo, sTopo] = await Promise.all([getStatic("geo/us_house_2026.topo.json"), getStatic("geo/us_regions.topo.json")]);
  const hf = topojson.feature(hTopo, hTopo.objects.districts), sf = topojson.feature(sTopo, sTopo.objects.regions);
  proj = d3.geoAlbersUsa().fitExtent([[6, 6], [W - 6, H - 6]], hf);
  path = d3.geoPath(proj);
  feats.house = hf.features.filter((f) => path.bounds(f)[0][0] === path.bounds(f)[0][0]);
  feats.state = sf.features.filter((f) => path.bounds(f)[0][0] === path.bounds(f)[0][0]);
  svg = d3.select("#cnt-svg");
  svg.html('<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5" stroke="#fcfbf8" stroke-width="2"/></pattern></defs>');
  gz = svg.append("g").attr("class", "zoomg");
  const layer = (cls) => gz.append("g").attr("class", `lay ${cls}`);
  const gh = layer("lay-house"), gs = layer("lay-state");
  houseSel = gh.selectAll("path").data(feats.house, (f) => f.properties.id).join("path").attr("class", "u").attr("d", path).attr("data-key", (f) => `house:${f.properties.id}`);
  houseHatch = gh.append("g").attr("class", "hatchg").selectAll("path").data(feats.house, (f) => f.properties.id).join("path").attr("class", "h").attr("d", path).attr("fill", "url(#hatch)");
  gh.append("path").attr("class", "stborder").attr("d", path(topojson.mesh(sTopo, sTopo.objects.regions, (a, b) => a !== b)));
  stateSel = gs.selectAll("path").data(feats.state, (f) => f.properties.region).join("path").attr("class", "u").attr("d", path).attr("data-key", (f) => `state:${f.properties.region.toUpperCase()}`);
  stateHatch = gs.append("g").attr("class", "hatchg").selectAll("path").data(feats.state, (f) => f.properties.region).join("path").attr("class", "h").attr("d", path).attr("fill", "url(#hatch)");
  zoom = d3.zoom().scaleExtent([1, 60]).translateExtent([[0, 0], [W, H]]).extent([[0, 0], [W, H]])
    .filter((e) => (e.type === "touchstart" ? e.touches.length > 1 || curK > 1.01 : !e.ctrlKey || e.type === "wheel") && !e.button)
    .on("zoom", (e) => { curK = e.transform.k; gz.attr("transform", e.transform); svg.style("touch-action", curK > 1.01 ? "none" : "pan-y"); gz.classed("zoomed", curK > 1.5); syncBack(); });
  svg.call(zoom).on("dblclick.zoom", null).style("touch-action", "pan-y");
  const bindUnit = (sel, keyOf) => {
    sel.on("pointerenter pointermove", (e, f) => { if (e.pointerType === "touch") return; hover(keyOf(f), e); })
      .on("pointerleave", () => hover(null))
      .on("click", (e, f) => choose(keyOf(f), { zoomTo: true }));
  };
  bindUnit(houseSel, (f) => `house:${f.properties.id}`);
  bindUnit(stateSel, (f) => `${S.tab}:${f.properties.region.toUpperCase()}`);
  $("map-empty").hidden = true;
  setLayerVisibility(false);
}
function setLayerVisibility() {
  const h = S.tab === "house";
  svg.select(".lay-house").classed("off", !h);
  svg.select(".lay-state").classed("off", h);
}
function unitOfKey(key) {
  if (!key) return null;
  const [office, id] = key.split(":");
  return S.units[office]?.get(id) ?? null;
}
function paint() {
  if (!svg) return;
  const colourSel = (sel, hsel, keyFn) => {
    sel.each(function (f) {
      const u = unitOfKey(keyFn(f)), el = this;
      const c = u ? fillOf(u) : { fill: GREY, hatch: 0 };
      const noModel = S.mode === "proj" && u && u.v.P == null && u.v.projP != null;
      el.style.fill = c.fill;
      el.classList.toggle("est", !!noModel);
      el.classList.toggle("none", !u || (S.mode === "gap" && u.v.gap == null) || (u.v.projP == null && u.v.raw == null && S.mode !== "gap"));
      el.__hatch = c.hatch;
    });
    hsel.each(function (f, i) { this.style.opacity = sel.nodes()[i].__hatch || 0; });
  };
  colourSel(houseSel, houseHatch, (f) => `house:${f.properties.id}`);
  colourSel(stateSel, stateHatch, (f) => `${S.tab === "house" ? "senate" : S.tab}:${f.properties.region.toUpperCase()}`);
  paintMarks();
}
function paintMarks() {
  const key = S.sel;
  gz?.selectAll(".u").classed("sel", function () { return this.dataset.key === key || (key && this.dataset.key === `state:${key.split(":")[1]}` && S.tab !== "house" && key.startsWith(S.tab)); });
  gz?.selectAll(".u.sel").raise();
  gz?.selectAll(".lay-state .u.sel").raise();
}
function legendHTML() {
  const sw = (c, t) => `<span class="lg"><i style="background:${c}"></i>${t}</span>`;
  if (S.mode === "count") return `${sw(D, "D leads")}${sw(R, "R leads")}<span class="lg lg-fade"><i class="fade"></i>faded / hatched: little counted</span>${sw(GREY, "no count yet")}<span class="lg">stronger colour = bigger margin</span>`;
  if (S.mode === "proj") return `${sw(D, "chance D wins: high")}${sw(NEUTRAL, "toss-up")}${sw(R, "chance D wins: low")}<span class="lg lg-dot"><i class="dot"></i>lighter, dotted: no live model, pre-count estimate</span>`;
  return `${sw(ORANGE, "model more Democratic than market")}${sw(PURPLE, "market more Democratic than model")}${sw(GREY, "no model or no usable market")}`;
}

// ---- hover, select, zoom ----
const tip = () => $("tip");
function hover(key, ev) {
  const el = tip();
  gz?.selectAll(".u.hov").classed("hov", false);
  const u = unitOfKey(key);
  if (!u) { el.hidden = true; return; }
  gz.selectAll(`.u[data-key="${S.tab === "house" ? key : `state:${u.st}`}"]`).classed("hov", true).raise();
  gz.selectAll(".u.sel").raise();
  if (!ev) { el.hidden = true; return; }
  el.innerHTML = tipHTML(u);
  el.hidden = false;
  const r = el.getBoundingClientRect(), pad = 14;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 6) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 6) y = ev.clientY - r.height - pad;
  el.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
}
function tipHTML(u) {
  const v = u.v;
  const lead = v.raw == null ? "no count yet" : `${v.raw >= 0.5 ? "D" : "R"} leads, ${marginText(v.raw)}`;
  const mk = v.mkt != null ? `${pc(v.mkt)} (${v.mktSrc})` : v.onesided ? ONESIDED : "–";
  return `<b>${esc(u.name)}</b><span>${esc(tierLabel(u.tier))}</span><div>Counted: ${v.counted != null && v.raw != null ? pc(v.counted) : "–"} · ${lead}</div><div>Model chance D: ${v.P != null ? pc(v.P) : v.projP != null ? `${pc(v.projP)} (pre-count)` : "–"}</div><div>Market: ${mk}</div>${u.aux && S.t == null ? `<div>D ${fmtVotes(u.aux.D)} · R ${fmtVotes(u.aux.R)}${Array.isArray(u.aux.precincts) ? ` · precincts ${u.aux.precincts[0]} of ${u.aux.precincts[1]}` : ""} · count only, no model</div>` : ""}`;
}
function bboxOf(list) {
  const b = [[Infinity, Infinity], [-Infinity, -Infinity]];
  for (const f of list) { const [[x0, y0], [x1, y1]] = path.bounds(f); b[0][0] = Math.min(b[0][0], x0); b[0][1] = Math.min(b[0][1], y0); b[1][0] = Math.max(b[1][0], x1); b[1][1] = Math.max(b[1][1], y1); }
  return b;
}
function zoomToBox(b, ms = 650) {
  const [[x0, y0], [x1, y1]] = b;
  const k = Math.max(1, Math.min(40, 0.86 / Math.max((x1 - x0) / W, (y1 - y0) / H)));
  const t = d3.zoomIdentity.translate(W / 2, H / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
  svg.transition().duration(matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : ms).call(zoom.transform, t);
}
function zoomToState(st, office = S.tab) {
  const list = office === "house" ? feats.house.filter((f) => f.properties.st === st) : feats.state.filter((f) => f.properties.region.toUpperCase() === st);
  if (!list.length) return;
  S.focusSt = st;
  zoomToBox(bboxOf(list));
  syncBack();
}
function syncBack() {
  const b = document.querySelector('.cnt-tools [data-act="back"]');
  if (b) b.hidden = !(S.focusSt && curK > 1.05);
  if (curK <= 1.05) S.focusSt = null;
}
function choose(key, { zoomTo = false, tabSwitch = false } = {}) {
  const u = unitOfKey(key);
  if (!u) return;
  if (tabSwitch && u.office !== S.tab) setTab(u.office, false);
  S.sel = key;
  paintMarks();
  renderPanel();
  markRows();
  if (zoomTo && (S.tab !== "house" || curK < 1.4 || S.focusSt !== u.st)) { if (S.tab === "house") zoomToState(u.st); else if (curK < 1.4) zoomToState(u.st); }
}

// ---- panel ----
const tierNote = {
  registered: "Registered live model (county results)",
  added: "Added live model (county results)",
  civicAPI: "civicAPI model (secondary source; statewide results)",
  "count only": "Count only: no live model; the counted vote from civicAPI",
  "count only (official)": "Count only, no model: official-source totals from the secondary recorder",
  "estimate only": "Estimate only: pre-count estimate, no live data",
};
function caveatsFor(u) {
  const v = u.v, out = [];
  if (NOSPLIT[u.st] && u.tier !== "estimate only" && !u.tier.startsWith("count only")) out.push(`${NOSPLIT[u.st]}: no early / Election Day split in this model. Early and mail ballots counted first distort the share counted and the chance until Election Day precincts report.`);
  if (u.office === "house" && (u.st === "NC" || u.st === "GA") && v.live) out.push(`NC and GA House models are far more Democratic than markets at 50–70% counted; cause not yet verified. Read with caution.`);
  if (v.onesided) out.push("This market price comes from a one-sided order book and reads inverted on a near-certain race, so it is not shown.");
  if (u.tier === "count only") out.push("No live model for this district: only the counted vote is shown. Partial counts can mislead.");
  const stale = S.t == null ? staleMins(u.rows) : null;
  if (stale != null) out.unshift(`Stale feed: the share counted has not changed for ${stale} min.`);
  if (u.aux) out.push("Count only, no model: totals from an official source via a secondary recorder. They do not enter any projection.");
  if (u.tier === "estimate only") out.push("No votes in yet or no live source: shaded by the pre-count estimate.");
  out.push("Not an official call; not financial advice.");
  return out;
}
function renderPanel() {
  const el = $("panel"), u = unitOfKey(S.sel);
  if (!u) {
    el.innerHTML = `<h2 class="cnt-ph">Pick a race</h2><p class="cnt-muted">Hover or click a ${S.tab === "house" ? "district" : "state"} on the map, search above, or click a row of the table. Nothing scrolls when you do.</p><p class="cnt-muted">Colours: <b>Count now</b> is who leads the votes counted; <b>Projection</b> is the model's chance; <b>Model vs market</b> is the two compared.</p>`;
    return;
  }
  const v = u.v, mkRow = (name, x) => `<div class="kv"><span>${name}</span><b>${x}</b></div>`;
  const stName = S.stateName.get(u.st) ?? (S.json.hreg ? u.st : u.st);
  const cand = `<div class="cnt-cands"><span class="c-d"><i></i>${esc(u.cand.d ?? "Democratic candidate")}</span><span class="c-r"><i></i>${esc(u.cand.r ?? "Republican candidate")}</span></div>`;
  const xa = u.aux;
  const auxBlock = () => {
    const link = xa.src?.url ? `<a href="${esc(xa.src.url)}" target="_blank" rel="noopener">${esc(xa.src.name ?? xa.source ?? "source")}</a>` : esc(xa.src?.name ?? xa.source ?? "official source");
    return `${mkRow("Democrat", fmtVotes(xa.D))}${mkRow("Republican", fmtVotes(xa.R))}${xa.other ? mkRow("Other", fmtVotes(xa.other)) : ""}${mkRow("Counted D share", `${pc(v.raw, 1)} <small>${marginText(v.raw)}</small>`)}`
      + `${mkRow("Reporting", v.counted != null ? pc(v.counted) : "not given")}${Array.isArray(xa.precincts) ? mkRow("Precincts", `${xa.precincts[0].toLocaleString("en-US")} of ${xa.precincts[1].toLocaleString("en-US")}`) : ""}`
      + `<div class="kv"><span>Source</span><b>${link}</b></div>${xa.as_of ? mkRow("As of", esc(Number.isFinite(xa.t) ? clockText(xa.t) : xa.as_of)) : ""}<div class="kv"><span>Tag</span><b><span class="pb-tag t-early">count only, no model</span></b></div>`;
  };
  const counted = xa && S.t == null && v.raw != null ? auxBlock() : v.counted != null && v.raw != null
    ? `${mkRow("Counted", `${pc(v.counted)} <small>of expected votes</small>`)}<div class="bar"><i style="width:${Math.min(100, 100 * v.counted)}%"></i></div>${mkRow("Counted D share", `${pc(v.raw, 1)} <small>${marginText(v.raw)}</small>`)}${mkRow("Votes counted", fmtVotes(v.votes))}`
    : mkRow("Counted", "no count yet");
  const proj = v.P != null
    ? `${mkRow("Projected D share", `${pc(v.share, 1)} <small>± ${v.sd != null ? (100 * v.sd).toFixed(1) : "–"} pts</small>`)}${mkRow("Model chance D wins", pc(v.P, v.P > 0.98 || v.P < 0.02 ? 1 : 0))}`
    : u.est ? `${mkRow("Pre-count estimate", `${pc(u.est.share, 1)} D <small>chance ${pc(u.est.P)}</small>`)}` : mkRow("Projection", "none");
  const mkt = v.mkt != null ? `${pc(v.mkt)} <small>${v.mktSrc}</small>` : v.onesided ? `<span class="cnt-warn">${ONESIDED}</span>` : "–";
  const pmLine = `${mkRow("Market chance D wins", mkt)}${v.live && v.pmU.v != null && v.kU.v != null ? mkRow("Other market", `${pc(v.mktSrc === "Polymarket" ? v.kU.v : v.pmU.v)} <small>${v.mktSrc === "Polymarket" ? "Kalshi" : "Polymarket"}</small>`) : ""}${v.gap != null ? mkRow("Model − market", `${v.gap >= 0 ? "+" : "−"}${Math.abs(100 * v.gap).toFixed(0)} pts`) : ""}`;
  const alt = u.alt ? (() => { const r = rowAt(u.alt.rows, S.t); return r && r.counted > 0 ? mkRow(`${u.alt.tier} model`, `${pc(r.P)} chance, ${pc(r.share, 1)} D`) : ""; })() : "";
  const upd = v.t ? `${clockText(v.t)}${S.t != null ? " (replay)" : ""}` : "no live update";
  el.innerHTML = `<h2 class="cnt-ph">${esc(u.name)}</h2><p class="cnt-sub">${esc(stName)} · <span class="tier t-${u.tier.replace(/\W+/g, "-").replace(/-$/, "")}">${esc(tierLabel(u.tier))}</span></p>${cand}
    <div class="blk">${counted}</div><div class="blk">${proj}${alt}</div><div class="blk">${pmLine}</div>
    <p class="cnt-sub">${esc(tierNote[u.tier])}. Last update ${esc(upd)}.</p>
    <ul class="cnt-cavs">${caveatsFor(u).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
}

// ---- projections banner: the election page's rules (dash-events.js), fed the same rows ----
const EV_KEY = `ubiu.count.events.${window.__BUILD ?? ""}`;
let EV = null;
const evSample = (r) => ({ t: r.t, p: r.P ?? null, c: r.counted ?? null, pm: r.pm ?? null, k: r.kalshi ?? null, raw: r.raw ?? null, vp: r.vp ?? null, rem: r.rem ?? null, exp: r.exp ?? null });
function feedEvents() {
  if (!S.evSrc) return;
  EV ??= createEvents(loadSaved(EV_KEY));
  for (const src of S.evSrc.values()) {
    const r = src.race, from = EV.lastT(`race:${src.id}`), fresh = src.rows.filter((d) => d.t > from);
    if (!fresh.length || r.stale || src.feed === false) continue;
    EV.feedRace({ id: src.id, label: r.name ?? src.id, a: r.a, b: r.b, secondary: src.tier === "civicAPI" ? true : src.tier === "added" ? addedLabel(r) : false, partial: partialOf(r) }, fresh.map(evSample));
  }
  save(EV_KEY, EV);
}
// minutes the counted share has been unchanged (a race still counting), or null
function staleMins(rows) {
  const last = rows?.[rows.length - 1];
  if (!last || last.counted == null || !(last.counted > 0) || last.counted >= 0.99) return null;
  let i = rows.length - 1;
  while (i > 0 && rows[i - 1].counted === last.counted) i--;
  const m = Math.round((last.t - rows[i].t) / 60000);
  return m >= 10 ? m : null;
}
const EARLY_RISK = new Set(["FL", "SC", "OH", "NE", "NH"]), SPLIT_RISK = new Set(["NC", "GA", "TX", "AZ"]);
const TAGS = {
  stale: { label: "stale feed", cls: "t-stale" },
  disagree: { label: "disagrees with market", cls: "t-dis" },
  split: { label: "split model under-corrects", cls: "t-split" },
  early: { label: "early-ballot risk", cls: "t-early" },
  ok: { label: "holds up so far", cls: "t-ok" },
};
// reliability tags of a projected race, from the rows the page holds now. Returns [{tag, why}] most severe first.
function reliabilityOf(src, unit, side, mk) {
  const out = [], rows = src.rows, last = rows[rows.length - 1], st = unit.st;
  if (last && last.counted != null && last.counted < 0.99) {
    let i = rows.length - 1;
    while (i > 0 && rows[i - 1].counted === last.counted) i--;
    const mins = Math.round((last.t - rows[i].t) / 60000);
    const silent = Math.round((Date.now() - last.t) / 60000);
    if (src.race.stale || mins >= 10) out.push({ tag: "stale", why: src.race.stale ? "The feed for this race has stopped updating." : `Share counted unchanged for ${mins} min: the feed may have stalled.` });
    else if (silent >= 10 && S.lastOk && Date.now() - S.lastOk < 120000) out.push({ tag: "stale", why: `No new row for this race for ${silent} min.` });
  }
  if (mk.v != null && last?.P != null) {
    const mine = side === "a" ? last.P : 1 - last.P, gap = Math.abs(mine - mk.v);
    if (gap > 0.3) out.push({ tag: "disagree", why: `Model ${pc(mine)} and market ${pc(mk.v)} differ by ${Math.round(100 * gap)} pts.` });
  }
  if (unit.office === "house" && SPLIT_RISK.has(st)) out.push({ tag: "split", why: "Calibrated on 2024, when early and Election Day votes ran alike; tonight's gap is far larger." });
  if (EARLY_RISK.has(st) || src.tier === "civicAPI" || src.tier === "added") out.push({ tag: "early", why: "No early / Election Day split here: early ballots counted first can read as a swing." });
  if (!out.length) out.push({ tag: "ok", why: "None of the known risks apply so far." });
  return out;
}
// the market's price for the side the model projects, now; a price at or under 1 cent for that side is opposite a near-certain model: one-sided book
function sideMarket(last, side) {
  let one = false;
  for (const [v, name] of [[last?.pm, "Polymarket"], [last?.kalshi, "Kalshi"]]) {
    if (v == null) continue;
    const x = side === "a" ? v : 1 - v;
    if (x <= 0.01) { one = true; continue; }
    return { v: x, name, one };
  }
  return { v: null, name: null, one };
}
let pbSeen = null, pbOpen = false, pbSig = "";
function renderProjections() {
  const host = $("proj-list");
  if (!host || !EV || !S.evSrc) return;
  const all = EV.items().filter((i) => i.kind === "call" || i.kind === "withdrawn" || i.kind === "decided");
  const decided = EV.toJSON().decided ?? {}, byId = new Map(all.map((i) => [i.id, i]));
  const rowsHTML = all.map((i) => {
    const raceId = i.key.replace(/^(race:|decided:)/, ""), src = S.evSrc.get(raceId);
    if (!src) return null;
    const unit = unitOfKey(src.unitKey);
    if (!unit) return null;
    const call = i.kind === "withdrawn" ? byId.get(i.ref) : i;
    const side = i.kind === "decided" ? decided[raceId]?.side : call?.side, party = i.kind === "decided" ? decided[raceId]?.party : partyOf(src.race[side]);
    const at = rowAt(src.rows, i.ts), last = src.rows[src.rows.length - 1];
    const cls = `pb-item${party ? ` pb-${party.toLowerCase()}` : ""}${i.kind === "withdrawn" ? " pb-wd" : ""}${i.struck ? " pb-struck" : ""}`;
    let meta = "";
    if (i.kind !== "withdrawn" && side) {
      const mk = sideMarket(last, side), rel = reliabilityOf(src, unit, side, mk), main = rel[0];
      const mtxt = mk.v != null ? `market now ${pc(mk.v)} (${mk.name})` : mk.one ? "market unavailable (one-sided book)" : "no market price";
      meta = `<span class="pb-meta">${at?.counted != null ? `${pc(at.counted)} counted then` : ""} · ${esc(mtxt)}</span>` + rel.map((r, n) => `<span class="pb-tag ${TAGS[r.tag].cls}" title="${esc(r.why)}">${TAGS[r.tag].label}</span>${n === 0 ? `<span class="pb-why">${esc(r.why)}</span>` : ""}`).join("");
    }
    return { id: i.id, html: `<li class="${cls}" data-key="${unit.key}" data-id="${esc(i.id)}" tabindex="0" role="button"><span class="pb-t">${etClock(i.ts)}</span><span class="pb-x">${esc(i.text)}</span>${meta}</li>` };
  }).filter(Boolean);
  const sig = rowsHTML.map((r) => r.html).join("") + pbOpen;
  if (sig === pbSig) return;
  pbSig = sig;
  const fresh = pbSeen ? new Set(rowsHTML.filter((r) => !pbSeen.has(r.id)).map((r) => r.id)) : new Set();
  pbSeen = new Set(rowsHTML.map((r) => r.id));
  const show = pbOpen ? rowsHTML : rowsHTML.slice(0, 3);
  host.innerHTML = rowsHTML.length
    ? show.map((r) => (fresh.has(r.id) ? r.html.replace('class="pb-item', 'class="pb-item pb-new') : r.html)).join("")
    : `<li class="pb-none">No projections yet. A race appears here when the model's chance for one side is 99% or more on two refreshes in a row with at least 20% of the vote counted.</li>`;
  const more = $("proj-more");
  more.hidden = rowsHTML.length <= 3;
  more.textContent = pbOpen ? "Show fewer" : `Show all (${rowsHTML.length})`;
  more.setAttribute("aria-expanded", String(pbOpen));
  $("proj-n").textContent = rowsHTML.length ? `${rowsHTML.length} so far` : "";
}

// ---- summary and status ----
function renderSummary() {
  const card = (title, m) => {
    let cnt = 0, tot = 0;
    const live = { D: 0, R: 0, T: 0 }, pre = { D: 0, R: 0, T: 0 };
    for (const u of m.values()) {
      tot++;
      if ((u.v.counted > 0 && u.v.counted < 0.995) || (u.aux && u.v.raw != null && u.v.counted == null)) cnt++;
      const a = lean(u.v.projP), b = lean(u.est?.P);
      if (a) live[a]++; if (b) pre[b]++;
    }
    return `<div class="cnt-card"><h3>${title}</h3><p class="big"><span class="d">${live.D} D</span> · <span class="t">${live.T} toss-up</span> · <span class="r">${live.R} R</span></p><p class="sm">by projection now; pre-count <span class="d">${pre.D} D</span> · ${pre.T} · <span class="r">${pre.R} R</span></p><p class="sm"><b>${cnt}</b> of ${tot} counting now</p></div>`;
  };
  $("summary").innerHTML = card("House seats", S.units.house) + card("Senate races", S.units.senate) + card("Governor races", S.units.governor);
}
function renderStatus() {
  const chip = $("status"), txt = $("status-text"), note = $("status-note");
  let latest = S.inkyT ?? 0;
  for (const m of Object.values(S.units)) for (const u of m.values()) { const r = u.rows?.[u.rows.length - 1]; if (r && r.t > latest) latest = r.t; }
  let state, text;
  if (!S.lastOk) { state = S.errs >= 2 ? "recon" : "wait"; text = S.errs >= 2 ? "Reconnecting…" : "Waiting for the live feed…"; }
  else if (S.errs >= 2) { state = "recon"; text = "Reconnecting… showing the last data"; }
  else {
    const age = Math.max(0, Math.round((Date.now() - latest) / 1000));
    state = age > 600 ? "stale" : "live";
    text = `${age > 600 ? "Stale" : "Live"} · updated ${age < 120 ? `${age} s` : `${Math.round(age / 60)} min`} ago`;
  }
  chip.dataset.state = state;
  if (txt.textContent !== text) txt.textContent = text;
  note.textContent = S.lastOk ? `Data to ${clockText(latest)} · refreshed every 30 s` : "";
}

// ---- table ----
const COLS = [
  { k: "name", t: "Race", get: (u) => u.name, s: (u) => (u.office === "house" ? u.id : u.name) },
  { k: "tier", t: "Source", get: (u) => u.tier, s: (u) => u.tier },
  { k: "counted", t: "Counted", get: (u) => (u.v.counted != null && u.v.raw != null ? pc(u.v.counted) : "–"), s: (u) => (u.v.raw != null ? u.v.counted : null), num: true },
  { k: "lead", t: "Count leader", get: (u) => (u.v.raw == null ? "–" : `<span class="${u.v.raw >= 0.5 ? "d" : "r"}">${marginText(u.v.raw)}</span>`), s: (u) => (u.v.raw == null ? null : u.v.raw), num: true },
  { k: "share", t: "Projected D", get: (u) => (u.v.P != null ? pc(u.v.share, 1) : u.est ? `<i class="pre">${pc(u.est.share, 1)}</i>` : "–"), s: (u) => u.v.share ?? u.est?.share ?? null, num: true },
  { k: "P", t: "Chance D", get: (u) => (u.v.P != null ? pc(u.v.P) : u.est?.P != null ? `<i class="pre">${pc(u.est.P)}</i>` : "–"), s: (u) => u.v.projP, num: true },
  { k: "mkt", t: "Market", get: (u) => (u.v.mkt != null ? pc(u.v.mkt) : u.v.onesided ? "<i class='pre'>one-sided</i>" : "–"), s: (u) => u.v.mkt, num: true },
  { k: "gap", t: "Model − market", get: (u) => (u.v.gap != null ? `${u.v.gap >= 0 ? "+" : "−"}${Math.abs(100 * u.v.gap).toFixed(0)}` : "–"), s: (u) => u.v.gap, num: true },
];
function tableRows() {
  let list = [...S.units[S.tab].values()];
  const f = S.filter;
  if (f === "counting") list = list.filter((u) => u.v.raw != null && (u.v.counted == null || u.v.counted > 0));
  else if (f === "close") list = list.filter((u) => u.v.projP != null).sort((a, b) => Math.abs(a.v.projP - 0.5) - Math.abs(b.v.projP - 0.5)).slice(0, 40);
  else if (f === "gap") list = list.filter((u) => u.v.gap != null).sort((a, b) => Math.abs(b.v.gap) - Math.abs(a.v.gap)).slice(0, 40);
  const col = COLS.find((c) => c.k === S.sort.col);
  if (col && !S.sortAuto) list.sort((a, b) => {
    const x = col.s(a), y = col.s(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y, "en", { numeric: true }) : x - y) * S.sort.dir;
  });
  return list;
}
function renderTable() {
  const head = document.querySelector("#race-table thead"), body = document.querySelector("#race-table tbody");
  head.innerHTML = `<tr>${COLS.map((c) => `<th scope="col" ${S.sort.col === c.k ? `aria-sort="${S.sort.dir > 0 ? "ascending" : "descending"}"` : ""} class="${c.num ? "n" : ""}"><button type="button" data-sort="${c.k}">${c.t}${S.sort.col === c.k ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</button></th>`).join("")}</tr>`;
  const list = tableRows(), focusKey = document.activeElement?.closest?.("tr[data-key]")?.dataset.key;
  body.innerHTML = list.map((u) => `<tr tabindex="0" data-key="${u.key}"${u.key === S.sel ? ' class="sel" aria-selected="true"' : ""}>${COLS.map((c) => `<${c.k === "name" ? 'th scope="row"' : "td"} class="${c.num ? "n" : ""}">${c.k === "name" || c.k === "tier" ? esc(c.get(u)) : c.get(u)}</${c.k === "name" ? "th" : "td"}>`).join("")}</tr>`).join("") || `<tr><td colspan="${COLS.length}" class="cnt-muted">No races match yet.</td></tr>`;
  $("tbl-n").textContent = `${list.length} race${list.length === 1 ? "" : "s"}`;
  if (focusKey) body.querySelector(`tr[data-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
}
function markRows() {
  document.querySelectorAll("#race-table tbody tr[data-key]").forEach((tr) => { const on = tr.dataset.key === S.sel; tr.classList.toggle("sel", on); if (on) tr.setAttribute("aria-selected", "true"); else tr.removeAttribute("aria-selected"); });
}

// ---- time slider and replay ----
function timeBounds() {
  let lo = Infinity, hi = -Infinity;
  for (const m of Object.values(S.units)) for (const u of m.values()) if (u.rows?.length) { lo = Math.min(lo, u.rows[0].t); hi = Math.max(hi, u.rows[u.rows.length - 1].t); }
  if (!Number.isFinite(lo)) return [0, 0];
  return [Math.max(lo, S.hist ? NIGHT_START : 0), hi];
}
function syncSlider() {
  const [lo, hi] = timeBounds(), n = Math.max(0, Math.round((hi - lo) / 60000)), r = $("t-range");
  S.tRange = [lo, hi];
  r.max = String(n);
  r.disabled = n === 0;
  const val = S.t == null ? n : Math.max(0, Math.min(n, Math.round((S.t - lo) / 60000)));
  r.value = String(val);
  $("t-label").textContent = S.t == null ? (hi ? `Live · ${clockText(hi)}` : "Live") : `As of ${clockText(S.t)}`;
  $("t-live").disabled = S.t == null;
  $("t-play").textContent = S.playing ? "Pause" : "Play";
  $("t-play").setAttribute("aria-label", S.playing ? "Pause the replay" : "Play the night");
  $("t-note").textContent = S.t == null ? "" : "Replay: count-only districts have no history and show as unreported.";
  if (S.histState === "loading") $("t-note").textContent = "Loading the earlier night…";
}
async function loadHistory() {
  if (S.histState !== "idle" || !S.base) return;
  S.histState = "loading"; syncSlider();
  try {
    const got = await Promise.all(HISTORY.map(async (k) => {
      const f = FILES.find((x) => x.k === k), r = await fetch(S.base + f.p.replace("timeline_tail", "timeline"));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return [k, await r.json()];
    }));
    S.hist = Object.fromEntries(got);
    S.histState = "done";
    rebuild();
  } catch (_) { S.histState = "failed"; $("t-note").textContent = "Could not load the earlier night; the replay covers the last hour."; }
  syncSlider();
}
let playTimer = null;
function setTime(ms) {
  S.t = ms;
  computeAll(); paint(); renderPanel(); renderSummary(); syncSlider();
  scheduleTable();
}
let tableTimer = 0;
function scheduleTable() {
  if (!S.playing) { renderTable(); return; }
  if (!tableTimer) tableTimer = setTimeout(() => { tableTimer = 0; renderTable(); }, 700);
}
function play() {
  if (S.playing) { stop(); return; }
  loadHistory();
  const [lo, hi] = timeBounds();
  if (hi <= lo) return;
  S.playing = true;
  svg.classed("playing", true);
  if (S.t == null || S.t >= hi) S.t = lo;
  const step = () => {
    const speed = Number($("t-speed").value), [, top] = S.tRange;
    const next = S.t + speed * 60000;
    if (next >= top) { stop(); setTime(null); return; }
    setTime(next);
    playTimer = setTimeout(step, 1000);
  };
  setTime(S.t);
  playTimer = setTimeout(step, 1000);
}
function stop() {
  S.playing = false; clearTimeout(playTimer); svg?.classed("playing", false);
  $("t-play").textContent = "Play"; $("t-play").setAttribute("aria-label", "Play the night");
  renderTable();
}

// ---- tabs, modes, search ----
function setTab(tab, redraw = true) {
  S.tab = tab;
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  if (S.sel && !S.sel.startsWith(`${tab}:`)) {
    const u = unitOfKey(S.sel), next = u && S.units[tab].get(u.st);
    S.sel = next && tab !== "house" ? next.key : null;
  }
  S.focusSt = null;
  setLayerVisibility();
  if (redraw) refreshAll();
  if (curK > 1.05) svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity);
}
function setMode(mode) {
  S.mode = mode;
  document.querySelectorAll("#modes [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("legend").innerHTML = legendHTML();
  paint();
}
const norm = (s) => String(s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
function searchResults(q) {
  const n = norm(q);
  if (n.length < 2) return [];
  const out = [], seen = new Set();
  const add = (r) => { if (!seen.has(r.label) && out.length < 9) { seen.add(r.label); out.push(r); } };
  for (const [name, st] of [...S.stateName].map(([c, nm]) => [nm, c]).sort()) {
    if (norm(name).startsWith(n) || (n.length === 2 && st.toLowerCase() === n)) {
      const nh = [...S.units.house.values()].filter((u) => u.st === st).length;
      add({ label: `${name} · House map (${nh})`, office: "house", st });
      for (const o of ["senate", "governor"]) { const u = S.units[o].get(st); if (u) add({ label: `${name} · ${OFFICES[o]}`, key: u.key, office: o, st }); }
    }
  }
  for (const m of [S.units.house, S.units.senate, S.units.governor]) for (const u of m.values()) {
    if (norm(u.id).startsWith(n) && u.office === "house") add({ label: `${u.name} · House`, key: u.key, office: u.office, st: u.st });
    else if (norm(u.cand.d).includes(n) || norm(u.cand.r).includes(n)) add({ label: `${u.cand.d && norm(u.cand.d).includes(n) ? u.cand.d : u.cand.r} · ${u.name}`, key: u.key, office: u.office, st: u.st });
  }
  return out;
}
let qItems = [], qActive = -1;
function renderSearch() {
  const list = $("q-list"), q = $("q");
  qItems = searchResults(q.value); qActive = qItems.length ? 0 : -1;
  list.innerHTML = qItems.map((r, i) => `<li role="option" id="q-o${i}" data-i="${i}"${i === qActive ? ' aria-selected="true"' : ""}>${esc(r.label)}</li>`).join("");
  list.hidden = !qItems.length;
  q.setAttribute("aria-expanded", String(!list.hidden));
  q.removeAttribute("aria-activedescendant");
  if (qActive >= 0) q.setAttribute("aria-activedescendant", "q-o0");
}
function pickResult(r) {
  if (!r) return;
  $("q").value = ""; $("q-list").hidden = true; $("q").setAttribute("aria-expanded", "false");
  if (r.office !== S.tab) setTab(r.office, false);
  if (r.key) choose(r.key, {}); else { S.sel = null; paintMarks(); renderPanel(); markRows(); }
  refreshAll();
  zoomToState(r.st, r.office);
}

// ---- refresh everything that depends on the data ----
function refreshAll() {
  if (!S.units.house.size) return;
  paint();
  $("legend").innerHTML = legendHTML();
  renderSummary(); renderStatus(); renderPanel(); syncSlider(); renderProjections();
  if (!S.playing) renderTable();
}

// ---- boot ----
async function boot() {
  const [dist, markets, overview] = await Promise.all([getStatic("us2026/house/districts.json"), getStatic("us2026/house/markets.json"), getStatic("us2026/overview.json")]);
  Object.assign(S, { dist, markets, overview });
  await initMap();
  // wire controls
  $("tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b && b.dataset.tab !== S.tab) setTab(b.dataset.tab); });
  $("modes").addEventListener("click", (e) => { const b = e.target.closest("[data-mode]"); if (b) setMode(b.dataset.mode); });
  document.querySelector(".cnt-tools").addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act, to = (f) => svg.transition().duration(250).call(zoom.scaleBy, f);
    if (act === "in") to(1.6); else if (act === "out") to(1 / 1.6);
    else if (act === "fit" || act === "back") { S.focusSt = null; svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity); }
  });
  $("q").addEventListener("input", renderSearch);
  $("q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!qItems.length) return; e.preventDefault();
      qActive = (qActive + (e.key === "ArrowDown" ? 1 : -1) + qItems.length) % qItems.length;
      $("q-list").querySelectorAll("li").forEach((li, i) => li.toggleAttribute("aria-selected", i === qActive));
      $("q").setAttribute("aria-activedescendant", `q-o${qActive}`);
    } else if (e.key === "Enter") { e.preventDefault(); pickResult(qItems[Math.max(0, qActive)]); }
    else if (e.key === "Escape") { $("q-list").hidden = true; $("q").setAttribute("aria-expanded", "false"); }
  });
  $("q-list").addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) { e.preventDefault(); pickResult(qItems[Number(li.dataset.i)]); } });
  $("q").addEventListener("blur", () => { $("q-list").hidden = true; $("q").setAttribute("aria-expanded", "false"); });
  const tbl = $("race-table");
  tbl.addEventListener("click", (e) => {
    const sb = e.target.closest("[data-sort]");
    if (sb) { const k = sb.dataset.sort; S.sort = { col: k, dir: S.sort.col === k ? -S.sort.dir : k === "name" || k === "tier" ? 1 : -1 }; S.sortAuto = false; renderTable(); return; }
    const tr = e.target.closest("tr[data-key]");
    if (tr) choose(tr.dataset.key, { zoomTo: false });
  });
  tbl.addEventListener("keydown", (e) => { const tr = e.target.closest?.("tr[data-key]"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choose(tr.dataset.key); } });
  tbl.addEventListener("pointerover", (e) => { const tr = e.target.closest("tr[data-key]"); if (tr && e.pointerType !== "touch") hover(tr.dataset.key, null); });
  tbl.addEventListener("pointerleave", () => hover(null));
  $("flt").addEventListener("change", (e) => { S.filter = e.target.value; S.sortAuto = S.filter === "close" || S.filter === "gap"; renderTable(); });
  $("proj-more").addEventListener("click", () => { pbOpen = !pbOpen; renderProjections(); });
  const pbPick = (el) => { const li = el.closest?.("li[data-key]"); if (!li) return; const u = unitOfKey(li.dataset.key); if (!u) return; if (u.office !== S.tab) setTab(u.office, false); choose(u.key, {}); refreshAll(); zoomToState(u.st, u.office); };
  $("proj-list").addEventListener("click", (e) => pbPick(e.target));
  $("proj-list").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pbPick(e.target); } });
  $("t-play").addEventListener("click", play);
  $("t-live").addEventListener("click", () => { stop(); setTime(null); });
  $("t-range").addEventListener("input", (e) => {
    const [lo] = S.tRange, n = Number(e.target.max), v = Number(e.target.value);
    loadHistory();
    if (S.playing) stop();
    setTime(v >= n ? null : lo + v * 60000);
  });
  $("t-range").addEventListener("pointerdown", () => loadHistory(), { once: true });
  // empty state before live data arrives: statics are enough for the pre-count map
  rebuild();
  S.filter = "counting";
  setInterval(renderStatus, 1000);
  cycle();
  window.__cnt = { S, choose, setTab, setMode, setTime, pickResult, searchResults };
  document.body.dataset.ready = "1";
}
boot().catch((e) => window.__showLoadError?.(e));
