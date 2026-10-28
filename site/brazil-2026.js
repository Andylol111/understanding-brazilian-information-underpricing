import * as d3 from "./vendor/d3.js";
import * as Plot from "./vendor/plot.js";

const C = { market: "#1b1b1b", model: "#c4501b", raw: "#6a7f99", muted: "#5f6368", rule: "#e4e2dc" };
const load = (name) => fetch(`data/${name}.json`).then((r) => r.json());
const [nights, races, binned, catchup, robustness] = await Promise.all(
  ["nights", "races", "binned", "catchup", "robustness"].map(load),
);

/* ---------- 1. Count night timeline (D3, two aligned panels, shared crosshair) ---------- */

const NIGHT_META = {
  r2: { question: "Lula wins the presidency", from: "18:45", to: "21:30" },
  r1: { question: "Lula finishes first in round one", from: "17:00", to: "19:45" },
};
const parseT = d3.timeParse("%H:%M");
const pct = (x, d = 1) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);

function drawNight(key) {
  const root = d3.select("#night").html("");
  const { rows: raw, final } = nights[key];
  const { from, to } = NIGHT_META[key];
  const rows = raw.filter((r) => r.t >= from && r.t <= to).map((r) => ({ ...r, time: parseT(r.t) }));
  const width = Math.min(root.node().clientWidth, 980);
  const m = { l: 52, r: 96, t: 24, b: 30 };
  const h1 = 250, h2 = 190, gap = 34;
  const height = m.t + h1 + gap + h2 + m.b;
  const x = d3.scaleTime().domain(d3.extent(rows, (d) => d.time)).range([m.l, width - m.r]);
  const shares = rows.flatMap((d) => [d.raw, d.share != null && d.sd != null ? d.share - 1.645 * d.sd : null, d.share != null && d.sd != null ? d.share + 1.645 * d.sd : null]).filter((v) => v != null);
  const counted = rows.filter((d) => d.counted > 0.005);
  const yS = d3.scaleLinear().domain([Math.max(0.42, d3.min(counted, (d) => Math.min(d.raw ?? 1, d.share - 1.645 * d.sd)) - 0.005), Math.min(0.6, d3.max(counted, (d) => Math.max(d.raw ?? 0, d.share + 1.645 * d.sd)) + 0.005)]).nice().range([m.t + h1, m.t]);
  const top2 = m.t + h1 + gap;
  const yP = d3.scaleLinear().domain([0, 1]).range([top2 + h2, top2]);
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", "100%").style("display", "block");

  // grids + axes
  svg.append("g").attr("class", "grid").selectAll("line").data(yS.ticks(5)).join("line")
    .attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS).attr("y2", yS);
  svg.append("g").attr("class", "grid").selectAll("line").data([0, 0.25, 0.5, 0.75, 1]).join("line")
    .attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP).attr("y2", yP);
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yS).ticks(5).tickFormat((v) => `${Math.round(v * 1000) / 10}%`).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yP).tickValues([0, 0.5, 0.9, 1]).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top2 + h2})`).call(d3.axisBottom(x).ticks(d3.timeMinute.every(30)).tickFormat(d3.timeFormat("%H:%M")).tickSizeOuter(0));
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", m.t - 10).text("Lula's share of the two-candidate vote");
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", top2 - 12).text(`Probability: ${NIGHT_META[key].question}`);

  // shade: raw tally on the wrong side of 50% while the eventual winner was the other way
  const wrong = rows.filter((d) => d.counted > 0.01 && d.raw != null && (d.raw - 0.5) * (final - 0.5) < 0);
  if (wrong.length) {
    const [a, b] = d3.extent(wrong, (d) => d.time);
    svg.append("rect").attr("x", x(a)).attr("width", Math.max(1, x(b) - x(a))).attr("y", m.t).attr("height", top2 + h2 - m.t).attr("fill", C.raw).attr("opacity", 0.08);
    svg.append("text").attr("x", x(a) + 6).attr("y", yP(0.2)).attr("font-size", 12).attr("fill", C.raw)
      .text(final > 0.5 ? "raw tally shows Lula behind" : "raw tally shows Lula ahead");
  }

  // top panel
  svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS(0.5)).attr("y2", yS(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS(final)).attr("y2", yS(final)).attr("stroke", C.model).attr("stroke-dasharray", "5,4").attr("opacity", 0.7);
  svg.append("text").attr("x", m.l + 6).attr("y", yS(final) - 6).attr("font-size", 12).attr("fill", C.model).text(`final ${pct(final)}`);
  const live = rows.filter((d) => d.counted > 0.0005);
  svg.append("path").datum(live.filter((d) => d.sd != null)).attr("fill", C.model).attr("opacity", 0.14)
    .attr("d", d3.area().x((d) => x(d.time)).y0((d) => yS(Math.max(yS.domain()[0], d.share - 1.645 * d.sd))).y1((d) => yS(Math.min(yS.domain()[1], d.share + 1.645 * d.sd))).curve(d3.curveStepAfter));
  svg.append("path").datum(live.filter((d) => d.raw != null)).attr("fill", "none").attr("stroke", C.raw).attr("stroke-width", 2)
    .attr("d", d3.line().x((d) => x(d.time)).y((d) => yS(Math.max(yS.domain()[0], Math.min(yS.domain()[1], d.raw)))));
  svg.append("path").datum(live).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2.2)
    .attr("d", d3.line().x((d) => x(d.time)).y((d) => yS(Math.max(yS.domain()[0], Math.min(yS.domain()[1], d.share)))).curve(d3.curveStepAfter));
  const lastLive = live[live.length - 1];
  svg.append("text").attr("x", width - m.r + 6).attr("y", yS(Math.max(yS.domain()[0], lastLive.raw)) + 14).attr("font-size", 12).attr("fill", C.raw).text("raw tally");
  svg.append("text").attr("x", width - m.r + 6).attr("y", yS(lastLive.share) - 8).attr("font-size", 12).attr("fill", C.model).text("model");

  // 1% counted: the earliest point the model is allowed to trade
  const gate = rows.find((d) => d.counted >= 0.01);
  if (gate) {
    svg.append("line").attr("x1", x(gate.time)).attr("x2", x(gate.time)).attr("y1", m.t).attr("y2", top2 + h2).attr("stroke", C.muted).attr("stroke-dasharray", "1,3");
    svg.append("text").attr("x", x(gate.time) + 5).attr("y", yP(0.03)).attr("font-size", 11.5).attr("fill", C.muted).text(`1% counted (${gate.t})`);
  }

  // bottom panel
  svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP(0.9)).attr("y2", yP(0.9)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP(0.1)).attr("y2", yP(0.1)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  svg.append("path").datum(rows.filter((d) => d.P != null && d.counted > 0.0005)).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2.2)
    .attr("d", d3.line().x((d) => x(d.time)).y((d) => yP(d.P)).curve(d3.curveStepAfter));
  svg.append("path").datum(rows.filter((d) => d.price != null)).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.8)
    .attr("d", d3.line().x((d) => x(d.time)).y((d) => yP(d.price)).curve(d3.curveStepAfter));
  const last = rows[rows.length - 1];
  svg.append("text").attr("x", width - m.r + 6).attr("y", yP(last.price) + (last.price > 0.5 ? 14 : -8)).attr("font-size", 12).attr("fill", C.market).text("market");
  svg.append("text").attr("x", width - m.r + 6).attr("y", yP(last.P) + (last.P > 0.5 ? -4 : 22)).attr("font-size", 12).attr("fill", C.model).text("model");

  // crosshair
  const readout = d3.select("#night-readout");
  const rule = svg.append("line").attr("y1", m.t).attr("y2", top2 + h2).attr("stroke", C.ink || "#1b1b1b").attr("opacity", 0).attr("stroke-width", 1);
  const bisect = d3.bisector((d) => d.time).center;
  const show = (d) => {
    rule.attr("x1", x(d.time)).attr("x2", x(d.time)).attr("opacity", 0.35);
    readout.html(`<b>${d.t}</b> · ${pct(d.counted)} of sections counted · raw tally <b style="color:${C.raw}">${pct(d.raw)}</b> · model share <b style="color:${C.model}">${pct(d.share)}</b> · market <b>${d.price?.toFixed(2) ?? "–"}</b> · model <b style="color:${C.model}">${d.P?.toFixed(2) ?? "–"}</b>`);
  };
  const idle = () => {
    rule.attr("opacity", 0);
    readout.html("Move across the chart to read any minute.");
  };
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", width - m.l - m.r).attr("height", top2 + h2 - m.t).attr("fill", "transparent")
    .on("pointermove", (e) => show(rows[bisect(rows, x.invert(d3.pointer(e)[0]))]))
    .on("pointerleave", idle);
  idle();
}

let current = "r2";
document.querySelectorAll("#night-toggle button").forEach((b) => b.addEventListener("click", () => {
  current = b.dataset.night;
  document.querySelectorAll("#night-toggle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  drawNight(current);
}));
drawNight(current);
let rw;
addEventListener("resize", () => { clearTimeout(rw); rw = setTimeout(() => drawNight(current), 150); });

/* ---------- 2. Binned gap vs next-30-min move ---------- */

const POOL_LABEL = { "2026 first round (in-sample)": "First round", "2026 runoff night (pre-specified)": "Runoff night" };
const POOL_COLOR = { "2026 first round (in-sample)": C.raw, "2026 runoff night (pre-specified)": C.model };

function binnedChart(label) {
  const b = binned[label];
  const lim = 0.45;
  return Plot.plot({
    width: 460, height: 340, marginLeft: 48, marginBottom: 40,
    style: { fontFamily: "var(--sans)", fontSize: "12px", color: C.muted },
    x: { domain: [-0.3, lim], label: "gap now: model − market →", tickFormat: "+.1f" },
    y: { domain: [-0.3, lim], label: "↑ market's move over the next 30 min", tickFormat: "+.1f" },
    marks: [
      Plot.gridX({ stroke: "#ecebe6", strokeOpacity: 1 }), Plot.gridY({ stroke: "#ecebe6", strokeOpacity: 1 }),
      Plot.ruleX([0], { stroke: "#c9c6bd" }), Plot.ruleY([0], { stroke: "#c9c6bd" }),
      Plot.line([[-0.3, -0.3], [lim, lim]], { stroke: "#9a968c", strokeDasharray: "3,4" }),
      Plot.text([[0.3, 0.37]], { text: ["closes the whole gap"], fill: "#9a968c", textAnchor: "end", rotate: -45, fontSize: 11 }),
      Plot.line([[-0.3, -0.3 * b.slope], [lim, lim * b.slope]], { stroke: POOL_COLOR[label], strokeWidth: 1.5, strokeOpacity: 0.6 }),
      Plot.dot(b.bins, { x: "gap", y: "move", r: 6, fill: POOL_COLOR[label], fillOpacity: 0.9, stroke: "white",
        title: (d) => `gap ${d.gap.toFixed(2)} → move ${d.move.toFixed(2)} (${d.n} market-minutes)` }),
    ],
    title: undefined,
  });
}
for (const [id, label] of [["binned-r1", "2026 first round (in-sample)"], ["binned-r2", "2026 runoff night (pre-specified)"]]) {
  const b = binned[label];
  const el = document.getElementById(id);
  el.innerHTML = `<p class="chart-title">${POOL_LABEL[label]}: slope ${b.slope.toFixed(2)} <span style="font-weight:400;color:${C.muted}">[${b.lo.toFixed(2)}, ${b.hi.toFixed(2)}]</span></p><p class="chart-sub">${b.markets} markets, ${b.n.toLocaleString()} market-minutes, in ten equal-size groups by gap</p>`;
  el.append(binnedChart(label));
}

/* ---------- 3. Catch-up curve ---------- */

const cu = Object.entries(catchup).flatMap(([label, pts]) => pts.filter((p) => p.h <= 60).map((p) => ({ ...p, pool: POOL_LABEL[label], color: POOL_COLOR[label] })));
document.getElementById("catchup").append(Plot.plot({
  width: 980, height: 360, marginLeft: 52, marginRight: 110, marginBottom: 40,
  style: { fontFamily: "var(--sans)", fontSize: "12px", color: C.muted },
  x: { domain: [0, 62], label: "minutes after the gap appears →", ticks: [0, 5, 10, 15, 20, 30, 45, 60] },
  y: { domain: [0, 1.05], label: "↑ share of the gap the price has closed", tickFormat: "%" },
  marks: [
    Plot.gridY({ stroke: "#ecebe6", strokeOpacity: 1, ticks: [0.25, 0.5, 0.75] }),
    Plot.ruleY([1], { stroke: "#9a968c", strokeDasharray: "3,4" }),
    Plot.text([[61, 1]], { text: ["fully priced"], fill: "#9a968c", textAnchor: "start", dx: 6 }),
    Plot.ruleY([0], { stroke: "#c9c6bd" }),
    Plot.areaY(cu, { x: "h", y1: "lo", y2: (d) => Math.min(1.05, d.hi), z: "pool", fill: "color", fillOpacity: 0.1, curve: "monotone-x" }),
    Plot.line(cu, { x: "h", y: "s", z: "pool", stroke: "color", strokeWidth: 2.2, curve: "monotone-x" }),
    Plot.dot(cu, { x: "h", y: "s", fill: "color", r: 3, title: (d) => `${d.pool}, ${d.h} min: ${Math.round(100 * d.s)}% [${Math.round(100 * d.lo)}–${Math.round(100 * d.hi)}%]` }),
    Plot.text(cu.filter((d) => d.h === 60), { x: "h", y: "s", text: "pool", fill: "color", textAnchor: "start", dx: 8, fontWeight: 600 }),
  ],
}));

/* ---------- 4. Robustness forest ---------- */

const CHECK_ORDER = ["baseline", "move starts 1 min later", "move starts 2 min later", "move starts 5 min later", "gap from p(t-1)", "gap from p(t-5)", "gap + reversal control", "gap + 0.5 placebo", "placebo: model replaced by 0.5", "placebo: own 5-min reversal"];
const CHECK_NAME = {
  "baseline": "Baseline", "move starts 1 min later": "Move measured from 1 min later", "move starts 2 min later": "Move measured from 2 min later",
  "move starts 5 min later": "Move measured from 5 min later", "gap from p(t-1)": "Gap from the price 1 min earlier", "gap from p(t-5)": "Gap from the price 5 min earlier",
  "gap + reversal control": "Controlling for the market's own last move", "gap + 0.5 placebo": "Controlling for distance from 50¢",
  "placebo: model replaced by 0.5": "Placebo: model replaced by a coin flip", "placebo: own 5-min reversal": "Placebo: the market's own last move",
};
const forest = Object.entries(robustness).flatMap(([label, rows]) => rows.map((r) => ({ ...r, pool: POOL_LABEL[label], color: POOL_COLOR[label], name: CHECK_NAME[r.check] })));
document.getElementById("robust").append(Plot.plot({
  width: 980, height: 420, marginLeft: 300, marginBottom: 40,
  style: { fontFamily: "var(--sans)", fontSize: "12.5px", color: C.muted },
  x: { domain: [-0.65, 0.85], label: "slope of the next 30-min move on the predictor →", ticks: [-0.5, -0.25, 0, 0.25, 0.5, 0.75] },
  y: { domain: CHECK_ORDER.map((c) => CHECK_NAME[c]), label: null },
  marks: [
    Plot.gridX({ stroke: "#ecebe6", strokeOpacity: 1 }),
    Plot.ruleX([0], { stroke: "#9a968c" }),
    ...[["First round", -5], ["Runoff night", 5]].flatMap(([pool, dy]) => {
      const rows = forest.filter((d) => d.pool === pool);
      return [
        Plot.ruleY(rows, { y: "name", x1: "lo", x2: "hi", stroke: "color", strokeWidth: 2, dy }),
        Plot.dot(rows, { y: "name", x: "est", fill: "color", r: 4.5, dy, title: (d) => `${d.pool}: ${d.est.toFixed(2)} [${d.lo.toFixed(2)}, ${d.hi.toFixed(2)}]` }),
      ];
    }),
  ],
}));

/* ---------- 5. Race-by-race lead ---------- */

const raceRows = races.map((r) => ({ ...r, label: r.race.replace(", runoff 2026", "").replace(" 2026", "") }));
document.getElementById("races").append(Plot.plot({
  width: 980, height: 40 + 32 * raceRows.length, marginLeft: 260, marginRight: 60, marginBottom: 40,
  style: { fontFamily: "var(--sans)", fontSize: "12.5px", color: C.muted },
  x: { label: "← market (or raw tally) sure first · model sure first, minutes →", domain: [-185, 250] },
  y: { domain: raceRows.map((r) => r.race), label: null, tickFormat: (r) => r.replace(", runoff 2026", " (runoff)").replace("Governor, ", "Gov. ") },
  marks: [
    Plot.gridX({ stroke: "#ecebe6", strokeOpacity: 1 }),
    Plot.barX(raceRows, { y: "race", x: "lead", fill: (d) => (d.lead >= 0 ? C.model : C.market), fillOpacity: (d) => (d.vs === "market" ? 0.9 : 0.35), insetTop: 7, insetBottom: 7 }),
    Plot.ruleX([0], { stroke: C.market }),
    Plot.text(raceRows.filter((d) => d.lead >= 0), { y: "race", x: "lead", text: (d) => (d.lead === 0 ? "tie" : `+${d.lead}`), dx: 6, textAnchor: "start", fill: C.market }),
    Plot.text(raceRows.filter((d) => d.lead < 0), { y: "race", x: "lead", text: (d) => `${d.lead}`, dx: -6, textAnchor: "end", fill: C.market }),
    Plot.text(raceRows, { y: "race", frameAnchor: "right", text: (d) => (d.vs === "market" ? "vs market" : "vs raw tally"), dx: 56, fill: "#9a968c", fontSize: 11 }),
  ],
}));
