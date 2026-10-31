"""Spain general election 2026-11-29 forward test: official-count recorder, province nowcast, dashboard export.

The model works on the 52 Congress constituencies. The count arrives as "tables counted" plus party votes per
province; the model turns that into projected final votes per province, allocates Congress seats province by
province with the D'Hondt rule and the 3% threshold, and reports the probability of each market outcome
(party first / second / third in seats, party vote-share lines).

Everything is frozen in configs/es2026_v1.toml before the count starts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import kalshi, polymarket, snap
from .paths import RAW, ROOT as ROOT_DIR

EID = "es2026"
NAME = "Spain general election 2026"
TAG = "spain_2026"
# Interior publishes the first provisional results once the polls close in the Canary Islands (20:00 local time
# there = 21:00 in mainland Spain = 20:00 UTC).
POLL_CLOSE = "2026-11-29T20:00:00Z"
HISTORY = ROOT_DIR / "data_public" / "es_congreso_province_2016_2023.csv"
SNAP_ROOT = RAW / "snap" / TAG
UA = "brunder/0.1 (research; recorder)"

# Lanes: one "list" per party family. History lanes keep the left-of-PSOE parties together (LEFT) because the
# lists changed every election; 2026 splits them into Frente Amplio (FA, the Sumar successor) and Podemos.
HIST_LANES = ("PP", "PSOE", "VOX", "CS", "LEFT", "ERC", "JUNTS", "BILDU", "PNV", "BNG", "CCA", "UPN", "CUP", "OTHER")
LANES = ("PP", "PSOE", "VOX", "FA", "PODEMOS", "ERC", "JUNTS", "BILDU", "PNV", "BNG", "CCA", "UPN", "CUP", "OTHER")
NO_SEATS = ("OTHER",)          # the OTHER lane is many small lists; it is never given seats
THRESHOLD = 0.03               # share of valid votes (blank included) a list needs in a province to win seats
COLORS = {"PP": "#1d84ce", "PSOE": "#e30613", "VOX": "#63be21", "FA": "#e51c55", "PODEMOS": "#6b2c91",
          "ERC": "#ffb232", "JUNTS": "#00c3b2", "BILDU": "#a3c940", "PNV": "#008135", "BNG": "#76b3dd",
          "CCA": "#f2c200", "UPN": "#1f4e9c", "CUP": "#ffd800", "OTHER": "#777777",
          "CS": "#eb6109", "LEFT": "#e51c55"}
LANE_NAMES = {"PP": "PP", "PSOE": "PSOE", "VOX": "Vox", "FA": "Frente Amplio", "PODEMOS": "Podemos", "ERC": "ERC",
              "JUNTS": "Junts", "BILDU": "EH Bildu", "PNV": "PNV", "BNG": "BNG", "CCA": "Coalición Canaria",
              "UPN": "UPN", "CUP": "CUP", "OTHER": "Other lists", "CS": "Ciudadanos", "LEFT": "Left lists"}

PROVINCES = {
    "01": "Araba/Álava", "02": "Albacete", "03": "Alicante/Alacant", "04": "Almería", "05": "Ávila", "06": "Badajoz",
    "07": "Illes Balears", "08": "Barcelona", "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón/Castelló",
    "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña", "16": "Cuenca", "17": "Girona", "18": "Granada",
    "19": "Guadalajara", "20": "Gipuzkoa", "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida",
    "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia", "31": "Navarra", "32": "Ourense",
    "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra", "37": "Salamanca",
    "38": "Santa Cruz de Tenerife", "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria",
    "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "Valencia/València", "47": "Valladolid", "48": "Bizkaia",
    "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla"}
CCAA = {
    "andalucia": ("Andalucía", "04 11 14 18 21 23 29 41"), "aragon": ("Aragón", "22 44 50"),
    "asturias": ("Asturias", "33"), "baleares": ("Illes Balears", "07"), "canarias": ("Canarias", "35 38"),
    "cantabria": ("Cantabria", "39"), "cyl": ("Castilla y León", "05 09 24 34 37 40 42 47 49"),
    "clm": ("Castilla-La Mancha", "02 13 16 19 45"), "cataluna": ("Cataluña", "08 17 25 43"),
    "valencia": ("Comunitat Valenciana", "03 12 46"), "extremadura": ("Extremadura", "06 10"),
    "galicia": ("Galicia", "15 27 32 36"), "madrid": ("Madrid", "28"), "murcia": ("Murcia", "30"),
    "navarra": ("Navarra", "31"), "paisvasco": ("País Vasco", "01 20 48"), "rioja": ("La Rioja", "26"),
    "ceutamelilla": ("Ceuta y Melilla", "51 52")}
PROV_CCAA = {p: k for k, (_, ps) in CCAA.items() for p in ps.split()}
# Congress seats in 2026 (Real Decreto 806/2026; Interior press note of 2026-10-20). Only Cádiz (9 -> 8) and
# Madrid (37 -> 38) differ from 2023.
SEATS_2026 = {
    "01": 4, "02": 4, "03": 12, "04": 6, "05": 3, "06": 5, "07": 8, "08": 32, "09": 4, "10": 4, "11": 8, "12": 5,
    "13": 5, "14": 6, "15": 8, "16": 3, "17": 6, "18": 7, "19": 3, "20": 6, "21": 5, "22": 3, "23": 5, "24": 4,
    "25": 4, "26": 4, "27": 4, "28": 38, "29": 11, "30": 10, "31": 5, "32": 4, "33": 7, "34": 3, "35": 8, "36": 7,
    "37": 4, "38": 7, "39": 5, "40": 3, "41": 12, "42": 2, "43": 6, "44": 3, "45": 6, "46": 16, "47": 5, "48": 8,
    "49": 3, "50": 7, "51": 1, "52": 1}


def uid_of(code: str) -> str:
    return f"es-{int(code):02d}"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", t)


_HIST_PATTERNS = [
    ("VOX", r"\bVOX\b"),
    ("PP", r"PARTIDO POPULAR|PARTIT POPULAR|\bPP\b|^PP-"),
    ("PSOE", r"PSOE|\bPSC\b|PSDEG|PSE-EE|PSIB|\bPSN\b|SOCIALIST|PARTIT SOCIALISTA"),
    ("CS", r"CIUDADANOS|CIUTADANS|\bC'?S\b"),
    ("ERC", r"ESQUERRA REPUBLICANA|\bERC\b"),
    ("JUNTS", r"JUNTS|JXCAT|CONVERGENCIA|\bCDC\b|PDECAT"),
    ("BILDU", r"BILDU"),
    ("PNV", r"EAJ-PNV|EUZKO ALDERDI|\bPNV\b"),
    ("BNG", r"\bBNG\b|B\.N\.G|BLOQUE NACIONALISTA GALEGO|BNG-NOS"),
    ("CCA", r"COALICION CANARIA|\bCCA\b"),
    ("UPN", r"UNION DEL PUEBLO NAVARRO|\bUPN\b|U\.P\.N|NAVARRA SUMA"),
    ("CUP", r"\bCUP\b|CANDIDATURA D'UNITAT"),
    ("LEFT", r"SUMAR|PODEMOS|EN COMU|EN MAREA|COMPROMIS|MAS PAIS|FRENTE AMPLIO|\bFA\b|IZQUIERDA UNIDA|\bIU\b|"
             r"ELKARREKIN|UNIDAS|UNIDOS"),
]


def hist_lane(text: str) -> str:
    """History lane of an official candidacy name (first matching pattern wins; unknown lists are OTHER)."""
    t = _norm(text)
    for lane, pat in _HIST_PATTERNS:
        if re.search(pat, t):
            return lane
    return "OTHER"


def lane_2026(text: str, patterns: dict[str, str] | None = None) -> str:
    """2026 lane of a feed result row (acronym + name + family). Podemos and Frente Amplio run separately in 2026.

    `patterns` (config [lanes]) can force a lane by regex; they are tried first."""
    t = _norm(text)
    for lane, pat in (patterns or {}).items():
        if re.search(pat, t, re.I):
            return lane
    lane = hist_lane(text)
    if lane in ("CS",):
        return "OTHER"
    if lane == "LEFT":
        return "PODEMOS" if re.search(r"PODEMOS", t) and not re.search(r"FRENTE|AMPLIO|\bFA\b|SUMAR|UNIDAS|UNIDOS", t) else "FA"
    return lane


def load_config(path: Path | str) -> dict:
    path = Path(path)
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def load_history(path: Path | str = HISTORY) -> pd.DataFrame:
    d = pd.read_csv(path, comment="#", dtype={"prov": str})
    for c in (*HIST_LANES, "electors", "valid", "blank"):
        d[c] = pd.to_numeric(d[c], errors="raise")
    return d


def history_units(date: str, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """One election's province table: lane shares of candidacy votes, candidacy votes V, blank share, seats."""
    h = load_history() if history is None else history
    d = h[h["election"] == date].copy()
    if d.empty:
        raise KeyError(date)
    d["unit_id"] = [uid_of(p) for p in d["prov"]]
    d = d.set_index("unit_id").sort_index()
    votes = d[list(HIST_LANES)].to_numpy(float)
    out = pd.DataFrame(index=d.index)
    out["name"] = d["name"]
    out["region"] = [f"es-{PROV_CCAA[p.split('-')[1]]}" for p in d.index]
    out["V"] = votes.sum(axis=1)
    out["blank_frac"] = d["blank"] / d["valid"]
    out["seats"] = d[[f"seats_{x}" for x in HIST_LANES]].sum(axis=1).astype(int)
    out[list(HIST_LANES)] = votes / out["V"].to_numpy()[:, None]
    return out


