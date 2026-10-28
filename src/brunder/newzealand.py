"""New Zealand 2026 general election: official XML feed, MMP seats, nowcast and dashboard export.

The election-night source is the Electoral Commission's public XML media feed documented in its 2026
Media Kit. The recorder polls the five small reference files and 71 electorate aggregate files; it does
not crawl the thousands of voting-place result files. Each snapshot is saved only when its bytes change.

Seat allocation follows Electoral Act 1993 ss 191--193: parties with at least 5% of valid party votes or
one electorate seat enter the Sainte-Lague allocation; quotients use divisors 1, 3, 5, ...; electorate
seats above a party's proportional entitlement are overhang seats.  Seats won by independents or parties
without a party list reduce the 120-seat proportional pool.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import kalshi, polymarket, snap
from .paths import RAW, ROOT as ROOT_DIR

EID = "nz2026"
TAG = "nz_2026"
BASE = "https://media.election.net.nz/xml"
POLL_CLOSE = "2026-11-07T06:00:00Z"       # 19:00 NZDT (UTC+13)
PUBLIC_PRIOR = ROOT_DIR / "data_public" / "nz2023_electorates.csv"
RAW_NZ = RAW / "newzealand"
SNAP_ROOT = RAW / "snap" / TAG
PARTY_KEYS = ("ACT", "AJP", "ALCP", "APN", "FPT", "GP", "LAB", "NAT", "NC", "NLY", "NZF",
              "OFP", "OPP", "TPMAO", "TTTP", "VNZ", "WRP")
COLORS = {"NAT": "#00529f", "LAB": "#d82a20", "GP": "#098137", "ACT": "#f6c343",
          "NZF": "#111111", "TPMAO": "#b2001d", "OPP": "#28a9a1"}

# 2025 boundaries changed these names. This is deliberately only a name crosswalk, not an assertion that
# the polygons are identical; unmatched/reconfigured seats retain the named predecessor's 2023 composition.
CROSSWALK = {"East Cape": "East Coast", "Glendene": "Kelston", "Henderson": "New Lynn",
             "Kapiti": "Ōtaki", "Kenepuru": "Mana", "Mt Maunganui": "Bay of Plenty",
             "Waitākere": "Te Atatū", "Wellington Bays": "Rongotai", "Wellington North": "Ōhāriu",
             "Ōtāhuhu": "Panmure-Ōtāhuhu"}

PRIOR_PARTIES = {"ACT": "ACT New Zealand", "AJP": "Animal Justice Party", "ALCP": "Aotearoa Legalise Cannabis Party",
                 "GP": "Green Party", "LAB": "Labour Party", "NAT": "National Party",
                 "NC": "New Conservatives", "NLY": "NZ Loyal", "NZF": "New Zealand First Party",
                 "OFP": "Freedoms NZ", "OPP": "The Opportunities Party (TOP)",
                 "TPMAO": "Te Pāti Māori", "WRP": "Women’s Rights Party"}


def load_config(path: Path | str) -> dict:
    path = Path(path)
    with open(path, "rb") as f:
        local = tomllib.load(f)
    parent = local.pop("extends", None)
    if parent:
        out = load_config(path.parent / parent)
        out.pop("_path", None)
        local["markets"] = [*out.get("markets", []), *local.get("markets", [])]
        out.update(local)
    else:
        out = local
    out["_path"] = str(path)
    return out


# -------------------------------------------------------------------------------------- MMP allocation

def allocate_many(votes: np.ndarray, electorate_seats: np.ndarray, total: int = 120,
                  threshold: float = .05, independent_seats: int | np.ndarray = 0) -> np.ndarray:
    """Allocate MMP seats for every row of ``votes``.

    ``votes`` and ``electorate_seats`` are draws × parties (one-dimensional inputs are accepted).
    ``independent_seats`` also covers electorate winners from parties that did not submit a party list.
    A quotient tie is resolved by column order; real official ties are resolved by lot and are vanishingly
    unlikely in simulations.
    """
    V = np.atleast_2d(np.asarray(votes, dtype=float))
    E = np.asarray(electorate_seats, dtype=np.int64)
    if E.ndim == 1:
        E = np.broadcast_to(E, V.shape)
    if E.shape != V.shape:
        raise ValueError("votes and electorate_seats must have the same shape")
    ind = np.broadcast_to(np.asarray(independent_seats, dtype=np.int64), len(V))
    if (V < 0).any() or (E < 0).any() or (ind < 0).any() or (ind > total).any():
        raise ValueError("votes/seats must be non-negative and independent seats cannot exceed the House")
    denom = V.sum(axis=1, keepdims=True)
    qualify = (V >= threshold * denom) | (E > 0)
    ent = np.zeros_like(E)
    rows = np.arange(len(V))
    for _ in range(total):
        active = ent.sum(axis=1) < (total - ind)
        if not active.any():
            break
        q = np.where(qualify, V / (2 * ent + 1), -np.inf)
        j = q.argmax(axis=1)
        ent[rows[active], j[active]] += 1
    return np.maximum(ent, E)


def allocate(votes: dict[str, float], electorate_seats: dict[str, int], total: int = 120,
             threshold: float = .05, independent_seats: int = 0) -> dict[str, int]:
    names = list(dict.fromkeys([*votes, *electorate_seats]))
    v = np.array([votes.get(k, 0) for k in names], dtype=float)
    e = np.array([electorate_seats.get(k, 0) for k in names], dtype=np.int64)
    s = allocate_many(v, e, total, threshold, independent_seats)[0]
    return {k: int(x) for k, x in zip(names, s)}


# -------------------------------------------------------------------------------------- official CSVs

def read_summary(path: Path | str) -> pd.DataFrame:
    """Official ``overall-results-summary.csv`` -> party, party votes, list/electorate/total seats."""
    rows = list(csv.reader(Path(path).read_text(encoding="utf-8-sig").splitlines()))
    out, in_parties = [], False
    for r in rows:
        if r and r[0].strip() == "Registered Parties with List":
            in_parties = True
            continue
        if in_parties and r and (r[0].startswith("Unregistered") or r[0].startswith("Registered Parties, no")):
            break
        if in_parties and len(r) >= 6 and r[2].strip().replace(",", "").isdigit():
            out.append({"party": r[0].strip(), "list_seats": int(r[1] or 0),
                        "party_votes": int(r[2].replace(",", "")), "electorate_seats": int(r[5] or 0)})
    if not out:
        raise ValueError(f"not an Electoral Commission overall-results summary: {path}")
    d = pd.DataFrame(out)
    d["total_seats"] = d["list_seats"] + d["electorate_seats"]
    return d


def read_electorate_party_votes(path: Path | str) -> pd.DataFrame:
    """Official ``votes-for-registered-parties-by-electorate.csv``; totals rows are removed."""
    d = pd.read_csv(path, skiprows=1, encoding="utf-8-sig")
    d = d[~d["Electorate"].astype(str).str.endswith("Totals")].copy()
    d = d.rename(columns={"Electorate": "electorate", "Total Valid Party Votes": "valid",
                          "Informal Party Votes": "informal", "Total Party Votes Counted": "total"})
    return d


def read_winners(path: Path | str) -> pd.DataFrame:
    """Official ``winning-electorate-candidates.csv``."""
    d = pd.read_csv(path, skiprows=1, encoding="utf-8-sig")
    return d.rename(columns={"Electoral District": "electorate", "Electorate Candidate": "candidate",
                             "Party": "winner_party", "Valid Votes": "winner_votes", "Majority": "majority"})


def read_voting_place_party_votes(path: Path | str) -> pd.DataFrame:
    """One official ``party-votes-by-voting-place-N.csv`` -> voting-place rows, including special-vote rows."""
    rows = list(csv.reader(Path(path).read_text(encoding="utf-8-sig").splitlines()))
    if len(rows) < 4 or "Party Vote Details" not in ",".join(rows[1]):
        raise ValueError(f"not an Electoral Commission party voting-place file: {path}")
    electorate = re.sub(r"\s+\d+$", "", rows[1][0]).strip()
    parties = [x.strip() for x in rows[2][2:-2]]
    out, section, area = [], None, ""
    for r in rows[3:]:
        if not r:
            continue
        if len(r) == 1 and r[0] in ("Advance Voting Places", "Voting Places"):
            section = "advance" if r[0].startswith("Advance") else "election-day"
            area = ""
            continue
        if len(r) < len(parties) + 4 or not r[1].strip() or r[1].endswith(" Total"):
            continue
        if r[0].strip():
            area = r[0].strip()
        vals = r[2:2 + len(parties)]
        try:
            nums = [int(x or 0) for x in vals]
            valid, informal = int(r[2 + len(parties)] or 0), int(r[3 + len(parties)] or 0)
        except ValueError:
            continue
        place = r[1].strip()
        special = any(x in place for x in ("Special Votes", "Votes Allowed", "less than 6 votes"))
        kind = "special" if special else (section or "special")
        row = {"electorate": electorate, "kind": kind, "area": area, "place": place,
               "valid": valid, "informal": informal}
        row.update(dict(zip(parties, nums)))
        out.append(row)
    return pd.DataFrame(out)


# -------------------------------------------------------------------------------------- XML feed

def _root(data: bytes | str) -> ET.Element:
    return ET.fromstring(data)


def parse_parties(data: bytes | str) -> pd.DataFrame:
    rows = [{"p_no": int(x.attrib["p_no"]), "abbrev": x.findtext("abbrev", ""),
             "name": x.findtext("short_name", ""), "registered": x.findtext("registered") == "yes"}
            for x in _root(data).findall("party")]
    return pd.DataFrame(rows).set_index("p_no")


def parse_electorates(data: bytes | str) -> pd.DataFrame:
    rows = [{"e_no": int(x.attrib["e_no"]), "name": x.findtext("electorate_name", "")}
            for x in _root(data).findall("electorate")]
    d = pd.DataFrame(rows).set_index("e_no")
    d["region"] = np.where(d.index <= 64, "gen", "maori")
    d["unit_id"] = [f"nz-{r}-{i:02d}" for i, r in zip(d.index, d["region"])]
    return d


def parse_candidates(data: bytes | str) -> pd.DataFrame:
    rows = [{"c_no": int(x.attrib["c_no"]), "name": x.findtext("candidate_name", ""),
             "e_no": int(x.findtext("electorate", "0") or 0), "p_no": int(x.findtext("party", "0") or 0),
             "list_no": int(x.findtext("list_no", "0") or 0)} for x in _root(data).findall("candidate")]
    return pd.DataFrame(rows).set_index("c_no")


@dataclass
class ElectorateCount:
    e_no: int
    updated: str
    final: bool
    places: int
    places_total: int
    votes_cast: int
    party_votes: dict[str, int]
    candidate_votes: dict[str, int]

    @property
    def f(self) -> float:
        return self.places / self.places_total if self.places_total else 0.0


def parse_electorate(data: bytes | str, parties: pd.DataFrame, candidates: pd.DataFrame) -> ElectorateCount:
    x = _root(data)
    stat = x.find("statistics")
    num = lambda tag: int(float(stat.findtext(tag, "0") or 0))
    pv = {}
    for p in x.findall("./partyvotes/party"):
        n = int(p.attrib["p_no"])
        if n in parties.index:
            pv[parties.at[n, "abbrev"]] = int(p.findtext("votes", "0") or 0)
    cv = {}
    for c in x.findall("./candidatevotes/candidate"):
        n = int(c.attrib["c_no"])
        if n in candidates.index:
            pn = int(candidates.at[n, "p_no"])
            key = parties.at[pn, "abbrev"] if pn in parties.index else "IND"
            cv[key] = cv.get(key, 0) + int(c.findtext("votes", "0") or 0)
    return ElectorateCount(int(x.attrib["e_no"]), x.attrib.get("updated", ""), x.attrib.get("final") == "true",
                           num("total_voting_places_counted"), num("total_voting_places"),
                           num("total_votes_cast"), pv, cv)


def parse_election(data: bytes | str, parties: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    x = _root(data)
    st = {c.tag: float(c.text or 0) for c in x.find("statistics")}
    rows = []
    for p in x.findall("./partystatus/party"):
        n = int(p.attrib["p_no"])
        if n not in parties.index:
            continue
        rows.append({"party": parties.at[n, "abbrev"], **{c.tag: float(c.text or 0) for c in p}})
    return st, pd.DataFrame(rows).set_index("party")


def feed_urls() -> list[str]:
    refs = ["election.xml", "electorates.xml", "parties.xml", "candidates.xml", "statistics.xml"]
    return [f"{BASE}/{x}" for x in refs] + [f"{BASE}/e{i:02d}/e{i:02d}.xml" for i in range(1, 72)]


def _fetch_references() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    headers = {"User-Agent": "brunder-research/0.1 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
        blobs = {k: c.get(f"{BASE}/{k}.xml").raise_for_status().content for k in ("parties", "electorates", "candidates")}
    return parse_parties(blobs["parties"]), parse_electorates(blobs["electorates"]), parse_candidates(blobs["candidates"])


# -------------------------------------------------------------------------------------- prior + model

def load_prior(path: Path | str = PUBLIC_PRIOR) -> pd.DataFrame:
    return pd.read_csv(path, comment="#").set_index("electorate")


def prior_units(electorates: pd.DataFrame, prior: pd.DataFrame | None = None) -> pd.DataFrame:
    p = load_prior() if prior is None else prior.copy()
    rows = []
    nat = p[list(PARTY_KEYS)].sum() / p["valid"].sum()
    for e_no, e in electorates.iterrows():
        old = CROSSWALK.get(e["name"], e["name"])
        if old in p.index:
            r = p.loc[old]
            shares = r[list(PARTY_KEYS)].to_numpy(float) / max(float(r["valid"]), 1)
            valid, winner = float(r["valid"]), str(r["winner"])
        else:
            shares, valid, winner = nat.to_numpy(float), float(p["valid"].median()), ""
        row = {"e_no": e_no, "unit_id": e["unit_id"], "name": e["name"], "region": e["region"],
               "V": valid, "winner": winner}
        row.update({k: v for k, v in zip(PARTY_KEYS, shares)})
        rows.append(row)
    return pd.DataFrame(rows).set_index("e_no")


@dataclass(frozen=True)
class Settings:
    prior_votes: float = 50_000.0
    own_votes: float = 2_000.0
    growth_votes: float = 100_000.0
    party_log_sd: float = 0.817
    floor_log_sd: float = 0.015
    candidate_log_sd: float = 0.25
    incumbent_bonus: float = 1.20
    lock_fraction: float = 0.85
    draws: int = 4000


def settings(cfg: dict) -> Settings:
    m = cfg.get("model", {})
    return Settings(**{f.name: m[f.name] for f in fields(Settings) if f.name in m})


@dataclass
class Projection:
    parties: list[str]
    counted: np.ndarray
    mean: np.ndarray
    unit: pd.DataFrame
    unit_shares: np.ndarray
    candidate_shares: np.ndarray
    counted_frac: float
    expected_total: float


def empty_counts(units: pd.DataFrame) -> pd.DataFrame:
    d = pd.DataFrame(index=units.index)
    for c in ("f", "places", "places_total", "valid"):
        d[c] = 0.0
    for p in (*PARTY_KEYS, "IND"):
        if p != "IND":
            d[f"pv_{p}"] = 0.0
        d[f"cv_{p}"] = 0.0
    return d


def counts_frame(items: list[ElectorateCount], units: pd.DataFrame) -> pd.DataFrame:
    d = empty_counts(units)
    for x in items:
        if x.e_no not in d.index:
            continue
        d.loc[x.e_no, ["f", "places", "places_total", "valid"]] = [x.f, x.places, x.places_total,
                                                                       sum(x.party_votes.values())]
        for p, v in x.party_votes.items():
            if f"pv_{p}" in d:
                d.at[x.e_no, f"pv_{p}"] = v
        for p, v in x.candidate_votes.items():
            if f"cv_{p}" in d:
                d.at[x.e_no, f"cv_{p}"] = v
    return d


def project(units: pd.DataFrame, count: pd.DataFrame, st: Settings) -> Projection:
    parties = list(PARTY_KEYS)
    U, C = units.reindex(count.index), count.reindex(units.index).fillna(0.0)
    prior_s = U[parties].to_numpy(float, copy=True)
    # New 2026 parties have no 2023 column. A tiny symmetric seed keeps the multiplicative transition
    # model defined without giving them a material pre-count share (17 seeds together are < 0.002%).
    prior_s = np.maximum(prior_s, 1e-6)
    prior_s /= np.maximum(prior_s.sum(1, keepdims=True), 1e-12)
    V = U["V"].to_numpy(float)
    f = np.clip(C["f"].to_numpy(float), 0, 1)
    cv = C[[f"pv_{p}" for p in parties]].to_numpy(float)
    valid = cv.sum(1)
    expected_in = f * V
    growth = (valid.sum() + st.growth_votes) / (expected_in.sum() + st.growth_votes)
    prior_nat_votes = (V[:, None] * prior_s).sum(0)
    expected_party_in = (expected_in[:, None] * prior_s).sum(0)
    observed = cv.sum(0)
    ratio = (observed + st.prior_votes * prior_nat_votes / prior_nat_votes.sum()) / (
        expected_party_in + st.prior_votes * prior_nat_votes / prior_nat_votes.sum())
    ratio = np.clip(ratio, .05, 20)
    pred = prior_s * ratio[None, :]
    pred /= np.maximum(pred.sum(1, keepdims=True), 1e-12)
    own = np.divide(cv, valid[:, None], out=np.zeros_like(cv), where=valid[:, None] > 0)
    w = valid / (valid + st.own_votes)
    comp = w[:, None] * own + (1 - w)[:, None] * pred
    comp /= np.maximum(comp.sum(1, keepdims=True), 1e-12)
    rem_n = np.maximum((1 - f) * V * growth, 0)
    rem = rem_n[:, None] * comp
    mean = observed + rem.sum(0)
    final_u = cv + rem
    final_u /= np.maximum(final_u.sum(1, keepdims=True), 1e-12)
    candidate_keys = [*parties, "IND"]
    cand = C[[f"cv_{p}" for p in candidate_keys]].to_numpy(float)
    cand_tot = cand.sum(1)
    cand_share = np.divide(cand, cand_tot[:, None], out=np.zeros_like(cand), where=cand_tot[:, None] > 0)
    places_total = C["places_total"].to_numpy(float)
    counted_frac = (C["places"].sum() / places_total.sum()) if places_total.sum() else 0.0
    out = U[["unit_id", "name", "region", "V", "winner"]].copy()
    out["f"], out["valid"], out["proj_nat"] = f, valid, final_u[:, parties.index("NAT")]
    out["raw_nat"] = np.divide(cv[:, parties.index("NAT")], valid, out=np.full(len(U), np.nan), where=valid > 0)
    return Projection(parties, observed, mean, out, final_u, cand_share, float(counted_frac), float(mean.sum()))


def simulate(p: Projection, st: Settings, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    base = p.mean / max(p.mean.sum(), 1)
    sigma = st.floor_log_sd + st.party_log_sd * math.sqrt(max(0.0, 1 - p.counted_frac))
    z = rng.standard_normal((st.draws, len(base))) * sigma
    sh = np.exp(np.log(np.maximum(base, 1e-8))[None, :] + z)
    sh /= sh.sum(1, keepdims=True)
    votes = sh * p.expected_total
    ratio = sh / np.maximum(base[None, :], 1e-12)
    # The last candidate column is IND (including candidates whose party did not submit a list). It can win an
    # electorate but has no party-vote baseline and is excluded from party market rankings.
    seats = np.zeros((st.draws, len(base) + 1), dtype=np.int64)
    for i, (_, u) in enumerate(p.unit.iterrows()):
        party_score = p.unit_shares[i][None, :] * ratio
        score = np.column_stack([party_score, np.zeros(st.draws)])
        f = float(u["f"])
        if p.candidate_shares[i].sum() > 0:
            blend = min(1.0, f / max(st.lock_fraction, 1e-9))
            score = (1 - blend) * score + blend * p.candidate_shares[i][None, :]
        elif u["winner"] in p.parties:
            score[:, p.parties.index(u["winner"])] *= 1 + (st.incumbent_bonus - 1) * (1 - f)
        noise = st.candidate_log_sd * math.sqrt(max(0.0, 1 - f))
        if noise:
            score *= np.exp(noise * rng.standard_normal(score.shape))
        win = score.argmax(1)
        seats[np.arange(st.draws), win] += 1
    return votes, seats


def seat_draws(p: Projection, st: Settings, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    votes, electorate_all = simulate(p, st, rng)
    electorate, independent = electorate_all[:, :-1], electorate_all[:, -1]
    return votes, electorate, allocate_many(votes, electorate, independent_seats=independent)


def outcomes(votes: np.ndarray, seats: np.ndarray, parties: list[str], markets: list[dict]) -> dict[str, np.ndarray]:
    ix = {p: i for i, p in enumerate(parties)}
    key = seats.astype(float) * 1e12 + votes
    order = np.argsort(-key, axis=1)
    total = votes.sum(1)
    out = {}
    for m in markets:
        kind = m["kind"]
        party = m.get("party")
        if kind == "most_seats":
            out[m["rid"]] = (order[:, 0] == ix.get(party, -1)).astype(float)
        elif kind == "rank":
            out[m["rid"]] = (order[:, int(m["rank"]) - 1] == ix.get(party, -1)).astype(float)
        elif kind == "seats_band":
            s = seats[:, ix[party]] if party in ix else np.zeros(len(seats))
            out[m["rid"]] = ((s >= int(m["lo"])) & (s <= int(m["hi"]))).astype(float)
        elif kind == "margin_band":
            margin = 100 * (votes[:, ix[m["a"]]] - votes[:, ix[m["b"]]]) / total
            lo, hi = float(m["lo"]), float(m["hi"])
            out[m["rid"]] = ((margin >= lo) & (margin < hi)).astype(float)
        elif kind == "other_popular":
            a, b = ix[m["a"]], ix[m["b"]]
            out[m["rid"]] = ((votes.argmax(1) != a) & (votes.argmax(1) != b)).astype(float)
        elif kind == "vote_ahead":
            out[m["rid"]] = (votes[:, ix[m["a"]]] > votes[:, ix[m["b"]]]).astype(float)
        else:
            raise ValueError(f"unknown market kind {kind}")
    return out


# -------------------------------------------------------------------------------------- snapshot archive

class Archive:
    def __init__(self, root: Path | str = SNAP_ROOT):
        self.root = Path(root)
        self.files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        self.refresh()

    def refresh(self) -> None:
        out: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        index = self.root / "index.jsonl"
        if index.exists():
            for line in index.read_text().splitlines():
                try:
                    x = json.loads(line); p = self.root / x["file"]
                    if p.exists():
                        out.setdefault(x["key"], []).append((pd.Timestamp(x["t"]).tz_convert("UTC"), p))
                except Exception:
                    continue
        for k in out:
            out[k].sort()
        self.files = out

    def times(self) -> list[pd.Timestamp]:
        return sorted({t for rows in self.files.values() for t, _ in rows})

    def at(self, key: str, t: pd.Timestamp) -> Path | None:
        rows = self.files.get(key, [])
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= t: lo = mid + 1
            else: hi = mid
        return rows[lo - 1][1] if lo else None

    @staticmethod
    def read(path: Path) -> bytes:
        b = path.read_bytes()
        return gzip.decompress(b) if path.suffix == ".gz" else b


def _key(url: str) -> str:
    return snap._name(url)


def _iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, n=4):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, path)


def _asof(series: pd.Series, grid: pd.DatetimeIndex) -> list[float | None]:
    if series is None or series.empty:
        return [None] * len(grid)
    s = series.sort_index(); s = s[~s.index.duplicated(keep="last")]
    x = s.reindex(s.index.union(grid)).ffill().reindex(grid)
    return [None if not np.isfinite(v) else float(v) for v in x.to_numpy(float)]


def _market_info(cfg: dict) -> tuple[dict[str, dict], dict[str, str]]:
    info, tokens = {}, {}
    wanted = {m["id"] for m in cfg["markets"]}
    for ev in sorted({m["event"] for m in cfg["markets"]}):
        for m in polymarket.event(ev)["markets"]:
            if m["slug"] not in wanted:
                continue
            ids = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds") or []
            info[m["slug"]] = m
            if ids: tokens[m["slug"]] = ids[0]
    return info, tokens


def _book_prices(arc: Archive, path: Path | None) -> dict[str, float]:
    if path is None: return {}
    out = {}
    for k, b in json.loads(arc.read(path)).items():
        bids, asks = b.get("bids") or [], b.get("asks") or []
        bid = max((float(x["price"]) for x in bids), default=np.nan)
        ask = min((float(x["price"]) for x in asks), default=np.nan)
        last = b.get("last_trade_price")
        out[k] = (bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) and ask - bid <= .10 else float(last or np.nan)
    return out


def _kalshi_prices(arc: Archive, path: Path | None) -> dict[str, float]:
    if path is None: return {}
    return {k: kalshi.prob_from_book(v) for k, v in json.loads(arc.read(path)).items()}


def _tail(timeline: dict[str, list], minutes: int = 60) -> dict:
    out, first = {}, []
    for k, rows in timeline.items():
        if rows:
            cut = pd.Timestamp(rows[-1]["t"]) - pd.Timedelta(minutes=minutes)
            v = [r for r in rows if pd.Timestamp(r["t"]) > cut]
            out[k] = v; first.append(v[0]["t"])
        else: out[k] = []
    return {**out, "tail": True, "since": min(first) if first else None}


def _empty_stats(reason: str) -> dict:
    keys = ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage", "evidence")
    return {**{k: None for k in keys}, "reasons": {k: reason for k in keys}}


def _race(m: dict, info: dict[str, dict], token: str | None = None) -> dict:
    question = info.get(m["id"], {}).get("question", m.get("question", m["id"]))
    yes = m.get("yes_name", "Yes")
    markets = [{"platform": "polymarket", "id": m["id"], "question": question, "yes_is": "a",
                "token_yes": token}]
    if m.get("kalshi"):
        markets.append({"platform": "kalshi", "id": m["kalshi"], "question": m.get("kalshi_question", question),
                        "yes_is": "a"})
    return {"race_id": m["rid"], "name": m.get("name", question), "region": None,
            "a": {"id": "yes", "name": yes, "color": COLORS.get(m.get("party"), "#c4501b")},
            "b": {"id": "no", "name": m.get("no_name", "No"), "color": "#777777"}, "markets": markets}


def meta(cfg: dict, refs, info: dict, tokens: dict, phase: str, probs: dict | None = None) -> dict:
    _, elect, _ = refs
    races = [_race(m, info, tokens.get(m["id"])) for m in cfg["markets"]]
    if phase == "pre-count":
        for r in races: r["precount"] = {"P": _r((probs or {}).get(r["race_id"]))}
    return {"id": EID, "name": "New Zealand general election", "date": "2026-11-07", "country": "NZ",
            "tz": "Pacific/Auckland", "phase": phase, "poll_close_utc": POLL_CLOSE,
            "geometry": "geo/nz_electorates.topo.json", "geometry_object": "electorates",
            "unit_key": "unit_id", "count_timeline": True,
            "regions": [{"id": "gen", "name": "General electorates"}, {"id": "maori", "name": "Māori electorates"}],
            "races": races,
            "sources": [
                {"name": "Electoral Commission XML media feed", "url": f"{BASE}/",
                 "licence": "Crown copyright; reproduction permitted with acknowledgement (results-site terms)"},
                {"name": "Electoral Commission 2023 official results", "url": "https://electionresults.govt.nz/electionresults_2023/statistics/",
                 "licence": "Crown copyright; reproduction permitted with acknowledgement"},
                {"name": "Stats NZ General and Māori Electorates 2025", "url": "https://datafinder.stats.govt.nz/layer/122741-general-electorates-2025/",
                 "licence": "Creative Commons Attribution 4.0 International"},
                {"name": "Polymarket CLOB", "url": "https://polymarket.com", "licence": "public market data"},
                {"name": "© Kalshi order books", "url": "https://kalshi.com", "licence": "© Kalshi; see docs/kalshi.md"}],
            "notes": [
                "counted is the share of voting-place results received, not the share of votes.",
                "The prior is the official 2023 result mapped by electorate name onto the 2025 boundaries; ten renamed or reconfigured electorates use the named predecessor in docs/newzealand.md.",
                "P is the frozen model probability of each binary market outcome. Discrete seat outcomes have raw/share/sd empty.",
                "Preliminary election-night results exclude special votes; the official result is due 27 November 2026.",
                f"Model frozen before polls close ({Path(cfg['_path']).name}); evidence is forward."],
            "n_units": len(elect)}


def units_object(units: pd.DataFrame, frames: list[dict], t: str) -> dict:
    den = units["NAT"] + units["LAB"]
    prior = np.divide(units["NAT"], den, out=np.full(len(units), .5), where=den > 0)
    if not frames:
        frames = [{"t": t, "f": [0] * len(units), "a": [-1] * len(units),
                   "proj": np.rint(1000 * prior).astype(int).tolist()}]
    return {"unit_ids": units["unit_id"].tolist(), "region": units["region"].tolist(),
            "names": units["name"].tolist(), "prior": np.rint(1000 * prior).astype(int).tolist(),
            "V0": np.rint(units["V"]).astype(int).tolist(),
            "a": {"name": "National", "color": COLORS["NAT"]},
            "b": {"name": "Labour", "color": COLORS["LAB"]}, "frames": frames}


def _projection(cfg, refs, counts=None):
    parties, elect, _ = refs
    units = prior_units(elect)
    c = empty_counts(units) if counts is None else counts
    p = project(units, c, settings(cfg))
    rng = np.random.default_rng(int(cfg.get("seed", 20261107)))
    votes, _, seats = seat_draws(p, settings(cfg), rng)
    vals = outcomes(votes, seats, p.parties, cfg["markets"])
    return units, p, {k: float(v.mean()) for k, v in vals.items()}


def preelection(config: Path | str, out: Path | str = ROOT_DIR / "site" / "data", days: float = 14,
                step: str = "30min", now: pd.Timestamp | None = None, force: bool = False) -> Path:
    cfg = load_config(config); refs = _fetch_references(); info, tokens = _market_info(cfg)
    d = Path(out) / EID
    if (d / "meta.json").exists() and not force:
        old = json.loads((d / "meta.json").read_text()).get("phase")
        if old in ("live", "replay"): return d
    now = (now or pd.Timestamp.now(tz="UTC")).floor(step)
    grid = pd.date_range(now - pd.Timedelta(days=days), now, freq=step)
    units, _, probs = _projection(cfg, refs)
    timeline = {}
    for m in cfg["markets"]:
        pmv = [None] * len(grid)
        if tokens.get(m["id"]):
            try:
                h = polymarket.price_history(tokens[m["id"]], grid[0] - pd.Timedelta(hours=6), now, fidelity_min=30)
                pmv = _asof(h.set_index("time")["price"].astype(float), grid)
            except Exception as e: print(f"{m['rid']}: Polymarket history: {type(e).__name__}: {e}", flush=True)
        kv = [None] * len(grid)
        if m.get("kalshi"):
            try:
                c = kalshi.candlesticks(m["kalshi"].split("-")[0], m["kalshi"], grid[0] - pd.Timedelta(hours=6), now, 1)
                kv = [None if not np.isfinite(x) else float(x) for x in kalshi.prob_from_candles(c, grid).to_numpy(float)]
            except Exception as e: print(f"{m['rid']}: Kalshi history: {type(e).__name__}: {e}", flush=True)
        timeline[m["rid"]] = [{"t": _iso(t), "counted": None, "raw": None, "share": None, "sd": None,
                                "P": _r(probs[m["rid"]]), "pm": _r(a), "kalshi": _r(b)}
                               for t, a, b in zip(grid, pmv, kv)]
    _write_json(d / "meta.json", meta(cfg, refs, info, tokens, "pre-count", probs))
    _write_json(d / "timeline_precount.json", timeline); _write_json(d / "timeline.json", timeline)
    _write_json(d / "timeline_tail.json", _tail(timeline)); _write_json(d / "stats.json", _empty_stats("count has not started"))
    _write_json(d / "arb.json", []); _write_json(d / "units.json", units_object(units, [], _iso(now)))
    _write_json(d / "entry.json", {"id": EID, "name": "New Zealand general election", "date": "2026-11-07",
                                     "country": "NZ", "status": "scheduled", "unit_level": "electorate",
                                     "n_units": 71, "evidence": "forward",
                                     "summary": "A frozen electorate nowcast will follow the official count live against Polymarket and Kalshi."})
    return d


class Exporter:
    def __init__(self, config: Path | str, snap_root: Path | str | None = None, phase: str = "live"):
        self.cfg = load_config(config); self.arc = Archive(snap_root or SNAP_ROOT); self.phase = phase
        self.timeline = {m["rid"]: [] for m in self.cfg["markets"]}; self.frames = []
        self.cursor = pd.Timestamp(POLL_CLOSE) - pd.Timedelta(minutes=1)
        self.refs = None; self.info = self.tokens = None; self._sig = None; self._result = None

    def _blob(self, url: str, t: pd.Timestamp) -> tuple[bytes | None, Path | None]:
        p = self.arc.at(_key(url), t)
        return (self.arc.read(p), p) if p else (None, None)

    def _references(self, t):
        b1, _ = self._blob(f"{BASE}/parties.xml", t); b2, _ = self._blob(f"{BASE}/electorates.xml", t)
        b3, _ = self._blob(f"{BASE}/candidates.xml", t)
        if b1 and b2 and b3: self.refs = (parse_parties(b1), parse_electorates(b2), parse_candidates(b3))
        if self.refs is None: self.refs = _fetch_references()
        if self.info is None: self.info, self.tokens = _market_info(self.cfg)
        return self.refs

    def _model(self, t):
        refs = self._references(t); sig, items = [], []
        for i in range(1, 72):
            b, p = self._blob(f"{BASE}/e{i:02d}/e{i:02d}.xml", t)
            if p: sig.append(p)
            if b: items.append(parse_electorate(b, refs[0], refs[2]))
        key = tuple(sig)
        if key == self._sig: return self._result
        units = prior_units(refs[1]); c = counts_frame(items, units); p = project(units, c, settings(self.cfg))
        rng = np.random.default_rng(int(self.cfg.get("seed", 20261107)))
        votes, _, seats = seat_draws(p, settings(self.cfg), rng)
        probs = {k: float(v.mean()) for k, v in outcomes(votes, seats, p.parties, self.cfg["markets"]).items()}
        self._sig, self._result = key, (units, p, probs)
        return self._result

    def update(self, until: pd.Timestamp | None = None):
        self.arc.refresh(); ts = self.arc.times()
        if not ts: return
        end = min(until, ts[-1]) if until is not None else ts[-1]
        for t in pd.date_range(self.cursor + pd.Timedelta(minutes=1), end.floor("min"), freq="1min"):
            units, p, probs = self._model(t)
            pp = _book_prices(self.arc, self.arc.at("polymarket_books", t)); kp = _kalshi_prices(self.arc, self.arc.at("kalshi_books", t))
            for m in self.cfg["markets"]:
                pmkey = f"{m['event']}/{m['id']}"
                self.timeline[m["rid"]].append({"t": _iso(t), "counted": _r(p.counted_frac), "raw": None,
                    "share": None, "sd": None, "P": _r(probs[m["rid"]]), "pm": _r(pp.get(pmkey)),
                    "kalshi": _r(kp.get(m.get("kalshi")))})
            if not self.frames or t - pd.Timestamp(self.frames[-1]["t"]) >= pd.Timedelta(minutes=5):
                u = p.unit
                self.frames.append({"t": _iso(t), "f": np.rint(1000 * u["f"]).astype(int).tolist(),
                    "a": np.rint(1000 * u["raw_nat"].fillna(-.001)).astype(int).tolist(),
                    "proj": np.rint(1000 * u["proj_nat"]).astype(int).tolist()})
                if len(self.frames) > 120: self.frames[:] = self.frames[::-2][::-1]
            self.cursor = t

    def write(self, out: Path | str):
        d = Path(out) / EID; refs = self._references(self.cursor); units, _, _ = self._model(self.cursor)
        pre = {}
        try: pre = json.loads((d / "timeline_precount.json").read_text())
        except Exception: pass
        merged = {}
        for k, rows in self.timeline.items():
            first = rows[0]["t"] if rows else None
            merged[k] = [r for r in pre.get(k, []) if first is None or r["t"] < first] + rows
        _write_json(d / "meta.json", meta(self.cfg, refs, self.info, self.tokens, self.phase))
        _write_json(d / "timeline.json", merged); _write_json(d / "timeline_tail.json", _tail(merged))
        _write_json(d / "units.json", units_object(units, self.frames, _iso(self.cursor)))
        if not (d / "stats.json").exists(): _write_json(d / "stats.json", _empty_stats("computed after the count"))
        return d


def export(config, out=ROOT_DIR / "site" / "data", snap_root=None, until=None):
    e = Exporter(config, snap_root, "replay"); e.update(pd.Timestamp(until).tz_convert("UTC") if until else None); return e.write(out)


async def run_live(config, out=ROOT_DIR / "site" / "data", snap_root=None, interval=60.0, stop_at=None):
    e = Exporter(config, snap_root, "live")
    while stop_at is None or datetime.now(timezone.utc) < stop_at:
        t = datetime.now(timezone.utc)
        try: e.update(); e.write(out)
        except Exception as x: print(f"nz live: {type(x).__name__}: {x}", flush=True)
        await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - t).total_seconds()))


async def record_feed(interval: float = 120.0, until: datetime | None = None, request_delay: float = .5) -> None:
    """Record the official feed without request bursts: electorate files are fetched sequentially.

    At 120 s this is about 0.6 requests/s. Reference files are refreshed once per hour; election.xml and
    the 71 aggregate electorate files are refreshed every cycle. Bytes are written only on change.
    """
    out = SNAP_ROOT
    out.mkdir(parents=True, exist_ok=True)
    index, last = out / "index.jsonl", {}
    references = [f"{BASE}/{x}.xml" for x in ("electorates", "parties", "candidates", "statistics")]
    dynamic = [f"{BASE}/election.xml"] + [f"{BASE}/e{i:02d}/e{i:02d}.xml" for i in range(1, 72)]
    headers = {"User-Agent": "brunder-research/0.1 "
                            "(+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"}

    def save(url: str, body: bytes, t: datetime) -> None:
        key, h = _key(url), hashlib.sha256(body).hexdigest()
        if last.get(key) == h:
            return
        last[key] = h
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.xml"
        tmp = out / ".tmp" / f"{key}.{f.name}"
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(body)
        os.replace(tmp, f)
        with index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(out)),
                                 "sha256": h, "bytes": len(body), "gzip": False}) + "\n")

    cycle = 0
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        while until is None or datetime.now(timezone.utc) < until:
            started = datetime.now(timezone.utc)
            urls = (references if cycle % max(1, round(3600 / interval)) == 0 else []) + dynamic
            for url in urls:
                t = datetime.now(timezone.utc)
                try:
                    r = await client.get(httpx.URL(url).copy_merge_params({"t": int(t.timestamp())}))
                    if r.status_code == 200:
                        save(url, r.content, t)
                    else:
                        print(f"{t:%H:%M:%S} NZ feed {r.status_code}: {url}", flush=True)
                except Exception as e:
                    print(f"{t:%H:%M:%S} NZ feed error {url}: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(request_delay)
            cycle += 1
            await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - started).total_seconds()))


async def record(config, interval=120.0, until=None):
    cfg = load_config(config)
    events = sorted({m["event"] for m in cfg["markets"]})
    tickers = sorted({m["kalshi"] for m in cfg["markets"] if m.get("kalshi")})
    jobs = [record_feed(interval, until), snap.run(TAG, [], events, interval, until)]
    if tickers:
        jobs.append(kalshi.record(TAG, tickers, interval, until))
    await asyncio.gather(*jobs)


def ingest(source: Path | str, year: int) -> Path:
    """Validate/copy official browser downloads into $BRUNDER_DATA/raw/newzealand/<year>."""
    source, dest = Path(source), RAW_NZ / str(year); dest.mkdir(parents=True, exist_ok=True)
    expected = ["overall-results-summary.csv", "votes-for-registered-parties-by-electorate.csv",
                "winning-electorate-candidates.csv"]
    copied = []
    for name in expected:
        p = source / name
        if not p.exists():
            raise FileNotFoundError(p)
        text = p.read_text(encoding="utf-8-sig")
        if "Election" not in text and "electorate" not in text.lower():
            raise ValueError(f"unexpected file: {p}")
        shutil.copyfile(p, dest / name)
        copied.append(name)
    for p in sorted(source.glob("*-votes-by-voting-place-*.csv")):
        text = p.read_text(encoding="utf-8-sig")
        if "Vote Details" not in text:
            raise ValueError(f"unexpected voting-place file: {p}")
        if p.name.startswith("party-"):
            read_voting_place_party_votes(p)
        shutil.copyfile(p, dest / p.name)
        copied.append(p.name)
    print(f"ingested {', '.join(copied)} into {dest}")
    return dest


def cli(a) -> None:
    stop = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
    if a.step == "ingest": ingest(a.source, a.year)
    elif a.step == "record": asyncio.run(record(a.config, a.interval, stop))
    elif a.step == "preelection": print(preelection(a.config, a.out, a.days, force=a.force))
    elif a.step == "export": print(export(a.config, a.out, a.snap, a.until))
    elif a.step == "live": asyncio.run(run_live(a.config, a.out, a.snap, a.interval, stop))
