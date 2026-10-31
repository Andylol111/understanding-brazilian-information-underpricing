"""Fetch France replay data: official final results, count-night captures, Polymarket markets.

* `open`     official final results (Ministère de l'Intérieur, via data.gouv.fr; Licence Ouverte, the 2022 datasets carry no
             licence tag): presidential 2012 / 2017 / 2022 (both rounds) by polling station, 2022 also by département and
             commune group; legislative 2024 (both rounds) by polling station, commune, constituency, département.
* `cdx`      Internet Archive CDX listing of the ministry's results site on each of the six count nights (cdx_<night>.json).
* `wayback`  Internet Archive: the national result page `FE.html` of the four presidential nights 2017 R1/R2, 2022 R1/R2 (one
             file per distinct content, named by its first capture time) and the one archived copy of each night's media-feed
             index (per-département last-update stamps). CDX listings of all six nights are in CDX_NIGHTS.
* `markets`  Polymarket events and 1-minute YES prices of the 2022 and 2024 markets (2022 returns no history).

Requests: sequential, at least 1.1 s apart per host, User-Agent `brunder/0.1 (research; recorder)`, also to web.archive.org.
A download is kept only when it is the expected size / table and not an HTML error body.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/fr_fetch.py open|cdx|wayback|markets
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from brunder.paths import RAW

UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
RAW_FR = RAW / "france"
DG = "https://www.data.gouv.fr/api/1/datasets"
PAUSE = 1.1
_last: dict[str, float] = {}

# (dataset slug, wanted resource file names, local sub-directory)
OPEN = {
    "pr2012": [
        ("election-presidentielle-2012-resultats-par-bureaux-de-vote-1", ["PR12_Bvot_T1T2.txt"]),
    ],
    "pr2017": [
        ("election-presidentielle-des-23-avril-et-7-mai-2017-resultats-definitifs-du-1er-tour-par-bureaux-de-vote",
         ["PR17_BVot_T1_FE.txt"]),
        ("election-presidentielle-des-23-avril-et-7-mai-2017-resultats-definitifs-du-2nd-tour-par-bureaux-de-vote",
         ["PR17_BVot_T2_FE.txt"]),
    ],
    "pr2022": [
        ("election-presidentielle-des-10-et-24-avril-2022-resultats-definitifs-du-1er-tour",
         ["resultats-par-niveau-burvot-t1-france-entiere.txt", "resultats-par-niveau-dpt-t1-france-entiere.txt",
          "resultats-par-niveau-subcom-t1-france-entiere.txt", "resultats-par-niveau-fe-t1-outre-mer.txt"]),
        ("election-presidentielle-des-10-et-24-avril-2022-resultats-definitifs-du-2nd-tour",
         ["resultats-par-niveau-burvot-t2-france-entiere.txt", "resultats-par-niveau-dpt-t2-france-entiere.txt",
          "resultats-par-niveau-subcom-t2-france-entiere.txt", "resultats-par-niveau-fe-t2-outre-mer.txt"]),
    ],
    "lg2024": [
        ("elections-legislatives-des-30-juin-et-7-juillet-2024-resultats-definitifs-du-1er-tour",
         ["resultats-definitifs-par-bureau-de-vote.csv", "resultats-definitifs-par-communes.csv",
          "resultats-definitifs-par-circonscriptions-legislatives.csv", "resultats-definitifs-par-departements.csv"]),
        ("elections-legislatives-des-30-juin-et-7-juillet-2024-resultats-definitifs-du-2nd-tour",
         ["resultats-definitifs-par-bureau-de-vote.csv", "resultats-definitifs-par-commune.csv",
          "resultats-definitifs-par-circonscription.csv", "resultats-definitifs-par-departement.csv"]),
    ],
}


def get(c: httpx.Client, url: str, **kw) -> httpx.Response:
    host = httpx.URL(url).host
    for attempt in range(6):
        wait = PAUSE - (time.monotonic() - _last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = c.get(url, **kw)
        except httpx.TransportError:
            _last[host] = time.monotonic()
            time.sleep(5 * (attempt + 1))
            continue
        _last[host] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        return r
    raise RuntimeError(f"giving up on {url}")


def fetch_open(c: httpx.Client) -> None:
    for tag, datasets in OPEN.items():
        out = RAW_FR / "open" / tag
        out.mkdir(parents=True, exist_ok=True)
        for slug, names in datasets:
            meta = get(c, f"{DG}/{slug}/").json()
            (out / f"{slug}.meta.json").write_text(json.dumps(
                {k: meta.get(k) for k in ("title", "license", "last_modified", "page")}
                | {"resources": [{k: r.get(k) for k in ("title", "url", "filesize", "checksum")}
                                 for r in meta["resources"]]}, indent=1))
            by = {}
            for r in meta["resources"]:
                by.setdefault(r["url"].rsplit("/", 1)[-1], r)
            for name in names:
                res = by.get(name) or next((r for r in meta["resources"] if r["title"].startswith(name[:-4])
                                            and r["format"] == name[-3:]), None)
                if res is None:
                    raise SystemExit(f"{slug}: {name} not listed")
                dest = out / (("t1_" if "1er-tour" in slug else "t2_" if "2nd-tour" in slug else "") + name)
                if dest.exists() and dest.stat().st_size == res["filesize"]:
                    continue
                r = get(c, res["url"])
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "html" in ctype or len(r.content) != res["filesize"]:
                    raise SystemExit(f"{res['url']}: bad body {ctype} {len(r.content)} != {res['filesize']}")
                dest.write_bytes(r.content)
                print("open", dest.relative_to(RAW_FR), len(r.content), flush=True)


CDX = "https://web.archive.org/cdx/search/cdx"
# night -> (CDX url prefix, from, to) of the ministry's results site, UTC
CDX_NIGHTS = {
    "pr17r1": ("elections.interieur.gouv.fr/presidentielle-2017", "20170423120000", "20170424060000"),
    "pr17r2": ("elections.interieur.gouv.fr/presidentielle-2017", "20170507120000", "20170508060000"),
    "pr22r1": ("www.resultats-elections.interieur.gouv.fr/presidentielle-2022", "20220410120000", "20220411060000"),
    "pr22r2": ("www.resultats-elections.interieur.gouv.fr/presidentielle-2022", "20220424120000", "20220425060000"),
    "lg24r1": ("www.resultats-elections.interieur.gouv.fr/legislatives2024", "20240630120000", "20240701060000"),
    "lg24r2": ("www.resultats-elections.interieur.gouv.fr/legislatives2024", "20240707120000", "20240708060000"),
}
# the ministry's media-feed index files that Wayback holds for a night (one capture each; stamps per département)
FEED_INDEX = {
    "pr22r1": "https://www.resultats-elections.interieur.gouv.fr/telechargements/PR2022/resultatsT1/index.xml",
    "pr22r2": "https://www.resultats-elections.interieur.gouv.fr/telechargements/PR2022/resultatsT2/index.xml",
    "lg24r1": "https://www.resultats-elections.interieur.gouv.fr/telechargements/LG2024/resultatsT1/INDEX1FE.xml",
}


def cdx(c: httpx.Client, url: str, lo: str, hi: str, extra: dict | None = None) -> list[list[str]]:
    r = get(c, CDX, params={"url": url, "matchType": "prefix", "from": lo, "to": hi, "output": "json",
                            "fl": "timestamp,original,statuscode,mimetype,digest", "limit": "20000", **(extra or {})})
    r.raise_for_status()
    return json.loads(r.text)[1:] if r.text.strip() else []


def fe_captures(night: str, rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """(first capture time, url, digest) of every distinct content of the national FE.html page (status 200)."""
    seen: dict[str, tuple[str, str, str]] = {}
    for ts, url, st, mt, dg in sorted(rows):
        if st == "200" and url.split("?")[0].endswith("/FE.html") and dg not in seen:
            seen[dg] = (ts, url, dg)
    return sorted(seen.values())


def cdx_all(c: httpx.Client) -> None:
    for night, (u, lo, hi) in CDX_NIGHTS.items():
        f = RAW_FR / f"cdx_{night}.json"
        if not f.exists():
            f.write_text(json.dumps(cdx(c, u, lo, hi)))
        print("cdx", night, len(json.loads(f.read_text())), flush=True)


def wayback_fe(c: httpx.Client) -> None:
    """National result page of each night, one file per distinct content, named by its first capture time."""
    for night in ("pr17r1", "pr17r2", "pr22r1", "pr22r2"):
        out = RAW_FR / "wayback" / night
        out.mkdir(parents=True, exist_ok=True)
        lst = out / "cdx.json"
        if not lst.exists():
            u, lo, hi = CDX_NIGHTS[night]
            lst.write_text(json.dumps(cdx(c, u, lo, hi)))
        for ts, url, dg in fe_captures(night, json.loads(lst.read_text())):
            f = out / f"FE_{ts}.html"
            if f.exists():
                continue
            r = get(c, f"https://web.archive.org/web/{ts}id_/{url}")
            body = r.content
            if r.status_code != 200 or b"Voix" not in body or b"Inscrits" not in body:
                print("skip", night, ts, r.status_code, len(body), flush=True)
                continue
            f.write_bytes(body)
            print("wayback", night, ts, len(body), flush=True)


def wayback_feed(c: httpx.Client) -> None:
    """The one archived copy of each night's media-feed index (per-département last-update stamps)."""
    out = RAW_FR / "wayback" / "feed"
    out.mkdir(parents=True, exist_ok=True)
    for night, url in FEED_INDEX.items():
        rows = cdx(c, url.split("//")[1], "20220401000000", "20240801000000")
        rows = [x for x in rows if x[1].split("?")[0] == url and x[2] == "200"]
        (out / f"{night}_cdx.json").write_text(json.dumps(rows))
        for ts, u, st, mt, dg in rows[:1]:
            f = out / f"{night}_{ts}.xml"
            if f.exists():
                continue
            r = get(c, f"https://web.archive.org/web/{ts}id_/{u}")
            if r.status_code != 200 or not r.content.lstrip().startswith(b"<?xml"):
                print("skip feed", night, ts, r.status_code, flush=True)
                continue
            f.write_bytes(r.content)
            print("feed", night, ts, len(r.content), flush=True)


