// Race timeline: raw count vs model projection (top), model P vs market prices (bottom), on the
// dashboard's shared time axis. A solid cursor marks the scrubber time, a dashed one the hover.
// Without a published count (meta.count_timeline false) only the probability panel is drawn.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { C, pct, clock } from "./common.js";

export function drawTimeline(root, { race, rows, domain, tz, wall, height, countPanel = true, onPreview, onSet }) {
  root.innerHTML = "";
  if (!rows.length) {
    root.innerHTML = `<p class="note">No count or price data for this race yet.</p>`;
    return { setCursor() {} };
  }
  const width = Math.max(280, root.clientWidth);
  const narrow = width < 520;
  const m = { l: narrow ? 38 : 46, r: narrow ? 74 : 84, t: 18, b: 22 };
  const gap = countPanel ? 26 : 0;
  const inner = height - m.t - m.b - gap;
  const h1 = countPanel ? Math.round(inner * 0.55) : 0, h2 = inner - h1;
  const top2 = m.t + h1 + gap;
  const x = d3.scaleUtc().domain(domain.map(wall)).range([m.l, width - m.r]);
  const X = (d) => x(wall(d.time));
  const vis = rows.filter((d) => d.time >= domain[0] && d.time <= domain[1]);
  const yP = d3.scaleLinear().domain([0, 1]).range([top2 + h2, top2]);
  const lastOf = (k) => [...vis].reverse().find((d) => d[k] != null);

  const svg = d3.select(root).append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height).style("display", "block");
  const clipId = `clip-${Math.random().toString(36).slice(2)}`;
  svg.append("clipPath").attr("id", clipId).append("rect").attr("x", m.l).attr("y", 0).attr("width", width - m.l - m.r).attr("height", height);
  const g = svg.append("g").attr("clip-path", `url(#${clipId})`);
  const last = rows[rows.length - 1];
  const final = countPanel && last.counted >= 0.999 && last.raw != null ? last.raw : null;

  if (countPanel) {
    const lo = (d) => (d.share != null && d.sd != null ? d.share - 1.645 * d.sd : null);
    const hi = (d) => (d.share != null && d.sd != null ? d.share + 1.645 * d.sd : null);
    const counted = vis.filter((d) => d.counted > 0.005);
    const vals = (counted.length ? counted : vis).flatMap((d) => [d.raw, d.share, lo(d), hi(d)]).filter((v) => v != null);
    let [a, b] = vals.length ? d3.extent(vals) : [0.4, 0.6];
    if (b - a < 0.04) { const c = (a + b) / 2; a = c - 0.02; b = c + 0.02; }
    if (0.5 > a - 0.03 && 0.5 < b + 0.03) { a = Math.min(a, 0.495); b = Math.max(b, 0.505); }
    const yS = d3.scaleLinear().domain([Math.max(0, a - 0.005), Math.min(1, b + 0.005)]).nice().range([m.t + h1, m.t]);
    const [d0, d1] = yS.domain();
    const cS = (v) => yS(Math.max(d0, Math.min(d1, v)));
    svg.insert("g", "g").attr("class", "grid").selectAll("line").data(yS.ticks(4)).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS).attr("y2", yS);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yS).ticks(4).tickFormat((v) => `${Math.round(v * 1000) / 10}%`).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
    svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", m.t - 6).text(`${race.a.name}'s share of the two-candidate vote`);
    if (final != null) {
      const wrong = vis.filter((d) => d.counted > 0.01 && d.raw != null && (d.raw - 0.5) * (final - 0.5) < 0);
      if (wrong.length) {
        const [wa, wb] = d3.extent(wrong, X);
        g.append("rect").attr("x", wa).attr("width", Math.max(1, wb - wa)).attr("y", m.t).attr("height", top2 + h2 - m.t).attr("fill", C.raw).attr("opacity", 0.08);
        g.append("text").attr("x", wa + 5).attr("y", yP(0.12)).attr("class", "chart-note").attr("fill", C.raw).text(final > 0.5 ? `raw count: ${race.a.name} behind` : `raw count: ${race.a.name} ahead`);
      }
      g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", cS(final)).attr("y2", cS(final)).attr("stroke", C.model).attr("stroke-dasharray", "5,4").attr("opacity", 0.6);
    }
    if (0.5 > d0 && 0.5 < d1) g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS(0.5)).attr("y2", yS(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
    g.append("path").datum(vis.filter((d) => d.sd != null && d.share != null)).attr("fill", C.model).attr("opacity", 0.14)
      .attr("d", d3.area().x(X).y0((d) => cS(lo(d))).y1((d) => cS(hi(d))).curve(d3.curveStepAfter));
    g.append("path").datum(vis.filter((d) => d.raw != null && d.counted > 0)).attr("fill", "none").attr("stroke", C.raw).attr("stroke-width", 2)
      .attr("d", d3.line().x(X).y((d) => cS(d.raw)));
    g.append("path").datum(vis.filter((d) => d.share != null)).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2)
      .attr("d", d3.line().x(X).y((d) => cS(d.share)).curve(d3.curveStepAfter));
    endLabels(svg, width - m.r + 5, [
      [lastOf("raw"), (d) => cS(d.raw), "raw count", C.raw],
      [lastOf("share"), (d) => cS(d.share), "model", C.model],
      [final != null ? {} : null, () => cS(final), `final ${pct(final)}`, C.model],
    ]);
  }

  // probability panel
  svg.insert("g", "g").attr("class", "grid").selectAll("line").data([0, 0.5, 1]).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP).attr("y2", yP);
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yP).tickValues([0, 0.5, 0.9]).tickSize(0).tickPadding(5)).call((s) => s.select(".domain").remove());
  const long = domain[1] - domain[0] > 24 * 3600000; // past 24 h, ticks carry the day ("12 Apr 22:00")
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top2 + h2})`).call(d3.axisBottom(x).ticks(Math.max(2, Math.floor((width - m.l - m.r) / (long ? 100 : 70)))).tickFormat(d3.utcFormat(long ? "%-d %b %H:%M" : "%H:%M")).tickSizeOuter(0));
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", top2 - 7).text(`Probability ${race.a.name} wins`);
  for (const v of [0.1, 0.9]) g.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP(v)).attr("y2", yP(v)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  const step = (k) => d3.line().defined((d) => d[k] != null).x(X).y((d) => yP(d[k])).curve(d3.curveStepAfter);
  g.append("path").datum(vis).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2).attr("d", step("P"));
  g.append("path").datum(vis).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.6).attr("d", step("pm"));
  g.append("path").datum(vis).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.4).attr("stroke-dasharray", "5,3").attr("d", step("kalshi"));
  endLabels(svg, width - m.r + 5, [
    [lastOf("P"), (d) => yP(d.P), "model", C.model],
    [lastOf("pm"), (d) => yP(d.pm), "Polymarket", C.market],
    [lastOf("kalshi"), (d) => yP(d.kalshi), "Kalshi", C.market],
  ]);
  const gate = countPanel ? vis.find((d) => d.counted >= 0.01) : null;
  if (gate) {
    g.append("line").attr("x1", X(gate)).attr("x2", X(gate)).attr("y1", m.t).attr("y2", top2 + h2).attr("stroke", C.muted).attr("stroke-dasharray", "1,3");
    g.append("text").attr("x", X(gate) + 4).attr("y", yP(0.03)).attr("class", "chart-note").attr("fill", C.muted).text(`1% counted ${clock(gate.t, tz)}`);
  }

  const preview = svg.append("line").attr("class", "cursor-preview").attr("y1", m.t).attr("y2", top2 + h2).attr("opacity", 0);
  const cursor = svg.append("line").attr("class", "cursor").attr("y1", m.t - 4).attr("y2", top2 + h2);
  const off = +wall(0);
  const tAt = (ev) => Math.max(domain[0], Math.min(domain[1], Math.round((+x.invert(d3.pointer(ev)[0]) - off) / 60000) * 60000));
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", width - m.l - m.r).attr("height", top2 + h2 - m.t).attr("fill", "transparent").style("cursor", "crosshair")
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
