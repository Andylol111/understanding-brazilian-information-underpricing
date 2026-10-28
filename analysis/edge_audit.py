"""Edge audit: one method for every scored election. (1) Does the count model's projection hold? (2) Does real,
executable profit exist after costs, at sizes that could have been filled?

Works only from cached outputs; never reruns a registered replay (night_replay.py / history_review.py):
  Brazil 2026  results/night/<cid>/{ticks.parquet, evaluation.csv, final.json, tally.parquet, capacity_trades/}
               results/export_brazil/*.pkl (per-state projected share / sd / raw share, written by export_brazil.py)
               ~/brunder-stage/<stage>/raw/polymarket/<event>/markets.parquet (YES token of each market)
  Brazil 2022  results/history/<cid>/{ticks.parquet, summary.json} (model only: no prices survive)
  Israel 2026  results/israel/{ticks.parquet, releases.csv, shares.csv, settle.csv} (analysis/il_score.py) and the
               recorded Polymarket order books $BRUNDER_DATA/raw/snap/israel_2026/polymarket_books/*.json (30 s)

Accuracy (per election and race):
  - projected two-way share minus final, and raw count minus final (points), at the first minute with >= 1, 5,
    10, 25, 50% counted;
  - 90% interval coverage, z = (final - share) / sd, over 1-95% counted and split at 90%;
  - P(win): Brier and log score of the model's P and of the market price on the same minutes, from the 1% trade
    gate until both have settled (the later of model / market first staying >= 0.9 on the winner; whole night if
    either never settles); difference model - market with a cluster bootstrap that resamples races; reliability
    in ten P bins, per election and pooled.
Profit (the frozen rule, night.evaluate with favoured_only: first minute per market where the side the model
favours has edge >= min_edge after costs with >= min_counted of the market counted; one entry per market; hold
to resolution):
  - Brazil: no recorded books, so fills are real taker trades on our side at or better than the limit, in
    [signal + latency, signal + horizon) (analysis/night_capacity.py semantics), limit = the signal minute's
    price, and 2c / 5c worse; return per $ at the signal's all-in cost (+ slip). Never the minute price history.
  - Israel: walk the recorded book at the first snapshot >= signal + latency (0 / 30 / 60 s) as a limit order
    (only levels whose all-in unit cost leaves >= min_edge under the model), incl. the market's taker fee
    (feeSchedule; cached in results/edge_audit/il_fees.json from the public Gamma API). Signal = first book
    snapshot after a release (>= 1% counted) where the $100 book walk leaves edge >= min_edge (the live engine's
    cost definition). Outcome: il_score's provisional count. Not pre-registered: no trading rule was frozen for
    Israel; the Brazil rule is applied unchanged.
  Per size ($25 ... $5,000 per signal): signals fillable in full, $ deployed, P&L, return per $, a percentile
  bootstrap over markets (each signal = one unit), an exact p-value under "the price paid was the fair win
  probability", and the share of the total from the single largest trade.

    uv run python analysis/edge_audit.py [--no-site]      # from the repo root; no env vars needed
(Israel books: $BRUNDER_DATA/raw/snap/... if present, else $BRUNDER_IL_DATA, default ~/brunder-data-local.)
Writes results/edge_audit/*.csv + summary.json and adds "accuracy" / "profit" blocks to site/data/<eid>/stats.json
(schema: docs/edge_audit.md). Israel blocks hold aggregates only (the 2026 count is private-research data).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import pickle
import sys
import tomllib
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from scipy import stats as sps

from brunder import live, night
from brunder.paths import RAW, ROOT

sys.path.insert(0, str(ROOT / "analysis"))
from night_capacity import same_side_fills  # noqa: E402

RES = ROOT / "results"
OUT = RES / "edge_audit"
SITE = ROOT / "site" / "data"
STAGES = Path(os.environ.get("BRUNDER_STAGE_ROOT", Path.home() / "brunder-stage"))
IL_CFG = ROOT / "configs" / "il2026_v1.toml"
_IL_SNAP = Path("snap") / "israel_2026" / "polymarket_books"
# The recorded Israel books live in the Israel data root, not the Brazil one: $BRUNDER_DATA if it holds them,
# else $BRUNDER_IL_DATA (default ~/brunder-data-local), so the script runs the same under either env.
IL_BOOKS = next((p for p in (RAW / _IL_SNAP,
                             Path(os.environ.get("BRUNDER_IL_DATA", Path.home() / "brunder-data-local")) / "raw" / _IL_SNAP)
                 if any(p.glob("*.json"))), RAW / _IL_SNAP)

SIZES = (25, 100, 500, 1000, 5000)
QS = (0.01, 0.05, 0.10, 0.25, 0.50)
BR_LATENCY_S = (0, 60)
BR_HORIZON_MIN = (5, 30)
BR_SLIPS = (0.0, 0.02, 0.05)
IL_LATENCY_S = (0, 30, 60)
SIGNAL_STAKE = 100.0            # live engine: edges from the book walk of the first stake (night.run_live)
HEAD_BR = (60, 30, 0.0)         # headline Brazil execution: 60 s latency, 30-min window, at the signal price
HEAD_IL = 60                    # headline Israel execution: book 60 s after the signal
BINS = np.linspace(0, 1, 11)
EPS = 0.001                     # log score: probabilities clipped to [EPS, 1 - EPS] (market tick 0.001)
B_ACC, B_PNL = 2000, 10000
RNG = np.random.default_rng(20261028)

# eid -> (evidence label, [(cid, config, stage, race id of the config's national market)])
NIGHTS = {
    "br2026r1": ("in-sample", [("brazil_2026_r1_dev_mun", "configs/brazil_2026_r1_dev_mun.toml", "r1_dev", "br")]),
    "br2026r2": ("pre-specified", [("night_r2_v1", "configs/night_r2_v1.toml", "night_r2_v1", "br")]),
    "br2026r2gov": ("pre-specified", [(f"night_gov_r2_{u}_v1", f"configs/governor_r2/night_gov_r2_{u}_v1.toml",
                                       f"night_gov_r2_{u}_v1", f"gov-{u}") for u in ("ac", "am", "df", "es", "rn", "to")]),
}
HISTORY = {"br2022r1": "hist_2022_r1_v1", "br2022r2": "hist_2022_r2_v1"}
GROUPS = {"in-sample": ["br2026r1"], "pre-specified": ["br2026r2", "br2026r2gov"], "not-preregistered": ["il2026"]}


def _r(x, n=4):
    if x is None:
        return None
    x = float(x)
    return round(x, n) if math.isfinite(x) else None


def _iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def settle(ts: pd.Series, ok: pd.Series):
    """First time after which `ok` holds to the end (export_brazil.settle / il_score.settle)."""
    bad = ts[~ok.to_numpy()]
    if bad.empty:
        return ts.iloc[0] if len(ts) else pd.NaT
    after = ts[ts > bad.iloc[-1]]
    return after.iloc[0] if len(after) else pd.NaT


# ============================================================== race data (cached outputs only)

def _ticks(cid: str) -> pd.DataFrame:
    t = pd.read_parquet(RES / "night" / cid / "ticks.parquet")
    t["t"] = pd.to_datetime(t["t"], utc=True)
    return t.drop_duplicates(["t", "scope"]).sort_values("t").reset_index(drop=True)


def _raw_tally(cid: str) -> pd.Series:
    """Last raw two-way share at or before each minute (export_brazil.raw_tally)."""
    tally = pd.read_parquet(RES / "night" / cid / "tally.parquet")
    tally["t"] = pd.to_datetime(tally["t"], utc=True)
    return tally.set_index("t")["raw_share"].resample("1min", label="right", closed="right").last().ffill()


def _state_series(eid: str, cid: str) -> tuple[pd.DataFrame, dict]:
    """Per-state projected share / sd / raw share by minute, from export_brazil's cache (newest with states)."""
    cands = sorted((RES / "export_brazil").glob(f"{eid}_{cid}_*.pkl"))
    cands = [p for p in cands if not p.stem.endswith("_0")]
    if not cands:
        return pd.DataFrame(columns=["t", "scope", "share", "sd", "raw"]), {}
    res = pickle.loads(cands[-1].read_bytes())
    s = res["series"].copy()
    s["t"] = pd.to_datetime(s["t"], utc=True)
    return s, res["final_uf"]


