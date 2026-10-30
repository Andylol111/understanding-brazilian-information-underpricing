"""US midterms 2026-11-03: the national picture on election night, its own process.

    python -m brunder.us_national live configs/us2026_national/national_v1.toml --out site/data [--snap DIR]

Question answered: given the counties/districts counted so far, how far has tonight moved from the pre-count priors
(poll averages for Senate/governor, 2024 president + generic ballot for the House), and what does that mean for
control of the Senate and the House? Nothing here is a registered per-race model.

Model (all in two-party Democratic share; docs/us_national.md):

  final share of race r      S_r = P_r + e_r          P_r = prior (poll average / overview estimate / House baseline)
  deviation from the prior   e_r = c + h·1[House] + g_region(r) + λ_r·s_state(r) + ε_r
                             c   national common miss      (sd 1.34 pts; docs/us_poll_error.md, REML 2002-2022)
                             h   House-only environment   (generic ballot error beyond c)
                             g   region x cycle (0.41), s  state (persistent 0.79 + state x cycle 1.87 -> 2.03)
                             ε   race specific, sd² = prior sd² − the shared parts (floored)
  count signal of race r     y_r = ŝ_r − P_r  with variance v_r = sd_r²   (ŝ_r, sd_r: the COUNT-ONLY projection of the
                             race's final share, no poll prior: the v1 timeline row `share`/`sd` of `brunder us live`
                             and of the House process, or the same nowcast on the extra feeds). Only races with counted
                             votes inform; a race with no count has no signal, so the pre-count state is the prior.
  posterior of (c, h, g, s)  Gaussian conjugate update, ε_r of counted races marginalised.

Propagation: draw the shared factors from their posterior, then each race given its factors; races with their own live
model keep THEIR marginal (own model mean/sd, correlated with the rest through the shared draw); the others use the
pooled posterior. `*_pooled` outputs let modelled races borrow strength too. Senate: D caucus = holdovers + races won,
control needs 51 (the vice president is Republican); House: 218 of 435. Both chambers use the same draw, so the
balance-of-power probabilities are joint. Output files: site/data/us2026/national_live/ (schema: docs/us_national.md).
"""

from __future__ import annotations

import argparse
import json
import re
import time
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import us

OUT_DIR = "national_live"
SIGNAL_VERSION = "v1"
CHUNK = 4000                      # simulation rows per block (bounds memory: the process has 512 MB)

# Census regions (the 'region' of analysis/us_poll_error.py's variance decomposition)
REGION = {s: r for r, ss in {
    "northeast": "ct me ma nh ri vt nj ny pa", "midwest": "il in mi oh wi ia ks mn mo ne nd sd",
    "south": "de dc fl ga md nc sc va wv al ky ms tn ar la ok tx", "west": "az co id mt nv nm ut wy ak ca hi or wa"}.items()
    for s in ss.split()}

# Official closing times, UTC on 2026-11-03/04 (EST: daylight saving ended 2026-11-01): (first, last). Standard
# hours, split-time-zone states by their first/last area. Used for the 'closed' count and to ignore returns that
# are stamped before a state's polls could close. Covered states take us.POLL_CLOSE / POLL_CLOSE_LAST instead.
_Z = {"23": "2026-11-03T23:00:00Z", "00": "2026-11-04T00:00:00Z", "0030": "2026-11-04T00:30:00Z",
      "01": "2026-11-04T01:00:00Z", "0130": "2026-11-04T01:30:00Z", "02": "2026-11-04T02:00:00Z",
      "03": "2026-11-04T03:00:00Z", "04": "2026-11-04T04:00:00Z", "05": "2026-11-04T05:00:00Z",
      "06": "2026-11-04T06:00:00Z", "07": "2026-11-04T07:00:00Z"}
_CLOSE = {"in": ("23", "00"), "ky": ("23", "00"), "ga": ("00", "00"), "sc": ("00", "00"), "vt": ("00", "00"),
          "va": ("00", "00"), "fl": ("00", "01"), "nc": ("0030", "0030"), "oh": ("0030", "0030"),
          "wv": ("0030", "0030"), "al": ("01", "01"), "ct": ("01", "01"), "de": ("01", "01"), "dc": ("01", "01"),
          "il": ("01", "01"), "ks": ("01", "02"), "me": ("01", "01"), "md": ("01", "01"), "ma": ("01", "01"),
          "mi": ("01", "02"), "ms": ("01", "01"), "mo": ("01", "01"), "nh": ("00", "01"), "nj": ("01", "01"),
          "ok": ("01", "01"), "pa": ("01", "01"), "ri": ("01", "01"), "tn": ("00", "01"), "tx": ("01", "02"),
          "ar": ("0130", "0130"), "az": ("02", "02"), "co": ("02", "02"), "la": ("02", "02"), "mn": ("02", "02"),
          "ne": ("02", "03"), "nm": ("02", "02"), "ny": ("02", "02"), "nd": ("02", "03"), "sd": ("02", "03"),
          "wi": ("02", "02"), "wy": ("02", "02"), "ia": ("03", "03"), "mt": ("03", "03"), "nv": ("03", "03"),
          "ut": ("03", "03"), "id": ("02", "03"), "ca": ("04", "04"), "or": ("04", "04"), "wa": ("04", "04"),
          "hi": ("05", "05"), "ak": ("05", "06")}
