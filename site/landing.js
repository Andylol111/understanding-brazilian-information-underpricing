import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import { C, LIVE, getJSON, esc, pct, fmtDate, evidenceLabel, EVIDENCE, STATUS, plural, gatedSettle, leadMinutes } from "./common.js";

const tbody = document.querySelector("#overview tbody");
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countryName = (c) => { try { return c ? regionNames.of(c) : "Other"; } catch { return c; } };
const href = (id) => `election.html?id=${encodeURIComponent(id)}${LIVE ? `&live=${encodeURIComponent(LIVE)}` : ""}`;
const lazy = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    lazy.unobserve(e.target);
    spark(e.target);
  }
}, { rootMargin: "200px" });

function render(elections) {
  // countries ordered by their most recent election; elections newest first
  const groups = d3.groups(elections, (e) => e.country ?? "");
  const latest = (list) => d3.max(list, (e) => e.date);
  groups.sort((a, b) => latest(b[1]).localeCompare(latest(a[1])));
  tbody.innerHTML = groups.map(([c, list]) => `<tr class="country"><td colspan="8">${esc(countryName(c))}</td></tr>${
    [...list].sort((a, b) => b.date.localeCompare(a.date)).map((e) => {
      const ev = EVIDENCE[e.evidence];
      return `<tr data-id="${esc(e.id)}">
        <td class="e-name"><a href="${href(e.id)}">${esc(e.name)}</a><span class="e-sum">${esc(e.summary ?? "")}</span></td>
        <td>${esc(fmtDate(e.date))}</td>
        <td><span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span></td>
        <td>${ev ? `<span class="tip" tabindex="0" data-tip="${esc(ev.long[0].toUpperCase() + ev.long.slice(1))}.">${evidenceLabel(e.evidence)}</span>` : esc(e.evidence ?? "")}</td>
        <td class="n">${e.n_units != null ? `${e.n_units.toLocaleString("en-US")} ` : ""}<span class="note">${esc(plural(e.unit_level, e.n_units))}</span></td>
        ${e.status === "scheduled"
          ? `<td class="sched" colspan="3">Runs live on election night</td>`
          : `<td class="n lead">–</td><td class="n cal">–</td><td class="sp" data-id="${esc(e.id)}"><svg class="spark" width="150" height="34" aria-hidden="true"></svg></td>`}
      </tr>`;
    }).join("")}`).join("");
  tbody.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", (ev) => { if (!ev.target.closest("a, .tip")) location.href = href(tr.dataset.id); });
    const sp = tr.querySelector(".sp");
    if (sp) lazy.observe(sp);
  });
}

const metaCache = new Map();
const meta = (id) => {
  if (!metaCache.has(id)) metaCache.set(id, getJSON(`${id}/meta.json`, { optional: true }));
  return metaCache.get(id);
};
const mainRace = (m) => m?.races?.find((r) => !r.region) ?? m?.races?.[0];

// Lead, calibration and the sparkline are filled in when the row scrolls into view.
async function spark(td) {
  const id = td.dataset.id;
  const tr = td.closest("tr");
  const m = await meta(id);
  const race = mainRace(m);
  if (!race) return;
  const [stats, timeline] = await Promise.all([getJSON(`${id}/stats.json`, { optional: true }), getJSON(`${id}/timeline.json`, { optional: true })]);
  const rows = (timeline?.[race.race_id] ?? []).map((d) => ({ ...d, tms: Date.parse(d.t) }));
  const gate = m.count_timeline === false ? null : rows.find((d) => d.counted >= 0.01);
  const lead = leadMinutes(gatedSettle(stats, race.race_id, gate?.t));
  if (lead != null) tr.querySelector(".lead").textContent = `${lead > 0 ? "+" : lead < 0 ? "−" : ""}${Math.abs(lead)}`;
  const cal = stats?.calibration?.[race.race_id];
  if (cal?.coverage90_1_95 != null) tr.querySelector(".cal").textContent = pct(cal.coverage90_1_95, 0);
  if (!rows.length) return;
  const vis = gate ? rows.filter((d) => d.tms >= gate.tms - 15 * 60000) : rows;
  const w = 150, h = 34;
  const x = d3.scaleLinear().domain(d3.extent(vis, (d) => d.tms)).range([1, w - 1]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - 2, 2]);
  const svg = d3.select(td).select("svg").attr("viewBox", `0 0 ${w} ${h}`);
  svg.append("line").attr("x1", 0).attr("x2", w).attr("y1", y(0.5)).attr("y2", y(0.5)).attr("stroke", "#e4e2dc");
  const line = (k) => d3.line().defined((d) => d[k] != null).x((d) => x(d.tms)).y((d) => y(d[k])).curve(d3.curveStepAfter);
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.market).attr("stroke-width", 1.2).attr("d", line("pm"));
  svg.append("path").datum(vis).attr("fill", "none").attr("stroke", C.model).attr("stroke-width", 1.5).attr("d", line("P"));
  td.title = `${race.name}: probability ${race.a.name} wins`;
}

async function load() {
  try {
    render(await getJSON("elections.json"));
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="note">Could not load the list of elections (${esc(err.message)}).</td></tr>`;
  }
}

if (LIVE) {
  const note = document.getElementById("live-note");
  note.hidden = false;
  note.textContent = `Live data from ${LIVE}, refreshed every 15 seconds.`;
  setInterval(load, 15000);
}
load();
