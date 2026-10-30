"""US national election-night estimate (brunder.us_national) replayed on the 2018, 2020, 2022 and 2024 nights.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_national_backtest.py [tune] [test] [report]   # default: all

Inputs: analysis/us_national_data.py tables (MEDSL county Senate/governor returns, House results by district, 538 poll
averages, generic ballot, official poll-close times). REPORTING ORDERS ARE SIMULATED: the real order of county returns
is not in the archive, so each replication draws one (county size, state close time, noise, a per-state early-count
lean) and counts votes in it. Partial counts are the county's final share plus sampling noise. This stresses the
method against an order-bias assumption; it does not reproduce the real count.

Design (docs/us_national.md):
  - covered = the 2026 live-model states with a working feed (CA TX OH GA VT NC FL PA MN NE NM AZ; SC and AK are no-count states) for Senate/governor, and the 40 House
    districts closest to even (prior) in the covered House states (TX FL NC OH GA AZ). Their count signals feed the
    national estimate; Senate/governor races there keep their own model (nowcast with the poll prior).
  - everything else gets prior (poll average / baseline + environment shift; House: previous president on the district +
    generic-ballot swing) plus the estimated national / region / state deviation.
  - horizons 1, 2, 4, 6 h after the first poll close of the night; compared with the prior alone (same structure, no count).
  - tuning (meas_inflate, sd_house_env, house prior sd) on 2018 and 2020 only; 2022 and 2024 are the test, scored with the
    frozen values. Four nights: descriptive, not a significance test.
Scores per chamber: Brier of P(control); ranked probability score over seat-count thresholds (the whole predictive
distribution); |mean seats − actual|; share of nights the 80 % band holds the actual.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us, us_national as N

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = us.ROOT_DIR / "results" / "us_national_backtest"
COVER = "ca tx oh ga vt nc fl pa mn ne nm az".split()          # SC (Clarity blocks the VM) and AK are no-count states
COVER_HOUSE = "tx fl nc oh ga az".split()
HOURS = (1, 2, 4, 6)
TUNE, TEST = (2018, 2020), (2022, 2024)
DUPLICATE_SEATS = ("ca-sen-s",)
SLOW = {"ca": 4.0, "az": 3.0, "nv": 3.0, "wa": 4.0, "or": 3.0, "ak": 3.0}       # mail-heavy states count slowly


@dataclass(frozen=True)
class Sim:
    """Assumptions of the simulated count (not estimated from real count orders)."""
    L_med: float = 1.0           # median hours from close to a county's finish
    size_slope: float = 0.30     # log hours per log county size relative to the state median (big counties are slow)
    sigma_L: float = 0.5
    sig_s: float = 0.02          # partial-count sd of a county share: sig_s·sqrt((1-f)/f)
    tau_order: float = 0.015     # sd of the state's early-count lean (counted share − final), × (1 − f)
    a_h: float = 1.3e-4          # House: partial-count variance a_h·(1−f)/f
    tau_h: float = 0.015         # House model's own order-bias allowance
    n_nowcast_tau: float = 0.02


def _rows(st: str, race: str, c: pd.DataFrame, n: np.ndarray, a: np.ndarray, f: np.ndarray) -> pd.DataFrame:
    done = f >= 0.999
    base = {"level": "county", "name": "", "race": race, "candidate": "", "early": np.nan, "ballots": np.nan,
            "registered": np.nan, "outstanding": np.nan, "units": 10, "reporting": np.where(done, 10, 0)}
    d = pd.DataFrame({**base, "unit_id": c["unit_id"].to_numpy(), "party": "DEM", "votes": a})
    r = pd.DataFrame({**base, "unit_id": c["unit_id"].to_numpy(), "party": "REP", "votes": n - a})
    out = pd.concat([d, r], ignore_index=True)
    return out[np.tile(n > 0, 2)]


def _cfg(st: str, race: str, ratio: float, sim: Sim, prior: float | None = None, sd: float | None = None,
         base: float | None = None) -> dict:
    model = {"tau": sim.n_nowcast_tau, "rho": 0.6, "sigma_mun0": 0.08, "sigma_uf0": 0.03}
    if prior is not None:
        model.update(swing_prior_mean=prior - base, swing_prior_sd=sd, count_scaled_terms=True)
    return {"election": {"state": st, "race": race, "a": {"party": "DEM"}, "b": {"party": "REP"}},
            "model": model, "count": {"turnout_ratio": ratio, "f_max": 0.95, "f_complete": 0.98, "learn_turnout": True}}


class Night:
    """One replication of one cycle: a reporting order, and the races as of any hour after the first close."""

    def __init__(self, cyc: dict, year: int, sim: Sim, seed: int):
        self.cyc, self.year, self.sim = cyc, year, sim
        rng = np.random.default_rng(seed)
        close = cyc["poll_close"].set_index("state")["close_utc"].map(pd.Timestamp)
        self.close = close
        self.t0 = close.min()
        self.bias = {st: rng.normal(0, sim.tau_order) for st in close.index}
        self.ratio = 0.72 if year % 4 == 2 else 1.0
        self.timing: dict[str, pd.DataFrame] = {}
        for key, c in cyc["counties"].items():
            m = c["total"].median()
            L = sim.L_med * SLOW.get(c["state"].iat[0], 1.0) * np.exp(
                sim.size_slope * np.log(np.maximum(c["total"], 1) / m) + rng.normal(0, sim.sigma_L, len(c)))
            self.timing[key] = pd.DataFrame({"L": L.to_numpy(), "start": 0.3 * L.to_numpy(),
                                             "eta": rng.standard_normal(len(c))}, index=c.index)
        h = cyc["house"]
        Lh = sim.L_med * h["state"].map(lambda s: SLOW.get(s, 1.0)).to_numpy() * np.exp(rng.normal(0, sim.sigma_L, len(h)))
        self.house_t = pd.DataFrame({"L": Lh, "start": 0.3 * Lh, "eta": rng.standard_normal(len(h))}, index=h.index)

    def _f(self, t: pd.DataFrame, st: str, hours: float) -> np.ndarray:
        el = hours - (self.close[st] - self.t0).total_seconds() / 3600
        return np.clip((el - t["start"].to_numpy()) / (0.7 * t["L"].to_numpy()), 0, 1)

    def races(self, hours: float, p: N.Params, own_states=COVER, extra_states=(), house_cover=None,
              house_all: bool = False) -> list[N.Race]:
        cyc, sim = self.cyc, self.sim
        R = cyc["races"]
        polled = R[R["poll_avg"].notna() & ~R["state"].isin(own_states)]
        shift = (float(np.average(polled["poll_avg"] - polled["baseline_share"], weights=1 / polled["poll_sd"] ** 2))
                 if len(polled) else 0.0)
        out: list[N.Race] = []
        for _, r in R.iterrows():
            if np.isfinite(r["poll_avg"]):
                prior, sd = float(r["poll_avg"]), max(float(r["poll_sd"]), 0.01)
            else:
                prior, sd = float(r["baseline_share"]) + shift, p.sd_unpolled
            y = v = counted = om = osd = None
            src = "prior"
            c = cyc["counties"].get(r["race_key"])
            if c is not None and (r["state"] in own_states or r["state"] in extra_states) and hours > 0:
                tm = self.timing[r["race_key"]]
                f = self._f(tm, r["state"], hours)
                if f.sum() > 0:
                    fin_s = (c["d"] / (c["d"] + c["r"])).to_numpy()
                    nfin = (c["d"] + c["r"]).to_numpy()
                    share = np.clip(fin_s + tm["eta"].to_numpy() * sim.sig_s * np.sqrt((1 - f) / np.maximum(f, 0.02))
                                    + self.bias[r["state"]] * (1 - f), 0.001, 0.999)
                    n = nfin * f
                    a = n * share
                    pri = pd.DataFrame({"unit_id": c["unit_id"].to_numpy(), "name": "", "s0": c["s0"].to_numpy(),
                                        "V": c["V0"].to_numpy()})
                    rows = _rows(r["state"], r["office"], c, n, a, f)
                    B = us.baseline_share(pri)
                    k0 = _cfg(r["state"], r["office"], self.ratio, sim)
                    res, _ = us.project(rows, k0, pri)
                    counted = res["counted_frac"]
                    y, v = res["share_hat"] - prior, res["share_sd"] ** 2
                    src = "count"
                    if r["state"] in own_states:
                        k1 = _cfg(r["state"], r["office"], self.ratio, sim, prior, sd, B)
                        res1, _ = us.project(rows, k1, pri)
                        om, osd, src = res1["share_hat"], res1["share_sd"], "own"
            out.append(N.Race(key=r["race_key"], office=r["office"], state=r["state"], prior=prior, prior_sd=sd, y=y, v=v,
                              counted=counted, own_mean=om, own_sd=osd, source=src, extra={"modeled": src == "own"}))
        H, gen = cyc["house"], cyc["generic"]
        swing = float(gen["avg"] - cyc["nat_prev_pres"])
        cover = house_cover if house_cover is not None else self.house_cover(p)
        for i, d in H.iterrows():
            prior = float(d["prior_d2"]) + swing
            y = v = counted = om = osd = None
            if d["uncontested"]:                       # one major party on the ballot: known beforehand (as live: civicAPI)
                out.append(N.Race(key=f"house:{d['district']}", office="house", state=d["state"],
                                  prior=0.999 if d["winner_party"] == "D" else 0.001, prior_sd=N.UNCONTESTED_SD,
                                  extra={"uncontested": d["winner_party"]}))
                continue
            own = d["district"] in cover
            if hours > 0 and (own or house_all):
                f = float(self._f(self.house_t.loc[[i]], d["state"], hours)[0])
                if f > 0:
                    var = sim.a_h * (1 - f) / max(f, 0.02)
                    s_obs = d["final_d2"] + self.house_t.at[i, "eta"] * np.sqrt(var) + self.bias[d["state"]] * (1 - f)
                    y, v, counted = s_obs - prior, var + (sim.tau_h * (1 - f)) ** 2, f
                    if own:
                        om, osd = s_obs, np.sqrt(v)
            out.append(N.Race(key=f"house:{d['district']}", office="house", state=d["state"], prior=prior,
                              prior_sd=p_house_sd(p), y=y, v=v, counted=counted, own_mean=om, own_sd=osd,
                              source="own" if om is not None else ("civic" if y is not None else "prior")))
        return out

    def house_cover(self, p: N.Params) -> set[str]:
        H = self.cyc["house"]
        h = H[H["state"].isin(COVER_HOUSE)].copy()
        swing = float(self.cyc["generic"]["avg"] - self.cyc["nat_prev_pres"])
        h["gap"] = (h["prior_d2"] + swing - 0.5).abs()
        return set(h.nsmallest(40, "gap")["district"])


def p_house_sd(p: N.Params) -> float:
    return 0.052                                    # replaced by the tuned value when scoring (score_cycle)


def load(year: int) -> dict:
    import us_national_data as D
    t = D.load_cycle(year)
    panel = pd.read_csv(us.ROOT_DIR / "data_public" / "us_county_panel.csv", comment="#", dtype={"fips": str})
    prev = {2018: 2016, 2020: 2016, 2022: 2020, 2024: 2020}[year]
    pv = panel[panel["year"] == prev].dropna(subset=["s_pres", "v_pres"])
    races = t["races"].copy()
    races["race_key"] = races["race_key"].astype(str)
    races = races[~races["race_key"].isin(DUPLICATE_SEATS)]          # same seat as the regular race (CA 2022, 2024)
    cc = t["counties"].dropna(subset=["s0", "V0", "d", "r"])
    cc = cc[(cc["V0"] > 0) & (cc["d"] + cc["r"] > 0)]
    counties = {k: g.reset_index(drop=True) for k, g in cc.groupby("race_key")}
    races = races[races["race_key"].isin(counties) | races["race_key"].notna()]
    hs = t["house"].dropna(subset=["final_d2", "prior_d2"]).reset_index(drop=True)
    hs["district"] = hs["district"].astype(str)
    return {"races": races.reset_index(drop=True), "counties": counties, "house": hs, "poll_close": t["poll_close"],
            "generic": t["generic"].iloc[0], "holdover": int(t["holdovers"]["d_caucus_holdover"].iloc[0]),
            "nat_prev_pres": float(np.average(pv["s_pres"], weights=pv["v_pres"])), "year": year,
            "house_unknown": t["house"][t["house"]["final_d2"].isna()]}


# ------------------------------------------------------------------------------ scoring

def thresholds(dist: list[float], ks) -> np.ndarray:
    cdf_ge = np.cumsum(np.asarray(dist)[::-1])[::-1]               # P(seats >= k)
    return np.array([cdf_ge[k] if k < len(cdf_ge) else 0.0 for k in ks])


def score(doc: dict, actual: int, need: int, ks: range) -> dict:
    pg = thresholds(doc["dist"], ks)
    act = (actual >= np.asarray(ks)).astype(float)
    return {"p": doc["p_control"], "brier": (doc["p_control"] - float(actual >= need)) ** 2,
            "rps": float(np.mean((pg - act) ** 2)), "abs_err": abs(doc["mean"] - actual),
            "band": float(doc["p10"] <= actual <= doc["p90"]), "mean": doc["mean"], "actual": actual}


def generate(year: int, sim: Sim, reps: int, extra_states=(), seed: int = 0, p: N.Params | None = None,
             house_all: bool = False) -> dict:
    """The simulated nights of one cycle: for every replication and hour the races as the live system would see them
    (independent of the national model's parameters, so tuning re-scores the same nights)."""
    p = p or N.Params()
    cyc = load(year)
    out = []
    for rep in range(reps):
        nt = Night(cyc, year, sim, seed * 1000 + rep)
        for h in (0,) + HOURS:
            out.append((rep, h, nt.races(h, p, extra_states=extra_states, house_all=house_all)))
    return {"cyc": cyc, "nights": out}


def need_lo(need: int, k: int) -> int:
    return max(0, need - k)


def score_cycle(g: dict, p: N.Params, house_sd: float = 0.052) -> pd.DataFrame:
    cyc, year = g["cyc"], g["cyc"]["year"]
    act_s = cyc["holdover"] + int(cyc["races"].query("office == 'sen'")["d_caucus_win"].sum())
    act_h = int(cyc["house"]["d_wins"].sum()) + int(cyc["house_unknown"]["d_wins"].sum())
    rows = []
    for rep, h, races in g["nights"]:
        races = [replace(r, prior_sd=house_sd) if r.office == "house" and not r.extra else r for r in races]
        ev = N.evaluate(races, p, cyc["holdover"])
        for mode in ("prior", "live", "pooled"):
            if h == 0 and mode != "prior":
                continue
            m = ev["modes"][mode]
            for ch, act, need, ks in (("senate", act_s, p.senate_need, range(need_lo(p.senate_need, 11), p.senate_need + 10)),
                                      ("house", act_h, p.house_need, range(need_lo(p.house_need, 50), p.house_need + 51))):
                rows.append({"year": year, "rep": rep, "hours": h, "mode": mode, "chamber": ch,
                             **score(m[ch], act, need, ks)})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if "variant" not in df:
        df = df.assign(variant="base")
    g = df.groupby(["variant", "year", "chamber", "hours", "mode"])[["p", "brier", "rps", "abs_err", "band", "mean", "actual"]].mean()
    return g.reset_index()


def tune(reps: int = 4) -> tuple[N.Params, float, pd.DataFrame]:
    """Grid on 2018 + 2020 only (lowest mean RPS of the live mode, both chambers, hours 1/2/4/6). History (tune_grid_pass*.csv):
    pass 1-2 treated uncontested seats as contests (optimum ran to the edge); pass 3 (uncontested known beforehand) chose
    sd_house_env 0.01, house sd 0.08 and left meas_inflate 1, sd_common 0.0134 at their structural values. The first
    replay of 2022/2024 with that setting failed in 2024 through one unpolled governor race (VT, an incumbent Republican in a
    D+34 state: count deviation -45 points) taken as national news; robust (Huber) weights were added and are chosen here,
    on 2018/2020 only, which contain the same kind of race (VT, MD, MA governors 2018). 2024 is therefore no longer a
    pristine test of the robust weights: reported as such."""
    best, res = None, []
    base = N.Params(n_sim=3000, huber_k=3.0)
    gens = [generate(y, Sim(), reps) for y in TUNE]
    for lc in (0.0, 0.5, 1.0):
        for henv in (0.0125, 0.02, 0.0375):
            for hsd in (0.052, 0.065, 0.08):
                p = replace(base, sd_house_env=henv, house_c_load=lc)
                df = pd.concat([score_cycle(g, p, hsd) for g in gens])
                d = df[(df["mode"] == "live") & (df["hours"] > 0)]
                score_ = float(d["rps"].mean())
                res.append({"house_c_load": lc, "sd_house_env": henv, "house_sd": hsd, "rps": score_,
                            "brier": float(d["brier"].mean()), "band": float(d["band"].mean()),
                            "house_abs_err": float(d[d["chamber"] == "house"]["abs_err"].mean())})
                print(res[-1], flush=True)
                if best is None or score_ < best[0]:
                    best = (score_, p, hsd)
    return best[1], best[2], pd.DataFrame(res)


def main(argv: list[str]) -> None:
    steps = argv or ["tune", "test", "report"]
    OUT.mkdir(parents=True, exist_ok=True)
    if "tune" in steps:
        p, hsd, grid = tune()
        grid.to_csv(OUT / "tune_grid.csv", index=False)
        (OUT / "tuned.json").write_text(pd.Series({"meas_inflate": p.meas_inflate, "sd_house_env": p.sd_house_env,
                                                   "house_sd": hsd, "sd_common": p.sd_common, "huber_k": p.huber_k,
                                                   "house_c_load": p.house_c_load}).to_json())
        print(grid.sort_values("rps").head())
    if "test" in steps:
        t = pd.read_json(OUT / "tuned.json", typ="series")
        p = replace(N.Params(n_sim=6000), meas_inflate=float(t["meas_inflate"]), sd_house_env=float(t["sd_house_env"]),
                    sd_common=float(t["sd_common"]), huber_k=float(t["huber_k"]),
                    house_c_load=float(t["house_c_load"]))
        EXTRA = ("sd", "va", "id", "md", "ma")            # 2b states with an open feed (brunder.us_national_feeds)
        variants = {"base": {}, "extra_states": {"extra_states": EXTRA}, "extra+civic": {"extra_states": EXTRA,
                                                                                         "house_all": True}}
        parts = []
        for name, kw in variants.items():
            for y in TUNE + TEST:
                parts.append(score_cycle(generate(y, Sim(), 12, **kw), p, float(t["house_sd"])).assign(variant=name))
        pd.concat(parts).to_csv(OUT / "runs.csv", index=False)
    if "report" in steps:
        df = pd.read_csv(OUT / "runs.csv")
        s = summarize(df)
        s.to_csv(OUT / "summary.csv", index=False)
        pd.set_option("display.width", 200)
        print(s.round(3).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
