"""Backtest of the night Hispanic slope (model v5, [model] hisp_slope) against the primary model (v3).

Replays past statewide races from the county panel (data_public/us_county_panel.csv: county two-party Dem share and votes
of presidents 2012-2024 and the 2018/2022 Senate and governor races; Hispanic share = ACS 2020-24 B03003, the same file
as site/data/us2026/hub/census.json) through `brunder.us.project` with each state's own v3 [count]/[model] settings:

  state      v3 config used                             races replayed (prior race -> final race)
  TX FL CA AZ NM NC GA PA   configs/us2026_<st>_v3.toml   president 2016->2020, 2020->2024 (+ 2012->2016 as a sensitivity
                                                          cycle, used to choose hisp_slope_tau, never scored);
                                                          Senate/governor 2018->2022 where both county files exist
  GA 2022 governor and Senate again with the real vote-type split of the certified results (analysis/us_calibrate.py,
  needs the cached GA ballot items under $BRUNDER_DATA/raw/us/ga).

Count order (three scenarios, same simulated clock as analysis/us_calibrate.py / us_night_replay.py):
  size       early batch + 4 Election Day chunks per county, larger counties later (the generator of us_calibrate.events)
  hisp_last  pessimistic: counties report in order of Hispanic share, the Hispanic-heavy ones last (near sequential)
  hisp_first optimistic: the same order reversed, the Hispanic-heavy counties first
Vote-type states (TX, NC, GA, PA: vote_type = true) get an early+mail batch of early_share of every county's final vote
at the county's own final share (no partisan vote-type gap, which the GA vote-type replay covers).
The poll prior (swing_prior_mean) is simulated: true statewide swing + N(0, swing_prior_sd²), the sd of the state's v3.
Checkpoints: 1, 5, 10, 25, 50, 75 % of the final two-party vote counted.

    BRUNDER_DATA=/tmp/hisp/data uv run python analysis/us_hisp_backtest.py run --sims 20 --out results/us_hisp_backtest
"""

from __future__ import annotations

import argparse
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_calibrate as cal  # noqa: E402

ROOT = us.ROOT_DIR
PANEL = ROOT / "data_public" / "us_county_panel.csv"
STATES = {"tx": "48", "fl": "12", "ca": "06", "az": "04", "nm": "35", "nc": "37", "ga": "13", "pa": "42"}
V3 = {"tx": "us2026_tx_sen_v3", "fl": "us2026_fl_gov_v3", "ca": "us2026_ca_gov_v3", "az": "us2026_az_gov_v3",
      "nm": "us2026_nm_sen_v3", "nc": "us2026_nc_sen_v3", "ga": "us2026_ga_gov_v3", "pa": "us2026_pa_gov_v3"}
CHECK = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75)
SCENARIOS = ("size", "hisp_last", "hisp_first")
TAUS = (0.1, 0.2, 0.4, 0.8)                     # hisp_slope_tau grid
# (final year, race, prior year, prior race)
RACES = [(2016, "pres", 2012, "pres"), (2020, "pres", 2016, "pres"), (2024, "pres", 2020, "pres"),
         (2022, "sen", 2018, "sen"), (2022, "gov", 2018, "gov")]
TUNE = {2016, 2020}                             # cycles hisp_slope_tau is chosen on; 2022 and 2024 are out of sample


@dataclass
class Case:
    st: str
    year: int
    race: str
    pri: pd.DataFrame            # unit_id, name, s0, V, h
    dem: np.ndarray              # final two-party Dem / Rep votes per county (same order as pri)
    rep: np.ndarray
    real: pd.DataFrame | None = None     # GA: real vote-type split (cal.by_type)

    @property
    def label(self) -> str:
        return f"{self.st}-{self.race}{self.year}" + ("-vt" if self.real is not None else "")


def _panel() -> pd.DataFrame:
    p = pd.read_csv(PANEL, comment="#", dtype={"fips": str, "st": str})
    ids = pd.read_csv(ROOT / "data_public" / "us2026_expected_votes_v4.csv", comment="#")["unit_id"]
    abbr = {u[-5:]: u.split("-")[1] for u in ids}
    p["unit_id"] = ["us-" + abbr.get(f, "xx") + "-" + f for f in p["fips"]]
    return p


