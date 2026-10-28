// "Before the count" helpers (meta.phase = "pre-count"): countdowns to poll close and live Polymarket
// prices read in the browser from Polymarket's public market API (it allows cross-origin reads).
// Kalshi's API does not, so Kalshi values come from the timeline the scheduled build writes.

const GAMMA = "https://gamma-api.polymarket.com/markets";

// "5 d 3 h", "3 h 20 min", "12 min" (compact: "5d 3h", "3h 20m", "12m"); null once the time has passed.
export function untilText(ms, compact = false) {
  if (!(ms > 0)) return null;
  const min = Math.ceil(ms / 60000);
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
  const [D, H, M] = compact ? ["d", "h", "m"] : [" d", " h", " min"];
  if (d) return `${d}${D} ${h}${H}`;
  if (h) return `${h}${H} ${m}${M}`;
  return `${m}${M}`;
}

// YES probability from a Gamma market object: the midpoint when the spread is at most 10¢, else the
// last trade (the exporter's `pm` rule).
function yesPrice(m) {
  const bid = +m.bestBid, ask = +m.bestAsk;
  const yes = Number.isFinite(bid) && Number.isFinite(ask) && ask > 0 && ask - bid <= 0.1 ? (bid + ask) / 2 : +m.lastTradePrice;
  return Number.isFinite(yes) ? yes : null;
}

// YES prices for many markets by slug, 20 slugs per request, one request after another.
export async function polymarketYes(slugs) {
  const out = new Map();
  const list = [...new Set(slugs.filter(Boolean))];
  for (let i = 0; i < list.length; i += 20) {
    const q = list.slice(i, i + 20).map((s) => `slug=${encodeURIComponent(s)}`).join("&");
    const r = await fetch(`${GAMMA}?${q}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`Polymarket: HTTP ${r.status}`);
    for (const m of await r.json()) if (!m.closed) out.set(m.slug, yesPrice(m));
  }
  return out;
}
