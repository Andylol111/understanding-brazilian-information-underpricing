// guide.html: the rules behind "What to watch now" and the race board. No DOM and no network, so tests run it in node
// (tests/test_us_guide.py). Everything shown is computed here from the live files the dashboard also reads.
//
// Rows are the timeline rows of a race (data/us2026/timeline.json + timeline_tail.json): t, P / P_<poll model> (the model's
// chance that side a wins), pm / kalshi (the markets' price of side a), counted (share of the expected vote), raw (side a's
// two-party share of the count). Group figures come from groups-agg.js.
//
// The feed is built by an engine that is fed rows in time order and keeps one small state per race, so every rule fires on a
// change, once. A rule that is held back by its rate limit keeps its state and fires on a later row if it still holds.
// Nothing counts before a race has 1% of its vote counted: what the prices and the model said before the count is the
// starting point of each rule, never an event.
import { aggregate, frameAt, membersOf, nationalPick } from "./groups-agg.js";

export const GATE = 0.01;                       // share counted from which a race has a count
export const LEVELS = [0.9, 0.95, 0.99];       // confidence levels of the model and the markets
export const GAP_OPEN = 0.15, GAP_CLOSE = 0.05; // model vs Polymarket gap that opens / closes (probability points)
export const FLIP_MARGIN = 0.02;               // the model's leader changes only past 50% -/+ this
export const COUNT_LEAD_FROM = 0.05;           // share counted before the count's leader is followed
export const COUNT_FLIP_MARGIN = 0.004;        // ... and the margin (two-party share) a new leader needs
export const COUNTED_STEPS = [0.1, 0.5, 0.9];
export const SHIFT_MIN = 0.03;                 // group shift vs 2024 (two-party margin) that makes an event
export const TURNOUT_MIN = 0.10;               // group turnout vs expected that makes an event
export const COOL_MIN = { gap: 10, leader: 10, count: 15, group: 5 }; // minutes between events of one family per race
export const KEEP = 300;                       // events kept by the engine
export const TZ = "America/New_York";

const MIN = 60000;
const pc = (x) => `${Math.round(100 * x)}%`;
const pts = (x) => `${(100 * Math.abs(x)).toFixed(1)}`;

/* ---------------------------------------------------------------- rows and labels */

// A race's timeline rows reduced to what the guide reads, ascending by time. pollModel: meta.races[].poll_model ("v2" when absent):
// the model's chance is that version's P, else the count-only P (the dashboard's rule).
export function normRows(rows, pollModel = "v2") {
  const key = `P_${pollModel || "v2"}`;
  const out = [];
  for (const d of rows ?? []) {
    const t = Date.parse(d.t);
    if (!Number.isFinite(t)) continue;
    out.push({ t, p: d[key] ?? d.P ?? null, pm: d.pm ?? null, k: d.kalshi ?? null, c: d.counted ?? null, raw: d.raw ?? null, vp: d.votes_proj ?? null, rem: d.rem_total ?? null, exp: d.turnout_exp_total ?? null });
  }
  return out.sort((x, y) => x.t - y.t);
}

// The model's chance of side `side` ("a" | "b") from a value for side a.
export const forSide = (p, side) => (p == null ? null : side === "a" ? p : 1 - p);
export const sideOf = (p) => (p >= 0.5 ? "a" : "b");

const party = (s) => (s?.id === "dem" || s?.color === "#1f5fbf" ? "D" : s?.id === "rep" || s?.color === "#c8102e" ? "R" : null);
// "D", "R", "I" (an independent facing a major party) or the side's name
export function letter(side, other) {
  if (!side) return "–";
  return party(side) ?? (party(other) ? "I" : side.name ?? side.id ?? "–");
}
export const officeName = (race) => ({ sen: "Senate", gov: "governor" }[String(race.race_id).split("-").pop()] ?? String(race.race_id).split("-").pop());
// "Georgia Senate"; names: region id -> state name
export const raceLabel = (race, names) => `${names?.get?.(race.region) ?? String(race.region ?? "").toUpperCase()} ${officeName(race)}`.trim();
export const raceLink = (id) => `election.html?id=us2026&race=${encodeURIComponent(id)}`;

/* ---------------------------------------------------------------- leads, status */

const first = (rows, f) => { for (let i = 0; i < rows.length; i++) if (f(rows[i])) return rows[i]; return null; };
export const gateOf = (rows) => first(rows, (r) => r.c != null && r.c >= GATE)?.t ?? null;

