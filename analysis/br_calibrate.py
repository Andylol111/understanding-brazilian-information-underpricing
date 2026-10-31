"""Choose retrospective Brazil v2 settings from elections before each target.

The fixed rule and simulated-order disclosure are in docs/brazil.md. Raw section-result archives live
under BRUNDER_DATA/raw/tse_open/br_calibration; 2022 uses real BU-web receipt timestamps. Outputs:
results/brazil/calibration_{through_2018,through_2022}.csv and calibration.json.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/br_calibrate.py
"""
from __future__ import annotations

import io
import itertools
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import bweb, nowcast
from brunder.paths import ROOT

RAW = Path(os.environ.get("BRUNDER_DATA", Path.home() / "brunder-data-local")) / "raw" / "tse_open"
CACHE = RAW / "br_calibration"
OUT = ROOT / "results" / "brazil"
TAUS = [0.0, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03]
KAPPAS = [10_000.0, 100_000.0]
RHOS = [0.5, 0.8]
FRACTIONS = [0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20,
             0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
DRAWS = 4

# night: (year, round, A, B, prior year, prior round, prior A, prior B, real timestamps)
NIGHTS = {
    "2014r1": (2014, 1, "13", "45", 2010, 1, "13", "45", False),
    "2014r2": (2014, 2, "13", "45", 2014, 1, "13", "45", False),
    "2018r1": (2018, 1, "13", "17", 2014, 2, "13", "45", False),
    "2018r2": (2018, 2, "13", "17", 2018, 1, "13", "17", False),
    "2022r1": (2022, 1, "13", "22", 2018, 1, "13", "17", True),
    "2022r2": (2022, 2, "13", "22", 2022, 1, "13", "22", True),
}


def compact_sections(year: int) -> pd.DataFrame:
    """Candidate rows from TSE's national section-result archive, cached compactly."""
    pq = CACHE / f"sections_{year}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)
    zpath = CACHE / f"votacao_secao_{year}_BR.zip"
    if not zpath.exists():
        raise FileNotFoundError(f"download official TSE archive first: {zpath}")
    keep = {2010: {"13", "45"}, 2014: {"13", "45"}, 2018: {"13", "17"}}[year]
    cols = ["NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NR_ZONA", "NR_SECAO",
            "DS_CARGO", "NR_VOTAVEL", "QT_VOTOS"]
    frames = []
    with zipfile.ZipFile(zpath) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(member) as fh:
            text = io.TextIOWrapper(fh, encoding="latin-1")
            for ch in pd.read_csv(text, sep=";", usecols=cols, dtype=str, chunksize=1_000_000):
                ch["cand_n"] = ch["NR_VOTAVEL"].str.replace(r"\.0$", "", regex=True)
                ch = ch[(ch["DS_CARGO"].str.upper() == "PRESIDENTE") & ch["cand_n"].isin(keep)]
                if len(ch):
                    frames.append(pd.DataFrame({
                        "round": pd.to_numeric(ch["NR_TURNO"], downcast="integer"),
                        "uf": ch["SG_UF"].str.lower(),
                        "cd_mun": ch["CD_MUNICIPIO"].str.replace(r"\.0$", "", regex=True).str.zfill(5),
                        "zona": ch["NR_ZONA"].str.replace(r"\.0$", "", regex=True),
                        "secao": ch["NR_SECAO"].str.replace(r"\.0$", "", regex=True),
                        "cand_n": ch["cand_n"],
                        "votes": pd.to_numeric(ch["QT_VOTOS"]).astype("int32"),
                    }))
    d = pd.concat(frames, ignore_index=True)
    d.to_parquet(pq, index=False)
    return d


def section_rows(year: int, rnd: int, a: str, b: str) -> pd.DataFrame:
    if year == 2022:
        s = bweb.sections(year, rnd, a, b)
        s["n"] = s["a"] + s["b"]
        return s[["uf", "cd_mun", "zona", "secao", "received", "a", "n"]]
    d = compact_sections(year)
    d = d[(d["round"] == rnd) & d["cand_n"].isin([a, b])]
    key = ["uf", "cd_mun", "zona", "secao"]
    p = d.pivot_table(index=key, columns="cand_n", values="votes", aggfunc="sum", fill_value=0)
    for c in (a, b):
        if c not in p:
            p[c] = 0
    return p.assign(a=p[a], n=p[a] + p[b]).reset_index()[key + ["a", "n"]]


def prior(year: int, rnd: int, a: str, b: str) -> pd.DataFrame:
    s = section_rows(year, rnd, a, b)
    g = s.groupby(["uf", "cd_mun"])[["a", "n"]].sum()
    g = g[g["n"] > 0]
    return g.assign(s0=g["a"] / g["n"], V0=g["n"])[["s0", "V0"]].reset_index()


def _state(s: pd.DataFrame, selected: np.ndarray, totals: pd.Series) -> pd.DataFrame:
    x = s.iloc[selected]
    g = x.groupby(["uf", "cd_mun"]).agg(n=("n", "sum"), a=("a", "sum"), k=("n", "size"))
    g["f"] = g["k"] / totals.reindex(g.index)
    return g.drop(columns="k").reset_index()


def simulated_frames(s: pd.DataFrame, seed: int) -> list[pd.DataFrame]:
    """Four deterministic small-section-first reporting orders, matching docs/brazil.md."""
    s = s[s["n"] > 0].reset_index(drop=True)
    totals = s.groupby(["uf", "cd_mun"]).size()
    out = []
    for draw in range(DRAWS):
        rng = np.random.default_rng(seed + draw)
        score = np.log(s["n"].to_numpy()) + rng.normal(0, 0.7, len(s))
        order = np.argsort(score, kind="stable")
        for f in FRACTIONS:
            out.append(_state(s, order[:max(1, round(f * len(s)))], totals))
    return out


def real_frames(s: pd.DataFrame) -> list[pd.DataFrame]:
    s = s[s["n"] > 0].copy()
    s["visible"] = (s["received"] + pd.Timedelta("3h")).dt.tz_localize("UTC")
    totals = s.groupby(["uf", "cd_mun"]).size()
    by_t = s.groupby("visible").size().sort_index().cumsum() / len(s)
    start = by_t[by_t > 0.01].index[0]
    end = by_t[by_t >= 0.95].index[0]
    times = pd.date_range(start.ceil("1min"), end.ceil("1min"), freq="1min")
    return [_state(s, np.flatnonzero((s["visible"] <= t).to_numpy()), totals) for t in times]


def build_nights() -> dict[str, dict]:
    out = {}
    for i, (name, spec) in enumerate(NIGHTS.items()):
        year, rnd, a, b, py, pr, pa, pb, real = spec
        sections = section_rows(year, rnd, a, b)
        final = float(sections["a"].sum() / sections["n"].sum())
        frames = real_frames(sections) if real else simulated_frames(sections, 10_000 + 100 * i)
        out[name] = {"frames": frames, "prior": prior(py, pr, pa, pb), "final": final,
                     "order": "real receipt timestamps" if real else "four simulated orders"}
        print(name, len(frames), f"final={final:.5f}", out[name]["order"], flush=True)
    return out


def score_one(nights: dict[str, dict], names: list[str], tau: float, kappa: float, rho: float) -> dict:
    p = nowcast.Params(tau=tau, kappa=kappa, rho=rho)
    covs, maes, counts = [], [], []
    row = {"tau": tau, "kappa": kappa, "rho": rho}
    for name in names:
        d = nights[name]
        z, err = [], []
        for st in d["frames"]:
            o = nowcast.nowcast(st, d["prior"], p)
            if 0.01 < o["counted_frac"] <= 0.95:
                z.append((d["final"] - o["share_hat"]) / o["share_sd"])
                err.append(abs(d["final"] - o["share_hat"]))
        cov = float((np.abs(z) < 1.645).mean())
        mae = float(np.mean(err))
        row[f"coverage_{name}"] = cov
        row[f"mae_pt_{name}"] = 100 * mae
        covs.append(cov)
        maes.append(mae)
        counts.append(len(z))
    row.update(pooled_coverage=float(np.mean(covs)), worst_coverage=float(np.min(covs)),
               mean_mae_pt=100 * float(np.mean(maes)), frames=sum(counts))
    return row


def choose(g: pd.DataFrame) -> dict:
    ok = g[(g["pooled_coverage"] >= 0.90) & (g["worst_coverage"] >= 0.75)]
    if len(ok):
        tau = float(ok["tau"].min())
        pool = ok[ok["tau"] == tau]
        qualified = True
    else:
        tau = float(max(TAUS))
        pool = g[g["tau"] == tau]
        qualified = False
    best = pool.sort_values(["mean_mae_pt", "kappa", "rho"]).iloc[0]
    return {k: (bool(best[k]) if k == "qualified" else float(best[k]))
            for k in ["tau", "kappa", "rho", "pooled_coverage", "worst_coverage", "mean_mae_pt"]} | {
                "qualified": qualified}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    nights = build_nights()
    sets = {"through_2018": list(NIGHTS)[:4], "through_2022": list(NIGHTS)}
    chosen = {}
    for label, names in sets.items():
        rows = [score_one(nights, names, *x) for x in itertools.product(TAUS, KAPPAS, RHOS)]
        g = pd.DataFrame(rows)
        g.to_csv(OUT / f"calibration_{label}.csv", index=False)
        chosen[label] = choose(g)
        chosen[label]["nights"] = names
    chosen["sources"] = {
        "section_results": "TSE Open Data Portal, CC-BY",
        "timestamp_order": "2022 TSE BU-web DT_BU_RECEBIDO",
        "archive_search": "No Internet Archive CDX captures for 2018 national dadosdivweb codes 295/296",
    }
    (OUT / "calibration.json").write_text(json.dumps(chosen, indent=1))
    print(json.dumps(chosen, indent=1))


if __name__ == "__main__":
    main()
