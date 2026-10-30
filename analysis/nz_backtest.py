"""Backtest harness for the NZ replay: run model variants over (year, count order, seed) and aggregate by stage.

A *variant* is a dict: ``model`` (Settings overrides), ``poll`` (bool: use the final-3-week poll average as party prior),
``special`` (bool: apply the special-vote log shift estimated on the *other* training years, never on the scored year).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import nz_history as H
import nz_polls as P
import nz_replay as R
from brunder import newzealand as nz


def poll_shares(year: int, keys: list[str]) -> dict[str, float]:
    """Mean of the polls ending in the 21 days before election day (``nz_polls``), restricted to parties on the list."""
    avg, _ = P.window_average(year)
    return {k: v for k, v in avg.items() if k in keys}


def shift_table(year: int) -> dict[str, float]:
    """log(final share / election-night share) per party with >= 0.5% final share, one election."""
    E = R.election(year)
    fv, ov = E.final_party_votes(), E.ordinary_party_votes()
    fs, os_ = fv / fv.sum(), ov / ov.sum()
    return {k: float(np.log(fs[j] / os_[j])) for j, k in enumerate(E.party_keys) if fs[j] >= 0.005 and os_[j] > 0}


def special_shift(train_years: list[int], shrink: float = 1.0) -> dict[str, float]:
    tabs = [shift_table(y) for y in train_years]
    keys = set().union(*tabs)
    return {k: shrink * float(np.mean([t[k] for t in tabs if k in t])) for k in keys}


def _job(args):
    year, variant, scheme, seed, draws, stages, train = args
    E = R.election(year)
    kw = dict(variant.get("model", {}))
    st = nz.Settings(**kw)
    if variant.get("poll"):
        st = replace(st, poll_shares=poll_shares(year, E.party_keys))
    if variant.get("special"):
        sh = special_shift([y for y in train if y != year], variant.get("shrink", 1.0))
        st = replace(st, special_shift={k: v for k, v in sh.items() if k in E.party_keys})
    adv = year - 3 if st.advance_shrink > 0 and (H.HIST / str(year - 3) / "party-votes-by-voting-place-1.csv").exists() else None
    return R.run_one(year, st, scheme, seed, stages, draws, advance_from=adv)


def evaluate(variant: dict, years=(2017, 2020, 2023), schemes=R.SCHEMES, seeds=(1, 2), draws=1000,
             stages=R.STAGES, train=(2017, 2020), workers: int | None = None) -> pd.DataFrame:
    jobs = [(y, variant, s, sd, draws, stages, tuple(train)) for y in years for s in schemes for sd in seeds]
    with ProcessPoolExecutor(workers or max(1, (os.cpu_count() or 2) - 1)) as ex:
        rows = [r for part in ex.map(_job, jobs) for r in part]
    out = []
    for r in rows:
        out.append({"year": r["year"], "scheme": r["scheme"], "seed": r["seed"], "stage": r["stage"],
                    "cover": np.mean(r["cover"]), "n_cover": len(r["cover"]), "mae": np.mean(r["ae"]),
                    "brier": np.mean(r["brier"]), "ll": np.mean(r["ll"]),
                    "brier_nl": np.mean(r["brier_big"]) if r["brier_big"] else np.nan,
                    "brier_most": r["brier_most"], "p_most": r["p_most"],
                    "brier_margin": r.get("brier_margin", np.nan), "ll_margin": r.get("ll_margin", np.nan),
                    "pit_lo": np.mean(np.array(r["pit"]) < .05), "pit_hi": np.mean(np.array(r["pit"]) > .95),
                    "worst_ll": np.max(r["ll_worst"]), "share_z2": np.mean(np.abs(r["share_z"]) > 2),
                    "share_rmse": float(np.sqrt(np.mean(np.square(r["share_log_err"]))))})
    return pd.DataFrame(out)


def summarize(df: pd.DataFrame, by=("year", "stage")) -> pd.DataFrame:
    cols = ["cover", "mae", "brier", "ll", "brier_nl", "brier_most", "brier_margin", "ll_margin",
            "pit_lo", "pit_hi", "worst_ll", "share_z2", "share_rmse"]
    return df.groupby(list(by))[cols].mean()
