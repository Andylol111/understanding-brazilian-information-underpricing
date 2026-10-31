"""France 2022 presidential election nights: retrospective replays (site/data/fr2022r1, fr2022r2).

Everything published is official (the ministry's national result page as the Internet Archive captured it, and the
ministry's final results from data.gouv.fr). Settings come from the earlier French nights only (analysis/fr_calibrate.py ->
data_public/fr_calibration_v1.json). No Polymarket or Kalshi price history exists for these two nights (the old 2022
Polymarket markets return no history from the CLOB API, Kalshi had no French market), so the pages show the model only.

Headline P = the tau mixture over the earlier nights; sensitivity `point` = one tau (the pre-mixture repo rule).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/fr_export.py
    uv run python analysis/decided_stats.py fr2022r1 fr2022r2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sens_export as se  # noqa: E402

from brunder import france as fr  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402
from brunder.paths import ROOT  # noqa: E402

CAL = json.loads((ROOT / "data_public" / "fr_calibration_v1.json").read_text())
SITE = ROOT / "site" / "data"
COL = {"MACRON": "#e6b800", "LE_PEN": "#0b3a75", "MELENCHON": "#c8102e", "OTHER": "#8a8a8a"}
NAME = {"MACRON": "Macron", "LE_PEN": "Le Pen", "MELENCHON": "Mélenchon", "OTHER": "Other candidates"}
NDRAW = 4000
END_AFTER_CLOSE = pd.Timedelta(hours=12)

# race id -> (name, A id, B id, kind). kind: "lp_jlm" Le Pen ahead of Mélenchon, "mac_first" Macron first,
# "lp_second" Le Pen exactly second, "mac_win" Macron wins round 2, "mac_54" Macron 54% or more.
RACES = {
    "pr22r1": {"lepen-vs-melenchon": ("Le Pen finishes ahead of Mélenchon (a place in the runoff)", "LE_PEN", "MELENCHON", "lp_jlm"),
               "macron-first": ("Macron finishes first", "MACRON", "LE_PEN", "mac_first"),
               "lepen-second": ("Le Pen finishes second", "LE_PEN", "OTHER", "lp_second")},
    "pr22r2": {"macron-wins": ("Macron wins the runoff", "MACRON", "LE_PEN", "mac_win"),
               "macron-54": ("Macron wins 54% or more of the runoff vote", "MACRON", "OTHER", "mac_54")},
}
SHARE_RACES = {"lepen-vs-melenchon", "macron-first", "macron-wins"}
HEADLINE = {"pr22r1": "lepen-vs-melenchon", "pr22r2": "macron-wins"}
EID = {"pr22r1": "fr2022r1", "pr22r2": "fr2022r2"}
TITLE = {"pr22r1": "France, presidential election 2022, first round", "pr22r2": "France, presidential election 2022, runoff"}


def num(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def two_way(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a / (a + b)


def race_state(night: str, row: pd.Series, cats: list[str], taus: tuple[float, ...], rng: np.random.Generator) -> dict:
    """Per race at one state: P, projected share, 90% band, raw (counted) share. `row` = one `night_frames` row."""
    share = np.array([row[f"share_{c}"] for c in cats])
    sd0 = np.array([row[f"sd0_{c}"] for c in cats])
    cnt = float(row["counted"])
    out = {}
    if night == "pr22r2":
        sd = np.array([fr.sd_at(sd0[:1], cnt, t)[0] for t in taus])
        s = share[0]
        lo, hi = no.mixture_band(np.array([s]), np.array([2 * sd0[0]]), np.array([2 * (1 - cnt)]), np.array([1.0]), taus)
        out["macron-wins"] = {"P": float(np.mean(norm.cdf((s - 0.5) / sd))), "share": s, "lo": float(lo[0]), "hi": float(hi[0])}
        out["macron-54"] = {"P": float(np.mean(norm.cdf((s - 0.54) / sd)))}
        return out
    d = fr.mixture_draws(share, sd0, cnt, taus, NDRAW, rng)
    M, L, J = d[:, 0], d[:, 1], d[:, 2]
    lj, ml = two_way(L, J), two_way(M, L)
    out["lepen-vs-melenchon"] = {"P": float((L > J).mean()), "share": float(two_way(share[1], share[2])),
                                 "lo": float(np.quantile(lj, 0.05)), "hi": float(np.quantile(lj, 0.95))}
    out["macron-first"] = {"P": float(((M > L) & (M > J)).mean()), "share": float(two_way(share[0], share[1])),
                           "lo": float(np.quantile(ml, 0.05)), "hi": float(np.quantile(ml, 0.95))}
    out["lepen-second"] = {"P": float((((M > L) & (L > J)) | ((J > L) & (L > M))).mean())}
    return out


def outcome_a(night: str, rid: str, fin: dict) -> bool:
    """Official outcome of a race's question (A true), from the final shares."""
    m, l, j = fin["MACRON"], fin["LE_PEN"], fin.get("MELENCHON", 0.0)
    return {"lepen-vs-melenchon": l > j, "macron-first": m > l and m > j, "lepen-second": (m > l > j) or (j > l > m),
            "macron-wins": m > 0.5, "macron-54": m >= 0.54}[rid]


