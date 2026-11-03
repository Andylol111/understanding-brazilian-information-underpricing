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
import us_turnout_proj as tproj  # noqa: E402  (projection of the final total votes: states.<st>.proj, proj_summary)

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
NO_FIGURE_FRAC = 0.05         # a county count below this share of its expected votes ...
NO_FIGURE_MEDIAN = 0.40       # ... while the state's median county is above this share is a feed gap, not turnout
ALL_MAIL = {"CO", "OR", "WA", "UT", "HI", "NV", "CA", "VT", "DC"}   # states that mail a ballot to every active voter: "requested" there is not a request
COUNTY_FILES = ("NC", "CO", "TN", "WI")   # full county tables from site/data/ev2026/counties_<st>.json (pre-Election Day ballots, all methods)
PARTY_FILES = ("NC", "CO", "FL", "PA")    # counties_<st>.json that carries registration-based party counts of the returned ballots
AGE_MIN_COVERAGE = 90.0       # an age split is shown only where at least this share of the ballots has an age
OUTSTANDING_MAX_GAP_DAYS = 1  # issued and returned must be as of dates at most this far apart
LEAN_MIN = 2.0                # points of D share of (D+R) away from 50 before the wording says "lean"


def num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def county_key(name: str, st: str) -> str:
    """Comparable county name: no parenthesis, no trailing state code, no county/parish word, letters and digits only."""
    s = re.sub(r"\(.*?\)", " ", (name or "").replace("&", " and ")).strip()
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


# ------------------------------------------------------------------------------------------------------------------ extras
def load_extra(path: Path | None) -> dict[str, Any]:
    """The research side's turnout_extra.json (outstanding mail inputs, 2022 final pre-election figures, all-county rows, age); {} if absent."""
    if path and path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            return {}
    return {}


def day_gap(a: str | None, b: str | None) -> int | None:
    """Days between two YYYY-MM-DD dates (None if either is missing or unparsable)."""
    try:
        return abs((datetime.fromisoformat(str(a)[:10]) - datetime.fromisoformat(str(b)[:10])).days)
    except ValueError:
        return None


def outstanding_rule(st: str, issued: float | None, returned: float | None, issued_date: str | None, returned_date: str | None, issued_label: str = "sent") -> dict[str, Any]:
    """Mail ballots not yet returned = ballots sent (else requested) minus ballots returned, both from the same state source.
    Not computed (ok False, with the reason) when a number is missing, when returned exceeds issued, or when the two are as of dates more than
    OUTSTANDING_MAX_GAP_DAYS apart. In a state that mails a ballot to every active voter (ALL_MAIL) the same difference is not "outstanding"
    (most of those voters never asked for a ballot); it is labelled "not returned yet" and says so."""
    if issued is None or returned is None or issued <= 0:
        return {"ok": False, "why": "no ballots-sent/requested figure beside the returned figure"}
    if returned > issued:
        return {"ok": False, "why": "returned exceeds sent/requested, so the two are not on the same basis"}
    gap = day_gap(issued_date, returned_date) if issued_date and returned_date else None
    if gap is not None and gap > OUTSTANDING_MAX_GAP_DAYS:
        return {"ok": False, "why": f"sent/requested and returned are as of dates {gap} days apart"}
    all_mail = st in ALL_MAIL
    out = {"ok": True, "kind": "not_returned_yet" if all_mail else "outstanding", "issued": round(issued), "issued_label": issued_label, "returned": round(returned),
           "value": round(issued - returned), "pct_returned": round(100 * returned / issued, 1)}
    out["note"] = ("A ballot is mailed to every active voter here, so this is not a count of voters still to vote; most were never requested."
                   if all_mail else "Sent (or requested) and not yet returned; some of these voters will vote in person instead, and some will not vote.")
    return out


def uf_outstanding(root: Path, st: str) -> dict[str, Any] | None:
    """Requested vs returned from the UF Election Lab state file (state_<st>.json): mail accepted where the file splits methods, else the total
    (all-mail states, where a returned ballot in person is the same ballot)."""
    p = root / "site" / "data" / "ev2026" / f"state_{st.lower()}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    req, lat = d.get("requested") or {}, d.get("latest") or {}
    issued = num(req.get("value"))
    if not issued:
        return None
    returned = num(lat.get("total")) if st in ALL_MAIL else num(lat.get("mail"))
    r = outstanding_rule(st, issued, returned, req.get("date"), lat.get("date"), "requested")
    r.update({"as_of_date": lat.get("date"), "prov": "secondary", "source": "UF Election Lab state file: requested vs " + ("all returned ballots" if st in ALL_MAIL else "mail ballots accepted")})
    return r


