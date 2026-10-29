"""US midterms: feed parsers on real snapshots (tests/fixtures/us), Census unit ids, model sanity, export."""

import base64
import csv
import gzip
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us

F = Path(__file__).parent / "fixtures" / "us"
ROOT = Path(__file__).resolve().parents[1]


def rd(name: str) -> bytes:
    b = (F / name).read_bytes()
    return gzip.decompress(b) if name.endswith(".gz") else b


def _census() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with open(F / "census_national_county2020_subset.txt") as fh:
        for r in csv.DictReader(fh, delimiter="|"):
            out.setdefault(r["STATE"].lower(), {})[r["STATEFP"] + r["COUNTYFP"]] = r["COUNTYNAME"]
    return out


PARSED = {
    "ca": lambda: us.parse_ca(rd("ca_governor_county_all.json"), rd("ca_status.json")),
    "tx": lambda: us.parse_tx(rd("tx_53814_election.json"), rd("tx_53814_countyInfo.json.gz")),
    "tx26": lambda: us.parse_tx(rd("tx_53815_election.json"), rd("tx_53815_countyInfo.json.gz")),
    "oh": lambda: us.parse_oh(rd("oh_vssc1622.xml.gz"), rd("oh_summary.csv")),
    "ak": lambda: us.parse_ak(rd("ak_statewide.js.gz")),
    "ga": lambda: us.parse_ga(rd("ga_2024_president_item.json.gz")),
}


# ------------------------------------------------------------------ parsers

@pytest.mark.parametrize("feed", list(PARSED))
def test_unit_totals_add_up_to_statewide(feed):
    df = PARSED[feed]()
    assert set(df.columns) == set(us.COLS)
    assert len(df) and df["votes"].ge(0).all()
    for race, g in df.groupby("race"):
        state = g[g["level"] == "state"].groupby("candidate")["votes"].sum()
        units = g[g["level"] != "state"].groupby("candidate")["votes"].sum().reindex(state.index).fillna(0)
        if feed == "ak" and race == "sen":
            # federal races: overseas federal-only absentee ballots are in the statewide total but in no district
            assert (state - units).abs().sum() / state.sum() < 1e-3
            assert (state >= units).all()
        else:
            assert np.allclose(state.to_numpy(), units.to_numpy()), (feed, race)


def test_unit_counts_and_progress_fields():
    assert PARSED["ca"]()["unit_id"].nunique() == 59                    # 58 counties + statewide
    tx = PARSED["tx"]()
    assert tx.loc[tx["level"] == "county", "unit_id"].nunique() == 254
    assert set(tx["race"]) == {"gov", "sen"} and tx["early"].notna().all()
    oh = PARSED["oh"]()
    assert oh.loc[oh["level"] == "county", "unit_id"].nunique() == 88
    adams = oh[(oh["unit_id"] == "us-oh-39001")].iloc[0]                 # summary CSV: 17,018 reg, 2,200 counted
    assert (adams["registered"], adams["ballots"], adams["outstanding"]) == (17018, 2200, 1 + 70)
    ak = PARSED["ak"]()
    assert sorted(ak.loc[ak["level"] == "district", "unit_id"].unique()) == [f"us-ak-hd{d:02d}" for d in range(1, 41)]
    peltola = ak[(ak["level"] == "state") & (ak["race"] == "sen") & ak["candidate"].str.startswith("Peltola")]
    assert peltola["party"].item() == "DEM" and peltola["votes"].item() == 82244
    ga = PARSED["ga"]()
    st = ga[ga["level"] == "state"].set_index("party")["votes"]
    assert st["REP"] == 2663117 and st["DEM"] == 2548017                  # GA 2024 certified president
    assert (ga["early"] <= ga["votes"]).all()


def test_tx_2026_general_format_known():
    """The 2026 general feed (pre-election, zeros) parses with the same parser as the primary."""
    df = PARSED["tx26"]()
    names = set(df["candidate"])
    assert {"JAMES TALARICO", "KEN PAXTON", "GINA HINOJOSA", "GREG ABBOTT (I)"} <= names
    travis = df[df["unit_id"] == "us-tx-48453"].iloc[0]
    assert travis["registered"] > 900_000 and travis["units"] > 0


def test_race_and_party_keys():
    assert us.race_key("U. S. SENATOR ") == "sen" and us.race_key("US Senate") == "sen"
    assert us.race_key("Governor / Lieutenant Governor") == "gov"
    assert us.race_key("Governor and Lieutenant Governor") == "gov"
    assert us.race_key("Lieutenant Governor") is None
    assert us.race_key("State Senate District 3") is None
    assert us.race_key("President of the US") == "pres"
    assert [us.party_code(p) for p in ("Dem", "REP", "Democratic", "Republican", "W")] == ["DEM", "REP", "DEM", "REP", "W"]


# ------------------------------------------------------------------ unit ids = Census county FIPS

@pytest.mark.parametrize("st", ["ca", "tx", "oh", "ga", "az", "sc", "vt"])
def test_county_table_matches_census(st):
    census = _census()[st]
    c = us.counties(st)
    fips = c["unit_id"].str.removeprefix(f"us-{st}-")
    assert set(fips) == set(census) and len(fips) == len(census)
    for f, name in zip(fips, c["name"]):
        assert us._norm(census[f]) == us._norm(name)


def test_tx_order_matches_enr_county_ids():
    lk = json.loads(base64.b64decode(json.loads(rd("tx_53815_election.json"))["Lookups"]))["County"]
    c = us.counties("tx")
    for x in lk:
        assert us._norm(x["CN"]) == c["key"].iat[x["ID"] - 1]


@pytest.mark.parametrize("feed", list(PARSED))
def test_parsed_unit_ids_are_known_units(feed):
    df = PARSED[feed]()
    st = {"tx26": "tx"}.get(feed, feed)
    units = df.loc[df["level"] != "state", "unit_id"]
    assert units.str.fullmatch(rf"us-{st}-(\d{{5}}|hd\d\d)").all()
    assert set(units) <= set(us.counties(st)["unit_id"])


# ------------------------------------------------------------------ model on synthetic partial counts

def _cfg(**count) -> dict:
    return {"election": {"state": "ca", "race": "gov", "a": {"party": "DEM"}, "b": {"party": "REP"}},
            "prior": {}, "model": {"tau": 0.03, "rho": 0.3, "sigma_mun0": 0.08}, "count": count}


def _pri(seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = us.counties("ca")
    return pd.DataFrame({"unit_id": c["unit_id"], "name": c["name"], "s0": rng.uniform(0.3, 0.75, len(c)),
                         "V": rng.integers(5_000, 400_000, len(c)).astype(float)})


def _rows(pri, share, frac, complete=False, outstanding=np.nan, ballots=np.nan):
    n = pri["V"].to_numpy() * frac
    base = {"level": "county", "name": "", "race": "gov", "candidate": "", "early": np.nan,
            "units": 10.0, "reporting": 10.0 if complete else 5.0, "ballots": ballots, "registered": np.nan,
            "outstanding": outstanding}
    a = pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "DEM", "votes": n * share})
    b = pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "REP", "votes": n * (1 - share)})
    return pd.concat([a, b], ignore_index=True)[us.COLS]


def test_full_count_is_certain():
    pri = _pri()
    truth = np.clip(pri["s0"] - 0.04, 0, 1).to_numpy()
    res, units = us.project(_rows(pri, truth, 1.0, complete=True), _cfg(f_complete=1.0), pri)
    final = (pri["V"] * truth).sum() / pri["V"].sum()
    assert res["share_hat"] == pytest.approx(final, abs=1e-9)
    assert res["raw"] == pytest.approx(final, abs=1e-9)
    assert res["share_sd"] < 1e-6 and res["p_win"] == float(final > 0.5)
    assert units["f"].eq(1.0).all()


def test_uniform_swing_partial_count_recovers_final_share():
    pri = _pri()
    truth = np.clip(pri["s0"] + 0.05, 0, 1).to_numpy()
    final = (pri["V"] * truth).sum() / pri["V"].sum()
    rows = _rows(pri.iloc[::2], truth[::2], 0.4)                       # half the counties, 40 % counted each
    res, units = us.project(rows, _cfg(), pri)
    assert res["share_hat"] == pytest.approx(final, abs=0.01)
    assert res["swing_nat"] == pytest.approx(0.05, abs=0.005)
    assert 0 < res["counted_frac"] < 0.3
    assert 0.002 < res["share_sd"] < 0.05
    assert (res["p_win"] > 0.5) == (final > 0.5)
    seen = units["n"] > 0
    assert units.loc[seen, "f"].round(6).eq(0.4).all() and units.loc[~seen, "f"].eq(0).all()
    assert units["proj"].between(0, 1).all()


def test_sd_shrinks_as_count_proceeds():
    pri = _pri()
    truth = pri["s0"].to_numpy()
    sds = [us.project(_rows(pri, truth, fr), _cfg(), pri)[0]["share_sd"] for fr in (0.1, 0.5, 0.9)]
    assert sds[0] > sds[1] > sds[2]


def test_no_returns_gives_prior():
    pri = _pri()
    res, units = us.project(_rows(pri, pri["s0"], 0.0).iloc[:0], _cfg(), pri)
    assert res["share_hat"] == pytest.approx((pri["s0"] * pri["V"]).sum() / pri["V"].sum(), abs=1e-9)
    assert np.isnan(res["raw"]) and units["f"].eq(0).all()


def test_outstanding_rule_and_turnout_learning():
    pri = _pri()
    # complete counties at 80 % of prior turnout with 10 % of their ballots still outstanding
    rows = _rows(pri, pri["s0"], 0.8, complete=True, outstanding=0.1 * 0.8 * pri["V"], ballots=0.8 * pri["V"])
    cs = us.count_state(rows, _cfg(use_outstanding=True), pri)
    assert np.allclose(cs["f"], 1 / 1.1) and cs["complete"].all()
    # half complete (f_complete), half partial: learned turnout scales the partial counties' expected vote
    done = _rows(pri.iloc[::2], pri["s0"].iloc[::2], 0.6, complete=True)
    part = _rows(pri.iloc[1::2], pri["s0"].iloc[1::2], 0.3)
    cs = us.count_state(pd.concat([done, part]), _cfg(f_complete=1.0, learn_turnout=True,
                                                       turnout_prior_votes=1.0), pri).set_index("unit_id")
    assert cs["turnout_T"].iloc[0] == pytest.approx(0.6, rel=1e-3)
    odd = pri["unit_id"].iloc[1::2]
    assert np.allclose(cs.loc[odd, "f"], 0.5, atol=1e-3)               # 0.3 counted of 0.6 expected


