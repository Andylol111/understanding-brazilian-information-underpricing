"""Reporting-order risk (`brunder.us_votetype`) tested on real US count nights and the Georgia simulated nights, leave-one-cycle-out.

INTERNAL: the real nights are NYT / Edison copies (terms: no redistribution, nothing derived is published). Everything this script writes
goes to $BRUNDER_DATA/derived/us_votetype/ (cache/, tables, REPORT.md), never into the repository. The only numbers fit for docs/us_votetype.md
are the official-source Michigan 2022 night (section `official`) and the Georgia simulated nights (`ga`).

  uv run python -W ignore analysis/us_votetype.py cache [--only <key substrings>]   # base v1 / v3 / unified projections per frame
  uv run python -W ignore analysis/us_votetype.py ga [--n 30]                       # same for the Georgia simulated nights
  uv run python -W ignore analysis/us_votetype.py run                               # leave-one-cycle-out fit + tables -> REPORT.md
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_real_nights as rn                          # noqa: E402
from brunder import us, us_unified as uu             # noqa: E402
from brunder import us_votetype as vt                # noqa: E402
from brunder.paths import DATA                       # noqa: E402

OUT = DATA / "derived" / "us_votetype"
CACHE = OUT / "cache"
PROC = rn.PROC
VARIANTS = ("v1", "v3", "unified")
UCOLS = ["unit_id", "n", "a", "ne", "ae", "rem", "rem_margin"]


# ------------------------------------------------------------------ base projections (cached)


def with_unit_id(snaps: pd.DataFrame, st: str) -> pd.DataFrame:
    return snaps.assign(unit_id=[uu.unit_id(st, f) for f in snaps["fips"]])


def night_frames(snaps: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.DataFrame, float]]:
    out, last = [], -1.0
    for ts, fr in snaps.groupby("ts", sort=True):
        n = float((fr["d"] + fr["r"]).sum())
        if n <= 0 or n == last:
            continue
        out.append((ts, fr, n))
        last = n
    return out


def cache_night(ctx: rn.Ctx, sp: dict, snaps: pd.DataFrame, truth: dict, vs: dict) -> dict:
    """Base (unextended) projections of every scored frame: per variant the res dict and the compact units frame."""
    fr = night_frames(snaps)
    if "real" in snaps.columns:
        anchor = snaps.loc[snaps["real"] & ((snaps["d"] + snaps["r"]) > 0), "ts"].min()
        fr = [f for f in fr if f[0] >= anchor]
    t0 = fr[0][0]
    rec = {"key": sp["key"], "t": sp["t"], "st": sp["st"], "office": sp["office"], "kind": sp["kind"], "interp": sp["interp"],
           "truth": truth, "t0": t0, "v": {}}
    for name, (cfg, pri) in vs.items():
        if name not in VARIANTS:
            continue
        pm = us.params(cfg)
        r0, _ = us.project(us._frame([]), cfg, pri)
        frames = []
        for ts, f, n in fr:
            res, u = us.project(rn.feed_rows(f, sp["st"]), cfg, pri)
            frames.append({"ts": ts, "minute": (ts - t0).total_seconds() / 60, "counted": n / truth["v2"], "res": res, "u": u[UCOLS].copy()})
        rec["v"][name] = {"cfg": cfg, "rho": pm.rho, "kappa": pm.kappa, "pre": r0, "frames": frames}
    return rec


def cmd_cache(only: list[str] | None) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    ctx = rn.Ctx()
    for sp in rn.night_specs():
        if only and not any(o in sp["key"] for o in only):
            continue
        if sp["st"] not in rn.TARGET:
            continue
        f = PROC / f'{sp["key"]}.parquet'
        dest = CACHE / f'{sp["key"]}.pkl'
        if not f.exists() or (dest.exists() and not only):
            continue
        truth = ctx.truth(sp["t"], sp["st"], sp["office"], sp["special"])
        if truth is None:
            continue
        snaps = pd.read_parquet(f)
        try:
            rec = cache_night(ctx, sp, snaps, truth, rn.variants(ctx, sp))
        except (KeyError, ValueError, IndexError, RuntimeError) as e:
            print(f'{sp["key"]}: FAILED {type(e).__name__}: {e}')
            continue
        if not rec["v"] or not any(v["frames"] for v in rec["v"].values()):
            continue
        rec["snaps"] = with_unit_id(snaps, sp["st"])[["ts", "unit_id", "d", "r", "ed", "er"]]
        with open(dest, "wb") as fh:
            pickle.dump(rec, fh)
        print(f'{sp["key"]}: {sorted(rec["v"])} {len(next(iter(rec["v"].values()))["frames"])} frames', flush=True)


# ------------------------------------------------------------------ Georgia simulated nights (certified returns by vote type, simulated count order)

import us_calibrate as cal                           # noqa: E402
import us_unified_replay as ur                       # noqa: E402

GA_RACES = [("ga_gov_2022", 2022, "gov", "2022NovGen"), ("ga_sen_2022", 2022, "sen", "2022NovGen"), ("ga_pres_2024", 2024, "pres", "2024NovGen")]
GA_CERT = [(2020, "2020NovGen", "pres"), (2022, "2022NovGen", "gov"), (2022, "2022NovGen", "sen"), (2024, "2024NovGen", "pres")]
CHECKS = [c / 100 for c in range(5, 91, 5)]


def ga_night_frame(eid: str, race: str) -> pd.DataFrame:
    """GA certified county returns by vote type as a one-frame night: d, r and the early (advance + mail) part ed, er."""
    b = cal.by_type(eid, race)
    return pd.DataFrame({"ts": pd.Timestamp("2000-01-01", tz="UTC"), "unit_id": b["unit_id"], "d": b["early_d"] + b["late_d"],
                         "r": b["early_r"] + b["late_r"], "ed": b["early_d"], "er": b["early_r"]})


def cmd_ga(n_sim: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    ctx = rn.Ctx()
    priors = us.load_priors()
    base = {"election": {"state": "ga", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
            "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "early_share": 0.64}}
    rng = np.random.default_rng(20261103)
    recs = []
    for name, t, office, eid in GA_RACES:
        btp = cal.by_type(eid, office)
        tot = float(btp[["early_d", "early_r", "late_d", "late_r"]].sum().sum())
        final = float((btp["early_d"] + btp["late_d"]).sum() / tot)
        truth = {"s2": final, "v2": tot}
        if office == "pres":
            pri1 = us.race_prior({**base, "prior": {"share": "ga_sen_2022", "votes": "ga_sen_2022"}}, priors)
            cfg1 = {**base, "count": {**base["count"], "turnout_ratio": 1.30}}
            poll = rn.pres_poll(ctx, t, "ga")
            pri_u, info = None, None
        else:
            pri1 = us.race_prior({**base, "prior": {"share": "ga_pres_2020", "votes": "ga_pres_2020"}}, priors)
            cfg1 = {**base, "count": {**base["count"], "turnout_ratio": 0.8}}
            pri_u, info = ur.ga_state_rows(ctx.inp, ctx.R, ctx.res, ctx.C, office)
            poll = (info["P"], info["n_polls"]) if info["n_polls"] > 0 else None
        base1 = us.baseline_share(pri1)
        vs = {"v1": (cfg1, pri1, {})}
        if poll and poll[0] is not None and not pd.isna(poll[0]) and poll[1] > 0:
            vs["v3"] = (cfg1, pri1, {"swing_prior_mean": float(poll[0]) - base1, "swing_prior_sd": us.poll_sd_by_n(int(poll[1])), "count_scaled_terms": True})
        if pri_u is not None:
            cfg_u = {**base, "count": {**base["count"], "turnout_ratio": 1.0}}
            vs["unified"] = (cfg_u, pri_u, {"swing_prior_mean": 0.0, "swing_prior_sd": info["sd"], "count_scaled_terms": True})
        cfgs = {k: ({**c, "model": {"tau": 0.04, "rho": 0.6, "sigma_mun0": 0.08, **x}, "count": {**c["count"], "vote_type": True}}, p) for k, (c, p, x) in vs.items()}
        pre = {k: us.project(us._frame([]), c, p)[0] for k, (c, p) in cfgs.items()}
        for s in range(n_sim):
            ev = cal.events(btp, rng)
            cum = (ev["d"] + ev["r"]).cumsum() / tot
            ups = {c: int(np.searchsorted(cum.to_numpy(), c)) + 1 for c in CHECKS}
            rec = {"key": f"{name}_sim{s:02d}", "race": name, "t": t, "st": "ga", "office": office, "kind": "sim", "interp": False, "truth": truth, "v": {}}
            for k, (c, p) in cfgs.items():
                pm = us.params(c)
                frames = []
                for chk, up in ups.items():
                    res, u = us.project(cal.rows_at(ev, up), c, p)
                    frames.append({"ts": pd.NaT, "minute": float(ev["t"].iloc[up - 1]), "counted": chk, "res": res, "u": u[UCOLS].copy()})
                rec["v"][k] = {"cfg": c, "rho": pm.rho, "kappa": pm.kappa, "pre": pre[k], "frames": frames}
            recs.append(rec)
        print(f"{name}: {n_sim} nights", flush=True)
    with open(CACHE / "ga_sim.pkl", "wb") as fh:
        pickle.dump(recs, fh)


# ------------------------------------------------------------------ leave-one-cycle-out evaluation

from scipy.stats import norm                         # noqa: E402

SETTINGS = ("base", "vt", "vt shift only", "vt widen only", "uniform widen")
KAPPAS = np.r_[0.0, np.geomspace(0.05, 8.0, 40)]


def load_real() -> list[dict]:
    return [pickle.load(open(p, "rb")) for p in sorted(CACHE.glob("*.pkl")) if p.stem != "ga_sim"]


def arrays(rec: dict, variant: str, profile: vt.Profile | None) -> dict:
    """Per-frame arrays of the unextended projection and of the extension at lam = 1, kappa = 0:
    share0, sd0 (share units), S (shift of the share at lam = 1), X (exposure, share units), Xu (outstanding share of the vote)."""
    v = rec["v"][variant]
    pm = vt.Params(lam=1.0, kappa=0.0)
    cols = {k: [] for k in ("minute", "counted", "share0", "sd0", "S", "X", "Xu", "split")}
    pre = v["pre"]
    for k, x in zip(cols, (-1.0, 0.0, pre["share_hat"], pre["share_sd"], 0.0, 0.0, 0.0, 0.0)):
        cols[k].append(x)
    for fr in v["frames"]:
        r2, u = vt.adjust(fr["res"], fr["u"], profile, v["rho"], v["kappa"], pm)
        rem, n = float(fr["u"]["rem"].sum()), float(fr["u"]["n"].sum())
        for k, x in zip(cols, (fr["minute"], fr["counted"], fr["res"]["share_hat"], fr["res"]["share_sd"],
                               r2["share_hat"] - fr["res"]["share_hat"], r2["vt_exposure"], rem / (n + rem) if n + rem > 0 else 0.0,
                               float(fr["u"]["ne"].sum() > 0))):
            cols[k].append(x)
    out = {k: np.asarray(x, float) for k, x in cols.items()}
    out.update(night=rec["key"], variant=variant, final=rec["truth"]["s2"], win=rec["truth"]["s2"] > 0.5, has_profile=profile is not None)
    return out


def frames_of_setting(a: dict, lam, kappa, uni: float, label: str) -> pd.DataFrame:
    """lam / kappa: scalars or per-frame arrays."""
    share = a["share0"] + lam * a["S"]
    sd = np.sqrt(a["sd0"] ** 2 + (kappa * a["X"]) ** 2 + (uni * a["Xu"]) ** 2)
    err = share - a["final"]
    P = norm.cdf((share - 0.5) / sd)
    d = pd.DataFrame({"variant": label, "minute": a["minute"], "counted": a["counted"], "share_hat": share, "sd": sd, "P": P, "night": a["night"],
                      "final": a["final"], "win": a["win"], "err": err, "z": err / sd})
    d["cover90"] = d["z"].abs() < 1.645
    d["p_correct"] = np.where(d["win"], d["P"], 1 - d["P"])
    return d


LAM_EDGES = (0.15, 0.40)       # shift weights are fitted separately for frames below 15 %, 15-40 % and above 40 % counted


def _bin(a: dict) -> np.ndarray:
    return np.digitize(a["counted"], LAM_EDGES)


def _use(a: dict, g: int | None = None, b: int | None = None) -> np.ndarray:
    m = (a["minute"] >= 0) & (a["counted"] >= 0.05) & (a["counted"] <= 0.9)
    if g is not None:
        m = m & (a["split"] == g)
    return m if b is None else m & (_bin(a) == b)


def _weights(arrs: list[dict], g: int | None, b: int | None = None) -> list[np.ndarray]:
    """Frame weights: every (night, variant) counts once (in the group g of frames: 1 = the feed reported an early split, 0 = not)."""
    return [(_use(a, g, b) / max(_use(a, g, b).sum(), 1)) for a in arrs]


def fit_lam(arrs: list[dict], g: int | None, b: int | None = None) -> float:
    W = _weights(arrs, g, b)
    num = sum(float((w * (a["share0"] - a["final"]) * a["S"]).sum()) for a, w in zip(arrs, W))
    den = sum(float((w * a["S"] ** 2).sum()) for a, w in zip(arrs, W))
    return float(np.clip(-num / den, 0.0, 1.5)) if den > 0 else 0.0


def _lam_of(a: dict, lams) -> np.ndarray:
    return np.asarray(lams, float)[_bin(a)]


def fit_kappa(arrs: list[dict], lams, g: int | None, key: str = "X") -> float:
    """Widening scale minimising the Gaussian log score of the (shifted) errors over the 5-90 % counted frames, nights weighted equally."""
    W = _weights(arrs, g)
    best, bk = np.inf, 0.0
    for k in KAPPAS:
        nll = 0.0
        for a, w in zip(arrs, W):
            sd = np.sqrt(a["sd0"] ** 2 + (k * a[key]) ** 2)
            e = a["share0"] + _lam_of(a, lams) * a["S"] - a["final"]
            nll += float((w * (np.log(sd) + 0.5 * (e / sd) ** 2)).sum())
        if nll < best - 1e-9:
            best, bk = nll, float(k)
    return bk


def fit_all(arrs: list[dict]) -> dict:
    """Shift weights lam (one per counted bin) and widening kappa, separately for frames where the feed reports an early split ('obs') and
    where it does not ('hist': the projection then rests on the history alone), plus the widening of the uniform control."""
    out: dict = {"nights": len(arrs), "kappa_uniform": fit_kappa(arrs, [0.0] * 3, None, "Xu")}
    for g, name in ((1, "obs"), (0, "hist")):
        lams = [fit_lam(arrs, g, b) for b in range(len(LAM_EDGES) + 1)]
        out[name] = {"lam": lams, "kappa": fit_kappa(arrs, lams, g), "kappa_widen": fit_kappa(arrs, [0.0] * 3, g)}
    return out


def _vec(a: dict, fit: dict, key: str, hist_on: bool = True) -> np.ndarray:
    def one(name):
        x = fit[name][key]
        return _lam_of(a, x) if key == "lam" else np.full(len(a["minute"]), x)
    return np.where(a["split"] == 1, one("obs"), one("hist") if hist_on else 0.0)


def setting_frames(a: dict, fit: dict) -> list[pd.DataFrame]:
    v = a["variant"]
    return [frames_of_setting(a, 0.0, 0.0, 0.0, f"{v} | base"),
            frames_of_setting(a, _vec(a, fit, "lam"), _vec(a, fit, "kappa"), 0.0, f"{v} | vt"),
            frames_of_setting(a, _vec(a, fit, "lam", False), _vec(a, fit, "kappa", False), 0.0, f"{v} | vt tonight's split only"),
            frames_of_setting(a, _vec(a, fit, "lam"), 0.0, 0.0, f"{v} | vt shift only"),
            frames_of_setting(a, 0.0, _vec(a, fit, "kappa_widen"), 0.0, f"{v} | vt widen only"),
            frames_of_setting(a, 0.0, 0.0, fit["kappa_uniform"], f"{v} | uniform widen")]


W_OTHER = 0.25      # weight of a cycle of the other kind (presidential year vs midterm) relative to one of the same kind


def cycle_weights(target: int, cycles) -> dict[int, float]:
    """Past cycles of the same kind as the night's (presidential / midterm) count fully, the others W_OTHER."""
    return {c: 1.0 if (c % 4 == target % 4) else W_OTHER for c in cycles}