NIGHTS = {  # id -> (window start, window end), UTC; the count night plus the polling hours before it
    "pr22r1": ("2022-04-10 12:00", "2022-04-11 12:00"),
    "pr22r2": ("2022-04-24 12:00", "2022-04-25 12:00"),
    "lg24r1": ("2024-06-30 12:00", "2024-07-01 12:00"),
    "lg24r2": ("2024-07-07 12:00", "2024-07-08 12:00"),
}
EVENTS = {  # Polymarket event slug -> nights it traded through
    "who-will-win-the-2022-french-presidential-election": ["pr22r1", "pr22r2"],
    "will-marine-le-pen-win-2nd-place-in-the-first-round-of-the-2022-french-presidential-election": ["pr22r1"],
    "will-macron-win-round-2-of-the-2022-french-presidential-election-with-54-of-votes-or-more": ["pr22r2"],
    "which-party-wins-the-most-seats-in-french-election": ["lg24r1", "lg24r2"],
    "which-coalition-wins-most-seats-in-french-election": ["lg24r1", "lg24r2"],
    "national-rally-coalition-wins-a-majority-of-seats-in-french-election": ["lg24r1", "lg24r2"],
    "how-many-seats-will-national-rally-coalition-win-in-french-election": ["lg24r1", "lg24r2"],
    "how-many-seats-will-new-popular-front-coalition-win-in-french-election": ["lg24r1", "lg24r2"],
    "how-many-seats-will-the-ensemble-coalition-win-in-the-french-election": ["lg24r1", "lg24r2"],
    "next-french-prime-minister": ["lg24r1", "lg24r2"],
}


