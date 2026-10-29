// US House 2026 (us.html, House tab): the 435 districts from data/us2026/house/ (built by
// analysis/; docs/us_midterms.md "US House districts"). One map on the 2026 lines (or the 2024 lines
// for past results), coloured by the market chance, the district polls, the model-only baseline,
// Harris 2024, the 2024 House result or early vote; or swing arrows by county (2020 to 2024
// president, NYT style). Redrawn states: old seats to new. A sortable table of every district.
// Margins: Democratic minus Republican, points; chances: P(Democratic party wins the seat).
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, num, pct, fmtDate, marginText } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec", ROOT = "us2026/house/";
const ramp = (v) => d3.interpolateLab(NEUTRAL, v >= 0 ? DEM : REP)(Math.min(1, Math.abs(v)) ** 0.8); // v in [-1, 1]
const byP = (p) => (p == null ? null : ramp((p - 0.5) * 2));
const byMargin = (m, full = 30) => (m == null ? null : ramp(m / full));
const EV = d3.interpolateLab("#ece9e1", "#3d5a40");
const day = (d) => (d ? fmtDate(d).replace(/ \d{4}$/, "") : "–");

// colour modes: [key, label, lines (2026 only, or both), value(district, data) -> fill | null, legend]
const MODES = [
  ["market", "Market chance", "2026", (d) => byP(mk(d)), "Chance the Democratic party wins the seat: Polymarket, else Kalshi."],
  ["polls", "District polls", "2026", (d) => byMargin(pollM(d), 20), "District poll average, two-party margin (full colour at ±20)."],
  ["baseline", "Baseline (model only)", "2026", (d) => byP(d.baseline?.p_d), "MODEL ONLY: Harris 2024 shifted by the generic ballot; not a forecast and not traded on."],
  ["pres24", "Harris 2024", "both", (d) => byMargin(d.pres24?.margin), "Harris minus Trump, % of all votes (full colour at ±30); split counties approximated."],
  ["house24", "House 2024", "both", (d) => byMargin(h24(d)?.margin), "2024 House result, D minus R (full colour at ±30); redrawn 2026 districts: their core 2024 district."],
  ["ev", "Early vote", "2026", (d, X) => { const e = X.ev?.districts?.[d.id]; return e ? EV(Math.min(1, e.share_of_2022_total)) : null; }, "Early votes so far as a share of the 2022 total (Texas, Florida, North Carolina)."],
  ["swing", "Swing arrows", "both", () => "#f4f2ec", "Each county's shift from 2020 to 2024 (president): arrow right and red, towards Trump; left and blue, towards Harris; length by the shift."],
];
let X = null; // the files
const mk = (d) => X.markets?.districts?.[d.id]?.pm?.p ?? X.markets?.districts?.[d.id]?.kalshi?.p ?? null;
const pollM = (d) => { const a = X.markets?.districts?.[d.id]?.polls?.average; return a?.avg != null ? (2 * a.avg - 1) * 100 : null; };
const h24 = (d) => d.house24 ?? d.house24_core ?? null;