OFFICIAL_KINDS = ("advance", "mail_returned", "total")   # cumulative pre-Election Day counts; an Election Day-only count is never swapped in


def official_latest(root: Path, st: str, lat: dict[str, Any] | None) -> dict[str, Any] | None:
    """The newest official (or official-compiled) cumulative count of a state, used in place of the UF early-vote file only where its as-of
    date is strictly later than the UF file's last day. lat: the research extra's "latest" for the state (statewide, aggregates only).
    Returns {total, party, mail, in_person, lead, as_of, source, ...} or None when there is none or it is not newer."""
    if not lat or lat.get("kind") not in OFFICIAL_KINDS or not num(lat.get("total")) or not lat.get("as_of_date"):
        return None
    p = root / "site" / "data" / "ev2026" / f"state_{st.lower()}.json"
    uf = (json.loads(p.read_text()).get("latest") or {}) if p.exists() else {}
    uf_date = uf.get("date")
    if uf_date and str(lat["as_of_date"]) <= str(uf_date):
        return None
    party = lat.get("party")
    if party and (num(party.get("D")) is None or num(party.get("R")) is None):
        party = None
    tot = float(lat["total"])
    out = {k: lat[k] for k in ("kind", "total", "mail", "in_person", "other_method", "method_published", "as_of", "as_of_date", "as_of_utc", "prov", "source", "source_url", "checks") if lat.get(k) is not None}
    out["party"] = party
    out["lead"] = round(100 * (party["D"] - party["R"]) / tot, 3) if party else None
    out["d_share_dr"] = round(100 * party["D"] / (party["D"] + party["R"]), 2) if party and party["D"] + party["R"] > 0 else None
    out["uf"] = {"date": uf_date, "total": uf.get("total"), "lead": uf.get("lead")}
    out["note"] = (f"Newer official file than the early-vote file: {lat['as_of']} against the early-vote file's last day {uf_date or 'not available'}"
                   f"{f' ({int(uf['total']):,} ballots)' if num(uf.get('total')) else ''}. The early-vote series is kept for the history charts.")
    return out


def county_files(root: Path, st: str) -> dict[str, Any] | None:
    p = root / "site" / "data" / "ev2026" / f"counties_{st.lower()}.json"
    return json.loads(p.read_text()) if p.exists() else None


