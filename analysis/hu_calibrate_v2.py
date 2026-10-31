"""Choose the Hungary 2026 v2 settings from every earlier night that can be replayed (docs/hungary.md, "v2").

Chosen AFTER the 2026 result was known (sensitivity); v1 (analysis/hu_calibrate.py, 2022 only) stays the headline.
The rule below was written in docs/hungary.md on 2026-10-30 before this script was first run and is not changed.

Nights (each weighted equally):
  2022  real archived order: the 9 Internet Archive copies of the NVI count (same frames, prior and final as v1).
  2018  simulated orders of the official station-level result (NVI valasztasi_eredmenyek_1990-2024.zip); prior = 2014
        district results on the same 106 districts. District and list model.
  2014  simulated orders; LIST model only (county prior = 2010 list votes by county; 2010 used another district map).
Simulated orders: stations of every district (county, for the list) in "random" order or "smallest first"
(registered voters x lognormal(0, 0.3)), 5 seeds each, the same share of stations counted everywhere:
FRACTIONS. Postal / envelope ballots are not part of the 2014 and 2018 frames.

Rule (grid of v1: TAUS x SIG_MUN x KAPPAS):
  district tau = smallest tau whose pooled coverage (mean over nights of the share of frames with 1-95% of stations
    counted whose final is inside the 90% interval) is >= 0.90 and whose worst night is >= 0.75, BOTH for the 106
    district shares (all district-frames of a night) and for the national two-way district share;
    sigma_mun0, kappa = lowest mean (over nights) absolute district error at that tau.
  list tau = smallest tau with pooled coverage >= 0.90 and worst night >= 0.75 of the national list share.
If no grid value satisfies the rule the largest tau is taken and the failure is printed.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_calibrate_v2.py
Writes results/hungary/calibration_v2.json and calibration_v2_*.csv.
"""

from __future__ import annotations

import io
import itertools
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder import nowcast as nc
from brunder.paths import RAW, ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import hu_calibrate as v1  # noqa: E402

OUT = ROOT / "results" / "hungary"
ARCH = RAW / "hungary" / "nvi_archive" / "valasztasi_eredmenyek_1990-2024"
TAUS, SIG_MUN, KAPPAS = v1.TAUS, v1.SIG_MUN, v1.KAPPAS
FRACTIONS = [0.02, 0.05, 0.10, 0.20, 0.30, 0.45, 0.60, 0.75, 0.90]
SEEDS = range(5)
SCHEMES = ("random", "small_first")
DRAWS = 2000
MIN_POOLED, MIN_WORST, Z90 = 0.90, 0.75, 1.645

A_KEYS = ["JOBBIK", "MAGYAR SZOCIALISTA", "MSZP", "DEMOKRATIKUS KOAL", "LEHET M", "LMP", "MOMENTUM", "EGYÜTT"]
B_KEYS = ["FIDESZ"]


def side(org: str) -> str:
    u = org.upper()
    if any(k in u for k in B_KEYS):
        return "B"
    if "EGYÜTTMŰKÖDÉS" in u:
        return "O"
    if any(k in u for k in A_KEYS):
        return "A"
    return "O"


# ================================================================================ official station-level finals

def read_stations(year: int, kind: str) -> pd.DataFrame:
    """One row per station and organisation: unit (hu-<county>-<district>), county, reg, key, side, votes.
    kind 'egyeni' (district votes) or 'lista' (national list votes, domestic)."""
    d = ARCH / str(year) / f"{year}_parlamenti"
    name = {(2018, "egyeni"): "Egyni_szavaz s_szkjkv.csv", (2018, "lista"): "List s_szavaz s_szkjkv.csv",
            (2014, "egyeni"): "Egyni-szavaz s-jkv.csv", (2014, "lista"): "List s-szavaz s-jkv.csv"}[(year, kind)]
    raw = (d / name).read_bytes()
    try:
        txt = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        txt = raw.decode("cp1250")
    sep = ";" if txt.split("\n", 1)[0].count(";") > 3 else ","
    x = pd.read_csv(io.StringIO(txt), sep=sep, dtype=str)
    x.columns = [c.strip("'").upper() for c in x.columns]
    org = "SZERVEZET" if kind == "egyeni" else "LISTA"
    sid = x["SZAVAZÓKÖR_AZON"].str.slice(0, -1)
    head = x[x["SZAVAZÓKÖR_AZON"].str.endswith("F")].assign(sid=lambda t: t["SZAVAZÓKÖR_AZON"].str.slice(0, -1))
    head = head.set_index("sid")[["MEGYEKÓD", "OEVK", "VÁLASZTÓPOLGÁR"]]
    body = x[x["SZAVAZAT"].notna()].assign(sid=sid)[["sid", org, "SZAVAZAT"]]
    body = body.rename(columns={org: "org", "SZAVAZAT": "votes"})
    body["votes"] = body["votes"].astype(float)
    out = body.join(head, on="sid").dropna(subset=["MEGYEKÓD"])
    out["county"] = out["MEGYEKÓD"]
    out["unit"] = "hu-" + out["MEGYEKÓD"] + "-" + out["OEVK"]
    out["reg"] = out["VÁLASZTÓPOLGÁR"].astype(float)
    out["side"] = out["org"].map(side)
    return out[["sid", "unit", "county", "reg", "org", "side", "votes"]]


