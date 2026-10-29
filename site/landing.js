// Homepage: the evidence across elections (from site/data/summary.json, written by
// analysis/edge_audit.py) and the list of elections (elections.json, each election's files).
import * as d3 from "./vendor/d3.js";
import { C, LIVE, STATIC, getJSON, esc, pct, fmtDate, STATUS, plural, gatedSettle, leadMinutes, modelP, shortName } from "./common.js";
import { polymarketYes } from "./dash-precount.js";
import { createWorldMap } from "./world-map.js";

const $ = (id) => document.getElementById(id);
const tbody = document.querySelector("#overview tbody");
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countryName = (c) => { try { return c ? regionNames.of(c) : "Other"; } catch { return c; } };
// With ?live=, the live server carries only the elections being counted (status live or scheduled);
// every other election's files and dashboard come from the static site.
let liveIds = new Set();
const onLive = (id) => LIVE && liveIds.has(id);
const href = (id) => `election.html?id=${encodeURIComponent(id)}${onLive(id) ? `&live=${encodeURIComponent(LIVE)}` : ""}`;
const rootOf = (id) => (onLive(id) ? LIVE : STATIC);
const money = (x) => `$${Math.round(x).toLocaleString("en-US")}`;
const signedPct = (x, d = 1) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}%`;
const pts = (x) => (x == null ? "–" : x.toFixed(1));
let names = new Map(); // election id -> name
let years = new Map(); // election id -> year of the vote

/* ---------------------------------------------------------------- evidence (summary.json) */

function drawAll(S) {
  drawError(S);
  drawCalibration(S);
  drawMoney(S);
  drawVenues(S);
}

// 4. Polymarket against Kalshi (summary.kalshi, written by analysis/kalshi_export.py; absent: hidden)
function drawVenues(S) {
  const rows = (S.kalshi?.elections ?? []).filter((e) => e.pairs || e.settle?.compared);
  if (!rows.length) return;
  $("ev-venues").hidden = false;
  const tot = (k) => d3.sum(rows, (e) => e.settle?.[k] ?? 0);
  const compared = tot("compared");
  const usdOf = (x) => (x == null ? "–" : `$${Math.round(x).toLocaleString("en-US")}`);
  const els = `${rows.length} ${plural("election", rows.length)}`;
  $("ev-venues-lead").textContent = compared
    ? `In ${compared} races listed on both venues across ${els}, Kalshi settled on the winner first in ${tot("kalshi_first")}, Polymarket in ${tot("polymarket_first")}, and both in the same minute in ${tot("same_minute")}.`
    : `Kalshi lists races in ${els} covered here.`;
  const med = (m) => (m == null ? "–" : m === 0 ? "same minute" : `Kalshi ${Math.abs(Math.round(m))} min ${m < 0 ? "earlier" : "later"}`);
  const label = (id) => {
    const n = names.get(id) ?? id, y = years.get(id);
    return y && !n.includes(y) ? `${n}, ${y}` : n;
  };
  $("venues").innerHTML = `<thead><tr><th>Election</th><th class="n">Races on both</th><th class="n">Settled first: Kalshi / Polymarket / same</th><th class="n">Median difference</th><th class="n">Minutes buying both sides paid</th><th class="n">Largest (¢ per $1)</th><th class="n">Fillable in the same minute (net)</th></tr></thead>
    <tbody>${rows.map((e) => {
      const s = e.settle ?? {}, a = e.arbitrage ?? {};
      return `<tr><td><a href="election.html?id=${encodeURIComponent(e.id)}">${esc(label(e.id))}</a></td><td class="n">${e.races ?? "–"}</td>
        <td class="n">${s.compared ? `${s.kalshi_first ?? 0} / ${s.polymarket_first ?? 0} / ${s.same_minute ?? 0}` : "–"}</td><td class="n">${med(s.median_kalshi_minus_pm_min)}</td>
        <td class="n">${a.minutes_with_net_arb ?? "–"}</td><td class="n">${a.max_net_edge != null ? (100 * a.max_net_edge).toFixed(1) : "–"}</td>
        <td class="n">${a.fillable_usd != null ? `${usdOf(a.fillable_usd)} (${usdOf(a.fillable_net_usd)})` : "–"}</td></tr>`;
    }).join("")}</tbody>`;
}

// 1. distance from the final share by share counted: each election's main race, thin; the
// average over elections, thick
function drawError(S) {
  const pe = S.projection_error;
  if (!pe) return;
  $("ev-error").hidden = false;
  const th = pe.thresholds;
  const pool = pe.pooled?.main ?? {};
  const at = (c) => pool[c] ? `${pts(pool[c].model)} points from the final share, the raw count ${pts(pool[c].raw)}` : "";
  $("ev-error-lead").textContent = pool["5%"] && pool["25%"]
    ? `With 5% of the vote counted the projection was on average ${at("5%")}. At 25% counted: ${pts(pool["25%"].model)} against ${pts(pool["25%"].raw)}. Average over the main race of ${pool["5%"].elections} elections.`
    : "";
  const series = [];
  for (const [id, e] of Object.entries(pe.by_election)) {
    if (!e?.main) continue;
    for (const k of ["model", "raw"]) {
      series.push({ id, k, thin: true, pts: th.map((c) => [c, e.main[c]?.[k] == null ? null : Math.abs(e.main[c][k])]) });
    }
  }
  for (const k of ["model", "raw"]) series.push({ id: "pooled", k, thin: false, pts: th.map((c) => [c, pool[c]?.[k] ?? null]) });
  const root = $("chart-error");
  const draw = () => {
    const w = Math.max(300, root.clientWidth), h = Math.min(360, Math.max(240, w * 0.45));
    const m = { l: 40, r: 120, t: 24, b: 34 };
    const x = d3.scalePoint().domain(th).range([m.l, w - m.r]);
    const ymax = Math.min(10, d3.max(series, (s) => d3.max(s.pts, (p) => p[1])) ?? 5);
    const y = d3.scaleLinear().domain([0, ymax]).nice().range([h - m.b, m.t]);
    const svg = d3.select(root).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
      .attr("aria-label", "Distance from the final share by share counted, model projection and raw count");
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
    svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", h - 4).text("share of the vote counted");
    svg.append("text").attr("class", "panel-label").attr("x", m.l - 34).attr("y", 10).text("points from the final share");
    const line = d3.line().defined((p) => p[1] != null).x((p) => x(p[0])).y((p) => y(Math.min(ymax, p[1])));
    const g = svg.append("g");
    for (const s of series.filter((x) => x.thin)) {
      g.append("path").datum(s.pts).attr("class", "ev-thin").attr("fill", "none").attr("stroke", s.k === "model" ? C.model : C.raw).attr("stroke-width", 1.2).attr("d", line)
        .append("title").text(`${names.get(s.id) ?? s.id}: ${s.k === "model" ? "model projection" : "raw count"}`);
    }
    for (const s of series.filter((x) => !x.thin)) {
      g.append("path").datum(s.pts).attr("fill", "none").attr("stroke", s.k === "model" ? C.model : C.raw).attr("stroke-width", 3).attr("d", line);
      const last = [...s.pts].reverse().find((p) => p[1] != null);
      if (last) svg.append("text").attr("class", "end-label").attr("x", x(last[0]) + 8).attr("y", y(last[1]) + (s.k === "model" ? 12 : -4)).attr("fill", s.k === "model" ? C.model : C.raw)
        .text(s.k === "model" ? "projection, average" : "raw count, average");
    }
  };
  draw();
  new ResizeObserver(() => draw()).observe(root);
}

// 2. share of the count with the final result inside the 90% band, per election; pooled reliability
function drawCalibration(S) {
  const cal = S.calibration;
  if (!cal) return;
  $("ev-cal").hidden = false;
  const rows = S.elections.filter((e) => cal.by_election[e.id]?.coverage90 != null).map((e) => ({ ...e, ...cal.by_election[e.id] }));
  const lo = d3.least(rows, (r) => r.coverage90);
  const rel = cal.reliability;
  const byMin = rel?.by_election ? Object.entries(rel.by_election).sort((a, b) => (b[1].minutes ?? 0) - (a[1].minutes ?? 0)) : [];
  const dom = byMin[0] && rel.minutes ? `${names.get(byMin[0][0]) ?? byMin[0][0]} accounts for ${byMin[0][1].minutes.toLocaleString("en-US")} of the ${rel.minutes.toLocaleString("en-US")} minutes in the probability plot, so it mostly shows that election.` : "";
  $("ev-cal-lead").textContent = `A 90% band of the right width contains the final result in about 90% of the minutes. Across ${rows.length} elections the share ran from ${pct(lo.coverage90, 0)} (${lo.name}) to ${pct(d3.max(rows, (r) => r.coverage90), 0)}. ${dom}`;

  const covRoot = $("chart-coverage");
  const drawCov = () => {
    // each election: its name on one line, its share as a dot on the track below
    const w = Math.max(260, covRoot.clientWidth), rh = 36, m = { l: 4, r: 44, t: 22, b: 26 };
    const h = m.t + rows.length * rh + m.b;
    const x = d3.scaleLinear().domain([Math.min(0.6, d3.min(rows, (r) => r.coverage90)), 1]).range([m.l, w - m.r]);
    const svg = d3.select(covRoot).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img").attr("aria-label", "Share of the count inside the 90% band, per election");
    svg.append("line").attr("x1", x(0.9)).attr("x2", x(0.9)).attr("y1", m.t - 6).attr("y2", h - m.b).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
    svg.append("text").attr("class", "legend-text").attr("x", x(0.9)).attr("y", m.t - 9).attr("text-anchor", "middle").text("90%");
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickFormat(d3.format(".0%")).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    const g = svg.selectAll("g.row").data(rows).join("g").attr("class", "row").attr("transform", (d, i) => `translate(0,${m.t + i * rh})`);
    g.append("text").attr("class", "row-name").attr("x", m.l).attr("y", 11).text((d) => d.name);
    g.append("line").attr("x1", x.range()[0]).attr("x2", (d) => x(d.coverage90)).attr("y1", 24).attr("y2", 24).attr("stroke", "#d8d5cd");
    g.append("circle").attr("cx", (d) => x(d.coverage90)).attr("cy", 24).attr("r", 4.5).attr("fill", C.model).append("title").text((d) => `${d.name}: ${pct(d.coverage90, 1)} (${d.basis})`);
    g.append("text").attr("class", "legend-text").attr("x", (d) => x(d.coverage90) + 8).attr("y", 24).attr("dy", "0.35em").text((d) => pct(d.coverage90, 0));
  };
  drawCov();
  new ResizeObserver(() => drawCov()).observe(covRoot);

  const relRoot = $("chart-reliability");
  if (!rel?.model?.length) { relRoot.hidden = true; return; }
  const drawRel = () => {
    const w = Math.min(360, Math.max(240, relRoot.clientWidth)), h = w, m = { l: 36, r: 10, t: 10, b: 34 };
    const x = d3.scaleLinear().domain([0, 1]).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    const r = d3.scaleSqrt().domain([0, d3.max([...rel.model, ...(rel.market ?? [])], (d) => d.minutes) || 1]).range([1.5, 9]);
    const svg = d3.select(relRoot).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img").attr("aria-label", "Probability given against how often it happened, model and market");
    svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickSize(3));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(3));
    svg.append("text").attr("class", "legend-text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 2).attr("text-anchor", "middle").text("probability given");
    for (const [list, color] of [[rel.market, C.market], [rel.model, C.model]]) {
      const pts2 = (list ?? []).filter((d) => d.minutes > 0 && d.mean_p != null && d.freq != null);
      svg.append("path").datum(pts2).attr("fill", "none").attr("stroke", color).attr("opacity", 0.6).attr("d", d3.line().x((d) => x(d.mean_p)).y((d) => y(d.freq)));
      svg.append("g").selectAll("circle").data(pts2).join("circle").attr("cx", (d) => x(d.mean_p)).attr("cy", (d) => y(d.freq)).attr("r", (d) => r(d.minutes)).attr("fill", color).attr("fill-opacity", 0.85)
        .append("title").text((d) => `${d.bin}: given ${d.mean_p.toFixed(2)}, happened ${pct(d.freq, 0)} (${d.minutes.toLocaleString("en-US")} minutes, ${d.races} races)`);
    }
  };
  drawRel();
  relRoot.insertAdjacentHTML("beforeend", `<figcaption>How often the outcome happened (up) against the probability given (across), in ten bins of minutes: <span class="m">model</span>, <span class="k">market price</span>. On the dashed line the probabilities were right on average; dot size is the number of minutes.</figcaption>`);
  new ResizeObserver(() => { const cap = relRoot.querySelector("figcaption"); drawRel(); relRoot.append(cap); }).observe(relRoot);
}

// 3. $10,000 per election. Headline: the model-limit rule (invest10k.headline); the stricter
// signal-price rule and the sensitivity grid sit beside it.
const SHARED_NOTES = [/Single event:[^.]*\.\s*/i, /Eligible volume is an upper bound:[^.]*\.\s*/i];
const shareOfGain = (s) => (s >= 0.995 && s < 1 ? `Almost all (${(100 * s).toFixed(1)}%)` : pct(s, 0));
const MODEL_LIMIT_RULE = "The rule: from the moment 1% of the vote is counted, buy 10% of every real trade (Israel: every offer in the recorded order book, one snapshot per release) on the side the model favours whose price, fees included, is at least 5¢ below the model's probability as known 60 seconds earlier; no limit per market; keep buying until the $10,000 is spent and hold to the result.";
function drawMoney(S) {
  const inv = S.invest10k;
  if (!inv) return;
  $("ev-money").hidden = false;
  const p = inv.pooled, sp = inv.signal_price;
  const markets = d3.sum(Object.values(inv.by_election), (b) => b.markets ?? 0);
  const strictPooled = sp?.pooled ? ` Buying only at each signal's own price, the same ${money(sp.pooled.bankroll)} would have become ${money(sp.pooled.ending_value)} (${signedPct(sp.pooled.return_pct)}).` : "";
  $("ev-money-lead").innerHTML = p
    ? `${esc(`Across ${p.elections} elections, ${money(p.bankroll)} became ${money(p.ending_value)} (${signedPct(p.return_pct)}): ${p.bets.toLocaleString("en-US")} trades in ${markets} markets, ${p.won.toLocaleString("en-US")} of them on the winning side.${strictPooled}`)}<span id="money-conc"></span>
      Each election is one outcome: all of its markets ride on the same count, so these are a handful of results, not many independent bets.${inv.headline === "model_limit" ? " This buying rule was defined after the outcomes were known." : ""}`
    : "";
  $("ev-money-rule").textContent = `${inv.headline === "model_limit" ? MODEL_LIMIT_RULE : inv.rule ?? ""} The trades the rule could take are an upper bound for one trader: other traders took them.`;
  const list = S.elections.filter((e) => inv.by_election[e.id]);
  const root = $("money");
  root.innerHTML = list.map((e) => {
    const b = inv.by_election[e.id];
    const strict = sp?.by_election?.[e.id];
    return `<figure class="money-cell" data-id="${esc(e.id)}">
      <figcaption><a href="${href(e.id)}">${esc(e.name)}</a></figcaption>
      <p class="money-end"><b>${money(b.ending_value)}</b> <span class="${b.return_pct > 0 ? "up" : b.return_pct < 0 ? "down" : ""}">${signedPct(b.return_pct)}</span></p>
      ${strict ? `<p class="money-strict">buying only at the signal's own price: ${money(strict.ending_value)}</p>` : ""}
      <div class="money-chart"></div>
      <p class="money-races" hidden></p>
      <p class="money-note">${esc(moneySentence(b))}</p>
    </figure>`;
  }).join("");
  $("money-variants").innerHTML = gridTable(inv.grid, list, sp);
  $("money-variants").hidden = !$("money-variants").innerHTML;
  const drawCharts = () => {
    for (const cell of root.querySelectorAll(".money-cell")) {
      const b = inv.by_election[cell.dataset.id];
      const box = cell.querySelector(".money-chart");
      box.innerHTML = "";
      const path = (b.value_path ?? []).map((d) => ({ t: Date.parse(d.t), v: d.value }));
      if (path.length < 2) { box.classList.add("empty"); continue; }
      const w = Math.max(160, box.clientWidth), h = 64;
      const x = d3.scaleUtc().domain(d3.extent(path, (d) => d.t)).range([2, w - 2]);
      const ext = d3.extent([...path.map((d) => d.v), b.bankroll]);
      const pad = Math.max(50, (ext[1] - ext[0]) * 0.1);
      const y = d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).range([h - 4, 4]);
      const svg = d3.select(box).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("aria-hidden", "true");
      svg.append("line").attr("x1", 0).attr("x2", w).attr("y1", y(b.bankroll)).attr("y2", y(b.bankroll)).attr("stroke", "#c9c6bd").attr("stroke-dasharray", "2,3");
      svg.append("path").datum(path).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.5).attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.v)).curve(d3.curveStepAfter));
      for (const bet of b.bets_path ?? []) {
        const t = Date.parse(bet.t);
        const fills = bet.n_fills != null ? `${bet.n_fills} ${bet.n_fills === 1 ? "trade" : "trades"}, ` : "";
        svg.append("circle").attr("cx", x(t)).attr("cy", y(d3.least(path, (d) => Math.abs(d.t - t))?.v ?? b.bankroll)).attr("r", 2.5).attr("fill", bet.won ? C.model : C.raw)
          .append("title").text(`${bet.side} ${bet.market}: ${fills}$${Math.round(bet.stake).toLocaleString("en-US")} staked, ${bet.won ? "won" : "lost"} (${bet.pnl >= 0 ? "+" : "−"}$${Math.abs(Math.round(bet.pnl)).toLocaleString("en-US")})`);
      }
    }
  };
  drawCharts();
  new ResizeObserver(() => drawCharts()).observe(root);
  concentration(inv, p);
  for (const cell of root.querySelectorAll(".money-cell")) topRaces(cell, inv.by_election[cell.dataset.id]);
}

// Under each election's line: the (up to) three races that made the $10,000 the most, named from
// the election's meta. Entries keyed by market id (Hungary) are added up per race. Hidden when
// by_race is absent.
async function topRaces(cell, b) {
  const entries = Object.entries(b?.by_race ?? {});
  if (!entries.some(([, r]) => r.bankroll_pnl)) return;
  const id = cell.dataset.id;
  const meta = await getJSON(`${id}/meta.json`, { optional: true, root: rootOf(id) }).catch(() => null);
  const races = meta?.races ?? [];
  const raceOf = (rid, r) => races.find((x) => x.race_id === rid) ?? races.find((x) => (x.markets ?? []).some((m) => m.id === r.market));
  const byRace = d3.rollups(entries, (list) => ({ pnl: d3.sum(list, ([, r]) => r.bankroll_pnl ?? 0), stake: d3.sum(list, ([, r]) => r.bankroll_stake ?? 0), vol: d3.sum(list, ([, r]) => r.eligible_volume ?? 0) }),
    ([rid, r]) => raceOf(rid, r)?.name ?? `${rid[0].toUpperCase()}${rid.slice(1).replace(/_/g, " ")}`);
  const top = byRace.filter(([, v]) => v.pnl).sort((x, y) => y[1].pnl - x[1].pnl).slice(0, 3);
  if (!top.length) return;
  const sgn = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`;
  const el = cell.querySelector(".money-races");
  el.innerHTML = `${top.length === 1 ? "From one race" : `Top ${top.length} races`}: ${top.map(([name, v]) => `<span title="${esc(`$${Math.round(v.stake).toLocaleString("en-US")} staked; trades that qualified: $${Math.round(v.vol).toLocaleString("en-US")}`)}">${esc(name)} ${sgn(v.pnl)}</span>`).join(" · ")}`;
  el.hidden = false;
}

// The single market behind most of the pooled gain, named by its question from the election's meta.
async function concentration(inv, p) {
  const gain = p ? p.ending_value - p.bankroll : 0;
  const [id, b] = d3.greatest(Object.entries(inv.by_election), ([, x]) => x.largest_market_pnl ?? -Infinity) ?? [];
  if (!b?.largest_market || !(gain > 0) || !(b.largest_market_pnl > gain / 2)) return;
  const meta = await getJSON(`${id}/meta.json`, { optional: true, root: rootOf(id) }).catch(() => null);
  const mk = meta?.races?.flatMap((r) => r.markets ?? []).find((m) => m.id === b.largest_market);
  const what = mk?.question ? `“${mk.question.replace(/\?$/, "")}”` : "one market";
  const el = $("money-conc");
  if (el) el.textContent = ` Most of the gain is one market: ${money(b.largest_market_pnl)} of the ${money(gain)} came from ${what} in the ${names.get(id) ?? id}.`;
}

function moneySentence(b) {
  const parts = [];
  if (!b.bets) parts.push("No trade qualified.");
  else {
    parts.push(`${b.bets.toLocaleString("en-US")} ${b.bets === 1 ? "trade" : "trades"} in ${b.markets} ${b.markets === 1 ? "market" : "markets"}, ${b.won.toLocaleString("en-US")} on the winning side.`);
    if (b.largest_market_share != null && b.largest_market_share >= 0.5 && b.markets > 1) parts.push(`${shareOfGain(b.largest_market_share)} of the gain came from one market.`);
    else if (b.return_pct < 0 && b.largest_market_pnl != null && b.largest_market_pnl < 0) parts.push(`The largest loss was ${money(-b.largest_market_pnl)} in one market.`);
  }
  if (b.outcome && /provisional|not yet/i.test(b.outcome)) parts.push("Markets not yet resolved.");
  let note = b.note ?? "";
  for (const re of SHARED_NOTES) note = note.replace(re, "");
  note = note.replace(/\d+% of the eligible volume was in one market \([^)]*\)\.\s*/i, "").trim();
  if (note) parts.push(note[0].toUpperCase() + note.slice(1) + (note.endsWith(".") ? "" : "."));
  return parts.join(" ");
}

