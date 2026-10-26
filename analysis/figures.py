"""Figures for the model–market relationship. Run after r2_night.py, calibrate_tau.py.

  fig1_night_edge.png      model P vs market p on count night + the gap P − p
  fig2_edge_predicts.png   gap now vs market move over the next 30 min (does the market catch up?)
  fig3_model_vs_market.png p against P through the night (under-reaction ⇒ points below the diagonal)
  fig4_flow_impact.png     between rounds: price vs cumulative net taker flow; impact profile λ(h)
  fig5_tau_calibration.png R1/R2 national z by checkpoint and τ
  fig6_poliwave.png        live public projection (Poliwave) and raw count vs the market

Run: uv run python analysis/figures.py
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from brunder import edge
from brunder import microstructure as ms
from brunder import poliwave
from brunder.paths import POLYMARKET, RAW, ROOT

EV = POLYMARKET / "brazil-presidential-election"
LULA = "will-luiz-incio-lula-da-silva-win-the-2026-brazilian-presidential-election"
FLAVIO = "will-flvio-bolsonaro-win-the-2026-brazilian-presidential-election"
RES = ROOT / "results"
RELEASE = pd.Timestamp("2026-10-25 22:00", tz="UTC")
NIGHT_END = pd.Timestamp("2026-10-26 01:00", tz="UTC")   # both series ≈ 1 afterwards
TAUS = (0.005, 0.01, 0.02)
HEADLINE = 0.01
BRT = "America/Sao_Paulo"

plt.rcParams.update({"figure.dpi": 150, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})


def night(tau: float) -> pd.DataFrame:
    path = pd.read_csv(RES / f"r2_nowcast_tau{tau}.csv", index_col=0, parse_dates=True)["p_win"]
    px = pd.read_parquet(EV / "prices" / f"{LULA}.parquet")
    return edge.align(path, px).loc[RELEASE:NIGHT_END]


def fig1() -> None:
    fig, (a, b) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, height_ratios=[2, 1])
    base = night(HEADLINE)
    a.step(base.index.tz_convert(BRT), base["p"], where="post", color="k", lw=1.6, label="Polymarket p (Lula YES)")
    for tau in TAUS:
        d = night(tau)
        a.plot(d.index.tz_convert(BRT), d["P"], lw=1.6 if tau == HEADLINE else 0.9,
               ls="-" if tau == HEADLINE else "--", label=f"nowcast P, τ={tau}" + (" (calibrated)" if tau == HEADLINE else ""))
        b.plot(d.index.tz_convert(BRT), d["edge"], lw=1.6 if tau == HEADLINE else 0.9,
               ls="-" if tau == HEADLINE else "--")
    b.fill_between(base.index.tz_convert(BRT), 0, base["edge"], where=base["edge"] > 0, alpha=0.2, color="C1")
    b.axhline(0, color="k", lw=0.6)
    a.set_ylabel("P(Lula wins)")
    b.set_ylabel("gap P − p")
    b.set_xlabel("25–26 Oct 2026, Brasília time")
    a.set_title("Count night: public returns implied a Lula win well before the market priced it")
    a.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(RES / "fig1_night_edge.png")


def fig2() -> dict:
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
    out = {}
    for ax, tau in zip(axes, TAUS):
        d = night(tau)
        fwd = d["p"].shift(-30) - d["p"]                       # market move over the next 30 min
        m = pd.DataFrame({"gap": d["edge"], "fwd": fwd}).dropna()
        slope, icpt = np.polyfit(m["gap"], m["fwd"], 1)
        r = m["gap"].corr(m["fwd"])
        mins = (m.index - m.index[0]).total_seconds() / 60
        sc = ax.scatter(m["gap"], m["fwd"], c=mins, cmap="viridis", s=8)
        xs = np.linspace(m["gap"].min(), m["gap"].max(), 50)
        ax.plot(xs, icpt + slope * xs, color="C3", lw=1.5)
        ax.plot(xs, xs, color="0.5", ls=":", lw=1, label="full catch-up (slope 1)")
        ax.axhline(0, color="k", lw=0.5)
        ax.axvline(0, color="k", lw=0.5)
        ax.set_title(f"τ={tau}: slope {slope:.2f}, r = {r:.2f}")
        ax.set_xlabel("gap P − p at t")
        out[tau] = {"slope": slope, "r": r, "n": len(m)}
    axes[0].set_ylabel("market move p(t+30m) − p(t)")
    axes[0].legend(fontsize=7, loc="upper left")
    fig.colorbar(sc, ax=axes, label="minutes after publication resumed", shrink=0.8)
    fig.suptitle("Does today's gap predict the market's next 30 minutes? (1-min grid, overlapping windows)")
    fig.savefig(RES / "fig2_edge_predicts.png", bbox_inches="tight")
    return out


def fig3() -> None:
    d = night(HEADLINE)
    fig, ax = plt.subplots(figsize=(5.5, 5))
    mins = (d.index - d.index[0]).total_seconds() / 60
    sc = ax.scatter(d["P"], d["p"], c=mins, cmap="viridis", s=10)
    ax.plot([0, 1], [0, 1], color="0.5", ls=":")
    ax.set_xlim(0.6, 1.005)
    ax.set_ylim(0.3, 1.005)
    ax.set_xlabel(f"nowcast P (τ={HEADLINE})")
    ax.set_ylabel("Polymarket p")
    ax.set_title("Market vs model, count night\n(below the diagonal = market under-weights the count)")
    fig.colorbar(sc, label="minutes after publication resumed")
    fig.tight_layout()
    fig.savefig(RES / "fig3_model_vs_market.png")


def fig4() -> None:
    mk = pd.read_parquet(EV / "markets.parquet").set_index("slug")
    t = pd.concat([ms.to_yes(pd.read_parquet(EV / "trades" / f"{s}.parquet"), str(mk.loc[s, "yes_token"]), invert=inv)
                   for s, inv in ((LULA, False), (FLAVIO, True))]).sort_values("time")
    px = pd.read_parquet(EV / "prices" / f"{LULA}.parquet")
    a0, a1 = pd.Timestamp("2026-10-05", tz="UTC"), pd.Timestamp("2026-10-25 20:00", tz="UTC")
    w = t[(t["time"] >= a0) & (t["time"] < a1)]
    tk = w[w["is_taker"]]
    flow = (tk["sign"] * tk["notional"]).groupby(tk["time"].dt.floor("1h")).sum().cumsum() / 1e6
    p = px.set_index("time")["price"]
    p = p[(p.index >= a0) & (p.index < a1)].resample("1h").last().ffill()

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(12, 4.2), width_ratios=[1.6, 1])
    ax.plot(p.index.tz_convert(BRT), p, color="k", lw=1.2, label="Polymarket p (Lula YES)")
    ax.set_ylabel("p")
    ax2 = ax.twinx()
    ax2.plot(flow.index.tz_convert(BRT), flow, color="C3", lw=1.2, label="cumulative net taker flow → Lula")
    ax2.set_ylabel("$M")
    ax2.spines["right"].set_visible(True)
    hourly = pd.DataFrame({"dp": p.diff(), "f": flow.diff()}).dropna()
    r = hourly["dp"].corr(hourly["f"])
    ax.set_title(f"Between rounds: price vs aggressive flow (hourly Δp–flow r = {r:.2f})")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")

    hz = ["1min", "5min", "15min", "30min", "1h", "2h", "4h"]
    for name, (b0, b1), c in (("between rounds", (a0, a1), "C0"),
                               ("count night", (RELEASE, pd.Timestamp("2026-10-26 02:30", tz="UTC")), "C1")):
        imp = ms.price_impact(t[(t["time"] >= b0) & (t["time"] < b1)], px, hz)
        lam = imp["lambda_per_$10k"] * 100
        se = lam / imp["t"]
        x = np.arange(len(imp))
        bx.errorbar(x, lam, yerr=1.96 * se, marker="o", capsize=3, color=c, label=name)
    bx.axhline(0, color="k", lw=0.5)
    bx.set_xticks(np.arange(len(hz)), hz)
    bx.set_xlabel("horizon after the trade minute")
    bx.set_ylabel("Δp per $10k net taker flow (pt)")
    bx.set_title("Impact builds then reverts between rounds\n(transient pressure); flat on count night")
    bx.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(RES / "fig4_flow_impact.png")


def fig5() -> None:
    c = pd.read_csv(RES / "tau_calibration.csv")
    c = c[c["race"] == "br"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, rnd in zip(axes, ("R1", "R2")):
        for tau, g in c[c["round"] == rnd].groupby("tau"):
            ax.plot(g["level"] * 100, g["z"], marker="o", lw=2 if tau == HEADLINE else 1, label=f"τ={tau}")
        ax.axhspan(-1.96, 1.96, color="0.9", zorder=0)
        ax.axhline(0, color="k", lw=0.5)
        ax.set_title({"R1": "1st round (prior: 2022) — out of sample", "R2": "Runoff (prior: 2026 R1) — in sample"}[rnd])
        ax.set_xlabel("% of national vote counted")
    axes[0].set_ylabel("z = (final − projected) / projected sd")
    axes[1].legend(fontsize=7, ncol=2)
    fig.suptitle("Calibrating τ: national projection errors (grey band = ±1.96)")
    fig.tight_layout()
    fig.savefig(RES / "fig5_tau_calibration.png")


def fig6() -> pd.DataFrame:
    h = poliwave.history(RAW / "poliwave" / "presidente-2026.html").set_index("time")
    px = pd.read_parquet(EV / "prices" / f"{LULA}.parquet").set_index("time")["price"]
    a, b = pd.Timestamp("2026-10-25 20:00", tz="UTC"), pd.Timestamp("2026-10-26 00:30", tz="UTC")
    px = px[(px.index >= a) & (px.index <= b)]
    h = h[h.index <= b]
    fig, (ax, cx) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, height_ratios=[2.2, 1])
    ax.step(px.index.tz_convert(BRT), px, where="post", color="k", lw=1.6, label="Polymarket p (Lula YES)")
    ax.set_ylabel("market probability")
    ax.set_ylim(0, 1.02)
    bx = ax.twinx()
    bx.spines["right"].set_visible(True)
    bx.plot(h.index.tz_convert(BRT), h["share_13"], color="C0", lw=1, marker=".", ms=3, label="raw count: Lula % of valid")
    bx.plot(h.index.tz_convert(BRT), h["proj_13"], color="C3", lw=2,
            label="Poliwave stored projection history (live status unverified)")
    bx.axhline(50, color="0.5", ls=":", lw=1)
    bx.set_ylim(46, 58)
    bx.set_ylabel("Lula share (%)")
    first = h["proj_13"].first_valid_index()
    behind = h[(h["share_13"] < 50) & (h.index > first)]
    if len(behind):
        bx.axvspan(behind.index[0].tz_convert(BRT), behind.index[-1].tz_convert(BRT), color="C0", alpha=0.08)
        bx.text(behind.index[0].tz_convert(BRT), 56.8, "raw count shows Flávio ahead;\nprojection still Lula", fontsize=8, color="C0")
    pm = px[:first].iloc[-1]
    bx.annotate(f"first projection {first.tz_convert(BRT):%H:%M}: Lula {h.loc[first, 'proj_13']:.1f}%\n"
                f"at {h.loc[first, 'counted_pct']:.1f}% counted; market {pm:.2f}",
                xy=(first.tz_convert(BRT), h.loc[first, "proj_13"]), xytext=(-170, 25), textcoords="offset points",
                fontsize=8, arrowprops={"arrowstyle": "->", "lw": 0.8})
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = bx.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="lower right")
    ax.set_title("Market vs raw count and Poliwave's stored projection history (live status unverified)")
    cx.plot(h.index.tz_convert(BRT), h["counted_pct"], color="0.3")
    cx.set_ylabel("% sections counted")
    cx.set_xlabel("25 Oct 2026, Brasília time (TSE publication paused 17:06–19:00)")
    fig.tight_layout()
    fig.savefig(RES / "fig6_poliwave.png")
    h["market"] = px.reindex(px.index.union(h.index)).ffill().reindex(h.index)
    return h


def main() -> None:
    fig1()
    stats = fig2()
    fig3()
    fig4()
    fig5()
    fig6().to_csv(RES / "poliwave_vs_market.csv")
    print(pd.DataFrame(stats).T.round(3).to_string())
    for f in sorted(RES.glob("fig*.png")):
        print(f)


if __name__ == "__main__":
    main()
