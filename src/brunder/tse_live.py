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
    """Poll during the count. Each time the national file changes, snapshot all UF progress
    files plus municipality files whose counted-section total changed since the last poll."""
    u = _urls(cycle, ele, cargo)
    last_idg = None
    last_st: dict[tuple[str, str], str] = {}
    async with _http.aclient() as client:
        cm = json.loads(await _http.aget_bytes(client, u["cm"]))
        ufs = [a["cd"].lower() for a in cm["abr"]]
        while True:
            br = json.loads(await _http.aget_bytes(client, u["u"]("br")))
            if br["idg"] != last_idg:
                abs_ = await asyncio.gather(*(_http.aget_bytes(client, u["ab"](uf)) for uf in ufs))
                changed = set()
                for uf, raw in zip(ufs, abs_):
                    if raw is None:
                        continue
                    for x in json.loads(raw)["abr"]:
                        if x["tpabr"] == "mun" and last_st.get((uf, x["cdabr"])) != x["s"]["st"]:
                            changed.add((uf, x["cdabr"]))
                            last_st[(uf, x["cdabr"])] = x["s"]["st"]
                out = await snapshot(cycle, ele, cargo, concurrency, only_muns=changed)
                last_idg = br["idg"]
                print(f"{out.name}: national {br['s']['pst']}% sections, {len(changed)} munis changed", flush=True)
                if br.get("tf") == "s":
                    print("totalização finalizada; stopping", flush=True)
                    return
            await asyncio.sleep(interval)


# ---------------------------------------------------------------- parsing

def _num(x: str | None) -> float:
    return float(x.replace(",", ".")) if x not in (None, "") else float("nan")


def _brt_to_utc(dt: str, ht: str) -> pd.Timestamp:
    if not (dt or "").strip() or not (ht or "").strip():
        return pd.NaT
    return pd.Timestamp(datetime.strptime(f"{dt} {ht}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=BRT)).tz_convert("UTC")


def parse_u(d: dict, uf: str) -> list[dict]:
    """Votes file → one row per candidate with unit-level totals repeated."""
    unit = {
        "level": d["tpabr"],
        "uf": uf,
        "cd_mun": d["cdabr"] if d["tpabr"] == "mu" else None,
        "published_utc": _brt_to_utc(d["dg"], d["hg"]),
        "totalized_utc": _brt_to_utc(d["dt"], d["ht"]) if d.get("ht") else pd.NaT,
        "sections_total": int(d["s"]["ts"]),
        "sections_counted": int(d["s"]["st"]),
        "electorate": int(d["e"]["te"]),
        "totalized_utc": _brt_to_utc(d.get("dt"), d.get("ht")),
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
            "last_update_utc": _brt_to_utc(x["dt"], x["ht"]),
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
