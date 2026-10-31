"""Add the v2 sensitivity replay (configs/hu2026_v2.toml, chosen after the result was known) to site/data/hu2026/.

v1 fields are not touched. Added, per the data contract:
  timeline.json  each race row: P_sensitivity (+ share_sensitivity, sd_sensitivity for the two share races);
  meta.json      sensitivity {label, config} and one note;
  stats.json     sensitivity {settings, calibration nights, calibration, settle, settle_summary, settle_gated,
                 early_error, accuracy, compare (minutes to 0.9 / 0.95 against the market, v1 and v2)}.
Needs results/hungary_v2/ (analysis/hu_score.py configs/hu2026_v2.toml) and results/hungary/calibration_v2.json
(analysis/hu_calibrate_v2.py). v1's numbers are read from the published site files, so v1 is not re-run.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_export_v2.py
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder.paths import ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import hu_export as ex  # noqa: E402

RES = ROOT / "results" / "hungary_v2"
CAL = ROOT / "results" / "hungary" / "calibration_v2.json"
SITE = ROOT / "site" / "data" / "hu2026"
CFG2 = ROOT / "configs" / "hu2026_v2.toml"
THRESH = (0.9, 0.95)


def settle_min(t: pd.Series, ok: pd.Series) -> pd.Timestamp | None:
    """First minute from which `ok` holds to the end of the window (None if it never settles)."""
    ok = ok.fillna(False).to_numpy(dtype=bool)
    if not ok.size or not ok[-1]:
        return None
    bad = np.flatnonzero(~ok)
    i = 0 if len(bad) == 0 else bad[-1] + 1
    return pd.Timestamp(t.iloc[i])


def mins(a: pd.Timestamp | None, b: pd.Timestamp | None) -> int | None:
    return None if a is None or b is None else int(round((b - a).total_seconds() / 60))


def main() -> None:
    cfg = hu.load_config(CFG2)
    label = cfg["sensitivity"]["label"]
    R = pd.read_csv(RES / "releases.csv", parse_dates=["t"])
    S = json.loads((RES / "stats.json").read_text())
    SE = pd.read_csv(RES / "settle.csv").set_index("market")
    cal = json.loads(CAL.read_text())
    tl = json.loads((SITE / "timeline.json").read_text())
    meta = json.loads((SITE / "meta.json").read_text())
    st = json.loads((SITE / "stats.json").read_text())
    # idempotent: drop an earlier sensitivity block
    meta.pop("sensitivity", None)
    meta["sources"] = [s for s in meta["sources"] if not s["name"].startswith("v2 calibration")]
    meta["sources"].append(
        {"name": "v2 calibration: Nemzeti Választási Iroda results archive 1990-2024 (2018 and 2014 polling-station results, 2010 "
                 "county list votes) and Internet Archive copies of the 2018 night list page",
         "url": "https://static.valasztas.hu/dyn/letoltesek/valasztasi_eredmenyek_1990-2024.zip",
         "licence": "official communication; not protected by copyright in Hungary (Act LXXVI of 1999, s. 1(4)); only "
                    "derived series (calibration settings) are published here"})
    meta["notes"] = [n for n in meta["notes"] if not n.startswith("Sensitivity (v2)")]
    st.pop("sensitivity", None)

    rel = R.set_index("t").sort_index()
    compare = {}
    for rid, (_, _, _, cols, pcol, pm_id, _) in ex.RACES.items():
        rows = tl[rid]
        for r in rows:
            r.pop("P_sensitivity", None), r.pop("share_sensitivity", None), r.pop("sd_sensitivity", None)
        times = pd.DatetimeIndex([pd.Timestamp(r["t"]) for r in rows])
        step = lambda s: s.reindex(s.index.union(times)).ffill().reindex(times)  # noqa: E731
        P2 = step(rel[pcol])
        sh2 = step(rel[cols[2]]) if cols[2] else None
        sd2 = step(rel[cols[3]]) if cols[3] else None
        for i, r in enumerate(rows):
            r["P_sensitivity"] = ex.num(P2.iloc[i])
            if sh2 is not None:
                r["share_sensitivity"] = ex.num(sh2.iloc[i])
                r["sd_sensitivity"] = ex.num(sd2.iloc[i])
        # minutes to 0.9 / 0.95 on the outcome, v1 / v2 / market, within the drawn window (19:00-08:00 CEST)
        y = int(SE.loc[pm_id, "outcome"])
        side = (lambda s: s) if y == 1 else (lambda s: 1 - s)
        P1 = pd.Series([np.nan if r["P"] is None else r["P"] for r in rows], index=times)
        PM = pd.Series([np.nan if r["pm"] is None else r["pm"] for r in rows], index=times)
        first = rel.index.min()
        item = {"market": pm_id, "outcome": y, "first_publication": ex.iso(first),
                "market_price_at_first_publication": ex.num(SE.loc[pm_id, "price_at_first_release"]),
                "decided_before_count": not bool(SE.loc[pm_id, "contested"]),
                "P_first_v1": ex.num(P1[P1.index >= first].iloc[0]), "P_first_v2": ex.num(P2[P2.index >= first].iloc[0])}
        for th in THRESH:
            key = f"{int(round(th * 100))}"
            t1 = settle_min(times[times >= first].to_series(), (side(P1) >= th)[times >= first])
            t2 = settle_min(times[times >= first].to_series(), (side(P2) >= th)[times >= first])
            tm = settle_min(times.to_series(), side(PM) >= th)
            item[f"settle{key}"] = {"v1": ex.iso(t1) if t1 is not None else None, "v2": ex.iso(t2) if t2 is not None else None,
                                    "market": ex.iso(tm) if tm is not None else None,
                                    "lead_v1_min": mins(t1, tm), "lead_v2_min": mins(t2, tm)}
        compare[rid] = item
    (SITE / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, separators=(",", ":")))

    v1c, v2c = hu.load_config(ROOT / "configs" / "hu2026_v1.toml"), cfg
    settings = {"v1": {"district": v1c["model"]["district"], "list": v1c["model"]["list"],
                       "nights": "2022 (9 archived frames; 2018 prior)"},
                "v2": {"district": v2c["model"]["district"], "list": v2c["model"]["list"],
                       "nights": "district: 2022 real order + 2018 simulated orders; list: 2022 + 2018 + 2014 (simulated)"}}
    meta["sensitivity"] = {"label": label, "config": "configs/hu2026_v2.toml"}
    meta["notes"].append(
        "Sensitivity (v2): uncertainty settings re-chosen from the 2022 and 2018 nights (list: also 2014) by one common "
        "rule written down before it was run; chosen after the result was known, so it is not a forecast test. v1 "
        "(2022 night only) remains the headline. 2018 and 2014 count orders are simulated from the official "
        "polling-station results (no timeline was archived).")
    (SITE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))

    gated = ex.settle_gated(R, S)
    st["sensitivity"] = {
        "label": label, "config": "configs/hu2026_v2.toml", "evidence": "sensitivity-after-result",
        "evidence_note": S["evidence_note"], "settings": settings,
        "calibration_nights": {"district": cal["district"], "list": {k: v for k, v in cal["list"].items()},
                               "by_tau": cal["district_by_tau_mean_over_sigma_kappa"]},
        "calibration": S["calibration"],
        "settle": {k: {kk: v.get(kk) for kk in ("model", "polymarket", "kalshi", "raw", "contested", "lead_min")}
                   for k, v in S["settle"].items()},
        "settle_summary": S["settle_summary"], "settle_gated": gated, "early_error": S["early_error"],
        "accuracy": {k: S["accuracy"][k] for k in ("share_error_pt", "coverage90", "pwin", "pwin_all_markets", "pwin_window")},
        "compare": compare,
        "compare_note": "minutes from the first minute a version stays >= 0.9 (0.95) on the outcome to the end of the "
                        "19:00-08:00 CEST window, against Polymarket; positive lead = model first; v1 = published P, v2 = "
                        "this replay; decided_before_count = market price at the first publication outside 0.1-0.9"}
    (SITE / "stats.json").write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(compare, indent=1))


if __name__ == "__main__":
    main()