// Ending value of $10,000 under each variant of the rule (invest10k.grid), and the strict rule.
const GRID_COLS = [
  ["edge5c_delay60s", "10pct", "Headline: 5¢, 60 s, 10% of each trade"],
  ["edge5c_delay60s", "first", "All of each trade"],
  ["edge5c_delay60s", "10pct_cap", "$1,000 cap per market"],
  ["edge10c_delay60s", "10pct", "10¢ below the model"],
  ["edge20c_delay60s", "10pct", "20¢ below the model"],
  ["edge5c_delay300s", "10pct", "300 s delay"],
];
function gridTable(grid, list, sp) {
  const rows = list.filter((e) => grid?.[e.id]);
  if (!rows.length) return "";
  return `<summary>The same $10,000 under other versions of the rule</summary><div class="table-scroll"><table class="compact"><thead><tr><th>Election</th>${GRID_COLS.map(([, , l]) => `<th class="n">${l}</th>`).join("")}${sp ? `<th class="n">Only at the signal's price</th>` : ""}</tr></thead><tbody>${rows.map((e) => {
    const g = grid[e.id];
    const v = (k, mode) => (g[k]?.[mode] != null ? money(g[k][mode]) : "–");
    return `<tr><td>${esc(e.name)}</td>${GRID_COLS.map(([k, mode]) => `<td class="n">${v(k, mode)}</td>`).join("")}${sp ? `<td class="n">${sp.by_election?.[e.id] ? money(sp.by_election[e.id].ending_value) : "–"}</td>` : ""}</tr>`;
  }).join("")}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- elections list */

