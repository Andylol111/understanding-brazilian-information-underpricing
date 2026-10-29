"""analysis/us_poll_error.py: poll-weighting schemes, pollster statistics without look-ahead, error models, decision."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def m():
    sys.path.insert(0, str(ROOT / "analysis"))
    spec = importlib.util.spec_from_file_location("us_poll_error", ROOT / "analysis" / "us_poll_error.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["us_poll_error"] = mod                 # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _polls(rows: list[dict]) -> pd.DataFrame:
    """Poll rows in load_538's layout from (cycle, race, pollster, tte, s, act[, n, partisan]) dicts."""
    out = []
    for i, r in enumerate(rows):
        s, act, n = r["s"], r["act"], r.get("n", 600)
        out.append({"cycle": r["cycle"], "race": r["race"], "race_id": r["race"], "type": r.get("type", "Sen-G"),
                    "state": r.get("state", "ga"), "pollster": r["pollster"], "pid": "p" + r["pollster"],
                    "partisan": r.get("partisan"), "tte": r["tte"], "n": float(n), "dem": 100 * s * 0.96,
                    "opp": 100 * (1 - s) * 0.96, "s": s, "act": act, "err": s - act,
                    "cand1": r.get("cand1", "a b"), "cand2": r.get("cand2", "c d")})
    return pd.DataFrame(out)


def test_v3_scheme_reproduces_the_registered_average(m):
    rows = _polls([{"cycle": 2022, "race": "r", "pollster": p, "tte": t, "s": s, "act": 0.5, "n": n}
                   for p, t, s, n in [("A", 2, 0.52, 800), ("A", 9, 0.50, 700), ("B", 5, 0.47, 1500),
                                      ("C", 30, 0.55, 400), ("D", 60, 0.40, 900), ("E", 0, 0.60, 900)]])
    a = m.average(rows, m.SCHEMES["v3"], None)
    eve = pd.Timestamp("2022-11-07")
    legacy = rows.assign(end=[(eve + pd.Timedelta(days=1) - pd.Timedelta(days=int(t))).strftime("%Y-%m-%d")
                              for t in rows["tte"]])
    ref = us.poll_average(legacy[["pollster", "end", "n", "dem", "opp", "s"]], eve.strftime("%Y-%m-%d"))
    assert a["n_polls"] == ref["n_polls"] == 4          # 60 days out and the Election Day poll are outside
    assert a["avg"] == pytest.approx(ref["avg"], abs=5e-5)


def test_latest_per_pollster_keeps_one_poll_each(m):
    rows = _polls([{"cycle": 2022, "race": "r", "pollster": "A", "tte": t, "s": s, "act": 0.5}
                   for t, s in [(2, 0.60), (8, 0.40), (15, 0.40)]]
                  + [{"cycle": 2022, "race": "r", "pollster": "B", "tte": 4, "s": 0.50, "act": 0.5}])
    a = m.average(rows, m.Scheme(latest=True, half_life=None, n_power=0.0, cap=None), None)
    assert a["n_polls"] == 2 and a["avg"] == pytest.approx(0.55)


def test_pollster_stats_use_only_earlier_cycles_and_correct_a_house_effect(m):
    hist = []
    for c in (2014, 2016, 2018):
        for k in range(4):
            race = f"{c}-{k}"
            hist += [{"cycle": c, "race": race, "pollster": "Lean", "tte": 5, "s": 0.53, "act": 0.50},
                     {"cycle": c, "race": race, "pollster": "X", "tte": 6, "s": 0.50, "act": 0.50},
                     {"cycle": c, "race": race, "pollster": "Y", "tte": 7, "s": 0.50, "act": 0.50}]
    polls = _polls(hist)
    st = m.pollster_stats(polls, 2020)
    assert st.house["pLean"] > 0.01 and st.house["pX"] < 0
    # polls of the predicted cycle (or later) never enter its statistics
    later = pd.concat([polls, _polls([{"cycle": 2020, "race": "z", "pollster": "Lean", "tte": 3, "s": 0.40,
                                       "act": 0.50}] * 5)])
    assert m.pollster_stats(later, 2020).house["pLean"] == pytest.approx(st.house["pLean"])
    now = _polls([{"cycle": 2020, "race": "n", "pollster": p, "tte": 3, "s": s, "act": 0.5}
                  for p, s in [("Lean", 0.53), ("X", 0.50)]])          # the D-leaning pollster is half of this race
    raw = m.average(now, m.SCHEMES["v3"], st)["avg"]
    fixed = m.average(now, m.SCHEMES["house effect"], st)["avg"]
    assert abs(fixed - 0.5) < abs(raw - 0.5)


def test_partisan_shift_and_discount_only_touch_sponsored_polls(m):
    st = m.PollsterStats(house=pd.Series(dtype=float), lean=pd.Series(dtype=float), excess=pd.Series(dtype=float),
                         excess_all=0.0004, partisan={"DEM": 0.02, "REP": -0.01})
    rows = _polls([{"cycle": 2022, "race": "r", "pollster": "A", "tte": 3, "s": 0.54, "act": 0.5, "partisan": "DEM"},
                   {"cycle": 2022, "race": "r", "pollster": "B", "tte": 3, "s": 0.50, "act": 0.5}])
    plain = m.Scheme(half_life=None, n_power=0.0, cap=None)
    assert m.average(rows, plain, st)["avg"] == pytest.approx(0.52)
    assert m.average(rows, m.Scheme(half_life=None, n_power=0.0, cap=None, partisan_shift=True), st)["avg"] \
        == pytest.approx(0.51)
    assert m.average(rows, m.Scheme(half_life=None, n_power=0.0, cap=None, partisan_w=0.0), st)["avg"] \
        == pytest.approx(0.50)


