"""Stage 3 of the model review: minute-level scoring of the held-out target nights with settings chosen on other nights.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_targets.py

Targets: Poland 2025 runoff (every archived version), Mexico 2024 (one tick per 3 minutes), Brazil 2026 round 1 and runoff
(one tick per minute). For each target the settings come from analysis/model_review_loeo.py (loeo_choices.csv: chosen on the
other nights only; "chronological" = earlier nights only) for three arms: the repo's rule on the base model ("repo"), the
sharpness rule on the base model ("sharp/none") and the sharpness rule on the order-aware family ("sharp/order"); plus the
settings the published v1 replay used (reference, to check this harness reproduces the published numbers).
Per arm: coverage of the 90% band over ticks with 1% < counted <= 95%, mean z (z > 0: projected A too low), MAE (points), mean
band sd, and the time the model's P(eventual outcome) >= 0.9 for good ("settle", first tick from which it stays >= 0.9 to the
end of the count) against the published Polymarket settle time of the same market. Lead = market minus model, in minutes (positive = the
model was first); "from the 1% gate" uses max(model settle, first tick with 1% counted). Mexico 2024 was decided before the
count in the market (no contested market), so only the model's time is reported.
Writes results/model_review/targets_arms.csv and targets_ticks_<night>.parquet on the SSD.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
import mr_nights_pl  # noqa: E402
from mr_common import OUT  # noqa: E402
import model_review_loeo as L  # noqa: E402
import model_review_tau_mix as TM  # noqa: E402

from brunder import nowcast as nc  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NIGHTS = {**mr_nights_br.NIGHTS, **mr_nights_mx.NIGHTS, **mr_nights_pl.NIGHTS}
TARGETS = {"pl2025r2": ("pl2025r2", "winner", 1), "mx2024": ("mx2024", "mx", 3), "br2026r1": ("br2026r1", "br", 1), "br2026r2": ("br2026r2", "br", 1)}
# settings the published v1 replays used (configs/*_v1.toml; Brazil: nowcast defaults for rho / kappa), reference arm
PUBLISHED = {"pl2025r2": dict(cov="none", slope_sd=0.0, rho=0.8, kappa=100_000.0, tau=0.005),
             "mx2024": dict(cov="none", slope_sd=0.0, rho=0.8, kappa=50_000.0, tau=0.01),
             "br2026r1": dict(cov="none", slope_sd=0.0, rho=0.8, kappa=50_000.0, tau=0.015),
             "br2026r2": dict(cov="none", slope_sd=0.0, rho=0.8, kappa=50_000.0, tau=0.015)}


def run(job):
    name, cov, slope_sd, rho, kappa = job
    _, _, step = TARGETS[name]
    night = NIGHTS[name]()
    p = nc.Params(tau=0.0, rho=rho, kappa=kappa, unpooled=night.unpooled)
    o = no.OrderParams(cov, slope_sd if cov != "none" else 0.02)
    rows = []
    for t, st in night.ticks(step):
        if st["n"].sum() <= 0:
            continue
        r = no.order_nowcast(st, night.prior, p, o)
        rows.append({"t": t, "counted": r["counted_frac"], "share_hat": r["share_hat"], "share_sd0": r["share_sd"], "margin_hat": r["margin_hat"],
                     "margin_sd0": r["margin_sd"], "beta": r.get("hisp_beta", np.nan), "raw": float(st["a"].sum() / st["n"].sum())})
    d = pd.DataFrame(rows).assign(night=name, cov=cov, slope_sd=slope_sd, rho=rho, kappa=kappa)
    d["final"], d["alt_final"] = night.final, (night.alt_final if night.alt_final is not None else np.nan)
    return d


def settle(t: pd.Series, p_side: pd.Series, thr=0.9):
    bad = ~(p_side >= thr).to_numpy()
    if not len(t) or bad[-1]:
        return pd.NaT
    k = np.flatnonzero(bad)
    return t.iloc[(k[-1] + 1) if len(k) else 0]


def arm_stats(d: pd.DataFrame, tau: float, market_settle, gate_t, target="final") -> dict:
    total = d["margin_sd0"] / (2 * d["share_sd0"])
    sd = np.sqrt(d["margin_sd0"] ** 2 + (2 * total * (1 - d["counted"])) ** 2 * tau**2)
    sh = sd / (2 * total)
    w = (d["counted"] > 0.01) & (d["counted"] <= 0.95)
    z = (d[target] - d["share_hat"]) / sh
    p = pd.Series(norm.cdf(d["margin_hat"] / sd), index=d.index)
    a_wins = d["final"].iloc[0] > 0.5
    ps = p if a_wins else 1 - p
    ts = settle(d["t"].reset_index(drop=True), ps.reset_index(drop=True))
    ts_gate = max(ts, gate_t) if pd.notna(ts) else ts
    out = {"coverage": float((z[w].abs() < 1.645).mean()), "mean_z": float(z[w].mean()), "mae_pt": float(100 * (d[target] - d["share_hat"]).abs()[w].mean()),
           "mean_sd_pt": float(100 * sh[w].mean()), "ticks": int(w.sum()), "model_settle90": ts,
           "brier": float(((ps.reset_index(drop=True) - 1.0) ** 2)[w.reset_index(drop=True)].mean())}
    if market_settle is not None and pd.notna(ts):
        out["lead_min"] = (market_settle - ts).total_seconds() / 60
        out["lead_from_gate_min"] = (market_settle - ts_gate).total_seconds() / 60
    return out


def arm_stats_mix(d: pd.DataFrame, taus: list[float], market_settle, gate_t, target="final") -> dict:
    """Same scores for the equal-weight mixture over `taus` (nowcast_order.mixture_band / mixture_pwin)."""
    a = no.frame_arrays(d.assign(share_sd0=d["share_sd0"]))
    lo, hi = no.mixture_band(d["share_hat"], a["margin_sd0"], a["two_r"], a["total"], taus)
    p = pd.Series(no.mixture_pwin(d["margin_hat"], a["margin_sd0"], a["two_r"], taus), index=d.index)
    w = ((d["counted"] > 0.01) & (d["counted"] <= 0.95)).to_numpy()
    y = d[target].to_numpy()
    mid_sd = (hi - lo) / (2 * 1.645)
    a_wins = d["final"].iloc[0] > 0.5
    ps = (p if a_wins else 1 - p).reset_index(drop=True)
    ts = settle(d["t"].reset_index(drop=True), ps)
    ts_gate = max(ts, gate_t) if pd.notna(ts) else ts
    out = {"coverage": float(((lo <= y) & (y <= hi))[w].mean()), "mean_z": float(((y - d["share_hat"].to_numpy()) / mid_sd)[w].mean()),
           "mae_pt": float(100 * np.abs(y - d["share_hat"].to_numpy())[w].mean()), "mean_sd_pt": float(100 * mid_sd[w].mean()), "ticks": int(w.sum()),
           "model_settle90": ts, "brier": float(((ps - 1.0) ** 2)[w].mean())}
    if market_settle is not None and pd.notna(ts):
        out["lead_min"] = (market_settle - ts).total_seconds() / 60
        out["lead_from_gate_min"] = (market_settle - ts_gate).total_seconds() / 60
    return out


def main() -> None:
    ch = pd.read_csv(OUT / "loeo_choices.csv")
    ch = ch[(ch["held_out"].isin(TARGETS)) & (ch["target"] == "final")]
    ch = ch[((ch["rule"] == "repo") & (ch["family"] == "none")) | ((ch["rule"] == "sharp") & (ch["family"].isin(["none", "order", "size", "lean"])))]
    arms = []
    for _, c in ch.iterrows():
        arms.append({"held_out": c["held_out"], "arm": f"{c['rule']}/{c['family']}", "scheme": c["scheme"], "cov": c["covariate"],
                     "slope_sd": 0.0 if c["covariate"] == "none" else c["slope_sd"], "rho": c["rho"], "kappa": c["kappa"], "tau": c["tau"]})
    for n, c in PUBLISHED.items():
        arms.append({"held_out": n, "arm": "published v1 settings", "scheme": "reference", **c})
    fr = L.load()
    fr = fr[(fr["rho"] == 0.8) & (fr["kappa"] == 50_000.0) & (fr["cov"] == "none")]
    nights = sorted(fr["night"].unique(), key=lambda n: L.DATES[n])
    mix_taus = {}
    for n in TARGETS:
        for scheme in ("loeo", "chronological"):
            train = [x for x in nights if x != n] if scheme == "loeo" else [x for x in nights if L.DATES[x] < L.DATES[n]]
            mix_taus[(n, scheme)] = TM.training_taus(fr, train)
            arms.append({"held_out": n, "arm": "mixture/none", "scheme": scheme, "cov": "none", "slope_sd": 0.0, "rho": 0.8, "kappa": 50_000.0,
                         "tau": np.nan})
    A = pd.DataFrame(arms)
    jobs = sorted({(r.held_out, r.cov, r.slope_sd, r.rho, r.kappa) for r in A.itertuples()})
    print(len(jobs), "replays", flush=True)
    with ProcessPoolExecutor(max_workers=9) as ex:
        res = list(ex.map(run, jobs))
    D = {j: d for j, d in zip(jobs, res)}
    for n in TARGETS:
        pd.concat([d for j, d in D.items() if j[0] == n]).to_parquet(OUT / f"targets_ticks_{n}.parquet")
    rows = []
    for r in A.itertuples():
        d = D[(r.held_out, r.cov, r.slope_sd, r.rho, r.kappa)]
        S = json.loads((ROOT / "site" / "data" / r.held_out / "stats.json").read_text())
        race = TARGETS[r.held_out][1]
        pm = S["settle"][race].get("polymarket")
        contested = not S["market_classification"]["by_race"][race]["decided_before_count"]
        gate_t = d.loc[d["counted"] >= 0.01, "t"].iloc[0]
        for tgt in (["final", "alt_final"] if r.held_out == "mx2024" else ["final"]):
            ms = pd.Timestamp(pm) if (pm and contested) else None
            if r.arm == "mixture/none":
                taus = mix_taus[(r.held_out, r.scheme)]
                st = arm_stats_mix(d, taus, ms, gate_t, tgt)
                tau_label = ";".join(f"{t:g}" for t in taus)
            else:
                st = arm_stats(d, r.tau, ms, gate_t, tgt)
                tau_label = r.tau
            rows.append({"held_out": r.held_out, "arm": r.arm, "scheme": r.scheme, "target": tgt, "covariate": r.cov, "slope_sd": r.slope_sd, "rho": r.rho,
                         "kappa": r.kappa, "tau": tau_label, "market_settle": pm if contested else None, "contested": contested, **st})
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "targets_arms.csv", index=False)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    print(R[R["target"] == "final"].drop(columns=["target", "market_settle", "kappa", "ticks"]).round(3).to_string(index=False))
    print(R[R["target"] == "alt_final"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