const lazy = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    lazy.unobserve(e.target);
    spark(e.target);
  }
}, { rootMargin: "200px" });

function render(elections) {
  names = new Map([...names, ...elections.map((e) => [e.id, e.name])]);
  // countries ordered by their most recent election; elections newest first
  const groups = d3.groups(elections, (e) => e.country ?? "");
  const latest = (list) => d3.max(list, (e) => e.date);
  years = new Map(elections.map((e) => [e.id, e.date?.slice(0, 4)]));
  groups.sort((a, b) => latest(b[1]).localeCompare(latest(a[1])));
  tbody.innerHTML = groups.map(([c, list]) => `<tr class="country"><td colspan="7">${esc(countryName(c))}</td></tr>${
    [...list].sort((a, b) => b.date.localeCompare(a.date)).map((e) => `<tr data-id="${esc(e.id)}">
        <td class="e-name"><a href="${href(e.id)}">${esc(e.name)}</a><span class="e-sum">${esc(e.summary ?? "")}</span></td>
        <td>${esc(fmtDate(e.date))}</td>
        <td><span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span></td>
        <td class="n">${e.n_units != null ? `${e.n_units.toLocaleString("en-US")} ` : ""}<span class="note">${esc(plural(e.unit_level, e.n_units))}</span></td>
        ${e.status === "scheduled"
          ? `<td class="sched" colspan="3" data-pre="${esc(e.id)}">Runs live on election night</td>`
          : `<td class="n lead">–</td><td class="n cal">–</td><td class="sp" data-id="${esc(e.id)}"><svg class="spark" width="150" height="34" aria-hidden="true"></svg></td>`}
      </tr>`).join("")}`).join("");
  tbody.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", (ev) => { if (!ev.target.closest("a")) location.href = href(tr.dataset.id); });
    const sp = tr.querySelector(".sp");
    if (sp) lazy.observe(sp);
    const pre = tr.querySelector("[data-pre]");
    if (pre) preCell(pre);
  });
}

