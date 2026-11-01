"""Track-record scorecard: every replay and forward test scored the same way, from the published per-election site data.

Reads site/data/elections.json and, per election, meta.json / stats.json / timeline.json; reads the US 2016-2024 pre-count
backtest tables from docs/us_unified.md. Writes site/data/scorecard/{index,elections,backtest}.json.
Nothing is typed in by hand: every number on the page comes from these files.

    uv run python analysis/scorecard.py

Definitions (also written into index.json so the page can state them):
  * outcome        which side of a race won: stats.settle.outcome_a, else the final win chance (>= 0.995 or <= 0.005),
                   else the final raw share, else the final market price (same rule as site/decided.js).
  * calibration    every (race, minute) with 1% to 90% of the vote counted is one forecast "the first-named side wins" with
                   the model's win chance P. Forecasts fall in ten bins; the table gives the mean forecast and how often the
                   side did win. Each election has total weight 1 (its minutes spread equally), so a long night does not
                   count more than a short one.
  * share error    the projected share of the first-named side at the first minute with 1/5/10/25/50/75% counted, minus its
                   final share, in points (final = the official final result). The unadjusted count is scored alongside.
  * band coverage  share of minutes with 1% to 95% counted in which the final share lay inside the model's 90% band.
  * time to 90%    first minute from which the model and the Polymarket / Kalshi price stay at 0.90 or more on the eventual
                   winner, counted from the first minute with 1% counted. Lead = market minus model; markets already
                   "decided before the count" (site/decided.js) are left out of the lead.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
OUT = DATA / "scorecard"
DOC = ROOT / "docs" / "us_unified.md"

N_BINS = 10
CAL_FROM, CAL_TO = 0.01, 0.90
COV_FROM, COV_TO = 0.01, 0.95
GATE = 0.01
ERR_POINTS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75)
FINAL_COUNTED = 0.98
CONVERGED = 0.90
AGREE = 0.005
SETTLE_LEVEL = 0.90
DECIDED_CLOSE, DECIDED_FLOOR = 0.90, 0.80

# Elections that are retrospective sensitivities chosen after the result was known: their numbers are never pooled with
# headline replays (the election's own evidence_note in elections.json says so).
SENSITIVITY_ELECTIONS = {"mx2024_qc"}

# timeline columns of each model version: (probability, share, sd). The headline model is "v1".
HEADLINE = ("P", "share", "sd")
POST_HOC_VERSIONS = {
    "v2": ("P_sensitivity", "share_sensitivity", "sd_sensitivity"),
    "mix": ("P_sens_mix", "share_sens_mix", "sd_sens_mix"),
    "v3": ("P_sens_v3", "share_sens_v3", "sd_sens_v3"),
    "point": ("P_sens_point", "share_sens_point", "sd_sens_point"),
}
# forward tests register every version before the night; their versions are the P_v<k> columns
FORWARD_VERSIONS = {f"v{k}": (f"P_v{k}", f"share_v{k}", f"sd_v{k}") for k in (2, 3, 5)}

VERSION_NAMES = {
    "v1": "v1, the headline model",
    "v2": "v2, settings from earlier nights (chosen after the result)",
    "mix": "mixture over earlier nights (chosen after the result)",
    "v3": "v3, polls as the starting point (chosen after the result)",
    "point": "point-estimate variant (chosen after the result)",
}
FORWARD_NAMES = {"v1": "v1", "v2": "v2", "v3": "v3", "v5": "v5"}

HEADLINE_GROUPS = {"replay_prespecified", "replay_other", "replay_in_sample"}
GROUP_TITLES = {
    "forward": "Registered forward tests",
    "replay_prespecified": "Replays with settings frozen before the data was examined",
    "replay_other": "Other replays: settings chosen on earlier elections, not frozen in advance",
    "replay_in_sample": "In-sample replays: settings were chosen on this very data",
    "sensitivity": "Sensitivities chosen after the result",
}
EVIDENCE_TEXT = {
    "forward": "settings frozen before the outcome existed",
    "pre-specified": "settings frozen before this data was examined; the outcome was already known",
    "in-sample": "settings were chosen on this data",
    "not-preregistered": "settings chosen on earlier elections and scored once, not frozen in the repository before scoring",
    "model-only": "no market prices to compare with",
    "market-only": "no count model was run; market prices against the official result only",
}


# --------------------------------------------------------------------------------------------------------- helpers
def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def minutes_between(a, b):
    return round((parse_t(b) - parse_t(a)) / 60)


def num(v):
    return v is not None and not isinstance(v, bool) and v == v


def outcome_a(rows, settle=None):
    """True if the first-named side won, False if the other did, None if unknown (site/decided.js: outcomeA)."""
    if settle and settle.get("outcome_a") is not None:
        return bool(settle["outcome_a"])
    final = next((d for d in reversed(rows) if any(num(d.get(k)) for k in ("P", "P_v2", "raw", "share"))), None)
    if final is not None:
        p = final.get("P_v2") if num(final.get("P_v2")) else final.get("P")
        if num(p) and (p <= 0.005 or p >= 0.995):
            return p > 0.5
        share = final.get("raw") if num(final.get("raw")) else final.get("share")
        if num(share) and share != 0.5:
            return share > 0.5
    market = next((d["pm"] for d in reversed(rows) if num(d.get("pm"))), None)
    return None if market is None or market == 0.5 else market > 0.5


def winner_p(row, a_won, col):
    p = row.get(col)
    return None if not num(p) or a_won is None else (p if a_won else 1 - p)


def decided_before_count(rows, poll_close, a_won):
    """Winner priced >= 0.90 at the first archived minute at/after poll close and never below 0.80 afterwards."""
    if a_won is None or not poll_close:
        return False
    close = parse_t(poll_close)
    after = [d for d in rows if num(d.get("pm")) and parse_t(d["t"]) >= close]
    if not after:
        return False
    first = winner_p(after[0], a_won, "pm")
    floor = min(winner_p(d, a_won, "pm") for d in after)
    return first >= DECIDED_CLOSE and floor >= DECIDED_FLOOR


def stays_above(rows, a_won, col, start, level=SETTLE_LEVEL):
    """Time of the first row from `start` on after which the winner's price stays >= level (rows without a price skipped)."""
    at = None
    for i in range(len(rows) - 1, start - 1, -1):
        p = winner_p(rows[i], a_won, col)
        if p is None:
            continue
        if p < level:
            break
        at = rows[i]["t"]
    return at


