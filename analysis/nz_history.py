"""Load historical (2014, 2017, 2020, 2023) Electoral Commission results for the NZ replay backtest.

Official voting-place CSVs (fetched by ``nz_history_fetch.py``; 2014 only has the HTML statistics parts) give, per
electorate, every ordinary voting place (advance + election day) and the special/overseas rows.  The Commission does
NOT archive the order in which places reported on election night, so replays use *simulated* count orders
(``count_order`` below) over these real voting-place results.
"""
from __future__ import annotations

import csv
import math
import os
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import newzealand as nz

HIST = Path(os.environ.get("NZ_HIST_DIR", Path.home() / "nz_v3_local" / "raw"))

# canonical key per (casefolded) official party name; unknown names get an upper-case slug
NAME_KEYS = {
    "act new zealand": "ACT", "aotearoa legalise cannabis party": "ALCP", "green party": "GP", "labour party": "LAB",
    "national party": "NAT", "new zealand first party": "NZF", "māori party": "TPMAO", "te pāti māori": "TPMAO",
    "the opportunities party (top)": "OPP", "opportunity party": "OPP", "conservative": "NC",
    "new conservative": "NC", "new conservatives": "NC", "mana": "MANA", "internet mana": "MANA",
    "internet party": "INT", "united future": "UF", "advance nz": "APN", "nz loyal": "NLY",
    "democrats for social credit": "DSC", "animal justice party": "AJP", "freedoms nz": "OFP",
}


def key_of(name: str) -> str:
    n = name.strip().casefold()
    n = n.replace("mÄori party", "māori party").replace("mäori party", "māori party")
    if n in NAME_KEYS:
        return NAME_KEYS[n]
    return re.sub(r"[^A-Z0-9]", "", name.upper())[:10]


# ---------------------------------------------------------------------------------------------- CSV readers

def _rows(path: Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))


def read_places(path: Path, header_tail: int = 2) -> tuple[str, list[str], pd.DataFrame]:
    """One official voting-place file (party or candidate) -> (electorate, column names, rows).

    ``header_tail`` trailing numeric columns follow the party/candidate columns: total valid and informal.
    Rows keep file order; ``kind`` is advance / election-day / special."""
    rows = _rows(path)
    electorate = re.sub(r"\s+\d+$", "", rows[1][0]).strip()
    names = [x.strip() for x in rows[2][2:-header_tail]]
    out, section, area = [], None, ""
    for r in rows[3:]:
        if not r:
            continue
        if len(r) == 1 and r[0] in ("Advance Voting Places", "Voting Places"):
            section = "advance" if r[0].startswith("Advance") else "election-day"
            area = ""
            continue
        if len(r) < len(names) + 4 or not r[1].strip() or r[1].strip().endswith(" Total"):
            continue
        if r[0].strip():
            area = r[0].strip()
        try:
            nums = [int(x or 0) for x in r[2:2 + len(names)]]
            valid = int(r[2 + len(names)] or 0)
        except ValueError:
            continue
        place = r[1].strip()
        special = any(x in place for x in ("Special Votes", "Votes Allowed", "less than 6 votes", "Overseas", "Postal Votes"))
        out.append({"area": area, "place": place, "kind": "special" if special else (section or "special"),
                    "valid": valid, "votes": nums})
    return electorate, names, pd.DataFrame(out)


def number_of_electorates(year: int) -> int:
    return len(list((HIST / str(year)).glob("party-votes-by-voting-place-*.csv")))


def summary(year: int) -> pd.DataFrame:
    d = nz.read_summary(HIST / str(year) / "overall-results-summary.csv")
    d["key"] = [key_of(x) for x in d["party"]]
    return d


def candidate_party_map(year: int, electorate: str, names: list[str], cand_totals: list[int],
                        pct: pd.DataFrame) -> list[str]:
    """Candidate column -> party key by matching the candidate's official total to the party's candidate-vote total in
    the electorate (``percentage-candidate-votes-for-registered-parties.csv``).  Unmatched -> ``IND``."""
    keys = ["IND"] * len(names)
    if electorate not in pct.index:
        return keys
    row = pct.loc[electorate]
    avail = {k: int(v) for k, v in row.items() if k != "Other" and v > 0}
    for i, t in enumerate(cand_totals):
        for k, v in list(avail.items()):
            if v == t:
                keys[i] = k
                del avail[k]
                break
    return keys


