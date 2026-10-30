"""US midterms 2026: post-election scoring of the registered forward test, plus the display-only candidates for future
versions, against the same outcomes. Reads only; safe to re-run whenever results update (every run rewrites the
outputs from scratch). Definitions and output schema: docs/us_scoring.md.

    uv run brunder us score [configs/us2026_*_v1.toml] [--out site/data] [--snap <recorder dir>]
    uv run python analysis/us_score.py [--out site/data] [--snap DIR] [--final]

Inputs
  <out>/us2026/timeline.json     the live / replay model output (brunder us live|export): per race and minute the
                                 count, every registered version's P / share / sd, and the Polymarket / Kalshi P(a)
  <snap>                         the recorder's archive (brunder.us.Archive): the official feeds (provisional
                                 outcome = the latest snapshot) and polymarket_books (order books for execution)
  data_public/us2026_certified.csv           certified statewide results (state canvass), one row per race
  data_public/us2026_certified_counties.csv  certified county results of the modelled races (optional)
  configs/us2026_*_v*.toml, prereg/registry.jsonl (hash check; nothing is written there except by --final)
  <out>/us2026/overview.json, hub/polls.json, hub/crosstabs.json, data_public/us_county_panel.csv,
  data_public/us2026_expected_votes_v4.csv
  <snap>_ws/ws_crossings.jsonl, logs/us2026_updates.jsonl   brunder.pm_ws threshold crossings and the live model's update
                                 log (display block `latency`: analysis/us_latency.py)

Outcome per race: the certified row when present ("certified"), else the latest official feed snapshot in the archive
("provisional": the night's final official feed, not certified), else none. Every block states which it used.

Registered reading (prereg/NOTES.md, US section): per race and version margin error, turnout error, Brier / log loss,
90 % band coverage at fixed counted fractions, the settle lead (model from the 1 % gate vs Polymarket first staying
>= 0.9 on the winner), the 30-minute catch-up slope pooled over markets, the frozen favoured-side edge rule (min_edge
0.05, min_counted 0.01, configs/night_r2_v1.toml [trade]) executed on the recorded books at the signal price and at
the model limit (analysis/edge_audit.py semantics), and Senate control. Display-only candidates: crosstab
recalled-vote adjusted averages, county fundamentals (us_fundamentals Fit, 2026 as the test cycle) and the county
drop-off expected vote (v4 vs v3 vs the display turnout index).

--final: the registered look. Refuses unless every modelled race with a count has a certified result; logs one look
per scored registered config in prereg/holdout_runs.jsonl (brunder.registry.open_holdout) and keeps a copy of that
score.json in results/us_score/. Ordinary runs are monitoring and are labelled as such.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import live, registry, us
from brunder.paths import PROCESSED

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_audit as ea  # noqa: E402
import underpricing_figures as uf  # noqa: E402
import us_latency as ul  # noqa: E402

ROOT = us.ROOT_DIR
CERT = ROOT / "data_public" / "us2026_certified.csv"
CERT_COUNTIES = ROOT / "data_public" / "us2026_certified_counties.csv"
REGISTRY = ROOT / "prereg" / "registry.jsonl"
RES = ROOT / "results" / "us_score"
TRADE_RULE = ROOT / "configs" / "night_r2_v1.toml"        # the frozen favoured-side rule ([trade])
FIRST_CLOSE = pd.Timestamp("2026-11-03T23:00:00Z")       # first polls close (Indiana / Kentucky)
QS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90)           # fixed counted fractions (timeline `counted`, v1 axis)
Z90 = 1.645
SETTLE = 0.9
GATE = 0.01
CATCHUP_MIN = 30
LATENCY_S = (0, 30, 60)
SLIPS = (0.0, 0.02, 0.05)
HEAD = {"latency_s": 60, "limit": "signal", "slip": 0.0}  # headline execution (edge_audit HEAD_BR / HEAD_IL)
EPS = ea.EPS


def _r(x, n: int = 4):
    return ea._r(x, n)


def _iso(t) -> str | None:
    return ea._iso(t)


def _mins(a, b) -> float | None:
    """Minutes from a to b (b − a); None when either is missing."""
    return None if a is None or b is None or pd.isna(a) or pd.isna(b) else _r((b - a).total_seconds() / 60, 1)


# ============================================================== configs, registration, outcomes

def default_configs() -> list[Path]:
    """The statewide Senate / governor forward test (prereg/NOTES.md). House district configs
    (us2026_house_*) are a separate study with their own feeds (brunder.us_house_night) and are not scored here."""
    return sorted(p for p in (ROOT / "configs").glob("us2026_*_v1.toml") if not p.name.startswith("us2026_house_"))


def registered_ids(path: Path = REGISTRY) -> dict[str, str]:
    return {r["id"]: r["sha256"] for r in registry._read(path)}


def version_cfgs(cfg_paths: list[Path]) -> tuple[list[dict], dict[str, dict[str, dict]], dict[str, str]]:
    """(v1 configs, {race: {"v1": cfg, "v2": cfg, …}}, {race: primary version}). Primary = brunder.us.poll_model
    (the latest non-secondary version with a poll prior: v3, Ohio v4), as registered in prereg/NOTES.md."""
    cfgs, later = us.load_races(cfg_paths)
    allv, prim = {}, {}
    for c in cfgs:
        rid = c["election"]["race_id"]
        allv[rid] = {"v1": c, **later.get(rid, {})}
        prim[rid] = us.poll_model(later.get(rid, {})) or "v1"
    return cfgs, allv, prim


def registration(cfg: dict, reg: dict[str, str]) -> dict:
    import hashlib
    sha = hashlib.sha256(Path(cfg["_path"]).read_bytes()).hexdigest()
    return {"id": cfg["id"], "registered": reg.get(cfg["id"]) == sha,
            "note": None if reg.get(cfg["id"]) == sha else
            ("not in prereg/registry.jsonl" if cfg["id"] not in reg else "file differs from its registered sha256")}


def _read_csv(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path, comment="#", dtype={"race": str, "unit_id": str}) if path.exists() else None


def certified(path: Path = CERT) -> dict[str, dict]:
    """race -> {dem, rep, total, certified, source} from the committed certified results file."""
    d = _read_csv(path)
    if d is None:
        return {}
    return {r["race"]: {"dem": float(r["dem"]), "rep": float(r["rep"]), "total": float(r["total"]),
                        "certified": str(r.get("certified", "")), "source": str(r["source"])} for _, r in d.iterrows()}


def certified_counties(path: Path = CERT_COUNTIES) -> dict[str, pd.DataFrame]:
    d = _read_csv(path)
    return {} if d is None else {k: g[["unit_id", "dem", "rep", "total"]].reset_index(drop=True)
                                  for k, g in d.groupby("race")}


def feed_rows(arc: us.Archive, kind: str, t: pd.Timestamp, memo: dict) -> pd.DataFrame:
    """Parsed rows of one feed at t (latest valid snapshots), memoised by the snapshot set."""
    files = us.feed_files(arc, kind, t)
    sig = (kind,) + tuple(x for f in files.values() for x in (f if isinstance(f, list) else [f]) if x is not None)
    if sig not in memo:
        memo[sig] = us.parse_feed(kind, us.feed_blobs(arc, files)) if len(sig) > 1 else us._frame([])
    return memo[sig]


def provisional(arc: us.Archive, c: dict, memo: dict) -> tuple[dict | None, pd.DataFrame | None]:
    """(statewide result, county result) from the latest official feed snapshot of the race's feed, or (None, None)."""
    kind, race = c["election"]["feed"], c["election"]["race"]
    files = us.feed_files(arc, kind, pd.Timestamp.max.tz_localize("UTC"))
    ts = [arc_t(p) for f in files.values() for p in (f if isinstance(f, list) else [f]) if p is not None]
    if not ts:
        return None, None
    try:
        rows = feed_rows(arc, kind, max(ts), memo)
    except Exception as e:                          # a feed whose last snapshot does not parse: no provisional result
        return {"error": f"{type(e).__name__}: {e}"}, None
    rows = rows[rows["race"] == race]
    if rows.empty:
        return None, None
    sides = sorted((c["election"]["a"]["party"], c["election"]["b"]["party"]), key=lambda p: p == "REP")
    u = rows[rows["level"] != "state"]                 # dem = the dem side (DEM, or the flagged independent's code)
    g = u.pivot_table(index="unit_id", columns="party", values="votes", aggfunc="sum", fill_value=0)
    cty = pd.DataFrame({"dem": g.get(sides[0], 0), "rep": g.get(sides[1], 0), "total": g.sum(axis=1)}).reset_index()
    if cty.empty or cty[["dem", "rep"]].to_numpy().sum() == 0:
        return None, None
    tot = cty[["dem", "rep", "total"]].sum()
    return ({"dem": float(tot["dem"]), "rep": float(tot["rep"]), "total": float(tot["total"]),
             "snapshot": _iso(max(ts)),
             "source": f"official election-night feed '{kind}', latest recorded snapshot (not certified)"},
            cty[["unit_id", "dem", "rep", "total"]].reset_index(drop=True))


