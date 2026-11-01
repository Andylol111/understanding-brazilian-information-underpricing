// Group figures for groups.html: counties grouped by what the places are like, aggregated from the dashboard's units files
// (data/us2026/units/<race>.json, the same files the US dashboard reads, live under ?live=). No DOM; tests run it in node.
//
// Per unit j of a race (a county) and frame k, from the units file: V0 (expected votes), f (share counted, thousandths),
// a (side a's two-party share of the count, thousandths, -1 = none), proj_poll / proj (the model's projected final share),
// turnout_so_far (votes counted), turnout_exp and turnout_ratio_live (projected turnout / expected, thousandths).
// Margins are two-party, side a minus side b, as fractions (0.05 = 5 points); baselines come from counties.json.
//
// The headline shift uses COMPLETE counties only (f >= COMPLETE_AT): early and mail ballots are often counted first and lean
// differently, so a part-counted county's margin says little. It is given (complete.swing) only once the complete counties
// are at least MIN_COMPLETE_N counties and MIN_COMPLETE_EV of the group's expected vote; complete.swingAny is the same
// figure without that floor. The raw counted margin of all counted votes is kept as `partial` and is null while the group
// is under PARTIAL_MIN counted.

export const COMPLETE_AT = 950;   // thousandths counted
export const MIN_COMPLETE_N = 3;  // complete counties before a group's shift is given
export const MIN_COMPLETE_EV = 0.15; // and their share of the group's expected vote
export const PARTIAL_MIN = 0.5;   // share of the group's expected vote counted before the partial margin is given

const unitFips = (id) => String(id).slice(String(id).lastIndexOf("-") + 1);

// Index of counties.json by FIPS (built once per page).
export function countyIndex(counties) {
  const m = new Map();
  counties.fips.forEach((f, i) => m.set(f, i));
  return m;
}

// A units file joined to the county table. race: { race_id, a, b, office? }. Baselines are null for a race whose side b is
// not the Republican candidate (its margin is not comparable with D - R margins).
export function prepRace(file, race, counties, index) {
  const n = file.unit_ids.length;
  const office = race.office ?? String(race.race_id).split("-").pop();
  const comparable = race.b?.id === "rep";
  const cidx = file.unit_ids.map((u) => index.get(unitFips(u)) ?? -1);
  const col = (key) => cidx.map((i) => (comparable && i >= 0 ? counties[key]?.[i] ?? null : null));
  const frames = (file.frames ?? []).map((fr) => ({
    t: fr.t ? Date.parse(fr.t) : -8.64e15,
    f: fr.f ?? [], a: fr.a ?? [], p: fr.proj_poll ?? fr.proj ?? [], so: fr.turnout_so_far ?? null, tr: fr.turnout_ratio_live ?? null,
  }));
  return {
    race, n, cidx, fips: file.unit_ids.map(unitFips), names: file.names ?? [],
    V0: Array.from({ length: n }, (_, j) => +(file.V0?.[j] ?? 0) || 0),
    TE: Array.from({ length: n }, (_, j) => +(file.turnout_exp?.[j] ?? NaN)),
    base: { p20: col("p20"), p24: col("p24"), o22: col(office === "gov" ? "g22" : "s22") },
    frames,
  };
}

// The latest frame at or before time t (all frames when t is null): its index, -1 before the first.
export function frameAt(prep, t = null) {
  const fr = prep.frames;
  if (t == null) return fr.length - 1;
  let k = -1;
  for (let i = 0; i < fr.length && fr[i].t <= t; i++) k = i;
  return k;
}

// Units of a race in a group: dimension groups by the county's class (counties.cls[i][dim]), regions by FIPS list.
export function membersOf(prep, counties, sel) {
  const out = [];
  if (sel.fips) {
    const s = sel.fips instanceof Set ? sel.fips : new Set(sel.fips);
    prep.fips.forEach((f, j) => { if (s.has(f)) out.push(j); });
    return out;
  }
  prep.cidx.forEach((i, j) => { if (i >= 0 && counties.cls[i]?.[sel.dim] === sel.group) out.push(j); });
  return out;
}

const wmean = (pairs) => {
  let s = 0, w = 0;
  for (const [v, x] of pairs) if (v != null && Number.isFinite(v) && x > 0) { s += v * x; w += x; }
  return w > 0 ? s / w : null;
};

