"""Score the frozen Mexico replay with the edge-audit method (docs/edge_audit.md): accuracy and executable profit.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mx_score.py configs/mx2024_v1.toml

Inputs: results/mexico/<id>/{ticks,path}.parquet, final.json (analysis/mx_replay.py) and the archived Polymarket
event ($BRUNDER_DATA/raw/polymarket/<event>/: prices, all fills with is_taker). Uses analysis/edge_audit.py's
functions unchanged (share_error, coverage, pwin_minutes / pwin_scores / reliability, size_table, settle) and
night_capacity.same_side_fills for the fills, so the numbers are comparable with Brazil and Israel.

Accuracy race "mx": national two-way share Sheinbaum / (Sheinbaum + Gálvez) against the official cómputo; P(win) is
scored on the Sheinbaum market only (the Gálvez market is the same event's complement). Profit: the frozen rule on
both markets; fills = taker trades on our side at or better than the limit in [signal + latency, signal + horizon),
latency 0 / 60 s, horizon 5 / 30 min, limit = signal price, +2c, +5c (edge_audit's Brazil execution).
Writes results/mexico/<id>/score.json (the standard stats.json blocks + accuracy + profit) and audit tables.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_audit as EA
from night_capacity import same_side_fills

from brunder import mexico, night
from brunder.paths import POLYMARKET, ROOT

RACE = "mx"
HEAD = (60, 30, 0.0)                 # headline execution, as edge_audit's Brazil: 60 s latency, 30 min, signal price
EVIDENCE = "not-preregistered"       # contract label (integrator's call); details in EV_NOTE
EV_NOTE = ("not pre-registered: settings chosen on the 2018 count only (configs/mx2024_dev_2018.toml), but "
           "configs/mx2024_v1.toml was first committed after scoring, and one exploratory run on the 2024 count "
           "(tau = 0.015, 30-minute ticks) was looked at before the config was frozen; outcome long known")


def _iso(t):
    return EA._iso(t)


def load(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    d = ROOT / "results" / "mexico" / cfg["id"]
    tk = pd.read_parquet(d / "ticks.parquet")
    pa = pd.read_parquet(d / "path.parquet")
    for x in (tk, pa):
        x["t"] = pd.to_datetime(x["t"], utc=True)
    return tk, pa, json.loads((d / "final.json").read_text())


def market_series(cfg: dict, scope: str) -> pd.Series:
    m = cfg["markets"][scope]
    h = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
    return h.set_index("time")["price"].sort_index()


def taker_trades(cfg: dict, scope: str) -> tuple[pd.DataFrame, str]:
    m = cfg["markets"][scope]
    tr = pd.read_parquet(POLYMARKET / m["event"] / "trades" / f"{m['market']}.parquet")
    mk = pd.read_parquet(POLYMARKET / m["event"] / "markets.parquet").set_index("slug")
    return tr[tr["is_taker"]].copy(), mk.loc[m["market"], "yes_token"]


def accuracy(cfg: dict, tk: pd.DataFrame, pa: pd.DataFrame, fin: dict) -> tuple[dict, dict]:
    final, y = fin["final_share"], int(fin["final_share"] > 0.5)
    sh = tk[tk["scope"] == "sheinbaum"].set_index("t")["price"]
    rows = pa.assign(price=pa["t"].map(sh))[["t", "counted", "share", "sd", "raw", "P", "price"]]
    ev = pd.read_csv(ROOT / "results" / "mexico" / cfg["id"] / "evaluation.csv").set_index("scope")
    r = {"eid": cfg["eid"], "race": RACE, "rows": rows, "final": final, "outcome": y,
         "model_settle": pd.Timestamp(ev.loc["sheinbaum", "model_0.9"]),
         "market_settle": pd.Timestamp(ev.loc["sheinbaum", "market_0.9"])}
    se, cv = EA.share_error(r), EA.coverage(r)
    w = EA.pwin_minutes(r)
    pw = EA.pwin_scores(w, True)
    pw["reliability_model"], pw["reliability_market"] = EA.reliability(w, "P"), EA.reliability(w, "price")
    block = {
        "evidence": EVIDENCE,
        "share_error_pt": {RACE: {f"{int(x['q'] * 100)}%": {"model": x["model_pt"], "raw": x["raw_pt"]} for x in se}},
        "share_error_mae_pt": {f"{int(x['q'] * 100)}%": {"model": None if x["model_pt"] is None else abs(x["model_pt"]),
                                                        "raw": None if x["raw_pt"] is None else abs(x["raw_pt"]),
                                                        "races": int(x["model_pt"] is not None)} for x in se},
        "coverage90": {RACE: {c["window"]: {"coverage90": c["coverage90"], "mean_z": c["mean_z"], "n": c["n"]}
                              for c in cv}},
        "coverage90_pooled": {c["window"]: {"races": int(c["n"] > 0), "median": c["coverage90"], "min": c["coverage90"],
                                            "races_below_80pct": int(c["coverage90"] is not None and c["coverage90"] < 0.8),
                                            "minutes_weighted": c["coverage90"]} for c in cv},
        "pwin": pw,
        "pwin_window": "minutes from >= 1% counted until both model and market stayed >= 0.9 on the winner; both had "
                       "settled before the count reached 1%, so no minute qualifies",
        "outcome": "official cómputos distritales (INE SICEE 2024); Polymarket markets resolved 2024-06-03",
    }
    detail = {"share_error": se, "coverage": cv, "pwin_minutes": len(w)}
    return block, detail


def signals(cfg: dict, tk: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    T = cfg["trade"]
    outcomes = {s: int(m["outcome"]) for s, m in cfg["markets"].items()}
    ev = night.evaluate(tk, outcomes, T["min_edge"], T["min_counted"], True)
    sig = ev.dropna(subset=["entry"]).merge(tk, left_on=["scope", "entry"], right_on=["scope", "t"], how="left")
    rows = []
    for r in sig.itertuples():
        trades, yes_tok = taker_trades(cfg, r.scope)
        side_yes = r.side == "YES"
        cost = r.yes_cost if side_yes else r.no_cost
        p_fav = r.P if side_yes else 1 - r.P
        won = int((r.outcome == 1) == side_yes)
        entry = pd.Timestamp(r.entry)
        for L, N, s in itertools.product(EA.BR_LATENCY_S, EA.BR_HORIZON_MIN, EA.BR_SLIPS):
            lim = r.price + s if side_yes else r.price - s
            fs = same_side_fills(trades, yes_tok, side_yes, lim)
            w = (fs["time"] >= entry + pd.Timedelta(seconds=L)) & (fs["time"] < entry + pd.Timedelta(minutes=N))
            c = cost + s
            rows.append({"eid": cfg["eid"], "market_key": f"{cfg['eid']}:{r.scope}", "market": cfg["markets"][r.scope]["market"],
                         "side": r.side, "entry": entry, "price": r.price, "cost": cost, "P_fav": p_fav,
                         "edge": p_fav - cost, "won": won, "latency_s": L, "horizon_min": N, "slip": s,
                         "avail": float(fs.loc[w, "notional"].sum()), "all_in": c, "ret": (1 / c - 1) if won else -1.0,
                         "rule_ok": bool(p_fav - c >= T["min_edge"] - 1e-12)})
    cols = ["eid", "market_key", "market", "side", "entry", "price", "cost", "P_fav", "edge", "won", "latency_s",
            "horizon_min", "slip", "avail", "all_in", "ret", "rule_ok"]
    return ev, pd.DataFrame(rows, columns=cols)


def best_edge(cfg: dict, tk: pd.DataFrame) -> dict:
    """Why no signal: the largest favoured-side edge after costs each market offered once >= 1% was counted."""
    T, out = cfg["trade"], {}
    for scope, g in tk[tk["scope_counted"] >= T["min_counted"]].groupby("scope"):
        fav = np.where(g["P"] >= 0.5, g["edge_yes"], g["edge_no"])
        i = int(np.argmax(fav))
        out[scope] = {"max_edge": EA._r(fav[i]), "t": _iso(g["t"].iloc[i]), "P": EA._r(g["P"].iloc[i]),
                      "price": EA._r(g["price"].iloc[i]), "minutes": len(g),
                      "last_t": _iso(g["t"].iloc[-1])}
    return out


def profit(cfg: dict, BR: pd.DataFrame) -> tuple[dict, list[dict]]:
    def summarize(sig: pd.DataFrame, table: list[dict]) -> dict:
        n, k = len(sig), int(sig["won"].sum()) if len(sig) else 0
        mx = next(t for t in table if t["size"] == "max")
        return {"signals": n, "won": k, "hit_rate": EA._r(k / n, 3) if n else None, "hit_rate_ci": EA._cp(k, n),
                "max_deployed": mx["deployed"], "max_profit": mx["pnl"], "max_profit_ci": mx["pnl_ci"],
                "max_return_per_usd": mx["return_per_usd"], "p_market_fair": mx["p_market_fair"],
                "largest_trade_share": mx.get("largest_trade_share"), "pnl_without_largest": mx.get("pnl_without_largest"),
                "by_size": {str(t["size"]): {kk: t.get(kk) for kk in ("fillable", "filled_any", "deployed", "pnl",
                                                                      "return_per_usd", "pnl_ci", "return_ci",
                                                                      "p_market_fair", "largest_trade_share")}
                            for t in table if t["size"] != "max"}}

    variants, rows = {}, []
    for L, N, s in itertools.product(EA.BR_LATENCY_S, EA.BR_HORIZON_MIN, EA.BR_SLIPS):
        for rule_only in ((False, True) if s > 0 else (False,)):
            x = BR[(BR.latency_s == L) & (BR.horizon_min == N) & (BR.slip == s)].copy().reset_index(drop=True)
            if rule_only:
                x.loc[~x["rule_ok"], "avail"] = 0.0
            tab = EA.size_table(x, "avail", "ret", "all_in")
            key = f"trades_lat{L}s_{N}min_slip{int(round(s * 100))}c" + ("_rule" if rule_only else "")
            variants[key] = summarize(x, tab)
            rows += [{"execution": key, **t} for t in tab]
    L, N, s = HEAD
    head = variants[f"trades_lat{L}s_{N}min_slip{int(s * 100)}c"]
    block = {"evidence": EVIDENCE,
             "rule": "frozen night rule: favoured side only, edge >= 5c after costs, >= 1% of the market counted, first "
                     "signal per market, held to resolution",
             "execution": f"real taker trades on our side at or better than the signal price, from {L} s to {N} min "
                          "after the signal; return at the signal's all-in cost (price + half-spread + fee)",
             **head, "variants": {k: {kk: v[kk] for kk in ("max_deployed", "max_profit")} for k, v in variants.items()}}
    return block, rows


def fast_signal_stats(cfg: dict, pa: pd.DataFrame, final: float) -> dict | None:
    """Comparison block for a separately scored official quick-estimate version."""
    q = cfg.get("quick_count")
    if not q:
        return None
    release = pd.Timestamp(q["published_utc"])
    post = pa["t"] >= release
    counted = pa["counted"].between(0.01, 0.95, inclusive="right")
    lo = q["a_low"] / (q["a_low"] + q["b_high"])
    hi = q["a_high"] / (q["a_high"] + q["b_low"])

    def projection(name: str, share: pd.Series, sd: pd.Series) -> dict:
        z = (final - share) / sd
        return {
            "mae_pt_after_release": EA._r(100 * (share[post] - final).abs().mean(), 3),
            "coverage90_after_release": EA._r((z[post].abs() < 1.645).mean(), 3),
            "coverage90_1_95": EA._r((z[counted].abs() < 1.645).mean(), 3),
            "error_pt_at_release": EA._r(100 * (share[post].iloc[0] - final), 3),
            "name": name,
        }

    def threshold_times(t: pd.Series, p: pd.Series, threshold: float) -> dict:
        ok = p >= threshold
        first = t[ok.to_numpy()].iloc[0] if ok.any() else pd.NaT
        stable = EA.settle(t, ok)
        opening = pa["t"].iloc[0]
        return {"first": _iso(first), "stable": _iso(stable),
                "minutes_from_count_open": None if pd.isna(stable) else int((stable - opening).total_seconds() / 60)}

    markets = {}
    brier = {"quick_count_model": [], "pure_count_v1": [], "polymarket": []}
    brier_contested = {k: [] for k in brier}
    for scope, market in cfg["markets"].items():
        quick_yes = pa["P"] if market["yes_is"] == "a" else 1 - pa["P"]
        count_yes = pa["count_P"] if market["yes_is"] == "a" else 1 - pa["count_P"]
        quick_resolved = quick_yes if market["outcome"] else 1 - quick_yes
        count_resolved = count_yes if market["outcome"] else 1 - count_yes
        price = market_series(cfg, scope)
        price_t = pd.Series(price.index)
        market_resolved = price if market["outcome"] else 1 - price
        rows = {}
        for threshold in (0.90, 0.95):
            key = str(int(threshold * 100))
            rows[key] = {
                "quick_count_model": threshold_times(pa["t"], quick_resolved, threshold),
                "pure_count_v1": threshold_times(pa["t"], count_resolved, threshold),
                "polymarket": threshold_times(price_t, pd.Series(market_resolved.to_numpy()), threshold),
            }
        market_minutes = rows["90"]["polymarket"]["minutes_from_count_open"]
        markets[scope] = {
            "contested_at_90_when_count_opened": market_minutes is None or market_minutes >= 0,
            "thresholds": rows,
        }
        grid = pd.DatetimeIndex(pa["t"])
        pm = price.reindex(price.index.union(grid)).ffill().reindex(grid)
        pm[grid > price.index[-1] + pd.Timedelta(minutes=1)] = np.nan
        pm_resolved = pm if market["outcome"] else 1 - pm
        valid = pm_resolved.notna()
        scores = {
            "quick_count_model": ((1 - quick_resolved[valid.to_numpy()]) ** 2).tolist(),
            "pure_count_v1": ((1 - count_resolved[valid.to_numpy()]) ** 2).tolist(),
            "polymarket": ((1 - pm_resolved[valid]) ** 2).tolist(),
        }
        for name, values in scores.items():
            brier[name].extend(values)
            if markets[scope]["contested_at_90_when_count_opened"]:
                brier_contested[name].extend(values)

    return {
        "label": cfg["label"],
        "published_utc": _iso(release),
        "two_way_interval": {"low": EA._r(lo, 6), "high": EA._r(hi, 6),
                             "midpoint": EA._r((lo + hi) / 2, 6)},
        "projection": {
            "pure_count_v1": projection("pure count v1", pa["count_share"], pa["count_sd"]),
            "quick_count": projection("v1 + official quick count", pa["share"], pa["sd"]),
        },
        "markets": markets,
        "contested_market_count": sum(v["contested_at_90_when_count_opened"] for v in markets.values()),
        "brier_from_count_open": {k: EA._r(np.mean(v), 6) for k, v in brier.items()},
        "brier_contested_markets": ({k: EA._r(np.mean(v), 6) for k, v in brier_contested.items()}
                                      if any(brier_contested.values()) else None),
        "note": "Markets already settled at 90% before count opening are classified as decided before the count; "
                "their negative minute values are not model leads.",
    }


def standard_stats(cfg: dict, tk: pd.DataFrame, pa: pd.DataFrame, fin: dict) -> dict:
    final = fin["final_share"]
    rows = pa.copy()
    z = (final - rows["share"]) / rows["sd"]
    w = rows["counted"].between(0.01, 0.95, inclusive="right") & np.isfinite(z)
    early = {}
    for q in (0.01, 0.05, 0.10, 0.25):
        r = rows[rows["counted"] >= q]
        early[f"{int(q * 100)}%"] = ({"model": None, "raw": None} if r.empty else
                                     {"model": EA._r(100 * (r.iloc[0]["share"] - final), 2),
                                      "raw": EA._r(100 * (r.iloc[0]["raw"] - final), 2)})
    y = final > 0.5
    model_settle = EA.settle(rows["t"], (rows["P"] if y else 1 - rows["P"]) >= 0.9)
    started = rows["counted"] > 0
    raw_settle = EA.settle(rows.loc[started, "t"], ((rows["raw"] > 0.5) if y else (rows["raw"] < 0.5))[started])
    px = market_series(cfg, "sheinbaum")
    px = px[px.index <= rows["t"].iloc[-1]]
    pm_settle = EA.settle(pd.Series(px.index), pd.Series((px >= 0.9).to_numpy() if y else (px <= 0.1).to_numpy()))
    gate = rows.loc[rows["counted"] >= cfg["trade"]["min_counted"], "t"].iloc[0]
    gm = max(model_settle, gate)
    stats = {
        "calibration": {RACE: {"coverage90_1_95": EA._r((z[w].abs() < 1.645).mean(), 3), "mean_z": EA._r(z[w].mean(), 2)}},
        "settle": {RACE: {"model": _iso(model_settle), "polymarket": _iso(pm_settle), "kalshi": None,
                          "raw": _iso(raw_settle)}},
        "catchup": {"polymarket": None, "kalshi": None},
        "early_error": {RACE: early},
        "capacity": {"polymarket": None, "kalshi": None},
        "arbitrage": None,
        "settle_gated": {RACE: {"gate": _iso(gate), "model": _iso(gm), "polymarket": _iso(pm_settle), "kalshi": None,
                                "raw": _iso(raw_settle),
                                "lead": EA._r((pm_settle - gm).total_seconds() / 60, 0), "lead_vs": "polymarket"}},
        "evidence": {k: EVIDENCE for k in ("calibration", "settle", "catchup", "early_error", "capacity",
                                           "arbitrage", "settle_gated")},
        "evidence_note": EV_NOTE,
        "reasons": {"catchup.polymarket": "the market was already >= 0.9 on Sheinbaum before the count began, so there "
                                          "is no repricing during the count to measure",
                    "capacity.polymarket": "no gated signal fired (best favoured-side edge after costs stayed below 5c)",
                    "arbitrage": "no Kalshi data (Kalshi data licence, docs/kalshi.md)",
                    "settle.kalshi": "no Kalshi data (Kalshi data licence, docs/kalshi.md)",
                    "catchup.kalshi": "no Kalshi data (Kalshi data licence, docs/kalshi.md)",
                    "capacity.kalshi": "no Kalshi data (Kalshi data licence, docs/kalshi.md)"},
    }
    fast = fast_signal_stats(cfg, pa, final)
    if fast:
        stats["fast_signal"] = fast
    return stats


def main(cfg_path: str) -> None:
    cfg = mexico.load_config(cfg_path)
    out = ROOT / "results" / "mexico" / cfg["id"]
    tk, pa, fin = load(cfg)
    stats = standard_stats(cfg, tk, pa, fin)
    acc, acc_detail = accuracy(cfg, tk, pa, fin)
    ev, BR = signals(cfg, tk)
    BR.to_csv(out / "profit_signals.csv", index=False)
    prof, size_rows = profit(cfg, BR)
    pd.DataFrame(size_rows).to_csv(out / "profit_by_size.csv", index=False)
    stats["accuracy"], stats["profit"] = acc, prof
    extra = {"best_edge_after_gate": best_edge(cfg, tk), "evaluation": ev.to_dict("records"), "accuracy": acc_detail,
             "final_share": fin["final_share"], "end_of_prep": {k: fin[k] for k in ("prep_end_share_raw", "prep_end_actas")},
             "end_model_share": float(pa["share"].iloc[-1])}
    (out / "score.json").write_text(json.dumps(EA._clean({"stats": stats, "extra": extra}), indent=1, ensure_ascii=False,
                                               default=str))
    print(json.dumps(EA._clean({k: stats[k] for k in ("calibration", "settle", "settle_gated", "early_error")}), indent=1))
    print(json.dumps(EA._clean(acc), ensure_ascii=False)[:1500])
    print(json.dumps(EA._clean({k: v for k, v in prof.items() if k != "variants"}))[:800])
    print(json.dumps(EA._clean(extra["best_edge_after_gate"]), indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(ROOT / "configs" / "mx2024_v1.toml")))
