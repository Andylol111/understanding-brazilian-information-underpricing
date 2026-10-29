"""Wallet-flow metrics (analysis/us_wallets.py) and the published site/data/us2026/hub/wallets.json."""

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("us_wallets", ROOT / "analysis" / "us_wallets.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)
OUT = ROOT / "site" / "data" / "us2026" / "hub" / "wallets.json"


def _fills(rows):
    return pd.DataFrame(rows, columns=["proxyWallet", "asset", "side", "size", "price"])


def test_yes_flow_treats_no_as_short_yes():
    f = _fills([("a", "Y", "BUY", 10, 0.6), ("b", "N", "BUY", 10, 0.4), ("c", "Y", "SELL", 10, 0.6),
                ("d", "N", "SELL", 10, 0.4)])
    assert W.yes_flow(f, "Y").tolist() == pytest.approx([6, -6, -6, 6])


def test_realised_pnl_is_zero_sum_and_split_invariant():
    # a buys YES from b at 0.3; c (NO at 0.4) and d (YES at 0.6) are matched by minting a full set; YES wins
    f = _fills([("a", "Y", "BUY", 100, 0.3), ("b", "Y", "SELL", 100, 0.3),
                ("c", "N", "BUY", 50, 0.4), ("d", "Y", "BUY", 50, 0.6)])
    pnl = W.realised_pnl(f, {"Y": 1.0, "N": 0.0})
    assert pnl["a"] == pytest.approx(70) and pnl["b"] == pytest.approx(-70)
    assert pnl["c"] == pytest.approx(-20) and pnl["d"] == pytest.approx(20)
    assert pnl.sum() == pytest.approx(0)
    # selling YES shares obtained by a split (not in fills) = buying NO at 1-p: same PnL
    s = W.realised_pnl(_fills([("e", "Y", "SELL", 10, 0.3)]), {"Y": 0.0, "N": 1.0})["e"]
    n = W.realised_pnl(_fills([("e", "N", "BUY", 10, 0.7)]), {"Y": 0.0, "N": 1.0})["e"]
    assert s == pytest.approx(n) == pytest.approx(3)


def test_concentration_extremes():
    assert W.concentration(np.ones(100))["top10_share"] == pytest.approx(0.1)
    assert W.concentration(np.ones(100))["hhi"] == pytest.approx(100)
    one = W.concentration(np.array([5.0]))
    assert one["top10_share"] == 1 and one["hhi"] == 10_000
    # a larger outstanding supply than the listed holders dilutes the shares
    assert W.concentration(np.array([5.0]), total=10)["top10_share"] == pytest.approx(0.5)


def test_bucket_edges():
    assert W.bucket(pd.Series([0, 99.99, 100, 999, 1000, 1e6])).tolist() == \
        ["small", "small", "medium", "medium", "large", "large"]


def test_kyle_lambda_recovers_slope():
    rng = np.random.default_rng(0)
    flow = rng.normal(size=2000)
    dp = 0.002 * flow + rng.normal(scale=0.001, size=2000)
    res = W.kyle_lambda(dp, flow)
    assert res["lambda_per_1k"] == pytest.approx(0.002, rel=0.05) and res["t"] > 20


def test_lead_lag_detects_leading_flow():
    rng = np.random.default_rng(1)
    flow = rng.normal(size=3000)
    dp = np.r_[0, 0.5 * flow[:-1]] + rng.normal(scale=0.5, size=3000)
    ll = W.lead_lag(flow, dp)
    assert ll["lead"] > 0.5 and abs(ll["follow"]) < 0.1


def test_offset_logit_finds_signal_beyond_price_and_none_when_absent():
    rng = np.random.default_rng(2)
    n = 600
    p = rng.uniform(0.2, 0.8, n)
    x = rng.normal(size=n)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-(np.log(p / (1 - p)) + 1.0 * x)))).astype(float)
    res = W.offset_logit(y, p, x)
    assert res["beta"] == pytest.approx(1.0, abs=0.3) and res["p_value"] < 0.01
    assert res["loo_logloss_with_flow"] < min(res["loo_logloss_price_only"], res["loo_logloss_price_intercept"])
    y0 = (rng.uniform(size=n) < p).astype(float)
    assert W.offset_logit(y0, p, x)["p_value"] > 0.001


# ------------------------------------------------------------------ published file

@pytest.fixture(scope="module")
def data():
    if not OUT.exists():
        pytest.skip("wallets.json not built")
    return json.loads(OUT.read_text())


def test_wallets_json_compact_and_private(data):
    assert OUT.stat().st_size < 2e6
    assert not re.search(r"0x[0-9a-fA-F]{40}", OUT.read_text()), "no wallet addresses may be published"


def test_wallets_json_race_metrics_are_coherent(data):
    races = data["races"]
    assert len(races) >= 50
    for k, r in races.items():
        if "flow" not in r:
            continue
        assert r["wallets_30d"] <= r["wallets_all"]
        for side in ("holders_yes", "holders_no"):
            h = r.get(side)
            if h and h["top10_share"] is not None:
                assert 0 < h["top10_share"] <= 1 and 0 < h["hhi"] <= 10_000
        assert set(r["flow"]) <= {"small", "medium", "large"}
        for b in r["flow"].values():
            assert abs(b["net"]) <= b["gross"] + 1
        assert 0 <= r["seasoned"]["gross_share"] <= 1
        for row in data["series"][k]:
            assert len(row) == 7 and 0 <= row[1] <= 1 and row[6] >= 0


def test_historical_test_is_reported(data):
    h = data["historical_2024"]
    assert h["logit_all"]["n"] == len(h["markets"]) >= 30
    assert h["seasoning"]["seasoned"] > 0
    assert pd.Timestamp(h["window"][1]) < pd.Timestamp("2024-11-05 23:00", tz="UTC")
