"""Netherlands, Tweede Kamer election of 2025-10-29: parsers for the Kiesraad result files, the seat
allocation, the poll table parser and the prior-only projection (docs/netherlands.md).

No count over time exists for this election (nothing official or openly licensed was archived during
the night), so this module has no count model. The "model" is a projection made before any votes: the
2023 result, shifted toward the final polls, with a poll error calibrated on the 2012-2023 elections
(`analysis/nl_calibrate.py`), run through the seat allocation to give the chance of each market outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

import numpy as np
import pandas as pd

SEATS = 150

# id -> (name in the Kiesraad 2025 file, Polymarket label, colour). Polymarket's tie-break is the
# alphabetical order of the label (case-insensitive), so the labels are kept here.
LISTS = {
    "pvv": ("PVV (Partij voor de Vrijheid)", "PVV", "#1d4e89"),
    "glpvda": ("GROENLINKS / Partij van de Arbeid (PvdA)", "GL/PvdA", "#c4123b"),
    "vvd": ("VVD", "VVD", "#ee7c1b"),
    "nsc": ("Nieuw Sociaal Contract (NSC)", "NSC", "#1c8aa0"),
    "d66": ("D66", "D66", "#2fa84f"),
    "bbb": ("BBB", "BBB", "#8fb43a"),
    "cda": ("CDA", "CDA", "#2b7a4b"),
    "sp": ("SP (Socialistische Partij)", "SP", "#e0302b"),
    "denk": ("DENK", "Denk", "#00a6a6"),
    "pvdd": ("Partij voor de Dieren", "PvdD", "#6a9a28"),
    "fvd": ("Forum voor Democratie", "FvD", "#7b2d26"),
    "sgp": ("Staatkundig Gereformeerde Partij (SGP)", "SGP", "#e9740e"),
    "cu": ("ChristenUnie", "CU", "#2d9cdb"),
    "volt": ("Volt", "Volt", "#6c3d91"),
    "ja21": ("JA21", "JA21", "#0a3a6a"),
    "50plus": ("50PLUS", "50PLUS", "#8a6bbe"),
}
PARTY_IDS = tuple(LISTS)
LABEL = {k: v[1] for k, v in LISTS.items()}

# names used in the Kiesraad files of 2012-2025 -> canonical list id. GroenLinks and the PvdA ran
# separately until 2021 ("gl", "pvda"); `merge_gl_pvda` turns those into the joint list of 2023/2025.
NAME_TO_ID = {
    "PVV (Partij voor de Vrijheid)": "pvv", "VVD": "vvd", "CDA": "cda", "Christen Democratisch Appèl (CDA)": "cda",
    "D66": "d66", "Democraten 66 (D66)": "d66", "SP (Socialistische Partij)": "sp",
    "GROENLINKS": "gl", "Partij van de Arbeid (P.v.d.A.)": "pvda",
    "GROENLINKS / Partij van de Arbeid (PvdA)": "glpvda", "ChristenUnie": "cu",
    "Partij voor de Dieren": "pvdd", "50PLUS": "50plus", "Staatkundig Gereformeerde Partij (SGP)": "sgp",
    "DENK": "denk", "Forum voor Democratie": "fvd", "JA21": "ja21", "Volt": "volt", "BBB": "bbb", "BIJ1": "bij1",
    "Nieuw Sociaal Contract": "nsc", "Nieuw Sociaal Contract (NSC)": "nsc",
}
ELECTION_DATE = {2012: date(2012, 9, 12), 2017: date(2017, 3, 15), 2021: date(2021, 3, 17),
                 2023: date(2023, 11, 22), 2025: date(2025, 10, 29)}


def list_id(name: str) -> str:
    return NAME_TO_ID.get(name.strip(), "other")


# ------------------------------------------------------------------ result files
def read_uitslag(path) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Kiesraad `TK20xx_uitslag.csv` (2023, 2025) -> (votes by gemeente x list id, official seats by list id,
    gemeente table with name, province code, valid votes and electorate).

    Rows: every 'G' (gemeente, including the Hague's "kiezers buitenland" NBSB) and 'O' (Bonaire, Sint
    Eustatius, Saba) region. Lists that are not one of the 2025 lists are pooled in `other`."""
    d = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)
    reg = d[d["RegioCode"].str[0].isin(["G", "O"])]
    lv = reg[reg["VeldType"] == "LijstAantalStemmen"].copy()
    lv["id"] = lv["LijstNaam"].map(list_id)
    lv["v"] = lv["Waarde"].astype(int)
    votes = lv.pivot_table(index="RegioCode", columns="id", values="v", aggfunc="sum", fill_value=0)
    info = reg.drop_duplicates("RegioCode").set_index("RegioCode")[["Regio", "OuderRegioCode", "GrootOuderRegioCode"]]
    info.columns = ["name", "kieskring", "province"]
    for field, col in (("AantalGeldigeStemmen", "valid"), ("Kiesgerechtigden", "electorate"), ("Opkomst", "turnout")):
        s = reg[reg["VeldType"] == field].set_index("RegioCode")["Waarde"].astype(int)
        info[col] = s
    seats_rows = d[(d["VeldType"] == "LijstAantalZetels") & (d["RegioCode"].str[0] == "L")]
    seats = pd.Series({list_id(r.LijstNaam): int(r.Waarde) for r in seats_rows.itertuples()}, dtype=int)
    return votes.sort_index(), seats, info.loc[votes.index]


