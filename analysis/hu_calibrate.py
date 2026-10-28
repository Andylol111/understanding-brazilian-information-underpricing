"""Choose the Hungary 2026 model settings from the 2022 count only (prior: the 2018 result on the same districts).

Inputs: the 2022 election-night count publications the Internet Archive kept (analysis/hu_fetch.py wayback; the
official server no longer has them), the 2022 final result, and the 2018 district results the 2022 site publishes
(ElozoOevkEredmenyek). Nothing from 2026 is read.

Grid and selection rule (fixed before the run):
  - district model (brunder.nowcast equations per district): tau x sigma_mun0 x kappa. Per frame and district with
    1-95% of polling stations processed: z = (final two-way share - projected) / sd. tau = the smallest value
    whose 90% coverage of those z is >= 0.90; then sigma_mun0 and kappa minimise the mean absolute district error.
  - list model (counties + postal): tau = the smallest value whose 90% interval covers the final national two-way
    list share in every frame with 1-95% counted (5 frames, so all of them must be covered).
  - mail_return = final valid postal ballots / registered postal voters; mail_two_way = A+B share of valid postal
    list votes (2022 final).
  - envelope votes (transferred and consular voters, counted days later): env_shift = their A two-way share
    minus the station share at the last night frame (2022, national); env_sd = 0.10 by judgment.
  - turnout: envelope_rate = final envelope votes / registered transferred + consular voters (2022 final);
    sd = RMS error of the projected turnout over the night frames.
Writes results/hungary/calibration_*.csv and prints the chosen values (copied into configs/hu2026_v1.toml).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_calibrate.py
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder import nowcast as nc
from brunder.paths import ROOT

OUT = ROOT / "results" / "hungary"
YEAR = 2022
A, B, TL_A, TL_B = 1803, 1798, 950, 952          # joint opposition, Fidesz-KDNP (2022 codes)
# 2018 nominating groups whose voters formed the 2022 joint opposition (DK, Jobbik, LMP, MSZP, Momentum, Párbeszéd)
A18 = ["JOBBIK", "MSZP-PÁRBESZÉD", "DK", "LMP", "MOMENTUM", "EGYÜTT", "PÁRBESZÉD", "MSZP"]
B18 = ["FIDESZ-KDNP"]
MAIL18 = (0.05, 220_000.0)   # ASSUMPTION: 2018 postal two-way opposition share ~5%; shrunk toward the 2022 count
TAUS = [0.0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05]
SIG_MUN = [0.03, 0.06, 0.10]
KAPPAS = [10_000.0, 50_000.0, 200_000.0]
DRAWS = 2000
ENV_SD = 0.10                # judgment, not fitted: one past election behind env_shift


def envelope_shift(last: dict) -> float:
    """A two-way share of the domestic votes counted after the last night frame (envelope ballots and the last
    ~1% of stations) minus the share counted by then, 2022 national."""
    E = hu.ELECTIONS[YEAR]
    fin = hu.list_count(hu._j(YEAR, f"{E['final']}/szavossz/ListasJkv.json"))
    dA = fin["dom"][TL_A] - last["dom"][TL_A]
    dB = fin["dom"][TL_B] - last["dom"][TL_B]
    return float(dA / (dA + dB) - last["dom"][TL_A] / (last["dom"][TL_A] + last["dom"][TL_B]))


def frames() -> list[dict]:
    cands = hu.candidates(YEAR)
    out = []
    for v in hu.versions(YEAR):
        p = hu.read_version(YEAR, v, cands)
        if p is not None:
            out.append(p)
    return out


def finals() -> dict:
    E = hu.ELECTIONS[YEAR]
    U, V = hu.smd_count(hu._j(YEAR, f"{E['final']}/szavossz/OevkJkv.json"), hu.candidates(YEAR))
    st = hu.district_state(U, V, A, B)
    L = hu.list_count(hu._j(YEAR, f"{E['final']}/szavossz/ListasJkv.json"))
    tot = L["dom"].add(L["mail"], fill_value=0)
    m = L["nat"]
    return {"district": pd.Series((st["a"] / st["n"]).to_numpy(), index=st["cd_mun"]),
            "smd_nat": float(st["a"].sum() / st["n"].sum()),
            "list": float(tot[TL_A] / (tot[TL_A] + tot[TL_B])),
            "mail_return": float(m["level_ervenyes_irat"]) / float(m["vp_level"]),
            "mail_two_way": float((L["mail"][TL_A] + L["mail"][TL_B]) / L["mail"].sum()),
            "turnout": float(m["szavazott_osszesen"]) / float(m["vp_osszes"]),
            "envelope_rate": float(m["beerkezett_boritekok"]) / float(m["vp_atjel"] + m["vp_kulkepv"])}


def district_grid(fr: list[dict], prior: dict, fin: dict, reg_d: pd.DataFrame, env_shift: float) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(2022)
    for tau, sg, kp in itertools.product(TAUS, SIG_MUN, KAPPAS):
        p = nc.Params(tau=tau, sigma_mun0=sg, kappa=kp)
        z_all, err, nat_z = [], [], []
        for f in fr:
            if f["smd"] is None:
                continue
            U, V = f["smd"]
            state = hu.district_state(U, V, A, B, reg_d["env"], fin["envelope_rate"])
            m, s = hu.district_draws(state, prior["district"], p, rng.standard_normal(DRAWS), rng, env_shift, ENV_SD)
            final = fin["district"].reindex(m["cd_mun"]).to_numpy()
            mu, sd = s.mean(0), s.std(0)
            w = (m["f"] > 0.01) & (m["f"] <= 0.95)
            z_all += list(((final - mu) / sd)[w])
            err += list(np.abs(final - mu)[w])
            nf = (m["n"] + m["R"] + m["R_env"]).to_numpy()
            nat = (s * nf).sum(1) / nf.sum()
            cnt = hu.counted_share(U, reg_d)
            if 0.01 < cnt <= 0.95:
                nat_z.append((fin["smd_nat"] - nat.mean()) / nat.std())
        z = np.array(z_all)
        rows.append({"tau": tau, "sigma_mun0": sg, "kappa": kp, "n": len(z), "coverage90": float((np.abs(z) < 1.645).mean()),
                     "mean_z": float(z.mean()), "mae_pt": 100 * float(np.mean(err)),
                     "nat_frames": len(nat_z), "nat_coverage90": float((np.abs(np.array(nat_z)) < 1.645).mean()) if nat_z else np.nan})
    return pd.DataFrame(rows)


def list_grid(fr: list[dict], prior: dict, fin: dict, env_shift: float, n_env: float) -> pd.DataFrame:
    rows = []
    for tau in TAUS:
        st = hu.Settings(list_nowcast=nc.Params(tau=tau), mail_return=fin["mail_return"],
                         mail_two_way=fin["mail_two_way"], envelope_rate=fin["envelope_rate"],
                         env_shift=env_shift, env_sd=ENV_SD)
        for f in fr:
            if f["list"] is None:
                continue
            r = hu.list_race(f["list"], prior["county"], prior["mail"], TL_A, TL_B, st, n_env)
            rows.append({"tau": tau, "v": f["v"], "counted": r["counted"], "share": r["share"], "sd": r["sd"],
                         "raw": r["raw"], "final": fin["list"], "z": (fin["list"] - r["share"]) / r["sd"]})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fr = frames()
    fin = finals()
    prior = hu.prior_2018_for_2022(A18, B18, MAIL18)
    print("frames:", [(f["v"], f["smd"] is not None, f["list"] is not None) for f in fr])

    night = [f for f in fr if f["list"] is not None]
    reg = hu.registered(YEAR)
    env_shift = envelope_shift(max(night, key=lambda f: f["t"])["list"])

    dg = district_grid(fr, prior, fin, hu.district_registered(YEAR), env_shift)
    dg.to_csv(OUT / "calibration_districts.csv", index=False)
    ok = dg[dg["coverage90"] >= 0.90]
    tau_d = ok["tau"].min() if len(ok) else dg["tau"].max()
    best = dg[dg["tau"] == tau_d].sort_values("mae_pt").iloc[0]

    lg = list_grid(fr, prior, fin, env_shift, reg["env"])
    lg.to_csv(OUT / "calibration_list.csv", index=False)
    inside = lg[(lg["counted"] > 0.01) & (lg["counted"] <= 0.95)]
    cov = inside.assign(c=inside["z"].abs() < 1.645).groupby("tau")["c"].mean()
    tau_l = cov[cov >= 1.0].index.min() if (cov >= 1.0).any() else max(TAUS)

    st = hu.Settings(mail_return=fin["mail_return"], envelope_rate=fin["envelope_rate"])
    terr = []
    for f in night:
        to, _ = hu.turnout(f["list"], reg, st)
        terr.append({"v": f["v"], "projected": to, "final": fin["turnout"], "err": to - fin["turnout"]})
    te = pd.DataFrame(terr)
    te.to_csv(OUT / "calibration_turnout.csv", index=False)
    t_sd = float(np.sqrt((te["err"] ** 2).mean()))

    chosen = {"district": {"tau": float(tau_d), "sigma_mun0": float(best["sigma_mun0"]), "kappa": float(best["kappa"]),
                           "coverage90": float(best["coverage90"]), "mae_pt": float(best["mae_pt"]), "n": int(best["n"])},
              "list": {"tau": float(tau_l), "coverage_by_tau": {str(k): float(v) for k, v in cov.items()}},
              "mail_return": fin["mail_return"], "mail_two_way": fin["mail_two_way"],
              "envelope_rate": fin["envelope_rate"], "env_shift": env_shift, "env_sd": ENV_SD, "turnout_sd": t_sd,
              "final": {k: fin[k] for k in ("smd_nat", "list", "turnout")}}
    (OUT / "calibration_2022.json").write_text(json.dumps(chosen, indent=1))
    pd.set_option("display.width", 200)
    print(dg.sort_values(["tau", "mae_pt"]).to_string(index=False))
    print(lg.to_string(index=False))
    print(te.to_string(index=False))
    print(json.dumps(chosen, indent=1))


if __name__ == "__main__":
    main()
