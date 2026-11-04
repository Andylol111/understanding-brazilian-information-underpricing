// No imports and no browser globals: loaded by node tests (guide-rules.js) as well as the pages.
// A Polymarket price read from a one-sided book comes out inverted (election night 2026: Rhode Island Senate read 0.007
// for Reed while Kalshi had 0.99 and polls 0.999). A price at an extreme that every reference contradicts is not a market
// reading: null, so callers fall back to Kalshi. refs: other probabilities for the same side (Kalshi, polls p_win, model).
export function pmUsable(pm, ...refs) {
  if (pm == null) return null;
  const r = refs.filter((x) => x != null && Number.isFinite(x));
  if (r.length && ((pm <= 0.02 && r.every((x) => x >= 0.9)) || (pm >= 0.98 && r.every((x) => x <= 0.1)))) return null;
  return pm;
}
