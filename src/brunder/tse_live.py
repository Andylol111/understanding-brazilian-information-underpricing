"""TSE live-results feed (resultados.tse.jus.br): archive, watch, parse.

The live JSON tree is ephemeral: TSE removes it after the cycle (the 2022 files
already 404). Archive it while it exists; the open-data CSVs keep only final
totals, not the timing of the count.

File layout (cycle e.g. "ele2026", election code e.g. 6258 = 2026 2nd round, federal):
  {BASE}/{cycle}/{ele}/config/mun-e{ele:06}-cm.json        municipality catalogue
  {BASE}/{cycle}/{ele}/dados/{uf}/{uf}-e{ele:06}-ab.json    per-muni count progress + completion time
  {BASE}/{cycle}/{ele}/dados/br/br-c{cargo:04}-e{ele:06}-u.json          national votes
  {BASE}/{cycle}/{ele}/dados/{uf}/{uf}-c{cargo:04}-e{ele:06}-u.json      state votes
  {BASE}/{cycle}/{ele}/dados/{uf}/{uf}{mun}-c{cargo:04}-e{ele:06}-u.json municipality votes
Times (dt/ht) are Brasília time (UTC-3).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from . import _http
from .paths import TSE_LIVE

BASE = "https://resultados.tse.jus.br/oficial"
BRT = timezone(timedelta(hours=-3))
# State files stamp dt/ht in the state's own local time (verified 2026 R1: the Acre file read 15:25 and
# the Amazonas, Rondônia, Mato Grosso, Mato Grosso do Sul and Roraima files 16:25 when the others read
# 17:25 BRT, all matching BU-web receipt counts at 17:25 BRT). Brazil has no DST in 2026.
UTC_OFFSET_H = {"ac": -5, "am": -4, "ro": -4, "rr": -4, "mt": -4, "ms": -4}


def elections() -> pd.DataFrame:
    """All elections listed in the live-results config (both rounds, supplementary, referendums)."""
    with _http.client() as c:
        cfg = _http.get_json(c, f"{BASE}/comum/config/ele-c.json")
    rows = []
    for pl in cfg["pl"]:
        for e in pl["e"]:
            rows.append(
                {
                    "cycle": pl["c"],
                    "date": pl["dt"],
                    "ele": int(e["cd"]),
                    "ele_round2": int(e["cdt2"]) if e.get("cdt2") else None,
                    "round": int(e["t"]),
                    "name": e["nm"],
                    "offices": sorted({cp["ds"].strip() for a in e["abr"] for cp in a["cp"]}),
                }
            )
    return pd.DataFrame(rows)


def _urls(cycle: str, ele: int, cargo: int) -> dict:
    root = f"{BASE}/{cycle}/{ele}"
    e, c = f"e{ele:06d}", f"c{cargo:04d}"
    return {
        "cm": f"{root}/config/mun-{e}-cm.json",
        "ab": lambda uf: f"{root}/dados/{uf}/{uf}-{e}-ab.json",
        "u": lambda uf, mun="": f"{root}/dados/{uf}/{uf}{mun}-{c}-{e}-u.json",
    }


def _snapshot_dir(cycle: str, ele: int, cargo: int) -> Path:
    return TSE_LIVE / cycle / f"e{ele}" / f"c{cargo}"


async def _fetch_many(client, jobs: list[tuple[str, Path]], concurrency: int) -> list[str]:
    """Fetch jobs, skipping destinations that already exist. Returns URLs that still failed."""
    sem = asyncio.Semaphore(concurrency)
    failed: list[str] = []

    async def one(url: str, dest: Path):
        if dest.exists():
            return
        async with sem:
            try:
                body = await _http.aget_bytes(client, url)
            except httpx.HTTPError:
                failed.append(url)
                return
        if body is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)

    await asyncio.gather(*(one(u, d) for u, d in jobs))
    return failed


async def snapshot(cycle: str, ele: int, cargo: int = 1, concurrency: int = 8,
                   only_muns: set[tuple[str, str]] | None = None, resume: bool = False) -> Path:
    """Download one full snapshot (national, every UF, every municipality) into a timestamped dir.

    only_muns restricts municipality-level files to the given (uf, mun) pairs (used by `watch`).
    resume=True completes the latest existing snapshot dir instead of starting a new one — only
    meaningful once the count is final (otherwise files from different moments would mix).
    Raises if any file still fails after retries (rerun with resume=True).
    """
    u = _urls(cycle, ele, cargo)
    prev = snapshots(cycle, ele, cargo) if resume else []
    out = prev[-1] if prev else _snapshot_dir(cycle, ele, cargo) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    async with _http.aclient() as client:
        cm = json.loads(await _http.aget_bytes(client, u["cm"]))
        out.mkdir(parents=True, exist_ok=True)
        (out / "cm.json").write_text(json.dumps(cm))
        ufs = [a["cd"].lower() for a in cm["abr"]]
        jobs = [(u["u"]("br"), out / "br" / "u.json")]
        for uf in ufs:
            jobs.append((u["ab"](uf), out / uf / "ab.json"))
            jobs.append((u["u"](uf), out / uf / "u.json"))
        for a in cm["abr"]:
            uf = a["cd"].lower()
            for m in a["mu"]:
                if only_muns is None or (uf, m["cd"]) in only_muns:
                    jobs.append((u["u"](uf, m["cd"]), out / uf / f"{m['cd']}.json"))
        failed = await _fetch_many(client, jobs, concurrency)
    if failed:
        raise RuntimeError(f"{len(failed)} files failed (e.g. {failed[0]}); rerun with resume=True → {out}")
    return out


async def watch(cycle: str, ele: int, cargo: int = 1, interval: float = 30.0, concurrency: int = 32):
    """Poll during the count, state files first. Every `interval` seconds fetch the national and all
    27 UF (+ abroad) vote files; whenever any of them changed, write a snapshot dir with the changed
    UF files, their progress files, and the municipality files whose counted sections changed.

    State files are published before the national aggregate (2026 R1 cross-section: up to ~75 s
    earlier), so triggering on the national file alone would record the count late."""
    u = _urls(cycle, ele, cargo)
    last_idg: dict[str, str] = {}
    last_st: dict[tuple[str, str], str] = {}
    sem = asyncio.Semaphore(concurrency)
    FAILED = object()

    async def get(url):
        """Body, None on 404, or FAILED on any network/HTTP error (retried next poll)."""
        async with sem:
            try:
                return await _http.aget_bytes(client, url)
            except (httpx.HTTPError, OSError) as e:
                print(f"  fetch failed, will retry next poll: {url.rsplit('/', 1)[-1]} ({type(e).__name__})", flush=True)
                return FAILED

    pending: dict[tuple[str, str], str] = {}        # (uf, mun) → counted-sections value still to save

    async def poll(cm_raw: bytes, ufs: list[str]) -> bool:
        """One poll. Returns True when the count is final, nothing changed and nothing is pending."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")   # unique even for sub-second polls
        out = _snapshot_dir(cycle, ele, cargo) / stamp
        units = ["br", *ufs]
        raws = dict(zip(units, await asyncio.gather(*(get(u["u"](x)) for x in units))))
        ok = {x: r for x, r in raws.items() if r is not FAILED and r is not None}
        changed_ufs = [x for x, raw in ok.items() if json.loads(raw)["idg"] != last_idg.get(x)]
        if changed_ufs or pending:
            out.mkdir(parents=True, exist_ok=True)
            (out / "cm.json").write_bytes(cm_raw)
        for x in changed_ufs:
            (out / x).mkdir(exist_ok=True)
            (out / x / "u.json").write_bytes(ok[x])
        if "br" in changed_ufs:
            last_idg["br"] = json.loads(ok["br"])["idg"]
        state_ufs = [x for x in changed_ufs if x != "br"]
        abs_ = dict(zip(state_ufs, await asyncio.gather(*(get(u["ab"](x)) for x in state_ufs))))
        for x, raw in abs_.items():
            if raw is FAILED or raw is None:
                continue                          # last_idg[x] not advanced → whole UF re-polled next time
            (out / x / "ab.json").write_bytes(raw)
            last_idg[x] = json.loads(ok[x])["idg"]
            for a in json.loads(raw)["abr"]:
                if a["tpabr"] == "mun" and last_st.get((x, a["cdabr"])) != a["s"]["st"]:
                    pending[(x, a["cdabr"])] = a["s"]["st"]
        muns = list(pending.items())
        bodies = await asyncio.gather(*(get(u["u"](x, mun)) for (x, mun), _ in muns))
        for ((x, mun), st), body in zip(muns, bodies):
            if body is FAILED:
                continue                          # stays pending → retried next poll
            del pending[(x, mun)]
            last_st[(x, mun)] = st
            if body is not None:
                (out / x).mkdir(parents=True, exist_ok=True)
                (out / x / f"{mun}.json").write_bytes(body)
        br = json.loads(ok["br"]) if "br" in ok else {}
        if changed_ufs or muns:
            print(f"{stamp}: {len(state_ufs)} UFs changed, {len(muns) - len(pending)} munis saved, "
                  f"{len(pending)} pending; national {br.get('s', {}).get('pst', '?')}% sections", flush=True)
        return br.get("tf") == "s" and not changed_ufs and not pending

    async with _http.aclient() as client:
        cm_raw = None
        while cm_raw is None or cm_raw is FAILED:
            cm_raw = await get(u["cm"])
            if cm_raw is None or cm_raw is FAILED:
                await asyncio.sleep(interval)
        ufs = [a["cd"].lower() for a in json.loads(cm_raw)["abr"]]
        while True:
            try:
                if await poll(cm_raw, ufs):
                    print("totalização finalizada and no state changes; stopping", flush=True)
                    return
            except Exception as e:                # never let one bad poll end a once-only recording
                print(f"poll error, continuing: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(interval)


# ---------------------------------------------------------------- parsing

def _num(x: str | None) -> float:
    return float(x.replace(",", ".")) if x not in (None, "") else float("nan")


def _local_to_utc(dt: str, ht: str, uf: str = "br") -> pd.Timestamp:
    if not (dt or "").strip() or not (ht or "").strip():
        return pd.NaT
    tz = timezone(timedelta(hours=UTC_OFFSET_H.get(uf, -3)))
    return pd.Timestamp(datetime.strptime(f"{dt} {ht}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=tz)).tz_convert("UTC")


def parse_u(d: dict, uf: str) -> list[dict]:
    """Votes file → one row per candidate with unit-level totals repeated."""
    unit = {
        "level": d["tpabr"],
        "uf": uf,
        "cd_mun": d["cdabr"] if d["tpabr"] == "mu" else None,
        "published_utc": _local_to_utc(d["dg"], d["hg"], uf),
        "totalized_utc": _local_to_utc(d.get("dt"), d.get("ht"), uf),
        "sections_total": int(d["s"]["ts"]),
        "sections_counted": int(d["s"]["st"]),
        "electorate": int(d["e"]["te"]),
        "votes_total": int(d["v"]["tv"]),
        "votes_valid": int(d["v"]["vv"]),
        "votes_blank": int(d["v"]["vb"]),
        "votes_null": int(d["v"]["tvn"]),
        "finished": d.get("tf") == "s",
    }
    rows = []
    for carg in d["carg"]:
        for agr in carg.get("agr", []):
            for par in agr["par"]:
                for c in par["cand"]:
                    rows.append({**unit, "cand_n": c["n"], "cand": c["nmu"], "party": par["sg"],
                                 "votes": int(c["vap"])})
    return rows


def parse_ab(d: dict, uf: str) -> list[dict]:
    """Progress file → one row per municipality (and the UF itself) with completion time."""
    rows = []
    for x in d["abr"]:
        rows.append({
            "level": x["tpabr"],
            "uf": uf,
            "cd_mun": x["cdabr"] if x["tpabr"] == "mun" else None,
            "last_update_utc": _local_to_utc(x["dt"], x["ht"], uf),
            "sections_total": int(x["s"]["ts"]),
            "sections_counted": int(x["s"]["st"]),
            "electorate": int(x["e"]["te"]),
            "turnout": int(x["e"].get("c", 0) or 0),
        })
    return rows


def parse_snapshot(snap: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (votes, progress, catalogue) for one snapshot directory."""
    votes, progress = [], []
    for f in sorted(snap.glob("*/*.json")):
        uf = f.parent.name
        d = json.loads(f.read_text())
        if f.name == "ab.json":
            progress += parse_ab(d, uf)
        else:
            votes += parse_u(d, uf)
    cm = json.loads((snap / "cm.json").read_text())
    cat = pd.DataFrame([
        {"uf": a["cd"].lower(), "uf_name": a["ds"], "cd_mun": m["cd"], "cd_ibge": m.get("cdi"),
         "mun_name": m["nm"], "is_capital": m.get("c") == "s"}
        for a in cm["abr"] for m in a["mu"]
    ])
    v = pd.DataFrame(votes)
    p = pd.DataFrame(progress)
    v["snapshot"] = p["snapshot"] = snap.name
    return v, p, cat


def snapshots(cycle: str, ele: int, cargo: int = 1) -> list[Path]:
    return sorted(p for p in _snapshot_dir(cycle, ele, cargo).iterdir() if p.is_dir())
