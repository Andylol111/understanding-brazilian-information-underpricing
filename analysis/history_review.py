"""Model-only review of a past count (no market prices survive): how early and how accurately the
count model reads an election it was never developed on.

    uv run python analysis/history_review.py configs/history/hist_2022_r2_v1.toml

Registered configs only; each run is logged as a look (prereg/holdout_runs.jsonl). Rebuilds the count
minute by minute from BU-web receipt times exactly as night.replay does (same partials, same model),
and reports the primary results fixed in the config header:
  (1) lead of the model settling >= 0.9 on the eventual winner over the raw running tally settling on
      the eventual winner for good;
  (2) share of one-minute moments between 1% and 95% counted whose 90% interval contains the final
      two-party share (also split at 90% counted).
Also: projection error vs raw-tally error at 1/5/10/25% counted. Writes results/history/<id>/.
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

from brunder import bweb, night, registry
from brunder.paths import ROOT

BRT = "America/Sao_Paulo"


def settle(ts: pd.Series, ok: pd.Series):
    """First time after which `ok` holds to the end."""
    bad = ts[~ok.values]
    if bad.empty:
        return ts.iloc[0]
    after = ts[ts > bad.iloc[-1]]
    return after.iloc[0] if len(after) else pd.NaT


def main(cfg_path: str) -> None:
    cfg = night.load_config(Path(cfg_path))
    if "id" in cfg:                       # registered: log the look (an unregistered copy is a dry run)
        look = registry.open_holdout(registry.load_spec(Path(cfg_path)))
        print(f"registered test {cfg['id']}, look #{look['look']}")
    E, R = cfg["election"], cfg["replay"]
    out = ROOT / "results" / "history" / cfg["_id"]
    out.mkdir(parents=True, exist_ok=True)

    pri, p = night.prior_mun(cfg), night.params_mun(cfg)
    raw = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"))
    raw["visible"] = (raw["received"] + pd.Timedelta("3h")).dt.tz_localize("UTC")
    raw["n"] = raw["a"] + raw["b"]
    totals = raw.groupby(["uf", "cd_mun"]).size()
    final = float(raw["a"].sum() / raw["n"].sum())
    times = pd.date_range(raw["visible"].min().floor("1min"), raw["visible"].max().ceil("1min"), freq="1min")
    srt = raw.sort_values("visible")
    cum = srt.assign(k=np.arange(1, len(srt) + 1), A=srt["a"].cumsum(), N=srt["n"].cumsum()).groupby("visible")[["k", "A", "N"]].last()
    rows = []
    for t in times:
        pr = night.model_probs_mun(night.replay_partials_mun(raw, t, totals), pri, p, ["br"])
        c = cum[:t]
        k, A, N = (c.iloc[-1].values if len(c) else (0, 0, 0))
        rows.append({"t": t, "P": pr["br"], "share": pr["_share"], "sd": pr["_sd"], "counted": k / len(raw),
                     "tally": A / N if N else np.nan})
    d = pd.DataFrame(rows)
    d.to_parquet(out / "ticks.parquet")

    win_a = final > 0.5
    p_win = d["P"] if win_a else 1 - d["P"]
    tally_win = (d["tally"] > 0.5) if win_a else (d["tally"] < 0.5)
    started = d["counted"] > 0
    t_model = settle(d["t"], (p_win >= 0.9) | ~started)
    t_tally = settle(d.loc[started, "t"], tally_win[started].fillna(False))
    lead = (t_tally - t_model).total_seconds() / 60 if pd.notna(t_model) and pd.notna(t_tally) else np.nan
    z = (final - d["share"]) / d["sd"]
    w = d["counted"].between(0.01, 0.95, inclusive="right")
    cov = {lab: round(float((z[w & m].abs() < 1.645).mean()), 3) for lab, m in
           (("1-95%", w), ("1-90%", d["counted"] <= 0.90), ("90-95%", d["counted"] > 0.90))}
    errs = {}
    for q in (0.01, 0.05, 0.10, 0.25):
        r = d[d["counted"] >= q].iloc[0]
        errs[f"{int(q * 100)}%"] = {"model_pt": round(100 * (r.share - final), 2), "raw_tally_pt": round(100 * (r.tally - final), 2)}
    brt = lambda x: x.tz_convert(BRT).strftime("%H:%M") if pd.notna(x) else None
    summary = {"id": cfg.get("id", cfg["_id"] + " (dry run)"), "final_share_a": round(final, 4), "winner": E["cand_a"] if win_a else E.get("cand_b"),
               "model_settles": brt(t_model), "raw_tally_settles": brt(t_tally), "lead_min": lead,
               "coverage_90": cov, "mean_z_1_95": round(float(z[w].mean()), 2), "error_at_counted": errs,
               "sections": int(len(raw))}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))

    tt = d.assign(t=d["t"].dt.tz_convert(BRT).dt.tz_localize(None))
    tt = tt[tt["counted"] > 0]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)
    a1.plot(tt.t, 100 * tt.tally, color="0.35", label="raw running tally")
    a1.plot(tt.t, 100 * tt.share, color="C3", label="model's projected final share")
    a1.fill_between(tt.t, 100 * (tt.share - 1.645 * tt.sd), 100 * (tt.share + 1.645 * tt.sd), color="C3", alpha=0.15)
    a1.axhline(100 * final, color="k", ls="--", lw=1, label=f"final {100 * final:.2f}%")
    a1.axhline(50, color="0.6", lw=0.8)
    a1.set_ylim(100 * final - 6, 100 * final + 6)
    a1.set_ylabel(f"candidate {E['cand_a']}, % two-party")
    a1.legend(fontsize=8)
    a1.set_title(f"{summary['id']} (model only, pre-specified): model {brt(t_model)}, raw tally {brt(t_tally)}, lead {lead:+.0f} min")
    a2.plot(tt.t, tt.P, color="C3", label=f"model P(candidate {E['cand_a']} wins)")
    a2.plot(tt.t, tt.counted, color="0.6", ls=":", label="share counted")
    a2.set_ylim(0, 1.02)
    a2.legend(fontsize=8)
    for ax in (a1, a2):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.grid(alpha=0.25)
    a2.set_xlabel("Brasília time")
    fig.tight_layout()
    fig.savefig(out / "night.png", dpi=150)


if __name__ == "__main__":
    main(sys.argv[1])
