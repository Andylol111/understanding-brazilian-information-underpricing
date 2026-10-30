// Worker wrapper around dash-engine.js: jobs ({ id, type: "geo" | "units" | "prefetch" | "audit" })
// answer with { id, out } or { id, error }; control messages ({ type, ... } without id) go straight
// to the engine, and the engine's events ("painted", "drawStats") are posted back.
// the engine is imported with this worker's own ?v= (the page's build stamp), so a cached engine cannot pair with a new page
const { geo, units, prefetch, audit, control, setEmitter } = await import(`./dash-engine.js${self.location.search}`);

const JOBS = { geo, units, prefetch, audit };

setEmitter((ev) => self.postMessage(ev));
self.postMessage({ type: "ready" }); // the engine module and its imports have loaded

self.onmessage = async ({ data }) => {
  const { id, type } = data;
  if (id == null) {
    try { control(data); } catch (err) { self.postMessage({ type: "error", error: String(err?.stack ?? err) }); }
    return;
  }
  try {
    const out = await JOBS[type](data);
    self.postMessage({ id, out: out.result }, out.transfer);
  } catch (err) {
    self.postMessage({ id, error: String(err?.stack ?? err) });
  }
};
