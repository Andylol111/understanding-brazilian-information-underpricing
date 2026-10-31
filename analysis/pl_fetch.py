"""Fetch the Polish presidential-election 2025 count history (PKW, via the Internet Archive) and the final results,
and the Polymarket price history.

The PKW results site (https://wybory.gov.pl/prezydent2025, now https://prezydent2025.pkw.gov.pl/prezydent2025) is a
single-page app. It reads gzip-compressed protobuf files from /prezydent2025/data/:
  config.blob                       current data versions
  obkw/ (round 1), obkw2/ (runoff):
    pl_po_woj.<epoch>.blob          the whole country by voivodeship (+ abroad, ships): obwody counted, valid votes and
                                    votes per candidate, plus votes by gmina-size class. <epoch> = the version (unix
                                    seconds, the moment PKW generated it); a new version every ~4 minutes on the night.
The live server no longer serves old versions (it answers every unknown path with the app shell), so the night's
count history is whatever the Internet Archive captured: `cdx` lists the captures, `blobs` downloads them.
`final` downloads the official final results by gmina (2020 and 2025, both rounds) from the live PKW sites;
`markets` archives the Polymarket events, `kalshi` the Kalshi ones (1-minute candles).

robots.txt: wybory.gov.pl and prezydent2025.pkw.gov.pl answer /robots.txt with a redirect to the home page (no
robots file). Requests are sequential, >= 1.1 s apart, honest User-Agent, web.archive.org CDX + id_ snapshots only.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_fetch.py cdx|blobs|final|markets|kalshi
"""

from __future__ import annotations

import gzip
import json
import sys
import time

import httpx
import pandas as pd

from brunder import kalshi
from brunder import polymarket as pm
from brunder.paths import DATA, POLYMARKET

UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
PAUSE = 1.1
RAW = DATA / "raw" / "poland"
CDX = "https://web.archive.org/cdx/search/cdx"
SITE = "wybory.gov.pl/prezydent2025"
ROUNDS = {1: ("20250518", "20250520", "obkw"), 2: ("20250601", "20250603", "obkw2")}
FINAL = {
    "pl2020r1_gminy.zip": "https://prezydent20200628.pkw.gov.pl/prezydent20200628/data/1/csv/wyniki_gl_na_kand_po_gminach_csv.zip",
    "pl2020r2_gminy.zip": "https://prezydent20200628.pkw.gov.pl/prezydent20200628/data/2/csv/wyniki_gl_na_kand_po_gminach_csv.zip",
    "pl2025r1_gminy.zip": "https://prezydent2025.pkw.gov.pl/prezydent2025/data/csv/wyniki_gl_na_kandydatow_po_gminach_csv.1758650309.zip",
    "pl2025r2_gminy.zip": "https://prezydent2025.pkw.gov.pl/prezydent2025/data/csv/wyniki_gl_na_kandydatow_po_gminach_w_drugiej_turze_csv.1758650309.zip",
}
EVENTS = {  # slug -> (price windows start/end UTC, each <= 3 days = one request; markets kept (None = all))
    "poland-presidential-election": ([("2025-05-31T04:00:00Z", "2025-06-02T12:00:00Z")],
                                     ["will-karol-nawrocki-be-the-next-president-of-poland",
                                      "will-rafa-trzaskowski-be-the-next-president-of-poland"]),
    "poland-presidential-election-margin-of-victory": ([("2025-05-31T04:00:00Z", "2025-06-02T12:00:00Z")], None),
    "poland-presidential-election-1st-round-winner": ([("2025-05-18T04:00:00Z", "2025-05-19T12:00:00Z")], None),
}

KALSHI_EVENTS = {  # event ticker -> (window start, end UTC)
    "KXPRESPOLAND": ("2025-05-31T04:00:00Z", "2025-06-02T12:00:00Z"),
    "KXPOLANDMOV-25": ("2025-05-31T04:00:00Z", "2025-06-02T12:00:00Z"),
    "KXPOLANDROUND1-25": ("2025-05-18T04:00:00Z", "2025-05-19T12:00:00Z"),
}

_last = [0.0]


def _get(c: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    for attempt in range(6):
        wait = PAUSE - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = c.get(url, params=params)
        except httpx.TransportError:
            _last[0] = time.monotonic()
            time.sleep(2**attempt)
            continue
        _last[0] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(2 ** (attempt + 1), 60))
            continue
        return r
    raise RuntimeError(f"giving up on {url}")


