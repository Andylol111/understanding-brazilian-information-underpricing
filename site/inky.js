// Early signal from the first counted U.S. House districts (Indiana, Kentucky, then the nation), and the "early-signal adjusted"
// seat line under the hemicycles. DISPLAY ONLY, unregistered, not validated. Reads extra/us2026/inky.json (written each cycle by the
// added-source process, brunder.us_extra_inky) only on a live page; the file is asked for with the rule of the other added files:
// at most once every 30 s, and after a missing read (404 before the process runs) back off 2, 4, 8, 10 polls so no request storm.
// Shared by the night guide (guide.js) and the election page (election.js); everything here is pure (data in, markup out) except load().
import { LIVE, getJSON, esc } from "./common.js";
import { seatTally } from "./dash-bop.js";

export const INKY_PATH = "extra/us2026/inky.json";
export const INKY_MS = 30000;
export const WARN = "early signal, not validated; partial counts can mislead";
export const ADJ_NOTE = "Early-signal adjusted (unregistered, not validated): applies the swing seen in counted districts to races not yet counted.";
// Paused on election night 2026-11-03 (01:20 UTC): nearly every early-reporting state posts early / mail ballots first, so the
// counted-vs-expected swing measured the count order, not the vote (it moved the public Senate line from 51 to 55 D). The
// panel keeps showing the measured swing; the adjusted seat line is not drawn.
export const ADJ_PAUSED = true;
const ADJ_PAUSED_NOTE = "Early-signal seat adjustment paused: most states report early and mail ballots first, so the swing seen so far reflects the order of the count, not the vote.";

export const createInky = () => ({ data: null, miss: 0, skip: 0, at: 0, loaded: false });

// One read; true when the document changed. Never throws: a failed read keeps the last good document.
export async function loadInky(s) {
  if (!LIVE) return false;
  if (Date.now() - s.at < INKY_MS - 500) return false;
  if (s.skip > 0) { s.skip--; s.at = Date.now(); return false; }
  s.at = Date.now();
  let j = null;
  try { j = await getJSON(INKY_PATH, { optional: true }); } catch { return false; }
  s.loaded = true;
  if (!j) { s.miss++; s.skip = Math.min(10, 2 ** s.miss) - 1; return false; }
  s.miss = 0;
  const changed = JSON.stringify(j) !== JSON.stringify(s.data);
  s.data = j;
  return changed;
}

/* ---------------------------------------------------------------- the adjusted seat line */

