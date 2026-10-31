"""Write a model SENSITIVITY (a second or third model line, chosen or built after the result was known) into an election's
published files without touching any v1 field.

    from sens_export import write_sensitivity, write_v2_block

timeline.json  rows of every race in `series` gain  P_sens_<key>  (+ share_sens_<key>, sd_sens_<key> for share races);
meta.json      `sensitivities` = [{key, label, config, note}] (entry for <key> replaced; the legacy single `sensitivity` = key "v2" is left alone);
stats.json     `sensitivities.<key>` = {label, config, note, headline_race, by_race, summary}  (same metrics as the v1 stats:
               90% band coverage over 1% < counted <= 95%, MAE, Brier, MAE at 1/5/10/25/50% counted, minutes to P >= 0.90 / 0.95 and
               the lead over the market on contested markets). Run `analysis/decided_stats.py <id>` afterwards (it only re-derives v1 fields).

`series[race]` is a DataFrame with columns: t (UTC Timestamp), P (P that side A wins), and for share races share, plus either
sd or the band lo / hi (the band wins for coverage; sd = (hi - lo) / (2 * 1.645) is what the timeline stores). Rows are aligned to the
timeline minutes by carrying the last value forward (state held between archived frames); before the first value they stay empty.
`finals[race]` = official final share of A for share races (None for probability-only races). Idempotent.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
Z90 = 1.6448536269514722


def _r(x, n):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)


def _iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def settle(t: pd.Series, ok: pd.Series):
    """First time from which `ok` stays true to the last row (NaT if the last row is not ok)."""
    ok = ok.fillna(False).to_numpy(bool)
    if not len(ok) or not ok[-1]:
        return pd.NaT
    bad = np.flatnonzero(~ok)
    return t.iloc[(bad[-1] + 1) if len(bad) else 0]


def _align(series: pd.DataFrame, times: pd.DatetimeIndex) -> pd.DataFrame:
    s = series.drop_duplicates("t", keep="last").set_index("t").sort_index()
    s.index = pd.DatetimeIndex(s.index)
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s.reindex(s.index.union(times)).ffill().reindex(times)


def _metrics(rows: pd.DataFrame, share: str | None, lo: str | None, hi: str | None, prob: str, final: float | None,
             outcome_a: bool | None, market: dict, gate) -> dict:
    """rows: t, counted, pm, plus the model's columns. market: {settle_90, settle_95} Timestamps (NaT/None allowed), None if no market."""
    w = rows["counted"].between(0.01, 0.95, inclusive="right") & rows[prob].notna()
    out: dict = {}
    if outcome_a is None and rows[prob].notna().any():
        last = rows[prob].dropna().iloc[-1]
        outcome_a = bool(last > 0.5) if last not in (0.5,) else None
    if share is not None and final is not None and rows[share].notna().any():
        ws = w & rows[share].notna()
        inside = (rows[lo] <= final) & (final <= rows[hi])
        sd = (rows[hi] - rows[lo]) / (2 * Z90)
        z = (final - rows[share]) / sd.where(sd > 0)
        out["coverage90_1_95"] = _r(inside[ws].mean(), 3) if ws.any() else None
        out["mean_z"] = _r(z[ws & z.notna()].mean(), 2) if (ws & z.notna()).any() else None
        out["mae_pt_1_95"] = _r(100 * (rows.loc[ws, share] - final).abs().mean(), 3) if ws.any() else None
        by = {}
        for q in (0.01, 0.05, 0.10, 0.25, 0.50):
            r = rows[(rows["counted"] >= q) & rows[share].notna()]
            by[f"{int(100 * q)}%"] = _r(100 * abs(r.iloc[0][share] - final), 3) if len(r) else None
        out["mae_pt_by_counted"] = by
    if outcome_a is not None:
        out["brier_1_95"] = _r(((rows.loc[w, prob] - float(outcome_a)) ** 2).mean(), 4) if w.any() else None
        pwin = rows[prob] if outcome_a else 1 - rows[prob]
        for thr in (0.90, 0.95):
            k = int(thr * 100)
            t = settle(rows["t"], pwin >= thr)
            g = max(t, gate) if pd.notna(t) and gate is not None and pd.notna(gate) else pd.NaT
            out[f"settle_{k}"] = _iso(t)
            out[f"settle_{k}_gated"] = _iso(g)
            out[f"settle_{k}_from_gate_min"] = _r((g - gate).total_seconds() / 60, 1) if pd.notna(g) else None
            m = market.get(f"settle_{k}") if market else None
            if m is not None and pd.notna(m):
                out[f"market_settle_{k}"] = _iso(m)
                if pd.notna(t):
                    out[f"lead_{k}_min"] = _r((m - t).total_seconds() / 60, 1)
                if pd.notna(g):
                    out[f"lead_{k}_min_gate1pct"] = _r((m - g).total_seconds() / 60, 1)
    return out


