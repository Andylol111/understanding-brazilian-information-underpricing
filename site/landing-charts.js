// Homepage charts. Each one explains itself: a plain-language "how to read it" box with a worked
// example whose numbers come from the same data as the chart.
//   1. one night, step by step (the featured replay's timeline)
//   2. who got to 90% first, per election (the elections' own stats files)
//   3. the projection against the raw count (summary.json projection_error)
//   4. how often the final result stayed inside the 90% band (summary.json calibration)
import * as d3 from "./vendor/d3.js";
import { C, pct, clock, tzName } from "./common.js";
import { $, allElections, label, labelOf, leadOf, featured, dur } from "./landing-data.js";

const pts = (x) => (x == null ? "–" : x.toFixed(1));
const fmtInt = (n) => n.toLocaleString("en-US");
const signed = (m) => `${m > 0 ? "+" : m < 0 ? "−" : ""}${dur(m)}`;

// Redraw a chart whenever its box changes width.
function responsive(root, draw) {
  draw();
  let w = root.clientWidth;
  new ResizeObserver(() => { if (root.clientWidth !== w) { w = root.clientWidth; draw(); } }).observe(root);
}

// A chart box with its caption under it: the chart redraws in the box, the caption stays.
function mount(fig, caption) {
  fig.innerHTML = `<div class="chart-box"></div>${caption ? `<figcaption>${caption}</figcaption>` : ""}`;
  return fig.querySelector(".chart-box");
}

const how = (...paras) => `<div class="how"><b>How to read it</b>${paras.map((p, i) => `<p${i === paras.length - 1 && /^Example/.test(p) ? ' class="eg"' : ""}>${p}</p>`).join("")}</div>`;

/* ------------------------------------------------------------------ 1. one night, step by step */

