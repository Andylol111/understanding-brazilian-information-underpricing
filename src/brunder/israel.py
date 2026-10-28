"""Israel Knesset elections: seat allocation, count parsers, and a locality-level count nowcast.

Seats (Knesset Elections Law [Consolidated Version] 5729-1969, sections 81-81A; "Bader-Ofer"):
  1. A list takes part only if its votes are at least 3.25% of all valid votes.
  2. General quota = valid votes of the passing lists / 120; each list gets floor(votes / quota).
  3. The remaining seats go one at a time to the largest votes / (seats + 1) (D'Hondt), where the two
     lists of a surplus-vote agreement count as one list, provided both passed the threshold.
  4. A pair's seats are split between its two lists the same way: pair quota = pair votes / pair seats,
     floor for each, the leftover seat by the larger votes / (seats + 1).

Nowcast (one call per CEC release): units are localities (CEC/CBS locality code) plus the double-envelope
pseudo-locality (code 9999), with a prior from the previous Knesset's final result in the same locality.
  - expected valid votes: prior valid votes x a growth factor (national, estimated from the counted
    localities; local, from the locality's own counted ballot boxes, both shrunk);
  - party shares where uncounted: weighted ridge regression ("transition matrix") of each new list's share
    in counted localities on the locality's previous-election composition, shrunk to the national share
    counted so far, so no party lineage between elections is needed; a partly counted locality mixes its
    own counted share with that prediction (not the envelopes: they are counted in batches by category);
  - uncertainty: independent locality errors (residual variance of the regression), the regression
    coefficients' own uncertainty, one shared error per list of sd tau x sqrt(m(1-m)) on its share of the
    uncounted votes, and a turnout error on the uncounted total.
Simulated final votes are run through `allocate_many` to price the seat markets.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SEATS = 120
THRESHOLD = 0.0325
ENVELOPE = 9999                     # CEC locality code of the double-envelope ("external") ballots
OTHER = "_other"


# ---------------------------------------------------------------------------------------- seat allocation

def allocate_many(votes: np.ndarray, pairs: list[tuple[int, int]] = (), seats: int = SEATS,
                  threshold: float = THRESHOLD) -> np.ndarray:
    """Bader-Ofer seats for each row of `votes` (draws x lists). `pairs`: surplus agreements as column pairs."""
    V = np.atleast_2d(np.asarray(votes, dtype=float))
    n, _ = V.shape
    ok = V >= threshold * V.sum(1, keepdims=True)
    Vp = np.where(ok, V, 0.0)
    tot = Vp.sum(1, keepdims=True)
    s = np.floor_divide(Vp * seats, np.where(tot > 0, tot, 1.0)).astype(np.int64)
    uv, us, live = Vp.copy(), s.copy(), ok.copy()
    valid = []
    for a, b in pairs:                                   # an agreement counts only if both lists passed
        v = ok[:, a] & ok[:, b]
        uv[v, a] += Vp[v, b]
        us[v, a] += s[v, b]
        live[v, b] = False
        valid.append(v)
    left = seats - s.sum(1)
    rows = np.arange(n)
    while (left > 0).any():
        g = left > 0
        q = np.where(live, uv / (us + 1), -np.inf)
        j = q.argmax(1)
        us[rows[g], j[g]] += 1
        left[g] -= 1
    out = np.where(live, us, 0)
    for (a, b), v in zip(pairs, valid):                  # split each valid pair's seats between its lists
        if not v.any():
            continue
        T, va, vb = out[v, a], Vp[v, a], Vp[v, b]
        q2 = (va + vb) / T
        sa, sb = np.floor(va / q2).astype(np.int64), np.floor(vb / q2).astype(np.int64)
        rem = T - sa - sb
        while (rem > 0).any():
            g = rem > 0
            to_a = va / (sa + 1) >= vb / (sb + 1)
            sa = sa + (g & to_a)
            sb = sb + (g & ~to_a)
            rem = rem - g
        out[v, a], out[v, b] = sa, sb
    return out


def allocate(votes: dict[str, float], agreements: list[tuple[str, str]] = (), seats: int = SEATS,
             threshold: float = THRESHOLD) -> dict[str, int]:
    """Seats per list for one result; `agreements` as pairs of list letters (pairs naming unknown lists are ignored)."""
    names = list(votes)
    ix = {k: i for i, k in enumerate(names)}
    pairs = [(ix[a], ix[b]) for a, b in agreements if a in ix and b in ix]
    s = allocate_many(np.array([[votes[k] for k in names]], dtype=float), pairs, seats, threshold)[0]
    return {k: int(x) for k, x in zip(names, s)}


# ---------------------------------------------------------------------------------------- CEC files

BOX_COLS = {"סמל ישוב": "loc", "שם ישוב": "loc_name", "קלפי": "box", "בזב": "eligible", "מצביעים": "voters",
            "פסולים": "invalid", "כשרים": "valid"}


def read_expb(path: Path | str) -> pd.DataFrame:
    """CEC ballot-box file (expb.csv; UTF-8 from 2022, Windows-1255 before). One row per counted box; list
    columns are the ballot letters."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1255"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(txt), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    if df.empty:
        return pd.DataFrame(columns=list(BOX_COLS.values()))
    lists = [c for c in df.columns[df.columns.get_loc("כשרים") + 1:] if not c.startswith("Unnamed")]
    out = df[list(BOX_COLS)].rename(columns=BOX_COLS)
    out["loc"] = out["loc"].astype(int)
    for c in ("eligible", "voters", "invalid", "valid"):
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype(np.int64)
    return pd.concat([out, df[lists].apply(pd.to_numeric, errors="coerce").fillna(0).astype(np.int64)], axis=1)