// Start of the current unbroken stretch at or above `level` for `side` in series `key` ("p" | "pm" | "k"), moved up to the
// 1% gate (the dashboard's definition: the first minute from which the series stays at 0.9, counted from 1% of the vote).
// Minutes with no value are skipped. null when the latest value is below the level or the series has none.
export function runStart(rows, key, side, gate, level = 0.9) {
  let at = null, seen = false;
  for (let i = rows.length - 1; i >= 0; i--) {
    const q = forSide(rows[i][key], side);
    if (q == null) continue;
    if (rows[i].t < gate) break;
    if (q < level) { if (!seen) return null; break; }
    seen = true;
    at = rows[i].t;
  }
  return at == null ? null : Math.max(at, gate);
}

// The model's lead over Polymarket (and Kalshi), minutes: when the model's current 90% stretch on a side began against when
// the market's on the same side did; positive = the model was first. null until both have one. times: the stretches' starts.
export function leadOf(rows) {
  const gate = gateOf(rows);
  const last = [...rows].reverse().find((r) => r.p != null);
  if (gate == null || !last) return { minutes: null, kalshi: null, side: null, times: {} };
  const side = sideOf(last.p);
  const m = runStart(rows, "p", side, gate), pm = runStart(rows, "pm", side, gate), k = runStart(rows, "k", side, gate);
  const mins = (x) => (m != null && x != null ? Math.round((x - m) / MIN) : null);
  return { minutes: mins(pm), kalshi: mins(k), side, times: { model: m, polymarket: pm, kalshi: k } };
}

// What a race is doing now (the status chip): "not started" until 1% counted, "counting", "model 90%" when the model is at 90% on
// its leader, "decided" when it is at 99% and every market is at 95% on the same side.
export function statusOf(last) {
  if (!last || last.c == null || last.c < GATE) return "not started";
  if (last.p == null) return "counting";
  const side = sideOf(last.p), q = forSide(last.p, side);
  const markets = [last.pm, last.k].filter((x) => x != null).map((x) => forSide(x, side));
  if (q >= 0.99 && markets.every((x) => x >= 0.95)) return "decided";
  return q >= 0.9 ? "model 90%" : "counting";
}

/* ---------------------------------------------------------------- the event engine */

export function createEngine() {
  return { events: [], keys: new Set(), cursor: new Map(), st: new Map(), last: new Map(), counted: new Map(), group: { t: -Infinity, cursor: -Infinity } };
}

// one race's rule state
const fresh = () => ({ gate: null, reach: { a: [false, false, false], b: [false, false, false] }, mk: { pm: { a: false, b: false }, k: { a: false, b: false } }, leader: null, cleader: null, gap: false });

function emit(E, ev) {
  if (E.keys.has(ev.key)) return false;
  E.keys.add(ev.key);
  E.events.push(ev);
  if (E.events.length > KEEP * 2) { E.events.sort((x, y) => y.t - x.t || y.seq - x.seq); E.events.length = KEEP; }
  return true;
}
let SEQ = 0;
const make = (key, kind, t, race, text, link, extra = {}) => ({ key, kind, t, race, text, link, seq: SEQ++, ...extra });

// a rate-limited family: an event of the family for this race at most every COOL_MIN[family] minutes
function cooled(E, race, family, t) {
  const at = E.last.get(`${race}|${family}`);
  return at == null || t - at >= COOL_MIN[family] * MIN;
}
const stamp = (E, race, family, t) => E.last.set(`${race}|${family}`, t);