def run(night: str, taus: tuple[float, ...], cfg: dict) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    p = fr.Params(kappa=cfg["kappa"], sigma_u=cfg["sigma_u"])
    pr = fr.night_prior(night)
    frames = fr.night_frames(night, p, pr)
    states = fr.read_night(night)
    rng = np.random.default_rng(20220410)
    rows: dict[str, list[dict]] = {k: [] for k in RACES[night]}
    for i, row in frames.iterrows():
        st = states.iloc[i]
        rs = race_state(night, row, pr.cats, taus, rng)
        cnt_votes = {c: float(st[c]) for c in pr.cats[:-1]}
        raw = {}
        if night == "pr22r1":
            raw = {"lepen-vs-melenchon": cnt_votes["LE_PEN"] / (cnt_votes["LE_PEN"] + cnt_votes["MELENCHON"]),
                   "macron-first": cnt_votes["MACRON"] / (cnt_votes["MACRON"] + cnt_votes["LE_PEN"])}
        else:
            raw = {"macron-wins": cnt_votes["MACRON"] / float(st["exprimes"])}
        for rid, v in rs.items():
            rows[rid].append({"t": row["t"], "counted": row["counted"], "raw": raw.get(rid), **v})
    return frames, {k: pd.DataFrame(v) for k, v in rows.items()}


def grid(night: str, series: dict[str, pd.DataFrame]) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    close = pd.Timestamp(fr.NIGHTS[night]["poll_close_utc"])
    times = pd.date_range(close, close + END_AFTER_CLOSE, freq="1min")
    out = {}
    for rid, df in series.items():
        s = df.drop_duplicates("t", keep="last").set_index("t").sort_index()
        s = s.reindex(s.index.union(times)).ffill().reindex(times)
        out[rid] = s
    return times, out


