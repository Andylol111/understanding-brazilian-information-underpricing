"""Score the Poland 2025 count model against Polymarket (and Kalshi): replay every archived official PKW count version
with the frozen config, then the standard metrics (settle times, calibration, early error, P(win) scores by share
counted, catch-up).

Inputs (nothing was recorded on the night; all public and archived after the fact):
  - the PKW count versions the Internet Archive kept (analysis/pl_fetch.py blobs): 28 for the runoff, 31 for round 1;
  - final results by gmina, 2020 (prior) and 2025 (electorate of each cell) from the PKW csv (pl_fetch.py final);
  - Polymarket 1-minute YES price history and Kalshi 1-minute candles (pl_fetch.py markets / kalshi);
  - outcomes: each market's own resolution, checked against the official final.
A version is usable from its epoch (the time PKW generated it); the replay never looks ahead.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/pl_score.py [configs/pl2025r2_v1.toml]
Writes results/poland/<eid>/{releases.csv, units.json, ticks.parquet, stats.json}.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import kalshi
from brunder import nowcast as nc
from brunder import poland as pl
from brunder.paths import POLYMARKET, ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import edge_audit as ea  # noqa: E402
import underpricing_figures as uf  # noqa: E402

EVIDENCE = "not-preregistered"
SETTLE = (0.9, 0.95)
CATCHUP_MIN = 30
BINS = [(0.01, 0.05), (0.05, 0.10), (0.10, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 0.95)]


def load_cfg(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def iso(x):
    return None if x is None or pd.isna(x) else pd.Timestamp(x).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------- replay
def replay(cfg: dict) -> tuple[pd.DataFrame, dict, dict]:
    """One row per count version with something counted: time, counted share (of expected votes), raw national tally,
    projected share, sd, P(A wins); the per-voivodeship frames for the map."""
    R = pl.Replay(cfg["round"])
    p = nc.Params(**cfg["model"])
    rows, frames = [], {}
    for path in pl.versions(cfg["round"]):
        st = R.state(path)
        raw, n = pl.national(st)
        if n <= 0:
            continue
        o = nc.nowcast(st, R.prior, p)
        ver = pl.blob_version(path)
        m = pl.margins(path)
        rows.append({"t": pl.version_time(ver), "ver": ver, "counted": o["counted_frac"], "raw": raw, "votes": n,
                     "share": o["share_hat"], "sd": o["share_sd"], "P": o["p_win"], "swing": o["swing_nat"],
                     "obw": m["obw"], "obw_total": m["obw_total"]})
        frames[ver] = pl.unit_projection(st, R.prior, p)
    return pd.DataFrame(rows), frames, {"prior": R.prior, "classes": R.classes}


def final_truth(cfg: dict) -> dict:
    """Final A (Trzaskowski) share of the two-way vote from the last archived version, and the PKW csv cross-check."""
    last = pl.versions(cfg["round"])[-1]
    d = pl.margins(last)
    a = d["Aw"].sum() + d["abroad"]["a"]
    n = a + d["Bw"].sum() + (d["abroad"]["n"] - d["abroad"]["a"])
    csv = pl.final_by_unit(2025, cfg["round"])
    ca, cn = float(csv["a"].sum()), float(csv["a"].sum() + csv["b"].sum())
    return {"share": float(a / n), "a": float(a), "b": float(n - a), "last_version": pl.blob_version(last),
            "csv_share": ca / cn, "csv_a": ca, "csv_b": cn - ca, "csv_note": "PKW gmina csv incl. abroad and ships"}


# ---------------------------------------------------------------------------------------------- markets
def price_series(m: dict, grid: pd.DatetimeIndex) -> pd.Series:
    """YES price per minute: Polymarket last/mid price carried forward; Kalshi bid/ask midpoint else last trade."""
    if m["platform"] == "polymarket":
        px = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet").set_index("time")["price"].sort_index()
        return px.reindex(px.index.union(grid)).ffill().reindex(grid)
    c = pd.read_parquet(kalshi.KALSHI / m["event"] / "candles" / f"{m['market']}.parquet")
    return kalshi.prob_from_candles(c, grid)


def resolution(m: dict) -> int | None:
    if m["platform"] == "polymarket":
        mk = pd.read_parquet(POLYMARKET / m["event"] / "markets.parquet").set_index("slug").loc[m["market"]]
        lp = list(mk["last_prices"])
        return int(round(lp[0])) if lp else None
    mk = pd.read_parquet(kalshi.KALSHI / m["event"] / "markets.parquet").set_index("ticker").loc[m["market"]]
    return {"yes": 1, "no": 0}.get(mk["result"])


def market_end(m: dict):
    if m["platform"] == "polymarket":
        mk = pd.read_parquet(POLYMARKET / m["event"] / "markets.parquet").set_index("slug").loc[m["market"]]
        return mk["closed_time"]
    return pd.NaT


def ticks(cfg: dict, R: pd.DataFrame, truth: dict) -> tuple[pd.DataFrame, dict]:
    """1-minute grid from poll close to the end of the price window: model P(A) of every race, share/sd/counted/raw,
    and every market's YES price and outcome. Returns long table (one row per market-minute) and race metadata."""
    grid = pd.date_range(pd.Timestamp(cfg["poll_close_utc"]), pd.Timestamp(cfg["prices"]["end_utc"]), freq="1min")
    rel = R.set_index("t").sort_index()
    step = lambda s: s.reindex(s.index.union(grid)).ffill().reindex(grid)  # noqa: E731
    share, sd = step(rel["share"]), step(rel["sd"])
    base = {"counted": step(rel["counted"]).fillna(0.0), "raw": step(rel["raw"]), "share": share, "sd": sd}
    out, meta = [], {}
    for race in cfg["race"]:
        PA = step(rel["P"]) if race["kind"] == "winner" else pd.Series(
            pl.band_p(share.to_numpy(), sd.to_numpy(), race["side"], race["lo"], race["hi"]), index=grid).where(share.notna())
        meta[race["id"]] = {"P": PA}
        for m in race["market"]:
            y = resolution(m)
            px = price_series(m, grid)
            end = market_end(m)
            if pd.notna(end):
                px[grid > pd.Timestamp(end)] = np.nan
            yes_a = m["yes_is"] == "a"
            d = pd.DataFrame({"t": grid, "race": race["id"], "market": m["market"], "platform": m["platform"],
                              "yes_is": m["yes_is"], "outcome_yes": y,
                              "P_yes": (PA if yes_a else 1 - PA).to_numpy(), "price_yes": px.to_numpy(),
                              "counted": base["counted"].to_numpy()})
            # race side A: the winning-side probability the dashboard draws
            d["P_A"] = PA.to_numpy()
            d["price_A"] = (px if yes_a else 1 - px).to_numpy()
            out.append(d)
    return pd.concat(out, ignore_index=True), {"grid": grid, "base": base, "races": meta}


