"""Historic-only uncertainty calculation for configs/nz2026_v1.toml.

Inputs are Electoral Commission ``overall-results-summary.csv`` browser downloads for 2017, 2020 and 2023
under ``$BRUNDER_DATA/raw/newzealand/<year>/``. No 2026 return is read. The reported number is the pooled
root-mean-square centred log change in party share over 2017→2020 and 2020→2023 for parties present with
at least 0.3% at both endpoints.
"""
from __future__ import annotations

import math

import numpy as np

from brunder import newzealand as nz
from brunder.paths import RAW

ALIASES = {"Māori Party": "Te Pāti Māori", "The Opportunities Party (TOP)": "Opportunity Party",
           "New Conservative": "Conservative", "New Conservatives": "Conservative"}


def shares(year: int) -> dict[str, float]:
    d = nz.read_summary(RAW / "newzealand" / str(year) / "overall-results-summary.csv")
    total = d["party_votes"].sum()
    return {ALIASES.get(r.party, r.party): r.party_votes / total for r in d.itertuples()}


def transition(a: dict[str, float], b: dict[str, float]) -> tuple[float, int]:
    names = [k for k in a.keys() & b.keys() if a[k] >= .003 and b[k] >= .003]
    x = np.array([math.log(b[k] / a[k]) for k in names])
    x -= x.mean()
    return float(np.mean(x * x)), len(x)


if __name__ == "__main__":
    s = {y: shares(y) for y in (2017, 2020, 2023)}
    vals = [transition(s[a], s[b]) for a, b in ((2017, 2020), (2020, 2023))]
    for pair, (v, n) in zip(("2017→2020", "2020→2023"), vals):
        print(f"{pair}: n={n}, RMS centred log-share change={math.sqrt(v):.3f}")
    pooled = math.sqrt(sum(v * n for v, n in vals) / sum(n for _, n in vals))
    print(f"pooled RMS={pooled:.3f}")
