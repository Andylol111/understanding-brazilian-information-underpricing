"""NZ final-three-week party-vote poll averages and historical poll error (data_public/nz_polls_<year>.csv, Wikipedia
CC BY-SA tables of published polls).

The average is the plain mean of every poll whose fieldwork ended in the 21 days before election day (fieldwork end
<= the day before).  A party enters the average only if at least half of those polls report it (minor parties are
otherwise left to the model's "unlisted" remainder).  Nothing is weighted by pollster, sample size or house effect.

    uv run python analysis/nz_polls.py error            # poll-vs-official final log error for 2017, 2020, 2023
    uv run python analysis/nz_polls.py toml 2026-11-07 [asof]   # a [poll] block for a config (asof = latest fieldwork end)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data_public"
ELECTION = {2017: "2017-09-23", 2020: "2020-10-17", 2023: "2023-10-14", 2026: "2026-11-07"}
# Wikipedia column -> key used by the official count / brunder.newzealand
KEYS = {"MNA": "MANA", "UNF": "UF", "ANZ": "APN", "TTT": "TTTP", "NOFP": "OFP"}
META = {"pollster", "fieldwork_start", "fieldwork_end", "sample_size", "OTH"}


def polls(year: int) -> pd.DataFrame:
    d = pd.read_csv(DATA / f"nz_polls_{year}.csv", comment="#", parse_dates=["fieldwork_start", "fieldwork_end"])
    return d.rename(columns=KEYS)


def window_average(year: int, election: str | None = None, asof: str | None = None, days: int = 21,
                   min_frac: float = 0.5) -> tuple[dict[str, float], pd.DataFrame]:
    """Poll average (fractions) over polls ending in [election-days, election-1d] (and <= ``asof``)."""
    e = pd.Timestamp(election or ELECTION[year])
    d = polls(year)
    m = (d["fieldwork_end"] >= e - pd.Timedelta(days=days)) & (d["fieldwork_end"] < e)
    if asof:
        m &= d["fieldwork_end"] <= pd.Timestamp(asof)
    w = d[m]
    if w.empty:
        raise ValueError(f"no polls for {year} in the window")
    out = {}
    for k in w.columns:
        if k in META or k.startswith("Unnamed"):
            continue
        x = pd.to_numeric(w[k], errors="coerce")
        if x.notna().mean() >= min_frac:
            out[k] = float(x.mean()) / 100
    return out, w


def poll_error(final: dict[str, float], year: int, min_final: float = 0.03) -> dict[str, float]:
    """log(final share / poll average) for parties with at least ``min_final`` of the final party vote."""
    avg, _ = window_average(year)
    return {k: float(np.log(v / avg[k])) for k, v in final.items() if v >= min_final and avg.get(k)}


def toml_block(election: str, asof: str | None, keys: set[str]) -> str:
    avg, w = window_average(int(election[:4]), election, asof)
    lines = [f"# Mean of {len(w)} polls with fieldwork ending {w['fieldwork_end'].min():%Y-%m-%d}..{w['fieldwork_end'].max():%Y-%m-%d}"
             f" (21 days before {election}); {', '.join(sorted(set(w['pollster'])))}.", "[poll]"]
    lines += [f"{k} = {v:.5f}" for k, v in sorted(avg.items()) if k in keys]
    return "\n".join(lines)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "error"
    if cmd == "error":
        import nz_replay as R
        allerr = []
        for y in (2017, 2020, 2023):
            E = R.election(y)
            fv = E.final_party_votes()
            final = {k: float(v) / fv.sum() for k, v in zip(E.party_keys, fv)}
            err = poll_error(final, y)
            allerr += list(err.values())
            print(y, {k: round(v, 3) for k, v in err.items()}, "rms", round(float(np.sqrt(np.mean(np.square(list(err.values()))))), 3))
        print("pooled rms log error (parties >= 3% of final vote):", round(float(np.sqrt(np.mean(np.square(allerr)))), 3))
    elif cmd == "toml":
        from brunder import newzealand as nz
        print(toml_block(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None, set(nz.PARTY_KEYS)))
    else:
        raise SystemExit(f"unknown command {cmd}")
