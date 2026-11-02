"""Blend component 6 (docs/us_blend.md 5.6): House v2 starting estimate with the registered House v1 count settings vs registered v2,
on Georgia-style SIMULATED House nights (2022 and 2024 districts, official certified results by vote type).

    BRUNDER_DATA=~/brunder-data-local uv run python -W ignore analysis/us_blend_house_count.py [--draws 200] [--jobs 4]

Outputs: data_public/us_blend_tests_housecount.csv (the one Holm-family test), data_public/us_blend_housecount_ga.csv (per scope x
checkpoint x variant: mean absolute margin error, 90 % coverage, paired difference), data_public/us_blend_housecount_ga_districts.csv
(per district-cycle). All of it is certified Georgia data and simulation: publishable.

What differs between the registered House configs (configs/us2026_house/ga-*_v1.toml, _v2.toml; read, never written):
  [prior]  v2 adds generic_ballot_shift; [model] v2 adds swing_prior_mean (= shift) and swing_prior_sd = 0.052; everything else equal;
  [count]  v1: vote_type = true, early_share = 0.64 (GA); v2: vote_type = false (early_share left at the CountRules default 0.6, unused).
Comparator = registered v2 ([model] and [count] of the v2 file). Candidate = the same [model] (the v2 starting estimate: prior mean =
2020-president lean of the district + generic-ballot shift of the cycle, sd 0.052) with the v1 [count] (vote-type units, early_share 0.64).
The only change between the two arms is the [count] block; turnout_ratio is the cycle's convention (0.80 midterm vs the previous
president, 1.0 presidential vs the previous president; us_benchmark_count.cycle_ratio) in both arms.

Starting estimate of a district (same for both arms, known before the night):
  district lean   2024: the registered House panel's pres_prev_d2 / pres_prev_dem+rep (2020 president on the 2024 lines; the House v2
                  backtest's own input). 2022: the panel has NONE (Georgia redrew in 2021; analysis/us_unified_house.py excludes it),
                  so 2020 president county totals are split over the county's 2022 districts like the county's 2022 House votes (the
                  repo's 'county_split_by_house18' method for PA 2018; uses only the county's district turnout weights).
  generic shift   gb_d of the cycle = generic-ballot average minus the previous presidential two-party share (us_unified_house_2026.house_table).
  units           the district's counties (a split county: its part), s0 = county 2020 president share moved so the vote-weighted mean
                  equals the district lean; V = the county's 2020 two-party votes x its share of the county's House votes in this
                  district, scaled to the district total.
Count order: analysis/us_calibrate.events / rows_at (early + mail first, Election Day in 4 chunks), seed = crc32(scope|draw) with
scope = 'ga<year>_house_<district>', identical draws for both arms (paired); checkpoints 10, 25, 50, 75 % of the district's final
two-party vote. Metric: absolute error of the projected margin (2 x share error, in points); coverage = |z| < 1.645 with the
model's own share sd.

LIMITATION (simulation-only): one state's 14 districts per cycle, count orders from one generator, vote-type split of Georgia; the 200
draws of a district share its certified results, so draws are not independent: the draw bootstrap (rule 4) is optimistic and the
district-cluster interval reported alongside is the honest one. Even a pass cannot show a real-night effect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_blend_common as bc                       # noqa: E402
import us_calibrate as cal                         # noqa: E402
import us_unified_house_2026 as uh                 # noqa: E402
from brunder import us, us_unified as uu           # noqa: E402

PUB = us.ROOT_DIR / "data_public"
CFG_DIR = us.ROOT_DIR / "configs" / "us2026_house"
CACHE = us.US_RAW / "house_night" / "ga"
CHECKS = (0.10, 0.25, 0.50, 0.75)
CYCLES = {2022: ("2022NovGen", 0.80), 2024: ("2024NovGen", 1.0)}      # eid, turnout_ratio vs the previous president (cycle_ratio)
ARMS = ("registered_v2", "v2_start_v1_count")
Z90 = 1.645
API = "https://results.sos.ga.gov/results/public/api/elections/Georgia"
_HOUSE = re.compile(r"^\s*US House(?: of Representatives)?\s*[-,]?\s*Dist(?:rict)?\.?\s*0*(\d+)\s*$", re.I)


# ------------------------------------------------------------------ registered settings (read only)


def configs() -> tuple[dict, dict, dict]:
    """[model] of v2, [count] of v1 and v2: asserted identical across all Georgia House configs."""
    v1 = [tomllib.loads(p.read_text()) for p in sorted(CFG_DIR.glob("ga-*_v1.toml"))]
    v2 = [tomllib.loads(p.read_text()) for p in sorted(CFG_DIR.glob("ga-*_v2.toml"))]
    assert v1 and v2 and len(v1) == len(v2)
    for s in (v1, v2):
        assert all(c["model"] == s[0]["model"] and c["count"] == s[0]["count"] for c in s)
    assert {k: v for k, v in v1[0]["model"].items()} == {k: v for k, v in v2[0]["model"].items()
                                                         if not k.startswith("swing_prior")}
    return v2[0]["model"], v1[0]["count"], v2[0]["count"]


# ------------------------------------------------------------------ certified Georgia House data by vote type


def house_items(eid: str) -> dict[int, bytes]:
    data = json.loads(us._fetch(f"{API}/{eid}/data", us.US_RAW / "ga" / f"{eid}_data.json"))
    out = {}
    for b in data["ballotItems"]:
        m = _HOUSE.match(b["name"][0]["text"])
        if m:
            out[int(m.group(1))] = us._fetch(f"{API}/{eid}/data/ballot-item/{b['id']}", CACHE / f"{eid}_{b['id']}.json")
    return out


def by_type_district(eid: str) -> dict[int, pd.DataFrame]:
    """district -> unit_id, early_d, early_r, late_d, late_r (county parts; the cal.by_type layout)."""
    out = {}
    for cd, blob in sorted(house_items(eid).items()):
        rows = us.parse_ga(blob, "ga")
        rows = rows[(rows["level"] == "county") & rows["party"].isin(["DEM", "REP"])]
        g = rows.pivot_table(index="unit_id", columns="party", values=["votes", "early"], aggfunc="sum", fill_value=0)
        out[cd] = pd.DataFrame({"early_d": g[("early", "DEM")], "early_r": g[("early", "REP")],
                                "late_d": g[("votes", "DEM")] - g[("early", "DEM")],
                                "late_r": g[("votes", "REP")] - g[("early", "REP")]}).reset_index()
    return out


# ------------------------------------------------------------------ starting estimate


def starting_estimates(bts: dict[int, dict[int, pd.DataFrame]]) -> dict:
    """(t, cd) -> dict(pri = unit_id, name, s0, V (before turnout_ratio), mu_v2 = lean + shift, lean)."""
    inp = uu.load_inputs()
    n = uu.nat_table(inp["nat"]).set_index("cycle")
    gb = (n["gb_avg"] - n["pres_d2_last"]).to_dict()
    panel = uh.house_table(n.reset_index())
    panel = panel[(panel["st"].str.upper() == "GA") & (panel["t"] == 2024)].set_index("did")
    cp = us.load_priors()
    cp = cp[cp["source"] == "ga_pres_2020"].set_index("unit_id")
    share_c = cp["dem"] / (cp["dem"] + cp["rep"])
    vot_c = cp["dem"] + cp["rep"]
    out = {}
    for t, bt_by_cd in bts.items():
        tot = {}                                                       # county two-party House votes over all districts
        for cd, bt in bt_by_cd.items():
            for u, v in zip(bt["unit_id"], bt[["early_d", "early_r", "late_d", "late_r"]].sum(axis=1)):
                tot[u] = tot.get(u, 0.0) + v
        for cd, bt in bt_by_cd.items():
            u = bt["unit_id"]
            h = bt[["early_d", "early_r", "late_d", "late_r"]].sum(axis=1).to_numpy()
            w = h / np.array([tot[x] for x in u])
            V0 = (vot_c.reindex(u).to_numpy() * w)
            s0 = share_c.reindex(u).to_numpy()
            assert not np.isnan(V0).any() and not np.isnan(s0).any(), (t, cd)
            lean0 = float((V0 * s0).sum() / V0.sum())
            did = f"GA-{cd:02d}"
            if t == 2024:
                row = panel.loc[did]
                lean, vtot = float(row["pres_prev_d2"]), float(row["pres_prev_dem"] + row["pres_prev_rep"])
            else:
                lean, vtot = lean0, float(V0.sum())
            s0 = np.clip(s0 + (lean - lean0), 0.01, 0.99)
            s0 = np.clip(s0 + lean - float((V0 * s0).sum() / V0.sum()), 0.01, 0.99)
            pri = pd.DataFrame({"unit_id": u.to_numpy(), "name": u.to_numpy(), "s0": s0, "V": V0 * vtot / V0.sum()})
            out[(t, cd)] = {"pri": pri, "lean": lean, "gb": float(gb[t]), "mu_v2": float(np.clip(lean + gb[t], 0.01, 0.99)),
                            "lean_unit_mean": float((pri["V"] * pri["s0"]).sum() / pri["V"].sum())}
    return out


# ------------------------------------------------------------------ simulated nights


def arm_cfgs(t: int, gbshift: float, model: dict, c1: dict, c2: dict) -> dict[str, dict]:
    ratio = CYCLES[t][1]
    base = {"election": {"state": "ga", "race": "x", "a": {"party": "DEM"}, "b": {"party": "REP"}}, "prior": {},
            "model": {**model, "swing_prior_mean": gbshift}}
    return {"registered_v2": {**base, "count": {**c2, "turnout_ratio": ratio}},
            "v2_start_v1_count": {**base, "count": {**c1, "turnout_ratio": ratio}}}


def task(args: tuple) -> list[dict]:
    t, cd, bt, pri, cfgs, draw = args
    scope = f"ga{t}_house_GA-{cd:02d}"
    rng = np.random.default_rng(zlib.crc32(f"{scope}|{draw}".encode()))
    ev = cal.events(bt, rng)
    tot = float(bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum())
    final = float((bt["early_d"] + bt["late_d"]).sum() / tot)
    cum = ((ev["d"] + ev["r"]).cumsum() / tot).to_numpy()
    out = []
    for c in CHECKS:
        up = int(np.searchsorted(cum, c)) + 1
        rows = cal.rows_at(ev, up)
        for arm, cfg in cfgs.items():
            r, _ = us.project(rows, cfg, pri)
            out.append({"year": t, "district": f"GA-{cd:02d}", "draw": draw, "check": c, "arm": arm, "err": r["share_hat"] - final,
                        "sd": r["share_sd"], "P": r["p_win"], "win": final > 0.5, "counted": float(cum[up - 1])})
    return out


# ------------------------------------------------------------------ analysis


def cluster_boot(dd: pd.DataFrame, b: int, rng: np.random.Generator) -> tuple[float, float, float]:
    """Mean paired gain with a district-cluster bootstrap (districts resampled within cycle; each district-cycle = its draw mean)."""
    tot = np.zeros(b)
    n = len(dd)
    for _, g in dd.groupby("year"):
        x = g["gain"].to_numpy()
        tot += x[rng.integers(0, len(x), (b, len(x)))].sum(1)
    m = tot / n
    return float(dd["gain"].mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def paired_ci(delta: np.ndarray, year: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    m, lo, hi, _ = bc.boot_delta(delta, year, rng)
    return m, lo, hi


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    model, c1, c2 = configs()
    print("registered [model] (v2):", model)
    print("registered [count] v1:", c1)
    print("registered [count] v2:", c2)
    bts = {t: by_type_district(eid) for t, (eid, _) in CYCLES.items()}
    se = starting_estimates(bts)
    info = []
    for (t, cd), s in sorted(se.items()):
        bt = bts[t][cd]
        tot = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        fin = (bt["early_d"] + bt["late_d"]).sum() / tot
        info.append({"year": t, "district": f"GA-{cd:02d}", "units": len(bt), "two_party_votes": int(tot), "certified_d2": fin,
                     "lean": s["lean"], "gb_shift": s["gb"], "start_mu_v2": s["mu_v2"], "start_err_pts": 100 * (s["mu_v2"] - fin),
                     "early_share_true": float((bt["early_d"] + bt["early_r"]).sum() / tot)})
    info = pd.DataFrame(info)
    print(info.round(4).to_string(index=False), flush=True)
    tasks = []
    for (t, cd), s in sorted(se.items()):
        cfgs = arm_cfgs(t, s["gb"], model, c1, c2)
        tasks += [(t, cd, bts[t][cd], s["pri"], cfgs, d) for d in range(a.draws)]
    with ProcessPoolExecutor(a.jobs) as ex:
        res = [r for rs in ex.map(task, tasks, chunksize=4) for r in rs]
    df = pd.DataFrame(res)
    df["abs_margin_err_pts"] = 200 * df["err"].abs()
    df["cover90"] = (df["err"].abs() / df["sd"] < Z90).astype(float)
    df["sd_margin_pts"] = 200 * df["sd"]

    # paired wide table per (year, district, draw, check)
    w = df.pivot_table(index=["year", "district", "draw", "check"], columns="arm",
                       values=["abs_margin_err_pts", "cover90", "sd_margin_pts"]).reset_index()
    w.columns = ["_".join(c).strip("_") for c in w.columns]
    w["gain"] = w["abs_margin_err_pts_registered_v2"] - w["abs_margin_err_pts_v2_start_v1_count"]
    dr = w.groupby(["year", "district", "draw"], as_index=False).agg(gain=("gain", "mean"))          # pooled over the 4 checkpoints
    dd = dr.groupby(["year", "district"], as_index=False).agg(gain=("gain", "mean"))
    rng = np.random.default_rng(bc.SEED)
    trow = bc.test_row("housecount", "v2 start + v1 count settings vs registered v2 (abs margin error)", "pooled",
                       dr["gain"].to_numpy(), dr["year"].to_numpy(), rng)
    pd.DataFrame([trow], columns=bc.TEST_COLS).to_csv(PUB / "us_blend_tests_housecount.csv", index=False)

    # aggregate table
    rows = []
    scopes = [("all", w)] + [(str(y), w[w["year"] == y]) for y in CYCLES]
    for sc, x in scopes:
        for ck in ("pooled",) + CHECKS:
            y = x if ck == "pooled" else x[x["check"] == ck]
            ddraw = y.groupby(["year", "district", "draw"], as_index=False)["gain"].mean() if ck == "pooled" else y
            m, lo, hi = paired_ci(ddraw["gain"].to_numpy(), ddraw["year"].to_numpy(), rng)
            for arm in ARMS:
                rows.append({"scope": sc, "check": ck, "variant": arm, "districts": y["district"].nunique() * (2 if sc == "all" else 1),
                             "draws_per_district": a.draws, "n": len(y),
                             "mean_abs_margin_err_pts": y[f"abs_margin_err_pts_{arm}"].mean(), "cover90": y[f"cover90_{arm}"].mean(),
                             "mean_sd_margin_pts": y[f"sd_margin_pts_{arm}"].mean(), "gain_pts": np.nan, "gain_lo": np.nan, "gain_hi": np.nan})
            rows.append({"scope": sc, "check": ck, "variant": "gain (registered - candidate; + = candidate better)",
                         "districts": y["district"].nunique() * (2 if sc == "all" else 1), "draws_per_district": a.draws, "n": len(ddraw),
                         "mean_abs_margin_err_pts": np.nan, "cover90": y["cover90_v2_start_v1_count"].mean() - y["cover90_registered_v2"].mean(),
                         "mean_sd_margin_pts": np.nan, "gain_pts": m, "gain_lo": lo, "gain_hi": hi})
    agg = pd.DataFrame(rows)
    agg.round(5).to_csv(PUB / "us_blend_housecount_ga.csv", index=False)

    # per district-cycle detail + cluster interval + two-thirds rule
    det = w.groupby(["year", "district"], as_index=False).agg(
        mae_registered_v2=("abs_margin_err_pts_registered_v2", "mean"), mae_v2_start_v1_count=("abs_margin_err_pts_v2_start_v1_count", "mean"),
        cover90_registered_v2=("cover90_registered_v2", "mean"), cover90_v2_start_v1_count=("cover90_v2_start_v1_count", "mean"))
    det["gain_pts"] = det["mae_registered_v2"] - det["mae_v2_start_v1_count"]
    det = det.merge(info[["year", "district", "two_party_votes", "certified_d2", "lean", "gb_shift", "start_mu_v2", "start_err_pts", "early_share_true"]],
                    on=["year", "district"])
    det.round(5).to_csv(PUB / "us_blend_housecount_ga_districts.csv", index=False)
    cm, clo, chi = cluster_boot(dd, bc.B, np.random.default_rng(bc.SEED + 1))
    frac = float((dd["gain"] > 0).mean())
    byd = dd.groupby("district")["gain"].mean()
    print(json.dumps({"test": trow, "cluster_boot": [cm, clo, chi], "frac_district_cycles_positive": frac,
                      "frac_districts_positive_mean_over_cycles": float((byd > 0).mean()),
                      "frac_pos_by_year": {int(y): float((g["gain"] > 0).mean()) for y, g in dd.groupby("year")}}, default=float, indent=1))
    pd.set_option("display.width", 220)
    print(agg.round(4).to_string(index=False))
    print(det.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
