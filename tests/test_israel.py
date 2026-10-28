import numpy as np
import pandas as pd

from brunder import israel as il

# 25th Knesset (1 Nov 2022), official final valid votes per list: the CEC's final ballot-box file
# (media25.bechirot.gov.il/files/expb.csv, Wayback capture 2022-11-08 16:59 UTC) summed over all boxes,
# double envelopes included. Total 4,764,742 valid votes.
K25_VOTES = {
    "אמת": 175992, "אצ": 15801, "ב": 56775, "ג": 280194, "ד": 138617, "ום": 178735, "ז": 430, "זך": 205,
    "זנ": 153, "זץ": 292, "ט": 516470, "י": 1157, "יז": 13920, "ינ": 234, "יץ": 262, "יק": 411, "כן": 432482,
    "ך": 746, "ל": 213687, "מחל": 1115336, "מרצ": 150793, "נז": 350, "ני": 1354, "נף": 255, "נץ": 1020,
    "נק": 1215, "נר": 334, "עם": 194047, "פה": 847435, "ף": 1728, "צ": 8800, "ץ": 988, "ק": 1618, "קי": 415,
    "קך": 1078, "קנ": 1292, "קץ": 14694, "רז": 939, "שס": 392964, "ת": 1524,
}
# surplus agreements filed for the 25th Knesset: Likud-Religious Zionism, UTJ-Shas, Yesh Atid-National
# Unity, Labor-Meretz, Hadash-Ta'al-Ra'am
K25_AGREEMENTS = [("מחל", "ט"), ("ג", "שס"), ("פה", "כן"), ("אמת", "מרצ"), ("ום", "עם")]
# official seats of the 25th Knesset
K25_SEATS = {"מחל": 32, "פה": 24, "ט": 14, "כן": 12, "שס": 11, "ג": 7, "ל": 6, "עם": 5, "ום": 5, "אמת": 4}


def test_total_matches_official_valid_votes():
    assert sum(K25_VOTES.values()) == 4_764_742


def test_reproduces_25th_knesset_seats_exactly():
    s = il.allocate(K25_VOTES, K25_AGREEMENTS)
    assert {k: v for k, v in s.items() if v} == K25_SEATS
    assert sum(s.values()) == 120


def test_agreement_matters_in_2022():
    # without the Likud-RZP agreement Likud's 32nd seat goes to Labor: the test above is not trivially passed
    s = il.allocate(K25_VOTES, [])
    assert s["מחל"] == 31 and s["אמת"] == 5


def test_threshold_and_failed_partner():
    # Meretz (3.16%) and Balad (2.91%) fall below 3.25%; an agreement with a failed list is void
    s = il.allocate(K25_VOTES, K25_AGREEMENTS)
    assert s["מרצ"] == 0 and s["ד"] == 0
    s2 = il.allocate(K25_VOTES, [p for p in K25_AGREEMENTS if p != ("אמת", "מרצ")])
    assert s2 == s


def test_vectorised_rows_are_independent():
    names = list(K25_VOTES)
    v = np.array([[K25_VOTES[k] for k in names]] * 3, dtype=float)
    v[1, names.index("מרצ")] += 10_000                    # Meretz now passes
    ix = {k: i for i, k in enumerate(names)}
    seats = il.allocate_many(v, [(ix[a], ix[b]) for a, b in K25_AGREEMENTS])
    assert (seats.sum(1) == 120).all()
    assert seats[0, ix["מרצ"]] == 0 and seats[1, ix["מרצ"]] >= 4
    assert (seats[0] == seats[2]).all()


def test_project_recovers_uniform_transition():
    # 40 localities, two prior groups; new lists X and Y with fixed transition shares. Count half of them:
    # the projection of the uncounted half must match the truth closely.
    rng = np.random.default_rng(1)
    n = 40
    comp = rng.uniform(0.1, 0.9, n)
    W = rng.integers(2_000, 20_000, n).astype(float)
    units = pd.DataFrame({"W": W, "boxes_total": 4.0, "comp_A": comp, "comp_" + il.OTHER: 1 - comp},
                         index=pd.Index(range(1, n + 1), name="loc"))
    share_x = 0.8 * comp + 0.1 * (1 - comp)
    truth = pd.DataFrame({"X": W * share_x, "Y": W * (1 - share_x)}, index=units.index)
    counted = truth.iloc[: n // 2].copy()
    counted.insert(0, "valid", counted.sum(axis=1))
    counted.insert(0, "boxes", 4)
    p = il.project(units, counted, il.Settings(prior_votes=100.0, tau=0.0))
    final = truth.sum().to_numpy()
    est = p.mean[:2]
    assert np.allclose(est / est.sum(), final / final.sum(), atol=0.002)
    assert abs(p.mean.sum() - W.sum()) / W.sum() < 0.01


def test_outcomes_bloc_tie_breaks_on_votes():
    lists = ["a1", "b1"]
    votes = np.array([[100.0, 90.0], [90.0, 100.0]])
    seats = np.array([[60, 60], [60, 60]])
    o = il.outcomes(votes, seats, lists, [{"id": "bloc", "kind": "bloc", "a": ["a1"], "b": ["b1"]},
                                          {"id": "most", "kind": "most_seats", "party": "a1"}])
    assert list(o["bloc"]) == [1.0, 0.0]
    assert list(o["most"]) == [1.0, 0.0]
