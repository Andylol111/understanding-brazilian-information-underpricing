// US early-vote page: reads data/ev2026/{index,state_<st>,counties_<st>}.json (analysis/us_ev_site.py). Every number shown comes from
// those files; this module only formats and draws them.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, STATIC } from "./common.js";

const $ = (s) => document.querySelector(s);
const COLORS = { total: "#1b1b1b", mail: "#6a7f99", inperson: "#c4501b", D: "#1f5fbf", R: "#c8102e", U: "#8a8578", other: "#b9b5aa", now: "#1b1b1b" };
const CYCLE_COLORS = { 2018: "#8a6fb0", 2020: "#2f8a6c", 2022: "#c49a1b", 2024: "#6a7f99" };
const STATUS = { collected: "collected", blocked: "blocked", not_published: "not published" };
const MAP_COLORS = { county: "#2f8a6c", state: "#86bfa7", partial: "#c9dfd3", blocked: "#d9743a", not_published: "#cfcbc0" };

const int = d3.format(",");
const si = (v) => (v == null ? "–" : Math.abs(v) >= 1e4 ? d3.format(".3~s")(v).replace("G", "B") : int(v));
const num = (v) => (v == null ? "–" : int(Math.round(v)));
const pt = (v) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)} pt`);
const pc = (v) => (v == null ? "–" : `${v.toFixed(1)}%`);
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dayShort = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}` : "–");
const dayLong = (iso) => { const d = new Date(`${iso}T12:00:00Z`); return `${WD[d.getUTCDay()]} ${dayShort(iso)}`; };
const stamp = (iso) => (iso ? `${dayShort(iso.slice(0, 10))}, ${iso.slice(11, 16)} UTC` : "–");
const dbOf = (iso, ed) => Math.round((Date.parse(`${ed}T00:00:00Z`) - Date.parse(`${iso}T00:00:00Z`)) / 864e5);
const dateAt = (dbDays, ed) => new Date(Date.parse(`${ed}T12:00:00Z`) - dbDays * 864e5).toISOString().slice(0, 10);

const S = { index: null, st: null, cache: new Map(), sort: { key: "name", dir: 1 }, filter: "all", geo: null, cty: { metric: null, sort: { key: "total", dir: -1 } }, ctyData: null };
const get = (name, exists = true) => (!exists ? null : S.cache.has(name) ? S.cache.get(name) : (S.cache.set(name, getJSON(`ev2026/${name}.json`, { optional: true })), S.cache.get(name)));

// ------------------------------------------------------------------------------------------------------ coverage
const level = (c) => (c.level === "none" ? "–" : c.partial ? "county only" : c.level);
const sortVal = {
  name: (c) => c.name, status: (c) => c.status, level: (c) => level(c), party: (c) => (c.party ? 1 : 0), as_of: (c) => c.as_of ?? "", last: (c) => c.last_fetch ?? "",
  total: (c) => c.total ?? -1, lead: (c) => c.lead ?? -1e9, hist: (c) => c.hist.length,
};
const COLS = [["name", "State"], ["status", "Status"], ["level", "Data level"], ["party", "Party detail"], ["as_of", "Counted through", "n"], ["last", "Last checked (UTC)", "n ev-hide-sm"],
  ["total", "Ballots so far", "n"], ["lead", "D minus R", "n"], ["hist", "Past elections", "n ev-hide-sm"], ["links", "Official source", "ev-hide-sm"]];

