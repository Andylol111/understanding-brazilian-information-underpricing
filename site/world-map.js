// Homepage world map: the way into each election, drawn like the election maps (flat fills, white
// borders, a light wash and an outline on hover). Countries with elections share one muted accent.
// Choosing a country zooms to it (d3.interpolateZoom, ~700 ms) and swaps in that country's own
// regions in place, in the same projection (no jump): each region coloured by the leader of its
// race in the country's latest election (YAPMS bands), with a side panel listing the country's
// elections and that election's races; a race or a region opens its dashboard. Back / Esc zooms
// out. A search box finds elections, races and regions. Small screens get the list first.
// Natural Earth 1:110m countries (public domain), geo/world.topo.json and the region outlines
// geo/<cc>_regions.topo.json from analysis/geo_build.py.
import * as d3 from "./vendor/d3.js";
import * as topojson from "./vendor/topojson-client.js";
import { C, esc, fmtDate, getJSON, modelP, num } from "./common.js";
import { bandColours, bandColour } from "./dash-agg.js";

const LAND = "#e6e4dd", ACCENT = "#e3b8a0", ACCENT_ON = C.model, NO_RACE = "#efede7";
const DEM = "#1f5fbf", REP = "#c8102e";
const STATUS_LABEL = { live: "Live", scheduled: "Before the count", replay: "Replay" };
const RANK = { live: 0, scheduled: 1, replay: 2 };
const ZOOM_MS = 700;
const REGION_MAPS = new Set(["US", "BR", "CA", "MX", "HU"]); // geo/<cc>_regions.topo.json
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const fold = (s) => String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
// a chance as a pseudo-margin for the bands where a race has no projected share
const pseudo = (p) => { const a = Math.abs(p - 0.5); return Math.sign(p - 0.5) * (a < 0.1 ? 0.5 : a < 0.25 ? 3 : a < 0.4 ? 10 : 20); };