class Profiles:
    """Leave-cycles-out historical profiles per state, cached."""

    def __init__(self, recs: list[dict]):
        self.recs, self.cache = recs, {}

    def get(self, st: str, excl: frozenset, target: int) -> vt.Profile | None:
        k = (st, excl, target)
        if k not in self.cache:
            cyc: dict[int, list[pd.DataFrame]] = {}
            for r in self.recs:
                if r["st"] == st and not r["interp"] and r["kind"] == rn.NYT and r["t"] not in excl:
                    cyc.setdefault(r["t"], []).append(r["snaps"])
            self.cache[k] = vt.fit_profile(cyc, weights=cycle_weights(target, cyc))
        return self.cache[k]


def run_real(recs: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    P_ = Profiles(recs)
    frames, fits = [], []
    for T in sorted({r["t"] for r in recs}):
        train = [r for r in recs if r["t"] != T and not r["interp"] and r["kind"] == rn.NYT]
        tr = [arrays(r, v, P_.get(r["st"], frozenset({T, r["t"]}), r["t"])) for r in train for v in r["v"]]
        fit = fit_all(tr)
        fits.append({"test_cycle": T, **fit})
        print(f"test {T}: {fit}", flush=True)
        for r in (x for x in recs if x["t"] == T):
            prof = P_.get(r["st"], frozenset({T}), T)
            for v in r["v"]:
                a = arrays(r, v, prof)
                d = pd.concat(setting_frames(a, fit), ignore_index=True)
                d["has_profile"], d["t"], d["st"], d["office"], d["kind"], d["interp"] = prof is not None, T, r["st"], r["office"], r["kind"], r["interp"]
                frames.append(d)
    return pd.concat(frames, ignore_index=True), pd.DataFrame(fits)


GA_PROFILE_CYCLES = {2020: [("2020NovGen", "pres")], 2022: [("2022NovGen", "gov"), ("2022NovGen", "sen")], 2024: [("2024NovGen", "pres")]}


def ga_profile(excl: frozenset, target: int) -> vt.Profile | None:
    cyc = {c: [ga_night_frame(e, r) for e, r in lst] for c, lst in GA_PROFILE_CYCLES.items() if c not in excl}
    return vt.fit_profile(cyc, weights=cycle_weights(target, cyc))


def run_ga(sims: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    prof_cache: dict = {}

    def prof(excl, target):
        if (excl, target) not in prof_cache:
            prof_cache[(excl, target)] = ga_profile(excl, target)
        return prof_cache[(excl, target)]

    frames, fits = [], []
    for T in sorted({r["t"] for r in sims}):
        train = [r for r in sims if r["t"] != T]
        tr = [arrays(r, v, prof(frozenset({T, r["t"]}), r["t"])) for r in train for v in r["v"]]
        fit = fit_all(tr)
        fits.append({"test_cycle": T, **fit})
        print(f"GA sim test {T}: {fit}", flush=True)
        for r in (x for x in sims if x["t"] == T):
            p = prof(frozenset({T}), T)
            for v in r["v"]:
                d = pd.concat(setting_frames(arrays(r, v, p), fit), ignore_index=True)
                d["t"], d["race"] = T, r["race"]
                frames.append(d)
    return pd.concat(frames, ignore_index=True), pd.DataFrame(fits)


def summarize(raw: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """rn.night_summary per (night, 'variant | setting'), plus the night's metadata and class."""
    ns = rn.night_summary(raw)
    ns[["v", "setting"]] = ns["variant"].str.split(" \\| ", expand=True)
    return ns.merge(meta, on="night", how="left")


def agg(ns: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    return ns.groupby(by).agg(nights=("night", "nunique"), decided=("decided", "mean"), minutes_to_90_median=("minutes_to_90", "median"),
                              pre_abs_err_pts=("pre_err_pts", lambda x: x.abs().mean()), err10=("abs_err_10_pts", "mean"), err25=("abs_err_25_pts", "mean"),
                              err50=("abs_err_50_pts", "mean"), err75=("abs_err_75_pts", "mean"), cover90=("cover90", "mean")).reset_index()


def paired(ns: pd.DataFrame, setting: str = "vt", ref: str = "base") -> pd.DataFrame:
    """Per night and variant: setting - ref for the headline metrics (negative = better; coverage is scored by its distance to 90 %)."""
    out = []
    for col, lab in (("abs_err_10_pts", "abs error at ~10 % counted, pts"), ("abs_err_25_pts", "abs error at ~25 % counted, pts"),
                     ("abs_err_50_pts", "abs error at ~50 % counted, pts"), ("abs_err_75_pts", "abs error at ~75 % counted, pts"),
                     ("minutes_to_90", "minutes to P(winner) >= 0.9"), ("cover90", "|90 % coverage - 0.9|")):
        w = ns.pivot_table(index=["night", "v"], columns="setting", values=col)
        if setting not in w or ref not in w:
            continue
        if col == "cover90":
            w = (w - 0.9).abs()
        d = (w[setting] - w[ref]).dropna()
        out.append({"metric": lab, "setting": setting, "vs": ref, "rows": len(d), "mean_diff": d.mean() if len(d) else np.nan,
                    "median_diff": d.median() if len(d) else np.nan, "better": int((d < -1e-9).sum()), "worse": int((d > 1e-9).sum())})
    return pd.DataFrame(out)


def classify(ns: pd.DataFrame) -> pd.DataFrame:
    ns = ns.assign(interp=ns["interp"].astype(bool))
    ns["cls"] = rn.night_class(ns)
    ns["grp"] = np.where(ns["office"] == "pres", "president", "senate/governor")
    return ns


SHOW = ["base", "vt", "vt tonight's split only", "vt shift only", "vt widen only", "uniform widen"]


def sensitivity_widen(recs: list[dict], fits: pd.DataFrame, kappas=(0.0, 0.5, 1.0, 2.0, 4.0, 8.0)) -> pd.DataFrame:
    """NOT out of sample (the grid is read off the tested cycles): dense real presidential nights, v1, shift of the leave-one-cycle-out fit,
    widening kappa on the exposure in every frame with a profile. Shows what a larger fixed kappa would do to coverage and minutes to 90."""
    P_, out = Profiles(recs), []
    for f in fits.to_dict("records"):
        T = f["test_cycle"]
        for r in recs:
            if r["t"] != T or r["interp"] or r["kind"] != rn.NYT or r["office"] != "pres" or "v1" not in r["v"]:
                continue
            a = arrays(r, "v1", P_.get(r["st"], frozenset({T}), T))
            for k in kappas:
                out.append(frames_of_setting(a, _vec(a, f, "lam", False), k * a["has_profile"], 0.0, f"v1 | kappa {k:g}").assign(t=T, interp=False, office="pres"))
    d = pd.concat(out, ignore_index=True)
    ns = rn.night_summary(d)
    ns["cycle"] = ns["night"].str[:4].astype(int)
    ns["kappa"] = ns["variant"].str.extract(r"kappa ([\d.]+)")[0].astype(float)
    return ns[ns["n_mid"] >= rn.DENSE_MID].groupby(["cycle", "kappa"]).agg(
        nights=("night", "nunique"), decided=("decided", "mean"), minutes_to_90_median=("minutes_to_90", "median"), err25=("abs_err_25_pts", "mean"),
        err50=("abs_err_50_pts", "mean"), err75=("abs_err_75_pts", "mean"), cover90=("cover90", "mean")).reset_index()


def verdict(ns: pd.DataFrame, setting: str) -> pd.DataFrame:
    """The criterion for 'a clear out-of-sample gain on real nights' (dense real presidential nights, v1 and v3, per tested cycle; fixed in this
    function and applied unchanged to every variant of the design that was tried):
    (1) mean abs error at 25 / 50 / 75 % counted lower by >= 10 % at >= 2 of the 3 checkpoints; (2) |coverage - 0.9| not larger and the
    cycle's coverage nearer 0.9; (3) median minutes to 90 not more than 15 % later; all three in BOTH 2020 and 2024."""
    rows = []
    for T in (2020, 2024):
        for v in ("v1", "v3"):
            g = ns[(ns["t"] == T) & (ns["v"] == v) & (ns["cls"] == "dense real") & (ns["grp"] == "president")]
            b, s = g[g["setting"] == "base"], g[g["setting"] == setting]
            gain = {c: 1 - s[c].mean() / b[c].mean() for c in ("abs_err_25_pts", "abs_err_50_pts", "abs_err_75_pts")}
            cb, cs = abs(b["cover90"].mean() - 0.9), abs(s["cover90"].mean() - 0.9)
            mb, ms = b["minutes_to_90"].median(), s["minutes_to_90"].median()
            c1 = sum(x >= 0.10 for x in gain.values()) >= 2
            c2 = cs <= cb
            c3 = (ms <= 1.15 * mb) if mb == mb and ms == ms else True
            rows.append({"cycle": T, "variant": v, "err25 gain": gain["abs_err_25_pts"], "err50 gain": gain["abs_err_50_pts"], "err75 gain": gain["abs_err_75_pts"],
                         "|cover-0.9| base": cb, "|cover-0.9| vt": cs, "minutes base": mb, "minutes vt": ms, "(1) error": c1, "(2) coverage": c2, "(3) minutes": c3,
                         "clear gain": c1 and c2 and c3})
    return pd.DataFrame(rows)


def write_report(raw, fits, nsr, rawg, fitg, nsg, sens, recs) -> Path:
    md = rn.md
    L = ["# US vote-type (reporting-order) risk extension: leave-one-cycle-out test (INTERNAL: real nights are NYT / Edison copies, do not publish)", "",
         "Library `brunder.us_votetype` (post-processes `brunder.us.project`), experiment `analysis/us_votetype.py`. Every fit (state profiles, shift weights `lam` per counted bin, "
         "widening `kappa`) excludes the tested cycle; the profile used to fit `lam` / `kappa` on a training night also excludes that night's own cycle. Settings: "
         "`base` = unextended; `vt` = shift + widening (frames where the feed reports an early split tonight use the 'obs' fit, the others the 'hist' fit); "
         "`vt tonight's split only` = the extension is switched off in frames without a split; `vt shift only` / `vt widen only`; `uniform widen` = the control that inflates the "
         "sd in proportion to the outstanding vote only (no vote-type information), fitted the same way.", ""]
    ok = [bool(verdict(nsr, s)["clear gain"].all()) for s in ("vt", "vt tonight's split only")]
    L += ["## verdict", "", f"Criterion (section 'clear-gain criterion'): error at 25 / 50 / 75 % counted lower by >= 10 % at two of three checkpoints, coverage nearer 0.9, "
          f"minutes to 90 not more than 15 % later, for v1 and v3 on the dense real presidential nights of BOTH 2020 and 2024, fitted without the tested cycle. "
          f"`vt`: {'MET' if ok[0] else 'NOT met'}; `vt tonight's split only`: {'MET' if ok[1] else 'NOT met'}. "
          f"The paired tables and the per-class tables below say where it helps (errors in the first ~15 % counted) and where it does not.", ""]
    L += ["## fitted parameters per tested cycle", "", "```", *[json.dumps(f, default=float) for f in fits.to_dict("records")], "```", ""]
    cols = ["v", "setting", "nights", "decided", "minutes_to_90_median", "pre_abs_err_pts", "err10", "err25", "err50", "err75", "cover90"]
    for (cls, grp, t), g in nsr.groupby(["cls", "grp", "t"]):
        if cls == "sparse real" and g["night"].nunique() < 3:
            continue
        a = agg(g[g["setting"].isin(SHOW)], ["v", "setting"])
        a["setting"] = pd.Categorical(a["setting"], SHOW, ordered=True)
        a = a.sort_values(["v", "setting"])[cols]
        if cls == "sparse real":
            a = a.assign(decided=np.nan, minutes_to_90_median=np.nan)
        L += [f"## {cls}: {grp} {t} ({g['night'].nunique()} nights)", "", md(a), ""]
    nr = nsr[nsr["cls"].isin(["dense real", "interpolated (pres clock)"])]
    for st in ("vt", "vt tonight's split only"):
        L += [f"## paired differences `{st}` - `base` (dense real + interpolated nights, rows = night x variant; negative = better)", "", md(paired(nr, st)), ""]
    for st in ("vt", "vt tonight's split only"):
        L += [f"## clear-gain criterion for `{st}` (dense real presidential nights)", "", md(verdict(nsr, st)), ""]
    L += ["## widening sensitivity (NOT out of sample: kappa read off the tested cycles)", "", md(sens), ""]
    ga = nsg.groupby(["race", "v", "setting"]).agg(sims=("night", "nunique"), decided=("decided", "mean"), minutes_to_90_median=("minutes_to_90", "median"),
                                                    pre_abs_err_pts=("pre_err_pts", lambda x: x.abs().mean()), err10=("abs_err_10_pts", "mean"), err25=("abs_err_25_pts", "mean"),
                                                    err50=("abs_err_50_pts", "mean"), err75=("abs_err_75_pts", "mean"), cover90=("cover90", "mean")).reset_index()
    ga = ga[ga["setting"].isin(SHOW)]
    L += ["## Georgia simulated nights (official certified returns by vote type; fits from the other simulated cycle; 30 simulated nights per race)", "",
         "```", *[json.dumps(f, default=float) for f in fitg.to_dict("records")], "```", "", md(ga), ""]
    mi = raw[(raw["night"] == "2022_mi_gov_off") & raw["variant"].str.contains(r"\| (?:base|vt)$")].copy()
    mi[["v", "setting"]] = mi["variant"].str.split(" \\| ", expand=True)
    mi = mi.assign(error_pts=100 * mi["err"], sd_pts=100 * mi["sd"], capture=mi["minute"].round(0))[["v", "setting", "capture", "counted", "error_pts", "sd_pts", "p_correct"]]
    L += ["## Michigan 2022 governor, official state file (5 archive captures + before the count; the file has no vote-type split: minutes = since the first capture)", "", md(mi), ""]
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "REPORT.md"
    p.write_text("\n".join(L))
    return p


def cmd_run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    recs = load_real()
    raw, fits = run_real(recs)
    meta = raw.drop_duplicates("night")[["night", "t", "st", "office", "kind", "interp", "has_profile"]]
    nsr = classify(summarize(raw, meta))
    sims = pickle.load(open(CACHE / "ga_sim.pkl", "rb"))
    rawg, fitg = run_ga(sims)
    nsg = summarize(rawg, rawg.drop_duplicates("night")[["night", "t", "race"]])
    sens = sensitivity_widen(recs, fits)
    raw.to_csv(OUT / "frames_real.csv.gz", index=False)
    rawg.to_csv(OUT / "frames_ga_sim.csv.gz", index=False)
    nsr.to_csv(OUT / "nights_real.csv", index=False)
    nsg.to_csv(OUT / "nights_ga_sim.csv", index=False)
    fits.to_json(OUT / "fits_real.json", orient="records", indent=1)
    fitg.to_json(OUT / "fits_ga_sim.json", orient="records", indent=1)
    print(write_report(raw, fits, nsr, rawg, fitg, nsg, sens, recs))




def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cache", "ga", "run"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    warnings.filterwarnings("ignore")
    if a.cmd == "cache":
        cmd_cache(a.only)
    elif a.cmd == "ga":
        cmd_ga(a.n)
    else:
        cmd_run()


if __name__ == "__main__":
    main()
