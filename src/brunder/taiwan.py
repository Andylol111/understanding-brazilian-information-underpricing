"""Taipei mayor 2026 forward test: CEC recorder, li-level nowcast and dashboard export."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import kalshi, polymarket, snap
from .paths import RAW, ROOT as ROOT_DIR

EID = "tw2026_taipei"
NAME = "Taiwan: Taipei mayoral election 2026"
TAG = "taipei_2026"
POLL_CLOSE = "2026-11-28T08:00:00Z"
HISTORY = ROOT_DIR / "data_public" / "tw_taipei_mayor_2014_2022.csv"
SNAP_ROOT = RAW / "snap" / TAG
UA = "brunder/0.1 (research; recorder)"
PARTIES = ("KMT", "DPP", "TPP", "OTHER")
DISTRICT_CODES = {
    "010": "松山區", "020": "信義區", "030": "大安區", "040": "中山區",
    "050": "中正區", "060": "大同區", "070": "萬華區", "080": "文山區",
    "090": "南港區", "100": "內湖區", "110": "士林區", "120": "北投區",
}
COLORS = {"KMT": "#000095", "DPP": "#1B9431", "OTHER": "#777777"}


def load_config(path: Path | str) -> dict:
    path = Path(path)
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def load_history(path: Path | str = HISTORY) -> pd.DataFrame:
    d = pd.read_csv(path, comment="#", dtype={"station_id": str})
    for c in (*PARTIES, "valid"):
        d[c] = pd.to_numeric(d[c], errors="raise")
    return d


def historical_units(year: int, history: pd.DataFrame | None = None) -> pd.DataFrame:
    d = (load_history() if history is None else history)
    d = d[d["year"] == year]
    g = d.groupby(["unit_id", "district_name", "li_name"], as_index=False)[[*PARTIES, "valid"]].sum()
    places = d.groupby("unit_id")["station_id"].nunique()
    g["stations"] = g["unit_id"].map(places)
    return g.set_index("unit_id")


def _rake_profiles(profiles: np.ndarray, volume: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Multiplicatively rake unit compositions to an exact citywide target."""
    mult = np.ones(len(target))
    for _ in range(100):
        p = profiles * mult
        p /= p.sum(axis=1, keepdims=True)
        current = (p * volume[:, None]).sum(axis=0) / volume.sum()
        mult *= target / np.maximum(current, 1e-12)
    p = profiles * mult
    return p / p.sum(axis=1, keepdims=True)


