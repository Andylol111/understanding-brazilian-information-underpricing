"""Where does the base model's miss come from: units not reporting yet, or the not-yet-counted rest of units already reporting?

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_decomp.py

At the frames of 5 / 10 / 25 / 50% counted of every real-order night the base model (rho 0.8, kappa 50,000, tau 0, as the
published replays) gives each unit i a remaining-vote share s_hat_i and remaining votes R_i (`brunder.mexico.project_units`,
the same equations as `nowcast.nowcast`). The night's own last state gives the truth of what was still to come: the share of
the votes that arrived after the frame, s_rem_i = (a_end - a_i) / (n_end - n_i). The national share error then splits exactly
(up to the turnout scale k) into
  between : units with nothing counted yet (f = 0)         sum R_i (s_rem_i - s_hat_i) / total votes
  within  : units already partly counted (0 < f < 1)       sum R_i (s_rem_i - s_hat_i) / total votes
in points of the final share (positive = the model projected A too low). `within_gap` = the vote-weighted mean of
(s_rem_i - counted share_i) over the partly counted units: how different the late part of a unit is from its early part,
i.e. a within-unit reporting-order effect that no between-unit covariate can see. Output: results/model_review/decomp.csv + markdown.
For Mexico the truth is the end-of-PREP state (the PREP stops at 93%; the cómputo gap is a separate matter) and for Poland
the end state is rebuilt from marginals (cells, not polling districts), so 'within' is lower-bound evidence there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
import mr_nights_pl  # noqa: E402
from mr_common import OUT, expected_counted, frames_at  # noqa: E402

from brunder import mexico, nowcast as nc  # noqa: E402

NIGHTS = {**mr_nights_br.NIGHTS, **mr_nights_mx.NIGHTS, **mr_nights_pl.NIGHTS}
AT = (0.05, 0.10, 0.25, 0.50)


def decompose(st: pd.DataFrame, end: pd.DataFrame, prior: pd.DataFrame, unpooled) -> dict:
    p = nc.Params(tau=0.0, unpooled=unpooled)
    u = mexico.project_units(st, prior, p)
    e = end[["uf", "cd_mun", "n", "a"]].rename(columns={"n": "n_end", "a": "a_end"})
    m = u.merge(e, on=["uf", "cd_mun"], how="left").fillna({"n_end": 0.0, "a_end": 0.0})
    m = m.merge(st[["uf", "cd_mun", "f"]], on=["uf", "cd_mun"], how="left").fillna({"f": 0.0})
    rem_n = (m["n_end"] - m["n"]).clip(lower=0)
    s_rem = ((m["a_end"] - m["a"]) / rem_n.where(rem_n > 0)).clip(0, 1).fillna(m["s_hat"])
    total = float(m["n"].sum() + m["R"].sum())
    diff = m["R"] * (s_rem - m["s_hat"])
    part = (m["f"] > 0) & (m["f"] < 1) & (m["n"] > 0)
    zero = m["n"] <= 0
    cs = (m["a"] / m["n"].where(m["n"] > 0))
    w = m["R"].where(part, 0.0)
    return {"between_pt": 100 * float(diff[zero].sum() / total), "within_pt": 100 * float(diff[part].sum() / total),
            "other_pt": 100 * float(diff[~zero & ~part].sum() / total),
            "within_gap_pt": 100 * float(((s_rem - cs).where(part, 0.0) * w).sum() / w.sum()) if w.sum() > 0 else np.nan,
            "remaining_share_in_part_units": float(w.sum() / m["R"].sum()) if m["R"].sum() > 0 else np.nan}


def main() -> None:
    rows = []
    for name in NIGHTS:
        night = NIGHTS[name]()
        ticks = list(night.ticks(1)) if night.native else None
        end = (ticks[-1][1] if ticks else None)
        if end is None:
            for _, end in night.ticks(5):
                pass
        frames = ticks and [(None, t, st) for t, st in ticks] or frames_at(night, AT)
        if night.native:
            pick = []
            for a in AT:
                c = [(abs(expected_counted(st, night.prior) - a), t, st) for _, t, st in frames]
                pick.append((a,) + min(c, key=lambda x: x[0])[1:])
            frames = pick
        for fr, t, st in frames:
            d = decompose(st, end, night.prior, night.unpooled)
            nat = float(end["a"].sum() / end["n"].sum())
            rows.append({"night": name, "counted": fr, **d, "total_err_pt": 100 * (nat - 0) * 0})
        print(name, flush=True)
    D = pd.DataFrame(rows).drop(columns="total_err_pt")
    D["miss_pt"] = D["between_pt"] + D["within_pt"] + D["other_pt"]
    D.to_csv(OUT / "decomp.csv", index=False)
    pd.set_option("display.width", 200)
    print(D.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
