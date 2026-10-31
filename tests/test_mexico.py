"""brunder.mexico: PREP / cómputos parsers on real INE rows (tests/fixtures/mexico, INE "Términos de libre uso"),
publication times, priors, the incremental count replay and the per-unit projection."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import mexico as mx
from brunder import nowcast

FX = Path(__file__).parent / "fixtures" / "mexico"
A24 = ["MORENA", "PT", "PVEM", "PVEM_PT_MORENA", "PVEM_PT", "PVEM_MORENA", "PT_MORENA"]
B24 = ["PAN", "PRI", "PRD", "PAN-PRI-PRD", "PAN-PRI", "PAN-PRD", "PRI-PRD"]
A18 = ["PT", "MORENA", "PES", "C_PT_MORENA_PES", "C_PT_MORENA", "C_PT_PES", "C_MORENA_PES"]
B18 = ["PAN", "PRI", "PRD", "PVEM", "MC", "PANAL", "C_PAN_PRD_MC", "C_PAN_PRD", "C_PAN_MC", "C_PRD_MC",
       "C_PRI_PVEM_PANAL", "C_PRI_PVEM", "C_PRI_PANAL", "C_PVEM_PANAL"]
A18CW = ["PT", "MORENA", "ENCUENTRO SOCIAL", "PT_MORENA_PES", "PT_MORENA", "PT_PES", "MORENA_PES"]
B18CW = ["PAN", "PRI", "PRD", "PVEM", "MOVIMIENTO CIUDADANO", "NUEVA ALIANZA", "PAN_PRD_MC", "PAN_PRD", "PAN_MC",
         "PRD_MC", "PRI_PVEM_NA", "PRI_PVEM", "PRI_NA", "PVEM_NA"]
A12, B12 = ["PRD", "PT", "MC", "PRD_PT_MC", "PRD_PT", "PRD_MC", "PT_MC"], ["PAN", "PRI", "PVEM", "PRI_PVEM"]


@pytest.fixture(scope="module")
def prep24():
    return mx.read_prep(FX / "PRES_2024_sample.csv", A24, B24)


def test_prep2024_header_and_catalogue(prep24):
    d, h = prep24
    assert h["utc_offset_h"] == -6
    assert h["cut_utc"] == pd.Timestamp("2024-06-04T02:05:00Z")       # "03/06/2024 20:05 (UTC-6)"
    assert h["out_of_catalogue"] == 1 and h["expected"] == 7 == len(d)
    keys = set(zip(d["uf"], d["cd_mun"]))
    assert keys == {("zz", "m01"), ("01", "0338"), ("zz", "a"), ("01", "0362"), ("01", "0344")}


def test_prep2024_votes_times_status(prep24):
    d, _ = prep24
    b = d[d["acta"] == "010338B01001"].iloc[0]
    # MORENA 193 + PT 15 + PVEM 17 + PVEM_PT_MORENA 10 + PVEM_PT 1 + PVEM_MORENA 1 + PT_MORENA 2
    assert (b["a"], b["b"], b["ln"], b["counted"]) == (239, 134, 660, True)
    assert b["t_verif"] == pd.Timestamp("2024-06-03T08:29:52Z")       # 02:29:52 central time (UTC-6)
    e = d[d["acta"] == "010344E01001"].iloc[0]                         # 'Sin dato' cells count as 0, as in the PREP
    assert (e["a"], e["b"]) == (268, 190)
    pending = d[d["acta"] == "010338C02001"].iloc[0]                   # not yet captured: '-' everywhere
    assert not pending["counted"] and pd.isna(pending["t_verif"]) and pending["ln"] == 659
    assert d.loc[d["tipo"] == "M", "district"].iloc[0] == 0


def test_prep2018_pipe_latin1_summer_time():
    d, h = mx.read_prep(FX / "presidencia_2018_sample.csv", A18, B18)
    assert h["utc_offset_h"] == -5 and h["out_of_catalogue"] == 1 and len(d) == 4
    m = d[d["tipo"] == "M"].iloc[0]
    assert (m["uf"], m["cd_mun"], m["a"], m["b"]) == ("zz", "m01", 463, 334)
    assert m["t_verif"] == pd.Timestamp("2018-07-02T12:46:08Z")        # 07:46:08 central summer time (UTC-5)


def test_computos_formats_agree_with_prep():
    cw = mx.read_computos(FX / "computos_2018_sample.csv", A18CW, B18CW).set_index(["uf", "cd_mun"])
    prep, _ = mx.read_prep(FX / "presidencia_2018_sample.csv", A18, B18)
    s = prep[prep["cd_mun"] == "0338"]                                 # the same two actas, counted in both
    assert cw.loc[("01", "0338"), "a"] == s["a"].sum() and cw.loc[("01", "0338"), "b"] == s["b"].sum()
    assert cw.loc[("zz", "m01"), "a"] == 463
    sec = mx.read_computos(FX / "2012_SEE_PRE_NAL_SEC_sample.csv", A12, B12).set_index(["uf", "cd_mun"])
    assert tuple(sec.loc[("zz", "m01"), ["a", "b", "ln"]]) == (21, 67, 166)    # section 0 = abroad
    assert tuple(sec.loc[("01", "0338"), ["a", "b", "ln"]]) == (68, 671, 1515)


def test_sicee2024_bom_with_latin1_body():
    b = [c.replace("-", "_") for c in B24]
    sec = mx.read_computos(FX / "2024_SEE_PRE_NAL_SEC_sample.csv", A24, b).set_index(["uf", "cd_mun"])
    assert tuple(sec.loc[("zz", "m01"), ["a", "b", "ln"]]) == (861, 1148, 2528)    # = the PREP abroad acta
    assert tuple(sec.loc[("05", "0003"), ["a", "b"]]) == (821, 636)                  # row with 'ACUÑA' in Latin-1
    assert tuple(sec.loc[("01", "0338"), ["a", "b", "ln"]]) == (706, 365, 1979)


def test_visible_at_cut_rule():
    op = pd.Timestamp("2024-06-03T02:00:00Z")
    v = pd.Series(pd.to_datetime(["2024-06-03T02:31:30Z", "2024-06-03T02:34:00Z", "2024-06-02T23:00:00Z", None]))
    got = mx.visible_at(v, op)
    assert got.iloc[0] == pd.Timestamp("2024-06-03T02:35:00Z")        # cut 02:30 (holds up to ~02:33:30), +5 min
    assert got.iloc[1] == pd.Timestamp("2024-06-03T02:40:00Z")        # next cut
    assert got.iloc[2] == pd.Timestamp("2024-06-03T02:05:00Z")        # verified before opening: first cut
    assert pd.isna(got.iloc[3])


def test_prior_imputes_new_units_from_district():
    cat = pd.DataFrame({"uf": ["01", "01", "01", "zz"], "cd_mun": ["0001", "0002", "0003", "m01"],
                        "ent": [1, 1, 1, 1], "district": [1, 1, 1, 0], "k": [1, 1, 1, 1],
                        "ln": [1000.0, 500.0, 800.0, 100.0]})
    prev = pd.DataFrame({"uf": ["01", "01", "zz", "01"], "cd_mun": ["0001", "0002", "m01", "9999"],
                         "a": [300.0, 100.0, 10.0, 5.0], "b": [300.0, 300.0, 30.0, 5.0], "ln": [1000.0, 1000.0, 50.0, 10.0]})
    p = mx.prior(prev, cat).set_index("cd_mun")
    assert "9999" not in p.index                                        # gone from the catalogue: dropped
    assert p.loc["0001", "s0"] == 0.5 and p.loc["0001", "V0"] == 600.0
    assert p.loc["0002", "V0"] == pytest.approx(500 * 400 / 1000)      # lista nominal now x two-way per voter then
    assert p.loc["0003", "imputed"] and p.loc["0003", "s0"] == pytest.approx(400 / 1000)
    assert p.loc["0003", "V0"] == pytest.approx(800 * 1000 / 2000)
    assert p.loc["m01", "V0"] == pytest.approx(100 * 40 / 50)


def _toy():
    rng = np.random.default_rng(1)
    units = pd.DataFrame({"uf": np.repeat(["01", "02", "zz"], [30, 30, 2]),
                          "cd_mun": [f"{i:04d}" for i in range(60)] + ["m01", "m02"]})
    pri = units.assign(s0=rng.uniform(0.3, 0.7, len(units)), V0=rng.uniform(200, 900, len(units)), imputed=False)
    cat = units.assign(ent=1, district=1, k=rng.integers(1, 4, len(units)), ln=1000.0)
    rows = []
    for u in cat.itertuples():
        for j in range(u.k):
            a, b = rng.integers(50, 200), rng.integers(50, 200)
            rows.append({"uf": u.uf, "cd_mun": u.cd_mun, "acta": f"{u.cd_mun}-{j}", "a": float(a), "b": float(b),
                         "counted": j != 0 or u.Index % 5 != 0,
                         "visible": pd.Timestamp("2024-06-03T02:00Z") + pd.Timedelta(minutes=int(rng.integers(0, 120)))})
    return pri, cat, pd.DataFrame(rows)


def test_count_states_incremental_matches_direct_sum():
    _, cat, actas = _toy()
    times = pd.date_range("2024-06-03T02:00Z", periods=13, freq="10min")
    for t, st in mx.count_states(actas, cat, times):
        c = actas[actas["counted"] & (actas["visible"] <= t)]
        g = c.groupby(["uf", "cd_mun"]).agg(a_d=("a", "sum"), n_d=("b", "sum"), k=("a", "size"))
        m = st.set_index(["uf", "cd_mun"]).join(g).fillna(0.0)
        assert np.allclose(m["a"], m["a_d"]) and np.allclose(m["n"], m["a_d"] + m["n_d"])
        assert np.allclose(m["f"] * cat.set_index(["uf", "cd_mun"])["k"].reindex(m.index), m["k"])


def test_project_units_sums_to_nowcast_share():
    pri, cat, actas = _toy()
    p = nowcast.Params(tau=0.01, unpooled=("zz",))
    times = pd.DatetimeIndex([pd.Timestamp("2024-06-03T02:30Z"), pd.Timestamp("2024-06-03T03:30Z")])
    for _, st in mx.count_states(actas, cat, times):
        u = mx.project_units(st, pri, p)
        r = nowcast.nowcast(st, pri, p)
        assert (u["a"] + u["R"] * u["s_hat"]).sum() / (u["n"] + u["R"]).sum() == pytest.approx(r["share_hat"], abs=1e-12)


def test_sd_rescaling_matches_a_direct_run():
    pri, cat, actas = _toy()
    s = {"actas": actas, "cat": cat, "prior": pri}
    times = pd.date_range("2024-06-03T02:20Z", periods=4, freq="20min")
    p0 = mx.national_path(s, nowcast.Params(tau=0.0, unpooled=("zz",)), times)
    p1 = mx.national_path(s, nowcast.Params(tau=0.02, unpooled=("zz",)), times)
    assert np.allclose(mx.sd_at_tau(p0, 0.0, 0.02), p1["sd"])


def test_prep2012_rows_header_and_times():
    d, h = mx.read_prep2012(FX / "prep2012_sample.txt",
                            ["PRD", "PT", "MC", "C_PRD_PT_MC", "C_PRD_PT", "C_PRD_MC", "C_PT_MC"],
                            ["PAN", "PRI", "PVEM", "C_PRI_PVEM"])
    assert h["cut_utc"] == pd.Timestamp("2012-07-03T01:00:00Z") and h["utc_offset_h"] == -5
    assert h["out_of_catalogue"] == 1 and len(d) == 7          # the 'No identificada' acta is dropped
    first = d.iloc[0]
    assert first["a"] == 19 + 1 + 2 + 6 + 1 + 1 + 0 and first["b"] == 127 + 65 + 8 + 20
    assert first["t_verif"] == pd.Timestamp("2012-07-02T09:28:15.608Z")      # HORA_REGISTRO 04:28 at UTC-5
    assert (first["uf"], first["cd_mun"]) == ("01", "0338")
    ab = d[d["section"] == 0].iloc[0]                                          # section 0 = abroad, zz pseudo-unit
    assert (ab["uf"], ab["cd_mun"]) == ("zz", "m01") and ab["counted"]
    sin = d.iloc[-1]
    assert not sin["counted"] and pd.isna(sin["t_verif"])                      # 'Sin acta': in the catalogue, not counted


def test_quick_count_combines_only_after_publication():
    r = {"share_hat": 0.67, "share_sd": 0.01, "p_win": 1.0}
    cfg = {"quick_count": {"published_utc": "2024-06-03T05:50:00Z", "confidence": 0.95,
                           "a_low": 0.583, "a_high": 0.607, "b_low": 0.266, "b_high": 0.286}}
    assert mx.apply_quick_count(r, cfg, pd.Timestamp("2024-06-03T05:49:00Z")) is r
    got = mx.apply_quick_count(r, cfg, pd.Timestamp("2024-06-03T05:50:00Z"))
    lo, hi = 0.583 / (0.583 + 0.286), 0.607 / (0.607 + 0.266)
    q_mean, q_sd = (lo + hi) / 2, (hi - lo) / (2 * 1.959963984540054)
    variance = 1 / (1 / r["share_sd"] ** 2 + 1 / q_sd**2)
    expected = variance * (r["share_hat"] / r["share_sd"] ** 2 + q_mean / q_sd**2)
    assert got["share_hat"] == pytest.approx(expected)
    assert got["share_sd"] == pytest.approx(np.sqrt(variance))
    assert got["quick_count_share"] == pytest.approx(q_mean)
    assert got["count_share_hat"] == r["share_hat"]