function drawCoverage() {
  const cs = S.index.states, n = (s) => cs.filter((c) => c.status === s).length;
  const county = cs.filter((c) => c.status === "collected" && c.level === "county" && !c.partial).length;
  const party = cs.filter((c) => c.status === "collected" && c.party).length;
  $("#cov-sum").textContent = `${n("collected")} of ${cs.length} states and DC have official counts we collect (${county} down to the county, ${party} with party registration); ` +
    `${n("blocked")} are blocked to our program and ${n("not_published")} publish no usable counts.`;
  const f = $("#cov-filter");
  f.replaceChildren(...[["all", `All ${cs.length}`], ["collected", `Collected ${n("collected")}`], ["blocked", `Blocked ${n("blocked")}`], ["not_published", `Not published ${n("not_published")}`]].map(([k, t]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.setAttribute("aria-pressed", String(S.filter === k));
    b.onclick = () => { S.filter = k; drawCoverage(); }; return b;
  }));
  const th = $("#cov-table thead");
  th.innerHTML = `<tr>${COLS.map(([k, t, cls]) => `<th class="${cls ?? ""}" ${S.sort.key === k ? `aria-sort="${S.sort.dir > 0 ? "ascending" : "descending"}"` : ""}>${k === "links" ? t : `<button type="button" data-k="${k}">${t}</button>`}</th>`).join("")}</tr>`;
  th.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.sort = { key: b.dataset.k, dir: S.sort.key === b.dataset.k ? -S.sort.dir : 1 }; drawCoverage(); }));
  const rows = cs.filter((c) => S.filter === "all" || c.status === S.filter).sort((a, b) => {
    const x = sortVal[S.sort.key](a), y = sortVal[S.sort.key](b);
    return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * S.sort.dir;
  });
  $("#cov-table tbody").innerHTML = rows.map((c) => {
    const why = c.detail.replace(/^(blocked|not published|none published|dashboard-only)\s*[:;]?\s*/i, (m) => (/^(dashboard-only|none published)/i.test(m) ? m : ""));
    const detail = why ? `<span class="ev-detail">${esc(why.length > 150 ? `${why.slice(0, 147)}…` : why)}</span>` : "";
    const stale = c.stale ? ` <span class="ev-detail" style="display:inline">(older than the rest)</span>` : "";
    return `<tr data-st="${c.st}" class="${c.st === S.st ? "sel" : ""}"><td><button type="button" class="ev-name" data-st="${c.st}">${esc(c.name)}</button></td>` +
      `<td><span class="ev-pill s-${c.status}">${STATUS[c.status]}</span>${detail}</td><td>${level(c)}</td><td>${c.status === "collected" ? (c.party ? "yes" : "no") : "–"}</td>` +
      `<td class="n">${dayShort(c.as_of)}${stale}</td><td class="n ev-hide-sm">${stamp(c.last_fetch)}</td><td class="n">${num(c.total)}</td><td class="n">${pt(c.lead)}</td>` +
      `<td class="n ev-hide-sm">${c.hist.length ? c.hist.join(", ") : "–"}</td><td class="ev-links ev-hide-sm">${c.links.slice(0, 2).map((l) => `<a href="${esc(l.url)}" rel="noopener">${esc(l.label)}</a>`).join("")}</td></tr>`;
  }).join("");
  $("#cov-table tbody").querySelectorAll("[data-st]").forEach((el) => el.addEventListener("click", (e) => { if (e.target.closest("a")) return; select(el.dataset.st, true); }));
  const tr = S.index.tracker;
  $("#cov-note").innerHTML = `"Counted through" is the last day the official file covers; "last checked" is when our program last fetched a state's files. "D minus R" is the Democratic minus Republican share of returned ballots by party registration, not a vote count. ` +
    `${tr?.url ? `<a href="${esc(tr.url)}" rel="noopener">${esc(tr.label)}</a>.` : ""}`;
}

async function drawCoverageMap() {
  S.geo ??= await getJSON("geo/us_regions.topo.json", { root: STATIC });
  const fc = topojson.feature(S.geo, S.geo.objects.regions);
  const host = $("#cov-map"), w = Math.max(320, host.clientWidth || 600), h = Math.round(w * 0.62);
  const proj = d3.geoAlbersUsa().fitSize([w, h], fc), path = d3.geoPath(proj);
  const by = new Map(S.index.states.map((c) => [c.st, c]));
  const key = (c) => (c.status !== "collected" ? c.status : c.partial ? "partial" : c.level);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`);
  const txt = (c) => `${c.name}: ${STATUS[c.status]}${c.status === "collected" ? `, ${level(c)} data${c.party ? ", party detail" : ""}, counted through ${dayShort(c.as_of)}` : ""}`;
  svg.append("g").selectAll("path").data(fc.features).join("path").attr("class", (f) => `ev-st${f.id === S.st ? " sel" : ""}`).attr("d", path).attr("data-st", (f) => f.id)
    .attr("fill", (f) => MAP_COLORS[key(by.get(f.id))]).attr("tabindex", 0).attr("role", "button").attr("aria-label", (f) => txt(by.get(f.id)))
    .on("click", (_, f) => select(f.id, true)).on("keydown", (e, f) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(f.id, true); } })
    .append("title").text((f) => txt(by.get(f.id)));
  const dc = fc.features.find((f) => f.id === "dc");
  if (dc) { const [x, y] = path.centroid(dc); svg.append("circle").attr("class", "ev-dc").attr("cx", x).attr("cy", y).attr("r", 6).attr("fill", MAP_COLORS[key(by.get("dc"))]).on("click", () => select("dc", true)).append("title").text(txt(by.get("dc"))); }
  host.replaceChildren(svg.node());
  const have = new Set(S.index.states.map(key));
  $("#cov-legend").innerHTML = [["county", "county data"], ["state", "state totals only"], ["partial", "some counties only"], ["blocked", "blocked"], ["not_published", "not published"]]
    .filter(([k]) => have.has(k)).map(([k, t]) => `<span><i style="background:${MAP_COLORS[k]}"></i>${t}</span>`).join("");
}

// ----------------------------------------------------------------------------------------------------- chart kit
function frame(host, { height = 270, m = { l: 58, r: 14, t: 10, b: 38 }, x, y, yFmt, xTitle }) {
  const w = Math.max(300, host.clientWidth || 640), h = w < 520 ? Math.round(height * 0.85) : height;
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`);
  const X = d3.scaleLinear(x, [m.l, w - m.r]), Y = d3.scaleLinear(y, [h - m.b, m.t]).nice();
  svg.append("g").attr("class", "grid").selectAll("line").data(Y.ticks(5)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", Y).attr("y2", Y);
  const xt = X.ticks(Math.min(10, Math.ceil(Math.abs(x[0] - x[1]) / 5))).filter((v) => Number.isInteger(v));
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(X).tickValues(xt).tickFormat((v) => (v === 0 ? "Day 0" : v)).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(Y).ticks(5).tickFormat(yFmt).tickSize(0));
  svg.append("text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 4).attr("text-anchor", "middle").text(xTitle);
  return { svg, X, Y, w, h, m };
}

