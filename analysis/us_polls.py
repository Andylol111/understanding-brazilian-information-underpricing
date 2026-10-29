"""US midterms 2026: poll error, poll averages and the model v2 configs.

  uv run python analysis/us_polls.py error            # historical error of final-week poll averages (POLL_SD_HIST)
  uv run python analysis/us_polls.py build [--day D]  # VoteHub polls -> data_public/us2026_polls.json
  uv run python analysis/us_polls.py configs          # configs/us2026_*_v2.toml from the v1 configs + the averages

Rule (fixed before computing; docs/us_midterms.md#polls): brunder.us.poll_rows / poll_average.
error: FiveThirtyEight pollster-ratings raw_polls.csv (CC BY 4.0), Senate and governor general elections 2016-2022,
Democrat vs Republican questions with median field date at most 7 days before the election; per race the plain
mean of the polls' two-party Dem share minus the actual two-party share; RMSE over races.
Raw files stay under data/raw/us/polls (gitignored).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

RAW_538 = "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/raw_polls.csv"


def error() -> None:
    f = us.US_RAW / "polls" / "538_raw_polls.csv"
    us._fetch(RAW_538, f)                            # cached; downloaded once
    d = pd.read_csv(f)
    d = d[d["type_simple"].isin(["Sen-G", "Gov-G"]) & d["cycle"].between(2016, 2024) & (d["time_to_election"] <= 7)
          & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")].copy()
    d["s_poll"] = d["cand1_pct"] / (d["cand1_pct"] + d["cand2_pct"])
    d["s_act"] = d["cand1_actual"] / (d["cand1_actual"] + d["cand2_actual"])
    r = d.groupby("race").agg(avg=("s_poll", "mean"), act=("s_act", "first"), cycle=("cycle", "first"))
    r["err"] = r["avg"] - r["act"]
    print(f"{len(r)} races, {len(d)} polls, cycles {sorted(r['cycle'].unique())}")
    print(f"RMSE {np.sqrt((r['err'] ** 2).mean()):.4f}  mean (Dem overstatement) {r['err'].mean():.4f}  "
          f"sd {r['err'].std():.4f}   [brunder.us.POLL_SD_HIST = {us.POLL_SD_HIST}]")


def build(day: str | None) -> None:
    out = us.build_polls(us.fetch_polls(day))
    n = sum(v is not None for v in out["races"].values())
    print(f"{us.POLLS_PUBLIC}: {n} of {len(out['races'])} races with polls in the window")


def _after_block(body: str, section: str, lines: list[str]) -> str:
    """Append lines at the end of a TOML section's key block (before its trailing blank line)."""
    m = re.search(rf"\[{section}\][^\n]*\n(?:[^\n\[][^\n]*\n)*?(?=\n|\[)", body)
    return body[:m.end()] + "".join(x + "\n" for x in lines) + body[m.end():]


