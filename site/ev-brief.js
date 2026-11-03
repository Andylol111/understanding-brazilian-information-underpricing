// 'What the early data says' (turnout.html and earlyvote.html, first section): four plain-English questions, each with a one-line answer
// computed here from the published files (ev_races.json, ev_history_all.json, turnout_model.json) and one chart. Every number and every
// word that depends on a number (direction, 'up', 'down', 'no consistent direction') is derived at load time; nothing is hard-coded.
// Registration-based, unregistered, unvalidated context, not used by any model. Never scrolls the page.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const MINUS = "\u2212";
const D_COL = "#1f5fbf", R_COL = "#c8102e", UP_COL = "#6a7f99", DOWN_COL = "#c4501b", INK = "#1b1b1b", MUTED = "#5f6368", RULE = "#d9d6cc";
const num = (x, d = 1) => Math.abs(x).toFixed(d);
const side = (x, d = 1) => (Math.abs(x) < 0.05 ? "even" : `${x > 0 ? "D" : "R"}+${num(x, d)}`); // D right, R left
const mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;
const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const pct = (x, d = 0) => `${(100 * x).toFixed(d)} %`;
const cnt = (x) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1e4 ? `${Math.round(x / 1e3)}k` : Math.round(x).toLocaleString("en-US"));
const fetchJSON = (f) => fetch(new URL(`data/ev2026/${f}`, location.href), { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const plural = (n, one, many) => (n === 1 ? one : many);
const stateOf = (r) => r.name.replace(/\s+(senate|governor|house|president)$/i, "");

// ------------------------------------------------------------------------------------------------------------------ chart
function niceStep(span, maxTicks) {
  const k = 10 ** Math.floor(Math.log10(span / maxTicks));
  return [1, 2, 5, 10].map((m) => m * k).find((st) => span / st <= maxTicks);
}
// rows: [{label, v, color, title}]; kind 'dot' (lollipop) or 'bar'; lo/hi: value domain; fmt: value label; line: {v, text} extra vertical line; ends: [leftText, rightText]
function hchart(o) {
  const W = Math.max(240, Math.floor(o.w)), labelW = o.labelW ?? (W < 420 ? 62 : 84), padR = 8, rowH = 24, top = o.line ? 20 : 6, bot = o.ends ? 40 : 22;
  const px0 = labelW, pw = W - labelW - padR, H = top + o.rows.length * rowH + bot;
  const x = (v) => px0 + ((v - o.lo) / (o.hi - o.lo)) * pw;
  const step = niceStep(o.hi - o.lo, W < 420 ? 4 : 6);
  let g = "";
  for (let t = Math.ceil(o.lo / step) * step; t <= o.hi + 1e-9; t += step) {
    g += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - bot}" stroke="${RULE}" stroke-width="1"/><text x="${x(t)}" y="${H - bot + 14}" text-anchor="middle" font-size="11" fill="${MUTED}">${esc(o.tick ? o.tick(t) : (t < 0 ? MINUS : "") + Math.abs(Math.round(t * 100) / 100))}</text>`;
  }
  const z = x(o.base ?? 0);
  g += `<line x1="${z}" x2="${z}" y1="${top - 2}" y2="${H - bot}" stroke="${INK}" stroke-width="1.5"/>`;
  if (o.line) g += `<line x1="${x(o.line.v)}" x2="${x(o.line.v)}" y1="${top - 4}" y2="${H - bot}" stroke="${INK}" stroke-width="1.5" stroke-dasharray="4 3"/><text x="${Math.min(Math.max(x(o.line.v), px0 + 40), W - 40)}" y="${top - 8}" text-anchor="middle" font-size="11.5" font-weight="600" fill="${INK}">${esc(o.line.text)}</text>`;
  o.rows.forEach((r, i) => {
    const y = top + i * rowH + rowH / 2, vx = x(r.v), out = r.v >= (o.base ?? 0) ? 1 : -1;
    g += `<g><title>${esc(r.title ?? r.label)}</title><text x="${labelW - 8}" y="${y + 4}" text-anchor="end" font-size="12" fill="${INK}">${esc(r.label)}</text>`;
    if (o.kind === "dot") g += `<line x1="${z}" x2="${vx}" y1="${y}" y2="${y}" stroke="${r.color}" stroke-opacity="0.45" stroke-width="2"/><circle cx="${vx}" cy="${y}" r="5.5" fill="${r.color}"/>`;
    else g += `<rect x="${Math.min(z, vx)}" y="${y - 7}" width="${Math.max(1, Math.abs(vx - z))}" height="14" fill="${r.color}" rx="1"/>`;
    // dot plot: the value sits on the empty side of the zero line; bars: at the bar's end
    const inward = o.kind === "dot", tx = inward ? z - out * 8 : vx + out * 6, anchor = (inward ? -out : out) > 0 ? "start" : "end";
    g += `<text x="${tx}" y="${y + 4}" text-anchor="${anchor}" font-size="11.5" fill="${MUTED}">${esc(r.text ?? o.fmt(r.v))}</text></g>`;
  });
  if (o.ends) g += `<text x="${px0}" y="${H - 4}" text-anchor="start" font-size="11" fill="${o.endColors?.[0] ?? MUTED}">${esc(o.ends[0])}</text><text x="${W - padR}" y="${H - 4}" text-anchor="end" font-size="11" fill="${o.endColors?.[1] ?? MUTED}">${esc(o.ends[1])}</text>`;
  return `<svg class="brief-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">${g}</svg>`;
}
const span = (vals, pad = 0.12, floor = 1) => { const m = Math.max(floor, ...vals.map(Math.abs)); return m * (1 + pad); };

// -------------------------------------------------------------------------------------------------------------- answers
// each returns {answer, caveats[], how, chart(w) | null}
function qModel(E) {
  const races = (E?.races ?? []).filter((r) => r.implied?.ok && r.implied.vs_model?.diff != null);
  if (!races.length) return { answer: "No race has both an early-vote-implied figure and a model figure yet.", caveats: [], how: "Needs ev_races.json races with an EV-implied value.", chart: null };
  const rows = races.map((r) => ({ r, d: r.implied.vs_model.diff })).sort((a, b) => a.d - b.d);
  const ds = rows.map((x) => x.d), n = ds.length, m = mean(ds), lo = ds[0], hi = ds[n - 1];
  const nD = ds.filter((d) => d > 0).length, nR = ds.filter((d) => d < 0).length, major = Math.max(nD, nR);
  const consistent = n >= 5 && major / n >= 0.8;
  const ahead = nD > nR ? "D" : "R";
  const answer = consistent
    ? `Yes, one direction: the early vote runs ${ahead}-ward of the model in ${major} of ${n} races (differences ${side(lo)} to ${side(hi)}, average ${side(m)}).`
    : `No consistent direction: differences range from ${side(lo)} to ${side(hi)} and average ${side(m)} (${nD} ${plural(nD, "race", "races")} D-ward, ${nR} R-ward, of ${n}).`;
  const caveats = ["The early-vote-implied method failed its registered test and is not used by any model. Registration-based, unvalidated."];
  const by = new Map();
  for (const x of rows) by.set(x.r.state, [...(by.get(x.r.state) ?? []), x]);
  let contra = null;
  for (const [st, xs] of by) {
    const neg = xs.filter((x) => x.d < -5), pos = xs.filter((x) => x.d > 5);
    if (neg.length && pos.length) { const a = neg[0], b = pos[pos.length - 1]; contra = `${stateOf(a.r)}: the ${a.r.office} race reads ${side(a.d)} and the ${b.r.office} race ${side(b.d)} from the same early ballots, so the early vote is not telling a consistent story there; that points to noise.`; break; }
  }
  if (contra) caveats.push(contra);
  const how = `Each dot is a race with an EV-implied value (${n} of ${E.races.length} listed races): EV-implied D share minus the model's D share (the model with polls), in points. Positive means the early vote reads more Democratic than the model, negative more Republican. The dashed line is the plain average of the ${n} differences. A mean near zero with both signs present means there is no systematic miss to read off. ${E.test?.text ? `Registered test: ${E.test.text}.` : ""}`;
  const chart = (w) => { const M = span(ds, 0.14, 2); return hchart({ w, kind: "dot", lo: -M, hi: M, fmt: (v) => side(v), line: { v: m, text: `average ${side(m)}` }, ends: ["\u2190 EV reads more R than model", "more D \u2192"], endColors: [R_COL, D_COL],
    aria: `Dot plot of EV-implied minus model, ${n} races, average ${side(m)}`, rows: rows.map((x) => ({ label: `${x.r.state} ${x.r.office === "governor" ? "Gov" : x.r.office === "senate" ? "Sen" : x.r.office}`, v: x.d, color: x.d >= 0 ? D_COL : R_COL, title: `${x.r.name}: ${side(x.d)}` })) }); };
  return { answer, caveats, how, chart, stats: { n, m, lo, hi, nD, nR, consistent, ahead } };
}

function qHistory(H) {
  const rows = (H?.rows ?? []).filter((r) => r.gap != null && r.act != null);
  const cycles = (H?.cycles ?? []).filter((c) => c.n > 0 && c.mean_gap != null);
  if (!rows.length) return { answer: "No past state-cycle has both an early-vote party figure and a result yet.", caveats: [], how: "Needs ev_history_all.json rows.", chart: null };
  const n = rows.length, right = rows.filter((r) => r.right).length, m = mean(rows.map((r) => r.gap)), mabs = mean(rows.map((r) => Math.abs(r.gap)));
  const lop = rows.filter((r) => Math.abs(r.act - 50) >= 5), close = rows.filter((r) => Math.abs(r.act - 50) < 5);
  const lopRight = lop.filter((r) => r.right).length, closeWrong = close.filter((r) => !r.right).length;
  const tilt = m > 1 ? `tilted D (average ${side(m)})` : m < -1 ? `tilted R (average ${side(m)})` : `with no steady tilt (average ${side(m)})`;
  const answer = `Right on the winner in ${right} of ${n} state-cycles (${pct(right / n)}), ${lopRight} of ${lop.length} where the result was 5+ points from even; off by ${num(mabs)} points on average, ${tilt}.`;
  const caveats = [close.length ? `In the ${close.length} state-cycles decided by under 5 points, the early vote was on the wrong side in ${closeWrong} (${pct(closeWrong / close.length)}): it cannot call a close race.` : "No state-cycle was decided by under 5 points.",
    "Gap = early-vote Democratic share of Democratic + Republican registrations on returned ballots minus the certified two-party result; positive means the early vote read more Democratic than the result. Registration is not a vote."];
  const how = `Every past state-cycle in ${cycles.map((c) => c.cycle).join(", ")} where a state published early ballots by party and the certified statewide result is known (${n} of them, from ${H.sources ? Object.keys(H.sources).length : "several"} sources). Winner right means the early-vote share and the result fall on the same side of 50. Mean gap is the signed average; mean |gap| ignores direction. Bars are each cycle's mean gap. ${H.test_caveat ?? ""}`;
  const chart = (w) => { const M = span(cycles.map((c) => c.mean_gap), 0.45, 2); return hchart({ w, kind: "bar", labelW: 112, lo: cycles.some((c) => c.mean_gap < 0) ? -M : -M * 0.2, hi: M, fmt: (v) => side(v), ends: ["\u2190 EV read more R than result", "more D \u2192"], endColors: [R_COL, D_COL],
    aria: `Mean early-vote gap by cycle: ${cycles.map((c) => `${c.cycle} ${side(c.mean_gap)}`).join(", ")}`, rows: cycles.map((c) => ({ label: `${c.cycle}: ${c.right}/${c.right_n} right`, v: c.mean_gap, color: c.mean_gap >= 0 ? D_COL : R_COL, title: `${c.cycle}: mean gap ${side(c.mean_gap)}, mean |gap| ${num(c.mean_abs_gap)}, winner right ${c.right} of ${c.right_n}` })) }); };
  return { answer, caveats, how, chart, stats: { n, right, m, mabs, closeN: close.length, closeWrong } };
}

function stateRatios(T) {
  const out = [];
  for (const s of Object.values(T?.states ?? {})) {
    const sp = s.vs_2022_same_point, sv = typeof sp === "number" ? sp : sp?.ratio ?? null, fin = s.vs_2022_final?.ok ? s.vs_2022_final.ratio : null;
    if (sv != null) out.push({ st: s.st, name: s.name, ratio: sv, basis: "same point" });
    else if (fin != null) out.push({ st: s.st, name: s.name, ratio: fin, basis: "final pre-election report" });
  }
  return out.sort((a, b) => b.ratio - a.ratio);
}
function projTest(T) {
  const P = T?.proj_summary, v = P?.variant;
  if (!P || !v) return null;
  const rows = (P.backtest?.rows ?? []).filter((r) => r.kind === "same" && r[`mae_${v}`] != null && r.mae_M != null && r.n);
  const r = rows[rows.length - 1];
  return r ? { cycle: r.cycle, n: r.n, mae: r[`mae_${v}`], base: r.mae_M } : null;
}
function qTurnout(T) {
  const rs = stateRatios(T);
  const bt = projTest(T);
  const proj = bt ? `Final turnout against the model's expectation is not projected reliably: in the ${bt.cycle} backtest (${bt.n} states) the projection missed by ${num(bt.mae)} % on average against ${num(bt.base)} % for a naive baseline${bt.mae > bt.base ? "; it does not beat the baseline" : ""}.` : "Final turnout against the model's expectation is not projected reliably here; treat early counts as counts only.";
  if (!rs.length) return { answer: "No state has a 2022 early-ballot figure to compare with yet.", caveats: [proj], how: "Needs turnout_model.json states with vs_2022_same_point or vs_2022_final.", chart: null };
  const up = rs.filter((r) => r.ratio > 1).length, down = rs.filter((r) => r.ratio < 1).length, med = median(rs.map((r) => r.ratio));
  const nSame = rs.filter((r) => r.basis === "same point").length, nFin = rs.length - nSame;
  const answer = down === 0 || up === 0
    ? `Early ballots are ${up ? "up" : "down"} in all ${rs.length} states with a 2022 figure (median ${(med).toFixed(2)}\u00d7 2022).`
    : `Early ballots are up in ${up} of ${rs.length} states with a 2022 figure (median ${med.toFixed(2)}\u00d7 2022), down in ${down}.`;
  const caveats = [nFin ? `${nSame ? `${nSame} ${plural(nSame, "state compares", "states compare")} the same point before Election Day; ` : ""}${nFin} (marked *) ${plural(nFin, "compares", "compare")} with 2022's final pre-election report, which is later than today's count, so the true like-for-like rise is larger than shown.` : "All comparisons are at the same point before Election Day.", proj];
  const how = `For every state with a 2022 figure: ballots banked before Election Day (early + mail) divided by 2022's, at the same number of days before Election Day where a day-by-day 2022 history exists, otherwise against the final pre-election report of 2022 (marked *). Bars show the percent change; zero is 2022. The median is the middle state's ratio. Only ${rs.length} of ${Object.keys(T.states).length} states have either figure; the rest published no 2022 history. Early ballots are not turnout: mail-heavy states move timing, not totals.`;
  const chart = (w) => { const pc = rs.map((r) => 100 * (r.ratio - 1)), M = span(pc, 0.3, 10), mn = Math.min(0, ...pc); return hchart({ w, kind: "bar", lo: mn < 0 ? mn * 1.4 : -M * 0.04, hi: M, fmt: (v) => `${v >= 0 ? "+" : MINUS}${Math.abs(v).toFixed(0)} %`, tick: (t) => `${t > 0 ? "+" : t < 0 ? MINUS : ""}${Math.abs(t)}%`, ends: ["early ballots vs 2022", "* vs 2022 final report"],
    aria: `Early ballots against 2022 by state: ${rs.map((r) => `${r.st} ${r.ratio.toFixed(2)}x`).join(", ")}`, rows: rs.map((r) => ({ label: `${r.st}${r.basis === "same point" ? "" : "*"}`, v: 100 * (r.ratio - 1), color: r.ratio >= 1 ? UP_COL : DOWN_COL, title: `${r.name}: ${r.ratio.toFixed(2)}\u00d7 2022 (${r.basis})` })) }); };
  return { answer, caveats, how, chart, stats: { n: rs.length, up, down, med } };
}

const FIRST = new Set(["early_first", "with_precincts"]);
function leanGroups(T) {
  const g = { D: [], R: [], even: [], late: [] };
  for (const s of Object.values(T?.states ?? {})) {
    const c = s.order?.class, d = s.lean?.d_share_of_dr;
    if (!c || d == null) continue;
    const e = { st: s.st, d };
    if (c === "late") g.late.push(e);
    else if (FIRST.has(c)) (d >= 52.5 ? g.D : d <= 47.5 ? g.R : g.even).push(e);
  }
  for (const k of Object.keys(g)) g[k].sort((a, b) => b.d - a.d);
  return g;
}
function qReturns(T) {
  const g = leanGroups(T);
  const out = Object.values(T?.states ?? {}).filter((s) => s.outstanding?.ok && s.outstanding.kind === "outstanding" && s.outstanding.value > 0)
    .map((s) => ({ st: s.st, name: s.name, v: s.outstanding.value, cls: s.order?.class, text: s.order?.text, issued: s.outstanding.issued_label })).sort((a, b) => b.v - a.v).slice(0, 6);
  const list = (a) => a.map((e) => e.st).join(", ");
  const parts = [];
  if (g.D.length) parts.push(`D in ${list(g.D)}`);
  if (g.R.length) parts.push(`R in ${list(g.R)}`);
  if (g.even.length) parts.push(`near even in ${list(g.even)}`);
  let answer = parts.length ? `First returns likely lean ${parts.join("; ")}, by party registration of the ballots already banked` : "No state yet has both a count-order statement and a party breakdown of its banked ballots";
  answer += out.length ? `. The biggest mail counts still to come back: ${out.slice(0, 3).map((o) => `${o.st} ${cnt(o.v)}`).join(", ")}.` : ".";
  const caveats = ["Registration is not a vote: unaffiliated and crossover voters mean a lean is a hint about the order of the first batch, not about the final result."];
  if (g.late.length) caveats.push(`${list(g.late)} ${plural(g.late.length, "counts", "count")} early ballots late (${[...new Set(g.late.map((e) => T.states[e.st].order.text))].join("; ")}), so the registration lean does not describe its first returns.`);
  const how = `Lean: states where the project has both a count-order statement (when early ballots are reported relative to Election Day votes) and a party breakdown of banked ballots. Registered Democrats' share of Democrats + Republicans of at least 52.5 % is called D, at most 47.5 % R, in between even; only states whose early ballots are reported first or with precincts are listed. Bars: ballots sent or requested and not yet returned (some will vote in person, some not at all), the six largest; states that mail a ballot to every voter are left out because that figure is not a count of voters still to vote.`;
  const chart = out.length ? (w) => hchart({ w, kind: "bar", lo: 0, hi: Math.max(...out.map((o) => o.v)) * 1.3, fmt: (v) => cnt(v), tick: (t) => cnt(t),
    aria: `Largest outstanding mail ballot counts: ${out.map((o) => `${o.st} ${cnt(o.v)}`).join(", ")}`, rows: out.map((o) => ({ label: `${o.st}${o.cls === "late" ? " (late count)" : ""}`.replace(" (late count)", " \u00b7 late"), v: o.v, color: o.cls === "late" ? DOWN_COL : UP_COL, title: `${o.name}: ${cnt(o.v)} ${o.issued ?? "sent"} and not returned${o.cls === "late" && o.text ? `; ${o.text}` : ""}` })) }) : null;
  return { answer, caveats, how, chart, stats: { g, out } };
}

function bottomLine(a1, a2, a3) {
  const bits = [];
  const s1 = a1.stats, s2 = a2.stats, s3 = a3.stats;
  if (s1) bits.push(s1.consistent ? `The early vote reads ${s1.ahead}-ward of the model in most races (${Math.max(s1.nD, s1.nR)} of ${s1.n}), though the method failed its registered test` : "The early data neither confirms nor contradicts the model: the differences go both ways");
  if (s2) bits.push(`the early vote has been ${s2.m > 1 ? "tilted D" : s2.m < -1 ? "tilted R" : "untilted"} and off by about ${s2.mabs.toFixed(0)} points, so it cannot settle a close race`);
  if (s3) bits.push(s3.down === 0 ? "early ballots are up on 2022" : s3.up === 0 ? "early ballots are down on 2022" : `early ballots are up in ${s3.up} of ${s3.n} states with a 2022 figure`);
  return bits.length ? `${bits.join("; ")}; the count tonight is what tests the model.` : "The early data is not yet available; the count tonight is what tests the model.";
}

// ------------------------------------------------------------------------------------------------------------------ page
const QS = [
  { id: "model", q: "Does the early vote point to the model being off?", f: "model" },
  { id: "history", q: "Has the early vote matched results before?", f: "history" },
  { id: "turnout", q: "Is turnout up?", f: "turnout" },
  { id: "returns", q: "What should I expect from the returns tonight?", f: "returns" },
];
export async function initBrief() {
  const host = document.getElementById("brief");
  if (!host) return;
  const [E, H, T] = await Promise.all([fetchJSON("ev_races.json"), fetchJSON("ev_history_all.json"), fetchJSON("turnout_model.json")]);
  const A = { model: qModel(E), history: qHistory(H), turnout: qTurnout(T), returns: qReturns(T) };
  const cards = QS.map((Q, i) => {
    const a = A[Q.f];
    return `<article class="brief-card" id="brief-${Q.id}" aria-labelledby="brief-${Q.id}-h"><h3 id="brief-${Q.id}-h">${esc(Q.q)}</h3>`
      + `<p class="brief-answer" data-brief="answer"><b>${esc(a.answer)}</b></p><div class="brief-chart" data-brief="chart"></div>`
      + a.caveats.map((c) => `<p class="brief-cav">${esc(c)}</p>`).join("")
      + `<details class="brief-how"><summary>How this was computed</summary><p>${esc(a.how)}</p></details></article>`;
  }).join("");
  host.innerHTML = `<h2 id="brief-h">What the early data says</h2>`
    + `<p class="brief-bottom" id="brief-bottom" data-brief="bottom"><b>Bottom line.</b> ${esc(bottomLine(A.model, A.history, A.turnout))}</p>`
    + `<p class="brief-label">Registration-based. The early-vote-implied method failed its registered test. Unregistered and unvalidated; not used by any model.</p>`
    + `<div class="brief-grid">${cards}</div>`;
  const draw = () => {
    for (const Q of QS) {
      const box = host.querySelector(`#brief-${Q.id} .brief-chart`), a = A[Q.f];
      if (!a.chart) { box.hidden = true; continue; }
      const w = box.clientWidth;
      if (w && Math.abs(w - (box._w ?? 0)) > 2) { box.innerHTML = a.chart(w); box._w = w; }
    }
  };
  draw();
  let t = 0;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(draw, 120); });
  window.__briefReady = true;
}