STATE_CLOSE = {s: (_Z[a], _Z[b]) for s, (a, b) in _CLOSE.items()}
for _s, _v in us.POLL_CLOSE.items():
    STATE_CLOSE[_s] = (_v, us.POLL_CLOSE_LAST.get(_s, _v))
FIRST_CLOSE = pd.Timestamp(min(v[0] for v in STATE_CLOSE.values()))


@dataclass(frozen=True)
class Params:
    """[model] of the config. sd_* in share units (0.0134 = 1.34 points of two-party share)."""
    sd_common: float = 0.0134          # c: cycle-wide common error (docs/us_poll_error.md)
    sd_region: float = 0.0041          # region x cycle
    sd_state: float = 0.0203           # state: persistent 0.79 + state x cycle 1.87
    sd_house_env: float = 0.0375       # h: generic-ballot environment beyond c (sqrt(0.0398² − 0.0134²))
    house_c_load: float = 1.0          # loading of a House district on the common factor c (Senate/governor polls' miss);
                                       #   the rest of its national shift is h
    state_load_house: float = 0.74     # a House district loads on its state's factor by this (1.5 pts / 2.03)
    house_sd: float = 0.0              # prior sd of a House district's final share; 0 = the districts.json baseline sd
    sd_unpolled: float = 0.06          # prior sd of a Senate/governor race without polls (brunder.us.EST_SD_NOPOLL)
    sd_floor: float = 0.01             # floor of a race's own (ε) sd
    meas_inflate: float = 1.0          # factor on the variance of count signals
    meas_floor: float = 0.0            # floor on the sd of count signals
    min_counted: float = 0.0           # a race informs only above this counted fraction
    huber_k: float = 0.0               # robust weights: a race whose count deviates from the fitted factors by more than
                                       #   k standard deviations (race noise + count noise) is down-weighted to k/|z| (0 = off)
    house_min_pool: int = 150          # House counts move the national factors only once this many districts inform
                                       #   (a few dozen districts in a handful of states are not a sample of the nation)
    civic_a: float = 1.3e-4            # House district count signal from the civicAPI statewide totals: partial-count
    civic_tau: float = 0.015           #   variance civic_a·(1−f)/f + (civic_tau·(1−f))²  (f = percent reporting)
    civic_min_votes: float = 500.0
    n_sim: int = 20000
    seed: int = 2026
    senate_need: int = 51
    house_need: int = 218


def load_params(cfg: dict) -> Params:
    m = cfg.get("model", {})
    return Params(**{f.name: m[f.name] for f in fields(Params) if f.name in m})


@dataclass
class Race:
    key: str                       # 'ga-sen', 'house:AZ-01'
    office: str                    # 'sen' | 'gov' | 'house'
    state: str                     # lowercase postal
    prior: float                   # prior mean of the final D share
    prior_sd: float                # prior sd of the final share (own, total)
    y: float | None = None         # count-only projection − prior (None: no count signal)
    v: float | None = None         # its variance
    counted: float | None = None
    own_mean: float | None = None  # the race's own live model: final share mean/sd (None: not modelled)
    own_sd: float | None = None
    source: str = "prior"          # 'own' | 'count' | 'civic' | 'prior'
    pool: bool = True              # False: the race's count informs only itself, not the shared factors
    extra: dict | None = None


# ------------------------------------------------------------------------------ inference

class Factors:
    """Shared factors c, h, region (4), state (all in `races`): names, prior sd, and loadings per race."""

    def __init__(self, races: list[Race], p: Params):
        self.p = p
        states = sorted({r.state for r in races})
        regions = sorted(set(REGION.values()))
        self.names = ["common", "house_env"] + [f"region:{g}" for g in regions] + [f"state:{s}" for s in states]
        self.prior_sd = np.array([p.sd_common, p.sd_house_env] + [p.sd_region] * len(regions)
                                 + [p.sd_state] * len(states))
        K, R = len(self.names), len(races)
        A = np.zeros((R, K))
        col = {n: i for i, n in enumerate(self.names)}
        for i, r in enumerate(races):
            house = r.office == "house"
            A[i, 0] = p.house_c_load if house else 1.0
            A[i, 1] = 1.0 if house else 0.0
            A[i, col[f"region:{REGION.get(r.state, 'south')}"]] = 1.0
            A[i, col[f"state:{r.state}"]] = p.state_load_house if house else 1.0
        self.A = A
        shared = (A ** 2) @ (self.prior_sd ** 2)
        sd2 = np.array([r.prior_sd ** 2 for r in races])
        self.eps2 = np.maximum(sd2 - shared, p.sd_floor ** 2)


@dataclass
class Posterior:
    mean: np.ndarray
    cov: np.ndarray


def measurement_var(r: Race, p: Params) -> float:
    return max(float(r.v) * p.meas_inflate, p.meas_floor ** 2, 1e-8)


def informs(r: Race, p: Params) -> bool:
    return (r.y is not None and r.v is not None and np.isfinite(r.y) and np.isfinite(r.v)
            and (r.counted or 0.0) > p.min_counted)