def gate_index(rows):
    for i, d in enumerate(rows):
        if num(d.get("counted")) and d["counted"] >= GATE:
            return i
    return None


def official_final(rows, early_error):
    """The official final share implied by a published early-error entry {"5%": {"model": m, "raw": e}, ...}: the raw count at
    that many percent counted minus the raw error, in points. The median over the points; None without such an entry."""
    est = []
    for key, v in (early_error or {}).items():
        if not v or not num(v.get("raw")):
            continue
        x = float(key.rstrip("%")) / 100
        row = next((d for d in rows if num(d.get("counted")) and d["counted"] >= x and num(d.get("raw"))), None)
        if row is not None:
            est.append(row["raw"] - v["raw"] / 100)
    return median(est)


def final_share(rows, early_error=None):
    """Final first-named share. The official final implied by the published early-error entry when there is one; else the raw
    count of the last row with 98%+ counted, when it agrees with the projection's own basis (within AGREE); else the headline
    projection at the last row with 90%+ counted, which has converged on the count by then."""
    official = official_final(rows, early_error)
    if official is not None:
        return official
    last = next((d for d in reversed(rows) if num(d.get("counted")) and d["counted"] >= FINAL_COUNTED and num(d.get("raw"))), None)
    if last is not None and (not num(last.get("share")) or abs(last["raw"] - last["share"]) <= AGREE):
        return last["raw"]
    last = next((d for d in reversed(rows) if num(d.get("counted")) and d["counted"] >= CONVERGED and num(d.get("share"))), None)
    return None if last is None else last["share"]


