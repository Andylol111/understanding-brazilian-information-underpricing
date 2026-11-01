"""brunder.us_votetype: the reporting-order (vote-type) extension on top of brunder.us.project (synthetic counties; no SSD data)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us
from brunder import us_votetype as vt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

CFG = {"election": {"state": "ca", "race": "gov", "a": {"party": "DEM"}, "b": {"party": "REP"}},
       "prior": {}, "model": {"tau": 0.03, "rho": 0.3, "sigma_mun0": 0.08}, "count": {"f_complete": 0.98}}


def _pri(seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = us.counties("ca")
    return pd.DataFrame({"unit_id": c["unit_id"], "name": c["name"], "s0": np.full(len(c), 0.5),
                         "V": rng.integers(20_000, 400_000, len(c)).astype(float)})


def _rows(pri, frac, share_early, share_late, early_frac, only_early=False, split=True) -> pd.DataFrame:
    """Counted votes of every county: `frac` of its expected votes, of which `early_frac` is early (Dem share share_early) and the rest late."""
    n = pri["V"].to_numpy() * frac
    ne = n if only_early else n * early_frac
    nl = n - ne
    d = ne * share_early + nl * share_late
    base = {"level": "county", "name": "", "race": "gov", "candidate": "", "units": 10.0, "reporting": 5.0, "ballots": np.nan, "registered": np.nan,
            "outstanding": np.nan}
    a = pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "DEM", "votes": d, "early": ne * share_early if split else np.nan})
    b = pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "REP", "votes": n - d, "early": ne * (1 - share_early) if split else np.nan})
    return pd.concat([a, b], ignore_index=True)[us.COLS]


def _night(pri, pi=0.5, se=0.62, sl=0.42) -> pd.DataFrame:
    """One finished past night as profile input: cumulative per-county d, r and early d / r."""
    E = pri["V"].to_numpy()
    e = E * pi
    return pd.DataFrame({"ts": pd.Timestamp("2020-11-04", tz="UTC"), "unit_id": pri["unit_id"], "d": e * se + (E - e) * sl,
                         "r": e * (1 - se) + (E - e) * (1 - sl), "ed": e * se, "er": e * (1 - se)})


def test_profile_recovers_early_share_and_gap_and_ignores_unusable_splits():
    pri = _pri()
    p = vt.fit_profile({2020: [_night(pri)]})
    assert p.pi_state == pytest.approx(0.5) and p.gap_state == pytest.approx(0.20, abs=1e-9)
    assert p.pi.between(0.499, 0.501).all() and p.gap.between(0.19, 0.21).all() and p.n_cycles == 1
    nosplit = _night(pri).assign(ed=np.nan, er=np.nan)
    assert vt.fit_profile({2020: [nosplit]}) is None                                # no early split in the feed
    assert vt.fit_profile({2020: [_night(pri, pi=0.001)]}) is None                  # the split exists but is not the early vote (NV 2020)
    both = vt.fit_profile({2020: [_night(pri, pi=0.8)], 2024: [_night(pri, pi=0.4)]}, weights={2020: 3.0, 2024: 1.0})
    assert both.pi_state == pytest.approx(0.7)                                       # cycle weights


def test_no_profile_or_zero_weights_leave_the_projection_untouched():
    pri = _pri()
    rows = _rows(pri, 0.3, 0.62, 0.42, 1.0, only_early=True)
    res, units = us.project(rows, CFG, pri)
    r0, u0 = vt.adjust(res, units, None, 0.3, 50_000.0)
    assert r0["share_hat"] == res["share_hat"] and r0["margin_sd"] == res["margin_sd"] and u0 is units
    prof = vt.fit_profile({2020: [_night(pri)]})
    r1, u1 = vt.adjust(res, units, prof, 0.3, 50_000.0, vt.Params(lam=0.0, kappa=0.0))
    assert r1["share_hat"] == pytest.approx(res["share_hat"], abs=1e-12) and r1["margin_sd"] == pytest.approx(res["margin_sd"])
    assert u1["rem_margin"].to_numpy() == pytest.approx(units["rem_margin"].to_numpy())


def test_only_early_votes_counted_pushes_the_projection_toward_the_late_vote_and_widens_it():
    pri = _pri()
    prof = vt.fit_profile({2020: [_night(pri)]})
    rows = _rows(pri, 0.30, 0.62, 0.42, 1.0, only_early=True)                       # only the early (D-leaning) batches are in
    res, units = us.project(rows, CFG, pri)
    r, u = vt.adjust(res, units, prof, 0.3, 50_000.0, vt.Params(lam=1.0, kappa=1.0))
    assert r["share_hat"] < res["share_hat"] - 0.005 and r["vt_shift_pts"] < -0.5       # the late half leans less Democratic than the counted part
    assert r["margin_sd"] > res["margin_sd"] and r["vt_exposure"] > 0
    # the margin bookkeeping is the nowcast's: share = 0.5 + margin / (2 total), total unchanged
    total = units["n"].sum() + units["rem"].sum()
    assert r["share_hat"] == pytest.approx(0.5 + r["margin_hat"] / (2 * total))
    assert r["share_sd"] == pytest.approx(r["margin_sd"] / (2 * total))
    assert (u["rem_margin"].abs() <= units["rem"] + 1e-9).all()
    # the same count but with the early share already at its final level: nothing left to learn about the mix
    flat = _rows(pri, 0.30, 0.62, 0.42, 0.5)
    rf, uf = us.project(flat, CFG, pri)
    r2, _ = vt.adjust(rf, uf, prof, 0.3, 50_000.0, vt.Params(lam=1.0, kappa=1.0))
    assert abs(r2["vt_shift_pts"]) < 0.3 * abs(r["vt_shift_pts"])


def test_shift_flips_when_the_early_vote_is_the_one_still_out():
    pri = _pri()
    E = pri["V"].to_numpy()
    frames = []                                    # history: late (Election Day) votes counted first, the early / mail votes last (PA 2020)
    for frac, q in ((0.3, 0.0), (0.6, 0.1), (1.0, 0.5)):
        n = E * frac
        e = n * q
        frames.append(pd.DataFrame({"ts": pd.Timestamp("2020-11-04", tz="UTC") + pd.Timedelta(hours=10 * frac), "unit_id": pri["unit_id"], "d": n * 0.5,
                                    "r": n * 0.5, "ed": e * 0.62, "er": e * 0.38}))
    prof = vt.fit_profile({2020: [pd.concat(frames, ignore_index=True)]})
    rows = _rows(pri, 0.30, 0.45, 0.45, 0.0, split=False)                           # tonight: 30 % counted, the feed has no split
    res, units = us.project(rows, CFG, pri)
    assert units["ne"].sum() == 0
    r, _ = vt.adjust(res, units, prof, 0.3, 50_000.0, vt.Params(lam=1.0, kappa=0.0))
    assert r["vt_shift_pts"] > 0.5                                                   # the Democratic-leaning early votes are still to come


def test_order_curve_stands_in_for_a_missing_split_and_a_finished_count_has_no_exposure():
    pri = _pri()
    late_first = [_night(pri)]
    # a history in which early votes were counted first: at 20 % counted the early share of the counted votes is 2 x the final share
    frames = []
    E = pri["V"].to_numpy()
    for frac, q in ((0.2, 1.0), (0.5, 1.0), (1.0, 0.5)):
        n, e = E * frac, E * frac * q
        frames.append(pd.DataFrame({"ts": pd.Timestamp("2020-11-04", tz="UTC") + pd.Timedelta(hours=frac * 10), "unit_id": pri["unit_id"], "d": n * 0.5, "r": n * 0.5,
                                    "ed": e * 0.6, "er": e * 0.4}))
    prof = vt.fit_profile({2020: [pd.concat(frames, ignore_index=True)]})
    assert prof.order and prof.order[1] > 1.5 and prof.order[-1] == pytest.approx(1.0, abs=0.2)
    rows = _rows(pri, 0.2, 0.55, 0.45, 1.0, only_early=True, split=False)
    _, units = us.project(rows, CFG, pri)
    assert units["ne"].sum() == 0
    t = vt.terms(units, prof, 0.3, 1.0, 50_000.0, vt.Params())
    assert t["q"].gt(0.7).all()                                                     # inferred from the state's order, not observed
    full = _rows(pri, 1.0, 0.55, 0.45, 0.5).assign(reporting=10.0)
    rf, uf = us.project(full, {**CFG, "count": {"f_complete": 1.0}}, pri)
    r, _ = vt.adjust(rf, uf, vt.fit_profile({2020: late_first}), 0.3, 50_000.0, vt.Params(lam=1.0, kappa=2.0))
    assert r["vt_exposure"] == pytest.approx(0, abs=1e-6) and r["share_hat"] == pytest.approx(rf["share_hat"], abs=1e-6)


def test_tonights_early_and_late_counts_override_the_historical_gap():
    pri = _pri()
    prof = vt.fit_profile({2020: [_night(pri, se=0.62, sl=0.42)]})                    # history: gap 0.20
    rows = _rows(pri, 0.30, 0.52, 0.50, 0.5)                                         # tonight early and late lean alike: gap ~ 0.02
    res, units = us.project(rows, CFG, pri)
    t = vt.terms(units, prof, 0.3, 1.0, 50_000.0, vt.Params())
    assert 0.02 <= np.average(t["g"], weights=t["R"]) < 0.08
    assert abs(vt.adjust(res, units, prof, 0.3, 50_000.0, vt.Params(lam=1.0))[0]["vt_shift_pts"]) < 0.3


# ------------------------------------------------------------------ analysis/us_votetype.py: the fitting helpers

def _arr(S, err0, sd0=0.01, X=0.02, split=1.0, final=0.5):
    n = len(S)
    return {"minute": np.arange(n, dtype=float), "counted": np.linspace(0.06, 0.88, n), "share0": final + np.asarray(err0, float), "sd0": np.full(n, sd0),
            "S": np.asarray(S, float), "X": np.full(n, X), "Xu": np.full(n, 0.5), "split": np.full(n, split), "final": final, "night": "x", "variant": "v1",
            "win": True, "has_profile": True}


def test_fit_lam_recovers_the_scale_of_the_shift_and_fit_kappa_widens_for_unexplained_errors():
    us_vt = pytest.importorskip("us_votetype")
    rng = np.random.default_rng(3)
    S = rng.normal(0, 0.02, 40)
    arrs = [_arr(S, -0.5 * S)]                                  # the base errs by -0.5 S: adding 0.5 S fixes it
    assert us_vt.fit_lam(arrs, 1) == pytest.approx(0.5, abs=1e-9)
    assert us_vt.fit_lam(arrs, 0) == 0.0                         # no frames of the other group
    calm = [_arr(S, rng.normal(0, 0.002, 40))]
    wild = [_arr(S, rng.normal(0, 0.05, 40))]
    assert us_vt.fit_kappa(calm, [0.0] * 3, 1) == 0.0
    assert us_vt.fit_kappa(wild, [0.0] * 3, 1) > 0.5
    d = us_vt.frames_of_setting(arrs[0], 0.5, 1.0, 0.0, "v1 | vt")
    assert d["err"].abs().max() < 1e-12 and (d["sd"] > 0.01).all()
    assert us_vt.cycle_weights(2024, [2020, 2022]) == {2020: 1.0, 2022: us_vt.W_OTHER}