def by_locality(boxes: pd.DataFrame) -> pd.DataFrame:
    """Sum ballot boxes to localities: columns boxes, valid, eligible + one per list."""
    num = [c for c in boxes.columns if c not in ("loc", "loc_name", "box")]
    g = boxes.groupby("loc")
    out = g[num].sum()
    out.insert(0, "boxes", g.size())
    return out


def lists_of(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("boxes", "boxes_total", "valid", "eligible", "voters", "invalid")]


def national(df: pd.DataFrame) -> pd.Series:
    return df[lists_of(df)].sum()


def composition(final_loc: pd.DataFrame, min_share: float = 0.01) -> pd.DataFrame:
    """Prior composition per locality: share of each list with >= min_share nationally, the rest pooled."""
    L = lists_of(final_loc)
    nat = final_loc[L].sum()
    keep = [c for c in L if nat[c] >= min_share * nat.sum()]
    comp = final_loc[keep].copy()
    comp[OTHER] = final_loc[L].sum(axis=1) - comp.sum(axis=1)
    tot = final_loc[L].sum(axis=1).replace(0, np.nan)
    return comp.div(tot, axis=0).fillna(0.0)


# ---------------------------------------------------------------------------------------- 2026 count feed

def read_navon(obj: dict) -> tuple[pd.DataFrame, pd.Series]:
    """results.json of github.com/navonyaniv/election-il-2026 (third-party mirror of the CEC count; private
    research only). Returns (localities counted so far: boxes, boxes_total, valid + one column per list
    letter, incl. the envelopes as ENVELOPE when reported), boxes_total for every locality."""
    letters = obj["parties"]
    prog = obj.get("progress", {}).get("towns", {})
    total = pd.Series({int(k): int(v[1]) for k, v in prog.items()}, name="boxes_total", dtype=np.int64)
    rows = {}
    for k, v in (obj.get("towns") or {}).items():
        rows[int(k)] = [int(prog.get(k, [0])[0]), int(v[3])] + [int(x) for x in v[4]]
    ext = obj.get("external")
    if ext and ext[3]:
        rows[ENVELOPE] = [1, int(ext[3])] + [int(x) for x in ext[4]]
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["boxes", "valid"] + letters)
    df.index.name = "loc"
    return df, total


# ---------------------------------------------------------------------------------------- nowcast

@dataclass(frozen=True)
class Settings:
    prior_votes: float = 20_000.0       # ridge strength (in votes) toward "every group votes like the national count"
    own_votes: float = 1_000.0          # a partly counted locality's own share outweighs the regression past this
    var_votes: float = 20_000.0         # pseudo-votes behind the prior residual variance m(1-m)/4
    growth_votes: float = 20_000.0      # pseudo-votes behind growth factor 1 (same turnout as last time)
    tau: float = 0.10                   # shared error on a list's share of the uncounted votes, x sqrt(m(1-m))
    turnout_sd: float = 0.03            # log-sd of the uncounted vote total
    min_list_share: float = 0.003       # lists below this share of the count are pooled into OTHER
    draws: int = 4000


