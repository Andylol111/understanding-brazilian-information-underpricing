"""Estimates table, leave-one-cycle-out test and survey / poll comparison for the origin-group model.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_report.py estimates   # region x group x cycle table
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_report.py loco        # leave-one-cycle-out prediction
  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_report.py compare      # CES and published polls

Everything reads the posterior draws written by analysis/us_origin_fit.py (processed/us2026/origin/fits/).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtri

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_origin_fit as M
import us_origin_frames as F

from brunder import origin_ei as ei

OUT = F.OUT
LABEL = {"mexican": "Mexican", "puerto_rican": "Puerto Rican", "cuban": "Cuban", "dominican": "Dominican",
         "central_am": "Central American", "south_am": "South American", "oth_hisp": "Other Hispanic",
         "nh_white": "White non-Hispanic",
         "nh_black": "Black non-Hispanic", "nh_asian": "Asian non-Hispanic", "nh_other": "Other non-Hispanic",
         "hispanic": "Hispanic (all origins)"}
STATE_NAME = {"01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California", "08": "Colorado",
              "09": "Connecticut", "10": "Delaware", "11": "District of Columbia", "12": "Florida", "13": "Georgia", "15": "Hawaii",
              "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana",
              "23": "Maine", "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota", "28": "Mississippi",
              "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada", "33": "New Hampshire", "34": "New Jersey",
              "35": "New Mexico", "36": "New York", "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma",
              "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
              "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont", "51": "Virginia", "53": "Washington",
              "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming"}
COMBINED = {
    "South Florida (Miami-Dade, Broward, Palm Beach)": ["Miami-Dade", "Broward", "Palm Beach"],
    "Florida I-4 (Orlando, Tampa Bay)": ["Orlando (Orange/Osceola)", "Tampa Bay (Hillsborough/Pinellas)"],
    "Texas border (Rio Grande Valley, El Paso, Laredo)": ["Rio Grande Valley (Hidalgo/Cameron/Starr/Willacy)", "El Paso",
                                                          "Webb (Laredo)"],
    "New York - New Jersey metro": ["New York City", "Long Island (Nassau/Suffolk)", "Hudson/Passaic/Essex/Union NJ",
                                    "Middlesex/Monmouth NJ"],
}


def area_regions(areas: pd.DataFrame) -> dict[str, np.ndarray]:
    """Region name -> boolean mask over areas: the contiguous US, each state, each named area, the combined regions."""
    names = areas["area"].to_numpy()
    out = {"United States (contiguous)": np.ones(len(areas), bool)}
    for fips, nm in STATE_NAME.items():
        m = (areas["state_fips"] == fips).to_numpy()
        if m.any():
            out[nm] = m
    for a in names:
        if a.startswith("rest-"):
            out[f"Rest of {STATE_NAME.get(a[5:], a[5:])}"] = names == a
        else:
            out[a] = names == a
    for nm, parts in COMBINED.items():
        out[nm] = np.isin(names, parts)
    return out


def group_draws(fit: dict, mask: np.ndarray, members: list[int]) -> tuple[np.ndarray, np.ndarray, float]:
    """Two-party Democratic preference and turnout draws of the union of `members` (group columns) over the areas in `mask`."""
    cv = fit["cvap_ak"][mask][:, members]
    d = (fit["bd"][:, mask][:, :, members].astype(float) * cv[None]).sum(axis=(1, 2))
    r = (fit["br"][:, mask][:, :, members].astype(float) * cv[None]).sum(axis=(1, 2))
    tot = d + r
    return np.where(tot > 0, d / np.where(tot > 0, tot, 1), np.nan), tot / max(cv.sum(), 1e-9), float(cv.sum())


def estimates(years=(2016, 2020, 2022, 2024), spec: str = "plain", level: float = 0.90, shift: dict | None = None,
              sigma_m: float = 0.0) -> pd.DataFrame:
    """Region x group x cycle table from the posterior draws.  d2p / d2p_lo / d2p_hi: ecological estimate and the posterior interval.
    With `shift` (cycle -> logit shift applied to Hispanic groups) and `sigma_m` (model error sd, probability scale) the table also
    carries d2p_cal and d2p_cal_lo / d2p_cal_hi: the level-aligned estimate and an interval that adds sigma_m (and the survey's own
    error in the shift) to the posterior sd, in quadrature."""
    areas = pd.read_csv(M.FITS / "areas.csv", dtype={"state_fips": str})
    regs = area_regions(areas)
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    zq = float(ndtri(hi))
    rows = []
    for y in years:
        fit = M.load_fit(y, spec, "origin")
        groups = [str(g) for g in fit["groups"]]
        sets = {g: [i] for i, g in enumerate(groups)}
        sets["hispanic"] = [i for i, g in enumerate(groups) if g in F.HISP]
        for reg, mask in regs.items():
            for g, mem in sets.items():
                pref, turn, cv = group_draws(fit, mask, mem)
                if cv < 1000:
                    continue
                rec = {"year": y, "region": reg, "group": g, "group_label": LABEL[g], "cvap": cv,
                       "d2p": np.nanmedian(pref), "d2p_lo": np.nanquantile(pref, lo), "d2p_hi": np.nanquantile(pref, hi),
                       "turnout": np.nanmedian(turn), "turnout_lo": np.nanquantile(turn, lo), "turnout_hi": np.nanquantile(turn, hi),
                       "n_tracts": int(fit["n_tracts"][mask].sum())}
                if shift is not None:
                    pc = pref if g not in ("hispanic",) + tuple(F.HISP) else 1 / (1 + np.exp(-(logit(pref) + shift[y])))
                    sd = np.sqrt(np.nanvar(pc) + sigma_m ** 2 + (CAL_SE ** 2 if g in ("hispanic",) + tuple(F.HISP) else 0.0))
                    med = float(np.nanmedian(pc))
                    rec.update({"d2p_cal": med, "d2p_cal_lo": max(0.0, med - zq * sd), "d2p_cal_hi": min(1.0, med + zq * sd)})
                rows.append(rec)
    return pd.DataFrame(rows)


CAL_SE = 0.02              # sd of the CES national Hispanic value the shift is aligned to (CES 2016/2020/2024: 0.014-0.020)


RESULTS = F.DATA / "processed" / "us2026" / "unified" / "results_county.parquet"


def county_frame(year: int = 2022, vint: int = 2022) -> pd.DataFrame:
    """County units for a cycle without tract-level votes: group CVAP (same construction as the tract frames) and the official county
    two-party votes of the state's top statewide contest (governor, else Senate, else the House total) that validated."""
    r = pd.read_parquet(RESULTS)
    r = r[(r["year"] == year) & (r["contest"] == r["office"]) & r["val_status"].isin(["pass", "no_ref"])]
    pick = []
    for st, g in r.groupby("st"):
        for office in ("gov", "sen", "house_total"):
            o = g[g["office"] == office]
            if len(o) >= 0.9 * g["fips"].nunique() and len(o) > 0:
                pick.append(o.assign(race=office))
                break
    r = pd.concat(pick)
    c = pd.read_parquet(OUT / f"county_b03001_{vint}.parquet")
    c = c[c["geoid"].str[:2].astype(int) <= 56].dropna(subset=["pop", "a_cvap", "h_cvap", "nh_white"]).reset_index(drop=True)
    cv = F.group_cvap(c, vint)
    f = pd.concat([c[["geoid"]], cv.add_prefix("c_")], axis=1)
    f["cvap"] = cv.sum(axis=1)
    f = f.merge(r[["fips", "dem", "rep", "race"]].rename(columns={"fips": "geoid", "dem": "votes_d", "rep": "votes_r"}), on="geoid")
    f = f[(f["cvap"] >= 200) & (f["votes_d"] + f["votes_r"] > 0)].copy()
    f["c_hispanic"] = f[[f"c_{g}" for g in F.HISP]].sum(axis=1)
    f["state_fips"] = f["geoid"].str[:2]
    f["county"] = f["geoid"]
    f["area"] = F.area_of(f["county"])
    f["year"] = year
    return f.reset_index(drop=True)


