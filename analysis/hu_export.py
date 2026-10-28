"""Export Hungary 2026 to the dashboard (site/data/hu2026/: meta, timeline, units, stats, entry).

Everything published is official (Nemzeti Választási Iroda count publications and district polygons; official
communications are not protected by copyright in Hungary, Act LXXVI of 1999 s. 1(4)) or public Polymarket prices,
so the count over time is shown (count_timeline true). Reads results/hungary/ from analysis/hu_score.py; the map
geometry is analysis/hu_geo.py's.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_export.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder.paths import POLYMARKET, ROOT

RES = ROOT / "results" / "hungary"
OUT = ROOT / "site" / "data" / "hu2026"
CFG = ROOT / "configs" / "hu2026_v1.toml"
NIGHT_END = pd.Timestamp("2026-04-13T08:00:00Z")
TISZA, FIDESZ = "#253ba3", "#ee8016"          # party colours as published by the NVI (Jlcs.json)
YES, NO = "#1b1b1b", "#9a9a9a"
# race id -> (name, A, B, columns (counted, raw, share, sd), P column, price market, markets with yes_is)
RACES = {
    "seats": ("Most seats (projected two-way district vote)", ("tisza", "Tisza", TISZA), ("fidesz", "Fidesz-KDNP", FIDESZ),
              ("counted", "smd_raw", "smd_share", "smd_sd"), "P:most_seats_tisza", "most_seats_tisza",
              [("most_seats_tisza", "a"), ("most_seats_fidesz", "b"), ("pm_magyar", "a"), ("pm_orban", "b")]),
    "list": ("National list vote", ("tisza", "Tisza", TISZA), ("fidesz", "Fidesz-KDNP", FIDESZ),
             ("list_counted", "list_raw", "list_share", "list_sd"), "P:list_winner_tisza", "list_winner_tisza",
             [("list_winner_tisza", "a"), ("list_winner_fidesz", "b")]),
    "tisza130": ("Tisza 130+ seats (two-thirds)", ("yes", "130+", TISZA), ("no", "under 130", FIDESZ),
                 ("counted", None, None, None), "P:tisza_atleast_130", "tisza_atleast_130",
                 [("tisza_atleast_130", "a"), ("tisza_seats_130plus", "a")]),
    "tisza120": ("Tisza 120+ seats", ("yes", "120+", TISZA), ("no", "under 120", FIDESZ),
                 ("counted", None, None, None), "P:tisza_atleast_120", "tisza_atleast_120", [("tisza_atleast_120", "a")]),
    "margin9": ("Tisza wins the list vote by 9+ points", ("yes", "9+", TISZA), ("no", "less", FIDESZ),
                ("list_counted", None, None, None), "P:margin_tisza_9plus", "margin_tisza_9plus", [("margin_tisza_9plus", "a")]),
    "turnout": ("Turnout 77-80%", ("yes", "77-80%", YES), ("no", "other", NO),
                ("counted", None, None, None), "P:turnout_77_80", "turnout_77_80", [("turnout_77_80", "a")]),
}


def dump(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def num(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def main() -> None:
    cfg = hu.load_config(CFG)
    R = pd.read_csv(RES / "releases.csv", parse_dates=["t"])
    TK = pd.read_parquet(RES / "ticks.parquet")
    D = pd.read_parquet(RES / "districts.parquet")
    S = json.loads((RES / "stats.json").read_text())
    qs = {m["id"]: m for m in cfg["markets"]}

    # ---- timeline: minutes through the night, then each later publication
    rel = R.set_index("t").sort_index()
    grid = pd.date_range(pd.Timestamp(cfg["poll_close_utc"]), NIGHT_END, freq="1min")
    later = rel.index[rel.index > NIGHT_END]
    times = grid.union(later)
    timeline = {}
    for rid, (_, _, _, cols, pcol, pm_id, _) in RACES.items():
        step = lambda s: s.reindex(s.index.union(times)).ffill().reindex(times)  # noqa: E731
        px = TK[TK["scope"] == pm_id].set_index("t")["price"]
        px = px.reindex(px.index.union(times)).ffill().reindex(times)
        vals = {k: (step(rel[c]) if c else pd.Series(np.nan, index=times)) for k, c in zip(("counted", "raw", "share", "sd"), cols)}
        P = step(rel[pcol])
        timeline[rid] = [{"t": iso(t), "counted": num(vals["counted"].iloc[i]), "raw": num(vals["raw"].iloc[i]),
                          "share": num(vals["share"].iloc[i]), "sd": num(vals["sd"].iloc[i]), "P": num(P.iloc[i]),
                          "pm": num(px.iloc[i], 4), "kalshi": None} for i, t in enumerate(times)]
    dump("timeline.json", timeline)

    # ---- units: prior and one frame per publication
    prior = hu.prior_2022_for_2026(cfg)["district"].set_index("cd_mun")
    E = hu.ELECTIONS[2026]
    oevk = {hu.unit_id(r["maz"], r["evk"]): r for r in hu._list(hu._j(2026, f"{E['ver']}/ver/OevkAdatok.json"))}
    ids = sorted(prior.index)
    frames = []
    for (v, t), g in D.groupby(["v", "t"], sort=True):
        g = g.set_index("unit").reindex(ids)
        frames.append({"t": iso(t), "f": [int(round(1000 * x)) for x in g["f"].fillna(0)],
                       "a": [int(round(1000 * x)) if pd.notna(x) else -1 for x in g["raw"]],
                       "proj": [int(round(1000 * x)) for x in g["proj"]]})
    frames.sort(key=lambda f: f["t"])
    dump("units.json", {"unit_ids": ids, "region": [u.split("-")[1] for u in ids],
                        "names": [oevk[u].get("evk_nev_en") or oevk[u]["evk_nev"] for u in ids],
                        "prior": [int(round(1000 * prior.loc[u, "s0"])) for u in ids],
                        "V0": [int(round(prior.loc[u, "V0"])) for u in ids], "frames": frames})

    # ---- meta
    counties = {}
    for r in oevk.values():
        counties[r["maz"]] = r.get("maz_nev_en") or r["maz_nev"]
    races = []
    for rid, (name, a, b, *_rest, mk) in RACES.items():
        races.append({"race_id": rid, "name": name, "region": None,
                      "a": {"id": a[0], "name": a[1], "color": a[2]}, "b": {"id": b[0], "name": b[1], "color": b[2]},
                      "markets": [{"platform": "polymarket", "id": qs[m]["market"], "event": qs[m]["event"],
                                   "question": next(x["question"] for x in json.loads(
                                       (POLYMARKET / qs[m]["event"] / "event.json").read_text())["markets"]
                                       if x["slug"] == qs[m]["market"]), "yes_is": y} for m, y in mk]})
    meta = {"id": "hu2026", "name": "Hungary, National Assembly election", "date": "2026-04-12", "tz": "Europe/Budapest",
            "poll_close_utc": cfg["poll_close_utc"], "geometry": "geo/hu_districts.topo.json", "geometry_object": "districts",
            "unit_key": "unit_id", "count_timeline": True,
            "regions": [{"id": k, "name": v} for k, v in sorted(counties.items())], "races": races,
            "sources": [
                {"name": "Nemzeti Választási Iroda: 2026 count publications (OevkJkv, ListasJkv), candidates, district polygons",
                 "url": "https://vtr.valasztas.hu/ogy2026", "licence": "official communication; not protected by copyright in "
                 "Hungary (Act LXXVI of 1999 on copyright, s. 1(4)); public data of public interest (Act CXII of 2011)"},
                {"name": "Nemzeti Választási Iroda: 2022 final result (prior) and 2022 night publications via the Internet Archive",
                 "url": "https://vtr.valasztas.hu/ogy2022", "licence": "as above"},
                {"name": "Polymarket price history (public CLOB API)", "url": "https://polymarket.com", "licence": "public market data"}],
            "notes": [
                "Units are the 106 single-member districts; 'counted' is the share of polling stations processed (seats "
                "race) or of the expected two-way list vote counted, incl. postal ballots (list race).",
                "Share and raw are Tisza's share of the Tisza + Fidesz-KDNP vote. Map prior = the 2022 joint opposition's "
                "two-way share, mapped onto the 2024 district map by polling station.",
                "The count is replayed from the 40 official publications of election night (every 10 min from 20:00 CEST) "
                "and 12 later ones; each is used from its publication time. Minute rows run to 08:00 UTC on 13 April, "
                "then one row per later publication.",
                "P for seat and margin markets comes from joint simulation of all districts and the list vote through "
                "the seat law (D'Hondt with compensation votes); the next-PM markets are priced as 'most seats'.",
                "Evidence: " + S["evidence_note"]]}
    dump("meta.json", meta)

    # ---- stats (standard keys)
    stats = {"calibration": S["calibration"],
             "settle": {k: {kk: v.get(kk) for kk in ("model", "polymarket", "kalshi", "raw", "contested", "lead_min")}
                        for k, v in S["settle"].items()},
             "settle_summary": S["settle_summary"], "catchup": S["catchup"], "early_error": S["early_error"],
             "capacity": S["capacity"], "arbitrage": None,
             "evidence": {k: "not-preregistered" for k in ("settle", "calibration", "catchup", "early_error", "capacity", "profit")},
             "evidence_note": S["evidence_note"], "final": S["final"], "final_seats": S["final_seats"],
             "reasons": S["reasons"] | {"calibration": "edge-audit window: minutes with 1-95% counted from the first "
                                                       "publication to the end of the price window (6 days)"},
             "accuracy": S["accuracy"], "profit": S["profit"]}
    dump("stats.json", stats)

    lead = S["settle_summary"]
    pr = S["profit"]
    dump("entry.json", {"id": "hu2026", "name": "Hungary, National Assembly election", "date": "2026-04-12", "country": "HU",
                        "status": "replay", "unit_level": "district", "n_units": len(ids), "evidence": "not-preregistered",
                        "summary": (f"The district model settled first on {lead['model_first']} of {lead['contested_markets']} "
                                    f"contested Polymarket markets (median lead {lead['median_lead_min']:.1f} min), but the "
                                    f"frozen trading rule lost ${-pr['max_profit']:,.0f} on ${pr['max_deployed']:,.0f} of real "
                                    "fills, almost all on one early seat-band call.")})
    for f in sorted(OUT.glob("*.json")):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