@dataclass
class Projection:
    lists: list[str]
    counted: np.ndarray                 # votes counted per list
    rem_mean: np.ndarray                # projected uncounted votes per list
    rem_sd: np.ndarray
    rem_total: float
    frac_counted: float                 # share of expected boxes counted (incl. envelopes)
    extra: dict = field(default_factory=dict)

    @property
    def mean(self) -> np.ndarray:
        return self.counted + self.rem_mean

    def shares(self) -> tuple[np.ndarray, np.ndarray]:
        tot = self.mean.sum()
        return self.mean / tot, self.rem_sd / tot


def project(units: pd.DataFrame, count: pd.DataFrame, st: Settings) -> Projection:
    """units: index loc, columns W (prior valid votes), boxes_total, comp_* (prior composition).
    count: index loc, columns boxes (counted), valid, list letters (counted votes so far)."""
    lists_all = lists_of(count)
    tot_l = count[lists_all].sum()
    big = [c for c in lists_all if tot_l[c] >= st.min_list_share * max(tot_l.sum(), 1)]
    cnt = count[big].copy()
    cnt[OTHER] = count[lists_all].sum(axis=1) - cnt.sum(axis=1)
    L = big + [OTHER]
    comp_cols = [c for c in units.columns if c.startswith("comp_")]

    U = units.reindex(units.index.union(cnt.index))
    U["W"] = U["W"].fillna(0.0)
    U["boxes_total"] = U["boxes_total"].fillna(0)
    U[comp_cols] = U[comp_cols].fillna(0.0)
    V = cnt.reindex(U.index).fillna(0.0)
    Vtot = V[L].sum(axis=1).to_numpy()
    nb = count["boxes"].reindex(U.index).fillna(0).to_numpy(float)
    bt = np.maximum(U["boxes_total"].to_numpy(float), nb)
    f = np.where(bt > 0, nb / np.where(bt > 0, bt, 1), np.where(Vtot > 0, 1.0, 0.0))
    W = U["W"].to_numpy(float)
    env = U.index.to_numpy() == ENVELOPE

    # growth: counted votes over the prior votes of the counted share of each locality
    reg = (f > 0) & (W > 0) & ~env
    g = (Vtot[reg].sum() + st.growth_votes) / ((f[reg] * W[reg]).sum() + st.growth_votes)
    g_loc = np.where(W > 0, (Vtot + st.own_votes * g) / (f * W + st.own_votes), g)
    R = np.where(W > 0, (1 - f) * W * g_loc, np.where(f > 0, Vtot * (1 - f) / np.maximum(f, 1e-9), 0.0))
    R = np.maximum(R, 0.0)

    # regression of counted shares on prior composition
    nat = V[L].sum().to_numpy(float)
    s_hat = nat / nat.sum() if nat.sum() > 0 else np.full(len(L), 1.0 / len(L))
    X = U[comp_cols].to_numpy(float)
    has_x = X.sum(1) > 0
    obs = (Vtot > 0) & has_x & ~env
    Y = np.divide(V[L].to_numpy(float), Vtot[:, None], out=np.zeros((len(U), len(L))), where=Vtot[:, None] > 0)
    w = Vtot[obs]
    K = X.shape[1]
    B0 = np.tile(s_hat, (K, 1))
    A = (X[obs] * w[:, None]).T @ X[obs] + st.prior_votes * np.eye(K)
    B = np.linalg.solve(A, (X[obs] * w[:, None]).T @ Y[obs] + st.prior_votes * B0)
    yhat = np.where(has_x[:, None], np.clip(X @ B, 0, None), s_hat[None, :])
    yhat = yhat / np.maximum(yhat.sum(1, keepdims=True), 1e-12)
    res2 = (w[:, None] * (Y[obs] - yhat[obs]) ** 2).sum(0)
    sig2 = (res2 + st.var_votes * s_hat * (1 - s_hat) / 4) / (w.sum() + st.var_votes)

    # weight left on the regression; the envelopes are counted in batches by voter category (soldiers,
    # diplomats, hospitals, ...), so their counted part says little about the rest: regression only
    a = np.where(env, 1.0, st.own_votes / (Vtot + st.own_votes))
    m = np.where((Vtot > 0)[:, None], (1 - a)[:, None] * Y + a[:, None] * yhat, yhat)
    rem = (R[:, None] * m).sum(0)
    Rt = R.sum()
    mbar = rem / Rt if Rt > 0 else s_hat
    # coefficient uncertainty of the regression part: sig2 * r' A^-1 (X'W^2X) A^-1 r, r = sum R a x
    r = ((R * a * has_x)[:, None] * X).sum(0)
    Xw = X[obs] * w[:, None]
    Ar = np.linalg.solve(A, r)
    v_param = sig2 * float(Ar @ (Xw.T @ Xw) @ Ar)
    var = sig2 * (R ** 2 * a).sum() + v_param + (st.tau * Rt) ** 2 * mbar * (1 - mbar)
    exp_boxes = bt.sum()
    return Projection(L, nat, rem, np.sqrt(var), Rt, float(nb.sum() / exp_boxes) if exp_boxes else 0.0,
                      {"growth": g, "B": B, "comp": comp_cols})