def brazil_races(eid: str) -> list[dict]:
    """One dict per race: minute rows (t, counted, share, sd, raw, P, price), final share, outcome of the YES side,
    and the model / market settle times recomputed on the same cached minute ticks."""
    races = []
    for cid, cfg_path, _stage, nat_id in NIGHTS[eid][1]:
        tk = _ticks(cid)
        ev = pd.read_csv(RES / "night" / cid / "evaluation.csv")
        outcome = dict(zip(ev["scope"], ev["outcome"].astype(int)))
        sett = night.evaluate(tk, outcome, 0.05, 0.01, True).set_index("scope")
        series, final_uf = _state_series(eid, cid) if nat_id == "br" else (None, {})
        for scope, g in tk.groupby("scope", sort=False):
            g = g.sort_values("t")
            if scope == "br":
                rows = g[["t", "counted", "share", "share_sd", "P", "price"]].rename(columns={"share_sd": "sd"})
                rows = rows.merge(_raw_tally(cid).rename("raw"), left_on="t", right_index=True, how="left")
                rows["raw"] = rows["raw"].ffill()
                final = json.loads((RES / "night" / cid / "final.json").read_text())["final_share"]
                rid = nat_id
            else:
                rows = g[["t", "scope_counted", "P", "price"]].rename(columns={"scope_counted": "counted"})
                s = series[series["scope"] == scope][["t", "share", "sd", "raw"]]
                rows = rows.merge(s, on="t", how="left")
                inside = rows["t"] <= s["t"].max() if len(s) else pd.Series(False, index=rows.index)
                rows.loc[inside, ["share", "sd", "raw"]] = rows.loc[inside, ["share", "sd", "raw"]].ffill()
                final = final_uf.get(scope, np.nan)
                rid = scope
            x = sett.loc[scope]
            races.append({"eid": eid, "race": rid, "cid": cid, "scope": scope, "rows": rows.reset_index(drop=True),
                          "final": float(final), "outcome": outcome[scope],
                          "model_settle": x["model_0.9"], "market_settle": x["market_0.9"]})
    return races


def history_races(eid: str) -> list[dict]:
    cid = HISTORY[eid]
    h = pd.read_parquet(RES / "history" / cid / "ticks.parquet")
    h["t"] = pd.to_datetime(h["t"], utc=True)
    s = json.loads((RES / "history" / cid / "summary.json").read_text())
    final = float(s["final_share_a"])
    y = int(final > 0.5)
    rows = h.rename(columns={"tally": "raw"})[["t", "counted", "share", "sd", "raw", "P"]].assign(price=np.nan)
    win = rows["P"] if y else 1 - rows["P"]
    return [{"eid": eid, "race": "br", "cid": cid, "scope": "br", "rows": rows, "final": final, "outcome": y,
             "model_settle": settle(rows["t"], win >= 0.9), "market_settle": pd.NaT}]


# ============================================================== accuracy

def share_error(r: dict) -> list[dict]:
    out = []
    rows = r["rows"].dropna(subset=["share"])
    for q in QS:
        x = rows[rows["counted"] >= q]
        if x.empty:
            out.append({"q": q, "model_pt": None, "raw_pt": None, "t": None, "counted": None})
            continue
        x = x.iloc[0]
        out.append({"q": q, "model_pt": _r(100 * (x["share"] - r["final"]), 3),
                    "raw_pt": _r(100 * (x["raw"] - r["final"]), 3) if pd.notna(x["raw"]) else None,
                    "t": _iso(x["t"]), "counted": _r(x["counted"])})
    return out


