"""US midterms: feed parsers on real snapshots (tests/fixtures/us), Census unit ids, model sanity, export."""

import base64
import csv
import gzip
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

@pytest.mark.parametrize("st", ["ca", "tx", "oh", "ga"])
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
        assert set(row) == {"t", "counted", "raw", "share", "sd", "P", "pm", "kalshi"}
        assert 0 <= row["counted"] <= 1 and 0 <= row["P"] <= 1 and row["kalshi"] is None
    assert tl[-1]["pm"] == pytest.approx(0.96) and tl[-1]["counted"] > tl[0]["counted"]
    units = json.loads((d / "units" / f"{rid}.json").read_text())
    n = len(units["unit_ids"])
    assert n == 58 and all(len(units[k]) == n for k in ("region", "names", "prior", "V0"))
    assert 1 <= len(units["frames"]) <= 60
    for fr in units["frames"]:
        assert all(len(fr[k]) == n and all(isinstance(x, int) for x in fr[k]) for k in ("f", "a", "proj"))
        assert all(0 <= x <= 1000 for x in fr["f"])
    assert json.loads((d / "units.json").read_text())["race_id"] == rid
