// US midterms context page: renders data/us2026_context/*.json (built by analysis/us_context.py).
const V = window.__V;
const get = (f) => fetch(`data/us2026_context/${f}?v=${V}`).then((r) => { if (!r.ok) throw new Error(`${f}: ${r.status}`); return r.json(); });
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const int = (x) => (x == null ? "–" : Math.round(x).toLocaleString("en-US"));
const f1 = (x) => (x == null ? "–" : x.toFixed(1));
const sg = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`);
const link = (u, t) => (u ? `<a href="${esc(u)}" rel="noopener">${esc(t)}</a>` : esc(t));
const bar = (v, max) => {
  if (v == null) return "";
  const w = Math.min(50, (Math.abs(v) / max) * 50);
  return `<div class="mt-bar" aria-hidden="true"><i class="${v >= 0 ? "up" : "dn"}" style="${v >= 0 ? `left:50%` : `right:50%`};width:${w}%"></i></div>`;
};

function shiftTable(rows, { unit, nowLabel, pastLabel, counts }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.share_change_pp ?? 0)));
  let h = `<div class="table-scroll"><table class="mt-tab"><thead><tr><th>Group</th><th class="n">${esc(pastLabel)}</th><th class="n">${esc(nowLabel)}</th><th class="n">Change (points)</th><th></th>${counts ? `<th class="n">${esc(counts)}</th>` : ""}</tr></thead><tbody>`;
  let dim = null;
  for (const r of rows) {
    if (r.dim !== dim) { dim = r.dim; h += `<tr class="dim"><td colspan="${counts ? 6 : 5}">${esc(r.dim_label)}</td></tr>`; }
    h += `<tr><td>${esc(r.group)}</td><td class="n">${f1(r.share_2022)}%</td><td class="n">${f1(r.share_now)}%</td><td class="n">${sg(r.share_change_pp)}</td><td>${bar(r.share_change_pp, max)}</td>${counts ? `<td class="n">${sg(r.count_change_pct, 0)}%</td>` : ""}</tr>`;
  }
  return h + "</tbody></table></div>";
}

function groupGrid(g) {
  const st = g.states;
  const src = (s) => (s.url ? link(s.url, s.name) : esc(s.name));
  $("eg-table").tHead.innerHTML = `<tr><th>Group (share of ballots)</th>${st.map((s) => `<th class="n">${src(s)}</th>`).join("")}</tr>`;
  const cell = (s, k) => {
    const c = s.groups[k];
    if (!c) return `<td class="n mt-na">–</td>`;
    const ch = c.change_pp == null ? "" : `<small class="${c.change_pp >= 0 ? "up" : "dn"}${s.baseline_2022 === "same day" ? "" : " mt-soft"}">${sg(c.change_pp)} pts</small>`;
    const rg = c.reg_share == null ? "" : `<small class="mt-reg">${f1(c.reg_share)}% of voters on the rolls</small>`;
    return `<td class="n"><b>${f1(c.share)}%</b>${ch}${rg}</td>`;
  };
  let h = "", dim = null;
  for (const r of g.rows) {
    if (!st.some((s) => s.groups[r.key])) continue;
    if (r.dim !== dim) { dim = r.dim; h += `<tr class="dim"><td colspan="${st.length + 1}">${esc(r.dim_label)}</td></tr>`; }
    h += `<tr><td>${esc(r.group)}</td>${st.map((s) => cell(s, r.key)).join("")}</tr>`;
  }
  const row = (label, f) => `<tr class="mt-meta"><td>${esc(label)}</td>${st.map((s) => `<td class="n">${f(s)}</td>`).join("")}</tr>`;
  h += `<tr class="dim"><td colspan="${st.length + 1}">About each state's numbers</td></tr>`;
  h += row("Ballots counted", (s) => int(s.total));
  h += row("Counted through", (s) => esc(s.as_of));
  for (const [k, lab] of [["race", "Race recorded for"], ["age", "Age recorded for"], ["gender", "Gender recorded for"]]) {
    h += row(lab, (s) => (s.coverage[k] == null ? "–" : `<span class="${s.coverage[k] < 90 ? "mt-small" : ""}">${f1(s.coverage[k])}%</span>`));
  }
  h += row("Change measured against", (s) => (s.baseline_label ? esc(s.baseline_label) : "no 2022 figure"));
  h += row("Source", (s) => (s.source.startsWith("UF") ? "UF Election Lab, used with permission" : esc(s.source)));
  $("eg-table").tBodies[0].innerHTML = h;
  const n = st.filter((s) => s.baseline_2022).length;
  $("eg-sub").textContent = `${st.length} states with a published breakdown by race, age or gender; ${n} have a 2022 comparison. Each cell is the group's share of the ballots counted so far; the small line is the change in points against 2022; where shown, "on the rolls" is the group's share of registered voters.`;
  $("eg-note").innerHTML = `Share = the group's share of the ballots that have that attribute recorded; the "recorded for" rows give how many of all ballots that is (red under 90%: the state publishes that attribute for only part of its ballots). Changes in grey compare with a 2022 final count, not the same day. States other than North Carolina, Colorado, South Carolina and Louisiana: ${link(g.states.find((s) => s.source.startsWith("UF"))?.url, "UF Election Lab (compiled from official state sources), used with permission")} of Dr. Michael McDonald. Colorado's four-band ages come from the same compilation; its own bands carry the 2022 comparison. Counts of ballots by recorded attribute, not how anyone voted.`;
}

