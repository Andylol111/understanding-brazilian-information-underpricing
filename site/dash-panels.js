// Right-hand panels of the dashboard: the readout at the current time, and the three tabs
// (races, statistics, method & sources). Pure rendering from values the controller passes in.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { C, esc, pct, num, usd, clock, tzName, evidenceLabel, EVIDENCE, leadMinutes } from "./common.js";

const PLATFORM = { polymarket: "Polymarket", kalshi: "Kalshi" };
const signed = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);

/* ---------------------------------------------------------------- readout */

export function renderReadout(el, { race, row, t, preview, tz, countTimeline, frameT }) {
  const a = esc(race.a.name);
  const head = `<p class="ro-head"><span class="ro-at">${preview ? "Preview at" : "At"} <b>${clock(t, tz)}</b> ${esc(tzName(tz, t))}</span><span class="ro-race">${esc(race.name)} · figures for ${a}</span></p>`;
  if (!row) { el.innerHTML = `${head}<p class="note">No data for this race at this time.</p>`; return; }
  const band = row.sd != null && row.share != null ? `<small>± ${(164.5 * row.sd).toFixed(1)} pt (90%)</small>` : "";
  const market = row.pm ?? row.kalshi;
  const marketName = row.pm != null ? "Polymarket" : "Kalshi";
  const figs = [];
  if (countTimeline) {
    figs.push(`<div><dt>Counted</dt><dd>${pct(row.counted)}</dd></div>`);
    figs.push(`<div><dt>Raw count</dt><dd class="r">${pct(row.raw)}</dd></div>`);
    figs.push(`<div><dt>Model projection</dt><dd class="m">${pct(row.share)}${band}</dd></div>`);
  }
  const pParts = [];
  if (row.P != null) pParts.push(`<span class="m">${num(row.P)}</span><small>model</small>`);
  if (row.pm != null) pParts.push(`<span class="k">${num(row.pm)}</span><small>Polymarket</small>`);
  if (row.kalshi != null) pParts.push(`<span class="k">${num(row.kalshi)}</span><small>Kalshi</small>`);
  figs.push(`<div class="ro-p"><dt>Chance of winning</dt><dd>${pParts.join(" ") || "–"}</dd></div>`);
  let gap = "";
  if (row.P != null && market != null) {
    const g = row.P - market;
    gap = `Model ${num(row.P)}, ${marketName} ${num(market)}: gap <b>${num(Math.abs(g))}</b>${Math.abs(g) >= 0.005 ? `, model ${g > 0 ? "higher" : "lower"}` : ""}.`;
  } else if (row.P != null) gap = "No market price at this time.";
  const frame = frameT != null && countTimeline ? ` <span class="ro-frame">Map: ${clock(frameT, tz)} frame.</span>` : "";
  el.innerHTML = `${head}<dl class="ro-figs">${figs.join("")}</dl>${gap || frame ? `<p class="ro-gap">${gap}${frame}</p>` : ""}`;
}

/* ---------------------------------------------------------------- races tab */

// leadOf(race) -> minutes the model reached 90% on the winner before Polymarket, both measured from
// the race's 1% gate (see gatedSettle in common.js); null when not computable.
const SORT = { key: "order", dir: 1 }; // kept across rebuilds (live refresh)

