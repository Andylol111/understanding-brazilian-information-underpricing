"""Export Australia 2025 to the dashboard (site/data/au2025/: meta, timeline, units, stats, entry, arb).

Published: the AEC's own count (media feed archive, CC BY 4.0 as AEC website material), derived division series, the
model's probabilities and public Polymarket / Kalshi prices. Reads results/australia/v1/ (analysis/au_score.py) and
the observation pickle (analysis/au_observe.py); the map geometry is analysis/au_geo.py's.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/au_export.py
"""

from __future__ import annotations

import json
import pickle
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import australia as au
from brunder.paths import DATA, POLYMARKET, ROOT

sys.path.insert(0, str(Path(__file__).parent))
from decided_stats import annotate  # noqa: E402

RES = ROOT / "results" / "australia" / "v1"
RES2 = ROOT / "results" / "australia" / "v2"
OUT = ROOT / "site" / "data" / "au2025"
CFG = ROOT / "configs" / "au2025_v1.toml"
CFG2 = ROOT / "configs" / "au2025_v2.toml"
COL = {"ALP": "#d9353c", "LNC": "#1d4f9c", "GRN": "#2ca24a", "IND": "#d98c1f", "OTH": "#8a8a8a"}
PNAME = {"ALP": "Labor", "LNC": "Coalition", "GRN": "Greens", "IND": "Independent", "OTH": "Other party"}
NO = "#9a9a9a"
STATE_NAMES = {"ACT": "Australian Capital Territory", "NSW": "New South Wales", "NT": "Northern Territory",
               "QLD": "Queensland", "SA": "South Australia", "TAS": "Tasmania", "VIC": "Victoria", "WA": "Western Australia"}
AEST = "Australia/Sydney"