def apply_pool_rule(races: list[Race], p: Params) -> list[Race]:
    """House counts feed the shared factors only when at least p.house_min_pool districts have counted votes."""
    n = sum(1 for r in races if r.office == "house" and informs(r, p))
    if n >= p.house_min_pool:
        return races
    return [replace(r, pool=False) if r.office == "house" else r for r in races]


def posterior(races: list[Race], F: Factors, p: Params, use: np.ndarray | None = None) -> Posterior:
    """Gaussian posterior of the shared factors given every informing race (ε marginalised: noise = ε² + v)."""
    J = np.diag(1.0 / F.prior_sd ** 2)
    b = np.zeros(len(F.names))
    for i, r in enumerate(races):
        if (use is not None and not use[i]) or not informs(r, p) or not r.pool:
            continue
        w = 1.0 / (F.eps2[i] + measurement_var(r, p))
        a = F.A[i]
        J += w * np.outer(a, a)
        b += w * a * r.y
    cov = np.linalg.inv(J)
    return Posterior(cov @ b, cov)


def robust_weights(races: list[Race], F: Factors, p: Params, iters: int = 6) -> np.ndarray:
    """Huber weights (IRLS) of the informing races: w = min(1, k/|z|), z = (y − fitted factors)/sd. 1 for all if off."""
    w = np.ones(len(races))
    if p.huber_k <= 0:
        return w
    idx = [i for i, r in enumerate(races) if informs(r, p)]
    if not idx:
        return w
    base = np.array([F.eps2[i] + measurement_var(races[i], p) for i in idx])
    y = np.array([races[i].y for i in idx])
    for _ in range(iters):
        eff = [replace(r, v=((F.eps2[i] + measurement_var(r, p)) / w[i] - F.eps2[i]) / p.meas_inflate)
               if i in idx else r for i, r in enumerate(races)]
        post = posterior(eff, F, p)
        z = (y - F.A[idx] @ post.mean) / np.sqrt(base)
        w[idx] = np.minimum(1.0, p.huber_k / np.maximum(np.abs(z), 1e-9))
    return w


def race_marginals(races: list[Race], F: Factors, post: Posterior, p: Params) -> pd.DataFrame:
    """Pooled posterior of every race's final share: mean, sd (analytic), k = weight of its own count signal."""
    A = F.A
    fm = A @ post.mean
    fv = np.einsum("ik,kl,il->i", A, post.cov, A)
    rows = []
    for i, r in enumerate(races):
        if informs(r, p):
            v = measurement_var(r, p)
            k = F.eps2[i] / (F.eps2[i] + v)
            m = (1 - k) * fm[i] + k * r.y
            var = (1 - k) ** 2 * fv[i] + k * v
        else:
            k, m, var = 0.0, fm[i], fv[i] + F.eps2[i]
        rows.append((r.prior + m, np.sqrt(var), k))
    return pd.DataFrame(rows, columns=["share", "sd", "k"], index=[r.key for r in races])


class Simulator:
    """Fixed standard-normal draws (same every cycle: the output moves only with the inputs)."""

    def __init__(self, n_races: int, n_factors: int, p: Params):
        rng = np.random.default_rng(p.seed)
        self.z = rng.standard_normal((p.n_sim, n_factors)).astype(np.float32)
        self.u = rng.standard_normal((p.n_sim, n_races)).astype(np.float32)

    def shares(self, races: list[Race], F: Factors, post: Posterior, p: Params, own: bool,
               sl: slice = slice(None)) -> np.ndarray:
        """(rows of sl, n_races) final D shares. own=True: races with their own live model use its marginal."""
        L = np.linalg.cholesky(post.cov + 1e-14 * np.eye(len(post.mean)))
        fac = post.mean + self.z[sl] @ L.T                     # (n, K)
        f = fac @ F.A.T                                        # (n, R)
        fm = F.A @ post.mean
        fv = np.einsum("ik,kl,il->i", F.A, post.cov, F.A)
        R = len(races)
        k = np.zeros(R)
        y = np.zeros(R)
        v = np.zeros(R)
        obs = np.array([informs(r, p) for r in races])
        for i, r in enumerate(races):
            if obs[i]:
                y[i], v[i] = r.y, measurement_var(r, p)
        k = np.where(obs, F.eps2 / (F.eps2 + v), 0.0)
        noise_var = np.where(obs, k * v, F.eps2)
        e = (1 - k) * f + k * y + np.sqrt(noise_var) * self.u[sl]
        share = np.array([r.prior for r in races]) + e
        if own:
            mean_p = (1 - k) * fm + k * y
            sd_p = np.sqrt(np.where(obs, (1 - k) ** 2 * fv + k * v, fv + F.eps2))
            for i, r in enumerate(races):
                if r.own_mean is not None and r.own_sd is not None and obs[i]:
                    share[:, i] = r.own_mean + r.own_sd * (e[:, i] - mean_p[i]) / max(sd_p[i], 1e-9)
        return share


