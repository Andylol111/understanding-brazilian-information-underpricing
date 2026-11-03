// Election Day turnout section of the early-vote page: reads data/ev2026/turnout_day.json (+ turnout_volume.json if present), both
// written by analysis/us_turnout_publish.py. Turnout only: who voted, not how. Not used by any model. Every number shown comes
// from the file; this module only formats it. The file is refreshed in place, so it is re-read every 5 minutes.
import { esc } from "./common.js";

const $ = (s) => document.querySelector(s);
const KINDS = [
  ["election_day", "Election Day in person"], ["early_in_person", "Early in person"], ["mail_returned", "Mail/absentee returned"],
  ["advance", "All pre-Election Day (advance)"], ["total", "Total, all methods (early + mail + Election Day)"], ["requests", "Requests/sent"], ["unclear", "Type unclear"],
];
const KIND_LABEL = Object.fromEntries(KINDS);
const PROV = { official: "official file", official_post: "official, via post", derived: "official, summed", secondary: "secondary (news / compiler)", manual: "manual download" };
const VERDICT = { blocked: "blocked", excluded: "excluded", none: "none found", other: "partial" };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STALE_MS = 24 * 3600e3;
const REFRESH_MS = 5 * 60e3;
const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
const pc = (v) => (v == null ? "–" : `${v.toFixed(1)}%`);
const day = (iso) => `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}`;
const utc = (iso) => (iso ? `${day(iso.slice(0, 10))}, ${iso.slice(11, 16)} UTC` : "–");

const T = { data: null, st: "", kind: "all", cache: "" };

