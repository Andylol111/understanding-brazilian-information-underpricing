"""Hierarchical ecological regression of party votes on group shares of the voting-age citizen population.

For one election and one party, tract (or county) i in area a has votes per citizen of voting age
    y_i = sum_k x_ik * beta_ak + e_i,   e_i ~ N(0, s2_a / w_i)
where x_ik is group k's share of the unit's CVAP (shares add to 1: no intercept) and w_i is a weight (CVAP, capped).
beta_ak is group k's votes per group citizen, in [0, 1].  Run for the Democratic and the Republican vote, the two-party
preference of group k is  beta^D_k / (beta^D_k + beta^R_k)  and its turnout (of CVAP) is  beta^D_k + beta^R_k.
This double regression (Goodman) keeps turnout differences between groups out of the preference estimate.

Pooling (the "region effects"):  beta_ak ~ N(m_s(a)k, kappa_k^2)   area within state
                                  m_sk   ~ N(mu_k, tau_k^2)         state within nation
                                  mu_k   ~ N(0.3, 0.3^2)            all truncated to [0, 1]
kappa_k^2, tau_k^2 ~ InvGamma(2, 0.005); s2_a ~ InvGamma(3, 2*pilot), separately for D and R.
Optional turnout prior: beta^D_ak + beta^R_ak ~ N(t0_ak, sd_t^2), t0 from external group turnout ratios (CPS) times the
area's observed turnout.  Without it collinear groups (Hispanic, Black and other minorities share tracts) get turnout
estimates above 100 % and a preference ratio that is extrapolated, not measured.  So a group with little geographic support in an
area borrows from its state and the nation instead of being estimated from noise.  The truncation constants of the
hierarchical priors are ignored (standard for truncated hierarchical regressions).

Everything is computed from per-area sufficient statistics (X'WX, X'Wy, y'Wy), so a sweep costs O(areas x K^2) and does not
touch the units.  Pure numpy/scipy.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import truncnorm


@dataclass
class Suff:
    """Per-area sufficient statistics of the weighted regression."""
    A: np.ndarray      # (R, K, K)  sum w x x'
    b: np.ndarray      # (R, K)     sum w x y
    c: np.ndarray      # (R,)       sum w y^2
    n: np.ndarray      # (R,)       number of units
    W: np.ndarray      # (R,)       sum w


def suff_stats(X: np.ndarray, y: np.ndarray, w: np.ndarray, area: np.ndarray, n_areas: int) -> Suff:
    """X (N,K) group shares, y (N,) response, w (N,) weights, area (N,) int in [0, n_areas)."""
    K = X.shape[1]
    A = np.zeros((n_areas, K, K))
    b = np.zeros((n_areas, K))
    c = np.zeros(n_areas)
    n = np.bincount(area, minlength=n_areas).astype(float)
    W = np.bincount(area, weights=w, minlength=n_areas)
    wx = X * w[:, None]
    for k in range(K):
        b[:, k] = np.bincount(area, weights=wx[:, k] * y, minlength=n_areas)
        for j in range(k, K):
            A[:, k, j] = A[:, j, k] = np.bincount(area, weights=wx[:, k] * X[:, j], minlength=n_areas)
    c = np.bincount(area, weights=w * y * y, minlength=n_areas)
    return Suff(A, b, c, n, W)


def _tn(mean: np.ndarray, sd: np.ndarray, lo: float, hi: float, rng: np.random.Generator) -> np.ndarray:
    a = (lo - mean) / sd
    bb = (hi - mean) / sd
    out = truncnorm.rvs(a, bb, loc=mean, scale=sd, random_state=rng)
    return np.clip(out, lo, hi)


def _invgamma(shape: np.ndarray | float, scale: np.ndarray | float, rng: np.random.Generator) -> np.ndarray:
    return scale / rng.gamma(shape, 1.0, size=np.shape(scale))


def pilot_variance(s: Suff) -> float:
    """Pooled pointwise residual variance (weights included) of an unpooled-by-area, ridge-stabilised WLS fit."""
    K = s.A.shape[1]
    rss = 0.0
    dof = 0.0
    for a in range(len(s.n)):
        if s.n[a] < K + 1:
            continue
        beta = np.linalg.solve(s.A[a] + 1e-6 * np.trace(s.A[a]) / K * np.eye(K), s.b[a])
        rss += s.c[a] - 2 * beta @ s.b[a] + beta @ s.A[a] @ beta
        dof += s.n[a] - K
    return max(rss / max(dof, 1.0), 1e-9)


def context_terms(X: np.ndarray, cvap: np.ndarray, area: np.ndarray, n_areas: int) -> tuple[np.ndarray, np.ndarray]:
    """Concentration terms z_ik = x_ik (x_ik - e_ak), e_ak = CVAP-weighted mean share of group k seen by group k in area a.

    Adding z to the regressors lets a group's vote rate vary linearly with how concentrated it is in the tract
    (rate_ik = beta_ak + gamma_ak (x_ik - e_ak)).  Because sum_i C_ik (x_ik - e_ak) = 0, beta_ak stays the group's average vote
    rate over the area, so group totals and preferences are read from beta alone.  Returns (Z, e) with e (R, K)."""
    C = X * cvap[:, None]
    num = np.zeros((n_areas, X.shape[1]))
    den = np.zeros_like(num)
    for k in range(X.shape[1]):
        num[:, k] = np.bincount(area, weights=C[:, k] * X[:, k], minlength=n_areas)
        den[:, k] = np.bincount(area, weights=C[:, k], minlength=n_areas)
    e = np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)
    return X * (X - e[area]), e


def fit(sd: Suff, sr: Suff, state: np.ndarray, n_beta: int | None = None, n_draws: int = 400, burn: int = 300, thin: int = 2,
        seed: int = 0, turn_mean: np.ndarray | None = None, turn_sd: float | np.ndarray = 0.08,
        prior_d: np.ndarray | None = None, prior_r: np.ndarray | None = None, prior_sd: float | np.ndarray = 0.05,
        gamma_bound: float = 0.6) -> dict:
    """Joint posterior draws of the Democratic and Republican coefficients.

    sd, sr     sufficient statistics of the D and R regressions (same regressors, same areas); the first n_beta regressors are the
               group shares (coefficients in [0, 1]), any further ones are concentration terms (coefficients in +-gamma_bound).
    state      (R,) int: parent state of each area.
    turn_mean  optional (R, n_beta) prior mean of group turnout beta^D + beta^R, sd turn_sd (float or (R, n_beta)): anchors the level
               of turnout (e.g. from CPS turnout ratios) so the D/R split is not extrapolated from collinear shares.
    prior_d / prior_r  optional (R, n_beta) informative centres with sd prior_sd that REPLACE the state/nation prior of beta (carrying a
               fit forward to a cycle with few units per area); concentration terms keep the hierarchy, hyper-parameters of beta are
               then not updated.
    Returns bd, br (S, R, n_beta), gd, gr (S, R, G), hyper-parameter draws.
    """
    rng = np.random.default_rng(seed)
    R, D = sd.b.shape
    K = D if n_beta is None else n_beta
    G = D - K
    S = int(state.max()) + 1
    lo = np.concatenate([np.zeros(K), np.full(G, -gamma_bound)])
    hi = np.concatenate([np.ones(K), np.full(G, gamma_bound)])
    lo2, hi2 = np.tile(lo, 2), np.tile(hi, 2)
    pil = np.array([pilot_variance(sd), pilot_variance(sr)])
    b_s2 = 2.0 * pil
    mu = np.zeros((2, D))
    mu[:, :K] = 0.15
    mu0 = mu.copy()
    mu_sd2 = np.concatenate([np.full(K, 0.09), np.full(G, 0.04)])
    m = np.tile(mu[:, None, :], (1, S, 1))                     # (2, S, D)
    kappa2 = np.full((2, D), 0.01)
    tau2 = np.full((2, D), 0.01)
    s2 = np.tile(pil[:, None], (1, R))                         # (2, R)
    A2 = np.zeros((R, 2 * D, 2 * D))
    A2[:, :D, :D] = sd.A
    A2[:, D:, D:] = sr.A
    b2 = np.concatenate([sd.b, sr.b], axis=1)                  # (R, 2D)
    c2 = np.stack([sd.c, sr.c])
    n = sd.n
    has = n > 0
    fixed_prior = prior_d is not None
    if turn_mean is not None:
        ts = np.broadcast_to(np.asarray(turn_sd, float), (R, K))
        w_t = 1.0 / np.square(ts)
        Tq = np.zeros((R, 2 * D, 2 * D))
        idx = np.arange(K)
        for a_, b_ in ((idx, idx), (idx + D, idx + D), (idx, idx + D), (idx + D, idx)):
            Tq[:, a_, b_] = w_t
        Th = np.zeros((R, 2 * D))
        Th[:, :K] = turn_mean * w_t
        Th[:, D:D + K] = turn_mean * w_t
    beta = np.zeros((R, 2 * D))
    beta[:, :K] = beta[:, D:D + K] = 0.15
    keep, hyper = [], []
    eye = np.eye(2 * D)
    for it in range(burn + n_draws * thin):
        pc = np.concatenate([m[0][state], m[1][state]], axis=1)                       # (R, 2D)
        pp = np.concatenate([np.broadcast_to(1.0 / kappa2[0], (R, D)), np.broadcast_to(1.0 / kappa2[1], (R, D))], axis=1)
        if fixed_prior:
            pc[:, :K], pc[:, D:D + K] = prior_d, prior_r
            pp[:, :K] = pp[:, D:D + K] = np.broadcast_to(1.0 / np.square(np.asarray(prior_sd, float)), (R, K))
        sv = np.concatenate([np.repeat(s2[0][:, None], D, axis=1), np.repeat(s2[1][:, None], D, axis=1)], axis=1)   # (R, 2D)
        P = A2 / sv[:, :, None] + pp[:, :, None] * eye
        h = b2 / sv + pc * pp
        if turn_mean is not None:
            P = P + Tq
            h = h + Th
        Lc = np.linalg.cholesky(P)
        mean = np.linalg.solve(P, h[:, :, None])[:, :, 0]
        todo = np.ones(R, bool)
        for _ in range(40):
            z = rng.standard_normal((R, 2 * D))
            draw = mean + np.linalg.solve(np.swapaxes(Lc, 1, 2), z[:, :, None])[:, :, 0]
            ok = todo & ((draw >= lo2) & (draw <= hi2)).all(axis=1) & ((draw[:, :K] + draw[:, D:D + K]) <= 1).all(axis=1)
            beta[ok] = draw[ok]
            todo &= ~ok
            if not todo.any():
                break
        if todo.any():                                         # coordinate-wise truncated Gibbs for the areas the box rejected
            ti = np.where(todo)[0]
            Pt, ht, bt = P[ti], h[ti], beta[ti].copy()
            for _ in range(5):
                for j in rng.permutation(2 * D):
                    oth = np.einsum("rl,rl->r", Pt[:, j, :], bt) - Pt[:, j, j] * bt[:, j]
                    mn, sdv = (ht[:, j] - oth) / Pt[:, j, j], 1.0 / np.sqrt(Pt[:, j, j])
                    hi_j = hi2[j]
                    jb = j % D
                    if jb < K:
                        hi_j = np.minimum(hi_j, 1.0 - bt[:, (j + D) % (2 * D)])
                    bt[:, j] = _tn(mn, sdv, lo2[j], hi_j, rng)
            beta[ti] = bt
        for q in range(2):                                        # per-area residual variances
            bq = beta[:, q * D:(q + 1) * D]
            Aq = A2[:, q * D:(q + 1) * D, q * D:(q + 1) * D]
            rss = np.maximum(c2[q] - 2 * np.einsum("rk,rk->r", bq, b2[:, q * D:(q + 1) * D]) + np.einsum("rk,rkj,rj->r", bq, Aq, bq), 0.0)
            s2[q] = np.where(has, _invgamma(3.0 + n / 2, b_s2[q] + rss / 2, rng), b_s2[q] / 3.0)
        cnt = np.bincount(state, minlength=S).astype(float)
        for q in range(2):                                        # hierarchy: state means, national means, spreads
            bq = beta[:, q * D:(q + 1) * D]
            for k in range(D):
                if fixed_prior and k < K:
                    continue
                sb = np.bincount(state, weights=bq[:, k], minlength=S)
                prec = cnt / kappa2[q, k] + 1.0 / tau2[q, k]
                mn = (sb / kappa2[q, k] + mu[q, k] / tau2[q, k]) / prec
                m[q, :, k] = _tn(mn, 1.0 / np.sqrt(prec), lo[k], hi[k], rng)
                precm = S / tau2[q, k] + 1.0 / mu_sd2[k]
                mnm = (m[q, :, k].sum() / tau2[q, k] + mu0[q, k] / mu_sd2[k]) / precm
                mu[q, k] = float(_tn(np.array([mnm]), np.array([1.0 / np.sqrt(precm)]), lo[k], hi[k], rng).reshape(-1)[0])
                kappa2[q, k] = np.clip(_invgamma(2.0 + R / 2, 0.005 + ((bq[:, k] - m[q, state, k]) ** 2).sum() / 2, rng), 1e-6, 1.0)
                tau2[q, k] = np.clip(_invgamma(2.0 + S / 2, 0.005 + ((m[q, :, k] - mu[q, k]) ** 2).sum() / 2, rng), 1e-6, 1.0)
        if it >= burn and (it - burn) % thin == 0:
            keep.append(beta.copy())
            hyper.append((mu.copy(), np.sqrt(tau2), np.sqrt(kappa2)))
    B = np.array(keep)
    return {"bd": B[:, :, :K], "br": B[:, :, D:D + K], "gd": B[:, :, K:D], "gr": B[:, :, D + K:],
            "mu": np.array([h[0] for h in hyper]), "tau": np.array([h[1] for h in hyper]),
            "kappa": np.array([h[2] for h in hyper]), "pilot": pil}


def preference(beta_d: np.ndarray, beta_r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two-party Democratic preference and turnout (of CVAP) draws from beta^D, beta^R draws (same shape)."""
    t = beta_d + beta_r
    return np.where(t > 1e-9, beta_d / np.where(t > 1e-9, t, 1.0), np.nan), t


def summarise(draws: np.ndarray, level: float = 0.90) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Median, lower, upper of draws along axis 0 (NaN-aware)."""
    lo = (1 - level) / 2
    return (np.nanmedian(draws, axis=0), np.nanquantile(draws, lo, axis=0), np.nanquantile(draws, 1 - lo, axis=0))


def aggregate(beta_d: np.ndarray, beta_r: np.ndarray, cvap: np.ndarray, member: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Group preference and turnout of a union of areas.

    beta_*  (S, R, K) draws; cvap (R, K) group CVAP per area; member (R,) bool.  Votes are summed over areas
    (beta x CVAP), then divided: preference = D / (D + R), turnout = (D + R) / CVAP.
    """
    cv = cvap[member][None]                      # (1, r, K)
    d = (beta_d[:, member] * cv).sum(axis=1)
    r = (beta_r[:, member] * cv).sum(axis=1)
    tot = d + r
    pref = np.where(tot > 0, d / np.where(tot > 0, tot, 1), np.nan)
    turn = tot / np.where(cv.sum(axis=1) > 0, cv.sum(axis=1), 1)
    return pref, turn
