"""H1: what did first-round municipal results imply for the runoff, using only information
available on first-round night? Backtested across cycles, then compared with the market.

Out of sample: transfers fitted on cycle c project cycle c + 4 from its first round.
  2014 → 2018,  2018 → 2022,  2022 → 2026 (target)
Baseline: proportional split of eliminated candidates' voters.

Run: uv run python analysis/transfer_backtest.py
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import transfer
from brunder.paths import POLYMARKET, PROCESSED, ROOT

RES = ROOT / "results"
LULA_MKT = "will-luiz-incio-lula-da-silva-win-the-2026-brazilian-presidential-election"


def live_round(ele: int) -> pd.DataFrame:
    v = pd.read_parquet(PROCESSED / "ele2026" / f"e{ele}_c1" / "votes.parquet")
    return v[v["level"] == "mu"][["uf", "cd_mun", "cand_n", "votes"]]


def main() -> None:
    RES.mkdir(exist_ok=True)
    pd.set_option("display.width", 200)
    m = {y: transfer.municipal(y) for y in (2014, 2018, 2022)}
    m[2026] = transfer.municipal(2026, live_round(6257), live_round(6258))
    fits = {y: transfer.fit(m[y], y) for y in (2014, 2018, 2022, 2026)}
    print("in-sample transfers (α keep PT, β opponent→PT, θ others→PT):")
    for y, t in fits.items():
        print(f"  {y}: α={t.alpha:.3f} β={t.beta:.3f} θ={t.theta:.3f}")

    rows = []
    for y, prev in ((2018, 2014), (2022, 2018), (2026, 2022)):
        d = m[y]
        actual = transfer.national(d, d["s2"], "V2")
        for name, t in (("proportional", None), (f"transfers {prev}", fits[prev])):
            proj = transfer.national(d, transfer.project(d, t))
            rows.append({"target": y, "method": name, "projected": proj, "actual": actual, "error": proj - actual})
        # in-sample ceiling: how well the model can do with this cycle's own transfers
        rows.append({"target": y, "method": "in-sample (oracle)", "projected": transfer.national(d, transfer.project(d, fits[y])),
                     "actual": actual, "error": transfer.national(d, transfer.project(d, fits[y])) - actual})
    bt = pd.DataFrame(rows)
    bt.to_csv(RES / "transfer_backtest.csv", index=False)
    print("\nnational PT two-party share in the runoff:")
    print(bt.assign(**{c: bt[c] * 100 for c in ("projected", "actual", "error")}).round(2).to_string(index=False))

    oos = bt[(bt["method"].str.startswith("transfers")) & (bt["target"] < 2026)]
    sigma = float(np.sqrt((oos["error"] ** 2).mean()))
    proj26 = float(bt[(bt["target"] == 2026) & bt["method"].str.startswith("transfers")]["projected"].iloc[0])
    base26 = float(bt[(bt["target"] == 2026) & (bt["method"] == "proportional")]["projected"].iloc[0])
    print(f"\nout-of-sample RMS error 2018/2022: {sigma * 100:.2f} pt (two cycles)")
    for s in (sigma, 0.01, 0.02, 0.03):
        print(f"  σ = {s * 100:.2f} pt → P(Lula wins | transfers 2022) = {norm.cdf((proj26 - 0.5) / s):.3f}, "
              f"P(proportional) = {norm.cdf((base26 - 0.5) / s):.3f}")

    # the market between rounds
    px = pd.read_parquet(POLYMARKET / "brazil-presidential-election" / "prices" / f"{LULA_MKT}.parquet")
    px = px[(px["time"] >= "2026-10-05") & (px["time"] < "2026-10-25 20:00")].set_index("time")["price"]
    daily = px.resample("1D").last()
    print("\nPolymarket P(Lula) between rounds, daily close:", daily.round(3).to_dict())
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.step(px.index, px, where="post", color="k", lw=1.2, label="Polymarket P(Lula)")
    for s, ls in ((sigma, "-"), (0.02, "--")):
        ax.axhline(norm.cdf((proj26 - 0.5) / s), color="C3", ls=ls,
                   label=f"transfer model (2022 transfers), σ = {s * 100:.1f} pt")
        ax.axhline(norm.cdf((base26 - 0.5) / s), color="C0", ls=ls, label=f"proportional split, σ = {s * 100:.1f} pt")
    ax.set_ylim(0, 1)
    ax.set_ylabel("P(Lula wins)")
    ax.set_title("Between rounds: market vs projections available on first-round night")
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(RES / "fig8_between_rounds.png", dpi=150)
    print(RES / "fig8_between_rounds.png")


if __name__ == "__main__":
    main()
