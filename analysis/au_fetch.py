"""Download the AEC election-night media feed archive (anonymous FTP, one request per second).

Archive index: https://results.aec.gov.au/ ("Media feed archive"), ftp://mediafeedarchive.aec.gov.au/<event id>/.
Events: 31496 = 2025, 27966 = 2022, 24310 = 2019 federal election.
Files go to $BRUNDER_DATA/au/<event>/<Granularity>-<Verbosity>/ (zip each). Every zip is validated before it is kept.

    uv run python analysis/au_fetch.py list 31496 Detailed/Light
    uv run python analysis/au_fetch.py get 31496 Detailed/Light --since 20250503165000 --until 20250504040000
    uv run python analysis/au_fetch.py get 31496 Detailed/Preload          # all (3 files)
    uv run python analysis/au_fetch.py markets                             # Polymarket + Kalshi, 2025 election
"""
from __future__ import annotations

import argparse
import ftplib
import io
import re
import sys
import time
import zipfile

import json

import pandas as pd

from brunder.paths import DATA, POLYMARKET, RAW

HOST = "mediafeedarchive.aec.gov.au"
OUT = DATA / "au"
STAMP = re.compile(r"-(\d{14})\.zip$")


def connect() -> ftplib.FTP:
    f = ftplib.FTP(HOST, timeout=120)
    f.login("anonymous", "brunder-research@users.noreply.github.com")
    f.voidcmd("TYPE I")
    return f


def listing(event: str, variant: str) -> list[str]:
    cache = OUT / event / f"ls_{variant.replace('/', '-')}.txt"
    if cache.exists():
        return cache.read_text().split()
    f = connect()
    names = [n for n in f.nlst(f"/{event}/{variant}") if n.endswith(".zip")]
    f.quit()
    names = sorted(n.rsplit("/", 1)[-1] for n in names)
    if not names:
        raise SystemExit(f"empty listing for {event}/{variant}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(names) + "\n")
    return names


def valid_zip(blob: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return z.testzip() is None and any(n.endswith(".xml") for n in z.namelist())
    except zipfile.BadZipFile:
        return False


def get(event: str, variant: str, since: str = "0", until: str = "99999999999999", every: int = 1) -> int:
    names = [n for n in listing(event, variant) if since <= STAMP.search(n).group(1) <= until]
    names = names[::every]
    dest = OUT / event / variant.replace("/", "-")
    dest.mkdir(parents=True, exist_ok=True)
    todo = [n for n in names if not (dest / n).exists()]
    print(f"{event}/{variant}: {len(names)} files, {len(todo)} to fetch", flush=True)
    ftp = connect()
    last = 0.0
    for i, n in enumerate(todo):
        for attempt in range(6):
            wait = 1.0 - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.time()
            try:
                buf = io.BytesIO()
                ftp.retrbinary(f"RETR /{event}/{variant}/{n}", buf.write)
                blob = buf.getvalue()
                if not valid_zip(blob):
                    raise ValueError("not a valid zip")
                tmp = dest / (n + ".part")
                tmp.write_bytes(blob)
                tmp.rename(dest / n)
                break
            except (ftplib.all_errors, ValueError, OSError) as e:
                print("retry", n, attempt, e, file=sys.stderr, flush=True)
                time.sleep(2 ** attempt)
                try:
                    ftp.close()
                except Exception:
                    pass
                ftp = connect()
        else:
            raise SystemExit(f"failed {n}")
        if i % 25 == 0:
            print(f"  {i + 1}/{len(todo)} {n}", flush=True)
    ftp.quit()
    return len(todo)


PM_EVENTS = [
    "next-prime-minister-of-australia", "next-government-of-australia", "australia-parliamentary-election-winner",
    "australia-election-of-seats-margin-of-victory", "which-party-wins-the-most-primary-votes-in-australian-election",
    "of-seats-liberalnational-coalition-wins-in-australian-election", "of-seats-labor-win-in-australian-election",
    "australia-election-results-in-a-hung-parliament", "will-the-patient-focus-pf-win-a-seat-in-norwegian-election-328",
    "will-peter-dutton-lose-his-seat", "will-anthony-albanese-lose-his-seat", "australia-election-seat-of-bennelong",
    "australia-election-seat-of-brisbane",
]
KALSHI = [("KXAUSTRALIA", "KXAUSTRALIA"), ("KXAUSSEATSLABOR", "KXAUSSEATSLABOR-25"),
          ("KXAUSSEATSLIBNAT", "KXAUSSEATSLIBNAT-25"), ("KXAUSSEATSGREENS", "KXAUSSEATSGREENS-25"),
          ("KXAUSSEATDUTTON", "KXAUSSEATDUTTON-25"), ("KXAUSSEATALBANESE", "KXAUSSEATALBANESE-25"),
          ("KXAUSSEATBENNELONG", "KXAUSSEATBENNELONG-25"), ("KXAUSSEATBRISBANE", "KXAUSSEATBRISBANE-25")]
PRICE_START, PRICE_END = pd.Timestamp("2025-05-03 04:00", tz="UTC"), pd.Timestamp("2025-05-06 00:00", tz="UTC")


def markets() -> None:
    """1-minute Polymarket YES prices and Kalshi candles, 3 May 04:00 UTC to 6 May 00:00 UTC (poll close 08:00 UTC)."""
    from brunder import kalshi, polymarket as pm

    for slug in PM_EVENTS:
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
        print("polymarket", slug, len(mk), flush=True)
    for series, evt in KALSHI:
        out = RAW / "kalshi" / "australia" / evt
        (out / "candles").mkdir(parents=True, exist_ok=True)
        ev = kalshi.event(evt)
        (out / "event.json").write_text(json.dumps(ev))
        mk = kalshi.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for t in mk["ticker"]:
            f = out / "candles" / f"{t}.parquet"
            if not f.exists():
                kalshi.candlesticks(series, t, PRICE_START, PRICE_END, 1).to_parquet(f)
        print("kalshi", evt, len(mk), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "get", "markets"])
    ap.add_argument("event", nargs="?")
    ap.add_argument("variant", nargs="?")
    ap.add_argument("--since", default="0")
    ap.add_argument("--until", default="99999999999999")
    ap.add_argument("--every", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "markets":
        markets()
    elif a.cmd == "list":
        ns = listing(a.event, a.variant)
        print(len(ns), ns[0], ns[-1])
    else:
        get(a.event, a.variant, a.since, a.until, a.every)
