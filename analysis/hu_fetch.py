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

v2 calibration (docs/hungary.md): `nvi` downloads the NVI results archive valasztasi_eredmenyek_1990-2024.zip
(309 MB, official, polling-station results of 2014 and 2018, county list results of 2010) into
raw/hungary/nvi_archive/ and unpacks the 2010/2014/2018/2022 parliamentary zips (accents dropped from file names);
`wayback2018` saves the Internet Archive's captures of the 2018 election-night national list page (orszlist.html),
the only 2018 count timeline that exists (national totals from 22:43 CEST), used as an order check.
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

UA = {"User-Agent": "brunder/0.1 (research; recorder)"}
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


NVI_ZIP = "https://static.valasztas.hu/dyn/letoltesek/valasztasi_eredmenyek_1990-2024.zip"


def nvi(c: httpx.Client, year: int) -> None:
    """Official results archive 1990-2024 (309 MB): keep it and unpack the parliamentary zips of 2014, 2018 and 2022
    (accents dropped from member names, which are not valid on macOS) next to it, as hu_calibrate_v2 expects."""
    import re
    import unicodedata
    import zipfile
    d = RAW_HU / "nvi_archive"
    top = d / "valasztasi_eredmenyek_1990-2024.zip"
    d.mkdir(parents=True, exist_ok=True)
    if not top.exists():
        with c.stream("GET", NVI_ZIP) as r:
            r.raise_for_status()
            with open(top.with_suffix(".part"), "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        top.with_suffix(".part").rename(top)
    out = d / "valasztasi_eredmenyek_1990-2024"
    out.mkdir(exist_ok=True)
    z = zipfile.ZipFile(top)
    for y in (2010, 2014, 2018, 2022):
        name = f"valasztasi_eredmenyek_1990-2024/{y}_parlamenti.zip"
        (out / f"{y}_parlamenti.zip").write_bytes(z.read(name))
        if y == 2010:
            continue
        inner = zipfile.ZipFile(out / f"{y}_parlamenti.zip")
        for i in inner.infolist():
            if i.is_dir():
                continue
            try:
                n = i.filename.encode("cp437").decode("cp1250")
            except UnicodeError:
                n = i.filename
            n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
            if re.search(r"\.(jpg|png|gif|inf|ico|exe|htm|html|pdf|css)$", n, re.I):
                continue
            f = out / str(y) / n
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(inner.read(i))


def wayback2018(c: httpx.Client, year: int) -> None:
    """Internet Archive captures of the 2018 election-night national list page (the only 2018 timeline): saved as
    ogy2018_wb/orszlist_<capture timestamp>.html (raw page bytes)."""
    r = _get(c, CDX + "?" + str(httpx.QueryParams({
        "url": "valasztas.hu/dyn/pv18/szavossz/hu/orszlist.html", "from": "20180408170000", "to": "20180409070000",
        "output": "json", "fl": "timestamp,original", "filter": "statuscode:200"})), pause=3.0)
    r.raise_for_status()
    out = RAW_HU / "ogy2018_wb"
    out.mkdir(parents=True, exist_ok=True)
    for ts, orig in r.json()[1:]:
        f = out / f"orszlist_{ts}.html"
        if not f.exists():
            f.write_bytes(_get(c, f"https://web.archive.org/web/{ts}id_/{orig}", pause=3.0).content)


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
            {"meta": meta, "scan": scan, "night": night, "wayback": wayback, "stations": stations, "nvi": nvi,
             "wayback2018": wayback2018}[what](c, y)
    print(RAW_HU)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], [int(x) for x in a[1:]] or [2022, 2026])
