// Election-night announcements (the strip under the top bar of election.html, the top of guide.html). Everything is derived on the
// page from the live files it already reads; this module is pure (samples in, items out) apart from mountBanner. Wording is
// plain: "Model projects", never "calls"; model figures only, a market price enters only the follow-up line.
//
// Rules (set by the integrator):
//  * a projection ("Model projects X wins Y") fires only when the model's chance for one side is >= CALL (0.99) on 2 consecutive
//    distinct row times AND >= MIN_COUNTED (20%) of the expected vote is counted; one noisy frame never fires it;
//  * a projection that later drops below WITHDRAW (0.95) for that side is withdrawn: a new "Projection withdrawn" item, the
//    original is struck through; it may fire again under the same rule;
//  * "decided" is never taken from a model chance. Count rule (countDecided): the leader's counted lead exceeds the most the
//    rest of the vote could still move it (50% headroom over the model's expected vote) and >= 80% is counted. Rows carry:
//    raw (counted two-party share of side a), votes_proj (projected total), rem_total (projected remaining), turnout_exp_total
//    (the model's expected total), counted (share counted); a row missing any of them is never decided. A chamber is decided
//    when seats not up + decided races (this rule; on a replayed night also the page's decisionOf) reach the threshold.
//  * Each item fires once; the engine's state is serialisable (sessionStorage) so a reload does not re-announce.
import { clock } from "./common.js";

export const DECIDED_COUNTED = 0.8, DECIDED_HEADROOM = 1.5;
export const CALL = 0.99, WITHDRAW = 0.95, MIN_COUNTED = 0.2, NEED_ROWS = 2, MARKET_LEVEL = 0.9;
const TZ = "America/New_York";
// An added (extra/us2026) race whose live feed covers only part of its district / state is "partial": no projection, no decided item.
// The meta says so with partial (true/false) or a coverage share (coverage_share, coverage: 0 to 1; full = 1); a missing field counts as partial.
export const partialOf = (c) => {
  if (!c?.added) return false;
  if (typeof c.partial === "boolean") return c.partial;
  const cov = c.coverage_share ?? c.coverage;
  return !(typeof cov === "number" && cov >= 0.999);
};
// "added model (secondary source)" where the added race's count comes from civicAPI, "added model" where from an official feed
export const addedLabel = (r) => (/civicapi/i.test(r?.source?.name ?? "") ? "added model (secondary source)" : "added model");
export const etClock = (t) => `${clock(t, TZ)} ET`;
const pct = (x) => `${Math.round(100 * x)}%`;
const min = (ms) => Math.max(0, Math.round(ms / 60000));

// Chance that at least `need` of the seats go to the side whose per-seat chances are ps (independent seats; a seat not up is 1 or 0).
export function controlChance(ps, need) {
  let dist = [1];
  for (const raw of ps) {
    const p = raw == null || !Number.isFinite(raw) ? 0.5 : Math.min(1, Math.max(0, raw));
    const next = new Array(dist.length + 1).fill(0);
    for (let k = 0; k < dist.length; k++) { next[k] += dist[k] * (1 - p); next[k + 1] += dist[k] * p; }
    dist = next;
  }
  let s = 0;
  for (let k = Math.max(0, need); k < dist.length; k++) s += dist[k];
  return Math.min(1, Math.max(0, s));
}

// A side's party letter from its id ("dem" | "rep"); null for any other side (an independent, "no").
export const partyOf = (side) => ({ dem: "D", rep: "R" })[side?.id] ?? null;

// Senate: the Vice President in 2026 is JD Vance, a Republican (term 2025-2029), so 50 Republican seats control the chamber (the VP
// breaks a 50-50 tie) while the Democrats need 51. House: 218 for either side (435 seats; a vacancy changes nothing here).
export const SENATE_NEED = { D: 51, R: 50 };
export const HOUSE_NEED = { D: 218, R: 218 };

