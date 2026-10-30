"""US early vote by party registration vs the statewide polling miss (analysis only; docs/us_ev_gap.md).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_gap.py [misses|panel|test|project|all]

Question: does the party composition of early/mail votes, relative to the state's own earlier cycles at the same number
of days before Election Day, predict the direction and size of the statewide polling miss (poll average minus
certified two-party Democratic share)? The metric, the sign convention, the cycle structure and the test were written
into docs/us_ev_gap.md ("Specification") before any EV figure was compared with any miss.

Polling miss (section 1, `misses`): Senate, governor and president races, statewide, 2016-2024, Democrat vs
Republican, with >= 1 poll in the final 21 days (the v3 race universe of docs/us_poll_error.md); the average is the
frozen v3 rule (sqrt n weight, 14-day half-life, 45-day window, 25% pollster cap, as of the eve) from
analysis/us_poll_error.py. Polls and certified results: FiveThirtyEight raw_polls.csv (CC BY 4.0) for 2016-2022;
FiveThirtyEight historical Senate/governor/president polls (CC BY 4.0) with MEDSL 2024 precinct returns and the
MEDSL county presidential file (CC0) for 2024. miss > 0: the polls overstated the Democrat.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_crosstabs  # noqa: E402
import us_poll_error as pe  # noqa: E402

RAW = us.US_RAW
PUB = us.ROOT_DIR / "data_public"
UA = "brunder/0.1 (research; ev-gap analysis)"
ELECTION_DAY = {2016: "2016-11-08", 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05",
                2026: "2026-11-03"}
CYCLES = (2016, 2018, 2020, 2022, 2024)
TYPES = ("Sen-G", "Gov-G", "Pres-G")
TYPE_OF = {"U.S. Senate": "Sen-G", "Governor": "Gov-G", "President": "Pres-G"}
STATE_PO = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh "
               "ok or pa ri sc sd tn tx ut vt va wa wv wi wy".split())
POP_RANK = {"lv": 0, "v": 1, "rv": 2, "a": 3}


# ------------------------------------------------------------------ 1. polling miss table

def polls_2016_2022() -> pd.DataFrame:
    d = pe.load_538()
    d = d[d["type"].isin(TYPES) & d["cycle"].isin((2016, 2018, 2020, 2022)) & d["state"].isin(STATE_PO)]
    return d.assign(src="538 raw_polls")


def _results_2024_medsl() -> pd.DataFrame:
    """Statewide two-party D share 2024 by office: MEDSL precinct files (Senate, governor), county file (president)."""
    rows = []
    for st in sorted(STATE_PO):
        f = RAW / "medsl24" / f"{st}24.zip"
        if not f.exists():
            continue
        z = zipfile.ZipFile(f)
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        t = pd.read_csv(z.open(name), usecols=["office", "party_simplified", "votes", "stage", "special", "dataverse"],
                        low_memory=False)
        t["votes"] = pd.to_numeric(t["votes"], errors="coerce")
        t = t[(t["stage"] == "GEN") & (~t["special"].astype(bool)) & t["office"].isin(["US SENATE", "GOVERNOR"])
              & t["party_simplified"].isin(["DEMOCRAT", "REPUBLICAN"])]
        g = t.groupby(["office", "party_simplified"])["votes"].sum().unstack()
        for off, r in g.iterrows():
            rows.append({"state": st, "type": "Sen-G" if off == "US SENATE" else "Gov-G",
                         "dem": r.get("DEMOCRAT", np.nan), "rep": r.get("REPUBLICAN", np.nan)})
    c = pd.read_csv(RAW / "medsl" / "countypres_2000-2024.csv",
                    usecols=["state_po", "year", "party", "candidatevotes", "mode"], low_memory=False)
    c = c[(c["year"] == 2024) & c["party"].isin(["DEMOCRAT", "REPUBLICAN"])]
    c = c[c["mode"].fillna("TOTAL").str.upper() == "TOTAL"]
    p = c.groupby(["state_po", "party"])["candidatevotes"].sum().unstack()
    for st, r in p.iterrows():
        rows.append({"state": st.lower(), "type": "Pres-G", "dem": r["DEMOCRAT"], "rep": r["REPUBLICAN"]})
    out = pd.DataFrame(rows)
    out[["dem", "rep"]] = out[["dem", "rep"]].astype(float)
    out["act"] = out["dem"] / (out["dem"] + out["rep"])
    return out.dropna(subset=["act"])


def _pick_question(t: pd.DataFrame) -> pd.DataFrame:
    """One question per poll x race: the one with the most candidate rows (full ballot), then the best population."""
    q = t.groupby(["poll_id", "race_id", "question_id"]).agg(k=("answer", "size"), pop=("population", "first")).reset_index()
    q["rank"] = q["pop"].map(POP_RANK).fillna(9)
    q = q.sort_values(["poll_id", "race_id", "k", "rank", "question_id"], ascending=[True, True, False, True, True])
    q = q.drop_duplicates(["poll_id", "race_id"])
    return t.merge(q[["question_id"]], on="question_id")


def polls_2024() -> pd.DataFrame:
    """538 historical polls, cycle 2024, general election, statewide, Democrat vs Republican, as `pe.load_538` rows."""
    frames = []
    for f, ty in (("senate", "Sen-G"), ("governor", "Gov-G"), ("president", "Pres-G")):
        d = pd.read_csv(RAW / "polls" / f"538_{f}_polls_historical.csv", low_memory=False)
        d = d[(d["cycle"] == 2024) & (d["stage"] == "general") & (~d["hypothetical"].astype(bool))
              & (~d["nationwide_batch"].astype(bool)) & d["party"].isin(["DEM", "REP"])
              & (d["seat_name"].astype(str) != "Special")]
        d = d.dropna(subset=["pct", "sample_size", "end_date"])
        d = _pick_question(d)
        d = d.assign(type=ty)
        frames.append(d)
    d = pd.concat(frames)
    # a candidate row per party; keep the first candidate when a ballot lists two (e.g. fusion lines)
    w = d.sort_values("pct", ascending=False).drop_duplicates(["question_id", "party"])
    k = ["poll_id", "race_id", "question_id", "type", "state", "pollster", "pollster_rating_id", "partisan",
         "sample_size", "end_date", "election_date"]
    dem = w[w["party"] == "DEM"][k + ["pct"]].rename(columns={"pct": "DEM"})
    rep = w[w["party"] == "REP"][["question_id", "pct"]].rename(columns={"pct": "REP"})
    p = dem.merge(rep, on="question_id")
    p = p[(p["DEM"] + p["REP"] > 0) & p["state"].isin(_STATE_NAME)]
    end = pd.to_datetime(p["end_date"], format="%m/%d/%y")
    ed = pd.to_datetime(p["election_date"], format="%m/%d/%y")
    pid = p["pollster_rating_id"].map(lambda x: f"r{int(x)}" if pd.notna(x) else None)
    st = p["state"].map(_STATE_NAME).str.lower()
    out = pd.DataFrame({
        "cycle": 2024, "race": [f"2024_{t}_{s.upper()}" for t, s in zip(p["type"], st)], "race_id": p["race_id"],
        "type": p["type"], "state": st, "pollster": p["pollster"],
        "pid": pid.fillna("n" + p["pollster"].map(us_crosstabs._norm)),
        "partisan": p["partisan"].where(p["partisan"].isin(["DEM", "REP"])), "tte": (ed - end).dt.days.astype(int),
        "n": p["sample_size"].astype(float), "dem": p["DEM"], "opp": p["REP"], "s": p["DEM"] / (p["DEM"] + p["REP"]),
    })
    out = out.drop_duplicates(["race", "pollster", "tte", "n", "dem", "opp"])
    out = out[out["state"].isin(STATE_PO)]
    res = _results_2024_medsl().set_index(["state", "type"])["act"]
    out["act"] = [res.get((s, t), np.nan) for s, t in zip(out["state"], out["type"])]
    out = out.dropna(subset=["act"])
    out["err"] = out["s"] - out["act"]
    return out.assign(src="538 historical + MEDSL 2024")


_STATE_NAME = {n: s.upper() for n, s in {
    "Alabama": "al", "Alaska": "ak", "Arizona": "az", "Arkansas": "ar", "California": "ca", "Colorado": "co",
    "Connecticut": "ct", "Delaware": "de", "Florida": "fl", "Georgia": "ga", "Hawaii": "hi", "Idaho": "id",
    "Illinois": "il", "Indiana": "in", "Iowa": "ia", "Kansas": "ks", "Kentucky": "ky", "Louisiana": "la",
    "Maine": "me", "Maryland": "md", "Massachusetts": "ma", "Michigan": "mi", "Minnesota": "mn", "Mississippi": "ms",
    "Missouri": "mo", "Montana": "mt", "Nebraska": "ne", "Nevada": "nv", "New Hampshire": "nh", "New Jersey": "nj",
    "New Mexico": "nm", "New York": "ny", "North Carolina": "nc", "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok",
    "Oregon": "or", "Pennsylvania": "pa", "Rhode Island": "ri", "South Carolina": "sc", "South Dakota": "sd",
    "Tennessee": "tn", "Texas": "tx", "Utah": "ut", "Vermont": "vt", "Virginia": "va", "Washington": "wa",
    "West Virginia": "wv", "Wisconsin": "wi", "Wyoming": "wy"}.items()}


def miss_table() -> pd.DataFrame:
    """One row per statewide race: v3 poll average as of the eve, certified two-party D share, miss = avg - act."""
    polls = pd.concat([polls_2016_2022(), polls_2024()], ignore_index=True)
    sch = pe.SCHEMES["v3"]
    rows = []
    for race, g in polls.groupby("race"):
        near = g[(g["tte"] >= 1) & (g["tte"] <= pe.STATS_DAYS)]
        if near.empty:
            continue
        a = pe.average(g, sch, None)
        if a is None:
            continue
        f = g.iloc[0]
        rows.append({"race": race, "cycle": int(f["cycle"]), "state": f["state"], "type": f["type"],
                     "n_polls": a["n_polls"], "n_pollsters": a["n_pollsters"], "poll_avg": a["avg"],
                     "act": float(f["act"]), "miss": a["avg"] - float(f["act"]), "samp_sd": a["samp"] ** 0.5,
                     "src": f["src"]})
    return pd.DataFrame(rows).sort_values(["cycle", "state", "type"]).reset_index(drop=True)


def write_misses() -> pd.DataFrame:
    m = miss_table()
    out = m.copy()
    for c in ("poll_avg", "act", "miss", "samp_sd"):
        out[c] = (100 * out[c]).round(3)        # points of two-party Democratic share
    PUB.mkdir(exist_ok=True)
    out.to_csv(PUB / "us_ev_party_misses.csv", index=False)
    return m


# ------------------------------------------------------------------ 2. metric and historical test

def select_same_day(panel: pd.DataFrame) -> pd.DataFrame:
    """Select each state's 2026 cut-off and comparable earlier observations without looking past that date.

    `panel` has cumulative D/R/O counts by date and kind. Total is preferred. A state with no total series may use
    one consistent component (for example Maryland in-person early voting); it is labelled in `kind`.
    """
    p = panel.copy()
    p["date"] = pd.to_datetime(p["date"])
    p["ed"] = p["year"].map(ELECTION_DAY).map(pd.Timestamp)
    p["days_before"] = (p["ed"] - p["date"]).dt.days
    chosen = []
    for state, s in p.groupby("state"):
        kinds_2026 = set(s.loc[s["year"] == 2026, "kind"])
        if not kinds_2026:
            continue
        kind = "total" if "total" in kinds_2026 else ("inperson" if "inperson" in kinds_2026 else sorted(kinds_2026)[0])
        s = s[s["kind"] == kind]
        now = s[s["year"] == 2026].sort_values("date")
        if now.empty:
            continue
        latest = now.iloc[-1]
        target = int(latest["days_before"])
        for year, y in s.groupby("year"):
            eligible = y[(y["days_before"] >= target) & (y["days_before"] <= target + 2)]
            if eligible.empty:
                continue
            row = eligible.sort_values("days_before").iloc[0].copy()
            row["target_days_before"] = target
            row["date_lag"] = int(row["days_before"] - target)
            chosen.append(row)
    return pd.DataFrame(chosen).drop(columns=["ed"]).sort_values(["state", "year"]).reset_index(drop=True)


def ev_metrics(selected: pd.DataFrame, registration: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pre-specified EV margin and prior-cycle gap; optional registration-turnout-normalised variant."""
    d = selected.copy().sort_values(["state", "year"])
    n = d[["D", "R", "O"]].sum(axis=1)
    d["ev_margin"] = 100 * (d["D"] - d["R"]) / n
    if registration is not None and not registration.empty:
        r = registration.copy()
        r["date"] = pd.to_datetime(r["date"])
        # nearest registration snapshot in that state-cycle; election-day snapshots are allowed because registration,
        # unlike returned ballots, is a stock fixed before voting closes.
        r["ed"] = r["year"].map(ELECTION_DAY).map(pd.Timestamp)
        r["dist"] = (r["date"] - r["ed"]).abs().dt.days
        r = r.sort_values("dist").drop_duplicates(["state", "year"])
        r = r.rename(columns={"D": "reg_D", "R": "reg_R", "O": "reg_O"})
        d = d.merge(r[["state", "year", "reg_D", "reg_R", "reg_O"]], on=["state", "year"], how="left")
        tr_d, tr_r = d["D"] / d["reg_D"], d["R"] / d["reg_R"]
        d["ev_margin_norm"] = 100 * (tr_d - tr_r) / (tr_d + tr_r)
    else:
        d["ev_margin_norm"] = np.nan
    for metric, gap in (("ev_margin", "ev_gap"), ("ev_margin_norm", "ev_gap_norm")):
        d[gap] = np.nan
        d[f"{gap}_same_type"] = np.nan
        for _, ix in d.groupby("state").groups.items():
            for i in ix:
                prior = d.loc[[j for j in ix if d.at[j, "year"] < d.at[i, "year"]], metric].dropna()
                if not prior.empty:
                    d.at[i, gap] = d.at[i, metric] - prior.mean()
                same = d.loc[[j for j in ix if d.at[j, "year"] < d.at[i, "year"]
                              and d.at[j, "year"] % 4 == d.at[i, "year"] % 4], metric].dropna()
                if not same.empty:
                    d.at[i, f"{gap}_same_type"] = d.at[i, metric] - same.mean()
    return d