// Apply one row of a race to the engine.
function step(E, race, label, row, ctx) {
  const id = race.race_id;
  const S = E.st.get(id) ?? fresh();
  E.st.set(id, S);
  const a = letter(race.a, race.b), b = letter(race.b, race.a), name = (s) => (s === "a" ? a : b);
  const link = raceLink(id);
  const counted = row.c != null ? row.c : null;
  const started = counted != null && counted >= GATE;
  const levelsNow = (p) => ({ a: p, b: 1 - p });

  if (!started) {
    // before the count: the starting point of every rule
    if (row.p != null) {
      const q = levelsNow(row.p);
      for (const s of ["a", "b"]) LEVELS.forEach((lv, i) => { if (q[s] >= lv) S.reach[s][i] = true; });
      S.leader = sideOf(row.p);
    }
    for (const [src, v] of [["pm", row.pm], ["k", row.k]]) if (v != null) for (const s of ["a", "b"]) if (forSide(v, s) >= LEVELS[0]) S.mk[src][s] = true;
    return;
  }
  if (S.gate == null) S.gate = row.t;
  const at = `${pc(counted)} counted`;

  // the model reaches 90 / 95 / 99% on a side: one event for the highest level crossed
  if (row.p != null) {
    for (const s of ["a", "b"]) {
      const q = forSide(row.p, s);
      let top = -1;
      LEVELS.forEach((lv, i) => { if (q >= lv && !S.reach[s][i]) top = i; });
      if (top >= 0) {
        for (let i = 0; i <= top; i++) S.reach[s][i] = true;
        emit(E, make(`${id}|model|${s}|${LEVELS[top]}`, "model", row.t, id, `${label}: the model reaches ${pc(LEVELS[top])} for ${name(s)} (${at}).`, link, { tag: "Model", level: LEVELS[top], side: s }));
      }
    }
  }
  // each market crosses 90% on a side
  for (const [src, v, nm] of [["pm", row.pm, "Polymarket"], ["k", row.k, "Kalshi"]]) {
    if (v == null) continue;
    for (const s of ["a", "b"]) {
      if (!S.mk[src][s] && forSide(v, s) >= LEVELS[0]) {
        S.mk[src][s] = true;
        emit(E, make(`${id}|${src}|${s}|0.9`, "market", row.t, id, `${label}: ${nm} reaches 90% for ${name(s)} (${at}).`, link, { tag: "Market", source: nm, side: s }));
      }
    }
  }
  // the model and Polymarket (Kalshi where there is no Polymarket) move more than 15 points apart, and later back within 5
  const mk = row.pm ?? row.k, mkName = row.pm != null ? "Polymarket" : "Kalshi";
  if (row.p != null && mk != null) {
    const gap = Math.abs(row.p - mk), s = sideOf(row.p);
    if (!S.gap && gap > GAP_OPEN && cooled(E, id, "gap", row.t)) {
      S.gap = true;
      stamp(E, id, "gap", row.t);
      emit(E, make(`${id}|gap|open|${row.t}`, "gap", row.t, id, `${label}: the model gives ${name(s)} ${pc(forSide(row.p, s))}, ${mkName} ${pc(forSide(mk, s))}, ${Math.round(100 * gap)} points apart.`, link, { tag: "Gap", points: 100 * gap }));
    } else if (S.gap && gap < GAP_CLOSE && cooled(E, id, "gap", row.t)) {
      S.gap = false;
      stamp(E, id, "gap", row.t);
      emit(E, make(`${id}|gap|close|${row.t}`, "gap", row.t, id, `${label}: the model and ${mkName} are back within ${Math.max(1, Math.round(100 * gap))} ${Math.round(100 * gap) <= 1 ? "point" : "points"} (${name(s)} ${pc(forSide(row.p, s))} against ${pc(forSide(mk, s))}).`, link, { tag: "Gap", points: 100 * gap }));
    }
  }
  // the model's leader changes
  if (row.p != null) {
    const want = S.leader === "a" && row.p <= 0.5 - FLIP_MARGIN ? "b" : S.leader === "b" && row.p >= 0.5 + FLIP_MARGIN ? "a" : null;
    if (S.leader == null) S.leader = sideOf(row.p);
    else if (want && cooled(E, id, "leader", row.t)) {
      stamp(E, id, "leader", row.t);
      const prev = S.leader;
      S.leader = want;
      emit(E, make(`${id}|leader|${row.t}`, "leader", row.t, id, `${label}: the model now favours ${name(want)} (${pc(forSide(row.p, want))}), after favouring ${name(prev)} (${at}).`, link, { tag: "Model" }));
    }
  }
  // the count's leader changes (two-party share of the count)
  if (row.raw != null && counted >= COUNT_LEAD_FROM) {
    if (S.cleader == null) S.cleader = sideOf(row.raw);
    else {
      const want = S.cleader === "a" && row.raw <= 0.5 - COUNT_FLIP_MARGIN ? "b" : S.cleader === "b" && row.raw >= 0.5 + COUNT_FLIP_MARGIN ? "a" : null;
      if (want && cooled(E, id, "count", row.t)) {
        stamp(E, id, "count", row.t);
        S.cleader = want;
        emit(E, make(`${id}|count|${row.t}`, "count", row.t, id, `${label}: ${name(want)} moves ahead in the count, ${(100 * forSide(row.raw, want)).toFixed(1)}% of the two-party vote (${at}).`, link, { tag: "Count" }));
      }
    }
  }
  // a state passes 10 / 50 / 90% counted (the first of its races to do so)
  const reg = race.region;
  const done = E.counted.get(reg) ?? -1;
  let top = -1;
  COUNTED_STEPS.forEach((st, i) => { if (counted >= st && i > done) top = i; });
  if (top >= 0) {
    E.counted.set(reg, top);
    emit(E, make(`${reg}|counted|${COUNTED_STEPS[top]}`, "counted", row.t, id, `${label}: ${pc(COUNTED_STEPS[top])} of the expected vote counted.`, link, { tag: "Count" }));
  }
}

