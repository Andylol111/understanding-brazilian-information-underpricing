"""TSE open data (dadosabertos.tse.jus.br): final certified results, used as priors and ground truth.

CSV conventions: latin-1, ';'-separated, '#NULO#'/-1 sentinels. CD_MUNICIPIO is the TSE
5-digit municipality code (same as the live feed's cd_mun), not IBGE.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from ._http import UA
from .paths import TSE_OPEN

CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele"
DATASETS = {
    # candidate votes by municipality × zone (all offices)
    "votacao_candidato_munzona": "{cdn}/votacao_candidato_munzona/votacao_candidato_munzona_{year}.zip",
    # turnout / blank / null by municipality × zone
    "detalhe_votacao_munzona": "{cdn}/detalhe_votacao_munzona/detalhe_votacao_munzona_{year}.zip",
    # president votes by polling section (large)
    "votacao_secao_br": "{cdn}/votacao_secao/votacao_secao_{year}_BR.zip",
}


def download(dataset: str, year: int, force: bool = False) -> Path:
    url = DATASETS[dataset].format(cdn=CDN, year=year)
    dest = TSE_OPEN / Path(url).name
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, headers=UA, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


_KEYS = ["NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_CANDIDATO", "NM_URNA_CANDIDATO"]


def _office_rows(zf: zipfile.ZipFile, name: str, cargo: str) -> pd.DataFrame:
    """Stream one CSV in chunks, keeping only rows for one office (files cover every office)."""
    with zf.open(name) as fh:
        header = pd.read_csv(io.TextIOWrapper(fh, encoding="latin-1"), sep=";", nrows=0).columns
    vcol = "QT_VOTOS_NOMINAIS_VALIDOS" if "QT_VOTOS_NOMINAIS_VALIDOS" in header else "QT_VOTOS_NOMINAIS"
    frames = []
    with zf.open(name) as fh:
        for ch in pd.read_csv(io.TextIOWrapper(fh, encoding="latin-1"), sep=";", dtype=str,
                              usecols=[*_KEYS, "DS_CARGO", vcol], chunksize=1_000_000):
            ch = ch[ch["DS_CARGO"].str.upper().str.strip() == cargo]
            ch = ch.assign(votes=pd.to_numeric(ch[vcol]))
            frames.append(ch.groupby(_KEYS, as_index=False)["votes"].sum())
    return pd.concat(frames, ignore_index=True)


def votes_by_municipality(year: int, cargo: str = "PRESIDENTE") -> pd.DataFrame:
    """Final votes per municipality for one office (e.g. PRESIDENTE, GOVERNADOR), both rounds, from
    votacao_candidato_munzona. The zip ships per-UF CSVs plus a BRASIL file; use the BRASIL file when
    present to avoid double counting, else concatenate UFs. Cached as parquet next to the zip."""
    cargo = cargo.upper()
    cache = TSE_OPEN / f"{cargo.lower()}_by_municipality_{year}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    zpath = download("votacao_candidato_munzona", year)
    with zipfile.ZipFile(zpath) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        br = [n for n in csvs if "BRASIL" in n.upper()]
        df = pd.concat([_office_rows(zf, n, cargo) for n in (br or csvs)], ignore_index=True)
    out = (df.groupby(_KEYS, as_index=False)["votes"].sum()
             .rename(columns={"NR_TURNO": "round", "SG_UF": "uf", "CD_MUNICIPIO": "cd_mun",
                              "NM_MUNICIPIO": "mun_name", "NR_CANDIDATO": "cand_n",
                              "NM_URNA_CANDIDATO": "cand"}))
    out["round"] = out["round"].astype(int)
    out["uf"] = out["uf"].str.lower()
    out["cd_mun"] = out["cd_mun"].str.zfill(5)
    out.to_parquet(cache)
    return out