@lru_cache(maxsize=None)
def _pct_candidates(year: int) -> pd.DataFrame:
    rows = _rows(HIST / str(year) / "percentage-candidate-votes-for-registered-parties.csv")
    head = rows[1]
    cols = [(i, key_of(head[i]) if head[i] else "Other") for i in range(1, len(head) - 1, 2)]
    data = {}
    for r in rows[3:]:
        if not r or not r[0] or r[0].endswith("Totals") or r[0].strip().casefold().startswith("total"):
            continue
        try:
            data[r[0].strip()] = {k: int(r[i].replace(",", "")) for i, k in cols}
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(data).T


# ---------------------------------------------------------------------------------------------- election object

class Election:
    """Everything the replay needs for one election.

    ``party_keys``: list parties with a list vote (keys), ``units``: per electorate name, ``places``: per electorate
    DataFrame of ordinary places with party vote arrays, candidate vote arrays (aligned to [*party_keys, 'IND']),
    and ``special``: per-electorate special party/candidate vote arrays. ``final_*`` are official totals.
    """

    def __init__(self, year: int):
        self.year = year
        d = HIST / str(year)
        self.summary = summary(year)
        n = number_of_electorates(year)
        pct = _pct_candidates(year)
        self.party_keys: list[str] = []
        raw = {}
        for i in range(1, n + 1):
            pf, cf = d / f"party-votes-by-voting-place-{i}.csv", d / f"candidate-votes-by-voting-place-{i}.csv"
            el, pnames, pdf = read_places(pf)
            _, cnames, cdf = read_places(cf)
            raw[el] = (pnames, pdf, cnames, cdf)
            for nm in pnames:
                k = key_of(nm)
                if k not in self.party_keys:
                    self.party_keys.append(k)
        self.party_keys = sorted(self.party_keys)
        K = len(self.party_keys)
        pidx = {k: j for j, k in enumerate(self.party_keys)}
        self.electorates: list[str] = list(raw)
        self.places, self.special_pv, self.special_cv = {}, {}, {}
        self.final_pv, self.final_cv = {}, {}
        for el, (pnames, pdf, cnames, cdf) in raw.items():
            cand_total = np.array(cdf["votes"].tolist()).sum(0) if len(cdf) else np.zeros(len(cnames), int)
            ckeys = candidate_party_map(year, el, cnames, [int(x) for x in cand_total], pct)
            P = np.zeros((len(pdf), K))
            for r, v in enumerate(pdf["votes"]):
                for nm, x in zip(pnames, v):
                    P[r, pidx[key_of(nm)]] += x
            C = np.zeros((len(cdf), K + 1))
            for r, v in enumerate(cdf["votes"]):
                for ck, x in zip(ckeys, v):
                    C[r, pidx[ck] if ck in pidx else K] += x
            pk, ck_ = pdf["kind"].to_numpy(), cdf["kind"].to_numpy()
            pord, cord = pk != "special", ck_ != "special"
            # A place can appear in only one file (a place where electors cast party-only votes for this electorate,
            # or an electorate whose poll was cancelled and has no candidate votes yet): the missing side is zeros.
            def keyed(places, mask):
                seen, out = {}, []
                for i in np.where(mask)[0]:
                    n = seen[places[i]] = seen.get(places[i], 0) + 1
                    out.append(((places[i], n), i))
                return out
            pkey, ckey = dict(keyed(pdf["place"].tolist(), pord)), dict(keyed(cdf["place"].tolist(), cord))
            order = list(pkey) + [k for k in ckey if k not in pkey]
            self.places[el] = pd.DataFrame({
                "kind": [pk[pkey[k]] if k in pkey else ck_[ckey[k]] for k in order],
                "valid": [P[pkey[k]].sum() if k in pkey else 0.0 for k in order]})
            self.places[el]["P"] = [P[pkey[k]] if k in pkey else np.zeros(K) for k in order]
            self.places[el]["C"] = [C[ckey[k]] if k in ckey else np.zeros(K + 1) for k in order]
            self.special_pv[el], self.special_cv[el] = P[~pord].sum(0), C[~cord].sum(0)
            self.final_pv[el], self.final_cv[el] = P.sum(0), C.sum(0)
        self.winners = self._winners(d)

    def _winners(self, d: Path) -> dict[str, str]:
        w = nz.read_winners(d / "winning-electorate-candidates.csv").set_index("electorate")
        return {e: key_of(str(p)) for e, p in w["winner_party"].items()}

    # -- official national outcomes
    def final_party_votes(self) -> np.ndarray:
        return sum(self.final_pv.values())

    def ordinary_party_votes(self) -> np.ndarray:
        return sum(self.final_pv[e] - self.special_pv[e] for e in self.electorates)

    def advance_table(self, rename: dict[str, str] | None = None) -> pd.DataFrame:
        """Per electorate: ordinary voting places and party votes split into advance and election-day (as read by
        ``brunder.newzealand.advance_profile``).  ``rename`` maps this election's party keys to the 2026 keys."""
        rename = rename or {}
        K = len(self.party_keys)
        rows = {}
        for e in self.electorates:
            pl = self.places[e]
            P = np.array(list(pl["P"])).reshape(-1, K)
            adv = (pl["kind"] == "advance").to_numpy()
            r = {"adv_places": int(adv.sum()), "ed_places": int((~adv).sum())}
            for j, k in enumerate(self.party_keys):
                k = rename.get(k, k)
                r[f"adv_{k}"] = r.get(f"adv_{k}", 0) + int(P[adv, j].sum())
                r[f"ed_{k}"] = r.get(f"ed_{k}", 0) + int(P[~adv, j].sum())
            rows[e] = r
        return pd.DataFrame(rows).T.rename_axis("electorate")

    def official_seats(self) -> dict[str, int]:
        s = self.summary.groupby("key")["total_seats"].sum()
        return {k: int(s.get(k, 0)) for k in self.party_keys}


