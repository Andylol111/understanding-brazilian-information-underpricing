// National state map for elections with an overview file (US: every Senate and governor race of
// the year, us2026/overview.json). States with a race are coloured by the market's P(Democratic
// side), Polymarket first, Kalshi where Polymarket has none; states without a race are grey.
// Modeled states (with a county model) carry a ring; clicking them drills into the county view,
// other states open a small race panel with the price history. Pure view: the controller passes
// the overview, current Polymarket prices and (on the night) model chances, and gets callbacks.
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import * as topojson from "https://cdn.jsdelivr.net/npm/topojson-client@3/+esm";
import { C, esc, num } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec", NORACE = "#e6e4dd";
const OFFICES = [["governor", "Governor"], ["senate", "Senate"]];
const STATE_NAMES = new Map(Object.entries({
  al: "Alabama", ak: "Alaska", az: "Arizona", ar: "Arkansas", ca: "California", co: "Colorado", ct: "Connecticut", de: "Delaware", dc: "District of Columbia",
  fl: "Florida", ga: "Georgia", hi: "Hawaii", id: "Idaho", il: "Illinois", in: "Indiana", ia: "Iowa", ks: "Kansas", ky: "Kentucky", la: "Louisiana", me: "Maine",
  md: "Maryland", ma: "Massachusetts", mi: "Michigan", mn: "Minnesota", ms: "Mississippi", mo: "Missouri", mt: "Montana", ne: "Nebraska", nv: "Nevada",
  nh: "New Hampshire", nj: "New Jersey", nm: "New Mexico", ny: "New York", nc: "North Carolina", nd: "North Dakota", oh: "Ohio", ok: "Oklahoma", or: "Oregon",
  pa: "Pennsylvania", ri: "Rhode Island", sc: "South Carolina", sd: "South Dakota", tn: "Tennessee", tx: "Texas", ut: "Utah", vt: "Vermont", va: "Virginia",
  wa: "Washington", wv: "West Virginia", wi: "Wisconsin", wy: "Wyoming",
}));
export const stateName = (st) => STATE_NAMES.get(st) ?? st.toUpperCase();
const ramp = d3.range(201).map((j) => {
  const v = j / 100 - 1; // +1: Democratic side certain
  return d3.interpolateLab(NEUTRAL, v >= 0 ? DEM : REP)(Math.min(1, Math.abs(v) / 0.8) ** 0.8);
});
export const colourOf = (p) => (p == null ? NORACE : ramp[Math.round(Math.max(0, Math.min(1, p)) * 200)]);

