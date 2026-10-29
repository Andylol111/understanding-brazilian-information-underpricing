"""US midterms 2026: poll weighting and the poll-error model, judged out of sample (analysis only; docs/us_poll_error.md).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_poll_error.py     # all tables -> results/us_poll_error/

Every averaging scheme and every error model is fitted on earlier cycles only and scored on the next cycle (test cycles
2016, 2018, 2020, 2022; 2006-2014 as a robustness check), against the current rule (brunder.us.poll_average; v3's
swing_prior_sd = POLL_SD_BINS). No config is written: the decision on a v5 is reported (docs/us_poll_error.md).

Inputs (no look-ahead: pollster statistics, sd models and the common-miss history use cycles before the one predicted)
  538 raw_polls.csv (FiveThirtyEight pollster ratings, CC BY 4.0): general-election polls 1998-2022 with certified results.
      Senate/governor races 2002-2022 are the test universe; Senate/governor/president/House polls of earlier cycles
      give each pollster's house effect, absolute lean and excess error.
  MEDSL County Presidential Election Returns 2000-2024 (doi:10.7910/DVN/VOQCHQ, CC0): state presidential lean.
  site/data/us2026/hub/polls.json (VoteHub Polling API, CC BY 4.0) and data_public/us2026_races.json: the 2026 races.
  site/data/us2026/hub/census.json (ACS 2020-2024, public domain): state Bachelor's+ share, a diagnostic covariate only
      (published after the test cycles: look-ahead, so never part of a candidate model).
Units: two-party Democratic share (fraction); err = poll average - certified share (+ = polls overstated the Democrat).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_crosstabs  # noqa: E402
import us_polls  # noqa: E402

OUT = us.ROOT_DIR / "results" / "us_poll_error"
HUB = us.ROOT_DIR / "site" / "data" / "us2026" / "hub"
GEN_TYPES = ("Sen-G", "Gov-G", "Pres-G", "House-G")
RACE_TYPES = ("Sen-G", "Gov-G")
TEST_CYCLES = (2016, 2018, 2020, 2022)
EARLY_TESTS = (2006, 2008, 2010, 2012, 2014)
FIRST_CYCLE = 2002            # first race cycle with a previous presidential result in the MEDSL file (2000)
STATS_DAYS = 21               # pollster statistics from polls of the final 21 days (538's rating window)
SHRINK_K = us_crosstabs.SHRINK_K
EXCESS_FLOOR = 0.0001         # excess-error variance floor (1 point of two-party share)
Z90 = 1.6448536269514722
PRES_PARTY = {1998: "D", 2000: "D", 2002: "R", 2004: "R", 2006: "R", 2008: "R", 2010: "D", 2012: "D", 2014: "D",
              2016: "D", 2018: "R", 2020: "R", 2022: "D", 2026: "R"}
REGION = {s: r for r, ss in {
    "northeast": "ct me ma nh ri vt nj ny pa", "midwest": "il in mi oh wi ia ks mn mo ne nd sd",
    "south": "de fl ga md nc sc va dc wv al ky ms tn ar la ok tx",
    "west": "az co id mt nv nm ut wy ak ca hi or wa"}.items() for s in ss.split()}


# ------------------------------------------------------------------ data

def load_538(path: Path | None = None) -> pd.DataFrame:
    """One row per poll × race: general elections, Democrat vs Republican, with sample size and certified result."""
    f = path or us.US_RAW / "polls" / "538_raw_polls.csv"
    if path is None:
        us._fetch(us_polls.RAW_538, f)
    d = pd.read_csv(f)
    d = d[d["type_simple"].isin(GEN_TYPES) & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")]
    d = d.dropna(subset=["cand1_pct", "cand2_pct", "cand1_actual", "cand2_actual", "samplesize", "polldate"])
    d = d[(d["cand1_pct"] + d["cand2_pct"] > 0) & (d["samplesize"] > 0)].drop_duplicates(["poll_id", "race_id"])
    s = d["cand1_pct"] / (d["cand1_pct"] + d["cand2_pct"])
    act = d["cand1_actual"] / (d["cand1_actual"] + d["cand2_actual"])
    pid = d["pollster_rating_id"].map(lambda x: f"r{int(x)}" if pd.notna(x) else None)
    return pd.DataFrame({
        "cycle": d["cycle"].astype(int), "race": d["race"], "race_id": d["race_id"], "type": d["type_simple"],
        "state": d["location"].str[:2].str.lower(), "pollster": d["pollster"],
        "pid": pid.fillna("n" + d["pollster"].map(us_crosstabs._norm)), "partisan": d["partisan"].where(
            d["partisan"].isin(["DEM", "REP"])), "tte": d["time_to_election"].astype(int),
        "n": d["samplesize"].astype(float), "dem": d["cand1_pct"], "opp": d["cand2_pct"], "s": s, "act": act,
        "err": s - act, "cand1": d["cand1_name"].map(_person), "cand2": d["cand2_name"].map(_person)}).reset_index(drop=True)


def _person(name: str) -> str:
    """First and last name, lower-case letters only (538 candidate ids change between cycles)."""
    w = us._person(str(name))
    return f"{w[0]} {w[-1]}" if w else ""


def pres_lean(path: Path | None = None) -> pd.DataFrame:
    """state, year, share (two-party D), lean = share - national two-party D share; MEDSL county file summed."""
    f = path or us.US_RAW / "medsl" / "countypres_2000-2024.csv"
    d = pd.read_csv(f, usecols=["state_po", "year", "party", "candidatevotes", "mode"], low_memory=False)
    d = d[d["party"].isin(["DEMOCRAT", "REPUBLICAN"])]
    # a county reports TOTAL or a split by mode; summing every row double counts where both exist
    tot = d[d["mode"].fillna("TOTAL").str.upper() == "TOTAL"]
    split = d[~d.index.isin(tot.index)]
    have = set(zip(tot["state_po"], tot["year"]))
    d = pd.concat([tot, split[[(s, y) not in have for s, y in zip(split["state_po"], split["year"])]]])
    g = d.pivot_table(index=["state_po", "year"], columns="party", values="candidatevotes", aggfunc="sum").reset_index()
    g["share"] = g["DEMOCRAT"] / (g["DEMOCRAT"] + g["REPUBLICAN"])
    nat = g.groupby("year")[["DEMOCRAT", "REPUBLICAN"]].sum()
    g["lean"] = g["share"] - g["year"].map(nat["DEMOCRAT"] / (nat["DEMOCRAT"] + nat["REPUBLICAN"]))
    return g.assign(state=g["state_po"].str.lower())[["state", "year", "share", "lean"]]


def prev_pres_year(cycle: int) -> int:
    """The latest presidential election before `cycle` (a presidential year's own result is not known beforehand)."""
    return cycle - 2 if cycle % 4 == 2 else cycle - 4


def incumbents(polls: pd.DataFrame) -> pd.DataFrame:
    """race -> inc ('D', 'R' or ''): whether a candidate won the same office in the state earlier (538 races from 1998;
    Senate 2-6 years before, governor 2 or 4). Appointed incumbents and races 538 did not poll are missed."""
    r = polls[polls["type"].isin(RACE_TYPES)].groupby("race").agg(
        cycle=("cycle", "first"), state=("state", "first"), type=("type", "first"), c1=("cand1", "first"),
        c2=("cand2", "first"), act=("act", "first"))
    r["winner"] = np.where(r["act"] > 0.5, r["c1"], r["c2"])
    won = {(t, s): [] for t, s in zip(r["type"], r["state"])}
    for t, s, c, w in zip(r["type"], r["state"], r["cycle"], r["winner"]):
        won[(t, s)].append((c, w))
    back = {"Sen-G": (2, 4, 6), "Gov-G": (2, 4)}
    out = {}
    for race, x in r.iterrows():
        prev = {w for c, w in won[(x["type"], x["state"])] if x["cycle"] - c in back[x["type"]]}
        out[race] = "D" if x["c1"] in prev else "R" if x["c2"] in prev else ""
    return pd.Series(out, name="inc")


def state_ba(census: Path | None = None) -> pd.Series:
    """State Bachelor's+ share of adults 25+ (ACS 2020-2024; county values weighted by 2020 population)."""
    c = json.loads((census or HUB / "census.json").read_text())["counties"]
    d = pd.DataFrame([{"state": k.split("-")[1], "ba": v.get("ba_plus_pct"), "w": v.get("pop2020")}
                      for k, v in c.items()]).dropna()
    return d.groupby("state").apply(lambda g: np.average(g["ba"], weights=g["w"]) / 100, include_groups=False)


# ------------------------------------------------------------------ pollster statistics (earlier cycles only)

@dataclass
class PollsterStats:
    house: pd.Series          # pid -> shrunk mean of (poll error - its race's mean poll error)
    lean: pd.Series           # pid -> shrunk mean signed poll error (contains the industry misses of those cycles)
    excess: pd.Series         # pid -> shrunk excess error variance beyond sampling (relative to the race mean)
    excess_all: float         # the excess variance of an unrated pollster
    partisan: dict            # 'DEM' / 'REP' sponsor -> shrunk mean relative error


def pollster_stats(polls: pd.DataFrame, cycle: int, k: float = SHRINK_K) -> PollsterStats:
    p = polls[(polls["cycle"] < cycle) & (polls["tte"] >= 1) & (polls["tte"] <= STATS_DAYS)].copy()
    g = p.groupby("race_id")["err"]
    p["rel"] = (p["err"] - g.transform("mean")).where(g.transform("size") >= 3)
    p["samp"] = p["s"] * (1 - p["s"]) / (p["n"] * (p["dem"] + p["opp"]) / 100)
    r = p.dropna(subset=["rel"])
    ex_all = max(float((r["rel"] ** 2 - r["samp"]).mean()), EXCESS_FLOOR)
    a = r.assign(ex=r["rel"] ** 2 - r["samp"]).groupby("pid").agg(n=("rel", "size"), rel=("rel", "sum"), ex=("ex", "sum"))
    lean = p.groupby("pid")["err"].agg(["sum", "size"])
    part = r.groupby("partisan")["rel"].agg(["sum", "size"])
    return PollsterStats(house=a["rel"] / (a["n"] + k), lean=lean["sum"] / (lean["size"] + k),
                         excess=((a["ex"] + k * ex_all) / (a["n"] + k)).clip(lower=EXCESS_FLOOR), excess_all=ex_all,
                         partisan={i: float(x["sum"] / (x["size"] + k)) for i, x in part.iterrows()})


# ------------------------------------------------------------------ averaging schemes

@dataclass(frozen=True)
class Scheme:
    window: int = us.POLL_WINDOW_DAYS      # days before the election (field date >= 1 day before)
    half_life: float | None = us.POLL_HALF_LIFE
    n_power: float = 0.5                   # weight × n^power (0: equal, 0.5: v3's sqrt(n), 1: n)
    cap: float | None = us.POLL_CAP        # max share of one pollster
    latest: bool = False                   # only each pollster's latest poll
    ivw: bool = False                      # weight × 1 / (pollster excess variance + sampling variance) instead of n
    house: bool = False                    # subtract the pollster's earlier house effect
    lean: bool = False                     # subtract the pollster's earlier absolute lean (recalled-vote proxy)
    partisan_w: float = 1.0                # weight multiplier of DEM/REP-sponsored polls
    partisan_shift: bool = False           # subtract the earlier mean house effect of same-party sponsors


SCHEMES = {
    "v3": Scheme(),
    "half-life 7": Scheme(half_life=7.0),
    "half-life 30": Scheme(half_life=30.0),
    "no decay": Scheme(half_life=None),
    "window 21": Scheme(window=21),
    "equal weights": Scheme(n_power=0.0),
    "weight n": Scheme(n_power=1.0),
    "latest per pollster": Scheme(latest=True),
    "quality (inverse variance)": Scheme(ivw=True),
    "house effect": Scheme(house=True),
    "quality + house": Scheme(ivw=True, house=True),
    "partisan x0.5": Scheme(partisan_w=0.5),
    "partisan shift": Scheme(partisan_shift=True),
    "recall proxy (abs. lean)": Scheme(lean=True),
    "quality + house + partisan shift": Scheme(ivw=True, house=True, partisan_shift=True),
}


def average(rows: pd.DataFrame, sch: Scheme, st: PollsterStats | None) -> dict | None:
    """rows: tte (days to the election), n, s, dem, opp, pid, pollster, partisan. Average as of the day before the
    election: {avg, n_polls, n_pollsters, samp (sampling variance of the weighted mean)}; None without polls."""
    r = rows[(rows["tte"] >= 1) & (rows["tte"] - 1 <= sch.window)]          # age = tte - 1 days as of the eve
    if r.empty:
        return None
    if sch.latest:
        r = r.sort_values("tte").drop_duplicates("pollster")
    s = r["s"].to_numpy(float)
    n2 = (r["n"] * (r["dem"] + r["opp"]) / 100).to_numpy(float)
    age = (r["tte"] - 1).to_numpy(float)
    w = 0.5 ** (age / sch.half_life) if sch.half_life else np.ones(len(r))
    if sch.ivw:
        w = w / (r["pid"].map(st.excess).fillna(st.excess_all).to_numpy(float) + s * (1 - s) / n2)
    else:
        w = w * r["n"].to_numpy(float) ** sch.n_power
    sponsored = r["partisan"].notna().to_numpy()
    w = np.where(sponsored, w * sch.partisan_w, w)
    if sch.house:
        s = s - r["pid"].map(st.house).fillna(0.0).to_numpy(float)
    if sch.lean:
        s = s - r["pid"].map(st.lean).fillna(0.0).to_numpy(float)
    if sch.partisan_shift:
        s = s - r["partisan"].map(st.partisan).fillna(0.0).to_numpy(float)
    w = pd.Series(w, index=r.index)
    w = us._cap(w, r["pollster"], sch.cap) if sch.cap else w / w.sum()
    w = w.to_numpy()
    return {"avg": float(w @ s), "n_polls": len(r), "n_pollsters": int(r["pollster"].nunique()),
            "samp": float((w**2 * r["s"].to_numpy() * (1 - r["s"].to_numpy()) / n2).sum())}


def race_table(polls: pd.DataFrame, lean: pd.DataFrame, ba: pd.Series | None = None) -> pd.DataFrame:
    """Senate/governor races 2002-2022 (even years) with at least one poll in the final 21 days, and covariates."""
    t = polls[polls["type"].isin(RACE_TYPES) & (polls["cycle"] % 2 == 0) & (polls["cycle"] >= FIRST_CYCLE)]
    t = t[t["race"].isin(t.loc[(t["tte"] >= 1) & (t["tte"] <= STATS_DAYS), "race"])]
    r = t.groupby("race").agg(cycle=("cycle", "first"), state=("state", "first"), type=("type", "first"),
                              act=("act", "first")).reset_index()
    li = lean.set_index(["state", "year"])["lean"]
    r["pres_lean"] = [li.get((s, prev_pres_year(c)), np.nan) for s, c in zip(r["state"], r["cycle"])]
    r["inc"] = r["race"].map(incumbents(polls)).fillna("")
    r["midterm"] = (r["cycle"] % 4 == 2).astype(int)
    r["inparty"] = r["cycle"].map(PRES_PARTY)
    r["gov"] = (r["type"] == "Gov-G").astype(int)
    r["region"] = r["state"].map(REGION)
    r["ba"] = r["state"].map(ba) if ba is not None else np.nan
    return r.dropna(subset=["pres_lean"]).reset_index(drop=True)


def scheme_errors(polls: pd.DataFrame, races: pd.DataFrame, schemes: dict[str, Scheme] = SCHEMES) -> pd.DataFrame:
    """Long table: race, scheme, avg, n_polls, samp, err (pollster statistics from cycles before the race's)."""
    by_race = dict(tuple(polls[polls["race"].isin(races["race"])].groupby("race")))
    st = {c: pollster_stats(polls, c) for c in races["cycle"].unique()}
    out = []
    for _, x in races.iterrows():
        rows = by_race[x["race"]]
        for name, sch in schemes.items():
            a = average(rows, sch, st[x["cycle"]])
            if a:
                out.append({"race": x["race"], "scheme": name, **a, "err": a["avg"] - x["act"]})
    return pd.DataFrame(out).merge(races, on="race")


DEBIAS = "v3 - earlier mean error"


def add_debias(E: pd.DataFrame) -> pd.DataFrame:
    """Adds the scheme 'v3 minus the mean error of all earlier cycles' races' (a constant shift per cycle: the bet that
    the industry-wide miss repeats)."""
    v = E[E["scheme"] == "v3"]
    shift = {c: float(v.loc[v["cycle"] < c, "err"].mean()) for c in v["cycle"].unique()}
    d = v.assign(scheme=DEBIAS, avg=v["avg"] - v["cycle"].map(shift).fillna(0.0))
    return pd.concat([E, d.assign(err=d["avg"] - d["act"])], ignore_index=True)


# ------------------------------------------------------------------ error (sd) models

HETERO = ["log_n", "abs_lean", "midterm", "inc_running", "gov"]


def design(d: pd.DataFrame, cols: list[str]) -> np.ndarray:
    f = {"log_n": np.log(d["n_polls"]), "abs_lean": d["pres_lean"].abs() * 10, "midterm": d["midterm"],
         "inc_running": (d["inc"] != "").astype(float), "gov": d["gov"], "ba": d["ba"].fillna(d["ba"].mean()) * 10,
         "inparty_d": (d["inparty"] == "D").astype(float)}
    return np.column_stack([np.ones(len(d))] + [np.asarray(f[c], float) for c in cols])


def fit_hetero(train: pd.DataFrame, cols: list[str] = HETERO) -> np.ndarray:
    """ML fit of err ~ N(0, exp(X b)): log variance linear in the covariates."""
    X, e2 = design(train, cols), train["err"].to_numpy() ** 2

    def nll(b):
        v = X @ b
        return 0.5 * float(np.sum(v + e2 * np.exp(-v)))

    b0 = np.zeros(X.shape[1])
    b0[0] = np.log(e2.mean())
    return optimize.minimize(nll, b0, method="BFGS").x


def hetero_se(train: pd.DataFrame, b: np.ndarray, cols: list[str] = HETERO) -> np.ndarray:
    """Standard errors from the expected information (X'X / 2)^-1 of the Gaussian log-variance model."""
    X = design(train, cols)
    return np.sqrt(np.diag(np.linalg.inv(X.T @ X / 2)))


def sd_model(train: pd.DataFrame, kind: str):
    """Fitted on `train` (one scheme's races of earlier cycles) -> function(test frame) -> sd array."""
    if kind == "bins":
        bins = us.poll_sd_bins(train[["n_polls", "err"]])
        return lambda d: d["n_polls"].map(lambda n: us.poll_sd_by_n(int(n), bins)).to_numpy(float)
    if kind == "bins (registered v3)":
        return lambda d: d["n_polls"].map(lambda n: us.poll_sd_by_n(int(n))).to_numpy(float)
    if kind == "flat":
        sd = float(np.sqrt((train["err"] ** 2).mean()))
        return lambda d: np.full(len(d), sd)
    cols = {"hetero": HETERO, "hetero + education (look-ahead)": HETERO + ["ba"],
            "n polls only (log)": ["log_n"]}[kind]
    b = fit_hetero(train, cols)
    return lambda d: np.sqrt(np.exp(design(d, cols) @ b))


SD_KINDS = ("bins", "flat", "n polls only (log)", "hetero")


def score(d: pd.DataFrame, sd: np.ndarray) -> dict:
    e, won = d["err"].to_numpy(), (d["act"] > 0.5).to_numpy(float)
    p = stats.norm.cdf((d["avg"].to_numpy() - 0.5) / sd).clip(1e-6, 1 - 1e-6)
    return {"races": len(d), "mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e**2).mean())),
            "bias": float(e.mean()), "cov90": float((np.abs(e) <= Z90 * sd).mean()), "sd_mean": float(sd.mean()),
            "brier": float(((p - won) ** 2).mean()), "logloss": float(-(won * np.log(p) + (1 - won) * np.log(1 - p)).mean())}


def backtest(E: pd.DataFrame, sd_kinds=SD_KINDS, tests=TEST_CYCLES) -> pd.DataFrame:
    """Per test cycle, scheme and sd model: race rows with avg, err, sd (fitted on earlier cycles only)."""
    out = []
    for c in tests:
        for name, g in E.groupby("scheme"):
            tr, te = g[g["cycle"] < c], g[g["cycle"] == c]
            for k in sd_kinds:
                out.append(te.assign(sd_kind=k, sd=sd_model(tr, k)(te), test=c))
    return pd.concat(out, ignore_index=True)


def nested_choice(E: pd.DataFrame, c: int, first_eval: int = FIRST_CYCLE) -> str:
    """The scheme with the lowest MAE on the earlier cycles' out-of-sample races (each scored with pollster
    statistics of its own past): a selection that itself uses no data from cycle c."""
    g = E[(E["cycle"] < c) & (E["cycle"] >= first_eval)]
    return g.groupby("scheme")["err"].apply(lambda e: e.abs().mean()).idxmin()


def boot_ci(d: pd.DataFrame, f, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95 % interval of statistic f(frame) over race bootstrap resamples (stratified by test cycle)."""
    rng = np.random.default_rng(seed)
    groups = [g.index.to_numpy() for _, g in d.groupby("test")]
    vals = [f(d.loc[np.concatenate([rng.choice(ix, len(ix)) for ix in groups])]) for _ in range(n)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


# ------------------------------------------------------------------ decomposition and the common miss

def decompose(e: pd.DataFrame) -> dict:
    """Variance components of race errors (one scheme): cycle, region × cycle, state × cycle (Senate and governor of
    the same state and year), state (persistent across cycles), race. Crossed random effects, REML (statsmodels)."""
    import statsmodels.formula.api as smf
    d = e.assign(cyc=e["cycle"].astype(str), creg=e["cycle"].astype(str) + e["region"],
                 cst=e["cycle"].astype(str) + e["state"], one=1, y=e["err"] * 100)
    vc = {"cycle": "0 + C(cyc)", "region_x_cycle": "0 + C(creg)", "state_x_cycle": "0 + C(cst)", "state": "0 + C(state)"}
    m = smf.mixedlm("y ~ 1", d, groups="one", vc_formula=vc, re_formula="0").fit(reml=True)
    comp = dict(zip(m.model.exog_vc.names, np.asarray(m.vcomp, float)))
    comp["race"] = float(m.scale)
    tot = sum(comp.values())
    common = comp["cycle"] + comp["region_x_cycle"]
    return {"races": len(d), "intercept_pts": float(m.fe_params.iloc[0]),
            "corr": {"other_region": comp["cycle"] / tot, "same_region": common / tot,
                     "same_state_other_cycle": comp["state"] / tot,
                     "same_state_same_cycle": (common + comp["state"] + comp["state_x_cycle"]) / tot},
            "sd_pts": {k: float(np.sqrt(v)) for k, v in comp.items()},
            "share": {k: float(v / tot) for k, v in comp.items()}, "total_sd_pts": float(np.sqrt(tot))}


def cycle_means(polls: pd.DataFrame, sch: Scheme = SCHEMES["v3"]) -> pd.DataFrame:
    """Mean error of the Senate/governor race averages per even cycle 1998-2022, signed and towards the president's
    party (in_party_err > 0: polls overstated the president's party)."""
    t = polls[polls["type"].isin(RACE_TYPES) & (polls["cycle"] % 2 == 0)]
    rows = []
    for race, g in t.groupby("race"):
        a = average(g, sch, None)
        if a:
            rows.append({"cycle": int(g["cycle"].iloc[0]), "err": a["avg"] - g["act"].iloc[0]})
    c = pd.DataFrame(rows).groupby("cycle")["err"].agg(["mean", "size"]).reset_index()
    c["inparty"] = c["cycle"].map(PRES_PARTY)
    c["midterm"] = c["cycle"] % 4 == 2
    c["in_party_err"] = np.where(c["inparty"] == "D", c["mean"], -c["mean"])
    return c


def common_miss_forecast(cm: pd.DataFrame, upto: int, midterm_only: bool = True) -> dict:
    """Expected in-party error for the next cycle from cycles < upto: mean, its t interval and the predictive interval
    (a new cycle's common miss)."""
    h = cm[(cm["cycle"] < upto) & (cm["midterm"] if midterm_only else True)]["in_party_err"].to_numpy()
    k, m, s = len(h), float(h.mean()), float(h.std(ddof=1))
    t = stats.t.ppf(0.95, k - 1)
    return {"cycles": k, "mean": m, "sd": s, "mean_90": (m - t * s / np.sqrt(k), m + t * s / np.sqrt(k)),
            "pred_90": (m - t * s * np.sqrt(1 + 1 / k), m + t * s * np.sqrt(1 + 1 / k))}


# ------------------------------------------------------------------ 2026

ASOF_2026 = pd.Timestamp(us.POLLS_ASOF)
ELECTION_2026 = pd.Timestamp("2026-11-03")


def rows_2026(race: dict, name_to_pid: dict[str, str]) -> pd.DataFrame:
    """The polls v3 would use for a race (one version per poll, names the Democratic side, sample size), with the
    538 pollster id where the normalised name matches. tte counted to Election Day; the scheme's window and weights then
    run as of the day before, which is POLLS_ASOF."""
    ok = {None, "outside the 45-day window"}
    ps = [p for p in race["polls"] if p.get("excluded") in ok and p.get("n") and p.get("dem2") is not None]
    if not ps:
        return pd.DataFrame()
    d = pd.DataFrame(ps)
    tte = (ELECTION_2026 - pd.to_datetime(d["end"])).dt.days - (ELECTION_2026 - pd.Timedelta(days=1) - ASOF_2026).days
    return pd.DataFrame({"tte": tte, "n": d["n"].astype(float), "s": d["dem2"].astype(float),
                         "dem": d["dem"].astype(float), "opp": d["opp"].astype(float), "pollster": d["pollster"],
                         "pid": d["pollster"].map(lambda x: name_to_pid.get(us_crosstabs._norm(x),
                                                                            "n" + us_crosstabs._norm(x))),
                         "partisan": d["partisan"].where(d["partisan"].isin(["DEM", "REP"]))})


def frozen_v3() -> dict[str, dict]:
    """race_id -> the primary poll model's frozen poll_avg, swing_prior_sd, swing_prior_mean, poll_n, config path."""
    out = {}
    for p in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")):
        c1 = us.load_config(p)
        vs = {v: us.load_config(q) for v, q in us._sibling_versions(c1).items()}
        pm = us.poll_model(vs)
        if pm:
            c = vs[pm]
            out[c1["election"]["race_id"]] = {"config": c["_path"], "version": pm, "poll_avg": c["prior"]["poll_avg"],
                                              "poll_n": c["prior"]["poll_n"], "sd": c["model"]["swing_prior_sd"],
                                              "mean": c["model"]["swing_prior_mean"]}
    return out


def project_2026(polls: pd.DataFrame, E: pd.DataFrame, lean: pd.DataFrame, scheme: str) -> pd.DataFrame:
    """Per 2026 race with polls: the v3 rule and `scheme` on the current hub polls (pollster statistics from 1998-2022),
    the v3 rule's sd from the poll-count bins and from the heteroscedastic model, and `scheme`'s bins, all fitted on all
    races 2002-2022; the frozen
    v3 prior of the modeled races; the recalled-vote adjusted average (analysis/us_crosstabs.py, display only)."""
    hub = json.loads((HUB / "polls.json").read_text())
    reg = {r["race_key"]: r for r in json.loads(us.RACES_REGISTRY.read_text())["races"]}
    names = polls.drop_duplicates("pollster").set_index("pollster")["pid"]
    name_to_pid = {us_crosstabs._norm(k): v for k, v in names.items()}
    st = pollster_stats(polls, 2026)
    li = lean.set_index(["state", "year"])["lean"]
    sd3, sdb = sd_model(E[E["scheme"] == "v3"], "bins"), sd_model(E[E["scheme"] == scheme], "bins")
    sdh = sd_model(E[E["scheme"] == "v3"], "hetero")
    pres = pd.read_csv(us.STATE_PRES24, comment="#").set_index("state")
    adj = {r["race"]: r for r in us_crosstabs.per_race(
        us_crosstabs.per_poll(hub, us_crosstabs.load_crosstabs(), pres)[0], hub)}
    fz = frozen_v3()
    out = []
    for key, race in hub["races"].items():
        rows = rows_2026(race, name_to_pid) if race else pd.DataFrame()
        a3 = average(rows, SCHEMES["v3"], st) if not rows.empty else None
        ab = average(rows, SCHEMES[scheme], st) if a3 else None
        if not ab:
            continue
        r = reg.get(key, {})
        x = pd.DataFrame([{"n_polls": ab["n_polls"], "pres_lean": li.get((key[:2], 2024), np.nan), "midterm": 1,
                           "inc": "x" if r.get("incumbent") and not r.get("open") else "", "gov": int(key.endswith("gov")),
                           "inparty": "R", "ba": np.nan}])
        f, rec = fz.get(key, {}), adj.get(key) or {}
        out.append({"race": key, "modeled": bool(f), "n_polls": a3["n_polls"],
                    "frozen_avg": f.get("poll_avg"), "frozen_sd": f.get("sd"), "frozen_version": f.get("version"),
                    "v3_avg": a3["avg"], "v3_sd": float(sd3(x.assign(n_polls=a3["n_polls"]))[0]),
                    "v3_hetero_sd": float(sdh(x.assign(n_polls=a3["n_polls"]))[0]),
                    "alt_avg": ab["avg"], "alt_sd": float(sdb(x)[0]),
                    "recall_adj_avg": 0.5 + rec["adj"] / 200 if rec.get("adj") is not None else np.nan})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ report

def summarize(B: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    return B.groupby(by).apply(lambda g: pd.Series(score(g, g["sd"].to_numpy())), include_groups=False).reset_index()


def mae_vs(B: pd.DataFrame, scheme: str, base: str = "v3", sd_kind: str = "bins") -> dict:
    """Mean of |err| - |err of base| over the same races (points), with its 95 % race-bootstrap interval."""
    a = B[(B["scheme"] == scheme) & (B["sd_kind"] == sd_kind)].set_index("race")
    b = B[(B["scheme"] == base) & (B["sd_kind"] == sd_kind)].set_index("race")["err"]
    j = a[["err", "test"]].assign(d=(a["err"].abs() - b.reindex(a.index).abs()) * 100).reset_index()
    lo, hi = boot_ci(j, lambda g: g["d"].mean())
    return {"diff": float(j["d"].mean()), "lo": lo, "hi": hi, "races": len(j)}


def decide(B: pd.DataFrame, candidate: str, sd_kind: str) -> dict:
    """v5 rule (fixed before looking): the candidate beats v3's registered prior on the 2016-2022 tests (lower MAE with
    a 95 % interval below zero, coverage of the 90 % interval within 0.85-0.95, Brier not worse) and is not worse on
    the 2006-2014 tests."""
    late = B[B["test"].isin(TEST_CYCLES)]
    c = score(late[(late["scheme"] == candidate) & (late["sd_kind"] == sd_kind)],
              late.loc[(late["scheme"] == candidate) & (late["sd_kind"] == sd_kind), "sd"].to_numpy())
    v = late[(late["scheme"] == "v3") & (late["sd_kind"] == "bins (registered v3)")]
    v3 = score(v, v["sd"].to_numpy())
    d_late = mae_vs(late, candidate, sd_kind=sd_kind)
    d_early = mae_vs(B[B["test"].isin(EARLY_TESTS)], candidate, sd_kind=sd_kind)
    ok = {"lower_mae": d_late["hi"] < 0, "coverage_85_95": 0.85 <= c["cov90"] <= 0.95,
          "brier_not_worse": c["brier"] <= v3["brier"], "early_not_worse": d_early["diff"] <= 0}
    return {"candidate": candidate, "sd_kind": sd_kind, "score": c, "v3": v3, "mae_diff_late": d_late,
            "mae_diff_early": d_early, "checks": ok, "v5": all(ok.values())}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pts = lambda df, cols: df.assign(**{c: df[c] * 100 for c in cols})
    polls, lean = load_538(), pres_lean()
    races = race_table(polls, lean, state_ba())
    E = add_debias(scheme_errors(polls, races))
    E.to_csv(OUT / "race_errors.csv", index=False)
    tests = EARLY_TESTS + TEST_CYCLES
    nest = {c: nested_choice(E, c) for c in tests}
    print("scheme chosen on earlier cycles only:", nest)
    E = pd.concat([E, pd.concat([E[(E["scheme"] == nest[c]) & (E["cycle"] == c)] for c in tests])
                   .assign(scheme="nested choice")], ignore_index=True)
    kinds = SD_KINDS + ("bins (registered v3)", "hetero + education (look-ahead)")
    B = pd.concat([backtest(E[E["scheme"] != "nested choice"], kinds, tests),
                   # the nested choice's sd models are fitted on the chosen scheme's own earlier errors
                   pd.concat([backtest(E[E["scheme"] == nest[c]], kinds, (c,)).assign(scheme="nested choice")
                              for c in tests])], ignore_index=True)
    B.to_csv(OUT / "backtest_rows.csv", index=False)
    late = B[B["test"].isin(TEST_CYCLES)]

    # 1. weighting
    s = summarize(late[late["sd_kind"] == "bins"], ["scheme", "test"])
    s.to_csv(OUT / "backtest_by_cycle.csv", index=False)
    t = s.pivot(index="scheme", columns="test", values="mae") * 100
    pooled = summarize(late[late["sd_kind"] == "bins"], ["scheme"]).set_index("scheme")
    t["MAE 16-22"] = pooled["mae"] * 100
    t["cov90"] = pooled["cov90"]
    t["Brier"] = pooled["brier"]
    for name in t.index:
        if name != "v3":
            d, e = mae_vs(late, name), mae_vs(B[B["test"].isin(EARLY_TESTS)], name)
            t.loc[name, ["vs v3", "lo", "hi", "vs v3 06-14"]] = d["diff"], d["lo"], d["hi"], e["diff"]
    t = t.sort_values("MAE 16-22")
    t.to_csv(OUT / "weighting.csv")
    print("\n== 1. weighting: race MAE (points of two-party share) by test cycle; pooled cov90 / Brier with bins sd ==")
    print(t.round(3).to_string())

    # 2. error models
    em = []
    for (sc, k), g in late[late["scheme"].isin(["v3", "nested choice"])].groupby(["scheme", "sd_kind"]):
        g = g.reset_index(drop=True)
        r = {"scheme": sc, "sd_kind": k, **score(g, g["sd"].to_numpy())}
        r["cov_lo"], r["cov_hi"] = boot_ci(g, lambda x: float((np.abs(x["err"]) <= Z90 * x["sd"]).mean()))
        r["rmse_lo"], r["rmse_hi"] = boot_ci(g, lambda x: float(np.sqrt((x["err"] ** 2).mean())))
        r["brier_lo"], r["brier_hi"] = boot_ci(g, lambda x: score(x, x["sd"].to_numpy())["brier"])
        r.update({f"cov{c}": float((np.abs(x["err"]) <= Z90 * x["sd"]).mean()) for c, x in g.groupby("test")})
        e = B[(B["scheme"] == sc) & (B["sd_kind"] == k) & B["test"].isin(EARLY_TESTS)]
        r["cov 06-14"] = float((np.abs(e["err"]) <= Z90 * e["sd"]).mean())
        em.append(r)
    em = pts(pd.DataFrame(em), ["mae", "rmse", "rmse_lo", "rmse_hi", "bias", "sd_mean"])
    em.to_csv(OUT / "error_models.csv", index=False)
    print("\n== 2. error models, 2016-2022 tests (points; cov90 nominal 0.90) ==")
    print(em.drop(columns=["races", "logloss"]).round(3).to_string(index=False))
    bins_cov = late[(late["scheme"] == "v3") & late["sd_kind"].isin(["bins", "bins (registered v3)"])].assign(
        bin=lambda x: pd.cut(x["n_polls"], [0, 2, 5, 10, 10**6], labels=["1-2", "3-5", "6-10", "11+"]),
        cov=lambda x: np.abs(x["err"]) <= Z90 * x["sd"])
    print(bins_cov.groupby(["sd_kind", "bin"], observed=True)["cov"].agg(["mean", "size"]).round(2).to_string())
    v3all = E[E["scheme"] == "v3"]
    b = fit_hetero(v3all)
    coef = pd.DataFrame({"coef": b, "se": hetero_se(v3all, b)}, index=["const"] + HETERO)
    for c in TEST_CYCLES:
        coef[f"fit<{c}"] = fit_hetero(v3all[v3all["cycle"] < c])
    coef.to_csv(OUT / "hetero_coef.csv")
    print("\nlog-variance model (v3 average; all 2002-2022 races, then fitted on cycles before each test):")
    print(coef.round(3).to_string())
    cb = fit_hetero(v3all, HETERO + ["ba"])
    print("education (look-ahead) coef per 10 points of BA share:", round(cb[-1], 3),
          "se", round(hetero_se(v3all, cb, HETERO + ["ba"])[-1], 3))
    dec = decompose(v3all)
    (OUT / "decomposition.json").write_text(json.dumps(dec, indent=1))
    print("\n== decomposition of race errors (v3 average, 2002-2022) ==\n", json.dumps(dec, indent=1))

    # common miss
    cm = cycle_means(polls)
    cm.to_csv(OUT / "cycle_means.csv", index=False)
    print("\n== cycle mean error (points; in_party_err > 0 = polls overstated the president's party) ==")
    print(pts(cm, ["mean", "in_party_err"]).round(2).to_string(index=False))
    oos = pd.DataFrame([{"cycle": c, "forecast": common_miss_forecast(cm, c)["mean"],
                         "actual": float(cm.set_index("cycle").at[c, "in_party_err"])}
                        for c in cm.loc[cm["midterm"] & (cm["cycle"] >= 2006), "cycle"]])
    print("midterm in-party miss forecast from earlier midterms:\n", pts(oos, ["forecast", "actual"]).round(2)
          .to_string(index=False))
    print(f"  MAE of that forecast {(oos['forecast'] - oos['actual']).abs().mean() * 100:.2f} vs forecasting 0: "
          f"{oos['actual'].abs().mean() * 100:.2f}")
    f26 = {k: common_miss_forecast(cm, 2026, m) for k, m in (("midterms", True), ("all", False))}
    (OUT / "common_miss_2026.json").write_text(json.dumps(f26, default=float, indent=1))
    for k, f in f26.items():
        print(f"2026 in-party (R) miss from {f['cycles']} {k}: mean {f['mean'] * 100:+.2f}, sd {f['sd'] * 100:.2f}, "
              f"90 % predictive {f['pred_90'][0] * 100:+.2f} to {f['pred_90'][1] * 100:+.2f} points")

    # 3. 2026 and 4. decision
    best = nested_choice(E[E["scheme"] != "nested choice"], 2026)
    P = project_2026(polls, E, lean, best)
    P.to_csv(OUT / "projection_2026.csv", index=False)
    print(f"\n== 3. 2026: v3 rule vs '{best}' (chosen on 2002-2022), points of two-party D share ==")
    print(pts(P, ["frozen_avg", "frozen_sd", "v3_avg", "v3_sd", "v3_hetero_sd", "alt_avg", "alt_sd", "recall_adj_avg"])
          .round(2).to_string(index=False))
    D = decide(B, "nested choice", "bins")
    (OUT / "decision.json").write_text(json.dumps(D, default=float, indent=1))
    print("\n== 4. v5 decision ==\n", json.dumps(D, default=float, indent=1))


if __name__ == "__main__":
    main()