# ---------------------------------------------------------------------------------------------- scores
def settle_time(ts: pd.Series, p_win: pd.Series, thr: float):
    ok = p_win.notna()
    ts, p_win = ts[ok], p_win[ok]
    return ea.settle(ts, p_win >= thr) if len(ts) else pd.NaT


def brier_by_counted(d: pd.DataFrame) -> list[dict]:
    """Mean Brier of the model's and the market's P(race side A) by share of the vote counted (market-minutes in which
    both have a value). One row per bin."""
    out = []
    for lo, hi in BINS:
        x = d[(d["counted"] >= lo) & (d["counted"] < hi) & d["P_A"].notna() & d["price_A"].notna()]
        if x.empty:
            out.append({"bin": f"{int(lo * 100)}-{int(hi * 100)}%" if hi * 100 == int(hi * 100) else f"{lo:.0%}-{hi:.0%}",
                        "minutes": 0, "brier_model": None, "brier_market": None})
            continue
        y = x["y_A"].to_numpy(float)
        out.append({"bin": f"{lo * 100:g}-{hi * 100:g}%", "minutes": int(len(x)),
                    "brier_model": round(float(((x["P_A"].to_numpy() - y) ** 2).mean()), 4),
                    "brier_market": round(float(((x["price_A"].to_numpy() - y) ** 2).mean()), 4)})
    return out