export function createRaceList(el, { races, raceName, leadOf, onSelect }) {
  const st = { values: new Map() };
  const lead = (r) => leadOf(r);
  const cols = [
    ["name", "Race", false], ["P", "Model", true], ["m", "Market", true], ["gap", "Gap", true], ["lead", "Lead from 1% counted (min)", true],
  ];
  el.innerHTML = `<table class="races"><thead><tr>${cols.map(([k, label, n]) => `<th class="${n ? "n" : ""}" aria-sort="${SORT.key === k ? (SORT.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${label}</button></th>`).join("")}</tr></thead><tbody></tbody></table>
    <p class="note">Model: the model's probability that the first-named candidate wins, at the scrubber time. Market: Polymarket price for the same outcome. Gap: model minus market. Lead: minutes by which the model reached 90% on the winner before Polymarket did, both counted from the moment the race reached 1% counted (negative: after).</p>`;
  const tbody = el.querySelector("tbody");
  const rows = races.map((r, order) => {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.race = r.race_id;
    tr.innerHTML = `<td>${esc(raceName(r))}</td><td class="n m"></td><td class="n"></td><td class="n"><span class="gapbar"><i></i></span></td><td class="n">${lead(r) == null ? "–" : signed(lead(r), 0)}</td>`;
    tr.addEventListener("click", () => onSelect(r));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") onSelect(r); });
    return { r, tr, order, cells: tr.children };
  });
  rows.forEach((x) => tbody.append(x.tr));
  el.querySelectorAll("th button").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.key;
    SORT.dir = SORT.key === k ? -SORT.dir : k === "name" ? 1 : -1;
    SORT.key = k;
    el.querySelectorAll("th").forEach((th) => th.setAttribute("aria-sort", "none"));
    b.parentElement.setAttribute("aria-sort", SORT.dir > 0 ? "ascending" : "descending");
    sort();
  }));
  function val(x) {
    const v = st.values.get(x.r.race_id) ?? {};
    return { order: x.order, name: x.r.name, P: v.P, m: v.m, gap: v.P != null && v.m != null ? Math.abs(v.P - v.m) : null, lead: lead(x.r) }[SORT.key];
  }
  function sort() {
    const sorted = [...rows].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null && vb == null) return a.order - b.order;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * SORT.dir;
    });
    sorted.forEach((x) => tbody.append(x.tr));
  }
  return {
    update(valuesByRace) {
      st.values = valuesByRace;
      for (const x of rows) {
        const v = valuesByRace.get(x.r.race_id) ?? {};
        x.cells[1].textContent = num(v.P);
        x.cells[2].textContent = num(v.m);
        const bar = x.cells[3].firstChild.firstChild;
        if (v.P != null && v.m != null) {
          const g = v.P - v.m;
          bar.style.width = `${Math.min(50, Math.abs(g) * 50)}%`;
          bar.style.left = g >= 0 ? "50%" : `${50 - Math.min(50, Math.abs(g) * 50)}%`;
          bar.style.background = g >= 0 ? C.model : C.market;
          x.cells[3].title = `gap ${signed(g, 2)}`;
        } else bar.style.width = "0";
      }
      if (SORT.key !== "order") sort();
    },
    select(raceId) {
      for (const x of rows) x.tr.classList.toggle("sel", x.r.race_id === raceId);
    },
  };
}

/* ---------------------------------------------------------------- statistics tab */

