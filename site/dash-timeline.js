// Race timeline: one chart on the dashboard's shared time axis, in one of two views.
//  - "margin": the model's margin (side a minus side b, points) with its 90% band, the raw
//    count's margin once counting starts, the polls (US) and the market-implied margin (the
//    margin at which the model's uncertainty gives the market's price);
//  - "chance": the model's probability that side a wins against the market prices.
// A solid cursor marks the scrubber time, a dashed one the hover. Labels sit at the line ends.
import * as d3 from "./vendor/d3.js";
import { C, clock, probit } from "./common.js";

const POLLS = "#8d8a82";

// Margin of a row (points, side a minus side b) and its sd: US rows carry margins (with polls
// where the race has a poll model); other two-sided races derive them from the share.
const mm = (d) => d.margin_v2 ?? d.margin_model ?? (d.share != null ? (2 * d.share - 1) * 100 : null);
const msd = (d) => (d.margin_v2 != null && d.sd_v2 != null ? 200 * d.sd_v2 : d.margin_model_sd ?? (d.sd != null ? 200 * d.sd : null));
const implied = (p, sd) => (p != null && sd != null && p > 0 && p < 1 ? probit(p) * sd : null);
const mkt = (d) => d.margin_market ?? implied(d.pm, msd(d));
const mktK = (d) => d.margin_market_kalshi ?? implied(d.kalshi, msd(d));
const rawM = (d) => (d.raw != null && d.counted > 0 ? (2 * d.raw - 1) * 100 : null);

// Which views a race's rows support (the margin view needs a model share or margin).
export const timelineViews = (rows) => (rows.some((d) => mm(d) != null) ? ["margin", "chance"] : ["chance"]);

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
    const vals = span.flatMap((d) => [mm(d), lo(d), hi(d), d.margin_polls, mkt(d), mktK(d), rawM(d)]).filter((v) => v != null && Number.isFinite(v));
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
    // the final result, once fully counted
    const last = rows[rows.length - 1];
    const final = countPanel && last.counted >= 0.999 && last.raw != null ? (2 * last.raw - 1) * 100 : null;
    if (final != null) g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", Y(final)).attr("y2", Y(final)).attr("stroke", C.model).attr("stroke-dasharray", "5,4").attr("opacity", 0.6);
    endLabels(svg, width - m.r + 5, [
      [lastF(mm), (d) => Y(mm(d)), two ? "model, polls" : "model", C.model],
      [lastF((d) => d.margin_polls ?? null), (d) => Y(d.margin_polls), "polls", POLLS],
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
    endLabels(svg, width - m.r + 5, [
      [lastF(main), (d) => yP(main(d)), two ? "model, polls" : "model", C.model],
      [two ? lastF((d) => d.P ?? null) : null, (d) => yP(d.P), "without polls", C.model],
      [lastF((d) => d.pm ?? null), (d) => yP(d.pm), "Polymarket", C.market],
      [lastF((d) => d.kalshi ?? null), (d) => yP(d.kalshi), "Kalshi", C.kalshi],
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
  const pts = items.filter(([d]) => d).map(([d, y, text, color]) => ({ y: y(d), text, color }));
  pts.sort((a, b) => a.y - b.y);
  for (let i = 1; i < pts.length; i++) pts[i].y = Math.max(pts[i].y, pts[i - 1].y + 12);
  svg.selectAll(null).data(pts).join("text").attr("x", x).attr("y", (d) => d.y + 4).attr("class", "end-label").attr("fill", (d) => d.color).text((d) => d.text);
}
