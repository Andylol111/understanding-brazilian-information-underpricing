"""Fetch historical (2014, 2017, 2020, 2023) Electoral Commission result CSVs from the Internet Archive.

electionresults.govt.nz answers non-browser clients with a Cloudflare 403, which we do not evade. The Internet
Archive holds unmodified captures of the official CSV downloads. Files are written byte-for-byte under
``$NZ_HIST_DIR/<year>/`` (default ~/nz_v3_local/raw, a local disk) and a manifest records each Wayback capture timestamp.

    uv run python analysis/nz_history_fetch.py 2017 2020 2023
"""
from __future__ import annotations

import json
import sys
import time

import httpx

import os
from pathlib import Path

HIST = Path(os.environ.get("NZ_HIST_DIR", Path.home() / "nz_v3_local" / "raw"))

UA = {"User-Agent": "brunder-research/0.1 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}
BASE = "https://web.archive.org/web/2026id_/https://electionresults.govt.nz/electionresults_{y}/statistics/csv/{f}.csv"
SINGLE = ("overall-results-summary", "votes-for-registered-parties-by-electorate", "winning-electorate-candidates",
          "candidate-votes-and-turnout-by-electorate", "party-votes-and-turnout-by-electorate")


def fetch(years: list[int], per_place: bool = True) -> None:
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as c:
        for y in years:
            out = HIST / str(y)
            out.mkdir(parents=True, exist_ok=True)
            man_path = out / "wayback_manifest.json"
            man = json.loads(man_path.read_text()) if man_path.exists() else {}
            names = list(SINGLE)
            if per_place:
                names += [f"{k}-votes-by-voting-place-{i}" for i in range(1, 73) for k in ("party", "candidate")]
            for n in names:
                dest = out / f"{n}.csv"
                if dest.exists() and dest.stat().st_size > 200:
                    continue
                for attempt in range(3):
                    try:
                        r = c.get(BASE.format(y=y, f=n))
                    except httpx.HTTPError:
                        time.sleep(5 * (attempt + 1)); continue
                    if r.status_code == 200 and len(r.content) > 200:
                        dest.write_bytes(r.content)
                        man[n] = {"bytes": len(r.content), "wayback": r.headers.get("x-archive-src", "")}
                        break
                    if r.status_code == 404:
                        man[n] = {"missing": 404}; break
                    time.sleep(5 * (attempt + 1))
                time.sleep(0.7)
            man_path.write_text(json.dumps(man, indent=1, sort_keys=True))
            print(y, "done", sum(1 for v in man.values() if "bytes" in v), "files", flush=True)


if __name__ == "__main__":
    fetch([int(x) for x in sys.argv[1:]] or [2017, 2020, 2023])
