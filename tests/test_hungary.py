import numpy as np
import pandas as pd

from brunder import hungary as hu
from brunder import nowcast as nc

# 2022 National Assembly election, official final (vtr.valasztas.hu/ogy2022, folder 04161400: ListasJkv.json,
# SzervezetekEredmenye.json, Patko.json). Party lists: list votes (domestic + postal), compensation votes,
# threshold. MKKP, MEMO and Normális Párt failed the 5% threshold; their compensation votes do not matter.
PARTY = {  # tl_id: (name, list votes, compensation votes, threshold)
    952: ("FIDESZ-KDNP", 3_060_706, 1_330_256, 0.10),
    950: ("DK-JOBBIK-MOMENTUM-MSZP-LMP-PÁRBESZÉD", 1_947_331, 1_579_149, 0.15),
    942: ("MI HAZÁNK", 332_487, 307_064, 0.05),
    951: ("MKKP", 185_052, 126_648, 0.05),
    954: ("MEMO", 58_929, 0, 0.05),
    955: ("NORMÁLIS PÁRT", 39_720, 0, 0.05),
}
# the 13 nationality lists; the German list (947, 24,630) passed the preferential quota of 23,085
NATIONALITY = {947: 24_630, 940: 1_760, 949: 1_208, 937: 645, 946: 526, 938: 418, 944: 396, 948: 281, 943: 232,
               941: 219, 945: 163, 939: 157}
DISTRICT_SEATS_2022 = {952: 87, 950: 19}
OFFICIAL_2022 = {952: 135, 950: 57, 942: 6, 947: 1}       # Patko.json: 199 seats


def _allocate_2022():
    ids = list(PARTY)
    L = np.array([[PARTY[i][1] for i in ids]], float)
    C = np.array([[PARTY[i][2] for i in ids]], float)
    thr = np.array([PARTY[i][3] for i in ids])
    NV = np.array([[NATIONALITY[i] for i in NATIONALITY]], float)
    s, ns = hu.allocate_many(L, C, thr, NV)
    return dict(zip(ids, s[0])), dict(zip(NATIONALITY, ns[0]))


def test_valid_list_votes_match_official_total():
    assert sum(v[1] for v in PARTY.values()) + sum(NATIONALITY.values()) == 5_654_860


def test_reproduces_2022_list_seats_and_parliament():
    s, ns = _allocate_2022()
    assert {k: v for k, v in s.items() if v} == {952: 48, 950: 38, 942: 6}
    assert {k: v for k, v in ns.items() if v} == {947: 1}
    total = {k: s.get(k, 0) + DISTRICT_SEATS_2022.get(k, 0) + ns.get(k, 0) for k in OFFICIAL_2022}
    assert total == OFFICIAL_2022
    assert sum(s.values()) + sum(ns.values()) == hu.LIST_SEATS


def test_joint_list_threshold_and_compensation_matter():
    ids = list(PARTY)
    L = np.array([[PARTY[i][1] for i in ids]], float)
    C = np.array([[PARTY[i][2] for i in ids]], float)
    NV = np.array([[NATIONALITY[i] for i in NATIONALITY]], float)
    # a 15% threshold for Mi Hazánk (5.9% of the list vote) removes it; its 6 seats go to the two blocs
    thr = np.array([PARTY[i][3] for i in ids])
    thr[ids.index(942)] = 0.15
    s, _ = hu.allocate_many(L, C, thr, NV)
    assert s[0][ids.index(942)] == 0 and s[0].sum() == 92
    # without compensation votes the opposition (more fragment votes) loses list seats to Fidesz
    s0, _ = hu.allocate_many(L, np.zeros_like(C), np.array([PARTY[i][3] for i in ids]), NV)
    assert s0[0][ids.index(952)] > 48 and s0[0][ids.index(950)] < 38


def test_compensation_votes():
    v = pd.DataFrame({"unit": ["d1"] * 3 + ["d2"] * 2, "jlcs": [1, 2, 3, 1, 2], "votes": [500.0, 300, 100, 200, 400]})
    c = hu.comp_votes(v)
    # d1: winner 1 keeps 500 - (300 + 1); 2 and 3 all their votes. d2: winner 2 keeps 400 - 201; 1 all
    assert c.to_dict() == {1: 199 + 200, 2: 300 + 199, 3: 100}


def test_district_draws_match_nowcast():
    rng = np.random.default_rng(0)
    n = 60
    uf = np.repeat(["01", "02", "03", "04", "05", "06"], 10)
    prior = pd.DataFrame({"uf": uf, "cd_mun": [f"hu-{u}-{i:02d}" for i, u in enumerate(uf)],
                          "s0": rng.uniform(0.3, 0.6, n), "V0": rng.uniform(3e4, 6e4, n)})
    f = np.where(rng.random(n) < 0.6, rng.uniform(0.1, 0.9, n), 0.0)
    counted = f * prior["V0"] * 1.1
    share = prior["s0"] + 0.05 + rng.normal(0, 0.03, n)
    state = pd.DataFrame({"uf": uf, "cd_mun": prior["cd_mun"], "n": counted, "a": counted * share, "f": f})
    p = nc.Params(tau=0.01)
    ref = nc.nowcast(state, prior, p)
    m, s = hu.district_draws(state, prior, p, rng.standard_normal(100_000), rng)
    nf = (m["n"] + m["R"]).to_numpy()
    agg = (s * nf).sum(1) / nf.sum()
    assert abs(agg.mean() - ref["share_hat"]) < 2e-4
    assert abs(agg.std() / ref["share_sd"] - 1) < 0.02


def test_version_time_is_budapest_local():
    assert hu.version_time(2026, "04122000") == pd.Timestamp("2026-04-12T18:00:00Z")
    assert hu.version_time(2022, "04040700") == pd.Timestamp("2022-04-04T05:00:00Z")
