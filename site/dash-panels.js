// Right-hand panels of the dashboard: the readout at the current time, and the three tabs
// (races, statistics, method & sources). Pure rendering from values the controller passes in.
import * as d3 from "./vendor/d3.js";
import { C, esc, pct, num, usd, votesShort, modelP, sensP, marginText, thinPolls, thinLabel, clock, tzName, evidenceLabel, EVIDENCE, leadMinutes, probabilityLead, splitChanceHTML, sideLabel } from "./common.js";

const PLATFORM = { polymarket: "Polymarket", kalshi: "Kalshi" };
const signed = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);

/* ---------------------------------------------------------------- readout */

// One compact strip: a heading line, then chips (counted, the model's margin or projection, polls,
// the market-implied margin, chances), a one-line comparison and small notes.
// pre: before the count (meta.phase "pre-count"): { day } for the date in the heading, { note } when
// the model has no pre-count figure for the race, { live } "Polymarket live · 12 s ago" for the
// latest row, { basis } the estimate's own note. ev: { short, full } early vote for the race's state.
// turnout: { text, title } the race's projected turnout against expected.
export function renderReadout(el, { race, row, t, preview, tz, countTimeline, frameT, pre = null, ev = null, turnout = null, nPolls = null, settled = false, winner = null, sensitivities = [], show = {} }) {
  // show: which layers are on (election.js showOf); a missing field counts as on
  const S = (k) => show[k] !== false, SENS = (k) => show.sens?.[k] !== false;
  const mExists = show.marketExists !== false;
  const won = winner ? ` — ${esc(winner)} won` : "";
  const head = `<p class="ro-head"><span class="ro-at">${preview ? "Preview at" : "At"} ${pre?.day ? `${esc(pre.day)} ` : ""}<b>${clock(t, tz)}</b> ${esc(tzName(tz, t))}</span><span class="ro-race">${esc(race.name)}${won}</span></p>${race.source ? `<p class="note ro-src">Results: <a href="${esc(race.source.url)}">${esc(race.source.name)}</a> (secondary source)</p>` : ""}`;
  if (!row) { el.innerHTML = `${head}<p class="note">No data for this race at this time.</p>`; return; }
  const chip = (label, value, cls = "", sub = "", title = "") => `<span class="chip ${cls}"${title ? ` title="${esc(title)}"` : ""}><small>${label}</small><b>${value}</b>${sub ? `<i>${sub}</i>` : ""}</span>`;
  const chips = [];
  const sideName = (side, compact = false) => `${sideLabel(side, compact)}${side?.id === "dem" ? " (D)" : side?.id === "rep" ? " (R)" : ""}`;
  const leadShare = (share, compact = false) => {
    const lead = probabilityLead(share, race.a, race.b);
    return lead ? `<span style="color:${esc(lead.side.color)}">${esc(sideName(lead.side, compact))} ${pct(lead.chance)}</span>` : "–";
  };
  const leadMargin = (v, compact = false) => {
    if (v == null) return "–";
    if (Math.abs(v) < 0.05) return "even";
    const side = v > 0 ? race.a : race.b;
    return `<span style="color:${esc(side.color)}">${esc(sideName(side, compact))} +${Math.abs(v).toFixed(1)}</span>`;
  };
  if (countTimeline && !pre) {
    chips.push(chip("Counted", pct(row.counted, 0)));
    if (S("raw")) chips.push(chip("Raw count", leadShare(row.raw), "r"));
  }
  // US rows carry margins for side a minus side b, in points; other races show two-candidate share.
  const mm = row.margin_v2 ?? row.margin_model ?? null;
  const margins = mm != null || row.margin_market != null || row.margin_polls != null;
  const mmSd = row.margin_v2 != null && row.sd_v2 != null ? 200 * row.sd_v2 : row.margin_model_sd;
  const mkP = S("pm") ? row.margin_market : null, mkK = S("kalshi") ? row.margin_market_kalshi : null;
  const mkt = mkP ?? mkK ?? null, mktP = mkP != null ? row.pm : row.kalshi;
  const who = "Model", whoTip = row.estimate ? "Estimate: 2024 county results shifted to the poll average" : "v3: poll average prior + county count (registered)";
  if (margins) {
    if (S("model")) chips.push(chip(`${who}${row.margin_v2 != null ? ", polls" : ""}`, leadMargin(mm), "m", mm != null && mmSd != null ? `± ${mmSd.toFixed(1)}` : "", `${whoTip}. Two-candidate margin; ± one standard deviation`));
    if (S("polls")) chips.push(chip("Polls", leadMargin(row.margin_polls)));
    if (mExists && (S("pm") || S("kalshi"))) chips.push(chip("Market-implied", leadMargin(mkt), "k", mkt != null ? `from ${num(mktP)}` : "", `the margin at which the model's uncertainty gives the ${mkP != null ? "Polymarket" : "Kalshi"} price${mkK != null && mkP != null ? `; Kalshi-implied ${marginText(mkK)}` : ""}`));
  } else if (S("model")) {
    const v2 = row.share_v2 != null, share = v2 ? row.share_v2 : row.share, sd = v2 ? row.sd_v2 : row.sd;
    const label = pre ? "Model, before votes" : "Model projection";
    if (share != null || pre?.note) chips.push(chip(`${label}${v2 ? ", polls" : ""}`, share != null ? leadShare(share) : "–", "m", share != null && sd != null ? `± ${(164.5 * sd).toFixed(1)}` : "", share != null ? "Leader's projected share of the two-candidate vote; ± the 90% band" : pre.note));
  }
  // Each source gets a two-colour bar; the stored value remains P(side a) in data-p.
  const P = S("model") ? modelP(row) : null, two = row.P_v2 != null && row.P != null;
  const sens = sensitivities.filter((s) => SENS(s.key)).map((s) => [sensP(row, s), s.key]);
  const pmV = S("pm") ? row.pm : null, kxV = S("kalshi") ? row.kalshi : null;
  const chances = [[P, "m", "model"], ...sens.map(([v, k]) => [v, "m", k]), [pmV, "k", "Polymarket"], [kxV, "kx", "Kalshi"]].filter(([v]) => v != null);
  const lead = probabilityLead(P ?? pmV ?? kxV ?? sens.find(([v]) => v != null)?.[0], race.a, race.b);
  let leadCall = "";
  if (settled) leadCall = `<p class="lead-call">${winner ? `${esc(winner)} won` : "Settled"}</p>`;
  else if (lead) {
    const figures = chances.map(([v, , label]) => `<span>${esc(label === "model" ? "Model" : label)} ${pct(lead.aLeads ? v : 1 - v, 0)}</span>`).join("");
    leadCall = `<p class="lead-call"><span class="lead-side" style="--side:${esc(lead.side.color)}">${esc(sideName(lead.side))}</span> leads<span class="lead-sources">${figures}</span></p>`;
  }
  if (chances.length) {
    const bars = chances.map(([v, cls, label]) => `<span class="${cls} ro-src-bar" data-source="${esc(label)}" data-p="${v}"><em>${esc(label === "model" ? "Model" : label)}</em>${splitChanceHTML(v, race.a, race.b, { compact: true, source: label === "model" ? "Model" : label })}</span>`).join("");
    chips.push(chip("Chance of winning", bars, "ro-ch", thinPolls(nPolls) != null ? `<span class="thin-poll">${thinLabel(nPolls)}</span>` : ""));
  }
  if (row.rem_total > 0 && S("model")) {
    const lean = row.rem_margin_total;
    chips.push(chip("Votes left (est.)", `≈ ${votesShort(row.rem_total)}`, "", lean != null ? `lean ${esc(lean >= 0 ? race.a.name : race.b.name)} +${(100 * Math.abs(lean) / row.rem_total).toFixed(1)}` : "", "the model's estimate of the votes still to count and how they lean"));
  }
  if (turnout && S("turnout")) chips.push(chip("Turnout", esc(turnout.text), "", "", turnout.title));
  if (ev && S("early")) chips.push(chip("Early vote", esc(ev.short), "", "", ev.full));
  const market = pmV ?? kxV, marketName = pmV != null ? "Polymarket" : "Kalshi";
  let gap = "";
  if (margins && S("model") && mm != null && mkt != null) {
    const g = mkt - mm;
    gap = `${who} ${leadMargin(mm)}, market-implied ${leadMargin(mkt)}: ${Math.abs(g) < 0.05 ? "the same margin" : `<b>${Math.abs(g).toFixed(1)} pt</b> apart`}.`;
  } else if (P != null && market != null) {
    const fav = probabilityLead(P, race.a, race.b), marketOnFav = fav.aLeads ? market : 1 - market, g = marketOnFav - fav.chance;
    gap = `${marketName} vs model on ${esc(fav.side.name)}: <b>${Math.abs(100 * g).toFixed(0)} point${Math.abs(100 * g) >= 1.5 ? "s" : ""}</b> ${Math.abs(g) < 0.005 ? "apart" : g > 0 ? "more confident" : "less confident"}.`;
  } else if (P != null && mExists && (S("pm") || S("kalshi"))) gap = "No market price at this time.";
  const notes = [frameT != null && countTimeline && !pre ? `Map: ${clock(frameT, tz)} frame` : "", pre && row.live && pre.live ? esc(pre.live) : "", pre?.basis ? esc(pre.basis) : ""].filter(Boolean).join(" · ");
  el.innerHTML = `${head}${leadCall}<div class="chips">${chips.join("")}</div>${gap || notes ? `<p class="ro-gap">${gap}${notes ? ` <span class="ro-frame">${notes}</span>` : ""}</p>` : ""}`;
}

