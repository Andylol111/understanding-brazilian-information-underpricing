"""Section-level count path from TSE "Boletim de Urna na Web" files (dadosabertos, per state).

Every polling section's report carries DT_BU_RECEBIDO, the time TSE received it, and its votes by
candidate. Cumulating sections by receipt time rebuilds the count as it arrived, state by state —
the information set a live observer of the state files had, up to the publication lag between
receipt and the results site (calibrated against the archived 17:26 BRT cross-section).

Layout: data/raw/tse_open/bweb/<round>t/<UF>.parquet — one row per (section, office, candidate number),
presidential and governor rows (`cargo`). Zips are deleted after parsing (re-downloadable).

`from_arquivo_urna` builds the same layout from TSE's per-ballot-box files (arquivo-urna: the `aux`
file's receipt time plus the decoded BU), as published in rows by a third party; used for the 2026
runoff before the BU-web files were released. Its provenance is written to SOURCE.json in that dir.
"""

from __future__ import annotations

import io
import json
import time
import urllib.request
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from ._http import UA
from .paths import TSE_OPEN

CKAN = "https://dadosabertos.tse.jus.br/api/3/action/package_show?id=resultados-{year}-boletim-de-urna"
DIR = TSE_OPEN / "bweb"
COLS = ["SG_UF", "CD_MUNICIPIO", "NR_ZONA", "NR_SECAO", "DS_CARGO_PERGUNTA", "DT_BU_RECEBIDO", "NR_VOTAVEL", "QT_VOTOS"]


def files(year: int, rnd: int) -> list[str]:
    d = json.load(urllib.request.urlopen(CKAN.format(year=year)))["result"]
    return sorted(r["url"] for r in d["resources"] if r["url"].endswith(".zip") and f"_{rnd}t_" in r["url"])


def _download(url: str, dest: Path, tries: int = 8) -> Path:
    """Stream to dest.part, resuming with HTTP Range after a dropped connection."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    for attempt in range(tries):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {**UA, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with httpx.stream("GET", url, headers=headers, timeout=600, follow_redirects=True) as r:
                if r.status_code == 416:          # already complete
                    break
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                with tmp.open(mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            break
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == tries - 1:
                raise
            time.sleep(min(2**attempt, 60))
    tmp.rename(dest)
    return dest


OFFICES = ("PRESIDENTE", "GOVERNADOR")


def _parse(zpath: Path) -> pd.DataFrame:
    """Presidential and governor rows of one state's BU-web file."""
    frames = []
    with zipfile.ZipFile(zpath) as zf:
        for name in (n for n in zf.namelist() if n.lower().endswith(".csv")):
            with zf.open(name) as fh:
                for ch in pd.read_csv(io.TextIOWrapper(fh, encoding="latin-1"), sep=";", usecols=COLS, dtype=str,
                                      chunksize=2_000_000):
                    ch = ch.assign(cargo=ch["DS_CARGO_PERGUNTA"].str.strip().str.upper())
                    frames.append(ch[ch["cargo"].isin(OFFICES)].drop(columns="DS_CARGO_PERGUNTA"))
    d = pd.concat(frames, ignore_index=True)
    d["QT_VOTOS"] = pd.to_numeric(d["QT_VOTOS"])
    d["DT_BU_RECEBIDO"] = pd.to_datetime(d["DT_BU_RECEBIDO"])
    return d.rename(columns={"SG_UF": "uf", "CD_MUNICIPIO": "cd_mun", "NR_ZONA": "zona", "NR_SECAO": "secao",
                             "DT_BU_RECEBIDO": "received", "NR_VOTAVEL": "cand_n", "QT_VOTOS": "votes"})