PRIOR_SD_2022 = 0.07            # sd (votes per CVAP) of the carried-forward prior of an area x group coefficient
W_CAP_COUNTY = 20000.0


def carried_forward(areas: pd.DataFrame, f22: pd.DataFrame, spec: str = "plain", sources=(2020, 2024)):
    """Prior centres (R, K) for a cycle without tract data: mean of the coefficients of `sources`, scaled per area by the ratio of
    this cycle's turnout (two-party votes per CVAP) to the sources' average turnout."""
    fits = [M.load_fit(y, spec, "origin") for y in sources]
    bd = np.mean([x["bd"].mean(axis=0) for x in fits], axis=0)
    br = np.mean([x["br"].mean(axis=0) for x in fits], axis=0)
    t_src = np.zeros(len(areas))
    for y in sources:
        g = M.load(y).groupby("area")[["votes_d", "votes_r", "cvap"]].sum().reindex(areas["area"])
        t_src += ((g["votes_d"] + g["votes_r"]) / g["cvap"]).fillna(np.nan).to_numpy() / len(sources)
    g = f22.groupby("area")[["votes_d", "votes_r", "cvap"]].sum().reindex(areas["area"])
    t_new = ((g["votes_d"] + g["votes_r"]) / g["cvap"]).to_numpy()
    ratio = np.where(np.isfinite(t_new / t_src), t_new / t_src, 1.0)
    return bd * ratio[:, None], br * ratio[:, None], ratio