def _rake(profiles: np.ndarray, volume: np.ndarray, target: np.ndarray, iters: int = 200) -> np.ndarray:
    """Multiplicatively rescale lane shares in every province so the volume-weighted national shares hit `target`."""
    mult = np.ones(len(target))
    for _ in range(iters):
        p = profiles * mult
        p /= p.sum(axis=1, keepdims=True)
        mult *= target / np.maximum((p * volume[:, None]).sum(axis=0) / volume.sum(), 1e-12)
    p = profiles * mult
    return p / p.sum(axis=1, keepdims=True)


def poll_medians(cfg: dict) -> dict[str, float]:
    """Median reported share per lane over the frozen polls (a missing figure is skipped)."""
    out = {}
    for lane in ("pp", "psoe", "vox", "fa", "podemos"):
        xs = [p[lane] for p in cfg["poll"]["polls"] if p.get(lane) is not None]
        out[lane.upper()] = float(np.median(xs)) / 100
    return out


def poll_target(cfg: dict, shares: pd.DataFrame, volume: pd.Series, blank: float) -> np.ndarray:
    """National candidacy-vote share per 2026 lane: poll medians for the five polled lanes, the other lanes keep
    their relative 2023 sizes and fill the rest. Polls are shares of valid votes, so blank is taken out first."""
    med = poll_medians(cfg)
    polled = ("PP", "PSOE", "VOX", "FA", "PODEMOS")
    t = np.zeros(len(LANES))
    for i, lane in enumerate(LANES):
        if lane in polled:
            t[i] = med[lane] / (1 - blank)
    nat = (shares[list(LANES)].to_numpy() * volume.to_numpy()[:, None]).sum(axis=0) / volume.sum()
    rest = [i for i, lane in enumerate(LANES) if lane not in polled]
    t[rest] = (1 - t.sum()) * nat[rest] / nat[rest].sum()
    return t


