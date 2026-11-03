// "Has the early vote tracked the result? 2016-2024, every state": reads data/ev2026/ev_history_all.json (analysis/us_ev_history_all.py).
// Every number shown comes from that file; this module only formats and draws. It is independent of earlyvote.js except for two events that
// keep the state choice in step with the page: "ev:state" (the page selected a state) and "ev:pick" (this section selected one). Neither scrolls.
import * as d3 from "./vendor/d3.js";
import { getJSON, esc } from "./common.js";

const $ = (s) => document.querySelector(s);
const CYC = { 2016: "#b5562f", 2018: "#8a6fb0", 2020: "#2f8a6c", 2022: "#c49a1b", 2024: "#6a7f99" };
const MINUS = "\u2212";
const f1 = (v) => `${Math.abs(v).toFixed(1)}`;
const pt = (v) => (v == null ? "–" : `${v > 0.049 ? "+" : v < -0.049 ? MINUS : ""}${f1(v)} pt`);
const pc = (v, d = 1) => (v == null ? "–" : `${v.toFixed(d)}%`);
const sg = (v, d = 2) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? MINUS : ""}${Math.abs(v).toFixed(d)}`);
const int = d3.format(",");
const frac = (k, n) => (n ? `${k}/${n}` : "–");
const pc0 = (k, n) => (n ? `${Math.round((100 * k) / n)}%` : "–");
const ci = (w) => (w ? `${Math.round(100 * w[0])}–${Math.round(100 * w[1])}%` : "–");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}` : "–");

const S = { H: null, st: null, filter: "party", sort: { key: "name", dir: 1 }, change: { measure: "party", dm: true } };