def coverage(r: dict) -> list[dict]:
    rows = r["rows"].dropna(subset=["share", "sd"])
    z = (r["final"] - rows["share"]) / rows["sd"]
    ok = np.isfinite(z)
    out = []
    for name, lo, hi in (("1-95%", 0.01, 0.95), ("1-90%", 0.01, 0.90), ("90-95%", 0.90, 0.95)):
        w = ok & rows["counted"].between(lo, hi, inclusive="right")
        out.append({"window": name, "n": int(w.sum()),
                    "coverage90": _r((z[w].abs() < 1.645).mean(), 3) if w.any() else None,
                    "mean_z": _r(z[w].mean(), 2) if w.any() else None})
    return out


def pwin_minutes(r: dict) -> pd.DataFrame:
    """Minutes from the 1% gate until both model and market have settled (whole night if either never does)."""
    rows = r["rows"]
    ends = [r["model_settle"], r["market_settle"]] if rows["price"].notna().any() else [r["model_settle"]]
    end = pd.NaT if any(pd.isna(e) for e in ends) else max(pd.Timestamp(e) for e in ends)
    w = rows[(rows["counted"] >= 0.01) & rows["P"].notna()]
    if pd.notna(end):
        w = w[w["t"] <= end]
    if rows["price"].notna().any():
        w = w[w["price"].notna()]
    return w.assign(race=f"{r['eid']}:{r['race']}", y=r["outcome"])


def _scores(d: pd.DataFrame, col: str) -> tuple[np.ndarray, np.ndarray]:
    p = d[col].to_numpy(float)
    y = d["y"].to_numpy(float)
    pc = np.clip(p, EPS, 1 - EPS)
    return (p - y) ** 2, -(y * np.log(pc) + (1 - y) * np.log(1 - pc))


def pwin_scores(d: pd.DataFrame, has_market: bool) -> dict:
    """Pooled-minute Brier / log score of model and market; race-cluster bootstrap of model - market."""
    if d.empty:
        return {"races": 0, "minutes": 0}
    bm, lm = _scores(d, "P")
    out = {"races": int(d["race"].nunique()), "minutes": int(len(d)),
           "brier_model": _r(bm.mean()), "log_model": _r(lm.mean())}
    if not has_market:
        return out
    bk, lk = _scores(d, "price")
    out.update({"brier_market": _r(bk.mean()), "log_market": _r(lk.mean()),
                "brier_diff": _r((bm - bk).mean(), 5), "log_diff": _r((lm - lk).mean(), 5)})
    g = pd.DataFrame({"race": d["race"].to_numpy(), "db": bm - bk, "dl": lm - lk}).groupby("race").agg(
        db=("db", "sum"), dl=("dl", "sum"), n=("db", "size"))
    k = len(g)
    if k >= 3:
        idx = RNG.integers(0, k, (B_ACC, k))
        n = g["n"].to_numpy()[idx].sum(1)
        bd = g["db"].to_numpy()[idx].sum(1) / n
        ld = g["dl"].to_numpy()[idx].sum(1) / n
        out["brier_diff_ci"] = [_r(v, 5) for v in np.percentile(bd, [2.5, 97.5])]
        out["log_diff_ci"] = [_r(v, 5) for v in np.percentile(ld, [2.5, 97.5])]
        out["races_model_better_brier"] = int((g["db"] < 0).sum())
        out["races_market_better_brier"] = int((g["db"] > 0).sum())
    else:
        out["brier_diff_ci"] = out["log_diff_ci"] = None
    return out


def reliability(d: pd.DataFrame, col: str) -> list[dict]:
    if d.empty or d[col].isna().all():
        return []
    b = np.clip(np.digitize(d[col].to_numpy(float), BINS[1:-1], right=True), 0, len(BINS) - 2)  # (lo, hi], first [0, .1]
    out = []
    for i, g in d.groupby(b):
        out.append({"bin": f"{BINS[i]:.1f}-{BINS[i + 1]:.1f}", "minutes": int(len(g)), "races": int(g["race"].nunique()),
                    "mean_p": _r(g[col].mean()), "freq": _r(g["y"].mean())})
    return out


# ============================================================== profit: shared

def _cp(k: int, n: int) -> list:
    if n == 0:
        return [None, None]
    lo = 0.0 if k == 0 else sps.beta.ppf(0.025, k, n - k + 1)
    hi = 1.0 if k == n else sps.beta.ppf(0.975, k + 1, n - k)
    return [_r(lo, 3), _r(hi, 3)]


def p_market_fair(dep: np.ndarray, price: np.ndarray, pnl_obs: float) -> float | None:
    """Exact P(total P&L >= observed) if each filled trade won with probability equal to the all-in price paid
    (expected P&L 0). Enumerates outcomes (<= 16 trades), else 200k Monte Carlo draws."""
    m = dep > 0
    dep, price = dep[m], price[m]
    n = len(dep)
    if n == 0:
        return None
    win, lose = dep * (1 / price - 1), -dep
    if n <= 16:
        p = 0.0
        for o in itertools.product((0, 1), repeat=n):
            o = np.array(o, bool)
            if np.where(o, win, lose).sum() >= pnl_obs - 1e-9:
                p += float(np.prod(np.where(o, price, 1 - price)))
        return _r(p, 4)
    o = RNG.random((200_000, n)) < price
    return _r(float((np.where(o, win, lose).sum(1) >= pnl_obs - 1e-9).mean()), 4)


