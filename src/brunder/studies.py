"""Cross-category studies of information under-weighting, run from pre-registered specs.

Observations are built point-in-time from each market's 5-min YES price path p(·) around its
scheduled information time E (pit.asof; entries at t + EXEC_DELAY; nothing after the market's
close is ever used as a signal or an entry).

Study kinds (spec `kind`):
  shock_drift      first |p_t − p_{t−1h}| ≥ threshold in [E−7d, E+2d]; s = sign of the move;
                   drift = s·(y − p_entry). Depth = taker $ in the 7 days before t (data-api).
                   Primary tests per category: deep_continuation (drift > 0 | depth ≥ split),
                   thin_reversal (drift < 0 | depth < split).
  favourite_tilt   at E − h for each horizon: r = y − p_entry regressed on (p_t − ½);
                   primary per category: slope > 0 (favourites under-priced = information
                   under-weighted).
  late_certainty   first horizon (largest h first) where the leading side trades in [lo, hi]:
                   buy it; primary per category: mean net return per $ > 0 after spread + fee.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import pit, registry, stats, universe
from .paths import PROCESSED, ROOT

OBS = PROCESSED / "observations"
GRID = pd.Timedelta("30min")
WINDOW = (pd.Timedelta(days=-7), pd.Timedelta(days=2))
LO, HI = 0.03, 0.97
THRESHOLDS = (0.05, 0.10, 0.15, 0.20)
HORIZONS = {"7d": pd.Timedelta(days=7), "1d": pd.Timedelta(days=1), "6h": pd.Timedelta(hours=6),
            "1h": pd.Timedelta(hours=1)}
MIN_CLUSTERS = 20


# ------------------------------------------------------------------ observations

def _market_obs(m, px: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    T = px["time"].to_numpy(dtype="datetime64[ns]")
    V = px["price"].to_numpy(float)
    E, close = m.scheduled, m.closed_time
    last_ok = (close - pit.EXEC_DELAY) if pd.notna(close) else pd.Timestamp.max.tz_localize("UTC")
    base = {"condition_id": m.condition_id, "event_id": m.event_id, "category": m.category,
            "yes_token": m.yes_token, "y": m.outcome, "fee_rate": m.fee_rate}

    # label check: a price observed after close that contradicts the recorded outcome
    post = V[T >= np.datetime64(close.tz_convert(None))] if pd.notna(close) else V[:0]
    label = {"condition_id": m.condition_id, "post_close_price": float(post[-1]) if len(post) else np.nan}

    g = pd.date_range((E + WINDOW[0]).ceil(GRID), E + WINDOW[1], freq=GRID)
    g = g[(g >= pd.Timestamp(T[0], tz="UTC") + pd.Timedelta("6h")) & (g <= last_ok)]
    shocks = []
    lag = np.timedelta64(int(pit.OBS_LAG.total_seconds()), "s")
    delay = np.timedelta64(int(pit.EXEC_DELAY.total_seconds()), "s")
    if len(g):
        q = g.tz_convert(None).to_numpy(dtype="datetime64[ns]")
        p = pit.asof(T, V, q - lag)
        p1 = pit.asof(T, V, q - lag - np.timedelta64(1, "h"))
        pe = pit.asof(T, V, q + delay)
        ok = (p >= LO) & (p <= HI) & np.isfinite(p1) & np.isfinite(pe)
        m1 = p - p1
        for thr in THRESHOLDS:
            hit = np.flatnonzero(ok & (np.abs(m1) >= thr))
            if len(hit):
                i = hit[0]
                shocks.append({**base, "threshold": thr, "t": g[i], "hours_to_E": (g[i] - E) / pd.Timedelta("1h"),
                               "p": p[i], "m1": m1[i], "p_entry": pe[i]})
    horizons = []
    for name, h in HORIZONS.items():
        t = E - h
        if t > last_ok or t < pd.Timestamp(T[0], tz="UTC") + pd.Timedelta("6h"):
            continue
        q = np.array([t.tz_convert(None).to_datetime64()])
        p = pit.asof(T, V, q - lag)[0]
        pe = pit.asof(T, V, q + delay)[0]
        if np.isfinite(p) and np.isfinite(pe) and LO <= p <= HI:
            horizons.append({**base, "horizon": name, "t": t, "p": p, "p_entry": pe})
    return shocks, horizons, label


def build_observations() -> None:
    """One pass over every market's price file → shocks / horizons / labels parquet (all splits)."""
    _, mk = universe.load()
    split = pit.split_events(mk)
    mk = mk.assign(split=mk["event_id"].map(split))
    S, H, L = [], [], []
    for m in mk.itertuples():
        f = universe.DIR / "prices" / f"{m.condition_id}.parquet"
        if pd.isna(m.scheduled) or not f.exists():
            continue
        px = pd.read_parquet(f)
        if len(px) < 10:
            continue
        s, h, lab = _market_obs(m, px)
        S += [{**x, "split": m.split} for x in s]
        H += [{**x, "split": m.split} for x in h]
        L.append(lab)
    OBS.mkdir(parents=True, exist_ok=True)
    labels = pd.DataFrame(L).merge(mk[["condition_id", "outcome"]], on="condition_id")
    labels["label_conflict"] = ((labels["post_close_price"] >= 0.9) & (labels["outcome"] == 0)) | \
                               ((labels["post_close_price"] <= 0.1) & (labels["outcome"] == 1))
    pd.DataFrame(S).to_parquet(OBS / "shocks.parquet")
    pd.DataFrame(H).to_parquet(OBS / "horizons.parquet")
    labels.to_parquet(OBS / "labels.parquet")
    print(f"shocks {len(S)}, horizon obs {len(H)}, label conflicts {int(labels['label_conflict'].sum())}/{len(labels)}")


