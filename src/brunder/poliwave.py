"""Poliwave live-results pages (poliwave.com/<cc>/live/<race>): a public, real-time projection.

The page embeds the race's count history as a JS object literal:
  history:[{t:"<ISO UTC>",counted:<% sections>,valid:<n>,votes:{"13":..,"22":..},
            shares:{..},projected:{"13":<proj %>,"22":..}|null}, ...]
The count fields match TSE partials. Whether the stored `projected` values are what readers
saw live (vs recomputed later) is unverified — see docs/research_design.md. Pages are served to
browser user agents only and may be overwritten, so archive the HTML.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pandas as pd

from .paths import RAW

BASE = "https://www.poliwave.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
DIR = RAW / "poliwave"

_ENTRY = re.compile(r'\{t:"(?P<t>[^"]+)",counted:(?P<counted>[\d.]+)(?P<rest>.*?)(?=\},\{t:"|\}\])', re.S)
_PAIR = re.compile(r'"(\w+)":([\d.]+)')


def download(path: str = "br/live/presidente-2026") -> Path:
    r = httpx.get(f"{BASE}/{path}", headers=UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    DIR.mkdir(parents=True, exist_ok=True)
    out = DIR / (path.split("/live/")[-1].replace("/", "_") + ".html")
    out.write_text(r.text)
    return out


def _obj(rest: str, key: str) -> dict[str, float]:
    m = re.search(key + r":(\{[^}]*\}|null)", rest)
    return {} if m is None or m.group(1) == "null" else {k: float(v) for k, v in _PAIR.findall(m.group(1))}


def history(html_file: Path) -> pd.DataFrame:
    """One row per published update: counted % of sections, valid votes, shares, projection."""
    s = html_file.read_text()
    i = s.find("history:[{t:")
    if i < 0:
        return pd.DataFrame()
    rows = []
    for m in _ENTRY.finditer(s, i):
        rest = m.group("rest")
        valid = re.search(r"valid:(\d+)", rest)
        row = {"time": pd.Timestamp(m.group("t")), "counted_pct": float(m.group("counted")),
               "valid": int(valid.group(1)) if valid else None}
        row |= {f"share_{k}": v for k, v in _obj(rest, "shares").items()}
        row |= {f"proj_{k}": v for k, v in _obj(rest, "projected").items()}
        rows.append(row)
        if s.startswith("}]", m.end()):
            break
    return pd.DataFrame(rows)
