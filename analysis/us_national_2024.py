"""US 2024 retrospective: does sharing the early states' swing (brunder.us_national) beat the per-state model and match
Polymarket on the slow contested states? RETROSPECTIVE, 2024 is the designated TEST cycle: nothing here is tuned on it.

  BRUNDER_DATA=~/us2024_data uv run python analysis/us_national_2024.py

Inputs (all from the 2024 replay, worktree ubiu-us2024; docs/us2024_replay.md): the per-state count-only (v1, no poll
prior) and poll-prior (v3) model paths at every timestamped ENR observation ($BRUNDER_DATA/processed/us2024/<st>.pkl),
Polymarket 1-minute YES (= Trump) prices, certified results, the 538 poll prior. Model: brunder.us_national with its 2026
structural prior (c 1.34, region 0.41, state 2.03 points; Huber k from the 2018/2020 tuning), races = the states'
presidential contests, count signal y = v1 share − poll prior, v = v1 sd², known at t − 60 s like the replay's signal.
For each state the pooled posterior (its own count + the national / region / state factors from every other state's count)
gives P(Harris wins).

Scores per state: time (UTC) from which the winner's probability stays >= 0.9 to the market's close (night.evaluate's
definition) for the national model, the per-state v3 model, the per-state v1 model and Polymarket; the lead of each over
Polymarket in minutes (positive = earlier); and the projected-margin error (points of two-party margin vs certified) at the
first observation with >= 5 / 25 / 50 % of the expected vote counted.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us, us_national as N
from brunder.paths import PROCESSED, RAW

ROOT = Path(__file__).resolve().parents[1]
REPLAY_SRC = Path(os.environ.get("US2024_SRC", ROOT.parent / "ubiu-us2024" / "src" / "brunder" / "us2024_replay.py"))
OUT = ROOT / "results" / "us_national_2024"
PM = RAW / "polymarket_us2024"
CACHE = PROCESSED / "us2024"
STATE_CSV = RAW / "us" / "medsl_state" / "1976-2024-president.csv"
GRID_START = pd.Timestamp("2024-11-05 22:00", tz="UTC")
LAG = pd.Timedelta(seconds=60)
LEVELS = (0.05, 0.25, 0.50)


def replay_module():
    """brunder.us2024_replay from the replay worktree, loaded as a member of this worktree's brunder package."""
    spec = importlib.util.spec_from_file_location("brunder.us2024_replay", REPLAY_SRC)
    m = importlib.util.module_from_spec(spec)
    sys.modules["brunder.us2024_replay"] = m
    spec.loader.exec_module(m)
    return m


def asof(path: pd.DataFrame, grid: pd.DatetimeIndex, cols=("share", "sd", "counted", "P")) -> pd.DataFrame:
    g = pd.DataFrame({"t": grid, "known": grid - LAG})
    m = pd.merge_asof(g, path.sort_values("t").rename(columns={"t": "obs_t"}), left_on="known", right_on="obs_t",
                      direction="backward")
    return m[["t", *cols]]


def settle(t: pd.Series, ok: np.ndarray):
    """First time after which `ok` holds to the end of the series; None if never / not at the end."""
    ok = np.asarray(ok, bool)
    if not len(ok) or not ok[-1]:
        return None
    bad = np.flatnonzero(~ok)
    return t.iloc[0 if not len(bad) else bad[-1] + 1]


def national_paths(rep: dict, prior: dict, p: N.Params, grid: pd.DatetimeIndex, states: list[str]) -> dict:
    """Pooled posterior share / sd of every state on the grid. rep[st]['v1'] is the count-only path."""
    A = {s: asof(rep[s]["v1"], grid) for s in states if s in rep}
    tmpl = [N.Race(key=f"{s}-pres", office="sen", state=s, prior=prior[s][0], prior_sd=prior[s][1]) for s in states]
    F = N.Factors(tmpl, p)
    out = {s: np.full((len(grid), 2), np.nan) for s in states}
    fac = np.zeros((len(grid), 3))
    for i in range(len(grid)):
        races = []
        for s, r in zip(states, tmpl):
            a = A.get(s)
            if a is not None and a["counted"].iat[i] > 0 and np.isfinite(a["share"].iat[i]):
                races.append(replace(r, y=float(a["share"].iat[i] - r.prior), v=float(a["sd"].iat[i]) ** 2,
                                     counted=float(a["counted"].iat[i])))
            else:
                races.append(r)
        w = N.robust_weights(races, F, p)
        eff = [replace(r, v=((F.eps2[j] + N.measurement_var(r, p)) / w[j] - F.eps2[j]) / p.meas_inflate)
               if N.informs(r, p) else r for j, r in enumerate(races)]
        post = N.posterior(eff, F, p)
        m = N.race_marginals(eff, F, post, p)
        for s in states:
            out[s][i] = (m.loc[f"{s}-pres", "share"], m.loc[f"{s}-pres", "sd"])
        fac[i] = (post.mean[0], np.sqrt(post.cov[0, 0]), sum(N.informs(r, p) for r in eff))
    return {"state": out, "factors": fac}