def arc_t(f: Path) -> pd.Timestamp:
    return pd.Timestamp(datetime.strptime(f.name[:22], "%Y%m%dT%H%M%S.%f"), tz="UTC")


def outcomes(cfgs: list[dict], arc: us.Archive, cert: dict, cert_cty: dict, memo: dict) -> dict[str, dict]:
    """Per modelled race: status certified / provisional / none, two-party share of side a, totals, county frame."""
    out = {}
    for c in cfgs:
        rid = c["election"]["race_id"]
        a_party = c["election"]["a"]["party"]
        prov, prov_cty = provisional(arc, c, memo)
        if rid in cert:
            res, cty, status = cert[rid], cert_cty.get(rid), "certified"
            if cty is None and prov_cty is not None:
                cty = prov_cty                                       # county detail stays provisional, labelled
        elif prov and "error" not in prov:
            res, cty, status = prov, prov_cty, "provisional"
        else:
            out[rid] = {"status": "none", "reason": (prov or {}).get("error", "no certified row and no feed snapshot")}
            continue
        a, b = (res["rep"], res["dem"]) if a_party == "REP" else (res["dem"], res["rep"])   # dem = the dem side
        share = a / (a + b)
        out[rid] = {"status": status, "share_a": share, "margin_pts": 100 * (2 * share - 1), "a_won": int(share > 0.5),
                    "two_party": a + b, "total": res["total"], "source": res["source"],
                    "as_of": res.get("certified") or res.get("snapshot"),
                    "county": cty, "county_status": "certified" if rid in cert_cty else ("provisional" if cty is not None else None)}
    return out


# ============================================================== registered metrics per race and version

def versions_of(rows: list[dict], allv: dict) -> list[str]:
    return [v for v in allv if v == "v1" or any(r.get(f"P_{v}") is not None for r in rows)]


def cols(v: str) -> tuple[str, str, str]:
    return ("P", "share", "sd") if v == "v1" else (f"P_{v}", f"share_{v}", f"sd_{v}")


def frame(rows: list[dict], v: str) -> pd.DataFrame:
    P, s, sd = cols(v)
    d = pd.DataFrame(rows)
    d["t"] = pd.to_datetime(d["t"], utc=True)
    for k in (P, s, sd, "counted", "raw", "pm", "kalshi", "votes_proj"):
        if k not in d:
            d[k] = np.nan
    d = d.rename(columns={P: "Pv", s: "sv", sd: "sdv"})
    return d[["t", "counted", "raw", "Pv", "sv", "sdv", "pm", "kalshi", "votes_proj"]].astype(
        {k: float for k in ("counted", "raw", "Pv", "sv", "sdv", "pm", "kalshi", "votes_proj")}).sort_values("t") \
        .reset_index(drop=True)


def settle_times(d: pd.DataFrame, a_won: int) -> dict:
    """Gate (first row >= 1 % counted), model settle from the gate (max of its settle and the gate), Polymarket and
    Kalshi settle (first staying >= 0.9 on the winner, whole series), leads in minutes (positive: model first)."""
    gate_rows = d[d["counted"] >= GATE]
    gate = gate_rows["t"].iloc[0] if len(gate_rows) else pd.NaT
    out = {"gate": gate}
    m = d.dropna(subset=["Pv"])
    if len(m):
        win = m["Pv"] if a_won else 1 - m["Pv"]
        s = ea.settle(m["t"], win >= SETTLE)
        out["model_raw"] = s
        out["model"] = pd.NaT if pd.isna(s) or pd.isna(gate) else max(s, gate)
    else:
        out["model_raw"] = out["model"] = pd.NaT
    for k in ("pm", "kalshi"):
        x = d.dropna(subset=[k])
        out[k] = ea.settle(x["t"], (x[k] if a_won else 1 - x[k]) >= SETTLE) if len(x) else pd.NaT
    return out


def at_fractions(d: pd.DataFrame, final_share: float) -> list[dict]:
    """First row with counted >= q: margin error (model, raw), z, inside the 90 % band."""
    out = []
    for q in QS:
        x = d[(d["counted"] >= q) & d["sv"].notna()]
        if x.empty:
            out.append({"q": q, "t": None})
            continue
        r = x.iloc[0]
        z = (final_share - r["sv"]) / r["sdv"] if r["sdv"] and r["sdv"] > 0 else np.nan
        out.append({"q": q, "t": _iso(r["t"]), "counted": _r(r["counted"]),
                    "margin_err_pts": _r(200 * (r["sv"] - final_share), 3),
                    "raw_margin_err_pts": _r(200 * (r["raw"] - final_share), 3) if pd.notna(r["raw"]) else None,
                    "z": _r(z, 3), "in90": bool(abs(z) < Z90) if np.isfinite(z) else None})
    return out


