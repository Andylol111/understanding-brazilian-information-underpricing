// Worker wrapper around dash-engine.js: jobs ({ id, type: "geo" | "units" | "prefetch" | "audit" })
// answer with { id, out } or { id, error }; control messages ({ type, ... } without id) go straight
// to the engine, and the engine's events ("painted", "drawStats") are posted back.
// the engine is imported with this worker's own ?v= (the page's build stamp), so a cached engine cannot pair with a new page.
// The handler is attached BEFORE the engine loads: a message that arrives while a top-level await is pending finds no
// handler and is dropped, which left the page's first geometry job unanswered until its 20 s cap whenever the engine
// came over a slow network (GitHub Pages). Messages that arrive early wait for the engine, in order.
let engine = null;
const loading = import(`./dash-engine.js${self.location.search}`).then((m) => {
  engine = m;
  m.setEmitter((ev) => self.postMessage(ev));
  self.postMessage({ type: "ready" }); // the engine module and its imports have loaded
  return m;
}, (err) => {
  setTimeout(() => { throw err; }); // surfaces as the worker's error event, so the page falls back at once
  throw err;
});

self.onmessage = async ({ data }) => {
  const { id, type } = data;
  let m = engine;
  if (!m) {
    try { m = await loading; } catch (err) {
      if (id != null) self.postMessage({ id, error: String(err?.stack ?? err) });
      return;
    }
  }
  if (id == null) {
    try { m.control(data); } catch (err) { self.postMessage({ type: "error", error: String(err?.stack ?? err) }); }
    return;
  }
  try {
    const out = await ({ geo: m.geo, units: m.units, prefetch: m.prefetch, audit: m.audit })[type](data);
    self.postMessage({ id, out: out.result }, out.transfer);
  } catch (err) {
    self.postMessage({ id, error: String(err?.stack ?? err) });
  }
};
