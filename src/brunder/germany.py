"""Germany, Bundestag election of 23 February 2025: parsers, prior, count-night model, market definitions.

Data (all Bundeswahlleiterin, Datenlizenz Deutschland - Namensnennung 2.0 for the open data; the election-night
pages are official communications of the Bundeswahlleiterin, read from Internet Archive captures):

* kerg2.csv                    final result by Wahlkreis / Land / Bund (Erst- and Zweitstimmen),
* btwkr25_umrechnung_btw21.csv 2021 result recomputed on the 299 Wahlkreise of 2025 (the model's prior),
* result pages bund-99.html, bund-99/land-N.html, bund-99/land-N/wahlkreis-M.html: server-rendered tables with the
  caption "Zwischenergebnis: DD.MM.YYYY HH:MM:SS Uhr, N von 299 Wahlkreisen ausgezählt" (Berlin time).

The race is the Zweitstimmen (list vote) share of each party; the Bundestag seat shares, the 5% threshold and
the three-seat rule follow from those shares (`seat_shares`).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd

from brunder.paths import RAW

RAW_DE = RAW / "germany"
LANDS = {1: "SH", 2: "HH", 3: "NI", 4: "HB", 5: "NW", 6: "HE", 7: "RP", 8: "BW", 9: "BY", 10: "SL", 11: "BE",
         12: "BB", 13: "MV", 14: "SN", 15: "ST", 16: "TH"}
LAND_NAMES = {"SH": "Schleswig-Holstein", "HH": "Hamburg", "NI": "Niedersachsen", "HB": "Bremen",
              "NW": "Nordrhein-Westfalen", "HE": "Hessen", "RP": "Rheinland-Pfalz", "BW": "Baden-Württemberg",
              "BY": "Bayern", "SL": "Saarland", "BE": "Berlin", "BB": "Brandenburg", "MV": "Mecklenburg-Vorpommern",
              "SN": "Sachsen", "ST": "Sachsen-Anhalt", "TH": "Thüringen"}
# Parties modelled; everything else is "other". CDU and CSU run in disjoint Länder: the race is the Union.
PARTIES = ["Union", "AfD", "SPD", "GRÜNE", "Die Linke", "BSW", "FDP"]
OTHER = "Übrige"

_STAMP = re.compile(r"(?:Zwischenergebnis|Intermediate result|Endergebnis|Final result)[^<]*?"
                    r"(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d):(\d\d)[^<]*?(\d+) (?:von|of) (\d+)", re.S)


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.caption = ""
        self._in_cap = False
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _num(s: str) -> float | None:
    """A vote count as printed: thousands separated by '.' (de) or ',' (en); '-' is no entry."""
    s = re.sub(r"[\s.,]", "", s)
    return float(s) if s.isdigit() else None


def parse_page(html: str) -> dict | None:
    """Parse one result page: {"stamp": Berlin-time Timestamp, "counted": n, "total": 299, "rows": {name: (erst,
    zweit)}, "system": {...}} or None if the page has no interim/final result table (e.g. not counted yet)."""
    m = _STAMP.search(html)
    if not m:
        return None
    d, mo, y, hh, mm, ss, n, tot = m.groups()
    p = _Table()
    p.feed(html)
    out = {"stamp": pd.Timestamp(f"{y}-{mo}-{d} {hh}:{mm}:{ss}"), "counted": int(n), "total": int(tot), "rows": {}}
    for r in p.rows:
        if len(r) >= 7 and r[0] and r[0] not in ("Merkmal", "Characteristic"):
            out["rows"][r[0]] = (_num(r[1]), _num(r[4]))
    return out if out["rows"] else None


SYSTEM_DE = {"Wahlberechtigte": "electorate", "Wählende": "voters", "Ungültige": "invalid", "Gültige": "valid"}
SYSTEM_EN = {"Eligible voters": "electorate", "Persons entitled to vote": "electorate", "Voters": "voters",
             "Invalid": "invalid", "Valid": "valid"}


def to_votes(page: dict) -> dict[str, float]:
    """Second votes of a parsed page: system rows and the modelled parties (CDU + CSU summed to 'Union'); the rest
    of the parties is not kept (the model derives 'Übrige' from the valid votes)."""
    v: dict[str, float] = {}
    for name, (_, z) in page["rows"].items():
        if z is None:
            continue
        key = SYSTEM_DE.get(name) or SYSTEM_EN.get(name)
        if key:
            v[key] = z
        elif party_of(name) != OTHER:
            v[party_of(name)] = v.get(party_of(name), 0.0) + z
    return v


# ------------------------------------------------------------------------------------------------ party names
def party_of(name: str) -> str:
    """Model party of a list name as printed on the pages and in kerg2 (Brandenburg prints 'GRÜNE/B 90')."""
    n = name.strip().upper()
    if n in ("CDU", "CSU"):
        return "Union"
    if n.startswith("GRÜNE") or n.startswith("GREENS"):
        return "GRÜNE"
    if n in ("DIE LINKE", "LINKE", "LEFT PARTY", "THE LEFT"):
        return "Die Linke"
    return {"AFD": "AfD", "SPD": "SPD", "FDP": "FDP", "BSW": "BSW"}.get(n, OTHER)


ALL = PARTIES + [OTHER]


def _wide(rows: pd.DataFrame, col: str) -> pd.DataFrame:
    """Second votes of kerg2 rows (one election, one level) as one row per area: electorate, valid and ALL parties."""
    sys_ = rows[rows["Gruppenart"] == "System-Gruppe"]
    out = pd.DataFrame({"electorate": sys_[sys_["Gruppenname"] == "Wahlberechtigte"].set_index("Gebietsnummer")[col]})
    out["valid"] = sys_[(sys_["Gruppenname"] == "Gültige") & (sys_["Stimme"] == 2)].set_index("Gebietsnummer")[col]
    pt = rows[(rows["Gruppenart"] == "Partei") & (rows["Stimme"] == 2)].copy()
    pt["party"] = pt["Gruppenname"].map(party_of)
    pv = pt.groupby(["Gebietsnummer", "party"])[col].sum(min_count=1).unstack().reindex(columns=PARTIES).fillna(0.0)
    out = out.join(pv)
    out[OTHER] = out["valid"] - out[PARTIES].sum(axis=1)
    return out


def read_kerg2(path: Path, col: str = "Anzahl") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(Wahlkreise, Länder) second votes from a Bundeswahlleiterin kerg2.csv; `col` 'Anzahl' = this election,
    'VorpAnzahl' = the previous election on this election's constituencies. Wahlkreise carry 'land' (1..16)."""
    d = pd.read_csv(path, sep=";", skiprows=9, decimal=",", encoding="utf-8-sig")
    wk = d[d["Gebietsart"] == "Wahlkreis"]
    w = _wide(wk, col)
    w["land"] = wk.drop_duplicates("Gebietsnummer").set_index("Gebietsnummer")["UegGebietsnummer"].astype(int)
    la = _wide(d[d["Gebietsart"] == "Land"], col)
    return w.sort_index(), la.sort_index()


