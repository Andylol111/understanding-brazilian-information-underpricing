import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us
from brunder import us_national as n

T = pd.Timestamp("2026-11-04T05:00:00Z")          # every covered state's polls have closed
P = replace(n.Params(), n_sim=6000, house_need=28)


def _ov():
    def race(key, st, avg, sd=0.04, modeled=False, off="senate"):
        return {"race_key": key, "state": st, "office": off, "special": False, "modeled": modeled,
                "race_id": key if modeled else None,
                "polls": {"avg": avg, "sd": sd, "n_polls": 5}, "estimate": None if modeled else
                {"baseline_share": avg - 0.02, "share": avg}, "pm": None, "kalshi": None}
    sen = [race(f"{s}-sen", s, 0.50 + 0.01 * i, modeled=s in ("ga", "nc")) for i, s in
           enumerate("ga nc mi me oh tx ia ak ks mn nh nm".split())]
    gov = [race("ga-gov", "ga", 0.49, modeled=True, off="governor"), race("wi-gov", "wi", 0.5, off="governor")]
    return {"offices": {"senate": {"holdovers": {"D": 30, "R": 40, "I": 2}, "races": sen,
                                   "control_markets": [{"platform": "polymarket", "event": "ev", "slug": "sl"},
                                                       {"platform": "kalshi", "ticker": "CONTROLS-2026-D"}]},
                        "governor": {"races": gov}, "house": {"control_markets": []}}}