def test_vote_type_split_learns_election_day_swing():
    """Early + mail votes are 15 points more D than Election Day: all early batches are in, Election Day is counted
    in half the counties. Splitting by vote type must project the rest from Election Day returns."""
    pri = _pri()
    early_frac, s_e, s_l = 0.6, pri["s0"] + 0.06, pri["s0"] - 0.09     # county final = s0 + 0.0
    V = pri["V"]
    half = np.arange(len(pri)) % 2 == 0
    n_l = np.where(half, V * (1 - early_frac), 0.0)
    d = V * early_frac * s_e + n_l * s_l
    r = V * early_frac * (1 - s_e) + n_l * (1 - s_l)
    base = {"level": "county", "name": "", "race": "gov", "candidate": "", "units": 10.0, "ballots": np.nan,
            "registered": np.nan, "outstanding": np.nan}
    rows = pd.concat([
        pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "DEM", "votes": d, "early": V * early_frac * s_e,
                      "reporting": np.where(half, 10.0, 2.0)}),
        pd.DataFrame({**base, "unit_id": pri["unit_id"], "party": "REP", "votes": r,
                      "early": V * early_frac * (1 - s_e), "reporting": np.where(half, 10.0, 2.0)})])[us.COLS]
    final = (V * pri["s0"]).sum() / V.sum()
    cfg_on = _cfg(f_complete=1.0, vote_type=True, early_share=0.6)
    res_on, units = us.project(rows, cfg_on, pri)
    res_off, _ = us.project(rows, _cfg(f_complete=1.0), pri)
    assert abs(res_on["share_hat"] - final) < 0.005 < abs(res_off["share_hat"] - final)
    assert units["proj"].between(0, 1).all() and units.loc[half, "proj"].sub(pri.loc[half, "s0"]).abs().max() < 1e-6


# ------------------------------------------------------------------ export dry run (synthetic night)

def _ca_snapshot(scale: float, swing: float, pri: pd.DataFrame) -> bytes:
    rows = json.loads(rd("ca_governor_county_all.json"))
    s0 = dict(zip(pri["name"], pri["s0"]))
    V = dict(zip(pri["name"], pri["V"]))
    tot_d = tot_r = 0
    for r in rows:
        where = r["raceTitle"].split(" - ", 1)[1].replace(" County Results", "")
        if where.startswith("Statewide"):
            continue
        n = V[where] * scale
        d, rep = round(n * (s0[where] + swing)), round(n * (1 - s0[where] - swing))
        tot_d, tot_r = tot_d + d, tot_r + rep
        r["candidates"][0]["Votes"], r["candidates"][1]["Votes"] = f"{d:,}", f"{rep:,}"
    for r in rows:
        if "Statewide" in r["raceTitle"]:
            r["candidates"][0]["Votes"], r["candidates"][1]["Votes"] = f"{tot_d:,}", f"{tot_r:,}"
    return json.dumps(rows).encode()


def test_export_dry_run_writes_valid_contract(tmp_path):
    cfg_path = ROOT / "configs" / "us2026_ca_gov_v1.toml"
    cfg = us.load_config(cfg_path)
    c = us.counties("ca")
    rng = np.random.default_rng(3)
    pri_df = pd.DataFrame({"unit_id": c["unit_id"], "name": c["name"], "s0": rng.uniform(0.35, 0.8, len(c)),
                           "V": rng.integers(5_000, 300_000, len(c)).astype(float)})
    priors = pd.concat([pd.DataFrame({"source": src, "unit_id": pri_df["unit_id"], "dem": pri_df["V"] * pri_df["s0"],
                                      "rep": pri_df["V"] * (1 - pri_df["s0"]), "total": pri_df["V"]})
                        for src in {cfg["prior"]["share"], cfg["prior"]["votes"]}])
    snap = tmp_path / "snap"
    key = us.FEEDS["ca"]["results"]
    (snap / key).mkdir(parents=True)
    (snap / "polymarket_books").mkdir()
    pm = cfg["markets"]["polymarket"]
    for i, (t, scale) in enumerate([("20261104T040500", 0.05), ("20261104T043000", 0.2), ("20261104T060000", 0.45)]):
        (snap / key / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, -0.02, pri_df))
        book = {"bids": [{"price": "0.95", "size": "100"}], "asks": [{"price": "0.97", "size": "50"}],
                "last_trade_price": "0.96"}
        (snap / "polymarket_books" / f"{t}.000001Z.json").write_text(json.dumps({f"{pm['event']}/{pm['market']}": book}))
    ex = us.Exporter([cfg_path], snap, priors=priors)
    ex.update()
    d = ex.write(tmp_path / "site")
    meta = json.loads((d / "meta.json").read_text())
    assert {"id", "name", "date", "tz", "poll_close_utc", "geometry", "geometry_object", "unit_key", "regions",
            "races", "sources", "notes"} <= set(meta)
    race = meta["races"][0]
    rid = cfg["election"]["race_id"]
    assert race["race_id"] == rid and race["units"] == f"units/{rid}.json"
    assert {"platform", "id", "question", "yes_is"} <= set(race["markets"][0])
    tl = json.loads((d / "timeline.json").read_text())[rid]
    assert len(tl) == 116                                              # 04:05 … 06:00, one row per minute
    for row in tl:
        later = {f"{k}_{v}" for v in ex.versions.get(rid, {}) if v != "v2" for k in ("P", "share", "sd")}
        assert set(row) == {"t", "counted", "raw", "share", "sd", "P", "P_v2", "share_v2", "sd_v2", "pm", "kalshi",
                            "rem_total", "rem_margin_total", "votes_proj", "margin_model", "margin_model_sd",
                            "margin_v2", "margin_polls", "margin_market", "margin_market_kalshi", "turnout_exp_total",
                            "turnout_proj_total", "turnout_vs_expected", "turnout_surprise_corr",
                            "turnout_surprise_slope", "margin_hot", "margin_cold", "counties_reported"} | later
        assert row["margin_model"] == pytest.approx((2 * row["share"] - 1) * 100, abs=0.02)
        assert row["votes_proj"] >= row["rem_total"] and row["margin_market_kalshi"] is None
        assert 0 <= row["P_v2"] <= 1 and 0 < row["share_v2"] < 1 and row["sd_v2"] > 0   # v2 runs alongside v1
        assert isinstance(row["rem_total"], int) and row["rem_total"] % 10 == 0 and row["rem_total"] >= 0
        assert 0 <= row["counted"] <= 1 and 0 <= row["P"] <= 1 and row["kalshi"] is None
    assert tl[-1]["pm"] == pytest.approx(0.96) and tl[-1]["counted"] > tl[0]["counted"]
    units = json.loads((d / "units" / f"{rid}.json").read_text())
    n = len(units["unit_ids"])
    assert n == 58 and all(len(units[k]) == n for k in ("region", "names", "prior", "V0"))
    assert 1 <= len(units["frames"]) <= 60
    for fr in units["frames"]:
        assert all(len(fr[k]) == n and all(isinstance(x, int) for x in fr[k])
                   for k in ("f", "a", "proj", "proj_poll", "rem_margin_poll", "turnout_so_far", "turnout_ratio_live"))
        assert all(0 <= x <= 1000 for x in fr["f"])
    assert len(units["turnout_exp"]) == n and len(units["lowprop_index"]) == n
    assert sum(units["turnout_exp"]) == pytest.approx(sum(units["V0"]), rel=1e-3)   # same statewide expectation
    assert json.loads((d / "units.json").read_text())["race_id"] == rid


# ------------------------------------------------------------------ robustness (live night)

def _synth_priors(cfgs: list[dict], seed=5) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rng = np.random.default_rng(seed)
    parts, by_state = [], {}
    for cfg in cfgs:
        c = us.counties(cfg["election"]["state"])
        p = pd.DataFrame({"unit_id": c["unit_id"], "name": c["name"], "s0": rng.uniform(0.35, 0.8, len(c)),
                          "V": rng.integers(5_000, 300_000, len(c)).astype(float)})
        by_state[cfg["election"]["state"]] = p
        for src in {cfg["prior"]["share"], cfg["prior"]["votes"]}:
            parts.append(pd.DataFrame({"source": src, "unit_id": p["unit_id"], "dem": p["V"] * p["s0"],
                                       "rep": p["V"] * (1 - p["s0"]), "total": p["V"]}))
    return pd.concat(parts, ignore_index=True), by_state


