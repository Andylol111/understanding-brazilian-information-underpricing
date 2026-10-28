"""Fetch the Hungarian official count (Nemzeti Választási Iroda, vtr.valasztas.hu) for the 2022 and 2026
parliamentary elections: metadata, the final result, and every published version of the election-night count;
and the 2026 Polymarket price history.

The results site is a static single-page app reading JSON from https://vtr.valasztas.hu/ogy<year>/data/:
  config.json                      {"ver": <MMDDHHMM>, "napkozi": ..., "szavossz": <MMDDHHMM>} (current folders)
  <ver>/ver/*.json                 candidates, lists, organisations, districts, settlements, district polygons
  <szavossz>/szavossz/*.json       the count: OevkJkv (106 single-member districts), ListasJkv (national list
                                   vote, nation + 20 counties), LevelJkv (postal votes), DHondtMatrix, Patko, ...
Every count publication is a new <MMDDHHMM> folder (Budapest local time). For 2026 the old folders stay online,
so the election-night count can be replayed version by version; which folders exist is discovered by a HEAD
request per candidate minute (no listing exists) and cached in versions.txt. For 2022 the night folders were
removed from the server; the Internet Archive kept 9 of them (`wayback`), which is all the 2022 replay has.

robots.txt allows everything (`Disallow:` empty). Requests are sequential, >= 1.1 s apart, with an honest
User-Agent. Election results are official communications of a public body: not protected by copyright in
Hungary (Act LXXVI of 1999 on copyright, s. 1(4)) and public data of public interest (Act CXII of 2011).

`markets` archives the 2026 Polymarket events named in configs/hu2026_v1.toml (event.json, markets.parquet and
1-minute YES price history for the count window only) under $BRUNDER_DATA/raw/polymarket/<event>/, the layout of
brunder.polymarket.download_event. Trades are fetched later, per signal window, by analysis/hu_score.py.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_fetch.py meta|scan|night|stations|wayback|markets [year]
"""

from __future__ import annotations

import json
import sys
import time

import httpx
import pandas as pd

from brunder import polymarket as pm
from brunder.hungary import ELECTIONS, RAW_HU, load_config
from brunder.paths import POLYMARKET, ROOT

UA = {"User-Agent": "brunder-research/0.1 (election-count replay; github.com/Andylol111/"
                    "understanding-brazilian-information-underpricing; 1 request/s)"}
BASE = "https://vtr.valasztas.hu/ogy{year}/data/"
CDX = "https://web.archive.org/cdx/search/cdx"
PAUSE = 1.1
META = ["EgyeniJeloltek", "ListakEsJeloltek", "Szervezetek", "Jlcs", "Megyek", "OevkAdatok", "Telepulesek",
        "OevkPoligonok", "ElozoOevkEredmenyek"]
FINAL = ["OevkJkv", "ListasJkv", "LevelJkv", "DHondtMatrix", "Patko", "SzervezetekEredmenye", "HatarszamEredmenye",
         "OevkElsok"]
NIGHT = ["OevkJkv", "ListasJkv", "LevelJkv"]

_last = [0.0]


def _get(c: httpx.Client, url: str, head: bool = False, pause: float = PAUSE) -> httpx.Response:
    wait = pause - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(5):
        try:
            r = c.head(url) if head else c.get(url)
        except httpx.TransportError:
            time.sleep(5 * (attempt + 1))
            continue
        _last[0] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        return r
    raise RuntimeError(f"giving up on {url}")


def fetch(c: httpx.Client, year: int, path: str, required: bool = True) -> bool:
    """Download data/<path> once into RAW_HU/ogy<year>/<path>; False when the server has no such file."""
    out = RAW_HU / f"ogy{year}" / path
    if out.exists() and out.stat().st_size > 0:
        return True
    r = _get(c, BASE.format(year=year) + path)
    if r.status_code == 404:
        if required:
            print("404", year, path)
        return False
    r.raise_for_status()
    json.loads(r.content)                                        # refuse to cache an error page
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    return True


def meta(c: httpx.Client, year: int) -> None:
    E = ELECTIONS[year]
    for f in META:
        fetch(c, year, f"{E['ver']}/ver/{f}.json")
    for f in FINAL:
        fetch(c, year, f"{E['final']}/szavossz/{f}.json")


