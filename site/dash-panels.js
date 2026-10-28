// Right-hand panels of the dashboard: the readout at the current time, and the three tabs
// (races, statistics, method & sources). Pure rendering from values the controller passes in.
import * as d3 from "./vendor/d3.js";
import { C, esc, pct, num, usd, votesShort, clock, tzName, evidenceLabel, EVIDENCE, leadMinutes } from "./common.js";

const PLATFORM = { polymarket: "Polymarket", kalshi: "Kalshi" };
const signed = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);

/* ---------------------------------------------------------------- readout */

// pre: before the count (meta.phase "pre-count"): { day } for the date in the heading, { note } when
// the model has no pre-count figure for the race, { live } "Polymarket live · 12 s ago" for the latest row.
export function renderReadout(el, { race, row, t, preview, tz, countTimeline, frameT, pre = null }) {
  const a = esc(race.a.name);
  const head = `<p class="ro-head"><span class="ro-at">${preview ? "Preview at" : "At"} ${pre?.day ? `${esc(pre.day)} ` : ""}<b>${clock(t, tz)}</b> ${esc(tzName(tz, t))}</span><span class="ro-race">${esc(race.name)} · figures for ${a}</span></p>`;
  if (!row) { el.innerHTML = `${head}<p class="note">No data for this race at this time.</p>`; return; }
  const band = row.sd != null && row.share != null ? `<small>± ${(164.5 * row.sd).toFixed(1)} pt (90%)</small>` : "";
  const market = row.pm ?? row.kalshi; // the gap is measured against Polymarket where it lists the race
  const marketName = row.pm != null ? "Polymarket" : "Kalshi";
  const figs = [];
  if (pre) {
    figs.push(`<div><dt>Model before any votes</dt><dd class="m">${row.share != null ? `${pct(row.share)}${band}` : `<small>${esc(pre.note ?? "not available")}</small>`}</dd></div>`);
  } else if (countTimeline) {
    figs.push(`<div><dt>Counted</dt><dd>${pct(row.counted)}</dd></div>`);
    figs.push(`<div><dt>Raw count</dt><dd class="r">${pct(row.raw)}</dd></div>`);
    figs.push(`<div><dt>Model projection</dt><dd class="m">${pct(row.share)}${band}</dd></div>`);
  }
  const pParts = [];
  if (row.P != null) pParts.push(`<span class="m">${num(row.P)}</span><small>model</small>`);
  if (row.pm != null) pParts.push(`<span class="k">${num(row.pm)}</span><small>Polymarket</small>`);
  if (row.kalshi != null) pParts.push(`<span class="kx">${num(row.kalshi)}</span><small>Kalshi</small>`);
  figs.push(`<div class="ro-p"><dt>Chance of winning</dt><dd>${pParts.join(" ") || "–"}</dd></div>`);
  let gap = "";
  if (row.P != null && market != null) {
    const g = row.P - market;
    const both = row.pm != null && row.kalshi != null ? `, Kalshi ${num(row.kalshi)}` : "";
    gap = `Model ${num(row.P)}, ${marketName} ${num(market)}${both}: gap to ${marketName} <b>${num(Math.abs(g))}</b>${Math.abs(g) >= 0.005 ? `, model ${g > 0 ? "higher" : "lower"}` : ""}.`;
  } else if (row.P != null) gap = "No market price at this time.";
  const frame = frameT != null && countTimeline && !pre ? ` <span class="ro-frame">Map: ${clock(frameT, tz)} frame.</span>` : "";
  const live = pre && row.live && pre.live ? ` <span class="ro-frame">${esc(pre.live)}.</span>` : "";
  // the model's estimate of the votes still to count and how they lean (rem_total, rem_margin_total)
  let rem = "";
  if (row.rem_total > 0) {
    const lean = row.rem_margin_total;
    const side = lean >= 0 ? race.a.name : race.b.name;
    rem = `<p class="ro-rem">≈ ${votesShort(row.rem_total)} votes left${lean != null ? `; they lean ${esc(side)} +${(100 * Math.abs(lean) / row.rem_total).toFixed(1)} pt` : ""} <small>(model estimate)</small></p>`;
  }
  el.innerHTML = `${head}<dl class="ro-figs">${figs.join("")}</dl>${gap || frame || live ? `<p class="ro-gap">${gap}${frame}${live}</p>` : ""}${rem}`;
}

/* ---------------------------------------------------------------- races tab */

// leadOf(race) -> minutes the model reached 90% on the winner before Polymarket, both measured from
// the race's 1% gate (see gatedSettle in common.js); null when not computable.
const SORT = { key: "order", dir: 1 }; // kept across rebuilds (live refresh)