def test_archive_skips_malformed_snapshot_and_ignores_tmp(tmp_path):
    key = us.FEEDS["ca"]["results"]
    d = tmp_path / key
    d.mkdir()
    good = rd("ca_governor_county_all.json")
    (d / "20261104T040000.000000Z.json").write_bytes(good)
    bad = d / "20261104T041000.000000Z.json"
    bad.write_bytes(good[: len(good) // 2])                            # truncated write
    (d / "20261104T042000.000000Z.json.gz").write_bytes(b"not gzip")    # corrupt gzip
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / f"{key}.20261104T043000.000000Z.json").write_bytes(good)
    arc = us.Archive(tmp_path)
    arc.refresh()
    assert arc.keys(key) == [key] and ".tmp" not in arc._files
    t = pd.Timestamp("2026-11-04T04:30Z")
    f = arc.at(key, t)
    assert f.name == "20261104T040000.000000Z.json"                     # previous valid snapshot
    assert len(arc.bad) == 2 and not (set(arc.bad) & set(arc._cache))   # malformed bytes never cached
    assert us.parse_ca(arc.read(f))["votes"].sum() > 0
    with pytest.raises(ValueError):
        arc.read(bad)
    bad.write_bytes(good)                                              # completed later (size changed): usable
    assert arc.at(key, t) == bad and bad not in arc.bad


def _two_feed_archive(tmp_path, cfgs, by_state, times):
    snap = tmp_path / "snap"
    for k in (us.FEEDS["ca"]["results"], us.FEEDS["tx"]["county"]):
        (snap / k).mkdir(parents=True)
    for t, scale in times:
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
        (snap / us.FEEDS["tx"]["county"] / f"{t}.000000Z.json").write_bytes(rd("tx_53815_countyInfo.json.gz"))
    return snap


TIMES = [("20261104T040000", 0.1), ("20261104T041000", 0.3), ("20261104T042000", 0.5)]


def test_feed_parser_failure_does_not_block_other_races(tmp_path, monkeypatch):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml", ROOT / "configs" / "us2026_tx_sen_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    snap = _two_feed_archive(tmp_path, cfgs, by_state, TIMES)

    def broken(*a, **k):
        raise KeyError("Races")
    monkeypatch.setattr(us, "parse_tx", broken)
    ex = us.Exporter(paths, snap, priors=priors)
    ex.update()
    d = ex.write(tmp_path / "site")
    tl = json.loads((d / "timeline.json").read_text())
    assert len(tl["ca-gov"]) == 21 and tl["ca-gov"][-1]["counted"] > tl["ca-gov"][0]["counted"]
    assert len(tl["tx-sen"]) == 21 and tl["tx-sen"][-1]["raw"] is None   # nothing parsed yet: prior only
    assert "feed:tx" in ex.errors
    assert (d / "units" / "ca-gov.json").exists()


def test_failing_race_retries_without_duplicates_while_others_publish(tmp_path, monkeypatch):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml", ROOT / "configs" / "us2026_tx_sen_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    snap = _two_feed_archive(tmp_path, cfgs, by_state, TIMES[:2])
    real = us.project
    fault = {"on": True}

    def project(rows, cfg, pri):
        if fault["on"] and cfg["election"]["race_id"] == "tx-sen":
            raise RuntimeError("model blew up")
        return real(rows, cfg, pri)
    monkeypatch.setattr(us, "project", project)
    ex = us.Exporter(paths, snap, priors=priors, max_retries=3)
    ex.update()
    ex.write(tmp_path / "site")
    assert len(ex.timeline["ca-gov"]) == 11 and ex.timeline["tx-sen"] == [] and ex.cursor["tx-sen"] is None
    assert "tx-sen" in ex.errors
    ex.update()                                                       # second pass: CA has nothing new
    assert len(ex.timeline["ca-gov"]) == 11 and ex.timeline["tx-sen"] == []
    t3, s3 = TIMES[2]
    (snap / us.FEEDS["ca"]["results"] / f"{t3}.000000Z.json").write_bytes(_ca_snapshot(s3, 0.0, by_state["ca"]))
    (snap / us.FEEDS["tx"]["county"] / f"{t3}.000000Z.json").write_bytes(rd("tx_53815_countyInfo.json.gz"))
    fault["on"] = False                                               # fault cleared: the race backfills
    ex.update()
    for rid in ("ca-gov", "tx-sen"):
        ts = [r["t"] for r in ex.timeline[rid]]
        assert len(ts) == len(set(ts)) == 21, rid                     # 04:00 … 04:20, no duplicates, no gaps
    assert not ex.errors


def test_minute_failing_every_pass_is_skipped_not_retried_forever(tmp_path, monkeypatch):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    snap = tmp_path / "snap"
    (snap / us.FEEDS["ca"]["results"]).mkdir(parents=True)
    for t, scale in TIMES[:2]:
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
    real = us.project

    def project(rows, cfg, pri):                                      # the 04:00 state always fails
        if rows.loc[rows["level"] == "county", "votes"].sum() < by_state["ca"]["V"].sum() * 0.2:
            raise RuntimeError("bad state")
        return real(rows, cfg, pri)
    monkeypatch.setattr(us, "project", project)
    ex = us.Exporter(paths, snap, priors=priors, max_retries=2)
    for _ in range(25):
        ex.update()
    ts = [r["t"] for r in ex.timeline["ca-gov"]]
    assert ts[0] == "2026-11-04T04:10:00Z" and len(ts) == len(set(ts)) == 1


PAIRS = '''election = "us2026"
[[pair]]
id = "ca-gov:b"
race_id = "ca-gov"
proposition = "Hilton wins"
pm_market = "will-steve-hilton-win-the-california-governor-election-in-2026"
kalshi_ticker = "KXTEST-HIL"
same_side = true
kalshi_yes_is = "b"
[[pair]]
id = "ca-gov:a"
race_id = "ca-gov"
proposition = "Becerra wins"
pm_market = "will-xavier-becerra-win-the-california-governor-election-in-2026"
kalshi_ticker = "KXTEST-BEC"
same_side = true
kalshi_yes_is = "a"
kalshi_resolution = "Yes if Becerra is elected (our words)."
'''


def _kbook(bid: float, no_bid: float) -> dict:
    return {"yes_dollars": [[f"{bid:.4f}", "100.00"]], "no_dollars": [[f"{no_bid:.4f}", "100.00"]]}


def test_kalshi_column_from_books_with_isolation(tmp_path):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    pairs = tmp_path / "pairs.toml"
    pairs.write_text(PAIRS)
    snap = tmp_path / "snap"
    (snap / us.FEEDS["ca"]["results"]).mkdir(parents=True)
    (snap / "kalshi_books").mkdir()
    for t, scale in TIMES:
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
    # 04:05 a book (mid 0.95); 04:12 a well-formed file that is not a book map; 04:15 a book whose spread > 10c
    (snap / "kalshi_books" / "20261104T040500.000000Z.json").write_text(json.dumps({"KXTEST-BEC": _kbook(.94, .04)}))
    (snap / "kalshi_books" / "20261104T041200.000000Z.json").write_text("[1, 2]")
    (snap / "kalshi_books" / "20261104T041500.000000Z.json").write_text(json.dumps({"KXTEST-BEC": _kbook(.60, .20)}))
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=pairs)
    ex.update()
    d = ex.write(tmp_path / "site")
    k = {r["t"][11:16]: r["kalshi"] for r in json.loads((d / "timeline.json").read_text())["ca-gov"]}
    assert k["04:04"] is None and k["04:05"] == pytest.approx(0.95) and k["04:11"] == pytest.approx(0.95)
    assert k["04:12"] == pytest.approx(0.95) and "kalshi" in ex.errors      # bad file: last book kept, logged
    assert k["04:15"] is None and k["04:20"] is None                        # spread 0.20 > 10c: no price
    assert len(k) == 21                                                     # the race never stopped
    race = json.loads((d / "meta.json").read_text())["races"][0]
    km = [m for m in race["markets"] if m["platform"] == "kalshi"]
    assert km == [{"platform": "kalshi", "id": "KXTEST-BEC", "series": "",
                   "question": "Yes if Becerra is elected (our words).", "yes_is": "a"}]


def test_no_kalshi_pairs_or_books_gives_null_column(tmp_path):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    snap = tmp_path / "snap"
    (snap / us.FEEDS["ca"]["results"]).mkdir(parents=True)
    for t, scale in TIMES:
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=tmp_path / "missing.toml")
    ex.update()
    d = ex.write(tmp_path / "site")
    rows = json.loads((d / "timeline.json").read_text())["ca-gov"]
    assert len(rows) == 21 and all(r["kalshi"] is None for r in rows)
    assert all(m["platform"] != "kalshi" for m in json.loads((d / "meta.json").read_text())["races"][0]["markets"])


def test_timeline_tail_is_last_60_minutes_of_full_timeline(tmp_path):
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml", ROOT / "configs" / "us2026_tx_sen_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    snap = _two_feed_archive(tmp_path, cfgs, by_state, [("20261104T040000", 0.1), ("20261104T050000", 0.3),
                                                         ("20261104T053000", 0.5)])
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=tmp_path / "none.toml")
    ex.update()
    d = ex.write(tmp_path / "site")
    full = json.loads((d / "timeline.json").read_text())
    tail = json.loads((d / "timeline_tail.json").read_text())
    assert set(tail) == set(full) | {"tail", "since"} and tail["tail"] is True
    for rid in full:
        assert len(full[rid]) == 91
        assert tail[rid] == full[rid][-60:]                               # 04:31 … 05:30
    assert tail["since"] == "2026-11-04T04:31:00Z"


# ------------------------------------------------------------------ before the count (brunder us preelection)

PAIRS_PRE = '''election = "us2026"
[[pair]]
id = "ca"
race_id = "ca-gov"
proposition = "Becerra wins"
pm_market = "will-xavier-becerra-win-the-california-governor-election-in-2026"
pm_yes_token = "111"
kalshi_ticker = "KXCA-BEC"
kalshi_series = "KXCA"
same_side = true
kalshi_yes_is = "a"
[[pair]]
id = "oh"
race_id = "oh-sen"
proposition = "Democrats win"
pm_market = "will-the-democrats-win-the-ohio-senate-race-in-2026"
pm_yes_token = "222"
kalshi_ticker = "KXOH-D"
kalshi_series = "KXOH"
same_side = true
kalshi_yes_is = "a"
'''
NOW = pd.Timestamp("2026-11-03T12:00Z")


def _mock_prices(monkeypatch):
    from brunder import kalshi, polymarket

    def ph(token, start, end, fidelity_min=1, **k):
        t = pd.date_range(start.ceil("10min"), end, freq="10min")
        return pd.DataFrame({"time": t, "price": 0.6 if token == "111" else 0.3})

    def cs(series, ticker, start, end, period_min=1):
        t = pd.date_range(start.ceil("1min"), end, freq="7min")
        p = 0.62 if ticker == "KXCA-BEC" else 0.33
        return pd.DataFrame({"time": t, "yes_bid": p - .01, "yes_ask": p + .01, "price": p, "volume": 1.0,
                             "open_interest": 1.0})
    monkeypatch.setattr(polymarket, "price_history", ph)
    monkeypatch.setattr(kalshi, "candlesticks", cs)


def _pre_setup(tmp_path, monkeypatch):
    _mock_prices(monkeypatch)
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml", ROOT / "configs" / "us2026_oh_sen_v1.toml"]
    priors, by_state = _synth_priors([us.load_config(paths[0])])          # no Ohio prior
    pairs = tmp_path / "pairs.toml"
    pairs.write_text(PAIRS_PRE)
    return paths, priors, by_state, pairs