def read_umrechnung(path: Path) -> pd.DataFrame:
    """The 2021 Bundestag result recomputed on the 2025 constituencies (btwkr25_umrechnung_btw21.csv): second votes
    per Wahlkreis, same columns as read_kerg2 (BSW did not exist: 0), plus 'land' (1..16)."""
    d = pd.read_csv(path, sep=";", header=None, encoding="utf-8-sig", dtype=str, comment=None)
    h = int(d.index[d[0] == "Wkr-Nr."][0])
    names = d.iloc[h].tolist()
    d = d.iloc[h + 2:].reset_index(drop=True)
    d = d[pd.to_numeric(d[0], errors="coerce").between(1, 299)]
    abbr = {v: k for k, v in LANDS.items()}
    out = pd.DataFrame(index=pd.to_numeric(d[0]).astype(int))
    num = lambda i: pd.to_numeric(d[i]).fillna(0).to_numpy()  # noqa: E731
    out["land"] = d[1].map(abbr).to_numpy()
    out["electorate"] = num(3)
    out["valid"] = num(12)                                   # Gültige, Zweitstimmen
    for i in range(13, len(names), 2):
        n = names[i]
        if n in ("CDU", "CSU", "AfD", "SPD", "FDP", "DIE LINKE", "GRÜNE"):
            out[party_of(n)] = (out[party_of(n)] if party_of(n) in out else 0.0) + num(i + 1)
    out["BSW"] = 0.0
    out[OTHER] = out["valid"] - out[PARTIES].sum(axis=1)
    return out[["land", "electorate", "valid", *ALL]].sort_index()


# ------------------------------------------------------------------------------------------------ election night
BERLIN = "Europe/Berlin"