def read_gemeente_votes(path) -> pd.DataFrame:
    """Kiesraad `TKxxxx_Stemmen_Per_Lijst_Per_Gemeente.csv` (2012, 2017, 2021) -> votes by gemeente code x list id."""
    d = pd.read_csv(path, sep=";")
    d["id"] = d["PartijNaam"].map(list_id)
    return d.pivot_table(index="GemeenteCode", columns="id", values="AantalStemmen", aggfunc="sum", fill_value=0)


def merge_gl_pvda(v) -> pd.DataFrame | pd.Series:
    """GroenLinks + PvdA -> the joint list (applies to a votes table or a national vote series)."""
    v = v.copy()
    if isinstance(v, pd.Series):
        if "gl" in v or "pvda" in v:
            v["glpvda"] = v.get("gl", 0) + v.get("pvda", 0)
            v = v.drop([c for c in ("gl", "pvda") if c in v])
        return v
    if "gl" in v or "pvda" in v:
        v["glpvda"] = v.get("gl", 0) + v.get("pvda", 0)
        v = v.drop(columns=[c for c in ("gl", "pvda") if c in v])
    return v


def read_kieskring_eml_totals(files) -> pd.Series:
    """National votes by list name from the EML 'Telling' files of the 20 kieskringen (the 2017 dataset's gemeente
    files cover only 279 of 388 gemeenten, the kieskring files all of the country)."""
    import xml.etree.ElementTree as ET
    tot: dict[str, int] = {}
    tag = lambda e: e.tag.rsplit("}", 1)[-1]
    for f in files:
        for tv in (e for e in ET.parse(f).getroot().iter() if tag(e) == "TotalVotes"):
            for sel in (c for c in tv if tag(c) == "Selection"):
                kids = [tag(c) for c in sel]
                if "AffiliationIdentifier" in kids and "Candidate" not in kids:
                    name = next(x.text for x in sel.iter() if tag(x) == "RegisteredName")
                    tot[name] = tot.get(name, 0) + int(next(x.text for x in sel.iter() if tag(x) == "ValidVotes"))
    return pd.Series(tot, dtype=int)


def national_by_name(year: int, raw) -> pd.Series:
    """National valid votes by list name (as printed in the Kiesraad file), from the files under `raw`
    (= $BRUNDER_DATA/raw/netherlands, layout in docs/netherlands.md)."""
    from pathlib import Path
    raw = Path(raw)
    yy = year % 100
    if year in (2023, 2025):
        d = pd.read_csv(raw / f"x{yy}" / f"TK{year}_uitslag.csv", sep=";", dtype=str, keep_default_na=False)
        d = d[(d["RegioCode"] == "L528") & (d["VeldType"] == "LijstAantalStemmen")]
        return pd.Series(d["Waarde"].astype(int).to_numpy(), index=d["LijstNaam"].to_numpy())
    if year == 2017:
        return read_kieskring_eml_totals(sorted((raw / "eml17").rglob("Telling_TK2017_kieskring_*.eml.xml")))
    d = pd.read_csv(raw / f"x{yy}" / f"TK{year}_Stemmen_Per_Lijst_Per_Gemeente.csv", sep=";")
    return d.groupby("PartijNaam")["AantalStemmen"].sum()