function biggest(ev, mix) {
  const items = [];
  for (const r of ev?.rows ?? []) items.push({ v: r.share_change_pp, t: `${r.group}: share of the early vote`, s: `${f1(r.share_2022)}% → ${f1(r.share_now)}% (same day 2022 vs now)`, k: "ev" });
  for (const r of mix?.rows ?? []) items.push({ v: r.share_change_pp, t: `${r.group}: share of registered voters`, s: `${f1(r.share_2022)}% → ${f1(r.share_now)}% (Nov 2022 vs ${mix.asof_now})`, k: "rg" });
  items.sort((a, b) => Math.abs(b.v ?? 0) - Math.abs(a.v ?? 0));
  $("big-list").innerHTML = items.slice(0, 8).map((i) => `<li><b>${sg(i.v)} pt</b>${esc(i.t)}<small>${esc(i.s)}</small></li>`).join("");
  $("big-sub").textContent = "Largest changes in a group's share, in percentage points, North Carolina (the state whose per-voter early-vote file allows every group to be compared). Ranked by size, not by group.";
}

function spark(series, key) {
  const pts = series.filter((s) => s[key] != null && s.total);
  if (pts.length < 2) return "";
  const v = pts.map((s) => 100 * s[key] / s.total);
  const lo = Math.min(...v), hi = Math.max(...v), W = 110, H = 26;
  const xy = v.map((y, i) => `${(i / (v.length - 1) * W).toFixed(1)},${(H - 2 - (hi === lo ? 0.5 : (y - lo) / (hi - lo)) * (H - 4)).toFixed(1)}`).join(" ");
  return xy;
}

