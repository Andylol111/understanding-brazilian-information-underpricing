"""Count signals from the states without a registered model, for the national estimate only (brunder.us_national).

SD, VA, ID, MD and MA have open official feeds (brunder.us_national_feeds; the recorder does not record them). Once a
state's polls are about to close this fetches the state's feed once per call (one request per key; the national process
calls it every 60 s), parses county rows, keeps the raw bytes under <data>/raw/us_national/<st>/ (only when they
changed) and projects the final two-party Democratic share with the same count-only nowcast as the v1 models: the
2024 president county baseline (data_public/us2026_county_baseline.csv) as prior, no poll prior, turnout 0.72 x 2024,
learned from complete counties. No per-race model is registered or published; the result is a count signal.
"""
from __future__ import annotations

import gzip
import hashlib
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import us, us_national_feeds as feeds

RAW_DIR = us.RAW / "us_national"
EARLY = pd.Timedelta("10min")            # start fetching this long before a state's first close


def baseline_prior(st: str, base: pd.DataFrame | None = None) -> pd.DataFrame:
    """unit_id, name, s0 (2024 president two-party Dem share), V (two-party votes) of a state's counties."""
    base = us.load_county_baseline() if base is None else base
    b = base[(base["state"] == st) & ((base["pres24_dem"] + base["pres24_rep"]) > 0)]
    return pd.DataFrame({"unit_id": b["unit_id"].to_numpy(), "name": b["name"].to_numpy(),
                         "s0": (b["pres24_dem"] / (b["pres24_dem"] + b["pres24_rep"])).to_numpy(),
                         "V": (b["pres24_dem"] + b["pres24_rep"]).to_numpy()})


def count_cfg(st: str, race: str) -> dict:
    return {"election": {"state": st, "race": race, "a": {"party": "DEM"}, "b": {"party": "REP"}},
            "model": {"tau": 0.02, "rho": 0.6, "sigma_mun0": 0.08, "sigma_uf0": 0.03},
            "count": {"turnout_ratio": us.EST_TURNOUT, "f_max": 0.95, "f_complete": 0.98, "learn_turnout": True}}


def count_signal(rows: pd.DataFrame, st: str, race: str, pri: pd.DataFrame) -> dict | None:
    """{'share', 'sd', 'counted'} of one race from parsed rows, or None: no Democratic votes in the feed (e.g. an
    independent is the Democratic-side candidate, ID Senate) or nothing counted."""
    u = rows[(rows["level"] != "state") & (rows["race"] == race)]
    votes = u.groupby("party")["votes"].sum()
    if votes.get("DEM", 0) <= 0 or votes.get("REP", 0) <= 0:
        return None
    res, _ = us.project(rows, count_cfg(st, race), pri)
    if not np.isfinite(res["share_hat"]) or not res["counted_frac"] > 0:
        return None
    return {"share": float(res["share_hat"]), "sd": float(res["share_sd"]), "counted": float(res["counted_frac"])}


class ExtraStates:
    def __init__(self, cfg: dict, arc=None, states: tuple[str, ...] | None = None, client: httpx.Client | None = None,
                 min_interval: float = 50.0):
        self.states = tuple(states or feeds.STATES_NATIONAL)
        self.client = client or httpx.Client(timeout=feeds.TIMEOUT, follow_redirects=True)
        self.min_interval = min_interval
        self.base = us.load_county_baseline()
        self.pri = {st: baseline_prior(st, self.base) for st in self.states}
        self.rows: dict[str, pd.DataFrame] = {}
        self._at: dict[str, float] = {}
        self._sha: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def _save(self, st: str, blobs: dict, now: pd.Timestamp) -> None:
        sha = hashlib.sha256(b"".join(blobs[k] or b"" for k in sorted(blobs))).hexdigest()
        if self._sha.get(st) == sha:
            return
        self._sha[st] = sha
        d = RAW_DIR / st
        d.mkdir(parents=True, exist_ok=True)
        for k, b in blobs.items():
            if b:
                (d / f"{now:%Y%m%dT%H%M%S}_{k}.gz").write_bytes(gzip.compress(b))

    def refresh(self, now: pd.Timestamp) -> None:
        for st in self.states:
            if now < pd.Timestamp(feeds.POLL_CLOSE_NATIONAL[st]) - EARLY:
                continue
            if time.monotonic() - self._at.get(st, -1e9) < self.min_interval:
                continue
            self._at[st] = time.monotonic()
            try:
                blobs = feeds.fetch(st, self.client)
                self._save(st, blobs, now)
                self.rows[st] = feeds.parse_national(st, blobs)
                self.errors.pop(st, None)
            except Exception as e:                               # a feed that is down keeps its last good rows
                self.errors[st] = f"{type(e).__name__}: {e}"

    def signals(self, now: pd.Timestamp) -> dict[str, dict]:
        self.refresh(now)
        out = {}
        for st, rows in self.rows.items():
            for race in feeds.RACES_NATIONAL[st]:
                try:
                    s = count_signal(rows, st, race, self.pri[st])
                except Exception as e:
                    self.errors[f"{st}-{race}"] = f"{type(e).__name__}: {e}"
                    continue
                if s:
                    out[f"{st}-{race}"] = s
        return out
