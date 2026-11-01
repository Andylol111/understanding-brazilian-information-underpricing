"""US benchmark, part B (count stage): every statewide model version through `brunder.us.project` on past count nights.

    export BRUNDER_DATA=~/brunder-data-local
    uv run python analysis/us_benchmark_count.py ga   [--sims 100]      # Georgia 2022 gov / Senate, 2024 president, simulated nights
    uv run python analysis/us_benchmark_count.py report                 # internal real-night report from the saved real tables
    uv run python analysis/us_benchmark_count.py tables                 # markdown of data_public/us_benchmark_count_ga.csv
    uv run python analysis/us_benchmark_count.py real [--only KEY ...]  # real past nights (INTERNAL: NYT/Edison derived, SSD only)
    # a full `real` run also checks its unified rows against analysis/us_real_nights.py's own replay (same code path)

Versions (all configured only with information available before the election; brunder.us is imported read-only)
  v1       prior = the last race for the same office (president: the last president), V = that race's two-party votes x the
           cycle-type ratio (0.80 midterm <- presidential year, 1.30 presidential <- midterm, else 1; the convention of
           analysis/us_calibrate.py), no polls.
  v2       v1 + poll average: swing_prior_mean = poll avg - baseline_share(prior), swing_prior_sd = poll_sd (flat 0.039 +
           sampling; Georgia: the registered poll_average() sd, real nights: POLL_SD_HIST, the sampling term is ~0.001).
  v3       v2 with swing_prior_sd = poll_sd_by_n(number of polls) and count_scaled_terms = true (PRIMARY).
  v4       v3 + county expected votes (analysis/us_model_analysis.py expected_votes): last presidential two-party votes x the
           county's own drop-off (previous midterm top race / previous-previous president), relative to its state and capped
           x0.61-x1.65, level from the file; swing_prior_mean re-centred on those weights (v2 definition).
           Presidential nights have no drop-off: the same function on pres(t-4) x own change from pres(t-8) to pres(t-4),
           file used only to redistribute the statewide level (expected_votes_level = "prior"): an analogue, not registered.
  v5       v3 + the night Hispanic slope (hisp_slope = true, hisp_slope_tau = 0.2); Hispanic share = the ACS 5-year vintage
           ending t-2 (the county demographic panel of brunder.us_unified, the same vintage the unified model uses).
  v4d      sensitivity: v4's county distribution only (expected_votes_level = "prior": v3's statewide level), midterms only.
  v3p, v4p sensitivity, Georgia 2022 only: v3 / v4 on the prior of analysis/us_model_analysis.py validate_v4 (2020 president x 0.80
           instead of the last same-office race), to reproduce docs/us_model_analysis.md.
  unified  data_public/us_unified_params fits re-done leave-one-cycle-out (analysis/us_real_nights.py unified_prior),
           Senate / governor only (no president model).
Polls (v2-v5): the 538 raw_polls rule of analysis/us_polls.py (= the state drift panel average); no look-ahead.

Georgia simulated nights: certified results by vote type (Georgia SoS), the count-order generator of analysis/us_calibrate.py
(early + mail posted first, Election Day chunks later), seed = crc32(race|draw): identical draws for every version.
Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later checkpoint (up to 95 % counted); a wrong call is
P(wrong winner) >= 0.9 at any checkpoint. Paired bootstrap (2000 resamples of draws, stratified by race; real nights: nights)
for differences against v3.
"""

from __future__ import annotations

import argparse
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_calibrate as cal                           # noqa: E402
import us_model_analysis as ma                       # noqa: E402  (expected_votes)
import us_polls                                      # noqa: E402  (538 poll prior)
import us_real_nights as rn                          # noqa: E402  (real nights, LOCO unified prior)
from brunder import us, us_unified as uu             # noqa: E402

OUT = rn.DATA / "derived" / "us_benchmark"           # SSD: real-night numbers live ONLY here
PUBLIC_CSV = us.ROOT_DIR / "data_public" / "us_benchmark_count_ga.csv"
VERSIONS = ("v1", "v2", "v3", "v4", "v4d", "v5", "unified", "v3p", "v4p")      # v4d, v3p, v4p: sensitivities (see docstring)
HISP_TAU = 0.2
B_BOOT = 2000
CHECKS = (0.10, 0.25, 0.50, 0.75)
SIM_GRID = (0.01, 0.02, 0.05) + tuple(round(x, 2) for x in np.arange(0.10, 0.951, 0.05))
Z90 = 1.645

# ------------------------------------------------------------------ priors / configs of the versions


def cycle_ratio(t: int, oy: float) -> float:
    """Expected votes of a race in year t relative to the prior race of year oy (us_calibrate.py convention)."""
    if t % 4 == 2 and oy % 4 == 0:
        return 0.80
    if t % 4 == 0 and oy % 4 == 2:
        return 1.30
    return 1.0


