// The US midterms hub (polls, pollsters, markets, track record) as tabs of the election page
// (election.html?id=us2026), following the race or state chosen there. Data: data/us2026/hub/
// (built by analysis/; see their `source`/`sources` fields), overview.json and house/. Margins are
// Democratic minus Republican in points of the two-party vote; prices and P are the chance the
// Democratic side wins.
//   const H = await loadUsHub();
//   renderUsTab(H, "polls" | "pollsters" | "markets" | "track", el, { race, state, onRace });
// race: a Senate/governor key ("tx-sen"), a House district ("house:TX-07") or null; state: "tx"
// or null (national); onRace(key): the reader clicked a race in a table.
import * as d3 from "./vendor/d3.js";
import { C, getJSON, esc, num, pct, fmtDate, marginText, probit, plural } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", POLLS = "#8d8a82";
const SORT = {}; // each table's sort, kept across renders: { [table id]: { key, dir } }
const m2 = (share) => (share == null ? null : (2 * share - 1) * 100); // two-party share -> margin
const day = (d) => (d ? fmtDate(d).replace(/ \d{4}$/, "") : "–");
const int = (v) => (v == null ? "–" : d3.format(",")(v));
const sources = (x) => {
  const list = [x?.source, ...(x?.sources ?? [])].flat().filter(Boolean);
  return list.length ? `<p class="note hub-src">Sources: ${list.map((s) => (typeof s === "string" ? esc(s) : `${s.url ? `<a href="${esc(s.url)}">${esc(s.name)}</a>` : esc(s.name)}${s.licence ? `, ${esc(s.licence)}` : ""}`)).join("; ")}.</p>` : "";
};
// the element of this render marked data-slot="k"
const slot = (el, k) => el.querySelector(`[data-slot="${k}"]`);
// clicks on [data-race] inside box (a fresh element of this render) select that race
const raceClicks = (box, onRace) => box.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-race]");
  if (t && box.contains(t)) onRace(t.dataset.race);
});

export async function loadUsHub() {
  const opt = { optional: true };
  const [overview, polls, pollsters, markets, backtest, crosstabs, wallets, houseMarkets, houseDistricts] = await Promise.all(
    ["overview.json", "hub/polls.json", "hub/pollsters.json", "hub/markets.json", "hub/backtest.json", "hub/crosstabs.json", "hub/wallets.json", "house/markets.json", "house/districts.json"]
      .map((f) => getJSON(`us2026/${f}`, opt)));
  // race names from the overview ("Texas Senate") and its rows by race key
  const names = new Map(), ov = new Map(), stateNames = new Map();
  for (const [o, off] of Object.entries(overview?.offices ?? {})) for (const r of off.races ?? []) {
    names.set(r.race_key, `${r.state_name ?? r.state.toUpperCase()} ${o === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`);
    ov.set(r.race_key, r);
    if (r.state_name) stateNames.set(r.state, r.state_name);
  }
  return { overview, polls, pollsters, markets, backtest, crosstabs, wallets, houseMarkets, houseDistricts, names, ov, stateNames };
}

export function renderUsTab(H, tab, el, ctx = {}) {
  const c = { race: ctx.race ?? null, state: ctx.state ? ctx.state.toLowerCase() : null, onRace: ctx.onRace ?? (() => {}) };
  const house = c.race?.startsWith("house:") ? c.race.slice(6) : null;
  const race = house ? null : c.race;
  if (tab === "polls") house ? drawHousePolls(H, el, house) : race ? drawPolls(H, el, race) : drawAllPolls(H, el, c);
  else if (tab === "pollsters") drawPollsters(H, el, c, house, race);
  else if (tab === "markets") house ? drawHouseMarkets(H, el, house) : race ? drawMarkets(H, el, race) : drawAllMarkets(H, el, c);
  else if (tab === "track") drawTrack(H, el, c, house);
  else el.innerHTML = `<p class="note">Unknown tab.</p>`;
}

const nameOf = (H, k) => H.names.get(k) ?? k.toUpperCase();
const stateOf = (H, k) => H.ov.get(k)?.state ?? k.split("-")[0];
const stateName = (H, st) => H.stateNames.get(st) ?? st.toUpperCase();

/* ---------------------------------------------------------------- small charts */

// time chart: series [{ values: [[Date, v]], color, width, dash, label }], y domain from the data
function timeChart(el, series, { height = 240, fmt = (v) => v, zero = false, band = null, bars = null, dots = null, domain = null } = {}) {
  const width = Math.max(300, el.clientWidth || 640), m = { l: 46, r: 110, t: 10, b: 22 };
  const all = series.flatMap((s) => s.values.map((d) => d[0]));
  const ys = [...series.flatMap((s) => s.values.map((d) => d[1])), ...(dots ?? []).map((d) => d.v), ...(zero ? [0] : [])].filter((v) => v != null && Number.isFinite(v));
  if (!all.length || (!domain && ys.length <= (zero ? 1 : 0))) { el.innerHTML = `<p class="note">No history for this race.</p>`; return; }
  const x = d3.scaleUtc().domain(d3.extent(all)).range([m.l, width - m.r]);
  const [a, b] = d3.extent(ys);
  const y = d3.scaleLinear().domain(domain ?? [a - (b - a) * 0.08 - 0.5, b + (b - a) * 0.08 + 0.5]).nice().range([height - m.b - (bars ? 34 : 0), m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height).style("display", "block").style("max-width", "100%").style("height", "auto");
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(fmt).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(Math.floor((width - m.l - m.r) / 80)).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
  if (zero && y.domain()[0] < 0 && y.domain()[1] > 0) svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  if (band) svg.append("path").datum(band).attr("fill", C.model).attr("opacity", 0.12).attr("d", d3.area().x((d) => x(d[0])).y0((d) => y(d[1])).y1((d) => y(d[2])));
  if (bars?.length) {
    const yb = d3.scaleLinear().domain([0, d3.max(bars, (d) => d[1]) || 1]).range([height - m.b, height - m.b - 28]);
    svg.append("g").selectAll("rect").data(bars).join("rect").attr("x", (d) => x(d[0]) - 1).attr("width", 2).attr("y", (d) => yb(d[1])).attr("height", (d) => height - m.b - yb(d[1])).attr("fill", C.kalshi).attr("opacity", 0.5);
    svg.append("text").attr("class", "chart-note").attr("x", width - m.r + 5).attr("y", height - m.b - 10).attr("fill", C.kalshi).text("Kalshi volume");
  }
  for (const s of series) {
    const v = s.values.filter((d) => d[1] != null);
    if (v.length) svg.append("path").datum(v).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width ?? 1.6).attr("stroke-dasharray", s.dash ?? null)
      .attr("d", d3.line().x((d) => x(d[0])).y((d) => y(d[1])));
  }
  if (dots) svg.append("g").selectAll("circle").data(dots).join("circle").attr("cx", (d) => x(d.t)).attr("cy", (d) => y(d.v)).attr("r", (d) => d.r).attr("fill", (d) => d.color).attr("fill-opacity", 0.55)
    .append("title").text((d) => d.title);
  // labels at the line ends
  const ends = series.map((s) => { const v = s.values.filter((d) => d[1] != null).at(-1); return v ? { y: y(v[1]), text: s.label, color: s.color } : null; }).filter(Boolean).sort((p, r) => p.y - r.y);
  for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 12);
  svg.selectAll(null).data(ends).join("text").attr("class", "end-label").attr("x", width - m.r + 5).attr("y", (d) => d.y + 4).attr("fill", (d) => d.color).text((d) => d.text);
  el.replaceChildren(svg.node());
}

