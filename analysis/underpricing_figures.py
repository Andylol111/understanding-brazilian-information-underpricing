"""How much of the count's information does Polymarket price, and how fast does it catch up?

Reads the cached 1-minute replay ticks (results/night/<id>/ticks.parquet, written by
analysis/night_figures.py cache) and draws:
  docs/figures/underpricing_gap_predicts.png  gap (model P − market p) vs the market's next 30-min move
  docs/figures/underpricing_catchup.png       share of the gap the market closes after h minutes
  docs/figures/underpricing_market_vs_count.png  runoff: market vs raw tally vs model projection
and prints the slopes. A slope of 0 means today's gap tells you nothing about the price's next move
(the market already priced the count); 1 means the market eventually moves the whole gap.

Sample: every market and minute after the market's own 1% trade gate (scope_counted ≥ 0.01) and before
its price settles (both p and P strictly inside (0.02, 0.98)), so settled markets don't add trivial
zero-gap points. Windows overlap, so CIs are a cluster bootstrap over (market, 30-min block).
Model-vs-market evidence: the first round is in-sample; the runoff is pre-specified (outcomes known).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results" / "night"
FIG = ROOT / "docs" / "figures"
BRT = "America/Sao_Paulo"
POOLS = [("2026 first round (in-sample)", ["brazil_2026_r1_dev_mun"]),
         ("2026 runoff night (pre-specified)", ["night_r2_v1"] + [f"night_gov_r2_{u}_v1" for u in ("ac", "am", "df", "es", "rn", "to")])]
HORIZONS = (1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120)
rng = np.random.default_rng(0)


def pairs(cid: str, h: int) -> pd.DataFrame:
    t = pd.read_parquet(RES / cid / "ticks.parquet")[["t", "scope", "price", "P", "scope_counted"]]
    t["t"] = pd.to_datetime(t["t"], utc=True)
    out = []
    for scope, g in t.groupby("scope"):
        g = g.drop_duplicates("t").set_index("t").sort_index()
        fut = g["price"].reindex(g.index + pd.Timedelta(minutes=h)).to_numpy()
        d = pd.DataFrame({"scope": f"{cid}:{scope}", "t": g.index, "gap": g["P"] - g["price"], "move": fut - g["price"].to_numpy(),
                          "p": g["price"], "P": g["P"], "c": g["scope_counted"]})
        out.append(d)
    d = pd.concat(out)
    d = d[(d["c"] >= 0.01) & d["p"].between(0.02, 0.98, inclusive="neither") & d["P"].between(0.02, 0.98, inclusive="neither")]
    d = d.dropna(subset=["move"])
    d["cluster"] = d["scope"] + "_" + d["t"].dt.floor("30min").astype(str)
    return d


def slope_ci(d: pd.DataFrame, b: int = 500) -> tuple[float, float, float, float, int]:
    def fit(x):
        return float(np.polyfit(x["gap"], x["move"], 1)[0]) if len(x) > 3 and x["gap"].std() > 0 else np.nan
    s = fit(d)
    groups = [g for _, g in d.groupby("cluster")]
    k = len(groups)
    boots = [fit(pd.concat([groups[i] for i in rng.integers(0, k, k)])) for _ in range(b)] if k > 2 else [np.nan]
    lo, hi = np.nanpercentile(boots, [2.5, 97.5]) if k > 2 else (np.nan, np.nan)
    r = float(np.corrcoef(d["gap"], d["move"])[0, 1]) if len(d) > 3 else np.nan
    return s, lo, hi, r, k


def pool(cids: list[str], h: int) -> pd.DataFrame:
    return pd.concat([pairs(c, h) for c in cids], ignore_index=True)


def fig_gap_predicts() -> dict:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), sharey=True)
    stats = {}
    for ax, (label, cids) in zip(axes, POOLS):
        d = pool(cids, 30)
        s, lo, hi, r, k = slope_ci(d)
        stats[label] = {"slope": s, "ci": [lo, hi], "r": r, "n": len(d), "clusters": k, "markets": int(d["scope"].nunique())}
        nat = d["scope"].isin(["brazil_2026_r1_dev_mun:br", "night_r2_v1:br"])
        gov = d["scope"].str.startswith("night_gov")
        st = ~nat & ~gov
        ax.scatter(d.loc[st, "gap"], d.loc[st, "move"], s=7, alpha=0.35, color="C0", label="presidential state markets")
        if gov.any():
            ax.scatter(d.loc[gov, "gap"], d.loc[gov, "move"], s=10, alpha=0.6, color="C2", label="governor markets")
        ax.scatter(d.loc[nat, "gap"], d.loc[nat, "move"], s=12, alpha=0.85, color="C3", label="national market")
        x = np.linspace(d["gap"].min(), d["gap"].max(), 10)
        ax.plot(x, x, color="0.5", ls=":", lw=1.2, label="full catch-up (slope 1)")
        b0 = np.polyfit(d["gap"], d["move"], 1)[1]
        ax.plot(x, s * x + b0, color="k", lw=1.6, label=f"fit: slope {s:.2f} [{lo:.2f}, {hi:.2f}]")
        ax.axhline(0, color="0.7", lw=0.8), ax.axvline(0, color="0.7", lw=0.8)
        ax.set_title(f"{label}\n{d['scope'].nunique()} markets, {len(d):,} market-minutes, r = {r:.2f}", fontsize=10)
        ax.set_xlabel("gap now: model P − market price")
        ax.legend(fontsize=7.5, loc="upper left")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("market's move over the next 30 min")
    fig.suptitle("Does the gap between what the count implies and the price predict where the price goes next?", fontsize=12)
    fig.tight_layout()
    fig.savefig(FIG / "underpricing_gap_predicts.png", dpi=150)
    return stats


def fig_catchup() -> dict:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    curves = {}
    for (label, cids), c in zip(POOLS, ("C0", "C3")):
        pts = []
        for h in HORIZONS:
            s, lo, hi, r, k = slope_ci(pool(cids, h), b=300)
            pts.append((h, s, lo, hi))
        p = np.array(pts, dtype=float)
        ax.plot(p[:, 0], p[:, 1], marker="o", color=c, label=label)
        ax.fill_between(p[:, 0], p[:, 2], p[:, 3], color=c, alpha=0.12)
        curves[label] = {int(h): [round(float(s), 3), round(float(lo), 3), round(float(hi), 3)] for h, s, lo, hi in pts}
    ax.axhline(1, color="0.5", ls=":", lw=1)
    ax.text(121, 1.0, " full catch-up", va="center", fontsize=8, color="0.4")
    ax.axhline(0, color="0.7", lw=0.8)
    ax.set_xscale("log")
    ax.set_xticks(HORIZONS, [str(h) for h in HORIZONS])
    ax.set_xlabel("minutes after the gap is observed")
    ax.set_ylabel("share of the gap the market has closed\n(slope of price move on gap)")
    ax.set_title("How fast does the market catch up with the count? (95% cluster-bootstrap bands)")
    ax.legend(fontsize=8.5)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG / "underpricing_catchup.png", dpi=150)
    return curves


def fig_market_vs_count() -> Path:
    cid = "night_r2_v1"
    t = pd.read_parquet(RES / cid / "ticks.parquet")
    t = t[t["scope"] == "br"].drop_duplicates("t").copy()
    t["t"] = pd.to_datetime(t["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
    tally = pd.read_parquet(RES / cid / "tally.parquet")
    tally["t"] = pd.to_datetime(tally["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
    final = json.loads((RES / cid / "final.json").read_text())["final_share"]
    lo, hi = pd.Timestamp("2026-10-25 17:00"), pd.Timestamp("2026-10-25 21:30")
    t, tally = t[(t.t >= lo) & (t.t <= hi)], tally[(tally.t >= lo) & (tally.t <= hi)]
    fig, (a1, a3) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2.4, 1]})
    a1.step(t.t, t.price, where="post", color="k", lw=1.6, label="Polymarket p (Lula YES)")
    a1.set_ylabel("market probability")
    a1.set_ylim(0, 1.02)
    a2 = a1.twinx()
    a2.plot(tally.t, 100 * tally.raw_share, color="C0", lw=1.5, label="raw count: Lula % of two-party")
    a2.plot(t.t, 100 * t.share, color="C3", lw=2, label="model projection of the final share")
    a2.axhline(100 * final, color="C3", ls="--", lw=0.8)
    a2.axhline(50, color="0.6", ls=":", lw=1)
    a2.set_ylim(46, 58)
    a2.set_ylabel("Lula share (%)")
    behind = tally[(tally.raw_share < 0.5) & (tally.counted > 0.01)]
    if len(behind):
        a1.axvspan(behind.t.min(), behind.t.max(), color="C0", alpha=0.08)
        a1.text(behind.t.min(), 0.04, " raw count shows Flávio ahead;\n model still Lula", fontsize=8.5, color="C0", va="bottom")
    h1, l1 = a1.get_legend_handles_labels()
    h2, l2 = a2.get_legend_handles_labels()
    a1.legend(h1 + h2, l1 + l2, fontsize=8.5, loc="lower right")
    a1.set_title("Runoff night: market vs raw count and the count model's projection (official section data)")
    a3.plot(tally.t, 100 * tally.counted, color="0.3")
    a3.set_ylabel("% sections counted")
    a3.set_ylim(0, 102)
    for ax in (a1, a3):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.grid(alpha=0.25)
    a3.set_xlabel("25 Oct 2026, Brasília time")
    fig.tight_layout()
    p = FIG / "underpricing_market_vs_count.png"
    fig.savefig(p, dpi=150)
    return p


def _robust_frame(cids: list[str], h: int = 30, skip: int = 0, lag: int = 0, rev: int = 5) -> pd.DataFrame:
    """Same sample as pairs(), with the variants the robustness checks need on a filled 1-minute grid."""
    rows = []
    for cid in cids:
        t = pd.read_parquet(RES / cid / "ticks.parquet")[["t", "scope", "price", "P", "scope_counted"]]
        t["t"] = pd.to_datetime(t["t"], utc=True)
        for scope, g in t.groupby("scope"):
            g = g.drop_duplicates("t").set_index("t").sort_index()
            g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq="1min")).ffill()
            p = g["price"]
            rows.append(pd.DataFrame({"scope": f"{cid}:{scope}", "t": g.index, "P": g["P"], "p": p, "c": g["scope_counted"],
                                      "p_gap": p.shift(lag), "move": p.shift(-(skip + h)) - p.shift(-skip),
                                      "rev": p.shift(rev) - p}))
    d = pd.concat(rows)
    d = d[(d.c >= 0.01) & d.p.between(0.02, 0.98, inclusive="neither") & d.P.between(0.02, 0.98, inclusive="neither")]
    d = d.dropna(subset=["move", "p_gap", "rev"])
    d["gap"], d["half"] = d.P - d.p_gap, 0.5 - d.p_gap
    d["cluster"] = d.scope + "_" + d.t.dt.floor("30min").astype(str)
    return d


def _ols_ci(d: pd.DataFrame, cols: list[str], b: int = 400) -> list[str]:
    def fit(x):
        X = np.column_stack([np.ones(len(x))] + [x[c].to_numpy() for c in cols])
        return np.linalg.lstsq(X, x["move"].to_numpy(), rcond=None)[0][1:]
    est, groups = fit(d), [g for _, g in d.groupby("cluster")]
    k = len(groups)
    boots = np.array([fit(pd.concat([groups[i] for i in rng.integers(0, k, k)])) for _ in range(b)])
    lo, hi = np.percentile(boots, [2.5, 97.5], axis=0)
    return [f"{c} {e:+.2f} [{a:+.2f}, {z:+.2f}]" for c, e, a, z in zip(cols, est, lo, hi)]


def robustness() -> None:
    """Is the 30-min slope just price noise or mean reversion? gap and move share p_t, so bounce in p_t alone
    would give a positive slope. Checks: start the move later, take the gap from an earlier price, swap the
    model for a constant 0.5, and control for the market's own recent move."""
    for label, cids in POOLS:
        base = _robust_frame(cids)
        print(f"== {label} (n={len(base)})")
        checks = [("baseline", base, ["gap"])]
        checks += [(f"move starts {s} min later", _robust_frame(cids, skip=s), ["gap"]) for s in (1, 2, 5)]
        checks += [(f"gap from p(t-{k})", _robust_frame(cids, lag=k), ["gap"]) for k in (1, 5)]
        checks += [("placebo: model replaced by 0.5", base, ["half"]), ("placebo: own 5-min reversal", base, ["rev"]),
                   ("gap + reversal control", base, ["gap", "rev"]), ("gap + 0.5 placebo", base, ["gap", "half"])]
        for name, d, cols in checks:
            print(f"  {name:32s}", "  ".join(_ols_ci(d, cols)))


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["robustness"]:
        robustness()
        raise SystemExit
    s = fig_gap_predicts()
    c = fig_catchup()
    fig_market_vs_count()
    print(json.dumps({"gap_predicts_30m": s, "catchup_slopes": c}, indent=1, default=float))
