"""Score the Hungary 2026 count model against Polymarket: replay every official count publication with the frozen
config, then the standard metrics (settle lead, calibration, early error, catch-up) and the edge-audit profit test
(analysis/edge_audit.py method: frozen Brazil night rule, one entry per market, real taker trades after the signal).

Inputs (no recording was made on the night; everything is public and archived after the fact):
  - official count publications $BRUNDER_DATA/raw/hungary/ogy2026/<MMDDHHMM>/szavossz/{OevkJkv,ListasJkv}.json
    (analysis/hu_fetch.py night): 40 on election night (20:00-06:00 CEST) and 12 later ones;
  - Polymarket 1-minute YES price history (analysis/hu_fetch.py markets) and, for each signal, the taker trades
    in [signal, signal + 2 h) from the public data API (cached in results/hungary/capacity_trades/);
  - outcomes: Polymarket's resolution of each market, checked against the official final (05071600).
A publication is usable from its folder time (the time the site switched to it); the replay never looks ahead.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/hu_score.py [configs/hu2026_v1.toml]
Writes results/hungary/{releases.csv, districts.parquet, ticks.parquet, races.parquet, settle.csv, signals.csv,
profit_by_size.csv, stats.json}.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder import night
from brunder.paths import POLYMARKET, RAW, ROOT

sys.path.insert(0, str(ROOT / "analysis"))
import edge_audit as ea  # noqa: E402
import underpricing_figures as uf  # noqa: E402
from night_capacity import _fetch_takers, same_side_fills  # noqa: E402

OUT = ROOT / "results" / "hungary"
CFG = ROOT / "configs" / "hu2026_v1.toml"
EID = "hu2026"
LATENCY_S = (0, 60)
HORIZON_MIN = (5, 30)
SLIPS = (0.0, 0.02, 0.05)
EVIDENCE = "not-preregistered"  # settings frozen (config, 18:59Z) from 2022 only, but scored before registration
EV_NOTE = ("not pre-registered: settings frozen in configs/hu2026_v1.toml at 2026-10-28T18:59Z (sha256 6fec9912...) "
           "from the 2022 count only (9 Internet Archive copies of official night publications, 2018 prior); before "
           "the freeze the 2026 market resolutions, the 02:05 CEST national list summary and one district row had "
           "been seen while checking the feed; one scoring run on the 2026 count at 19:01:53Z, before the config was "
           "committed or registered; no setting changed afterwards")
HEAD = (60, 30, 0.0)            # headline execution, as for Brazil: 60 s latency, 30-min window, signal price
SETTLE = 0.9
CATCHUP_MIN = 30


def releases(cfg: dict, st: hu.Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model after every count publication: one row per publication (race summaries, P per market) and the
    district frames for the map."""
    year = cfg["year"]
    prior = hu.prior_2022_for_2026(cfg)
    ctx = {"pl": hu.party_lists(year), "reg": hu.registered(year), "reg_d": hu.district_registered(year)}
    cands = hu.candidates(year)
    rng = np.random.default_rng(cfg["seed"])
    rows, dist, last_list = [], [], None
    for v in sorted(hu.versions(year), key=lambda v: hu.version_time(year, v)):
        p = hu.read_version(year, v, cands)
        if p is None or p["smd"] is None:
            continue
        if p["list"] is None:
            p["list"] = last_list
        last_list = p["list"]
        o = hu.evaluate(p, prior, cfg, st, ctx, rng)
        U, V = p["smd"]
        lead = V[V["jlcs"].isin([cfg["groups"]["a"], cfg["groups"]["b"]])].sort_values("votes").groupby("unit").tail(1)
        row = {"v": v, "t": p["t"], "counted": o["smd"]["counted"], "list_counted": o["list"]["counted"],
               "list_share": o["list"]["share"], "list_sd": o["list"]["sd"], "list_raw": o["list"]["raw"],
               "list_P": o["list"]["P"], "smd_share": o["smd"]["share"], "smd_sd": o["smd"]["sd"],
               "smd_raw": o["smd"]["raw"], "seats_a": o["seats"]["a_mean"], "seats_a_sd": o["seats"]["a_sd"],
               "seats_b": o["seats"]["b_mean"], "seats_P": o["seats"]["P_a_most"],
               "turnout": o["turnout"]["mean"], "raw_districts_a": int((lead["jlcs"] == cfg["groups"]["a"]).sum()),
               "raw_districts_b": int((lead["jlcs"] == cfg["groups"]["b"]).sum())}
        row.update({f"P:{k}": x for k, x in o["P"].items()})
        rows.append(row)
        dist.append(o["districts"].assign(t=p["t"], v=v))
        print(v, f"counted={row['counted']:.3f} list={row['list_share']:.4f}±{row['list_sd']:.4f} "
                 f"seatsA={row['seats_a']:.1f} P(most)={row['seats_P']:.3f}", flush=True)
    return pd.DataFrame(rows), pd.concat(dist, ignore_index=True)