def median(xs):
    return statistics.median(xs) if xs else None


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def r(v, nd=4):
    return None if v is None else round(v, nd)


# ------------------------------------------------------------------------------------------------- per-race scoring
def calibration_obs(rows, a_won, col):
    """(forecast, outcome 0/1) for every minute with CAL_FROM..CAL_TO counted."""
    if a_won is None:
        return []
    return [(d[col], 1.0 if a_won else 0.0) for d in rows
            if num(d.get(col)) and num(d.get("counted")) and CAL_FROM <= d["counted"] <= CAL_TO]


def band_coverage(rows, fin, share_col, sd_col):
    """Fraction of minutes with COV_FROM..COV_TO counted in which the final share lay in share +- 1.645 sd."""
    if fin is None:
        return None
    hits = n = 0
    for d in rows:
        if num(d.get(share_col)) and num(d.get(sd_col)) and num(d.get("counted")) and COV_FROM <= d["counted"] <= COV_TO:
            n += 1
            hits += abs(d[share_col] - fin) <= 1.645 * d[sd_col]
    return None if n == 0 else (hits / n, n)


def share_errors(rows, fin, share_col):
    """{point: (model error, raw error)} in points of the first-named share at each counted level."""
    out = {}
    if fin is None:
        return out
    for x in ERR_POINTS:
        row = next((d for d in rows if num(d.get("counted")) and d["counted"] >= x), None)
        if row is None or row["counted"] > 2 * x or not num(row.get(share_col)):
            continue
        raw = row.get("raw")
        out[x] = (100 * (row[share_col] - fin), 100 * (raw - fin) if num(raw) else None)
    return out


def race_times(rows, a_won, col):
    """Times to 90% on the winner for the model version `col`, Polymarket and Kalshi, from the 1% gate on."""
    g = gate_index(rows)
    if g is None or a_won is None:
        return None
    return {"model": stays_above(rows, a_won, col, g), "polymarket": stays_above(rows, a_won, "pm", g),
            "kalshi": stays_above(rows, a_won, "kalshi", g)}


# ---------------------------------------------------------------------------------------------- election scoring
def new_bins():
    return [{"w": 0.0, "wp": 0.0, "wy": 0.0, "n": 0, "races": set()} for _ in range(N_BINS)]


def bin_of(p):
    return min(N_BINS - 1, max(0, int(p * N_BINS)))


def versions_for(election, rows_by_race):
    """{version id: (P, share, sd) columns} that have data in this election."""
    cols = {"v1": HEADLINE}
    cols.update(FORWARD_VERSIONS if election["evidence"] == "forward" else POST_HOC_VERSIONS)
    have = {}
    for vid, c in cols.items():
        if any(num(d.get(c[0])) for rows in rows_by_race.values() for d in rows):
            have[vid] = c
    return have


def has_count(rows_by_race):
    return any(num(d.get("counted")) for rows in rows_by_race.values() for d in rows)


