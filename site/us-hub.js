// US midterms 2026 hub (us.html): polls, pollsters, markets, track record and census, from the
// files in data/us2026/hub/ (built by analysis/; see their `source`/`sources` fields). Margins are
// Democratic minus Republican in points of the two-party vote; prices and P are the chance the
// Democratic side wins. Deep links: ?tab=polls&race=tx-sen.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { C, getJSON, esc, num, pct, fmtDate, marginText, probit } from "./common.js";

const $ = (id) => document.getElementById(id);
const DEM = "#1f5fbf", REP = "#c8102e", POLLS = "#8d8a82";
const TABS = ["polls", "pollsters", "markets", "track", "census"];
const RACE_TABS = new Set(["polls", "markets"]);
const q = new URLSearchParams(location.search);
const S = { tab: TABS.includes(q.get("tab")) ? q.get("tab") : "polls", race: q.get("race"), drawn: new Set(), sort: {} };
const m2 = (share) => (share == null ? null : (2 * share - 1) * 100); // two-party share -> margin
const day = (d) => fmtDate(d).replace(/ \d{4}$/, "");
const sources = (x) => {
  const list = [x?.source, ...(x?.sources ?? [])].flat().filter(Boolean);
  return list.length ? `<p class="note hub-src">Sources: ${list.map((s) => (typeof s === "string" ? esc(s) : `${s.url ? `<a href="${esc(s.url)}">${esc(s.name)}</a>` : esc(s.name)}${s.licence ? `, ${esc(s.licence)}` : ""}`)).join("; ")}.</p>` : "";
};

