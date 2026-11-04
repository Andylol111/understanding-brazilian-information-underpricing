// No imports and no browser globals: loaded by node tests (guide-rules.js) as well as the pages.
// A Polymarket price read from a one-sided book comes out inverted (election night 2026: Rhode Island Senate read 0.007
// for Reed while Kalshi had 0.99; Florida's special 0.999 for the Democrat while Kalshi had 0.0015). A price at an extreme
// that the references contradict is not a market reading: null, so callers fall back to Kalshi.
// strong: Kalshi for the same side; weak: the poll average's chance, used only without a strong one. NEVER pass the
// model: a market that disagrees with the model is the thing measured and must not be hidden.
export function pmUsable(pm, strong = [], weak = []) {
  if (pm == null) return null;
  const ok = (xs) => xs.filter((x) => x != null && Number.isFinite(x));
  const s = ok(strong), refs = s.length ? s : ok(weak);
  if (refs.length && ((pm <= 0.02 && refs.every((x) => x >= 0.9)) || (pm >= 0.98 && refs.every((x) => x <= 0.1)))) return null;
  return pm;
}