def prior_units(cfg: dict, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """2026 prior: 2023 province lane shares (Sumar split into Frente Amplio and Podemos by the polls' ratio, Ciudadanos
    folded into OTHER), raked to the frozen poll medians. V0 = 2023 candidacy votes; seats = 2026 allocation.

    Rehearsal configs (analysis/es_rehearsal.py) set prior.poll_rake = false (no poll raking, Podemos share 0) and
    prior.seats_date (use that election's seats) to replay an earlier night without looking at its own result."""
    h = load_history() if history is None else history
    base = history_units(cfg["prior"]["date"], h)
    rake = cfg["prior"].get("poll_rake", True)
    if rake:
        med = poll_medians(cfg)
        rho = med["PODEMOS"] / (med["FA"] + med["PODEMOS"])
    else:
        rho = 0.0
    shares = pd.DataFrame(0.0, index=base.index, columns=list(LANES))
    for lane in LANES:
        if lane in HIST_LANES:
            shares[lane] = base[lane]
    shares["FA"] = base["LEFT"] * (1 - rho)
    shares["PODEMOS"] = base["LEFT"] * rho
    shares["OTHER"] = base["OTHER"] + base["CS"]
    prof = shares.to_numpy(float) + 1e-6
    prof /= prof.sum(axis=1, keepdims=True)
    if rake:
        blank = float((base["blank_frac"] * base["V"]).sum() / base["V"].sum())
        p = _rake(prof, base["V"].to_numpy(float), poll_target(cfg, shares, base["V"], blank))
    else:
        p = prof
    out = base[["name", "region", "blank_frac"]].copy()
    out["V0"] = base["V"]
    out[list(LANES)] = p
    if cfg["prior"].get("seats_date"):
        out["seats"] = history_units(cfg["prior"]["seats_date"], h)["seats"]
    else:
        out["seats"] = [SEATS_2026[u.split("-")[1]] for u in out.index]
    return out


@dataclass(frozen=True)
class Settings:
    own_votes: float = 400_000.0      # province's own counted votes weigh against its prior (pseudo-votes)
    swing_votes: float = 1_000_000.0  # pooled national swing: pseudo-votes of prior
    sd_votes: float = 250_000.0       # how fast the prior's national error fades: pseudo-votes of prior in the error model
    turnout_votes: float = 20_000.0   # province turnout ratio: pseudo-votes of prior
    cera_frac: float = 0.0083         # share of final valid votes cast abroad (CERA), not in the night count
    tau_nat: float = 0.03             # log-sd of each lane's national error left in the uncounted votes, late count
    precount_sd: float = 0.12         # same, before any count
    tau_prov: float = 0.06            # log-sd of a province's own error in its uncounted votes
    draws: int = 4000


def settings(cfg: dict) -> Settings:
    m = cfg.get("model", {})
    return Settings(**{f.name: m[f.name] for f in fields(Settings) if f.name in m})


@dataclass
class Projection:
    lanes: tuple
    votes: np.ndarray        # (P, K) expected final candidacy votes
    counted: np.ndarray      # (P, K) votes already counted
    remaining: np.ndarray    # (P, K) expected votes still to arrive (uncounted tables + abroad votes)
    f: np.ndarray            # (P,) share of tables counted
    blank_frac: np.ndarray   # (P,)
    seats: np.ndarray        # (P,)
    counted_frac: float
    unit: pd.DataFrame


def empty_counts(units: pd.DataFrame, lanes=LANES) -> pd.DataFrame:
    d = pd.DataFrame(0.0, index=units.index, columns=[*lanes, "f"])
    return d


def project(units: pd.DataFrame, count: pd.DataFrame, st: Settings, lanes=LANES) -> Projection:
    """Province prior + pooled national swing + each province's own counted composition, then abroad votes."""
    L = list(lanes)
    p0 = units[L].to_numpy(float)
    V0 = units["V0"].to_numpy(float)
    V0n = V0 * (1 - st.cera_frac)
    c = count.reindex(units.index).fillna(0.0)
    cv = c[L].to_numpy(float)
    n = cv.sum(axis=1)
    f = np.clip(c["f"].to_numpy(float), 0, 1)
    f = np.where(n > 0, f, 0.0)
    expn = V0n * f
    g = (n.sum() + st.turnout_votes) / (expn.sum() + st.turnout_votes)
    rho = (n + st.turnout_votes * g) / (expn + st.turnout_votes)
    rem_total = (1 - f) * V0n * rho
    expected = (p0 * expn[:, None]).sum(axis=0)
    observed = cv.sum(axis=0)
    if observed.sum() == 0:
        base = p0.copy()
    else:
        es = expected / expected.sum()
        os_ = (observed + st.swing_votes * es) / (observed.sum() + st.swing_votes)
        base = p0 * (os_ / np.maximum(es, 1e-9))
        base /= base.sum(axis=1, keepdims=True)
    local = np.divide(cv, n[:, None], out=base.copy(), where=n[:, None] > 0)
    w = np.divide(n, n + st.own_votes * (1 - f), out=np.zeros_like(n), where=(n + st.own_votes * (1 - f)) > 0)
    share = (1 - w[:, None]) * base + w[:, None] * local
    share /= share.sum(axis=1, keepdims=True)
    remaining = rem_total[:, None] * share + (st.cera_frac * V0)[:, None] * base
    votes = cv + remaining
    counted_frac = float(n.sum() / votes.sum())
    ia, ib = L.index("PP"), L.index("PSOE")
    u = pd.DataFrame(index=units.index)
    u["f"] = f
    two = cv[:, ia] + cv[:, ib]
    u["raw"] = np.divide(cv[:, ia], two, out=np.full(len(n), np.nan), where=two > 0)   # PP's share of the PP+PSOE vote
    u["projected"] = votes[:, ia] / (votes[:, ia] + votes[:, ib])
    return Projection(tuple(L), votes, cv, remaining, f, units["blank_frac"].to_numpy(float),
                      units["seats"].to_numpy(int), counted_frac, u)


def dhondt(votes: np.ndarray, seats: int, valid: np.ndarray, skip: tuple[int, ...] = ()) -> np.ndarray:
    """D'Hondt seats for many draws of one province. votes (D, K); valid (D,) = valid votes incl. blank."""
    ok = votes >= THRESHOLD * valid[:, None]
    for i in skip:
        ok[:, i] = False
    v = np.where(ok, votes, 0.0)
    q = (v[:, :, None] / np.arange(1, seats + 1)[None, None, :]).reshape(len(v), -1)
    top = np.argpartition(-q, seats - 1, axis=1)[:, :seats] if seats < q.shape[1] else np.tile(np.arange(q.shape[1]), (len(v), 1))
    won = np.take_along_axis(q, top, axis=1) > 0
    lane = top // seats
    out = np.zeros(votes.shape)
    for k in range(votes.shape[1]):
        out[:, k] = ((lane == k) & won).sum(axis=1)
    return out


@dataclass
class Draws:
    lanes: tuple
    votes: np.ndarray        # (D, K) national candidacy votes
    valid: np.ndarray        # (D,) national valid votes incl. blank
    seats: np.ndarray        # (D, K)

    @property
    def share(self) -> np.ndarray:
        return self.votes / self.valid[:, None]


def simulate(p: Projection, st: Settings, seed: int) -> Draws:
    """Draw final votes: counted votes stay, the uncounted part gets a national and a province-level log-normal error."""
    rng = np.random.default_rng(seed)
    D, (P, K) = st.draws, p.votes.shape
    # The national error in the uncounted votes: the prior's error (precount_sd) fades as counted votes outweigh the
    # prior's pseudo-votes, and a floor (tau_nat) stays for unrepresentative early tables.
    sd_nat = float(np.sqrt(st.tau_nat ** 2 + st.precount_sd ** 2 * st.sd_votes / (st.sd_votes + p.counted.sum())))
    sd_prov = st.tau_prov * np.sqrt(np.clip(1 - p.f, 0, 1))
    a = rng.normal(0, sd_nat, (D, 1, K))
    b = rng.normal(0, 1, (D, P, K)) * sd_prov[None, :, None]
    mult = np.exp(a + b - 0.5 * (sd_nat ** 2 + sd_prov[None, :, None] ** 2))
    votes = p.counted[None] + p.remaining[None] * mult
    seats = np.zeros((D, K))
    valid = np.zeros(D)
    skip = tuple(p.lanes.index(x) for x in NO_SEATS if x in p.lanes)
    for i in range(P):
        vp = votes[:, i, :]
        vl = vp.sum(axis=1) / (1 - p.blank_frac[i])
        valid += vl
        seats += dhondt(vp, int(p.seats[i]), vl, skip)
    return Draws(p.lanes, votes.sum(axis=1), valid, seats)


def race_probability(d: Draws, race: dict) -> float:
    lanes = d.lanes
    if race["kind"] == "rank":
        i = lanes.index(race["lane"])
        key = d.seats * 1e10 + d.votes
        rank = (key > key[:, i:i + 1]).sum(axis=1) + 1
        return float(np.mean(rank == race["rank"]))
    idx = [lanes.index(x) for x in race["lanes"]]
    share = d.share[:, idx].sum(axis=1)
    return float(np.mean(share * 100 >= race["threshold"] - 0.005))


def race_subject(race: dict) -> list[str]:
    return list(race["lanes"]) if race["kind"] == "share" else [race["lane"]]


def race_share(d: Draws, race: dict) -> tuple[float, float]:
    idx = [d.lanes.index(x) for x in race_subject(race)]
    s = d.share[:, idx].sum(axis=1)
    return float(s.mean()), float(s.std())


# --------------------------------------------------------------------------------------------- feed parsing

def parse_province(data: bytes | str, patterns: dict[str, str] | None = None) -> dict:
    """Parse one province file of the results feed: tables counted, valid/blank votes, votes per 2026 lane.

    The feed's schema is the one its historical files use: scope.{type,code}, counted.{tables,total_tables},
    totals.{valid,blank}, results[].{acronym,name,family,votes}. Anything else raises."""
    d = json.loads(data)
    scope = d["scope"]
    if scope.get("type") != "prov":
        raise ValueError(f"not a province file: {scope!r}")
    code = str(scope["code"])
    if code not in PROVINCES:
        raise ValueError(f"unknown province code {code!r}")
    c, t = d["counted"], d["totals"]
    votes = {x: 0 for x in LANES}
    for r in d["results"]:
        lane = lane_2026(f"{r.get('acronym') or ''} {r.get('name') or ''} {r.get('family') or ''}", patterns)
        votes[lane] += int(r["votes"])
    valid, blank = int(t["valid"]), int(t["blank"])
    if abs(sum(votes.values()) + blank - valid) > 1:
        raise ValueError(f"province {code}: lane votes {sum(votes.values())} + blank {blank} != valid {valid}")
    total = int(c["total_tables"])
    if total <= 0 or not 0 <= int(c["tables"]) <= total:
        raise ValueError(f"province {code}: bad table counts {c!r}")
    if int(c["tables"]) > 0 and valid >= 500 and (votes["PP"] + votes["UPN"] <= 0 or votes["PSOE"] <= 0):
        raise ValueError(f"province {code}: no PP/UPN or PSOE votes after {c['tables']} tables; check the lane patterns")
    return {"unit_id": uid_of(code), "tables": int(c["tables"]), "total_tables": total, "valid": valid,
            "blank": blank, "votes": votes}


def count_frame(units: pd.DataFrame, parsed: dict[str, dict], lanes=LANES) -> pd.DataFrame:
    d = empty_counts(units, lanes)
    for uid, x in parsed.items():
        if uid in d.index:
            d.loc[uid, list(lanes)] = [x["votes"].get(k, 0) for k in lanes]
            d.loc[uid, "f"] = x["tables"] / x["total_tables"]
    return d


# --------------------------------------------------------------------------------------------- export

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, path)


