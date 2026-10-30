"""Replay NZ 2017/2020/2023 election nights through ``brunder.newzealand`` and score the seat distribution.

The Electoral Commission keeps no timestamped count archive, so *voting-place* results (official) are revealed in
simulated orders (``SCHEMES``); at stage s the first ``s`` of the ordinary voting places (advance + election day) have
reported and special/overseas votes have not -- as on a real election night.  Truth is the official FINAL result
(seats from ``overall-results-summary.csv``), which is what markets settle on.  Each election's prior is the
previous election's electorate results (2014 HTML for 2017), exactly the structure of the 2026 model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np
import pandas as pd

import nz_history as H
from brunder import newzealand as nz

SCHEMES = ("random", "small_first", "advance_first", "advance_last", "region_lag")
NIGHT = ("night",)          # the realistic release order (advance first, then election day); scored separately
STAGES = (0.05, 0.25, 0.50, 0.75, 0.95, 1.00)
EPS = 1e-3          # probability floor for log loss (one event in 1,000 -- below the sampling noise of 4,000 draws)


@lru_cache(maxsize=None)
def election(year: int) -> H.Election:
    return H.Election(year)


@lru_cache(maxsize=None)
def election_2014() -> pd.DataFrame:
    a = (H.HIST / "e9_part4_2014.html").read_text(encoding="utf-8", errors="replace")
    b = (H.HIST / "e9_part6_2014.html").read_text(encoding="utf-8", errors="replace")
    return H.parse_2014(a, b)


def prior_table(year: int) -> tuple[pd.DataFrame, dict[str, str]]:
    """Previous election's electorate party votes (keys as columns) + 'valid' and winner key by electorate name."""
    if year == 2017:
        d = election_2014()
        return d.drop(columns=["winner", "valid"]).astype(float), d["winner"].to_dict()
    prev = election(year - 3)
    d = pd.DataFrame({e: pd.Series(prev.final_pv[e], index=prev.party_keys) for e in prev.electorates}).T
    return d, prev.winners


def units_for(E: H.Election) -> pd.DataFrame:
    prev, win = prior_table(E.year)
    nat = prev.sum() / prev.to_numpy().sum()
    nat_shares = np.array([nat.get(k, 0.0) for k in E.party_keys])
    med_v = float(prev.sum(1).median())
    rows = []
    for i, e in enumerate(E.electorates, 1):
        if e in prev.index:
            r = prev.loc[e]
            v = float(r.sum())
            sh = np.array([r.get(k, 0.0) for k in E.party_keys]) / v
            w = win.get(e, "")
        else:
            sh, v, w = nat_shares, med_v, ""
        rows.append({"e_no": i, "unit_id": f"u{i}", "name": e, "region": "gen", "layer": "general", "V": v,
                     "winner": w, "winner_margin": 0.0, **dict(zip(E.party_keys, sh))})
    return pd.DataFrame(rows).set_index("e_no")


class Order:
    """A reproducible national count order over all ordinary voting places of one election."""

    def __init__(self, E: H.Election, scheme: str, seed: int):
        rng = np.random.default_rng(seed)
        K = len(E.party_keys)
        ei, kind, valid, P, C = [], [], [], [], []
        for i, e in enumerate(E.electorates):
            pl = E.places[e]
            ei += [i] * len(pl)
            kind += list(pl["kind"])
            valid += list(pl["valid"])
            P += list(pl["P"])
            C += list(pl["C"])
        self.ei, self.valid = np.array(ei), np.array(valid, float)
        self.kind = np.array(kind)
        self.P, self.C = np.array(P).reshape(-1, K), np.array(C).reshape(-1, K + 1)
        self.n_e = len(E.electorates)
        N = len(self.ei)
        size = np.log(self.valid + 5)
        if scheme == "random":
            t = rng.random(N)
        elif scheme == "small_first":
            t = 0.6 * size + 0.6 * rng.standard_normal(N)
        elif scheme == "advance_first":
            t = (self.kind != "advance") * 10.0 + rng.random(N)
        elif scheme == "advance_last":
            t = (self.kind == "advance") * 10.0 + rng.random(N)
        elif scheme == "region_lag":
            lag = 1.2 * rng.standard_normal(self.n_e)
            t = lag[self.ei] + 0.5 * rng.standard_normal(N)
        elif scheme == "night":
            # Election-night release order: every electorate's advance places first (within a few minutes of each
            # other), then election-day places trickling in with electorate-level lags and small places earlier.
            lag = 1.2 * rng.standard_normal(self.n_e)
            adv = self.kind == "advance"
            t = np.where(adv, 0.3 * lag[self.ei] + 0.2 * rng.standard_normal(N),
                         10.0 + lag[self.ei] + 0.3 * size + 0.5 * rng.standard_normal(N))
        else:
            raise ValueError(scheme)
        self.rank = np.argsort(np.argsort(t))
        self.N = N
        self.places_total = np.bincount(self.ei, minlength=self.n_e).astype(float)

    def counts(self, E: H.Election, s: float) -> pd.DataFrame:
        K = len(E.party_keys)
        m = self.rank < int(round(s * self.N))
        e = self.ei[m]
        c = nz.empty_counts(pd.DataFrame(index=pd.RangeIndex(1, self.n_e + 1)), E.party_keys)
        places = np.bincount(e, minlength=self.n_e).astype(float)
        c["places"], c["places_total"] = places, self.places_total
        c["f"] = places / np.maximum(self.places_total, 1)
        for j, k in enumerate(E.party_keys):
            c[f"pv_{k}"] = np.bincount(e, weights=self.P[m, j], minlength=self.n_e)
            c[f"cv_{k}"] = np.bincount(e, weights=self.C[m, j], minlength=self.n_e)
        c["cv_IND"] = np.bincount(e, weights=self.C[m, K], minlength=self.n_e)
        c["valid"] = sum(c[f"pv_{k}"] for k in E.party_keys)
        return c


