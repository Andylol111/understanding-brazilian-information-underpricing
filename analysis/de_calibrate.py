"""Choose the Germany 2025 model settings from the 2021 Bundestag night only (prior: the 2017 result on the 2021
constituencies, official 'Vorperiode' columns of the 2021 kerg2.csv). Nothing from 2025 is read.

Frames: every distinct page stamp of the archived 2021 night (national, Land and Wahlkreis pages), 2021-09-26 evening.
At each, `germany.project` gives the projected final second-vote shares; the error is final minus projected.

Selection rules (fixed before the run):
  - kappa (Land swing shrinkage, pseudo-votes): the grid value with the smallest mean squared share error of the six
    big parties, averaged over frames with 5-95% of the electorate counted (equal weight per 10% counted bin).
  - sd_a, sd_b: predictive sd of a party's final share = max(sd_floor, sd_a * share * f_rem ** sd_b), f_rem = the
    expected share of valid votes still to come. sd_b = slope of log|error| on log f_rem (pooled over parties);
    sd_a = the smallest value on a grid with pooled 90% coverage >= 0.90 over frames with 1-95% counted.
  - sd_floor: the largest |national page at 299/299 - kerg2 final| second-vote share difference over the parties.
  - corr: correlation of the standardised errors across frames, shrunk halfway to the sum-to-zero equicorrelation.
Writes results/germany/calibration_2021.json (copied into configs/de2025_v1.toml).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_calibrate.py
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
BIG = ["Union", "AfD", "SPD", "GRÜNE", "Die Linke", "FDP"]       # parties that existed in 2017 and 2021


def frames_2021():
    night = g.read_night(g.RAW_DE / "wayback2021")
    wk, la = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv")
    pv, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv", "VorpAnzahl")
    prior = g.make_prior(pv, wk["electorate"], 0.0)
    final = wk[g.ALL].sum() / wk["valid"].sum()
    times = sorted({*night["bund"]["t"], *night["wk"]["t"], *[t for d in night["land"].values() for t in d["t"]]})
    return night, prior, final, times


def run(night, prior, times, p: g.Params) -> pd.DataFrame:
    rows = []
    for t in times:
        st = g.state_at(night, prior, t)
        if st is None:
            continue
        r = g.project(st, night, prior, p)
        rows.append({"t": t, "counted": r["counted"], "f_rem": r["f_rem"], **dict(zip(g.ALL, r["share"]))})
    return pd.DataFrame(rows)


def main() -> None:
    night, prior, final, times = frames_2021()
    print(f"2021 night: {len(night['bund'])} national pages, {sum(len(d) for d in night['land'].values())} Land pages, "
          f"{len(night['wk'])} Wahlkreis pages, {len(times)} frame times")
    OUT.mkdir(parents=True, exist_ok=True)
    best = None
    for kappa in KAPPAS:
        df = run(night, prior, times, g.Params(kappa=kappa))
        d = df[(df["counted"] >= 0.05) & (df["counted"] <= 0.95)]
        err = (d[BIG] - final[BIG]) ** 2
        bins = pd.cut(d["counted"], np.arange(0.05, 1.0, 0.1))
        mse = err.sum(axis=1).groupby(bins, observed=True).mean().mean()
        print(f"kappa {kappa:>12,.0f}  mean sq share error {mse:.3e}  ({len(d)} frames)")
        if best is None or mse < best[0]:
            best = (mse, kappa, df)
    _, kappa, df = best
    d = df[(df["counted"] >= 0.01) & (df["counted"] <= 0.95) & (df["f_rem"] > 0.02)]
    err = d[g.ALL].sub(final[g.ALL]).mul(-1)                                      # final - projected
    # slope of log|err| on log f_rem, pooled over the big parties, after dividing by the share
    y = np.log(np.abs(err[BIG] / d[BIG]).stack().clip(lower=1e-6))
    x = np.log(d["f_rem"]).reindex(y.index.get_level_values(0)).to_numpy()
    sd_b = float(np.clip(np.polyfit(x, y.to_numpy(), 1)[0], 0.5, 1.5))
    cover = {}
    for a in SD_A:
        sd = a * d[BIG] * d["f_rem"].to_numpy()[:, None] ** sd_b
        cover[a] = float((err[BIG].abs() <= 1.645 * sd).to_numpy().mean())
    sd_a = next((a for a in SD_A if cover[a] >= 0.90), SD_A[-1])
    print("sd_b", round(sd_b, 2), "coverage by sd_a", {k: round(v, 3) for k, v in cover.items()}, "-> sd_a", sd_a)
    z = err[g.ALL] / (sd_a * d[g.ALL].clip(lower=0.01) * d["f_rem"].to_numpy()[:, None] ** sd_b)
    C = np.corrcoef(z[BIG + [g.OTHER]].to_numpy().T)
    k = len(BIG) + 1
    eq = np.full((k, k), -1.0 / (k - 1))
    np.fill_diagonal(eq, 1.0)
    corr = 0.5 * C + 0.5 * eq
    fin_page = night["bund"].iloc[-1]
    wk, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv")
    page = fin_page[g.ALL].astype(float) / fin_page["valid"]
    sd_floor = max(float((page - final).abs().max()), 0.0001)
    out = {"kappa": kappa,  "sd_a": sd_a, "sd_b": round(sd_b, 3), "sd_floor": round(sd_floor, 5),
           "corr_parties": BIG + [g.OTHER], "corr": np.round(corr, 3).tolist(), "coverage90_by_sd_a": cover,
           "n_frames": int(len(d)), "final_page_k": int(fin_page["k"])}
    (OUT / "calibration_2021.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in out.items() if k != "corr"}, ensure_ascii=False))
    df.to_csv(OUT / "calibration_2021_frames.csv", index=False)


if __name__ == "__main__":
    main()
