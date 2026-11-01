// groups.html: the US count by kind of place. Reads the US dashboard's files (us2026/meta.json, us2026/units/<race>.json and,
// where served, civic/us2026/meta.json + units) from data/ or, with ?live=<base>, from the live server every 15 s, plus
// data/us2026_groups/{groups,counties}.json (analysis/us_groups.py). All figures come from groups-agg.js.
import * as d3 from "./vendor/d3.js";
import { LIVE, STATIC, getJSON, esc, param, sideLabel } from "./common.js";
import { prepRace, frameAt, membersOf, aggregate, baseline, timesOf, nationalPick, COMPLETE_AT, PARTIAL_MIN, MIN_COMPLETE_N, MIN_COMPLETE_EV } from "./groups-agg.js";

const EID = "us2026", CIVIC = "civic/us2026", GDIR = "us2026_groups";
const TZ = "America/New_York";
const TICK_MS = 15000;
const PALETTE = ["#1b1b1b", "#c4501b", "#2f8a6c", "#8a6fb0", "#c49a1b", "#6a7f99", "#b0577a", "#4f8fb8", "#7a7a3a"];
const $ = (id) => document.getElementById(id);

const S = {
  meta: null, groups: null, counties: null, index: null,
  races: [],                 // registered races with units, then civicAPI races of other states
  preps: new Map(),          // race_id -> prepared units file
  stamp: new Map(),          // race_id -> last frame time held
  fetched: new Map(),        // race_id -> ms of the last read
  view: param("race") ?? "all", vs: param("vs") === "o22" ? "o22" : "p24", tab: param("tab"), dim: param("dim") ?? "place", regSt: param("st"),
  names: new Map(),          // state id -> name (meta.regions)
  updates: 0, ticks: 0, lastOk: null, failed: false, civicTry: 0, civicSkip: 0,
};
window.__groups = { ready: false, live: !!LIVE, updates: 0, headlines: [] };