export async function drawHouse(el, { sortable, sources }) {
  el.innerHTML = `<p class="note">Loading the districts…</p>`;
  const opt = { optional: true };
  const [districts, markets, ev, flows, topo26] = await Promise.all(["districts.json", "markets.json", "earlyvote.json", "flows.json", "districts_2026.topo.json"].map((f) => getJSON(`${ROOT}${f}`, opt)));
  if (!districts || !topo26) { el.innerHTML = `<p class="note">No House district files yet.</p>`; return; }
  X = { districts, markets, ev, flows, topo: { 2026: topo26 }, data: { 2026: districts }, lines: "2026", mode: "market", sel: null };
  const D = Object.entries(districts.districts).map(([id, d]) => ({ id, ...d }));
  const gb = markets?.generic_ballot ?? districts.national?.generic_ballot;
  const pm = D.map(mk).filter((p) => p != null);
  const base = D.map((d) => d.baseline?.p_d).filter((p) => p != null);
  el.innerHTML = `<p class="hub-lede">Generic ballot: Democrats ${pct(gb?.avg)} of the two-party vote (± ${(100 * (gb?.sd ?? 0)).toFixed(1)}), ${gb?.n_polls ?? 0} recent polls. Markets: Democrats favoured in <b>${pm.filter((p) => p > 0.5).length}</b> of ${pm.length} priced seats, about <b>${d3.sum(pm).toFixed(0)}</b> expected; 218 for a majority. <span class="note">Baseline (model only): ${d3.sum(base).toFixed(0)}.</span></p>
    <div class="toggle hx-modes" role="group" aria-label="Map colour">${MODES.map(([k, l], i) => `<button type="button" data-mode="${k}" aria-pressed="${i === 0}">${l}</button>`).join("")}</div>
    <div class="toggle hx-lines" role="group" aria-label="District lines"><button type="button" data-lines="2026" aria-pressed="true">2026 lines</button><button type="button" data-lines="2024">2024 lines</button></div>
    <div class="hx-wrap"><div id="hx-map" class="hub-map"></div><div class="hx-card place" id="hx-card" hidden></div></div>
    <p class="legend-note" id="hx-legend"></p>
    <div id="hx-sel"></div>
    <h3 class="tab-h">Redrawn since 2024</h3><div class="toggle hx-states" role="group" aria-label="Redrawn state">${(districts.redrawn ?? []).map((st, i) => `<button type="button" data-st="${st}" aria-pressed="${i === 0}">${st}</button>`).join("")}</div><div id="hx-flow"></div>
    <h3 class="tab-h">Every district</h3><label class="select"><span>District</span> <input id="hx-filter" type="search" placeholder="TX, TX-07, Fletcher…" aria-label="Filter districts"></label><div class="hub-scroll" id="hx-table"></div>
    <p class="note">${esc(markets?.note ?? "")} ${esc(districts.notes?.pres24 ?? "")}</p>${sources(districts)}${sources(markets)}`;
  await drawMap();
  el.querySelectorAll(".hx-modes button").forEach((b) => b.addEventListener("click", () => { X.mode = b.dataset.mode; drawMap(); }));
  el.querySelectorAll(".hx-lines button").forEach((b) => b.addEventListener("click", () => { X.lines = b.dataset.lines; drawMap(); }));
  el.querySelectorAll(".hx-states button").forEach((b) => b.addEventListener("click", () => drawFlow(b.dataset.st)));
  drawFlow(districts.redrawn?.[0]);
  const rows = D.map((d) => {
    const m = markets?.districts?.[d.id] ?? {};
    return { ...d, inc: h24(d)?.incumbent ?? "", incp: h24(d)?.incumbent_party ?? "", pm: m.pm?.p ?? null, kalshi: m.kalshi?.p ?? null, poll: pollM(d), base: d.baseline?.p_d ?? null, h: pres(d), house: h24(d)?.margin ?? null, sw: d.swing_pres ?? null };
  });
  const table = () => {
    const f = $("hx-filter").value.trim().toLowerCase();
    sortable($("hx-table"), "house", [
      ["id", "District", false, (r) => `<button type="button" class="linkish" data-id="${esc(r.id)}">${esc(r.id)}</button>${r.redrawn ? ` <small>new lines</small>` : ""}`],
      ["inc", "Incumbent", false, (r) => (r.inc ? `${esc(r.inc)} <small>${esc(r.incp)}</small>` : "–")],
      ["pm", "Polymarket", true, (r) => num(r.pm)], ["kalshi", "Kalshi", true, (r) => num(r.kalshi)],
      ["poll", "Polls", true, (r) => marginText(r.poll)],
      ["base", "Baseline (model only)", true, (r) => num(r.base)],
      ["h", "Harris 2024", true, (r) => marginText(r.h)], ["house", "House 2024", true, (r) => marginText(r.house)],
      ["sw", "Swing 20→24", true, (r) => marginText(r.sw)],
    ], rows.filter((r) => !f || r.id.toLowerCase().includes(f) || r.inc.toLowerCase().includes(f)), { key: "pm", dir: -1 });
  };
  $("hx-filter").addEventListener("input", table);
  $("hx-table").addEventListener("click", (ev) => { const b = ev.target.closest("[data-id]"); if (b) select(b.dataset.id, true); });
  table();
}
const $ = (id) => document.getElementById(id);
const pres = (d) => d.pres24?.margin ?? null;