export function renderStats(el, { stats, race, races, raceName, tz, arb, settleOf }) {
  if (!stats) { el.innerHTML = `<p class="note">No statistics for this election yet.</p>`; return; }
  const ev = (k) => (stats.evidence?.[k] ? evidenceLabel(stats.evidence[k]) : "");
  const reasons = stats.reasons ?? stats.null_reasons ?? {};
  const why = (k) => reasons[k] ?? "not available";
  const t = (iso) => (iso ? clock(iso, tz) : "–");
  const id = race.race_id;
  const cal = stats.calibration?.[id], set = stats.settle?.[id], ee = stats.early_error?.[id];
  const line = (label, metric, body) => `<div class="st-row"><dt>${label} ${ev(metric)}</dt><dd>${body}</dd></div>`;
  const na = (k) => `<span class="na">Not computed: ${esc(why(k))}.</span>`;

  const raceRows = [
    line("In the model's 90% band", "calibration", stats.calibration == null ? na("calibration") : cal
      ? `${pct(cal.coverage90_1_95, 0)} of minutes from 1% to 95% counted; mean error ${signed(cal.mean_z, 2)} sd`
      : na(`calibration.${id}`)),
    line("Settled at 90% on the winner", "settle", stats.settle == null ? na("settle") : set
      ? `model ${t(set.model)} · Polymarket ${set.polymarket ? t(set.polymarket) : "–"} · raw count ${t(set.raw)}${leadMinutes(settleOf(id)) != null ? ` · from 1% counted, the model was ${Math.abs(leadMinutes(settleOf(id)))} min ${leadMinutes(settleOf(id)) >= 0 ? "earlier" : "later"}` : ""}`
      : na(`settle.${id}`)),
    line("Early error, model / raw count (points)", "early_error", stats.early_error == null ? na("early_error") : ee
      ? ["1%", "5%", "10%", "25%"].map((c) => `${c}: <span class="m">${signed(ee[c]?.model)}</span> / <span class="r">${signed(ee[c]?.raw)}</span>`).join(" · ")
      : na(`early_error.${id}`)),
  ];

  const plat = (metric, fmt) => {
    const v = stats[metric];
    if (v == null) return na(metric);
    return Object.entries(v).map(([p, x]) => `${PLATFORM[p] ?? esc(p)}: ${x ? fmt(x) : `<span class="na">${esc(why(`${metric}.${p}`))}</span>`}`).join("<br>");
  };
  const a = stats.arbitrage;
  const allRows = [
    line("Catch-up in 30 min", "catchup", plat("catchup", (x) => `${num(x.slope30)} of the gap (95%: ${num(x.lo)} to ${num(x.hi)}), ${x.markets} markets, ${x.n?.toLocaleString("en-US")} market-minutes`)),
    line("Fillable at the signal price / +2¢ / +5¢", "capacity", plat("capacity", (x) => `${usd(x.signal)} / ${usd(x.slip2c)} / ${usd(x.slip5c)}`)),
    line("Polymarket–Kalshi arbitrage", "arbitrage", a == null ? na("arbitrage")
      : `${a.pairs} pairs; ${a.minutes_with_net_arb} minutes with a net edge; largest ${a.max_net_edge != null ? `${(100 * a.max_net_edge).toFixed(1)}¢` : "–"} per $1; ${usd(a.fillable_usd)} fillable`),
  ];

  const others = races.filter((r) => r.race_id !== id && (stats.calibration?.[r.race_id] || stats.settle?.[r.race_id]));
  const othersTable = others.length ? `<details class="st-others"><summary>Other races (${others.length})</summary>
    <table class="compact"><thead><tr><th>Race</th><th class="n">In band</th><th class="n">Mean error</th><th class="n">Lead from 1% (min)</th></tr></thead><tbody>${others.map((r) => {
      const c = stats.calibration?.[r.race_id];
      const l = leadMinutes(settleOf(r.race_id));
      return `<tr><td>${esc(raceName(r))}</td><td class="n">${c ? pct(c.coverage90_1_95, 0) : "–"}</td><td class="n">${c ? signed(c.mean_z, 2) : "–"}</td><td class="n">${l == null ? "–" : signed(l, 0)}</td></tr>`;
    }).join("")}</tbody></table></details>` : "";

  const extras = Object.entries(stats).filter(([k, v]) => !KNOWN.has(k) && v != null);
  const extraHTML = extras.length ? `<h3 class="tab-h">Further measures for this election</h3><dl class="st">${extras.map(([k, v]) =>
    `<div class="st-row"><dt>${esc(k.replace(/_/g, " "))} ${ev(k)}</dt><dd>${flat(v)}</dd></div>`).join("")}</dl>` : "";

  el.innerHTML = `${stats.evidence_note ? `<p class="st-evnote">${esc(stats.evidence_note)}</p>` : ""}
    ${auditHTML(stats, race, raceName)}
    <h3 class="tab-h">${esc(raceName(race))}</h3><dl class="st">${raceRows.join("")}</dl>
    <h3 class="tab-h">All markets in this election</h3><dl class="st">${allRows.join("")}</dl>
    ${arb?.length ? arbHTML(arb, raceName, races) : ""}
    ${extraHTML}
    ${othersTable}
    <p class="note">The same measures are computed for every election; the <a href="./#measures">overview page</a> defines them.</p>`;
}

const KNOWN = new Set(["calibration", "settle", "settle_gated", "catchup", "early_error", "capacity", "arbitrage", "evidence", "evidence_note", "reasons", "null_reasons", "accuracy", "profit"]);

/* ---------------------------------------------------------------- edge audit (stats.accuracy, stats.profit) */