const clockET = (t) => new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ }).format(new Date(t));
const pct = (x, d = 0) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);
const votes = (v) => (v == null ? "–" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}k` : d3.format(",")(Math.round(v)));
const office = (r) => String(r.race_id).split("-").pop();

// side letters and colours of the view: D / R nationally, the race's own sides otherwise
function sides() {
  const r = S.view !== "all" ? S.races.find((x) => x.race_id === S.view) : null;
  if (!r) return { a: "D", b: "R", ca: "#1f5fbf", cb: "#c8102e" };
  return { a: sideLabel(r.a, true, r.b), b: sideLabel(r.b, true, r.a), ca: r.a?.color ?? "#1f5fbf", cb: r.b?.color ?? "#c8102e" };
}
// a margin or shift in points with the side it favours: "D+2.1", "R+0.4", "even"
function mg(m, sd = sides()) {
  if (m == null || !Number.isFinite(m)) return "–";
  const p = Math.abs(100 * m);
  if (p < 0.05) return "even";
  return `${m > 0 ? sd.a : sd.b}+${p.toFixed(1)}`;
}
const mgHTML = (m, sd = sides()) => (m == null ? "–" : `<span style="color:${m > 0 ? sd.ca : m < 0 ? sd.cb : "inherit"}">${esc(mg(m, sd))}</span>`);

/* ---------------------------------------------------------------- data */

async function loadBase() {
  const [groups, counties, meta] = await Promise.all([
    getJSON(`${GDIR}/groups.json`, { root: STATIC }), getJSON(`${GDIR}/counties.json`, { root: STATIC }), getJSON(`${EID}/meta.json`),
  ]);
  S.groups = groups;
  S.counties = counties;
  S.index = new Map(counties.fips.map((f, i) => [f, i]));
  S.meta = meta;
  S.races = meta.races.filter((r) => r.units).map((r) => ({ ...r, path: `${EID}/${r.units}` }));
  for (const r of meta.regions ?? []) S.names.set(r.id, r.name);
}

const civicWanted = () => !!LIVE;
// civicAPI races (a separate process, optional): only for states without a registered race of the same office. Asked for
// only once a registered race shows counted votes (the civic process writes its files long before that), so an absent
// folder never costs a logged 404 before the night starts.
const countSeen = () => [...S.preps.values()].some((p) => p.frames.some((fr) => fr.t > -8e15 && fr.f.some((x) => x > 0)));
async function loadCivic() {
  if (!civicWanted() || !countSeen() || S.races.some((r) => r.civic)) return false;
  if (S.civicSkip > 0) { S.civicSkip--; return false; }
  let meta = null;
  try { meta = await getJSON(`${CIVIC}/meta.json`, { optional: true }); } catch { return false; }
  const taken = new Set(S.races.map((r) => `${r.region}-${office(r)}`));
  const add = (meta?.races ?? []).filter((c) => c.units && c.region && c.a && c.b && !taken.has(`${c.region}-${office(c)}`))
    .map((c) => ({ ...c, civic: true, name: `${c.name} (civicAPI)`, path: `${CIVIC}/${c.units}` }));
  if (!add.length) { S.civicTry++; S.civicSkip = Math.min(8, 2 ** (S.civicTry - 1)); return false; }
  S.races.push(...add);
  for (const r of meta.regions ?? []) if (!S.names.has(r.id)) S.names.set(r.id, r.name);
  return true;
}

// read one race's units file; true when it holds a newer frame than before
async function loadRace(r) {
  const file = await getJSON(r.path, { optional: true });
  S.fetched.set(r.race_id, Date.now());
  if (!file?.unit_ids?.length) return false;
  const prep = prepRace(file, r, S.counties, S.index);
  const last = prep.frames.at(-1);
  const sig = `${prep.frames.length}|${last?.t}|${last?.f?.reduce?.((s, x) => s + x, 0)}`;
  const changed = S.stamp.get(r.race_id) !== sig;
  const had = S.preps.get(r.race_id);
  if (had && !changed) return false;
  prep.mem = new Map();
  S.preps.set(r.race_id, prep);
  S.stamp.set(r.race_id, sig);
  return true;
}

const viewRaces = () => (S.view === "all" ? nationalPick(S.races) : S.races.filter((r) => r.race_id === S.view));

// read the races of the view that are due (all of them on the first read)
async function refresh(force = false) {
  const due = viewRaces().filter((r) => force || !S.preps.has(r.race_id) || Date.now() - (S.fetched.get(r.race_id) ?? 0) >= (S.view === "all" ? 2 * TICK_MS : TICK_MS) - 500);
  const out = await Promise.allSettled(due.map(loadRace));
  const bad = out.filter((o) => o.status === "rejected");
  S.failed = bad.length > 0;
  if (bad.length) console.warn("groups: read failed", bad[0].reason?.message ?? bad[0].reason);
  if (!bad.length || out.length > bad.length) S.lastOk = Date.now();
  return out.some((o) => o.status === "fulfilled" && o.value);
}

/* ---------------------------------------------------------------- groups */

// the rows of each card for the current view: [{ id, label, rows: [{ key, label, rule, sel }] }]
function cards() {
  const out = S.groups.dimensions.map((d, k) => ({
    id: d.id, label: d.label, rows: d.groups.map((g, gi) => ({ key: `${d.id}.${g.id}`, label: g.label, rule: g.rule, sel: { dim: k, group: gi } })),
  }));
  const st = regionState();
  const rows = (S.groups.regions[st] ?? []).map((g) => ({
    key: `reg.${st}.${g.id}`, label: g.label, rule: `${g.fips.length} ${g.fips.length === 1 ? "county" : "counties"}`,
    sel: { fips: new Set(g.fips) }, state: st,
  }));
  if (rows.length) out.push({ id: "reg", label: S.view === "all" && regionStates().length > 1 ? "Key regions" : `Key regions: ${stateName(st)}`, rows, pick: S.view === "all" });
  return out;
}
const stateName = (st) => S.names.get(st) ?? st.toUpperCase();
// states of the view with key regions; the regions card shows one of them (the race's state, or ?st= in the national view)
const regionStates = () => [...new Set(viewRaces().map((r) => r.region))].filter((st) => S.groups.regions[st]).sort((a, b) => stateName(a).localeCompare(stateName(b)));
function regionState() {
  const list = regionStates();
  if (list.includes(S.regSt)) return S.regSt;
  return ["ga", "pa", "mi", "az", "nc", "wi", "nv", "tx"].find((s) => list.includes(s)) ?? list[0] ?? null;
}

// [{ prep, k, members }] of a group over the view's races at time t (latest frame when null)
function items(row, t = null) {
  const out = [];
  for (const r of viewRaces()) {
    if (row.state && r.region !== row.state) continue;
    const prep = S.preps.get(r.race_id);
    if (!prep) continue;
    let members = prep.mem.get(row.key);
    if (!members) prep.mem.set(row.key, (members = membersOf(prep, S.counties, row.sel)));
    if (members.length) out.push({ prep, k: frameAt(prep, t), members });
  }
  return out;
}
const ALL = { key: "all", label: "All counties", sel: null };
function allItems(t = null) {
  return viewRaces().map((r) => S.preps.get(r.race_id)).filter(Boolean).map((prep) => ({ prep, k: frameAt(prep, t), members: prep.fips.map((_, j) => j) }));
}

const anyCount = () => viewRaces().some((r) => { const p = S.preps.get(r.race_id); const fr = p?.frames.at(-1); return fr && fr.t > -8e15 && fr.f.some((x) => x > 0); });

/* ---------------------------------------------------------------- render */

function countRow(row, a, sd) {
  const c = a.complete;
  const basis = c.enough ? `${c.n} of ${a.n} · ${pct(c.evShare)} of vote` : a.counted ? `${c.n} of ${a.n} counties complete` : "";
  const shift = c.swing != null ? mgHTML(c.swing, sd) : "—";
  const proj = a.proj.margin != null ? `${mgHTML(a.proj.margin, sd)}<small>${S.vs === "p24" ? "2024" : "2022"}: ${esc(mg(a.proj.base, sd))}</small>` : "–";
  const turn = a.turnout.complete != null || a.turnout.proj != null
    ? `${a.turnout.complete != null ? pct(a.turnout.complete) : "–"}<small>${a.turnout.proj != null ? `projected ${pct(a.turnout.proj)}` : ""}</small>` : "–";
  const part = a.partial != null
    ? `<tr class="gr-partial"><td></td><td colspan="4">partial: ${pct(a.counted)} counted, early/mail ballots often counted first · ${esc(mg(a.partial, sd))}</td></tr>` : "";
  return `<tr data-g="${esc(row.key)}"><th scope="row" title="${esc(row.rule ?? "")}">${esc(row.label)}<small>${a.n} ${a.n === 1 ? "county" : "counties"}</small></th>`
    + `<td class="n">${a.counted == null ? "–" : pct(a.counted)}</td>`
    + `<td class="n gr-hl" data-shift="${c.swing ?? ""}" data-complete="${c.n}">${shift}<small>${esc(basis)}</small></td>`
    + `<td class="n">${proj}</td><td class="n">${turn}</td></tr>${part}`;
}

function baseRow(row, b, sd) {
  const m = b.makeup;
  const mk = [m.ba != null ? `${pct(m.ba)} college degree` : null, m.age65 != null ? `${pct(m.age65)} aged 65+` : null,
    m.medinc != null ? `$${Math.round(m.medinc / 1000)}k income` : null,
    m.white != null ? `${pct(m.white)} white · ${pct(m.black)} Black · ${pct(m.hisp)} Hispanic` : null].filter(Boolean).join(" · ");
  return `<tr data-g="${esc(row.key)}"><th scope="row" title="${esc(row.rule ?? "")}">${esc(row.label)}<small>${esc(mk)}</small></th>`
    + `<td class="n">${b.n}</td><td class="n">${votes(b.ev)}</td>`
    + `<td class="n">${mgHTML(b.p20, sd)}</td><td class="n">${mgHTML(b.o22, sd)}</td><td class="n">${mgHTML(b.p24, sd)}</td></tr>`;
}

function render() {
  const sd = sides();
  const count = S.tab === "count";
  const vsLabel = S.vs === "p24" ? "2024" : "2022";
  const heads = [];
  const html = cards().map((card) => {
    const body = card.rows.map((row) => {
      const it = items(row);
      if (!it.length) return "";
      if (!count) return baseRow(row, baseline(it, S.counties), sd);
      const a = aggregate(it, S.vs);
      heads.push({ key: row.key, label: row.label, n: a.n, counted: a.counted, shown: c2txt(a.complete.swing, sd), value: a.complete.swing, nComplete: a.complete.n,
        evShare: a.complete.evShare, basis: a.complete.basis.map((x) => [x.race, x.fips, x.f]), partialShown: a.partial != null, proj: a.proj.margin });
      return countRow(row, a, sd);
    }).join("");
    if (!body) return "";
    const th = count
      ? `<th>Places</th><th class="n">Counted</th><th class="n">Shift vs ${vsLabel}, fully counted counties</th><th class="n">Projected margin</th><th class="n">Turnout vs expected</th>`
      : `<th>Places</th><th class="n">Counties</th><th class="n">Expected votes</th><th class="n">2020 president</th><th class="n">2022 same office</th><th class="n">2024 president</th>`;
    const pick = card.pick && regionStates().length > 1
      ? ` <select id="gr-st" aria-label="State">${regionStates().map((s) => `<option value="${s}"${s === regionState() ? " selected" : ""}>${esc(stateName(s))}</option>`).join("")}</select>` : "";
    return `<article class="gr-card" data-card="${card.id}"><h3>${esc(card.label)}${pick}</h3><div class="table-scroll"><table class="gr-t"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div></article>`;
  }).join("");
  $("gr-panels").innerHTML = html;
  // the whole view, above the cards
  const all = allItems();
  const a = aggregate(all, S.vs);
  window.__groups = { ...window.__groups, ready: true, view: S.view, vs: S.vs, tab: S.tab, counted: a.counted, updates: S.updates,
    frameT: Math.max(-1, ...all.map((x) => x.prep.frames.at(-1)?.t ?? -1)), headlines: heads, completeAt: COMPLETE_AT, partialMin: PARTIAL_MIN,
    races: Object.fromEntries(all.map((x) => [x.prep.race.race_id, { path: x.prep.race.path, t: x.prep.frames.at(-1)?.t ?? null }])) };
  renderStatus(a, sd);
  renderChart();
}
const c2txt = (v, sd) => (v == null ? "" : mg(v, sd));

function renderStatus(a, sd) {
  const live = anyCount();
  const parts = [];
  if (live) {
    parts.push(`${pct(a.counted)} of the expected vote counted in ${S.view === "all" ? `${viewRaces().length} races, one per state` : "this race"}`);
    if (a.complete.swing != null) parts.push(`fully counted counties shifted ${mg(a.complete.swing, sd)} vs ${S.vs === "p24" ? "2024" : "2022"} (${a.complete.n} of ${a.n})`);
    if (a.proj.margin != null) parts.push(`projected margin ${mg(a.proj.margin, sd)}`);
  } else parts.push("No votes counted yet: past results and makeup of each group");
  if (LIVE && S.lastOk) parts.push(`read ${clockET(S.lastOk)} ET${S.failed ? ", last read failed, retrying" : ""}`);
  $("gr-status").textContent = parts.join(" · ");
  $("gr-key").textContent = S.tab === "count"
    ? `Shift: change in margin from the same counties' ${S.vs === "p24" ? "2024 presidential" : "2022 same-office"} result, only counties with ${COMPLETE_AT / 10}%+ counted, weighted by votes counted; given once those are ${MIN_COMPLETE_N}+ counties and ${pct(MIN_COMPLETE_EV)}+ of the group's expected vote. Projected margin: the model's projected final result for these counties.`
    : "Margins are two-party, weighted by expected votes; makeup is weighted by population (ACS 2020-2024).";
}

