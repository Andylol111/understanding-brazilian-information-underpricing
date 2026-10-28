import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import * as topojson from "https://cdn.jsdelivr.net/npm/topojson-client@3/+esm";
import { C, LIVE, STATIC, param, getJSON, esc, pct, num, usd, fmtDate, clock, tzName, tzOffsetMin, evidenceLabel, EVIDENCE, STATUS } from "./common.js";

const EID = param("id");
const $ = (id) => document.getElementById(id);
const S = { mode: "margin", frame: 0, playing: null, race: null, followLatest: true };

/* ---------------------------------------------------------------- loading */

// Map frames: the selected race's own file when meta lists one (races[i].units, relative to <eid>/),
// otherwise the election's units.json.
const unitsPath = () => `${EID}/${S.race?.units ?? "units.json"}`;

async function loadAll(first) {
  const opt = { optional: true };
  if (first) {
    const [elections, meta] = await Promise.all([getJSON("elections.json", opt), getJSON(`${EID}/meta.json`)]);
    S.meta = meta;
    S.entry = elections?.find((e) => e.id === EID) ?? null;
    const nat = races().find((r) => !r.region) ?? races()[0];
    S.race = raceById(param("race")) ?? nat ?? null;
  }
  const path = unitsPath();
  const [timeline, units, stats, arb] = await Promise.all([
    getJSON(`${EID}/timeline.json`, opt),
    getJSON(path, opt),
    getJSON(`${EID}/stats.json`, opt),
    getJSON(`${EID}/arb.json`, opt),
  ]);
  S.timeline = timeline;
  S.stats = stats;
  S.arb = arb;
  if (path === unitsPath()) setUnits(units); // the race may have changed while fetching
}

/* ---------------------------------------------------------------- header */

function races() { return S.meta.races ?? []; }
const raceById = (id) => races().find((r) => r.race_id === id);
const regionName = (id) => S.meta.regions?.find((r) => r.id === id)?.name ?? id?.toUpperCase();

function renderHeader() {
  const m = S.meta, e = S.entry;
  document.title = `${m.name}: election night`;
  $("crumb").textContent = m.name;
  $("kicker").textContent = `${fmtDate(m.date)}${e?.unit_level ? ` · by ${e.unit_level}` : ""}`;
  $("title").textContent = m.name;
  $("dek").textContent = e?.summary ?? "";
  const status = e?.status ? `<span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span>` : "";
  const long = EVIDENCE[e?.evidence]?.long;
  const ev = e?.evidence ? `${evidenceLabel(e.evidence)} ${long ? esc(long[0].toUpperCase() + long.slice(1)) + "." : ""}` : "";
  const closed = e?.status === "scheduled" || e?.status === "live" ? "Polls close at" : "Polls closed at";
  const close = m.poll_close_utc ? ` ${closed} ${clock(m.poll_close_utc, m.tz)} ${esc(tzName(m.tz, m.poll_close_utc))}.` : "";
  const live = LIVE ? ` <span id="live-stamp">Live data from ${esc(LIVE)}, refreshed every 15 seconds.</span>` : "";
  $("byline").innerHTML = `${status} ${ev}${close}${live}`;
  if (LIVE) $("home").href = `./?live=${encodeURIComponent(LIVE)}`;
}

function renderFooter() {
  const m = S.meta;
  const notes = m.notes ?? [];
  $("notes-section").hidden = !notes.length;
  $("notes").innerHTML = notes.map((n) => `<li>${esc(n)}</li>`).join("");
  const src = (m.sources ?? []).map((s) => `<a href="${esc(s.url)}">${esc(s.name)}</a> (${esc(s.licence)})`);
  const geo = m.geometry?.includes("us_")
    ? "Map: U.S. Census Bureau cartographic boundary files, 2024 (public domain)"
    : m.geometry?.includes("br_")
      ? "Map: IBGE Malha Municipal Digital 2025 (public open data, attribution IBGE), joined to TSE municipality codes with the TSE table of TSE and IBGE codes (CC-BY)"
      : "";
  $("sources").innerHTML = `Sources: ${src.join("; ") || "–"}.${geo ? ` ${geo}, built by <code>analysis/geo_build.py</code>.` : ""} Charts: D3. Code under MIT licence.`;
}

/* ---------------------------------------------------------------- map */

const NO_DATA = C.none;
const NEUTRAL = "#f4f2ec";

function setUnits(units) {
  S.units = units?.frames?.length ? units : null;
  if (!S.units) return;
  S.index = new Map(units.unit_ids.map((id, i) => [id, i]));
  // Which race each unit's colours come from: units.race_id, else the race for the unit's region.
  const byRegion = new Map(races().filter((r) => r.region).map((r) => [r.region, r]));
  const fixed = units.race_id ? raceById(units.race_id) : null;
  const national = races().find((r) => r.region == null) ?? races()[0];
  S.unitRace = units.unit_ids.map((_, i) => fixed ?? byRegion.get(units.region?.[i]) ?? national);
  S.mapRaces = [...new Set(S.unitRace)];
  indexFeatures();
}

function indexFeatures() {
  if (!S.features || !S.units) return;
  S.features.forEach((f) => { f.i = S.index.get(f.properties[S.key]) ?? -1; });
}

