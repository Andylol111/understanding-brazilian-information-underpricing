// Homepage world map: the way into each election. Countries with elections are coloured by their
// most current status (live, before the count, replay) with a small count where there are several;
// a card on hover names them. Choosing a country zooms to it (d3.interpolateZoom, ~700 ms); with one
// election the page then fades into its dashboard, with several an in-map chooser lists them.
// Back / Esc zooms out. Keyboard: countries with elections are focusable; Enter chooses. Small
// screens also get the same choices as a list under the map. Natural Earth 1:110m countries
// (public domain), geo/world.topo.json from analysis/geo_build.py.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { C, esc, fmtDate } from "./common.js";

const MUTED = "#e6e4dd";
const STATUS_COLOUR = { live: C.model, scheduled: C.raw, replay: "#3b3b3b" };
const STATUS_LABEL = { live: "Live", scheduled: "Before the count", replay: "Replay" };
const RANK = { live: 0, scheduled: 1, replay: 2 };
const ZOOM_MS = 700;
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// root: the section to fill; elections: elections.json entries; href(id): the dashboard link;
// countryName(code): display name. frames (optional): array receiving rAF intervals during zooms.
export function createWorldMap(root, { topo, elections, href, countryName, frames = null }) {
  const byCountry = d3.group(elections.filter((e) => e.country), (e) => e.country.toUpperCase());
  const features = topojson.feature(topo, topo.objects.countries).features;
  const borders = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
  const statusOf = (code) => d3.least(byCountry.get(code) ?? [], (e) => RANK[e.status] ?? 9)?.status ?? null;
  const nameOf = (f) => countryName(f.properties.iso2) || f.properties.name;
  const active = features.filter((f) => byCountry.has(f.properties.iso2));

  root.innerHTML = `
    <div class="wm-box">
      <svg class="wm-svg" role="group" aria-label="Countries with elections on this site"></svg>
      <div class="wm-legend">${Object.entries(STATUS_LABEL).filter(([s]) => elections.some((e) => e.status === s)).map(([s, l]) => `<span><i style="background:${STATUS_COLOUR[s]}"></i>${l}</span>`).join("")}</div>
      <div class="place-card wm-card" hidden></div>
    </div>
    <div class="wm-chooser" hidden></div>
    <ul class="wm-list" aria-label="Elections by country">${[...byCountry].sort((a, b) => countryName(a[0]).localeCompare(countryName(b[0]))).map(([code, list]) =>
      `<li><button type="button" data-code="${esc(code)}"><i style="background:${STATUS_COLOUR[statusOf(code)] ?? MUTED}"></i>${esc(countryName(code))}${list.length > 1 ? ` <span>${list.length}</span>` : ""}</button></li>`).join("")}</ul>`;
  const box = root.querySelector(".wm-box"), svg = d3.select(root).select("svg");
  const card = root.querySelector(".wm-card"), chooser = root.querySelector(".wm-chooser");
  const st = { w: 0, h: 0, view: { k: 1, x: 0, y: 0 }, anim: 0, chosen: null, path: null };

  const g = svg.append("g").attr("class", "wm-zoom");
  const land = g.append("g").selectAll("path").data(features).join("path")
    .attr("class", (f) => (byCountry.has(f.properties.iso2) ? "wm-c on" : "wm-c"))
    .attr("fill", (f) => STATUS_COLOUR[statusOf(f.properties.iso2)] ?? MUTED);
  const mesh = g.append("path").attr("class", "wm-border").datum(borders);
  // hit and count markers for the countries with elections (small countries stay easy to reach)
  const marks = g.append("g").selectAll("g").data(active).join("g").attr("class", "wm-mark");
  marks.append("circle").attr("class", "wm-hit");
  marks.filter((f) => byCountry.get(f.properties.iso2).length > 1).append("text").attr("class", "wm-count")
    .text((f) => byCountry.get(f.properties.iso2).length);
  // the countries with elections (and their markers) answer pointer and keyboard; the countries
  // themselves are the focus targets, one per country
  const electionCountries = land.filter((f) => byCountry.has(f.properties.iso2));
  const count = (f) => byCountry.get(f.properties.iso2).length;
  electionCountries.attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", (f) => `${nameOf(f)}: ${count(f)} election${count(f) > 1 ? "s" : ""}`);
  for (const sel of [electionCountries, marks]) {
    sel.on("pointermove", (ev, f) => showCard(f.properties.iso2, ev))
      .on("pointerleave", () => { card.hidden = true; })
      .on("focus", (ev, f) => showCard(f.properties.iso2, null, ev.currentTarget))
      .on("blur", () => { card.hidden = true; })
      .on("click", (ev, f) => choose(f.properties.iso2))
      .on("keydown", (ev, f) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); choose(f.properties.iso2); } });
  }
  root.querySelectorAll(".wm-list button").forEach((b) => b.addEventListener("click", () => choose(b.dataset.code)));
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && st.chosen) back(); });

  function layout() {
    const r = box.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || (r.width === st.w && r.height === st.h)) return;
    st.w = r.width; st.h = r.height;
    const proj = d3.geoEqualEarth().fitExtent([[8, 8], [st.w - 8, st.h - 8]], { type: "FeatureCollection", features });
    st.path = d3.geoPath(proj);
    svg.attr("viewBox", `0 0 ${st.w} ${st.h}`).attr("width", st.w).attr("height", st.h);
    land.attr("d", st.path);
    mesh.attr("d", st.path);
    marks.attr("transform", (f) => `translate(${centre(f)})`);
    st.view = st.chosen ? fit(st.chosen) : { k: 1, x: 0, y: 0 };
    apply();
  }
  // the label point: the largest polygon's centroid (France's mainland, not its overseas parts)
  function centre(f) {
    const geom = f.geometry.type === "MultiPolygon"
      ? { type: "Polygon", coordinates: d3.greatest(f.geometry.coordinates, (c) => d3.geoArea({ type: "Polygon", coordinates: c })) }
      : f.geometry;
    return st.path.centroid(geom);
  }
  function fit(code) {
    const f = features.find((x) => x.properties.iso2 === code);
    const geom = f.geometry.type === "MultiPolygon" ? { type: "Polygon", coordinates: d3.greatest(f.geometry.coordinates, (c) => d3.geoArea({ type: "Polygon", coordinates: c })) } : f.geometry;
    const [[x0, y0], [x1, y1]] = st.path.bounds(geom);
    const pad = Math.min(st.w, st.h) * 0.12;
    const k = Math.min(12, Math.min((st.w - 2 * pad) / Math.max(1, x1 - x0), (st.h - 2 * pad) / Math.max(1, y1 - y0)));
    return { k, x: st.w / 2 - k * (x0 + x1) / 2, y: st.h / 2 - k * (y0 + y1) / 2 };
  }
  function apply() {
    const { k, x, y } = st.view;
    g.attr("transform", `translate(${x},${y}) scale(${k})`);
    marks.select(".wm-hit").attr("r", 11 / k);
    marks.select(".wm-count").style("font-size", `${11 / k}px`).attr("dy", 4 / k);
  }
  function zoomTo(view, done) {
    cancelAnimationFrame(st.anim);
    if (reducedMotion() || document.hidden) { st.view = view; apply(); done?.(); return; }
    const toZ = (v) => [(st.w / 2 - v.x) / v.k, (st.h / 2 - v.y) / v.k, st.w / v.k];
    const interp = d3.interpolateZoom(toZ(st.view), toZ(view));
    let t0 = null, last = 0, settle = 1; // the first frame carries the style changes made on the click
    const step = (now) => {
      if (settle-- > 0) { st.anim = requestAnimationFrame(step); return; }
      if (t0 == null) { t0 = now; last = now; } else frames?.push(now - last);
      last = now;
      const u = d3.easeCubicInOut(Math.min(1, (now - t0) / ZOOM_MS));
      const [cx, cy, w] = interp(u);
      const k = st.w / w;
      st.view = { k, x: st.w / 2 - cx * k, y: st.h / 2 - cy * k };
      apply();
      if (u < 1) st.anim = requestAnimationFrame(step); else done?.();
    };
    st.anim = requestAnimationFrame(step);
  }

  function cardHTML(code) {
    const list = [...byCountry.get(code)].sort((a, b) => b.date.localeCompare(a.date));
    return `<p class="pc-name"><b>${esc(countryName(code))}</b></p>
      <ul class="wm-elections">${list.map((e) => `<li><b>${esc(e.name)}</b> <span class="wm-meta">${esc(fmtDate(e.date))} · <span class="wm-st" style="color:${STATUS_COLOUR[e.status] ?? C.muted}">${esc(STATUS_LABEL[e.status] ?? e.status)}</span></span>${e.summary ? `<br><span class="wm-sum">${esc(e.summary)}</span>` : ""}</li>`).join("")}</ul>`;
  }
  function showCard(code, ev, target) {
    if (st.chosen) return;
    card.innerHTML = cardHTML(code);
    card.hidden = false;
    const b = box.getBoundingClientRect();
    let x, y;
    if (ev) { x = ev.clientX - b.left; y = ev.clientY - b.top; } else { const t = target.getBoundingClientRect(); x = t.right - b.left; y = t.top - b.top; }
    card.style.left = `${Math.max(8, Math.min(b.width - card.offsetWidth - 8, x + 14))}px`;
    card.style.top = `${Math.max(8, Math.min(b.height - card.offsetHeight - 8, y + 14))}px`;
  }

  // zoom in; one election: fade into its dashboard; several: list them in the map
  function choose(code) {
    if (!byCountry.has(code)) return;
    card.hidden = true;
    st.chosen = code;
    root.classList.add("wm-zoomed");
    land.classed("dim", (f) => f.properties.iso2 !== code);
    const list = [...byCountry.get(code)].sort((a, b) => b.date.localeCompare(a.date));
    chooser.hidden = true;
    zoomTo(fit(code), () => {
      if (st.chosen !== code) return;
      if (list.length === 1) { leave(href(list[0].id)); return; }
      chooser.innerHTML = `<p class="wm-ch-head"><b>${esc(countryName(code))}</b> <button type="button" class="crumb-back">All countries</button></p>
        <ul>${list.map((e) => `<li><a href="${href(e.id)}"><b>${esc(e.name)}</b><span class="wm-meta">${esc(fmtDate(e.date))} · <span style="color:${STATUS_COLOUR[e.status] ?? C.muted}">${esc(STATUS_LABEL[e.status] ?? e.status)}</span></span></a></li>`).join("")}</ul>`;
      chooser.hidden = false;
      chooser.querySelector(".crumb-back").addEventListener("click", back);
      chooser.querySelectorAll("a").forEach((a) => a.addEventListener("click", (ev) => { if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); leave(a.href); } }));
      chooser.querySelector("a")?.focus({ preventScroll: true });
    });
  }
  function back() {
    st.chosen = null;
    chooser.hidden = true;
    root.classList.remove("wm-zoomed");
    land.classed("dim", false);
    zoomTo({ k: 1, x: 0, y: 0 });
  }
  // a short fade before the dashboard loads (its map opens framed on the same country)
  function leave(url) {
    if (reducedMotion()) { location.href = url; return; }
    document.body.classList.add("leaving");
    setTimeout(() => { location.href = url; }, 220);
  }
  window.addEventListener("pageshow", (ev) => { if (ev.persisted) { document.body.classList.remove("leaving"); back(); } });
  // back from an election (index.html?from=<id>): start framed on its country, then zoom out
  function arrive(code) {
    if (!byCountry.has(code) || !st.w) return;
    land.classed("dim", (f) => f.properties.iso2 !== code);
    st.view = fit(code);
    apply();
    requestAnimationFrame(() => requestAnimationFrame(() => { land.classed("dim", false); zoomTo({ k: 1, x: 0, y: 0 }); }));
  }

  new ResizeObserver(layout).observe(box);
  layout();
  return { choose, back, arrive };
}