def test_incumbent_found_by_name_across_cycles(m):
    polls = _polls([{"cycle": 2012, "race": "2012_Sen-G_OH", "pollster": "A", "tte": 3, "s": 0.5, "act": 0.53,
                     "state": "oh", "cand1": "sherrod brown", "cand2": "josh mandel"},
                    {"cycle": 2018, "race": "2018_Sen-G_OH", "pollster": "A", "tte": 3, "s": 0.5, "act": 0.53,
                     "state": "oh", "cand1": "sherrod brown", "cand2": "jim renacci"},
                    {"cycle": 2018, "race": "2018_Gov-G_OH", "pollster": "A", "tte": 3, "s": 0.5, "act": 0.47,
                     "state": "oh", "type": "Gov-G", "cand1": "richard cordray", "cand2": "mike dewine"}])
    inc = m.incumbents(polls)
    assert inc["2018_Sen-G_OH"] == "D" and inc["2012_Sen-G_OH"] == "" and inc["2018_Gov-G_OH"] == ""


def test_hetero_fit_recovers_a_variance_slope_and_sd_models_fit_on_train_only(m):
    rng = np.random.default_rng(1)
    n = 4000
    d = pd.DataFrame({"n_polls": rng.integers(1, 30, n), "pres_lean": rng.uniform(-0.3, 0.3, n),
                      "midterm": rng.integers(0, 2, n), "inc": np.where(rng.random(n) < 0.5, "D", ""),
                      "gov": rng.integers(0, 2, n), "ba": 0.35, "inparty": "D"})
    sd = 0.04 * np.exp(-0.25 * np.log(d["n_polls"]))
    d["err"] = rng.normal(0, sd)
    b = m.fit_hetero(d)
    assert b[1] == pytest.approx(-0.5, abs=0.08)            # log variance slope = 2 × log sd slope
    assert np.all(np.abs(b[2:]) < 0.1)
    f = m.sd_model(d.iloc[:2000], "bins")
    g = m.sd_model(pd.concat([d.iloc[:2000], d.iloc[2000:].assign(err=1.0)]), "bins")
    assert f(d.iloc[:5]).tolist() != g(d.iloc[:5]).tolist()


def test_score_coverage_and_brier(m):
    d = pd.DataFrame({"avg": [0.55, 0.45, 0.52, 0.40], "act": [0.54, 0.47, 0.49, 0.30]})
    d["err"] = d["avg"] - d["act"]
    s = m.score(d, np.full(4, 0.02))
    assert s["cov90"] == pytest.approx(0.75)                # only the 10-point miss is outside ±3.3
    assert s["mae"] == pytest.approx((0.01 + 0.02 + 0.03 + 0.10) / 4)
    assert 0 < s["brier"] < 0.5


def test_common_miss_forecast_uses_only_earlier_midterms(m):
    cm = pd.DataFrame({"cycle": [2010, 2012, 2014, 2018, 2022], "midterm": [True, False, True, True, True],
                       "in_party_err": [0.01, 0.05, 0.03, -0.01, 0.40]})
    f = m.common_miss_forecast(cm, 2022)
    assert f["cycles"] == 3 and f["mean"] == pytest.approx(0.01)
    assert f["pred_90"][0] < f["mean_90"][0] < f["mean"] < f["mean_90"][1] < f["pred_90"][1]


def test_decomposition_recovers_a_common_cycle_component(m):
    rng = np.random.default_rng(3)
    states = list(m.REGION)[:30]
    rows = []
    for c in range(2002, 2024, 2):
        cyc = rng.normal(0, 0.03)
        for s in states:
            for t in ("Sen-G", "Gov-G"):
                rows.append({"cycle": c, "state": s, "region": m.REGION[s], "err": cyc + rng.normal(0, 0.01)})
    dec = m.decompose(pd.DataFrame(rows))
    assert dec["share"]["cycle"] > 0.7 and dec["sd_pts"]["race"] == pytest.approx(1.0, abs=0.2)


def test_decide_requires_every_check(m):
    rng = np.random.default_rng(0)
    rows = []
    for c in m.EARLY_TESTS + m.TEST_CYCLES:
        for i in range(60):
            act = rng.uniform(0.35, 0.65)
            e = rng.normal(0, 0.03)
            for sc, err, k in [("v3", e, "bins"), ("v3", e, "bins (registered v3)"), ("cand", e * 0.5, "bins")]:
                rows.append({"race": f"{c}-{i}", "test": c, "scheme": sc, "sd_kind": k, "act": act, "err": err,
                             "avg": act + err, "sd": 0.03 if sc == "v3" else 0.015})
    B = pd.DataFrame(rows)
    d = m.decide(B, "cand", "bins")
    assert d["v5"] and all(d["checks"].values())
    worse = B.assign(err=np.where(B["scheme"] == "cand", B["err"] * 4, B["err"]))
    worse["avg"] = worse["act"] + worse["err"]
    assert not m.decide(worse, "cand", "bins")["v5"]


def test_rows_2026_age_counts_back_from_the_poll_asof_day(m):
    race = {"polls": [{"pollster": "Emerson College", "end": us.POLLS_ASOF, "n": 800, "dem": 48, "opp": 46,
                       "dem2": 48 / 94, "partisan": None, "excluded": None},
                      {"pollster": "X", "end": "2026-10-01", "n": 500, "dem": 40, "opp": 50, "dem2": 0.4,
                       "partisan": "REP", "excluded": "does not name the Democratic-side candidate"}]}
    r = m.rows_2026(race, {"emerson": "r88"})
    assert len(r) == 1 and r["tte"].iloc[0] == 1 and r["pid"].iloc[0] == "r88"