# ---------------------------------------------------------------------------------------------- 2014 (HTML only)

class _Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cur, self.cell = [], None, None

    def handle_starttag(self, t, a):
        if t == "tr":
            self.cur = []
        if t in ("td", "th"):
            self.cell = ""

    def handle_endtag(self, t):
        if t in ("td", "th") and self.cur is not None and self.cell is not None:
            self.cur.append(self.cell.strip()); self.cell = None
        if t == "tr" and self.cur is not None:
            self.rows.append(self.cur); self.cur = None

    def handle_data(self, d):
        if self.cell is not None:
            self.cell += d


def parse_2014(html_party: str, html_winners: str) -> pd.DataFrame:
    """2014 ``Votes for Registered Parties by Electorate`` HTML (column-major flat cells) -> electorate x party counts
    (+ ``valid`` and ``winner`` key).  Source: archive.electionresults.govt.nz/electionresults_2014/e9/html/e9_part4.html."""
    p = _Cells(); p.feed(html_party)
    r = [x[0] for x in p.rows if x and any(x)]
    heads = r[2:r.index("Total Valid Party Votes")]
    i = r.index("Informal Party Votes") + 1
    out = {}
    ncol = len(heads) + 2
    for block in range(2):
        names = []
        while not re.fullmatch(r"[\d,]+", r[i]):
            names.append(r[i]); i += 1
        n = len(names)
        cols = [[int(x.replace(",", "")) for x in r[i + c * n: i + (c + 1) * n]] for c in range(ncol)]
        i += ncol * n
        if block == 0:
            i += 1 + ncol                       # "General Electorate Totals" + one total per column
        for j, nm in enumerate(names):
            out[nm] = {key_of(h): cols[c][j] for c, h in enumerate(heads)}
            out[nm]["valid"] = cols[len(heads)][j]
    d = pd.DataFrame(out).T
    p2 = _Cells(); p2.feed(html_winners)
    win = {}
    for row in p2.rows:
        if len(row) >= 7 and row[3].isdigit():
            win[row[0]] = key_of(row[2])
    d["winner"] = pd.Series(win)
    return d