def legacy_prior(ctx: rn.Ctx, t: int, st: str, office: str, special: bool) -> pd.DataFrame:
    """v1 prior: last same-office race (president: last president), V x the cycle-type ratio. Like rn.v1_prior, plus the ratio."""
    nm = rn.names_of(st)
    if office == "pres":
        rc = ctx.inp["rc"]
        b = rc[(rc["year"] == t - 4) & (rc["st"] == st) & (rc["contest"] == "pres")].dropna(subset=["s2", "v2"])
        uid = [uu.unit_id(st, f) for f in b["fips"]]
        return pd.DataFrame({"unit_id": uid, "name": nm.reindex(uid).values, "s0": b["s2"].values,
                             "V": b["v2"].values * cycle_ratio(t, t - 4)}).dropna()
    d = ctx.C[(ctx.C["t"] == t) & (ctx.C["st"] == st) & (ctx.C["office"] == office) & (ctx.C["special"] == special)]
    ratio = np.array([cycle_ratio(t, y) if y == y else 1.0 for y in d["oy"]])
    V1 = (d["vo_b"] * ratio).fillna(d["vp_b"] * (0.72 if t % 4 == 2 else 1.0))
    uid = [uu.unit_id(st, f) for f in d["fips"]]
    return pd.DataFrame({"unit_id": uid, "name": nm.reindex(uid).values, "s0": d["so_f"].values, "V": V1.values}).dropna()


_EV_WRITTEN: set[str] = set()


def expected_votes_file(ctx: rn.Ctx, t: int, st: str) -> tuple[str, str] | None:
    """(path, level) of the v4 expected-vote file for a race of year t in `st`, from county results before t only.
    Midterm: pres(t-2) two-party votes x own (Senate else governor (t-4)) / pres(t-6) drop-off (all-candidate totals), level "file".
    President: pres(t-4) x own pres(t-4) / pres(t-8) change, level "prior" (redistribution only)."""
    rc = ctx.inp["rc"]
    rc = rc[rc["st"] == st]

    def votes(year: int, contest: str, col: str) -> pd.Series:
        d = rc[(rc["year"] == year) & (rc["contest"] == contest) & (rc["v2"] > 0)]
        return d.set_index("fips")[col].astype(float)

    # base = two-party votes; the drop-off ratio uses all-candidate totals like the registered recipe (MEDSL county totals)
    if t % 4 == 2:
        base, den = votes(t - 2, "pres", "v2"), votes(t - 6, "pres", "total_all")
        num = votes(t - 4, "sen", "total_all")
        if num.empty:
            num = votes(t - 4, "gov", "total_all")
        level = "file"
    else:
        base, num, den = votes(t - 4, "pres", "v2"), votes(t - 4, "pres", "total_all"), votes(t - 8, "pres", "total_all")
        level = "prior"
    if base.empty or num.empty or den.empty:
        return None
    ev = ma.expected_votes(base, num.reindex(base.index), den.reindex(base.index), pd.Series(st, index=base.index))
    path = OUT / "inputs" / f"expected_votes_{t}_{st}.csv"
    if str(path) not in _EV_WRITTEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"unit_id": [uu.unit_id(st, f) for f in ev.index], "exp_votes": ev.round(0).to_numpy()}).dropna().to_csv(path, index=False)
        us._EXPECTED.pop(str(path), None)
        _EV_WRITTEN.add(str(path))
    return str(path), level


def hisp_of(ctx: rn.Ctx, t: int, st: str) -> pd.Series:
    """unit_id -> Hispanic share (ACS 5-year ending t-2; the demographic panel of brunder.us_unified)."""
    d = ctx.inp["demo"]
    d = d[(d["year"] == t) & (d["st"] == st)]
    return pd.Series(d["hisp"].to_numpy(float), index=[uu.unit_id(st, f) for f in d["fips"]])


def poll_of(ctx: rn.Ctx, t: int, st: str, office: str, special: bool) -> tuple[float, int, float | None] | None:
    """(poll average two-party Dem share, n polls, registered sd or None) of a race, or None when unpolled."""
    if office == "pres":
        p = rn.pres_poll(ctx, t, st)
        return (p[0], p[1], None) if p else None
    tr = ctx.truth(t, st, office, special) or {}
    if tr.get("P") is None or pd.isna(tr.get("P")) or tr.get("n_polls", 0) <= 0:
        return None
    return float(tr["P"]), int(tr["n_polls"]), None