def seat_summary(seats: np.ndarray, need: int, total: int) -> dict:
    dist = np.bincount(seats.astype(int), minlength=total + 1)[: total + 1] / len(seats)
    return {"p_control": float(np.mean(seats >= need)), "mean": float(seats.mean()),
            "p10": int(np.percentile(seats, 10)), "p50": int(np.percentile(seats, 50)),
            "p90": int(np.percentile(seats, 90)), "dist": [round(float(x), 5) for x in dist]}


def evaluate(races: list[Race], p: Params, d_holdover: int, sim: Simulator | None = None) -> dict:
    """Everything the night file needs from a list of races (pure function of its inputs)."""
    races = apply_pool_rule(races, p)
    F = Factors(races, p)
    w = robust_weights(races, F, p)
    if p.huber_k > 0:                                   # a down-weighted race: its whole noise variance (ε² + v) / w
        races = [replace(r, v=((F.eps2[i] + measurement_var(r, p)) / w[i] - F.eps2[i]) / p.meas_inflate)
                 if informs(r, p) else r for i, r in enumerate(races)]
    sim = sim or Simulator(len(races), len(F.names), p)
    sen = np.array([r.office == "sen" for r in races])
    hou = np.array([r.office == "house" for r in races])
    out: dict = {"factors": {}, "modes": {}}
    posts = {"live": posterior(races, F, p), "prior": posterior([replace(r, y=None) for r in races], F, p)}
    for name, post in posts.items():
        out["factors"][name] = {n: (float(m), float(np.sqrt(post.cov[i, i])))
                                for i, (n, m) in enumerate(zip(F.names, post.mean))}
    for mode, post, own in (("live", posts["live"], True), ("pooled", posts["live"], False),
                            ("prior", posts["prior"], False)):
        rs = races if mode != "prior" else [replace(r, y=None) for r in races]
        wins, sum_w, sum_s, sum_s2 = [], 0.0, 0.0, 0.0
        for a in range(0, p.n_sim, CHUNK):
            share = sim.shares(rs, F, post, p, own, slice(a, a + CHUNK))
            win = share > 0.5
            wins.append((win[:, sen].sum(axis=1), win[:, hou].sum(axis=1)))
            sum_w, sum_s, sum_s2 = sum_w + win.sum(axis=0), sum_s + share.sum(axis=0), sum_s2 + (share ** 2).sum(axis=0)
        n = p.n_sim
        sen_seats = d_holdover + np.concatenate([w[0] for w in wins])
        hou_seats = np.concatenate([w[1] for w in wins])
        s = seat_summary(sen_seats, p.senate_need, 100)
        h = seat_summary(hou_seats, p.house_need, int(hou.sum()))
        sc, hc = sen_seats >= p.senate_need, hou_seats >= p.house_need
        mean_s = sum_s / n
        out["modes"][mode] = {
            "senate": s, "house": h,
            "joint": {"DD": float(np.mean(sc & hc)), "DR": float(np.mean(sc & ~hc)),    # D Senate, R House
                      "RD": float(np.mean(~sc & hc)), "RR": float(np.mean(~sc & ~hc))},
            "p_win": dict(zip([r.key for r in races], (sum_w / n).round(4).tolist())),
            "share_mean": dict(zip([r.key for r in races], mean_s.round(4).tolist())),
            "share_sd": dict(zip([r.key for r in races], np.sqrt(np.maximum(sum_s2 / n - mean_s ** 2, 0)).round(4).tolist()))}
    out["pooled"] = race_marginals(races, F, posts["live"], p)
    return out


# ------------------------------------------------------------------------------ inputs

def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def last_rows(path: Path) -> dict[str, dict]:
    """Latest timeline row per race of a timeline(_tail).json."""
    d = _read_json(path)
    return {k: v[-1] for k, v in (d or {}).items() if isinstance(v, list) and v}


def _num(x) -> float | None:
    return None if x is None or not isinstance(x, (int, float)) or not np.isfinite(x) else float(x)


def poll_model_versions(config_dir: Path) -> dict[str, str]:
    """race_id -> the version whose live row is the race's own model (us.poll_model of its registered configs)."""
    paths = [p for p in sorted(Path(config_dir).glob("us2026_*_v1.toml"))]
    out = {}
    for p in paths:
        try:
            cfg = us.load_config(p)
            cfgs, versions = us.load_races([p])
            rid = cfg["election"]["race_id"]
        except Exception:                                       # not a race config
            continue
        pm = us.poll_model(versions.get(rid, {}))
        out[rid] = pm or "v1"
    return out


def count_signal(row: dict | None, prior: float, v1_keys: tuple[str, str] = ("share", "sd")) -> tuple:
    """(y, v, counted) from a live timeline row's count-only projection; (None, None, counted) without a count."""
    if not row:
        return None, None, None
    counted = _num(row.get("counted"))
    share, sd = _num(row.get(v1_keys[0])), _num(row.get(v1_keys[1]))
    if not counted or counted <= 0 or share is None or sd is None or sd <= 0:
        return None, None, counted
    return share - prior, sd ** 2, counted


def own_model(row: dict | None, version: str) -> tuple[float | None, float | None]:
    if not row or not _num(row.get("counted")):
        return None, None
    v = "" if version == "v1" else f"_{version}"
    m, s = _num(row.get(f"share{v}")), _num(row.get(f"sd{v}"))
    return (m, s) if m is not None and s else (None, None)


