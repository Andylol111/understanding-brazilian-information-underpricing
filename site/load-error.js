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
  // never hang silently: a map still "loading" after this long has failed (a stalled fetch or module)
  setTimeout(() => { if (document.getElementById("map-empty")?.textContent.trim() === "Loading the map…") show(new Error("the map did not finish loading")); }, 45000);
  window.addEventListener("error", (event) => show(event));
  window.addEventListener("unhandledrejection", (event) => show(event));
})();
