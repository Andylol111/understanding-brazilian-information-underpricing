"""Builds site/data/us2026_guide/watch.json for site/guide.html: the counties to watch first in each statewide race.

  uv run python analysis/us_guide_data.py

Input (read only): data_public/us2026_benchmarks.csv (analysis/us_benchmarks.py, frozen 2026-11-02; method in
docs/us_benchmarks.md). Per Senate and governor race the counties flagged tell_top (the five early-reporting counties whose result
moves the statewide projection most), best first by tell_rank, with the expected two-party Democratic share (s0), its 5-95% band
(band_lo / band_hi, if the statewide result is on benchmark) and the expected votes (V). Nothing is computed from 2026 count data.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data_public" / "us2026_benchmarks.csv"
OUT = ROOT / "site" / "data" / "us2026_guide" / "watch.json"
TOP = 3


def build() -> dict:
    d = date.fromisoformat(re.search(r"frozen (\d{4}-\d{2}-\d{2})", SRC.read_text().splitlines()[0]).group(1))   # the file's own header
    df = pd.read_csv(SRC, comment="#")
    df = df[df["office"].isin(["sen", "gov"]) & (df["tell_top"] == 1)].sort_values(["race_id", "tell_rank"])
    races = {}
    for race, g in df.groupby("race_id", sort=True):
        races[race] = [
            {"name": r["name"], "s0": round(float(r["s0"]), 4), "lo": round(float(r["band_lo"]), 4), "hi": round(float(r["band_hi"]), 4),
             "v": int(round(float(r["V"])))}
            for _, r in g.head(TOP).iterrows()
        ]
    return {
        "frozen": f"{d.day} {d:%B}",
        "source": f"data_public/us2026_benchmarks.csv (analysis/us_benchmarks.py, frozen {d.isoformat()})",
        "note": "Counties expected to report early whose result moves the statewide projection most (tell_top), best first. s0 = expected two-party Democratic share, lo-hi = its 5-95% band if the statewide result is on benchmark, v = expected votes.",
        "races": races,
    }


if __name__ == "__main__":
    out = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False) + "\n")
    print(f"{len(out['races'])} races -> {OUT}")
