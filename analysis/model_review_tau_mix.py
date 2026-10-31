"""Hierarchical tau: a predictive MIXTURE over the tau values other nights needed, against one point tau chosen by the repo's rule.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_tau_mix.py

Frame level (analysis/model_review_frames.py output), base model at the repo defaults (rho 0.8, kappa 50,000). For a held-out night X:
  point (repo rule)   : smallest tau of the grid with pooled 90% coverage >= 0.90 and worst night >= 0.75 on the training nights;
  mixture             : each training night j contributes tau_j = the smallest grid tau at which that night alone has 90% coverage (a night-level
                        estimate of the reporting-order error). The predictive law of the final share is the equal-weight mixture of
                        Normal(share_hat, sd(tau_j)) over the training nights (an empirical-Bayes draw from the cross-election tau distribution);
                        the 90% band is its 5%-95% quantile range and P(A wins) = mean_j Phi(margin_hat / sd_j);
  max                 : the largest training tau_j (the conservative point choice).
Scores on the held-out frames (1% < counted <= 95%): coverage of the 90% band, mean band width (points), Winkler interval score
(points, width + 20 x miss distance; lower = better), Brier of P(eventual outcome). Schemes: leave-one-election-out and chronological.
Output: results/model_review/tau_mix.csv (SSD) + markdown on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_review_loeo as L  # noqa: E402
from mr_common import OUT  # noqa: E402

from brunder import nowcast_order as no  # noqa: E402

RHO, KAPPA = 0.8, 50_000.0


def score(held: pd.DataFrame, taus: list[float], mode: str) -> dict:
    a = no.frame_arrays(held)
    y = held["final"].to_numpy(float)
    if mode == "mixture":
        lo, hi = no.mixture_band(held["share_hat"], a["margin_sd0"], a["two_r"], a["total"], taus)
        p = no.mixture_pwin(held["margin_hat"], a["margin_sd0"], a["two_r"], taus)
    else:
        sd = no._sd_matrix(a["margin_sd0"], a["two_r"], a["total"], taus)[:, 0]
        lo, hi = held["share_hat"].to_numpy(float) - 1.645 * sd, held["share_hat"].to_numpy(float) + 1.645 * sd
        p = no.mixture_pwin(held["margin_hat"], a["margin_sd0"], a["two_r"], taus)
    a_wins = y[0] > 0.5
    wink = (hi - lo) + 20 * np.maximum(lo - y, 0) + 20 * np.maximum(y - hi, 0)
    return {"coverage": float(((lo <= y) & (y <= hi)).mean()), "width_pt": 100 * float((hi - lo).mean()), "winkler_pt": 100 * float(wink.mean()),
            "brier": float((((p if a_wins else 1 - p) - 1) ** 2).mean())}


def training_taus(d: pd.DataFrame, train: list[str]) -> list[float]:
    """Each training night's own needed tau (smallest grid tau with 90% frame coverage)."""
    out = []
    for n in train:
        h = d[d["night"] == n]
        a = no.frame_arrays(h)
        out.append(no.needed_tau(h["final"] - h["share_hat"], a["margin_sd0"], a["two_r"], a["total"], L.TAUS))
    return out


def main() -> None:
    d = L.load()
    d = d[(d["rho"] == RHO) & (d["kappa"] == KAPPA) & (d["cov"] == "none")]
    G = L.grid_stats(d)
    nights = sorted(d["night"].unique(), key=lambda n: L.DATES[n])
    rows = []
    for X in nights:
        for scheme in ("loeo", "chronological"):
            train = [n for n in nights if n != X] if scheme == "loeo" else [n for n in nights if L.DATES[n] < L.DATES[X]]
            if len(train) < 2:
                continue
            taus_j = training_taus(d, train)
            P = L.pooled(G, train)
            feas = P[(P["pcov"] >= L.COVERAGE_MIN) & (P["worst"] >= L.WORST_MIN)]
            tau_pt = float(feas["tau"].min()) if len(feas) else float(max(L.TAUS))
            held = d[d["night"] == X]
            for arm, taus, mode in (("point (repo rule)", [tau_pt], "point"), ("max of training needs", [max(taus_j)], "point"),
                                    ("mixture of training needs", taus_j, "mixture")):
                rows.append({"held_out": X, "scheme": scheme, "arm": arm, "taus": ";".join(f"{t:g}" for t in (taus if mode == "mixture" else taus)), **score(held, taus, mode)})
        print(X, flush=True)
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "tau_mix.csv", index=False)
    pd.set_option("display.width", 200)
    agg = R.groupby(["scheme", "arm"]).agg(nights=("held_out", "nunique"), coverage=("coverage", "mean"), min_cov=("coverage", "min"), width_pt=("width_pt", "mean"),
                                           winkler_pt=("winkler_pt", "mean"), brier=("brier", "mean")).round(3)
    print(agg.to_string())
    print(R.pivot_table(index=["scheme", "arm"], columns="held_out", values="coverage")[[n for n in nights if n in set(R["held_out"])]].round(2).to_string())
    print(R.pivot_table(index=["scheme", "arm"], columns="held_out", values="winkler_pt")[[n for n in nights if n in set(R["held_out"])]].round(2).to_string())


if __name__ == "__main__":
    main()