def configs() -> None:
    polls = us.load_polls()
    built = __import__("json").loads(us.POLLS_PUBLIC.read_text())["built"]
    priors = us.load_priors()
    for p in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")):
        text = p.read_text()
        cfg = us.load_config(p)
        rid = cfg["election"]["race_id"]
        pa = polls.get(rid)
        pri = us.race_prior(cfg, priors)
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        body = text[text.index("\nid = ") + 1:].replace(f'id = "{cfg["id"]}"', f'id = "{cfg["id"][:-3]}_v2"', 1)
        head = [f"# US midterms 2026-11-03, {cfg['election']['name']}: model v2. Identical to {p.name} (sha256 {sha})",
                "# except a prior on the statewide swing from the polls ([model] swing_prior_*; brunder.nowcast): the",
                "# swing is the precision-weighted mix of the poll prior and the swing in counted votes, so the polls",
                "# lead early and the count takes over. Poll average frozen here (rule docs/us_midterms.md#polls;",
                f"# VoteHub polls, CC BY 4.0, data built {built}); nothing is fetched on the night. v1 and v2 are both",
                "# registered and scored."]
        status = "poll prior"
        if pa is None:
            head.append("# No polls in the window: no poll prior (v2 = v1).")
            status = "NO polls (v2 = v1)"
        else:
            body = _after_block(body, "prior", [
                f"poll_avg = {pa['avg']}           # two-party Dem share, weighted average of {pa['n_polls']} polls",
                f"poll_sd = {pa['sd']}            # sqrt(POLL_SD_HIST^2 + sampling variance)",
                f"poll_n = {pa['n_polls']}",
                f'poll_last = "{pa["last_date"]}"      # latest field end date',
                f'poll_asof = "{pa["asof"]}"      # window and weights counted back from this day',
                f"poll_p_win = {pa['p_win']}        # display only: Phi((avg - 0.5) / sd)"])
            if pri is None or pri.empty:
                head.append("# No county prior for this race yet: the swing prior needs its baseline (v2 = v1).")
                status = "polls but NO county prior (v2 = v1)"
            else:
                base = us.baseline_share(pri)
                body = _after_block(body, "model", [
                    f"swing_prior_mean = {pa['avg'] - base:.4f}   # poll_avg - prior statewide share {base:.4f} "
                    f"(V-weighted, data_public/us2026_priors.csv)",
                    f"swing_prior_sd = {pa['sd']}      # = poll_sd"])
        q = p.with_name(p.name.replace("_v1.toml", "_v2.toml"))
        q.write_text("\n".join(head) + "\n" + body)
        print(q.name, status)


CHECK = (0.0, 0.01, 0.05, 0.10, 0.25, 0.50)