// Live figures of a group over one or more races (the national view adds one race per state).
// items: [{ prep, k, members }], vs: baseline key ("p24" | "o22" | "p20").
export function aggregate(items, vs = "p24") {
  let ev = 0, evc = 0, n = 0, nComplete = 0, evComplete = 0;
  const swing = [], cm = [], cb = [], pm = [], pb = [], raw = [];
  let soC = 0, teC = 0, teP = 0, trP = 0;
  const basis = [];
  for (const { prep, k, members } of items) {
    const fr = k >= 0 ? prep.frames[k] : null;
    const base = prep.base[vs];
    for (const j of members) {
      const w = prep.V0[j];
      n++;
      if (!(w > 0)) continue;
      ev += w;
      const b = base[j];
      if (!fr) { if (b != null) pb.push([b, w]); continue; }
      const f = fr.f[j] ?? 0, a = fr.a[j] ?? -1;
      evc += (w * f) / 1000;
      const votes = fr.so?.[j] != null ? fr.so[j] : (w * f) / 1000;
      const m = a >= 0 ? (2 * a) / 1000 - 1 : null;
      if (m != null && f > 0) raw.push([m, votes]);
      if (f >= COMPLETE_AT && m != null) {
        nComplete++;
        evComplete += w;
        basis.push({ race: prep.race.race_id, fips: prep.fips[j], f });
        if (b != null) { swing.push([m - b, votes]); cm.push([m, votes]); cb.push([b, votes]); }
        const te = prep.TE[j];
        if (fr.so?.[j] != null && te > 0) { soC += fr.so[j]; teC += te; }
      }
      const p = f >= 1000 && a >= 0 ? a : fr.p[j] ?? -1;
      if (p >= 0) { pm.push([(2 * p) / 1000 - 1, w]); if (b != null) pb.push([b, w]); }
      const tr = fr.tr?.[j], te = prep.TE[j];
      if (tr != null && tr >= 0 && te > 0) { teP += te; trP += (te * tr) / 1000; }
    }
  }
  const counted = ev > 0 ? evc / ev : null;
  const projMargin = wmean(pm), projBase = wmean(pb);
  return {
    n, ev, counted,
    complete: (() => {
      const evShare = ev > 0 ? evComplete / ev : 0, any = wmean(swing);
      const enough = nComplete >= MIN_COMPLETE_N && evShare >= MIN_COMPLETE_EV;
      return { n: nComplete, evShare, enough, swing: enough ? any : null, swingAny: any, margin: wmean(cm), base: wmean(cb), basis };
    })(),
    proj: { margin: projMargin, base: projBase, shift: projMargin != null && projBase != null ? projMargin - projBase : null },
    partial: counted != null && counted >= PARTIAL_MIN ? wmean(raw) : null,
    turnout: { complete: teC > 0 ? soC / teC : null, proj: teP > 0 ? trP / teP : null },
  };
}

// Baselines and makeup of a group before any count: margins weighted by expected votes (V0), makeup by population.
export function baseline(items, counties) {
  let n = 0, ev = 0;
  const m = { p20: [], o22: [], p24: [] };
  const mk = { ba: [], age65: [], medinc: [], white: [], black: [], hisp: [] };
  for (const { prep, members } of items) {
    for (const j of members) {
      n++;
      const w = prep.V0[j];
      if (w > 0) {
        ev += w;
        for (const key of Object.keys(m)) if (prep.base[key][j] != null) m[key].push([prep.base[key][j], w]);
      }
      const i = prep.cidx[j];
      if (i < 0) continue;
      const pop = counties.pop[i];
      for (const key of Object.keys(mk)) mk[key].push([counties[key][i], pop]);
    }
  }
  const out = { n, ev };
  for (const key of Object.keys(m)) out[key] = wmean(m[key]);
  out.makeup = Object.fromEntries(Object.keys(mk).map((key) => [key, wmean(mk[key])]));
  return out;
}

// All frame times of the given races, ascending.
export function timesOf(preps) {
  return [...new Set(preps.flatMap((p) => p.frames.map((f) => f.t)))].filter((t) => t > -8e15).sort((a, b) => a - b);
}

// One race per state for the national view: the Senate race where side a is the Democratic candidate, else such a governor's
// race; states without either are left out (their margins are not D - R).
export function nationalPick(races) {
  const by = new Map();
  for (const r of races) {
    if (r.a?.id !== "dem" || r.b?.id !== "rep" || !r.region) continue;
    const off = String(r.race_id).split("-").pop();
    const had = by.get(r.region);
    if (!had || (off === "sen" && String(had.race_id).split("-").pop() !== "sen")) by.set(r.region, r);
  }
  return [...by.values()];
}
