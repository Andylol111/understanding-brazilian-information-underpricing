"""Forest plots of every pre-registered test: estimate and 95% CI per category, discovery vs holdout.

Run after `brunder study … --split discovery` and `--split holdout` for each spec.
Writes results/studies/fig_<spec>.png.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from brunder.paths import ROOT

STUDIES = ROOT / "results" / "studies"
LABELS = {"deep_continuation": "drift after shock, deep markets (s·(y − p))",
          "thin_reversal": "drift after shock, thin markets (s·(y − p))",
          "favourite_slope": "slope of (y − p) on (p − ½)",
          "net_return": "net return per $ (after spread + fee)"}


def main() -> None:
    plt.rcParams.update({"figure.dpi": 150, "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    for spec_dir in sorted(p for p in STUDIES.iterdir() if p.is_dir()):
        runs = {s: pd.read_csv(spec_dir / s / "primary.csv") for s in ("discovery", "holdout")
                if (spec_dir / s / "primary.csv").exists()}
        if not runs:
            continue
        tests = sorted(set().union(*(r["test"] for r in runs.values())))
        fig, axes = plt.subplots(1, len(tests), figsize=(5.5 * len(tests), 5), squeeze=False)
        for ax, test in zip(axes[0], tests):
            cats = sorted(set().union(*(r.loc[r["test"] == test, "category"] for r in runs.values())))
            y = np.arange(len(cats))
            for k, (split, r) in enumerate(runs.items()):
                d = r[(r["test"] == test) & (r["status"] == "tested")].set_index("category").reindex(cats)
                off = -0.15 if split == "discovery" else 0.15
                sig = d["significant"].fillna(False).astype(bool)
                ax.errorbar(d["estimate"], y + off, xerr=[d["estimate"] - d["lo95"], d["hi95"] - d["estimate"]],
                            fmt="none", ecolor=f"C{k}", alpha=0.7)
                ax.scatter(d["estimate"][~sig], (y + off)[~sig.to_numpy()], color=f"C{k}", facecolors="none",
                           label=f"{split}")
                ax.scatter(d["estimate"][sig], (y + off)[sig.to_numpy()], color=f"C{k}",
                           label=f"{split}, Holm p < α")
            ax.axvline(0, color="k", lw=0.6)
            ax.set_yticks(y, cats)
            ax.set_xlabel(LABELS.get(test, test))
            ax.set_title(test)
        axes[0][0].legend(fontsize=7, loc="lower right")
        fig.suptitle(f"{spec_dir.name}: estimate and event-cluster 95% CI by category")
        fig.tight_layout()
        fig.savefig(STUDIES / f"fig_{spec_dir.name}.png")
        print(STUDIES / f"fig_{spec_dir.name}.png")


if __name__ == "__main__":
    main()