// as-of of a row: the instant (or the end of its date) and whether it is more than 24 h before the file was generated
function asOf(r, nowMs) {
  const ref = r.as_of_utc ? Date.parse(r.as_of_utc) : r.as_of_date ? Date.parse(`${r.as_of_date}T23:59:59Z`) : null;
  const stale = ref != null && nowMs - ref > STALE_MS;
  const text = r.as_of_utc ? utc(r.as_of_utc) : r.as_of_date ? `${day(r.as_of_date)} (${r.as_of_date.slice(0, 4)}), no clock time` : "as-of unclear";
  return { stale, text, short: r.as_of_date ? day(r.as_of_date) : "unclear" };
}
function compare(r) {
  const bits = [];
  if (r.same_time_2024 != null) bits.push(`2024 same point: ${int(r.same_time_2024)}`);
  if (r.same_time_2022 != null) bits.push(`2022 same point: ${int(r.same_time_2022)}${r.same_time_2022_ratio != null ? ` (now ${r.same_time_2022_ratio.toFixed(2)}×)` : ""}`);
  if (bits.length) return `<span>${bits.join("<br>")}</span>`;
  const share = [];
  if (r.pct_of_2022_total != null) share.push(`${pc(r.pct_of_2022_total)} of the 2022 total`);
  if (r.pct_of_2024_total != null) share.push(`${pc(r.pct_of_2024_total)} of the 2024 total`);
  const why = r.same_time_comparison ? `<span class="ev-detail">same point: ${esc(r.same_time_comparison)}</span>` : "";
  return (share.length ? `<span>${share.join("<br>")}</span>` : "–") + why;
}
const badge = (r) => `<span class="td-badge td-prov-${esc(r.prov)}" title="${esc(r.provenance ?? "")}">${esc(PROV[r.prov] ?? r.prov)}</span>`;
const link = (r) => (/^https?:\/\//.test(r.source_url ?? "") ? `<a href="${esc(r.source_url)}" rel="noopener">source</a>` : "–");

function rowHtml(r, nowMs, withState) {
  const a = asOf(r, nowMs);
  const cnt = r.count != null ? int(r.count) : "–";
  const reg = r.pct_registered != null ? `<span class="ev-detail">${pc(r.pct_registered)} of registered${r.registered ? ` (${int(r.registered)})` : ""}</span>` : "";
  const prev = r.prev_count != null && r.count != null && r.prev_as_of ? `<span class="ev-detail">+${int(r.count - r.prev_count)} since ${esc(utc(String(r.prev_as_of).replace(/\.\d+/, "").replace("+00:00", "Z")))}</span>` : "";
  const place = `${withState ? `${esc(r.state)} · ` : ""}${esc(r.jurisdiction)}`;
  return `<tr class="${a.stale ? "td-stale" : ""}"><td>${place}<span class="ev-detail">${esc(r.measure)}${r.note ? ` — ${esc(r.note)}` : ""}</span></td>` +
    `<td class="n td-count">${cnt}${reg}${prev}</td><td class="n td-asof">${a.stale ? `<b>stale: as of ${esc(a.short)}</b><span class="ev-detail">${esc(a.text)}</span>` : esc(a.text)}</td>` +
    `<td class="td-cmp">${compare(r)}</td><td>${badge(r)}</td><td>${link(r)}</td></tr>`;
}
const HEAD = `<thead><tr><th>Place and measure</th><th class="n">Count</th><th class="n">As of</th><th>Against 2022 / 2024</th><th>Provenance</th><th>Source</th></tr></thead>`;

// rows grouped by kind (never one number across kinds); each group carries its own badge
function groups(rows, nowMs, withState) {
  const sel = T.kind;
  return KINDS.filter(([k]) => (sel === "all" || sel === k) && rows.some((r) => r.kind === k)).map(([k, label]) => {
    const rs = rows.filter((r) => r.kind === k).sort((a, b) => (b.count ?? -1) - (a.count ?? -1));
    return `<tbody data-kind="${k}"><tr class="td-group"><th colspan="6"><span class="td-kind td-k-${k}">${esc(label)}</span> <span class="ev-detail" style="display:inline">${rs.length} row${rs.length === 1 ? "" : "s"}${k === "unclear" ? ": the measure text does not say which kind of count this is" : ""}</span></th></tr>${rs.map((r) => rowHtml(r, nowMs, withState)).join("")}</tbody>`;
  }).join("");
}

function render() {
  const d = T.data, nowMs = Date.parse(d.generated_utc) || Date.now();
  $("#td-asof").innerHTML = `Data as of <b>${esc(utc(d.generated_utc))}</b> (checked ${esc(utc(d.published_utc))}); this section re-reads its file every 5 minutes. Rows whose own as-of is more than 24 hours before that are greyed and marked stale.`;
  const states = d.states, pick = $("#td-pick");
  if (!pick.options.length) {
    pick.innerHTML = `<option value="">All states and DC (${states.length})</option>` + states.map((s) => `<option value="${s.st}">${esc(s.name)}${s.rows.length || s.counties.length ? "" : ` (${VERDICT[s.verdict_class]})`}</option>`).join("");
    pick.onchange = () => { T.st = pick.value; render(); };
  }
  pick.value = T.st;
  const used = new Set(states.flatMap((s) => [...s.rows, ...s.counties]).map((r) => r.kind));
  const f = $("#td-kinds");
  f.replaceChildren(...[["all", "All kinds"], ...KINDS.filter(([k]) => used.has(k))].map(([k, t]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.setAttribute("aria-pressed", String(T.kind === k));
    b.onclick = () => { T.kind = k; render(); }; return b;
  }));
  const one = states.find((s) => s.st === T.st);
  const shown = one ? [one] : states;
  const stateRows = shown.flatMap((s) => s.rows);
  $("#td-table").innerHTML = stateRows.length ? HEAD + groups(stateRows, nowMs, !one) : `<tbody><tr><td>No statewide count for ${one ? esc(one.name) : "this selection"}.</td></tr></tbody>`;
  const withRows = states.filter((s) => s.rows.length).length;
  $("#td-sum").textContent = `${withRows} of ${states.length} states and DC have a statewide row; ${states.filter((s) => s.counties.length).length} have county rows; ${d.n_rows} rows in all. Counts of different kinds are listed under their own heading and are never added together.`;
  // states with no statewide row: the verdict and why, so every state appears
  const none = shown.filter((s) => !s.rows.length);
  $("#td-none").innerHTML = none.length ? `<thead><tr><th>State</th><th>Verdict</th><th>What we found</th><th>County rows</th></tr></thead><tbody>${none.map((s) => {
    const blocked = s.unrecorded.map((u) => `${esc(u.jurisdiction)}: ${esc(u.verdict)}${u.reason ? ` (${esc(u.reason)})` : ""}`).join("; ");
    return `<tr><td>${esc(s.name)}</td><td><span class="td-badge td-v-${s.verdict_class}">${esc(VERDICT[s.verdict_class])}</span><span class="ev-detail">${esc(s.verdict)}</span></td>` +
      `<td>${esc(s.statewide ?? "–")}${blocked ? `<span class="ev-detail">${blocked}</span>` : ""}</td><td>${s.counties.length ? (one ? `${s.counties.length} below` : `<button type="button" class="ev-name" data-st="${s.st}">${s.counties.length} counties</button>`) : "–"}</td></tr>`;
  }).join("")}</tbody>` : "";
  $("#td-none-h").hidden = !none.length;
  $("#td-none").querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => { T.st = b.dataset.st; render(); }));
  // one state: its notes and the county drill-down
  const cty = $("#td-cty-wrap");
  cty.hidden = !one;
  if (one) {
    $("#td-state-note").innerHTML = `<span class="td-badge td-v-${one.verdict_class}">${esc(one.rows.length || one.counties.length ? "has rows" : VERDICT[one.verdict_class])}</span> ${esc(one.verdict)}. Statewide: ${esc(one.statewide ?? "–")}. Counties: ${esc(one.county_note ?? "–")}. Same-point history: ${esc(one.same_time_note ?? "–")}.` +
      (one.unrecorded.length && one.rows.length ? ` Not recorded: ${one.unrecorded.map((u) => `${esc(u.jurisdiction)} (${esc(u.verdict)})`).join(", ")}.` : "");
    $("#td-cty-h").textContent = one.counties.length ? `${one.name}: ${one.counties.length} county rows` : `${one.name}: no county rows`;
    $("#td-cty").innerHTML = one.counties.length ? HEAD + groups(one.counties, nowMs, false) : "";
  }
  window.__tdReady = true;
}

async function loadHistory() {
  const el = $("#td-hist");
  try {
    const r = await fetch(new URL("data/ev2026/turnout_volume.json", location.href), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const v = await r.json();
    el.innerHTML = `<b>Does Election Day volume tell us anything?</b> ${esc(v.verdict)}${v.summary ? ` ${esc(v.summary)}` : ""}${v.n_cases != null ? ` (${esc(String(v.n_cases))} past cases)` : ""}`;
  } catch {
    el.textContent = "The test of whether turnout volume says anything about the result has not been published.";
  }
}

async function load() {
  const r = await fetch(new URL("data/ev2026/turnout_day.json", location.href), { cache: "no-store" });
  if (!r.ok) throw new Error(`turnout_day.json: HTTP ${r.status}`);
  const d = await r.json();
  if (d.generated_utc === T.cache) return;
  T.cache = d.generated_utc; T.data = d;
  render();
}

export async function initTurnout() {
  loadHistory();
  try {
    await load();
  } catch (e) {
    $("#td-asof").textContent = "The turnout file is not published yet.";
    window.__tdReady = true;
    console.warn(e);
  }
  setInterval(() => load().catch(() => {}), REFRESH_MS);
}
