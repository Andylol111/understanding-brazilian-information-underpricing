// Time scrubber: a native range input (keyboard + screen readers) over a small area chart of the
// share counted, with event markers for the selected race.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { C } from "./common.js";

const MIN = 60000;

export function createScrubber(root, { onInput }) {
  root.innerHTML = `
    <div class="scrub-track">
      <svg class="scrub-svg" aria-hidden="true"></svg>
      <input class="scrub-range" type="range" step="1" aria-label="Time of night">
    </div>`;
  const svgEl = root.querySelector(".scrub-svg");
  const range = root.querySelector(".scrub-range");
  let st = { domain: [0, 1], rows: [], events: [], wall: (d) => new Date(+d), tz: "UTC" };
  range.addEventListener("input", () => onInput(+range.value * MIN));

  function draw() {
    const w = Math.max(200, svgEl.clientWidth || root.clientWidth);
    const h = 64; // labels 0-26, area 26-56, axis text below
    const pad = 9; // thumb radius: keeps the area aligned with the range input's thumb travel
    const x = d3.scaleLinear().domain(st.domain).range([pad, w - pad]);
    const svg = d3.select(svgEl).attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
    svg.selectAll("*").remove();
    const yTop = 30, yBot = 54;
    const y = d3.scaleLinear().domain([0, 1]).range([yBot, yTop]);
    const rows = st.rows.filter((d) => +d.time >= st.domain[0] && +d.time <= st.domain[1]);
    svg.append("rect").attr("x", pad).attr("width", w - 2 * pad).attr("y", yTop).attr("height", yBot - yTop).attr("fill", "#f1efe9");
    svg.append("path").datum(rows).attr("fill", C.raw).attr("opacity", 0.35)
      .attr("d", d3.area().x((d) => x(+d.time)).y0(yBot).y1((d) => y(d.counted ?? 0)).curve(d3.curveStepAfter));
    svg.append("line").attr("x1", pad).attr("x2", w - pad).attr("y1", yBot).attr("y2", yBot).attr("stroke", "#bdb9ae");
    // hour ticks; past 24 h the labels carry the day ("12 Apr 22:00") and need more room
    const hours = (st.domain[1] - st.domain[0]) / 3600000;
    const long = hours > 24;
    const gapPx = long ? 84 : 46;
    const every = [1, 2, 3, 4, 6, 12, 24, 48, 96].find((s) => (w / Math.max(1, hours)) * s >= gapPx) ?? 168;
    const ticks = d3.utcHour.every(every).range(st.wall(st.domain[0]), st.wall(st.domain[1]));
    svg.append("g").selectAll("text").data(ticks).join("text").attr("class", "scrub-tick")
      .attr("x", (d) => x(+d - (+st.wall(0)))).attr("y", h - 0).attr("text-anchor", "middle").text(d3.utcFormat(long ? "%-d %b %H:%M" : "%H:%M"));
    // event markers: labels on two rows; markers within 40 px of the previous one go on the other row
    const ev = st.events.filter((e) => e.t != null).map((e) => ({ ...e, x: x(Math.max(st.domain[0], Math.min(st.domain[1], e.t))) })).sort((a, b) => a.x - b.x);
    const rowsEnd = [-Infinity, -Infinity];
    let prev = null;
    for (const e of ev) {
      const wText = e.label.length * 6 + 8;
      let lx = Math.min(Math.max(e.x - 3, 0), w - wText);
      let r = prev && e.x - prev.x < 40 ? 1 - prev.r : rowsEnd[0] <= lx ? 0 : rowsEnd[1] <= lx ? 1 : rowsEnd[0] <= rowsEnd[1] ? 0 : 1;
      lx = Math.max(lx, rowsEnd[r]);
      rowsEnd[r] = lx + wText;
      e.lx = lx; e.ly = r === 0 ? 11 : 23; e.r = r;
      prev = e;
    }
    const gE = svg.append("g").selectAll("g").data(ev).join("g");
    gE.append("line").attr("x1", (d) => d.x).attr("x2", (d) => d.x).attr("y1", (d) => d.ly + 2).attr("y2", yBot).attr("stroke", (d) => d.color).attr("stroke-width", 1.2);
    gE.append("text").attr("class", "scrub-event").attr("x", (d) => d.lx).attr("y", (d) => d.ly).attr("fill", (d) => d.color).text((d) => d.label);
  }

  const ro = new ResizeObserver(() => draw());
  ro.observe(root);

  return {
    set({ domain, rows, events, wall, tz }) {
      st = { domain, rows, events, wall, tz };
      range.min = String(Math.round(domain[0] / MIN));
      range.max = String(Math.round(domain[1] / MIN));
      draw();
    },
    setTime(t, label) {
      range.value = String(Math.round(t / MIN));
      range.setAttribute("aria-valuetext", label);
    },
    range,
  };
}
