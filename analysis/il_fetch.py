"""Fetch the Israel inputs that were not recorded live (run once; everything lands in data/raw/israel/).

1. Official CEC ballot-box files (expb.csv) for the 23rd-25th Knesset, as captured by the Internet
   Archive's Wayback Machine. The CEC's own results sites (votesNN/mediaNN.bechirot.gov.il) and the
   data.gov.il download host answer 403 from outside Israel, and data.gov.il's robots.txt disallows /api/,
   so the Wayback copies of the CEC's own files are the route used. For 2021 and 2022 every distinct
   capture of the count is kept: that is the official count over time (sparse, ~25 captures each).
   2020 (23rd) is only the prior for the 2021 replay, so only its final file is kept.
2. 2026 count by locality: the commit history of results.json in github.com/navonyaniv/election-il-2026
   (a third-party mirror of the CEC count with no licence: private research only, never published).
   The recorder's earthelects snapshots carry only district totals; this history has the same CEC
   releases by locality. Commit times are kept so availability can be checked against the recording.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_fetch.py [wayback|github|all]
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from brunder.paths import RAW

OUT = RAW / "israel"
UA = {"User-Agent": "brunder-research (github.com/Andylol111/understanding-brazilian-information-underpricing)"}
CDX = "https://web.archive.org/cdx/search/cdx"
PAUSE = 3.0                                                     # seconds between archive downloads

# Knesset -> (CEC media host file, capture window, keep every distinct capture (count) or only the last)
WAYBACK = {
    "k25": ("media25.bechirot.gov.il/files/expb.csv", "20221101200000", "20221110000000", True),
    "k24": ("media24.bechirot.gov.il/files/expb.csv", "20210323200000", "20210405000000", True),
    "k23": ("media23.bechirot.gov.il/files/expb.csv", "20200301000000", "20221231000000", False),
}
REPO = "navonyaniv/election-il-2026"


def _captures(c: httpx.Client, url: str, t0: str, t1: str) -> list[tuple[str, str]]:
    r = c.get(CDX, params={"url": url, "output": "json", "fl": "timestamp,digest", "filter": "statuscode:200",
                           "from": t0, "to": t1}, timeout=180)
    r.raise_for_status()
    rows = r.json()[1:]
    seen, out = set(), []
    for ts, dig in rows:                                         # one file per distinct content
        if dig not in seen:
            seen.add(dig)
            out.append((ts, dig))
    return out


def wayback() -> None:
    with httpx.Client(headers=UA, follow_redirects=True) as c:
        for k, (url, t0, t1, count) in WAYBACK.items():
            d = OUT / "cec_wayback" / k
            d.mkdir(parents=True, exist_ok=True)
            caps = _captures(c, url, t0, t1)
            if not count:
                caps = caps[-1:]                                 # only the final file is needed (prior)
            index = []
            for ts, dig in caps:
                f = d / f"expb_{ts}.csv"
                if not f.exists():
                    r = c.get(f"https://web.archive.org/web/{ts}id_/https://{url}", timeout=300)
                    r.raise_for_status()
                    f.write_bytes(r.content)
                    time.sleep(PAUSE)
                index.append({"timestamp": ts, "digest": dig, "file": f.name, "bytes": f.stat().st_size,
                              "source": f"https://web.archive.org/web/{ts}/https://{url}"})
            (d / "index.json").write_text(json.dumps(index, indent=1))
            print(k, len(index), "files")


def github() -> None:
    d = OUT / "navonyaniv"
    d.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, follow_redirects=True, timeout=60) as c:
        commits, page = [], 1
        while True:
            r = c.get(f"https://api.github.com/repos/{REPO}/commits",
                      params={"path": "results.json", "since": "2026-10-27T18:00:00Z", "per_page": 100, "page": page})
            r.raise_for_status()
            batch = r.json()
            commits += batch
            if len(batch) < 100:
                break
            page += 1
        index = []
        for cm in sorted(commits, key=lambda x: x["commit"]["committer"]["date"]):
            t = cm["commit"]["committer"]["date"]
            f = d / f"{t.replace(':', '').replace('-', '')}_{cm['sha'][:8]}.json"
            if not f.exists():
                r = c.get(f"https://raw.githubusercontent.com/{REPO}/{cm['sha']}/results.json")
                r.raise_for_status()
                f.write_bytes(r.content)
                time.sleep(0.5)
            index.append({"committed": t, "sha": cm["sha"], "message": cm["commit"]["message"], "file": f.name})
        (d / "index.json").write_text(json.dumps(index, indent=1))
        print("navonyaniv", len(index), "commits")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("wayback", "all"):
        wayback()
    if what in ("github", "all"):
        github()
