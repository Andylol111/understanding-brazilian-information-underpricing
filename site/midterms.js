// US midterms page: renders data/us2026_context/*.json (built by analysis/us_context.py), one state at a time.
// Every number shown comes from those files; this module only formats and draws them.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, STATIC } from "./common.js";

const $ = (s) => document.querySelector(s);
const ctxGet = (f, optional = true) => getJSON(`us2026_context/${f}?v=${window.__V}`, { root: STATIC, optional });
const int = (x) => (x == null ? "–" : Math.round(x).toLocaleString("en-US"));
const f1 = (x) => (x == null ? "–" : x.toFixed(1));
const sg = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);
const sgi = (x) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(Math.round(x)).toLocaleString("en-US")}`);
const link = (u, t) => (u ? `<a href="${esc(u)}" rel="noopener">${esc(t)}</a>` : esc(t));
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayShort = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}` : "–");
const dayLong = (iso) => (iso ? `${dayShort(iso)}, ${iso.slice(0, 4)}` : "–");
const YCOL = { 2018: "#8a6fb0", 2020: "#2f8a6c", 2022: "#c49a1b", 2024: "#6a7f99" };
const ELECTION = { 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05" };
const LEAD_ORDER = [2022, 2018, 2024, 2020];
const BASIS = { "same day": "same day", final: "final count", complete: "complete early vote" };
const COL = { D: "#1f5fbf", R: "#c8102e", O: "#8a8578" };
const TIER_COL = { tossup: "#8a6fb0", lean_d: "#a9c1e6", lean_r: "#e6aab2", far: "#d9d5c9", none: "#efede7" };
const UF_LABEL = "UF Election Lab (compiled from official state sources), used with permission";

const S = { ctx: null, ev: null, mix: null, reg: null, issues: null, ct: null, geo: null, years: new Set(), st: null, poll: {}, iss: {} };
const stateName = (st) => (st === "us" ? "United States" : S.ctx.states.find((s) => s.st === st)?.name ?? st.toUpperCase());
const evOf = (st) => S.ev?.states.find((s) => s.state === st);
const leadYear = (has) => LEAD_ORDER.find((y) => S.years.has(y) && has(y));

// ------------------------------------------------------------------------------------------------------ dot plot (HTML)
const niceMax = (v) => { const st = v > 60 ? 20 : v > 30 ? 10 : v > 12 ? 5 : 2; return { max: Math.ceil(v / st) * st, step: st }; };
function axisHTML(max, step, lo = 0) {
  const t = [];
  for (let v = lo; v <= max + 1e-9; v += step) t.push(v);
  return `<div class="dp-axis"><span></span><span class="dp-ticks">${t.map((v) => `<i style="left:${(100 * (v - lo) / (max - lo)).toFixed(2)}%">${v}</i>`).join("")}</span><span></span></div>`;
}
const dotsHTML = (pts, lo, max) => {
  const xs = pts.filter((p) => p.v != null).map((p) => p.v);
  if (!xs.length) return "";
  const x = (v) => (100 * (v - lo) / (max - lo)).toFixed(2);
  const a = Math.min(...xs), b = Math.max(...xs);
  return `<i class="dp-conn" style="left:${x(a)}%;width:${(x(b) - x(a)).toFixed(2)}%"></i>` +
    pts.filter((p) => p.v != null).map((p) => `<i class="dp-d ${p.cls}" style="left:${x(p.v)}%${p.col ? `;--c:${p.col}` : ""}"></i>`).join("");
};
// rows: [{dim?, label, now, past:{year: value}, title, n, click}] -> compare 'now' with every selected past year; change shown against the lead year
function sharePlot(rows, { gridMax } = {}) {
  rows = rows.filter((r) => r.now != null);
  if (!rows.length) return "";
  const years = [...S.years].sort();
  const vals = rows.flatMap((r) => [r.now, ...years.map((y) => r.past?.[y]?.share ?? r.past?.[y])].filter((v) => typeof v === "number"));
  const { max, step } = niceMax(gridMax ?? Math.max(...vals) * 1.05);
  const pv = (r, y) => { const c = r.past?.[y]; return typeof c === "number" ? c : c?.share; };
  let h = "", dim = null;
  for (const r of rows) {
    if (r.dim !== dim) { dim = r.dim; h += `<div class="dp-dim${r.own ? " own" : ""}">${esc(r.dimLabel ?? r.dim)}${r.own ? " <small>own categories</small>" : ""}</div>`; }
    const ly = leadYear((y) => pv(r, y) != null);
    const ch = ly ? r.now - pv(r, ly) : null;
    const tip = `${r.label}: ${f1(r.now)}% now` + years.filter((y) => pv(r, y) != null).map((y) => `, ${f1(pv(r, y))}% in ${y}`).join("");
    const pts = [...years.map((y) => ({ v: pv(r, y), cls: "past", col: YCOL[y] })), { v: r.now, cls: "now" }];
    const lab = r.click ? `<button type="button" class="dp-lab ev-name" data-st="${esc(r.click)}">${esc(r.label)}</button>` : `<span class="dp-lab">${esc(r.label)}</span>`;
    h += `<div class="dp-row" title="${esc(tip)}">${lab}<span class="dp-t" style="--n:${max / step}">${dotsHTML(pts, 0, max)}</span><span class="dp-v"><b>${f1(r.now)}%</b>${ch == null ? "" : `<small class="${ch >= 0 ? "up" : "dn"}">${sg(ch)}</small>`}</span></div>`;
  }
  return `<div class="dp">${h}${axisHTML(max, step)}</div>`;
}
function keyHTML(items) {
  return `<div class="ev-key">${items.map(([c, t, kind]) => `<span><i class="mt-k ${kind ?? ""}" style="--c:${c}"></i>${esc(t)}</span>`).join("")}</div>`;
}
const hasPast = (rows, y) => rows.some((r) => (r.past?.[y]?.share ?? r.past?.[y]) != null);
const shareKey = (rows) => keyHTML([["#1b1b1b", "2026 so far"], ...[...S.years].sort().filter((y) => hasPast(rows, y)).map((y) => [YCOL[y], String(y), "hollow"])]);
const lead = (has) => leadYear(has);
const baseNote = (e) => {
  const bs = e.baselines ?? {};
  const t = [...S.years].sort().filter((y) => bs[y]).map((y) => `${y}: ${BASIS[bs[y].basis] ?? bs[y].basis}${bs[y].note ? ` (${bs[y].note})` : ""}`);
  return t.length ? t.join(" · ") : "";
};

// ------------------------------------------------------------------------------------------------------ overview
function drawBase() {
  const ys = S.ev.years;
  $("#base-tog").innerHTML = ys.slice().sort((a, b) => (a.type === b.type ? a.year - b.year : a.type === "midterm" ? -1 : 1)).map((y) => `<button type="button" data-y="${y.year}" aria-pressed="${S.years.has(y.year)}" title="${y.type === "midterm" ? "Midterm" : "Presidential"} year">${y.year}</button>`).join("");
  $("#base-tog").querySelectorAll("button").forEach((b) => (b.onclick = () => {
    const y = +b.dataset.y;
    if (S.years.has(y) && S.years.size === 1) return;
    S.years.has(y) ? S.years.delete(y) : S.years.add(y);
    drawBase(); drawOverview(); drawPanel(); drawCompare();
  }));
}
function drawOverview() {
  // early vote: the cells with the biggest move against the lead year's same-day count
  const ly = lead(() => true);
  const cells = [];
  for (const e of S.ev.states) {
    const bl = e.baselines?.[ly];
    if (!bl || bl.basis !== "same day") continue;
    for (const [k, c] of Object.entries(e.groups)) {
      const p = c.past?.[ly];
      if (!p) continue;
      const [dim, grp] = [k.split(":")[0], k.slice(k.indexOf(":") + 1)];
      cells.push({ dim: "all", dimLabel: "", label: `${grp} · ${e.state.toUpperCase()}`, now: c.share, past: c.past, click: e.state, d: c.share - p.share });
    }
  }
  cells.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
  const top = cells.slice(0, 8);
  $("#ov-ev-sub").textContent = top.length ? `Share of ballots cast so far, against the same day before Election Day in ${ly}; the ${top.length} biggest moves across ${new Set(cells.map((c) => c.click)).size} states with a same-day count.` : `No state has a same-day ${ly} breakdown.`;
  $("#ov-ev").innerHTML = top.length ? shareKey(top) + sharePlot(top) : "";
  $("#ov-ev").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => select(b.dataset.st, true)));

  // registration: change since the lead year in (D share − R share) of the rolls, in points
  const ry = lead((y) => Object.values(S.reg.states).some((v) => regAt(v, y)));
  const rows = [];
  for (const [st, v] of Object.entries(S.reg.states)) {
    const a = regAt(v, ry), b = v.series[v.series.length - 1];
    const sh = (s) => { const t = s.total ?? ["D", "R", "O", "U"].reduce((x, k) => x + (s[k] ?? 0), 0); return s.D != null && s.R != null && t ? 100 * (s.D - s.R) / t : null; };
    if (!a || a === b || sh(a) == null || sh(b) == null) continue;
    rows.push({ st: st.toLowerCase(), v: sh(b) - sh(a), a, b });
  }
  rows.sort((x, y) => y.v - x.v);
  const m = Math.max(1, ...rows.map((r) => Math.abs(r.v)));
  $("#ov-reg-sub").textContent = rows.length ? `Change in points from ${ry} to the latest report. Right of centre: Democratic share grew faster than Republican; left: the reverse.` : "No registration reports loaded.";
  $("#ov-reg").innerHTML = rows.length ? `<div class="rb">${rows.map((r) => `<div class="rb-row" title="${esc(`${stateName(r.st)}: ${sg(r.v)} points, ${r.a.date} to ${r.b.date}`)}"><button type="button" class="rb-lab ev-name" data-st="${r.st}">${esc(r.st.toUpperCase())}</button><span class="rb-t"><i class="${r.v >= 0 ? "d" : "r"}" style="${r.v >= 0 ? "left:50%" : "right:50%"};width:${(50 * Math.abs(r.v) / m).toFixed(1)}%"></i></span><b>${sg(r.v)}</b></div>`).join("")}</div>` : "";
  $("#ov-reg").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => select(b.dataset.st, true)));
}
// the report nearest to a past election day (within 45 days)
function regAt(v, year) {
  const t = Date.parse(ELECTION[year]);
  let best = null, bd = 45 * 864e5;
  for (const s of v.series) { const d = Math.abs(Date.parse(s.date) - t); if (d <= bd) { bd = d; best = s; } }
  return best;
}

