"""Poll-start sensitivity (analysis/poll_priors.py, Hungary's district swing prior)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

import poll_priors as pp  # noqa: E402

from brunder import hungary as hu  # noqa: E402
from brunder import nowcast as nc  # noqa: E402


def test_two_way_ignores_undecided_and_other_columns():
    d = pd.DataFrame({"A": ["40", "<1", "30"], "B": ["40", "10", "10"], "U": [20, 80, 60]})
    s = pp.two_way(d, ["A"], ["B"])
    assert s.round(4).tolist() == [0.5, 0.0476, 0.75]       # '<1' counts as half the bound
    assert pp.two_way(d, ["A", "U"], ["B"]).iloc[1] == pytest.approx(80.5 / 90.5)


def test_average_uses_only_the_last_14_days_before_election_day(tmp_path, monkeypatch):
    f = tmp_path / "x.csv"
    f.write_text("# header\npollster,fieldwork_start,fieldwork_end,sample_size,A,B\n"
                 "p1,2025-05-01,2025-05-03,1000,60,40\n"        # too early
                 "p2,2025-05-10,2025-05-12,1000,50,50\n"
                 "p3,2025-05-15,2025-05-17,1000,55,45\n"
                 "p4,2025-05-17,2025-05-18,1000,90,10\n")       # ends on election day: not published before the vote
    monkeypatch.setattr(pp, "DATA", tmp_path)
    a = pp.average("x.csv", "2025-05-18", ["A"], ["B"])
    assert a["n_polls"] == 2 and a["two_way"] == pytest.approx(0.525) and a["last_fieldwork_end"] == "2025-05-17"


def test_prior_estimate_sd_is_rms_of_earlier_elections_only(tmp_path, monkeypatch):
    polls = "pollster,fieldwork_start,fieldwork_end,sample_size,A,B\n"
    (tmp_path / "t1.csv").write_text(polls + "p,2020-05-01,2020-05-05,1,50,50\n")
    (tmp_path / "t2.csv").write_text(polls + "p,2022-05-01,2022-05-05,1,60,40\n")
    (tmp_path / "t3.csv").write_text(polls + "p,2024-05-01,2024-05-05,1,55,45\n")
    (tmp_path / "res.csv").write_text("year,A,B\n2020,54,46\n2022,58,42\n2024,99,1\n")
    monkeypatch.setattr(pp, "DATA", tmp_path)
    monkeypatch.setitem(pp.HIST, "zz", ("res.csv", [("2020", "2020-05-10", "t1.csv", {"year": 2020}, ["A"], ["B"]),
                                                    ("2022", "2022-05-10", "t2.csv", {"year": 2022}, ["A"], ["B"]),
                                                    ("2024", "2024-05-10", "t3.csv", {"year": 2024}, ["A"], ["B"])]))
    est = pp.prior_estimate("zz", "2024-05-10", "t3.csv", ["A"], ["B"])       # the 2024 result (0.99) must not enter its own error
    assert est["two_way"] == pytest.approx(0.55) and est["n_errors"] == 2
    assert est["sd"] == pytest.approx(np.sqrt((0.04**2 + (0.58 - 0.60) ** 2) / 2))


def _hu_inputs(rng, n=60):
    uf = np.repeat(["01", "02", "03", "04", "05", "06"], 10)
    prior = pd.DataFrame({"uf": uf, "cd_mun": [f"hu-{u}-{i:02d}" for i, u in enumerate(uf)],
                          "s0": rng.uniform(0.3, 0.6, n), "V0": rng.uniform(3e4, 6e4, n)})
    f = np.where(rng.random(n) < 0.6, rng.uniform(0.1, 0.9, n), 0.0)
    counted = f * prior["V0"] * 1.1
    share = prior["s0"] + 0.05 + rng.normal(0, 0.03, n)
    return prior, pd.DataFrame({"uf": uf, "cd_mun": prior["cd_mun"], "n": counted, "a": counted * share, "f": f})


@pytest.mark.parametrize("scaled", [False, True])
def test_hungary_district_swing_prior_matches_nowcast(scaled):
    rng = np.random.default_rng(1)
    prior, state = _hu_inputs(rng)
    p = nc.Params(tau=0.01, swing_prior_mean=0.08, swing_prior_sd=0.03, count_scaled_terms=scaled)
    ref = nc.nowcast(state, prior, p)
    m, s = hu.district_draws(state, prior, p, rng.standard_normal(200_000), rng)
    nf = (m["n"] + m["R"]).to_numpy()
    agg = (s * nf).sum(1) / nf.sum()
    assert abs(agg.mean() - ref["share_hat"]) < 3e-4
    assert abs(agg.std() / ref["share_sd"] - 1) < 0.03


def test_hungary_district_swing_prior_before_any_count_is_the_poll():
    rng = np.random.default_rng(2)
    prior, state = _hu_inputs(rng)
    state = state.assign(n=0.0, a=0.0, f=0.0)
    p = nc.Params(tau=0.01, swing_prior_mean=0.08, swing_prior_sd=0.03, count_scaled_terms=True)
    m, s = hu.district_draws(state, prior, p, rng.standard_normal(200_000), rng)
    nf = m["R"].to_numpy()
    agg = (s * nf).sum(1) / nf.sum()
    base = float((prior["s0"] * prior["V0"]).sum() / prior["V0"].sum())
    assert agg.mean() == pytest.approx(base + 0.08, abs=2e-3)
    assert agg.std() == pytest.approx(0.03, rel=0.1)           # tau is scaled to zero, only the poll error is left