// reliability: predicted chance (across) against how often it happened (up), dots sized by count
function calibration(el, series) {
  const w = 300, h = 260, m = { l: 36, r: 10, t: 10, b: 32 };
  const x = d3.scaleLinear().domain([0, 1]).range([m.l, w - m.r]), y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
  const r = d3.scaleSqrt().domain([0, d3.max(series.flatMap((s) => s.bins), (d) => d.n) || 1]).range([2, 9]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).style("max-width", "100%").style("height", "auto");
  svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(5).tickSize(3));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(3));
  svg.append("text").attr("class", "legend-text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 2).attr("text-anchor", "middle").text("chance given");
  for (const s of series) {
    const pts = s.bins.filter((d) => d.n > 0 && d.mean_p != null);
    svg.append("path").datum(pts).attr("fill", "none").attr("stroke", s.color).attr("opacity", 0.6).attr("d", d3.line().x((d) => x(d.mean_p)).y((d) => y(d.freq)));
    svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => x(d.mean_p)).attr("cy", (d) => y(d.freq)).attr("r", (d) => r(d.n)).attr("fill", s.color).attr("fill-opacity", 0.8)
      .append("title").text((d) => `${s.label}: chance ${num(d.mean_p)}, happened ${pct(d.freq, 0)} (${d.n})`);
  }
  el.replaceChildren(svg.node());
}

// a sortable table: cols [[key, label, numeric, cell(row) -> html]]; rowRace(row): the race key a
// row selects (data-race on the row), for tables wired with raceClicks
function sortable(el, id, cols, rows, initial, rowRace = null) {
  SORT[id] ??= initial;
  const draw = () => {
    const { key, dir } = SORT[id];
    const sorted = [...rows].sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
    el.innerHTML = `<table class="races hub-table"><thead><tr>${cols.map(([k, l, n]) => `<th class="${n ? "n" : ""}" aria-sort="${key === k ? (dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${l}</button></th>`).join("")}</tr></thead>
      <tbody>${sorted.map((r) => `<tr${rowRace ? ` data-race="${esc(rowRace(r))}"` : ""}>${cols.map(([k, , n, cell]) => `<td class="${n ? "n" : ""}">${cell ? cell(r) : esc(r[k] ?? "–")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    el.querySelectorAll("th button").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.key;
      SORT[id] = { key: k, dir: SORT[id].key === k ? -SORT[id].dir : typeof rows[0]?.[k] === "string" ? 1 : -1 };
      draw();
    }));
  };
  draw();
}

/* ---------------------------------------------------------------- recalled vote (crosstabs.json) */