// the map: districts on the chosen lines, coloured by the mode; swing arrows by county on top
async function drawMap() {
  const mode = MODES.find((m) => m[0] === X.mode);
  if (mode[2] === "2026") X.lines = "2026";
  document.querySelectorAll(".hx-modes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === X.mode)));
  document.querySelectorAll(".hx-lines button").forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.lines === X.lines)); b.disabled = mode[2] === "2026" && b.dataset.lines === "2024"; });
  if (!X.topo[X.lines]) {
    const [t, d] = await Promise.all([getJSON(`${ROOT}districts_${X.lines}.topo.json`), getJSON(`${ROOT}districts_${X.lines}.json`)]);
    X.topo[X.lines] = t; X.data[X.lines] = d;
  }
  const topo = X.topo[X.lines], data = X.data[X.lines].districts;
  const fc = topojson.feature(topo, topo.objects.districts);
  const box = $("hx-map");
  const w = Math.max(320, box.clientWidth || 900), h = Math.round(w * 0.6);
  const proj = d3.geoAlbersUsa().fitSize([w, h], fc), path = d3.geoPath(proj);
  const fill = (f) => { const d = data[f.properties.id]; return d ? mode[3]({ id: f.properties.id, ...d }, X) : null; };
  box.innerHTML = "";
  const svg = d3.select(box).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img").attr("aria-label", `US House districts, ${mode[1]}`);
  svg.append("defs").html(`<pattern id="hx-hatch" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#efede7"/><path d="M0 6L6 0M-1 1L1 -1M5 7L7 5" stroke="#c9c6bd" stroke-width="1"/></pattern>`);
  const g = svg.append("g");
  g.selectAll("path").data(fc.features).join("path").attr("d", path).attr("fill", (f) => fill(f) ?? "url(#hx-hatch)")
    .attr("stroke", "#fff").attr("stroke-width", 0.4).attr("data-id", (f) => f.properties.id)
    .on("pointermove", (ev, f) => card(ev, f.properties.id)).on("pointerleave", () => { $("hx-card").hidden = true; })
    .on("click", (ev, f) => select(f.properties.id));
  svg.append("path").datum(topojson.mesh(topo, topo.objects.districts, (a, b) => a !== b && a.properties.st !== b.properties.st)).attr("d", path).attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 1.4).attr("pointer-events", "none");
  if (X.sel) svg.append("path").datum(fc.features.find((f) => f.properties.id === X.sel)).attr("d", path).attr("fill", "none").attr("stroke", "#5f6368").attr("stroke-width", 1.4).attr("pointer-events", "none");
  if (X.mode === "swing") await arrows(svg, proj);
  const hatch = `<span class="lg-none"></span> no data`;
  $("hx-legend").innerHTML = X.mode === "swing" ? `${esc(mode[4])} <span class="hx-arr" style="color:${REP}">→ Trump</span> <span class="hx-arr" style="color:${DEM}">← Harris</span>`
    : `<span class="hx-ramp" style="background:linear-gradient(90deg,${REP},${NEUTRAL},${DEM})"></span> ${esc(mode[4])} ${hatch}`;
  if (X.mode === "ev") $("hx-legend").innerHTML = `<span class="hx-ramp" style="background:linear-gradient(90deg,${EV(0)},${EV(1)})"></span> ${esc(mode[4])} ${hatch}`;
}

