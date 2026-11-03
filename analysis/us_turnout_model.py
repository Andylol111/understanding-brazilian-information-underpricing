"""Turnout dashboard data: site/data/ev2026/turnout_model.json, built from the published turnout_day.json plus files already in the repo.

Turnout only; every derived figure is "unvalidated context, not used by any model". Nothing here is read by a registered model.

Per state it adds
  - banked ballots (one rule, see bank()), split by method, and the share of the model's expected final votes;
  - the expected final votes the registered live models use: the units files under site/data/us2026/units (V0, expected two-party votes
    per county; the mean over the state's races), else EST_TURNOUT (0.72, brunder.us.EST_TURNOUT) x 2024 president two-party votes
    (data_public/us2026_county_baseline.csv; the state total from data_public/us2026_state_pres24.csv);
  - composition shift: the statewide Democratic two-party share if every county voted like its 2024 president two-party share (moved by
    the state's poll-implied swing from overview.json where there is one), weighted by banked ballots minus weighted by expected votes,
    over the counties that have a count, with a bootstrap over those counties; only with >= MIN_COUNTIES counties;
  - the count order each state's registered config states (first sentence of the [count] comment), and the registration-based party mix of
    banked ballots where a file publishes it;
  - a history of banked totals across runs (read back from the previous turnout_model.json).

  uv run python analysis/us_turnout_model.py [--root REPO] [--day turnout_day.json] [--src research turnout_day.json] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import us_turnout_publish as tp  # noqa: E402  (STATES, clean_row: the same drop rules, incl. no Redistricting Data Hub rows)

ROOT = HERE.parent
EST_TURNOUT = 0.72            # brunder.us.EST_TURNOUT (a test pins it to the source)
MIN_COUNTIES = 10
N_BOOT = 2000
SEED = 2026
HISTORY_MAX = 200
LABEL = "Unvalidated context, not used by any model. Turnout only: who voted, not how."
COUNTY_WORDS = re.compile(r"\s+(county|parish|borough|census area|municipality)$", re.I)
SOURCE_FIELDS = ("kind", "level", "count", "measure", "jurisdiction", "as_of", "as_of_utc", "as_of_date", "prov", "source_url", "pct_registered", "total_2022", "total_2024",
                 "same_time_2022", "same_time_2022_ratio", "note", "fetched_utc")
PRIORITY = ("total", "advance", "mail_returned", "early_in_person", "election_day")   # which kind a county or state is summarised by
PROV_RANK = {"official": 4, "derived": 3, "official_post": 3, "manual": 2, "secondary": 1}
# count-order class from the registered configs' own [count] comments (a state without a config comment is unknown)
ORDER_CLASS = {"GA": "early_first", "NC": "early_first", "TX": "early_first", "FL": "early_first", "PA": "early_first",
               "CA": "late", "AZ": "late", "MN": "with_precincts", "NE": "with_precincts", "NM": "with_precincts", "SC": "with_precincts",
               "VT": "with_precincts"}
LEAN_MIN = 2.0                # points of D share of (D+R) away from 50 before the wording says "lean"


def num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def county_key(name: str, st: str) -> str:
    """Comparable county name: no parenthesis, no trailing state code, no county/parish word, letters and digits only."""
    s = re.sub(r"\(.*?\)", " ", name or "").strip()
    s = re.sub(rf"[ ,]+{st}$", "", s, flags=re.I)
    s = COUNTY_WORDS.sub("", s.strip(" ,"))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


# ------------------------------------------------------------------------------------------------------------------ inputs
def load_baseline(root: Path) -> dict[str, list[dict[str, Any]]]:
    """state abbreviation -> counties with 2024 president Harris / Trump votes (data_public/us2026_county_baseline.csv)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in read_csv(root / "data_public" / "us2026_county_baseline.csv"):
        st = r["state"].upper()
        d, rep = num(float(r["pres24_dem"])), num(float(r["pres24_rep"]))
        if not d and not rep:
            continue
        out.setdefault(st, []).append({"unit_id": r["unit_id"], "fips": r["unit_id"].split("-")[-1], "name": r["name"], "key": county_key(r["name"], st),
                                       "dem": d, "rep": rep, "two": d + rep, "share": d / (d + rep)})
    return out


def load_state_two(root: Path) -> dict[str, float]:
    return {r["state"].upper(): float(r["dem"]) + float(r["rep"]) for r in read_csv(root / "data_public" / "us2026_state_pres24.csv")}