// root: the section to fill; elections: elections.json entries; href(id): the dashboard link;
// countryName(code): display name; dataRoot(id): where an election's files live.
// frames (optional): array receiving rAF intervals during zooms.
export function createWorldMap(root, { topo, elections, href, countryName, dataRoot = () => "data/", frames = null }) {
  const byCountry = d3.group(elections.filter((e) => e.country), (e) => e.country.toUpperCase());
  const features = topojson.feature(topo, topo.objects.countries).features;
  const borders = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
  const sorted = (code) => [...(byCountry.get(code) ?? [])].sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) || b.date.localeCompare(a.date));
  const nameOf = (f) => countryName(f.properties.iso2) || f.properties.name;
  const active = features.filter((f) => byCountry.has(f.properties.iso2));

  root.innerHTML = `
    <div class="wm-search"><label class="visually-hidden" for="wm-q">Search elections, races and regions</label>
      <input id="wm-q" type="search" autocomplete="off" placeholder="Search: Texas Senate, São Paulo, Hungary…" role="combobox" aria-expanded="false" aria-controls="wm-hits">
      <ul id="wm-hits" class="wm-hits" role="listbox" hidden></ul></div>
    <ul class="wm-list" aria-label="Elections by country">${[...byCountry].sort((a, b) => countryName(a[0]).localeCompare(countryName(b[0]))).map(([code, list]) =>
      `<li><button type="button" data-code="${esc(code)}">${esc(countryName(code))}${list.length > 1 ? ` <span>${list.length}</span>` : ""}</button></li>`).join("")}</ul>
    <div class="wm-stage">
      <div class="wm-box">
        <svg class="wm-svg" role="group" aria-label="Countries with elections on this site"></svg>
        <div class="place-card wm-card" hidden></div>
      </div>
      <div class="wm-chooser" hidden></div>
    </div>`;
  const box = root.querySelector(".wm-box"), svg = d3.select(root).select("svg");
  const card = root.querySelector(".wm-card"), chooser = root.querySelector(".wm-chooser");
  const st = { w: 0, h: 0, view: { k: 1, x: 0, y: 0 }, anim: 0, chosen: null, path: null, token: 0 };

  const g = svg.append("g").attr("class", "wm-zoom");
  const land = g.append("g").selectAll("path").data(features).join("path")
    .attr("class", (f) => (byCountry.has(f.properties.iso2) ? "wm-c on" : "wm-c"))
    .attr("fill", (f) => (byCountry.has(f.properties.iso2) ? ACCENT : LAND));
  const mesh = g.append("path").attr("class", "wm-border").datum(borders);
  const regionLayer = g.append("g").attr("class", "wm-regions"); // the chosen country's regions
  const hover = g.append("path").attr("class", "wm-hover"); // wash + outline, as on the election maps
  const marks = g.append("g").selectAll("g").data(active).join("g").attr("class", "wm-mark");
  marks.append("circle").attr("class", "wm-hit");
  marks.filter((f) => byCountry.get(f.properties.iso2).length > 1).append("text").attr("class", "wm-count").text((f) => byCountry.get(f.properties.iso2).length);
  const electionCountries = land.filter((f) => byCountry.has(f.properties.iso2));
  const count = (f) => byCountry.get(f.properties.iso2).length;
  electionCountries.attr("tabindex", 0).attr("role", "button").attr("aria-label", (f) => `${nameOf(f)}: ${count(f)} election${count(f) > 1 ? "s" : ""}`);
  const featureOf = (code) => features.find((x) => x.properties.iso2 === code);
  for (const sel of [electionCountries, marks]) {
    sel.on("pointermove", (ev, f) => { if (!st.chosen) { hover.attr("d", st.path(f)); showCard(cardHTML(f.properties.iso2), ev); } })
      .on("pointerleave", () => { if (!st.chosen) { hover.attr("d", null); card.hidden = true; } })
      .on("focus", (ev, f) => { if (!st.chosen) { hover.attr("d", st.path(f)); showCard(cardHTML(f.properties.iso2), null, ev.currentTarget); } })
      .on("blur", () => { hover.attr("d", null); card.hidden = true; })
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
    regionLayer.selectAll("path").attr("d", st.path);
    marks.attr("transform", (f) => `translate(${centre(f)})`);
    st.view = st.chosen ? fit(st.chosen) : { k: 1, x: 0, y: 0 };
    apply();
  }
  // the label point: the largest polygon's centroid (France's mainland, not its overseas parts)
  const mainPart = (f) => (f.geometry.type === "MultiPolygon" ? { type: "Polygon", coordinates: d3.greatest(f.geometry.coordinates, (c) => d3.geoArea({ type: "Polygon", coordinates: c })) } : f.geometry);
  const centre = (f) => st.path.centroid(mainPart(f));
  function fit(code) {
    const [[x0, y0], [x1, y1]] = st.path.bounds(mainPart(featureOf(code)));
    const panel = st.w > 900 ? 360 : 0; // the side panel covers the right of the map
    const pad = Math.min(st.w, st.h) * 0.08;
    const w = st.w - panel;
    const k = Math.min(14, Math.min((w - 2 * pad) / Math.max(1, x1 - x0), (st.h - 2 * pad) / Math.max(1, y1 - y0)));
    return { k, x: w / 2 - k * (x0 + x1) / 2, y: st.h / 2 - k * (y0 + y1) / 2 };
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
    return `<p class="pc-name"><b>${esc(countryName(code))}</b></p>
      <ul class="wm-elections">${sorted(code).map((e) => `<li><b>${esc(e.name)}</b> <span class="wm-meta">${esc(fmtDate(e.date))} · ${esc(STATUS_LABEL[e.status] ?? e.status)}</span>${e.summary ? `<br><span class="wm-sum">${esc(e.summary)}</span>` : ""}</li>`).join("")}</ul>`;
  }
  function showCard(html, ev, target) {
    card.innerHTML = html;
    card.hidden = false;
    const b = box.getBoundingClientRect();
    let x, y;
    if (ev) { x = ev.clientX - b.left; y = ev.clientY - b.top; } else { const t = target.getBoundingClientRect(); x = t.right - b.left; y = t.top - b.top; }
    card.style.left = `${Math.max(8, Math.min(b.width - card.offsetWidth - 8, x + 14))}px`;
    card.style.top = `${Math.max(8, Math.min(b.height - card.offsetHeight - 8, y + 14))}px`;
  }

  /* ------------------------------------------------ a country: its regions and races */

  // The country's latest election: its races (name, leader, chance, link) and a colour per region.
  async function countryData(code) {
    const e = sorted(code)[0];
    const opt = { optional: true, root: dataRoot(e.id) };
    const meta = await getJSON(`${e.id}/meta.json`, opt);
    const link = (q) => `${href(e.id)}${q}`;
    const races = [], colours = new Map(), regionLink = new Map(), regionInfo = new Map();
    if (meta?.overview) {
      // US: every race of the overview (Senate first), coloured by the model's margin (with polls),
      // else the polls, else the market-implied margin
      const ov = await getJSON(`${e.id}/${meta.overview}`, opt);
      for (const [o, off] of Object.entries(ov?.offices ?? {})) for (const r of off.races ?? []) {
        const mg = r.margins ?? {}, p = r.pm?.price ?? r.kalshi?.price ?? null;
        const m = mg.model_polls ?? mg.model_v2 ?? mg.model ?? mg.estimate ?? mg.polls ?? mg.market ?? null;
        const name = `${r.state_name} ${o === "governor" ? "governor" : "Senate"}${r.special ? " (special)" : ""}`;
        const lead = p == null ? null : p >= 0.5 ? r.dem_side?.name ?? "Democratic side" : r.candidates?.R ?? "Republican side";
        const q = `&race=${encodeURIComponent(r.race_id ?? r.race_key)}&region=${r.state}&level=units`;
        races.push({ name, office: o, lead, chance: p == null ? null : Math.max(p, 1 - p), dem: p != null && p >= 0.5, href: r.units ? link(q) : link(""), region: r.state });
        if (o === "senate" || !colours.has(r.state)) {
          if (m != null) colours.set(r.state, bandColour(m, bandColours(DEM), bandColours(REP)));
          regionLink.set(r.state, r.units ? link(q) : link(""));
          regionInfo.set(r.state, `${name}: ${lead ? `${esc(lead)} ${num(Math.max(p, 1 - p))}` : "no price"}`);
        }
      }
    } else if (meta) {
      // other countries: each region's race (the main race's office first), at its last row
      const tl = await getJSON(`${e.id}/timeline.json`, opt);
      const last = (id) => tl?.[id]?.at?.(-1) ?? null;
      for (const r of meta.races ?? []) {
        const row = last(r.race_id), P = modelP(row) ?? row?.pm ?? row?.kalshi ?? null;
        const lead = P == null ? null : P >= 0.5 ? r.a.name : r.b.name;
        const q = `&race=${encodeURIComponent(r.race_id)}${r.region ? `&region=${r.region}&level=units` : ""}`;
        races.push({ name: r.name, lead, chance: P == null ? null : Math.max(P, 1 - P), href: link(q), region: r.region, colour: P == null ? null : P >= 0.5 ? r.a.color : r.b.color });
        if (r.region && !colours.has(r.region) && P != null) {
          const pt = row?.share != null ? (2 * row.share - 1) * 100 : pseudo(P);
          colours.set(r.region, bandColour(pt, bandColours(r.a.color), bandColours(r.b.color)));
          regionLink.set(r.region, link(q));
          regionInfo.set(r.region, `${esc(r.name)}: ${esc(lead)} ${num(Math.max(P, 1 - P))}`);
        }
      }
    }
    return { e, meta, races, colours, regionLink, regionInfo };
  }

  async function choose(code) {
    if (!byCountry.has(code)) return;
    card.hidden = true;
    hover.attr("d", null);
    const token = ++st.token;
    st.chosen = code;
    root.classList.add("wm-zoomed");
    land.classed("dim", (f) => f.properties.iso2 !== code).classed("sel", (f) => f.properties.iso2 === code);
    regionLayer.selectAll("*").remove();
    chooser.hidden = true;
    // the country's figures and outlines load during the zoom
    const data = countryData(code).catch(() => null);
    const shapes = REGION_MAPS.has(code) ? getJSON(`geo/${code.toLowerCase()}_regions.topo.json`, { optional: true }).catch(() => null) : Promise.resolve(null);
    zoomTo(fit(code), async () => {
      const [d, t] = await Promise.all([data, shapes]);
      if (st.token !== token) return;
      if (t && d) drawRegions(t, d);
      panel(code, d);
    });
  }

  function drawRegions(t, d) {
    const regs = topojson.feature(t, t.objects.regions).features;
    land.filter((f) => f.properties.iso2 === st.chosen).classed("under", true); // its own regions cover it
    const paths = regionLayer.selectAll("path").data(regs).join("path").attr("class", "wm-r").attr("d", st.path)
      .attr("fill", (f) => d.colours.get(f.id) ?? NO_RACE).style("opacity", 0);
    regionLayer.append("path").datum(topojson.mesh(t, t.objects.regions, (a, b) => a !== b)).attr("class", "wm-rborder").attr("d", st.path).style("opacity", 0);
    regionLayer.selectAll("path").transition().duration(reducedMotion() ? 0 : 300).style("opacity", 1);
    paths.filter((f) => d.regionLink.has(f.id)).attr("tabindex", 0).attr("role", "link").attr("aria-label", (f) => d.regionInfo.get(f.id)?.replace(/<[^>]+>/g, "") ?? f.id)
      .on("pointermove", (ev, f) => { hover.attr("d", st.path(f)); showCard(`<p class="pc-name"><b>${d.regionInfo.get(f.id)}</b></p><p class="pc-hint">Click to open its map.</p>`, ev); })
      .on("pointerleave", () => { hover.attr("d", null); card.hidden = true; })
      .on("click", (ev, f) => leave(d.regionLink.get(f.id)))
      .on("keydown", (ev, f) => { if (ev.key === "Enter") leave(d.regionLink.get(f.id)); });
  }

  // the side panel: the country's elections; the latest one's races (chance, leader), linked
  function panel(code, d) {
    const list = sorted(code);
    const races = d?.races ?? [];
    const officeName = (o) => (o === "governor" ? "Governor" : o === "senate" ? "Senate" : "");
    const groups = d3.groups(races, (r) => r.office ?? "");
    chooser.innerHTML = `<p class="wm-ch-head"><b>${esc(countryName(code))}</b> <button type="button" class="crumb-back">‹ All countries</button></p>
      <ul class="wm-el">${list.map((e, i) => `<li><a href="${href(e.id)}"><b>${esc(e.name)}</b><span class="wm-meta">${esc(fmtDate(e.date))} · ${esc(STATUS_LABEL[e.status] ?? e.status)}${i === 0 && races.length ? " · races below" : ""}</span></a></li>`).join("")}</ul>
      ${races.length ? groups.map(([o, rs]) => `<p class="wm-ch-sub">${esc(officeName(o) || d.e.name)}: chance the leader wins</p>
        <ul class="wm-races">${rs.map((r) => `<li><a href="${r.href}"><span>${esc(r.name)}</span><span class="wm-lead">${r.lead ? `${esc(r.lead)} <b>${num(r.chance)}</b>` : "<small>no price</small>"}</span></a></li>`).join("")}</ul>`).join("") : ""}`;
    chooser.hidden = false;
    chooser.querySelector(".crumb-back").addEventListener("click", back);
    chooser.querySelectorAll("a").forEach((a) => a.addEventListener("click", (ev) => { if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); leave(a.href); } }));
    chooser.querySelector("a")?.focus({ preventScroll: true });
  }

  function back() {
    st.chosen = null;
    st.token++;
    chooser.hidden = true;
    card.hidden = true;
    root.classList.remove("wm-zoomed");
    regionLayer.selectAll("*").remove();
    hover.attr("d", null);
    land.classed("dim", false).classed("sel", false).classed("under", false);
    zoomTo({ k: 1, x: 0, y: 0 });
  }
  // a short fade before the dashboard loads (its map opens framed on the same country)
  function leave(url) {
    if (!url) return;
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

  /* ------------------------------------------------ search */

  // index: elections, countries, every election's regions and races, the US overview's races;
  // loaded on first use. Fuzzy: each query word must start a word of the entry (accents ignored).
  const q = root.querySelector("#wm-q"), hits = root.querySelector("#wm-hits");
  let index = null, results = [], cur = -1;
  async function buildIndex() {
    const out = [];
    for (const [code, list] of byCountry) out.push({ text: countryName(code), kind: "Country", go: () => { q.blur(); hits.hidden = true; choose(code); } });
    await Promise.all(elections.map(async (e) => {
      out.push({ text: e.name, kind: `Election · ${fmtDate(e.date)}`, url: href(e.id) });
      const meta = await getJSON(`${e.id}/meta.json`, { optional: true, root: dataRoot(e.id) }).catch(() => null);
      if (!meta) return;
      for (const r of meta.regions ?? []) out.push({ text: r.name, kind: `Region · ${e.name}`, url: `${href(e.id)}&region=${r.id}&level=units` });
      for (const r of meta.races ?? []) out.push({ text: r.name, kind: `Race · ${e.name}`, url: `${href(e.id)}&race=${encodeURIComponent(r.race_id)}${r.region ? `&region=${r.region}&level=units` : ""}` });
      if (meta.overview) {
        const ov = await getJSON(`${e.id}/${meta.overview}`, { optional: true, root: dataRoot(e.id) }).catch(() => null);
        for (const [o, off] of Object.entries(ov?.offices ?? {})) for (const r of off.races ?? []) {
          out.push({ text: `${r.state_name} ${o === "governor" ? "governor" : "Senate"}${r.special ? " special" : ""}`, kind: `Race · ${e.name}`, url: `${href(e.id)}&race=${encodeURIComponent(r.race_id ?? r.race_key)}&region=${r.state}&level=units` });
        }
      }
    }));
    // one entry per text and destination
    const seen = new Set();
    return out.filter((x) => { const k = `${x.text}|${x.url ?? x.kind}`; if (seen.has(k)) return false; seen.add(k); return true; }).map((x) => ({ ...x, words: fold(x.text).split(/[^a-z0-9]+/).filter(Boolean) }));
  }
  const score = (x, qw, qs) => {
    if (!qw.every((w) => x.words.some((v) => v.startsWith(w)))) return fold(x.text).includes(qs) ? 1 : 0;
    return 2 + (x.words[0]?.startsWith(qw[0]) ? 1 : 0) + (x.kind.startsWith("Race") ? 0.5 : 0) - x.words.length * 0.01;
  };
  function search() {
    const qs = fold(q.value.trim());
    if (!qs || !index) { hits.hidden = true; q.setAttribute("aria-expanded", "false"); results = []; return; }
    const qw = qs.split(/[^a-z0-9]+/).filter(Boolean);
    results = index.map((x) => [x, score(x, qw, qs)]).filter(([, s]) => s > 0).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([x]) => x);
    cur = results.length ? 0 : -1;
    hits.innerHTML = results.length ? results.map((x, i) => `<li role="option" id="wm-hit-${i}" aria-selected="${i === cur}"><b>${esc(x.text)}</b> <span class="wm-meta">${esc(x.kind)}</span></li>`).join("") : `<li class="wm-none">No match</li>`;
    hits.hidden = false;
    q.setAttribute("aria-expanded", "true");
    hits.querySelectorAll("[role=option]").forEach((li, i) => li.addEventListener("mousedown", (ev) => { ev.preventDefault(); open(results[i]); }));
  }
  const open = (x) => { if (!x) return; if (x.go) x.go(); else leave(x.url); };
  q.addEventListener("focus", async () => { index ??= await buildIndex(); search(); }, { once: false });
  q.addEventListener("input", async () => { index ??= await buildIndex(); search(); });
  q.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!results.length) return;
      cur = (cur + (ev.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
      hits.querySelectorAll("[role=option]").forEach((li, i) => li.setAttribute("aria-selected", String(i === cur)));
      q.setAttribute("aria-activedescendant", `wm-hit-${cur}`);
    } else if (ev.key === "Enter") { ev.preventDefault(); open(results[cur >= 0 ? cur : 0]); }
    else if (ev.key === "Escape") { hits.hidden = true; ev.stopPropagation(); }
  });
  q.addEventListener("blur", () => setTimeout(() => { hits.hidden = true; q.setAttribute("aria-expanded", "false"); }, 150));

  new ResizeObserver(layout).observe(box);
  layout();
  return { choose, back, arrive };
}
