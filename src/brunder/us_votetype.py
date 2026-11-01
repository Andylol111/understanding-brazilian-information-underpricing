"""Reporting-order risk for the US county count model: an OPTION on top of `brunder.us.project` (nothing in `brunder.us` /
`brunder.nowcast` is changed; the registered v1 / v3 / unified models run exactly as before and this layer post-processes their output).

Why. `brunder.nowcast` projects a county's uncounted votes from the county's counted votes (persistence rho) and the state swing. When
the votes still out are of a different TYPE than the votes already counted (early / mail posted first and Election Day later, or the
reverse in PA and MI where mail is counted last), the counted part and the remaining part lean differently even inside one county, and
the count-based sd does not know it. docs/us_real_nights.md shows the consequence: in 2020 the 90 % bands held about 40 % of the time.

Method (docs/us_votetype.md). Two vote types per county: "early" (the feed's early / absentee / advance split) and "late" (the rest).
From PAST cycles only (`fit_profile`, always fitted without the cycle that is tested):
  pi_c   share of the county's final votes that are early;
  g_c    early-minus-late Democratic two-party share inside the county (county value shrunk to the state's, k_gap pseudo-votes);
  rho_s  the state's counting order: (early share of the counted votes at counted fraction f) / (final early share), 10 bins.
At a count state (`adjust`), per county with counted votes n, early part e and model-remaining votes R (E = n + R):
  q    early share of the counted votes: the feed's split when it reports one (e > 0), else the state's order pi_c * rho_s(f);
  r_e  early share of the remaining votes = clip(pi_c E - e, 0, R) / R;
  g    county gap: the historical gap combined with the gap seen in this night's own counted early and late votes;
  The nowcast carries a fraction rho of the county's counted deviation (which contains the mix effect (q - pi) g) into the remaining
  votes and moves the state swing by the weighted mean M of the counted mix effects. The remaining votes' true mix effect is (r_e - pi) g,
  so the remaining Democratic share is shifted by
        shift_c = lam * [ (r_e - pi) g - rho (q - pi) g - (1 - rho) w M ]          (w = the weight the swing carries on counted votes)
  and the projection's uncertainty is widened, margin sd^2 -> sd^2 + (kappa * sum_c 2 R_c |shift_c / lam|)^2, the exposure to
  the vote type still out (zero when the types are not expected to lean differently or none is out).
States without a profile get shift 0 and no widening. lam and kappa are fitted by leave-one-cycle-out (analysis/us_votetype.py).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us

BINS = np.linspace(0.0, 1.0, 11)
MIDS = (BINS[:-1] + BINS[1:]) / 2
MIN_EARLY = 0.05           # a night's feed split is "usable" when the final early share is in [MIN_EARLY, MAX_EARLY]
MAX_EARLY = 0.98


@dataclass(frozen=True)
class Params:
    lam: float = 1.0            # weight of the shift (fitted)
    kappa: float = 0.0          # widening: margin sd gets + (kappa * exposure) in quadrature (fitted)
    k_gap: float = 20_000.0     # pseudo-votes shrinking a county's historical gap to the state's
    k_hist: float = 20_000.0    # pseudo-votes of a county's prior gap against the gap seen in its own counted early / late votes
    k_state: float = 100_000.0  # pseudo-votes of the state's historical gap against the gap seen tonight across counties
    k_psi: float = 50_000.0     # pseudo early votes holding tonight's early-share scale (vs history) at 1


DEFAULT = Params()


@dataclass(frozen=True, eq=False)
class Profile:
    """Historical vote-type profile of one state (fitted on cycles other than the one being projected)."""
    pi: pd.Series               # unit_id -> early share of the county's final votes
    gap: pd.Series              # unit_id -> early minus late Dem share in the county, shrunk to the state
    pi_state: float
    gap_state: float
    order: tuple[float, ...]    # rho_s at MIDS; () = no counting-order information
    n_cycles: int


def usable(frames: pd.DataFrame) -> bool:
    """A night's frames carry a usable early split: early + late Dem/Rep both present at the last frame and a plausible early share."""
    last = frames[frames["ts"] == frames["ts"].max()]
    if last["ed"].isna().all() or last["er"].isna().all():
        return False
    tot = float((last["d"] + last["r"]).sum())
    early = float((last["ed"].fillna(0) + last["er"].fillna(0)).sum())
    return tot > 0 and MIN_EARLY <= early / tot <= MAX_EARLY