// Feed the engine the rows of every race. races: [{ race, rows (normRows) }]; names: region id -> state name. Rows at or before
// a race's cursor were seen already. Rows of all races are applied in time order (ties by race), so the first race of a state
// to pass a count step is the one reported.
export function feedRows(E, races, names) {
  const todo = [];
  races.forEach(({ race, rows }, ri) => {
    const cur = E.cursor.get(race.race_id) ?? -Infinity;
    for (const row of rows) if (row.t > cur) todo.push({ ri, race, row, label: raceLabel(race, names) });
    if (rows.length) E.cursor.set(race.race_id, Math.max(cur, rows[rows.length - 1].t));
  });
  todo.sort((x, y) => x.row.t - y.row.t || x.ri - y.ri);
  for (const x of todo) step(E, x.race, x.label, x.row);
  return todo.length;
}

/* ---------------------------------------------------------------- group events */

// Candidates from groups-agg's headline figures at time t. Only groups whose fully counted counties pass the floor of
// groups-agg (>= 3 counties and >= 15% of the group's expected vote) have a swing or a turnout here.
// ctx: { races (registered, with units), preps: Map(race_id -> prepRace), groups, counties, names }
export function groupCandidates(ctx, t) {
  const out = [];
  const picks = nationalPick(ctx.races).filter((r) => ctx.preps.has(r.race_id));
  const mem = memberCache(ctx);
  // sides: the letters of side a and side b
  const judge = (scope, row, items, link, sides) => {
    const g = aggregate(items, "p24");
    const c = g.complete;
    if (c.swing != null && Math.abs(c.swing) >= SHIFT_MIN) {
      const who = c.swing > 0 ? sides[0] : sides[1];
      out.push({ key: `grp|${scope}|${row.key}|shift`, kind: "shift", t, scope, row, value: c.swing, link, text: (name) => `${name}: ${who} shift of ${pts(c.swing)} points against the 2024 presidential vote, ${countiesOf(c.n)}.` });
    }
    const tr = g.turnout.complete;
    if (c.enough && tr != null && Math.abs(tr - 1) >= TURNOUT_MIN) {
      out.push({ key: `grp|${scope}|${row.key}|turnout`, kind: "turnout", t, scope, row, value: tr - 1, link, text: (name) => `${name}: turnout ${Math.round(100 * Math.abs(tr - 1))}% ${tr > 1 ? "above" : "below"} what was expected, ${countiesOf(c.n)}.` });
    }
  };
  // nationally: every kind of place, one race per state
  ctx.groups.dimensions.forEach((d, k) => d.groups.forEach((g, gi) => {
    const row = { key: `${d.id}.${g.id}`, label: groupLabel(d, g) };
    const items = picks.map((r) => {
      const prep = ctx.preps.get(r.race_id);
      return { prep, k: frameAt(prep, t), members: mem(prep, row.key, { dim: k, group: gi }) };
    }).filter((x) => x.k >= 0);
    if (items.length) judge("us", row, items, "groups.html", ["D", "R"]);
  }));
  // per state: its key regions
  for (const r of picks) {
    const prep = ctx.preps.get(r.race_id), k = frameAt(prep, t);
    if (k < 0) continue;
    for (const g of ctx.groups.regions?.[r.region] ?? []) {
      const row = { key: `reg.${r.region}.${g.id}`, label: g.label };
      judge(r.region, row, [{ prep, k, members: mem(prep, row.key, { fips: g.fips }) }], raceLink(r.race_id), [letter(r.a, r.b), letter(r.b, r.a)]);
    }
  }
  return out;
}

// the member lists of groups are kept on the prepared race (groups.js does the same), so each is worked out once
function memberCache(ctx) {
  return (prep, key, sel) => {
    const m = (prep.mem ??= new Map());
    let v = m.get(key);
    if (!v) m.set(key, (v = membersOf(prep, ctx.counties, sel)));
    return v;
  };
}
const countiesOf = (n) => `${n} fully counted ${n === 1 ? "county" : "counties"}`;