def national_by_id(by_name: pd.Series) -> pd.Series:
    """Collapse a by-name national vote series to canonical list ids (everything else -> 'other')."""
    return by_name.groupby([list_id(n) for n in by_name.index]).sum()


def seats_by_name(by_name: pd.Series) -> pd.Series:
    """Seats of every list that stood, from the national votes."""
    return pd.Series(allocate(by_name.to_numpy(dtype=float)), index=by_name.index)


def gemeente_code(region_code: str) -> int:
    """'G0014' -> 14, 'O9001' -> 9001."""
    return int(region_code[1:])


# ------------------------------------------------------------------ seat allocation
def allocate(votes, seats: int = SEATS, pool: int | None = None) -> np.ndarray:
    """Tweede Kamer seat allocation (Kieswet, since 2017: no list combinations). Votes: (lists,) or
    (draws, lists), the votes of every list that stood, *including* lists that cannot win a seat, because the
    quota is all valid votes / 150. A list needs at least one quota to take part; seats go by D'Hondt's
    highest average (the full seats of the law's first step are the same seats D'Hondt gives first).
    `pool` is the column of a pool of small lists (counted in the quota, never given a seat).
    Ties in the average are broken in column order (the law draws lots; irrelevant outside simulations)."""
    v = np.atleast_2d(np.asarray(votes, dtype=float))
    quota = v.sum(axis=1, keepdims=True) / seats
    elig = v >= quota
    if pool is not None:
        elig[:, pool] = False
    base = np.where(elig, v, 0.0)
    n = np.zeros(v.shape, dtype=np.int64)
    rows = np.arange(v.shape[0])
    for _ in range(seats):
        j = np.argmax(base / (n + 1), axis=1)
        n[rows, j] += 1
    return n[0] if np.ndim(votes) == 1 else n


def rank_order(seats: np.ndarray, labels: list[str]) -> np.ndarray:
    """Rank of each list by Polymarket's rule: most seats first, ties by the alphabetical order of the list's
    abbreviation (case-insensitive). seats: (lists,) or (draws, lists). Returns 0-based ranks, same shape."""
    s = np.atleast_2d(seats)
    tb = np.argsort(np.argsort([x.lower() for x in labels]))      # alphabetical position of each list
    key = s.astype(np.int64) * 10_000 - tb[None, :]                # higher key ranks first
    rank = np.argsort(np.argsort(-key, axis=1), axis=1)
    return rank[0] if np.ndim(seats) == 1 else rank


