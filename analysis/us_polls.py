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


def history_averages() -> pd.DataFrame:
    """Our averaging rule (brunder.us.poll_average, 45-day window) on every 2016-2022 Senate/governor race of 538's
    raw_polls.csv (D vs R questions, one per poll; median field date for the end date), as of the day before the
    election: race, cycle, n_polls, n_pollsters, err = average − certified two-party share."""
    f = us.US_RAW / "polls" / "538_raw_polls.csv"
    us._fetch(RAW_538, f)
    d = pd.read_csv(f)
    d = d[d["type_simple"].isin(["Sen-G", "Gov-G"]) & d["cycle"].between(2016, 2022) & (d["cand1_party"] == "DEM")
          & (d["cand2_party"] == "REP")].drop_duplicates("poll_id")
    out = []
    for race, g in d.groupby("race"):
        rows = pd.DataFrame({"pollster": g["pollster"], "end": g["polldate"], "n": g["samplesize"],
                             "dem": g["cand1_pct"], "opp": g["cand2_pct"],
                             "s": g["cand1_pct"] / (g["cand1_pct"] + g["cand2_pct"])}).dropna().reset_index(drop=True)
        a = us.poll_average(rows, (pd.Timestamp(g["electiondate"].iloc[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        if a:
            act = g["cand1_actual"].iloc[0] / (g["cand1_actual"].iloc[0] + g["cand2_actual"].iloc[0])
            out.append({"race": race, "cycle": int(g["cycle"].iloc[0]), "n_polls": a["n_polls"],
                        "n_pollsters": a["n_pollsters"], "err": a["avg"] - act})
    return pd.DataFrame(out)


def errorbins() -> None:
    """RMSE of the poll average by number of polls in the window (bins 1-2 / 3-5 / 6-10 / 11+, adjacent bins pooled
    where a larger bin is not more accurate), all years; and the out-of-sample check: bins fit on 2016-2020,
    coverage of the 2022 races."""
    r = history_averages()
    rmse = lambda e: float(np.sqrt((e**2).mean()))
    edges, labels = [0, 2, 5, 10, 10**6], ["1-2", "3-5", "6-10", "11+"]
    r["bin"] = pd.cut(r["n_polls"], edges, labels=labels)
    print(r.groupby("bin", observed=False)["err"].agg(races="size", rmse=rmse, mean="mean").round(4).to_string())
    print("by pollsters:", r.assign(b=pd.cut(r["n_pollsters"], edges, labels=labels)).groupby(
        "b", observed=False)["err"].agg(rmse).round(4).to_dict())
    tr, te = r[r["cycle"] <= 2020], r[r["cycle"] == 2022]
    fit = {n: us.poll_sd_by_n(n, us.poll_sd_bins(tr)) for n in te["n_polls"]}
    z = te["err"] / te["n_polls"].map(fit)
    print(f"fit 2016-2020 {us.poll_sd_bins(tr)}; 2022 ({len(te)} races): |z|<1 {np.mean(np.abs(z) < 1):.2f} "
          f"(nominal 0.68), |z|<1.645 {np.mean(np.abs(z) < 1.645):.2f} (0.90), rms z {np.sqrt(np.mean(z**2)):.2f}")
    print(f"all years: {us.poll_sd_bins(r)}   [brunder.us.POLL_SD_BINS = {us.POLL_SD_BINS}]")


def next_versions() -> None:
    """The next model version of every race with an active poll prior (v3; Ohio v4): its poll model's config with
    (a) [model] count_scaled_terms = true — before the count the statewide uncertainty is the poll error alone, the
    reporting-order (tau) and between-region terms grow with the counted votes' weight (brunder.nowcast) — and
    (b) swing_prior_sd = the historical error of a poll average with that many polls (brunder.us.POLL_SD_BINS),
    instead of the flat 0.039 + sampling. Registered files are never written. A race added after the first freeze
    (v1 not registered, e.g. North Carolina Senate) builds its next version from its own unregistered v2."""
    frozen = registered_ids()
    for p in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")):
        c1 = us.load_config(p)
        new = c1["id"] not in frozen
        vs = {v: q for v, q in us._sibling_versions(c1).items() if new or q.stem in frozen}   # registered only
        cfgs = {v: us.load_config(q) for v, q in vs.items()}
        pm = us.poll_model(cfgs)
        if pm is None:
            print(p.name, "no registered poll model: no next version")
            continue
        if cfgs[pm]["model"].get("count_scaled_terms"):
            print(vs[pm].name, "already has the count-scaled poll error: no next version")
            continue
        reg = [int(v[1:]) for v in vs]
        n = max(reg + [1]) + 1
        q = p.with_name(p.name.replace("_v1.toml", f"_v{n}.toml"))
        if q.stem in frozen:
            print(q.name, "registered: not regenerated")
            continue
        base = vs[pm]
        text, cb = base.read_text(), cfgs[pm]
        sd = us.poll_sd_by_n(int(cb["prior"]["poll_n"]))
        body = text[text.index("\nid = ") + 1:].replace(f'id = "{cb["id"]}"', f'id = "{q.stem}"', 1)
        body = re.sub(r"^swing_prior_sd = .*$",
                      f"swing_prior_sd = {sd}      # historical RMSE of a poll average with {cb['prior']['poll_n']} "
                      "polls (brunder.us.POLL_SD_BINS)\ncount_scaled_terms = true   # tau / between-region terms "
                      "scale in with the counted votes", body, count=1, flags=re.M)
        sha = hashlib.sha256(base.read_bytes()).hexdigest()
        head = [f"# US midterms 2026-11-03, {c1['election']['name']}: model v{n}. Identical to {base.name} (sha256 {sha})",
                "# except the pre-count uncertainty: swing_prior_sd = the historical error of a poll average with this",
                "# many polls (538 raw_polls 2016-2022, CC BY 4.0; analysis/us_polls.py errorbins), and",
                "# count_scaled_terms = true, so before any vote the statewide uncertainty is that poll error alone and",
                "# the reporting-order / between-region terms grow with the counted votes (brunder.nowcast). Earlier",
                "# versions stay registered and scored."]
        q.write_text("\n".join(head) + "\n" + body)
        print(q.name, f"from {base.name}: swing_prior_sd {cb['model']['swing_prior_sd']} -> {sd}")


V4_EXPECTED = "data_public/us2026_expected_votes_v4.csv"     # analysis/us_model_analysis.py write_expected_v4


def next_v4() -> None:
    """Secondary model v4 (Ohio v5): the latest registered poll model with count_scaled_terms (v3; Ohio v4) plus
    exactly one change, the expected vote per county = 2024 president two-party votes × the county's own 2022/2020
    midterm drop-off ([count] expected_votes, level from the file). Registered files are never written; idempotent."""
    frozen = registered_ids()
    for p in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")):
        c1 = us.load_config(p)
        vs = {v: q for v, q in us._sibling_versions(c1).items() if q.stem in frozen}
        cfgs = {v: us.load_config(q) for v, q in vs.items()}
        base_v = [v for v, c in cfgs.items() if c["model"].get("count_scaled_terms")]
        if not base_v:
            print(p.name, "no registered v3-type poll model: no v4")
            continue
        cb = cfgs[base_v[-1]]
        if cb["count"].get("expected_votes"):
            print(vs[base_v[-1]].name, "already has an expected-vote file: nothing to do")
            continue
        n = max(int(v[1:]) for v in vs) + 1
        q = p.with_name(p.name.replace("_v1.toml", f"_v{n}.toml"))
        if q.stem in frozen:
            print(q.name, "registered: not regenerated")
            continue
        base = vs[base_v[-1]]
        text = base.read_text()
        body = text[text.index("\nid = ") + 1:].replace(f'id = "{cb["id"]}"', f'id = "{q.stem}"\nrole = "secondary"', 1)
        body = _after_block(body, "count", [
            f'expected_votes = "{V4_EXPECTED}"   # 2024 president x own 2022/2020 drop-off (secondary model)',
            'expected_votes_level = "file"'])
        # the v2 definition of the swing prior (poll average minus the prior's statewide share weighted by the model's
        # expected votes) applied with v4's expected votes, so that before the count v4 also centres on the poll average
        pri = us.race_prior(cb, us.load_priors())
        if pri is not None and not pri.empty and cb["prior"].get("poll_avg") is not None:
            ev = pd.read_csv(us.ROOT_DIR / V4_EXPECTED, comment="#").set_index("unit_id")["exp_votes"]
            w = pri["unit_id"].map(ev).fillna(pri["V"] * us.rules(cb).turnout_ratio).to_numpy()
            base_w = float(np.average(pri["s0"], weights=w))
            body = re.sub(r"^swing_prior_mean = .*$",
                          f"swing_prior_mean = {cb['prior']['poll_avg'] - base_w:.4f}   # poll_avg - prior statewide "
                          f"share {base_w:.4f} weighted by v4's expected votes", body, count=1, flags=re.M)
        sha = hashlib.sha256(base.read_bytes()).hexdigest()
        head = [f"# US midterms 2026-11-03, {c1['election']['name']}: model v{n} (SECONDARY; the primary stays "
                f"{base.stem}).",
                f"# Identical to {base.name} (sha256 {sha}) except the expected vote per county:",
                "# 2024 president two-party votes x the county's own 2022/2020 midterm drop-off (relative to its state,",
                "# capped x0.61-x1.65; state flat ratio where missing), with swing_prior_mean re-centred on those",
                "# weights (the v2 definition, so v4 still starts at the poll average). docs/us_model_analysis.md."]
        q.write_text("\n".join(head) + "\n" + body)
        print(q.name, f"from {base.name}")


def build(day: str | None) -> None:
    out = us.build_polls(us.fetch_polls(day))
    n = sum(v is not None for v in out["races"].values())
    print(f"{us.POLLS_PUBLIC}: {n} of {len(out['races'])} races with polls in the window")


def _after_block(body: str, section: str, lines: list[str]) -> str:
    """Append lines at the end of a TOML section's key block (before its trailing blank line)."""
    m = re.search(rf"\[{section}\][^\n]*\n(?:[^\n\[][^\n]*\n)*?(?=\n|\[)", body)
    return body[:m.end()] + "".join(x + "\n" for x in lines) + body[m.end():]


def registered_ids() -> set[str]:
    """Config ids in prereg/registry.jsonl: frozen, never regenerated."""
    f = us.ROOT_DIR / "prereg" / "registry.jsonl"
    if not f.exists():
        return set()
    return {__import__("json").loads(line)["id"] for line in f.read_text().splitlines() if line.strip()}


def configs() -> None:
    polls = us.load_polls()
    built = __import__("json").loads(us.POLLS_PUBLIC.read_text())["built"]
    priors = us.load_priors()
    frozen = registered_ids()
    for p in sorted((us.ROOT_DIR / "configs").glob(f"{us.EID}_*_v1.toml")):
        if p.name.replace("_v1.toml", "_v2") in frozen:
            print(p.name.replace("_v1.toml", "_v2.toml"), "registered: not regenerated")
            continue
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


CAL_CHECK = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75)


def calib(n_sim: int = 40) -> None:
    """Count-time calibration of v1 / v2 / v3 (and the candidate v3 'any': reporting-order and between-region terms
    fully present from the first returns) on Georgia 2022 governor and Senate: certified county results by vote type,
    simulated count orders with early + mail votes posted first (analysis/us_calibrate.py), production GA settings.
    Poll priors: our rule on 538's 2022 polls (v2 sd = 0.039 + sampling; v3 sd = POLL_SD_BINS by number of polls).
    GA 2024 president has no polls in the 538 file, so it is not included. Reports 90 % coverage, mean |z| and the
    mean absolute margin error (points) by share counted."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import us_calibrate as cal
    priors = us.load_priors()
    rng = np.random.default_rng(20261101)
    ga = us.load_config(us.ROOT_DIR / "configs" / "us2026_ga_sen_v1.toml")
    out = []
    for name, eid, race, prior_src, ratio, r538 in [
            ("GA 2022 gov", "2022NovGen", "gov", "ga_pres_2020", 0.80, "2022_Gov-G_GA"),
            ("GA 2022 sen", "2022NovGen", "sen", "ga_pres_2020", 0.80, "2022_Sen-G_GA")]:
        bt = cal.by_type(eid, race)
        cfg = {**ga, "election": {**ga["election"], "race": "x"}, "prior": {"share": prior_src, "votes": prior_src},
               "count": {**ga["count"], "turnout_ratio": ratio}}
        pri = us.race_prior(cfg, priors)
        pa = _poll_prior_538(r538)
        v2 = _v2(cfg, pri, pa["avg"], pa["sd"])
        v3 = {**v2, "model": {**v2["model"], "swing_prior_sd": us.poll_sd_by_n(pa["n_polls"]), "count_scaled_terms": True}}
        v3a = {**v3, "model": {**v3["model"], "count_scale_mode": "any"}}
        models = {"v1": cfg, "v2": v2, "v3": v3, "v3-any": v3a}
        total = bt[["early_d", "early_r", "late_d", "late_r"]].sum().sum()
        final = (bt["early_d"] + bt["late_d"]).sum() / total
        for s in range(n_sim):
            ev = cal.events(bt, rng)
            cum = ((ev["d"] + ev["r"]).cumsum() / total).to_numpy()
            for c in CAL_CHECK:
                rows = cal.rows_at(ev, int(np.searchsorted(cum, c)) + 1)
                for m, cm in models.items():
                    r = us.project(rows, cm, pri)[0]
                    out.append({"race": name, "model": m, "check": c, "z": (r["share_hat"] - final) / r["share_sd"],
                                "margin_err": 200 * (r["share_hat"] - final), "margin_sd": 200 * r["share_sd"]})
    df = pd.DataFrame(out)
    tab = df.groupby(["model", "check"]).agg(cover90=("z", lambda z: np.mean(np.abs(z) < 1.645)),
                                             mean_abs_z=("z", lambda z: np.abs(z).mean()),
                                             margin_mae=("margin_err", lambda e: np.abs(e).mean()),
                                             margin_sd=("margin_sd", "mean")).round(3).reset_index()
    pd.set_option("display.width", 200)
    print(tab.pivot(index="check", columns="model", values=["cover90", "mean_abs_z", "margin_mae", "margin_sd"])
          .to_string())


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
    ap.add_argument("step", choices=["error", "errorbins", "build", "configs", "next", "next_v4", "effect", "calib"])
    ap.add_argument("--day", help="VoteHub cache day YYYYmmdd (default today)")
    a = ap.parse_args()
    {"error": error, "errorbins": errorbins, "build": lambda: build(a.day), "configs": configs,
     "next": next_versions, "next_v4": next_v4, "effect": effect, "calib": calib}[a.step]()
