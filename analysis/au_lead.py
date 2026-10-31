#!/usr/bin/env python3
"""Australia 2025: is the model's lead over Polymarket a real informational lead, or thin / stale markets,
or contract caution?  Reads only the published replay (site/data/au2025/{timeline,meta,stats,units}.json);
the optional `fetch` step adds public Polymarket data (gamma market metadata, data-api taker trades) cached under
/tmp/au_lead_cache (UA "brunder/0.1 (research; recorder)", <= 1 request/s). Without the cache the trade-based
columns fall back to the counts already stored in stats.json (profit_model_limit.by_race).

    uv run python analysis/au_lead.py fetch          # contested markets: gamma metadata + taker trades (about 2 min)
    uv run python analysis/au_lead.py run            # prints every table; writes /tmp/au_lead_cache/au_lead_results.json
    uv run python analysis/au_lead.py run --boot 2000

Nothing here changes the site. Retrospective, not pre-registered.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "data" / "au2025"
CACHE = Path("/tmp/au_lead_cache")
UA = "brunder/0.1 (research; recorder)"
GAMMA = "https://gamma-api.polymarket.com/markets"
DATA_API = "https://data-api.polymarket.com/trades"
END = pd.Timestamp("2025-05-03T18:00:00Z")
THRESH = 0.90
HORIZONS = (15, 30, 60, 120)
MODELS = {"v1": "P", "v2": "P_sensitivity", "mix": "P_sens_mix"}
LAGS = 30
MIN_TRADES = 10          # "essentially no trading": fewer taker trades than this in the whole count window
# 21 divisions that stayed Maverick all night (docs/australia.md): the AEC's Labor-v-Coalition 2PP count is not the final two-candidate count there
MAVERICK = {"Bean", "Blaxland", "Farrer", "Hunter", "Newcastle", "Richmond", "Watson", "Brisbane", "Griffith", "Maranoa", "Ryan", "Mayo",
            "Franklin", "Bendigo", "Calwell", "Flinders", "Fraser", "Melbourne", "Monash", "Bullwinkel", "Fremantle"}
# band edges (seats) of the contested seat-count markets: race -> (statistic, lower, upper); statistic is evaluated on the final seats
BANDS = {"alp-majority": ("ALP", 76, 999), "hung-parliament": ("ALP", 76, 999), "grn-4plus": ("GRN", 4, 999),
         "alp-seats-70-74": ("ALP", 70, 74), "alp-seats-75-79": ("ALP", 75, 79), "alp-seats-80-84": ("ALP", 80, 84),
         "alp-seats-85plus": ("ALP", 85, 999), "lnc-seats-u45": ("LNC", 0, 44), "lnc-seats-45-49": ("LNC", 45, 49),
         "lnc-seats-50-54": ("LNC", 50, 54), "lnc-seats-55-59": ("LNC", 55, 59), "lnc-seats-60-64": ("LNC", 60, 64),
         "lnc-seats-65-69": ("LNC", 65, 69), "seatmargin-alp40": ("MARGIN", 40, 999), "seatmargin-alp30": ("MARGIN", 30, 39),
         "seatmargin-alp20": ("MARGIN", 20, 29), "seatmargin-alp10": ("MARGIN", 10, 19), "seatmargin-alp1": ("MARGIN", 1, 9)}


# ----------------------------------------------------------------------------- data
def load() -> dict:
    tl = json.loads((SITE / "timeline.json").read_text())
    meta = json.loads((SITE / "meta.json").read_text())
    st = json.loads((SITE / "stats.json").read_text())
    units = json.loads((SITE / "units.json").read_text())
    races = {r["race_id"]: r for r in meta["races"]}
    ser = {}
    for rid, rows in tl.items():
        d = pd.DataFrame(rows)
        d["t"] = pd.to_datetime(d["t"], utc=True)
        ser[rid] = d.set_index("t")
    cls = st["market_classification"]["by_race"]
    contested = [r for r in races if r in cls and not cls[r]["decided_before_count"]]
    gate = pd.Timestamp(st["settle_gated"]["alp-majority"]["gate"])
    return {"tl": ser, "meta": meta, "stats": st, "units": units, "races": races, "cls": cls, "contested": contested,
            "gate": gate, "close": pd.Timestamp(meta["poll_close_utc"]),
            "complete": pd.Timestamp(st["final"]["count_complete"])}


def pm_slug(race: dict) -> str | None:
    return next((m["id"] for m in race["markets"] if m["platform"] == "polymarket"), None)


def oriented(d: pd.DataFrame, a_won: bool, col: str) -> pd.Series:
    """Probability / price of the eventual outcome."""
    x = d[col]
    return x if a_won else 1 - x


def first_stay(q: pd.Series, start: pd.Timestamp, thr: float = THRESH) -> pd.Timestamp | None:
    """First minute t >= start from which q stays >= thr to the last minute (the headline 'settle')."""
    x = q[q.index >= start]
    ok = (x >= thr).to_numpy()
    if not len(ok) or not ok[-1]:
        return None
    bad = np.flatnonzero(~ok)
    i = 0 if not len(bad) else bad[-1] + 1
    return x.index[i]


def first_reach(q: pd.Series, start: pd.Timestamp, thr: float = THRESH) -> pd.Timestamp | None:
    x = q[(q.index >= start) & (q >= thr)]
    return x.index[0] if len(x) else None


def at(q: pd.Series, t: pd.Timestamp, minutes: int = 0) -> float | None:
    t2 = min(max(t + pd.Timedelta(minutes=minutes), q.index[0]), q.index[-1])
    v = q.loc[:t2].dropna()
    return float(v.iloc[-1]) if len(v) else None


# ----------------------------------------------------------------------------- fetch
def fetch(D: dict) -> None:
    import httpx
    CACHE.mkdir(exist_ok=True)
    (CACHE / "trades").mkdir(exist_ok=True)
    lo, hi = int(D["close"].timestamp()), int((END + pd.Timedelta(minutes=1)).timestamp())
    last = [0.0]

    def get(c, url, params):
        wait = 1.05 - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        for k in range(5):
            r = c.get(url, params=params)
            last[0] = time.time()
            if r.status_code in (429, 500, 502, 503, 504) and k < 4:
                time.sleep(2 ** k)
                continue
            r.raise_for_status()
            return r.json()

    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        for rid in D["races"]:
            slug = pm_slug(D["races"][rid])
            f = CACHE / f"gamma_{slug}.json"
            if slug and not f.exists():
                rows = get(c, GAMMA, {"slug": slug, "closed": "true"}) or get(c, GAMMA, {"slug": slug})
                f.write_text(json.dumps(rows[0] if rows else None))
        for rid in D["contested"]:
            slug = pm_slug(D["races"][rid])
            f = CACHE / "trades" / f"{slug}.csv"
            g = json.loads((CACHE / f"gamma_{slug}.json").read_text())
            if f.exists() or not g:
                continue
            yes_tok = json.loads(g["clobTokenIds"])[0]
            seen, rows, end, offset = set(), [], hi - 1, 0
            while True:
                b = get(c, DATA_API, {"market": g["conditionId"], "takerOnly": "true", "limit": 500, "offset": offset, "end": end})
                new = 0
                for x in b:
                    k = (x["transactionHash"], x["asset"], x["proxyWallet"], x["side"], x["size"], x["price"], x["timestamp"])
                    if k not in seen:
                        seen.add(k)
                        new += 1
                        if lo <= x["timestamp"] < hi:
                            rows.append(x)
                if len(b) < 500 or not b:
                    break
                mn = min(x["timestamp"] for x in b)
                if mn < lo:
                    break
                if mn == end or new == 0:
                    offset += 500
                else:
                    end, offset = mn, 0
            df = pd.DataFrame(rows, columns=["timestamp", "side", "asset", "price", "size", "transactionHash", "proxyWallet"])
            df["yes_price"] = np.where(df["asset"].astype(str) == str(yes_tok), df["price"].astype(float), 1 - df["price"].astype(float))
            df["usd"] = df["size"].astype(float) * df["price"].astype(float)
            df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            df.sort_values("time").to_csv(f, index=False)
            print(f"{slug}: {len(df)} taker trades", file=sys.stderr)


def trades_for(slug: str) -> pd.DataFrame | None:
    f = CACHE / "trades" / f"{slug}.csv"
    if not f.exists():
        return None
    d = pd.read_csv(f)
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d


def gamma_for(slug: str) -> dict | None:
    f = CACHE / f"gamma_{slug}.json"
    return json.loads(f.read_text()) if f.exists() else None


# ----------------------------------------------------------------------------- (1) price after the model's crossing
def crossing_table(D: dict) -> pd.DataFrame:
    out = []
    for rid in D["contested"]:
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        mkt = oriented(d, a_won, "pm").dropna()
        for mv, col in MODELS.items():
            q = oriented(d, a_won, col).dropna()
            ts = first_stay(q, D["gate"])
            tr = first_reach(q, D["gate"])
            tm = first_stay(mkt, D["gate"])
            tmr = first_reach(mkt, D["gate"])
            row = {"race": rid, "model": mv, "outcome": "yes" if a_won else "no", "t_model_settle": ts, "t_model_reach": tr,
                   "t_mkt_settle": tm, "t_mkt_reach": tmr}
            row["lead_settle_min"] = (tm - ts).total_seconds() / 60 if ts is not None and tm is not None else np.nan
            row["lead_reach_min"] = (tmr - tr).total_seconds() / 60 if tr is not None and tmr is not None else np.nan
            t0 = ts if ts is not None else tr
            row["price_at_cross"] = at(mkt, t0) if t0 is not None else np.nan
            for h in HORIZONS:
                row[f"price_p{h}"] = at(mkt, t0, h) if t0 is not None else np.nan
            row["last_price_end"] = float(mkt.iloc[-1])
            row["counted_at_model"] = at(d["counted"].dropna(), ts) if ts is not None else np.nan
            row["counted_at_mkt"] = at(d["counted"].dropna(), tm) if tm is not None else np.nan
            row["price_before_cross_60"] = at(mkt, t0, -60) if t0 is not None else np.nan
            out.append(row)
    return pd.DataFrame(out)


def lead_by_threshold(D: dict, thresholds=(0.8, 0.9, 0.95, 0.99)) -> pd.DataFrame:
    """Median (model settle -> market settle) lead at several thresholds; markets that never settle for one side drop out."""
    rows = []
    for mv, col in MODELS.items():
        for thr in thresholds:
            leads = []
            for rid in D["contested"]:
                a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
                d = D["tl"][rid]
                ts = first_stay(oriented(d, a_won, col).dropna(), D["gate"], thr)
                tm = first_stay(oriented(d, a_won, "pm").dropna(), D["gate"], thr)
                if ts is not None and tm is not None:
                    leads.append((tm - ts).total_seconds() / 60)
            rows.append({"model": mv, "thr": thr, "n": len(leads), "median": float(np.median(leads)) if leads else np.nan,
                         "model_first": int(sum(x > 0 for x in leads)), "market_first": int(sum(x < 0 for x in leads))})
    return pd.DataFrame(rows)


def calls_table(D: dict) -> pd.DataFrame:
    """First minute the model is >= 0.9 on EITHER side after the gate, whether that call was right, and the market price then."""
    rows = []
    for rid in D["contested"]:
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        for mv, col in MODELS.items():
            p = d[col].dropna()
            p = p[p.index >= D["gate"]]
            side = None
            for t, v in p.items():
                if v >= THRESH:
                    side, tt = True, t
                    break
                if v <= 1 - THRESH:
                    side, tt = False, t
                    break
            if side is None:
                rows.append({"race": rid, "model": mv, "call": None})
                continue
            px = d["pm"].loc[tt]
            wrong_minutes = int(((p >= THRESH) if not a_won else (p <= 1 - THRESH)).sum())
            rows.append({"race": rid, "model": mv, "call": "yes" if side else "no", "t": tt, "right": side == a_won,
                         "mkt_on_call_side": float(px if side else 1 - px), "wrong_side_minutes_ge90": wrong_minutes})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- (2) lead-lag
def _panel(D: dict, col: str, races: list[str], step: int = 1, start=None, end=None):
    """Per race: arrays dpm_t, dP_t at the `step`-minute grid (changes over `step` minutes)."""
    out = {}
    for rid in races:
        d = D["tl"][rid]
        d = d[(d.index >= (start or D["gate"])) & (d.index <= (end or D["complete"]))]
        p, m = d[col].astype(float), d["pm"].astype(float)
        if p.isna().any() or m.isna().any():
            p, m = p.ffill(), m.ffill()
        out[rid] = (m.diff(step).to_numpy(), p.diff(step).to_numpy(), m.shift(step).to_numpy(), p.shift(step).to_numpy())
    return out


def _design(y, x, nlag, step, dep_active=None):
    """Rows t with y_t and x_{t-k*step} (k=1..nlag) all defined (x = changes over `step` minutes)."""
    n = len(y)
    cols = [np.r_[np.full(k * step, np.nan), x[:n - k * step]] for k in range(1, nlag + 1)]
    X = np.column_stack(cols)
    ok = ~np.isnan(y) & ~np.isnan(X).any(1)
    if step > 1:
        ok &= (np.arange(n) % step == 0)
    if dep_active is not None:
        ok &= dep_active
    return y[ok], X[ok]


def _fe_ols(blocks, nlag):
    """Within-market OLS. blocks: list of (y, X). Returns coefficient vector for the X columns."""
    ys, Xs = [], []
    for y, X in blocks:
        if len(y) < nlag + 5:
            continue
        ys.append(y - y.mean())
        Xs.append(X - X.mean(0))
    if not ys:
        return np.full(nlag, np.nan)
    Y, XX = np.concatenate(ys), np.vstack(Xs)
    return np.linalg.lstsq(XX, Y, rcond=None)[0]


def leadlag(D: dict, col: str, races: list[str], direction: str, nlag=LAGS, step=1, active=False, boot=1000, seed=7) -> dict:
    """direction 'model->mkt': dpm_t on dP_{t-1..t-nlag};  'mkt->model': dP_t on dpm_{t-1..t-nlag}.
    Market fixed effects (within transform); block bootstrap over markets."""
    P = _panel(D, col, races, step)
    blocks = {}
    for rid, (dm, dp, lm, lp) in P.items():
        y, x = (dm, dp) if direction == "model->mkt" else (dp, dm)
        act = None
        if active:   # dependent minutes where either series sat inside (0.02, 0.98) a step earlier
            act = ((lm > .02) & (lm < .98)) | ((lp > .02) & (lp < .98))
        blocks[rid] = _design(y, x, nlag, step, act)
    names = list(blocks)
    beta = _fe_ols([blocks[r] for r in names], nlag)
    rng = np.random.default_rng(seed)
    B = np.empty((boot, nlag))
    for b in range(boot):
        pick = rng.integers(0, len(names), len(names))
        B[b] = _fe_ols([blocks[names[i]] for i in pick], nlag)
    ssum = np.nansum(beta)
    bs = np.nansum(B, 1)
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    # R2 contribution of the lagged regressors (within)
    Y = np.concatenate([b[0] - b[0].mean() for b in blocks.values() if len(b[0]) > nlag + 5])
    XX = np.vstack([b[1] - b[1].mean(0) for b in blocks.values() if len(b[0]) > nlag + 5])
    r2 = 1 - np.sum((Y - XX @ beta) ** 2) / np.sum(Y ** 2)
    return {"sum": ssum, "ci": (lo, hi), "beta": beta, "beta_ci": np.nanpercentile(B, [2.5, 97.5], axis=0), "n": len(Y), "r2": r2,
            "p_sum_pos": float((bs > 0).mean()), "markets": len(names)}


def granger(D: dict, col: str, races: list[str], nlag=LAGS, boot=1000, seed=11) -> dict:
    """Joint regression: dpm_t on own lags + model lags, and dP_t on own lags + market lags; returns the cross sums.
    Within-market, block bootstrap over markets."""
    P = _panel(D, col, races, 1)
    blk = {"m": {}, "p": {}}
    for rid, (dm, dp, *_r) in P.items():
        n = len(dm)
        def lags(x): return np.column_stack([np.r_[np.full(k, np.nan), x[:n - k]] for k in range(1, nlag + 1)])
        Lm, Lp = lags(dm), lags(dp)
        for key, y, own, other in (("m", dm, Lm, Lp), ("p", dp, Lp, Lm)):
            X = np.hstack([other, own])
            ok = ~np.isnan(y) & ~np.isnan(X).any(1)
            blk[key][rid] = (y[ok], X[ok])
    res = {}
    names = list(P)
    for key, label in (("m", "model->mkt"), ("p", "mkt->model")):
        def fit(pick):
            ys, Xs = [], []
            for i in pick:
                y, X = blk[key][names[i]]
                ys.append(y - y.mean())
                Xs.append(X - X.mean(0))
            b = np.linalg.lstsq(np.vstack(Xs), np.concatenate(ys), rcond=None)[0]
            return b[:nlag].sum()
        est = fit(range(len(names)))
        rng = np.random.default_rng(seed)
        bs = np.array([fit(rng.integers(0, len(names), len(names))) for _ in range(boot)])
        res[label] = {"sum_cross": est, "ci": tuple(np.percentile(bs, [2.5, 97.5]))}
    return res


def lag0(D: dict, col: str, races: list[str], boot=1000, seed=3) -> dict:
    """Contemporaneous within-market slope of dpm_t on dP_t and of dP_t on dpm_t (minute changes, from the gate)."""
    P = _panel(D, col, races)
    names = list(P)
    pairs = {}
    for rid, (dm, dp, *_r) in P.items():
        ok = ~np.isnan(dm) & ~np.isnan(dp)
        pairs[rid] = (dm[ok], dp[ok])

    def slope(pick, i):
        num = den = 0.0
        for k in pick:
            y, x = pairs[names[k]][i], pairs[names[k]][1 - i]
            x = x - x.mean()
            y = y - y.mean()
            num += (x * y).sum()
            den += (x * x).sum()
        return num / den

    rng = np.random.default_rng(seed)
    out = {}
    for i, lab in ((0, "dpm_on_dP"), (1, "dP_on_dpm")):
        est = slope(range(len(names)), i)
        bs = [slope(rng.integers(0, len(names), len(names)), i) for _ in range(boot)]
        out[lab] = (est, tuple(np.percentile(bs, [2.5, 97.5])))
    return out


def gap_regression(D: dict, col: str, races: list[str], horizons=(15, 30, 60, 120), boot=1000, seed=5) -> pd.DataFrame:
    """Error-correction test in levels. gap_t = P_t - pm_t (YES-oriented).
    market catches up : pm_{t+h} - pm_t = b * gap_t   (b -> 1 means the market closes the gap to the model)
    model follows     : P_{t+h} - P_t  = c * (-gap_t) (c > 0 means the model moves toward the market)
    Market fixed effects, minutes from the gate to the end of the count, block bootstrap over markets."""
    rows = []
    for h in horizons:
        blocks = {}
        for rid in races:
            d = D["tl"][rid]
            d = d[(d.index >= D["gate"]) & (d.index <= D["complete"])]
            p, m = d[col].astype(float).ffill(), d["pm"].astype(float).ffill()
            gap = (p - m).to_numpy()
            fm = (m.shift(-h) - m).to_numpy()
            fp = (p.shift(-h) - p).to_numpy()
            ok = ~np.isnan(gap) & ~np.isnan(fm) & ~np.isnan(fp)
            blocks[rid] = (gap[ok], fm[ok], fp[ok])
        names = list(blocks)

        def slopes(pick):
            sx = sxy_m = sxy_p = 0.0
            for k in pick:
                g, fm, fp = blocks[names[k]]
                g0 = g - g.mean()
                sx += (g0 * g0).sum()
                sxy_m += (g0 * (fm - fm.mean())).sum()
                sxy_p += (g0 * (fp - fp.mean())).sum()
            return sxy_m / sx, -sxy_p / sx

        est = slopes(range(len(names)))
        rng = np.random.default_rng(seed + h)
        bs = np.array([slopes(rng.integers(0, len(names), len(names))) for _ in range(boot)])
        for j, lab in enumerate(("market closes gap (b)", "model moves to market (c)")):
            lo, hi = np.percentile(bs[:, j], [2.5, 97.5])
            rows.append({"h_min": h, "slope": lab, "est": est[j], "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def reliability_by_counted(D: dict, col: str = "P", edges=(0.0, 0.05, 0.10, 0.25, 0.50, 1.0)) -> pd.DataFrame:
    """Every contested market-minute (gate -> end of count) on which the model is >= 0.9 on one side: how often was that side right,
    by share of the count in; and the market's price on that side. Hit rates are market-weighted (one vote per market and bin)."""
    rows = []
    for rid in D["contested"]:
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        d = d[(d.index >= D["gate"]) & (d.index <= D["complete"])].dropna(subset=[col, "counted"])
        side_yes = d[col] >= THRESH
        side_no = d[col] <= 1 - THRESH
        for lo, hi in zip(edges[:-1], edges[1:]):
            inb = (d["counted"] >= lo) & (d["counted"] < hi)
            for m, yes in ((side_yes, True), (side_no, False)):
                x = d[inb & m]
                if len(x):
                    rows.append({"race": rid, "bin": f"{int(lo * 100)}-{int(hi * 100)}%", "side_yes": yes, "right": yes == a_won, "minutes": len(x),
                                 "mkt_on_side": float((x["pm"] if yes else 1 - x["pm"]).mean())})
    r = pd.DataFrame(rows)
    out = []
    for b, g in r.groupby("bin", sort=False):
        # one call per (market, bin): the side with more minutes
        g = g.sort_values("minutes", ascending=False).drop_duplicates("race")
        out.append({"bin": b, "markets": len(g), "right": int(g["right"].sum()), "wrong": int((~g["right"]).sum()),
                    "hit_rate": float(g["right"].mean()), "mean_mkt_price_on_called_side": float(g["mkt_on_side"].mean()),
                    "wrong_markets": list(g[~g["right"]]["race"])})
    return pd.DataFrame(out)


