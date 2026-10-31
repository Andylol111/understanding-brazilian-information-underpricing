"""How far off were last-week poll medians in the three earlier Spanish general elections? (sets precount_sd)

    uv run python analysis/es_poll_error.py

Reads the Wikipedia polling tables (CC BY-SA 4.0; wikitext via the API, 1 request every 2 s), takes the median of the
polls whose fieldwork ended in the last 7 days before each election, and compares it with the official result in the
same table. Prints the log error per party and the root-mean-square over all of them. The 2026 config uses a larger
figure (docs/spain.md) because its polls are 5-7 weeks old.
"""
from __future__ import annotations

import math
import re
import time
import urllib.request

import numpy as np

UA = "brunder/0.1 (research; recorder)"
PAGES = {
    "2023-07-23": ("Opinion_polling_for_the_2023_Spanish_general_election", {"PSOE": 2, "PP": 3, "VOX": 4, "LEFT": 5}),
    "2019-11-10": ("Opinion_polling_for_the_November_2019_Spanish_general_election", {"PSOE": 2, "PP": 3, "VOX": 6, "LEFT": 5}),
    "2019-04-28": ("Opinion_polling_for_the_April_2019_Spanish_general_election", {"PP": 2, "PSOE": 3}),
}
DATE = re.compile(r"\d{1,2} [A-Z][a-z]{2}|\d{1,2}[–-]\d{1,2} [A-Z][a-z]{2}|\d{1,2} [A-Z][a-z]{2}[–-]\d{1,2} [A-Z][a-z]{2}")


def fetch(title: str) -> str:
    url = f"https://en.wikipedia.org/w/index.php?title={title}&action=raw"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status} for {title}")
        text = r.read().decode()
    if "{|" not in text:
        raise RuntimeError(f"{title}: no wikitext table")
    return text


def rows(text: str):
    for r in re.split(r"\n\|-[^\n]*\n", text):
        cells = [re.sub(r"<ref.*?(</ref>|/>)", "", c.strip(), flags=re.S) for c in re.split(r"\n\||\|\|", r) if c.strip()]
        di = next((i for i, c in enumerate(cells) if DATE.fullmatch(c)), None)
        if di is None or len(cells) < 8:
            continue
        nums = []
        for c in cells[di + 1:di + 12]:
            m = re.search(r"(\d{1,2}\.\d)(?!\d)", re.sub(r"\{\{[^}]*\}\}", "", c.split("<br")[0]))
            nums.append(float(m.group(1)) if m else None)
        yield cells[di], nums


def end_day(s: str):
    parts = re.findall(r"(\d{1,2})(?: ([A-Z][a-z]{2}))?", s)
    day, mon = parts[-1]
    return int(day), mon or [m for _, m in parts if m][-1]


def main() -> None:
    errs = []
    for date, (title, lanes) in PAGES.items():
        data = list(rows(fetch(title)))
        time.sleep(2)
        (rd, res), polls = data[0], data[1:]
        d0, m0 = end_day(rd)
        last = [n for d, n in polls if end_day(d)[1] == m0 and 0 <= d0 - end_day(d)[0] <= 7]
        print(date, len(last), "polls in the final week")
        for lane, ix in lanes.items():
            xs = [p[ix] for p in last if p[ix] is not None]
            e = math.log(float(np.median(xs)) / res[ix])
            errs.append(e)
            print(f"  {lane}: median {np.median(xs):.1f} vs result {res[ix]:.1f}  log error {e:+.3f}")
    print(f"RMS log error over {len(errs)} party-elections: {np.sqrt(np.mean(np.square(errs))):.3f}")


if __name__ == "__main__":
    main()
