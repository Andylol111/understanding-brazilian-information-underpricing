"""Fit the hierarchical ecological model (brunder.origin_ei) to tract frames 2016/2020/2024 and county results 2022.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_fit.py fit        # posterior draws -> origin/fits/
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_fit.py validate   # leave-one-cycle-out + polls

Definitions
  response     y = votes / CVAP of the tract (separately for the Democratic and the Republican candidate), weight min(CVAP, 4000)
  groups (11)  mexican, puerto_rican, cuban, dominican, central_am, south_am, oth_hisp, nh_white, nh_black, nh_asian, nh_other
  variant "hisp1"  the seven Hispanic origins merged into one Hispanic group (the model most analyses use)
  areas        counties or county groups named in us_origin_frames.AREAS, plus the rest of each state (pooling units)
Outputs are written under $BRUNDER_DATA/processed/us2026/origin/fits/.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_origin_frames as F

from brunder import origin_ei as ei

OUT = F.OUT
FITS = OUT / "fits"
GROUPS = F.GROUPS
HISP = F.HISP
NH = F.NH
YEARS = (2016, 2020, 2024)
MAX_W = 4000.0
MIN_CVAP = 200.0
MAX_VOTE_RATIO = 1.3          # votes / CVAP above this is allocation or ACS noise (group quarters, tiny tracts)
VARIANTS = {"origin": GROUPS, "hisp1": ["hispanic"] + NH}


def load(year: int) -> pd.DataFrame:
    f = pd.read_parquet(OUT / f"tract_frame_{year}.parquet")
    v = f["votes_d"] + f["votes_r"]
    f = f[(f["cvap"] >= MIN_CVAP) & (v > 0) & (v / f["cvap"] <= MAX_VOTE_RATIO)].copy()
    f["c_hispanic"] = f[[f"c_{g}" for g in HISP]].sum(axis=1)
    return f.reset_index(drop=True)


def area_table(frames: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """Common area index across cycles: area name -> id, state id."""
    names = sorted(set().union(*[set(f["area"]) for f in frames.values()]))
    t = pd.DataFrame({"area": names})
    first = {}
    for f in frames.values():
        first.update(f.drop_duplicates("area").set_index("area")["state_fips"].to_dict())
    t["state_fips"] = t["area"].map(first)
    t["state"] = pd.factorize(t["state_fips"])[0]
    return t


def design(f: pd.DataFrame, groups: list[str], areas: pd.DataFrame):
    C = f[[f"c_{g}" for g in groups]].to_numpy(float)
    X = C / C.sum(axis=1, keepdims=True)
    cv = C.sum(axis=1)
    w = np.minimum(cv, MAX_W)
    area = f["area"].map(dict(zip(areas["area"], range(len(areas))))).to_numpy()
    yd, yr = f["votes_d"].to_numpy() / cv, f["votes_r"].to_numpy() / cv
    return X, w, area, yd, yr, C


def group_cvap_by_area(f: pd.DataFrame, groups: list[str], areas: pd.DataFrame) -> np.ndarray:
    g = f.groupby("area")[[f"c_{x}" for x in groups]].sum().reindex(areas["area"]).fillna(0.0)
    return g.to_numpy()


# Relative turnout of each group, CPS reported turnout of citizens (Census P20-585 2020 and P20-590 2024 reports, Table 2/4a):
# group rate / all-citizen rate.  2016: all 61.4, W 65.3, B 59.4, A 49.0, H 47.6; 2020: 66.8, 71.0, 63.0, 59.0, 53.7;
# 2024: 65.3, 70.5, 59.6, 57.1, 50.6.  nh_other (AIAN, multiracial, ...) is set between Black and Asian.
CPS = {2016: (61.4, 65.3, 59.4, 49.0, 47.6), 2020: (66.8, 71.0, 63.0, 59.0, 53.7), 2024: (65.3, 70.5, 59.6, 57.1, 50.6)}
TURN_SD = {"white": 0.08, "black": 0.08, "asian": 0.08, "hisp": 0.12, "other": 0.12}


def turnout_prior(year: int, groups: list[str], cvap_ak: np.ndarray, votes_a: np.ndarray, scale: float = 1.0):
    """(R, K) prior mean and sd of group turnout (D+R votes per CVAP): CPS ratio x the area's own turnout, normalised so the
    prior mean reproduces the area's total two-party votes.  scale multiplies the sd (sensitivity runs)."""
    allr, w, b, a, h = CPS[year]
    rho_kind = {"nh_white": ("white", w), "nh_black": ("black", b), "nh_asian": ("asian", a), "nh_other": ("other", 0.5 * (b + a)),
                }
    rho = np.empty(len(groups))
    sd = np.empty(len(groups))
    for k, g in enumerate(groups):
        kind, rate = rho_kind.get(g, ("hisp", h))
        rho[k] = rate / allr
        sd[k] = TURN_SD[kind]
    denom = cvap_ak @ rho
    t0 = rho[None, :] * (votes_a / np.where(denom > 0, denom, 1.0))[:, None]
    return np.clip(t0, 0.02, 0.98), np.broadcast_to(sd * scale, t0.shape)