// NYT-style arrows: one per county at its centroid, horizontal, length by |shift|, red to the
// right (towards Trump), blue to the left (towards Harris)
async function arrows(svg, proj) {
  X.swing ??= await getJSON(`${ROOT}swing.json`, { optional: true });
  X.counties ??= await getJSON("geo/us_counties.topo.json", { optional: true });
  if (!X.swing || !X.counties) return;
  const fc = topojson.feature(X.counties, X.counties.objects.counties);
  const max = 15; // points of shift for the longest arrow (most counties moved under 10)
  const L = 22;
  const g = svg.append("g").attr("pointer-events", "none");
  const items = [];
  for (const f of fc.features) {
    const id = f.properties?.unit_id ?? f.id, s = X.swing.counties[id];
    if (!s || s[0] == null || s[1] == null) continue;
    const c = proj(d3.geoCentroid(f));
    if (!c) continue;
    const shift = s[1] - s[0]; // + = towards Harris
    if (Math.abs(shift) < 0.25) continue;
    const len = Math.max(2, L * Math.min(1, Math.abs(shift) / max)), dir = shift > 0 ? -1 : 1;
    items.push(`<path d="M${c[0].toFixed(1)},${c[1].toFixed(1)}h${(dir * len).toFixed(1)}m${(-dir * 3).toFixed(1)},-2l${(dir * 3).toFixed(1)},2l${(-dir * 3).toFixed(1)},2" stroke="${shift > 0 ? DEM : REP}"/>`);
  }
  g.attr("fill", "none").attr("stroke-width", 1).attr("stroke-opacity", 0.75).html(items.join(""));
}

