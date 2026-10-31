"""Build data_public/es_candidacies_2026_presented.csv: the Congress candidacies presented in each of the 52 constituencies for
2026-11-29, with the model lane(s) each one represents.

    uv run python analysis/es_lists_2026.py

Source: the lists published in the BOE of 2026-10-28 (BOE-A-2026-22607), as republished province by province with the head of
list by https://votomunicipal.es/candidaturas/ (one request, honest user agent). These are the PRESENTED lists: the provincial
electoral boards proclaim them on 2026-11-02/03 and a list can still drop out. The BOE itself was not read (press republication).
Lane mapping (`LANES_OF`): a list that merges several model lanes lists them with '+', the FIRST gets the seats of a joint list.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brunder import spain as es
from brunder.paths import ROOT

URL = "https://votomunicipal.es/candidaturas/"
OUT = ROOT / "data_public" / "es_candidacies_2026_presented.csv"

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper().replace("–", "-")).strip()


PROV_CODE = {norm(v): c for c, v in es.PROVINCES.items()}


# normalised acronym (or, if it is empty, the full name) -> lanes
LANES_OF = [
    (r"^PP$", "PP"), (r"^UPN-PP$", "UPN+PP"), (r"^VOX$", "VOX"),
    (r"^(PSOE|PSC|PSDEG-PSOE|PSIB-PSOE|PSN-PSOE)$|\(PSOE\)", "PSOE"),
    (r"^PODEMOS$", "PODEMOS"),
    (r"^COMPROMIS-EUPV-PODEM-ERPV$|^NC-BC, PODEMOS, IU, SUMAR$", "FA+PODEMOS"),
    (r"^FA$|^FRENTE AMPLIO|^COMUNS-FRONT AMPLI$|^CHA-IU-MS-FRENTE AMPLIO$|^ARAMES-EU-MS$", "FA"),
    (r"^ERC$", "ERC"), (r"^JXCAT$", "JUNTS"), (r"^EH[ -]BILDU$", "BILDU"), (r"^EAJ-PNV$", "PNV"), (r"^BNG$", "BNG"),
    (r"CCA", "CCA"), (r"^ADELANTE ANDALUCIA$", "ADELANTE"), (r"^TERUEL EXISTE$", "EXISTE"),
]


def lanes_of(acronym: str, name: str) -> str:
    key = norm(acronym) or norm(name)
    for pat, lanes in LANES_OF:
        if re.search(pat, key):
            return lanes
    return "OTHER"


def fetch() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": es.UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return r.read().decode("utf-8")


def parse(page: str) -> pd.DataFrame:
    rows = []
    for m in re.finditer(r"<details>\s*<summary><b>(.*?)</b> · (\d+) candidaturas · (\d+) diputados?</summary>(.*?)</details>", page, flags=re.S):
        name, n, seats, body = html.unescape(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        code = PROV_CODE.get(norm(name)) or _alias(name)
        found = re.findall(r'<abbr title="(.*?)">(.*?)</abbr></th><td>(.*?)</td>', body, flags=re.S)
        assert len(found) == n, (name, len(found), n)
        assert seats == es.SEATS_2026[code], (name, seats)
        for full, acr, head in found:
            full, acr, head = html.unescape(full), html.unescape(acr), html.unescape(head)
            rows.append({"prov": code, "acronym": acr.strip(), "name": full.strip(), "head": head.strip(),
                         "lanes": lanes_of(acr, full)})
    return pd.DataFrame(rows)


def _alias(name: str) -> str:
    alias = {"A CORUNA": "15", "ALACANT/ALICANTE": "03", "ARABA/ALAVA": "01", "CASTELLO/CASTELLON": "12", "ILLES BALEARS": "07",
             "LAS PALMAS": "35", "SANTA CRUZ DE TENERIFE": "38", "VALENCIA/VALENCIA": "46"}
    return alias[norm(name)]


def main() -> None:
    d = parse(fetch())
    assert len(d) == 556 and d["prov"].nunique() == 52, (len(d), d["prov"].nunique())
    head = ("# Congress candidacies PRESENTED for 2026-11-29, per constituency (not yet proclaimed: boards proclaim 2026-11-02/03).\n"
            "# Source: BOE of 2026-10-28 (BOE-A-2026-22607) as republished by votomunicipal.es/candidaturas/. lanes = model lane(s) of the list;\n"
            "# '+' = one joint list for several lanes (the first lane is credited with its seats). Built by analysis/es_lists_2026.py.\n")
    OUT.write_text(head + d.sort_values(["prov", "lanes"], kind="stable").to_csv(index=False))
    print(OUT, len(d), "rows")


if __name__ == "__main__":
    main()
