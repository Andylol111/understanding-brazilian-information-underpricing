// Early vote against the model, race by race (data/ev2026/ev_races.json, built by analysis/us_ev_races.py).
// Two mounts, both optional: #evr (election.html: a collapsed 'Early vote' card for the selected race, with the live count
// next to the EV-implied and model figures) and #evr-table (turnout.html: the sortable races table). With no ev_races.json
// both stay hidden. Display only: unvalidated context, not used by any model. Never scrolls the page.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const MINUS = "\u2212";
const fix = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : x.toFixed(d));
const sgn = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : `${x < 0 ? MINUS : "+"}${Math.abs(x).toFixed(d)}`);
const rnd = (x) => (x == null ? "–" : String(Math.round(x)));
const cnt = (x) => (x == null ? "–" : x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1e4 ? `${Math.round(x / 1e3)}k` : Math.round(x).toLocaleString("en-US"));
const prob = (p) => (p == null ? "–" : `${Math.round(100 * p)} %`);
const FAILED = "this method failed the project's registered test (right on direction 61 %, made estimates worse)";

let dataP = null;
function load() {
  dataP ??= fetch(new URL("data/ev2026/ev_races.json", location.href), { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  return dataP;
}

function index(E) {
  const by = new Map();
  for (const r of E.races ?? []) { by.set(r.race_id, r); by.set(r.key, r); }
  for (const h of E.house ?? []) by.set(h.race_id, h);
  return by;
}

// ------------------------------------------------------------------------------------------------------------ card text
function summary(rec, E, live) {
  const parts = [];
  if (rec.office === "house") {
    const ev = rec.ev;
    parts.push(`${cnt(ev.banked)} banked${ev.share_of_2022_total != null ? ` (${rnd(100 * ev.share_of_2022_total)} % of the 2022 total)` : ""}`);
    if (ev.party) parts.push(`registered D ${rnd(ev.party.d_pct)} / R ${rnd(ev.party.r_pct)}`);
    return { parts, flag: false };
  }
  const st = E.states[rec.ev_state];
  if (!st) return { parts: ["no early-vote figure for this state"], flag: false };
  parts.push(`${cnt(st.banked)} banked${st.pct_expected != null ? ` (${rnd(st.pct_expected)} % of expected)` : ""}`);
  if (st.party) parts.push(`registered D ${rnd(st.party.d_pct)} / R ${rnd(st.party.r_pct)}`);
  if (st.shift) parts.push(`vs 2022: D ${sgn(st.shift.d_pt)}`);
  else if (st.pct_2022_final != null) parts.push(`vs 2022: ${rnd(st.pct_2022_final)} % of the final 2022 total`);
  const im = rec.implied;
  if (im?.ok) parts.push(`EV-implied D ${fix(im.implied)} vs model ${fix(rec.model.model_share)}${im.vs_model ? ` (${im.vs_model.text})` : ""}`);
  if (live) parts.push(`counted D ${fix(100 * live.raw)} at ${rnd(100 * live.counted)} % counted`);
  return { parts, flag: !!im?.ok };
}

const row = (label, a, b, c) => `<tr><th scope="row">${label}</th><td>${a}</td><td>${b}</td><td>${c}</td></tr>`;
const mixText = (m) => `D ${fix(m.d_pct)} · R ${fix(m.r_pct)} · other ${fix(m.o_pct)}`;

function earlyTable(rec, E) {
  const st = E.states[rec.ev_state];
  const rows = [];
  rows.push(row("Banked ballots", `${cnt(st.banked)}${st.pct_expected != null ? ` (${fix(st.pct_expected)} % of expected ${cnt(st.expected)})` : ""}`,
    st.total_2022 != null ? `${cnt(st.total_2022)} final total` : "–",
    st.same_point_2022 != null ? `${fix(st.same_point_2022, 2)}× the same point in 2022` : st.pct_2022_final != null ? `${fix(st.pct_2022_final)} % of the 2022 final total` : "–"));
  if (st.party) {
    const p = st.party, q = st.party_2022, s = st.shift;
    rows.push(row("Registered party mix", `${mixText(p)}<br><small>as of ${esc(p.as_of)}, ${cnt(p.n)} ballots with a party</small>`,
      q ? `${mixText(q)}<br><small>${esc(q.date)} (${q.prov === "official" ? "official files" : "secondary"})</small>` : "no same-stage figure",
      s ? `D ${sgn(s.d_pt)} · R ${sgn(s.r_pt)}` : "–"));
    rows.push(row("D share of D + R", fix(p.d_share_dr), q ? fix(q.d_share_dr) : "–", s ? `${sgn(s.d_share_dr_pt)} pts` : "–"));
  }
  return `<div class="table-scroll"><table class="compact evr-t"><thead><tr><th></th><th>2026</th><th>2022, same stage</th><th>Change</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function countyText(st) {
  const c = st?.composition;
  if (!c) return "";
  return `<h4>County pattern</h4><p>Each county voting as in 2024, weighted by the ballots banked there: D ${fix(c.share_so_far)} %, against D ${fix(c.share_expected)} % weighted by expected votes `
    + `(${sgn(c.diff_pt, 2)} pts, 95 % interval ${sgn(c.lo, 2)} to ${sgn(c.hi, 2)}, ${c.n} counties). <span class="note">Partial-day, a different mix of methods in each state, and the banked pool drifts toward R as Election Day nears: read it as so far, not as a lean.</span></p>`;
}

function modelText(rec) {
  const m = rec.model;
  const bits = [`Pre-count D ${fix(m.model_share)} %${m.model_p != null ? ` · P(D) ${prob(m.model_p)}` : ""}${m.model_basis ? ` <span class="note">(${esc(m.model_basis)})</span>` : ""}`];
  if (m.polls_avg != null) bits.push(`Poll average D ${fix(m.polls_avg)} %${m.polls_n != null ? ` (${m.polls_n} polls)` : ""}`);
  const mk = [m.pm != null ? `Polymarket ${prob(m.pm)}` : "", m.kalshi != null ? `Kalshi ${prob(m.kalshi)}` : ""].filter(Boolean);
  if (mk.length) bits.push(`Market P(D) ${mk.join(" · ")}`);
  return `<h4>Model view</h4><p>${bits.join("<br>")}</p>`;
}

function impliedText(rec, E) {
  const im = rec.implied;
  if (!im?.ok) return `<h4>EV-implied</h4><p class="note">No EV-implied figure: ${esc(im?.why ?? "not available")}.</p>${caveat(E)}`;
  return `<h4>EV-implied</h4><p>2022 certified D two-party ${fix(im.act22)} % (${esc(im.act22_office)}) − 2022 registered D share of D + R ${fix(im.ev22_d_share_dr)} = offset ${sgn(im.offset)}.<br>`
    + `2026 registered D share of D + R ${fix(im.ev26_d_share_dr)} + offset = <b>EV-implied D ${fix(im.implied)} %</b>, `
    + `${im.vs_model ? `<b>${esc(im.vs_model.text)}</b> against the model's ${fix(rec.model.model_share)} %` : "no model share to compare"}`
    + `${im.vs_polls ? ` (poll average: ${esc(im.vs_polls.text)})` : ""}.`
    + `${im.note ? `<br><span class="note">${esc(im.note)}</span>` : ""}</p>${caveat(E)}`;
}

function caveat(E) {
  return `<p class="evr-caveat"><b>Read with care.</b> ${esc(E.test.text.charAt(0).toUpperCase() + E.test.text.slice(1))} `
    + `An EV-implied figure exists for ${E.n_states_with_estimate} states only (${esc((E.states_with_estimate ?? []).join(", "))}): the others have no 2022 party figure at the same stage.</p>`;
}

function liveText(rec, E, live) {
  const st = E.states[rec.ev_state];
  const order = st?.order?.text ? `<p><b>Which ballots count first:</b> ${esc(st.order.text)}${st.order.config ? ` <span class="note">(${esc(st.order.config)})</span>` : ""}.</p>`
    : `<p class="note">Count order for this state: not recorded.</p>`;
  let body;
  if (rec.office === "house") body = `<p class="note">No live comparison for House districts here.</p>`;
  else if (!live) body = `<p class="note">${rec.modeled ? "No votes counted yet. Counted share, % counted and the comparison appear here once counting starts." : "No live county model for this race."}</p>`;
  else {
    const raw = 100 * live.raw, im = rec.implied?.ok ? rec.implied.implied : null, mo = rec.model.model_share;
    const r3 = (a, b, c) => `<tr><th scope="row">${a}</th><td>${b}</td><td>${c}</td></tr>`;
    body = `<table class="compact evr-t"><thead><tr><th></th><th>D %</th><th>Counted share minus it, pts</th></tr></thead><tbody>`
      + r3("Counted so far", `${fix(raw)}<br><small>${rnd(100 * live.counted)} % counted</small>`, "")
      + (im != null ? r3("EV-implied", fix(im), sgn(raw - im)) : "")
      + (mo != null ? r3("Model pre-count", fix(mo), sgn(raw - mo)) : "")
      + `</tbody></table><p class="note">Counted share is the raw two-party count so far. Which batches are in is not known here: compare it with the count order below.</p>`;
  }
  return `<h4>Live count</h4>${body}${order}`;
}

function bodyHTML(rec, E, live) {
  const head = `<p class="evr-label"><b>Registration-based. Unvalidated context, not used by any model.</b> Snapshot ${esc(E.generated_utc)}.</p>`;
  if (rec.office === "house") {
    const ev = rec.ev;
    return `${head}<p>${cnt(ev.banked)} ballots banked${ev.share_of_2022_total != null ? `, ${rnd(100 * ev.share_of_2022_total)} % of the 2022 total` : ""}${ev.approx ? " (county ballots summed to the district; split counties approximated)" : ""}`
      + `${ev.as_of ? `, as of ${esc(ev.as_of)}` : ""}.${ev.party ? `<br>Registered D ${fix(ev.party.d_pct)} · R ${fix(ev.party.r_pct)} · other ${fix(ev.party.o_pct)} %.` : ""}</p>`
      + modelText(rec) + `<h4>EV-implied</h4><p class="note">${esc(rec.implied.why)}.</p>${liveText(rec, E, live)}`;
  }
  const st = E.states[rec.ev_state];
  if (!st) return `${head}<p class="note">No early-vote figure for this state.</p>${modelText(rec)}${impliedText(rec, E)}${liveText(rec, E, live)}`;
  return `${head}<h4>Early vote</h4>${earlyTable(rec, E)}${countyText(st)}${modelText(rec)}${impliedText(rec, E)}${liveText(rec, E, live)}`;
}

// ------------------------------------------------------------------------------------------------------------ election page
function initElection() {
  const params = new URLSearchParams(location.search);
  const host = document.getElementById("evr");
  if (!host || params.get("id") !== "us2026") return;
  const liveBase = params.get("live") ? (params.get("live").endsWith("/") ? params.get("live") : `${params.get("live")}/`) : "data/";
  const sel = document.getElementById("race");
  const WIDE = matchMedia("(min-width: 1100px)");
  let E = null, by = null, key = null, open = false, rec = null, live = null, tail = null, tailAt = 0, html = "";

  const place = () => {
    const body = host.querySelector(".evr-body"), sum = host.querySelector(".evr-sum");
    if (!body || !sum) return;
    if (!open || !WIDE.matches) { body.style.cssText = ""; body.classList.remove("evr-pop"); return; }
    // wide screens: the details open as a panel over the right column, above the summary line, so no panel changes height
    const tabs = document.querySelector(".p-tabs")?.getBoundingClientRect(), s = sum.getBoundingClientRect();
    if (!tabs) return;
    body.classList.add("evr-pop");
    Object.assign(body.style, { left: `${tabs.left}px`, width: `${tabs.width}px`, bottom: `${innerHeight - s.top}px`, maxHeight: `${Math.max(180, s.top - 10)}px` });
  };

  const draw = () => {
    if (!E) return;
    if (!rec) { host.hidden = true; return; }
    const s = summary(rec, E, live);
    const next = `${rec.race_id}|${open}|${JSON.stringify(live)}|${s.parts.join("¦")}`;
    if (next === html) { place(); return; }
    html = next;
    host.hidden = false;
    const line = `Early vote: ${s.parts.map(esc).join(" · ")}`;
    host.innerHTML = `<button type="button" class="evr-sum" aria-expanded="${open}" aria-controls="evr-body" title="${esc(line)}">`
      + `<span class="evr-arrow" aria-hidden="true">${open ? "▾" : "▸"}</span><span class="evr-line">${esc(line)}</span>${s.flag ? `<span class="evr-flag"> · method failed its registered test</span>` : ""}</button>`
      + `<div class="evr-body" id="evr-body" ${open ? "" : "hidden"}>${open ? `<p class="evr-line-full"><b>${esc(line)}</b>${s.flag ? ` <span class="note">(${esc(FAILED)})</span>` : ""}</p>${bodyHTML(rec, E, live)}` : ""}</div>`;
    host.querySelector(".evr-sum").addEventListener("click", () => { open = !open; html = ""; draw(); });
    place();
  };

  const liveRow = (id) => {
    const rows = tail?.[id];
    if (!Array.isArray(rows)) return null;
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].counted > 0 && rows[i].raw != null) return { counted: rows[i].counted, raw: rows[i].raw, share: rows[i].share ?? null, t: rows[i].t };
    return null;
  };
  const pick = () => {
    const v = sel?.value ?? "";
    const next = v && by ? by.get(v) ?? null : null;
    if (next?.race_id === rec?.race_id && v === key) return;
    key = v; rec = next; live = rec ? liveRow(rec.key ?? rec.race_id) : null; html = "";
    draw();
    if (rec && Date.now() - tailAt > 20000) poll();
  };
  const poll = async () => {
    if (document.hidden || !rec) return;
    tailAt = Date.now(); // one fetch at a time per 20 s, kept even when it fails
    try {
      const r = await fetch(new URL(`${liveBase}us2026/timeline_tail.json`, location.href), { cache: "no-cache" });
      if (!r.ok) return;
      tail = await r.json();
      const nl = rec ? liveRow(rec.key ?? rec.race_id) : null;
      if (JSON.stringify(nl) !== JSON.stringify(live)) { live = nl; draw(); }
    } catch { /* offline: keep the last good live row */ }
  };

  load().then((d) => {
    if (!d?.races) return;
    E = d; by = index(E);
    pick();
    new MutationObserver(() => requestAnimationFrame(pick)).observe(document.getElementById("readout"), { childList: true });
    sel?.addEventListener("change", pick);
    addEventListener("resize", place);
    document.querySelector(".tab-panes")?.addEventListener("scroll", place, { passive: true });
    addEventListener("keydown", (e) => { if (e.key === "Escape" && open) { open = false; html = ""; draw(); host.querySelector(".evr-sum")?.focus({ preventScroll: true }); } });
    poll(); setInterval(poll, 60000);
    window.__evrReady = true;
  });
}

// ------------------------------------------------------------------------------------------------------------ turnout page
const COLS = [
  ["name", "Race", false, (r) => r.name],
  ["p", "Model P(D)", true, (r) => r.model.model_p],
  ["polls", "Polls, D %", true, (r) => r.model.polls_avg],
  ["mk", "Market P(D)", true, (r) => r.model.pm ?? r.model.kalshi],
  ["shift", "EV party shift vs 2022, D (pts)", true, (r, E) => E.states[r.ev_state]?.shift?.d_pt ?? null],
  ["imp", "EV-implied D % (model D %)", true, (r) => (r.implied.ok ? r.implied.implied : null)],
  ["diff", "Agreement", true, (r) => (r.implied.ok && r.implied.vs_model ? r.implied.vs_model.diff : null)],
];

function initTable() {
  const host = document.getElementById("evr-table");
  if (!host) return;
  load().then((E) => {
    if (!E?.races?.length) return;
    const st = { key: "name", dir: 1, only: false, sel: null };
    host.hidden = false;
    host.innerHTML = `<h3 id="evr-h">Races: early vote vs the model</h3>`
      + `<p class="td-label"><b>Registration-based. Unvalidated context, not used by any model.</b></p>`
      + `<p class="note">The EV-implied D share is the 2026 registered-party D share of D + R among banked ballots, moved by each state's 2022 offset (the 2022 certified D two-party share minus the 2022 registered D share of D + R at the same stage). `
      + `<b>${esc(FAILED.charAt(0).toUpperCase() + FAILED.slice(1))}.</b> It exists for ${E.n_states_with_estimate} states (${esc(E.states_with_estimate.join(", "))}); other races have no 2022 same-stage party figure. `
      + `'Agrees' means within ${E.agree_pt} points of the model's pre-count D share. Snapshot ${esc(E.generated_utc)}. Click a race to show its state above.</p>`
      + `<div class="toggle evr-filter" role="group" aria-label="Races shown"><button type="button" data-only="0" aria-pressed="true">All races</button><button type="button" data-only="1" aria-pressed="false">With an EV-implied value</button></div>`
      + `<div class="table-scroll" tabindex="0" role="region" aria-label="Races: early vote versus the model"><table class="compact races evr-races"><thead><tr></tr></thead><tbody></tbody></table></div>`;
    const head = host.querySelector("thead tr"), body = host.querySelector("tbody");
    const val = (c, r) => c[3](r, E);
    const cell = (c, r) => {
      const v = val(c, r);
      switch (c[0]) {
        case "name": return `<td><button type="button" class="evr-pick" data-st="${esc(r.state)}">${esc(r.name)}</button></td>`;
        case "p": return `<td>${prob(v)}</td>`;
        case "polls": return `<td>${v == null ? "–" : fix(v)}</td>`;
        case "mk": return `<td>${prob(v)}</td>`;
        case "shift": return `<td>${v == null ? '<span class="na">no 2022 figure</span>' : sgn(v)}</td>`;
        case "imp": return `<td>${r.implied.ok ? `${fix(r.implied.implied)} (${fix(r.model.model_share)})` : '<span class="na">–</span>'}</td>`;
        default: return `<td>${r.implied.ok && r.implied.vs_model ? esc(r.implied.vs_model.text) : '<span class="na">–</span>'}</td>`;
      }
    };
    const cmp = (a, b) => {
      const c = COLS.find((x) => x[0] === st.key), va = val(c, a), vb = val(c, b);
      if (va == null && vb == null) return a.name.localeCompare(b.name);
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * st.dir;
    };
    const pickEl = () => document.getElementById("tdd-pick");
    const syncSel = () => {
      st.sel = pickEl()?.value ?? null;
      for (const tr of body.children) {
        const on = tr.dataset.st === st.sel;
        tr.classList.toggle("sel", on);
        if (on) tr.setAttribute("aria-current", "true"); else tr.removeAttribute("aria-current");
      }
    };
    const draw = () => {
      head.innerHTML = COLS.map((c) => `<th scope="col" aria-sort="${st.key === c[0] ? (st.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-k="${c[0]}">${esc(c[1])}</button></th>`).join("");
      const rows = E.races.filter((r) => !st.only || r.implied.ok).sort(cmp);
      body.innerHTML = rows.map((r) => `<tr data-st="${esc(r.state)}">${COLS.map((c) => cell(c, r)).join("")}</tr>`).join("");
      syncSel();
    };
    head.addEventListener("click", (e) => {
      const k = e.target.closest("button")?.dataset.k;
      if (!k) return;
      st.dir = st.key === k ? -st.dir : (k === "name" ? 1 : -1);
      st.key = k; draw();
    });
    host.querySelector(".evr-filter").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      st.only = b.dataset.only === "1";
      for (const x of host.querySelectorAll(".evr-filter button")) x.setAttribute("aria-pressed", String(x === b));
      draw();
    });
    // a row selects the state's panel in place: the dashboard's own picker changes, the page does not scroll
    body.addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      const p = pickEl();
      if (!tr || !p || ![...p.options].some((o) => o.value === tr.dataset.st)) return;
      p.value = tr.dataset.st;
      p.dispatchEvent(new Event("change", { bubbles: true }));
      syncSel();
    });
    pickEl()?.addEventListener("change", syncSel);
    setInterval(syncSel, 1000); // the dashboard sets its picker in code (no event) when it opens on a state
    draw();
    window.__evrTableReady = true;
  });
}

initElection();
initTable();