// Each poll's own crosstab: the 2024 vote its sample recalls against the certified state result
// (the gap), and its topline as published (raw) and reweighted to that result (adjusted). Display
// only; neither number feeds a model. Gaps and shifts: + = towards the Democratic side.
const STATUS = [
  ["recall_xtab", "recalled vote and the race by recalled vote", "#1b1b1b"],
  ["recall", "recalled vote only", "#5f6368"],
  ["composition", "party or demographics only", "#a5a29a"],
  ["no_crosstabs", "no crosstabs published", "#d9d6cf"],
  ["blocked", "release blocked to plain requests", "#ece9e2"],
  ["robots", "robots.txt disallows reading", "#f4f2ec"],
];
const signed = (v, d = 1) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`);
const gapBadge = (g) => (g == null ? "–" : `<span class="badge" style="color:${g > 0 ? DEM : g < 0 ? REP : "inherit"};border-color:currentColor" title="recalled 2024 margin minus the certified state result">${signed(g)}</span>`);
const xtCaveat = (X) => `<p class="note hub-caveat"><b>Display only.</b> ${esc(X.method?.adjustment ?? "")}. ${esc(X.method?.caveat ?? "")}</p>`;

/* ---------------------------------------------------------------- polls */

// one Senate or governor race: its polls with their weights, the average over time, and the
// recalled-vote crosstabs (the race's raw and adjusted average, each poll's gap, pollsters by gap)
function drawPolls(H, el, race) {
  const R = H.polls?.races?.[race];
  if (!R) { el.innerHTML = `<p class="note">No polls for ${esc(nameOf(H, race))}.</p>${sources(H.polls)}`; return; }
  const a = R.average ?? {};
  const head = a.n_polls
    ? `<p class="hub-lede"><b>${esc(nameOf(H, race))}</b>: ${esc(R.dem)} against ${esc(R.opp)}. Polling average <b>${marginText(m2(a.avg))}</b> (${pct(a.avg)} of the two-party vote for ${esc(R.dem)}, ± ${num(a.sd == null ? null : 200 * a.sd, 1)} pt), chance ${num(a.p_win)}; ${a.n_polls} ${plural("poll", a.n_polls)} from ${a.n_pollsters ?? "–"} ${plural("pollster", a.n_pollsters)}, the latest ending ${esc(day(a.last_date))}.</p>`
    : `<p class="hub-lede"><b>${esc(nameOf(H, race))}</b>: no recent polls.</p>`;
  const X = H.crosstabs, xr = X?.races?.find((r) => r.race === race);
  const xp = new Map((X?.polls ?? []).filter((p) => p.race === race).map((p) => [p.id, p]));
  const rank = (X?.pollsters ?? []).filter((p) => p.mean_gap != null).sort((p, q) => q.mean_gap - p.mean_gap);
  const xLine = xr ? `<p class="note">Recalled 2024 vote: ${xr.n_recall} of ${xr.n_polls} polls print it${xr.mean_gap != null ? `; their samples recalled a Harris margin ${signed(xr.mean_gap)} pt from the certified result` : ""}. Average as published <b>${marginText(xr.raw)}</b>, with those polls reweighted to the certified 2024 result <b>${marginText(xr.adj)}</b> (display only; method and caveats below).</p>` : "";
  el.innerHTML = `${head}${xLine}<div class="hub-chart" data-slot="chart"></div><div class="hub-scroll" data-slot="table"></div>
    <p class="note">Average: ${esc(H.polls.rule ?? "")}. Over time: ${esc(H.polls.history_note ?? "")}. Dots: each poll's two-party margin, sized by its weight in today's average (grey: not in it). Bars in the table: the weight.${X ? " Recall gap: the sample's recalled Harris–Trump margin minus the state's certified 2024 margin (+: more Harris); adjusted: reweighted to that result (\"cells\" = the poll prints the race by recalled vote; \"approx\" = recalled shares only, the spread between the groups taken from the \"cells\" polls)." : ""}</p>
    ${rank.length ? `<p class="note"><b>Pollsters by recall gap</b> (mean over their polls that print it): ${rank.map((p) => `${esc(p.pollster)} ${gapBadge(p.mean_gap)}<small> ${p.n_recall}</small>`).join(" · ")}</p>` : ""}${X ? xtCaveat(X) : ""}${sources(H.polls)}`;
  const polls = R.polls ?? [];
  const hist = (R.history ?? []).map(([d, v]) => [new Date(d), m2(v)]);
  const maxW = d3.max(polls, (p) => p.weight ?? 0) || 1;
  const dots = polls.filter((p) => p.dem2 != null).map((p) => ({ t: new Date(p.end), v: m2(p.dem2), r: 2 + 6 * Math.sqrt((p.weight ?? 0) / maxW), color: p.weight ? C.model : POLLS, title: `${p.pollster}, ${day(p.end)}: ${marginText(m2(p.dem2))}` }));
  timeChart(slot(el, "chart"), [{ values: hist, color: C.model, width: 2, label: "average" }], { fmt: (v) => marginText(v).replace(".0", ""), zero: true, dots });
  sortable(slot(el, "table"), "polls", [
    ["pollster", "Pollster", false, (p) => `${p.url ? `<a href="${esc(p.url)}">${esc(p.pollster)}</a>` : esc(p.pollster)}${p.partisan ? ` <small>(${esc(p.partisan)})</small>` : ""}`],
    ["end", "Field dates", false, (p) => `${esc(day(p.start))}–${esc(day(p.end))}`],
    ["n", "Sample", true, (p) => `${p.n ?? "–"} <small>${esc(p.population ?? "")}</small>`],
    ["dem", esc(R.dem), true, (p) => num(p.dem, 0)],
    ["opp", esc(R.opp), true, (p) => num(p.opp, 0)],
    ["margin", "Margin", true, (p) => marginText(p.margin)],
    ["w", "Weight in the average", true, (p) => (p.weight ? `<span class="wbar"><i style="width:${(100 * p.weight / maxW).toFixed(0)}%"></i></span> ${pct(p.weight, 0)}` : `<small>${esc(p.excluded ?? "not in the average")}</small>`)],
    ...(X ? [["gap", "Recall gap", true, (p) => gapBadge(p.gap)], ["adj", "Adjusted", true, (p) => (p.adj == null ? "–" : `${marginText(p.adj)} <small>${esc(p.adj_method ?? "")}</small>`)]] : []),
  ], polls.map((p) => ({ ...p, margin: m2(p.dem2), w: p.weight ?? -1, gap: xp.get(p.id)?.gap ?? null, adj: xp.get(p.id)?.adj ?? null, adj_method: xp.get(p.id)?.adj_method ?? null })), { key: "end", dir: -1 });
}

// a House district's polls (house/markets.json): the list and the 45-day average
function drawHousePolls(H, el, id) {
  const HM = H.houseMarkets, m = HM?.districts?.[id], d = H.houseDistricts?.districts?.[id];
  if (!m && !d) { el.innerHTML = `<p class="note">No House district ${esc(id)}.</p>`; return; }
  // newest first; dates with the year (district polls go back to 2025)
  const polls = [...(m?.polls?.polls ?? [])].sort((p, q) => (q.end ?? "").localeCompare(p.end ?? "")), a = m?.polls?.average;
  const cands = polls[0] ?? {};
  const h = d?.house24 ?? d?.house24_core;
  const past = d ? ` Harris 2024 ${marginText(d.pres24?.margin)}${h ? `; House 2024${d.house24 ? "" : ` (core: ${esc(d.core_2024 ?? "")})`} ${esc(h.winner ?? "")} (${esc(h.winner_party ?? "")}) ${marginText(h.margin)}` : ""}.` : "";
  const lede = a?.avg != null
    ? `<b>${esc(id)}</b>: district poll average <b>${marginText(m2(a.avg))}</b> (${pct(a.avg)} of the two-party vote for the Democrat${cands.d ? `, ${esc(cands.d)}` : ""}, ± ${num(a.sd == null ? null : 200 * a.sd, 1)} pt), chance ${num(a.p_win)}; ${a.n_polls} ${plural("poll", a.n_polls)} from ${a.n_pollsters ?? "–"} ${plural("pollster", a.n_pollsters)}, the latest ending ${esc(day(a.last_date))}.`
    : `<b>${esc(id)}</b>: ${polls.length ? "no poll in the 45-day window" : "no district polls"}.`;
  el.innerHTML = `<p class="hub-lede">${lede}${past}</p>
    ${polls.length ? `<div class="hub-scroll"><table class="races hub-table"><thead><tr><th>Pollster</th><th>Ended</th><th class="n">Sample</th><th class="n">D</th><th class="n">R</th><th class="n">Margin</th></tr></thead><tbody>${polls.map((p) => `<tr><td>${esc(p.pollster)}</td><td>${p.end ? esc(fmtDate(p.end)) : "–"}</td><td class="n">${p.n ?? "–"}</td><td class="n">${esc(p.d ?? "")} ${num(p.d_pct, 0)}</td><td class="n">${esc(p.r ?? "")} ${num(p.r_pct, 0)}</td><td class="n">${marginText(m2(p.d2))}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${sources(HM)}`;
}

// every Senate and governor race (or the chosen state's): polling average, raw and adjusted by
// recalled vote; then what the polls' crosstabs show across the country
function drawAllPolls(H, el, c) {
  const P = H.polls, X = H.crosstabs;
  const xr = new Map((X?.races ?? []).map((r) => [r.race, r]));
  const keys = [...new Set([...Object.keys(P?.races ?? {}), ...xr.keys()])].filter((k) => !c.state || stateOf(H, k) === c.state);
  const rows = keys.map((k) => {
    const a = P?.races?.[k]?.average, x = xr.get(k);
    return { race: k, name: nameOf(H, k), avg: a?.n_polls ? m2(a.avg) : null, n_polls: a?.n_polls ?? 0, raw: x?.raw ?? null, adj: x?.adj ?? null,
      shift: x?.adj != null && x?.raw != null ? x.adj - x.raw : null, mean_gap: x?.mean_gap ?? null, cover: x?.n_polls ? x.n_recall / x.n_polls : null, x };
  });
  const where = c.state ? stateName(H, c.state) : null;
  const lede = where
    ? (rows.length ? `<b>${esc(where)}</b>: ${rows.length === 1 ? "the Senate or governor race" : `the ${rows.length} Senate and governor races`} with polls. Click a race for its polls.` : `<b>${esc(where)}</b>: no Senate or governor race with polls.`)
    : `Every Senate and governor race with polls: the polling average, and the average with each recalled-vote poll as published (raw) and reweighted to the certified 2024 result (adjusted). Click a race for its polls.`;
  el.innerHTML = `<p class="hub-lede">${lede}</p>${rows.length ? `<div class="hub-scroll" data-slot="races"></div>
    <p class="note">Average: ${esc(P?.rule ?? "")}. Raw and adjusted: polls without a recalled vote count as published. Shift and recall gap: + = towards the Democratic side.</p>` : ""}
    <div data-slot="xt"></div>${sources(P)}`;
  if (rows.length) {
    const box = slot(el, "races");
    sortable(box, "all-polls", [
      ["name", "Race", false, (r) => `<button type="button" class="linkish" data-race="${esc(r.race)}">${esc(r.name)}</button>`],
      ["avg", "Polling average", true, (r) => marginText(r.avg)],
      ["n_polls", "Polls", true],
      ["cover", "With recalled vote", true, (r) => (r.x ? `${r.x.n_recall} / ${r.x.n_polls}` : "–")],
      ["raw", "Raw", true, (r) => marginText(r.raw)],
      ["adj", "Adjusted", true, (r) => marginText(r.adj)],
      ["shift", "Shift", true, (r) => signed(r.shift)],
      ["mean_gap", "Recall gap", true, (r) => gapBadge(r.mean_gap)],
    ], rows, { key: "n_polls", dir: -1 }, (r) => r.race);
    raceClicks(box, c.onRace);
  }
  drawCrosstabs(H, slot(el, "xt"));
}

// the recalled-vote overview (crosstabs.json): coverage, the national gap, pollsters, 2016–2022
function drawCrosstabs(H, el) {
  const X = H.crosstabs;
  if (!X) { el.innerHTML = `<p class="note">No crosstab file yet.</p>`; return; }
  const cov = X.coverage ?? {}, sm = X.summary ?? {};
  const total = d3.sum(STATUS, ([k]) => cov.status?.[k] ?? 0) || 1;
  el.innerHTML = `<h3 class="tab-h">Recalled vote: do this year's samples lean Democratic?</h3>
    <p class="hub-lede">${cov.with_recall ?? 0} of ${cov.polls ?? 0} polls print the 2024 vote their respondents recall. On average those samples recalled a Harris margin <b>${signed(sm.mean_gap)} pt</b> from the certified state result (median ${signed(sm.median_gap)}; more Harris than the state voted in ${sm.polls_gap_d ?? 0} polls, more Trump in ${sm.polls_gap_r ?? 0}). Reweighting them to the certified result moves their margins by ${signed(sm.mean_shift)} pt on average.</p>
    ${xtCaveat(X)}
    <h3 class="tab-h">What the ${cov.polls ?? 0} polls in the averages publish</h3>
    <div class="cov-bar" role="img" aria-label="Crosstab coverage">${STATUS.map(([k, , col]) => (cov.status?.[k] ? `<i style="width:${(100 * cov.status[k] / total).toFixed(2)}%;background:${col}" title="${esc(k)}"></i>` : "")).join("")}</div>
    <p class="cov-key">${STATUS.map(([k, label, col]) => `<span><i style="background:${col}"></i>${esc(label)} <b>${cov.status?.[k] ?? 0}</b></span>`).join("")}</p>
    <p class="note">Recalled-vote polls carry ${pct(cov.recall_weight_share, 0)} of the averages' weight.${sm.gap_vs_538_house?.pollsters ? ` Across the ${sm.gap_vs_538_house.pollsters} pollsters with a FiveThirtyEight house effect, gap and house effect correlate at ${num(sm.gap_vs_538_house.corr, 2)}.` : ""}</p>
    <h3 class="tab-h">Pollsters</h3><div class="hub-scroll" data-slot="xt-pollsters"></div>
    <h3 class="tab-h">Did leaning samples miss more? (2016–2022)</h3><div data-slot="xt-history"></div>
    ${sources(X)}`;
  sortable(slot(el, "xt-pollsters"), "xt-pollsters", [
    ["pollster", "Pollster", false],
    ["polls", "Polls", true],
    ["n_recall", "With recalled vote", true],
    ["mean_gap", "Recall gap", true, (p) => gapBadge(p.mean_gap)],
    ["mean_shift", "Shift", true, (p) => signed(p.mean_shift)],
    ["house_538", "538 house effect", true, (p) => marginText(p.house_538)],
  ], X.pollsters ?? [], { key: "polls", dir: -1 });
  drawCrosstabHistory(slot(el, "xt-history"), X.history);
}

// one table per way of measuring a pollster's lean: relative to its races' other polls (the house
// effect, the main result) and absolute (its own mean error)
function drawCrosstabHistory(el, XH) {
  if (!XH?.cycles?.length) { el.innerHTML = ""; return; }
  const table = (basis) => {
    const of = (c) => c[basis] ?? {};
    const lean = (c, k) => { const x = of(c).by_prior_lean?.[k]; return x ? `${signed(x.mean_err)} <small>(${x.polls ?? "–"})</small>` : "–"; };
    return `<div class="hub-scroll"><table class="races hub-table"><thead><tr><th>Cycle</th><th class="n">Polls</th><th class="n">Mean error</th><th class="n">Rated polls</th><th class="n">D-lean pollsters</th><th class="n">Neutral</th><th class="n">R-lean</th><th class="n">RMSE raw → lean removed</th><th class="n">Error removed</th></tr></thead>
      <tbody>${XH.cycles.map((c) => `<tr><td>${c.cycle}</td><td class="n">${c.polls ?? "–"}</td><td class="n">${signed(c.cycle_mean_err)}</td><td class="n">${of(c).rated_polls ?? "–"} <small>(${of(c).rated_pollsters ?? "–"} pollsters)</small></td><td class="n">${lean(c, "d_lean")}</td><td class="n">${lean(c, "neutral")}</td><td class="n">${lean(c, "r_lean")}</td><td class="n">${num(of(c).rmse_raw, 2)} → ${num(of(c).rmse_corrected, 2)}</td><td class="n">${of(c).mse_removed_pct == null ? "–" : `${of(c).mse_removed_pct.toFixed(1)}%`}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">On average over the cycles, removing the lean measured this way changed the squared error by ${num(XH.overall?.[basis]?.mse_removed_pct_mean, 1)}% (+: removed).</p>`;
  };
  const D = XH.decomposition;
  el.innerHTML = `<p class="note">${esc(XH.question ?? "")} Lean: ${esc(XH.lean_rule ?? "")}. Errors: poll margin minus result (+: the poll overstated the Democratic side); lean columns: the next cycle's mean error of the pollsters in each group (polls). Data: ${esc(XH.data ?? "")}.</p>
    <h4 class="xt-sub">Lean relative to the race (house effect): the main result</h4>${table("relative")}
    <h4 class="xt-sub">Lean measured absolutely (the pollster's own mean error)</h4>${table("absolute")}
    ${D ? `<p class="note">Of the squared error of ${D.polls ?? "–"} polls (RMSE ${num(D.rmse, 1)} pt): ${num(D.cycle_mean_pct, 0)}% is the whole cycle's miss, ${num(D.race_within_cycle_pct, 0)}% the race's, ${num(D.poll_within_race_pct, 0)}% the single poll's.</p>` : ""}`;
}

/* ---------------------------------------------------------------- pollsters */

function drawPollsters(H, el, c, house, race) {
  const P = H.pollsters;
  if (!P) { el.innerHTML = `<p class="note">No pollster file yet.</p>`; return; }
  let names = null, where = "";
  if (house) {
    names = new Set((H.houseMarkets?.districts?.[house]?.polls?.polls ?? []).map((p) => p.pollster));
    where = `${house} district polls`;
  } else if (race) {
    names = new Set((H.polls?.races?.[race]?.polls ?? []).map((p) => p.pollster));
    where = nameOf(H, race);
  } else if (c.state) {
    names = new Set(Object.entries(H.polls?.races ?? {}).filter(([k]) => stateOf(H, k) === c.state).flatMap(([, r]) => (r.polls ?? []).map((p) => p.pollster)));
    where = stateName(H, c.state);
  }
  const rows = (P.pollsters ?? []).filter((p) => !names || names.has(p.pollster)).map((p) => ({ ...p, n_cycles: p.cycles?.length ?? 0, in: p.in_2026 ? 1 : 0 }));
  el.innerHTML = `<p class="hub-lede">${where ? `<b>${esc(where)}</b>: ${rows.length ? `${rows.length} pollster${rows.length === 1 ? "" : "s"} in the selected scope. ` : "No pollsters in the selected scope. "}` : ""}How far each pollster's final-three-week polls landed from the result: ${esc(P.window ?? "")}. Lean: the average error (${esc(P.units ?? "")}); RMSE: the typical size of an error.</p>
    <label class="select"><span>Pollster</span> <input type="search" placeholder="Filter" aria-label="Filter pollsters" data-slot="filter"></label>
    <div class="hub-scroll" data-slot="table"></div>
    <p class="note"><span class="badge">2026</span> ${esc(P.in_2026_rule ?? "")}.</p>${sources(P)}`;
  const filter = slot(el, "filter"), box = slot(el, "table");
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    sortable(box, "pollsters", [
      ["pollster", "Pollster", false, (p) => `${esc(p.pollster)}${p.in_2026 ? ` <span class="badge" title="in the 2026 averages">2026</span>` : ""}`],
      ["polls", "Polls", true], ["races", "Races", true],
      ["n_cycles", "Cycles", true, (p) => `<span title="${esc((p.cycles ?? []).join(", "))}">${p.n_cycles}</span>`],
      ["lean", "Lean", true, (p) => (p.lean == null ? "–" : `${marginText(p.lean)}`)],
      ["rmse", "RMSE (pt)", true, (p) => num(p.rmse, 1)],
    ], rows.filter((p) => !f || p.pollster.toLowerCase().includes(f)), { key: "polls", dir: -1 });
  };
  filter.addEventListener("input", draw);
  draw();
}