// Count-based "decided", independent of any model chance. sample: { c (share counted), raw (counted two-party share of side a),
// vp (projected total vote), rem (projected remaining vote), exp (the model's expected total vote) }. Returns "a" | "b" | null.
// counted votes = vp - rem; the lead = |2 raw - 1| x counted votes; the remaining vote that could still move it is at most
// remaining_upper = max(0, 1.5 x exp - counted votes) (50% headroom: the expected-vote level is a known weakness).
export function countDecided(x) {
  const { c, raw, vp, rem, exp } = x ?? {};
  if (![c, raw, vp, rem, exp].every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  if (c < DECIDED_COUNTED || raw === 0.5) return null;
  const counted = vp - rem;
  if (!(counted > 0) || !(exp > 0)) return null;
  const lead = Math.abs(2 * raw - 1) * counted, upper = Math.max(0, DECIDED_HEADROOM * exp - counted);
  return lead > upper ? (raw > 0.5 ? "a" : "b") : null;
}

export function createEvents(saved = null) {
  const st = saved && saved.v === 1 ? saved : { v: 1, series: {}, items: [] };
  st.decided ??= {};
  const have = new Set(st.items.map((i) => i.id));
  const add = (it) => { if (!have.has(it.id)) { have.add(it.id); st.items.push(it); } };
  const ser = (key) => (st.series[key] ??= { lastT: -Infinity, run: 0, runSide: null, active: null, n: 0, mFirst: {}, mDone: {} });

  // One step of a projection series (a race or a chamber): sample { t, p (chance of side a), c (share counted, 0 to 1) }.
  // describe(side) -> { call, withdrawn, tip } texts.
  function step(key, s, sample, d) {
    const p = sample.p;
    if (p == null || !Number.isFinite(p)) return;
    const c = sample.c ?? 0;
    const chance = { a: p, b: 1 - p };
    if (s.active) {
      const cur = chance[s.active.side];
      if (cur < WITHDRAW) {
        add({ id: `${key}|w|${s.active.n}`, kind: "withdrawn", ts: sample.t, ref: `${key}|c|${s.active.n}`, key, text: d.withdrawn(s.active.side, cur), tip: d.tip });
        s.active = null; s.run = 0; s.runSide = null;
      }
    }
    const lead = p >= CALL ? "a" : p <= 1 - CALL ? "b" : null;
    const qualifies = lead && c >= MIN_COUNTED;
    if (!qualifies) { s.run = 0; s.runSide = null; return; }
    if (s.runSide === lead) s.run++; else { s.runSide = lead; s.run = 1; }
    if (s.run >= NEED_ROWS && !s.active) {
      s.n++;
      s.active = { side: lead, n: s.n, t: sample.t, mk: {} };
      add({ id: `${key}|c|${s.n}`, kind: "call", ts: sample.t, key, side: lead, text: d.call(lead), tip: d.tip });
    }
  }

  // Market follow-up for a race: rows carry pm / k (a price for side a). Called after step() for the same sample.
  function markets(key, s, sample, label) {
    // a price counts as "reaching 90%" only from the first counted votes on: a price that was already there before the count is not news
    if (s.mStart == null && (sample.c ?? 0) > 0) s.mStart = sample.t;
    for (const plat of ["pm", "k"]) {
      const v = sample[plat];
      if (v == null || !((sample.c ?? 0) > 0)) continue;
      // a market already at 90% for a side when the first votes were counted never "reached" it: no item for that side
      const init = sample.t === s.mStart;
      for (const side of ["a", "b"]) {
        const price = side === "a" ? v : 1 - v;
        const k = `${plat}${side}`;
        if (price >= MARKET_LEVEL && s.mFirst[k] == null) s.mFirst[k] = init ? "pre" : sample.t;
      }
    }
    const a = s.active;
    if (!a) return;
    for (const [plat, name] of [["pm", "Polymarket"], ["k", "Kalshi"]]) {
      const first = s.mFirst[`${plat}${a.side}`], tag = `${plat}${a.n}`;
      if (first == null || first === "pre" || s.mDone[tag]) continue;
      s.mDone[tag] = 1;
      const ts = Math.max(first, a.t), diff = Math.abs(first - a.t);
      const when = first < a.t ? `${min(diff)} min before the model` : first === a.t ? "at the same time as the model" : `${min(diff)} min after the model`;
      add({ id: `${key}|m|${plat}|${a.n}`, kind: "market", ts, key, text: `${name} reaches 90% on ${label}: ${when}`, tip: "Market price for the same side the model projects; the model's figure is not changed by prices." });
    }
  }

  return {
    // race: { id, label (state and office), a, b (sides with id/name), secondary }, rows: [{ t, p, c, pm, k }] ascending by t
    feedRace(race, rows) {
      const key = `race:${race.id}`, s = ser(key);
      const label = race.label + (typeof race.secondary === "string" ? ` (${race.secondary})` : race.secondary ? " (secondary source, civicAPI)" : "");
      const who = (side) => { const x = race[side]; const pty = partyOf(x); return `${x?.name ?? (side === "a" ? "first side" : "second side")}${pty ? ` (${pty})` : ""}`; };
      const d = {
        call: (side) => `Model projects ${who(side)} wins ${label} · ≥99%`,
        withdrawn: (side, cur) => `Projection withdrawn: ${label} back to ${pct(cur)}`,
        tip: `Model chance ≥99% for one side on 2 refreshes in a row, with ${pct(MIN_COUNTED)} or more of the expected vote counted. Not an official call.`,
      };
      for (const r of rows) {
        if (!(r.t > s.lastT)) continue;
        s.lastT = r.t;
        // a race with only part of its vote covered live (race.partial) never produces a projection or a "decided" item
        if (race.partial) continue;
        step(key, s, r, d);
        markets(key, s, r, label);
        const side = st.decided[race.id] ? null : countDecided(r);
        if (side) {
          const x = race[side], pty = partyOf(x);
          st.decided[race.id] = { side, party: pty, t: r.t };
          add({ id: `decided:race:${race.id}`, kind: "decided", ts: r.t, key: `decided:${race.id}`, text: `Decided by the count: ${x?.name ?? "leader"}${pty ? ` (${pty})` : ""} wins ${label} — lead larger than any plausible remaining vote`, tip: "The counted lead is larger than 1.5 times the model's expected vote minus the votes counted, with 80% or more counted. Not a model chance." });
        }
      }
    },
    // chamber: "senate" | "house"; samples [{ t, p (chance Democrats control, seats independent), c }] ascending
    feedChamber(chamber, samples) {
      const key = `chamber:${chamber}`, s = ser(key), name = chamber === "senate" ? "Senate" : "House";
      const d = {
        call: (side) => `Model projects ${side === "a" ? "Democrats" : "Republicans"} control the ${name}`,
        withdrawn: (side, cur) => `Projection withdrawn: ${side === "a" ? "Democratic" : "Republican"} control of the ${name} back to ${pct(cur)}`,
        tip: "If races are independent: chance of control from the live seat projection, 99% or more on 2 refreshes in a row with 20% or more counted.",
      };
      for (const x of samples) {
        if (!(x.t > s.lastT)) continue;
        s.lastT = x.t;
        step(key, s, x, d);
      }
    },
    // The race the count rule decided: { side, party, t } or null.
    decidedOf: (id) => st.decided[id] ?? null,
    // Chamber decided: seats not up + decided races. notUp { D, R }; decided [{ party, t }] (one per race); need { D, R }.
    feedDecided(chamber, { notUp, decided, need }) {
      const name = chamber === "senate" ? "Senate" : "House";
      for (const party of ["D", "R"]) {
        const n = need[party] - (notUp?.[party] ?? 0);
        const list = decided.filter((x) => x.party === party).sort((x, y) => x.t - y.t);
        if (n > list.length || n <= 0) continue;
        const who = party === "D" ? "Democrats" : "Republicans";
        const vp = chamber === "senate" && party === "R" ? " with the Republican Vice President" : "";
        add({ id: `decided:${chamber}:${party}`, kind: "decided", ts: list[n - 1].t, key: `decided:${chamber}`, text: `${name} control decided: ${who} reach ${need[party]}${vp}`, tip: "Seats not up this year plus races decided by the count (or, on a replayed night, by the page's decision rule)." });
      }
    },
    // Items shown at time t (ms; Infinity: all), newest first; an item is struck when its projection was withdrawn by t.
    items(t = Infinity) {
      const seen = st.items.filter((i) => i.ts <= t);
      const out = new Set(seen.filter((i) => i.kind === "withdrawn").map((i) => i.ref));
      return seen.map((i) => ({ ...i, struck: out.has(i.id) })).sort((x, y) => y.ts - x.ts || (y.kind === "withdrawn") - (x.kind === "withdrawn"));
    },
    lastT: (key) => st.series[key]?.lastT ?? -Infinity,
    toJSON: () => st,
  };
}

// ---- the strip
const escH = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const itemHTML = (i) => `<span class="ev-t">${etClock(i.ts)}</span> <span class="ev-x${i.struck ? " ev-struck" : ""}${i.kind === "withdrawn" ? " ev-wd" : ""}" title="${escH(i.tip ?? "")}">${escH(i.text)}</span>`;

// Fill `host` (a div, hidden when there is nothing to say): the newest item on one line, a count of earlier ones, a click list.
// counting: whether the count has started (before that the strip stays hidden).
export function renderBanner(host, items, { counting = true } = {}) {
  if (!host) return;
  if (!counting) { host.hidden = true; return; }
  host.hidden = false;
  const key = JSON.stringify(items.map((i) => [i.id, i.struck]));
  if (host.__key === key) return;
  host.__key = key;
  const open = host.classList.contains("open") && items.length > 1;
  if (!items.length) { host.innerHTML = `<span class="ev-none">Announcements: none yet</span>`; host.classList.remove("open"); return; }
  const more = items.length - 1;
  host.innerHTML = `<button type="button" class="ev-top" aria-expanded="${open}" aria-controls="ev-list" ${more ? "" : "disabled"}><span class="ev-lab">Latest</span><span class="ev-line">${itemHTML(items[0])}</span>${more ? `<span class="ev-n">+${more} earlier</span>` : ""}</button>`
    + `<ol class="ev-list" id="ev-list"${open ? "" : " hidden"}>${items.map((i) => `<li>${itemHTML(i)}</li>`).join("")}</ol>`;
  host.classList.toggle("open", open);
}

// One delegated click handler: the top line opens and closes the list.
export function bindBanner(host) {
  host.addEventListener("click", (e) => {
    const b = e.target.closest(".ev-top");
    if (!b || b.disabled) return;
    const open = !host.classList.contains("open");
    host.classList.toggle("open", open);
    b.setAttribute("aria-expanded", String(open));
    host.querySelector(".ev-list").hidden = !open;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && host.classList.contains("open")) host.querySelector(".ev-top")?.click(); });
}

export const loadSaved = (key) => { try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; } };
export const save = (key, ev) => { try { sessionStorage.setItem(key, JSON.stringify(ev.toJSON())); } catch { /* storage full or off */ } };
