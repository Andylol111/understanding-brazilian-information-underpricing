import numpy as np
import pandas as pd

from brunder import statecast

PRIOR = pd.DataFrame({"uf": ["aa", "bb"], "s0": [0.40, 0.60], "V": [1_000_000.0, 1_000_000.0]})


def test_fully_counted_states_return_the_actual_result():
    states = pd.DataFrame({"uf": ["aa", "bb"], "n": [1e6, 1e6], "a": [0.45e6, 0.65e6], "f": [1.0, 1.0]})
    r = statecast.nowcast(states, PRIOR)
    assert np.isclose(r["share"], 0.55) and r["sd"] == 0


def test_early_returns_from_one_state_do_not_drag_the_national_share():
    # only the A-weak state has reported, exactly at its prior: the raw count says 40%,
    # but nothing has moved, so the projection stays at the prior national 50%
    states = pd.DataFrame({"uf": ["aa"], "n": [50_000.0], "a": [20_000.0], "f": [0.05]})
    r = statecast.nowcast(states, PRIOR)
    assert np.isclose(r["share"], 0.50)


def test_a_uniform_swing_in_reporting_states_is_carried_to_unreported_ones():
    states = pd.DataFrame({"uf": ["aa"], "n": [50_000.0], "a": [0.43 * 50_000], "f": [0.05]})
    r = statecast.nowcast(states, PRIOR, statecast.Params(kappa=0.0, kappa_nat=0.0))
    assert np.isclose(r["swing"], 0.03) and np.isclose(r["share"], 0.53)