/* ---------------------------------------------------------------- markets */

const fig = (label, v, title = "") => `<span class="chip"${title ? ` title="${esc(title)}"` : ""}><small>${label}</small><b>${v}</b></span>`;
const usd = (v) => (v == null ? "–" : v === 0 ? "$0" : `${v < 0 ? "−" : ""}$${d3.format(".3s")(Math.abs(v)).replace("G", "B")}`);
// a venue's current price: the bid/ask midpoint, else the last daily price
const price = (v) => (v?.bid != null && v?.ask != null ? (v.bid + v.ask) / 2 : v?.daily?.at(-1)?.[1] ?? null);
// the 2024 calibration of market prices (hub markets.json accuracy)
const accuracyHtml = (MK) => `<h3 class="tab-h">Were past market prices right? (2024)</h3>
  <div class="hub-pair"><div data-slot="cal"></div><p class="note">${MK?.accuracy ? `${MK.accuracy.n} Senate and governor markets, the price a week before the election against who won: Brier score ${num(MK.accuracy.brier, 3)} (0 is perfect; always saying 0.5 scores 0.25). ${esc(MK.accuracy.coverage ?? "")}` : "Not available."}</p></div>`;
const drawAccuracy = (MK, el) => { if (MK?.accuracy?.bins) calibration(slot(el, "cal"), [{ bins: MK.accuracy.bins, color: C.market, label: "market" }]); };

