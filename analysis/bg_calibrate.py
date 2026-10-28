"""Settings for the Bulgaria 2026 runoff model (configs/bg2026r2_v1.toml), chosen on the 2016 and 2021 presidential
runoffs only. No 2026 runoff data exists when this runs; the 2026 first round is not used here either.

Data: CIK open-data archives (export.zip) of the 2016 and 2021 presidential elections, read from the Internet
Archive's captures (results.cik.bg itself answers scripted requests with a Cloudflare challenge):
  2016 https://web.archive.org/web/20260420105116/https://results.cik.bg/pvrnr2016/tur1/export.zip
       (one archive with both rounds, folders 06.11.2016/ and 13.11.2016/; tur2/export.zip is the same file)
  2021 https://web.archive.org/web/20260420220400/https://results.cik.bg/pvrns2021/tur1/export.zip
       https://web.archive.org/web/20260420220447/https://results.cik.bg/pvrns2021/tur2/export.zip
Put them in $BRUNDER_DATA/raw/bulgaria/wayback/ under the names in YEARS below.

1. Transfer rule (how the eliminated candidates' first-round votes enter the MIR prior): "two_way" (they split like
   the MIR's own finalists) vs "even" (half each). Score: vote-weighted RMS, over domestic MIRs, of the runoff
   share's deviation from the prior after removing the national swing (the swing is learned from the count; the
   MIR pattern is what the prior must supply).
2. Model settings (tau, rho): no count-over-time data survives from earlier runoffs (the CIK site keeps only the
   final state; archive captures are hours apart), so the reporting order is SIMULATED from the final section
   results: within each MIR villages report before towns (the usual Bulgarian pattern, the adverse case for an
   early swing estimate), MIRs progress at random speeds, big-city MIRs slower, abroad last. A 'random order'
   variant is reported for comparison. Choose the smallest tau whose 90% interval covers the final share in >= 90%
   of checkpoints (1% .. 90% counted) in each year under the villages-first order, and among those the rho with
   the lowest mean absolute error.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/bg_calibrate.py
Writes results/bulgaria/calibration.csv and prints the choice.
"""

from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

from brunder import bulgaria as bg
from brunder import nowcast
from brunder.paths import RAW, ROOT

W = RAW / "bulgaria" / "wayback"
OUT = ROOT / "results" / "bulgaria"
# year -> (R1 archive, R1 member part, R2 archive, R2 member part, finalist A surname, finalist B surname)
YEARS = {
    2016: ("pvrnr2016_tur1_export.zip", "06.11.2016/", "pvrnr2016_tur1_export.zip", "13.11.2016/", "Радев", "Цачева"),
    2021: ("pvrns2021_tur1_export.zip", "pvr/", "pvrns2021_tur2_export.zip", None, "Радев", "Герджиков"),
}
CHECKPOINTS = [0.01, 0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90]
BIG_CITY = {"02", "03", "16", "23", "24", "25"}       # Burgas, Varna, Plovdiv city, Sofia x3
TAUS = [0.005, 0.01, 0.015, 0.02, 0.03, 0.04]
RHOS = [0.0, 0.3, 0.6, 0.8]
DRAWS = 20


def year_data(y: int) -> dict:
    f1, d1, f2, d2, a, b = YEARS[y]
    r1 = bg.export_frame(W / f1, a, b, d1)
    v2 = bg.read_export_votes(W / f2, d2)
    names = bg.read_export_candidates(W / f2, d2)
    na, nb = (next(k for k, n in names.items() if s in n.split(" и ")[0]) for s in (a, b))
    sec = v2[v2["no"].isin([na, nb])].pivot_table(index=["section", "mir"], columns="no", values="votes",
                                                   aggfunc="sum", fill_value=0).reset_index()
    sec = sec.rename(columns={na: "a", nb: "b"})
    meta = bg.read_export_sections(W / f2, d2).set_index("section")["town"]
    sec["town"] = sec["section"].map(meta).fillna(True).astype(bool)
    sec = sec[(sec["a"] + sec["b"]) > 0].reset_index(drop=True)
    return {"r1": r1, "sec": sec, "final": sec["a"].sum() / (sec["a"] + sec["b"]).sum()}


def transfer_scores(data: dict) -> pd.DataFrame:
    rows = []
    for y, d in data.items():
        r2 = d["sec"].groupby("mir")[["a", "b"]].sum()
        s2 = r2["a"] / (r2["a"] + r2["b"])
        w = r2["a"] + r2["b"]
        for rule, tr in [("two_way", {"rule": "two_way"}), ("even", {"rule": "fixed", "to_a": 0.5})]:
            p = bg.prior_from_frame(d["r1"], tr).set_index("cd_mun")
            dom = [m for m in p.index if m != bg.ABROAD]
            dev = (s2[dom] - p.loc[dom, "s0"])
            swing = np.average(dev, weights=w[dom])
            rms = float(np.sqrt(np.average((dev - swing) ** 2, weights=w[dom])))
            reg = pd.DataFrame({"dev": dev, "w": w[dom], "uf": p.loc[dom, "uf"]})
            ru = reg.groupby("uf").apply(lambda g: np.average(g["dev"], weights=g["w"]), include_groups=False)
            rows.append({"year": y, "rule": rule, "national_swing": round(float(swing), 4), "mir_rms": round(rms, 4),
                         "region_sd": round(float(np.std(ru - swing)), 4),
                         "mir_sd_within_region": round(float(np.sqrt(np.average(
                             (reg["dev"] - reg["uf"].map(ru)) ** 2, weights=reg["w"]))), 4)})
    return pd.DataFrame(rows)