def _iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, n=4):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


class Archive:
    def __init__(self, root: Path | str = SNAP_ROOT):
        self.root = Path(root)
        self.files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        self.refresh()

    def refresh(self):
        out: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        p = self.root / "index.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    x = json.loads(line)
                    f = self.root / x["file"]
                    if f.exists():
                        out.setdefault(x["key"], []).append((pd.Timestamp(x["t"]).tz_convert("UTC"), f))
                except Exception:
                    continue
        for rows in out.values():
            rows.sort()
        self.files = out

    def times(self):
        return sorted({t for k, rows in self.files.items() if k.startswith("prov/") for t, _ in rows})

    def at(self, key: str, t: pd.Timestamp) -> Path | None:
        rows = self.files.get(key, [])
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo - 1][1] if lo else None


def _book_prices(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    out = {}
    for k, b in json.loads(path.read_text()).items():
        bids, asks = b.get("bids") or [], b.get("asks") or []
        bid = max((float(x["price"]) for x in bids), default=np.nan)
        ask = min((float(x["price"]) for x in asks), default=np.nan)
        out[k] = (bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) and ask - bid <= .1 else float(b.get("last_trade_price") or np.nan)
    return out


def _kalshi_prices(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    return {k: kalshi.prob_from_book(v) for k, v in json.loads(path.read_text()).items()}


def _tail(timeline: dict[str, list], minutes: int = 60) -> dict:
    out, first = {}, []
    for k, rows in timeline.items():
        if rows:
            cut = pd.Timestamp(rows[-1]["t"]) - pd.Timedelta(minutes=minutes)
            out[k] = [r for r in rows if pd.Timestamp(r["t"]) > cut]
            first.append(out[k][0]["t"])
        else:
            out[k] = []
    return {**out, "tail": True, "since": min(first) if first else None}


def _pm_key(race: dict) -> str | None:
    return f"{race['pm_event']}/{race['polymarket']}" if race.get("polymarket") else None


def _market_info(cfg: dict) -> dict[str, dict]:
    out = {}
    for ev in cfg["markets"]["polymarket_events"]:
        try:
            event = polymarket.event(ev)
            out.update({f"{ev}/{m['slug']}": m for m in event["markets"]})
        except Exception as e:
            print(f"Spain Gamma metadata {ev}: {type(e).__name__}: {e}", flush=True)
    return out


def meta(cfg: dict, phase: str, probs: dict[str, float], info: dict[str, dict] | None = None) -> dict:
    info = info or {}
    races = []
    for r in cfg["races"]:
        markets = []
        if r.get("polymarket"):
            m = info.get(_pm_key(r), {})
            markets.append({"platform": "polymarket", "id": r["polymarket"],
                            "question": m.get("question", r["question"]), "yes_is": "a", "token_yes": r.get("pm_token")})
        if r.get("kalshi"):
            markets.append({"platform": "kalshi", "id": r["kalshi"], "question": r["question"], "yes_is": "a"})
        subject = race_subject(r)
        races.append({"race_id": r["id"], "name": r["name"], "region": None,
                      "a": {"id": r["id"], "name": r["short"], "color": COLORS[subject[0]]},
                      "b": {"id": "field", "name": "Otherwise", "color": "#777777"}, "markets": markets,
                      **({"precount": {"P": _r(probs[r["id"]])}} if phase == "pre-count" else {})})
    regions = [{"id": f"es-{k}", "name": nm} for k, (nm, _) in CCAA.items()]
    return {"id": EID, "name": NAME, "date": "2026-11-29", "country": "ES", "country_name": "Spain",
            "tz": "Europe/Madrid", "phase": phase, "poll_close_utc": POLL_CLOSE,
            "geometry": "geo/es_provinces.topo.json", "geometry_object": "provinces", "unit_key": "unit_id",
            "count_timeline": True, "regions": regions,
            "parties": [{"id": k, "name": LANE_NAMES[k], "color": COLORS[k]} for k in LANES],
            "races": races, "sources": [
                {"name": "Spanish Ministry of the Interior: provisional results, as republished by Elecciones 29N",
                 "url": "https://generales29n.com/datos/",
                 "licence": "Public JSON, reusable with the source named (Ley 37/2007); the Ministry's own feed needs a media login"},
                {"name": "Ministry of the Interior, Infoelectoral: Congress results by province 1977-2023",
                 "url": "https://descargas.interior.gob.es/datasets/resultados_electorales/Elecciones-Congreso.xlsx",
                 "licence": "CC BY 4.0"},
                {"name": "Real Decreto 806/2026 (seats per province)", "url": "https://www.boe.es/buscar/doc.php?id=BOE-A-2026-20742",
                 "licence": "Reusable (BOE)"},
                {"name": "Natural Earth Admin 1 (province map)", "url": "https://www.naturalearthdata.com/", "licence": "public domain"},
                *({"name": f"Poll: {p['pollster']} ({p['published']})", "url": p["url"], "licence": "reported topline"}
                  for p in cfg["poll"]["polls"]),
                {"name": "Polymarket CLOB", "url": "https://polymarket.com", "licence": "public market data"},
                {"name": "© Kalshi order books", "url": "https://kalshi.com", "licence": "© Kalshi; see docs/kalshi.md"}],
            "notes": ["Each row is a market question. P is the model's chance that the question resolves Yes.",
                      "counted is the projected share of final votes already counted. f on the map is the share of tables counted in each province.",
                      "The map shows who leads between PP and PSOE in each province: PP's share of the PP and PSOE vote. Other parties lead in some provinces.",
                      "The PP + Vox 50% question shows the projected vote share and its margin. The other questions show chances only.",
                      "Seats are allocated province by province with the D'Hondt rule and the 3% threshold. Lists with no seats are grouped as Other lists.",
                      "Votes cast abroad (about 0.8% of the vote) are added days after election night. The model adds them to its projection but they are not in the night count.",
                      "The Prime Minister markets are not modelled: they depend on coalition talks after the count.",
                      "Historical count orders are simulated: the Ministry does not keep a timestamped record of past nights.",
                      f"Model frozen before the count ({Path(cfg['_path']).name}); evidence is forward."],
            "n_units": 52}


def units_object(units: pd.DataFrame, frames: list[dict], t: str, cfg: dict) -> dict:
    if not frames:
        frames = [{"t": t, "f": [0] * len(units), "a": [-1] * len(units),
                   "proj": np.rint(1000 * _two_way(units)).astype(int).tolist()}]
    return {"unit_ids": units.index.tolist(), "region": units["region"].tolist(), "names": units["name"].tolist(),
            "prior": np.rint(1000 * _two_way(units)).astype(int).tolist(),
            "V0": np.rint(units["V0"]).astype(int).tolist(),
            "a": {"name": "PP", "color": COLORS["PP"]}, "b": {"name": "PSOE", "color": COLORS["PSOE"]}, "frames": frames}


def _initial(cfg: dict):
    units = prior_units(cfg)
    st = settings(cfg)
    p = project(units, empty_counts(units), st)
    d = simulate(p, st, int(cfg["seed"]))
    return units, p, d, {r["id"]: race_probability(d, r) for r in cfg["races"]}


def entry() -> dict:
    return {"id": EID, "name": NAME, "date": "2026-11-29", "country": "ES", "country_name": "Spain",
            "status": "scheduled", "unit_level": "province", "n_units": 52, "evidence": "forward",
            "summary": "A model frozen before polls close will follow the provisional count by province (official figures as "
                       "republished by Elecciones 29N, a third-party site), give out seats by the legal formula, and run live "
                       "against Polymarket and Kalshi."}


def _two_way(units: pd.DataFrame) -> np.ndarray:
    """PP's share of the PP + PSOE vote in each province (the map's 'side a' value)."""
    return (units["PP"] / (units["PP"] + units["PSOE"])).to_numpy(float)


def _timeline_row(cfg, race, t, p, d, probs, pp, kp, counted=True):
    """One race at one minute. The PP + Vox 50% line is a two-sided question, so its row carries the projected and counted
    share (the dashboard reads 50% as the margin's zero line). Other questions carry chances only."""
    row = {"t": _iso(t), "counted": _r(p.counted_frac) if counted else None, "raw": None, "share": None, "sd": None,
           "P": _r(probs[race["id"]]), "pm": _r(pp.get(_pm_key(race))), "kalshi": _r(kp.get(race.get("kalshi")))}
    if race["kind"] == "share" and abs(race["threshold"] - 50.0) < 1e-9:
        row["share"], row["sd"] = (_r(x) for x in race_share(d, race))
        idx = [p.lanes.index(x) for x in race_subject(race)]
        if counted and p.counted[:, idx].sum() > 0:
            blank = float(np.average(p.blank_frac, weights=p.votes.sum(axis=1)))
            row["raw"] = _r(float(p.counted[:, idx].sum() / (p.counted.sum() / (1 - blank))))
    return row


def preelection(config: Path | str, out: Path | str = ROOT_DIR / "site" / "data", force: bool = False) -> Path:
    cfg = load_config(config)
    units, p, d, probs = _initial(cfg)
    info = _market_info(cfg)
    dd = Path(out) / EID
    if (dd / "meta.json").exists() and not force:
        old = json.loads((dd / "meta.json").read_text()).get("phase")
        if old in ("live", "replay"):
            return dd
    now = pd.Timestamp.now(tz="UTC").floor("min")
    pp, kp = {}, {}
    for ev in cfg["markets"]["kalshi_events"]:
        try:
            km = kalshi.markets(kalshi.event(ev)).set_index("ticker")
            for ticker, row in km.iterrows():
                bid, ask, last = row["yes_bid"], row["yes_ask"], row["last"]
                kp[ticker] = ((bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) and ask - bid <= .1
                              else last if np.isfinite(last) else None)
        except Exception as e:
            print(f"Spain Kalshi metadata {ev}: {type(e).__name__}: {e}", flush=True)
    for key, m in info.items():
        prices = json.loads(m.get("outcomePrices", "[]") or "[]")
        if prices:
            pp[key] = float(prices[0])
    timeline = {r["id"]: [_timeline_row(cfg, r, now, p, d, probs, pp, kp, counted=False)] for r in cfg["races"]}
    _write_json(dd / "meta.json", meta(cfg, "pre-count", probs, info))
    for name in ("timeline_precount.json", "timeline.json"):
        _write_json(dd / name, timeline)
    _write_json(dd / "timeline_tail.json", _tail(timeline))
    _write_json(dd / "units.json", units_object(units, [], _iso(now), cfg))
    _write_json(dd / "stats.json", {"calibration": None, "settle": None, "reasons": {"calibration": "count has not started"}})
    _write_json(dd / "arb.json", [])
    _write_json(dd / "entry.json", entry())
    return dd


def _frame(t, p: Projection) -> dict:
    """Map frame at time t: share of tables counted, PP's raw and projected share of the PP+PSOE vote, per province (x1000)."""
    return {"t": _iso(t), "f": np.rint(1000 * p.unit["f"]).astype(int).tolist(),
            "a": np.rint(1000 * p.unit["raw"].fillna(-.001)).astype(int).tolist(),
            "proj": np.rint(1000 * p.unit["projected"]).astype(int).tolist()}


class Exporter:
    def __init__(self, config: Path | str, snap_root: Path | str | None = None, phase: str = "live"):
        self.cfg = load_config(config)
        self.arc = Archive(snap_root or SNAP_ROOT)
        self.phase = phase
        self.units = prior_units(self.cfg)
        self.patterns = self.cfg.get("lanes")
        self.races = self.cfg["races"]
        self.timeline = {r["id"]: [] for r in self.races}
        self.frames: list[dict] = []
        self.cursor = pd.Timestamp(POLL_CLOSE) - pd.Timedelta(minutes=1)
        self.st = settings(self.cfg)
        self.info: dict = {}
        self._sig = None
        self._result = None
        self._parsed: dict[Path, dict] = {}

    def _read(self, path: Path) -> dict:
        if path not in self._parsed:
            self._parsed[path] = parse_province(path.read_bytes(), self.patterns)
        return self._parsed[path]

    def _model(self, t: pd.Timestamp):
        parsed, sig = {}, []
        for uid in self.units.index:
            f = self.arc.at(f"prov/{uid}", t)
            if f is None:
                continue
            sig.append(f)
            try:
                parsed[uid] = self._read(f)
            except Exception as e:
                print(f"Spain parse {f.name}: {type(e).__name__}: {e}", flush=True)
                sig.pop()
        key = tuple(sig)
        if key == self._sig:
            return self._result
        p = project(self.units, count_frame(self.units, parsed), self.st)
        d = simulate(p, self.st, int(self.cfg["seed"]))
        self._sig, self._result = key, (p, d, {r["id"]: race_probability(d, r) for r in self.races})
        return self._result

    def update(self, until: pd.Timestamp | None = None):
        self.arc.refresh()
        times = self.arc.times()
        if not times:
            return
        end = min(until, times[-1]) if until is not None else times[-1]
        for t in pd.date_range(self.cursor + pd.Timedelta(minutes=1), end.floor("min"), freq="1min"):
            p, d, probs = self._model(t)
            pp = _book_prices(self.arc.at("polymarket_books", t))
            kp = _kalshi_prices(self.arc.at("kalshi_books", t))
            for r in self.races:
                self.timeline[r["id"]].append(_timeline_row(self.cfg, r, t, p, d, probs, pp, kp))
            if not self.frames or t - pd.Timestamp(self.frames[-1]["t"]) >= pd.Timedelta(minutes=5):
                self.frames.append(_frame(t, p))
                if len(self.frames) > 120:
                    self.frames[:] = self.frames[::-2][::-1]
            self.cursor = t

    def write(self, out: Path | str):
        dd = Path(out) / EID
        now = pd.Timestamp.now(tz="UTC").floor("s")
        phase = (("pre-count" if now < pd.Timestamp(POLL_CLOSE) else "live") if self.phase == "live" else self.phase)
        p, d, probs = self._model(self.cursor)
        pre = {}
        try:
            pre = json.loads((dd / "timeline_precount.json").read_text())
        except Exception:
            pass
        merged = {k: [r for r in pre.get(k, []) if not self.timeline[k] or r["t"] < self.timeline[k][0]["t"]] + self.timeline[k]
                  for k in self.timeline}
        _write_json(dd / "meta.json", meta(self.cfg, phase, probs, self.info))
        _write_json(dd / "timeline.json", merged)
        _write_json(dd / "timeline_tail.json", _tail(merged))
        frames = list(self.frames)
        if pd.Timestamp(self.cursor) > pd.Timestamp(POLL_CLOSE) - pd.Timedelta(minutes=1) and (not frames or frames[-1]["t"] != _iso(self.cursor)):
            frames.append(_frame(self.cursor, p))
        _write_json(dd / "units.json", units_object(self.units, frames, _iso(now), self.cfg))
        if not (dd / "timeline_precount.json").exists():
            _write_json(dd / "timeline_precount.json", merged)
        if not (dd / "stats.json").exists():
            _write_json(dd / "stats.json", {"calibration": None, "reasons": {"calibration": "computed after count"}})
        if not (dd / "arb.json").exists():
            _write_json(dd / "arb.json", [])
        if not (dd / "entry.json").exists():
            _write_json(dd / "entry.json", entry())
        return dd


def export(config, out=ROOT_DIR / "site" / "data", snap_root=None, until=None):
    e = Exporter(config, snap_root, "replay")
    e.update(pd.Timestamp(until).tz_convert("UTC") if until else None)
    return e.write(out)


async def run_live(config, out=ROOT_DIR / "site" / "data", snap_root=None, interval=60.0, stop_at=None):
    e = Exporter(config, snap_root, "live")
    while stop_at is None or datetime.now(timezone.utc) < stop_at:
        started = datetime.now(timezone.utc)
        try:
            e.update()
            print(f"Spain live wrote {e.write(out)}", flush=True)
        except Exception as x:
            print(f"Spain live: {type(x).__name__}: {x}", flush=True)
        await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - started).total_seconds()))


