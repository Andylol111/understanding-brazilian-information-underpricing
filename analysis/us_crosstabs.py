"""Recalled 2024 vote in the published crosstabs of our 2026 polls, a recalled-vote reweighting of each topline, and a
historical check of whether D-leaning pollsters missed more (display / analysis only; registered configs untouched).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_crosstabs.py     # → site/data/us2026/hub/crosstabs.json

Inputs
  site/data/us2026/hub/polls.json      polls in our averages (VoteHub, CC BY 4.0) and their weights
  data_public/us2026_crosstabs.csv     what each poll's public release prints (recalled 2024 vote, vote by recalled vote,
                                       party / education / race / age mix, LV screen, mode); read 2026-10-29, see
                                       docs/us_crosstabs.md for how it was collected and checked
  data_public/us2026_state_pres24.csv  certified 2024 president by state (MEDSL, CC0)
  538 raw_polls.csv (CC BY 4.0)        Senate/governor polls 2016-2022 with results, for the historical test

All margins are two-party Democratic-side minus opponent, in points.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_polls

HUB = us.ROOT_DIR / "site" / "data" / "us2026" / "hub"
CROSSTABS = us.ROOT_DIR / "data_public" / "us2026_crosstabs.csv"
PRES24 = us.STATE_PRES24
SHRINK_K = 10          # pollster lean shrinkage: lean x n / (n + K)
LEAN_MIN_N = 5         # pollsters need >= 5 earlier polls to get a prior lean
LEAN_BAND = 1.0        # |prior lean| <= 1 point = "neutral"


def _r(x, nd=2):
    return None if x is None or not np.isfinite(float(x)) else round(float(x), nd)


def margin2(a: float, b: float) -> float:
    """Two-party margin of a over b, points."""
    return 100.0 * (a - b) / (a + b)


def recall_gap(harris: float, trump: float, act_dem: float, act_rep: float) -> float:
    """Recalled Harris-Trump two-party margin minus the certified 2024 two-party margin (+ = sample recalls more Harris
    than the state actually voted)."""
    return margin2(harris, trump) - margin2(act_dem, act_rep)


def reweight_shift(shares: dict[str, float], act_dem: float, act_rep: float,
                   m_harris: float, m_trump: float) -> float:
    """Change in the poll's dem-minus-opp margin (points of the full sample) when the recalled Harris and Trump groups are
    re-weighted so that their two-party split equals the certified 2024 result, holding the combined Harris+Trump share
    and the other/non-voter groups fixed (one-variable post-stratification). m_* = dem-minus-opp margin within the
    group, points."""
    tot = sum(v for v in shares.values() if v is not None and np.isfinite(v))
    sh, st = shares["harris"] / tot, shares["trump"] / tot
    h_rec, h_act = sh / (sh + st), act_dem / (act_dem + act_rep)
    return (sh + st) * (h_act - h_rec) * (m_harris - m_trump)


def _shares(row: pd.Series) -> dict[str, float]:
    other = sum(x for x in (row["recall_other"], row["recall_nonvoter"]) if pd.notna(x))
    rest = max(0.0, 100.0 - row["recall_harris"] - row["recall_trump"] - other)   # unprinted remainder (not sure, etc.)
    return {"harris": row["recall_harris"], "trump": row["recall_trump"], "other": other + rest}


def load_crosstabs() -> pd.DataFrame:
    return pd.read_csv(CROSSTABS, skiprows=1)        # line 1 is the provenance note (URLs may contain '#')


def per_poll(polls: dict, xt: pd.DataFrame, pres: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    for rk, r in polls["races"].items():
        for p in r["polls"]:
            if p["weight"]:
                rows.append({"race": rk, "state": rk.split("-")[0], "dem_name": r["dem"], "opp_name": r["opp"], **p})
    d = pd.DataFrame(rows).merge(xt.drop(columns=["pollster", "race"]), on="id", how="left")
    d["raw"] = 100 * (2 * d["dem2"] - 1)
    rec = d["status"].isin(["recall", "recall_xtab"])
    d["act"] = d["state"].map(lambda s: margin2(pres.loc[s, "dem"], pres.loc[s, "rep"]))
    d["recall_margin"] = np.where(rec, 100 * (d["recall_harris"] - d["recall_trump"])
                                  / (d["recall_harris"] + d["recall_trump"]), np.nan)
    d["gap"] = d["recall_margin"] - d["act"]
    cells = d["status"] == "recall_xtab"
    d["m_h"] = d["dem_among_harris"] - d["opp_among_harris"]
    d["m_t"] = d["dem_among_trump"] - d["opp_among_trump"]
    # polls that print shares but not the vote by recalled vote: the median Harris-minus-Trump group spread of the
    # polls that print both
    spread = (d.loc[cells, "m_h"] - d.loc[cells, "m_t"]).median()
    kappa = {"spread_median": _r(spread, 1), "n_polls": int(cells.sum()),
             "spread_range": [_r((d.loc[cells, "m_h"] - d.loc[cells, "m_t"]).min(), 1),
                              _r((d.loc[cells, "m_h"] - d.loc[cells, "m_t"]).max(), 1)]}
    shift = []
    for _, x in d.iterrows():
        if x["status"] not in ("recall", "recall_xtab"):
            shift.append(np.nan)
            continue
        mh, mt = (x["m_h"], x["m_t"]) if x["status"] == "recall_xtab" else (spread, 0.0)
        s = reweight_shift(_shares(x), pres.loc[x["state"], "dem"], pres.loc[x["state"], "rep"], mh, mt)
        shift.append(s * 100.0 / (x["dem"] + x["opp"]))          # full-sample points → two-party points
    d["shift"] = shift
    d["adj"] = d["raw"] + d["shift"].fillna(0.0)
    d["adj_method"] = np.where(cells, "cells", np.where(rec, "approx", None))
    return d, kappa


def per_race(d: pd.DataFrame, polls: dict) -> list[dict]:
    out = []
    for rk, g in d.groupby("race", sort=True):
        w = g["weight"] / g["weight"].sum()
        rec = g["shift"].notna()
        wr = g.loc[rec, "weight"] / g.loc[rec, "weight"].sum() if rec.any() else None
        out.append({"race": rk, "dem": polls["races"][rk]["dem"], "opp": polls["races"][rk]["opp"],
                    "n_polls": len(g), "n_crosstabs": int(g["status"].isin(["composition", "recall", "recall_xtab"]).sum()),
                    "n_recall": int(rec.sum()), "recall_weight": _r(g.loc[rec, "weight"].sum() / g["weight"].sum(), 3),
                    "raw": _r((w * g["raw"]).sum()), "adj": _r((w * g["adj"]).sum()),
                    "raw_recall_polls": _r((wr * g.loc[rec, "raw"]).sum()) if rec.any() else None,
                    "adj_recall_polls": _r((wr * g.loc[rec, "adj"]).sum()) if rec.any() else None,
                    "mean_gap": _r(g.loc[rec, "gap"].mean()) if rec.any() else None})
    return out


def per_pollster(d: pd.DataFrame, lean: dict[str, float]) -> list[dict]:
    out = []
    for name, g in d.groupby("pollster"):
        rec = g["shift"].notna()
        st = g["status"].fillna("no url").value_counts().to_dict()
        out.append({"pollster": name, "polls": len(g), "status": {k: int(v) for k, v in st.items()},
                    "n_recall": int(rec.sum()), "mean_gap": _r(g.loc[rec, "gap"].mean()) if rec.any() else None,
                    "mean_shift": _r(g.loc[rec, "shift"].mean()) if rec.any() else None,
                    "house_538": lean.get(_norm(name))})
    return sorted(out, key=lambda x: (-x["polls"], x["pollster"]))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("university", "u").replace("college", ""))


# ------------------------------------------------------------------ historical test (538 raw_polls)

def raw538() -> pd.DataFrame:
    f = us.US_RAW / "polls" / "538_raw_polls.csv"
    us._fetch(us_polls.RAW_538, f)
    d = pd.read_csv(f)
    d = d[d["type_simple"].isin(["Sen-G", "Gov-G"]) & d["cycle"].between(2016, 2022) & (d["time_to_election"] <= 21)
          & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")].drop_duplicates(["poll_id", "race_id"])
    d = d.assign(err=2 * 100 * (d["cand1_pct"] / (d["cand1_pct"] + d["cand2_pct"])
                                - d["cand1_actual"] / (d["cand1_actual"] + d["cand2_actual"])))
    d = d[["cycle", "race_id", "race", "pollster", "pollster_rating_id", "err"]]
    g = d.groupby("race_id")["err"]
    return d.assign(rel=(d["err"] - g.transform("mean")).where(g.transform("size") >= 3))   # house effect within race


def lean_test(d: pd.DataFrame, cycle: int) -> dict:
    """Out-of-sample: each pollster's lean in all earlier cycles (shrunk) as the proxy for a D-leaning sample, against
    its error in `cycle`. 'absolute' lean = mean signed error (contains the industry-wide miss of those cycles);
    'relative' lean = mean error minus the race's mean poll error (the pollster's own house effect)."""
    t = d[d["cycle"] == cycle]
    out = {"cycle": cycle, "polls": len(t), "cycle_mean_err": _r(t["err"].mean())}
    for kind, col in (("absolute", "err"), ("relative", "rel")):
        prior = d[d["cycle"] < cycle].groupby("pollster_rating_id")[col].agg(["mean", "count"])
        prior = prior[prior["count"] >= LEAN_MIN_N]
        x = t.assign(lean=t["pollster_rating_id"].map(prior["mean"]), n_prior=t["pollster_rating_id"].map(prior["count"]))
        rated = x[x["lean"].notna()].copy()
        rated["corr_err"] = rated["err"] - rated["lean"] * rated["n_prior"] / (rated["n_prior"] + SHRINK_K)
        grp = np.where(rated["lean"] > LEAN_BAND, "d_lean", np.where(rated["lean"] < -LEAN_BAND, "r_lean", "neutral"))
        by = {k: {"polls": int((grp == k).sum()), "mean_err": _r(rated.loc[grp == k, "err"].mean()),
                  "rmse": _r(np.sqrt((rated.loc[grp == k, "err"] ** 2).mean()))} for k in ("d_lean", "neutral", "r_lean")}
        # race averages: plain mean of each race's polls, raw vs with the rated polls' lean removed
        x = x.assign(corr_err=x["err"])
        x.loc[rated.index, "corr_err"] = rated["corr_err"]
        ra = x.groupby("race_id")[["err", "corr_err"]].mean()
        mse_raw, mse_corr = float((rated["err"] ** 2).mean()), float((rated["corr_err"] ** 2).mean())
        out[kind] = {
            "rated_polls": len(rated), "rated_pollsters": int(rated["pollster_rating_id"].nunique()), "by_prior_lean": by,
            "corr_lean_err": _r(rated[["lean", "err"]].corr().iloc[0, 1], 3),
            "corr_lean_house": _r(rated[["lean", "rel"]].corr().iloc[0, 1], 3),
            "rmse_raw": _r(np.sqrt(mse_raw)), "rmse_corrected": _r(np.sqrt(mse_corr)),
            "mse_removed_pct": _r(100 * (1 - mse_corr / mse_raw), 1),
            "races": len(ra), "race_avg_rmse_raw": _r(np.sqrt((ra["err"] ** 2).mean())),
            "race_avg_rmse_corrected": _r(np.sqrt((ra["corr_err"] ** 2).mean())),
            "race_avg_mean_raw": _r(ra["err"].mean()), "race_avg_mean_corrected": _r(ra["corr_err"].mean())}
    return out


def decomposition(d: pd.DataFrame) -> dict:
    """Share of poll-level squared error 2016-2022 that is (a) the cycle-wide mean, (b) race within cycle, (c) the
    pollster's own deviation within the race (house effect + noise)."""
    e = d["err"]
    cyc = d.groupby("cycle")["err"].transform("mean")
    race = d.groupby("race_id")["err"].transform("mean")
    tot = float((e ** 2).sum())
    return {"polls": len(d), "rmse": _r(np.sqrt((e ** 2).mean())),
            "cycle_mean_pct": _r(100 * float((cyc ** 2).sum()) / tot, 1),
            "race_within_cycle_pct": _r(100 * float(((race - cyc) ** 2).sum()) / tot, 1),
            "poll_within_race_pct": _r(100 * float(((e - race) ** 2).sum()) / tot, 1),
            "cycle_means": {int(c): _r(v) for c, v in d.groupby("cycle")["err"].mean().items() if c % 2 == 0}}


