"""Figures that show how a count night unfolds and how it was priced, for every replayed race.

Two steps, because each config's inputs live in its own verified stage (BRUNDER_DATA):
    BRUNDER_DATA=~/brunder-stage/<stage> uv run python analysis/night_figures.py cache <config.toml>
    uv run python analysis/night_figures.py draw
`cache` rebuilds the replay ticks (deterministic; not a registered look: looks are logged only by
analysis/night_replay.py) plus the raw running tally of the race, and writes them under
results/night/<id>/. `draw` reads those caches and writes docs/figures/night_*.png.
"""

from __future__ import annotations

import json
import sys
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

RACES = [  # (config id, label, evidence)
    ("brazil_2026_r1_dev_mun", "President, 1st round", "in-sample"),
    ("night_r2_v1", "President, runoff", "pre-specified"),
    ("night_gov_r2_es_v1", "Governor ES", "pre-specified"),
    ("night_gov_r2_rn_v1", "Governor RN", "pre-specified"),
    ("night_gov_r2_am_v1", "Governor AM", "pre-specified"),
    ("night_gov_r2_ac_v1", "Governor AC", "pre-specified"),
    ("night_gov_r2_to_v1", "Governor TO", "pre-specified"),
    ("night_gov_r2_df_v1", "Governor DF", "pre-specified"),
]


def cache(cfg_path: str) -> None:
    from brunder import bweb, night
    cfg = night.load_config(Path(cfg_path))
    E, R = cfg["election"], cfg["replay"]
    out = RES / cfg["_id"]
    out.mkdir(parents=True, exist_ok=True)
    t = night.replay(Path(cfg_path), lag="0min", step="1min", save=False)
    t.to_parquet(out / "ticks.parquet")
    s = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    s = s.sort_values("received")
    cum = s.assign(k=np.arange(1, len(s) + 1), A=s["a"].cumsum(), B=s["b"].cumsum()).groupby("received")[["k", "A", "B"]].last()
    tally = pd.DataFrame({"t": (cum.index + pd.Timedelta("3h")).tz_localize("UTC"), "counted": cum["k"].values / len(s),
                          "raw_share": (cum["A"] / (cum["A"] + cum["B"])).values})
    tally.to_parquet(out / "tally.parquet")
    final = float(s["a"].sum() / (s["a"].sum() + s["b"].sum()))
    (out / "final.json").write_text(json.dumps({"final_share": final, "label_a": E["cand_a"]}))
    print(cfg["_id"], len(t), "ticks;", len(tally), "tally points; final", round(final, 4))


