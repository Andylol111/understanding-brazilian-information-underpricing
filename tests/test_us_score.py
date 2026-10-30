"""analysis/us_score.py: end to end on a replayed night (analysis/us_night_replay.py feed → brunder.us.export → score),
plus the Senate-control rule on small synthetic inputs."""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import us_night_replay as R  # noqa: E402
import us_score as S  # noqa: E402

from brunder import us  # noqa: E402

REPLAYED = {"ga-sen": "ga_sen_2022", "ga-gov": "ga_gov_2022", "tx-sen": "tx_sen_2024", "tx-gov": "tx_gov_2022",
            "nc-sen": "nc_sen_2022", "fl-sen": "fl_sen_2022", "fl-gov": "fl_gov_2022"}


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    w = tmp_path_factory.mktemp("usnight")
    snap, out = w / "snap", w / "out"
    R.feed(snap, minutes=0.01, night=60)
    shutil.copytree(ROOT / "site" / "data" / "us2026", out / "us2026")
    us.export(R.CONFIGS, out, snap)
    p = S.run(out, snap, R.CONFIGS, fees={}, cert_path=w / "none.csv", cert_counties_path=w / "none_c.csv")
    return {"w": w, "snap": snap, "out": out, "score": json.loads(p.read_text())}


def _final_share(src: str) -> float:
    pr = us.load_priors()
    g = pr[pr["source"] == src]
    return float(g["dem"].sum() / (g["dem"] + g["rep"]).sum())


def test_provisional_outcome_is_the_final_feed_snapshot(night):
    s = night["score"]
    assert s["outcome_status"] == "provisional" and s["reading"].startswith("monitoring")
    for rid, src in REPLAYED.items():
        o = s["races"][rid]["outcome"]
        assert o["status"] == "provisional" and "not certified" in o["source"]
        assert abs(o["share_a"] - _final_share(src)) < 1e-4, rid
    for rid in ("ca-gov", "oh-sen", "sc-gov", "vt-gov"):              # no feed in the replay: not scored, said why
        assert s["races"][rid]["outcome"]["status"] == "none" and s["races"][rid]["versions"] == {}


def test_registered_metrics_per_race_and_version(night):
    s = night["score"]
    for rid in REPLAYED:
        r = s["races"][rid]
        assert r["primary"] == "v3" and {"v1", "v2", "v3"} <= set(r["versions"])
        assert all(x["registered"] for x in r["registration"].values())
        for v, x in r["versions"].items():
            af = {a["q"]: a for a in x["at_fractions"]}
            assert set(af) == set(S.QS) and all(isinstance(a["in90"], bool) for a in af.values())
            assert x["settle"]["gate"] and x["settle"]["model"] and x["settle"]["lead_min"] is not None
            p = x["probability"]["gate_to_settle"]
            assert 0 <= p["brier_model"] <= 1 and 0 <= p["brier_market"] <= 1
            # the version's turnout is re-projected from the archive with the registered code; its share must equal
            # what the live model published at that minute
            assert [a["share_check"] for a in x["turnout_at_fractions"] if a["q"] > 0] == [0.0] * len(S.QS)
            assert x["edge_rule"]["fee_unverified"] is True          # fees={} in the test: flagged, not hidden
        assert r["versions"]["v1"]["score_race"]["turnout_rel"]["n"] > 0
    pooled = s["pooled"]["primary"]
    assert set(REPLAYED) <= set(pooled["races"]) and pooled["catchup"]["polymarket"]["markets"] >= len(REPLAYED)
    late = next(a for a in pooled["at_fractions"] if a["q"] == 0.9)
    assert late["mae_margin_pts"] < 2                                # replayed priors: the count converges


def test_execution_blocks(night):
    e = night["score"]["execution"]["v3"]
    assert e["signal"]["headline"]["latency_s"] == 60 and e["signal"]["headline"]["limit"] == "signal"
    assert {t["limit"] for t in e["signal"]["table"]} == {"signal", "model"}
    assert set(e["model_limit"]["grid"]) == {f"edge{x}c_delay{d}s" for x in (5, 10, 20) for d in (60, 300)}
    h = e["model_limit"]["headline"]
    assert h["deployed"] <= 10_000 and h["ending_value"] >= 0


def test_rerun_is_stable_and_certified_rows_take_over(night):
    w = night["w"]
    cert = w / "cert.csv"
    cert.write_text("# test\nrace,dem,rep,total,certified,source\nga-sen,600,400,1010,2026-12-01,https://sos.ga.gov/x\n")
    p = S.run(night["out"], night["snap"], R.CONFIGS, fees={}, cert_path=cert, cert_counties_path=w / "none_c.csv")
    s = json.loads(p.read_text())
    o = s["races"]["ga-sen"]["outcome"]
    assert o["status"] == "certified" and abs(o["share_a"] - 0.6) < 1e-9 and o["county_status"] == "provisional"
    assert s["races"]["ga-gov"]["outcome"]["status"] == "provisional" and s["outcome_status"] == "provisional"
    p = S.run(night["out"], night["snap"], R.CONFIGS, fees={}, cert_path=w / "none.csv", cert_counties_path=w / "none_c.csv")
    again = json.loads(p.read_text())
    a, b = dict(night["score"]), dict(again)
    a.pop("generated_utc"), b.pop("generated_utc")
    assert a == b
    assert (night["out"] / "us2026" / "score.md").read_text().startswith("# US midterms 2026: scoring")


def test_final_refuses_without_certified_results(night):
    with pytest.raises(SystemExit):
        S.run(night["out"], night["snap"], R.CONFIGS, fees={}, cert_path=night["w"] / "none.csv", final=True)


def test_senate_control_outcome_and_forecast_at_poll_close():
    hist = [["2026-11-03T22:30:00Z", 0.6], ["2026-11-04T03:00:00Z", 0.99]]
    ov = {"offices": {"senate": {
        "holdovers": {"D": 49, "R": 49, "I": 0}, "majority": 51,
        "control_markets": [{"platform": "polymarket", "yes_is": "dem", "history": hist}],
        "races": [{"race_key": "aa-sen", "polls": None, "pm": {"price": 0.99}, "history": hist},
                  {"race_key": "bb-sen", "polls": {"avg": 0.55, "n_polls": 12}, "pm": None, "history": []}]}}}
    s = S.senate_control(ov, {"aa-sen": {"dem_share": 0.52, "status": "certified"}})
    assert s["status"] == "pending" and s["missing"] == ["bb-sen"] and s["forecast"]["market_p"] == 0.6
    s = S.senate_control(ov, {"aa-sen": {"dem_share": 0.52, "status": "certified"},
                              "bb-sen": {"dem_share": 0.51, "status": "provisional"}})
    assert s["status"] == "provisional" and s["dem_caucus_seats"] == 51 and s["dem_control"] == 1
    assert s["brier_market_p"] == pytest.approx(0.16)