def stale_table(D: dict) -> pd.DataFrame:
    """Per contested market, gate -> end of count: longest run of unchanged price (minutes), and the share of the model's lead window
    (model settle -> market settle) with the price unchanged."""
    rows = []
    for rid in D["contested"]:
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        w = d[(d.index >= D["gate"]) & (d.index <= D["complete"])]["pm"]
        chg = w.diff().abs() > 1e-9
        run = best = 0
        for c in chg.to_numpy()[1:]:
            run = 0 if c else run + 1
            best = max(best, run)
        ts = first_stay(oriented(d, a_won, "P").dropna(), D["gate"])
        tm = first_stay(oriented(d, a_won, "pm").dropna(), D["gate"])
        row = {"race": rid, "longest_unchanged_min": best, "share_minutes_unchanged": float(1 - chg.to_numpy()[1:].mean())}
        if ts is not None and tm is not None and tm > ts:
            lw = d["pm"][(d.index >= ts) & (d.index < tm)]
            row["lead_window_min"] = len(lw)
            row["lead_window_changes"] = int((lw.diff().abs() > 1e-9).sum())
            row["lead_window_unchanged_share"] = float(1 - (lw.diff().abs() > 1e-9).to_numpy()[1:].mean()) if len(lw) > 1 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- (3) activity
