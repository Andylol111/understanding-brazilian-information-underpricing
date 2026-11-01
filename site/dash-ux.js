// The map's controls beyond hover and click, shared by every election page (replays, pre-count and the live US night):
//  - keyboard shortcuts (zoom, pan, step, layers, search, help) and the help overlay;
//  - search for a place by name;
//  - the Layers menu: a second encoding over the fill (hatching for the share counted, dots for the votes left, an
//    outline for the turnout against the expectation) and the bivariate fill (margin x counted), each with its opacity;
//  - multi-select (shift-click, lasso) with a Selection panel, and a sparkline of the model's estimate for a unit;
//  - the view (zoom, selection, layers, time) kept in the address hash, so a link shares it.
// The page (election.js) owns the state S and passes what the controls need in `host`.
import * as d3 from "./vendor/d3.js";
import { C, esc, pct, votesShort, clock, plural, sideLabel } from "./common.js";
import { regionAgg, BIV_LEVELS, bandColours, bivColour } from "./dash-agg.js";

const DOT_R = 13;        // largest dot of the votes-left layer, CSS px (as in dash-engine.js)
const HASH_IDS_MAX = 400; // a selection of more units than this is not written to the address
const SPARK = { w: 244, h: 58 };

const ENCODINGS = [
  ["none", "None"],
  ["hatch", "Hatching: share still to count"],
  ["dots", "Dots: votes left"],
  ["outline", "Outline: turnout against expectation"],
];

const HELP = [
  ["+ / −", "Zoom in / out"],
  ["0", "Reset the view"],
  ["← ↑ → ↓", "Pan the map"],
  ["Space", "Play or pause"],
  [", / .", "Previous / next map frame"],
  ["L", "Layers menu"],
  ["/", "Search for a place by name"],
  ["Shift + click", "Add a place to the selection, or remove it"],
  ["Shift + drag", "Draw a lasso round the places to select"],
  ["Drag", "Move the map"],
  ["Ctrl or ⌘ + scroll", "Zoom (a pinch on a trackpad does the same)"],
  ["Esc", "Clear the selection; close a menu"],
  ["?", "This list"],
];

