"""Blend component 4 (docs/us_blend.md 5.4): statewide expected-vote LEVEL correction V' = V exp(lambda z), county pattern untouched.

Parents: the registered v1 level rule (last same-office midterm race by county, counties without it / races with < 90 % coverage: last president x 0.72;
exactly the v1 rule of analysis/us_benchmark_turnout.py) and the unified level (analysis/us_unified_backtest.predict_vstate, fitted leave-one-cycle-out).
Signals: S1 early-vote pace, S2 registration growth, S3 generic-ballot enthusiasm (see signal_*). lambda in [0,1], grid 0.05, fitted LOCO over midterm
cycles (2018, 2022) by minimising mean |log(V'/V_actual)| on the training cycles' races. Metric per race |log level error| before - after (positive = better).

  BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_blend_turnout.py

Writes data_public/us_blend_tests_turnout.csv, data_public/us_blend_turnout_levels.csv. Real-night data is not used.
This analysis was conducted using data from the Redistricting Data Hub (county panel / L2-derived inputs of the unified panel).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_blend_common as bc            # noqa: E402
import us_unified_backtest as bt        # noqa: E402
from brunder import us_unified as uu    # noqa: E402

PUB = uu.ROOT / "data_public"
MID = (2018, 2022)
LGRID = np.round(np.arange(0, 1.0001, 0.05), 2)


def unified_dir() -> Path:
    import os
    return Path(os.environ["BRUNDER_DATA"]).expanduser() / "processed" / "us2026" / "unified"


# ------------------------------------------------------------------ signals
def signal_s3(fund: pd.DataFrame, year: int) -> float:
    """Standardised generic-ballot enthusiasm: |gb_avg - 0.5| of the cycle (pre-election average, asof date in the file), standardised with the
    mean/sd of the EARLIER midterms that have a generic-ballot value (>= 2 required). NaN if not available."""
    f = fund.dropna(subset=["gb_avg"])
    cur = f[f["year"] == year]
    prev = f[f["year"] < year]
    if cur.empty or len(prev) < 2:
        return np.nan
    m = (prev["gb_avg"] - 0.5).abs()
    return float((abs(cur["gb_avg"].iloc[0] - 0.5) - m.mean()) / m.std(ddof=1))


def level_table() -> pd.DataFrame:
    inp = uu.load_inputs()
    rc = inp["rc"]
    R = uu.race_table(inp)
    C = bt.prep_county(inp, R)
    gb = bt.state_base_votes(C)
    rows = []
    for c in MID:
        vm = bt.fit_vstate(gb[gb["t"] != c])
        te = C[C["t"] == c]
        for race, d in te.groupby("race", sort=True):
            g = gb.loc[race]
            st, office = d["st"].iloc[0], d["office"].iloc[0]
            vp = d["vp_b"].to_numpy(float)
            prev = rc[(rc["year"] == c - 4) & (rc["contest"] == office) & (rc["st"] == st)].drop_duplicates("fips").set_index("fips")["v2"]
            vo = d["fips"].map(prev).to_numpy(float)
            if np.isfinite(vo).mean() >= 0.9:
                v1 = np.where(np.isfinite(vo), vo, vp * 0.72)
            else:
                v1 = vp * 0.72
            act = float(d["v2"].sum())
            vu = bt.predict_vstate(vm, float(np.nansum(vp)), float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None,
                                   True, bool(d["oy"].max() % 4 == 2))
            rows.append({"race": race, "year": c, "state": st, "office": office, "actual": act, "v1": float(np.nansum(v1)), "unified": vu,
                         "v1_ok": bool(np.isfinite(v1).all())})
    D = pd.DataFrame(rows)
    rs = inp["rs"]
    rs = rs[rs["year"].isin(MID)].assign(race=lambda x: x["year"].astype(str) + "_" + x["st"] + "_" + x["contest"].str[:3]
                                         + x["contest"].str.contains("special").astype(int).astype(str))
    stv = rs.drop_duplicates("race").set_index("race")["v2"]
    chk = D.set_index("race")["actual"] / D["race"].map(stv).set_axis(D["race"])
    bad = set(chk[(chk - 1).abs() > 0.02].index) | set(chk[chk.isna()].index)
    print("QC excluded (county sum vs statewide v2 off by > 2 % or no statewide total):", sorted(bad))
    D = D[~D["race"].isin(bad) & D["v1_ok"] & (D["actual"] > 0) & np.isfinite(D["v1"]) & (D["v1"] > 0)].reset_index(drop=True)
    return D.drop(columns="v1_ok")


def fit_lambda(le: np.ndarray, z: np.ndarray) -> float:
    """grid lambda minimising mean |le + lambda z|; le = log(V/actual) of the training races. Ties -> smaller lambda."""
    loss = [np.abs(le + l * z).mean() for l in LGRID]
    return float(LGRID[int(np.argmin(loss))])


def main() -> None:
    D = level_table()
    fund = pd.read_csv(unified_dir() / "midterm_fundamentals.csv")
    zs = {c: signal_s3(fund, c) for c in MID}
    print("S3 z:", zs, " | 2026 z:", signal_s3(fund, 2026))
    rng = np.random.default_rng(bc.SEED)
    out, tests = D.copy(), []
    for base in ("v1", "unified"):
        out["le_" + base] = np.log(out[base] / out["actual"])
    for sig in ("S1", "S2", "S3"):
        if sig != "S3":
            for base in ("v1", "unified"):
                tests.append({"component": "turnout", "test": f"{sig} level correction of {base}", "class": "not evaluable", "n": 0, "p": np.nan,
                              "cycles_eval": 0, "cycles_pos": 0})
            continue
        z = out["year"].map(zs)
        for base in ("v1", "unified"):
            le = out["le_" + base].to_numpy()
            lam = pd.Series(np.nan, index=out.index)
            for c in MID:
                tr = out["year"] != c
                tr &= z.notna()
                if tr.sum() < 30 or out.loc[out["year"] == c, :].empty or not np.isfinite(zs[c]):
                    continue
                lam[out["year"] == c] = fit_lambda(le[tr.to_numpy()], z[tr].to_numpy())
            ok = lam.notna().to_numpy()
            after = le + lam.to_numpy() * z.to_numpy()
            out[f"lambda_{base}_{sig}"] = lam
            out[f"le_{base}_{sig}"] = after
            delta = np.abs(le) - np.abs(after)
            tests.append(bc.test_row("turnout", f"{sig} level correction of {base}", "pooled", delta[ok], out["year"].to_numpy()[ok], rng))
            for c in MID:
                m = (out["year"] == c).to_numpy() & ok
                tests.append({"component": "turnout", "test": f"{sig} level correction of {base}", "class": f"cycle {c}", "n": int(m.sum()),
                              "mean_delta": float(delta[m].mean()) if m.any() else np.nan, "p": np.nan})
    T = pd.DataFrame(tests)
    for col in bc.TEST_COLS:
        if col not in T:
            T[col] = np.nan
    T = T[bc.TEST_COLS]
    # per-cycle rows are descriptive (p empty -> not in the Holm family); the family rows are the pooled / not-evaluable ones
    T.to_csv(PUB / "us_blend_tests_turnout.csv", index=False)
    lv = out.copy()
    for c in ("actual", "v1", "unified"):
        lv[c] = lv[c].round(0)
    lv = lv.round(5)
    lv.to_csv(PUB / "us_blend_turnout_levels.csv", index=False)
    pd.set_option("display.width", 220, "display.float_format", lambda v: f"{v:.4f}")
    print(T.to_string(index=False))
    for base in ("v1", "unified"):
        print(f"\n== {base}: level error log(V/actual): signed mean / mean abs, before and after S3")
        g = out.groupby("year")
        print(pd.DataFrame({"n": g.size(), "signed_before": g["le_" + base].mean(), "abs_before": g["le_" + base].apply(lambda x: x.abs().mean()),
                            "lambda": g[f"lambda_{base}_S3"].first(), "signed_after": g[f"le_{base}_S3"].mean(),
                            "abs_after": g[f"le_{base}_S3"].apply(lambda x: x.abs().mean())}))
        print("pooled", out["le_" + base].mean(), out["le_" + base].abs().mean(), out[f"le_{base}_S3"].mean(), out[f"le_{base}_S3"].abs().mean())


if __name__ == "__main__":
    main()