const ci = (c, f) => (Array.isArray(c) && c[0] != null ? ` (95%: ${f(c[0])} to ${f(c[1])})` : "");
const money = (x) => (x == null ? "–" : `${x < 0 ? "−" : x > 0 ? "+" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`);
const ret = (x) => (x == null ? "–" : `${x < 0 ? "−" : "+"}${Math.abs(100 * x).toFixed(0)}%`);
const races_ = (n) => `${n} race${n === 1 ? "" : "s"}`;

function auditHTML(stats, race, raceName) {
  return `<section class="audit">${accuracyHTML(stats.accuracy, race, raceName)}${profitHTML(stats.profit)}</section>`;
}

function accuracyHTML(acc, race, raceName) {
  const head = (ans) => `<h3 class="tab-h">Did the projection hold? ${acc?.evidence ? evidenceLabel(acc.evidence) : ""}</h3>${ans ? `<p class="answer">${ans}</p>` : ""}`;
  if (!acc) return `${head("")}<p class="na">Not computed for this election yet.</p>`;
  const lines = [];
  // coverage of the 90% band: the selected race, then pooled (the pre-stated test: most races at or above ~80%)
  const covRace = acc.coverage90?.[race.race_id]?.["1-95%"];
  const pooled = acc.coverage90_pooled?.["1-95%"];
  const lists = acc.coverage90_list_shares?.["1-95%"];
  let answer = "";
  if (pooled?.races) {
    const ok = pooled.median >= 0.8 && pooled.races_below_80pct < pooled.races / 2;
    answer = `${ok ? "Yes" : "No"}, by the project's coverage test (the 90% band should contain the final share in about 80% or more of minutes in most races). ${pooled.races === 1 ? `Coverage ${pct(pooled.median, 0)} in the one race.` : `Median coverage ${pct(pooled.median, 0)} across ${pooled.races} races (lowest ${pct(pooled.min, 0)}; ${pooled.races_below_80pct} below 80%).`}`;
  } else if (lists) {
    answer = `The 90% bands covered the final list shares in ${pct(lists.coverage90, 0)} of ${lists.n?.toLocaleString("en-US") ?? "–"} checks (mean error ${signed(lists.mean_z, 2)} sd).`;
  }
  if (covRace) lines.push(`<div class="st-row"><dt>${esc(raceName(race))}: final share inside the 90% band</dt><dd>${pct(covRace.coverage90, 0)} of ${covRace.n} minutes from 1% to 95% counted; mean error ${signed(covRace.mean_z, 2)} sd${acc.coverage90[race.race_id]["90-95%"] ? `; 90–95% counted: ${pct(acc.coverage90[race.race_id]["90-95%"].coverage90, 0)}` : ""}</dd></div>`);
  const err = acc.share_error_pt?.[race.race_id];
  const cuts = ["1%", "5%", "10%", "25%", "50%"];
  if (err) lines.push(`<div class="st-row"><dt>${esc(raceName(race))}: error against the final share (points)</dt><dd>${cuts.filter((c) => err[c]).map((c) => `${c}: <span class="m">${signed(err[c].model)}</span> / <span class="r">${signed(err[c].raw)}</span>`).join(" · ")}<br><small>model / raw count, at each share counted</small></dd></div>`);
  const mae = acc.share_error_mae_pt;
  if (mae) lines.push(`<div class="st-row"><dt>All races: mean absolute error (points)</dt><dd>${cuts.filter((c) => mae[c]).map((c) => `${c}: <span class="m">${num(mae[c].model, 1)}</span> / <span class="r">${num(mae[c].raw, 1)}</span>`).join(" · ")}<br><small>model / raw count</small></dd></div>`);
  const pw = acc.pwin;
  if (pw?.minutes) {
    const vs = pw.brier_market != null
      ? `model ${num(pw.brier_model, 3)}, market ${num(pw.brier_market, 3)}; difference ${signed(pw.brier_diff, 3)}${ci(pw.brier_diff_ci, (x) => signed(x, 3))}. Model lower in ${races_(pw.races_model_better_brier)}, market lower in ${races_(pw.races_market_better_brier)}.`
      : `model ${num(pw.brier_model, 3)} (no market prices to compare).`;
    lines.push(`<div class="st-row"><dt>Probability of winning: Brier score (lower is better)</dt><dd>${vs} ${races_(pw.races)}, ${pw.minutes.toLocaleString("en-US")} minutes.${acc.pwin_window ? `<br><small>${esc(acc.pwin_window)}</small>` : ""}</dd></div>`);
  }
  const plot = reliabilityPlot(pw?.reliability_model, pw?.reliability_market);
  return `${head(answer)}<dl class="st">${lines.join("")}</dl>${plot}`;
}