def prior_units(cfg: dict, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build 2026 candidate profiles from official 2022 li results and the frozen poll target."""
    old = historical_units(int(cfg["prior"]["year"]), history)
    cands = cfg["candidates"]
    raw = []
    transfer = float(cfg["prior"].get("tpp_to_kmt", .8))
    for c in cands:
        profile = c["profile"]
        if profile == "KMT":
            x = old["KMT"].to_numpy(float) + transfer * old["TPP"].to_numpy(float)
        elif profile == "DPP":
            x = old["DPP"].to_numpy(float) + (1 - transfer) * old["TPP"].to_numpy(float)
        else:
            x = old["OTHER"].to_numpy(float)
        raw.append(x + 1.0)
    raw = np.column_stack(raw)
    raw /= raw.sum(axis=1, keepdims=True)
    target = np.array([float(c["prior_share"]) for c in cands])
    if not np.isclose(target.sum(), 1, atol=1e-6):
        raise ValueError("candidate prior_share values must sum to one")
    p = _rake_profiles(raw, old["valid"].to_numpy(float), target)
    out = old[["district_name", "li_name", "valid", "stations"]].rename(columns={"valid": "V0"}).copy()
    out["region"] = [f"tw-{x}" for x in out.index.str.split("-").str[1]]
    for i, c in enumerate(cands):
        out[c["id"]] = p[:, i]
    return out


def historical_prior(prev_year: int, target: dict[str, float], history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Prior used by the calibration/replay script for KMT/DPP/Ko-Huang/other lanes."""
    old = historical_units(prev_year, history)
    raw = old[list(PARTIES)].to_numpy(float) + 1.0
    raw /= raw.sum(axis=1, keepdims=True)
    p = _rake_profiles(raw, old["valid"].to_numpy(float), np.array([target[x] for x in PARTIES]))
    out = old[["district_name", "li_name", "valid", "stations"]].rename(columns={"valid": "V0"}).copy()
    out["region"] = [f"tw-{x}" for x in out.index.str.split("-").str[1]]
    out[list(PARTIES)] = p
    return out


@dataclass(frozen=True)
class Settings:
    own_votes: float = 100.0
    swing_votes: float = 5_000.0
    growth_votes: float = 100_000.0
    tau: float = .02
    floor_sd: float = .002
    precount_sd: float = .06
    draws: int = 20_000


def settings(cfg: dict) -> Settings:
    m = cfg.get("model", {})
    return Settings(**{f.name: m[f.name] for f in fields(Settings) if f.name in m})


@dataclass
class Projection:
    candidates: list[str]
    shares: np.ndarray
    raw_shares: np.ndarray
    votes: np.ndarray
    unit: pd.DataFrame
    counted_frac: float
    sd: float


def empty_counts(units: pd.DataFrame, candidates: list[str]) -> pd.DataFrame:
    d = pd.DataFrame(0.0, index=units.index, columns=[*candidates, "places"])
    return d


def project(units: pd.DataFrame, count: pd.DataFrame, st: Settings, candidates: list[str]) -> Projection:
    """Unit prior + nationally pooled swing + partly counted li composition."""
    p0 = units[candidates].to_numpy(float)
    V0 = units["V0"].to_numpy(float)
    S0 = units["stations"].to_numpy(float)
    c = count.reindex(units.index).fillna(0)
    cv = c[candidates].to_numpy(float)
    n = cv.sum(axis=1)
    places = c["places"].to_numpy(float)
    f = np.divide(places, S0, out=np.zeros_like(places), where=S0 > 0)

    expected = (p0 * (V0 * f)[:, None]).sum(axis=0)
    observed = cv.sum(axis=0)
    if observed.sum() == 0:
        base = p0.copy()
    else:
        expected_share = expected / expected.sum()
        observed_share = (observed + st.swing_votes * expected_share) / (observed.sum() + st.swing_votes)
        tilt = observed_share / np.maximum(expected_share, 1e-9)
        base = p0 * tilt
        base /= base.sum(axis=1, keepdims=True)

    local = np.divide(cv, n[:, None], out=base.copy(), where=n[:, None] > 0)
    weight = np.divide(n, n + st.own_votes * np.maximum(1 - f, 0), out=np.zeros_like(n),
                       where=(n + st.own_votes * np.maximum(1 - f, 0)) > 0)
    remaining_share = (1 - weight[:, None]) * base + weight[:, None] * local
    remaining_share /= remaining_share.sum(axis=1, keepdims=True)
    growth = (n.sum() + st.growth_votes) / (np.sum(V0 * f) + st.growth_votes)
    total = np.maximum(n, V0 * growth)
    votes = cv + np.maximum(total - n, 0)[:, None] * remaining_share
    shares = votes.sum(axis=0) / votes.sum()
    counted = float(n.sum() / votes.sum())
    sd = max(st.floor_sd, (st.precount_sd if n.sum() == 0 else st.tau * (1 - counted)))

    u = pd.DataFrame(index=units.index)
    u["f"] = np.minimum(1, f)
    u["raw"] = np.divide(cv[:, 0], n, out=np.full(len(n), np.nan), where=n > 0)
    u["projected"] = votes[:, 0] / votes.sum(axis=1)
    raw_shares = observed / observed.sum() if observed.sum() else np.full(len(candidates), np.nan)
    return Projection(candidates, shares, raw_shares, votes, u, counted, sd)


def win_probabilities(p: Projection, st: Settings, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    draws = p.shares + rng.normal(0, p.sd, (st.draws, len(p.candidates)))
    winners = draws.argmax(axis=1)
    return {c: float(np.mean(winners == i)) for i, c in enumerate(p.candidates)}


class _ResultHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._row = []
        elif tag in ("td", "th") and self._row is not None: self._cell = []

    def handle_data(self, data):
        x = data.strip()
        if x:
            self.text.append(x)
            if self._cell is not None: self._cell.append(x)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell)); self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row: self.rows.append(self._row)
            self._row = None


