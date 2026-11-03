// Balance of power as a congress hemicycle: one dot per seat (Senate 100, House 435) in concentric semicircle rows, filled from
// the safest Democratic seat on the left to the safest Republican seat on the right by each seat's model chance, so the
// tossups sit at the top centre. Shared by the national view (election.js) and the night guide (guide.js). Everything here
// is pure (figures in, markup out); the pages decide where each seat's chance comes from. Model chances only: no market
// price enters the seat count or the picture.
import { esc } from "./common.js";

const SIDE = { D: "Democrats", R: "Republicans" };
const pctText = (x) => `${Math.round(100 * x)}%`;

// Confidence tier of a seat's chance p (of the Democratic side): party of the leader and 3 solid (≥ 90%), 2 medium (70–90%),
// 1 light (50–70%); a seat at exactly 50% has no leader.
export function tier(p) {
  if (p == null || !Number.isFinite(p) || p === 0.5) return { party: "T", conf: 1 };
  const c = Math.max(p, 1 - p);
  return { party: p > 0.5 ? "D" : "R", conf: c >= 0.9 ? 3 : c >= 0.7 ? 2 : 1 };
}

// Ordered seats of a chamber. notUp: seats not on the ballot this year { D, R } (the Democratic count includes independents
// who caucus with them): solid, at the two ends. races: [{ key, name, p }] the seats up, p the chance of the Democratic side
// (null: no figure; the seat counts as 0.5). The seats up run from the safest Democratic (p highest) to the safest Republican.
export function seatOrder(notUp, races) {
  const up = races.map((r) => ({ key: r.key ?? null, name: r.name, p: r.p == null || !Number.isFinite(r.p) ? 0.5 : Math.min(1, Math.max(0, r.p)), noModel: r.p == null, live: !!r.live, up: true }))
    .sort((a, b) => b.p - a.p || String(a.name).localeCompare(String(b.name)));
  const fixed = (n, p, name) => Array.from({ length: Math.max(0, n) }, () => ({ key: null, name, p, noModel: false, up: false }));
  return [...fixed(notUp?.D ?? 0, 1, "Democratic seat not up in 2026"), ...up, ...fixed(notUp?.R ?? 0, 0, "Republican seat not up in 2026")];
}

// The numbers over the chart: expected seats = the sum of the seats' chances (a seat not up counts 1 or 0), rounded; the other
// party has the rest. leads: the seats each side is ahead in (a seat at exactly 50% is neither).
export function seatTally(seats) {
  const total = seats.length;
  let sum = 0, dLeads = 0, rLeads = 0, tied = 0;
  for (const s of seats) { sum += s.p; const t = tier(s.p).party; if (t === "D") dLeads++; else if (t === "R") rLeads++; else tied++; }
  const d = Math.min(total, Math.max(0, Math.round(sum)));
  return { total, expectedD: sum, d, r: total - d, dLeads, rLeads, tied, majority: Math.floor(total / 2) + 1 };
}

// A chamber's projection: ordered seats, the tally, and counted (the mean share of the expected vote counted in the races with a
// live model, 0 to 1; null before any votes: the projection is then the one made before the count).
export function seatProjection({ chamber, notUp, races, majority = null, counted = null }) {
  const seats = seatOrder(notUp, races), tally = seatTally(seats);
  return { chamber, seats, ...tally, majority: majority ?? tally.majority, counted };
}

// Seat positions of a hemicycle of n dots in outer radius `outer`: rows of dots on concentric semicircles (inner radius
// innerFrac × outer), each row holding dots in proportion to its radius; the row count is the one giving the largest dots.
// Dots are ordered left to right by angle (outer row first where angles tie), the order the seats are filled in.
export function hemicycleLayout(n, { outer = 240, innerFrac = 0.4 } = {}) {
  let best = null;
  for (let rows = 2; rows <= 18; rows++) {
    const radii = Array.from({ length: rows }, (_, i) => outer * (innerFrac + (1 - innerFrac) * i / (rows - 1)));
    const sum = radii.reduce((a, b) => a + b, 0);
    const counts = radii.map((r) => Math.max(2, Math.round(n * r / sum)));
    counts[rows - 1] += n - counts.reduce((a, b) => a + b, 0);
    if (counts[rows - 1] < 2) continue;
    const gap = outer * (1 - innerFrac) / (rows - 1);
    const arc = Math.min(...radii.map((r, i) => Math.PI * r / (counts[i] - 1)));
    const dot = 0.42 * Math.min(gap, arc);
    if (!best || dot > best.dot) best = { rows, radii, counts, dot };
  }
  const pts = [];
  best.radii.forEach((r, i) => {
    const c = best.counts[i];
    for (let j = 0; j < c; j++) pts.push({ a: Math.PI * (1 - j / (c - 1)), r });
  });
  pts.sort((p, q) => q.a - p.a || q.r - p.r);
  const pad = 4, top = 18, cx = outer + best.dot + pad, cy = top + pad + outer + best.dot;
  return { dot: best.dot, rows: best.rows, cx, cy, w: 2 * cx, h: cy + best.dot + pad, outer, inner: outer * innerFrac,
    pts: pts.map((p) => ({ x: cx + p.r * Math.cos(p.a), y: cy - p.r * Math.sin(p.a) })) };
}

const layouts = new Map();
const layoutFor = (n) => { if (!layouts.has(n)) layouts.set(n, hemicycleLayout(n)); return layouts.get(n); };