export async function drawNight() {
  const F = await featured();
  if (!F) return;
  const { rows, gate, race, settle, tz } = F;
  const last = rows[rows.length - 1];
  const side = race.a.name;
  const nameOf = label(allElections().find((e) => e.id === F.id) ?? { name: F.meta.name, date: F.meta.date });
  $("ev-night").hidden = false;

  const t = (ms) => clock(ms, tz);
  const modelAt = settle?.model ? Date.parse(settle.model) : null;
  const marketAt = settle?.polymarket ? Date.parse(settle.polymarket) : null;
  const modelMerged = modelAt != null && Math.abs(modelAt - gate.tms) <= 3 * 60000;
  const events = [{ t: gate.tms, lines: modelMerged ? ["1% counted", `model already at ${pct(gate.P, 0)}`] : ["1% counted"], on: ["P", "pm"] }];
  if (modelAt != null && !modelMerged) events.push({ t: modelAt, lines: ["Model reaches 90%"], on: ["P"] });
  if (marketAt != null) events.push({ t: marketAt, lines: ["Polymarket reaches 90%"], on: ["pm"] });
  events.push({ t: last.tms, lines: [`Result: ${side} ${pct(last.raw, 1)}`, `of the vote, ${(100 * last.counted).toFixed(2)}% counted`], on: ["P", "pm"] });

  const lead = modelAt != null && marketAt != null ? Math.round((marketAt - modelAt) / 60000) : null;
  $("ev-night-lead").textContent = `Follow one count from the first votes to the result. This is the ${nameOf} night. The orange line is the model's chance that ${side} wins. The black line is the Polymarket price for the same outcome. The grey area is the share of the vote counted.`;
  $("ev-night-how").innerHTML = how(
    "Read the chart from left to right. Each vertical line marks one moment.",
    `Example: at ${t(gate.tms)}, with 1% counted, the model gave ${side} ${pct(gate.P, 0)}. Polymarket priced the same outcome at ${pct(gate.pm, 0)}.${marketAt != null ? ` The market reached 90% at ${t(marketAt)}${lead != null && lead > 0 ? `, ${dur(lead)} later` : ""}.` : ""}`,
  );

  const root = mount($("chart-night"), `<span class="m">Model</span> (chance ${side} wins), <span class="k">Polymarket price</span>, <span class="r2">vote counted</span> (grey area, right axis). Dotted lines, left to right: ${events.map((ev, i) => `${i + 1}) ${ev.lines[0]}`).join("; ")}.`);
  responsive(root, () => {
    const w = Math.max(320, root.clientWidth), h = w < 560 ? 430 : 440;
    const m = { l: 56, r: 56, t: 104, b: 50 };
    const x = d3.scaleUtc().domain([rows[0].tms, last.tms]).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    const svg = d3.select(root).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
      .attr("aria-label", `One night: the model's chance and the Polymarket price that ${side} wins, and the share of the vote counted, with the key moments marked`);
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y);
    svg.append("path").datum(rows).attr("fill", "#ebe9e2").attr("d", d3.area().x((d) => x(d.tms)).y0(y(0)).y1((d) => y(d.counted)).curve(d3.curveStepAfter));
    svg.append("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0.9)).attr("y2", y(0.9)).attr("stroke", "#9a968c").attr("stroke-dasharray", "4,3");
    svg.append("text").attr("class", "legend-text").attr("x", w - m.r - 4).attr("y", y(0.9) - 4).attr("text-anchor", "end").text("90%");
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).tickValues([0, 0.25, 0.5, 0.75, 1]).tickFormat(d3.format(".0%")).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis axis-r").attr("transform", `translate(${w - m.r},0)`).call(d3.axisRight(y).tickValues([0, 0.25, 0.5, 0.75, 1]).tickFormat(d3.format(".0%")).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`)
      .call(d3.axisBottom(x).ticks(w < 560 ? 4 : 8).tickFormat((d) => clock(d, tz)).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
    svg.append("text").attr("class", "panel-label").attr("x", (m.l + w - m.r) / 2).attr("y", h - 6).attr("text-anchor", "middle").text(`time of day (${tzName(tz, gate.tms)})`);
    svg.append("text").attr("class", "panel-label").attr("transform", `translate(12,${(m.t + h - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text(`chance ${side} wins`);
    svg.append("text").attr("class", "panel-label").attr("transform", `translate(${w - 8},${(m.t + h - m.b) / 2}) rotate(90)`).attr("text-anchor", "middle").attr("fill", "#8a867c").text("share of the vote counted");
    const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.tms)).y((d) => y(d[k])).curve(d3.curveStepAfter);
    svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 2).attr("d", line("pm"));
    svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2.4).attr("d", line("P"));
    const rowAt = (ms) => rows.find((d) => d.tms >= ms) ?? last;
    events.forEach((ev, i) => {
      const px = x(ev.t), level = i % 3, ty = 16 + level * 30;
      const right = px > w - 150;
      svg.append("line").attr("x1", px).attr("x2", px).attr("y1", ty + 6 + (ev.lines.length - 1) * 14).attr("y2", h - m.b).attr("stroke", C.muted).attr("stroke-dasharray", "2,3");
      const tx = svg.append("text").attr("class", "note-text").attr("x", px + (right ? -6 : 6)).attr("y", ty).attr("text-anchor", right ? "end" : "start");
      tx.append("tspan").attr("font-weight", 700).text(`${i + 1}  ${t(ev.t)}  ${ev.lines[0]}`);
      ev.lines.slice(1).forEach((s) => tx.append("tspan").attr("x", px + (right ? -6 : 6)).attr("dy", 14).text(s));
      const row = rowAt(ev.t);
      for (const k of ev.on) svg.append("circle").attr("cx", px).attr("cy", y(row[k] ?? 0)).attr("r", 4.2).attr("fill", k === "P" ? C.model : C.market).attr("stroke", "#fff").attr("stroke-width", 1.5);
    });
  });
}

/* ------------------------------------------------------------------ 2. who got to 90% first */

export async function drawLead() {
  const all = await Promise.all(allElections().filter((e) => e.status !== "scheduled").map(async (e) => ({ e, L: await leadOf(e.id) })));
  const rows = all.filter((r) => r.L?.vs === "market").sort((a, b) => b.L.minutes - a.L.minutes);
  if (!rows.length) return;
  $("ev-lead").hidden = false;
  const first = rows.filter((r) => r.L.minutes > 0).length;
  const best = rows[0];
  const sameCount = rows.length === 1 ? "the one election with market prices" : `${rows.length} elections with market prices`;
  $("ev-lead-lead").textContent = `The model reached 90% on the eventual winner before the market in ${first} of ${sameCount}.${best.L.minutes > 0 ? ` The biggest lead was ${dur(best.L.minutes)}, in the ${label(best.e)}.` : ""}`;
  const D = Math.max(60, Math.ceil(d3.max(rows.filter((r) => Math.abs(r.L.minutes) <= 240), (r) => Math.abs(r.L.minutes)) / 50) * 50 || 60);
  const off = rows.filter((r) => Math.abs(r.L.minutes) > D);
  $("ev-lead-how").innerHTML = how(
    "Each bar is one election. It compares the model with Polymarket on the main race. A bar to the right means the model reached 90% on the eventual winner first. A bar to the left means the market did. Time starts when 1% of the vote is counted.",
    `Example: the ${label(best.e)} bar says ${signed(best.L.minutes)}. The model reached 90% ${dur(best.L.minutes)} ${best.L.minutes >= 0 ? "before" : "after"} the market.`,
    `Each row also says how many Polymarket markets the election had. The bar shows the main race only.${off.length ? ` ${off.map((r) => label(r.e)).join(" and ")} ${off.length === 1 ? "runs" : "run"} off the scale (${off.map((r) => signed(r.L.minutes)).join(", ")}).` : ""} Elections with no market prices, or with no count over time, have no bar.`,
  );
  const root = mount($("chart-lead"), "");
  responsive(root, () => {
    const w = Math.max(280, root.clientWidth), narrow = w < 560, rh = narrow ? 60 : 46, m = { l: 6, r: 56, t: 30, b: 34 };
    const h = m.t + rows.length * rh + m.b;
    const x = d3.scaleLinear().domain([-D, D]).range([m.l, w - m.r]);
    const clip = (v) => Math.max(-D, Math.min(D, v));
    const svg = d3.select(root).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
      .attr("aria-label", "Minutes by which the model reached 90% on the eventual winner before (right) or after (left) the market, per election");
    svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(4)).join("line").attr("x1", x).attr("x2", x).attr("y1", m.t - 6).attr("y2", h - m.b);
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickFormat((d) => (d > 0 ? `+${d}` : d < 0 ? `−${-d}` : "0")).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("text").attr("class", "panel-label").attr("x", x(0) - 8).attr("y", 12).attr("text-anchor", "end").text("◀ market first");
    svg.append("text").attr("class", "panel-label").attr("x", x(0) + 8).attr("y", 12).text("model first ▶");
    svg.append("text").attr("class", "panel-label").attr("x", (m.l + w - m.r) / 2).attr("y", h - 4).attr("text-anchor", "middle").text("minutes between the two reaching 90%");
    const g = svg.selectAll("g.row").data(rows).join("g").attr("class", "row").attr("transform", (d, i) => `translate(0,${m.t + i * rh})`);
    g.append("title").text((d) => `${label(d.e)}: main race ${d.L.race.name} (${d.L.scope}). The model reached 90% ${dur(d.L.minutes)} ${d.L.minutes >= 0 ? "before" : "after"} the market; ${d.L.markets} Polymarket markets in all.`);
    g.append("text").attr("class", "row-name").attr("x", m.l).attr("y", 11).text((d) => label(d.e));
    g.append("text").attr("class", "legend-text").attr("x", narrow ? m.l : w - m.r).attr("y", narrow ? 52 : 11).attr("text-anchor", narrow ? "start" : "end").text((d) => `${d.L.markets} ${d.L.markets === 1 ? "market" : "markets"}`);
    g.append("rect").attr("x", (d) => x(Math.min(0, clip(d.L.minutes)))).attr("width", (d) => Math.max(1.5, Math.abs(x(clip(d.L.minutes)) - x(0)))).attr("y", 20).attr("height", 16)
      .attr("fill", (d) => (d.L.minutes >= 0 ? C.model : C.market));
    g.append("text").attr("class", "legend-text val").attr("y", 28).attr("dy", "0.35em")
      .attr("x", (d) => (d.L.minutes < 0 ? x(0) + 6 : x(clip(d.L.minutes)) + 6)).text((d) => `${signed(d.L.minutes)}${Math.abs(d.L.minutes) > D ? " (off the scale)" : ""}`);
    svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.t - 6).attr("y2", h - m.b).attr("stroke", "#1b1b1b");
  });
}

/* ------------------------------------------------------------------ 3. projection against the raw count */

export function drawError(S) {
  const pe = S.projection_error;
  if (!pe) return;
  $("ev-error").hidden = false;
  const th = pe.thresholds;
  const pool = pe.pooled?.main ?? {};
  const at = (c, k) => pts(pool[c]?.[k]);
  $("ev-error-lead").textContent = pool["5%"] && pool["25%"]
    ? `Early in the count the model's projection was much closer to the final result than the raw count. With 5% of the vote counted, it was on average ${at("5%", "model")} points off. The raw count was ${at("5%", "raw")} points off.`
    : "";
  $("ev-error-how").innerHTML = how(
    "Each line shows how far a guess was from the final result, in percentage points of the two-way vote. The guess is measured when that share of the vote (along the bottom) had been counted. Lower is better.",
    `${pool["25%"] ? `Example: with 25% counted, the model was ${at("25%", "model")} points off on average. The raw count was ${at("25%", "raw")} points off. ` : ""}The raw count is the running total on the official results page. It can point the wrong way early, because places do not report in random order.`,
  );
  const ids = Object.entries(pe.by_election).filter(([, e]) => e?.main).map(([id]) => id);
  const val = (id, c, k) => { const v = pe.by_election[id].main[c]?.[k]; return v == null ? null : Math.abs(v); };
  const ymax = Math.min(10, d3.max(ids, (id) => d3.max(th, (c) => d3.max(["model", "raw"], (k) => val(id, c, k)))) ?? 5);
  const pooled = (c, k) => pool[c]?.[k] ?? null;
  const root = $("chart-error");
  const key = `<span class="m">Model projection</span>, <span class="r">raw count</span>`;

  const drawAll = (box) => {
    const w = Math.max(300, box.clientWidth), h = Math.min(360, Math.max(240, w * 0.45));
    const m = { l: 40, r: 120, t: 24, b: 34 };
    const x = d3.scalePoint().domain(th).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, ymax]).nice().range([h - m.b, m.t]);
    const svg = d3.select(box).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
      .attr("aria-label", "Distance from the final share by share counted, model projection and raw count, all elections together");
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
    svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", h - 4).text("share of the vote counted");
    svg.append("text").attr("class", "panel-label").attr("x", m.l - 34).attr("y", 10).text("points from the final share");
    const line = d3.line().defined((p) => p[1] != null).x((p) => x(p[0])).y((p) => y(Math.min(ymax, p[1])));
    const g = svg.append("g");
    for (const id of ids) for (const k of ["model", "raw"]) {
      g.append("path").datum(th.map((c) => [c, val(id, c, k)])).attr("class", "ev-thin").attr("fill", "none").attr("stroke", k === "model" ? C.model : C.raw).attr("stroke-width", 1.2).attr("d", line)
        .append("title").text(`${labelOf(id)}: ${k === "model" ? "model projection" : "raw count"}`);
    }
    for (const k of ["model", "raw"]) {
      const p = th.map((c) => [c, pooled(c, k)]);
      g.append("path").datum(p).attr("fill", "none").attr("stroke", k === "model" ? C.model : C.raw).attr("stroke-width", 3).attr("d", line);
      const last = [...p].reverse().find((q) => q[1] != null);
      if (last) svg.append("text").attr("class", "end-label").attr("x", x(last[0]) + 8).attr("y", y(last[1]) + (k === "model" ? 12 : -4)).attr("fill", k === "model" ? C.model : C.raw).text(k === "model" ? "projection, average" : "raw count, average");
    }
  };

  // one small panel per election: both lines on the same scale, so the panels compare at a glance
  const drawPanels = (box) => {
    box.innerHTML = "";
    const wrap = d3.select(box).append("div").attr("class", "panels");
    for (const id of ids) {
      const cell = wrap.append("figure").attr("class", "panel");
      cell.append("figcaption").text(labelOf(id));
      const node = cell.append("div").node();
      const w = Math.max(150, node.clientWidth || 200), h = 120, m = { l: 26, r: 6, t: 6, b: 20 };
      const x = d3.scalePoint().domain(th).range([m.l + 10, w - m.r - 8]);
      const y = d3.scaleLinear().domain([0, ymax]).range([h - m.b, m.t]);
      const svg = d3.select(node).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img")
        .attr("aria-label", `${labelOf(id)}: distance from the final share, model projection and raw count`);
      svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(2)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y);
      svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(2).tickSize(0).tickPadding(4)).call((g) => g.select(".domain").remove());
      svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickValues(th.filter((c) => ["1%", "10%", "50%", "90%"].includes(c))).tickSize(0).tickPadding(5)).call((g) => g.select(".domain").remove());
      const line = d3.line().defined((p) => p[1] != null).x((p) => x(p[0])).y((p) => y(Math.min(ymax, p[1])));
      for (const k of ["raw", "model"]) svg.append("path").datum(th.map((c) => [c, val(id, c, k)])).attr("fill", "none").attr("stroke", k === "model" ? C.model : C.raw).attr("stroke-width", 2).attr("d", line);
    }
  };

  root.innerHTML = `<div class="toggle" role="group" aria-label="Chart layout"><button type="button" data-v="panels" aria-pressed="true">One panel per election</button><button type="button" data-v="all" aria-pressed="false">All together</button></div><div class="chart-box"></div><figcaption>${key}. Points from the final share, by share of the vote counted. Panels share one scale. In the combined view, thin lines are single elections and thick lines are the average.</figcaption>`;
  const box = root.querySelector(".chart-box");
  let view = "panels";
  const draw = () => (view === "all" ? drawAll(box) : drawPanels(box));
  root.querySelectorAll(".toggle button").forEach((b) => b.addEventListener("click", () => {
    view = b.dataset.v;
    root.querySelectorAll(".toggle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    draw();
  }));
  responsive(box, draw);
}