// topo: the US county topology (unit ids "us-<st>-<fips>"); office: "senate" | "governor".
export function createNational(root, { topo, overview, office = "senate", onDrill, onOffice }) {
  const obj = Object.values(topo.objects)[0];
  const stOf = (g) => String(g.properties?.unit_id ?? g.id).split("-")[1];
  const byState = d3.group(obj.geometries, stOf);
  const states = [...byState].map(([st, geoms]) => ({ type: "Feature", id: st, properties: { st }, geometry: topojson.merge(topo, geoms) }));
  const borders = topojson.mesh(topo, obj, (a, b) => stOf(a) !== stOf(b));
  const outline = topojson.mesh(topo, obj, (a, b) => a === b);
  const st = { office, overview, live: new Map(), model: new Map(), panel: null };

  root.innerHTML = `
    <div class="nat-top">
      <div class="toggle nat-office" role="group" aria-label="Office">${OFFICES.map(([k, l]) => `<button type="button" data-office="${k}" aria-pressed="${k === office}">${l}</button>`).join("")}</div>
      <div class="nat-control" hidden></div>
    </div>
    <svg class="nat-map" role="img" aria-label="States coloured by the market's chance that the Democratic side wins"></svg>
    <div class="nat-legend map-legend"></div>
    <div class="place-card nat-card" hidden></div>
    <div class="nat-panel" hidden></div>`;
  const svg = d3.select(root).select("svg");
  const card = root.querySelector(".nat-card");
  const panel = root.querySelector(".nat-panel");
  root.querySelectorAll(".nat-office button").forEach((b) => b.addEventListener("click", () => {
    st.office = b.dataset.office;
    root.querySelectorAll(".nat-office button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    closePanel();
    draw();
    onOffice?.(st.office);
  }));

  const races = () => st.overview?.offices?.[st.office]?.races ?? [];
  // P(Democratic side): live Polymarket price, else the file's Polymarket price, else Kalshi's
  const pmP = (r) => (r.pm ? (st.live.has(r.race_key) ? st.live.get(r.race_key) : r.pm.price) : null);
  const priceOf = (r) => pmP(r) ?? r.kalshi?.price ?? null;
  const raceIn = (s) => races().filter((r) => r.state === s);

  function draw() {
    const w = Math.max(280, root.clientWidth), h = Math.max(240, root.clientHeight - root.querySelector(".nat-top").offsetHeight);
    const projection = d3.geoAlbersUsa().fitExtent([[8, 8], [w - 8, h - 30]], { type: "FeatureCollection", features: states });
    const path = d3.geoPath(projection);
    svg.attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).html("");
    const g = svg.append("g");
    g.selectAll("path.st").data(states).join("path").attr("class", "st")
      .attr("d", path)
      .attr("fill", (f) => { const rs = raceIn(f.id); return rs.length ? colourOf(priceOf(rs[0])) : NORACE; })
      .attr("tabindex", (f) => (raceIn(f.id).length ? 0 : null))
      .attr("aria-label", (f) => label(f.id))
      .on("pointermove", (ev, f) => hover(f.id, ev))
      .on("pointerleave", () => hover(null))
      .on("focus", (ev, f) => hover(f.id, ev))
      .on("blur", () => hover(null))
      .on("click", (ev, f) => pick(f.id))
      .on("keydown", (ev, f) => { if (ev.key === "Enter") pick(f.id); });
    g.append("path").datum(borders).attr("class", "st-border").attr("d", path);
    g.append("path").datum(outline).attr("class", "st-outline").attr("d", path);
    // modeled states: a ring at the state's centre
    const modeled = states.filter((f) => raceIn(f.id).some((r) => r.modeled));
    g.selectAll("circle.st-model").data(modeled).join("circle").attr("class", "st-model")
      .attr("cx", (f) => path.centroid(f)[0]).attr("cy", (f) => path.centroid(f)[1]).attr("r", 5);
    drawControl();
    drawLegend();
    if (st.panel) openPanel(st.panel);
  }

  function label(s) {
    const rs = raceIn(s);
    if (!rs.length) return `${stateName(s)}: no ${st.office} race this year`;
    return rs.map((r) => `${raceName(r)}: ${r.dem_side?.name ?? "Democratic side"} ${num(priceOf(r))}`).join("; ");
  }
  const raceName = (r) => `${stateName(r.state)} ${r.office === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`;
  const incumbent = (r) => (r.incumbent ? `${esc(r.incumbent)}${r.incumbent_party ? ` (${esc(r.incumbent_party)})` : ""}${r.open ? ", not running" : ""}` : r.open ? "open seat" : "–");

  function cardHTML(r) {
    const m = r.race_id != null ? st.model.get(r.race_id) : null;
    return `<p><b>${esc(raceName(r))}</b></p>
      <p>Incumbent: ${incumbent(r)}</p>
      <p>Chance ${esc(r.dem_side?.name ?? "the Democratic side")}${r.dem_side?.party && r.dem_side.party !== "D" ? ` (${esc(r.dem_side.party)})` : ""} wins:
        ${m != null ? `<span class="m">${num(m)}</span> model · ` : ""}<span class="k">${num(pmP(r))}</span> Polymarket${st.live.has(r.race_key) ? " now" : ""} · <span class="kx">${num(r.kalshi?.price)}</span> Kalshi</p>
      ${r.dem_side?.aligned ? `<p class="pc-hint">${esc(r.dem_side.aligned)}</p>` : ""}
      <p class="pc-hint">${r.modeled ? "Click for the county model." : "Click for the price history. No county model for this state."}</p>`;
  }

  function hover(s, ev) {
    const rs = s ? raceIn(s) : [];
    svg.selectAll("path.st").classed("hl", (f) => f.id === s && rs.length > 0);
    if (!rs.length) { card.hidden = true; return; }
    card.innerHTML = rs.map(cardHTML).join("<hr>");
    card.hidden = false;
    const [x, y] = ev?.clientX != null ? d3.pointer(ev, root) : [root.clientWidth / 2, 80];
    card.style.left = `${Math.min(root.clientWidth - 280, x + 14)}px`;
    card.style.top = `${Math.max(48, Math.min(root.clientHeight - card.offsetHeight - 8, y + 14))}px`;
  }

  function pick(s) {
    const rs = raceIn(s);
    if (!rs.length) return;
    card.hidden = true;
    const r = rs[0];
    if (r.modeled && r.race_id) { onDrill?.(r.race_id); return; }
    openPanel(r.race_key);
  }

  function closePanel() { st.panel = null; panel.hidden = true; panel.innerHTML = ""; }
  function openPanel(key) {
    const r = races().find((x) => x.race_key === key);
    if (!r) { closePanel(); return; }
    st.panel = key;
    panel.hidden = false;
    panel.innerHTML = `<button type="button" class="nat-close" aria-label="Close">×</button>${cardHTML(r).replace(/<p class="pc-hint">Click[^<]*<\/p>/, "")}
      <div class="nat-hist"></div>
      <p class="note">Prices every 30 minutes over the last 14 days: <span class="k">Polymarket</span>, <span class="kx">Kalshi</span>. No county model for this state.</p>`;
    panel.querySelector(".nat-close").addEventListener("click", closePanel);
    history(panel.querySelector(".nat-hist"), r);
  }

  function history(box, r) {
    const rows = (r.history ?? []).map(([t, pm, k]) => ({ t: new Date(t), pm, k }));
    if (rows.length < 2) { box.innerHTML = `<p class="note">No price history yet.</p>`; return; }
    const w = Math.max(240, box.clientWidth || 300), h = 120, m = { l: 26, r: 6, t: 6, b: 18 };
    const x = d3.scaleUtc().domain(d3.extent(rows, (d) => d.t)).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    const s = d3.select(box).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
    s.append("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).tickValues([0, 0.5, 1]).tickSize(0).tickPadding(4)).call((a) => a.select(".domain").remove());
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(d3.utcWeek.every(1)).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
    const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.t)).y((d) => y(d[k])).curve(d3.curveStepAfter);
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.kalshi).attr("stroke-width", 1.4).attr("d", line("k"));
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.5).attr("d", line("pm"));
  }

  // Senate: seats held over plus the seats each side is favoured to win (expected seats = sum of
  // P(Democratic side) at current prices), against the majority line; the control markets beside it.
  function drawControl() {
    const box = root.querySelector(".nat-control");
    const sen = st.overview?.offices?.senate;
    if (st.office !== "senate" || !sen?.holdovers) { box.hidden = true; return; }
    const hold = sen.holdovers, total = sen.seats_total ?? 100, maj = sen.majority ?? 51;
    const rs = sen.races ?? [];
    const priced = rs.filter((r) => priceOf(r) != null);
    const eD = d3.sum(priced, priceOf), eR = priced.length - eD;
    const holdD = (hold.D ?? 0) + (hold.I ?? 0), holdR = hold.R ?? 0;
    const unpriced = rs.length - priced.length;
    const pct = (v) => `${(100 * v / total).toFixed(2)}%`;
    const ctl = (sen.control_markets ?? []).map((c) => `${c.platform === "kalshi" ? "Kalshi" : "Polymarket"} ${num(c.price)}`).join(" · ");
    box.hidden = false;
    box.innerHTML = `<div class="nat-bar" role="img" aria-label="Senate seats: Democrats ${holdD} held over plus ${eD.toFixed(1)} expected, Republicans ${holdR} held over plus ${eR.toFixed(1)} expected">
        <i style="width:${pct(holdD)};background:${DEM}"></i><i style="width:${pct(eD)};background:${DEM};opacity:.45"></i><i class="gap" style="width:${pct(unpriced)}"></i><i style="width:${pct(eR)};background:${REP};opacity:.45"></i><i style="width:${pct(holdR)};background:${REP}"></i>
        <b style="left:${pct(maj - 0.5)}"></b></div>
      <p class="nat-bar-text"><span style="color:${DEM}">Democrats ${(holdD + eD).toFixed(1)}</span> (${holdD} not up${hold.I ? `, incl. ${hold.I} independents` : ""} + ${eD.toFixed(1)} expected) ·
        <span style="color:${REP}">Republicans ${(holdR + eR).toFixed(1)}</span> (${holdR} + ${eR.toFixed(1)}) · ${maj} for control${ctl ? ` · Democrats win the Senate: ${ctl}` : ""}</p>`;
  }

  function drawLegend() {
    const box = root.querySelector(".nat-legend");
    const w = 200;
    box.innerHTML = `<svg viewBox="0 0 ${w} 24" width="${w}" height="24"><defs><linearGradient id="nat-grad">${d3.range(0, 201, 10).map((j) => `<stop offset="${j / 200}" stop-color="${ramp[200 - j]}"/>`).join("")}</linearGradient></defs>
        <rect width="${w}" height="8" fill="url(#nat-grad)"/><text x="0" y="20" class="legend-text">Democratic side</text><text x="${w}" y="20" text-anchor="end" class="legend-text">Republican</text></svg>
      <p class="legend-note"><span class="sw" style="background:${NORACE}"></span> no ${st.office === "senate" ? "Senate" : "governor"} race · <span class="ring"></span> county model</p>
      <p class="legend-note">Colour: market chance (Polymarket, else Kalshi).</p>`;
  }

  const api = {
    draw,
    get office() { return st.office; },
    update({ overview, live, model } = {}) {
      if (overview) st.overview = overview;
      if (live) st.live = live;
      if (model) st.model = model;
      draw();
    },
    // Polymarket slugs of every race (both offices) and the control markets, for live prices
    slugs() {
      const out = [];
      for (const o of Object.values(st.overview?.offices ?? {})) for (const r of o.races ?? []) if (r.pm?.slug) out.push(r.pm.slug);
      return out;
    },
  };
  new ResizeObserver(() => { if (!root.hidden) draw(); }).observe(root);
  return api;
}
