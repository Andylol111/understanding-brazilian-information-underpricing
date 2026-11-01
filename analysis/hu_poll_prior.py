"""Poll-start sensitivity "v3" for Hungary 2026 (the same idea as analysis/poll_prior_replay.py, for the district + list model).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_poll_prior.py build    # configs/hu2026_v3.toml from v1 + the polls
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_score.py configs/hu2026_v3.toml   # replay -> results/hungary_v3/
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_poll_prior.py export   # publish key "v3" through analysis/sens_export.py
    uv run python analysis/decided_stats.py hu2026

v1's model (district nowcast over 106 districts in 20 county groups, list nowcast over 20 counties, postal / envelope / turnout / third
parties) with a starting estimate of the national swing from the final polls (rule in analysis/poll_priors.py): a normal prior whose
mean is the poll's Tisza / (Tisza + Fidesz-KDNP) minus the 2022 prior's vote-weighted share (separately for the list counties and the
districts) and whose sd is the root-mean-square poll error of the 2014, 2018 and 2022 list polls. One national error (tau, scaled by
the weight the counted votes have in the swing, plus the swing's posterior variance) drives the list and every district, so seats and
list move together. The first value is the first count publication (18:00 UTC, 3% counted incl. postal ballots): before it the seat
model has no third-party shares, so only the list chance from the poll alone is stated (stats.sensitivities.v3.precount).
Designed after the result was known: a sensitivity, not a forecast.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))

import poll_priors as pp  # noqa: E402

from brunder import hungary as hu  # noqa: E402
from brunder.paths import ROOT  # noqa: E402

CFG1 = ROOT / "configs" / "hu2026_v1.toml"
CFG3 = ROOT / "configs" / "hu2026_v3.toml"
RES = ROOT / "results" / "hungary_v3"
LABEL = "v3: poll start (chosen after the result was known)"


def bases(cfg: dict) -> tuple[float, float]:
    pr = hu.prior_2022_for_2026(cfg)
    c, d = pr["county"], pr["district"]
    return float((c["s0"] * c["V0"]).sum() / c["V0"].sum()), float((d["s0"] * d["V0"]).sum() / d["V0"].sum())


def build() -> None:
    cfg = hu.load_config(CFG1)
    country, date, f, a, b = pp.TARGETS["hu2026"]
    est = pp.prior_estimate(country, date, f, a, b)
    b_list, b_dist = bases(cfg)
    m_list, m_dist = est["two_way"] - b_list, est["two_way"] - b_dist
    src = CFG1.read_text()
    body = re.sub(r"\A(?:#[^\n]*\n)+", "", src)
    body = body.replace('id = "hu2026_v1"', 'id = "hu2026_v3"', 1)
    hdr = (f"# hu2026 v3 SENSITIVITY: POLL START (RETROSPECTIVE, NOT PRE-REGISTERED: designed after the result was known, chosen after the\n"
           f"# result was known). v1 stays the headline. Everything is v1's (2022-only settings, prior, postal, envelope, turnout, third\n"
           f"# parties) except a starting estimate of the national swing from the final polls (analysis/poll_priors.py, analysis/hu_poll_prior.py;\n"
           f"# rule written before this night was replayed with it):\n"
           f"#   swing_prior_mean = poll two-way share Tisza / (Tisza + Fidesz-KDNP) {est['two_way']:.4f} (plain mean of {est['n_polls']} polls whose fieldwork ended\n"
           f"#       {est['first_fieldwork_end']} .. {est['last_fieldwork_end']}, decided voters; Wikipedia poll tables, CC BY-SA, data_public/hu_polls_2026.csv)\n"
           f"#       minus the 2022 prior's vote-weighted two-way share: list counties {b_list:.4f}, districts {b_dist:.4f};\n"
           f"#   swing_prior_sd = root-mean-square error of the same kind of poll average (Fidesz-KDNP vs the runner-up list) in {est['n_errors']} earlier\n"
           f"#       elections (2014, 2018, 2022) = {est['sd']:.4f};\n"
           f"#   count_scaled_terms: tau and the between-county deviations enter in proportion to the weight the counted votes have in the swing.\n"
           f"#   No count, price or exit poll of this night is an input.\n")
    add_d = (f"swing_prior_mean = {m_dist:.5f}    # poll {est['two_way']:.5f} - district prior base {b_dist:.5f}\n"
             f"swing_prior_sd = {est['sd']:.5f}      # RMS poll error, {est['n_errors']} earlier elections\ncount_scaled_terms = true\n")
    add_l = (f"swing_prior_mean = {m_list:.5f}    # poll {est['two_way']:.5f} - county prior base {b_list:.5f}\n"
             f"swing_prior_sd = {est['sd']:.5f}\ncount_scaled_terms = true\n")
    body = re.sub(r"(^\[model\.district\][^\n]*\n(?:(?!\[)[^\n]*\n)*?)(\n\[model\.list\])", lambda m: m.group(1).rstrip("\n") + "\n" + add_d + m.group(2), body, count=1, flags=re.M)
    body = re.sub(r"(^\[model\.list\][^\n]*\n(?:(?!\[)[^\n]*\n)*?)(\n\[model\.other\])", lambda m: m.group(1).rstrip("\n") + "\n" + add_l + m.group(2), body, count=1, flags=re.M)
    poll = ("\n[poll]\n"
            f"file = \"{f}\"\nelection_date = \"{date}\"\nn_polls = {est['n_polls']}\ntwo_way = {est['two_way']:.5f}\n"
            f"prior_base_list = {b_list:.5f}\nprior_base_district = {b_dist:.5f}\nsd = {est['sd']:.5f}\nn_errors = {est['n_errors']}\n"
            + "pollsters = [" + ", ".join(json.dumps(x, ensure_ascii=False) for x in est["pollsters"]) + "]\n")
    sens = f"\n[sensitivity]\nlabel = \"{LABEL}\"\nconfig = \"configs/hu2026_v3.toml\"\nbase = \"configs/hu2026_v1.toml\"\n"
    CFG3.write_text(hdr + body.rstrip("\n") + "\n" + poll + sens)
    print(f"hu2026: poll {est['two_way']:.4f}, list base {b_list:.4f}, district base {b_dist:.4f}, sd {est['sd']:.4f}, "
          f"list poll-only P(Tisza) {norm.cdf((est['two_way'] - 0.5) / est['sd']):.3f}")


def export() -> None:
    import hu_export as ex
    import hu_score as hs
    from sens_export import write_sensitivity
    import tomllib
    cfg = hu.load_config(CFG3)
    raw = tomllib.loads(CFG3.read_text())
    est = raw["poll"]
    R = pd.read_csv(RES / "releases.csv", parse_dates=["t"]).sort_values("t")
    F = hs.finals(cfg)
    series, finals = {}, {}
    for rid, (_, _, _, cols, pcol, _, _) in ex.RACES.items():
        df = pd.DataFrame({"t": R["t"], "P": R[pcol]})
        if cols[2]:
            df["share"], df["sd"] = R[cols[2]], R[cols[3]]
        series[rid] = df
        finals[rid] = {"list": F["list_share"], "seats": F["smd_share"]}.get(rid)
    p_pre = float(norm.cdf((est["two_way"] - 0.5) / est["sd"]))
    note = (f"The same model as v1, started from the final polls: the national swing begins at the average of {est['n_polls']} polls from the last 14 days "
            f"(Wikipedia poll tables; Tisza {100 * est['two_way']:.1f}% of the Tisza + Fidesz-KDNP vote among decided voters) with a sd of {100 * est['sd']:.1f} points, "
            f"the root-mean-square error of the same kind of poll average in {est['n_errors']} earlier elections. One national error drives the list and every district; "
            "the reporting-order error enters as counted votes take over. The first value is the first count publication (3% counted). "
            "Nothing from this night's count, prices or exit polls is an input, but it was designed after the result was known: a sensitivity, not a forecast.")
    block = write_sensitivity("hu2026", "v3", LABEL, "configs/hu2026_v3.toml", note, series, finals, "seats",
                              extra_stats={"poll": {k: est[k] for k in ("file", "election_date", "n_polls", "two_way", "prior_base_list", "prior_base_district", "sd", "n_errors", "pollsters")},
                                           "precount": {"P_list_tisza_from_poll_alone": p_pre,
                                                        "note": "normal(poll two-way share, poll error sd) above 0.5; the seat model needs a count publication"}})
    pp.publish_meta("hu2026", "hu", "Tisza", "Fidesz-KDNP", pp.average(est["file"], est["election_date"], ["Tisza"], ["Fidesz-KDNP"]) | {"sd": est["sd"], "n_errors": est["n_errors"]},
                    F["list_share"], est["prior_base_list"])
    s = block["summary"]
    for rid in ("seats", "list"):
        h = block["by_race"][rid]
        print(f"  {rid}: first P {series[rid]['P'].iloc[0]:.3f} coverage {h.get('coverage90_1_95')} MAE {h.get('mae_pt_1_95')} settle90 {h.get('settle_90')} gated {h.get('settle_90_gated')}")
    print(json.dumps(s, indent=1)[:600])


if __name__ == "__main__":
    {"build": build, "export": export}[sys.argv[1]]()