async function main() {
  const opt = { optional: true };
  const [overview, polls, pollsters, markets, backtest, census] = await Promise.all(
    ["overview.json", "hub/polls.json", "hub/pollsters.json", "hub/markets.json", "hub/backtest.json", "hub/census.json"].map((f) => getJSON(`us2026/${f}`, opt)));
  Object.assign(S, { overview, polls, pollsters, markets, backtest, census });
  // race names from the overview ("Texas Senate"), for every race in the polls or markets files
  S.names = new Map();
  for (const [o, off] of Object.entries(overview?.offices ?? {})) for (const r of off.races ?? []) {
    S.names.set(r.race_key, `${r.state_name ?? r.state.toUpperCase()} ${o === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`);
    S.ov ??= new Map(); S.ov.set(r.race_key, r);
  }
  S.races = [...new Set([...Object.keys(polls?.races ?? {}), ...Object.keys(markets?.races ?? {})])].sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  if (!S.races.includes(S.race)) S.race = S.races.includes("tx-sen") ? "tx-sen" : S.races[0];
  setupRaceSelect();
  document.querySelectorAll(".tabs [role=tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  showTab(S.tab);
}
const nameOf = (k) => S.names.get(k) ?? k.toUpperCase();

function setupRaceSelect() {
  const sel = $("race-select"), filter = $("race-filter");
  const fill = () => {
    const f = filter.value.trim().toLowerCase();
    const list = S.races.filter((k) => !f || nameOf(k).toLowerCase().includes(f) || k.includes(f));
    sel.innerHTML = list.map((k) => `<option value="${esc(k)}"${k === S.race ? " selected" : ""}>${esc(nameOf(k))}</option>`).join("");
    if (list.length && !list.includes(S.race)) { S.race = list[0]; redraw(); }
  };
  filter.addEventListener("input", fill);
  sel.addEventListener("change", () => { S.race = sel.value; redraw(); });
  fill();
}

function showTab(tab) {
  S.tab = tab;
  for (const t of TABS) {
    $(`pane-${t}`).hidden = t !== tab;
    document.querySelector(`[data-tab=${t}]`).setAttribute("aria-selected", String(t === tab));
  }
  $("hub-race").hidden = !RACE_TABS.has(tab);
  redraw();
}

// the address follows the tab and race (?tab=polls&race=tx-sen)
function redraw() {
  const url = new URL(location.href);
  url.searchParams.set("tab", S.tab);
  if (RACE_TABS.has(S.tab)) url.searchParams.set("race", S.race); else url.searchParams.delete("race");
  history.replaceState(null, "", url);
  const el = $(`pane-${S.tab}`);
  if (S.tab === "polls") drawPolls(el);
  else if (S.tab === "markets") drawMarkets(el);
  else if (!S.drawn.has(S.tab)) {
    S.drawn.add(S.tab);
    ({ pollsters: drawPollsters, track: drawTrack, census: drawCensus })[S.tab](el);
  }
}

/* ---------------------------------------------------------------- small charts */

// time chart: series [{ values: [[Date, v]], color, width, dash, label }], y domain from the data
function timeChart(el, series, { height = 240, fmt = (v) => v, zero = false, band = null, bars = null, dots = null, domain = null } = {}) {
  const width = Math.max(300, el.clientWidth || 640), m = { l: 46, r: 110, t: 10, b: 22 };
  const all = series.flatMap((s) => s.values.map((d) => d[0]));
  if (!all.length) { el.innerHTML = `<p class="note">No history for this race.</p>`; return; }
  const x = d3.scaleUtc().domain(d3.extent(all)).range([m.l, width - m.r]);
  const ys = [...series.flatMap((s) => s.values.map((d) => d[1])), ...(dots ?? []).map((d) => d.v), ...(zero ? [0] : [])].filter((v) => v != null && Number.isFinite(v));
  const [a, b] = d3.extent(ys);
  const y = d3.scaleLinear().domain(domain ?? [a - (b - a) * 0.08 - 0.5, b + (b - a) * 0.08 + 0.5]).nice().range([height - m.b - (bars ? 34 : 0), m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height).style("display", "block");
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
    svg.append("path").datum(s.values.filter((d) => d[1] != null)).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width ?? 1.6).attr("stroke-dasharray", s.dash ?? null)
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
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
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

// a sortable table: cols [[key, label, numeric, cell(row) -> html]]
function sortable(el, id, cols, rows, initial) {
  S.sort[id] ??= initial;
  const draw = () => {
    const { key, dir } = S.sort[id];
    const sorted = [...rows].sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
    el.innerHTML = `<table class="races hub-table"><thead><tr>${cols.map(([k, l, n]) => `<th class="${n ? "n" : ""}" aria-sort="${key === k ? (dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${l}</button></th>`).join("")}</tr></thead>
      <tbody>${sorted.map((r) => `<tr>${cols.map(([k, , n, cell]) => `<td class="${n ? "n" : ""}">${cell ? cell(r) : esc(r[k] ?? "–")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    el.querySelectorAll("th button").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.key;
      S.sort[id] = { key: k, dir: S.sort[id].key === k ? -S.sort[id].dir : typeof rows[0]?.[k] === "string" ? 1 : -1 };
      draw();
    }));
  };
  draw();
}

/* ---------------------------------------------------------------- polls */

function drawPolls(el) {
  const R = S.polls?.races?.[S.race];
  if (!R) { el.innerHTML = `<p class="note">No polls for ${esc(nameOf(S.race))}.</p>${sources(S.polls)}`; return; }
  const a = R.average ?? {};
  const head = a.n_polls
    ? `<p class="hub-lede"><b>${esc(nameOf(S.race))}</b>: ${esc(R.dem)} against ${esc(R.opp)}. Polling average <b>${marginText(m2(a.avg))}</b> (${pct(a.avg)} of the two-party vote for ${esc(R.dem)}, ± ${(200 * a.sd).toFixed(1)} pt), chance ${num(a.p_win)}; ${a.n_polls} polls from ${a.n_pollsters} pollsters, the latest ending ${esc(day(a.last_date))}.</p>`
    : `<p class="hub-lede"><b>${esc(nameOf(S.race))}</b>: no recent polls.</p>`;
  el.innerHTML = `${head}<div class="hub-chart" id="polls-chart"></div><div class="hub-scroll" id="polls-table"></div>
    <p class="note">Average: ${esc(S.polls.rule ?? "")}. Over time: ${esc(S.polls.history_note ?? "")}. Dots: each poll's two-party margin, sized by its weight in today's average (grey: not in it). Bars in the table: the weight.</p>${sources(S.polls)}`;
  const hist = (R.history ?? []).map(([d, v]) => [new Date(d), m2(v)]);
  const maxW = d3.max(R.polls, (p) => p.weight ?? 0) || 1;
  const dots = R.polls.filter((p) => p.dem2 != null).map((p) => ({ t: new Date(p.end), v: m2(p.dem2), r: 2 + 6 * Math.sqrt((p.weight ?? 0) / maxW), color: p.weight ? C.model : POLLS, title: `${p.pollster}, ${day(p.end)}: ${marginText(m2(p.dem2))}` }));
  timeChart($("polls-chart"), [{ values: hist, color: C.model, width: 2, label: "average" }], { fmt: (v) => marginText(v).replace(".0", ""), zero: true, dots });
  const rows = R.polls.map((p) => ({ ...p, margin: m2(p.dem2), w: p.weight ?? -1 }));
  sortable($("polls-table"), "polls", [
    ["pollster", "Pollster", false, (p) => `${p.url ? `<a href="${esc(p.url)}">${esc(p.pollster)}</a>` : esc(p.pollster)}${p.partisan ? ` <small>(${esc(p.partisan)})</small>` : ""}`],
    ["end", "Field dates", false, (p) => `${esc(day(p.start))}–${esc(day(p.end))}`],
    ["n", "Sample", true, (p) => `${p.n ?? "–"} <small>${esc(p.population ?? "")}</small>`],
    ["dem", esc(R.dem), true, (p) => num(p.dem, 0)],
    ["opp", esc(R.opp), true, (p) => num(p.opp, 0)],
    ["margin", "Margin", true, (p) => marginText(p.margin)],
    ["w", "Weight in the average", true, (p) => (p.weight ? `<span class="wbar"><i style="width:${(100 * p.weight / maxW).toFixed(0)}%"></i></span> ${pct(p.weight, 0)}` : `<small>${esc(p.excluded ?? "not in the average")}</small>`)],
  ], rows, { key: "end", dir: -1 });
}

/* ---------------------------------------------------------------- pollsters */

function drawPollsters(el) {
  const P = S.pollsters;
  if (!P) { el.innerHTML = `<p class="note">No pollster file yet.</p>`; return; }
  el.innerHTML = `<p class="hub-lede">How far each pollster's final-three-week polls landed from the result: ${esc(P.window ?? "")}. Lean: the average error (${esc(P.units ?? "")}); RMSE: the typical size of an error.</p>
    <label class="select"><span>Pollster</span> <input id="pollster-filter" type="search" placeholder="Filter" aria-label="Filter pollsters"></label>
    <div class="hub-scroll" id="pollster-table"></div>
    <p class="note"><span class="badge">2026</span> ${esc(P.in_2026_rule ?? "")}.</p>${sources(P)}`;
  const rows = P.pollsters.map((p) => ({ ...p, n_cycles: p.cycles?.length ?? 0, in: p.in_2026 ? 1 : 0 }));
  const draw = () => {
    const f = $("pollster-filter").value.trim().toLowerCase();
    sortable($("pollster-table"), "pollsters", [
      ["pollster", "Pollster", false, (p) => `${esc(p.pollster)}${p.in_2026 ? ` <span class="badge" title="in the 2026 averages">2026</span>` : ""}`],
      ["polls", "Polls", true], ["races", "Races", true],
      ["n_cycles", "Cycles", true, (p) => `<span title="${esc((p.cycles ?? []).join(", "))}">${p.n_cycles}</span>`],
      ["lean", "Lean", true, (p) => (p.lean == null ? "–" : `${marginText(p.lean)}`)],
      ["rmse", "RMSE (pt)", true, (p) => num(p.rmse, 1)],
    ], rows.filter((p) => !f || p.pollster.toLowerCase().includes(f)), { key: "polls", dir: -1 });
  };
  $("pollster-filter").addEventListener("input", draw);
  draw();
}

/* ---------------------------------------------------------------- markets */

function drawMarkets(el) {
  const M = S.markets?.races?.[S.race], ov = S.ov?.get(S.race), mg = ov?.margins ?? {};
  const pm = M?.pm, k = M?.kalshi;
  const sd = mg.market_sd_used ?? null;
  const fig = (label, v) => `<span class="chip"><small>${label}</small><b>${v}</b></span>`;
  const modelM = mg.model_polls ?? mg.model_v2 ?? mg.model ?? mg.estimate ?? null;
  el.innerHTML = `<p class="hub-lede"><b>${esc(nameOf(S.race))}</b>: the chance the Democratic side wins on each venue, and the same prices read as margins next to the polls and the model.</p>
    <div class="chips">${pm ? fig("Polymarket bid / ask", `${num(pm.bid)} / ${num(pm.ask)}`) + fig("Polymarket volume (total)", pm.volume_total != null ? `$${d3.format(".3s")(pm.volume_total).replace("G", "B")}` : "–") : ""}${k ? fig("Kalshi bid / ask", `${num(k.bid)} / ${num(k.ask)}`) : ""}${fig("Model margin", marginText(modelM))}${fig("Polls", marginText(mg.polls))}</div>
    <h3 class="tab-h">Price (chance the Democratic side wins)</h3><div class="hub-chart" id="mk-price"></div>
    <h3 class="tab-h">As margins</h3><div class="hub-chart" id="mk-margin"></div>
    <p class="note">Market-implied margin: the margin at which the model's uncertainty (sd ${sd != null ? `${sd.toFixed(1)} pt` : "–"}) gives the price, Φ⁻¹(price) × sd. ${esc(S.markets?.note ?? "")}</p>
    <h3 class="tab-h">Were past market prices right? (2024)</h3>
    <div class="hub-pair"><div id="mk-cal"></div><p class="note">${S.markets?.accuracy ? `${S.markets.accuracy.n} Senate and governor markets, the price a week before the election against who won: Brier score ${num(S.markets.accuracy.brier, 3)} (0 is perfect; always saying 0.5 scores 0.25). ${esc(S.markets.accuracy.coverage ?? "")}` : "Not available."}</p></div>${sources(S.markets)}`;
  const pmD = (pm?.daily ?? []).map(([d, p]) => [new Date(d), p]);
  const kD = (k?.daily ?? []).map(([d, p]) => [new Date(d), p]);
  timeChart($("mk-price"), [{ values: pmD, color: C.market, label: "Polymarket" }, { values: kD, color: C.kalshi, label: "Kalshi" }],
    { height: 220, fmt: (v) => num(v, 1), domain: [0, 1], bars: (k?.daily ?? []).filter((d) => d[2] != null).map(([d, , v]) => [new Date(d), v]) });
  const toM = (rows) => rows.map(([d, p]) => [d, sd != null && p > 0 && p < 1 ? probit(p) * sd : null]);
  const pollsHist = (S.polls?.races?.[S.race]?.history ?? []).map(([d, v]) => [new Date(d), m2(v)]);
  const span = d3.extent([...pmD, ...kD, ...pollsHist].map((d) => d[0]));
  timeChart($("mk-margin"), [
    { values: toM(pmD), color: C.market, label: "Polymarket-implied" },
    { values: toM(kD), color: C.kalshi, width: 1.2, label: "Kalshi-implied" },
    { values: pollsHist, color: POLLS, dash: "4,3", label: "polls" },
    ...(modelM != null && span[0] ? [{ values: [[span[0], modelM], [span[1], modelM]], color: C.model, width: 2, label: "model now" }] : []),
  ], { height: 220, fmt: (v) => marginText(v).replace(".0", ""), zero: true });
  if (S.markets?.accuracy?.bins) calibration($("mk-cal"), [{ bins: S.markets.accuracy.bins, color: C.market, label: "market" }]);
}

/* ---------------------------------------------------------------- track record */

function drawTrack(el) {
  const B = S.backtest;
  if (!B) { el.innerHTML = `<p class="note">No backtest file yet.</p>`; return; }
  const sm = B.summary ?? {};
  const line = (label, k, f) => `<tr><td>${label}</td><td class="n m">${f(sm.model?.[k])}</td><td class="n">${f(sm.baseline?.[k])}</td></tr>`;
  el.innerHTML = `<p class="hub-lede">The poll model on past Senate and governor races, against a baseline of past results only. ${esc(B.label ?? "")}</p>
    <div class="hub-pair"><table class="races hub-table"><thead><tr><th></th><th class="n">Model</th><th class="n">Past results</th></tr></thead><tbody>
      ${line("Races", "races", (v) => v ?? "–")}${line("Result inside the 68% band", "cover68", (v) => pct(v, 0))}${line("Result inside the 90% band", "cover90", (v) => pct(v, 0))}
      ${line("Mean absolute error (pt)", "mae_margin", (v) => num(v, 1))}${line("Brier score (lower is better)", "brier", (v) => num(v, 3))}${line("Log loss (lower is better)", "log_loss", (v) => num(v, 3))}
    </tbody></table><div><div id="tr-cal"></div><p class="note">Chance given against how often the Democrat won: <span class="m">model</span>, <span style="color:${POLLS}">past results</span>. On the dashed line the chances were right on average.</p></div></div>
    <h3 class="tab-h">Every race</h3><div class="hub-scroll" id="tr-table"></div>
    <p class="note">Baseline: ${esc(typeof B.baseline === "string" ? B.baseline : JSON.stringify(B.baseline))}.</p>${sources(B)}`;
  calibration($("tr-cal"), [{ bins: sm.baseline?.calibration ?? [], color: POLLS, label: "past results" }, { bins: sm.model?.calibration ?? [], color: C.model, label: "model" }]);
  sortable($("tr-table"), "track", [
    ["cycle", "Year", true], ["state", "State", false], ["office", "Office", false, (r) => (r.office === "gov" ? "governor" : "Senate")], ["n_polls", "Polls", true],
    ["pred_margin", "Model", true, (r) => `${marginText(r.pred_margin)} <small>± ${num(r.sd_margin, 1)}</small>`],
    ["actual_margin", "Result", true, (r) => marginText(r.actual_margin)],
    ["error", "Error (pt)", true, (r) => num(r.error, 1)],
    ["base_margin", "Past results", true, (r) => marginText(r.base_margin)],
    ["P", "Chance (model / past)", true, (r) => `${num(r.P)} / ${num(r.base_P)}`],
    ["dem_won", "Winner", false, (r) => (r.dem_won ? `<span style="color:${DEM}">D</span>` : `<span style="color:${REP}">R</span>`)],
  ], B.races ?? [], { key: "cycle", dir: -1 });
}

/* ---------------------------------------------------------------- census */

const LAYERS = [
  ["change_pct", "Population change 2020–25", (v) => `${v > 0 ? "+" : ""}${num(v, 1)}%`, "div"],
  ["age65_pct", "Age 65+", (v) => `${num(v, 1)}%`],
  ["ba_plus_pct", "Bachelor's or higher", (v) => `${num(v, 1)}%`],
  ["hispanic_pct", "Hispanic", (v) => `${num(v, 1)}%`],
  ["black_pct", "Black", (v) => `${num(v, 1)}%`],
  ["median_income", "Median household income", (v) => `$${d3.format(",")(Math.round(v))}`],
];
async function drawCensus(el) {
  const Cn = S.census;
  if (!Cn) { el.innerHTML = `<p class="note">No census file yet.</p>`; return; }
  el.innerHTML = `<div class="toggle" id="cen-layer" role="group" aria-label="Census layer">${LAYERS.map(([k, l], i) => `<button type="button" data-k="${k}" aria-pressed="${i === 0}">${l}</button>`).join("")}</div>
    <div class="hub-pair"><div><div id="cen-map" class="hub-map"></div><div id="cen-legend" class="legend-note"></div></div><div><p class="hub-lede" id="cen-sum"></p><div class="hub-scroll" id="cen-states"></div></div></div>
    <p class="note">${esc(Cn.redistricting ?? "")} Fields: ${Object.entries(Cn.fields ?? {}).map(([k, v]) => `${esc(LAYERS.find((l) => l[0] === k)?.[1] ?? k)}: ${esc(v)}`).join("; ")}.</p>${sources(Cn)}`;
  const topo = await getJSON("geo/us_counties.topo.json");
  const fc = topojson.feature(topo, topo.objects.counties);
  const w = Math.max(320, $("cen-map").clientWidth || 640), h = Math.round(w * 0.62);
  const path = d3.geoPath(d3.geoAlbersUsa().fitSize([w, h], fc));
  const svg = d3.select("#cen-map").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
  const idOf = (f) => f.properties?.unit_id ?? f.id;
  const counties = svg.append("g").selectAll("path").data(fc.features).join("path").attr("d", path).attr("stroke", "#fff").attr("stroke-width", 0.2);
  const title = counties.append("title");
  svg.append("path").datum(topojson.mesh(topo, topo.objects.counties, (a, b) => a !== b && idOf(a).split("-")[1] !== idOf(b).split("-")[1])).attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 1).attr("d", path);
  const states = Object.entries(Cn.states ?? {}).map(([st, s]) => ({ st, ...s, race: [...(S.ov?.values() ?? [])].some((r) => r.state === st) }));
  const setLayer = (k) => {
    const [, label, fmt, kind] = LAYERS.find((l) => l[0] === k);
    const vals = Object.values(Cn.counties).map((c) => c[k]).filter((v) => v != null);
    const colour = kind === "div"
      ? d3.scaleDiverging([-10, 0, 10], d3.interpolateBrBG).clamp(true) // not red/blue, which read as parties
      : d3.scaleSequentialQuantile(vals, d3.interpolate("#ece9e1", "#3d5a40"));
    counties.attr("fill", (f) => { const v = Cn.counties[idOf(f)]?.[k]; return v == null ? "#efede7" : colour(v); });
    title.text((f) => `${f.properties?.name ?? idOf(f)}: ${Cn.counties[idOf(f)]?.[k] != null ? fmt(Cn.counties[idOf(f)][k]) : "no data"}`);
    $("cen-legend").textContent = kind === "div" ? `${label}: brown, shrinking; teal, growing (full colour at ±10%). Hover a county for its figure.` : `${label}: darker is higher (by county rank, low to high). Hover a county for its figure.`;
    el.querySelectorAll("#cen-layer button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
  };
  el.querySelectorAll("#cen-layer button").forEach((b) => b.addEventListener("click", () => setLayer(b.dataset.k)));
  setLayer("change_pct");
  const grew = states.filter((s) => s.race).sort((a, b) => b.change_pct - a.change_pct);
  $("cen-sum").innerHTML = grew.length ? `Of the ${grew.length} states with a Senate or governor race, <b>${esc(grew[0].name)}</b> grew fastest since 2020 (${num(grew[0].change_pct, 1)}%) and <b>${esc(grew.at(-1).name)}</b> changed least (${num(grew.at(-1).change_pct, 1)}%).` : "";
  sortable($("cen-states"), "census", [
    ["name", "State", false, (s) => `${esc(s.name)}${s.race ? "" : " <small>no race</small>"}`],
    ["pop2025", "Population 2025", true, (s) => d3.format(",")(s.pop2025)],
    ["change_pct", "Change since 2020", true, (s) => `${s.change_pct > 0 ? "+" : ""}${num(s.change_pct, 1)}%`],
  ], states, { key: "change_pct", dir: -1 });
}

main().catch((err) => { $("hub").innerHTML = `<p class="dash-note load-error">The hub could not load: ${esc(err.message)}</p>`; });