def env_shift(ov: dict) -> tuple[float, int]:
    """Mean (poll average − 2024 president baseline) of the polled Senate/governor races without a county model: the
    national environment applied to races without polls (their estimate is the 2024 baseline alone)."""
    d, w = [], []
    for o in ov["offices"].values():
        for r in o.get("races", []):
            e, pa = r.get("estimate") or {}, r.get("polls")
            if pa and e.get("baseline_share") is not None and not r.get("modeled"):
                d.append(pa["avg"] - e["baseline_share"])
                w.append(1.0 / pa["sd"] ** 2)
    return (float(np.average(d, weights=w)), len(d)) if d else (0.0, 0)


CIVIC_KEY = "civic_87524"          # recorder key of https://civicapi.org/api/v2/race/87524 (House, all 435 districts)
CIVIC_ATTRIBUTION = {"name": "civicAPI (civicapi.org)", "url": "https://civicapi.org"}


def civic_signals(blob: bytes, p: Params) -> dict[str, dict]:
    """District ('AZ-01', 'AK-AL') -> {'share', 'var', 'counted', 'votes'} from one civicAPI House snapshot: Democratic
    two-party share of the votes counted, and the partial-count variance (docs/us_national.md). Districts with no votes,
    zero percent reporting or only one major party's votes give nothing."""
    out = {}
    doc = json.loads(blob)
    if doc.get("test_data"):                                   # civicAPI's own rehearsal payload
        return out
    for key, d in (doc.get("region_results") or {}).items():
        f = (d.get("percent_reporting") or 0) / 100.0
        v = {"D": 0.0, "R": 0.0}
        for c in d.get("candidates") or []:
            k = {"Democratic": "D", "Republican": "R"}.get(c.get("party"))
            if k:
                v[k] += float(c.get("votes") or 0)
        n = v["D"] + v["R"]
        if f <= 0 or v["D"] <= 0 or v["R"] <= 0 or n < p.civic_min_votes:
            continue
        f = min(f, 1.0)
        out[key.upper()] = {"share": v["D"] / n, "counted": f, "votes": n,
                            "var": p.civic_a * (1 - f) / max(f, 0.02) + (p.civic_tau * (1 - f)) ** 2}
    return out


def civic_uncontested(blob: bytes) -> dict[str, str]:
    """District -> 'D' / 'R' when the snapshot lists candidates of one major party only (no contest: the seat is that
    party's; includes California/Washington same-party generals). Empty candidate lists give nothing."""
    out, major = {}, {"Democratic": "D", "Republican": "R"}
    for key, d in (json.loads(blob).get("region_results") or {}).items():
        ps = {major.get(c.get("party")) for c in d.get("candidates") or []} - {None}
        if len(ps) == 1:
            out[key.upper()] = ps.pop()
    return out


UNCONTESTED_SD = 0.01


def build_races(ov: dict, house: dict, sen_rows: dict, house_rows: dict, versions: dict[str, str],
                p: Params, extra: dict[str, dict] | None = None, now: pd.Timestamp | None = None,
                civic: dict[str, dict] | None = None, uncontested: dict[str, str] | None = None) -> list[Race]:
    """All Senate (35) and governor races of the overview and the 435 House districts as Race objects with priors and,
    where counted, the count signal. extra: race_key -> {'share','sd','counted'} from the extra-feed states."""
    shift, _ = env_shift(ov)
    races: list[Race] = []

    def closed(st: str) -> bool:                       # returns stamped before a state's polls close are ignored
        return now is None or st not in STATE_CLOSE or pd.Timestamp(STATE_CLOSE[st][0]) <= now

    for off_name, off in (("sen", "senate"), ("gov", "governor")):
        for r in ov["offices"].get(off, {}).get("races", []):
            pa, est = r.get("polls"), r.get("estimate") or {}
            if pa:
                prior, sd = pa["avg"], max(pa["sd"], 0.01)
            elif est.get("baseline_share") is not None:
                prior, sd = est["baseline_share"] + shift, p.sd_unpolled
            else:
                prior, sd = 0.5, p.sd_unpolled
            key = r["race_key"]
            row = sen_rows.get(r.get("race_id") or key) if r.get("modeled") else None
            src = {"share": row.get("share"), "sd": row.get("sd"), "counted": row.get("counted")} if row else \
                (extra or {}).get(key)
            if not closed(r["state"]):
                row = src = None
            y, v, counted = count_signal(src, prior)
            om, osd = own_model(row, versions.get(r.get("race_id") or key, "v1")) if row else (None, None)
            races.append(Race(key=key, office=off_name, state=r["state"], prior=prior, prior_sd=sd, y=y, v=v,
                              counted=counted, own_mean=om, own_sd=osd,
                              source="own" if om is not None else ("count" if y is not None else "prior"),
                              extra={"special": bool(r.get("special")), "modeled": bool(r.get("modeled"))}))
    for did, d in house["districts"].items():
        b = d["baseline"]
        key = f"house:{did}"
        row = house_rows.get(key) if closed(d["st"].lower()) else None
        y, v, counted = count_signal(row, b["share"])
        om, osd = own_model(row, "v1")
        src = "own" if om is not None else "prior"
        c = (civic or {}).get(did)
        if y is None and c and closed(d["st"].lower()):          # all-district totals where there is no district model
            y, v, counted, src = c["share"] - b["share"], c["var"], c["counted"], "civic"
        prior, psd, ex = b["share"], p.house_sd or b["sd"], None
        if (uncontested or {}).get(did):                          # one major party on the ballot: the seat is known
            prior, psd, ex = (0.999 if uncontested[did] == "D" else 0.001), UNCONTESTED_SD, {"uncontested": uncontested[did]}
            y = v = counted = om = osd = None
            src = "prior"
        races.append(Race(key=key, office="house", state=d["st"].lower(), prior=prior, prior_sd=psd,
                          y=y, v=v, counted=counted, own_mean=om, own_sd=osd, source=src, extra=ex))
    return races