# --------------------------------------------------------------------------------------------- recorder

def _save_snapshot(root: Path, key: str, body: bytes, t: datetime, suffix: str = ".json") -> None:
    h = hashlib.sha256(body).hexdigest()
    d = root / key
    d.mkdir(parents=True, exist_ok=True)
    old = sorted(d.glob("*" + suffix))
    if old and hashlib.sha256(old[-1].read_bytes()).hexdigest() == h:
        return
    f = d / f"{t:%Y%m%dT%H%M%S.%fZ}{suffix}"
    f.write_bytes(body)
    with (root / "index.jsonl").open("a") as out:
        out.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(root)), "sha256": h, "bytes": len(body)}) + "\n")


def feed_ready(manifest: dict) -> bool:
    """True once the feed says results exist: not embargoed and past the 'pre' phase or already counting."""
    if manifest.get("embargo"):
        return False
    return manifest.get("phase") != "pre" or float(manifest.get("counted_pct") or 0) > 0


class _Limiter:
    """At most one request per `delay` seconds for the whole host."""

    def __init__(self, delay: float):
        self.delay, self._next = delay, 0.0

    async def wait(self):
        loop = asyncio.get_running_loop()
        now = loop.time()
        wait = max(0.0, self._next - now)
        self._next = max(now, self._next) + self.delay
        if wait:
            await asyncio.sleep(wait)