def test_preelection_writes_precount_contract(tmp_path, monkeypatch):
    paths, priors, by_state, pairs = _pre_setup(tmp_path, monkeypatch)
    d = us.preelection(paths, tmp_path / "site", days=14, now=NOW, priors=priors, pairs_path=pairs)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["phase"] == "pre-count" and meta["poll_close_utc"] == "2026-11-04T00:30:00Z"   # ca + oh only here
    assert meta["poll_close_by_region"] == {"ca": "2026-11-04T04:00:00Z", "oh": "2026-11-04T00:30:00Z"}
    assert any("model v2 adds the poll average" in n for n in meta["notes"])
    ca, oh = (next(r for r in meta["races"] if r["race_id"] == k) for k in ("ca-gov", "oh-sen"))
    assert ca["precount"]["P"] is not None and 0 < ca["precount"]["sd"] < 0.2 and ca["units"] == "units/ca-gov.json"
    assert oh["precount"] is None and oh["model_note"] == us.NO_PRIOR and oh["units"] is None
    pm = next(m for m in ca["markets"] if m["platform"] == "polymarket")
    assert pm["token_yes"] == "111"
    tl = json.loads((d / "timeline.json").read_text())
    assert json.loads((d / "timeline_precount.json").read_text()) == tl
    for rid, pmv, kv in (("ca-gov", 0.6, 0.62), ("oh-sen", 0.3, 0.33)):
        rows = tl[rid]
        assert len(rows) == 14 * 48 + 1 and rows[-1]["t"] == "2026-11-03T12:00:00Z"
        assert all(r["counted"] is None and r["raw"] is None for r in rows)
        assert rows[-1]["pm"] == pytest.approx(pmv) and rows[-1]["kalshi"] == pytest.approx(kv)
    assert {r["P"] for r in tl["ca-gov"]} == {ca["precount"]["P"]} and all(r["P"] is None for r in tl["oh-sen"])
    assert ca["config_v2"] == "us2026_ca_gov_v2.toml" and ca["precount"]["share_v2"] is not None
    assert {r["P_v2"] for r in tl["ca-gov"]} == {ca["precount"]["P_v2"]} and all(r["P_v2"] is None for r in tl["oh-sen"])
    u = json.loads((d / "units" / "ca-gov.json").read_text())
    assert len(u["frames"]) == 1 and set(u["frames"][0]["f"]) == {0} and u["frames"][0]["proj"] == u["prior"]
    pp, V = np.array(u["frames"][0]["proj_poll"]) / 1000, np.array(u["V0"], float)
    assert np.average(pp, weights=V) == pytest.approx(ca["precount"]["share_v2"], abs=2e-3)   # poll model's map
    assert pp.mean() > np.array(u["prior"]).mean() / 1000                                      # CA polls above prior
    assert len(u["prior_margin"]) == len(u["unit_ids"]) == 58
    st = json.loads((d / "stats.json").read_text())
    assert all(st[k] is None for k in us.STATS_KEYS) and st["reasons"]["calibration"] == "count has not started"
    tail = json.loads((d / "timeline_tail.json").read_text())
    assert tail["tail"] is True and len(tail["ca-gov"]) == 2               # 11:30 and 12:00


def test_live_takes_over_precount_files_without_manual_step(tmp_path, monkeypatch):
    paths, priors, by_state, pairs = _pre_setup(tmp_path, monkeypatch)
    out = tmp_path / "site"
    us.preelection(paths, out, days=1, now=NOW, priors=priors, pairs_path=pairs)
    snap = tmp_path / "snap"
    (snap / us.FEEDS["ca"]["results"]).mkdir(parents=True)
    (snap / "polymarket_books").mkdir()
    cfg_oh = us.load_config(paths[1])
    book = {"bids": [{"price": "0.30", "size": "1"}], "asks": [{"price": "0.32", "size": "1"}]}
    for t, scale in (("20261104T040500", 0.1), ("20261104T042000", 0.3)):
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
        key = f"{cfg_oh['markets']['polymarket']['event']}/{cfg_oh['markets']['polymarket']['market']}"
        (snap / "polymarket_books" / f"{t}.000001Z.json").write_text(json.dumps({key: book}))
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=pairs)       # live phase by default
    ex.update()
    d = ex.write(out)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["phase"] == "live" and all("precount" not in r for r in meta["races"])
    tl = json.loads((d / "timeline.json").read_text())
    ca = tl["ca-gov"]
    pre = [r for r in ca if r["counted"] is None]
    assert len(pre) == 49 and pre[-1]["t"] == "2026-11-03T12:00:00Z"        # pre-count rows kept in front
    assert ca[49]["t"] == "2026-11-04T04:05:00Z" and ca[-1]["counted"] > 0
    oh = [r for r in tl["oh-sen"] if r["t"] >= "2026-11-04"]
    assert len(oh) == 16 and all(r["P"] is None for r in oh) and oh[-1]["pm"] == pytest.approx(0.31)
    assert len(json.loads((d / "units" / "ca-gov.json").read_text())["frames"]) >= 1
    assert json.loads((d / "stats.json").read_text())["reasons"]["calibration"] == "computed after the count"
    # the scheduled pre-count job must not overwrite the live files
    us.preelection(paths, out, days=1, now=NOW, priors=priors, pairs_path=pairs)
    assert json.loads((d / "meta.json").read_text())["phase"] == "live"


# ------------------------------------------------------------------ national overview

def test_overview_from_registry(tmp_path, monkeypatch):
    _mock_prices(monkeypatch)
    reg = json.loads((ROOT / "data_public" / "us2026_races.json").read_text())
    ov = us.overview(NOW, days=1)
    sen, gov = ov["offices"]["senate"], ov["offices"]["governor"]
    assert len(gov["races"]) == 36 and len(sen["races"]) == len([r for r in reg["races"] if r["office"] == "senate"])
    assert sum(sen["holdovers"].values()) + len(sen["races"]) == 100
    assert sum(r["special"] for r in sen["races"]) == 2
    geo = json.loads((ROOT / "site" / "data" / "geo" / "us_counties.topo.json").read_text())
    states = {g["properties"]["unit_id"].split("-")[1] for g in geo["objects"]["counties"]["geometries"]}
    assert {r["state"] for r in sen["races"] + gov["races"]} <= states
    modeled = {r["race_id"] for r in sen["races"] + gov["races"] if r["modeled"]}
    assert modeled == {us.load_config(p)["election"]["race_id"] for p in ROOT.glob("configs/us2026_*_v1.toml")}
    polls = us.load_polls()
    for r in sen["races"] + gov["races"]:
        assert len(r["history"]) == 49 and r["pm"] is not None
        pa = polls.get(r["race_key"])
        assert (r["polls"] is None) == (pa is None)
        if pa:
            assert set(r["polls"]) == {"avg", "sd", "n_polls", "last_date", "p_win"} and r["polls"]["avg"] == pa["avg"]
        if r["pm"]["token_id"] == "111":
            assert r["pm"]["price"] == pytest.approx(0.6)
    assert [m["platform"] for m in sen["control_markets"]] == ["polymarket", "kalshi"]
    assert ov["offices"]["house"]["control_markets"][0]["price"] is not None


def test_live_refreshes_overview_from_books(tmp_path, monkeypatch):
    _mock_prices(monkeypatch)
    paths = [ROOT / "configs" / "us2026_ca_gov_v1.toml"]
    cfgs = [us.load_config(p) for p in paths]
    priors, by_state = _synth_priors(cfgs)
    reg = {"holdovers": {"senate": {"D": 32, "R": 31, "I": 2}}, "sources": [], "races": [
        {"race_key": "me-sen", "state": "me", "office": "senate", "special": False, "incumbent": "x",
         "incumbent_party": "R", "open": False, "dem_side": {"name": "y", "party": "D", "aligned": None},
         "modeled": False, "race_id": None,
         "pm": {"event": "maine-senate-election-winner", "slug": "will-the-democrats-win-the-maine-senate-race-in-2026",
                "token_id": "111", "yes_is": "dem_side"},
         "kalshi": {"ticker": "SENATEME-26-D", "series": "SENATEME", "yes_is": "dem_side"}}],
        "control_markets": {c: [{"platform": "polymarket", "event": f"ev-{c}", "slug": f"dem-{c}", "token_id": "222",
                                 "question": "q", "yes_is": "dem"},
                                {"platform": "kalshi", "ticker": f"CONTROL{c[0].upper()}-2026-D", "series": "S",
                                 "yes_is": "dem"}] for c in ("senate", "house")}}
    regf = tmp_path / "races.json"
    regf.write_text(json.dumps(reg))
    out = tmp_path / "site"
    (out / "us2026").mkdir(parents=True)
    (out / "us2026" / "overview.json").write_text(json.dumps(us.overview(pd.Timestamp("2026-11-04T03:00Z"), days=0.5,
                                                                         registry=regf)))
    snap = tmp_path / "snap"
    for k in (us.FEEDS["ca"]["results"], "polymarket_books", "kalshi_books"):
        (snap / k).mkdir(parents=True)
    pmbook = {"bids": [{"price": "0.70", "size": "1"}], "asks": [{"price": "0.72", "size": "1"}]}
    for t, scale in (("20261104T040500", 0.1), ("20261104T050000", 0.3)):
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
        (snap / "polymarket_books" / f"{t}.000001Z.json").write_text(json.dumps(
            {"maine-senate-election-winner/will-the-democrats-win-the-maine-senate-race-in-2026": pmbook,
             "ev-senate/dem-senate": pmbook}))
        (snap / "kalshi_books" / f"{t}.000001Z.json").write_text(json.dumps({"SENATEME-26-D": _kbook(.68, .30)}))
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=tmp_path / "none.toml")
    ex.update()
    d = ex.write(out)
    assert json.loads((d / "meta.json").read_text())["overview"] == "overview.json"
    ov = json.loads((d / "overview.json").read_text())
    r = ov["offices"]["senate"]["races"][0]
    assert r["pm"]["price"] == pytest.approx(0.71) and r["kalshi"]["price"] == pytest.approx(0.69)
    assert [h[0][11:16] for h in r["history"][-4:]] == ["03:30", "04:00", "04:30", "05:00"]
    assert r["history"][-2][1:] == [pytest.approx(0.71), pytest.approx(0.69)] and r["history"][-3][1] is None
    assert ov["offices"]["senate"]["control_markets"][0]["price"] == pytest.approx(0.71)
    assert ov["as_of"].startswith("2026-11-04T05:00")


# ------------------------------------------------------------------ outstanding-vote display fields