async function initMap() {
  if (!S.meta.geometry) return;
  S.topo = await getJSON(S.meta.geometry, { root: STATIC });
  const obj = S.topo.objects[S.meta.geometry_object];
  S.key = S.meta.unit_key ?? "unit_id";
  S.features = topojson.feature(S.topo, obj).features;
  const reg = (g) => g.properties[S.key].split("-")[1];
  S.regionMesh = topojson.mesh(S.topo, obj, (a, b) => a !== b && reg(a) !== reg(b));
  S.outline = topojson.mesh(S.topo, obj, (a, b) => a === b);
  S.isUS = S.features[0]?.properties[S.key]?.startsWith("us-");
  indexFeatures();
  document.querySelectorAll("#map-mode button").forEach((b) => b.addEventListener("click", () => {
    S.mode = b.dataset.mode;
    document.querySelectorAll("#map-mode button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    colourMap();
  }));
  $("frame").addEventListener("input", (e) => { stop(); setFrame(+e.target.value); });
  $("play").addEventListener("click", () => (S.playing ? stop() : play()));
}

// (Re)draw the map for the current units file, at its latest frame.
function showMap() {
  stop();
  if (!S.features || !S.units) {
    $("map-section").hidden = true;
    S.unitSel = null;
    return;
  }
  const mapped = S.features.filter((f) => f.i >= 0).length;
  const n = S.units.unit_ids.length;
  const opening = defaultFrame();
  const opensAt = !LIVE && opening < S.units.frames.length - 1
    ? ` The map opens at ${clock(S.units.frames[opening].t, S.meta.tz)}, the first frame after 25% of the vote was counted, coloured by the model's projected margin.`
    : "";
  $("map-title").textContent = S.entry?.unit_level ? `The count by ${S.entry.unit_level}` : "The count by place";
  $("map-intro").textContent = `${n.toLocaleString("en-US")} reporting units${mapped < n ? `; ${(n - mapped).toLocaleString("en-US")} have no shape on the map (for example votes cast abroad)` : ""}.${opensAt} Use the slider or Play to step through the count; hover or tap a place for its numbers.`;
  $("map-section").hidden = false;
  S.frame = opening;
  drawMap();
  setFrame(S.frame);
}

// Replays open where the map has something to show: the first frame at which the national count
// (the national race's timeline `counted`, else the selected race's) reached 25%. Live pages open on
// the latest frame.
function defaultFrame() {
  const frames = S.units.frames, last = frames.length - 1;
  if (LIVE) return last;
  const race = races().find((r) => !r.region) ?? S.race;
  const hit = (S.timeline?.[race?.race_id] ?? []).find((d) => d.counted >= 0.25);
  if (!hit) return last;
  const k = frames.findIndex((f) => PARSE(f.t) >= PARSE(hit.t));
  return k < 0 ? last : k;
}

function play() {
  const last = S.units.frames.length - 1;
  if (S.frame >= last) setFrame(0);
  $("play").textContent = "Pause";
  S.playing = setInterval(() => {
    if (S.frame >= S.units.frames.length - 1) return stop();
    setFrame(S.frame + 1);
  }, 450);
}

function stop() {
  clearInterval(S.playing);
  S.playing = null;
  $("play").textContent = "Play";
}

function selectedRegion() {
  const r = S.race;
  if (!r?.region || !S.units) return null;
  return S.units.region?.includes(r.region) ? r.region : null;
}

// National US: Albers with Alaska/Hawaii insets. Other countries: Mercator. One region: an
// equal-area conic centred on it (also keeps Alaska at its true scale).
function projectionFor(fc, region) {
  if (!region) return S.isUS ? d3.geoAlbersUsa() : d3.geoMercator();
  const [lon] = d3.geoCentroid(fc);
  const [[, la0], [, la1]] = d3.geoBounds(fc);
  const pad = (la1 - la0) / 6;
  return d3.geoConicEqualArea().parallels([la0 + pad, la1 - pad]).rotate([-lon, 0]);
}

function drawMap() {
  const box = $("map");
  const width = Math.max(280, box.clientWidth);
  const maxH = Math.min(680, Math.max(360, innerHeight * 0.75));
  const region = selectedRegion();
  const fit = S.features.filter((f) => f.i >= 0 && (!region || S.units.region[f.i] === region));
  const fc = { type: "FeatureCollection", features: fit.length ? fit : S.features };
  const proj = projectionFor(fc, region);
  proj.fitWidth(width, fc);
  let path = d3.geoPath(proj);
  const [[, y0], [, y1]] = path.bounds(fc);
  let height = Math.ceil(y1 - y0);
  if (height > maxH) {
    height = maxH;
    proj.fitSize([width, height], fc);
    path = d3.geoPath(proj);
  }
  S.path = path;
  const root = d3.select(box).html("");
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", "100%").attr("class", "mapsvg");
  const small = S.features.length > 4000 && !region;
  S.unitSel = svg.append("g").attr("class", "units").attr("stroke", "#fff").attr("stroke-width", small ? 0.12 : 0.35)
    .selectAll("path").data(S.features).join("path").attr("d", path)
    .attr("opacity", (f) => (region && (f.i < 0 || S.units.region[f.i] !== region) ? 0.35 : null));
  svg.append("path").datum(S.regionMesh).attr("class", "region-mesh").attr("d", path);
  svg.append("path").datum(S.outline).attr("class", "outline").attr("d", path);
  S.hover = svg.append("path").attr("class", "hover").attr("fill", "none");
  const g = svg.select("g.units");
  g.on("pointermove", (e) => {
    const f = e.target.__data__;
    if (f) showUnit(f);
  });
  svg.on("pointerleave", () => { S.hover.attr("d", null); S.hovered = null; unitReadout(null); });
  colourMap();
}

function diverging(v, max, race) {
  if (v == null) return NO_DATA;
  const t = Math.min(1, Math.abs(v) / max) ** 0.8;
  return d3.interpolateLab(NEUTRAL, v >= 0 ? race.a.color : race.b.color)(t);
}
const countedScale = d3.scaleSequential(d3.interpolateLab("#e9e7e0", "#4f647d")).domain([0, 1]);
const SWING_MAX = 0.1, MARGIN_MAX = 0.4;

function unitValues(i) {
  const fr = S.units.frames[S.frame];
  const prior = S.units.prior[i] >= 0 ? S.units.prior[i] / 1000 : null;
  const a = fr.a[i] >= 0 ? fr.a[i] / 1000 : null;
  const proj = fr.proj?.[i] >= 0 ? fr.proj[i] / 1000 : null;
  return { f: fr.f[i] / 1000, a, prior, proj, swing: a != null && prior != null ? a - prior : null, margin: proj != null ? 2 * proj - 1 : null };
}

function unitColour(i) {
  if (i < 0) return NO_DATA;
  const v = unitValues(i);
  if (S.mode === "counted") return v.f > 0 ? countedScale(v.f) : NO_DATA;
  if (S.mode === "swing") return diverging(v.swing, SWING_MAX, S.unitRace[i]);
  return diverging(v.margin, MARGIN_MAX, S.unitRace[i]);
}

function colourMap() {
  if (!S.unitSel || !S.units.frames.length) return;
  S.unitSel.attr("fill", (f) => unitColour(f.i));
  renderLegend();
  frameLabel();
  if (S.hovered) unitReadout(S.hovered);
}

function legendRace() {
  return S.mapRaces.includes(S.race) ? S.race : S.mapRaces[0];
}

function renderLegend() {
  const w = 240, h = 10;
  const root = d3.select("#legend").html("");
  const svg = root.append("svg").attr("viewBox", `0 0 ${w} 30`).attr("width", w).attr("height", 30);
  const id = `grad-${S.mode}`;
  const grad = svg.append("defs").append("linearGradient").attr("id", id);
  const r = legendRace();
  const stops = S.mode === "counted"
    ? d3.range(0, 1.01, 0.1).map((t) => [t, countedScale(t)])
    : d3.range(-1, 1.01, 0.1).map((t) => [(t + 1) / 2, diverging(-t, 1, r)]);
  grad.selectAll("stop").data(stops).join("stop").attr("offset", (d) => d[0]).attr("stop-color", (d) => d[1]);
  svg.append("rect").attr("x", 0).attr("y", 0).attr("width", w).attr("height", h).attr("fill", `url(#${id})`);
  const max = S.mode === "swing" ? SWING_MAX * 100 : MARGIN_MAX * 100;
  const ticks = S.mode === "counted" ? [[0, "0%"], [0.5, "50%"], [1, "100%"]] : [[0, `+${max} pt`], [0.5, "0"], [1, `+${max} pt`]];
  svg.selectAll("text").data(ticks).join("text").attr("x", (d) => d[0] * w).attr("y", 24)
    .attr("text-anchor", (d) => (d[0] === 0 ? "start" : d[0] === 1 ? "end" : "middle")).attr("class", "legend-text").text((d) => d[1]);
  if (S.mode !== "counted") {
    const lab = (n) => (S.mode === "swing" ? `towards ${esc(n)}` : `${esc(n)} ahead`);
    root.append("div").attr("class", "legend-names").style("max-width", `${w}px`)
      .html(`<span>${lab(r.a.name)}</span><span>${lab(r.b.name)}</span>`);
  }
  const pairs = new Set(S.mapRaces.map((x) => `${x.a.name}|${x.b.name}`));
  const many = pairs.size > 1 && S.mode !== "counted";
  const note = {
    counted: "Share of the place's polling units or votes already counted. Pale grey: nothing counted yet.",
    swing: `Change in ${r.a.name}'s two-way share of the votes counted so far, against the same place in the comparison election, in points. Pale grey: nothing counted yet.`,
    margin: "The model's projected final margin in the place, in points of the two-way vote.",
  }[S.mode];
  $("mode-note").textContent = `${note}${many ? " Each region is coloured by its own race's candidates; the scale shows the selected race." : ""}`;
}

function frameLabel() {
  const fr = S.units.frames[S.frame];
  const reporting = fr.f.reduce((n, x) => n + (x > 0 ? 1 : 0), 0);
  const tz = S.meta.tz;
  $("frame-time").innerHTML = `<b>${clock(fr.t, tz)}</b> ${esc(tzName(tz, fr.t))} · ${reporting.toLocaleString("en-US")} of ${fr.f.length.toLocaleString("en-US")} units reporting`;
  markTimeline();
}

function setFrame(k) {
  const n = S.units.frames.length;
  S.frame = Math.max(0, Math.min(n - 1, k));
  S.followLatest = S.frame === n - 1;
  $("frame").max = String(n - 1);
  $("frame").value = String(S.frame);
  colourMap();
}

function showUnit(f) {
  S.hovered = f;
  S.hover.attr("d", S.path(f));
  unitReadout(f);
}

function unitReadout(f) {
  const el = $("map-readout");
  if (!f) { el.innerHTML = `<p class="note">Hover or tap a place.</p>`; return; }
  if (f.i < 0) { el.innerHTML = `<p><b>${esc(f.properties.name)}</b></p><p class="note">Not part of this election's results.</p>`; return; }
  const i = f.i, v = unitValues(i), r = S.unitRace[i];
  const name = S.units.names?.[i] ?? f.properties.name;
  const reg = S.units.region?.[i];
  const sw = v.swing == null ? "–" : `${v.swing >= 0 ? "+" : "−"}${Math.abs(100 * v.swing).toFixed(1)} pt`;
  el.innerHTML = `<p><b>${esc(name)}</b>${reg ? `, ${esc(regionName(reg))}` : ""}</p>
    <table class="kv"><tbody>
    <tr><td>Counted</td><td class="n">${pct(v.f, 0)}</td></tr>
    <tr><td>${esc(r.a.name)}, votes counted</td><td class="n">${pct(v.a)}</td></tr>
    <tr><td>${esc(r.a.name)}, comparison election</td><td class="n">${pct(v.prior)}</td></tr>
    <tr><td>Swing</td><td class="n">${sw}</td></tr>
    <tr><td>${esc(r.a.name)}, model's projected final</td><td class="n">${pct(v.proj)}</td></tr>
    <tr><td>Votes in comparison election</td><td class="n">${S.units.V0?.[i]?.toLocaleString("en-US") ?? "–"}</td></tr>
    </tbody></table>`;
}

/* ---------------------------------------------------------------- race timeline */

function initRaces() {
  const rs = races();
  if (!rs.length) return;
  const sel = $("race");
  const nat = rs.filter((r) => !r.region), reg = rs.filter((r) => r.region);
  const opt = (r) => `<option value="${esc(r.race_id)}">${esc(r.name)}${r.region && !r.name.includes(regionName(r.region)) ? `, ${esc(regionName(r.region))}` : ""}</option>`;
  sel.innerHTML = nat.map(opt).join("") + (reg.length ? `<optgroup label="By region">${reg.map(opt).join("")}</optgroup>` : "");
  sel.value = S.race.race_id;
  sel.addEventListener("change", async () => {
    const before = unitsPath();
    S.race = raceById(sel.value);
    const u = new URL(location.href);
    u.searchParams.set("race", S.race.race_id);
    history.replaceState(null, "", u);
    drawTimeline();
    if (unitsPath() !== before) {
      const path = unitsPath();
      const units = await getJSON(path, { optional: true });
      if (path !== unitsPath()) return;
      setUnits(units);
      showMap();
    } else if (S.unitSel) drawMap();
  });
  $("race-section").hidden = false;
}

const PARSE = (t) => new Date(t);

function raceIntro(rows) {
  const pm = rows.some((d) => d.pm != null), k = rows.some((d) => d.kalshi != null);
  const prices = pm && k ? `the <span class="k">Polymarket</span> price (solid) and the <span class="k">Kalshi</span> price (dashed)`
    : pm ? `the <span class="k">Polymarket</span> price` : k ? `the <span class="k">Kalshi</span> price (dashed)` : null;
  $("race-intro").innerHTML = `The top panel compares the <span class="r">raw count</span> with the <span class="m">model's</span> projected final share and its 90% band. ${
    prices ? `The bottom panel compares the model's probability with ${prices}.` : "The bottom panel shows the model's probability; no market price was recorded for this race."}${
    S.units ? " Click the chart to move the map to that time." : ""}`;
}

function drawTimeline() {
  const root = d3.select("#timeline").html("");
  const r = S.race;
  const all = (S.timeline?.[r.race_id] ?? []).map((d) => ({ ...d, time: PARSE(d.t) }));
  marketsNote(r);
  raceIntro(all);
  if (!all.length) {
    root.append("p").attr("class", "note").text("No count or price data for this race yet.");
    $("tl-readout").textContent = "";
    $("tl-note").textContent = "";
    S.tl = null;
    return;
  }
  const tz = S.meta.tz;
  const off = tzOffsetMin(tz, all[0].t) * 60000;
  const wall = (d) => new Date(d.getTime() + off);
  // Start 15 minutes before the first minute with 1% counted (the gate before which the model may
  // not trade). A 0.1% threshold does not work: in the Brazil runoff 0.2-0.4% was in from 17:06
  // and stayed flat until 19:04, which left two empty hours on the chart.
  const gate1 = all.find((d) => d.counted >= 0.01);
  const start = gate1 ? new Date(Math.max(all[0].time, gate1.time - 15 * 60000)) : all[0].time;
  const rows = all.filter((d) => d.time >= start);
  const before = all.filter((d) => d.time < start);
  const close = S.meta.poll_close_utc;
  $("tl-note").textContent = before.length
    ? `${close ? `Between poll close (${clock(close, tz)}) and` : "Before"} ${clock(gate1.t, tz)} less than 1% of the vote was counted (at most ${pct(d3.max(before, (d) => d.counted))}), so the chart starts at ${clock(rows[0].t, tz)}.`
    : "";
  const narrow = root.node().clientWidth < 560;
  const width = Math.max(300, Math.min(root.node().clientWidth, 980));
  const m = { l: narrow ? 40 : 52, r: narrow ? 80 : 92, t: 24, b: 46 };
  const h1 = narrow ? 200 : 250, h2 = narrow ? 150 : 190, gap = 36;
  const height = m.t + h1 + gap + h2 + m.b;
  const x = d3.scaleUtc().domain(d3.extent(rows, (d) => wall(d.time))).range([m.l, width - m.r]);
  const X = (d) => x(wall(d.time));
  const lo = (d) => (d.share != null && d.sd != null ? d.share - 1.645 * d.sd : null);
  const hi = (d) => (d.share != null && d.sd != null ? d.share + 1.645 * d.sd : null);
  const counted = rows.filter((d) => d.counted > 0.005);
  const vals = (counted.length ? counted : rows).flatMap((d) => [d.raw, d.share, lo(d), hi(d)]).filter((v) => v != null);
  let [a, b] = vals.length ? d3.extent(vals) : [0.4, 0.6];
  if (b - a < 0.04) { const c = (a + b) / 2; a = c - 0.02; b = c + 0.02; }
  if (0.5 > a - 0.03 && 0.5 < b + 0.03) { a = Math.min(a, 0.495); b = Math.max(b, 0.505); }
  const yS = d3.scaleLinear().domain([Math.max(0, a - 0.005), Math.min(1, b + 0.005)]).nice().range([m.t + h1, m.t]);
  const [d0, d1] = yS.domain();
  const clampS = (v) => yS(Math.max(d0, Math.min(d1, v)));
  const top2 = m.t + h1 + gap;
  const yP = d3.scaleLinear().domain([0, 1]).range([top2 + h2, top2]);
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("width", "100%").style("display", "block");

  svg.append("g").attr("class", "grid").selectAll("line").data(yS.ticks(5)).join("line")
    .attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS).attr("y2", yS);
  svg.append("g").attr("class", "grid").selectAll("line").data([0, 0.25, 0.5, 0.75, 1]).join("line")
    .attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP).attr("y2", yP);
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yS).ticks(5).tickFormat((v) => `${Math.round(v * 1000) / 10}%`).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yP).tickValues([0, 0.5, 0.9, 1]).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top2 + h2})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor((width - m.l - m.r) / 80))).tickFormat(d3.utcFormat("%H:%M")).tickSizeOuter(0));
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", m.t - 10).text(`${r.a.name}'s share of the two-candidate vote`);
  svg.append("text").attr("class", "panel-label").attr("x", m.l).attr("y", top2 - 12).text(`Probability ${r.a.name} wins`);
  svg.append("text").attr("class", "panel-label tz").attr("x", width - m.r).attr("y", top2 + h2 + 42).attr("text-anchor", "end").text(`times: ${tzName(tz, rows[0].t)}`);

  const last = all[all.length - 1];
  const final = last.counted >= 0.999 && last.raw != null ? last.raw : null;

  if (final != null) {
    const wrong = rows.filter((d) => d.counted > 0.01 && d.raw != null && (d.raw - 0.5) * (final - 0.5) < 0);
    if (wrong.length) {
      const [wa, wb] = d3.extent(wrong, X);
      svg.append("rect").attr("x", wa).attr("width", Math.max(1, wb - wa)).attr("y", m.t).attr("height", top2 + h2 - m.t).attr("fill", C.raw).attr("opacity", 0.08);
      svg.append("text").attr("x", wa + 6).attr("y", yP(0.2)).attr("class", "chart-note").attr("fill", C.raw)
        .text(final > 0.5 ? `raw count shows ${r.a.name} behind` : `raw count shows ${r.a.name} ahead`);
    }
  }

  // top panel
  if (0.5 > d0 && 0.5 < d1) svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yS(0.5)).attr("y2", yS(0.5)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  if (final != null) {
    svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", clampS(final)).attr("y2", clampS(final)).attr("stroke", C.model).attr("stroke-dasharray", "5,4").attr("opacity", 0.7);
  }
  const band = rows.filter((d) => d.sd != null && d.share != null);
  svg.append("path").datum(band).attr("fill", C.model).attr("opacity", 0.14)
    .attr("d", d3.area().x(X).y0((d) => clampS(lo(d))).y1((d) => clampS(hi(d))).curve(d3.curveStepAfter));
  const rawRows = rows.filter((d) => d.raw != null && d.counted > 0);
  svg.append("path").datum(rawRows).attr("fill", "none").attr("stroke", C.raw).attr("stroke-width", 2)
    .attr("d", d3.line().x(X).y((d) => clampS(d.raw)));
  svg.append("path").datum(rows.filter((d) => d.share != null)).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2.2)
    .attr("d", d3.line().x(X).y((d) => clampS(d.share)).curve(d3.curveStepAfter));

  const lastOf = (k) => [...rows].reverse().find((d) => d[k] != null);
  endLabels(svg, width - m.r + 6, [
    [lastOf("raw"), (d) => clampS(d.raw), "raw count", C.raw],
    [lastOf("share"), (d) => clampS(d.share), "model", C.model],
    [final != null ? {} : null, () => clampS(final), `final ${pct(final)}`, C.model],
  ]);

  const gate = rows.find((d) => d.counted >= 0.01);
  if (gate) {
    svg.append("line").attr("x1", X(gate)).attr("x2", X(gate)).attr("y1", m.t).attr("y2", top2 + h2).attr("stroke", C.muted).attr("stroke-dasharray", "1,3");
    svg.append("text").attr("x", X(gate) + 5).attr("y", yP(0.03)).attr("class", "chart-note").attr("fill", C.muted).text(`1% counted (${clock(gate.t, tz)})`);
  }

  // bottom panel
  for (const v of [0.1, 0.9]) svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", yP(v)).attr("y2", yP(v)).attr("stroke", "#bdb9ae").attr("stroke-dasharray", "2,3");
  const step = (k) => d3.line().defined((d) => d[k] != null).x(X).y((d) => yP(d[k])).curve(d3.curveStepAfter);
  svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 2.2).attr("d", step("P"));
  svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.7).attr("d", step("pm"));
  svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.5).attr("stroke-dasharray", "5,3").attr("d", step("kalshi"));
  endLabels(svg, width - m.r + 6, [
    [lastOf("P"), (d) => yP(d.P), "model", C.model],
    [lastOf("pm"), (d) => yP(d.pm), "Polymarket", C.market],
    [lastOf("kalshi"), (d) => yP(d.kalshi), "Kalshi", C.market],
  ]);

  // crosshair, readout, frame marker
  S.tl = { svg, x, wall, top: m.t, bottom: top2 + h2, rows };
  S.tl.frameRule = svg.append("line").attr("class", "frame-rule").attr("y1", m.t).attr("y2", top2 + h2).attr("opacity", 0);
  const rule = svg.append("line").attr("y1", m.t).attr("y2", top2 + h2).attr("stroke", C.market).attr("opacity", 0);
  const bisect = d3.bisector((d) => wall(d.time)).center;
  const readout = $("tl-readout");
  const hasPm = rows.some((d) => d.pm != null), hasK = rows.some((d) => d.kalshi != null);
  const show = (d) => {
    rule.attr("x1", X(d)).attr("x2", X(d)).attr("opacity", 0.35);
    const band90 = d.sd != null ? ` <span class="dim">(90%: ${pct(lo(d))}–${pct(hi(d))})</span>` : "";
    const prices = [["Polymarket", hasPm ? d.pm : undefined], ["Kalshi", hasK ? d.kalshi : undefined]]
      .filter(([, v]) => v !== undefined).map(([p, v]) => ` · ${p} <b>${num(v)}</b>`).join("");
    readout.innerHTML = `<b>${clock(d.t, tz)}</b> · ${pct(d.counted)} counted · raw count <b style="color:${C.raw}">${pct(d.raw)}</b> · model share <b style="color:${C.model}">${pct(d.share)}</b>${band90} · P(${esc(r.a.name)}) model <b style="color:${C.model}">${num(d.P)}</b>${prices}`;
  };
  const idle = () => { rule.attr("opacity", 0); readout.textContent = "Move across the chart to read any minute."; };
  const pick = (e) => rows[bisect(rows, x.invert(d3.pointer(e)[0]))];
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", width - m.l - m.r).attr("height", top2 + h2 - m.t).attr("fill", "transparent")
    .style("cursor", S.units ? "pointer" : null)
    .on("pointermove", (e) => show(pick(e)))
    .on("pointerleave", idle)
    .on("click", (e) => jumpMap(pick(e).time));
  idle();
  markTimeline();
}

