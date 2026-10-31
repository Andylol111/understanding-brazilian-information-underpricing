// Homepage: the list of elections (elections.json and each election's files), the $10,000 cards
// and the Polymarket-against-Kalshi table (site/data/summary.json, written by analysis/edge_audit.py).
// The charts are in landing-charts.js; the data helpers they share are in landing-data.js.
import * as d3 from "./vendor/d3.js";
import { C, LIVE, STATIC, getJSON, esc, pct, fmtDate, plural, modelP, shortName, clock, EVIDENCE } from "./common.js";
import { polymarketYes } from "./dash-precount.js";
import { createWorldMap } from "./world-map.js";
import { $, href, rootOf, setLiveIds, setElections, allElections, label, labelOf, bareName, qualifierOf, unitWord, metaOf, statsOf, timelineOf, leadOf, decisionOf, mainRace, hasMarkets, featured, money, signedPct, dur } from "./landing-data.js";
import { drawNight, drawLead, drawError, drawCalibration, drawMix } from "./landing-charts.js";

const tbody = document.querySelector("#overview tbody");
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countryName = (c) => { try { return c ? regionNames.of(c) : "Other"; } catch { return c; } };
const longDate = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
const fmtInt = (n) => n.toLocaleString("en-US");

function drawAll(S) {
  drawError(S);
  drawCalibration(S);
  drawMoney(S);
  drawVenues(S);
}

// Polymarket against Kalshi (summary.kalshi, written by analysis/kalshi_export.py; absent: hidden)
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
  $("venues").innerHTML = `<thead><tr><th>Election</th><th class="n">Races on both</th><th class="n">Settled first: Kalshi / Polymarket / same</th><th class="n">Median difference</th><th class="n">Minutes buying both sides paid</th><th class="n">Largest (¢ per $1)</th><th class="n">Fillable in the same minute (net)</th></tr></thead>
    <tbody>${rows.map((e) => {
      const s = e.settle ?? {}, a = e.arbitrage ?? {};
      return `<tr><td><a href="election.html?id=${encodeURIComponent(e.id)}">${esc(labelOf(e.id))}</a></td><td class="n">${e.races ?? "–"}</td>
        <td class="n">${s.compared ? `${s.kalshi_first ?? 0} / ${s.polymarket_first ?? 0} / ${s.same_minute ?? 0}` : "–"}</td><td class="n">${med(s.median_kalshi_minus_pm_min)}</td>
        <td class="n">${a.minutes_with_net_arb ?? "–"}</td><td class="n">${a.max_net_edge != null ? (100 * a.max_net_edge).toFixed(1) : "–"}</td>
        <td class="n">${a.fillable_usd != null ? `${usdOf(a.fillable_usd)} (${usdOf(a.fillable_net_usd)})` : "–"}</td></tr>`;
    }).join("")}</tbody>`;
}

/* ---------------------------------------------------------------- $10,000 per election */

// The first rule note every election repeats ("Single event ...", "Eligible volume ...") is said once, in the text above the cards.
const SHARED_NOTES = [/Single event:[^.]*\.\s*/i, /Eligible volume is an upper bound:[^.]*\.\s*/i];
const shareOfGain = (s) => (s >= 0.995 && s < 1 ? `Almost all (${(100 * s).toFixed(1)}%)` : pct(s, 0));
const FIXED_AFTER = "We wrote the rule after we knew the results.";

// What a card says for an election the rule was not run on, and why (m: its meta, where there is one).
function stateOf(e, m) {
  if (e.status === "scheduled") return { head: `After the count on ${longDate(e.date)}`, note: "The rule starts once 1% of the vote is counted." };
  if (e.status === "live") return { head: "Counting now", note: "The rule starts once 1% of the vote is counted." };
  if (m?.count_timeline === false) return { head: "No count over time", note: "The official count over time was not published, so the model cannot run." };
  if (m && !hasMarkets(m)) return { head: "No market during the count", note: "No market prices are available for this night, so there was nothing to buy." };
  return { head: "Not run", note: "The rule has not been run on this election." };
}

