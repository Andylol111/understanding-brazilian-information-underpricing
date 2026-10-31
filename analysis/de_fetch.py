"""Fetch the German Bundestag election 2025 (23 Feb 2025) data for the replay.

* `open`     official open data (Bundeswahlleiterin, Datenlizenz Deutschland - Namensnennung 2.0): the final
             results by Wahlkreis (kerg2.csv), Wahlkreis names, the 2021 result on the 2025 constituencies
             (btwkr25_umrechnung_btw21.csv), party list.
* `wayback`  the election-night count. The results pages are server-rendered HTML with an "Zwischenergebnis:
             <date time>, <n> von 299 Wahlkreisen ausgezählt" caption; the Internet Archive holds several
             captures of the national page (bund-99), of the 16 Land pages and of single Wahlkreis pages
             (German and English). All captures between 2025-02-23 17:00 and 2025-02-24 06:00 UTC are saved.
* `markets`  Polymarket events (Germany 2025), 1-minute YES prices, count window only.
* `kalshi`   Kalshi 'most seats' / '2nd place' markets, 1-minute candles (the only Kalshi markets open through the count).

The bundeswahlleiterin.de robots.txt answers 404 (no restrictions); requests are sequential, >= 1.1 s apart, with
an honest User-Agent, also to web.archive.org.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/de_fetch.py open|wayback|markets|kalshi
"""

from __future__ import annotations

import json
import re
import sys
import time

import httpx
import pandas as pd

from brunder import polymarket as pm
from brunder.paths import POLYMARKET, RAW

UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
RAW_DE = RAW / "germany"
SITE = "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse"
DAM = "https://www.bundeswahlleiterin.de/dam/jcr/"
OPEN = {
    "kerg2.csv": f"{SITE}/opendata/btw25/csv/kerg2.csv",
    "kerg.csv": f"{SITE}/opendata/btw25/csv/kerg.csv",
    "btw25_wahlkreisnamen_utf8.csv": DAM + "17e066f6-a0af-42df-a5d2-365dc87769ab/btw25_wahlkreisnamen_utf8.csv",
    "btwkr25_umrechnung_btw21.csv": DAM + "201df610-802b-4121-9e6b-5d04504df5c8/btwkr25_umrechnung_btw21.csv",
    "btw25_parteien.csv": DAM + "925d6e98-3616-465a-835a-cec1ea73abc2/btw25_parteien.csv",
    "opendata.html": f"{SITE}/opendata.html",
}
CDX = "https://web.archive.org/cdx/search/cdx"
FROM, TO = "20250223170000", "20250224060000"
PAUSE = 1.1
_last = [0.0]


def get(c: httpx.Client, url: str, **kw) -> httpx.Response:
    for attempt in range(6):
        wait = PAUSE - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = c.get(url, **kw)
        except httpx.TransportError:
            _last[0] = time.monotonic()
            time.sleep(5 * (attempt + 1))
            continue
        _last[0] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        return r
    raise RuntimeError(f"giving up on {url}")


def fetch_open(c: httpx.Client) -> None:
    out = RAW_DE / "open"
    out.mkdir(parents=True, exist_ok=True)
    for name, url in OPEN.items():
        if not (out / name).exists():
            r = get(c, url)
            r.raise_for_status()
            (out / name).write_bytes(r.content)
            print("open", name, len(r.content), flush=True)


def kind_of(url: str, year: int = 2025) -> tuple[str, str] | None:
    """(lang, unit) of a results page URL: unit is 'bund', 'land-N' or 'wahlkreis-N'; None for anything else."""
    m = re.match(rf"https?://(?:www\.)?bundeswahlleiter(?:in)?\.de/(en/)?bundestagswahlen/{year}/ergebnisse/bund-99"
                 r"(?:/land-(\d+))?(?:/wahlkreis-(\d+))?\.html(?:\?.*)?$", url)
    if not m:
        return None
    unit = f"wahlkreis-{m[3]}" if m[3] else f"land-{m[2]}" if m[2] else "bund"
    return ("en" if m[1] else "de", unit)


WINDOW = {2025: ("20250223170000", "20250224060000"), 2021: ("20210926160000", "20210927060000")}
HOSTS = {2025: "bundeswahlleiterin.de", 2021: "bundeswahlleiter.de"}


def captures(c: httpx.Client, year: int = 2025) -> list[tuple[str, str, str, str, str]]:
    """(timestamp, lang, unit, original url, digest) of every HTTP-200 capture of a results page in the night."""
    rows: dict[tuple[str, str, str], tuple] = {}
    lo, hi = WINDOW[year]
    for lang, pre in (("de", f"{HOSTS[year]}/bundestagswahlen/{year}/ergebnisse/bund-99"),
                      ("en", f"{HOSTS[year]}/en/bundestagswahlen/{year}/ergebnisse/bund-99")):
        cache = RAW_DE / (f"cdx_{lang}.json" if year == 2025 else f"cdx{year}_{lang}.json")
        if not cache.exists():
            r = get(c, CDX, params={"url": pre, "matchType": "prefix", "from": lo, "to": hi, "output": "json",
                                    "fl": "timestamp,original,statuscode,digest", "limit": "20000"}, timeout=300)
            r.raise_for_status()
            cache.write_bytes(r.content)
        for ts, orig, st, dg in json.loads(cache.read_text())[1:]:
            k = kind_of(orig, year)
            if k and st in ("200", "-"):
                rows[(ts, *k)] = (ts, k[0], k[1], orig, dg)
    return sorted(rows.values())


