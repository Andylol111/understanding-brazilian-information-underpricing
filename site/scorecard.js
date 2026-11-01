// Track-record page: reads data/scorecard/{index,elections,backtest}.json (analysis/scorecard.py). Every number shown comes from
// those files; this module only formats and draws them.
import * as d3 from "./vendor/d3.js";
import { getJSON, esc, evidenceLabel } from "./common.js";

const $ = (s) => document.querySelector(s);
const MODEL = "#c4501b", HEAD = "#6a7f99", INK = "#1b1b1b";
const pc = (v, nd = 0) => (v == null ? "–" : `${(100 * v).toFixed(nd)}%`);
const pt = (v) => (v == null ? "–" : v.toFixed(1));
const f3 = (v) => (v == null ? "–" : v.toFixed(3));
const int = (v) => (v == null ? "–" : d3.format(",")(v));
const dur = (m) => {
  if (m == null) return "–";
  const sign = m < 0 ? "−" : "", a = Math.abs(Math.round(m));
  return a < 60 ? `${sign}${a} min` : `${sign}${Math.floor(a / 60)} h ${String(a % 60).padStart(2, "0")} min`;
};
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso) => (iso ? `${+iso.slice(8, 10)} ${MON[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}` : "–");
const plural = (n, one, many) => (n === 1 ? one : many);
const clean = (t) => String(t ?? "").replace(/`/g, "").replace(/\*\*/g, "");

// ---------------------------------------------------------------------------------------------------- definitions
function defs(D) {
  const cal = D.calibration_counted, cov = D.coverage_counted;
  const pts = D.error_points.map((x) => pc(x)).join(", ");
  return [
    `<b>Win chances (calibration).</b> Every minute of every race with ${pc(cal[0])} to ${pc(cal[1])} of the vote counted is one forecast: the model's chance that the first-named side wins. The forecasts are sorted into ${D.bins} equal bins; the table gives the average forecast in each bin and how often that side did win. A well-calibrated model wins about as often as it says. Each election carries the same total weight, so a long night does not count for more than a short one; the Brier score is the average squared miss; lower is better.`,
    `<b>Error of the projected share.</b> The projected final share of the first-named side at the first minute with ${pts} of the vote counted, minus the official final share, in percentage points (average absolute miss). The unadjusted count, the running total on the results page, is scored at the same minutes.`,
    `<b>The 90% band.</b> Of the minutes with ${pc(cov[0])} to ${pc(cov[1])} counted, the share in which the final result lay inside the model's 90% band. A calibrated band holds about as often as it claims.`,
    `<b>Time to 90% on the winner.</b> The first minute from which the model, and each market, stay at ${D.settle_level} or more on the eventual winner, counted from ${pc(D.gate)} of the vote. The lead is the market's time minus the model's. A market is “decided before the count” if the eventual winner was already priced at ${D.decided.close} or more at the first minute at or after poll close and never fell below ${D.decided.floor} afterwards; those markets are left out of the lead.`,
    `<b>Evidence labels.</b> A <em>forward test</em> is registered in the repository before the result exists. A <em>replay</em> runs the same model on a night that has already happened; if its settings were frozen before the data was examined it is “pre-specified”, if they were chosen on earlier elections but not frozen in advance it is “not pre-registered”, and if they were tuned on that very night it is “in-sample”. A <em>variation chosen after the result</em> is shown for what it teaches, never as a forecast.`,
  ];
}

