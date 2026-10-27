"""Reporting-order test on the 2026 first round: how wrong is the raw national count early, and how much
of that does a state-level model with ex-ante priors remove?

Inputs (all official TSE data):
  - the TSE live-feed state files as archived by the Wayback Machine at 17:26 BRT (20:27 UTC), when
    1.70% of sections were counted (data/raw/wayback/ele2026_6257_20261004T2027/);
  - the prior, known before the night: Lula's 2022 first-round share and valid votes per state
    (TSE certified results by municipality);
  - the final result from the BU-web section files.
Outputs the projected Lula share of valid votes by method and its error vs the final.

    uv run python analysis/reporting_order_test.py
"""

from __future__ import annotations

import json

import pandas as pd

from brunder import bweb, statecast, tse_live, tse_open
from brunder.paths import RAW

SNAP = RAW / "wayback" / "ele2026_6257_20261004T2027"
LULA = "13"


def main() -> None:
    rows = []
    for f in sorted(SNAP.glob("*-c0001-e006257-u.json")):
        uf = f.name[:2]
        v = tse_live.parse_u(json.loads(f.read_text()), uf)
        if not v:
            continue
        u = v[0]
        a = sum(r["votes"] for r in v if r["cand_n"] == LULA)
        rows.append({"uf": uf, "n": float(u["votes_valid"]), "a": float(a),
                     "f": u["sections_counted"] / u["sections_total"] if u["sections_total"] else 0.0})
    snap = pd.DataFrame(rows)
    nat = snap[snap["uf"] == "br"].iloc[0]
    states = snap[snap["uf"] != "br"].reset_index(drop=True)

    d22 = tse_open.votes_by_municipality(2022)
    d22 = d22[d22["round"] == 1]
    g = d22.groupby("uf").agg(V=("votes", "sum"))
    g["s0"] = d22[d22["cand_n"] == LULA].groupby("uf")["votes"].sum() / g["V"]
    prior = g.reset_index()[["uf", "s0", "V"]]

    sec = bweb.sections(2026, 1, LULA)
    final = sec["a"].sum() / sec["valid"].sum()

    raw = nat["a"] / nat["n"]
    w = states.merge(prior, on="uf")
    w = w[w["n"] > 0]
    size_weighted = float((w["a"] / w["n"] * w["V"]).sum() / w["V"].sum())
    out = [("national file (raw)", raw), ("state partials weighted by 2022 size", size_weighted)]
    for label, p in [("state nowcast, current defaults", statecast.Params()),
                     ("  kappa = 0", statecast.Params(kappa=0.0)),
                     ("  kappa = 50k", statecast.Params(kappa=50_000.0)),
                     ("  kappa = inf", statecast.Params(kappa=1e15))]:
        r = statecast.nowcast(states, prior, p)
        out.append((label, r["share"]))
    print(f"snapshot: {SNAP.name}, national sections counted {nat['f']:.2%}; final Lula share {final:.2%}")
    print(pd.DataFrame([{"method": m, "projected": f"{s:.2%}", "error_pt": round(100 * (s - final), 2)}
                        for m, s in out]).to_string(index=False))


if __name__ == "__main__":
    main()
