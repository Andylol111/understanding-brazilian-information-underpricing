"""Calibrate the Taipei count model on 2014->2018 and score the frozen settings on 2018->2022.

CEC retains final polling-station results but no timestamps.  Each replay therefore uses 20 seeded random orders and
20 adverse small-stations-first orders.  Poll targets were public before each election; no target-year result is used
in its prior.  Run: ``uv run python analysis/tw_calibrate.py``.

Reporting-order error ``tau``: the point rule (v1, kept as the documented baseline) is one tau; the adopted rule is an
equal-weight mixture over each earlier night's own needed tau (docs/model_review.md, `brunder.nowcast_order`).  A "night" is
one election x one order family (random / small-stations-first); the 2022 check uses only the 2018 nights, the 2026
mixture uses 2018 and 2022.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scipy.stats import norm

from brunder import taiwan as tw
from brunder.nowcast_order import TAU_GRID

CHECKPOINTS = (.01, .05, .10, .25, .50, .75, .90)


def replay(prev_year: int, year: int, order: str, seed: int, own_votes: float, swing_votes: float):
    history = tw.load_history()
    prior = tw.historical_prior(prev_year, tw.HISTORICAL_POLLS[year], history)
    final = history[history.year == year].copy()
    rng = np.random.default_rng(seed)
    if order == "random":
        key = rng.random(len(final))
    elif order == "small-first":
        key = final["valid"].to_numpy() + rng.normal(0, 100, len(final))
    else:
        raise ValueError(order)
    ordering = np.argsort(key)
    cumulative = final["valid"].to_numpy()[ordering].cumsum()
    candidates = list(tw.PARTIES)
    st = tw.Settings(own_votes=own_votes, swing_votes=swing_votes, tau=.02, draws=10_000)
    rows = []
    for q in CHECKPOINTS:
        selected = ordering[:np.searchsorted(cumulative, q * final["valid"].sum()) + 1]
        d = final.iloc[selected]
        count = d.groupby("unit_id")[candidates].sum()
        count["places"] = d.groupby("unit_id")["station_id"].nunique()
        p = tw.project(prior, count, st, candidates)
        rows.append({"checkpoint": q, "counted": p.counted_frac,
                     **{c: p.shares[i] for i, c in enumerate(candidates)}})
    return pd.DataFrame(rows)


ORDERS = ("random", "small-first")
SEEDS = range(1, 21)
Z90 = 1.645


def replays(prev_year: int, year: int, own_votes: float, swing_votes: float) -> dict[str, list[pd.DataFrame]]:
    return {o: [replay(prev_year, year, o, s, own_votes, swing_votes) for s in SEEDS] for o in ORDERS}


def truth_shares(year: int) -> np.ndarray:
    t = tw.historical_units(year, tw.load_history())[list(tw.PARTIES)].sum().to_numpy(float)
    return t / t.sum()


def needed_tau(frames: list[pd.DataFrame], truth: np.ndarray, floor: float = .002, level: float = .90) -> float:
    """Smallest TAU_GRID value whose pooled 90% interval coverage (frames x candidates) is >= level."""
    c = list(tw.PARTIES)
    err = np.concatenate([np.abs(f[c].to_numpy(float) - truth).ravel() for f in frames])
    cnt = np.concatenate([np.repeat(f["counted"].to_numpy(float), len(c)) for f in frames])
    for t in TAU_GRID:
        if np.mean(err <= Z90 * np.maximum(floor, t * (1 - cnt))) >= level: return float(t)
    return float(TAU_GRID[-1])


def night_taus(reps: dict[str, list[pd.DataFrame]], truth: np.ndarray) -> dict[str, float]:
    return {o: needed_tau(reps[o], truth) for o in ORDERS}


def score(reps: dict[str, list[pd.DataFrame]], truth: np.ndarray, taus: tuple[float, ...], floor: float = .002,
          draws: int = 10_000) -> pd.DataFrame:
    """Coverage of the central 90% band of the equal-weight normal mixture (one tau = the point rule), Brier, log loss and
    P(eventual winner) per checkpoint; draws cycle through the components exactly as `taiwan.win_probabilities` does."""
    c = list(tw.PARTIES); winner = int(truth.argmax()); outcome = np.arange(len(c)) == winner; rows = []
    for order, frames in reps.items():
        for seed, r in zip(SEEDS, frames):
            for _, x in r.iterrows():
                shares = x[c].to_numpy(float)
                sds = np.array([max(floor, t * (1 - x["counted"])) for t in taus])
                cdf = norm.cdf((truth[:, None] - shares[:, None]) / sds[None, :]).mean(axis=1)
                rng = np.random.default_rng(seed * 1000 + int(100 * x["checkpoint"]))
                sd = np.resize(sds, draws)[:, None]
                d = shares + sd * rng.normal(0, 1, (draws, len(c)))
                probs = np.bincount(d.argmax(1), minlength=len(c)) / draws
                rows.append({"order": order, "seed": seed, "checkpoint": x["checkpoint"],
                             "coverage90": np.mean((cdf >= .05) & (cdf <= .95)),
                             "brier": np.mean((probs - outcome) ** 2),
                             "log_loss": -np.log(max(probs[winner], 1e-6)), "p_winner": probs[winner]})
    return pd.DataFrame(rows)


def main():
    # Settings selection uses 2018 only.  This grid is intentionally small and declared here.
    grid = []
    truth18 = truth_shares(2018)
    for own in (100, 300, 1000, 3000):
        for swing in (5000, 20000, 50000, 100000):
            errors = []
            for order in ORDERS:
                for seed in SEEDS:
                    r = replay(2014, 2018, order, seed, own, swing)
                    errors.extend(np.abs(r[list(tw.PARTIES)].to_numpy() - truth18).ravel())
            grid.append({"own_votes": own, "swing_votes": swing, "mae": np.mean(errors)})
    best = min(grid, key=lambda x: x["mae"])
    r18 = replays(2014, 2018, best["own_votes"], best["swing_votes"])
    r22 = replays(2018, 2022, best["own_votes"], best["swing_votes"])
    truth22 = truth_shares(2022)
    need18, need22 = night_taus(r18, truth18), night_taus(r22, truth22)
    print("selected", json.dumps(best))
    print("needed tau (smallest TAU_GRID value with 90% coverage), by night:")
    print(f"  2018 (prior 2014): {need18}\n  2022 (prior 2018): {need22}")
    point = .02  # v1 point tau, frozen 2026-10-30
    mix22 = tuple(need18.values())  # settings available before the 2022 night: 2018 nights only
    mix26 = tuple([*need18.values(), *need22.values()])  # settings available before 2026
    print(f"point tau (v1) {point}; 2022 check mixture {mix22}; 2026 mixture {mix26}")
    cal = score(r18, truth18, (point,))
    print("\n2018 in-sample coverage, point tau, by order"); print(cal.groupby("order")["coverage90"].mean().round(4).to_string())
    tables = {"point": score(r22, truth22, (point,)), "mixture": score(r22, truth22, mix22)}
    cols = ["coverage90", "brier", "log_loss", "p_winner"]
    out = Path("results/taiwan"); out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, t in tables.items():
        by = t.groupby("checkpoint")[cols].mean()
        print(f"\n2022 out-of-sample, {name} (mean over 40 simulated orders)"); print(by.round(4).to_string())
        print(f"  all checkpoints: coverage {t.coverage90.mean():.4f}  brier {t.brier.mean():.5f}  log loss {t.log_loss.mean():.5f}")
        by.to_csv(out / f"tw2022_backtest_{name}.csv")
        summary[name] = by.reset_index().to_dict("records")
    (out / "tw2022_backtest.json").write_text(json.dumps({"selected": best, "needed_tau_2018": need18,
        "needed_tau_2022": need22, "point_tau": point, "mixture_for_2022_check": mix22, "mixture_for_2026": mix26,
        "calibration_coverage_point_2018": cal.groupby("order")["coverage90"].mean().to_dict(), "backtest": summary}, indent=2))


if __name__ == "__main__": main()
