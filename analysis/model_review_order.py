"""Reporting-order evidence on the real-order nights: who reports first, and how wrong is the raw tally because of it.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/model_review_order.py

For every night in analysis/mr_nights_*.py and each frame at 5 / 10 / 25 / 50% counted it computes, with prior-only
covariates (`brunder.nowcast_order`):
  imb_size   counted-vote-weighted mean of the standardised log unit size minus its expected-vote mean (sd units; < 0 = small
             units over-represented among the counted votes);
  imb_lean   same for the prior share s0 (> 0 = units that leaned to A before are over-represented);
  corr_size  vote-weighted correlation, region means removed, of unit size with the counted units' swing a/n - s0;
  corr_lean  same with s0;
  raw_err    raw tally share minus the final share (points) = what the early count gets wrong if read as is;
  raw_err_prior_adj   the raw tally corrected for composition alone: sum(V0 * s0 over counted units, weighted by counted
             votes) vs the prior share of all units (points), i.e. how much of raw_err is "who reported" and not "who they voted for".
It also gives, per night, the Spearman correlation of a unit's completion/visible time with its size where the loader
carries it (Brazil: municipality complete_time; Mexico: per-section median visible time).
Output: $BRUNDER_DATA/results/model_review/order_evidence.csv (+ markdown on stdout).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
import mr_nights_pl  # noqa: E402
from mr_common import OUT, expected_counted, frames_at  # noqa: E402

from brunder import nowcast_order as no  # noqa: E402

NIGHTS = {**mr_nights_br.NIGHTS, **mr_nights_mx.NIGHTS, **mr_nights_pl.NIGHTS}
AT = (0.05, 0.10, 0.25, 0.50)


def evidence(name: str) -> tuple[list[dict], dict]:
    night = NIGHTS[name]()
    pri = night.prior
    frames = [(None, t, st) for t, st in night.ticks(1) if expected_counted(st, pri) > 0.005] if night.native \
        else frames_at(night, AT)
    prior_share = float((pri["s0"] * pri["V0"]).sum() / pri["V0"].sum())
    rows = []
    for fr, t, st in frames:
        cnt = expected_counted(st, pri)
        m = pri.merge(st[["uf", "cd_mun", "n", "a"]], on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0})
        raw = float(m["a"].sum() / m["n"].sum()) if m["n"].sum() > 0 else np.nan
        comp = float((m["s0"] * m["n"]).sum() / m["n"].sum()) if m["n"].sum() > 0 else np.nan
        rows.append({"night": name, "target": fr, "t": t, "counted_proxy": round(cnt, 3),
                     "imb_size": no.order_imbalance(st, pri, "size", night.unpooled),
                     "imb_lean": no.order_imbalance(st, pri, "lean", night.unpooled),
                     "corr_size": no.swing_correlation(st, pri, "size", night.unpooled),
                     "corr_lean": no.swing_correlation(st, pri, "lean", night.unpooled),
                     "raw_err_pt": 100 * (raw - night.final),
                     "composition_pt": 100 * (comp - prior_share)})
    order = {"night": name}
    ex = night.extra
    for key, label in (("complete_time", "spearman_complete_vs_size"), ("median_visible", "spearman_visible_vs_size")):
        if key in ex:
            s = ex[key]
            size = pri.set_index(["uf", "cd_mun"])["V0"].reindex(s.index)
            ok = s.notna() & size.notna()
            tt = pd.to_datetime(s[ok]).astype("int64")
            order[label] = float(spearmanr(tt, size[ok])[0])
    return rows, order


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    allrows, orders = [], []
    for name in NIGHTS:
        r, o = evidence(name)
        allrows += r
        orders.append(o)
        print(name, "done", flush=True)
    df = pd.DataFrame(allrows)
    if df["target"].isna().any():                      # Poland: nearest frame to each counted share
        pick = []
        for name, g in df.groupby("night"):
            if g["target"].isna().all():
                for a in AT:
                    pick.append(g.iloc[(g["counted_proxy"] - a).abs().argmin()].copy().rename(None).set_axis(g.columns).where(lambda r: r.index != "target", a))
            else:
                pick += [r for _, r in g.iterrows()]
        df = pd.DataFrame(pick)
    df.to_csv(OUT / "order_evidence.csv", index=False)
    pd.DataFrame(orders).to_csv(OUT / "order_spearman.csv", index=False)
    pd.set_option("display.width", 220)
    print(df.drop(columns=["t"]).round(2).to_string(index=False))
    print(pd.DataFrame(orders).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
