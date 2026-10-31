"""Hungary 2026: "tau mixture over earlier nights (chosen after the result was known)", a retrospective SENSITIVITY.

Hungary's model has genuine reporting-order errors, `tau` of brunder.nowcast: one for the 106 district shares (a national error shared by
all districts, `nat = tau z_nat` in hungary.district_draws) and one for the national list vote (`hungary.list_race`). v1 uses one point
value of each, chosen on the 2022 night alone (district 0.0, list 0.05); v2 (configs/hu2026_v2.toml) another point value from the common rule.

This script follows `nowcast_order`: every EARLIER night that can be replayed gives the tau it needed, and the model is run once per
earlier night's tau, mixing the draws with equal weights.
* needed tau of a night = the smallest value of the v1/v2 grid (hu_calibrate.TAUS) at which that night's own 90% bands covered the final
  on >= 90% of its frames with 1% < counted <= 95% (`needed_tau`): list model = the national list share; district model = the 106 district
  shares AND the national two-way district share (the v2 rule applied to one night), at v1's sigma_mun0 = 0.10 and kappa = 200,000.
* nights (analysis/hu_calibrate_v2.py, which also holds the 2018/2014 simulated count orders): district 2022 (real archived order) and 2018
  (simulated orders of the official station results); list 2022, 2018 and 2014 (simulated; county prior from 2010).
* the district tau mixture (2 values) and the list tau mixture (3 values) are crossed, equal weights: 6 components. Every other setting
  (postal, envelope, turnout, sigma_mun0, kappa, ...) and every random number is v1's. Probability of a market = mean over components
  (equivalently: pooled draws); the list-share band is the 5-95% quantile range of the equal-weight normal mixture; the district-share
  band uses the pooled mean and sd of all components' draws.
Nothing from 2026 chooses a tau or a weight; the mixture itself was adopted after the result was known.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_hu.py calibrate   # earlier nights -> $BRUNDER_DATA/results/mix/hu/calibration/
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_hu.py run         # 2026 replay, 6 components -> .../hu/
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_hu.py export      # -> site/data/hu2026 (new fields only)
"""

from __future__ import annotations

import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from brunder import hungary as hu  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402
from brunder.paths import DATA  # noqa: E402

OUT = DATA / "results" / "mix" / "hu"
CAL = OUT / "calibration"
CFG = ROOT / "configs" / "hu2026_v1.toml"
SIGMA, KAPPA = 0.10, 200_000.0                      # v1's district settings the earlier nights are scored at
KEY = "mix"
LABEL = "tau mixture over earlier nights (chosen after the result was known)"
CONFIG = "analysis/mix_hu.py"
NOTE = ("Instead of one value for how wrong the early count can be, the model is run with each value the earlier nights needed "
        "(districts: 2022 and 2018; national list: 2022, 2018 and 2014; the 2018 and 2014 count orders are simulated), the draws "
        "mixed with equal weights; the 2026 count chose nothing, but the idea was adopted after the result was known.")
HEADLINE = "list"
Z90 = 1.645


def needed_from_coverage(cov: pd.Series, level: float = 0.90) -> float:
    """Smallest tau (index) whose coverage is >= level; the largest if none (the `nowcast_order.needed_tau` rule on a coverage table)."""
    ok = cov[cov >= level]
    return float(ok.index.min()) if len(ok) else float(cov.index.max())


def district_needs(dg22: pd.DataFrame, d18: pd.DataFrame) -> dict[str, float]:
    """Per night: the smallest tau at which BOTH the district-share coverage and the national two-way share coverage are >= 0.90 (the
    repo's v2 rule applied to a single night), at SIGMA / KAPPA."""
    sel = lambda x: x[(x["sigma_mun0"] == SIGMA) & (x["kappa"] == KAPPA)]  # noqa: E731
    a = sel(dg22).set_index("tau")
    x = sel(d18[(d18["counted"] > 0.01) & (d18["counted"] <= 0.95)])
    g = x.groupby("tau")
    b = pd.DataFrame({"cov": g["cov"].sum() / g["n"].sum(), "nat": g["nat_z"].apply(lambda z: float((z.abs() < Z90).mean()))})
    return {"2022": needed_from_coverage(pd.concat([a["coverage90"], a["nat_coverage90"]], axis=1).min(axis=1)),
            "2018": needed_from_coverage(b.min(axis=1))}


def list_needs(lists: dict[str, pd.DataFrame]) -> dict[str, float]:
    return {y: needed_from_coverage(df[(df["counted"] > 0.01) & (df["counted"] <= 0.95)].assign(c=lambda t: t["z"].abs() < Z90)
                                    .groupby("tau")["c"].mean()) for y, df in lists.items()}


def components(d_taus: list[float], l_taus: list[float]) -> list[tuple[float, float]]:
    """Every (district tau, list tau) pair, equal weights (duplicates count: two nights with the same tau weigh double)."""
    return list(itertools.product(d_taus, l_taus))