// ------------------------------------------------------------------------------------------------------ picker
const hasEv = (st) => !!evOf(st);
function drawPicker() {
  const by = new Map(S.ctx.states.map((s) => [s.st, s]));
  $("#mt-list").innerHTML = `<p class="mt-ln"><button type="button" class="mt-st${S.st === "us" ? " sel" : ""}" data-st="us">United States <small>polls, issues</small></button></p>` +
    S.ctx.tiers.map((t) => {
      const ss = S.ctx.states.filter((s) => s.tier === t.id);
      if (!ss.length) return "";
      const sub = t.lo == null ? "" : t.id === "far" ? "" : `${Math.round(100 * t.lo)}–${Math.round(100 * t.hi)}%`;
      return `<div class="mt-tier"><h3>${esc(t.label)} <small>${ss.length} states${sub ? `, Democratic-side chance ${sub}` : ""}</small></h3><div class="mt-sts">${ss.map((s) => {
        const r = s.races[0];
        const tip = r ? s.races.map((x) => `${x.office === "senate" ? "Senate" : "Governor"} ${Math.round(100 * x.p)}%`).join(", ") : "no Senate or governor race";
        return `<button type="button" class="mt-st${S.st === s.st ? " sel" : ""}" data-st="${s.st}" title="${esc(`${s.name}: ${tip}`)}">${hasEv(s.st) ? '<i class="mt-has" aria-label="early vote by group"></i>' : ""}${esc(s.name)}</button>`;
      }).join("")}</div></div>`;
    }).join("") + `<p class="note mt-ln">Tiers use the statewide race closest to 50% (mean of Polymarket and Kalshi, ${esc(S.ctx.as_of.slice(0, 10))}). <span class="mt-has-k"><i class="mt-has"></i> early vote by group published</span></p>`;
  $("#mt-list").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => select(b.dataset.st, false)));
  $("#mt-legend").innerHTML = S.ctx.tiers.filter((t) => S.ctx.states.some((s) => s.tier === t.id)).map((t) => `<span><i style="background:${TIER_COL[t.id]}"></i>${esc(t.label)}</span>`).join("");
  document.querySelectorAll("#mt-map path[data-st]").forEach((p) => p.classList.toggle("sel", p.dataset.st === S.st));
  void by;
}
async function drawMap() {
  S.geo ??= await getJSON("geo/us_regions.topo.json", { root: STATIC });
  const fc = topojson.feature(S.geo, S.geo.objects.regions);
  const host = $("#mt-map"), w = Math.max(280, host.clientWidth || 520), h = Math.round(w * 0.62);
  const path = d3.geoPath(d3.geoAlbersUsa().fitSize([w, h], fc));
  const by = new Map(S.ctx.states.map((s) => [s.st, s]));
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`);
  const txt = (s) => `${s.name}${s.races[0] ? `: ${s.races.map((x) => `${x.office === "senate" ? "Senate" : "Governor"} ${Math.round(100 * x.p)}%`).join(", ")}` : ""}`;
  svg.append("g").selectAll("path").data(fc.features).join("path").attr("class", "ev-st").attr("d", path).attr("data-st", (f) => f.id)
    .attr("fill", (f) => TIER_COL[by.get(f.id)?.tier ?? "none"]).attr("tabindex", 0).attr("role", "button").attr("aria-label", (f) => txt(by.get(f.id)))
    .on("click", (_, f) => select(f.id, false)).on("keydown", (e, f) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(f.id, false); } })
    .append("title").text((f) => txt(by.get(f.id)));
  const dc = fc.features.find((f) => f.id === "dc");
  if (dc) { const [x, y] = path.centroid(dc); svg.append("circle").attr("class", "ev-dc").attr("cx", x).attr("cy", y).attr("r", 6).attr("fill", TIER_COL[by.get("dc")?.tier ?? "none"]).on("click", () => select("dc", false)).append("title").text(txt(by.get("dc"))); }
  host.replaceChildren(svg.node());
  drawPicker();
}

// ------------------------------------------------------------------------------------------------------ state panel
function evRows(e) {
  const rows = [];
  for (const t of S.ev.taxonomy) for (const g of t.groups) {
    const c = e.groups[`${t.dim}:${g}`];
    if (c) rows.push({ dim: t.dim, dimLabel: t.label, label: g, now: c.share, past: c.past, reg: c.reg_share });
  }
  for (const d of S.ev.own_dims ?? []) {
    const base = S.ev.taxonomy.find((t) => t.dim === d.dim.split("_")[0]);
    if (base && base.groups.every((g) => e.groups[`${base.dim}:${g}`])) continue;   // the state already has the full harmonised dim
    for (const g of d.groups) {
    const c = e.own?.[`${d.dim}:${g}`];
    if (c) rows.push({ dim: d.dim, dimLabel: d.label, own: true, label: g, now: c.share, past: c.past });
    }
  }
  return rows;
}
function secEarly(st) {
  const e = evOf(st);
  if (!e) return "";
  const rows = evRows(e);
  if (!rows.length) return "";
  const src = e.source.startsWith("UF") ? UF_LABEL : e.source;
  const cov = [...new Set(Object.entries(e.coverage ?? {}).filter(([, v]) => v != null && v < 90).map(([k, v]) => `${k.split("_")[0]} recorded for ${Math.round(v)}% of ballots`))];
  const bn = baseNote(e);
  return `<figure class="mt-fig"><h3>Early vote by group</h3><p class="chart-sub">${int(e.total)} ballots counted through ${esc(dayShort(e.as_of))}. Each group's share of the ballots that record it. ${e.url ? link(e.url, src) : esc(src)}${cov.length ? `; ${esc(cov.join(", "))}` : ""}.</p>` +
    shareKey(rows) + sharePlot(rows) + (bn ? `<p class="mt-bl">${esc(bn)}. Change shown against ${lead((y) => rows.some((r) => r.past?.[y])) ?? "–"}.</p>` : "") + `</figure>`;
}
function regTrend(st) {
  const v = S.reg.states[st.toUpperCase()];
  if (!v || v.series.length < 2) return "";
  const ch = v.change;
  const t = ["D", "R", "O"].filter((k) => ch[k]).map((k) => `${{ D: "Democratic", R: "Republican", O: "other" }[k]} ${sg(ch[k].pct)}%`).join(", ");
  return `<div class="mt-sub"><p class="chart-title">Share of registered voters by party</p><p class="chart-sub">${esc(v.base_date)} to ${esc(v.latest_date)}: ${esc(t)}. ${link(v.url, "Official report")}. Other = unaffiliated and minor parties.</p>` +
    keyHTML([[COL.D, "Democratic", "line"], [COL.R, "Republican", "line"], [COL.O, "Other", "line"]]) + `<div class="mt-line" id="reg-line" data-st="${st}"></div></div>`;
}
function drawLine() {
  const host = $("#reg-line");
  if (!host) return;
  const v = S.reg.states[host.dataset.st.toUpperCase()];
  const pts = v.series.filter((s) => s.D != null && s.R != null).map((s) => { const t = s.total ?? ["D", "R", "O", "U"].reduce((x, k) => x + (s[k] ?? 0), 0); return { d: new Date(s.date), D: 100 * s.D / t, R: 100 * s.R / t, O: 100 * (t - s.D - s.R) / t, date: s.date }; });
  if (pts.length < 2) { host.innerHTML = ""; return; }
  const w = Math.max(300, host.clientWidth || 600), h = w < 520 ? 190 : 230, m = { l: 40, r: 12, t: 8, b: 26 };
  const ext = d3.extent(pts, (p) => p.d), yMax = Math.ceil(d3.max(pts, (p) => Math.max(p.D, p.R, p.O)) / 5) * 5, yMin = Math.floor(d3.min(pts, (p) => Math.min(p.D, p.R, p.O)) / 5) * 5;
  const X = d3.scaleTime(ext, [m.l, w - m.r]), Y = d3.scaleLinear([yMin, yMax], [h - m.b, m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img").attr("aria-label", "Share of registered voters by party over time");
  svg.append("g").attr("class", "grid").selectAll("line").data(Y.ticks(4)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", Y).attr("y2", Y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(X).ticks(w < 520 ? 4 : 7).tickFormat(d3.timeFormat("%b %Y")).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(Y).ticks(4).tickFormat((x) => `${x}%`).tickSize(0));
  for (const y of [2018, 2020, 2022, 2024]) { const x = X(new Date(ELECTION[y])); if (x > m.l && x < w - m.r) { svg.append("line").attr("x1", x).attr("x2", x).attr("y1", m.t).attr("y2", h - m.b).attr("stroke", YCOL[y]).attr("stroke-dasharray", "3 3"); svg.append("text").attr("x", x + 3).attr("y", m.t + 9).text(y); } }
  for (const k of ["D", "R", "O"]) {
    svg.append("path").attr("fill", "none").attr("stroke", COL[k]).attr("stroke-width", 2).attr("d", d3.line().x((p) => X(p.d)).y((p) => Y(p[k]))(pts));
    svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (p) => X(p.d)).attr("cy", (p) => Y(p[k])).attr("r", 2.4).attr("fill", COL[k]).append("title").text((p) => `${p.date}: ${k === "D" ? "Democratic" : k === "R" ? "Republican" : "other"} ${p[k].toFixed(1)}%`);
  }
  host.replaceChildren(svg.node());
}
function regGroups(st) {
  if (st !== "nc" || !S.mix) return "";
  const rows = S.mix.rows.filter((r) => r.share_now != null).map((r) => ({ dim: r.dim, dimLabel: r.dim_label, own: !S.ev.taxonomy.some((t) => t.dim === r.dim), label: r.group, now: r.share_now, past: Object.fromEntries(Object.entries(r.past ?? {}).map(([y, c]) => [y, c.share])) }));
  if (!rows.length) return "";
  return `<div class="mt-sub"><p class="chart-title">Who is registered</p><p class="chart-sub">Share of registered voters by group, ${esc(S.mix.asof_now)}. ${link(S.mix.url, "Official report")}.</p>${shareKey(rows)}${sharePlot(rows)}</div>`;
}
function secReg(st) {
  const a = regGroups(st), b = S.reg.states[st.toUpperCase()] ? regTrend(st) : "";
  return a || b ? `<figure class="mt-fig"><h3>Registration</h3>${b}${a}</figure>` : "";
}

// polls by group -------------------------------------------------------------------------------------
const pollKey = (r) => `${r.pollster}|${r.field_start}|${r.field_end}`;
const pollLabel = (r) => `${r.pollster}, ${dayShort(r.field_end)}`;
const raceName = (k) => (k === "generic" ? "Generic ballot" : `${k.slice(0, 2).toUpperCase()} ${{ sen: "Senate", gov: "governor" }[k.slice(3)] ?? k.slice(3)}`);
function pollChart(rs) {
  const p = rs[0];
  const taxOrder = S.ev.taxonomy.flatMap((t) => t.groups.map((g) => `${t.dim}:${g}`));
  const taxLab = Object.fromEntries(S.ev.taxonomy.map((t) => [t.dim, t.label]));
  const mapped = rs.filter((r) => r.tax).sort((a, b) => taxOrder.indexOf(a.tax) - taxOrder.indexOf(b.tax));
  const own = rs.filter((r) => !r.tax);
  const all = rs.filter((r) => r.dem_pct != null && r.rep_pct != null);
  if (!all.length) return "";
  const lo = Math.max(0, Math.floor((Math.min(...all.map((r) => Math.min(r.dem_pct, r.rep_pct) - (r.moe_group ?? 0))) - 1) / 10) * 10);
  const hi = Math.min(100, Math.ceil((Math.max(...all.map((r) => Math.max(r.dem_pct, r.rep_pct) + (r.moe_group ?? 0))) + 1) / 10) * 10);
  const x = (v) => (100 * (v - lo) / (hi - lo)).toFixed(2);
  const row = (r, label, dimHead) => {
    const small = r.group_n_used != null && r.group_n_used < 100;
    const wh = (v, c) => (r.moe_group == null ? "" : `<i class="pp-w" style="left:${x(Math.max(lo, v - r.moe_group))}%;width:${(x(Math.min(hi, v + r.moe_group)) - x(Math.max(lo, v - r.moe_group))).toFixed(2)}%;--c:${c}"></i>`);
    const tip = `${r.group}: ${p.dem_name ?? "Democratic"} ${f1(r.dem_pct)}%, ${p.rep_name ?? "Republican"} ${f1(r.rep_pct)}%${r.moe_group != null ? `, margin of error ±${f1(r.moe_group)}` : ""}${r.group_n_used != null ? `, ${int(r.group_n_used)} interviews` : ""}`;
    return `${dimHead ?? ""}<div class="dp-row" title="${esc(tip)}"><span class="dp-lab">${esc(label)} <small class="${small ? "mt-small" : ""}">${r.group_n_used == null ? "" : `n=${r.group_n_est ? "~" : ""}${int(r.group_n_used)}`}</small></span><span class="dp-t">${wh(r.dem_pct, COL.D)}${wh(r.rep_pct, COL.R)}<i class="dp-d" style="left:${x(r.dem_pct)}%;--c:${COL.D}"></i><i class="dp-d" style="left:${x(r.rep_pct)}%;--c:${COL.R}"></i></span><span class="dp-v"><b>${f1(r.dem_pct).replace(/\.0$/, "")}–${f1(r.rep_pct).replace(/\.0$/, "")}</b><small class="${r.margin >= 0 ? "up" : "dn"}">${sg(r.margin, 0)}</small></span></div>`;
  };
  let h = "", dim = null;
  for (const r of mapped) { const d = r.tax.split(":")[0]; h += row(r, r.tax.slice(r.tax.indexOf(":") + 1), d !== dim ? `<div class="dp-dim">${esc(taxLab[d])}</div>` : ""); dim = d; }
  dim = null;
  for (const r of own) { h += row(r, r.group, r.dim !== dim ? `<div class="dp-dim own">${esc({ race: "Race / ethnicity", age: "Age", education: "Education" }[r.dim] ?? r.dim)} <small>the poll's own categories</small></div>` : ""); dim = r.dim; }
  const ticks = []; for (let v = lo; v <= hi; v += 10) ticks.push(v);
  const axis = `<div class="dp-axis"><span></span><span class="dp-ticks">${ticks.map((v) => `<i style="left:${x(v)}%">${v}</i>`).join("")}</span><span></span></div>`;
  return `<div class="dp pp">${h}${axis}</div>`;
}
function secPolls(st) {
  const rows = (S.ct?.rows ?? []).filter((r) => (st === "us" ? r.race === "generic" : r.race.startsWith(`${st}-`)));
  if (!rows.length) return "";
  const races = [...new Set(rows.map((r) => r.race))].sort();
  const race = races.includes(S.poll[st]?.race) ? S.poll[st].race : races[0];
  const polls = [...new Map(rows.filter((r) => r.race === race).map((r) => [pollKey(r), r])).values()].sort((a, b) => b.field_end.localeCompare(a.field_end));
  const pk = polls.some((p) => pollKey(p) === S.poll[st]?.poll) ? S.poll[st].poll : pollKey(polls[0]);
  const rs = rows.filter((r) => r.race === race && pollKey(r) === pk);
  const p = rs[0];
  const sel = (id, opts, cur) => `<select id="${id}">${opts.map(([v, t]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const pick = (races.length > 1 ? `<label class="select">Race ${sel("po-race", races.map((r) => [r, raceName(r)]), race)}</label> ` : "") + (polls.length > 1 ? `<label class="select">Poll ${sel("po-poll", polls.map((q) => [pollKey(q), pollLabel(q)]), pk)}</label>` : "");
  return `<figure class="mt-fig"><h3>Poll results by group</h3>${pick ? `<div class="ev-pick">${pick}</div>` : ""}<p class="chart-sub">${link(p.source_url, p.pollster)}, ${esc(dayShort(p.field_start))} – ${esc(dayShort(p.field_end))}, ${int(p.sample_n)} ${esc(p.population)}; ${esc(raceName(race))}. ${rs.some((r) => r.moe_group != null) ? "Bars: each group's margin of error; n in orange = under 100 interviews." : "This poll gives no group sample sizes, so no margins of error."} Right: Democratic–Republican %, then the gap.</p>` +
    keyHTML([[COL.D, p.dem_name ?? "Democratic"], [COL.R, p.rep_name ?? "Republican"]]) + pollChart(rs) + `</figure>`;
}

// issues ---------------------------------------------------------------------------------------------
function issueSets(st) {
  const sets = [];
  const polls = S.issues?.polls ?? [];
  const geo = st === "us" ? "national" : st.toUpperCase();
  const by = new Map();
  for (const r of polls.filter((r) => r.geography === geo)) { const k = [r.pollster, r.field_end, r.question].join("|"); (by.get(k) ?? by.set(k, []).get(k)).push(r); }
  for (const [k, rs] of by) sets.push({ id: k, label: `${rs[0].pollster}, ${dayShort(rs[0].field_end)}`, sort: rs[0].field_end, rs: rs.map((r) => ({ name: r.issue, pct: r.pct })), src: rs[0].source_url, who: `${rs[0].pollster}, ${dayShort(rs[0].field_start)} – ${dayShort(rs[0].field_end)}${rs[0].n ? `, ${int(rs[0].n)}` : ""} ${rs[0].population}`, q: rs[0].question, mean: /mean score/i.test(rs[0].question) });
  if (st === "us" && S.issues?.gallup?.length) {
    const g = S.issues.gallup, dates = [...new Set(g.map((r) => r.date_label))], last = dates[dates.length - 1];
    const rs = g.filter((r) => r.date_label === last);
    sets.push({ id: "gallup", label: `Gallup most important problem, ${last}`, sort: "9999", rs: rs.map((r) => ({ name: r.category.replace(/_/g, " "), pct: r.pct })), src: rs[0].source_url, who: `Gallup, ${last}`, q: "% of US adults naming the problem (open-ended)" });
  }
  return sets.sort((a, b) => b.sort.localeCompare(a.sort));
}
function secIssues(st) {
  const sets = issueSets(st);
  if (!sets.length) return "";
  const cur = sets.find((s) => s.id === S.iss[st]) ?? sets[0];
  const rs = cur.rs.filter((r) => r.pct != null).sort((a, b) => b.pct - a.pct);
  const max = cur.mean ? 10 : Math.max(...rs.map((r) => r.pct)), unit = cur.mean ? "" : "%";
  const pick = sets.length > 1 ? `<div class="ev-pick"><label class="select">Poll <select id="is-poll">${sets.map((s) => `<option value="${esc(s.id)}"${s.id === cur.id ? " selected" : ""}>${esc(s.label)}</option>`).join("")}</select></label></div>` : "";
  return `<figure class="mt-fig"><h3>Issues</h3>${pick}<p class="chart-sub">${link(cur.src, cur.who)}. ${esc(cur.q)}.</p><div class="ib">${rs.map((r) => `<div class="ib-row"><span class="ib-l">${esc(r.name)}</span><span class="ib-t"><i style="width:${(100 * r.pct / max).toFixed(1)}%"></i></span><b>${f1(r.pct).replace(/\.0$/, "")}${unit}</b></div>`).join("")}</div></figure>`;
}

const sections = (st) => [secEarly(st), secReg(st), secPolls(st), secIssues(st)];
function marketChips(st) {
  const s = S.ctx.states.find((x) => x.st === st);
  if (!s?.races.length) return "";
  return `<div class="chips mt-mk">${s.races.map((r) => `<span class="chip"><small>${r.office === "senate" ? "Senate" : "Governor"}${r.special ? " (special)" : ""}</small><b>${Math.round(100 * r.p)}%</b><i>${esc((r.side ?? "").split(" ").slice(-1)[0])} (${esc(r.party ?? "D")}), market</i></span>`).join("")}</div>`;
}
function drawPanel() {
  const st = S.st;
  const secs = sections(st).filter(Boolean);
  $("#state-panel").dataset.st = st;
  $("#state-panel").innerHTML = `<h2 id="stn">${esc(stateName(st))}</h2>${st === "us" ? "" : marketChips(st)}` + (secs.join("") || `<p class="chart-sub">No group data, registration reports or polls for this state yet.</p>`);
  drawLine();
  const rebind = (id, key, extra) => { const el = $(id); if (el) el.onchange = () => { extra(el.value); drawPanel(); }; };
  rebind("#po-race", "race", (v) => (S.poll[st] = { race: v }));
  rebind("#po-poll", "poll", (v) => (S.poll[st] = { race: $("#po-race")?.value ?? S.poll[st]?.race, poll: v }));
  rebind("#is-poll", "iss", (v) => (S.iss[st] = v));
  $("#state-panel").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => select(b.dataset.st, true)));
}
const richness = (st) => sections(st).filter(Boolean).length * 1e9 + (evOf(st)?.total ?? 0);
function select(st) {
  if (st !== "us" && !S.ctx.states.some((s) => s.st === st)) st = S.st;
  S.st = st;
  if (location.hash !== `#st=${st}`) history.replaceState(null, "", `#st=${st}`);
  drawPicker(); drawPanel();
}

// ------------------------------------------------------------------------------------------------------ compare states
function drawCompare() {
  const has = (e, k) => e.groups[k] ?? e.own?.[k];
  const opts = [];
  for (const t of S.ev.taxonomy) {
    const g = t.groups.filter((x) => S.ev.states.filter((e) => e.groups[`${t.dim}:${x}`]).length >= 2);
    if (g.length) opts.push([t.label, g.map((x) => [`${t.dim}:${x}`, x])]);
  }
  for (const d of S.ev.own_dims ?? []) {
    const g = d.groups.filter((x) => S.ev.states.filter((e) => e.own?.[`${d.dim}:${x}`]).length >= 2);
    if (g.length) opts.push([`${d.label} (own categories)`, g.map((x) => [`${d.dim}:${x}`, x])]);
  }
  const sel = $("#cmp-group");
  const count = (k) => S.ev.states.filter((e) => has(e, k)).length;
  const all = opts.flatMap(([, g]) => g.map(([k]) => k));
  const tax = opts.filter(([l]) => !l.endsWith("(own categories)")).flatMap(([, g]) => g.map(([k]) => k));
  const cur = sel.value && all.includes(sel.value) ? sel.value : (tax.length ? tax : all).sort((a, b) => count(b) - count(a))[0];
  sel.innerHTML = opts.map(([l, g]) => `<optgroup label="${esc(l)}">${g.map(([k, t]) => `<option value="${esc(k)}"${k === cur ? " selected" : ""}>${esc(t)}</option>`).join("")}</optgroup>`).join("");
  sel.onchange = drawCompare;
  if (!cur) { $("#cmp-grid").innerHTML = ""; return; }
  const ss = S.ev.states.filter((e) => has(e, cur)).map((e) => ({ e, c: has(e, cur) })).sort((a, b) => b.c.share - a.c.share);
  const years = [...S.years].sort();
  const pv = (c, y) => c.past?.[y]?.share;
  const vals = ss.flatMap(({ c }) => [c.share, ...years.map((y) => pv(c, y))].filter((v) => v != null));
  const { max, step } = niceMax(Math.max(...vals) * 1.05);
  const own = !S.ev.taxonomy.some((t) => t.dim === cur.split(":")[0]);
  const ownDim = S.ev.own_dims?.find((d) => d.dim === cur.split(":")[0]);
  $("#cmp-sub").textContent = `${cur.slice(cur.indexOf(":") + 1)}${own ? ` (${ownDim?.label ?? "own categories"}; only states that use the same categories)` : ""}: share of ballots cast so far in ${ss.length} states, one scale for all.`;
  $("#cmp-grid").innerHTML = keyHTML([["#1b1b1b", "2026 so far"], ...years.filter((y) => ss.some(({ c }) => pv(c, y) != null)).map((y) => [YCOL[y], String(y), "hollow"])]).replace('class="ev-key"', 'class="ev-key mt-cmpkey"') +
    `<div class="cmp-tiles">${ss.map(({ e, c }) => {
      const ly = leadYear((y) => pv(c, y) != null), ch = ly ? c.share - pv(c, ly) : null;
      const pts = [...years.map((y) => ({ v: pv(c, y), cls: "past", col: YCOL[y] })), { v: c.share, cls: "now" }];
      const tip = `${e.name}: ${f1(c.share)}%${years.filter((y) => pv(c, y) != null).map((y) => `, ${f1(pv(c, y))}% in ${y}`).join("")}`;
      return `<div class="cmp-tile${e.state === S.st ? " sel" : ""}" title="${esc(tip)}"><button type="button" class="ev-name" data-st="${e.state}">${esc(e.name)}</button><b>${f1(c.share)}%${ch == null ? "" : ` <small class="${ch >= 0 ? "up" : "dn"}">${sg(ch)}</small>`}</b><span class="dp-t" style="--n:${max / step}">${dotsHTML(pts, 0, max)}</span><span class="cmp-sc"><i>0</i><i>${max}%</i></span></div>`;
    }).join("")}</div>`;
  $("#cmp-grid").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => select(b.dataset.st, true)));
}