def simulate(p: Projection, st: Settings, rng: np.random.Generator) -> np.ndarray:
    """Draws x lists of final votes."""
    z = rng.standard_normal((st.draws, len(p.lists)))
    rem = np.maximum(p.rem_mean + p.rem_sd * z, 0.0)
    target = p.rem_total * np.exp(st.turnout_sd * rng.standard_normal(st.draws))
    s = rem.sum(1, keepdims=True)
    rem = np.where(s > 0, rem * (target[:, None] / np.where(s > 0, s, 1)), 0.0)
    return p.counted[None, :] + rem


def units_from(prior_loc: pd.DataFrame, boxes_total: pd.Series, min_share: float = 0.01) -> pd.DataFrame:
    """Unit frame for `project`: prior valid votes W, this election's box count, prior composition."""
    comp = composition(prior_loc, min_share).add_prefix("comp_")
    U = pd.DataFrame({"W": prior_loc[lists_of(prior_loc)].sum(axis=1)}).join(comp, how="outer")
    U = U.reindex(U.index.union(boxes_total.index))
    U["boxes_total"] = boxes_total.reindex(U.index).fillna(0).astype(float)
    U["W"] = U["W"].fillna(0.0)
    return U.fillna(0.0)


# ---------------------------------------------------------------------------------------- market outcomes

def outcomes(votes: np.ndarray, seats: np.ndarray, lists: list[str], markets: list[dict]) -> dict[str, np.ndarray]:
    """Per market id, a 0/0.5/1 array over draws for the YES side. Market kinds:
    most_seats {party}; seats_band {party, lo, hi}; bloc {a: [...], b: [...]} (YES = a ahead; tie -> more votes
    of a's passing lists; still tied -> 0.5); majority {a: [...], n}."""
    ix = {k: i for i, k in enumerate(lists)}
    out = {}
    passing = votes >= THRESHOLD * votes.sum(1, keepdims=True)
    for m in markets:
        k = m["kind"]
        if k == "most_seats":
            key = seats * 1e12 + votes                   # seats first, then votes (market tie rule)
            out[m["id"]] = (key.argmax(1) == ix[m["party"]]).astype(float) if m["party"] in ix else np.zeros(len(votes))
        elif k == "seats_band":
            s = seats[:, ix[m["party"]]] if m["party"] in ix else np.zeros(len(votes))
            out[m["id"]] = ((s >= m["lo"]) & (s <= m["hi"])).astype(float)
        elif k in ("bloc", "majority"):
            ia = [ix[x] for x in m["a"] if x in ix]
            sa = seats[:, ia].sum(1)
            if k == "majority":
                out[m["id"]] = (sa >= m["n"]).astype(float)
                continue
            ib = [ix[x] for x in m["b"] if x in ix]
            sb = seats[:, ib].sum(1)
            va = (votes[:, ia] * passing[:, ia]).sum(1)
            vb = (votes[:, ib] * passing[:, ib]).sum(1)
            out[m["id"]] = np.where(sa != sb, (sa > sb).astype(float), np.where(va != vb, (va > vb).astype(float), 0.5))
        else:
            raise ValueError(f"unknown market kind {k}")
    return out
