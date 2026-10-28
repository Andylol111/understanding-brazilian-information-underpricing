// National view for elections with an overview file (US: every Senate and governor race of the
// year, us2026/overview.json). The state map itself is the dashboard's map canvas with a state
// layer (dash-map.js setNational): this module works out each state's colour, the market's
// P(Democratic side) (Polymarket first, Kalshi where Polymarket has none; grey: no race), and the
// rings on modelled states (with a county model), and owns what sits around the map: the office
// toggle, the Senate control bar, the legend, the hover card, the race panel with price history
// and the office summary beside the map. Clicking a modelled state drills into its county view.
import * as d3 from "./vendor/d3.js";
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

// office: "senate" | "governor"; summary: the element for the office summary beside the map;
// onColours(Map region -> colour, rings): the state layer for the map; onHighlight(region | null):
// outline a state on the map (summary row hover); onDrill(race_id): open a modelled state's county view.
export function createNational(root, { overview, office = "senate", summary = null, onDrill, onOffice, onColours, onHighlight }) {
  const st = { office, overview, live: new Map(), model: new Map(), panel: null, sort: { key: "state", dir: 1 } };

  root.innerHTML = `
    <div class="nat-top">
      <div class="toggle nat-office" role="group" aria-label="Office">${OFFICES.map(([k, l]) => `<button type="button" data-office="${k}" aria-pressed="${k === office}">${l}</button>`).join("")}</div>
      <div class="nat-control" hidden></div>
    </div>
    <div class="nat-legend map-legend"></div>
    <div class="place-card nat-card" hidden></div>
    <div class="nat-panel" hidden></div>`;
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
    const colours = new Map(), rings = [];
    for (const r of races()) {
      if (!colours.has(r.state)) colours.set(r.state, colourOf(priceOf(r)));
      if (r.modeled) rings.push(r.state);
    }
    onColours?.(colours, rings, NORACE);
    drawControl();
    drawLegend();
    drawSummary();
    if (st.panel) openPanel(st.panel);
  }

  const raceName = (r) => `${r.state_name ?? stateName(r.state)} ${r.office === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`;
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

  // the card for the state under the pointer (s: state code or null; ev: the pointer event)
  function hover(s, ev) {
    const rs = s ? raceIn(s) : [];
    if (!rs.length) { card.hidden = true; return; }
    card.innerHTML = rs.map(cardHTML).join("<hr>");
    card.hidden = false;
    const box = root.getBoundingClientRect();
    const [x, y] = ev?.clientX != null ? [ev.clientX - box.left, ev.clientY - box.top] : [box.width / 2, 80];
    card.style.left = `${Math.max(8, Math.min(box.width - 280, x + 14))}px`;
    card.style.top = `${Math.max(48, Math.min(box.height - card.offsetHeight - 8, y + 14))}px`;
  }

  function pick(s) {
    const rs = s ? raceIn(s) : [];
    card.hidden = true;
    if (!rs.length) { closePanel(); return; }
    const r = rs[0];
    if (r.modeled && r.race_id) { closePanel(); onDrill?.(r.race_id); return; }
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
    chart(box, (r.history ?? []).map(([t, pm, k]) => ({ t: new Date(t), pm, k })));
  }

  // 14 days of prices (pm: Polymarket black, k: Kalshi green), 0 to 1
  function chart(box, rows) {
    if (rows.length < 2) { box.innerHTML = `<p class="note">No price history yet.</p>`; return; }
    const w = Math.max(240, box.clientWidth || 300), h = 120, m = { l: 26, r: 6, t: 6, b: 18 };
    const x = d3.scaleUtc().domain(d3.extent(rows, (d) => d.t)).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    const s = d3.select(box).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
    s.append("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).tickValues([0, 0.5, 1]).tickSize(0).tickPadding(4)).call((a) => a.select(".domain").remove());
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(d3.utcWeek.every(1)).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
    const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.t)).y((d) => y(d[k])).curve(d3.curveStepAfter);
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.kalshi).attr("stroke-width", 1.4).attr("d", line("k"));
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.5).attr("d", line("pm"));
  }

  // Senate control: Democrats' expected seats and the control markets (live Polymarket price when read)
  function control() {
    const sen = st.overview?.offices?.senate;
    if (!sen?.holdovers) return null;
    const hold = sen.holdovers, rs = sen.races ?? [];
    const priced = rs.filter((r) => priceOf(r) != null);
    const eD = d3.sum(priced, priceOf), eR = priced.length - eD;
    const holdD = (hold.D ?? 0) + (hold.I ?? 0), holdR = hold.R ?? 0;
    const markets = (sen.control_markets ?? []).map((c) => ({ ...c, now: c.slug && st.live.has(`ctl:${c.slug}`) ? st.live.get(`ctl:${c.slug}`) : c.price }));
    return { hold, holdD, holdR, eD, eR, unpriced: rs.length - priced.length, total: sen.seats_total ?? 100, maj: sen.majority ?? 51, markets };
  }

  // The office summary beside the map: control (Senate) and a sortable list of the office's races.
  function drawSummary() {
    if (!summary) return;
    const office = st.office === "senate" ? "Senate" : "Governor";
    const ctl = st.office === "senate" ? control() : null;
    const rows = races().map((r) => ({ r, state: r.state_name ?? stateName(r.state), inc: r.incumbent ?? "", pm: pmP(r), k: r.kalshi?.price ?? null, model: r.race_id != null ? st.model.get(r.race_id) ?? null : null }));
    const { key, dir } = st.sort;
    rows.sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va == null && vb == null) return a.state.localeCompare(b.state);
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
    const cols = [["state", "State"], ["inc", "Incumbent"], ["pm", "Polymarket"], ["k", "Kalshi"], ["model", "Model"]];
    const ctlHTML = ctl ? `<h3 class="tab-h">Control of the Senate</h3>
      <p class="nat-sum-ctl"><span style="color:${DEM}"><b>Democrats ${(ctl.holdD + ctl.eD).toFixed(1)}</b></span> seats expected (${ctl.holdD} not up${ctl.hold.I ? `, incl. ${ctl.hold.I} independents` : ""}, + ${ctl.eD.toFixed(1)}),
        <span style="color:${REP}"><b>Republicans ${(ctl.holdR + ctl.eR).toFixed(1)}</b></span> (${ctl.holdR} + ${ctl.eR.toFixed(1)}); ${ctl.maj} seats for control${ctl.unpriced ? `; ${ctl.unpriced} races without a price` : ""}.
        Expected seats: each race counted at the market's chance.</p>
      ${ctl.markets.length ? `<p>Chance the Democrats control the Senate: ${ctl.markets.map((c) => `<span class="${c.platform === "kalshi" ? "kx" : "k"}">${num(c.now)}</span> ${c.platform === "kalshi" ? "Kalshi" : `Polymarket${c.slug && st.live.has(`ctl:${c.slug}`) ? " now" : ""}`}`).join(" · ")}</p>
        <div class="nat-ctl-chart"></div><p class="note">The last 14 days: <span class="k">Polymarket</span>, <span class="kx">Kalshi</span>.</p>` : ""}` : "";
    summary.innerHTML = `<p class="ro-head"><span class="ro-at"><b>${office}</b> races this year</span><span class="ro-race">chance the Democratic side wins</span></p>
      ${ctlHTML}
      <h3 class="tab-h">${office} races (${rows.length})</h3>
      <table class="races nat-races"><thead><tr>${cols.map(([k, l]) => `<th class="${k === "state" || k === "inc" ? "" : "n"}" aria-sort="${key === k ? (dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-key="${k}">${l}</button></th>`).join("")}</tr></thead>
      <tbody>${rows.map((x) => `<tr tabindex="0" data-key="${esc(x.r.race_key)}" data-state="${esc(x.r.state)}"><td>${esc(x.state)}${x.r.special ? " (special)" : ""}${x.r.modeled ? ` <span class="ring" title="county model"></span>` : ""}</td><td>${esc(x.inc)}${x.r.incumbent_party ? ` (${esc(x.r.incumbent_party)})` : ""}${x.r.open ? " <small>not running</small>" : ""}</td>
        <td class="n">${num(x.pm)}</td><td class="n kx">${num(x.k)}</td><td class="n m">${x.r.modeled ? num(x.model) : ""}</td></tr>`).join("")}</tbody></table>
      <p class="note">Click a race (or a state on the map) for its prices; ringed states open the county model. Polymarket's prices are read live every minute; Kalshi's are from the last scheduled build.</p>`;
    if (ctl?.markets.length) {
      const pm = ctl.markets.find((c) => c.platform !== "kalshi"), ks = ctl.markets.find((c) => c.platform === "kalshi");
      const byT = new Map();
      for (const [t, p] of pm?.history ?? []) byT.set(t, { t: new Date(t), pm: p, k: null });
      for (const [t, p] of ks?.history ?? []) byT.set(t, { ...(byT.get(t) ?? { t: new Date(t), pm: null }), k: p });
      chart(summary.querySelector(".nat-ctl-chart"), [...byT.values()].sort((a, b) => a.t - b.t));
    }
    summary.querySelectorAll("th button").forEach((b) => b.addEventListener("click", () => {
      st.sort = { key: b.dataset.key, dir: st.sort.key === b.dataset.key ? -st.sort.dir : b.dataset.key === "state" || b.dataset.key === "inc" ? 1 : -1 };
      drawSummary();
    }));
    summary.querySelectorAll("tbody tr").forEach((tr) => {
      const go = () => {
        const r = races().find((x) => x.race_key === tr.dataset.key);
        if (r?.modeled && r.race_id) onDrill?.(r.race_id);
        else if (r) openPanel(r.race_key);
      };
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      tr.addEventListener("pointerenter", () => onHighlight?.(tr.dataset.state));
      tr.addEventListener("pointerleave", () => onHighlight?.(null));
    });
  }

  // Senate: seats held over plus the seats each side is favoured to win (expected seats = sum of
  // P(Democratic side) at current prices), against the majority line; the control markets beside it.
  function drawControl() {
    const box = root.querySelector(".nat-control");
    const c = st.office === "senate" ? control() : null;
    if (!c) { box.hidden = true; return; }
    const pct = (v) => `${(100 * v / c.total).toFixed(2)}%`;
    const ctl = c.markets.map((m) => `${m.platform === "kalshi" ? "Kalshi" : "Polymarket"} ${num(m.now)}`).join(" · ");
    box.hidden = false;
    box.innerHTML = `<div class="nat-bar" role="img" aria-label="Senate seats: Democrats ${c.holdD} held over plus ${c.eD.toFixed(1)} expected, Republicans ${c.holdR} held over plus ${c.eR.toFixed(1)} expected">
        <i style="width:${pct(c.holdD)};background:${DEM}"></i><i style="width:${pct(c.eD)};background:${DEM};opacity:.45"></i><i class="gap" style="width:${pct(c.unpriced)}"></i><i style="width:${pct(c.eR)};background:${REP};opacity:.45"></i><i style="width:${pct(c.holdR)};background:${REP}"></i>
        <b style="left:${pct(c.maj - 0.5)}"></b></div>
      <p class="nat-bar-text"><span style="color:${DEM}">Democrats ${(c.holdD + c.eD).toFixed(1)}</span> (${c.holdD} not up${c.hold.I ? `, incl. ${c.hold.I} independents` : ""} + ${c.eD.toFixed(1)} expected) ·
        <span style="color:${REP}">Republicans ${(c.holdR + c.eR).toFixed(1)}</span> (${c.holdR} + ${c.eR.toFixed(1)}) · ${c.maj} for control${ctl ? ` · Democrats win the Senate: ${ctl}` : ""}</p>`;
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
    hover,
    pick,
    // a modelled state's race for the current office (for prefetching its county file)
    raceFor: (s) => raceIn(s)[0] ?? null,
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
      for (const o of Object.values(st.overview?.offices ?? {})) {
        for (const r of o.races ?? []) if (r.pm?.slug) out.push(r.pm.slug);
        for (const c of o.control_markets ?? []) if (c.platform === "polymarket" && c.slug) out.push(c.slug);
      }
      return out;
    },
  };
  return api;
}
