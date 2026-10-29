// A second map layer of the US election page (election.html?id=us2026), drawn in SVG over the
// county map in the same map box: the House office (435 districts, data/us2026/house/) and the
// census map modes (counties, data/us2026/hub/census.json). The county canvas stays underneath and
// returns when the layer is off. One layer is shown at a time.
//
// House: the 2026 lines (or the 2024 lines for past results) coloured by the market chance, else
// the district poll average, else the model-only baseline ("best"), or by any one of them, Harris
// 2024, the 2024 House result or early vote; or NYT-style swing arrows by county. A state drill
// zooms to its districts; a district click selects it (the page shows it as the race).
// Margins: Democratic minus Republican, points; chances: P(Democratic party wins the seat).
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { getJSON, esc, num, pct, fmtDate, marginText } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec", ROOT = "us2026/house/";
const ramp = (v) => d3.interpolateLab(NEUTRAL, v >= 0 ? DEM : REP)(Math.min(1, Math.abs(v)) ** 0.8); // v in [-1, 1]
const byP = (p) => (p == null ? null : ramp((p - 0.5) * 2));
const byMargin = (m, full = 30) => (m == null ? null : ramp(m / full));
const EV = d3.interpolateLab("#ece9e1", "#3d5a40");
const day = (d) => (d ? fmtDate(d).replace(/ \d{4}$/, "") : "–");
const ZOOM_MS = 700;

// House colour modes: [key, label, lines ("2026" only or "both"), value(district) -> fill | null, legend]
const MODES = [
  ["best", "Chance", "2026", (d) => byP(best(d)?.p), "Chance the Democratic party wins the seat: the market (Polymarket, else Kalshi), else the district polls, else the baseline (model only); the card says which."],
  ["market", "Market", "2026", (d) => byP(mk(d)), "Market chance: Polymarket, else Kalshi."],
  ["polls", "Polls", "2026", (d) => byMargin(pollM(d), 20), "District poll average (polls ending in the 45 days to election day), two-party margin; full colour at ±20."],
  ["baseline", "Baseline (model only)", "2026", (d) => byP(d.baseline?.p_d), "MODEL ONLY: Harris 2024 shifted by the generic ballot; not a forecast and not traded on."],
  ["pres24", "Harris 2024", "both", (d) => byMargin(d.pres24?.margin), "Harris minus Trump, % of all votes (full colour at ±30); split counties approximated."],
  ["house24", "House 2024", "both", (d) => byMargin(h24(d)?.margin), "2024 House result, D minus R (full colour at ±30); redrawn districts: their core 2024 district."],
  ["ev", "Early vote", "2026", (d) => { const e = H.ev?.districts?.[d.id]; return e ? EV(Math.min(1, e.share_of_2022_total)) : null; }, "Early votes so far as a share of the 2022 total (Texas, Florida, North Carolina)."],
  ["swing", "Swing arrows", "both", () => "#f4f2ec", "Each county's shift from 2020 to 2024 (president): right and red, towards Trump; left and blue, towards Harris; length by the shift."],
];
// census map modes: [key, label, format, diverging]
export const CENSUS = [
  ["change_pct", "Population change 2020–25", (v) => `${v > 0 ? "+" : ""}${num(v, 1)}%`, true],
  ["age65_pct", "Age 65+", (v) => `${num(v, 1)}%`],
  ["ba_plus_pct", "Bachelor's or higher", (v) => `${num(v, 1)}%`],
  ["hispanic_pct", "Hispanic", (v) => `${num(v, 1)}%`],
  ["black_pct", "Black", (v) => `${num(v, 1)}%`],
  ["median_income", "Median household income", (v) => `$${d3.format(",")(Math.round(v))}`],
];

const H = { files: null, data: {}, topo: {} }; // loaded files
const mkt = (id) => H.files?.markets?.districts?.[id] ?? null;
const mk = (d) => mkt(d.id)?.pm?.p ?? mkt(d.id)?.kalshi?.p ?? null;
const pollM = (d) => { const a = mkt(d.id)?.polls?.average; return a?.avg != null ? (2 * a.avg - 1) * 100 : null; };
const h24 = (d) => d.house24 ?? d.house24_core ?? null;
// the chance shown in the "Chance" mode, and where it comes from
export function best(d) {
  const m = mkt(d.id);
  if (m?.pm?.p != null) return { p: m.pm.p, src: "Polymarket" };
  if (m?.kalshi?.p != null) return { p: m.kalshi.p, src: "Kalshi" };
  if (m?.polls?.average?.p_win != null) return { p: m.polls.average.p_win, src: "district polls" };
  if (d.baseline?.p_d != null) return { p: d.baseline.p_d, src: "baseline (model only)" };
  return null;
}