def parse_result_html(data: bytes | str, candidate_names: dict[str, str]) -> dict:
    """Parse one official vote-site aggregate fragment (same HTML contract as vote2024.cec.gov.tw)."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    p = _ResultHTML(); p.feed(text)
    all_text = " ".join(p.text)
    m = re.search(r"已送投票所數\s*/\s*總投票所數\s*[:：]\s*([\d,]+)\s*/\s*([\d,]+)", all_text)
    if not m:
        raise ValueError("CEC result page has no polling-station progress")
    places, total = (int(x.replace(",", "")) for x in m.groups())
    votes = {k: 0 for k in candidate_names}
    reverse = {v: k for k, v in candidate_names.items()}
    for row in p.rows:
        if len(row) >= 3 and row[1] in reverse:
            votes[reverse[row[1]]] = int(row[2].replace(",", ""))
    if not any(votes.values()) and places:
        raise ValueError("CEC result page has progress but no configured candidate names")
    stamp = re.search(r"資料時間\s*[:：]\s*(\d{2}/\d{2}\s+\d{2}:\d{2})", all_text)
    return {"places": places, "places_total": total, "votes": votes,
            "stamp": stamp.group(1) if stamp else None}


def parse_static_results(data: bytes | str, candidate_map: dict[str, str]) -> pd.DataFrame:
    """Parse the official db.cec.gov.tw T-level JSON into one row per polling station."""
    obj = json.loads(data)
    rows = [r for values in obj.values() for r in values]
    d = pd.DataFrame(rows)
    d["candidate"] = d["cand_name"].map(candidate_map).fillna("OTHER")
    g = d.groupby(["dept_code", "li_code", "tbox_no", "candidate"])["ticket_num"].sum().unstack(fill_value=0)
    g.index = pd.MultiIndex.from_tuples([(f"tw-{a}-{b}", c) for a, b, c in g.index], names=["unit_id", "station_id"])
    return g


def parse_2026_districts(data: bytes | str) -> pd.DataFrame:
    """Parse the 2026 election site's published Taipei district/li topology (not vote results)."""
    obj = json.loads(data)
    rows = [{"unit_id": f"tw-{d['deptCode']}-{int(li['liCode']):04d}",
             "district_name": d["deptName"], "li_name": li["liName"]}
            for d in obj["depts"] for li in d["lis"]]
    out = pd.DataFrame(rows).set_index("unit_id")
    if out.index.has_duplicates:
        raise ValueError("CEC 2026 topology contains duplicate li codes")
    return out


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
        self.root = Path(root); self.files: dict[str, list[tuple[pd.Timestamp, Path]]] = {}; self.refresh()

    def refresh(self):
        out: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
        p = self.root / "index.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    x = json.loads(line); f = self.root / x["file"]
                    if f.exists(): out.setdefault(x["key"], []).append((pd.Timestamp(x["t"]).tz_convert("UTC"), f))
                except Exception: continue
        for rows in out.values(): rows.sort()
        self.files = out

    def times(self): return sorted({t for rows in self.files.values() for t, _ in rows})

    def at(self, key: str, t: pd.Timestamp) -> Path | None:
        rows = self.files.get(key, []); lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= t: lo = mid + 1
            else: hi = mid
        return rows[lo - 1][1] if lo else None


def _book_prices(arc: Archive, path: Path | None) -> dict[str, float]:
    if path is None: return {}
    out = {}
    for k, b in json.loads(path.read_text()).items():
        bids, asks = b.get("bids") or [], b.get("asks") or []
        bid = max((float(x["price"]) for x in bids), default=np.nan)
        ask = min((float(x["price"]) for x in asks), default=np.nan)
        out[k] = (bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) and ask - bid <= .1 else float(b.get("last_trade_price") or np.nan)
    return out


def _kalshi_prices(arc: Archive, path: Path | None) -> dict[str, float]:
    if path is None: return {}
    return {k: kalshi.prob_from_book(v) for k, v in json.loads(path.read_text()).items()}