def finals(cfg: dict) -> dict:
    """Official final (the folder the site settled on) for the races and the market definitions."""
    year, G = cfg["year"], cfg["groups"]
    E = hu.ELECTIONS[year]
    U, V = hu.smd_count(hu._j(year, f"{E['final']}/szavossz/OevkJkv.json"), hu.candidates(year))
    st = hu.district_state(U, V, G["a"], G["b"])
    L = hu.list_count(hu._j(year, f"{E['final']}/szavossz/ListasJkv.json"))
    tot = L["dom"].add(L["mail"], fill_value=0)
    pl = hu.party_lists(year)
    party = tot.reindex(pl.index[pl["kind"] != "N"]).fillna(0)
    a, b = float(tot[G["tl_a"]]), float(tot[G["tl_b"]])
    seats = hu.final_seats(year)
    m = L["nat"]
    patko = hu._j(year, f"{E['final']}/szavossz/Patko.json")
    official = {int(x["jlcs_kod"]): int(x["mand_ossz"]) for x in hu._list(patko)["eredmenyek"]}
    return {"list_share": a / (a + b), "smd_share": float(st["a"].sum() / st["n"].sum()),
            "margin_pt": 100 * (a - b) / float(party.sum()), "turnout": float(m["szavazott_osszesen"]) / float(m["vp_osszes"]),
            "seats_a": int(seats.loc[G["tl_a"], "seats"]), "seats_b": int(seats.loc[G["tl_b"], "seats"]),
            "seats_official_a": official.get(G["a"]), "seats_official_b": official.get(G["b"]),
            "seats": seats[seats["seats"] > 0][["name", "district_seats", "list_seats", "seats"]].to_dict("index")}


def model_truth(cfg: dict, F: dict) -> dict[str, int]:
    out = {}
    sa = F["seats_a"]
    for m in cfg["markets"]:
        k, side = m["kind"], m.get("party")
        if k in ("most_seats", "pm"):
            y = (F["seats_a"] > F["seats_b"]) == (side == "a")
        elif k == "list_winner":
            y = (F["list_share"] > 0.5) == (side == "a")
        elif k == "list_margin":
            mg = F["margin_pt"] if side == "a" else -F["margin_pt"]
            y = m["lo"] <= mg < m["hi"]
        elif k == "seats_band":
            y = m["lo"] <= sa <= m["hi"]
        elif k == "seats_atleast":
            y = sa >= m["n"]
        else:
            y = m["lo"] <= 100 * F["turnout"] < m["hi"]
        out[m["id"]] = int(y)
    return out


def market_meta(cfg: dict) -> pd.DataFrame:
    """Per configured market: YES token, condition id, Polymarket resolution (YES won = 1), taker fee rate."""
    rows = []
    for m in cfg["markets"]:
        d = POLYMARKET / m["event"]
        mk = pd.read_parquet(d / "markets.parquet").set_index("slug").loc[m["market"]]
        ev = json.loads((d / "event.json").read_text())
        g = next(x for x in ev["markets"] if x["slug"] == m["market"])
        fs = g.get("feeSchedule") or {}
        lp = list(mk["last_prices"])
        rows.append({"id": m["id"], "slug": m["market"], "event": m["event"], "yes_token": mk["yes_token"],
                     "condition_id": mk["condition_id"], "resolved_yes": int(round(lp[0])) if lp else None,
                     "closed_time": mk["closed_time"], "fee_rate": float(fs.get("rate") or 0) if g.get("feesEnabled") else 0.0,
                     "question": mk["question"], "race": m["race"]})
    return pd.DataFrame(rows).set_index("id")