def pooled_moments(means: np.ndarray, sds: np.ndarray) -> tuple[float, float]:
    """Mean and sd of an equal-weight mixture of components with the given means and sds."""
    m = float(np.mean(means))
    return m, float(np.sqrt(np.mean(np.asarray(sds) ** 2 + np.asarray(means) ** 2) - m**2))


def calibrate() -> None:
    import hu_calibrate_v2 as v2
    CAL.mkdir(parents=True, exist_ok=True)
    v2.OUT = CAL
    v2.main()


def needs() -> dict:
    dg22 = pd.read_csv(CAL / "calibration_v2_districts_2022.csv")
    d18 = pd.read_csv(CAL / "calibration_v2_districts_2018.csv")
    lists = {y: pd.read_csv(CAL / f"calibration_v2_list_{y}.csv") for y in ("2022", "2018", "2014")}
    return {"district": district_needs(dg22, d18), "list": list_needs(lists)}


def run() -> None:
    cfg = hu.load_config(CFG)
    nd = needs()
    d_taus, l_taus = list(nd["district"].values()), list(nd["list"].values())
    comps = components(d_taus, l_taus)
    print("needed taus", nd, "->", len(comps), "components", comps, flush=True)
    base = hu.settings_from_config(cfg)
    sets = [replace(base, nowcast=replace(base.nowcast, tau=td), list_nowcast=replace(base.list_nowcast, tau=tl)) for td, tl in comps]
    year = cfg["year"]
    prior = hu.prior_2022_for_2026(cfg)
    ctx = {"pl": hu.party_lists(year), "reg": hu.registered(year), "reg_d": hu.district_registered(year)}
    cands = hu.candidates(year)
    rngs = [np.random.default_rng(cfg["seed"]) for _ in comps]
    rows, last_list = [], None
    for v in sorted(hu.versions(year), key=lambda v: hu.version_time(year, v)):
        p = hu.read_version(year, v, cands)
        if p is None or p["smd"] is None:
            continue
        if p["list"] is None:
            p["list"] = last_list
        last_list = p["list"]
        os_ = [hu.evaluate(p, prior, cfg, s, ctx, r) for s, r in zip(sets, rngs)]
        row = {"v": v, "t": p["t"], "counted": os_[0]["smd"]["counted"], "list_counted": os_[0]["list"]["counted"]}
        sh = np.array([o["list"]["share"] for o in os_])
        assert np.ptp(sh) < 1e-9, "list centre must not depend on tau"
        row["list_share"] = float(sh[0])
        for k, o in enumerate(os_):
            row[f"list_sd_{k}"] = o["list"]["sd"]
        row["smd_share"], row["smd_sd"] = pooled_moments(np.array([o["smd"]["share"] for o in os_]), np.array([o["smd"]["sd"] for o in os_]))
        for m in os_[0]["P"]:
            row[f"P:{m}"] = float(np.mean([o["P"][m] for o in os_]))
        rows.append(row)
        print(v, f"counted={row['counted']:.3f} list={row['list_share']:.4f} P(most)={row.get('P:most_seats_tisza', float('nan')):.3f}", flush=True)
    R = pd.DataFrame(rows)
    sd_cols = [f"list_sd_{k}" for k in range(len(comps))]
    lo, hi = [], []
    for _, r in R.iterrows():           # 5-95% quantiles of the equal-weight normal mixture, one frame at a time (sds as "taus")
        a, b = no.mixture_band([r["list_share"]], [0.0], [1.0], [0.5], [r[c] for c in sd_cols])
        lo.append(a[0])
        hi.append(b[0])
    R["list_lo"], R["list_hi"] = lo, hi
    R.to_csv(OUT / "releases.csv", index=False)
    (OUT / "components.json").write_text(json.dumps({"needed": nd, "components": comps}, indent=1))


def export() -> None:
    import hu_export as ex
    from sens_export import write_sensitivity, write_v2_block
    comp = json.loads((OUT / "components.json").read_text())
    R = pd.read_csv(OUT / "releases.csv", parse_dates=["t"]).sort_values("t")
    cfg = hu.load_config(CFG)
    import hu_score as hs
    F = hs.finals(cfg)
    series, finals = {}, {}
    for rid, (_, _, _, cols, pcol, _, _) in ex.RACES.items():
        df = pd.DataFrame({"t": R["t"], "P": R[pcol]})
        if rid == "list":
            df["share"], df["lo"], df["hi"] = R["list_share"], R["list_lo"], R["list_hi"]
        elif rid == "seats":
            df["share"], df["sd"] = R["smd_share"], R["smd_sd"]
        series[rid] = df
        finals[rid] = {"list": F["list_share"], "seats": F["smd_share"]}.get(rid)
    extra = {"components": {"needed_taus": comp["needed"], "pairs_district_list": comp["components"]}}
    write_sensitivity("hu2026", KEY, LABEL, CONFIG, NOTE, series, finals, HEADLINE, extra_stats=extra)
    write_v2_block("hu2026", finals, HEADLINE)


if __name__ == "__main__":
    {"calibrate": calibrate, "run": run, "export": export}[sys.argv[1]]()