function moneySentence(b) {
  const parts = [];
  if (!b.bets) parts.push("The rule found nothing to buy.");
  else if (b.markets) {
    parts.push(`${fmtInt(b.bets)} ${b.bets === 1 ? "trade" : "trades"} in ${b.markets} ${b.markets === 1 ? "market" : "markets"}. ${b.won === b.bets ? (b.bets === 1 ? "It was" : "All were") : `${fmtInt(b.won)} were`} on the winning side.`);
    if (b.largest_market_share != null && b.largest_market_share >= 0.5 && b.markets > 1) parts.push(`${shareOfGain(b.largest_market_share)} of the gain came from one market.`);
    else if (b.return_pct < 0 && b.largest_market_pnl != null && b.largest_market_pnl < 0) parts.push(`The largest loss was ${money(-b.largest_market_pnl)} in one market.`);
  } else parts.push(`${fmtInt(b.bets)} ${b.bets === 1 ? "trade" : "trades"}. ${fmtInt(b.won)} on the winning side.`);
  if (b.outcome && /provisional|not yet/i.test(b.outcome)) parts.push("The result is provisional: the markets have not settled.");
  if (b.value_path == null) parts.push("Totals only: bet times and the bankroll path are not published, because the count data is licensed for private research.");
  else {
    let note = b.note ?? "";
    for (const re of SHARED_NOTES) note = note.replace(re, "");
    note = note.replace(/\d+% of the eligible volume was in one market \([^)]*\)\.\s*/i, "").trim();
    if (note) parts.push(note[0].toUpperCase() + note.slice(1) + (note.endsWith(".") ? "" : "."));
  }
  return parts.join(" ");
}