def wayback(c: httpx.Client, year: int = 2025) -> None:
    out = RAW_DE / ("wayback" if year == 2025 else f"wayback{year}")
    out.mkdir(parents=True, exist_ok=True)
    caps = captures(c, year)
    (RAW_DE / ("captures.json" if year == 2025 else f"captures{year}.json")).write_text(json.dumps(caps))
    print(len(caps), "captures", flush=True)
    for ts, lang, unit, orig, _ in caps:
        f = out / f"{ts}_{lang}_{unit}.html"
        if f.exists() and f.stat().st_size > 0:
            continue
        r = get(c, f"https://web.archive.org/web/{ts}id_/{orig}")
        if r.status_code != 200:
            print("skip", ts, unit, r.status_code, flush=True)
            continue
        f.write_bytes(r.content)
    print("done", len(list(out.glob("*.html"))), flush=True)


def fetch_open2021(c: httpx.Client) -> None:
    """2021 final result by Wahlkreis (kerg2.csv, with the 2017 result on the 2021 constituencies as 'Vorperiode')
    from the Internet Archive (the live site no longer serves it)."""
    out = RAW_DE / "open2021"
    out.mkdir(parents=True, exist_ok=True)
    f = out / "kerg2.csv"
    if not f.exists():
        r = get(c, "https://web.archive.org/web/20210927161053id_/https://www.bundeswahlleiter.de/bundestagswahlen/"
                   "2021/ergebnisse/opendata/csv/kerg2.csv")
        r.raise_for_status()
        f.write_bytes(r.content)
        print("open2021 kerg2.csv", len(r.content))


EVENTS = [
    "germany-parliamentary-election", "which-party-wins-2nd-most-seats-in-german-election",
    "bsw-wins-5-or-more-of-vote-in-german-election", "fdp-wins-5-or-more-of-vote-in-german-election",
    "will-the-left-wins-5-or-more-of-vote-in-german-election", "will-the-left-make-it-into-the-bundestag-as-a-fraktion",
    "cducsu-of-vote-in-german-election", "cducsu-of-vote-in-german-election-2-brackets",
    "afd-of-vote-in-german-election", "afd-of-vote-in-german-election-2-brackets", "spd-of-vote-in-german-election",
    "will-the-greens-win-over-15-of-the-vote-in-the-german-election", "next-chancellor-of-germany",
    "which-coalition-will-form-the-next-german-government",
]
PRICE_START, PRICE_END = pd.Timestamp("2025-02-23 14:00", tz="UTC"), pd.Timestamp("2025-02-24 12:00", tz="UTC")


def markets() -> None:
    for slug in EVENTS:
        out = POLYMARKET / slug
        (out / "prices").mkdir(parents=True, exist_ok=True)
        ev = pm.event(slug)
        (out / "event.json").write_text(json.dumps(ev))
        mk = pm.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for m in mk.itertuples():
            f = out / "prices" / f"{m.slug}.parquet"
            if not f.exists():
                pm.price_history(m.yes_token, PRICE_START, PRICE_END, 1, chunk=pd.Timedelta(days=1)).to_parquet(f)
        print(slug, len(mk), flush=True)


KALSHI = [("KXGERMANYPARTY", "KXGERMANYPARTY-25-CDU"), ("KXGERMANYPARTY", "KXGERMANYPARTY-25-AFD"),
          ("KXGER2ND", "KXGER2ND-25-AFD")]


def kalshi_markets() -> None:
    """1-minute Kalshi candles of the German-election 'most seats' and '2nd place' markets that traded through the
    count (the 'parties in parliament' markets closed 15:00 UTC, before polls closed). Stored under
    $BRUNDER_DATA/raw/kalshi/germany/ (never published raw; docs/kalshi.md)."""
    from brunder import kalshi

    out = RAW / "kalshi" / "germany"
    out.mkdir(parents=True, exist_ok=True)
    for series, ticker in KALSHI:
        f = out / f"{ticker}.parquet"
        if not f.exists():
            kalshi.candlesticks(series, ticker, PRICE_START, PRICE_END, 1).to_parquet(f)
        print(ticker, len(pd.read_parquet(f)), flush=True)


def main(what: str) -> None:
    if what == "markets":
        markets()
        return
    if what == "kalshi":
        kalshi_markets()
        return
    with httpx.Client(headers=UA, timeout=90, follow_redirects=True) as c:
        {"open": fetch_open, "wayback": wayback, "open2021": fetch_open2021,
         "wayback2021": lambda cl: wayback(cl, 2021)}[what](c)
    print(RAW_DE)


if __name__ == "__main__":
    main(sys.argv[1])
