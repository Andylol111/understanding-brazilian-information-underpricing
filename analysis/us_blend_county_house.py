"""Blends 2 and 3: county-share blend (v3 + unified) and House pre-count blend (v2 + unified).

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_blend_county_house.py [county|house|all]
Re-uses the LOCO panels of analysis/us_benchmark.py (same fits; nothing is refitted differently). Writes
data_public/us_blend_tests_{county,house}.csv, us_blend_{county,house}_summary.csv, us_blend_house_districts.csv (aggregates/per-race only).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_blend_common as bc  # noqa: E402
import us_benchmark as bm  # noqa: E402
import us_unified_backtest as ub  # noqa: E402
from brunder import us_unified as uu  # noqa: E402

PUB = bm.PUB


def county_blend(inp, sw):
    """Per race: wMAE of v3 shares, unified shares, blend at grid weights (so the LOCO weight is a lookup). Same panel as bm.county_panel."""
    R = uu.race_table(inp)
    C = ub.prep_county(inp, R)
    key = sw.set_index("race")
    gb = ub.state_base_votes(C)
    rows = []
    for c in ub.TEST:
        tr, te = C[(C["t"] != c) & (C["t"].isin(ub.TEST))], C[C["t"] == c]
        lam = uu.select_lam(tr)
        sm = uu.fit_share(tr, lam)
        tm = uu.fit_turnout(tr, 0.0, uu.TURN_FEATS)
        vm = ub.fit_vstate(gb[gb["t"] != c])
        for race, d in te.groupby("race"):
            if race not in key.index or d["s2"].isna().any():
                continue
            k = key.loc[race]
            y, w = d["s2"].values, d["v2"].values
            tot = w.sum()
            polled = k["n_polls"] > 0
            vp = float(d["vp_b"].sum())
            vo = float(d["vo_b"].sum()) if d["vo_b"].notna().mean() > 0.9 else None
            Vs = ub.predict_vstate(vm, vp, vo, c % 4 == 2, bool(d["oy"].max() % 4 == 2))
            pr = uu.county_prior(d, sm, tm, float(k["unified_mu"]), Vs)
            s1 = d["so_f"].values
            if c % 4 == 2:
                V1 = np.where((d["oy"].values == c - 4) & d["vo_b"].notna().values, d["vo_b"].values, d["vp_b"].values * 0.72)
            else:
                V1 = d["vo_b"].fillna(d["vp_b"]).values
            V1 = np.nan_to_num(V1, nan=0.0) + 1e-9
            base1 = np.average(s1, weights=V1)
            shift = (k["P"] - base1) if polled else 0.0
            s3 = np.clip(s1 + shift, 0.005, 0.995)
            su = pr["s0"].values
            row = {"race": race, "t": c, "n_counties": len(d)}
            for wt in bc.GRID:
                s = wt * s3 + (1 - wt) * su
                row[f"wmae_{wt:.1f}"] = float((np.abs(s - y) * w).sum() / tot * 100)
            rows.append(row)
    return pd.DataFrame(rows)


def run_county(inp, sw):
    P = county_blend(inp, sw)
    P = P.merge(sw[["race", "C1"]], on="race")
    g = lambda w: P[f"wmae_{w:.1f}"]           # noqa: E731
    wl = bc.loco_weight(P, "t", lambda w, x: x[f"wmae_{w:.1f}"].mean())
    P["w_loco"] = wl
    P["wmae_v3"], P["wmae_unified"], P["wmae_eq"] = g(1.0), g(0.0), g(0.5)
    P["wmae_loco"] = [P.loc[i, f"wmae_{wl[i]:.1f}"] for i in P.index]
    rng = np.random.default_rng(bc.SEED + 2)
    rows, summ = [], []
    classes = [("pooled", np.ones(len(P), bool))] + [(f"C1 {k}", (P["C1"] == k).values) for k in ("low", "medium", "high")]
    for cand, col in (("blend_eq", "wmae_eq"), ("blend_loco", "wmae_loco")):
        for par in ("v3", "unified"):
            for cl, m in classes:
                x = P[m]
                rows.append(bc.test_row("county", f"{cand} vs {par}", cl, (x[f"wmae_{par}"] - x[col]).values, x["t"].values, rng))
    for cl, m in classes + [(f"cycle {c}", (P["t"] == c).values) for c in sorted(P["t"].unique())]:
        for mod in ("v3", "unified", "eq", "loco"):
            summ.append({"class": cl, "model": mod, "n": int(m.sum()), "share_wmae_pts": P.loc[m, f"wmae_{mod}"].mean()})
    pd.DataFrame(rows).to_csv(PUB / "us_blend_tests_county.csv", index=False)
    S = pd.DataFrame(summ)
    S.round(5).to_csv(PUB / "us_blend_county_summary.csv", index=False)
    print(P.groupby("t")["w_loco"].first())
    print(S.to_string(index=False))
    print(pd.DataFrame(rows).to_string(index=False))


def run_house(inp):
    d = bm.house_panel(inp)
    y = d["s2"].values
    win = (y > 0.5).astype(float)
    v2m, v2s, um, us = d["v2"].values, np.asarray(d["sd_v2r"], float), d["mu"].values, d["sd"].values

    def crps_w(w, x):
        mu, sd = bc.mix(x["v2"].values, x["sd_v2r"].values, x["mu"].values, x["sd"].values, w)
        return (bc.crps_normal(x["s2"].values, mu, sd) * 100).mean()

    d["w_loco"] = bc.loco_weight(d, "t", crps_w)
    out = {"v2": (v2m, v2s), "unified": (um, us)}
    out["blend_eq"] = bc.mix(v2m, v2s, um, us, 0.5)
    out["blend_loco"] = bc.mix(v2m, v2s, um, us, d["w_loco"].values)
    for m, (mu, sd) in out.items():
        mu, sd = np.asarray(mu, float), np.asarray(sd, float)
        p = np.clip(ndtr((mu - 0.5) / sd), 1e-4, 1 - 1e-4)
        d[f"{m}_c"] = bc.crps_normal(y, mu, sd) * 100
        d[f"{m}_b"] = (p - win) ** 2
        d[f"{m}_cov"] = (np.abs(y - mu) < 1.645 * sd).astype(float)
        d[f"{m}_mae"] = np.abs(y - mu) * 100
    rng = np.random.default_rng(bc.SEED + 3)
    rows, summ = [], []
    classes = [("pooled", np.ones(len(d), bool)), ("H1 polled", (d["H1"] == "polled").values), ("H1 unpolled", (d["H1"] == "unpolled").values)]
    for cand in ("blend_eq", "blend_loco"):
        for par in ("v2", "unified"):
            for cl, m in classes:
                x = d[m]
                rows.append(bc.test_row("house", f"{cand} vs {par}", cl, (x[f"{par}_c"] - x[f"{cand}_c"]).values, x["t"].values, rng))
    for cl, m in classes + [(f"cycle {c}", (d["t"] == c).values) for c in sorted(d["t"].unique())]:
        for mod in out:
            x = d[m]
            summ.append({"class": cl, "model": mod, "n": len(x), "crps_pts": x[f"{mod}_c"].mean(), "mae_pts": x[f"{mod}_mae"].mean(),
                         "brier": x[f"{mod}_b"].mean(), "cov90": x[f"{mod}_cov"].mean()})
    pd.DataFrame(rows).to_csv(PUB / "us_blend_tests_house.csv", index=False)
    S = pd.DataFrame(summ)
    S.round(5).to_csv(PUB / "us_blend_house_summary.csv", index=False)
    print(d.groupby("t")["w_loco"].first())
    print(S.to_string(index=False))
    print(pd.DataFrame(rows).to_string(index=False))


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    pd.set_option("display.width", 250, "display.max_rows", 400, "display.float_format", lambda v: f"{v:.4g}")
    inp = uu.load_inputs()
    if what in ("county", "all"):
        sw = bm.statewide_panel(inp)
        run_county(inp, sw)
    if what in ("house", "all"):
        run_house(inp)


if __name__ == "__main__":
    main()
