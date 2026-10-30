"""US 2020 retrospective (second test cycle): per-state county nowcast vs the same nowcast + national / region / state
sharing (brunder.us_national), on the New York Times / National Election Pool county feed of 2020-11-03..06
(MEDSL "Replication Data for The Blue Shift in the 2020 Election", doi:10.7910/DVN/LZXR5A, CC0; nyt_counties.tab:
county totals for Trump and Biden every few minutes, precincts reporting). RETROSPECTIVE; nothing tuned on 2020.

  BRUNDER_DATA=~/us2020_data uv run python analysis/us_national_2020.py [fetch] [replay] [score]

Per state (as the 2024 replay, brunder.us2024_replay + its frozen settings): v1 = count only on the 2016 county prior,
v3 = + the 538 poll average prior on the statewide swing. National: count-only v1 paths, prior = poll average (else 2016
share, sd 0.06), presidential structure fitted on 2000-2016 polls only (analysis/us_national_pres_structure.py without
2020), Huber/meas_inflate from the 2018/2020 Senate/House tuning (grep results/us_national_backtest/tuned.json: NB 2020
Senate/House nights were part of that tuning; the sharing structure here is the 2000-2016 presidential one). Expected vote
= 2016 two-party votes x 1.2 (2020/2016 national two-party vote ratio, assumed: turnout was the story of 2020 and the
frozen 2024 ratio 1.0 would misstate every state's counted fraction equally for v1, v3 and the national path). No
Polymarket state series exists for 2020: the comparison is v3 / v1 vs national only. Writes results/us_national_2020/.
"""
from __future__ import annotations

import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us, us_national as N
from brunder.paths import PROCESSED, RAW

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_national_2024 as M24  # noqa: E402
import us_national_pres_structure as PS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAWD = RAW / "us" / "nyt2020"
CACHE = PROCESSED / "us2020"
OUT = ROOT / "results" / "us_national_2020"
URL = "https://dataverse.harvard.edu/api/access/datafile/4749507"
CONFIG = ROOT.parent / "ubiu-us2024" / "configs" / "retro" / "us2024_pres_v3.toml"
TURNOUT = 1.2
COUNTYPRES = Path(os.environ.get("US2024_COUNTYPRES", Path.home() / "brunder-data-local" / "raw" / "us" / "medsl" / "countypres_2000-2024.csv"))
STATE_CSV = RAW / "us" / "medsl_state" / "1976-2024-president.csv"
GRID_START = pd.Timestamp("2020-11-03 23:00", tz="UTC")
GRID_END = pd.Timestamp("2020-11-07 00:00", tz="UTC")
LAG = pd.Timedelta(seconds=60)


