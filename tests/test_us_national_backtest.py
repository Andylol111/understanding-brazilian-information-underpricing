"""The replay harness on a synthetic cycle: reporting order, signals, scoring."""
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_national_backtest as B  # noqa: E402

from brunder import us_national as N  # noqa: E402

STATES = "tx ga nc oh pa ca mi wi az fl mn ne co".split()


def _cycle(shift=0.03, seed=1):
    rng = np.random.default_rng(seed)
    races, counties = [], {}
    for i, st in enumerate(STATES):
        n = 30
        V0 = rng.lognormal(9, 1, n)
        s0 = np.clip(0.5 + rng.normal(0, 0.1, n), 0.1, 0.9)
        s = np.clip(s0 + shift + rng.normal(0, 0.02, n), 0.05, 0.95)
        tot = V0 * 0.75
        c = pd.DataFrame({"unit_id": [f"us-{st}-{k:05d}" for k in range(n)], "state": st, "d": tot * s, "r": tot * (1 - s),
                          "total": tot, "s0": s0, "V0": V0})
        counties[f"{st}-sen"] = c
        fin, base = c.d.sum() / c.total.sum(), np.average(s0, weights=V0)
        races.append(dict(race_key=f"{st}-sen", office="sen", state=st, poll_avg=base if i % 3 else np.nan, poll_sd=0.04,
                          baseline_share=base, final_d2=fin, d_wins=fin > .5, d_caucus_win=fin > .5, year=2022))
    H = pd.DataFrame([dict(district=f"{st.upper()}-{k:02d}", state=st, final_d2=0.5 + shift + rng.normal(0, .06),
                           prior_d2=0.5 + rng.normal(0, .06), uncontested=False, winner_party="D") for st in STATES
                      for k in range(1, 9)])
    H["d_wins"] = H.final_d2 > .5
    H.loc[0, ["uncontested", "winner_party", "final_d2", "d_wins"]] = [True, "R", 0.0, False]
    return dict(races=pd.DataFrame(races), counties=counties, house=H, year=2022, house_unknown=H.iloc[:0], holdover=40,
                poll_close=pd.DataFrame({"state": STATES, "close_utc": ["2022-11-09T00:00:00Z"] * len(STATES)}),
                generic=pd.Series({"avg": 0.5}), nat_prev_pres=0.5)


P = replace(N.Params(), n_sim=2000, house_need=52)


def test_counts_arrive_after_close_in_size_order_and_prior_hour_has_none():
    nt = B.Night(_cycle(), 2022, B.Sim(), 3)
    assert all(r.y is None for r in nt.races(0, P))
    early, late = nt.races(1, P), nt.races(6, P)
    n_early = sum(r.y is not None for r in early)
    n_late = sum(r.y is not None for r in late)
    assert 0 < n_early <= n_late
    c = {r.key: r.counted for r in late if r.counted is not None}
    assert max(c.values()) > 0.5
    f = nt._f(nt.timing["tx-sen"], "tx", 6)
    big, small = np.argmax(nt.cyc["counties"]["tx-sen"]["total"]), np.argmin(nt.cyc["counties"]["tx-sen"]["total"])
    assert f.min() >= 0 and f.max() <= 1
    assert nt.timing["tx-sen"]["L"].iloc[big] > nt.timing["tx-sen"]["L"].iloc[small] * 0.5   # big counties are slower on average


def test_uncontested_known_and_not_overridden():
    nt = B.Night(_cycle(), 2022, B.Sim(), 3)
    races = nt.races(4, P)
    u = races[len([r for r in races if r.office != "house"])]
    assert u.extra == {"uncontested": "R"} and u.prior == 0.001 and u.prior_sd == N.UNCONTESTED_SD


def test_democratic_surprise_is_detected_and_scored_better_than_prior_alone():
    cyc = _cycle(shift=0.04)
    g = {"cyc": cyc, "nights": []}
    for rep in range(4):
        nt = B.Night(cyc, 2022, B.Sim(), rep)
        for h in (0,) + B.HOURS:
            g["nights"].append((rep, h, nt.races(h, P, house_all=True)))
    df = B.score_cycle(g, P, 0.06)
    late = df[(df["hours"] == 6) & (df["chamber"] == "house")]
    live = late[late["mode"] == "live"]
    pri = late[late["mode"] == "prior"]
    assert live["rps"].mean() < pri["rps"].mean()
    assert live["abs_err"].mean() < pri["abs_err"].mean()
    senate = df[(df["chamber"] == "senate") & (df["hours"] == 6)]
    assert senate[senate["mode"] == "live"]["mean"].mean() > senate[senate["mode"] == "prior"]["mean"].mean()


def test_scoring_helpers():
    dist = [0.0] * 5 + [0.2, 0.5, 0.3]
    assert B.thresholds(dist, [4, 6, 7, 9]) == pytest.approx([1.0, 0.8, 0.3, 0.0])
    sc = B.score({"dist": dist, "p_control": 0.8, "mean": 6.1, "p10": 5, "p90": 7}, 6, 6, range(4, 8))
    assert sc["brier"] == pytest.approx(0.04) and sc["band"] == 1.0 and sc["abs_err"] == pytest.approx(0.1)
    assert sc["rps"] == pytest.approx(np.mean([(1 - 1) ** 2, (1 - 1) ** 2, (0.8 - 1) ** 2, (0.3 - 0) ** 2]))