function keyHTML(items) {
  return `<div class="ev-key">${items.map(([c, t, bar]) => `<span><i class="${bar ? "bar" : ""}" style="border-color:${c};color:${c}"></i>${esc(t)}</span>`).join("")}</div>`;
}

// series: [{name,color,width,dash,pts:[[daysBefore, value]...]}]; read(day) -> text for the readout line
function lineChart(host, read, { series, x, yFmt, zero, xTitle = "Days before Election Day", height }) {
  const ys = series.flatMap((s) => s.pts.map((p) => p[1])).filter((v) => v != null);
  if (!ys.length) { host.replaceChildren(); return; }
  const y = [Math.min(zero ? Math.min(0, d3.min(ys)) : d3.min(ys), zero ? 0 : Infinity), Math.max(d3.max(ys), zero ? 0 : -Infinity)];
  if (!zero) { const pad = (y[1] - y[0]) * 0.08 || 1; y[0] -= pad; y[1] += pad; } else if (y[0] === y[1]) y[1] += 1;
  const f = frame(host, { x, y, yFmt, xTitle, height });
  if (zero) f.svg.append("line").attr("x1", f.m.l).attr("x2", f.w - f.m.r).attr("y1", f.Y(0)).attr("y2", f.Y(0)).attr("stroke", "#1b1b1b").attr("stroke-width", 1);
  const line = d3.line().defined((p) => p[1] != null).x((p) => f.X(p[0])).y((p) => f.Y(p[1]));
  for (const s of series) {
    const pts = [...s.pts].sort((a, b) => b[0] - a[0]);
    f.svg.append("path").datum(pts).attr("d", line).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width ?? 2).attr("stroke-dasharray", s.dash ?? null).attr("stroke-linejoin", "round");
    if (pts.length < 40) f.svg.append("g").selectAll("circle").data(pts.filter((p) => p[1] != null)).join("circle").attr("cx", (p) => f.X(p[0])).attr("cy", (p) => f.Y(p[1])).attr("r", s.width > 2 ? 3 : 2.2).attr("fill", s.color);
  }
  const rule = f.svg.append("line").attr("y1", f.m.t).attr("y2", f.h - f.m.b).attr("stroke", "#1b1b1b").attr("stroke-width", 0.8).attr("opacity", 0);
  f.svg.append("rect").attr("x", f.m.l).attr("y", f.m.t).attr("width", f.w - f.m.l - f.m.r).attr("height", f.h - f.m.t - f.m.b).attr("fill", "none").attr("pointer-events", "all")
    .on("pointermove", (e) => { const r = e.currentTarget.getBoundingClientRect(), d = Math.round(f.X.invert(f.m.l + ((e.clientX - r.left) / r.width) * (f.w - f.m.l - f.m.r))); rule.attr("x1", f.X(d)).attr("x2", f.X(d)).attr("opacity", 1); read(d); })
    .on("pointerleave", () => { rule.attr("opacity", 0); read(null); });
  host.replaceChildren(f.svg.node());
  return f;
}