def fit_county_cycle(year: int = 2022, spec: str = "plain", n_draws=300, burn=300, thin=2, seed=2022) -> dict:
    """County-level fit for a cycle with only county results: the carried-forward coefficients are the prior, the counties' official
    two-party votes update them (mostly the area totals: an area with one county, e.g. Miami-Dade, is one data point)."""
    areas = pd.read_csv(M.FITS / "areas.csv", dtype={"state_fips": str})
    f = county_frame(year, vint=year)
    f = f[f["area"].isin(areas["area"])].reset_index(drop=True)
    groups = M.VARIANTS["origin"]
    X, w, area, yd, yr, C = M.design(f, groups, areas)
    w = np.minimum(C.sum(axis=1), W_CAP_COUNTY)
    R = len(areas)
    sd, sr = ei.suff_stats(X, yd, w, area, R), ei.suff_stats(X, yr, w, area, R)
    pd_, pr_, ratio = carried_forward(areas, f, spec)
    r = ei.fit(sd, sr, areas["state"].to_numpy(), n_beta=len(groups), seed=seed, n_draws=n_draws, burn=burn, thin=thin,
               prior_d=pd_, prior_r=pr_, prior_sd=PRIOR_SD_2022)
    return {"bd": r["bd"].astype(np.float32), "br": r["br"].astype(np.float32), "gd": r["gd"].astype(np.float32),
            "gr": r["gr"].astype(np.float32), "groups": np.array(groups), "cvap_ak": M.group_cvap_by_area(f, groups, areas),
            "n_tracts": sd.n, "mu_d": r["mu"][:, 0], "tau_d": r["tau"][:, 0], "kappa_d": r["kappa"][:, 0],
            "turnout_ratio": ratio}


def _shift_to_totals(logit: np.ndarray, v: np.ndarray, d_target: np.ndarray, area: np.ndarray, n_areas: int) -> np.ndarray:
    """Per-area logit shift so that sum_i v_i sigmoid(logit_i + delta_a) equals the area's actual Democratic votes."""
    delta = np.zeros(n_areas)
    for _ in range(40):
        p = 1.0 / (1.0 + np.exp(-(logit + delta[area])))
        f = np.bincount(area, weights=v * p, minlength=n_areas) - d_target
        g = np.bincount(area, weights=v * p * (1 - p), minlength=n_areas)
        delta -= np.where(g > 1e-9, f / np.maximum(g, 1e-9), 0.0)
        delta = np.clip(delta, -6, 6)
    return 1.0 / (1.0 + np.exp(-(logit + delta[area])))