def band_windows(d: pd.DataFrame, final_share: float) -> list[dict]:
    x = d.dropna(subset=["sv", "sdv"])
    x = x[x["sdv"] > 0]
    z = (final_share - x["sv"]) / x["sdv"]
    out = []
    for name, lo, hi in (("1-95%", 0.01, 0.95), ("1-90%", 0.01, 0.90), ("90-95%", 0.90, 0.95)):
        w = x["counted"].between(lo, hi, inclusive="right")
        out.append({"window": name, "minutes": int(w.sum()),
                    "coverage90": _r((z[w].abs() < Z90).mean(), 3) if w.any() else None,
                    "mean_z": _r(z[w].mean(), 2) if w.any() else None})
    return out


def prob_scores(d: pd.DataFrame, st: dict, y: int) -> dict:
    """Brier / log loss: (a) brunder.us.score_race's definition, every count row with P; (b) model vs Polymarket on
    the same minutes, from the 1 % gate until both settled (edge_audit.pwin_minutes); (c) at the first poll close
    (the last pre-count row before the race's first count row) for model and market."""
    def bl(p):
        p = np.asarray(p, float)
        pc = np.clip(p, EPS, 1 - EPS)
        return _r(float(np.mean((p - y) ** 2))), _r(float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))))
    out = {}
    ends = [st["model"], st["pm"]]
    end = pd.NaT if any(pd.isna(e) for e in ends) else max(ends)
    w = d[(d["counted"] >= GATE) & d["Pv"].notna() & d["pm"].notna()]
    if pd.notna(end):
        w = w[w["t"] <= end]
    if len(w):
        bm, lm = bl(w["Pv"])
        bk, lk = bl(w["pm"])
        out["gate_to_settle"] = {"minutes": int(len(w)), "brier_model": bm, "log_model": lm, "brier_market": bk,
                                 "log_market": lk, "brier_diff": _r(bm - bk, 5), "log_diff": _r(lm - lk, 5)}
    first = d.loc[d["counted"].notna(), "t"].min()
    pre = d[d["counted"].isna() & (d["t"] < first)] if pd.notna(first) else d.iloc[:0]
    if len(pre):
        r = pre.iloc[-1]
        out["at_poll_close"] = {"t": _iso(r["t"]),
                                **({"brier_model": bl([r["Pv"]])[0], "P": _r(r["Pv"])} if pd.notna(r["Pv"]) else {}),
                                **({"brier_market": bl([r["pm"]])[0], "pm": _r(r["pm"])} if pd.notna(r["pm"]) else {})}
    return out


def turnout_at_fractions(arc: us.Archive, cv: dict, pri: pd.DataFrame | None, d: pd.DataFrame, final_2p: float,
                         memo: dict) -> list[dict]:
    """Projected two-party total of THIS version (brunder.us.project on the archived feed at the checkpoint minute)
    / certified two-party total − 1; q = 0 is the expected vote before any return. Also the version's share there,
    as a check against the timeline (|diff| should be 0)."""
    if pri is None or pri.empty:
        return []
    out = []
    res, units = us.project(us._frame([]), cv, pri)
    out.append({"q": 0.0, "turnout_err": _r(float(units["n"].sum() + res["rem_total"]) / final_2p - 1)})
    kind = cv["election"]["feed"]
    for q in QS:
        x = d[(d["counted"] >= q) & d["sv"].notna()]
        if x.empty:
            continue
        r = x.iloc[0]
        rows = feed_rows(arc, kind, r["t"], memo)
        res, units = us.project(rows, cv, pri)
        out.append({"q": q, "t": _iso(r["t"]),
                    "turnout_err": _r(float(units["n"].sum() + res["rem_total"]) / final_2p - 1),
                    "share_check": _r(abs(float(res["share_hat"]) - r["sv"]), 4)})
    return out


def score_race_version(rows: list[dict], v: str, oc: dict) -> tuple[dict, pd.DataFrame, dict]:
    d = frame(rows, v)
    y, fs = oc["a_won"], oc["share_a"]
    count = d[d["counted"].notna()]
    m = [{"counted": r.counted, "margin_model": (2 * r.sv - 1) * 100 if pd.notna(r.sv) else None,
          "votes_proj": r.votes_proj if v == "v1" and pd.notna(r.votes_proj) else None,
          "P": r.Pv if pd.notna(r.Pv) else None} for r in count.itertuples()]
    base = us.score_race(m, fs, oc["two_party"])
    st = settle_times(d, y)
    other, vs = (st["pm"], "polymarket") if pd.notna(st["pm"]) else (st["kalshi"], "kalshi")
    rec = {"score_race": base, "at_fractions": at_fractions(d, fs), "band": band_windows(d, fs),
           "probability": prob_scores(d, st, y),
           "settle": {"gate": _iso(st["gate"]), "model": _iso(st["model"]), "model_ungated": _iso(st["model_raw"]),
                      "polymarket": _iso(st["pm"]), "kalshi": _iso(st["kalshi"]),
                      "lead_min": _mins(st["model"], other), "lead_vs": vs,
                      "lead_kalshi_min": _mins(st["model"], st["kalshi"])}}
    return rec, d, st


# ============================================================== catch-up slope (pooled over markets)

def catchup(frames: dict[str, pd.DataFrame], col: str) -> dict | None:
    """Slope of the market's 30-minute move on the gap P − price (hu_score.catchup / underpricing_figures rules):
    rows >= 1 % counted, both strictly inside (0.02, 0.98); cluster bootstrap over (race, 30-min block)."""
    parts = []
    for rid, d in frames.items():
        g = d.dropna(subset=[col]).drop_duplicates("t").set_index("t").sort_index()
        if g.empty:
            continue
        fut = g[col].reindex(g.index + pd.Timedelta(minutes=CATCHUP_MIN)).to_numpy()
        parts.append(pd.DataFrame({"scope": rid, "t": g.index, "gap": (g["Pv"] - g[col]).to_numpy(),
                                   "move": fut - g[col].to_numpy(), "p": g[col].to_numpy(), "P": g["Pv"].to_numpy(),
                                   "c": g["counted"].to_numpy()}))
    if not parts:
        return None
    x = pd.concat(parts, ignore_index=True)
    x = x[(x["c"] >= GATE) & x["p"].between(0.02, 0.98, inclusive="neither") & x["P"].between(0.02, 0.98, inclusive="neither")]
    x = x.dropna(subset=["move", "gap"])
    if len(x) <= 3 or x["gap"].std() == 0:
        return None
    x["cluster"] = x["scope"] + "_" + x["t"].dt.floor("30min").astype(str)
    s, lo, hi, r, k = uf.slope_ci(x, b=300)
    return {"slope30": _r(s, 3), "lo": _r(lo, 3), "hi": _r(hi, 3), "r": _r(r, 3), "markets": int(x["scope"].nunique()),
            "n": int(len(x)), "clusters": int(k)}


# ============================================================== execution on the recorded Polymarket books

def trade_rule() -> dict:
    return tomllib.loads(TRADE_RULE.read_text())["trade"]