def test_remaining_votes_consistent_with_projection():
    pri = _pri()
    truth = np.clip(pri["s0"] + 0.03, 0, 1).to_numpy()
    rows = _rows(pri.iloc[::2], truth[::2], 0.4)
    res, units = us.project(rows, _cfg(), pri)
    n, a = units["n"].sum(), units["a"].sum()
    # projected final share = (counted + remaining) margins over all votes, exactly as the nowcast computes it
    assert (2 * a - n + res["rem_margin_total"]) / (n + res["rem_total"]) == pytest.approx(2 * res["share_hat"] - 1)
    assert res["rem_total"] == pytest.approx(units["rem"].sum()) and (units["rem"] >= 0).all()
    assert abs(units["rem_margin"]).le(units["rem"] + 1e-9).all()
    full, u_full = us.project(_rows(pri, truth, 1.0, complete=True), _cfg(f_complete=1.0), pri)
    assert full["rem_total"] == pytest.approx(0, abs=1e-6) and u_full["rem"].abs().max() < 1e-6
    pre, u_pre = us.project(_rows(pri, pri["s0"], 0.0).iloc[:0], _cfg(), pri)
    assert pre["rem_total"] == pytest.approx(pri["V"].sum())                # before the count: all expected votes
    assert pre["rem_margin_total"] == pytest.approx(((2 * pri["s0"] - 1) * pri["V"]).sum())


def test_rem_fields_in_precount_and_live_files(tmp_path, monkeypatch):
    paths, priors, by_state, pairs = _pre_setup(tmp_path, monkeypatch)
    out = tmp_path / "site"
    d = us.preelection(paths, out, days=1, now=NOW, priors=priors, pairs_path=pairs)
    tl = json.loads((d / "timeline.json").read_text())
    ca = tl["ca-gov"][-1]
    assert ca["rem_total"] == int(round(by_state["ca"]["V"].sum(), -1)) and ca["rem_total"] % 10 == 0
    assert tl["oh-sen"][-1]["rem_total"] is None
    fr = json.loads((d / "units" / "ca-gov.json").read_text())["frames"][0]
    assert all(isinstance(x, int) and x % 10 == 0 for x in fr["rem"] + fr["rem_margin"])
    assert abs(sum(fr["rem"]) - ca["rem_total"]) <= 10 * len(fr["rem"])
    snap = tmp_path / "snap"
    (snap / us.FEEDS["ca"]["results"]).mkdir(parents=True)
    for t, scale in (("20261104T040500", 0.2), ("20261104T042000", 0.5)):
        (snap / us.FEEDS["ca"]["results"] / f"{t}.000000Z.json").write_bytes(_ca_snapshot(scale, 0.0, by_state["ca"]))
    ex = us.Exporter(paths, snap, priors=priors, pairs_path=pairs)
    ex.update()
    d = ex.write(out)
    rows = [r for r in json.loads((d / "timeline.json").read_text())["ca-gov"] if r["counted"] is not None]
    assert rows[-1]["rem_total"] < rows[0]["rem_total"] < ca["rem_total"]
    last = json.loads((d / "units" / "ca-gov.json").read_text())["frames"][-1]
    assert len(last["rem"]) == len(last["rem_margin"]) == 58


# ------------------------------------------------------------------ AZ (Enhanced Voting), SC (Clarity), VT (static JSON)

def test_parse_az_enhanced_voting_testdata():
    df = us.parse_ga(rd("az_2026_governor_item_testdata.json.gz"), "az")
    cty = df[df["level"] == "county"]
    assert set(df["race"]) == {"gov"} and cty["unit_id"].nunique() == 15
    assert set(cty["unit_id"]) == set(us.counties("az")["unit_id"])
    assert {"DEM", "REP"} <= set(df["party"]) and (cty["units"] > 0).all()


@pytest.mark.parametrize("eid,races", [("122436", {"pres"}), ("115412", {"gov", "sen"})])
def test_parse_sc_clarity_totals(eid, races):
    df = us.parse_clarity(rd(f"sc_{eid}_ALL.json.gz"), rd(f"sc_{eid}_summary.json.gz"), rd(f"sc_{eid}_status.json.gz"))
    assert set(df["race"]) == races
    for race, g in df.groupby("race"):
        state = g[g["level"] == "state"].groupby("candidate")["votes"].sum()
        units = g[g["level"] == "county"].groupby("candidate")["votes"].sum().reindex(state.index)
        assert np.allclose(state.to_numpy(), units.to_numpy())
        assert g.loc[g["level"] == "county", "unit_id"].nunique() == 46
    abb = df[df["unit_id"] == "us-sc-45001"].iloc[0]
    assert abb["reporting"] == abb["units"] > 0 and abb["ballots"] > 0 and abb["registered"] > abb["ballots"]
    if eid == "115412":
        gov = df[(df["race"] == "gov") & (df["level"] == "state")].set_index("party")["votes"]
        assert gov["REP"] == 988501 and gov["DEM"] == 692691            # 2022 certified, SC Election Commission


def test_parse_vt_towns_to_counties():
    df = us.parse_vt(rd("vt_2024_main.json.gz"), rd("vt_2024_statewide_governor.json.gz"))
    assert set(df["unit_id"]) == set(us.counties("vt")["unit_id"]) and set(df["race"]) == {"gov"}
    tot = df.groupby("party")["votes"].sum()
    assert tot["REP"] == 266439 and tot["DEM"] == 79217                  # 2024 certified (Vermont SoS)
    assert (df["reporting"] == df["units"]).all()
    pre = us.parse_vt(rd("vt_2026_main.json.gz"), rd("vt_2026_statewide_governor.json.gz"))
    assert pre["votes"].sum() == 0 and (pre["reporting"] == 0).all() and {"DEM", "REP"} <= set(pre["party"])


@pytest.mark.parametrize("name", ["us2026_sc_sen_v1", "us2026_sc_gov_v1", "us2026_vt_gov_v1", "us2026_oh_sen_v1",
                                  "us2026_oh_gov_v1"])
def test_new_configs_have_public_priors(name):
    cfg = us.load_config(ROOT / "configs" / f"{name}.toml")
    pri = us.race_prior(cfg, us.load_priors(us.PUBLIC_PRIORS))
    assert pri is not None and len(pri) == len(us.counties(cfg["election"]["state"]))
    res, _ = us.project(us._frame([]), cfg, pri)
    assert 0 <= res["p_win"] <= 1 and res["rem_total"] > 0


def test_snap_resolver_clarity_listing_and_vt_static():
    import asyncio
    import httpx
    from brunder import snap
    page = ('<a href="https://www.enr-scvotes.org/SC/127000/web.1/#/">2026 Statewide General Election Runoffs</a>'
            '<a href="https://www.enr-scvotes.org/SC/127500/web.345435/#/summary">2026 General Election</a>')

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("/SC/elections.json"):
            return httpx.Response(200, json=[])
        if u.startswith("https://scvotes.gov/"):
            return httpx.Response(200, text=page)
        if u.endswith("/SC/127500/current_ver.txt"):
            return httpx.Response(200, text="380001\n")
        if u.endswith("/elections/g1.json"):
            return httpx.Response(200, json={"stateWide": {"path": "elections\\g1-s-20261103230500.json"}})
        return httpx.Response(404)

    snap._CLARITY_EID.clear()
    spec = "https://www.enr-scvotes.org/SC|https://scvotes.gov/elections-statistics/election-results/|^\\s*2026 (Statewide )?General Election\\s*$"

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as web:
            return await snap._resolve(web, [spec], [], "2026-11-03",
                                       ["https://static.electionresults.vermont.gov/elections/g1.json"])
    out = dict(asyncio.run(go()))
    base = "https://www.enr-scvotes.org/SC/127500/380001/json"
    assert out[us.FEEDS["sc"]["all"]] == f"{base}/ALL.json"
    assert out[us.FEEDS["sc"]["summary"]] == f"{base}/en/summary.json"
    assert out[us.FEEDS["sc"]["status"]] == f"{base}/status.json"
    assert out["vt_static_main"] == "https://static.electionresults.vermont.gov/elections/g1.json"
    assert out["vt_static_statewide"] == "https://static.electionresults.vermont.gov/elections/g1-s-20261103230500.json"
    assert snap._CLARITY_EID[spec] == "127500"                          # found once, cached


# ------------------------------------------------------------------ polls and model v2

def _poll(pid, pollster, end, n, d, r, pop="lv", start=None, subject="2026 Testland", ptype="governor", extra=()):
    return {"id": pid, "poll_type": ptype, "subject": subject, "pollster": pollster, "population": pop,
            "start_date": start or end, "end_date": end, "sample_size": n,
            "answers": [{"choice": "Dana Demo", "pct": d}, {"choice": "Rex Repp", "pct": r}, *extra]}


def test_poll_rows_one_version_per_poll_and_opponent():
    polls = [_poll("a1", "P1", "2026-10-20", 800, 48, 44, pop="rv"),
             _poll("a2", "P1", "2026-10-20", 600, 47, 46, pop="lv"),                       # same poll, LV wins
             _poll("b1", "P2", "2026-10-10", 500, 45, 40, extra=[{"choice": "Ian Indy", "pct": 41}]),
             _poll("c1", "P3", "2026-10-01", 500, 50, 50, subject="2026 Elsewhere"),
             _poll("d1", "P4", "2026-10-01", None, 50, 40),                               # no sample size
             {**_poll("e1", "P5", "2026-10-01", 400, 1, 1), "answers": [{"choice": "Rex Repp", "pct": 50}]}]
    rows = us.poll_rows(polls, "Testland", "gov", "Dana Q. Demo", "Rex Repp")
    assert list(rows["id"]) == ["b1", "a2"]
    assert rows.set_index("id").loc["a2", "s"] == pytest.approx(47 / 93)
    assert rows.set_index("id").loc["b1", "s"] == pytest.approx(45 / 85)                   # named opponent, not Indy
    no_opp = us.poll_rows(polls, "Testland", "gov", "Dana Demo")
    assert no_opp.set_index("id").loc["b1", "s"] == pytest.approx(45 / 86)                 # strongest other