def activity_table(D: dict) -> pd.DataFrame:
    by = D["stats"]["profit_model_limit"]["by_race"]
    rows = []
    for rid in D["contested"]:
        slug = pm_slug(D["races"][rid])
        d = D["tl"][rid]
        w = d[(d.index >= D["gate"]) & (d.index <= D["complete"])]
        chg = (w["pm"].diff().abs() > 1e-9)
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        ts = first_stay(oriented(d, a_won, "P").dropna(), D["gate"])
        tm = first_stay(oriented(d, a_won, "pm").dropna(), D["gate"])
        s = by.get(slug, {})
        row = {"race": rid, "slug": slug, "minutes": len(w), "min_with_change": int(chg.sum()), "pm_levels": int(w["pm"].nunique()),
               "trades_stats": s.get("trades_read"), "first_trade": s.get("first_trade"), "last_trade": s.get("last_trade")}
        tr = trades_for(slug)
        if tr is not None:
            row["trades"] = len(tr)
            row["usd_taker"] = float(tr["usd"].sum())
            row["trades_after_gate"] = int((tr["time"] >= D["gate"]).sum())
            if ts is not None:
                end_ = tm if tm is not None else D["complete"]
                lw = tr[(tr["time"] >= ts) & (tr["time"] < end_)]
                row["trades_in_lead_window"] = len(lw)
                row["usd_in_lead_window"] = float(lw["usd"].sum())
                row["trades_before_model_settle"] = int(((tr["time"] >= D["gate"]) & (tr["time"] < ts)).sum())
        n = row.get("trades", row["trades_stats"])
        row["thin"] = bool(n is not None and n < MIN_TRADES)
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- (4) contract conditions and end of night
def contract_table(D: dict) -> pd.DataFrame:
    rows = []
    for rid in D["contested"]:
        slug = pm_slug(D["races"][rid])
        g = gamma_for(slug)
        if not g:
            rows.append({"race": rid, "slug": slug})
            continue
        desc = " ".join((g.get("description") or "").split())
        rows.append({"race": rid, "slug": slug, "question": g.get("question"), "resolutionSource": g.get("resolutionSource") or "",
                     "endDate": g.get("endDate"), "closedTime": g.get("closedTime"), "umaEndDate": g.get("umaEndDate"),
                     "outcomePrices": g.get("outcomePrices"), "volume": g.get("volumeNum"), "description": desc})
    return pd.DataFrame(rows)


