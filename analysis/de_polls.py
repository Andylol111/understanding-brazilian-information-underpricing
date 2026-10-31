"""German federal election national poll tables (data_public/de_polls_<year>.csv, Wikipedia CC BY-SA tables of
published polls) and the official national second-vote result printed in the same table.

Source: https://en.wikipedia.org/wiki/Opinion_polling_for_the_<year>_German_federal_election (REST HTML, saved under
$BRUNDER_DATA/raw/germany/polls/). Wikipedia text is CC BY-SA 4.0; the polls themselves are published by the polling
firms (Allensbach, Forsa, INSA, Infratest dimap, Forschungsgruppe Wahlen, YouGov, ...). Only the polls of the final 60
days before the election are kept, with the fieldwork dates and the party figures as printed.

The average used by the models is the plain mean of every poll whose fieldwork ended in the 14 days before election day
(fieldwork end <= the day before), parties reported by at least half of those polls, no weighting by firm or sample.

    uv run python analysis/de_polls.py fetch      # download the four pages (>= 1.5 s apart), write the CSVs
    uv run python analysis/de_polls.py error      # poll-average vs official final, log error per party and election
"""
from __future__ import annotations

import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from brunder.paths import RAW

DATA = Path(__file__).resolve().parent.parent / "data_public"
RAW_POLLS = RAW / "germany" / "polls"
ELECTION = {2013: "2013-09-22", 2017: "2017-09-24", 2021: "2021-09-26", 2025: "2025-02-23"}
UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
# Wikipedia column -> model party
COLS = {"Union": "Union", "AfD": "AfD", "SPD": "SPD", "Grüne": "GRÜNE", "Linke": "Die Linke", "FDP": "FDP",
        "BSW": "BSW"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"])}


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("style", "sup") and self._cell is not None:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "sup") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self.tables and self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def _value(s: str) -> float:
    s = re.sub(r"\[.*?\]", "", s).replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _dates(s: str, year_hint: int) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    s = re.sub(r"\[.*?\]", "", s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")).strip()
    days = [int(x) for x in re.findall(r"\b(\d{1,2})\b(?!\d)", re.sub(r"\b\d{4}\b", "", s))]
    mons = [MONTHS[m[:3].lower()] for m in re.findall(r"[A-Za-z]{3,9}", s) if m[:3].lower() in MONTHS]
    yr = re.findall(r"\b(20\d\d|19\d\d)\b", s)
    if not days or not mons:
        return None
    y = int(yr[-1]) if yr else year_hint
    end = pd.Timestamp(year=y, month=mons[-1], day=days[-1])
    sm = mons[0] if len(mons) > 1 else mons[-1]
    sy = y - 1 if sm > mons[-1] else y
    try:
        start = pd.Timestamp(year=sy, month=sm, day=days[0])
    except ValueError:
        start = end
    return start, end


def parse_page(html: str, year: int) -> tuple[pd.DataFrame, dict[str, float]]:
    """(polls of the final 60 days, official result row) from the first poll table of a Wikipedia page."""
    p = _Tables()
    p.feed(html)
    tab = next(t for t in p.tables if t and any(c in ("Polling firm", "Polling Firm") for c in t[0]))
    head = tab[0]
    ei = pd.Timestamp(ELECTION[year])
    polls, result = [], {}
    for r in tab[1:]:
        if len(r) != len(head) or not any(r):
            continue
        row = dict(zip(head, r))
        firm = row.get("Polling firm") or row.get("Polling Firm")
        date = row.get("Fieldwork date") or row.get("Date")
        vals = {COLS[c]: _value(row[c]) for c in head if c in COLS}
        oth = _value(row.get("Others", ""))
        if re.search(r"election|result", f"{firm} {date}", re.I):
            if not result:                                   # the first such row is this election's (older ones follow)
                result = {**vals, "Übrige": oth, "date": str(date)}
            continue
        d = _dates(date, year)
        if d is None:
            continue
        if d[1] < ei - pd.Timedelta(days=60) or d[1] >= ei:
            continue
        polls.append({"pollster": re.sub(r"\[.*?\]", "", firm).strip(), "fieldwork_start": d[0].date(),
                      "fieldwork_end": d[1].date(), "sample_size": row.get("Samplesize", ""), **vals, "OTH": oth})
    return pd.DataFrame(polls), result


def fetch() -> None:
    RAW_POLLS.mkdir(parents=True, exist_ok=True)
    res = {}
    with httpx.Client(headers=UA, timeout=120) as c:
        for y in ELECTION:
            f = RAW_POLLS / f"poll_{y}.html"
            if not f.exists():
                r = c.get(f"https://en.wikipedia.org/api/rest_v1/page/html/Opinion_polling_for_the_{y}_German_federal_election")
                r.raise_for_status()
                f.write_bytes(r.content)
                time.sleep(1.5)
            d, result = parse_page(f.read_text(encoding="utf-8"), y)
            d.to_csv(DATA / f"de_polls_{y}.csv", index=False)
            res[y] = result
            print(y, len(d), "polls in the final 60 days; result row", result)
    rows = [{"year": y, "election": ELECTION[y], **{k: v for k, v in r.items() if k != "date"}} for y, r in res.items()]
    pd.DataFrame(rows).to_csv(DATA / "de_poll_results.csv", index=False)


def polls(year: int) -> pd.DataFrame:
    return pd.read_csv(DATA / f"de_polls_{year}.csv", parse_dates=["fieldwork_start", "fieldwork_end"])


def window_average(year: int, days: int = 14, min_frac: float = 0.5) -> tuple[dict[str, float], pd.DataFrame]:
    """Poll average (fractions) over polls ending in [election-days, election-1d]; 'Übrige' = 1 - listed parties."""
    e = pd.Timestamp(ELECTION[year])
    d = polls(year)
    w = d[(d["fieldwork_end"] >= e - pd.Timedelta(days=days)) & (d["fieldwork_end"] < e)]
    if w.empty:
        raise ValueError(f"no polls for {year} in the window")
    out = {}
    for k in COLS.values():
        if k in w and w[k].notna().mean() >= min_frac:
            out[k] = float(w[k].mean()) / 100
    out["Übrige"] = 1.0 - sum(out.values())
    return out, w


def official(year: int) -> dict[str, float]:
    r = pd.read_csv(DATA / "de_poll_results.csv").set_index("year").loc[year]
    out = {k: float(r[k]) / 100 for k in COLS.values() if k in r and pd.notna(r[k])}
    out["Übrige"] = 1.0 - sum(out.values())
    return out


def poll_errors(years=(2013, 2017, 2021)) -> pd.DataFrame:
    """log(official final / poll average) per party and election; parties with >= 3% of the official vote."""
    rows = []
    for y in years:
        avg, _ = window_average(y)
        fin = official(y)
        for k, v in fin.items():
            if k != "Übrige" and v >= 0.03 and avg.get(k):
                rows.append({"year": y, "party": k, "poll": avg[k], "final": v, "log_err": float(np.log(v / avg[k]))})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch()
    else:
        e = poll_errors()
        print(e.round(4).to_string())
        print("RMS log error", float(np.sqrt((e["log_err"] ** 2).mean())), "n", len(e))