// bars: [{x: day of the last day covered, span, parts:[[key, value]...]}]; a span over one day is drawn lighter at the daily average
function barChart(host, read, { bars, colors, x, xTitle = "Days before Election Day" }) {
  const tops = bars.map((b) => d3.sum(b.parts, (p) => Math.max(0, p[1] ?? 0)) / (b.span || 1)), bot = bars.map((b) => d3.sum(b.parts, (p) => Math.min(0, p[1] ?? 0)) / (b.span || 1));
  if (!bars.length) { host.replaceChildren(); return; }
  const f = frame(host, { x: [x[0] + 0.5, x[1] - 0.5], y: [Math.min(0, d3.min(bot)), d3.max(tops) || 1], yFmt: si, xTitle, height: 240 });
  const g = f.svg.append("g");
  bars.forEach((b) => {
    const span = b.span || 1, x0 = f.X(b.x + span - 0.5), x1 = f.X(b.x - 0.5);
    let up = 0, dn = 0;
    for (const [k, v] of b.parts) {
      if (v == null) continue;
      const a = v / span, y0 = a >= 0 ? up : dn, y1 = y0 + a;
      if (a >= 0) up = y1; else dn = y1;
      g.append("rect").attr("x", x0 + 0.5).attr("width", Math.max(1, x1 - x0 - 1)).attr("y", f.Y(Math.max(y0, y1))).attr("height", Math.max(0, Math.abs(f.Y(y0) - f.Y(y1)))).attr("fill", colors[k]).attr("opacity", span > 1 ? 0.55 : 1);
    }
  });
  const rule = f.svg.append("line").attr("y1", f.m.t).attr("y2", f.h - f.m.b).attr("stroke", "#1b1b1b").attr("stroke-width", 0.8).attr("opacity", 0);
  f.svg.append("rect").attr("x", f.m.l).attr("y", f.m.t).attr("width", f.w - f.m.l - f.m.r).attr("height", f.h - f.m.t - f.m.b).attr("fill", "none").attr("pointer-events", "all")
    .on("pointermove", (e) => { const r = e.currentTarget.getBoundingClientRect(), d = Math.round(f.X.invert(f.m.l + ((e.clientX - r.left) / r.width) * (f.w - f.m.l - f.m.r))); rule.attr("x1", f.X(d)).attr("x2", f.X(d)).attr("opacity", 1); read(d); })
    .on("pointerleave", () => { rule.attr("opacity", 0); read(null); });
  host.replaceChildren(f.svg.node());
}

// ------------------------------------------------------------------------------------------------------ state view
const col = (s, k) => s.series[k] ?? [];
const pts = (s, k) => col(s, "db").map((d, i) => [d, col(s, k)[i]]);

async function select(st, scroll = false) {
  S.st = st;
  const u = new URL(location.href); u.searchParams.set("st", st); history.replaceState(null, "", u);
  $("#st-pick").value = st;
  $("#cov-table tbody").querySelectorAll("tr").forEach((r) => r.classList.toggle("sel", r.dataset.st === st));
  document.querySelectorAll("#cov-map path.ev-st").forEach((p) => p.classList.toggle("sel", p.dataset.st === st));
  await drawState();
  if (scroll) $("#st-sec").scrollIntoView({ behavior: "smooth", block: "start" });
}

function chip(label, value, sub) { return `<span class="chip"><small>${esc(label)}</small><b>${value}</b>${sub ? `<i>${esc(sub)}</i>` : ""}</span>`; }