def ticks(cfg: dict, R: pd.DataFrame, MM: pd.DataFrame, truth: dict) -> pd.DataFrame:
    """1-minute grid from poll close to the end of the price window: the last publication's P (none before the
    first), the last traded/mid YES price, all-in costs and edges as in brunder.night.replay."""
    T = cfg["trade"]
    grid = pd.date_range(pd.Timestamp(cfg["poll_close_utc"]).ceil("1min"), pd.Timestamp(cfg["prices"]["end_utc"]),
                         freq="1min")
    rel = R.set_index("t").sort_index()
    hs = T["half_spread"]
    out = []
    for m in cfg["markets"]:
        mid = m["id"]
        px = pd.read_parquet(POLYMARKET / m["event"] / "prices" / f"{m['market']}.parquet").set_index("time")["price"].sort_index()
        price = px.reindex(px.index.union(grid)).ffill().reindex(grid)
        end = MM.loc[mid, "closed_time"]
        if pd.notna(end):
            price[grid > pd.Timestamp(end)] = np.nan
        step = lambda s: s.reindex(s.index.union(grid)).ffill().reindex(grid)  # noqa: E731
        P = step(rel[f"P:{mid}"])
        cnt_col = {"list": "list_counted", "seats": "counted", "turnout": "counted"}[m["race"]]
        fee = MM.loc[mid, "fee_rate"]
        d = pd.DataFrame({"t": grid, "scope": mid, "P": P.to_numpy(), "price": price.to_numpy(),
                          "scope_counted": step(rel[cnt_col]).fillna(0.0).to_numpy(), "counted": step(rel["counted"]).fillna(0.0).to_numpy(),
                          "outcome": truth[mid]})
        d["yes_cost"] = d["price"] + hs + fee * d["price"] * (1 - d["price"])
        d["no_cost"] = (1 - d["price"]) + hs + fee * d["price"] * (1 - d["price"])
        d["edge_yes"] = d["P"] - d["yes_cost"]
        d["edge_no"] = (1 - d["P"]) - d["no_cost"]
        out.append(d)
    return pd.concat(out, ignore_index=True)


def races(cfg: dict, R: pd.DataFrame, TK: pd.DataFrame, F: dict, sett: pd.DataFrame) -> list[dict]:
    """Edge-audit race dicts: list (Tisza vs Fidesz national list vote) and seats (most seats; share = projected
    two-way district vote), minute rows from the first publication."""
    rel = R.set_index("t").sort_index()
    out = []
    for race, mid, cols, final in (("list", "list_winner_tisza", ("list_counted", "list_share", "list_sd", "list_raw", "P:list_winner_tisza"), F["list_share"]),
                                   ("seats", "most_seats_tisza", ("counted", "smd_share", "smd_sd", "smd_raw", "P:most_seats_tisza"), F["smd_share"])):
        g = TK[TK["scope"] == mid].set_index("t")
        grid = g.index
        step = lambda s: s.reindex(s.index.union(grid)).ffill().reindex(grid)  # noqa: E731
        rows = pd.DataFrame({"t": grid, **{k: step(rel[c]).to_numpy() for k, c in zip(("counted", "share", "sd", "raw", "P"), cols)},
                             "price": g["price"].to_numpy()}).dropna(subset=["P"]).reset_index(drop=True)
        s = sett.set_index("scope").loc[mid]
        out.append({"eid": EID, "race": race, "scope": mid, "rows": rows, "final": float(final),
                    "outcome": int(TK.loc[TK["scope"] == mid, "outcome"].iloc[0]),
                    "model_settle": s["model_0.9"], "market_settle": s["market_0.9"]})
    return out


