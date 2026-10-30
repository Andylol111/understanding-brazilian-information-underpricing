// Race timeline: one chart on the dashboard's shared time axis, in one of two views.
//  - "margin": the model's margin (side a minus side b, points) with its 90% band, the raw
//    count's margin once counting starts, the polls (US) and the market-implied margin (the
//    margin at which the model's uncertainty gives the market's price);
//  - "chance": the model's probability that side a wins against the market prices.
// A solid cursor marks the scrubber time, a dashed one the hover. Labels sit at the line ends.
import * as d3 from "./vendor/d3.js";
import { C, clock, probit } from "./common.js";

const POLLS = "#8d8a82";
// method hovers for the model's line (US)
const REG = "v3: poll average prior + county count (registered)";
const EST = "Estimate: 2024 county results shifted to the poll average";

// Margin of a row (points, side a minus side b) and its sd: US rows carry margins (with polls
// where the race has a poll model); other two-sided races derive them from the share.
const mm = (d) => d.margin_v2 ?? d.margin_model ?? (d.share != null ? (2 * d.share - 1) * 100 : null);
const msd = (d) => (d.margin_v2 != null && d.sd_v2 != null ? 200 * d.sd_v2 : d.margin_model_sd ?? (d.sd != null ? 200 * d.sd : null));
const implied = (p, sd) => (p != null && sd != null && p > 0 && p < 1 ? probit(p) * sd : null);
const mkt = (d) => d.margin_market ?? implied(d.pm, msd(d));
const mktK = (d) => d.margin_market_kalshi ?? implied(d.kalshi, msd(d));
const rawM = (d) => (d.raw != null && d.counted > 0 ? (2 * d.raw - 1) * 100 : null);

// Which views a race's rows support (the margin view needs a margin: the model's, the polls' or a
// market-implied one).
export const timelineViews = (rows) => (rows.some((d) => mm(d) != null || d.margin_polls != null || mkt(d) != null || d.margin_estimate != null) ? ["margin", "chance"] : ["chance"]);

