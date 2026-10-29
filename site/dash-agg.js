// Region totals from unit data (the state level of a map), shared by the drawing engine and the
// page. Units are weighted by their expected vote (V0; equal weights where a file has none):
// share counted, the raw count's share for side a (weighted by votes counted), the model's
// projection, the comparison election, and who leads where units carry a winner.
// U: { n, F, A, P, prior, V0 } as built by dash-engine.js units(); values in thousandths, -1 = none.
import * as d3 from "./vendor/d3.js";

// Leader colouring (as on YAPMS): the side ahead's colour in four bands of margin (points between
// the two sides): tilt under 1, lean 1-5, likely 5-15, safe 15 or more. Even the lightest band is
// clearly tinted; an exact tie is grey.
export const BANDS = ["Tilt", "Lean", "Likely", "Safe"];
export const TIE = "#bdbab2";
const STRENGTH = [0.4, 0.6, 0.8, 1];
export const bandOf = (pt) => { const a = Math.abs(pt); return a < 1 ? 0 : a < 5 ? 1 : a < 15 ? 2 : 3; };
export const bandColours = (colour, neutral = "#f4f2ec") => STRENGTH.map((s) => d3.interpolateLab(neutral, colour)(s));
// colour for a margin in points (side a minus side b), from bandColours of each side
export const bandColour = (pt, aCols, bCols) => (pt === 0 ? TIE : (pt > 0 ? aCols : bCols)[bandOf(pt)]);
export function unitsByRegion(region) {
  const out = new Map();
  region.forEach((r, j) => {
    if (r == null) return;
    if (!out.has(r)) out.set(r, []);
    out.get(r).push(j);
  });
  return out;
}

export function regionAgg(U, k, js) {
  let w = 0, wc = 0, wa = 0, sa = 0, wp = 0, sp = 0, wq = 0, sq = 0;
  const flat = !js.some((j) => U.V0[j] > 0);
  for (const j of js) {
    const v = flat ? 1 : U.V0[j];
    if (!(v > 0)) continue;
    w += v;
    const pr = U.prior[j];
    if (pr >= 0) { wq += v; sq += v * pr; }
    if (k < 0) continue;
    const i = k * U.n + j, f = U.F[i] / 1000, a = U.A[i], p = U.P[i];
    wc += v * f;
    if (a >= 0 && f > 0) { wa += v * f; sa += v * f * a; }
    if (p >= 0) { wp += v; sp += v * p; }
  }
  return {
    weight: w,
    counted: w > 0 && k >= 0 ? wc / w : null,
    raw: wa > 0 ? sa / wa / 1000 : null,
    proj: wp > 0 ? sp / wp / 1000 : null,
    prior: wq > 0 ? sq / wq / 1000 : null,
  };
}

// The party leading the most expected votes among a region's units (winner: per unit party ids).
export function regionWinner(U, js, winnerOf) {
  const by = new Map();
  for (const j of js) {
    const p = winnerOf(j);
    if (p == null) continue;
    const v = U.V0[j] > 0 ? U.V0[j] : 1;
    by.set(p, (by.get(p) ?? 0) + v);
  }
  let best = null, bv = 0, total = 0;
  for (const [p, v] of by) { total += v; if (v > bv) { best = p; bv = v; } }
  return best == null ? null : { party: best, share: bv / total };
}