// "Middle" of education, age or income needs its dimension to be understood on its own
export const groupLabel = (d, g) => (g.label === "Middle" ? `Middle ${d.label.toLowerCase()}` : g.label);

// Evaluate the groups at every frame time after the last one seen, thinned to one evaluation per `every` ms of frame time. At each
// evaluation the strongest candidate not yet reported is emitted, at most one per COOL_MIN.group minutes overall.
export function feedGroups(E, ctx, every = 2 * MIN) {
  const times = [...new Set([...ctx.preps.values()].flatMap((p) => p.frames.map((f) => f.t)))].filter((t) => t > -8e15 && t > E.group.cursor).sort((x, y) => x - y);
  let lastEval = -Infinity, n = 0;
  for (const t of times) {
    if (t - lastEval < every && t !== times[times.length - 1]) continue;
    lastEval = t;
    n++;
    if (t - E.group.t < COOL_MIN.group * MIN) continue;
    const cands = groupCandidates(ctx, t).filter((c) => !E.keys.has(c.key));
    cands.sort((x, y) => Math.abs(y.value) / (y.kind === "shift" ? SHIFT_MIN : TURNOUT_MIN) - Math.abs(x.value) / (x.kind === "shift" ? SHIFT_MIN : TURNOUT_MIN));
    const c = cands[0];
    if (!c) continue;
    const state = ctx.names?.get?.(c.scope) ?? c.scope.toUpperCase();
    const name = c.scope === "us" ? `${c.row.label}, nationally` : c.row.label.includes(state) ? c.row.label : `${c.row.label}, ${state}`;
    E.group.t = t;
    emit(E, make(c.key, "group", t, c.scope, c.text(name), c.link, { tag: "Groups", value: c.value }));
  }
  if (times.length) E.group.cursor = times[times.length - 1];
  return n;
}

// The groups that have a headline shift now (complete counties pass groups-agg's floor), largest shift first. scope: "us" (every kind
// of place over one race per state) or a state id (its race: the kinds of place inside the state, then its key regions).
// Each: { label, scope, swing (side a minus side b margin against 2024, a fraction), who (the letter of the side it favours),
// n (fully counted counties), turnout (against expected, 1 = as expected, or null) }
export function groupHeadlines(ctx, scope = "us") {
  const picks = nationalPick(ctx.races).filter((r) => ctx.preps.has(r.race_id));
  const races = scope === "us" ? picks : picks.filter((r) => r.region === scope);
  const mem = memberCache(ctx);
  const out = [];
  const add = (label, items, sides) => {
    if (!items.length) return;
    const g = aggregate(items, "p24"), c = g.complete;
    if (c.swing != null) out.push({ label, scope, swing: c.swing, who: c.swing > 0 ? sides[0] : sides[1], n: c.n, turnout: c.enough ? g.turnout.complete : null });
  };
  ctx.groups.dimensions.forEach((d, k) => d.groups.forEach((g, gi) => {
    const items = races.map((r) => {
      const prep = ctx.preps.get(r.race_id);
      return { prep, k: frameAt(prep), members: mem(prep, `${d.id}.${g.id}`, { dim: k, group: gi }) };
    }).filter((x) => x.k >= 0);
    add(groupLabel(d, g), items, scope === "us" ? ["D", "R"] : [letter(races[0]?.a, races[0]?.b), letter(races[0]?.b, races[0]?.a)]);
  }));
  if (scope !== "us") {
    for (const r of races) {
      const prep = ctx.preps.get(r.race_id), k = frameAt(prep);
      if (k < 0) continue;
      for (const g of ctx.groups.regions?.[scope] ?? []) add(g.label, [{ prep, k, members: mem(prep, `reg.${scope}.${g.id}`, { fips: g.fips }) }], [letter(r.a, r.b), letter(r.b, r.a)]);
    }
  }
  return out.sort((x, y) => Math.abs(y.swing) - Math.abs(x.swing));
}

// The feed: newest first, at most `limit`.
export const feedOf = (E, limit = 30) => [...E.events].sort((x, y) => y.t - x.t || y.seq - x.seq).slice(0, limit);

/* ---------------------------------------------------------------- the race board and disagreements */