def score_election(entry, meta, stats, timeline):
    """Scores of one election, per model version. Returns the election record (JSON-ready, with calibration bins)."""
    rows_by_race = timeline
    races = {x["race_id"]: x for x in meta.get("races", [])}
    settle = (stats or {}).get("settle") or {}
    early = (stats or {}).get("early_error") or {}
    poll_close = meta.get("poll_close_utc")
    outcomes = {rid: outcome_a(rows, settle.get(rid)) for rid, rows in rows_by_race.items()}
    counted = has_count(rows_by_race)
    rec = {"id": entry["id"], "name": entry.get("name"), "date": entry.get("date"), "country": entry.get("country"),
           "evidence": entry.get("evidence"), "status": entry.get("status"), "n_races": len(rows_by_race),
           "has_count_timeline": counted, "versions": {}}
    has_market = any(num(d.get("pm")) for rows in rows_by_race.values() for d in rows)
    rec["has_market"] = has_market
    if not counted:
        return rec, {}
    cal = {}
    labels = {s["key"]: s.get("label") for s in meta.get("sensitivities", [])}
    for vid, (pc, sc, dc) in versions_for(entry, rows_by_race).items():
        bins = new_bins()
        obs_n = 0
        per_race = {}
        for rid, rows in rows_by_race.items():
            obs = calibration_obs(rows, outcomes[rid], pc)
            obs_n += len(obs)
            per_race[rid] = obs
        briers, races_used = [], 0
        for rid, obs in per_race.items():
            if obs:
                races_used += 1
        err = {x: [] for x in ERR_POINTS}
        raw_err = {x: [] for x in ERR_POINTS}
        bias = {x: [] for x in ERR_POINTS}
        cov, cov_n = [], 0
        times = {}
        for rid, rows in rows_by_race.items():
            for p, y in per_race[rid]:
                w = 1.0 / obs_n
                b = bins[bin_of(p)]
                b["w"] += w
                b["wp"] += w * p
                b["wy"] += w * y
                b["n"] += 1
                b["races"].add(rid)
                briers.append(w * (p - y) ** 2)
            fin = final_share(rows, early.get(rid))
            for x, (m, rw) in share_errors(rows, fin, sc).items():
                err[x].append(abs(m))
                bias[x].append(m)
                if rw is not None:
                    raw_err[x].append(abs(rw))
            c = band_coverage(rows, fin, sc, dc)
            if c:
                cov.append(c[0])
                cov_n += c[1]
            t = race_times(rows, outcomes[rid], pc)
            if t:
                times[rid] = t
        v = {"id": vid, "label": FORWARD_NAMES.get(vid) if entry["evidence"] == "forward" else VERSION_NAMES[vid],
             "n_races_scored": races_used, "n_minutes": obs_n, "brier": r(sum(briers)) if obs_n else None,
             "coverage90": r(mean(cov)) if cov else None, "coverage_minutes": cov_n,
             "share_error": {f"{round(100 * x)}%": {"mae": r(mean(err[x]), 3), "bias": r(mean(bias[x]), 3),
                                                     "raw_mae": r(mean(raw_err[x]), 3), "n": len(err[x])}
                             for x in ERR_POINTS if err[x]}}
        if labels.get(vid):
            v["election_label"] = labels[vid]
        # time to 90% against the markets
        rr = []
        for rid, t in times.items():
            decided = decided_before_count(rows_by_race[rid], poll_close, outcomes[rid])
            priced = any(num(d.get("pm")) or num(d.get("kalshi")) for d in rows_by_race[rid])
            lead = (minutes_between(t["model"], t["polymarket"]) if t["model"] and t["polymarket"] else None)
            lead_k = (minutes_between(t["model"], t["kalshi"]) if t["model"] and t["kalshi"] else None)
            rr.append({"race": rid, "name": (races.get(rid) or {}).get("name"), "priced": priced, "decided": decided,
                       "model": t["model"], "polymarket": t["polymarket"], "kalshi": t["kalshi"],
                       "lead_polymarket": lead, "lead_kalshi": lead_k})
        v["races"] = rr
        priced = [x for x in rr if x["priced"]]
        contested = [x for x in priced if not x["decided"]]
        leads = [x["lead_polymarket"] for x in contested if x["lead_polymarket"] is not None]
        v["market"] = {"n_markets": len(priced), "n_decided_before_count": sum(x["decided"] for x in priced),
                       "n_contested": len(contested), "n_compared": len(leads),
                       "model_first": sum(1 for x in leads if x > 0), "market_first": sum(1 for x in leads if x < 0),
                       "tied": sum(1 for x in leads if x == 0),
                       "median_lead_min": median(leads)} if priced else None
        rec["versions"][vid] = v
        cal[vid] = bins
    return rec, cal


