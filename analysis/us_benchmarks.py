"""Count-night benchmarks for the 2026 statewide and House races: where the votes should land BEFORE they arrive.

A pre-election, frozen, publishable "what to expect" table. No live use and no 2026 count data: everything is computed from
the unified pre-count priors (data_public/us2026_unified_*priors.csv, configs/us2026_unified/**), the registered nowcast
(brunder.us.project, unchanged) and parameters learned on 2014-2024 only. Method, columns and backtest: docs/us_benchmarks.md.

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_benchmarks.py order     # reporting-order table (needs ~/us2024_data)
    ... us_benchmarks.py resid        # county residual sd by size from the leave-one-cycle-out county priors
    ... us_benchmarks.py build        # data_public/us2026_benchmarks.csv + data_public/us2026_benchmarks/<race>.json (deterministic)
    ... us_benchmarks.py backtest     # 2016-2024 backtest of the benchmark idea -> results/us_benchmarks/*.csv
    ... us_benchmarks.py ordercheck | mirage | check | tables   # order validation, raw-count gap check, fast-nowcast check, doc tables

Definitions (two-party shares, D = Democratic): county c has benchmark share s0_c and expected votes V_c; the final margin is
M = sum_c V_c (2 s_c - 1) votes. "Reports" means the county's whole vote is in. dMargin_dshare / dP_dshare are the nowcast's own:
county c is fed to brunder.us.project as a complete county at s0_c +- 1 point, after the counties expected to report before it (all on
benchmark), and the projected margin and win probability are differenced. The tell scores average the same sensitivity over random
reporting orders (fast_nowcast, a vectorised copy of us.project checked against it to 1e-9) because the order is uncertain.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brunder import us                                   # noqa: E402
from brunder.paths import DATA, ROOT                     # noqa: E402

FROZEN = "2026-11-02"
PUB = ROOT / "data_public"
OUT_CSV = PUB / "us2026_benchmarks.csv"
OUT_JSON = PUB / "us2026_benchmarks"
ORDER_CSV = PUB / "us2026_benchmarks_order.csv"
RESID_CSV = PUB / "us2026_benchmarks_resid.csv"
UNI_PRIORS = PUB / "us2026_unified_priors.csv"
UNI_HOUSE = PUB / "us2026_unified_house_priors.csv"
CFG_DIR = ROOT / "configs" / "us2026_unified"
RES = ROOT / "results" / "us_benchmarks"
US2024 = Path(__import__("os").environ.get("US2024_DATA", "~/us2024_data")).expanduser() / "raw" / "us"

DELTA = 0.01                  # share perturbation (one point) for the sensitivities
EARLY_FRAC = 0.40             # "early reporting" = completes within the first 40 % of the expected vote
TOP_K = 5                     # tell_top flags per race
CHECKS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90)       # benchmark path checkpoints (share of the final vote counted)
W_EMP = 2.0 / 3.0             # weight of the empirical (2020, 2024) order percentile against the size percentile
ORDER_T = ("t_first", "t10", "t25", "t50", "t75", "t95", "t_final")
ELECTION_DAY = {2020: "2020-11-03", 2024: "2024-11-05", 2026: "2026-11-03"}
GH_X, GH_W = np.polynomial.hermite_e.hermegauss(61)
GH_W = GH_W / np.sqrt(2 * np.pi)


# ------------------------------------------------------------------ races: unified configs, priors, registered fallback


def _load_priors() -> pd.DataFrame:
    parts = [pd.read_csv(UNI_PRIORS, comment="#")]
    if UNI_HOUSE.exists():
        parts.append(pd.read_csv(UNI_HOUSE, comment="#"))
    return pd.concat(parts, ignore_index=True)


def unified_specs() -> list[dict]:
    """One spec per unified prior source: statewide configs (the recorded-feed config when a civicAPI twin shares the
    source), then House districts. Spec: id, race_id, kind, cfg, pri, source ("unified"), cfg_path."""
    pri_all = _load_priors()
    have = set(pri_all["source"])
    chosen: dict[str, tuple[Path, dict]] = {}
    paths = sorted(CFG_DIR.glob("us2026_unified_*_v1.toml")) + sorted((CFG_DIR / "house").glob("us2026_unified_house_*_v1.toml"))
    for p in paths:
        c = us.load_config(p)
        src = c["prior"]["share"]
        civic = "_civic_" in p.name
        if src not in chosen or (chosen[src][1].get("_civic") and not civic):
            c["_civic"] = civic
            chosen[src] = (p, c)
    specs = []
    for src, (p, c) in sorted(chosen.items()):
        if src not in have:
            continue
        pri = us.race_prior(c, pri_all)
        if pri is None or pri.empty:
            continue
        rid = c["election"]["race_id"]
        specs.append({"id": c["id"], "race_id": rid, "kind": "house" if rid.startswith("house:") else "statewide", "cfg": c,
                      "pri": pri.reset_index(drop=True), "source": "unified", "cfg_path": str(p.relative_to(ROOT))})
    return specs


def fallback_specs(covered: set[str]) -> list[dict]:
    """Registered races (v1 configs) whose unified prior is missing: the registered v3 config (v1 if it has no v3) with the
    registered county priors. Returns [] when the unified set covers every registered race (the 2026 case)."""
    pri_all = us.load_priors()
    out = []
    pats = [CFG_DIR.parent.glob("us2026_*_v1.toml"), (CFG_DIR.parent / "us2026_civic").glob("*_v1.toml"),
            (CFG_DIR.parent / "us2026_house").glob("*_v1.toml")]
    for g in pats:
        for p in sorted(g):
            c1 = us.load_config(p)
            rid = c1["election"]["race_id"].replace("-civic", "")
            if rid in covered:
                continue
            p3 = p.with_name(p.name.replace("_v1.toml", "_v3.toml"))
            c = us.load_config(p3) if p3.exists() else c1
            pri = us.race_prior(c, pri_all)
            if pri is None or pri.empty:
                continue
            base = us.baseline_share(pri)
            m = c.get("model", {})
            mean = base + float(m.get("swing_prior_mean") or 0.0)
            sd = float(m.get("swing_prior_sd") or 0.05)
            c = {**c, "prior": {**c["prior"], "statewide_mean": round(mean, 4), "statewide_sd": sd,
                                "p_win": round(float(norm.cdf((mean - 0.5) / sd)), 4)}}
            out.append({"id": c["id"], "race_id": rid, "kind": "house" if rid.startswith("house:") else "statewide", "cfg": c,
                        "pri": pri.reset_index(drop=True), "source": "registered v3" if p3.exists() else "registered v1",
                        "cfg_path": str(p3.relative_to(ROOT) if p3.exists() else p.relative_to(ROOT))})
    return out


def race_specs() -> tuple[list[dict], list[str]]:
    sp = unified_specs()
    cov = {s["race_id"] for s in sp}
    fb = fallback_specs(cov)
    return sp + fb, [s["race_id"] for s in fb]


# ------------------------------------------------------------------ feeding complete counties to brunder.us.project


def complete_fraction(cfg: dict) -> float:
    """Counted share of a county's expected vote when the feed calls it complete (what count_state assumes)."""
    R = us.rules(cfg)
    if R.use_outstanding:
        return 1.0
    return R.f_complete if R.f_complete > 0 else R.f_max


def complete_rows(cfg: dict, pri: pd.DataFrame, pos: np.ndarray, share: np.ndarray) -> pd.DataFrame:
    """Feed-style rows (as cal.rows_at builds them) with counties `pos` of `pri` complete at two-party share `share`; the
    counted votes are the complete fraction of V so that the model's expected vote of the county is exactly V."""
    if len(pos) == 0:
        return us._frame([])
    R, E = us.rules(cfg), cfg["election"]
    n = pri["V"].to_numpy(float)[pos] * complete_fraction(cfg)
    ids = pri["unit_id"].to_numpy()[pos]
    share = np.asarray(share, float)
    parts = []
    for party, sh in ((E["a"]["party"], share), (E["b"]["party"], 1 - share)):
        v = n * sh
        parts.append(pd.DataFrame({
            "level": "county", "unit_id": ids, "name": "", "race": E["race"], "candidate": "", "party": party, "votes": v,
            "early": v * R.early_share if R.vote_type else np.nan, "reporting": 1.0, "units": 1.0,
            "ballots": n if R.use_outstanding else np.nan, "registered": np.nan,
            "outstanding": 0.0 if R.use_outstanding else np.nan}))
    return pd.concat(parts, ignore_index=True)


def run_project(cfg: dict, pri: pd.DataFrame, pos: np.ndarray, share: np.ndarray) -> dict:
    res, _ = us.project(complete_rows(cfg, pri, pos, share), cfg, pri)
    return res


def partial_rows(cfg: dict, pri: pd.DataFrame, f: np.ndarray, share: np.ndarray) -> pd.DataFrame:
    """Rows for counties that have reported the fraction f of their expected vote (not complete), all at `share`; with a
    vote-type feed the early batch (early_share of V) comes first."""
    R, E = us.rules(cfg), cfg["election"]
    pos = np.where(f > 0)[0]
    if len(pos) == 0:
        return us._frame([])
    n = f[pos] * pri["V"].to_numpy(float)[pos]
    parts = []
    for party, sh in ((E["a"]["party"], share[pos]), (E["b"]["party"], 1 - share[pos])):
        v = n * sh
        early = np.minimum(n, R.early_share * pri["V"].to_numpy(float)[pos]) * sh
        parts.append(pd.DataFrame({
            "level": "county", "unit_id": pri["unit_id"].to_numpy()[pos], "name": "", "race": E["race"], "candidate": "", "party": party,
            "votes": v, "early": early if R.vote_type else np.nan, "reporting": 0.0, "units": 1.0, "ballots": np.nan,
            "registered": np.nan, "outstanding": np.nan}))
    return pd.concat(parts, ignore_index=True)


def project_partial(cfg: dict, pri: pd.DataFrame, f: np.ndarray, share: np.ndarray) -> dict:
    return us.project(partial_rows(cfg, pri, f, share), cfg, pri)[0]


PACE_GRID = np.arange(0.0, 3 * 1440 + 1, 5.0)             # minutes after poll close