// A scheduled election in its "pre-count" phase: the model's chance before any votes against
// Polymarket's current price (read in the browser) and the last recorded Kalshi price. Three
// headline markets (who wins / most seats / control first, then by Polymarket volume) by their short
// names, the rest behind a toggle.
const HEADLINE = /^most-|winner|control|^pres|prime-minister|chamber|^win-/;
const SHOWN = 3;
async function preCell(td) {
  const id = td.dataset.pre;
  const m = await getJSON(`${id}/meta.json`, { optional: true, root: rootOf(id) }).catch(() => null);
  if (m?.phase !== "pre-count" || !m.races?.length) return;
  const timeline = await getJSON(`${id}/timeline.json`, { optional: true, root: rootOf(id) }).catch(() => null);
  const lastRow = (rid) => { const rows = (timeline?.[rid] ?? []).filter((d) => d.t); return rows[rows.length - 1] ?? null; };
  const pm = (r) => (r.markets ?? []).find((x) => x.platform === "polymarket" && x.id);
  const volume = new Map();
  const yes = await polymarketYes(m.races.map((r) => pm(r)?.id), volume).catch(() => new Map());
  const live = new Map(m.races.map((r) => {
    const y = pm(r) ? yes.get(pm(r).id) : null;
    return [r.race_id, y == null ? null : pm(r).yes_is === "b" ? 1 - y : y];
  }));
  const f = (x) => (x == null ? "–" : x.toFixed(2));
  const regions = new Map((m.regions ?? []).map((r) => [r.id, r.name]));
  // the short name, with its region and the side the chances are for where the name lacks them
  const label = (r) => {
    let s = shortName(r);
    if (r.region && !s.includes(regions.get(r.region) ?? "")) s = `${regions.get(r.region)} ${s}`;
    return r.a?.name && !s.includes(r.a.name) ? `${esc(s)} <span class="note">${esc(r.a.name)}</span>` : esc(s);
  };
  const anyLive = [...live.values()].some((v) => v != null);
  const order = m.races.map((r, i) => ({ r, i, head: HEADLINE.test(r.race_id) ? 0 : 1, vol: volume.get(pm(r)?.id) ?? -1 }))
    .sort((a, b) => a.head - b.head || b.vol - a.vol || a.i - b.i).map((x) => x.r);
  const row = (r) => {
    const last = lastRow(r.race_id);
    const v = r.poll_model ?? "v2"; // the race's "with polls" model version
    return `<tr><td title="${esc(r.name)}">${label(r)}</td><td class="n m">${f(last?.[`P_${v}`] ?? modelP(last) ?? r.precount?.[`P_${v}`] ?? r.precount?.P)}</td><td class="n">${f(live.get(r.race_id) ?? last?.pm)}</td><td class="n kx">${f(last?.kalshi)}</td></tr>`;
  };
  const rest = order.length - SHOWN;
  const note = `Before the count: the model's chance before any votes (${m.races.some((r) => lastRow(r.race_id)?.P_v2 != null || r.precount?.P_v2 != null) ? "from past certified results shifted toward the recent polls, where a race has them" : "from past certified results, not polls"}), ${anyLive ? "Polymarket's price now" : "the last recorded Polymarket price"} and the last recorded Kalshi price. Counting starts ${fmtDate(m.date)}.`;
  td.classList.add("pre");
  td.innerHTML = `<table class="pre-races"><thead><tr><th>Chance the named side wins</th><th class="n">Model</th><th class="n">Poly&shy;market</th><th class="n">Kalshi</th></tr></thead>
    <tbody>${order.slice(0, SHOWN).map(row).join("")}</tbody>${rest > 0 ? `<tbody class="pre-more" hidden>${order.slice(SHOWN).map(row).join("")}</tbody>` : ""}</table>
    ${rest > 0 ? `<button type="button" class="pre-toggle" aria-expanded="false">+${rest} more market${rest === 1 ? "" : "s"}</button>` : ""}
    <p class="note pre-note" title="${esc(note)}">${esc(note)}</p>`;
  const btn = td.querySelector(".pre-toggle");
  btn?.addEventListener("click", (ev) => {
    ev.stopPropagation(); // the row itself links to the election
    const more = td.querySelector(".pre-more"), open = more.hidden;
    more.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "Fewer markets" : `+${rest} more market${rest === 1 ? "" : "s"}`;
  });
}