export function loadHouse() {
  H.loading ??= (async () => {
    const opt = { optional: true };
    const [districts, markets, ev, flows] = await Promise.all(["districts.json", "markets.json", "earlyvote.json", "flows.json"].map((f) => getJSON(`${ROOT}${f}`, opt)));
    H.files = { districts, markets, ev, flows };
    H.ev = ev;
    H.data[2026] = districts;
    return H;
  })();
  return H.loading;
}
export const houseDistrict = (id) => (H.files?.districts?.districts?.[id] ? { id, ...H.files.districts.districts[id] } : null);

// container: the map box. onSelect(id | null): a district picked; onState(st | null): the view
// zoomed to a state or back to the country.
// top(): where the layer's controls start (below the page's own controls over the map), px
export function createOverlay(container, { onSelect, onState, top = () => 52 } = {}) {
  const st = { kind: null, mode: "best", lines: "2026", census: CENSUS[0][0], state: null, sel: null, k: { k: 1, x: 0, y: 0 }, w: 0, h: 0, home: null };
  const box = document.createElement("div");
  box.className = "ov";
  box.hidden = true;
  box.innerHTML = `<svg class="ov-svg" role="img"></svg>
    <div class="ov-ctl"><div class="toggle ov-modes" role="group" aria-label="Map colour"></div><div class="toggle ov-lines" role="group" aria-label="District lines"></div></div>
    <div class="ov-legend map-legend"></div><div class="place-card ov-card" hidden></div>`;
  container.appendChild(box);
  const svg = d3.select(box.querySelector("svg"));
  const card = box.querySelector(".ov-card");
  let g = null, path = null, proj = null, features = [];

  async function show(kind, opts = {}) {
    Object.assign(st, opts);
    st.kind = kind;
    box.hidden = false;
    container.classList.add("ov-on");
    if (kind === "house") await loadHouse();
    await draw();
  }
  function hide() {
    st.kind = null;
    box.hidden = true;
    card.hidden = true;
    container.classList.remove("ov-on");
  }

  // the controls of the layer shown
  function controls() {
    const modes = box.querySelector(".ov-modes"), lines = box.querySelector(".ov-lines");
    if (st.kind === "house") {
      const mode = MODES.find((m) => m[0] === st.mode);
      modes.innerHTML = MODES.map(([k, l]) => `<button type="button" data-mode="${k}" aria-pressed="${k === st.mode}">${l}</button>`).join("");
      lines.innerHTML = ["2026", "2024"].map((l) => `<button type="button" data-lines="${l}" aria-pressed="${l === st.lines}"${mode[2] === "2026" && l === "2024" ? " disabled" : ""}>${l} lines</button>`).join("");
      lines.hidden = false;
      modes.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { st.mode = b.dataset.mode; if (MODES.find((m) => m[0] === st.mode)[2] === "2026") st.lines = "2026"; draw(); }));
      lines.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { st.lines = b.dataset.lines; st.sel = null; draw(); }));
    } else {
      modes.innerHTML = "";
      lines.hidden = true;
    }
  }

  async function draw() {
    const token = (st.token = (st.token ?? 0) + 1); // a newer draw (resize, mode) supersedes this one
    controls();
    const ctl = box.querySelector(".ov-ctl");
    ctl.style.top = `${Math.round(top())}px`;
    st.top = ctl.offsetTop + ctl.offsetHeight + 6;
    const r = container.getBoundingClientRect();
    st.w = Math.max(320, r.width); st.h = Math.max(240, r.height);
    let topo, obj, fill, idOf, stOf;
    if (st.kind === "house") {
      await loadHouse(); // a resize can redraw before show() has the files
      if (!H.topo[st.lines]) {
        // the 2026 lines' figures are districts.json (loaded above); the 2024 lines' districts_2024.json
        const [t, d] = await Promise.all([getJSON(`${ROOT}districts_${st.lines}.topo.json`), H.data[st.lines] ? Promise.resolve(H.data[st.lines]) : getJSON(`${ROOT}districts_2024.json`)]);
        H.topo[st.lines] = t; H.data[st.lines] = d;
      }
      topo = H.topo[st.lines]; obj = topo.objects.districts;
      const data = H.data[st.lines].districts, mode = MODES.find((m) => m[0] === st.mode);
      idOf = (f) => f.properties.id; stOf = (f) => f.properties.st.toLowerCase();
      fill = (f) => { const d = data[idOf(f)]; return d ? mode[3]({ id: idOf(f), ...d }) : null; };
      svg.attr("aria-label", `US House districts, ${mode[1]}`);
    } else {
      H.census ??= await getJSON("us2026/hub/census.json", { optional: true });
      H.counties ??= await getJSON("geo/us_counties.topo.json");
      topo = H.counties; obj = topo.objects.counties;
      const [k, label, , div] = CENSUS.find((c) => c[0] === st.census);
      const vals = Object.values(H.census?.counties ?? {}).map((c) => c[k]).filter((v) => v != null);
      const colour = div ? d3.scaleDiverging([-10, 0, 10], d3.interpolateBrBG).clamp(true) : d3.scaleSequentialQuantile(vals, d3.interpolate("#ece9e1", "#3d5a40"));
      idOf = (f) => f.properties?.unit_id ?? f.id; stOf = (f) => idOf(f).split("-")[1];
      fill = (f) => { const v = H.census?.counties?.[idOf(f)]?.[k]; return v == null ? null : colour(v); };
      svg.attr("aria-label", `Counties, ${label}`);
    }
    if (token !== st.token || !st.kind) return;
    svg.attr("viewBox", `0 0 ${st.w} ${st.h}`).attr("width", st.w).attr("height", st.h);
    svg.selectAll("*").remove();
    svg.append("defs").html(`<pattern id="ov-hatch" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#efede7"/><path d="M0 6L6 0M-1 1L1 -1M5 7L7 5" stroke="#c9c6bd" stroke-width="1"/></pattern>`);
    const fc = topojson.feature(topo, obj);
    features = fc.features;
    proj = d3.geoAlbersUsa().fitExtent([[10, st.top], [st.w - 10, st.h - 40]], fc);
    path = d3.geoPath(proj);
    st.home = { k: 1, x: 0, y: 0 };
    g = svg.append("g").attr("class", "ov-g");
    g.selectAll("path.f").data(features).join("path").attr("class", "f").attr("d", path)
      .attr("fill", (f) => fill(f) ?? "url(#ov-hatch)").attr("data-id", idOf).attr("data-fill", (f) => (fill(f) ? "colour" : "hatch"))
      .attr("stroke", "#fff").attr("stroke-width", st.kind === "house" ? 0.5 : 0.2).attr("vector-effect", "non-scaling-stroke")
      .on("pointermove", (ev, f) => hover(ev, idOf(f), stOf(f))).on("pointerleave", () => { card.hidden = true; g.select(".hl").remove(); })
      .on("click", (ev, f) => click(idOf(f), stOf(f)));
    g.append("path").attr("class", "ov-states").datum(topojson.mesh(topo, obj, (a, b) => a !== b && stOf(a) !== stOf(b))).attr("d", path)
      .attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 1.6).attr("vector-effect", "non-scaling-stroke").attr("pointer-events", "none");
    if (st.kind === "house" && st.mode === "swing") await arrows();
    if (st.kind === "house" && st.sel) outline(st.sel, "ov-sel");
    legend();
    // keep the zoomed state after a redraw (mode or lines changed)
    st.k = st.state ? fitState(st.state) : st.home;
    g.attr("transform", tf(st.k));
    g.selectAll(".ov-arrows path").attr("stroke-width", 1 / st.k.k);
  }
  const tf = (v) => `translate(${v.x},${v.y}) scale(${v.k})`;
  function outline(id, cls) {
    const f = features.find((x) => (x.properties?.id ?? x.properties?.unit_id ?? x.id) === id);
    g.select(`.${cls}`).remove();
    if (f) g.append("path").attr("class", cls).datum(f).attr("d", path).attr("fill", "none").attr("stroke", "#5f6368").attr("stroke-width", 1.4).attr("vector-effect", "non-scaling-stroke").attr("pointer-events", "none");
  }

  // NYT-style arrows: one per county at its centroid, horizontal, length by |shift| (full length at
  // 15 points), red to the right (towards Trump), blue to the left (towards Harris)
  async function arrows() {
    H.swing ??= await getJSON(`${ROOT}swing.json`, { optional: true });
    H.counties ??= await getJSON("geo/us_counties.topo.json", { optional: true });
    if (!H.swing || !H.counties) return;
    H.centroids ??= topojson.feature(H.counties, H.counties.objects.counties).features.map((f) => [f.properties?.unit_id ?? f.id, d3.geoCentroid(f)]);
    const L = 22, items = [];
    for (const [id, ll] of H.centroids) {
      const s = H.swing.counties[id];
      if (!s || s[0] == null || s[1] == null) continue;
      const c = proj(ll);
      if (!c) continue;
      const shift = s[1] - s[0]; // + = towards Harris
      if (Math.abs(shift) < 0.25) continue;
      const len = Math.max(2, L * Math.min(1, Math.abs(shift) / 15)), dir = shift > 0 ? -1 : 1;
      items.push(`<path d="M${c[0].toFixed(1)},${c[1].toFixed(1)}h${(dir * len).toFixed(1)}m${(-dir * 3).toFixed(1)},-2l${(dir * 3).toFixed(1)},2l${(-dir * 3).toFixed(1)},2" stroke="${shift > 0 ? DEM : REP}"/>`);
    }
    g.append("g").attr("class", "ov-arrows").attr("fill", "none").attr("stroke-opacity", 0.75).attr("pointer-events", "none").html(items.join(""));
  }

  function legend() {
    const el = box.querySelector(".ov-legend");
    const hatch = `<span class="lg-none"></span> no data`;
    if (st.kind === "house") {
      const mode = MODES.find((m) => m[0] === st.mode);
      el.innerHTML = st.mode === "swing" ? `<p class="legend-note">${esc(mode[4])} <span style="color:${REP}">→ Trump</span> <span style="color:${DEM}">← Harris</span></p>`
        : `<p class="legend-note"><span class="ov-ramp" style="background:linear-gradient(90deg,${st.mode === "ev" ? `${EV(0)},${EV(1)}` : `${REP},${NEUTRAL},${DEM}`})"></span> ${esc(mode[4])} ${hatch}</p>`;
    } else {
      const [, label, , div] = CENSUS.find((c) => c[0] === st.census);
      el.innerHTML = `<p class="legend-note"><span class="ov-ramp" style="background:linear-gradient(90deg,${div ? "#8c510a,#f5f5f5,#01665e" : "#ece9e1,#3d5a40"})"></span> ${esc(label)}: ${div ? "brown, shrinking; teal, growing (full colour at ±10%)" : "darker is higher (by county rank)"}. ${hatch}. ${esc(H.census?.fields?.[st.census] ?? "")}</p>`;
    }
  }

  function cardHTML(id) {
    if (st.kind === "census") {
      const c = H.census?.counties?.[id];
      const f = features.find((x) => (x.properties?.unit_id ?? x.id) === id);
      const [k, label, fmt] = CENSUS.find((x) => x[0] === st.census);
      return `<p class="pc-name"><b>${esc(f?.properties?.name ?? id)}</b></p><p>${esc(label)}: ${c?.[k] != null ? fmt(c[k]) : "no data"}</p>`;
    }
    const d = H.data[st.lines]?.districts?.[id], m = st.lines === "2026" ? mkt(id) : null, e = st.lines === "2026" ? H.ev?.districts?.[id] : null;
    if (!d) return "";
    const h = h24(d), b = st.lines === "2026" ? best({ id, ...d }) : null;
    const rows = [
      b && ["Chance (D)", `${num(b.p)} <small>${esc(b.src)}</small>`],
      m?.pm && ["Polymarket", `${num(m.pm.p)}${m.pm.cands?.D ? ` <small>${esc(m.pm.cands.D)} (D) v ${esc(m.pm.cands.R ?? "–")} (R)</small>` : ""}`],
      m?.kalshi && ["Kalshi", num(m.kalshi.p)],
      pollM({ id }) != null ? ["Poll average", `${marginText(pollM({ id }))} <small>${m.polls.average.n_polls ?? ""} polls</small>`]
        : m?.polls?.polls?.length ? ["Poll average", "<small>no poll in the 45-day window</small>"] : null,
      d.baseline && ["Baseline (model only)", num(d.baseline.p_d)],
      ["Harris 2024", `${marginText(d.pres24?.margin)}${d.pres24?.method && d.pres24.method !== "county" ? " <small>approx.</small>" : ""}`],
      h && [d.house24 ? "House 2024" : `House 2024 (core: ${esc(d.core_2024 ?? "")})`, `${esc(h.winner ?? "")} (${esc(h.winner_party ?? "")}) ${marginText(h.margin)}${h.uncontested ? " <small>uncontested</small>" : ""}`],
      d.swing_pres != null && ["Swing 2020→24 (president)", marginText(d.swing_pres)],
      e && ["Early vote", `${d3.format(",")(e.ev_total)} (${pct(e.share_of_2022_total, 0)} of 2022)`],
    ].filter(Boolean);
    return `<p class="pc-name"><b>${esc(id)}</b>${d.redrawn ? " <small>new lines</small>" : ""}</p><table class="kv"><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="n">${v}</td></tr>`).join("")}</tbody></table><p class="pc-hint">${st.state ? "Click to select." : "Click for the state's districts."}</p>`;
  }
  function hover(ev, id, s) {
    const html = cardHTML(id);
    if (!html) { card.hidden = true; return; }
    card.innerHTML = html;
    card.hidden = false;
    const b = box.getBoundingClientRect();
    const x = ev.clientX - b.left, y = ev.clientY - b.top;
    card.style.left = `${Math.max(4, Math.min(b.width - card.offsetWidth - 4, x + 14))}px`;
    card.style.top = `${Math.max(4, Math.min(b.height - card.offsetHeight - 4, y + 14))}px`;
    outline(id, "hl");
  }
  // at the country level a click opens the state; inside a state it selects the district (House)
  function click(id, s) {
    card.hidden = true;
    if (st.state !== s) { focus(s); if (st.kind === "house") select(id); return; }
    if (st.kind === "house") select(id);
  }
  function select(id) {
    st.sel = id;
    if (g) outline(id, "ov-sel");
    onSelect?.(id);
  }

  // the view that fits a state's features (null: the whole country)
  function fitState(s) {
    const fs = features.filter((f) => (st.kind === "house" ? f.properties.st.toLowerCase() : (f.properties?.unit_id ?? f.id).split("-")[1]) === s);
    if (!fs.length) return st.home;
    const [[x0, y0], [x1, y1]] = path.bounds({ type: "FeatureCollection", features: fs });
    const k = Math.min(40, 0.9 * Math.min(st.w / Math.max(1, x1 - x0), (st.h - 40 - st.top) / Math.max(1, y1 - y0)));
    return { k, x: st.w / 2 - k * (x0 + x1) / 2, y: (st.top + st.h - 40) / 2 - k * (y0 + y1) / 2 };
  }
  // zoom to a state, or back to the whole country (null), animated
  function focus(s, animate = true) {
    st.state = s || null;
    if (!s) { st.sel = null; g?.select(".ov-sel").remove(); }
    const to = s ? fitState(s) : st.home;
    if (!g) return;
    const from = st.k, ip = d3.interpolateObject(from, to);
    st.k = to;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!animate || reduce) g.attr("transform", tf(to));
    else g.transition().duration(ZOOM_MS).ease(d3.easeCubicInOut).attrTween("transform", () => (t) => tf(ip(t)));
    onState?.(st.state);
  }

  return {
    show, hide, focus, select,
    get kind() { return st.kind; },
    get state() { return st.state; },
    get selected() { return st.sel; },
    setCensus(k) { st.census = k; if (st.kind === "census") draw(); },
    resize() { if (st.kind) draw(); },
    // for the map checks: what is drawn, the view and a card's text
    audit: () => ({
      kind: st.kind, mode: st.mode, lines: st.lines, state: st.state, sel: st.sel, view: { ...st.k }, home: { ...st.home },
      n: features.length, colour: g ? g.selectAll("path.f[data-fill=colour]").size() : 0, hatch: g ? g.selectAll("path.f[data-fill=hatch]").size() : 0,
      ids: features.map((f) => f.properties?.id ?? f.properties?.unit_id ?? f.id),
    }),
    cardText: (id) => { const d = document.createElement("div"); d.innerHTML = cardHTML(id); return [...d.children].map((c) => c.textContent).join("\n"); },
    setMode(m) { st.mode = m; return draw(); },
  };
}

// The selected district for the page's readout: its figures (chips), and a strip chart of the
// chances (markets, polls, baseline) on one 0-1 axis.
export function districtReadout(id) {
  const d = houseDistrict(id), m = mkt(id), e = H.ev?.districts?.[id];
  if (!d) return `<p class="note">No data for ${esc(id)}.</p>`;
  const h = h24(d), b = best(d), F = H.files?.flows?.states?.[d.st];
  const flow = F?.districts?.[id];
  const from = (F?.flows ?? []).filter((f) => f[1] === id).sort((a, x) => x[4] - a[4]).slice(0, 3).map((f) => `${esc(f[0])} ${pct(f[4], 0)}`).join(", ");
  const chip = (label, v, cls = "") => `<span class="chip"><small>${label}</small><b class="${cls}">${v}</b></span>`;
  return `<p class="ro-head"><span class="ro-at"><b>${esc(id)}</b>${d.redrawn ? " · new lines" : ""}${h?.incumbent ? ` · incumbent ${esc(h.incumbent)} (${esc(h.incumbent_party ?? "")})` : ""}</span><span class="ro-race">chance the Democratic party wins</span></p>
    <div class="chips">${chip("Chance", b ? `${num(b.p)}` : "–")}${chip("Polymarket", num(m?.pm?.p), "k")}${chip("Kalshi", num(m?.kalshi?.p), "kx")}${chip("Polls", pollM(d) != null ? marginText(pollM(d)) : m?.polls?.polls?.length ? "none in 45 days" : "–")}${chip("Baseline (model only)", num(d.baseline?.p_d))}${chip("Harris 2024", marginText(d.pres24?.margin))}${chip("House 2024", h ? `${esc(h.winner_party ?? "")} ${marginText(h.margin)}` : "–")}</div>
    <p class="note">${b ? `Chance from ${esc(b.src)}.` : ""} ${m?.pm?.cands?.D ? `${esc(m.pm.cands.D)} (D) v ${esc(m.pm.cands.R ?? "–")} (R).` : ""}${flow ? ` New lines: core 2024 district ${esc(flow.core_2024 ?? "")} (Harris ${marginText(flow.core_margin)}), lean change ${marginText(flow.lean_change)}; made from ${from}.` : ""}${d.swing_pres != null ? ` Swing 2020→24: ${marginText(d.swing_pres)}.` : ""}${e ? ` Early vote ${d3.format(",")(e.ev_total)} (${pct(e.share_of_2022_total, 0)} of 2022).` : ""}</p>`;
}
export function districtChart(el, id) {
  const d = houseDistrict(id), m = mkt(id);
  const pts = [["Polymarket", m?.pm?.p, "#1b1b1b"], ["Kalshi", m?.kalshi?.p, "#2f8a6c"], ["District polls", m?.polls?.average?.p_win, "#6a7f99"], ["Baseline (model only)", d?.baseline?.p_d, "#c4501b"]].filter((x) => x[1] != null);
  const w = Math.max(260, el.clientWidth || 400), rowH = 22, h = 24 + rowH * Math.max(1, pts.length), m0 = { l: 150, r: 14 };
  const x = d3.scaleLinear([0, 1], [m0.l, w - m0.r]);
  el.innerHTML = `<h3 class="tab-h">${esc(id)}: chance the Democratic party wins</h3><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" class="ov-strip">
    <line x1="${x(0.5)}" x2="${x(0.5)}" y1="4" y2="${h - 18}" stroke="#d6d3cb" stroke-dasharray="3,3"/>
    ${pts.map(([l, p, c], i) => `<text x="${m0.l - 8}" y="${16 + i * rowH}" text-anchor="end" class="legend-text">${esc(l)}</text><line x1="${x(0)}" x2="${x(1)}" y1="${12 + i * rowH}" y2="${12 + i * rowH}" stroke="#eceae4"/><circle cx="${x(p)}" cy="${12 + i * rowH}" r="5" fill="${c}"/><text x="${x(p) + 8}" y="${16 + i * rowH}" class="legend-text">${num(p)}</text>`).join("")}
    ${[0, 0.5, 1].map((t) => `<text x="${x(t)}" y="${h - 4}" text-anchor="middle" class="legend-text">${t}</text>`).join("")}</svg>
    ${m?.polls?.polls?.length ? `<p class="note">District polls: ${m.polls.polls.map((p) => `${esc(p.pollster)} ${esc(day(p.end))} ${marginText(p.d2 != null ? (2 * p.d2 - 1) * 100 : null)}`).join(" · ")}</p>` : ""}`;
}

// The House races list (the summary beside the map at the country level): every district,
// competitive first, filterable; and, for a redrawn state, its old and new seats.
export function houseList(el, { state = null, onPick }) {
  const D = Object.keys(H.files?.districts?.districts ?? {}).map(houseDistrict).filter((d) => !state || d.st.toLowerCase() === state);
  const rows = D.map((d) => ({ d, b: best(d) })).sort((a, b) => Math.abs((a.b?.p ?? 0) - 0.5) - Math.abs((b.b?.p ?? 0) - 0.5) || a.d.id.localeCompare(b.d.id));
  const gb = H.files?.markets?.generic_ballot ?? H.files?.districts?.national?.generic_ballot;
  const ps = D.map((d) => mk(d)).filter((p) => p != null);
  const F = state ? H.files?.flows?.states?.[state.toUpperCase()] : null;
  el.innerHTML = `<p class="ro-head"><span class="ro-at"><b>House</b> ${state ? `· ${esc(state.toUpperCase())} districts` : "districts this year"}</span><span class="ro-race">chance the Democratic party wins</span></p>
    ${state ? "" : `<p class="nat-sum-ctl">Generic ballot: Democrats ${pct(gb?.avg)} of the two-party vote (± ${(100 * (gb?.sd ?? 0)).toFixed(1)}). Markets: Democrats favoured in <b>${ps.filter((p) => p > 0.5).length}</b> of ${ps.length} priced seats, about <b>${d3.sum(ps).toFixed(0)}</b> expected; 218 for a majority.</p>`}
    ${F ? `<p class="nat-sum-ctl">Redrawn: seats Harris won ${F.old_seats.harris_won} of ${F.old_seats.districts} on the 2024 lines, ${F.new_seats.harris_won} on the new ones (expected Democratic seats at the 2024 vote ${num(F.old_seats.expected_d, 1)} → ${num(F.new_seats.expected_d, 1)}); Democrats won ${F.house24_won_d} in 2024.</p>` : ""}
    <label class="select"><span>District</span> <input type="search" class="hl-filter" placeholder="TX-07, Fletcher…" aria-label="Filter districts"></label>
    <div class="hub-scroll hl-box"><table class="races nat-races hl-table"><thead><tr><th>District</th><th>Incumbent</th><th class="n">Chance</th><th class="n">Harris 2024</th></tr></thead><tbody></tbody></table></div>
    <p class="note">Most competitive first (chance nearest 0.5). Chance: the market (Polymarket, else Kalshi), else district polls, else the baseline (model only).</p>`;
  const body = el.querySelector("tbody"), filter = el.querySelector(".hl-filter");
  const fill = () => {
    const f = filter.value.trim().toLowerCase();
    body.innerHTML = rows.filter(({ d }) => !f || d.id.toLowerCase().includes(f) || (h24(d)?.incumbent ?? "").toLowerCase().includes(f)).slice(0, 120)
      .map(({ d, b }) => `<tr tabindex="0" data-id="${esc(d.id)}"><td>${esc(d.id)}${d.redrawn ? " <small>new</small>" : ""}</td><td>${esc(h24(d)?.incumbent ?? "–")}</td><td class="n" title="${esc(b?.src ?? "")}">${num(b?.p)}</td><td class="n">${marginText(d.pres24?.margin)}</td></tr>`).join("");
  };
  filter.addEventListener("input", fill);
  body.addEventListener("click", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr) onPick?.(tr.dataset.id); });
  body.addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr && ev.key === "Enter") onPick?.(tr.dataset.id); });
  fill();
}