def pm_fees(cfgs: list[dict], cache: Path = RES / "pm_fees.json") -> dict[str, float | None]:
    """Taker fee rate per configured market slug from the public Gamma API feeSchedule (0 when fees are disabled),
    cached; None for a market whose metadata could not be read (its P&L is then flagged fee_unverified)."""
    have = json.loads(cache.read_text()) if cache.exists() else {}
    for c in cfgs:
        pm = us._pm_market(c)
        if not pm or pm[0].split("/")[1] in have:
            continue
        ev, slug = pm[0].split("/")
        try:
            md = ea._gamma_meta(ev, slug)
            have[slug] = None if md is None else float(md.get("fee") or 0.0)
        except Exception:
            have[slug] = None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(have, indent=1))
    return have


class Books:
    """polymarket_books snapshots of the archive: book of one market at / after a time."""

    def __init__(self, arc: us.Archive):
        self.arc = arc
        self.files = arc._files.get("polymarket_books", [])
        self.times = pd.DatetimeIndex([t for t, _ in self.files]) if self.files else pd.DatetimeIndex([], tz="UTC")
        self._c: dict[int, dict] = {}

    def _get(self, i: int) -> dict:
        if i not in self._c:
            try:
                self._c[i] = json.loads(self.arc.read(self.files[i][1]))
            except Exception:
                self._c[i] = {}
            if len(self._c) > 256:
                self._c.pop(next(iter(self._c)))
        return self._c[i]

    def at(self, t: pd.Timestamp, key: str) -> dict | None:
        """Latest snapshot <= t holding the market."""
        i = int(self.times.searchsorted(t, side="right")) - 1
        while i >= 0:
            b = self._get(i).get(key)
            if b:
                return b
            i -= 1
        return None

    def after(self, t: pd.Timestamp, key: str) -> tuple[pd.Timestamp, dict] | None:
        """First snapshot >= t holding the market."""
        for i in range(int(self.times.searchsorted(t, side="left")), len(self.files)):
            b = self._get(i).get(key)
            if b:
                return self.times[i], b
        return None


def _unit(q: float, fee: float) -> float:
    return q + fee * q * (1 - q)


def signal_pnl(d: pd.DataFrame, c: dict, books: Books, fee: float, y_yes: int, T: dict) -> dict:
    """The frozen favoured-side rule on one market and version: first count minute with >= min_counted where the
    side the model favours has edge >= min_edge after the $100 book walk (live engine cost; night.evaluate with
    favoured_only). Executed at the first book >= signal + latency as a limit order: 'signal' = the signal's all-in
    cost + slip, 'model' = P(fav) − min_edge (edge_audit.walk_limit), per size; held to resolution."""
    key, yes_is = us._pm_market(c)
    g = d[(d["counted"] >= T["min_counted"]) & d["Pv"].notna()]
    for r in g.itertuples():
        bk = books.at(r.t, key)
        if not bk:
            continue
        p_yes = r.Pv if yes_is == "a" else 1 - r.Pv
        side_yes = p_yes >= 0.5
        p_fav = p_yes if side_yes else 1 - p_yes
        w = live.walk(bk, side_yes, ea.SIGNAL_STAKE, fee)
        if not (np.isfinite(w["avg_cost"]) and p_fav - w["avg_cost"] >= T["min_edge"]):
            continue
        won = int(y_yes == int(side_yes))
        out = {"signal_t": _iso(r.t), "counted": _r(r.counted), "side": "YES" if side_yes else "NO",
               "P_fav": _r(p_fav), "signal_cost_100": _r(w["avg_cost"]), "edge": _r(p_fav - w["avg_cost"]), "won": won,
               "fills": []}
        for L in LATENCY_S:
            nxt = books.after(r.t + pd.Timedelta(seconds=L), key)
            if nxt is None:
                continue
            bt, b2 = nxt
            limits = [("signal", s, w["avg_cost"] + s) for s in SLIPS] + [("model", None, p_fav - T["min_edge"])]
            for name, s, lim in limits:
                for size in [*ea.SIZES, None]:
                    x = ea.walk_limit(b2, side_yes, size, fee, lim)
                    out["fills"].append({"latency_s": L, "book_t": _iso(bt), "limit": name, "slip": s,
                                         "size": size if size else "max", "spent": _r(x["spent"], 2),
                                         "shares": _r(x["shares"], 2), "pnl": _r(x["shares"] * won - x["spent"], 2)})
        return out
    return {"signal_t": None, "reason": "no count minute with the favoured side >= min_edge after the $100 book walk"}


def signal_table(sigs: list[dict]) -> list[dict]:
    """Per execution variant, over the markets of one version: signals, filled, deployed, P&L, return, and the exact
    P(P&L >= observed) if each fill won with probability = its average price (edge_audit.p_market_fair)."""
    fired = [s for s in sigs if s.get("signal_t")]
    out = []
    keys = sorted({(f["latency_s"], f["limit"], f["slip"] if f["slip"] is not None else -1, str(f["size"]))
                   for s in fired for f in s["fills"]})
    for L, lim, slip, size in keys:
        fs = [next((f for f in s["fills"] if (f["latency_s"], f["limit"], f["slip"] if f["slip"] is not None else -1,
                                                str(f["size"])) == (L, lim, slip, size)), None) for s in fired]
        dep = np.array([f["spent"] if f else 0.0 for f in fs], float)
        sh = np.array([f["shares"] if f else 0.0 for f in fs], float)
        pnl = float(sum(f["pnl"] for f in fs if f))
        avg = np.divide(dep, sh, out=np.ones_like(dep), where=sh > 0)
        out.append({"latency_s": L, "limit": lim, "slip": None if slip == -1 else slip, "size": size,
                    "signals": len(fired), "filled": int((dep > 0).sum()), "deployed": _r(dep.sum(), 2), "pnl": _r(pnl, 2),
                    "return_per_usd": _r(pnl / dep.sum(), 4) if dep.sum() > 0 else None,
                    "p_market_fair": ea.p_market_fair(dep, avg, pnl)})
    return out


def releases(d: pd.DataFrame) -> pd.DataFrame:
    """Count minutes where the version's projection changed (a feed update), from the 1 % gate."""
    g = d[(d["counted"] >= GATE) & d["Pv"].notna()]
    ch = (g[["counted", "sv", "Pv"]].diff().abs().sum(axis=1) > 0) | (np.arange(len(g)) == 0)
    return g[ch.to_numpy()]