def _load(cid: str):
    d = RES / cid
    t = pd.read_parquet(d / "ticks.parquet")
    t = t[t["scope"] == "br"].drop_duplicates("t").copy()
    t["t"] = pd.to_datetime(t["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
    tally = pd.read_parquet(d / "tally.parquet")
    tally["t"] = pd.to_datetime(tally["t"], utc=True).dt.tz_convert(BRT).dt.tz_localize(None)
    final = json.loads((d / "final.json").read_text())["final_share"]
    ev = pd.read_csv(d / "evaluation.csv")
    return t, tally, final, ev[ev["scope"] == "br"].iloc[0]


def _fmt(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(alpha=0.25)


def fig_how_it_works(cid: str = "night_r2_v1") -> Path:
    """Runoff national: what the raw tally said, what the model inferred, and what the market priced."""
    t, tally, final, ev = _load(cid)
    lo, hi = pd.Timestamp("2026-10-25 18:50"), pd.Timestamp("2026-10-25 21:00")
    tt, ta = t[(t.t >= lo) & (t.t <= hi)], tally[(tally.t >= lo) & (tally.t <= hi)]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={"height_ratios": [1.1, 1]})
    a1.plot(ta.t, 100 * ta.raw_share, color="0.35", lw=1.6, label="raw running tally (what the results site showed)")
    a1.plot(tt.t, 100 * tt.share, color="C3", lw=2, label="model's projected final share")
    a1.fill_between(tt.t, 100 * (tt.share - 1.645 * tt.share_sd), 100 * (tt.share + 1.645 * tt.share_sd), color="C3", alpha=0.15,
                    label="model's 90% interval")
    a1.axhline(100 * final, color="k", ls="--", lw=1, label=f"final result {100 * final:.2f}%")
    a1.axhline(50, color="0.6", lw=0.8)
    behind = ta[(ta.raw_share < 0.5) & (ta.counted > 0.01)]
    for ax in (a1, a2):
        if len(behind):
            ax.axvspan(behind.t.min(), behind.t.max(), color="C0", alpha=0.08)
    a1.set_ylim(46, 56)
    a1.set_ylabel("Lula, % of two-party vote")
    a1.legend(loc="upper right", fontsize=8.5)
    a1.set_title("Runoff night, 25 Oct 2026: the raw count said one thing, the model another, and the market followed the count")
    a2.step(tt.t, tt.price, where="post", color="k", lw=1.6, label="Polymarket: P(Lula wins)")
    a2.plot(tt.t, tt.P, color="C3", lw=2, label="model: P(Lula wins)")
    a2.set_ylim(0, 1.02)
    a2.set_ylabel("probability / price")
    if len(behind):
        a2.text(behind.t.min(), 0.06, "  raw tally shows Flávio ahead", color="C0", fontsize=9)
    m = tt.loc[(tt.t >= behind.t.min()) & (tt.t <= behind.t.max())] if len(behind) else tt.iloc[:0]
    if len(m):
        low = m.loc[m.price.idxmin()]
        a2.annotate(f"market falls to {low.price:.2f}", (low.t, low.price), (low.t + pd.Timedelta("12min"), 0.35),
                    arrowprops={"arrowstyle": "->", "color": "0.3"}, fontsize=9)
    a3 = a2.twinx()
    a3.plot(ta.t, 100 * ta.counted, color="0.6", ls=":", lw=1.2)
    a3.set_ylim(0, 100)
    a3.set_ylabel("% of sections counted (dotted)", color="0.45")
    a2.legend(loc="lower right", fontsize=8.5)
    _fmt(a1), _fmt(a2)
    a2.set_xlabel("Brasília time")
    fig.tight_layout()
    p = FIG / "night_how_it_works.png"
    fig.savefig(p, dpi=150)
    return p


def fig_panel() -> Path:
    fig, axes = plt.subplots(2, 4, figsize=(15, 6.6), sharey=True)
    for ax, (cid, label, evid) in zip(axes.flat, RACES):
        t, tally, final, ev = _load(cid)
        start = tally.t.min() - pd.Timedelta("10min")
        end = tally.loc[tally.counted >= 0.98, "t"].min() + pd.Timedelta("20min")
        tt, ta = t[(t.t >= start) & (t.t <= end)], tally[(tally.t >= start) & (tally.t <= end)]
        ax.step(tt.t, tt.price, where="post", color="k", lw=1.3)
        ax.plot(tt.t, tt.P, color="C3", lw=1.7)
        ax2 = ax.twinx()
        ax2.plot(ta.t, ta.counted, color="0.6", ls=":", lw=1)
        ax2.set_ylim(0, 1), ax2.set_yticks([])
        lead = "" if pd.isna(ev.lead_min) else f"lead {ev.lead_min:+.0f} min"
        if cid == "night_r2_v1":
            lead += ", ~80 from the 1% trade gate"
        won = "won" if ev.outcome == 1 else "lost"
        ax.set_title(f"{label}\n({evid}; YES {won}; {lead})", fontsize=9)
        ax.set_ylim(0, 1.02)
        _fmt(ax)
        ax.xaxis.set_major_locator(mdates.HourLocator())
    axes[0, 0].plot([], [], color="C3", label="model P(YES)")
    axes[0, 0].plot([], [], color="k", label="Polymarket price")
    axes[0, 0].plot([], [], color="0.6", ls=":", label="share counted")
    axes[0, 0].legend(fontsize=8, loc="lower right")
    fig.suptitle("Every replayed race: model vs market through the count (YES = first-round leader / Lula; Brasília time)")
    fig.tight_layout()
    p = FIG / "night_all_races.png"
    fig.savefig(p, dpi=150)
    return p

def fig_capacity() -> Path:
    rows = []
    for cid, label in [("brazil_2026_r1_dev_mun", "1st round (in-sample)"), ("night_r2_v1", "runoff, presidential markets"),
                       ("night_gov_r2_am_v1", "runoff, governor markets"), ("night_gov_r2_es_v1", "runoff, governor markets"),
                       ("night_gov_r2_rn_v1", "runoff, governor markets")]:
        f = RES / cid / ("capacity_favoured_only_true.csv" if cid.startswith("brazil") else "capacity.csv")
        c = pd.read_csv(f)
        rows.append({"night": label, "at signal": c["fill_30m"].sum(), "+2¢": c["fill_30m_slip2c"].sum(),
                     "+5¢": c["fill_30m_slip5c"].sum()})
    d = pd.DataFrame(rows).groupby("night", sort=False).sum()
    fig, ax = plt.subplots(figsize=(9, 4.2))
    x = np.arange(len(d.columns))
    for i, (night, r) in enumerate(d.iterrows()):
        ax.bar(x + (i - 1) * 0.27, r.values, width=0.27, label=night)
        for xi, v in zip(x + (i - 1) * 0.27, r.values):
            ax.text(xi, v + 60, f"${v:,.0f}", ha="center", fontsize=8)
    ax.set_xticks(x, ["at the signal's price", "accepting 2¢ worse", "accepting 5¢ worse"])
    ax.set_ylabel("$ that traded on our side within 30 min")
    ax.set_title("How much could the signals actually have bought? (all signals of the night, summed)")
    ax.legend(fontsize=8.5)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    p = FIG / "night_capacity.png"
    fig.savefig(p, dpi=150)
    return p


def fig_calibration() -> Path:
    """Races × count buckets: share of moments whose 90% interval contains the final result."""
    bins = [0.01, 0.2, 0.5, 0.8, 0.9, 0.95]
    labels = ["1–20%", "20–50%", "50–80%", "80–90%", "90–95%"]
    mat, ns, names = [], [], []
    for cid, label, evid in RACES:
        t, tally, final, ev = _load(cid)
        z = (final - t.share) / t.share_sd
        b = pd.cut(t.counted, bins, labels=labels)
        g = (z.abs() < 1.645).groupby(b, observed=False)
        mat.append(100 * g.mean().reindex(labels).values)
        ns.append(g.size().reindex(labels).values)
        names.append(f"{label} ({evid})")
    m = np.array(mat, dtype=float)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    im = ax.imshow(np.nan_to_num(m, nan=100), cmap="RdYlGn", vmin=50, vmax=100, aspect="auto")
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            txt = "–" if np.isnan(m[i, j]) else f"{m[i, j]:.0f}%\n(n={ns[i][j]})"
            ax.text(j, i, txt, ha="center", va="center", fontsize=7.5)
    ax.set_xticks(range(len(labels)), labels)
    ax.set_yticks(range(len(names)), names, fontsize=8.5)
    ax.set_xlabel("share of sections counted (n = one-minute moments)")
    ax.set_title("Is the model's uncertainty honest? Share of moments whose 90% interval contained the final result\n"
                 "(target 90%; green = at or above)", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.03)
    fig.tight_layout()
    p = FIG / "night_calibration.png"
    fig.savefig(p, dpi=150)
    return p


if __name__ == "__main__":
    if sys.argv[1] == "cache":
        cache(sys.argv[2])
    else:
        FIG.mkdir(parents=True, exist_ok=True)
        for f in (fig_how_it_works, fig_panel, fig_capacity, fig_calibration):
            print(f())