// ----------------------------------------------------------------------------------------------------------------- findings
function strip() {
  const P = S.H.pooled, g = P.gap, cs = S.H.cycles.filter((c) => c.n);
  const nst = new Set(S.H.rows.filter((r) => r.gap != null).map((r) => r.st)).size;
  const items = [
    ["State-cycles compared", int(g.n), `${nst} states, ${cs.length} cycles (${cs.map((c) => c.cycle).join(", ")})`],
    ["Mean gap", pt(g.mean_gap), "registered-D share of early ballots minus the D result"],
    ["Mean absolute gap", pt(g.mean_abs_gap).replace(/^\+/, ""), `spread (sd) ${g.sd_gap != null ? f1(g.sd_gap) : "–"} pt`],
    ["Same side of 50 as the result", `${g.right} of ${g.right_n}`, pc0(g.right, g.right_n)],
    ["Cycle means", cs.map((c) => `${c.cycle} ${sg(c.mean_gap, 1)}`).join(" · "), "points, early vote minus result"],
  ];
  $("#eh-strip").innerHTML = items.map(([k, n, a]) => `<div class="eh-s"><p class="kf-k">${esc(k)}</p><p class="eh-n">${esc(n)}</p><p class="kf-a">${esc(a)}</p></div>`).join("");
}
const card = (k, n, m, a) => `<div class="kf-card"><p class="kf-k">${esc(k)}</p><p class="kf-n">${n}</p><p class="kf-m">${m}</p><p class="kf-a">${a}</p></div>`;
function findings() {
  const P = S.H.pooled, g = P.gap, cs = S.H.cycles.filter((c) => c.n);
  const lo = cs.reduce((a, c) => (c.mean_gap < a.mean_gap ? c : a)), hi = cs.reduce((a, c) => (c.mean_gap > a.mean_gap ? c : a));
  const ps = P.party_change_same, pa = P.party_change_any, vs = P.volume_same, va = P.volume_any, ag = P.age;
  const cards = [
    card("Who was ahead", `${g.right} of ${g.right_n}`, `In ${pc0(g.right, g.right_n)} of the state-cycles, registered Democrats' share of the early ballots was on the same side of 50 as the result. Most are lopsided states. The wrong side: ${esc(P.winner_wrong.join(", "))}.`, "The early vote said who led far more often than by how much."),
    card("By how much", `${f1(g.mean_abs_gap)} pt`, `The registered-D share was ${f1(g.mean_gap)} points ${g.mean_gap >= 0 ? "more" : "less"} Democratic than the D two-party result on average, and missed by ${f1(g.mean_abs_gap)} points in either direction (from ${sg(g.min_gap, 1)} to ${sg(g.max_gap, 1)}).`, "Gap = registered-D share of D+R among early ballots, minus the D two-party result."),
    card("It depends on the year", `${sg(lo.mean_gap, 1)} to ${sg(hi.mean_gap, 1)}`, `The average gap was ${sg(lo.mean_gap, 1)} points in ${lo.cycle} and ${sg(hi.mean_gap, 1)} in ${hi.cycle}. ${cs.map((c) => `${c.cycle}: ${sg(c.mean_gap, 1)} (${c.n} states)`).join("; ")}.`, "Years when one party shifted its voters to early voting move every state's gap together."),
    card("Same state, next cycle", `${frac(ps.agree_dm, ps.agree_dm_n)} agree`, `When a state's early-vote D share rose or fell from the same kind of election four years earlier, the result moved the same way in ${ps.agree} of ${ps.agree_n} states (${pc0(ps.agree, ps.agree_n)}). After taking out each cycle's average change, ${ps.agree_dm} of ${ps.agree_dm_n} (${pc0(ps.agree_dm, ps.agree_dm_n)}, 95% interval ${ci(ps.agree_dm_wilson)}); correlation ${sg(ps.corr_dm)}. Against the state's previous cycle of any kind: ${pa.agree_dm} of ${pa.agree_dm_n} after the same adjustment, correlation ${sg(pa.corr_dm)}.`, `${ps.n} same-type pairs${ps.skipped_definition ? `; ${ps.skipped_definition} left out because the early-vote definition changed` : ""}. A weak sign, not a rule.`),
    card("How much was cast early", `${frac(vs.agree_dm, vs.agree_dm_n)} agree`, `Early ballots as a share of the final vote, relative to the state's own past (same kind of election, each cycle's average removed): the D result moved the same way in ${vs.agree_dm} of ${vs.agree_dm_n} states (${pc0(vs.agree_dm, vs.agree_dm_n)}, 95% interval ${ci(vs.agree_dm_wilson)}); correlation ${sg(vs.corr_dm)}. Against any previous cycle: ${frac(va.agree_dm, va.agree_dm_n)}, correlation ${sg(va.corr_dm)}.`, `${vs.n} same-type pairs across all states with a total. Roughly a coin flip.`),
    card("Older voters (descriptive)", ag.n ? `${ag.n} states` : "too few", ag.n ? `Where a tracker reported the share of early voters in the oldest age group in both cycles (${ag.pairs.map((p) => `${p.st.toUpperCase()} ${p.c0} to ${p.c1}: ${sg(p.dx, 1)} pt of voters, result ${sg(p.dact, 1)} pt`).join("; ")}). Three states, a presidential year against a midterm: too few to read a pattern.` : "No state reports the oldest age group in two cycles with the same definition.", "Age groups are reported for only a handful of state-cycles; 2024's file does not state its groups."),
  ].join("");
  $("#eh-cards").innerHTML = cards;
  $("#eh-caveat").textContent = S.H.test_caveat;
}

// ----------------------------------------------------------------------------------------------------------------- small multiples
const T = { w: 148, h: 158, l: 22, r: 8, t: 22, b: 20 };
function tileSVG(st) {
  const rows = S.H.rows.filter((r) => r.st === st && r.gap != null);
  const X = d3.scaleLinear([10, 90], [T.l, T.w - T.r]), Y = d3.scaleLinear([10, 90], [T.h - T.b, T.t]);
  const dots = rows.map((r) => `<circle cx="${X(r.act).toFixed(1)}" cy="${Y(r.ev_d2).toFixed(1)}" r="4" fill="${CYC[r.cycle]}" stroke="#fff" stroke-width="1"><title>${esc(`${st.toUpperCase()} ${r.cycle}: early vote ${pc(r.ev_d2)} D, result ${pc(r.act)} D, gap ${pt(r.gap)}`)}</title></circle>`).join("");
  return `<svg viewBox="0 0 ${T.w} ${T.h}" role="img" aria-hidden="true"><line x1="${X(50)}" x2="${X(50)}" y1="${T.t}" y2="${T.h - T.b}" class="eh-g"/><line y1="${Y(50)}" y2="${Y(50)}" x1="${T.l}" x2="${T.w - T.r}" class="eh-g"/>` +
    `<line x1="${X(10)}" y1="${Y(10)}" x2="${X(90)}" y2="${Y(90)}" class="eh-d"/>${dots}<text x="${T.l}" y="${T.h - 6}" class="eh-t">10</text><text x="${X(90) - 8}" y="${T.h - 6}" class="eh-t">90</text><text x="2" y="${Y(90) + 4}" class="eh-t">90</text><text x="4" y="${Y(10)}" class="eh-t">10</text></svg>`;
}
function tiles() {
  const sts = S.H.states.filter((s) => s.n > 0);
  $("#eh-tiles").innerHTML = sts.map((s) => `<button type="button" class="eh-tile" data-st="${s.st}" aria-pressed="false" aria-label="${esc(`${s.name}: ${s.n} cycle${s.n === 1 ? "" : "s"}, mean gap ${pt(s.mean_gap)}, early vote on the same side of 50 as the result in ${s.right} of ${s.right_n}`)}"><span class="eh-tn"><b>${esc(s.name)}</b><i>${s.n} cycle${s.n === 1 ? "" : "s"} · ${pt(s.mean_gap)}</i></span>${tileSVG(s.st)}</button>`).join("");
  $("#eh-tiles").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => pick(b.dataset.st)));
  const used = new Set(S.H.rows.filter((r) => r.gap != null).map((r) => r.cycle));
  $("#eh-key").innerHTML = [...used].map((c) => `<span><i style="background:${CYC[c]}"></i>${c}</span>`).join("") + `<span><i class="eh-dl"></i>early vote = result</span>`;
}