def _wrmse(err: np.ndarray, w: np.ndarray) -> float:
    return float(np.sqrt((w * err ** 2).sum() / w.sum())) if w.sum() > 0 else float("nan")


def loco(spec: str = "plain", years=M.YEARS) -> pd.DataFrame:
    """Leave one cycle out.  Coefficients of the other cycles (posterior means, area x group) predict each tract of the held-out
    cycle from its group shares; the area's total is then matched to the actual area result (a logit shift), so the score is how well
    the group structure places the vote across the tracts of an area, not the area's swing.  Variants: area average only (no
    groups), one Hispanic group, seven origin groups."""
    frames = {y: M.load(y) for y in years}
    areas = M.area_table(frames)
    rows = []
    for h in years:
        f = frames[h]
        others = [y for y in years if y != h]
        v = (f["votes_d"] + f["votes_r"]).to_numpy()
        d = f["votes_d"].to_numpy()
        s_true = d / v
        ai = f["area"].map(dict(zip(areas["area"], range(len(areas))))).to_numpy()
        d_t = np.bincount(ai, weights=d, minlength=len(areas))
        hs = (f[[f"c_{g}" for g in F.HISP]].sum(axis=1) / f["cvap"]).to_numpy()
        preds = {"area average only": np.full(len(f), 0.0)}
        for var, label in (("hisp1", "one Hispanic group"), ("origin", "seven origin groups")):
            fits = [M.load_fit(y, spec, var) for y in others]
            bd = np.mean([x["bd"].mean(axis=0) for x in fits], axis=0)
            br = np.mean([x["br"].mean(axis=0) for x in fits], axis=0)
            X = M.design(f, M.VARIANTS[var], areas)[0]
            yd, yr = (X * bd[ai]).sum(1), (X * br[ai]).sum(1)
            sh = np.clip(yd / np.maximum(yd + yr, 1e-9), 0.005, 0.995)
            preds[label] = np.log(sh / (1 - sh))
        for label, lg in preds.items():
            p = _shift_to_totals(lg, v, d_t, ai, len(areas))
            e = p - s_true
            rec = {"heldout": h, "model": label, "rmse_all": _wrmse(e, v)}
            for nm, m in (("hisp>=25%", hs >= 0.25), ("hisp>=50%", hs >= 0.5), ("hisp<10%", hs < 0.10),
                          ("Florida", (f["state_fips"] == "12").to_numpy()), ("Texas", (f["state_fips"] == "48").to_numpy()),
                          ("NY+NJ", f["state_fips"].isin(["36", "34"]).to_numpy())):
                rec[f"rmse_{nm}"] = _wrmse(e[m], v[m])
            rec["n_tracts"] = len(f)
            rows.append(rec)
    out = pd.DataFrame(rows)
    base = out[out["model"] == "area average only"].set_index("heldout")
    for c in [c for c in out.columns if c.startswith("rmse_")]:
        out[c.replace("rmse_", "skill_")] = 1 - (out[c] / out["heldout"].map(base[c])) ** 2
    return out