function regStates(reg) {
  const st = Object.entries(reg.states).map(([k, v]) => [k, v]).sort((a, b) => Math.abs(b[1].d_minus_r_change ?? 0) - Math.abs(a[1].d_minus_r_change ?? 0));
  $("rs-sub").textContent = `Official voter-registration reports. Change is from the first report on or after Oct 2022 to the latest report. "Other" = everyone not registered Democratic or Republican (unaffiliated and minor parties). ${st.length} states, sorted by the size of the change in Democratic minus Republican registrations.`;
  $("rs-table").tHead.innerHTML = `<tr><th>State</th><th>From → to</th><th class="n">Democratic</th><th class="n">Republican</th><th class="n">Other</th><th class="n">D minus R change</th><th>Share of rolls over time: D (green), R (orange), other (gray)</th></tr>`;
  const cell = (c) => (c ? `${sg(c.n, 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} (${sg(c.pct)}%)` : "–");
  $("rs-table").tBodies[0].innerHTML = st.map(([k, v]) => {
    const line = (key, col) => { const p = spark(v.series, key); return p ? `<polyline points="${p}" stroke="${col}"/>` : ""; };
    return `<tr><td>${link(v.url, k)}</td><td>${esc(v.base_date)} → ${esc(v.latest_date)}</td><td class="n">${cell(v.change.D)}</td><td class="n">${cell(v.change.R)}</td><td class="n">${cell(v.change.O)}</td><td class="n">${v.d_minus_r_change == null ? "–" : sg(v.d_minus_r_change, 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}</td><td><svg class="mt-spark" viewBox="0 0 110 26" role="img" aria-label="${esc(k)} registration shares over time">${line("D", "#2f8a6c")}${line("R", "#c4501b")}${line("O", "#8a8a8a")}</svg></td></tr>`;
  }).join("") || `<tr><td>No state reports loaded.</td></tr>`;
  const b = reg.blocked ?? [];
  $("rs-blocked").innerHTML = b.length ? "Not available: " + b.map((x) => `${esc(String(x.st).toUpperCase())} (${link(x.url, x.reason)})`).join("; ") + "." : "";
}

function issues(is) {
  const g = is.gallup ?? [];
  const dkey = (r) => r.date_label;
  const dates = [...new Set(g.map(dkey))].slice(-6);
  const cats = [...new Set(g.map((r) => r.category))];
  const val = (c, d) => g.find((r) => r.category === c && dkey(r) === d);
  const latest = dates[dates.length - 1];
  cats.sort((a, b) => (val(b, latest)?.pct ?? -1) - (val(a, latest)?.pct ?? -1));
  $("gal-table").tHead.innerHTML = `<tr><th>Problem named</th>${dates.map((d) => `<th class="n">${esc(d)}</th>`).join("")}</tr>`;
  const src = g.length ? link(g[g.length - 1].source_url, "Gallup") : "";
  $("gal-table").tBodies[0].innerHTML = g.length ? cats.map((c) => `<tr><td>${esc(c.replace(/_/g, " "))}</td>${dates.map((d) => `<td class="n">${val(c, d)?.pct != null ? val(c, d).pct + "%" : "–"}</td>`).join("")}</tr>`).join("") + `<tr><td colspan="${dates.length + 1}">Percent of US adults naming the problem (open-ended); ${src}. Interview counts: ${esc([...new Set(g.map((r) => r.n))].filter(Boolean).slice(-3).join(", "))}.</td></tr>` : `<tr><td>No Gallup data loaded.</td></tr>`;
  const by = new Map();
  for (const r of is.polls ?? []) { const k = [r.pollster, r.geography, r.field_end, r.question].join("|"); if (!by.has(k)) by.set(k, []); by.get(k).push(r); }
  $("iss-table").tHead.innerHTML = `<tr><th>Poll</th><th>Where</th><th>Field dates</th><th class="n">n</th><th>Who</th><th>Issues named (%)</th></tr>`;
  $("iss-table").tBodies[0].innerHTML = [...by.values()].sort((a, b) => String(b[0].field_end).localeCompare(String(a[0].field_end))).map((rs) => {
    const r = rs[0];
    const top = rs.slice().sort((a, b) => b.pct - a.pct).map((x) => `${esc(x.issue)} ${x.pct}`).join(", ");
    return `<tr><td>${link(r.source_url, r.pollster)}</td><td>${esc(r.geography)}</td><td>${esc(r.field_start)} – ${esc(r.field_end)}</td><td class="n">${int(r.n)}</td><td>${esc(r.population)}</td><td>${top} <small>(${esc(r.question)})</small></td></tr>`;
  }).join("") || `<tr><td>No poll records loaded.</td></tr>`;
}

