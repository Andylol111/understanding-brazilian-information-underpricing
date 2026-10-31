// The "Show:" bar: one toggle chip per layer that exists on the page (model, polls, Polymarket, Kalshi,
// raw count, each sensitivity, turnout, early vote). A layer that is off is left out everywhere it
// appears (chart line and label, readout chip, races-table column, replay marks).
//
// State: the set of HIDDEN layer keys (default: none, everything on).
//  - kept in localStorage under STORE, for the whole site (not per election);
//  - carried in the address as `show=<comma list of the layers that are ON>`, written only while
//    something is off (absent: all on). A shared link with `show=` reproduces the view: on load, every
//    layer the page knows that the list leaves out is hidden. `show=` (empty) hides every layer.
// The page tells the bar which layers exist (setLayers); the bar draws one chip per layer, and calls
// the subscribers when a chip or Reset changes the state.
import { param } from "./common.js";

const STORE = "ubiu.layers.hidden";
// layers that can exist on any page; sensitivities add their own keys
const FIXED = ["model", "polls", "polymarket", "kalshi", "raw", "turnout", "early"];

const hidden = new Set();
const subs = new Set();
let list = [];          // [{ key, label, title, color, dash }] the layers on the current race's page
let known = new Set(FIXED);
let applied = false;
let bar = null;

function save() {
  try { localStorage.setItem(STORE, JSON.stringify([...hidden])); } catch { /* storage off: the address still carries it */ }
}
function readStored() {
  try {
    const v = JSON.parse(localStorage.getItem(STORE) ?? "[]");
    return Array.isArray(v) ? v.filter((k) => typeof k === "string") : [];
  } catch { return []; }
}
// the address: show=<layers on> (only while something is off)
function syncUrl() {
  const url = new URL(location.href);
  const off = [...known].filter((k) => hidden.has(k));
  if (off.length) url.searchParams.set("show", [...known].filter((k) => !hidden.has(k)).join(","));
  else url.searchParams.delete("show");
  if (url.href !== location.href) history.replaceState(history.state, "", url);
}

// The first call: the address wins over what is stored.
function apply() {
  applied = true;
  for (const k of readStored()) hidden.add(k);
  const show = param("show");
  if (show != null) {
    const on = new Set(show.split(",").map((s) => s.trim()).filter(Boolean));
    hidden.clear();
    for (const k of known) if (!on.has(k)) hidden.add(k);
    save();
  }
}

export const layerOn = (key) => !hidden.has(key);

function change() {
  save();
  syncUrl();
  draw();
  for (const f of subs) f();
}

function draw() {
  if (!bar) return;
  const focus = bar.contains(document.activeElement) ? document.activeElement.dataset.layer ?? (document.activeElement.classList.contains("lb-reset") ? "#reset" : null) : null;
  const anyOff = list.some((l) => hidden.has(l.key));
  bar.innerHTML = `<span class="lb-label" id="lb-label">Show:</span>${list.map((l) => `<button type="button" class="lb-chip" data-layer="${l.key}" aria-pressed="${!hidden.has(l.key)}"${l.title ? ` title="${l.title.replace(/"/g, "&quot;")}"` : ""}><i class="lb-dot${l.dash ? " dashed" : ""}" style="--c:${l.color ?? "transparent"}" aria-hidden="true"></i>${l.label}</button>`).join("")}<button type="button" class="lb-reset" ${anyOff ? "" : "hidden"}>Reset</button>`;
  bar.hidden = list.length === 0;
  if (focus) bar.querySelector(focus === "#reset" ? ".lb-reset:not([hidden])" : `[data-layer="${focus}"]`)?.focus();
}

// Where the bar lives (the element is filled by setLayers).
export function mountLayers(el) {
  bar = el;
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-labelledby", "lb-label");
  bar.title = "Layer choices are saved on this site. In a shared link, show= lists the layers that are on.";
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.classList.contains("lb-reset")) hidden.clear();
    else if (hidden.has(b.dataset.layer)) hidden.delete(b.dataset.layer);
    else hidden.add(b.dataset.layer);
    change();
  });
}

// The layers that exist now: [{ key, label, title, color, dash }]. Redraws only when the set changes.
export function setLayers(next) {
  for (const l of next) known.add(l.key);
  if (!applied) apply();
  const sig = next.map((l) => l.key).join("|");
  if (sig === list.map((l) => l.key).join("|") && bar?.childElementCount) return;
  list = next;
  draw();
  syncUrl();
}

export function onLayersChange(f) { subs.add(f); }

// for tests: set a layer from code the way the chip does
export function toggleLayer(key, on) {
  if (on) hidden.delete(key); else hidden.add(key);
  change();
}
