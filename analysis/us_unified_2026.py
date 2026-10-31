"""Build the 2026 unified priors and configs (statewide Senate / governor races; House districts in `house`).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_2026.py statewide   # priors csv + configs
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_unified_2026.py house

Inputs available before the polls close: certified past results, Census/ACS, the frozen poll averages of
data_public/us2026_polls.json (rule: docs/us_midterms.md#polls), the national generic-ballot average of
$BRUNDER_DATA/processed/us2026/unified/nat_panel.csv, and parameters fitted on 2016-2024 only
(data_public/us_unified_params.json; `analysis/us_unified_backtest.py final`). Nothing here reads a 2026 count.

Outputs
  data_public/us2026_unified_priors.csv   source, unit_id, dem, rep, total   (dem+rep = expected two-party votes V,
                                           dem/(dem+rep) = county prior share s0; source "uni_<st>_<office>_2026")
  configs/us2026_unified/us2026_unified_*_v1.toml   one per registered v1 statewide config (same [election], [count]
                                           (turnout_ratio = 1: V is already the expected vote), [markets]; own [prior], [model])
  results/us_unified/2026_races.csv       the race-level forecast (mean, sd, P(win), components, v1/v3 comparison)
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import us, us_unified as uu

ROOT = uu.ROOT
CFG_OUT = ROOT / "configs" / "us2026_unified"
PRIORS_OUT = ROOT / "data_public" / "us2026_unified_priors.csv"
POLLS = ROOT / "data_public" / "us2026_polls.json"
RES = ROOT / "results" / "us_unified"
FREEZE = "2026-11-02"                   # polls and national environment as of this day


def v1_configs() -> list[tuple[Path, dict]]:
    paths = sorted(ROOT.glob("configs/us2026_*_v1.toml")) + sorted((ROOT / "configs" / "us2026_civic").glob("*_v1.toml"))
    return [(p, tomllib.loads(p.read_text())) for p in paths]


def poll_key(race_id: str) -> str:
    return race_id.replace("-civic", "")


def specs(cfgs) -> list[dict]:
    seen, out = set(), []
    for _, c in cfgs:
        e = c["election"]
        k = (e["state"], e["race"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"st": e["state"], "office": e["race"], "race_id": poll_key(e["race_id"])})
    return out


def build_statewide() -> pd.DataFrame:
    inp = uu.load_inputs()
    params = uu.load_params()
    cfgs = v1_configs()
    polls = json.loads(POLLS.read_text())["races"]
    sp = specs(cfgs)
    R = uu.race_table(inp, sp, polls)
    n = uu.nat_table(inp["nat"])
    nm = params["nat"]
    swing = float(uu.predict_nat(nm, n[n["cycle"] == 2026].iloc[0])[0])
    R26 = R[R["t"] == 2026].reset_index(drop=True)
    pred = uu.predict_state(params["state"], R26, swing)
    Rall = pd.concat([R[R["t"] < 2026], R26], ignore_index=True)
    C = uu.county_table(inp, R26)
    C = uu.add_dev(C, uu.SHARE_FEATS)
    C = uu.turnout_features(C)
    share_m, turn_m, vm = params["share"], params["turnout"], params["vstate"]
    rows, parts = [], []
    for i, r in R26.iterrows():
        d = C[(C["st"] == r["st"]) & (C["office"] == r["office"])]
        S_hat, sd = float(pred.at[i, "mu"]), float(pred.at[i, "sd"])
        vp = float(d["vp_b"].sum())
        has_o = d["vo_b"].notna().mean() > 0.9
        vo = float(d["vo_b"].sum()) if has_o else None
        a = np.log(vp) + vm["p_1"]
        mo = vm.get("o_11")
        Vs = float(np.exp(0.5 * a + 0.5 * (np.log(vo) + mo))) if (vo and mo is not None and d["oy"].max() % 4 == 2) else \
            float(np.exp(0.5 * a + 0.5 * (np.log(vo) + vm["o_10"]))) if (vo and vm.get("o_10") is not None) else float(np.exp(a))
        pr = uu.county_prior(d, share_m, turn_m, S_hat, Vs)
        src = f"uni_{r['st']}_{r['office']}_2026"
        s0, V = pr["s0"].to_numpy(), pr["V"].to_numpy()
        parts.append(pd.DataFrame({"source": src, "unit_id": [uu.unit_id(r["st"], f) for f in pr["fips"]],
                                   "dem": s0 * V, "rep": (1 - s0) * V, "total": V}))
        v1 = r["O"] if np.isfinite(r["O"]) else r["L"]
        base_share = float(np.average(d["so_f"], weights=d["vo_b"].fillna(d["vp_b"])))
        rows.append({"race_id": r["race_id"], "st": r["st"], "office": r["office"], "n_polls": int(r["n_polls"]),
                     "poll_avg": r["P"], "F": float(pred.at[i, "F"]), "mu": S_hat, "sd": sd, "p_win": float(pred.at[i, "p_win"]),
                     "v1_share": base_share, "v1_p_win_sd": float(norm.cdf((base_share - 0.5) / 0.08)),
                     "v3_p_win": float(norm.cdf((r["P"] - 0.5) / uu_v3_sd(int(r["n_polls"])))) if r["n_polls"] > 0 else np.nan,
                     "V_state": Vs, "inc": int(r["inc"]), "Lc": r["Lc"], "Oc_f": r["Oc_f"], "swing_hat": swing,
                     "counties": len(d), "county_dev_sd": float(np.std(pr["s0"] - S_hat))})
    out = pd.DataFrame(rows)
    pri = pd.concat(parts, ignore_index=True)
    # every county of brunder.us.counties(st) must have a row (race_prior drops the rest): fill gaps with the state mean
    fill = []
    for (st, off), g in pri.assign(st=pri["unit_id"].str.split("-").str[1],
                                   off=pri["source"].str.split("_").str[2]).groupby(["st", "off"]):
        want = set(us.counties(st)["unit_id"])
        have = set(g["unit_id"])
        miss = sorted(want - have)
        if miss:
            s0 = float((g["dem"].sum()) / (g["dem"].sum() + g["rep"].sum()))
            v = float(g["total"].median()) * 0.25
            print(f"{st}-{off}: {len(miss)} counties without history filled with state share {s0:.3f}, V {v:.0f}: {miss[:5]}")
            fill.append(pd.DataFrame({"source": g["source"].iloc[0], "unit_id": miss, "dem": s0 * v, "rep": (1 - s0) * v, "total": v}))
        extra = have - want
        if extra:
            print(f"{st}-{off}: {len(extra)} units not in brunder.us.counties (dropped): {sorted(extra)[:5]}")
            pri = pri[~((pri["source"] == g["source"].iloc[0]) & pri["unit_id"].isin(extra))]
    pri = pd.concat([pri, *fill], ignore_index=True)
    pri[["dem", "rep", "total"]] = pri[["dem", "rep", "total"]].round(2)
    header = ("# Unified pre-count county priors for the 2026 statewide races (analysis/us_unified_2026.py; model docs/us_unified.md).\n"
              "# source uni_<st>_<office>_2026: dem + rep = expected two-party votes of the county, dem / (dem + rep) = prior two-party\n"
              "# Democratic share s0. Built from certified 2016-2024 county results (MIT Election Data and Science Lab, CC0; state\n"
              "# Secretary of State records), Census ACS and PEP (public domain), poll averages (VoteHub, CC BY 4.0; FiveThirtyEight\n"
              f"# poll history CC BY 4.0) frozen as of {FREEZE}; parameters fitted on 2016-2024 only. No 2026 count data.\n")
    PRIORS_OUT.write_text(header + pri.to_csv(index=False))
    RES.mkdir(parents=True, exist_ok=True)
    out.to_csv(RES / "2026_races.csv", index=False)
    print(out[["race_id", "n_polls", "poll_avg", "F", "mu", "sd", "p_win", "v1_share", "v3_p_win", "V_state"]].round(4).to_string(index=False))
    print(f"swing_hat {swing:.4f}; wrote {PRIORS_OUT} ({len(pri)} rows)")
    return out


def uu_v3_sd(n: int) -> float:
    return us.poll_sd_by_n(n)


# ------------------------------------------------------------------ configs


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split a toml text into (header line, body lines); the first item is the preamble (header '')."""
    out, cur, hdr = [], [], ""
    for ln in text.splitlines():
        if ln.startswith("["):
            out.append((hdr, cur))
            hdr, cur = ln, []
        else:
            cur.append(ln)
    out.append((hdr, cur))
    return out


