// Turnout dashboard: reads data/ev2026/turnout_model.json (analysis/us_turnout_model.py, which builds on turnout_day.json), turnout_volume.json
// and, for the time series, data/ev2026/state_<st>.json. Every number shown comes from those files; this module only formats and draws them.
// Turnout only: who voted, not how. Unvalidated context, not used by any model.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, STATIC } from "./common.js";

const $ = (s) => document.querySelector(s);
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
const pc = (v, d = 1) => (v == null ? "–" : `${v.toFixed(d)}%`);
const pt = (v) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)} pt`);
const day = (iso) => `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}`;
const stamp = (iso) => (iso && iso.length > 10 ? `${day(iso)}, ${iso.slice(11, 16)} UTC` : iso ? `${day(iso)} (date only)` : "as-of unclear");
const STALE_MS = 24 * 3600e3;
const REFRESH_MS = 5 * 60e3;
const CTX = `<span class="td-ctx">unvalidated context, not used by any model</span>`;
const KIND = { election_day: "Election Day in person", early_in_person: "Early in person", mail_returned: "Mail / absentee returned", advance: "All pre-Election Day (early + mail)", total: "Total, all methods" };
const KIND_COLOR = { election_day: "var(--td-ed)", early_in_person: "var(--td-early)", mail_returned: "var(--td-mail)", advance: "var(--td-adv)", total: "var(--td-total)" };
const PROV = { official: "official file", official_post: "official, via post", derived: "official, summed", secondary: "secondary (news / compiler)", manual: "manual download" };
const STATUS = {
  all_methods: ["all methods", "A statewide count of pre-Election Day ballots plus Election Day check-ins, or a total of all methods."],
  partial_methods: ["pre-Election Day only", "Only some methods are published statewide (for example early and mail, without Election Day check-ins)."],
  counties_only: ["county sums only", "No whole-state count; the figures are sums of some counties and are lower bounds."],
  none: ["none found", "No turnout count found for this state."], blocked: ["blocked", "The source is blocked or excluded by project rule."], excluded: ["blocked", "The source is excluded by project rule."],
};
const STATUS_FILL = { all_methods: "#2f8a6c", partial_methods: "#86bfa7", counties_only: "#c9a24a", none: null, blocked: "#d9743a", excluded: "#d9743a" };
const METRICS = {
  expected: { label: "Banked, % of expected votes", val: (s) => s.banked_pct_expected, fmt: (v) => pc(v), dom: [0, 100], unit: "of the votes the model expects" },
  total22: { label: "Banked, % of 2022 total", val: (s) => s.banked_pct_2022, fmt: (v) => pc(v), dom: [0, 100], unit: "of the 2022 total" },
  same22: { label: "Now vs 2022, same point", val: (s) => s.vs_2022_same_point, fmt: (v) => `${v.toFixed(2)}×`, dom: [0.5, 1.5], div: true, unit: "of the 2022 count at the same point" },
  type: { label: "Data type", cat: true },
};

const T = { m: null, vol: null, st: null, metric: "expected", geo: null, ev: new Map(), sort: { key: "exp", dir: -1 }, gen: "", drawn: false };

function asOf(iso, genMs) {
  if (!iso) return { text: "as-of unclear", stale: false };
  const ms = iso.length > 10 ? Date.parse(iso) : Date.parse(`${iso}T23:59:59Z`);
  return { text: stamp(iso), stale: Number.isFinite(ms) && genMs - ms > STALE_MS };
}
const asOfHtml = (iso, genMs) => { const a = asOf(iso, genMs); return a.stale ? `<b class="td-stale">stale: ${esc(a.text)}</b>` : esc(a.text); };

// -------------------------------------------------------------------------------------------------------------------- header
function drawStrip() {
  const m = T.m, s = m.summary;
  const chip = (a, b, c) => `<span class="chip"><small>${a}</small><b>${b}</b><i>${c}</i></span>`;
  $("#tdd-chips").innerHTML = chip("Data as of", stamp(m.generated_utc).replace(", ", "<br>"), `checked ${stamp(m.published_utc)}`) +
    chip("States with a count", s.states_with_data, "of 51 incl. DC") + chip("Election Day check-ins", s.election_day, "states (state or county rows)") +
    chip("Early in person", s.early_in_person, "states with an early-in-person count") + chip("Mail / absentee", s.mail, "states with a mail count") +
    chip("All pre-Election Day", s.advance, "states with a combined count") + chip("Banked vs expected", s.with_expected_pct, "states with a whole-state count");
  $("#tdd-asof").textContent = `Refreshed from the turnout file about every 10 minutes while it runs; this page re-reads it every 5 minutes. A row whose own as-of is more than 24 hours before ${stamp(m.generated_utc)} is marked stale.`;
}
async function drawHist() {
  const el = $("#tdd-hist");
  const v = T.vol;
  el.innerHTML = v ? `<b>Does turnout volume tell us anything about the result?</b> ${esc(v.verdict)}${v.n_cases != null ? ` (${esc(String(v.n_cases))} past cases)` : ""}` : "The test of whether turnout volume says anything about the result has not been published.";
}

// -------------------------------------------------------------------------------------------------------------------- map
const seq = d3.scaleSequential(d3.interpolateGreens).domain([0, 100]);
const seqColor = (v) => seq(Math.max(0, Math.min(100, v)) * 0.85 + 8);
function fillFor(s) {
  const M = METRICS[T.metric];
  if (!s) return null;
  if (M.cat) return STATUS_FILL[s.status];
  const v = M.val(s);
  if (v == null) return null;
  if (M.div) return d3.interpolateRdBu(1 - Math.max(0, Math.min(1, (v - M.dom[0]) / (M.dom[1] - M.dom[0]))));
  return seqColor(v);
}
function tip(s) {
  if (!s) return "";
  const M = METRICS[T.metric];
  const v = M.cat ? null : M.val(s);
  return `${s.name}: ${M.cat ? STATUS[s.status][0] : v != null ? `${M.fmt(v)} ${M.unit}` : "no figure for this measure"}${s.banked ? `; ${int(s.banked.value)} banked${s.banked.complete ? "" : " (lower bound)"}` : ""}`;
}
async function drawMap() {
  T.geo ??= await getJSON("geo/us_regions.topo.json", { root: STATIC });
  const fc = topojson.feature(T.geo, T.geo.objects.regions);
  const host = $("#tdd-map"), w = Math.max(300, host.clientWidth || 560), h = Math.round(w * 0.62);
  const proj = d3.geoAlbersUsa().fitSize([w, h], fc), path = d3.geoPath(proj);
  const S = T.m.states, svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "group").attr("aria-label", "Map of the states, coloured by the chosen measure; each state is a button");
  const pick = (id) => select(id.toUpperCase(), true);
  svg.append("g").selectAll("path").data(fc.features).join("path").attr("class", (f) => `td-st${f.id.toUpperCase() === T.st ? " sel" : ""}`).attr("d", path).attr("data-st", (f) => f.id.toUpperCase())
    .attr("fill", (f) => fillFor(S[f.id.toUpperCase()]) ?? "var(--td-none)").attr("tabindex", 0).attr("role", "button").attr("aria-label", (f) => tip(S[f.id.toUpperCase()]))
    .on("click", (_, f) => pick(f.id)).on("keydown", (e, f) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(f.id); } })
    .append("title").text((f) => tip(S[f.id.toUpperCase()]));
  const dc = fc.features.find((f) => f.id === "dc");
  if (dc) {
    const [x, y] = path.centroid(dc);
    svg.append("circle").attr("class", `td-dc${T.st === "DC" ? " sel" : ""}`).attr("data-st", "DC").attr("cx", x).attr("cy", y).attr("r", 6).attr("fill", fillFor(S.DC) ?? "var(--td-none)")
      .attr("tabindex", 0).attr("role", "button").attr("aria-label", tip(S.DC)).on("click", () => pick("dc")).on("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick("dc"); } })
      .append("title").text(tip(S.DC));
  }
  host.replaceChildren(svg.node());
  const M = METRICS[T.metric];
  $("#tdd-legend").innerHTML = M.cat
    ? ["all_methods", "partial_methods", "counties_only", "blocked"].map((k) => `<span><i style="background:${STATUS_FILL[k]}"></i>${STATUS[k][0]}</span>`).join("") + `<span><i style="background:var(--td-none)"></i>none found</span>`
    : `<span>${M.fmt(M.dom[0])}<span class="grad" style="background:linear-gradient(90deg,${M.div ? `${d3.interpolateRdBu(1)},${d3.interpolateRdBu(0.5)},${d3.interpolateRdBu(0)}` : `${seqColor(0)},${seqColor(100)}`})"></span>${M.fmt(M.dom[1])}</span><span><i style="background:var(--td-none)"></i>no figure</span>`;
  $("#tdd-metric-note").textContent = M.cat ? "How much of the vote each state's published counts cover." : `${M.label}. Only whole-state counts are shown; states with only county sums or none are grey. Unvalidated context, not used by any model.`;
}
function drawMetric() {
  const tg = $("#tdd-metric");
  tg.replaceChildren(...Object.entries(METRICS).map(([k, m]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = m.label; b.setAttribute("aria-pressed", String(T.metric === k));
    b.onclick = () => { T.metric = k; drawMetric(); drawMap(); }; return b;
  }));
}

// -------------------------------------------------------------------------------------------------------------------- state panel
const card = (label, value, sub, cls = "") => `<div class="td-card ${cls}"><small>${label}</small><b>${value}</b><span>${sub}</span></div>`;
function methodCard(s, k, genMs) {
  const r = s.by_method?.[k];
  if (!r) return card(KIND[k], "–", "not published for this state");
  return card(KIND[k], int(r.count), `${r.partial ? `<em>sum of some counties only</em> · ` : ""}${asOfHtml(r.as_of, genMs)}`);
}
function kpis(s, genMs) {
  const b = s.banked, E = s.expected;
  const t22 = b?.complete && b.total_2022 ? `${pc(s.banked_pct_2022)}` : "–", t24 = b?.complete && b.total_2024 ? pc(s.banked_pct_2024) : "–";
  const same = s.vs_2022_same_point != null ? `, ${s.vs_2022_same_point.toFixed(2)}× the 2022 count at the same point` : "";
  return [
    methodCard(s, "election_day", genMs), methodCard(s, "early_in_person", genMs), methodCard(s, "mail_returned", genMs), methodCard(s, "advance", genMs),
    card("Total banked", b ? int(b.value) : "–", b ? `${esc(b.basis)}${b.complete ? "" : " · <em>lower bound</em>"} · ${asOfHtml(b.parts.map((p) => p.as_of).filter(Boolean).sort()[0], genMs)}` : "no count", "td-card-main"),
    card("% of expected", s.banked_pct_expected != null ? pc(s.banked_pct_expected) : "–", s.banked_pct_expected != null ? `of ${int(E.votes)} expected two-party votes · ${CTX}` : `${b && !b.complete ? "lower bound; not compared" : "no whole-state count"} · ${CTX}`),
    card("Of the 2022 total", t22, `${b?.total_2022 ? `${int(b.total_2022)} votes in 2022` : "no 2022 total in the source"}${same}`),
    card("Of the 2024 total", t24, b?.total_2024 ? `${int(b.total_2024)} votes in 2024` : "no 2024 total in the source"),
  ].join("");
}
function methodBar(s) {
  const b = s.banked;
  if (!b) return `<p class="note">No banked count to split.</p>`;
  const tot = b.value;
  const seg = b.parts.map((p) => `<span class="td-seg" style="flex:${Math.max(p.count, 0)};background:${KIND_COLOR[p.kind] ?? "var(--td-total)"}" title="${esc(KIND[p.kind])}: ${int(p.count)}"><span>${pc((100 * p.count) / tot, 0)}</span></span>`).join("");
  const key = b.parts.map((p) => `<span><i style="background:${KIND_COLOR[p.kind] ?? "var(--td-total)"}"></i>${esc(KIND[p.kind] ?? p.kind)}: <b>${int(p.count)}</b></span>`).join("");
  const only = b.parts.length === 1 && b.parts[0].kind === "total" ? " The source gives one total and no split by method." : "";
  const part = (b.partial_rows ?? []).length ? ` Not added (they only sum some counties): ${b.partial_rows.map((p) => `${esc(KIND[p.kind] ?? p.kind)} ${int(p.count)} (${esc(p.jurisdiction ?? "partial")})`).join("; ")}.` : "";
  return `<div class="td-bar" role="img" aria-label="Banked ballots by method: ${esc(b.parts.map((p) => `${KIND[p.kind]} ${int(p.count)}`).join(", "))}">${seg}</div><div class="ev-legend">${key}</div><p class="note">Kinds are added only where they do not overlap: all pre-Election Day ballots plus Election Day check-ins, or one total.${only}${part}</p>`;
}
function partyHtml(s) {
  const P = s.party ?? [];
  if (!P.length) return `<p class="note">No party split of ballots is published for ${esc(s.name)} in the files we have.</p>`;
  return P.map((p) => {
    const t = p.D + p.R + (p.other ?? 0), w = (v) => (100 * v) / t;
    const dr = pc((100 * p.D) / (p.D + p.R));
    return `<div class="td-party"><p class="chart-sub">${esc(p.label)} · ${esc(p.source)} · ${esc(stamp(p.as_of))}</p><div class="td-bar td-bar-party" role="img" aria-label="Registered Democrats ${int(p.D)}, Republicans ${int(p.R)}, other or unaffiliated ${int(p.other)}">` +
      `<span class="td-seg" style="flex:${p.D};background:var(--td-d)" title="Democratic registration: ${int(p.D)}"><span>D ${pc(w(p.D), 0)}</span></span><span class="td-seg" style="flex:${p.R};background:var(--td-r)" title="Republican registration: ${int(p.R)}"><span>R ${pc(w(p.R), 0)}</span></span>` +
      `${p.other ? `<span class="td-seg" style="flex:${p.other};background:var(--td-o)" title="Unaffiliated and other: ${int(p.other)}"><span>${pc(w(p.other), 0)}</span></span>` : ""}</div>` +
      `<p class="note">Registered Democrats are ${dr} of the ballots from registered Democrats or Republicans. ${CTX}</p></div>`;
  }).join("") + `<p class="note ev-caveat">By party <em>registration</em>, not by vote. Unaffiliated voters and crossover voting are large, so this is not a measure of who is ahead.</p>`;
}

// time series: official daily early-vote totals (state_<st>.json) and this page's own snapshots of the banked total
async function evSeries(st) {
  T.evIndex ??= getJSON("ev2026/index.json", { optional: true }).catch(() => null);
  const idx = await T.evIndex;
  if (!idx?.states?.some((c) => c.st === st.toLowerCase() && c.has_state)) return null;
  if (!T.ev.has(st)) T.ev.set(st, getJSON(`ev2026/state_${st.toLowerCase()}.json`, { optional: true }).catch(() => null));
  return T.ev.get(st);
}
function drawSeries(s, ev) {
  const host = $("#tdd-series");
  const daily = ev?.series?.date ? ev.series.date.map((d, i) => ({ t: Date.parse(`${d}T12:00:00Z`), v: ev.series.total[i] })).filter((p) => p.v != null) : [];
  const snaps = (T.m.history?.[s.st] ?? []).map((h) => ({ t: Date.parse(h.t), v: h.v })).filter((p) => Number.isFinite(p.t));
  if (daily.length + snaps.length < 2) {
    host.innerHTML = `<p class="note">Only ${daily.length + snaps.length} snapshot so far for ${esc(s.name)}. The line appears once the state has a second one: the turnout file adds a point at every refresh and the early-vote files add one per day.</p>`;
    return;
  }
  const w = Math.max(300, host.clientWidth || 640), h = w < 520 ? 230 : 280, m = { l: 62, r: 14, t: 12, b: 34 };
  const all = [...daily, ...snaps], x0 = d3.min(all, (p) => p.t), x1 = d3.max(all, (p) => p.t), exp = s.expected.votes;
  const X = d3.scaleTime([x0, x1 === x0 ? x0 + 36e5 : x1], [m.l, w - m.r]), Y = d3.scaleLinear([0, Math.max(d3.max(all, (p) => p.v), exp ?? 0) * 1.04], [h - m.b, m.t]).nice();
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img").attr("aria-label", `Ballots banked in ${s.name} over time: ${daily.length} daily early-vote points and ${snaps.length} snapshots of the turnout file`);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(X).ticks(w < 520 ? 4 : 7).tickFormat(d3.utcFormat("%b %-d")).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(Y).ticks(5).tickFormat((v) => d3.format(".3~s")(v).replace("G", "B")).tickSize(-(w - m.l - m.r)));
  if (exp) svg.append("line").attr("class", "td-exp").attr("x1", m.l).attr("x2", w - m.r).attr("y1", Y(exp)).attr("y2", Y(exp));
  if (exp) svg.append("text").attr("x", w - m.r).attr("y", Y(exp) - 4).attr("text-anchor", "end").text(`expected ${d3.format(".3~s")(exp)}`);
  const line = d3.line().x((p) => X(p.t)).y((p) => Y(p.v));
  if (daily.length > 1) svg.append("path").attr("class", "td-line daily").attr("d", line(daily));
  svg.selectAll("circle.daily").data(daily).join("circle").attr("class", "daily").attr("cx", (p) => X(p.t)).attr("cy", (p) => Y(p.v)).attr("r", 3.5).append("title").text((p) => `${new Date(p.t).toISOString().slice(0, 10)}: ${int(p.v)} (official daily file)`);
  if (snaps.length > 1) svg.append("path").attr("class", "td-line snap").attr("d", line(snaps));
  svg.selectAll("circle.snap").data(snaps).join("circle").attr("class", "snap").attr("cx", (p) => X(p.t)).attr("cy", (p) => Y(p.v)).attr("r", 3).append("title").text((p) => `${stamp(new Date(p.t).toISOString().replace(/\.\d+Z/, "Z"))}: ${int(p.v)} (turnout file)`);
  host.replaceChildren(svg.node());
  $("#tdd-series-key").innerHTML = `<span><i class="td-dot daily"></i>Official daily early-vote total (${daily.length} days)</span><span><i class="td-dot snap"></i>Banked total at each refresh of the turnout file (${snaps.length}; kinds as published)</span>${exp ? `<span><i class="td-dashed"></i>Expected votes (two-party)</span>` : ""}`;
}

// counties
function drawCounties(s) {
  const C = s.counties;
  $("#tdd-cty-sec").hidden = !C.length;
  if (!C.length) return;
  const kind = KIND[s.county_kind] ?? s.county_kind;
  $("#tdd-cty-sub").innerHTML = `${esc(s.name)}: ${C.length} counties listed${C.some((c) => c.no_figure) ? `, ${C.filter((c) => c.no_figure).length} with no figure from the source (a count of 0 or a gap; left out of the chart and every calculation)` : ""}. Measure: <b>${esc(kind)}</b>, divided by the county's expected votes (${esc(s.expected.source === "units" ? "registered models' expected two-party votes" : "0.72 × 2024 president two-party votes")}). ${CTX}`;
  const host = $("#tdd-scatter");
  if (!C.some((c) => !c.no_figure)) { host.replaceChildren(); } else drawScatter(s, C, host);
  drawTable(s, C);
}
function drawScatter(s, C, host) {
  const w = Math.max(300, host.clientWidth || 640), h = w < 520 ? 300 : 360, m = { l: 52, r: 16, t: 14, b: 42 };
  const rows = C.filter((c) => !c.no_figure).map((c) => ({ ...c, y: c.exp ? (100 * c.banked) / c.exp : null, x: 100 * c.share })).filter((c) => c.y != null);
  const X = d3.scaleLinear([Math.max(0, Math.floor(d3.min(rows, (c) => c.x) / 10) * 10 - 0), Math.min(100, Math.ceil(d3.max(rows, (c) => c.x) / 10) * 10)], [m.l, w - m.r]);
  const Y = d3.scaleLinear([0, Math.max(5, d3.max(rows, (c) => c.y)) * 1.08], [h - m.b, m.t]).nice();
  const R = d3.scaleSqrt([0, d3.max(rows, (c) => c.exp)], [2.5, w < 520 ? 16 : 24]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img").attr("aria-label", `${rows.length} counties of ${s.name}: 2024 Democratic two-party share against turnout so far as a percent of expected; the table below has the same numbers`);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(X).ticks(6).tickFormat((v) => `${v}%`).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(Y).ticks(5).tickFormat((v) => `${v}%`).tickSize(-(w - m.l - m.r)));
  if (X.domain()[0] < 50 && X.domain()[1] > 50) svg.append("line").attr("class", "td-fifty").attr("x1", X(50)).attr("x2", X(50)).attr("y1", m.t).attr("y2", h - m.b);
  svg.append("text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 6).attr("text-anchor", "middle").text(w < 520 ? "2024 Democratic two-party share" : "County 2024 Democratic share of the two-party presidential vote");
  svg.append("text").attr("transform", `translate(12,${(m.t + h - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text("so far, % of expected");
  svg.append("g").selectAll("circle").data(rows.sort((a, b) => b.exp - a.exp)).join("circle").attr("class", "td-bub").attr("cx", (c) => X(c.x)).attr("cy", (c) => Y(c.y)).attr("r", (c) => R(c.exp))
    .append("title").text((c) => `${c.name}: ${pc(c.y)} of expected (${int(c.banked)} of ${int(c.exp)}); 2024 D share ${pc(c.x)}`);
  host.replaceChildren(svg.node());
}
function drawTable(s, C) {
  const { key, dir } = T.sort, get = { name: (c) => c.name, banked: (c) => c.banked, exp: (c) => c.exp, y: (c) => (c.exp ? c.banked / c.exp : -1), x: (c) => c.share, as_of: (c) => c.as_of ?? "" };
  const sorted = [...C].sort((a, b) => (get[key](a) < get[key](b) ? -1 : get[key](a) > get[key](b) ? 1 : 0) * dir);
  const cols = [["name", "County"], ["banked", "So far", "n"], ["exp", "Expected votes", "n"], ["y", "% of expected", "n"], ["x", "2024 D share", "n"], ["as_of", "As of", "n"]];
  const gen = Date.parse(T.m.generated_utc);
  $("#tdd-cty-table").innerHTML = `<thead><tr>${cols.map(([k, t, c]) => `<th class="${c ?? ""}" ${key === k ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}><button type="button" data-k="${k}">${t}</button></th>`).join("")}</tr></thead><tbody>` +
    sorted.map((c) => (c.no_figure
      ? `<tr class="td-nofig"><td>${esc(c.name)}</td><td class="n" colspan="2">– (no figure from source)</td><td class="n">–</td><td class="n">${pc(100 * c.share)}</td><td class="n">${asOfHtml(c.as_of, gen)}</td></tr>`
      : `<tr><td>${esc(c.name)}</td><td class="n">${int(c.banked)}</td><td class="n">${int(c.exp)}</td><td class="n">${c.exp ? pc((100 * c.banked) / c.exp) : "–"}</td><td class="n">${pc(100 * c.share)}</td><td class="n">${asOfHtml(c.as_of, gen)}</td></tr>`)).join("") + `</tbody>`;
  $("#tdd-cty-table").querySelectorAll("th button").forEach((b) => (b.onclick = () => { T.sort = { key: b.dataset.k, dir: key === b.dataset.k ? -dir : b.dataset.k === "name" ? 1 : -1 }; drawTable(s, C); }));
}

// how the model sees this
function modelHtml(s) {
  const E = s.expected, b = s.banked, c = s.composition, o = s.order, L = s.lean;
  const a = `<li><b>(a) Expected final votes.</b> ${E.votes != null ? `The model assumes about <b>${int(E.votes)}</b> two-party votes in ${esc(s.name)}` : "No expected-vote figure is available"} (${esc(E.basis)}). ` +
    `${s.banked_pct_expected != null ? `Banked ballots are <b>${pc(s.banked_pct_expected)}</b> of that (${int(b.value)} banked; ballots include third-party and blank votes, so the share runs a little high).` : b ? `The banked figure (${int(b.value)}) is a lower bound from some counties, so no share is given.` : "There is no banked count to compare."} ${CTX}</li>`;
  let bb;
  if (c.ok) {
    const zero = c.lo <= 0 && c.hi >= 0;
    bb = `<li><b>(b) Composition shift.</b> If every county voted like its 2024 president two-party share${c.swing_pt != null ? ` moved ${pt(c.swing_pt)} by the poll-implied swing (${esc(c.swing_races.join(", "))})` : " (no poll swing available, so no shift applied)"}, ` +
      `the statewide Democratic share would be <b>${pc(c.share_so_far, 2)}</b> weighted by the ballots counted so far (${esc((KIND[c.kind] ?? c.kind).toLowerCase())}) and <b>${pc(c.share_expected, 2)}</b> weighted by expected turnout, over the ${c.n} counties with a count. ` +
      `Difference: <b>${pt(c.diff_pt)}</b> (95% interval ${pt(c.lo)} to ${pt(c.hi)}, bootstrap of ${c.boot} resamples of the ${c.n} counties)${zero ? "; the interval includes zero, so the counties voting so far are not clearly more Democratic or Republican than expected turnout" : c.diff_pt > 0 ? "; the counties voting so far lean more Democratic than their expected-turnout weight" : "; the counties voting so far lean more Republican than their expected-turnout weight"}. ` +
      `These counties are ${c.coverage_pct != null ? pc(c.coverage_pct, 0) : "–"} of the state's expected votes. This compares places only, not people. ${CTX}</li>`;
  } else bb = `<li><b>(b) Composition shift.</b> Not computed for ${esc(s.name)}: ${esc(c.why)}. It needs at least ${T.m.min_counties} counties with a count.</li>`;
  const cc = `<li><b>(c) Count order.</b> ${o.text ? `The registered configuration says: “${esc(o.text)}” (${esc(o.config)}).` : "Unknown: no registered configuration states the order for this state."} ` +
    `${L?.text ? `By party registration, ${pc(L.d_share_of_dr)} of the banked ballots from registered Democrats or Republicans are Democratic: ${esc(L.text)}. Registration-based, not a vote count. ${CTX}` : L ? `Registered Democrats are ${pc(L.d_share_of_dr)} of banked ballots from registered Democrats or Republicans (registration-based); with no stated count order, no first-returns wording is given.` : "No party split of banked ballots is published, so no first-returns wording is given."}</li>`;
  const d = `<li><b>(d)</b> The registered live models use none of this before the count: they learn each county's turnout from the votes counted, county by county.</li>`;
  return `<ul class="td-model">${a}${bb}${cc}${d}</ul>`;
}

function sourcesHtml(s, genMs) {
  if (!s.sources.length) return `<p class="note">No statewide row. ${esc(s.verdict)}.</p>`;
  const rows = s.sources.map((r) => {
    const a = r.as_of_utc ?? r.as_of_date ?? r.fetched_utc;
    return `<tr><td>${esc(r.jurisdiction)}<span class="ev-detail">${esc(r.measure)}${r.note ? ` — ${esc(r.note)}` : ""}</span></td><td><span class="td-kind">${esc(KIND[r.kind] ?? r.kind)}</span></td><td class="n">${r.count != null ? int(r.count) : "–"}</td>` +
      `<td class="n">${asOfHtml(a, genMs)}${r.as_of_utc || r.as_of_date ? "" : `<span class="ev-detail">fetched, as-of not stated</span>`}</td><td><span class="td-badge">${esc(PROV[r.prov] ?? r.prov)}</span></td><td>${/^https?:\/\//.test(r.source_url ?? "") ? `<a href="${esc(r.source_url)}" rel="noopener">source</a>` : "–"}</td></tr>`;
  }).join("");
  return `<div class="table-scroll" tabindex="0" role="region" aria-label="Statewide rows for ${esc(s.name)}"><table class="compact td-table"><thead><tr><th>Place and measure</th><th>Kind</th><th class="n">Count</th><th class="n">As of</th><th>Provenance</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function drawPanel() {
  const s = T.m.states[T.st], genMs = Date.parse(T.m.generated_utc);
  const [label, why] = STATUS[s.status];
  $("#tdd-title").textContent = s.name;
  $("#tdd-status").innerHTML = `<span class="td-badge td-s-${s.status}" title="${esc(why)}">${esc(label)}</span> ${esc(s.verdict)}`;
  $("#tdd-kpis").innerHTML = kpis(s, genMs);
  $("#tdd-bar").innerHTML = methodBar(s);
  $("#tdd-party").innerHTML = partyHtml(s);
  $("#tdd-model").innerHTML = modelHtml(s);
  $("#tdd-sources").innerHTML = sourcesHtml(s, genMs);
  drawCounties(s);
  const ev = await evSeries(s.st);
  if (T.st === s.st) drawSeries(s, ev);
  document.title = `Turnout today: ${s.name}`;
}

async function select(st, scroll = false) {
  if (!T.m.states[st]) return;
  T.st = st;
  $("#tdd-pick").value = st;
  const u = new URL(location.href); u.searchParams.set("st", st); history.replaceState(null, "", u);
  document.querySelectorAll("#tdd-map [data-st]").forEach((el) => el.classList.toggle("sel", el.dataset.st === st));
  await drawPanel();
  if (scroll && matchMedia("(max-width: 1000px)").matches) $("#tdd-title").scrollIntoView({ block: "start" });
}

// -------------------------------------------------------------------------------------------------------------------- boot
async function fetchJSON(name) {
  const r = await fetch(new URL(`data/ev2026/${name}`, location.href), { cache: "no-store" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}
async function load(first) {
  const m = await fetchJSON("turnout_model.json");
  if (!m) throw new Error("turnout_model.json is not published yet");
  T.vol = await fetchJSON("turnout_volume.json").catch(() => null);
  if (m.generated_utc === T.gen && m.published_utc === T.m?.published_utc) return;
  T.gen = m.generated_utc; T.m = m;
  drawStrip(); drawHist();
  const pick = $("#tdd-pick");
  if (!pick.options.length) {
    const names = Object.values(m.states).sort((a, b) => a.name.localeCompare(b.name));
    pick.innerHTML = names.map((s) => `<option value="${s.st}">${esc(s.name)}${s.banked || s.counties.length ? "" : ` (${STATUS[s.status][0]})`}</option>`).join("");
    pick.onchange = () => select(pick.value);
    const want = new URLSearchParams(location.search).get("st")?.toUpperCase();
    const first1 = Object.values(m.states).filter((s) => s.banked?.complete).sort((a, b) => b.banked.value - a.banked.value)[0];
    T.st = m.states[want] ? want : first1?.st ?? names[0].st;
  }
  drawMetric();
  await drawMap();
  await select(T.st);
  if (first) window.__tddReady = true;
}

export async function initDash() {
  try {
    await load(true);
  } catch (e) {
    $("#tdd-asof").textContent = "The turnout dashboard file is not published yet.";
    window.__tddReady = true;
    console.warn(e);
  }
  document.fonts?.ready;
  setInterval(() => load(false).catch(() => {}), REFRESH_MS);
  let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { if (T.m) { drawMap(); drawPanel(); } }, 200); });
}