def signals(cfg: dict, TK: pd.DataFrame, MM: pd.DataFrame, truth: dict, fetch: bool = True) -> pd.DataFrame:
    """Frozen-rule entries (night.evaluate, favoured side only) and their real-trade fills in every variant.
    fetch=False (sensitivity replays): settle times only, no trade download and no profit rows."""
    T = cfg["trade"]
    ev = night.evaluate(TK.dropna(subset=["P", "price"]), truth, T["min_edge"], T["min_counted"], T["favoured_only"])
    if not fetch:
        return pd.DataFrame(), ev
    sig = ev.dropna(subset=["entry"]).merge(TK, left_on=["scope", "entry"], right_on=["scope", "t"], how="left")
    cache = OUT / "capacity_trades"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in sig.itertuples():
        m = MM.loc[r.scope]
        entry = pd.Timestamp(r.entry)
        lo, hi = int(entry.timestamp()), int((entry + pd.Timedelta(minutes=120)).timestamp())
        f = cache / f"{m['slug']}_{lo}_{hi}.parquet"
        if f.exists():
            trades = pd.read_parquet(f)
        else:
            trades = _fetch_takers(m["condition_id"], lo, hi)
            trades.to_parquet(f)
        side_yes = r.side == "YES"
        cost = r.yes_cost if side_yes else r.no_cost
        p_fav = r.P if side_yes else 1 - r.P
        won = int((truth[r.scope] == 1) == side_yes)
        base = {"eid": EID, "market_key": f"{EID}:{r.scope}", "scope": r.scope, "market": m["slug"], "side": r.side,
                "entry": entry, "price": r.price, "cost": cost, "P_fav": p_fav, "edge": p_fav - cost, "won": won,
                "fee_rate": m["fee_rate"], "counted": r.scope_counted, "trades_in_2h": len(trades)}
        for L, N, s in itertools.product(LATENCY_S, HORIZON_MIN, SLIPS):
            lim = r.price + s if side_yes else r.price - s
            fs = same_side_fills(trades, m["yes_token"], side_yes, lim) if len(trades) else trades.assign(notional=[])
            w = (fs["time"] >= entry + pd.Timedelta(seconds=L)) & (fs["time"] < entry + pd.Timedelta(minutes=N)) if len(fs) else []
            avail = float(fs.loc[w, "notional"].sum()) if len(fs) else 0.0
            c = cost + s
            rows.append({**base, "latency_s": L, "horizon_min": N, "slip": s, "avail": avail, "all_in": c,
                         "ret": (1 / c - 1) if won else -1.0, "rule_ok": bool(p_fav - c >= T["min_edge"] - 1e-12)})
    return pd.DataFrame(rows), ev


def summarize(sig: pd.DataFrame, table: list[dict]) -> dict:
    """edge_audit.main's summary of one execution variant."""
    n, k = len(sig), int(sig["won"].sum()) if len(sig) else 0
    mx = next(t for t in table if t["size"] == "max")
    return {"signals": n, "won": k, "hit_rate": ea._r(k / n, 3) if n else None, "hit_rate_ci": ea._cp(k, n),
            "max_deployed": mx["deployed"], "max_profit": mx["pnl"], "max_profit_ci": mx["pnl_ci"],
            "max_return_per_usd": mx["return_per_usd"], "p_market_fair": mx["p_market_fair"],
            "largest_trade_share": mx.get("largest_trade_share"), "pnl_without_largest": mx.get("pnl_without_largest"),
            "by_size": {str(t["size"]): {kk: t.get(kk) for kk in ("fillable", "filled_any", "deployed", "pnl", "return_per_usd",
                                                                  "pnl_ci", "return_ci", "p_market_fair", "largest_trade_share")}
                        for t in table if t["size"] != "max"}}


