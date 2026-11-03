"""Has the early vote tracked the result? 2016-2024, every state: site/data/ev2026/ev_history_all.json for site/earlyvote.html.

  python analysis/us_ev_history_all.py --csv ~/brunder-research/data/ev_history_all.csv     # no network, aggregates only

Input: one row per state x cycle from the UF Election Lab / US Elections Project early-vote trackers (secondary source; the last
report dated on or before Election Day; collected and parsed by brunder-research/scripts/session/ev_history_all.py), the certified
statewide results of data_public/us_ev_party_misses.csv (D two-party share, polled Senate / governor / president races) and the
county panel (final total votes; president where the table has no race).

Descriptive only: not a model input, not a projection. Registration-based: the party a voter is registered with, not how the ballot
was cast.

  ev_d2   registered-D share of D+R among early ballots, in percent (only where the source reports party for the whole early vote)
  act     mean D two-party share (percent) of the state-cycle's statewide Senate / governor / president races
  gap     ev_d2 - act, points (positive: the early vote was more Democratic than the result)
  right   ev_d2 and act on the same side of 50
  early%  early ballots as a percent of the state's final total votes (largest statewide race)
  change  a state's change between two cycles; 'same type' = presidential with presidential, midterm with midterm; 'any' = the state's
          previous available cycle. Direction agreement = the early-vote measure and the result moved the same way; the cycle-demeaned
          version subtracts the cycle's mean change over the states first, so a national swing cannot make it agree.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_NAME = Path("site") / "data" / "ev2026" / "ev_history_all.json"
CYCLES = [2016, 2018, 2020, 2022, 2024]
PRESIDENTIAL = {2016, 2020, 2024}
STALE_DAYS = 7          # a report dated more than this many days before Election Day is not taken as the state's final early vote for the volume figures
CONTESTED = (5.0, 95.0)  # a county-panel race whose D share is outside this has no Democrat on the ballot (or no other candidate) and is not used
ED = {2016: "2016-11-08", 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
OFFICE = {"Pres-G": "President", "Sen-G": "Senate", "Gov-G": "Governor"}
SOURCES = {
    2016: "US Elections Project 2016 early-vote sheet: the Internet Archive holds only its empty shell, no data tab",
    2018: "US Elections Project 2018 early-vote sheet, Internet Archive copy of 2018-11-06",
    2020: "US Elections Project 2020 state pages (electproject.github.io/Early-Vote-2020G)",
    2022: "UF Election Lab / US Elections Project 2022 state pages (rpubs.com/ElectProject)",
    2024: "UF Election Lab 2024 early-vote file (election.lab.ufl.edu/data-downloads/earlyvote/2024/US.csv)",
}
LABEL = "UF Election Lab / US Elections Project early-vote trackers (secondary source, Michael McDonald)"
LICENCE = "CC BY-NC-ND 4.0; displayed with the permission of Dr. Michael McDonald, granted to the project (recorded 2026-11-01)"
TEST = ("The registered early-vote-gap test of docs/us_ev_gap.md found no usable signal (24 races, direction 14/23 = 61 %, Wilson lower bound 0.41; "
        "adjusting the polls made them worse out of sample). It is not used by any model.")


# ---------------------------------------------------------------------------------------------------------------- math helpers
def d2(d, r):
    """Registered-D share of D+R in percent; None when either count is missing or both are zero."""
    if d is None or r is None or d + r <= 0:
        return None
    return 100.0 * d / (d + r)


def gap(ev, act):
    """Early-vote D share minus the result, in points; None if either is missing."""
    return None if ev is None or act is None else ev - act


def right_winner(ev, act):
    """True when the early-vote D share and the result are on the same side of 50; None when either is missing or exactly 50."""
    if ev is None or act is None or ev == 50 or act == 50:
        return None
    return (ev > 50) == (act > 50)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def sd(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def demean(values, keys):
    """Subtract, from each value, the mean of the values that share its key (None stays None; a key with one value becomes 0)."""
    groups: dict = {}
    for v, k in zip(values, keys):
        if v is not None:
            groups.setdefault(k, []).append(v)
    mu = {k: sum(g) / len(g) for k, g in groups.items()}
    return [None if v is None else v - mu[k] for v, k in zip(values, keys)]


def sign(x, eps=1e-12):
    return 0 if x is None or abs(x) <= eps else (1 if x > 0 else -1)


def agree(xs, ys):
    """(pairs where both moved the same way, pairs where both moved at all). Zero and missing pairs are not counted."""
    k = n = 0
    for x, y in zip(xs, ys):
        sx, sy = sign(x), sign(y)
        if sx and sy:
            n += 1
            k += sx == sy
    return k, n


def corr(xs, ys):
    p = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(p) < 3:
        return None
    mx, my = mean([a for a, _ in p]), mean([b for _, b in p])
    sxx = sum((a - mx) ** 2 for a, _ in p)
    syy = sum((b - my) ** 2 for _, b in p)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in p) / math.sqrt(sxx * syy)


def wilson(k, n, z=1.96):
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]


def same_type_prev(cycle, have):
    """The cycle four years earlier when the state has it (presidential with presidential, midterm with midterm)."""
    return cycle - 4 if cycle - 4 in have else None


def any_prev(cycle, have):
    prev = [c for c in sorted(have) if c < cycle]
    return prev[-1] if prev else None


def components(mail, inperson, total=None):
    """What the source's early-vote total is made of; changes between cycles are only compared when this is the same."""
    m, i = mail or 0, inperson or 0
    if total:
        if m > 0 and i > 0 and abs(m + i - total) <= 0.05 * total:
            return "mail + in-person"
        if m >= 0.95 * total and i <= 0.05 * total:
            return "mail only"
        if i >= 0.95 * total and m <= 0.05 * total:
            return "in-person only"
        return "total only"
    return "mail + in-person" if m > 0 and i > 0 else "mail only" if m > 0 else "in-person only" if i > 0 else "total only"


