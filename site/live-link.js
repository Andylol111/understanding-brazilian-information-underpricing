// Election-night "go live" banner. When a page is opened without ?live= and data/live_link.json names a live base URL
// (the night's tunnel, written by hand from the runbook and re-written if the tunnel restarts), a banner at the top links to
// the same page with &live=<url>. Self-contained: any failure (no file, bad JSON, expired) leaves the page untouched.
(async () => {
  try {
    const params = new URLSearchParams(location.search);
    if (params.get("live")) return;
    const r = await fetch(`data/live_link.json?t=${Math.floor(Date.now() / 60000)}`, { cache: "no-store" });
    if (!r.ok) return;
    const j = await r.json();
    if (!j || typeof j.url !== "string" || !/^https:\/\/[a-z0-9-]+\.trycloudflare\.com\/?$/.test(j.url)) return;
    if (j.until && Date.now() > Date.parse(j.until)) return;
    const u = new URL(location.href);
    u.searchParams.set("live", j.url.endsWith("/") ? j.url : `${j.url}/`);
    if (/election\.html$/.test(u.pathname) && !u.searchParams.get("id") && j.id) u.searchParams.set("id", j.id);
    const bar = document.createElement("div");
    bar.className = "live-link-bar";
    bar.setAttribute("role", "status");
    bar.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:10000;background:#c4501b;color:#fff;font:600 15px/1.4 system-ui,sans-serif;padding:10px 18px;border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.25);max-width:calc(100vw - 32px);text-align:center";
    const a = document.createElement("a");
    a.href = u.toString();
    a.style.cssText = "color:#fff;text-decoration:underline";
    a.textContent = j.label || "Live now: open the election-night page with live counts →";
    bar.append(a);
    document.body.prepend(bar);
  } catch (_) { /* never break the page */ }
})();