def main() -> None:
    r = replay_module()
    OUT.mkdir(parents=True, exist_ok=True)
    t = pd.read_json(ROOT / "results" / "us_national_backtest" / "tuned.json", typ="series")
    p = replace(N.Params(), huber_k=float(t.get("huber_k", 2.0)), meas_inflate=float(t["meas_inflate"]),
                sd_common=float(t["sd_common"]))
    markets = {m["state"]: m for m in json.loads((PM / "markets.json").read_text())}
    c24 = r.certified_state(STATE_CSV, 2024)
    c20 = r.certified_state(STATE_CSV, 2020)
    final = {s: c24[s][0] / (c24[s][0] + c24[s][1]) for s in c24 if s in r.STATE_NAMES}
    rep = {f.stem: pickle.loads(f.read_bytes()) for f in CACHE.glob("*.pkl")}
    states = sorted(s for s in r.STATE_NAMES if s != "dc")
    prior = {}
    for s in states:
        pol = (rep.get(s) or {}).get("poll")
        if pol:
            prior[s] = (pol["avg"], max(pol["sd"], 0.01))
        else:
            d, rr = c20[s]
            prior[s] = (d / (d + rr), p.sd_unpolled)
    end = max(pd.Timestamp(m["closed_time"]) for m in markets.values() if m["state"] != "us")
    # 1-minute grid like the replay's (a one-minute dip of a market below 0.9 moves its settle time); coarser after 24 h
    grid = pd.date_range(GRID_START, pd.Timestamp("2024-11-07 00:00", tz="UTC"), freq="1min").append(
        pd.date_range(pd.Timestamp("2024-11-07 00:10", tz="UTC"), end, freq="10min"))
    pres = N.Params(sd_common=0.0142, sd_region=0.0057, sd_state=0.0129, huber_k=p.huber_k, meas_inflate=p.meas_inflate)
    variants = {"structure_2026": p, "presidential_2000_2020": pres, "no_huber": replace(p, huber_k=0.0)}
    allrows, alllev = [], []
    for name, pv in variants.items():
        df, lev, nat, hr = score_variant(r, rep, prior, pv, grid, states, markets, final)
        hr.assign(variant=name).to_csv(OUT / f"hourly_margin_error_{name}.csv", index=False)
        df.assign(variant=name).to_csv(OUT / f"time_to_0.9_{name}.csv", index=False)
        lev.assign(variant=name).to_csv(OUT / f"margin_error_{name}.csv", index=False)
        pd.DataFrame({"t": grid, "c_mean_pts": 100 * nat["factors"][:, 0], "c_sd_pts": 100 * nat["factors"][:, 1],
                      "n_informing": nat["factors"][:, 2]}).to_csv(OUT / f"national_factor_{name}.csv", index=False)
        allrows.append(df.assign(variant=name))
        alllev.append(lev.assign(variant=name))
        report(name, df, lev, hr)
    pd.concat(allrows).to_csv(OUT / "time_to_0.9.csv", index=False)
    pd.concat(alllev).to_csv(OUT / "margin_error.csv", index=False)


def report(name: str, df: pd.DataFrame, lev: pd.DataFrame, hr: pd.DataFrame) -> None:
    pd.set_option("display.width", 220)
    print(f"\n===== {name}")
    show = df[df["contested"]].copy()
    for c in ("nat", "v3", "v1", "pm", "gate"):
        show[c] = pd.to_datetime(show[c]).dt.strftime("%m-%d %H:%M")
    print(show[["state", "winner", "pm_at_gate", "gate", "nat", "v3", "v1", "pm", "lead_nat", "lead_v3", "lead_v1"]]
          .to_string(index=False))
    print("median lead (min) vs Polymarket, contested states:",
          {c: float(df[df['contested']][f'lead_{c}'].median()) for c in ('nat', 'v3', 'v1')})
    c = lev[lev["state"].isin(df[df["contested"]]["state"])]
    print("median lead with the US2024 gate rule (model time >= 1 % counted):",
          {c: float(df[df['contested']][f'glead_{c}'].median()) for c in ('nat', 'v3', 'v1')},
          "| all scored states with a market:", {c: float(df[f'glead_{c}'].median()) for c in ('nat', 'v3', 'v1')})
    cs = hr[hr["state"].isin(df[df["contested"]]["state"])]
    print("median |margin error| (points) by UTC hour of Nov 6, contested states (counted = median v1 counted)")
    print(cs.groupby("hour_utc").agg(counted=("counted", "median"), prior=("err_prior", lambda x: x.abs().median()),
                                     v3=("err_v3", lambda x: x.abs().median()),
                                     nat=("err_nat", lambda x: x.abs().median())).round(2).T.to_string())
    print("median |margin error| (points) at x% counted, contested states")
    print(c.groupby("level")[["err_nat", "err_v3", "err_v1"]].agg(lambda x: float(np.median(np.abs(x)))).round(2))