def unified_config(path: Path, cfg: dict, race: pd.Series, uid: str | None = None, src: str | None = None,
                   priors_name: str | None = None, fitted: str = "2016-2024") -> str:
    text = path.read_text()
    e = cfg["election"]
    st, off = e["state"], e["race"]
    civic = "civic" in path.parts[-2] or "civic" in path.name
    uid = uid or (("us2026_unified_civic_" if civic else "us2026_unified_") + f"{st}_{off}_v1")
    m = cfg.get("model", {})
    src = src or f"uni_{st}_{off}_2026"
    polled = race["n_polls"] > 0
    head = [
        f"# US midterms 2026-11-03, {e['name']}: UNIFIED pre-count model (labelled challenger to v1/v3; not a replacement).",
        f"# One model for races with and without polls: fundamentals (last presidential and same-office lean, seat holder, national",
        f"# generic-ballot swing) blended with the poll average by number of polls, county shares and expected votes from past",
        f"# results, population growth and demographics (docs/us_unified.md). Parameters fitted leave-one-cycle-out on {fitted}.",
        f"# Same [election], [count] and [markets] as {path.name} except turnout_ratio = 1.0 (the prior's V is already the expected",
        f"# vote). The prior is {priors_name or PRIORS_OUT.name} (NOT read by brunder.us.load_priors: run it with",
        f"# analysis/us_unified_replay.py). Statewide forecast {race['mu']:.4f} ± {race['sd']:.4f} (P(D wins) {race['p_win']:.3f}),",
        f"# {int(race['n_polls'])} polls in the window, poll average {'%.4f' % race['poll_avg'] if polled else 'none'}; frozen {FREEZE}.",
        "# Frozen before polls close; nothing here was chosen on 2026 count data.",
    ]
    secs = _sections(text)
    body: list[str] = []
    for hdr, lines in secs:
        if hdr == "":
            body += [ln for ln in lines if not ln.startswith("#")]
            body = [re.sub(r'^id = ".*"', f'id = "{uid}"', ln) for ln in body]
        elif hdr.startswith("[prior]"):
            body += ["[prior]                    # data_public/us2026_unified_priors.csv", f'share = "{src}"',
                     f'votes = "{src}"', f"statewide_mean = {race['mu']:.4f}", f"statewide_sd = {race['sd']:.4f}",
                     f"p_win = {race['p_win']:.4f}", f"poll_n = {int(race['n_polls'])}", ""]
        elif hdr.startswith("[model]"):
            keep = [f"{k} = {m[k]}" for k in ("kappa", "tau", "rho", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf") if k in m]
            body += ["[model]                    # brunder.nowcast.Params: count-order settings as the registered v1 of this feed", *keep,
                     "swing_prior_mean = 0.0       # the unified prior already contains the poll / national information",
                     f"swing_prior_sd = {race['sd']:.4f}      # predictive sd of the statewide share (leave-one-cycle-out residuals by number of polls)",
                     "count_scaled_terms = true", ""]
        elif hdr.startswith("[count]"):
            body += [hdr]
            for ln in lines:
                if ln.startswith("turnout_ratio"):
                    ln = "turnout_ratio = 1.0         # unified V = expected two-party votes of the county"
                if ln.startswith("expected_votes"):
                    continue
                body.append(ln)
        else:
            body += [hdr, *lines]
    return "\n".join(head + body).rstrip() + "\n"


def write_configs(races: pd.DataFrame) -> None:
    CFG_OUT.mkdir(parents=True, exist_ok=True)
    by = races.set_index(["st", "office"])
    n = 0
    for p, c in v1_configs():
        e = c["election"]
        r = by.loc[(e["state"], e["race"])]
        (CFG_OUT / (("us2026_unified_civic_" if "civic" in str(p.parent) else "us2026_unified_") + f"{e['state']}_{e['race']}_v1.toml")
         ).write_text(unified_config(p, c, r))
        n += 1
    print(f"wrote {n} configs to {CFG_OUT}")


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "statewide"
    if what == "statewide":
        write_configs(build_statewide())
    elif what == "house":
        from us_unified_house_2026 import build_house
        build_house()


if __name__ == "__main__":
    main()