def band_distance(D: dict) -> pd.DataFrame:
    """Seats between the final result and the nearest edge of each contested seat-count band (0.5 = inside the band, next to the edge)."""
    s = D["stats"]["final"]["seats"]
    val = {"ALP": s["ALP"], "LNC": s["LNC"], "GRN": s["GRN"], "MARGIN": s["ALP"] - s["LNC"]}
    rows = []
    for rid, (stat, lo, hi) in BANDS.items():
        v = val[stat]
        edges = [e for e in (lo - 0.5, hi + 0.5) if 0 < e < 900]
        rows.append({"race": rid, "stat": stat, "final": v, "band": f"{lo}-{hi if hi < 900 else '+'}", "in_band": lo <= v <= hi,
                     "dist_to_edge": min(abs(v - e) for e in edges)})
    return pd.DataFrame(rows)


def end_of_night(D: dict) -> dict:
    """Model end-of-night (last published frame) vs the AEC's final result: per division, per band, nationally."""
    u, st = D["units"], D["stats"]
    last = u["frames"][-1]
    names = u["names"]
    f, a, proj = np.array(last["f"]) / 1000, np.array(last["a"]) / 1000, np.array(last["proj"]) / 1000
    win, mar = np.array(u["winner"]), np.array(u["margin"]) / 1000
    V0 = np.array(u["V0"], float)
    has = (a >= 0) & (proj >= 0) & np.isin(win, ["ALP", "LNC"]) & ~np.isin(names, list(MAVERICK))
    # final Labor share of the two-candidate count (ALP v LNC divisions only); winner share = 0.5 + margin/2
    fin = np.where(win == "ALP", 0.5 + mar / 2, 0.5 - mar / 2)
    rows = pd.DataFrame({"div": names, "winner": win, "counted": f, "a_end": a, "proj_end": proj, "final": fin, "V0": V0, "ok": has})
    r = rows[rows["ok"]].copy()
    r["drift_vs_counted"] = r["final"] - r["a_end"]
    r["err_proj"] = r["proj_end"] - r["final"]
    w = r["V0"] / r["V0"].sum()
    flips_count = r[(r["a_end"] > .5) != (r["winner"] == "ALP")]
    flips_proj = r[(r["proj_end"] > .5) != (r["winner"] == "ALP")]
    # nationally
    tl = D["tl"]["most-alp"]
    lastrow = tl.loc[:D["complete"]].iloc[-1]
    final2pp = st["final"]["two_party_preferred_pct"]["Labor two-party-preferred"] / 100
    nat = {"t": str(lastrow.name), "counted": float(lastrow["counted"]), "raw": float(lastrow["raw"]), "model_share": float(lastrow["share"]),
           "model_share_v2": float(lastrow["share_sensitivity"]), "model_share_mix": float(lastrow.get("share_sens_mix", np.nan)),
           "final_2pp": final2pp}
    return {"nat": nat, "divisions": len(r), "mean_counted": float(r["counted"].mean()),
            "drift_w": float((w * r["drift_vs_counted"]).sum()), "drift_mean": float(r["drift_vs_counted"].mean()),
            "err_proj_w": float((w * r["err_proj"]).sum()), "err_proj_mae": float(r["err_proj"].abs().mean()),
            "drift_toward_lnc": int((r["drift_vs_counted"] < 0).sum()), "drift_toward_alp": int((r["drift_vs_counted"] > 0).sum()),
            "flips_counted": flips_count[["div", "winner", "a_end", "proj_end", "final", "counted"]].to_dict("records"),
            "flips_proj": flips_proj[["div", "winner", "a_end", "proj_end", "final", "counted"]].to_dict("records"),
            "final_seats": st["final"]["seats"], "rows": r}


