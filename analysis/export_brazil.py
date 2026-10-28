"""Export the Brazil elections into the multi-election dashboard contract (site/data/<eid>/ + elections.json).

    uv run python analysis/export_brazil.py            # all five Brazil elections
    uv run python analysis/export_brazil.py br2026r2   # one

Everything that was already computed comes from cached outputs (results/night/<id>/{ticks,tally}.parquet,
final.json, evaluation.csv, capacity.csv; results/history/<id>/{summary.json,ticks.parquet}). What the cache
lacks is recomputed directly from the section receipts and the frozen model functions, never by rerunning
night_replay.py / history_review.py (a rerun of a registered config would count as a look):
  - map frames: per-municipality partials (night.replay_partials_mun) and the model's per-municipality
    projection (the s_hat step of nowcast.nowcast, reproduced in `mun_projection` and checked against
    nowcast.nowcast's national share at every frame);
  - presidential state races: per-minute raw two-way share and the model's projected share / sd for each
    state (nowcast.nowcast with uf=...), which the cached ticks do not hold.
Each stage of data lives in its own BRUNDER_DATA root (~/brunder-stage/<stage>; override the parent dir with
BRUNDER_STAGE_ROOT), and brunder.paths binds BRUNDER_DATA at import, so the section work runs in one child
process per (config, stage); results are cached under results/export_brazil/ (gitignored).
"""

from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
CACHE = RES / "export_brazil"
OUT = ROOT / "site" / "data"
STAGES = Path(os.environ.get("BRUNDER_STAGE_ROOT", Path.home() / "brunder-stage"))
BRT = "America/Sao_Paulo"
MAX_FRAMES = 60
COLOR_A, COLOR_B = "#b2182b", "#2b5c9e"     # candidate a (Lula / config cand_a), candidate b

UF_NAMES = {
    "ac": "Acre", "al": "Alagoas", "ap": "Amapá", "am": "Amazonas", "ba": "Bahia", "ce": "Ceará", "df": "Distrito Federal",
    "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão", "mt": "Mato Grosso", "ms": "Mato Grosso do Sul",
    "mg": "Minas Gerais", "pa": "Pará", "pb": "Paraíba", "pr": "Paraná", "pe": "Pernambuco", "pi": "Piauí",
    "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte", "rs": "Rio Grande do Sul", "ro": "Rondônia", "rr": "Roraima",
    "sc": "Santa Catarina", "sp": "São Paulo", "se": "Sergipe", "to": "Tocantins", "zz": "Abroad",
}
GOV_UFS = ("ac", "am", "df", "es", "rn", "to")

SRC_BWEB = {"name": "TSE dados abertos: Boletim de Urna na Web (section results with receipt time)",
            "url": "https://dadosabertos.tse.jus.br/", "licence": "Brazilian public open data (Lei 12.527/2011), reuse with attribution"}
SRC_MUN = {"name": "TSE dados abertos: votacao_candidato_munzona (certified results by municipality)",
           "url": "https://dadosabertos.tse.jus.br/", "licence": "Brazilian public open data (Lei 12.527/2011), reuse with attribution"}
SRC_LIVE = {"name": "TSE results feed (resultados.tse.jus.br), archived first-round municipal totals",
            "url": "https://resultados.tse.jus.br/", "licence": "Brazilian public information (Lei 12.527/2011), reuse with attribution"}
SRC_PM = {"name": "Polymarket public price history (CLOB prices-history API)", "url": "https://polymarket.com/",
          "licence": "public API data; prices shown for research commentary"}