# ---------------------------------------------------------------------------------------------- scoring

def bands(party: str) -> list[tuple[int, int]]:
    """Market-like seat brackets: width 5 for NAT/LAB, width 3 otherwise (aligned like the Polymarket bands)."""
    if party in ("NAT", "LAB"):
        edges = list(range(0, 75, 5)); w = 5
    else:
        edges = [0] + list(range(4, 40, 3)); w = 3
        return [(lo, (edges[i + 1] - 1) if i + 1 < len(edges) else 200) for i, lo in enumerate(edges)]
    return [(lo, lo + w - 1 if i + 1 < len(edges) else 200) for i, lo in enumerate(edges)]


def score(seats: np.ndarray, votes: np.ndarray, E: H.Election, parties: list[str]) -> dict:
    """Metrics of one projection against the official final result."""
    truth = E.official_seats()
    fv = E.final_party_votes()
    D = len(seats)
    out = {"cover": [], "ae": [], "ae_med": [], "brier": [], "ll": [], "ll_worst": [], "pit": [], "brier_big": []}
    for j, k in enumerate(parties):
        t = truth.get(k, 0)
        s = seats[:, j]
        if t == 0 and s.mean() < 0.5:
            continue
        lo, hi = np.quantile(s, [.05, .95])
        out["cover"].append(float(lo <= t <= hi))
        out["ae"].append(abs(s.mean() - t))
        out["ae_med"].append(abs(np.median(s) - t))
        out["pit"].append(float((s < t).mean() + .5 * (s == t).mean()))
        bs = bands(k)
        p = np.array([((s >= a) & (s <= b)).mean() for a, b in bs])
        y = np.array([a <= t <= b for a, b in bs], float)
        out["brier"].append(float(((p - y) ** 2).sum()))
        pa = (p * D + 0.5) / (D + 1)            # same smoothing for every variant
        pt = max(float(pa[y > 0][0]), EPS)
        out["ll"].append(-math.log(pt))
        out["ll_worst"].append(-math.log(pt))
        if k in ("NAT", "LAB"):
            out["brier_big"].append(float(((p - y) ** 2).sum()))
    # most seats
    tk = np.array([truth.get(k, 0) for k in parties], float)
    key_t = tk * 1e12 + fv
    top_t = int(np.argmax(key_t))
    key = seats.astype(float) * 1e12 + votes
    p_top = (np.argmax(key, axis=1) == top_t).mean()
    out["p_most"] = float(p_top)
    out["brier_most"] = float(sum(((np.argmax(key, axis=1) == j).mean() - (j == top_t)) ** 2 for j in range(len(parties))))
    # vote margin NAT-LAB bands of 5pp
    ix = {k: i for i, k in enumerate(parties)}
    if "NAT" in ix and "LAB" in ix:
        tot = votes.sum(1)
        m = 100 * (votes[:, ix["NAT"]] - votes[:, ix["LAB"]]) / tot
        mt = 100 * (fv[ix["NAT"]] - fv[ix["LAB"]]) / fv.sum()
        edges = [-100, -15, -10, -5, 0, 5, 10, 100]
        pb = np.array([((m >= a) & (m < b)).mean() for a, b in zip(edges[:-1], edges[1:])])
        yb = np.array([a <= mt < b for a, b in zip(edges[:-1], edges[1:])], float)
        out["brier_margin"] = float(((pb - yb) ** 2).sum())
        out["ll_margin"] = -math.log(max(float(((pb * D + .5) / (D + 1))[yb > 0][0]), EPS))
    # national share error (final vs projected mean), per party >= 3%
    sh = votes.mean(0) / votes.mean(0).sum()
    fsh = fv / fv.sum()
    big = fsh >= 0.03
    out["share_log_err"] = [float(math.log(fsh[j] / max(sh[j], 1e-9))) for j in np.where(big)[0]]
    out["share_z"] = [float((math.log(fsh[j]) - np.log(votes[:, j] / votes.sum(1)).mean()) /
                            np.log(votes[:, j] / votes.sum(1)).std()) for j in np.where(big)[0]]
    return out


def run_one(year: int, st: nz.Settings, scheme: str, seed: int, stages=STAGES, draws: int | None = None,
            advance_from: int | None = None) -> list[dict]:
    E = election(year)
    units = units_for(E)
    prof = None
    if advance_from is not None and st.advance_shrink > 0:
        prof = nz.advance_profile(list(units["name"]), election(advance_from).advance_table(), E.party_keys)
    od = Order(E, scheme, seed)
    st = replace(st, draws=draws or st.draws)
    rows = []
    for s in stages:
        c = od.counts(E, s)
        p = nz.project(units, c, st, E.party_keys, advance=prof)
        rng = np.random.default_rng(1000 * seed + int(100 * s) + year)
        votes, electorate, seats = nz.seat_draws(p, st, rng)
        m = score(seats, votes, E, p.parties)
        m.update(year=year, scheme=scheme, seed=seed, stage=s, counted=p.counted_frac)
        rows.append(m)
    return rows
