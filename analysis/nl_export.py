"""Netherlands, Tweede Kamer election of 2025-10-29 -> dashboard files under site/data/nl2025/ (meta, timeline, units,
stats, entry). A RETROSPECTIVE replay, not pre-registered, with NO count history: Polymarket prices over the night
against the official final result and a projection made before any votes (the 2023 result alone, and the final polls).
Sources, licences and what could not be done: docs/netherlands.md.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_export.py

Needs the files listed in docs/netherlands.md under $BRUNDER_DATA/raw/netherlands (Kiesraad CSVs, Wikipedia poll pages) and
the maps from analysis/nl_geo.py. Downloads from Polymarket (Gamma events, CLOB price history) are cached in
$BRUNDER_DATA/raw/netherlands/polymarket, at most one request a second, User-Agent "brunder/0.1 (research; recorder)".
"""

from __future__ import annotations

import json
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import kalshi as kal
from brunder import netherlands as nl
from brunder.paths import RAW, ROOT

OUT = ROOT / "site" / "data" / "nl2025"
GEO = ROOT / "site" / "data" / "geo" / "nl_gemeenten.topo.json"
CONFIG = ROOT / "configs" / "nl2025_v1.toml"
NLRAW = RAW / "netherlands"
PM = NLRAW / "polymarket"
UA = "brunder/0.1 (research; recorder)"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_EVENT = "KXPARTYWINNL-25"          # "Who will win the Dutch general election?", one market per party, settled 2025-11-10
KALSHI_SUFFIX = {"glpvda": "GLPDVA"}       # every other list: its id upper-cased

POLL_CLOSE = pd.Timestamp("2025-10-29T20:00:00Z")     # 21:00 CET
START = pd.Timestamp("2025-10-29T18:00:00Z")          # 19:00 CET
END = pd.Timestamp("2025-10-30T12:00:00Z")            # 13:00 CET
FETCH_FROM = START - pd.Timedelta(hours=12)           # so that every market has a price at START
SETTLE = 0.9
SETTLE_HI = 0.95
REASON = "no count over time exists for this election (nothing official or openly licensed was archived during the night)"
SCORE_TIMES = [START, POLL_CLOSE, POLL_CLOSE + pd.Timedelta(hours=1), POLL_CLOSE + pd.Timedelta(hours=3), END]

MOST_EVENT = "netherlands-parliamentary-election"
SECOND_EVENT = "which-party-wins-2nd-most-seats-in-netherlands-election"
BAND_EVENTS = {
    "pvv": "dutch-parliamentary-election-pvv-seats",
    "glpvda": "dutch-parliamentary-election-groenlinkspvda-seats",
    "vvd": "netherlands-parliamentary-election-vvd-seats",
    "d66": "netherlands-parliamentary-election-d66-seats",
    "ja21": "netherlands-parliamentary-election-ja21-seats",
    "cda": "how-many-seats-will-the-christian-democratic-appeal-cda-win-in-the-upcoming-dutch-parliamentary-election",
}
SECOND_LISTS = ("pvv", "vvd", "glpvda", "cda", "d66", "ja21")
# the contest this page is about goes first (the dashboard's headline race is the first one without a region)
ORDER = ["d66", "pvv", "glpvda", "vvd", "cda", "ja21", "fvd", "bbb", "denk", "sgp", "pvdd", "cu", "sp", "volt", "nsc"]
LONG = {"pvv": "PVV", "glpvda": "GL/PvdA", "vvd": "VVD", "nsc": "NSC", "d66": "D66", "bbb": "BBB", "cda": "CDA", "sp": "SP",
        "denk": "DENK", "pvdd": "Party for the Animals", "fvd": "Forum for Democracy", "sgp": "SGP", "cu": "ChristenUnie",
        "volt": "Volt", "ja21": "JA21"}

_last = [0.0]


def get_json(url: str, params: dict) -> dict | list:
    wait = 1.05 - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                _last[0] = time.monotonic()
                return json.loads(r.read())
        except Exception:
            _last[0] = time.monotonic()
            if attempt == 4:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------ Polymarket
def event(slug: str) -> dict:
    p = PM / "events" / f"{slug}.json"
    if not p.exists():
        res = get_json(f"{GAMMA}/events", {"slug": slug})
        assert res, slug
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res[0]))
    return json.loads(p.read_text())