def pacing_curves(state: str, pri: pd.DataFrame, otab: pd.DataFrame, pc_utc: str) -> np.ndarray | None:
    """(counties x PACE_GRID) typical fraction of each county's vote reported at each minute after poll close: the mean over
    the 2020 and 2024 presidential feeds of the piecewise-linear curve through (first report 0, t10 .10, t25 .25, t50 .50, t75 .75,
    t95 .95, final 1.0). Counties without a feed match get the vote-weighted mean curve. None without a feed covering 90 % of V."""
    V = pri["V"].to_numpy(float)
    fips = pri["unit_id"].str[-5:].to_numpy()
    pc = pd.Timestamp(pc_utc)
    ys = np.array([0.0, 0.10, 0.25, 0.50, 0.75, 0.95, 1.0])
    curves = []
    for cyc in (2020, 2024):
        sub = otab[(otab["state"] == state) & (otab["cycle"] == cyc)].drop_duplicates("fips5").set_index("fips5")
        if sub.empty:
            continue
        have = np.isin(fips, sub.index.to_numpy())
        if V[have].sum() < 0.9 * V.sum():
            continue
        shift = pd.Timestamp(ELECTION_DAY[cyc], tz="UTC") - pd.Timestamp(ELECTION_DAY[2026], tz="UTC")
        F = np.zeros((len(V), len(PACE_GRID)))
        for i in np.where(have)[0]:
            r = sub.loc[fips[i]]
            xs = np.array([(pd.Timestamp(r[c]) - (pc + shift)).total_seconds() / 60.0 for c in ORDER_T])
            F[i] = np.interp(PACE_GRID, np.maximum.accumulate(xs), ys, left=0.0, right=1.0)
        mean_curve = (F[have] * V[have, None]).sum(axis=0) / V[have].sum()
        F[~have] = mean_curve
        curves.append(F)
    return np.mean(curves, axis=0) if curves else None


# ------------------------------------------------------------------ county residual sd (size profile)


def load_resid_profile() -> pd.DataFrame:
    return pd.read_csv(RESID_CSV, comment="#")


def resid_sd(V, prof: pd.DataFrame) -> np.ndarray:
    """County residual sd (share, not points) given the statewide share, by expected votes V: piecewise linear in log V
    through the bin medians of the leave-one-cycle-out county errors, flat outside."""
    x = np.log(prof["median_V"].to_numpy(float))
    y = prof["rmse_pts"].to_numpy(float) / 100.0
    return np.interp(np.log(np.maximum(np.asarray(V, float), 1.0)), x, y)


# ------------------------------------------------------------------ expected reporting order


def load_order_table() -> pd.DataFrame:
    return pd.read_csv(ORDER_CSV, comment="#", dtype={"fips5": str})


def _pct(x: pd.Series) -> pd.Series:
    return x.rank(method="average") / x.notna().sum()


def expected_order(state: str, pri: pd.DataFrame, otab: pd.DataFrame, pc_utc: str) -> pd.DataFrame:
    """Per county: order score (later = larger), expected rank (0 first), minutes to 95 % of the county vote after poll
    close (geometric mean over the cycles with data), order source. score = 2/3 mean percentile of the county's completion
    time in the 2020 (NYT/Edison) and 2024 (CNN/Edison) presidential count feeds of its state + 1/3 percentile of its
    expected votes (size); size alone where no feed county matches. Weight chosen on how well one cycle's order predicts
    the other's (docs/us_benchmarks.md)."""
    V = pri["V"].to_numpy(float)
    fips = pri["unit_id"].str[-5:]
    size = pd.Series(V).rank(method="average") / len(V)
    pc = pd.Timestamp(pc_utc)
    emp, mins, used = [], [], []
    for cyc in (2020, 2024):
        sub = otab[(otab["state"] == state) & (otab["cycle"] == cyc)].drop_duplicates("fips5").set_index("fips5")
        if sub.empty:
            continue
        t95 = pd.to_datetime(sub["t95"], utc=True).reindex(fips.values)
        if t95.notna().to_numpy()[:].astype(float) @ V < 0.9 * V.sum():
            continue
        shift = pd.Timestamp(ELECTION_DAY[cyc], tz="UTC") - pd.Timestamp(ELECTION_DAY[2026], tz="UTC")
        m = ((t95 - (pc + shift)).dt.total_seconds() / 60.0).clip(lower=1.0)
        pct = _pct(pd.Series(m.to_numpy()))
        emp.append(pct.fillna(size).to_numpy())
        mins.append(m.to_numpy())
        used.append(str(cyc))
    if emp:
        score = W_EMP * np.mean(emp, axis=0) + (1 - W_EMP) * size.to_numpy()
        lm = np.nanmean(np.log(np.vstack(mins)), axis=0)
        ok = np.isfinite(lm)
        if ok.sum() >= 3 and (~ok).any():                       # fill counties without a feed match from their order score
            b = np.polyfit(score[ok], lm[ok], 1)
            lm = np.where(ok, lm, np.polyval(b, score))
        src = "+".join(used) + "+size"
    else:
        score, lm, src = size.to_numpy(), np.full(len(V), np.nan), "size"
    rank = pd.Series(score).rank(method="first").to_numpy().astype(int) - 1
    return pd.DataFrame({"score": score, "rank": rank, "minutes": np.exp(lm), "order_source": src})


# ------------------------------------------------------------------ the sensitivities, tells and the benchmark path


def exp_abs_dp(m0: float, sd: float, g: float, s_delta: float) -> float:
    """E |Phi((m0 + g x)/sd) - Phi(m0/sd)|, x ~ N(0, s_delta^2): the expected move of P(win) when the county's deviation
    (share) is drawn from its residual distribution and the margin moves linearly (g votes per unit share)."""
    x = GH_X * s_delta
    if sd <= 0:                                                     # nothing left to project: the call is a step function
        return float(np.sum(GH_W * np.abs(((m0 + g * x) > 0).astype(float) - float(m0 > 0))))
    return float(np.sum(GH_W * np.abs(norm.cdf((m0 + g * x) / sd) - norm.cdf(m0 / sd))))