def load_units(root: Path) -> dict[str, dict[str, Any]]:
    """state -> {"races": [race_id], "v0": {unit_id: mean V0 over the state's races}} from the registered units files."""
    by: dict[str, dict[str, Any]] = {}
    for f in sorted(glob.glob(str(root / "site" / "data" / "us2026" / "units" / "*.json"))):
        d = json.loads(Path(f).read_text())
        ids, v0 = d.get("unit_ids") or [], d.get("V0") or []
        if not ids or len(ids) != len(v0):
            continue
        st = ids[0].split("-")[1].upper()
        s = by.setdefault(st, {"races": [], "vals": {}})
        s["races"].append(d.get("race_id") or Path(f).stem)
        for u, v in zip(ids, v0):
            if num(v) is not None:
                s["vals"].setdefault(u, []).append(float(v))
    return {st: {"races": s["races"], "v0": {u: sum(v) / len(v) for u, v in s["vals"].items()}} for st, s in by.items()}


def load_polls(root: Path) -> dict[str, list[dict[str, Any]]]:
    """state -> [{race, avg}] two-party Democratic-side poll averages (site/data/us2026/overview.json, senate and governor)."""
    p = root / "site" / "data" / "us2026" / "overview.json"
    out: dict[str, list[dict[str, Any]]] = {}
    if not p.exists():
        return out
    for office, o in (json.loads(p.read_text()).get("offices") or {}).items():
        for r in o.get("races") or []:
            avg = num((r.get("polls") or {}).get("avg"))
            if avg is not None and r.get("state"):
                out.setdefault(r["state"].upper(), []).append({"race": r.get("race_key") or office, "avg": avg})
    return out


def count_orders(root: Path) -> dict[str, dict[str, str]]:
    """state -> count-order sentence: the first clause of the [count] comment of the newest registered config of the state."""
    texts: dict[str, tuple[int, str, str]] = {}
    refs: dict[str, str] = {}
    for f in sorted(glob.glob(str(root / "configs" / "us2026_*_v*.toml"))):
        m = re.match(r"us2026_([a-z]{2})_(gov|sen)_v(\d+)\.toml", Path(f).name)
        if not m:
            continue
        lines = Path(f).read_text().split("\n")
        i = next((k for k, ln in enumerate(lines) if ln.startswith("[count]")), None)
        if i is None:
            continue
        com = []
        for ln in lines[i + 1:]:
            if not ln.startswith("#"):
                break
            com.append(ln[1:].strip())
        t = " ".join(com)
        st, race, ver = m[1].upper(), f"{m[1]}-{m[2]}", int(m[3])
        ref = re.match(r"As ([a-z]{2}-(?:gov|sen))\.", t)
        if ref:
            refs[st] = ref[1]
            continue
        if t and (st not in texts or ver >= texts[st][0]):
            texts[st] = (ver, t, Path(f).name)
    out = {}
    for st, (_, t, fname) in texts.items():
        t = re.sub(r"\s*\([^)]*\)", "", t)
        t = re.split(r":\s|;\s|\.\s+(?=[A-Z])|,\s+so\s|,\s+the same order", t)[0].strip().rstrip(".")
        out[st] = {"text": t[:260], "config": fname}
    return out


def party_from_ev(root: Path, st: str) -> dict[str, Any] | None:
    """Latest registration-based party split of returned ballots from data/ev2026/state_<st>.json (where the state publishes it)."""
    p = root / "site" / "data" / "ev2026" / f"state_{st.lower()}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    s = d.get("series") or {}
    if not d.get("has_party"):
        return None
    for i in range(len(s.get("date") or []) - 1, -1, -1):
        D, R = num((s.get("D") or [None] * (i + 1))[i]), num((s.get("R") or [None] * (i + 1))[i])
        if D is not None and R is not None:
            U, O = num((s.get("U") or [None] * (i + 1))[i]), num((s.get("other") or [None] * (i + 1))[i])
            return {"source": "early-vote files", "label": d.get("basis") or "returned ballots", "as_of": s["date"][i], "kind": "pre_election_day",
                    "D": D, "R": R, "other": (U or 0) + (O or 0) if (U is not None or O is not None) else None}
    return None