# eid -> definition. "parts": (config, stage, race_id, region) — one model run each.
ELECTIONS = {
    "br2026r1": {"name": "Brazil presidential election, first round", "date": "2026-10-04", "evidence": "in-sample",
                 "parts": [("configs/brazil_2026_r1_dev_mun.toml", "r1_dev", "br", None)],
                 "cid": "brazil_2026_r1_dev_mun", "kind": "night", "names": ("Lula", "Flávio Bolsonaro"),
                 "prior_src": SRC_MUN, "prior_note": "Prior: 2022 first round by municipality, Lula vs Bolsonaro."},
    "br2026r2": {"name": "Brazil presidential runoff", "date": "2026-10-25", "evidence": "pre-specified",
                 "parts": [("configs/night_r2_v1.toml", "night_r2_v1", "br", None)],
                 "cid": "night_r2_v1", "kind": "night", "names": ("Lula", "Flávio Bolsonaro"),
                 "prior_src": SRC_LIVE, "prior_note": "Prior: 2026 first round by municipality, same two candidates."},
    "br2026r2gov": {"name": "Brazil governor runoffs (6 states)", "date": "2026-10-25", "evidence": "pre-specified",
                    "parts": [(f"configs/governor_r2/night_gov_r2_{u}_v1.toml", f"night_gov_r2_{u}_v1", f"gov-{u}", u)
                              for u in GOV_UFS],
                    "kind": "gov", "prior_src": SRC_MUN,
                    "prior_note": "Prior per state: 2026 governor first round by municipality, same two candidates."},
    "br2022r1": {"name": "Brazil presidential election, first round", "date": "2022-10-02", "evidence": "model-only",
                 "parts": [("configs/history/hist_2022_r1_v1.toml", "hist2022", "br", None)],
                 "cid": "hist_2022_r1_v1", "kind": "hist", "names": ("Lula", "Jair Bolsonaro"), "prior_src": SRC_MUN,
                 "prior_note": "Prior: 2018 first round by municipality, Haddad (13) vs Bolsonaro (17)."},
    "br2022r2": {"name": "Brazil presidential runoff", "date": "2022-10-30", "evidence": "model-only",
                 "parts": [("configs/history/hist_2022_r2_v1.toml", "hist2022", "br", None)],
                 "cid": "hist_2022_r2_v1", "kind": "hist", "names": ("Lula", "Jair Bolsonaro"), "prior_src": SRC_MUN,
                 "prior_note": "Prior: 2022 first round by municipality, same two candidates."},
}
NEWLY_COMPUTED: dict[str, list[str]] = {}


def _r(x, n=4):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) or pd.isna(x) else round(float(x), n)


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _new(eid: str, what: str) -> None:
    NEWLY_COMPUTED.setdefault(eid, []).append(what)


# ------------------------------------------------------------------ child: section receipts + model

def mun_projection(state: pd.DataFrame, prior: pd.DataFrame, p) -> pd.DataFrame:
    """Per-municipality projected final A share: counted votes plus the remaining votes at nowcast.nowcast's
    s_hat (same steps as nowcast.nowcast lines computing R, delta_u and s_hat; uf=None)."""
    df = prior.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0
    expected = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / expected if expected > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k
    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    c = df[counted]
    pool = c[~c["uf"].isin(p.unpooled)]
    delta = float(np.average(pool["d"], weights=pool["n"])) if len(pool) else 0.0
    W = c.groupby("uf")["n"].sum().reindex(df["uf"].unique()).fillna(0.0)
    wd = (c["n"] * c["d"]).groupby(c["uf"]).sum().reindex(W.index).fillna(0.0)
    delta_u = ((wd + p.kappa * delta) / (W + p.kappa)).rename("delta_u")
    df = df.join(delta_u, on="uf")
    resid = (df["d"] - df["delta_u"]).fillna(0.0)
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted).clip(0, 1)
    df["proj"] = (df["a"] + df["R"] * df["s_hat"]) / (df["n"] + df["R"])
    return df


def child(cfg_path: str, t0: str, t1: str, frame_step: str, state_scopes: str, out: str) -> None:
    from brunder import bweb, night, nowcast

    cfg = night.load_config(ROOT / cfg_path)
    E, R = cfg["election"], cfg["replay"]
    pri, p = night.prior_mun(cfg), night.params_mun(cfg)
    raw = bweb.sections(R["year"], R["round"], E["cand_a"], E.get("cand_b"), E.get("cargo_name", "PRESIDENTE"), E.get("uf"))
    raw["visible"] = (raw["received"] + pd.Timedelta("3h")).dt.tz_localize("UTC")
    raw["n"] = raw["a"] + raw["b"]
    totals = raw.groupby(["uf", "cd_mun"]).size()
    res = {"prior": pri, "frames": [], "series": {}, "final_uf": {}, "max_check": 0.0,
           "unmatched_sections": int((~totals.index.isin(pd.MultiIndex.from_frame(pri[["uf", "cd_mun"]]))).sum())}
    g = raw.groupby("uf")[["a", "n"]].sum()
    res["final_uf"] = (g["a"] / g["n"]).to_dict()
    for t in pd.date_range(t0, t1, freq=frame_step):
        part = night.replay_partials_mun(raw, t, totals)
        df = mun_projection(part, pri, p)
        nat = nowcast.nowcast(part, pri, p)
        share = float((df["a"] + df["R"] * df["s_hat"]).sum() / (df["n"] + df["R"]).sum())
        res["max_check"] = max(res["max_check"], abs(share - nat["share_hat"]))
        res["frames"].append((t, df[["uf", "cd_mun", "f", "n", "a", "proj"]].reset_index(drop=True)))
    scopes = [s for s in state_scopes.split(",") if s]
    if scopes:
        rows = []
        for t in pd.date_range(t0, t1, freq="1min"):
            part = night.replay_partials_mun(raw, t, totals)
            by = part.groupby("uf")[["a", "n"]].sum()
            for uf in scopes:
                r = nowcast.nowcast(part, pri, p, uf=uf)
                rawsh = by.at[uf, "a"] / by.at[uf, "n"] if uf in by.index and by.at[uf, "n"] > 0 else np.nan
                rows.append({"t": t, "scope": uf, "share": r["share_hat"], "sd": r["share_sd"], "Pc": r["p_win"], "raw": rawsh})
        res["series"] = pd.DataFrame(rows)
    Path(out).write_bytes(pickle.dumps(res))