async def record_feed(cfg: dict, interval: float = 30, until: datetime | None = None, root: Path | str = SNAP_ROOT):
    """Poll the manifest; on every new publication (seq) fetch the national file and all 52 province files."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = cfg["feed"]["base"].rstrip("/")
    lim = _Limiter(float(cfg["feed"].get("request_delay_seconds", 1.1)))
    patterns = cfg.get("lanes")
    last_seq, pending, got = None, [], {}
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as client:
        async def get(url: str) -> bytes | None:
            await lim.wait()
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return r.content
                print(f"Spain feed {r.status_code}: {url}", flush=True)
            except Exception as e:
                print(f"Spain feed: {type(e).__name__}: {e}", flush=True)
            return None

        while until is None or datetime.now(timezone.utc) < until:
            started = datetime.now(timezone.utc)
            body = await get(f"{base}/manifest.json")
            if body is not None:
                try:
                    m = json.loads(body)
                    _save_snapshot(root, "manifest", body, started)
                    if feed_ready(m) and (m["seq"] != last_seq or pending):
                        seq_base = m["base"].rstrip("/")
                        if m["seq"] != last_seq:
                            pending = ["es"] + [f"prov/{c}" for c in PROVINCES]
                            got = {}
                        for item in list(pending):
                            data = await get(f"{seq_base}/{item}.json")
                            if data is None:
                                continue
                            try:
                                if item.startswith("prov/"):
                                    parse_province(data, patterns)
                                else:
                                    json.loads(data)
                            except Exception as e:
                                print(f"Spain feed {item}: {type(e).__name__}: {e}", flush=True)
                                continue
                            got[item] = data
                            pending.remove(item)
                        if not pending:
                            for item, data in got.items():
                                key = "es" if item == "es" else f"prov/{uid_of(item.split('/')[1])}"
                                _save_snapshot(root, key, data, started)
                            last_seq, got = m["seq"], {}
                except Exception as e:
                    print(f"Spain manifest: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - started).total_seconds()))


async def record(config, interval=30.0, until=None, snap_root=None):
    cfg = load_config(config)
    root = Path(snap_root or SNAP_ROOT)
    events = list(cfg["markets"]["polymarket_events"])
    tickers = sorted({r["kalshi"] for r in cfg["races"] if r.get("kalshi")})
    jobs = [record_feed(cfg, interval, until, root), snap.run(root.name, [], events, 30, until, out_root=root.parent),
            kalshi.record(root.name, tickers, 30, until, out_root=root.parent)]
    await asyncio.gather(*jobs)


def cli(a) -> None:
    stop = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
    if a.step == "record":
        asyncio.run(record(a.config, a.interval, stop, a.snap))
    elif a.step == "preelection":
        print(preelection(a.config, a.out, a.force))
    elif a.step == "export":
        print(export(a.config, a.out, a.snap, a.until))
    elif a.step == "live":
        asyncio.run(run_live(a.config, a.out, a.snap, a.interval, stop))