// view: "margin" | "chance"; us: margins read "D+3" / "R+3" (else "+3" for side a, "−3" for b).
export function drawTimeline(root, { race, rows, domain, tz, wall, height, view = "chance", us = false, countPanel = true, onPreview, onSet }) {
  root.innerHTML = "";
  if (!rows.length) {
    root.innerHTML = `<p class="note">No count or price data for this race yet.</p>`;
    return { setCursor() {} };
  }
  const width = Math.max(280, root.clientWidth);
  const narrow = width < 520;
  const two = rows.some((d) => d.P_v2 != null);
  const m = { l: narrow ? 38 : 46, r: narrow ? 104 : 116, t: 8, b: 22 };
  const h = height - m.t - m.b;
  const x = d3.scaleUtc().domain(domain.map(wall)).range([m.l, width - m.r]);
  const X = (d) => x(wall(d.time));
  const vis = rows.filter((d) => d.time >= domain[0] && d.time <= domain[1]);
  const lastF = (f) => [...vis].reverse().find((d) => f(d) != null);

  const svg = d3.select(root).append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height).style("display", "block");
  const clipId = `clip-${Math.random().toString(36).slice(2)}`;
  svg.append("clipPath").attr("id", clipId).append("rect").attr("x", m.l).attr("y", 0).attr("width", width - m.l - m.r).attr("height", height);
  const g = svg.append("g").attr("clip-path", `url(#${clipId})`);
  const hline = (y, stroke = "#bdb9ae") => g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y).attr("y2", y).attr("stroke", stroke).attr("stroke-dasharray", "2,3");
  const path = (stroke, w, f, dash) => g.append("path").datum(vis).attr("fill", "none").attr("stroke", stroke).attr("stroke-width", w).attr("stroke-dasharray", dash ?? null)
    .attr("d", d3.line().defined((d) => f(d) != null).x(X).y((d) => Y(f(d))).curve(d3.curveStepAfter));
  let Y;

  if (view === "margin") {
    const lo = (d) => (mm(d) != null && msd(d) != null ? mm(d) - 1.645 * msd(d) : null);
    const hi = (d) => (mm(d) != null && msd(d) != null ? mm(d) + 1.645 * msd(d) : null);
    const counting = vis.filter((d) => d.counted > 0.005);
    const span = counting.length ? counting : vis;
    const est = lastF((d) => d.margin_estimate ?? null); // a race without a live model: the county estimate now
    const eLo = est?.margin_estimate_sd != null ? est.margin_estimate - 1.645 * est.margin_estimate_sd : null, eHi = eLo != null ? est.margin_estimate + 1.645 * est.margin_estimate_sd : null;
    const vals = [...span.flatMap((d) => [mm(d), lo(d), hi(d), d.margin_polls, mkt(d), mktK(d), rawM(d)]), est?.margin_estimate, eLo, eHi].filter((v) => v != null && Number.isFinite(v));
    const [a, b] = vals.length ? d3.extent([...vals, 0]) : [-10, 10];
    const yM = d3.scaleLinear().domain([a - 1, b + 1]).nice().range([m.t + h, m.t]);
    const [d0, d1] = yM.domain();
    Y = (v) => yM(Math.max(d0, Math.min(d1, v)));
    const fmt = (v) => (v === 0 ? "0" : us ? `${v > 0 ? "D" : "R"}+${Math.abs(v)}` : `${v > 0 ? "+" : "−"}${Math.abs(v)}`);
    svg.insert("g", "g").attr("class", "grid").selectAll("line").data(yM.ticks(4)).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yM).attr("y2", yM);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yM).ticks(4).tickFormat(fmt).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
    hline(yM(0));
    g.append("path").datum(vis.filter((d) => lo(d) != null)).attr("fill", C.model).attr("opacity", 0.14)
      .attr("d", d3.area().x(X).y0((d) => Y(lo(d))).y1((d) => Y(hi(d))).curve(d3.curveStepAfter));
    path(POLLS, 1.4, (d) => d.margin_polls ?? null, "4,3");
    g.append("path").datum(vis.filter((d) => rawM(d) != null)).attr("fill", "none").attr("stroke", C.raw).attr("stroke-width", 2).attr("d", d3.line().x(X).y((d) => Y(rawM(d))));
    path(C.kalshi, 1.2, mktK);
    path(C.market, 1.6, mkt);
    path(C.model, 2, mm);
    if (est) {
      if (eLo != null) g.append("line").attr("x1", X(est)).attr("x2", X(est)).attr("y1", Y(eLo)).attr("y2", Y(eHi)).attr("stroke", C.model).attr("stroke-width", 1.5).attr("opacity", 0.6);
      g.append("circle").attr("cx", X(est)).attr("cy", Y(est.margin_estimate)).attr("r", 4).attr("fill", C.model).attr("stroke", "#fff");
    }
    // the final result, once fully counted
    const last = rows[rows.length - 1];
    const final = countPanel && last.counted >= 0.999 && last.raw != null ? (2 * last.raw - 1) * 100 : null;
    if (final != null) g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", Y(final)).attr("y2", Y(final)).attr("stroke", C.model).attr("stroke-dasharray", "5,4").attr("opacity", 0.6);
    endLabels(svg, width - m.r + 5, [
      [lastF(mm), (d) => Y(mm(d)), `model${two ? ", polls" : ""}`, C.model, us ? REG : ""],
      [lastF((d) => d.margin_polls ?? null), (d) => Y(d.margin_polls), "polls", POLLS],
      [est, (d) => Y(d.margin_estimate), "model", C.model, EST],
      [lastF(mkt), (d) => Y(mkt(d)), "market-implied", C.market],
      [lastF(mktK), (d) => Y(mktK(d)), "Kalshi-implied", C.kalshi],
      [lastF(rawM), (d) => Y(rawM(d)), "raw count", C.raw],
      [final != null ? {} : null, () => Y(final), `final ${fmt(Math.round(final * 10) / 10)}`, C.model],
    ]);
  } else {
    const yP = d3.scaleLinear().domain([0, 1]).range([m.t + h, m.t]);
    Y = yP;
    svg.insert("g", "g").attr("class", "grid").selectAll("line").data([0, 0.5, 1]).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP).attr("y2", yP);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yP).tickValues([0, 0.5, 0.9]).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
    for (const v of [0.1, 0.9]) hline(yP(v));
    const main = two ? (d) => d.P_v2 ?? null : (d) => d.P ?? null;
    if (two) path(C.model, 1, (d) => d.P ?? null, "3,2").attr("opacity", 0.75);
    path(C.model, 2, main);
    path(C.kalshi, 1.5, (d) => d.kalshi ?? null);
    path(C.market, 1.6, (d) => d.pm ?? null);
    const estP = lastF((d) => d.P_estimate ?? null);
    if (estP) g.append("circle").attr("cx", X(estP)).attr("cy", yP(estP.P_estimate)).attr("r", 4).attr("fill", C.model).attr("stroke", "#fff");
    endLabels(svg, width - m.r + 5, [
      [lastF(main), (d) => yP(main(d)), `model${two ? ", polls" : ""}`, C.model, us ? REG : ""],
      [two ? lastF((d) => d.P ?? null) : null, (d) => yP(d.P), "without polls", C.model],
      [lastF((d) => d.pm ?? null), (d) => yP(d.pm), "Polymarket", C.market],
      [lastF((d) => d.kalshi ?? null), (d) => yP(d.kalshi), "Kalshi", C.kalshi],
      [estP, (d) => yP(d.P_estimate), "model", C.model, EST],
    ]);
  }

  const long = domain[1] - domain[0] > 24 * 3600000; // past 24 h, ticks carry the day ("12 Apr 22:00")
  const days = domain[1] - domain[0] > 72 * 3600000; // past 3 days, the date only ("12 Apr")
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${m.t + h})`).call(d3.axisBottom(x).ticks(Math.max(2, Math.floor((width - m.l - m.r) / (days ? 60 : long ? 100 : 70)))).tickFormat(d3.utcFormat(days ? "%-d %b" : long ? "%-d %b %H:%M" : "%H:%M")).tickSizeOuter(0));
  const gate = countPanel ? vis.find((d) => d.counted >= 0.01) : null;
  if (gate) {
    g.append("line").attr("x1", X(gate)).attr("x2", X(gate)).attr("y1", m.t).attr("y2", m.t + h).attr("stroke", C.muted).attr("stroke-dasharray", "1,3");
    g.append("text").attr("x", X(gate) + 4).attr("y", m.t + h - 4).attr("class", "chart-note").attr("fill", C.muted).text(`1% counted ${clock(gate.t, tz)}`);
  }

  const preview = svg.append("line").attr("class", "cursor-preview").attr("y1", m.t).attr("y2", m.t + h).attr("opacity", 0);
  const cursor = svg.append("line").attr("class", "cursor").attr("y1", m.t - 4).attr("y2", m.t + h);
  const off = +wall(0);
  const tAt = (ev) => Math.max(domain[0], Math.min(domain[1], Math.round((+x.invert(d3.pointer(ev)[0]) - off) / 60000) * 60000));
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", width - m.l - m.r).attr("height", h).attr("fill", "transparent").style("cursor", "crosshair")
    .on("pointermove", (ev) => onPreview(tAt(ev)))
    .on("pointerleave", () => onPreview(null))
    .on("click", (ev) => onSet(tAt(ev)));

  return {
    setCursor(t, previewT) {
      const cx = x(wall(t));
      cursor.attr("x1", cx).attr("x2", cx);
      if (previewT == null) preview.attr("opacity", 0);
      else { const px = x(wall(previewT)); preview.attr("x1", px).attr("x2", px).attr("opacity", 1); }
    },
  };
}

function endLabels(svg, x, items) {
  const pts = items.filter(([d]) => d).map(([d, y, text, color, title = ""]) => ({ y: y(d), text, color, title }));
  pts.sort((a, b) => a.y - b.y);
  for (let i = 1; i < pts.length; i++) pts[i].y = Math.max(pts[i].y, pts[i - 1].y + 12);
  svg.selectAll(null).data(pts).join("text").attr("x", x).attr("y", (d) => d.y + 4).attr("class", "end-label").attr("fill", (d) => d.color).text((d) => d.text)
    .filter((d) => d.title).append("title").text((d) => d.title);
}


// Turnout view (US modelled races): each reported county (95% or more counted) as a dot, its
// turnout surprise log(counted / expected) against its low-propensity index (higher: a larger
// expected midterm drop-off), sized by expected votes, with the weighted least-squares line.
// pts: [{ x, y, w, name }]; before any county reports, `pre` (HTML) is shown instead.
export function drawTurnout(root, { pts, height, slope = null, corr = null, pre = null, sentence = null }) {
  root.innerHTML = "";
  if (!pts.length) { root.innerHTML = `<p class="note tl-turnout">${pre ?? "Turnout fills in as counties report."}</p>`; return { setCursor() {} }; }
  height -= sentence ? 20 : 0; // the sentence sits under the chart, inside the same height
  const width = Math.max(280, root.clientWidth), m = { l: 46, r: 16, t: 22, b: 30 };
  const x = d3.scaleLinear().domain(d3.extent(pts, (d) => d.x)).nice().range([m.l, width - m.r]);
  const yx = d3.extent(pts, (d) => d.y), ya = Math.max(0.05, Math.abs(yx[0]), Math.abs(yx[1]));
  const y = d3.scaleLinear().domain([-ya, ya]).nice().range([height - m.b, m.t]);
  const r = d3.scaleSqrt().domain([0, d3.max(pts, (d) => d.w) || 1]).range([1.5, 9]);
  const svg = d3.select(root).append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height).style("display", "block");
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat((v) => `${v > 0 ? "+" : ""}${Math.round((Math.exp(v) - 1) * 100)}%`).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(5).tickSizeOuter(0));
  svg.append("text").attr("class", "legend-text").attr("x", width - m.r).attr("y", height - 2).attr("text-anchor", "end").text("low-propensity index (higher: bigger expected drop-off) →");
  svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  svg.append("g").selectAll("circle").data([...pts].sort((a, b) => b.w - a.w)).join("circle").attr("cx", (d) => x(d.x)).attr("cy", (d) => y(Math.max(-ya, Math.min(ya, d.y)))).attr("r", (d) => r(d.w))
    .attr("fill", C.raw).attr("fill-opacity", 0.45).attr("stroke", "#fff").attr("stroke-width", 0.5)
    .append("title").text((d) => `${d.name}: turnout ${d.y >= 0 ? "+" : ""}${((Math.exp(d.y) - 1) * 100).toFixed(1)}% vs expected; index ${d.x.toFixed(2)}`);
  // weighted least squares through the dots (the slope is the file's where it has one)
  const W = d3.sum(pts, (d) => d.w) || 1, mx = d3.sum(pts, (d) => d.w * d.x) / W, my = d3.sum(pts, (d) => d.w * d.y) / W;
  const sxx = d3.sum(pts, (d) => d.w * (d.x - mx) ** 2), sxy = d3.sum(pts, (d) => d.w * (d.x - mx) * (d.y - my));
  const b = slope ?? (sxx > 0 ? sxy / sxx : 0);
  const [x0, x1] = x.domain();
  svg.append("line").attr("x1", x(x0)).attr("x2", x(x1)).attr("y1", y(my + b * (x0 - mx))).attr("y2", y(my + b * (x1 - mx))).attr("stroke", C.model).attr("stroke-width", 2);
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", 12).text(`Turnout vs expected, reported counties (${pts.length})${corr != null ? ` · correlation ${corr.toFixed(2)}` : ""}`);
  if (sentence) d3.select(root).append("p").attr("class", "tl-turnout").text(sentence);
  return { setCursor() {} };
}