# ------------------------------------------------------------------------------ markets

BOP = re.compile(r"balance-of-power.*?-(d|r)-senate-(d|r)-house", re.I)


def control_markets(ov: dict, pm: dict[str, float], ks: dict[str, float]) -> dict:
    """Prices of the Senate and House control markets (Polymarket 'event/slug', Kalshi ticker) and the four balance-of-
    power markets (Polymarket event balance-of-power-2026-midterms) from the recorder's latest books. P(D)."""
    out: dict = {"senate": {}, "house": {}, "bop": {}}
    for chamber in ("senate", "house"):
        for m in ov["offices"].get(chamber, {}).get("control_markets", []):
            if m["platform"] == "polymarket" and f"{m['event']}/{m['slug']}" in pm:
                out[chamber]["polymarket"] = pm[f"{m['event']}/{m['slug']}"]
            if m["platform"] == "kalshi" and m["ticker"] in ks:
                out[chamber]["kalshi"] = ks[m["ticker"]]
    for k, price in pm.items():
        mm = BOP.search(k)
        if mm:
            out["bop"][mm.group(1).upper() + mm.group(2).upper()] = price
    return out


# ------------------------------------------------------------------------------ night state

def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


class Night:
    """One national estimate per call: inputs in, a JSON-able document and a timeline row out."""

    def __init__(self, p: Params, d_holdover: int):
        self.p, self.d_holdover = p, d_holdover
        self.sim: Simulator | None = None
        self.prior_cache: dict | None = None

    def run(self, now: pd.Timestamp, races: list[Race], mk: dict | None = None) -> tuple[dict, dict]:
        p = self.p
        if self.sim is None or self.sim.u.shape[1] != len(races):
            self.sim = Simulator(len(races), len(Factors(races, p).names), p)
        ev = evaluate(races, p, self.d_holdover, self.sim)
        live, pooled, prior = ev["modes"]["live"], ev["modes"]["pooled"], ev["modes"]["prior"]
        fac = ev["factors"]["live"]
        closed = [s for s, (a, b) in STATE_CLOSE.items() if pd.Timestamp(a) <= now]
        informing = [r for r in races if informs(r, p)]
        pooled_rules = apply_pool_rule(races, p)
        n_pooled = len([r for r in pooled_rules if informs(r, p) and r.pool])
        house_pooled = any(r.office == "house" and r.pool and informs(r, p) for r in pooled_rules)
        regions = {k.split(":")[1]: {"mean": _r(m * 100, 2), "sd": _r(s * 100, 2)}
                   for k, (m, s) in fac.items() if k.startswith("region:")}
        states = {k.split(":")[1]: {"mean": _r(m * 100, 2), "sd": _r(s * 100, 2)}
                  for k, (m, s) in fac.items() if k.startswith("state:")
                  and any(r.state == k.split(":")[1] for r in informing)}
        c, h = fac["common"], fac["house_env"]
        pooled_m = ev["pooled"]
        rows_sen = {}
        for r in races:
            if r.office == "house":
                continue
            rows_sen[r.key] = {"office": r.office, "state": r.state, "prior": _r(r.prior), "prior_sd": _r(r.prior_sd),
                               "source": r.source, "counted": _r(r.counted), "modeled": bool((r.extra or {}).get("modeled")),
                               "p_d": live["p_win"][r.key], "share": live["share_mean"][r.key],
                               "sd": live["share_sd"][r.key], "p_d_pooled": pooled["p_win"][r.key],
                               "p_d_prior": prior["p_win"][r.key]}
        rows_house = {r.key.split(":")[1]: [_r(r.prior), live["p_win"][r.key], live["share_mean"][r.key],
                                           live["share_sd"][r.key], {"own": "o", "prior": "p", "civic": "v"}.get(r.source, "c"),
                                           _r(r.counted)] for r in races if r.office == "house"}
        doc = {
            "as_of": us._iso(now), "n_races": len(races), "n_informing": len(informing), "n_pooled": n_pooled,
            "n_states_closed": len(closed), "n_states": len(STATE_CLOSE),
            "hours_since_first_close": _r((now - FIRST_CLOSE).total_seconds() / 3600, 3),
            "swing": {"unit": "points of two-party D share vs the pre-count priors (positive = more Democratic than "
                              "priors)", "common": {"mean": _r(c[0] * 100, 2), "sd": _r(c[1] * 100, 2),
                                                    "prior_sd": _r(p.sd_common * 100, 2)},
                      "house_env": {"mean": _r(h[0] * 100, 2), "sd": _r(h[1] * 100, 2)},
                      "house_total": {"mean": _r((c[0] + h[0]) * 100, 2)},
                      "regions": regions, "states": states},
            "senate": {"d_holdover": self.d_holdover, "need": p.senate_need,
                       "p_control": _r(live["senate"]["p_control"]), "p_control_pooled": _r(pooled["senate"]["p_control"]),
                       "p_control_prior": _r(prior["senate"]["p_control"]),
                       "seats": {k: live["senate"][k] for k in ("mean", "p10", "p50", "p90", "dist")},
                       "seats_prior": {k: prior["senate"][k] for k in ("mean", "p10", "p90")},
                       "races": rows_sen},
            "house": {"need": p.house_need, "p_control": _r(live["house"]["p_control"]),
                      "p_control_pooled": _r(pooled["house"]["p_control"]),
                      "p_control_prior": _r(prior["house"]["p_control"]),
                      "seats": {k: live["house"][k] for k in ("mean", "p10", "p50", "p90", "dist")},
                      "seats_prior": {k: prior["house"][k] for k in ("mean", "p10", "p90")},
                      "pooled": house_pooled,
                      "n_own_model": int(sum(r.source == "own" for r in races if r.office == "house")),
                      "n_civic": int(sum(r.source == "civic" for r in races if r.office == "house")),
                      "districts_cols": ["prior", "p_d", "share", "sd", "src(o=own model,v=civicAPI totals,c=count,p=prior)", "counted"],
                      "districts": rows_house},
            "joint": {"live": {k: _r(v) for k, v in live["joint"].items()},
                      "prior": {k: _r(v) for k, v in prior["joint"].items()},
                      "note": "first letter = Senate, second = House; D = Democratic control"},
            "markets": mk or {},
        }
        if mk:
            doc["gaps"] = {"senate": {k: _r(live["senate"]["p_control"] - v) for k, v in mk.get("senate", {}).items()},
                           "house": {k: _r(live["house"]["p_control"] - v) for k, v in mk.get("house", {}).items()},
                           "bop": {k: _r(live["joint"][k] - v) for k, v in mk.get("bop", {}).items()
                                   if k in live["joint"]}}
        row = {"t": us._iso(now), "h_close": doc["hours_since_first_close"], "n_inf": len(informing),
               "n_closed": len(closed), "c": _r(c[0] * 100, 2), "c_sd": _r(c[1] * 100, 2),
               "h": _r(h[0] * 100, 2), "h_sd": _r(h[1] * 100, 2),
               "P_sen": doc["senate"]["p_control"], "P_sen_pooled": doc["senate"]["p_control_pooled"],
               "P_sen_prior": doc["senate"]["p_control_prior"],
               "sen_mean": _r(live["senate"]["mean"], 2), "sen_p10": live["senate"]["p10"], "sen_p90": live["senate"]["p90"],
               "P_house": doc["house"]["p_control"], "P_house_pooled": doc["house"]["p_control_pooled"],
               "P_house_prior": doc["house"]["p_control_prior"],
               "house_mean": _r(live["house"]["mean"], 1), "house_p10": live["house"]["p10"],
               "house_p90": live["house"]["p90"],
               "pm_sen": (mk or {}).get("senate", {}).get("polymarket"), "kalshi_sen": (mk or {}).get("senate", {}).get("kalshi"),
               "pm_house": (mk or {}).get("house", {}).get("polymarket"),
               "kalshi_house": (mk or {}).get("house", {}).get("kalshi"),
               "joint": {k: _r(v) for k, v in live["joint"].items()}, "bop": (mk or {}).get("bop") or None}
        return doc, row