def _vec(p: dict, votes: dict[str, float]) -> dict:
    v = {k: votes.get(k, 0.0) for k in PARTIES}
    valid = votes.get("valid", 0.0)
    v[OTHER] = max(valid - sum(v.values()), 0.0)
    return {"t": pd.Timestamp(p["stamp"]).tz_localize(BERLIN).tz_convert("UTC"), "k": p["counted"],
            "electorate": votes.get("electorate", np.nan), "valid": valid, **v}


def read_night(folder: Path) -> dict:
    """Parse the archived result pages of one night (files `<capture>_<lang>_<unit>.html`, see analysis/de_fetch.py)
    into {"bund": rows, "land": {n: rows}, "wk": {n: row}}. A row is the page's own stamp (UTC), the number of
    Wahlkreise counted, the electorate, valid second votes and the votes of ALL parties. Pages without a result
    table (before the first Wahlkreis reported) are skipped; of identical stamps one row is kept."""
    bund, land, wk = {}, {}, {}
    for f in sorted(folder.glob("*.html")):
        _, lang, unit = f.stem.split("_", 2)
        p = parse_page(f.read_text(encoding="utf-8"))
        if p is None:
            continue
        row = _vec(p, to_votes(p))
        if not row["valid"] or not row["electorate"] or row["electorate"] != row["electorate"]:
            continue                                          # english pages carry no 'electorate' on some layouts
        if unit == "bund":
            bund[row["t"]] = row
        elif unit.startswith("land-"):
            land.setdefault(int(unit[5:]), {})[row["t"]] = row
        else:
            n = int(unit[10:])
            if n not in wk or row["t"] < wk[n]["t"]:
                wk[n] = row
    return {"bund": pd.DataFrame(sorted(bund.values(), key=lambda r: r["t"])),
            "land": {n: pd.DataFrame(sorted(d.values(), key=lambda r: r["t"])) for n, d in land.items()},
            "wk": pd.DataFrame(wk.values(), index=list(wk)).sort_index()}


# ------------------------------------------------------------------------------------------------ model
from dataclasses import dataclass  # noqa: E402

NP = len(ALL)
COLS = ["electorate", "valid", *ALL]          # column order of every count vector below


@dataclass(frozen=True)
class Params:
    kappa: float = 3_000_000.0    # pseudo-votes shrinking a Land's party swing toward the national one
    theta0: float = 1_000_000.0   # pseudo-votes shrinking the turnout scale (counted vs prior expectation) to 1
    bsw_c: float = 0.3            # BSW prior share = bsw_c * (AfD + Die Linke) prior share (only the pattern matters)
    sd_a: float = 0.2            # predictive sd of a party's final share = sd_a * share * f_rem ** sd_b ...
    sd_b: float = 0.9
    sd_floor: float = 0.0001      # ... plus this much (share points / 100) of irreducible error


def make_prior(votes: pd.DataFrame, electorate: pd.Series, bsw_c: float) -> pd.DataFrame:
    """Per-Wahlkreis prior for a night: `votes` = previous election's second votes on this election's constituencies
    (read_umrechnung / read_kerg2 'VorpAnzahl'), `electorate` = this election's eligible voters (published before the
    election). pi = prior shares of ALL parties (BSW: a proxy proportional to AfD + Die Linke, see Params.bsw_c),
    tau0 = prior valid votes per eligible voter."""
    pi = votes[ALL].div(votes["valid"], axis=0)
    pi["BSW"] = bsw_c * (pi["AfD"] + pi["Die Linke"])
    out = pd.DataFrame({"land": votes["land"].astype(int), "E": electorate.reindex(votes.index).astype(float),
                        "tau0": votes["valid"] / votes["electorate"]})
    return out.join(pi)


def _row(r) -> np.ndarray:
    return np.array([r[c] for c in COLS], dtype=float)


