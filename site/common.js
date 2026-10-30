// Shared helpers for the landing page and the election page. Pages read only the dashboard
// contract files (elections.json, <eid>/meta|timeline|units|stats|arb.json).

// market = Polymarket; kalshi = Kalshi, the second market where it lists the same race
export const C = { market: "#1b1b1b", kalshi: "#2f8a6c", model: "#c4501b", raw: "#6a7f99", muted: "#5f6368", rule: "#e4e2dc", none: "#efede7" };

const params = new URLSearchParams(location.search);
export const param = (k) => params.get(k);

// Static: site/data/. Live: ?live=<base url> serves the same layout (geometry always comes from the
// static site, it does not change during a night).
const live = params.get("live");
const RELOAD = params.has("_r"); // a Reload after a failed load: revalidate every file
export const LIVE = live ? (live.endsWith("/") ? live : `${live}/`) : null;
export const DATA = LIVE ?? "data/";
export const STATIC = "data/";

// Parsed static files are kept for the page's lifetime (switching races re-reads nothing); live
// files are revalidated on every read ("no-cache": the browser sends If-None-Match and an unchanged
// file costs a 304). Optional files are null only when missing (404); any other failure throws, so
// a live refresh during an outage keeps the last good data instead of blanking it.
const parsed = new Map();
export function getJSON(path, { root = DATA, optional = false } = {}) {
  const url = new URL(path, new URL(root, location.href)).href;
  const live = LIVE && root === LIVE;
  if (!live && parsed.has(url)) return parsed.get(url);
  const p = fetch(url, { cache: live ? "no-cache" : RELOAD ? "reload" : "default" }).then((r) => {
    if (r.ok) return r.json();
    if (optional && r.status === 404) return null;
    throw new Error(`${path}: HTTP ${r.status}`);
  });
  if (!live) parsed.set(url, p.catch((e) => { parsed.delete(url); throw e; }));
  return live ? p : parsed.get(url);
}

export const absUrl = (path, root = DATA) => new URL(path, new URL(root, location.href)).href;

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(d)}%`);
export const num = (x, d = 2) => (x == null || Number.isNaN(x) ? "–" : x.toFixed(d));
export const usd = (x) => (x == null ? "–" : `$${Math.round(x).toLocaleString("en-US")}`);
export const pts = (x, d = 1) => (x == null ? "–" : `${x.toFixed(d)} pt`);
// a vote count in short form: "1.2M", "45k", "8.3k", "830"
export const votesShort = (v) => {
  if (v == null || Number.isNaN(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e6 ? `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M` : a >= 1e4 ? `${Math.round(v / 1e3)}k` : a >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : `${Math.round(v)}`;
};
// The model's chance in a timeline row: the version with polls (P_v2) where an election has one,
// else the count-only model (P). Rows with both also show P as "model without polls".
// A poll average built from this many polls or fewer is "thin": the US page labels it. n: the race's n_polls.
export const THIN_POLLS = 3;
export const thinPolls = (n) => (n >= 1 && n <= THIN_POLLS ? n : null);
export const thinLabel = (n) => `thin polling (${n} poll${n === 1 ? "" : "s"})`;
export const modelP = (row) => row?.P_v2 ?? row?.P ?? null;
// Cross-document view transitions (style.css @view-transition): where the browser has them, page
// links navigate at once and the map box and header carry over; elsewhere a short fade-out first.
export const crossDocTransitions = () => typeof window !== "undefined" && "CSSViewTransitionRule" in window;
// A race's short label (homepage panel, table and search), with the question's context where the
// first side's name alone would be ambiguous: "Alberta: most seats — Conservative", "Most seats:
// National", "Next PM: Poilievre", "Popular vote: Liberals by 0-3", "Seat margin: Liberals by 25-49";
// other long market questions read by their first side's name ("National 25–29").
// regionName: the race's region by name, where it has one.
export const shortName = (r, regionName = null) => {
  if (r.short) return r.short;
  const id = r.race_id ?? "", a = r.a?.name;
  if (a && /^(?:[a-z]{2}-)?most-/.test(id)) return r.region ? `${regionName ?? r.region.toUpperCase()}: most seats — ${a}` : `Most seats: ${a}`;
  if (a && /^pm-/.test(id)) return `Next PM: ${a}`;
  if (a && /^(?:vote)?margin-/.test(id)) return `Popular vote: ${a}`;
  if (a && /^seatmargin-/.test(id)) return `Seat margin: ${a}`;
  return r.name.length <= 40 ? r.name : a ?? r.name;
};
// a yes/no market's "no" side ("No", "Not Carney", "Another party", "Other margin"): not a party
export const genericNo = (side) => side?.id === "no" && /^(no\b|not |another |other )/i.test(side.name ?? "");
// A US margin (Democratic minus Republican, points of the two-party vote): "D+2.5", "R+8.3", "even".
export const marginText = (v) => (v == null || !Number.isFinite(v) ? "–" : Math.abs(v) < 0.05 ? "even" : `${v > 0 ? "D" : "R"}+${Math.abs(v).toFixed(1)}`);
// Inverse of the standard normal distribution (Acklam's approximation; |error| < 1.2e-9): the
// market-implied margin is probit(price) × the model's sd of the margin.
export function probit(p) {
  if (!(p > 0 && p < 1)) return p <= 0 ? -Infinity : p >= 1 ? Infinity : NaN;
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425;
  if (p < lo) { const q = Math.sqrt(-2 * Math.log(p)); return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
  if (p > 1 - lo) { const q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
  const q = p - 0.5, r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

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
  "model-only": { cls: "mo", text: "no market", long: "no market prices to compare with" },
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