def cdx() -> None:
    """Captures of the site on the two count days (statuscode 200), cached as cdx_wybory_r<round>.json."""
    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=280, follow_redirects=True) as c:
        for rnd, (a, b, _d) in ROUNDS.items():
            r = _get(c, CDX, {"url": f"{SITE}*", "from": a, "to": b, "output": "json", "limit": 20000,
                              "fl": "timestamp,original,mimetype,statuscode,digest"})
            r.raise_for_status()
            (RAW / f"cdx_wybory_r{rnd}.json").write_text(r.text)
            print(rnd, len(r.json()) - 1, "captures")


def versions(rnd: int) -> pd.DataFrame:
    """pl_po_woj versions with their Internet Archive capture: ver (epoch), capture timestamp, original URL."""
    rows = json.loads((RAW / f"cdx_wybory_r{rnd}.json").read_text())
    d = ROUNDS[rnd][2]
    out = {}
    for ts, url, _mt, st, _dg in rows[1:]:
        if st != "200" or f"/data/{d}/pl_po_woj." not in url:
            continue
        v = int(url.rsplit("pl_po_woj.", 1)[1].split(".")[0])
        out.setdefault(v, (ts, url))
    return pd.DataFrame([{"ver": v, "capture": t, "url": u} for v, (t, u) in sorted(out.items())])


def blobs() -> None:
    with httpx.Client(headers=UA, timeout=90, follow_redirects=True) as c:
        for rnd in ROUNDS:
            out = RAW / "blobs" / f"r{rnd}"
            out.mkdir(parents=True, exist_ok=True)
            extra = []
            for _, v in versions(rnd).iterrows():
                f = out / f"pl_po_woj.{v.ver}.blob"
                if f.exists() and f.stat().st_size > 0:
                    continue
                r = _get(c, f"https://web.archive.org/web/{v.capture}id_/{v.url}")
                if r.status_code != 200 or r.content[:5] in (b"<!DOC", b"<html"):
                    print("skip", rnd, v.ver, r.status_code)
                    continue
                f.write_bytes(gzip.compress(r.content) if r.content[:2] != b"\x1f\x8b" else r.content)   # httpx may have unzipped it
                print(rnd, v.ver, len(r.content), flush=True)
            del extra
        # candidate lists (names and ids) as the site loaded them
        for rnd, name in ((1, "komitety"), (2, "komitety2")):
            rows = json.loads((RAW / f"cdx_wybory_r{rnd}.json").read_text())[1:]
            cand = [(ts, u) for ts, u, _m, st, _d in rows if st == "200" and f"/data/{name}." in u]
            if cand:
                ts, u = cand[-1]
                f = RAW / "blobs" / f"{name}.blob"
                if not f.exists():
                    r = _get(c, f"https://web.archive.org/web/{ts}id_/{u}")
                    f.write_bytes(gzip.compress(r.content) if r.content[:2] != b"\x1f\x8b" else r.content)
                    print(name, len(r.content))


def final() -> None:
    d = RAW / "final"
    d.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=120, follow_redirects=True) as c:
        for name, url in FINAL.items():
            if (d / name).exists():
                continue
            r = _get(c, url)
            r.raise_for_status()
            (d / name).write_bytes(r.content)
            print(name, len(r.content))


def markets() -> None:
    for slug, (windows, keep) in EVENTS.items():
        ev = pm.event(slug)
        out = POLYMARKET / slug
        (out / "prices").mkdir(parents=True, exist_ok=True)
        (out / "event.json").write_text(json.dumps(ev))
        mk = pm.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for m in mk.itertuples():
            pf = out / "prices" / f"{m.slug}.parquet"
            if pf.exists() or (keep is not None and m.slug not in keep):
                continue
            parts = []
            for a, b in windows:
                time.sleep(PAUSE)
                parts.append(pm.price_history(m.yes_token, pd.Timestamp(a), pd.Timestamp(b), 1))
            pd.concat(parts).drop_duplicates("time").to_parquet(pf)
            print(slug, m.slug, flush=True)


def kalshi_markets() -> None:
    """Kalshi event + 1-minute candles for markets with volume (no trades), under raw/kalshi/<event>/."""
    for ev_t, (a, b) in KALSHI_EVENTS.items():
        ev = kalshi.event(ev_t)
        out = kalshi.KALSHI / ev_t
        (out / "candles").mkdir(parents=True, exist_ok=True)
        (out / "event.json").write_text(json.dumps(ev))
        mk = kalshi.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for m in mk.itertuples():
            cf = out / "candles" / f"{m.ticker}.parquet"
            if cf.exists() or not m.volume:
                continue
            time.sleep(PAUSE)
            kalshi.candlesticks(ev["series_ticker"], m.ticker, pd.Timestamp(a), pd.Timestamp(b)).to_parquet(cf)
            print(ev_t, m.ticker, flush=True)


if __name__ == "__main__":
    {"cdx": cdx, "blobs": blobs, "final": final, "markets": markets, "kalshi": kalshi_markets}[sys.argv[1]]()
