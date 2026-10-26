from types import SimpleNamespace

import numpy as np
import pandas as pd

from brunder import pit, studies


def test_asof_never_returns_a_later_observation():
    t = np.array(["2026-01-01T00:00", "2026-01-01T00:10"], dtype="datetime64[ns]")
    v = np.array([0.2, 0.8])
    q = np.array(["2025-12-31T23:59", "2026-01-01T00:09:59", "2026-01-01T00:10"], dtype="datetime64[ns]")
    out = pit.asof(t, v, q)
    assert np.isnan(out[0])
    assert out[1] == 0.2          # one second before the jump still sees the old price
    assert out[2] == 0.8


def test_split_embargoes_events_straddling_the_cutoff():
    c = pit.CUTOFF
    mk = pd.DataFrame({
        "event_id": ["a", "b", "c", "c"],
        "scheduled": [c - pd.Timedelta("10D"), c + pd.Timedelta("1D"), c - pd.Timedelta("2D"), c + pd.Timedelta("2D")],
        "closed_time": [c - pd.Timedelta("9D"), c + pd.Timedelta("2D"), c - pd.Timedelta("1D"), c + pd.Timedelta("3D")],
    })
    s = pit.split_events(mk)
    assert s["a"] == "discovery" and s["b"] == "holdout" and s["c"] == "embargo"


def test_discovery_requires_resolution_before_cutoff():
    c = pit.CUTOFF
    mk = pd.DataFrame({"event_id": ["a"], "scheduled": [c - pd.Timedelta("3D")], "closed_time": [c + pd.Timedelta("1D")]})
    assert pit.split_events(mk)["a"] == "embargo"


def test_taker_cost_includes_fee_peaking_at_half():
    assert np.isclose(pit.taker_cost(0.5, 0.07, 0.01), 0.5 + 0.01 + 0.07 * 0.25)
    assert np.isclose(pit.taker_cost(0.9, 0.0, 0.01), 0.91)


def _market(scheduled, closed):
    return SimpleNamespace(condition_id="m", event_id="e", category="x", yes_token="y", outcome=1, fee_rate=0.0,
                           scheduled=scheduled, closed_time=closed)


def test_shock_signal_uses_only_the_past_and_enters_after_the_delay():
    E = pd.Timestamp("2026-06-10 12:00", tz="UTC")
    jump = E - pd.Timedelta("2h")
    times = pd.date_range(E - pd.Timedelta("8D"), E + pd.Timedelta("1D"), freq="5min")
    price = np.select([times < jump, times < jump + pd.Timedelta("35min")], [0.30, 0.55], 0.60)
    px = pd.DataFrame({"time": times, "price": price})
    shocks, _, _ = studies._market_obs(_market(E, E + pd.Timedelta("20h")), px)
    s10 = next(s for s in shocks if s["threshold"] == 0.10)
    assert s10["t"] - pit.OBS_LAG >= jump                # the move was visible OBS_LAG before the signal
    assert s10["t"] == jump + pd.Timedelta("30min")      # first 30-min grid point satisfying that
    assert s10["p"] == 0.55 and s10["p_entry"] == 0.60   # entry at the later price, not the signal's


def test_no_observation_or_entry_after_the_market_closed():
    E = pd.Timestamp("2026-06-10 12:00", tz="UTC")
    close = E - pd.Timedelta("3h")
    times = pd.date_range(E - pd.Timedelta("8D"), E + pd.Timedelta("1D"), freq="5min")
    price = np.where(times < close, 0.5, 1.0)
    px = pd.DataFrame({"time": times, "price": price})
    shocks, horizons, _ = studies._market_obs(_market(E, close), px)
    assert all(s["t"] + pit.EXEC_DELAY <= close for s in shocks)
    assert {h["horizon"] for h in horizons} == {"7d", "1d", "6h"}       # E−1h is after the close