def station_table(rows: pd.DataFrame, by: str) -> pd.DataFrame:
    """Per station: unit/county, reg, A, B, other votes (A/B/O)."""
    p = rows.pivot_table(index="sid", columns="side", values="votes", aggfunc="sum", fill_value=0.0)
    for c in "ABO":
        if c not in p:
            p[c] = 0.0
    g = rows.drop_duplicates("sid").set_index("sid")[["unit", "county", "reg"]]
    return g.join(p[["A", "B", "O"]]).rename_axis("sid").reset_index()


def orders(st: pd.DataFrame, by: str, scheme: str, seed: int) -> pd.Series:
    """Rank of every station within its district/county (0 = counted first)."""
    rng = np.random.default_rng(1000 * seed + (7 if scheme == "random" else 13))
    key = rng.random(len(st)) if scheme == "random" else (st["reg"].to_numpy() * np.exp(0.3 * rng.standard_normal(len(st))))
    return pd.Series(key, index=st.index).groupby(st[by]).rank(method="first").sub(1)


def frame_state(st: pd.DataFrame, by: str, rank: pd.Series, f: float) -> pd.DataFrame:
    """Counted state per unit when a share f of its stations is in (at least one)."""
    cnt = st.groupby(by)["sid"].transform("size")
    take = rank < np.maximum(np.ceil(f * cnt), 1)
    c = st[take].groupby(by).agg(a=("A", "sum"), b=("B", "sum"), o=("O", "sum"), k=("sid", "size"))
    tot = st.groupby(by).agg(N=("sid", "size"))
    d = tot.join(c).fillna(0.0)
    d["n"] = d["a"] + d["b"]
    d["f"] = d["k"] / d["N"]
    return d


# ================================================================================ priors

def prior_from_stations(st: pd.DataFrame) -> dict:
    """District prior (and county sums) from station votes of the previous election on the same districts."""
    g = st.groupby("unit").agg(A=("A", "sum"), B=("B", "sum"), O=("O", "sum"))
    votes = pd.concat([g["A"].rename("votes").reset_index().assign(name="A"),
                       g["B"].rename("votes").reset_index().assign(name="B"),
                       g["O"].rename("votes").reset_index().assign(name="O")])
    return hu._prior_tables(votes, ["A"], ["B"], "name", (0.05, 1.0))


def prior_2010_counties() -> pd.DataFrame:
    """2010 first-round list votes by county (A = MSZP, Jobbik, LMP; B = Fidesz-KDNP), as a county prior."""
    z = zipfile.ZipFile(ARCH / "2010_parlamenti.zip")
    t = z.read("2010_parlamenti/txt_1ford/lis_ert.txt").decode("cp1250").splitlines()
    rows = []
    for ln in t:
        f = [x.strip() for x in ln.split("|")]
        if len(f) > 3 and f[0].isdigit() and f[3].isdigit():
            rows.append({"county": f[0].zfill(2), "side": side(f[2]), "votes": float(f[3])})
    d = pd.DataFrame(rows).pivot_table(index="county", columns="side", values="votes", aggfunc="sum", fill_value=0.0)
    two = d["A"] + d["B"]
    return pd.DataFrame({"uf": "hu", "cd_mun": d.index, "s0": (d["A"] / two).to_numpy(), "V0": two.to_numpy()})


# ================================================================================ simulated nights