def _load(name: str, split: str, categories) -> pd.DataFrame:
    d = pd.read_parquet(OBS / f"{name}.parquet")
    bad = pd.read_parquet(OBS / "labels.parquet").query("label_conflict")["condition_id"]
    d = d[(d["split"] == split) & ~d["condition_id"].isin(bad)]
    return d if categories == "all" else d[d["category"].isin(categories)]


# ------------------------------------------------------------------ returns

def _side_returns(d: pd.DataFrame, buy_yes: np.ndarray, half_spread: float) -> np.ndarray:
    q = np.where(buy_yes, d["p_entry"], 1 - d["p_entry"])
    cost = pit.taker_cost(q, d["fee_rate"], half_spread)
    win = np.where(buy_yes, d["y"] == 1, d["y"] == 0)
    return np.where(win, 1 / np.minimum(cost, 0.999), 0.0) - 1


def _test(d: pd.DataFrame, stat, direction: str, seed: int) -> dict:
    if d["event_id"].nunique() < MIN_CLUSTERS:
        return {"estimate": np.nan, "lo95": np.nan, "hi95": np.nan, "p": np.nan,
                "n": len(d), "clusters": d["event_id"].nunique(), "status": "insufficient"}
    r = stats.cluster_bootstrap(d, stat, seed=seed)
    return {"estimate": r["estimate"], "lo95": r["lo95"], "hi95": r["hi95"],
            "p": r["p_greater"] if direction == "greater" else r["p_less"],
            "n": r["n"], "clusters": r["clusters"], "status": "tested"}


# ------------------------------------------------------------------ study kinds