def _tail(timeline: dict[str, list], minutes: int = 60) -> dict:
    out, first = {}, []
    for k, rows in timeline.items():
        if rows:
            cut = pd.Timestamp(rows[-1]["t"]) - pd.Timedelta(minutes=minutes)
            out[k] = [r for r in rows if pd.Timestamp(r["t"]) > cut]; first.append(out[k][0]["t"])
        else: out[k] = []
    return {**out, "tail": True, "since": min(first) if first else None}


def _market_info(cfg: dict) -> dict[str, dict]:
    out = {}
    try:
        event = polymarket.event(cfg["markets"]["polymarket_event"])
        out = {m["slug"]: m for m in event["markets"]}
    except Exception as e:
        print(f"Taipei Gamma metadata: {type(e).__name__}: {e}", flush=True)
    return out


def meta(cfg: dict, phase: str, probs: dict[str, float], info: dict[str, dict] | None = None) -> dict:
    info = info or {}; races = []
    for c in cfg["candidates"]:
        markets = []
        if c.get("polymarket"):
            m = info.get(c["polymarket"], {})
            markets.append({"platform": "polymarket", "id": c["polymarket"],
                            "question": m.get("question", f"Will {c['name']} win Taiwan's Taipei mayor election?"),
                            "yes_is": "a", "token_yes": c.get("pm_token")})
        if c.get("kalshi"):
            markets.append({"platform": "kalshi", "id": c["kalshi"],
                            "question": f"Will {c['name']} win Taiwan's 2026 Taipei mayoral election?", "yes_is": "a"})
        races.append({"race_id": c["id"], "name": f"Taiwan: {c['name']} wins Taipei mayor",
                      "region": None, "a": {"id": c["id"], "name": c["name"], "color": c["color"]},
                      "b": {"id": "field", "name": "Field", "color": "#777777"}, "markets": markets,
                      **({"precount": {"P": _r(probs[c["id"]])}} if phase == "pre-count" else {})})
    regions = [{"id": f"tw-{code}", "name": name} for code, name in DISTRICT_CODES.items()]
    return {"id": EID, "name": NAME, "date": "2026-11-28", "country": "TW", "country_name": "Taiwan",
            "tz": "Asia/Taipei", "phase": phase, "poll_close_utc": POLL_CLOSE, "unit_key": "unit_id",
            "count_timeline": True, "regions": regions,
            "parties": [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in cfg["candidates"]],
            "races": races, "sources": [
                {"name": "Taiwan Central Election Commission live results", "url": cfg["feed"]["base"],
                 "licence": "Government open data; Taiwan Government Data Open Licence 1.0"},
                {"name": "CEC election database, 2014/2018/2022", "url": "https://db.cec.gov.tw/",
                 "licence": "Taiwan Government Data Open Licence 1.0"},
                {"name": "Public pre-election polls", "url": cfg["poll"]["source"], "licence": "reported toplines"},
                {"name": "Polymarket CLOB", "url": "https://polymarket.com", "licence": "public market data"},
                {"name": "© Kalshi order books", "url": "https://kalshi.com", "licence": "© Kalshi; see docs/kalshi.md"}],
            "notes": ["counted is the projected share of valid mayoral votes counted; f on the map is polling stations received within each li.",
                      "The 2022 TPP-aligned Huang vote is transferred 80% to KMT and 20% to DPP before raking to the frozen public-poll prior.",
                      "Historical count orders are simulated because CEC does not preserve timestamped election-night sequences.",
                      f"Model frozen before polls close ({Path(cfg['_path']).name}); evidence is forward."],
            "n_units": 456}


def units_object(units: pd.DataFrame, frames: list[dict], t: str, cfg: dict) -> dict:
    a, b = cfg["candidates"][0], cfg["candidates"][1]
    if not frames:
        frames = [{"t": t, "f": [0] * len(units), "a": [-1] * len(units),
                   "proj": np.rint(1000 * units[a["id"]]).astype(int).tolist()}]
    return {"unit_ids": units.index.tolist(), "region": units["region"].tolist(),
            "names": (units["district_name"] + " " + units["li_name"]).tolist(),
            "prior": np.rint(1000 * units[a["id"]]).astype(int).tolist(),
            "V0": np.rint(units["V0"]).astype(int).tolist(),
            "a": {"name": a["name"], "color": a["color"]},
            "b": {"name": b["name"], "color": b["color"]}, "frames": frames}