function reliabilityPlot(model, market) {
  if (!model?.length) return "";
  const w = 230, h = 200, m = { l: 34, r: 8, t: 8, b: 30 };
  const x = d3.scaleLinear().domain([0, 1]).range([m.l, w - m.r]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
  const r = d3.scaleSqrt().domain([0, d3.max([...model, ...(market ?? [])], (d) => d.minutes) || 1]).range([1.5, 7]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
    .attr("aria-label", "Reliability: predicted probability against how often the event happened");
  svg.append("g").attr("class", "grid").selectAll("line").data([0.25, 0.5, 0.75]).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y);
  svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickSize(3));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(3));
  svg.append("text").attr("class", "legend-text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 2).attr("text-anchor", "middle").text("predicted probability");
  for (const [rows, color] of [[market, C.market], [model, C.model]]) {
    if (!rows?.length) continue;
    const pts = rows.filter((d) => d.minutes > 0 && d.mean_p != null && d.freq != null);
    svg.append("path").datum(pts).attr("fill", "none").attr("stroke", color).attr("stroke-width", 1).attr("opacity", 0.6).attr("d", d3.line().x((d) => x(d.mean_p)).y((d) => y(d.freq)));
    svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => x(d.mean_p)).attr("cy", (d) => y(d.freq)).attr("r", (d) => r(d.minutes))
      .attr("fill", color).attr("fill-opacity", 0.85).append("title").text((d) => `${d.bin}: predicted ${num(d.mean_p)}, happened ${pct(d.freq, 0)} (${d.minutes} minutes, ${d.races} races)`);
  }
  return `<figure class="reliability">${svg.node().outerHTML}<figcaption>How often the favoured outcome happened (up) against the probability given (across), in bins: <span class="m">model</span>${market?.length ? `, <span class="k">market price</span>` : ""}. On the dashed line the probabilities were right on average. Dot size: minutes in the bin.</figcaption></figure>`;
}

function profitHTML(p) {
  const head = (ans) => `<h3 class="tab-h">Was there profit after costs? ${p?.evidence ? evidenceLabel(p.evidence) : ""}</h3>${ans ? `<p class="answer">${ans}</p>` : ""}`;
  if (!p) return `${head("")}<p class="na">Not computed for this election (no market prices, or not run yet).</p>`;
  const best = p.max_profit;
  const lo = Array.isArray(p.max_profit_ci) ? p.max_profit_ci[0] : null;
  const answer = !p.signals ? "No signals were triggered, so nothing was traded."
    : best == null || best <= 0 ? `No: at most ${money(best ?? 0)} with ${money(p.max_deployed ?? 0).replace("+", "")} deployed.`
      : lo != null && lo > 0 ? `Yes: up to ${money(best)} on ${money(p.max_deployed).replace("+", "")} deployed${ci(p.max_profit_ci, money)}.`
        : `Not clearly: up to ${money(best)} on ${money(p.max_deployed).replace("+", "")} deployed, but the interval includes losses${ci(p.max_profit_ci, money)}.`;
  const sizes = Object.entries(p.by_size ?? {}).sort((a, b) => +a[0] - +b[0]);
  const table = sizes.length ? `<table class="compact"><thead><tr><th>Stake per signal</th><th class="n">Fully fillable</th><th class="n">Deployed</th><th class="n">P&amp;L</th><th class="n">Per $</th></tr></thead><tbody>${sizes.map(([s, v]) =>
    `<tr><td>$${(+s).toLocaleString("en-US")}</td><td class="n">${v.fillable ?? "–"} of ${p.signals}</td><td class="n">$${Math.round(v.deployed ?? 0).toLocaleString("en-US")}</td><td class="n">${money(v.pnl)}</td><td class="n">${ret(v.return_per_usd)}</td></tr>`).join("")}</tbody></table>` : "";
  const facts = [
    `${p.signals} signals, ${p.won} won${p.hit_rate != null ? `: hit rate ${pct(p.hit_rate, 0)}${ci(p.hit_rate_ci, (x) => pct(x, 0))}` : ""}`,
    p.largest_trade_share != null ? `${pct(p.largest_trade_share, 0)} of the profit came from the largest single trade; without it ${money(p.pnl_without_largest)}` : null,
  ].filter(Boolean);
  const variants = Object.entries(p.variants ?? {});
  return `${head(answer)}<p class="st-facts">${facts.join(". ")}.</p>${table}
    ${p.rule ? `<p class="note">Rule: ${esc(p.rule)}</p>` : ""}${p.execution ? `<p class="note">Execution: ${esc(p.execution)}</p>` : ""}
    ${variants.length ? `<details class="st-others"><summary>Other execution assumptions (${variants.length})</summary><ul>${variants.map(([k, v]) => `<li>${esc(k.replace(/_/g, " "))}: ${money(v.max_profit)} on $${Math.round(v.max_deployed ?? 0).toLocaleString("en-US")} deployed</li>`).join("")}</ul></details>` : ""}`;
}