def district_night(year: int, prior: dict) -> tuple[pd.DataFrame, dict]:
    """Rows (tau, sigma, kappa, scheme, seed, f, counted, ...) per frame of a simulated district night."""
    st = station_table(read_stations(year, "egyeni"), "unit")
    fin = st.groupby("unit").agg(A=("A", "sum"), B=("B", "sum"))
    final = (fin["A"] / (fin["A"] + fin["B"])).rename("final")
    smd_nat = float(fin["A"].sum() / (fin["A"] + fin["B"]).sum())
    uf = st.groupby("unit")["county"].first()
    pd_ = prior["district"]
    assert set(pd_["cd_mun"]) == set(final.index), "district keys of prior and final differ"
    rows = []
    rng = np.random.default_rng(year)
    for scheme, seed in itertools.product(SCHEMES, SEEDS):
        rank = orders(st, "unit", scheme, seed)
        for f in FRACTIONS:
            d = frame_state(st, "unit", rank, f)
            counted = float(d["k"].sum() / d["N"].sum())
            state = pd.DataFrame({"uf": uf.reindex(d.index).to_numpy(), "cd_mun": d.index, "n": d["n"].to_numpy(),
                                  "a": d["a"].to_numpy(), "f": d["f"].to_numpy(), "R_env": 0.0})
            for tau, sg, kp in itertools.product(TAUS, SIG_MUN, KAPPAS):
                p = nc.Params(tau=tau, sigma_mun0=sg, kappa=kp)
                m, s = hu.district_draws(state, pd_, p, rng.standard_normal(DRAWS), rng, 0.0, 0.0)
                fin_d = final.reindex(m["cd_mun"]).to_numpy()
                mu, sd = s.mean(0), s.std(0)
                nf = (m["n"] + m["R"]).to_numpy()
                nat = (s * nf).sum(1) / nf.sum()
                w = (m["f"] > 0.01) & (m["f"] <= 0.95)
                rows.append({"tau": tau, "sigma_mun0": sg, "kappa": kp, "scheme": scheme, "seed": seed, "f": f,
                             "counted": counted, "n": int(w.sum()),
                             "cov": float((np.abs((fin_d - mu) / sd)[w] < Z90).sum()),
                             "ae": float(np.abs(fin_d - mu)[w].sum()),
                             "nat_z": float((smd_nat - nat.mean()) / nat.std())})
    return pd.DataFrame(rows), {"smd_nat": smd_nat}


def list_night(year: int, prior_county: pd.DataFrame) -> pd.DataFrame:
    st = station_table(read_stations(year, "lista"), "county")
    fin = st.groupby("county").agg(A=("A", "sum"), B=("B", "sum"))
    final = float(fin["A"].sum() / (fin["A"] + fin["B"]).sum())
    rows = []
    for scheme, seed in itertools.product(SCHEMES, SEEDS):
        rank = orders(st, "county", scheme, seed)
        for f in FRACTIONS:
            d = frame_state(st, "county", rank, f)
            counted_n = float(d["k"].sum() / d["N"].sum())
            L = {"nat": {}, "dom": pd.Series({"A": d["a"].sum(), "B": d["b"].sum(), "O": d["o"].sum()}),
                 "mail": pd.Series({"A": 0.0, "B": 0.0}),
                 "county": pd.DataFrame({"A": d["a"], "B": d["b"], "O": d["o"]}), "county_f": d["f"]}
            for tau in TAUS:
                s = hu.Settings(list_nowcast=nc.Params(tau=tau))
                r = hu.list_race(L, prior_county, (0.05, 1.0), "A", "B", s, 0.0)
                rows.append({"tau": tau, "scheme": scheme, "seed": seed, "f": f, "counted": r["counted"],
                             "stations": counted_n, "share": r["share"], "sd": r["sd"], "final": final,
                             "raw": r["raw"], "z": (final - r["share"]) / r["sd"]})
    return pd.DataFrame(rows)


# ================================================================================ 2022 (real order, as v1)

def night_2022() -> tuple[pd.DataFrame, pd.DataFrame, float]:
    fr = v1.frames()
    fin = v1.finals()
    prior = hu.prior_2018_for_2022(v1.A18, v1.B18, v1.MAIL18)
    night = [f for f in fr if f["list"] is not None]
    reg = hu.registered(v1.YEAR)
    env_shift = v1.envelope_shift(max(night, key=lambda f: f["t"])["list"])
    dg = v1.district_grid(fr, prior, fin, hu.district_registered(v1.YEAR), env_shift)
    lg = v1.list_grid(fr, prior, fin, env_shift, reg["env"])
    return dg, lg, env_shift


def cover_list(lg: pd.DataFrame) -> pd.Series:
    w = lg[(lg["counted"] > 0.01) & (lg["counted"] <= 0.95)]
    return w.assign(c=w["z"].abs() < Z90).groupby("tau")["c"].mean()


