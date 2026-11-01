"""Poll-start sensitivity "v3" for Poland 2025 (rounds 1 and 2), Brazil (2022 and 2026 rounds 1 and 2) and Mexico 2024.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/poll_prior_replay.py build pl2025r1 [...]   # write configs/<id>_v3.toml
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/poll_prior_replay.py run   pl2025r1 [...]   # replay it, publish key "v3"
    uv run python analysis/decided_stats.py <ids>                                                        # afterwards

The model is v1's (same prior, tau, rho, kappa, same count states), plus a starting estimate from the final polls (rule in
analysis/poll_priors.py): a normal prior on the national swing, mean = poll two-way share minus the prior's vote-weighted two-way
share, sd = the root-mean-square poll error of the country's earlier elections; `count_scaled_terms` makes the reporting-order
terms (tau) enter in proportion to the weight the counted votes have in the swing, so before any count the uncertainty is the poll
error alone and the chance exists from poll close. It was designed after the result was known: a sensitivity, not a forecast, and
v1 stays the headline. Brazilian state races take the national swing prior with the full tau and state-deviation terms (a state's
own deviation is not a poll error that the count removes).

Output: site/data/<id>/{timeline,meta,stats}.json gain P_sens_v3 (+ share_sens_v3 / sd_sens_v3), meta.sensitivities and
stats.sensitivities.v3 through analysis/sens_export.py. The v1 reproduction check (same code, no prior) prints against the published v1.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mix_replay as mix  # noqa: E402
import poll_priors as pp  # noqa: E402
from sens_export import DATA, write_sensitivity  # noqa: E402

from brunder import nowcast as nc  # noqa: E402
from brunder import poland as pl  # noqa: E402
from brunder.paths import ROOT  # noqa: E402

LABEL = "v3: poll start (chosen after the result was known)"
# election id -> (config the v3 file is built from, v1 (rho, kappa, tau) = mix.ELECTIONS)
BASE = {"pl2025r1": "pl2025r1_v1", "pl2025r2": "pl2025r2_v1", "br2022r1": "br2022r1_v2", "br2022r2": "br2022r2_v2",
        "br2026r1": "br2026r1_v2", "br2026r2": "br2026r2_v2", "mx2024": "mx2024_v1"}
PRIOR_TXT = {"pl2025r1": "2020 round 1 by cell", "pl2025r2": "2020 runoff by cell", "mx2024": "2018 cómputos by section",
             "br2022r1": "2018 round 1 by municipality", "br2022r2": "2022 round 1 by municipality",
             "br2026r1": "2022 round 1 by municipality", "br2026r2": "2026 round 1 by municipality"}
NAMES = {"pl2025r1": ("Trzaskowski", "Nawrocki"), "pl2025r2": ("Trzaskowski", "Nawrocki"), "mx2024": ("Sheinbaum", "Gálvez"),
         "br2022r1": ("Lula", "Bolsonaro"), "br2022r2": ("Lula", "Bolsonaro"), "br2026r1": ("Lula", "Flávio Bolsonaro"),
         "br2026r2": ("Lula", "Flávio Bolsonaro")}


def base_share(night) -> float:
    p = night.prior[~night.prior["uf"].isin(night.unpooled)]
    return float((p["s0"] * p["V0"]).sum() / p["V0"].sum())


def header(eid: str, est: dict, base: float) -> str:
    a, b = NAMES[eid]
    return (f"# {eid} v3 SENSITIVITY: POLL START (RETROSPECTIVE, NOT PRE-REGISTERED: designed after the result was known, chosen after the\n"
            f"# result was known). v1 stays the headline. Everything is v1's (prior, tau, rho, kappa, count states) except a starting\n"
            f"# estimate of the national swing from the final polls (analysis/poll_priors.py; rule written before this night was replayed with it):\n"
            f"#   swing_prior_mean = poll two-way share of {a} / ({a} + {b}) {est['two_way']:.4f} (plain mean of {est['n_polls']} polls whose fieldwork ended\n"
            f"#       {est['first_fieldwork_end']} .. {est['last_fieldwork_end']}, the 14 days before the vote; Wikipedia poll tables, CC BY-SA, data_public/)\n"
            f"#       minus the prior's vote-weighted two-way share {base:.4f} ({PRIOR_TXT[eid]});\n"
            f"#   swing_prior_sd = root-mean-square error of the same kind of poll average over {est['n_errors']} earlier elections = {est['sd']:.4f};\n"
            f"#   count_scaled_terms: tau enters in proportion to the weight the counted votes have in the swing, so before any count the\n"
            f"#       uncertainty is the poll error alone; no count, price or exit poll of this night is an input.\n")


def build(eid: str) -> None:
    country, date, pfile, a, b = pp.TARGETS[eid]
    est = pp.prior_estimate(country, date, pfile, a, b)
    night = mix.NIGHTS[mix.ELECTIONS[eid][0]]()
    base = base_share(night)
    mean = est["two_way"] - base
    src = (ROOT / "configs" / f"{BASE[eid]}.toml").read_text()
    body = re.sub(r"\A(?:#[^\n]*\n)+", "", src)                       # drop the source file's header comment
    body = re.sub(r'^id = "[^"]*"', f'id = "{eid}_v3"', body, count=1, flags=re.M)
    rho1, kappa1, tau1 = mix.ELECTIONS[eid][2]
    new = (f"swing_prior_mean = {mean:.5f}    # poll {est['two_way']:.5f} - prior base {base:.5f}\n"
           f"swing_prior_sd = {est['sd']:.5f}      # RMS poll error, {est['n_errors']} earlier elections\n"
           f"count_scaled_terms = true\n")
    m = re.search(r"^\[model\][^\n]*\n(?:(?!\[)[^\n]*\n)*", body, flags=re.M)
    block = m.group(0).rstrip("\n") + "\n"
    if eid.startswith("br"):                                          # v2 file carries v2's settings: replace by v1's
        block = f"[model]\nkind = \"municipal\"\ntau = {tau1}          # v1\nkappa = {kappa1}\nrho = {rho1}\n"
    poll = ("\n[poll]\n"
            f"file = \"{pfile}\"\nelection_date = \"{date}\"\nn_polls = {est['n_polls']}\ntwo_way = {est['two_way']:.5f}\n"
            f"prior_base = {base:.5f}\nsd = {est['sd']:.5f}\nn_errors = {est['n_errors']}\n"
            "pollsters = [" + ", ".join(json.dumps(x, ensure_ascii=False) for x in est["pollsters"]) + "]\n")
    body = body.replace(m.group(0), block + new + poll + "\n")
    (ROOT / "configs" / f"{eid}_v3.toml").write_text(header(eid, est, base) + body)
    print(f"{eid}: poll {est['two_way']:.4f}, prior base {base:.4f}, swing {mean:+.4f}, sd {est['sd']:.4f} ({est['n_errors']} elections), final {night.final:.4f}")


def params(cfg: dict, night, state_scope: bool) -> nc.Params:
    M = cfg["model"]
    keys = ("tau", "rho", "kappa", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf", "swing_prior_mean", "swing_prior_sd", "count_scaled_terms")
    kw = {k: M[k] for k in keys if k in M}
    if state_scope:
        kw["count_scaled_terms"] = False
    return nc.Params(**kw, unpooled=night.unpooled)


def replay(eid: str, cfg: dict, scopes: list[str | None], with_prior: bool = True) -> tuple[dict, dict]:
    """Nowcast of every scope at poll close (nothing counted) and at every distinct count state."""
    night = mix.NIGHTS[mix.ELECTIONS[eid][0]]()
    tl0 = pd.Timestamp(json.loads((DATA / eid / "meta.json").read_text())["poll_close_utc"])
    tl0 = tl0.tz_convert("UTC") if tl0.tzinfo else tl0.tz_localize("UTC")
    recs = {s: [] for s in scopes}
    P = {s: params(cfg, night, s is not None) for s in scopes}
    if not with_prior:
        P = {s: dataclasses.replace(p, swing_prior_mean=None, swing_prior_sd=None, count_scaled_terms=False) for s, p in P.items()}

    def evaluate(t, st):
        for s in scopes:
            r = nc.nowcast(st, night.prior, P[s], s)
            recs[s].append({"t": t, "counted": r["counted_frac"], "P": r["p_win"], "share": r["share_hat"], "sd": r["share_sd"]})

    evaluate(tl0, mix._empty())
    last_sig, last_state = None, None
    for t, st in night.ticks(1):
        sig = (len(st), round(float(st["n"].sum()), 3), round(float(st["f"].sum()), 6))
        if sig == last_sig:
            continue
        last_sig, last_state = sig, st
        evaluate(t, st)
    finals = {}
    for s in scopes:
        if s is None:
            finals[s] = night.final
        else:
            u = last_state[last_state["uf"] == s]
            finals[s] = float(u["a"].sum() / u["n"].sum())
    return {s: pd.DataFrame(v) for s, v in recs.items()}, finals


def check_v1(eid: str, d: pd.DataFrame, rows: list[dict]) -> None:
    """Same code without the prior against the published v1 (share, sd and P on the published rows, 1-95% counted)."""
    pub = pd.DataFrame(rows)
    pub["t"] = pd.to_datetime(pub["t"], utc=True)
    pub = pub.set_index("t")
    d = d.drop_duplicates("t", keep="last").set_index("t")
    d.index = pd.DatetimeIndex(d.index)
    a = d.reindex(d.index.union(pub.index)).ffill().reindex(pub.index)
    m = pub["share"].notna() & pub["counted"].between(0.01, 0.95)
    print(f"  v1 check {eid}: max |share - published| {np.nanmax(np.abs(a['share'] - pub['share'])[m]):.4f}, "
          f"max |sd - published| {np.nanmax(np.abs(a['sd'] - pub['sd'])[m]):.5f}, max |P - published| {np.nanmax(np.abs(a['P'] - pub['P'])[m]):.4f}", flush=True)


def run(eid: str) -> None:
    cfg = tomllib.loads((ROOT / "configs" / f"{eid}_v3.toml").read_text())
    tl = json.loads((DATA / eid / "timeline.json").read_text())
    races = list(tl)
    scopes = sorted({mix.scope_of(eid, r) for r in races}, key=lambda x: (x is not None, x or ""))
    D, finals_scope = replay(eid, cfg, scopes)
    series, finals = {}, {}
    cfg_races = {r["id"]: r for r in cfg.get("race", [])}
    for race in races:
        s = mix.scope_of(eid, race)
        d = D[s]
        if race in cfg_races and cfg_races[race]["kind"] == "band":
            c = cfg_races[race]
            P = pl.band_p(d["share"].to_numpy(), d["sd"].to_numpy(), c["side"], c["lo"], c["hi"])
            series[race], finals[race] = pd.DataFrame({"t": d["t"], "P": P}), None
        else:
            series[race] = d[["t", "P", "share", "sd"]]
            finals[race] = finals_scope[s]
    headline = mix.ELECTIONS[eid][1]
    est = cfg["poll"]
    a, b = NAMES[eid]
    note = (f"The same model as v1, started from the final polls: the national swing begins at the average of {est['n_polls']} polls from the last 14 days "
            f"(Wikipedia poll tables; {a} {100 * est['two_way']:.1f}% of the {a} + {b} vote against {100 * est['prior_base']:.1f}% in the prior) "
            f"with a sd of {100 * est['sd']:.1f} points, the root-mean-square error of the same kind of poll average in {est['n_errors']} earlier elections. "
            "The reporting-order error enters as counted votes take over, so the chance exists from poll close, before any count, and fades into the count. "
            "Nothing from this night's count, prices or exit polls is an input, but it was designed after the result was known: a sensitivity, not a forecast.")
    block = write_sensitivity(eid, "v3", LABEL, f"configs/{eid}_v3.toml", note, series, finals, headline,
                              extra_stats={"poll": {k: est[k] for k in ("file", "election_date", "n_polls", "two_way", "prior_base", "sd", "n_errors", "pollsters")},
                                           "precount": {"P_headline": float(series[headline]["P"].iloc[0]), "share_headline": float(series[headline]["share"].iloc[0])
                                                        if "share" in series[headline] else None}})
    pp.publish_meta(eid, pp.TARGETS[eid][0], a, b, pp.average(est["file"], est["election_date"], pp.TARGETS[eid][3], pp.TARGETS[eid][4]) | {"sd": est["sd"], "n_errors": est["n_errors"]},
                    finals[headline], est["prior_base"])
    s = block["summary"]
    h = block["by_race"][headline]
    print(f"  {eid} v3: P at poll close {series[headline]['P'].iloc[0]:.3f}; headline coverage {s['coverage90_headline']} MAE {s['mae_pt_headline']} "
          f"settle90 {h.get('settle_90')} (gated {h.get('settle_90_gated')}) from gate {h.get('settle_90_from_gate_min')} min", flush=True)
    # reproduction of v1 with the same code, no prior
    D1, _ = replay(eid, tomllib.loads((ROOT / "configs" / f"{eid}_v3.toml").read_text()), [mix.scope_of(eid, headline)], with_prior=False)
    check_v1(eid, D1[mix.scope_of(eid, headline)], tl[headline])


def main(argv: list[str]) -> None:
    cmd, ids = argv[0], argv[1:]
    for eid in ids:
        {"build": build, "run": run}[cmd](eid)


if __name__ == "__main__":
    main(sys.argv[1:])