def _poll_prior_538(race: str) -> dict:
    """Our averaging rule applied to FiveThirtyEight raw_polls rows of a past race (median field date stands in for
    the end date; one question per poll), as of the day before the election."""
    d = pd.read_csv(us.US_RAW / "polls" / "538_raw_polls.csv")
    d = d[(d["race"] == race) & (d["cand1_party"] == "DEM") & (d["cand2_party"] == "REP")].drop_duplicates("poll_id")
    rows = pd.DataFrame({"pollster": d["pollster"], "end": d["polldate"], "n": d["samplesize"],
                         "dem": d["cand1_pct"], "opp": d["cand2_pct"],
                         "s": d["cand1_pct"] / (d["cand1_pct"] + d["cand2_pct"])}).dropna()
    asof = (pd.Timestamp(d["electiondate"].iloc[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return us.poll_average(rows.reset_index(drop=True), asof)


def _v2(cfg: dict, pri: pd.DataFrame, avg: float, sd: float) -> dict:
    return {**cfg, "model": {**cfg["model"], "swing_prior_mean": avg - us.baseline_share(pri), "swing_prior_sd": sd}}


def effect(n_sim: int = 30) -> None:
    """P (v1) vs P_v2 as the count grows. (a) Georgia 2022 governor and Senate: certified county results by vote type,
    simulated count orders (analysis/us_calibrate.py), production GA settings, v2 prior = our rule on 538's 2022
    polls. (b) Synthetic nights for every modeled 2026 race with a county prior: county truth = prior + a uniform
    swing to the target + N(0, 0.03) county noise, whole counties reported in random order; two targets, the 2026
    poll average ("polls right") and the prior's statewide share ("polls wrong by the shift")."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import us_calibrate as cal
    priors = us.load_priors()
    rng = np.random.default_rng(20261102)
    out = []
    ga = us.load_config(us.ROOT_DIR / "configs" / "us2026_ga_sen_v1.toml")
    for name, eid, race, prior_src, ratio, r538 in [
            ("GA 2022 gov (replay)", "2022NovGen", "gov", "ga_pres_2020", 0.80, "2022_Gov-G_GA"),
            ("GA 2022 sen (replay)", "2022NovGen", "sen", "ga_pres_2020", 0.80, "2022_Sen-G_GA")]:
        bt = cal.by_type(eid, race)
        cfg = {**ga, "election": {**ga["election"], "race": "x"}, "prior": {"share": prior_src, "votes": prior_src},
               "count": {**ga["count"], "turnout_ratio": ratio}}
        pri = us.race_prior(cfg, priors)
        pa = _poll_prior_538(r538)
        cfg2 = _v2(cfg, pri, pa["avg"], pa["sd"])
        total = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        final = (bt["early_d"] + bt["late_d"]).sum() / total
        print(f"{name}: final {final:.4f}, prior {us.baseline_share(pri):.4f}, polls {pa['avg']:.4f} ± {pa['sd']:.4f}"
              f" ({pa['n_polls']} polls)")
        for s in range(n_sim):
            ev = cal.events(bt, rng)
            cum = ((ev["d"] + ev["r"]).cumsum() / total).to_numpy()
            for c in CHECK:
                rows = cal.rows_at(ev, int(np.searchsorted(cum, c)) + 1) if c > 0 else us._frame([])
                r1, r2 = us.project(rows, cfg, pri)[0], us.project(rows, cfg2, pri)[0]
                out.append({"case": name, "truth": "actual", "check": c, "P": r1["p_win"], "P_v2": r2["p_win"],
                            "err": r1["share_hat"] - final, "err_v2": r2["share_hat"] - final,
                            "w_prior": r2.get("swing_prior_weight")})
    polls = us.load_polls()
    for p2 in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v2.toml")):
        cfg2 = us.load_config(p2)
        cfg = us.load_config(p2.with_name(p2.name.replace("_v2", "_v1")))
        pri = us.race_prior(cfg, priors)
        if pri is None or "swing_prior_sd" not in cfg2["model"]:
            continue
        rid = cfg["election"]["race_id"]
        base, avg = us.baseline_share(pri), polls[rid]["avg"]
        E = (pri["V"] * us.rules(cfg).turnout_ratio).to_numpy()
        cols = {"level": "county", "race": cfg["election"]["race"], "candidate": "", "name": "", "early": np.nan,
                "units": 1.0, "reporting": 1.0, "ballots": np.nan, "registered": np.nan, "outstanding": np.nan}
        for truth_name, target in (("polls right", avg), ("polls wrong", base)):
            for s in range(n_sim):
                sh = np.clip(pri["s0"].to_numpy() + (target - base) + rng.normal(0, 0.03, len(pri)), 0.02, 0.98)
                final = float((E * sh).sum() / E.sum())
                order = rng.permutation(len(pri))
                cum = np.cumsum(E[order]) / E.sum()
                for c in CHECK:
                    k = int(np.searchsorted(cum, c)) + 1 if c > 0 else 0
                    idx = order[:k]
                    rows = (pd.concat([pd.DataFrame({**cols, "unit_id": pri["unit_id"].to_numpy()[idx], "party": pt,
                                                     "votes": E[idx] * (sh[idx] if pt == "DEM" else 1 - sh[idx])})
                                       for pt in ("DEM", "REP")], ignore_index=True)[us.COLS]
                            if k else us._frame([]))
                    r1, r2 = us.project(rows, cfg, pri)[0], us.project(rows, cfg2, pri)[0]
                    out.append({"case": f"{rid} (synthetic)", "truth": truth_name, "check": c, "P": r1["p_win"],
                                "P_v2": r2["p_win"], "err": r1["share_hat"] - final, "err_v2": r2["share_hat"] - final,
                                "w_prior": r2.get("swing_prior_weight")})
    df = pd.DataFrame(out)
    pd.set_option("display.width", 220)
    tab = df.groupby(["case", "truth", "check"]).agg(P=("P", "mean"), P_v2=("P_v2", "mean"),
                                                     mae=("err", lambda e: e.abs().mean()),
                                                     mae_v2=("err_v2", lambda e: e.abs().mean()),
                                                     w_prior=("w_prior", "mean")).round(3).reset_index()
    print(tab.to_string(index=False))
    f = us.US_PROC / "polls_v2_effect.csv"
    f.parent.mkdir(parents=True, exist_ok=True)
    tab.to_csv(f, index=False)
    print(f"table: {f}")



if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["error", "build", "configs", "effect"])
    ap.add_argument("--day", help="VoteHub cache day YYYYmmdd (default today)")
    a = ap.parse_args()
    {"error": error, "build": lambda: build(a.day), "configs": configs, "effect": effect}[a.step]()