def prices(slug: str, token: str) -> pd.Series:
    """YES price by minute (last price in the minute), from FETCH_FROM to END."""
    p = PM / "prices" / f"{slug}.json"
    if not p.exists():
        hist, t = [], FETCH_FROM
        while t < END:
            u = min(t + pd.Timedelta(hours=6), END)
            d = get_json(f"{CLOB}/prices-history", {"market": token, "startTs": int(t.timestamp()),
                                                    "endTs": int(u.timestamp()), "fidelity": 1})
            hist += d.get("history", [])
            t = u
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(hist))
    h = pd.DataFrame(json.loads(p.read_text()), columns=["t", "p"]).drop_duplicates("t").sort_values("t")
    h["time"] = pd.to_datetime(h["t"], unit="s", utc=True).dt.floor("min")
    return h.groupby("time")["p"].last()


def kalshi_series(party: str) -> tuple[pd.Series, dict]:
    """Kalshi P(YES) by minute for the party's market in the 'most seats' event, and the market's own record. Market data of
    markets settled before Kalshi's historical cutoff come from /historical; candles are cached."""
    tick = f"{KALSHI_EVENT}-{KALSHI_SUFFIX.get(party, party.upper())}"
    mp, cp = PM.parent / "kalshi" / f"{tick}.market.json", PM.parent / "kalshi" / f"{tick}.candles.json"
    if not mp.exists():
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(get_json(f"{KALSHI_API}/historical/markets/{tick}", {})["market"]))
    if not cp.exists():
        d = get_json(f"{KALSHI_API}/historical/markets/{tick}/candlesticks",
                     {"start_ts": int(FETCH_FROM.timestamp()), "end_ts": int(END.timestamp()), "period_interval": 1})
        cp.write_text(json.dumps(d.get("candlesticks") or []))
    mk = json.loads(mp.read_text())
    rows = [{"time": pd.Timestamp(k["end_period_ts"], unit="s", tz="UTC"), "yes_bid": kal._close(k.get("yes_bid")),
             "yes_ask": kal._close(k.get("yes_ask")), "price": kal._close(k.get("price"))} for k in json.loads(cp.read_text())]
    if not rows:
        return pd.Series(dtype=float), mk
    c = pd.DataFrame(rows).drop_duplicates("time").sort_values("time")
    return kal.prob_from_candles(c, pd.date_range(START, END, freq="min")), mk


def band_text(title: str) -> str:
    t = title.replace("\u2013", "-").strip()
    if t[0] in "≤":
        return f"{t[1:]} or fewer"
    if t[0] == "<":
        return f"fewer than {t[1:]}"
    if t[0] == "≥" or t.endswith("+"):
        return f"{t.strip('≥+')} or more"
    return t


def specs() -> list[dict]:
    """Every market used: spec dict with the race, the outcome rule and the Polymarket market."""
    out = []
    label_to_id = {v.lower(): k for k, v in nl.LABEL.items()}

    def party_of(m: dict) -> str | None:
        return label_to_id.get((m.get("groupItemTitle") or "").strip().lower())

    def market(m: dict, **kw) -> dict:
        toks, outs = json.loads(m["clobTokenIds"]), json.loads(m["outcomes"])
        prices_final = json.loads(m["outcomePrices"])
        yes = outs.index("Yes")
        assert m.get("closed") and float(prices_final[yes]) in (0.0, 1.0), (m["slug"], prices_final)
        return {"slug": m["slug"], "question": m["question"], "token": toks[yes], "yes_won": float(prices_final[yes]) == 1.0,
                "volume": float(m.get("volume") or 0), **kw}

    ev = event(MOST_EVENT)
    most = {party_of(m): m for m in ev["markets"] if party_of(m)}
    assert set(most) == set(nl.PARTY_IDS) - {"50plus"}, sorted(most)
    for i in ORDER:
        out.append(market(most[i], kind="most", party=i, event=MOST_EVENT, race_id=f"most-{i}",
                          name=f"{LONG[i]} wins the most seats", a=LONG[i], b=f"Another party"))
    ev = event(SECOND_EVENT)
    second = {party_of(m): m for m in ev["markets"] if party_of(m) and float(m.get("volume") or 0) > 0}
    for i in SECOND_LISTS:
        out.append(market(second[i], kind="second", party=i, event=SECOND_EVENT, race_id=f"second-{i}",
                          name=f"{LONG[i]} wins the second most seats", a=LONG[i], b="Another party"))
    for i, slug in BAND_EVENTS.items():
        ev = event(slug)
        for m in ev["markets"]:
            title = m["groupItemTitle"]
            lo, hi = nl.parse_band(title)
            band = title.replace("\u2013", "-").replace("≤", "le").replace("≥", "ge").replace("<", "lt").replace("+", "plus")
            out.append(market(m, kind="band", party=i, event=slug, race_id=f"{i}-{band}", lo=lo, hi=hi, title=title,
                              name=f"{LONG[i]} wins {band_text(title)} seats", a=f"{LONG[i]} {band_text(title)}",
                              b="Other number of seats"))
    return out


