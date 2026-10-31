"""Seat-count calibration of the Spain model on EARLIER nights, and the poll-quality re-check of the tau mixture.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/es_seat_calibrate.py --out data_public/es_seat_calibration_v1.json

Why. The share calibration in es_calibrate.py starts every night from the previous election carried forward (precount_sd 0.32-0.35).
The 2026 config starts from poll medians (precount_sd 0.12), so its share bands are narrower for the same tau; and seat bands
depend on correlated province errors that independent draws can understate (Australia 2025: 2PP coverage 100%, seat-count
coverage 10%). This script scores both on the simulated nights (real-snapshot order bias, PP/PSOE/Vox/LEFT, 5-95% of the draws):
  * share and SEAT 90% coverage by share of tables counted, interval score, rank Brier, Brier of 'PP + Vox >= 176 seats';
  * a 2026-like prior: previous election's province shapes raked to the night's true national shares x log-normal poll error (0.12);
  * each night's needed tau_nat under that prior (same rule as es_calibrate.night_taus), and the 2023 holdout with the mixture
    of the two earlier nights against the mixture adopted earlier (needed tau under a carried-forward prior).
Settings other than tau come from data_public/es_calibration_v1.json (chosen on earlier nights); precount_sd is the config's 0.12.
Seats are those of the lane-level D'Hondt on the night's final votes (exact for 2023; 2019 official seats differ slightly, docs/spain.md).
Retrospective replay, not pre-registered.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import es_calibrate as ec
from brunder import spain as es

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "data_public" / "es_calibration_v1.json"
POLL_SD = 0.12
SEEDS = 24


def settings_of(block: dict, mixture=()) -> es.Settings:
    d = {**block["settings"], "precount_sd": POLL_SD, "tau_mixture": tuple(mixture), "draws": 600}
    return es.Settings(**d)


def score(st, cases, bias, mult=1.0, seeds=SEEDS):
    df = ec.evaluate(st, cases, seeds, bias * mult, bias * mult, draws=600, poll_sd=POLL_SD)
    return {**ec.summary(df), **ec.seat_summary(df), "share_coverage90_by_milestone": {k: v["coverage90"] for k, v in ec.by_milestone(df).items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cal = json.loads(CAL.read_text())
    bias = cal["real_snapshot_bias"]["bias_nat"]
    fit, hold = ec.TRANSITIONS[:2], ec.TRANSITIONS[2:]
    old_fit = cal["tau_mixture"]["mixture_for_2023_check"]          # needed tau under the carried-forward prior (adopted earlier today)
    old_all = cal["tau_mixture"]["mixture_for_2026"]
    st_fit, st_all = settings_of(cal["fit_2016_2019"]), settings_of(cal["frozen_2026"])
    need_fit = ec.night_taus(st_fit, fit, SEEDS, bias, bias, poll_sd=POLL_SD)
    need_all = ec.night_taus(st_all, ec.TRANSITIONS, SEEDS, bias, bias, poll_sd=POLL_SD)
    new_fit = [v["tau"] for v in need_fit.values()]
    new_all = [v["tau"] for v in need_all.values()]
    print("mixture for the 2023 check: before", old_fit, "poll-prior", new_fit, "| for 2026: before", old_all, "poll-prior", new_all, flush=True)
    arms = {"before_mixture_from_carried_forward_prior": settings_of(cal["fit_2016_2019"], old_fit),
            "poll_prior_mixture": settings_of(cal["fit_2016_2019"], new_fit)}
    holdout = {}
    for name, m in (("bias_x0.5", .5), ("bias_x1", 1.0), ("bias_x2", 2.0)):
        holdout[name] = {arm: score(st, hold, bias, m) for arm, st in arms.items()}
        for arm, r in holdout[name].items():
            print("holdout 2023", name, arm, {k: round(v, 3) for k, v in r.items() if isinstance(v, float)}, flush=True)
    in_sample = {}
    for case in fit:
        in_sample[f"{case[0]}>{case[1]}"] = {arm: score(st, [case], bias) for arm, st in
                                              (("poll_prior_mixture", settings_of(cal["frozen_2026"], new_all)),)}
    out = {"poll_sd": POLL_SD, "seeds": SEEDS, "tau_grid": list(ec.TAU_GRID), "needed_tau_fit_nights": need_fit,
           "needed_tau_all_nights": need_all, "mixture_for_2023_check": {"before": old_fit, "poll_prior": new_fit},
           "mixture_for_2026": {"before": old_all, "poll_prior": new_all}, "holdout_2023": holdout, "in_sample_2019": in_sample}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
