import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import arb

PAIRS = Path(__file__).resolve().parents[1] / "configs" / "pairs"


# ---------------------------------------------------------------------------------------------- Kalshi fees
# Published examples (Kalshi fee schedule, 0.07 coefficient, 100 contracts): $0.07 at 1¢, $0.63 at 10¢, $1.75 at 50¢.

@pytest.mark.parametrize("price,fee", [(0.01, 0.07), (0.10, 0.63), (0.50, 1.75), (0.90, 0.63), (0.99, 0.07)])
def test_kalshi_fee_matches_published_table(price, fee):
    assert arb.kalshi_fee(price, 100) == pytest.approx(fee)


def test_kalshi_fee_rounds_up_to_the_cent_for_non_direct_members():
    assert arb.kalshi_fee(0.50, 1) == pytest.approx(0.02)          # 0.0175 → 2¢
    assert arb.kalshi_fee(0.01, 1) == pytest.approx(0.01)          # 0.000693 → 1¢
    assert arb.kalshi_fee(0.50, 1, precision=0.0001) == pytest.approx(0.0175)   # direct member: 0.01¢ grid


def test_kalshi_fee_multiplier_and_maker_rate():
    assert arb.kalshi_fee(0.50, 100, multiplier=0) == 0.0
    assert arb.kalshi_fee(0.50, 100, multiplier=0.5) == pytest.approx(0.88)   # 0.875 → 88¢
    assert arb.kalshi_fee(0.50, 100, rate=arb.K_MAKER_RATE) == pytest.approx(0.44)   # 0.4375 → 44¢


def test_kalshi_fee_on_a_walked_order_is_rounded_once():
    # 50 @ 0.40 + 50 @ 0.41: model fee 0.07·(50·0.24 + 50·0.2419) = 1.68665 → balance change rounded up → 1.69
    assert arb.kalshi_fee_levels([(0.40, 50), (0.41, 50)]) == pytest.approx(1.69)
    # one contract each at 0.30 and 0.31: 0.0147 + 0.0150 = 0.0297 → 3¢ once, 2¢ + 2¢ if rounded per fill
    assert arb.kalshi_fee_levels([(0.30, 1), (0.31, 1)]) == pytest.approx(0.03)
    assert arb.kalshi_fee(0.30, 1) + arb.kalshi_fee(0.31, 1) == pytest.approx(0.04)


# ---------------------------------------------------------------------------------------------- Polymarket fees
# docs.polymarket.com/trading/fees, 100-share tables: Politics (0.04) $1.00 at 50¢, $0.84 at 30¢;
# Crypto (0.07) $1.75 at 50¢, $0.33 at 5¢; Sports (0.05) $1.25 at 50¢.

@pytest.mark.parametrize("rate,price,fee", [(0.04, 0.50, 1.00), (0.04, 0.30, 0.84), (0.07, 0.50, 1.75),
                                            (0.07, 0.05, 0.33), (0.05, 0.50, 1.25), (0.04, 0.99, 0.04)])
def test_polymarket_fee_matches_published_table(rate, price, fee):
    assert arb.pm_fee(price, 100, rate) == pytest.approx(fee, abs=0.005)    # tables show cents


def test_polymarket_fee_exact_formula_and_zero_rate():
    assert arb.pm_fee(0.30, 100, 0.04) == pytest.approx(100 * 0.04 * 0.30 * 0.70)
    assert arb.pm_fee(0.30, 100, 0.0) == 0.0
    assert arb.pm_fee(0.0001, 1, 0.04) == 0.0                            # below 0.00001 USDC rounds to zero


# ---------------------------------------------------------------------------------------------- walks

def _pair(same_side=True, pm_rate=0.0, k_mult=1.0):
    return arb.Pair(id="x", race_id="r", proposition="A", pm_market="m", kalshi_ticker="K", same_side=same_side,
                    pm_fee_rate=pm_rate, kalshi_fee_multiplier=k_mult)