// ------------------------------------------------------------------------------------------------------ mapping table
function drawMapping() {
  const m = S.ev.mapping ?? [];
  $("#tx-sub").textContent = "Every state, source and year is put into the same groups. A source's own categories are mapped only where they add up exactly to a group; otherwise they are shown under their own labels and never mixed into a comparison.";
  $("#tx-table").tHead.innerHTML = `<tr><th>Source</th><th>Group</th><th>Taken from</th><th>Status</th></tr>`;
  $("#tx-table").tBodies[0].innerHTML = m.map((r) => `<tr><td>${esc(r.source)}</td><td>${r.group ? esc(`${r.dim}: ${r.group}`) : esc(r.dim)}</td><td>${esc(r.from)}</td><td>${esc(r.status)}</td></tr>`).join("");
}

async function main() {
  const [ctx, ev, mix, reg, issues, ct] = await Promise.all(["states.json", "ev_groups.json", "reg_nc_mix.json", "registration.json", "issues.json", "crosstabs.json"].map((f) => ctxGet(f, f !== "states.json")));
  Object.assign(S, { ctx, ev, mix, reg: reg ?? { states: {} }, issues, ct });
  S.ev ??= { taxonomy: [], own_dims: [], years: [], states: [], mapping: [] };
  S.years = new Set(S.ev.years.filter((y) => y.type === "midterm").map((y) => y.year));
  const throughs = S.ev.states.map((e) => e.as_of).sort();
  $("#mt-asof").textContent = throughs.length ? `Early vote counted through ${dayLong(throughs[throughs.length - 1])}; ${S.ev.states.length} states publish a breakdown by group.` : "Early-vote files not available.";
  drawBase();
  drawOverview();
  const hs = /#st=([a-z]{2})\b/.exec(location.hash)?.[1];
  S.st = hs && (hs === "us" || S.ctx.states.some((s) => s.st === hs)) ? hs : S.ctx.states.map((s) => s.st).sort((a, b) => richness(b) - richness(a))[0];
  await drawMap();
  drawPanel();
  drawCompare();
  drawMapping();
  $("#mt-foot").innerHTML = `Counts: official state election offices; where a state publishes no file, ${esc(UF_LABEL)}. Registration: official state reports. Polls: published crosstabs, linked on each chart. Prices: Polymarket and Kalshi. Group results are counts of ballots or registrations, not how anyone voted. Each number on this page is generated from the data files. Charts: D3.`;
  window.addEventListener("hashchange", () => { const h = /#st=([a-z]{2})\b/.exec(location.hash)?.[1]; if (h && h !== S.st) select(h, false); });
  let t; window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { drawLine(); }, 150); });
  window.__mtReady = true;
}
main().catch((e) => window.__showLoadError?.(e) ?? console.error(e));
