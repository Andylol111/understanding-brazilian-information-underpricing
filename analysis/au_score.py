"""Replay the Australian federal election night of 2025-05-03 against Polymarket and Kalshi (RETROSPECTIVE, not pre-registered).

For every feed frame the AEC published (in order, each used from its own creation time), `brunder.australia` projects
the final two-candidate result of the 150 divisions, simulates the seat counts and gives the probability of each
market's YES. Scored against the markets' resolutions (checked against the AEC's final results) and 1-minute prices.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/au_observe.py 2025
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/au_score.py configs/au2025_v1.toml
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import australia as au
from brunder import kalshi
from brunder.paths import DATA, POLYMARKET, RAW, ROOT

OUT = Path(os.environ.get("AU_OUT", ROOT / "results" / "australia" / "v1"))
SETTLE = 0.9
BINS = [0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.95, 1.0001]


def iso(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def key(m: dict) -> str:
    return f"{m['platform']}:{m['market']}"


def step(s: pd.Series, times: pd.DatetimeIndex) -> pd.Series:
    return s.reindex(s.index.union(times)).ffill().reindex(times)


def settle_time(p: pd.Series, yes: bool, end: pd.Timestamp):
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


def prices(cfg, times) -> pd.DataFrame:
    out = {}
    for m in cfg["markets"]:
        if m["platform"] == "polymarket":
            d = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
            out[key(m)] = d.assign(t=d["time"].dt.floor("min")).groupby("t")["price"].last()
        else:
            c = pd.read_parquet(RAW / "kalshi" / "australia" / m["event"] / "candles" / f"{m['market']}.parquet")
            c = c[c["time"] > pd.Timestamp(cfg["prices"]["start_utc"])]
            out[key(m)] = kalshi.prob_from_candles(c, times)
    df = pd.DataFrame(out)
    return df.reindex(df.index.union(times)).ffill().reindex(times)


def resolutions(cfg) -> dict[str, bool]:
    res = {}
    for m in cfg["markets"]:
        if m["platform"] == "polymarket":
            mk = pd.read_parquet(POLYMARKET / m["event"] / "markets.parquet").set_index("slug")
            res[key(m)] = bool(mk.loc[m["market"], "last_prices"][0] == 1.0)
        else:
            mk = pd.read_parquet(RAW / "kalshi" / "australia" / m["event"] / "markets.parquet").set_index("ticker")
            res[key(m)] = mk.loc[m["market"], "result"] == "yes"
    return res


def replay(cfg: dict, d: dict) -> tuple[pd.DataFrame, list]:
    S, p = d["static"], cfg["model"]
    ids = sorted(S["divs"])
    n = len(ids)
    tot_booth = {did: sum(b["hist_formal"] for b in S["booths"][did]) for did in ids}
    all_hist = sum(S["booth_hist"].values())
    w2pp = np.array([(sum(S["divs"][i]["hist_tpp"].values()) if S["divs"][i]["hist_tpp"] else 0) *
                     S["divs"][i]["enrol"] / max(S["divs"][i]["enrol_hist"], 1) for i in ids], dtype=float)
    w2pp /= w2pp.sum()
    prior2 = np.array([S["divs"][i]["hist_tpp"][1] / sum(S["divs"][i]["hist_tpp"].values()) if S["divs"][i]["hist_tpp"] else 0.5
                       for i in ids])
    rows, frames = [], []
    for t, o in d["obs"]:
        pj = au.project(o, S, p)
        sim = au.simulate(pj, o, S, p, cfg["draws"], np.random.default_rng(cfg["seed"]))
        seats, win = sim["seats"], sim["win"]
        counted = sum(x["booth_hist"] for x in o.values()) / all_hist
        row = {"t": t.ceil("min"), "created": t, "counted": counted, "n_tcp": sum(1 for x in o.values() if x.get("n")), "n_pair": sum(1 for x in pj["pair"] if x),
               "seats_alp": float(seats[:, 0].mean()), "seats_lnc": float(seats[:, 1].mean()),
               "seats_alp_sd": float(seats[:, 0].std()), "mu_nat": pj["mu_nat"]}
        for m in cfg["markets"]:
            row["P:" + key(m)] = float(au.market_hit(m, seats, win, ids).mean())
        # national Labor two-party share: raw count and projection
        a_ct = sum(x["tpp"][1] for x in o.values() if x["tpp"])
        b_ct = sum(x["tpp"][2] for x in o.values() if x["tpp"])
        row["raw2pp"] = a_ct / (a_ct + b_ct) if a_ct + b_ct else None
        proj = np.empty(n)
        ca = np.empty(n)
        fcount = np.empty(n)
        for i, did in enumerate(ids):
            x = o.get(did, {})
            tp = x.get("tpp") or {}
            ca[i] = tp[1] / (tp[1] + tp[2]) if tp and tp.get(1, 0) + tp.get(2, 0) > 0 else -1
            fcount[i] = min(1.0, x.get("booth_hist", 0) / tot_booth[did]) if tot_booth[did] else 0.0
            if pj["pair"][i] == ("ALP", "LNC"):
                proj[i] = pj["mean"][i]
            else:
                proj[i] = prior2[i] + pj["st_mu"].get(pj["state"][i], 0.0)
                if ca[i] >= 0 and fcount[i] >= 0.9:
                    proj[i] = ca[i]
        row["proj2pp"] = float((w2pp * proj).sum())
        wst = {}
        for i in range(n):
            wst[pj["state"][i]] = wst.get(pj["state"][i], 0.0) + w2pp[i]
        var = pj["sd_nat"] ** 2 + sum((wst[s] * pj["st_sd"][s]) ** 2 for s in wst) + sum((w2pp[i] * pj["sd_div"][i]) ** 2 for i in range(n))
        row["sd2pp"] = float(var ** 0.5)
        rows.append(row)
        frames.append({"t": t.ceil("min"), "f": fcount, "a": ca, "proj": proj})
    return pd.DataFrame(rows), frames


def main(cfg_path: str = str(ROOT / "configs" / "au2025_v1.toml")) -> None:
    cfg = tomllib.loads(Path(cfg_path).read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    with open(DATA / "au" / "obs_2025.pkl", "rb") as f:
        d = pickle.load(f)
    S, fin = d["static"], d["final"]
    ids = sorted(S["divs"])
    f_seats, f_win = au.seats_of_final(fin, ids)
    won = resolutions(cfg)
    chk = {key(m): bool(au.market_hit(m, f_seats, f_win, ids)[0]) for m in cfg["markets"]}
    bad = [k for k in won if won[k] != chk[k]]
    print("final seats", dict(zip(au.SEAT_GROUPS, f_seats.tolist())), "| resolution vs AEC final: mismatches", bad)
    R, frames = replay(cfg, d)
    R.to_csv(OUT / "releases.csv", index=False)
    with open(OUT / "frames.pkl", "wb") as f:
        pickle.dump(frames, f)
    close, end = pd.Timestamp(cfg["poll_close_utc"]), pd.Timestamp(cfg["night_end_utc"])
    times = pd.date_range(close, end, freq="1min")
    # frames before the AEC released the two-candidate pairs (6pm) carry no usable projection
    rel = R[R["n_pair"] >= 100].drop_duplicates("t", keep="last").set_index("t").sort_index()
    PX = prices(cfg, times)
    Pm = {key(m): step(rel["P:" + key(m)], times) for m in cfg["markets"]}
    cnt = step(rel["counted"], times)
    first = rel.index[rel["n_tcp"] > 0].min()
    gate = cnt[cnt >= 0.01].index.min()
    last = rel.index.max()
    pd.concat([pd.DataFrame({"t": times, "scope": k, "price": PX[k].to_numpy(), "P": Pm[k].to_numpy()})
               for k in PX.columns]).to_parquet(OUT / "ticks.parquet")

    settle = {}
    for m in cfg["markets"]:
        k = key(m)
        yes = won[k]
        s_model = settle_time(Pm[k], yes, last)
        s_market = settle_time(PX[k], yes, last)
        gm = max(s_model, gate) if s_model is not None else None
        settle[k] = {"model": iso(gm), "model_ungated": iso(s_model), "market": iso(s_market),
                     "contested": bool(s_market is None or s_market > first), "outcome": "yes" if yes else "no",
                     "lead_min": None if gm is None or s_market is None else float((s_market - gm).total_seconds() / 60),
                     "model_reach90": iso(first_reach(Pm[k], yes, 0.9, last)),
                     "model_reach95": iso(first_reach(Pm[k], yes, 0.95, last)),
                     "mkt_reach90": iso(first_reach(PX[k], yes, 0.9, last)),
                     "mkt_reach95": iso(first_reach(PX[k], yes, 0.95, last)),
                     "platform": m["platform"], "race": m["race"]}

    win = (times >= first) & (times <= last)
    rows = []
    for m in cfg["markets"]:
        k = key(m)
        y = float(won[k])
        x = pd.DataFrame({"t": times[win], "counted": cnt[win].to_numpy(), "model": Pm[k][win].to_numpy(),
                          "market": PX[k][win].to_numpy()}).dropna()
        x["market_id"] = k
        x["event"] = m["event"]
        x["platform"] = m["platform"]
        x["bm"] = (x["model"] - y) ** 2
        x["bk"] = (x["market"] - y) ** 2
        rows.append(x)
    B = pd.concat(rows)
    B["bin"] = pd.cut(B["counted"], BINS, right=False)
    agg = dict(minutes=("bm", "size"), brier_model=("bm", "mean"), brier_market=("bk", "mean"))
    by_bin = B.groupby("bin", observed=True).agg(**agg)
    cont = [k for k, v in settle.items() if v["contested"]]
    Bc = B[B["market_id"].isin(cont)]
    by_bin_c = Bc.groupby("bin", observed=True).agg(**agg)
    evg = B.groupby("event")
    sums = {n: (g["bm"].sum() - g["bk"].sum(), len(g)) for n, g in evg}
    rng = np.random.default_rng(1)
    names = list(sums)
    diffs = []
    for _ in range(2000):
        pick = rng.choice(len(names), len(names))
        diffs.append(sum(sums[names[i]][0] for i in pick) / sum(sums[names[i]][1] for i in pick))
    pm_ = B.groupby("market_id").agg(bm=("bm", "mean"), bk=("bk", "mean"))
    pmc = Bc.groupby("market_id").agg(bm=("bm", "mean"), bk=("bk", "mean"))
    cl = Bc.groupby("event")
    sc = {n: (g["bm"].sum() - g["bk"].sum(), len(g)) for n, g in cl}
    nc = list(sc)
    dc = []
    for _ in range(2000):
        pick = rng.choice(len(nc), len(nc))
        dc.append(sum(sc[nc[i]][0] for i in pick) / sum(sc[nc[i]][1] for i in pick))

    # ---- accuracy of the national projection and calibration of the division shares
    fin2 = {i: fin[i]["tpp"] for i in ids if fin[i]["tpp"]}
    a1 = sum(v[1] for v in fin2.values())
    b1 = sum(v[2] for v in fin2.values())
    final2pp = a1 / (a1 + b1)
    err = {}
    for cut in (0.01, 0.05, 0.10, 0.25, 0.50):
        idx = rel.index[rel["counted"] >= cut]
        if not len(idx) or rel.loc[idx[0], "raw2pp"] is None:
            continue
        r0 = rel.loc[idx[0]]
        err[f"{int(cut * 100)}%"] = {"t": iso(idx[0]), "counted": float(r0["counted"]),
                                     "model_2pp": round(100 * (r0["proj2pp"] - final2pp), 3),
                                     "raw_2pp": round(100 * (r0["raw2pp"] - final2pp), 3),
                                     "model_alp_seats": round(float(r0["seats_alp"]) - float(f_seats[0]), 2)}
    sub = rel[(rel["counted"] >= 0.01) & (rel["counted"] <= 0.95)]
    z2 = (final2pp - sub["proj2pp"]) / sub["sd2pp"]
    zs = (f_seats[0] - sub["seats_alp"]) / sub["seats_alp_sd"].clip(lower=0.8)
    cover = {"alp_2pp": {"coverage90": float((z2.abs() <= 1.645).mean()), "mean_z": float(z2.mean()), "n": int(len(z2))},
             "alp_seats": {"coverage90": float((zs.abs() <= 1.645).mean()), "mean_z": float(zs.mean()), "n": int(len(zs))}}

    res = {"final_seats": dict(zip(au.SEAT_GROUPS, f_seats.tolist())), "final_2pp_alp": final2pp,
           "resolution_mismatches": bad, "first_count": iso(first), "gate_1pct": iso(gate), "last_frame": iso(last),
           "n_frames": int(len(R)), "settle": settle,
           "brier": {"window": f"minutes from the first two-candidate count to the last frame ({iso(first)} to {iso(last)})",
                     "by_counted_bin": {str(i): {"minutes": int(r.minutes), "brier_model": round(float(r.brier_model), 4),
                                                 "brier_market": round(float(r.brier_market), 4)} for i, r in by_bin.iterrows()},
                     "by_counted_bin_contested": {str(i): {"minutes": int(r.minutes), "brier_model": round(float(r.brier_model), 4),
                                                           "brier_market": round(float(r.brier_market), 4)} for i, r in by_bin_c.iterrows()},
                     "all": {"markets": len(cfg["markets"]), "minutes": int(len(B)), "brier_model": round(float(B["bm"].mean()), 4),
                             "brier_market": round(float(B["bk"].mean()), 4), "diff": round(float(B["bm"].mean() - B["bk"].mean()), 4),
                             "diff_ci_event_cluster": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
                             "markets_model_better": int((pm_["bm"] < pm_["bk"]).sum()),
                             "markets_market_better": int((pm_["bm"] > pm_["bk"]).sum())},
                     "contested": {"markets": len(cont), "minutes": int(len(Bc)),
                                   "brier_model": round(float(Bc["bm"].mean()), 4) if len(Bc) else None,
                                   "brier_market": round(float(Bc["bk"].mean()), 4) if len(Bc) else None,
                                   "diff_ci_event_cluster": [float(np.percentile(dc, 2.5)), float(np.percentile(dc, 97.5))] if dc else None,
                                   "markets_model_better": int((pmc["bm"] < pmc["bk"]).sum()) if len(pmc) else 0,
                                   "markets_market_better": int((pmc["bm"] > pmc["bk"]).sum()) if len(pmc) else 0}},
           "early_error": err, "coverage90": cover}
    (OUT / "stats.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: res[k] for k in ("first_count", "gate_1pct", "last_frame", "n_frames")}))
    print(json.dumps(res["brier"]["all"]), json.dumps(res["brier"]["contested"]))
    print(json.dumps(res["coverage90"]), json.dumps(res["early_error"]))
    for k, v in settle.items():
        if v["contested"]:
            print(f"{k[:70]:70s} {v['outcome']:3s} model {v['model'] and v['model'][11:16]} mkt {v['market'] and v['market'][11:16]} "
                  f"lead {v['lead_min']}  r90 m {v['model_reach90'] and v['model_reach90'][11:16]} mk {v['mkt_reach90'] and v['mkt_reach90'][11:16]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