# ---------------------------------------------------------------------------------------------------------------- inputs
def _f(x):
    if x in (None, "", "nan"):
        return None
    return float(x)


def load_csv(path: Path):
    return list(csv.DictReader(path.open()))


def load_results(root: Path):
    """{(st, cycle): [ {race, office, act, special} ]}: certified D two-party share of the polled statewide races (misses table)."""
    res: dict = {}
    for r in csv.DictReader((root / "data_public" / "us_ev_party_misses.csv").open()):
        key = (r["state"], int(r["cycle"]))
        t = r["type"]
        res.setdefault(key, []).append({"race": r["race"], "office": OFFICE.get(t, t), "act": float(r["act"]), "special": r["race"].split("_")[1] not in OFFICE})
    return res


def load_panel(root: Path):
    """({(st, cycle): final total votes (largest statewide race total)}, {(st, cycle): [races: vote-weighted D two-party share]}) from the county panel."""
    import pandas as pd
    ab = {"01": "al", "04": "az", "05": "ar", "06": "ca", "08": "co", "10": "de", "12": "fl", "13": "ga", "15": "hi", "16": "id", "17": "il", "18": "in", "19": "ia", "20": "ks", "21": "ky",
          "22": "la", "23": "me", "24": "md", "25": "ma", "26": "mi", "27": "mn", "28": "ms", "29": "mo", "30": "mt", "31": "ne", "32": "nv", "33": "nh", "34": "nj", "35": "nm", "36": "ny",
          "37": "nc", "38": "nd", "39": "oh", "40": "ok", "41": "or", "42": "pa", "44": "ri", "45": "sc", "46": "sd", "47": "tn", "48": "tx", "49": "ut", "50": "vt", "51": "va", "53": "wa",
          "54": "wv", "55": "wi", "56": "wy"}
    c = pd.read_csv(root / "data_public" / "us_county_panel.csv", skiprows=1, dtype={"fips": str, "st": str})
    c = c[c.year.isin(CYCLES)].copy()
    c["ab"] = c.st.map(ab)
    votes, pres = {}, {}
    for (st, y), g in c.groupby(["ab", "year"]):
        tot = max([g[v].sum() for v in ("v_pres", "v_sen", "v_gov", "v_house") if g[v].notna().any()] or [0])
        if tot > 0:
            votes[(st, int(y))] = float(tot)
        for office, sc, vc in (("President", "s_pres", "v_pres"), ("Senate", "s_sen", "v_sen"), ("Governor", "s_gov", "v_gov")):
            gp = g[g[vc].notna() & (g[vc] > 0) & g[sc].notna()]
            share = float(100 * (gp[sc] * gp[vc]).sum() / gp[vc].sum()) if len(gp) else None
            if share is not None and CONTESTED[0] <= share <= CONTESTED[1]:
                pres.setdefault((st, int(y)), []).append({"race": f"{int(y)}_{office[:3]}_{st.upper()}", "office": office, "act": share, "special": False, "from_panel": True})
    return votes, pres