function drawMarkets(H, el, race) {
  const MK = H.markets, M = MK?.races?.[race], mg = H.ov.get(race)?.margins ?? {};
  const pm = M?.pm, k = M?.kalshi;
  const sd = mg.market_sd_used ?? null;
  const modelM = mg.model_polls ?? mg.model_v2 ?? mg.model ?? mg.estimate ?? null;
  el.innerHTML = `<p class="hub-lede"><b>${esc(nameOf(H, race))}</b>: the chance the Democratic side wins on each venue, and the same prices read as margins next to the polls and the model.</p>
    <div class="chips">${pm ? fig("Polymarket bid / ask", `${num(pm.bid)} / ${num(pm.ask)}`) + fig("Polymarket volume (total)", usd(pm.volume_total)) : ""}${k ? fig("Kalshi bid / ask", `${num(k.bid)} / ${num(k.ask)}`) : ""}${fig("Our model margin", marginText(modelM), mg.estimate != null && mg.model_polls == null && mg.model_v2 == null && mg.model == null ? "Estimate: 2024 county results shifted to the poll average" : "v3: poll average prior + county count (registered)")}${fig("Polls", marginText(mg.polls))}</div>
    <h3 class="tab-h">Price (chance the Democratic side wins)</h3><div class="hub-chart" data-slot="price"></div>
    <h3 class="tab-h">As margins</h3><div class="hub-chart" data-slot="margin"></div>
    <p class="note">Market-implied margin: the margin at which the model's uncertainty (sd ${sd != null ? `${sd.toFixed(1)} pt` : "–"}) gives the price, Φ⁻¹(price) × sd. ${esc(MK?.note ?? "")}</p>
    <div data-slot="wallets"></div>
    ${accuracyHtml(MK)}${sources(MK)}`;
  const pmD = (pm?.daily ?? []).map(([d, p]) => [new Date(d), p]);
  const kD = (k?.daily ?? []).map(([d, p]) => [new Date(d), p]);
  timeChart(slot(el, "price"), [{ values: pmD, color: C.market, label: "Polymarket" }, { values: kD, color: C.kalshi, label: "Kalshi" }],
    { height: 220, fmt: (v) => num(v, 1), domain: [0, 1], bars: (k?.daily ?? []).filter((d) => d[2] != null).map(([d, , v]) => [new Date(d), v]) });
  const toM = (rows) => rows.map(([d, p]) => [d, sd != null && p > 0 && p < 1 ? probit(p) * sd : null]);
  const pollsHist = (H.polls?.races?.[race]?.history ?? []).map(([d, v]) => [new Date(d), m2(v)]);
  const span = d3.extent([...pmD, ...kD, ...pollsHist].map((d) => d[0]));
  timeChart(slot(el, "margin"), [
    { values: toM(pmD), color: C.market, label: "Polymarket-implied" },
    { values: toM(kD), color: C.kalshi, width: 1.2, label: "Kalshi-implied" },
    { values: pollsHist, color: POLLS, dash: "4,3", label: "polls" },
    ...(modelM != null && span[0] ? [{ values: [[span[0], modelM], [span[1], modelM]], color: C.model, width: 2, label: "our model now" }] : []),
  ], { height: 220, fmt: (v) => marginText(v).replace(".0", ""), zero: true });
  drawAccuracy(MK, el);
  drawWallets(H, slot(el, "wallets"), race);
}

