"""Election-night edge engine: state-level nowcast vs Polymarket, live or replayed.

One engine, two clocks:
  live    every `interval` s: TSE state files → statecast → order books (book-walk cost incl. fee)
          → edge per market; logged to data/night/<config id>/ticks.jsonl. Paper only.
  replay  a past count rebuilt from BU-web section receipts (bweb) plus a publication lag, and
          Polymarket price history (cost = price + half-spread + fee): the same ticks, evaluated
          against the resolved outcome — model-vs-market lead time and follow-the-model P&L.

Config (configs/*.toml): [election] race "share" (runoff, win > ½) or "margin" (plurality, A − B > 0),
cand_a / cand_b; [prior] where state priors come from; [model] statecast parameters;
[markets.national] / [markets.states.<uf>] Polymarket market slugs whose YES = "A wins";
[trade] min_edge, stakes, half_spread (replay).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import _http, bweb, live, nowcast, polymarket, statecast, tse_live, tse_open
from .paths import DATA, POLYMARKET, PROCESSED

NIGHT = DATA / "night"


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_id"] = Path(path).stem
    return cfg


# ------------------------------------------------------------------ priors and partials

def _numerator(votes: pd.DataFrame, cand_a: str, cand_b: str | None, race: str) -> pd.Series:
    a = votes.get(cand_a, 0)
    return a - votes.get(cand_b, 0) if race == "margin" else a


def prior(cfg: dict) -> pd.DataFrame:
    """uf, s0, V from a finished election. [prior] kind = "open" (year, round) or "live" (ele)."""
    P, E = cfg["prior"], cfg["election"]
    if P["kind"] == "open":
        d = tse_open.president_by_municipality(P["year"])
        d = d[d["round"] == P["round"]]
    else:
        v = pd.read_parquet(PROCESSED / E["cycle"] / f"e{P['ele']}_c1" / "votes.parquet")
        d = v[v["level"] == "mu"]
    g = d.pivot_table(index="uf", columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    a, b = P.get("cand_a", E["cand_a"]), P.get("cand_b", E.get("cand_b"))
    denom = g[a] + g[b] if E["race"] == "share" and b is not None else g.sum(axis=1)
    return pd.DataFrame({"s0": _numerator(g, a, b, E["race"]) / denom, "V": denom}).reset_index()


def params(cfg: dict) -> statecast.Params:
    M = cfg.get("model", {})
    thr = 0.5 if cfg["election"]["race"] == "share" else 0.0
    keys = ("kappa", "sigma_swing", "sigma_within", "sigma_nat", "kappa_nat", "tau")
    return statecast.Params(**{k: M[k] for k in keys if k in M}, threshold=thr)


def live_partials(raws: dict[str, bytes], cfg: dict) -> pd.DataFrame:
    E = cfg["election"]
    rows = []
    for uf, raw in raws.items():
        if uf == "br" or raw is None:
            continue
        v = pd.DataFrame(tse_live.parse_u(json.loads(raw), uf))
        if v.empty:
            continue
        by = v.set_index("cand_n")["votes"]
        u = v.iloc[0]
        n = by.get(E["cand_a"], 0) + by.get(E["cand_b"], 0) if E["race"] == "share" and E.get("cand_b") else u["votes_valid"]
        rows.append({"uf": uf, "n": float(n), "a": float(_numerator(by, E["cand_a"], E.get("cand_b"), E["race"])),
                     "f": u["sections_counted"] / u["sections_total"] if u["sections_total"] else 0.0})
    return pd.DataFrame(rows)


def replay_partials(sec: pd.DataFrame, t: pd.Timestamp, totals: pd.Series, cfg: dict) -> pd.DataFrame:
    s = sec[sec["visible"] <= t].groupby("uf").agg(n=("n", "sum"), a=("a", "sum"), k=("n", "size"))
    s["f"] = s["k"] / totals.reindex(s.index)
    return s.drop(columns="k").reset_index()


# municipal model (brunder.nowcast): top-two share A / (A + B) per municipality; a plurality race
# between two candidates is the same as this share being above ½.

def prior_mun(cfg: dict) -> pd.DataFrame:
    P, E = cfg["prior"], cfg["election"]
    if P["kind"] == "open":
        d = tse_open.president_by_municipality(P["year"])
        d = d[d["round"] == P["round"]]
    else:
        v = pd.read_parquet(PROCESSED / E["cycle"] / f"e{P['ele']}_c1" / "votes.parquet")
        d = v[v["level"] == "mu"]
    a, b = P.get("cand_a", E["cand_a"]), P.get("cand_b", E["cand_b"])
    g = d.pivot_table(index=["uf", "cd_mun"], columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    two = g[a] + g[b]
    out = pd.DataFrame({"s0": g[a] / two, "V0": two}).reset_index()
    return out[out["V0"] > 0]


def replay_partials_mun(sec: pd.DataFrame, t: pd.Timestamp, totals: pd.Series) -> pd.DataFrame:
    s = sec[sec["visible"] <= t].groupby(["uf", "cd_mun"]).agg(n=("n", "sum"), a=("a", "sum"), k=("n", "size"))
    s["f"] = s["k"] / totals.reindex(s.index)
    return s.drop(columns="k").reset_index()


def model_probs_mun(partials: pd.DataFrame, pri: pd.DataFrame, p: nowcast.Params, scopes: list[str]) -> dict[str, float]:
    probs: dict[str, float] = {}
    for scope in scopes:
        r = nowcast.nowcast(partials, pri, p, uf=None if scope == "br" else scope)
        probs[scope], probs[f"_f_{scope}"] = r["p_win"], r["counted_frac"]
        if scope == "br":
            probs["_share"], probs["_sd"], probs["_counted"] = r["share_hat"], r["share_sd"], r["counted_frac"]
    probs.setdefault("_share", np.nan)
    probs.setdefault("_sd", np.nan)
    probs.setdefault("_counted", np.nan)
    return probs


def params_mun(cfg: dict) -> nowcast.Params:
    M = cfg.get("model", {})
    keys = ("kappa", "rho", "tau", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf")
    return nowcast.Params(**{k: M[k] for k in keys if k in M})


# ------------------------------------------------------------------ edges

def _markets(cfg: dict) -> list[dict]:
    out = []
    if "national" in cfg.get("markets", {}):
        out.append({"scope": "br", **cfg["markets"]["national"]})
    for uf, m in cfg.get("markets", {}).get("states", {}).items():
        out.append({"scope": uf.lower(), **m})
    return out


def model_probs(partials: pd.DataFrame, pri: pd.DataFrame, p: statecast.Params) -> dict[str, float]:
    st, _ = statecast.project(partials, pri, p)
    probs = dict(zip(st["uf"], st["p_win"].astype(float)))
    probs.update({f"_f_{u}": float(f) for u, f in zip(st["uf"], st["f"])})
    nat = statecast.nowcast(partials, pri, p)
    probs["br"] = nat["p_win"]
    probs["_f_br"] = nat["counted_frac"]
    probs["_share"], probs["_sd"], probs["_counted"] = nat["share"], nat["sd"], nat["counted_frac"]
    return probs


def edges(P: float, yes_cost: float, no_cost: float) -> dict:
    """Expected profit per share of buying each side at its all-in cost, under model probability P."""
    return {"P": P, "yes_cost": yes_cost, "no_cost": no_cost, "edge_yes": P - yes_cost, "edge_no": (1 - P) - no_cost}


# ------------------------------------------------------------------ live

async def run_live(cfg_path: Path, interval: float = 30.0) -> None:
    cfg = load_config(cfg_path)
    E, T = cfg["election"], cfg.get("trade", {})
    pri, p = prior(cfg), params(cfg)
    u = tse_live._urls(E["cycle"], E["ele"], E.get("cargo", 1))
    out = NIGHT / cfg["_id"] / "ticks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    async with _http.aclient() as client:
        cm = json.loads(await _http.aget_bytes(client, u["cm"]))
        ufs = [a["cd"].lower() for a in cm["abr"]]
        mk = []
        for m in _markets(cfg):
            ev = polymarket.event(m["event"])
            row = polymarket.markets(ev).set_index("slug").loc[m["market"]]
            fee = (ev["markets"][0].get("feeSchedule") or {}).get("rate", 0) if ev["markets"][0].get("feesEnabled") else 0
            mk.append({**m, "yes_token": row["yes_token"], "fee": float(fee or 0)})
        while True:
            t0 = datetime.now(timezone.utc)
            try:
                raws = dict(zip(ufs, await asyncio.gather(*(_http.aget_bytes(client, u["u"](x)) for x in ufs),
                                                          return_exceptions=True)))
                raws = {k: v for k, v in raws.items() if isinstance(v, bytes)}
                part = live_partials(raws, cfg)
                probs = model_probs(part, pri, p)
                books = await live.fetch_books(client, [m["yes_token"] for m in mk])
                lines = []
                for m in mk:
                    bk = books.get(m["yes_token"])
                    if not bk or m["scope"] not in probs:
                        continue
                    stake = T.get("stakes", [100])[0]
                    y, n = live.walk(bk, True, stake, m["fee"]), live.walk(bk, False, stake, m["fee"])
                    e = edges(probs[m["scope"]], y["avg_cost"], n["avg_cost"])
                    rec = {"t": t0.isoformat(), "scope": m["scope"], "market": m["market"], **e,
                           "yes_fill": y["filled_fraction"], "no_fill": n["filled_fraction"],
                           "counted": probs["_counted"], "scope_counted": probs[f"_f_{m['scope']}"],
                           "share": probs["_share"], "share_sd": probs["_sd"]}
                    with out.open("a") as f:
                        f.write(json.dumps(rec, default=float) + "\n")
                    best = max(e["edge_yes"], e["edge_no"])
                    flag = "  <<" if best >= T.get("min_edge", 0.03) and probs[f"_f_{m['scope']}"] >= T.get("min_counted", 0.01) else ""
                    lines.append(f"{m['scope']:>3} P={e['P']:.3f} yes@{e['yes_cost']:.3f} no@{e['no_cost']:.3f} "
                                 f"edge {e['edge_yes']:+.3f}/{e['edge_no']:+.3f}{flag}")
                print(f"{t0:%H:%M:%S} counted {probs['_counted']:.1%} share {probs['_share']:.4f}±{probs['_sd']:.4f}", flush=True)
                for line in lines:
                    print("   " + line, flush=True)
            except Exception as ex:                                   # never stop on election night
                print(f"{t0:%H:%M:%S} tick error, continuing: {type(ex).__name__}: {ex}", flush=True)
            await asyncio.sleep(max(0.0, interval - (datetime.now(timezone.utc) - t0).total_seconds()))


# ------------------------------------------------------------------ replay

def calibrate_lag(sec: pd.DataFrame, snapshot: pd.DataFrame, tz_offset: str = "3h",
                  lags=tuple(range(0, 31))) -> pd.DataFrame:
    """Receipt → publication lag, from an archived state cross-section.
    snapshot: uf, totalized_utc (that state file's totalization time), sections_counted.
    For each lag L (minutes), compare sections received ≤ totalized − L with sections_counted."""
    rec = (sec["received"] + pd.Timedelta(tz_offset)).dt.tz_localize("UTC")
    rows = []
    for L in lags:
        err = 0
        for r in snapshot.itertuples():
            k = int(((sec["uf"] == r.uf) & (rec <= r.totalized_utc - pd.Timedelta(minutes=L))).sum())
            err += abs(k - r.sections_counted)
        rows.append({"lag_min": L, "abs_section_error": err})
    return pd.DataFrame(rows)


def replay(cfg_path: Path, lag: str = "0min", step: str = "1min", tz_offset: str = "3h",
           overrides: dict | None = None, scopes: set[str] | None = None, save: bool = True) -> pd.DataFrame:
    """Rebuild the count from BU-web receipts and evaluate model vs market for every configured market.
    tz_offset converts DT_BU_RECEBIDO (Brasília time) to UTC; lag delays visibility (receipt → site).
    overrides replace statecast parameters (calibration grids); scopes restricts the markets."""
    cfg = load_config(cfg_path)
    E, R, T = cfg["election"], cfg["replay"], cfg.get("trade", {})
    municipal = cfg.get("model", {}).get("kind", "state") == "municipal"
    if municipal:
        pri, p = prior_mun(cfg), params_mun(cfg)
    else:
        pri, p = prior(cfg), params(cfg)
    if overrides:
        p = dataclasses.replace(p, **overrides)
    raw = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"))
    raw["visible"] = (raw["received"] + pd.Timedelta(tz_offset) + pd.Timedelta(lag)).dt.tz_localize("UTC")
    if municipal:                                   # two-party share A / (A + B), win if > ½
        raw["n"] = raw["a"] + raw["b"]
        totals = raw.groupby(["uf", "cd_mun"]).size()
    else:
        raw["n"] = raw["a"] + raw["b"] if E["race"] == "share" and E.get("cand_b") else raw["valid"]
        raw["a"] = raw["a"] - raw["b"] if E["race"] == "margin" else raw["a"]
        totals = raw.groupby("uf").size()
    times = pd.date_range(raw["visible"].min().floor(step), raw["visible"].max().ceil(step), freq=step)
    prices = {}
    for m in _markets(cfg):
        if scopes is not None and m["scope"] not in scopes:
            continue
        px = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet")
        prices[m["scope"]] = px.set_index("time")["price"].sort_index()
    rows = []
    hs, fee = T.get("half_spread", 0.01), T.get("fee_rate", 0.0)
    for t in times:
        if municipal:
            probs = model_probs_mun(replay_partials_mun(raw, t, totals), pri, p, list(prices))
        else:
            probs = model_probs(replay_partials(raw, t, totals, cfg), pri, p)
        for scope, px in prices.items():
            q = px[:t]
            if q.empty or scope not in probs:
                continue
            pm = float(q.iloc[-1])
            yes_cost = pm + hs + fee * pm * (1 - pm)
            no_cost = (1 - pm) + hs + fee * pm * (1 - pm)
            rows.append({"t": t, "scope": scope, "price": pm, **edges(probs[scope], yes_cost, no_cost),
                         "counted": probs["_counted"], "scope_counted": probs[f"_f_{scope}"],
                         "share": probs["_share"], "share_sd": probs["_sd"]})
    out = pd.DataFrame(rows)
    if save:
        d = NIGHT / cfg["_id"]
        d.mkdir(parents=True, exist_ok=True)
        out.to_parquet(d / f"replay_lag{lag}.parquet")
    return out


def evaluate(ticks: pd.DataFrame, outcomes: dict[str, int], min_edge: float = 0.03,
             min_counted: float = 0.01, favoured_only: bool = False) -> pd.DataFrame:
    """Per market: when model and market each first stayed ≥ 0.9 on the winning side, and the return of
    a single entry at the first tick where the edge reached min_edge with at least min_counted of that
    market's sections counted (the tool trades the count, not the prior). favoured_only: only buy the
    side the model thinks will win (no long-shot buys on tail probabilities)."""
    rows = []
    for scope, g in ticks.groupby("scope"):
        y = outcomes.get(scope)
        if y is None:
            continue
        g = g.set_index("t").sort_index()
        win_P = g["P"] if y == 1 else 1 - g["P"]
        win_p = g["price"] if y == 1 else 1 - g["price"]

        def settle(s, lv=0.9):
            below = s < lv
            return s.index[s.index > below[below].index.max()][0] if below.any() and not below.iloc[-1] else (s.index[0] if not below.any() else pd.NaT)

        t_model, t_mkt = settle(win_P), settle(win_p)
        gate = g["scope_counted"] >= min_counted if "scope_counted" in g else True
        yes_ok = g["edge_yes"] >= min_edge
        no_ok = g["edge_no"] >= min_edge
        if favoured_only:
            yes_ok &= g["P"] >= 0.5
            no_ok &= g["P"] < 0.5
        sig = g[(yes_ok | no_ok) & gate]
        ret, side, t_entry = np.nan, None, pd.NaT
        if len(sig):
            r = sig.iloc[0]
            side = "YES" if yes_ok.loc[sig.index[0]] and (not no_ok.loc[sig.index[0]] or r["edge_yes"] >= r["edge_no"]) else "NO"
            cost = r["yes_cost"] if side == "YES" else r["no_cost"]
            won = (y == 1) == (side == "YES")
            ret, t_entry = (1 / cost if won else 0) - 1, sig.index[0]
        rows.append({"scope": scope, "outcome": y, "model_0.9": t_model, "market_0.9": t_mkt,
                     "lead_min": (t_mkt - t_model).total_seconds() / 60 if pd.notna(t_model) and pd.notna(t_mkt) else np.nan,
                     "entry": t_entry, "side": side, "return_per_$": ret})
    return pd.DataFrame(rows)