def catchup(T: pd.DataFrame, scopes: list[str], price_col: str) -> dict | None:
    """Slope of the market's 30-minute move on the model-market gap (underpricing_figures.slope_ci rules)."""
    parts = []
    for scope in scopes:
        g = T[T["scope"] == scope].set_index("t").sort_index()
        fut = g[price_col].reindex(g.index + pd.Timedelta(minutes=CATCHUP_MIN)).to_numpy()
        parts.append(pd.DataFrame({"scope": scope, "t": g.index, "gap": g["P_A"] - g[price_col],
                                   "move": fut - g[price_col].to_numpy(), "p": g[price_col], "P": g["P_A"], "c": g["counted"]}))
    d = pd.concat(parts)
    d = d[(d["c"] >= 0.01) & d["p"].between(0.02, 0.98, inclusive="neither") & d["P"].between(0.02, 0.98, inclusive="neither")]
    d = d.dropna(subset=["move", "gap"])
    if len(d) <= 3:
        return None
    d["cluster"] = d["scope"] + "_" + d["t"].dt.floor("30min").astype(str)
    s, lo, hi, _, k = uf.slope_ci(d, b=300)
    return {"slope30": round(s, 3), "lo": None if pd.isna(lo) else round(float(lo), 3),
            "hi": None if pd.isna(hi) else round(float(hi), 3), "markets": int(d["scope"].nunique()), "n": int(len(d)),
            "clusters": int(k)}


