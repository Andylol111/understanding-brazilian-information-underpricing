// Installed before the page's module graph so a failed import or rejected initializer cannot leave
// a permanent "Loading…" screen. This file has no imports: it still runs when a dependency fails.
(() => {
  const messageOf = (reason) => reason?.message ?? reason?.reason?.message ?? reason?.error?.message ?? String(reason?.reason ?? reason?.error ?? reason ?? "Unknown error");
  // A reload that bypasses the caches: ?_r=<time> gives the page's own JS and CSS new addresses (see
  // window.__V in election.html) and makes the data requests revalidate (common.js).
  const reloadButton = () => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = "Reload";
    b.addEventListener("click", () => {
      const u = new URL(location.href);
      u.searchParams.set("_r", String(Date.now()));
      location.replace(u.href);
    });
    return b;
  };
  const show = (reason) => {
    const message = messageOf(reason);
    const mapEmpty = document.getElementById("map-empty");
    const mapWaiting = mapEmpty?.textContent.trim() === "Loading the map…";
    if (mapWaiting) {
      mapEmpty.replaceChildren("The map could not load. Reload the page. ", reloadButton());
      mapEmpty.title = message;
    }
    document.querySelectorAll("h1, p, td").forEach((el) => {
      if (el === mapEmpty) return;
      if (el.textContent.trim() === "Loading…") el.textContent = `Could not load: ${message}`;
    });
    const title = document.getElementById("title");
    if (title?.textContent.trim().startsWith("Loading")) title.textContent = "Dashboard could not load";
    let note = document.getElementById("load-error");
    if (!note) {
      note = document.createElement("p");
      note.id = "load-error";
      note.className = "dash-note load-error";
      (document.querySelector("main") ?? document.body).prepend(note);
    }
    note.textContent = `The page could not be initialized: ${message}`;
    note.hidden = false;
  };
  window.__showLoadError = show;
  // never hang silently: a map still "loading" after 45 s of VISIBLE time has failed (a stalled fetch or module).
  // Only visible seconds count: browsers pause animation frames in background tabs and the map waits for one,
  // so a page opened in a background tab is not stalled. If the map draws after the note was shown
  // (election.js empties and hides #map-empty), the note is withdrawn.
  const waiting = () => document.getElementById("map-empty")?.textContent.trim() === "Loading the map…";
  let visibleMs = 0;
  const tick = setInterval(() => {
    if (document.hidden) return;
    visibleMs += 1000;
    if (!waiting()) { clearInterval(tick); return; } // drawn, or another final message ("No map for this race.")
    if (visibleMs < 45000) return;
    clearInterval(tick);
    show(new Error("the map did not finish loading"));
    const recover = setInterval(() => {
      const m = document.getElementById("map-empty");
      if (!m || !(m.hidden || m.textContent.trim() === "")) return;
      clearInterval(recover);
      const note = document.getElementById("load-error");
      if (note?.textContent.endsWith("the map did not finish loading")) note.hidden = true;
    }, 1000);
  }, 1000);
  window.addEventListener("error", (event) => show(event));
  window.addEventListener("unhandledrejection", (event) => show(event));
})();
