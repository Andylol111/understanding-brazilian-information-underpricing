"""H4: who traded the runoff, and does the flow look like manipulation or like information?

Combines the 'Lula wins' and 'Flávio wins' winner markets into one Lula-YES frame (Flávio YES at q
≡ Lula YES sold at 1-q; in the runoff they are complements). Windows:
  between rounds  2026-10-05 00:00 UTC → 2026-10-25 20:00 UTC (polls close)
  count night     2026-10-25 22:00 UTC (publication resumes) → 2026-10-26 02:30 UTC

Prereq: brunder pm-download brazil-presidential-election (trades with is_taker)
Run:    uv run python analysis/flows.py
"""

from __future__ import annotations

import pandas as pd

from brunder import microstructure as ms
from brunder.paths import POLYMARKET, ROOT

EV = POLYMARKET / "brazil-presidential-election"
LULA = "will-luiz-incio-lula-da-silva-win-the-2026-brazilian-presidential-election"
FLAVIO = "will-flvio-bolsonaro-win-the-2026-brazilian-presidential-election"
OUTCOME = 1  # Lula won
WINDOWS = {
    "between_rounds": (pd.Timestamp("2026-10-05", tz="UTC"), pd.Timestamp("2026-10-25 20:00", tz="UTC")),
    "count_night": (pd.Timestamp("2026-10-25 22:00", tz="UTC"), pd.Timestamp("2026-10-26 02:30", tz="UTC")),
}
OUT = ROOT / "results"


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    mk = pd.read_parquet(EV / "markets.parquet").set_index("slug")
    frames = []
    for slug, inv in ((LULA, False), (FLAVIO, True)):
        t = pd.read_parquet(EV / "trades" / f"{slug}.parquet")
        if "is_taker" not in t:
            raise SystemExit(f"{slug}: trades lack is_taker; rerun brunder pm-download")
        frames.append(ms.to_yes(t, str(mk.loc[slug, "yes_token"]), invert=inv).assign(market=slug))
    px = pd.read_parquet(EV / "prices" / f"{LULA}.parquet")
    return pd.concat(frames, ignore_index=True).sort_values("time"), px


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pd.set_option("display.width", 220, "display.max_columns", 20)
    t, px = load()
    summary = []
    for name, (a, b) in WINDOWS.items():
        w = t[(t["time"] >= a) & (t["time"] < b)]
        tk = w[w["is_taker"]]
        conc = ms.concentration(w).iloc[0]
        flows = ms.wallet_flows(w, OUTCOME)
        flows.to_csv(OUT / f"flows_wallets_{name}.csv")
        losers = flows[flows["pnl"] < 0]["pnl"]
        winners = flows[flows["pnl"] > 0]["pnl"]
        st = ms.self_trades(w)
        rt = ms.round_trips(w, "10min")
        imp = ms.price_impact(w, px)
        imp.to_csv(OUT / f"flows_impact_{name}.csv")
        summary.append({
            "window": name,
            "taker_volume_usd": tk["notional"].sum(),
            "wallets": int(conc["wallets"]),
            "hhi": conc["hhi"], "top10_share_of_fills": conc["top10_share"],
            "net_taker_flow_lula_usd": (tk["sign"] * tk["notional"]).sum(),
            "losers_total_usd": losers.sum(),
            "top10_losers_share": losers.nsmallest(10).sum() / losers.sum() if len(losers) else float("nan"),
            "top10_winners_share": winners.nlargest(10).sum() / winners.sum() if len(winners) else float("nan"),
            "self_trade_shares": st["washed_shares"].sum() if len(st) else 0.0,
            "self_trade_share_of_volume": (st["washed_shares"].sum() / w["size"].sum()) if len(st) else 0.0,
            "round_trips_10min": len(rt),
            "round_trip_share_of_volume": (2 * rt["size"].sum() / w["size"].sum()) if len(rt) else 0.0,
            **{f"lambda_{h}": imp.loc[h, "lambda_per_$10k"] for h in imp.index},
            **{f"t_{h}": imp.loc[h, "t"] for h in imp.index},
        })
        print(f"\n== {name}: top 10 wallets by gross notional (Lula-YES frame; pnl held to resolution)")
        print(flows.head(10)[["trades", "gross_notional", "net_yes_shares", "vwap_yes", "pnl"]].round(3).to_string())
        print(f"   biggest losers:")
        print(flows.nsmallest(5, "pnl")[["trades", "gross_notional", "net_yes_shares", "vwap_yes", "pnl"]].round(3).to_string())
        print("   price impact of taker flow (Δp per $10k net Lula-buying in a minute):")
        print(imp.round(4).to_string())

        daily = ms.concentration(w, by="1D") if name == "between_rounds" else ms.concentration(w, by="30min")
        daily.to_csv(OUT / f"flows_concentration_{name}.csv")

    s = pd.DataFrame(summary).set_index("window").T
    s.to_csv(OUT / "flows_summary.csv")
    print("\n== summary")
    print(s.to_string())


if __name__ == "__main__":
    main()