def age_block(st: str, groups: dict[str, Any] | None, extra_age: dict[str, Any] | None) -> dict[str, Any] | None:
    """Age shares of the banked ballots. Kansas: IKE Lab counts (2026 / 2024 / 2022 at the same report dates). Others: ev_groups.json, only
    where at least AGE_MIN_COVERAGE percent of the ballots has an age; its past-year shares are the same-day baselines the file names."""
    if extra_age:
        c = extra_age["counts"]
        tot = {y: sum(v.values()) for y, v in c.items()}
        bands = [{"band": b, "share": round(100 * c["2026"][b] / tot["2026"], 1),
                  "past": {y: round(100 * c[y][b] / tot[y], 1) for y in c if y != "2026" and tot.get(y)}} for b in extra_age["bands"]]
        return {"source": extra_age["source"], "as_of": extra_age["as_of_date"], "bands": bands, "past_labels": {y: extra_age["note"] for y in c if y != "2026"}, "n": tot["2026"]}
    if not groups:
        return None
    cov = (groups.get("coverage") or {})
    c = max((num(v) for k, v in cov.items() if k.startswith("age")), default=None)
    if c is None or c < AGE_MIN_COVERAGE:
        return None
    gs = {k: v for k, v in (groups.get("groups") or {}).items() if k.startswith("age:")} or {k: v for k, v in (groups.get("own") or {}).items() if k.startswith("age_uf:")}
    if not gs:
        return None
    bands = [{"band": k.split(":", 1)[1], "share": v["share"], "past": {y: p["share"] for y, p in (v.get("past") or {}).items() if y in ("2022", "2024")}} for k, v in gs.items()]
    labels = {y: (b.get("label") or "") for y, b in (groups.get("baselines") or {}).items() if y in ("2022", "2024")}
    return {"source": groups.get("source"), "as_of": groups.get("as_of"), "bands": bands, "past_labels": labels, "coverage_pct": c}


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
def flag_no_figure(cs: list[dict[str, Any]]) -> None:
    """Mark counties whose count is a feed gap: a count of 0, or below NO_FIGURE_FRAC of the expected votes while the median county (over
    counties with a count) is above NO_FIGURE_MEDIAN. They get no_figure=True and are left out of the scatter, the composition shift,
    the bootstrap and the county threshold."""
    ys = sorted(c["banked"] / c["exp"] for c in cs if c["banked"] > 0 and c["exp"] > 0)
    med = (ys[len(ys) // 2] if len(ys) % 2 else (ys[len(ys) // 2 - 1] + ys[len(ys) // 2]) / 2) if ys else 0.0
    for c in cs:
        c["no_figure"] = bool(c["banked"] <= 0 or (c["exp"] > 0 and c["banked"] / c["exp"] < NO_FIGURE_FRAC and med > NO_FIGURE_MEDIAN))


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


def build(day: dict[str, Any], root: Path, party_rows: dict[str, list[dict[str, Any]]] | None = None, prev: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base, state_two, units, polls, orders = load_baseline(root), load_state_two(root), load_units(root), load_polls(root), count_orders(root)
    party_rows = party_rows or {}
    extra = extra or {}
    gp = root / "site" / "data" / "us2026_context" / "ev_groups.json"
    groups = {g["state"].upper(): g for g in (json.loads(gp.read_text()).get("states") or [])} if gp.exists() else {}
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
        # mail ballots sent/requested vs returned per county (day-file rows that carry both), then the research extras; party of the county's banked ballots
        by_fips = {c["fips"]: c for c in cs}
        ocounty: dict[str, dict[str, Any]] = {}
        pcounty: dict[str, dict[str, Any]] = {}
        for hit, r in county_rows:
            iss = r.get("sent") or r.get("requests") or r.get("requested")
            if r["kind"] == "mail_returned" and num(r.get("count")) is not None and num(iss):
                ocounty[hit["unit_id"]] = {"issued": iss, "returned": r["count"], "label": "sent" if r.get("sent") else "requested", "as_of": r.get("as_of_utc") or r.get("as_of_date")}
        unmatched: list[str] = []
        cf = county_files(root, st) if st in COUNTY_FILES else None
        for c in (cf or {}).get("counties") or []:
            hit = by_fips.get(c.get("fips"))
            if hit and num(c.get("total")) is not None:
                county_rows.append((hit, {"kind": "advance", "level": "county", "count": c["total"], "as_of_date": cf.get("as_of"), "prov": "secondary", "_extra": True}))
        pf = county_files(root, st) if st in PARTY_FILES else None
        for c in ((pf or {}).get("counties") or []) if (pf or {}).get("has_party") else []:
            hit = by_fips.get(c.get("fips"))
            if hit and num(c.get("D")) is not None and num(c.get("R")) is not None:
                pcounty[hit["unit_id"]] = {"D": c["D"], "R": c["R"], "U": c.get("U"), "other": c.get("other"), "as_of": pf.get("as_of"), "of": "returned ballots"}
        ce = (extra.get("county_extra") or {}).get(st)
        for r in (ce or {}).get("rows") or []:
            hit = by_key.get(county_key(r["name"], st))
            if not hit:
                unmatched.append(r["name"])
                continue
            if num(r.get("count")) is not None:
                county_rows.append((hit, {"kind": ce["kind"], "level": "county", "count": r["count"], "as_of_date": ce.get("as_of_date"), "prov": ce.get("prov", "manual"), "_extra": True}))
            iss = r.get("sent") or r.get("issued") or r.get("requested")
            ret = r.get("returned", r.get("count"))
            if num(iss) and num(ret) is not None:
                ocounty[hit["unit_id"]] = {"issued": iss, "returned": ret, "label": "sent" if (r.get("sent") or r.get("issued")) else "requested", "as_of": ce.get("as_of_date")}
            if num(r.get("D")) is not None and num(r.get("R")) is not None:
                pcounty[hit["unit_id"]] = {"D": r["D"], "R": r["R"], "U": r.get("U"), "other": r.get("other"), "as_of": ce.get("as_of_date"), "of": "ballots counted in the county row"}
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
            if cur is None or r.get("_extra") or (r.get("as_of_utc") or "") >= (cur[1].get("as_of_utc") or ""):
                by_kind[r["kind"]][hit["unit_id"]] = (hit, r)
        ck = max(by_kind, key=lambda k: (len(by_kind[k]), -PRIORITY.index(k))) if by_kind else None
        counties = []
        if ck:
            for hit, r in by_kind[ck].values():
                rec_c = {"fips": hit["fips"], "name": re.sub(rf",\s*{st}$", "", hit["name"]), "share": round(hit["share"], 4), "exp": round(exp_by.get(hit["unit_id"], EST_TURNOUT * hit["two"])),
                         "banked": r["count"], "as_of": r.get("as_of_utc") or r.get("as_of_date"), "prov": r.get("prov")}
                o = ocounty.get(hit["unit_id"])
                if o and o["returned"] <= o["issued"]:
                    rec_c["mail"] = {"issued": round(o["issued"]), "returned": round(o["returned"]), "outstanding": round(o["issued"] - o["returned"]), "label": o["label"], "as_of": o["as_of"]}
                pt = pcounty.get(hit["unit_id"])
                if pt:
                    rec_c["party"] = {k: (round(v) if isinstance(v, (int, float)) else v) for k, v in pt.items() if v is not None}
                counties.append(rec_c)
            counties.sort(key=lambda c: -c["exp"])
            flag_no_figure(counties)
        # swing
        swing = None
        pl = polls.get(st)
        if pl and cs and exp_by:
            tot = sum(exp_by.get(c["unit_id"], 0) for c in cs)
            if tot > 0:
                state_base = sum(exp_by.get(c["unit_id"], 0) * c["share"] for c in cs) / tot
                swing = sum(p["avg"] for p in pl) / len(pl) - state_base
        comp = composition([{"share": c["share"], "banked": c["banked"], "exp": c["exp"]} for c in counties if not c["no_figure"]], swing) if counties else {"ok": False, "n": 0, "why": "no county counts"}
        if comp["ok"]:
            comp["kind"] = ck
            comp["coverage_pct"] = round(100 * sum(c["exp"] for c in counties if not c["no_figure"]) / exp_total, 1) if exp_total else None
            comp["swing_races"] = [p["race"] for p in (pl or [])] if swing is not None else []
            tot_all = sum(exp_by.values())
            if tot_all:
                comp["share_all_expected"] = round(100 * float(np.clip(np.array([c["share"] for c in cs]) + (swing or 0), 0, 1) @ np.array([exp_by.get(c["unit_id"], 0) for c in cs]) / tot_all), 2)
        # party
        lo = official_latest(root, st, ((extra.get("state_extra") or {}).get(st) or {}).get("latest"))
        parties = []
        if lo and lo["party"]:
            P_ = lo["party"]
            parties.append({"source": "newer official file", "label": lo["source"], "as_of": lo.get("as_of_utc") or lo["as_of_date"], "kind": "pre_election_day",
                            "D": P_["D"], "R": P_["R"], "other": (num(P_.get("U")) or 0) + (num(P_.get("other")) or 0) or None})
        evp = party_from_ev(root, st)
        if evp:
            parties.append(evp)
        for r in party_rows.get(st, []):
            parties.append(r)
        main_party = next((p for p in parties if p["kind"] != "election_day"), None)
        oc = ORDER_CLASS.get(st) if st in orders else None
        order = {"text": orders[st]["text"], "config": orders[st]["config"], "class": oc} if st in orders else {"text": None, "class": None}
        # mail ballots not yet returned (state): research extras, then a day-file mail row that carries sent/requested, then the UF requested-vs-returned pair
        outst = None
        se = (extra.get("state_extra") or {}).get(st)
        tries: list[dict[str, Any]] = []
        if se and se.get("returned") is not None:
            lab = "sent" if se.get("sent") else "requested"
            o = outstanding_rule(st, se.get("sent") or se.get("requested"), se["returned"], se.get("as_of_date"), se.get("as_of_date"), lab)
            o.update({"as_of_date": se.get("as_of_date"), "prov": se.get("prov"), "source": se.get("basis"), "source_url": se.get("source_url")})
            tries.append(o)
        mr = pick(statewide, "mail_returned")
        if mr and is_complete(mr, len(cs)) and (mr.get("sent") or mr.get("requests") or mr.get("requested")):
            d_ = mr.get("as_of_utc") or mr.get("as_of_date") or (mr.get("fetched_utc") or "")[:10] or None
            o = outstanding_rule(st, mr.get("sent") or mr.get("requests") or mr.get("requested"), mr["count"], d_, d_, "sent" if mr.get("sent") else "applications" if mr.get("requests") else "requested")
            o.update({"as_of_date": mr.get("as_of_date") or d_[:10] if d_ else None, "as_of_utc": mr.get("as_of_utc"), "prov": mr.get("prov"), "source": "state file: " + (mr.get("measure") or "mail ballots returned") + " beside its own sent/requested count",
                      "source_url": mr.get("source_url")})
            tries.append(o)
        if s["verdict_class"] != "excluded":
            u_ = uf_outstanding(root, st)
            if u_:
                tries.append(u_)
        outst = next((t for t in tries if t["ok"]), tries[0] if tries else None)
        # 2022 final pre-election report (not the same day): the ratio of the latest 2026 count of the same kind to it
        f22 = (extra.get("final_2022") or {}).get(st)
        vs22f = None
        if f22:
            vs22f = {"label": "vs 2022 final pre-election report (not same day)", "kind": f22["kind"], "value_2022": f22["value"], "date_2022": f22["as_of"], "what_2022": f22.get("label"), "source_2022": f22.get("src")}
            now_v = now_d = now_l = None
            adv = by_method.get("advance")
            if f22.get("usable") is False:
                vs22f.update({"ok": False, "why": f22.get("label")})
            else:
                if f22.get("now_value") is not None:
                    now_v, now_d, now_l = f22["now_value"], f22.get("now_as_of"), f22.get("now_label")
                elif adv and not adv["partial"] and f22["kind"] == "advance":
                    now_v, now_d, now_l = adv["count"], str(adv.get("as_of") or "")[:10] or None, "pre-Election Day ballots (early + mail), latest report"
                if now_v and f22["value"]:
                    vs22f.update({"ok": True, "now": now_v, "now_date": now_d, "now_what": now_l, "ratio": round(now_v / f22["value"], 3)})
                else:
                    vs22f.update({"ok": False, "why": "no 2026 count of the same kind"})
        who = {"age": age_block(st, groups.get(st.lower()) or groups.get(st), (extra.get("age") or {}).get(st)),
               "new_voters": {"share": None, "why": "no vote history in the recorded files, so the share of 2026 advance voters who did not vote in 2022 is not published here"}}
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
               "county_kind": ck, "n_county_rows": len(counties), "n_county_no_figure": sum(1 for c in counties if c.get("no_figure")), "counties": counties, "composition": comp, "order": order, "party": parties,
               "lean": lean_text(main_party, oc) if order["class"] else lean_text(main_party, None),
               "swing_pt": None if swing is None else round(swing * 100, 2),
               "latest_official": lo, "outstanding": outst, "vs_2022_final": vs22f, "who": who, "county_unmatched": unmatched or None,
               "county_party": bool(pcounty)}
        out_states[st] = rec
        if b and gen:
            h = history.setdefault(st, [])
            if not any(x["t"] == gen for x in h):
                h.append({"t": gen, "v": b["value"], "complete": b["complete"]})
            history[st] = sorted(h, key=lambda x: x["t"])[-HISTORY_MAX:]
    n = lambda k: len(kinds_seen[k])  # noqa: E731
    proj_summary = tproj.project(out_states, prev, gen, root)
    return {
        "label": LABEL, "generated_utc": gen, "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "est_turnout": EST_TURNOUT, "min_counties": MIN_COUNTIES, "bootstrap": {"n": N_BOOT, "seed": SEED},
        "summary": {"states_with_data": sum(1 for r in out_states.values() if r["banked"] or r["counties"]), "election_day": n("election_day"),
                    "early_in_person": n("early_in_person"), "mail": n("mail_returned"), "advance": n("advance"), "total": n("total"),
                    "complete": sum(1 for r in out_states.values() if r["status"] == "all_methods"),
                    "with_expected_pct": sum(1 for r in out_states.values() if r["banked_pct_expected"] is not None),
                    "composition_states": sorted(r["st"] for r in out_states.values() if r["composition"]["ok"]),
                    "latest_official_states": sorted(r["st"] for r in out_states.values() if r["latest_official"]),
                    "outstanding_states": sorted(r["st"] for r in out_states.values() if r["outstanding"] and r["outstanding"]["ok"]),
                    "vs_2022_final_states": sorted(r["st"] for r in out_states.values() if r["vs_2022_final"] and r["vs_2022_final"].get("ok"))},
        "states": out_states, "history": history, "proj_summary": proj_summary,
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
    ap.add_argument("--extra", default=str(tp.RESEARCH.parent.parent / "data" / "turnout_extra.json"), help="research turnout_extra.json (scripts/live/turnout_extra.py)")
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
    out = build(day, root, party, prev, load_extra(Path(a.extra)))
    changed = tp.write_if_changed(prev_p, out, ignore=("published_utc",))
    s = out["summary"]
    print(f"{s['states_with_data']} states with data, {s['with_expected_pct']} with % of expected, composition for {len(s['composition_states'])}; {'changed' if changed else 'no change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
