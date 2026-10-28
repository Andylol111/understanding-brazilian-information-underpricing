// Worker wrapper around dash-engine.js: jobs ({ id, type: "geo" | "units" | "prefetch" }) answer
// with { id, out } or { id, error }; control messages ({ type, ... } without id) go straight to the
// engine, and the engine's events ("painted", "drawStats") are posted back.
import { geo, units, prefetch, control, setEmitter } from "./dash-engine.js";

const JOBS = { geo, units, prefetch };

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