def loco_county(spec: str = "plain", year: int = 2022) -> pd.DataFrame:
    """The same leave-one-cycle-out test on county results of a cycle without tract data (2022): coefficients of 2020 and 2024 predict
    the counties' two-party shares from their group shares; area totals are matched.  Persistence baselines use each county's own
    earlier result (presidential 2020, and the 2020/2024 average) and are far stronger than any composition model - they are the bar
    for 'is there county information the model misses', not a competitor."""
    areas = pd.read_csv(M.FITS / "areas.csv", dtype={"state_fips": str})
    f = county_frame(year, vint=year)
    f = f[f["area"].isin(areas["area"])].reset_index(drop=True)
    v = (f["votes_d"] + f["votes_r"]).to_numpy()
    d = f["votes_d"].to_numpy()
    s_true = d / v
    ai = f["area"].map(dict(zip(areas["area"], range(len(areas))))).to_numpy()
    d_t = np.bincount(ai, weights=d, minlength=len(areas))
    hs = (f[[f"c_{g}" for g in F.HISP]].sum(axis=1) / f["cvap"]).to_numpy()
    preds = {"area average only": np.zeros(len(f))}
    for var, label in (("hisp1", "one Hispanic group"), ("origin", "seven origin groups")):
        fits = [M.load_fit(y, spec, var) for y in (2020, 2024)]
        bd = np.mean([x["bd"].mean(axis=0) for x in fits], axis=0)
        br = np.mean([x["br"].mean(axis=0) for x in fits], axis=0)
        X = M.design(f, M.VARIANTS[var], areas)[0]
        yd, yr = (X * bd[ai]).sum(1), (X * br[ai]).sum(1)
        sh = np.clip(yd / np.maximum(yd + yr, 1e-9), 0.005, 0.995)
        preds[label] = np.log(sh / (1 - sh))
    res = pd.read_parquet(RESULTS)
    res = res[(res["office"] == "pres") & res["year"].isin([2020, 2024])].pivot_table(index="fips", columns="year", values="s2")
    for lab, src in (("county's own 2020 result", res[2020]), ("county's own 2020/2024 average", (res[2020] + res[2024]) / 2)):
        sv = np.clip(f["county"].map(src).fillna(0.5).to_numpy(), 0.005, 0.995)
        preds[lab] = np.log(sv / (1 - sv))
    rows = []
    for label, lg in preds.items():
        p = _shift_to_totals(lg, v, d_t, ai, len(areas))
        e = p - s_true
        rec = {"heldout": year, "model": label, "rmse_all": _wrmse(e, v)}
        for nm, m in (("hisp>=10%", hs >= 0.10), ("hisp>=25%", hs >= 0.25), ("Florida", (f["state_fips"] == "12").to_numpy()),
                      ("Texas", (f["state_fips"] == "48").to_numpy())):
            rec[f"rmse_{nm}"] = _wrmse(e[m], v[m])
        rec["n_counties"] = len(f)
        rows.append(rec)
    out = pd.DataFrame(rows)
    b = out[out["model"] == "area average only"].iloc[0]
    for c in [c for c in out.columns if c.startswith("rmse_")]:
        out[c.replace("rmse_", "skill_")] = 1 - (out[c] / b[c]) ** 2
    return out


CES_GROUP = {"Mexican": "mexican", "Puerto Rican": "puerto_rican", "Cuban": "cuban", "Dominican": "dominican",
             "Central American": "central_am", "South American": "south_am", "Hispanic (all)": "hispanic"}
CES_STATE = {0: "United States (contiguous)", 4: "Arizona", 6: "California", 12: "Florida", 17: "Illinois", 32: "Nevada",
             34: "New Jersey", 36: "New York", 42: "Pennsylvania", 48: "Texas"}
MIN_N = 60                      # CES cells with fewer unweighted validated voters are listed but not scored


def ces_cells(est: pd.DataFrame, years=(2016, 2020, 2024)) -> pd.DataFrame:
    """One row per CES cell (year, region, origin) with the ecological estimate of the same cell next to the survey value."""
    ces = pd.read_csv(OUT / "ces_origin_vote.csv")
    rows = []
    for _, r in ces[ces["year"].isin(years)].iterrows():
        if r["origin"] not in CES_GROUP or r["state_fips"] not in CES_STATE:
            continue
        q = est[(est["year"] == r["year"]) & (est["region"] == CES_STATE[r["state_fips"]]) & (est["group"] == CES_GROUP[r["origin"]])]
        if len(q) == 0:
            continue
        q = q.iloc[0]
        rows.append({"year": int(r["year"]), "region": q["region"], "group": q["group"], "n": int(r["n"]), "ces": r["dem2p"],
                     "ces_se": r["se"], "ei": q["d2p"], "ei_sd": (q["d2p_hi"] - q["d2p_lo"]) / 3.29})
    t = pd.DataFrame(rows)
    t["diff"] = t["ei"] - t["ces"]
    return t


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def calibration_shift(est: pd.DataFrame, years=(2016, 2020, 2024)) -> dict[int, float]:
    """Cycle-level logit shift that moves the national Hispanic estimate onto the CES national Hispanic value (one number per cycle,
    applied to every Hispanic origin group and region of that cycle; non-Hispanic groups untouched)."""
    ces = pd.read_csv(OUT / "ces_origin_vote.csv")
    out = {}
    for y in years:
        c = ces[(ces["year"] == y) & (ces["origin"] == "Hispanic (all)") & (ces["state_fips"] == 0)].iloc[0]["dem2p"]
        e = est[(est["year"] == y) & (est["region"] == "United States (contiguous)") & (est["group"] == "hispanic")].iloc[0]["d2p"]
        out[y] = float(logit(c) - logit(e))
    if 2022 not in out:
        out[2022] = 0.5 * (out[2020] + out[2024])
    return out


