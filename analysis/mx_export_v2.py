"""Add the v2 replay (settings from every earlier night, chosen after the result was known) to site/data/mx2024/ next
to v1, following the integrator's data contract; v1 fields are not changed.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_export_v2.py

Needs results/mexico/{mx2024_v1,mx2024_v2}/{path.parquet,ticks.parquet,score.json} (mx_replay.py + mx_score.py) and
results/mexico/mx2024_calibration_v2/summary.json (mx_calibrate_multi.py). Writes into site/data/mx2024/:
  timeline.json  each row gets P_sensitivity, share_sensitivity, sd_sensitivity (v2)
  meta.json      `sensitivity` {label, config} and one more note
  stats.json     `sensitivity`: the v2 numbers beside the v1 ones (coverage against the cómputo and the end-of-PREP
                 share, errors, minutes to 90/95% against the market, Brier), re-runnable (idempotent)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_audit as EA
from mx_score import market_series

from brunder import mexico
from brunder.paths import ROOT

SITE = ROOT / "site" / "data" / "mx2024"
RES = ROOT / "results" / "mexico"
LABEL = ("v2: settings from the 2012 and 2018 nights (tau 0.015, rho 1.0, kappa 10,000; chosen after the result was "
         "known, sensitivity)")


def load(v: str):
    d = RES / f"mx2024_{v}"
    pa = pd.read_parquet(d / "path.parquet")
    pa["t"] = pd.to_datetime(pa["t"], utc=True)
    fin = json.loads((d / "final.json").read_text())
    return pa, fin, json.loads((d / "score.json").read_text())


def settle_at(pa: pd.DataFrame, level: float, y: bool) -> pd.Timestamp:
    P = pa["P"] if y else 1 - pa["P"]
    return EA.settle(pa["t"], P >= level)


def metrics(v: str, cfg: dict, prep_end: float) -> dict:
    pa, fin, sc = load(v)
    final = fin["final_share"]
    y = final > 0.5
    w = pa["counted"].between(0.01, 0.95, inclusive="right") & np.isfinite(pa["share"]) & (pa["sd"] > 0)
    cov = lambda target: float((((target - pa["share"]) / pa["sd"]).abs() < 1.645)[w].mean())  # noqa: E731
    px = market_series(cfg, "sheinbaum")
    px = px[px.index <= pa["t"].iloc[-1]]
    first_count = pa.loc[pa["counted"] >= 0.01, "t"].iloc[0]
    out = {"settings": {"tau": 0.01, "rho": 0.8, "kappa": 50000.0, **{k: v for k, v in cfg["model"].items() if k != "unit"}}, "coverage90_vs_computo": round(cov(final), 3),
           "coverage90_vs_prep_end": round(cov(prep_end), 3), "minutes_1_95": int(w.sum()),
           "mean_z_vs_computo": round(float(((final - pa["share"]) / pa["sd"])[w].mean()), 2),
           "share_error_pt": sc["stats"]["accuracy"]["share_error_pt"]["mx"],
           "mean_abs_error_pt_1_95": round(float(100 * (pa["share"] - final).abs()[w].mean()), 2),
           "mean_abs_error_vs_prep_end_pt_1_95": round(float(100 * (pa["share"] - prep_end).abs()[w].mean()), 2),
           "end_of_prep_projection_pt_vs_computo": round(100 * float(pa["share"].iloc[-1] - final), 2),
           "end_of_prep_projection_pt_vs_prep_end": round(100 * float(pa["share"].iloc[-1] - prep_end), 2)}
    for lvl in (0.9, 0.95):
        tm = settle_at(pa, lvl, y)
        mk = EA.settle(pd.Series(px.index), pd.Series((px >= lvl).to_numpy() if y else (px <= 1 - lvl).to_numpy()))
        out[f"settle_{int(lvl * 100)}"] = {
            "model": EA._iso(tm), "market": EA._iso(mk),
            "market_before_count": bool(mk <= first_count), "minutes_model_ahead_of_market": EA._r((mk - tm).total_seconds() / 60, 0)
            if mk > first_count else None}
    # Brier on the minutes from the first count (1%) on, both defined, Sheinbaum market
    pm = px.reindex(px.index.union(pa["t"])).ffill().reindex(pa["t"])
    pm[pa["t"].to_numpy() > px.index[-1] + pd.Timedelta(minutes=1)] = np.nan
    m = pd.DataFrame({"P": pa["P"].to_numpy(), "pm": pm.to_numpy()}, index=pa["t"])
    m = m[m.index >= first_count].dropna()
    if len(m):
        o = float(y)
        out["brier_from_1pct"] = {"minutes": len(m), "model": round(float(((m["P"] - o) ** 2).mean()), 7),
                                  "market": round(float(((m["pm"] - o) ** 2).mean()), 5)}
    return out


def main() -> None:
    cfg1, cfg2 = (mexico.load_config(ROOT / "configs" / f"mx2024_{v}.toml") for v in ("v1", "v2"))
    cal = json.loads((RES / "mx2024_calibration_v2" / "summary.json").read_text())
    pa1, fin1, _ = load("v1")
    pa2, fin2, _ = load("v2")
    prep_end = float(pa1["raw"].iloc[-1])
    m1, m2 = metrics("v1", cfg1, prep_end), metrics("v2", cfg2, prep_end)

    # ---- timeline: v2 columns beside v1's (same minute grid)
    tl = json.loads((SITE / "timeline.json").read_text())
    p2 = pa2.set_index("t")
    rows = tl["mx"]
    for r in rows:
        t = pd.Timestamp(r["t"])
        if t in p2.index:
            x = p2.loc[t]
            r["P_sensitivity"], r["share_sensitivity"], r["sd_sensitivity"] = (EA._r(x.P), EA._r(x.share), EA._r(x.sd, 5))
        else:
            r["P_sensitivity"] = r["share_sensitivity"] = r["sd_sensitivity"] = None
    (SITE / "timeline.json").write_text(json.dumps(tl, separators=(",", ":")))

    # ---- meta
    meta = json.loads((SITE / "meta.json").read_text())
    meta["sensitivity"] = {"label": LABEL, "config": "configs/mx2024_v2.toml"}
    c1, c2 = m1["coverage90_vs_computo"], m2["coverage90_vs_computo"]
    note = (f"v2 (sensitivity, chosen after the result was known): v1 chose tau on the 2018 night alone and its 90% "
            f"band held the final share in {100 * c1:.0f}% of minutes. v2 applies a rule written beforehand to the "
            f"2012 (IFE PREP database, prior 2006 cómputos) and 2018 nights: smallest tau with pooled 90% coverage "
            f">= 0.90 and worst night >= 0.75, then the (rho, kappa) of lowest projection error: tau "
            f"{cal['tau']}, rho {cal['rho']}, kappa {cal['kappa']:,.0f} (configs/mx2024_v2.toml). On 2024 the band "
            f"held {100 * c2:.0f}% of minutes against the cómputo and {100 * m2['coverage90_vs_prep_end']:.0f}% "
            f"against the end-of-PREP share (v1: {100 * m1['coverage90_vs_prep_end']:.0f}%). "
            "The PREP stops at 93.2% of actas and differs from the cómputo (end-of-PREP raw share "
            f"{100 * prep_end:.2f}% vs cómputo {100 * fin1['final_share']:.2f}%). v1 stays the headline.")
    meta["notes"] = [n for n in meta["notes"] if not n.startswith("v2 (sensitivity")] + [note]
    (SITE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    # ---- stats
    st = json.loads((SITE / "stats.json").read_text())
    st["sensitivity"] = {
        "label": LABEL, "config": "configs/mx2024_v2.toml",
        "calibration_nights": {
            "rule": "smallest tau with pooled 90% coverage >= 0.90 and worst night >= 0.75 over minutes with 1-95% "
                    "counted on the nights 2012 and 2018 (real archived order); then (rho, kappa) of lowest mean "
                    "absolute projection error",
            "chosen": {k: cal[k] for k in ("tau", "rho", "kappa", "pooled_coverage", "worst_coverage")},
            "nights": {Path(k).stem.split("_")[-1]: v for k, v in cal["nights"].items()}},
        "v1": m1, "v2": m2,
        "prep_end_share": round(prep_end, 5), "computo_share": round(fin1["final_share"], 5),
    }
    (SITE / "stats.json").write_text(json.dumps(EA._clean(st), ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(EA._clean({"v1": m1, "v2": m2}), indent=1, default=str))


if __name__ == "__main__":
    main()