async function drawMoney(S) {
  const inv = S.invest10k;
  if (!inv) return;
  $("ev-money").hidden = false;
  const p = inv.pooled, sp = inv.signal_price;
  const markets = d3.sum(Object.values(inv.by_election), (b) => b.markets ?? 0);
  const model = inv.headline === "model_limit";
  const F = await featured().catch(() => null);
  const side = F?.race?.a?.name;
  const price = F?.gate?.pm, chance = F?.gate?.P, won = F?.rows?.at(-1)?.P >= 0.99;
  const example = F && price != null && chance != null
    ? `<p class="example"><b>Example.</b> At ${clock(F.gate.tms, F.tz)}, with 1% counted, the model gave ${esc(side)} ${pct(chance, 0)}. Polymarket priced a ${esc(side)} win at ${pct(price, 0)}. The gap is far above 5¢, so the rule buys at ${pct(price, 0)}.${won ? ` ${esc(side)} won, so each share paid $1.` : ""}</p>`
    : "";
  const noPath = Object.entries(inv.by_election).filter(([, b]) => b.value_path == null).map(([id]) => labelOf(id));

  $("money-what").innerHTML = `
    <p class="lede">This is a thought experiment, not a trading strategy.</p>
    <p>Start with $10,000 in each election. Apply one fixed buying rule to the real trades made that night. Then see what the money is worth after the result.</p>
    ${p ? `<p><b>Across ${p.elections} elections, ${money(p.bankroll)} became ${money(p.ending_value)} (${signedPct(p.return_pct)}).</b> The rule made ${fmtInt(p.bets)} trades in ${markets} markets. ${fmtInt(p.won)} were on the winning side.<span id="money-conc"></span></p>
    ${sp?.pooled ? `<p>A stricter rule buys only at the price when each signal appeared, and stakes at most $1,000 per signal. With it, the same ${money(sp.pooled.bankroll)} became ${money(sp.pooled.ending_value)} (${signedPct(sp.pooled.return_pct)}).</p>` : ""}` : ""}
    <h3>Why it proves little</h3>
    <ul class="caveats">
      ${model ? `<li><b>${FIXED_AFTER}</b> A rule chosen after the fact looks good on the past. It says little about the future.</li>` : ""}
      <li><b>Each election is one outcome.</b> All its markets depend on the same count, so they move together. ${p ? `${p.elections} elections are ${p.elections} results, not ${fmtInt(p.bets)} independent bets.` : ""}</li>
      <li><b>This is a best case.</b> Other traders took the trades the rule would buy. One trader could not have had all of them.</li>
      ${noPath.map((n) => `<li><b>${esc(n)} shows totals only.</b> Its count data is licensed for private research, so bet times and the bankroll path are not published.</li>`).join("")}
    </ul>`;
  $("money-rule").innerHTML = model
    ? `<h3>The rule</h3>
    <ol class="steps">
      <li>Wait until 1% of the vote is counted.</li>
      <li>From then on, look at every real trade on Polymarket. For Israel, look at every offer in the recorded order book, one snapshot per release.</li>
      <li>Compare the trade's price, fees included, with the model's chance from 60 seconds earlier.</li>
      <li>If the price is at least 5¢ below the model's chance, buy 10% of that trade on the side the model favours.</li>
      <li>Keep buying until the $10,000 is spent. There is no limit per market. Hold every position until the result.</li>
    </ol>${example}`
    : `<h3>The rule</h3><p>${esc(inv.rule ?? "")}</p>`;

  // every election gets a card: the ones the rule ran on first (newest first), then the rest with the reason
  const byDate = (a, b) => b.date.localeCompare(a.date);
  const all = allElections();
  const ran = all.filter((e) => inv.by_election[e.id]).sort(byDate);
  const rest = all.filter((e) => !inv.by_election[e.id]).sort(byDate);
  const metas = new Map(await Promise.all(rest.map(async (e) => [e.id, await metaOf(e.id)])));
  const root = $("money");
  root.innerHTML = [...ran, ...rest].map((e) => {
    const b = inv.by_election[e.id];
    const strict = sp?.by_election?.[e.id];
    if (!b) {
      const st = stateOf(e, metas.get(e.id));
      return `<article class="money-cell empty" data-id="${esc(e.id)}">
        <h3 class="mc-name"><a href="${href(e.id)}" title="${esc(e.name)}">${esc(label(e))}</a></h3>
        <p class="mc-main"><span class="mc-state">${esc(st.head)}</span></p>
        <p class="mc-alt"></p>
        <div class="money-chart empty"></div>
        <p class="mc-facts">${esc(st.note)}</p>
        <p class="mc-races" hidden></p>
      </article>`;
    }
    return `<article class="money-cell" data-id="${esc(e.id)}">
      <h3 class="mc-name"><a href="${href(e.id)}" title="${esc(e.name)}">${esc(label(e))}</a></h3>
      <p class="mc-main"><b>${money(b.ending_value)}</b> <span class="${b.return_pct > 0 ? "up" : b.return_pct < 0 ? "down" : ""}">${signedPct(b.return_pct)}</span></p>
      <p class="mc-alt">${strict ? `Stricter rule: ${money(strict.ending_value)}` : ""}</p>
      <div class="money-chart"></div>
      <p class="mc-facts">${esc(moneySentence(b))}</p>
      <p class="mc-races" hidden></p>
    </article>`;
  }).join("");
  $("money-variants").innerHTML = gridTable(inv.grid, ran, sp);
  $("money-variants").hidden = !$("money-variants").innerHTML;
  const drawCharts = () => {
    for (const cell of root.querySelectorAll(".money-cell:not(.empty)")) {
      const b = inv.by_election[cell.dataset.id];
      const box = cell.querySelector(".money-chart");
      box.innerHTML = "";
      const path = (b.value_path ?? []).map((d) => ({ t: Date.parse(d.t), v: d.value }));
      box.classList.toggle("none", path.length < 2);
      if (path.length < 2) continue;
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
  let cardW = root.clientWidth;
  new ResizeObserver(() => { if (root.clientWidth !== cardW) { cardW = root.clientWidth; drawCharts(); } }).observe(root);
  concentration(inv, p);
  for (const cell of root.querySelectorAll(".money-cell:not(.empty)")) topRaces(cell, inv.by_election[cell.dataset.id]);
}

// Under each card: the (up to) three races that made the $10,000 the most, named from
// the election's meta. Entries keyed by market id (Hungary) are added up per race. Hidden when
// by_race is absent.
async function topRaces(cell, b) {
  const entries = Object.entries(b?.by_race ?? {});
  if (!entries.some(([, r]) => r.bankroll_pnl)) return;
  const id = cell.dataset.id;
  const meta = await metaOf(id);
  const races = meta?.races ?? [];
  const raceOf = (rid, r) => races.find((x) => x.race_id === rid) ?? races.find((x) => (x.markets ?? []).some((m) => m.id === r.market));
  const byRace = d3.rollups(entries, (list) => ({ pnl: d3.sum(list, ([, r]) => r.bankroll_pnl ?? 0), stake: d3.sum(list, ([, r]) => r.bankroll_stake ?? 0), vol: d3.sum(list, ([, r]) => r.eligible_volume ?? 0) }),
    ([rid, r]) => raceOf(rid, r)?.name ?? `${rid[0].toUpperCase()}${rid.slice(1).replace(/_/g, " ")}`);
  const top = byRace.filter(([, v]) => v.pnl).sort((x, y) => y[1].pnl - x[1].pnl).slice(0, 3);
  if (!top.length) return;
  const sgn = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`;
  const el = cell.querySelector(".mc-races");
  el.innerHTML = `${top.length === 1 ? "From one race" : `Top ${top.length} races`}: ${top.map(([name, v]) => `<span title="${esc(`$${Math.round(v.stake).toLocaleString("en-US")} staked; trades that qualified: $${Math.round(v.vol).toLocaleString("en-US")}`)}">${esc(name)} ${sgn(v.pnl)}</span>`).join(" · ")}`;
  el.hidden = false;
}

// The single market behind most of the pooled gain, named by its question from the election's meta.
async function concentration(inv, p) {
  const gain = p ? p.ending_value - p.bankroll : 0;
  const [id, b] = d3.greatest(Object.entries(inv.by_election), ([, x]) => x.largest_market_pnl ?? -Infinity) ?? [];
  if (!b?.largest_market || !(gain > 0) || !(b.largest_market_pnl > gain / 2)) return;
  const meta = await metaOf(id);
  const mk = meta?.races?.flatMap((r) => r.markets ?? []).find((m) => m.id === b.largest_market);
  const what = mk?.question ? `“${mk.question.replace(/\?$/, "")}”` : "one market";
  const el = $("money-conc");
  if (el) el.textContent = ` Most of the gain came from one market: ${money(b.largest_market_pnl)} of the ${money(gain)}, from ${what} in the ${labelOf(id)}.`;
}

// Ending value of $10,000 under each variant of the rule (invest10k.grid), and the stricter rule.
const GRID_COLS = [
  ["edge5c_delay60s", "10pct", "The rule above"],
  ["edge5c_delay60s", "first", "All of each trade, not 10%"],
  ["edge5c_delay60s", "10pct_cap", "At most $1,000 per market"],
  ["edge10c_delay60s", "10pct", "Price 10¢ below the model"],
  ["edge20c_delay60s", "10pct", "Price 20¢ below the model"],
  ["edge5c_delay300s", "10pct", "Wait 300 s, not 60 s"],
];
function gridTable(grid, list, sp) {
  const rows = list.filter((e) => grid?.[e.id]);
  if (!rows.length) return "";
  return `<summary>The same $10,000 under other versions of the rule</summary><div class="table-scroll"><table class="compact"><thead><tr><th>Election</th>${GRID_COLS.map(([, , l]) => `<th class="n">${l}</th>`).join("")}${sp ? `<th class="n">Stricter rule</th>` : ""}</tr></thead><tbody>${rows.map((e) => {
    const g = grid[e.id];
    const v = (k, mode) => (g[k]?.[mode] != null ? money(g[k][mode]) : "–");
    return `<tr><td>${esc(label(e))}</td>${GRID_COLS.map(([k, mode]) => `<td class="n">${v(k, mode)}</td>`).join("")}${sp ? `<td class="n">${sp.by_election?.[e.id] ? money(sp.by_election[e.id].ending_value) : "–"}</td>` : ""}</tr>`;
  }).join("")}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- elections list */

const WHEN = { scheduled: "Upcoming", live: "Live now", replay: "Replay" };
const todayUTC = () => Date.parse(new Date().toISOString().slice(0, 10));
function countdown(e) {
  if (e.status !== "scheduled") return WHEN[e.status] ?? e.status;
  const days = Math.round((Date.parse(e.date) - todayUTC()) / 864e5);
  return days > 1 ? `Upcoming, in ${days} days` : days === 1 ? "Upcoming, tomorrow" : days === 0 ? "Upcoming, today" : "Upcoming";
}

// What the test of each election rests on, in one sentence (EVIDENCE in common.js has the long form)
const SETTINGS = {
  forward: "Settings frozen before the vote.",
  "pre-specified": "Settings frozen before this data was examined; the result was already known.",
  "in-sample": "Settings were chosen on this data.",
  "not-preregistered": "Settings chosen on earlier elections, not frozen in advance.",
};
// one line (at most 140 characters), from the election's fields and its meta (the number of races listed)
function describe(e, m) {
  let base;
  if (e.status === "scheduled") base = `${m?.races?.length ?? "Several"} markets priced before the count.`;
  else if (e.status === "live") base = "Counting now; the model and the prices update live.";
  else if (e.count_private) base = "Count model run; its minute-by-minute data is private (licence).";
  else if (m?.count_timeline === false) base = "Market prices and final results only. No count over time.";
  else if (m && !hasMarkets(m)) return "Model against the raw count. No market prices for this night.";
  else base = "Model and market compared minute by minute.";
  return `${base} ${SETTINGS[e.evidence] ?? ""}`.trim();
}

const HELP = {
  date: "Upcoming: counting has not started, so the page shows the model and market before any votes. Replay: the night is over, and the page replays it minute by minute.",
  units: "The places the model counts one by one, for example US counties or Brazilian municipalities.",
  head: "Upcoming: the model's chance and the market prices for the main race, before any votes. Replay: how many minutes the model took to reach 90% on the eventual winner, against the market. Time starts when 1% of the vote is counted.",
  band: "The model gives a range for the final result: the 90% band. This is the share of minutes, from 1% to 95% counted, in which the final result was inside it. About 90% means the band was the right width.",
  chart: "The chance that the main race's first-named side wins. Through the night for a replay. For an upcoming election, one dot each for the model, Polymarket and Kalshi, where they are listed.",
};
const help = (k) => `<button type="button" class="help" aria-label="What is this column?" aria-expanded="false">?</button><span class="help-pop" role="note">${esc(HELP[k])}</span>`;

function render(elections) {
  const groups = d3.groups(elections, (e) => e.country ?? "");
  const latest = (list) => d3.max(list, (e) => e.date);
  groups.sort((a, b) => latest(b[1]).localeCompare(latest(a[1])));
  tbody.innerHTML = groups.map(([c, list]) => `<tr class="country"><td colspan="6">${esc(countryName(c))}</td></tr>${
    [...list].sort((a, b) => b.date.localeCompare(a.date)).map((e) => `<tr data-id="${esc(e.id)}" class="${esc(e.status)}">
        <td class="e-name" data-label="Election"><a href="${href(e.id)}" title="${esc(e.name)}">${esc(bareName(e.name))}</a><span class="e-desc">${esc(describe(e, null))}</span>${e.summary ? `<details class="e-more"><summary>Full summary</summary><p>${esc(e.summary)}</p>${EVIDENCE[e.evidence] ? `<p class="note">Test set-up: ${esc(EVIDENCE[e.evidence].long)}.</p>` : ""}</details>` : ""}</td>
        <td class="e-date" data-label="Date">${esc(fmtDate(e.date))}<span class="e-when ${esc(e.status)}">${esc(`${countdown(e)}${qualifierOf(e) ? ` · ${qualifierOf(e)}` : ""}`)}</span></td>
        <td class="n e-units" data-label="Units">${e.n_units != null ? `${fmtInt(e.n_units)} ` : ""}<span class="note">${esc(unitWord(e.unit_level, e.n_units))}</span></td>
        <td class="e-head" data-label="Model vs market"></td>
        <td class="n cal" data-label="In 90% band">–</td>
        <td class="sp" data-label="Main race"><svg class="spark" width="150" height="34" aria-hidden="true"></svg></td>
      </tr>`).join("")}`).join("");
  tbody.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", (ev) => { if (!ev.target.closest("a, details, button")) location.href = href(tr.dataset.id); });
    fillRow(tr).catch((err) => { console.error(err); fillHead(tr, `<p class="hl hl-none">Could not load this election's figures</p>`); });
  });
}

function fillHead(tr, html) { tr.querySelector(".e-head").innerHTML = html; }

async function fillRow(tr) {
  const id = tr.dataset.id;
  const e = allElections().find((x) => x.id === id);
  const m = await metaOf(id);
  tr.querySelector(".e-desc").textContent = describe(e, m);
  if (e.status === "scheduled" && m?.phase === "pre-count" && m.races?.length) return fillPre(tr, e, m);
  return fillPast(tr, e, m);
}

// What the model's lead over the market comes to, in a sentence.
function leadText(L) {
  const who = L.vs === "market" ? "the market" : "the raw count";
  if (L.minutes === 0) return `Model and ${L.vs} 90% sure in the same minute`;
  return `Model 90% sure ${dur(L.minutes)} ${L.minutes > 0 ? "before" : "after"} ${who}`;
}

// A replay (or a live count): the lead as a sentence, the share of minutes inside the 90% band, the night's lines.
async function fillPast(tr, e, m) {
  const id = e.id;
  const race = mainRace(m);
  const [L, stats, timeline, decision] = await Promise.all([leadOf(id), statsOf(id), timelineOf(id), decisionOf(id)]);
  const mainDecision = race ? decision?.by_race?.[race.race_id] : null;
  const nDecided = decision?.n_decided_before_count ?? 0, nMarkets = decision?.n_markets ?? 0;
  const decidedReason = (m?.notes ?? []).some((x) => /exit poll/i.test(x)) ? " (exit poll)" : "";
  const decidedNote = nDecided ? `${nDecided} of ${nMarkets} ${nMarkets === 1 ? "market was" : "markets were"} decided before the count${decidedReason}.` : "";
  const sub = race ? (L && L.scope !== "main" ? `Main race: ${L.scope}` : `Main race: ${race.name}`) : "";
  if (mainDecision?.decided) {
    const price = mainDecision.price_at_close;
    const settle = stats?.settle?.[race.race_id]?.polymarket;
    const days = settle && m?.poll_close_utc ? Math.round((Date.parse(m.poll_close_utc) - Date.parse(settle)) / 86400000) : 0;
    const detail = id === "mx2024" ? ` (market 0.96 two days before the vote)` : price != null ? ` (market ${price.toFixed(2)} at poll close${days >= 1 ? `; reached 90% ${days} day${days === 1 ? "" : "s"} earlier` : ""})` : "";
    fillHead(tr, `<p class="hl">Decided before the count${esc(detail)}</p><p class="note hl-sub">${esc(decidedNote || sub)}</p>`);
  } else if (L) fillHead(tr, `<p class="hl">${esc(leadText(L))}</p><p class="note hl-sub">${esc([sub, decidedNote].filter(Boolean).join(" · "))}</p>`);
  else if (e.count_private) {
    // The count model ran on licensed data; the aggregate omits pre-decided markets.
    const ld = stats?.lead_contested;
    if (ld?.n) {
      const g = ld.median_lead_min_gate1pct;
      fillHead(tr, `<p class="hl">Model first on ${ld.model_first_gate1pct} of ${ld.n} contested markets, median ${dur(g)}</p><p class="note hl-sub">Counted from 1% of the vote. ${esc(decidedNote)}</p>`);
    } else fillHead(tr, `<p class="hl hl-none">Count model run</p><p class="note hl-sub">Minute-by-minute data is private (licence). ${esc(decidedNote)}</p>`);
  } else if (m?.count_timeline === false) fillHead(tr, `<p class="hl hl-none">Prices and final results only</p><p class="note hl-sub">No count over time. ${esc(decidedNote)}</p>`);
  else if (m && !hasMarkets(m)) fillHead(tr, `<p class="hl hl-none">No market to compare with</p><p class="note hl-sub">${esc(sub)}; model only</p>`);
  else fillHead(tr, `<p class="hl hl-none">No contested-market lead to measure</p><p class="note hl-sub">${esc([sub, decidedNote].filter(Boolean).join(" · "))}</p>`);
  const cal = race ? stats?.calibration?.[race.race_id] : null;
  if (cal?.coverage90_1_95 != null) tr.querySelector(".cal").textContent = pct(cal.coverage90_1_95, 0);
  if (!race) return;
  const rows = (timeline?.[race.race_id] ?? []).map((d) => ({ ...d, tms: Date.parse(d.t) }));
  if (!rows.length) return;
  const gate = m.count_timeline === false ? null : rows.find((d) => d.counted >= 0.01);
  const vis = gate ? rows.filter((d) => d.tms >= gate.tms - 15 * 60000) : rows;
  const w = 150, h = 34;
  const x = d3.scaleLinear().domain(d3.extent(vis, (d) => d.tms)).range([1, w - 1]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - 2, 2]);
  const svg = d3.select(tr.querySelector(".sp")).select("svg").attr("viewBox", `0 0 ${w} ${h}`);
  svg.append("line").attr("x1", 0).attr("x2", w).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#e4e2dc");
  const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.tms)).y((d) => y(d[k])).curve(d3.curveStepAfter);
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.kalshi).attr("stroke-width", 1.1).attr("d", line("kalshi"));
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.2).attr("d", line("pm"));
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 1.5).attr("d", line("P"));
  tr.querySelector(".sp").title = `${race.name}: chance that ${race.a.name} wins, through the night`;
}