# ------------------------------------------------------------------------------------------------------------------ banking
def pick(rows: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    rs = [r for r in rows if r.get("kind") == kind and num(r.get("count")) is not None and r["count"] >= 0]
    return max(rs, key=lambda r: (PROV_RANK.get(r.get("prov", ""), 0), r.get("as_of_utc") or r.get("as_of_date") or "", r["count"]), default=None)


def is_complete(r: dict[str, Any], n_counties: int) -> bool:
    """A state / manual row is a whole-state count; a state-partial row is complete only when it sums nearly every county."""
    if r["level"] in ("state", "manual"):
        return True
    m = re.search(r"sum of (\d+) counties", r.get("jurisdiction") or "")
    return bool(m and n_counties and int(m[1]) >= 0.9 * n_counties)


def bank(rows: list[dict[str, Any]], n_counties: int) -> dict[str, Any] | None:
    """Banked ballots of a state from its statewide rows. Kinds are never mixed except where they are disjoint:
    advance (all pre-Election Day) + Election Day check-ins; else a total of all methods; else mail returned + early in person
    (+ Election Day). Rows that only sum some counties make the figure partial (a lower bound, not a statewide count)."""
    rows = [r for r in rows if r["kind"] in PRIORITY]
    ed = pick(rows, "election_day")
    adv, tot, mail, early = pick(rows, "advance"), pick(rows, "total"), pick(rows, "mail_returned"), pick(rows, "early_in_person")
    parts: list[tuple[str, dict[str, Any]]]
    whole = lambda r: r is not None and is_complete(r, n_counties)  # noqa: E731
    ed_all = ed if whole(ed) else None            # Election Day / method rows that only sum some counties are listed, never added
    if adv and whole(adv):
        parts = [("advance", adv)] + ([("election_day", ed_all)] if ed_all else [])
        basis = "pre-Election Day ballots (early + mail)" + (" plus Election Day check-ins" if ed_all else ", no statewide Election Day check-ins")
    elif tot and whole(tot):
        parts = [("total", tot)]
        basis = "total of all methods as published"
    elif whole(mail) or whole(early):
        parts = [(k, r) for k, r in (("mail_returned", mail), ("early_in_person", early), ("election_day", ed_all)) if r and whole(r)]
        basis = " plus ".join({"mail_returned": "mail/absentee returned", "early_in_person": "early in person", "election_day": "Election Day check-ins"}[k] for k, _ in parts)
    elif whole(ed):
        parts, basis = [("election_day", ed)], "Election Day check-ins only"
    elif adv or tot or mail or early or ed:
        r = next(x for x in (adv, tot, mail, early, ed) if x)         # only partial sums exist: a lower bound, never a statewide count
        parts, basis = [(r["kind"], r)], "sum of some counties only (lower bound)"
    else:
        return None
    partial = [{"kind": r["kind"], "count": r["count"], "measure": r.get("measure"), "jurisdiction": r.get("jurisdiction")} for r in (adv, tot, mail, early, ed)
               if r and not whole(r) and r["kind"] not in [k for k, _ in parts]]
    value = sum(r["count"] for _, r in parts)
    complete = all(is_complete(r, n_counties) for _, r in parts)
    only_ed = [k for k, _ in parts] == ["election_day"]
    covers = [k for k, _ in parts]
    # "all methods" means the figure can be compared with a whole-election total
    all_methods = bool(complete and ("total" in covers or ("advance" in covers and "election_day" in covers) or (("mail_returned" in covers or "early_in_person" in covers) and "election_day" in covers)))
    ref = next((r for _, r in parts if num(r.get("total_2024")) or num(r.get("total_2022"))), None)
    t22 = num(ref.get("total_2022")) if ref else None
    t24 = num(ref.get("total_2024")) if ref else None
    return {
        "value": value, "basis": basis, "partial_rows": partial, "complete": complete, "all_methods": all_methods, "only_election_day": only_ed,
        "parts": [{"kind": k, "count": r["count"], "as_of": r.get("as_of_utc") or r.get("as_of_date") or r.get("fetched_utc"), "prov": r.get("prov"), "measure": r.get("measure"),
                   "level": r["level"], "source_url": r.get("source_url")} for k, r in parts],
        "total_2022": t22 if t22 else None, "total_2024": t24 if t24 else None,
        "same_time_2022": next((r["same_time_2022"] for _, r in parts if num(r.get("same_time_2022"))), None),
        "same_time_2022_ratio": next((r["same_time_2022_ratio"] for _, r in parts if num(r.get("same_time_2022_ratio"))), None),
    }


# ------------------------------------------------------------------------------------------------------------------ composition
def composition(cs: list[dict[str, float]], swing: float | None = None, min_n: int = MIN_COUNTIES, n_boot: int = N_BOOT, seed: int = SEED) -> dict[str, Any]:
    """cs: counties with share (2024 D two-party, 0..1), banked, exp. D share as if every county voted like its baseline (+ swing),
    weighted by banked ballots vs by expected votes, over the same counties; bootstrap of the difference over counties."""
    cs = [c for c in cs if c["banked"] > 0 and c["exp"] > 0]
    if len(cs) < min_n:
        return {"ok": False, "n": len(cs), "why": f"{len(cs)} counties with a count; at least {min_n} are needed"}
    s = np.clip(np.array([c["share"] for c in cs]) + (swing or 0.0), 0.0, 1.0)
    b, e = np.array([c["banked"] for c in cs], float), np.array([c["exp"] for c in cs], float)
    so_far, exp = float((b * s).sum() / b.sum()), float((e * s).sum() / e.sum())
    idx = np.random.default_rng(seed).integers(0, len(cs), (n_boot, len(cs)))
    bs, es, ss = b[idx], e[idx], s[idx]
    diff = (bs * ss).sum(1) / bs.sum(1) - (es * ss).sum(1) / es.sum(1)
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return {"ok": True, "n": len(cs), "share_so_far": round(so_far * 100, 2), "share_expected": round(exp * 100, 2), "diff_pt": round((so_far - exp) * 100, 2),
            "lo": round(float(lo) * 100, 2), "hi": round(float(hi) * 100, 2), "boot": n_boot, "swing_pt": None if swing is None else round(swing * 100, 2)}


def lean_text(party: dict[str, Any] | None, order_class: str | None) -> dict[str, Any] | None:
    """First-returns wording, only where a party mix of pre-Election Day ballots exists AND the state counts them first."""
    if not party or party.get("kind") == "election_day" or not party.get("D") or not party.get("R"):
        return None
    d_share = 100 * party["D"] / (party["D"] + party["R"])
    out: dict[str, Any] = {"d_share_of_dr": round(d_share, 1)}
    if order_class != "early_first":
        out["text"] = None
        return out
    if abs(d_share - 50) < LEAN_MIN:
        out["text"] = "close to even among registered Democrats and Republicans, so the first returns would not be expected to lean either way on this alone"
    else:
        side = "Democratic" if d_share > 50 else "Republican"
        out["text"] = f"{side}-leaning by registration, so the first returns, if they are these ballots, would likely lean {'D' if d_share > 50 else 'R'}"
    return out


# ------------------------------------------------------------------------------------------------------------------ build
def parse_ts(s: str | None) -> float:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def build(day: dict[str, Any], root: Path, party_rows: dict[str, list[dict[str, Any]]] | None = None, prev: dict[str, Any] | None = None) -> dict[str, Any]:
    base, state_two, units, polls, orders = load_baseline(root), load_state_two(root), load_units(root), load_polls(root), count_orders(root)
    party_rows = party_rows or {}
    out_states: dict[str, Any] = {}
    kinds_seen: dict[str, set[str]] = {k: set() for k in ("election_day", "early_in_person", "mail_returned", "advance", "total")}
    history = {st: list(v) for st, v in ((prev or {}).get("history") or {}).items()}
    gen = day.get("generated_utc")
    for s in day["states"]:
        st = s["st"]
        cs = base.get(st, [])
        by_key = {c["key"]: c for c in cs}
        # rows named like a county of the state are county rows even where the file labels them "state"
        statewide, county_rows = [], []
        for r in s["rows"] + s["counties"]:
            hit = by_key.get(county_key(r["jurisdiction"], st))
            if hit and r["level"] != "state-partial" and "statewide" not in r["jurisdiction"].lower():
                county_rows.append((hit, r))
            else:
                statewide.append(r)
        for r in statewide + [r for _, r in county_rows]:
            if r["kind"] in kinds_seen:
                kinds_seen[r["kind"]].add(st)
        # expected votes
        u = units.get(st)
        if u:
            exp_by = {c["unit_id"]: u["v0"][c["unit_id"]] for c in cs if c["unit_id"] in u["v0"]}
            exp_total = sum(u["v0"].values())
            basis = f"registered live models, county expected two-party votes (units files: {', '.join(sorted(u['races']))}{'; mean of the races' if len(u['races']) > 1 else ''})"
            src = "units"
        else:
            exp_by = {c["unit_id"]: EST_TURNOUT * c["two"] for c in cs}
            two = state_two.get(st) or sum(c["two"] for c in cs)
            exp_total = EST_TURNOUT * two if two else 0
            basis = f"no registered statewide model: {EST_TURNOUT} x 2024 president two-party votes (brunder.us.EST_TURNOUT)"
            src = "est"
        exp_total = exp_total or None
        b = bank(statewide, len(cs))
        by_method = {}
        for k in PRIORITY:
            r = pick(statewide, k)
            if r:
                by_method[k] = {"count": r["count"], "level": r["level"], "partial": not is_complete(r, len(cs)), "as_of": r.get("as_of_utc") or r.get("as_of_date") or r.get("fetched_utc"),
                                "jurisdiction": r.get("jurisdiction")}
        sources = [{k: r[k] for k in SOURCE_FIELDS if r.get(k) is not None} for r in sorted(statewide, key=lambda r: (r["kind"], -(num(r.get("count")) or 0)))]
        # counties: the one kind that covers the most counties (ties by PRIORITY)
        by_kind: dict[str, dict[str, tuple[dict, dict]]] = {}
        for hit, r in county_rows:
            if r["kind"] not in PRIORITY or num(r.get("count")) is None:
                continue
            cur = by_kind.setdefault(r["kind"], {}).get(hit["unit_id"])
            if cur is None or (r.get("as_of_utc") or "") >= (cur[1].get("as_of_utc") or ""):
                by_kind[r["kind"]][hit["unit_id"]] = (hit, r)
        ck = max(by_kind, key=lambda k: (len(by_kind[k]), -PRIORITY.index(k))) if by_kind else None
        counties = []
        if ck:
            for hit, r in by_kind[ck].values():
                counties.append({"fips": hit["fips"], "name": re.sub(rf",\s*{st}$", "", hit["name"]), "share": round(hit["share"], 4), "exp": round(exp_by.get(hit["unit_id"], EST_TURNOUT * hit["two"])),
                                 "banked": r["count"], "as_of": r.get("as_of_utc") or r.get("as_of_date"), "prov": r.get("prov")})
            counties.sort(key=lambda c: -c["exp"])
        # swing
        swing = None
        pl = polls.get(st)
        if pl and cs and exp_by:
            tot = sum(exp_by.get(c["unit_id"], 0) for c in cs)
            if tot > 0:
                state_base = sum(exp_by.get(c["unit_id"], 0) * c["share"] for c in cs) / tot
                swing = sum(p["avg"] for p in pl) / len(pl) - state_base
        comp = composition([{"share": c["share"], "banked": c["banked"], "exp": c["exp"]} for c in counties], swing) if counties else {"ok": False, "n": 0, "why": "no county counts"}
        if comp["ok"]:
            comp["kind"] = ck
            comp["coverage_pct"] = round(100 * sum(c["exp"] for c in counties) / exp_total, 1) if exp_total else None
            comp["swing_races"] = [p["race"] for p in (pl or [])] if swing is not None else []
            tot_all = sum(exp_by.values())
            if tot_all:
                comp["share_all_expected"] = round(100 * float(np.clip(np.array([c["share"] for c in cs]) + (swing or 0), 0, 1) @ np.array([exp_by.get(c["unit_id"], 0) for c in cs]) / tot_all), 2)
        # party
        parties = []
        evp = party_from_ev(root, st)
        if evp:
            parties.append(evp)
        for r in party_rows.get(st, []):
            parties.append(r)
        main_party = next((p for p in parties if p["kind"] != "election_day"), None)
        oc = ORDER_CLASS.get(st) if st in orders else None
        order = {"text": orders[st]["text"], "config": orders[st]["config"], "class": oc} if st in orders else {"text": None, "class": None}
        # status for the map
        if b and b["complete"] and b["all_methods"]:
            status = "all_methods"
        elif b and b["complete"]:
            status = "partial_methods"
        elif b or counties:
            status = "counties_only"
        else:
            status = s["verdict_class"] if s["verdict_class"] in ("blocked", "excluded") else "none"
        banked_pct = round(100 * b["value"] / exp_total, 1) if b and exp_total and b["complete"] else None
        rec = {"st": st, "name": s["name"], "status": status, "verdict": s["verdict"], "verdict_class": s["verdict_class"], "n_counties": len(cs) or None,
               "expected": {"votes": round(exp_total) if exp_total else None, "source": src, "basis": basis},
               "banked": b, "by_method": by_method, "sources": sources, "banked_pct_expected": banked_pct,
               "banked_pct_2022": round(100 * b["value"] / b["total_2022"], 1) if b and b["total_2022"] and b["complete"] else None,
               "banked_pct_2024": round(100 * b["value"] / b["total_2024"], 1) if b and b["total_2024"] and b["complete"] else None,
               "vs_2022_same_point": round(b["value"] / b["same_time_2022"], 3) if b and b["same_time_2022"] and b["complete"] else (b["same_time_2022_ratio"] if b and b["complete"] else None),
               "county_kind": ck, "n_county_rows": len(counties), "counties": counties, "composition": comp, "order": order, "party": parties,
               "lean": lean_text(main_party, oc) if order["class"] else lean_text(main_party, None),
               "swing_pt": None if swing is None else round(swing * 100, 2)}
        out_states[st] = rec
        if b and gen:
            h = history.setdefault(st, [])
            if not any(x["t"] == gen for x in h):
                h.append({"t": gen, "v": b["value"], "complete": b["complete"]})
            history[st] = sorted(h, key=lambda x: x["t"])[-HISTORY_MAX:]
    n = lambda k: len(kinds_seen[k])  # noqa: E731
    return {
        "label": LABEL, "generated_utc": gen, "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "est_turnout": EST_TURNOUT, "min_counties": MIN_COUNTIES, "bootstrap": {"n": N_BOOT, "seed": SEED},
        "summary": {"states_with_data": sum(1 for r in out_states.values() if r["banked"] or r["counties"]), "election_day": n("election_day"),
                    "early_in_person": n("early_in_person"), "mail": n("mail_returned"), "advance": n("advance"), "total": n("total"),
                    "complete": sum(1 for r in out_states.values() if r["status"] == "all_methods"),
                    "with_expected_pct": sum(1 for r in out_states.values() if r["banked_pct_expected"] is not None),
                    "composition_states": sorted(r["st"] for r in out_states.values() if r["composition"]["ok"])},
        "states": out_states, "history": history,
    }


def party_rows_from_research(src: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Registration-based party counts that the research file carries on some rows (aggregates only); same drop rules as the publisher."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in src.get("rows") or []:
        if not isinstance(r, dict):
            continue
        c = tp.clean_row(r)
        D, R = num(r.get("dem")), num(r.get("rep"))
        if not c or D is None or R is None:
            continue
        other = sum(x for x in (num(r.get("unaffiliated")), num(r.get("other"))) if x is not None)
        out.setdefault(c["state"], []).append({"source": "turnout file", "label": f"{c['jurisdiction']}: {c['measure']}", "as_of": c.get("as_of_utc") or c.get("as_of_date"),
                                               "kind": c["kind"], "D": D, "R": R, "other": other or None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--day", default=None, help="published turnout_day.json (default: <out-dir>/turnout_day.json)")
    ap.add_argument("--src", default=str(tp.RESEARCH / "turnout_day.json"), help="research file, for the party counts only")
    a = ap.parse_args()
    root = Path(a.root)
    out_dir = Path(a.out_dir) if a.out_dir else root / "site" / "data" / "ev2026"
    day_p = Path(a.day) if a.day else out_dir / "turnout_day.json"
    if not day_p.exists():
        print(f"no {day_p}", file=sys.stderr)
        return 1
    day = json.loads(day_p.read_text())
    prev_p = out_dir / "turnout_model.json"
    prev = json.loads(prev_p.read_text()) if prev_p.exists() else None
    src = Path(a.src)
    party = party_rows_from_research(json.loads(src.read_text())) if src.exists() else {}
    out = build(day, root, party, prev)
    changed = tp.write_if_changed(prev_p, out, ignore=("published_utc",))
    s = out["summary"]
    print(f"{s['states_with_data']} states with data, {s['with_expected_pct']} with % of expected, composition for {len(s['composition_states'])}; {'changed' if changed else 'no change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
