"""Runner and count-night replay for the unified pre-count model. brunder.us is NOT modified: the unified priors are passed
into `brunder.us.Exporter(priors=…)` / `brunder.us.project` as a DataFrame built here.

  # (1) the night, offline, on the recorded archive (same outputs as `brunder us export`, unified configs only)
  BRUNDER_DATA=… uv run python analysis/us_unified_replay.py export --snap <archive root> --out <dir> [--until 2026-11-04T12:00:00Z]
  #     --configs configs/us2026_unified            (default; statewide) ; add --house to include configs/us2026_unified/house
  # (2) pre-count table of the unified configs (priors only; no archive)
  uv run python analysis/us_unified_replay.py precount
  # (3) count-night replay on Georgia 2022 certified returns by vote type (the order model of analysis/us_calibrate.py):
  #     unified prior (fitted without 2022) vs the registered v1 prior (2020 president x 0.8) and a v3-style poll shift
  uv run python analysis/us_unified_replay.py sim [--n 20]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_calibrate as cal                       # noqa: E402  (simulated count order, GA certified by vote type)
import us_unified_backtest as bt                 # noqa: E402
from brunder import us, us_unified as uu         # noqa: E402

ROOT = uu.ROOT
UNIFIED_PRIORS = ROOT / "data_public" / "us2026_unified_priors.csv"
HOUSE_PRIORS = ROOT / "data_public" / "us2026_unified_house_priors.csv"


def unified_priors(include_house: bool = False) -> pd.DataFrame:
    """The registered public priors plus the unified sources (source, unit_id, dem, rep, total)."""
    parts = [us.load_priors(), pd.read_csv(UNIFIED_PRIORS, comment="#")]
    if include_house and HOUSE_PRIORS.exists():
        parts.append(pd.read_csv(HOUSE_PRIORS, comment="#"))
    return pd.concat(parts, ignore_index=True)


def config_paths(cfg_dir: Path, house: bool) -> list[Path]:
    paths = sorted(cfg_dir.glob("us2026_unified_*_v1.toml"))
    if house:
        paths += sorted((cfg_dir / "house").glob("*_v1.toml"))
    return paths


def export(snap: Path, out: Path, cfg_dir: Path, house: bool, until: str | None) -> list[Path]:
    """Replay the recorded archive up to `until` with the unified priors: statewide configs through brunder.us.Exporter
    (-> <out>/statewide), House configs through brunder.us_house_night.HouseExporter (-> <out>/house). The registered code
    is untouched; only the `priors` argument differs from `brunder us export`."""
    pr = unified_priors(house)
    t = pd.Timestamp(until).tz_convert("UTC") if until else None
    outs = []
    ex = us.Exporter(config_paths(cfg_dir, False), snap, priors=pr, max_retries=1, phase="replay")
    ex.update(t)
    outs.append(ex.write(out / "statewide"))
    if house:
        from brunder.us_house_night import HouseExporter
        hx = HouseExporter(config_paths(cfg_dir, True)[len(config_paths(cfg_dir, False)):], snap, priors=pr, max_retries=1, phase="replay")
        hx.update(t)
        outs.append(hx.write(out / "house"))
    return outs


def precount(cfg_dir: Path) -> pd.DataFrame:
    pr = unified_priors()
    rows = []
    for p in config_paths(cfg_dir, False):
        c = us.load_config(p)
        pri = us.race_prior(c, pr)
        m = c["model"]
        rows.append({"id": c["id"], "race": c["election"]["race_id"], "counties": len(pri), "share": us.baseline_share(pri),
                     "sd": m["swing_prior_sd"], "V": pri["V"].sum()})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ Georgia 2022 count-night replay


def ga_state_rows(inp: dict, R: pd.DataFrame, res: pd.DataFrame, C: pd.DataFrame, office: str) -> tuple[pd.DataFrame, dict]:
    """Unified prior (pri frame unit_id, name, s0, V) for GA 2022 `office`, fitted without 2022, with the LOCO state forecast."""
    key = res[(res["t"] == 2022) & (res["st"] == "ga") & (res["office"] == office) & (~res["special"])].iloc[0]
    tr = C[(C["t"] != 2022) & (C["t"].isin(bt.TEST))]
    d = C[(C["t"] == 2022) & (C["st"] == "ga") & (C["office"] == office) & (~C["special"])]
    sm, tm = uu.fit_share(tr, uu.select_lam(tr)), uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
    gb = bt.state_base_votes(C)
    vm = bt.fit_vstate(gb[gb["t"] != 2022])
    vo = float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
    Vs = bt.predict_vstate(vm, float(d["vp_b"].sum()), vo, True, bool(d["oy"].max() % 4 == 2))
    pr = uu.county_prior(d, sm, tm, float(key["mu"]), Vs)
    names = us.counties("ga").set_index("unit_id")["name"]
    uid = [uu.unit_id("ga", f) for f in pr["fips"]]
    pri = pd.DataFrame({"unit_id": uid, "name": names.reindex(uid).values, "s0": pr["s0"].values, "V": pr["V"].values}).dropna()
    return pri, {"mu": float(key["mu"]), "sd": float(key["sd"]), "P": key["P"], "n_polls": int(key["n_polls"])}


def sim(n_sim: int) -> pd.DataFrame:
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"])
    R = uu.race_table(inp)
    spec = {"extras": (), "lam": 0.0}
    res = bt.state_backtest(R, n, spec)
    C = bt.prep_county(inp, R)
    priors = us.load_priors()
    base_cfg = {"election": {"state": "ga", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
                "count": {"f_complete": 0.98, "learn_turnout": True, "f_max": 0.95, "early_share": 0.64}}
    races = [("ga_gov_2022", "gov", "2022NovGen"), ("ga_sen_2022", "sen", "2022NovGen")]
    rng = np.random.default_rng(20261103)
    out = []
    checks = [c / 100 for c in range(0, 91, 5) if c > 0]
    for name, office, eid in races:
        btp = cal.by_type(eid, office)
        tot = btp[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        final = (btp["early_d"] + btp["late_d"]).sum() / tot
        win = final > 0.5
        pri_u, info = ga_state_rows(inp, R, res, C, office)
        cfg1 = {**base_cfg, "prior": {"share": "ga_pres_2020", "votes": "ga_pres_2020"}, "count": {**base_cfg["count"], "turnout_ratio": 0.8}}
        pri_1 = us.race_prior(cfg1, priors)
        base1 = us.baseline_share(pri_1)
        cfg_u = {**base_cfg, "count": {**base_cfg["count"], "turnout_ratio": 1.0}}
        variants = {
            "unified": (cfg_u, pri_u, {"swing_prior_mean": 0.0, "swing_prior_sd": info["sd"], "count_scaled_terms": True}),
            "v1 (2020 president x0.8)": (cfg1, pri_1, {}),
        }
        if info["n_polls"] > 0:
            variants["v3-style (v1 + poll swing)"] = (cfg1, pri_1, {"swing_prior_mean": info["P"] - base1,
                                                                    "swing_prior_sd": us.poll_sd_by_n(info["n_polls"]), "count_scaled_terms": True})
        for lab, (cfg, pri, extra) in variants.items():
            c0 = {**cfg, "model": {"tau": 0.04, "rho": 0.6, "sigma_mun0": 0.08, **extra}, "count": {**cfg["count"], "vote_type": True}}
            res0, _ = us.project(us._frame([]), c0, pri)
            out.append({"race": name, "variant": lab, "sim": -1, "check": 0.0, "err": res0["share_hat"] - final, "sd": res0["share_sd"],
                        "P": res0["p_win"], "win": win, "minute": 0.0})
        for s in range(n_sim):
            ev = cal.events(btp, rng)
            cum = (ev["d"] + ev["r"]).cumsum() / tot
            ups = {c: int(np.searchsorted(cum.to_numpy(), c)) + 1 for c in checks}
            for lab, (cfg, pri, extra) in variants.items():
                c0 = {**cfg, "model": {"tau": 0.04, "rho": 0.6, "sigma_mun0": 0.08, **extra}, "count": {**cfg["count"], "vote_type": True}}
                for c, up in ups.items():
                    r0, _ = us.project(cal.rows_at(ev, up), c0, pri)
                    out.append({"race": name, "variant": lab, "sim": s, "check": c, "err": r0["share_hat"] - final, "sd": r0["share_sd"],
                                "P": r0["p_win"], "win": win, "minute": float(ev["t"].iloc[up - 1])})
    df = pd.DataFrame(out)
    df["z"] = df["err"] / df["sd"]
    df["cover90"] = df["z"].abs() < 1.645
    df["p_correct"] = np.where(df["win"], df["P"], 1 - df["P"])
    df["brier"] = (df["p_correct"] - 1) ** 2
    return df


def sim_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cnt = df[df["sim"] >= 0]
    by = cnt.groupby(["variant", "check"]).agg(mae_pts=("err", lambda e: 100 * e.abs().mean()), cover90=("cover90", "mean"),
                                              p_correct=("p_correct", "mean")).reset_index()
    rows = []
    for (race, lab, s), g in cnt.groupby(["race", "variant", "sim"]):
        g = g.sort_values("check")
        ok = (g["p_correct"] >= 0.9).to_numpy()
        # first checkpoint from which P(correct winner) >= 0.9 at every later checkpoint
        first = next((i for i in range(len(ok)) if ok[i:].all()), None)
        rows.append({"race": race, "variant": lab, "sim": s, "counted_at_90": g["check"].iloc[first] if first is not None else np.nan,
                     "minutes_to_90": g["minute"].iloc[first] if first is not None else np.nan})
    t90 = pd.DataFrame(rows)
    pre = df[df["sim"] == -1][["race", "variant", "P", "p_correct", "err", "sd"]]
    summ = t90.groupby(["race", "variant"]).agg(decided=("minutes_to_90", lambda x: x.notna().mean()), minutes_to_90_median=("minutes_to_90", "median"),
                                                counted_at_90_median=("counted_at_90", "median")).reset_index()
    summ = summ.merge(pre.rename(columns={"P": "pre_P", "p_correct": "pre_p_correct", "err": "pre_err", "sd": "pre_sd"}), on=["race", "variant"])
    return by, summ


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "precount", "sim"])
    ap.add_argument("--snap", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--until")
    ap.add_argument("--configs", type=Path, default=ROOT / "configs" / "us2026_unified")
    ap.add_argument("--house", action="store_true")
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    pd.set_option("display.width", 220)
    if a.cmd == "export":
        print(export(a.snap, a.out, a.configs, a.house, a.until))
    elif a.cmd == "precount":
        print(precount(a.configs).round(4).to_string(index=False))
    else:
        df = sim(a.n)
        out = ROOT / "results" / "us_unified"
        out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / "ga2022_count_replay_raw.csv", index=False)
        by, summ = sim_tables(df)
        by.to_csv(out / "ga2022_count_replay_by_checkpoint.csv", index=False)
        summ.to_csv(out / "ga2022_count_replay_summary.csv", index=False)
        print(summ.round(3).to_string(index=False))
        print(by[by["check"].isin([0.05, 0.25, 0.5, 0.75, 0.9])].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