// ------------------------------------------------------------------------------------------------------ chart
function reliability(host, series) {
  const W = 480, H = 420, m = { l: 52, r: 14, t: 12, b: 48 };
  const x = d3.scaleLinear([0, 1], [m.l, W - m.r]), y = d3.scaleLinear([0, 1], [H - m.b, m.t]);
  const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img")
    .attr("aria-label", "Calibration: forecast win chance against how often that side won");
  const axis = svg.append("g").attr("class", "axis");
  const ticks = d3.range(0, 1.01, 0.2);
  axis.selectAll("line.g").data(ticks).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", (t) => y(t)).attr("y2", (t) => y(t)).attr("stroke", "#ece9e1");
  axis.selectAll("text.x").data(ticks).join("text").attr("x", (t) => x(t)).attr("y", H - m.b + 18).attr("text-anchor", "middle").text((t) => pc(t));
  axis.selectAll("text.y").data(ticks).join("text").attr("x", m.l - 8).attr("y", (t) => y(t) + 4).attr("text-anchor", "end").text((t) => pc(t));
  svg.append("text").attr("class", "panel-label").attr("x", (m.l + W - m.r) / 2).attr("y", H - 8).attr("text-anchor", "middle").text("Forecast chance that the first-named side wins");
  svg.append("text").attr("class", "panel-label").attr("transform", `translate(14 ${(m.t + H - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text("How often it did win");
  svg.append("line").attr("class", "sc-diag").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1));
  const all = series.flatMap((s) => s.bins);
  const rad = d3.scaleSqrt([0, d3.max(all, (b) => b.n_minutes) || 1], [0, 11]);
  for (const s of series) {
    const pts = s.bins.filter((b) => b.forecast != null);
    svg.append("path").attr("class", "sc-line").attr("stroke", s.color).attr("opacity", 0.8)
      .attr("d", d3.line().x((b) => x(b.forecast)).y((b) => y(b.observed))(pts));
    svg.selectAll(null).data(pts).join("circle").attr("class", "sc-pt").attr("fill", s.color).attr("cx", (b) => x(b.forecast)).attr("cy", (b) => y(b.observed))
      .attr("r", (b) => Math.max(2.5, rad(b.n_minutes))).append("title")
      .text((b) => `${s.name}: forecast ${pc(b.forecast, 1)}, won ${pc(b.observed, 1)}, ${int(b.n_minutes)} minutes from ${b.n_elections} ${plural(b.n_elections, "election", "elections")}`);
  }
  const legend = document.createElement("div");
  legend.className = "sc-legend";
  legend.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("") + `<span>Dashed: forecast equals result. Dot area: minutes.</span>`;
  host.append(legend);
}

const calTable = (bins) => `<div class="table-scroll"><table class="sc-t"><thead><tr><th>Forecast bin</th><th class="n">Average forecast</th><th class="n">Side won</th><th class="n">Minutes</th><th class="n">Elections</th></tr></thead><tbody>${
  bins.map((b) => `<tr><td>${pc(b.lo)} to ${pc(b.hi)}</td><td class="n">${pc(b.forecast, 1)}</td><td class="n">${pc(b.observed, 1)}</td><td class="n">${int(b.n_minutes)}</td><td class="n">${b.n_elections || "–"}</td></tr>`).join("")}</tbody></table></div>`;

// -------------------------------------------------------------------------------------------------- shared pieces
const errKeys = (D) => D.error_points.map((x) => `${Math.round(100 * x)}%`);
function errTable(D, pooled, label = "Projected share") {
  const ks = errKeys(D);
  const cell = (e, k) => (e[k] ? pt(e[k].mae) : "–"), raw = (e, k) => (e[k]?.raw_mae != null ? pt(e[k].raw_mae) : "–");
  return `<div class="table-scroll"><table class="sc-t"><thead><tr><th>Average miss, points, at this much counted</th>${ks.map((k) => `<th class="n">${k}</th>`).join("")}</tr></thead><tbody>
    <tr><td>${esc(label)}</td>${ks.map((k) => `<td class="n">${cell(pooled.share_error, k)}</td>`).join("")}</tr>
    <tr><td>Unadjusted count</td>${ks.map((k) => `<td class="n">${raw(pooled.share_error, k)}</td>`).join("")}</tr>
    <tr><td class="sub">Elections scored</td>${ks.map((k) => `<td class="n sub">${pooled.share_error[k]?.n_elections ?? "–"}</td>`).join("")}</tr></tbody></table></div>`;
}
function facts(pooled) {
  const mk = pooled.market;
  const out = [`<span><b>${pooled.n_elections}</b>${plural(pooled.n_elections, "election", "elections")}</span>`,
    `<span><b>${f3(pooled.brier)}</b>Brier score</span>`,
    `<span><b>${pc(pooled.coverage90)}</b>of minutes the 90% band held (${pooled.n_coverage_elections} ${plural(pooled.n_coverage_elections, "election", "elections")})</span>`];
  if (mk && mk.n_markets) {
    out.push(`<span><b>${mk.n_decided_before_count} of ${mk.n_markets}</b>markets decided before the count</span>`);
    if (mk.n_compared) out.push(`<span><b>${mk.model_first} of ${mk.n_compared}</b>contested markets: the model reached 90% first${mk.tied ? ` (${mk.tied} level)` : ""}${mk.median_lead_min != null ? `, median lead <b style="display:inline;font-size:inherit">${dur(mk.median_lead_min)}</b>` : ""}</span>`);
  }
  return `<div class="sc-facts">${out.join("")}</div>`;
}
const leadText = (m) => (!m ? "–" : m.n_compared ? `${m.model_first} of ${m.n_compared}, ${dur(m.median_lead_min)}` : "–");
function electionRows(D, recs, vid = "v1", opts = {}) {
  const ks = errKeys(D);
  return recs.filter((r) => r.versions[vid]).map((r, i) => {
    const v = r.versions[vid];
    const mk = v.market;
    return `<tr${opts.sep && i === 0 ? ' class="sc-sep"' : ""}><td class="sc-name"><a href="election.html?id=${esc(r.id)}">${esc(r.name)}</a>${opts.versionLabel ? `<br><span class="sub">${esc(v.label)}</span>` : ""}</td>
      <td class="nw">${day(r.date)}</td><td>${!opts.versionLabel && ["in-sample", "pre-specified", "not-preregistered"].includes(r.evidence) ? evidenceLabel(r.evidence) : ""}</td><td class="n">${v.n_races_scored}</td><td class="n">${f3(v.brier)}</td><td class="n">${pc(v.coverage90)}</td>
      ${ks.map((k) => `<td class="n">${v.share_error[k] ? `${pt(v.share_error[k].mae)}<span class="sub"> (${pt(v.share_error[k].raw_mae)})</span>` : "–"}</td>`).join("")}
      <td class="n">${mk && mk.n_markets ? `${mk.n_decided_before_count} of ${mk.n_markets}` : "–"}</td><td class="n">${leadText(mk)}</td></tr>`;
  }).join("");
}
function electionTable(D, recs, vid = "v1", opts = {}) {
  const ks = errKeys(D);
  return `<div class="table-scroll"><table class="sc-t"><thead><tr><th>Election</th><th>Date</th><th>Evidence</th><th class="n">Races</th><th class="n">Brier</th><th class="n">90% band held</th>${ks.map((k) => `<th class="n">Share miss at ${k}</th>`).join("")}<th class="n">Decided before the count</th><th class="n">Model first, median lead</th></tr></thead><tbody>${electionRows(D, recs, vid, opts)}</tbody></table></div>
    <p class="note">Share miss: average absolute miss of the projected share in points, with the unadjusted count in brackets. Elections with no count over time have no row here.</p>`;
}

function groupBlock(D, I, gid, recs) {
  const g = I.groups[gid], pooled = g.pooled.v1;
  const evs = [...new Set(recs.map((r) => r.evidence))];
  const note = evs.filter((e) => ["forward", "pre-specified", "not-preregistered", "in-sample"].includes(e)).map((e) => I.evidence_text[e]).filter(Boolean).join("; ");
  if (!pooled) return `<h3>${esc(g.title)}</h3><p class="sc-empty">None scored yet.</p>`;
  const host = document.createElement("div");
  host.innerHTML = `<h3>${esc(g.title)}</h3>${note ? `<p class="sc-lede">${esc(note[0].toUpperCase() + note.slice(1))}.</p>` : ""}${facts(pooled)}
    <div class="sc-grid"><div class="sc-chart" data-chart></div><div>${calTable(pooled.calibration)}</div></div>
    ${errTable(D, pooled)}${electionTable(D, recs)}`;
  reliability(host.querySelector("[data-chart]"), [{ name: "Headline model", color: MODEL, bins: pooled.calibration }]);
  return host;
}

// ---------------------------------------------------------------------------------------------------- sections
function forward(D, I, E) {
  const body = $("#fwd-body");
  const done = E.filter((r) => r.group === "forward");
  body.innerHTML = `<p class="sc-lede">${I.groups.forward.pooled.v1 ? "Models registered before the count; each is scored below as soon as its night is counted." : "A forward test fixes every setting in the repository before the night, so the result cannot have shaped it. None of the registered nights has been counted yet, so there is nothing to score; each is listed below and will appear here with the same tables as the replays."}</p>`;
  if (I.pending_forward.length) {
    body.insertAdjacentHTML("beforeend", `<ul class="sc-pending">${I.pending_forward.map((p) => `<li><a href="election.html?id=${esc(p.id)}">${esc(p.name)}</a><span class="d">${day(p.date)}</span></li>`).join("")}</ul>`);
  }
  if (done.length && I.groups.forward.pooled.v1) {
    const b = groupBlock(D, I, "forward", done);
    body.append(b);
  }
}

function replays(D, I, E) {
  const body = $("#rep-body");
  $("#rep-lede").textContent = "The model run on nights that have already happened, as if the count were arriving live: it sees only the places counted by each minute, and the markets only the prices quoted by then. These are not forecasts made in advance. Nights whose settings were frozen before the data was examined are kept apart from the rest, and from nights the settings were tuned on.";
  for (const gid of ["replay_prespecified", "replay_other", "replay_in_sample"]) {
    const recs = E.filter((r) => r.group === gid && r.versions.v1);
    if (!recs.length) continue;
    body.append(groupBlock(D, I, gid, recs));
  }
  const noCount = E.filter((r) => !r.has_count_timeline && r.group !== "forward");
  if (noCount.length) {
    body.insertAdjacentHTML("beforeend", `<p class="note">No count over time exists for ${noCount.map((r) => `<a href="election.html?id=${esc(r.id)}">${esc(r.name)}</a>`).join(", ")}, so they have no model scores here.</p>`);
  }
}

function sensitivities(D, I, E) {
  const g = I.groups.sensitivity, body = $("#sens-body");
  $("#sens-lede").textContent = "Each variation below was designed or chosen after the result of the night it is scored on was known. It shows what a different choice would have done, not what the model would have said. The headline model is shown beside every variation on exactly the same elections.";
  const vs = Object.entries(g.versions ?? {});
  if (vs.length) {
    const ks = errKeys(D);
    const rows = vs.map(([vid, v]) => {
      const a = v.same_elections_headline, b = v.pooled;
      const row = (name, p, cls) => `<tr${cls}><td>${esc(name)}</td><td class="n">${b.n_elections}</td><td class="n">${f3(p.brier)}</td><td class="n">${pc(p.coverage90)}</td>${ks.map((k) => `<td class="n">${p.share_error[k] ? pt(p.share_error[k].mae) : "–"}</td>`).join("")}<td class="n">${p.market?.n_compared ? `${p.market.model_first} of ${p.market.n_compared}, ${dur(p.market.median_lead_min)}` : "–"}</td></tr>`;
      return row(`Headline model, same elections as ${v.label.split(" (")[0]}`, a, ' class="sc-sep"') + row(v.label, b, "");
    }).join("");
    body.insertAdjacentHTML("beforeend", `<h3>Pooled over every replay that has the variation</h3><div class="table-scroll"><table class="sc-t"><thead><tr><th>Version</th><th class="n">Elections</th><th class="n">Brier</th><th class="n">90% band held</th>${ks.map((k) => `<th class="n">Share miss at ${k}</th>`).join("")}<th class="n">Model first, median lead</th></tr></thead><tbody>${rows}</tbody></table></div>`);
    const grid = document.createElement("div");
    grid.className = "sc-grid";
    for (const [vid, v] of vs) {
      const cell = document.createElement("div");
      cell.innerHTML = `<p class="chart-title">${esc(v.label)}</p><div class="sc-chart"></div>`;
      grid.append(cell);
      reliability(cell.querySelector(".sc-chart"), [{ name: "Headline model, same elections", color: HEAD, bins: v.same_elections_headline.calibration }, { name: v.label.split(" (")[0], color: MODEL, bins: v.pooled.calibration }]);
    }
    body.append(grid);
  }
  const recs = E.filter((r) => r.group !== "forward");
  const post = [];
  for (const r of recs) for (const vid of Object.keys(r.versions)) if (vid !== "v1" || r.group === "sensitivity") post.push([r, vid]);
  const rowsHtml = post.map(([r, vid], i) => electionRows(D, [r], vid, { versionLabel: true, sep: false })).join("");
  if (rowsHtml) {
    const ks = errKeys(D);
    body.insertAdjacentHTML("beforeend", `<h3>Every night and variation</h3><div class="table-scroll"><table class="sc-t"><thead><tr><th>Election and version</th><th>Date</th><th>Evidence</th><th class="n">Races</th><th class="n">Brier</th><th class="n">90% band held</th>${ks.map((k) => `<th class="n">Share miss at ${k}</th>`).join("")}<th class="n">Decided before the count</th><th class="n">Model first, median lead</th></tr></thead><tbody>${rowsHtml}</tbody></table></div>`);
  }
  const sensElections = E.filter((r) => r.group === "sensitivity");
  if (sensElections.length) body.insertAdjacentHTML("beforeend", `<p class="note">${sensElections.map((r) => `<a href="election.html?id=${esc(r.id)}">${esc(r.name)}</a>`).join(", ")} ${plural(sensElections.length, "is a variation", "are variations")} chosen after the result and scored separately; the headline version of that night is in the replays above.</p>`);
}

const COLS = { subset: "Subset", label: "Version", n: "Races", mae: "Average miss (points)", rmse: "Root-mean-square miss (points)", brier: "Brier", logloss: "Log loss", cov90: "90% band held", crps: "CRPS", sd_mean: "Average band sd (points)",
  cycle: "Cycle", model: "Version", races: "Races", wRMSE_pts: "Vote-weighted RMSE (points)", wMAE_pts: "Vote-weighted MAE (points)", RMSE_pts_unweighted: "Unweighted RMSE (points)",
  county_share_wMAE_pts: "County share MAE (points)", county_share_wRMSE_pts: "County share RMSE (points)", turnout_share_TV_pct: "Turnout share distance (%)", state_votes_abs_logerr: "Votes, absolute log error", state_votes_logerr_bias: "Votes, log bias" };
function btCell(v, col) {
  if (col === "cycle") return esc(v);
  if (typeof v === "number") return Number.isInteger(v) ? int(v) : v.toFixed(3);
  return esc(v);
}
function backtest(I, B) {
  const body = $("#bt-body");
  const sections = Object.fromEntries(B.sections.map((s) => [s.id, s.tables]));
  const cycles = [...new Set((sections.statewide ?? []).concat(sections.house ?? []).flatMap((t) => t.rows.map((r) => r.cycle)).filter((c) => Number.isInteger(c)))].sort();
  if (cycles.length) $("#bt-h").textContent = `Before any count: the US backtest, ${cycles[0]} to ${cycles[cycles.length - 1]}`;
  $("#bt-lede").textContent = "A different test from the count nights above: forecasts made before a single county was counted, for past US statewide races and House districts, each scored on a cycle the model was not fitted on. The registered baselines are v1 (last result) and v3 (poll average); the unified model combines every signal into one prior. Tables are copied from the backtest documentation, which can be rebuilt from public data.";
  const titles = { statewide: "Statewide races (Senate and governor), leave one cycle out", county: "County priors", house: "House districts, leave one cycle out" };
  for (const sid of ["statewide", "house", "county"]) {
    const tables = sections[sid] ?? [];
    if (!tables.length) continue;
    body.insertAdjacentHTML("beforeend", `<h3>${titles[sid]}</h3>`);
    for (const t of tables) {
      const num = (c) => t.rows.some((r) => typeof r[c] === "number");
      body.insertAdjacentHTML("beforeend", `<p class="sc-bt-note">${esc(clean(t.title))}</p><div class="table-scroll"><table class="sc-t"><thead><tr>${t.columns.map((c) => `<th${num(c) ? ' class="n"' : ""}>${esc(COLS[c] ?? c)}</th>`).join("")}</tr></thead><tbody>${
        t.rows.map((r) => `<tr>${t.columns.map((c) => `<td${typeof r[c] === "number" ? ' class="n"' : ""}>${btCell(r[c], c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    }
  }
}

// ------------------------------------------------------------------------------------------------------- start
const [I, E, B] = await Promise.all([getJSON("scorecard/index.json"), getJSON("scorecard/elections.json"), getJSON("scorecard/backtest.json")]);
$("#sc-asof").textContent = `Generated ${I.generated.slice(0, 10)} from the data files of ${E.length + I.pending_forward.length} elections.`;
$("#sc-defs").innerHTML = defs(I.definitions).map((t) => `<li>${t}</li>`).join("");
forward(I.definitions, I, E);
replays(I.definitions, I, E);
sensitivities(I.definitions, I, E);
backtest(I, B);
window.__scReady = true;