// ----------------------------------------------------------------------------------------------------------------- change vs change
const PAIRS = { party: ["party_same", "Change in registered-D share of early ballots (points)"], volume: ["volume_same", "Change in early ballots as % of final votes (points)"] };
function drawChange() {
  const { measure, dm } = S.change, [key, xt] = PAIRS[measure];
  const used = S.H.pairs[key].filter((p) => !p.skipped);
  const xv = (p) => (dm ? p.dx_dm : p.dx), yv = (p) => (dm ? p.dact_dm : p.dact);
  const host = $("#eh-chart"), w = Math.max(300, host.clientWidth || 640), h = w < 520 ? 300 : 380, m = { l: 52, r: 14, t: 12, b: 44 };
  const ex = d3.max(used, (p) => Math.abs(xv(p))) * 1.1 || 1, ey = d3.max(used, (p) => Math.abs(yv(p))) * 1.1 || 1;
  const X = d3.scaleLinear([-ex, ex], [m.l, w - m.r]), Y = d3.scaleLinear([-ey, ey], [h - m.b, m.t]);
  const same = used.filter((p) => Math.sign(xv(p)) === Math.sign(yv(p)) && xv(p) && yv(p)).length, mv = used.filter((p) => xv(p) && yv(p)).length;
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img").attr("aria-label", `Scatter of ${xt.toLowerCase()} against the change in the D two-party result; ${same} of ${mv} on the same side of zero`);
  svg.append("g").attr("class", "grid").selectAll("line").data(Y.ticks(6)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", Y).attr("y2", Y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${Y(0)})`).call(d3.axisBottom(X).ticks(7).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${X(0)},0)`).call(d3.axisLeft(Y).ticks(6).tickSizeOuter(0));
  svg.append("text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 6).attr("text-anchor", "middle").text(`${xt}${dm ? ", cycle average removed" : ""}`);
  svg.append("text").attr("transform", `translate(12,${(m.t + h - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text(`Change in D two-party result${dm ? ", cycle average removed" : ""}`);
  svg.selectAll("circle").data(used).join("circle").attr("class", (p) => `eh-dot${p.st === S.st ? " sel" : ""}`).attr("cx", (p) => X(xv(p))).attr("cy", (p) => Y(yv(p))).attr("r", (p) => (p.st === S.st ? 6.5 : 4.5)).attr("fill", (p) => CYC[p.c1]).attr("data-st", (p) => p.st)
    .on("click", (_, p) => pick(p.st)).append("title").text((p) => `${p.st.toUpperCase()} ${p.c0} to ${p.c1}: early measure ${sg(p.x1 - p.x0, 1)} pt (${pc(p.x0)} to ${pc(p.x1)}), result ${sg(p.dact, 1)} pt (${pc(p.act0)} to ${pc(p.act1)})`);
  host.replaceChildren(svg.node());
  const s = S.H.pooled[measure === "party" ? "party_change_same" : "volume_same"];
  $("#eh-chart-read").textContent = `${used.length} state pairs (a state against the same kind of election four years earlier). ${dm ? `Cycle average removed: ` : ""}${same} of ${mv} moved the same way as the result (${pc0(same, mv)}); correlation ${sg(dm ? s.corr_dm : s.corr)}${s.skipped_definition ? `. ${s.skipped_definition} pairs are left out because the early-vote definition changed between the two cycles` : ""}.`;
  $("#eh-chart-key").innerHTML = [...new Set(used.map((p) => p.c1))].sort().map((c) => `<span><i style="background:${CYC[c]}"></i>${used.filter((p) => p.c1 === c)[0].c0} to ${c}</span>`).join("");
}
function changeUI() {
  const mk = (host, items, cur, set) => { const el = $(host); el.replaceChildren(...items.map(([k, t]) => { const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.setAttribute("aria-pressed", String(cur === k)); b.onclick = () => { set(k); changeUI(); }; return b; })); };
  mk("#eh-measure", [["party", "Party mix of early ballots"], ["volume", "How much was cast early"]], S.change.measure, (k) => (S.change.measure = k));
  mk("#eh-dm", [[true, "Cycle average removed"], [false, "Raw change"]].map(([k, t]) => [String(k), t]), String(S.change.dm), (k) => (S.change.dm = k === "true"));
  drawChange();
}

// ----------------------------------------------------------------------------------------------------------------- table
const sv = {
  name: (s) => s.name, cyc: (s) => s.n, mean: (s) => s.mean_gap ?? -1e9, abs: (s) => s.mean_abs_gap ?? -1e9, right: (s) => (s.right_n ? s.right / s.right_n : -1), dsame: (s) => (s.dir_same_n ? s.dir_same / s.dir_same_n : -1),
  dany: (s) => (s.dir_any_n ? s.dir_any / s.dir_any_n : -1), early: (s) => lastVolume(s)?.early_pct ?? -1, ev26: (s) => s.ev26?.d2 ?? -1, rng: (s) => s.ev26?.range_mean ?? -1,
};
const COLS = [["name", "State"], ["cyc", "Cycles", "n"], ["mean", "Mean gap", "n"], ["abs", "Mean |gap| (sd)", "n"], ["right", "Winner right", "n"], ["dsame", "Change direction, same type", "n"], ["dany", "Change direction, any previous", "n"],
  ["early", "Early % of final votes, latest", "n"], ["ev26", "2026 early-vote D share", "n"], ["rng", "D result if the past gaps held", "n"]];
const lastVolume = (s) => [...S.H.rows].filter((r) => r.st === s.st && r.early_pct != null).sort((a, b) => b.cycle - a.cycle)[0];
function table() {
  const th = $("#eh-table thead");
  th.innerHTML = `<tr>${COLS.map(([k, t, c]) => `<th class="${c ?? ""}" ${S.sort.key === k ? `aria-sort="${S.sort.dir > 0 ? "ascending" : "descending"}"` : ""}><button type="button" data-k="${k}">${esc(t)}</button></th>`).join("")}</tr>`;
  th.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.sort = { key: b.dataset.k, dir: S.sort.key === b.dataset.k ? -S.sort.dir : b.dataset.k === "name" ? 1 : -1 }; table(); $(`#eh-table thead [data-k="${b.dataset.k}"]`).focus({ preventScroll: true }); }));
  const rows = S.H.states.filter((s) => S.filter === "all" || s.n > 0).sort((a, b) => {
    const x = sv[S.sort.key](a), y = sv[S.sort.key](b);
    return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * S.sort.dir;
  });
  $("#eh-table tbody").innerHTML = rows.map((s) => {
    const lv = lastVolume(s), e = s.ev26;
    return `<tr data-st="${s.st}" class="${s.st === S.st ? "sel" : ""}"><td><button type="button" class="ev-name" data-st="${s.st}">${esc(s.name)}</button></td>` +
      `<td class="n">${s.n ? esc(s.cycles_party.map((c) => String(c).slice(2)).join(" ")) : "–"}</td><td class="n">${s.n ? pt(s.mean_gap) : "–"}</td><td class="n">${s.n ? `${f1(s.mean_abs_gap)}${s.sd_gap != null ? ` (${f1(s.sd_gap)})` : ""}` : "–"}</td>` +
      `<td class="n">${frac(s.right, s.right_n)}</td><td class="n">${frac(s.dir_same, s.dir_same_n)}</td><td class="n">${frac(s.dir_any, s.dir_any_n)}</td>` +
      `<td class="n">${lv ? `${pc(lv.early_pct, 0)} <span class="ev-detail">${lv.cycle}</span>` : "–"}</td><td class="n">${e ? pc(e.d2) : "–"}</td>` +
      `<td class="n">${e?.range ? `${pc(e.range[0], 0)} to ${pc(e.range[1], 0)}${e.range_crosses_50 ? `<span class="ev-detail">spans 50</span>` : ""}` : "–"}</td></tr>`;
  }).join("");
  $("#eh-table tbody").querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => pick(tr.dataset.st)));
  const f = $("#eh-filter");
  const n = S.H.states.filter((s) => s.n > 0).length;
  f.replaceChildren(...[["party", `With party figures (${n})`], ["all", `All ${S.H.states.length}`]].map(([k, t]) => { const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.setAttribute("aria-pressed", String(S.filter === k)); b.onclick = () => { S.filter = k; table(); }; return b; }));
}

// ----------------------------------------------------------------------------------------------------------------- one state
function detail() {
  const host = $("#eh-detail");
  if (!S.st) { host.innerHTML = `<p class="note">Choose a state here, on the map or in a table to see each cycle's early-vote share, every race's result and the gap.</p>`; return; }
  const s = S.H.states.find((x) => x.st === S.st), rows = S.H.rows.filter((r) => r.st === S.st).sort((a, b) => a.cycle - b.cycle);
  const body = rows.map((r) => {
    if (!r.report) return `<tr><td>${r.cycle}</td><td class="why" colspan="7">no report in the tracker for this state${r.why ? `: ${esc(r.why.replace(/^no report:\s*/, ""))}` : ""}</td></tr>`;
    const races = r.races.length ? r.races.map((x) => `${esc(x.office)}${x.special ? " (special)" : ""} ${pc(x.act)}`).join("; ") : "none in the results table";
    const why = r.party_ok ? "" : r.partial ? `partial report (${esc(r.partial)})` : r.D != null ? "party counts cover only part of the early ballots" : "no party registration reported";
    return `<tr><td>${r.cycle}</td><td>${day(r.report)} <span class="ev-detail">${r.days_before} day${r.days_before === 1 ? "" : "s"} before</span></td><td class="n">${r.total != null ? int(Math.round(r.total)) : "–"}<span class="ev-detail">${esc(r.defn)}</span></td>` +
      `<td class="n">${pc(r.early_pct, 0)}${r.volume_why && r.total ? `<span class="ev-detail">${esc(r.volume_why)}</span>` : ""}</td><td class="n">${r.ev_d2 != null ? pc(r.ev_d2) : `–<span class="ev-detail">${why}</span>`}</td><td>${races}</td><td class="n">${r.gap != null ? `${pt(r.gap)}<span class="ev-detail">${r.right ? "winner right" : r.right === false ? "winner wrong" : ""}</span>` : "–"}</td></tr>`;
  }).join("");
  const e = s.ev26;
  const now = e ? `<tr class="now"><td>2026</td><td>${day(e.as_of)} <span class="ev-detail">so far</span></td><td class="n">${e.total != null ? int(e.total) : "–"}</td><td class="n">–</td><td class="n">${pc(e.d2)}</td><td colspan="2" class="why">result not yet known</td></tr>` : "";
  let ctx;
  if (e && e.range) ctx = `<b>If this state's typical gap held (descriptive, not a projection).</b> Its registered-D share of early ballots today is ${pc(e.d2)}. Past gaps: ${pt(s.min_gap)} to ${pt(s.max_gap)} (mean ${pt(s.mean_gap)}, ${s.n} cycles). Taking each away from today's figure gives a D two-party share of ${pc(e.range[0])} to ${pc(e.range[1])}${e.range_crosses_50 ? ", a range that includes 50, so it does not settle who leads" : ""} (mean gap: ${pc(e.range_mean)}). ${esc(S.H.test_caveat)}`;
  else if (e) ctx = `<b>2026 context.</b> Registered-D share of early ballots today: ${pc(e.d2)}. ${s.n === 1 ? "Only one past cycle has a gap for this state, so no range is given." : "No past cycle has a gap for this state, so no range is given."}`;
  else ctx = `<b>2026 context.</b> ${s.ev26_status === "blocked" || s.ev26_status === "not_published" ? "No party count for 2026 is available for this state." : "No party-registration count of 2026 early ballots is published for this state."}`;
  host.innerHTML = `<h3 class="side-h">${esc(s.name)}</h3><div class="table-scroll"><table class="compact ev-cmp eh-det"><thead><tr><th>Cycle</th><th>Last report</th><th class="n">Early ballots</th><th class="n">% of final votes</th><th class="n">Registered-D share of D+R</th><th>Result, D two-party share</th><th class="n">Gap</th></tr></thead><tbody>${now}${body}</tbody></table></div>` +
    `<p class="note">${ctx}</p>`;
}

// ----------------------------------------------------------------------------------------------------------------- selection
function setSel(st) {
  S.st = st || null;
  const p = $("#eh-pick");
  if (p && p.value !== (st || "")) p.value = st || "";
  document.querySelectorAll("#eh-tiles .eh-tile").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.st === st)));
  document.querySelectorAll("#eh-table tbody tr").forEach((r) => r.classList.toggle("sel", r.dataset.st === st));
  document.querySelectorAll("#eh-chart .eh-dot").forEach((c) => { const on = c.getAttribute("data-st") === st; c.classList.toggle("sel", on); c.setAttribute("r", on ? 6.5 : 4.5); });
  detail();
}
function pick(st) {
  if (!st) return;
  const u = new URL(location.href); u.searchParams.set("st", st); history.replaceState(null, "", u);
  setSel(st);
  document.dispatchEvent(new CustomEvent("ev:pick", { detail: st }));
}