def shock_drift(spec: dict, split: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    P = spec["params"]
    d = _load("shocks", split, spec["categories"])
    d = d[d["threshold"] == P["threshold"]].copy()
    cache = OBS / "presignal.parquet"
    have = pd.read_parquet(cache) if cache.exists() else pd.DataFrame(columns=["condition_id", "t"])
    if len(have):
        have["t"] = pd.to_datetime(have["t"], utc=True)
    need = d[["condition_id", "t"]].merge(have[["condition_id", "t"]], how="left", indicator=True)
    need = need[need["_merge"] == "left_only"].drop(columns="_merge")
    if len(need):
        got = asyncio.run(universe.presignal_volume(need, P["depth_window"]))
        have = pd.concat([have, got], ignore_index=True)
        have["t"] = pd.to_datetime(have["t"], utc=True)
        have.to_parquet(cache)
    d = d.merge(have, on=["condition_id", "t"], how="left")
    d = d[~d["failed"].fillna(True).astype(bool)]
    d["sign"] = np.sign(d["m1"])
    d["drift"] = d["sign"] * (d["y"] - d["p_entry"])
    d["follow_ret"] = _side_returns(d, d["sign"].to_numpy() > 0, P["half_spread"])
    d["fade_ret"] = _side_returns(d, d["sign"].to_numpy() < 0, P["half_spread"])
    d["log_depth"] = np.log10(1 + d["pre_vol"])
    d["deep"] = d["pre_vol"] >= P["depth_split_usd"]
    rows = []
    for i, (cat, g) in enumerate(sorted(d.groupby("category"))):
        rows.append({"category": cat, "test": "deep_continuation",
                     **_test(g[g["deep"]], stats.mean_of("drift"), "greater", i)})
        rows.append({"category": cat, "test": "thin_reversal",
                     **_test(g[~g["deep"]], stats.mean_of("drift"), "less", 100 + i)})
    secondary = []
    for i, (cat, g) in enumerate(sorted(d.groupby("category"))):
        for name, sub, stat in (("depth_slope", g, stats.slope_of("drift", "log_depth")),
                                ("follow_ret_deep", g[g["deep"]], stats.mean_of("follow_ret")),
                                ("fade_ret_thin", g[~g["deep"]], stats.mean_of("fade_ret"))):
            secondary.append({"category": cat, "statistic": name, **_test(sub, stat, "greater", 200 + i)})
    bins = d.assign(depth_bin=pd.cut(d["pre_vol"], [-1, 1e3, 1e4, 1e5, 1e6, np.inf],
                                     labels=["<$1k", "$1k–10k", "$10k–100k", "$100k–1M", "≥$1M"])) \
        .groupby(["category", "depth_bin"], observed=True) \
        .agg(signals=("drift", "size"), events=("event_id", "nunique"), drift=("drift", "mean"),
             follow_ret=("follow_ret", "mean"), fade_ret=("fade_ret", "mean")).reset_index()
    return pd.DataFrame(rows), {"secondary": pd.DataFrame(secondary), "depth_bins": bins, "signals": d}


def favourite_tilt(spec: dict, split: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    P = spec["params"]
    d = _load("horizons", split, spec["categories"])
    d = d[d["horizon"].isin(P["horizons"])].copy()
    d["r"] = d["y"] - d["p_entry"]
    d["tilt"] = d["p"] - 0.5
    fav_yes = d["p"].to_numpy() >= 0.5
    d["fav_ret"] = _side_returns(d, fav_yes, P["half_spread"])
    rows, secondary = [], []
    for i, (cat, g) in enumerate(sorted(d.groupby("category"))):
        rows.append({"category": cat, "test": "favourite_slope", **_test(g, stats.slope_of("r", "tilt"), "greater", i)})
        secondary.append({"category": cat, "statistic": "fav_ret", **_test(g, stats.mean_of("fav_ret"), "greater", 300 + i)})
    calib = d.assign(bin=pd.cut(d["p"], np.linspace(LO, HI, 11))).groupby(["category", "bin"], observed=True) \
        .agg(obs=("y", "size"), price=("p", "mean"), freq=("y", "mean")).reset_index()
    return pd.DataFrame(rows), {"secondary": pd.DataFrame(secondary), "calibration": calib, "signals": d}


def late_certainty(spec: dict, split: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    P = spec["params"]
    d = _load("horizons", split, spec["categories"])
    d = d[d["horizon"].isin(P["horizons"])].copy()
    lead = np.maximum(d["p"], 1 - d["p"])
    d = d[(lead >= P["lead_lo"]) & (lead <= P["lead_hi"])]
    order = {h: i for i, h in enumerate(P["horizons"])}               # earliest listed horizon first
    d = d.assign(_o=d["horizon"].map(order)).sort_values("_o").groupby("condition_id", as_index=False).first()
    fav_yes = d["p"].to_numpy() >= 0.5
    d["ret"] = _side_returns(d, fav_yes, P["half_spread"])
    d["side_yes"] = fav_yes
    rows = []
    for i, (cat, g) in enumerate(sorted(d.groupby("category"))):
        rows.append({"category": cat, "test": "net_return", **_test(g, stats.mean_of("ret"), "greater", i)})
    return pd.DataFrame(rows), {"signals": d}


# ------------------------------------------------------------------ runner

def _entries(kind: str, sig: pd.DataFrame, half_spread: float) -> pd.DataFrame:
    """The trade each study would place: side and YES-equivalent limit (entry ± half-spread)."""
    if kind == "shock_drift":
        side_yes = np.where(sig["deep"], sig["sign"] > 0, sig["sign"] < 0)    # follow deep, fade thin
    elif kind == "late_certainty":
        side_yes = sig["side_yes"].to_numpy()
    else:
        return pd.DataFrame()
    t = sig["t"] + pit.EXEC_DELAY
    limit = np.where(side_yes, sig["p_entry"] + half_spread, sig["p_entry"] - half_spread)
    return pd.DataFrame({"condition_id": sig["condition_id"].to_numpy(), "event_id": sig["event_id"].to_numpy(),
                         "category": sig["category"].to_numpy(), "yes_token": sig["yes_token"].to_numpy(),
                         "t": t.to_numpy(), "side_yes": side_yes, "limit_yes": limit})


def run(spec_path: Path, split: str, fills: bool | None = None) -> Path:
    """fills: check executability against actual trades (default: on for holdout)."""
    spec = registry.load_spec(spec_path)
    look = None
    if split == "holdout":
        look = registry.open_holdout(spec)
    elif split != "discovery":
        raise ValueError("split must be 'discovery' or 'holdout'")
    primary, extra = KINDS[spec["kind"]](spec, split)
    if fills if fills is not None else split == "holdout":
        ent = _entries(spec["kind"], extra["signals"], spec["params"]["half_spread"])
        if len(ent):
            ent["t"] = pd.to_datetime(ent["t"], utc=True)
            f = asyncio.run(universe.fills_after(ent))
            extra["fills"] = f.groupby("category").agg(
                entries=("fill_notional", "size"),
                share_fillable_100usd=("fill_notional", lambda x: float((x >= 100).mean())),
                median_fill_usd=("fill_notional", "median"),
                total_fill_usd=("fill_notional", "sum")).reset_index()
    tested = primary["status"] == "tested"
    primary.loc[tested, "p_holm"] = stats.holm(primary.loc[tested, "p"])
    primary.loc[tested, "q_bh"] = stats.bh(primary.loc[tested, "p"])
    primary["significant"] = primary["p_holm"] < spec["primary"]["alpha"]
    out = ROOT / "results" / "studies" / spec["id"] / split
    out.mkdir(parents=True, exist_ok=True)
    primary.to_csv(out / "primary.csv", index=False)
    for k, v in extra.items():
        v.to_parquet(out / f"{k}.parquet") if k == "signals" else v.to_csv(out / f"{k}.csv", index=False)
    meta = {"spec": spec["id"], "sha256": spec["_sha256"], "split": split, "holdout_look": look,
            "cutoff": str(pit.CUTOFF), "exec_delay": str(pit.EXEC_DELAY)}
    (out / "run.json").write_text(json.dumps(meta, indent=2, default=str))
    pd.set_option("display.width", 220, "display.max_columns", 20)
    if look and look["look"] > 1:
        print(f"!! holdout look #{look['look']} for {spec['id']} — results are no longer a first look")
    print(f"== {spec['id']} [{split}] — primary tests, Holm-adjusted across {int(tested.sum())} tests")
    print(primary.round(4).to_string(index=False))
    return out
