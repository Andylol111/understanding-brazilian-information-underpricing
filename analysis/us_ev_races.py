"""Early vote against the model, race by race: site/data/ev2026/ev_races.json for the election-night race panel and turnout.html.

  python analysis/us_ev_races.py            # reads the repo's published files only (no network, no voter-level data)

Display only: unvalidated context, not used by any model. Party figures are registration-based (the party a voter is
registered with, not how the ballot was cast).

Per statewide Senate / governor race (and per House district with county early-vote rows): banked ballots and their
registration party mix with the change against 2022 at the same stage, the model's pre-count view (registered model share
and P(D), poll average, market price), and the EV-implied D share:

    offset      = 2022 certified D two-party share  -  2022 registered-party D share of D+R among banked ballots
                  at the same stage (same days before Election Day)
    EV-implied  = 2026 registered-party D share of D+R among banked ballots  +  offset

compared with the model's pre-count share: 'agrees' within AGREE_PT points, else 'EV leans D/R by X'. The method failed the
project's registered test of the early-vote gap (docs/us_ev_gap.md): right on direction 61 %, RMSE 2.59 -> 3.20 over 24
races. A race with no 2022 same-stage party figure gets no EV-implied value (reason carried in the file).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "data"
OUT = SITE / "ev2026" / "ev_races.json"
ELECTION_DAY = date(2026, 11, 3)
ELECTION_DAY_2022 = date(2022, 11, 8)
AGREE_PT = 2.0                       # |EV-implied - model| within this: 'agrees'
MATCH_TOL_DAYS = 0                   # 2022 stage must be the same number of days before Election Day

LABEL = "Unvalidated context, not used by any model. Registration-based: the party a voter is registered with, not how the ballot was cast."
TEST = {
    "text": "This method failed the project's registered test (right on direction 61 %, made estimates worse): the registered early-vote-gap "
            "test, 24 races, direction 14/23 = 61 %, RMSE 2.59 -> 3.20 (docs/us_ev_gap.md). That test used a state's own earlier cycles; the "
            "offset used here is a simpler cousin and was not itself registered.",
    "direction_pct": 61, "rmse_none": 2.59, "rmse_adjusted": 3.20, "races": 24, "doc": "docs/us_ev_gap.md",
}

# 2022 final pre-election party mix of banked ballots where no open daily file exists: secondary (news / tracker / academic),
# /Users/andreh./brunder-research/notes/EVfound.md section 2 (data/ev_found.json), reports dated 2022-11-07 = one day before Election Day.
FOUND_2022 = {
    "IA": {"days_before": 1, "D": 51.4, "R": 32.7, "O": 15.9, "unit": "pct", "src": "McDonald / US Elections Project 2022-11-07 report (secondary)"},
    "ME": {"days_before": 1, "D": 110071, "R": 53876, "O": 58485, "unit": "n", "src": "McDonald / US Elections Project 2022-11-07 report (secondary)"},
    "KS": {"days_before": 1, "D": 38.2, "R": 49.1, "O": 12.2, "unit": "pct", "src": "IKE Lab, 2022-11-07 report (secondary)"},
}


def ev_dshare(d: float | None, r: float | None) -> float | None:
    """Registered D share of D+R, in points."""
    if d is None or r is None or d + r <= 0:
        return None
    return 100.0 * d / (d + r)


def mix_pct(d: float, r: float, o: float) -> dict[str, float] | None:
    tot = d + r + o
    if tot <= 0:
        return None
    return {"D": 100.0 * d / tot, "R": 100.0 * r / tot, "O": 100.0 * o / tot}


def offset(act22: float | None, ev22_dshare: float | None) -> float | None:
    """2022 certified D two-party share minus the 2022 registered-party D share of D+R (points)."""
    if act22 is None or ev22_dshare is None:
        return None
    return act22 - ev22_dshare


def verdict(implied: float | None, model: float | None, agree: float = AGREE_PT) -> dict[str, Any] | None:
    if implied is None or model is None:
        return None
    diff = implied - model
    if abs(diff) <= agree:
        return {"diff": round(diff, 2), "text": "agrees", "side": None}
    side = "D" if diff > 0 else "R"
    return {"diff": round(diff, 2), "text": f"EV leans {side} by {abs(diff):.1f}", "side": side}


def days_before(as_of: str | None) -> int | None:
    if not as_of:
        return None
    try:
        return (ELECTION_DAY - date.fromisoformat(as_of[:10])).days
    except ValueError:
        return None


def match_2022(panel: list[dict[str, Any]], st: str, db: int | None, tol: int = MATCH_TOL_DAYS) -> dict[str, Any] | None:
    """The 2022 'total' row (D, R, O registration counts) of state `st` the same number of days before its Election Day as the 2026
    figure (never a later one: no look-ahead). panel rows: state (lower), year, date, kind, D, R, O."""
    if db is None:
        return None
    want = ELECTION_DAY_2022 - timedelta(days=db)
    best = None
    for r in panel:
        if r["state"] != st.lower() or int(r["year"]) != 2022 or r["kind"] != "total":
            continue
        d = date.fromisoformat(str(r["date"])[:10])
        lag = (want - d).days
        if 0 <= lag <= tol and (best is None or lag < best[0]):
            best = (lag, r, d)
    if best is None:
        return None
    _, r, d = best
    return {"D": float(r["D"]), "R": float(r["R"]), "O": float(r["O"]), "date": d.isoformat(), "src": "official files (project early-vote panel)", "prov": "official"}


def found_2022(st: str, db: int | None) -> dict[str, Any] | None:
    f = FOUND_2022.get(st.upper())
    if f is None or db is None or f["days_before"] != db:
        return None
    return {"D": float(f["D"]), "R": float(f["R"]), "O": float(f["O"]), "date": (ELECTION_DAY_2022 - timedelta(days=db)).isoformat(), "src": f["src"], "prov": "secondary"}


def party_2022(panel: list[dict[str, Any]], st: str, as_of: str | None) -> dict[str, Any] | None:
    db = days_before(as_of)
    row = match_2022(panel, st, db) or found_2022(st, db)
    if row is None:
        return None
    mix = mix_pct(row["D"], row["R"], row["O"])
    return {**row, "d_pct": mix["D"], "r_pct": mix["R"], "o_pct": mix["O"], "d_share_dr": ev_dshare(row["D"], row["R"]), "days_before": db}


def act_2022(misses: list[dict[str, Any]], st: str, office: str) -> tuple[float, str] | None:
    """2022 certified D two-party share (points) of the state's race: the same office, else the other statewide one."""
    pref = ["Sen-G", "Gov-G"] if office == "senate" else ["Gov-G", "Sen-G"]
    by = {r["race"]: float(r["act"]) for r in misses if r["state"] == st.lower() and r["cycle"] == 2022}
    for t in pref:
        v = by.get(f"2022_{t}_{st.upper()}")
        if v is not None:
            return v, ("senate" if t == "Sen-G" else "governor")
    return None