DELTA_H = 0.09       # prior sd of an origin group's vote rate around its area's Hispanic rate (beta units: votes per CVAP)
DELTA_NH = 0.03      # non-Hispanic groups are held near their stage-1 values


def fit_year(f: pd.DataFrame, areas: pd.DataFrame, variant: str, seed: int, year: int, n_draws=300, burn=400, thin=2,
             turn_scale: float | None = 1.0, context: bool = False, centre: dict | None = None) -> dict:
    groups = VARIANTS[variant]
    X, w, area, yd, yr, C = design(f, groups, areas)
    R = len(areas)
    Xr = X
    if context:                                              # concentration terms: a group's rate varies with its tract share
        Z, _ = ei.context_terms(X, C.sum(axis=1), area, R)
        Xr = np.hstack([X, Z])
    sd = ei.suff_stats(Xr, yd, w, area, R)
    sr = ei.suff_stats(Xr, yr, w, area, R)
    cv_ak = group_cvap_by_area(f, groups, areas)
    votes_a = (f.groupby("area")[["votes_d", "votes_r"]].sum().sum(axis=1).reindex(areas["area"]).fillna(0.0)).to_numpy()
    kw = {}
    if turn_scale is not None:
        t0, tsd = turnout_prior(year, groups, cv_ak, votes_a, turn_scale)
        kw = {"turn_mean": t0, "turn_sd": tsd}
    if centre is not None:                                    # stage 2: origin groups centred on the area's stage-1 Hispanic rate
        is_h = np.array([g in HISP for g in groups])
        j_nh = [centre["groups"].index(g) for g in groups if g not in HISP]
        pd_, pr_ = np.empty((R, len(groups))), np.empty((R, len(groups)))
        pd_[:, is_h], pr_[:, is_h] = centre["bd"][:, [0]], centre["br"][:, [0]]
        pd_[:, ~is_h], pr_[:, ~is_h] = centre["bd"][:, j_nh], centre["br"][:, j_nh]
        kw.update(prior_d=pd_, prior_r=pr_, prior_sd=np.where(is_h, DELTA_H, DELTA_NH)[None, :] * np.ones((R, 1)))
    r = ei.fit(sd, sr, areas["state"].to_numpy(), n_beta=len(groups), seed=seed, n_draws=n_draws, burn=burn, thin=thin, **kw)
    return {"bd": r["bd"].astype(np.float32), "br": r["br"].astype(np.float32), "groups": groups, "cvap_ak": cv_ak,
            "gd": r["gd"].astype(np.float32), "gr": r["gr"].astype(np.float32), "n_tracts": sd.n, "mu_d": r["mu"][:, 0], "tau_d": r["tau"][:, 0], "kappa_d": r["kappa"][:, 0]}


SPECS = {"plain": {"context": False}, "ctx": {"context": True}}


def fit_all(n_draws=300, burn=400, years=YEARS, specs=tuple(SPECS)) -> None:
    FITS.mkdir(parents=True, exist_ok=True)
    frames = {y: load(y) for y in YEARS if (OUT / f"tract_frame_{y}.parquet").exists()}
    areas = area_table(frames)
    areas.to_csv(FITS / "areas.csv", index=False)
    for y in years:
        for sp in specs:
            cen = None
            for v in ("hisp1", "origin"):
                t0 = time.time()
                r = fit_year(frames[y], areas, v, seed=100 * y + len(v) + 7 * len(sp), year=y, n_draws=n_draws, burn=burn,
                             centre=cen, **SPECS[sp])
                np.savez_compressed(FITS / f"fit_{y}_{sp}_{v}.npz", bd=r["bd"], br=r["br"], gd=r["gd"], gr=r["gr"],
                                    groups=np.array(r["groups"]), cvap_ak=r["cvap_ak"], n_tracts=r["n_tracts"], mu_d=r["mu_d"],
                                    tau_d=r["tau_d"], kappa_d=r["kappa_d"])
                if v == "hisp1":
                    cen = {"groups": r["groups"], "bd": np.median(r["bd"], axis=0), "br": np.median(r["br"], axis=0)}
                print(y, sp, v, f"{time.time() - t0:.0f}s", "tracts", len(frames[y]), flush=True)


def load_fit(y: int, sp: str, v: str) -> dict:
    z = np.load(FITS / f"fit_{y}_{sp}_{v}.npz", allow_pickle=False)
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fit":
        fit_all()