const mainRace = (m) => m?.races?.find((r) => !r.region) ?? m?.races?.[0];

// Lead, calibration and the sparkline are filled in when the row scrolls into view.
async function spark(td) {
  const id = td.dataset.id;
  const tr = td.closest("tr");
  const m = await getJSON(`${id}/meta.json`, { optional: true, root: rootOf(id) });
  const race = mainRace(m);
  if (!race) return;
  const [stats, timeline] = await Promise.all([getJSON(`${id}/stats.json`, { optional: true, root: rootOf(id) }), getJSON(`${id}/timeline.json`, { optional: true, root: rootOf(id) })]);
  const rows = (timeline?.[race.race_id] ?? []).map((d) => ({ ...d, tms: Date.parse(d.t) }));
  const gate = m.count_timeline === false ? null : rows.find((d) => d.counted >= 0.01);
  const lead = leadMinutes(gatedSettle(stats, race.race_id, gate?.t));
  if (lead != null) tr.querySelector(".lead").textContent = `${lead > 0 ? "+" : lead < 0 ? "−" : ""}${Math.abs(lead)}`;
  const cal = stats?.calibration?.[race.race_id];
  if (cal?.coverage90_1_95 != null) tr.querySelector(".cal").textContent = pct(cal.coverage90_1_95, 0);
  if (!rows.length) return;
  const vis = gate ? rows.filter((d) => d.tms >= gate.tms - 15 * 60000) : rows;
  const w = 150, h = 34;
  const x = d3.scaleLinear().domain(d3.extent(vis, (d) => d.tms)).range([1, w - 1]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - 2, 2]);
  const svg = d3.select(td).select("svg").attr("viewBox", `0 0 ${w} ${h}`);
  svg.append("line").attr("x1", 0).attr("x2", w).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#e4e2dc");
  const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.tms)).y((d) => y(d[k])).curve(d3.curveStepAfter);
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.kalshi).attr("stroke-width", 1.1).attr("d", line("kalshi"));
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.2).attr("d", line("pm"));
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 1.5).attr("d", line("P"));
  td.title = `${race.name}: probability ${race.a.name} wins`;
}

