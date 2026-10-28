"""Score the frozen Israel count model (configs/il2026_v1.toml) on the recorded 2026 count night.

Pre-specified run (settings frozen on 2021/2022 before any 2026 model run; the outcome was partly known).
For every CEC release (locality-level content from the navonyaniv mirror, usable from the first time our
own recorder saw its national total) the model projects final votes, simulates seats and prices the
recorded Polymarket markets. Against the recorded order books (mid if spread <= 10c, else last trade):
  - settle: first time after which P(eventual outcome) >= 0.9 holds to the end of the recording, for the
    model, the market and the raw count (seats allocated from the votes counted so far);
  - calibration of projected list shares (90% interval coverage, mean z) against the outcome count;
  - early error of the projected vs raw share at 1/5/10/25% of regular boxes counted;
  - catch-up slope: regression of the price's 30-minute move on the model-market gap (same filters and
    30-minute cluster bootstrap as the Brazil analysis, analysis/underpricing_figures.py).
Outcome: the official CEC final when given (--official <expb.csv>); otherwise the latest count available
(--latest <navonyaniv results.json>, default the newest file), labelled provisional.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_score.py configs/il2026_v1.toml
Writes results/israel/{releases.csv, ticks.parquet, settle.csv, shares.csv, stats.json}. Nothing here is
publishable: the 2026 count comes from third-party mirrors (docs/israel.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import israel as il
from brunder import live
from brunder.paths import RAW, ROOT

OUT = ROOT / "results" / "israel"
sys.path.insert(0, str(ROOT / "analysis"))


def _t(name: str) -> pd.Timestamp:
    return pd.Timestamp(pd.to_datetime(name.split(".json")[0].rstrip("Z"), format="%Y%m%dT%H%M%S.%f"), tz="UTC")


def first_seen(rec: Path) -> dict[int, pd.Timestamp]:
    """National valid-vote total of each CEC release -> first time our recorder saw it."""
    seen: dict[int, pd.Timestamp] = {}
    for f in sorted((rec / "votes26.com_api_v1_elections_26_live").glob("*.json")):
        t = _t(f.name)
        for c in json.loads(f.read_text()).get("checkpoints", []):
            v = int(c.get("validObs") or 0)
            if v and (v not in seen or t < seen[v]):
                seen[v] = t
    for f in sorted((rec / "earthelects.com_israel_results.json").glob("*.json")):
        t = _t(f.name)
        regs = json.loads(f.read_text()).get("regions", {})
        v = int(sum(sum(r["v"].values()) for r in regs.values()))
        if v and (v not in seen or t < seen[v]):
            seen[v] = t
    return seen


def releases(cfg: dict) -> list[dict]:
    content = RAW / cfg["feed"]["content"]
    seen = first_seen(RAW / cfg["feed"]["recording"])
    end = pd.Timestamp(cfg["feed"]["recording_end_utc"])
    out, done, totals = [], set(), []
    for r in json.loads((content / "index.json").read_text()):
        obj = json.loads((content / r["file"]).read_text())
        if obj.get("stage") != "counting" or not obj.get("towns"):
            continue
        count, total = il.read_navon(obj)
        totals.append(total)
        v = int(count["valid"].sum())
        if v in done or v not in seen or seen[v] > end:
            continue
        done.add(v)
        out.append({"t": seen[v], "committed": r["committed"], "file": r["file"], "count": count})
    # ballot boxes per locality: the polling-station list is published before election day, but the first
    # mirror files list only the localities already reporting, so take the largest count seen per locality
    stations = pd.concat(totals, axis=1).max(axis=1).astype(np.int64)
    for r in out:
        r["boxes_total"] = stations
    return sorted(out, key=lambda x: x["t"])


def market_prices(cfg: dict) -> pd.DataFrame:
    """YES price per recorded book: mid if spread <= 10c, else last trade (brunder.live.top), bounded by the
    best bid and ask that exist (a one-sided book showed a stale 0.999 last trade under a 0.001 ask)."""
    cache = OUT / "pm_prices.parquet"
    rec = RAW / cfg["feed"]["recording"] / "polymarket_books"
    files = sorted(rec.glob("*.json"))
    if cache.exists() and pd.read_parquet(cache, columns=["t"])["t"].nunique() == len(files):
        d = pd.read_parquet(cache)
    else:
        want = {f"{m['event']}/{m['id']}": m["id"] for m in cfg["markets"]}
        rows = []
        for f in files:
            b = json.loads(f.read_text())
            t = _t(f.name)
            for k, mid in want.items():
                if k in b:
                    tp = live.top(b[k])
                    rows.append({"t": t, "market": mid, "price": tp["price"], "bid": tp["best_bid"], "ask": tp["best_ask"],
                                 "spread": tp["spread"], "last": tp["last"]})
        d = pd.DataFrame(rows)
        d.to_parquet(cache)
    d["price"] = d["price"].clip(lower=d["bid"].fillna(0.0), upper=d["ask"].fillna(1.0))
    return d


def outcome_votes(args, cfg) -> tuple[pd.Series, str]:
    if args.official:
        nat = il.national(il.by_locality(il.read_expb(args.official)))
        return nat, f"official CEC final ({Path(args.official).name})"
    content = RAW / cfg["feed"]["content"]
    f = Path(args.latest) if args.latest else content / json.loads((content / "index.json").read_text())[-1]["file"]
    obj = json.loads(f.read_text())
    count, _ = il.read_navon(obj)
    nat = count[il.lists_of(count)].sum()
    return nat, (f"PROVISIONAL: latest count available ({obj.get('updated')}, {obj['progress']['country'][0]} of "
                 f"{obj['progress']['country'][1]} regular boxes, double envelopes not yet counted)")


def settle(ts: pd.Series, ok: pd.Series):
    """First time after which `ok` holds to the end (export_brazil.settle)."""
    bad = ts[~ok.to_numpy()]
    if bad.empty:
        return ts.iloc[0]
    after = ts[ts > bad.iloc[-1]]
    return after.iloc[0] if len(after) else pd.NaT


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--official", help="official CEC final expb.csv (replaces the provisional outcome)")
    ap.add_argument("--latest", help="navonyaniv results.json to use as the provisional outcome")
    args = ap.parse_args()
    cfg_path = Path(args.config)
    cfg = tomllib.loads(cfg_path.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    st = il.Settings(**{f.name: type(getattr(il.Settings(), f.name))(cfg["model"][f.name])
                        for f in fields(il.Settings) if f.name in cfg["model"]})
    prior_f = RAW / cfg["prior"]["file"]
    assert hashlib.sha256(prior_f.read_bytes()).hexdigest() == cfg["prior"]["sha256"], "prior file changed"
    prior = il.by_locality(il.read_expb(prior_f))
    blocs = cfg["blocs"]
    mk = [dict(m, a=blocs.get(m.get("a"), m.get("a")), b=blocs.get(m.get("b"), m.get("b"))) for m in cfg["markets"]]
    mo = [dict(m, a=blocs[m["a"]]) for m in cfg.get("model_only", [])]
    rng = np.random.default_rng(cfg["seed"])

    # ---- outcome
    out_votes, out_label = outcome_votes(args, cfg)
    out_seats = il.allocate(out_votes.to_dict(), [tuple(p) for p in cfg["agreements"]["pairs"]])
    lists_o = list(out_votes.index)
    ov = out_votes.to_numpy(float)[None, :]
    os_ = np.array([[out_seats[k] for k in lists_o]])
    truth = {k: float(v[0]) for k, v in il.outcomes(ov, os_, lists_o, mk + mo).items()}

    # ---- model at each release
    rel = releases(cfg)
    rows, share_rows = [], []
    for r in rel:
        units = il.units_from(prior, r["boxes_total"], cfg["prior"]["min_share"])
        p = il.project(units, r["count"], st)
        L = p.lists
        pairs = [(L.index(a), L.index(b)) for a, b in cfg["agreements"]["pairs"] if a in L and b in L]
        V = il.simulate(p, st, rng)
        S = il.allocate_many(V, pairs)
        P = {k: float(v.mean()) for k, v in il.outcomes(V, S, L, mk + mo).items()}
        raw_v = r["count"][il.lists_of(r["count"])].sum()
        raw_s = il.allocate(raw_v.to_dict(), [tuple(x) for x in cfg["agreements"]["pairs"]])
        rl = list(raw_v.index)
        raw = il.outcomes(raw_v.to_numpy(float)[None, :], np.array([[raw_s[k] for k in rl]]), rl, mk + mo)
        share, sd = p.shares()
        raw_share = raw_v / raw_v.sum()
        row = {"t": r["t"], "committed": r["committed"], "counted": p.frac_counted, "valid_counted": float(p.counted.sum()),
               "growth": p.extra["growth"], "likud_seats_mean": float(S[:, L.index("מחל")].mean())}
        row.update({f"P:{k}": v for k, v in P.items()})
        row.update({f"raw:{k}": float(v[0]) for k, v in raw.items()})
        rows.append(row)
        for x, s, d in zip(L, share, sd):
            if x == il.OTHER:
                continue
            fin = float(out_votes.get(x, 0.0) / out_votes.sum())
            share_rows.append({"t": r["t"], "counted": p.frac_counted, "list": x, "share": s, "sd": d,
                               "raw": float(raw_share.get(x, 0.0)), "outcome": fin, "z": (fin - s) / d if d > 0 else np.nan,
                               "seats_mean": float(S[:, L.index(x)].mean()),
                               "seats_lo": float(np.percentile(S[:, L.index(x)], 5)),
                               "seats_hi": float(np.percentile(S[:, L.index(x)], 95))})
    R = pd.DataFrame(rows)
    SH = pd.DataFrame(share_rows)
    R.to_csv(OUT / "releases.csv", index=False)
    SH.to_csv(OUT / "shares.csv", index=False)

    # ---- minute ticks: model (last release available) vs market price
    pm = market_prices(cfg)
    t0 = pd.Timestamp(cfg["poll_close_utc"])
    t1 = pd.Timestamp(cfg["evaluation"]["window_end_utc"])
    grid = pd.date_range(t0.ceil("1min"), t1, freq="1min")
    rel_t = R.set_index("t").sort_index()
    ticks = []
    for m in cfg["markets"]:
        g = pm[pm["market"] == m["id"]].set_index("t").sort_index()
        price = g["price"].reindex(g.index.union(grid)).ffill().reindex(grid)
        Pm = rel_t[f"P:{m['id']}"].reindex(rel_t.index.union(grid)).ffill().reindex(grid)
        cnt = rel_t["counted"].reindex(rel_t.index.union(grid)).ffill().reindex(grid).fillna(0.0)
        ticks.append(pd.DataFrame({"t": grid, "scope": m["id"], "price": price.to_numpy(), "P": Pm.to_numpy(),
                                   "scope_counted": cnt.to_numpy(), "outcome": truth[m["id"]]}))
    T = pd.concat(ticks, ignore_index=True)
    T.to_parquet(OUT / "ticks.parquet")

    # ---- settle (P of the eventual outcome >= level to the end). Primary: as frozen (model from the first
    # release). Sensitivity, added after the first run: model only from >= 1% of boxes counted, the lower
    # edge of the range the settings were calibrated on (the first 2026 release had 15 boxes, 0.1%).
    lv = cfg["evaluation"]["settle_level"]
    t_first = R["t"].min()
    srows = []
    for m in cfg["markets"]:
        y = truth[m["id"]]
        g = T[T["scope"] == m["id"]]
        pe = lambda s: s if y == 1.0 else 1 - s                                   # noqa: E731
        mod = g.dropna(subset=["P"])
        ms = settle(mod["t"], pe(mod["P"]) >= lv) if len(mod) else pd.NaT
        mod1 = mod[mod["scope_counted"] >= 0.01]
        ms1 = settle(mod1["t"], pe(mod1["P"]) >= lv) if len(mod1) else pd.NaT
        mk_ = g.dropna(subset=["price"])
        ps = settle(mk_["t"], pe(mk_["price"]) >= lv) if len(mk_) else pd.NaT
        rr = R[["t", f"raw:{m['id']}"]]
        rs = settle(rr["t"], rr[f"raw:{m['id']}"] == y) if len(rr) else pd.NaT
        p_first = mk_.loc[mk_["t"] <= t_first, "price"]
        p_first = float(p_first.iloc[-1]) if len(p_first) else np.nan
        srows.append({"market": m["id"], "outcome": y, "model": ms, "polymarket": ps, "raw": rs, "model_gate1pct": ms1,
                      "lead_min": (ps - ms).total_seconds() / 60 if pd.notna(ps) and pd.notna(ms) else np.nan,
                      "lead_min_gate1pct": (ps - ms1).total_seconds() / 60 if pd.notna(ps) and pd.notna(ms1) else np.nan,
                      "price_at_close": float(mk_["price"].iloc[0]) if len(mk_) else np.nan,
                      "price_at_first_release": p_first,
                      "contested": bool(0.1 < p_first < 0.9) if np.isfinite(p_first) else False,
                      "price_end": float(mk_["price"].iloc[-1]) if len(mk_) else np.nan,
                      "P_end": float(mod["P"].iloc[-1]) if len(mod) else np.nan})
    SE = pd.DataFrame(srows)
    SE.to_csv(OUT / "settle.csv", index=False)
    con = SE[SE["contested"]]
    lead = {"contested_markets": int(len(con)),
            "median_lead_min": None if con["lead_min"].isna().all() else float(con["lead_min"].median()),
            "median_lead_min_gate1pct": None if con["lead_min_gate1pct"].isna().all() else float(con["lead_min_gate1pct"].median()),
            "model_first": int((con["lead_min"] > 0).sum()), "market_first": int((con["lead_min"] < 0).sum()),
            "model_first_gate1pct": int((con["lead_min_gate1pct"] > 0).sum()),
            "market_first_gate1pct": int((con["lead_min_gate1pct"] < 0).sum())}

    # ---- Brier score of model vs market at each release (all 12 markets, outcome as above)
    bt = []
    for r in R.itertuples():
        at = T[T["t"] == pd.Timestamp(r.t).ceil("1min")]
        at = at.dropna(subset=["price", "P"])
        if len(at):
            bt.append({"t": r.t, "counted": r.counted, "brier_model": float(((at["P"] - at["outcome"]) ** 2).mean()),
                       "brier_market": float(((at["price"] - at["outcome"]) ** 2).mean()), "n": len(at)})
    BR = pd.DataFrame(bt)
    BR.to_csv(OUT / "brier_by_release.csv", index=False)

    # ---- calibration and early error (lists with >= 1% of the outcome)
    big = SH[SH["outcome"] >= 0.01]
    w = big[big["counted"].between(0.01, 0.95, inclusive="right")]
    calib = {"coverage90_1_95": round(float((w["z"].abs() < 1.645).mean()), 3) if len(w) else None,
             "mean_z": round(float(w["z"].mean()), 2) if len(w) else None, "n": int(len(w)),
             "releases": int(w["t"].nunique())}
    early = {}
    for q in (0.01, 0.05, 0.10, 0.25):
        r0 = big[big["counted"] >= q]
        if r0.empty:
            early[f"{int(q * 100)}%"] = None
            continue
        r0 = r0[r0["t"] == r0["t"].min()]
        early[f"{int(q * 100)}%"] = {"model_mae_pt": round(100 * float((r0["share"] - r0["outcome"]).abs().mean()), 2),
                                     "raw_mae_pt": round(100 * float((r0["raw"] - r0["outcome"]).abs().mean()), 2),
                                     "likud_model_pt": round(100 * float((r0.loc[r0["list"] == "מחל", "share"] - r0.loc[r0["list"] == "מחל", "outcome"]).iloc[0]), 2),
                                     "likud_raw_pt": round(100 * float((r0.loc[r0["list"] == "מחל", "raw"] - r0.loc[r0["list"] == "מחל", "outcome"]).iloc[0]), 2),
                                     "t": r0["t"].iloc[0].isoformat()}

    # ---- catch-up slope (same sample rules as analysis/underpricing_figures.pairs)
    import underpricing_figures as U
    h = cfg["evaluation"]["catchup_horizon_min"]
    parts = []
    for scope, g in T.groupby("scope"):
        g = g.set_index("t").sort_index()
        fut = g["price"].reindex(g.index + pd.Timedelta(minutes=h)).to_numpy()
        parts.append(pd.DataFrame({"scope": scope, "t": g.index, "gap": g["P"] - g["price"], "move": fut - g["price"].to_numpy(),
                                   "p": g["price"], "P": g["P"], "c": g["scope_counted"]}))
    d = pd.concat(parts)
    d = d[(d["c"] >= 0.01) & d["p"].between(0.02, 0.98, inclusive="neither") & d["P"].between(0.02, 0.98, inclusive="neither")]
    d = d.dropna(subset=["move", "gap"])
    d["cluster"] = d["scope"] + "_" + d["t"].dt.floor("30min").astype(str)
    if len(d) > 3:
        s, lo, hi, _, k = U.slope_ci(d, b=300)
        catch = {"slope30": round(s, 3), "lo": round(float(lo), 3), "hi": round(float(hi), 3),
                 "markets": int(d["scope"].nunique()), "n": int(len(d)), "clusters": int(k)}
    else:
        catch = None

    def iso(x):
        return x.isoformat() if pd.notna(x) else None

    stats = {
        "id": cfg["id"], "evidence": "pre-specified", "outcome": out_label,
        "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        "outcome_seats": {k: v for k, v in out_seats.items() if v},
        "settle": {r.market: {"model": iso(r.model), "polymarket": iso(r.polymarket), "raw": iso(r.raw), "kalshi": None,
                              "model_gate1pct": iso(r.model_gate1pct), "contested": r.contested,
                              "lead_min": None if pd.isna(r.lead_min) else round(r.lead_min, 1),
                              "lead_min_gate1pct": None if pd.isna(r.lead_min_gate1pct) else round(r.lead_min_gate1pct, 1)}
                   for r in SE.itertuples()},
        "settle_summary": lead,
        "brier_by_release": BR.assign(t=BR["t"].map(iso)).round(4).to_dict("records") if len(BR) else [],
        "calibration": {"list_shares": calib},
        "early_error": early,
        "catchup": {"polymarket": catch, "kalshi": None},
        "capacity": {"polymarket": None, "kalshi": None},
        "arbitrage": None,
        "model_only": {m["id"]: {"P_end": float(R[f"P:{m['id']}"].iloc[-1]), "outcome": truth[m["id"]]} for m in mo},
        "reasons": {"capacity": "no trading rule is specified for Israel; only settle/catch-up are scored",
                    "arbitrage": "Kalshi workstream (configs/pairs/il2026.toml)",
                    "kalshi": "not part of this run (Kalshi workstream; Kalshi prices may not be redistributed)",
                    "calibration": "against the outcome named in 'outcome'; provisional outcomes lack the double envelopes, which the projection includes"},
        "releases": len(R), "first_release": iso(R["t"].min()), "last_release": iso(R["t"].max()),
    }
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False, default=float))
    pd.set_option("display.width", 220)
    print(out_label)
    print({k: v for k, v in out_seats.items() if v})
    print(R[["t", "counted", "likud_seats_mean"] + [c for c in R.columns if c.startswith("P:")][:3]].to_string(index=False))
    print(SE.to_string(index=False))
    print(json.dumps({k: stats[k] for k in ("settle_summary", "calibration", "early_error", "catchup", "model_only")},
                     indent=1, default=float))
    print(BR.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