// hasKalshi: show a Kalshi column (some race in the election has Kalshi prices). profitOf(race): the
// race's entry in stats.profit_model_limit.by_race, or null; the Profit column shows when any race has one.
const profitText = (p) => (p == null ? "–" : `${p.bankroll_pnl > 0 ? "+" : p.bankroll_pnl < 0 ? "−" : ""}$${Math.abs(Math.round(p.bankroll_pnl ?? 0)).toLocaleString("en-US")}`);
const profitTitle = (p) => (p == null ? "" : p.eligible_trades ? `${p.eligible_trades.toLocaleString("en-US")} trades at least 5¢ below the model, $${Math.round(p.eligible_volume).toLocaleString("en-US")} in all; the $10,000 put $${Math.round(p.bankroll_stake ?? 0).toLocaleString("en-US")} into this race` : p.reason ? `No trade qualified: ${p.reason}` : "No trade qualified");
export function createRaceList(el, { races, raceName, leadOf, onSelect, hasKalshi = false, profitOf = () => null }) {
  const st = { values: new Map() };
  const lead = (r) => leadOf(r);
  const hasProfit = races.some((r) => profitOf(r));
  const cols = [
    ["name", "Race", false], ["P", "Model", true], ["pm", "Polymarket", true], ...(hasKalshi ? [["k", "Kalshi", true]] : []), ["gap", "Gap", true], ["lead", "Lead from 1% counted (min)", true],
    ...(hasProfit ? [["profit", "Profit on $10,000", true]] : []),
  ];
  el.innerHTML = `<table class="races"><thead><tr>${cols.map(([k, label, n]) => `<th class="${n ? "n" : ""}" aria-sort="${SORT.key === k ? (SORT.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${label}</button></th>`).join("")}</tr></thead><tbody></tbody></table>
    <p class="note">Model: the model's probability that the first-named candidate wins, at the scrubber time. Polymarket${hasKalshi ? " and Kalshi: each venue's price" : ": the price"} for the same outcome (–: not listed there). Gap: model minus the Polymarket price (or Kalshi's, where only Kalshi lists the race). Lead: minutes by which the model reached 90% on the winner before Polymarket did, both counted from the moment the race reached 1% counted (negative: after).${hasProfit ? " Profit on $10,000: what the election's $10,000 made in this race under the buying rule in the Statistics tab (hover for the trades that qualified)." : ""}</p>`;
  const tbody = el.querySelector("tbody");
  const rows = races.map((r, order) => {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.race = r.race_id;
    const pr = hasProfit ? profitOf(r) : null;
    tr.innerHTML = `<td>${esc(raceName(r))}</td><td class="n m"></td><td class="n"></td>${hasKalshi ? `<td class="n kx"></td>` : ""}<td class="n"><span class="gapbar"><i></i></span></td><td class="n">${lead(r) == null ? "–" : signed(lead(r), 0)}</td>${hasProfit ? `<td class="n" title="${esc(profitTitle(pr))}">${profitText(pr)}</td>` : ""}`;
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
    const m = v.pm ?? v.k;
    return { order: x.order, name: x.r.name, P: v.P, pm: v.pm, k: v.k, gap: v.P != null && m != null ? Math.abs(v.P - m) : null, lead: lead(x.r), profit: profitOf(x.r)?.bankroll_pnl }[SORT.key];
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
        const c = x.cells;
        c[1].textContent = num(v.P);
        c[2].textContent = num(v.pm);
        if (hasKalshi) c[3].textContent = num(v.k);
        const gapCell = c[hasKalshi ? 4 : 3];
        const bar = gapCell.firstChild.firstChild;
        const m = v.pm ?? v.k;
        if (v.P != null && m != null) {
          const g = v.P - m;
          bar.style.width = `${Math.min(50, Math.abs(g) * 50)}%`;
          bar.style.left = g >= 0 ? "50%" : `${50 - Math.min(50, Math.abs(g) * 50)}%`;
          bar.style.background = g >= 0 ? C.model : C.market;
          gapCell.title = `gap ${signed(g, 2)}`;
        } else bar.style.width = "0";
      }
      if (!["order", "name", "lead", "profit"].includes(SORT.key)) sort();
    },
    select(raceId) {
      for (const x of rows) x.tr.classList.toggle("sel", x.r.race_id === raceId);
    },
  };
}

/* ---------------------------------------------------------------- statistics tab */

