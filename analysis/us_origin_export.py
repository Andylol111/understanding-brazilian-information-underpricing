"""Public outputs of the Hispanic-origin model: aggregate estimates, validation tables, and the midterms-page JSON.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_origin_export.py

Writes
  data_public/us_hispanic_origin_estimates.csv      region x group x cycle: estimate, 90 % interval, CVAP (aggregates only)
  data_public/us_hispanic_origin_validation.csv     survey cells (CES) and published polls next to the estimates; leave-one-cycle-out scores
  site/data/us2026_context/origin.json              the same for the midterms page (Hispanic groups; not read by any page yet)
Level and uncertainty: d2p is the ecological estimate itself.  d2p_cal moves every Hispanic-origin group of a cycle by one logit shift
so the national Hispanic value equals the CES validated-voter value (the same number for every origin and region of that cycle);
its interval adds the model error measured on the CES cells (sigma_m) and the survey's own error.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_origin_fit as M
import us_origin_frames as F
import us_origin_report as Rp

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "data_public"
SITE = ROOT / "site" / "data" / "us2026_context" / "origin.json"
YEARS = (2016, 2020, 2022, 2024)
POLLS = PUB / "us_hispanic_origin_polls.csv"
JSON_STATES_MIN_HISP = 250_000            # states listed on the page: Hispanic CVAP in 2024
JSON_AREAS = ["Miami-Dade", "Broward", "Palm Beach", "Orlando (Orange/Osceola)", "Tampa Bay (Hillsborough/Pinellas)",
              "Harris", "Bexar", "Rio Grande Valley (Hidalgo/Cameron/Starr/Willacy)", "El Paso", "Maricopa", "Clark",
              "New York City", "Hudson/Passaic/Essex/Union NJ", "Los Angeles", "Cook", "Philadelphia"]
RELIABLE_CVAP = 25_000                    # a group x region cell with fewer citizen adults than this is mostly prior


def region_type(name: str, areas: pd.DataFrame) -> str:
    if name.startswith("United States"):
        return "country"
    if name in Rp.STATE_NAME.values():
        return "state"
    if name in Rp.COMBINED:
        return "combined"
    return "area"


def poll_table(est: pd.DataFrame) -> pd.DataFrame:
    """Published polls with a split by origin next to the model (raw and level-aligned)."""
    polls = pd.read_csv(POLLS)
    where = {("Cuban", "Florida"): "Florida", ("Puerto Rican", "Florida"): "Florida", ("Hispanic", "United States"): "United States (contiguous)",
             ("Puerto Rican", "United States"): "United States (contiguous)", ("Mexican", "United States"): "United States (contiguous)",
             ("Cuban", "United States"): "United States (contiguous)", ("Cuban", "Miami-Dade"): "Miami-Dade"}
    gmap = {"Cuban": "cuban", "Puerto Rican": "puerto_rican", "Mexican": "mexican", "Hispanic": "hispanic"}
    rows = []
    for _, p in polls.iterrows():
        reg = where[(p["group"], p["geography"])]
        q = est[(est["year"] == p["year"]) & (est["region"] == reg) & (est["group"] == gmap[p["group"]])].iloc[0]
        if pd.notna(p["dem_pct"]) and pd.notna(p["rep_pct"]):
            poll, how = p["dem_pct"] / (p["dem_pct"] + p["rep_pct"]), "Democratic / (Democratic + Republican)"
        elif pd.notna(p["dem_pct"]):
            poll, how = p["dem_pct"] / 100.0, "Democratic share of all voters (Republican share not published)"
        else:
            poll, how = 1.0 - p["rep_pct"] / 100.0, "1 - Republican share of all voters (Democratic share not published)"
        rows.append({"year": p["year"], "group": p["group"], "geography": p["geography"], "poll": poll, "poll_basis": how,
                     "universe": p["universe"], "source": p["source"], "model_raw": q["d2p"], "model_raw_lo": q["d2p_lo"],
                     "model_raw_hi": q["d2p_hi"], "model_cal": q["d2p_cal"], "model_cal_lo": q["d2p_cal_lo"],
                     "model_cal_hi": q["d2p_cal_hi"]})
    t = pd.DataFrame(rows)
    t["diff_raw"] = t["model_raw"] - t["poll"]
    t["diff_cal"] = t["model_cal"] - t["poll"]
    t["poll_inside_cal_interval"] = (t["poll"] >= t["model_cal_lo"]) & (t["poll"] <= t["model_cal_hi"])
    return t


def main() -> None:
    est0 = Rp.estimates(years=YEARS)
    shift = Rp.calibration_shift(est0)
    cmp0 = Rp.compare(est0)
    c = cmp0["cells_cal"]
    s = c[(c["n"] >= Rp.MIN_N) & ~((c["region"] == "United States (contiguous)") & (c["group"] == "hispanic"))]
    resid = s["diff_cal"] ** 2 - s["ces_se"] ** 2 - s["ei_sd"] ** 2
    by_year = resid.groupby(s["year"]).mean()
    sigma_m = float(np.sqrt(max(by_year.loc[[2020, 2024]].mean(), 0.0)))
    est = Rp.estimates(years=YEARS, shift=shift, sigma_m=sigma_m)
    cmp1 = Rp.compare(est)
    # out-of-sample check of the interval: sigma_m from the 2020 cells only, coverage on the 2024 cells
    s20 = float(np.sqrt(max(by_year.loc[2020], 0.0)))
    c24 = cmp1["cells_cal"]
    c24 = c24[(c24["year"] == 2024) & (c24["n"] >= Rp.MIN_N) & ~((c24["region"] == "United States (contiguous)") & (c24["group"] == "hispanic"))]
    tot = np.sqrt(c24["ei_sd"] ** 2 + s20 ** 2 + Rp.CAL_SE ** 2 + c24["ces_se"] ** 2)
    cover24 = float((c24["diff_cal"].abs() <= 1.645 * tot).mean())
    polls = poll_table(est)
    loco = pd.read_csv(F.OUT / "loco.csv")
    loco_c = pd.read_csv(F.OUT / "loco_county2022.csv")

    areas = pd.read_csv(M.FITS / "areas.csv", dtype={"state_fips": str})
    est["region_type"] = est["region"].map(lambda r: region_type(r, areas))
    hisp = est[est["group"] == "hispanic"].set_index(["year", "region"])["cvap"]
    est["share_of_hispanic_cvap"] = [r.cvap / hisp.get((r.year, r.region), np.nan) if r.group in F.HISP else np.nan
                                     for r in est.itertuples()]
    est["validated_against_survey"] = est["group"].isin(F.HISP + ["hispanic"])
    est["reliable"] = est["cvap"] >= RELIABLE_CVAP
    cols = ["year", "region", "region_type", "group", "group_label", "cvap", "share_of_hispanic_cvap", "d2p", "d2p_lo", "d2p_hi",
            "d2p_cal", "d2p_cal_lo", "d2p_cal_hi", "turnout", "n_tracts", "validated_against_survey", "reliable"]
    out = est[cols].copy()
    out["cvap"] = out["cvap"].round(0).astype(int)
    for c_ in ["share_of_hispanic_cvap", "d2p", "d2p_lo", "d2p_hi", "d2p_cal", "d2p_cal_lo", "d2p_cal_hi", "turnout"]:
        out[c_] = out[c_].round(4)
    out = out.rename(columns={"d2p": "dem_two_party", "d2p_lo": "dem_two_party_lo90", "d2p_hi": "dem_two_party_hi90",
                              "d2p_cal": "dem_two_party_aligned", "d2p_cal_lo": "dem_two_party_aligned_lo90",
                              "d2p_cal_hi": "dem_two_party_aligned_hi90", "turnout": "turnout_of_cvap", "n_tracts": "units"})
    out.to_csv(PUB / "us_hispanic_origin_estimates.csv", index=False)

    val = {"ces_cells": cmp1["cells_cal"].assign(sigma_m=sigma_m), "polls": polls}
    pd.concat([val["ces_cells"].assign(kind="CES validated voters"), polls.assign(kind="published poll")], ignore_index=True).to_csv(
        PUB / "us_hispanic_origin_validation.csv", index=False)
    loco.to_csv(PUB / "us_hispanic_origin_loco.csv", index=False)
    loco_c.to_csv(PUB / "us_hispanic_origin_loco_county2022.csv", index=False)

    # ------------------------------------------------------------------ JSON for the midterms page
    hsp24 = est[(est["year"] == 2024) & (est["group"] == "hispanic")].set_index("region")["cvap"]
    names = ["United States (contiguous)"] + [r for r in hsp24.index if region_type(r, areas) == "state" and hsp24[r] >= JSON_STATES_MIN_HISP] \
        + [a for a in JSON_AREAS if a in hsp24.index] + [r for r in Rp.COMBINED if r in hsp24.index]
    regions = []
    for nm in dict.fromkeys(names):
        cyc = {}
        for y in YEARS:
            e = est[(est["year"] == y) & (est["region"] == nm) & (est["group"].isin(F.HISP + ["hispanic"]))]
            if e.empty:
                continue
            h = e[e["group"] == "hispanic"].iloc[0]
            groups = []
            for r in e[(e["group"] != "hispanic") & e["reliable"]].sort_values("cvap", ascending=False).itertuples():
                groups.append({"group": r.group, "label": r.group_label, "cvap": round(r.cvap), "share": round(r.share_of_hispanic_cvap, 4),
                               "d2p": round(r.d2p_cal, 3), "lo": round(r.d2p_cal_lo, 3), "hi": round(r.d2p_cal_hi, 3),
                               "d2p_raw": round(r.d2p, 3)})
            cyc[str(y)] = {"hispanic": {"cvap": round(h["cvap"]), "d2p": round(h["d2p_cal"], 3), "lo": round(h["d2p_cal_lo"], 3),
                                        "hi": round(h["d2p_cal_hi"], 3), "d2p_raw": round(h["d2p"], 3)}, "groups": groups}
        regions.append({"name": nm, "type": region_type(nm, areas), "cycles": cyc})
    loc = loco[loco["model"].isin(["one Hispanic group", "seven origin groups"])]
    doc = {
        "built": datetime.now(UTC).date().isoformat(), "kind": "estimate",
        "title": "Democratic share of the two-party vote by Hispanic origin group (ecological estimates)",
        "years": list(YEARS),
        "notes": [
            ("Estimates for places, not counts of how people voted: a regression of tract (2016, 2020, 2024) or county (2022) results "
             "on the group make-up of citizen adults."),
            ("Every Hispanic-origin value is moved by one cycle-level shift so the national Hispanic figure equals the CES validated-voter "
             "figure; the interval adds the error measured against CES cells."),
            "Groups with fewer than 25,000 citizen adults in a region are left out: their estimate is mostly the prior.",
        ],
        "validation": {
            "ces_cells_scored": len(c24), "sigma_m": round(sigma_m, 3), "sigma_m_2020_only": round(s20, 3),
            "coverage_2024_with_2020_sigma": round(cover24, 2),
            "ces_by_year": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
                            for r in cmp1["calibrated"].to_dict("records")],
            "loco_skill_seven_vs_one": [{"heldout": int(h), "all": round(float(g.loc[g.model == "seven origin groups", "skill_all"].iloc[0]), 3),
                                         "one_group_all": round(float(g.loc[g.model == "one Hispanic group", "skill_all"].iloc[0]), 3),
                                         "florida": round(float(g.loc[g.model == "seven origin groups", "skill_Florida"].iloc[0]), 3),
                                         "one_group_florida": round(float(g.loc[g.model == "one Hispanic group", "skill_Florida"].iloc[0]), 3)}
                                        for h, g in loc.groupby("heldout")],
            "polls": [{"year": int(r.year), "group": r.group, "geography": r.geography, "poll": round(r.poll, 3),
                       "model": round(r.model_cal, 3), "lo": round(r.model_cal_lo, 3), "hi": round(r.model_cal_hi, 3),
                       "inside": bool(r.poll_inside_cal_interval), "source": r.source} for r in polls.itertuples()],
        },
        "sources": ["ACS 5-year B03001, B05003, B05003I, B03002 (Census Bureau)", "ACS PUMS citizen-adult ratios (Census Bureau)",
                    "Precinct results reallocated to tracts: Fekrazad, doi:10.7910/DVN/Z8TSH3 (CC0)",
                    "County results: MEDSL and state officials (unified/results_county)", "Cooperative Election Study 2016/2020/2024 (CC0)"],
        "regions": regions,
    }
    SITE.parent.mkdir(parents=True, exist_ok=True)
    SITE.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print("sigma_m", round(sigma_m, 3), "2020-only", round(s20, 3), "coverage 2024", round(cover24, 2), "shift", {k: round(v, 3) for k, v in shift.items()})
    print(polls[["year", "group", "geography", "poll", "model_raw", "model_cal", "model_cal_lo", "model_cal_hi", "poll_inside_cal_interval"]].round(3).to_string())
    print("json bytes", SITE.stat().st_size, "regions", len(regions))


if __name__ == "__main__":
    main()