// An upcoming election: the headline market (who wins, most seats or control first, then by Polymarket
// volume) as the model's chance before any votes against Polymarket's price now (read in the browser)
// and the last recorded Kalshi price.
const HEADLINE = /^most-|winner|control|^pres|prime-minister|chamber|^win-/;
async function fillPre(tr, e, m) {
  const id = e.id;
  const timeline = await timelineOf(id);
  const lastRow = (rid) => { const rows = (timeline?.[rid] ?? []).filter((d) => d.t); return rows[rows.length - 1] ?? null; };
  const pm = (r) => (r.markets ?? []).find((x) => x.platform === "polymarket" && x.id);
  const volume = new Map();
  const yes = await polymarketYes(m.races.map((r) => pm(r)?.id), volume).catch(() => new Map());
  const live = new Map(m.races.map((r) => {
    const y = pm(r) ? yes.get(pm(r).id) : null;
    return [r.race_id, y == null ? null : pm(r).yes_is === "b" ? 1 - y : y];
  }));
  const regions = new Map((m.regions ?? []).map((r) => [r.id, r.name]));
  const nameOf = (r) => {
    let s = shortName(r, regions.get(r.region));
    if (r.region && !s.includes(regions.get(r.region) ?? "")) s = `${regions.get(r.region)} ${s}`;
    return r.a?.name && !s.includes(r.a.name) ? `${s}, ${r.a.name}` : s;
  };
  const order = m.races.map((r, i) => ({ r, i, head: HEADLINE.test(r.race_id) ? 0 : 1, vol: volume.get(pm(r)?.id) ?? -1 }))
    .sort((a, b) => a.head - b.head || b.vol - a.vol || a.i - b.i).map((x) => x.r);
  const r = order[0];
  const last = lastRow(r.race_id);
  const v = r.poll_model ?? "v2";
  const vals = {
    model: last?.[`P_${v}`] ?? modelP(last) ?? r.precount?.[`P_${v}`] ?? r.precount?.P ?? null,
    polymarket: live.get(r.race_id) ?? last?.pm ?? null,
    kalshi: last?.kalshi ?? null,
  };
  const f = (x) => (x == null ? "–" : pct(x, 0));
  const n = m.races.length;
  fillHead(tr, `<p class="hl hl-pre">${esc(nameOf(r))}</p>
    <p class="pre-vals"><span class="m">Model ${f(vals.model)}</span> · <span class="k">Polymarket ${f(vals.polymarket)}</span>${vals.kalshi != null ? ` · <span class="kx">Kalshi ${f(vals.kalshi)}</span>` : ""}</p>
    <p class="note hl-sub"><a class="all-mk" href="${href(e.id)}">See all ${n} ${n === 1 ? "market" : "markets"}</a></p>`);
  tr.querySelector(".cal").title = "Not yet: the count has not started";
  // the chart cell: one dot each on a 0-100% line
  const w = 150, h = 34, x = d3.scaleLinear().domain([0, 1]).range([6, w - 6]);
  const svg = d3.select(tr.querySelector(".sp")).select("svg").attr("viewBox", `0 0 ${w} ${h}`);
  svg.append("line").attr("x1", x(0)).attr("x2", x(1)).attr("y1", 15).attr("y2", 15).attr("stroke", "#d8d5cd");
  svg.append("line").attr("x1", x(0.5)).attr("x2", x(0.5)).attr("y1", 10).attr("y2", 20).attr("stroke", "#d8d5cd");
  svg.append("text").attr("class", "legend-text").attr("x", x(0)).attr("y", 32).text("0%");
  svg.append("text").attr("class", "legend-text").attr("x", x(1)).attr("y", 32).attr("text-anchor", "end").text("100%");
  for (const [k, color] of [["kalshi", C.kalshi], ["polymarket", C.market], ["model", C.model]]) {
    if (vals[k] != null) svg.append("circle").attr("cx", x(vals[k])).attr("cy", 15).attr("r", 4.5).attr("fill", color).attr("stroke", "#fff").attr("stroke-width", 1.2).append("title").text(`${k === "model" ? "Model" : k === "polymarket" ? "Polymarket" : "Kalshi"} ${pct(vals[k], 0)}`);
  }
  tr.querySelector(".sp").title = `${nameOf(r)}: chance before the count`;
}