/* ---------------------------------------------------------------- chart */

function series() {
  const card = cards().find((c) => c.id === S.dim) ?? cards()[0];
  const preps = viewRaces().map((r) => S.preps.get(r.race_id)).filter(Boolean);
  const times = timesOf(preps).filter((t) => preps.some((p) => { const k = frameAt(p, t); return k >= 0 && p.frames[k].f.some((x) => x > 0); }));
  const lines = card.rows.map((row, i) => ({ key: row.key, label: row.label, color: PALETTE[i % PALETTE.length], pts: [] }));
  for (const t of times) {
    card.rows.forEach((row, i) => {
      const it = items(row, t);
      if (!it.length) return;
      const a = aggregate(it, S.vs);
      lines[i].pts.push({ t, swing: a.complete.swing, proj: a.proj.margin });
    });
  }
  return { card, lines: lines.filter((l) => l.pts.length) };
}

function drawOne(el, lines, field, sd, zeroLabel) {
  const w = Math.max(280, el.clientWidth || 440), h = 240, m = { t: 10, r: 26, b: 26, l: 46 };
  el.innerHTML = "";
  const pts = lines.flatMap((l) => l.pts.filter((p) => p[field] != null));
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", "100%").attr("role", "img")
    .attr("aria-label", zeroLabel);
  if (!pts.length) {
    svg.append("text").attr("x", w / 2).attr("y", h / 2).attr("text-anchor", "middle").attr("class", "gr-empty")
      .text(field === "swing" ? "Not enough fully counted counties yet" : "No count yet");
    return;
  }
  const x = d3.scaleTime().domain(d3.extent(pts, (p) => p.t)).range([m.l, w - m.r]);
  if (x.domain()[0] === x.domain()[1]) x.domain([x.domain()[0] - 6e5, +x.domain()[1] + 6e5]);
  const ext = d3.extent(pts, (p) => p[field] * 100);
  const lim = Math.max(2, Math.abs(ext[0]), Math.abs(ext[1])) * 1.1;
  const y = d3.scaleLinear().domain(field === "swing" ? [-lim, lim] : [Math.min(0, ext[0]) - 2, Math.max(0, ext[1]) + 2]).nice().range([h - m.b, m.t]);
  const fmt = (v) => (v === 0 ? "0" : `${v > 0 ? sd.a : sd.b}+${Math.abs(v)}`);
  svg.append("g").attr("class", "gr-axis").attr("transform", `translate(0,${h - m.b})`)
    .call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(w / 110))).tickFormat((d) => clockET(d)));
  svg.append("g").attr("class", "gr-axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(fmt));
  svg.append("line").attr("class", "gr-zero").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0)).attr("y2", y(0));
  const line = d3.line().defined((p) => p[field] != null).x((p) => x(p.t)).y((p) => y(p[field] * 100));
  for (const l of lines) {
    svg.append("path").attr("class", "gr-line").attr("d", line(l.pts)).attr("stroke", l.color).attr("data-key", l.key);
    const last = [...l.pts].reverse().find((p) => p[field] != null);
    if (last) svg.append("circle").attr("cx", x(last.t)).attr("cy", y(last[field] * 100)).attr("r", 2.6).attr("fill", l.color);
  }
}

