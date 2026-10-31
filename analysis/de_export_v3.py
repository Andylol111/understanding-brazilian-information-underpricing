"""Publish the Germany 2025 v3 sensitivity (poll prior + recalibrated) and mirror v2 under stats.sensitivities, without touching
any v1 field. Run after analysis/de_export.py and analysis/decided_stats.py de2025:

    DE_OUT=results/germany/v3 uv run python analysis/de_score.py configs/de2025_v3.toml
    uv run python analysis/de_export.py && uv run python analysis/decided_stats.py de2025
    uv run python analysis/de_export_v3.py && uv run python analysis/decided_stats.py de2025

timeline.json gains P_sens_v3 / share_sens_v3 / sd_sens_v3 per race (share for the CDU/CSU-most-seats race, the CDU/CSU share of
CDU/CSU + AfD); meta.sensitivities gets the v3 entry; stats.sensitivities.v3 and .v2 carry the same metrics as v1
(analysis/sens_export.py), plus v3's pre-count P against the price at poll close and its settle time against the raw count.
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from brunder import germany as g
from brunder.paths import ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import de_export as ex  # noqa: E402
import sens_export as se  # noqa: E402

RES = ROOT / "results" / "germany"
LABEL = "v3: poll prior + recalibrated (chosen after the result was known)"
NOTE = ("The prior is the 2021 result on the 2025 constituencies tilted to the mean of the 25 polls that ended 10-22 February "
        "2025 (proportional swing per party), its weight fades as the count arrives, and the band, the Land shrinkage and the "
        "correlations were re-chosen on 2021 (real archived order) and 2017 (simulated orders) with the same kind of poll prior; "
        "before the first count page the band is the historical error of final-fortnight poll averages (2013, 2017, 2021). "
        "Nothing from the 2025 count, prices or the exit poll is an input, but it was designed after the result was known: a "
        "sensitivity, not a forecast. The model's chance exists from poll close (17:00 UTC), before any count.")


def main() -> None:
    cfg = g.load_config(ROOT / "configs" / "de2025_v3.toml")
    S3 = json.loads((RES / "v3" / "stats.json").read_text())
    R = pd.read_csv(RES / "v3" / "releases.csv", parse_dates=["t"]).sort_values("t")
    qs = {m["market"]: m for m in cfg["markets"]}
    M = dict(ex.M)
    for k, v in M.items():
        M[k] = next(m for m in qs if m.startswith(v))
    finals, series = {}, {}
    final = S3["final_share"]
    for rid, (_, _, _, pkey, _, party, _) in ex.RACES.items():
        d = pd.DataFrame({"t": R["t"], "P": R[f"P:{M[pkey]}"]})
        if rid == "most-union":
            d["share"] = R["union_two_way"]
            d["sd"] = R["union_two_way_sd"]
            finals[rid] = final["Union"] / (final["Union"] + final["AfD"])
        else:
            finals[rid] = None
        series[rid] = d
    mvr = S3["model_vs_raw"]
    pre = S3["precount"]
    pc = {rid: {"P": pre["markets"][M[pkey]]["P"], "price_at_poll_close": pre["markets"][M[pkey]]["price_at_poll_close"],
                "outcome": pre["markets"][M[pkey]]["outcome"]} for rid, (_, _, _, pkey, _, _, _) in ex.RACES.items()}
    settings = {k: cfg["model"][k] for k in ("kappa", "prior_votes", "sd_a", "sd_b", "sd_floor")}
    extra = {"settings": settings, "poll": {"polls_ending": "2025-02-10..2025-02-22", "n_polls": 25, "average": cfg["poll"]},
             "calibration_nights": ["2021 (real archived order, 2017 prior tilted to the 2021 polls)",
                                    "2017 (21 simulated orders, 2013 prior tilted to the 2017 polls)"],
             "precount": {"all_markets": {"brier_model": pre["brier_model"], "brier_market": pre["brier_market"],
                                          "n": len(pre["markets"])}, "by_race": pc},
             "model_vs_raw": {"markets_with_both": mvr["markets_with_both"], "model_later_than_raw": mvr["model_later_than_raw"],
                              "contested_model_later_than_raw": mvr["contested_model_later_than_raw"]},
             "coverage90_all_parties_1_95": S3["coverage90"]["all"],
             "brier_all_markets": S3["brier"]["all"], "brier_contested": S3["brier"]["contested"]}
    se.write_sensitivity("de2025", "v3", LABEL, "configs/de2025_v3.toml", NOTE, series, finals, "most-union", extra_stats=extra)
    se.write_v2_block("de2025", finals, "most-union")
    print("wrote v3 and mirrored v2:", ", ".join(json.loads((ROOT / "site/data/de2025/stats.json").read_text())["sensitivities"]))


if __name__ == "__main__":
    main()