def markets() -> None:
    """Polymarket events and 1-minute YES prices for every night in `EVENTS` (CLOB prices-history, Gamma metadata)."""
    import pandas as pd

    from brunder import polymarket as pm
    from brunder.paths import POLYMARKET

    for slug, nights in EVENTS.items():
        out = POLYMARKET / slug
        (out / "prices").mkdir(parents=True, exist_ok=True)
        ev = pm.event(slug)
        (out / "event.json").write_text(json.dumps(ev))
        mk = pm.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for m in mk.itertuples():
            for night in nights:
                lo, hi = (pd.Timestamp(x, tz="UTC") for x in NIGHTS[night])
                f = out / "prices" / f"{m.slug}__{night}.parquet"
                if f.exists() or m.created > hi:
                    continue
                pm.price_history(m.yes_token, max(lo, m.created.floor("min")), hi, 1, chunk=pd.Timedelta(days=1)).to_parquet(f)
                time.sleep(0.3)
        print(slug, len(mk), flush=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "open"
    with httpx.Client(headers=UA, timeout=300, follow_redirects=True) as c:
        if what == "open":
            fetch_open(c)
        elif what == "cdx":
            cdx_all(c)
        elif what == "wayback":
            wayback_fe(c)
            wayback_feed(c)
        elif what == "markets":
            markets()
        else:
            raise SystemExit(f"unknown step {what}")