# ------------------------------------------------------------------ polls (Wikipedia tables)
class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict]]] = []
        self._stack: list[list[list[dict]]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
            self._stack[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"t": "", "cs": int(a.get("colspan") or 1), "rs": int(a.get("rowspan") or 1)}
            self._row.append(self._cell)
        elif tag in ("sup", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
        elif tag in ("td", "th"):
            self._cell = None
        elif tag in ("sup", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell["t"] += data


def html_tables(html: str) -> list[list[list[str]]]:
    """Every <table> of an HTML page as a rectangular grid of cell texts (row/colspans expanded)."""
    p = _Tables()
    p.feed(html)
    out = []
    for rows in p.tables:
        grid, pend = [], {}
        for r, row in enumerate(rows):
            line, c = [], 0
            for cell in row:
                while (r, c) in pend:
                    line.append(pend.pop((r, c)))
                    c += 1
                for _ in range(cell["cs"]):
                    line.append(cell["t"].strip())
                    for rr in range(1, cell["rs"]):
                        pend[(r + rr, c)] = cell["t"].strip()
                    c += 1
            while (r, c) in pend:
                line.append(pend.pop((r, c)))
                c += 1
            grid.append(line)
        out.append(grid)
    return out


POLL_COLUMN = {"PVV": "pvv", "GL/PvdA": "glpvda", "GL–PvdA": "glpvda", "VVD": "vvd", "NSC": "nsc", "D66": "d66",
               "BBB": "bbb", "CDA": "cda", "SP": "sp", "Denk": "denk", "DENK": "denk", "PvdD": "pvdd", "FvD": "fvd",
               "SGP": "sgp", "CU": "cu", "Volt": "volt", "JA21": "ja21", "50+": "50plus", "GL": "gl", "PvdA": "pvda",
               "BIJ1": "bij1", "BVNL": "bvnl"}
_MONTH = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def last_date(text: str) -> date | None:
    """End date of a fieldwork-date cell ('27–28 Oct 2025', '31 Oct–3 Nov 2025', '15 Mar 2017')."""
    m = re.findall(r"(\d{1,2})\s+([A-Z][a-z]{2})[a-z]*\.?\s+(\d{4})", text)
    if not m:
        return None
    d, mon, y = m[-1]
    return date(int(y), _MONTH[mon], int(d))


def parse_polls(grid: list[list[str]]) -> pd.DataFrame:
    """One poll table of the English Wikipedia's 'Opinion polling for the <year> Dutch general election' pages
    (seats by list; columns 'Polling firm' and 'Fieldwork date' or 'Date', in either order) -> one row per poll:
    pollster, end (fieldwork end date), and seats by list id. The results row and rows with no date are dropped.
    A joint GL-PvdA column that spans two cells (2023 page) is read once."""
    head = next(i for i, r in enumerate(grid) if any(c.startswith("Polling") for c in r))
    names = grid[head]
    ci = {c: i for i, c in enumerate(names) if c in POLL_COLUMN}
    # the 2023 page lists the joint column twice (colspan); read the first occurrence
    cols: dict[str, int] = {}
    for c, i in ci.items():
        cols.setdefault(POLL_COLUMN[c], i)
    pf = next(i for i, c in enumerate(names) if c.startswith("Polling"))
    dt = next(i for i, c in enumerate(names) if c.startswith("Fieldwork") or c == "Date")
    rows = []
    for r in grid[head + 1:]:
        if len(r) <= max(cols.values()) or not r[dt]:
            continue
        end = last_date(r[dt])
        firm = re.sub(r"\s*Archived.*$", "", r[pf]).strip()
        if end is None or "election" in firm.lower() or "result" in firm.lower() or firm == "Kiesraad":
            continue
        seats = {}
        for pid, i in cols.items():
            m = re.match(r"^\d+", r[i].replace(" ", ""))
            seats[pid] = int(m.group()) if m else np.nan
        rows.append({"pollster": firm, "end": end, **seats})
    return pd.DataFrame(rows)


def final_poll(polls: pd.DataFrame, election: date, days: int = 7) -> tuple[pd.Series, pd.DataFrame]:
    """Final polling average: each pollster's latest poll that ended 1..`days` days before the election (so the
    exit polls of election day are out), averaged over pollsters. Returns (average seats by list id, the polls used)."""
    e, ends = pd.Timestamp(election), pd.to_datetime(polls["end"])
    w = polls[(ends < e) & (ends >= e - pd.Timedelta(days=days))]
    used = w.sort_values("end").groupby("pollster").tail(1)
    ids = [c for c in polls.columns if c not in ("pollster", "end")]
    return used[ids].mean(skipna=True).fillna(0.0), used.sort_values("end")


# ------------------------------------------------------------------ prior-only projection
@dataclass(frozen=True)
class Settings:
    """Chosen on the 2012, 2017, 2021 and 2023 elections only (analysis/nl_calibrate.py, configs/nl2025_v1.toml)."""
    w_prev: float = 0.0        # weight of the previous election's result against the final polls in the central share
    err: float = 0.30          # sd of a list's share error = err * sqrt(share * (1 - share))
    other: float = 0.015       # share of valid votes for lists that win no seat (with-seat lists share the rest)
    draws: int = 20_000


def central_shares(poll_seats: pd.Series | None, prev: pd.Series, s: Settings) -> pd.Series:
    """Central vote share of each list (over the lists in `prev`'s index) as a share of the valid votes of the lists that
    win seats: the polls' seat shares mixed with the previous election's vote shares, weight `w_prev` on the latter.
    With no polls this is the previous result."""
    pv = prev / prev.sum()
    if poll_seats is None:
        return pv
    ps = poll_seats.reindex(pv.index).fillna(0.0)
    ps = ps / ps.sum()
    b = (1 - s.w_prev) * ps + s.w_prev * pv
    return b / b.sum()


def simulate_seats(central: pd.Series, s: Settings, rng: np.random.Generator) -> np.ndarray:
    """Seats by list for `s.draws` simulated elections: shares = central + independent normal errors of sd
    err * sqrt(b(1-b)), floored at 0 and renormalised, a fixed `other` pool for the lists that win no seat (it counts in the quota), then `allocate`."""
    b = central.to_numpy()
    sd = s.err * np.sqrt(b * (1 - b))
    x = np.clip(b[None, :] + rng.normal(size=(s.draws, len(b))) * sd[None, :], 0.0, None)
    x = x / x.sum(axis=1, keepdims=True) * (1 - s.other)
    votes = np.concatenate([x, np.full((s.draws, 1), s.other)], axis=1)      # last column: the other pool, never eligible
    n = allocate(votes, pool=votes.shape[1] - 1)
    return n[:, :-1]


def swing_projection(votes_prev: pd.DataFrame, prev_total: pd.Series, central: pd.Series) -> pd.DataFrame:
    """Gemeente votes projected before the count: each list's previous gemeente votes times
    (central national share / previous national share)."""
    pv = prev_total.reindex(central.index).fillna(0)
    pv = pv / pv.sum()
    f = (central / pv.where(pv > 0)).fillna(0.0)
    return votes_prev[central.index] * f


# ------------------------------------------------------------------ market outcomes
def outcome_probabilities(seats: np.ndarray, ids: list[str], band_lists: dict[str, list[tuple[str, int, int]]]) -> dict:
    """Probabilities of the market outcomes from simulated seats (draws x lists, columns in `ids` order)."""
    labels = [LABEL[i] for i in ids]
    rank = rank_order(seats, labels)
    out = {}
    for j, i in enumerate(ids):
        out[f"most:{i}"] = float((rank[:, j] == 0).mean())
        out[f"second:{i}"] = float((rank[:, j] == 1).mean())
        for name, lo, hi in band_lists.get(i, []):
            out[f"band:{i}:{name}"] = float(((seats[:, j] >= lo) & (seats[:, j] <= hi)).mean())
    return out


def parse_band(title: str) -> tuple[int, int]:
    """Seat band from a Polymarket group title: '≤26', '27–29', '≥33', '<16', '20+'."""
    t = title.replace("\u2013", "-").replace("\u2014", "-").strip()
    if t.startswith("≤"):
        return 0, int(t[1:])
    if t.startswith("<"):
        return 0, int(t[1:]) - 1
    if t.startswith("≥"):
        return int(t[1:]), SEATS
    if t.endswith("+"):
        return int(t[:-1]), SEATS
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f"unrecognised seat band {title!r}")


# ------------------------------------------------------------------ map units
PROVINCES = {"P20": ("gr", "Groningen"), "P21": ("fr", "Fryslân"), "P22": ("dr", "Drenthe"), "P23": ("ov", "Overijssel"),
             "P24": ("fl", "Flevoland"), "P25": ("ge", "Gelderland"), "P26": ("ut", "Utrecht"),
             "P27": ("nh", "Noord-Holland"), "P28": ("zh", "Zuid-Holland"), "P29": ("ze", "Zeeland"),
             "P30": ("nb", "Noord-Brabant"), "P31": ("li", "Limburg")}


def unit_id(province_code: str, gemeente: int) -> str:
    """`nl-<province>-<CBS gemeente code, 4 digits>`, e.g. ('P20', 14) -> nl-gr-0014 (Groningen)."""
    return f"nl-{PROVINCES[province_code][0]}-{int(gemeente):04d}"