/* ---------------------------------------------------------------- races tab */

// leadOf(race) -> minutes the model reached 90% on the winner before Polymarket, both measured from
// the race's 1% gate (see gatedSettle in common.js); null when not computable.
const SORT = { key: "order", dir: 1 }; // kept across rebuilds (live refresh)

// show: which layers are on ({ model, pm, kalshi, sens: {key: bool} }, election.js showOf; a missing
// field counts as on). A column of a layer that is off (or that the election does not have, such as a
// market in an election with none) is not in the table. Gap needs the model and a market; Lead and
// Profit need the model and Polymarket. profitOf(race): the race's entry in
// stats.profit_model_limit.by_race, or null; the Profit column shows when any race has one.
const profitText = (p) => (p == null ? "–" : `${p.bankroll_pnl > 0 ? "+" : p.bankroll_pnl < 0 ? "−" : ""}$${Math.abs(Math.round(p.bankroll_pnl ?? 0)).toLocaleString("en-US")}`);
const profitTitle = (p) => (p == null ? "" : p.eligible_trades ? `${p.eligible_trades.toLocaleString("en-US")} trades at least 5¢ below the model, $${Math.round(p.eligible_volume).toLocaleString("en-US")} in all; the $10,000 put $${Math.round(p.bankroll_stake ?? 0).toLocaleString("en-US")} into this race` : p.reason ? `No trade qualified: ${p.reason}` : "No trade qualified");
export function createRaceList(el, { races, raceName, leadOf, onSelect, sensitivities = [], profitOf = () => null, nPollsOf = () => null, decisionOf = () => null, winnerOf = () => null, show = {} }) {
  const st = { values: new Map() };
  const lead = (r) => leadOf(r);
  const S = (k) => show[k] !== false;
  const sens = sensitivities.filter((s) => show.sens?.[s.key] !== false);
  const hasPm = S("pm"), hasKx = S("kalshi"), hasModel = S("model");
  const hasGap = hasModel && (hasPm || hasKx), hasLead = hasModel && hasPm;
  const hasProfit = hasModel && hasPm && races.some((r) => profitOf(r));
  const anyThin = races.some((r) => thinPolls(nPollsOf(r)) != null);
  const cols = [
    ["name", "Race", false],
    ...(hasModel ? [["P", "Model", true]] : []),
    ...sens.map((s) => [`sens_${s.key}`, esc(s.key), true]),
    ...(hasPm ? [["pm", "Polymarket", true]] : []),
    ...(hasKx ? [["k", "Kalshi", true]] : []),
    ...(hasGap ? [["gap", "Market vs model", true]] : []),
    ...(hasLead ? [["lead", "Lead from 1% counted (min)", true]] : []),
    ...(hasProfit ? [["profit", "Profit on $10,000", true]] : []),
  ];
  const at = (k) => cols.findIndex((c) => c[0] === k);
  if (SORT.key !== "order" && !cols.some((c) => c[0] === SORT.key)) { SORT.key = "order"; SORT.dir = 1; } // the sorted column is not shown
  const note = [
    anyThin ? "† 3 or fewer polls in the poll average.<br>" : "",
    hasModel ? "Each probability cell names the leading side and shows both sides in its split bar." : "",
    ...sens.map((s) => ` ${esc(s.key)}: ${esc(s.text)}.`),
    hasPm || hasKx ? ` ${hasPm && hasKx ? "Polymarket and Kalshi: each venue's price" : hasPm ? "Polymarket: the price" : "Kalshi: the price"} for the same outcome (–: not listed there).` : "",
    hasGap ? ` Market vs model: the market's confidence in the model leader, minus the model's, in percentage points.` : "",
    hasLead ? " Lead: minutes by which the model reached 90% on the winner before Polymarket did, both counted from the moment the race reached 1% counted (negative: after). A “decided before count” market was already at least 90% on the winner at poll close and stayed at least 80%; its lead is not meaningful." : "",
    hasProfit ? " Profit on $10,000: what the election's $10,000 made in this race under the buying rule in the Statistics tab (hover for the trades that qualified)." : "",
  ].join("");
  el.innerHTML = `<table class="races"><thead><tr>${cols.map(([k, label, n]) => `<th class="${n ? "n" : ""}" aria-sort="${SORT.key === k ? (SORT.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${label}</button></th>`).join("")}</tr></thead><tbody></tbody></table>
    ${note ? `<p class="note">${note}</p>` : ""}`;
  const tbody = el.querySelector("tbody");
  const rows = races.map((r, order) => {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.race = r.race_id;
    const pr = hasProfit ? profitOf(r) : null, decision = hasPm || hasKx ? decisionOf(r) : null, winner = winnerOf(r);
    tr.innerHTML = cols.map(([k]) => {
      if (k === "name") return `<td>${esc(raceName(r))}${winner ? ` ${esc(winner)}` : ""}${decision?.decided ? ` <span class="label decided">decided before count</span>` : ""}${thinPolls(nPollsOf(r)) != null ? ` <sup class="thin-mark" title="${esc(thinLabel(nPollsOf(r)))}">†</sup>` : ""}</td>`;
      if (k === "P" || k.startsWith("sens_")) return `<td class="n m"></td>`;
      if (k === "k") return `<td class="n kx"></td>`;
      if (k === "gap") return `<td class="n chance-gap"></td>`;
      if (k === "lead") return `<td class="n">${decision?.decided ? "not meaningful" : lead(r) == null ? "–" : signed(lead(r), 0)}</td>`;
      if (k === "profit") return `<td class="n" title="${esc(profitTitle(pr))}">${profitText(pr)}</td>`;
      return `<td class="n"></td>`;
    }).join("");
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
  const market = (v) => (hasPm ? v.pm : null) ?? (hasKx ? v.k : null);
  function val(x) {
    const v = st.values.get(x.r.race_id) ?? {};
    const m = market(v);
    return { order: x.order, name: x.r.name, P: v.P, pm: v.pm, k: v.k, ...Object.fromEntries(sens.map((s) => [`sens_${s.key}`, v.sens?.[s.key]])),
      gap: v.P != null && m != null ? Math.abs(v.P - m) : null, lead: lead(x.r),
      profit: profitOf(x.r)?.bankroll_pnl }[SORT.key];
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
  const [iP, iPm, iK, iGap] = ["P", "pm", "k", "gap"].map(at);
  const iSens = sens.map((s) => at(`sens_${s.key}`));
  const setChance = (cell, p, race, source) => {
    cell.innerHTML = splitChanceHTML(p, race.a, race.b, { compact: true, source });
  };
  return {
    update(valuesByRace) {
      st.values = valuesByRace;
      for (const x of rows) {
        const v = valuesByRace.get(x.r.race_id) ?? {};
        const c = x.cells;
        if (iP >= 0) setChance(c[iP], v.P, x.r, "Model");
        sens.forEach((s, i) => setChance(c[iSens[i]], v.sens?.[s.key], x.r, s.key));
        if (iPm >= 0) setChance(c[iPm], v.pm, x.r, "Polymarket");
        if (iK >= 0) setChance(c[iK], v.k, x.r, "Kalshi");
        if (iGap >= 0) {
          const gapCell = c[iGap], m = market(v), fav = probabilityLead(v.P, x.r.a, x.r.b);
          if (fav && m != null) {
            const marketOnFav = fav.aLeads ? m : 1 - m, points = 100 * (marketOnFav - fav.chance);
            gapCell.innerHTML = `<span title="${esc(`${marketOnFav >= fav.chance ? "Market" : "Model"} is more confident in ${fav.side.name}`)}">${Math.abs(points) < 0.5 ? "0" : `${points > 0 ? "+" : "−"}${Math.abs(points).toFixed(0)}`}<small>pt</small></span>`;
          } else gapCell.textContent = "–";
        }
      }
      if (!["order", "name", "lead", "profit"].includes(SORT.key)) sort();
    },
    select(raceId) {
      for (const x of rows) x.tr.classList.toggle("sel", x.r.race_id === raceId);
    },
  };
}

/* ---------------------------------------------------------------- statistics tab */

// hasMarket: false where no race of the election has a market price: the tab then has no market
// line, section or column (and no profit audit, which is a market strategy).
export function renderStats(el, { stats, race, races, raceName, tz, arb, settleOf, profitOf = () => null, hasMarket = true }) {
  if (!stats) { el.innerHTML = `<p class="note">No statistics for this election yet.</p>`; return; }
  const ev = (k) => (hasMarket && stats.evidence?.[k] ? evidenceLabel(stats.evidence[k]) : "");
  const reasons = stats.reasons ?? stats.null_reasons ?? {};
  const why = (k) => !hasMarket && /market/i.test(reasons[k] ?? "") ? "not available for this election" : reasons[k] ?? "not available";
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
      ? `model ${t(set.model)}${hasMarket ? ` · Polymarket ${set.polymarket ? t(set.polymarket) : "–"}${set.kalshi !== undefined ? ` · Kalshi ${set.kalshi ? t(set.kalshi) : "–"}` : ""}` : ""} · raw count ${t(set.raw)}${hasMarket && leadMinutes(settleOf(id)) != null ? ` · from 1% counted, the model was ${Math.abs(leadMinutes(settleOf(id)))} min ${leadMinutes(settleOf(id)) >= 0 ? "earlier" : "later"} than Polymarket` : ""}`
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
    <table class="compact"><thead><tr><th>Race</th><th class="n">In band</th><th class="n">Mean error</th>${hasMarket ? `<th class="n">Lead from 1% (min)</th>` : ""}</tr></thead><tbody>${others.map((r) => {
      const c = stats.calibration?.[r.race_id];
      const l = leadMinutes(settleOf(r.race_id));
      return `<tr><td>${esc(raceName(r))}</td><td class="n">${c ? pct(c.coverage90_1_95, 0) : "–"}</td><td class="n">${c ? signed(c.mean_z, 2) : "–"}</td>${hasMarket ? `<td class="n">${l == null ? "–" : signed(l, 0)}</td>` : ""}</tr>`;
    }).join("")}</tbody></table></details>` : "";

  const extras = Object.entries(stats).filter(([k, v]) => !KNOWN.has(k) && v != null && (hasMarket || !MARKET_STATS.has(k)));
  const extraHTML = extras.length ? `<h3 class="tab-h">Further measures for this election</h3><dl class="st">${extras.map(([k, v]) =>
    `<div class="st-row"><dt>${esc(k.replace(/_/g, " "))} ${ev(k)}</dt><dd>${flat(v)}</dd></div>`).join("")}</dl>` : "";

  el.innerHTML = `${stats.evidence_note && (hasMarket || !/market/i.test(stats.evidence_note)) ? `<p class="st-evnote">${esc(stats.evidence_note)}</p>` : ""}
    ${auditHTML(stats, race, raceName, races, raceProfitHTML(profitOf(race), race, raceName, tz), hasMarket)}
    <h3 class="tab-h">${esc(raceName(race))}</h3><dl class="st">${raceRows.join("")}</dl>
    ${hasMarket ? `<h3 class="tab-h">All markets in this election</h3><dl class="st">${allRows.join("")}</dl>
    ${arb?.length ? arbHTML(arb, race, raceName, races, tz) : ""}` : ""}
    ${extraHTML}
    ${othersTable}
    <p class="note">The same measures are computed for every election; the <a href="./#measures">overview page</a> defines them.</p>`;
}