// Plain rendering of election-specific stats the standard panel does not know about.
function flat(v, depth = 0) {
  if (v == null) return "–";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : Math.abs(v) < 10 ? v.toFixed(3) : v.toFixed(1);
  if (typeof v !== "object") return esc(v);
  if (Array.isArray(v)) return v.length > 6 ? `${v.length} values` : v.map((x) => flat(x, depth + 1)).join(", ");
  if (depth >= 2) return "…";
  return Object.entries(v).map(([k, x]) => `${esc(k.replace(/_/g, " "))}: ${flat(x, depth + 1)}`).join(depth ? ", " : "<br>");
}

function arbHTML(arb, raceName, races) {
  const byId = new Map(races.map((r) => [r.race_id, r]));
  return `<h3 class="tab-h">Polymarket–Kalshi pairs</h3><ul class="arb">${arb.map((p) => {
    const rows = p.rows.filter((d) => d.net_edge != null);
    const pos = rows.filter((d) => d.net_edge > 0).length;
    const best = d3.max(rows, (d) => d.net_edge);
    return `<li>${esc(byId.has(p.race_id) ? raceName(byId.get(p.race_id)) : p.race_id)}: ${pos} of ${rows.length} minutes with net edge above zero; largest ${best != null ? `${(100 * best).toFixed(1)}¢` : "–"} per $1.</li>`;
  }).join("")}</ul>`;
}

/* ---------------------------------------------------------------- method tab */

export function renderMethod(el, { meta, entry, geometryCredit }) {
  const notes = (meta.notes ?? []).map((n) => `<li>${esc(n)}</li>`).join("");
  const src = (meta.sources ?? []).map((s) => `<li><a href="${esc(s.url)}">${esc(s.name)}</a>, ${esc(s.licence)}</li>`).join("");
  const evs = Object.entries(EVIDENCE).map(([k, e]) => `<li>${evidenceLabel(k)} ${esc(e.long[0].toUpperCase() + e.long.slice(1))}.</li>`).join("");
  el.innerHTML = `
    <h3 class="tab-h">How this election was modelled</h3>
    ${notes ? `<ul>${notes}</ul>` : `<p class="note">No notes.</p>`}
    <h3 class="tab-h">Evidence labels</h3>
    <p>This election: ${entry?.evidence ? evidenceLabel(entry.evidence) : "–"}</p>
    <ul class="evs">${evs}</ul>
    <h3 class="tab-h">Sources</h3>
    <ul>${src}${geometryCredit ? `<li>${geometryCredit}</li>` : ""}<li>Kalshi data is not shown: Kalshi's data terms do not allow republishing it.</li></ul>
    <p class="note"><a href="./">All elections and definitions</a> · <a href="brazil-2026.html">Brazil 2026 write-up</a> · Code under MIT licence.</p>`;
}