function crosstabs(ct) {
  const rows = ct.rows ?? [];
  const races = [...new Set(rows.map((r) => r.race))].sort();
  $("px-race").innerHTML = races.map((r) => `<option>${esc(r)}</option>`).join("");
  const pollKey = (r) => `${r.pollster} (${r.field_start} – ${r.field_end})`;
  const draw = () => {
    const race = $("px-race").value, pk = $("px-poll").value;
    const rs = rows.filter((r) => r.race === race && pollKey(r) === pk);
    if (!rs.length) { $("px-table").tBodies[0].innerHTML = "<tr><td>No crosstabs loaded.</td></tr>"; return; }
    const p = rs[0];
    $("px-sub").innerHTML = `${link(p.source_url, p.pollster)}, ${esc(p.field_start)} – ${esc(p.field_end)}, ${int(p.sample_n)} ${esc(p.population)}; whole-poll margin of error about ±${f1(p.moe_poll)} points. ${esc(p.dem_name ?? "Democratic")} vs ${esc(p.rep_name ?? "Republican")}.`;
    $("px-table").tHead.innerHTML = `<tr><th>Group</th><th class="n">Interviews</th><th class="n">Margin of error (±pts)</th><th class="n">Democratic %</th><th class="n">Republican %</th><th class="n">D minus R</th></tr>`;
    let h = "", dim = null;
    for (const r of rs) {
      if (r.dim !== dim) { dim = r.dim; h += `<tr class="dim"><td colspan="6">${esc({ race: "Race / ethnicity", age: "Age", education: "Education" }[r.dim] ?? r.dim)}</td></tr>`; }
      const small = r.group_n_used != null && r.group_n_used < 100;
      h += `<tr><td>${esc(r.group)}</td><td class="n ${small ? "mt-small" : ""}">${r.group_n_used == null ? "–" : (r.group_n_est ? "~" : "") + int(r.group_n_used)}</td><td class="n">${r.moe_group == null ? "–" : f1(r.moe_group)}</td><td class="n">${f1(r.dem_pct)}</td><td class="n">${f1(r.rep_pct)}</td><td class="n">${r.margin == null ? "–" : sg(r.margin)}</td></tr>`;
    }
    $("px-table").tBodies[0].innerHTML = h;
  };
  const pickRace = () => {
    const ps = [...new Set(rows.filter((r) => r.race === $("px-race").value).map(pollKey))];
    $("px-poll").innerHTML = ps.map((p) => `<option>${esc(p)}</option>`).join("");
    draw();
  };
  $("px-race").onchange = pickRace;
  $("px-poll").onchange = draw;
  if (races.length) pickRace(); else $("px-table").tBodies[0].innerHTML = "<tr><td>No crosstabs loaded.</td></tr>";
}

async function main() {
  const [ev, mix, reg, is, ct, eg] = await Promise.all(["ev_nc.json", "reg_nc_mix.json", "registration.json", "issues.json", "crosstabs.json", "ev_groups.json"].map(get));
  if (ev) {
    $("mt-asof").textContent = `Early vote through ${ev.through_2026} (${ev.days_before_election_day} days before Election Day), compared with ${ev.through_2022} in 2022.`;
    $("ev-sub").textContent = `North Carolina: ${int(ev.total_now)} accepted ballots now vs ${int(ev.total_2022)} on the same day in 2022. Each group's share of all ballots cast so far.`;
    $("ev-tables").innerHTML = shiftTable(ev.rows, { nowLabel: "2026 share", pastLabel: "Same day 2022", counts: "Ballots vs 2022" });
  } else $("mt-asof").textContent = "Early-vote file not available.";
  if (mix) {
    $("rg-sub").textContent = `North Carolina: each group's share of registered voters, ${mix.asof_2022} vs ${mix.asof_now}.`;
    $("rg-tables").innerHTML = shiftTable(mix.rows, { nowLabel: "Now", pastLabel: "Nov 2022" });
  }
  if (eg) groupGrid(eg); else $("eg-table").tBodies[0].innerHTML = "<tr><td>Group table not available.</td></tr>";
  biggest(ev, mix);
  regStates(reg);
  issues(is);
  crosstabs(ct);
  window.__mtReady = true;
}
main().catch((e) => { document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="note">Could not load data: ${esc(e.message)}</p>`); });