def report_times(sec: pd.DataFrame, order: str, rng: np.random.Generator) -> np.ndarray:
    """A report time in [0, ~2] per section."""
    u = rng.random(len(sec))
    t = np.empty(len(sec))
    speed = {m: rng.uniform(1.0, 1.6) if m in BIG_CITY else rng.uniform(0.6, 1.2) for m in sec["mir"].unique()}
    for m, idx in sec.groupby("mir").indices.items():
        if order == "villages_first":
            key = sec["town"].to_numpy()[idx] * 1.0 + u[idx]      # villages (0..1) before towns (1..2)
        else:
            key = u[idx]
        r = key.argsort().argsort() / max(len(idx) - 1, 1)       # 0..1 within the MIR
        t[idx] = r * speed[m] + (0.5 if m == bg.ABROAD else 0.0)
    return t


def states(sec: pd.DataFrame, t: np.ndarray) -> list[tuple[float, pd.DataFrame]]:
    """MIR count states at the checkpoints (share of two-way votes counted)."""
    n = (sec["a"] + sec["b"]).to_numpy()
    o = np.argsort(t)
    cum = np.cumsum(n[o]) / n.sum()
    totals = sec.groupby("mir").size()
    out = []
    for c in CHECKPOINTS:
        k = int(np.searchsorted(cum, c)) + 1
        s = sec.iloc[o[:k]]
        g = s.groupby("mir").agg(a=("a", "sum"), b=("b", "sum"), k=("a", "size"))
        st = pd.DataFrame({"uf": [bg.MIRS[m][2] for m in g.index], "cd_mun": g.index, "n": (g["a"] + g["b"]).to_numpy(float),
                           "a": g["a"].to_numpy(float), "f": (g["k"] / totals[g.index]).to_numpy(float)})
        out.append((c, st))
    return out


def main() -> None:
    data = {y: year_data(y) for y in YEARS}
    tr = transfer_scores(data)
    print(tr.to_string(index=False))
    best_rule = tr.groupby("rule")["mir_rms"].mean().idxmin()
    transfer = {"rule": "two_way"} if best_rule == "two_way" else {"rule": "fixed", "to_a": 0.5}
    t = tr[tr["rule"] == best_rule]
    # prior sds of the model's MIR and region swings: the two years' observed values, rounded up to 0.005
    sig_mun = float(np.ceil(t["mir_sd_within_region"].mean() / 0.005) * 0.005)
    sig_uf = float(np.ceil(t["region_sd"].mean() / 0.005) * 0.005)
    print(f"transfer rule: {best_rule} (lower mean MIR RMS over 2016 and 2021); sigma_mun0={sig_mun}, "
          f"sigma_uf0={sig_uf}")

    rng = np.random.default_rng(20261101)
    sims = []                                   # (year, order, draw, checkpoint, state)
    for y, d in data.items():
        for order in ("villages_first", "random"):
            for i in range(DRAWS):
                for c, st in states(d["sec"], report_times(d["sec"], order, rng)):
                    sims.append((y, order, i, c, st))
    rows = []
    for tau, rho in itertools.product(TAUS, RHOS):
        p = nowcast.Params(tau=tau, rho=rho, sigma_mun0=sig_mun, sigma_uf0=sig_uf, unpooled=("zz",))
        for y, order, i, c, st in sims:
            prior = bg.prior_from_frame(data[y]["r1"], transfer)
            r = nowcast.nowcast(st, prior, p)
            z = (data[y]["final"] - r["share_hat"]) / r["share_sd"]
            rows.append({"tau": tau, "rho": rho, "year": y, "order": order, "draw": i, "checkpoint": c,
                         "err": r["share_hat"] - data[y]["final"], "sd": r["share_sd"], "cover90": abs(z) <= 1.645})
    R = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    R.to_csv(OUT / "calibration.csv", index=False)
    S = R.groupby(["order", "tau", "rho", "year"]).agg(cover90=("cover90", "mean"),
                                                        mae_pt=("err", lambda e: 100 * e.abs().mean())).reset_index()
    print(S.pivot_table(index=["order", "tau", "rho"], columns="year", values=["cover90", "mae_pt"]).round(3).to_string())
    vf = S[S["order"] == "villages_first"]
    ok = vf.groupby(["tau", "rho"]).filter(lambda g: (g["cover90"] >= 0.90).all())
    if ok.empty:
        sys.exit("no setting reaches 90% coverage in both years; widen the grid")
    tau = ok["tau"].min()
    rho = ok[ok["tau"] == tau].groupby("rho")["mae_pt"].mean().idxmin()
    print(f"choice: transfer={transfer} sigma_mun0={sig_mun} sigma_uf0={sig_uf} tau={tau} rho={rho}")
    print(S[(S["tau"] == tau) & (S["rho"] == rho)].round(3).to_string(index=False))
    by_cp = R[(R["tau"] == tau) & (R["rho"] == rho)].groupby(["order", "checkpoint"]).agg(
        cover90=("cover90", "mean"), mae_pt=("err", lambda e: 100 * e.abs().mean()), sd_pt=("sd", lambda s: 100 * s.mean()))
    print(by_cp.round(3).to_string())


if __name__ == "__main__":
    main()