// Right-edge labels that do not overlap.
function endLabels(svg, x, items) {
  const pts = items.filter(([d]) => d).map(([d, y, text, color]) => ({ y: y(d), text, color }));
  pts.sort((a, b) => a.y - b.y);
  for (let i = 1; i < pts.length; i++) pts[i].y = Math.max(pts[i].y, pts[i - 1].y + 13);
  svg.selectAll(null).data(pts).join("text").attr("x", x).attr("y", (d) => d.y + 4).attr("class", "end-label").attr("fill", (d) => d.color).text((d) => d.text);
}

function markTimeline() {
  if (!S.tl || !S.units?.frames.length) return;
  const t = PARSE(S.units.frames[S.frame].t);
  const [a, b] = S.tl.x.domain();
  const w = S.tl.wall(t);
  if (w < a || w > b) { S.tl.frameRule.attr("opacity", 0); return; }
  S.tl.frameRule.attr("x1", S.tl.x(w)).attr("x2", S.tl.x(w)).attr("opacity", 1);
}

function jumpMap(time) {
  if (!S.units?.frames.length) return;
  const k = d3.bisector((f) => PARSE(f.t)).right(S.units.frames, time) - 1;
  stop();
  setFrame(Math.max(0, k));
  $("map-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function marketsNote(r) {
  const ms = r.markets ?? [];
  if (!ms.length) { $("markets-note").textContent = "No market listed for this race."; return; }
  const one = (mk) => {
    const yes = esc(r[mk.yes_is]?.name ?? mk.yes_is);
    return mk.question && mk.question !== mk.id
      ? `<i>${esc(mk.question)}</i> (<code>${esc(mk.id)}</code>, YES = ${yes})`
      : `<code>${esc(mk.id)}</code> (YES = ${yes})`;
  };
  const kalshiHidden = ms.some((mk) => mk.platform === "kalshi") && !(S.timeline?.[r.race_id] ?? []).some((d) => d.kalshi != null);
  const lines = d3.groups(ms, (mk) => mk.platform).map(([p, list]) => (p === "kalshi" && kalshiHidden
    ? `Kalshi: ${list.map((mk) => `<code>${esc(mk.id)}</code> (YES = ${esc(r[mk.yes_is]?.name ?? mk.yes_is)})`).join(", ")}; prices not shown, because Kalshi's data terms do not allow republishing them.`
    : `${esc(PLATFORM[p] ?? p)}: ${list.map(one).join("; ")}.`));
  $("markets-note").innerHTML = `${lines.join(" ")} Prices are shown as the probability that ${esc(r.a.name)} wins.`;
}

/* ---------------------------------------------------------------- statistics */

function reason(key) {
  const rs = S.stats?.reasons ?? S.stats?.null_reasons ?? {};
  return rs[key] ?? null;
}

const raceName = (id) => {
  const r = raceById(id);
  if (!r) return id;
  return r.region && !r.name.includes(regionName(r.region)) ? `${r.name}, ${regionName(r.region)}` : r.name;
};
const PLATFORM = { polymarket: "Polymarket", kalshi: "Kalshi" };
const signed = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);