def ml_candidates(d: pd.DataFrame, c: dict, books: Books, fee: float, y_yes: int, delay: int, scope: str) -> pd.DataFrame:
    """Model limit on books (edge_audit.il_ml_candidates): at the first book >= delay after each release, every
    level of the favoured side with its margin under that release's P (one snapshot per release)."""
    key, yes_is = us._pm_market(c)
    rows = []
    for r in releases(d).itertuples():
        nxt = books.after(r.t + pd.Timedelta(seconds=delay), key)
        if nxt is None:
            continue
        bt, b = nxt
        p_yes = r.Pv if yes_is == "a" else 1 - r.Pv
        yes = p_yes >= 0.5
        levels = ([(float(x["price"]), float(x["size"])) for x in b.get("asks") or []] if yes else
                  [(1 - float(x["price"]), float(x["size"])) for x in b.get("bids") or []])
        for q, size in sorted(levels):
            cst = _unit(q, fee)
            rows.append({"t": bt, "scope": scope, "market": key, "yes": yes, "q": q, "cost": cst,
                         "margin": (p_yes if yes else 1 - p_yes) - cst, "shares": size, "won": bool(y_yes == int(yes))})
    return pd.DataFrame(rows, columns=["t", "scope", "market", "yes", "q", "cost", "margin", "shares", "won"])


def model_limit(cands: dict[int, pd.DataFrame]) -> dict:
    """Eligible volume / P&L per (edge, delay) and the $10,000 bankroll per mode (edge_audit.model_limit)."""
    grid = {}
    for e, dl in itertools.product(ea.ML_EDGES, ea.ML_DELAYS):
        c = cands.get(dl)
        el = c[c["margin"] >= e - 1e-12] if c is not None and len(c) else pd.DataFrame()
        if len(el):
            vol = el["shares"] * el["cost"]
            pnl = el["won"].astype(float) * el["shares"] - vol
            g = {"eligible_trades": int(len(el)), "eligible_volume": round(float(vol.sum()), 2),
                 "eligible_pnl": round(float(pnl.sum()), 2),
                 "eligible_return": round(float(pnl.sum()) / float(vol.sum()), 4) if vol.sum() > 0 else None,
                 "markets": int(el["market"].nunique()), "bankroll": {}}
            for name, frac, cap in ea.ML_MODES:
                g["bankroll"][name] = ea.ml_bankroll(el, frac, cap)[0]
        else:
            g = {"eligible_trades": 0, "eligible_volume": 0.0, "eligible_pnl": 0.0, "eligible_return": None, "markets": 0,
                 "bankroll": {name: {"ending_value": ea.BANKROLL, "deployed": 0.0, "fills": 0, "markets": 0,
                                     "return_pct": 0.0} for name, _, _ in ea.ML_MODES}}
        grid[f"edge{int(round(e * 100))}c_delay{dl}s"] = g
    e, dl, mode = ea.ML_HEAD
    h = grid[f"edge{int(round(e * 100))}c_delay{dl}s"]
    return {"headline": {"edge": e, "delay_s": dl, "mode": mode, **h["bankroll"][mode],
                         "eligible_volume": h["eligible_volume"], "eligible_pnl": h["eligible_pnl"]},
            "grid": grid, "rule": ea.ML_RULE.replace("(Israel: resting ask in the recorded book)",
                                                      "(US: resting level in the recorded Polymarket book)")}


# ============================================================== Senate control

def _before(hist: list, t: pd.Timestamp, i: int = 1) -> float | None:
    x = [h for h in hist or [] if pd.Timestamp(h[0]) < t and h[i] is not None]
    return float(x[-1][i]) if x else None


def senate_control(ov: dict | None, results: dict[str, dict]) -> dict:
    """Forecast at the first poll close: brunder.us.senate_control on the overview with each race's Polymarket
    price set to its last history point before 2026-11-03T23:00Z (the live refresh moves prices afterwards); the
    Polymarket control market at the same time. Outcome: Democratic caucus (dem side, incl. flagged independents,
    as in the forecast) >= 51 seats, from results of every Senate race; pending until all are in."""
    if not ov:
        return {"status": "none", "reason": "overview.json missing"}
    o = json.loads(json.dumps(ov))
    sen = o["offices"]["senate"]
    for r in sen["races"]:
        if r.get("pm"):
            p = _before(r.get("history"), FIRST_CLOSE, 1)
            r["pm"]["price"] = p if p is not None else r["pm"].get("price")
    fc = us.senate_control(o)
    mk = next((m for m in sen.get("control_markets", []) if m.get("platform") == "polymarket"), None)
    p_mk = _before(mk.get("history"), FIRST_CLOSE, 1) if mk else None
    if mk and mk.get("yes_is") not in (None, "dem"):
        p_mk = None if p_mk is None else 1 - p_mk
    keys = [r["race_key"] for r in sen["races"]]
    have = {k: results[k] for k in keys if k in results}
    out = {"forecast": {"control_p_corr": fc.get("control_p_corr"), "control_p_indep": fc.get("control_p_indep"),
                        "seats_80_corr": fc.get("seats_80_corr"), "market_p": _r(p_mk), "as_of": _iso(FIRST_CLOSE)},
           "races": len(keys), "results_in": len(have),
           "certified": sum(v["status"] == "certified" for v in have.values())}
    if len(have) < len(keys):
        out.update({"status": "pending", "missing": sorted(set(keys) - set(have))})
        return out
    hold = sen.get("holdovers") or {}
    seats = int(hold.get("D", 0) + hold.get("I", 0)) + sum(v["dem_share"] > 0.5 for v in have.values())
    y = int(seats >= int(sen.get("majority", 51)))
    out.update({"status": "certified" if out["certified"] == len(keys) else "provisional", "dem_caucus_seats": seats,
                "dem_control": y})
    for k in ("control_p_corr", "control_p_indep", "market_p"):
        p = out["forecast"][k]
        if p is not None:
            pc = min(max(p, EPS), 1 - EPS)
            out[f"brier_{k}"] = _r((p - y) ** 2)
            out[f"log_{k}"] = _r(-(y * math.log(pc) + (1 - y) * math.log(1 - pc)))
    return out


def all_results(cert: dict, oc: dict, cfgs: list[dict]) -> dict[str, dict]:
    """race key -> dem-side two-party share and status, for every race: certified rows, else the modelled races'
    provisional feed result."""
    out = {k: {"dem_share": v["dem"] / (v["dem"] + v["rep"]), "status": "certified"} for k, v in cert.items()}
    for c in cfgs:
        rid = c["election"]["race_id"]
        o = oc.get(rid, {})
        if rid not in out and o.get("status") == "provisional":
            s = 1 - o["share_a"] if c["election"]["a"]["party"] == "REP" else o["share_a"]
            out[rid] = {"dem_share": s, "status": "provisional"}
    return out


# ============================================================== display-only candidates

def crosstabs(path: Path, results: dict[str, dict]) -> dict:
    """Raw vs recalled-vote adjusted poll averages (hub/crosstabs.json races[].raw / adj, two-party margin points)
    against the outcome margin."""
    if not path.exists():
        return {"status": "none", "reason": f"{path.name} missing"}
    rows = []
    for r in json.loads(path.read_text()).get("races", []):
        o = results.get(r["race"])
        if o is None or r.get("raw") is None or r.get("adj") is None:
            continue
        m = 100 * (2 * o["dem_share"] - 1)
        rows.append({"race": r["race"], "status": o["status"], "raw_err": _r(r["raw"] - m, 2), "adj_err": _r(r["adj"] - m, 2),
                     "recall_weight": r.get("recall_weight")})
    if not rows:
        return {"status": "pending", "reason": "no race with both an average and a result"}
    d = pd.DataFrame(rows)
    return {"status": "certified" if (d["status"] == "certified").all() else "provisional", "races": int(len(d)),
            "mae_raw": _r(d["raw_err"].abs().mean(), 2), "mae_adj": _r(d["adj_err"].abs().mean(), 2),
            "mean_raw": _r(d["raw_err"].mean(), 2), "mean_adj": _r(d["adj_err"].mean(), 2),
            "adj_closer": int((d["adj_err"].abs() < d["raw_err"].abs()).sum()), "by_race": rows}