def _market(rows: pd.DataFrame, outcome_a: bool | None, published_settle_90: str | None) -> dict | None:
    if "pm" not in rows or not rows["pm"].notna().any() or outcome_a is None:
        return None
    pm = rows[rows["pm"].notna()]
    pwin = pm["pm"] if outcome_a else 1 - pm["pm"]
    out = {}
    for thr in (0.90, 0.95):
        out[f"settle_{int(thr * 100)}"] = settle(pm["t"], pwin >= thr)
    if published_settle_90:      # the v1 lead uses the published Polymarket settle time; keep the two consistent
        out["settle_90"] = pd.Timestamp(published_settle_90)
    return out


def _block(key: str, label: str, config: str, note: str, per_race: dict, rows_by_race: dict, stats: dict, headline: str,
           finals: dict, cols: dict) -> dict:
    by_race, contested_leads, leads95 = {}, {}, {}
    cls = ((stats.get("market_classification") or {}).get("by_race")) or {}
    for race, rows in rows_by_race.items():
        gate_rows = rows[rows["counted"] >= 0.01]
        gate = gate_rows["t"].iloc[0] if len(gate_rows) else None
        info = cls.get(race, {})
        outcome = info.get("outcome_a")
        pub = ((stats.get("settle") or {}).get(race) or {}).get("polymarket")
        mk = _market(rows, outcome, pub)
        c = cols[race]
        m = _metrics(rows, c.get("share"), c.get("lo"), c.get("hi"), c["prob"], finals.get(race), outcome, mk, gate)
        m["contested"] = (not info["decided_before_count"]) if "decided_before_count" in info else None
        by_race[race] = m
        if m["contested"] and m.get("lead_90_min_gate1pct") is not None:
            contested_leads[race] = m["lead_90_min_gate1pct"]
        if m["contested"] and m.get("lead_95_min_gate1pct") is not None:
            leads95[race] = m["lead_95_min_gate1pct"]
    raw_leads = [m["lead_90_min"] for m in by_race.values() if m["contested"] and m.get("lead_90_min") is not None]
    cov = [m["coverage90_1_95"] for m in by_race.values() if m.get("coverage90_1_95") is not None]
    summ = {"headline_race": headline,
            "coverage90_headline": (by_race.get(headline) or {}).get("coverage90_1_95"),
            "coverage90_mean_share_races": _r(statistics.mean(cov), 3) if cov else None, "n_share_races": len(cov),
            "mae_pt_headline": (by_race.get(headline) or {}).get("mae_pt_1_95"),
            "n_contested_with_lead": len(contested_leads),
            "median_lead_90_min_gate1pct": statistics.median(contested_leads.values()) if contested_leads else None,
            "median_lead_90_min": statistics.median(raw_leads) if raw_leads else None,
            "median_lead_95_min_gate1pct": statistics.median(leads95.values()) if leads95 else None,
            "model_first_90_gate1pct": sum(x > 0 for x in contested_leads.values()),
            "market_first_90_gate1pct": sum(x < 0 for x in contested_leads.values()),
            "lead_90_by_race_gate1pct": contested_leads}
    return {"label": label, "config": config, "note": note, "by_race": by_race, "summary": summ}


def _frame(rows_json: list[dict], need: list[str]) -> pd.DataFrame:
    d = pd.DataFrame(rows_json)
    d["t"] = pd.to_datetime(d["t"], utc=True)
    for c in need:
        if c not in d:
            d[c] = np.nan
    return d