def fetch(year: int, rnd: int) -> list[Path]:
    """Download + parse every state's file for a round (resumable: parsed states are skipped)."""
    out = []
    for url in files(year, rnd):
        uf = url.split("/")[-1].split("_")[2]
        pq = DIR / f"{year}_{rnd}t" / f"{uf}.parquet"
        if not pq.exists():
            z = _download(url, DIR / f"{year}_{rnd}t" / url.split("/")[-1])
            d = _parse(z)
            d["uf"] = d["uf"].str.lower()
            d["cd_mun"] = d["cd_mun"].str.zfill(5)
            d.to_parquet(pq)
            z.unlink()
            print(f"{uf}: {d[['zona', 'secao', 'cd_mun']].drop_duplicates().shape[0]} sections, received "
                  f"{d['received'].min()} → {d['received'].max()}", flush=True)
        out.append(pq)
    return out


def from_arquivo_urna(src: Path, year: int, rnd: int, provenance: dict) -> list[Path]:
    """Convert per-section rows (`<uf>.jsonl.gz`: k = mun-zone-section, dr/hr = receipt time BRT,
    presidential votes by candidate number plus br/nu, optional `g` = governor race in the same box) to
    this module's parquet layout. Sections without a receipt time ("miss") are skipped and counted."""
    import gzip
    out_dir = DIR / f"{year}_{rnd}t"
    out_dir.mkdir(parents=True, exist_ok=True)
    special = {"br": "95", "nu": "96"}
    skip = {"apt", "comp"}
    written, missing = [], 0
    for f in sorted(src.glob("*.jsonl.gz")):
        uf = f.name.split(".")[0].lower()
        rows = []
        for line in gzip.open(f, "rt"):
            r = json.loads(line)
            if "dr" not in r:
                missing += 1
                continue
            mun, zona, secao = r["k"].split("-")
            t = pd.to_datetime(f"{r['dr']} {r['hr']}", format="%d/%m/%Y %H:%M:%S")
            base = {"uf": uf, "cd_mun": mun.zfill(5), "zona": str(int(zona)), "secao": str(int(secao)), "received": t}
            for cargo, votes in (("PRESIDENTE", r), ("GOVERNADOR", r.get("g") or {})):
                for key, v in votes.items():
                    if key in skip or not (key.isdigit() or key in special):
                        continue
                    rows.append({**base, "cand_n": special.get(key, key), "votes": int(v), "cargo": cargo})
        d = pd.DataFrame(rows)
        pq = out_dir / f"{uf.upper()}.parquet"
        d.to_parquet(pq)
        written.append(pq)
    (out_dir / "SOURCE.json").write_text(json.dumps({**provenance, "sections_without_receipt_time": missing}, indent=1))
    return written


def sections(year: int, rnd: int, cand_a: str, cand_b: str | None = None, cargo: str = "PRESIDENTE",
             uf: str | None = None) -> pd.DataFrame:
    """One row per section for one office (and optionally one state): uf, received, valid votes,
    A votes, B votes (blank 95 / null 96 excluded). Files parsed before governor rows were kept hold
    presidential rows only and have no `cargo` column."""
    files_ = sorted((DIR / f"{year}_{rnd}t").glob(f"{uf.upper()}.parquet" if uf else "*.parquet"))
    d = pd.concat(pd.read_parquet(p) for p in files_)
    if "cargo" not in d:
        if cargo.upper() != "PRESIDENTE":
            raise ValueError(f"{year} round {rnd} was parsed presidential-only; re-fetch for {cargo}")
    else:
        d = d[d["cargo"] == cargo.upper()]
    d = d[~d["cand_n"].isin(["95", "96"])]
    key = ["uf", "cd_mun", "zona", "secao"]
    g = d.groupby(key).agg(received=("received", "max"), valid=("votes", "sum"))
    g["a"] = d[d["cand_n"] == cand_a].groupby(key)["votes"].sum()
    if cand_b is not None:
        g["b"] = d[d["cand_n"] == cand_b].groupby(key)["votes"].sum()
    return g.fillna(0).reset_index()


def state_partials(sec: pd.DataFrame, t: pd.Timestamp, total_sections: pd.Series) -> pd.DataFrame:
    """State partials (statecast input) from sections received at or before t."""
    s = sec[sec["received"] <= t].groupby("uf").agg(n=("valid", "sum"), a=("a", "sum"), k=("valid", "size"))
    s["f"] = s["k"] / total_sections.reindex(s.index)
    return s.drop(columns="k").reset_index()