/* ------------------------------------------------------------------ 4. calibration */

export function drawCalibration(S) {
  const cal = S.calibration;
  if (!cal) return;
  $("ev-cal").hidden = false;
  const rows = S.elections.filter((e) => cal.by_election[e.id]?.coverage90 != null).map((e) => ({ ...e, ...cal.by_election[e.id] }));
  const lo = d3.least(rows, (r) => r.coverage90);
  const rel = cal.reliability;
  const byMin = rel?.by_election ? Object.entries(rel.by_election).sort((a, b) => (b[1].minutes ?? 0) - (a[1].minutes ?? 0)) : [];
  const dom = byMin[0] && rel.minutes ? ` The right-hand chart is dominated by one election: ${labelOf(byMin[0][0])} supplies ${fmtInt(byMin[0][1].minutes)} of its ${fmtInt(rel.minutes)} minutes.` : "";
  const hi = d3.greatest(rows, (r) => r.coverage90);
  $("ev-cal-lead").textContent = `The model gives a 90% band: the range it says the final result will fall in, 9 times out of 10. If the band is the right width, the final result is inside it in about 90% of the minutes. Across ${rows.length} elections the share ran from ${pct(lo.coverage90, 0)} (${lo.name}) to ${pct(hi.coverage90, 0)}.`;
  const ex = rows.find((r) => r.coverage90 < 0.99 && r.coverage90 > 0.5) ?? hi;
  const bins = (rel?.model ?? []).filter((d) => d.minutes > 0 && d.mean_p != null && d.freq != null);
  const off = d3.greatest(bins, (d) => d.minutes * Math.abs(d.freq - d.mean_p));
  const pctRange = (b) => { const [a, c] = b.split("-").map(Number); return `${Math.round(a * 100)}–${Math.round(c * 100)}%`; };
  $("ev-cal-how").innerHTML = how(
    "Left: each dot is one election. The dashed line marks 90%. A dot at the line means the band was the right width. A dot to the left means the band was too narrow: the result fell outside it too often.",
    `Example: the ${ex.name} dot is at ${pct(ex.coverage90, 0)}. In ${Math.round(100 * ex.coverage90)} of every 100 minutes, the final result was inside the band.`,
    off ? `Right: each dot groups the minutes in which the model, or the market, gave the same chance. Across is the chance given. Up is how often that side went on to win. On the dashed line, the chance given was right. Example: when the model gave ${pctRange(off.bin)} (${fmtInt(off.minutes)} minutes), that side won ${pct(off.freq, 0)} of the time. The dot sits ${off.freq > off.mean_p ? "above" : "below"} the line, so the model was ${off.freq > off.mean_p ? "too cautious" : "too confident"} there.${dom}` : "",
  );
  const covRoot = mount($("chart-coverage"), "Share of minutes, from 1% to 95% counted, with the final result inside the model's 90% band.");
  responsive(covRoot, () => {
    const w = Math.max(260, covRoot.clientWidth), rh = 36, m = { l: 4, r: 44, t: 22, b: 26 };
    const h = m.t + rows.length * rh + m.b;
    const x = d3.scaleLinear().domain([Math.min(0.6, d3.min(rows, (r) => r.coverage90)), 1]).range([m.l, w - m.r]);
    const svg = d3.select(covRoot).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img").attr("aria-label", "Share of minutes with the final result inside the 90% band, per election");
    svg.append("line").attr("x1", x(0.9)).attr("x2", x(0.9)).attr("y1", m.t - 6).attr("y2", h - m.b).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
    svg.append("text").attr("class", "legend-text").attr("x", x(0.9)).attr("y", m.t - 9).attr("text-anchor", "middle").text("90%");
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickFormat(d3.format(".0%")).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    const g = svg.selectAll("g.row").data(rows).join("g").attr("class", "row").attr("transform", (d, i) => `translate(0,${m.t + i * rh})`);
    g.append("text").attr("class", "row-name").attr("x", m.l).attr("y", 11).text((d) => d.name);
    g.append("line").attr("x1", x.range()[0]).attr("x2", (d) => x(d.coverage90)).attr("y1", 24).attr("y2", 24).attr("stroke", "#d8d5cd");
    g.append("circle").attr("cx", (d) => x(d.coverage90)).attr("cy", 24).attr("r", 4.5).attr("fill", C.model).append("title").text((d) => `${d.name}: ${pct(d.coverage90, 1)} (${d.basis})`);
    g.append("text").attr("class", "legend-text").attr("x", (d) => x(d.coverage90) + 8).attr("y", 24).attr("dy", "0.35em").text((d) => pct(d.coverage90, 0));
  });
  const relFig = $("chart-reliability");
  if (!rel?.model?.length) { relFig.hidden = true; return; }
  const relRoot = mount(relFig, `<span class="m">Model</span>, <span class="k">market price</span>. Dot size is the number of minutes. On the dashed line, the chance given was right on average.`);
  responsive(relRoot, () => {
    const w = Math.min(360, Math.max(240, relRoot.clientWidth)), h = w, m = { l: 54, r: 10, t: 10, b: 38 };
    const x = d3.scaleLinear().domain([0, 1]).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    const r = d3.scaleSqrt().domain([0, d3.max([...rel.model, ...(rel.market ?? [])], (d) => d.minutes) || 1]).range([1.5, 9]);
    const svg = d3.select(relRoot).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).attr("role", "img").attr("aria-label", "How often a side won against the chance the model or the market gave it");
    svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", "#9a968c").attr("stroke-dasharray", "3,3");
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(4).tickFormat(d3.format(".0%")).tickSize(3));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(d3.format(".0%")).tickSize(3));
    svg.append("text").attr("class", "legend-text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 3).attr("text-anchor", "middle").text("chance given");
    svg.append("text").attr("class", "legend-text").attr("transform", `translate(11,${(m.t + h - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text("how often that side won");
    for (const [list, color] of [[rel.market, C.market], [rel.model, C.model]]) {
      const p = (list ?? []).filter((d) => d.minutes > 0 && d.mean_p != null && d.freq != null);
      svg.append("path").datum(p).attr("fill", "none").attr("stroke", color).attr("opacity", 0.6).attr("d", d3.line().x((d) => x(d.mean_p)).y((d) => y(d.freq)));
      svg.append("g").selectAll("circle").data(p).join("circle").attr("cx", (d) => x(d.mean_p)).attr("cy", (d) => y(d.freq)).attr("r", (d) => r(d.minutes)).attr("fill", color).attr("fill-opacity", 0.85)
        .append("title").text((d) => `${d.bin}: chance given ${pct(d.mean_p, 0)}, that side won ${pct(d.freq, 0)} of the time (${fmtInt(d.minutes)} minutes, ${d.races} races)`);
    }
  });
}
