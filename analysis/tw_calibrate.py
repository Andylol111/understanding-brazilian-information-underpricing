"""Calibrate the Taipei count model on 2014->2018 and score the frozen settings on 2018->2022.

CEC retains final polling-station results but no timestamps.  Each replay therefore uses 20 seeded random orders and
20 adverse small-stations-first orders.  Poll targets were public before each election; no target-year result is used
in its prior.  Run: ``uv run python analysis/tw_calibrate.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import taiwan as tw

CHECKPOINTS = (.01, .05, .10, .25, .50, .75, .90)
POLL_TARGETS = {
    # Mean of the two 2018 polls reported by Taipei Times 2018-09-18, normalized over named choices.
    2018: {"KMT": .384, "DPP": .119, "TPP": .485, "OTHER": .012},
    # RWNews 2022-11-01/05 topline (36 Chiang, 33 Chen, 28 Huang; remainder other).
    2022: {"KMT": .36, "DPP": .33, "TPP": .28, "OTHER": .03},
}


def replay(prev_year: int, year: int, order: str, seed: int, own_votes: float, swing_votes: float):
    history = tw.load_history()
    prior = tw.historical_prior(prev_year, POLL_TARGETS[year], history)
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


def evaluate(prev_year: int, year: int, tau: float, own_votes: float = 100, swing_votes: float = 5_000):
    history = tw.load_history(); candidates = list(tw.PARTIES)
    truth = tw.historical_units(year, history)[candidates].sum().to_numpy(float)
    truth /= truth.sum(); winner = int(truth.argmax()); rows = []
    for order in ("random", "small-first"):
        for seed in range(1, 21):
            r = replay(prev_year, year, order, seed, own_votes, swing_votes)
            for _, x in r.iterrows():
                shares = x[candidates].to_numpy(float)
                sd = max(.002, tau * (1 - x["counted"]))
                rng = np.random.default_rng(seed * 1000 + int(100 * x["checkpoint"]))
                draws = shares + rng.normal(0, sd, (10_000, len(candidates)))
                probs = np.bincount(draws.argmax(1), minlength=len(candidates)) / len(draws)
                outcome = np.arange(len(candidates)) == winner
                rows.append({"year": year, "order": order, "seed": seed,
                    "checkpoint": x["checkpoint"], "coverage90": np.mean(np.abs(shares - truth) <= 1.645 * sd),
                    "brier": np.mean((probs - outcome) ** 2),
                    "log_loss": -np.log(max(probs[winner], 1e-6)), "p_winner": probs[winner]})
    return pd.DataFrame(rows)


def main():
    # Settings selection uses 2018 only.  This grid is intentionally small and declared here.
    grid = []
    history = tw.load_history(); truth = tw.historical_units(2018, history)[list(tw.PARTIES)].sum().to_numpy(float)
    truth /= truth.sum()
    for own in (100, 300, 1000, 3000):
        for swing in (5000, 20000, 50000, 100000):
            errors = []
            for order in ("random", "small-first"):
                for seed in range(1, 21):
                    r = replay(2014, 2018, order, seed, own, swing)
                    errors.extend(np.abs(r[list(tw.PARTIES)].to_numpy() - truth).ravel())
            grid.append({"own_votes": own, "swing_votes": swing, "mae": np.mean(errors)})
    best = min(grid, key=lambda x: x["mae"])
    calibration = evaluate(2014, 2018, .02, best["own_votes"], best["swing_votes"])
    backtest = evaluate(2018, 2022, .02, best["own_votes"], best["swing_votes"])
    table = backtest.groupby("checkpoint")[["coverage90", "brier", "log_loss", "p_winner"]].mean()
    print("selected", json.dumps(best)); print("2018 coverage by order")
    print(calibration.groupby("order")["coverage90"].mean().round(4).to_string())
    print("\n2022 out-of-sample (mean over 40 simulated orders)")
    print(table.round(4).to_string())
    out = Path("results/taiwan"); out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "tw2022_backtest.csv")
    (out / "tw2022_backtest.json").write_text(json.dumps({"selected": best,
        "calibration_coverage": calibration.groupby("order")["coverage90"].mean().to_dict(),
        "backtest": table.reset_index().to_dict("records")}, indent=2))


if __name__ == "__main__": main()