export function renderStats(el, { stats, race, races, raceName, tz, arb, settleOf, profitOf = () => null }) {
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
      ? `model ${t(set.model)} · Polymarket ${set.polymarket ? t(set.polymarket) : "–"}${set.kalshi !== undefined ? ` · Kalshi ${set.kalshi ? t(set.kalshi) : "–"}` : ""} · raw count ${t(set.raw)}${leadMinutes(settleOf(id)) != null ? ` · from 1% counted, the model was ${Math.abs(leadMinutes(settleOf(id)))} min ${leadMinutes(settleOf(id)) >= 0 ? "earlier" : "later"} than Polymarket` : ""}`
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
      : `${a.pairs} pairs; ${a.minutes_with_net_arb} minutes with a net edge; largest ${a.max_net_edge != null ? `${(100 * a.max_net_edge).toFixed(1)}¢` : "–"} per $1; ${usd(a.fillable_usd)} fillable on both venues in the same minute${a.fillable_net_usd != null ? `, ${usd(a.fillable_net_usd)} net after fees` : ""}${a.fillable_usd_1s != null ? `; ${usd(a.fillable_usd_1s)} within the same second` : ""}${a.note ? `<br><small>${esc(a.note)}</small>` : ""}`),
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
    ${auditHTML(stats, race, raceName, races, raceProfitHTML(profitOf(race), race, raceName, tz))}
    <h3 class="tab-h">${esc(raceName(race))}</h3><dl class="st">${raceRows.join("")}</dl>
    <h3 class="tab-h">All markets in this election</h3><dl class="st">${allRows.join("")}</dl>
    ${arb?.length ? arbHTML(arb, race, raceName, races, tz) : ""}
    ${extraHTML}
    ${othersTable}
    <p class="note">The same measures are computed for every election; the <a href="./#measures">overview page</a> defines them.</p>`;
}

const KNOWN = new Set(["calibration", "settle", "settle_gated", "catchup", "early_error", "capacity", "arbitrage", "evidence", "evidence_note", "reasons", "null_reasons", "accuracy", "profit", "invest10k", "profit_model_limit"]);

/* ---------------------------------------------------------------- edge audit (stats.accuracy, stats.profit) */

const ci = (c, f) => (Array.isArray(c) && c[0] != null ? ` (95%: ${f(c[0])} to ${f(c[1])})` : "");
const money = (x) => (x == null ? "–" : `${x < 0 ? "−" : x > 0 ? "+" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`);
const ret = (x) => (x == null ? "–" : `${x < 0 ? "−" : "+"}${Math.abs(100 * x).toFixed(0)}%`);
const races_ = (n) => `${n} race${n === 1 ? "" : "s"}`;

function auditHTML(stats, race, raceName, races, raceBlock) {
  return `<section class="audit">${accuracyHTML(stats.accuracy, race, raceName)}${profitHTML(stats.invest10k, stats.profit_model_limit, stats.profit, races, raceBlock)}</section>`;
}

// The selected race under the headline buying rule (stats.profit_model_limit.by_race); "" when absent.
function raceProfitHTML(p, race, raceName, tz) {
  if (!p) return "";
  const name = `<b>This race, ${esc(raceName(race))}${p.markets > 1 ? ` (its ${p.markets} markets together)` : ""}:</b>`;
  if (!p.eligible_trades) return `<p class="answer race-profit">${name} no trade qualified${p.reason ? `: ${esc(p.reason)}` : ""}.</p>`;
  const f = p.first_eligible;
  const first = f ? ` The first came at ${clock(f.t, tz)}: ${esc(f.side)} at ${num(f.price)} with the model at ${num(f.model_p)}.` : "";
  const bank = p.bankroll_stake > 0
    ? `The $10,000 put ${usd0(p.bankroll_stake)} into it and made ${money(p.bankroll_pnl)}.`
    : "The $10,000 was already spent when these trades came, so it made nothing here.";
  return `<p class="answer race-profit">${name} ${p.eligible_trades.toLocaleString("en-US")} trades, ${usd0(p.eligible_volume)} in all, were at least 5¢ below the model; together they made ${money(p.eligible_pnl)}. ${bank}${first}</p>`;
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

// Headline: $10,000 under the model-limit rule (stats.invest10k); beside it the strict rule of buying
// only at each signal's own price (invest10k.signal_price, and stats.profit by stake size).
const usd0 = (x) => (x == null ? "–" : `$${Math.round(x).toLocaleString("en-US")}`);
const signedPct = (x) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(1)}%`);
const SHARED_NOTES = [/Single event:[^.]*\.\s*/i, /Eligible volume is an upper bound:[^.]*\.\s*/i, /\d+% of the eligible volume was in one market \([^)]*\)\.\s*/i];
const shareOfGain = (s) => (s >= 0.995 && s < 1 ? `Almost all (${(100 * s).toFixed(1)}%)` : pct(s, 0));
function profitHTML(inv, mlGrid, old, races, raceBlock = "") {
  // no evidence label here: the headline buying rule was defined after the outcomes were known
  const head = (ans) => `<h3 class="tab-h">Was there profit after costs?</h3>${raceBlock}${ans ? `<p class="answer">${ans}</p>` : ""}`;
  if (!inv) return old ? signalPriceHTML(old, head) : `${head("")}<p class="na">Not computed for this election (no market prices, or not run yet).</p>`;
  const end = `$10,000 became ${usd0(inv.ending_value)} (${signedPct(inv.return_pct)})`;
  const whole = raceBlock ? "<b>Whole election:</b> " : "";
  const answer = whole + (!inv.bets ? "No: no trade qualified under the model-limit rule, so the $10,000 was never used."
    : inv.ending_value > inv.bankroll ? `Yes, under the model-limit rule: ${end}.`
      : `No: under the model-limit rule ${end}.`);
  const sp = inv.signal_price;
  const question = (id) => races.flatMap((r) => r.markets ?? []).find((m) => m.id === id)?.question;
  const facts = [];
  if (inv.bets) facts.push(`${inv.bets.toLocaleString("en-US")} trades in ${inv.markets} ${inv.markets === 1 ? "market" : "markets"}, ${inv.won.toLocaleString("en-US")} of them on the winning side; ${usd0(inv.deployed)} spent.`);
  if (sp) facts.push(`Buying only at each signal's own price: ${usd0(sp.ending_value)} (${signedPct(sp.return_pct)})${sp.signals === 0 ? ", no signal fired" : sp.signals != null ? `, ${sp.bets} of ${sp.signals} signals filled` : ""}.`);
  if (inv.largest_market_share != null && inv.largest_market_share >= 0.5 && inv.markets > 1) facts.push(`${shareOfGain(inv.largest_market_share)} of the gain came from one market${question(inv.largest_market) ? `: “${esc(question(inv.largest_market).replace(/\?$/, ""))}”` : ""}.`);
  facts.push("The election is one outcome: all of its markets ride on the same count.");
  if (inv.eligible_volume) facts.push(`Trades that qualified: ${usd0(inv.eligible_volume)}, with a P&amp;L of ${money(inv.eligible_pnl)}. That is an upper bound for one trader, since other traders took them.`);
  facts.push("This buying rule was defined after the outcomes were known.");
  let note = inv.note ?? "";
  for (const re of SHARED_NOTES) note = note.replace(re, "");
  note = note.trim();
  const grid = Object.entries(mlGrid?.grid ?? {});
  const modes = [["10pct", "10% of each trade"], ["first", "All of each trade"], ["10pct_cap", "10%, $1,000 cap per market"], ["first_cap", "All, $1,000 cap"]];
  const gridTable = grid.length ? `<details class="st-others"><summary>The same $10,000 under other versions of the rule</summary>
    <table class="compact"><thead><tr><th>Price at least … below the model, delay</th>${modes.map(([, l]) => `<th class="n">${l}</th>`).join("")}</tr></thead><tbody>${grid.map(([k, g]) => {
      const m = /edge(\d+)c_delay(\d+)s/.exec(k);
      return `<tr><td>${m ? `${m[1]}¢, ${m[2]} s` : esc(k)}</td>${modes.map(([mode]) => `<td class="n">${usd0(g.bankroll?.[mode]?.ending_value)}</td>`).join("")}</tr>`;
    }).join("")}</tbody></table></details>` : "";
  return `${head(answer, inv.evidence)}<p class="st-facts">${facts.join(" ")}</p>
    ${note ? `<p class="note">${esc(note[0].toUpperCase() + note.slice(1))}</p>` : ""}
    <p class="note">The rule: from the moment 1% of the vote is counted, buy 10% of every real trade (Israel: every offer in the recorded order book, one snapshot per release) on the side the model favours whose price, fees included, is at least 5¢ below the model's probability as known 60 seconds earlier; no limit per market; keep buying until the $10,000 is spent and hold to the result.</p>
    ${gridTable}
    ${old ? `<details class="st-others"><summary>Buying only at the signal's own price, by stake size</summary>${signalPriceHTML(old, (ans) => (ans ? `<p class="answer">${ans}</p>` : ""))}</details>` : ""}`;
}