async function load() {
  try {
    const [elections, summary] = await Promise.all([getJSON("elections.json"), getJSON("summary.json", { optional: true })]);
    liveIds = new Set(elections.filter((e) => e.status === "live" || e.status === "scheduled").map((e) => e.id));
    render(elections);
    if (!load.world) {
      load.world = true;
      getJSON("geo/world.topo.json", { root: STATIC })
        .then((topo) => {
          window.__worldFrames = [];
          const wm = createWorldMap($("world"), { topo, elections, href, countryName, dataRoot: rootOf, frames: window.__worldFrames });
          window.__world = wm; // for the frame and screenshot checks
          // back from an election's breadcrumb: open on its country and zoom out to the world
          const from = elections.find((e) => e.id === new URLSearchParams(location.search).get("from"));
          if (from?.country) wm.arrive(from.country.toUpperCase());
        })
        .catch((err) => { $("world").innerHTML = `<p class="note">The world map could not be drawn (${esc(err.message)}).</p>`; });
    }
    if (summary && !load.drawn) {
      load.drawn = true;
      // chart labels carry the year: two elections can share a name (Brazil's 2022 and 2026 runoffs)
      const year = new Map(elections.map((e) => [e.id, e.date?.slice(0, 4)]));
      summary.elections = (summary.elections ?? []).map((e) => ({ ...e, name: year.get(e.id) ? `${e.name}, ${year.get(e.id)}` : e.name }));
      names = new Map([...names, ...summary.elections.map((e) => [e.id, e.name])]);
      drawAll(summary);
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="note">Could not load the list of elections (${esc(err.message)}).</td></tr>`;
  }
}

if (LIVE) {
  const note = $("live-note");
  note.hidden = false;
  note.textContent = `Live data from ${LIVE}, refreshed every 15 seconds.`;
  setInterval(load, 15000);
}
load();