def _initial(cfg: dict):
    units = prior_units(cfg); cand = [c["id"] for c in cfg["candidates"]]; st = settings(cfg)
    p = project(units, empty_counts(units, cand), st, cand)
    return units, p, win_probabilities(p, st, int(cfg["seed"]))


def preelection(config: Path | str, out: Path | str = ROOT_DIR / "site" / "data", force: bool = False) -> Path:
    cfg = load_config(config); units, p, probs = _initial(cfg); info = _market_info(cfg)
    d = Path(out) / EID
    if (d / "meta.json").exists() and not force:
        old = json.loads((d / "meta.json").read_text()).get("phase")
        if old in ("live", "replay"): return d
    now = pd.Timestamp.now(tz="UTC").floor("min"); pp, kp = {}, {}
    try:
        km = kalshi.markets(kalshi.event(cfg["markets"]["kalshi_event"])).set_index("ticker")
        for ticker, row in km.iterrows():
            bid, ask, last = row["yes_bid"], row["yes_ask"], row["last"]
            kp[ticker] = ((bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) and ask - bid <= .1
                          else last if np.isfinite(last) else None)
    except Exception as e:
        print(f"Taipei Kalshi metadata: {type(e).__name__}: {e}", flush=True)
    for c in cfg["candidates"]:
        if c.get("polymarket") in info:
            prices = json.loads(info[c["polymarket"]].get("outcomePrices", "[]"))
            if prices: pp[c["id"]] = float(prices[0])
        if c.get("kalshi"): kp[c["id"]] = kp.get(c["kalshi"])
    timeline = {c["id"]: [{"t": _iso(now), "counted": None, "raw": None, "share": _r(p.shares[i]),
                    "sd": _r(p.sd), "P": _r(probs[c["id"]]), "pm": _r(pp.get(c["id"])),
                    "kalshi": _r(kp.get(c["id"]))}] for i, c in enumerate(cfg["candidates"])}
    _write_json(d / "meta.json", meta(cfg, "pre-count", probs, info)); _write_json(d / "timeline_precount.json", timeline)
    _write_json(d / "timeline.json", timeline); _write_json(d / "timeline_tail.json", _tail(timeline))
    _write_json(d / "units.json", units_object(units, [], _iso(now), cfg))
    _write_json(d / "stats.json", {"calibration": None, "settle": None, "reasons": {"calibration": "count has not started"}})
    _write_json(d / "arb.json", []); _write_json(d / "entry.json", entry())
    return d


def entry() -> dict:
    return {"id": EID, "name": NAME, "date": "2026-11-28", "country": "TW", "country_name": "Taiwan",
            "status": "scheduled", "unit_level": "li", "n_units": 456, "evidence": "forward",
            "summary": "A frozen Taiwan li-level mayoral nowcast follows official CEC returns live against Polymarket and Kalshi."}