def main(cfg_path: Path) -> None:
    cfg = load_cfg(cfg_path)
    eid = cfg["id"].rsplit("_", 1)[0]
    out = ROOT / "results" / "poland" / eid
    out.mkdir(parents=True, exist_ok=True)
    R, frames, aux = replay(cfg)
    R.to_csv(out / "releases.csv", index=False)
    F = final_truth(cfg)
    T, G = ticks(cfg, R, F)
    grid = G["grid"]

    # outcome of each race side A: the resolution of its first Polymarket market with yes_is == a
    yA, scopes = {}, {}
    for race in cfg["race"]:
        pm = [m for m in race["market"] if m["platform"] == "polymarket"]
        first = next(m for m in pm if m["yes_is"] == "a")
        yA[race["id"]] = resolution(first)
        scopes[race["id"]] = first["market"]
    T["y_A"] = T["race"].map(yA).astype(float)
    T["scope"] = T["market"]
    # sanity: Polymarket resolutions against the official final two-way share (winner race only)
    win = next(r for r in cfg["race"] if r["kind"] == "winner")
    won_a = int(F["share"] > 0.5)
    check = {"final_share_a": F["share"], "csv_share_a": F["csv_share"], "a_won_official": bool(won_a),
             "a_won_polymarket": yA[win["id"]], "mismatch": yA[win["id"]] != won_a}

    rel = R.set_index("t").sort_index()
    t_first = rel.index.min()
    gate = rel.index[rel["counted"] >= 0.01].min()
    sett, race_rows, cov_by, share_err, early, calib = {}, [], {}, {}, {}, {}
    for race in cfg["race"]:
        rid = race["id"]
        g = T[(T["race"] == rid) & (T["platform"] == "polymarket") & (T["yes_is"] == "a")].reset_index(drop=True)
        if g.empty:
            continue
        y = int(yA[rid])
        pe = lambda s: s if y == 1 else 1 - s  # noqa: E731  P / price of the winning side of this race's first market
        mod_t = g["t"]
        s_model = {thr: settle_time(mod_t, pe(g["P_A"]), thr) for thr in SETTLE}
        s_market = {thr: settle_time(mod_t[g["price_A"].notna()], pe(g["price_A"])[g["price_A"].notna()], thr) for thr in SETTLE}
        k = T[(T["race"] == rid) & (T["platform"] == "kalshi") & (T["yes_is"] == "a")]
        s_kal = {thr: settle_time(k["t"], pe(k["price_A"]), thr) if len(k) else pd.NaT for thr in SETTLE}
        s_raw = pd.NaT
        if race["kind"] == "winner":
            ok = (rel["raw"] > 0.5) if y == 1 else (rel["raw"] < 0.5)
            s_raw = ea.settle(pd.Series(rel.index, index=rel.index), ok)
        p_first = g.loc[g["t"] <= max(t_first, mod_t.iloc[0]) + pd.Timedelta(minutes=0), "price_A"]
        pf = g.loc[g["t"] >= t_first, "price_A"].dropna()
        p_at_first = float(pf.iloc[0]) if len(pf) else np.nan
        gm = {thr: (max(s_model[thr], gate) if pd.notna(s_model[thr]) else pd.NaT) for thr in SETTLE}
        sett[rid] = {"gate": gate, "model": s_model, "model_gated": gm, "polymarket": s_market, "kalshi": s_kal, "raw": s_raw,
                     "outcome_a": y, "price_at_first_release": p_at_first,
                     "contested": bool(0.1 < pe(pd.Series([p_at_first])).iloc[0] < 0.9) if np.isfinite(p_at_first) else False,
                     "P_first": float(g["P_A"].dropna().iloc[0]) if g["P_A"].notna().any() else np.nan}
        if race["kind"] == "winner":
            rows = pd.DataFrame({"t": g["t"], "counted": g["counted"], "share": G["base"]["share"].to_numpy(),
                                 "sd": G["base"]["sd"].to_numpy(), "raw": G["base"]["raw"].to_numpy(), "P": g["P_A"],
                                 "price": g["price_A"]}).dropna(subset=["P"]).reset_index(drop=True)
            r = {"eid": eid, "race": rid, "rows": rows, "final": F["share"], "outcome": y,
                 "model_settle": s_model[0.9], "market_settle": s_market[0.9]}
            race_rows.append(r)
            share_err[rid] = {f"{int(x['q'] * 100)}%": {"model": x["model_pt"], "raw": x["raw_pt"]} for x in ea.share_error(r)}
            cov_by[rid] = {x["window"]: {"coverage90": x["coverage90"], "mean_z": x["mean_z"], "n": x["n"]} for x in ea.coverage(r)}
            early[rid] = {f"{int(q * 100)}%": {"model": share_err[rid][f"{int(q * 100)}%"]["model"],
                                               "raw": share_err[rid][f"{int(q * 100)}%"]["raw"]} for q in (0.01, 0.05, 0.10, 0.25)
                          if share_err[rid].get(f"{int(q * 100)}%")}
            c = cov_by[rid].get("1-95%", {})
            calib[rid] = {"coverage90_1_95": c.get("coverage90"), "mean_z": c.get("mean_z"), "n": c.get("n")}

    # P(win) scores: the winner race (edge-audit window rule) and every Polymarket market
    mins = pd.concat([ea.pwin_minutes(r) for r in race_rows], ignore_index=True) if race_rows else pd.DataFrame()
    pw = ea.pwin_scores(mins, True) if len(mins) else {}
    if pw:
        pw["reliability_model"] = ea.reliability(mins, "P")
        pw["reliability_market"] = ea.reliability(mins, "price")
    allm = []
    for race in cfg["race"]:
        g = T[(T["race"] == race["id"]) & (T["platform"] == "polymarket") & (T["yes_is"] == "a")]
        if g.empty:
            continue
        s = sett[race["id"]]
        allm.append(ea.pwin_minutes({"eid": eid, "race": race["id"], "rows": pd.DataFrame({
            "t": g["t"], "counted": g["counted"], "P": g["P_A"], "price": g["price_A"]}), "outcome": s["outcome_a"],
            "model_settle": s["model"][0.9], "market_settle": s["polymarket"][0.9]}))
    pw_all = ea.pwin_scores(pd.concat(allm, ignore_index=True), True)
    wm = T[(T["race"] == "winner") & (T["platform"] == "polymarket") & (T["yes_is"] == "a")]
    bbc = brier_by_counted(wm)
    bbc_all = brier_by_counted(T[(T["platform"] == "polymarket") & (T["yes_is"] == "a")])

    # settle summary over every Polymarket market with a settle time (first market of each race)
    rows = []
    for rid, s in sett.items():
        ps, ms = s["polymarket"][0.9], s["model"][0.9]
        rows.append({"race": rid, "lead_min": (ps - ms).total_seconds() / 60 if pd.notna(ps) and pd.notna(ms) else np.nan,
                     "lead_min_gated": (ps - s["model_gated"][0.9]).total_seconds() / 60
                     if pd.notna(ps) and pd.notna(s["model_gated"][0.9]) else np.nan, "contested": s["contested"]})
    SE = pd.DataFrame(rows)
    con = SE[SE["contested"]]
    summary = {"markets": int(len(SE)), "contested_markets": int(len(con)),
               "median_lead_min": None if con["lead_min"].isna().all() else float(con["lead_min"].median()),
               "median_lead_min_gate1pct": None if con["lead_min_gated"].isna().all() else float(con["lead_min_gated"].median()),
               "model_first": int((con["lead_min_gated"] > 0).sum()), "market_first": int((con["lead_min_gated"] < 0).sum()),
               "model_never": int(sum(pd.isna(sett[r]["model"][0.9]) for r in con["race"])),
               "market_never": int(sum(pd.isna(sett[r]["polymarket"][0.9]) for r in con["race"]))}
    cu = catchup(T[(T["platform"] == "polymarket") & (T["yes_is"] == "a")].assign(scope=lambda x: x["market"]),
                 [scopes[r] for r in scopes], "price_A")
    cu_k = catchup(T[(T["platform"] == "kalshi") & (T["yes_is"] == "a")].assign(scope=lambda x: x["market"]),
                   sorted(T[(T["platform"] == "kalshi") & (T["yes_is"] == "a")]["market"].unique()), "price_A")

    def tidy(d: dict) -> dict:
        return {f"{int(k * 100)}": iso(v) if not isinstance(v, dict) else v for k, v in d.items()}
    settle_out = {rid: {"gate": iso(s["gate"]), "model": iso(s["model"][0.9]), "model_95": iso(s["model"][0.95]),
                        "polymarket": iso(s["polymarket"][0.9]), "polymarket_95": iso(s["polymarket"][0.95]),
                        "kalshi": iso(s["kalshi"][0.9]), "kalshi_95": iso(s["kalshi"][0.95]), "raw": iso(s["raw"]),
                        "outcome_a": s["outcome_a"], "P_first": None if pd.isna(s["P_first"]) else round(s["P_first"], 4),
                        "price_at_first_release": None if pd.isna(s["price_at_first_release"]) else round(s["price_at_first_release"], 4),
                        "contested": s["contested"]} for rid, s in sett.items()}
    gated_out = {rid: {"gate": iso(s["gate"]), "model": iso(s["model_gated"][0.9]), "polymarket": iso(s["polymarket"][0.9]),
                       "kalshi": iso(s["kalshi"][0.9]), "raw": iso(s["raw"]),
                       "lead": None if pd.isna(s["polymarket"][0.9]) or pd.isna(s["model_gated"][0.9])
                       else int(round((s["polymarket"][0.9] - s["model_gated"][0.9]).total_seconds() / 60)),
                       "lead_vs": "polymarket", "market": scopes[rid]} for rid, s in sett.items()}
    # lead at 95% for the winner race
    w = sett["winner"]
    lead95 = {"model": iso(w["model"][0.95]), "polymarket": iso(w["polymarket"][0.95]), "kalshi": iso(w["kalshi"][0.95])}

    accuracy = {"evidence": EVIDENCE, "share_error_pt": share_err, "coverage90": cov_by, "pwin": pw,
                "pwin_all_markets": pw_all,
                "pwin_window": "minutes from >= 1% counted until both model and market stayed >= 0.9 on the winner",
                "outcome": "Polymarket resolution of each market (checked against the official final: "
                           f"A's two-way share {100 * F['share']:.2f}%)"}
    stats = {"id": cfg["id"], "evidence": EVIDENCE, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
             "final": F, "outcome_check": check, "first_release": iso(R["t"].min()), "last_release": iso(R["t"].max()),
             "releases": int(len(R)), "gate_1pct": iso(gate),
             "calibration": calib, "settle": settle_out, "settle_gated": gated_out, "settle_summary": summary,
             "settle_95_winner": lead95,
             "catchup": {"polymarket": cu, "kalshi": cu_k}, "early_error": early,
             "brier_by_counted_winner": bbc, "brier_by_counted_all_markets": bbc_all,
             "accuracy": ea._clean(accuracy)}
    (out / "stats.json").write_text(json.dumps(ea._clean(stats), indent=1, ensure_ascii=False, default=str))
    T.to_parquet(out / "ticks.parquet")
    uj = {str(v): {u: {k: (None if pd.isna(x) else float(x)) for k, x in r.items()} for u, r in f.iterrows()}
          for v, f in frames.items()}
    (out / "units.json").write_text(json.dumps(uj))
    pd.set_option("display.width", 220)
    print(json.dumps(ea._clean({k: stats[k] for k in ("final", "outcome_check", "gate_1pct", "settle", "settle_95_winner",
                                                       "settle_summary", "calibration", "early_error", "catchup",
                                                       "brier_by_counted_winner", "brier_by_counted_all_markets")}),
                     indent=1, default=str))
    print(json.dumps(ea._clean({"pwin": {k: v for k, v in pw.items() if not k.startswith("reliab")}, "pwin_all": pw_all}),
                     indent=1, default=str))
    print(R[["t", "counted", "raw", "share", "sd", "P"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "configs" / "pl2025r2_v1.toml")