def group_of(entry):
    if entry["id"] in SENSITIVITY_ELECTIONS:
        return "sensitivity"
    if entry.get("evidence") == "forward":
        return "forward"
    ev = entry.get("evidence")
    return {"pre-specified": "replay_prespecified", "in-sample": "replay_in_sample"}.get(ev, "replay_other")


def pool(records_cal, versions=None):
    """records_cal: list of (election record, {version: bins}). Pool equal-weight per election for each version.
    `versions` limits which versions are pooled."""
    out = {}
    for vid in sorted({v for _, cal in records_cal for v in cal}):
        if versions is not None and vid not in versions:
            continue

        bins = [{"w": 0.0, "wp": 0.0, "wy": 0.0, "n": 0, "elections": 0} for _ in range(N_BINS)]
        members = []
        for rec, cal in records_cal:
            if vid not in cal:
                continue
            members.append((rec, rec["versions"][vid]))
            for i, b in enumerate(cal[vid]):
                for k in ("w", "wp", "wy", "n"):
                    bins[i][k] += b[k]
                bins[i]["elections"] += 1 if b["n"] else 0
        if not members:
            continue
        n_e = len(members)
        table = [{"lo": i / N_BINS, "hi": (i + 1) / N_BINS,
                  "forecast": r(b["wp"] / b["w"]) if b["w"] else None,
                  "observed": r(b["wy"] / b["w"]) if b["w"] else None,
                  "weight": r(b["w"] / n_e), "n_minutes": b["n"], "n_elections": b["elections"]} for i, b in enumerate(bins)]
        errs = {}
        for key in {k for _, v in members for k in v["share_error"]}:
            xs = [(v["share_error"][key]["mae"], v["share_error"][key]["raw_mae"]) for _, v in members if key in v["share_error"]]
            raw = [b for _, b in xs if b is not None]
            errs[key] = {"mae": r(mean([a for a, _ in xs]), 3), "raw_mae": r(mean(raw), 3) if raw else None, "n_elections": len(xs)}
        errs = dict(sorted(errs.items(), key=lambda kv: int(kv[0][:-1])))
        covs = [v["coverage90"] for _, v in members if v["coverage90"] is not None]
        briers = [v["brier"] for _, v in members if v["brier"] is not None]
        mk = [v["market"] for _, v in members if v["market"]]
        leads = [x["lead_polymarket"] for _, v in members for x in v["races"]
                 if x["priced"] and not x["decided"] and x["lead_polymarket"] is not None]
        out[vid] = {"n_elections": n_e, "elections": [rc["id"] for rc, _ in members], "calibration": table,
                    "brier": r(mean(briers)), "coverage90": r(mean(covs)), "n_coverage_elections": len(covs), "share_error": errs,
                    "market": {"n_elections": len(mk), "n_markets": sum(m["n_markets"] for m in mk),
                               "n_decided_before_count": sum(m["n_decided_before_count"] for m in mk),
                               "n_compared": len(leads), "model_first": sum(1 for x in leads if x > 0),
                               "market_first": sum(1 for x in leads if x < 0), "tied": sum(1 for x in leads if x == 0),
                               "median_lead_min": median(leads)} if mk else None}
    return out


# ------------------------------------------------------------------------------------------------------ backtest
def parse_md_tables(text):
    """Markdown tables of `text` as {title, columns, rows}; title is the paragraph just above the table."""
    tables, para, gap, lines = [], [], False, text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[-| :]+\|$", lines[i + 1]):
            cols = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                rows.append({c: _cell(v) for c, v in zip(cols, cells)})
                i += 1
            tables.append({"title": " ".join(para), "columns": cols, "rows": rows})
            para, gap = [], False
            continue
        if line.startswith("#"):
            para, gap = [], False
        elif not line.strip():
            gap = True
        else:
            if gap:
                para = []
            para.append(line.strip())
            gap = False
        i += 1
    return tables