def scan(c: httpx.Client, year: int) -> list[str]:
    """Every count folder published on election night (19:00 .. 08:00 next morning, local time), found by HEAD
    on <MMDDHHMM>/szavossz/OevkJkv.json for each minute; cached in versions.txt."""
    E = ELECTIONS[year]
    f = RAW_HU / f"ogy{year}" / "versions.txt"
    if f.exists():
        return f.read_text().split()
    day = pd.Timestamp(E["date"])
    minutes = pd.date_range(day + pd.Timedelta(hours=19), day + pd.Timedelta(hours=32), freq="1min", inclusive="left")
    found = []
    for t in minutes:
        v = t.strftime("%m%d%H%M")
        if _get(c, BASE.format(year=year) + f"{v}/szavossz/OevkJkv.json", head=True).status_code == 200:
            found.append(v)
            print("found", v, flush=True)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(found) + "\n")
    return found


def night(c: httpx.Client, year: int) -> None:
    for v in scan(c, year):
        for name in NIGHT:
            fetch(c, year, f"{v}/szavossz/{name}.json", required=name != "LevelJkv")


def wayback(c: httpx.Client, year: int) -> None:
    """Night count files of `year` as captured by the Internet Archive (the official server no longer has them)."""
    E = ELECTIONS[year]
    day = pd.Timestamp(E["date"])
    found = []
    for name in ("OevkJkv", "ListasJkv"):
        r = _get(c, CDX + "?" + str(httpx.QueryParams({
            "url": f"vtr.valasztas.hu/ogy{year}/data/", "matchType": "prefix", "output": "json",
            "fl": "timestamp,original", "filter": ["statuscode:200", f"original:.*/szavossz/{name}\\.json$"],
            "collapse": "original"})), pause=3.0)
        r.raise_for_status()
        for ts, orig in r.json()[1:]:
            v = orig.split("/data/")[1].split("/")[0]
            if len(v) != 8 or not v.isdigit():
                continue
            t = pd.Timestamp(f"{day.year}-{v[:2]}-{v[2:4]} {v[4:6]}:{v[6:]}")
            if not day + pd.Timedelta(hours=19) <= t < day + pd.Timedelta(hours=32):
                continue
            out = RAW_HU / f"ogy{year}" / v / "szavossz" / f"{name}.json"
            if not out.exists():
                rr = _get(c, f"https://web.archive.org/web/{ts}id_/{orig}", pause=3.0)
                rr.raise_for_status()
                json.loads(rr.content)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(rr.content)
            found.append(v)
    f = RAW_HU / f"ogy{year}" / "versions.txt"
    f.write_text("\n".join(sorted(set(found))) + "\n")


def stations(c: httpx.Client, year: int) -> None:
    """Polling-station lists (with district) of the settlements split between districts: the 2022 -> 2026
    district crosswalk is built from these (brunder.hungary.crosswalk)."""
    E = ELECTIONS[year]
    tel = json.loads((RAW_HU / f"ogy{year}" / E["ver"] / "ver" / "Telepulesek.json").read_text())["list"]
    for x in tel:
        d = x.get("leiro", x)
        if len(d["evk_lst"]) > 1:
            fetch(c, year, f"{E['ver']}/ver/{d['maz']}/Szavazokorok-{d['maz']}-{d['taz']}.json")


def markets(cfg_path=ROOT / "configs" / "hu2026_v1.toml") -> None:
    cfg = load_config(cfg_path)
    lo, hi = pd.Timestamp(cfg["prices"]["start_utc"]), pd.Timestamp(cfg["prices"]["end_utc"])
    for slug in sorted({m["event"] for m in cfg["markets"]}):
        out = POLYMARKET / slug
        (out / "prices").mkdir(parents=True, exist_ok=True)
        ev = pm.event(slug)
        (out / "event.json").write_text(json.dumps(ev))
        mk = pm.markets(ev)
        mk.to_parquet(out / "markets.parquet")
        for m in mk.itertuples():
            f = out / "prices" / f"{m.slug}.parquet"
            if not f.exists():
                pm.price_history(m.yes_token, lo, hi, 1, chunk=pd.Timedelta(days=1)).to_parquet(f)
        print(slug, len(mk), flush=True)


def main(what: str, years: list[int]) -> None:
    if what == "markets":
        markets()
        return
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as c:
        for y in years:
            {"meta": meta, "scan": scan, "night": night, "wayback": wayback, "stations": stations}[what](c, y)
    print(RAW_HU)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], [int(x) for x in a[1:]] or [2022, 2026])