def load_2026(root: Path):
    site = root / "site" / "data" / "ev2026"
    idx = json.loads((site / "index.json").read_text())
    names = {c["st"]: c["name"] for c in idx["states"]}
    ev26 = {}
    for c in idx["states"]:
        f = site / f"state_{c['st']}.json"
        if not f.exists():
            continue
        L = json.loads(f.read_text()).get("latest") or {}
        P = L.get("party") or {}
        ev26[c["st"]] = {"as_of": L.get("date"), "total": L.get("total"), "d2": d2(P.get("D"), P.get("R")) if P else None, "status": c["status"]}
    return names, ev26, idx.get("election_day", "2026-11-03")


# ---------------------------------------------------------------------------------------------------------------- build
def build_rows(csv_rows, results, votes, pres_panel):
    rows = []
    for r in csv_rows:
        st, cy = r["state"], int(r["cycle"])
        if not r.get("report_date"):
            rows.append({"st": st, "cycle": cy, "report": None, "why": r.get("note") or "no report"})
            continue
        total, mail, inp = _f(r["total"]), _f(r["mail"]), _f(r["inperson"])
        partial = r.get("partial") or None
        D, R, O = _f(r["party_D"]), _f(r["party_R"]), _f(r["party_O"])
        party_ok = r["party_ok"] == "1" and not partial
        ev = d2(D, R) if party_ok else None
        races = list(results.get((st, cy), []))
        # the county panel fills in: the president where the polled-races table has none; every race of a state-cycle the table does not cover at all
        pan = pres_panel.get((st, cy), [])
        if races:
            if not any(x["office"] == "President" for x in races):
                races += [x for x in pan if x["office"] == "President"]
        else:
            races += pan
        act = mean([x["act"] for x in races])
        final = votes.get((st, cy))
        pct = 100.0 * total / final if total and final and not partial else None
        why = None
        if pct is not None and pct > 100:
            pct, why = None, "early ballots exceed the final total in the table: definition or table mismatch"
        elif pct is not None and int(r["days_before"]) > STALE_DAYS:
            pct, why = None, f"last report is {int(r['days_before'])} days before Election Day"
        age_n, age_all = _f(r["age_old_n"]), _f(r["age_all_n"])
        rows.append({
            "st": st, "cycle": cy, "report": r["report_date"], "days_before": int(r["days_before"]), "total": total, "mail": mail, "inperson": inp,
            "defn": components(mail, inp, total), "partial": partial, "final_votes": final, "early_pct": pct, "volume_why": why,
            "D": D, "R": R, "O": O, "party_basis": r["party_basis"] or None, "party_ok": party_ok, "ev_d2": ev,
            "races": [{k: x[k] for k in ("race", "office", "act", "special", "from_panel") if k in x} for x in races],
            "act": act, "gap": gap(ev, act), "right": right_winner(ev, act),
            "age_old": (100.0 * age_n / age_all) if age_n and age_all else None, "age_label": r["age_old_label"] or None,
            "source": r["source"], "url": r["url"], "note": r["note"] or None,
        })
    return rows