def size_table(sig: pd.DataFrame, avail: str, ret: str, price: str) -> list[dict]:
    """sig: one row per signal with available $ (`avail`), return per $ (`ret`), all-in price (`price`), won."""
    out = []
    n = len(sig)
    for s in [*SIZES, None]:
        dep = (sig[avail].clip(upper=s) if s else sig[avail]).fillna(0.0).to_numpy(float)
        pnl_i = dep * sig[ret].to_numpy(float)
        pnl = float(pnl_i.sum())
        filled = dep > 0
        row = {"size": s if s else "max", "signals": n,
               "fillable": int((sig[avail].fillna(0) >= s - 0.01).sum()) if s else int(filled.sum()),
               "filled_any": int(filled.sum()), "deployed": _r(dep.sum(), 2), "pnl": _r(pnl, 2),
               "return_per_usd": _r(pnl / dep.sum(), 4) if dep.sum() > 0 else None,
               "hit_rate_filled": _r(sig.loc[filled, "won"].mean(), 3) if filled.any() else None}
        if filled.sum() >= 2:
            idx = RNG.integers(0, n, (B_PNL, n))
            bp, bd = pnl_i[idx].sum(1), dep[idx].sum(1)
            row["pnl_ci"] = [_r(v, 2) for v in np.percentile(bp, [2.5, 97.5])]
            rr = np.divide(bp, bd, out=np.full(B_PNL, np.nan), where=bd > 0)
            row["return_ci"] = [_r(v, 4) for v in np.nanpercentile(rr, [2.5, 97.5])]
        else:
            row["pnl_ci"] = row["return_ci"] = None
        row["p_market_fair"] = p_market_fair(dep, sig[price].to_numpy(float), pnl)
        if filled.any():
            j = int(np.argmax(np.abs(pnl_i)))
            row["largest_trade"] = str(sig.iloc[j]["market_key"])
            row["largest_trade_pnl"] = _r(pnl_i[j], 2)
            row["largest_trade_share"] = _r(pnl_i[j] / pnl, 3) if pnl > 0 else None
            row["pnl_without_largest"] = _r(pnl - pnl_i[j], 2)
        out.append(row)
    return out


# ============================================================== profit: Brazil (real trades)

def _yes_tokens() -> dict[str, str]:
    out = {}
    for f in STAGES.glob("*/raw/polymarket/*/markets.parquet"):
        d = pd.read_parquet(f, columns=["slug", "yes_token"])
        out.update(dict(zip(d["slug"], d["yes_token"])))
    return out


def brazil_signals(eid: str, yes_tok: dict[str, str]) -> pd.DataFrame:
    """Frozen-rule signals from the cached 1-minute ticks and their trade-based fills in every execution variant."""
    rows = []
    for cid, cfg_path, _stage, nat_id in NIGHTS[eid][1]:
        cfg = night.load_config(ROOT / cfg_path)
        T = cfg["trade"]
        tk = _ticks(cid)
        ev = pd.read_csv(RES / "night" / cid / "evaluation.csv")
        outcome = dict(zip(ev["scope"], ev["outcome"].astype(int)))
        sig = night.evaluate(tk, outcome, T["min_edge"], T["min_counted"], True).dropna(subset=["entry"])
        sig = sig.merge(tk, left_on=["scope", "entry"], right_on=["scope", "t"], how="left")
        mk = {m["scope"]: m for m in night._markets(cfg)}
        for r in sig.itertuples():
            m = mk[r.scope]
            side_yes = r.side == "YES"
            entry = pd.Timestamp(r.entry)
            f = (RES / "night" / cid / "capacity_trades" /
                 f"{m['market']}_{int(entry.timestamp())}_{int((entry + pd.Timedelta(minutes=120)).timestamp())}.parquet")
            trades = pd.read_parquet(f) if f.exists() else None
            cost = r.yes_cost if side_yes else r.no_cost
            p_fav = r.P if side_yes else 1 - r.P
            won = int((r.outcome == 1) == side_yes)
            base = {"eid": eid, "cid": cid, "race": nat_id if r.scope == "br" else r.scope,
                    "market_key": f"{eid}:{nat_id if r.scope == 'br' else r.scope}", "market": m["market"],
                    "side": r.side, "entry": entry, "price": r.price, "cost": cost, "P_fav": p_fav,
                    "edge": p_fav - cost, "won": won, "fee_rate": T.get("fee_rate", 0.0),
                    "half_spread": T.get("half_spread", 0.01), "trades_file": f.name if trades is not None else None,
                    "return_registered_2min": float(ev.loc[ev["scope"] == r.scope, "return_per_$"].iloc[0])}
            for L, N, s in itertools.product(BR_LATENCY_S, BR_HORIZON_MIN, BR_SLIPS):
                if trades is None:
                    avail = np.nan
                else:
                    lim = r.price + s if side_yes else r.price - s
                    fs = same_side_fills(trades, yes_tok[m["market"]], side_yes, lim)
                    w = (fs["time"] >= entry + pd.Timedelta(seconds=L)) & (fs["time"] < entry + pd.Timedelta(minutes=N))
                    avail = float(fs.loc[w, "notional"].sum())
                c = cost + s
                rows.append({**base, "latency_s": L, "horizon_min": N, "slip": s, "avail": avail, "all_in": c,
                             "ret": (1 / c - 1) if won else -1.0, "rule_ok": bool(p_fav - c >= T["min_edge"] - 1e-12)})
    return pd.DataFrame(rows)


# ============================================================== profit: Israel (recorded books)

def il_fees(cfg: dict) -> dict[str, float]:
    """Taker fee rate per market from its Gamma feeSchedule (0 when fees are disabled); cached."""
    f = OUT / "il_fees.json"
    if f.exists():
        return json.loads(f.read_text())
    out = {}
    with httpx.Client(timeout=30) as c:
        for e in sorted({m["event"] for m in cfg["markets"]}):
            for ev in c.get("https://gamma-api.polymarket.com/events", params={"slug": e}).json():
                for m in ev["markets"]:
                    fs = m.get("feeSchedule") or {}
                    out[m["slug"]] = float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0
    f.write_text(json.dumps(out, indent=1))
    return out