# ------------------------------------------------------------------------------ live process

THIN = ((pd.Timedelta("48h"), 13), (pd.Timedelta("6h"), 15))


def thin(rows: list[dict]) -> list[dict]:
    """Same thinning as the House process: older than 6 h one row per 10 minutes, older than 48 h one per hour."""
    from .us_house_night import HouseExporter
    return HouseExporter.thin(rows)


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


class Live:
    def __init__(self, cfg: dict, out: Path, snap_root: Path | None = None, extra: bool = True):
        self.cfg, self.out = cfg, Path(out)
        self.p = load_params(cfg)
        S = cfg["sources"]
        self.site = self.out / us.EID
        self.dir = self.site / OUT_DIR
        self.paths = {k: self.site / v for k, v in S.items() if k.endswith("_json")}
        self.config_dir = Path(us.ROOT_DIR) / cfg["sources"].get("config_dir", "configs")
        self.versions = poll_model_versions(self.config_dir)
        self.arc = us.Archive(snap_root or us.SNAP / us.TAG)
        self.night = Night(self.p, 0)
        self.timeline: list[dict] = []
        self.errors: dict[str, str] = {}
        self.extra = None
        old = _read_json(self.dir / "timeline.json")
        if isinstance(old, list):
            self.timeline = old
        if extra:
            from . import us_national_extra
            self.extra = us_national_extra.ExtraStates(cfg, self.arc)

    def inputs(self) -> tuple[dict, dict, dict, dict]:
        ov = _read_json(self.paths["overview_json"])
        house = _read_json(self.paths["districts_json"])
        if ov is None or house is None:
            raise FileNotFoundError("overview.json / house districts.json unreadable")
        return ov, house, last_rows(self.paths["senate_timeline_json"]), last_rows(self.paths["house_timeline_json"])

    def civic(self, now: pd.Timestamp) -> tuple[dict, dict]:
        """(count signals, uncontested seats) of the latest civicAPI House snapshot."""
        try:
            self.arc.refresh()
            f = self.arc.at(CIVIC_KEY, now)
            blob = self.arc.read(f) if f else None
            self.errors.pop("civic", None)
            return (civic_signals(blob, self.p), civic_uncontested(blob)) if blob else ({}, {})
        except Exception as e:                                 # an unreadable snapshot must not block the model
            self.errors["civic"] = f"{type(e).__name__}: {e}"
            return {}, {}

    def markets(self, ov: dict, now: pd.Timestamp) -> dict:
        try:
            self.arc.refresh()
            pm = us.pm_prices(self.arc, self.arc.at("polymarket_books", now))
            ks = us.kalshi_prices(self.arc, self.arc.at("kalshi_books", now))
            return control_markets(ov, pm, ks)
        except Exception as e:                                 # market data must not block the model
            self.errors["markets"] = f"{type(e).__name__}: {e}"
            return {}

    def step(self, now: pd.Timestamp) -> dict:
        ov, house, sen_rows, house_rows = self.inputs()
        extra = {}
        if self.extra is not None:
            try:
                extra = self.extra.signals(now)
            except Exception as e:
                self.errors["extra"] = f"{type(e).__name__}: {e}"
            else:
                self.errors.pop("extra", None)
                if self.extra.errors:                  # states whose feed is down keep their last good rows
                    self.errors["extra"] = "; ".join(f"{k}: {v}" for k, v in self.extra.errors.items())
        sig, unc = self.civic(now)
        races = build_races(ov, house, sen_rows, house_rows, self.versions, self.p, extra, now, sig, unc)
        hold = ov["offices"]["senate"].get("holdovers") or {}
        self.night.d_holdover = int(hold.get("D", 0) + hold.get("I", 0))
        doc, row = self.night.run(now, races, self.markets(ov, now))
        doc["errors"] = dict(self.errors)
        self.timeline.append(row)
        self.timeline = thin(self.timeline)
        return doc

    def write(self, doc: dict) -> None:
        us._write_json(self.dir / "national.json", doc)
        us._write_json(self.dir / "timeline.json", self.timeline)
        last = pd.Timestamp(self.timeline[-1]["t"])
        us._write_json(self.dir / "timeline_tail.json",
                       {"rows": [r for r in self.timeline if pd.Timestamp(r["t"]) > last - pd.Timedelta("60min")],
                        "tail": True})
        us._write_json(self.dir / "meta.json", {
            "id": self.cfg["id"], "as_of": doc["as_of"], "params": {f.name: getattr(self.p, f.name)
                                                                                  for f in fields(Params)},
            "schema": "docs/us_national.md", "sources": [CIVIC_ATTRIBUTION], "inputs": {k: str(v.relative_to(self.site)) for k, v in self.paths.items()}})
        us._write_json(self.dir / "status.json", {"t": doc["as_of"], "errors": self.errors})