def dump(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def num(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def party_id(group: str | None) -> str:
    if group is None:
        return "OTH"
    if group in ("ALP", "LNC", "GRN"):
        return group
    return "IND" if group.startswith("IND:") else "OTH"


def short(race: dict) -> tuple[str, str, str]:
    """(yes label, no label, yes colour) of a race."""
    rid = race["race_id"]
    rule = race["rule"]
    party = rule.get("party")
    base = {"ALP": "Labor", "LNC": "Coalition", "GRN": "Greens", "OTH": "Other"}.get(party, "")
    col = COL.get(party, "#555555")
    if rid.startswith(("most-", "pm-")):
        return {"most-oth": "Another party", "pm-albanese": "Albanese", "pm-dutton": "Dutton"}.get(rid, base), "Another party", col
    if rule["kind"] == "majority":
        return f"{base} majority", "No majority", col
    if rule["kind"] == "seats":
        lo, hi = rule["lo"], rule["hi"]
        txt = (f"{base} under {hi + 1}" if lo == 0 else f"{base} {lo} or more" if hi >= 150 else f"{base} {lo}–{hi}")
        return txt, "Other result", col
    if rule["kind"] == "margin":
        lo, hi = rule["lo"], rule["hi"]
        if lo >= 1:
            txt = f"Labor +{lo} or more" if hi >= 150 else f"Labor +{lo}–{hi}"
            return txt, "Other result", COL["ALP"]
        txt = f"Coalition +{-hi if hi < 0 else 0}–{-lo}" if hi != 0 else f"Coalition 0–{-lo}"
        if lo <= -150:
            txt = f"Coalition +{-hi} or more"
        return txt, "Other result", COL["LNC"]
    if rule["kind"] == "div":
        return race["name"].split(" (")[0], "No", col
    return race["name"], "No", col


def main() -> None:
    cfg = tomllib.loads(CFG.read_text(encoding="utf-8"))
    S = json.loads((RES / "stats.json").read_text())
    R = pd.read_csv(RES / "releases.csv", parse_dates=["t", "created"])
    R = R[R["n_pair"] >= 100].drop_duplicates("t", keep="last").set_index("t").sort_index()
    TK = pd.read_parquet(RES / "ticks.parquet")
    S2 = json.loads((RES2 / "stats.json").read_text())
    R2 = pd.read_csv(RES2 / "releases.csv", parse_dates=["t", "created"])
    R2 = R2[R2["n_pair"] >= 100].drop_duplicates("t", keep="last").set_index("t").sort_index()
    with open(RES / "frames.pkl", "rb") as f:
        frames = pickle.load(f)
    with open(DATA / "au" / "obs_2025.pkl", "rb") as f:
        d = pickle.load(f)
    St, fin = d["static"], d["final"]
    ids = sorted(St["divs"])
    close, end = pd.Timestamp(cfg["poll_close_utc"]), pd.Timestamp(cfg["night_end_utc"])
    times = pd.date_range(close, end, freq="1min")
    stp = lambda s: s.reindex(s.index.union(times)).ffill().reindex(times)  # noqa: E731

    # ---- races (grouped markets)
    races: dict[str, dict] = {}
    for m in cfg["markets"]:
        r = races.setdefault(m["race"], {"race_id": m["race"], "name": m["name"], "markets": [],
                                         "rule": {k: v for k, v in m.items() if k in ("kind", "party", "lo", "hi", "div", "groups")}})
        r["markets"].append(m)

    # ---- timeline
    timeline, ksf = {}, {}
    cnt = stp(R["counted"])
    for rid, r in races.items():
        pm = next((m for m in r["markets"] if m["platform"] == "polymarket"), None)
        ka = next((m for m in r["markets"] if m["platform"] == "kalshi"), None)
        key = lambda m: f"{m['platform']}:{m['market']}"  # noqa: E731
        lead = pm or ka
        P = stp(R["P:" + key(lead)])
        px = TK[TK["scope"] == key(pm)].set_index("t")["price"] if pm else None
        kx = TK[TK["scope"] == key(ka)].set_index("t")["price"] if ka else None
        main_race = rid == "most-alp"
        raw, sh, sd = (stp(R["raw2pp"]), stp(R["proj2pp"]), stp(R["sd2pp"])) if main_race else (None, None, None)
        P2 = stp(R2["P:" + key(lead)])
        sh2, sd2 = (stp(R2["proj2pp"]), stp(R2["sd2pp"])) if main_race else (None, None)
        timeline[rid] = [{"t": iso(t), "counted": num(cnt.iloc[i]), "raw": num(raw.iloc[i]) if main_race else None,
                          "share": num(sh.iloc[i]) if main_race else None, "sd": num(sd.iloc[i]) if main_race else None,
                          "P": num(P.iloc[i]), "P_sensitivity": num(P2.iloc[i]),
                          "share_sensitivity": num(sh2.iloc[i]) if main_race else None,
                          "sd_sensitivity": num(sd2.iloc[i]) if main_race else None,
                          "pm": num(px.iloc[i]) if px is not None else None,
                          "kalshi": num(kx.iloc[i]) if kx is not None else None} for i, t in enumerate(times)]
        ksf[rid] = kx
    dump("timeline.json", timeline)

    # ---- units: prior and one frame per minute at which a frame was published
    tot_hist = {i: St["divs"][i]["hist_tpp"] for i in ids}
    i1000 = lambda x: -1 if x is None or (isinstance(x, float) and np.isnan(x)) or x < 0 else int(round(1000 * x))  # noqa: E731
    prior = [i1000(tot_hist[i][1] / (tot_hist[i][1] + tot_hist[i][2])) if tot_hist[i] else -1 for i in ids]
    keep = [fr for fr in frames if fr["t"] in set(R.index)]
    uf, last_t = [], None
    for fr in keep:
        if last_t is not None and fr["t"] == last_t:
            uf.pop()
        last_t = fr["t"]
        uf.append({"t": iso(fr["t"]), "f": [int(round(1000 * x)) for x in fr["f"]], "a": [i1000(x) for x in fr["a"]],
                   "proj": [i1000(x) for x in fr["proj"]]})
    # the AEC's final two-party-preferred is the count's end state: published as the last frame
    fa = []
    for i in ids:
        tp = fin[i]["tpp"]
        fa.append(i1000(tp[1] / (tp[1] + tp[2])) if tp and tp[1] + tp[2] > 0 else -1)
    winner, margin = [], []
    for i in ids:
        tcp = fin[i]["tcp"]
        w = fin[i]["winner"]
        winner.append(party_id(w))
        v = sorted(tcp.values(), reverse=True)
        margin.append(int(round(1000 * (v[0] - v[1]) / sum(v))) if len(v) > 1 else 0)
    sid = lambda i: f"au-{St['divs'][i]['state'].lower()}-{i}"  # noqa: E731
    dump("units.json", {"unit_ids": [sid(i) for i in ids], "region": [St["divs"][i]["state"].lower() for i in ids],
                        "names": [St["divs"][i]["name"] for i in ids], "prior": prior,
                        "V0": [int(sum(tot_hist[i].values())) if tot_hist[i] else 0 for i in ids],
                        "winner": winner, "margin": margin,
                        "a": {"name": "Labor", "color": COL["ALP"]}, "b": {"name": "Coalition", "color": COL["LNC"]},
                        "frames": uf})

    # ---- stats
    settle_all = S["settle"]
    st_set, st_gate, st_cal, st_ee, st_cov = {}, {}, {}, {}, {}
    gate = pd.Timestamp(S["gate_1pct"])
    cv = S["coverage90"]["alp_seats"]
    for rid, r in races.items():
        pm = next((m for m in r["markets"] if m["platform"] == "polymarket"), None)
        ka = next((m for m in r["markets"] if m["platform"] == "kalshi"), None)
        k_pm = f"polymarket:{pm['market']}" if pm else None
        k_ka = f"kalshi:{ka['market']}" if ka else None
        s = settle_all[k_pm or k_ka]
        st_set[rid] = {"model": s["model"], "polymarket": settle_all[k_pm]["market"] if k_pm else None,
                       "kalshi": settle_all[k_ka]["market"] if k_ka else None, "raw": None, "contested": s["contested"],
                       "lead_min": s["lead_min"], "reach90_model": s["model_reach90"], "reach95_model": s["model_reach95"],
                       "reach90_polymarket": settle_all[k_pm]["mkt_reach90"] if k_pm else None,
                       "reach95_polymarket": settle_all[k_pm]["mkt_reach95"] if k_pm else None,
                       "reach90_kalshi": settle_all[k_ka]["mkt_reach90"] if k_ka else None,
                       "reach95_kalshi": settle_all[k_ka]["mkt_reach95"] if k_ka else None,
                       "outcome_a": s["outcome"] == "yes"}
        ms = pd.Timestamp(s["model"]) if s["model"] else None
        mt = pd.Timestamp(s["market"]) if s["market"] else None
        st_gate[rid] = {"gate": iso(gate), "model": s["model"], "polymarket": st_set[rid]["polymarket"],
                        "kalshi": st_set[rid]["kalshi"], "raw": None,
                        "lead": int(round((mt - ms).total_seconds() / 60)) if ms is not None and mt is not None else None,
                        "lead_vs": "polymarket" if k_pm else "kalshi", "market": (pm or ka)["market"]}
        st_cal[rid] = {"coverage90_1_95": round(cv["coverage90"], 3), "mean_z": round(cv["mean_z"], 2), "n": cv["n"]}
        st_ee[rid] = {c: {"model": e["model_2pp"], "raw": e["raw_2pp"]} for c, e in S["early_error"].items()}
        st_cov[rid] = {"1-95%": st_cal[rid]}
    cont = [v for v in settle_all.values() if v["contested"]]
    rcont = {rid for rid, v in st_set.items() if v["contested"]}
    leads = [v["lead_min"] for rid, v in st_set.items() if v["contested"] and v["lead_min"] is not None]
    summary = {"markets": len(races), "contested_markets": len(rcont),
               "median_lead_min": float(np.median(leads)) if leads else None,
               "model_first": sum(1 for x in leads if x > 0), "market_first": sum(1 for x in leads if x < 0),
               "settled_before_first_count": len(races) - len(rcont)}
    B = S["brier"]
    bc = B["contested"]
    acc = {"evidence": "not-preregistered", "share_error_pt": st_ee, "coverage90": st_cov,
           "pwin": {"races": bc["markets"], "minutes": bc["minutes"], "brier_model": bc["brier_model"],
                    "brier_market": bc["brier_market"],
                    "brier_diff": None if bc["brier_model"] is None else round(bc["brier_model"] - bc["brier_market"], 5),
                    "brier_diff_ci": bc["diff_ci_event_cluster"], "log_diff_ci": None},
           "pwin_all_markets": {"races": B["all"]["markets"], "minutes": B["all"]["minutes"],
                                "brier_model": B["all"]["brier_model"], "brier_market": B["all"]["brier_market"],
                                "brier_diff": B["all"]["diff"], "brier_diff_ci": B["all"]["diff_ci_event_cluster"],
                                "races_model_better_brier": B["all"]["markets_model_better"],
                                "races_market_better_brier": B["all"]["markets_market_better"]},
           "pwin_window": "contested = the markets the platform had not settled (>= 0.9 on the outcome to the end) before "
                          "the first two-candidate count; minutes from then to the last frame (04:00 AEST)",
           "outcome": "AEC final results (media feed, last archived message), which match every Polymarket and Kalshi resolution"}
    cal = json.loads((ROOT / "results" / "australia" / "calibration_2025_v1.json").read_text())
    cal2 = json.loads((ROOT / "results" / "australia" / "calibration_2025_v2.json").read_text())
    chk = json.loads((ROOT / "results" / "australia" / "calibration_2022_check.json").read_text())
    note = ("RETROSPECTIVE, not pre-registered: built after the election with the result and market resolutions known. "
            f"Settings were chosen on the {' and '.join(map(str, cal['fitted_on']))} count nights replayed from the same AEC archive "
            "in feed order; no 2025 count went into them.")
    reasons = {"capacity": "no trading rule was run in this replay", "profit": "no trading rule was run in this replay",
               "catchup": "not computed in this replay",
               "arbitrage": "no thin-spread Polymarket-Kalshi pairs through the count were checked",
               "calibration": "minutes from 1% to 95% of the expected polling-place votes counted; Labor's final seat count "
                              "against the model's 90% range (the same range applies to every seat market)"}
    sources = [
        {"name": "Australian Electoral Commission: 2025 federal election media feed archive (Detailed Light and Preload, "
                 "one message every 90 seconds), election id 31496; the 2022, 2019 and 2016 archives for calibration and "
                 "polling-place priors",
         "url": "ftp://mediafeedarchive.aec.gov.au/31496/",
         "licence": "AEC website material: Creative Commons Attribution 4.0 (https://www.aec.gov.au/footer/Copyright.htm). "
                    "(c) Commonwealth of Australia (Australian Electoral Commission)"},
        {"name": "AEC Tally Room archive and Media Feed user guide (feed structure; two-candidate-preferred and 'Maverick' rules)",
         "url": "https://results.aec.gov.au/", "licence": "as above"},
        {"name": "Federal Electoral Divisions (March 2025), AEC boundaries, simplified; copy in Geoscience Australia's Digital "
                 "Atlas of Australia",
         "url": "https://services-ap1.arcgis.com/ypkPEy1AmwPKGNNv/ArcGIS/rest/services/Federal_Electoral_Boundaries_2025/FeatureServer/0",
         "licence": "Creative Commons Attribution 4.0 (item licence). This product incorporates data that is (c) Commonwealth of "
                    "Australia (Australian Electoral Commission) 2025."},
        {"name": "Polymarket price history (public CLOB API, 1-minute) and market questions (Gamma API)",
         "url": "https://polymarket.com/event/next-prime-minister-of-australia", "licence": "Polymarket public data"},
        {"name": "(c) Kalshi: 1-minute candles of the Australian election markets (published only as the per-minute "
                 "probability line and settle times; terms in docs/kalshi.md)",
         "url": "https://kalshi.com", "licence": "Kalshi data terms; see docs/kalshi.md"}]
    fs = S["final_seats"]
    cv1, cv2 = S["coverage90"]["alp_seats"]["coverage90"], S2["coverage90"]["alp_seats"]["coverage90"]
    notes = [
        note,
        "Two model versions are shown. v1 is the headline (solid line). v2 is a sensitivity (thin dashed line). It was written "
        "after v1 had been scored on the 2025 count and its misses looked at. Its settings still come only from 2022 and 2019.",
        f"The map has 150 divisions, on the 2025 boundaries. The count is replayed from the AEC's own election-night feed: "
        f"{S['n_frames']} messages, each used from the time the AEC created it. Times are Canberra time (AEST). The first "
        f"two-candidate count came at {pd.Timestamp(S['first_count']).tz_convert(AEST):%H:%M}. The last message shown is "
        f"{pd.Timestamp(S['last_frame']).tz_convert(AEST):%H:%M}.",
        "'Counted' is the share of votes counted at polling places, measured against what the same places counted last time. "
        "Postal, absent and provisional votes come in later. They are not in the night feed.",
        "The map shows Labor's share of the two-party vote (Labor against the Coalition). In about 20 divisions the real contest "
        "was not Labor against the Coalition. The 'winning party' view shows the real winner of each division.",
        "How the model works: it finds the swing in the polling places already counted. It compares each place with the same place "
        "at the last election. It blends that swing with the swing in the state and in the country. The winner of each division is "
        "the candidate ahead after preferences. The AEC picks the two candidates for that count. In 21 divisions its pick was "
        "wrong all night. There the model uses the Labor against Coalition count and the first-preference leaders. "
        "8,000 random draws turn this into seat counts.",
        "Questions modelled: most seats, majority, hung parliament, Labor and Coalition seat bands, the Labor minus Coalition seat "
        "gap, Greens 4 or more, and four seat markets (Dickson, Grayndler, Bennelong, Brisbane). Prime minister questions are "
        "modelled as 'the party with the most seats gets the prime minister'. The count cannot test that in a hung parliament. "
        "Not modelled: minority governments, other prime minister names, the national first-preference vote, and small parties.",
        f"The model was too sure of itself. Labor's final seat count was inside its 90% range in only {100 * cv1:.0f}% of minutes "
        f"(v1) and {100 * cv2:.0f}% (v2). Read 'the model was sure first' with that in mind.",
        f"Final seats (AEC): Labor {fs['ALP']}, Coalition {fs['LNC']}, Greens {fs['GRN']}, others {fs['OTH']}. Labor's final "
        f"two-party share: {100 * S['final_2pp_alp']:.2f}%.",
        "Evidence: " + note]
    mk_races = []
    for rid, r in races.items():
        yes, no, col = short(r)
        mkts = []
        for m in r["markets"]:
            if m["platform"] == "polymarket":
                ev = json.loads((POLYMARKET / m["event"] / "event.json").read_text())
                q = next(x["question"] for x in ev["markets"] if x["slug"] == m["market"])
                mkts.append({"platform": "polymarket", "id": m["market"], "event": m["event"], "question": q, "yes_is": "a"})
            else:
                ev = json.loads((DATA / "raw" / "kalshi" / "australia" / m["event"] / "event.json").read_text())
                mm = next(x for x in ev["markets"] if x["ticker"] == m["market"])
                title = (mm.get("title") or ev.get("title") or "").strip()
                sub = mm.get("yes_sub_title") or ""
                mkts.append({"platform": "kalshi", "id": m["market"], "yes_is": "a",
                             "question": f"{title} {sub} (Kalshi {m['market']})".replace("  ", " ")})
        mk_races.append({"race_id": rid, "name": r["name"], "region": None,
                         "a": {"id": "yes", "name": yes, "color": col}, "b": {"id": "no", "name": no, "color": NO},
                         "markets": mkts})
    meta = {"id": "au2025", "name": "Australia, House of Representatives election", "date": "2025-05-03",
            "tz": AEST, "poll_close_utc": cfg["poll_close_utc"], "geometry": "geo/au_divisions.topo.json",
            "geometry_object": "divisions", "unit_key": "unit_id", "count_timeline": True,
            "regions": [{"id": s.lower(), "name": STATE_NAMES[s]} for s in sorted({v["state"] for v in St["divs"].values()})],
            "parties": [{"id": k, "name": PNAME[k], "color": COL[k]} for k in ("ALP", "LNC", "GRN", "IND", "OTH")],
            "races": mk_races,
            "sensitivity": {"label": "v2: added after the 2025 count was scored",
                            "config": "configs/au2025_v2.toml"},
            "map": {"prior": "Labor share of the two-party-preferred vote at the 2022 election on the 2025 boundaries "
                             "(AEC notional, from the media feed Preload)",
                    "final": "AEC final two-party-preferred result and two-candidate-preferred winners (media feed, last message)"},
            "sources": sources, "notes": notes}
    dump("meta.json", meta)
    sens = {"config": "configs/au2025_v2.toml", "chosen_after_result": True, "headline": False,
            "fitted_on": cal2["fitted_on"], "settings": cal2["settings"], "fit_scores": cal2["fit_scores"],
            "coverage90": S2["coverage90"], "early_error": S2["early_error"], "brier": S2["brier"],
            "settle": {k: {kk: v[kk] for kk in ("model", "market", "contested", "lead_min", "outcome", "platform", "race")}
                       for k, v in S2["settle"].items()},
            "note": "v2 adds floors under the state and national swing errors and compares a third candidate with the leading "
                    "major party; written after v1 had been scored on the 2025 count and its misses inspected"}
    fshare = {"Labor two-party-preferred": round(100 * S["final_2pp_alp"], 2)}
    stats = {"calibration": st_cal, "settle": st_set, "settle_summary": summary, "settle_gated": st_gate, "catchup": None,
             "early_error": st_ee, "capacity": None, "arbitrage": None,
             "evidence": {k: "not-preregistered" for k in ("settle", "settle_gated", "calibration", "early_error")},
             "evidence_note": note, "final": {"seats": fs, "two_party_preferred_pct": fshare,
                                              "first_count_information": S["first_count"], "count_complete": S["last_frame"]},
             "settle_gated_note": "per race: gate = first minute with >= 1% of the expected polling-place votes counted; "
                                  "model = max(model settle, gate); lead = market settle - gated model settle, minutes "
                                  "(settle = staying >= 0.9 on the outcome to the last frame, 04:00 AEST); market = Polymarket "
                                  "where the race has one, else Kalshi",
             "reasons": reasons, "accuracy": acc, "profit": None,
             "brier_by_counted": {"window": B["window"], "all_markets": B["by_counted_bin"],
                                  "contested_markets": B["by_counted_bin_contested"]},
             "settle_all_markets": {k: {kk: v[kk] for kk in ("model", "market", "contested", "lead_min", "outcome", "platform", "race")}
                                    for k, v in settle_all.items()},
             "sensitivity_v2": sens,
             "calibration_settings": {"fitted_on": cal["fitted_on"], "settings": cal["settings"],
                                      "national_swings": cal["national_swings"], "fit_scores": cal["fit_scores"],
                                      "out_of_sample_check": {"fitted_on": chk["fitted_on"], "checked": chk.get("check_year"),
                                                              "settings": chk["settings"], "scores": chk.get("check_scores")}}}
    dump("stats.json", stats)
    annotate(OUT)                                           # decided-before-count classification, as for every election
    st = json.loads((OUT / "stats.json").read_text())
    lc = st["lead_contested"]
    entry = {"id": "au2025", "name": "Australia, House of Representatives election", "date": "2025-05-03", "country": "AU",
             "status": "replay", "unit_level": "division", "n_units": len(ids), "evidence": "not-preregistered",
             "summary": (f"Retrospective replay from the AEC's own election-night count ({S['n_frames']} messages, polling place by "
                         f"polling place). Of {st['n_markets']} questions, {st['n_decided_before_count']} were already at 90% or more on "
                         f"the right answer before the count, including the $8.6M prime minister market. On the "
                         f"{lc['n_contested_markets']} contested questions the model reached 90% on the right answer first on "
                         f"{lc['model_first_gate1pct']}, the market first on {lc['market_first_gate1pct']}, a median "
                         f"{lc['median_lead_min_gate1pct']:.0f} minutes earlier. But it was too sure of itself: its 90% range for "
                         f"Labor's seats held {100 * cv1:.0f}% of the time.")}
    dump("entry.json", entry)
    index = ROOT / "site" / "data" / "elections.json"           # append-only: this election's own entry, replaced on re-run
    lst = json.loads(index.read_text(encoding="utf-8"))
    lst = [e for e in lst if e["id"] != entry["id"]] + [entry]
    index.write_text(json.dumps(lst, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    dump("arb.json", [])
    for f in sorted(OUT.glob("*.json")):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
