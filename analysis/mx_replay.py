"""Replay a frozen Mexico config once: the PREP count minute by minute, the section-level model, Polymarket prices.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_replay.py configs/mx2024_v1.toml

Needs $BRUNDER_DATA/raw/<files in the config> (docs/mexico.md) and the Polymarket event archived with
`brunder.polymarket.download_event` ($BRUNDER_DATA/raw/polymarket/<event>/). Writes results/mexico/<id>/:
  ticks.parquet   one row per minute and market (night.replay's columns: t, scope, price, P, yes_cost, no_cost,
                  edge_yes, edge_no, counted, scope_counted, share, share_sd) plus raw and actas; a market's rows stop
                  at its last price (no stale prices after the market closed)
  path.parquet    one row per minute: counted, actas, raw, share, sd, P (national, P = P(A wins))
  frames.parquet  map frames by federal district: t, ent, district, f (actas counted), n, a, proj
  evaluation.csv  night.evaluate with the config's [trade] rule (settle times, first signal, return)
  final.json      official final share (cómputos), outcome per market, header of the PREP file
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import mexico, night, nowcast
from brunder.paths import POLYMARKET, ROOT

FRAME_TIMES = ("15min", "60min")      # map frames: every 15 min for the first 6 hours, then hourly


def frame_times(opening: pd.Timestamp, end: pd.Timestamp) -> set[pd.Timestamp]:
    early = pd.date_range(opening, opening + pd.Timedelta(hours=6), freq=FRAME_TIMES[0])
    late = pd.date_range(opening + pd.Timedelta(hours=7), end, freq=FRAME_TIMES[1])
    return set(early) | set(late) | {end}


def district_frame(st: pd.DataFrame, s: dict, p: nowcast.Params) -> pd.DataFrame:
    u = mexico.project_units(st, s["prior"], p).merge(s["cat"][["uf", "cd_mun", "ent", "district", "k"]],
                                                      on=["uf", "cd_mun"])
    u = u.merge(st[["uf", "cd_mun", "f"]], on=["uf", "cd_mun"], how="left")
    u = u[u["uf"] != mexico.ZZ]
    u["kc"] = u["f"] * u["k"]
    u["proj_a"] = u["a"] + u["R"] * u["s_hat"]
    u["tot"] = u["n"] + u["R"]
    g = u.groupby(["ent", "district"]).agg(kc=("kc", "sum"), k=("k", "sum"), n=("n", "sum"), a=("a", "sum"),
                                            proj_a=("proj_a", "sum"), tot=("tot", "sum")).reset_index()
    g["f"] = g["kc"] / g["k"]
    g["proj"] = g["proj_a"] / g["tot"]
    return g[["ent", "district", "f", "n", "a", "proj"]]


def main(cfg_path: str) -> None:
    cfg = mexico.load_config(cfg_path)
    out = ROOT / "results" / "mexico" / cfg["id"]
    out.mkdir(parents=True, exist_ok=True)
    s = mexico.setup(cfg)
    p = mexico.params(cfg)
    T = cfg["trade"]
    hs, fee = T.get("half_spread", 0.01), T.get("fee_rate", 0.0)
    opening = pd.Timestamp(cfg["publication"]["opening_utc"])
    end = s["header"]["cut_utc"] + pd.Timedelta(minutes=10)
    times = pd.date_range(opening, end, freq="1min")
    fts = frame_times(opening, times[-1])

    px = {}
    for scope, m in cfg["markets"].items():
        h = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
        px[scope] = h.set_index("time")["price"].sort_index()

    k_all = float(s["cat"]["k"].sum())
    ticks, path, frames = [], [], []
    for t, st in mexico.count_states(s["actas"], s["cat"], times):
        count_result = nowcast.nowcast(st, s["prior"], p)
        r = mexico.apply_quick_count(count_result, cfg, t)
        n = float(st["n"].sum())
        raw = float(st["a"].sum()) / n if n > 0 else np.nan
        actas = float((st["f"] * s["cat"]["k"].to_numpy()).sum()) / k_all
        path.append({"t": t, "counted": r["counted_frac"], "actas": actas, "raw": raw, "share": r["share_hat"],
                     "sd": r["share_sd"], "P": r["p_win"], "count_share": count_result["share_hat"],
                     "count_sd": count_result["share_sd"], "count_P": count_result["p_win"]})
        for scope, m in cfg["markets"].items():
            q = px[scope][:t]
            if q.empty or t > px[scope].index[-1] + pd.Timedelta(minutes=1):
                continue
            pm = float(q.iloc[-1])
            P = r["p_win"] if m["yes_is"] == "a" else 1 - r["p_win"]
            yes_cost = pm + hs + fee * pm * (1 - pm)
            no_cost = (1 - pm) + hs + fee * pm * (1 - pm)
            ticks.append({"t": t, "scope": scope, "price": pm, **night.edges(P, yes_cost, no_cost),
                          "counted": r["counted_frac"], "scope_counted": r["counted_frac"], "share": r["share_hat"],
                          "share_sd": r["share_sd"], "count_share": count_result["share_hat"],
                          "count_share_sd": count_result["share_sd"], "raw": raw, "actas": actas})
        if t in fts:
            frames.append(district_frame(st, s, p).assign(t=t))
    tk, pa, fr = pd.DataFrame(ticks), pd.DataFrame(path), pd.concat(frames, ignore_index=True)
    tk.to_parquet(out / "ticks.parquet")
    pa.to_parquet(out / "path.parquet")
    fr.to_parquet(out / "frames.parquet")
    outcomes = {scope: int(m["outcome"]) for scope, m in cfg["markets"].items()}
    ev = night.evaluate(tk, outcomes, T["min_edge"], T["min_counted"], T.get("favoured_only", False))
    ev.to_csv(out / "evaluation.csv", index=False)
    final = {"final_share": s["final_share"], "outcomes": outcomes, "header": {k: str(v) for k, v in s["header"].items()},
             "prep_end_share_raw": float(pa["raw"].iloc[-1]), "prep_end_actas": float(pa["actas"].iloc[-1]),
             "imputed_prior_units": int(s["prior"]["imputed"].sum()), "units": len(s["prior"]),
             "counted_actas": int(s["actas"]["counted"].sum()), "expected_actas": len(s["actas"])}
    (out / "final.json").write_text(json.dumps(final, indent=1))
    pd.set_option("display.width", 200)
    print(ev.to_string(index=False))
    print(pa.iloc[::60].round(4).to_string(index=False))
    print(json.dumps(final, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(ROOT / "configs" / "mx2024_v1.toml")))