class Exporter:
    def __init__(self, config: Path | str, snap_root: Path | str | None = None, phase: str = "live"):
        self.cfg = load_config(config); self.arc = Archive(snap_root or SNAP_ROOT); self.phase = phase
        self.units = prior_units(self.cfg); self.candidates = [c["id"] for c in self.cfg["candidates"]]
        self.timeline = {c: [] for c in self.candidates}; self.frames = []; self.cursor = pd.Timestamp(POLL_CLOSE) - pd.Timedelta(minutes=1)
        self.st = settings(self.cfg); self.info = {}; self._sig = None; self._result = None

    def _model(self, t: pd.Timestamp):
        count = empty_counts(self.units, self.candidates); sig = []
        for uid in self.units.index:
            f = self.arc.at(f"results/{uid}", t)
            if f is None: continue
            sig.append(f)
            x = json.loads(f.read_text()); count.loc[uid, self.candidates] = [x["votes"].get(c, 0) for c in self.candidates]
            count.loc[uid, "places"] = x["places"]
        key = tuple(sig)
        if key == self._sig: return self._result
        p = project(self.units, count, self.st, self.candidates)
        probs = win_probabilities(p, self.st, int(self.cfg["seed"]))
        self._sig, self._result = key, (p, probs)
        return self._result

    def update(self, until: pd.Timestamp | None = None):
        self.arc.refresh(); times = self.arc.times()
        if not times: return
        end = min(until, times[-1]) if until is not None else times[-1]
        for t in pd.date_range(self.cursor + pd.Timedelta(minutes=1), end.floor("min"), freq="1min"):
            p, probs = self._model(t); pp = _book_prices(self.arc, self.arc.at("polymarket_books", t)); kp = _kalshi_prices(self.arc, self.arc.at("kalshi_books", t))
            for i, c in enumerate(self.cfg["candidates"]):
                pmkey = f"{self.cfg['markets']['polymarket_event']}/{c.get('polymarket')}"
                self.timeline[c["id"]].append({"t": _iso(t), "counted": _r(p.counted_frac), "raw": _r(p.raw_shares[i]),
                    "share": _r(p.shares[i]), "sd": _r(p.sd), "P": _r(probs[c["id"]]),
                    "pm": _r(pp.get(pmkey)), "kalshi": _r(kp.get(c.get("kalshi")))})
            if not self.frames or t - pd.Timestamp(self.frames[-1]["t"]) >= pd.Timedelta(minutes=5):
                self.frames.append({"t": _iso(t), "f": np.rint(1000 * p.unit["f"]).astype(int).tolist(),
                    "a": np.rint(1000 * p.unit["raw"].fillna(-.001)).astype(int).tolist(),
                    "proj": np.rint(1000 * p.unit["projected"]).astype(int).tolist()})
                if len(self.frames) > 120: self.frames[:] = self.frames[::-2][::-1]
            self.cursor = t

    def write(self, out: Path | str):
        d = Path(out) / EID; now = pd.Timestamp.now(tz="UTC").floor("s")
        phase = (("pre-count" if now < pd.Timestamp(POLL_CLOSE) else "live") if self.phase == "live" else self.phase)
        p, probs = self._model(self.cursor); pre = {}
        try: pre = json.loads((d / "timeline_precount.json").read_text())
        except Exception: pass
        merged = {k: [r for r in pre.get(k, []) if not self.timeline[k] or r["t"] < self.timeline[k][0]["t"]] + self.timeline[k]
                  for k in self.timeline}
        _write_json(d / "meta.json", meta(self.cfg, phase, probs, self.info)); _write_json(d / "timeline.json", merged)
        _write_json(d / "timeline_tail.json", _tail(merged)); _write_json(d / "units.json", units_object(self.units, self.frames, _iso(now), self.cfg))
        if not (d / "timeline_precount.json").exists(): _write_json(d / "timeline_precount.json", merged)
        if not (d / "stats.json").exists(): _write_json(d / "stats.json", {"calibration": None, "reasons": {"calibration": "computed after count"}})
        if not (d / "arb.json").exists(): _write_json(d / "arb.json", [])
        if not (d / "entry.json").exists(): _write_json(d / "entry.json", entry())
        return d


def export(config, out=ROOT_DIR / "site" / "data", snap_root=None, until=None):
    e = Exporter(config, snap_root, "replay"); e.update(pd.Timestamp(until).tz_convert("UTC") if until else None); return e.write(out)


async def run_live(config, out=ROOT_DIR / "site" / "data", snap_root=None, interval=60.0, stop_at=None):
    e = Exporter(config, snap_root, "live")
    while stop_at is None or datetime.now(timezone.utc) < stop_at:
        started = datetime.now(timezone.utc)
        try:
            e.update(); print(f"Taipei live wrote {e.write(out)}", flush=True)
        except Exception as x: print(f"Taipei live: {type(x).__name__}: {x}", flush=True)
        await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - started).total_seconds()))