def state_at(night: dict, prior: pd.DataFrame, t: pd.Timestamp) -> dict | None:
    """What the official pages show at time t, per Land: base[L] (17 x len(COLS), index 1..16) = counted electorate,
    valid votes, party votes; k[L] = Wahlkreise counted; known = Wahlkreise whose own page stamp is <= t. Land pages
    are used at their own stamps; the latest national page's surplus over the Land sum is spread over the unfinished
    Länder in proportion to their uncounted electorate. None before the first page."""
    wk = night["wk"]
    kn = wk[wk["t"] <= t]
    base = np.zeros((17, len(COLS)))
    k = np.zeros(17)
    seen = len(kn) > 0
    for L in range(1, 17):
        kw = kn[prior.loc[kn.index, "land"] == L] if len(kn) else kn
        lp = night["land"].get(L)
        lp = lp[lp["t"] <= t] if lp is not None else None
        if lp is not None and len(lp):
            r = lp.iloc[-1]
            seen = True
            ex = kw[kw["t"] > r["t"]]
            base[L] = _row(r) + (ex[COLS].to_numpy(float).sum(axis=0) if len(ex) else 0)
            k[L] = r["k"] + len(ex)
        elif len(kw):
            base[L] = kw[COLS].to_numpy(float).sum(axis=0)
            k[L] = len(kw)
    bp = night["bund"][night["bund"]["t"] <= t]
    if len(bp):
        seen = True
        r = bp.iloc[-1]
        ex = kn[kn["t"] > r["t"]]
        bt = _row(r) + (ex[COLS].to_numpy(float).sum(axis=0) if len(ex) else 0)
        D = bt - base.sum(axis=0)
        if D[0] > 0 and D[1] > 0:
            D = np.clip(D, 0, None)
            Etot = prior.groupby("land")["E"].sum().reindex(range(1, 17)).to_numpy()
            w = np.clip(Etot - base[1:, 0], 0, None)
            if w.sum() > 0:
                base[1:] += np.outer(w / w.sum(), D)
                k[1:] += (r["k"] + len(ex) - k.sum()) * w / w.sum()
    return {"base": base, "k": k, "known": kn} if seen else None


def project(st: dict, night: dict, prior: pd.DataFrame, p: Params = Params()) -> dict:
    """Projected final second-vote shares of ALL parties, and of every Wahlkreis.

    Wahlkreise whose own page is archived (known) are exact. Per Land the other counted votes are an unidentified block
    assumed to resemble the Land's unidentified Wahlkreise. Additive swing per party: the Land's counted share minus
    the prior share of its counted votes, shrunk toward the national swing by `kappa` pseudo-votes, is added to the
    prior of every uncounted Wahlkreis (floored, renormalised over parties)."""
    base, known = st["base"], st["known"]
    pi = prior[ALL].to_numpy()
    E = prior["E"].to_numpy()
    tau0 = prior["tau0"].to_numpy()
    land = prior["land"].to_numpy()
    ids = prior.index.to_numpy()
    is_known = np.isin(ids, known.index.to_numpy())
    Nw = E * tau0                                    # expected valid votes of every Wahlkreis
    num = np.zeros((17, NP))
    den = np.zeros((17, NP))
    EU = np.zeros(17)
    Erem = np.zeros(17)
    tau_u = np.zeros(17)
    exp_counted = 0.0
    for L in range(1, 17):
        m = land == L
        U = m & ~is_known
        mk = is_known & m
        kn = known.reindex(ids[mk])
        Kv = kn[COLS].to_numpy(float).sum(axis=0) if mk.any() else np.zeros(len(COLS))
        cu = np.clip(base[L] - Kv, 0, None)          # counted but not individually known
        EU[L] = E[U].sum()
        tau_u[L] = Nw[U].sum() / EU[L] if EU[L] > 0 else 0.0
        pu = (pi[U] * Nw[U, None]).sum(axis=0) / max(Nw[U].sum(), 1.0)
        num[L] = base[L, 2:]
        den[L] = (pi[mk] * kn["valid"].to_numpy(float)[:, None]).sum(axis=0) + cu[1] * pu
        Erem[L] = max(EU[L] - cu[0], 0.0)
        exp_counted += (kn["electorate"].to_numpy(float) * tau0[mk]).sum() + cu[0] * tau_u[L]
    Nc = base[1:, 1]
    theta = (Nc.sum() + p.theta0) / (exp_counted + p.theta0)
    W = np.maximum(base[:, 1], 1.0)[:, None]
    dl = (num - den) / W                             # counted share minus the prior share of the counted votes
    dn = (num.sum(axis=0) - den.sum(axis=0)) / max(Nc.sum(), 1.0)
    d_hat = (W * dl + p.kappa * dn) / (W + p.kappa)
    d_hat[base[:, 1] <= 0] = dn
    s_wk = np.clip(pi + d_hat[land], 0.0005, None) * (pi > 0)
    s_wk = s_wk / np.where(s_wk.sum(axis=1, keepdims=True) > 0, s_wk.sum(axis=1, keepdims=True), 1.0)
    rem = np.zeros(NP)
    Nrem = np.zeros(17)
    for L in range(1, 17):
        U = (land == L) & ~is_known
        if EU[L] <= 0:
            continue
        frac = Erem[L] / EU[L]                       # share of the unidentified Wahlkreise's electorate not yet counted
        Nrem[L] = frac * Nw[U].sum() * theta
        rem += frac * theta * (s_wk[U] * Nw[U, None]).sum(axis=0)
    V = base[1:, 2:].sum(axis=0) + rem
    Ntot = Nc.sum() + Nrem.sum()
    return {"share": V / Ntot, "f_rem": Nrem.sum() / Ntot, "counted": base[1:, 0].sum() / E.sum(),
            "k": st["k"].sum(), "delta": d_hat, "Erem": Erem, "Nrem": Nrem, "theta": theta,
            "raw": base[1:, 2:].sum(axis=0) / max(Nc.sum(), 1.0), "s_wk": s_wk, "ids": ids}