def change_pairs(rows, measure, how, need_defn=True):
    """Change pairs of `measure` ('ev_d2', 'early_pct' or 'age_old') against the change of the result.
    how: 'same' (cycle - 4) or 'any' (the previous cycle for which the state has the measure). Raw and cycle-demeaned changes."""
    by: dict = {}
    for r in rows:
        if r.get("report") and r.get(measure) is not None and r.get("act") is not None:
            by.setdefault(r["st"], {})[r["cycle"]] = r
    out = []
    for st, d in by.items():
        for c1 in sorted(d):
            c0 = same_type_prev(c1, d) if how == "same" else any_prev(c1, d)
            if c0 is None:
                continue
            a, b = d[c0], d[c1]
            if measure == "age_old" and a["age_label"] != b["age_label"]:
                continue
            if need_defn and a["defn"] != b["defn"]:
                out.append({"st": st, "c0": c0, "c1": c1, "skipped": "definition changed: " + a["defn"] + " -> " + b["defn"]})
                continue
            out.append({"st": st, "c0": c0, "c1": c1, "dx": b[measure] - a[measure], "dact": b["act"] - a["act"], "x0": a[measure], "x1": b[measure], "act0": a["act"], "act1": b["act"]})
    used = [p for p in out if "skipped" not in p]
    keys = [(p["c0"], p["c1"]) for p in used]
    for p, dx, da in zip(used, demean([p["dx"] for p in used], keys), demean([p["dact"] for p in used], keys)):
        p["dx_dm"], p["dact_dm"] = dx, da
    return out


def pair_summary(pairs, demean_ok=True):
    used = [p for p in pairs if "skipped" not in p]
    k, n = agree([p["dx"] for p in used], [p["dact"] for p in used])
    res = {"n": len(used), "skipped_definition": len(pairs) - len(used), "agree": k, "agree_n": n, "agree_wilson": wilson(k, n), "corr": corr([p["dx"] for p in used], [p["dact"] for p in used])}
    if demean_ok:
        k2, n2 = agree([p.get("dx_dm") for p in used], [p.get("dact_dm") for p in used])
        res.update({"agree_dm": k2, "agree_dm_n": n2, "agree_dm_wilson": wilson(k2, n2), "corr_dm": corr([p.get("dx_dm") for p in used], [p.get("dact_dm") for p in used])})
    return res


def gap_summary(rs):
    g = [r["gap"] for r in rs if r["gap"] is not None]
    return {"n": len(g), "mean_gap": mean(g), "mean_abs_gap": mean([abs(x) for x in g]), "sd_gap": sd(g), "min_gap": min(g) if g else None, "max_gap": max(g) if g else None,
            "right": sum(1 for r in rs if r["right"] is True), "right_n": sum(1 for r in rs if r["right"] is not None)}


