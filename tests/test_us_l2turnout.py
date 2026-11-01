"""Unit tests of brunder.us_l2turnout numerics (synthetic frames; the RDH files are not needed)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from brunder import us_l2turnout as L


def test_fix_fips_restores_the_stripped_county_padding():
    s = pd.Series(["161", "1611", "16111", "16001"])
    assert list(L.fix_fips(s, "ID")) == ["16001", "16011", "16111", "16001"]
    assert list(L.fix_fips(pd.Series(["1001", "01003"]), "AL")) == ["01001", "01003"]       # state 01: a plain short code
    assert list(L.fix_fips(pd.Series(["251"]), "MA")) == ["25001"]


def test_canonical_column_names():
    assert L._canon("g20221108_voted_gender_f") == "2022.voted.gen.f"
    assert L._canon("g20201103_reg_hisp") == "2020.reg.eth.hisp"
    assert L._canon("g20181106_voted_all") is None                    # only the two generals of the files are kept
    assert L._canon("reg_age_65_74", 2024) == "2024.reg.age.65_74"
    assert L._canon("voted_party_ind_npp", 2024) == "2024.voted.party.npp"
    assert L._canon("reg_eur_party_democratic", 2024) == "2024.reg.ep.eur_dem"
    assert L._canon("party_dem", None, snap=True) == "snap.party.dem"
    assert L._canon("party_dem") is None


def _county_frame():
    """Two states: counties 1 and 2 of state A differ in ethnic mix, state B is a copy with its own rates."""
    rows = [("A", "a1", 1000, 800, 0, 0, 200, 100), ("A", "a2", 1000, 0, 1000, 0, 0, 0), ("B", "b1", 500, 500, 0, 0, 0, 0)]
    df = pd.DataFrame(rows, columns=["st", "fips", "n", "eur", "aa", "hisp", "unk", "_"])
    out = pd.DataFrame(index=df["fips"])
    out["st"] = df["st"].values
    out["2020.reg.all"] = df["n"].values.astype(float)
    reg = {"eur": df["eur"], "aa": df["aa"], "hisp": df["hisp"], "esa": 0, "oth": 0, "unk": df["unk"]}
    rate = {"eur": 0.8, "aa": 0.5, "hisp": 0.4, "esa": 0.7, "oth": 0.7, "unk": 0.6}
    for g, v in reg.items():
        out[f"2020.reg.eth.{g}"] = np.asarray(v, float)
        out[f"2020.voted.eth.{g}"] = np.asarray(v, float) * rate[g]
    out["2020.voted.all"] = sum(out[f"2020.voted.eth.{g}"] for g in reg)
    return out


def test_composition_model_recovers_the_ballots_it_was_fitted_on():
    d = _county_frame()
    rates = L.pooled_rates(d, 2020, ("eth",))
    assert abs(rates[("A", "eth")]["eur"] - 0.8) < 1e-12 and abs(rates[("A", "eth")]["aa"] - 0.5) < 1e-12
    m = L.model_ballots(d, 2020, rates, ("eth",))
    # a state with one group per county: the model reproduces the voted counts exactly (up to the state's pooled rates)
    assert abs(m.loc["b1", "ballots"] - d.loc["b1", "2020.voted.all"]) < 1e-6
    assert np.allclose(m["ballots"].loc[["a1", "a2"]].sum(), d.loc[["a1", "a2"], "2020.voted.all"].sum(), rtol=1e-9)
    # composition index: the county with more high-turnout (European) voters is above 1, the other below
    assert m.loc["a1", "idx_eth"] > 1 > m.loc["a2", "idx_eth"]


def _sub():
    """One (county, cd119) group split over two 2026 districts, plus a second group whose 2026 districts do not match `old`."""
    sub = pd.DataFrame({
        "st": ["TX"] * 4, "county": ["48001"] * 2 + ["48003"] * 2, "cd119": ["01", "01", "02", "02"], "cd120": ["07", "08", "07", "09"],
        "2022.voted.all": [300.0, 100.0, 50.0, 50.0], "2024.voted.all": [500.0, 300.0, 10.0, 90.0],
        "2024.reg.all": [800.0, 700.0, 100.0, 100.0], "2024.reg.party.dem": [400.0, 200.0, 40.0, 60.0], "2024.reg.party.rep": [200.0, 400.0, 40.0, 20.0],
        "2024.reg.eth.aa": [80.0, 70.0, 10.0, 10.0], "2024.reg.eth.hisp": [80.0, 70.0, 10.0, 10.0], "2024.reg.eth.esa": [8.0, 7.0, 1.0, 1.0],
        "blocks": [10, 10, 5, 5]})
    old = pd.DataFrame({"st": ["TX"] * 3, "county": ["48001", "48001", "48003"], "cd": [1, 1, 2], "cd26": [7, 8, 7],
                        "d24": [600.0, 400.0, 500.0], "r24": [400.0, 600.0, 500.0], "t24": [1010.0, 1010.0, 1010.0]})
    return old, sub


def test_split_shares_sum_to_one_and_fall_back_on_blocks():
    _, sub = _sub()
    sub.loc[2:3, ["2022.voted.all", "2024.voted.all"]] = 0.0
    sh = L.split_shares(sub, "2024.voted.all")
    assert np.allclose(sh.groupby([sub["st"], sub["county"], sub["cd119"]]).sum(), 1.0)
    assert np.allclose(sh.iloc[2:], [0.5, 0.5])                               # zero key: the block counts decide


def test_shift_features_are_centred_on_the_weights():
    _, sub = _sub()
    w = sub["2024.voted.all"]
    X = L.shift_features(sub.assign(w=w), "w", snap=False)
    g = [sub[c] for c in L.GROUP]
    assert np.allclose((X.mul(w, axis=0)).groupby(g).sum().to_numpy(), 0.0, atol=1e-9)
    assert X["dr"].iloc[0] > 0 > X["dr"].iloc[1]                              # the first sub-piece is the more Democratic one


def test_resplit_preserves_the_parent_piece_and_keeps_unmatched_groups():
    old, sub = _sub()
    new = L.resplit_pieces(old, sub, None)
    a = new[new["county"] == "48001"]
    assert np.allclose(a[["d24", "r24"]].sum().to_numpy(), [1000.0, 1000.0])
    ws = ((300 / 400 + 500 / 800) / 2, (100 / 400 + 300 / 800) / 2)          # mean of the 2022 and 2024 vote shares
    assert np.allclose(a["d24"] + a["r24"], [2000 * ws[0], 2000 * ws[1]])
    assert set(a["how"]) == {"L2"}
    b = new[new["county"] == "48003"]
    assert set(b["how"]) == {"pop"} and np.allclose(b["d24"], [500.0])         # 2026 districts {7} (old) vs {7, 9} (L2): the old split stays
    shifted = L.resplit_pieces(old, sub, np.array([0.5, 0.0, 0.0, 0.0]))
    s = shifted[shifted["county"] == "48001"]
    assert abs(s["d24"].sum() - 1000.0) < 1e-6                                # the shift moves votes between sub-pieces, not out of the parent
    assert s["d24"].iloc[0] / (s["d24"] + s["r24"]).iloc[0] > a["d24"].iloc[0] / (a["d24"] + a["r24"]).iloc[0]


def test_fit_shift_recovers_a_planted_coefficient():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, 4)), columns=list(L.SHIFT_FEATS))
    y = X["dr"].to_numpy() * 0.6 - X["hisp"].to_numpy() * 0.3 + rng.normal(scale=0.01, size=300)
    b = L.fit_shift(X, y, rng.uniform(1, 5, 300))
    assert np.allclose(b, [0.6, 0.0, -0.3, 0.0], atol=0.02)
