// US early-vote page: reads data/ev2026/{index,state_<st>,counties_<st>}.json (analysis/us_ev_site.py). Every number shown comes from
// those files; this module only formats and draws them.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, STATIC } from "./common.js";
import { initTurnout, data as TD, usMap } from "./turnout.js";

const $ = (s) => document.querySelector(s);
const COLORS = { total: "#1b1b1b", mail: "#6a7f99", inperson: "#c4501b", D: "#1f5fbf", R: "#c8102e", U: "#8a8578", other: "#b9b5aa", now: "#1b1b1b" };
const CYCLE_COLORS = { 2018: "#8a6fb0", 2020: "#2f8a6c", 2022: "#c49a1b", 2024: "#6a7f99" };
const STATUS = { collected: "collected", compiled: "compiled (UF Election Lab)", blocked: "blocked", not_published: "not published" };
const MAP_COLORS = { county: "#2f8a6c", state: "#86bfa7", partial: "#c9dfd3", compiled: "#9d86c2", blocked: "#d9743a", not_published: "#cfcbc0" };

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

const S = { index: null, model: null, metric: "pct22", scale: null, st: null, cache: new Map(), sort: { key: "name", dir: 1 }, filter: "all", geo: null, cty: { metric: null, sort: { key: "total", dir: -1 } }, ctyData: null };
const get = (name, exists = true) => (!exists ? null : S.cache.has(name) ? S.cache.get(name) : (S.cache.set(name, getJSON(`ev2026/${name}.json`, { optional: true })), S.cache.get(name)));

// ------------------------------------------------------------------------------------------------------ coverage
const level = (c) => (c.level === "none" ? "–" : c.partial ? "county only" : c.status === "compiled" ? "state" : c.level);
const OFFICIAL = { blocked: "blocked to our program", not_published: "not published as data", county_only: "only some county offices publish" };
const sortVal = {
  name: (c) => c.name, status: (c) => c.status, level: (c) => level(c), party: (c) => (c.party ? 1 : 0), as_of: (c) => c.as_of ?? "", last: (c) => c.last_fetch ?? "",
  total: (c) => c.total ?? -1, pace: (c) => c.pace?.pace ?? -1, pct22: (c) => c.pct22 ?? -1, lead: (c) => c.lead ?? -1e9, hist: (c) => c.hist.length,
};
const COLS = [["name", "State"], ["status", "Status"], ["level", "Data level"], ["party", "Party detail"], ["as_of", "Counted through", "n"], ["last", "Last checked (UTC)", "n ev-hide-sm"],
  ["total", "Ballots so far", "n"], ["pace", "Pace vs 2022 (same day)", "n"], ["pct22", "% of 2022 total votes", "n"], ["lead", "D minus R", "n"], ["hist", "Past elections", "n ev-hide-sm"], ["links", "Official source", "ev-hide-sm"]];

// Pace vs 2022 (analysis/us_ev_pace.py): this year's count over the 2022 count on the same day before Election Day.
const x2 = (v) => (v == null ? "–" : `${v.toFixed(2)}×`);
const paceTags = (p) => {
  const t = [];
  if (p.gap != null && p.flags.includes("gap")) t.push(`2022 figure ${Math.abs(p.gap)} day${Math.abs(p.gap) === 1 ? "" : "s"} ${p.gap > 0 ? "earlier" : "later"} than this year's day`);
  if (p.flags.includes("definition")) t.push(`different definition in 2022: ${p.then.measure}`);
  return t;
};
const paceSrc = (p) => `this year: ${p.now.source_label}; 2022: ${p.then.source_label}`;
function paceCell(c) {
  const p = c.pace;
  if (!p) return `<td class="n">–</td>`;
  if (p.pace == null) return `<td class="n" title="${esc(p.why || "no comparable 2022 figure")}">–</td>`;
  const tags = paceTags(p);
  return `<td class="n ev-pace${tags.length ? " ev-flag" : ""}" title="${esc([paceSrc(p), ...tags].join("; "))}">${x2(p.pace)}<span class="ev-detail">${esc(p.now.source_label)} / ${esc(p.then.source_label)}${tags.length ? " *" : ""}</span></td>`;
}
function drawPace() {
  const cs = S.index.states.filter((c) => c.pace?.pace != null).sort((a, b) => b.pace.pace - a.pace.pace);
  const none = S.index.states.filter((c) => c.pace && c.pace.pace == null);
  const max = Math.max(1.5, ...cs.map((c) => c.pace.pace));
  const pi = S.index.pace;
  $("#pace-sub").textContent = cs.length ? `${cs.length} states have a comparable 2022 figure on the same day before Election Day. 1.00× means as many ballots as at this point in 2022; ${none.length} have none (see the reason on hover in the table).` : "No state has a comparable 2022 figure.";
  $("#pace-list").innerHTML = cs.map((c) => {
    const p = c.pace, tags = paceTags(p);
    return `<li><button type="button" class="ev-name" data-st="${c.st}">${esc(c.name)}</button><span class="ev-bar"><i style="width:${(100 * p.pace / max).toFixed(1)}%"></i><b style="left:${(100 / max).toFixed(1)}%"></b></span>` +
      `<span class="n">${x2(p.pace)}</span><span class="ev-detail">${esc(paceSrc(p))}; ${num(p.now.value)} on ${dayShort(p.now.date)} vs ${num(p.then.value)} on ${dayShort(p.then.date)} in 2022${tags.length ? `; ${esc(tags.join("; "))}` : ""}</span></li>`;
  }).join("");
  $("#pace-list").querySelectorAll("[data-st]").forEach((el) => (el.onclick = () => select(el.dataset.st)));
  $("#pace-note").innerHTML = `Sources: <b>official</b> is a state's own published count; <b>compiled (UF)</b> is the ${esc(S.index.tracker?.label ?? "UF Election Lab compilation")}; <b>archived UF 2022</b> is ${esc(pi.uf2022_label)}` +
    `${pi.uf2022_used.length ? ` (used for: ${pi.uf2022_used.map((x) => x.toUpperCase()).join(", ")})` : ` (no state uses it: the archive holds ${pi.uf2022_found.length ? `a page for only ${pi.uf2022_found.map((x) => x.toUpperCase()).join(", ")} before Election Day, all weeks earlier than the day compared` : "none of these pages from before Election Day"})`}. ` +
    `A 2022 figure up to ${pi.flag_gap_days} day from the same day is treated as the same day; up to ${pi.max_gap_days} days is shown and marked *; further apart is not used. ` +
    `"% of 2022 total votes": ballots so far over the certified 2022 total (${esc(pi.certified_basis.replace(/^Certified 2022 total votes: /, "").replace(/\.$/, ""))}).`;
}

