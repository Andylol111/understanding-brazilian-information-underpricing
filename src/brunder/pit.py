"""Point-in-time discipline: nothing a signal uses may be timestamped after the signal.

- `asof` returns the last observation at or before each query time (never a later one).
  Verified against trades: a prices-history point at t matches the last trade ≤ t best
  (docs/research_design.md, "price semantics"), so a point at t is information available at t.
- Entries execute EXEC_DELAY after the signal, at the price then — the signal never trades at the
  price that defined it.
- Train/test split is by event and by time, with an embargo: an event is in DISCOVERY only if
  every market in it had closed before CUTOFF, in HOLDOUT only if every market's scheduled time is
  on/after CUTOFF; events straddling the cutoff are dropped from both.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CUTOFF = pd.Timestamp("2026-04-01", tz="UTC")
EXEC_DELAY = pd.Timedelta("5min")


def asof(times: np.ndarray, values: np.ndarray, query: np.ndarray) -> np.ndarray:
    """values at the last times[i] ≤ query (NaN if none). times must be sorted ascending."""
    i = np.searchsorted(times, query, side="right") - 1
    out = np.where(i >= 0, values[np.clip(i, 0, None)], np.nan)
    return out.astype(float)


def split_events(markets: pd.DataFrame, cutoff: pd.Timestamp = CUTOFF) -> pd.Series:
    """event_id → 'discovery' | 'holdout' | 'embargo'."""
    g = markets.groupby("event_id")
    last_close = g["closed_time"].max()
    first_sched = g["scheduled"].min()
    out = pd.Series("embargo", index=last_close.index)
    out[last_close < cutoff] = "discovery"
    out[first_sched >= cutoff] = "holdout"
    return out


def taker_cost(q: np.ndarray, fee_rate: np.ndarray, half_spread: float) -> np.ndarray:
    """All-in cost per share of buying a side quoted at q: q + half-spread + Polymarket taker fee
    fee_rate·q·(1−q) (per-market rate from its feeSchedule; 0 where fees are disabled)."""
    q = np.asarray(q, float)
    return q + half_spread + np.asarray(fee_rate, float) * q * (1 - q)