// What a dot says on hover: the seat and its chance.
export function seatText(s) {
  if (!s.up) return `${s.name} · solid`;
  if (s.noModel) return `${s.name} · no model figure`;
  const t = tier(s.p);
  return t.party === "T" ? `${s.name} · even` : `${s.name} · ${SIDE[t.party]} ${pctText(Math.max(s.p, 1 - s.p))}`;
}

export function projectionLabel(proj) {
  if (proj.counted == null) return "Projection before votes";
  const c = proj.counted;
  return `Live projection · ${c > 0 && c < 0.01 ? "<1" : Math.round(100 * c)}% counted`;
}

// The headline over the chart (label, projected seats, seats each side leads, majority) and the chart itself, as two strings so
// a page can replace each only when it changed.
export function hemicycleParts(proj) {
  const L = layoutFor(proj.seats.length);
  const head = `<p class="hemi-label"${proj.counted != null ? ` title="Average share of the expected vote counted across the ${esc(proj.chamber)} races with a live model"` : ""}>${esc(projectionLabel(proj))}</p>
    <p class="hemi-head" title="Expected seats ${proj.expectedD.toFixed(1)} Democratic, ${(proj.total - proj.expectedD).toFixed(1)} Republican: the sum of each seat's model chance">
      <span class="hd">Democrats <b>${proj.d}</b></span> · <span class="hr">Republicans <b>${proj.r}</b></span></p>`;
  const dots = proj.seats.map((s, i) => {
    const t = tier(s.p), p = L.pts[i];
    return `<circle class="hs ${t.party.toLowerCase()}${t.conf}${s.up ? "" : " nu"}${s.key ? " go" : ""}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${L.dot.toFixed(2)}"${s.key ? ` data-k="${esc(s.key)}"` : ""} data-t="${esc(seatText(s))}"/>`;
  }).join("");
  const top = L.cy - L.outer - L.dot - 6;
  const svg = `<svg class="hemi-svg" viewBox="0 0 ${L.w.toFixed(0)} ${L.h.toFixed(0)}" role="img" aria-label="${esc(`${proj.chamber} projection: Democrats ${proj.d} seats, Republicans ${proj.r}, majority ${proj.majority}`)}">
    <line class="hemi-maj" x1="${L.cx}" x2="${L.cx}" y1="${top}" y2="${(L.cy - L.inner + L.dot + 3).toFixed(1)}"/>
    <text class="hemi-maj-t" x="${L.cx}" y="${top - 3}" text-anchor="middle">${proj.majority} for majority</text>${dots}</svg>`;
  return { head, svg, key: closestHTML(proj) };
}

// The seats up closest to even (by model chance), beside the chart: where control is being decided; a row opens its race.
export function closestSeats(proj, n = 8) {
  return proj.seats.filter((s) => s.up && !s.noModel).sort((a, b) => Math.abs(a.p - 0.5) - Math.abs(b.p - 0.5) || String(a.name).localeCompare(String(b.name))).slice(0, n);
}
function closestHTML(proj) {
  const rows = closestSeats(proj).map((s) => {
    const t = tier(s.p), c = Math.max(s.p, 1 - s.p);
    const who = t.party === "T" ? "even" : `${t.party} ${pctText(c)}`;
    return `<li${s.key ? ` class="go" data-k="${esc(s.key)}" tabindex="0" role="button"` : ""}><span class="hc-n">${esc(String(s.name).replace(/ \(House\)$/, ""))}</span><span class="hc-p ${t.party.toLowerCase()}">${who}</span><i class="hc-bar"><b style="width:${(100 * s.p).toFixed(1)}%"></b></i></li>`;
  }).join("");
  return rows ? `<p class="hemi-label">Closest seats</p><ol class="hemi-close">${rows}</ol>` : "";
}

export const hemicycleShell = (title) => `<h3 class="tab-h">${esc(title)}</h3><div class="hemi-top"></div><p class="hemi-sub hemi-adj" hidden></p><div class="hemi-body"><div><div class="hemi-chart"></div><p class="hemi-hover"></p></div><div class="hemi-keys"></div></div>`;

// Fill a shell (hemicycleShell) with a projection; each part is replaced only when its markup changed, so a hovered dot's readout
// survives a refresh that changes nothing. adjHTML: the secondary line under the headline (inky.js, early-signal adjusted), "" hides it.
export function renderHemicycle(el, proj, adjHTML = "") {
  const { head, svg, key } = hemicycleParts(proj);
  for (const [sel, html] of [[".hemi-top", head], [".hemi-chart", svg], [".hemi-keys", key], [".hemi-adj", adjHTML]]) {
    const box = el.querySelector(sel);
    if (box && box.__html !== html) { box.innerHTML = html; box.__html = html; if (sel === ".hemi-adj") box.hidden = !html; }
  }
}

// Hover readout and click for every hemicycle inside `root` (one delegated listener set): onPick(seat key) on a dot of a race.
export function bindHemicycle(root, onPick) {
  const readout = (e) => e.target.closest?.("circle.hs")?.closest(".hemi")?.querySelector(".hemi-hover");
  const hint = "";
  root.addEventListener("pointerover", (e) => { const c = e.target.closest?.("circle.hs"), r = readout(e); if (c && r) r.textContent = c.dataset.t; });
  root.addEventListener("pointerout", (e) => { const c = e.target.closest?.("circle.hs"), r = readout(e); if (c && r) r.textContent = hint; });
  root.addEventListener("click", (e) => { const c = e.target.closest?.("circle.hs[data-k], .hemi-close li[data-k]"); if (c) onPick?.(c.dataset.k); });
  root.addEventListener("keydown", (e) => { const c = e.target.closest?.(".hemi-close li[data-k]"); if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onPick?.(c.dataset.k); } });
}