function drawCoverage() {
  drawPace();
  const cs = S.index.states, n = (s) => cs.filter((c) => c.status === s).length;
  const county = cs.filter((c) => c.status === "collected" && c.level === "county" && !c.partial).length;
  const party = cs.filter((c) => c.status === "collected" && c.party).length;
  $("#cov-sum").textContent = `${n("collected")} of ${cs.length} states and DC have official counts we collect (${county} down to the county, ${party} with party registration). ` +
    (n("compiled") ? `${n("compiled")} more have none we can collect and are filled from the UF Election Lab's compilation of state sources, marked compiled (state totals; party registration where the Lab reports it). ` : "") +
    `${n("blocked")} are blocked to our program and ${n("not_published")} publish nothing usable, with no other source.`;
  const f = $("#cov-filter");
  f.replaceChildren(...[["all", `All ${cs.length}`], ["collected", `Collected ${n("collected")}`], ["compiled", `Compiled ${n("compiled")}`], ["blocked", `Blocked ${n("blocked")}`], ["not_published", `Not published ${n("not_published")}`]].map(([k, t]) => {
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
    const off = c.status === "compiled" ? `Official count: ${OFFICIAL[c.official?.status] ?? "none collected"}.` : "";
    const why = c.status === "compiled" ? off : c.detail.replace(/^(blocked|not published|none published|dashboard-only)\s*[:;]?\s*/i, (m) => (/^(dashboard-only|none published)/i.test(m) ? m : ""));
    const detail = why ? `<span class="ev-detail">${esc(why.length > 150 ? `${why.slice(0, 147)}…` : why)}</span>` : "";
    const stale = c.stale ? ` <span class="ev-detail" style="display:inline">(older than the rest)</span>` : "";
    return `<tr data-st="${c.st}" class="${c.st === S.st ? "sel" : ""}"><td><button type="button" class="ev-name" data-st="${c.st}">${esc(c.name)}</button></td>` +
      `<td><span class="ev-pill s-${c.status}">${STATUS[c.status]}</span>${detail}</td><td>${level(c)}</td><td>${c.status === "collected" || c.status === "compiled" ? (c.party ? "yes" : "no") : "–"}</td>` +
      `<td class="n">${dayShort(c.as_of)}${stale}</td><td class="n ev-hide-sm">${stamp(c.last_fetch)}</td><td class="n">${num(c.total)}</td>${paceCell(c)}<td class="n"${c.pct22 > 100 ? ` title="the 2022 total in the source looks too low"` : ""}>${pct22Of(c) != null ? pc(c.pct22) : "–"}</td><td class="n">${pt(c.lead)}</td>` +
      `<td class="n ev-hide-sm">${c.hist.length ? c.hist.join(", ") : "–"}</td><td class="ev-links ev-hide-sm">${c.links.slice(0, 2).map((l) => `<a href="${esc(l.url)}" rel="noopener">${esc(l.label)}</a>`).join("")}</td></tr>`;
  }).join("");
  $("#cov-table tbody").querySelectorAll("[data-st]").forEach((el) => el.addEventListener("click", (e) => { if (e.target.closest("a")) return; select(el.dataset.st); }));
  const tr = S.index.tracker;
  const licence = tr?.licence ? ` <b>${esc(tr.label)}</b>: ${esc(tr.licence)}. <a href="${esc(tr.url)}" rel="noopener">UF Election Lab early-vote tracker</a>.` : "";
  $("#cov-note").innerHTML = `"Counted through" is the last day the official file covers; "last checked" is when our program last fetched a state's files. "D minus R" is the Democratic minus Republican share of returned ballots by party registration, not a vote count. ` +
    `${licence}`;
}

// Map metrics. val(c) -> number or null; colours has one more entry than breaks; derived ones carry the "unvalidated context" label.
const RAMP = d3.interpolateLab("#ece9e1", "#3d5a40"); // the early-vote ramp of election.html
const SEQ = [0.14, 0.36, 0.58, 0.8, 1].map(RAMP);
const DIVG = ["#c4501b", "#dedbd1", "#9fcbb6", "#52a17f", "#1f6a4f"];
const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec"; // the national map's party colours
const DIVP = [d3.interpolateLab(NEUTRAL, REP)(0.95), d3.interpolateLab(NEUTRAL, REP)(0.5), NEUTRAL, d3.interpolateLab(NEUTRAL, DEM)(0.5), d3.interpolateLab(NEUTRAL, DEM)(0.95)];
const NOFIG = "#e6e4dd";
const modelOf = (c) => S.model?.states?.[c.st.toUpperCase()];
// A share above 100% of the 2022 total means the 2022 total in the source is too low (all of 2022's votes cannot be fewer than the early ballots of this year), so it is not mapped.
const pct22Of = (c) => (c.pct22 != null && c.pct22 <= 100 ? c.pct22 : null);
const suspect22 = () => S.index.states.filter((c) => c.pct22 != null && c.pct22 > 100);
const SRC = { collected: "official count", compiled: "UF Election Lab compile (secondary)", blocked: "none collected (blocked)", not_published: "none published" };
const MAPM = {
  pct22: { label: "% of 2022 total votes", val: pct22Of, fmt: pc, short: (v) => `${Math.round(v)}%`, colors: SEQ, unit: "of 2022's total votes", asof: (c) => dayShort(c.as_of), src: (c) => SRC[c.status],
    note: () => `Ballots counted so far divided by the certified 2022 total vote in the same state; breaks are quintiles of the states shown. Definitions differ by state (some count only mail or only in-person).${suspect22().map((c) => ` ${c.name} is not shown: its ${num(c.total)} ballots already exceed the 2022 total of ${num(c.pace?.cert22 ?? (c.total * 100) / c.pct22)} in the source, so that 2022 total looks too low.`).join("")}` },
  pace: { label: "vs 2022 same point", val: (c) => c.pace?.pace, fmt: x2, short: (v) => `${v.toFixed(2)}×`, fixed: [0.95, 1.05, 1.15, 1.3], colors: DIVG, unit: "of 2022 at this point", asof: (c) => (c.pace?.now ? dayShort(c.pace.now.date) : "–"), src: (c) => (c.pace?.now ? paceSrc(c.pace) : SRC[c.status]),
    note: () => "This year's ballots divided by the 2022 ballots on the same number of days before Election Day. 1.00× is level with 2022. Shown only where a state published a comparable 2022 figure; the rest are grey." },
  expected: { label: "% of model's expected", val: (c) => modelOf(c)?.banked_pct_expected, fmt: pc, short: (v) => `${Math.round(v)}%`, colors: SEQ, unit: "of expected", derived: true, asof: () => (S.model ? stamp(S.model.generated_utc) : "–"), src: () => "turnout_model.json (banked ballots over the models' expected votes)",
    note: () => "Banked ballots (early, mail, and Election Day check-ins where published) over the total votes the registered models expect; breaks are quintiles. Unvalidated context, not used by any model. Ballots include third-party and blank votes, so shares run a little high." },
  lead: { label: "Party lead (registration)", val: (c) => c.lead, fmt: pt, short: (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(0)}`, fixed: [-10, -3, 3, 10], colors: DIVP, unit: "D minus R", asof: (c) => dayShort(c.as_of), src: (c) => `${SRC[c.status]}; party registration`,
    note: () => "Democratic minus Republican share of returned ballots, by the voter's party registration, only where a state publishes it. Registration is not a vote: unaffiliated and crossover voters are large." },
  coverage: { label: "Data coverage", val: null, note: () => "Which states publish counts we can collect, at what level, and which we fill from the UF Election Lab compilation." },
};
const covKey = (c) => (c.status !== "collected" ? c.status : c.partial ? "partial" : c.level);
const metricsAvailable = () => Object.keys(MAPM).filter((k) => k !== "expected" || S.model);
// breaks: fixed ones, else quintiles of this metric's values, rounded and de-duplicated (colours thin out with them)
function scaleOf(k) {
  const m = MAPM[k];
  if (m.fixed) return { breaks: m.fixed, colors: m.colors };
  const vs = S.index.states.map(m.val).filter((v) => v != null).sort((a, b) => a - b);
  const br = [...new Set([0.2, 0.4, 0.6, 0.8].map((q) => Math.round(d3.quantileSorted(vs, q))))];
  const cols = br.length === 4 ? m.colors : br.length ? br.map((_, i) => m.colors[Math.round(((i + 1) / (br.length + 1)) * 4)]).concat(m.colors[4]) : [m.colors[2]];
  return { breaks: br, colors: cols };
}
const fillOf = (c) => { if (S.metric === "coverage") return MAP_COLORS[covKey(c)]; const m = MAPM[S.metric], v = m.val(c); return v == null ? NOFIG : S.scale.colors[d3.bisect(S.scale.breaks, v)]; };
const mapText = (c) => {
  if (S.metric === "coverage") return `${c.name}: ${STATUS[c.status]}${c.status === "collected" || c.status === "compiled" ? `, ${level(c)} data${c.party ? ", party detail" : ""}, counted through ${dayShort(c.as_of)}` : ""}`;
  const m = MAPM[S.metric], v = m.val(c);
  return v == null ? `${c.name}: no figure for this measure` : `${c.name}: ${m.fmt(v)}${m.unit ? ` ${m.unit}` : ""}, as of ${m.asof(c)}; ${m.src(c)}`;
};
function tipHTML(c) {
  if (S.metric === "coverage") return `<b>${esc(c.name)}</b><br>${esc(STATUS[c.status])}${c.as_of ? `<br>Counted through ${dayShort(c.as_of)}` : ""}<br>${esc(SRC[c.status])}`;
  const m = MAPM[S.metric], v = m.val(c);
  return v == null ? `<b>${esc(c.name)}</b><br>No figure for this measure<br>${esc(SRC[c.status])}` : `<b>${esc(c.name)}</b><br>${esc(m.fmt(v))}${m.unit ? ` ${esc(m.unit)}` : ""}<br>As of ${esc(m.asof(c))}<br>Source: ${esc(m.src(c))}`;
}

// ---- the panel beside the map: a national summary of the current measure, or the whole picture of one state
const kpi = (k, n, sub, ctx) => `<div class="kf-card"><p class="kf-k">${esc(k)}</p><p class="kf-n">${n}</p><p class="kf-a">${sub}</p>${ctx ? `<p class="kf-ctx">Unvalidated context, not used by any model</p>` : ""}</div>`;
let sideToken = 0;
function sideSummary() {
  const cs = S.index.states, m = MAPM[S.metric];
  const btn = (c, txt) => `<li><button type="button" class="ev-name" data-st="${c.st}">${esc(c.name)}</button> <span class="n">${txt}</span></li>`;
  if (S.metric === "coverage") {
    const n = (k) => cs.filter((c) => c.status === k).length;
    return `<h3 class="side-h">All states</h3><p class="side-note">${n("collected")} publish counts we collect, ${n("compiled")} are filled from the UF Election Lab compile, ${n("blocked")} are blocked and ${n("not_published")} publish nothing usable.</p><p class="note">Click a state on the map for its details.</p>`;
  }
  const have = cs.filter((c) => m.val(c) != null).sort((a, b) => m.val(b) - m.val(a));
  const list = (xs) => `<ol class="side-list">${xs.map((c) => btn(c, `${esc(m.fmt(m.val(c)))}`)).join("")}</ol>`;
  return `<h3 class="side-h">${esc(m.label)}</h3><p class="side-note">${have.length} of ${cs.length} states and DC have a figure. Click a state on the map for its details.</p>` +
    (have.length > 1 ? `<p class="kf-k">Highest</p>${list(have.slice(0, 5))}<p class="kf-k">Lowest</p>${list(have.slice(-5).reverse())}` : have.length ? `<p class="kf-k">Only</p>${list(have)}` : "");
}
function spark(s) {
  const db = s.series.db, y = s.series.total, ok = db.map((d, i) => [d, y[i]]).filter((p) => p[1] != null && p[0] <= 30);
  if (ok.length < 2) return `<p class="note">Not enough days published for a line.</p>`;
  const W = 300, H = 64, X = d3.scaleLinear([d3.max(ok, (p) => p[0]), 0], [4, W - 4]), Y = d3.scaleLinear([0, d3.max(ok, (p) => p[1])], [H - 4, 4]);
  const ln = d3.line().x((p) => X(p[0])).y((p) => Y(p[1]));
  return `<svg class="side-spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="Ballots so far by days before Election Day"><path d="${ln(ok.sort((a, b) => b[0] - a[0]))}" fill="none" stroke="#1b1b1b" stroke-width="2"/></svg><p class="kf-a">Cumulative ballots, last ${d3.max(ok, (p) => p[0])} days to Election Day; ${num(d3.max(ok, (p) => p[1]))} at the latest count.</p>`;
}
const bar = (parts) => { const t = d3.sum(parts, (p) => p[1] ?? 0) || 1; return `<div class="side-bar">${parts.filter((p) => p[1]).map(([n, v, c]) => `<span style="flex:${v};background:${c}" title="${esc(n)}: ${num(v)}"></span>`).join("")}</div><p class="kf-a">${parts.filter((p) => p[1]).map(([n, v, c]) => `<i style="background:${c}"></i>${esc(n)} ${pc((100 * v) / t, 0)}`).join(" · ")}</p>`; };
async function sideState(c) {
  const [s, cty] = await Promise.all([get(`state_${c.st}`, c.has_state), get(`counties_${c.st}`, c.has_counties)]);
  const mo = modelOf(c), L = s?.latest, pace = c.pace?.pace;
  const head = `<h3 class="side-h">${esc(c.name)} <span class="ev-pill s-${c.status}">${STATUS[c.status]}</span></h3>`;
  if (!s) return `${head}<p class="side-note">${esc(c.detail || "No official counts are collected for this state.")}</p><p class="note">${c.links.map((l) => `<a href="${esc(l.url)}" rel="noopener">${esc(l.label)}</a>`).join(", ")}</p>`;
  const pcts = pct22Of(c);
  const cards = [
    kpi("Ballots so far", num(L.total), `counted through ${dayShort(L.date)}`),
    kpi("Of 2022's total", pcts != null ? pc(pcts) : "–", c.pct22 != null && pcts == null ? "2022 total in the source looks too low" : "certified 2022 total votes"),
    kpi("Vs 2022, same point", pace != null ? x2(pace) : "–", pace != null ? esc(paceSrc(c.pace)) : "no comparable 2022 figure"),
    mo?.banked_pct_expected != null ? kpi("Of the model's expected", pc(mo.banked_pct_expected), `generated ${esc(stamp(S.model.generated_utc))}`, true) : "",
    L.lead != null ? kpi("Party lead (registration)", pt(L.lead), "Democratic minus Republican share of returned ballots") : "",
    L.added != null ? kpi(L.span > 1 ? `Added in ${L.span} days` : "Added in the last day", num(L.added), `to ${dayShort(L.date)}`) : "",
  ].join("");
  const P = L.party, parts = [];
  const method = L.mail != null && L.inperson != null ? `<p class="kf-k">By method</p>${bar([[s.mail_label, L.mail, COLORS.mail], ["In-person early", L.inperson, COLORS.inperson]])}` : L.mail != null ? `<p class="kf-a">All counted as ${esc(s.mail_label.toLowerCase())}.</p>` : "";
  const party = P ? `<p class="kf-k">By party registration (not votes)</p>${bar([["Democratic", P.D, COLORS.D], ["Republican", P.R, COLORS.R], ["Unaffiliated", P.U, COLORS.U], ["Other", P.other, COLORS.other]])}` : "";
  const top = cty ? `<p class="kf-k">Largest counties</p><ol class="side-list">${[...cty.counties].sort((a, b) => (b.total ?? 0) - (a.total ?? 0)).slice(0, 5).map((r) => `<li>${esc(r.name)} <span class="n">${num(r.total)}</span></li>`).join("")}</ol>` : "";
  return `${head}<div class="side-cards">${cards}</div>${method}${party}<p class="kf-k">Over time</p>${spark(s)}${top}<p class="note">Source: ${esc(s.sources.map((x) => x.what || x.id).join("; "))}. ${esc(SRC[c.status])}. Full charts and the county table are further down and update with this panel.</p>`;
}
async function updateSel() {
  const host = $("#cov-sel");
  if (!host) return;
  const tok = ++sideToken;
  const c = S.st ? S.index.states.find((x) => x.st === S.st) : null;
  const html = c ? await sideState(c) : sideSummary();
  if (tok !== sideToken) return;
  host.innerHTML = html;
  host.querySelectorAll("[data-st]").forEach((el) => (el.onclick = () => select(el.dataset.st)));
}
function drawMetricUI() {
  const keys = metricsAvailable();
  if (!keys.includes(S.metric)) S.metric = keys[0];
  S.scale = S.metric === "coverage" ? null : scaleOf(S.metric);
  $("#cov-metric").replaceChildren(...keys.map((k) => { const b = document.createElement("button"); b.type = "button"; b.textContent = MAPM[k].label; b.setAttribute("aria-pressed", String(S.metric === k)); b.onclick = () => { S.metric = k; drawMetricUI(); recolorMap(); }; return b; }));
  $("#cov-metric-note").textContent = MAPM[S.metric].note();
  const lg = $("#cov-legend");
  if (S.metric === "coverage") {
    const have = new Set(S.index.states.map(covKey));
    lg.innerHTML = `<ul class="party-legend">${[["county", "county data"], ["state", "state totals only"], ["compiled", "compiled by UF Election Lab"], ["partial", "some counties only"], ["blocked", "blocked"], ["not_published", "not published"]].filter(([k]) => have.has(k)).map(([k, t]) => `<li><i style="background:${MAP_COLORS[k]}"></i>${t}</li>`).join("")}</ul>`;
  } else {
    const m = MAPM[S.metric], { breaks: b, colors } = S.scale, f = (v) => m.short(v);
    const labs = colors.map((_, i) => (i === 0 ? `below ${f(b[0])}` : i === b.length ? `${f(b[i - 1])} and above` : `${f(b[i - 1])} to ${f(b[i])}`));
    if (S.metric === "lead") { labs[0] = `Republican by more than ${Math.abs(b[0])} pt`; labs[b.length] = `Democratic by more than ${b[b.length - 1]} pt`; labs[2] = "within 3 pt"; }
    lg.innerHTML = `<ul class="party-legend">${colors.map((c, i) => `<li><i style="background:${c}"></i>${esc(labs[i])}</li>`).join("")}<li><i style="background:${NOFIG}"></i>no figure</li></ul>`;
  }
  updateSel();
}
function recolorMap() {
  if (!S.us) return;
  S.us.colour(new Map(S.index.states.map((c) => [c.st, fillOf(c)])));
  const m = MAPM[S.metric];
  S.us.values(new Map(S.metric === "coverage" ? [] : S.index.states.filter((c) => m.val(c) != null).map((c) => [c.st, m.short(m.val(c))])));
  for (const c of S.index.states) S.us.label(c.st, mapText(c));
}
// the hover card of election.html (.place-card), over the map box
function showTip(c, at) {
  const card = $("#cov-tip");
  if (!c) { card.hidden = true; return; }
  card.innerHTML = `<p class="pc-name">${tipHTML(c)}</p>`;
  card.hidden = false;
  const box = $("#cov-map").getBoundingClientRect(), pos = at ?? [box.width / 2, box.height / 2];
  if (matchMedia("(min-width: 700px)").matches) {
    let x = pos[0] + 16, y = pos[1] + 16;
    if (x + card.offsetWidth > box.width - 8) x = pos[0] - card.offsetWidth - 16;
    if (y + card.offsetHeight > box.height - 8) y = Math.max(8, box.height - card.offsetHeight - 8);
    card.style.left = `${Math.max(8, x)}px`; card.style.top = `${Math.max(8, y)}px`;
  } else { card.style.left = ""; card.style.top = ""; }
}

async function drawCoverageMap() {
  if (S.us) { drawMetricUI(); recolorMap(); return; }
  const box = $("#cov-map"), by = new Map(S.index.states.map((c) => [c.st, c]));
  const at = (ev) => { const r = box.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
  S.us = await usMap(box, {
    names: (r) => by.get(r)?.name ?? r.toUpperCase(),
    onHover: (r, ev) => showTip(r ? by.get(r) : null, ev ? at(ev) : null),
    onClick: (r) => select(r),
    onFocus: (r) => showTip(r ? by.get(r) : null, null),
  });
  if (S.st) S.us.select(S.st);
  $("#cov-legend-toggle").onclick = () => { const lg = $("#cov-legend"), on = lg.hidden; lg.hidden = !on; $("#cov-legend-toggle").textContent = on ? "Hide legend" : "Show legend"; $("#cov-legend-toggle").setAttribute("aria-expanded", String(on)); };
  drawMetricUI();
  recolorMap();
}

// ----------------------------------------------------------------------------------------------------- key findings
const bigNum = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e8 ? 0 : 1)}M` : v >= 1e4 ? `${(v / 1e3).toFixed(0)}k` : int(Math.round(v)));
const names = (cs) => cs.map((c) => c.st.toUpperCase()).join(", ");
const card = (k, n, m, a, ctx) => `<div class="kf-card"><p class="kf-k">${esc(k)}</p><p class="kf-n">${esc(n)}</p><p class="kf-m">${m}</p><p class="kf-a">${a}</p>${ctx ? `<p class="kf-ctx">Unvalidated context, not used by any model</p>` : ""}</div>`;

function drawFindings() {
  if (!S.index) return;
  const cs = S.index.states, have = cs.filter((c) => c.total != null), none = cs.filter((c) => c.total == null);
  const tot = d3.sum(have, (c) => c.total), newest = d3.max(have, (c) => c.as_of), oldest = d3.min(have, (c) => c.as_of);
  const w22 = have.filter((c) => pct22Of(c) != null), now22 = d3.sum(w22, (c) => c.total), cert = d3.sum(w22, (c) => c.pace?.cert22 ?? (c.total * 100) / c.pct22);
  const pc22 = cs.filter((c) => c.pace?.pace != null), up = pc22.filter((c) => c.pace.pace > 1), dn = pc22.filter((c) => c.pace.pace < 1);
  const party = cs.filter((c) => c.party && c.lead != null), dl = party.filter((c) => c.lead > 0), rl = party.filter((c) => c.lead < 0);
  // Election Day check-ins: the largest statewide Election Day row of each state
  const days = (TD.day?.states ?? []).map((s) => [s, d3.max(s.rows.filter((r) => r.kind === "election_day" && r.count != null), (r) => r.count)]).filter(([, n]) => n != null);
  const edTot = d3.sum(days, ([, n]) => n), edAt = d3.max(days.flatMap(([s]) => s.rows.filter((r) => r.kind === "election_day").map((r) => r.fetched_utc)));
  const mod = S.model ? Object.values(S.model.states).filter((m) => m.banked?.value != null && m.expected?.votes) : [];
  const mB = d3.sum(mod, (m) => m.banked.value), mE = d3.sum(mod, (m) => m.expected.votes);
  // mail ballots sent/requested and not yet returned (states that do not mail every voter), and the 2022 final pre-election report comparison
  const out = Object.values(S.model?.states ?? {}).filter((m) => m.outstanding?.ok && m.outstanding.kind === "outstanding").sort((a, b) => b.outstanding.value - a.outstanding.value);
  const outTot = d3.sum(out, (m) => m.outstanding.value), outNew = d3.max(out, (m) => m.outstanding.as_of_date), outOld = d3.min(out, (m) => m.outstanding.as_of_date);
  const f22 = Object.values(S.model?.states ?? {}).filter((m) => m.vs_2022_final?.ok).sort((a, b) => b.vs_2022_final.ratio - a.vs_2022_final.ratio);
  const f22Text = f22.length ? `Against the 2022 final pre-election report (not same day): ${f22.map((m) => `${m.st} ${x2(m.vs_2022_final.ratio)} (${dayShort(m.vs_2022_final.now_date)} vs ${dayShort(m.vs_2022_final.date_2022)})`).join(", ")}.` : "";
  const cards = [
    card("Ballots already cast", bigNum(tot), `${int(tot)} early and mail ballots counted by ${have.length} of ${cs.length} states and DC.`, `Counted through ${dayShort(newest)}${oldest !== newest ? ` (oldest state: ${dayShort(oldest)})` : ""}`),
    w22.length ? card("Against 2022's final total", pc(100 * now22 / cert), `Those ballots, in the ${w22.length} states with a 2022 total, are ${bigNum(now22)} of ${bigNum(cert)} votes cast in those states in 2022.`, `As of ${dayShort(newest)}`) : "",
    pc22.length ? card("Against 2022, same point", `${up.length} above, ${dn.length} below`, `Of ${pc22.length} states with a comparable 2022 figure on the same day, ${up.length} have more ballots in than at this point in 2022 and ${dn.length} fewer (${x2(d3.min(pc22, (c) => c.pace.pace))} to ${x2(d3.max(pc22, (c) => c.pace.pace))}).`, `Same days before Election Day, as of ${dayShort(d3.max(pc22, (c) => c.pace.now.date))}`) : "",
    f22.length ? card("Against 2022, final pre-election report", `${f22.filter((m) => m.vs_2022_final.ratio > 1).length} of ${f22.length} above`, esc(f22Text), `Each pair is dated; the 2022 report is the last before Election Day, so the days before the election differ`) : "",
    out.length ? card("Mail ballots not yet returned", bigNum(outTot), `${int(outTot)} ballots sent or requested and not yet returned, in the ${out.length} states with both figures (largest: ${esc(out.slice(0, 4).map((m) => `${m.st} ${bigNum(m.outstanding.value)}`).join(", "))}). States that mail every voter are left out. Some will vote in person instead.`, `Sent or requested minus returned, as of ${dayShort(outNew)}${outOld !== outNew ? ` (oldest state: ${dayShort(outOld)})` : ""}`, true) : "",
    TD.day ? card("Election Day check-ins so far", edTot ? bigNum(edTot) : "none yet", edTot ? `${int(edTot)} people checked in on Election Day, in the ${days.length} states that publish it.` : "No state has published an Election Day count in the file yet.", `Checked ${esc(stamp(edAt || TD.day.published_utc))}`) : "",
    party.length ? card("Party mix of banked ballots", `${dl.length} D · ${rl.length} R`, `Of ${party.length} states that publish party registration, registered Democrats hold the larger share of returned ballots in ${dl.length} and registered Republicans in ${rl.length}. Registration, not votes.`, `Through ${dayShort(d3.max(party, (c) => c.as_of))}`) : "",
    mod.length ? card("Against the models' expected total", pc(100 * mB / mE), `Banked ballots are ${bigNum(mB)} of the ${bigNum(mE)} votes the models expect in the ${mod.length} states with both figures.`, `As of ${esc(stamp(S.model.generated_utc))}`, true) : "",
  ].join("");
  $("#kf-cards").innerHTML = cards;
  const top = (k, dir) => w22.slice().sort((a, b) => dir * (b[k] - a[k])).slice(0, 3).map((c) => `${c.name} ${pc(c.pct22)}`).join(", ");
  const li = [];
  li.push(`<b>Furthest along against 2022:</b> ${esc(top("pct22", 1))} of their 2022 total votes already cast.`);
  li.push(`<b>Least far along:</b> ${esc(top("pct22", -1))}; some of these publish only part of the early vote.`);
  if (pc22.length) li.push(`<b>Above 2022 pace:</b> ${esc(names(up) || "none")}. <b>Below:</b> ${esc(names(dn) || "none")}.`);
  if (party.length) li.push(`<b>Registered-party lead of banked ballots:</b> Democratic in ${esc(names(dl) || "none")}; Republican in ${esc(names(rl) || "none")}. Registration-based.`);
  const v = TD.vol;
  if (v?.verdict) {
    const first = v.verdict.split(":")[0].replace(/\.$/, "");
    li.push(`<b>Does turnout volume say anything about the result?</b> Past test: “${esc(first.length > 90 ? `${first.slice(0, 87)}…` : first)}”${v.n_cases != null ? ` (${esc(String(v.n_cases))} past races)` : ""}. Full wording in the details below.`);
  }
  $("#kf-list").innerHTML = li.map((x) => `<li>${x}</li>`).join("");
  $("#kf-asof").textContent = `Generated from the published files: counts through ${dayLong(newest)}, fetched ${stamp(S.index.built)}${TD.day ? `; turnout file ${stamp(TD.day.generated_utc)}` : ""}.`;
  $("#kf-more").innerHTML = `<p><b>States in the ballot total (${have.length}):</b> ${esc(have.map((c) => `${c.st.toUpperCase()} ${bigNum(c.total)}`).join(", "))}.</p>` +
    `<p><b>No count yet (${none.length}):</b> ${esc(names(none) || "none")}. States differ in what they count (mail only, in-person only, or both), so the total is a sum of each state's own published figure, not one definition.</p>` +
    (w22.length ? `<p><b>States with a 2022 total (${w22.length}):</b> ${esc(names(w22))}.</p>` : "") +
    (suspect22().length ? `<p><b>Left out of the 2022 comparison:</b> ${suspect22().map((c) => `${esc(c.name)} (${num(c.total)} ballots already exceed the 2022 total of ${num(c.pace?.cert22 ?? (c.total * 100) / c.pct22)} in the source, so that total looks too low)`).join("; ")}.</p>` : "") +
    (pc22.length ? `<p><b>Same-point comparison:</b> ${pc22.map((c) => `${esc(c.name)} ${x2(c.pace.pace)}`).join(", ")}.</p>` : "") +
    (days.length ? `<p><b>Election Day check-ins:</b> ${days.map(([s, n]) => `${esc(s.name)} ${int(n)}`).join(", ")}.</p>` : "") +
    (v?.verdict ? `<p><b>History verdict, in full:</b> ${esc(v.verdict)}${v.summary ? ` ${esc(v.summary)}` : ""}</p>` : "");
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

async function select(st) {
  S.st = st || null;
  if (!st) return;
  const u = new URL(location.href); u.searchParams.set("st", st); history.replaceState(null, "", u);
  $("#st-pick").value = st;
  $("#cov-table tbody").querySelectorAll("tr").forEach((r) => r.classList.toggle("sel", r.dataset.st === st));
  S.us?.select(st);
  $("#st-sec").hidden = false;
  for (const el of document.querySelectorAll("#st-chips, #st-uf, #st-sources")) el.hidden = false;
  updateSel();
  await drawState();
}

function chip(label, value, sub) { return `<span class="chip"><small>${esc(label)}</small><b>${value}</b>${sub ? `<i>${esc(sub)}</i>` : ""}</span>`; }

async function drawState() {
  const st = S.st, cov = S.index.states.find((c) => c.st === st);
  $("#st-h").textContent = cov.name;
  $("#st-status").innerHTML = `<span class="ev-pill s-${cov.status}">${STATUS[cov.status]}</span> ${cov.status === "collected" || cov.status === "compiled" ? `${level(cov)} data${cov.party ? ", with party registration" : ""}` : ""}`;
  const [s, cty] = await Promise.all([get(`state_${st}`, cov.has_state), get(`counties_${st}`, cov.has_counties)]);
  const none = !s;
  for (const id of ["#st-chips", "#c1", "#c2", "#c3", "#c4", "#c5", "#st-uf"]) $(id).replaceChildren();
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
  const tr = S.index.tracker, ufNote = (extra) => `<p class="ev-uf"><b>${esc(tr.label)}.</b> ${extra} <a href="${esc(tr.url)}" rel="noopener">UF Election Lab early-vote tracker</a>. ${esc(tr.licence)}.</p>`;
  $("#st-uf").innerHTML = !tr ? "" : s.compiled ? ufNote(`These figures are the Lab's, not an official count${cov.official ? `; the state's own count is ${OFFICIAL[cov.official.status] ?? "not collected"}` : ""}. Counted through the Lab's own update date for the state.`)
    : s.uf_check ? ufNote(`Cross-check: the Lab's count is ${num(s.uf_check.uf_total)} (through ${dayShort(s.uf_check.uf_date)}); the official count is ${num(s.uf_check.official_total)} (through ${dayShort(s.uf_check.official_date)}); the Lab is ${s.uf_check.diff === 0 ? "equal" : `${num(Math.abs(s.uf_check.diff))} ${s.uf_check.diff > 0 ? "above" : "below"} (${Math.abs(s.uf_check.diff_pct).toFixed(2)}%)`}. The official count is the one shown on this page.`) : "";
  const kindNote = s.compiled ? "One point per update the Lab published; days it did not update have no point." : s.daily_file ? "Day by day from the return date of every ballot in the official file." : "One point per official report; days a state did not publish have no point.";
  $("#st-basis").textContent = `${s.basis} ${kindNote}`;
  $("#st-sources").innerHTML = `<b>${s.compiled ? "Source" : "Official sources"}.</b> ${s.sources.map((x) => (x.url ? `<a href="${esc(x.url)}" rel="noopener">${esc(x.what || x.id)}</a>` : esc(x.what || x.id))).join("; ")}.`;
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
    if (d == null) { r1.textContent = ""; return; }
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
    if (d == null) { r2.textContent = ""; return; }
    const b = bars.find((q) => d >= q.x && d < q.x + (q.span || 1)); if (!b) { r2.textContent = `${d} days before Election Day: no change reported.`; return; }
    r2.textContent = `${dateLabel(b.x)}: +${num(col(s, "added_total")[b.i])} in ${b.span > 1 ? `${b.span} days` : "the day"}${splitAdd ? ` (${s.mail_label.toLowerCase()} ${num(col(s, "added_mail")[b.i])}, in person ${num(col(s, "added_inperson")[b.i])})` : ""}`;
  };
  $("#c2").closest("figure").toggleAttribute("hidden", !bars.length);
  barChart($("#c2"), say2, { bars, colors: { mail: COLORS.mail, inperson: COLORS.inperson, total: COLORS.total }, x });
  $("#c2").insertAdjacentHTML("afterbegin", keyHTML(splitAdd ? [[COLORS.mail, s.mail_label, 1], [COLORS.inperson, "In-person early votes", 1]] : [[COLORS.total, "All ballots", 1]])); say2(null);
  // 3 party lead
  if (s.has_party) {
    const ld = pts(s, "lead");
    $("#c3-sub").textContent = `${s.name}: the Democratic share minus the Republican share of all returned ballots, in percentage points. Above zero: more ballots from registered Democrats.`;
    const r3 = $("#c3-read"), say3 = (d) => { if (d == null) { r3.textContent = ""; return; } const i = dayRow(d); r3.textContent = i == null ? `${d} days before Election Day: no report.` : `${dateLabel(db[i])}: ${pt(col(s, "lead")[i])}`; };
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
    `<tr><td>${y.year}${y.uf ? " (UF)" : ""}</td><td>${y.midterm ? "midterm" : "presidential"}</td><td>${dayShort(y.date)}${y.days_used !== c.db ? ` <small>(${y.days_used} days before)</small>` : ""}</td><td class="n">${num(y.ballots)}</td><td class="n">${pc(y.pct_final)}</td><td class="n">${pc(y.now_pct_final)}</td><td class="n">${y.lead != null ? pt(y.lead) : "–"}</td></tr>` :
    `<tr><td>${y.year}</td><td>${y.midterm ? "midterm" : "presidential"}</td><td class="why" colspan="5">no comparison: ${esc(y.why)}</td></tr>`).join("")}</tbody>`;
  const withHist = ok.length > 0;
  $("#cmp-table").closest(".table-scroll").nextElementSibling.toggleAttribute("hidden", !withHist);
  $("#c4-read").toggleAttribute("hidden", !withHist);
  $("#cmp-note").textContent = withHist ? `${ok.some((y) => y.uf) ? "The past figure is the UF Election Lab's 2024 tracker file (one snapshot per state). " : ""}${c.votes_basis} ${c.kind === "inperson" ? "This state's past files count in-person early votes only, so the comparison uses this year's in-person count too." : ""} Presidential-year turnout runs well above midterm turnout: compare like with like (midterm with midterm).` : "";
  if (!withHist) return;
  const db = col(s, "db"), cur = db.map((d, i) => [d, col(s, c.kind === "inperson" ? "inperson" : "total")[i]]).filter((p) => p[0] <= 45);
  const ser = [{ name: "2026", color: COLORS.now, width: 3, pts: cur }, ...ok.map((y) => ({ name: String(y.year), color: CYCLE_COLORS[y.year], width: 1.8, dash: y.midterm ? null : "5 3", pts: y.curve.db.map((d, i) => [d, y.curve.cum[i]]) }))];
  const x = [Math.min(45, Math.max(...ser.flatMap((q) => q.pts.map((p) => p[0])))), 0];
  const key = keyHTML([[COLORS.now, "2026"], ...ok.map((y) => [CYCLE_COLORS[y.year], `${y.year}${y.midterm ? " (midterm)" : ""}`])]);
  const r = $("#c4-read"), say = (d) => {
    if (d == null) { r.textContent = "Dashed lines are presidential years."; return; }
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
  $("#cty-sub").textContent = `${cty.name}: ${cty.n} ${cty.n === 1 ? "county" : "counties"} with counts through ${dayShort(cty.as_of)}.${cty.measure !== "total" ? ` Only ${cty.measure === "inperson" ? "in-person early votes" : "mail ballots"} are published.` : ""}${S.index.states.find((c) => c.st === S.st)?.partial || S.index.states.find((c) => c.st === S.st)?.county_only_official ? " Only some county offices publish; the rest are not shown." : ""}`;
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
  const cols = [["name", "County"], ["total", cty.measure === "total" ? "Ballots" : cty.measure === "inperson" ? "In-person early votes" : "Mail ballots", "n"], ["mail", "Mail", "n"], ["inperson", "In person", "n"], ["added", "Added in last report", "n"], ["pct22", "% of 2022 votes", "n"], ...(cty.has_party ? [["lead", "D minus R", "n"]] : [])];
  const { key, dir } = S.cty.sort, rows = [...cty.counties].sort((a, b) => ((a[key] ?? -1e12) < (b[key] ?? -1e12) ? -1 : (a[key] ?? -1e12) > (b[key] ?? -1e12) ? 1 : 0) * dir);
  const fm = { total: num, mail: num, inperson: num, added: num, pct22: pc, lead: pt };
  $("#cty-table").innerHTML = `<thead><tr>${cols.map(([k, t, c]) => `<th class="${c ?? ""}" ${key === k ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}><button type="button" data-k="${k}">${t}</button></th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map(([k, , c]) => `<td class="${c ?? ""}">${k === "name" ? esc(r.name) : fm[k](r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("#cty-table").querySelectorAll("th button").forEach((b) => (b.onclick = () => { S.cty.sort = { key: b.dataset.k, dir: key === b.dataset.k ? -dir : b.dataset.k === "name" ? 1 : -1 }; drawCounties(cty); }));
}

// -------------------------------------------------------------------------------------------------------------- boot
async function boot() {
  const idx = (S.index = await getJSON("ev2026/index.json"));
  $("#ev-years").textContent = idx.years.join(", ");
  $("#tb-date").textContent = `Election Day ${dayShort(idx.election_day)}, ${idx.election_day.slice(0, 4)}`;
  const newest = idx.states.reduce((m, c) => (c.as_of && c.as_of > m ? c.as_of : m), "");
  $("#ev-asof").textContent = `Latest official counts run through ${dayLong(newest)}, ${idx.election_day.slice(0, 4)}; the files were last fetched ${stamp(idx.built)}. Election Day is ${dayLong(idx.election_day)}, ${dbOf(newest, idx.election_day)} days after that count.`;
  const pick = $("#st-pick");
  pick.innerHTML = `<option value="">Choose a state</option>` + idx.states.map((c) => `<option value="${c.st}">${esc(c.name)}${c.status === "collected" ? "" : ` (${STATUS[c.status]})`}</option>`).join("");
  pick.onchange = () => { if (pick.value) select(pick.value); };
  S.model = await getJSON("ev2026/turnout_model.json", { optional: true }).catch(() => null);
  drawCoverage();
  const td = initTurnout(drawFindings);
  drawFindings();
  const want = new URLSearchParams(location.search).get("st");
  S.st = idx.states.some((c) => c.st === want) ? want : null; // no state is open until one is chosen (a ?st= link opens it)
  await drawCoverageMap();
  $("#st-sec").hidden = false;
  if (S.st) await select(S.st);
  else {
    for (const el of document.querySelectorAll("#st-sec > figure, #st-chips, #st-uf, #st-sources")) el.hidden = true;
    $("#st-basis").textContent = "Choose a state on the map, in the table or here to see its charts and county detail.";
  }
  document.fonts?.ready;
  let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { if (S.st) drawState(); }, 200); });
  await td;
  window.__evReady = true;
}
boot().catch(window.__showLoadError ?? console.error);
