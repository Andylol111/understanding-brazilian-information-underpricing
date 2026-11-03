"""Projection of the FINAL total votes per state, from the ballots banked so far. Unregistered: updates with incoming data; the registered models do not use it.

Called from us_turnout_model.build(), so every refresh of turnout_model.json refreshes it; output: states.<st>.proj and the top-level proj_summary.

Estimators (each uses only data available at the time):
  A  banked pre-Election Day ballots / s_hat. s_hat = the state's early-vote share of its final total in its previous same-type cycle (2022, else 2018 for 2026), else the
     previous any-type cycle (2024, else 2020); clipped to [CLIP]. Where a state publishes mail ballots sent/requested and not yet returned, a floor
     banked + r x outstanding (r = 0.5: no state's late-return rate is on disk) and the ballots already banked (incl. Election Day check-ins) are applied: the projection is never below them.
  B  A blended with the model's expected votes by precision, weight on A = (1/vA) / (1/vA + 1/vM), vA and vM the mean squared log errors of A and of the naive baseline in the
     backtest cycles BEFORE the cycle being projected. The model's expected votes are two-party votes; they are scaled to ballots by the state's 2024 total/two-party ratio.
  C  Election Day check-ins x 1 / (share of Election Day turnout in by this clock hour): NOT AVAILABLE, no prior-cycle hour curve is on disk; none is invented.

Backtest (same-type cycle pairs: 2022 on 2018 shares, 2024 on 2020, 2020 on 2016 where an early-vote file exists; plus the any-type fallback pairs 2022 on 2020, 2024 on 2022):
error = log(projected / actual final); naive baseline = 0.72 x the previous presidential total (midterms) or the previous same-type total (president years).
The variant with the lowest out-of-sample MAE is the one shown. The interval is exp(+-1.2816 x sd) of the variant's past log errors (80 %).

  uv run python analysis/us_turnout_proj.py [--root REPO]        # prints the backtest table
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CLIP = (0.15, 0.98)
R_DEFAULT = 0.5
EST_TURNOUT = 0.72            # as us_turnout_model.EST_TURNOUT (the model-style baseline for midterms)
Z80 = 1.2816
LEVEL = 80
MIN_N = 8                     # fewer past records than this: no fit, no blend, no interval
BACKTEST_CYCLES = (2020, 2022, 2024)
TARGET_CYCLE = 2026
HIST_CAP = 120
INLINE_TOL = 0.0
LABEL = "Unregistered, updates with incoming data; the registered models do not use it"
ED_WHY = "no prior-cycle Election Day check-in curve by clock hour is on disk (the history files hold only each state's last pre-election early-vote report), so no hour curve is invented"
COUNTY_WHY = "no county-level early-vote share history is on disk (only each county's 2022 final total), so no county projection is made"


# ------------------------------------------------------------------------------------------------------------------ pure maths
def clip_share(s: float) -> float:
    return min(max(s, CLIP[0]), CLIP[1])


def est_A(early: float, s_prev: float) -> float:
    """Banked pre-Election Day ballots over the previous cycle's early share of the final total (share clipped)."""
    return early / clip_share(s_prev)


def blend_weight(vA: float, vM: float) -> float:
    a, m = 1.0 / max(vA, 1e-9), 1.0 / max(vM, 1e-9)
    return a / (a + m)


def blend(A: float, M: float, w: float) -> float:
    """Precision-weighted blend in log space (w on A)."""
    return math.exp(w * math.log(A) + (1 - w) * math.log(M))


def is_midterm(cycle: int) -> bool:
    return cycle % 4 == 2


def prev_cycle(cycle: int, kind: str) -> int:
    """same: the previous cycle of the same type (4 years back); any: the previous cycle (2 years back)."""
    return cycle - (4 if kind == "same" else 2)


def baseline(st: str, cycle: int, final: dict[tuple[str, int], float], pres: dict[str, dict[int, float]]) -> float | None:
    """Naive projection: 0.72 x the previous presidential total (midterm); the previous same-type total (president year). Prior cycles only."""
    if is_midterm(cycle):
        v = (pres.get(st) or {}).get(cycle - 2)
        return EST_TURNOUT * v if v else None
    return final.get((st, cycle - 4)) or (pres.get(st) or {}).get(cycle - 4) or None


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def pct_err(proj: float, actual: float) -> float:
    return abs(proj / actual - 1) * 100