const KNOWN = new Set(["calibration", "settle", "settle_gated", "catchup", "early_error", "capacity", "arbitrage", "evidence", "evidence_note", "reasons", "null_reasons", "accuracy", "profit", "invest10k", "profit_model_limit"]);
// the further measures that are about markets: left out of an election with none
const MARKET_STATS = new Set(["n_markets", "n_decided_before_count", "market_classification", "lead_contested"]);

/* ---------------------------------------------------------------- edge audit (stats.accuracy, stats.profit) */

const ci = (c, f) => (Array.isArray(c) && c[0] != null ? ` (95%: ${f(c[0])} to ${f(c[1])})` : "");
const money = (x) => (x == null ? "–" : `${x < 0 ? "−" : x > 0 ? "+" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`);
const ret = (x) => (x == null ? "–" : `${x < 0 ? "−" : "+"}${Math.abs(100 * x).toFixed(0)}%`);
const races_ = (n) => `${n} race${n === 1 ? "" : "s"}`;

function auditHTML(stats, race, raceName, races, raceBlock, hasMarket = true) {
  return `<section class="audit">${accuracyHTML(stats.accuracy, race, raceName, hasMarket)}${hasMarket ? profitHTML(stats.invest10k, stats.profit_model_limit, stats.profit, races, raceBlock) : ""}</section>`;
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

function accuracyHTML(acc, race, raceName, hasMarket = true) {
  const head = (ans) => `<h3 class="tab-h">Did the projection hold? ${hasMarket && acc?.evidence ? evidenceLabel(acc.evidence) : ""}</h3>${ans ? `<p class="answer">${ans}</p>` : ""}`;
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
      : `model ${num(pw.brier_model, 3)}${hasMarket ? " (no market prices to compare)" : ""}.`;
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
const overconfident = (b) => (b?.coverage90_headline?.coverage90 != null && b.coverage90_headline.coverage90 < 0.7 && b.return_pct > 0 ? `This model was overconfident: its 90% range held the final result only ${Math.round(100 * b.coverage90_headline.coverage90)}% of the time, so part of this gain is luck.` : "");
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
  if (inv.largest_market_share != null && inv.largest_market_share >= 0.5 && inv.markets > 1) facts.push(`${inv.largest_market_share > 1 ? "More than the whole gain" : `${shareOfGain(inv.largest_market_share)} of the gain`} came from one market${question(inv.largest_market) ? `: “${esc(question(inv.largest_market).replace(/\?$/, ""))}”` : ""}${inv.largest_market_share > 1 ? "; the other markets lost money" : ""}.`);
  if (overconfident(inv)) facts.push(overconfident(inv));
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

// hasMarket: false where no race of the election has a market price: the notes' Polymarket / Kalshi
// lines (about where markets were looked for) become one plain sentence, and the market sources go.
export function renderMethod(el, { meta, entry, geometryCredit, twoModels = false, margins = false, sensitivities = [], hasMarket = true }) {
  const noteList = hasMarket ? (meta.notes ?? []) : (meta.notes ?? []).filter((n) => !/market|polymarket|kalshi/i.test(n));
  const notes = noteList.map((n) => `<li>${esc(n)}</li>`).join("");
  const srcList = hasMarket ? (meta.sources ?? []) : (meta.sources ?? []).filter((s) => !/market|polymarket|kalshi/i.test(s.name));
  const src = srcList.map((s) => `<li><a href="${esc(s.url)}">${esc(s.name)}</a>, ${esc(s.licence)}</li>`).join("");
  const evidence = Object.entries(EVIDENCE).filter(([k]) => hasMarket || !["model-only", "market-only"].includes(k));
  const evs = evidence.map(([k, e]) => `<li>${evidenceLabel(k)} ${esc(e.long[0].toUpperCase() + e.long.slice(1))}.</li>`).join("");
  el.innerHTML = `
    <h3 class="tab-h">How this election was modelled</h3>
    ${sensitivities.length ? `<p>The solid line is the headline model. Thin dashed lines are sensitivities, not the headline: ${sensitivities.map((s) => `<b>${esc(s.key)}</b> = <span class="m">${esc(s.text)}</span>`).join("; ")}.</p>` : twoModels ? `<p>Two versions of the model are shown. The <span class="m">model with polls</span> (the main line and figure) starts from past certified results shifted toward the recent polling average for the race, then updates as votes are counted. The <span class="m">model without polls</span> (thin dashed line) starts from past certified results alone and updates from the count in the same way. Both use only what was known at each minute. As more votes are counted both rely more on the count, so the two come closer; the difference matters most before and early in the count.</p>` : ""}
    ${margins && hasMarket ? `<p><b>Market-implied margin</b>: the vote margin at which the model's own uncertainty would give the market's price. A price p becomes the margin Φ⁻¹(p) × the model's standard deviation of the margin (Φ⁻¹: the inverse normal distribution), so 0.50 is an even race and 0.84 is a lead of one standard deviation.</p>` : ""}
    ${notes ? `<ul>${notes}</ul>` : `<p class="note">No notes.</p>`}
    <h3 class="tab-h">Evidence labels</h3>
    <p>This election: ${entry?.evidence ? hasMarket || !["model-only", "market-only"].includes(entry.evidence) ? evidenceLabel(entry.evidence) : "model replay" : "–"}</p>
    <ul class="evs">${evs}</ul>
    <h3 class="tab-h">Sources</h3>
    ${hasMarket && (meta.sources ?? []).some((s) => /^Kalshi/.test(s.name)) ? `<p>Market prices: Polymarket and, where Kalshi lists the same race, Kalshi. Kalshi data: Kalshi Inc.</p>` : ""}
    <ul>${src}${geometryCredit ? `<li>${geometryCredit}</li>` : ""}</ul>
    <p class="note"><a href="./">All elections and definitions</a> · <a href="brazil-2026.html">Brazil 2026 write-up</a> · Code under MIT licence.</p>`;
}
