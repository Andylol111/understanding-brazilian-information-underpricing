"""Who moves Polymarket prices in the 2026 Senate/governor markets: aggregate, pseudonymous wallet-flow analysis.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_wallets.py [fetch|build]   # default: both

Only Polymarket's public, unauthenticated APIs (Gamma events, data-api trades/holders/oi/activity). Wallet addresses
are pseudonymous proxy-wallet hashes; nothing here tries to link them to people or places. Raw fills are cached under
$BRUNDER_DATA/raw/polymarket/wallets (never published); the site file holds only aggregates and anonymised ranks.

Definitions (docs/us_wallets.md):
  fill rows       data-api /trades, one row per participant per fill (taker and each maker), side from that
                  participant's view. Taker-only download = aggressor orders (signed order flow).
  YES flow        YES-share change in YES-equivalent dollars: buy YES +s*p, sell YES -s*p, buy NO -s*(1-q),
                  sell NO +s*(1-q) (a NO share is a short YES share).
  realised PnL    per wallet and resolved market: cash from fills + final shares x payout (exact for a binary market:
                  splits/merges/redemptions net out because YES + NO = 1).
  seasoned        positive total realised PnL over >= 3 resolved political markets of the seasoning universe.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from brunder import _http, polymarket, us
from brunder.paths import POLYMARKET

OUT = us.ROOT_DIR / "site" / "data" / "us2026" / "hub" / "wallets.json"
CACHE = POLYMARKET / "wallets"
TODAY = pd.Timestamp.now(tz="UTC").floor("h")
CUT_2024 = pd.Timestamp("2024-10-22", tz="UTC")            # historical test window start (final 2 weeks)
END_2024 = pd.Timestamp("2024-11-05 11:00", tz="UTC")      # window end: Election Day morning, before any poll closes
BUCKETS = [(0, 100, "small"), (100, 1000, "medium"), (1000, np.inf, "large")]   # taker order size, USD
MIN_SEASON_MARKETS = 3
SERIES_DAYS = 180
PRE_VOLUME = (10_000, 3_000_000)   # seasoning markets: skip dust and the few $5M+ nomination markets (download cost)
STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
          "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
          "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
          "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
          "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
          "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"]
KEEP = ["time", "proxyWallet", "side", "asset", "price", "size", "transactionHash"]


def _r(x, nd=4):
    return None if x is None or not np.isfinite(float(x)) else round(float(x), nd)


# ------------------------------------------------------------------ pure metrics (tested)

def yes_flow(f: pd.DataFrame, yes_token: str) -> pd.Series:
    """Signed YES-equivalent dollars per fill row (see module doc)."""
    is_yes = f["asset"].astype(str) == str(yes_token)
    buy = f["side"].eq("BUY")
    p_yes = np.where(is_yes, f["price"], 1 - f["price"])
    sign = np.where(is_yes == buy, 1.0, -1.0)
    return pd.Series(sign * f["size"].to_numpy(float) * p_yes, index=f.index)


def realised_pnl(f: pd.DataFrame, payout: dict[str, float]) -> pd.Series:
    """Per-wallet PnL of a resolved market from its fills: cash + final shares x payout per token."""
    s = f["size"].astype(float)
    sign = np.where(f["side"].eq("BUY"), 1.0, -1.0)
    pay = f["asset"].astype(str).map(payout).astype(float)
    return (sign * s * (pay - f["price"].astype(float))).groupby(f["proxyWallet"]).sum()


def concentration(amounts: np.ndarray, total: float | None = None) -> dict:
    """Top-10 share and HHI (0-10,000) of holdings; `total` = outstanding supply if known, else the listed sum."""
    a = np.sort(np.asarray(amounts, float))[::-1]
    tot = max(float(total or 0), a.sum())
    if tot <= 0:
        return {"top10_share": None, "hhi": None}
    sh = a / tot
    return {"top10_share": _r(sh[:10].sum()), "hhi": _r(1e4 * (sh ** 2).sum(), 1)}


def bucket(usd: pd.Series) -> pd.Series:
    out = pd.Series("large", index=usd.index)
    for lo, hi, name in BUCKETS:
        out[(usd >= lo) & (usd < hi)] = name
    return out


def kyle_lambda(dp: np.ndarray, flow_k: np.ndarray) -> dict:
    """OLS dp = a + lambda * flow ($1k), HC1 errors. lambda in probability points (0-1 scale) per $1k net YES flow."""
    m = np.isfinite(dp) & np.isfinite(flow_k)
    if m.sum() < 30 or np.std(flow_k[m]) == 0:
        return {"lambda_per_1k": None, "t": None, "r2": None, "n": int(m.sum())}
    res = sm.OLS(dp[m], sm.add_constant(flow_k[m])).fit(cov_type="HC1")
    return {"lambda_per_1k": _r(res.params[1], 6), "t": _r(res.tvalues[1], 2), "r2": _r(res.rsquared, 3),
            "n": int(m.sum())}


def lead_lag(flow: np.ndarray, dp: np.ndarray) -> dict:
    """corr(flow_t, dp_{t+1}) (flow leads) vs corr(dp_t, flow_{t+1}) (flow follows) and contemporaneous."""
    def c(a, b):
        m = np.isfinite(a) & np.isfinite(b)
        return _r(np.corrcoef(a[m], b[m])[0, 1], 3) if m.sum() > 10 and np.std(a[m]) > 0 and np.std(b[m]) > 0 \
            else None
    return {"lead": c(flow[:-1], dp[1:]), "same": c(flow, dp), "follow": c(dp[:-1], flow[1:]), "n": int(len(dp))}


def offset_logit(y: np.ndarray, p: np.ndarray, x: np.ndarray) -> dict:
    """y ~ a + b*x + offset(logit p): does x predict the outcome beyond the market price? LR test of b against the
    intercept-only offset model (a absorbs a common, cycle-wide miss such as 2024's shift to the right);
    leave-one-out log loss of price alone, price + intercept, price + intercept + x."""
    from scipy.stats import chi2
    p = np.clip(p, 0.01, 0.99)
    off = np.log(p / (1 - p))
    X = sm.add_constant(x, has_constant="add")
    fam = sm.families.Binomial()
    full = sm.GLM(y, X, family=fam, offset=off).fit()
    base = sm.GLM(y, X[:, :1], family=fam, offset=off).fit()
    lr = 2 * (full.llf - base.llf)

    def ll(q, yi):
        q = np.clip(q, 1e-6, 1 - 1e-6)
        return -(yi * np.log(q) + (1 - yi) * np.log(1 - q))
    loo = {"price": [], "intercept": [], "flow": []}
    for i in range(len(y)):
        k = np.arange(len(y)) != i
        loo["price"].append(ll(p[i], y[i]))
        for name, cols in (("intercept", 1), ("flow", 2)):
            try:
                fi = sm.GLM(y[k], X[k, :cols], family=fam, offset=off[k]).fit()
                q = 1 / (1 + np.exp(-(X[i, :cols] @ fi.params + off[i])))
            except Exception:
                q = p[i]
            loo[name].append(ll(q, y[i]))
    return {"n": int(len(y)), "beta": _r(full.params[1], 3), "se": _r(full.bse[1], 3),
            "z": _r(full.tvalues[1], 2), "p_value": _r(full.pvalues[1], 3), "alpha": _r(full.params[0], 3),
            "lr_p": _r(chi2.sf(lr, 1), 3), "loo_logloss_with_flow": _r(np.mean(loo["flow"])),
            "loo_logloss_price_intercept": _r(np.mean(loo["intercept"])),
            "loo_logloss_price_only": _r(np.mean(loo["price"]))}


# ------------------------------------------------------------------ fetch (cached; public APIs only)

def _fills(cid: str, taker: bool) -> pd.DataFrame:
    f = CACHE / "fills" / f"{cid}{'_taker' if taker else ''}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    f.parent.mkdir(parents=True, exist_ok=True)
    df = polymarket.trades(cid, taker_only=taker, sleep=0.1)
    df = df[KEEP] if len(df) else pd.DataFrame(columns=KEEP)
    df["asset"] = df["asset"].astype(str)
    df.to_parquet(f)
    return df


def _resolved(m: dict) -> dict | None:
    """{token: payout} for a resolved binary market, else None."""
    if not m.get("closed") or not m.get("clobTokenIds") or not m.get("outcomePrices"):
        return None
    toks, px = json.loads(m["clobTokenIds"]), [float(x) for x in json.loads(m["outcomePrices"])]
    if len(toks) != 2 or sorted(px) != [0.0, 1.0]:
        return None
    return dict(zip(toks, px))


def _events(params: dict) -> list[dict]:
    out = []
    with _http.client() as c:
        for off in range(0, 5000, 100):
            r = _http.get_json(c, f"{polymarket.GAMMA}/events", {**params, "limit": 100, "offset": off})
            out += r
            if len(r) < 100:
                break
            time.sleep(0.1)
    return out


def universes() -> dict:
    """Seasoning universe U_pre (political markets resolved before the 2024 test window) and the 2024 general-election
    state markets (Democratic-side market of each Senate/governor/presidential state event)."""
    f = CACHE / "universe.json"
    if f.exists():
        return json.loads(f.read_text())
    pre = []
    for e in _events({"tag_slug": "politics", "closed": "true", "end_date_min": "2023-01-01",
                      "end_date_max": (CUT_2024 - pd.Timedelta(days=1)).strftime("%Y-%m-%d")}):
        for m in e.get("markets", []):
            pay = _resolved(m)
            end = pd.Timestamp(m.get("closedTime") or m.get("endDate") or e["endDate"])
            end = end.tz_localize("UTC") if end.tzinfo is None else end
            if pay and PRE_VOLUME[0] <= float(m.get("volume") or 0) <= PRE_VOLUME[1] and end < CUT_2024:
                pre.append({"cid": m["conditionId"], "event": e["slug"], "payout": pay,
                            "volume": float(m.get("volume") or 0)})
    gen = []
    for office, suffix in (("sen", "us-senate-election-winner"), ("gov", "governor-election-winner"),
                           ("pres", "presidential-election-winner")):
        for st in STATES + (["Washington DC"] if office == "pres" else []):
            try:
                e = polymarket.event(f"{st.lower().replace(' ', '-')}-{suffix}")
            except Exception:
                continue
            if not str(e.get("endDate", "")).startswith("2024"):
                continue
            m = next((m for m in e["markets"] if "democrat" in m["question"].lower() and _resolved(m)), None)
            if m:
                pay = _resolved(m)
                yes = json.loads(m["clobTokenIds"])[json.loads(m["outcomes"]).index("Yes")]
                gen.append({"cid": m["conditionId"], "event": e["slug"], "office": office, "state": st,
                            "payout": pay, "yes": yes, "dem_won": pay[yes], "volume": float(m.get("volume") or 0)})
    u = {"pre": pre, "gen2024": gen}
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(u))
    return u


def live_markets() -> list[dict]:
    f = CACHE / "live.json"
    if f.exists() and pd.Timestamp(f.stat().st_mtime, unit="s", tz="UTC") > TODAY - pd.Timedelta(hours=6):
        return json.loads(f.read_text())
    out = []
    for r in json.loads(us.RACES_REGISTRY.read_text())["races"]:
        pm = r.get("pm") or {}
        if not pm.get("token_id"):
            continue
        ev = polymarket.event(pm["event"])
        m = next(m for m in ev["markets"] if m["slug"] == pm["slug"])
        toks = json.loads(m["clobTokenIds"])
        out.append({"race": r["race_key"], "office": r["office"], "cid": m["conditionId"], "yes": pm["token_id"],
                    "no": next(t for t in toks if t != pm["token_id"]), "volume": float(m.get("volume") or 0),
                    "yes_is": pm.get("yes_is")})
    f.write_text(json.dumps(out))
    return out


def _holders(cid: str) -> tuple[dict, float | None]:
    with _http.client() as c:
        h = _http.get_json(c, f"{polymarket.DATA_API}/holders", {"market": cid, "limit": 500})
        oi = _http.get_json(c, f"{polymarket.DATA_API}/oi", {"market": cid})
    return ({x["token"]: np.array([float(y["amount"]) for y in x["holders"]]) for x in h},
            float(oi[0]["value"]) if oi else None)


def _first_seen(wallets: list[str]) -> pd.Series:
    """First Polymarket activity per wallet (data-api /activity ascending, limit 1); cached locally, never published."""
    f = CACHE / "first_activity.parquet"
    have = pd.read_parquet(f)["ts"] if f.exists() else pd.Series(dtype="float64")
    todo = [w for w in wallets if w not in have.index]

    def one(w):
        with _http.client() as c:
            try:
                r = _http.get_json(c, f"{polymarket.DATA_API}/activity",
                                   {"user": w, "limit": 1, "sortBy": "TIMESTAMP", "sortDirection": "ASC"})
                return w, float(r[0]["timestamp"]) if r else np.nan
            except Exception:
                return w, np.nan
    with ThreadPoolExecutor(4) as ex:
        new = dict(ex.map(one, todo))
    if new:
        have = pd.concat([have, pd.Series(new, dtype="float64")])
        have.rename("ts").to_frame().to_parquet(f)
    return have.reindex(wallets)


def fetch() -> None:
    u = universes()
    live = live_markets()
    jobs = [(m["cid"], t) for m in live for t in (False, True)] + [(m["cid"], False) for m in u["gen2024"] + u["pre"]]
    print(f"{len(jobs)} fill downloads (pre {len(u['pre'])}, gen2024 {len(u['gen2024'])}, live {len(live)})", flush=True)

    def one(j):
        try:
            n = len(_fills(*j))
            return j, n
        except Exception as e:
            print(f"fills {j}: {type(e).__name__}: {e}", flush=True)
            return j, -1
    with ThreadPoolExecutor(4) as ex:
        for k, (j, n) in enumerate(ex.map(one, jobs)):
            if k % 25 == 0:
                print(k, j[0][:10], n, flush=True)


# ------------------------------------------------------------------ build

def _pnl_table(markets: list[dict]) -> pd.DataFrame:
    rows = []
    for m in markets:
        f = CACHE / "fills" / f"{m['cid']}.parquet"
        if not f.exists():
            continue
        fl = pd.read_parquet(f)
        if len(fl):
            rows.append(realised_pnl(fl, m["payout"]).rename("pnl").reset_index().assign(cid=m["cid"]))
    return pd.concat(rows) if rows else pd.DataFrame(columns=["proxyWallet", "pnl", "cid"])


def seasoned(markets: list[dict]) -> tuple[set, dict]:
    t = _pnl_table(markets)
    g = t.groupby("proxyWallet").agg(pnl=("pnl", "sum"), n=("cid", "nunique"))
    ok = g[(g["n"] >= MIN_SEASON_MARKETS) & (g["pnl"] > 0)]
    return set(ok.index), {"markets": int(t["cid"].nunique()), "wallets_traded": int(len(g)),
                           "wallets_3plus": int((g["n"] >= MIN_SEASON_MARKETS).sum()), "seasoned": int(len(ok))}


def historical(u: dict) -> dict:
    """2024: seasoned (pre-window universe) net YES flow over 22 Oct - 5 Nov 11:00 UTC vs outcome, price as offset."""
    s_pre, s_info = seasoned(u["pre"])
    rows = []
    for m in u["gen2024"]:
        f = CACHE / "fills" / f"{m['cid']}.parquet"
        if not f.exists():
            continue
        fl = pd.read_parquet(f)
        w = fl[(fl["time"] >= CUT_2024) & (fl["time"] < END_2024)]
        last = fl[(fl["time"] >= END_2024 - pd.Timedelta(hours=24)) & (fl["time"] < END_2024)]
        if len(last) == 0 or len(w) == 0:
            continue
        py = np.where(last["asset"] == m["yes"], last["price"], 1 - last["price"])
        p = float(np.average(py, weights=last["size"]))      # YES-equivalent VWAP, final 24h before the cutoff
        fy = yes_flow(w, m["yes"])
        gross = float((w["size"] * w["price"]).sum()) / 2      # each fill appears once per side
        s = w["proxyWallet"].isin(s_pre)
        rows.append({"office": m["office"], "state": m["state"], "p": p, "dem_won": m["dem_won"],
                     "seasoned_net": float(fy[s].sum()), "seasoned_gross": float((w["size"] * w["price"])[s].sum()),
                     "gross": gross})
    d = pd.DataFrame(rows)
    d["x"] = d["seasoned_net"] / d["gross"].clip(lower=1)
    res = offset_logit(d["dem_won"].to_numpy(float), d["p"].to_numpy(float), d["x"].to_numpy(float))
    comp = d[(d["p"] > 0.05) & (d["p"] < 0.95)]
    res_c = offset_logit(comp["dem_won"].to_numpy(float), comp["p"].to_numpy(float), comp["x"].to_numpy(float)) \
        if len(comp) >= 10 else None
    agree = d[d["x"].abs() > 0.01]
    hit = float(((agree["x"] > 0) == (agree["dem_won"] > agree["p"])).mean()) if len(agree) else None
    return {"window": [CUT_2024.isoformat(), END_2024.isoformat()], "seasoning": s_info,
            "x_definition": "seasoned wallets' net YES-equivalent $ in the window / market gross $ in the window",
            "x_sd": _r(d["x"].std()), "x_mean": _r(d["x"].mean()), "logit_all": res, "logit_competitive": res_c,
            "direction_hit_rate": _r(hit, 3), "n_direction": int(len(agree)),
            "brier_price": _r(((d["p"] - d["dem_won"]) ** 2).mean()),
            "markets": [[r.office, r.state, _r(r.p, 3), int(r.dem_won), _r(r.x, 4)] for r in d.itertuples()]}


def _grid(fl: pd.DataFrame, yes: str, freq: str) -> pd.DataFrame:
    p_yes = np.where(fl["asset"] == yes, fl["price"], 1 - fl["price"])
    px = pd.Series(p_yes, index=fl["time"]).resample(freq).last().ffill()
    return px


def race_metrics(m: dict, s26: set) -> tuple[dict, list]:
    full, tk = pd.read_parquet(CACHE / "fills" / f"{m['cid']}.parquet"), \
        pd.read_parquet(CACHE / "fills" / f"{m['cid']}_taker.parquet")
    full = full[full["time"] < TODAY]
    tk = tk[tk["time"] < TODAY]
    out: dict = {"volume_total": _r(m["volume"], 0), "fills": int(len(tk))}
    if len(tk) < 20:
        return out, []
    # holders
    try:
        hold, oi = _holders(m["cid"])
        out["holders_yes"] = {**concentration(hold.get(m["yes"], []), oi), "listed": int(len(hold.get(m["yes"], [])))}
        out["holders_no"] = {**concentration(hold.get(m["no"], []), oi), "listed": int(len(hold.get(m["no"], [])))}
        out["open_interest"] = _r(oi, 0)
    except Exception as e:
        print(f"{m['race']} holders: {e}")
    # wallets
    last30 = full[full["time"] >= TODAY - pd.Timedelta(days=30)]
    out["wallets_all"] = int(full["proxyWallet"].nunique())
    out["wallets_30d"] = int(last30["proxyWallet"].nunique())
    vol30 = (last30["size"] * last30["price"]).groupby(last30["proxyWallet"]).sum()
    big = vol30[vol30 >= 100].index.tolist()
    if big:
        ts = _first_seen(big)
        age = (TODAY.timestamp() - ts) / 86400
        wv = vol30.reindex(age.index)
        ok = age.notna()
        out["age_30d"] = {"wallets": int(ok.sum()), "median_days": _r(age[ok].median(), 0),
                          "share_lt30d": _r((age[ok] < 30).mean(), 3), "share_lt90d": _r((age[ok] < 90).mean(), 3),
                          "share_ge1y": _r((age[ok] >= 365).mean(), 3),
                          "vol_share_lt30d": _r(wv[ok & (age < 30)].sum() / wv[ok].sum(), 3)}
    # taker orders, signed flow, buckets
    tk = tk.assign(flow=yes_flow(tk, m["yes"]), usd=tk["size"] * tk["price"])
    tk["absflow"] = tk["flow"].abs()
    orders = tk.groupby(["transactionHash", "proxyWallet"]).agg(time=("time", "first"), flow=("flow", "sum"),
                                                                usd=("usd", "sum"), absflow=("absflow", "sum"))
    orders["bucket"] = bucket(orders["usd"])
    out["flow"] = {b: {"net": _r(g["flow"].sum(), 0), "gross": _r(g["absflow"].sum(), 0), "orders": int(len(g))}
                   for b, g in orders.groupby("bucket")}
    full = full.assign(flow=yes_flow(full, m["yes"]))
    sf = full[full["proxyWallet"].isin(s26)]
    out["seasoned"] = {"wallets": int(sf["proxyWallet"].nunique()), "net": _r(sf["flow"].sum(), 0),
                       "gross_share": _r((sf["size"] * sf["price"]).sum() / (full["size"] * full["price"]).sum(), 3),
                       "net_30d": _r(sf.loc[sf["time"] >= TODAY - pd.Timedelta(days=30), "flow"].sum(), 0)}
    # hourly / daily grids
    res = {}
    for freq, key in (("h", "1h"), ("D", "1d")):
        px = _grid(full, m["yes"], freq)
        dp = px.diff().to_numpy()
        of = orders.set_index("time")
        fl_all = of["flow"].resample(freq).sum().reindex(px.index, fill_value=0).to_numpy() / 1e3
        fl_large = of.loc[of["bucket"] == "large", "flow"].resample(freq).sum().reindex(px.index, fill_value=0) \
            .to_numpy() / 1e3
        fl_seas = sf.set_index("time")["flow"].resample(freq).sum().reindex(px.index, fill_value=0).to_numpy() / 1e3 \
            if len(sf) else np.zeros(len(px))
        res[key] = {"lambda": kyle_lambda(dp, fl_all), "large": lead_lag(fl_large, dp),
                    "seasoned": lead_lag(fl_seas, dp)}
        if key == "1d":
            day = pd.DataFrame({"p": px, "all": fl_all, "large": fl_large, "seasoned": fl_seas}, index=px.index)
    out["impact"] = res
    # compact daily series: [date, price, net small, medium, large, seasoned ($), active wallets]
    od = orders.set_index("time")
    nb = {b: od.loc[od["bucket"] == b, "flow"].resample("D").sum() for b in ("small", "medium", "large")}
    act = full.set_index("time")["proxyWallet"].resample("D").nunique()
    day = day[day.index >= TODAY.normalize() - pd.Timedelta(days=SERIES_DAYS)]
    series = [[t.strftime("%Y-%m-%d"), _r(r.p, 3), *[int(round(nb[b].get(t, 0))) for b in ("small", "medium", "large")],
               int(round(r.seasoned * 1e3)), int(act.get(t, 0))] for t, r in day.iterrows()]
    return out, series


def _pooled(per: dict) -> dict:
    """Median and volume-weighted mean lead/follow correlations across races."""
    out = {}
    for key in ("1h", "1d"):
        for who in ("large", "seasoned"):
            rows = [(v["impact"][key][who]["lead"], v["impact"][key][who]["follow"], v["volume_total"] or 0)
                    for v in per.values() if "impact" in v and v["impact"][key][who]["lead"] is not None
                    and v["impact"][key][who]["follow"] is not None]
            if rows:
                a = np.array(rows, float)
                out[f"{who}_{key}"] = {"n_races": len(rows), "lead_median": _r(np.median(a[:, 0]), 3),
                                       "follow_median": _r(np.median(a[:, 1]), 3),
                                       "lead_vw": _r(np.average(a[:, 0], weights=a[:, 2] + 1), 3),
                                       "follow_vw": _r(np.average(a[:, 1], weights=a[:, 2] + 1), 3),
                                       "share_lead_gt_follow": _r((a[:, 0] > a[:, 1]).mean(), 3)}
    return out


def build() -> Path:
    u = universes()
    live = live_markets()
    s26, s26_info = seasoned(u["pre"] + u["gen2024"])
    per, series = {}, {}
    for m in live:
        try:
            per[m["race"]], series[m["race"]] = race_metrics(m, s26)
        except FileNotFoundError:
            print(f"{m['race']}: fills not cached")
        print(m["race"], flush=True)
    hist = historical(u)
    obj = {"built": TODAY.strftime("%Y-%m-%d %H:00Z"),
           "sources": [{"name": "Polymarket public APIs: Gamma /events; data-api /trades, /holders, /oi, /activity",
                        "url": "https://docs.polymarket.com", "licence": "public market data (no auth)"}],
           "privacy": "Aggregates only. Wallets are pseudonymous proxy-wallet hashes; no addresses, names or "
                      "per-wallet rows are published, and no attempt is made to identify or locate traders.",
           "definitions": {
               "yes": "registry YES token (Democratic side, as in markets.json)",
               "flow": "net (and gross = sum of |.|) YES-equivalent USD of taker orders; buckets by taker order size "
                       "paid (fills of one tx per wallet): small <$100, medium $100-1k, large >=$1k",
               "seasoned": f"positive total realised PnL over >= {MIN_SEASON_MARKETS} resolved political Polymarket "
                           "markets of the seasoning universe (all fills, maker and taker)",
               "holders": "top-10 share and HHI (0-10,000) of the YES/NO token over max(open interest, listed "
                          "top-500 holders' sum)",
               "age_30d": "days since first Polymarket activity, wallets with >= $100 traded in the race in the "
                          "last 30 days",
               "lambda_per_1k": "OLS price change (0-1) per $1k net taker YES flow per bar, HC1 t",
               "lead_lag": "lead = corr(flow_t, dprice_t+1); follow = corr(dprice_t, flow_t+1)",
               "series": "[date, YES price close, net small, net medium, net large, net seasoned (USD), active "
                         "wallets]"},
           "seasoning_2026": {**s26_info, "universe": "political markets resolved 2023-01 to 2024-10-21 (Gamma tag "
                                                     "politics, volume >= $10k) + 2024 Democratic-side Senate/governor/"
                                                     "presidential state markets"},
           "races": per, "series": series, "pooled": _pooled(per), "historical_2024": hist}
    OUT.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.2f} MB")
    return OUT


if __name__ == "__main__":
    for s in sys.argv[1:] or ["fetch", "build"]:
        {"fetch": fetch, "build": build}[s]()