def band_end_table(D: dict) -> pd.DataFrame:
    rows = []
    for rid in D["contested"]:
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        e = d.loc[:D["complete"]].iloc[-1]
        rows.append({"race": rid, "outcome": "yes" if a_won else "no",
                     "P_v1": float(oriented(e.to_frame().T, a_won, "P").iloc[0]),
                     "P_v2": float(oriented(e.to_frame().T, a_won, "P_sensitivity").iloc[0]),
                     "P_mix": float(oriented(e.to_frame().T, a_won, "P_sens_mix").iloc[0]),
                     "pm": float(oriented(e.to_frame().T, a_won, "pm").iloc[0])})
    return pd.DataFrame(rows)


def signal_fills(D: dict, half_spread=0.01, edge=0.05, latency_s=60, horizon_min=30) -> pd.DataFrame:
    """Approximate replication of the frozen-rule 'signal price' execution on the contested markets (edge_audit.py is not rerun):
    first minute after the 1% gate on which the favoured side's model P minus (its price + half-spread) >= edge; then the taker trades
    on our side from latency to horizon after it, at or better than the signal price. Polymarket AU markets carry no taker fee (gamma feesEnabled false)."""
    rows = []
    for rid in D["contested"]:
        slug = pm_slug(D["races"][rid])
        tr, g = trades_for(slug), gamma_for(slug)
        if tr is None or not g:
            continue
        yes_tok = json.loads(g["clobTokenIds"])[0]
        a_won = bool(D["stats"]["settle"][rid]["outcome_a"])
        d = D["tl"][rid]
        d = d[d["counted"] >= 0.01].dropna(subset=["P", "pm"])
        sig = None
        for t, r in d.iterrows():
            fy = r.P >= 0.5
            pf, q = (r.P, r.pm) if fy else (1 - r.P, 1 - r.pm)
            if pf - (q + half_spread) >= edge:
                sig = (t, fy, pf, q)
                break
        if sig is None:
            rows.append({"race": rid, "signal": None})
            continue
        t, fy, pf, q = sig
        is_yes = tr["asset"].astype(str) == str(yes_tok)
        long_yes = (tr["side"] == "BUY") == is_yes
        sp = pd.Series(np.where(long_yes, tr["yes_price"], 1 - tr["yes_price"]), index=tr.index)
        win = (long_yes == fy) & (tr["time"] >= t + pd.Timedelta(seconds=latency_s)) & (tr["time"] < t + pd.Timedelta(minutes=horizon_min))
        ours = tr[win]
        fill = ours[sp[win] <= q + 1e-9]
        rows.append({"race": rid, "signal": t, "side": "YES" if fy else "NO", "model_P_side": pf, "signal_price": q, "trades_our_side": len(ours),
                     "best_price_our_side": float(sp[win].min()) if len(ours) else np.nan, "fills": len(fill), "fill_usd": float(fill["usd"].sum()),
                     "won": fy == a_won})
    return pd.DataFrame(rows)