def pick_tau(cov: pd.DataFrame) -> tuple[float, bool]:
    """cov: rows = tau, columns = nights. Smallest tau with pooled >= MIN_POOLED and worst >= MIN_WORST."""
    ok = cov[(cov.mean(1) >= MIN_POOLED) & (cov.min(1) >= MIN_WORST)]
    return (float(ok.index.min()), True) if len(ok) else (float(cov.index.max()), False)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dg22, lg22, env_shift = night_2022()

    # --- 2018 district + list (prior: 2014 on the same districts)
    st14 = station_table(read_stations(2014, "egyeni"), "unit")
    prior14 = prior_from_stations(st14)
    d18, d18info = district_night(2018, prior14)
    l18 = list_night(2018, prior14["county"])
    # --- 2014 list (prior: 2010 list votes by county)
    l14 = list_night(2014, prior_2010_counties())
    d18.to_csv(OUT / "calibration_v2_districts_2018.csv", index=False)
    l18.to_csv(OUT / "calibration_v2_list_2018.csv", index=False)
    l14.to_csv(OUT / "calibration_v2_list_2014.csv", index=False)
    dg22.to_csv(OUT / "calibration_v2_districts_2022.csv", index=False)
    lg22.to_csv(OUT / "calibration_v2_list_2022.csv", index=False)

    # --- district model
    g18 = d18.groupby(["tau", "sigma_mun0", "kappa"])
    in18 = d18[(d18["counted"] > 0.01) & (d18["counted"] <= 0.95)]
    gi = in18.groupby(["tau", "sigma_mun0", "kappa"])
    tab = pd.DataFrame({"cov_2022": dg22.set_index(["tau", "sigma_mun0", "kappa"])["coverage90"],
                        "natcov_2022": dg22.set_index(["tau", "sigma_mun0", "kappa"])["nat_coverage90"],
                        "mae_2022": dg22.set_index(["tau", "sigma_mun0", "kappa"])["mae_pt"],
                        "cov_2018": gi["cov"].sum() / gi["n"].sum(),
                        "natcov_2018": gi["nat_z"].apply(lambda z: float((z.abs() < Z90).mean())),
                        "mae_2018": 100 * gi["ae"].sum() / gi["n"].sum()}).reset_index()
    tab["cov_pooled"] = (tab["cov_2022"] + tab["cov_2018"]) / 2
    tab["cov_worst"] = tab[["cov_2022", "cov_2018"]].min(axis=1)
    tab["natcov_pooled"] = (tab["natcov_2022"] + tab["natcov_2018"]) / 2
    tab["natcov_worst"] = tab[["natcov_2022", "natcov_2018"]].min(axis=1)
    tab["mae_mean"] = (tab["mae_2022"] + tab["mae_2018"]) / 2
    tab.to_csv(OUT / "calibration_v2_districts.csv", index=False)
    bytau = tab.groupby("tau").agg(cov_2022=("cov_2022", "mean"), cov_2018=("cov_2018", "mean"),
                                   natcov_2022=("natcov_2022", "mean"), natcov_2018=("natcov_2018", "mean"))
    # coverage varies slightly with sigma/kappa: use the best (sigma, kappa) per tau for the rule's coverage test
    ok = tab[(tab["cov_pooled"] >= MIN_POOLED) & (tab["cov_worst"] >= MIN_WORST)
             & (tab["natcov_pooled"] >= MIN_POOLED) & (tab["natcov_worst"] >= MIN_WORST)]
    met = len(ok) > 0
    tau_d = float(ok["tau"].min()) if met else float(max(TAUS))
    at = (ok if met else tab)
    at = at[at["tau"] == tau_d].sort_values("mae_mean").iloc[0]

    # --- list model
    cov_l = pd.DataFrame({"2022": cover_list(lg22), "2018": cover_list(l18), "2014": cover_list(l14)})
    tau_l, met_l = pick_tau(cov_l)

    chosen = {"district": {"tau": tau_d, "sigma_mun0": float(at["sigma_mun0"]), "kappa": float(at["kappa"]),
                           "rule_met": met,
                           "cov_2022": float(at["cov_2022"]), "cov_2018": float(at["cov_2018"]),
                           "natcov_2022": float(at["natcov_2022"]), "natcov_2018": float(at["natcov_2018"]),
                           "mae_2022_pt": float(at["mae_2022"]), "mae_2018_pt": float(at["mae_2018"])},
              "list": {"tau": tau_l, "rule_met": met_l,
                       "coverage_by_tau": {str(k): {c: float(v) for c, v in r.items()} for k, r in cov_l.iterrows()}},
              "env_shift_2022": env_shift,
              "district_by_tau_mean_over_sigma_kappa": bytau.round(3).reset_index().to_dict("records")}
    (OUT / "calibration_v2.json").write_text(json.dumps(chosen, indent=1))
    pd.set_option("display.width", 200)
    print(bytau.round(3).to_string())
    print(cov_l.round(3).to_string())
    print(json.dumps(chosen, indent=1))


if __name__ == "__main__":
    main()
