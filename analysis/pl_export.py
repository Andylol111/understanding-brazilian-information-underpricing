"""Export Poland 2025 to the dashboard (site/data/pl2025r1/ and pl2025r2/: meta, timeline, units, stats, entry).

Published: derived per-minute series (model P, projected share, raw national tally) and, per voivodeship and archived
PKW count version, the counted share, the raw tally and the projection; Polymarket and Kalshi prices (credited). Not
published: the PKW protobuf files, and nothing from the 2020/2025 csv beyond the per-voivodeship prior share. PKW
states no licence for its results data (see docs/poland.md); the derived series are what this export rests on.
Reads results/poland/<eid>/ from analysis/pl_score.py; the map geometry is analysis/pl_geo.py's.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_export.py [pl2025r2 pl2025r1]
"""

from __future__ import annotations

import json
import sys
import tomllib

import numpy as np
import pandas as pd

from brunder import poland as pl
from brunder.paths import POLYMARKET, ROOT

RES = ROOT / "results" / "poland"
TRZ, NAW = "#e8731a", "#1d3a8a"          # Trzaskowski (Civic Coalition orange), Nawrocki (navy)
YES, NO = "#1b1b1b", "#9a9a9a"
EVIDENCE_NOTE = {
    2: ("Retrospective replay, not pre-registered. Before the settings were frozen the archived PKW versions were downloaded "
        "and decoded, and an earlier voivodeship-level version of the model was run on the runoff count with default "
        "settings (no settings chosen). The frozen settings (configs/pl2025r2_v1.toml) come only from the 2025 round-1 count "
        "night, an earlier election; no runoff count went into any setting. The round-1 settings did not transfer well: "
        "the runoff's 90% band held the final share in under half the minutes from 1% to 95% counted."),
    1: ("Retrospective replay, not pre-registered. The settings (configs/pl2025r1_v1.toml) come only from the 2020 runoff "
        "counted in a simulated reporting order, not from any 2025 count; the archived 2025 versions were downloaded and "
        "decoded before they were frozen."),
}
NAMES = {2: "Poland, presidential runoff", 1: "Poland, presidential election, first round"}
DATES = {2: "2025-06-01", 1: "2025-05-18"}
NIGHT_END = {2: "2025-06-02T08:00:00Z", 1: "2025-05-19T08:00:00Z"}