def markdown_tables(D: dict, ct: pd.DataFrame, at_: pd.DataFrame, stl: pd.DataFrame, bd: pd.DataFrame, ctr: pd.DataFrame) -> None:
    """Per-market tables for docs/australia_lead.md (times in AEST = UTC+10)."""
    aest = lambda t: "-" if t is None or pd.isna(t) else (t + pd.Timedelta(hours=10)).strftime("%H:%M")
    f = lambda v, n=2: "-" if v is None or pd.isna(v) else f"{v:.{n}f}"
    x = ct[ct.model == "v1"].set_index("race")
    c = ctr.set_index("race")
    print("\nTABLE 1 (markdown)")
    print("| market | outcome | model >= 0.90 (AEST) | Polymarket price then | +15 min | +30 | +60 | +120 | market >= 0.90 (AEST) | lead (min) | Polymarket resolved (h after close) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for rid in D["contested"]:
        r = x.loc[rid]
        hrs = (pd.Timestamp(c.loc[rid, "closedTime"], tz="UTC") - D["close"]).total_seconds() / 3600 if c.loc[rid].get("closedTime") else float("nan")
        print(f"| {rid} | {r.outcome} | {aest(r.t_model_settle)} | {f(r.price_at_cross)} | {f(r.price_p15)} | {f(r.price_p30)} | {f(r.price_p60)} | "
              f"{f(r.price_p120)} | {aest(r.t_mkt_settle)} | {f(r.lead_settle_min, 0)} | {f(hrs, 1)} |")
    print("\nTABLE 2 (markdown)")
    print("| market | taker trades | taker $ | minutes with a price change (of 464) | longest flat run (min) | trades in lead window | count in at market >= 0.90 | final seats vs band edge | lead (min) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    a = at_.set_index("race")
    s = stl.set_index("race")
    b = bd.set_index("race")
    for rid in D["contested"]:
        r = x.loc[rid]
        print(f"| {rid} | {int(a.loc[rid, 'trades'])} | {a.loc[rid, 'usd_taker']:,.0f} | {int(a.loc[rid, 'min_with_change'])} | {int(s.loc[rid, 'longest_unchanged_min'])} | "
              f"{f(a.loc[rid, 'trades_in_lead_window'], 0)} | {f(100 * r.counted_at_mkt, 0) + '%' if pd.notna(r.counted_at_mkt) else '-'} | "
              f"{f(b.loc[rid, 'dist_to_edge'], 1) if rid in b.index else '-'} | {f(r.lead_settle_min, 0)} |")


# ----------------------------------------------------------------------------- (5) $10k signal price vs model limit
def invest_table(D: dict) -> dict:
    st = D["stats"]
    ml = st["invest10k"]
    sp = ml["signal_price"]
    by = ml["by_race"]
    tab = pd.DataFrame([{"market": k, "bankroll_stake": v["bankroll_stake"], "bankroll_pnl": v["bankroll_pnl"],
                         "eligible_volume": v["eligible_volume"], "eligible_pnl": v["eligible_pnl"]} for k, v in by.items()])
    prm = st["profit_model_limit"]["by_race"]
    tab["trades_read"] = tab["market"].map(lambda k: prm.get(k, {}).get("trades_read"))
    tab["first_edge_price"] = tab["market"].map(lambda k: (prm.get(k, {}).get("first_eligible") or {}).get("price"))
    tab["first_edge_side"] = tab["market"].map(lambda k: (prm.get(k, {}).get("first_eligible") or {}).get("side"))
    tab["first_edge_modelP"] = tab["market"].map(lambda k: (prm.get(k, {}).get("first_eligible") or {}).get("model_p"))
    tab = tab.sort_values("bankroll_pnl", ascending=False).reset_index(drop=True)
    return {"table": tab, "ml": {k: ml[k] for k in ("ending_value", "deployed", "bets", "markets", "won", "return_pct", "eligible_volume",
                                                  "eligible_pnl", "largest_market", "largest_market_pnl", "largest_market_share")},
            "sp": {k: sp[k] for k in ("ending_value", "deployed", "bets", "won", "return_pct", "signals", "bets_path")},
            "grid": st["profit_model_limit"]["grid"]["edge5c_delay60s"]}


# ----------------------------------------------------------------------------- main
def fmt_t(t) -> str:
    return "-" if t is None or (isinstance(t, float) and np.isnan(t)) or pd.isna(t) else t.strftime("%H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "run"])
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    D = load()
    if a.cmd == "fetch":
        fetch(D)
        return
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200, "display.max_colwidth", 60)
    res = {}
    C = D["contested"]
    print(f"contested {len(C)}: gate {D['gate']:%H:%M}Z, complete {D['complete']:%H:%M}Z")

    print("\n=== (1) market price (on the eventual outcome) when the model settled >= 0.90, and 15/30/60/120 min later ===")
    ct = crossing_table(D)
    for mv in MODELS:
        x = ct[ct.model == mv]
        print(f"-- model {mv}: leads (settle) n={x.lead_settle_min.notna().sum()} median {x.lead_settle_min.median():.0f} "
              f"model first {(x.lead_settle_min > 0).sum()} market first {(x.lead_settle_min < 0).sum()}")
    x = ct[ct.model == "v1"].copy()
    for c in ("t_model_settle", "t_mkt_settle"):
        x[c] = x[c].map(fmt_t)
    print(x[["race", "outcome", "t_model_settle", "t_mkt_settle", "lead_settle_min", "price_before_cross_60", "price_at_cross",
             "price_p15", "price_p30", "price_p60", "price_p120", "last_price_end"]].round(3).to_string(index=False))
    y = ct[(ct.model == "v1") & ct.lead_settle_min.notna()]
    print("v1 price at crossing, median/mean over markets with a lead:", round(y.price_at_cross.median(), 3), round(y.price_at_cross.mean(), 3),
          "| +15/30/60/120 medians:", [round(y[f"price_p{h}"].median(), 3) for h in HORIZONS],
          "| share still < 0.9 at +15/30/60/120:", [round((y[f"price_p{h}"] < THRESH).mean(), 2) for h in HORIZONS])
    for mv in ("v2", "mix"):
        y2 = ct[(ct.model == mv) & ct.lead_settle_min.notna()]
        print(f"{mv} price at crossing median", round(y2.price_at_cross.median(), 3), "| +15/30/60/120 medians:",
              [round(y2[f"price_p{h}"].median(), 3) for h in HORIZONS])
    lt = lead_by_threshold(D)
    print("\nmedian lead by threshold (settle = stays above to the end of the night):")
    print(lt.assign(thr=lt.thr.map(lambda v: f"{v:.2f}")).round(1).to_string(index=False))
    cl = calls_table(D)
    print("\nfirst model call >= 0.9 either side after the gate (v1, v2, mix): right / wrong / none")
    for mv in MODELS:
        c = cl[cl.model == mv]
        print(f"  {mv}: right {int((c.right == True).sum())} wrong {int((c.right == False).sum())} none {int(c.call.isna().sum())}")
    print(cl[(cl.model == "v1") & (cl.right == False)].round(3).to_string(index=False))
    res["crossing"], res["thresholds"], res["calls"] = ct, lt, cl
    rel = reliability_by_counted(D)
    print("\nmodel v1 >= 0.9 calls by share of the count in (one call per market and bin, minutes gate -> end of count):")
    print(rel.assign(hit_rate=rel.hit_rate.round(2), mean_mkt_price_on_called_side=rel.mean_mkt_price_on_called_side.round(2)).to_string(index=False))
    res["reliability"] = rel
    x1 = ct[(ct.model == "v1") & ct.lead_settle_min.notna()]
    print("share of the count in when the model settled / when the market settled (v1, markets with a lead): median",
          round(x1.counted_at_model.median(), 3), "/", round(x1.counted_at_mkt.median(), 3),
          "| market settle times (UTC, rounded to 15 min):", sorted(pd.Series([t.floor("15min").strftime("%H:%M") for t in x1.t_mkt_settle]).value_counts().to_dict().items()))

    print("\n=== (2) lead-lag, market fixed effects, block bootstrap over markets ===")
    usable = list(C)
    print(f"markets in the regression: {len(usable)} (all contested; grn-4plus is the Polymarket market 'Will the Greens win 4+ seats in Australian Election?' whose slug reads will-the-patient-focus-pf-win-a-seat-in-norwegian-election-328)")
    tab = []
    for mv, col in MODELS.items():
        for lab, kw in (("1-min, lags 1-30, from gate", {}), ("1-min, active minutes only", {"active": True}),
                        ("5-min grid, lags 1-24 (to 120 min)", {"step": 5, "nlag": 24})):
            for dr in ("model->mkt", "mkt->model"):
                r = leadlag(D, col, usable, dr, boot=a.boot, **kw)
                tab.append({"model": mv, "spec": lab, "dir": dr, "sum_lags": r["sum"], "lo": r["ci"][0], "hi": r["ci"][1],
                            "within_R2": r["r2"], "n": r["n"], "P(sum>0)": r["p_sum_pos"]})
                res[f"leadlag_{mv}_{lab}_{dr}"] = r
    T = pd.DataFrame(tab)
    print(T.round(4).to_string(index=False))
    res["leadlag"] = T
    g = {mv: granger(D, col, usable, boot=a.boot) for mv, col in MODELS.items()}
    print("joint (own lags included) cross-lag sums:")
    for mv, v in g.items():
        print("  ", mv, {k: (round(x["sum_cross"], 4), tuple(round(z, 4) for z in x["ci"])) for k, x in v.items()})
    r1 = res["leadlag_v1_1-min, lags 1-30, from gate_model->mkt"]
    r2 = res["leadlag_v1_1-min, lags 1-30, from gate_mkt->model"]
    print("v1 lag coefficients model->mkt (lags 1,2,3,5,10,20,30):", [round(float(r1["beta"][k - 1]), 4) for k in (1, 2, 3, 5, 10, 20, 30)])
    print("v1 lag coefficients mkt->model (lags 1,2,3,5,10,20,30):", [round(float(r2["beta"][k - 1]), 4) for k in (1, 2, 3, 5, 10, 20, 30)])
    l0 = {mv: lag0(D, col, usable, boot=a.boot) for mv, col in MODELS.items()}
    print("contemporaneous slopes (lag 0):", {mv: {k: (round(v[0], 4), tuple(round(z, 4) for z in v[1])) for k, v in x.items()} for mv, x in l0.items()})
    res["lag0"] = l0
    gr = pd.concat([gap_regression(D, col, usable, boot=a.boot).assign(model=mv) for mv, col in MODELS.items()])
    print("\nerror-correction regression on the gap P - pm (market fixed effects, block bootstrap):")
    print(gr.round(3).to_string(index=False))
    res["gap"] = gr
    res["granger"] = g

    print("\n=== (3) activity in the count window (gate -> end of count) ===")
    at_ = activity_table(D)
    x = at_.copy()
    print(x.drop(columns=["slug", "first_trade", "last_trade"]).round(1).to_string(index=False))
    res["activity"] = at_
    n_thin = int(at_.thin.sum())
    print(f"thin (< {MIN_TRADES} taker trades in the window): {n_thin} of {len(at_)}:", list(at_[at_.thin].race))
    ctv1 = ct[(ct.model == "v1") & ct.lead_settle_min.notna()].merge(at_[["race", "thin", "trades", "trades_stats", "trades_in_lead_window"]], on="race")
    print("median lead, all:", ctv1.lead_settle_min.median(), "| not thin:", ctv1[~ctv1.thin].lead_settle_min.median(), "n", int((~ctv1.thin).sum()),
          "| thin:", ctv1[ctv1.thin].lead_settle_min.median(), "n", int(ctv1.thin.sum()))
    ctv1 = ctv1.merge(at_[["race", "usd_taker"]], on="race", how="left")
    for lab, m in (("taker volume >= $5,000 in the window", ctv1.usd_taker >= 5000), ("taker volume < $1,000", ctv1.usd_taker < 1000),
                   (">= 25 taker trades", ctv1.trades >= 25), ("< 25 taker trades", ctv1.trades < 25)):
        print(f"median lead, {lab}: {ctv1[m].lead_settle_min.median():.0f} min over {int(m.sum())} markets")
    w = ctv1.dropna(subset=["usd_taker"]).sort_values("lead_settle_min")
    cum = w.usd_taker.cumsum() / w.usd_taker.sum()
    print("taker-$-weighted median lead:", float(w.lead_settle_min[cum >= .5].iloc[0]),
          "| taker $ in window, median per contested market:", round(float(at_.usd_taker.median())), "trades median:", float(at_.trades.median()),
          "| markets with < $1,000:", int((at_.usd_taker < 1000).sum()), "of", len(at_), "| price-change minutes median:", float(at_.min_with_change.median()),
          "of", int(at_.minutes.iloc[0]))
    if "trades_in_lead_window" in ctv1:
        z = ctv1[ctv1.trades_in_lead_window.notna()]
        print("markets with zero taker trades during their lead window:", list(z[z.trades_in_lead_window == 0].race))
        print("median lead among markets with >=1 trade inside the lead window:", z[z.trades_in_lead_window > 0].lead_settle_min.median(),
              "n", int((z.trades_in_lead_window > 0).sum()))
    stl = stale_table(D)
    print(stl.round(2).to_string(index=False))
    res["stale"] = stl

    print("\n=== (4) contract conditions (Polymarket, gamma) ===")
    ctr = contract_table(D)
    res["contract"] = ctr
    if "closedTime" in ctr:
        for _, r in ctr.iterrows():
            cl_ = pd.Timestamp(r.get("closedTime"), tz="UTC") if r.get("closedTime") else None
            hrs = (cl_ - D["close"]).total_seconds() / 3600 if cl_ is not None else float("nan")
            print(f"{r['race']:18s} closed {str(r.get('closedTime'))[:16]}Z = {hrs:6.1f} h after poll close  planned end {str(r.get('endDate'))[:10]}  "
                  f"source field '{r.get('resolutionSource')}'  vol ${r.get('volume'):,.0f}")
    bd = band_distance(D)
    x1 = ct[(ct.model == "v1") & ct.lead_settle_min.notna()][["race", "lead_settle_min"]]
    bd = bd.merge(x1, on="race", how="left")
    print("\ndistance of the final result to the nearest band edge (seats) vs the lead:")
    print(bd.to_string(index=False))
    z = bd.dropna(subset=["lead_settle_min"])
    print("Spearman(dist to edge, lead) over", len(z), "band markets:", round(float(z[["dist_to_edge", "lead_settle_min"]].corr(method="spearman").iloc[0, 1]), 2))
    res["band_distance"] = bd
    eon = end_of_night(D)
    n = eon["nat"]
    print("\nend of night (last published frame):", n)
    print(f"non-Maverick divisions (Labor-v-Coalition count is the final pair) with a Labor/Coalition winner: {eon['divisions']}, mean counted {eon['mean_counted']:.3f}")
    r_ = eon["rows"]
    print(f"final share minus share counted at end of night: vote-weighted {100 * eon['drift_w']:+.2f} pt, mean {100 * eon['drift_mean']:+.2f} pt, "
          f"median {100 * r_.drift_vs_counted.median():+.2f} pt; divisions drifting to Coalition {eon['drift_toward_lnc']}, to Labor {eon['drift_toward_alp']}")
    print(f"  among divisions >= 95% counted at end of night: {int((r_.counted >= .95).sum())}, mean drift {100 * r_[r_.counted >= .95].drift_vs_counted.mean():+.2f} pt; "
          f"< 95% counted: {int((r_.counted < .95).sum())}, mean drift {100 * r_[r_.counted < .95].drift_vs_counted.mean():+.2f} pt")
    from math import comb
    k, nn = eon["drift_toward_lnc"], eon["drift_toward_lnc"] + eon["drift_toward_alp"]
    print(f"  sign test (drift to Coalition vs Labor): {k} of {nn}, two-sided p = {min(1.0, 2 * sum(comb(nn, i) for i in range(max(k, nn - k), nn + 1)) / 2 ** nn):.4f}")
    mv = [(n_, w_) for n_, w_ in zip(D["units"]["names"], D["units"]["winner"]) if n_ in MAVERICK]
    print("  Maverick divisions' final winners:", {p: sum(1 for _, w_ in mv if w_ == p) for p in sorted({w_ for _, w_ in mv})})
    print(f"model projection minus final share: vote-weighted {100 * eon['err_proj_w']:+.2f} pt, MAE {100 * eon['err_proj_mae']:.2f} pt")
    print("divisions whose end-of-night LEADER lost the final count:", [(f['div'], f['winner'], round(f['a_end'], 3), round(f['final'], 3)) for f in eon["flips_counted"]])
    print("divisions whose model-projected winner was wrong:", [(f['div'], f['winner'], round(f['proj_end'], 3), round(f['final'], 3)) for f in eon["flips_proj"]])
    res["eon"] = eon
    be = band_end_table(D)
    print("\nlast-frame probability of the eventual outcome per contested market (v1 / v2 / mix / Polymarket):")
    print(be.round(3).to_string(index=False))
    print("Brier at end of night:", {k: round(float(((1 - be[k]) ** 2).mean()), 4) for k in ("P_v1", "P_v2", "P_mix", "pm")})
    res["bands_end"] = be

    print("\n=== (5) $10k: signal price vs model limit ===")
    inv = invest_table(D)
    res["invest"] = inv
    print({k: v for k, v in inv["ml"].items()}, "\nsignal price:", {k: v for k, v in inv["sp"].items() if k != "bets_path"})
    print(pd.DataFrame(inv["sp"]["bets_path"]).to_string(index=False))
    sf = signal_fills(D)
    print("\nreplication of signal-price fills on the contested markets (first signal per market):")
    print(sf.round(3).to_string(index=False))
    s_ = sf.dropna(subset=["signal"])
    print(f"signals {len(s_)}; with >= 1 fill at or better than the signal price: {int((s_.fills > 0).sum())}; no taker trade on our side in the window: "
          f"{int((s_.trades_our_side == 0).sum())}; trades on our side but all at a worse price: {int(((s_.trades_our_side > 0) & (s_.fills == 0)).sum())}")
    res["signal_fills"] = sf
    t = inv["table"]
    print(t.round(2).to_string(index=False))
    tot = t.bankroll_pnl.sum()
    print(f"sum bankroll_pnl {tot:.2f}; top1 {t.bankroll_pnl.iloc[0] / tot:.2f} top3 {t.bankroll_pnl.iloc[:3].sum() / tot:.2f} top5 {t.bankroll_pnl.iloc[:5].sum() / tot:.2f}")
    sg = t[t.market.str.contains("norwegian")]
    if len(sg):
        print("Greens 4+ market (slug 'norwegian-election-328'): stake", float(sg.bankroll_stake.iloc[0]), "pnl", float(sg.bankroll_pnl.iloc[0]))
    markdown_tables(D, ct, at_, stl, bd, ctr)
    if CACHE.exists():
        pd.to_pickle(res, CACHE / "au_lead_results.pkl")


if __name__ == "__main__":
    main()