def _night_stats(frames: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """County final values of one night and its order-curve numerator / denominator per bin."""
    last = frames[frames["ts"] == frames["ts"].max()].set_index("unit_id")
    E = last["d"] + last["r"]
    ed, er = last["ed"].fillna(0.0), last["er"].fillna(0.0)
    e = ed + er
    ld, lr = last["d"] - ed, last["r"] - er
    fin = pd.DataFrame({"E": E, "e": e, "ed": ed, "er": er, "ld": ld, "lr": lr})
    pi = (e / E.where(E > 0)).fillna(0.0)
    num, den = np.zeros(len(MIDS)), np.zeros(len(MIDS))
    f = frames.merge(E.rename("Ef"), left_on="unit_id", right_index=True).merge(pi.rename("pi"), left_on="unit_id", right_index=True)
    n = f["d"] + f["r"]
    ok = (f["Ef"] > 0) & (n > 0)
    ff = (n / f["Ef"].where(f["Ef"] > 0)).clip(0, 1)
    b = np.clip(np.digitize(ff.to_numpy(), BINS[1:-1]), 0, len(MIDS) - 1)
    ee = (f["ed"].fillna(0) + f["er"].fillna(0)).to_numpy()
    for k in range(len(MIDS)):
        m = ok.to_numpy() & (b == k)
        num[k], den[k] = ee[m].sum(), (f["pi"].to_numpy()[m] * n.to_numpy()[m]).sum()
    return fin, num, den


def _colmean(x: np.ndarray) -> np.ndarray:
    """Column means over the finite entries (NaN where a column has none)."""
    ok = np.isfinite(x)
    return np.where(ok.any(axis=0), np.where(ok, x, 0.0).sum(axis=0) / np.maximum(ok.sum(axis=0), 1), np.nan)


def _wmean(frames: list[pd.Series], w: list[float]) -> pd.Series:
    """Weighted mean over the frames in which an index is present."""
    X = pd.concat(frames, axis=1, keys=range(len(frames)))
    W = pd.DataFrame({i: X[i].notna() * w[i] for i in range(len(w))})
    return (X.fillna(0.0) * W).sum(axis=1) / W.sum(axis=1)


def fit_profile(cycles: dict[int, list[pd.DataFrame]], k_gap: float = Params.k_gap, weights: dict[int, float] | None = None) -> Profile | None:
    """Profile of one state from its past nights, `cycles` = {cycle: [night frames]}; night frames have columns ts, unit_id, d, r, ed, er
    (cumulative per county and frame; ed / er = early Dem / Rep votes, NaN when the feed has no split). Nights without a usable split are
    ignored; None when no night has one. Cycles are weighted by `weights` (default equally; e.g. presidential years above midterms for a
    presidential night), nights of a cycle by their votes."""
    per_cycle = []
    for cyc, nights in cycles.items():
        ok = [n for n in nights if usable(n)]
        if not ok:
            continue
        stats = [_night_stats(n) for n in ok]
        fin = pd.concat([s[0] for s in stats]).groupby(level=0).sum()
        # order curve: ratio per night, averaged over the cycle's nights
        curves = [s[1] / np.where(s[2] > 0, s[2], np.nan) for s in stats]
        per_cycle.append((float((weights or {}).get(cyc, 1.0)), fin, _colmean(np.vstack(curves))))
    per_cycle = [c for c in per_cycle if c[0] > 0]
    if not per_cycle:
        return None
    wc = [c[0] for c in per_cycle]
    pis, gaps, ws = [], [], []
    for _, fin, _ in per_cycle:
        E, e = fin["E"], fin["e"]
        sE = (fin["ed"] / (fin["ed"] + fin["er"]).where((fin["ed"] + fin["er"]) > 0))
        sL = (fin["ld"] / (fin["ld"] + fin["lr"]).where((fin["ld"] + fin["lr"]) > 0))
        w = (e * (E - e) / E.where(E > 0)).fillna(0.0)
        g = (sE - sL)
        good = g.notna() & (w > 0)
        gs = float((g[good] * w[good]).sum() / w[good].sum()) if good.any() else 0.0
        gaps.append(((w * g.fillna(0.0) + k_gap * gs) / (w + k_gap)).where(good, gs))
        pis.append((e / E.where(E > 0)).fillna(0.0))
        ws.append(w)
    pi, gap = _wmean(pis, wc), _wmean(gaps, wc)
    wsum = sum(w.reindex(gap.index).fillna(0.0) * x for w, x in zip(ws, wc))
    pi_state = sum(x * f["e"].sum() / f["E"].sum() for x, (_, f, _) in zip(wc, per_cycle)) / sum(wc)
    gstate = float((gap * wsum).sum() / wsum.sum()) if wsum.sum() > 0 else 0.0
    curves = np.vstack([c[2] for c in per_cycle])
    curve = np.array([np.average(curves[~np.isnan(curves[:, k]), k], weights=np.array(wc)[~np.isnan(curves[:, k])]) if (~np.isnan(curves[:, k])).any() else np.nan
                      for k in range(len(MIDS))])
    if np.isnan(curve).all():
        order: tuple[float, ...] = ()
    else:                                    # fill bins without data from the nearest bin that has some
        idx = np.arange(len(curve))
        good = ~np.isnan(curve)
        order = tuple(float(x) for x in np.interp(idx, idx[good], curve[good]))
    return Profile(pi=pi, gap=gap, pi_state=float(pi_state), gap_state=gstate, order=order, n_cycles=len(per_cycle))


def _order_ratio(profile: Profile, f: np.ndarray) -> np.ndarray:
    if not profile.order:
        return np.ones_like(f)
    x = np.r_[MIDS, 1.0]
    y = np.r_[np.asarray(profile.order), 1.0]
    return np.interp(np.clip(f, 0, 1), x, y)


def terms(units: pd.DataFrame, profile: Profile, rho: float, w_swing: float, kappa_nowcast: float, p: Params) -> pd.DataFrame:
    """Per county: the unscaled remaining-share shift (lam = 1) and its parts. `units` is the second output of brunder.us.project."""
    n, a = units["n"].to_numpy(float), units["a"].to_numpy(float)
    ne, ae = units["ne"].to_numpy(float), units["ae"].to_numpy(float)
    R = units["rem"].to_numpy(float)
    pi = units["unit_id"].map(profile.pi).fillna(profile.pi_state).to_numpy(float)
    gh = units["unit_id"].map(profile.gap).fillna(profile.gap_state).to_numpy(float)
    Ef = n + R
    f = np.where(Ef > 0, n / np.where(Ef > 0, Ef, 1.0), 0.0)
    seen = ne > 0                                           # the feed reports an early split for the county
    mature = seen & (f >= 0.9)                              # counties nearly in: their early share is (close to) final
    psi = (float(ne[mature].sum()) + p.k_psi) / (float((pi * n)[mature].sum()) + p.k_psi)     # tonight's early share vs the history's
    pi = np.clip(psi * pi, 0, 1)
    q = np.where(seen, ne / np.where(n > 0, n, 1.0), np.clip(pi * _order_ratio(profile, f), 0, 1))
    q = np.where(n > 0, np.clip(q, 0, 1), 0.0)
    e = q * n
    Re = np.clip(pi * Ef - e, 0, R)
    r_e = np.where(R > 0, Re / np.where(R > 0, R, 1.0), pi)
    late = n - ne
    ok = seen & (late > 0) & (ne < n)
    g_obs = np.where(ok, ae / np.where(ne > 0, ne, 1.0) - (a - ae) / np.where(late > 0, late, 1.0), 0.0)
    w_obs = np.where(ok, ne * late / np.where(n > 0, n, 1.0), 0.0)
    # the vote-type lean gap differs a lot between cycles (GA 2020 0.10, 2024 0.015): tonight's own early / late counts dominate the state's
    # gap as soon as counties have both types counted; the history sets the prior and each county's departure from the state's gap
    W_obs = float(w_obs.sum())
    g_state = (float((w_obs * g_obs).sum()) + p.k_state * profile.gap_state) / (W_obs + p.k_state)
    g_prior = g_state + (gh - profile.gap_state)
    g = (p.k_hist * g_prior + w_obs * g_obs) / (p.k_hist + w_obs)
    mix = (q - pi) * g * (n > 0)
    W = float(n.sum())
    M = float((n * mix).sum() / W) if W > 0 else 0.0
    shr = W / (W + kappa_nowcast)
    shift = (r_e - pi) * g - rho * mix - (1 - rho) * w_swing * shr * M
    return pd.DataFrame({"unit_id": units["unit_id"].to_numpy(), "shift": shift, "r_e": r_e, "q": q, "g": g, "R": R})


def adjust(res: dict, units: pd.DataFrame, profile: Profile | None, rho: float, kappa_nowcast: float, p: Params = DEFAULT) -> tuple[dict, pd.DataFrame]:
    """Reporting-order adjustment of one `brunder.us.project` output: shifted remaining margin, widened sd. profile None -> unchanged."""
    out = dict(res)
    out.update(vt_shift_pts=0.0, vt_exposure=0.0)
    if profile is None:
        return out, units
    w_swing = 1.0 - float(res.get("swing_prior_weight", 0.0))
    t = terms(units, profile, rho, w_swing, kappa_nowcast, p)
    rem = units["rem"].to_numpy(float)
    rem_margin = units["rem_margin"].to_numpy(float)
    s_rem = np.where(rem > 0, (rem_margin / np.where(rem > 0, rem, 1.0) + 1) / 2, 0.5)
    s_new = np.clip(s_rem + p.lam * t["shift"].to_numpy(), 0, 1)
    new_margin = np.where(rem > 0, rem * (2 * s_new - 1), 0.0)
    total = float(units["n"].sum() + rem.sum())
    dm = float((new_margin - rem_margin).sum())
    expo = float((2 * rem * np.abs(t["shift"].to_numpy())).sum())
    sd = float(np.sqrt(res["margin_sd"] ** 2 + (p.kappa * expo) ** 2))
    m = res["margin_hat"] + dm
    out.update(margin_hat=m, margin_sd=sd, p_win=float(norm.cdf(m / sd)) if sd > 0 else float(m > 0),
               share_hat=0.5 + m / (2 * total) if total else np.nan, share_sd=sd / (2 * total) if total else np.nan,
               vt_shift_pts=100 * dm / (2 * total) if total else 0.0, vt_exposure=expo / (2 * total) if total else 0.0)
    return out, units.assign(rem_margin=new_margin)


def project(rows: pd.DataFrame, cfg: dict, pri: pd.DataFrame, profile: Profile | None, p: Params = DEFAULT) -> tuple[dict, pd.DataFrame]:
    """brunder.us.project + the reporting-order adjustment (the model's own rho and kappa)."""
    res, units = us.project(rows, cfg, pri)
    pm = us.params(cfg)
    return adjust(res, units, profile, pm.rho, pm.kappa, p)


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)
