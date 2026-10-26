import numpy as np

from brunder import live

BOOK = {"bids": [{"price": "0.10", "size": "100"}, {"price": "0.40", "size": "50"}],
        "asks": [{"price": "0.90", "size": "100"}, {"price": "0.60", "size": "10"}],
        "last_trade_price": "0.45"}


def test_price_is_mid_when_tight_else_last_trade():
    tight = {**BOOK, "asks": [{"price": "0.44", "size": "5"}]}
    assert np.isclose(live.top(tight)["price"], 0.42)
    assert live.top(BOOK)["price"] == 0.45          # 20¢ spread → displayed price is the last trade


def test_walk_consumes_best_levels_first_and_charges_fee():
    w = live.walk(BOOK, side_yes=True, stake_usd=5.0, fee_rate=0.0)          # fits in the 0.60 level
    assert np.isclose(w["avg_cost"], 0.60) and np.isclose(w["shares"], 5 / 0.60)
    f = live.walk(BOOK, side_yes=True, stake_usd=5.0, fee_rate=0.07)
    assert np.isclose(f["avg_cost"], 0.60 + 0.07 * 0.60 * 0.40)
    deep = live.walk(BOOK, side_yes=True, stake_usd=15.0, fee_rate=0.0)      # 10 @ 0.60 then 0.90
    assert np.isclose(deep["shares"], 10 + 9.0 / 0.90)


def test_buying_no_walks_yes_bids_at_one_minus_price():
    w = live.walk(BOOK, side_yes=False, stake_usd=30.0 + 9.0, fee_rate=0.0)
    # best NO price is 1 − 0.40 = 0.60 for 50 shares ($30), then 0.90 for the rest
    assert np.isclose(w["shares"], 50 + 9.0 / 0.90)
    assert np.isclose(w["filled_fraction"], 1.0)


def test_walk_reports_partial_fill_when_book_is_thin():
    w = live.walk({"asks": [{"price": "0.50", "size": "4"}], "bids": []}, True, 100.0, 0.0)
    assert np.isclose(w["spent"], 2.0) and np.isclose(w["filled_fraction"], 0.02)