async function drawState() {
  const st = S.st, cov = S.index.states.find((c) => c.st === st);
  $("#st-h").textContent = cov.name;
  $("#st-status").innerHTML = `<span class="ev-pill s-${cov.status}">${STATUS[cov.status]}</span> ${cov.status === "collected" ? `${level(cov)} data${cov.party ? ", with party registration" : ""}` : ""}`;
  const [s, cty] = await Promise.all([get(`state_${st}`, cov.has_state), get(`counties_${st}`, cov.has_counties)]);
  const none = !s;
  for (const id of ["#st-chips", "#c1", "#c2", "#c3", "#c4", "#c5"]) $(id).replaceChildren();
  for (const id of ["#c1", "#c2", "#party-fig", "#cmp-fig"]) $(id).closest("figure")?.toggleAttribute("hidden", none);
  $("#party-fig").toggleAttribute("hidden", none || !s.has_party);
  if (none) {
    $("#st-basis").innerHTML = `${esc(cov.detail || "No official counts are collected for this state.")} ${cov.links.map((l) => `<a href="${esc(l.url)}" rel="noopener">${esc(l.label)}</a>`).join(", ")}`;
    $("#st-sources").replaceChildren();
    drawCounties(cty);
    return;
  }
  const L = s.latest, ed = s.election_day;
  $("#st-chips").innerHTML = [chip("Ballots so far", num(L.total), `counted through ${dayShort(L.date)}`), L.mail != null ? chip(s.mail_label, num(L.mail)) : "", L.inperson != null ? chip("In-person early votes", num(L.inperson)) : "",
    L.added != null ? chip(L.span > 1 ? `Added in ${L.span} days` : "Added in the last day", num(L.added), `to ${dayShort(L.date)}`) : "",
    L.lead != null ? chip("Democratic minus Republican", pt(L.lead), "share of returned ballots, by registration") : "",
    s.requested ? chip("Mail ballots requested", num(s.requested.value), `as of ${dayShort(s.requested.date)}`) : ""].join("");
  const kindNote = s.daily_file ? "Day by day from the return date of every ballot in the official file." : "One point per official report; days a state did not publish have no point.";
  $("#st-basis").textContent = `${s.basis} ${kindNote}`;
  $("#st-sources").innerHTML = `<b>Official sources.</b> ${s.sources.map((x) => (x.url ? `<a href="${esc(x.url)}" rel="noopener">${esc(x.what || x.id)}</a>` : esc(x.what || x.id))).join("; ")}.`;
  drawCharts(s);
  drawCompare(s);
  drawCounties(cty);
}

function drawCharts(s) {
  const ed = s.election_day, db = col(s, "db"), dmax = Math.max(...db, 14), x = [dmax, 0];
  const dayRow = (d) => { const i = db.indexOf(d); return i < 0 ? null : i; };
  // 1 cumulative
  const hasSplit = col(s, "mail").some((v) => v != null) && col(s, "inperson").some((v) => v != null);
  const ser = [{ name: "All ballots", color: COLORS.total, width: 2.6, pts: pts(s, "total"), k: "total" }];
  if (hasSplit) ser.push({ name: s.mail_label, color: COLORS.mail, pts: pts(s, "mail"), k: "mail" }, { name: "In-person early votes", color: COLORS.inperson, pts: pts(s, "inperson"), k: "inperson" });
  $("#c1-sub").textContent = `${s.name}: cumulative, through each day. ${hasSplit ? "" : "The state does not split mail and in-person on every day."}`;
  const key1 = keyHTML(ser.map((q) => [q.color, q.name]));
  const r1 = $("#c1-read"), say1 = (d) => {
    if (d == null) { r1.textContent = "Move over the chart to read a day."; return; }
    const i = dayRow(d); r1.textContent = i == null ? `${d} days before Election Day: no report for this day.` : `${dateLabel(db[i])}: ${ser.map((q) => `${q.name} ${num(col(s, q.k)[i])}`).join(" · ")}`;
  };
  const dateLabel = (d) => `${dayLong(dateAt(d, ed))} (${d} days before Election Day)`;
  lineChart($("#c1"), say1, { series: ser, x, yFmt: si, zero: true });
  $("#c1").insertAdjacentHTML("afterbegin", key1); say1(null);
  // 2 added per day
  const bars = col(s, "date").map((_, i) => ({ i, x: db[i], span: col(s, "span")[i] })).filter((b) => b.span != null);
  const splitAdd = col(s, "added_mail").some((v) => v != null) && col(s, "added_inperson").some((v) => v != null);
  bars.forEach((b) => (b.parts = splitAdd ? [["mail", col(s, "added_mail")[b.i]], ["inperson", col(s, "added_inperson")[b.i]]] : [["total", col(s, "added_total")[b.i]]]));
  const multi = bars.some((b) => b.span > 1);
  $("#c2-sub").textContent = `${s.name}: ballots added in a day, from the change between one report and the next.${multi ? " Where reports are more than a day apart, the change is spread evenly over the days (lighter bars)." : ""}`;
  const r2 = $("#c2-read"), say2 = (d) => {
    if (d == null) { r2.textContent = "Move over the chart to read a day."; return; }
    const b = bars.find((q) => d >= q.x && d < q.x + (q.span || 1)); if (!b) { r2.textContent = `${d} days before Election Day: no change reported.`; return; }
    r2.textContent = `${dateLabel(b.x)}: +${num(col(s, "added_total")[b.i])} in ${b.span > 1 ? `${b.span} days` : "the day"}${splitAdd ? ` (${s.mail_label.toLowerCase()} ${num(col(s, "added_mail")[b.i])}, in person ${num(col(s, "added_inperson")[b.i])})` : ""}`;
  };
  barChart($("#c2"), say2, { bars, colors: { mail: COLORS.mail, inperson: COLORS.inperson, total: COLORS.total }, x });
  $("#c2").insertAdjacentHTML("afterbegin", keyHTML(splitAdd ? [[COLORS.mail, s.mail_label, 1], [COLORS.inperson, "In-person early votes", 1]] : [[COLORS.total, "All ballots", 1]])); say2(null);
  // 3 party lead
  if (s.has_party) {
    const ld = pts(s, "lead");
    $("#c3-sub").textContent = `${s.name}: the Democratic share minus the Republican share of all returned ballots, in percentage points. Above zero: more ballots from registered Democrats.`;
    const r3 = $("#c3-read"), say3 = (d) => { if (d == null) { r3.textContent = "Move over the chart to read a day."; return; } const i = dayRow(d); r3.textContent = i == null ? `${d} days before Election Day: no report.` : `${dateLabel(db[i])}: ${pt(col(s, "lead")[i])}`; };
    lineChart($("#c3"), say3, { series: [{ name: "D minus R", color: "#1b1b1b", width: 2.4, pts: ld }], x, yFmt: (v) => `${v > 0 ? "+" : ""}${v}`, zero: true }); say3(null);
    const P = s.latest.party, tot = d3.sum(Object.values(P), (v) => v ?? 0);
    $("#party-table").innerHTML = `<thead><tr><th>Registration</th><th class="n">Returned ballots</th><th class="n">Share</th></tr></thead><tbody>${[["D", "Democratic"], ["R", "Republican"], ["U", "Unaffiliated"], ["other", "Other parties"]].filter(([k]) => P[k] != null).map(([k, t]) => `<tr><td><span style="color:${COLORS[k]}">■</span> ${t}</td><td class="n">${num(P[k])}</td><td class="n">${pc((100 * P[k]) / tot)}</td></tr>`).join("")}</tbody>`;
  }
}

