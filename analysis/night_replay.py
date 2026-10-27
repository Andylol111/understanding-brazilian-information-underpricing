"""Replay a count from BU-web section receipts against Polymarket and score it.

Outputs (results/night/<config>/): ticks, per-market evaluation (lead time, first-signal P&L),
calibration of the model's 90% intervals along the count, and a chart for the national market.

Run: uv run python analysis/night_replay.py configs/brazil_2026_r1_replay.toml
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import bweb, integrity, night, registry
from brunder.paths import ROOT


def main(cfg_path: str) -> None:
    pd.set_option("display.width", 220)
    cfg = night.load_config(Path(cfg_path))
    if "id" in cfg:                       # a registered (frozen) test: verified inputs, refuse if edited, log the look
        integrity.check_staged(Path(cfg_path))      # before the look: inputs must come from a verified local stage
        look = registry.open_holdout(registry.load_spec(Path(cfg_path)))
        print(f"registered test {cfg['id']}, look #{look['look']}")
    E, R, T = cfg["election"], cfg["replay"], cfg.get("trade", {})
    out = ROOT / "results" / "night" / cfg["_id"]
    out.mkdir(parents=True, exist_ok=True)

    ticks = night.replay(Path(cfg_path), lag="0min", step="2min")
    sec = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    g = sec.groupby("uf")[["a", "b", "valid"]].sum()
    if E["race"] == "margin":
        final = {u: (r.a - r.b) / r.valid for u, r in g.iterrows()}
        final["br"] = (sec["a"].sum() - sec["b"].sum()) / sec["valid"].sum()
        outcome = {u: int(v > 0) for u, v in final.items()}
    else:
        final = {u: r.a / (r.a + r.b) for u, r in g.iterrows()}
        final["br"] = sec["a"].sum() / (sec["a"].sum() + sec["b"].sum())
        outcome = {u: int(v > 0.5) for u, v in final.items()}

    ev = night.evaluate(ticks, outcome, T.get("min_edge", 0.05), T.get("min_counted", 0.01), T.get("favoured_only", False))
    ev.to_csv(out / "evaluation.csv", index=False)
    print(ev.sort_values("lead_min", ascending=False).round(3).to_string(index=False))
    traded = ev.dropna(subset=["return_per_$"])
    print(f"\nmarkets {len(ev)} | median lead {ev['lead_min'].median():.0f} min | mean lead {ev['lead_min'].mean():.1f} min")
    print(f"signals {len(traded)} | hit rate {(traded['return_per_$'] > 0).mean():.3f} | "
          f"mean return per $ {traded['return_per_$'].mean():+.3f} | total on $1 each {traded['return_per_$'].sum():+.2f}")

    # calibration of the national projection along the count: z = (final − projected) / sd
    nat = ticks[ticks["scope"] == "br"].drop_duplicates("t").set_index("t")
    z = (final["br"] - nat["share"]) / nat["share_sd"]
    # bins include the pre-committed splits (1–90% and 90–95% counted; docs/edge_tool.md), plus the tail
    bins = pd.cut(nat["counted"], [0, 0.01, 0.05, 0.2, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0])
    cal = pd.DataFrame({"z": z, "bin": bins}).groupby("bin", observed=True)["z"].agg(
        ticks="size", mean_z="mean", share_inside_90=lambda x: float((x.abs() < 1.645).mean()))
    cal.to_csv(out / "national_calibration.csv")
    window = nat["counted"].between(0.01, 0.95, inclusive="right")
    for lo, hi in ((0.01, 0.95), (0.01, 0.90), (0.90, 0.95)):
        w = z[nat["counted"].between(lo, hi, inclusive="right") & window]
        print(f"pre-committed window ({lo:.0%}, {hi:.0%}] counted: {len(w)} ticks, inside 90% = "
              f"{(w.abs() < 1.645).mean():.2f}, mean z = {w.mean():+.2f}")
    print("\nnational projection calibration along the count (90% interval should contain the final ~90%):")
    print(cal.round(3).to_string())

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(nat.index.tz_convert("America/Sao_Paulo"), nat["P"], color="C3", label="model P(A wins)")
    ax.step(nat.index.tz_convert("America/Sao_Paulo"), nat["price"], where="post", color="k", label="Polymarket")
    ax2 = ax.twinx()
    ax2.plot(nat.index.tz_convert("America/Sao_Paulo"), nat["counted"], color="0.6", ls=":", label="counted")
    ax2.set_ylim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title(f"{cfg['_id']}: national market, model vs price (outcome: {'YES' if outcome['br'] else 'NO'})")
    ax.legend(loc="center left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "national.png", dpi=150)
    print(out / "national.png")


if __name__ == "__main__":
    main(sys.argv[1])
