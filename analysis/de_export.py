"""Export Germany 2025 to the dashboard (site/data/de2025/: meta, timeline, units, stats, entry).

Everything published is official (Bundeswahlleiterin results pages and open data, Datenlizenz Deutschland -
Namensnennung 2.0; constituency geometry (c) Geobasis-DE / BKG) or public Polymarket / Kalshi prices, so the count over
time is shown (count_timeline true). Reads results/germany/ from analysis/de_score.py; the map geometry is
analysis/de_geo.py's.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_export.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from brunder import germany as g
from brunder import kalshi
from brunder.paths import POLYMARKET, RAW, ROOT

RES = ROOT / "results" / "germany"
OUT = ROOT / "site" / "data" / "de2025"
CFG = ROOT / "configs" / "de2025_v1.toml"
COL = {"Union": "#5a6b78", "AfD": "#009ee0", "SPD": "#e3000f", "GRÜNE": "#46962b", "Die Linke": "#be3075",
       "BSW": "#a0185a", "FDP": "#d4a300", "Übrige": "#8a8a8a"}
PID = {"Union": "CDU_CSU", "AfD": "AFD", "SPD": "SPD", "GRÜNE": "GRUENE", "Die Linke": "LINKE", "BSW": "BSW",
       "FDP": "FDP", "Übrige": "OTH"}
PNAME = {"Union": "CDU/CSU", "AfD": "AfD", "SPD": "SPD", "GRÜNE": "Greens", "Die Linke": "Die Linke", "BSW": "BSW",
         "FDP": "FDP", "Übrige": "Other"}
NO = "#9a9a9a"
KQ = {"KXGERMANYPARTY-25-CDU": "Yes if the CDU/CSU wins the most seats in the 2025 German Bundestag election. (Kalshi KXGERMANYPARTY-25-CDU)",
      "KXGERMANYPARTY-25-AFD": "Yes if the AfD wins the most seats in the 2025 German Bundestag election. (Kalshi KXGERMANYPARTY-25-AFD)",
      "KXGER2ND-25-AFD": "Yes if the AfD finishes second in the 2025 German Bundestag election. (Kalshi KXGER2ND-25-AFD)"}
MK = "will-{}-win-the-most-seats-in-the-next-german-election"
M = {"union_most": MK.format("union"), "afd_most": MK.format("afd"),
     "afd_2nd": "will-afd-win-the-second-most-seats-in-the-next-german-election",
     "bsw5": "bsw-wins-5-or-more-of-vote-in-german-election", "fdp5": "fdp-wins-5-or-more-of-vote-in-german-election",
     "left5": "will-the-left-wins-5-or-more-of-vote-in-german-election",
     "union28": "will-the-cducsu-win-between-28-and-30-of-the-vote-in-the-german-electi",
     "afd20": "will-the-afd-win-between-20-and-22-of-the-vote-in-the-german-e",
     "spd15": "will-the-spd-win-15-20-of-the-vote-in-the-german-election",
     "gr15": "will-the-greens-win-over-15-of-the-vote-in-the-german-election"}
# race id -> (name, A (id, name, colour), B, P market key, [(market key, yes_is)], party whose share the race tracks,
#             Kalshi tickers [(ticker, yes_is)])
RACES = {
    "most-union": ("CDU/CSU wins the most seats (share: CDU/CSU of CDU/CSU + AfD)", ("union", "CDU/CSU", COL["Union"]),
                   ("afd", "AfD", COL["AfD"]), "union_most", [("union_most", "a"), ("afd_most", "b")], "Union",
                   [("KXGERMANYPARTY-25-CDU", "a"), ("KXGERMANYPARTY-25-AFD", "b")]),
    "second-afd": ("AfD finishes second", ("yes", "AfD second", COL["AfD"]), ("no", "another party", NO), "afd_2nd",
                   [("afd_2nd", "a")], "AfD", [("KXGER2ND-25-AFD", "a")]),
    "bsw5": ("BSW wins 5% or more of the vote", ("yes", "5% or more", COL["BSW"]), ("no", "under 5%", NO), "bsw5",
             [("bsw5", "a")], "BSW", []),
    "fdp5": ("FDP wins 5% or more of the vote", ("yes", "5% or more", COL["FDP"]), ("no", "under 5%", NO), "fdp5",
             [("fdp5", "a")], "FDP", []),
    "left5": ("Die Linke wins 5% or more of the vote", ("yes", "5% or more", COL["Die Linke"]), ("no", "under 5%", NO),
              "left5", [("left5", "a")], "Die Linke", []),
    "union28": ("CDU/CSU wins 28-30% of the vote", ("yes", "28-30%", COL["Union"]), ("no", "other", NO), "union28",
                [("union28", "a")], "Union", []),
    "afd20": ("AfD wins 20-22% of the vote", ("yes", "20-22%", COL["AfD"]), ("no", "other", NO), "afd20",
              [("afd20", "a")], "AfD", []),
    "spd15": ("SPD wins 15-20% of the vote", ("yes", "15-20%", COL["SPD"]), ("no", "other", NO), "spd15",
              [("spd15", "a")], "SPD", []),
    "greens15": ("Greens win over 15% of the vote", ("yes", "over 15%", COL["GRÜNE"]), ("no", "15% or less", NO), "gr15",
                 [("gr15", "a")], "GRÜNE", []),
}


def dump(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def cet(t: str) -> str:
    return pd.Timestamp(t).tz_convert("Europe/Berlin").strftime("%H:%M")


def num(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def kalshi_series(ticker: str, times: pd.DatetimeIndex) -> pd.Series:
    f = RAW / "kalshi" / "germany" / f"{ticker}.parquet"
    c = pd.read_parquet(f)
    return kalshi.prob_from_candles(c, times)


def main() -> None:
    cfg = g.load_config(CFG)
    p = g.params_of(cfg)
    S = json.loads((RES / "stats.json").read_text())
    R = pd.read_csv(RES / "releases.csv", parse_dates=["t"]).set_index("t").sort_index()
    TK = pd.read_parquet(RES / "ticks.parquet")
    night = g.read_night(g.RAW_DE / "wayback")
    wk, la = g.read_kerg2(g.RAW_DE / "open" / "kerg2.csv")
    umr = g.read_umrechnung(g.RAW_DE / "open" / "btwkr25_umrechnung_btw21.csv")
    prior = g.make_prior(umr, wk["electorate"], cfg["model"]["bsw_c"])
    names = pd.read_csv(g.RAW_DE / "open" / "btw25_wahlkreisnamen_utf8.csv", sep=";",
                        encoding="utf-8-sig", comment="#").set_index("WKR_NR")
    qs = {m["market"]: m for m in cfg["markets"]}
    for k, v in M.items():                                    # prefixes -> full market slugs
        M[k] = next(m for m in qs if m.startswith(v))
    times = pd.date_range(pd.Timestamp(cfg["poll_close_utc"]), pd.Timestamp(cfg["night_end_utc"]), freq="1min")
    final = wk[g.ALL].div(wk["valid"], axis=0)
    last_page = pd.Timestamp(S["last_page"])

    # ---- timeline: one row per minute from poll close (18:00 CET) to 07:00 CET the next morning
    step = lambda s: s.reindex(s.index.union(times)).ffill().reindex(times)  # noqa: E731
    timeline, kal = {}, {}
    for rid, (_, _, _, pkey, mks, party, kt) in RACES.items():
        slug = M[pkey]
        px = TK[TK["scope"] == slug].set_index("t")["price"]
        pa = step(R[f"P:{slug}"])
        cnt = step(R["counted"])
        if rid == "most-union":
            vals = [step(R["union_two_way_raw"]), step(R["union_two_way"]), step(R["union_two_way_sd"])]
        else:
            vals = [pd.Series(np.nan, index=times)] * 3
        k = kalshi_series(kt[0][0], times) if kt else None
        if k is not None:
            k = k.where(times >= R.index.min())
        kal[rid] = k
        timeline[rid] = [{"t": iso(t), "counted": num(cnt.iloc[i]), "raw": num(vals[0].iloc[i]), "share": num(vals[1].iloc[i]),
                          "sd": num(vals[2].iloc[i]), "P": num(pa.iloc[i]), "pm": num(px.iloc[i], 4),
                          "kalshi": num(k.iloc[i], 4) if k is not None else None} for i, t in enumerate(times)]
    dump("timeline.json", timeline)

    # ---- units: prior and one frame per minute at which the official pages changed
    ids = sorted(prior.index)
    uid = {n: f"de-{g.LANDS[int(prior.loc[n, 'land'])].lower()}-{n}" for n in ids}
    tw = lambda a, b: a / (a + b)  # noqa: E731
    i1000 = lambda x: -1 if pd.isna(x) else int(round(1000 * x))  # noqa: E731
    pr_two = tw(umr["Union"], umr["AfD"])
    fin_two = tw(wk["Union"], wk["AfD"])
    frames = []
    for t in R.index:
        st = g.state_at(night, prior, t)
        r = g.project(st, night, prior, p)
        known = st["known"]
        proj = pd.Series(tw(r["s_wk"][:, 0], r["s_wk"][:, 1]), index=r["ids"])
        kn_two = tw(known["Union"], known["AfD"])
        f = [1000 if n in known.index else 0 for n in ids]
        a = [i1000(kn_two[n]) if n in known.index else -1 for n in ids]
        pj = [i1000(kn_two[n] if n in known.index else proj[n]) for n in ids]
        if t >= last_page:                                 # count complete: the official final for every Wahlkreis
            f, a, pj = [1000] * len(ids), [i1000(fin_two[n]) for n in ids], [i1000(fin_two[n]) for n in ids]
        frames.append({"t": iso(t), "f": f, "a": a, "proj": pj})
    # the last national page is stamped between two minutes; make sure the complete-count frame exists
    if pd.Timestamp(frames[-1]["t"]) < last_page.ceil("min"):
        frames.append({"t": iso(last_page.ceil("min")), "f": [1000] * len(ids), "a": [i1000(fin_two[n]) for n in ids],
                       "proj": [i1000(fin_two[n]) for n in ids]})
    winner, margin = [], []
    for n in ids:
        s = final.loc[n].sort_values(ascending=False)
        winner.append(PID[s.index[0]])
        margin.append(i1000(s.iloc[0] - s.iloc[1]))
    dump("units.json", {"unit_ids": [uid[n] for n in ids], "region": [g.LANDS[int(prior.loc[n, "land"])].lower() for n in ids],
                        "names": [str(names.loc[n, "WKR_NAME"]).strip() for n in ids],
                        "prior": [i1000(pr_two[n]) for n in ids],
                        "V0": [int(umr.loc[n, "Union"] + umr.loc[n, "AfD"]) for n in ids],
                        "winner": winner, "margin": margin,
                        "a": {"name": "CDU/CSU", "color": COL["Union"]}, "b": {"name": "AfD", "color": COL["AfD"]},
                        "frames": frames})

    # ---- stats per race
    st_set, st_cal, st_ee, st_gate, st_err, st_cov = {}, {}, {}, {}, {}, {}
    gate = pd.Timestamp(S["gate_1pct"])
    for rid, (_, _, _, pkey, mks, party, kt) in RACES.items():
        s = S["settle"][M[pkey]]
        k = kal[rid]
        yes = s["outcome"] == "yes"
        ks = None
        if k is not None:
            kk = (k if yes else 1 - k).dropna()
            bad = kk[~(kk >= 0.9)]
            ks = iso(kk.index[0] if not len(bad) else (None if bad.index[-1] >= kk.index[-1] else bad.index[-1] + pd.Timedelta(minutes=1)))
        st_set[rid] = {"model": s["model"], "polymarket": s["polymarket"], "kalshi": ks, "raw": s["raw"],
                       "contested": s["contested"], "lead_min": s["lead_min"], "reach90_model": s["model_reach90"],
                       "reach95_model": s["model_reach95"], "reach90_polymarket": s["pm_reach90"],
                       "reach95_polymarket": s["pm_reach95"]}
        ms = pd.Timestamp(s["model"]) if s["model"] else None
        ps = pd.Timestamp(s["polymarket"]) if s["polymarket"] else None
        st_gate[rid] = {"gate": iso(gate), "model": s["model"], "polymarket": s["polymarket"], "kalshi": ks, "raw": s["raw"],
                        "lead": int(round((ps - ms).total_seconds() / 60)) if ms is not None and ps is not None else None,
                        "lead_vs": "polymarket", "market": M[pkey]}
        cv = S["coverage90"][party]
        st_cal[rid] = {"coverage90_1_95": round(cv["coverage90"], 3), "mean_z": round(cv["mean_z"], 2), "n": cv["n"]}
        ee = {}
        for c in ("1%", "5%", "10%", "25%"):
            e = S["early_error"][c]
            if rid == "most-union":
                ee[c] = {"model": e["model_two_way_union"], "raw": e["raw_two_way_union"]}
            else:
                ee[c] = {"model": e["model"][party], "raw": e["raw"][party]}
        st_ee[rid] = ee
        st_err[rid] = {c: {**v, } for c, v in ee.items()}
        st_cov[rid] = {"1-95%": st_cal[rid]}
    leads = [v["lead_min"] for v in S["settle"].values() if v["contested"] and v["lead_min"] is not None]
    summary = {"markets": len(S["settle"]), "contested_markets": sum(v["contested"] for v in S["settle"].values()),
               "median_lead_min": float(np.median(leads)) if leads else None,
               "model_first": sum(1 for v in S["settle"].values() if v["contested"] and v["lead_min"] is not None and v["lead_min"] > 0),
               "market_first": sum(1 for v in S["settle"].values() if v["contested"] and v["lead_min"] is not None and v["lead_min"] < 0),
               "settled_before_first_count": sum(1 for v in S["settle"].values() if not v["contested"])}
    B = S["brier"]
    acc = {"evidence": "not-preregistered", "share_error_pt": st_err, "coverage90": st_cov,
           "pwin": {"races": B["contested"]["markets"], "minutes": B["contested"]["minutes"],
                    "brier_model": B["contested"]["brier_model"], "brier_market": B["contested"]["brier_market"],
                    "brier_diff": round(B["contested"]["brier_model"] - B["contested"]["brier_market"], 5),
                    "brier_diff_ci": None, "log_diff_ci": None},
           "pwin_all_markets": {"races": B["all"]["markets"], "minutes": B["all"]["minutes"],
                                "brier_model": B["all"]["brier_model"], "brier_market": B["all"]["brier_market"],
                                "brier_diff": B["all"]["diff"], "brier_diff_ci": B["all"]["diff_ci_event_cluster"],
                                "races_model_better_brier": B["all"]["markets_model_better"],
                                "races_market_better_brier": B["all"]["markets_market_better"]},
           "pwin_window": "contested = the markets Polymarket had not settled (>= 0.9 on the outcome to the end) before "
                          "the first count information; minutes from then to the last national page",
           "outcome": "Polymarket resolution (identical to the official final, Bundeswahlleiterin 14 March 2025)"}
    note = ("RETROSPECTIVE, not pre-registered: built after the election with the result and the market resolutions "
            "known. Model settings come only from the 2021 election night (Internet Archive copies of the 2021 result "
            "pages, 2017 result as prior), never from 2025 (configs/de2025_v1.toml).")
    reasons = {"capacity": "no trading rule was run in this replay", "profit": "no trading rule was run in this replay",
               "catchup": "not computed in this replay", "arbitrage": "Kalshi lists no thin-spread pairs through the "
               "count (its 'parties in parliament' markets closed 15:00 UTC, before polls closed)",
               "calibration": "minutes from 1% to 95% counted (electorate share); the party's projected final "
                              "second-vote share against the official final, 90% band from the 2021-calibrated sd",
               "settle.kalshi": "Kalshi traded only the 'most seats' and '2nd place' markets through the count, "
                                "thinly (12 to 23 one-minute candles each)"}
    sources = [
        {"name": "Die Bundeswahlleiterin: Bundestag election 2025, results pages bund-99 / land / Wahlkreis as captured by "
                 "the Internet Archive (23-24 Feb 2025)",
         "url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse.html",
         "licence": "official results pages of a federal authority; read from Internet Archive copies; the derived "
                    "per-minute series is published with attribution"},
        {"name": "Die Bundeswahlleiterin: final results by Wahlkreis (kerg2.csv), Wahlkreis names, 2021 result recomputed "
                 "on the 2025 constituencies",
         "url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/opendata.html",
         "licence": "Datenlizenz Deutschland - Namensnennung - Version 2.0 (https://www.govdata.de/dl-de/by-2-0); "
                    "(c) Die Bundeswahlleiterin, Wiesbaden 2024/2025"},
        {"name": "Die Bundeswahlleiterin / Bundeswahlleiter: 2021 election-night pages (Internet Archive) and 2021 "
                 "result with the 2017 result on the 2021 constituencies (calibration only)",
         "url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2021/ergebnisse.html",
         "licence": "Datenlizenz Deutschland - Namensnennung - Version 2.0 (kerg2.csv); pages as above"},
        {"name": "Wahlkreis geometry, generalised (c) Die Bundeswahlleiterin, Statistisches Bundesamt 2024; Grundlage der "
                 "Geoinformationen (c) Geobasis-DE / BKG 2024",
         "url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/wahlkreiseinteilung/downloads.html",
         "licence": "Datenlizenz Deutschland - Namensnennung - Version 2.0"},
        {"name": "Polymarket price history (public CLOB API, 1-minute) and market questions (Gamma API)",
         "url": "https://polymarket.com/event/germany-parliamentary-election", "licence": "Polymarket public data"},
        {"name": "(c) Kalshi: 1-minute candles of KXGERMANYPARTY-25-CDU, KXGERMANYPARTY-25-AFD, KXGER2ND-25-AFD "
                 "(published only as the per-minute probability line and settle times; terms in docs/kalshi.md)",
         "url": "https://kalshi.com", "licence": "Kalshi data terms; see docs/kalshi.md"}]
    fshare = {PNAME[a]: round(100 * float(S["final_share"][a]), 2) for a in g.ALL}
    notes = [
        note,
        "Units are the 299 Wahlkreise (2025 boundaries). The count over time is replayed from the Bundeswahlleiterin's "
        f"server-rendered result pages as the Internet Archive captured them: {len(night['bund'])} distinct national "
        f"pages, {sum(len(v) for v in night['land'].values())} Land pages and {len(night['wk'])} single-Wahlkreis pages "
        "(each with its own 'Zwischenergebnis' stamp; a Wahlkreis stamp is its arrival time). First information: "
        f"{S['first_frame'][11:16]} UTC ({cet(S['first_frame'])} CET, one Wahlkreis); national pages from 21:00 CET, "
        f"{len(night['bund'])} of them, 6 to 69 minutes apart (median 13.5); complete at {S['last_page'][11:16]} UTC "
        f"({cet(S['last_page'])} CET). Each page is used from its own stamp.",
        f"'Counted' is the share of the electorate of the Wahlkreise already reported. The map draws a Wahlkreis as "
        f"counted only when its own page was archived ({len(night['wk'])} of 299); the others are drawn at the "
        "projection, although some of them were also counted (the national pages say how many Wahlkreise were in).",
        "Share and raw (main race) are CDU/CSU's share of the CDU/CSU + AfD second votes; the other races have no "
        "share line. Map prior = the 2021 result recomputed on the 2025 constituencies (Bundeswahlleiterin).",
        "P for every market comes from 20,000 joint draws of the final second-vote shares of the eight party groups: "
        "projection + correlated normal errors (sd from the 2021 night). 'Most seats' and 'second most seats' use the "
        "order of the second-vote shares (seats are proportional to them among the parties above 5%).",
        "Markets not modelled: chancellor, coalition (they depend on bargaining, not on the count), 'Left in the "
        "Bundestag as a Fraktion' (needs the direct-mandate and seat count); 46 vote-share, threshold and seat-order "
        "markets are scored in the stats, 9 are drawn.",
        f"Official final (Bundeswahlleiterin, 14 March 2025), second votes: {', '.join(f'{k} {v}%' for k, v in fshare.items())}. "
        "BSW missed the 5% threshold by 0.02 points.",
        "Polymarket was already at or above 0.9 on the eventual outcome when polls closed on "
        f"{summary['settled_before_first_count']} of {summary['markets']} markets (exit polls at 18:00 CET are public "
        "information the count model does not use).",
        "Evidence: " + note]
    mk_races = []
    for rid, (name, a, b, pkey, mks, party, kt) in RACES.items():
        mkts = []
        for key, y in mks:
            slug = M[key]
            q = qs[slug]
            ev = json.loads((POLYMARKET / q["event"] / "event.json").read_text())
            question = next(x["question"] for x in ev["markets"] if x["slug"] == slug)
            mkts.append({"platform": "polymarket", "id": slug, "event": q["event"], "question": question, "yes_is": y})
        for tk, y in kt:
            mkts.append({"platform": "kalshi", "id": tk, "yes_is": y, "question": KQ[tk]})
        mk_races.append({"race_id": rid, "name": name, "region": None,
                         "a": {"id": a[0], "name": a[1], "color": a[2]}, "b": {"id": b[0], "name": b[1], "color": b[2]},
                         "markets": mkts})
    meta = {"id": "de2025", "name": "Germany, Bundestag election", "date": "2025-02-23", "tz": "Europe/Berlin",
            "poll_close_utc": cfg["poll_close_utc"], "geometry": "geo/de_wahlkreise.topo.json",
            "geometry_object": "wahlkreise", "unit_key": "unit_id", "count_timeline": True,
            "regions": [{"id": g.LANDS[i].lower(), "name": g.LAND_NAMES[g.LANDS[i]]} for i in sorted(g.LANDS)],
            "parties": [{"id": PID[a], "name": PNAME[a], "color": COL[a]} for a in g.ALL],
            "races": mk_races,
            "map": {"prior": "CDU/CSU share of CDU/CSU + AfD second votes, 2021 recomputed on the 2025 Wahlkreise (official)",
                    "final": "official 2025 result (Bundeswahlleiterin, kerg2.csv)"},
            "sources": sources, "notes": notes}
    dump("meta.json", meta)

    stats = {"calibration": st_cal, "settle": st_set, "settle_summary": summary, "settle_gated": st_gate,
             "catchup": None, "early_error": st_ee, "capacity": None, "arbitrage": None,
             "evidence": {k: "not-preregistered" for k in ("settle", "settle_gated", "calibration", "early_error")},
             "evidence_note": note, "final": {"second_vote_share_pct": fshare, "valid_votes": S["valid"],
                                              "first_count_information": S["first_frame"], "count_complete": S["last_page"]},
             "settle_gated_note": "per race (its first market): gate = first minute with >= 1% of the electorate counted; "
                                  "model = max(model settle, gate); lead = Polymarket settle - gated model settle, minutes "
                                  "(settle = staying >= 0.9 on the outcome to the end of the night, 07:00 CET)",
             "reasons": reasons, "accuracy": acc, "profit": None,
             "brier_by_counted": {"window": B["window"], "all_markets": B["by_counted_bin"],
                                  "markets_not_settled_before_first_count":
                                      B["by_counted_bin_markets_not_settled_before_first_frame"]},
             "settle_all_markets": {k: {kk: v[kk] for kk in ("model", "polymarket", "contested", "lead_min", "outcome")}
                                    for k, v in S["settle"].items()}}
    dump("stats.json", stats)

    bsw = S["settle"][M["bsw5"]]
    entry = {"id": "de2025", "name": "Germany, Bundestag election", "date": "2025-02-23", "country": "DE",
             "status": "replay", "unit_level": "Wahlkreis", "n_units": len(ids), "evidence": "not-preregistered",
             "summary": (f"Retrospective replay from the Bundeswahlleiterin's archived night pages ({len(night['bund'])} national "
                         f"pages, {len(night['wk'])} Wahlkreis pages). Polymarket was already above 0.9 on the outcome at "
                         f"poll close on {summary['settled_before_first_count']} of {summary['markets']} markets; on the "
                         f"{summary['contested_markets']} it had not settled, the count model settled first on "
                         f"{summary['model_first']} (BSW under 5%: {bsw['lead_min']:+.0f} min) and its probabilities "
                         f"scored worse than the market's on {B['all']['markets_market_better']} of {B['all']['markets']}.")}
    dump("entry.json", entry)
    dump("arb.json", [])                                     # no Polymarket-Kalshi pairs through the count
    for f in sorted(OUT.glob("*.json")):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