function renderChart() {
  const dims = cards();
  if (!dims.some((c) => c.id === S.dim)) S.dim = dims[0].id;
  $("gr-dim").innerHTML = dims.map((c) => `<button type="button" data-dim="${c.id}" aria-pressed="${c.id === S.dim}">${esc(c.label)}</button>`).join("");
  const sd = sides();
  const { lines } = series();
  drawOne($("gr-c1"), lines, "swing", sd, `Shift vs ${S.vs === "p24" ? "2024" : "2022"} in fully counted counties, by group, over the night`);
  drawOne($("gr-c2"), lines, "proj", sd, "Projected margin by group over the night");
  $("gr-legend").innerHTML = lines.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}</span>`).join("");
}

/* ---------------------------------------------------------------- controls */

function fillRaces() {
  const sel = $("gr-race");
  const byState = d3.groups(S.races, (r) => r.region).sort((a, b) => a[0].localeCompare(b[0]));
  sel.innerHTML = `<option value="all">All races, one per state</option>` + byState.map(([, list]) => list.map((r) => `<option value="${esc(r.race_id)}">${esc(r.name)}</option>`).join("")).join("");
  if (S.view !== "all" && !S.races.some((r) => r.race_id === S.view)) S.view = "all";
  sel.value = S.view;
}

function setURL() {
  const u = new URL(location.href);
  for (const [k, v, d] of [["race", S.view, "all"], ["vs", S.vs, "p24"], ["dim", S.dim, "place"], ["tab", S.tab, null], ["st", S.view === "all" ? S.regSt : null, null]]) {
    if (v == null || v === d) u.searchParams.delete(k); else u.searchParams.set(k, v);
  }
  history.replaceState(null, "", u);
}

function pressed(group, attr, value) {
  for (const b of $(group).querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset[attr] === value));
}

function wire() {
  $("gr-race").addEventListener("change", async (e) => {
    S.view = e.target.value;
    setURL();
    await refresh();
    if (!S.tabSet) S.tab = anyCount() ? "count" : "base";
    pressed("gr-tab", "tab", S.tab);
    render();
  });
  $("gr-vs").addEventListener("click", (e) => {
    const v = e.target.closest("button")?.dataset.vs;
    if (!v) return;
    S.vs = v; pressed("gr-vs", "vs", v); setURL(); render();
  });
  $("gr-tab").addEventListener("click", (e) => {
    const v = e.target.closest("button")?.dataset.tab;
    if (!v) return;
    S.tab = v; S.tabSet = true; pressed("gr-tab", "tab", v); setURL(); render();
  });
  $("gr-panels").addEventListener("change", (e) => {
    if (e.target.id !== "gr-st") return;
    S.regSt = e.target.value; setURL(); render();
  });
  $("gr-dim").addEventListener("click", (e) => {
    const v = e.target.closest("button")?.dataset.dim;
    if (!v) return;
    S.dim = v; setURL(); renderChart();
  });
  let rt = null;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(renderChart, 150); });
}

function footer() {
  const civic = S.races.some((r) => r.civic);
  $("gr-foot").innerHTML = `<p>${esc(S.groups.note)}</p>`
    + `<p>Sources: county counts and projections from the US dashboard's files${civic ? `; for some states <a href="https://civicapi.org">civicAPI</a> (secondary source)` : ""}; ${esc(S.groups.source)}. `
    + `Fully counted: ${COMPLETE_AT / 10}% or more of a county's expected vote counted. Groups: hover a name for its rule.</p>`;
}

/* ---------------------------------------------------------------- start */

async function tick() {
  S.ticks++;
  let changed = false;
  if (await loadCivic()) { fillRaces(); footer(); changed = true; }
  if (await refresh()) changed = true;
  if (changed) {
    S.updates++;
    if (!S.tabSet && S.tab === "base" && anyCount()) { S.tab = "count"; pressed("gr-tab", "tab", "count"); }
  }
  render();
}

async function start() {
  await loadBase();
  if (LIVE) $("dash-link").href = `election.html?id=${EID}&live=${encodeURIComponent(LIVE)}`;
  await loadCivic();
  fillRaces();
  footer();
  await refresh(true);
  S.tabSet = S.tab === "count" || S.tab === "base";
  if (!S.tabSet) S.tab = anyCount() ? "count" : "base";
  pressed("gr-vs", "vs", S.vs);
  pressed("gr-tab", "tab", S.tab);
  wire();
  render();
  if (LIVE) setInterval(() => tick().catch((e) => console.warn("groups: refresh failed", e)), TICK_MS);
}

start().catch((e) => {
  $("gr-status").textContent = `The page could not load its data: ${e.message}`;
  console.error(e);
});