def county_finals(oc: dict, cfgs: list[dict]) -> pd.DataFrame:
    """One row per county of the modelled states: fips, st, two-party D share s, total votes; the Senate race where the
    state has one modelled, else governor (the panel's midterm target-race rule)."""
    by_state: dict[str, tuple[str, dict]] = {}
    for c in cfgs:
        rid, st, race = c["election"]["race_id"], c["election"]["state"], c["election"]["race"]
        o = oc.get(rid, {})
        if o.get("county") is None:
            continue
        if c["election"]["a"]["party"] != "DEM":           # an independent's share is not the panel's D share
            continue
        if st not in by_state or race == "sen":
            by_state[st] = (rid, o)
    parts = []
    for st, (rid, o) in by_state.items():
        x = o["county"].copy()
        x["fips"] = x["unit_id"].str[-5:]
        x["s"] = x["dem"] / (x["dem"] + x["rep"])
        parts.append(x.assign(race=rid, status=o["county_status"])[["fips", "s", "total", "race", "status"]])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["fips", "s", "total", "race", "status"])


def fundamentals(fin: pd.DataFrame) -> dict:
    """us_fundamentals' tests with 2026 as the test cycle (fit on every earlier cycle, predict 2026): county margin
    ΔRMSE vs the state-uniform swing (HYP, state mode) and county turnout ΔRMSE vs the flat ratio (TURNOUT_HYP)."""
    import us_fundamentals as F
    if fin.empty:
        return {"status": "pending", "reason": "no county results"}
    P = F.load_panel()
    m = P["year"] == 2026
    res = fin.set_index("fips")
    P.loc[m, "s"] = P.loc[m, "fips"].map(res["s"])
    P.loc[m, "votes"] = P.loc[m, "fips"].map(res["total"])
    train = [F.cycle_frame(P, t, p) for t, p in F.CYCLES]
    d = F.cycle_frame(P, 2026, 2024)
    if d.empty:
        return {"status": "pending", "reason": "no county of the modelled states in the panel with a 2026 result"}
    out = {"status": "certified" if (fin["status"] == "certified").all() else "provisional",
           "counties": int(len(d)), "states": sorted(d["st"].unique().tolist()), "swing": {}, "turnout": {}}
    wv = d["votes"].to_numpy()
    for h, feats in F.HYP.items():
        fit = F.Fit(train, feats, "swing", "st")
        if fit.n_cycles == 0 or not F._has(d, feats):
            out["swing"][h] = None
            continue
        base = F._recentre(d["s_p"], d, "st")
        pred = F._recentre(d["s_p"] + fit.adj(d), d, "st")
        eb, em = 200 * (base - d["s"]).to_numpy(), 200 * (pred - d["s"]).to_numpy()
        out["swing"][h] = {"rmse_base": _r(F._rmse(eb, wv), 3), "rmse_model": _r(F._rmse(em, wv), 3),
                           "d_rmse": _r(F._rmse(em, wv) - F._rmse(eb, wv), 3), "fit_cycles": fit.n_cycles}
    for h, feats in F.TURNOUT_HYP.items():
        fit = F.Fit(train, feats, "turnout", "st")
        if fit.n_cycles == 0 or not F._has(d, feats):
            out["turnout"][h] = None
            continue
        a = fit.adj(d)

        def err(pr: np.ndarray) -> np.ndarray:
            lv = pd.Series(np.log(d["v_p"].to_numpy()) + pr, index=d.index)
            c = np.log(d["votes"].groupby(d["st"]).transform("sum")) - np.log(np.exp(lv).groupby(d["st"]).transform("sum"))
            return (100 * (lv + c - np.log(d["votes"]))).to_numpy()
        eb, em = err(np.zeros(len(d))), err(a)
        out["turnout"][h] = {"rmse_base": _r(F._rmse(eb, wv), 3), "rmse_model": _r(F._rmse(em, wv), 3),
                             "d_rmse": _r(F._rmse(em, wv) - F._rmse(eb, wv), 3), "fit_cycles": fit.n_cycles}
    return out


def expected_vote_templates(oc: dict, allv: dict, pri_v: dict) -> dict:
    """County distribution of the expected vote against the result, per race: v3-style (prior-race two-party votes,
    flat ratio), v4 (county drop-off file) and the display turnout index (brunder.us.turnout_profile). Error =
    100 × log(predicted / actual two-party votes) after scaling each template to the actual statewide total
    (us_fundamentals.turnout_tests), vote-weighted RMSE and MAE."""
    out = {}
    for rid, vs in allv.items():
        o = oc.get(rid, {})
        if o.get("county") is None:
            continue
        act = o["county"].set_index("unit_id")
        act = (act["dem"] + act["rep"])
        tmpl = {}
        v1 = pri_v[rid].get("v1")
        if v1 is not None and not v1.empty:
            tmpl["flat_prior"] = v1.set_index("unit_id")["V"]
            tmpl["turnout_index"] = pd.Series(us.turnout_profile(vs["v1"], v1)["turnout_exp"].to_numpy(), index=v1["unit_id"])
        for v, cv in vs.items():
            pv = pri_v[rid].get(v)
            if cv.get("count", {}).get("expected_votes") and pv is not None and not pv.empty:
                ev = us._expected_votes(cv["count"]["expected_votes"])
                tmpl[f"dropoff_{v}"] = ev.reindex(pv["unit_id"]).fillna(pv.set_index("unit_id")["V"])
        rec = {}
        for name, s in tmpl.items():
            j = pd.concat([s.rename("p"), act.rename("a")], axis=1, join="inner")
            j = j[(j["p"] > 0) & (j["a"] > 0)]
            if j.empty:
                continue
            e = 100 * (np.log(j["p"] * j["a"].sum() / j["p"].sum()) - np.log(j["a"]))
            rec[name] = {"counties": int(len(j)), "rmse": _r(float(np.sqrt(np.average(e ** 2, weights=j["a"]))), 2),
                         "mae": _r(float(np.average(e.abs(), weights=j["a"])), 2)}
        if rec:
            out[rid] = {"county_status": o["county_status"], **rec}
    return {"status": "none" if not out else ("certified" if all(v["county_status"] == "certified" for v in out.values())
                                              else "provisional"), "by_race": out}


# ============================================================== run