def race_table(spec: dict, otab: pd.DataFrame, prof: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """(per-county table, race summary) for one race."""
    cfg, pri = spec["cfg"], spec["pri"]
    E, P, M = cfg["election"], cfg["prior"], cfg.get("model", {})
    N = len(pri)
    V, s0 = pri["V"].to_numpy(float), pri["s0"].to_numpy(float)
    totV = V.sum()
    od = expected_order(E["state"], pri, otab, E["poll_close_utc"])
    order = np.argsort(od["rank"].to_numpy())                        # positions in expected reporting order
    # prefix k (k counties complete at benchmark, in expected order): the benchmark path of the nowcast
    pre = [run_project(cfg, pri, order[:k], s0[order[:k]]) for k in range(N + 1)]
    p_pre, m_pre, sd_pre = (np.array([r[k] for r in pre]) for k in ("p_win", "margin_hat", "margin_sd"))
    cumV = np.concatenate([[0.0], np.cumsum(V[order])]) / totV
    sd_state = float(P["statewide_sd"])
    rsd = resid_sd(V, prof)
    rows = []
    for j in range(N):
        i = order[j]
        d = min(DELTA, s0[i] - 0.001, 0.999 - s0[i])
        up = run_project(cfg, pri, order[:j + 1], np.append(s0[order[:j]], s0[i] + d))
        dn = run_project(cfg, pri, order[:j + 1], np.append(s0[order[:j]], s0[i] - d))
        gm = (up["margin_hat"] - dn["margin_hat"]) / (2 * d)                      # votes of statewide margin per unit share
        gp = (up["p_win"] - dn["p_win"]) / (2 * d)
        m0, sd0 = m_pre[j + 1], sd_pre[j + 1]                                      # after the county reports on benchmark
        direct = 2.0 * V[i]                                                         # own votes: margin votes per unit share
        dp_direct = float(norm.pdf(m0 / sd0) / sd0 * direct) if sd0 > 0 else 0.0
        s_delta = float(np.hypot(sd_state, rsd[i]))                                 # deviation from benchmark: state + county part
        pos_i = int(i)
        rows.append({
            "unit_id": pri["unit_id"].iat[i], "name": pri["name"].iat[i], "s0": s0[i], "V": V[i], "turnout_share": V[i] / totV,
            "margin_votes": V[i] * (2 * s0[i] - 1),
            "resid_sd_pts": 100 * rsd[i],
            "band_lo": s0[i] - 1.645 * rsd[i], "band_hi": s0[i] + 1.645 * rsd[i],
            "band_state_lo": s0[i] - 1.645 * s_delta, "band_state_hi": s0[i] + 1.645 * s_delta,
            "exp_rank": j + 1, "exp_cum_before": cumV[j], "exp_cum_after": cumV[j + 1],
            "exp_minutes": od["minutes"].iat[i], "order_source": od["order_source"].iat[i],
            "dMargin_dshare": gm * 0.01, "dMargin_direct": direct * 0.01, "dMargin_prop": (gm - direct) * 0.01,
            "dP_dshare": 100 * gp * 0.01, "dP_direct": 100 * dp_direct * 0.01, "dP_prop": 100 * (gp - dp_direct) * 0.01,
            "pos": pos_i,
            "_sd_diff": abs(up["margin_sd"] - dn["margin_sd"]) / max(sd0, 1.0),
            "_nonlin": abs((up["margin_hat"] + dn["margin_hat"]) / 2 - m0) / max(abs(gm * d), 1.0),
        })
    t = pd.DataFrame(rows)
    pct = pd.Series(od["score"].to_numpy()).rank(method="average").to_numpy() / N
    mc = mc_tells(s0, V, sd_state, rsd, lambda rg: np.argsort(pct + SIGMA_ORDER * rg.standard_normal(N), kind="stable"),
                  par_from_cfg(cfg), float(M["swing_prior_sd"]), MC_SIMS, np.random.default_rng(zlib.crc32(spec["race_id"].encode())))
    mc = mc.iloc[t["pos"].to_numpy()].reset_index(drop=True)
    t = pd.concat([t.drop(columns="pos"), mc], axis=1)
    t["tell_margin_per10k"] = t["tell_margin"] / t["V"] * 1e4
    t["tell_P_per10k"] = t["tell_P"] / t["V"] * 1e4
    flag = flag_tells(t)
    t["tell_rank"] = pd.Series(np.where(flag, t["tell_early"].where(flag).rank(ascending=False, method="first"), np.nan)).astype("Int64")
    t["tell_top"] = flag
    # race summary and the raw-count path at benchmark
    bench_m = float((V * (2 * s0 - 1)).sum())
    path = []
    cm = np.concatenate([[0.0], np.cumsum(V[order] * (2 * s0[order] - 1))])
    pace = pacing_curves(E["state"], pri, otab, E["poll_close_utc"])
    qgrid = None if pace is None else (pace * V[:, None]).sum(axis=0) / totV
    for q in CHECKS:
        k = min(int(np.searchsorted(cumV, q - 1e-12)), N)
        raw_o = float(np.interp(q, cumV, cm) / (q * totV))               # whole counties in expected order, the straddling one partly
        row = {"q": q, "final_margin_pts": 100 * bench_m / totV, "counties_in_order": k, "counted_frac_order": float(cumV[k]),
               "raw_margin_pts_order": 100 * raw_o, "gap_pts_order": 100 * (raw_o - bench_m / totV),
               "nowcast_p_win_order": float(p_pre[k]), "nowcast_margin_sd_pts_order": 100 * float(sd_pre[k]) / totV}
        if qgrid is not None and qgrid.max() >= q:
            tau = float(np.interp(q, qgrid, PACE_GRID))
            f = np.array([np.interp(tau, PACE_GRID, pace[i]) for i in range(N)])
            raw_p = float((f * V * (2 * s0 - 1)).sum() / (f * V).sum())
            rp = project_partial(cfg, pri, f, s0)
            row.update({"minutes_after_close": tau, "raw_margin_pts": 100 * raw_p, "gap_pts": 100 * (raw_p - bench_m / totV),
                        "nowcast_p_win": float(rp["p_win"]), "nowcast_margin_sd_pts": 100 * float(rp["margin_sd"]) / totV})
        path.append(row)
    top = t[t["tell_top"]].sort_values("tell_rank")
    run0 = pre[0]
    summ = {
        "race_id": spec["race_id"], "name": E["name"], "state": E["state"], "kind": spec["kind"], "config": spec["id"],
        "config_path": spec["cfg_path"], "prior_source": spec["source"], "prior": P["share"], "frozen": FROZEN,
        "statewide": {"mean": P["statewide_mean"], "sd": P["statewide_sd"], "p_win": P["p_win"],
                      "poll_n": P.get("poll_n"), "p_win_nowcast_precount": float(run0["p_win"]),
                      "margin_sd_pts_nowcast_precount": 100 * float(run0["margin_sd"]) / totV,
                      "benchmark_margin_pts": 100 * bench_m / totV, "benchmark_margin_votes": bench_m,
                      "expected_votes": totV, "units": N, "side_a": E["a"]["name"], "side_b": E["b"]["name"]},
        "params": {"tau": M.get("tau"), "rho": M.get("rho"), "sigma_mun0": M.get("sigma_mun0"),
                   "swing_prior_sd": M.get("swing_prior_sd"), "count_scaled_terms": M.get("count_scaled_terms"),
                   "f_complete": cfg.get("count", {}).get("f_complete"), "f_max": cfg.get("count", {}).get("f_max"),
                   "vote_type": cfg.get("count", {}).get("vote_type", False), "early_share": cfg.get("count", {}).get("early_share"),
                   "use_outstanding": cfg.get("count", {}).get("use_outstanding", False),
                   "learn_turnout": cfg.get("count", {}).get("learn_turnout", False),
                   "complete_fraction_used": complete_fraction(cfg)},
        "order": {"source": od["order_source"].iat[0], "early_cutoff_cum_share": EARLY_FRAC, "mc_orders": MC_SIMS, "order_noise_sd": SIGMA_ORDER,
                  "note": "expected order: 2/3 mean percentile of 2020/2024 presidential county completion times + 1/3 size percentile"},
        "benchmark_path": path,
        "top_tells": [{"unit_id": r.unit_id, "name": r.name_, "exp_rank": int(r.exp_rank), "p_early": round(float(r.p_early), 3),
                       "s0": round(float(r.s0), 4), "V": round(float(r.V), 0), "dP_dshare": round(float(r.dP_dshare), 3),
                       "dMargin_dshare": round(float(r.dMargin_dshare), 1), "tell_early": round(float(r.tell_early), 0),
                       "tell_margin": round(float(r.tell_margin), 0), "tell_P": round(float(r.tell_P), 3)}
                      for r in top.rename(columns={"name": "name_"}).itertuples()],
        "checks": {"max_rel_sd_change_under_perturbation": float(t["_sd_diff"].max()), "max_nonlinearity": float(t["_nonlin"].max())},
    }
    return t.drop(columns=["_sd_diff", "_nonlin"]), summ


# ------------------------------------------------------------------ build


CSV_COLS = ["race_id", "unit_id", "name", "office", "state", "kind", "s0", "V", "turnout_share", "margin_votes",
            "statewide_mean", "statewide_sd", "p_win", "resid_sd_pts", "band_lo", "band_hi", "band_state_lo", "band_state_hi",
            "exp_rank", "exp_cum_before", "exp_cum_after", "exp_minutes", "order_source",
            "dMargin_dshare", "dMargin_direct", "dMargin_prop", "dP_dshare", "dP_direct", "dP_prop",
            "p_early", "tell_margin", "tell_margin_per10k", "tell_P", "tell_P_per10k", "tell_early", "sd_reduction", "sd_reduction_frac",
            "tell_rank", "tell_top"]
ROUND = {"s0": 5, "V": 0, "turnout_share": 6, "margin_votes": 0, "statewide_mean": 4, "statewide_sd": 4, "p_win": 4,
         "resid_sd_pts": 2, "band_lo": 4, "band_hi": 4, "band_state_lo": 4, "band_state_hi": 4, "exp_cum_before": 4,
         "exp_cum_after": 4, "exp_minutes": 0, "dMargin_dshare": 1, "dMargin_direct": 1, "dMargin_prop": 1, "dP_dshare": 4,
         "dP_direct": 4, "dP_prop": 4, "p_early": 3, "tell_margin": 0, "tell_margin_per10k": 2, "tell_P": 4, "tell_P_per10k": 4,
         "tell_early": 0, "sd_reduction": 0, "sd_reduction_frac": 4}

HEADER = """# Count-night benchmarks for the 2026 statewide and House races, frozen {frozen}: where the votes should land before they arrive (analysis/us_benchmarks.py; docs/us_benchmarks.md).
# One row per county (House: the county's part of the district). s0 = expected two-party Democratic share, V = expected two-party votes,
# turnout_share = V / race total, margin_votes = V (2 s0 - 1). statewide_mean / sd / p_win = the config's [prior] (unified model, {frozen}).
# resid_sd_pts = sd of the county's final share given the statewide share (leave-one-cycle-out county priors 2016-2024, by county size);
#   band_lo/hi = 5-95 % band on that sd if the statewide result is on benchmark, band_state_lo/hi = also with the statewide sd.
# exp_rank / exp_cum_before / exp_cum_after / exp_minutes = expected reporting order (2020 and 2024 presidential count feeds + county size),
#   the share of the race's votes in before / after the county completes, minutes after poll close to 95 % of its vote.
# dMargin_dshare = votes of statewide margin per +1 point of this county's final share (nowcast: own votes dMargin_direct + propagated dMargin_prop);
#   dP_dshare = points of P(win) per +1 point (dP_direct + dP_prop), both after the counties expected before it, all on benchmark.
# tell_margin = expected |change| of the projected statewide margin (votes) when the county reports, averaged over {n} random reporting orders around
#   the expected one (county complete, the counties before it on benchmark, its share drawn from N(benchmark, statewide sd^2 + resid sd^2));
#   tell_P the same for P(win) in points; *_per10k per 10,000 votes of the county; p_early = share of orders in which it completes within the first
#   {e:.0%} of the vote; tell_early = expected |change of the margin| counted only when it reports early; sd_reduction = mean fall of the margin
#   sd (votes; negative = the nowcast adds reporting-order uncertainty). tell_top = top {k} tell_early among counties with p_early >= 0.5.
# Inputs: unified county priors (MEDSL CC0, state Secretary of State records, Census ACS/PEP, VoteHub CC BY 4.0, FiveThirtyEight CC BY 4.0),
#   NYT/Edison 2020 county feed and CNN/Edison 2024 count feed (Harvard Dataverse, CC0) for the order only. No 2026 count data.
"""


def build() -> None:
    specs, lacking = race_specs()
    otab, prof = load_order_table(), load_resid_profile()
    OUT_JSON.mkdir(parents=True, exist_ok=True)
    for f in OUT_JSON.glob("*.json"):
        f.unlink()
    frames, index = [], []
    for sp in specs:
        t, summ = race_table(sp, otab, prof)
        E, P = sp["cfg"]["election"], sp["cfg"]["prior"]
        t = t.assign(race_id=sp["race_id"], office="house" if sp["kind"] == "house" else E["race"], state=E["state"],
                     kind="county_piece" if sp["kind"] == "house" else "county", statewide_mean=P["statewide_mean"],
                     statewide_sd=P["statewide_sd"], p_win=P["p_win"])
        frames.append(t[CSV_COLS])
        fn = sp["race_id"].replace("house:", "house_").lower() + ".json"
        (OUT_JSON / fn).write_text(json.dumps(_clean(summ), indent=1) + "\n")
        index.append((sp["race_id"], sp["source"], len(t), summ["statewide"]["p_win_nowcast_precount"], P["p_win"],
                      float(t["tell_top"].sum())))
        print(f"{sp['race_id']:12s} {sp['source']:8s} n={len(t):4d} P(config)={P['p_win']:.3f} P(nowcast)={summ['statewide']['p_win_nowcast_precount']:.3f}"
              f"  maxnonlin={summ['checks']['max_nonlinearity']:.3f}", flush=True)
    df = pd.concat(frames, ignore_index=True)
    for c, nd in ROUND.items():
        df[c] = df[c].round(nd)
    df["tell_top"] = df["tell_top"].astype(int)
    with open(OUT_CSV, "w") as f:
        f.write(HEADER.format(frozen=FROZEN, k=TOP_K, e=EARLY_FRAC, n=MC_SIMS))
        df.to_csv(f, index=False)
    print(f"wrote {OUT_CSV} ({len(df)} rows, {df['race_id'].nunique()} races); races lacking a unified prior: {lacking or 'none'}")


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


# ------------------------------------------------------------------ order table from the 2020 / 2024 presidential count feeds


def completion_table(df: pd.DataFrame, key: str, tcol: str, vcol: str) -> pd.DataFrame:
    """Per unit: final votes (running maximum of the feed), first report, time of 50 % and 95 % of the final vote."""
    out = []
    for k, g in df.groupby(key):
        g = g.sort_values(tcol)
        cm = g[vcol].cummax().to_numpy()
        fin = cm[-1]
        if fin <= 0:
            continue
        t = g[tcol].to_numpy()
        out.append({key: k, "final_votes": fin, "t_first": t[np.argmax(cm > 0)], "t10": t[np.argmax(cm >= 0.10 * fin)],
                    "t25": t[np.argmax(cm >= 0.25 * fin)], "t50": t[np.argmax(cm >= 0.5 * fin)],
                    "t75": t[np.argmax(cm >= 0.75 * fin)], "t95": t[np.argmax(cm >= 0.95 * fin)],
                    "t_final": t[np.argmax(cm >= fin)]})
    return pd.DataFrame(out)


def build_order() -> None:
    """data_public/us2026_benchmarks_order.csv: per state and county, when the county reached 50 / 95 % of its final two-party
    presidential vote in the 2020 NYT/Edison county feed (Harvard Dataverse doi:10.7910/DVN/LZXR5A, CC0, UTC times) and in the 2024
    CNN/Edison county feed (doi:10.7910/DVN/2CKSIK, CC0; overcounts relative to certified, used for timing only)."""
    abbr = {"Alabama": "al", "Alaska": "ak", "Arizona": "az", "Arkansas": "ar", "California": "ca", "Colorado": "co", "Connecticut": "ct",
            "Delaware": "de", "District of Columbia": "dc", "Florida": "fl", "Georgia": "ga", "Hawaii": "hi", "Idaho": "id",
            "Illinois": "il", "Indiana": "in", "Iowa": "ia", "Kansas": "ks", "Kentucky": "ky", "Louisiana": "la", "Maine": "me",
            "Maryland": "md", "Massachusetts": "ma", "Michigan": "mi", "Minnesota": "mn", "Mississippi": "ms", "Missouri": "mo",
            "Montana": "mt", "Nebraska": "ne", "Nevada": "nv", "New Hampshire": "nh", "New Jersey": "nj", "New Mexico": "nm",
            "New York": "ny", "North Carolina": "nc", "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok", "Oregon": "or",
            "Pennsylvania": "pa", "Rhode Island": "ri", "South Carolina": "sc", "South Dakota": "sd", "Tennessee": "tn",
            "Texas": "tx", "Utah": "ut", "Vermont": "vt", "Virginia": "va", "Washington": "wa", "West Virginia": "wv",
            "Wisconsin": "wi", "Wyoming": "wy"}
    n20 = pd.read_csv(US2024 / "nyt2020" / "nyt_counties.tab", sep="\t", low_memory=False,
                      usecols=["fips", "state", "time", "trumpd", "bidenj"])
    n20 = n20.assign(st=n20["state"].map(abbr), t=pd.to_datetime(n20["time"]), v=n20["trumpd"] + n20["bidenj"],
                     fips5=n20["fips"].astype(int).astype(str).str.zfill(5))
    parts = []
    for st, g in n20.groupby("st"):
        parts.append(completion_table(g, "fips5", "t", "v").assign(state=st, cycle=2020))
    e = pd.read_parquet(US2024 / "enr2024" / "county_ENR_president.parquet",
                        columns=["state", "candidate", "votes", "datetime", "jurisdiction_fips"])
    e = e[e["candidate"].isin(["Harris", "Trump"])]
    p = e.pivot_table(index=["state", "jurisdiction_fips", "datetime"], columns="candidate", values="votes", aggfunc="last",
                      observed=True).reset_index()
    # the ENR file's clock is US Eastern (first Georgia votes 19:15 on election night, polls close 19:00 ET), not UTC
    et = pd.to_datetime(p["datetime"]).dt.tz_localize("America/New_York").dt.tz_convert("UTC").dt.tz_localize(None)
    p = p.assign(v=p["Harris"] + p["Trump"], t=et, fips5=p["jurisdiction_fips"].astype(str).str.zfill(5))
    for st, g in p.groupby("state", observed=True):
        parts.append(completion_table(g, "fips5", "t", "v").assign(state=str(st).lower(), cycle=2024))
    o = pd.concat(parts, ignore_index=True)
    for c in ORDER_T:
        o[c] = pd.to_datetime(o[c]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    o = o[["state", "cycle", "fips5", "final_votes", *ORDER_T]].sort_values(["state", "cycle", "fips5"])
    hdr = ("# When each county reached 10, 25, 50, 75, 95 and 100 % of its final two-party presidential vote (UTC), 2020 and 2024 count nights: the typical\n"
           "# reporting order for analysis/us_benchmarks.py. 2020: New York Times / Edison county feed (Harvard Dataverse doi:10.7910/DVN/LZXR5A,\n"
           "# CC0 1.0); 2024: CNN / Edison county feed mirrored by MEDSL (doi:10.7910/DVN/2CKSIK, CC0 1.0; the county file overcounts relative to\n"
           "# certified returns, used for timing only; Vermont 2024 is by town and does not match counties). final_votes = running maximum of the\n"
           "# feed. Descriptive; no 2026 data.\n")
    with open(ORDER_CSV, "w") as f:
        f.write(hdr)
        o.to_csv(f, index=False)
    print(f"wrote {ORDER_CSV}: {len(o)} rows")


# ------------------------------------------------------------------ leave-one-cycle-out county priors (backtest inputs)


def loco_frames() -> pd.DataFrame:
    """Per past statewide race (2016-2024): the unified county prior fitted WITHOUT its cycle (as analysis/us_unified_backtest.py
    county_backtest part b: predicted statewide share mu and sd, predicted expected votes) beside the certified result."""
    import us_unified_backtest as bt
    from brunder import us_unified as uu
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    R = uu.race_table(inp)
    res = bt.state_backtest(R, n, {"extras": (), "lam": 0.0})
    C = bt.prep_county(inp, R)
    key = res.assign(race=lambda d: d["t"].astype(str) + "_" + d["st"] + "_" + d["office"] + d["special"].astype(int).astype(str)).set_index("race")
    gb = bt.state_base_votes(C)
    out = []
    for c in bt.TEST:
        tr, te = C[(C["t"] != c) & (C["t"].isin(bt.TEST))], C[C["t"] == c]
        lam = uu.select_lam(tr)
        sm = uu.fit_share(tr, lam)
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = bt.fit_vstate(gb[gb["t"] != c])
        for race, d in te.groupby("race"):
            if race not in key.index or d["s2"].isna().any():
                continue
            k = key.loc[race]
            vp, vo = float(d["vp_b"].sum()), float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = bt.predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            pr = uu.county_prior(d, sm, tm, float(k["mu"]), Vs)
            dev_u = uu.predict_dev(sm, d) + sm["b0"]
            out.append(pd.DataFrame({
                "race": race, "t": c, "st": k["st"], "office": k["office"], "special": bool(k["special"]), "fips5": d["fips"].astype(str).str.zfill(5).values,
                "s0": pr["s0"].values, "V": pr["V"].values, "s_true": d["s2"].values, "v_true": d["v2"].values,
                "S_act": float(d["S_act"].iloc[0]), "dev_given_true": dev_u, "mu": float(k["mu"]), "sd": float(k["sd"]),
                "n_polls": int(k["n_polls"])}))
    return pd.concat(out, ignore_index=True)


RESID_EDGES = (0, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 1e9)


def resid_profile(F: pd.DataFrame, exclude_cycle: int | None = None) -> pd.DataFrame:
    """County error given the TRUE statewide share (certified share - (true state share + unified county deviation)), by county
    expected votes: the county residual of the model. exclude_cycle drops a cycle (used inside the backtest)."""
    d = F if exclude_cycle is None else F[F["t"] != exclude_cycle]
    e = d["s_true"] - (d["S_act"] + d["dev_given_true"])
    b = pd.cut(d["V"], list(RESID_EDGES))
    g = pd.DataFrame({"e": e, "V": d["V"], "b": b}).groupby("b", observed=True)
    out = pd.DataFrame({"bin_lo": [iv.left for iv in g.size().index], "bin_hi": [iv.right for iv in g.size().index],
                        "n": g.size().values, "median_V": g["V"].median().values,
                        "rmse_pts": 100 * np.sqrt(g["e"].apply(lambda x: (x**2).mean()).values),
                        "mae_pts": 100 * g["e"].apply(lambda x: x.abs().mean()).values})
    return out


def build_resid() -> pd.DataFrame:
    F = loco_frames()
    F.to_parquet(_cache("loco_county_priors.parquet"))
    prof = resid_profile(F)
    wr = 100 * np.sqrt(np.average((F["s_true"] - (F["S_act"] + F["dev_given_true"])) ** 2, weights=F["v_true"]))
    hdr = ("# County residual sd of the unified county prior given the statewide share, by county expected votes (leave-one-cycle-out, 260 statewide\n"
           "# races of 2016-2024; counties of every size pooled; vote-weighted RMSE over all counties = {:.2f} points). rmse_pts = sqrt(mean((certified county\n"
           "# share - (true state share + predicted county deviation))^2)). Used by analysis/us_benchmarks.py for the county bands and tell scores.\n").format(wr)
    with open(RESID_CSV, "w") as f:
        f.write(hdr)
        prof.round(3).to_csv(f, index=False)
    print(prof.round(2).to_string(index=False), f"\nvote-weighted rmse {wr:.2f}")
    return prof


def _cache(name: str) -> Path:
    d = DATA / "processed" / "us2026" / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


# ------------------------------------------------------------------ vectorised nowcast (verified against brunder.us.project)

BT_PAR = {"tau": 0.04, "rho": 0.6, "sigma_mun0": 0.08, "kappa": 50_000.0, "nu_mun": 20.0, "sigma_uf0": 0.03, "nu_uf": 5.0,
          "turnout_prior_votes": 200_000.0, "fc": 0.98, "fdiv": 0.98, "learn": True, "vote_type": False, "early_share": 0.6,
          "count_scaled": True, "scale_mode": "weight"}


def par_from_cfg(cfg: dict) -> dict:
    """Settings of fast_nowcast from a race config (the model and count settings brunder.us.project would use)."""
    M, R = cfg.get("model", {}), us.rules(cfg)
    p = {**BT_PAR, "tau": M.get("tau", 0.01), "rho": M.get("rho", 0.8), "sigma_mun0": M.get("sigma_mun0", 0.06),
         "sigma_uf0": M.get("sigma_uf0", 0.03), "kappa": M.get("kappa", 50_000.0), "nu_mun": M.get("nu_mun", 20.0),
         "nu_uf": M.get("nu_uf", 5.0), "count_scaled": bool(M.get("count_scaled_terms", False)),
         "scale_mode": M.get("count_scale_mode", "weight"), "turnout_prior_votes": R.turnout_prior_votes,
         "vote_type": R.vote_type, "early_share": R.early_share, "fc": complete_fraction(cfg),
         "fdiv": 1.0 if R.use_outstanding else R.f_complete, "learn": bool(R.learn_turnout and (R.use_outstanding or R.f_complete > 0))}
    return p


def _post_var(x, w, s0, nu) -> float:
    n = len(x)
    wv = float(np.average((x - np.average(x, weights=w)) ** 2, weights=w)) if n >= 2 and w.sum() > 0 else 0.0
    return (nu * s0**2 + max(n - 1, 0) * wv) / (nu + max(n - 1, 0))


def nowcast_units(s0, V0, n, a, f, reg, nreg, sd_prior, par) -> dict:
    """brunder.nowcast.nowcast for units with regions `reg` (0..nreg-1), swing prior mean 0 and sd `sd_prior`, no Hispanic slope:
    the same formulas, vectorised."""
    tau, rho, kap = par["tau"], par["rho"], par["kappa"]
    counted = n > 0
    fV = (f * V0).sum()
    k = n.sum() / fV if fV > 0 else 1.0
    R = np.clip(1 - f, 0, None) * V0 * k
    d = np.where(counted, a / np.where(counted, n, 1.0) - s0, 0.0)
    W = np.bincount(reg, n, nreg)
    wd = np.bincount(reg, n * d, nreg)
    W2 = np.bincount(reg, n**2, nreg)
    delta = float((n * d).sum() / n.sum()) if counted.any() else 0.0
    delta_u = (wd + kap * delta) / (W + kap)
    resid = np.where(counted, d - delta_u[reg], 0.0)
    sig_m2 = _post_var(resid[counted], n[counted], par["sigma_mun0"], par["nu_mun"])
    pooled = W > 0
    raw_u = wd[pooled] / W[pooled]
    sig_b2 = _post_var(raw_u, W[pooled], par["sigma_uf0"], par["nu_uf"])
    lam = W / (W + kap)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = np.where(W > 0, lam**2 * sig_m2 * W2 / np.where(W > 0, W, 1.0) ** 2, 0.0)
    var_u = t1 + sig_b2 * (1 - lam)
    Wp, W2p = W[pooled], W2[pooled]
    var_delta = (sig_m2 * W2p.sum() + sig_b2 * (Wp**2).sum()) / Wp.sum() ** 2 if Wp.sum() > 0 else 0.0
    twoR = 2 * R
    twoR_u = np.bincount(reg, twoR, nreg)
    var_mun = float((twoR**2 * sig_m2 * np.where(counted, 1 - f, 1.0)).sum())
    var_uf = float((twoR_u**2 * var_u).sum())
    var_nat = twoR.sum() ** 2 * tau**2 + float((twoR_u * (1 - lam)).sum()) ** 2 * var_delta
    s2 = sd_prior**2
    var_obs = np.inf
    if Wp.sum() > 0:
        var_obs = sig_m2 * W2p.sum() / Wp.sum() ** 2
        if nreg > 1:
            var_obs += sig_b2 * (Wp**2).sum() / Wp.sum() ** 2
    w_obs = s2 / (s2 + var_obs) if np.isfinite(var_obs) else 0.0
    delta_post = w_obs * delta
    var_post = (1 - w_obs) * s2
    delta_u = delta_u + (delta_post - delta)
    resid_post = np.where(counted, d - delta_u[reg], 0.0)
    s_hat = np.clip(s0 + delta_u[reg] + rho * resid_post * counted, 0, 1)
    scale = (w_obs if par["scale_mode"] == "weight" else float(np.isfinite(var_obs))) if par["count_scaled"] else 1.0
    var_uf *= scale
    var_nat = twoR.sum() ** 2 * tau**2 * scale + twoR_u.sum() ** 2 * var_post
    sd = float(np.sqrt(var_mun + var_uf + var_nat))
    m = float((2 * a - n).sum() + (R * (2 * s_hat - 1)).sum())
    tot = float(n.sum() + R.sum())
    return {"margin_hat": m, "margin_sd": sd, "p_win": float(norm.cdf(m / sd)) if sd > 0 else float(m > 0),
            "share_hat": 0.5 + m / (2 * tot)}


def fast_nowcast(s0, V, counted, s_obs, v_obs, sd_prior, par=BT_PAR) -> dict:
    """brunder.us.count_state + _model_units + nowcast for counties that are either complete (feed: fc * v_obs votes at share
    s_obs, early + mail batch first when vote_type) or not reported. Same numbers as us.project on that feed (check_fast)."""
    fc = par["fc"]
    n = np.where(counted, v_obs * fc, 0.0)
    T = 1.0
    if par["learn"] and counted.any():
        T = ((n[counted] / par["fdiv"]).sum() + par["turnout_prior_votes"]) / (V[counted].sum() + par["turnout_prior_votes"])
    E = np.where(counted, n / par["fdiv"] if par["fdiv"] > 0 else np.maximum(V * T, n), V * T)
    N = len(s0)
    if not par["vote_type"]:
        f = np.where(counted, np.minimum(n / E, 1.0), 0.0)
        return nowcast_units(s0, E, n, n * s_obs, f, np.zeros(N, int), 1, sd_prior, par)
    ne = np.where(counted, par["early_share"] * n, 0.0)
    Ee = np.where(counted, ne, par["early_share"] * E)
    El = np.maximum(E - Ee, 0.02 * E)
    nl = n - ne
    fl = np.where(counted, np.minimum(nl / El, 1.0), 0.0)
    return nowcast_units(np.concatenate([s0, s0]), np.concatenate([Ee, El]), np.concatenate([ne, nl]),
                         np.concatenate([ne * s_obs, nl * s_obs]), np.concatenate([counted.astype(float), fl]),
                         np.concatenate([np.zeros(N, int), np.ones(N, int)]), 2, sd_prior, par)


def _xx_rows(pri, cfg, pos, share, n) -> pd.DataFrame:
    if len(pos) == 0:
        return us._frame([])
    R, E = us.rules(cfg), cfg["election"]
    parts = []
    for party, sh in ((E["a"]["party"], share[pos]), (E["b"]["party"], 1 - share[pos])):
        v = n[pos] * sh
        parts.append(pd.DataFrame({"level": "county", "unit_id": pri["unit_id"].to_numpy()[pos], "name": "", "race": E["race"],
                                   "candidate": "", "party": party, "votes": v, "early": v * R.early_share if R.vote_type else np.nan,
                                   "reporting": 1.0, "units": 1.0, "ballots": n[pos] if R.use_outstanding else np.nan, "registered": np.nan,
                                   "outstanding": 0.0 if R.use_outstanding else np.nan}))
    return pd.concat(parts, ignore_index=True)


def check_fast(F: pd.DataFrame, specs: list[dict], n_check: int = 10) -> float:
    """Max |difference| (votes) of margin and margin sd between fast_nowcast and us.project: on 2026 configs (all count-rule
    variants, random counted sets, random true shares and votes) and on backtest races (one unit per county rules)."""
    rng = np.random.default_rng(7)
    worst = 0.0
    cases = []
    kinds = {}
    for s in specs:
        c = s["cfg"]
        key = (c["count"].get("vote_type", False), c["count"].get("f_complete", 0) > 0, c["count"].get("use_outstanding", False),
               c["count"].get("learn_turnout", False))
        if key not in kinds and len(s["pri"]) >= 5:
            kinds[key] = s
    for s in list(kinds.values()) + [s for s in specs if s["race_id"] in ("ga-gov", "tx-sen", "ca-gov")]:
        cases.append((s["cfg"], s["pri"]))
    for r in rng.choice(sorted(F["race"].unique()), n_check // 2, replace=False):
        d = F[F["race"] == r].reset_index(drop=True)
        pri = pd.DataFrame({"unit_id": [f"us-xx-{i:05d}" for i in range(len(d))], "name": "", "s0": d["s0"].to_numpy(), "V": d["V"].to_numpy()})
        cfg = {"election": {"state": "xx", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
               "model": {"tau": BT_PAR["tau"], "rho": BT_PAR["rho"], "sigma_mun0": BT_PAR["sigma_mun0"], "swing_prior_mean": 0.0,
                         "swing_prior_sd": float(d["sd"].iat[0]), "count_scaled_terms": True},
               "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "turnout_ratio": 1.0}}
        cases.append((cfg, pri))
    for cfg, pri in cases:
        par = par_from_cfg(cfg)
        N = len(pri)
        s0, V = pri["s0"].to_numpy(float), pri["V"].to_numpy(float)
        sdp = float(cfg["model"]["swing_prior_sd"])
        for frac in (0.0, 0.05, 0.2, 0.5, 0.9):
            m = rng.random(N) < frac
            sh = np.clip(s0 + rng.normal(0, 0.05, N), 0.02, 0.98)
            v = V * rng.uniform(0.7, 1.4, N)
            rows = _xx_rows(pri, cfg, np.where(m)[0], sh, v * par["fc"])
            if par["fdiv"] == 0 or not par["learn"]:
                v = V                                     # rules without a completeness divisor do not use the true total
                rows = _xx_rows(pri, cfg, np.where(m)[0], sh, v * par["fc"])
            res = us.project(rows, cfg, pri)[0]
            fast = fast_nowcast(s0, V, m, sh, v, sdp, par)
            worst = max(worst, abs(res["margin_hat"] - fast["margin_hat"]), abs(res["margin_sd"] - fast["margin_sd"]))
    return worst


# ------------------------------------------------------------------ backtest: orders, tells and the estimators


def order_calibrate(V: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Size-dependent random completion order as analysis/us_calibrate.py events(): early batch at U(0, 60) + 30 rank, the
    last of four Election Day chunks U(30, 240 + 120 rank) later (rank = size percentile 0 small ... 1 large)."""
    rk = V.argsort().argsort() / max(len(V) - 1, 1)
    t = rng.uniform(0, 60, len(V)) + 30 * rk + rng.uniform(30, 240 + 120 * rk[:, None], (len(V), 4)).max(axis=1)
    return np.argsort(t, kind="stable")


def order_weak(V: np.ndarray, rng: np.random.Generator, sigma: float = 0.77) -> np.ndarray:
    """Size percentile + N(0, sigma^2) noise: Spearman(size, time) about 0.35, the 2020/2024 feeds' average."""
    rk = pd.Series(V).rank().to_numpy() / len(V)
    return np.argsort(rk + sigma * rng.standard_normal(len(V)), kind="stable")


TELL_SIMS = 60                   # random orders per past race for its ex-ante tells
SIGMA_ORDER = 0.57               # order noise (sd, in percentile units) of the 2026 expected order: Spearman(expected, realised) about 0.45
MC_SIMS = 80


def mc_tells(s0, V, sd_state, rsd, sampler, par, sd_prior, n_sims, rng) -> pd.DataFrame:
    """Order-averaged tells. For each of n_sims random reporting orders (sampler(rng) -> positions in order), county c reports
    complete after the counties before it (all on benchmark) and the nowcast (fast_nowcast, = us.project) is differenced at
    +-1 point of its share: g = d margin / d share (votes). With a deviation drawn from N(0, s_delta^2), s_delta^2 = sd_state^2 +
    resid_sd(c)^2 (the statewide shift every county shares + the county's own residual), the expected |change| of the projected
    margin is |g| s_delta sqrt(2/pi) and of P(win) E|Phi((m + g x)/sd) - Phi(m/sd)|. Averaged over the orders the county reports in:
    p_early = share of orders in which it completes within the first EARLY_FRAC of the vote; tell_margin / tell_P over all positions;
    tell_early = E[|change of margin| ; completes early]; g_early = E[|g| | early]; sd_reduction = mean fall of the margin sd."""
    N = len(s0)
    totV = V.sum()
    s_delta = np.hypot(sd_state, rsd)
    acc = {k: np.zeros(N) for k in ("m", "p", "early_m", "early_g", "sdred", "sdred_frac", "early")}
    cnt0 = np.zeros(N, bool)
    sd_start = fast_nowcast(s0, V, cnt0, s0, V, sd_prior, par)["margin_sd"]
    for _ in range(n_sims):
        order = sampler(rng)
        cum = np.cumsum(V[order]) / totV
        cnt = np.zeros(N, bool)
        sd_prev = sd_start
        for j in range(N):
            i = order[j]
            cnt[i] = True
            base = fast_nowcast(s0, V, cnt, s0, V, sd_prior, par)
            d = min(DELTA, s0[i] - 0.001, 0.999 - s0[i])
            so = s0.copy()
            so[i] = s0[i] + d
            up = fast_nowcast(s0, V, cnt, so, V, sd_prior, par)["margin_hat"]
            so[i] = s0[i] - d
            dn = fast_nowcast(s0, V, cnt, so, V, sd_prior, par)["margin_hat"]
            g = (up - dn) / (2 * d)
            tm = abs(g) * s_delta[i] * np.sqrt(2 / np.pi)
            acc["m"][i] += tm
            acc["p"][i] += exp_abs_dp(base["margin_hat"], base["margin_sd"], g, s_delta[i])
            acc["sdred"][i] += sd_prev - base["margin_sd"]
            acc["sdred_frac"][i] += (sd_prev - base["margin_sd"]) / sd_prev
            if cum[j] <= EARLY_FRAC:
                acc["early"][i] += 1
                acc["early_m"][i] += tm
                acc["early_g"][i] += abs(g)
            sd_prev = base["margin_sd"]
    ne = np.maximum(acc["early"], 1)
    return pd.DataFrame({"p_early": acc["early"] / n_sims, "tell_margin": acc["m"] / n_sims, "tell_P": 100 * acc["p"] / n_sims,
                         "tell_early": acc["early_m"] / n_sims, "g_early": acc["early_g"] / ne,
                         "sd_reduction": acc["sdred"] / n_sims, "sd_reduction_frac": acc["sdred_frac"] / n_sims})


def flag_tells(t: pd.DataFrame, k: int = TOP_K) -> np.ndarray:
    """Top k by tell_early among counties that complete within the first EARLY_FRAC of the vote in at least half of the orders."""
    ok = t[t["p_early"] >= 0.5]
    flag = np.zeros(len(t), bool)
    flag[ok["tell_early"].nlargest(k).index.to_numpy()] = True
    return flag


def est_tell(s0, V, counted, s_obs, v_obs, w, rsd, sd_state) -> float:
    """'Tell-weighted' final share: certified counted counties + for the others the benchmark moved by the shrunk, sensitivity-
    weighted mean deviation of the reported tell counties (weights w = their ex-ante |dMargin/dshare|; 0 = not a tell). With no tell
    reported yet the others stay on benchmark. The shrinkage is the Bayes gain of a statewide deviation (sd sd_state) seen through
    the weighted mean of county residuals (rsd)."""
    T = 1.0
    if counted.any():
        T = (v_obs[counted].sum() + BT_PAR["turnout_prior_votes"]) / (V[counted].sum() + BT_PAR["turnout_prior_votes"])
    sel = counted & (w > 0)
    shift = 0.0
    if sel.any():
        ww = w[sel]
        dev = float((ww * (s_obs[sel] - s0[sel])).sum() / ww.sum())
        v_noise = float((ww**2 * rsd[sel] ** 2).sum() / ww.sum() ** 2)
        shift = sd_state**2 / (sd_state**2 + v_noise) * dev
    un = ~counted
    num = (v_obs[counted] * s_obs[counted]).sum() + (V[un] * T * np.clip(s0[un] + shift, 0, 1)).sum()
    den = v_obs[counted].sum() + (V[un] * T).sum()
    return float(num / den)


def backtest(n_sim: int = 40, only: list[int] | None = None) -> None:
    RES.mkdir(parents=True, exist_ok=True)
    cache = _cache("loco_county_priors.parquet")
    F = pd.read_parquet(cache) if cache.exists() else (build_resid(), pd.read_parquet(cache))[1]
    otab = load_order_table()
    worst = check_fast(F, race_specs()[0])
    print(f"fast nowcast vs brunder.us.project: max |difference| {worst:.3g} (votes / margin sd)", flush=True)
    assert worst < 1e-3, "fast nowcast deviates from brunder.us.project"
    rng = np.random.default_rng(20261103)
    races = F.groupby("race")
    race_rows, info_rows, tell_rows, band_rows = [], [], [], []
    CK = (0.05, 0.10, 0.25, 0.50)
    for r, d in races:
        t = int(d["t"].iat[0])
        if only and t not in only:
            continue
        d = d.reset_index(drop=True)
        N = len(d)
        if N < 10 or d["V"].min() <= 0:
            continue
        s0, V = d["s0"].to_numpy(float), d["V"].to_numpy(float)
        s_true, v_true = d["s_true"].to_numpy(float), d["v_true"].to_numpy(float)
        S_act = float((s_true * v_true).sum() / v_true.sum())
        sd_state = float(d["sd"].iat[0])
        prof = resid_profile(F, exclude_cycle=t)
        rsd = resid_sd(V, prof)
        band_rows.append(pd.DataFrame({"t": t, "V": V, "cond": np.abs(s_true - (S_act + d["dev_given_true"].to_numpy())) <= 1.645 * rsd,
                                       "state": np.abs(s_true - s0) <= 1.645 * np.hypot(sd_state, rsd)}))
        mc = mc_tells(s0, V, sd_state, rsd, lambda rg: order_weak(V, rg), BT_PAR, sd_state, TELL_SIMS,
                      np.random.default_rng(zlib.crc32(r.encode())))              # ex-ante belief: smaller counties tend to report first
        flag = flag_tells(mc)
        w_tell = np.where(flag, mc["g_early"].to_numpy(), 0.0)
        w_all = V.copy()
        bench = float((s0 * V).sum() / V.sum())
        st = d["st"].iat[0]
        real = None
        if t in (2020, 2024):
            sub = otab[(otab["state"] == st) & (otab["cycle"] == t)].drop_duplicates("fips5").set_index("fips5")
            tt = pd.to_datetime(sub["t95"], utc=True).reindex(d["fips5"].values)
            if tt.notna().mean() >= 0.9:
                tv = tt.astype("int64").where(tt.notna(), np.nan)
                tv = tv.fillna(tv.max())
                real = np.argsort(tv.to_numpy(), kind="stable")
        schemes = [("calibrate", n_sim), ("weak", n_sim)] + ([("real", 1)] if real is not None else [])
        for scheme, ns in schemes:
            acc = {}
            for s in range(ns):
                order = (order_calibrate(V, rng) if scheme == "calibrate" else order_weak(V, rng) if scheme == "weak" else real)
                cum = np.cumsum(v_true[order]) / v_true.sum()
                steps = {}
                for q in CK:
                    k = int(np.searchsorted(cum, q - 1e-12))
                    steps[q] = k
                for q, k in steps.items():
                    c = np.zeros(N, bool)
                    c[order[:k + 1]] = True
                    raw = float((s_true[c] * v_true[c]).sum() / v_true[c].sum())
                    nw = fast_nowcast(s0, V, c, s_true, v_true, sd_state)
                    ests = {"benchmark": bench, "raw": raw, "nowcast": nw["share_hat"],
                            "tell": est_tell(s0, V, c, s_true, v_true, w_tell, rsd, sd_state),
                            "tell_all": est_tell(s0, V, c, s_true, v_true, w_all, rsd, sd_state)}
                    for nm, e in ests.items():
                        a = acc.setdefault((q, nm), [])
                        a.append((e - S_act, int(np.sign(e - 0.5) != np.sign(S_act - 0.5)), float(c.sum()), float(cum[k])))
                # realised information of each county reporting in the first 40 %: |change of the nowcast margin share|
                if s < min(ns, 10):
                    c = np.zeros(N, bool)
                    prev = fast_nowcast(s0, V, c, s_true, v_true, sd_state)["share_hat"]
                    for j in range(N):
                        i = order[j]
                        if cum[j] > EARLY_FRAC:
                            break
                        c[i] = True
                        cur = fast_nowcast(s0, V, c, s_true, v_true, sd_state)["share_hat"]
                        info_rows.append({"race": r, "t": t, "scheme": scheme, "sim": s, "pos": i, "step": j, "V": V[i],
                                          "tell_margin": mc["tell_margin"].iat[i], "tell_early": mc["tell_early"].iat[i],
                                          "p_early": mc["p_early"].iat[i], "g": mc["g_early"].iat[i], "tell_P": mc["tell_P"].iat[i],
                                          "dev": s_true[i] - s0[i], "info": abs(cur - prev), "flag": bool(flag[i])})
                        prev = cur
            for (q, nm), a in acc.items():
                x = np.array(a, float)
                race_rows.append({"race": r, "t": t, "st": st, "office": d["office"].iat[0], "scheme": scheme, "check": q, "est": nm,
                                  "mae": np.abs(x[:, 0]).mean(), "mse": (x[:, 0] ** 2).mean(), "bias": x[:, 0].mean(), "wrong": x[:, 1].mean(),
                                  "counted_frac": x[:, 3].mean(), "S_act": S_act, "bench": bench, "sd_state": sd_state, "n": N})
        tell_rows.append({"race": r, "t": t, "st": st, "n": N, "S_act": S_act, "bench": bench, "ntell": int(flag.sum()),
                          "tell_mean_V": float(V[flag].mean()), "tell_mean_p_early": float(mc["p_early"][flag].mean()),
                          "names": ";".join(d["fips5"][flag])})
        if len(race_rows) // 4000 != (len(race_rows) - 40) // 4000:
            print(f"  ... {r} done", flush=True)
    R = pd.DataFrame(race_rows)
    I = pd.DataFrame(info_rows)
    R.to_csv(RES / "backtest_race_level.csv", index=False)
    I.to_parquet(_cache("backtest_info.parquet"))
    pd.DataFrame(tell_rows).to_csv(RES / "backtest_tell_flags.csv", index=False)
    B = pd.concat(band_rows, ignore_index=True)
    B["size"] = pd.cut(B["V"], list(RESID_EDGES)).astype(str)
    cov = pd.concat([B.groupby("t").agg(counties=("V", "size"), cover_given_state=("cond", "mean"), cover_with_state_sd=("state", "mean")).reset_index().astype(
        {"t": str}), B.groupby("size").agg(counties=("V", "size"), cover_given_state=("cond", "mean"), cover_with_state_sd=("state", "mean")).reset_index().rename(
        columns={"size": "t"}), B.agg({"cond": "mean", "state": "mean"}).to_frame().T.rename(columns={"cond": "cover_given_state", "state": "cover_with_state_sd"}).assign(
        t="all", counties=len(B))], ignore_index=True)
    cov.to_csv(RES / "backtest_band_coverage.csv", index=False)
    summarise(R, I)


# ------------------------------------------------------------------ backtest tables


def _boot_ci(x: np.ndarray, rng: np.random.Generator, reps: int = 2000) -> tuple[float, float]:
    if len(x) < 3:
        return (np.nan, np.nan)
    b = rng.choice(x, (reps, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def summarise(R: pd.DataFrame, I: pd.DataFrame) -> None:
    rng = np.random.default_rng(11)
    E = R
    # 1. error by cycle / scheme / checkpoint / estimator (races equally weighted; share points of the final two-party share)
    g = E.groupby(["scheme", "t", "check", "est"]).agg(races=("race", "nunique"), mae_pts=("mae", lambda x: 100 * x.mean()),
                                                      rmse_pts=("mse", lambda x: 100 * np.sqrt(x.mean())),
                                                      bias_pts=("bias", lambda x: 100 * x.mean()), wrong_call=("wrong", "mean")).reset_index()
    g.to_csv(RES / "backtest_error_by_cycle.csv", index=False)
    allc = E.groupby(["scheme", "check", "est"]).agg(races=("race", "nunique"), mae_pts=("mae", lambda x: 100 * x.mean()),
                                                     rmse_pts=("mse", lambda x: 100 * np.sqrt(x.mean())),
                                                     bias_pts=("bias", lambda x: 100 * x.mean()), wrong_call=("wrong", "mean")).reset_index()
    allc.to_csv(RES / "backtest_error_all_cycles.csv", index=False)
    cl = E[(E["S_act"] - 0.5).abs() < 0.05]
    cl.groupby(["scheme", "check", "est"]).agg(races=("race", "nunique"), mae_pts=("mae", lambda x: 100 * x.mean()),
                                              rmse_pts=("mse", lambda x: 100 * np.sqrt(x.mean())),
                                              wrong_call=("wrong", "mean")).reset_index().to_csv(RES / "backtest_error_close_races.csv", index=False)
    # 2. paired differences of race-level MAE, tell vs raw / nowcast / benchmark
    rows = []
    for (scheme, ck), d in E.groupby(["scheme", "check"]):
        for sub, mask in (("all 5 cycles", d["t"].notna()), ("2018+2022", d["t"].isin([2018, 2022])), ("2016", d["t"] == 2016),
                          ("2018", d["t"] == 2018), ("2020", d["t"] == 2020), ("2022", d["t"] == 2022), ("2024", d["t"] == 2024),
                          ("close races (margin under 10 pts)", (d["S_act"] - 0.5).abs() < 0.05)):
            dd = d[mask]
            if dd.empty:
                continue
            p = dd.pivot_table(index="race", columns="est", values="mae")
            for other in ("raw", "nowcast", "benchmark", "tell_all"):
                diff = 100 * (p["tell"] - p[other]).dropna().to_numpy()
                if len(diff) == 0:
                    continue
                lo, hi = _boot_ci(diff, rng)
                rows.append({"scheme": scheme, "check": ck, "subset": sub, "tell_minus": other, "races": len(diff),
                             "mean_mae_diff_pts": diff.mean(), "ci_lo": lo, "ci_hi": hi, "tell_better_share": float((diff < 0).mean())})
            for a, b in (("nowcast", "raw"), ("nowcast", "benchmark")):
                diff = 100 * (p[a] - p[b]).dropna().to_numpy()
                lo, hi = _boot_ci(diff, rng)
                rows.append({"scheme": scheme, "check": ck, "subset": sub, "tell_minus": f"[{a} minus {b}]", "races": len(diff),
                             "mean_mae_diff_pts": diff.mean(), "ci_lo": lo, "ci_hi": hi, "tell_better_share": float((diff < 0).mean())})
    pd.DataFrame(rows).to_csv(RES / "backtest_paired.csv", index=False)
    # 3. does the ex-ante tell score predict the realised information of a county?
    out = []
    if len(I):
        for (r, scheme, s), d in I.groupby(["race", "scheme", "sim"]):
            if len(d) < 6:
                continue
            row = {"race": r, "t": int(d["t"].iat[0]), "scheme": scheme, "n": len(d)}
            for nm, col in (("tell_margin", "tell_margin"), ("tell_early", "tell_early"), ("tell_P", "tell_P"), ("size", "V"), ("sensitivity", "g")):
                row[nm] = spearmanr(d[col], d["info"]).statistic
            out.append(row)
    S = pd.DataFrame(out)
    S.to_csv(RES / "backtest_info_race_sim.csv", index=False)
    if len(S):
        agg = S.groupby(["scheme", "t"]).agg(race_sims=("race", "size"), races=("race", "nunique"), rho_tell=("tell_margin", "mean"),
                                             rho_tell_early=("tell_early", "mean"), rho_tellP=("tell_P", "mean"), rho_size=("size", "mean"), rho_sens=("sensitivity", "mean")).reset_index()
        tot = S.groupby("scheme").agg(race_sims=("race", "size"), races=("race", "nunique"), rho_tell=("tell_margin", "mean"),
                                      rho_tell_early=("tell_early", "mean"), rho_tellP=("tell_P", "mean"), rho_size=("size", "mean"), rho_sens=("sensitivity", "mean")).reset_index().assign(t="all")
        pd.concat([agg, tot], ignore_index=True).to_csv(RES / "backtest_info_rank_corr.csv", index=False)
        # realised information of tell-flagged vs other early counties, per vote
        I2 = I.assign(per10k=I["info"] / I["V"] * 1e4 * 100)
        f = I2.groupby(["scheme", "flag"]).agg(counties=("info", "size"), info_pts_mean=("info", lambda x: 100 * x.mean()),
                                              info_pts_per10k_mean=("per10k", "mean"), mean_V=("V", "mean")).reset_index()
        f.to_csv(RES / "backtest_info_flagged.csv", index=False)
    print(allc[(allc["scheme"] == "calibrate")].round(3).to_string(index=False))


def order_validation() -> pd.DataFrame:
    """How well does one presidential cycle's county order (and county size) predict the other's? Spearman correlations per
    state, 2020 -> 2024 and 2024 -> 2020, for: size alone, the other cycle's completion time, and their blend (weights W)."""
    o = load_order_table()
    rows = []
    for st, g in o.groupby("state"):
        a, b = (g[g["cycle"] == c].drop_duplicates("fips5").set_index("fips5") for c in (2020, 2024))
        j = a.join(b, lsuffix="_20", rsuffix="_24", how="inner")
        if len(j) < 10:
            continue
        t20, t24 = (pd.to_datetime(j[f"t95_{c}"], utc=True).rank() for c in ("20", "24"))
        for tgt, oth, sz, y in (("2024", t20, j["final_votes_20"], t24), ("2020", t24, j["final_votes_24"], t20)):
            r = {"state": st, "target_cycle": tgt, "counties": len(j), "size": spearmanr(sz, y).statistic, "other_cycle": spearmanr(oth, y).statistic}
            for w in (0.25, 0.5, 2 / 3, 0.75):
                r[f"blend_{w:.2f}"] = spearmanr(w * oth.rank(pct=True) + (1 - w) * sz.rank(pct=True), y).statistic
            rows.append(r)
    d = pd.DataFrame(rows)
    RES.mkdir(parents=True, exist_ok=True)
    d.to_csv(RES / "order_validation.csv", index=False)
    print(d.drop(columns=["state"]).groupby("target_cycle").mean(numeric_only=True).round(3).to_string())
    return d


def mirage_check() -> pd.DataFrame:
    """Benchmark raw-count gap (typical pacing, all counties on benchmark) vs the historical gap of the presidential raw count
    (site/data/us2026/tells.json, mean of 2020/2024) at 10/25/50/75 % counted, for the statewide races."""
    t = json.loads((ROOT / "site" / "data" / "us2026" / "tells.json").read_text())["states"]
    rows = []
    for f in sorted(OUT_JSON.glob("*.json")):
        j = json.loads(f.read_text())
        if j["kind"] != "statewide" or j["state"] not in t:
            continue
        for p in j["benchmark_path"]:
            q = str(int(round(p["q"] * 100)))
            h = t[j["state"]]["gaps"].get(q)
            if h and p.get("gap_pts") is not None:
                rows.append({"race_id": j["race_id"], "state": j["state"], "q": int(q), "benchmark_gap_pts": p["gap_pts"],
                             "benchmark_gap_order_pts": p["gap_pts_order"], "historical_gap_pts": h["mean"], "historical_cycles": h["n"]})
    d = pd.DataFrame(rows)
    d.to_csv(RES / "mirage_check.csv", index=False)
    return d


def _md(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                cells.append("" if not np.isfinite(v) else (fmt.get(c, "{:.3g}")).format(v))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _bin_label(x: str) -> str:
    """'(1000.0, 2500.0]' -> '1,000-2,500'; cycles and 'all' unchanged."""
    if x.startswith("("):
        lo, hi = (float(v) for v in x.strip("([]").replace(" ", "").split(","))
        return f"{lo:,.0f}+" if hi > 1e8 else f"{lo:,.0f}-{hi:,.0f}"
    return x


def tables() -> None:
    """results/us_benchmarks/doc_tables.md: the tables of docs/us_benchmarks.md, rendered from the produced CSV / JSON files."""
    parts = []
    rows = []
    for f in sorted(OUT_JSON.glob("*.json")):
        j = json.loads(f.read_text())
        if j["kind"] != "statewide":
            continue
        s, pth = j["statewide"], {round(p["q"], 2): p for p in j["benchmark_path"]}
        rows.append({"race": j["race_id"], "counties": s["units"], "votes (k)": s["expected_votes"] / 1e3, "mean": s["mean"], "sd": s["sd"],
                     "P(D) config": s["p_win"], "P(D) nowcast": s["p_win_nowcast_precount"], "benchmark margin": s["benchmark_margin_pts"],
                     "gap 10%": (pth[0.1].get("gap_pts")), "gap 25%": pth[0.25].get("gap_pts"), "gap 50%": pth[0.5].get("gap_pts"),
                     "P(D) at 50%": pth[0.5].get("nowcast_p_win"), "tells": ", ".join(t["name"] for t in j["top_tells"][:3])})
    t = pd.DataFrame(rows)
    parts.append("### statewide\n\n" + _md(t, {"votes (k)": "{:,.0f}", "mean": "{:.3f}", "sd": "{:.3f}", "P(D) config": "{:.3f}", "P(D) nowcast": "{:.3f}",
                                              "benchmark margin": "{:+.1f}", "gap 10%": "{:+.1f}", "gap 25%": "{:+.1f}", "gap 50%": "{:+.1f}",
                                              "P(D) at 50%": "{:.3f}"}))
    d = pd.read_csv(OUT_CSV, comment="#")
    h = d[d["kind"] == "county_piece"]
    parts.append(f"### house\n\n{h['race_id'].nunique()} districts, {len(h)} county pieces, {int(h['tell_top'].sum())} tell flags.")
    parts.append("### resid profile\n\n" + _md(load_resid_profile(), {"bin_lo": "{:,.0f}", "bin_hi": "{:,.0f}", "n": "{:,.0f}", "median_V": "{:,.0f}", "rmse_pts": "{:.2f}", "mae_pts": "{:.2f}"}))
    ov = pd.read_csv(RES / "order_validation.csv").drop(columns="state").groupby("target_cycle").mean(numeric_only=True).reset_index()
    parts.append("### order validation (mean Spearman over states)\n\n" + _md(ov, {c: "{:.2f}" for c in ov.columns} | {"target_cycle": "{:.0f}", "counties": "{:.0f}"}))
    for name, fmt in (("backtest_band_coverage", {c: "{:.3f}" for c in ("cover_given_state", "cover_with_state_sd")}),):
        if (RES / f"{name}.csv").exists():
            cv = pd.read_csv(RES / f"{name}.csv")
            cv["t"] = cv["t"].astype(str).map(_bin_label)
            cv = cv.assign(_k=cv["t"].map(lambda x: float(x.split("-")[0].replace(",", "").rstrip("+")) if x[0].isdigit() and len(x) > 4 else -1.0)).sort_values(
                ["_k"], kind="stable").drop(columns="_k").rename(columns={"t": "cycle / votes of the county"})
            parts.append(f"### {name}\n\n" + _md(cv, fmt))
    m = RES / "mirage_check.csv"
    if m.exists():
        mm = pd.read_csv(m).groupby("q").apply(lambda g: pd.Series({"races": len(g), "corr": np.corrcoef(g["benchmark_gap_pts"], g["historical_gap_pts"])[0, 1],
                                                                  "benchmark gap (mean)": g["benchmark_gap_pts"].mean(),
                                                                  "historical gap (mean)": g["historical_gap_pts"].mean()}), include_groups=False).reset_index()
        parts.append("### mirage check\n\n" + _md(mm, {"corr": "{:.2f}", "benchmark gap (mean)": "{:+.1f}", "historical gap (mean)": "{:+.1f}", "races": "{:.0f}"}))
    a = pd.read_csv(RES / "backtest_error_all_cycles.csv")
    for scheme in ("calibrate", "weak", "real"):
        x = a[a["scheme"] == scheme].pivot_table(index="check", columns="est", values="mae_pts")[["raw", "benchmark", "nowcast", "tell", "tell_all"]]
        w = a[a["scheme"] == scheme].pivot_table(index="check", columns="est", values="wrong_call")[["raw", "benchmark", "nowcast", "tell", "tell_all"]]
        n = a[a["scheme"] == scheme]["races"].max()
        parts.append(f"### error all cycles, {scheme} ({n} races): MAE of the final two-party share, points\n\n" + _md(x.reset_index(), {c: "{:.2f}" for c in x.columns})
                     + "\n\nwrong-winner rate\n\n" + _md(w.reset_index(), {c: "{:.3f}" for c in w.columns}))
    b = pd.read_csv(RES / "backtest_error_by_cycle.csv")
    for scheme in ("calibrate", "real"):
        x = b[(b["scheme"] == scheme) & b["t"].isin([2018, 2022] if scheme == "calibrate" else [2020, 2024])].pivot_table(index=["t", "check"], columns="est", values="mae_pts")[["raw", "benchmark", "nowcast", "tell", "tell_all"]]
        parts.append(f"### error by cycle, {scheme}: MAE, points\n\n" + _md(x.reset_index(), {c: "{:.2f}" for c in x.columns} | {"t": "{:.0f}"}))
    pdf = pd.read_csv(RES / "backtest_paired.csv")
    x = pdf[(pdf["scheme"] == "calibrate") & pdf["subset"].isin(["all 5 cycles", "2018", "2022", "close races (margin under 10 pts)"])
            & pdf["tell_minus"].isin(["raw", "nowcast", "benchmark", "tell_all"])].copy()
    x["cell"] = x.apply(lambda r: f"{r['mean_mae_diff_pts']:+.2f} [{r['ci_lo']:+.2f}, {r['ci_hi']:+.2f}]", axis=1)
    pv = x.pivot_table(index=["subset", "check"], columns="tell_minus", values="cell", aggfunc="first")[["raw", "benchmark", "nowcast", "tell_all"]].reset_index()
    nr = x.groupby("subset")["races"].first()
    pv["races"] = pv["subset"].map(nr).astype(float)
    pv = pv.sort_values(["subset", "check"], key=lambda c: c.map({"all 5 cycles": 0, "2018": 1, "2022": 2}).fillna(3) if c.name == "subset" else c)
    parts.append("### paired MAE differences (tell minus other), points of share, bootstrap 95 % interval over races, calibrate order\n\n"
                 + _md(pv[["subset", "races", "check", "raw", "benchmark", "nowcast", "tell_all"]], {"races": "{:.0f}", "check": "{:.2f}"}))
    rc = pd.read_csv(RES / "backtest_info_rank_corr.csv")
    parts.append("### rank correlation with the realised |change of the nowcast| when a county reports in the first 40 % (mean Spearman over race x simulated nights)\n\n" + _md(
        rc[rc["t"].astype(str).isin(["all", "2018", "2022"])], {c: "{:.3f}" for c in rc.columns if c.startswith("rho")}))
    fl = pd.read_csv(RES / "backtest_info_flagged.csv")
    parts.append("### realised information, flagged tells vs other early counties\n\n" + _md(fl, {"info_pts_mean": "{:.3f}", "info_pts_per10k_mean": "{:.3f}", "mean_V": "{:,.0f}", "counties": "{:,.0f}"}))
    (RES / "doc_tables.md").write_text("\n\n".join(parts) + "\n")
    print("wrote", RES / "doc_tables.md")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("order", "resid", "build", "backtest", "check", "ordercheck", "mirage", "tables"))
    ap.add_argument("--sims", type=int, default=40)
    ap.add_argument("--cycles", type=int, nargs="*")
    a = ap.parse_args()
    pd.set_option("display.width", 220)
    if a.what == "order":
        build_order()
    elif a.what == "tables":
        tables()
    elif a.what == "ordercheck":
        order_validation()
    elif a.what == "mirage":
        d = mirage_check()
        for q, g in d.groupby("q"):
            print(q, "n", len(g), "corr(benchmark, historical) %.2f" % np.corrcoef(g["benchmark_gap_pts"], g["historical_gap_pts"])[0, 1],
                  "mean benchmark %.1f historical %.1f" % (g["benchmark_gap_pts"].mean(), g["historical_gap_pts"].mean()))
    elif a.what == "resid":
        build_resid()
    elif a.what == "build":
        build()
    elif a.what == "backtest":
        backtest(a.sims, a.cycles)
    else:
        F = pd.read_parquet(_cache("loco_county_priors.parquet"))
        print("fast vs project max diff:", check_fast(F, race_specs()[0]))


if __name__ == "__main__":
    main()