def _cell(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def section(text, start, end):
    a = text.index(start)
    b = text.index(end, a + len(start)) if end else len(text)
    return text[a:b]


def backtest(doc_path=DOC):
    text = doc_path.read_text(encoding="utf-8")
    state = parse_md_tables(section(text, "## 3. Backtest", "### County priors"))
    county = parse_md_tables(section(text, "### County priors", "## 4. House"))
    house = parse_md_tables(section(text, "## 4. House", "## 5. Early vote"))
    return {"source": "docs/us_unified.md", "note_file": "docs/us_unified.md",
            "sections": [{"id": "statewide", "tables": state}, {"id": "county", "tables": county}, {"id": "house", "tables": house}]}


# ------------------------------------------------------------------------------------------------------- driver
def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def build(data=DATA, doc=DOC):
    elections = load(data / "elections.json")
    records, scored, pending = [], {}, []
    for e in elections:
        d = data / e["id"]
        if e.get("status") == "scheduled":
            pending.append({"id": e["id"], "name": e.get("name"), "date": e.get("date"), "country": e.get("country"),
                            "evidence": e.get("evidence")})
            continue
        if not (d / "timeline.json").exists():
            continue
        meta = load(d / "meta.json")
        stats = load(d / "stats.json") if (d / "stats.json").exists() else {}
        rec, cal = score_election(e, meta, stats, load(d / "timeline.json"))
        rec["group"] = group_of(e)
        records.append(rec)
        scored[e["id"]] = cal
    groups = {}
    for gid in GROUP_TITLES:
        ids = [r_["id"] for r_ in records if r_["group"] == gid]
        members = [(r_, scored[r_["id"]]) for r_ in records if r_["group"] == gid and scored[r_["id"]]]
        if gid == "sensitivity":
            groups[gid] = {"title": GROUP_TITLES[gid], "elections": ids, "pooled": pool(members, versions={"v1"})}
            continue
        groups[gid] = {"title": GROUP_TITLES[gid], "elections": ids, "pooled": pool(members, versions={"v1"})}
    # versions chosen after the result: pooled over every headline replay that has them, next to the headline model on
    # exactly the same elections
    headline = [(r_, scored[r_["id"]]) for r_ in records if r_["group"] in HEADLINE_GROUPS and scored[r_["id"]]]
    versions = {}
    for vid in POST_HOC_VERSIONS:
        have = [(rc, cal) for rc, cal in headline if vid in cal]
        if have:
            versions[vid] = {"label": VERSION_NAMES[vid], "same_elections_headline": pool(have, versions={"v1"})["v1"],
                             "pooled": pool(have, versions={vid})[vid]}
    groups["sensitivity"]["versions"] = versions
    index = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "groups": groups, "pending_forward": pending,
             "evidence_text": EVIDENCE_TEXT,
             "definitions": {"calibration_counted": [CAL_FROM, CAL_TO], "coverage_counted": [COV_FROM, COV_TO], "gate": GATE,
                             "error_points": list(ERR_POINTS), "bins": N_BINS, "settle_level": SETTLE_LEVEL,
                             "decided": {"close": DECIDED_CLOSE, "floor": DECIDED_FLOOR}},
             "version_names": {**VERSION_NAMES}}
    return index, records, backtest(doc)


def main():
    index, records, bt = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    (OUT / "elections.json").write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    (OUT / "backtest.json").write_text(json.dumps(bt, separators=(",", ":")), encoding="utf-8")
    print(f"scorecard: {len(records)} scored elections, {len(index['pending_forward'])} forward tests pending", file=sys.stderr)


if __name__ == "__main__":
    main()