function card(ev, id) {
  const d = X.data[X.lines].districts[id], m = X.lines === "2026" ? X.markets?.districts?.[id] : null, e = X.lines === "2026" ? X.ev?.districts?.[id] : null;
  const el = $("hx-card");
  if (!d) { el.hidden = true; return; }
  const h = h24(d);
  const rows = [
    m?.pm && ["Polymarket", `${num(m.pm.p)}${m.pm.cands?.D ? ` <small>${esc(m.pm.cands.D)} (D) v ${esc(m.pm.cands.R ?? "–")} (R)</small>` : ""}`],
    m?.kalshi && ["Kalshi", num(m.kalshi.p)],
    pollM(d) != null ? ["Poll average", `${marginText(pollM(d))} <small>${m.polls.average.n_polls ?? ""} polls</small>`]
      : m?.polls?.polls?.length ? ["Poll average", "<small>no poll in the 45-day window</small>"] : null,
    d.baseline && ["Baseline (model only)", num(d.baseline.p_d)],
    ["Harris 2024", `${marginText(pres(d))}${d.pres24?.method && d.pres24.method !== "county" ? " <small>approx.</small>" : ""}`],
    h && [d.house24 ? "House 2024" : `House 2024 (core: ${esc(d.core_2024 ?? "")})`, `${esc(h.winner ?? "")} (${esc(h.winner_party ?? "")}) ${marginText(h.margin)}${h.uncontested ? " <small>uncontested</small>" : ""}`],
    d.swing_pres != null && ["Swing 2020→24 (president)", marginText(d.swing_pres)],
    e && ["Early vote", `${d3.format(",")(e.ev_total)} (${pct(e.share_of_2022_total, 0)} of 2022)`],
  ].filter(Boolean);
  el.innerHTML = `<p class="pc-name"><b>${esc(id)}</b>${d.redrawn ? " <small>new lines</small>" : ""}</p><table class="kv"><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="n">${v}</td></tr>`).join("")}</tbody></table>`;
  el.hidden = false;
  const b = $("hx-map").getBoundingClientRect();
  const x = ev.clientX - b.left, y = ev.clientY - b.top;
  el.style.left = `${Math.max(4, Math.min(b.width - el.offsetWidth - 4, x + 14))}px`;
  el.style.top = `${Math.max(4, Math.min(b.height - el.offsetHeight - 4, y + 14))}px`;
}

// a district's details under the map: markets, polls, early vote by party
function select(id, scroll = false) {
  X.sel = id;
  const d = X.districts.districts[id], m = X.markets?.districts?.[id], e = X.ev?.districts?.[id];
  if (!d) return;
  const polls = m?.polls?.polls ?? [];
  $("hx-sel").innerHTML = `<h3 class="tab-h">${esc(id)}${d.redrawn ? " <small>(new lines)</small>" : ""}</h3>
    <p>${m?.pm ? `Polymarket ${num(m.pm.p)} (spread ${num(m.pm.spread_d, 3)}, volume $${d3.format(",.0f")(m.pm.volume ?? 0)})` : "No Polymarket market"} · ${m?.kalshi ? `Kalshi ${num(m.kalshi.p)}` : "no Kalshi market"} · Baseline (model only) ${num(d.baseline?.p_d)} · Harris 2024 ${marginText(pres(d))} · swing 2020→24 ${marginText(d.swing_pres)}${e ? ` · early vote ${d3.format(",")(e.ev_total)}${e.by_party ? ` (D ${d3.format(",")(e.by_party.D)}, R ${d3.format(",")(e.by_party.R)})` : ""}` : ""}</p>
    ${polls.length ? `<div class="hub-scroll"><table class="races hub-table"><thead><tr><th>Pollster</th><th>Ended</th><th class="n">Sample</th><th class="n">D</th><th class="n">R</th><th class="n">Margin</th></tr></thead><tbody>${polls.map((p) => `<tr><td>${esc(p.pollster)}</td><td>${esc(day(p.end))}</td><td class="n">${p.n ?? "–"}</td><td class="n">${esc(p.d ?? "")} ${num(p.d_pct, 0)}</td><td class="n">${esc(p.r ?? "")} ${num(p.r_pct, 0)}</td><td class="n">${marginText(p.d2 != null ? (2 * p.d2 - 1) * 100 : null)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="note">No district polls.</p>`}`;
  drawMap();
  if (scroll) $("hx-map").scrollIntoView({ block: "nearest" });
}

// a redrawn state: seats Harris won and expected D seats on the old and new lines, and each new
// district against its core 2024 district
function drawFlow(st) {
  const F = X.flows?.states?.[st];
  document.querySelectorAll(".hx-states button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.st === st)));
  if (!F) { $("hx-flow").innerHTML = `<p class="note">No flow data for ${esc(st ?? "")}.</p>`; return; }
  const o = F.old_seats, n = F.new_seats;
  const rows = Object.entries(F.districts ?? {}).map(([id, x]) => {
    const top = (F.flows ?? []).filter((f) => f[1] === id).sort((a, b) => b[4] - a[4]).slice(0, 2).map((f) => `${f[0]} ${pct(f[4], 0)}`).join(", ");
    return `<tr><td>${esc(id)}</td><td class="n">${marginText(x.pres24_margin)}</td><td>${esc(x.core_2024 ?? "")}</td><td class="n">${marginText(x.core_margin)}</td><td class="n" style="color:${x.lean_change > 0 ? DEM : x.lean_change < 0 ? REP : "inherit"}">${marginText(x.lean_change)}</td><td><small>${esc(top)}</small></td></tr>`;
  });
  $("hx-flow").innerHTML = `<p>Seats Harris won: <b>${o.harris_won}</b> of ${o.districts} on the 2024 lines, <b>${n.harris_won}</b> of ${n.districts} on the new lines (expected Democratic seats at the 2024 vote: ${num(o.expected_d, 1)} → ${num(n.expected_d, 1)}). Democrats won ${F.house24_won_d} in 2024.</p>
    <div class="hub-scroll"><table class="races hub-table"><thead><tr><th>New district</th><th class="n">Harris 2024</th><th>Core 2024 district</th><th class="n">its Harris 2024</th><th class="n">Lean change</th><th>Made from (share of the new district)</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
