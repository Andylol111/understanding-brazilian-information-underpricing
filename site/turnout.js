// Election Day turnout summary on the early-vote page: reads data/ev2026/turnout_day.json (+ turnout_volume.json if present), both written by
// analysis/us_turnout_publish.py, and shows a compact summary that links to the turnout dashboard (turnout.html). Turnout only: who voted,
// not how. Not used by any model. Every number shown comes from the files; this module only formats it. Re-read every 5 minutes.
import { esc } from "./common.js";

const $ = (s) => document.querySelector(s);
const KINDS = [["election_day", "Election Day check-ins"], ["early_in_person", "early in person"], ["mail_returned", "mail / absentee returned"], ["advance", "all pre-Election Day"], ["total", "total, all methods"]];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REFRESH_MS = 5 * 60e3;
const utc = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}, ${iso.slice(11, 16)} UTC` : "–");
const T = { cache: "" };
// The files as last read, for the key-findings cards of the early-vote page; onData() runs after each read.
export const data = { day: null, vol: null };
let onData = () => {};

function render(d) {
  $("#td-asof").innerHTML = `Data as of <b>${esc(utc(d.generated_utc))}</b> (checked ${esc(utc(d.published_utc))}); re-read every 5 minutes.`;
  const withRows = d.states.filter((s) => s.rows.length).length, withCounty = d.states.filter((s) => s.counties.length).length;
  const per = KINDS.map(([k, t]) => [t, d.states.filter((s) => [...s.rows, ...s.counties].some((r) => r.kind === k)).length]).filter(([, n]) => n);
  $("#td-sum").textContent = `${withRows} of ${d.states.length} states and DC have a statewide row; ${withCounty} have county rows; ${d.n_rows} rows in all. States with a count, by kind: ${per.map(([t, n]) => `${t} ${n}`).join(", ")}. Counts of different kinds are never added together.`;
  data.day = d;
  window.__tdReady = true;
  onData();
}

async function loadHistory() {
  const el = $("#td-hist");
  try {
    const r = await fetch(new URL("data/ev2026/turnout_volume.json", location.href), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const v = await r.json();
    data.vol = v;
    onData();
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
  T.cache = d.generated_utc;
  render(d);
}

export async function initTurnout(cb) {
  if (cb) onData = cb;
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