def run_live(cfg_path: Path, out: Path, snap_root: Path | None = None, interval: float | None = None,
             stop_at: pd.Timestamp | None = None, cycles: int | None = None) -> None:
    cfg = load_config(cfg_path)
    live = Live(cfg, out, snap_root)
    interval = interval or float(cfg.get("live", {}).get("interval", 60))
    n = 0
    while (stop_at is None or pd.Timestamp.now(tz="UTC") < stop_at) and (cycles is None or n < cycles):
        t0 = time.monotonic()
        now = pd.Timestamp.now(tz="UTC").floor("1min")
        try:
            doc = live.step(now)
            live.write(doc)
            print(f"{now:%H:%M:%S} national cycle {time.monotonic() - t0:.1f}s informing={doc['n_informing']} "
                  f"P_sen={doc['senate']['p_control']} P_house={doc['house']['p_control']}"
                  + (f", errors: {live.errors}" if live.errors else ""), flush=True)
        except Exception as e:                                 # never stop on the night
            print(f"{now:%H:%M:%S} national error, continuing: {type(e).__name__}: {e}", flush=True)
        n += 1
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))


def main(argv: list[str] | None = None) -> None:
    """python -m brunder.us_national live configs/us2026_national/national_v1.toml --out site/data [--snap DIR]
    [--interval 60] [--until ISO]"""
    ap = argparse.ArgumentParser(prog="python -m brunder.us_national", description=main.__doc__)
    ap.add_argument("cmd", choices=["live"])
    ap.add_argument("config", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--snap", type=Path, default=None)
    ap.add_argument("--interval", type=float, default=None)
    ap.add_argument("--until", default=None)
    a = ap.parse_args(argv)
    run_live(a.config, a.out, a.snap, a.interval, pd.Timestamp(a.until) if a.until else None)


if __name__ == "__main__":
    main()