def _race(p: pd.DataFrame, st: str, year: int, race: str) -> pd.DataFrame | None:
    d = p[(p["st"] == STATES[st]) & (p["year"] == year)]
    out = d[["unit_id", "hisp", f"s_{race}", f"v_{race}"]].rename(columns={f"s_{race}": "s", f"v_{race}": "v"}).dropna()
    return out if len(out) >= 10 else None


def build_cases(include_ga_real: bool = True) -> list[Case]:
    p = _panel()
    cases = []
    for st in STATES:
        for year, race, py, prace in RACES:
            f, pr = _race(p, st, year, race), _race(p, st, py, prace)
            if f is None or pr is None:
                continue
            m = pr.merge(f, on="unit_id", suffixes=("_p", "_f"))
            if len(m) < 0.95 * len(f):
                continue
            pri = pd.DataFrame({"unit_id": m["unit_id"], "name": m["unit_id"], "s0": m["s_p"], "V": m["v_p"],
                                "h": m["hisp_p"]})
            cases.append(Case(st, year, race, pri, (m["s_f"] * m["v_f"]).to_numpy(),
                              ((1 - m["s_f"]) * m["v_f"]).to_numpy()))
    if include_ga_real:
        priors = us.load_priors(us.PUBLIC_PRIORS)
        hisp = p[p["year"] == 2024].set_index("unit_id")["hisp"]
        for race in ("gov", "sen"):
            bt = cal.by_type("2022NovGen", race)
            cfg = {"election": {"state": "ga"}, "prior": {"share": "ga_pres_2020", "votes": "ga_pres_2020"}}
            pri = us.race_prior(cfg, priors)
            pri["h"] = pri["unit_id"].map(hisp).to_numpy()
            bt = bt.set_index("unit_id").reindex(pri["unit_id"]).reset_index()
            cases.append(Case("ga", 2022, race, pri, (bt["early_d"] + bt["late_d"]).to_numpy(),
                              (bt["early_r"] + bt["late_r"]).to_numpy(), real=bt))
    return cases


def _bt(case: Case, early_share: float) -> pd.DataFrame:
    if case.real is not None:
        return case.real
    d, r = case.dem, case.rep
    return pd.DataFrame({"unit_id": case.pri["unit_id"], "early_d": d * early_share, "early_r": r * early_share,
                         "late_d": d * (1 - early_share), "late_r": r * (1 - early_share)})


def events(bt: pd.DataFrame, h: np.ndarray, rng: np.random.Generator, scenario: str) -> pd.DataFrame:
    """One simulated night (time, unit_id, d, r, early, last). `size` is us_calibrate.events; the Hispanic scenarios
    order the counties by Hispanic share (rank 1 reports last) with start times spread over 270 minutes."""
    if scenario == "size":
        return cal.events(bt, rng)
    size = bt[["early_d", "early_r", "late_d", "late_r"]].sum(axis=1).to_numpy()
    rank = (h.argsort().argsort() + 0.0) / max(len(h) - 1, 1)
    if scenario == "hisp_first":
        rank = 1 - rank
    out = []
    for i, u in enumerate(bt["unit_id"]):
        t0 = 270 * rank[i] + rng.uniform(0, 40)
        out.append((t0, u, bt["early_d"].iat[i], bt["early_r"].iat[i], True, False))
        ts = np.sort(t0 + rng.uniform(20, 80, 4))
        w = rng.dirichlet(np.ones(4))
        for k, (t, wk) in enumerate(zip(ts, w)):
            out.append((t, u, bt["late_d"].iat[i] * wk, bt["late_r"].iat[i] * wk, False, k == 3))
    return pd.DataFrame(out, columns=["t", "unit_id", "d", "r", "early", "last"]).sort_values("t", ignore_index=True)