function signalPriceHTML(p, head) {
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

// One small chart per Polymarket–Kalshi pair of the selected race (net edge per $1 over time: above
// zero, buying both sides cost less than the $1 payout after fees), and a table for the other races.
function arbHTML(arb, race, raceName, races, tz) {
  const byId = new Map(races.map((r) => [r.race_id, r]));
  const name = (p) => esc(byId.has(p.race_id) ? raceName(byId.get(p.race_id)) : p.race_id);
  const sum = (p) => {
    const rows = p.rows.filter((d) => d.net_edge != null);
    return { rows, pos: rows.filter((d) => d.net_edge > 0).length, best: d3.max(rows, (d) => d.net_edge), fill: p.fillable?.["1min"] };
  };
  const mine = arb.filter((p) => p.race_id === race.race_id);
  const others = arb.filter((p) => p.race_id !== race.race_id);
  const charts = mine.map((p) => {
    const s = sum(p);
    return `<figure class="arb-pair"><figcaption><b>${name(p)}</b>: Polymarket <code>${esc(p.pm_market)}</code> against Kalshi <code>${esc(p.kalshi_ticker)}</code>${p.same_side === false ? " (opposite sides)" : ""}. ${s.pos} of ${s.rows.length} minutes with a net edge above zero; largest ${s.best != null ? `${(100 * s.best).toFixed(1)}¢` : "–"} per $1${s.fill ? `; ${usd(s.fill.usd)} fillable on both venues in the same minute, ${usd(s.fill.net_usd)} net` : ""}.${p.pm_quotes ? ` Polymarket asks: ${esc(p.pm_quotes)}.` : ""}</figcaption>${arbChart(s.rows, tz)}</figure>`;
  }).join("");
  const table = others.length ? `<details class="st-others"${mine.length ? "" : " open"}><summary>${mine.length ? "Other pairs" : "Pairs in this election"} (${others.length})</summary>
    <table class="compact"><thead><tr><th>Race</th><th class="n">Minutes above zero</th><th class="n">Largest (¢ per $1)</th><th class="n">Fillable, same minute</th></tr></thead><tbody>${others.map((p) => {
      const s = sum(p);
      return `<tr><td>${name(p)}</td><td class="n">${s.pos} of ${s.rows.length}</td><td class="n">${s.best != null ? (100 * s.best).toFixed(1) : "–"}</td><td class="n">${s.fill ? usd(s.fill.usd) : "–"}</td></tr>`;
    }).join("")}</tbody></table></details>` : "";
  return `<h3 class="tab-h">Buying both sides across Polymarket and Kalshi</h3>
    <p class="note">Net edge: $1 minus the cost of buying "yes" on one venue and "no" on the other, after both venues' fees. Above zero the pair paid out more than it cost, unless the two venues settle the question differently, which is why no pair is called risk-free.</p>
    ${charts}${table}`;
}

function arbChart(rows, tz) {
  if (rows.length < 2) return "";
  const w = 560, h = 120, m = { l: 34, r: 8, t: 6, b: 20 };
  const time = (d) => new Date(d.t);
  const x = d3.scaleUtc().domain(d3.extent(rows, time)).range([m.l, w - m.r]);
  const lo = Math.max(-0.1, Math.min(-0.02, d3.min(rows, (d) => d.net_edge)));
  const hi = Math.max(0.02, d3.max(rows, (d) => d.net_edge));
  const y = d3.scaleLinear().domain([lo, hi]).range([h - m.b, m.t]).clamp(true);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("class", "arb-chart").attr("role", "img").attr("aria-label", "Net edge per dollar over time");
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(5).tickSize(3).tickFormat((d) => clock(d, tz)));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(3).tickFormat((v) => `${Math.round(100 * v)}¢`));
  svg.append("path").datum(rows).attr("fill", C.kalshi).attr("fill-opacity", 0.25)
    .attr("d", d3.area().x((d) => x(time(d))).y0(y(0)).y1((d) => y(Math.max(0, d.net_edge))).curve(d3.curveStepAfter));
  svg.append("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", "#9a968c");
  svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1).attr("d", d3.line().x((d) => x(time(d))).y((d) => y(d.net_edge)).curve(d3.curveStepAfter));
  return svg.node().outerHTML;
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
    ${(meta.sources ?? []).some((s) => /^Kalshi/.test(s.name)) ? `<p>Market prices: Polymarket and, where Kalshi lists the same race, Kalshi. Kalshi data: Kalshi Inc.</p>` : ""}
    <ul>${src}${geometryCredit ? `<li>${geometryCredit}</li>` : ""}</ul>
    <p class="note"><a href="./">All elections and definitions</a> · <a href="brazil-2026.html">Brazil 2026 write-up</a> · Code under MIT licence.</p>`;
}
