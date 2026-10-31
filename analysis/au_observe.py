"""Reduce the AEC election-night archive of one federal election to per-division statistics, one row per feed frame.

Reads the zips fetched by analysis/au_fetch.py ($BRUNDER_DATA/au/<event>/Detailed-Light/, Detailed-Preload/), the
previous election's final polling-place results (the last Detailed-Light file of the previous event) and writes
$BRUNDER_DATA/au/obs_<year>.pkl: {"static": ..., "final": ..., "obs": [(created_utc, {division: stats})]}.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/au_observe.py 2025
"""
from __future__ import annotations

import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

from brunder import australia as au
from brunder.paths import DATA

AU = DATA / "au"
# year -> (event id, previous year, first and last feed stamp of the count night, Canberra time)
ELECTIONS = {
    2025: (31496, 2022, "20250503165000", "20250504040000"),
    2022: (27966, 2019, "20220521165000", "20220522040000"),
    2019: (24310, 2016, "20190518165000", "20190519040000"),
    2016: (20499, None, "20160702165000", "20160703040000"),
}
_S = None


def files(event: int, kind: str = "Detailed-Light") -> list:
    return sorted((AU / str(event) / kind).glob("*.zip"))


def stamp(p) -> str:
    return au.STAMP.search(p.name).group(1)


def preload(event: int) -> dict:
    return au.parse_preload(au.read_zip(files(event, "Detailed-Preload")[-1]))


def final_frame(event: int) -> dict:
    return au.parse_results(au.read_zip(files(event)[-1]))


def _init(path: str) -> None:
    global _S
    with open(path, "rb") as f:
        _S = pickle.load(f)


def _one(path: str):
    fr = au.parse_results(au.read_zip(path))
    return fr["created"], au.observe(fr, _S)


def build(year: int) -> dict:
    event, prev, t0, t1 = ELECTIONS[year]
    pre = preload(event)
    prev_booths = {}
    if prev:
        pe = ELECTIONS[prev][0]
        prev_booths = au.final_booths(final_frame(pe), preload(pe))
    S = au.static_tables(pre, prev_booths)
    fin = au.final_divisions(final_frame(event), pre)
    sp = AU / f"static_{year}.pkl"
    with open(sp, "wb") as f:
        pickle.dump(S, f)
    todo = [str(p) for p in files(event) if t0 <= stamp(p) <= t1]
    print(year, len(todo), "frames", flush=True)
    with ProcessPoolExecutor(8, initializer=_init, initargs=(str(sp),)) as ex:
        obs = list(ex.map(_one, todo, chunksize=2))
    obs.sort(key=lambda x: x[0])
    out = {"static": {k: S[k] for k in ("cands", "divs", "grp", "incumbent", "booth_hist")} | {"booths": S["booths"]},
           "final": fin, "obs": obs, "prev_booths": len(prev_booths)}
    with open(AU / f"obs_{year}.pkl", "wb") as f:
        pickle.dump(out, f)
    print("wrote", AU / f"obs_{year}.pkl", len(obs))
    return out


if __name__ == "__main__":
    build(int(sys.argv[1]))