def _pooled(per: dict, prim: dict) -> dict:
    """Per version label ('primary' = v3 / Ohio v4, and each vN): coverage at each fixed fraction pooled over races,
    mean abs margin error, settle leads."""
    out = {}
    labels = ["primary"] + sorted({v for r in per.values() for v in r["versions"]})
    for lab in labels:
        recs = [(rid, r["versions"].get(prim[rid] if lab == "primary" else lab)) for rid, r in per.items()]
        recs = [(rid, x) for rid, x in recs if x]
        if not recs:
            continue
        fr = []
        for q in QS:
            pts = [next(a for a in x["at_fractions"] if a["q"] == q) for _, x in recs]
            pts = [a for a in pts if a.get("t")]
            ins = [a["in90"] for a in pts if a.get("in90") is not None]
            fr.append({"q": q, "races": len(pts), "coverage90": _r(np.mean(ins), 3) if ins else None,
                       "mae_margin_pts": _r(np.mean([abs(a["margin_err_pts"]) for a in pts]), 3) if pts else None,
                       "mae_raw_pts": _r(np.mean([abs(a["raw_margin_err_pts"]) for a in pts
                                                  if a.get("raw_margin_err_pts") is not None]), 3)
                       if any(a.get("raw_margin_err_pts") is not None for a in pts) else None})
        leads = [x["settle"]["lead_min"] for _, x in recs if x["settle"]["lead_min"] is not None]
        out[lab] = {"races": [rid for rid, _ in recs], "at_fractions": fr,
                    "settle_lead_min": {"n": len(leads), "median": _r(np.median(leads), 1) if leads else None,
                                        "model_first": sum(1 for x in leads if x > 0)}}
    return out


def run(out: Path = ROOT / "site" / "data", snap: Path | None = None, configs: list[Path] | None = None,
        fees: dict[str, float | None] | None = None, cert_path: Path = CERT, cert_counties_path: Path = CERT_COUNTIES,
        final: bool = False, updates: Path | None = us.UPDATES) -> Path:
    """Score everything that has an outcome; write <out>/us2026/score.json and score.md. fees: taker fee rate per
    Polymarket slug (default: Gamma feeSchedule, cached in results/us_score/pm_fees.json). updates: the live model's
    update log (display block `latency`, analysis/us_latency.py)."""
    uf.rng, ea.RNG = np.random.default_rng(0), np.random.default_rng(20261028)   # same seeds every run: re-runs repeat
    out, d26 = Path(out), Path(out) / us.EID
    cfg_paths = [Path(p) for p in (configs or default_configs())]
    cfgs, allv, prim = version_cfgs(cfg_paths)
    reg = registered_ids()
    arc = us.Archive(Path(snap) if snap else us.SNAP / us.TAG)
    arc.refresh()
    memo: dict = {}
    cert, cert_cty = certified(cert_path), certified_counties(cert_counties_path)
    oc = outcomes(cfgs, arc, cert, cert_cty, memo)
    if final:
        need = [rid for rid, o in oc.items() if o["status"] != "none"]
        bad = [rid for rid in need if oc[rid]["status"] != "certified"]
        if bad or not need:
            raise SystemExit(f"--final needs certified results for every race with a count; missing: {bad or 'all'}")
    tl_path = d26 / "timeline.json"
    tl = json.loads(tl_path.read_text()) if tl_path.exists() else {}
    priors = us.load_priors()
    pri_v = {rid: {v: us.race_prior(cv, priors) for v, cv in vs.items()} for rid, vs in allv.items()}
    T = trade_rule()
    fees = fees if fees is not None else pm_fees(cfgs)
    books = Books(arc)
    per, frames, sig_by_v, ml_by_v = {}, {}, {}, {}
    for c in cfgs:
        rid = c["election"]["race_id"]
        o = oc[rid]
        rows = tl.get(rid) or []
        rec = {"name": c["election"]["name"], "primary": prim[rid], "outcome": {k: (_r(v, 6) if isinstance(v, float) else v)
                                                                               for k, v in o.items() if k != "county"},
               "registration": {v: registration(cv, reg) for v, cv in allv[rid].items()}, "versions": {}}
        per[rid] = rec
        if o["status"] == "none" or not any(r.get("counted") is not None for r in rows):
            rec["reason"] = o.get("reason") if o["status"] == "none" else "no count rows in timeline.json"
            continue
        pm = us._pm_market(c)
        slug = pm[0].split("/")[1] if pm else None
        fee = fees.get(slug) if slug else None
        y_yes = (o["a_won"] if pm and pm[1] == "a" else 1 - o["a_won"]) if pm else None
        for v in versions_of(rows, allv[rid]):
            x, dv, _ = score_race_version(rows, v, o)
            x["role"] = "primary" if v == prim[rid] else allv[rid][v].get("role", "secondary")
            x["turnout_at_fractions"] = turnout_at_fractions(arc, allv[rid][v], pri_v[rid][v], dv, o["two_party"], memo)
            if pm and len(books.files):
                f = 0.0 if fee is None else fee
                s = signal_pnl(dv, c, books, f, y_yes, T)
                s["fee_rate"], s["fee_unverified"] = f, fee is None
                x["edge_rule"] = s
                sig_by_v.setdefault(v, []).append(s)
                for dl in ea.ML_DELAYS:
                    ml_by_v.setdefault(v, {}).setdefault(dl, []).append(ml_candidates(dv, c, books, f, y_yes, dl, rid))
            rec["versions"][v] = x
            frames.setdefault(v, {})[rid] = dv
            if v == prim[rid]:
                frames.setdefault("primary", {})[rid] = dv
    pooled = _pooled({k: v for k, v in per.items() if v["versions"]}, prim)
    for lab, fr in frames.items():
        if lab in pooled:
            pooled[lab]["catchup"] = {"polymarket": catchup(fr, "pm"), "kalshi": catchup(fr, "kalshi")}
    execution = {}
    for v, sigs in sig_by_v.items():
        tab = signal_table(sigs)
        head = next((t for t in tab if t["latency_s"] == HEAD["latency_s"] and t["limit"] == HEAD["limit"]
                     and t["slip"] == HEAD["slip"] and t["size"] == "max"), None)
        cands = {dl: pd.concat([x for x in lst if len(x)], ignore_index=True) if any(len(x) for x in lst)
                 else lst[0] for dl, lst in ml_by_v.get(v, {}).items()}
        execution[v] = {"signal": {"headline": head, "table": tab}, "model_limit": model_limit(cands)}
    results = all_results(cert, oc, cfgs)
    ov_path = d26 / "overview.json"
    ov = json.loads(ov_path.read_text()) if ov_path.exists() else None
    hub = d26 / "hub"
    fin = county_finals(oc, cfgs)
    display = {"crosstabs": crosstabs(hub / "crosstabs.json", results),
               "fundamentals": _safe(lambda: fundamentals(fin)),
               "expected_vote_dropoff": _safe(lambda: expected_vote_templates(oc, allv, pri_v)),
               "latency": _safe(lambda: ul.block(tl, cfgs, arc.root.parent / (arc.root.name + "_ws"),
                                                 updates if updates and updates.exists() else None))}
    statuses = [o["status"] for o in oc.values() if o["status"] != "none"]
    obj = {"generated_utc": _iso(pd.Timestamp.now(tz="UTC")), "spec": "prereg/NOTES.md (US midterms 2026)",
           "reading": "registered look, --final" if final else "monitoring run, not the registered look",
           "outcome_status": ("none" if not statuses else "certified" if all(s == "certified" for s in statuses)
                              else "provisional"),
           "outcome_note": "certified = data_public/us2026_certified.csv (state canvass); provisional = latest "
                           "snapshot of the official election-night feed in the recorder archive (not certified)",
           "trade_rule": {k: T[k] for k in ("min_edge", "min_counted", "favoured_only")},
           "fixed_fractions": list(QS), "races": per, "pooled": pooled, "execution": execution,
           "senate_control": senate_control(ov, results), "display_only": display}
    obj = _clean(obj)
    us._write_json(d26 / "score.json", obj)
    (d26 / "score.md").write_text(markdown(obj))
    if final:
        looks = []
        for rid, vs in allv.items():
            if per[rid]["versions"]:
                for v, cv in vs.items():
                    if per[rid]["registration"][v]["registered"]:
                        looks.append(registry.open_holdout(registry.load_spec(Path(cv["_path"]))))
        RES.mkdir(parents=True, exist_ok=True)
        (RES / f"final_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%SZ}.json").write_text(
            json.dumps({"looks": looks, "score": obj}, indent=1))
    return d26 / "score.json"