def catchup(TK: pd.DataFrame) -> dict | None:
    """Slope of the market's 30-minute move on the model-market gap (il_score / underpricing_figures rules)."""
    parts = []
    for scope, g in TK.groupby("scope"):
        g = g.set_index("t").sort_index()
        fut = g["price"].reindex(g.index + pd.Timedelta(minutes=CATCHUP_MIN)).to_numpy()
        parts.append(pd.DataFrame({"scope": scope, "t": g.index, "gap": g["P"] - g["price"], "move": fut - g["price"].to_numpy(),
                                   "p": g["price"], "P": g["P"], "c": g["scope_counted"]}))
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
    global OUT, EVIDENCE, EV_NOTE
    cfg = hu.load_config(cfg_path)
    sens = bool(cfg.get("sensitivity"))
    if sens:                                            # v2: settings chosen after the result was known
        OUT = ROOT / "results" / f"hungary_{cfg['_id'].split('_')[-1]}"
        EVIDENCE = "sensitivity-after-result"
        EV_NOTE = cfg["sensitivity"]["label"]
    OUT.mkdir(parents=True, exist_ok=True)
    pf = RAW / cfg["prior"]["file"]
    assert hashlib.sha256(pf.read_bytes()).hexdigest() == cfg["prior"]["sha256"], "prior file changed"
    st = hu.settings_from_config(cfg)

    # ---- outcomes
    F = finals(cfg)
    MM = market_meta(cfg)
    mine = model_truth(cfg, F)
    truth = {k: int(v) for k, v in MM["resolved_yes"].items()}
    mismatch = {k: (truth[k], mine[k]) for k in truth if truth[k] != mine[k]}

    # ---- model at each publication
    R, D = releases(cfg, st)
    R.to_csv(OUT / "releases.csv", index=False)
    D.to_parquet(OUT / "districts.parquet")

    # ---- minute ticks, settle, races
    TK = ticks(cfg, R, MM, truth)
    TK.to_parquet(OUT / "ticks.parquet")
    sig, ev = signals(cfg, TK, MM, truth, fetch=not sens)
    sig.to_csv(OUT / "signals.csv", index=False)
    t_first = R["t"].min()
    srows = []
    for m in cfg["markets"]:
        mid, y = m["id"], truth[m["id"]]
        g = TK[TK["scope"] == mid]
        pe = (lambda s: s) if y == 1 else (lambda s: 1 - s)
        mod = g.dropna(subset=["P"])
        mk = g.dropna(subset=["price"])
        ms = ea.settle(mod["t"], pe(mod["P"]) >= SETTLE) if len(mod) else pd.NaT
        mod1 = mod[mod["scope_counted"] >= 0.01]
        ms1 = ea.settle(mod1["t"], pe(mod1["P"]) >= SETTLE) if len(mod1) else pd.NaT
        ps = ea.settle(mk["t"], pe(mk["price"]) >= SETTLE) if len(mk) else pd.NaT
        p_first = mk.loc[mk["t"] <= t_first, "price"]
        p_first = float(p_first.iloc[-1]) if len(p_first) else np.nan
        rs = pd.NaT
        if m["kind"] in ("most_seats", "pm", "list_winner"):
            lead_a = R["raw_districts_a"] > R["raw_districts_b"] if m["kind"] != "list_winner" else R["list_raw"] > 0.5
            ok = lead_a if m["party"] == "a" else ~lead_a
            rs = ea.settle(R["t"], ok == bool(y))
        srows.append({"market": mid, "kind": m["kind"], "outcome": y, "model": ms, "polymarket": ps, "raw": rs,
                      "model_gate1pct": ms1,
                      "lead_min": (ps - ms).total_seconds() / 60 if pd.notna(ps) and pd.notna(ms) else np.nan,
                      "lead_min_gate1pct": (ps - ms1).total_seconds() / 60 if pd.notna(ps) and pd.notna(ms1) else np.nan,
                      "price_at_close": float(mk["price"].iloc[0]) if len(mk) else np.nan,
                      "price_at_first_release": p_first, "contested": bool(0.1 < p_first < 0.9) if np.isfinite(p_first) else False,
                      "P_first": float(mod["P"].iloc[0]) if len(mod) else np.nan, "P_end": float(mod["P"].iloc[-1]) if len(mod) else np.nan})
    SE = pd.DataFrame(srows)
    SE.to_csv(OUT / "settle.csv", index=False)
    con = SE[SE["contested"]]
    lead = {"markets": int(len(SE)), "contested_markets": int(len(con)),
            "median_lead_min": None if con["lead_min"].isna().all() else float(con["lead_min"].median()),
            "median_lead_min_gate1pct": None if con["lead_min_gate1pct"].isna().all() else float(con["lead_min_gate1pct"].median()),
            "model_first": int((con["lead_min"] > 0).sum()), "market_first": int((con["lead_min"] < 0).sum()),
            "model_never": int(con["model"].isna().sum()), "market_never": int(con["polymarket"].isna().sum())}

    # ---- accuracy (edge_audit method)
    RC = races(cfg, R, TK, F, ev)
    pd.concat([r["rows"].assign(race=r["race"]) for r in RC]).to_parquet(OUT / "races.parquet")
    (OUT / "races_final.json").write_text(json.dumps({r["race"]: {"final": r["final"], "outcome": r["outcome"]} for r in RC}))
    se = {r["race"]: {f"{int(x['q'] * 100)}%": {"model": x["model_pt"], "raw": x["raw_pt"]} for x in ea.share_error(r)} for r in RC}
    cv = {r["race"]: {x["window"]: {"coverage90": x["coverage90"], "mean_z": x["mean_z"], "n": x["n"]} for x in ea.coverage(r)} for r in RC}
    mins = pd.concat([ea.pwin_minutes(r) for r in RC], ignore_index=True)
    pw = ea.pwin_scores(mins, True)
    pw["reliability_model"] = ea.reliability(mins, "P")
    pw["reliability_market"] = ea.reliability(mins, "price")
    # all markets (not only the two race markets): same window rule per market
    allm = []
    for mid, g in TK.groupby("scope"):
        s = ev.set_index("scope").loc[mid]
        r = {"eid": EID, "race": mid, "rows": g.rename(columns={"scope_counted": "counted_m"}).assign(counted=g["scope_counted"])[["t", "counted", "P", "price"]],
             "outcome": truth[mid], "model_settle": s["model_0.9"], "market_settle": s["market_0.9"]}
        allm.append(ea.pwin_minutes(r))
    pw_all = ea.pwin_scores(pd.concat(allm, ignore_index=True), True)
    accuracy = {"evidence": EVIDENCE, "share_error_pt": se, "coverage90": cv, "pwin": pw, "pwin_all_markets": pw_all,
                "pwin_window": "minutes from >= 1% counted until both model and market stayed >= 0.9 on the winner",
                "outcome": "Polymarket resolution (checked against the official final, 05071600)"}

    # ---- profit (edge_audit method)
    prof, size_rows = {}, []
    for L, N, s in itertools.product(LATENCY_S, HORIZON_MIN, SLIPS):
        for rule_only in ((False, True) if s > 0 else (False,)):
            x = sig[(sig.latency_s == L) & (sig.horizon_min == N) & (sig.slip == s)].copy() if len(sig) else sig
            if rule_only and len(x):
                x.loc[~x["rule_ok"], "avail"] = 0.0
            x = x.reset_index(drop=True)
            key = f"trades_lat{L}s_{N}min_slip{int(round(s * 100))}c" + ("_rule" if rule_only else "")
            if len(x) == 0:
                prof[key] = {"signals": 0}
                continue
            tab = ea.size_table(x, "avail", "ret", "all_in")
            prof[key] = summarize(x, tab)
            size_rows += [{"execution": key, **{a: (json.dumps(b) if isinstance(b, list) else b) for a, b in t.items()}} for t in tab]
    pd.DataFrame(size_rows).to_csv(OUT / "profit_by_size.csv", index=False)
    L, N, s = HEAD
    head = prof.get(f"trades_lat{L}s_{N}min_slip{int(s * 100)}c", {"signals": 0})
    profit = {"evidence": EVIDENCE,
              "rule": "frozen night rule: favoured side only, edge >= 5c after costs, >= 1% of the market counted, "
                      "first signal per market, held to resolution",
              "execution": f"real taker trades on our side at or better than the signal price, from {L} s to {N} min after "
                           "the signal; return at the signal's all-in cost (price + 1c half-spread + taker fee)",
              **head, "variants": {k: {kk: v.get(kk) for kk in ("max_deployed", "max_profit")} for k, v in prof.items()}}

    # ---- calibration / early error in the dashboard's standard keys
    def first_at(rows, q):
        x = rows[rows["counted"] >= q]
        return x.iloc[0] if len(x) else None
    early, calib = {}, {}
    for r in RC:
        rows = r["rows"]
        early[r["race"]] = {}
        for q in (0.01, 0.05, 0.10, 0.25):
            x = first_at(rows, q)
            early[r["race"]][f"{int(q * 100)}%"] = None if x is None else {
                "model": round(100 * (x["share"] - r["final"]), 2), "raw": round(100 * (x["raw"] - r["final"]), 2)}
        c = cv[r["race"]].get("1-95%", {})
        calib[r["race"]] = {"coverage90_1_95": c.get("coverage90"), "mean_z": c.get("mean_z"), "n": c.get("n")}

    def iso(x):
        return None if x is None or pd.isna(x) else pd.Timestamp(x).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {
        "id": cfg["id"], "evidence": EVIDENCE, "evidence_note": EV_NOTE, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        "final": {k: v for k, v in F.items() if k != "seats"}, "final_seats": F["seats"],
        "outcome_check": {"mismatches": mismatch, "note": "Polymarket resolution vs the official final as computed here"},
        "calibration": calib,
        "settle": {r.market: {"model": iso(r.model), "polymarket": iso(r.polymarket), "kalshi": None, "raw": iso(r.raw),
                              "model_gate1pct": iso(r.model_gate1pct), "contested": bool(r.contested),
                              "lead_min": None if pd.isna(r.lead_min) else round(r.lead_min, 1)} for r in SE.itertuples()},
        "settle_summary": lead,
        "catchup": {"polymarket": catchup(TK), "kalshi": None},
        "early_error": early,
        "capacity": {"polymarket": {"signal": ea._r(float(sig.loc[(sig.latency_s == 0) & (sig.horizon_min == 30) & (sig.slip == 0), "avail"].sum()), 2) if len(sig) else 0.0,
                                    "slip2c": ea._r(float(sig.loc[(sig.latency_s == 0) & (sig.horizon_min == 30) & (sig.slip == 0.02), "avail"].sum()), 2) if len(sig) else 0.0,
                                    "slip5c": ea._r(float(sig.loc[(sig.latency_s == 0) & (sig.horizon_min == 30) & (sig.slip == 0.05), "avail"].sum()), 2) if len(sig) else 0.0},
                     "kalshi": None},
        "arbitrage": None,
        "evidence_by_metric": {k: EVIDENCE for k in ("settle", "calibration", "catchup", "early_error", "capacity", "profit")},
        "accuracy": ea._clean(accuracy), "profit": ea._clean(profit),
        "reasons": {"kalshi": "no Kalshi data (project rule)", "arbitrage": "needs Kalshi prices",
                    "capacity": "dollars of real taker trades on our side at or better than the signal price in the 30 min "
                                "after each frozen-rule signal (0 s latency)"},
        "releases": len(R), "first_release": iso(R["t"].min()), "last_release": iso(R["t"].max()),
    }
    (OUT / "stats.json").write_text(json.dumps(ea._clean(stats), indent=1, ensure_ascii=False, default=str))
    pd.set_option("display.width", 250)
    print(json.dumps({k: stats[k] for k in ("final", "outcome_check", "settle_summary", "calibration", "early_error", "catchup", "capacity")},
                     indent=1, default=str))
    print(SE.to_string(index=False))
    print(sig[(sig.latency_s == 60) & (sig.horizon_min == 30) & (sig.slip == 0)].to_string(index=False) if len(sig) else "no signals")
    print(json.dumps(ea._clean({"pwin": {k: v for k, v in pw.items() if not k.startswith("reliab")}, "pwin_all": pw_all,
                                "profit_head": {k: v for k, v in profit.items() if k != "variants"}}), indent=1, default=str))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else CFG)
