"""Same election, two reporting orders: how much tau does a simulated small-first order need compared with the real one?

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_sim_vs_real.py

For the Brazilian nights (BU-web sections with real receipt times) and the Mexican nights (PREP actas with real verification
times) the same final section/acta results are revealed (a) in the real order and (b) in the repo's simulated order: units
smallest first with Normal(0, 0.7) noise on log size (analysis/br_calibrate.py, brunder.poland.simulated_selection), 4 draws.
Both use the same prior, the base model at rho 0.8 / kappa 50,000, frames at 1, 2, 3, 5, ... 95% counted. For each tau of the
grid: coverage of the 90% band of the national final share, and the smallest tau with coverage >= 0.90 ("tau needed"),
mean z (z > 0: projected A too low) and mean |error|. If simulated orders are gentler than real ones, tau needed is smaller for them.
Output: results/model_review/sim_vs_real.csv (SSD) + markdown on stdout. Poland: the real night / simulated 2020 runoff pair
is in docs/poland.md (tau 0.005 vs 0.04 chosen); the 2020 night itself is not archived, so it cannot be done on one election.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
from mr_common import FRACTIONS, OUT, frames_at  # noqa: E402

from brunder import bweb, mexico  # noqa: E402
from brunder import nowcast as nc  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402

TAUS = (0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
DRAWS = 4
P0 = dict(rho=0.8, kappa=50_000.0)


def score(frames, prior, final, unpooled, order) -> list[dict]:
    rows = []
    for fr, st in frames:
        r = nc.nowcast(st, prior, nc.Params(tau=0.0, unpooled=unpooled, **P0))
        if not (0.01 < r["counted_frac"] <= 0.95):
            continue
        total = r["margin_sd"] / (2 * r["share_sd"])
        for tau in TAUS:
            sd = no.with_tau(r, 0.0, tau)["share_sd"]
            rows.append({"order": order, "tau": tau, "z": (final - r["share_hat"]) / sd, "err": abs(final - r["share_hat"]), "sd": sd})
    return rows


def br_sim_frames(name: str, night, seed: int):
    spec = mr_nights_br._SPEC[name]
    from brunder import night as bn
    cfg = bn.load_config(mr_nights_br.CFG / f"{spec[0]}.toml")
    E, R = cfg["election"], cfg["replay"]
    s = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    s["n"] = s["a"] + s["b"]
    s = s[s["n"] > 0].reset_index(drop=True)
    totals = s.groupby(["uf", "cd_mun"]).size()
    out = []
    for draw in range(DRAWS):
        rng = np.random.default_rng(seed + draw)
        order = np.argsort(np.log(s["n"].to_numpy()) + rng.normal(0, 0.7, len(s)), kind="stable")
        for f in FRACTIONS:
            x = s.iloc[order[: max(1, round(f * len(s)))]]
            g = x.groupby(["uf", "cd_mun"]).agg(n=("n", "sum"), a=("a", "sum"), k=("n", "size"))
            g["f"] = g["k"] / totals.reindex(g.index)
            out.append((f, g.drop(columns="k").reset_index()))
    return out


def mx_sim_frames(key: str, seed: int):
    cfg_path, *_ = mr_nights_mx._CONFIGS[key]
    cfg = mexico.load_config(mr_nights_mx.ROOT / cfg_path)
    s = mexico.setup(cfg)
    actas, cat = s["actas"], s["cat"]
    c = actas[actas["counted"] & actas["visible"].notna()].copy()
    c["two"] = c["a"] + c["b"]
    c = c[c["two"] > 0]
    opening = pd.Timestamp(cfg["publication"]["opening_utc"])
    out = []
    for draw in range(DRAWS):
        rng = np.random.default_rng(seed + draw)
        rank = np.argsort(np.argsort(np.log(c["two"].to_numpy()) + rng.normal(0, 0.7, len(c)), kind="stable"))
        sim = c.assign(visible=opening + pd.to_timedelta(rank, unit="s"))
        times = [opening + pd.Timedelta(seconds=max(1, round(f * len(c))) - 1) for f in FRACTIONS]
        for f, (t, st) in zip(FRACTIONS, mexico.count_states(sim, cat, pd.DatetimeIndex(times))):
            out.append((f, st))
    return out


def main() -> None:
    rows = []
    for name, seed in (("br2022r1", 1), ("br2022r2", 2), ("br2026r1", 3), ("br2026r2", 4)):
        n = mr_nights_br.NIGHTS[name]()
        real = frames_at(n, FRACTIONS)
        rows += [{"night": name, **r} for r in score([(f, st) for f, _, st in real], n.prior, n.final, n.unpooled, "real")]
        rows += [{"night": name, **r} for r in score(br_sim_frames(name, n, 10_000 + seed * 100), n.prior, n.final, n.unpooled, "simulated")]
        print(name, flush=True)
    for name, seed in (("mx2012", 5), ("mx2018", 6), ("mx2024", 7)):
        n = mr_nights_mx.NIGHTS[name]()
        real = frames_at(n, FRACTIONS)
        rows += [{"night": name, **r} for r in score([(f, st) for f, _, st in real], n.prior, n.final, n.unpooled, "real")]
        rows += [{"night": name, **r} for r in score(mx_sim_frames(name, 10_000 + seed * 100), n.prior, n.final, n.unpooled, "simulated")]
        print(name, flush=True)
    d = pd.DataFrame(rows)
    g = d.groupby(["night", "order", "tau"]).agg(cov=("z", lambda z: float((z.abs() < 1.645).mean())), z=("z", "mean"), mae_pt=("err", lambda e: 100 * e.mean()),
                                                  sd_pt=("sd", lambda s: 100 * s.mean()), n=("z", "size")).reset_index()
    g.to_csv(OUT / "sim_vs_real.csv", index=False)

    def needed(x):
        ok = x[x["cov"] >= 0.90]
        return float(ok["tau"].min()) if len(ok) else np.nan

    tab = g.groupby(["night", "order"]).apply(lambda x: pd.Series({"tau_needed_cov90": needed(x), "cov_tau0": x[x["tau"] == 0.0]["cov"].iloc[0],
                                                                    "cov_tau0.015": x[x["tau"] == 0.015]["cov"].iloc[0], "mean_z_tau0": x[x["tau"] == 0.0]["z"].iloc[0],
                                                                    "mae_pt": x["mae_pt"].iloc[0], "frames": x["n"].iloc[0]}), include_groups=False).reset_index()
    tab.to_csv(OUT / "sim_vs_real_summary.csv", index=False)
    pd.set_option("display.width", 200)
    print(tab.round(3).to_string(index=False))
    transfer(g)


def transfer(g: pd.DataFrame) -> None:
    """tau chosen (repo rule: pooled coverage >= 0.90, worst >= 0.75) on the simulated vs the real versions of the OTHER nights,
    scored on the held-out night's real frames."""
    g = g.rename(columns={"cov": "covg"})
    nights = sorted(g["night"].unique())

    def rule(train, order):
        p = g[g["night"].isin(train) & (g["order"] == order)].groupby("tau")["covg"].agg(["mean", "min"])
        ok = p[(p["mean"] >= 0.90) & (p["min"] >= 0.75)]
        return float(ok.index.min()) if len(ok) else float(max(TAUS))

    rows = []
    for X in nights:
        train = [n for n in nights if n != X]
        ts, tr = rule(train, "simulated"), rule(train, "real")
        real = g[(g["night"] == X) & (g["order"] == "real")].set_index("tau")
        rows.append({"held_out": X, "tau_from_simulated": ts, "tau_from_real": tr, "coverage_real_night_with_sim_tau": real.loc[ts, "covg"],
                     "coverage_real_night_with_real_tau": real.loc[tr, "covg"], "sd_pt_sim_tau": real.loc[ts, "sd_pt"], "sd_pt_real_tau": real.loc[tr, "sd_pt"]})
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "tau_from_sim_vs_real.csv", index=False)
    print(R.round(3).to_string(index=False))
    print(R.drop(columns="held_out").mean().round(3).to_string())


if __name__ == "__main__":
    main()