def _house(n_d=60):
    sts = "ga nc mi tx oh pa az fl".split()
    d = {f"{s.upper()}-{i:02d}": {"st": s.upper(), "baseline": {"share": 0.40 + 0.2 * (i % 7) / 6, "sd": 0.052}}
         for s in sts for i in range(1, n_d // len(sts) + 1)}
    return {"districts": d}


def _row(share, sd, counted):
    return {"t": "x", "counted": counted, "share": share, "sd": sd, "share_v3": share, "sd_v3": sd, "P": 0.5}


def _races(sen_rows=None, house_rows=None, **kw):
    return n.build_races(_ov(), _house(), sen_rows or {}, house_rows or {}, {"ga-sen": "v3", "nc-sen": "v3", "ga-gov": "v3"},
                         P, now=T, **kw)


def _run(races):
    night = n.Night(P, 44)
    return night.run(T, races, {})


def test_no_count_is_the_prior():
    doc, row = _run(_races())
    assert row["n_inf"] == 0 and row["c"] == 0.0 and row["h"] == 0.0
    assert row["P_sen"] == row["P_sen_prior"] == row["P_sen_pooled"]
    assert row["P_house"] == row["P_house_prior"]
    assert sum(doc["senate"]["seats"]["dist"]) == pytest.approx(1, abs=1e-3)
    assert sum(doc["joint"]["live"].values()) == pytest.approx(1, abs=1e-3)


def test_uncounted_rows_carry_no_information():
    rows = {"ga-sen": _row(0.80, 0.03, None), "nc-sen": _row(0.80, 0.03, 0.0)}
    assert all(r.y is None for r in _races(rows) if r.key in ("ga-sen", "nc-sen"))
    _, row = _run(_races(rows))
    assert row["n_inf"] == 0


def test_democratic_count_shifts_everything_up_monotonically():
    base = _races()
    pri = {r.key: r.prior for r in base}
    lo = _run(_races({"ga-sen": _row(pri["ga-sen"] + 0.01, 0.02, 0.5), "nc-sen": _row(pri["nc-sen"] + 0.01, 0.02, 0.5)}))
    hi = _run(_races({"ga-sen": _row(pri["ga-sen"] + 0.04, 0.02, 0.5), "nc-sen": _row(pri["nc-sen"] + 0.04, 0.02, 0.5)}))
    assert hi[1]["c"] > lo[1]["c"] > 0
    assert hi[1]["P_sen"] > lo[1]["P_sen"] > hi[1]["P_sen_prior"]
    assert hi[1]["P_house"] > lo[1]["P_house"] >= hi[1]["P_house_prior"]
    assert hi[0]["senate"]["races"]["mi-sen"]["p_d"] > lo[0]["senate"]["races"]["mi-sen"]["p_d"]
    assert hi[1]["h"] == 0                               # h (House-only) learns only from House counts; c carries Senate news to it


def test_state_signal_stays_local_region_borrows_little():
    pri = {r.key: r.prior for r in _races()}
    doc, _ = _run(_races({"nc-sen": _row(pri["nc-sen"] + 0.05, 0.015, 0.8)}))
    st = doc["swing"]["states"]
    assert "nc" in st and set(st) == {"nc"}
    r = doc["senate"]["races"]
    assert r["nc-sen"]["source"] == "own"
    assert r["nc-sen"]["share"] == pytest.approx(pri["nc-sen"] + 0.05, abs=0.004)     # own marginal kept
    assert r["nc-sen"]["sd"] == pytest.approx(0.015, abs=0.003)
    assert r["nc-sen"]["p_d_pooled"] > r["nc-sen"]["p_d_prior"]
    gov = [k for k in r if k == "wi-gov"]
    assert doc["swing"]["common"]["mean"] > 0


def test_complete_count_is_the_result_pooled():
    pri = {r.key: r.prior for r in _races()}
    races = _races({"ga-sen": _row(pri["ga-sen"] - 0.06, 0.001, 0.999)})
    F = n.Factors(races, P)
    m = n.race_marginals(races, F, n.posterior(races, F, P), P)
    assert m.loc["ga-sen", "share"] == pytest.approx(pri["ga-sen"] - 0.06, abs=0.003)
    assert m.loc["ga-sen", "sd"] < 0.005
    assert m.loc["ga-sen", "k"] > 0.99


def test_early_noisy_count_moves_less_than_a_precise_one():
    pri = {r.key: r.prior for r in _races()}
    noisy = _run(_races({"ga-sen": _row(pri["ga-sen"] + 0.05, 0.10, 0.02)}))[1]["c"]
    sharp = _run(_races({"ga-sen": _row(pri["ga-sen"] + 0.05, 0.01, 0.9)}))[1]["c"]
    assert 0 <= noisy < sharp


def test_returns_before_close_are_ignored():
    early = pd.Timestamp("2026-11-03T12:00:00Z")
    rows = {"ga-sen": _row(0.7, 0.01, 0.9)}
    r = n.build_races(_ov(), _house(), rows, {}, {}, P, now=early)
    assert all(x.y is None for x in r)


def test_house_district_signal_and_own_model():
    h = {"house:GA-01": _row(0.40, 0.01, 0.9)}
    races = _races(house_rows=h)
    g = next(r for r in races if r.key == "house:GA-01")
    assert g.y is not None and g.source == "own"
    doc, _ = _run(races)
    assert doc["house"]["n_own_model"] == 1


def test_balance_of_power_and_gaps():
    ov = _ov()
    pm = {"ev/sl": 0.7, "balance-of-power-2026-midterms/2026-balance-of-power-d-senate-r-house-692": 0.01,
          "balance-of-power-2026-midterms/2026-balance-of-power-r-senate-d-house-444": 0.22}
    mk = n.control_markets(ov, pm, {"CONTROLS-2026-D": 0.65})
    assert mk["senate"] == {"polymarket": 0.7, "kalshi": 0.65}
    assert mk["bop"] == {"DR": 0.01, "RD": 0.22}
    doc, row = n.Night(P, 32).run(T, _races(), mk)
    assert set(doc["gaps"]["bop"]) == {"DR", "RD"} and row["pm_sen"] == 0.7


def test_live_files_end_to_end(tmp_path):
    site = tmp_path / "us2026"
    (site / "house_live").mkdir(parents=True)
    (site / "house").mkdir()
    (site / "overview.json").write_text(json.dumps(_ov()))
    (site / "house" / "districts.json").write_text(json.dumps(_house()))
    pri = {r.key: r.prior for r in _races()}
    (site / "timeline_tail.json").write_text(json.dumps(
        {"ga-sen": [_row(pri["ga-sen"] + 0.03, 0.02, 0.4)], "tail": True, "since": None}))
    (site / "house_live" / "timeline_tail.json").write_text(json.dumps({"house:GA-01": [_row(0.45, 0.02, 0.5)]}))
    cfg = n.load_config(Path(us.ROOT_DIR) / "configs/us2026_national/national_v1.toml")
    cfg["model"]["n_sim"] = 4000
    live = n.Live(cfg, tmp_path, snap_root=tmp_path / "snap", extra=False)
    live.version = {}
    doc = live.step(T)
    live.write(doc)
    d = site / n.OUT_DIR
    assert {p.name for p in d.iterdir()} == {"national.json", "timeline.json", "timeline_tail.json", "meta.json",
                                             "status.json"}
    got = json.loads((d / "national.json").read_text())
    assert got["n_informing"] == 2 and got["swing"]["common"]["mean"] is not None
    assert len(got["house"]["districts"]) == len(_house()["districts"])
    live2 = n.Live(cfg, tmp_path, snap_root=tmp_path / "snap", extra=False)        # restart keeps the history
    assert len(live2.timeline) == 1


def test_extra_state_count_signal_and_feed_failure(tmp_path, monkeypatch):
    from brunder import us_national_extra as X
    from brunder import us_national_feeds as feeds
    monkeypatch.setattr(X, "RAW_DIR", tmp_path / "raw")
    base = us.load_county_baseline()
    pri = X.baseline_prior("sd", base)
    assert len(pri) == 66 and pri["s0"].between(0, 1).all()
    shift = 0.04
    n = (pri["V"] * 0.72 * 0.6).to_numpy()
    d = n * np.clip(pri["s0"].to_numpy() + shift, 0, 1)

    def rows(votes_d, votes_r, done):
        mk = lambda party, v: pd.DataFrame({"level": "county", "unit_id": pri["unit_id"], "name": "", "race": "sen",
                                            "candidate": "", "party": party, "votes": v, "early": np.nan,
                                            "reporting": 10 if done else 5, "units": 10, "ballots": np.nan,
                                            "registered": np.nan, "outstanding": np.nan})
        return pd.concat([mk("DEM", votes_d), mk("REP", votes_r)], ignore_index=True)

    calls = []

    def fake_fetch(st, client):
        calls.append(st)
        if len(calls) == 3:
            raise RuntimeError("HTTP 503")
        return {"sen": b"x%d" % len(calls)}

    monkeypatch.setattr(feeds, "fetch", fake_fetch)
    monkeypatch.setattr(feeds, "parse_national", lambda st, blobs: rows(d, n - d, False))
    ex = X.ExtraStates({}, states=("sd",), client=object(), min_interval=0)
    assert ex.signals(pd.Timestamp("2026-11-03T12:00:00Z")) == {}               # before the close: not even fetched
    assert calls == []
    s = ex.signals(pd.Timestamp("2026-11-04T01:30:00Z"))["sd-sen"]
    assert s["counted"] > 0.3 and s["share"] == pytest.approx(float(np.average(pri["s0"], weights=pri["V"])) + shift, abs=0.02)
    ex.signals(pd.Timestamp("2026-11-04T01:31:00Z"))
    s2 = ex.signals(pd.Timestamp("2026-11-04T01:32:00Z"))                      # feed down: last good rows kept
    assert "sd-sen" in s2 and "sd" in ex.errors
    assert len(list((tmp_path / "raw" / "sd").glob("*.gz"))) == 2              # changed bytes only
    monkeypatch.setattr(feeds, "parse_national", lambda st, blobs: rows(np.zeros(len(pri)), n, False))
    ex2 = X.ExtraStates({}, states=("sd",), client=object(), min_interval=0)
    calls.clear()
    assert "sd-sen" not in ex2.signals(pd.Timestamp("2026-11-04T01:30:00Z"))   # no Democratic votes: no signal


def _civic(d_votes, r_votes, pct, key="ga-01"):
    return json.dumps({"region_results": {key: {"percent_reporting": pct, "candidates": [
        {"party": "Republican", "votes": r_votes}, {"party": "Democratic", "votes": d_votes},
        {"party": "Independent", "votes": 5}]}, "ga-02": {"percent_reporting": 0, "candidates": [
            {"party": "Republican", "votes": 0}, {"party": "Democratic", "votes": 0}]}}}).encode()


def test_civic_totals_inform_districts_without_a_model():
    sig = n.civic_signals(_civic(30000, 20000, 40), P)
    assert set(sig) == {"GA-01"} and sig["GA-01"]["share"] == pytest.approx(0.6) and sig["GA-01"]["counted"] == 0.4
    late = n.civic_signals(_civic(30000, 20000, 95), P)["GA-01"]["var"]
    assert late < sig["GA-01"]["var"]                                 # more counted, less noise
    races = _races(civic=sig)
    g = next(r for r in races if r.key == "house:GA-01")
    assert g.source == "civic" and g.y == pytest.approx(0.6 - g.prior)
    own = _races(house_rows={"house:GA-01": _row(0.45, 0.02, 0.5)}, civic=sig)
    assert next(r for r in own if r.key == "house:GA-01").source == "own"    # a district model wins over the totals
    doc, row = _run(races)
    assert row["n_inf"] == 1 and doc["house"]["n_civic"] == 1
    assert n.civic_signals(_civic(30000, 0, 40), P) == {} and n.civic_signals(_civic(10, 10, 40), P) == {}


def test_uncontested_seats_are_known_from_the_civic_candidate_lists():
    blob = json.dumps({"region_results": {
        "ga-01": {"percent_reporting": 0, "candidates": [{"party": "Republican"}, {"party": "Libertarian"}]},
        "ga-02": {"percent_reporting": 0, "candidates": [{"party": "Republican"}, {"party": "Democratic"}]},
        "ga-03": {"percent_reporting": 0, "candidates": [{"party": "Democratic"}]},
        "ga-04": {"percent_reporting": 0, "candidates": []}}}).encode()
    unc = n.civic_uncontested(blob)
    assert unc == {"GA-01": "R", "GA-03": "D"}
    races = n.build_races(_ov(), _house(), {}, {}, {}, P, now=T, uncontested=unc)
    g = {r.key: r for r in races}
    assert g["house:GA-01"].prior == 0.001 and g["house:GA-01"].prior_sd == n.UNCONTESTED_SD
    assert g["house:GA-02"].prior_sd == 0.052
    doc, _ = _run(races)
    assert doc["house"]["districts"]["GA-01"][1] == 0.0 and doc["house"]["districts"]["GA-03"][1] == 1.0


def test_huber_weights_stop_one_wild_race_from_moving_the_nation():
    pri = {r.key: r.prior for r in _races()}
    rows = {"ga-sen": _row(pri["ga-sen"] - 0.45, 0.01, 0.9), "nc-sen": _row(pri["nc-sen"] + 0.005, 0.01, 0.9)}
    plain = _run(_races(rows))[1]["c"]
    P2 = replace(P, huber_k=2.0)
    robust = n.Night(P2, 44).run(T, _races(rows), {})[1]["c"]
    assert abs(robust) < abs(plain) / 3 and abs(robust) < 1.0
    mild = {"ga-sen": _row(pri["ga-sen"] + 0.01, 0.01, 0.9)}
    w = n.robust_weights(_races(mild), n.Factors(_races(mild), P2), P2)
    assert w.min() == 1.0                                  # an ordinary deviation keeps full weight


def test_house_does_not_load_on_the_senate_miss_when_decoupled():
    pri = {r.key: r.prior for r in _races()}
    rows = {"ga-sen": _row(pri["ga-sen"] + 0.04, 0.02, 0.5), "nc-sen": _row(pri["nc-sen"] + 0.04, 0.02, 0.5)}
    coupled = n.Night(P, 44).run(T, _races(rows), {})[1]
    free = n.Night(replace(P, house_c_load=0.0), 44).run(T, _races(rows), {})[1]
    assert coupled["P_house"] > coupled["P_house_prior"]
    assert free["P_house"] - free["P_house_prior"] < (coupled["P_house"] - coupled["P_house_prior"]) / 2   # GA/NC spillover only
    assert free["P_sen"] > free["P_sen_prior"]


def test_house_counts_pool_only_above_the_district_threshold():
    rows = {f"house:GA-0{i}": _row(0.40, 0.02, 0.6) for i in range(1, 5)}
    races = _races(house_rows=rows)
    base = next(r for r in races if r.key == "house:GA-01")
    few = n.Night(replace(P, house_min_pool=5), 44).run(T, races, {})[0]
    many = n.Night(replace(P, house_min_pool=3), 44).run(T, races, {})[0]
    assert few["swing"]["house_env"]["mean"] == 0.0 and few["n_pooled"] == 0 and few["n_informing"] == 4
    assert many["swing"]["house_env"]["mean"] != 0.0 and many["n_pooled"] == 4
    assert few["house"]["districts"]["GA-01"][4] == "o"                       # own marginal still used for itself
    assert few["house"]["districts"]["GA-01"][2] == pytest.approx(base.own_mean, abs=0.005)
