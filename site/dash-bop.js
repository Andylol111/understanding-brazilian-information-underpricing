// Balance-of-power seat bars shared by the night guide (guide.js) and the national view (dash-national.js):
// pure markup from the figures guide-rules.js balanceOfPower() returns.
import { esc } from "./common.js";
import { marginText } from "./guide-rules.js";

const dash = "–";
export const sidePct = (p) => (p == null ? dash : `${Math.round(100 * p)}%`);
function seatBar(c, r) {
  const t = c.total, w = (x) => `${(100 * Math.max(0, x) / t).toFixed(2)}%`;
  const dVar = Math.max(0, r.dSeats - c.notUp.D), rVar = Math.max(0, t - c.notUp.D - c.notUp.R - dVar);
  const seg = (cls, x, label) => (x > 0 ? `<i class="${cls}" style="width:${w(x)}"${label ? ` title="${esc(label)}"` : ""}></i>` : "");
  return `<span class="bop-bar" role="img" aria-label="${esc(`Democratic ${r.dSeats.toFixed(1)} of ${t} seats, majority ${c.majority}`)}">` +
    seg("d0", c.notUp.D, `${c.notUp.D} Democratic seats not up`) + seg("d1", dVar) + seg("r1", rVar) + seg("r0", c.notUp.R, `${c.notUp.R} Republican seats not up`) +
    `<b class="bop-maj" style="left:${w(c.majority - 0.5)}" title="majority: ${c.majority}"></b></span>`;
}
function marketBar(m) {
  const pm = m.polymarket ?? m.kalshi;
  if (pm == null) return `<span class="bop-bar bop-empty"></span>`;
  const k = m.kalshi;
  return `<span class="bop-bar" role="img" aria-label="Democratic control ${sidePct(pm)}"><i class="d1" style="width:${(100 * pm).toFixed(1)}%"></i><i class="r1" style="width:${(100 * (1 - pm)).toFixed(1)}%"></i>` +
    `<b class="bop-maj" style="left:50%"></b>${k != null ? `<b class="bop-k" style="left:${(100 * k).toFixed(1)}%" title="Kalshi ${sidePct(k)}"></b>` : ""}</span>`;
}
function bopRow(c, label, r, tip, strong) {
  const mg = marginText(r.dSeats, c.majority);
  return `<div class="bop-row${strong ? " bop-x" : ""}"><span class="bop-l" title="${esc(tip)}">${label}</span>${seatBar(c, r)}` +
    `<span class="bop-n"><b class="${mg?.startsWith("D") ? "bop-dm" : "bop-rm"}">${mg ?? dash}</b> <span>${r.dSeats.toFixed(1)} D</span></span><span class="bop-p">${r.p == null ? dash : `${sidePct(r.p)} D control${r.indep ? `<small title="Each race decided on its own, no shared polling error: the page's pre-count figure allows one">if races are independent</small>` : ""}`}</span></div>`;
}
export function bopCard(name, c, chamber) {
  if (!c) return "";
  const m = c.markets, pmT = `Polymarket ${sidePct(m.polymarket)}`, kT = `Kalshi ${sidePct(m.kalshi)}`;
  const polls = c.polls ? bopRow(c, "Polls (before votes)", c.polls, chamber === "senate" ? `${c.notUp.D} Democratic-caucus seats not up + each race's poll chance` : "Each district's pre-count chance: 2024 president on the 2026 lines plus the generic-ballot swing") : "";
  const live = c.live ? bopRow(c, "Live model", c.live, chamber === "senate" ? "The live model's chance in each race with a county model, the market price in the others" : `${c.nModelled} modelled districts at the live model's chance, the others at their pre-count chance`) : "";
  const nat = c.national ? bopRow(c, "Experimental national model", c.national, "The national swing model's mean seat count (experimental, not registered)", true) : "";
  const mk = `<div class="bop-row"><span class="bop-l">Markets</span>${marketBar(m)}<span class="bop-n"><span>${pmT}</span></span><span class="bop-p">${m.kalshi != null || m.polymarket != null ? kT : dash}</span></div>`;
  const parts = [polls, live, nat, mk].join("");
  const note = chamber === "senate" ? `${c.notUp.D} Democratic-caucus seats not up (solid), ${c.notUp.R} Republican; majority line at ${c.majority}. `
    : `All ${c.total} seats up; majority line at ${c.majority}.`;
  return `<div class="bop-card"><h3>${name}</h3>${parts}<p class="gd-note bop-note">${note}</p></div>`;
}
