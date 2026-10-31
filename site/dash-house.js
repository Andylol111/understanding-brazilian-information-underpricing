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
import { getJSON, STATIC, esc, num, pct, fmtDate, marginText, probabilityLead, splitChanceHTML, sideLabel } from "./common.js";

const DEM = "#1f5fbf", REP = "#c8102e", NEUTRAL = "#f4f2ec", ROOT = "us2026/house/";
const ramp = (v) => d3.interpolateLab(NEUTRAL, v >= 0 ? DEM : REP)(Math.min(1, Math.abs(v)) ** 0.8); // v in [-1, 1]
const byP = (p) => (p == null ? null : ramp((p - 0.5) * 2));
const byMargin = (m, full = 30) => (m == null ? null : ramp(m / full));
const EV = d3.interpolateLab("#ece9e1", "#3d5a40");
const day = (d) => (d ? fmtDate(d).replace(/ ([A-Za-z]+)(?: \d{4})?$/, (_, month) => ` ${month.slice(0, 3)}`) : "–");
const ZOOM_MS = 700, FADE_MS = 300;
const PAPER = "#fcfbf8", VEIL = 0.6; // states outside the open one recede this far toward the paper (as dash-engine.js)
// swing arrows: what each arrow compares
// each county arrow's baseline is its race's (meta races[].baseline, else meta.baseline_estimate); the card names it
export const ARROWS = [["proj", "2026 projected vs past result"], ["past", "2020→2024 (past)"], ["live", "Counted vs past result"]];
const ARROW_FULL = 15, ARROW_L = 22; // points of shift for the longest arrow, its length in px

