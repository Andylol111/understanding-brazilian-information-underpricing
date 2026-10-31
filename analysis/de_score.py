"""Replay the German Bundestag election night of 2025-02-23 against Polymarket (RETROSPECTIVE, not pre-registered).

For every minute at which an archived official page changed what the Bundeswahlleiterin's site showed (national, Land
and Wahlkeis pages, stamps in UTC), `brunder.germany` projects the final second-vote shares and the probability of every
market's YES; scored against the Polymarket resolutions and 1-minute prices. Settings: configs/de2025_v1.toml (chosen on
the 2021 night only). Writes results/germany/{releases.csv,ticks.parquet,final.json,stats.json}.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_score.py [configs/de2025_v1.toml]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import germany as g
from brunder.paths import POLYMARKET, ROOT

OUT = Path(os.environ.get("DE_OUT", ROOT / "results" / "germany"))
SETTLE = 0.9
BINS = [0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.95, 1.0001]


def load(cfg):
    night = g.read_night(g.RAW_DE / "wayback")
    wk, _ = g.read_kerg2(g.RAW_DE / "open" / "kerg2.csv")
    umr = g.read_umrechnung(g.RAW_DE / "open" / "btwkr25_umrechnung_btw21.csv")
    prior = g.make_prior(umr, wk["electorate"], cfg["model"]["bsw_c"])
    return night, wk, prior


def frames(cfg, night, prior, p):
    """One row per minute at which something new was visible: projection, raw count, draws-based probabilities."""
    cal = cfg["calibration"]
    C = g.correlation(cal["corr"], cal["corr_parties"], cfg["model"]["bsw_rho"])
    chol = np.linalg.cholesky(C)
    z = np.random.default_rng(cfg["seed"]).standard_normal((cfg["draws"], g.NP))
    stamps = sorted({*night["bund"]["t"], *night["wk"]["t"], *[t for d in night["land"].values() for t in d["t"]]})
    minutes = sorted({t.ceil("min") for t in stamps})
    bund_t = set(night["bund"]["t"]) | {t for d in night["land"].values() for t in d["t"]}
    rows = []
    for m in minutes:
        st = g.state_at(night, prior, m)
        if st is None:
            continue
        r = g.project(st, night, prior, p)
        d = g.draw_shares(r["share"], r["f_rem"], p, chol, z)
        pr = g.all_probs(d, cfg["markets"])
        two = d[:, 0] / (d[:, 0] + d[:, 1])
        row = {"t": m, "counted": r["counted"], "k": r["k"], "f_rem": r["f_rem"],
               "n_known": len(st["known"]), "page": bool(m in {t.ceil("min") for t in bund_t}),
               "union_two_way": r["share"][0] / (r["share"][0] + r["share"][1]),
               "union_two_way_raw": r["raw"][0] / (r["raw"][0] + r["raw"][1]),
               "union_two_way_sd": float(two.std())}
        row |= {f"share:{a}": float(x) for a, x in zip(g.ALL, r["share"])}
        row |= {f"raw:{a}": float(x) for a, x in zip(g.ALL, r["raw"])}
        row |= {f"sd:{a}": float(x) for a, x in zip(g.ALL, g.party_sd(r["share"], r["f_rem"], p))}
        row |= {f"P:{k}": v for k, v in pr.items()}
        row |= {f"R:{m_['market']}": float(g.rule(m_, r["raw"])) for m_ in cfg["markets"]}
        rows.append(row)
    return pd.DataFrame(rows)


def step(s: pd.Series, times: pd.DatetimeIndex) -> pd.Series:
    return s.reindex(s.index.union(times)).ffill().reindex(times)


def settle_time(p: pd.Series, yes: bool, end: pd.Timestamp):
    """First minute from which the probability of the eventual outcome stays >= 0.9 to `end`; None if never."""
    q = (p if yes else 1 - p).loc[:end].dropna()
    if not len(q):
        return None
    bad = q[~(q >= SETTLE)]
    if not len(bad):
        return q.index[0]
    return None if bad.index[-1] >= q.index[-1] else bad.index[-1] + pd.Timedelta(minutes=1)


def first_reach(p: pd.Series, yes: bool, thr: float, end: pd.Timestamp):
    q = (p if yes else 1 - p).loc[:end].dropna()
    hit = q[q >= thr]
    return hit.index[0] if len(hit) else None


def iso(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def prices(cfg) -> pd.DataFrame:
    out = {}
    for m in cfg["markets"]:
        d = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
        s = d.assign(t=d["time"].dt.floor("min")).groupby("t")["price"].last()
        out[m["market"]] = s
    return pd.DataFrame(out)


def outcomes(cfg) -> dict[str, bool]:
    res = {}
    for ev in sorted({m["event"] for m in cfg["markets"]}):
        mk = pd.read_parquet(POLYMARKET / ev / "markets.parquet").set_index("slug")
        for m in cfg["markets"]:
            if m["event"] == ev:
                res[m["market"]] = bool(mk.loc[m["market"], "last_prices"][0] == 1.0)
    return res


def main(cfg_path: str = str(ROOT / "configs" / "de2025_v1.toml")) -> None:
    cfg = g.load_config(cfg_path)
    p = g.params_of(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    night, wk, prior = load(cfg)
    final = wk[g.ALL].sum() / wk["valid"].sum()
    won = outcomes(cfg)
    chk = {m["market"]: g.rule(m, final.to_numpy()) for m in cfg["markets"]}
    bad = [k for k in won if won[k] != chk[k]]
    print("resolution vs official final: mismatches", bad)
    R = frames(cfg, night, prior, p)
    R.to_csv(OUT / "releases.csv", index=False)
    start, close = pd.Timestamp(cfg["prices"]["start_utc"]), pd.Timestamp(cfg["poll_close_utc"])
    end = pd.Timestamp(cfg["night_end_utc"])
    times = pd.date_range(close, end, freq="1min")
    rel = R.set_index("t").sort_index()
    PX = prices(cfg)
    PXm = PX.reindex(PX.index.union(times)).ffill().reindex(times)
    Pm = {m["market"]: step(rel[f"P:{m['market']}"], times) for m in cfg["markets"]}
    Rm = {m["market"]: step(rel[f"R:{m['market']}"], times) for m in cfg["markets"]}
    cnt = step(rel["counted"], times)
    gate = cnt[cnt >= 0.01].index.min()
    first = rel.index.min()
    last_page = pd.Timestamp(night["bund"]["t"].max())

    ticks = pd.concat([pd.DataFrame({"t": times, "scope": k, "price": PXm[k].to_numpy(), "P": Pm[k].to_numpy()})
                       for k in PXm.columns])
    ticks.to_parquet(OUT / "ticks.parquet")

    # ---- settle per market
    settle = {}
    for m in cfg["markets"]:
        k = m["market"]
        yes = won[k]
        s_model = settle_time(Pm[k], yes, end)
        s_market = settle_time(PXm[k], yes, end)
        s_raw = settle_time(Rm[k].where(cnt.notna()), yes, end)
        s_model_g = max(s_model, gate) if s_model is not None else None
        # 'contested': Polymarket had not settled before the first count information
        contested = s_market is None or s_market > first
        settle[k] = {"model": iso(s_model_g), "model_ungated": iso(s_model), "polymarket": iso(s_market),
                     "kalshi": None, "raw": iso(s_raw), "contested": bool(contested),
                     "outcome": "yes" if yes else "no",
                     "lead_min": None if s_model_g is None or s_market is None
                     else float((s_market - s_model_g).total_seconds() / 60),
                     "model_reach90": iso(first_reach(Pm[k], yes, 0.9, end)),
                     "model_reach95": iso(first_reach(Pm[k], yes, 0.95, end)),
                     "pm_reach90": iso(first_reach(PXm[k], yes, 0.9, end)),
                     "pm_reach95": iso(first_reach(PXm[k], yes, 0.95, end))}

    # ---- Brier by % counted (model vs market), all markets, minutes from first frame to the last national page
    win = (times >= first) & (times <= last_page)
    rows = []
    for m in cfg["markets"]:
        k = m["market"]
        y = float(won[k])
        d = pd.DataFrame({"t": times[win], "counted": cnt[win].to_numpy(), "model": Pm[k][win].to_numpy(),
                          "market": PXm[k][win].to_numpy()}).dropna()
        d["market_id"] = k
        d["event"] = m["event"]
        d["bm"] = (d["model"] - y) ** 2
        d["bk"] = (d["market"] - y) ** 2
        rows.append(d)
    B = pd.concat(rows)
    B["bin"] = pd.cut(B["counted"], BINS, right=False)
    by_bin = B.groupby("bin", observed=True).agg(minutes=("bm", "size"), brier_model=("bm", "mean"),
                                                 brier_market=("bk", "mean"))
    by_bin_ex = B[~B["market_id"].map(lambda k: settle[k]["polymarket"] is not None
                                      and settle[k]["polymarket"] <= iso(first))].groupby("bin", observed=True).agg(
        minutes=("bm", "size"), brier_model=("bm", "mean"), brier_market=("bk", "mean"))
    ev_groups = B.groupby("event")
    rng = np.random.default_rng(1)
    names = list(ev_groups.groups)
    sums = {n: (ev_groups.get_group(n)["bm"].sum() - ev_groups.get_group(n)["bk"].sum(), len(ev_groups.get_group(n)))
            for n in names}
    diffs = []
    for _ in range(2000):
        pick = rng.choice(len(names), len(names))
        diffs.append(sum(sums[names[i]][0] for i in pick) / sum(sums[names[i]][1] for i in pick))
    ci = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
    per_market = B.groupby("market_id").agg(bm=("bm", "mean"), bk=("bk", "mean"))
    # contested-market Brier (markets Polymarket had not already settled before the first frame)
    cont = [k for k, v in settle.items() if v["contested"]]
    Bc = B[B["market_id"].isin(cont)]

    # ---- accuracy of the projected shares
    err = {}
    for cut in (0.01, 0.05, 0.10, 0.25, 0.50):
        idx = rel.index[rel["counted"] >= cut]
        if not len(idx):
            continue
        r0 = rel.loc[idx[0]]
        err[f"{int(cut * 100)}%"] = {"t": iso(idx[0]), "counted": float(r0["counted"]),
                                     "model": {a: round(100 * (r0[f"share:{a}"] - final[a]), 3) for a in g.ALL},
                                     "raw": {a: round(100 * (r0[f"raw:{a}"] - final[a]), 3) for a in g.ALL},
                                     "model_two_way_union": round(100 * (r0["union_two_way"] - final["Union"] / (final["Union"] + final["AfD"])), 3),
                                     "raw_two_way_union": round(100 * (r0["union_two_way_raw"] - final["Union"] / (final["Union"] + final["AfD"])), 3)}
    cover = {}
    sub = rel[(rel["counted"] >= 0.01) & (rel["counted"] <= 0.95)]
    z = pd.DataFrame({a: (final[a] - sub[f"share:{a}"]) / sub[f"sd:{a}"] for a in g.ALL})
    for a in g.ALL:
        cover[a] = {"coverage90": float((z[a].abs() <= 1.645).mean()), "mean_z": float(z[a].mean()), "n": int(len(z))}
    cover["all"] = {"coverage90": float((z.abs() <= 1.645).to_numpy().mean()), "mean_z": float(z.to_numpy().mean()),
                    "n": int(z.size)}

    res = {"final_share": {a: float(final[a]) for a in g.ALL},
           "final_votes": {a: float(wk[a].sum()) for a in g.ALL}, "valid": float(wk["valid"].sum()),
           "resolution_mismatches": bad, "first_frame": iso(first), "gate_1pct": iso(gate), "last_page": iso(last_page),
           "n_frames": int(len(R)), "settle": settle,
           "brier": {"window": "minutes from the first count information to the last national page "
                               f"({iso(first)} to {iso(last_page)})",
                     "by_counted_bin": {str(i): {"minutes": int(r.minutes), "brier_model": round(float(r.brier_model), 4),
                                                 "brier_market": round(float(r.brier_market), 4)}
                                        for i, r in by_bin.iterrows()},
                     "by_counted_bin_markets_not_settled_before_first_frame": {
                         str(i): {"minutes": int(r.minutes), "brier_model": round(float(r.brier_model), 4),
                                  "brier_market": round(float(r.brier_market), 4)} for i, r in by_bin_ex.iterrows()},
                     "all": {"markets": len(cfg["markets"]), "minutes": int(len(B)),
                             "brier_model": round(float(B["bm"].mean()), 4), "brier_market": round(float(B["bk"].mean()), 4),
                             "diff": round(float(B["bm"].mean() - B["bk"].mean()), 4), "diff_ci_event_cluster": ci,
                             "markets_model_better": int((per_market["bm"] < per_market["bk"]).sum()),
                             "markets_market_better": int((per_market["bm"] > per_market["bk"]).sum())},
                     "contested": {"markets": len(cont), "minutes": int(len(Bc)),
                                   "brier_model": round(float(Bc["bm"].mean()), 4),
                                   "brier_market": round(float(Bc["bk"].mean()), 4)}},
           "early_error": err, "coverage90": cover}
    (OUT / "stats.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: res[k] for k in ("first_frame", "gate_1pct", "n_frames")}))
    print(json.dumps(res["brier"]["all"]), json.dumps(res["brier"]["contested"]))
    for k, v in settle.items():
        if v["contested"]:
            print(f"{k[:58]:58s} {v['outcome']:3s} model {v['model'] and v['model'][11:16]} pm {v['polymarket'] and v['polymarket'][11:16]} "
                  f"lead {v['lead_min']}  reach90 m {v['model_reach90'] and v['model_reach90'][11:16]} pm {v['pm_reach90'] and v['pm_reach90'][11:16]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
