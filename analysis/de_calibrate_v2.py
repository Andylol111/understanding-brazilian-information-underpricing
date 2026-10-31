"""Germany 2025 v2 sensitivity: apply the fixed common calibration rule to usable earlier nights.

This is retrospective sensitivity analysis written after the 2025 result was known. It never reads 2025 data. The
selection rule was written in docs/germany.md before this program was run: each usable night has equal weight; sd_a is
the smallest value on the existing grid with pooled 90% coverage >= .90 and worst-night coverage >= .75 over 1--95%
counted; kappa is the grid value with the lowest mean absolute projection error; sd_b and correlations are estimated
from the pooled earlier-night errors.

The Internet Archive has a replayable 2021 sequence. Its 2017 night index has only one stamped result page and its 2013
night index has none, so neither is a usable night. The output records that availability decision.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_calibrate_v2.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from brunder import germany as g
from brunder.paths import ROOT

OUT = ROOT / "results" / "germany"
KAPPAS = [3e5, 1e6, 3e6, 1e7, 3e7]
SD_A = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0]
CAL_PARTIES = ["Union", "AfD", "SPD", "GRÜNE", "Die Linke", "FDP", g.OTHER]
AVAILABILITY = {
    "2021": {"usable": True, "source": "Internet Archive Bundeswahlleiter result pages"},
    "2017": {"usable": False, "captures": 1, "reason": "only Wahlkreis 129; no replayable sequence"},
    "2013": {"usable": False, "captures": 0, "reason": "no result-page captures during election night"},
}


def frames_2021() -> tuple[dict, pd.DataFrame, pd.Series, list[pd.Timestamp]]:
    night = g.read_night(g.RAW_DE / "wayback2021")
    wk, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv")
    previous, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv", "VorpAnzahl")
    prior = g.make_prior(previous, wk["electorate"], 0.0)
    final = wk[g.ALL].sum() / wk["valid"].sum()
    times = sorted({*night["bund"]["t"], *night["wk"]["t"],
                    *[t for d in night["land"].values() for t in d["t"]]})
    return night, prior, final, times


def run(night: dict, prior: pd.DataFrame, times: list[pd.Timestamp], p: g.Params) -> pd.DataFrame:
    rows = []
    for t in times:
        state = g.state_at(night, prior, t)
        if state is None:
            continue
        projection = g.project(state, night, prior, p)
        rows.append({"t": t, "counted": projection["counted"], "f_rem": projection["f_rem"],
                     **dict(zip(g.ALL, projection["share"]))})
    return pd.DataFrame(rows)


def main() -> None:
    # This list is deliberately explicit: an election is admitted only after its archived night is found replayable.
    night, prior, final, times = frames_2021()
    nights = {"2021": (night, prior, final, times)}
    print("usable nights", list(nights), "archive availability", AVAILABILITY)
    OUT.mkdir(parents=True, exist_ok=True)

    runs: dict[float, dict[str, pd.DataFrame]] = {}
    mae_by_kappa: dict[float, float] = {}
    for kappa in KAPPAS:
        runs[kappa] = {}
        night_mae = []
        for name, (n, pr, truth, ts) in nights.items():
            df = run(n, pr, ts, g.Params(kappa=kappa))
            runs[kappa][name] = df
            d = df[(df["counted"] >= 0.01) & (df["counted"] <= 0.95)]
            night_mae.append(float(d[CAL_PARTIES].sub(truth[CAL_PARTIES]).abs().to_numpy().mean()))
        mae_by_kappa[kappa] = float(np.mean(night_mae))
        print(f"kappa {kappa:>12,.0f} mean absolute share error {mae_by_kappa[kappa]:.6f}")
    kappa = min(KAPPAS, key=mae_by_kappa.get)

    selected: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    for name, (_, _, truth, _) in nights.items():
        df = runs[kappa][name]
        d = df[(df["counted"] >= 0.01) & (df["counted"] <= 0.95) & (df["f_rem"] > 0.02)].copy()
        selected[name] = (d, truth)

    # Relative error scale follows the existing Germany model and v1 calibration.
    ys, xs = [], []
    for d, truth in selected.values():
        error = d[CAL_PARTIES].sub(truth[CAL_PARTIES]).abs().div(d[CAL_PARTIES]).clip(lower=1e-6)
        y = np.log(error.stack())
        x = np.log(d["f_rem"]).reindex(y.index.get_level_values(0)).to_numpy()
        ys.append(y.to_numpy())
        xs.append(x)
    sd_b = float(np.clip(np.polyfit(np.concatenate(xs), np.concatenate(ys), 1)[0], 0.5, 1.5))

    coverage: dict[float, dict] = {}
    for a in SD_A:
        by_night = {}
        for name, (d, truth) in selected.items():
            error = d[CAL_PARTIES].sub(truth[CAL_PARTIES]).abs()
            sd = a * d[CAL_PARTIES] * d["f_rem"].to_numpy()[:, None] ** sd_b
            by_night[name] = float((error <= 1.645 * sd).to_numpy().mean())
        coverage[a] = {"pooled_equal_night": float(np.mean(list(by_night.values()))),
                       "worst_night": float(min(by_night.values())), "by_night": by_night}
    sd_a = next((a for a in SD_A if coverage[a]["pooled_equal_night"] >= 0.90
                 and coverage[a]["worst_night"] >= 0.75), SD_A[-1])
    print("sd_b", round(sd_b, 3), "coverage", coverage, "selected sd_a", sd_a)

    z_rows = []
    for d, truth in selected.values():
        error = d[CAL_PARTIES].sub(truth[CAL_PARTIES]) * -1
        z_rows.append(error / (sd_a * d[CAL_PARTIES].clip(lower=0.01)
                               * d["f_rem"].to_numpy()[:, None] ** sd_b))
    corr_raw = np.corrcoef(pd.concat(z_rows).to_numpy().T)
    k = len(CAL_PARTIES)
    equicorr = np.full((k, k), -1.0 / (k - 1))
    np.fill_diagonal(equicorr, 1.0)
    corr = 0.5 * corr_raw + 0.5 * equicorr

    floors = []
    for n, _, truth, _ in nights.values():
        page = n["bund"].iloc[-1]
        page_share = page[g.ALL].astype(float) / page["valid"]
        floors.append(float((page_share[CAL_PARTIES] - truth[CAL_PARTIES]).abs().max()))
    sd_floor = max(max(floors), 0.0001)

    out = {"retrospective_sensitivity": True, "availability": AVAILABILITY, "usable_nights": list(nights),
           "night_weighting": "equal", "coverage_window": "1-95% electorate counted", "kappa": kappa,
           "mae_by_kappa": {str(x): y for x, y in mae_by_kappa.items()}, "sd_a": sd_a,
           "sd_b": round(sd_b, 3), "sd_floor": round(sd_floor, 5), "coverage90_by_sd_a": coverage,
           "corr_parties": CAL_PARTIES, "corr": np.round(corr, 3).tolist(),
           "n_frames_by_night": {name: len(d) for name, (d, _) in selected.items()}}
    (OUT / "calibration_v2.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({key: value for key, value in out.items() if key != "corr"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