function drawCompare(s) {
  const c = s.compare, ed = s.election_day, ok = (c.cycles ?? []).filter((y) => y.ok);
  const L = s.latest;
  $("#cmp-sub").textContent = c.cycles?.length ? `${s.name}, ${c.db} days before Election Day: ${dayShort(L.date)} this year.` : (c.why ?? "");
  const kindWord = c.kind === "inperson" ? "In-person early votes" : "Ballots";
  const now = `<tr class="now"><td>2026</td><td>midterm</td><td>${dayShort(L.date)}</td><td class="n">${num(c.now.ballots)}</td><td class="n">–</td><td class="n">–</td><td class="n">${c.now.lead != null ? pt(c.now.lead) : "–"}</td></tr>`;
  $("#cmp-table").innerHTML = `<thead><tr><th>Election</th><th>Type</th><th>Same day</th><th class="n">${kindWord} then</th><th class="n">Then, as % of that election's final votes</th><th class="n">2026 so far, as % of that election's final votes</th><th class="n">D minus R then</th></tr></thead><tbody>${now}${(c.cycles ?? []).map((y) => y.ok ?
    `<tr><td>${y.year}</td><td>${y.midterm ? "midterm" : "presidential"}</td><td>${dayShort(y.date)}${y.days_used !== c.db ? ` <small>(${y.days_used} days before)</small>` : ""}</td><td class="n">${num(y.ballots)}</td><td class="n">${pc(y.pct_final)}</td><td class="n">${pc(y.now_pct_final)}</td><td class="n">${y.lead != null ? pt(y.lead) : "–"}</td></tr>` :
    `<tr><td>${y.year}</td><td>${y.midterm ? "midterm" : "presidential"}</td><td class="why" colspan="5">no comparison: ${esc(y.why)}</td></tr>`).join("")}</tbody>`;
  const withHist = ok.length > 0;
  $("#cmp-table").closest(".table-scroll").nextElementSibling.toggleAttribute("hidden", !withHist);
  $("#c4-read").toggleAttribute("hidden", !withHist);
  $("#cmp-note").textContent = withHist ? `${c.votes_basis} ${c.kind === "inperson" ? "This state's past files count in-person early votes only, so the comparison uses this year's in-person count too." : ""} Presidential-year turnout runs well above midterm turnout: compare like with like (midterm with midterm).` : "";
  if (!withHist) return;
  const db = col(s, "db"), cur = db.map((d, i) => [d, col(s, c.kind === "inperson" ? "inperson" : "total")[i]]).filter((p) => p[0] <= 45);
  const ser = [{ name: "2026", color: COLORS.now, width: 3, pts: cur }, ...ok.map((y) => ({ name: String(y.year), color: CYCLE_COLORS[y.year], width: 1.8, dash: y.midterm ? null : "5 3", pts: y.curve.db.map((d, i) => [d, y.curve.cum[i]]) }))];
  const x = [Math.min(45, Math.max(...ser.flatMap((q) => q.pts.map((p) => p[0])))), 0];
  const key = keyHTML([[COLORS.now, "2026"], ...ok.map((y) => [CYCLE_COLORS[y.year], `${y.year}${y.midterm ? " (midterm)" : ""}`])]);
  const r = $("#c4-read"), say = (d) => {
    if (d == null) { r.textContent = "Move over the chart to read a day. Dashed lines are presidential years."; return; }
    const at = (q) => { const p = q.pts.find((v) => v[0] === d); return p?.[1]; };
    r.textContent = `${d} days before Election Day: ${ser.map((q) => `${q.name} ${num(at(q))}`).join(" · ")}`;
  };
  lineChart($("#c4"), say, { series: ser, x, yFmt: si, zero: true }); $("#c4").insertAdjacentHTML("afterbegin", key); say(null);
  const withLead = ok.filter((y) => y.curve.lead), showLead = s.has_party && withLead.length;
  $("#c5-wrap").toggleAttribute("hidden", !showLead);
  if (showLead) {
    const ls = [{ name: "2026", color: COLORS.now, width: 3, pts: pts(s, "lead").filter((p) => p[0] <= 45) }, ...withLead.map((y) => ({ name: String(y.year), color: CYCLE_COLORS[y.year], width: 1.8, dash: y.midterm ? null : "5 3", pts: y.curve.db.map((d, i) => [d, y.curve.lead[i]]) }))];
    lineChart($("#c5"), () => {}, { series: ls, x, yFmt: (v) => `${v > 0 ? "+" : ""}${v}`, zero: true }); $("#c5").insertAdjacentHTML("afterbegin", key);
  }
}