def test_walk_pair_stops_when_the_marginal_unit_stops_paying():
    pm_asks, k_asks = [(0.40, 100)], [(0.55, 50), (0.59, 100)]
    no_fee = arb.walk_pair(pm_asks, k_asks, None, fee_b=arb.Fee("kalshi", 0))
    assert no_fee["units"] == pytest.approx(100)                         # 0.95 and 0.99 both < $1
    assert no_fee["net"] == pytest.approx(100 - (40 + 27.5 + 29.5))
    fee = arb.walk_pair(pm_asks, k_asks, None)                           # 0.99 + 0.07·0.59·0.41 > $1
    assert fee["units"] == pytest.approx(50)
    assert fee["fee_b"] == pytest.approx(0.87)                           # 0.86625 → 87¢
    assert fee["cost"] == pytest.approx(20 + 27.5 + 0.87)
    assert fee["net"] == pytest.approx(50 - 48.37)


def test_walk_pair_fixed_size_buys_through_unprofitable_levels():
    r = arb.walk_pair([(0.40, 100)], [(0.55, 50), (0.59, 100)], 80, fee_b=arb.Fee("kalshi", 0))
    assert r["units"] == pytest.approx(80) and r["cost"] == pytest.approx(32 + 27.5 + 30 * 0.59)


def test_book_arb_uses_the_right_complementary_legs():
    pm_book = {"asks": [{"price": "0.40", "size": "100"}], "bids": [{"price": "0.38", "size": "100"}]}
    # Kalshi YES bids 0.50 (→ NO ask 0.50), NO bids 0.45 (→ YES ask 0.55)
    k = {"yes_asks": [(0.55, 100)], "no_asks": [(0.50, 100)], "yes_bids": [(0.50, 100)], "no_bids": [(0.45, 100)]}
    same = arb.book_arb(_pair(True, k_mult=0), pm_book, k)
    assert same["combo"] == "pm YES + k NO" and same["avg_cost"] == pytest.approx(0.90)
    opp = arb.book_arb(_pair(False, k_mult=0), pm_book, k)               # K YES ≡ PM NO
    assert opp["combo"] == "pm YES + k YES" and opp["avg_cost"] == pytest.approx(0.95)


def test_minute_arb_costs_and_fills_quiet_minutes():
    t0 = pd.Timestamp("2026-10-25T21:00Z")
    pm = pd.DataFrame({"time": [t0, t0 + pd.Timedelta("2min")], "price": [0.40, 0.60]})
    kc = pd.DataFrame({"time": [t0], "yes_bid": [0.50], "yes_ask": [0.52], "price": [0.51], "volume": [1.0],
                       "open_interest": [1.0]})
    m = arb.minute_arb(_pair(True, k_mult=0), pm, kc, 0.01, t0, t0 + pd.Timedelta("2min"))
    assert len(m) == 3
    # minute 0: pm YES ask 0.41 + k NO ask 0.50 = 0.91; pm NO ask 0.61 + k YES ask 0.52 = 1.13
    assert m.loc[0, "cost_a"] == pytest.approx(0.91) and m.loc[0, "combo"] == "pm YES + k NO"
    assert m.loc[1, "cost_a"] == pytest.approx(0.91)                     # both forward-filled
    assert m.loc[2, "cost_a"] == pytest.approx(min(0.61 + 0.50, 0.41 + 0.52))
    assert m.loc[0, "net_edge"] == pytest.approx(0.09)


def test_fillable_walks_traded_prices_on_both_venues():
    t0 = pd.Timestamp("2026-10-25T21:00:10Z")
    pm = pd.DataFrame({"time": [t0, t0], "asset": ["Y", "N"], "side": ["BUY", "BUY"],
                       "price": [0.40, 0.65], "size": [30.0, 10.0]})
    k = pd.DataFrame({"time": [t0], "price": [0.45], "count": [20.0], "taker_side": ["no"], "trade_id": ["1"]})
    f = arb.fillable(_pair(True, k_mult=0), pm, k, "Y")
    # PM YES traded at 0.40 (30), Kalshi NO traded at 1 − 0.45 = 0.55 (20): 20 units at 0.95
    assert len(f) == 1 and f.loc[0, "combo"] == "pm YES + k NO"
    assert f.loc[0, "units"] == pytest.approx(20) and f.loc[0, "net_usd"] == pytest.approx(1.0)