function raceOrder(obj) {
  return Object.keys(obj).sort((a, b) => {
    const ra = raceById(a), rb = raceById(b);
    return (ra?.region ? 1 : 0) - (rb?.region ? 1 : 0) || raceName(a).localeCompare(raceName(b));
  });
}

function table(head, rows, { scroll = false } = {}) {
  const t = `<table><thead><tr>${head.map((h, i) => `<th${i ? ' class="n"' : ""}>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="n"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  return scroll && rows.length > 10 ? `<div class="scroll">${t}</div>` : t;
}

function block(metric, title, def, body) {
  const ev = S.stats.evidence?.[metric];
  const v = S.stats[metric];
  const empty = v == null || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length);
  const inner = empty ? `<p class="na">Not computed: ${esc(reason(metric) ?? "no data for this election")}.</p>` : body(v);
  return `<div class="stat"><h3>${title} ${ev ? evidenceLabel(ev) : ""}</h3><p class="def">${def}</p>${inner}</div>`;
}

function naRow(metric, key, ncol) {
  return [`${esc(PLATFORM[key] ?? raceName(key))}`, `<span class="na">${esc(reason(`${metric}.${key}`) ?? "not available")}</span>`, ...Array(ncol - 2).fill("")];
}

function renderStats() {
  const st = S.stats;
  $("stats-section").hidden = !st;
  if (!st) return;
  const tz = S.meta.tz;
  const t = (iso) => (iso ? clock(iso, tz) : "–");
  const lead = (a, b) => (a && b ? `${Math.round((new Date(b) - new Date(a)) / 60000)}` : "–");
  const html = [
    block("calibration", "Calibration", "Share of minutes between 1% and 95% counted in which the final share was inside the model's 90% band, and the mean error in standard deviations (negative: the model was below the final share).", (v) =>
      table(["Race", "In 90% band", "Mean error (sd)"], raceOrder(v).map((k) => (v[k] ? [esc(raceName(k)), pct(v[k].coverage90_1_95, 0), signed(v[k].mean_z, 2)] : naRow("calibration", k, 3))), { scroll: true })),
    block("settle", "Settle time", `First minute (${esc(tzName(tz, S.meta.poll_close_utc ?? S.meta.date))}) from which each source stayed at 0.9 or more on the eventual winner; for the raw count, from which its leader stopped changing. Lead: minutes the model settled before Polymarket.`, (v) =>
      table(["Race", "Model", "Polymarket", "Kalshi", "Raw count", "Lead (min)"], raceOrder(v).map((k) => {
        const s = v[k];
        return s ? [esc(raceName(k)), t(s.model), t(s.polymarket), t(s.kalshi), t(s.raw), lead(s.model, s.polymarket)] : naRow("settle", k, 6);
      }), { scroll: true })),
    block("catchup", "Catch-up", "Share of the gap between model and price that the price closed over the next 30 minutes (1 = all of it), with a 95% interval.", (v) =>
      table(["Platform", "Share closed in 30 min", "95% interval", "Markets", "Market-minutes"], Object.keys(v).map((k) => (v[k]
        ? [PLATFORM[k] ?? esc(k), num(v[k].slope30), `${num(v[k].lo)} to ${num(v[k].hi)}`, v[k].markets ?? "–", v[k].n?.toLocaleString("en-US") ?? "–"]
        : naRow("catchup", k, 5))))),
    block("early_error", "Early error", "Model projection and raw count minus the final share, in percentage points, at the moment 1%, 5%, 10% and 25% had been counted.", (v) => {
      const cuts = ["1%", "5%", "10%", "25%"];
      return table(["Race", ...cuts.map((c) => `${c}: model / raw`)], raceOrder(v).map((k) => (v[k]
        ? [esc(raceName(k)), ...cuts.map((c) => (v[k][c] ? `<span class="m">${signed(v[k][c].model)}</span> / <span class="r">${signed(v[k][c].raw)}</span>` : "–"))]
        : naRow("early_error", k, 5))), { scroll: true });
    }),
    block("capacity", "Capacity", "Dollars traded by takers on the signal's side within 30 minutes of each model signal, at the signal price or better, and at 2¢ and 5¢ worse.", (v) =>
      table(["Platform", "At the signal price", "+2¢", "+5¢"], Object.keys(v).map((k) => (v[k] ? [PLATFORM[k] ?? esc(k), usd(v[k].signal), usd(v[k].slip2c), usd(v[k].slip5c)] : naRow("capacity", k, 4))))),
    block("arbitrage", "Arbitrage", "Polymarket and Kalshi markets on the same outcome: minutes in which holding both sides cost less than $1 after fees, the largest net edge per $1, and the dollars fillable at those prices.", (v) =>
      table(["Matched pairs", "Minutes with net arbitrage", "Largest net edge", "Fillable"], [[v.pairs ?? "–", v.minutes_with_net_arb ?? "–", v.max_net_edge != null ? `${(100 * v.max_net_edge).toFixed(1)}¢` : "–", usd(v.fillable_usd)]])),
  ];
  $("stats").innerHTML = html.join("");
}

