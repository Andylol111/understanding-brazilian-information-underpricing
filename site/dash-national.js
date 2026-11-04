// National view for elections with an overview file (US: every Senate and governor race of the
// year, us2026/overview.json). The state map itself is the dashboard's map canvas with a state
// layer (dash-map.js setNational): this module works out each state's colour, the market's
// P(Democratic side) (Polymarket first, Kalshi where Polymarket has none; grey: no race), and a
// marker (●) on states with a live county model on election night, and owns what sits around the
// map: the office toggle, the legend, the hover card, the race panel with
// price history and the office summary beside the map (the Senate's control chances, and the seat hemicycle the page draws). Clicking a state drills into its county view.
import * as d3 from "./vendor/d3.js";
import { C, esc, num, fmtDate, pmUsable } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec", NORACE = "#e6e4dd";
// House: the page draws it (dash-house.js); this module only offers the button and steps aside
const OFFICES = [["senate", "Senate"], ["governor", "Governor"], ["house", "House"]];
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

// office: "senate" | "governor" | "house"; summary: the element for the office summary beside the map;
// onColours(Map region -> colour, marked): the state layer for the map (marked: states with a live
// county model); onSummary(): the summary was redrawn (the page fills its .hemi);
// onDrill(race_id): open a state's county view. modelled(race): the race has a live county model on
// election night (the page knows: its own race files); feedNote(state): one line on why a state has
// none. layers: [[key, label]] extra map modes (census) offered in a select; onLayer(key | "races").
export function createNational(root, { overview, office = "senate", summary = null, onDrill, onOffice, onColours, onSummary, layers = [], onLayer, modelled = (r) => !!r.modeled, feedNote = () => "" }) {
  const st = { office, overview, live: new Map(), model: new Map(), panel: null };

  root.innerHTML = `
    <div class="nat-top">
      <div class="toggle nat-office" role="group" aria-label="Office">${OFFICES.map(([k, l]) => `<button type="button" data-office="${k}" aria-pressed="${k === office}">${l}</button>`).join("")}</div>
      ${layers.length ? `<label class="select nat-layer"><span>Map</span> <select aria-label="Map colour"><option value="races">Races</option>${layers.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></label>` : ""}
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
  root.querySelector(".nat-layer select")?.addEventListener("change", (ev) => onLayer?.(ev.target.value));

  const races = () => st.overview?.offices?.[st.office]?.races ?? [];
  // P(Democratic side): live Polymarket price, else the file's Polymarket price, else Kalshi's. A one-sided-book price
  // (pmUsable: an extreme that Kalshi and the live model contradict, or the polls when neither exists) is dropped, so Kalshi is used.
  const pmP = (r) => (r.pm ? pmUsable(st.live.has(r.race_key) ? st.live.get(r.race_key) : r.pm.price, [r.kalshi?.price, st.model.get(r.race_id ?? r.race_key) ?? st.model.get(r.race_key)], [r.polls?.p_win]) : null);
  const priceOf = (r) => pmP(r) ?? r.kalshi?.price ?? null;
  const raceIn = (s) => races().filter((r) => r.state === s);

  function draw() {
    if (st.office === "house") { root.querySelector(".nat-legend").innerHTML = ""; closePanel(); return; }
    const colours = new Map(), rings = [];
    for (const r of races()) {
      // states without a race or a price: left out, the map hatches them (no data)
      const p = priceOf(r);
      if (!colours.has(r.state) && p != null) colours.set(r.state, colourOf(p));
      if (modelled(r)) rings.push(r.state);
    }
    onColours?.(colours, rings);
    drawLegend();
    drawSummary();
    if (st.panel) openPanel(st.panel);
  }

  const raceName = (r) => `${r.state_name ?? stateName(r.state)} ${r.office === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`;
  const incumbent = (r) => (r.incumbent ? `${esc(r.incumbent)}${r.incumbent_party ? ` (${esc(r.incumbent_party)})` : ""}${r.open ? ", not running" : ""}` : r.open ? "open seat" : "–");

  // overview polls: { avg (two-party Democratic share), n_polls, last_date, p_win } or null
  const pollsTitle = (p) => (p ? [p.avg != null ? `Polling average ${(100 * p.avg).toFixed(1)}% for the Democratic side (two-party)` : "Polling average", p.n_polls ? `${p.n_polls} poll${p.n_polls === 1 ? "" : "s"}` : "", p.last_date ? `last poll ${fmtDate(p.last_date)}` : ""].filter(Boolean).join(", ") : "No recent polls");
  function cardHTML(r) {
    const m = r.race_id != null ? st.model.get(r.race_id) : st.model.get(r.race_key) ?? null;
    const p = r.polls;
    return `<p><b>${esc(raceName(r))}</b></p>
      <p>Incumbent: ${incumbent(r)}</p>
      <p>Chance ${esc(r.dem_side?.name ?? "the Democratic side")}${r.dem_side?.party && r.dem_side.party !== "D" ? ` (${esc(r.dem_side.party)})` : ""} wins:
        ${m != null ? `<span class="m">${num(m)}</span> model · ` : ""}<span class="k">${num(pmP(r))}</span> Polymarket${st.live.has(r.race_key) ? " now" : ""} · <span class="kx">${num(r.kalshi?.price)}</span> Kalshi${"polls" in r ? ` · ${p ? `<b>${num(p.p_win)}</b> polls` : "no recent polls"}` : ""}</p>
      ${p ? `<p class="pc-hint">${esc(pollsTitle(p))}.</p>` : ""}
      ${r.dem_side?.aligned ? `<p class="pc-hint">${esc(r.dem_side.aligned)}</p>` : ""}
      <p class="pc-hint">${modelled(r) ? `<span class="mk-live" aria-hidden="true"></span> Live county model on election night. Click for the county model.` : `${esc(feedNote(r.state))} ${r.units ? "Click for county estimates (past results shifted by polls)." : "Click for the price history."}`}</p>`;
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
    // every race with a county file drills in (modelled races and the county estimates)
    if (r.units) { closePanel(); onDrill?.(r.race_id ?? r.race_key); return; }
    openPanel(r.race_key);
  }

  function closePanel() { st.panel = null; panel.hidden = true; panel.innerHTML = ""; }
  function openPanel(key) {
    const r = races().find((x) => x.race_key === key);
    if (!r) { closePanel(); return; }
    st.panel = key;
    panel.hidden = false;
    panel.innerHTML = `<button type="button" class="nat-close" aria-label="Close">×</button>${cardHTML(r).replace(/<p class="pc-hint">[^<]*Click[^<]*<\/p>/, "")}
      <div class="nat-hist"></div>
      <p class="note">Prices every 30 minutes over the last 14 days: <span class="k">Polymarket</span>, <span class="kx">Kalshi</span>. ${esc(feedNote(r.state))}</p>`;
    panel.querySelector(".nat-close").addEventListener("click", closePanel);
    history(panel.querySelector(".nat-hist"), r);
  }

  function history(box, r) {
    chart(box, (r.history ?? []).map(([t, pm, k]) => ({ t: new Date(t), pm, k })));
  }

  // 14 days of prices (pm: Polymarket black, k: Kalshi green), the y axis fitted to the prices' range; hover or drag reads a moment
  // into `read` (its default text restored on leaving)
  function chart(box, rows, read) {
    if (rows.length < 2) { box.innerHTML = `<p class="note">No price history yet.</p>`; return; }
    const w = Math.max(240, box.clientWidth || 300), h = 96, m = { l: 30, r: 8, t: 6, b: 18 };
    const x = d3.scaleUtc().domain(d3.extent(rows, (d) => d.t)).range([m.l, w - m.r]);
    const vals = rows.flatMap((d) => [d.pm, d.k]).filter((v) => v != null);
    const lo = Math.max(0, Math.floor((d3.min(vals) - 0.05) * 20) / 20), hi = Math.min(1, Math.ceil((d3.max(vals) + 0.05) * 20) / 20);
    const y = d3.scaleLinear().domain([lo, hi]).range([h - m.b, m.t]);
    const s = d3.select(box).html("").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h).style("touch-action", "pan-y");
    if (lo < 0.5 && hi > 0.5) s.append("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(3).tickFormat(d3.format(".2f")).tickSize(0).tickPadding(4)).call((a) => a.select(".domain").remove());
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(d3.utcWeek.every(1)).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
    const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.t)).y((d) => y(d[k])).curve(d3.curveStepAfter);
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.kalshi).attr("stroke-width", 1.4).attr("d", line("k"));
    s.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.5).attr("d", line("pm"));
    // the reading: a rule and a dot per market at the hovered time (each market's last price at or before it)
    const rule = s.append("line").attr("y1", m.t).attr("y2", h - m.b).attr("stroke", "#9a968c").attr("visibility", "hidden");
    const dots = { pm: s.append("circle").attr("r", 3).attr("fill", C.market).attr("visibility", "hidden"), k: s.append("circle").attr("r", 3).attr("fill", C.kalshi).attr("visibility", "hidden") };
    const last = (k, t) => { let v = null; for (const d of rows) { if (d.t > t) break; if (d[k] != null) v = d[k]; } return v; };
    const dflt = read?.innerHTML ?? "";
    const show = (ev) => {
      const [px] = d3.pointer(ev, s.node()), t = x.invert(Math.max(m.l, Math.min(w - m.r, px)));
      rule.attr("x1", x(t)).attr("x2", x(t)).attr("visibility", "visible");
      const v = { pm: last("pm", t), k: last("k", t) };
      for (const k of ["pm", "k"]) dots[k].attr("cx", x(t)).attr("cy", v[k] == null ? 0 : y(v[k])).attr("visibility", v[k] == null ? "hidden" : "visible");
      if (read) read.innerHTML = `${d3.utcFormat("%-d %b %H:%M UTC")(t)} · <span class="k">Polymarket ${v.pm == null ? "–" : num(v.pm)}</span> · <span class="kx">Kalshi ${v.k == null ? "–" : num(v.k)}</span>`;
    };
    const hide = () => { rule.attr("visibility", "hidden"); dots.pm.attr("visibility", "hidden"); dots.k.attr("visibility", "hidden"); if (read) read.innerHTML = dflt; };
    s.append("rect").attr("x", m.l).attr("y", 0).attr("width", w - m.l - m.r).attr("height", h).attr("fill", "transparent").style("cursor", "crosshair")
      .on("pointermove", show).on("pointerdown", show).on("pointerleave", hide);
  }

  // Chance the Democrats control the Senate: the control markets (live Polymarket price when read)
  function controlMarkets() {
    const sen = st.overview?.offices?.senate;
    return (sen?.control_markets ?? []).map((c) => ({ ...c, now: c.slug && st.live.has(`ctl:${c.slug}`) ? st.live.get(`ctl:${c.slug}`) : c.price }));
  }

  // The office summary beside the map: the Senate's control chances (markets and the model's) and, under them, the projected seat
  // count as a hemicycle (the page draws it into .hemi: onSummary). The races themselves are listed in the Races tab only.
  function drawSummary() {
    if (!summary) return;
    const senate = st.office === "senate", office = senate ? "Senate" : "Governor";
    const markets = senate ? controlMarkets() : [];
    // the model's control chance with poll errors correlated across states (overview, when built)
    const sen = st.overview?.offices?.senate ?? {};
    const ctlHTML = senate && (markets.length || sen.control_p_corr != null) ? `<h3 class="tab-h">Control of the Senate</h3>
      <p>Chance the Democrats control the Senate: ${markets.map((c) => `<span class="${c.platform === "kalshi" ? "kx" : "k"}">${num(c.now)}</span> ${c.platform === "kalshi" ? "Kalshi" : `Polymarket${c.slug && st.live.has(`ctl:${c.slug}`) ? " now" : ""}`}`).join(" · ")}${sen.control_p_corr != null ? `${markets.length ? " · " : ""}<span class="m" title="${esc(sen.control_note ?? "")}">${num(sen.control_p_corr)}</span> model with polls, poll errors correlated across states${sen.control_p_indep != null ? ` (${num(sen.control_p_indep)} if independent)` : ""}` : ""}</p>
` : "";
    const ctlChart = senate && markets.length ? `<div class="nat-ctl-chart"></div><p class="note nat-ctl-read">Control markets, the last 14 days: <span class="k">Polymarket</span>, <span class="kx">Kalshi</span>.</p>` : "";
    summary.innerHTML = `<p class="ro-head"><span class="ro-at"><b>${office}</b> races this year</span></p>
      ${senate ? `<div class="hemi" data-chamber="senate"></div>` : ""}
      ${ctlHTML}${ctlChart}`;
    if (markets.length) {
      const pm = markets.find((c) => c.platform !== "kalshi"), ks = markets.find((c) => c.platform === "kalshi");
      const byT = new Map();
      for (const [t, p] of pm?.history ?? []) byT.set(t, { t: new Date(t), pm: p, k: null });
      for (const [t, p] of ks?.history ?? []) byT.set(t, { ...(byT.get(t) ?? { t: new Date(t), pm: null }), k: p });
      chart(summary.querySelector(".nat-ctl-chart"), [...byT.values()].sort((a, b) => a.t - b.t), summary.querySelector(".nat-ctl-read"));
    }
    onSummary?.();
  }


  function drawLegend() {
    const box = root.querySelector(".nat-legend");
    const w = 200;
    box.innerHTML = `<svg viewBox="0 0 ${w} 24" width="${w}" height="24"><defs><linearGradient id="nat-grad">${d3.range(0, 201, 10).map((j) => `<stop offset="${j / 200}" stop-color="${ramp[200 - j]}"/>`).join("")}</linearGradient></defs>
        <rect width="${w}" height="8" fill="url(#nat-grad)"/><text x="0" y="20" class="legend-text">Democratic side</text><text x="${w}" y="20" text-anchor="end" class="legend-text">Republican</text></svg>
      <p class="legend-count">${(() => {
        const ps = races().map(priceOf).filter((p) => p != null);
        const d = ps.filter((p) => p > 0.5).length, r = ps.filter((p) => p < 0.5).length;
        return `Favoured: <span style="color:${DEM}">Democratic side</span> in <b>${d}</b> · <span style="color:${REP}">Republican</span> in <b>${r}</b> states`;
      })()}</p>
      <p class="legend-note"><span class="lg-none"></span> no ${st.office === "senate" ? "Senate" : "governor"} race or no price · <span class="mk-live"></span> live county model on election night (a marker, not data)</p>
      <p class="legend-note">Colour: market chance (Polymarket, else Kalshi).</p>`;
  }

  const api = {
    draw,
    hover,
    pick,
    // Polymarket's price for a race (live where read), by race_key
    price(key) {
      for (const o of Object.values(st.overview?.offices ?? {})) { const r = o.races?.find((x) => x.race_key === key); if (r) return pmP(r); }
      return null;
    },
    // a modelled state's race for the current office (for prefetching its county file)
    raceFor: (s) => raceIn(s)[0] ?? null,
    get office() { return st.office; },
    // open a race by its overview key (a seat of the hemicycle): its county view where it has one, else its price panel
    open(key) {
      const r = races().find((x) => x.race_key === key);
      if (r?.units) onDrill?.(r.race_id ?? r.race_key);
      else if (r) openPanel(r.race_key);
    },
    // switch office as a click on its button would (the page's ?office= and tab links)
    setOffice(o) { root.querySelector(`.nat-office [data-office="${o}"]`)?.click(); },
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