def _book_t(p: Path) -> pd.Timestamp:
    return pd.Timestamp(pd.to_datetime(p.name.split(".json")[0].rstrip("Z"), format="%Y%m%dT%H%M%S.%f"), tz="UTC")


def walk_limit(book: dict, side_yes: bool, stake: float | None, fee: float, max_unit: float) -> dict:
    """live.walk with a limit: only levels whose all-in unit cost <= max_unit; stake None = everything there."""
    if side_yes:
        levels = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks") or [])
    else:
        levels = sorted((1 - float(x["price"]), float(x["size"])) for x in book.get("bids") or [])
    spent = shares = 0.0
    for q, size in levels:
        unit = q + fee * q * (1 - q)
        if unit > max_unit + 1e-12:
            break
        take = size if stake is None else min(size, (stake - spent) / unit)
        if take <= 0:
            break
        shares += take
        spent += take * unit
        if stake is not None and spent >= stake - 1e-9:
            break
    return {"spent": spent, "shares": shares}


def israel_signals() -> tuple[pd.DataFrame, dict]:
    cfg = tomllib.loads(IL_CFG.read_text())
    T = tomllib.loads((ROOT / NIGHTS["br2026r2"][1][0][1]).read_text())["trade"]     # the frozen Brazil rule
    fees = il_fees(cfg)
    rel = pd.read_csv(RES / "israel" / "releases.csv")
    rel["t"] = pd.to_datetime(rel["t"], utc=True)
    out = pd.read_parquet(RES / "israel" / "ticks.parquet").groupby("scope")["outcome"].first().to_dict()
    files = sorted(IL_BOOKS.glob("*.json"))
    if not files:
        raise SystemExit(f"no recorded Israel order books under {IL_BOOKS}; set BRUNDER_IL_DATA to the Israel data root")
    times = pd.DatetimeIndex([_book_t(p) for p in files])
    gated = rel[rel["counted"] >= T["min_counted"]].sort_values("t")
    cache: dict[int, dict] = {}

    def book(i: int) -> dict:
        if i not in cache:
            cache[i] = json.loads(files[i].read_text())
        return cache[i]

    keys = {m["id"]: f"{m['event']}/{m['id']}" for m in cfg["markets"]}
    pending, rows = set(keys), []
    start = int(times.searchsorted(gated["t"].iloc[0]))
    for i in range(start, len(files)):
        if not pending:
            break
        r = gated[gated["t"] <= times[i]].iloc[-1]
        b = book(i)
        for mid in sorted(pending):
            if keys[mid] not in b:
                continue
            P = float(r[f"P:{mid}"])
            side_yes = P >= 0.5
            p_fav = P if side_yes else 1 - P
            w = live.walk(b[keys[mid]], side_yes, SIGNAL_STAKE, fees.get(mid, 0.0))
            if not (np.isfinite(w["avg_cost"]) and p_fav - w["avg_cost"] >= T["min_edge"]):
                continue
            pending.discard(mid)
            won = int((out[mid] == 1.0) == side_yes)
            base = {"eid": "il2026", "market_key": f"il2026:{mid}", "market": mid, "side": "YES" if side_yes else "NO",
                    "release_t": r["t"], "counted": float(r["counted"]), "signal_t": times[i], "P_fav": p_fav,
                    "signal_cost_100": w["avg_cost"], "won": won, "fee_rate": fees.get(mid, 0.0)}
            for L in IL_LATENCY_S:
                j = int(times.searchsorted(times[i] + pd.Timedelta(seconds=L)))
                if j >= len(files) or keys[mid] not in book(j):
                    continue
                bk = book(j)[keys[mid]]
                lim = p_fav - T["min_edge"]
                for s in [*SIZES, None]:
                    x = walk_limit(bk, side_yes, s, fees.get(mid, 0.0), lim)
                    pnl = x["shares"] * won - x["spent"]
                    rows.append({**base, "latency_s": L, "book_t": times[j], "size": s if s else "max",
                                 "spent": x["spent"], "shares": x["shares"],
                                 "avg_cost": x["spent"] / x["shares"] if x["shares"] else np.nan, "pnl": pnl})
    meta = {"markets": len(keys), "snapshots": len(files), "first_gated_release": _iso(gated["t"].iloc[0]),
            "min_edge": T["min_edge"], "min_counted": T["min_counted"], "fees": sorted(set(fees.get(m, 0.0) for m in keys))}
    return pd.DataFrame(rows), meta


