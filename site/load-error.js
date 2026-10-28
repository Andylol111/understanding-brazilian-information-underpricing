// Installed before the page's module graph so a failed import or rejected initializer cannot leave
// a permanent "Loading…" screen. This file has no imports: it still runs when a dependency fails.
(() => {
  const messageOf = (reason) => reason?.message ?? reason?.reason?.message ?? reason?.error?.message ?? String(reason?.reason ?? reason?.error ?? reason ?? "Unknown error");
  const show = (reason) => {
    const message = messageOf(reason);
    document.querySelectorAll("h1, p, td").forEach((el) => {
      if (el.textContent.trim() === "Loading…" || el.textContent.trim() === "Loading the map…") el.textContent = `Could not load: ${message}`;
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
  window.addEventListener("error", (event) => show(event));
  window.addEventListener("unhandledrejection", (event) => show(event));
})();