def num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and x == x else None


def state_block(st: str, ts: dict[str, Any], panel: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Early-vote facts of one state from turnout_model.json plus the 2022 comparison at the same stage."""
    banked = (ts.get("banked") or {}).get("value")
    party = next((p for p in ts.get("party") or [] if p.get("kind") == "pre_election_day"), None)
    if banked is None and party is None:
        return None
    exp = (ts.get("expected") or {}).get("votes")
    out: dict[str, Any] = {
        "st": st, "name": ts.get("name"),
        "banked": banked, "banked_basis": (ts.get("banked") or {}).get("basis"),
        "expected": exp, "pct_expected": ts.get("banked_pct_expected"),
        "total_2022": (ts.get("banked") or {}).get("total_2022"), "pct_2022_final": ts.get("banked_pct_2022"),
        "same_point_2022": ts.get("vs_2022_same_point"),
        "party": None, "party_2022": None, "shift": None,
        "composition": None, "order": None, "lean": (ts.get("lean") or {}).get("text"),
    }
    comp = ts.get("composition") or {}
    if comp.get("ok"):
        out["composition"] = {k: comp.get(k) for k in ("n", "share_so_far", "share_expected", "diff_pt", "coverage_pct", "kind")}
    order = ts.get("order") or {}
    if order.get("text"):
        out["order"] = {"text": order["text"], "class": order.get("class"), "config": order.get("config")}
    if party:
        d, r, o = float(party["D"] or 0), float(party["R"] or 0), float(party.get("other") or 0)
        mix = mix_pct(d, r, o)
        if mix:
            out["party"] = {"as_of": party.get("as_of"), "n": d + r + o, "D": d, "R": r, "O": o, "d_pct": mix["D"], "r_pct": mix["R"], "o_pct": mix["O"],
                            "d_share_dr": ev_dshare(d, r), "source": party.get("source"), "label": party.get("label")}
            p22 = party_2022(panel, st, party.get("as_of"))
            if p22:
                out["party_2022"] = p22
                out["shift"] = {"d_pt": mix["D"] - p22["d_pct"], "r_pt": mix["R"] - p22["r_pct"], "d_share_dr_pt": out["party"]["d_share_dr"] - p22["d_share_dr"]}
    return out


def race_view(r: dict[str, Any]) -> dict[str, Any]:
    """Model figures of a race. Two registered-model versions: the headline pre-count (county prior from past results only, no polls:
    share / P) and the poll-informed one the election page shows (the race's poll_model version, v3 for most races; v2 when none is named).
    model_share / model_p are the figures the EV-implied value is compared with: the poll-informed share, else (compared = 'past') the no-poll one."""
    pc, est, pol = r.get("precount") or {}, r.get("estimate") or {}, r.get("polls") or {}
    past, past_p = (num(pc.get("share")), num(pc.get("P"))) if pc else (None, None)
    ver = pc.get("poll_model") or "v2" if pc else None
    polled, polled_p = (num(pc.get(f"share_{ver}")), num(pc.get(f"P_{ver}"))) if pc else (None, None)
    if pc and polled is None and ver != "v2":
        ver, polled, polled_p = "v2", num(pc.get("share_v2")), num(pc.get("P_v2"))
    share, P, basis, compared = polled, polled_p, f"registered model with polls ({ver})" if polled is not None else None, "polls"
    if share is None and past is not None:
        share, P, basis, compared = past, past_p, "registered model, past results only (no polls)", "past"
    if share is None and est:
        share, P, basis, compared = num(est.get("share")), num(est.get("P")), f"poll-based estimate ({est.get('basis') or 'polls'})", "polls"
    pm, kx = (r.get("pm") or {}).get("price"), (r.get("kalshi") or {}).get("price")
    pct = lambda v: None if v is None else 100.0 * v  # noqa: E731
    return {
        "model_share": pct(share), "model_p": P, "model_basis": basis, "compared": compared,
        "past_share": pct(past), "past_p": past_p, "polled_share": pct(polled), "polled_p": polled_p, "polled_version": ver if polled is not None else None,
        "polls_avg": None if num(pol.get("avg")) is None else 100.0 * pol["avg"], "polls_p": num(pol.get("p_win")), "polls_n": pol.get("n_polls"),
        "pm": num(pm), "kalshi": num(kx),
    }


def implied_block(st: str, office: str, race: dict[str, Any], blk: dict[str, Any] | None, misses: list[dict[str, Any]], view: dict[str, Any]) -> dict[str, Any]:
    reason = None
    if blk is None or blk.get("party") is None:
        reason = "no early-vote party figure for this state"
    elif blk.get("party_2022") is None:
        reason = "no 2022 party figure at the same stage for this state"
    a = act_2022(misses, st, office)
    if reason is None and a is None:
        reason = "no 2022 certified two-party result for this office"
    if reason:
        return {"ok": False, "why": reason}
    act, a_office = a
    ev22 = blk["party_2022"]["d_share_dr"]
    off = offset(act, ev22)
    ev26 = blk["party"]["d_share_dr"]
    imp = ev26 + off
    out = {"ok": True, "ev26_d_share_dr": ev26, "ev22_d_share_dr": ev22, "act22": act, "act22_office": a_office, "offset": off, "implied": imp,
           "vs_model": verdict(imp, view["model_share"]), "vs_polls": None}
    if view["polls_avg"] is not None:
        out["vs_polls"] = verdict(imp, view["polls_avg"])
    if view["compared"] == "past":
        out["note"] = "no poll-informed registered model for this race: compared with the past-results-only share"
    d_party = (race.get("dem_side") or {}).get("party")
    if d_party and d_party != "D":
        out["note"] = (out.get("note", "") + f" the D-side candidate is {d_party}: registered D share is a rougher guide").strip()
    if a_office != office:
        out["note"] = (out.get("note", "") + f" 2022 {a_office} result used (no 2022 {office} race)").strip()
    return out


def house_rows(house_ev: dict[str, Any], house_mk: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    mk = house_mk.get("districts", {})
    for did, d in sorted((house_ev.get("districts") or {}).items()):
        bp = d.get("by_party") or {}
        mix = mix_pct(float(bp.get("D") or 0), float(bp.get("R") or 0), float(bp.get("O") or 0))
        m = mk.get(did) or {}
        base = m.get("baseline") or {}
        pavg = ((m.get("polls") or {}).get("average") or {}) if isinstance(m.get("polls"), dict) else {}
        out.append({
            "race_id": f"house:{did}", "id": did, "state": did.split("-")[0], "office": "house", "name": f"{did} (House)",
            "ev": {"banked": d.get("ev_total"), "baseline_2022": d.get("baseline_2022"), "share_of_2022_total": d.get("share_of_2022_total"), "approx": d.get("approx"),
                   "counties": d.get("counties"), "party": None if mix is None else {"d_pct": mix["D"], "r_pct": mix["R"], "o_pct": mix["O"], "d_share_dr": ev_dshare(bp.get("D"), bp.get("R"))},
                   "as_of": (house_ev.get("as_of") or {}).get(did.split("-")[0].lower())},
            "model": {"model_share": None if num(base.get("share")) is None else 100.0 * base["share"], "model_p": num(base.get("p_d")),
                      "model_basis": "baseline: 2024 presidential result on 2026 lines (display only)",
                      "polls_avg": None if num(pavg.get("avg")) is None else 100.0 * pavg["avg"], "polls_p": num(pavg.get("p_win")),
                      "pm": num((m.get("pm") or {}).get("p_d")), "kalshi": num((m.get("kalshi") or {}).get("p_d")) if m.get("kalshi") else None},
            "implied": {"ok": False, "why": "no 2022 party mix by district at the same stage"},
        })
    return out


def build(overview: dict[str, Any], tmodel: dict[str, Any], panel: list[dict[str, Any]], misses: list[dict[str, Any]],
          house_ev: dict[str, Any] | None = None, house_mk: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    states: dict[str, Any] = {}
    for st, ts in (tmodel.get("states") or {}).items():
        b = state_block(st, ts, panel)
        if b:
            states[st] = b
    races = []
    for office in ("senate", "governor"):
        for r in overview["offices"][office]["races"]:
            st = r["state"].upper()
            blk = states.get(st)
            view = race_view(r)
            races.append({
                "race_id": r.get("race_id") or r["race_key"], "key": r["race_key"], "state": st, "office": office,
                "name": f"{r['state_name']} {office}", "modeled": bool(r.get("modeled")),
                "ev_state": st if blk else None, "model": view,
                "implied": implied_block(st, office, r, blk, misses, view),
            })
    with_est = sorted({x["state"] for x in races if x["implied"]["ok"]})
    out = {
        "label": LABEL, "test": TEST, "agree_pt": AGREE_PT,
        "generated_utc": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {"overview_as_of": overview.get("as_of"), "turnout_model_generated_utc": tmodel.get("generated_utc")},
        "method": {"offset": "2022 certified D two-party share minus the 2022 registered-party D share of D+R among banked ballots at the same stage (same days before Election Day)",
                   "implied": "2026 registered-party D share of D+R among banked ballots plus that offset",
                   "agrees": f"within {AGREE_PT:g} points of the model's pre-count share",
                   "partial_day": "Banked ballots are counted through the party file's date; county composition is partial-day, mix of methods differs by state, and the banked electorate drifts toward R as Election Day nears"},
        "n_states_with_estimate": len(with_est), "states_with_estimate": with_est,
        "states": states, "races": races,
    }
    if house_ev:
        out["house"] = house_rows(house_ev, house_mk or {})
        out["house_note"] = "House districts of the states with county early-vote rows; no 2022 district party mix, so no EV-implied figure"
    return out


def rounded(x: Any) -> Any:
    """Floats to 3 decimals (the file is a display snapshot)."""
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def _load(p: Path) -> Any:
    return json.loads(p.read_text())


def _csv(p: Path) -> list[dict[str, Any]]:
    import csv
    with p.open() as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("D", "R", "O", "year", "cycle"):
            if k in r and r[k] != "":
                r[k] = float(r[k]) if k in ("D", "R", "O") else int(r[k])
    return rows


def main() -> int:
    ov = _load(SITE / "us2026" / "overview.json")
    tm = _load(SITE / "ev2026" / "turnout_model.json")
    panel = _csv(ROOT / "data_public" / "us_ev_party_panel.csv")
    misses = [r for r in _csv(ROOT / "data_public" / "us_ev_party_misses.csv")]
    hv = SITE / "us2026" / "house"
    house_ev = _load(hv / "earlyvote.json") if (hv / "earlyvote.json").exists() else None
    house_mk = _load(hv / "markets.json") if (hv / "markets.json").exists() else None
    out = build(ov, tm, panel, misses, house_ev, house_mk)
    OUT.write_text(json.dumps(rounded(out), separators=(",", ":"), allow_nan=False))
    est = [(x["name"], x["implied"]["implied"], x["model"]["model_share"], (x["implied"]["vs_model"] or {}).get("text")) for x in out["races"] if x["implied"]["ok"]]
    print(f"{OUT.relative_to(ROOT)}: {len(out['races'])} races, {len(out['house'] or [])} districts, {out['n_states_with_estimate']} states with an EV-implied value {out['states_with_estimate']}")
    for e in est:
        print(f"  {e[0]}: implied {e[1]:.1f} vs model {e[2]:.1f} ({e[3]})" if e[2] is not None else f"  {e[0]}: implied {e[1]:.1f}, no model share")
    return 0


if __name__ == "__main__":
    sys.exit(main())