/* ---------------------------------------------------------------- arbitrage */

function renderArb() {
  const pairs = S.arb ?? [];
  $("arb-section").hidden = !pairs.length;
  const root = d3.select("#arb").html("");
  if (!pairs.length) return;
  const tz = S.meta.tz;
  const off = tzOffsetMin(tz, pairs[0].rows[0]?.t ?? S.meta.date) * 60000;
  for (const p of pairs) {
    const rows = p.rows.map((d) => ({ ...d, time: new Date(new Date(d.t).getTime() + off) })).filter((d) => d.net_edge != null);
    const div = root.append("div").attr("class", "arb-pair");
    const positive = rows.filter((d) => d.net_edge > 0).length;
    const best = d3.max(rows, (d) => d.net_edge);
    div.append("p").attr("class", "chart-title").text(`${raceName(p.race_id)}`);
    div.append("p").attr("class", "chart-sub").text(`Polymarket ${p.pm_market} against Kalshi ${p.kalshi_ticker}. ${positive} of ${rows.length} minutes above zero; largest net edge ${best != null ? `${(100 * best).toFixed(1)}¢` : "–"} per $1.`);
    if (!rows.length) continue;
    const width = Math.max(300, Math.min(root.node().clientWidth, 980));
    const m = { l: 52, r: 20, t: 10, b: 28 }, h = 150;
    const x = d3.scaleUtc().domain(d3.extent(rows, (d) => d.time)).range([m.l, width - m.r]);
    const ext = d3.extent(rows, (d) => d.net_edge);
    const y = d3.scaleLinear().domain([Math.min(-0.02, ext[0]), Math.max(0.02, ext[1])]).nice().range([m.t + h, m.t]);
    const svg = div.append("svg").attr("viewBox", `0 0 ${width} ${m.t + h + m.b}`).attr("width", "100%").style("display", "block");
    svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat((v) => `${Math.round(v * 100)}¢`).tickSize(0).tickPadding(6)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${m.t + h})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(width / 90))).tickFormat(d3.utcFormat("%H:%M")).tickSizeOuter(0));
    svg.append("path").datum(rows).attr("fill", C.market).attr("opacity", 0.12)
      .attr("d", d3.area().x((d) => x(d.time)).y0(y(0)).y1((d) => y(Math.max(0, d.net_edge))).curve(d3.curveStepAfter));
    svg.append("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", C.market).attr("stroke-width", 0.8);
    svg.append("path").datum(rows).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.4)
      .attr("d", d3.line().x((d) => x(d.time)).y((d) => y(d.net_edge)).curve(d3.curveStepAfter));
  }
}