def race_panel(metrics: pd.DataFrame, misses: pd.DataFrame, gap: str = "ev_gap") -> pd.DataFrame:
    """Attach one state-cycle EV reading to every covered race; shares are converted to percentage points."""
    m = misses.copy()
    m["miss"] *= 100
    d = m.merge(metrics.rename(columns={"year": "cycle"}), on=["state", "cycle"])
    d = d.dropna(subset=[gap, "miss"]).copy()
    d["x"] = d[gap]
    d["x_centered"] = d["x"] - d.groupby("cycle")["x"].transform("mean")
    d["miss_centered"] = d["miss"] - d.groupby("cycle")["miss"].transform("mean")
    d["cluster"] = d["state"] + "-" + d["cycle"].astype(str)
    return d


def _loo_predictions(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for cycle in sorted(d["cycle"].unique()):
        train, test = d[d["cycle"] != cycle], d[d["cycle"] == cycle].copy()
        den = float((train["x_centered"] ** 2).sum())
        if den == 0 or train["cycle"].nunique() < 2:
            continue
        slope = float((train["x_centered"] * train["miss_centered"]).sum() / den)
        test["pred"] = slope * test["x_centered"]
        test["slope"] = slope
        out.append(test)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p, den = k / n, 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (mid - half, mid + half)


def _score(d: pd.DataFrame) -> dict:
    p = _loo_predictions(d)
    if p.empty:
        return {"n": 0}
    active = (p["x_centered"] != 0) & (p["miss_centered"] != 0)
    # Direction was pre-specified, so agreement uses -x, not the sign of the fitted slope.
    k = int((np.sign(-p.loc[active, "x_centered"]) == np.sign(p.loc[active, "miss_centered"])).sum())
    n = int(active.sum())
    rmse0 = float(np.sqrt(np.mean(p["miss_centered"] ** 2)))
    rmse1 = float(np.sqrt(np.mean((p["miss_centered"] - p["pred"]) ** 2)))
    lo, hi = _wilson(k, n)
    from scipy import stats
    return {"n": len(p), "sign_n": n, "sign_k": k, "sign_agreement": k / n if n else np.nan,
            "sign_ci_low": lo, "sign_ci_high": hi,
            "sign_p_one_sided": stats.binomtest(k, n, 0.5, alternative="greater").pvalue if n else np.nan,
            "rmse_none": rmse0, "rmse_adjusted": rmse1, "delta_rmse": rmse1 - rmse0,
            "slope_mean": float(p.groupby("cycle")["slope"].first().mean())}


def historical_test(d: pd.DataFrame, n_boot: int = 2000, seed: int = 0) -> tuple[dict, pd.DataFrame]:
    """Leave-one-cycle-out score and state-cycle cluster-bootstrap intervals."""
    score = _score(d)
    if score["n"] == 0 or n_boot == 0:
        return score, pd.DataFrame()
    rng = np.random.default_rng(seed)
    groups = {k: g for k, g in d.groupby("cluster")}
    keys = list(groups)
    boot = []
    for _ in range(n_boot):
        parts = []
        for draw, key in enumerate(rng.choice(keys, len(keys), replace=True)):
            g = groups[key].copy()
            g["cluster"] = f"{key}-{draw}"
            parts.append(g)
        b = pd.concat(parts, ignore_index=True)
        # Recompute fixed effects after resampling.
        b["x_centered"] = b["x"] - b.groupby("cycle")["x"].transform("mean")
        b["miss_centered"] = b["miss"] - b.groupby("cycle")["miss"].transform("mean")
        s = _score(b)
        if s["n"]:
            boot.append(s)
    B = pd.DataFrame(boot)
    for name in ("delta_rmse", "slope_mean", "sign_agreement"):
        if name in B and B[name].notna().any():
            score[f"{name}_ci_low"], score[f"{name}_ci_high"] = np.quantile(B[name].dropna(), [0.025, 0.975])
    return score, B


def coverage_table(races: pd.DataFrame) -> pd.DataFrame:
    return races.groupby("state").agg(cycles=("cycle", lambda x: ",".join(map(str, sorted(set(x))))),
                                      n_cycles=("cycle", "nunique"), n_races=("race", "size"),
                                      first=("cycle", "min"), last=("cycle", "max")).reset_index()


# ------------------------------------------------------------------ 3. 2026 EV-informed projection (display only)

LOYALTY = {
    "D": {"dem_probability": 0.95, "sd": 0.03},
    "R": {"dem_probability": 0.05, "sd": 0.03},
    "O": {"dem_probability": "race_poll_average", "sd": 0.08},
}


def expected_state_turnout(path: Path | None = None) -> pd.Series:
    """The production model's expected 2026 two-party votes, summed from counties to state."""
    f = path or PUB / "us2026_expected_votes_v4.csv"
    d = pd.read_csv(f, comment="#")
    d["state"] = d["unit_id"].str.split("-").str[1]
    return d.groupby("state")["exp_votes"].sum()


def _projection_one(row: pd.Series, poll: dict, expected: float, draws: int = 20_000) -> dict:
    seed = int.from_bytes(hashlib.sha256(f"{row.state}-{row.date}-{row.race}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    total = float(row.D + row.R + row.O)
    point_f = min(total / expected, 0.98)
    p = float(poll["avg"])
    d_loyal = np.clip(rng.normal(LOYALTY["D"]["dem_probability"], LOYALTY["D"]["sd"], draws), 0, 1)
    r_loyal = np.clip(rng.normal(LOYALTY["R"]["dem_probability"], LOYALTY["R"]["sd"], draws), 0, 1)
    o_loyal = np.clip(rng.normal(p, LOYALTY["O"]["sd"], draws), 0, 1)
    ev_share = (row.D * d_loyal + row.R * r_loyal + row.O * o_loyal) / total
    ev_point = (row.D * .95 + row.R * .05 + row.O * p) / total
    turnout = expected * np.exp(rng.normal(-0.5 * 0.08**2, 0.08, draws))
    f = np.clip(total / turnout, 0, 0.98)
    remaining_point = np.clip((p - point_f * ev_point) / max(1 - point_f, 0.02), 0, 1)
    remaining_sd = float(poll["sd"]) / max(1 - point_f, 0.20)
    remaining = np.clip(rng.normal(remaining_point, remaining_sd, draws), 0, 1)
    final_margin = 200 * (f * ev_share + (1 - f) * remaining) - 100
    q = np.quantile(final_margin, [.025, .10, .25, .50, .75, .90, .975])
    mean, sd = float(final_margin.mean()), float(final_margin.std(ddof=1))
    grid = np.linspace(mean - 3 * sd, mean + 3 * sd, 31) if sd else np.array([mean])
    density = np.exp(-0.5 * ((grid - mean) / sd) ** 2) / (sd * np.sqrt(2 * np.pi)) if sd else np.array([1.0])
    return {
        "ev_ballots": int(total), "expected_turnout": round(expected), "ev_fraction": round(point_f, 5),
        "official_ev_party_mix": {k: round(float(getattr(row, k)) / total, 5) for k in ("D", "R", "O")},
        "ev_dem_share_assumption": round(ev_point, 5), "remaining_dem_share": round(float(remaining_point), 5),
        "mean_margin": round(mean, 3), "sd_margin": round(sd, 3),
        "quantiles": {k: round(float(v), 3) for k, v in zip(("0.025", "0.10", "0.25", "0.50", "0.75", "0.90",
                                                              "0.975"), q)},
        "density": [[round(float(x), 3), round(float(y), 6)] for x, y in zip(grid, density)],
    }


def build_projection(panel: pd.DataFrame, out: Path | None = None) -> dict:
    """Predictive distribution by race and official EV data date; never read by the model."""
    polls_doc = json.loads((PUB / "us2026_polls.json").read_text())
    expected = expected_state_turnout()
    p = panel[(panel["year"] == 2026) & (panel["kind"] == "total")].copy()
    p["date"] = pd.to_datetime(p["date"]).dt.strftime("%Y-%m-%d")
    rows = {}
    for race, poll in polls_doc["races"].items():
        state = race.split("-")[0]
        if state not in expected.index or not poll or poll.get("avg") is None:
            continue
        for _, ev in p[p["state"] == state].sort_values("date").iterrows():
            ev["race"] = race
            rows.setdefault(race, {})[ev["date"]] = _projection_one(ev, poll, float(expected[state]))
    result = {
        "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "as_of": max((date for dates in rows.values() for date in dates), default=None),
        "note": "Display-only EV decomposition, never a model input. Registration is not a vote.",
        "schema": {"mean_margin": "Democratic minus Republican final margin, percentage points",
                   "density": "[margin, normal-density approximation] pairs"},
        "sources": {"early_vote": "official state archives; data_public/us_ev_party_panel.csv",
                    "polls": polls_doc["source"], "expected_turnout": "data_public/us2026_expected_votes_v4.csv"},
        "assumptions": {"party_choice": LOYALTY, "expected_turnout_cv": 0.08, "draws": 20_000,
                        "remaining_vote": "poll average adjusted algebraically for the official already-voted mix"},
        "mix_checks": {"available": 0, "flags": [],
                       "note": "No source publishes the party mix of a poll's already-voted subsample."},
        "races": rows,
        "latest": {race: dates[max(dates)] for race, dates in rows.items() if dates},
    }
    target = out or us.ROOT_DIR / "site" / "data" / "us2026" / "ev_projection.json"
    target.write_text(json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n")
    return result


# ------------------------------------------------------------------ 4. assembly, report, hypothesis

EV_SRC = us.US_RAW.parents[1] / "tmp" / "ev_src"          # collectors' outputs: <module>_panel.csv, <module>_reg.csv
HYP = us.ROOT_DIR / "configs" / "hypotheses" / "us2026_ev_gap_v1.toml"


def load_misses() -> pd.DataFrame:
    f = PUB / "us_ev_party_misses.csv"
    m = pd.read_csv(f) if f.exists() else write_misses()
    if f.exists():
        for c in ("poll_avg", "act", "miss", "samp_sd"):
            m[c] = m[c] / 100
    return m


def load_panel(src: Path = EV_SRC) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All collectors' cumulative EV tables (ED-30..ED) and registration snapshots; writes the compact public copy."""
    frames = [pd.read_csv(f) for f in sorted(src.glob("*_panel.csv"))]
    regs = [pd.read_csv(f) for f in sorted(src.glob("*_reg.csv"))]
    if not frames:
        raise FileNotFoundError(f"no *_panel.csv in {src}")
    p = pd.concat(frames, ignore_index=True)
    p["date"] = pd.to_datetime(p["date"])
    ed = p["year"].map(ELECTION_DAY).map(pd.Timestamp)
    p = p[(p["date"] <= ed) & (p["date"] >= ed - pd.Timedelta(days=30))]
    p = p.drop_duplicates(["state", "year", "date", "kind"], keep="last").sort_values(["state", "year", "kind", "date"])
    for c in ("D", "R", "O"):
        p[c] = p[c].astype("int64")
    out = p.assign(date=p["date"].dt.strftime("%Y-%m-%d"))[["state", "year", "date", "kind", "D", "R", "O", "note"]]
    out.to_csv(PUB / "us_ev_party_panel.csv", index=False)
    reg = pd.concat(regs, ignore_index=True) if regs else pd.DataFrame(columns=["state", "year", "date", "D", "R", "O"])
    return out, reg


VARIANTS = {"primary: EV gap": "ev_gap", "turnout-normalised": "ev_gap_norm", "same cycle type": "ev_gap_same_type"}


def run_test(panel: pd.DataFrame, reg: pd.DataFrame, misses: pd.DataFrame, n_boot: int = 2000) -> dict:
    sel = select_same_day(panel)
    met = ev_metrics(sel, reg)
    rnd = {c: 3 for c in ("ev_margin", "ev_margin_norm", "ev_gap", "ev_gap_norm", "ev_gap_same_type",
                          "ev_gap_same_type", "ev_gap_norm_same_type")}
    keep = ["state", "year", "kind", "date", "days_before", "target_days_before", "date_lag", "D", "R", "O"]
    m = met.assign(date=met["date"].dt.strftime("%Y-%m-%d"))
    m[keep + [c for c in rnd if c in m]].round(rnd).to_csv(PUB / "us_ev_party_metrics.csv", index=False)
    res = {"metrics": met}
    rows = []
    for label, gap in VARIANTS.items():
        d = race_panel(met, misses, gap)
        hist = d[d["cycle"] < 2026]
        s, _ = historical_test(hist, n_boot=n_boot) if len(hist) else ({"n": 0}, None)
        s.update(variant=label, races=len(hist), states=hist["state"].nunique(), cycles=hist["cycle"].nunique(),
                 state_cycles=hist["cluster"].nunique())
        rows.append(s)
        if gap == "ev_gap":
            res["races"] = hist
            for ty, g in hist.groupby("type"):
                t, _ = historical_test(g, n_boot=0)
                rows.append({**t, "variant": f"primary, {ty} only", "races": len(g), "states": g["state"].nunique(),
                             "cycles": g["cycle"].nunique(), "state_cycles": g["cluster"].nunique()})
            res["pooled_corr"] = float(hist[["x", "miss"]].corr().iloc[0, 1]) if len(hist) > 2 else np.nan
            res["coverage"] = coverage_table(hist)
    T = pd.DataFrame(rows)
    T.round(4).to_csv(PUB / "us_ev_party_test.csv", index=False)
    res["table"] = T
    p = res["table"].iloc[0]
    ok = (p.get("n", 0) >= 30 and p.get("cycles", 0) >= 3 and p.get("states", 0) >= 3
          and p.get("sign_ci_low", 0) > 0.5 and p.get("delta_rmse_ci_high", 1) < 0)
    res["include"] = bool(ok)
    return res


def full_slope(races: pd.DataFrame) -> float:
    return float((races["x_centered"] * races["miss_centered"]).sum() / (races["x_centered"] ** 2).sum())


def hypothesis_rows(met: pd.DataFrame, gamma: float) -> pd.DataFrame:
    """2026 covered, polled Senate/governor races: predicted race-demeaned miss = -max(0, -gamma) x~, where x~ is the
    EV gap minus its mean over the same races (as the historical cycle fixed effect, which demeans over covered races)."""
    polls = json.loads((PUB / "us2026_polls.json").read_text())["races"]
    now = met[(met["year"] == 2026)].dropna(subset=["ev_gap"])
    rows = []
    for race, p in sorted(polls.items()):
        st = race.split("-")[0]
        x = now[now["state"] == st]
        if x.empty or not p or not p.get("n_polls"):
            continue
        x = x.iloc[0]
        rows.append({"race": race, "state": st, "poll_avg": round(100 * p["avg"], 2), "n_polls": p["n_polls"],
                     "ev_kind": x["kind"], "ev_date": x["date"].strftime("%Y-%m-%d"),
                     "days_before": int(x["days_before"]), "ev_margin": round(float(x["ev_margin"]), 3),
                     "ev_gap": round(float(x["ev_gap"]), 3)})
    h = pd.DataFrame(rows)
    if h.empty:
        return h
    h["ev_gap_centered"] = (h["ev_gap"] - h["ev_gap"].mean()).round(3)
    h["pred_sign"] = (-np.sign(h["ev_gap_centered"])).astype(int)
    h["pred_miss_centered"] = (-max(0.0, -gamma) * h["ev_gap_centered"]).round(3)
    return h


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return "nan" if not np.isfinite(v) else repr(round(float(v), 4))
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def write_hypothesis(res: dict, hyp: pd.DataFrame, proj: dict | None, gamma: float, cutoff: str) -> Path:
    t = res["table"].iloc[0]
    L = [
        'id = "us2026_ev_gap_v1"',
        'kind = "hypothesis"',
        'status = "pre-registered hypothesis; never a model input"',
        f'written = {_toml(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))}',
        f'data_cutoff = {_toml("EV data through (state: date): " + cutoff + "; files read 2026-10-30")}',
        'doc = "docs/us_ev_gap.md"',
        'code = "analysis/us_ev_gap.py"',
        'hypothesis = """',
        "Where the party-registration mix of 2026 early and mail ballots is more Democratic than the state's own earlier",
        "cycles at the same number of days before Election Day (relative to the other covered states), the statewide",
        "polls understate the Democrat (poll average minus certified two-party D share is below the 2026 covered-race",
        'mean), and vice versa."""',
        "",
        "[metric]",
        'ev_margin = "100 * (D - R) / (D + R + O), cumulative ballots returned/accepted/cast by voter registration"',
        'ev_gap = "ev_margin minus the mean ev_margin of the state\'s earlier cycles (2016-2024) at the same T"',
        'timing = "T = 2026-11-03 minus the state\'s latest 2026 data date; past cycles use the latest data dated in [ED-T-2, ED-T]"',
        'centering = "ev_gap and miss each minus their mean over the scored races listed below (cycle fixed effect)"',
        'prediction = "sign(miss_centered) = -sign(ev_gap_centered); size = -max(0, -gamma) * ev_gap_centered"',
        f"gamma = {_toml(gamma)}",
        'gamma_unit = "points of two-party share of miss per point of EV D-R margin gap (fitted 2016-2024, cycle-demeaned)"',
        'outcome = "miss = 100 * (v3 poll average as of 2026-11-02 - certified two-party D share); centered over the scored races"',
        "",
        "[primary]",
        'test = "sign agreement of -ev_gap_centered with miss_centered over the races below (zero values dropped); one-sided exact binomial test against 0.5"',
        'report = "one-sided binomial p-value"',
        f'power_note = {_toml(f"with {len(hyp)} races the smallest attainable one-sided p is {0.5 ** len(hyp):.4g}")}',
        'family = ["us2026_ev_gap_v1", "us2026_drift_v1"]',
        'correction = "holm"',
        "alpha = 0.05",
        'scored = "after certification of every covered race"',
        'secondary = ["sign agreement on the uncentered miss", "Delta RMSE of centered miss after subtracting the predicted size"]',
        "",
        "[historical]",
        f"races = {_toml(int(t.get('races', 0)))}",
        f"state_cycles = {_toml(int(t.get('state_cycles', 0)))}",
        f"sign_agreement_loco = {_toml(t.get('sign_agreement', np.nan))}",
        f"sign_p_one_sided = {_toml(t.get('sign_p_one_sided', np.nan))}",
        f"delta_rmse_loco = {_toml(t.get('delta_rmse', np.nan))}",
        f"delta_rmse_ci = {_toml([t.get('delta_rmse_ci_low', np.nan), t.get('delta_rmse_ci_high', np.nan)])}",
        f"include_in_model = {_toml(res['include'])}",
    ]
    for _, r in hyp.iterrows():
        L += ["", "[[race]]"] + [f"{k} = {_toml(v)}" for k, v in r.items()]
        pr = (proj or {}).get("latest", {}).get(r["race"])
        if pr:
            L += [f"ev_projection_mean_margin = {_toml(pr['mean_margin'])}",
                  f"ev_projection_sd_margin = {_toml(pr['sd_margin'])}",
                  f"ev_projection_q10_q90 = {_toml([pr['quantiles']['0.10'], pr['quantiles']['0.90']])}"]
    HYP.parent.mkdir(parents=True, exist_ok=True)
    HYP.write_text("\n".join(L) + "\n")
    return HYP


def _md(df: pd.DataFrame) -> str:
    return df.to_string(index=False, float_format=lambda v: f"{v:.3f}")


def main(what: str = "all") -> None:
    if what == "misses":
        m = write_misses()
        print(m.groupby(["cycle", "type"]).agg(n=("miss", "size"), mean=("miss", "mean")).round(4))
        return
    misses = load_misses()
    panel, reg = load_panel()
    res = run_test(panel, reg, misses)
    print(_md(res["coverage"]))
    print(_md(res["table"]))
    print("pooled corr (descriptive):", round(res["pooled_corr"], 3), "include:", res["include"])
    gamma = full_slope(res["races"])
    hyp = hypothesis_rows(res["metrics"], gamma)
    print(_md(hyp) if len(hyp) else "no covered 2026 races")
    proj = build_projection(panel)
    now = res["metrics"][res["metrics"]["year"] == 2026]
    cutoff = ", ".join(f"{s}: {d:%Y-%m-%d}" for s, d in zip(now["state"], now["date"]))
    print(write_hypothesis(res, hyp, proj, gamma, cutoff))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
