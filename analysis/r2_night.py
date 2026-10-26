"""H2 on the 2026 runoff count: reconstructed count nowcast vs Polymarket "Lula wins" price.

Prereqs:
  brunder tse-snapshot --ele 6257 && brunder tse-build --ele 6257
  brunder tse-snapshot --ele 6258 && brunder tse-build --ele 6258
  brunder pm-download brazil-presidential-election
Run:  uv run python analysis/r2_night.py
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from brunder import edge, microstructure, nowcast
from brunder.paths import POLYMARKET, PROCESSED, ROOT

LULA, FLAVIO = "13", "22"
MARKET = "will-luiz-incio-lula-da-silva-win-the-2026-brazilian-presidential-election"
OUT = ROOT / "results"


def load(ele: int):
    d = PROCESSED / "ele2026" / f"e{ele}_c1"
    return pd.read_parquet(d / "votes.parquet"), pd.read_parquet(d / "progress.parquet")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    r1_votes, _ = load(6257)
    r2_votes, r2_prog = load(6258)

    r1_mu = r1_votes[r1_votes["level"] == "mu"][["uf", "cd_mun", "cand_n", "votes"]]
    prior = nowcast.prior_from_final(r1_mu, LULA, FLAVIO)
    final = nowcast.state_from_votes(r2_votes, LULA)
    completion = r2_prog[r2_prog["level"] == "mun"][["uf", "cd_mun", "last_update_utc"]].dropna()

    # Night timeline (Brasília = UTC-3), sources in docs/research_design.md:
    #   17:00 polls close; 17:06 first partial (0.25% of sections, Lula 57.66%) is published;
    #   STF extends voting to 19:00 where still open → TSE pauses publication;
    #   19:00+ publication resumes; 23:26 totalization finished.
    # The 17:06 partial's 1,225 sections can't be identified from the final files, so the model
    # sees nothing until RELEASE (prior only) — conservative for the model.
    t0 = pd.Timestamp("2026-10-25 20:00", tz="UTC")
    first_partial = pd.Timestamp("2026-10-25 20:06", tz="UTC")
    release = pd.Timestamp("2026-10-25 22:00", tz="UTC")
    t_end = pd.Timestamp("2026-10-26 02:30", tz="UTC")
    times = pd.date_range(t0, t_end, freq="1min")
    states = nowcast.reconstruct_states(final, completion, times, release=release)

    px = pd.read_parquet(POLYMARKET / "brazil-presidential-election" / "prices" / f"{MARKET}.parquet")
    outcome = 1

    fig, ax = plt.subplots(figsize=(11, 5))
    mk = px[(px["time"] >= t0 - pd.Timedelta("2h")) & (px["time"] <= times[-1])]
    ax.step(mk["time"], mk["price"], where="post", color="k", lw=1.5, label="Polymarket p (Lula YES)")
    trades = microstructure.to_yes(
        pd.read_parquet(POLYMARKET / "brazil-presidential-election" / "trades" / f"{MARKET}.parquet"),
        yes_token=str(pd.read_parquet(POLYMARKET / "brazil-presidential-election" / "markets.parquet")
                      .set_index("slug").loc[MARKET, "yes_token"]))
    rows = []
    for tau in (0.005, 0.01, 0.02):
        path = nowcast.path(states, prior, nowcast.Params(tau=tau))
        # H2 is about the count: score from publication onward (pre-release the model is prior-only)
        df = edge.align(path["p_win"], px).loc[release:]
        ct = pd.DataFrame({"model": edge.crossing_times(df["P"]), "market": edge.crossing_times(df["p"])})
        ct["market_delay_min"] = (ct["market"] - ct["model"]).dt.total_seconds() / 60
        lag = edge.information_lag(df, 60)
        sc = edge.scores(df, outcome)
        bt_trades, bt = edge.backtest(df, outcome, threshold=0.05, half_spread=0.005)
        # notional that actually traded YES-up within ±5 min of each YES signal at ≤ our cost
        cap = sum(microstructure.capacity(trades, r.time, "5min", side=1, max_price=r.cost)
                  for r in bt_trades.itertuples() if r.side == "YES") if len(bt_trades) else 0.0
        rows.append({"tau": tau, "peak_lag_min": int(lag["corr"].idxmax()), "peak_corr": lag["corr"].max(),
                     "brier_model": sc.loc["brier", "model"], "brier_market": sc.loc["brier", "market"],
                     **{f"delay@{lv}": ct.loc[lv, "market_delay_min"] for lv in (0.9, 0.99)},
                     "bt_n": bt["n"], "bt_roi": bt["roi"], "capacity_usd": cap})
        path.to_csv(OUT / f"r2_nowcast_tau{tau}.csv")
        ax.plot(path.index, path["p_win"], lw=1, label=f"nowcast P, τ={tau}")
    ax.axvspan(first_partial, release, color="0.85", label="TSE publication paused")
    ax2 = ax.twinx()
    ax2.plot(path.index, path["counted_frac"], color="0.6", ls=":", label="counted share (reconstructed)")
    ax2.set_ylim(0, 1)
    ax.set_ylabel("P(Lula wins)")
    ax.set_title("2026 runoff count: reconstructed nowcast vs Polymarket")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "r2_night.png", dpi=150)

    summary = pd.DataFrame(rows).set_index("tau")
    summary.to_csv(OUT / "r2_night_summary.csv")
    pd.set_option("display.width", 200)
    print(summary.round(4).to_string())
    print(OUT / "r2_night.png")


if __name__ == "__main__":
    main()