// column help: a "?" opens a short explanation (hover and keyboard focus also show it)
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".help");
  document.querySelectorAll(".help[aria-expanded=true]").forEach((b) => { if (b !== btn) { b.setAttribute("aria-expanded", "false"); b.parentElement.classList.remove("open"); } });
  if (!btn) return;
  const open = btn.getAttribute("aria-expanded") !== "true";
  btn.setAttribute("aria-expanded", String(open));
  btn.parentElement.classList.toggle("open", open);
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") document.querySelectorAll(".help[aria-expanded=true]").forEach((b) => { b.setAttribute("aria-expanded", "false"); b.parentElement.classList.remove("open"); }); });

for (const th of document.querySelectorAll("#overview th[data-help]")) th.insertAdjacentHTML("beforeend", help(th.dataset.help));

async function load() {
  try {
    const [elections, summary] = await Promise.all([getJSON("elections.json"), getJSON("summary.json", { optional: true })]);
    setLiveIds(elections.filter((e) => e.status === "live" || e.status === "scheduled").map((e) => e.id));
    setElections(elections);
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
    if (!load.charts) {
      load.charts = true;
      drawNight().catch((err) => console.error(err));
      drawLead().catch((err) => console.error(err));
      drawMix().catch((err) => console.error(err));
    }
    if (summary && !load.drawn) {
      load.drawn = true;
      // chart labels carry the year: two elections can share a name (Brazil's 2022 and 2026 runoffs)
      summary.elections = (summary.elections ?? []).map((e) => ({ ...e, name: labelOf(e.id) === e.id ? e.name : labelOf(e.id) }));
      drawAll(summary);
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="note">Could not load the list of elections (${esc(err.message)}).</td></tr>`;
  }
}

if (LIVE) {
  const note = $("live-note");
  note.hidden = false;
  note.textContent = `Live data from ${LIVE}, refreshed every 15 seconds.`;
  setInterval(load, 15000);
}
load();