const norm = (s) => String(s ?? "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const signedPt = (v) => (v == null || !Number.isFinite(v) ? "–" : `${v >= 0 ? "+" : "−"}${Math.abs(100 * v).toFixed(1)} pt`);
const isTyping = (el) => !!el && (el.isContentEditable || ["TEXTAREA", "SELECT"].includes(el.tagName) || (el.tagName === "INPUT" && el.type !== "range" && el.type !== "checkbox" && el.type !== "radio"));

// host: { S, $, frameIndex, render(), canSelect(), layerOn(j), unitNoun(), regionName(reg), setMode(m), modes(), stepFrame(dir),
//         goTo(item), searchItems(), hashTime(), timeState(), setTimeFromHash(t), afterZoom(fn) }
export function createUx(host) {
  const { S, $ } = host;
  const sel = new Set(); // selected features (the map's indexes)
  let selVersion = 0;
  const over = { enc: "none", encOp: 80, fillOp: 100 };
  const unitsId = new WeakMap();
  let unitsSeq = 0;
  const idOf = (U) => { let v = unitsId.get(U); if (!v) unitsId.set(U, (v = ++unitsSeq)); return v; };
  const box = $("map");

  /* ------------------------------------------------------------ the unit's numbers: votes, sparkline */

  // the units' share series over the night (the model's estimate and the count), per set of units
  const seriesMemo = new WeakMap();
  function series(U, js) {
    let m = seriesMemo.get(U);
    if (!m) seriesMemo.set(U, (m = new Map()));
    const sig = js.length === 1 ? `${js[0]}` : `${js.length}|${selVersion}`;
    if (m.has(sig)) return m.get(sig);
    const t = [], proj = [], raw = [];
    for (let k = 0; k < U.K; k++) {
      if (!(U.times[k] > -1e15)) continue; // a frame without a time (the estimate before the count)
      const g = regionAgg(U, k, js);
      t.push(U.times[k]); proj.push(g.proj); raw.push(g.raw);
    }
    const out = { t, proj, raw };
    if (m.size > 40) m.clear();
    m.set(sig, out);
    return out;
  }

  // The sparkline: the model's estimate (line) and the count so far (blue) for side a, with the current frame marked.
  function sparkHTML(U, js, k, race, label) {
    if (!S.countTimeline || !(U.K >= 3)) return "";
    const s = series(U, js);
    if (s.t.length < 3) return "";
    const { w, h } = SPARK, left = 26, right = 4, top = 4, bottom = 12;
    const vals = [...s.proj, ...s.raw].filter((v) => v != null);
    if (!vals.length) return "";
    const lo = Math.max(0, Math.min(0.5, ...vals) - 0.03), hi = Math.min(1, Math.max(0.5, ...vals) + 0.03);
    const x = d3.scaleLinear().domain([s.t[0], s.t[s.t.length - 1]]).range([left, w - right]);
    const y = d3.scaleLinear().domain([lo, hi]).range([h - bottom, top]);
    const line = (arr) => d3.line().defined((d) => d != null).x((_, i) => x(s.t[i])).y((d) => y(d))(arr);
    const cur = k >= 0 && U.times[k] > -1e15 ? x(Math.max(s.t[0], Math.min(s.t[s.t.length - 1], U.times[k]))) : null;
    const here = k >= 0 ? regionAgg(U, k, js).proj : null;
    const first = s.proj.find((v) => v != null), last = [...s.proj].reverse().find((v) => v != null);
    const aria = `${label}'s share over the night, model estimate: from ${pct(first, 0)} to ${pct(last, 0)}`;
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(aria)}">
      <line x1="${left}" x2="${w - right}" y1="${y(0.5)}" y2="${y(0.5)}" stroke="#c9c6bd" stroke-dasharray="3 3"/>
      <path d="${line(s.raw) ?? ""}" fill="none" stroke="${C.raw}" stroke-width="1.4"/>
      <path d="${line(s.proj) ?? ""}" fill="none" stroke="${C.model}" stroke-width="1.8"/>
      ${cur != null ? `<line x1="${cur}" x2="${cur}" y1="${top}" y2="${h - bottom}" stroke="#1b1b1b" stroke-width="1"/>${here != null ? `<circle cx="${cur}" cy="${y(here)}" r="3" fill="${C.model}" stroke="#fff"/>` : ""}` : ""}
      <text x="${left - 3}" y="${top + 7}" text-anchor="end" class="spark-t">${Math.round(100 * hi)}%</text>
      <text x="${left - 3}" y="${h - bottom}" text-anchor="end" class="spark-t">${Math.round(100 * lo)}%</text>
      <text x="${left}" y="${h - 1}" class="spark-t">${clock(s.t[0], S.tz)}</text>
      <text x="${w - right}" y="${h - 1}" text-anchor="end" class="spark-t">${clock(s.t[s.t.length - 1], S.tz)}</text>
    </svg>
    <p class="spark-note"><i style="background:${C.model}"></i>the model's estimate of ${esc(label)}'s share <i style="background:${C.raw}"></i>the count so far</p>`;
  }

  // Votes of one unit at frame k: expected, counted so far (as a share of the expected), left (the model's estimate where
  // the file has one). [] where the file has no expected votes.
  function voteRows(U, k, j) {
    const e = U.V0[j];
    if (!(e > 0) || k < 0) return [];
    const f = U.F[k * U.n + j] / 1000;
    const left = U.rem ? U.rem.votes[k * U.n + j] : e * (1 - f);
    return [["Expected votes", `≈ ${votesShort(e)}`], ["Counted so far", `≈ ${votesShort(e * f)}`], ...(Number.isFinite(left) ? [["Votes left", `≈ ${votesShort(left)}`]] : [])];
  }

  /* ------------------------------------------------------------ selection */

  const unitsOf = () => { const out = []; for (const i of sel) { const j = S.featIdx?.[i]; if (j >= 0) out.push(j); } return out; };
  const panel = document.createElement("aside");
  panel.id = "sel-panel";
  panel.className = "map-ui sel-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "Selection");
  box.append(panel);
  panel.addEventListener("click", (e) => { if (e.target.closest(".sp-clear")) clear(); });

  let shown = false; // what the map is told to outline
  function syncMap() {
    const on = host.canSelect() && sel.size > 0;
    if (on || shown) S.map?.setSelection(on ? sel : []);
    shown = on;
  }

  function changed() {
    selVersion++;
    syncMap();
    renderPanel();
    queueHash();
  }
  function clear() {
    if (!sel.size) return false;
    sel.clear();
    changed();
    return true;
  }
  function toggle(i) {
    if (sel.has(i)) sel.delete(i); else sel.add(i);
    changed();
  }
  function add(list) {
    for (const i of list) sel.add(i);
    changed();
  }
  // units rebound (a live refresh, another race or layer): the selection keeps the units still in the map
  function rebind() {
    let drop = false;
    for (const i of sel) if (!(S.featIdx?.[i] >= 0) || !host.layerOn(S.featIdx[i])) { sel.delete(i); drop = true; }
    selVersion++;
    syncMap();
    renderPanel();
    if (drop) queueHash();
  }

  // the two sides' names for a set of units (one race: its candidates; several: party letters)
  function sidesOf(js) {
    const r0 = S.unitRace[js[0]];
    if (js.every((j) => S.unitRace[j].a.name === r0.a.name && S.unitRace[j].b.name === r0.b.name)) return { a: r0.a.name, color: r0.a.color, race: r0 };
    const lab = sideLabel(r0.a, true, r0.b);
    return { a: lab.length <= 2 ? lab : "the first side", color: r0.a.color, race: r0 };
  }

  let panelKey = "", lastPanel = 0;
  function renderPanel() {
    const U = S.units, js = unitsOf();
    if (!U || !js.length || !host.canSelect()) { panel.hidden = true; panelKey = ""; return; }
    const k = host.frameIndex(S.preview ?? S.t);
    const key = `${selVersion}|${k}|${idOf(U)}|${S.mode}`;
    if (key === panelKey && !panel.hidden) return;
    panelKey = key;
    const g = regionAgg(U, k, js), side = sidesOf(js), a = esc(side.a);
    let left = 0, expected = 0;
    for (const j of js) {
      const e = U.V0[j] > 0 ? U.V0[j] : 0;
      expected += e;
      left += k < 0 ? e : U.rem ? (Number.isFinite(U.rem.votes[k * U.n + j]) ? U.rem.votes[k * U.n + j] : 0) : e * (1 - U.F[k * U.n + j] / 1000);
    }
    const swing = g.raw != null && g.prior != null ? g.raw - g.prior : null;
    const rows = S.countTimeline
      ? [[`${a}, votes counted`, pct(g.raw)], [`${a}, model estimate`, pct(g.proj)], ["Model estimate minus count", g.raw != null && g.proj != null ? signedPt(g.proj - g.raw) : "–"],
        ["Counted", pct(g.counted, 0)], ...(expected > 0 ? [["Votes left", `≈ ${votesShort(left)}`]] : []), ["Swing from the comparison election", signedPt(swing)]]
      : [[`${a}, result`, pct(g.raw)], [`${a}, comparison election`, pct(g.prior)], ["Swing", signedPt(swing)]];
    const names = js.slice(0, 4).map((j) => U.names[j] ?? U.unit_ids[j]);
    const noun = host.unitNoun();
    panel.innerHTML = `<div class="sp-head"><b>Selection</b> <span>${js.length.toLocaleString("en-US")} ${plural(noun, js.length)}</span><button type="button" class="sp-clear">Clear</button></div>
      <table class="kv"><tbody>${rows.map(([x, v]) => `<tr><td>${x}</td><td class="n">${v}</td></tr>`).join("")}</tbody></table>
      ${sparkHTML(U, js, k, side.race, side.a)}
      <p class="pc-hint">${esc(names.join(", "))}${js.length > names.length ? ` and ${js.length - names.length} more` : ""}. Shares weigh each ${esc(noun)} by its expected vote. Shift-click adds or removes one; Esc clears.</p>`;
    panel.hidden = false;
  }

  /* ------------------------------------------------------------ layers menu (second encoding, bivariate fill) */

  const tools = document.createElement("div");
  tools.className = "map-ui map-tools";
  tools.setAttribute("role", "toolbar");
  tools.setAttribute("aria-label", "Map view");
  tools.innerHTML = `<button type="button" data-act="in" title="Zoom in (+)" aria-label="Zoom in">+</button><button type="button" data-act="out" title="Zoom out (−)" aria-label="Zoom out">−</button>
    <button type="button" data-act="reset" title="Reset the view (0)" aria-label="Reset the view">Fit</button>
    <button type="button" data-act="search" title="Search for a place (/)" aria-haspopup="dialog" aria-expanded="false">Search</button>
    <button type="button" data-act="layers" title="Layers (L)" aria-haspopup="dialog" aria-expanded="false">Layers</button>
    <button type="button" data-act="help" title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">?</button>`;
  box.append(tools);

  const menu = document.createElement("div");
  menu.className = "map-ui map-pop layers-menu";
  menu.id = "layers-menu";
  menu.hidden = true;
  menu.setAttribute("role", "dialog");
  menu.setAttribute("aria-label", "Layers");
  box.append(menu);

  function renderMenu() {
    const turn = !!S.units?.turnout;
    const hasBiv = host.modes().includes("biv");
    menu.innerHTML = `<div class="pop-head"><b>Layers</b><button type="button" class="pop-x" aria-label="Close">×</button></div>
      <fieldset><legend>Fill</legend>
        ${hasBiv ? `<label class="pop-row"><input type="checkbox" id="lm-biv" ${S.mode === "biv" ? "checked" : ""}> Margin and share counted together</label>` : ""}
        <label class="pop-row">Opacity <input type="range" id="lm-fo" min="10" max="100" step="5" value="${over.fillOp}" aria-label="Fill opacity"><output>${over.fillOp}%</output></label>
        <p class="pop-note">The fill itself is chosen with the buttons at the top of the map.</p>
      </fieldset>
      <fieldset><legend>Second layer</legend>
        ${ENCODINGS.map(([v, l]) => `<label class="pop-row"><input type="radio" name="lm-enc" value="${v}" ${over.enc === v ? "checked" : ""} ${v === "outline" && !turn ? "disabled" : ""}> ${l}${v === "outline" && !turn ? " (no turnout figures here)" : ""}</label>`).join("")}
        <label class="pop-row">Opacity <input type="range" id="lm-eo" min="10" max="100" step="5" value="${over.encOp}" ${over.enc === "none" ? "disabled" : ""} aria-label="Second layer opacity"><output>${over.encOp}%</output></label>
      </fieldset>`;
  }
  menu.addEventListener("input", (e) => {
    if (e.target.id === "lm-fo") over.fillOp = +e.target.value;
    else if (e.target.id === "lm-eo") over.encOp = +e.target.value;
    else return;
    e.target.nextElementSibling.textContent = `${e.target.value}%`;
    pushOver();
  });
  menu.addEventListener("change", (e) => {
    if (e.target.name === "lm-enc") {
      over.enc = e.target.value;
      const eo = menu.querySelector("#lm-eo");
      if (eo) eo.disabled = over.enc === "none";
      pushOver();
    } else if (e.target.id === "lm-biv") {
      if (e.target.checked) { prevMode = S.mode === "biv" ? prevMode : S.mode; host.setMode("biv"); } else host.setMode(prevMode && prevMode !== "biv" ? prevMode : host.modes().find((m) => m !== "biv"));
    }
  });
  menu.addEventListener("click", (e) => { if (e.target.closest(".pop-x")) closeMenus(); });
  let prevMode = null;

  // the engine draws the layers; the legend and the address follow
  function pushOver() {
    S.map?.setOver({ enc: over.enc, encOp: over.encOp / 100, fillOp: over.fillOp / 100 });
    S.legendKey = null;
    host.render();
    queueHash();
  }

  /* ------------------------------------------------------------ legends for the layers */

  function bivLegend(root, race) {
    const row = (nm, col) => `<tr><th title="${esc(nm)}">${esc(nm)}</th>${BIV_LEVELS.map((_, lv) => `<td>${bandColours(col).map((c) => `<i style="background:${bivColour(c, 0, lv)}"></i>`).join("")}</td>`).join("")}</tr>`;
    root.append("div").html(`<table class="biv-legend"><thead><tr><th></th>${BIV_LEVELS.map((l) => `<th>${l.label}</th>`).join("")}</tr></thead><tbody>${row(race.a.name, race.a.color)}${row(race.b.name, race.b.color)}</tbody></table>
      <p class="legend-note">Who is ahead (four strengths each, as in the margin map) and how much of the vote is counted: paler while little is counted.</p><p class="legend-count"></p>`);
  }

  // the second layer's key, above the "no data" line
  function encLegend(root) {
    const U = S.units;
    if (over.enc === "none" || !U) return;
    if (over.enc === "hatch") {
      root.append("p").attr("class", "legend-note").html(`<span class="lg-hatch" aria-hidden="true"><i></i><i></i><i></i></span> Hatching: share of the vote still to count (denser: less counted)`);
    } else if (over.enc === "outline") {
      if (!U.turnout) return;
      root.append("p").attr("class", "legend-note").html(`<span class="lg-outline" aria-hidden="true"></span> Outline: turnout against the model's expectation, −20% to +20% (the turnout map's colours)`);
    } else if (over.enc === "dots") {
      const max = U.rem ? U.remScale?.maxRem : d3.max(U.V0);
      if (!(max > 0)) return;
      const nice = (v) => { const p = 10 ** Math.floor(Math.log10(v)); return [1, 2, 5, 10].map((m) => m * p).reverse().find((x) => x <= v) ?? p; };
      const sizes = [...new Set([nice(max), nice(max / 6), nice(max / 40)])].filter((v) => v > 0);
      const rad = (v) => Math.max(0.9, DOT_R * Math.sqrt(v / max));
      let cx = DOT_R + 1;
      const parts = sizes.map((v) => { const r = rad(v), c = cx + r; cx = c + r + 30; return `<circle cx="${c}" cy="${DOT_R + 1}" r="${r}" fill="#1b1b1b" fill-opacity="0.8" stroke="#fcfbf8" stroke-width="0.7"/><text class="legend-text" x="${c + r + 3}" y="${DOT_R + 5}">${votesShort(v)}</text>`; });
      root.append("svg").attr("viewBox", `0 0 ${cx} ${2 * DOT_R + 2}`).attr("width", cx).attr("height", 2 * DOT_R + 2).html(parts.join(""));
      root.append("p").attr("class", "legend-note").html(`Dots: votes still to count${U.rem ? " (the model's estimate)" : " (the expected vote not yet counted)"}.`);
    }
  }

  /* ------------------------------------------------------------ search */

  const search = document.createElement("div");
  search.className = "map-ui map-pop map-search";
  search.hidden = true;
  search.setAttribute("role", "dialog");
  search.setAttribute("aria-label", "Search the map");
  search.innerHTML = `<label class="visually-hidden" for="ms-input">Search for a place by name</label>
    <input id="ms-input" type="search" autocomplete="off" spellcheck="false" placeholder="Search by name" role="combobox" aria-expanded="false" aria-controls="ms-list" aria-autocomplete="list">
    <ul id="ms-list" role="listbox" aria-label="Matches"></ul><p class="pop-note" id="ms-note">Type a name; ↑ ↓ then Enter to go there.</p>`;
  box.append(search);
  const input = search.querySelector("input"), list = search.querySelector("ul");
  let results = [], active = 0;

  let cand = null, candKey = "";
  function candidates() {
    const key = `${S.units ? idOf(S.units) : 0}|${S.layer}`;
    if (cand && key === candKey) return cand;
    candKey = key;
    cand = [];
    for (const it of host.searchItems()) cand.push({ ...it, key: norm(`${it.name} ${it.where ?? ""}`), nname: norm(it.name) });
    return cand;
  }
  function find(q) {
    const t = norm(q).trim();
    if (!t) return [];
    const words = t.split(/\s+/);
    const hits = candidates().filter((c) => words.every((w) => c.key.includes(w)));
    const score = (c) => (c.nname === t ? 0 : c.nname.startsWith(t) ? 1 : c.nname.split(/[\s-]/).some((w) => w.startsWith(words[0])) ? 2 : 3);
    return hits.sort((a, b) => score(a) - score(b) || a.name.length - b.name.length || a.name.localeCompare(b.name)).slice(0, 8);
  }
  function drawResults() {
    list.innerHTML = results.map((r, n) => `<li role="option" id="ms-${n}" aria-selected="${n === active}" data-n="${n}"><span>${esc(r.name)}</span> <small>${esc(r.where ?? "")}${r.kind ? ` · ${esc(r.kind)}` : ""}</small></li>`).join("");
    input.setAttribute("aria-expanded", String(results.length > 0));
    if (results.length) input.setAttribute("aria-activedescendant", `ms-${active}`); else input.removeAttribute("aria-activedescendant");
    search.querySelector("#ms-note").textContent = input.value.trim() && !results.length ? "Nothing by that name on this map." : "Type a name; ↑ ↓ then Enter to go there.";
  }
  input.addEventListener("input", () => { results = find(input.value); active = 0; drawResults(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length) { active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length; drawResults(); }
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(results[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeMenus();
    }
  });
  list.addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) pick(results[+li.dataset.n]); });
  function pick(item) {
    if (!item) return;
    closeMenus();
    host.goTo(item);
  }

  /* ------------------------------------------------------------ open / close the menus, help */

  const help = document.createElement("div");
  help.className = "help-overlay";
  help.hidden = true;
  help.setAttribute("role", "dialog");
  help.setAttribute("aria-modal", "true");
  help.setAttribute("aria-label", "Keyboard shortcuts");
  help.innerHTML = `<div class="help-box"><div class="pop-head"><b>Keyboard shortcuts</b><button type="button" class="pop-x" aria-label="Close">×</button></div>
    <table>${HELP.map(([k, t]) => `<tr><th scope="row">${k.split(" + ").map((x) => `<kbd>${x}</kbd>`).join(" + ")}</th><td>${t}</td></tr>`).join("")}</table>
    <p class="pop-note">Shortcuts do not fire while you are typing in a box.</p></div>`;
  document.body.append(help);
  let helpFrom = null;
  help.addEventListener("click", (e) => { if (e.target === help || e.target.closest(".pop-x")) closeHelp(); });
  function openHelp() {
    closeMenus();
    helpFrom = document.activeElement;
    help.hidden = false;
    help.querySelector(".pop-x").focus();
  }
  function closeHelp() {
    if (help.hidden) return false;
    help.hidden = true;
    helpFrom?.focus?.();
    return true;
  }
  // keeps Tab inside the open help dialog
  help.addEventListener("keydown", (e) => { if (e.key === "Tab") { e.preventDefault(); help.querySelector(".pop-x").focus(); } });

  const setOpen = (act, on) => tools.querySelector(`[data-act="${act}"]`)?.setAttribute("aria-expanded", String(on));
  function closeMenus() {
    const was = !menu.hidden || !search.hidden;
    menu.hidden = true;
    search.hidden = true;
    setOpen("layers", false);
    setOpen("search", false);
    return was;
  }
  function openLayers() {
    const was = !menu.hidden;
    closeMenus();
    if (was) return;
    renderMenu();
    menu.hidden = false;
    setOpen("layers", true);
    menu.querySelector("input:not([disabled])")?.focus();
  }
  function openSearch() {
    const was = !search.hidden;
    closeMenus();
    if (was) return;
    search.hidden = false;
    setOpen("search", true);
    input.value = "";
    results = [];
    drawResults();
    input.focus();
  }
  tools.addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (act === "in") S.map?.zoomBy(1.5);
    else if (act === "out") S.map?.zoomBy(1 / 1.5);
    else if (act === "reset") S.map?.resetView();
    else if (act === "search") openSearch();
    else if (act === "layers") openLayers();
    else if (act === "help") openHelp();
  });

  /* ------------------------------------------------------------ keys */

  // true when the key was used here. Never while typing in a box (Esc in the search box closes it).
  function key(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return false;
    if (isTyping(e.target)) return false;
    const k = e.key;
    if (!help.hidden) {
      if (k === "Escape" || k === "?") { closeHelp(); e.preventDefault(); return true; }
      return false;
    }
    if (k === "?") { e.preventDefault(); openHelp(); return true; }
    if (k === "Escape") {
      if (closeMenus()) return true;
      if (sel.size) { clear(); S.pinned >= 0 && host.releasePin(); return true; }
      return false;
    }
    if (k === "/") { e.preventDefault(); openSearch(); return true; }
    if (k === "l" || k === "L") { e.preventDefault(); openLayers(); return true; }
    if (k === "," || k === ".") { e.preventDefault(); host.stepFrame(k === "." ? 1 : -1); return true; }
    if (!S.map || S.ov?.kind) return false; // the House / census layer has its own view
    if (e.target.closest?.(".map-pop")) return false; // a slider or box of a menu keeps its own keys
    const inMap = e.target === document.body || e.target === document.documentElement || box.contains(e.target);
    if (k === "+" || k === "=") { e.preventDefault(); S.map.zoomBy(1.5); return true; }
    if (k === "-" || k === "_") { e.preventDefault(); S.map.zoomBy(1 / 1.5); return true; }
    if (k === "0") { e.preventDefault(); S.map.resetView(); return true; }
    if (inMap && k.startsWith("Arrow")) {
      const r = box.getBoundingClientRect(), step = (e.shiftKey ? 0.5 : 0.2) * (k === "ArrowLeft" || k === "ArrowRight" ? r.width : r.height);
      e.preventDefault();
      S.map.panBy(k === "ArrowLeft" ? step : k === "ArrowRight" ? -step : 0, k === "ArrowUp" ? step : k === "ArrowDown" ? -step : 0);
      return true;
    }
    return false;
  }

  /* ------------------------------------------------------------ the address hash */

  // #z=<zoom>,<x>,<y>&s=<selected ids>&enc=hatch&eo=80&fo=100&m=<map colouring>&t=<time, UTC>
  let hashTimer = 0;
  function queueHash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(writeHash, 250);
  }
  function hashString() {
    const parts = [];
    const v = S.map?.viewState;
    if (v) parts.push(`z=${v.z.toFixed(3)},${v.cx.toFixed(1)},${v.cy.toFixed(1)}`);
    if (sel.size && sel.size <= HASH_IDS_MAX) parts.push(`s=${[...sel].map((i) => encodeURIComponent(S.map.ids[i])).join(",")}`);
    if (over.enc !== "none") parts.push(`enc=${over.enc}`, `eo=${over.encOp}`);
    if (over.fillOp !== 100) parts.push(`fo=${over.fillOp}`);
    if (S.modeChosen && S.mode) parts.push(`m=${S.mode}`);
    const ts = host.timeState();
    if (ts.moved && !ts.follow && Number.isFinite(ts.t)) parts.push(`t=${new Date(ts.t).toISOString().slice(0, 16)}Z`);
    return parts.length ? `#${parts.join("&")}` : "";
  }
  function writeHash() {
    hashTimer = 0;
    const h = hashString();
    if (h === location.hash || (!h && !location.hash)) return;
    const url = new URL(location.href);
    url.hash = h;
    history.replaceState(history.state, "", url);
  }
  const readHash = () => {
    const p = new URLSearchParams(location.hash.slice(1));
    return p;
  };
  const hashTime = () => { const t = Date.parse(readHash().get("t") ?? ""); return Number.isFinite(t) ? t : null; };

  // After the map has its units: the layers, the colouring, the selection and the view of a shared link.
  function applyHash() {
    const p = readHash();
    if (![...p.keys()].length) return;
    const enc = p.get("enc");
    if (enc && ENCODINGS.some(([v]) => v === enc) && !(enc === "outline" && !S.units?.turnout)) over.enc = enc; else if (p.has("enc") || p.has("eo") || p.has("fo")) over.enc = "none";
    const num = (k, d) => { const v = +p.get(k); return p.has(k) && Number.isFinite(v) ? Math.max(10, Math.min(100, Math.round(v))) : d; };
    over.encOp = num("eo", over.encOp);
    over.fillOp = num("fo", 100);
    S.map?.setOver({ enc: over.enc, encOp: over.encOp / 100, fillOp: over.fillOp / 100 });
    const m = p.get("m");
    if (m && host.modes().includes(m) && m !== S.mode) host.setMode(m);
    const s = p.get("s");
    if (s && S.map && S.featIdx) {
      const at = new Map(S.map.ids.map((id, i) => [id, i]));
      sel.clear();
      for (const id of s.split(",").map(decodeURIComponent)) { const i = at.get(id); if (i != null && S.featIdx[i] >= 0 && host.layerOn(S.featIdx[i])) sel.add(i); }
      selVersion++;
      syncMap();
      renderPanel();
    }
    const z = (p.get("z") ?? "").split(",").map(Number);
    if (z.length === 3 && z.every(Number.isFinite) && S.map) host.afterZoom(() => S.map.setViewState({ z: z[0], cx: z[1], cy: z[2] }));
    S.legendKey = null;
    host.render();
  }
  addEventListener("hashchange", () => { if (location.hash !== lastWritten()) applyHash(); });
  const lastWritten = () => hashString();

  return {
    key, clear, toggle, add, rebind, has: (i) => sel.has(i), get size() { return sel.size; },
    // every render(): the outlines and the panel; during playback the panel follows five times a second
    update() {
      syncMap();
      const now = performance.now();
      if (S.playing && now - lastPanel < 200) return;
      lastPanel = now;
      renderPanel();
    },
    sparkHTML, voteRows, bivLegend, encLegend, applyHash, hashTime, queueHash, over: () => over, closeMenus, openHelp,
    selected: () => [...sel],
  };
}