// Polymarket's pooled lead/lag across races and the 2024 test of seasoned-wallet flow
const walletPooled = (W) => {
  const P = W.pooled ?? {}, L = W.historical_2024?.logit_all ?? {};
  return `Across ${P.large_1h?.n_races ?? "–"} races the median lead is ${num(P.large_1h?.lead_median, 3)}; seasoned wallets ${num(P.seasoned_1h?.lead_median, 3)}. In 2024, seasoned-wallet flow did not predict the winner beyond the price (logit β ${num(L.beta, 1)} ± ${num(L.se, 1)}, p ${num(L.p_value, 2)}, n ${L.n ?? "–"}).`;
};
const walletPrivacy = (W) => `<p class="note">${esc(W.privacy ?? "")} ${esc(W.definitions?.flow ?? "")}. Seasoned: ${esc(W.definitions?.seasoned ?? "")}. Polymarket only: Kalshi publishes no trader-level data.</p>${sources(W)}`;

// Who trades the race on Polymarket (wallets.json): aggregate, pseudonymous figures only. Taker
// flow by order size and by "seasoned" wallets (profitable on earlier political markets), and
// whether that flow led the price; the 2024 test of the same idea.
function drawWallets(H, el, race) {
  const W = H.wallets, R = W?.races?.[race];
  if (!W || !R) { el.innerHTML = W ? `<p class="note">No wallet figures for this race.</p>` : ""; return; }
  const a = R.age_30d ?? {}, im = R.impact?.["1h"] ?? {};
  const flowRow = (k, label) => { const f = R.flow?.[k]; return f ? `<tr><td>${label}</td><td class="n">${usd(f.net)}</td><td class="n">${usd(f.gross)}</td><td class="n">${int(f.orders)}</td></tr>` : ""; };
  el.innerHTML = `<h3 class="tab-h">Who trades it on Polymarket</h3>
    <div class="chips">${fig("Volume (all time)", usd(R.volume_total))}${fig("Open interest", usd(R.open_interest))}${fig("Wallets (last 30 days)", `${int(R.wallets_all)} (${int(R.wallets_30d)})`)}${fig("New in 30 days", pct(a.share_lt30d, 0), W.definitions?.age_30d)}${fig("Top-10 holders: YES / NO", `${pct(R.holders_yes?.top10_share, 0)} / ${pct(R.holders_no?.top10_share, 0)}`, W.definitions?.holders)}</div>
    <div class="hub-pair"><div><p class="note">Net YES flow, cumulative (last 180 days; + = buying the Democratic side): taker orders by size; seasoned wallets from all their fills (maker and taker)</p><div class="hub-chart" data-slot="flow"></div></div><div class="hub-scroll"><table class="races hub-table"><thead><tr><th>Taker orders</th><th class="n">Net YES</th><th class="n" title="sum of |YES-equivalent $|, not $ paid">Gross (YES-equiv.)</th><th class="n">Orders</th></tr></thead><tbody>${flowRow("small", "Small (&lt; $100)")}${flowRow("medium", "Medium ($100–1k)")}${flowRow("large", "Large (≥ $1k)")}${R.seasoned ? `<tr><td>Seasoned wallets (${int(R.seasoned.wallets)}), all fills</td><td class="n">${usd(R.seasoned.net)}</td><td class="n">${pct(R.seasoned.gross_share, 0)} of gross</td><td class="n">30 d: ${usd(R.seasoned.net_30d)}</td></tr>` : ""}</tbody></table>
      <p class="note">Did large orders move the price first? Hourly: led ${num(im.large?.lead, 3)}, followed ${num(im.large?.follow, 3)} (correlation of flow with the next / previous price change). ${walletPooled(W)}</p></div></div>
    ${walletPrivacy(W)}`;
  // cumulative net taker flow (YES-equivalent $) by bucket over the last 180 days
  const ser = W.series?.[race] ?? [];
  const cum = (j) => { let s = 0; return ser.map((r) => [new Date(r[0]), (s += r[j] ?? 0)]); };
  timeChart(slot(el, "flow"), [
    { values: cum(4), color: "#1b1b1b", width: 1.8, label: "large" },
    { values: cum(3), color: "#5f6368", label: "medium" },
    { values: cum(2), color: "#a5a29a", label: "small" },
    { values: cum(5), color: "#1b1b1b", dash: "4,3", label: "seasoned (all fills)" },
  ], { height: 200, fmt: (v) => usd(v), zero: true });
}