// ----------------------------------------------------------------------------------------------------------------- boot
function notes() {
  const H = S.H;
  $("#eh-sum").textContent = `Every state the UF Election Lab / US Elections Project early-vote trackers covered, each cycle's last report dated on or before Election Day, set against the certified result. ` +
    H.cycles.map((c) => `${c.cycle}: ${c.states_reported ? `${c.states_reported} states reported, ${c.states_with_party} with party registration` : "no data"}`).join("; ") + ".";
  $("#eh-method").innerHTML = `<ul>` +
    `<li><b>The gap.</b> Registered Democrats' share of Democratic plus Republican registered voters among the early ballots in the last report, minus the Democratic two-party share of the result (the mean of the state's certified Senate, governor and president races that cycle, each shown in the state view). Positive means the early vote looked more Democratic than the result. It is registration, not how anyone voted, and unaffiliated voters are left out of the share.</li>` +
    `<li><b>Which reports.</b> The last report of each tracker dated on or before Election Day, with its date shown. ${H.cycles.map((c) => `${c.cycle}: ${esc(c.source)}${c.states_reported ? ` (${c.on_election_day} of ${c.states_reported} dated on Election Day itself, which count ballots through the day before or that morning)` : ""}`).join(". ")}.</li>` +
    `<li><b>Changes.</b> A state's change in the early-vote measure between two cycles against the change in its result. “Same type” compares presidential with presidential and midterm with midterm; “any previous” uses the state's previous cycle in the data. “Cycle average removed” subtracts that cycle's average change over the states first, so a swing shared by every state cannot make the two agree. States are compared only where the tracker's early-vote definition (mail only, in-person only, both) is the same in the two cycles.</li>` +
    H.notes.map((n) => `<li>${esc(n)}</li>`).join("") + `</ul>`;
  $("#eh-lic").innerHTML = `<b>${esc(H.label)}.</b> ${esc(H.licence)}. Secondary source, aggregates only. Generated ${esc(H.built)}.`;
}
async function init() {
  const H = (S.H = await getJSON("ev2026/ev_history_all.json", { optional: true }));
  const sec = $("#eh-sec");
  if (!H) { sec.hidden = true; return; }
  notes(); strip(); findings(); tiles(); changeUI(); table();
  const pk = $("#eh-pick");
  pk.innerHTML = `<option value="">Choose a state</option>` + H.states.map((s) => `<option value="${s.st}">${esc(s.name)}</option>`).join("");
  pk.onchange = () => pick(pk.value);
  const want = new URLSearchParams(location.search).get("st");
  setSel(H.states.some((s) => s.st === want) ? want : null);
  document.addEventListener("ev:state", (e) => setSel(e.detail));
  let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(drawChange, 200); });
  window.__ehReady = true;
}
init().catch((e) => { console.error(e); const s = $("#eh-sec"); if (s) s.hidden = true; });
