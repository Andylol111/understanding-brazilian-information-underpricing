// Region totals from unit data (the state level of a map), shared by the drawing engine and the
// page. Units are weighted by their expected vote (V0; equal weights where a file has none):
// share counted, the raw count's share for side a (weighted by votes counted), the model's
// projection, the comparison election, and who leads where units carry a winner.
// U: { n, F, A, P, prior, V0 } as built by dash-engine.js units(); values in thousandths, -1 = none.

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
