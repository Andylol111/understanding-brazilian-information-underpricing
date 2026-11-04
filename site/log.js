// Live log page: reads data/livelog/us2026/{manifest.json,incidents.json,<source>.jsonl} (written by scripts/live/livelog.py)
// and shows one race's logged rows, a P-versus-market step chart, the incidents touching it, and an in-browser chain check.
(() => {
  "use strict";
  const BASE = "data/livelog/us2026/";
  const SRC_LABEL = {
    "registered": "registered model", "registered-house": "registered House model", "added": "added-source model",
    "added-house": "added-source House model", "civic": "civicAPI model",
  };
  const STATES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming" };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bust = () => `?t=${Math.floor(Date.now() / 60000)}`;

  let manifest = null, incidents = [], races = [], cur = null, curRows = [], fileCache = {}, newestFirst = true;

  // ---- time helpers -------------------------------------------------------
  const ms = (t) => Date.parse(/T\d\d:\d\dZ$/.test(t) ? t.replace("Z", ":00Z") : t);
  const etFmt = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  const etShort = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const et = (t) => (t ? etFmt.format(new Date(ms(t))) : "");
  const utc = (t) => (t ? String(t).replace("T", " ").replace("Z", "") + " UTC" : "");
  const utcShort = (t) => String(t).slice(5, 16).replace("T", " ");
  const pct = (x, d = 1) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
  const cpct = (x) => (x == null ? "–" : (x * 100).toFixed(x > 0 && x < 0.1 ? 2 : 1) + "%");

  // ---- race naming / scope matching ---------------------------------------
  function parseKey(key, source) {
    let m = /^house:([A-Z]{2})-(\d+|AL)$/.exec(key);
    if (m) return { key, source, state: m[1], type: "house", label: `${m[1]}-${m[2]}`, long: `${STATES[m[1]] || m[1]} House district ${m[2]}` };
    m = /^([a-z]{2})-(sen|gov)(-civic)?$/.exec(key);
    if (m) {
      const st = m[1].toUpperCase();
      return { key, source, state: st, type: m[2], label: `${st} ${m[2] === "sen" ? "Senate" : "governor"}`, long: `${STATES[st] || st} ${m[2] === "sen" ? "Senate" : "governor"}` };
    }
    return { key, source, state: "", type: "other", label: key, long: key };
  }
  function touches(inc, r) {
    return (inc.scope || []).some((p) => {
      if (p === "*" || p === "all") return true;
      if (p === "national") return false;
      if (p.startsWith("src:")) return r.source === p.slice(4);
      if (/^[A-Z]{2}$/.test(p)) return r.state === p;
      if (p.endsWith("*")) return r.key.startsWith(p.slice(0, -1));
      return r.key === p;
    });
  }

  // ---- loading --------------------------------------------------------------
  function setStatus(state, text, note) {
    $("lg-status").dataset.state = state;
    $("lg-status-text").textContent = text;
    $("lg-status-note").textContent = note || "";
  }
  async function getJSON(name) {
    const r = await fetch(BASE + name + bust(), { cache: "no-store" });
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  }
  async function loadIndex() {
    try {
      [manifest, { incidents }] = await Promise.all([getJSON("manifest.json"), getJSON("incidents.json")]);
    } catch (e) {
      setStatus("err", "The log could not be loaded.", String(e.message || e));
      $("lg-all-inc").innerHTML = '<p class="cnt-muted">Could not load the log files.</p>';
      return false;
    }
    races = [];
    for (const f of manifest.files) for (const k of Object.keys(f.races)) races.push({ ...parseKey(k, f.source), n: f.races[k], file: f.file });
    races.sort((a, b) => (a.type === "house") - (b.type === "house") || a.label.localeCompare(b.label, "en", { numeric: true }) || a.source.localeCompare(b.source));
    const when = manifest.checked_at;
    setStatus("ok", `${manifest.total_rows.toLocaleString("en-US")} rows logged`, `Log last checked ${et(when)} ET (${utc(when)}). Reload the log for newer rows.`);
    renderAllIncidents();
    return true;
  }
  async function loadFile(f) {
    if (fileCache[f.file]) return fileCache[f.file];
    const r = await fetch(BASE + f.file + bust(), { cache: "no-store" });
    if (!r.ok) throw new Error(`${f.file}: HTTP ${r.status}`);
    const text = await r.text();
    const lines = text.split("\n").filter((l) => l.length);
    return (fileCache[f.file] = { lines, rows: null });
  }
  function parsedRows(entry) {
    if (!entry.rows) entry.rows = entry.lines.map((l) => JSON.parse(l));
    return entry.rows;
  }

  // ---- incidents ------------------------------------------------------------
  function incHTML(i) {
    const span = i.to ? `${et(i.from)} to ${et(i.to)} ET (${utcShort(i.from)} to ${utcShort(i.to)} UTC)` : `from ${et(i.from)} ET (${utcShort(i.from)} UTC), ongoing or not yet resolved`;
    const ap = i.approx ? " (start time approximate)" : "";
    const cor = i.correction ? `<p><b>Correction (${esc(et(i.corrected_at))} ET):</b> ${esc(i.correction)}${i.previous_from ? ` Previous start time: ${esc(utcShort(i.previous_from))} UTC.` : ""}${i.previous_to ? ` Previous end time: ${esc(utcShort(i.previous_to))} UTC.` : ""}</p>` : "";
    return `<article class="lg-inc"><h3><span class="kind">${esc(i.kind)}</span>${esc(i.scope_text)}</h3><p class="when">${esc(span + ap)}</p>` +
      `<p><b>What happened:</b> ${esc(i.what)}</p><p><b>Effect on the model:</b> ${esc(i.effect_on_model)}</p><p><b>Evidence:</b> ${esc(i.evidence)}</p>${cor}</article>`;
  }
  function renderAllIncidents() {
    const list = [...incidents].sort((a, b) => ms(a.from) - ms(b.from));
    $("lg-all-inc").innerHTML = list.length ? list.map(incHTML).join("") : '<p class="cnt-muted">No incidents listed.</p>';
  }

  // ---- search ---------------------------------------------------------------
  const q = $("lg-q"), listEl = $("lg-list");
  let shown = [], active = -1;
  function optText(r) { return `${r.label} · ${SRC_LABEL[r.source] || r.source}`; }
  function renderList() {
    const term = q.value.trim().toLowerCase();
    const toks = term.split(/\s+/).filter(Boolean);
    shown = races.filter((r) => {
      const hay = `${r.label} ${r.long} ${r.key} ${r.state} ${SRC_LABEL[r.source] || ""}`.toLowerCase();
      return toks.every((t) => hay.includes(t));
    }).slice(0, 60);
    active = shown.length && term ? 0 : -1;
    listEl.innerHTML = shown.map((r, i) => `<li role="option" id="lg-o${i}" data-i="${i}" aria-selected="${i === active}">${esc(r.label)}<span class="s">${esc(SRC_LABEL[r.source] || r.source)} · ${r.n} rows</span></li>`).join("") || '<li role="option" aria-selected="false">No race matches</li>';
    listEl.hidden = false;
    q.setAttribute("aria-expanded", "true");
  }
  function closeList() { listEl.hidden = true; q.setAttribute("aria-expanded", "false"); }
  function markActive() {
    [...listEl.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    if (active >= 0) q.setAttribute("aria-activedescendant", `lg-o${active}`);
  }
  q.addEventListener("input", renderList);
  q.addEventListener("focus", renderList);
  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (listEl.hidden) renderList(); active = Math.min(shown.length - 1, active + 1); markActive(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); markActive(); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) choose(shown[active]); }
    else if (e.key === "Escape") closeList();
  });
  listEl.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li) { e.preventDefault(); choose(shown[+li.dataset.i]); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".lg-search")) closeList(); });

  // ---- race view ------------------------------------------------------------
  async function choose(r) {
    closeList();
    q.value = optText(r);
    cur = r;
    const u = new URL(location.href);
    u.searchParams.set("race", r.key);
    u.searchParams.set("source", r.source);
    history.replaceState(null, "", u);
    $("lg-race").hidden = false;
    $("race-h").textContent = r.long;
    $("lg-race-sub").textContent = `${SRC_LABEL[r.source] || r.source}. Loading rows…`;
    $("lg-verify").disabled = true;
    $("lg-verify-out").textContent = "";
    $("lg-verify-out").className = "lg-verify-out";
    try {
      const entry = await loadFile(manifest.files.find((f) => f.file === r.file));
      if (cur !== r) return;
      curRows = parsedRows(entry).filter((x) => x.race === r.key);
      curRows.sort((a, b) => ms(a.t) - ms(b.t) || (a.logged_at < b.logged_at ? -1 : 1));
      $("lg-verify").disabled = false;
      renderRace(r, entry);
    } catch (e) {
      $("lg-race-sub").textContent = `Could not load the rows: ${e.message || e}`;
    }
  }

  function renderRace(r, entry) {
    const f = manifest.files.find((x) => x.file === r.file);
    $("lg-race-sub").textContent = `${SRC_LABEL[r.source] || r.source} · race key ${r.key} · ${curRows.length.toLocaleString("en-US")} logged rows. A row is logged only when a published value changed.`;
    const mine = incidents.filter((i) => touches(i, r)).sort((a, b) => ms(a.from) - ms(b.from));
    $("lg-race-inc").innerHTML = mine.length ? `<h3 class="lg-h" style="font-size:16px">Incidents that touch this race (${mine.length})</h3>` + mine.map(incHTML).join("") : '<p class="cnt-muted">No listed incident touches this race.</p>';
    const hasBack = curRows.some((x) => x.recorded === "backfilled"), hasCap = curRows.some((x) => x.recorded === "transcribed_as_published");
    let note = "";
    if (hasBack) {
      note = f.first_live_t
        ? `Rows before ${et(f.first_live_t)} ET (${utc(f.first_live_t)}) were rebuilt afterwards from the server's timeline files and may differ from what the page showed at the time.`
        : "No live row has been written yet for this source: every row was rebuilt afterwards from the server's timeline files and may differ from what the page showed at the time.";
    }
    if (hasCap) note += " Rows shaded orange were transcribed by hand from what the page served (a printout of the feed at about 03:46Z), before a restart rebuilt the history; they are not a machine capture.";
    $("lg-backfill-note").textContent = note;
    $("lg-backfill-note").hidden = !note;
    drawChart(r);
    drawTable();
  }

  function drawTable() {
    const rows = newestFirst ? [...curRows].reverse() : curRows;
    $("lg-order").textContent = newestFirst ? "Showing newest first" : "Showing oldest first";
    $("lg-rowcount").textContent = `${curRows.length.toLocaleString("en-US")} rows`;
    const tb = $("lg-table").tBodies[0];
    tb.innerHTML = rows.map((x) => {
      const share = x.share == null ? "–" : `${pct(x.share, 2)}${x.sd == null ? "" : ` ± ${(x.sd * 100).toFixed(2)}`}`;
      const stale = x.stale ? `yes${x.stale_since ? ` since ${esc(x.stale_since)}` : ""}` : "";
      const cls = (x.stale ? "stale " : "") + (x.recorded === "transcribed_as_published" ? "cap" : "");
      return `<tr class="${cls.trim()}"><td>${esc(et(x.t))}</td><td>${esc(utc(x.t))}</td><td>${cpct(x.counted)}</td><td>${pct(x.raw, 2)}</td><td>${share}</td><td>${pct(x.P)}</td><td>${pct(x.P_v2)}</td><td>${pct(x.pm)}</td><td>${pct(x.kalshi)}</td><td>${stale}</td><td>${esc(x.recorded || "")}</td></tr>`;
    }).join("");
  }
  $("lg-order").addEventListener("click", () => { newestFirst = !newestFirst; if (cur) drawTable(); });

  // ---- chart: step lines of P vs market -------------------------------------
  const NS = "http://www.w3.org/2000/svg";
  function drawChart(r) {
    const T0 = Date.parse("2026-11-03T18:00:00Z");
    const pts = curRows.filter((x) => ms(x.t) >= T0);
    const box = $("lg-chart");
    if (pts.length < 2) { box.innerHTML = '<p class="cnt-muted">Not enough rows from election day to draw a chart.</p>'; $("lg-chart-cap").textContent = ""; return; }
    const W = 900, H = 300, L = 44, R = 12, Tp = 14, B = 34;
    const x0 = ms(pts[0].t), x1 = Math.max(ms(pts[pts.length - 1].t), x0 + 60000);
    const X = (t) => L + ((t - x0) / (x1 - x0)) * (W - L - R), Y = (p) => Tp + (1 - p) * (H - Tp - B);
    const mk = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); return e; };
    const svg = mk("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Step chart of the model chance and market prices for ${r.long} over time` });
    for (let p = 0; p <= 1.001; p += 0.25) {
      svg.append(mk("line", { x1: L, x2: W - R, y1: Y(p), y2: Y(p), stroke: "#ddd" }));
      const t = mk("text", { x: L - 6, y: Y(p) + 4, "text-anchor": "end" }); t.textContent = Math.round(p * 100) + "%"; svg.append(t);
    }
    const nt = 5;
    for (let i = 0; i < nt; i++) {
      const t = x0 + ((x1 - x0) * i) / (nt - 1), tx = mk("text", { x: X(t), y: H - 12, "text-anchor": i === 0 ? "start" : i === nt - 1 ? "end" : "middle" });
      tx.textContent = etShort.format(new Date(t)) + " ET"; svg.append(tx);
    }
    const series = [
      { k: "P", c: "#1f5fbf", name: "Model P(D)", w: 2, f: (x) => x.recorded !== "transcribed_as_published" },
      { k: "P_v2", c: "#1f5fbf", name: "Model with polls", w: 1.5, d: "5 3", f: (x) => x.recorded !== "transcribed_as_published" },
      { k: "pm", c: "#c4501b", name: "Polymarket", w: 2, f: (x) => x.recorded !== "transcribed_as_published" },
      { k: "kalshi", c: "#2a8a4a", name: "Kalshi", w: 2, f: (x) => x.recorded !== "transcribed_as_published" },
      { k: "P", c: "#b3261e", name: "Model as served (hand-transcribed)", w: 2, d: "2 3", f: (x) => x.recorded === "transcribed_as_published" },
    ];
    const used = [];
    for (const s of series) {
      const p = pts.filter((x) => s.f(x) && x[s.k] != null);
      if (p.length < 1) continue;
      let d = "", prevY = null;
      p.forEach((x, i) => { const px = X(ms(x.t)), py = Y(x[s.k]); d += i === 0 ? `M${px.toFixed(1)} ${py.toFixed(1)}` : `H${px.toFixed(1)}V${py.toFixed(1)}`; prevY = py; });
      d += `H${X(x1).toFixed(1)}`;
      if (s.name.includes("transcribed")) d = d.replace(/H[\d.]+$/, "");
      svg.append(mk("path", { d, fill: "none", stroke: s.c, "stroke-width": s.w, ...(s.d ? { "stroke-dasharray": s.d } : {}) }));
      used.push(s);
    }
    box.replaceChildren(svg);
    $("lg-chart-cap").innerHTML = used.map((s) => `<span style="color:${s.c};font-weight:600">${s.d ? "┄" : "━"} ${esc(s.name)}</span>`).join(" &nbsp; ") +
      " &nbsp;·&nbsp; Each line holds its value until the next logged change. Polymarket can read inverted on one-sided books.";
  }

  // ---- verify chain ---------------------------------------------------------
  const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  async function sha(s) { return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))); }
  async function verify() {
    const out = $("lg-verify-out");
    if (!cur) return;
    const entry = fileCache[cur.file];
    const f = manifest.files.find((x) => x.file === cur.file);
    out.className = "lg-verify-out"; out.textContent = "Checking…";
    if (!(window.crypto && crypto.subtle)) { out.className = "lg-verify-out bad"; out.textContent = "This browser cannot compute SHA-256 here (it needs a secure https page)."; return; }
    const SUF = 75; // ,"hash":"<64 hex>"}
    let prev = manifest.genesis_hash;
    for (let i = 0; i < entry.lines.length; i++) {
      const ln = entry.lines[i];
      const m = /,"hash":"([0-9a-f]{64})"\}$/.exec(ln);
      if (!m) { out.className = "lg-verify-out bad"; out.textContent = `Chain broken at line ${i + 1}: no hash field at the end of the line.`; return; }
      const body = ln.slice(0, ln.length - SUF) + "}";
      let o; try { o = JSON.parse(ln); } catch (_) { out.className = "lg-verify-out bad"; out.textContent = `Chain broken at line ${i + 1}: not valid JSON.`; return; }
      if (o.prev_hash !== prev) { out.className = "lg-verify-out bad"; out.textContent = `Chain broken at line ${i + 1}: it does not point to the previous line's hash (a line may be missing or changed).`; return; }
      const h = await sha(prev + body);
      if (h !== m[1]) { out.className = "lg-verify-out bad"; out.textContent = `Chain broken at line ${i + 1}: the stored hash does not match the line's text.`; return; }
      prev = h;
    }
    const same = prev === f.last_hash;
    out.className = "lg-verify-out ok";
    out.textContent = `OK: chain is consistent for ${entry.lines.length.toLocaleString("en-US")} rows in ${f.file}. Last hash ${prev.slice(0, 16)}…` +
      (same ? " (matches the manifest)." : ` (the manifest was read at a different moment and lists ${f.last_hash.slice(0, 16)}…; reload to compare).`);
  }
  $("lg-verify").addEventListener("click", verify);

  // ---- reload / boot --------------------------------------------------------
  $("lg-reload").addEventListener("click", async () => {
    fileCache = {};
    const keep = cur;
    if (await loadIndex() && keep) {
      const r = races.find((x) => x.key === keep.key && x.source === keep.source);
      if (r) choose(r);
    }
  });
  (async () => {
    if (!(await loadIndex())) return;
    const p = new URLSearchParams(location.search);
    const r = races.find((x) => x.key === p.get("race") && (!p.get("source") || x.source === p.get("source")));
    if (r) choose(r);
  })();
})();