def write_sensitivity(eid: str, key: str, label: str, config: str, note: str, series: dict[str, pd.DataFrame], finals: dict[str, float | None],
                      headline: str, extra_stats: dict | None = None, data_dir: Path = DATA) -> dict:
    d = data_dir / eid
    tl = json.loads((d / "timeline.json").read_text())
    meta = json.loads((d / "meta.json").read_text())
    stats = json.loads((d / "stats.json").read_text())
    pk, sk, dk = f"P_sens_{key}", f"share_sens_{key}", f"sd_sens_{key}"
    for rows in tl.values():
        for r in rows:
            for k in (pk, sk, dk):
                r.pop(k, None)
    rows_by_race, cols = {}, {}
    for race, s in series.items():
        rows = tl[race]
        times = pd.DatetimeIndex(pd.to_datetime([r["t"] for r in rows], utc=True))
        a = _align(s, times)
        has_share = "share" in a and a["share"].notna().any()
        if has_share and "lo" not in a:
            a["lo"], a["hi"] = a["share"] - Z90 * a["sd"], a["share"] + Z90 * a["sd"]
        for r, (_, v) in zip(rows, a.iterrows()):
            if pd.isna(v["P"]):
                continue
            r[pk] = _r(v["P"], 4)
            if has_share and pd.notna(v["share"]):
                r[sk] = _r(v["share"], 4)
                r[dk] = _r((v["hi"] - v["lo"]) / (2 * Z90), 5)
        f = _frame(rows, ["counted", "pm"])
        f["P_m"], f["share_m"], f["lo_m"], f["hi_m"] = a["P"].to_numpy(), (a["share"].to_numpy() if has_share else np.nan), \
            (a["lo"].to_numpy() if has_share else np.nan), (a["hi"].to_numpy() if has_share else np.nan)
        rows_by_race[race] = f
        cols[race] = {"prob": "P_m", **({"share": "share_m", "lo": "lo_m", "hi": "hi_m"} if has_share else {})}
    sens = [e for e in meta.get("sensitivities", []) if e.get("key") != key] + [{"key": key, "label": label, "config": config, "note": note}]
    meta["sensitivities"] = sens
    block = _block(key, label, config, note, series, rows_by_race, stats, headline, finals, cols)
    if extra_stats:
        block.update(extra_stats)
    stats.setdefault("sensitivities", {})[key] = block
    for name, obj in (("timeline", tl), ("meta", meta), ("stats", stats)):
        (d / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return block


def write_v2_block(eid: str, finals: dict[str, float | None], headline: str, races: list[str] | None = None, data_dir: Path = DATA) -> dict | None:
    """Mirror the legacy single sensitivity (timeline P_sensitivity / share_sensitivity / sd_sensitivity, meta.sensitivity) under
    stats.sensitivities.v2 with the same metrics and the same code as the new sensitivities, so the homepage can compare the three
    versions from one place. Reads and writes only stats.json."""
    d = data_dir / eid
    tl = json.loads((d / "timeline.json").read_text())
    meta = json.loads((d / "meta.json").read_text())
    stats = json.loads((d / "stats.json").read_text())
    if not meta.get("sensitivity"):
        return None
    rows_by_race, cols = {}, {}
    for race, rows in tl.items():
        if races is not None and race not in races:
            continue
        if not any(r.get("P_sensitivity") is not None for r in rows):
            continue
        f = _frame(rows, ["counted", "pm", "P_sensitivity", "share_sensitivity", "sd_sensitivity"])
        has_share = f["share_sensitivity"].notna().any()
        f["lo_m"], f["hi_m"] = f["share_sensitivity"] - Z90 * f["sd_sensitivity"], f["share_sensitivity"] + Z90 * f["sd_sensitivity"]
        rows_by_race[race] = f
        cols[race] = {"prob": "P_sensitivity", **({"share": "share_sensitivity", "lo": "lo_m", "hi": "hi_m"} if has_share else {})}
    block = _block("v2", meta["sensitivity"]["label"], meta["sensitivity"].get("config", ""), "", {}, rows_by_race, stats, headline, finals, cols)
    stats.setdefault("sensitivities", {})["v2"] = block
    (d / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")))
    return block
