// Shared replay-market classification. Timeline probabilities are for the first-named
// outcome (race.a); winner probabilities invert them when race.b won.
export function outcomeA(rows, settle = null) {
  if (settle?.outcome_a != null) return !!settle.outcome_a;
  const last = [...(rows ?? [])].reverse();
  const final = last.find((d) => d.P != null || d.P_v2 != null || d.raw != null || d.share != null);
  const p = final?.P_v2 ?? final?.P;
  if (p != null && (p <= 0.005 || p >= 0.995)) return p > 0.5;
  const share = final?.raw ?? final?.share;
  if (share != null && share !== 0.5) return share > 0.5;
  const market = last.find((d) => d.pm != null)?.pm;
  return market == null || market === 0.5 ? null : market > 0.5;
}

const GENERIC = /^(no|other|another|not\b)/i;
export const winnerName = (race, aWon) => {
  const side = aWon == null ? null : (aWon ? race?.a : race?.b);
  return !side?.name || side.id === "no" || GENERIC.test(side.name) ? null : side.name;
};
export const winnerProbability = (row, aWon, key) => {
  const p = key === "model" ? row?.P_v2 ?? row?.P : row?.[key];
  return p == null || aWon == null ? null : (aWon ? p : 1 - p);
};

// Decided before the count means the eventual winner cost at least 0.90 at the
// first archived minute at/after poll close and never fell below 0.80 afterwards.
export function decidedBeforeCount(race, rows, pollClose, settle = null) {
  const aWon = outcomeA(rows, settle);
  const close = Date.parse(pollClose);
  const priced = (rows ?? []).filter((d) => d.pm != null && Number.isFinite(Date.parse(d.t)));
  const first = priced.find((d) => Date.parse(d.t) >= close);
  const after = priced.filter((d) => Date.parse(d.t) >= close);
  const closePrice = winnerProbability(first, aWon, "pm");
  const floor = after.length ? Math.min(...after.map((d) => winnerProbability(d, aWon, "pm"))) : null;
  return {
    race_id: race?.race_id,
    outcome_a: aWon,
    winner: winnerName(race, aWon),
    price_at_close: closePrice,
    minimum_after_close: floor,
    decided: Number.isFinite(close) && closePrice != null && closePrice >= 0.90 && floor >= 0.80,
  };
}

export const settledAt = (row, aWon) => {
  const p = winnerProbability(row, aWon, "pm");
  return p != null && p >= 0.995;
};

// The first minute from `from` on after which the eventual winner stays at 0.90 or more
// (minutes with no price are skipped); null when it never does.
export function staysAbove(rows, aWon, key, from = 0, level = 0.9) {
  let at = null;
  for (let i = rows.length - 1; i >= from; i--) {
    const p = winnerProbability(rows[i], aWon, key);
    if (p == null) continue;
    if (p < level) break;
    at = rows[i];
  }
  return at;
}