def settle_time(series: pd.Series, yes_won: bool, level: float = SETTLE) -> pd.Timestamp | None:
    """First minute from which P(eventual outcome) stays >= level until END (forward-filled)."""
    q = series if yes_won else 1 - series
    bad = q[~(q >= level)]
    if len(bad) == 0:
        return q.index[0]
    last_bad = bad.index[-1]
    return None if last_bad >= q.index[-1] else last_bad + pd.Timedelta(minutes=1)


def main() -> None:
    cfg = tomllib.loads(CONFIG.read_text())
    m = cfg["model"]
    S_polls = nl.Settings(w_prev=m["w_prev"], err=m["err"], other=m["other"], draws=m["draws"])
    S_prev = nl.Settings(w_prev=1.0, err=m["err_prev"], other=m["other"], draws=m["draws"])
    rng = np.random.default_rng(cfg["seed"])

    # ---- official results
    v25, seats25, info25 = nl.read_uitslag(NLRAW / "x25" / "TK2025_uitslag.csv")
    v23, seats23, info23 = nl.read_uitslag(NLRAW / "x23" / "TK2023_uitslag.csv")
    ids = list(nl.PARTY_IDS)
    nat25, nat23 = v25.sum(), v23.sum()
    seats = seats25.reindex(ids).fillna(0).astype(int)
    assert seats.sum() == 150
    assert (nl.allocate(nat25.reindex(ids + ["other"]).to_numpy(float), pool=len(ids)) [:-1] == seats.to_numpy()).all()
    rank25 = nl.rank_order(seats.to_numpy(), [nl.LABEL[i] for i in ids])
    winner_id = ids[int(np.argmin(rank25))]
    assert winner_id == "d66", winner_id

    # ---- polls 2025 and the two projections
    page = json.loads((NLRAW / "wiki" / "polls_html_2025.json").read_text())["parse"]
    rev = page["revid"]
    grid = max((t for t in nl.html_tables(page["text"]) if any(c.startswith("Polling") for r in t[:3] for c in r)), key=len)
    polls = nl.parse_polls(grid)
    avg, used = nl.final_poll(polls, nl.ELECTION_DATE[2025], cfg["polls"]["window_days"])
    prev23 = nat23.reindex(ids).astype(float)
    central_polls = nl.central_shares(avg.reindex(ids).fillna(0.0), prev23, S_polls)
    central_prev = nl.central_shares(None, prev23, S_prev)
    sim_polls = nl.simulate_seats(central_polls, S_polls, rng)
    sim_prev = nl.simulate_seats(central_prev, S_prev, rng)

    # ---- markets: outcome from the official seats, probabilities from the two projections
    sp = specs()
    band_lists = {i: [] for i in ids}
    for s in sp:
        if s["kind"] == "band":
            band_lists[s["party"]].append((s["race_id"], s["lo"], s["hi"]))
    label = [nl.LABEL[i] for i in ids]
    rank_sim = {"polls": nl.rank_order(sim_polls, label), "prev": nl.rank_order(sim_prev, label)}
    col = {i: j for j, i in enumerate(ids)}

    def prob(s: dict, which: str) -> float:
        sim = sim_polls if which == "polls" else sim_prev
        j = col[s["party"]]
        if s["kind"] == "most":
            return float((rank_sim[which][:, j] == 0).mean())
        if s["kind"] == "second":
            return float((rank_sim[which][:, j] == 1).mean())
        return float(((sim[:, j] >= s["lo"]) & (sim[:, j] <= s["hi"])).mean())

    def rule_yes(s: dict) -> bool:
        j = col[s["party"]]
        if s["kind"] == "most":
            return bool(rank25[j] == 0)
        if s["kind"] == "second":
            return bool(rank25[j] == 1)
        return bool(s["lo"] <= seats.iloc[j] <= s["hi"])

    races, timeline, settle = [], {}, {}
    kalshi_info, series_k = {}, {}
    grid_t = pd.date_range(START, END, freq="min")
    series = {}
    for s in sp:
        assert rule_yes(s) == s["yes_won"], f"{s['race_id']}: seats say {rule_yes(s)}, Polymarket resolved {s['yes_won']}"
        s["P_polls"], s["P_prev"] = prob(s, "polls"), prob(s, "prev")
        ser = prices(s["slug"], s["token"])
        ser = ser.reindex(ser.index.union(grid_t)).ffill().reindex(grid_t)
        assert ser.notna().all(), s["slug"]
        series[s["race_id"]] = ser
        color = nl.LISTS[s["party"]][2]
        markets = [{"platform": "polymarket", "id": s["slug"], "question": s["question"], "yes_is": "a"}]
        kp = None
        if s["kind"] == "most":
            kser, kmk = kalshi_series(s["party"])
            if kser.notna().any() and float(kmk.get("volume_fp") or 0) > 0:
                kp = kser
                kalshi_info[s["race_id"]] = {"ticker": kmk["ticker"], "result": kmk["result"],
                                             "settlement_value": float(kmk["settlement_value_dollars"]),
                                             "volume": float(kmk["volume_fp"])}
                markets.append({"platform": "kalshi", "id": kmk["ticker"], "yes_is": "a",
                                "question": f"Yes if {s['a']} wins a plurality of seats in the 2025 Dutch general election "
                                            f"(Kalshi {kmk['ticker']}; settled at ${float(kmk['settlement_value_dollars']):.2f})"})
        races.append({
            "race_id": s["race_id"], "name": s["name"], "region": None,
            "a": {"id": "yes", "name": s["a"], "color": color}, "b": {"id": "no", "name": s["b"], "color": "#8a8a8a"},
            "markets": markets, "precount": {"P": round(s["P_prev"], 4), "P_v2": round(s["P_polls"], 4)},
        })
        r4 = ser.round(4)
        keep = (r4 != r4.shift()) | (ser.index == ser.index[-1])
        if kp is not None:
            k4 = kp.round(4)
            keep = keep | ((k4 != k4.shift()) & kp.notna()) | (kp.notna() & ~kp.shift().notna())
        timeline[s["race_id"]] = [{"t": iso(t), "counted": None, "raw": None, "share": None, "sd": None,
                                   "P": round(s["P_prev"], 4), "P_v2": round(s["P_polls"], 4), "pm": round(float(ser[t]), 4),
                                   "kalshi": None if kp is None or pd.isna(kp[t]) else round(float(kp[t]), 4)} for t in ser.index[keep]]
        won = s["yes_won"]
        q_polls = pd.Series(s["P_polls"], index=grid_t)
        sp_model = settle_time(q_polls, won)
        settle[s["race_id"]] = {
            "model": iso(sp_model), "polymarket": iso(settle_time(ser, won)), "kalshi": None, "raw": None,
            "outcome": "yes" if won else "no",
            "polymarket_95": iso(settle_time(ser, won, SETTLE_HI)), "model_95": iso(settle_time(q_polls, won, SETTLE_HI)),
        }
        if kp is not None:
            kq = kp.ffill()
            settle[s["race_id"]]["kalshi"] = iso(settle_time(kq, won)) if kq.notna().all() else None
            settle[s["race_id"]]["kalshi_95"] = iso(settle_time(kq, won, SETTLE_HI)) if kq.notna().all() else None
            series_k[s["race_id"]] = kq

    # ---- scoring: market vs the two projections, by market group
    def brier(group: list[dict], at: pd.Timestamp, which: str) -> float:
        out = []
        for s in group:
            p = float(series[s["race_id"]].loc[at]) if which == "market" else s["P_polls" if which == "polls" else "P_prev"]
            out.append((p - float(s["yes_won"])) ** 2)
        return float(np.mean(out))

    groups = {"most": [s for s in sp if s["kind"] == "most"], "second": [s for s in sp if s["kind"] == "second"],
              "band": [s for s in sp if s["kind"] == "band"], "all": sp}
    score = {"times": [iso(t) for t in SCORE_TIMES],
             "groups": {g: {"n": len(v), "market": [round(brier(v, t, "market"), 4) for t in SCORE_TIMES],
                            "model_polls": round(brier(v, START, "polls"), 4),
                            "model_prev": round(brier(v, START, "prev"), 4)} for g, v in groups.items()}}
    contested = [s for s in groups["most"] if 0.05 <= float(series[s["race_id"]].loc[START]) <= 0.95]

    # ---- units
    gem = sorted(c for c in v25.index if c[0] == "G" and c != "G9010")
    topo = json.loads(GEO.read_text())
    geo_ids = {g["properties"]["unit_id"]: g["properties"]["name"] for g in topo["objects"]["gemeenten"]["geometries"]}
    u_ids = [nl.unit_id(info25.loc[c, "province"], nl.gemeente_code(c)) for c in gem]
    assert set(u_ids) == set(geo_ids) and len(gem) == 342, (len(gem), len(geo_ids))
    two = lambda v: (v["d66"] / (v["d66"] + v["pvv"]).where((v["d66"] + v["pvv"]) > 0))
    a25, a23 = two(v25.loc[gem]), two(v23.loc[gem])
    proj = nl.swing_projection(v23.loc[gem].reindex(columns=ids).fillna(0), nat23.reindex(ids), central_polls)
    a_proj = two(proj)
    i1000 = lambda x: -1 if pd.isna(x) else int(round(1000 * x))
    valid25 = info25.loc[gem, "valid"]
    top2 = v25.loc[gem, ids].apply(lambda r: r.sort_values(ascending=False).index[:2].tolist(), axis=1)
    winner = top2.str[0]
    margin = [(v25.at[c, top2[c][0]] - v25.at[c, top2[c][1]]) / valid25[c] for c in gem]
    units = {
        "unit_ids": u_ids, "region": [nl.PROVINCES[info25.loc[c, "province"]][0] for c in gem],
        "names": [geo_ids[u] for u in u_ids],
        "prior": [i1000(x) for x in a23], "V0": [int(x) for x in (v23.loc[gem, "d66"] + v23.loc[gem, "pvv"])],
        "winner": winner.tolist(), "margin": [i1000(x) for x in margin],
        "a": {"name": "D66", "color": nl.LISTS["d66"][2]}, "b": {"name": "PVV", "color": nl.LISTS["pvv"][2]},
        "frames": [{"t": iso(END), "f": [1000] * len(gem), "a": [i1000(x) for x in a25], "proj": [i1000(x) for x in a_proj],
                    "swing": [-1 if pd.isna(x) or pd.isna(y) else int(round(1000 * (x - y))) for x, y in zip(a25, a23)]}],
    }
    mae = float((a25 - a_proj).abs().mean() * 100)
    mae_prior = float((a25 - a23).abs().mean() * 100)
    d66_g = int((winner == "d66").sum())
    pvv_g = int((winner == "pvv").sum())

    # ---- stats
    nat_share = (nat25.reindex(ids) / nat25.sum()).round(5)
    cal = {r["race_id"]: None for r in races}
    stats = {
        "calibration": cal, "settle": settle, "catchup": {"polymarket": None, "kalshi": None},
        "early_error": {r["race_id"]: None for r in races}, "capacity": {"polymarket": None, "kalshi": None}, "arbitrage": None,
        "accuracy": None, "profit": None,
        "evidence": {"settle": "not-preregistered"},
        "evidence_note": ("Retrospective replay, not pre-registered: the projection's settings were chosen on the 2017, 2021 and 2023 "
                          "elections and scored once on 2025 after the result was known. There is no count history for this "
                          "election, so no count model was run: the page compares market prices over the night with the official "
                          "final result and with a projection made before any votes. The 0.9 and 0.95 settle rules are "
                          "descriptive, chosen after the fact."),
        "reasons": {k: REASON for k in ("calibration", "early_error", "settle.raw", "accuracy", "capacity")}
        | {"settle.model": "the projection is made before any votes, so it does not move during the night; 'model' is the "
                           "first minute its chance of the eventual outcome is at least 0.9 (null when it never was)",
           "catchup.polymarket": REASON + " (catch-up is measured against a count model)",
           "catchup.kalshi": REASON + " (catch-up is measured against a count model)",
           "profit": REASON + " (no count signal to trade on)",
           "settle.kalshi": "Kalshi's 'most seats' markets settled the 26-26 tie as $0.50 each (Polymarket: D66 by alphabetical tie-break), "
                            "so settle.kalshi applies the 0.9 rule to the Polymarket outcome; only the five markets Kalshi quoted have it",
           "arbitrage": "not computed: the venues' tie rules differ (Kalshi split the tie, Polymarket gave it to D66), so D66 and PVV pairs are not "
                        "risk-free, and there is no count over time to trade on",
           "timeline": "count fields are null: " + REASON},
        "outcome": {"seats": {nl.LABEL[i]: int(seats[i]) for i in ids if seats[i]}, "votes_share_pct": {nl.LABEL[i]: round(100 * float(nat_share[i]), 2) for i in ids},
                    "votes": {nl.LABEL[i]: int(nat25[i]) for i in ids}, "valid_votes": int(info25["valid"].sum()),
                    "most_seats": nl.LABEL[winner_id], "tie_note": "PVV and D66 both won 26 seats; Polymarket's rule gives a tie to the "
                    "alphabetically first abbreviation (D66). D66 also had more votes (1,790,634 vs 1,760,966)."},
        "projection": {
            "settings": {"w_prev": m["w_prev"], "err": m["err"], "err_prev": m["err_prev"], "other": m["other"], "draws": m["draws"], "seed": cfg["seed"]},
            "poll_average_seats": {nl.LABEL[i]: round(float(avg.get(i, 0.0)), 2) for i in ids},
            "polls_used": [{"pollster": p.pollster, "end": str(p.end)} for p in used.itertuples()],
            "central_share_pct_polls": {nl.LABEL[i]: round(100 * float(central_polls[i]), 2) for i in ids},
            "central_share_pct_2023": {nl.LABEL[i]: round(100 * float(central_prev[i]), 2) for i in ids},
            "expected_seats_polls": {nl.LABEL[i]: round(float(sim_polls[:, col[i]].mean()), 1) for i in ids},
            "expected_seats_2023": {nl.LABEL[i]: round(float(sim_prev[:, col[i]].mean()), 1) for i in ids},
            "p_most_seats_polls": {nl.LABEL[i]: round(float((rank_sim["polls"][:, col[i]] == 0).mean()), 3) for i in ids},
            "p_most_seats_2023": {nl.LABEL[i]: round(float((rank_sim["prev"][:, col[i]] == 0).mean()), 3) for i in ids},
            "map_mae_pts": {"swing_projection": round(mae, 2), "2023_result": round(mae_prior, 2)},
            "map_note": "Mean absolute error over the 342 gemeenten of the D66 share of D66 + PVV votes, in points: the 2023 gemeente result "
                        "scaled by the polling swing vs the 2023 result unchanged.",
        },
        "score": score,
        "market_behaviour": {
            "settle_threshold": SETTLE, "polymarket_settle": {r: v["polymarket"] for r, v in settle.items()},
            "price_at_start": {r: round(float(series[r].loc[START]), 4) for r in series},
            "price_at_poll_close": {r: round(float(series[r].loc[POLL_CLOSE]), 4) for r in series},
            "contested_most_seats_markets": [s["race_id"] for s in contested],
            "kalshi": {r: {**v, "price_at_start": round(float(series_k[r].loc[START]), 4) if r in series_k else None,
                           "price_at_poll_close": round(float(series_k[r].loc[POLL_CLOSE]), 4) if r in series_k else None}
                       for r, v in kalshi_info.items()},
            "note": "Prices are the 1-minute YES price history of Polymarket, and for the 'most seats' markets Kalshi's (bid/ask midpoint of the "
                    "1-minute candle when the spread is 10 cents or less, else the last trade). The exit poll (Ipsos I&O, published 21:00 CET = "
                    "20:00 UTC) is the first public information of the night.",
        },
    }
    mk = {s["race_id"]: s for s in sp}

    # ---- meta
    sources = [
        {"name": "Kiesraad, Verkiezingsuitslag Tweede Kamer 2025 (CSV; list votes by gemeente, seats), as imported on verkiezingsuitslagen.nl",
         "url": "https://data.overheid.nl/dataset/verkiezingsuitslag-tweede-kamer-2025", "licence": "CC0 1.0 (data.overheid.nl)"},
        {"name": "Kiesraad, Verkiezingsuitslag Tweede Kamer 2023 (CSV; the prior), and 2012, 2017, 2021 (calibration)",
         "url": "https://data.overheid.nl/dataset/verkiezingsuitslag-tweede-kamer-2023", "licence": "CC0 1.0 (data.overheid.nl)"},
        {"name": "Gemeente boundaries 2025 (gegeneraliseerd), CBS / Kadaster Gebiedsindelingen via PDOK, simplified",
         "url": "https://service.pdok.nl/cbs/gebiedsindelingen/2025/wfs/v1_0", "licence": "CC BY 4.0 (CBS, Kadaster)"},
        {"name": "Final poll averages: English Wikipedia, Opinion polling for the 2025 Dutch general election (and the 2017, 2021, 2023 pages for the "
                 f"settings); revision {rev} of the 2025 page. Polls by Ipsos I&O, Peil.nl, Verian",
         "url": "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2025_Dutch_general_election", "licence": "CC BY-SA 4.0"},
        {"name": "Polymarket price history (CLOB prices-history, 1-minute) and market rules (Gamma API)",
         "url": f"https://polymarket.com/event/{MOST_EVENT}", "licence": "Polymarket public data"},
        {"name": f"Kalshi candlesticks (historical tier) of the 'Who will win the Dutch general election?' markets ({KALSHI_EVENT}), most seats",
         "url": f"https://kalshi.com/markets/kxpartywinnl", "licence": "Kalshi public market data"},
    ]
    tie_note = (
        "Polymarket and Kalshi prices split after about 21:00 UTC because their contracts treat a seat tie differently. "
        "Polymarket gives a tie to the alphabetically first party (D66), so a D66 share paid $1. Kalshi splits a tie, paying $0.50 to each tied party. As a 26-26 tie became likely, Polymarket's D66 price rose toward $1 and Kalshi's D66 and PVV prices moved toward $0.50. Both were consistent with their own rules; the gap is not a mispricing."
    )
    notes = [
        tie_note,
        "RETROSPECTIVE replay, not pre-registered. No count history: market vs outcome only. Nothing official or openly licensed "
        "recorded the Dutch count by gemeente during the night, so the page shows Polymarket prices against the official final "
        "result and a projection made before any votes; there is no minute-by-minute count model, accuracy or profit result.",
        "Count sources checked for 2025-10-29 20:00 to 2025-10-30 12:00 UTC: the Kiesraad results site verkiezingsuitslagen.nl has no "
        "archived election page before 2025-11-14; NOS's results app (app.nos.nl/nieuws/tk2025) was captured about 11 times but its "
        "pages load the numbers by script and the archived copies say 'Wachten op uitslagen' (waiting for results); municipal sites "
        "publish their own results only.",
        f"Prices: Polymarket 1-minute YES price, {iso(START)} to {iso(END)} (19:00 to 13:00 CET); a row is written only when the price "
        f"moves (plus the last minute); Kalshi's 1-minute prices for the {len(kalshi_info)} 'most seats' markets it quoted (D66, PVV, GL/PvdA, VVD, CDA). Settled = the eventual outcome's price stays >= {SETTLE} from then to the end of the window "
        f"(a 0.95 version is in stats.settle). Kalshi settled D66 and PVV at $0.50 each (a tie split, scalar); Polymarket gave the tie to D66; "
        "settle.kalshi applies the same 0.9 rule to the Polymarket outcome.",
        "Races: each Polymarket question is its own two-way race, A = YES. Most seats (15 parties) and second most seats (6) follow the "
        "market rule: most seats, a tie going to the alphabetically first abbreviation; six parties' seat-band markets are scored too.",
        f"Projection (before any votes): 'without polls' = the 2023 result (shares of the 16 lists with a seat) with a share error "
        f"sd = {m['err_prev']} x sqrt(b(1-b)); 'with polls' (the model line) = the average of each pollster's last poll ending 1-7 days "
        f"before the vote (seats/150), error sd = {m['err']} x sqrt(b(1-b)). Both go through the Tweede Kamer seat allocation (D'Hondt "
        f"among lists with a full quota; reproduces the official 2017-2025 seats) in {m['draws']:,} simulated elections. Settings were "
        "chosen on 2017, 2021 and 2023 only (configs/nl2025_v1.toml); they are not frozen in the repository before scoring.",
        f"Result: D66 {int(seats['d66'])} seats, PVV {int(seats['pvv'])}, VVD {int(seats['vvd'])}, GL/PvdA {int(seats['glpvda'])}, CDA "
        f"{int(seats['cda'])}, JA21 {int(seats['ja21'])}. D66 and PVV tied on 26 seats; the 'most seats' markets resolved to D66 by the "
        "alphabetical tie-break (D66 also had the most votes).",
        "Map: one final frame (its time is the end of the price window; the Kiesraad publishes the official result later). Colour = D66 "
        "share of D66 + PVV votes by gemeente; prior = the same in 2023; the frame's 'proj' = the 2023 gemeente result scaled by the polling "
        f"swing (mean absolute error {mae:.1f} pts against {mae_prior:.1f} for the 2023 result unchanged); 'swing' = change in the share x1000. "
        "342 gemeenten; Bonaire, Sint Eustatius, Saba and the voters abroad are in the national totals but not on the map.",
        f"Of the 342 gemeenten on the map, PVV had the most votes in {pvv_g} and D66 in {d66_g}; D66 won the national vote through the cities.",
    ]
    meta = {
        "id": "nl2025", "name": "Netherlands, Tweede Kamer election", "date": "2025-10-29", "tz": "Europe/Amsterdam",
        "poll_close_utc": iso(POLL_CLOSE), "geometry": "geo/nl_gemeenten.topo.json", "geometry_object": "gemeenten", "unit_key": "unit_id",
        "count_timeline": False,
        "count_note": "Venues settle ties differently: Polymarket gave the 26-26 tie to D66 (alphabetical), Kalshi paid $0.50 to D66 and to PVV, "
                      "so their D66 and PVV prices diverged after about 21:00 UTC; neither is mispriced (first note below). No count history: market vs outcome only. Nothing official or openly licensed recorded the count by gemeente during the "
                      "night, so the chart shows market prices, and the model line is a projection made before any votes (not a count "
                      "model). The map shows the official final results. Retrospective, not pre-registered.",
        "regions": [{"id": k, "name": n} for k, n in nl.PROVINCES.values()],
        "parties": [{"id": i, "name": nl.LABEL[i], "color": nl.LISTS[i][2]} for i in ids] + [{"id": "other", "name": "Other", "color": "#8a8a8a"}],
        "races": races,
        "map": {"prior": "D66 share of D66 + PVV votes, 2023 result by gemeente (Kiesraad)", "final": "official 2025 results (Kiesraad)"},
        "sources": sources, "notes": notes,
    }
    dump(OUT / "meta.json", meta)
    dump(OUT / "timeline.json", timeline)
    dump(OUT / "units.json", units)
    dump(OUT / "stats.json", stats)

    main_r = settle["most-d66"]
    pm90 = main_r["polymarket"]
    pm_d66 = series["most-d66"]
    low = pm_d66.loc[POLL_CLOSE:].min()
    t_low = pm_d66.loc[POLL_CLOSE:].idxmin()
    summary = (f"Retrospective, not pre-registered; no count history exists, so this compares market prices with the final result only. "
               f"Polymarket's D66 'most seats' price was {pm_d66.loc[START]:.2f} at {START:%H:%M} UTC, {pm_d66.loc[POLL_CLOSE]:.2f} when the exit poll "
               f"came out at {POLL_CLOSE:%H:%M}, fell to {low:.2f} by {t_low:%H:%M} UTC and stayed above 0.9 only from {pm90[11:16]} UTC; the result was a "
               f"26-26 tie with PVV that D66 won on the alphabetical tie-break. A projection from the final polls made before any votes gave "
               f"D66 {mk['most-d66']['P_polls']:.0%} and PVV {mk['most-pvv']['P_polls']:.0%}; from the 2023 result alone, D66 "
               f"{mk['most-d66']['P_prev']:.1%}.")
    entry = {"id": "nl2025", "name": "Netherlands, Tweede Kamer election", "date": "2025-10-29", "country": "NL", "status": "replay",
             "unit_level": "gemeente", "n_units": len(gem), "evidence": "not-preregistered", "summary": summary}
    (OUT / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(stats["projection"], indent=1)[:3000])
    print(json.dumps(score, indent=1))
    print("settle most-*:", {r: (v["polymarket"], v["model"], v["outcome"]) for r, v in settle.items() if r.startswith("most-")})
    print(summary)


if __name__ == "__main__":
    main()