// a House district's markets (house/markets.json): current prices only, no history
function drawHouseMarkets(H, el, id) {
  const HM = H.houseMarkets, m = HM?.districts?.[id], d = H.houseDistricts?.districts?.[id];
  if (!m && !d) { el.innerHTML = `<p class="note">No House district ${esc(id)}.</p>`; return; }
  const pm = m?.pm, k = m?.kalshi, base = m?.baseline ?? d?.baseline;
  const cands = pm?.cands?.D || pm?.cands?.R ? pm.cands : k?.cands ?? {};
  const who = cands.D && cands.R ? `: ${esc(cands.D)} (D) against ${esc(cands.R)} (R)` : cands.D ? `: ${esc(cands.D)} (D)` : cands.R ? `: ${esc(cands.R)} (R)` : "";
  el.innerHTML = `<p class="hub-lede"><b>${esc(id)}</b>${who}. The chance the Democratic party wins the seat on each venue${pm || k ? "" : ": no market lists it"}.</p>
    <div class="chips">${pm ? fig("Polymarket", num(pm.p)) + fig("Polymarket spread", num(pm.spread_d, 3)) + fig("Polymarket volume", usd(pm.volume)) : fig("Polymarket", "no market")}${k ? fig("Kalshi", num(k.p)) : fig("Kalshi", "no market")}${base ? fig("Our model", num(base.p_d), `2024 president on the 2026 lines + generic-ballot swing${HM?.generic_ballot?.n_polls != null ? ` (${HM.generic_ballot.n_polls} polls)` : ""}; no district count model`) : ""}</div>
    <p class="note">No price history is published for House markets.</p>${sources(HM)}`;
}