def build(csv_rows, results, votes, pres_panel, names, ev26, election_day, now=None):
    rows = build_rows(csv_rows, results, votes, pres_panel)
    have = [r for r in rows if r.get("report")]
    party = [r for r in have if r["gap"] is not None]
    cyc = []
    for c in CYCLES:
        rr = [r for r in have if r["cycle"] == c]
        pr = [r for r in party if r["cycle"] == c]
        days = [r["days_before"] for r in rr]
        cyc.append({"cycle": c, "election_day": ED[c], "midterm": c not in PRESIDENTIAL, "source": SOURCES[c], "states_reported": len(rr), "states_with_total": sum(1 for r in rr if r["total"] and not r["partial"]),
                    "states_with_party": sum(1 for r in rr if r["party_ok"]), "states_with_gap": len(pr), "states_with_volume": sum(1 for r in rr if r["early_pct"] is not None),
                    "days_before_min": min(days) if days else None, "days_before_max": max(days) if days else None, "on_election_day": sum(1 for d in days if d == 0), **gap_summary(pr)})
    pairs_same, pairs_any = change_pairs(rows, "ev_d2", "same"), change_pairs(rows, "ev_d2", "any")
    states = []
    for st in sorted(names):
        sr = sorted([r for r in have if r["st"] == st], key=lambda r: r["cycle"])
        pr = [r for r in sr if r["gap"] is not None]
        g = gap_summary(pr)
        ps = [p for p in pairs_same if p["st"] == st and "skipped" not in p]
        pa = [p for p in pairs_any if p["st"] == st and "skipped" not in p]
        ks, ns = agree([p["dx"] for p in ps], [p["dact"] for p in ps])
        ka, na = agree([p["dx"] for p in pa], [p["dact"] for p in pa])
        e = ev26.get(st)
        e26 = None
        if e and e.get("d2") is not None:
            e26 = {"d2": e["d2"], "as_of": e["as_of"], "total": e["total"], "status": e["status"]}
            if g["n"] >= 2:
                lo, hi = e["d2"] - g["max_gap"], e["d2"] - g["min_gap"]
                e26.update({"range": [lo, hi], "range_mean": e["d2"] - g["mean_gap"], "range_crosses_50": lo < 50 < hi})
        states.append({"st": st, "name": names[st], "cycles_reported": [r["cycle"] for r in sr], "cycles_party": [r["cycle"] for r in pr], "cycles_volume": [r["cycle"] for r in sr if r["early_pct"] is not None],
                       **g, "dir_same": ks, "dir_same_n": ns, "dir_any": ka, "dir_any_n": na, "ev26": e26, "ev26_status": (e or {}).get("status")})
    vol_same, vol_any = change_pairs(rows, "early_pct", "same"), change_pairs(rows, "early_pct", "any")
    age_any = change_pairs(rows, "age_old", "any", need_defn=False)
    pooled = {
        "gap": gap_summary(party), "winner_wrong": [f"{r['st'].upper()} {r['cycle']}" for r in party if r["right"] is False],
        "party_change_same": pair_summary(pairs_same), "party_change_any": pair_summary(pairs_any),
        "volume_same": pair_summary(vol_same), "volume_any": pair_summary(vol_any),
        "age": {"pairs": [p for p in age_any if "skipped" not in p], **pair_summary(age_any, demean_ok=False)},
        "volume_by_cycle": [{"cycle": c, "n": sum(1 for r in have if r["cycle"] == c and r["early_pct"] is not None),
                             "mean_pct": mean([r["early_pct"] for r in have if r["cycle"] == c and r["early_pct"] is not None])} for c in CYCLES],
    }
    return {
        "built": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%MZ"), "election_day": election_day, "cycles": cyc, "label": LABEL, "licence": LICENCE, "test_caveat": TEST, "sources": SOURCES,
        "notes": ["Early vote = the last report dated on or before Election Day in each source (report date and days before Election Day are in every row). A report dated on Election Day counts ballots through the previous day or that morning.",
                  "Early-vote definitions differ by state and year (mail only, in-person only or both): changes between cycles are compared only where the definition is the same; a report that covers part of a state is kept out of the share and change figures.",
                  "Statewide result = mean D two-party share of the certified polled Senate, governor and president races of the state in that cycle (data_public/us_ev_party_misses.csv); president from the county panel where that table has none. Senate and governor races without a poll in the final 21 days are not in it.",
                  "Final total votes: the largest statewide race total in the county panel, which does not hold Alaska, Connecticut or DC, so their early-vote share is blank."],
        "rows": rows, "states": states, "pooled": pooled,
        "pairs": {"party_same": pairs_same, "party_any": pairs_any, "volume_same": vol_same, "volume_any": vol_any},
    }


def rounded(o, nd=4):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, nd)
    if isinstance(o, dict):
        return {k: rounded(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [rounded(v, nd) for v in o]
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="brunder-research data/ev_history_all.csv")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    root = Path(a.root)
    csv_rows = load_csv(Path(a.csv).expanduser())
    votes, pres = load_panel(root)
    names, ev26, ed = load_2026(root)
    out = build(csv_rows, load_results(root), votes, pres, names, ev26, ed)
    (root / OUT_NAME).write_text(json.dumps(rounded(out), separators=(",", ":"), allow_nan=False))
    P = out["pooled"]
    print(f"{OUT_NAME}: {len(out['rows'])} state-cycles; gap n={P['gap']['n']} mean {P['gap']['mean_gap']:.2f} |gap| {P['gap']['mean_abs_gap']:.2f} right {P['gap']['right']}/{P['gap']['right_n']}")
    for c in out["cycles"]:
        print(f"  {c['cycle']}: reported {c['states_reported']}, party {c['states_with_party']}, gap {c['states_with_gap']}, volume {c['states_with_volume']}, mean gap {c['mean_gap']}")
    for k in ("party_change_same", "party_change_any", "volume_same", "volume_any"):
        print(" ", k, {x: (round(v, 3) if isinstance(v, float) else v) for x, v in P[k].items() if "wilson" not in x})
    return 0


if __name__ == "__main__":
    sys.exit(main())