// House colour modes: [key, label, lines ("2026" only or "both"), value(district) -> fill | null, legend]
const MODES = [
  ["best", "Chance", "2026", (d) => byP(best(d)?.p), "Favoured party and its chance: the market (Polymarket, else Kalshi), else the district polls, else the model; deeper blue or red means a stronger favourite."],
  ["market", "Market", "2026", (d) => byP(mk(d)), "Market favourite and chance: Polymarket, else Kalshi."],
  ["polls", "Polls", "2026", (d) => byMargin(pollM(d), 20), "District poll average (polls ending in the 45 days to election day), two-party margin; full colour at ±20."],
  ["baseline", "Model", "2026", (d) => byP(d.baseline?.p_d), "Model favourite and chance; deeper blue or red means more confidence."],
  ["pres24", "Harris 2024", "both", (d) => byMargin(d.pres24?.margin), "Harris minus Trump, % of all votes (full colour at ±30); split counties approximated."],
  ["house24", "House 2024", "both", (d) => byMargin(h24(d)?.margin), "2024 House result, D minus R (full colour at ±30); redrawn districts: their core 2024 district."],
  ["ev", "Early vote", "2026", (d) => { const e = H.ev?.districts?.[d.id]; return e ? EV(Math.min(1, e.share_of_2022_total)) : null; }, "Early votes so far as a share of the 2022 total (Texas, Florida, North Carolina)."],
  ["swing", "Arrows", "both", () => "#f4f2ec", ""],
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
  if (d.baseline?.p_d != null) return { p: d.baseline.p_d, src: "Model" };
  return null;
}
// A district's projected 2026 margin (two-party, D minus R, points) and its source: the model, else the
// district poll average. Market prices are never read as margins here: near 0 or 1 a price says who wins,
// not by how much (a 0.97 seat reads as ~D+19 whatever the real margin), which drew false swings.
export function projMargin(d) {
  if (d.baseline?.share != null) return { v: (2 * d.baseline.share - 1) * 100, src: "Model" };
  const a = mkt(d.id)?.polls?.average?.avg;
  if (a != null) return { v: (2 * a - 1) * 100, src: "district polls" };
  return null;
}
// Harris 2024 two-party margin on the district's lines
const pres2p = (d) => (d.pres24?.d2 != null ? (2 * d.pres24.d2 - 1) * 100 : null);
// County figures of an office's races (overview → units files), as shifts from the county's
// baseline (the file's prior: the past certified two-party share the race's model shifts, named per
// race in meta.json): the latest projection (with polls) and, once counting, the counted share;
// two-party margins, points. base: county → its race's baseline label; bases: the labels in use.
async function countyRaces(office) {
  H.cproj ??= {};
  if (H.cproj[office]) return H.cproj[office];
  H.overview ??= await getJSON("us2026/overview.json", { optional: true });
  H.meta ??= await getJSON("us2026/meta.json", { optional: true });
  const label = new Map((H.meta?.races ?? []).map((r) => [r.race_id, r.baseline]));
  const proj = new Map(), counted = new Map(), base = new Map(), bases = new Map();
  await Promise.all((H.overview?.offices?.[office]?.races ?? []).filter((r) => r.units).map(async (r) => {
    const U = await getJSON(`us2026/${r.units}`, { optional: true }).catch(() => null);
    const fl = U?.frames?.at?.(-1);
    if (!fl) return;
    const lb = label.get(r.race_id ?? r.race_key) ?? H.meta?.baseline_estimate ?? "the race's past result";
    bases.set(lb, [...(bases.get(lb) ?? []), r.state ?? r.race_key?.slice(0, 2)]);
    U.unit_ids.forEach((id, j) => {
      const v = fl.proj_poll?.[j] ?? fl.proj?.[j];
      const b = U.prior?.[j];
      if (!(b >= 0)) return;
      base.set(id, lb);
      if (v >= 0) proj.set(id, (v - b) / 5); // ‰ of the two-party vote → margin points: × 2 / 10
      if (fl.f?.[j] > 0 && fl.a?.[j] >= 0) counted.set(id, (fl.a[j] - b) / 5);
    });
  }));
  return (H.cproj[office] = { proj, counted, base, bases });
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
export const houseDistrictIds = () => Object.keys(H.files?.districts?.districts ?? {}).sort();

// container: the map box. onSelect(id | null): a district picked; onState(st | null): the view
// zoomed to a state or back to the country. top(): where the layer's controls start (below the
// page's own controls over the map), px.
// kind: "house" (districts), "census" (counties by a census figure) or "arrows" (counties as a
// ground for swing arrows; office "senate" | "governor" for the projected and counted arrows).
export function createOverlay(container, { onSelect, onState, modelOf = () => null, top = () => 52 } = {}) {
  const st = { kind: null, mode: "best", lines: "2026", census: CENSUS[0][0], arrows: "proj", office: "senate", state: null, sel: null, k: { k: 1, x: 0, y: 0 }, w: 0, h: 0, home: null, geom: null };
  const box = document.createElement("div");
  box.className = "ov";
  box.hidden = true;
  box.innerHTML = `<svg class="ov-svg" role="img"></svg>
    <div class="ov-ctl"><div class="toggle ov-modes" role="group" aria-label="Map colour"></div><div class="toggle ov-lines" role="group" aria-label="District lines"></div><div class="toggle ov-arrsel" role="group" aria-label="Arrows compare"></div></div>
    <div class="ov-legend map-legend"></div><div class="place-card ov-card" hidden></div>`;
  container.appendChild(box);
  const svg = d3.select(box.querySelector("svg"));
  const card = box.querySelector(".ov-card");
  let g = null, path = null, proj = null, features = [], arrowData = new Map();
  const reduce = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const arrowsOn = () => st.kind === "arrows" || (st.kind === "house" && st.mode === "swing");

  // the layer fades in over the county map once drawn, and fades out (CSS opacity, FADE_MS)
  async function show(kind, opts = {}) {
    Object.assign(st, opts);
    st.kind = kind;
    if (kind === "house") await loadHouse();
    await draw();
    if (st.kind !== kind) return;
    box.hidden = false;
    container.classList.add("ov-on");
    requestAnimationFrame(() => box.classList.add("in"));
  }
  function hide() {
    st.kind = null;
    card.hidden = true;
    box.classList.remove("in");
    container.classList.remove("ov-on");
    setTimeout(() => { if (!st.kind) box.hidden = true; }, reduce() ? 0 : FADE_MS + 20);
  }

  // the controls of the layer shown
  function controls() {
    const modes = box.querySelector(".ov-modes"), lines = box.querySelector(".ov-lines"), arr = box.querySelector(".ov-arrsel");
    if (st.kind === "house") {
      const mode = MODES.find((m) => m[0] === st.mode);
      const only26 = mode[2] === "2026" || (st.mode === "swing" && st.arrows === "proj");
      modes.innerHTML = MODES.map(([k, l]) => `<button type="button" data-mode="${k}" aria-pressed="${k === st.mode}">${l}</button>`).join("");
      lines.innerHTML = ["2026", "2024"].map((l) => `<button type="button" data-lines="${l}" aria-pressed="${l === st.lines}"${only26 && l === "2024" ? " disabled" : ""}>${l} lines</button>`).join("");
      lines.hidden = false;
      modes.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { st.mode = b.dataset.mode; if (MODES.find((m) => m[0] === st.mode)[2] === "2026" || (st.mode === "swing" && st.arrows === "proj")) st.lines = "2026"; draw(); }));
      lines.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { st.lines = b.dataset.lines; st.sel = null; draw(); }));
    } else {
      modes.innerHTML = "";
      lines.hidden = true;
    }
    arr.hidden = !arrowsOn();
    if (arrowsOn()) {
      const live = st.kind === "arrows" && (H.cproj?.[st.office]?.counted.size ?? 0) > 0;
      // House districts compare with Harris 2024 on the same lines; counties with their race's baseline
      const label = (k, l) => (st.kind === "house" && k === "proj" ? "2026 projected vs Harris 2024" : l);
      arr.innerHTML = ARROWS.map(([k, l]) => `<button type="button" data-arr="${k}" aria-pressed="${k === st.arrows}"${k === "live" && !live ? ` disabled title="Once counting starts${st.kind === "house" ? " (counties of the Senate and governor races; no district count is published)" : ""}"` : ""}>${label(k, l)}</button>`).join("");
      arr.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { st.arrows = b.dataset.arr; if (st.arrows === "proj") st.lines = "2026"; draw(); }));
    }
  }

  // The geometry of a layer kind: the districts on the given lines, or the counties.
  async function geometry(kind, lines) {
    if (kind === "house") {
      await loadHouse();
      if (!H.topo[lines]) {
        // the 2026 lines' figures are districts.json (loaded with the House files); the 2024 lines' districts_2024.json
        const [t, d] = await Promise.all([getJSON(`geo/us_house_${lines}.topo.json`, { root: STATIC }), H.data[lines] ? Promise.resolve(H.data[lines]) : getJSON(`${ROOT}districts_2024.json`)]);
        H.topo[lines] = t; H.data[lines] = d;
      }
      const topo = H.topo[lines];
      return { topo, obj: topo.objects.districts, geom: `districts${lines}`, idOf: (f) => f.properties.id, stOf: (f) => f.properties.st.toLowerCase() };
    }
    H.counties ??= await getJSON("geo/us_counties.topo.json", { root: STATIC }); // geometry: never on the live server
    const idOf = (f) => f.properties?.unit_id ?? f.id;
    return { topo: H.counties, obj: H.counties.objects.counties, geom: "counties", idOf, stOf: (f) => idOf(f).split("-")[1] };
  }
  // the map's top edge: room for the page's controls and this layer's own. A fixed share of the box
  // (not the controls' measured height, which changes with the office) so a layer built ahead of
  // use still fits when shown
  const fitTop = () => Math.round(Math.min(130, 0.22 * container.getBoundingClientRect().height));
  function place() {
    box.querySelector(".ov-ctl").style.top = `${Math.round(top())}px`;
    st.top = fitTop();
    const r = container.getBoundingClientRect();
    return [Math.max(320, r.width), Math.max(240, r.height)];
  }

  // Draw the layer. Each geometry (districts on a set of lines, counties) is built once per size and
  // kept: switching between them cross-fades one built layer over the other, and a new colouring of
  // the same layer cross-fades its fills (no rebuild, no blank frame); the arrows fade likewise.
  async function draw() {
    const token = (st.token = (st.token ?? 0) + 1); // a newer draw (resize, mode) supersedes this one
    if (st.kind === "arrows" && st.arrows !== "past") await countyRaces(st.office);
    const G = await geometry(st.kind, st.lines);
    controls();
    const [w, h] = place();
    let fill;
    if (st.kind === "house") {
      const data = H.data[st.lines].districts, mode = MODES.find((m) => m[0] === st.mode);
      fill = (f) => { const id = G.idOf(f), d = data[id]; return d ? (st.mode === "baseline" ? byP(ours({ id, ...d }, modelOf(id))?.p) : mode[3]({ id, ...d })) : null; };
      svg.attr("aria-label", `US House districts, ${mode[1]}`);
    } else if (st.kind === "census") {
      H.census ??= await getJSON("us2026/hub/census.json", { optional: true });
      const [k, label, , div] = CENSUS.find((c) => c[0] === st.census);
      const vals = Object.values(H.census?.counties ?? {}).map((c) => c[k]).filter((v) => v != null);
      const colour = div ? d3.scaleDiverging([-10, 0, 10], d3.interpolateBrBG).clamp(true) : d3.scaleSequentialQuantile(vals, d3.interpolate("#ece9e1", "#3d5a40"));
      fill = (f) => { const v = H.census?.counties?.[G.idOf(f)]?.[k]; return v == null ? null : colour(v); };
      svg.attr("aria-label", `Counties, ${label}`);
    } else {
      fill = () => NEUTRAL;
      svg.attr("aria-label", "Counties, swing arrows");
    }
    if (arrowsOn()) await arrowValues();
    if (token !== st.token || !st.kind) return;
    st.w = w; st.h = h;
    const L = layer(G, w, h);
    const prev = g, switched = prev !== L.g;
    g = L.g; features = L.features; proj = L.proj; path = L.path; st.geom = G.geom;
    // the fills: cross-fade on the layer already shown, set at once on a layer coming in
    const sel = g.selectAll("path.f").attr("data-fill", (f) => (fill(f) ? "colour" : "hatch"));
    (switched || reduce() || box.hidden ? sel : sel.transition().duration(FADE_MS)).attr("fill", (f) => fill(f) ?? "url(#ov-hatch)");
    if (switched) {
      st.k = st.state ? fitState(st.state) : st.home;
      g.attr("transform", tf(st.k)).style("display", null).attr("pointer-events", null);
      if (prev && !reduce() && !box.hidden) {
        g.attr("opacity", 0).transition().duration(FADE_MS).attr("opacity", 1);
        prev.attr("pointer-events", "none").transition().duration(FADE_MS).attr("opacity", 0).on("end", () => prev.style("display", "none").attr("opacity", 1));
      } else {
        g.attr("opacity", 1);
        prev?.style("display", "none");
      }
    }
    drawArrows(switched);
    mute();
    if (st.kind === "house" && st.sel) outline(st.sel, "ov-sel");
    legend();
  }
  // a built layer for a geometry at this size (from the cache, or built now, hidden)
  const layers = new Map();
  function layer(G, w, h) {
    let L = layers.get(G.geom);
    if (L && L.w === w && L.h === h && L.top === st.top) return L;
    if (L) { if (L.g === g) g = null; L.g.remove(); }
    svg.attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
    if (svg.select("defs").empty()) svg.append("defs").html(`<pattern id="ov-hatch" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#f1efea"/><path d="M0 6L6 0M-1 1L1 -1M5 7L7 5" stroke="#dcd9d1" stroke-width="1"/></pattern>`);
    const fc = topojson.feature(G.topo, G.obj);
    const pr = d3.geoAlbersUsa().fitExtent([[10, st.top], [w - 10, h - 40]], fc), pa = d3.geoPath(pr);
    const lg = svg.append("g").attr("class", "ov-g").style("display", "none");
    lg.selectAll("path.f").data(fc.features).join("path").attr("class", "f").attr("d", pa).attr("fill", NEUTRAL).attr("data-id", G.idOf)
      .attr("stroke", "#fff").attr("stroke-width", G.geom === "counties" ? 0.2 : 0.5).attr("vector-effect", "non-scaling-stroke")
      .on("pointermove", (ev, f) => hover(ev, G.idOf(f), G.stOf(f))).on("pointerleave", () => { card.hidden = true; g?.select(".hl").remove(); })
      .on("click", (ev, f) => click(G.idOf(f), G.stOf(f)));
    lg.append("path").attr("class", "ov-states").datum(topojson.mesh(G.topo, G.obj, (a, b) => a !== b && G.stOf(a) !== G.stOf(b))).attr("d", pa)
      .attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 1.6).attr("vector-effect", "non-scaling-stroke").attr("pointer-events", "none");
    // centroids for the arrows: districts by id, in this layer's coordinates
    const centroids = G.geom.startsWith("districts") ? new Map(fc.features.map((f) => [G.idOf(f), pa.centroid(f)])) : null;
    L = { g: lg, features: fc.features, proj: pr, path: pa, w, h, top: st.top, centroids, stOf: (f) => String(G.stOf(f)).toLowerCase() };
    layers.set(G.geom, L);
    st.home = { k: 1, x: 0, y: 0 };
    return L;
  }
  // Build a layer ahead of use (when the page is idle), so switching to it is only a cross-fade.
  async function prepare(kind) {
    const G = await geometry(kind, "2026");
    const r = container.getBoundingClientRect();
    if (r.width < 10 || st.kind) return;
    st.top = fitTop();
    layer(G, Math.max(320, r.width), Math.max(240, r.height));
    if (kind !== "house") H.centroids ??= topojson.feature(H.counties, H.counties.objects.counties).features.map((f) => [f.properties?.unit_id ?? f.id, d3.geoCentroid(f)]);
  }
  const tf = (v) => `translate(${v.x},${v.y}) scale(${v.k})`;
  function outline(id, cls) {
    const f = features.find((x) => (x.properties?.id ?? x.properties?.unit_id ?? x.id) === id);
    g.select(`.${cls}`).remove();
    if (f) g.append("path").attr("class", cls).datum(f).attr("d", path).attr("fill", "none").attr("stroke", "#5f6368").attr("stroke-width", 1.4).attr("vector-effect", "non-scaling-stroke").attr("pointer-events", "none");
  }

  // The value behind each arrow, by district or county id: { shift (points, + = towards the
  // Democrats), src, at }. proj: House districts, the projected 2026 margin minus Harris 2024 on the
  // same lines; counties, the race's county projection minus the 2024 president margin. past: 2020
  // → 2024 president by county. live: the counted share so far minus the 2024 president margin.
  async function arrowValues() {
    arrowData = new Map();
    if (st.kind === "house" && st.arrows === "proj") {
      for (const [id, d] of Object.entries(H.data[2026]?.districts ?? {})) {
        const pm = projMargin({ id, ...d }), p24 = pres2p(d);
        if (pm && p24 != null) arrowData.set(id, { shift: pm.v - p24, src: `${pm.src} vs Harris 2024`, at: "district" });
      }
      return;
    }
    H.swing ??= await getJSON(`${ROOT}swing.json`, { optional: true });
    H.counties ??= await getJSON("geo/us_counties.topo.json", { root: STATIC }); // county centroids for the arrows
    if (st.arrows === "past") {
      for (const [id, s] of Object.entries(H.swing?.counties ?? {})) if (s?.[0] != null && s?.[1] != null) arrowData.set(id, { shift: s[1] - s[0], src: "2020 → 2024 president", at: "county" });
      return;
    }
    if (st.kind === "house") return; // live: no district count is published
    const c = await countyRaces(st.office);
    for (const [id, shift] of st.arrows === "live" ? c.counted : c.proj) {
      arrowData.set(id, { shift, src: `${st.arrows === "live" ? "counted so far" : "projected (model)"} vs ${c.base.get(id)}, two-party`, at: "county" });
    }
  }
  // NYT-style arrows, horizontal: length by |shift| (full at ARROW_FULL points), blue to the left
  // (towards the Democrats), red to the right (towards the Republicans), at each district's or
  // county's centroid. The old set fades out as the new one fades in.
  function drawArrows(fresh) {
    const old = g.selectAll(".ov-arrows");
    if (reduce() || fresh) old.remove(); else old.transition().duration(FADE_MS).attr("opacity", 0).remove();
    if (!arrowsOn() || !arrowData.size) return;
    const pts = [];
    if ([...arrowData.values()][0].at === "district") {
      const cs = layers.get(st.geom)?.centroids;
      for (const [id, a] of arrowData) { const c = cs?.get(id); if (c) pts.push([c, a.shift, id.slice(0, 2).toLowerCase()]); }
    } else {
      H.centroids ??= topojson.feature(H.counties, H.counties.objects.counties).features.map((f) => [f.properties?.unit_id ?? f.id, d3.geoCentroid(f)]);
      for (const [id, ll] of H.centroids) { const a = arrowData.get(id); if (a) pts.push([proj(ll), a.shift, id.split("-")[1]]); }
    }
    const items = [];
    for (const [c, shift, s] of pts) {
      if (!c || !Number.isFinite(c[0]) || Math.abs(shift) < 0.25) continue;
      const len = Math.max(2, ARROW_L * Math.min(1, Math.abs(shift) / ARROW_FULL)), dir = shift > 0 ? -1 : 1;
      items.push(`<path data-st="${s}" d="M${c[0].toFixed(1)},${c[1].toFixed(1)}h${(dir * len).toFixed(1)}m${(-dir * 3).toFixed(1)},-2l${(dir * 3).toFixed(1)},2l${(-dir * 3).toFixed(1)},2" stroke="${shift > 0 ? DEM : REP}"/>`);
    }
    const a = g.append("g").attr("class", "ov-arrows").attr("fill", "none").attr("stroke-opacity", 0.8).attr("pointer-events", "none")
      .attr("stroke-width", 1 / st.k.k).attr("opacity", reduce() || fresh ? 1 : 0).html(items.join(""));
    if (!reduce() && !fresh) a.transition().duration(FADE_MS).attr("opacity", 1);
  }
  // inside a state the others stay but recede, as on the map canvas (dash-engine.js veil): one
  // shape over everything but the state's own areas, greyed (saturation blend) and 60% toward the
  // paper, faded in with the zoom; their arrows fade too
  function mute() {
    const L = layers.get(st.geom);
    if (!g || !L) return;
    const s = st.state;
    g.selectAll(".ov-arrows path").classed("ov-out", function () { return !!s && this.dataset.st !== s; });
    if ((g.property("__veil") ?? null) === s) return;
    g.property("__veil", s);
    const quick = reduce() || box.hidden;
    const old = g.selectAll(".ov-veil path");
    if (quick) g.selectAll(".ov-veil").remove();
    else old.interrupt().transition().duration(ZOOM_MS).attr("opacity", 0).on("end", function () { this.parentNode?.remove(); });
    if (!s) return;
    const d = `M${-4 * L.w},${-4 * L.h}H${5 * L.w}V${5 * L.h}H${-4 * L.w}Z` + L.features.filter((f) => L.stOf(f) === s).map((f) => L.path(f) ?? "").join("");
    const v = g.insert("g", ".ov-states").attr("class", "ov-veil").attr("pointer-events", "none");
    for (const [fill, op, blend] of [["#808080", 0.8, "saturation"], [PAPER, VEIL, null]]) {
      const p = v.append("path").attr("d", d).attr("fill-rule", "evenodd").attr("fill", fill).style("mix-blend-mode", blend).attr("opacity", quick ? op : 0);
      if (!quick) p.transition().duration(ZOOM_MS).attr("opacity", op);
    }
  }

  // "; baselines: GA, NC: 2022 Senate (certified); …; other states: 2024 president (certified)" for the office shown
  function baselines() {
    const b = H.cproj?.[st.office]?.bases;
    if (!b?.size) return "";
    const est = H.meta?.baseline_estimate;
    const parts = [...b].filter(([l]) => l !== est).map(([l, sts]) => `${sts.map((s) => String(s).toUpperCase()).join(", ")}: ${l}`);
    if (b.has(est)) parts.push(`${parts.length ? "other states" : "all states"}: ${est}`);
    return `; past results: ${parts.join("; ")}`;
  }

  function legend() {
    const el = box.querySelector(".ov-legend");
    const hatch = `<span class="lg-none"></span> no data`;
    if (arrowsOn()) {
      const len = ARROW_L * 10 / ARROW_FULL;
      const scale = `<svg width="${Math.ceil(len + 6)}" height="10" class="ov-scale" aria-hidden="true"><path d="M2,5h${len.toFixed(1)}m-3,-2l3,2l-3,2" stroke="#1b1b1b" fill="none"/></svg> 10 points (full length at ${ARROW_FULL} or more)`;
      const what = st.kind === "house" && st.arrows === "proj"
        ? "Each district: the model's projected 2026 margin minus Harris 2024, two-party, on the same 2026 lines."
        : st.kind === "house" && st.arrows === "live" ? "No district count is published; counted arrows show for the Senate and governor counties."
        : st.arrows === "past" ? "Each county: the shift from 2020 to 2024 (president), D minus R as a share of all votes in both years."
        : st.arrows === "live" ? `Each county counting: the counted two-party margin so far minus the county's two-party margin in its race's past result (the one the model shifts${baselines()}).`
        : `Each county with a ${st.office === "governor" ? "governor" : "Senate"} race: the model's projected two-party margin minus the county's two-party margin in its race's past result${baselines()}. No arrow: no county file.`;
      el.innerHTML = `<p class="legend-note">${esc(what)} <span style="color:${DEM}">← towards the Democrats</span> · <span style="color:${REP}">towards the Republicans →</span> · ${scale}</p>`;
    } else if (st.kind === "house") {
      const mode = MODES.find((m) => m[0] === st.mode);
      el.innerHTML = `<p class="legend-note"><span class="ov-ramp" style="background:linear-gradient(90deg,${st.mode === "ev" ? `${EV(0)},${EV(1)}` : `${REP},${NEUTRAL},${DEM}`})"></span> ${esc(mode[4])} ${hatch}</p>`;
    } else {
      const [, label, , div] = CENSUS.find((c) => c[0] === st.census);
      el.innerHTML = `<p class="legend-note"><span class="ov-ramp" style="background:linear-gradient(90deg,${div ? "#8c510a,#f5f5f5,#01665e" : "#ece9e1,#3d5a40"})"></span> ${esc(label)}: ${div ? "brown, shrinking; teal, growing (full colour at ±10%)" : "darker is higher (by county rank)"}. ${hatch}. ${esc(H.census?.fields?.[st.census] ?? "")}</p>`;
    }
  }

  function cardHTML(id) {
    if (st.kind !== "house") {
      const f = features.find((x) => (x.properties?.unit_id ?? x.id) === id);
      const name = `<p class="pc-name"><b>${esc(f?.properties?.name ?? id)}</b></p>`;
      if (st.kind === "arrows") { const a = arrowData.get(id); return `${name}<p>${a ? `${marginText(a.shift)} <small>${esc(a.src)}</small>` : "No figure for this county."}</p>`; }
      const c = H.census?.counties?.[id];
      const [k, label, fmt] = CENSUS.find((x) => x[0] === st.census);
      return `${name}<p>${esc(label)}: ${c?.[k] != null ? fmt(c[k]) : "no data"}</p>`;
    }
    const d = H.data[st.lines]?.districts?.[id], m = st.lines === "2026" ? mkt(id) : null, e = st.lines === "2026" ? H.ev?.districts?.[id] : null;
    if (!d) return "";
    const h = h24(d), b = st.lines === "2026" ? best({ id, ...d }) : null, o = st.lines === "2026" ? ours({ id, ...d }, modelOf(id)) : null, arrow = arrowsOn() ? arrowData.get(id) : null;
    const sides = houseSides(m, modelOf(id));
    const rows = [
      arrow && ["Arrow", `${marginText(arrow.shift)} <small>${esc(arrow.src)}</small>`],
      b && ["Leader", `${splitChanceHTML(b.p, sides.a, sides.b, { compact: true, source: b.src })}`],
      m?.pm && ["Polymarket", splitChanceHTML(m.pm.p, sides.a, sides.b, { compact: true, source: "Polymarket" })],
      m?.kalshi && ["Kalshi", splitChanceHTML(m.kalshi.p, sides.a, sides.b, { compact: true, source: "Kalshi" })],
      pollM({ id }) != null ? ["Poll average", `${candidateMargin(pollM({ id }), sides, true)} <small>${m.polls.average.n_polls ?? ""} polls</small>`]
        : m?.polls?.polls?.length ? ["Poll average", "<small>no poll in the 45-day window</small>"] : null,
      o && ["Model", splitChanceHTML(o.p, sides.a, sides.b, { compact: true, source: "Model" })],
      ["Harris 2024", `${marginText(d.pres24?.margin)}${d.pres24?.method && d.pres24.method !== "county" ? " <small>approx.</small>" : ""}`],
      h && [d.house24 ? "House 2024" : `House 2024 (core: ${esc(d.core_2024 ?? "")})`, `${esc(h.winner ?? "")} (${esc(h.winner_party ?? "")}) ${marginText(h.margin)}${h.uncontested ? " <small>uncontested</small>" : ""}`],
      d.swing_pres != null && ["Swing 2020→24 (president)", marginText(d.swing_pres)],
      e && ["Early vote", `${d3.format(",")(e.ev_total)} (${pct(e.share_of_2022_total, 0)} of 2022)`],
    ].filter(Boolean);
    return `<p class="pc-name"><b>${esc(id)}</b>${d.redrawn ? " <small>new lines</small>" : ""}</p><table class="kv"><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="n">${v}</td></tr>`).join("")}</tbody></table><p class="pc-hint">${st.state ? "Click to select." : "Click for the state's districts."}</p>`;
  }
  function hover(ev, id) {
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
  // zoom to a state, or back to the whole country (null), animated; the arrows keep their width
  function focus(s, animate = true) {
    st.state = s || null;
    if (!s) { st.sel = null; g?.select(".ov-sel").remove(); }
    const to = s ? fitState(s) : st.home;
    if (!g) return;
    const from = st.k, ip = d3.interpolateObject(from, to);
    st.k = to;
    g.selectAll(".ov-arrows").attr("stroke-width", 1 / to.k);
    mute();
    if (!animate || reduce()) g.attr("transform", tf(to));
    else g.transition().duration(ZOOM_MS).ease(d3.easeCubicInOut).attrTween("transform", () => (t) => tf(ip(t)));
    onState?.(st.state);
  }

  return {
    show, hide, focus, select, prepare,
    // end a district selection (its outline goes; the page has moved to a race)
    deselect() { st.sel = null; g?.select(".ov-sel").remove(); },
    get kind() { return st.kind; },
    get state() { return st.state; },
    get selected() { return st.sel; },
    setCensus(k) { st.census = k; if (st.kind === "census") draw(); },
    setArrows(k) { st.arrows = k; return st.kind ? draw() : null; },
    resize() { if (st.kind) draw(); },
    // for the map checks: what is drawn, the view and a card's text
    audit: () => ({
      kind: st.kind, mode: st.mode, lines: st.lines, arrows: arrowsOn() ? st.arrows : null, state: st.state, sel: st.sel, view: { ...st.k }, home: { ...st.home },
      n: features.length, colour: g ? g.selectAll("path.f[data-fill=colour]").size() : 0, hatch: g ? g.selectAll("path.f[data-fill=hatch]").size() : 0,
      nArrows: g ? g.selectAll(".ov-arrows path").size() : 0, built: [...layers].map(([k, L]) => `${k} ${L.w}x${L.h}@${L.top}`),
      ids: features.map((f) => f.properties?.id ?? f.properties?.unit_id ?? f.id),
    }),
    cardText: (id) => { const d = document.createElement("div"); d.innerHTML = cardHTML(id); return [...d.children].map((c) => c.textContent).join("\n"); },
    setMode(m) { st.mode = m; return draw(); },
  };
}

// The selected district for the page's readout: its figures (chips), and a strip chart of the
// chances (count model, markets, polls, baseline) on one 0-1 axis. model: { row, race } of the
// district's count model (race "house:TX-23" in the election's files) at the page's time, or null.
const modelChance = (model) => model?.row?.P_v2 ?? model?.row?.P ?? null;
const OURS_REG = "Registered House v2: same inputs, plus the live count on election night";
const gbPolls = () => H.files?.markets?.generic_ballot?.n_polls ?? H.files?.districts?.national?.generic_ballot?.n_polls;
const oursEst = () => `2024 president on the 2026 lines + generic-ballot swing${gbPolls() != null ? ` (${gbPolls()} polls)` : ""}; no district count model`;
// the model's chance for a district: the registered House model's where it has one, else the district estimate
const ours = (d, model) => (modelChance(model) != null ? { p: modelChance(model), src: OURS_REG } : d?.baseline?.p_d != null ? { p: d.baseline.p_d, src: oursEst() } : null);
// "How the model works": the one place the model's caveats are written (Method & sources tab)
export function ourModelHTML() {
  const n = gbPolls();
  return `<h3 class="tab-h">How the model works</h3>
    <p><b>The model</b> starts from the last certified results and shifts them to today's polling. House: 2024 president on the 2026 lines, plus a swing from the generic-ballot average${n != null ? `, which rests on ${n} qualifying polls` : ""}; where a district has a registered House model, that model's pre-count chance is shown. Senate and governor: the poll average as the prior, then the county count once votes come in (v3, registered); races without a live county model use the 2024 county results shifted to the poll average. Redrawn House districts have no certified result on their new lines, so their past results are estimates. Within a state the swing is uniform across counties. The market − model difference is a diagnostic, not a claim of mispricing.</p>`;
}
const houseSides = (m, model = null) => {
  const c = m?.pm?.cands ?? m?.kalshi?.cands ?? {};
  const mr = model?.race;
  const ma = mr?.a?.id === "dem" ? mr.a : mr?.b?.id === "dem" ? mr.b : null;
  const mb = mr?.a?.id === "rep" ? mr.a : mr?.b?.id === "rep" ? mr.b : null;
  return {
    a: { id: "dem", name: c.D ?? ma?.name ?? "Democratic side", color: DEM },
    b: { id: "rep", name: c.R ?? mb?.name ?? "Republican side", color: REP },
  };
};
const surname = (name) => {
  const words = String(name ?? "").trim().split(/\s+/);
  return words.length > 1 ? words.at(-1) : words[0] || "–";
};
const candidateLabel = (side, compact = false) => compact ? sideLabel(side, true) : `${surname(side?.name)} (${side?.id === "dem" ? "D" : "R"})`;
const candidateMargin = (v, sides, compact = false) => {
  if (v == null || !Number.isFinite(v)) return "–";
  if (Math.abs(v) < 0.05) return "even";
  const side = v > 0 ? sides.a : sides.b;
  return `<span style="color:${esc(side.color)}">${esc(candidateLabel(side, compact))} +${Math.abs(v).toFixed(1)}</span>`;
};
const pollsterShort = (name) => {
  const words = String(name ?? "").match(/[A-Za-z]+/g) ?? [];
  return String(name ?? "").length <= 18 ? name : words.map((w) => w[0]).join("").toUpperCase();
};
const estimates = (d, m, model, show = {}) => {
  const S = (k) => show[k] !== false;
  return [
    S("model") && ["Model", ours(d, model)?.p, "m"],
    S("pm") && ["Polymarket", model?.row?.pm ?? m?.pm?.p, "k"],
    S("kalshi") && ["Kalshi", model?.row?.kalshi ?? m?.kalshi?.p, "kx"],
    S("polls") && ["District poll", m?.polls?.average?.p_win, "r"],
  ].filter((x) => x && x[1] != null);
};
const leaderCall = (pts, sides) => {
  const first = pts[0], lead = probabilityLead(first?.[1], sides.a, sides.b);
  if (!lead) return "";
  const figures = pts.filter(([label]) => label !== "District poll").map(([label, p]) => `<span>${esc(label)} ${pct(lead.aLeads ? p : 1 - p, 0)}</span>`).join("");
  return `<p class="lead-call"><span class="lead-side" style="--side:${esc(lead.side.color)}">${esc(candidateLabel(lead.side))}</span> leads${figures ? `<span class="lead-sources">${figures}</span>` : ""}</p>`;
};
export function districtReadout(id, model = null, show = {}) {
  const d = houseDistrict(id), m = mkt(id), e = H.ev?.districts?.[id];
  if (!d) return `<p class="note">No data for ${esc(id)}.</p>`;
  const h = h24(d), sides = houseSides(m, model), pts = estimates(d, m, model, show), F = H.files?.flows?.states?.[d.st], flow = F?.districts?.[id], row = model?.row;
  const latest = m?.polls?.polls?.at?.(-1);
  const contexts = [
    `Harris 2024 ${marginText(d.pres24?.margin)}`,
    latest && `District poll ${candidateMargin(latest.d2 != null ? (2 * latest.d2 - 1) * 100 : null, sides)} (${esc(pollsterShort(latest.pollster))}, ${esc(day(latest.end))})`,
  ].filter(Boolean);
  const candidateLine = `${esc(sides.a.name)} (D) v ${esc(sides.b.name)} (R)`;
  const chip = (label, v, cls = "", title = "") => `<span class="chip"${title ? ` title="${esc(title)}"` : ""}><small>${label}</small><b class="${cls}">${v}</b></span>`;
  const live = [];
  if (row?.counted > 0) live.push(chip("Counted", pct(row.counted, 0)));
  if (row?.raw != null && row.counted > 0 && show.raw !== false) live.push(chip("Raw count", candidateMargin((2 * row.raw - 1) * 100, sides), "r"));
  const share = row?.share_v2 ?? row?.share;
  if (share != null && show.model !== false) live.push(chip("Model projection", candidateMargin((2 * share - 1) * 100, sides), "m", OURS_REG));
  if (h) live.push(chip("House 2024", marginText(h.margin)));
  if (e && show.early !== false) live.push(chip("Early vote", `${d3.format(",")(e.ev_total)} · ${pct(e.share_of_2022_total, 0)} of 2022`));
  const detail = flow ? `New lines: core ${esc(flow.core_2024 ?? "")}; lean changed ${marginText(flow.lean_change)}.` : "";
  return `<p class="ro-head"><span class="ro-at"><b>${esc(id)}</b>${d.redrawn ? " · new lines" : ""}${h?.incumbent ? ` · incumbent ${esc(h.incumbent)} (${esc(h.incumbent_party ?? "")})` : ""}</span></p>
    ${leaderCall(pts, sides)}
    <p class="race-context">${candidateLine}${contexts.length ? ` · ${contexts.join(" · ")}` : ""}</p>
    ${live.length ? `<div class="chips">${live.join("")}</div>` : ""}${detail ? `<p class="ro-gap">${detail}</p>` : ""}`;
}
export function districtChart(el, id, model = null, show = {}) {
  const d = houseDistrict(id), m = mkt(id), sides = houseSides(m, model), pts = estimates(d, m, model, show);
  el.innerHTML = `<div class="chance-chart"><h3 class="tab-h">${esc(id)} · chance of winning, by source</h3>
    <div class="chance-chart-sides"><span style="color:${DEM}">${esc(sides.a.name)} (D)</span><span style="color:${REP}">${esc(sides.b.name)} (R)</span></div>
    ${pts.length ? pts.map(([label, p, cls]) => `<div class="chance-chart-row"><span class="chance-chart-source">${esc(label)}</span><span class="${cls}">${splitChanceHTML(p, sides.a, sides.b, { source: label })}</span></div>`).join("") : `<p class="note">No estimate is shown.</p>`}</div>`;
}

// The House races list (the summary beside the map at the country level): every district,
// competitive first, filterable; and, for a redrawn state, its old and new seats.
export function houseList(el, { state = null, onPick, modelOf = () => null, show = {} }) {
  const D = Object.keys(H.files?.districts?.districts ?? {}).map(houseDistrict).filter((d) => !state || d.st.toLowerCase() === state);
  const rows = D.map((d) => ({ d, model: modelOf(d.id), p: estimates(d, mkt(d.id), modelOf(d.id), show)[0]?.[1] ?? null })).sort((a, b) => Math.abs((a.p ?? 0) - 0.5) - Math.abs((b.p ?? 0) - 0.5) || a.d.id.localeCompare(b.d.id));
  const gb = H.files?.markets?.generic_ballot ?? H.files?.districts?.national?.generic_ballot;
  const ps = D.map((d) => mk(d)).filter((p) => p != null);
  const F = state ? H.files?.flows?.states?.[state.toUpperCase()] : null;
  el.innerHTML = `<p class="ro-head"><span class="ro-at"><b>House</b> ${state ? `· ${esc(state.toUpperCase())} districts` : "districts this year"}</span></p>
    ${state ? "" : `<p class="nat-sum-ctl">Generic ballot: Democrats ${pct(gb?.avg)} of the two-party vote (± ${(100 * (gb?.sd ?? 0)).toFixed(1)}; ${gb?.n_polls ?? 0} qualifying polls). Markets: Democrats favoured in <b>${ps.filter((p) => p > 0.5).length}</b> of ${ps.length} priced seats, about <b>${d3.sum(ps).toFixed(0)}</b> expected; 218 for a majority.</p>`}
    ${F ? `<p class="nat-sum-ctl">Redrawn: seats Harris won ${F.old_seats.harris_won} of ${F.old_seats.districts} on the 2024 lines, ${F.new_seats.harris_won} on the new ones (expected Democratic seats at the 2024 vote ${num(F.old_seats.expected_d, 1)} → ${num(F.new_seats.expected_d, 1)}); Democrats won ${F.house24_won_d} in 2024.</p>` : ""}
    <label class="select"><span>District</span> <input type="search" class="hl-filter" placeholder="TX-07, Fletcher…" aria-label="Filter districts"></label>
    <div class="hub-scroll hl-box"><table class="races nat-races hl-table"><thead><tr><th>District</th><th>Incumbent</th><th class="n">Leader</th><th class="n">Harris 2024</th></tr></thead><tbody></tbody></table></div>
    <p class="note">Most competitive first. The split bar uses the first visible estimate in the Show bar.</p>`;
  const body = el.querySelector("tbody"), filter = el.querySelector(".hl-filter");
  const fill = () => {
    const f = filter.value.trim().toLowerCase();
    body.innerHTML = rows.filter(({ d }) => !f || d.id.toLowerCase().includes(f) || (h24(d)?.incumbent ?? "").toLowerCase().includes(f)).slice(0, 120)
      .map(({ d, model, p }) => { const sides = houseSides(mkt(d.id), model); return `<tr tabindex="0" data-id="${esc(d.id)}"><td>${esc(d.id)}${d.redrawn ? " <small>new</small>" : ""}</td><td>${esc(h24(d)?.incumbent ?? "–")}</td><td class="n">${splitChanceHTML(p, sides.a, sides.b, { compact: true })}</td><td class="n">${marginText(d.pres24?.margin)}</td></tr>`; }).join("");
  };
  filter.addEventListener("input", fill);
  body.addEventListener("click", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr) onPick?.(tr.dataset.id); });
  body.addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr && ev.key === "Enter") onPick?.(tr.dataset.id); });
  fill();
}

// The Races tab with the House office on: every district, the open state's first, then the most
// competitive nationally. Hidden Show-bar layers have no column.
export function houseRaceTable(el, { state = null, selected = null, onPick, modelOf = () => null, show = {} }) {
  const S = (k) => show[k] !== false;
  const close = (d) => Math.abs((estimates(d, mkt(d.id), modelOf(d.id), show)[0]?.[1] ?? 0) - 0.5);
  const rows = houseDistrictIds().map(houseDistrict).sort((a, b) => (state ? (b.st.toLowerCase() === state) - (a.st.toLowerCase() === state) : 0) || close(a) - close(b) || a.id.localeCompare(b.id));
  const hasMarket = S("pm") || S("kalshi"), hasGap = S("model") && hasMarket;
  const cols = [["id", "District"], S("model") && ["model", "Model"], S("pm") && ["pm", "Polymarket"], S("kalshi") && ["kalshi", "Kalshi"], S("polls") && ["polls", "Polls"], ["pres", "Harris 2024"], hasGap && ["gap", "Market vs model"]].filter(Boolean);
  el.innerHTML = `<label class="select"><span>District</span> <input type="search" class="hr-filter" placeholder="TX-07, Fletcher…" aria-label="Filter districts"></label>
    <div class="hub-scroll"><table class="races hr-table"><thead><tr>${cols.map(([key, label]) => `<th class="${key === "id" ? "" : "n"}">${label}</th>`).join("")}</tr></thead><tbody></tbody></table></div>
    <p class="note">${state ? `${esc(state.toUpperCase())} first, then` : "Most competitive first:"} the split bars name each source's leader. Market vs model is the market's confidence in the model leader, minus the model's, in points.</p>`;
  const body = el.querySelector("tbody"), filter = el.querySelector(".hr-filter");
  const fill = () => {
    const f = filter.value.trim().toLowerCase();
    body.innerHTML = rows.filter((d) => !f || d.id.toLowerCase().includes(f) || (h24(d)?.incumbent ?? "").toLowerCase().includes(f)).slice(0, 150).map((d) => {
      const m = mkt(d.id), model = modelOf(d.id), o = ours(d, model), base = o?.p ?? null, market = (S("pm") ? (model?.row?.pm ?? m?.pm?.p) : null) ?? (S("kalshi") ? (model?.row?.kalshi ?? m?.kalshi?.p) : null), sides = houseSides(m, model), fav = probabilityLead(base, sides.a, sides.b);
      const points = fav && market != null ? 100 * ((fav.aLeads ? market : 1 - market) - fav.chance) : null;
      const val = { id: `${esc(d.id)}${d.redrawn ? " <small>new</small>" : ""}`, model: splitChanceHTML(base, sides.a, sides.b, { compact: true, source: "Model" }), pm: splitChanceHTML(model?.row?.pm ?? m?.pm?.p, sides.a, sides.b, { compact: true, source: "Polymarket" }), kalshi: splitChanceHTML(model?.row?.kalshi ?? m?.kalshi?.p, sides.a, sides.b, { compact: true, source: "Kalshi" }), polls: pollM(d) != null ? candidateMargin(pollM(d), sides) : "–", pres: marginText(d.pres24?.margin), gap: points == null ? "–" : `<span class="chance-gap">${Math.abs(points) < 0.5 ? "0" : `${points > 0 ? "+" : "−"}${Math.abs(points).toFixed(0)}`}<small>pt</small></span>` };
      return `<tr tabindex="0" data-id="${esc(d.id)}"${d.id === selected ? ' class="sel"' : ""}>${cols.map(([key]) => `<td class="${key === "id" ? "" : "n"}">${val[key]}</td>`).join("")}</tr>`;
    }).join("");
  };
  filter.addEventListener("input", fill);
  body.addEventListener("click", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr) onPick?.(tr.dataset.id); });
  body.addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-id]"); if (tr && ev.key === "Enter") onPick?.(tr.dataset.id); });
  fill();
}