const phi = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
function erf(x) { // Abramowitz-Stegun 7.1.26, |error| < 1.5e-7
  const s = Math.sign(x), a = Math.abs(x), t = 1 / (1 + 0.3275911 * a);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-a * a);
  return s * y;
}
function probit(p) { // Acklam's rational approximation
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const q = Math.min(1 - 1e-9, Math.max(1e-9, p));
  if (q < 0.02425) { const r = Math.sqrt(-2 * Math.log(q)); return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1); }
  if (q > 1 - 0.02425) { const r = Math.sqrt(-2 * Math.log(1 - q)); return -(((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1); }
  const r = q - 0.5, s = r * r;
  return (((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * r / (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1);
}

// The seats of projection `proj` (dash-bop.js seatProjection; each race seat may carry live: true when its count model has votes)
// with every seat not yet counted shifted by the file's probit shift dz (a race without an entry stays as it is). Returns the tally
// (d, r, expectedD, total) or null when the file has no applied adjustment. The shift is applied to whatever chance the page shows
// for the seat, so the line is consistent with the hemicycle above it.
export function adjustedTally(proj, inky, chamber) {
  const adj = inky?.adjusted;
  if (!proj || !adj?.applied) return null;
  const table = chamber === "house" ? adj.house : adj.senate;
  if (!table) return null;
  const seats = proj.seats.map((s) => {
    if (!s.up || s.live || s.noModel || !s.key) return s;
    const key = chamber === "house" ? String(s.key).replace(/^house:/i, "").toUpperCase() : s.key;
    const e = table[key];
    return e && e.dz ? { ...s, p: phi(probit(s.p) + e.dz) } : s;
  });
  return { ...seatTally(seats), majority: proj.majority };
}

export function adjustedHTML(proj, inky, chamber) {
  const t = adjustedTally(proj, inky, chamber);
  if (!t || ADJ_PAUSED) return "";
  const a = inky.adjusted;
  return `<span class="hemi-adj-t" title="${esc(`${ADJ_NOTE} Shift ${fmt(a.shift_pts)} pts = ${fmt(a.aggregate_swing_expected)} pts counted vs expected x weight ${a.weight} (${a.n} districts, ${a.basis === "national" ? "national" : "Indiana + Kentucky"}).`)}">Early-signal adjusted (unregistered, not validated): <span class="hd">D <b>${t.d}</b></span> · <span class="hr">R <b>${t.r}</b></span></span>`;
}

/* ---------------------------------------------------------------- the panel */

const MINUS = "\u2212";
export const fmt = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : `${x > 0 ? "+" : x < 0 ? MINUS : ""}${Math.abs(x).toFixed(d)}`);
const pc = (x, d = 1) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);
const votes = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const ratio = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const cls = (x) => (x == null ? "" : x > 0 ? " inky-d" : x < 0 ? " inky-r" : "");

function aggRow(label, a, extra = "") {
  if (!a || !a.n) return `<tr><th scope="row">${esc(label)}</th><td colspan="6" class="inky-wait">${esc(extra) || "no contested district with 10% reporting yet"}</td></tr>`;
  const se = a.se_expected == null ? "" : ` <span class="inky-se">±${a.se_expected.toFixed(1)}</span>`;
  return `<tr><th scope="row">${esc(label)}</th><td>${a.n}</td><td>${pc(a.reporting_share, 0)}</td><td class="${cls(a.swing_pres).trim()}">${fmt(a.swing_pres)}</td>
    <td class="inky-key${cls(a.swing_expected)}">${fmt(a.swing_expected)}${se}</td><td>${fmt(a.poll_implied)}</td><td>${ratio(a.turnout_ratio)}</td></tr>`;
}

function districtRow(r) {
  const flag = r.uncontested ? `<span class="inky-flag" title="no Democrat–Republican pair on the ballot">uncontested</span>` : r.status === "no votes yet" ? `<span class="inky-wait">no votes yet</span>` : "";
  const basis = r.expected_basis && r.expected_basis !== "district baseline" ? ` title="expected: ${esc(r.expected_basis)}"` : "";
  return `<tr><th scope="row">${esc(r.id)}</th><td>${r.pct == null ? "–" : `${Math.round(r.pct)}%`}</td><td>${votes(r.counted)}</td><td>${pc(r.d2)}</td><td>${pc(r.pres24_d2)}</td><td>${pc(r.house24_d2)}</td>
    <td class="${cls(r.swing_pres).trim()}">${fmt(r.swing_pres)}</td><td class="${cls(r.swing_house24).trim()}">${fmt(r.swing_house24)}</td>
    <td class="inky-key${cls(r.swing_expected)}"${basis}>${fmt(r.swing_expected)}${basis ? "*" : ""}</td><td>${ratio(r.turnout_ratio)}</td><td>${flag}</td></tr>`;
}

const HEAD = `<thead><tr><th>District</th><th title="share of precincts reporting (civicAPI)">Reporting</th><th>Counted (D+R)</th><th>D share so far</th><th>2024 pres D</th><th>2024 House D</th>
  <th title="counted share minus the 2024 president's, share points. Composition-biased: the first precincts are not a random sample">Swing vs 2024 pres</th><th>Swing vs 2024 House</th>
  <th title="counted share minus the district's own pre-count expectation (2024 president + poll-implied swing)">Swing vs expected</th><th title="counted votes / expected votes at that reporting level">Turnout vs expected</th><th></th></tr></thead>`;

// The panel's markup for the file `d` (null: not written yet). Two states: waiting for the polls to close, and counting.
export function panelHTML(d) {
  const title = `<h2 class="inky-h">Early signal: Indiana &amp; Kentucky (then national) <span class="inky-tag">${esc(WARN)}</span></h2>`;
  const polls = d?.polls?.text ? `<p class="inky-polls">For comparison: ${esc(d.polls.text)}.</p>` : "";
  if (!d || d.status === "waiting") {
    return `${title}<p class="inky-waiting">Waiting for 6 pm ET (23:00 UTC): the first polls close in Indiana and Kentucky. This panel fills in as the first districts report.</p>${polls}
      <p class="inky-note">Unregistered display, not a forecast. Counts: civicAPI (civicapi.org).</p>`;
  }
  const A = d.aggregates ?? {}, nat = A.national ?? {};
  const natRow = nat.ready ? aggRow("Nation (all districts)", nat) : aggRow("Nation", null, nat.note ?? "too early for a national read");
  const rows = Object.values(d.districts ?? {});
  const focus = rows.filter((r) => r.st === "IN" || r.st === "KY"), rest = rows.filter((r) => r.st !== "IN" && r.st !== "KY");
  const missing = d.focus_missing?.length ? `<p class="inky-note">Not in the feed yet: ${esc(d.focus_missing.join(", "))}.</p>` : "";
  const adj = ADJ_PAUSED ? `<p class="inky-adj">${esc(ADJ_PAUSED_NOTE)}</p>` : d.adjusted?.applied
    ? `<p class="inky-adj"><b>Early-signal adjusted</b> (unregistered, not validated): shift ${fmt(d.adjusted.shift_pts)} pts on races not yet counted = ${fmt(d.adjusted.aggregate_swing_expected)} pts counted vs expected × weight ${d.adjusted.weight} (${d.adjusted.n} districts, ${d.adjusted.basis === "national" ? "national" : "Indiana + Kentucky"}). Pre-count seats (every race, no live chance) Senate D ${d.adjusted.seats.senate.pre.d} → ${d.adjusted.seats.senate.adj.d}, House D ${d.adjusted.seats.house.pre.d} → ${d.adjusted.seats.house.adj.d}; the hemicycles show the same shift on the races not yet counted.</p>`
    : `<p class="inky-adj">Early-signal adjustment: not applied yet (needs ${d.adjusted?.n ?? 0} of at least 5 contested districts with 10% reporting).</p>`;
  const snap = d.snapshot ? `Counts as of ${esc(d.snapshot.t.slice(11, 16))} UTC${d.snapshot.age_s > 900 ? ` <span class="inky-flag">snapshot ${Math.round(d.snapshot.age_s / 60)} min old</span>` : ""}.` : "";
  return `${title}
    <p class="inky-lead">Raw swings vs 2024 are <b>composition-biased</b> (which precincts report first, early and mail ballots first); <b>swing vs expected</b> compares each district with its own expectation (2024 president on the 2026 lines + the poll-implied swing; IN-05: the counties that have reported). Share points, + = more Democratic. ${snap}</p>
    <div class="table-scroll"><table class="inky-t inky-agg"><thead><tr><th></th><th>Districts</th><th title="share of the expected vote counted">Counted</th><th>Swing vs 2024 pres</th><th>Swing vs expected</th><th title="the swing the polls imply in the same districts">Poll-implied</th><th>Turnout vs expected</th></tr></thead><tbody>
      ${aggRow("Indiana", A.in)}${aggRow("Kentucky", A.ky)}${aggRow("Indiana + Kentucky", A.in_ky)}${natRow}</tbody></table></div>
    <p class="inky-note">Vote-weighted over contested districts with at least 10% reporting. ± = standard error across districts (3 or more). Counted % = expected votes counted; turnout = counted votes / expected at that reporting level.</p>
    ${polls}${adj}
    <div class="table-scroll"><table class="inky-t">${HEAD}<tbody>${focus.map(districtRow).join("")}</tbody></table></div>${missing}
    ${rest.length ? `<details class="inky-more"><summary>Other districts with at least 20% reporting (${rest.length})</summary><div class="table-scroll"><table class="inky-t">${HEAD}<tbody>${rest.map(districtRow).join("")}</tbody></table></div></details>` : ""}
    <p class="inky-note">Unregistered display, not a forecast and not part of any model's chance. Uncontested races carry no signal. Counts: civicAPI (civicapi.org).</p>`;
}
