import { LIVE, getJSON, esc, fmtDate, evidenceLabel, STATUS } from "./common.js";

const list = document.getElementById("elections");
const order = { live: 0, scheduled: 1, replay: 2 };

function render(elections) {
  const rows = [...elections].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || (a.status === "scheduled" ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date)));
  const live = LIVE ? `&live=${encodeURIComponent(LIVE)}` : "";
  list.innerHTML = rows.map((e) => {
    const href = `election.html?id=${encodeURIComponent(e.id)}${live}`;
    const name = `<a href="${href}">${esc(e.name)}</a>`;
    const extra = e.country === "BR" && e.date.startsWith("2026") ? ` <a class="aside" href="brazil-2026.html">Write-up</a>` : "";
    return `<li class="election">
      <p class="when"><span class="status ${esc(e.status)}">${esc(STATUS[e.status] ?? e.status)}</span> ${esc(fmtDate(e.date))}</p>
      <p class="what">${name} ${evidenceLabel(e.evidence)}${extra}</p>
      <p class="summary">${esc(e.summary)}</p>
    </li>`;
  }).join("");
}

async function load() {
  try {
    render(await getJSON("elections.json"));
  } catch (err) {
    list.innerHTML = `<li class="note">Could not load the list of elections (${esc(err.message)}).</li>`;
  }
}

if (LIVE) {
  const note = document.getElementById("live-note");
  note.hidden = false;
  note.textContent = `Live data from ${LIVE}, refreshed every 15 seconds.`;
  setInterval(load, 15000);
}
load();