def main() -> None:
    for night in fr.ORDER[2:]:
        cfg = CAL["targets"][night]
        eid = EID[night]
        pr = fr.night_prior(night)
        fin = dict(zip(pr.cats, fr.final_shares(night)))
        fin_two = ({"lepen-vs-melenchon": two_way(fin["LE_PEN"], fin["MELENCHON"]), "macron-first": two_way(fin["MACRON"], fin["LE_PEN"])}
                   if night == "pr22r1" else {"macron-wins": fin["MACRON"]})
        frames, mix = run(night, tuple(cfg["tau_mixture"]), cfg)
        _, pnt = run(night, (cfg["tau_point"],), cfg)
        times, G = grid(night, mix)
        _, GP = grid(night, pnt)
        first = frames["t"].iloc[0]
        timeline = {}
        for rid, (_, a, b, kind) in RACES[night].items():
            g = G[rid]
            tl = []
            for t, v in g.iterrows():
                if pd.isna(v.get("P")):
                    tl.append({"t": iso(t), "counted": 0.0 if t < first else None, "raw": None, "share": None, "sd": None, "P": None,
                               "pm": None, "kalshi": None})
                    continue
                sd = (v["hi"] - v["lo"]) / (2 * se.Z90) if rid in SHARE_RACES else None
                tl.append({"t": iso(t), "counted": num(v["counted"]), "raw": num(v["raw"]) if rid in SHARE_RACES else None,
                           "share": num(v["share"]) if rid in SHARE_RACES else None, "sd": num(sd, 5), "P": num(v["P"]),
                           "pm": None, "kalshi": None})
            timeline[rid] = tl
        out = SITE / eid
        out.mkdir(parents=True, exist_ok=True)

        def dump(name, obj):
            (out / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

        dump("timeline.json", timeline)

        # ---- headline stats (no market: model only), same metrics as the sensitivities
        calib, replay = {}, {}
        for rid, (_, a, b, kind) in RACES[night].items():
            g = G[rid].reset_index(names="t")
            has = rid in SHARE_RACES
            m = se._metrics(g.assign(pm=np.nan), "share" if has else None, "lo" if has else None, "hi" if has else None, "P",
                            fin_two.get(rid), outcome_a(night, rid, fin), None, g[g["counted"] >= 0.01]["t"].iloc[0])
            replay[rid] = m
            if has:
                calib[rid] = {"coverage90_1_95": m.get("coverage90_1_95"), "mean_z": m.get("mean_z"),
                              "n": int(g["counted"].between(0.01, 0.95, inclusive="right").sum())}
        prior_final = {NAME[c]: round(100 * float(fin[c]), 2) for c in pr.cats}
        states = fr.read_night(night)
        n_states = len(states)
        rnd = "first round" if night == "pr22r1" else "runoff"
        note = ("RETROSPECTIVE, not pre-registered: built after the election with the result known. Every setting (the swing shrinkage, the "
                "commune-level noise and the reporting-order error tau, a mixture of the values earlier nights needed) comes from earlier "
                "French nights, never from this one; see docs/france.md.")
        notes = [
            note,
            f"REAL TIMES, NATIONAL TOTALS ONLY: the ministry's results site shows the whole country in one page. The Internet Archive "
            f"captured {n_states} distinct versions of it during this night, from {iso(states['t'].iloc[0])[11:16]} UTC to "
            f"{iso(states['t'].iloc[-1])[11:16]} UTC, each with every candidate's votes and the registered voters already counted "
            f"({states['pct_inscrits'].iloc[0]:.0f}% to {states['pct_inscrits'].iloc[-1]:.0f}%). A version is dated by its first capture, so the "
            "count may have been public a few minutes earlier. The first captured version already had a large part of the vote in; the "
            "exit-poll estimate at 18:00 UTC is not used.",
            "NO MARKET LINE: Polymarket listed markets on this election, but its price API returns no history for them any more "
            "(checked 2026-10-31) and Kalshi had no French market in 2022, so this page shows the model only and no lead over a market "
            "can be measured. The Polymarket markets that existed are listed in docs/france.md.",
            "The map is not drawn: the ministry's page gives national totals, not which communes are in. The model assumes communes are "
            "counted from the smallest to the largest (small rural communes first, big cities and French citizens abroad last). Every "
            "departure from that order is the reporting-order error tau.",
            f"Prior: each commune's result in the {'2017 first round' if night == 'pr22r1' else '2022 first round (the two runoff candidates only)'}; "
            "the 2022 registered voters (the electoral roll) set each commune's size.",
            f"90% bands use the equal-weight mixture of tau from the earlier nights ({', '.join(f'{x:g}' for x in cfg['tau_mixture'])}); "
            f"the sensitivity `point` uses the single value {cfg['tau_point']:g}. Only two to three earlier nights exist, one of them with two "
            "usable frames, so the mixture is thin; the 2017 first round and runoff and the 2022 first round were archived, 2012 was not.",
            f"Official final (Ministère de l'Intérieur, results by polling station), share of valid votes: "
            f"{', '.join(f'{k} {v}%' for k, v in prior_final.items())}.",
            "Share and raw (main race) are the first candidate's share of the two compared candidates' votes (round 1) or of all valid "
            "votes (runoff); the two other races have no share line except Macron first.",
        ]
        poll_close = fr.NIGHTS[night]["poll_close_utc"]
        races_meta = []
        for rid, (name, a, b, kind) in RACES[night].items():
            races_meta.append({"race_id": rid, "name": name, "region": None,
                               "a": {"id": a.lower(), "name": NAME[a] if a != "OTHER" else "Yes", "color": COL[a]},
                               "b": {"id": b.lower(), "name": NAME[b] if b != "OTHER" else "No", "color": COL[b] if b != "OTHER" else "#9a9a9a"},
                               "markets": []})
        parties = [{"id": c.lower(), "name": NAME[c], "color": COL[c]} for c in pr.cats]
        sources = [
            {"name": "Ministère de l'Intérieur: results site, national page FE.html, as captured by the Internet Archive "
                     f"({n_states} distinct versions on {fr.NIGHTS[night]['date']} and the night after)",
             "url": "https://www.resultats-elections.interieur.gouv.fr/presidentielle-2022/FE.html",
             "licence": "official page of a French ministry, read from Internet Archive copies; only the derived series is published"},
            {"name": "Ministère de l'Intérieur: final results by polling station, presidential 2012, 2017 and 2022 (data.gouv.fr)",
             "url": "https://www.data.gouv.fr/datasets/election-presidentielle-des-10-et-24-avril-2022-resultats-definitifs-du-1er-tour",
             "licence": "Licence Ouverte (2012, 2017); the 2022 datasets carry no licence tag on data.gouv.fr (public ministry open data)"},
            {"name": "Ministère de l'Intérieur: 2017 election-night national pages (calibration only)",
             "url": "https://web.archive.org/", "licence": "as above; only capture counts and derived errors are published"}]
        meta = {"id": eid, "name": TITLE[night], "date": fr.NIGHTS[night]["date"], "country": "FR", "tz": "Europe/Paris",
                "poll_close_utc": poll_close, "geometry": None, "geometry_object": None, "unit_key": "unit_id",
                "count_timeline": True, "regions": [], "parties": parties, "races": races_meta, "sources": sources, "notes": notes,
                "n_units": 0}
        dump("meta.json", meta)
        dump("units.json", {"unit_ids": [], "region": [], "names": [], "prior": [], "V0": [], "winner": [], "margin": [],
                            "a": {"name": "Macron", "color": COL["MACRON"]}, "b": {"name": "Le Pen", "color": COL["LE_PEN"]}, "frames": []})
        dump("arb.json", [])
        reasons = {k: "no market prices exist for this election" for k in
                   ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage")}
        reasons["calibration"] = "no market line; model-only coverage is in stats.replay"
        stats = {"calibration": calib, "settle": None, "catchup": None, "early_error": None, "capacity": None, "arbitrage": None,
                 "evidence": {"calibration": "not-preregistered"}, "evidence_note": note, "reasons": reasons,
                 "n_markets": 0, "n_decided_before_count": 0,
                 "market_classification": {"definition": "eventual outcome >= 0.90 at the first minute at/after official poll close and "
                                                         "never below 0.80 afterwards", "by_race": {}},
                 "lead_contested": {"n": 0, "n_contested_markets": 0, "median_lead_min": None, "median_lead_min_gate1pct": None,
                                    "model_first": 0, "market_first": 0, "model_first_gate1pct": 0, "market_first_gate1pct": 0, "by_race": {}},
                 "replay": {"settings": {k: cfg[k] for k in ("kappa", "sigma_u", "tau_mixture", "tau_point", "earlier_nights")},
                            "tau_needed_by_earlier_night": cfg["tau_needed"], "frames_by_earlier_night": cfg["n_frames_by_night"],
                            "by_race": replay, "states": n_states, "first_state": iso(states["t"].iloc[0]),
                            "last_state": iso(states["t"].iloc[-1]),
                            "final_share_pct": prior_final},
                 "final": {"share_pct": prior_final}}
        dump("stats.json", stats)
        n_in = int(frames["counted"].between(0.01, 0.95, inclusive="right").sum())
        hl = HEADLINE[night]
        cv = calib[hl]["coverage90_1_95"]
        entry = {"id": eid, "name": TITLE[night], "date": fr.NIGHTS[night]["date"], "country": "FR", "status": "replay",
                 "unit_level": "national total", "n_units": None, "evidence": "not-preregistered",
                 "summary": (f"Retrospective replay from the ministry's own results page ({n_states} archived versions, national totals "
                             f"with the real times) with settings taken from the earlier 2017 nights. No market price history exists for "
                             f"this night, so the model is shown alone. Its 90% range for the main question held in {100 * cv:.0f}% of "
                             f"the {n_in} versions from 1% to 95% counted.")}
        dump("entry.json", entry)
        index = SITE / "elections.json"           # append-only: this election's own entry, replaced on re-run
        lst = json.loads(index.read_text(encoding="utf-8"))
        lst = [e for e in lst if e["id"] != entry["id"]] + [entry]
        index.write_text(json.dumps(lst, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

        # ---- the single-tau sensitivity next to the mixture headline
        ser, finals = {}, {}
        for rid in RACES[night]:
            g = GP[rid].reset_index(names="t")
            cols = ["t", "P"] + (["share", "lo", "hi"] if rid in SHARE_RACES else [])
            ser[rid] = g[cols].dropna(subset=["P"])
            finals[rid] = fin_two.get(rid)
        se.write_sensitivity(eid, "point", f"one tau ({cfg['tau_point']:g}) instead of the mixture of earlier nights' tau",
                             "analysis/fr_calibrate.py",
                             "The model is run once with the single reporting-order error that the repo's pre-mixture rule picks from the "
                             "same earlier nights (smallest value whose 90% bands cover 90% of the earlier frames and 75% on each earlier "
                             "night). The mixture is the headline; this line shows how much one number differs from it.", ser, finals, HEADLINE[night])
        print(eid, "written", {f.name: f"{f.stat().st_size / 1e3:.0f} kB" for f in out.glob("*.json")})


if __name__ == "__main__":
    main()