def test_poll_average_rule_weights_window_and_cap():
    polls = [_poll("x1", "Big", "2026-11-01", 900, 52, 48), _poll("x2", "Big", "2026-10-31", 900, 52, 48),
             _poll("x3", "Big", "2026-10-30", 900, 52, 48), _poll("y1", "S1", "2026-10-19", 400, 45, 55),
             _poll("y2", "S2", "2026-10-05", 1600, 50, 50), _poll("y3", "S3", "2026-10-20", 900, 48, 52),
             _poll("old", "S4", "2026-09-10", 5000, 90, 10)]                              # 53 days old: out
    rows = us.poll_rows(polls, "Testland", "gov", "Dana Demo", "Rex Repp")
    a = us.poll_average(rows, asof="2026-11-02")
    r = rows[rows["id"] != "old"].copy()
    age = (pd.Timestamp("2026-11-02") - pd.to_datetime(r["end"])).dt.days
    w = np.sqrt(r["n"]) * 0.5 ** (age / 14)
    w = w / w.sum()
    big = r["pollster"] == "Big"
    assert w[big].sum() > 0.25                                                             # cap binds
    wc = w / w.groupby(r["pollster"]).transform("sum") * 0.25      # 4 pollsters: iterated cap leaves each at 25 %
    assert a["avg"] == pytest.approx(float((wc * r["s"]).sum()), abs=1e-4)
    assert a["n_polls"] == 6 and a["last_date"] == "2026-11-01" and a["n_pollsters"] == 4
    assert us.POLL_SD_HIST < a["sd"] < us.POLL_SD_HIST + 0.01
    from scipy.stats import norm
    assert a["p_win"] == pytest.approx(norm.cdf((a["avg"] - 0.5) / a["sd"]), abs=1e-4)
    assert us.poll_average(rows[rows["id"] == "old"], asof="2026-11-02") is None           # no polls in window


def _ga_v2(poll_avg: float, poll_sd: float = 0.04):
    cfg = us.load_config(ROOT / "configs" / "us2026_ga_sen_v1.toml")
    priors, _ = _synth_priors([cfg])
    pri = us.race_prior(cfg, priors)
    cfg2 = {**cfg, "model": {**cfg["model"], "swing_prior_mean": poll_avg - us.baseline_share(pri),
                             "swing_prior_sd": poll_sd}}
    return cfg, cfg2, pri


def test_v2_prior_shift_aggregates_to_poll_average():
    cfg, cfg2, pri = _ga_v2(0.531)
    res1, _ = us.project(us._frame([]), cfg, pri)
    res2, u2 = us.project(us._frame([]), cfg2, pri)
    assert res1["share_hat"] == pytest.approx(us.baseline_share(pri), abs=1e-9)
    assert res2["share_hat"] == pytest.approx(0.531, abs=1e-9)                # county baseline + shift = poll average
    shift = 0.531 - us.baseline_share(pri)
    assert np.allclose(u2["proj"], np.clip(pri["s0"] + shift, 0, 1))
    assert res2["swing_prior_weight"] == 1.0 and res2["share_sd"] > res1["share_sd"]   # + poll error, tau kept


def test_v2_swing_prior_hands_over_to_the_count():
    """Truth = the prior's baseline (polls wrong by 5 points): v2 starts at the polls and converges to v1 / the truth
    as counties report; the prior's weight falls monotonically."""
    cfg, cfg2, pri = _ga_v2(0.0)
    base = us.baseline_share(pri)
    cfg2["model"]["swing_prior_mean"] = 0.05                                        # polls say base + 5
    order = pri.sort_values("V").index[::-1]                                         # big counties first
    shares, ws = [], []
    for k in (0, 1, 5, 20, 80, len(pri) - 5):
        part = pri.loc[order[:k]]
        rows = _rows(part, part["s0"].to_numpy(), 1.0, complete=True).assign(race="sen") if k else us._frame([])
        r1, r2 = us.project(rows, cfg, pri)[0], us.project(rows, cfg2, pri)[0]
        shares.append((r1["share_hat"], r2["share_hat"]))
        ws.append(r2["swing_prior_weight"])
    assert shares[0][1] == pytest.approx(base + 0.05, abs=1e-9)
    assert all(a >= b for a, b in zip(ws, ws[1:])) and ws[0] == 1.0 and ws[-1] < 0.2
    gaps = [abs(b - a) for a, b in shares]
    assert all(x >= y - 1e-12 for x, y in zip(gaps, gaps[1:])) and gaps[-1] < 0.002


def test_swing_prior_off_is_bit_identical_to_previous_model():
    """Golden values from brunder.nowcast before the optional swing prior existed (the committed code of 2026-10-28),
    for a Brazil-style multi-state case and a US v1 race (GA Senate settings, vote-type split)."""
    from brunder import nowcast
    rng = np.random.default_rng(11)
    rows = [{"uf": u, "cd_mun": f"{u}{i}", "s0": rng.uniform(0.3, 0.7), "V0": float(rng.integers(1000, 90000))}
            for u in ["sp", "mg", "ba", "rs", "zz"] for i in range(30)]
    prior = pd.DataFrame(rows)
    f = np.where(rng.uniform(size=len(prior)) < 0.6, rng.uniform(0.1, 1.0, len(prior)), 0.0)
    n = prior["V0"] * f
    share = (prior["s0"] - 0.03 + rng.normal(0, 0.02, len(prior))).clip(0, 1)
    state = pd.DataFrame({"uf": prior["uf"], "cd_mun": prior["cd_mun"], "n": n, "a": n * share, "f": f})
    for p in (nowcast.Params(), nowcast.Params(swing_prior_mean=None, swing_prior_sd=None)):
        br = nowcast.nowcast(state, prior, p)
        assert (br["p_win"], br["margin_sd"], br["share_hat"]) == (8.342211860354956e-12, 95704.73921682102,
                                                                   0.45119738702190726)
        assert "swing_obs" not in br
    cfg = us.load_config(ROOT / "configs" / "us2026_ga_sen_v1.toml")
    priors, _ = _synth_priors([cfg])
    pri = us.race_prior(cfg, priors)
    res, _ = us.project(_rows(pri, np.clip(pri["s0"].to_numpy() - 0.03, 0, 1), 0.3).assign(race="sen"), cfg, pri)
    assert (res["p_win"], res["margin_sd"], res["share_hat"]) == (0.8983338861717141, 1279060.706500724,
                                                                  0.5358053444298526)


def test_v2_configs_only_change_the_prior():
    import hashlib
    import re as _re
    for p2 in sorted((ROOT / "configs").glob("us2026_*_v[2-9].toml")):
        p1 = p2.with_name(_re.sub(r"_v\d\.toml$", "_v1.toml", p2.name))
        c1, c2 = us.load_config(p1), us.load_config(p2)
        n = int(p2.stem[-1])
        prev = p2.with_name(_re.sub(r"_v\d\.toml$", f"_v{n - 1}.toml", p2.name))
        head = "".join(line for line in p2.read_text().splitlines() if line.startswith("#"))
        assert hashlib.sha256(prev.read_bytes()).hexdigest()[:16] in head        # cites the version it extends
        assert c2["id"] == c1["id"][:-3] + p2.stem[-3:]
        for k in ("election", "markets"):
            assert c2[k] == c1[k], (p2.name, k)
        assert {k: c2["count"][k] for k in c1["count"]} == c1["count"]
        assert set(c2["count"]) - set(c1["count"]) <= {"expected_votes", "expected_votes_level"}
        if c2["count"].get("expected_votes"):                       # v4 / Ohio v5: secondary, file in the repo
            assert c2.get("role") == "secondary" and (ROOT / c2["count"]["expected_votes"]).exists()
        assert {k: c2["model"][k] for k in c1["model"]} == c1["model"]
        assert set(c2["model"]) - set(c1["model"]) <= {"swing_prior_mean", "swing_prior_sd", "count_scaled_terms"}
        extra = set(c2["prior"]) - set(c1["prior"])
        assert {k: c2["prior"][k] for k in c1["prior"]} == c1["prior"] and extra <= {
            "poll_avg", "poll_sd", "poll_n", "poll_last", "poll_asof", "poll_p_win"}
        if "swing_prior_sd" in c2["model"]:
            assert c2["model"]["swing_prior_sd"] == (us.poll_sd_by_n(c2["prior"]["poll_n"])
                                                     if c2["model"].get("count_scaled_terms") else c2["prior"]["poll_sd"])
            pri = us.race_prior(c1, us.load_priors(us.PUBLIC_PRIORS))
            w = pri["V"]
            if c2["count"].get("expected_votes"):                   # v4: centred with its own expected-vote weights
                ev = pd.read_csv(ROOT / c2["count"]["expected_votes"], comment="#").set_index("unit_id")["exp_votes"]
                w = pri["unit_id"].map(ev).fillna(pri["V"] * c1["count"].get("turnout_ratio", 1.0))
            base = float(np.average(pri["s0"], weights=w))
            assert c2["model"]["swing_prior_mean"] == pytest.approx(c2["prior"]["poll_avg"] - base, abs=6e-5), p2.name
        if extra:
            assert 0.2 < c2["prior"]["poll_avg"] < 0.8 and _re.fullmatch(r"\d{4}-\d\d-\d\d", c2["prior"]["poll_last"])
    cfgs, v2 = us.load_races([ROOT / "configs" / "us2026_ga_sen_v1.toml", ROOT / "configs" / "us2026_ga_sen_v2.toml"])
    assert [c["id"] for c in cfgs] == ["us2026_ga_sen_v1"] and v2["ga-sen"]["v2"]["id"] == "us2026_ga_sen_v2"


# ------------------------------------------------------------------ early vote (display only)

def test_parse_ev_tx_county_counts():
    df, as_of = us.parse_ev_tx(rd("ev_tx_53815_20261027.json.gz"))
    assert as_of == "2026-10-27" and len(df) == 254 and set(df.index) == set(us.counties("tx")["unit_id"])
    a = df.loc["us-tx-48001"]                                            # Anderson (raw file: 7814 in person, 319 mail)
    assert (a["in_person"], a["mail_returned"], a["ev_total"]) == (7814, 319, 8133)
    assert (df["ev_total"] == df["in_person"] + df["mail_returned"]).all()