def compare(est: pd.DataFrame) -> dict:
    t = ces_cells(est)
    s = t[t["n"] >= MIN_N]
    res = {"cells": t}
    summary = []
    for y in (2016, 2020, 2024):
        q = s[s["year"] == y]
        summary.append({"year": y, "cells": len(q), "bias": q["diff"].mean(), "rmse": float(np.sqrt((q["diff"] ** 2).mean())),
                        "mean_abs": q["diff"].abs().mean()})
    res["raw"] = pd.DataFrame(summary)
    # calibrated: shift each Hispanic-origin estimate by the cycle's national shift, then score the non-national cells only
    shift = calibration_shift(est)
    t2 = t.copy()
    t2["ei_cal"] = 1 / (1 + np.exp(-(logit(t2["ei"]) + t2["year"].map(shift))))
    t2["diff_cal"] = t2["ei_cal"] - t2["ces"]
    summ = []
    for y in (2016, 2020, 2024):
        q = t2[(t2["year"] == y) & (t2["n"] >= MIN_N) & ~((t2["region"] == "United States (contiguous)") & (t2["group"] == "hispanic"))]
        summ.append({"year": y, "cells": len(q), "bias_raw": q["diff"].mean(), "rmse_raw": float(np.sqrt((q["diff"] ** 2).mean())),
                     "bias_cal": q["diff_cal"].mean(), "rmse_cal": float(np.sqrt((q["diff_cal"] ** 2).mean())),
                     "shift_logit": shift[y]})
    res["calibrated"] = pd.DataFrame(summ)
    res["cells_cal"] = t2
    # gaps between groups are the better-identified quantity: group minus Mexican, same region and year
    g = t[(t["n"] >= MIN_N)].copy()
    mex = g[g["group"] == "mexican"].set_index(["year", "region"])
    gaps = []
    for _, r in g[~g["group"].isin(["mexican", "hispanic"])].iterrows():
        k = (r["year"], r["region"])
        if k in mex.index:
            m = mex.loc[k]
            gaps.append({"year": r["year"], "region": r["region"], "group": r["group"], "ces_gap": r["ces"] - m["ces"],
                         "ces_gap_se": float(np.hypot(r["ces_se"], m["ces_se"])), "ei_gap": r["ei"] - m["ei"]})
    res["gaps"] = pd.DataFrame(gaps)
    return res


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "compare":
        e = estimates()
        r = compare(e)
        pd.set_option("display.width", 220)
        print(r["cells"].round(3).to_string()); print(r["raw"].round(3)); print(r["calibrated"].round(3)); print(r["gaps"].round(3))
    if cmd == "fit2022":
        r = fit_county_cycle()
        np.savez_compressed(M.FITS / "fit_2022_plain_origin.npz", **r)
        print("2022 county fit saved; units", int(r["n_tracts"].sum()))
    if cmd == "loco":
        t = loco()
        tc = loco_county()
        tc.to_csv(OUT / "loco_county2022.csv", index=False)
        pd.set_option("display.width", 240)
        print(tc.round(4).to_string())
        t.to_csv(OUT / "loco.csv", index=False)
        pd.set_option("display.width", 220)
        print(t.round(4).to_string())
    if cmd == "estimates":
        e = estimates()
        e.to_csv(OUT / "estimates_raw.csv", index=False)
        print(e[(e.region == "United States (contiguous)")].round(3).to_string())