def build_versions(ctx: rn.Ctx, sp: dict, poll_sd: float | None = None) -> dict[str, tuple[dict, pd.DataFrame]]:
    """version -> (config for brunder.us.project, prior frame)."""
    t, st, office, special = sp["t"], sp["st"], sp["office"], sp["special"]
    m, k = rn.count_cfg(st, office)
    cfg = rn.base_cfg(st, k)
    pri = legacy_prior(ctx, t, st, office, special)
    out = {"v1": ({**cfg, "model": dict(m)}, pri)}
    poll = poll_of(ctx, t, st, office, special)
    if poll is not None:
        avg, n, _ = poll
        sd2 = poll_sd if poll_sd is not None else us.POLL_SD_HIST
        mean = avg - us.baseline_share(pri)
        out["v2"] = ({**cfg, "model": {**m, "swing_prior_mean": mean, "swing_prior_sd": sd2}}, pri)
        m3 = {**m, "swing_prior_mean": mean, "swing_prior_sd": us.poll_sd_by_n(n), "count_scaled_terms": True}
        out["v3"] = ({**cfg, "model": m3}, pri)
        ev = expected_votes_file(ctx, t, st)
        if ev is not None:
            w = pri["unit_id"].map(pd.read_csv(ev[0]).set_index("unit_id")["exp_votes"]).fillna(pri["V"]).to_numpy()
            m4 = {**m3, "swing_prior_mean": avg - float(np.average(pri["s0"], weights=w))}
            out["v4"] = ({**cfg, "model": m4, "count": {**k, "expected_votes": ev[0], "expected_votes_level": ev[1]}}, pri)
            if ev[1] == "file":
                out["v4d"] = ({**cfg, "model": m4, "count": {**k, "expected_votes": ev[0], "expected_votes_level": "prior"}}, pri)
        h = hisp_of(ctx, t, st)
        out["v5"] = ({**cfg, "model": {**m3, "hisp_slope": True, "hisp_slope_tau": HISP_TAU}}, pri.assign(h=pri["unit_id"].map(h).to_numpy()))
    if office in ("sen", "gov") and ctx.truth(t, st, office, special) is not None and t in rn.bt.TEST:
        pri_u, info = rn.unified_prior(ctx, t, st, office, special)
        out["unified"] = ({**cfg, "model": {**m, "swing_prior_mean": 0.0, "swing_prior_sd": info["sd"], "count_scaled_terms": True}}, pri_u)
    return out


def pres_prior_versions(ctx: rn.Ctx, sp: dict, poll_sd: float) -> dict[str, tuple[dict, pd.DataFrame]]:
    """v3p / v4p: validate_v4's prior (ga_pres_2020 two-party votes, V x 0.80) with the poll prior and (v4p) the expected-vote file."""
    m, k = rn.count_cfg(sp["st"], sp["office"])
    cfg = rn.base_cfg(sp["st"], k)
    pri = us.race_prior({"election": {"state": sp["st"]}, "prior": {"share": "ga_pres_2020", "votes": "ga_pres_2020"}}, ctx.priors)
    pri = pri.assign(V=pri["V"] * 0.80)
    avg, n, _ = poll_of(ctx, sp["t"], sp["st"], sp["office"], sp["special"])
    m3 = {**m, "swing_prior_mean": avg - us.baseline_share(pri), "swing_prior_sd": us.poll_sd_by_n(n), "count_scaled_terms": True}
    ev = expected_votes_file(ctx, sp["t"], sp["st"])
    w = pri["unit_id"].map(pd.read_csv(ev[0]).set_index("unit_id")["exp_votes"]).fillna(pri["V"]).to_numpy()
    m4 = {**m3, "swing_prior_mean": avg - float(np.average(pri["s0"], weights=w))}
    return {"v3p": ({**cfg, "model": m3}, pri),
            "v4p": ({**cfg, "model": m4, "count": {**k, "expected_votes": ev[0], "expected_votes_level": ev[1]}}, pri)}


# ------------------------------------------------------------------ call rule


def first_held(ok: np.ndarray) -> int | None:
    """First index from which `ok` holds at every later index."""
    return next((i for i in range(len(ok)) if ok[i:].all()), None)


# ------------------------------------------------------------------ paired bootstrap


