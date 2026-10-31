"""Write the Mexico 2024 dashboard files (site/data/mx2024/: meta, timeline, units, stats, entry).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_export.py configs/mx2024_v1.toml

Reads results/mexico/<id>/ (analysis/mx_replay.py, analysis/mx_score.py), the PREP file named in the config (district
names) and the archived Polymarket prices. Map units are the 300 federal districts (geo/mx_districts.topo.json,
analysis/mx_geo.py); the model itself runs on 70k sections and the frames aggregate its section projections.
entry.json is this election's landing-page item (the integrator merges it into elections.json).
All inputs are INE data under its "Términos de libre uso" plus Polymarket's public price history.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_audit as EA
from mx_score import EV_NOTE, EVIDENCE, market_series

from brunder import mexico
from brunder.paths import POLYMARKET, RAW, ROOT

SITE = ROOT / "site" / "data"
POLL_CLOSE = pd.Timestamp("2024-06-03T00:00:00Z")      # 18:00 central time; PREP opened at 20:00 (02:00 UTC)
A = {"id": "sheinbaum", "name": "Claudia Sheinbaum", "color": "#9b2335"}
B = {"id": "galvez", "name": "Xóchitl Gálvez", "color": "#2b5c9e"}


def _r(x, n=4):
    return EA._r(x, n)


def district_names(cfg: dict) -> dict[tuple[int, int], str]:
    d = mexico._table(mexico._text(RAW / cfg["election"]["prep"]), "CLAVE_CASILLA")
    d = d.rename(columns={"ID_ESTADO": "ID_ENTIDAD"})
    e, k = mexico._int(d["ID_ENTIDAD"]), mexico._int(d["ID_DISTRITO_FEDERAL"])
    ok = e.notna() & k.notna()
    x = pd.DataFrame({"e": e[ok].astype(int), "k": k[ok].astype(int), "n": d.loc[ok, "DISTRITO_FEDERAL"].str.strip()})
    x = x[x["k"] > 0].drop_duplicates(["e", "k"])
    return {(r.e, r.k): f"{mexico.STATES[r.e][1]} {r.k:02d} ({r.n.title()})" for r in x.itertuples()}


def unit_id(e: int, k: int) -> str:
    return f"mx-{mexico.STATES[e][0]}-d{k:02d}"


def validate(eid: str) -> None:
    """Dashboard contract checks: unit ids = geometry ids, aligned arrays, races = timeline keys, standard keys."""
    d = SITE / eid
    meta, tl, un, st, en = (json.loads((d / f"{n}.json").read_text())
                            for n in ("meta", "timeline", "units", "stats", "entry"))
    geo = json.loads((SITE / meta["geometry"]).read_text())
    geo_ids = {o["properties"][meta["unit_key"]] for o in geo["objects"][meta["geometry_object"]]["geometries"]}
    n = len(un["unit_ids"])
    assert len(set(un["unit_ids"])) == n and set(un["unit_ids"]) == geo_ids, "unit ids vs geometry"
    assert all(len(un[k]) == n for k in ("region", "names", "prior", "V0"))
    assert all(len(fr[k]) == n for fr in un["frames"] for k in ("f", "a", "proj")) and len(un["frames"]) <= 60
    assert set(un["region"]) <= {r["id"] for r in meta["regions"]}, "regions"
    assert {r["race_id"] for r in meta["races"]} == set(tl), "races vs timeline"
    keys = {"t", "counted", "raw", "share", "sd", "P", "pm", "kalshi"}
    assert all(set(r) == keys for rows in tl.values() for r in rows)
    for k in ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage", "evidence", "accuracy", "profit"):
        assert k in st, k
    assert {"id", "name", "date", "country", "status", "unit_level", "n_units", "evidence", "summary"} <= set(en)
    print(f"{eid}: valid; {n} units (all in geometry), {len(un['frames'])} frames, "
          f"{sum(len(v) for v in tl.values())} timeline rows")


def main(cfg_path: str) -> None:
    cfg = mexico.load_config(cfg_path)
    eid = cfg["eid"]
    res = ROOT / "results" / "mexico" / cfg["id"]
    out = SITE / eid
    out.mkdir(parents=True, exist_ok=True)
    score = json.loads((res / "score.json").read_text())
    stats, extra = score["stats"], score["extra"]
    pa = pd.read_parquet(res / "path.parquet")
    pa["t"] = pd.to_datetime(pa["t"], utc=True)
    fr = pd.read_parquet(res / "frames.parquet")
    fr["t"] = pd.to_datetime(fr["t"], utc=True)
    final = extra["final_share"]
    is_quick = "quick_count" in cfg
    page_name = "Mexico presidential election + INE quick count" if is_quick else "Mexico presidential election"
    evidence_note = (EV_NOTE if not is_quick else
                     "retrospective sensitivity chosen after the 2024 result was known; pure-count v1 remains the "
                     "headline; this version adds the official INE quick estimate and is scored separately")

    # ---------------- timeline: poll close (18:00 central) to the last PREP cut, one row per minute
    t1 = pa["t"].iloc[-1]
    grid = pd.date_range(POLL_CLOSE, t1, freq="1min")
    px = market_series(cfg, "sheinbaum")
    last_px = px.index[-1]
    pm = px.reindex(px.index.union(grid)).ffill().reindex(grid)
    pm[grid > last_px + pd.Timedelta(minutes=1)] = np.nan
    m = pa.set_index("t").reindex(grid)
    rows = [{"t": EA._iso(t), "counted": _r(r.counted), "raw": _r(r.raw), "share": _r(r.share), "sd": _r(r.sd, 5),
             "P": _r(r.P), "pm": _r(pm.loc[t]), "kalshi": None} for t, r in m.iterrows()]
    (out / "timeline.json").write_text(json.dumps({"mx": rows}, separators=(",", ":")))

    # ---------------- units: 300 federal districts; prior aggregated from the section prior
    s = mexico.setup(cfg)
    pri = s["prior"].merge(s["cat"][["uf", "cd_mun", "ent", "district"]], on=["uf", "cd_mun"])
    pri = pri[pri["uf"] != mexico.ZZ]
    pd_ = pri.assign(sa=pri["s0"] * pri["V0"]).groupby(["ent", "district"]).agg(sa=("sa", "sum"), V0=("V0", "sum"))
    pd_["s0"] = pd_["sa"] / pd_["V0"]
    keys = sorted(pd_.index)
    names = district_names(cfg)
    frames = []
    for t, g in fr.groupby("t"):
        g = g.set_index(["ent", "district"]).reindex(keys)
        a = np.where(g["n"] > 0, g["a"] / g["n"].where(g["n"] > 0), np.nan)
        frames.append({"t": EA._iso(t), "f": [int(round(1000 * v)) for v in g["f"].fillna(0)],
                       "a": [int(round(1000 * v)) if np.isfinite(v) else -1 for v in a],
                       "proj": [int(round(1000 * v)) for v in g["proj"].fillna(pd_["s0"].reindex(g.index))]})
    units = {"race_id": "mx", "unit_ids": [unit_id(e, k) for e, k in keys],
             "region": [mexico.STATES[e][0] for e, _ in keys], "names": [names.get((e, k), unit_id(e, k)) for e, k in keys],
             "prior": [int(round(1000 * v)) for v in pd_["s0"].reindex(keys)],
             "V0": [int(round(v)) for v in pd_["V0"].reindex(keys)], "frames": frames}
    (out / "units.json").write_text(json.dumps(units, ensure_ascii=False, separators=(",", ":")))

    # ---------------- meta
    mk = pd.read_parquet(POLYMARKET / cfg["markets"]["sheinbaum"]["event"] / "markets.parquet").set_index("slug")
    markets = [{"platform": "polymarket", "id": m["market"], "question": str(mk.loc[m["market"], "question"]),
                "yes_is": m["yes_is"]} for m in cfg["markets"].values()]
    acc = stats["accuracy"]
    meta = {
        "id": eid, "name": page_name, "date": cfg["election"]["date"],
        "tz": "America/Mexico_City", "poll_close_utc": EA._iso(POLL_CLOSE),
        "geometry": "geo/mx_districts.topo.json", "geometry_object": "districts", "unit_key": "unit_id",
        "count_timeline": True,
        "regions": [{"id": a, "name": n} for _, (a, n) in sorted(mexico.STATES.items())],
        "races": [{"race_id": "mx", "name": "President", "region": None, "a": A, "b": B, "markets": markets}],
        "sources": [
            {"name": "INE, PREP 2024 base de datos (acta-level results with acopio / captura / verificación times)",
             "url": "https://prep2024.ine.mx/publicacion/nacional/base-datos",
             "licence": "INE Términos de libre uso (copy, publish, adapt with attribution; https://sicee.ine.mx); "
                        "accessed 2026-10-28"},
            {"name": "INE, SICEE: cómputos distritales 2024 by section (official final result)",
             "url": "https://sicee.ine.mx/", "licence": "INE Términos de libre uso; accessed 2026-10-28"},
            {"name": "INE, cómputos distritales 2018 by casilla (prior)", "url": "https://computos2018.ine.mx/",
             "licence": "INE Términos de libre uso; accessed 2026-10-28"},
            {"name": "INE, PREP 2024 federal district boundaries (distritos.geojson)",
             "url": "https://prep2024.ine.mx/publicacion/nacional/assets/GeoJson/distritos/distritos.geojson",
             "licence": "INE Términos de libre uso; accessed 2026-10-28"},
            {"name": "Internet Archive copies of PREP 2024 cut files (publication-time check only)",
             "url": "https://web.archive.org/web/2024*/prep2024.ine.mx/publicacion/nacional/assets/presidencia/*",
             "licence": "INE data (Términos de libre uso), read from the Wayback Machine"},
            {"name": "Polymarket public price history (CLOB prices-history) and trades (data-api)",
             "url": "https://polymarket.com/event/mexican-presidential-election-who-will-win",
             "licence": "public market data"}],
        "notes": [
            "Race: Sheinbaum's share of the Sheinbaum + Gálvez votes (two-way); she wins this market's event when it "
            "exceeds 50% (Máynez, third, could not win). Final = official cómputos (INE): "
            f"{100 * final:.2f}% two-way (59.76% of all votes cast).",
            "counted = the model's share of the expected two-way votes already counted (timeline); map f = share of "
            "the district's expected actas (polling-station tally sheets) counted.",
            "Count path rebuilt from each acta's PREP verification time: an acta is shown from the first 5-minute "
            "PREP cut that held it, 5 minutes after the cut's label (checked against archived cut files: "
            f"docs/mexico.md). PREP opened at 20:00 central time (02:00 UTC) and stopped at "
            f"{100 * extra['end_of_prep']['prep_end_actas']:.1f}% of actas; the "
            "rest entered only the official cómputo days later.",
            ("Model: brunder.nowcast on 70,537 electoral sections (state = region), prior = 2018 cómputos by "
             "section (López Obrador vs Anaya + Meade), tau = 0.01. " +
             ("From 05:50 UTC, the count projection is inverse-variance combined with the official INE Conteo Rápido "
              "two-way interval (67.09-69.53%); rule fixed before replay in docs/fast_signals.md. "
              if is_quick else "Every setting was chosen on the 2018 count and set in configs/mx2024_v1.toml. ") +
             "Evidence: " + evidence_note + ". The map shows the pure count's section projections summed to the "
             "300 federal districts."),
            f"Polymarket was already >= 0.9 on Sheinbaum from {stats['settle']['mx']['polymarket'][:16].replace('T', ' ')} "
            "UTC, two days before the count; the markets closed at 12:46-12:56 UTC on 3 June (Gamma closedTime), so "
            "pm is empty after that.",
            "Accuracy: projected share minus final at 1/5/10/25/50% counted: "
            + ", ".join(f"{k} {v['model']:+.1f} (raw {v['raw']:+.1f})" for k, v in acc['share_error_pt']['mx'].items())
            + " points. The 90% intervals held the final share in "
            f"{100 * acc['coverage90']['mx']['1-95%']['coverage90']:.0f}% of minutes (1-95% counted): far too narrow.",
            "Profit: the frozen Brazil rule (favoured side, edge >= 5c after costs, >= 1% counted) found no "
            "signal in either market, so there is nothing to fill.",
            "pm = Polymarket price of the YES (= Sheinbaum) side, carried forward minute by minute. No Kalshi data.",
        ],
    }
    if is_quick:
        fast = stats["fast_signal"]
        pure, quick = fast["projection"]["pure_count_v1"], fast["projection"]["quick_count"]
        meta["sources"].insert(-1, {
            "name": "INE, Conteo Rápido presidential estimate (national message and official release 352)",
            "url": cfg["quick_count"]["source"],
            "licence": "INE Términos de libre uso / máxima publicidad; accessed 2026-10-30",
        })
        meta["notes"].insert(0, cfg["label"] + ". Pure-count mx2024 remains the headline version.")
        meta["notes"].insert(-2, (
            f"After the 05:50 UTC release, mean absolute projection error was {quick['mae_pt_after_release']:.3f} "
            f"points with the quick estimate versus {pure['mae_pt_after_release']:.3f} for pure-count v1. "
            f"Full 1-95% coverage was {100 * quick['coverage90_1_95']:.1f}% versus "
            f"{100 * pure['coverage90_1_95']:.1f}%; both were still far too narrow. Neither included market was "
            "contested at 90% when the count opened, so no lead is claimed."
        ))
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    # ---------------- stats
    (out / "stats.json").write_text(json.dumps(EA._clean(stats), ensure_ascii=False, separators=(",", ":")))

    # ---------------- landing entry (merged by the integrator)
    e25 = acc["share_error_pt"]["mx"]["25%"]
    cov = acc["coverage90"]["mx"]["1-95%"]["coverage90"]
    if is_quick:
        fast = stats["fast_signal"]
        pure, quick = fast["projection"]["pure_count_v1"], fast["projection"]["quick_count"]
        summary = (f"With official quick estimate, scored separately: after INE's 23:50 release, mean absolute "
                   f"projection error fell from {pure['mae_pt_after_release']:.2f} to "
                   f"{quick['mae_pt_after_release']:.2f} points, but 90% coverage remained only "
                   f"{100 * quick['coverage90_1_95']:.0f}%. Both markets were already 90% settled before the count, "
                   "so this is not a market-lead claim.")
    else:
        summary = (f"Polymarket was already 90% sure of Sheinbaum two days before the vote, so the count offered no "
                   f"trade; at 25% counted the section model was {abs(e25['model']):.1f} points below her final "
                   f"share (raw count {abs(e25['raw']):.1f}), but its 90% intervals held the final share in only "
                   f"{100 * cov:.0f}% of minutes.")
    entry = {"id": eid, "name": page_name, "date": cfg["election"]["date"], "country": "MX",
             "status": "replay", "unit_level": "federal district", "n_units": len(keys), "evidence": EVIDENCE,
             "evidence_note": evidence_note, "summary": summary}
    (out / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    for f in sorted(out.iterdir()):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e3:.0f} kB")
    print(entry["summary"])
    validate(eid)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(ROOT / "configs" / "mx2024_v1.toml")))
