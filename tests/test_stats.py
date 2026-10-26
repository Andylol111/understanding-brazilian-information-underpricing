import numpy as np
import pandas as pd

from brunder import stats


def test_holm_and_bh_match_textbook_values():
    p = pd.Series([0.01, 0.04, 0.03, 0.20], index=list("abcd"))
    assert np.allclose(stats.holm(p).to_numpy(), [0.04, 0.09, 0.09, 0.20])
    assert np.allclose(stats.bh(p).to_numpy(), [0.04, 0.0533333, 0.0533333, 0.20])


def test_cluster_bootstrap_does_not_count_duplicated_rows_as_independent():
    rng = np.random.default_rng(0)
    base = pd.DataFrame({"event_id": np.arange(30), "x": rng.normal(size=30)})
    copies = pd.concat([base] * 20, ignore_index=True)       # 20 identical markets per event
    a = stats.cluster_bootstrap(base, stats.mean_of("x"), B=1000, seed=1)
    b = stats.cluster_bootstrap(copies, stats.mean_of("x"), B=1000, seed=1)
    assert np.isclose(a["hi95"] - a["lo95"], b["hi95"] - b["lo95"], rtol=0.05)
    assert b["clusters"] == 30


def test_one_sided_p_reflects_direction():
    d = pd.DataFrame({"event_id": np.arange(200), "x": np.random.default_rng(2).normal(0.5, 1, 200)})
    r = stats.cluster_bootstrap(d, stats.mean_of("x"), B=500, seed=3)
    assert r["p_greater"] < 0.01 < r["p_less"]
