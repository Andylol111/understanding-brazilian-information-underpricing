"""Export a retrospective replay of a past New Zealand election night to site/data/nz<year>/.

The Electoral Commission keeps no timestamped count archive, so the official VOTING-PLACE results of the night
(advance places first, then election-day places) are released in a SIMULATED order and clock
(``nz_replay.Order(..., "night")`` + ``nz_rehearsal.release_minutes``, seed 1; poll close 19:00 local).  At each
simulated minute the v3 model (``configs/nz2026_v3.toml`` settings, chosen on 2017 and 2020 only) sees only the ordinary
votes released so far; the prior is the previous election's electorate results, the poll prior is the mean of the polls
ending in the 21 days before election day, and the special-vote shift is the mean of the *other* training years.
Markets settle on the FINAL official result (seats include special votes), which is what the headline is scored against.
No Polymarket or Kalshi market existed for these elections, so the page is model only.

    uv run python analysis/nz_export_replay.py 2023 [--out site/data] [--draws 4000]
    uv run python analysis/nz_export_replay.py 2020
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import nz_backtest as B          # noqa: E402
import nz_rehearsal as RH        # noqa: E402
import nz_replay as R            # noqa: E402
from brunder import newzealand as nz   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "nz2026_v3.toml"
TRAIN = (2017, 2020)
SEED = 1
END_MIN = 225
MAORI = {"Hauraki-Waikato", "Ikaroa-Rāwhiti", "Tāmaki Makaurau", "Te Tai Hauāuru", "Te Tai Tokerau", "Te Tai Tonga", "Waiariki"}
CLOSE = {2020: datetime(2020, 10, 17, 6, 0, tzinfo=timezone.utc), 2023: datetime(2023, 10, 14, 6, 0, tzinfo=timezone.utc)}
DATE = {2020: "2020-10-17", 2023: "2023-10-14"}
SOURCES = {
    2020: "https://electionresults.govt.nz/electionresults_2020/",
    2023: "https://electionresults.govt.nz/electionresults_2023/",
}


def counts_for(order: R.Order, E, mask: np.ndarray) -> pd.DataFrame:
    """``Order.counts`` for an arbitrary set of released voting places."""
    K = len(E.party_keys)
    e = order.ei[mask]
    c = nz.empty_counts(pd.DataFrame(index=pd.RangeIndex(1, order.n_e + 1)), E.party_keys)
    places = np.bincount(e, minlength=order.n_e).astype(float)
    c["places"], c["places_total"] = places, order.places_total
    c["f"] = places / np.maximum(order.places_total, 1)
    for j, k in enumerate(E.party_keys):
        c[f"pv_{k}"] = np.bincount(e, weights=order.P[mask, j], minlength=order.n_e)
        c[f"cv_{k}"] = np.bincount(e, weights=order.C[mask, j], minlength=order.n_e)
    c["cv_IND"] = np.bincount(e, weights=order.C[mask, K], minlength=order.n_e)
    c["valid"] = sum(c[f"pv_{k}"] for k in E.party_keys)
    return c


def build(year: int, out: Path, draws: int) -> dict:
    cfg = nz.load_config(CONFIG)
    E = R.election(year)
    keys = list(E.party_keys)
    st = nz.settings(cfg)
    st = replace(st, draws=draws, poll_shares=B.poll_shares(year, keys),
                 special_shift={k: v for k, v in B.special_shift([y for y in TRAIN if y != year]).items() if k in keys})
    markets = []
    for m in cfg["markets"]:
        need = [m.get("party"), m.get("a"), m.get("b")]
        if all(p is None or p in keys for p in need):
            markets.append(m)
    units = R.units_for(E)
    units["layer"] = ["maori" if n in MAORI else "general" for n in units["name"]]
    units["unit_id"] = [f"nz{year}-{i:02d}" for i in units.index]
    order = R.Order(E, "night", SEED)
    t_rel = RH.release_minutes(order)
    close = CLOSE[year]

    truth_seats = E.official_seats()
    fv = E.final_party_votes()
    truth = {k: float(v[0]) for k, v in nz.outcomes(
        fv[None, :], np.array([[truth_seats.get(k, 0) for k in keys]]), keys, markets).items()}

    timeline = {m["rid"]: [] for m in markets}
    frames: list[dict] = []
    last_sig, last = None, None
    for minute in range(-2, END_MIN + 1):
        t = close + timedelta(minutes=minute)
        mask = t_rel <= minute
        sig = int(mask.sum())
        if sig != last_sig:
            c = counts_for(order, E, mask)
            p = nz.project(units, c, st, keys)
            rng = np.random.default_rng(int(cfg.get("seed", 20261107)))
            votes, _, seats = nz.seat_draws(p, st, rng)
            probs = {k: float(v.mean()) for k, v in nz.outcomes(votes, seats, keys, markets).items()}
            last, last_sig = (p, probs), sig
        p, probs = last
        iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        for m in markets:
            timeline[m["rid"]].append({"t": iso, "counted": nz._r(p.counted_frac), "raw": None, "share": None, "sd": None,
                                       "P": nz._r(probs[m["rid"]]), "pm": None, "kalshi": None})
        if minute >= 0 and (not frames or t - pd.Timestamp(frames[-1]["t"]) >= pd.Timedelta(minutes=5) or minute == END_MIN):
            u = p.unit
            frames.append({"t": iso, "f": np.rint(1000 * u["f"]).astype(int).tolist(),
                           "a": np.rint(1000 * u["raw_nat"].fillna(-.001)).astype(int).tolist(),
                           "proj": np.rint(1000 * u["proj_nat"]).astype(int).tolist(),
                           "winner": u["projected_winner"].tolist(),
                           "margin": np.rint(1000 * u["projected_margin"]).astype(int).tolist()})

    # ------------------------------------------------------------------ headline: model vs the FINAL official result
    def first_reach(rid: str, level: float) -> int | None:
        win = truth[rid] > 0.5
        for r in timeline[rid]:
            q = r["P"] if win else 1 - r["P"]
            if r["counted"] and q >= level:
                return int((pd.Timestamp(r["t"]) - pd.Timestamp(close)).total_seconds() // 60)
        return None

    most = [m["rid"] for m in markets if m["kind"] == "most_seats"]
    winner_rid = next(r for r in most if truth[r] > 0.5)
    stages = {}
    for s in (0.05, 0.25, 0.5, 0.75, 0.95, 1.0):
        row = None
        for i, r in enumerate(timeline[winner_rid]):
            if r["counted"] is not None and r["counted"] >= s - 1e-9:
                row = i
                break
        if row is None:
            continue
        b = [(timeline[m["rid"]][row]["P"] - truth[m["rid"]]) ** 2 for m in markets]
        stages[str(s)] = {"brier_mean": round(float(np.mean(b)), 4), "P_winner": timeline[winner_rid][row]["P"]}
    head = {"year": year, "winner_market": winner_rid, "n_markets": len(markets),
            "min_to_90": first_reach(winner_rid, 0.90), "min_to_95": first_reach(winner_rid, 0.95),
            "pre_count_P": timeline[winner_rid][0]["P"], "brier_by_counted": stages,
            "poll": {k: round(v, 4) for k, v in st.poll_shares.items()},
            "special_from": [y for y in TRAIN if y != year], "final_seats": {k: int(v) for k, v in truth_seats.items()}}

    # ------------------------------------------------------------------ files
    d = out / f"nz{year}"
    tuned = year in TRAIN
    basis = ("2020 was one of the two elections (2017 and 2020) on which the v3 settings were chosen, so this replay is IN SAMPLE: "
             "the tail widths, poll weight and prior weights were tuned on it. The special-vote shift uses 2017 only."
             if year == 2020 else
             "v3 settings were chosen on the 2017 and 2020 election nights only; 2023 was untouched test data, so this is an "
             "out-of-sample replay. The special-vote shift is the mean of 2017 and 2020, and the poll prior uses only polls "
             "published before 2023-10-14.")
    banner = f"Retrospective replay of {DATE[year]} with SIMULATED count order - not pre-registered, no market"
    name = f"New Zealand general election {year}: retrospective replay, simulated count order"
    races = []
    for m in markets:
        races.append({"race_id": m["rid"], "name": m.get("name", m["rid"]), "region": None,
                      "a": {"id": "yes", "name": m.get("yes_name", "Yes"), "color": nz.COLORS.get(m.get("party"), "#c4501b")},
                      "b": {"id": "no", "name": m.get("no_name", "No"), "color": "#777777"}, "markets": []})
    meta = {"id": f"nz{year}", "name": name, "date": DATE[year], "country": "NZ", "tz": "Pacific/Auckland",
            "poll_close_utc": close.strftime("%Y-%m-%dT%H:%M:%SZ"), "geometry": None, "geometry_object": None,
            "unit_key": "unit_id", "count_timeline": True, "regions": [],
            "layers": [{"id": "general", "name": "General electorates", "default": True}, {"id": "maori", "name": "Māori electorates"}],
            "parties": [{"id": k, "name": nz.PARTY_NAMES.get(k, nz.PRIOR_PARTIES.get(k, k)), "color": nz.COLORS.get(k, "#8a8a8a")}
                        for k in [*keys, "IND"]],
            "races": races,
            "sources": [
                {"name": f"Electoral Commission official {year} results: voting-place CSVs (via the Internet Archive)", "url": SOURCES[year],
                 "licence": "Crown copyright; reproduction permitted with acknowledgement. Only derived model outputs are published here."},
                {"name": "Wikipedia opinion-polling tables (poll prior)", "url": f"https://en.wikipedia.org/wiki/Opinion_polling_for_the_{year}_New_Zealand_general_election",
                 "licence": "CC BY-SA 4.0"}],
            "notes": [
                banner + ".",
                "SIMULATED COUNT ORDER: the Electoral Commission publishes no timestamped count archive for this election. The official "
                "voting-place results are released in a simulated order and clock (all advance places 4-14 minutes after poll close, then "
                "election-day places up to 225 minutes; seed 1), so the times on this page are NOT the real election-night times and nothing "
                "here measures speed against the real night.",
                "NO MARKET: no Polymarket or Kalshi market on this election was found (Polymarket search and Kalshi series checked on "
                "2026-10-30), so the page shows the model only; the market columns are empty.",
                "The market list is the 2026 market family (configs/nz2026_v3.toml) evaluated on this election; P is the model probability "
                "of each outcome and the eventual result is the FINAL official result including special votes.",
                basis,
                "counted is the share of voting-place results released, not the share of votes. Preliminary night counts exclude special votes (16-21% of the vote).",
                "No map: the electorate boundaries of this election are not published here.",
                "Model settings: configs/nz2026_v3.toml (docs/newzealand_v3.md). Retrospective replay, not pre-registered."],
            "n_units": len(units)}
    nz._write_json(d / "meta.json", meta)
    nz._write_json(d / "timeline.json", timeline)
    uo = nz.units_object(units, frames, frames[0]["t"])
    nz._write_json(d / "units.json", uo)
    nz._write_json(d / "stats.json", nz._empty_stats("no market prices exist for this election"))
    nz._write_json(d / "arb.json", [])
    ev = "in-sample" if tuned else "model-only"
    winner = head["winner_market"].removeprefix("most-").title()
    summ = (f"Replay of the {year} count in a simulated order. No market existed. "
            + ("Settings were tuned on this election (in sample)." if tuned
               else f"The model favoured {winner} from the start; settings from 2017 and 2020."))
    nz._write_json(d / "entry.json", {"id": f"nz{year}", "name": f"New Zealand general election {year} (retrospective replay, simulated count order)",
                                      "date": DATE[year], "country": "NZ", "status": "replay", "unit_level": "electorate",
                                      "n_units": len(units), "evidence": ev, "summary": summ})
    (ROOT / "site" / "data").mkdir(exist_ok=True)
    return head


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int, choices=sorted(CLOSE))
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    ap.add_argument("--draws", type=int, default=4000)
    a = ap.parse_args()
    print(json.dumps(build(a.year, a.out, a.draws), ensure_ascii=False, indent=1))