def party_sd(share: np.ndarray, f_rem: float, p: Params) -> np.ndarray:
    """Predictive sd of each party's final share given the projection and the remaining vote fraction."""
    return np.maximum(p.sd_floor, p.sd_a * share * f_rem ** p.sd_b)


def correlation(corr6: list[list[float]] | None, parties6: list[str], bsw_rho: float = -0.1) -> np.ndarray:
    """Correlation of the standardised errors of ALL parties: the 2021 estimate for the parties that existed (BSW
    gets `bsw_rho` with every other party, nothing to estimate it from)."""
    C = np.full((NP, NP), bsw_rho)
    np.fill_diagonal(C, 1.0)
    if corr6 is not None:
        idx = [ALL.index(x) for x in parties6]
        C[np.ix_(idx, idx)] = np.asarray(corr6)
    w, v = np.linalg.eigh(C)
    return (v * np.clip(w, 1e-6, None)) @ v.T


def draw_shares(share: np.ndarray, f_rem: float, p: Params, chol: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Draws of the final shares: projection + sd * correlated normal, renormalised to sum to 1. `z` is a fixed
    (n, NP) standard normal array (common random numbers across frames), `chol` the Cholesky of `correlation`."""
    s = share[None, :] + (z @ chol.T) * party_sd(share, f_rem, p)[None, :]
    s = np.clip(s, 0.0, None)
    return s / s.sum(axis=1, keepdims=True)


def event_prob(d: np.ndarray, ev: dict) -> float:
    """P(event) from share draws d (n, NP); ev = {"kind": "max"|"second"|"range"|"atleast", "party", ...}.
    max/second: highest / second-highest second-vote share (seats are allocated in proportion to the second votes
    of the parties passing 5%, so the order of the shares is the order of the seats)."""
    j = ALL.index(ev["party"])
    k = ev["kind"]
    if k in ("max", "second"):
        rank = (d > d[:, [j]]).sum(axis=1)
        return float((rank == (0 if k == "max" else 1)).mean())
    if k == "range":
        return float(((d[:, j] >= ev["lo"]) & (d[:, j] < ev["hi"])).mean())
    if k == "atleast":
        return float((d[:, j] >= ev["x"]).mean())
    raise ValueError(k)


def load_config(path: Path) -> dict:
    import tomllib
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def params_of(cfg: dict) -> Params:
    m = cfg["model"]
    return Params(kappa=m["kappa"], theta0=m["theta0"], bsw_c=m["bsw_c"], sd_a=m["sd_a"], sd_b=m["sd_b"],
                  sd_floor=m["sd_floor"])


def all_probs(d: np.ndarray, markets: list[dict]) -> dict[str, float]:
    """P(YES) of every market from share draws d (n, NP); keys are the market slugs."""
    rank = np.stack([(d > d[:, [j]]).sum(axis=1) for j in range(NP)], axis=1)
    out = {}
    for m in markets:
        j = ALL.index(m["party"])
        k = m["kind"]
        if k == "max":
            out[m["market"]] = float((rank[:, j] == 0).mean())
        elif k == "second":
            out[m["market"]] = float((rank[:, j] == 1).mean())
        else:
            out[m["market"]] = event_prob(d, {**m})
    return out


def rule(m: dict, share: np.ndarray) -> bool:
    """The market's YES condition on one vector of shares (final or raw counted)."""
    return bool(event_prob(share[None, :], m) > 0.5)