def score_variant(r, rep, prior, p, grid, states, markets, final):
    nat = national_paths(rep, prior, p, grid, states)
    rows, lv, hr = [], [], []
    for s in states:
        if s not in rep or s not in markets or s not in final:
            continue
        mk = markets[s]
        close = pd.Timestamp(mk["closed_time"])
        win_dem = final[s] > 0.5
        px = pd.read_parquet(PM / "prices" / f"{s}.parquet")
        pmk = r.price_on_grid(px, grid)                        # P(Trump) at t
        g = pd.DataFrame({"t": grid})
        sh, sd = nat["state"][s][:, 0], nat["state"][s][:, 1]
        g["nat"] = norm.cdf((sh - 0.5) / sd)                    # P(Harris)
        v3, v1 = asof(rep[s]["v3"], grid), asof(rep[s]["v1"], grid)
        pre3, pre1 = rep[s]["v3_pre"], rep[s]["v1_pre"]
        g["v3"] = v3["P"].fillna(pre3["P"])
        g["v1"] = v1["P"].fillna(pre1["P"])
        g["pm"] = 1 - pmk                                       # P(Harris)
        g["counted"] = v1["counted"].fillna(0.0)
        g_counted = g["counted"].to_numpy()
        g = g[(g["t"] <= close) & g["pm"].notna()]
        win = (lambda c: g[c]) if win_dem else (lambda c: 1 - g[c])
        res = {"state": s, "winner": "Harris" if win_dem else "Trump", "close": close}
        for c in ("nat", "v3", "v1", "pm"):
            res[c] = settle(g["t"], (win(c) >= 0.9).to_numpy())
        gate = g.loc[g["counted"] >= 0.01, "t"]
        gate = gate.iloc[0] if len(gate) else None
        pgate = None if gate is None else float(win("pm")[g["t"] == gate].iloc[0])
        res["gate"], res["pm_at_gate"] = gate, pgate
        res["contested"] = pgate is not None and pgate < 0.9
        for c in ("nat", "v3", "v1"):            # US2024 rule: a model time before the 1 % gate counts from the gate
            res[f"gated_{c}"] = None if res[c] is None or gate is None else max(res[c], gate)
            res[f"glead_{c}"] = None if res[f"gated_{c}"] is None or res["pm"] is None else \
                round((res["pm"] - res[f"gated_{c}"]).total_seconds() / 60)
        for c in ("nat", "v3", "v1"):
            res[f"lead_{c}"] = None if res[c] is None or res["pm"] is None else round((res["pm"] - res[c]).total_seconds() / 60)
        rows.append(res)
        fm = 2 * final[s] - 1
        v3s = asof(rep[s]["v3"], grid, ("share",))["share"].fillna(rep[s]["v3_pre"]["share"]).to_numpy()
        for h in range(0, 9):
            th = pd.Timestamp("2024-11-06 00:00", tz="UTC") + pd.Timedelta(hours=h)
            i = min(int(np.searchsorted(grid.values, th.to_datetime64())), len(grid) - 1)
            hr.append({"state": s, "hour_utc": h, "counted": float(g_counted[i]), "err_prior": 100 * ((2 * prior[s][0] - 1) - fm),
                       "err_nat": 100 * ((2 * sh[i] - 1) - fm), "err_v3": 100 * ((2 * v3s[i] - 1) - fm)})
        for x in LEVELS:
            b = rep[s]["v1"]
            b = b[b["counted"] >= x]
            if b.empty:
                continue
            t0 = b["t"].iloc[0]
            i = int(np.searchsorted(grid.values, (t0 + LAG).to_datetime64()))
            i = min(i, len(grid) - 1)
            v3p = rep[s]["v3"]
            v3r = v3p[v3p["t"] <= t0].iloc[-1]
            lv.append({"state": s, "level": x, "t": t0, "counted": float(b["counted"].iloc[0]),
                       "err_nat": 100 * ((2 * sh[i] - 1) - fm), "err_v3": 100 * ((2 * v3r["share"] - 1) - fm),
                       "err_v1": 100 * ((2 * b["share"].iloc[0] - 1) - fm)})
    return pd.DataFrame(rows), pd.DataFrame(lv), nat, pd.DataFrame(hr)


if __name__ == "__main__":
    main()
