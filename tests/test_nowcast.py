import numpy as np
import pandas as pd

from brunder.nowcast import Params, nowcast


def _prior(shares, uf="sp"):
    return pd.DataFrame({"uf": uf, "cd_mun": [f"{i:05d}" for i in range(len(shares))],
                         "s0": shares, "V0": 10_000.0})


def _state(prior, frac, share):
    return pd.DataFrame({"uf": prior["uf"], "cd_mun": prior["cd_mun"], "f": frac,
                         "n": prior["V0"] * frac, "a": prior["V0"] * frac * share})


def test_complete_count_resolves_to_actual_winner():
    prior = _prior([0.6] * 20)                      # prior says A wins comfortably …
    out = nowcast(_state(prior, 1.0, 0.49), prior)  # … but the full count says A lost
    assert out["p_win"] == 0.0
    assert np.isclose(out["share_hat"], 0.49)


def test_nothing_counted_tied_prior_is_coin_flip():
    prior = _prior([0.4, 0.6] * 10)
    assert np.isclose(nowcast(_state(prior, 0.0, 0.0), prior)["p_win"], 0.5)


def test_uniform_swing_moves_uncounted_projection():
    prior = _prior([0.5] * 40)
    state = _state(prior, 0.0, 0.0)
    state.loc[:9, ["f", "n", "a"]] = [1.0, 10_000.0, 5_300.0]   # first 10 munis swing +3pt
    out = nowcast(state, prior, Params(kappa=0.0))
    assert np.isclose(out["share_hat"], 0.53)
    assert 0.5 < out["p_win"] < 1.0


def test_abroad_units_do_not_set_national_swing():
    prior = pd.concat([_prior([0.5] * 20, "sp"), _prior([0.5] * 2, "zz")], ignore_index=True)
    state = _state(prior, 0.0, 0.0)
    state.loc[state["uf"] == "zz", ["f", "n", "a"]] = [1.0, 10_000.0, 8_000.0]
    assert nowcast(state, prior)["swing_nat"] == 0.0