def run_child(eid: str, cfg_path: str, stage: str, t0, t1, step: str, scopes: list[str]) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{eid}_{Path(cfg_path).stem}_{t0:%Y%m%dT%H%M}_{t1:%Y%m%dT%H%M}_{step}_{len(scopes)}.pkl"
    out = CACHE / key
    if not out.exists():
        env = {**os.environ, "BRUNDER_DATA": str(STAGES / stage)}
        subprocess.run([sys.executable, __file__, "_child", cfg_path, t0.isoformat(), t1.isoformat(), step, ",".join(scopes),
                        str(out)], env=env, check=True, cwd=ROOT)
    return pickle.loads(out.read_bytes())


# ------------------------------------------------------------------ cached outputs

def cfg_toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text())


def settle(ts: pd.Series, ok: pd.Series):
    """First time after which `ok` holds to the end (history_review.settle)."""
    bad = ts[~ok.values]
    if bad.empty:
        return ts.iloc[0]
    after = ts[ts > bad.iloc[-1]]
    return after.iloc[0] if len(after) else pd.NaT


def night_ticks(cid: str) -> pd.DataFrame:
    t = pd.read_parquet(RES / "night" / cid / "ticks.parquet")
    t["t"] = pd.to_datetime(t["t"], utc=True)
    return t.drop_duplicates(["t", "scope"]).sort_values("t")


def raw_tally(cid: str) -> pd.Series:
    tally = pd.read_parquet(RES / "night" / cid / "tally.parquet")
    tally["t"] = pd.to_datetime(tally["t"], utc=True)
    return tally.set_index("t")["raw_share"].resample("1min", label="right", closed="right").last().ffill()


