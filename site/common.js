// Shared helpers for the landing page and the election page. Pages read only the dashboard
// contract files (elections.json, <eid>/meta|timeline|units|stats|arb.json).

export const C = { market: "#1b1b1b", model: "#c4501b", raw: "#6a7f99", muted: "#5f6368", rule: "#e4e2dc", none: "#efede7" };

const params = new URLSearchParams(location.search);
export const param = (k) => params.get(k);

// Static: site/data/. Live: ?live=<base url> serves the same layout (geometry always comes from the
// static site, it does not change during a night).
const live = params.get("live");
export const LIVE = live ? (live.endsWith("/") ? live : `${live}/`) : null;
export const DATA = LIVE ?? "data/";
export const STATIC = "data/";

export async function getJSON(path, { root = DATA, optional = false } = {}) {
  const r = await fetch(new URL(path, new URL(root, location.href)), { cache: LIVE && root === LIVE ? "no-store" : "default" });
  if (!r.ok) {
    if (optional) return null;
    throw new Error(`${path}: HTTP ${r.status}`);
  }
  return r.json();
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(d)}%`);
export const num = (x, d = 2) => (x == null || Number.isNaN(x) ? "–" : x.toFixed(d));
export const usd = (x) => (x == null ? "–" : `$${Math.round(x).toLocaleString("en-US")}`);
export const pts = (x, d = 1) => (x == null ? "–" : `${x.toFixed(d)} pt`);

export function fmtDate(iso) {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

// Clock time in the election's own time zone, e.g. "19:04".
export function clock(t, tz) {
  return new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz });
}

export function tzName(tz, at) {
  const p = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" }).formatToParts(new Date(at));
  return p.find((x) => x.type === "timeZoneName")?.value ?? tz;
}

// Minutes to add to UTC to get the election's wall clock at `at` (constant over one night).
export function tzOffsetMin(tz, at) {
  const d = new Date(at);
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    .formatToParts(d).map((x) => [x.type, x.value]));
  const wall = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  return Math.round((wall - Math.floor(d.getTime() / 60000) * 60000) / 60000);
}

export const EVIDENCE = {
  "in-sample": { cls: "in", text: "in-sample", long: "model settings were chosen on this data" },
  "pre-specified": { cls: "pre", text: "pre-specified", long: "model settings were frozen before this data was examined; the outcome was already known" },
  forward: { cls: "fwd", text: "forward", long: "model settings were frozen before the outcome existed" },
  "not-preregistered": { cls: "np", text: "not pre-registered", long: "settings were chosen on earlier elections and scored once, but were not frozen in the repository before scoring" },
  "model-only": { cls: "mo", text: "model only", long: "no market prices to compare with" },
  "market-only": { cls: "mo", text: "market only", long: "no count model was run: there is no official count over time to run it on. The page shows market prices over the night and the official final results" },
};

export function evidenceLabel(ev) {
  const e = EVIDENCE[ev];
  if (!e) return "";
  return `<span class="label ${e.cls}" title="${esc(e.long)}">${esc(e.text)}</span>`;
}

export const STATUS = { live: "Live", replay: "Replay", scheduled: "Scheduled" };

export const plural = (w, n) => (!w ? "" : n === 1 ? w : w.endsWith("y") ? `${w.slice(0, -1)}ies` : `${w}s`);

export const REPO = "https://github.com/Andylol111/understanding-brazilian-information-underpricing";

// Settle times used for leads, measured from the moment the race reached 1% counted: the
// exporter's stats.settle_gated when present, else stats.settle with each time moved up to the
// 1% gate (a source that was already at 0.9 before the gate counts as settled at the gate).
export function gatedSettle(stats, raceId, gateIso) {
  const g = stats?.settle_gated?.[raceId];
  if (g) return { ...g, gated: true };
  const s = stats?.settle?.[raceId];
  if (!s) return null;
  if (!gateIso) return { ...s, gated: false };
  const gate = Date.parse(gateIso);
  const at = (iso) => (iso ? new Date(Math.max(Date.parse(iso), gate)).toISOString() : null);
  return { model: at(s.model), polymarket: at(s.polymarket), kalshi: at(s.kalshi), raw: at(s.raw), gated: true };
}

export const leadMinutes = (s) => (s?.model && s?.polymarket ? Math.round((Date.parse(s.polymarket) - Date.parse(s.model)) / 60000) : null);
