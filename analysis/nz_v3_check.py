"""Pre-registration checks for nz2026_v3 (run 2026-10-31, before any 2026 count): 2023 out-of-sample party-vote-SHARE vs
party-SEAT 90% coverage, seat-bracket / most-seats scores, by share of voting places counted, under (a) the study's poll
prior (all polls ending in the 21 days before election day) and (b) the 2026-style as-of prior (polls ending <= 12 days
before, as the 2026 poll block was cut on 2026-10-26 for a 2026-11-07 election).  Settings are the selected v3 (2017/2020).

    uv run python analysis/nz_v3_check.py [year]
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import nz_backtest as B          # noqa: E402
import nz_polls as P             # noqa: E402
import nz_replay as R            # noqa: E402
import nz_v3_study as S          # noqa: E402
from brunder import newzealand as nz  # noqa: E402


def _job(a):
    year, asof_days, scheme, seed, draws = a
    v3 = json.loads((S.OUT / "selected.json").read_text())["v3"]
    kw = {**v3["model"], "special_log_sd": S.HONEST_SPECIAL_SD}
    E = R.election(year)
    st = nz.Settings(**kw)
    if asof_days is None:
        poll = B.poll_shares(year, E.party_keys)
    else:
        asof = (pd.Timestamp(P.ELECTION[year]) - pd.Timedelta(days=asof_days)).strftime("%Y-%m-%d")
        avg, w = P.window_average(year, asof=asof)
        poll = {k: v for k, v in avg.items() if k in E.party_keys}
    sh = B.special_shift([y for y in S.TRAIN if y != year], 1.0)
    st = replace(st, poll_shares=poll, special_shift={k: v for k, v in sh.items() if k in E.party_keys}, draws=draws)
    units, od = R.units_for(E), R.Order(E, scheme, seed)
    truth, fv = E.official_seats(), E.final_party_votes()
    fsh = fv / fv.sum()
    rows = []
    for s in R.STAGES:
        p = nz.project(units, od.counts(E, s), st, E.party_keys)
        rng = np.random.default_rng(1000 * seed + int(100 * s) + year)
        votes, _, seats = nz.seat_draws(p, st, rng)
        m = R.score(seats, votes, E, p.parties)
        ix = {k: i for i, k in enumerate(E.party_keys)}
        sc = []
        shr = votes / votes.sum(1, keepdims=True)
        for j, k in enumerate(p.parties):
            if fsh[ix[k]] < 0.03:
                continue
            lo, hi = np.quantile(shr[:, j], [.05, .95])
            sc.append(float(lo <= fsh[ix[k]] <= hi))
        rows.append({"stage": s, "scheme": scheme, "seat_cover": np.mean(m["cover"]), "share_cover": np.mean(sc),
                     "brier": np.mean(m["brier"]), "ll": np.mean(m["ll"]), "brier_most": m["brier_most"]})
    return rows


def main(year: int = 2023, draws: int = 2000):
    out = {}
    for label, days in (("study prior (21-day window)", None), ("2026-style prior (polls ending <=12d before)", 12)):
        jobs = [(year, days, sc, sd, draws) for sc in S.SCHEMES for sd in S.SEEDS]
        with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as ex:
            df = pd.DataFrame([r for part in ex.map(_job, jobs) for r in part])
        cols = ["share_cover", "seat_cover", "brier", "ll", "brier_most"]
        print(f"\n== {year} {label}: overall\n{df[cols].mean().round(3).to_string()}")
        print(f"-- by stage\n{df.groupby('stage')[cols].mean().round(3).to_string()}")
        print(f"-- night order only\n{df[df.scheme == 'night'].groupby('stage')[cols].mean().round(3).to_string()}")
        out[label] = df
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2023)