def test_fee_legs_charge_each_venue_its_own_way():
    assert arb.Fee("polymarket", 0.04).total([(0.5, 100)]) == pytest.approx(1.00)
    assert arb.Fee("kalshi", 1.0).total([(0.5, 100)]) == pytest.approx(1.75)
    assert arb.Fee("kalshi", 1.0).unit(0.5) == pytest.approx(0.0175)
    p = _pair(pm_rate=0.04, k_mult=0.5)
    assert p.pm_fee_model() == arb.Fee("polymarket", 0.04) and p.k_fee_model() == arb.Fee("kalshi", 0.5)


def test_polymarket_only_complement_of_a_two_candidate_race():
    t0 = pd.Timestamp("2026-10-25T21:00Z")
    a = pd.DataFrame({"time": [t0], "price": [0.40]})
    b = pd.DataFrame({"time": [t0], "price": [0.55]})
    m = arb.pm_complement(a, b, arb.Fee("polymarket"), arb.Fee("polymarket"), 0.01, t0, t0 + pd.Timedelta("1min"))
    # YES A 0.41 + YES B 0.56 = 0.97 < NO A 0.61 + NO B 0.46 = 1.07
    assert len(m) == 2 and m.loc[0, "combo"] == "YES A + YES B" and m.loc[0, "cost_a"] == pytest.approx(0.97)
    trades_a = pd.DataFrame({"time": [t0], "asset": ["A"], "side": ["BUY"], "price": [0.40], "size": [10.0]})
    trades_b = pd.DataFrame({"time": [t0], "asset": ["B"], "side": ["BUY"], "price": [0.58], "size": [5.0]})
    w = arb.walk_trades(arb.trade_levels_pm(trades_a, "yes", "A"), arb.trade_levels_pm(trades_b, "yes", "B"),
                        arb.Fee("polymarket", 0.04), arb.Fee("polymarket", 0.04))
    fee = 5 * 0.04 * (0.40 * 0.60 + 0.58 * 0.42)
    assert w.loc[0, "units"] == pytest.approx(5) and w.loc[0, "net_usd"] == pytest.approx(5 - 4.9 - fee)


# ---------------------------------------------------------------------------------------------- matching

def test_names_match_across_venues():
    assert arb.same_name("Lula", "Luiz Inácio Lula da Silva")
    assert arb.same_name("James Talarico (D)", "James Talarico")
    assert arb.same_name("Sen. Dan Sullivan", "Dan Sullivan")
    assert arb.same_name("Flavio Bolsonaro", "Flávio Bolsonaro")
    assert not arb.same_name("Dan Sullivan", "Daniel J. Sullivan Jr.")
    assert not arb.same_name("Jair Bolsonaro", "Flávio Bolsonaro")


def test_match_outcomes_is_one_to_one_and_drops_ambiguous_names():
    pm = ["Lula", "Bolsonaro", "Renan Santos", "PSOE"]
    k = ["Luiz Inácio Lula da Silva", "Jair Bolsonaro", "Flávio Bolsonaro", "Renan Santos",
         "Spanish Socialist Workers' Party"]
    got = dict(arb.match_outcomes(pm, k, aliases={"PSOE": "Spanish Socialist Workers' Party"}))
    assert got == {0: 0, 2: 3, 3: 4}                                     # 'Bolsonaro' is ambiguous


@pytest.mark.parametrize("path", sorted(PAIRS.glob("*.toml")), ids=lambda p: p.stem)
def test_pairs_files_are_consistent(path):
    head, pairs = arb.load_pairs(path)
    raw = tomllib.loads(path.read_text())
    assert head["election"] == path.stem and pairs
    assert len({p.id for p in pairs}) == len(pairs)
    for p, r in zip(pairs, raw["pair"]):
        assert r["race_id"] in raw["races"]
        assert p.pm_market and p.kalshi_ticker and p.pm_yes_token and p.pm_rules and p.kalshi_resolution
        assert p.kalshi_url.startswith("https://kalshi.com/markets/")
        assert "kalshi_rules" not in r and "kalshi_title" not in r          # no Kalshi text in git
        assert (p.pm_yes_is == p.kalshi_yes_is) == p.same_side or "other" in (p.pm_yes_is, p.kalshi_yes_is)
        assert p.risk_free is False and p.resolution_differences
        assert np.isfinite(p.pm_fee_rate) and p.kalshi_fee_multiplier >= 0