def boot_diff(x: np.ndarray, y: np.ndarray, strata: np.ndarray, rng: np.random.Generator, b: int = B_BOOT) -> tuple[float, float, float]:
    """Mean of x - y (paired) and its 95 % bootstrap interval; resampling within strata. NaN pairs are dropped."""
    d = np.asarray(x, float) - np.asarray(y, float)
    ok = ~np.isnan(d)
    d, s = d[ok], np.asarray(strata)[ok]
    if len(d) < 2:
        return (float(d.mean()) if len(d) else np.nan), np.nan, np.nan
    tot = np.zeros(b)
    for lab in np.unique(s):
        v = d[s == lab]
        tot += v[rng.integers(0, len(v), (b, len(v)))].sum(axis=1)
    means = tot / len(d)
    return float(d.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ------------------------------------------------------------------ Georgia simulated nights

GA_RACES = [  # scope, eid, race key (cal.by_type), sp
    ("ga2022_gov", "2022NovGen", "gov", dict(t=2022, st="ga", office="gov", special=False)),
    ("ga2022_sen", "2022NovGen", "sen", dict(t=2022, st="ga", office="sen", special=False)),
    ("ga2024_pres", "2024NovGen", "pres", dict(t=2024, st="ga", office="pres", special=False)),
]


def ga_task(args: tuple) -> list[dict]:
    scope, bt, vs, sim = args
    seed = zlib.crc32(f"{scope}|{sim}".encode())
    rng = np.random.default_rng(seed)
    ev = cal.events(bt, rng)
    tot = float(bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum())
    final = float((bt["early_d"] + bt["late_d"]).sum() / tot)
    cum = ((ev["d"] + ev["r"]).cumsum() / tot).to_numpy()
    out = []
    for c in (0.0,) + SIM_GRID:
        if c == 0.0:
            rows, up = us._frame([]), 0
        else:
            up = int(np.searchsorted(cum, c)) + 1
            rows = cal.rows_at(ev, up)
        for name, (cfg, pri) in vs.items():
            r, u = us.project(rows, cfg, pri)
            out.append({"scope": scope, "version": name, "sim": sim, "check": c, "counted": float(cum[up - 1]) if up else 0.0,
                        "minute": float(ev["t"].iloc[up - 1]) if up else -1.0, "err": r["share_hat"] - final, "sd": r["share_sd"],
                        "P": r["p_win"], "win": final > 0.5, "turnout_err": (u["n"].sum() + r["rem_total"]) / tot - 1.0})
    return out


def run_ga(sims: int, jobs: int) -> pd.DataFrame:
    ctx = rn.Ctx()
    tasks, info = [], []
    for scope, eid, race, sp in GA_RACES:
        bt = cal.by_type(eid, race)
        truth = ctx.truth(sp["t"], sp["st"], sp["office"], sp["special"])
        tot = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        final = (bt["early_d"] + bt["late_d"]).sum() / tot
        poll_sd = None
        if sp["office"] in ("gov", "sen"):                        # the registered poll_average() sd (flat + sampling) for v2
            pa = us_polls._poll_prior_538(f"2022_{'Gov' if sp['office'] == 'gov' else 'Sen'}-G_GA")
            poll_sd = pa["sd"]
        vs = build_versions(ctx, sp, poll_sd)
        if sp["t"] == 2022:
            vs.update(pres_prior_versions(ctx, sp, poll_sd))
        poll = poll_of(ctx, sp["t"], sp["st"], sp["office"], sp["special"])
        info.append({"scope": scope, "certified_two_party_dem": final, "panel_s2": truth["s2"] if truth else np.nan,
                     "two_party_votes": tot, "poll_avg": poll[0] if poll else np.nan, "n_polls": poll[1] if poll else 0,
                     "versions": ",".join(vs)})
        tasks += [(scope, bt, vs, s) for s in range(sims)]
    print(pd.DataFrame(info).to_string(index=False), flush=True)
    with ProcessPoolExecutor(jobs) as ex:
        res = [r for rs in ex.map(ga_task, tasks, chunksize=2) for r in rs]
    df = pd.DataFrame(res)
    df["z"] = df["err"] / df["sd"]
    df["p_correct"] = np.where(df["win"], df["P"], 1 - df["P"])
    return df


def sim_sim_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (scope, version, sim, check): per-draw metrics used for means and paired bootstraps."""
    return df.assign(abs_margin_err_pts=200 * df["err"].abs(), share_err_pts=100 * df["err"], cover90=(df["z"].abs() < Z90).astype(float),
                     abs_turnout_err_pct=100 * df["turnout_err"].abs(), turnout_err_pct=100 * df["turnout_err"],
                     sd_margin_pts=200 * df["sd"])


def call_table(df: pd.DataFrame) -> pd.DataFrame:
    """Per (scope, version, sim): counted share and minute of the call, whether a wrong call was ever made, P(correct) before the count."""
    rows = []
    for (scope, v, s), g in df.groupby(["scope", "version", "sim"]):
        g = g.sort_values("check")
        h = g[g["check"] > 0]
        i = first_held((h["p_correct"] >= 0.9).to_numpy())
        rows.append({"scope": scope, "version": v, "sim": s, "decided": i is not None,
                     "counted_at_call": h["counted"].iat[i] if i is not None else np.nan,
                     "minutes_to_call": h["minute"].iat[i] if i is not None else np.nan,
                     "wrong_call": bool((h["p_correct"] <= 0.1).any()),
                     "precount_call": bool(g[g["check"] == 0]["p_correct"].iat[0] >= 0.9)})
    return pd.DataFrame(rows)


def ga_tables(df: pd.DataFrame) -> pd.DataFrame:
    """Long public table: scope (per race, plus the pooled 2022 gov + Senate), version, metric, check, value, diff vs v3 and its 95 % interval."""
    m = sim_sim_metrics(df)
    calls = call_table(df)
    rng = np.random.default_rng(20261102)
    scopes = {s: [s] for s in m["scope"].unique()}
    scopes["ga2022_gov_sen"] = ["ga2022_gov", "ga2022_sen"]
    metrics = ("abs_margin_err_pts", "share_err_pts", "cover90", "abs_turnout_err_pct", "turnout_err_pct", "sd_margin_pts", "p_correct")
    diff_metrics = {"abs_margin_err_pts", "cover90", "abs_turnout_err_pct"}
    out = []
    for sc, members in scopes.items():
        ms = m[m["scope"].isin(members)]
        cs = calls[calls["scope"].isin(members)]
        vers = [v for v in VERSIONS if v in set(ms["version"]) and all(v in set(m[m["scope"] == x]["version"]) for x in members)]
        for v in vers:
            for c in (0.0,) + CHECKS:
                a = ms[(ms["version"] == v) & np.isclose(ms["check"], c)].sort_values(["scope", "sim"])
                b3 = ms[(ms["version"] == "v3") & np.isclose(ms["check"], c)].sort_values(["scope", "sim"])
                for met in metrics:
                    row = {"scope": sc, "version": v, "metric": met, "check": c, "n": len(a), "value": float(a[met].mean()),
                           "diff_vs_v3": np.nan, "diff_lo": np.nan, "diff_hi": np.nan}
                    if met in diff_metrics and v != "v3":
                        row["diff_vs_v3"], row["diff_lo"], row["diff_hi"] = boot_diff(a[met].to_numpy(), b3[met].to_numpy(), a["scope"].to_numpy(), rng)
                    out.append(row)
            ca = cs[cs["version"] == v].sort_values(["scope", "sim"])
            c3 = cs[cs["version"] == "v3"].sort_values(["scope", "sim"])
            for met, val in (("decided_share", ca["decided"].mean()), ("counted_at_call_median", ca["counted_at_call"].median()),
                             ("counted_at_call_earliest", ca["counted_at_call"].min()), ("counted_at_call_mean", ca["counted_at_call"].mean()),
                             ("minutes_to_call_median", ca["minutes_to_call"].median()), ("minutes_to_call_earliest", ca["minutes_to_call"].min()),
                             ("wrong_call_rate", ca["wrong_call"].mean()), ("precount_call_share", ca["precount_call"].mean())):
                row = {"scope": sc, "version": v, "metric": met, "check": np.nan, "n": len(ca), "value": float(val),
                       "diff_vs_v3": np.nan, "diff_lo": np.nan, "diff_hi": np.nan}
                if met in ("counted_at_call_mean", "decided_share") and v != "v3":
                    x = ca["counted_at_call" if met == "counted_at_call_mean" else "decided"].to_numpy(float)
                    y = c3["counted_at_call" if met == "counted_at_call_mean" else "decided"].to_numpy(float)
                    row["diff_vs_v3"], row["diff_lo"], row["diff_hi"] = boot_diff(x, y, ca["scope"].to_numpy(), rng)
                out.append(row)
    return pd.DataFrame(out)


# ------------------------------------------------------------------ real nights (INTERNAL)


def replay_real(args: tuple) -> pd.DataFrame:
    """rn.replay_night with the turnout error added (projected two-party total vs the certified total)."""
    sp, snaps, vs, truth = args
    final, v2, win = truth["s2"], truth["v2"], truth["s2"] > 0.5
    frames, last_n = [], -1.0
    for ts, fr in snaps.groupby("ts", sort=True):
        n = float((fr["d"] + fr["r"]).sum())
        if n <= 0 or n == last_n:
            continue
        frames.append((ts, fr, n))
        last_n = n
    if not frames:
        return pd.DataFrame()
    t0 = frames[0][0]
    if "real" in snaps.columns:
        anchor = snaps.loc[snaps["real"] & ((snaps["d"] + snaps["r"]) > 0), "ts"].min()
        frames = [f for f in frames if f[0] >= anchor]
    rows = []
    for name, (cfg, pri) in vs.items():
        r0, u0 = us.project(us._frame([]), cfg, pri)
        rows.append({"variant": name, "ts": pd.NaT, "minute": -1.0, "counted": 0.0, "share_hat": r0["share_hat"], "sd": r0["share_sd"], "P": r0["p_win"],
                     "turnout_err": (u0["n"].sum() + r0["rem_total"]) / v2 - 1.0})
        for ts, fr, n in frames:
            res, u = us.project(rn.feed_rows(fr, sp["st"]), cfg, pri)
            rows.append({"variant": name, "ts": ts, "minute": (ts - t0).total_seconds() / 60, "counted": n / v2, "share_hat": res["share_hat"],
                         "sd": res["share_sd"], "P": res["p_win"], "turnout_err": (u["n"].sum() + res["rem_total"]) / v2 - 1.0})
    d = pd.DataFrame(rows)
    d["night"], d["final"], d["win"] = sp["key"], final, win
    d["err"] = d["share_hat"] - final
    d["z"] = d["err"] / d["sd"]
    d["cover90"] = d["z"].abs() < Z90
    d["p_correct"] = np.where(win, d["P"], 1 - d["P"])
    for k in ("kind", "source", "office", "st", "t", "interp"):
        d[k] = sp[k]
    return d


def night_metrics(d: pd.DataFrame, tol: float = 0.05) -> pd.DataFrame:
    """Per (night, variant): pre-count row, the frame nearest to each checkpoint (within tol), call rule, whole-grid coverage."""
    out = []
    for (night, var), g in d.groupby(["night", "variant"]):
        pre = g[g["minute"] < 0].iloc[0]
        h = g[(g["minute"] >= 0) & (g["counted"] <= 0.995)].sort_values("minute").reset_index(drop=True)
        ok = (h["p_correct"] >= 0.9).to_numpy()
        first = first_held(ok) if len(ok) else None
        row = {"night": night, "variant": var, "frames": len(h), "n_mid": int(((h["counted"] >= 0.05) & (h["counted"] <= 0.9)).sum()),
               "pre_abs_margin_err_pts": 200 * abs(pre["err"]), "pre_p_correct": pre["p_correct"], "pre_turnout_err_pct": 100 * pre["turnout_err"],
               "minutes_to_call": h["minute"].iat[first] if first is not None else np.nan,
               "counted_at_call": h["counted"].iat[first] if first is not None else np.nan, "decided": first is not None,
               "wrong_call": bool((h["p_correct"] <= 0.1).any())}
        for c in CHECKS:
            r = rn._nearest(h, c, tol)
            k = int(c * 100)
            row[f"abs_margin_err_{k}"] = 200 * abs(r["err"]) if r is not None else np.nan
            row[f"share_err_{k}"] = 100 * r["err"] if r is not None else np.nan
            row[f"cover90_{k}"] = float(r["cover90"]) if r is not None else np.nan
            row[f"abs_turnout_err_{k}"] = 100 * abs(r["turnout_err"]) if r is not None else np.nan
            row[f"at_counted_{k}"] = r["counted"] if r is not None else np.nan
        cov = [r["cover90"] for c in rn.GRID if (r := rn._nearest(h, c, 0.03)) is not None]
        row["cover90_grid"] = float(np.mean(cov)) if cov else np.nan
        out.append(row)
    return pd.DataFrame(out)


def run_real(only: list[str] | None, jobs: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    ctx = rn.Ctx()
    tasks = []
    for sp in rn.night_specs():
        if only and not any(o in sp["key"] for o in only):
            continue
        if not only and sp["st"] not in rn.TARGET:
            continue
        f = rn.PROC / f'{sp["key"]}.parquet'
        if not f.exists():
            continue
        truth = ctx.truth(sp["t"], sp["st"], sp["office"], sp["special"])
        if truth is None:
            print(f'{sp["key"]}: no certified result in the panel, skipped')
            continue
        try:
            vs = build_versions(ctx, sp)
        except (KeyError, ValueError, IndexError) as e:
            print(f'{sp["key"]}: versions FAILED {type(e).__name__}: {e}')
            continue
        meta = {k: v for k, v in sp.items() if k != "loader"}
        tasks.append((meta, pd.read_parquet(f), vs, truth))
    frames = []
    with ProcessPoolExecutor(jobs) as ex:
        futs = [ex.submit(replay_real, t) for t in tasks]
        for t, fu in zip(tasks, futs):
            try:
                d = fu.result()
            except (KeyError, ValueError, IndexError, RuntimeError) as e:
                print(f'{t[0]["key"]}: FAILED {type(e).__name__}: {e}')
                continue
            if len(d):
                frames.append(d)
                print(f'{t[0]["key"]}: {len(d)} rows', flush=True)
    raw = pd.concat(frames, ignore_index=True)
    ns = night_metrics(raw).merge(raw.drop_duplicates("night")[["night", "kind", "source", "office", "st", "t", "interp", "final"]], on="night")
    ns["cls"] = rn.night_class(ns)
    return raw, ns


def real_summary(ns: pd.DataFrame) -> pd.DataFrame:
    """Per subset / variant / checkpoint pooled means and the paired bootstrap (over nights) against v3."""
    rng = np.random.default_rng(20261103)
    subsets = {"all real nights": ns, "dense real + interpolated": ns[ns["cls"] != "sparse real"],
               "president": ns[ns["office"] == "pres"], "senate/governor": ns[ns["office"] != "pres"]}
    out = []
    for sname, s in subsets.items():
        for v in VERSIONS:
            sv = s[s["variant"] == v]
            if sv.empty:
                continue
            b3 = s[s["variant"] == "v3"].set_index("night")
            svi = sv.set_index("night")
            for c in (0,) + tuple(int(x * 100) for x in CHECKS):
                cols = {"abs_margin_err": f"abs_margin_err_{c}" if c else "pre_abs_margin_err_pts", "cover90": f"cover90_{c}" if c else None,
                        "abs_turnout_err": f"abs_turnout_err_{c}" if c else None}
                for met, col in cols.items():
                    if col is None and c == 0:
                        continue
                    x = svi[col].dropna()
                    row = {"subset": sname, "variant": v, "check": c / 100, "metric": met, "nights": len(x), "value": float(x.mean()) if len(x) else np.nan,
                           "diff_vs_v3": np.nan, "diff_lo": np.nan, "diff_hi": np.nan, "diff_nights": 0}
                    if v != "v3" and len(b3):
                        j = pd.concat([svi[col], b3[col]], axis=1, keys=["a", "b"]).dropna()
                        row["diff_nights"] = len(j)
                        if len(j):
                            row["diff_vs_v3"], row["diff_lo"], row["diff_hi"] = boot_diff(j["a"].to_numpy(), j["b"].to_numpy(), np.zeros(len(j)), rng)
                    out.append(row)
            dec = sv[sv["cls"] != "sparse real"]
            row = {"subset": sname, "variant": v, "check": np.nan, "metric": "call", "nights": len(dec)}
            row["decided_share"] = dec["decided"].mean() if len(dec) else np.nan
            row["counted_at_call_median"] = dec["counted_at_call"].median() if len(dec) else np.nan
            row["minutes_to_call_median"] = dec["minutes_to_call"].median() if len(dec) else np.nan
            row["wrong_call_nights"] = int(dec["wrong_call"].sum()) if len(dec) else 0
            if v != "v3" and len(b3):
                j = pd.concat([dec.set_index("night")["counted_at_call"], b3["counted_at_call"]], axis=1, keys=["a", "b"]).dropna()
                row["diff_nights"] = len(j)
                if len(j):
                    row["diff_vs_v3"], row["diff_lo"], row["diff_hi"] = boot_diff(j["a"].to_numpy(), j["b"].to_numpy(), np.zeros(len(j)), rng)
            out.append(row)
    return pd.DataFrame(out)


def write_real_report(ns: pd.DataFrame, summ: pd.DataFrame, out: Path) -> Path:
    """INTERNAL report (SSD only): every number derived from the NYT/Edison nights lives here, never in the repository."""
    L = ["# US benchmark part B, real past nights (INTERNAL: derived from NYT/Edison copies, do not publish)", "",
         "Margin errors in points; coverage = share of nights whose 90 % interval covers; turnout error = |projected two-party total / certified - 1| in %; "
         "diff = paired difference vs v3 with a 95 % bootstrap interval over nights (nights are not independent: same states and years).", "",
         "## nights per class and variant", "", rn.md(ns.groupby(["cls", "variant"]).size().unstack(fill_value=0).reset_index()), ""]
    for sub in summ["subset"].unique():
        s = summ[summ["subset"] == sub]
        L += [f"## {sub}", ""]
        for met in ("abs_margin_err", "cover90", "abs_turnout_err"):
            x = s[s["metric"] == met].copy()
            x["cell"] = [f"{v:.2f} ({d:+.2f} [{lo:+.2f}, {hi:+.2f}]; n={int(n)})" if pd.notna(d) else (f"{v:.2f} (n={int(n)})" if pd.notna(v) else "")
                         for v, d, lo, hi, n in zip(x["value"], x["diff_vs_v3"], x["diff_lo"], x["diff_hi"], x["nights"])]
            t = x.pivot_table(index="variant", columns="check", values="cell", aggfunc="first").reindex([v for v in VERSIONS if v in set(x["variant"])])
            t.columns = ["pre-count" if c == 0 else f"{int(round(c * 100))} %" for c in t.columns]
            L += [f"{met}", "", rn.md(t.fillna("").reset_index()), ""]
        c = s[s["metric"] == "call"][["variant", "nights", "decided_share", "counted_at_call_median", "minutes_to_call_median", "wrong_call_nights",
                                      "diff_vs_v3", "diff_lo", "diff_hi", "diff_nights"]]
        L += ["call rule (dense real + interpolated nights only)", "", rn.md(c.round(3)), ""]
    L += ["## per night", "", rn.md(ns.sort_values(["night", "variant"]).round(3)), ""]
    p = out / "REPORT_real.md"
    p.write_text("\n".join(L))
    return p


def check_unified(raw: pd.DataFrame) -> None:
    """The unified variant of this replay must equal the unified rows of analysis/us_real_nights.py (same code path)."""
    f = rn.DATA / "derived" / "us_real_nights" / "replay_frames.csv"
    old = pd.read_csv(f)
    old = old[old["variant"] == "unified"].set_index(["night", "minute", "counted"])["share_hat"]
    new = raw[raw["variant"] == "unified"].set_index(["night", "minute", "counted"])["share_hat"]
    j = pd.concat([old, new], axis=1, keys=["old", "new"]).dropna()
    print(f"unified rows in common {len(j)}; max |share_hat difference| {np.abs(j['old'] - j['new']).max():.2e}")


# ------------------------------------------------------------------ markdown tables of the public Georgia table


LABEL = {"v1": "v1", "v2": "v2", "v3": "v3 (primary)", "v4": "v4", "v4d": "v4d (sens.)", "v5": "v5", "unified": "unified",
         "v3p": "v3p (sens.)", "v4p": "v4p (sens.)"}


def _cell(r: pd.Series, nd: int = 2, diff: bool = True) -> str:
    if r is None or pd.isna(r["value"]):
        return ""
    s = f"{r['value']:.{nd}f}"
    if diff and pd.notna(r["diff_vs_v3"]):
        s += f" ({r['diff_vs_v3']:+.{nd}f} [{r['diff_lo']:+.{nd}f}, {r['diff_hi']:+.{nd}f}])"
    return s


def _grid(tab: pd.DataFrame, scope: str, metric: str, checks, nd: int = 2, diff: bool = True) -> str:
    t = tab[(tab["scope"] == scope) & (tab["metric"] == metric)]
    head = "| version | " + " | ".join("pre-count" if c == 0 else f"{int(round(c * 100))} %" for c in checks) + " |\n|---|" + "---|" * len(checks)
    rows = []
    for v in VERSIONS:
        tv = t[t["version"] == v]
        if tv.empty:
            continue
        rows.append(f"| {LABEL[v]} | " + " | ".join(_cell(next((r for _, r in tv[np.isclose(tv['check'], c)].iterrows()), None), nd, diff) for c in checks) + " |")
    return "\n".join([head] + rows)


def _calls(tab: pd.DataFrame, scope: str) -> str:
    t = tab[(tab["scope"] == scope) & tab["check"].isna()].pivot_table(index="version", columns="metric", values="value")
    d = tab[(tab["scope"] == scope) & (tab["metric"] == "counted_at_call_mean") & tab["diff_vs_v3"].notna()].set_index("version")
    head = ("| version | calls (share of draws) | counted at call, median | counted at call, earliest | minutes to call, median | minutes, earliest "
            "| wrong calls | counted at call, mean diff vs v3 [95 % CI] |\n|---|---|---|---|---|---|---|---|")
    rows = []
    for v in VERSIONS:
        if v not in t.index:
            continue
        r = t.loc[v]
        df = f"{d.at[v, 'diff_vs_v3']:+.3f} [{d.at[v, 'diff_lo']:+.3f}, {d.at[v, 'diff_hi']:+.3f}]" if v in d.index else ""
        rows.append(f"| {LABEL[v]} | {r['decided_share']:.2f} | {r['counted_at_call_median']:.3f} | {r['counted_at_call_earliest']:.3f} | "
                    f"{r['minutes_to_call_median']:.0f} | {r['minutes_to_call_earliest']:.0f} | {r['wrong_call_rate']:.3f} | {df} |")
    return "\n".join([head] + rows)


def md_tables(tab: pd.DataFrame) -> str:
    cs = (0.0,) + CHECKS
    out = []
    for scope, title in (("ga2022_gov_sen", "Georgia 2022 governor + Senate (2 races x 200 draws)"), ("ga2024_pres", "Georgia 2024 president (1 race x 200 draws)"),
                         ("ga2022_gov", "Georgia 2022 governor (200 draws)"), ("ga2022_sen", "Georgia 2022 Senate (200 draws)")):
        out += [f"#### {title}", "", "Mean absolute margin error (points; difference vs v3 with its paired 95 % bootstrap interval)", "",
                _grid(tab, scope, "abs_margin_err_pts", cs), "",
                "90 % coverage of the final share (nominal 0.90)", "", _grid(tab, scope, "cover90", CHECKS, 2, False), "",
                "Projected-turnout error: mean signed / mean absolute error of the projected two-party total (%)", "",
                "| version | " + " | ".join("pre-count" if c == 0 else f"{int(round(c * 100))} %" for c in cs) + " |\n|---|" + "---|" * len(cs)]
        for v in VERSIONS:
            a = tab[(tab["scope"] == scope) & (tab["version"] == v)]
            if a.empty:
                continue
            sg = a[a["metric"] == "turnout_err_pct"].set_index("check")["value"]
            ab = a[a["metric"] == "abs_turnout_err_pct"].set_index("check")["value"]
            out_row = f"| {LABEL[v]} | " + " | ".join(f"{sg.get(c, np.nan):+.1f} / {ab.get(c, np.nan):.1f}" for c in cs) + " |"
            out.append(out_row)
        out += ["", "Call rule: P(correct winner) >= 0.9 at a checkpoint and at every later one (to 95 % counted)", "", _calls(tab, scope), ""]
    return "\n".join(out)


# ------------------------------------------------------------------ CLI


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ga", "real", "tables", "report"])
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "tables":                                      # markdown of the public Georgia table (docs fragment)
        md = md_tables(pd.read_csv(PUBLIC_CSV, comment="#"))
        (OUT / "ga_tables.md").write_text(md)
        print(md)
    elif a.cmd == "report":                                    # rebuild the internal real-night report from the saved tables
        print(write_real_report(pd.read_csv(OUT / "real_nights.csv"), pd.read_csv(OUT / "real_summary.csv"), OUT))
    elif a.cmd == "ga":
        df = run_ga(a.sims, a.jobs)
        df.to_csv(OUT / "ga_sim_raw.csv", index=False)
        tab = ga_tables(df)
        PUBLIC_CSV.write_text(
            "# US benchmark part B (count stage), Georgia simulated count nights: every model version through brunder.us.project on "
            "Georgia 2022 governor / Senate and 2024 president certified results by vote type (Georgia Secretary of State), "
            f"{a.sims} simulated count orders per race (analysis/us_calibrate.py generator, seed crc32(scope|draw)). Polls: FiveThirtyEight "
            "raw_polls (CC BY 4.0) / state drift panel. check = share of the final two-party vote counted; diff_vs_v3 = paired difference with "
            "its 95% bootstrap interval over draws. analysis/us_benchmark_count.py\n" + tab.round(5).to_csv(index=False))
        print(f"wrote {PUBLIC_CSV} ({len(tab)} rows)")
        t = tab[tab["metric"].isin(["abs_margin_err_pts", "cover90", "abs_turnout_err_pct"]) & tab["check"].isin([0.1, 0.25, 0.5, 0.75])]
        for met, g in t.groupby("metric"):
            print(met)
            print(g.pivot_table(index=["scope", "version"], columns="check", values="value").round(3).to_string())
        print(tab[tab["metric"].str.contains("call|decided")].pivot_table(index=["scope", "version"], columns="metric", values="value").round(3).to_string())
    else:
        raw, ns = run_real(a.only, a.jobs)
        out = OUT / "partial" if a.only else OUT
        out.mkdir(parents=True, exist_ok=True)
        raw.to_csv(out / "real_frames.csv", index=False)
        ns.to_csv(out / "real_nights.csv", index=False)
        summ = real_summary(ns)
        summ.to_csv(out / "real_summary.csv", index=False)
        if not a.only:
            check_unified(raw)
        print(write_real_report(ns, summ, out))
        print(summ[summ["subset"] == "all real nights"].round(3).to_string(index=False))
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