def dump(out, name: str, obj) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def num(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def cest(t: str) -> str:
    return (pd.Timestamp(t) + pd.Timedelta(hours=2)).strftime("%H:%M")


def question(m: dict) -> str:
    if m["platform"] == "kalshi":
        return m["wording"]
    ev = json.loads((POLYMARKET / m["event"] / "event.json").read_text())
    return next(x["question"] for x in ev["markets"] if x["slug"] == m["market"])


def step2(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    return s.reindex(s.index.union(grid)).ffill().reindex(grid)


def export(eid: str) -> None:
    rnd = 2 if eid.endswith("r2") else 1
    cfg = tomllib.loads((ROOT / "configs" / f"{eid}_v1.toml").read_text())
    res = RES / eid
    S = json.loads((res / "stats.json").read_text())
    T = pd.read_parquet(res / "ticks.parquet")
    U = json.loads((res / "units.json").read_text())
    R = pd.read_csv(res / "releases.csv", parse_dates=["t"])
    out = ROOT / "site" / "data" / eid
    grid = pd.date_range(pd.Timestamp(cfg["poll_close_utc"]), pd.Timestamp(NIGHT_END[rnd]), freq="1min")
    rel = R.set_index("t").sort_index()
    step = lambda s: s.reindex(s.index.union(grid)).ffill().reindex(grid)  # noqa: E731

    # ---- timeline: one row per minute from poll close to 08:00 UTC the next morning
    timeline, races = {}, []
    for race in cfg["race"]:
        rid = race["id"]
        g = T[(T["race"] == rid) & T["t"].isin(grid)]
        pm = g[(g["platform"] == "polymarket") & (g["yes_is"] == "a")].set_index("t")
        ka = g[(g["platform"] == "kalshi") & (g["yes_is"] == "a")].set_index("t")
        win = race["kind"] == "winner"
        cols = {"counted": step(rel["counted"]).fillna(0.0), "raw": step(rel["raw"]) if win else None,
                "share": step(rel["share"]) if win else None, "sd": step(rel["sd"]) if win else None}
        P = pm["P_A"].reindex(grid)
        px = pm["price_A"].reindex(grid)
        kx = ka["price_A"].reindex(grid) if len(ka) else pd.Series(np.nan, index=grid)
        timeline[rid] = [{"t": iso(t), "counted": num(cols["counted"].iloc[i]),
                          "raw": num(cols["raw"].iloc[i]) if win else None, "share": num(cols["share"].iloc[i]) if win else None,
                          "sd": num(cols["sd"].iloc[i]) if win else None, "P": num(P.iloc[i]), "pm": num(px.iloc[i], 4),
                          "kalshi": num(kx.iloc[i], 4)} for i, t in enumerate(grid)]
        a_col, b_col = (TRZ, NAW) if win else (YES, NO)
        races.append({"race_id": rid, "name": race["name"], "region": None,
                      "a": {"id": "a", "name": race["a"], "color": a_col}, "b": {"id": "b", "name": race["b"], "color": b_col},
                      "markets": [{"platform": m["platform"], "id": m["market"], "event": m["event"],
                                   "question": question(m), "yes_is": m["yes_is"]} for m in race["market"]]})
    v2 = RES / "pl2025r2_v2"
    cfg2 = tomllib.loads((ROOT / "configs" / "pl2025r2_v2.toml").read_text()) if rnd == 2 and (v2 / "stats.json").exists() else None
    if cfg2:
        cfg2["_label"] = "configs/pl2025r2_v2.toml"
    if cfg2:                                     # v2 = sensitivity: P_sensitivity (+ share/sd for the winner race) per row
        T2 = pd.read_parquet(v2 / "ticks.parquet")
        R2 = pd.read_csv(v2 / "releases.csv", parse_dates=["t"]).set_index("t").sort_index()
        for race in cfg["race"]:
            rid = race["id"]
            g2 = T2[(T2["race"] == rid) & (T2["platform"] == "polymarket") & (T2["yes_is"] == "a")].set_index("t")
            P2 = g2["P_A"].reindex(grid)
            sh2, sd2 = step2(R2["share"], grid), step2(R2["sd"], grid)
            for i, row in enumerate(timeline[rid]):
                row["P_sensitivity"] = num(P2.iloc[i])
                if race["kind"] == "winner":
                    row["share_sensitivity"], row["sd_sensitivity"] = num(sh2.iloc[i]), num(sd2.iloc[i])
    dump(out, "timeline.json", timeline)

    # ---- units: prior and one frame per count version (+ a frame at poll close before anything is counted)
    pri = pl.Replay(rnd).prior
    pri = pri[pri["uf"] != "zz"].assign(w=lambda d: d["s0"] * d["V0"]).groupby("uf")[["w", "V0"]].sum()
    ids = [f"pl-{c}" for c in pl.WOJ]
    prior = [int(round(1000 * pri.loc[c, "w"] / pri.loc[c, "V0"])) for c in pl.WOJ]
    frames = [{"t": iso(cfg["poll_close_utc"]), "f": [0] * 16, "a": [-1] * 16, "proj": prior}]
    for ver in sorted(U, key=int):
        t = pl.version_time(int(ver))
        f = U[ver]
        frames.append({"t": iso(max(t, pd.Timestamp(cfg["poll_close_utc"]))),
                       "f": [int(round(1000 * (f[c]["counted"] or 0))) for c in pl.WOJ],
                       "a": [int(round(1000 * f[c]["raw"])) if f[c]["raw"] is not None else -1 for c in pl.WOJ],
                       "proj": [int(round(1000 * f[c]["proj"])) for c in pl.WOJ]})
    frames = [x for x in frames if x["t"] <= iso(NIGHT_END[rnd])]
    seen = {}
    for x in frames:
        seen[x["t"]] = x                      # a version generated before poll close shares the poll-close frame time
    frames = sorted(seen.values(), key=lambda x: x["t"])
    dump(out, "units.json", {"unit_ids": ids, "region": [None] * 16, "names": [pl.VOIVODESHIPS[c] for c in pl.WOJ],
                             "prior": prior, "V0": [int(round(pri.loc[c, "V0"])) for c in pl.WOJ], "frames": frames})

    # ---- meta
    F = S["final"]
    sources = [
        {"name": "Państwowa Komisja Wyborcza: count versions of the results site (pl_po_woj.<epoch>.blob), as captured by the "
                 "Internet Archive", "url": f"https://wybory.gov.pl/prezydent2025/data/{'obkw2' if rnd == 2 else 'obkw'}/",
         "licence": "none stated on the site or its robots.txt (absent). Official documents are outside copyright "
                    "(Polish Copyright Act art. 4(2)); derived series published, the files themselves are not"},
        {"name": "PKW: final results by gmina, 2025 (electorate of each cell) and 2020 (prior)",
         "url": "https://prezydent2025.pkw.gov.pl/prezydent2025/pl/dane_w_arkuszach", "licence": "as above"},
        {"name": "Natural Earth 1:10m admin-1 (voivodeship outlines)", "url": "https://www.naturalearthdata.com/",
         "licence": "public domain"},
        {"name": "Polymarket price history (public CLOB API)", "url": "https://polymarket.com", "licence": "public market data"}]
    if any(m["platform"] == "kalshi" for r in cfg["race"] for m in r["market"]):
        sources.append({"name": "Kalshi: 1-minute candles (© Kalshi)", "url": "https://kalshi.com",
                        "licence": "© Kalshi. Derived P line and settle times only; no raw rows (docs/kalshi.md)"})
    n_ver = len(R)
    notes = [
        f"Retrospective replay of the {DATES[rnd]} count, not pre-registered.",
        "Units on the map are the 16 voivodeships. The model runs over 128 cells (voivodeship x gmina-size class) plus abroad "
        "and ships, which are counted but not drawn. PKW published the count only by voivodeship and by size class, so each "
        "cell's counted votes are reconstructed from those two sets of totals (docs/poland.md).",
        f"The count is replayed from {n_ver} archived PKW versions ({cest(iso(R['t'].min()))}-{cest(iso(R['t'].max()))} CEST "
        "by PKW's own version time); the Internet Archive did not keep every version, so gaps of an hour or more occur "
        "(runoff: none between 22:00 and 23:11 UTC, nor between 01:07 and 03:13 UTC).",
        "'Counted' is the share of the expected votes counted (electorate counted, from PKW's own voter counts); 'raw' and "
        f"'share' are Trzaskowski's share of the Trzaskowski + Nawrocki vote, abroad included. Map prior = the 2020 {'runoff' if rnd == 2 else 'first round'} "
        "(Trzaskowski vs Duda) by voivodeship.",
        f"Official final (PKW, summed from the last archived version; the gmina csv agrees): Trzaskowski {F['a']:,.0f}, "
        f"Nawrocki {F['b']:,.0f}; Trzaskowski {100 * F['share']:.2f}% of the two-way vote." if rnd == 2 else
        f"Official final (PKW, last archived version): Trzaskowski {F['a']:,.0f}, Nawrocki {F['b']:,.0f} (Trzaskowski "
        f"{100 * F['share']:.2f}% of the two-way vote). Third-ranked Mentzen is not modelled.",
    ]
    if rnd == 2:
        notes.append("Margin bands (Nawrocki 0-4, 4-8, over 8; Trzaskowski 0-4, 4-8) are priced from the same normal "
                     "distribution of the final share, in points of valid votes.")
    meta = {"id": eid, "name": NAMES[rnd], "date": DATES[rnd], "tz": "Europe/Warsaw", "poll_close_utc": cfg["poll_close_utc"],
            "geometry": "geo/pl_voivodeships.topo.json", "geometry_object": "voivodeships", "unit_key": "unit_id",
            "count_timeline": True, "regions": [], "races": races, "sources": sources, "notes": notes + ["Evidence: " + EVIDENCE_NOTE[rnd]]}
    if cfg2:
        m2 = cfg2["model"]
        meta["sensitivity"] = {"label": "v2: settings pooled over the 2025 round-1 night and a simulated 2020 runoff count "
                                        "(chosen after the 2025 runoff result was known; not a forward test)",
                               "config": "configs/pl2025r2_v2.toml"}
        meta["notes"].append(
            f"Two settings, side by side. v1 (headline, configs/pl2025r2_v1.toml): tau {cfg['model']['tau']}, rho {cfg['model']['rho']}, "
            f"chosen on the 2025 round-1 night alone before the runoff was scored. v2 (sensitivity, {cfg2['_label']}): "
            f"tau {m2['tau']}, kappa {m2['kappa']:.0f}, rho {m2['rho']}, chosen after the runoff result was known "
            "because v1 was overconfident: pooled over every earlier night that could be scored (the real round-1 night and a "
            "simulated 2020 runoff count, each weighted equally). Only those two exist: the Internet Archive holds no count-night "
            "versions of the 2015, 2019 or 2020 elections, and 2023-2024 have one or two snapshots of party-list elections. "
            "v2 is shown as the second line on the chart (P_sensitivity) and in the statistics.")
    dump(out, "meta.json", meta)

    # ---- stats
    reasons = {"capacity": "no frozen trading rule for Poland and no taker trades were fetched (edge audit not run)",
               "profit": "not computed: the edge audit was not run for Poland",
               "arbitrage": "no Polymarket-Kalshi pair configuration for Poland (configs/pairs)",
               "settle.kalshi": "Kalshi lists no market on the margin bands over 8 points or Trzaskowski 4-8" if rnd == 2 else ""}
    for r in cfg["race"]:
        if r["kind"] != "winner":
            reasons[f"calibration.{r['id']}"] = reasons[f"early_error.{r['id']}"] = \
                "margin band: priced from the winner race's share projection, which is the one scored"
    stats = {"calibration": S["calibration"], "settle": S["settle"], "settle_summary": S["settle_summary"],
             "settle_gated": S["settle_gated"], "catchup": S["catchup"], "early_error": S["early_error"],
             "capacity": None, "arbitrage": None, "profit": None,
             "evidence": {k: "not-preregistered" for k in ("settle", "settle_gated", "calibration", "catchup", "early_error", "accuracy")},
             "evidence_note": EVIDENCE_NOTE[rnd], "final": S["final"],
             "settle_gated_note": "per race (its first Polymarket market): gate = first version with >= 1% counted; model = "
                                  "max(model settle, gate); lead = Polymarket settle - gated model settle, minutes (settle = "
                                  "staying >= 0.9 on the outcome to the end of the price window, 08:00 UTC)",
             "settle_95_winner": S["settle_95_winner"], "brier_by_counted_winner": S["brier_by_counted_winner"],
             "brier_by_counted_all_markets": S["brier_by_counted_all_markets"],
             "reasons": {k: v for k, v in reasons.items() if v},
             "accuracy": S["accuracy"]}
    sens = RES / "pl2025r2_sim2020" / "stats.json"
    if rnd == 2 and sens.exists():
        s2 = json.loads(sens.read_text())
        stats["sensitivity_2020_simulation_settings"] = {
            "note": "same replay with the settings the 2020 simulation chose (tau 0.04, rho 0.5) instead of the 2025 round-1 night's "
                    "(tau 0.005, rho 0.8); retrospective, not the headline",
            "settle_winner": s2["settle"]["winner"], "calibration_winner": s2["calibration"]["winner"],
            "early_error": s2["early_error"]["winner"],
            "pwin_winner_race": {k: v for k, v in s2["accuracy"]["pwin"].items() if not k.startswith("reliab")},
            "brier_by_counted_winner": s2["brier_by_counted_winner"]}
    if cfg2:
        S2 = json.loads((v2 / "stats.json").read_text())
        stats["sensitivity"] = {
            "label": meta["sensitivity"]["label"], "config": "configs/pl2025r2_v2.toml", "settings": cfg2["model"],
            "calibration": S2["calibration"], "settle": S2["settle"], "settle_gated": S2["settle_gated"],
            "settle_95_winner": S2["settle_95_winner"], "early_error": S2["early_error"], "catchup": S2["catchup"],
            "brier_by_counted_winner": S2["brier_by_counted_winner"],
            "brier_by_counted_all_markets": S2["brier_by_counted_all_markets"],
            "accuracy": {"coverage90": S2["accuracy"]["coverage90"],
                         "pwin": {k: v for k, v in S2["accuracy"]["pwin"].items() if not k.startswith("reliab")},
                         "pwin_all_markets": S2["accuracy"]["pwin_all_markets"]},
            "evidence": "not-preregistered"}
    dump(out, "stats.json", stats)

    # ---- entry for site/data/elections.json
    w = S["settle"]["winner"]
    cal = S["calibration"]["winner"]
    lead = lambda a, b: int(round((pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60))  # noqa: E731
    if rnd == 2:
        sens_w = stats["sensitivity_2020_simulation_settings"]["settle_winner"]
        summary = (f"Retrospective replay. With settings fixed on the 2025 round-1 night the model was 90% sure of Nawrocki's win at "
                   f"{cest(w['model'])} CEST, {lead(w['model'], w['polymarket'])} minutes before Polymarket ({cest(w['polymarket'])}), "
                   f"but its 90% band held the final share in only {100 * cal['coverage90_1_95']:.0f}% of minutes; with settings from a "
                   f"simulated 2020 count it settled at {cest(sens_w['model'])}, {lead(sens_w['polymarket'], sens_w['model'])} minutes "
                   f"after the market.")
    else:
        summary = (f"Retrospective replay. Polymarket had Trzaskowski at {100 * w['price_at_first_release']:.0f}% to win the most "
                   f"votes when the count began and stayed above 90% from {cest(w['polymarket'])} CEST; the model, with settings "
                   f"fixed on a simulated 2020 count, settled at {cest(w['model'])} CEST, {lead(w['polymarket'], w['model'])} "
                   f"minutes later (its 90% band held the final share in {100 * cal['coverage90_1_95']:.0f}% of minutes).")
    dump(out, "entry.json", {"id": eid, "name": NAMES[rnd], "date": DATES[rnd], "country": "PL", "status": "replay",
                             "unit_level": "voivodeship", "n_units": 16, "evidence": "not-preregistered", "summary": summary})
    for f in sorted(out.glob("*.json")):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    for e in sys.argv[1:] or ["pl2025r2", "pl2025r1"]:
        export(e)
