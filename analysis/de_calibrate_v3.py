"""Germany 2025 v3 sensitivity: poll-tilted prior, settings chosen on earlier nights only (nothing from 2025 is read except
the 2025 constituencies' prior, which this script does not touch).

The rule (docs/germany.md, "Sensitivity v3") was written before this script was run:

  Nights, equal weight: (A) 2021 with the real archived reporting order, prior = 2017 result on the 2021 constituencies
  tilted to the 14-day poll average of 2021; (B) 2017 with 21 simulated orders of the 299 Wahlkreise (1 small-first, 10
  noisy small-first, 10 random; a frame after every 10th Wahlkreis, counted Wahlkreise exactly identified), prior = 2013
  result on the 2017 constituencies tilted to the 2017 poll average.
  kappa x prior_votes: lowest mean absolute share error (frames with 1% < counted <= 95%, nights equal weight).
  sd_b: pooled slope of log relative error on log f_rem (clipped to 0.5..1.5).
  sd_a: smallest value of 0.02, 0.03, ..., 0.30 with pooled (equal-night) 90% coverage >= 0.90 AND worst-night coverage >=
        0.75, raised to the historical final-poll RMS log error (2013, 2017, 2021) if that is larger.
  corr: pooled standardised errors, shrunk halfway to the sum-to-zero equicorrelation. sd_floor as v1/v2.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_calibrate_v3.py
Writes results/germany/calibration_v3.json.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from brunder import germany as g
from brunder.paths import ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import de_polls as dp  # noqa: E402

OUT = ROOT / "results" / "germany"
KAPPAS = [3e5, 1e6, 3e6, 1e7, 3e7]
PRIOR_VOTES = [0.0, 1e5, 1e6, 1e7]
SD_A = [round(0.02 + 0.01 * i, 2) for i in range(29)]
CAL = ["Union", "AfD", "SPD", "GRÜNE", "Die Linke", "FDP", g.OTHER]      # BSW is zero on both earlier nights
T0 = pd.Timestamp("2017-09-24 16:00", tz="UTC")


def night_2021():
    night = g.read_night(g.RAW_DE / "wayback2021")
    wk, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv")
    pv, _ = g.read_kerg2(g.RAW_DE / "open2021" / "kerg2.csv", "VorpAnzahl")
    prior = g.poll_prior(g.make_prior(pv, wk["electorate"], 0.0), dp.window_average(2021)[0])
    final = wk[g.ALL].sum() / wk["valid"].sum()
    times = sorted({*night["bund"]["t"], *night["wk"]["t"], *[t for d in night["land"].values() for t in d["t"]]})
    return [(night, times)], prior, final


def night_2017(seed: int = 2017):
    wk = g.read_btw17_kerg(g.RAW_DE / "btw17_kerg.csv")
    pv = g.read_btw17_kerg(g.RAW_DE / "btw17_kerg.csv", previous=True)
    prior = g.poll_prior(g.make_prior(pv, wk["electorate"], 0.0), dp.window_average(2017)[0])
    final = wk[g.ALL].sum() / wk["valid"].sum()
    rng = np.random.default_rng(seed)
    log_e = np.log(wk["electorate"].to_numpy())
    keys = [log_e] + [log_e + rng.normal(0, 0.5, len(wk)) for _ in range(10)] + [rng.random(len(wk)) for _ in range(10)]
    runs = []
    for key in keys:
        order = wk.index.to_numpy()[np.argsort(key, kind="stable")]
        d = wk.loc[order].copy()
        d["t"] = [T0 + pd.Timedelta(minutes=i) for i in range(len(d))]
        d["k"] = 1
        night = {"bund": pd.DataFrame({"t": pd.Series([], dtype="datetime64[ns, UTC]")}), "land": {}, "wk": d}
        runs.append((night, [d["t"].iloc[i - 1] for i in range(10, len(d) + 1, 10)]))
    return runs, prior, final


def run(runs, prior, p: g.Params) -> list[pd.DataFrame]:
    out = []
    for night, times in runs:
        rows = []
        for t in times:
            st = g.state_at(night, prior, t)
            if st is None:
                continue
            r = g.project(st, night, prior, p)
            rows.append({"t": t, "counted": r["counted"], "f_rem": r["f_rem"], **dict(zip(g.ALL, r["share"]))})
        out.append(pd.DataFrame(rows))
    return out


def window(df: pd.DataFrame, need_frem: bool = False) -> pd.DataFrame:
    m = (df["counted"] > 0.01) & (df["counted"] <= 0.95)
    if need_frem:
        m &= df["f_rem"] > 0.02
    return df[m]


def night_mae(frames: list[pd.DataFrame], final: pd.Series) -> float:
    return float(np.mean([window(f)[CAL].sub(final[CAL]).abs().to_numpy().mean() for f in frames]))


def main() -> None:
    nights = {"2021 (real order)": night_2021(), "2017 (simulated orders)": night_2017()}
    OUT.mkdir(parents=True, exist_ok=True)
    grid = {}
    cache = {}
    for kappa in KAPPAS:
        for pv in PRIOR_VOTES:
            p = g.Params(kappa=kappa, prior_votes=pv)
            maes = {}
            fr = {}
            for name, (runs, prior, final) in nights.items():
                fr[name] = run(runs, prior, p)
                maes[name] = night_mae(fr[name], final)
            cache[(kappa, pv)] = fr
            grid[(kappa, pv)] = float(np.mean(list(maes.values())))
            print(f"kappa {kappa:>10,.0f} prior_votes {pv:>10,.0f} mean MAE {grid[(kappa, pv)]:.5f}  "
                  + "  ".join(f"{n}: {m:.5f}" for n, m in maes.items()), flush=True)
    kappa, prior_votes = min(grid, key=grid.get)
    frames = cache[(kappa, prior_votes)]
    print("chosen kappa", kappa, "prior_votes", prior_votes)

    # pooled equal-night arrays: rows of (night, order) frames inside the window, weight 1 / rows of its night
    rec = []
    for name, (runs, prior, final) in nights.items():
        sel = [window(f, True) for f in frames[name]]
        n = sum(len(s) for s in sel)
        for s in sel:
            rec.append((name, s, final, 1.0 / n))
    x = np.concatenate([np.log(s["f_rem"].to_numpy())[:, None].repeat(len(CAL), 1).ravel() for _, s, _, _ in rec])
    y = np.concatenate([np.log(np.clip((s[CAL].sub(f[CAL]).abs() / s[CAL].clip(lower=1e-4)).to_numpy(), 1e-6, None)).ravel()
                        for _, s, f, _ in rec])
    w = np.concatenate([np.full(len(s) * len(CAL), wt) for _, s, _, wt in rec])
    sd_b = float(np.clip(np.polyfit(x, y, 1, w=np.sqrt(w))[0], 0.5, 1.5))

    cover = {}
    for a in SD_A:
        per = {}
        for name, s, f, wt in rec:
            sd = a * s[CAL] * s["f_rem"].to_numpy()[:, None] ** sd_b
            inside = (s[CAL].sub(f[CAL]).abs() <= 1.645 * sd).to_numpy()
            per.setdefault(name, []).append((inside.mean() * len(s), len(s)))
        by_night = {k: sum(c for c, _ in v) / sum(n for _, n in v) for k, v in per.items()}
        cover[a] = {"pooled_equal_night": float(np.mean(list(by_night.values()))), "worst_night": float(min(by_night.values())),
                    "by_night": by_night}
    a_grid = next((a for a in SD_A if cover[a]["pooled_equal_night"] >= 0.90 and cover[a]["worst_night"] >= 0.75), SD_A[-1])
    pe = dp.poll_errors()
    poll_rms = float(np.sqrt((pe["log_err"] ** 2).mean()))
    sd_a = max(a_grid, round(poll_rms, 3))
    print("sd_b", round(sd_b, 3), "sd_a grid", a_grid, "poll rms", round(poll_rms, 4), "-> sd_a", sd_a)
    for a in SD_A[:14]:
        print(f"  a {a:.2f} pooled {cover[a]['pooled_equal_night']:.3f} worst {cover[a]['worst_night']:.3f}", cover[a]["by_night"])

    zs, ws = [], []
    for name, s, f, wt in rec:
        z = (f[CAL] - s[CAL]) / (sd_a * s[CAL].clip(lower=0.01) * s["f_rem"].to_numpy()[:, None] ** sd_b)
        zs.append(z.to_numpy())
        ws.append(np.full(len(z), wt))
    Z, W = np.concatenate(zs), np.concatenate(ws)
    C = np.cov(Z.T, aweights=W)
    sd = np.sqrt(np.diag(C))
    C = C / np.outer(sd, sd)
    k = len(CAL)
    eq = np.full((k, k), -1.0 / (k - 1))
    np.fill_diagonal(eq, 1.0)
    corr = 0.5 * C + 0.5 * eq

    night21 = nights["2021 (real order)"][0][0][0]
    page = night21["bund"].iloc[-1]
    pshare = page[g.ALL].astype(float) / page["valid"]
    final21 = nights["2021 (real order)"][2]
    sd_floor = max(float((pshare - final21).abs().max()), 0.0001)
    out = {"retrospective_sensitivity": True, "nights": list(nights), "kappa": kappa, "prior_votes": prior_votes,
           "mae_grid": {f"{k_:.0f}|{v:.0f}": m for (k_, v), m in grid.items()}, "sd_b": round(sd_b, 3),
           "sd_a_grid_rule": a_grid, "poll_rms_log_error": round(poll_rms, 4), "sd_a": sd_a, "sd_floor": round(sd_floor, 5),
           "coverage90_by_sd_a": {str(a): v for a, v in cover.items()},
           "poll_errors": pe.round(4).to_dict("records"), "corr_parties": CAL, "corr": np.round(corr, 3).tolist(),
           "n_frames": {n: int(sum(len(s) for nm, s, _, _ in rec if nm == n)) for n in nights},
           "poll_average_2021": dp.window_average(2021)[0], "poll_average_2017": dp.window_average(2017)[0]}
    (OUT / "calibration_v3.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k_: v for k_, v in out.items() if k_ not in ("corr", "coverage90_by_sd_a", "mae_grid", "poll_errors")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