def israel_tables(il: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Per latency: one row per signal with available $ = the limit walk of the whole book (size 'max')."""
    out = {}
    for L in IL_LATENCY_S:
        x = il[(il["latency_s"] == L) & (il["size"] == "max")].copy()
        x["avail"] = x["spent"]
        x["ret"] = np.where(x["won"] == 1, 1 / x["avg_cost"] - 1, -1.0)
        x["all_in"] = x["avg_cost"]
        out[L] = x.reset_index(drop=True)
    return out


def il_size_table(il: pd.DataFrame, L: int) -> list[dict]:
    """Like size_table, but each size is walked separately (deeper levels cost more)."""
    sig = il[(il["latency_s"] == L) & (il["size"] == "max")].reset_index(drop=True)
    n = len(sig)
    out = []
    for s in [*SIZES, "max"]:
        x = il[(il["latency_s"] == L) & (il["size"] == s)].set_index("market").reindex(sig["market"]).reset_index()
        dep, pnl_i = x["spent"].fillna(0).to_numpy(float), x["pnl"].fillna(0).to_numpy(float)
        pnl, filled = float(pnl_i.sum()), dep > 0
        row = {"size": s, "signals": n, "fillable": int((dep >= (s if s != "max" else 0) - 0.01).sum() if s != "max" else filled.sum()),
               "filled_any": int(filled.sum()), "deployed": _r(dep.sum(), 2), "pnl": _r(pnl, 2),
               "return_per_usd": _r(pnl / dep.sum(), 4) if dep.sum() > 0 else None,
               "hit_rate_filled": _r(x.loc[filled, "won"].mean(), 3) if filled.any() else None}
        if filled.sum() >= 2:
            idx = RNG.integers(0, n, (B_PNL, n))
            bp, bd = pnl_i[idx].sum(1), dep[idx].sum(1)
            row["pnl_ci"] = [_r(v, 2) for v in np.percentile(bp, [2.5, 97.5])]
            rr = np.divide(bp, bd, out=np.full(B_PNL, np.nan), where=bd > 0)
            row["return_ci"] = [_r(v, 4) for v in np.nanpercentile(rr, [2.5, 97.5])]
        else:
            row["pnl_ci"] = row["return_ci"] = None
        avg = np.divide(dep, x["shares"].fillna(0).to_numpy(float), out=np.ones(n), where=x["shares"].fillna(0) > 0)
        row["p_market_fair"] = p_market_fair(dep, avg, pnl)
        if filled.any():
            j = int(np.argmax(np.abs(pnl_i)))
            row["largest_trade_pnl"] = _r(pnl_i[j], 2)
            row["largest_trade_share"] = _r(pnl_i[j] / pnl, 3) if pnl > 0 else None
            row["pnl_without_largest"] = _r(pnl - pnl_i[j], 2)
        out.append(row)
    return out


# ============================================================== Israel accuracy (aggregates only)

def israel_accuracy() -> dict:
    sh = pd.read_csv(RES / "israel" / "shares.csv")
    sh["t"] = pd.to_datetime(sh["t"], utc=True)
    big = sh[sh["outcome"] >= 0.01]
    err = {}
    for q in QS:
        r0 = big[big["counted"] >= q]
        if r0.empty:
            err[f"{int(q * 100)}%"] = None
            continue
        r0 = r0[r0["t"] == r0["t"].min()]
        err[f"{int(q * 100)}%"] = {"model": _r(100 * (r0["share"] - r0["outcome"]).abs().mean(), 2),
                                   "raw": _r(100 * (r0["raw"] - r0["outcome"]).abs().mean(), 2),
                                   "counted": _r(r0["counted"].iloc[0], 3), "lists": int(len(r0))}
    cov = {}
    for name, lo, hi in (("1-95%", 0.01, 0.95), ("1-90%", 0.01, 0.90), ("90-95%", 0.90, 0.95)):
        w = big[big["counted"].between(lo, hi, inclusive="right")]
        cov[name] = {"coverage90": _r((w["z"].abs() < 1.645).mean(), 3) if len(w) else None,
                     "mean_z": _r(w["z"].mean(), 2) if len(w) else None, "n": int(len(w)),
                     "releases": int(w["t"].nunique())}
    tk = pd.read_parquet(RES / "israel" / "ticks.parquet")
    tk["t"] = pd.to_datetime(tk["t"], utc=True)
    se = pd.read_csv(RES / "israel" / "settle.csv").set_index("market")
    parts = []
    for mid, g in tk.groupby("scope"):
        ends = [pd.to_datetime(se.at[mid, "model"], utc=True), pd.to_datetime(se.at[mid, "polymarket"], utc=True)]
        end = pd.NaT if any(pd.isna(e) for e in ends) else max(ends)
        w = g[(g["scope_counted"] >= 0.01) & g["P"].notna() & g["price"].notna()]
        if pd.notna(end):
            w = w[w["t"] <= end]
        parts.append(w.assign(race=f"il2026:{mid}", y=g["outcome"].iloc[0]))
    d = pd.concat(parts, ignore_index=True)
    return {"d": d, "share_error_mae": err, "coverage_list_shares": cov}


# ============================================================== main

def _clean(x):
    """JSON-safe: non-finite floats (pandas NaN from missing cells) -> null, numpy scalars -> Python."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    return x


def _site_update(eid: str, blocks: dict) -> None:
    f = SITE / eid / "stats.json"
    s = json.loads(f.read_text())
    s.update(_clean(blocks))
    f.write_text(json.dumps(s, ensure_ascii=False, separators=(",", ":")))


def _write(name: str, df: pd.DataFrame) -> None:
    df.to_csv(OUT / name, index=False)


def main(site: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    summary: dict = {"accuracy": {}, "profit": {}, "reconcile": {}}

    # ---------------- accuracy
    races = {eid: brazil_races(eid) for eid in NIGHTS} | {eid: history_races(eid) for eid in HISTORY}
    se_rows, cov_rows, by_race, mins = [], [], [], {}
    for eid, rs in races.items():
        for r in rs:
            se_rows += [{"eid": eid, "race": r["race"], **x} for x in share_error(r)]
            cov_rows += [{"eid": eid, "race": r["race"], **x} for x in coverage(r)]
            w = pwin_minutes(r)
            mins.setdefault(eid, []).append(w)
            has_mk = eid not in HISTORY
            if len(w):
                bm, lm = _scores(w, "P")
                row = {"eid": eid, "race": r["race"], "minutes": len(w), "brier_model": bm.mean(), "log_model": lm.mean()}
                if has_mk:
                    bk, lk = _scores(w, "price")
                    row.update({"brier_market": bk.mean(), "log_market": lk.mean()})
                by_race.append(row)
    ila = israel_accuracy()
    mins["il2026"] = [ila["d"]]
    for mid, g in ila["d"].groupby("race"):
        bm, lm = _scores(g, "P")
        bk, lk = _scores(g, "price")
        by_race.append({"eid": "il2026", "race": mid.split(":", 1)[1], "minutes": len(g), "brier_model": bm.mean(),
                        "log_model": lm.mean(), "brier_market": bk.mean(), "log_market": lk.mean()})
    SE, COV = pd.DataFrame(se_rows), pd.DataFrame(cov_rows)
    _write("accuracy_share_error.csv", SE)
    _write("accuracy_coverage.csv", COV)
    _write("accuracy_pwin_by_race.csv", pd.DataFrame(by_race))

    pw, rel_rows = {}, []
    pools = {eid: [eid] for eid in mins} | {"brazil2026_all": list(NIGHTS), "brazil2026_prespecified": ["br2026r2", "br2026r2gov"],
                                             "all_with_market": [*NIGHTS, "il2026"]}
    for name, eids in pools.items():
        d = pd.concat([x for e in eids for x in mins[e]], ignore_index=True)
        has_mk = not set(eids) & set(HISTORY)
        pw[name] = pwin_scores(d, has_mk)
        pw[name]["reliability_model"] = reliability(d, "P")
        pw[name]["reliability_market"] = reliability(d, "price") if has_mk else []
        for src in ("model", "market"):
            rel_rows += [{"pool": name, "source": src, **x} for x in pw[name][f"reliability_{src}"]]
    _write("accuracy_reliability.csv", pd.DataFrame(rel_rows))
    _write("accuracy_pwin.csv", pd.DataFrame([{"pool": k, **{a: (json.dumps(b) if isinstance(b, list) else b)
                                                              for a, b in v.items() if not a.startswith("reliability")}}
                                              for k, v in pw.items()]))
    summary["accuracy"]["pwin"] = pw

    def mae(eid: str) -> dict:
        x = SE[SE["eid"] == eid]
        return {f"{int(q * 100)}%": {"model": _r(x.loc[x.q == q, "model_pt"].abs().mean(), 2),
                                     "raw": _r(x.loc[x.q == q, "raw_pt"].abs().mean(), 2),
                                     "races": int(x.loc[x.q == q, "model_pt"].notna().sum())} for q in QS}

    def pooled_cov(eid: str) -> dict:
        x = COV[COV["eid"] == eid]
        out = {}
        for wname, g in x.groupby("window"):
            g = g[g["n"] > 0]
            out[wname] = {"races": int(len(g)), "median": _r(g["coverage90"].median(), 3),
                          "min": _r(g["coverage90"].min(), 3),
                          "races_below_80pct": int((g["coverage90"] < 0.8).sum()),
                          "minutes_weighted": _r((g["coverage90"] * g["n"]).sum() / g["n"].sum(), 3) if g["n"].sum() else None}
        return out

    acc_blocks = {}
    for eid in [*NIGHTS, *HISTORY]:
        ev = NIGHTS[eid][0] if eid in NIGHTS else "model-only"
        se = {r: {f"{int(q * 100)}%": {"model": a, "raw": b} for q, a, b in zip(g.q, g.model_pt, g.raw_pt)}
              for r, g in SE[SE.eid == eid].groupby("race", sort=False)}
        cv = {r: {w: {"coverage90": c, "mean_z": z, "n": n} for w, c, z, n in zip(g.window, g.coverage90, g.mean_z, g.n)}
              for r, g in COV[COV.eid == eid].groupby("race", sort=False)}
        p = {k: v for k, v in pw[eid].items()}
        acc_blocks[eid] = {"evidence": ev, "share_error_pt": se, "share_error_mae_pt": mae(eid),
                           "coverage90": cv, "coverage90_pooled": pooled_cov(eid), "pwin": p,
                           "pwin_window": "minutes from >= 1% of the race counted until both model and market stayed >= 0.9 "
                                          "on the winner (model only where no prices survive)"}
    acc_blocks["il2026"] = {"evidence": "not-preregistered", "outcome": "provisional count (results/israel/stats.json)",
                            "share_error_mae_pt": ila["share_error_mae"], "coverage90_list_shares": ila["coverage_list_shares"],
                            "pwin": pw["il2026"],
                            "pwin_window": "minutes from >= 1% of boxes counted until both model and market stayed >= 0.9 "
                                           "on the provisional outcome; model P is the latest release's"}
    summary["accuracy"]["blocks"] = acc_blocks

    # ---------------- profit
    yes_tok = _yes_tokens()
    BR = pd.concat([brazil_signals(eid, yes_tok) for eid in NIGHTS], ignore_index=True)
    _write("profit_signals_brazil.csv", BR)
    il, il_meta = israel_signals()
    _write("profit_signals_israel.csv", il)          # private: derived from the 2026 count (results/ is not published)
    ilt = israel_tables(il)

    size_rows, prof = [], {}

    def br_variant(eids: list[str], L: int, N: int, s: float, rule_only: bool = False) -> pd.DataFrame:
        x = BR[BR["eid"].isin(eids) & (BR.latency_s == L) & (BR.horizon_min == N) & (BR.slip == s)].copy()
        if rule_only:
            x.loc[~x["rule_ok"], "avail"] = 0.0
        return x.reset_index(drop=True)

    def summarize(sig: pd.DataFrame, table: list[dict]) -> dict:
        n, k = len(sig), int(sig["won"].sum()) if len(sig) else 0
        mx = next(t for t in table if t["size"] == "max")
        return {"signals": n, "won": k, "hit_rate": _r(k / n, 3) if n else None, "hit_rate_ci": _cp(k, n),
                "max_deployed": mx["deployed"], "max_profit": mx["pnl"], "max_profit_ci": mx["pnl_ci"],
                "max_return_per_usd": mx["return_per_usd"], "p_market_fair": mx["p_market_fair"],
                "largest_trade_share": mx.get("largest_trade_share"), "pnl_without_largest": mx.get("pnl_without_largest"),
                "by_size": {str(t["size"]): {kk: t.get(kk) for kk in ("fillable", "filled_any", "deployed", "pnl", "return_per_usd",
                                                                      "pnl_ci", "return_ci", "p_market_fair",
                                                                      "largest_trade_share")} for t in table if t["size"] != "max"}}

    br_groups = {eid: [eid] for eid in NIGHTS} | {"pre-specified": ["br2026r2", "br2026r2gov"]}
    for name, eids in br_groups.items():
        prof[name] = {}
        for L, N, s in itertools.product(BR_LATENCY_S, BR_HORIZON_MIN, BR_SLIPS):
            for rule_only in ((False, True) if s > 0 else (False,)):
                sig = br_variant(eids, L, N, s, rule_only)
                tab = size_table(sig, "avail", "ret", "all_in")
                key = f"trades_lat{L}s_{N}min_slip{int(round(s * 100))}c" + ("_rule" if rule_only else "")
                prof[name][key] = summarize(sig, tab)
                size_rows += [{"group": name, "execution": key, **{a: (json.dumps(b) if isinstance(b, list) else b)
                                                                     for a, b in t.items()}} for t in tab]
    prof["il2026"] = {}
    for L in IL_LATENCY_S:
        tab = il_size_table(il, L)
        key = f"book_lat{L}s"
        prof["il2026"][key] = summarize(ilt[L].assign(market_key=ilt[L]["market_key"]), tab)
        size_rows += [{"group": "il2026", "execution": key, **{a: (json.dumps(b) if isinstance(b, list) else b)
                                                                for a, b in t.items()}} for t in tab]
    _write("profit_by_size.csv", pd.DataFrame(size_rows))
    summary["profit"] = prof
    summary["profit_meta"] = {"israel": il_meta}

    # ---------------- sanity: reconcile with the reported capacity numbers
    rec = []
    reported = {"br2026r1": (2741, 4111, 6906), "br2026r2": (0, 100, 4375), "br2026r2gov": (2, 5, 45)}
    for eid, (a, b, c) in reported.items():
        got = [float(br_variant([eid], 0, 30, s)["avail"].sum()) for s in BR_SLIPS]
        caps = []
        for cid, *_ in NIGHTS[eid][1]:
            for nm in ("capacity_favoured_only_true.csv", "capacity.csv"):
                p = RES / "night" / cid / nm
                if p.exists():
                    caps.append((cid, nm, pd.read_csv(p)))
                    break
        cap_sum = [sum(float(d[col].sum()) for _, _, d in caps if col in d) if all(col in d for _, _, d in caps) else None
                   for col in ("fill_30m", "fill_30m_slip2c", "fill_30m_slip5c")]
        site_cap = json.loads((SITE / eid / "stats.json").read_text())["capacity"]["polymarket"]
        rec.append({"eid": eid, "reported_signal": a, "reported_slip2c": b, "reported_slip5c": c,
                    "recomputed_signal": round(got[0], 2), "recomputed_slip2c": round(got[1], 2),
                    "recomputed_slip5c": round(got[2], 2),
                    "capacity_csv": ";".join(f"{x}/{y}" for x, y, _ in caps),
                    "capacity_csv_signal": cap_sum[0], "capacity_csv_slip2c": cap_sum[1], "capacity_csv_slip5c": cap_sum[2],
                    "site_stats_signal": site_cap["signal"], "site_stats_slip2c": site_cap["slip2c"],
                    "site_stats_slip5c": site_cap["slip5c"],
                    "signals": int(br_variant([eid], 0, 30, 0.0).shape[0])})
    REC = pd.DataFrame(rec)
    _write("reconcile.csv", REC)
    summary["reconcile"] = REC.to_dict("records")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))

    # ---------------- site blocks
    if site:
        for eid in [*NIGHTS, *HISTORY, "il2026"]:
            blocks = {"accuracy": acc_blocks[eid]}
            if eid in NIGHTS or eid == "il2026":
                p = prof[eid]
                if eid == "il2026":
                    head, exe = p[f"book_lat{HEAD_IL}s"], (f"walk of the recorded order book {HEAD_IL} s after the signal as a "
                                                           "limit order (levels leaving >= min_edge), incl. taker fee")
                    variants = {k: {kk: v[kk] for kk in ("max_deployed", "max_profit")} for k, v in p.items()}
                else:
                    L, N, s = HEAD_BR
                    head = p[f"trades_lat{L}s_{N}min_slip{int(s * 100)}c"]
                    exe = (f"real taker trades on our side at or better than the signal price, from {L} s to {N} min "
                           "after the signal; return at the signal's all-in cost (price + half-spread + fee)")
                    variants = {k: {kk: v[kk] for kk in ("max_deployed", "max_profit")} for k, v in p.items()}
                blocks["profit"] = {"evidence": "not-preregistered" if eid == "il2026" else NIGHTS[eid][0],
                                    "rule": "frozen night rule: favoured side only, edge >= 5c after costs, >= 1% of the "
                                            "market counted, first signal per market, held to resolution",
                                    "execution": exe, **head, "variants": variants}
                if eid == "il2026":
                    blocks["profit"]["outcome"] = "provisional count; markets not yet resolved"
            _site_update(eid, blocks)

    # ---------------- console
    print(REC.to_string(index=False))
    for g, v in prof.items():
        for k, x in v.items():
            if k.endswith(("slip0c", "lat0s", "lat30s", "lat60s")) or "lat60s_30min" in k or "lat0s_30min" in k:
                print(f"{g:14s} {k:34s} n={x['signals']:2d} won={x['won']:2d} max$={x['max_deployed']} "
                      f"maxP&L={x['max_profit']} ci={x['max_profit_ci']} p_fair={x['p_market_fair']} "
                      f"top={x['largest_trade_share']}")
    for k, v in pw.items():
        print(k, {a: b for a, b in v.items() if not a.startswith("reliab")})
    print(OUT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-site", action="store_true", help="do not write the accuracy/profit blocks into site/data")
    main(not ap.parse_args().no_site)