// One board row of a race from its normalised rows. The chance columns are for the side the race leans to (the model's where it
// has one, else Polymarket's, else Kalshi's), so the sources read against each other.
export function boardRow(race, rows, names) {
  const lastAny = rows.at(-1) ?? null;
  const latest = (key) => { for (let i = rows.length - 1; i >= 0; i--) if (rows[i][key] != null) return rows[i][key]; return null; };
  const p = latest("p"), pm = latest("pm"), k = latest("k");
  const ref = p ?? pm ?? k;
  const side = ref == null ? null : sideOf(ref);
  const c = lastAny?.c ?? null;
  const lead = leadOf(rows);
  const status = rows.length ? statusOf({ c, p, pm, k }) : "not started";
  return {
    id: race.race_id, race, label: raceLabel(race, names), region: race.region, link: raceLink(race.race_id), modeled: true,
    side, leaderLetter: side ? letter(side === "a" ? race.a : race.b, side === "a" ? race.b : race.a) : null,
    leaderName: side ? (side === "a" ? race.a : race.b)?.name ?? null : null,
    model: forSide(p, side), pm: forSide(pm, side), kalshi: forSide(k, side), ref: ref == null ? null : forSide(ref, side), refA: ref,
    pA: p, pmA: pm, kA: k, counted: c, status, lead: lead.minutes, leadKalshi: lead.kalshi, leadSide: lead.side,
    started: c != null && c >= GATE, close: ref == null ? 1 : Math.abs(ref - 0.5),
  };
}

// A race known only from the overview (no county count): market prices of its Democratic side.
export function overviewRow(o, names) {
  const pm = o.pm?.price ?? null, k = o.kalshi?.price ?? null;
  const ref = pm ?? k ?? o.polls?.p_win ?? null;
  const side = ref == null ? null : sideOf(ref);
  const a = { id: "dem", name: o.dem_side?.name ?? "Democratic", color: "#1f5fbf" }, b = { id: "rep", name: o.candidates?.R ?? "Republican", color: "#c8102e" };
  const race = { race_id: o.race_key, region: o.state, a, b };
  return {
    id: o.race_key, race, label: `${o.state_name ?? names?.get?.(o.state) ?? String(o.state).toUpperCase()} ${o.office === "senate" ? "Senate" : "governor"}`, region: o.state,
    link: raceLink(o.race_key), modeled: false, side, leaderLetter: side === "a" ? "D" : side === "b" ? "R" : null,
    leaderName: side ? (side === "a" ? a : b).name : null, model: null, pm: forSide(pm, side), kalshi: forSide(k, side), ref: ref == null ? null : forSide(ref, side), refA: ref,
    pA: null, pmA: pm, kA: k, counted: null, status: "market only", lead: null, leadKalshi: null, leadSide: null, started: false, close: ref == null ? 1 : Math.abs(ref - 0.5),
  };
}

// Sort keys of the board: tossups first = closest to 50% on the race's reference source.
export const BOARD_SORT = {
  race: (r) => r.label, model: (r) => -(r.model ?? -1), pm: (r) => -(r.pm ?? -1), kalshi: (r) => -(r.kalshi ?? -1),
  counted: (r) => -(r.counted ?? -1), status: (r) => ["counting", "model 90%", "decided", "not started", "market only"].indexOf(r.status),
  lead: (r) => (r.lead == null ? Infinity : -r.lead), close: (r) => r.close,
};
export function sortBoard(rows, key = "close", dir = 1) {
  const f = BOARD_SORT[key] ?? BOARD_SORT.close;
  const gone = (v) => v == null || v === Infinity; // nothing to sort by: always last
  return [...rows].sort((x, y) => {
    const a = f(x), b = f(y);
    if (gone(a) !== gone(b)) return gone(a) ? 1 : -1;
    const c = gone(a) ? 0 : typeof a === "string" ? a.localeCompare(b) : a - b;
    return c ? c * dir : x.close - y.close || x.label.localeCompare(y.label);
  });
}

// Races with a count, largest model-vs-market difference first (Polymarket, else Kalshi): [{ row, gap (points), side, model, market, source }]
export function disagreements(rows) {
  return rows.filter((r) => r.started && r.pA != null && (r.pmA ?? r.kA) != null).map((r) => {
    const m = r.pmA ?? r.kA, s = sideOf(r.pA);
    return { row: r, gap: 100 * Math.abs(r.pA - m), side: s, model: forSide(r.pA, s), market: forSide(m, s), source: r.pmA != null ? "Polymarket" : "Kalshi" };
  }).sort((x, y) => y.gap - x.gap);
}

// the clock in New York ("9:42 PM")
const fmt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ });
export const clockET = (t) => fmt.format(new Date(t));
