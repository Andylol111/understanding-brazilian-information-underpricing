"""Export the small JSON files the GitHub Pages site (site/) draws from.

Everything comes from cached pipeline outputs (results/night/*, results/history/*), so every number on
the page traces back to code here: run `python analysis/site_data.py` after the replays and
`analysis/night_figures.py cache`. Writes site/data/{nights,catchup,binned,races,robustness}.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import underpricing_figures as U

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = ROOT / "site" / "data"
BRT = "America/Sao_Paulo"
NIGHTS = {
    "r2": ("night_r2_v1", "2026-10-25 17:00", "2026-10-25 21:30"),
    "r1": ("brazil_2026_r1_dev_mun", "2026-10-04 17:00", "2026-10-04 21:30"),
}
GOV = {"ac": "Acre", "am": "Amazonas", "df": "Distrito Federal", "es": "Espírito Santo", "rn": "Rio Grande do Norte", "to": "Tocantins"}


def _r(x, n=4):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def nights() -> dict:
    out = {}
    for key, (cid, lo, hi) in NIGHTS.items():
        t = pd.read_parquet(RES / "night" / cid / "ticks.parquet")
        t = t[t.scope == "br"].drop_duplicates("t").copy()
        t["t"] = pd.to_datetime(t["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
        tally = pd.read_parquet(RES / "night" / cid / "tally.parquet")
        tally["t"] = pd.to_datetime(tally["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
        tally = tally.set_index("t").resample("1min").last().dropna().reset_index()
        m = t.merge(tally[["t", "raw_share"]], on="t", how="outer").sort_values("t")
        m = m[(m.t >= lo) & (m.t <= hi)]
        cols = ["price", "P", "share", "share_sd", "counted", "raw_share"]
        m[cols] = m[cols].ffill()
        final = json.loads((RES / "night" / cid / "final.json").read_text())["final_share"]
        rows = [{"t": r.t.strftime("%H:%M"), "price": _r(r.price), "P": _r(r.P), "share": _r(r.share), "sd": _r(r.share_sd),
                 "raw": _r(r.raw_share), "counted": _r(r.counted)} for r in m.itertuples()]
        out[key] = {"final": round(final, 4), "rows": rows}
    return out


def catchup() -> dict:
    out = {}
    for label, cids in U.POOLS:
        pts = []
        for h in U.HORIZONS:
            s, lo, hi, _, _ = U.slope_ci(U.pool(cids, h), b=300)
            pts.append({"h": h, "s": _r(s, 3), "lo": _r(lo, 3), "hi": _r(hi, 3)})
        out[label] = pts
    return out


def binned(nbins: int = 10) -> dict:
    out = {}
    for label, cids in U.POOLS:
        d = U.pool(cids, 30)
        d["bin"] = pd.qcut(d["gap"], nbins, labels=False, duplicates="drop")
        b = d.groupby("bin").agg(gap=("gap", "mean"), move=("move", "mean"), n=("gap", "size")).reset_index()
        s, lo, hi, r, k = U.slope_ci(d)
        out[label] = {"slope": _r(s, 3), "lo": _r(lo, 3), "hi": _r(hi, 3), "n": len(d), "markets": int(d.scope.nunique()),
                      "bins": [{"gap": _r(x.gap), "move": _r(x.move), "n": int(x.n)} for x in b.itertuples()]}
    return out


def races() -> list[dict]:
    rows = []

    def hm(ts):
        return None if pd.isna(ts) else pd.Timestamp(ts).tz_convert(BRT).strftime("%H:%M")

    for cid, race, ev in (("brazil_2026_r1_dev_mun", "President, first round 2026", "in-sample"),
                          ("night_r2_v1", "President, runoff 2026", "pre-specified")):
        e = pd.read_csv(RES / "night" / cid / "evaluation.csv", parse_dates=["model_0.9", "market_0.9"])
        x = e[e.scope == "br"].iloc[0]
        rows.append({"race": race, "evidence": ev, "vs": "market", "model": hm(x["model_0.9"]), "other": hm(x["market_0.9"]),
                     "lead": _r(x["lead_min"], 0)})
    for u, name in GOV.items():
        e = pd.read_csv(RES / "night" / f"night_gov_r2_{u}_v1" / "evaluation.csv", parse_dates=["model_0.9", "market_0.9"])
        x = e.iloc[0]
        rows.append({"race": f"Governor, {name}, runoff 2026", "evidence": "pre-specified", "vs": "market",
                     "model": hm(x["model_0.9"]), "other": hm(x["market_0.9"]), "lead": _r(x["lead_min"], 0)})
    for rid, race in (("hist_2022_r1_v1", "President, first round 2022"), ("hist_2022_r2_v1", "President, runoff 2022")):
        s = json.loads((RES / "history" / rid / "summary.json").read_text())
        rows.append({"race": race, "evidence": "pre-specified", "vs": "raw tally", "model": s["model_settles"],
                     "other": s["raw_tally_settles"], "lead": _r(s["lead_min"], 0)})
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {"nights": nights(), "races": races(), "binned": binned(), "catchup": catchup(), "robustness": U.robustness()}
    for name, obj in files.items():
        (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        print(name, (OUT / f"{name}.json").stat().st_size, "bytes")
    for r in files["races"]:
        print(r)


if __name__ == "__main__":
    main()
