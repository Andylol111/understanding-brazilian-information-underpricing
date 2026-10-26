"""Power analysis: how many signals each untested/undetermined hypothesis needs, and how long the
live recorder must run to get them. Sets the stopping rules in prereg/forward_v1.toml.

n needed (one-sided, Holm worst case α/m):  n = deff · ((z_{1−α/m} + z_{power}) · sd / δ)²
deff = (event-cluster bootstrap SE / iid SE)², estimated from the holdout signals.

Run after the holdout studies: uv run python analysis/power.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import stats, universe
from brunder.paths import ROOT

RES = ROOT / "results"
POWER = 0.8


def design(d: pd.DataFrame, col: str) -> tuple[float, float]:
    sd = float(d[col].std())
    se_iid = sd / np.sqrt(len(d))
    b = stats.cluster_bootstrap(d, stats.mean_of(col), B=1000, seed=7)
    se_cl = (b["hi95"] - b["lo95"]) / (2 * 1.96)
    return sd, max((se_cl / se_iid) ** 2, 1.0)


def n_needed(sd: float, deff: float, delta: float, m: int, alpha: float = 0.05) -> int:
    z = norm.ppf(1 - alpha / m) + norm.ppf(POWER)
    return int(np.ceil(deff * (z * sd / delta) ** 2))


def main() -> None:
    pd.set_option("display.width", 200)
    rows = []
    # deep continuation, pooled over categories and both splits
    sig = pd.concat([pd.read_parquet(RES / "studies" / "shock_depth_v1" / s / "signals.parquet") for s in ("discovery", "holdout")])
    deep = sig[sig["deep"]]
    sd, deff = design(deep, "drift")
    months = (deep["t"].max() - deep["t"].min()).days / 30.4
    rate = len(deep) / months
    for delta in (0.02, 0.03, 0.05):
        for m in (1, 10):
            n = n_needed(sd, deff, delta, m)
            rows.append({"hypothesis": "deep_continuation" + (" (per category, Holm m=10)" if m == 10 else " (pooled)"),
                         "effect": delta, "sd": sd, "deff": deff, "n_needed": n, "have": len(deep),
                         "months_at_current_sampling": n / rate if m == 1 else n / (rate / 10)})
    # mentions late certainty, net return per $
    lc = pd.read_parquet(RES / "studies" / "late_certainty_v1" / "holdout" / "signals.parquet")
    lc = lc[lc["category"] == "mentions"]
    sd, deff = design(lc, "ret")
    ev, mk = universe.load()
    listed = ev[ev["category"] == "mentions"]
    per_event = len(lc) / lc["event_id"].nunique()
    for delta in (0.01, 0.021):
        n = n_needed(sd, deff, delta, 1)
        rows.append({"hypothesis": "mentions late_certainty (live)", "effect": delta, "sd": sd, "deff": deff,
                     "n_needed": n, "have": 0, "months_at_current_sampling": np.nan,
                     "signals_per_event": per_event})
    out = pd.DataFrame(rows)
    out.to_csv(RES / "power.csv", index=False)
    print(out.round(3).to_string(index=False))
    print(f"\nshock-depth sample: {len(deep)} deep shocks over {months:.1f} months ({rate:.1f}/month, all categories)")
    print(f"mentions holdout: {len(lc)} late-certainty signals from {lc['event_id'].nunique()} events "
          f"({per_event:.1f}/event); universe sampled {len(listed)} mentions events")


if __name__ == "__main__":
    main()