def window(eid: str, counted: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    """17:00 Brasília (polls close) to 15 min after 99.9% counted; frame step 5 min (wider if > MAX_FRAMES)."""
    t0 = pd.Timestamp(f"{ELECTIONS[eid]['date']} 17:00", tz=BRT).tz_convert("UTC")
    done = counted[counted >= 0.999]
    t1 = (done.index[0] if len(done) else counted.index[-1]) + pd.Timedelta("15min")
    step = 5 * math.ceil((t1 - t0) / pd.Timedelta("5min") / MAX_FRAMES)
    t1 = t1.ceil(f"{step}min")
    return t0, t1, f"{step}min"


def market_question(stage: str, event: str, slug: str) -> str:
    """Question as listed (markets.parquet); stages without it get the slug spelled out."""
    f = STAGES / stage / "raw" / "polymarket" / event / "markets.parquet"
    q = pd.read_parquet(f).query("slug == @slug")["question"] if f.exists() else pd.Series(dtype=str)
    return str(q.iloc[0]) if len(q) else slug.replace("-", " ").capitalize() + "?"


def kalshi_markets(eid: str) -> dict[str, list[dict]]:
    """Kalshi markets per race from the Kalshi workstream's pairs file (configs/pairs/<eid>.toml), keeping only
    contracts whose YES is one of the race's two sides (other candidates' contracts are not this race)."""
    p = ROOT / "configs" / "pairs" / f"{eid}.toml"
    if not p.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for pr in tomllib.loads(p.read_text()).get("pair", []):
        side = pr.get("kalshi_yes_is")
        mk = {"platform": "kalshi", "id": pr["kalshi_ticker"], "question": pr.get("kalshi_title", pr["kalshi_ticker"]),
              "yes_is": side}
        if side in ("a", "b") and mk not in out.setdefault(pr["race_id"], []):
            out[pr["race_id"]].append(mk)
    return out


def mun_names() -> pd.Series:
    out = []
    for f in [STAGES / "hist2022" / "raw" / "tse_open" / "presidente_by_municipality_2022.parquet",
              STAGES / "night_gov_r2_ac_v1" / "raw" / "tse_open" / "governador_by_municipality_2026.parquet"]:
        if f.exists():
            out.append(pd.read_parquet(f, columns=["uf", "cd_mun", "mun_name"]).drop_duplicates(["uf", "cd_mun"]))
    d = pd.concat(out).drop_duplicates(["uf", "cd_mun"])
    return d.set_index(["uf", "cd_mun"])["mun_name"].str.title()


def gov_names(uf: str, a: str, b: str) -> tuple[str, str]:
    g = pd.read_parquet(STAGES / f"night_gov_r2_{uf}_v1" / "raw" / "tse_open" / "governador_by_municipality_2026.parquet",
                        columns=["uf", "cand_n", "cand"])
    g = g[g["uf"] == uf].drop_duplicates("cand_n").set_index("cand_n")["cand"].str.title()
    return g.get(a, a), g.get(b, b)


# ------------------------------------------------------------------ standard stats from a race's minute rows

def race_stats(rows: pd.DataFrame, final: float) -> dict:
    """calibration (90% interval coverage, mean z over 1–95% counted) and early error at 1/5/10/25% counted."""
    z = (final - rows["share"]) / rows["sd"]
    w = rows["counted"].between(0.01, 0.95, inclusive="right") & z.notna() & np.isfinite(z)
    cal = {"coverage90_1_95": _r((z[w].abs() < 1.645).mean(), 3) if w.any() else None,
           "mean_z": _r(z[w].mean(), 2) if w.any() else None}
    early = {}
    for q in (0.01, 0.05, 0.10, 0.25):
        r = rows[rows["counted"] >= q]
        if r.empty:
            early[f"{int(q * 100)}%"] = {"model": None, "raw": None}
            continue
        r = r.iloc[0]
        early[f"{int(q * 100)}%"] = {"model": _r(100 * (r["share"] - final), 2), "raw": _r(100 * (r["raw"] - final), 2)}
    return {"calibration": cal, "early_error": early}


def raw_settle(rows: pd.DataFrame, final: float):
    started = rows["counted"] > 0
    ok = (rows["raw"] > 0.5) if final > 0.5 else (rows["raw"] < 0.5)
    r = rows[started]
    return settle(r["t"], ok[started].fillna(False)) if len(r) else pd.NaT


def capacity(cids: list[str]) -> tuple[dict | None, str | None]:
    have = [RES / "night" / c / "capacity.csv" for c in cids if (RES / "night" / c / "capacity.csv").exists()]
    if not have:
        return None, "no gated signal fired (no capacity.csv)"
    d = pd.concat(pd.read_csv(p) for p in have)
    slip = "fill_30m_slip2c" in d
    return {"signal": _r(d["fill_30m"].sum(), 0), "slip2c": _r(d["fill_30m_slip2c"].sum(), 0) if slip else None,
            "slip5c": _r(d["fill_30m_slip5c"].sum(), 0) if slip else None}, \
        (None if slip else "slippage fills not computed for this config's capacity run")


def catchup(cids: list[str]) -> dict:
    sys.path.insert(0, str(ROOT / "analysis"))
    import underpricing_figures as U
    d = U.pool(cids, 30)
    s, lo, hi, _, _ = U.slope_ci(d, b=300)
    return {"slope30": _r(s, 3), "lo": _r(lo, 3), "hi": _r(hi, 3), "markets": int(d["scope"].nunique()), "n": int(len(d))}


# ------------------------------------------------------------------ election export

def rows_json(rows: pd.DataFrame) -> list[dict]:
    return [{"t": _iso(r.t), "counted": _r(r.counted), "raw": _r(r.raw), "share": _r(r.share), "sd": _r(r.sd, 5),
             "P": _r(r.P), "pm": _r(r.pm), "kalshi": None} for r in rows.itertuples()]


def units_json(results: list[dict], names: pd.Series) -> dict:
    pri = pd.concat([r["prior"] for r in results], ignore_index=True)
    ids = [f"br-{u}-{c}" for u, c in zip(pri["uf"], pri["cd_mun"])]
    key = pd.MultiIndex.from_frame(pri[["uf", "cd_mun"]])
    nm = names.reindex(key).fillna("").tolist()
    nm = [n or ("Abroad " + c if u == "zz" else c) for n, u, c in zip(nm, pri["uf"], pri["cd_mun"])]
    frames = []
    for i in range(len(results[0]["frames"])):
        t = results[0]["frames"][i][0]
        df = pd.concat([r["frames"][i][1] for r in results], ignore_index=True)
        assert (df["cd_mun"].values == pri["cd_mun"].values).all() and all(r["frames"][i][0] == t for r in results)
        a = np.where(df["n"] > 0, np.round(1000 * df["a"] / df["n"].where(df["n"] > 0)), -1)
        frames.append({"t": _iso(t), "f": np.round(1000 * df["f"].clip(0, 1)).astype(int).tolist(),
                       "a": np.nan_to_num(a, nan=-1).astype(int).tolist(),
                       "proj": np.round(1000 * df["proj"]).astype(int).tolist()})
    return {"unit_ids": ids, "region": pri["uf"].tolist(), "names": nm,
            "prior": np.round(1000 * pri["s0"]).astype(int).tolist(), "V0": np.round(pri["V0"]).astype(int).tolist(),
            "frames": frames}


def export(eid: str, names: pd.Series) -> dict:
    E = ELECTIONS[eid]
    ev, kind = E["evidence"], E["kind"]
    timeline, races, results = {}, [], []
    stats = {"calibration": {}, "settle": {}, "catchup": {}, "early_error": {}, "capacity": {}, "arbitrage": None,
             "evidence": {}, "reasons": {"arbitrage": "Kalshi workstream adds cross-platform pairs",
                                              "settle.kalshi": "Kalshi workstream adds Kalshi prices"}}
    kal = kalshi_markets(eid)
    notes = ["counted = model's share of the expected two-way votes already counted (timeline); map f = share of the "
             "municipality's polling sections received.",
             "Count path rebuilt from each section's TSE receipt time (DT_BU_RECEBIDO + 3 h = UTC), not from the "
             "results site's publication times.",
             "stats.settle = first time after which P (model) or the price (market) stayed >= 0.9 on the eventual "
             "winner to the end (the registered definition, night.evaluate); it can fall before 1% is counted when "
             "the prior alone already gives P >= 0.9.",
             E["prior_note"]]
    cids = []
    for cfg_path, stage, race_id, region in E["parts"]:
        cfg = cfg_toml(cfg_path)
        a, b = cfg["election"]["cand_a"], cfg["election"]["cand_b"]
        if kind == "gov":
            cid = cfg["id"]
            na, nb = gov_names(region, a, b)
        else:
            cid = E["cid"]
            na, nb = E["names"]
        cids.append(cid)
        if kind == "hist":
            h = pd.read_parquet(RES / "history" / cid / "ticks.parquet")
            h["t"] = pd.to_datetime(h["t"], utc=True)
            nat = h.rename(columns={"tally": "raw"}).assign(pm=np.nan)
        else:
            tk = night_ticks(cid)
            nat = tk[tk.scope == "br"].rename(columns={"share_sd": "sd", "price": "pm"})
            nat = nat.merge(raw_tally(cid).rename("raw"), left_on="t", right_index=True, how="left")
        t0, t1, step = window(eid, nat.set_index("t")["counted"])
        if kind == "gov":                # one frame grid for all six states: the latest state's window
            t1 = max(window(eid, (lambda x: x[x.scope == "br"].set_index("t")["counted"])(night_ticks(cfg_toml(c)["id"])))[1]
                     for c, *_ in E["parts"])
        nat = nat[(nat.t >= t0) & (nat.t <= t1)][["t", "counted", "raw", "share", "sd", "P", "pm"]].copy()
        nat[["counted", "raw", "share", "sd", "P", "pm"]] = nat[["counted", "raw", "share", "sd", "P", "pm"]].ffill()
        state_scopes = sorted(cfg.get("markets", {}).get("states", {})) if kind == "night" else []
        res = run_child(eid, cfg_path, stage, t0, t1, step, state_scopes)
        results.append(res)
        print(f"  {eid}/{race_id}: frames {len(res['frames'])} ({step}), projection check max |Δshare| "
              f"{res['max_check']:.2e}, section munis not in prior {res['unmatched_sections']}")
        assert res["max_check"] < 1e-9
        _new(eid, "units.json map frames (per-municipality partials + projection)")

        def race(rid, reg, name, mk):
            markets = [{"platform": "polymarket", "id": mk["market"], "question": market_question(stage, mk["event"], mk["market"]),
                        "yes_is": "a"}] if mk else []
            races.append({"race_id": rid, "name": name, "region": reg,
                          "a": {"id": a, "name": na, "color": COLOR_A}, "b": {"id": b, "name": nb, "color": COLOR_B},
                          "markets": markets + kal.get(rid, [])})

        office = "Governor" if kind == "gov" else "President"
        mk = cfg.get("markets", {}).get("national")
        race(race_id, region, f"{office}, {UF_NAMES[region]}" if region else office, mk)
        timeline[race_id] = rows_json(nat)
        # standard per-race stats
        if kind == "hist":
            s = json.loads((RES / "history" / cid / "summary.json").read_text())
            day = E["date"]
            hm = lambda x: _iso(pd.Timestamp(f"{day} {x}", tz=BRT)) if x else None
            stats["calibration"][race_id] = {"coverage90_1_95": s["coverage_90"]["1-95%"], "mean_z": s["mean_z_1_95"]}
            stats["early_error"][race_id] = {k: {"model": v["model_pt"], "raw": v["raw_tally_pt"]} for k, v in s["error_at_counted"].items()}
            stats["settle"][race_id] = {"model": hm(s["model_settles"]), "polymarket": None, "kalshi": None, "raw": hm(s["raw_tally_settles"])}
            continue
        final = json.loads((RES / "night" / cid / "final.json").read_text())["final_share"]
        full = tk[tk.scope == "br"].rename(columns={"share_sd": "sd"}).merge(raw_tally(cid).rename("raw"), left_on="t",
                                                                               right_index=True, how="left")
        full["raw"] = full["raw"].ffill()
        st = race_stats(full, final)
        stats["calibration"][race_id], stats["early_error"][race_id] = st["calibration"], st["early_error"]
        evl = pd.read_csv(RES / "night" / cid / "evaluation.csv")
        x = evl[evl.scope == "br"].iloc[0]
        stats["settle"][race_id] = {"model": _iso(pd.Timestamp(x["model_0.9"])) if pd.notna(x["model_0.9"]) else None,
                                    "polymarket": _iso(pd.Timestamp(x["market_0.9"])) if pd.notna(x["market_0.9"]) else None,
                                    "kalshi": None, "raw": _iso(raw_settle(full, final))}
        _new(eid, f"{race_id}: calibration over 1-95% counted, early_error, raw-tally settle time (from cached ticks/tally)")
        # presidential state races
        ser = res["series"]
        for uf in state_scopes:
            smk = cfg["markets"]["states"][uf]
            race(uf, uf, f"President, first place in {UF_NAMES[uf]}", smk)
            g = tk[tk.scope == uf][["t", "price", "P", "scope_counted"]].rename(columns={"price": "pm", "scope_counted": "counted"})
            g = g.merge(ser[ser.scope == uf][["t", "share", "sd", "raw", "Pc"]], on="t", how="left")
            g = g[(g.t >= t0) & (g.t <= t1)]
            g[["share", "sd", "raw"]] = g[["share", "sd", "raw"]].ffill()
            chk = (g["P"] - g["Pc"]).abs().max()
            if chk > 1e-9:
                print(f"  WARNING {eid}/{uf}: recomputed state P differs from cached ticks by {chk:.2e}")
            timeline[uf] = rows_json(g)
            fin = res["final_uf"][uf]
            st = race_stats(g, fin)
            stats["calibration"][uf], stats["early_error"][uf] = st["calibration"], st["early_error"]
            x = evl[evl.scope == uf].iloc[0]
            stats["settle"][uf] = {"model": _iso(pd.Timestamp(x["model_0.9"])) if pd.notna(x["model_0.9"]) else None,
                                   "polymarket": _iso(pd.Timestamp(x["market_0.9"])) if pd.notna(x["market_0.9"]) else None,
                                   "kalshi": None, "raw": _iso(raw_settle(g, fin))}
        if state_scopes:
            _new(eid, "state races: per-minute raw/projected share/sd, calibration, early_error, raw settle (window only)")
    # election-level stats
    if kind == "hist":
        stats["catchup"] = {"polymarket": None, "kalshi": None}
        stats["capacity"] = {"polymarket": None, "kalshi": None}
        stats["reasons"].update({"catchup": "no market prices survive for this election",
                                      "capacity": "no market prices survive for this election",
                                      "settle.polymarket": "no market prices survive for this election"})
        stats["evidence"] = {"calibration": ev, "settle": ev, "early_error": ev}
    else:
        stats["catchup"] = {"polymarket": catchup(cids), "kalshi": None}
        _new(eid, "catchup slope30 for this election's markets alone")
        cap, why = capacity(cids)
        stats["capacity"] = {"polymarket": cap, "kalshi": None}
        if why:
            stats["reasons"]["capacity.polymarket"] = why
        stats["evidence"] = {k: ev for k in ("calibration", "settle", "catchup", "early_error", "capacity")}
        stats["evidence"]["arbitrage"] = ev
        notes.append("pm = Polymarket last traded price of the YES (= candidate a) side, carried forward minute by minute.")
    sources = [SRC_BWEB, E["prior_src"]] + ([SRC_PM] if kind != "hist" else [])
    meta = {"id": eid, "name": E["name"], "date": E["date"], "tz": BRT,
            "poll_close_utc": _iso(pd.Timestamp(f"{E['date']} 17:00", tz=BRT)),
            "geometry": "geo/br_municipalities.topo.json", "geometry_object": "municipalities", "unit_key": "unit_id",
            "regions": [{"id": u, "name": UF_NAMES[u]} for u in sorted({r for x in results for r in x["prior"]["uf"]})],
            "races": races, "sources": sources, "notes": notes}
    # map frames: one national race, or (governors) each unit belongs to its own state's race
    units = {"race_id": None if kind == "gov" else "br", **units_json(results, names)}
    d = OUT / eid
    d.mkdir(parents=True, exist_ok=True)
    for name, obj in (("meta", meta), ("timeline", timeline), ("units", units), ("stats", stats)):
        (d / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return {"id": eid, "name": E["name"], "date": E["date"], "country": "BR", "status": "replay",
            "unit_level": "municipality", "evidence": ev, "summary": summary(eid, races, stats, timeline)}


def _brt(x) -> str:
    return pd.Timestamp(x).tz_convert(BRT).strftime("%H:%M")


def _gate_read(race: dict, settle: dict, rows: list[dict]) -> dict:
    """Gate-based reading of one race: time of 1% counted, the model's settle counted from the gate
    (max(model settle, gate): before the gate the model may not trade), the winner, and the last stretch in
    which the raw running tally showed the loser ahead before it settled on the winner."""
    d = pd.DataFrame(rows)
    d["t"] = pd.to_datetime(d["t"], utc=True)
    win_a = d["share"].iloc[-1] > 0.5
    winner, loser = (race["a"], race["b"]) if win_a else (race["b"], race["a"])
    gate = d.loc[d["counted"] >= 0.01, "t"].iloc[0]
    model = max(pd.Timestamp(settle["model"]), gate) if settle["model"] else None
    c = d[(d["counted"] > 0) & d["raw"].notna()]
    wrong = (c["raw"] < 0.5) if win_a else (c["raw"] > 0.5)
    raw_settle = pd.Timestamp(settle["raw"]) if settle["raw"] else None
    run = None
    if raw_settle is not None:
        w = c.loc[wrong.values & (c["t"] < raw_settle).values, "t"]
        if len(w):
            end = w.iloc[-1]
            right_before = c.loc[(~wrong).values & (c["t"] < end).values, "t"]
            start = c.loc[c["t"] > right_before.iloc[-1], "t"].iloc[0] if len(right_before) else c["t"].iloc[0]
            run = (start, end)
    pm = pd.Timestamp(settle["polymarket"]) if settle.get("polymarket") else None
    return {"gate": gate, "model": model, "pm": pm, "raw": raw_settle, "run": run,
            "winner": winner["name"], "loser": loser["name"]}


def _mins(a, b) -> int:
    return round((b - a).total_seconds() / 60)


def _raw_clause(g: dict) -> str:
    if g["run"] is None:
        return f"the raw tally had {g['winner']} ahead throughout"
    s, e = g["run"]
    return f"the raw tally showed {g['loser']} ahead from {_brt(s)} to {_brt(e)}"


def summary(eid: str, races: list[dict], stats: dict, timeline: dict) -> str:
    """One plain sentence, comparisons from the 1% trade gate (computed, not hard-coded)."""
    kind = ELECTIONS[eid]["kind"]
    what = "first place" if eid.endswith("r1") else "win"
    if kind == "gov":
        gs = [_gate_read(r, stats["settle"][r["race_id"]], timeline[r["race_id"]]) for r in races]
        leads = [_mins(g["model"], g["pm"]) for g in gs if g["model"] is not None and g["pm"] is not None]
        early = sum(g["pm"] is not None and g["pm"] <= g["gate"] for g in gs)
        tail = (f"; in {early} of {len(gs)} races Polymarket was already 90% sure before 1% was counted" if early else "")
        med = float(np.median(leads))
        rel = f"{abs(med):.0f} minutes {'before' if med >= 0 else 'after'} Polymarket" if round(med) else "at the same time as Polymarket"
        return (f"Counting from the moment each race reached 1% counted, the model was 90% sure of the winner a median "
                f"{rel} (lead range {min(leads):+d} to {max(leads):+d} minutes across {len(leads)} governor runoffs){tail}.")
    g = _gate_read(races[0], stats["settle"][races[0]["race_id"]], timeline[races[0]["race_id"]])
    sure = (f"was already 90% sure of {g['winner']}'s {what}" if g["model"] == g["gate"]
            else f"became 90% sure of {g['winner']}'s {what} at {_brt(g['model'])}")
    head = f"From 1% counted ({_brt(g['gate'])}), the model {sure}"
    if kind == "hist":
        lead = _mins(g["model"], g["raw"])
        rel = f"{abs(lead)} minutes {'before' if lead >= 0 else 'after'} the raw tally settled on {g['winner']} ({_brt(g['raw'])})"
        return f"{head}, {rel}; {_raw_clause(g)}. Model only: no market prices survive."
    lead = _mins(g["model"], g["pm"])
    rel = f"about {abs(lead)} minutes {'before' if lead >= 0 else 'after'} Polymarket ({_brt(g['pm'])})"
    return f"{head}, {rel}; {_raw_clause(g)}."


# ------------------------------------------------------------------ validation

def validate(eids: list[str]) -> None:
    print("\nvalidation")
    geo = OUT / "geo" / "br_municipalities.topo.json"
    geo_ids = None
    if geo.exists():
        g = json.loads(geo.read_text())
        geo_ids = {o["properties"]["unit_id"] for o in g["objects"]["municipalities"]["geometries"]}
    nights = json.loads((OUT / "nights.json").read_text()) if (OUT / "nights.json").exists() else {}
    for eid in eids:
        d = OUT / eid
        meta, tl, un, st = (json.loads((d / f"{n}.json").read_text()) for n in ("meta", "timeline", "units", "stats"))
        n = len(un["unit_ids"])
        assert len(set(un["unit_ids"])) == n, "duplicate unit ids"
        for k in ("region", "names", "prior", "V0"):
            assert len(un[k]) == n, k
        for fr in un["frames"]:
            assert all(len(fr[k]) == n for k in ("f", "a", "proj"))
        assert {r["race_id"] for r in meta["races"]} == set(tl), "races vs timeline"
        for k in ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage", "evidence"):
            assert k in st, k
        miss = "geometry not built yet" if geo_ids is None else \
            f"{sum(u not in geo_ids for u in un['unit_ids'] if not u.startswith('br-zz-'))} non-abroad ids missing from geometry"
        sizes = ", ".join(f"{x}={(d / f'{x}.json').stat().st_size / 1e3:.0f}kB" for x in ("meta", "timeline", "units", "stats"))
        print(f"{eid}: {n} units, {len(un['frames'])} frames, races {len(meta['races'])}; {miss}; {sizes}")
    key = {"br2026r2": "r2", "br2026r1": "r1"}
    for eid, k in key.items():
        if eid not in eids or k not in nights:
            continue
        tl = json.loads((OUT / eid / "timeline.json").read_text())["br"]
        by = {pd.Timestamp(r["t"]).tz_convert(BRT).strftime("%H:%M"): r for r in tl}
        rows = nights[k]["rows"]
        print(f"{eid} national vs nights.json[{k}] (BRT):")
        worst: dict[str, float] = {}
        for r in rows:
            x = by.get(r["t"])
            if x is None:
                continue
            for a, b in (("pm", "price"), ("P", "P"), ("share", "share"), ("sd", "sd"), ("raw", "raw"), ("counted", "counted")):
                if x[a] is not None and r[b] is not None:
                    worst[a] = max(worst.get(a, 0.0), abs(x[a] - r[b]))
        for r in rows[::len(rows) // 5][:6]:
            x = by.get(r["t"], {})
            print(f"  {r['t']}  nights: p={r['price']} P={r['P']} share={r['share']} raw={r['raw']} counted={r['counted']}"
                  f" | export: p={x.get('pm')} P={x.get('P')} share={x.get('share')} raw={x.get('raw')} counted={x.get('counted')}")
        print(f"  max |difference| over all {len(rows)} shared minutes: "
              + ", ".join(f"{a} {v:.5f}" for a, v in worst.items())
              + "  (raw: nights.json labels each minute with the last tally *inside* it; the export uses the last "
                "tally at or before the minute, i.e. no look-ahead)")


def main(eids: list[str]) -> None:
    names = mun_names()
    entries = []
    for eid in eids:
        print(eid)
        entries.append(export(eid, names))
    p = OUT / "elections.json"
    have = json.loads(p.read_text()) if p.exists() else []
    new = {e["id"]: e for e in entries}
    merged = [new.pop(e["id"], e) for e in have] + list(new.values())   # re-exported ids keep their position
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    validate(eids)
    print("\nnewly computed (not previously reported):")
    for eid, w in NEWLY_COMPUTED.items():
        print(f"  {eid}: " + "; ".join(dict.fromkeys(w)))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_child":
        child(*sys.argv[2:8])
    else:
        main(sys.argv[1:] or list(ELECTIONS))