# ------------------------------------------------------------------------------------------------------------------ inputs
def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def load_hist(root: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """(state, cycle) -> {early, final, share, defn, report} for the last pre-election early-vote report with a usable share (site/data/ev2026/ev_history_all.json)."""
    d = json.loads((root / "site" / "data" / "ev2026" / "ev_history_all.json").read_text())
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in d["rows"]:
        if r.get("early_pct") is None or not r.get("total") or not r.get("final_votes"):
            continue
        out[(r["st"].upper(), int(r["cycle"]))] = {"early": float(r["total"]), "final": float(r["final_votes"]), "share": float(r["early_pct"]) / 100.0,
                                                   "defn": r.get("defn"), "report": r.get("report")}
    return out


def load_pres(root: Path) -> tuple[dict[str, dict[int, float]], dict[str, float]]:
    """state -> {year: total president votes} from the county panel (certified), and state -> 2024 total / two-party ratio (ballots per two-party vote)."""
    f2s = {r["unit_id"].split("-")[-1][:2]: r["state"].upper() for r in read_csv(root / "data_public" / "us2026_county_baseline.csv")}
    pres: dict[str, dict[int, float]] = {}
    for r in read_csv(root / "data_public" / "us_county_panel.csv"):
        st = f2s.get(r["fips"][:2])
        if not st or not r["v_pres"]:
            continue
        y = int(r["year"])
        pres.setdefault(st, {})[y] = pres.setdefault(st, {}).get(y, 0.0) + float(r["v_pres"])
    k: dict[str, float] = {}
    for r in read_csv(root / "data_public" / "us2026_state_pres24.csv"):
        st = r["state"].upper()
        two = float(r["dem"]) + float(r["rep"])
        if two and pres.get(st, {}).get(2024):
            k[st] = pres[st][2024] / two
    return pres, k


# ------------------------------------------------------------------------------------------------------------------ backtest
def make_records(hist: dict[tuple[str, int], dict[str, Any]], pres: dict[str, dict[int, float]], kind: str, cycles: tuple[int, ...] = BACKTEST_CYCLES) -> list[dict[str, Any]]:
    """One record per (state, cycle) that has the cycle's last pre-election early count, the previous cycle's early share, and a baseline.
    The projection uses only: this cycle's early count, the previous cycle's share, prior-cycle totals. The cycle's final total is the target only."""
    final = {k: v["final"] for k, v in hist.items()}
    out = []
    for c in cycles:
        p = prev_cycle(c, kind)
        for st in sorted({k[0] for k in hist}):
            cur, prv = hist.get((st, c)), hist.get((st, p))
            base = baseline(st, c, {k: v for k, v in final.items() if k[1] < c}, pres)
            if not cur or not prv or not base:
                continue
            A = est_A(cur["early"], prv["share"])
            out.append({"st": st, "cycle": c, "share_cycle": p, "early": cur["early"], "s_prev": prv["share"], "actual": cur["final"], "A": A, "M": base,
                        "eA": math.log(A / cur["final"]), "eM": math.log(base / cur["final"]), "same_defn": cur["defn"] == prv["defn"] and cur["defn"] is not None})
    return out


def fit(past: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Variances of the log errors of A and of the baseline over `past` records, the blend weight and the sd of the A, baseline and B errors (B in sample on `past`)."""
    if len(past) < MIN_N:
        return None
    vA, vM = mean([r["eA"] ** 2 for r in past]), mean([r["eM"] ** 2 for r in past])
    w = blend_weight(vA, vM)
    vB = mean([(w * r["eA"] + (1 - w) * r["eM"]) ** 2 for r in past])
    return {"n": len(past), "vA": vA, "vM": vM, "w": w, "sdA": math.sqrt(vA), "sdM": math.sqrt(vM), "sdB": math.sqrt(vB)}


def evaluate(records: list[dict[str, Any]], cycles: tuple[int, ...] = BACKTEST_CYCLES) -> list[dict[str, Any]]:
    """Per cycle: A and baseline always; B and the 80 % coverage only when records of EARLIER cycles fit the blend weight and the interval (no future data)."""
    rows = []
    for c in cycles:
        rs = [r for r in records if r["cycle"] == c]
        past = [r for r in records if r["cycle"] < c]
        f = fit(past)
        row: dict[str, Any] = {"cycle": c, "n": len(rs), "fit_n": len(past) if f else 0}
        if rs:
            row.update(mae_A=round(mean([pct_err(r["A"], r["actual"]) for r in rs]), 2), mae_M=round(mean([pct_err(r["M"], r["actual"]) for r in rs]), 2),
                       bias_A=round(100 * (math.exp(mean([r["eA"] for r in rs])) - 1), 2), bias_M=round(100 * (math.exp(mean([r["eM"] for r in rs])) - 1), 2))
            sd = [r for r in rs if r["same_defn"]]
            row["n_same_defn"] = len(sd)
            row["mae_A_same_defn"] = round(mean([pct_err(r["A"], r["actual"]) for r in sd]), 2) if sd else None
        if rs and f:
            Bs = [blend(r["A"], r["M"], f["w"]) for r in rs]
            row.update(w=round(f["w"], 3), mae_B=round(mean([pct_err(b, r["actual"]) for b, r in zip(Bs, rs)]), 2),
                       cov_A=round(100 * mean([1.0 if abs(r["eA"]) <= Z80 * f["sdA"] else 0.0 for r in rs]), 1),
                       cov_B=round(100 * mean([1.0 if abs(math.log(b / r["actual"])) <= Z80 * f["sdB"] else 0.0 for b, r in zip(Bs, rs)]), 1))
            for r, b in zip(rs, Bs):
                r["B"], r["eB"] = b, math.log(b / r["actual"])
        rows.append(row)
    return rows


def select_variant(same_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lowest out-of-sample MAE among A and B on the cycles where B is defined (B needs an earlier cycle to fit on); ties go to A."""
    oos = [r for r in same_rows if r.get("mae_B") is not None and r["n"]]
    if not oos:
        return {"variant": "A", "why": "no earlier cycle to fit the blend on, so A is used", "n": 0, "beats_baseline": None, "verdict": "no out-of-sample test is possible"}
    n = sum(r["n"] for r in oos)
    mA, mB = sum(r["mae_A"] * r["n"] for r in oos) / n, sum(r["mae_B"] * r["n"] for r in oos) / n
    mM = sum(r["mae_M"] * r["n"] for r in oos) / n
    v = "B" if mB < mA else "A"
    best = min(mA, mB)
    verdict = (f"Out of sample the projection ({v}, MAE {best:.1f} %) {'beats' if best < mM else 'does not beat'} the naive baseline (MAE {mM:.1f} %) on {n} state-cycles; "
               + ("read it as a cross-check on the model's expected total, not a better number." if best >= mM else "it is the better estimate of the two."))
    return {"variant": v, "mae_A": round(mA, 2), "mae_B": round(mB, 2), "mae_M": round(mM, 2), "n": n, "cycles": [r["cycle"] for r in oos], "beats_baseline": best < mM, "verdict": verdict,
            "why": f"lowest out-of-sample MAE on {n} state-cycles ({', '.join(str(r['cycle']) for r in oos)}): A {mA:.1f} %, B {mB:.1f} %, naive baseline {mM:.1f} %"}


def run_backtest(hist: dict[tuple[str, int], dict[str, Any]], pres: dict[str, dict[int, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("same", "any"):
        recs = make_records(hist, pres, kind)
        rows = evaluate(recs)
        for r in rows:
            r["kind"] = kind
            r["share_from"] = prev_cycle(r["cycle"], kind)
        out[kind] = {"records": recs, "rows": rows, "fit": fit(recs)}
    out["selection"] = select_variant(out["same"]["rows"])
    return out


def backtest_table(bt: dict[str, Any]) -> dict[str, Any]:
    """The JSON-safe version: rows per cycle and the per-state errors (percent)."""
    errs = []
    for kind in ("same", "any"):
        for r in bt[kind]["records"]:
            errs.append({"st": r["st"], "cycle": r["cycle"], "kind": kind, "early": round(r["early"]), "s_prev": round(r["s_prev"], 4), "actual": round(r["actual"]),
                         "A": round(r["A"]), "M": round(r["M"]), "B": round(r["B"]) if "B" in r else None, "err_A_pct": round(100 * (math.exp(r["eA"]) - 1), 2),
                         "err_M_pct": round(100 * (math.exp(r["eM"]) - 1), 2), "err_B_pct": round(100 * (math.exp(r["eB"]) - 1), 2) if "eB" in r else None, "same_defn": r["same_defn"]})
    f = {k: ({kk: round(vv, 5) if isinstance(vv, float) else vv for kk, vv in bt[k]["fit"].items()} if bt[k]["fit"] else None) for k in ("same", "any")}
    return {"rows": bt["same"]["rows"] + bt["any"]["rows"], "selection": bt["selection"], "fit": f, "errors": errs, "level": LEVEL, "min_n": MIN_N,
            "baseline": f"{EST_TURNOUT} x the previous presidential total (midterms); the previous same-type total (president years)"}


# ------------------------------------------------------------------------------------------------------------------ live projection
def pick_share(st: str, hist: dict[tuple[str, int], dict[str, Any]], cycle: int = TARGET_CYCLE) -> dict[str, Any] | None:
    """Most recent same-type earlier cycle with a share, else the most recent earlier cycle of any type; only cycles before `cycle`."""
    for kind, cands in (("same", [c for c in range(cycle - 4, 2015, -4)]), ("any", [c for c in range(cycle - 2, 2015, -2) if not (is_midterm(c) == is_midterm(cycle))])):
        for c in cands:
            h = hist.get((st, c))
            if h:
                return {"kind": kind, "cycle": c, "share": h["share"], "used": clip_share(h["share"]), "clipped": clip_share(h["share"]) != h["share"], "defn": h["defn"]}
    return None


def project_state(rec: dict[str, Any], hist: dict[tuple[str, int], dict[str, Any]], bt: dict[str, Any], k: float, prev_hist: list[dict[str, Any]], gen: str | None) -> dict[str, Any]:
    st = rec["st"]
    by = rec.get("by_method") or {}
    adv = by.get("advance")
    banked = rec.get("banked")
    exp_raw = (rec.get("expected") or {}).get("votes")
    ed = next((p["count"] for p in ((banked or {}).get("parts") or []) if p["kind"] == "election_day"), None)
    out: dict[str, Any] = {"label": LABEL, "ok": False, "why": None, "ed": {"available": False, "why": ED_WHY, "checkins": ed}, "history": list(prev_hist)}
    sh = pick_share(st, hist)
    if not sh:
        out["why"] = "no history for this state"
        return out
    if not adv or not adv.get("count"):
        out["why"] = "no pre-Election Day count yet to project from"
        return out
    if adv.get("partial"):
        out["why"] = "the pre-Election Day count covers only part of the state"
        return out
    early = float(adv["count"])
    A = est_A(early, sh["share"])
    f = bt[sh["kind"]]["fit"]
    sel = bt["selection"]["variant"]
    M = exp_raw * k if exp_raw else None
    variant = "A"
    B = None
    if f and M:
        B = blend(A, M, f["w"])
        variant = sel
    proj = B if variant == "B" else A
    sd = (f["sdB"] if variant == "B" else f["sdA"]) if f else None
    # floors: ballots already banked (incl. Election Day check-ins), and banked + r x mail ballots sent/requested and not yet returned
    floors = []
    if banked and banked.get("value"):
        floors.append(("banked incl. Election Day check-ins", float(banked["value"])))
    o = rec.get("outstanding")
    r = R_DEFAULT
    if o and o.get("ok") and o.get("kind") == "outstanding":
        floors.append((f"banked + {r} x mail not yet returned", early + r * o["value"]))
    floor_v = max((v for _, v in floors), default=0.0)
    raw_proj = proj
    if proj < floor_v:
        proj = floor_v
    lo = hi = None
    if sd is not None:
        lo, hi = max(proj * math.exp(-Z80 * sd), floor_v), proj * math.exp(Z80 * sd)
        lo = min(lo, proj)
    ratio = proj / M if M else None
    ratio_raw = proj / exp_raw if exp_raw else None
    head = None
    if ratio is not None:
        d = 100 * (ratio - 1)
        head = f"heading {'above' if ratio > 1 else 'below'} the model's assumption by {abs(d):.1f} %" if abs(d) >= 0.05 else "heading in line with the model's assumption"
    prev_pt = next((h for h in reversed(prev_hist) if h["t"] != gen), None)
    delta = None
    if prev_pt:
        dT, dB = proj - prev_pt["v"], early - prev_pt["b"]
        dE = None if M is None or prev_pt.get("e") is None else M - prev_pt["e"]
        parts = [f"pre-Election Day ballots {'+' if dB >= 0 else '−'}{abs(round(dB)):,}"]
        parts.append("model expected unchanged" if dE is None or abs(dE) < 0.5 else f"model expected {'+' if dE >= 0 else '−'}{abs(round(dE)):,}")
        delta = {"since": prev_pt["t"], "d_total": round(dT), "d_total_pct": round(100 * dT / prev_pt["v"], 3) if prev_pt["v"] else None, "d_banked": round(dB), "d_expected": None if dE is None else round(dE),
                 "text": f"{'+' if dT >= 0 else '−'}{abs(round(dT)):,} since {prev_pt['t'][11:16]} UTC: " + ", ".join(parts)}
    inputs = [f"{int(early):,} pre-Election Day ballots (early + mail, {(adv.get('as_of') or '')[:10] or 'date not stated'})",
              f"early share of the final total in {sh['cycle']} ({'same-type cycle' if sh['kind'] == 'same' else 'fallback: previous cycle of the other type'}): {100 * sh['share']:.1f} %"
              + (f", clipped to {100 * sh['used']:.0f} %" if sh["clipped"] else "") + (f"; {sh['defn']}" if sh.get("defn") else "")]
    if exp_raw:
        inputs.append(f"model expected {int(exp_raw):,} two-party votes, x {k:.3f} (2024 ballots per two-party vote) = {int(M):,} ballots")
    inputs.append(f"mail late-return rate r = {r} (default: no state rate on disk)" + (f"; {int(o['value']):,} sent/requested and not yet returned" if o and o.get("ok") and o.get("kind") == "outstanding" else "; no outstanding-mail count for the floor"))
    if floors:
        inputs.append("floor: " + "; ".join(f"{n} = {int(v):,}" for n, v in floors) + (" (binding)" if raw_proj < floor_v else " (not binding)"))
    out.update(ok=True, proj_total=round(proj), low=None if lo is None else round(lo), high=None if hi is None else round(hi), level=LEVEL, variant=variant, A=round(A), B=None if B is None else round(B),
               expected=None if M is None else round(M), expected_raw=exp_raw, vs_expected=None if ratio is None else round(ratio, 4), vs_expected_raw=None if ratio_raw is None else round(ratio_raw, 4),
               heading=head, banked_adv=round(early), banked_share=round(banked["value"] / proj, 4) if banked and banked.get("value") else None,
               s_hat={"value": round(sh["used"], 4), "raw": round(sh["share"], 4), "cycle": sh["cycle"], "kind": sh["kind"], "clipped": sh["clipped"], "defn": sh["defn"]},
               r={"value": r, "source": "default, no state late-return rate on disk"}, floor=round(floor_v) if floor_v else None, floor_binding=raw_proj < floor_v, delta=delta, inputs=inputs,
               sd_log=None if sd is None else round(sd, 4), blend_w=None if not f else round(f["w"], 3))
    if gen:
        h = [x for x in out["history"] if x["t"] != gen] + [{"t": gen, "v": round(proj), "b": round(early), "e": None if M is None else round(M)}]
        out["history"] = sorted(h, key=lambda x: x["t"])[-HIST_CAP:]
    return out


def project(states: dict[str, dict[str, Any]], prev: dict[str, Any] | None, gen: str | None, root: Path) -> dict[str, Any]:
    """Sets states[st]["proj"] for every state and returns the proj_summary."""
    hist = load_hist(root)
    pres, kmap = load_pres(root)
    bt = run_backtest(hist, pres)
    prev_states = (prev or {}).get("states") or {}
    for st, rec in states.items():
        ph = ((prev_states.get(st) or {}).get("proj") or {}).get("history") or []
        rec["proj"] = project_state(rec, hist, bt, kmap.get(st, 1.0), ph, gen)
    ok = [r["proj"] | {"st": r["st"], "name": r["name"]} for r in states.values() if r["proj"]["ok"] and r["proj"].get("vs_expected") is not None]
    above = sorted((p for p in ok if p["vs_expected"] > 1 + INLINE_TOL), key=lambda p: -p["vs_expected"])
    below = sorted((p for p in ok if p["vs_expected"] < 1 - INLINE_TOL), key=lambda p: p["vs_expected"])
    sel = bt["selection"]
    tab = backtest_table(bt)
    return {"label": LABEL, "variant": sel["variant"], "selection": sel["why"], "level": LEVEL, "n_projected": sum(1 for r in states.values() if r["proj"]["ok"]),
            "n_no_history": sum(1 for r in states.values() if r["proj"].get("why") == "no history for this state"), "n_vs_expected": len(ok),
            "above": [p["st"] for p in above], "below": [p["st"] for p in below],
            "total_proj": sum(p["proj_total"] for p in ok), "total_expected": round(sum(p["expected"] for p in ok)),
            "backtest": tab, "estimator_C": {"available": False, "why": ED_WHY}, "county": {"available": False, "why": COUNTY_WHY},
            "notes": ["Early-vote definitions differ by state and year (mail only, in-person only or both); the backtest errors include those changes.",
                      "The model's expected votes are two-party votes; they are scaled to ballots by each state's 2024 total/two-party ratio before the blend and the comparison.",
                      "The blend weights and intervals come from the backtest only (earlier cycles); the floor by outstanding mail is not backtested (no historical outstanding counts)."]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    root = Path(ap.parse_args().root)
    hist = load_hist(root)
    pres, _ = load_pres(root)
    bt = run_backtest(hist, pres)
    for r in bt["same"]["rows"] + bt["any"]["rows"]:
        print({k: v for k, v in r.items()})
    print(bt["selection"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