def run_case(args: tuple) -> list[dict]:
    case, scenario, sims, taus = args
    v3 = us.load_config(ROOT / "configs" / f"{V3[case.st]}.toml")
    count = {k: v for k, v in v3["count"].items() if k not in ("expected_votes", "expected_votes_level")}
    count["turnout_ratio"] = 1.0
    model = {k: v for k, v in v3["model"].items() if k != "swing_prior_mean"}
    cfg0 = {"election": {"state": case.st, "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}},
            "prior": {}, "count": count}
    bt = _bt(case, count.get("early_share", 0.5))
    tot = float((bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()))
    final = float(case.dem.sum() / (case.dem.sum() + case.rep.sum()))
    base = us.baseline_share(case.pri)
    h = case.pri["h"].to_numpy()
    out = []
    for s in range(sims):
        seed = zlib.crc32(f"{case.label}|{s}".encode())
        eps = np.random.default_rng(seed).normal(0, model["swing_prior_sd"])
        rng = np.random.default_rng(seed + SCENARIOS.index(scenario) + 1)
        ev = events(bt, h, rng, scenario)
        cum = (ev["d"] + ev["r"]).cumsum() / tot
        for c in CHECK:
            upto = int(np.searchsorted(cum.to_numpy(), c)) + 1
            rows = cal.rows_at(ev, upto)
            m_base = {**model, "swing_prior_mean": final - base + eps}
            row = {"case": case.label, "st": case.st, "year": case.year, "race": case.race, "scenario": scenario,
                   "sim": s, "check": c, "final": final, "counted": float(ev["d"].iloc[:upto].sum() + ev["r"].iloc[:upto].sum()) / tot}
            for name, extra in (("v3", {}), *((f"v5_{t}", {"hisp_slope": True, "hisp_slope_tau": t}) for t in taus)):
                res, _ = us.project(rows, {**cfg0, "model": {**m_base, **extra}}, case.pri)
                row[f"{name}_share"], row[f"{name}_sd"], row[f"{name}_p"] = res["share_hat"], res["share_sd"], res["p_win"]
                if name != "v3":
                    row[f"{name}_beta"] = res.get("hisp_beta", np.nan)
            out.append(row)
    return out


def run(sims: int, taus=TAUS, jobs: int = 10, only: str | None = None) -> pd.DataFrame:
    cases = build_cases()
    if only:
        cases = [c for c in cases if c.st in only.split(",")]
    tasks = [(c, sc, sims, taus) for c in cases for sc in SCENARIOS]
    with ProcessPoolExecutor(jobs) as ex:
        res = [r for rs in ex.map(run_case, tasks) for r in rs]
    return pd.DataFrame(res)


# ------------------------------------------------------------------ analysis

GROUPS = {"hispanic-heavy (TX FL CA NM)": ("tx", "fl", "ca", "nm"), "other (AZ NC GA PA)": ("az", "nc", "ga", "pa")}
SCORED = (2020, 2022, 2024)                      # cycles scored; 2016 only tunes hisp_slope_tau (with 2020)
EPS = 1e-4                                       # win probabilities are clipped to [EPS, 1 - EPS] in log scores


def md(d: pd.DataFrame) -> str:
    """GitHub markdown table."""
    h = [str(c) for c in d.columns]
    rows = ["| " + " | ".join(str(x) for x in r) + " |" for r in d.itertuples(index=False)]
    return "\n".join(["| " + " | ".join(h) + " |", "|" + "---|" * len(h), *rows])


def prep(df: pd.DataFrame, tau: float) -> pd.DataFrame:
    """Per row: margin error (points, two-party Dem minus Rep), |error|, 90 % coverage, sd, win probability, for v3 and
    v5 at hisp_slope_tau = tau."""
    d = df.copy()
    for name, col in (("v3", "v3"), ("v5", f"v5_{tau}")):
        e = d[f"{col}_share"] - d["final"]
        d[f"{name}_err"] = 200 * e
        d[f"{name}_abs"] = 200 * e.abs()
        d[f"{name}_sd_pts"] = 200 * d[f"{col}_sd"]
        d[f"{name}_cov"] = (e.abs() < 1.645 * d[f"{col}_sd"]).astype(float)
        d[f"{name}_p"] = d[f"{col}_p"]
        d[f"{name}_z"] = e / d[f"{col}_sd"]
    d["win"] = (d["final"] > 0.5).astype(float)
    d["beta"] = d[f"v5_{tau}_beta"]
    return d


def choose_tau(df: pd.DataFrame, taus) -> tuple[float, pd.DataFrame]:
    """hisp_slope_tau with the smallest pooled mean |margin error| over the tuning cycles (2016, 2020), checkpoints
    5/10/25/50 % and the three order scenarios (states equally weighted); the table lists every tau."""
    t = df[df["year"].isin(TUNE) & df["check"].isin([0.05, 0.10, 0.25, 0.50])]
    rows = []
    for tau in (None, *taus):
        d = prep(t, taus[0] if tau is None else tau)
        k = "v3" if tau is None else "v5"
        g = d.groupby("st")[[f"{k}_abs", f"{k}_cov"]].mean().mean()
        rows.append({"model": "v3" if tau is None else f"v5 tau={tau}", "tau": tau, "mae_pts": g[f"{k}_abs"],
                     "coverage90": g[f"{k}_cov"]})
    tab = pd.DataFrame(rows)
    best = tab[tab["tau"].notna()].sort_values("mae_pts").iloc[0]["tau"]
    return float(best), tab


def _ll_win(p5, p3, win, w):
    p = np.clip(w * p5 + (1 - w) * p3, EPS, 1 - EPS)
    return np.log(np.where(win > 0, p, 1 - p))


def _ll_margin(m5, s5, m3, s3, x, w):
    from scipy.stats import norm
    return np.log(np.clip(w * norm.pdf(x, m5, s5) + (1 - w) * norm.pdf(x, m3, s3), 1e-300, None))


def stack_weight(d: pd.DataFrame, kind: str) -> float:
    """Weight w on v5 maximising the pooled log score of the mixture w·v5 + (1−w)·v3 over the rows of d."""
    from scipy.optimize import minimize_scalar
    if kind == "win":
        f = lambda w: -_ll_win(d["v5_p"].to_numpy(), d["v3_p"].to_numpy(), d["win"].to_numpy(), w).sum()
    else:
        x = d["final"].to_numpy()
        f = lambda w: -_ll_margin(d["v5_share"].to_numpy(), d["v5_sd"].to_numpy(), d["v3_share"].to_numpy(),
                                  d["v3_sd"].to_numpy(), x, w).sum()
    return float(minimize_scalar(f, bounds=(0, 1), method="bounded", options={"xatol": 1e-4}).x)


def analyze(df: pd.DataFrame, taus, out: Path) -> str:
    tau, tau_tab = choose_tau(df, taus)
    d = prep(df, tau)
    d["v5_share"], d["v5_sd"] = df[f"v5_{tau}_share"], df[f"v5_{tau}_sd"]
    d["v3_share"], d["v3_sd"] = df["v3_share"], df["v3_sd"]
    s = d[d["year"].isin(SCORED)]
    lines = [f"hisp_slope_tau chosen on the tuning cycles {sorted(TUNE)}: **{tau}**", "", md(tau_tab.round(3)), ""]
    cols = {"mae": ("{}_abs", "mean"), "cov": ("{}_cov", "mean"), "sd": ("{}_sd_pts", "mean")}

    def agg(g: pd.DataFrame) -> pd.Series:
        r = {}
        for m in ("v3", "v5"):
            for k, (c, fn) in cols.items():
                r[f"{m} {k}"] = getattr(g[c.format(m)], fn)()
        r["mae v5-v3"] = r["v5 mae"] - r["v3 mae"]
        return pd.Series(r)

    ck = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75]
    lines += ["## Per state, order scenario and checkpoint (scored cycles 2020/2022/2024, all races; margin points)", ""]
    t = s[s["check"].isin([0.05, 0.25, 0.50])].groupby(["st", "scenario", "check"]).apply(agg, include_groups=False).round(2)
    lines += [t.reset_index().pipe(md), ""]
    lines += ["## Pooled over states (each state equal weight), per scenario and checkpoint", ""]
    per_state = s.groupby(["st", "scenario", "check"]).apply(agg, include_groups=False)
    pooled = per_state.groupby(["scenario", "check"]).mean().round(2)
    lines += [pooled.reset_index().pipe(md), ""]
    allck = per_state.groupby(["scenario"]).mean().round(2)
    lines += ["## Pooled over states and all six checkpoints, per scenario", "", allck.reset_index().pipe(md), ""]
    ps = s.groupby("st").apply(agg, include_groups=False).round(3)
    lines += ["## Per state, all scenarios and checkpoints", "", ps.reset_index().pipe(md), ""]
    pc = s.groupby(["year", "race"]).apply(agg, include_groups=False).round(3)
    lines += ["## Per cycle/race, all states", "", pc.reset_index().pipe(md), ""]
    sens = d[d["year"] == 2016].groupby("st").apply(agg, include_groups=False).round(3)
    lines += ["## Sensitivity: 2012->2016 president (tuning cycle, slope of the opposite sign)", "", sens.reset_index().pipe(md), ""]
    beta = d.groupby(["st", "year", "race", "scenario"])["beta"].median().unstack("scenario").round(3)
    lines += ["## Median night slope beta at the checkpoints", "", beta.reset_index().pipe(md), ""]
    # stacking, leave-one-cycle-out
    lines += ["## Stacking weights on v5 (leave-one-cycle-out; cycles 2020, 2022, 2024; all scenarios and checkpoints pooled)", ""]
    res = []
    units = {**{g: v for g, v in GROUPS.items()}, **{st.upper(): (st,) for st in STATES}, "ALL": tuple(STATES)}
    for g, sts in units.items():
        gd = s[s["st"].isin(sts)]
        if gd.empty:
            continue
        row = {"group": g}
        for kind in ("win", "margin"):
            row[f"w_{kind}"] = stack_weight(gd, kind)
            ll = {"v3": [], "v5": [], "blend": []}
            for cyc in SCORED:
                tr, te = gd[gd["year"] != cyc], gd[gd["year"] == cyc]
                if te.empty or tr.empty:
                    continue
                w = stack_weight(tr, kind)
                for name, ww in (("v3", 0.0), ("v5", 1.0), ("blend", w)):
                    ll[name].append(
                        _ll_win(te["v5_p"].to_numpy(), te["v3_p"].to_numpy(), te["win"].to_numpy(), ww) if kind == "win" else
                        _ll_margin(te["v5_share"].to_numpy(), te["v5_sd"].to_numpy(), te["v3_share"].to_numpy(),
                                   te["v3_sd"].to_numpy(), te["final"].to_numpy(), ww))
            for name in ll:
                row[f"loco {kind} {name}"] = float(np.concatenate(ll[name]).mean()) if ll[name] else np.nan
        res.append(row)
    st_tab = pd.DataFrame(res)
    lines += [md(st_tab.round(3)), ""]
    st_tab.to_csv(out / "stacking.csv", index=False)
    per_state.reset_index().to_csv(out / "per_state_scenario_check.csv", index=False)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyze"])
    ap.add_argument("--sims", type=int, default=20)
    ap.add_argument("--out", default="results/us_hisp_backtest")
    ap.add_argument("--only", default=None, help="comma-separated states")
    ap.add_argument("--taus", default=",".join(map(str, TAUS)))
    ap.add_argument("--jobs", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "analyze":
        out = ROOT / a.out
        text = analyze(pd.read_csv(out / "runs.csv"), tuple(float(x) for x in a.taus.split(",")), out)
        (out / "summary.md").write_text(text)
        print(text)
        return
    df = run(a.sims, tuple(float(x) for x in a.taus.split(",")), a.jobs, a.only)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "runs.csv", index=False)
    print(f"{len(df)} rows -> {out / 'runs.csv'}")


if __name__ == "__main__":
    main()