// every Senate and governor race's prices (or the chosen state's), and the 2024 calibration
function drawAllMarkets(H, el, c) {
  const MK = H.markets, W = H.wallets;
  const rows = Object.entries(MK?.races ?? {}).filter(([k]) => !c.state || stateOf(H, k) === c.state).map(([k, M]) => {
    const p = price(M.pm), q = price(M.kalshi);
    return { race: k, name: nameOf(H, k), pm: p, kalshi: q, diff: p != null && q != null ? p - q : null, vol: M.pm?.volume_total ?? null };
  });
  const where = c.state ? stateName(H, c.state) : null;
  const lede = where
    ? (rows.length ? `<b>${esc(where)}</b>: the chance the Democratic side wins on each venue. Click a race for its price history.` : `<b>${esc(where)}</b>: no Senate or governor market.`)
    : `Every Senate and governor market: the chance the Democratic side wins on Polymarket and Kalshi. Click a race for its price history, the implied margins and who trades it.`;
  el.innerHTML = `<p class="hub-lede">${lede}</p>${rows.length ? `<div class="hub-scroll" data-slot="races"></div>
    <p class="note">Price: the midpoint of the best bid and ask, else the last daily price. Gap: Polymarket minus Kalshi. ${esc(MK?.note ?? "")}</p>` : ""}
    ${W ? `<h3 class="tab-h">Who trades on Polymarket</h3><p class="note">Do large orders move the price first? ${walletPooled(W)}</p>${walletPrivacy(W)}` : ""}
    ${accuracyHtml(MK)}${sources(MK)}`;
  if (rows.length) {
    const box = slot(el, "races");
    sortable(box, "all-markets", [
      ["name", "Race", false, (r) => `<button type="button" class="linkish" data-race="${esc(r.race)}">${esc(r.name)}</button>`],
      ["pm", "Polymarket", true, (r) => num(r.pm)],
      ["kalshi", "Kalshi", true, (r) => num(r.kalshi)],
      ["diff", "Gap", true, (r) => signed(r.diff, 2)],
      ["vol", "Polymarket volume", true, (r) => usd(r.vol)],
    ], rows, { key: "vol", dir: -1 }, (r) => r.race);
    raceClicks(box, c.onRace);
  }
  drawAccuracy(MK, el);
}

/* ---------------------------------------------------------------- track record */

function drawTrack(H, el, c, house) {
  const B = H.backtest;
  if (!B) { el.innerHTML = `<p class="note">No backtest file yet.</p>`; return; }
  if (house) { el.innerHTML = `<p class="note">There is no district-level House track record in this dataset. The district's current polls and markets are scoped in their tabs; the published backtest covers Senate and governor races.</p>`; return; }
  const all = B.races ?? [], rows = c.state ? all.filter((r) => String(r.state).toLowerCase() === c.state) : all;
  const sm = B.summary ?? {};
  const where = c.state ? `<b>${esc(stateName(H, c.state))}</b>: ${rows.length} past Senate or governor race${rows.length === 1 ? "" : "s"} in the published backtest. ` : "";
  const line = (label, k, f) => `<tr><td>${label}</td><td class="n m">${f(sm.model?.[k])}</td><td class="n">${f(sm.baseline?.[k])}</td></tr>`;
  el.innerHTML = `<p class="hub-lede">${where}Our poll model on past Senate and governor races, against past results only. ${esc(B.label ?? "")}</p>
    ${c.state ? "" : `<div class="hub-pair"><table class="races hub-table"><thead><tr><th></th><th class="n">Our model</th><th class="n">Past results</th></tr></thead><tbody>
      ${line("Races", "races", (v) => v ?? "–")}${line("Result inside the 68% band", "cover68", (v) => pct(v, 0))}${line("Result inside the 90% band", "cover90", (v) => pct(v, 0))}
      ${line("Mean absolute error (pt)", "mae_margin", (v) => num(v, 1))}${line("Brier score (lower is better)", "brier", (v) => num(v, 3))}${line("Log loss (lower is better)", "log_loss", (v) => num(v, 3))}
    </tbody></table><div><div data-slot="cal"></div><p class="note">Chance given against how often the Democrat won: <span class="m">our model</span>, <span style="color:${POLLS}">past results</span>. On the dashed line the chances were right on average.</p></div></div>`}
    <h3 class="tab-h">${c.state ? `${esc(stateName(H, c.state))} races` : "Every race"}</h3><div class="hub-scroll" data-slot="table"></div>
    <p class="note">Past results: ${esc(typeof B.baseline === "string" ? B.baseline : JSON.stringify(B.baseline))}.</p>${sources(B)}`;
  if (!c.state) calibration(slot(el, "cal"), [{ bins: sm.baseline?.calibration ?? [], color: POLLS, label: "past results" }, { bins: sm.model?.calibration ?? [], color: C.model, label: "our model" }]);
  sortable(slot(el, "table"), "track", [
    ["cycle", "Year", true], ["state", "State", false], ["office", "Office", false, (r) => (r.office === "gov" ? "governor" : "Senate")], ["n_polls", "Polls", true],
    ["pred_margin", "Our model", true, (r) => `${marginText(r.pred_margin)} <small>± ${num(r.sd_margin, 1)}</small>`],
    ["actual_margin", "Result", true, (r) => marginText(r.actual_margin)],
    ["error", "Error (pt)", true, (r) => num(r.error, 1)],
    ["base_margin", "Past results", true, (r) => marginText(r.base_margin)],
    ["P", "Chance (model / past)", true, (r) => `${num(r.P)} / ${num(r.base_P)}`],
    ["dem_won", "Winner", false, (r) => (r.dem_won ? `<span style="color:${DEM}">D</span>` : `<span style="color:${REP}">R</span>`)],
  ], rows, { key: "cycle", dir: -1 });
}