def test_parse_ev_fl_matches_state_totals_and_party():
    vbm, ev = rd("ev_fl_49894_VbmVoted.txt.gz"), rd("ev_fl_49894_EarlyVoted.txt.gz")
    df, as_of = us.parse_ev_fl(vbm, ev)
    assert len(df) == 67 and as_of == "2026-10-27"                       # older of the two compilations
    tot = {k: pd.read_csv(io.BytesIO(b), sep="\t", dtype=str).set_index("CountyName").loc["State Totals"]
           for k, b in (("mail", vbm), ("early", ev))}
    num = lambda s: float(s.replace(",", ""))
    assert df["mail_returned"].sum() == num(tot["mail"]["GrandTotal"])
    assert df["in_person"].sum() == num(tot["early"]["GrandTotal"])
    assert df["D"].sum() == num(tot["mail"]["TotalDem"]) + num(tot["early"]["TotalDem"])
    assert (df[["D", "R", "O"]].sum(axis=1) == df["ev_total"]).all()


def test_parse_ev_nc_types_and_party():
    raw = rd("ev_nc_counts_subset.csv.gz")
    df, _ = us.parse_ev_nc(raw, "2026-10-28")
    d = pd.read_csv(io.BytesIO(raw))
    n = d.columns[-1]
    early = d["abs_request_type"].isin(["EARLY VOTING", "ONE-STOP"])
    assert set(df.index) == {"us-nc-37001", "us-nc-37173", "us-nc-37183"}             # Alamance, Swain, Wake
    assert df["in_person"].sum() == d.loc[early, n].sum() and df["mail_returned"].sum() == d.loc[~early, n].sum()
    assert df.loc["us-nc-37183", "D"] == d.loc[(d["county_name"] == "WAKE") & (d["party_cd"] == "DEM"), n].sum()
    assert (df[["D", "R", "O"]].sum(axis=1) == df["ev_total"]).all()


def test_earlyvote_json_schema_and_stale_state(tmp_path):
    tx = lambda: us.parse_ev_tx(rd("ev_tx_53815_20261027.json.gz"))
    nc = lambda: us.parse_ev_nc(rd("ev_nc_counts_subset.csv.gz"), "2026-10-28")
    now = pd.Timestamp("2026-10-28T12:00Z")
    f = us.earlyvote(tmp_path, now, {"tx": tx, "nc": nc})
    e = json.loads(f.read_text())
    assert set(e["states"]) == {"tx", "nc"} and e["states"]["tx"]["as_of"] == "2026-10-27"
    c = e["counties"]["us-tx-48001"]
    base = pd.read_csv(us.EV_BASELINE, comment="#").set_index("unit_id")["total"]
    assert c["state"] == "tx" and c["ev_total"] == 8133 and c["by_party"] is None
    assert c["share_of_2022_total"] == round(8133 / base["us-tx-48001"], 4)
    assert set(e["counties"]["us-nc-37183"]["by_party"]) == {"D", "R", "O"}
    assert e["states"]["tx"]["totals"]["ev_total"] == sum(v["ev_total"] for v in e["counties"].values() if v["state"] == "tx")
    assert "ga" in e["not_available"]

    def broken():
        raise ConnectionError("down")
    e2 = json.loads(us.earlyvote(tmp_path, now, {"tx": broken, "nc": nc}).read_text())
    assert e2["states"]["tx"]["stale"] is True and e2["states"]["tx"]["as_of"] == "2026-10-27"
    assert e2["counties"]["us-tx-48001"]["ev_total"] == 8133 and "stale" not in e2["states"]["nc"]


# ------------------------------------------------------------------ county baselines, estimates, margins, EV-implied

def test_medsl_county_total_mode_rule():
    rows = pd.DataFrame({"county_fips": ["39001"] * 4 + ["39003"] * 2,
                         "mode": ["TOTAL", "TOTAL", "ELECTION DAY", "ELECTION DAY", "EARLY", "ELECTION DAY"],
                         "party_simplified": ["DEMOCRAT", "REPUBLICAN", "DEMOCRAT", "REPUBLICAN", "DEMOCRAT", "DEMOCRAT"],
                         "votes": ["10", "20", "7", "9", "3", "*"]})
    g = us._medsl_county(rows, "oh").set_index("unit_id")
    assert (g.loc["us-oh-39001", "dem"], g.loc["us-oh-39001", "rep"]) == (10, 20)       # TOTAL rows only
    assert g.loc["us-oh-39003", "dem"] == 3 and g.loc["us-oh-39003", "total"] == 3      # modes summed, '*' = 0
    # precinct files (NJ): same precinct as TOTAL and modes = duplicate (keep modes); TOTAL-only precincts and
    # mode-only pseudo-precincts are disjoint (summed); "COUNTY TOTAL" rows dropped; CONTEST TOTAL is not a candidate
    nj = pd.DataFrame({"county_fips": ["34003"] * 5 + ["34019"] * 2 + ["34015"] * 2,
                       "precinct": ["A 1", "A 1", "A 1", "A 2", "A 2", "B 1", "B Vote By Mail", "C 1", "COUNTY TOTAL"],
                       "mode": ["TOTAL", "EARLY", "ELECTION DAY", "TOTAL", "EARLY", "TOTAL", "MAIL-IN", "TOTAL", "TOTAL"],
                       "party_simplified": ["DEMOCRAT"] * 9, "candidate": ["H"] * 9, "votes": ["10", "4", "6", "5", "5",
                                                                                             "7", "3", "8", "8"]})
    g = us._medsl_county(nj, "nj").set_index("unit_id")
    assert (g.loc["us-nj-34003", "dem"], g.loc["us-nj-34019", "dem"], g.loc["us-nj-34015", "dem"]) == (15, 10, 8)


def test_county_baseline_public_file_covers_states_and_ohio_prior():
    b = us.load_county_baseline()
    assert b["state"].nunique() == 49 and b["unit_id"].is_unique and "ak" not in set(b["state"])  # see header
    oh = b[b["state"] == "oh"]
    assert len(oh) == 88 and (oh["pres24_dem"].sum(), oh["pres24_rep"].sum()) == (2533699, 3180116)  # certified
    pr = us.load_priors(us.PUBLIC_PRIORS)
    o = pr[pr["source"] == "oh_pres_2024"]
    assert len(o) == 88 and o["dem"].sum() == 2533699


def test_race_estimate_uniform_swing_and_no_market_fallback():
    base = us.load_county_baseline()
    r = {"race_key": "mi-sen", "state": "mi"}
    est, units = us.race_estimate(r, base, {"avg": 0.53, "sd": 0.04}, 0.04)
    V, proj = np.array(units["V0"], float), np.array(units["frames"][0]["proj"]) / 1000
    assert est["basis"] == "polls" and np.average(proj, weights=V) == pytest.approx(0.53, abs=2e-3)
    assert est["county_sd"] == pytest.approx(np.hypot(0.04, 0.04), abs=1e-4) and len(units["unit_ids"]) == 83
    est2, u2 = us.race_estimate(r, base, None, 0.04)                       # no polls: past results, never the market
    assert est2["basis"] == "past results (no recent polls)" and est2["share"] == est2["baseline_share"]
    assert u2["frames"][0]["proj"] == u2["prior"]
    assert us.race_estimate({"race_key": "xx", "state": "zz"}, base, None, 0.04) is None


def test_county_baseline_complete_for_every_race_state():
    """No county of a state with a 2026 race may drop out (the NJ bug); county sums match official state totals."""
    base = us.load_county_baseline()
    geo = {uid: name for uid, name in us._geo()}
    race_states = {r["state"] for r in json.loads(us.RACES_REGISTRY.read_text())["races"]}
    assert us.baseline_gaps(base, geo).keys().isdisjoint(race_states)
    nj = base[base["state"] == "nj"]
    assert len(nj) == 21 and ((nj["pres24_dem"] + nj["pres24_rep"]) > 0).all()
    burl = nj.set_index("unit_id").loc["us-nj-34005"]
    assert burl["pres24_dem"] > 100_000                                    # was 0 (TOTAL-mode bug)
    dev = us.baseline_state_check()
    assert all(v <= us.BASELINE_TOLERANCE for k, v in dev.items() if k in race_states and k not in us.BASELINE_PARTIAL)
    assert len(base[base["state"] == "ct"]) == 9                            # CT planning regions


def test_market_margin_and_ev_flag():
    assert us.market_margin(0.5, 8.0) == 0 and us.market_margin(0.8413, 8.0) == pytest.approx(8.0, abs=0.01)
    hist = {"states": {"nc": {"by_days_before": {"7": {"bias_mean": 6.0, "elections": []}}}}}
    s = {"as_of": "2026-10-28", "totals": {"by_party": {"D": 500, "R": 300, "O": 200}, "ev_total": 1000}}
    e = us.ev_implied("nc", s, hist)
    assert e["margin_ev_raw"] == 20.0 and e["days_before"] == 7 and e["ev_bias_hist"] == 6.0
    blk = us.ev_block({**s, **e}, {"market": 18.0})
    assert blk["margin_ev_corrected"] == 14.0 and blk["market_closer_to_raw_ev"] is True
    assert us.ev_block({**s, **e}, {"market": 13.0})["market_closer_to_raw_ev"] is False
    assert us.ev_implied("tx", {"as_of": "2026-10-28", "totals": {"by_party": None}}, hist)["margin_ev_raw"] is None


def test_scores_are_separate():
    rows = [{"counted": 0.1, "margin_model": 4.0, "votes_proj": 1100, "P": 0.7},
            {"counted": 0.9, "margin_model": 2.0, "votes_proj": 1000, "P": 0.9}]
    s = us.score_race(rows, final_share=0.51, final_votes=1000)            # final margin +2 points, D won
    assert s["margin_pts"]["mae"] == pytest.approx(1.0) and s["margin_pts"]["last"] == pytest.approx(0.0)
    assert s["turnout_rel"]["mean"] == pytest.approx(0.05)
    assert s["probability"]["brier"] == pytest.approx(((0.3) ** 2 + 0.1 ** 2) / 2)


# ------------------------------------------------------------------ model versions (v1, v2, v3 all run)

def test_load_races_attaches_every_version_and_picks_poll_model():
    cfgs, vs = us.load_races([ROOT / "configs" / "us2026_oh_sen_v1.toml"])
    assert [c["id"] for c in cfgs] == ["us2026_oh_sen_v1"]
    assert list(vs["oh-sen"]) == ["v2", "v3", "v4", "v5"] and us.poll_model(vs["oh-sen"]) == "v4"  # v5 secondary
    _, vs = us.load_races([ROOT / "configs" / "us2026_ga_sen_v1.toml"])
    assert list(vs["ga-sen"]) == ["v2", "v3", "v4"] and us.poll_model(vs["ga-sen"]) == "v3"         # v4 secondary