def fetch() -> Path:
    import httpx
    RAWD.mkdir(parents=True, exist_ok=True)
    f = RAWD / "nyt_counties.tab"
    if not f.exists():
        with httpx.stream("GET", URL, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(f, "wb") as fh:
                for b in r.iter_bytes():
                    fh.write(b)
    return f


def load_counts(r) -> dict[str, pd.DataFrame]:
    d = pd.read_csv(RAWD / "nyt_counties.tab", sep="\t", dtype={"fips": str},
                    usecols=["fips", "state", "time", "trumpd", "bidenj"])
    names = {v.lower(): k for k, v in r.STATE_NAMES.items()}
    d["st"] = d["state"].str.lower().map(names)
    d = d.dropna(subset=["st"])
    d["t"] = pd.to_datetime(d["time"]).dt.tz_localize("UTC")
    d["fips"] = d["fips"].str.zfill(5)
    out = {}
    for st, g in d.groupby("st"):
        g = g.drop_duplicates(["t", "fips"], keep="last")
        out[st] = g.rename(columns={"bidenj": "Harris", "trumpd": "Trump"})[["t", "fips", "Harris", "Trump"]] \
            .astype({"Harris": float, "Trump": float}).sort_values(["t", "fips"]).reset_index(drop=True)
    return out


def polls2020() -> dict[str, dict]:
    g = PS.state_errors()
    g = g[g["cycle"] == 2020]
    return {r_.state: {"avg": float(r_.avg), "sd": float(us.poll_sd_by_n(int(r_.n))), "n_polls": int(r_.n)} for r_ in g.itertuples()}


def run_state(args) -> dict:
    st, counts, prior, poll = args
    r = M24.replay_module()
    frozen = r.load_config(CONFIG)
    frozen["count"] = {**frozen["count"], "turnout_ratio": TURNOUT}
    out = {"st": st, "poll": poll}
    for v in ("v1", "v3"):
        cfg = r.race_cfg(frozen, st, v, poll, prior)
        path, _ = r.replay(counts, cfg, prior)
        out[v], out[f"{v}_pre"] = path, r.precount(cfg, prior)
    return out


def replay_all() -> dict:
    r = M24.replay_module()
    CACHE.mkdir(parents=True, exist_ok=True)
    counts = load_counts(r)
    cp16, cp20 = r.countypres(COUNTYPRES, 2016), r.countypres(COUNTYPRES, 2020)
    c20 = r.certified_state(STATE_CSV, 2020)
    polls = polls2020()
    todo, skipped = [], {}
    for st, cnt in counts.items():
        if st == "dc":
            continue
        pri = r.county_prior(cp16, st)
        last = cnt[cnt["t"] == cnt["t"].max()]
        matched = last[last["fips"].isin(pri["fips"])]
        share = (matched["Harris"].sum() + matched["Trump"].sum()) / max((last["Harris"].sum() + last["Trump"].sum()), 1)
        d, rr = c20[st]
        gap = (matched["Harris"].sum() + matched["Trump"].sum()) / (d + rr) - 1
        if share < 0.98 or abs(gap) > 0.03 or st not in c20:
            skipped[st] = f"county codes matched {share:.1%} of the feed's votes; final feed vs certified 2020 {gap:+.1%}"
            continue
        cnt = cnt[cnt["fips"].isin(pri["fips"])]
        if not (CACHE / f"{st}.pkl").exists():
            todo.append((st, cnt, pri, polls.get(st)))
    if todo:
        with ProcessPoolExecutor(max_workers=6) as ex:
            for res in ex.map(run_state, todo):
                (CACHE / f"{res['st']}.pkl").write_bytes(pickle.dumps(res))
                print("replayed", res["st"], flush=True)
    print("skipped:", skipped)
    return skipped


def score() -> None:
    r = M24.replay_module()
    OUT.mkdir(parents=True, exist_ok=True)
    rep = {f.stem: pickle.loads(f.read_bytes()) for f in CACHE.glob("*.pkl")}
    c20 = r.certified_state(STATE_CSV, 2020)
    cp16 = r.countypres(COUNTYPRES, 2016)
    final = {s: c20[s][0] / (c20[s][0] + c20[s][1]) for s in c20 if s in r.STATE_NAMES}
    g = PS.state_errors()
    g = g[g["cycle"] <= 2016]                                          # structure: 2000-2016 only (2020 is the test)
    st_ = PS.structure(g)
    pres = N.Params(sd_common=st_["sd_common"], sd_region=st_["sd_region"], sd_state=st_["sd_state_persistent"],
                    huber_k=3.0)
    print("presidential structure from 2000-2016:", {k: round(v, 4) for k, v in st_.items() if k.startswith("sd_")})
    polls = polls2020()
    states = sorted(s for s in r.STATE_NAMES if s != "dc")
    prior = {}
    for s in states:
        if polls.get(s):
            prior[s] = (polls[s]["avg"], max(polls[s]["sd"], 0.01))
        else:
            pri = r.county_prior(cp16, s)
            prior[s] = (float(np.average(pri["s0"], weights=pri["V"])), pres.sd_unpolled)
    grid = pd.date_range(GRID_START, GRID_END, freq="1min")
    nat = M24.national_paths(rep, prior, pres, grid, states)
    rows, lv, hr = [], [], []
    for s in states:
        if s not in rep or s not in final:
            continue
        fm = 2 * final[s] - 1
        win_dem = final[s] > 0.5
        sh, sd = nat["state"][s][:, 0], nat["state"][s][:, 1]
        v3, v1 = M24.asof(rep[s]["v3"], grid), M24.asof(rep[s]["v1"], grid)
        g_ = pd.DataFrame({"t": grid, "nat": norm.cdf((sh - 0.5) / sd), "v3": v3["P"].fillna(rep[s]["v3_pre"]["P"]),
                           "v1": v1["P"].fillna(rep[s]["v1_pre"]["P"]), "counted": v1["counted"].fillna(0.0)})
        w = (lambda c: g_[c]) if win_dem else (lambda c: 1 - g_[c])
        rows.append({"state": s, "winner": "Biden" if win_dem else "Trump", "margin_pts": 100 * fm,
                     **{c: M24.settle(g_["t"], (w(c) >= 0.9).to_numpy()) for c in ("nat", "v3", "v1")}})
        for x in M24.LEVELS:
            b = rep[s]["v1"]
            b = b[b["counted"] >= x]
            if b.empty:
                continue
            t0 = b["t"].iloc[0]
            i = min(int(np.searchsorted(grid.values, (t0 + LAG).to_datetime64())), len(grid) - 1)
            v3r = rep[s]["v3"][rep[s]["v3"]["t"] <= t0].iloc[-1]
            lv.append({"state": s, "level": x, "err_nat": 100 * ((2 * sh[i] - 1) - fm), "err_v3": 100 * ((2 * v3r["share"] - 1) - fm),
                       "err_v1": 100 * ((2 * b["share"].iloc[0] - 1) - fm)})
        v3s = M24.asof(rep[s]["v3"], grid, ("share",))["share"].fillna(rep[s]["v3_pre"]["share"]).to_numpy()
        for h in range(0, 31, 2):
            th = pd.Timestamp("2020-11-04 00:00", tz="UTC") + pd.Timedelta(hours=h)
            i = min(int(np.searchsorted(grid.values, th.to_datetime64())), len(grid) - 1)
            hr.append({"state": s, "hour_utc": h, "counted": float(g_["counted"].iat[i]),
                       "err_prior": 100 * ((2 * prior[s][0] - 1) - fm), "err_nat": 100 * ((2 * sh[i] - 1) - fm),
                       "err_v3": 100 * ((2 * v3s[i] - 1) - fm)})
    df, lev, hrd = pd.DataFrame(rows), pd.DataFrame(lv), pd.DataFrame(hr)
    df["contested"] = df["margin_pts"].abs() < 8
    for c in ("nat", "v3", "v1"):
        df[f"lead_nat_vs_{c}"] = [None if pd.isna(a) or pd.isna(b) else round((b - a).total_seconds() / 60)
                                  for a, b in zip(df["nat"], df[c])]
    df.to_csv(OUT / "time_to_0.9.csv", index=False)
    lev.to_csv(OUT / "margin_error.csv", index=False)
    hrd.to_csv(OUT / "hourly_margin_error.csv", index=False)
    pd.set_option("display.width", 200)
    cs = df[df["contested"]].copy()
    for c in ("nat", "v3", "v1"):
        cs[c] = pd.to_datetime(cs[c]).dt.strftime("%m-%d %H:%M")
    print(cs[["state", "winner", "margin_pts", "nat", "v3", "v1", "lead_nat_vs_v3"]].round(1).to_string(index=False))
    print("median minutes national is earlier than v3 (contested):", float(df[df["contested"]]["lead_nat_vs_v3"].dropna().median()))
    c = lev[lev["state"].isin(df[df["contested"]]["state"])]
    print("median |margin error| at x% counted (contested):")
    print(c.groupby("level")[["err_nat", "err_v3", "err_v1"]].agg(lambda x: float(np.median(np.abs(x)))).round(2))
    h = hrd[hrd["state"].isin(df[df["contested"]]["state"])]
    print("by UTC hour since 2020-11-04 00:00:")
    print(h.groupby("hour_utc").agg(counted=("counted", "median"), prior=("err_prior", lambda x: x.abs().median()),
                                    v3=("err_v3", lambda x: x.abs().median()),
                                    nat=("err_nat", lambda x: x.abs().median())).round(2).T.to_string())
    print("realized common poll miss 2020 (final - poll avg, D share pts):",
          round(float(np.mean([100 * (final[s] - polls[s]["avg"]) for s in polls if s in final])), 2))


if __name__ == "__main__":
    steps = sys.argv[1:] or ["fetch", "replay", "score"]
    if "fetch" in steps:
        print(fetch())
    if "replay" in steps:
        replay_all()
    if "score" in steps:
        score()