/* ---------------------------------------------------------------- main */

async function refresh() {
  try {
    await loadAll(false);
  } catch {
    return; // keep showing the last good data; next tick retries
  }
  if (S.units && S.unitSel) {
    const n = S.units.frames.length;
    $("frame").max = String(n - 1);
    if (S.followLatest && !S.playing) S.frame = n - 1;
    setFrame(S.frame);
  } else if (S.units && S.features) showMap();
  if (S.race) drawTimeline();
  renderStats();
  renderArb();
  const stamp = $("live-stamp");
  if (stamp) stamp.textContent = `Live data from ${LIVE}, last fetched ${new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} (your time), refreshed every 15 seconds.`;
}

async function main() {
  if (!EID) {
    $("title").textContent = "No election chosen";
    $("dek").innerHTML = `Pick one from the <a href="./">list of elections</a>.`;
    return;
  }
  try {
    await loadAll(true);
  } catch (err) {
    $("title").textContent = "Election not found";
    $("dek").textContent = `Could not load data for "${EID}" (${err.message}).`;
    return;
  }
  renderHeader();
  renderFooter();
  initRaces();
  await initMap();
  showMap();
  if (S.race) drawTimeline();
  renderStats();
  renderArb();
  unitReadout(null);
  let rw = innerWidth;
  addEventListener("resize", () => {
    if (innerWidth === rw) return; // mobile URL-bar resizes change only the height
    rw = innerWidth;
    clearTimeout(S.rz);
    S.rz = setTimeout(() => { if (S.unitSel) drawMap(); if (S.race) drawTimeline(); renderArb(); }, 150);
  });
  if (LIVE) setInterval(refresh, 15000);
}

main();