def lean_2026(d538: pd.DataFrame) -> dict[str, float]:
    """Each pollster's 2016-2022 house effect: mean of (poll error minus its race's mean poll error), two-party margin
    points, + = more Democratic than the other polls of the same race (normalised name → points)."""
    g = d538.groupby("pollster")["rel"].agg(["mean", "count"])
    return {_norm(k): _r(v["mean"]) for k, v in g.iterrows() if v["count"] >= LEAN_MIN_N}


# ------------------------------------------------------------------ build

def build() -> Path:
    polls = json.loads((HUB / "polls.json").read_text())
    xt = load_crosstabs()
    pres = pd.read_csv(PRES24, comment="#").set_index("state")
    d, kappa = per_poll(polls, xt, pres)
    d538 = raw538()
    lean = lean_2026(d538)
    pl = per_pollster(d, lean)
    races = per_race(d, polls)
    hist = [lean_test(d538, c) for c in (2018, 2020, 2022)]
    rec = d[d["shift"].notna()]
    both = [(x["mean_gap"], x["house_538"]) for x in pl if x["mean_gap"] is not None and x["house_538"] is not None]
    st = d["status"].fillna("blocked")
    read = ("composition", "recall", "recall_xtab")
    no_read = [x for x in pl if not any(k in x["status"] for k in read)]
    blocked = sorted(x["pollster"] for x in no_read if set(x["status"]) <= {"blocked", "robots"})
    not_found = sorted(x["pollster"] for x in no_read if x["pollster"] not in blocked)
    poll_rows = []
    ds = d.sort_values(["race", "end"])
    for _, x in ds.astype(object).where(ds.notna(), None).iterrows():
        recd = x["status"] in ("recall", "recall_xtab")
        poll_rows.append({
            "id": x["id"], "race": x["race"], "pollster": x["pollster"], "end": x["end"], "n": int(x["n"]),
            "population": x["population"], "weight": _r(x["weight"], 4), "status": x["status"],
            "source_url": x["source_url"] if isinstance(x["source_url"], str) else None,
            "raw": _r(x["raw"]), "adj": _r(x["adj"]) if recd else None, "shift": _r(x["shift"]) if recd else None,
            "adj_method": x["adj_method"],
            "recall": {k: _r(x[f"recall_{k}"], 1) for k in ("harris", "trump", "other", "nonvoter")} if recd else None,
            "recall_margin": _r(x["recall_margin"]) if recd else None, "actual_2024": _r(x["act"]),
            "gap": _r(x["gap"]) if recd else None,
            "party": ({"dem": _r(x["party_dem"], 1), "rep": _r(x["party_rep"], 1), "ind": _r(x["party_ind"], 1),
                       "kind": x["party_kind"]} if pd.notna(x["party_dem"]) else None),
            "college": _r(x["college_grad"], 1), "white": _r(x["white"], 1),
            "age_65plus": _r(x["age_65plus"], 1), "age_18_29": _r(x["age_18_29"], 1),
            "lv_screen": x["lv_screen"] if isinstance(x["lv_screen"], str) else None,
            "mode": x["mode"] if isinstance(x["mode"], str) else None})
    hist_tot = {k: {"mse_removed_pct_mean": _r(np.mean([h[k]["mse_removed_pct"] for h in hist]), 1)}
                for k in ("absolute", "relative")}
    obj = {
        "built": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"), "asof": polls["asof"],
        "label": "display only; registered models unchanged",
        "units": "two-party margin, Democratic side minus opponent, points; gap = recalled Harris-Trump two-party margin "
                 "minus certified 2024 state margin (+ = sample recalls more Harris than the state voted)",
        "sources": [polls["source"],
                    {"name": "pollster / sponsor release pages and linked crosstab PDF/XLSX (per poll: source_url)",
                     "url": "docs/us_crosstabs.md", "licence": "facts quoted from public releases"},
                    {"name": "MIT Election Data and Science Lab, 2024-president-state.csv (certified)",
                     "url": "https://github.com/MEDSL/2024-elections-official", "licence": "CC0 1.0"},
                    {"name": "FiveThirtyEight pollster ratings, raw_polls.csv",
                     "url": "https://github.com/fivethirtyeight/data/tree/master/pollster-ratings", "licence": "CC BY 4.0"}],
        "method": {
            "adjustment": "re-weight recalled Harris and Trump voters so their two-party split equals the certified 2024 "
                          "state result, other / non-voter groups fixed; 'cells' = the poll prints the race by recalled "
                          "vote; 'approx' = shares only, group spread = median of the 'cells' polls",
            "kappa": kappa,
            "caveat": "recalled-vote weighting has known biases: some 2024 voters misreport or forget (winner-recall "
                      "drift towards Trump, non-recall among low-propensity Trump voters), 'shy' respondents, and "
                      "midterm electorates differ from 2024's; many of these pollsters already weight on recalled vote, "
                      "so a remaining gap can reflect the LV screen rather than the sample. Raw and adjusted are both "
                      "shown; neither is a forecast."},
        "coverage": {"polls": len(d), "pollsters": int(d["pollster"].nunique()),
                     "status": {k: int(v) for k, v in st.value_counts().items()},
                     "with_crosstabs": int(st.isin(["composition", "recall", "recall_xtab"]).sum()),
                     "with_recall": len(rec), "with_recall_by_vote": int((st == "recall_xtab").sum()),
                     "recall_weight_share": _r(rec["weight"].sum() / d["weight"].sum(), 3),
                     "pollsters_not_found": not_found, "pollsters_blocked": blocked},
        "summary": {"mean_gap": _r(rec["gap"].mean()), "median_gap": _r(rec["gap"].median()),
                    "mean_shift": _r(rec["shift"].mean()), "polls_gap_d": int((rec["gap"] > 0).sum()),
                    "polls_gap_r": int((rec["gap"] < 0).sum()),
                    "gap_vs_538_house": {"pollsters": len(both),
                                        "corr": _r(np.corrcoef(*zip(*both))[0, 1], 3) if len(both) > 2 else None}},
        "races": races, "pollsters": pl, "polls": poll_rows,
        "history": {
            "question": "did pollsters whose samples leaned D (proxy: their own mean signed error in earlier cycles, "
                        "538 raw_polls) miss more in the next cycle, and how much would removing that lean have fixed?",
            "data": "538 raw_polls, Senate/governor general, final 21 days, D vs R; 2024 Senate/governor polls with "
                    "pollster detail are not in any open-licence file we have (538 raw_polls ends with 2023; VoteHub "
                    "serves only the current cycle), and no open archive of 2018-2022 recalled-2020-vote crosstabs exists",
            "lean_rule": f"prior lean = pollster's mean error (absolute) or mean error minus its race's mean poll error "
                         f"(relative) in all earlier cycles, >= {LEAN_MIN_N} polls, shrunk n/(n+{SHRINK_K}) before "
                         f"subtracting; d_lean > +{LEAN_BAND}, r_lean < -{LEAN_BAND}",
            "cycles": hist, "overall": hist_tot, "decomposition": decomposition(d538)},
    }
    f = HUB / "crosstabs.json"
    f.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"{f}: {f.stat().st_size / 1e6:.2f} MB")
    return f


if __name__ == "__main__":
    build()
