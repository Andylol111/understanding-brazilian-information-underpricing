"""Ingest official New Zealand election files and build the publishable 2023 electorate prior.

The results host can block automated clients. Download the three CSVs in a normal browser from the exact
URLs in docs/newzealand.md, put them in one directory, then run:

    BRUNDER_DATA=~/brunder-data-local uv run brunder nz ingest --year 2023 --source ~/Downloads/nz2023
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nz_fetch.py public-prior

``references`` makes five polite requests to the documented 2026 XML media feed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import httpx
import pandas as pd

from brunder import newzealand as nz
from brunder.paths import RAW, ROOT

UA = {"User-Agent": "brunder-research/0.1 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}


def references() -> None:
    out = RAW / "newzealand" / "2026" / "xml"
    out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        for name in ("election.xml", "electorates.xml", "parties.xml", "candidates.xml", "statistics.xml"):
            r = c.get(f"{nz.BASE}/{name}"); r.raise_for_status()
            (out / name).write_bytes(r.content)
            print(out / name)


def public_prior() -> Path:
    raw = RAW / "newzealand" / "2023"
    votes = nz.read_electorate_party_votes(raw / "votes-for-registered-parties-by-electorate.csv")
    winners = nz.read_winners(raw / "winning-electorate-candidates.csv").set_index("electorate")
    rev = {v: k for k, v in nz.PRIOR_PARTIES.items()}
    out = pd.DataFrame({"electorate": votes["electorate"], "valid": votes["valid"].astype(int)})
    for key in nz.PARTY_KEYS:
        source = nz.PRIOR_PARTIES.get(key)
        out[key] = votes[source].astype(int) if source in votes else 0
    out["winner"] = out["electorate"].map(winners["winner_party"]).map(rev).fillna("")
    # The official file names differ slightly for two parties.
    out.loc[out["electorate"].map(winners["winner_party"]) == "Te Pāti Māori", "winner"] = "TPMAO"
    out.loc[out["electorate"].map(winners["winner_party"]) == "Green Party", "winner"] = "GP"
    out.loc[out["electorate"].map(winners["winner_party"]) == "National Party", "winner"] = "NAT"
    out.loc[out["electorate"].map(winners["winner_party"]) == "Labour Party", "winner"] = "LAB"
    out.loc[out["electorate"].map(winners["winner_party"]) == "ACT New Zealand", "winner"] = "ACT"
    wp = pd.to_numeric(winners["winner_pct"].astype(str).str.rstrip("%"), errors="coerce") / 100
    out["winner_margin"] = (
        pd.to_numeric(winners["majority"], errors="coerce") * wp /
        pd.to_numeric(winners["winner_votes"], errors="coerce")).reindex(out["electorate"]).to_numpy()
    dest = ROOT / "data_public" / "nz2023_electorates.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = ("# Derived from New Zealand Electoral Commission official 2023 results; Crown copyright.\n"
            "# Reproduction permitted with accurate attribution; source URLs in docs/newzealand.md.\n" +
            out.to_csv(index=False))
    dest.write_text(body)
    print(dest)
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["references", "public-prior"])
    a = ap.parse_args()
    references() if a.step == "references" else public_prior()
