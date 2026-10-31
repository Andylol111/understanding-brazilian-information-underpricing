// Homepage data helpers shared by the elections list, the $10,000 cards and the charts:
// where each election's files live (the live server or the static site), one cached read per
// file, and the two figures several parts of the page need (the model's lead over the market,
// the featured replay).
import * as d3 from "./vendor/d3.js";
import { LIVE, STATIC, getJSON, gatedSettle, leadMinutes, plural } from "./common.js";

export const $ = (id) => document.getElementById(id);

// With ?live=, the live server carries only the elections being counted (status live or scheduled);
// every other election's files and dashboard come from the static site.
let liveIds = new Set();
export const setLiveIds = (ids) => { liveIds = new Set(ids); };
const onLive = (id) => LIVE && liveIds.has(id);
export const href = (id) => `election.html?id=${encodeURIComponent(id)}${onLive(id) ? `&live=${encodeURIComponent(LIVE)}` : ""}`;
export const rootOf = (id) => (onLive(id) ? LIVE : STATIC);

export const metaOf = (id) => getJSON(`${id}/meta.json`, { optional: true, root: rootOf(id) }).catch(() => null);
export const statsOf = (id) => getJSON(`${id}/stats.json`, { optional: true, root: rootOf(id) }).catch(() => null);
export const timelineOf = (id) => getJSON(`${id}/timeline.json`, { optional: true, root: rootOf(id) }).catch(() => null);

// elections.json, by id; names carry the year ("Brazil presidential runoff, 2026"): two elections can share a name
let elections = [];
export const setElections = (list) => { elections = list; };
export const allElections = () => elections;
export const electionOf = (id) => elections.find((e) => e.id === id);
// The name without its bracket ("... 2023 (retrospective replay, simulated count order)"), and the bracket's last part
export const bareName = (name) => name.replace(/\s*\([^)]*\)\s*$/, "");
export const qualifierOf = (e) => e.name.match(/\(([^)]*)\)\s*$/)?.[1].split(",").pop().trim() ?? "";
export const label = (e) => {
  const y = e.date?.slice(0, 4), n = bareName(e.name);
  return y && !n.includes(y) ? `${n}, ${y}` : n;
};
// units counted, by the word elections.json gives for them; a plural is never just "s" (li stays li)
const UNIT_PLURAL = { li: "li", electorate: "electorates", municipality: "municipalities", circonscription: "circonscriptions", county: "counties", district: "districts", locality: "localities", "electoral district": "electoral districts", "federal district": "federal districts", gemeente: "gemeenten", wahlkreis: "Wahlkreise", powiat: "powiaty" };
export const unitWord = (level, n) => {
  if (!level) return "";
  if (n === 1) return level;
  return UNIT_PLURAL[level] ?? plural(level, n);
};
export const labelOf = (id) => { const e = electionOf(id); return e ? label(e) : id; };

export const money = (x) => `$${Math.round(x).toLocaleString("en-US")}`;
export const signedPct = (x, d = 1) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}%`;
// a length of time: "80 min", "51 h"
export const dur = (m) => { const a = Math.abs(Math.round(m)); return a >= 180 ? `${Math.round(a / 60)} h` : `${a} min`; };

export const mainRace = (m) => m?.races?.find((r) => !r.region) ?? m?.races?.[0];
// any market listed for the election, on any venue
export const hasMarkets = (m) => (m?.races ?? []).some((r) => (r.markets ?? []).length > 0);
export const polymarketMarkets = (m) => new Set((m?.races ?? []).flatMap((r) => (r.markets ?? []).filter((x) => x.platform === "polymarket").map((x) => x.id))).size;

// The first minute at which `counted` reached 1%: the moment every lead is measured from.
export const gateRow = (m, rows) => (m?.count_timeline === false ? null : rows.find((d) => d.counted >= 0.01) ?? null);

const diffMin = (a, b) => (a && b ? Math.round((Date.parse(b) - Date.parse(a)) / 60000) : null);
const leads = new Map();
// How many minutes before (+) or after (−) the market the model reached 90% on the eventual winner,
// counted from 1% of the vote. The main race, or, where the election has only regional races
// (Brazil's governors), the median over its races. vs: "market" (Polymarket) or "raw count".
// null: the election has no such measure (no model, no market, or no count over time).
export function leadOf(id) {
  if (!leads.has(id)) {
    leads.set(id, (async () => {
      const m = await metaOf(id);
      const race = mainRace(m);
      if (!race) return null;
      const [stats, tl] = await Promise.all([statsOf(id), timelineOf(id)]);
      const rows = tl?.[race.race_id] ?? [];
      const gate = gateRow(m, rows);
      const s = gatedSettle(stats, race.race_id, gate?.t);
      let minutes = leadMinutes(s), vs = "market", scope = "main";
      if (minutes == null) { minutes = diffMin(s?.model, s?.raw); vs = "raw count"; }
      if (race.region && stats?.settle_gated) {
        const each = Object.values(stats.settle_gated).filter((g) => g.lead_vs === "polymarket" && Number.isFinite(g.lead)).map((g) => g.lead);
        if (each.length > 1) { minutes = d3.median(each); vs = "market"; scope = `median of ${each.length} races`; }
      }
      if (minutes == null) return null;
      return { minutes, vs, scope, race, markets: polymarketMarkets(m) };
    })());
  }
  return leads.get(id);
}

// The replay that explains the method: Brazil's 2026 runoff, where the model was ahead of the market for
// 80 minutes. The numbers at its 1% moment, for the worked examples and the one-night chart.
export const FEATURED = "br2026r2";
let featuredP;
export function featured() {
  featuredP ??= (async () => {
    const [m, stats, tl] = await Promise.all([metaOf(FEATURED), statsOf(FEATURED), timelineOf(FEATURED)]);
    const race = mainRace(m);
    const rows = (tl?.[race?.race_id] ?? []).map((d) => ({ ...d, tms: Date.parse(d.t) }));
    const gate = gateRow(m, rows);
    if (!race || !rows.length || !gate) return null;
    const g = gatedSettle(stats, race.race_id, gate.t);
    return { id: FEATURED, meta: m, race, rows, gate, settle: g, tz: m.tz };
  })();
  return featuredP;
}
