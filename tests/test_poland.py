import gzip

import numpy as np
import pandas as pd
import pytest

from brunder import nowcast as nc
from brunder import poland as pl


# ---- a hand-encoded PKW blob. The numbers are the real ones of the first runoff version (generated 2025-06-01
# 18:59:11 UTC, before the polls closed): 129 abroad obwody counted, 37,016 Trzaskowski and 39,471 Nawrocki votes.
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _sint(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def _f(num: int, v) -> bytes:
    if isinstance(v, bytes):
        return _varint(num << 3 | 2) + _varint(len(v)) + v
    return _varint(num << 3) + _varint(v)


def _stat(registered: int, n_obw: int, counted: int, elig: int, valid: int, votes: dict[int, int]) -> bytes:
    pro = _f(5, _sint(elig)) + _f(27, _sint(valid)) + b"".join(
        _f(28, _f(3, _sint(v)) + _f(8, _sint(c))) for c, v in votes.items())
    return _f(5, registered) + _f(12, n_obw) + _f(16, _f(2, counted) + _f(3, pro))


def _unit(field: int, teryt: int, name: bytes, stat: bytes) -> bytes:
    path = _f(1, _sint(teryt)) + _f(2, name) if field == 3 else _f(3, _sint(teryt))
    return _f(field, _f(1, path) + _f(4, stat))


def _blob() -> bytes:
    T, N = pl.COMMITTEE["trzaskowski"], pl.COMMITTEE["nawrocki"]
    abroad = _stat(600_000, 511, 129, 89_778, 76_487, {T: 37_016, N: 39_471})
    nat = _stat(29_000_000, 32_143, 129, 89_778, 76_487, {T: 37_016, N: 39_471})
    woj = _stat(2_138_556, 2_354, 0, 0, 0, {})
    cls = _f(3, b"".join(_f(c, 100 * (i + 1)) for i, c in enumerate(pl.CLASS_FIELDS))) + \
        _f(7, _f(1, pl.CANDIDATE["trzaskowski"]) + _f(2, _f(7, 30) + _f(14, 70)))
    raw = (_unit(3, 20000, "dolnośląskie".encode(), woj) + _unit(18, 149900, b"", abroad) + _f(4, nat) + _f(16, cls))
    return gzip.compress(raw)


def test_parse_blob_reads_units_national_and_classes():
    d = pl.parse_blob(_blob())
    assert d["units"]["02"]["registered"] == 2_138_556 and d["units"]["02"]["n_obw"] == 2_354
    ab = d["units"][pl.ABROAD]
    assert (ab["counted"], ab["valid"], ab["elig"]) == (129, 76_487, 89_778)
    assert ab["votes"] == {pl.COMMITTEE["trzaskowski"]: 37_016, pl.COMMITTEE["nawrocki"]: 39_471}
    assert d["national"]["valid"] == 76_487
    assert d["classes"]["elig"] == [100, 200, 300, 400, 500, 600, 700, 800]
    assert d["classes"]["votes"][pl.CANDIDATE["trzaskowski"]][0] == 30
    assert d["classes"]["votes"][pl.CANDIDATE["trzaskowski"]][-1] == 70


def test_parse_blob_accepts_raw_or_gzipped_bytes():
    assert pl.parse_blob(gzip.decompress(_blob()))["national"]["valid"] == 76_487


def test_margins_of_a_blob(tmp_path):
    f = tmp_path / "pl_po_woj.1748804351.blob"
    f.write_bytes(_blob())
    m = pl.margins(f)
    assert m["abroad"]["a"] == 37_016 and m["abroad"]["n"] == 76_487
    assert m["abroad"]["f"] == pytest.approx(89_778 / 600_000)
    assert m["Uw"].sum() == 0 and m["obw"] == 129 and m["obw_total"] == 511 + 2_354
    assert pl.blob_version(f) == 1748804351
    assert pl.version_time(1748804351) == pd.Timestamp("2025-06-01T18:59:11Z")


def test_fit_fraction_reproduces_both_margins():
    rng = np.random.default_rng(1)
    E = rng.uniform(1e4, 1e6, (16, 8))
    g_true = 1 / (1 + np.exp(-(rng.normal(0, 1, (16, 1)) + rng.normal(0, 1.5, (1, 8)))))
    Uw, Uk = (E * g_true).sum(1), (E * g_true).sum(0)
    g = pl.fit_fraction(E, Uw, Uk)
    assert np.allclose((E * g).sum(1), Uw, rtol=1e-4)
    assert np.allclose((E * g).sum(0), Uk, rtol=1e-4)
    assert (g >= 0).all() and (g <= 1).all()


def test_fit_fraction_nothing_counted_and_everything_counted():
    E = np.full((3, 2), 1000.0)
    assert (pl.fit_fraction(E, np.zeros(3), np.zeros(2)) == 0).all()
    g = pl.fit_fraction(E, np.full(3, 2000.0), np.full(2, 3000.0))
    assert np.allclose(g, 1.0, atol=1e-6)


def test_rake_matches_margins_and_keeps_zero_cells():
    M = np.array([[1.0, 2.0, 0.0], [3.0, 1.0, 1.0]])
    rows, cols = np.array([10.0, 20.0]), np.array([12.0, 10.0, 8.0])
    R = pl.rake(M, rows, cols)
    assert np.allclose(R.sum(1), rows) and np.allclose(R.sum(0), cols) and R[0, 2] == 0
    assert pl.rake(np.zeros((2, 2)), np.ones(2), np.ones(2)).sum() == 0


def test_band_probabilities_partition_the_line():
    s, sd = np.array([0.48]), np.array([0.01])
    bands = [("n", 0, 4), ("n", 4, 8), ("n", 8, 100), ("t", 0, 4), ("t", 4, 8), ("t", 8, 100)]
    assert sum(pl.band_p(s, sd, a, lo, hi)[0] for a, lo, hi in bands) == pytest.approx(1.0)
    # Trzaskowski at 48% loses by exactly 4 points: the 4-8 band holds Phi(0) - Phi(-2) of the mass
    assert pl.band_p(s, sd, "n", 4, 8)[0] == pytest.approx(0.4772, abs=1e-3)
    assert pl.band_p(np.array([0.5]), np.array([0.001]), "n", 0, 4)[0] == pytest.approx(0.5, abs=0.01)


def _toy():
    prior = pd.DataFrame({"uf": ["02", "02", "14", "14", "zz"], "cd_mun": ["02-0", "02-1", "14-0", "14-1", "99-0"],
                          "s0": [0.40, 0.60, 0.35, 0.65, 0.70], "V0": [5e4, 4e4, 6e4, 9e4, 3e4]})
    state = pd.DataFrame({"uf": ["02", "02", "14", "14", "zz"], "cd_mun": ["02-0", "02-1", "14-0", "14-1", "99-0"],
                          "n": [4.0e4, 5e3, 5.5e4, 1e4, 1.5e4], "a": [1.5e4, 3e3, 1.8e4, 6e3, 1.0e4],
                          "f": [0.8, 0.1, 0.9, 0.1, 0.5]})
    return state, prior


def test_project_agrees_with_nowcast_aggregate():
    state, prior = _toy()
    p = nc.Params(tau=0.005, kappa=100_000.0, rho=0.8)
    o = nc.nowcast(state, prior, p)
    df = pl.project(state, prior, p)
    share = (df["a"] + df["R"] * df["s_hat"]).sum() / (df["n"] + df["R"]).sum()
    assert share == pytest.approx(o["share_hat"], abs=1e-12)
    u = pl.unit_projection(state, prior, p)
    assert list(u.index) == pl.UNITS
    assert u.loc["02", "counted"] == pytest.approx(df[df["uf"] == "02"]["n"].sum() / (df[df["uf"] == "02"]["n"] + df[df["uf"] == "02"]["R"]).sum())
    assert np.isnan(u.loc["04", "raw"])


def test_national_tally():
    state, _ = _toy()
    share, n = pl.national(state)
    assert n == pytest.approx(state["n"].sum()) and share == pytest.approx(state["a"].sum() / state["n"].sum())
    assert np.isnan(pl.national(state.assign(n=0.0, a=0.0))[0])


# ---- tests on the downloaded data (skipped where $BRUNDER_DATA has no Polish files)
needs_data = pytest.mark.skipif(not (pl.RAW_PL / "blobs" / "r2").exists() or not (pl.RAW_PL / "final").exists(),
                                reason="no Polish data in $BRUNDER_DATA/raw/poland")


@needs_data
def test_last_runoff_version_is_the_official_final():
    d = pl.parse_blob(pl.versions(2)[-1].read_bytes())
    u = d["units"]
    t = sum(x["votes"].get(pl.COMMITTEE["trzaskowski"], 0) for x in u.values())
    n = sum(x["votes"].get(pl.COMMITTEE["nawrocki"], 0) for x in u.values())
    assert (t, n) == (10_237_286, 10_606_877)            # PKW final: 10,237,677 and 10,606,877 (391 votes later)
    assert sum(x["counted"] for x in u.values()) == sum(x["n_obw"] for x in u.values()) == 32_143
    assert d["national"]["valid"] == t + n + 0           # every valid runoff vote is for one of the two


@needs_data
def test_gmina_csv_totals_and_class_assignment_match_pkw_class_tables():
    g = pl.gminas(2025, 2, "TRZASKOWSKI", "NAWROCKI")
    dom = g[g["unit"].isin(pl.WOJ)]
    assert 2400 < len(dom) < 2500 and "146500" in set(dom["key"]) and "146501" not in set(dom["key"])  # Warsaw as one gmina
    assert g[g["unit"] == pl.ABROAD]["E"].iloc[0] == pytest.approx(721_297, abs=1_000)
    C = pl.Classes()
    k = C.of(dom["key"], dom["E"])
    blob = pl.parse_blob(pl.versions(2)[-1].read_bytes())["classes"]["elig"]
    by_class = dom.assign(k=k).groupby("k")["E"].sum()
    for j in (5, 6, 7):                                          # the cities: exact; small gminas: within 1%
        assert by_class[j] == blob[j]
    assert np.allclose(by_class.to_numpy(), blob, rtol=0.01)


@needs_data
def test_cell_state_reproduces_the_published_marginals():
    R = pl.Replay(2)
    path = pl.versions(2)[22]                                    # 2025-06-02 00:19 UTC, 81% counted
    m = pl.margins(path)
    st = R.state(path)
    dom = st[st["uf"] != "zz"]
    w = dom["cd_mun"].str.split("-").str[0]
    assert np.allclose(dom.groupby(w)["a"].sum().reindex(pl.WOJ).to_numpy(), m["Aw"], rtol=1e-4)
    k = dom["cd_mun"].str.split("-").str[1].astype(int)
    assert np.allclose(dom.groupby(k)["a"].sum().to_numpy(), m["Ak"], rtol=1e-4)
    assert dom["f"].between(0, 1).all()


@needs_data
def test_runoff_model_calls_nawrocki_once_the_cities_are_in():
    R = pl.Replay(2)
    p = nc.Params(tau=0.005, kappa=100_000.0, rho=0.8)
    last = nc.nowcast(R.state(pl.versions(2)[-1]), R.prior, p)
    assert last["p_win"] == 0.0 and last["share_hat"] == pytest.approx(0.4911, abs=1e-3)
    assert last["counted_frac"] > 0.999


def test_pooled_rule_weights_nights_equally_and_picks_smallest_tau():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
    import pl_calibrate_pooled as pp

    def night(cov, mae):
        return pd.DataFrame({"tau": [0.0, 0.01, 0.02], "sigma_mun0": 0.06, "kappa": 1e5, "rho": 0.5,
                             "n": 10, "coverage90": cov, "mean_z": 0.0, "mae_pt": mae})
    m = pp.pooled(night([0.5, 1.0, 1.0], 1.0), night([0.0, 0.5, 1.0], 3.0))
    assert list(m["coverage90"]) == [0.25, 0.75, 1.0] and set(m["mae_pt"]) == {2.0}
    assert pp.choose(m)["tau"] == 0.02
    # a night with few frames counts as much as one with many: 1/1 and 9/18 average to 0.75, not 10/19
    assert pp.pooled(night([1.0, 1.0, 1.0], 1), night([0.5, 0.5, 0.5], 1))["coverage90"].iloc[0] == 0.75