def test_preelection_runs_ohio_v1_v2_v3(tmp_path, monkeypatch):
    _mock_prices(monkeypatch)
    paths = [ROOT / "configs" / "us2026_oh_sen_v1.toml"]
    priors, _ = _synth_priors([us.load_config(paths[0])])
    pairs = tmp_path / "pairs.toml"
    pairs.write_text(PAIRS_PRE)
    d = us.preelection(paths, tmp_path / "site", days=1, now=NOW, priors=priors, pairs_path=pairs)
    race = json.loads((d / "meta.json").read_text())["races"][0]
    assert race["poll_model"] == "v4" and race["versions"] == {f"v{n}": f"us2026_oh_sen_v{n}.toml" for n in (2, 3, 4, 5)}
    pc = race["precount"]
    assert pc["P_v2"] == pytest.approx(pc["P"]) and pc["P_v3"] != pytest.approx(pc["P"])   # v2 = v1 (no swing prior)
    assert pc["share_v4"] == pytest.approx(pc["share_v3"]) and pc["sd_v4"] < pc["sd_v3"]     # v4: narrower, same mean
    row = json.loads((d / "timeline.json").read_text())["oh-sen"][-1]
    assert row["P_v4"] == pc["P_v4"] and row["share_v3"] == pc["share_v3"] and row["P_v5"] == pc["P_v5"]


def test_config_generator_never_touches_registered_files():
    import importlib.util
    spec = importlib.util.spec_from_file_location("us_polls", ROOT / "analysis" / "us_polls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    frozen = mod.registered_ids()
    assert {"us2026_oh_sen_v2", "us2026_oh_sen_v3", "us2026_ga_sen_v2"} <= frozen
    files = {p: p.read_bytes() for p in (ROOT / "configs").glob("us2026_*_v*.toml") if p.stem in frozen}
    mod.configs()
    mod.next_versions()
    assert all(p.read_bytes() == b for p, b in files.items())


# ------------------------------------------------------------------ v3: pre-count uncertainty = poll error alone

def test_count_scaled_terms_precount_sd_is_poll_error_plus_county_noise():
    from brunder import nowcast
    rng = np.random.default_rng(4)
    prior = pd.DataFrame({"uf": "tx", "cd_mun": [f"c{i}" for i in range(40)], "s0": rng.uniform(0.3, 0.7, 40),
                          "V0": rng.integers(1000, 90000, 40).astype(float)})
    state = prior[["uf", "cd_mun"]].assign(n=0.0, a=0.0, f=0.0)
    p = nowcast.Params(tau=0.04, sigma_uf0=0.03, sigma_mun0=0.08, swing_prior_mean=0.02, swing_prior_sd=0.0228,
                       count_scaled_terms=True)
    r = nowcast.nowcast(state, prior, p)
    R = prior["V0"]
    expect = np.sqrt(0.0228**2 + 0.08**2 * (R**2).sum() / R.sum() ** 2)        # identity: no tau, no between-region
    assert r["share_sd"] == pytest.approx(expect, rel=1e-9)
    off = nowcast.nowcast(state, prior, nowcast.Params(**{**p.__dict__, "count_scaled_terms": False}))
    assert off["share_sd"] == pytest.approx(np.sqrt(expect**2 + 0.04**2 + 0.03**2), rel=1e-9)   # v2 stacks them
    assert r["share_hat"] == off["share_hat"]


def test_count_scaled_terms_grow_with_the_count():
    cfg, cfg2, pri = _ga_v2(0.53, 0.0334)
    cfg3 = {**cfg2, "model": {**cfg2["model"], "count_scaled_terms": True}}
    order = pri.sort_values("V").index[::-1]
    gaps = []
    for k in (0, 5, 40, len(pri) - 3):
        part = pri.loc[order[:k]]
        rows = _rows(part, part["s0"].to_numpy(), 1.0, complete=True).assign(race="sen") if k else us._frame([])
        r2, r3 = us.project(rows, cfg2, pri)[0], us.project(rows, cfg3, pri)[0]
        assert r3["share_sd"] <= r2["share_sd"] + 1e-12 and r3["share_hat"] == pytest.approx(r2["share_hat"])
        gaps.append(r2["share_sd"] - r3["share_sd"])
    assert gaps[0] > 0.02 and gaps[-1] < 0.25 * gaps[0]                            # converges as the count grows


def test_poll_sd_bins_pool_and_lookup():
    hist = pd.DataFrame({"n_polls": [1, 2, 4, 4, 8, 8, 12, 15], "err": [0.05, -0.04, 0.02, -0.02, 0.03, -0.03,
                                                                         0.01, -0.01]})
    b = us.poll_sd_bins(hist)
    assert [hi for hi, _ in b] == [2, 10, 10**6]                                    # 6-10 (0.03) > 3-5 (0.02): pooled
    assert b[1][1] == pytest.approx(np.sqrt((2 * 0.02**2 + 2 * 0.03**2) / 4), abs=1e-4)
    assert us.poll_sd_by_n(2) == 0.0429 and us.poll_sd_by_n(7) == 0.0334 and us.poll_sd_by_n(17) == 0.0228


# ------------------------------------------------------------------ hub data (site/data/us2026/hub)

HUB = ROOT / "site" / "data" / "us2026" / "hub"


@pytest.mark.parametrize("name", ["polls", "pollsters", "markets", "backtest", "census"])
def test_hub_file_size_and_sources(name):
    f = HUB / f"{name}.json"
    assert f.exists() and f.stat().st_size < 1.5e6
    d = json.loads(f.read_text())
    assert d.get("source") or d.get("sources")


def test_hub_polls_weights_match_the_average():
    d = json.loads((HUB / "polls.json").read_text())
    assert d["source"]["licence"] == "CC BY 4.0" and len(d["races"]) >= 40
    for rk, r in d["races"].items():
        used = [p for p in r["polls"] if p["weight"]]
        assert all(p["excluded"] is None for p in used) and all(p["weight"] is None for p in r["polls"] if p["excluded"])
        if r["average"]:
            assert sum(p["weight"] for p in used) == pytest.approx(1.0, abs=2e-3)
            avg = sum(p["weight"] * p["dem2"] for p in used)
            assert avg == pytest.approx(r["average"]["avg"], abs=2e-3), rk
            assert len(r["history"]) == 60 and all(len(h) == 3 for h in r["history"])


def test_hub_pollsters_backtest_markets_census_schemas():
    p = json.loads((HUB / "pollsters.json").read_text())["pollsters"]
    assert {"pollster", "polls", "races", "cycles", "lean", "rmse", "in_2026"} <= set(p[0]) and any(x["in_2026"] for x in p)
    b = json.loads((HUB / "backtest.json").read_text())
    assert "pre-count only" in b["label"] and {"model", "baseline"} == set(b["summary"])
    for s in b["summary"].values():
        assert {"races", "cover68", "cover90", "mae_margin", "brier", "log_loss", "calibration"} <= set(s)
    assert {r["cycle"] for r in b["races"]} == {2018, 2022}
    m = json.loads((HUB / "markets.json").read_text())
    assert {"races", "accuracy", "sources"} <= set(m) and {"markets", "brier", "bins", "coverage"} <= set(m["accuracy"])
    for v in m["races"].values():
        for k in ("pm", "kalshi"):
            if v[k]:
                assert all(len(x) in (2, 3) and 0 <= (x[1] if x[1] is not None else 0) <= 1 for x in v[k]["daily"])
    c = json.loads((HUB / "census.json").read_text())
    geo = {uid for uid, _ in us._geo()}
    assert set(c["counties"]) <= geo and len(c["counties"]) > 3000 and "statewide" in c["redistricting"]
    nj = c["counties"]["us-nj-34005"]
    assert {"pop2020", "pop2025", "change_pct", "age65_pct", "ba_plus_pct", "hispanic_pct", "black_pct",
            "median_income"} == set(nj)


# ------------------------------------------------------------------ turnout display fields

def test_turnout_row_surprise_and_hot_cold():
    pri = _pri()
    cfg = _cfg(f_complete=1.0)
    prof = us.turnout_profile(cfg, pri).assign(lowprop=np.linspace(-2, 2, len(pri)))
    exp = prof["turnout_exp"].to_numpy()
    surprise = np.exp(-0.1 * prof["lowprop"].to_numpy())                # high-propensity counties run hot
    share = np.where(prof["lowprop"] < 0, 0.6, 0.4)
    rows = _rows(pri.assign(V=exp * surprise), share, 1.0, complete=True)
    res, units = us.project(rows, cfg, pri)
    r = us.turnout_row(units, prof, res["rem_total"])
    assert r["counties_reported"] == len(pri) and r["turnout_surprise_corr"] < -0.95
    assert r["turnout_surprise_slope"] == pytest.approx(-0.1, abs=0.01)
    assert r["margin_hot"] > 0 > r["margin_cold"]                        # D counties hot, R counties cold
    assert r["turnout_vs_expected"] == pytest.approx(100 * (exp * surprise).sum() / exp.sum(), rel=1e-3)
    fr = us.turnout_frame(units, prof)
    assert fr["turnout_ratio_live"][0] == round(1000 * surprise[0])
    none = us.turnout_row(None, None, None)
    assert all(v is None for v in none.values())


def test_senate_control_correlated_vs_independent():
    races = [{"polls": {"avg": 0.52, "n_polls": 12}, "pm": None}, {"polls": None, "pm": {"price": 0.5}},
             {"polls": {"avg": 0.47, "n_polls": 2}, "pm": None}]
    ov = {"offices": {"senate": {"holdovers": {"D": 49, "R": 48, "I": 0}, "races": races}}}
    c = us.senate_control(ov)
    assert {"control_p_indep", "control_p_corr", "seats_80_indep", "seats_80_corr", "control_note"} <= set(c)
    assert 0 < c["control_p_corr"] < 1 and 0 < c["control_p_indep"] < 1
    lo_i, hi_i = c["seats_80_indep"]
    lo_c, hi_c = c["seats_80_corr"]
    assert hi_c - lo_c >= hi_i - lo_i                                    # a shared error widens the seat range
    assert us.senate_control(ov, rho=0.0)["control_p_corr"] == pytest.approx(c["control_p_indep"], abs=0.01)