// ---------------------------------------------------------------------------------------------------------- counties
const METRICS = {
  pct22: { label: "Ballots so far, as % of 2022 votes", f: (r) => r.pct22, fmt: pc, seq: true },
  lead: { label: "Democratic minus Republican", f: (r) => r.lead, fmt: pt, div: true },
  total: { label: "Ballots so far", f: (r) => r.total, fmt: num, seq: true },
};

async function drawCounties(cty) {
  const fig = $("#cty-fig");
  fig.toggleAttribute("hidden", !cty);
  S.ctyData = cty;
  if (!cty) return;
  const metrics = Object.entries(METRICS).filter(([k]) => (k !== "lead" || cty.has_party) && (k !== "pct22" || cty.counties.some((r) => r.pct22 != null)));
  if (!metrics.some(([k]) => k === S.cty.metric)) S.cty.metric = metrics[0][0];
  $("#cty-sub").textContent = `${cty.name}: ${cty.n} ${cty.n === 1 ? "county" : "counties"} with counts through ${dayShort(cty.as_of)}.${S.index.states.find((c) => c.st === S.st)?.partial ? " This state publishes some counties only; the rest are not shown." : ""}`;
  const tg = $("#cty-metric");
  tg.replaceChildren(...metrics.map(([k, m]) => { const b = document.createElement("button"); b.type = "button"; b.textContent = m.label; b.setAttribute("aria-pressed", String(S.cty.metric === k)); b.onclick = () => { S.cty.metric = k; drawCounties(cty); }; return b; }));
  const m = METRICS[S.cty.metric], vals = new Map(cty.counties.map((r) => [r.fips, m.f(r)]));
  const finite = [...vals.values()].filter((v) => v != null);
  const mx = d3.max(finite) ?? 1, amax = Math.max(...finite.map(Math.abs), 1);
  const color = m.div ? d3.scaleDiverging([-amax, 0, amax], (t) => d3.interpolateRdBu(1 - t)) : d3.scaleSequential([0, mx], d3.interpolateGreens);
  S.geo2 ??= await getJSON("geo/us_counties.topo.json", { root: STATIC });
  const feats = topojson.feature(S.geo2, S.geo2.objects.counties).features.filter((f) => f.properties.unit_id.startsWith(`us-${S.st}-`));
  const host = $("#cty-map"), w = Math.max(300, host.clientWidth || 480), h = Math.round(w * 0.7);
  if (feats.length) {
    const proj = d3.geoAlbersUsa().fitExtent([[4, 4], [w - 4, h - 4]], { type: "FeatureCollection", features: feats });
    const path = d3.geoPath(proj), svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`), by = new Map(cty.counties.map((r) => [r.fips, r]));
    svg.selectAll("path").data(feats).join("path").attr("class", "ev-ct").attr("d", path)
      .attr("fill", (f) => { const v = vals.get(f.properties.unit_id.split("-")[2]); return v == null ? "#efede7" : color(v); })
      .append("title").text((f) => { const r = by.get(f.properties.unit_id.split("-")[2]); return r ? `${r.name}: ${m.fmt(m.f(r))} (${num(r.total)} ballots)` : f.properties.name; });
    host.replaceChildren(svg.node());
  } else host.replaceChildren();
  $("#cty-legend").innerHTML = finite.length ? (m.div ? `Democratic-leaning <span class="grad" style="background:linear-gradient(90deg,${d3.interpolateRdBu(1)},${d3.interpolateRdBu(0.5)},${d3.interpolateRdBu(0)})"></span> Republican-leaning: ${pt(-amax)} to ${pt(amax)}`
    : `0 <span class="grad" style="background:linear-gradient(90deg,${d3.interpolateGreens(0)},${d3.interpolateGreens(1)})"></span> ${m.fmt(mx)}`) : "";
  // table
  const cols = [["name", "County"], ["total", "Ballots", "n"], ["mail", "Mail", "n"], ["inperson", "In person", "n"], ["added", "Added in last report", "n"], ["pct22", "% of 2022 votes", "n"], ...(cty.has_party ? [["lead", "D minus R", "n"]] : [])];
  const { key, dir } = S.cty.sort, rows = [...cty.counties].sort((a, b) => ((a[key] ?? -1e12) < (b[key] ?? -1e12) ? -1 : (a[key] ?? -1e12) > (b[key] ?? -1e12) ? 1 : 0) * dir);
  const fm = { total: num, mail: num, inperson: num, added: num, pct22: pc, lead: pt };
  $("#cty-table").innerHTML = `<thead><tr>${cols.map(([k, t, c]) => `<th class="${c ?? ""}" ${key === k ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}><button type="button" data-k="${k}">${t}</button></th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map(([k, , c]) => `<td class="${c ?? ""}">${k === "name" ? esc(r.name) : fm[k](r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("#cty-table").querySelectorAll("th button").forEach((b) => (b.onclick = () => { S.cty.sort = { key: b.dataset.k, dir: key === b.dataset.k ? -dir : b.dataset.k === "name" ? 1 : -1 }; drawCounties(cty); }));
}

// -------------------------------------------------------------------------------------------------------------- boot
async function boot() {
  const idx = (S.index = await getJSON("ev2026/index.json"));
  $("#ev-years").textContent = idx.years.join(", ");
  const newest = idx.states.reduce((m, c) => (c.as_of && c.as_of > m ? c.as_of : m), "");
  $("#ev-asof").textContent = `Latest official counts run through ${dayLong(newest)}, ${idx.election_day.slice(0, 4)}; the files were last fetched ${stamp(idx.built)}. Election Day is ${dayLong(idx.election_day)}, ${dbOf(newest, idx.election_day)} days after that count.`;
  const pick = $("#st-pick");
  pick.innerHTML = idx.states.map((c) => `<option value="${c.st}">${esc(c.name)}${c.status === "collected" ? "" : ` (${STATUS[c.status]})`}</option>`).join("");
  pick.onchange = () => select(pick.value);
  drawCoverage();
  const want = new URLSearchParams(location.search).get("st");
  const first = idx.states.filter((c) => c.status === "collected" && !c.partial).sort((a, b) => (b.total ?? 0) - (a.total ?? 0))[0];
  S.st = idx.states.some((c) => c.st === want) ? want : first?.st ?? idx.states[0].st;
  $("#st-sec").hidden = false;
  await drawCoverageMap();
  await select(S.st);
  document.fonts?.ready;
  let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { drawCoverageMap(); drawState(); }, 200); });
  window.__evReady = true;
}
boot().catch(window.__showLoadError ?? console.error);