def _save_snapshot(root: Path, key: str, body: bytes, t: datetime, suffix: str = ".json") -> None:
    h = hashlib.sha256(body).hexdigest(); d = root / key; d.mkdir(parents=True, exist_ok=True)
    old = sorted(d.glob("*" + suffix))
    if old and hashlib.sha256(old[-1].read_bytes()).hexdigest() == h: return
    f = d / f"{t:%Y%m%dT%H%M%S.%fZ}{suffix}"; f.write_bytes(body)
    with (root / "index.jsonl").open("a") as out:
        out.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(root)), "sha256": h, "bytes": len(body)}) + "\n")


def _cec_unit_serial(uid: str) -> str:
    """CEC's 17-digit city/district/li/station key, with station zero denoting a li aggregate."""
    country, dept, li = uid.split("-")
    if country != "tw":
        raise ValueError(f"not a Taiwan unit id: {uid}")
    return f"63000{int(dept):04d}{int(li):04d}0000"


def _cec_result_url(base: str, prefix: str, uid: str) -> str:
    """Candidate live-result URL under the official 2026 app; the route remains unverified pre-election."""
    return f"{base.rstrip('/')}/zh-TW/{prefix}/{_cec_unit_serial(uid)}.html"


async def record_feed(cfg: dict, interval: float = 600, until: datetime | None = None, root: Path | str = SNAP_ROOT):
    """Poll 456 candidate li aggregate paths at <=1 request/2 s; store only strictly parsed results."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True); units = prior_units(cfg)
    names = {c["id"]: c["name_zh"] for c in cfg["candidates"]}; prefixes = cfg["feed"].get("prefixes", ["M1", "C1"])
    headers = {"User-Agent": UA}; chosen = None
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        metadata_url = cfg["feed"].get("metadata_url")
        if metadata_url:
            try:
                r = await client.get(metadata_url)
                r.raise_for_status()
                topology = parse_2026_districts(r.content)
                missing = set(units.index) - set(topology.index)
                if missing:
                    raise ValueError(f"CEC 2026 topology is missing {len(missing)} prior li")
                _save_snapshot(root, "cec_2026_topology", r.content, datetime.now(timezone.utc))
            except Exception as e:
                print(f"Taipei topology: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(float(cfg["feed"].get("request_delay_seconds", 2.0)))
        while until is None or datetime.now(timezone.utc) < until:
            started = datetime.now(timezone.utc)
            for uid in units.index:
                result = None
                for prefix in ([chosen] if chosen else prefixes):
                    url = _cec_result_url(cfg["feed"]["base"], prefix, uid)
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            result = parse_result_html(r.content, names); chosen = prefix; break
                        if r.status_code not in (403, 404): print(f"Taipei feed {r.status_code}: {url}", flush=True)
                    except Exception as e: print(f"Taipei feed: {type(e).__name__}: {e}", flush=True)
                if result is not None:
                    _save_snapshot(root, f"results/{uid}", json.dumps(result, ensure_ascii=False, sort_keys=True).encode(), datetime.now(timezone.utc))
                await asyncio.sleep(float(cfg["feed"].get("request_delay_seconds", 2.0)))
            await asyncio.sleep(max(0, interval - (datetime.now(timezone.utc) - started).total_seconds()))


async def record(config, interval=600.0, until=None, snap_root=None):
    cfg = load_config(config); root = Path(snap_root or SNAP_ROOT)
    events = [cfg["markets"]["polymarket_event"]]
    tickers = [c["kalshi"] for c in cfg["candidates"] if c.get("kalshi")]
    jobs = [record_feed(cfg, interval, until, root), snap.run(root.name, [], events, 30, until, out_root=root.parent),
            kalshi.record(root.name, tickers, 30, until, out_root=root.parent)]
    await asyncio.gather(*jobs)


def cli(a) -> None:
    stop = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
    if a.step == "record": asyncio.run(record(a.config, a.interval, stop, a.snap))
    elif a.step == "preelection": print(preelection(a.config, a.out, a.force))
    elif a.step == "export": print(export(a.config, a.out, a.snap, a.until))
    elif a.step == "live": asyncio.run(run_live(a.config, a.out, a.snap, a.interval, stop))