def _safe(f):
    try:
        return f()
    except Exception as e:                        # a display block must never stop the registered scoring
        return {"status": "error", "reason": f"{type(e).__name__}: {e}"}


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, pd.Timestamp):
        return _iso(x)
    return x


def markdown(s: dict) -> str:
    f = lambda x, n=2: "–" if x is None else (f"{x:+.{n}f}" if isinstance(x, float) else str(x))
    b = lambda x: "–" if x is None else f"{x:.3f}"
    L = [f"# US midterms 2026: scoring ({s['reading']})", "",
         f"Generated {s['generated_utc']}. Outcomes: **{s['outcome_status']}** ({s['outcome_note']}).", "",
         "## Per race (primary version)", "",
         "| race | outcome | primary | margin err at 5 / 25 / 50 % (pts) | in 90 % band at 5 / 25 / 50 % | settle lead (min) | Brier model / market (gate→settle) | edge-rule signal |",
         "|---|---|---|---|---|---|---|---|"]
    for rid, r in s["races"].items():
        v = r["versions"].get(r["primary"])
        if not v:
            L.append(f"| {rid} | {r['outcome'].get('status')} | {r['primary']} | not scored: {r.get('reason')} | | | | |")
            continue
        af = {a["q"]: a for a in v["at_fractions"]}
        me = " / ".join(f(af.get(q, {}).get("margin_err_pts")) for q in (0.05, 0.25, 0.5))
        inb = " / ".join({True: "yes", False: "no", None: "–"}[af.get(q, {}).get("in90")] for q in (0.05, 0.25, 0.5))
        p = v["probability"].get("gate_to_settle") or {}
        e = v.get("edge_rule") or {}
        sig = f"{e['side']} at {e['signal_t']} (P {e['P_fav']:.2f}, cost {e['signal_cost_100']:.2f})" if e.get("signal_t") else "none"
        L.append(f"| {rid} | {r['outcome']['status']} {f(r['outcome'].get('margin_pts'))} | {r['primary']} | {me} | {inb} | "
                 f"{f(v['settle']['lead_min'], 0)} vs {v['settle']['lead_vs']} | {b(p.get('brier_model'))} / "
                 f"{b(p.get('brier_market'))} | {sig} |")
    L += ["", "## Pooled", ""]
    for lab, p in s["pooled"].items():
        cov = ", ".join(f"{int(a['q'] * 100)}%: {a['coverage90'] if a['coverage90'] is not None else '–'}"
                        f" ({a['races']})" for a in p["at_fractions"])
        cu = (p.get("catchup") or {}).get("polymarket") or {}
        L.append(f"- **{lab}**: 90 % coverage by counted fraction (races) {cov}; median settle lead "
                 f"{p['settle_lead_min']['median']} min ({p['settle_lead_min']['model_first']}/{p['settle_lead_min']['n']} "
                 f"model first); catch-up slope30 {cu.get('slope30')} [{cu.get('lo')}, {cu.get('hi')}] (n {cu.get('n')}).")
    L += ["", "## Execution (Polymarket recorded books)", ""]
    for v, e in s["execution"].items():
        h, m = e["signal"]["headline"] or {}, e["model_limit"]["headline"]
        L.append(f"- **{v}**: frozen rule at the signal price, 60 s, whole book: {h.get('signals', 0)} signals, "
                 f"${h.get('deployed', 0)} deployed, P&L ${h.get('pnl', 0)}; model limit (5c, 60 s, 10 %): ending value "
                 f"${m['ending_value']} on $10,000 ({m['return_pct']} %).")
    sc = s["senate_control"]
    L += ["", "## Senate control", "",
          f"Status {sc.get('status')}; forecast at first poll close: P(D control) corr {sc.get('forecast', {}).get('control_p_corr')}, "
          f"indep {sc.get('forecast', {}).get('control_p_indep')}, Polymarket {sc.get('forecast', {}).get('market_p')}; "
          f"outcome {sc.get('dem_control', '–')} ({sc.get('dem_caucus_seats', '–')} seats); Brier corr / market "
          f"{sc.get('brier_control_p_corr', '–')} / {sc.get('brier_market_p', '–')}.", "",
          "## Display-only candidates (not registered tests)", ""]
    for k, v in s["display_only"].items():
        L.append(f"- **{k}**: {v.get('status')}" + (f" — {v['reason']}" if v.get("reason") else ""))
    ct = s["display_only"]["crosstabs"]
    if ct.get("races"):
        L.append(f"  - crosstabs: {ct['races']} races, MAE raw {ct['mae_raw']} vs adjusted {ct['mae_adj']} pts; adjusted closer in {ct['adj_closer']}.")
    lt = s["display_only"].get("latency") or {}
    if lt.get("status") == "ok":
        pl = lt["pooled_P"]
        L.append(f"  - latency: {pl['n']} paired threshold crossings (P vs the market's WebSocket price, "
                 f"{lt['window_min']} min window), market − model median {pl['median_s']} s "
                 f"[{pl['p25_s']}, {pl['p75_s']}], model first {pl['model_first']}/{pl['n']}.")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("configs", nargs="*", type=Path, help="v1 configs (default configs/us2026_*_v1.toml)")
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    ap.add_argument("--snap", type=Path, help="recorder directory (default data/raw/snap/us_midterms_2026)")
    ap.add_argument("--final", action="store_true", help="the registered look (needs certified results; logs looks)")
    a = ap.parse_args(argv)
    print(run(a.out, a.snap, a.configs or None, final=a.final))


if __name__ == "__main__":
    main()
