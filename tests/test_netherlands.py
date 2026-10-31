"""Tweede Kamer seat allocation, Kiesraad and poll-table parsers, market rules and the prior-only projection
(src/brunder/netherlands.py) on small real samples: the official national votes of 2023 and 2025, a trimmed copy of the
Kiesraad 2025 file and the final rows of Wikipedia's 2025 poll table."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import netherlands as nl

FIXTURE = Path(__file__).parent / "fixtures" / "nl_tk2025_uitslag_sample.csv"

# official national votes by list (Kiesraad, valid votes), and the official seats
VOTES_2025 = {"PVV (Partij voor de Vrijheid)": 1760966, "GROENLINKS / Partij van de Arbeid (PvdA)": 1352163, "VVD": 1505829,
              "Nieuw Sociaal Contract (NSC)": 39408, "D66": 1790634, "BBB": 279916, "CDA": 1246874,
              "SP (Socialistische Partij)": 199585, "DENK": 250368, "Partij voor de Dieren": 219371,
              "Forum voor Democratie": 480393, "Staatkundig Gereformeerde Partij (SGP)": 238093, "ChristenUnie": 201361,
              "Volt": 116468, "JA21": 628517, "Vrede voor Dieren": 16819, "Belang Van Nederland (BVNL)": 18477, "BIJ1": 40360,
              "LP (Libertaire Partij)": 8248, "50PLUS": 151053, "Piratenpartij": 10575, "FNP": 9331}
SEATS_2025 = {"D66": 26, "PVV (Partij voor de Vrijheid)": 26, "VVD": 22, "GROENLINKS / Partij van de Arbeid (PvdA)": 20, "CDA": 18,
              "JA21": 9, "Forum voor Democratie": 7, "BBB": 4, "SP (Socialistische Partij)": 3, "DENK": 3, "Partij voor de Dieren": 3,
              "Staatkundig Gereformeerde Partij (SGP)": 3, "ChristenUnie": 3, "50PLUS": 2, "Volt": 1}
VOTES_2023 = {"VVD": 1589519, "D66": 656292, "GROENLINKS / Partij van de Arbeid (PvdA)": 1643073,
              "PVV (Partij voor de Vrijheid)": 2450878, "CDA": 345822, "SP (Socialistische Partij)": 328225,
              "Forum voor Democratie": 232963, "Partij voor de Dieren": 235148, "ChristenUnie": 212532, "Volt": 178802,
              "JA21": 71345, "Staatkundig Gereformeerde Partij (SGP)": 217270, "DENK": 246765, "50PLUS": 51043, "BBB": 485551,
              "BIJ1": 44253, "BVNL / Groep Van Haga": 52913, "Nieuw Sociaal Contract": 1343287, "Splinter": 12838}
SEATS_2023 = {"PVV (Partij voor de Vrijheid)": 37, "GROENLINKS / Partij van de Arbeid (PvdA)": 25, "VVD": 24,
              "Nieuw Sociaal Contract": 20, "D66": 9, "BBB": 7, "CDA": 5, "SP (Socialistische Partij)": 5,
              "Forum voor Democratie": 3, "Partij voor de Dieren": 3, "ChristenUnie": 3,
              "Staatkundig Gereformeerde Partij (SGP)": 3, "DENK": 3, "Volt": 2, "JA21": 1}


@pytest.mark.parametrize("votes, seats", [(VOTES_2025, SEATS_2025), (VOTES_2023, SEATS_2023)])
def test_allocation_reproduces_official_seats(votes, seats):
    v = pd.Series(votes)
    got = pd.Series(nl.allocate(v.to_numpy(float)), index=v.index)
    assert got[got > 0].to_dict() == seats
    assert got.sum() == 150


def test_allocation_threshold_and_pool():
    # a list below one quota (valid votes / 150) gets nothing however it would average; a pool of small lists counts in the quota
    votes = np.array([6000.0, 3000.0, 60.0, 940.0])        # quota = 100 -> the 60-vote list is out
    n = nl.allocate(votes)
    assert n.sum() == 150 and n[2] == 0
    n2 = nl.allocate(np.array([6000.0, 3000.0, 1000.0]), pool=2)   # pool eligible by size, still never seated
    assert n2[2] == 0 and n2.sum() == 150


def test_rank_order_tie_goes_to_alphabetical_label():
    labels = ["PVV", "GL/PvdA", "VVD", "D66", "CDA"]
    seats = np.array([26, 20, 22, 26, 18])                 # 2025: D66 and PVV tied on 26
    rank = nl.rank_order(seats, labels)
    assert rank[3] == 0 and rank[0] == 1                   # D66 first (alphabetical), PVV second
    assert list(np.argsort(rank)) == [3, 0, 2, 1, 4]
    # draws x lists
    r2 = nl.rank_order(np.array([seats, seats[::-1]]), labels)
    assert r2[0, 3] == 0 and r2[1, 4] == 0


def test_read_uitslag_sample():
    votes, seats, info = nl.read_uitslag(FIXTURE)
    assert set(votes.index) == {"G0014", "G0034", "G9010", "O9001"}
    assert votes.loc["G0014", "d66"] == 36070 and votes.loc["G0014", "pvv"] == 12369
    assert seats["d66"] == 26 and seats["pvv"] == 26 and seats.sum() == 150
    assert info.loc["G0014", "name"] == "Groningen" and info.loc["G0014", "province"] == "P20"
    assert info.loc["O9001", "province"] == "P99"
    assert info.loc["G0014", "valid"] == votes.loc["G0014"].sum()          # every list of the gemeente is in the table
    assert nl.unit_id("P20", 14) == "nl-gr-0014" and nl.gemeente_code("G0014") == 14


def test_merge_gl_pvda():
    v = pd.DataFrame({"gl": [3, 1], "pvda": [2, 1], "vvd": [5, 5]})
    m = nl.merge_gl_pvda(v)
    assert list(m.columns) == ["vvd", "glpvda"] and m["glpvda"].tolist() == [5, 2]
    s = nl.merge_gl_pvda(pd.Series({"gl": 3, "pvda": 2, "vvd": 5}))
    assert s["glpvda"] == 5 and "gl" not in s


POLL_HTML = """<table>
<tr><th rowspan="2">Polling firm</th><th rowspan="2">Fieldwork date</th><th rowspan="2">Sample size</th>
<th>PVV</th><th>GL/PvdA</th><th>VVD</th><th>NSC</th><th>D66</th><th>BBB</th><th>CDA</th><th>SP</th><th>Denk</th><th>PvdD</th>
<th>FvD</th><th>SGP</th><th>CU</th><th>Volt</th><th>JA21</th><th>50+</th><th rowspan="2">Lead</th></tr>
<tr><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th></th></tr>
<tr><td>2025 election</td><td>29 Oct 2025</td><td>–</td><td>26</td><td>20</td><td>22</td><td>0</td><td>26</td><td>4</td><td>18</td><td>3</td><td>3</td><td>3</td><td>7</td><td>3</td><td>3</td><td>1</td><td>9</td><td>2</td><td>Tie</td></tr>
<tr><td>Ipsos I&amp;O</td><td>29 Oct 2025<sup>[a]</sup></td><td>–</td><td>25</td><td>20</td><td>23</td><td>0</td><td>27</td><td>4</td><td>19</td><td>3</td><td>3</td><td>3</td><td>6</td><td>3</td><td>2</td><td>1</td><td>9</td><td>2</td><td>2</td></tr>
<tr><td>Peil.nl</td><td>28 Oct 2025</td><td>~8,000</td><td>23</td><td>23</td><td>22</td><td>0</td><td>22</td><td>4</td><td>20</td><td>4</td><td>4</td><td>3</td><td>7</td><td>3</td><td>2</td><td>2</td><td>9</td><td>2</td><td>Tie</td></tr>
<tr><td>Verian</td><td>27–28 Oct 2025</td><td>1,151</td><td>29</td><td>25</td><td>16</td><td>0</td><td>24</td><td>3</td><td>19</td><td>5</td><td>3</td><td>2</td><td>6</td><td>3</td><td>3</td><td>2</td><td>8</td><td>2</td><td>4</td></tr>
<tr><td>Ipsos I&amp;O</td><td>25–28 Oct 2025</td><td>2,505</td><td>23</td><td>23</td><td>17</td><td>1</td><td>23</td><td>5</td><td>19</td><td>4</td><td>3</td><td>4</td><td>6</td><td>3</td><td>3</td><td>2</td><td>11</td><td>3</td><td>Tie</td></tr>
<tr><td>Peil.nl</td><td>24 Oct 2025</td><td>7,500+</td><td>29</td><td>24</td><td>20</td><td>0</td><td>17</td><td>4</td><td>22</td><td>4</td><td>4</td><td>3</td><td>6</td><td>3</td><td>2</td><td>2</td><td>9</td><td>1</td><td>5</td></tr>
<tr><td>Peil.nl</td><td>3 Oct 2025</td><td>–</td><td>30</td><td>27</td><td>13</td><td>0</td><td>12</td><td>4</td><td>23</td><td>6</td><td>4</td><td>3</td><td>5</td><td>3</td><td>3</td><td>3</td><td>13</td><td>1</td><td>3</td></tr>
</table>"""


def test_poll_table_and_final_average():
    (grid,) = nl.html_tables(POLL_HTML)
    polls = nl.parse_polls(grid)
    assert len(polls) == 6                                   # the results row is dropped
    assert polls["end"].max() == date(2025, 10, 29)            # the exit poll is parsed (and left out of the average below)
    avg, used = nl.final_poll(polls, date(2025, 10, 29))
    # election-day exit poll out; one poll per pollster (Peil.nl's 28 Oct, not its 24 Oct); 3 Oct is outside the 7-day window
    assert sorted(used["pollster"]) == ["Ipsos I&O", "Peil.nl", "Verian"]
    assert avg["pvv"] == pytest.approx((23 + 23 + 29) / 3) and avg["d66"] == pytest.approx((22 + 23 + 24) / 3)
    assert avg["glpvda"] == pytest.approx((23 + 23 + 25) / 3)
    assert nl.last_date("31 Oct–3 Nov 2025") == date(2025, 11, 3) and nl.last_date("n/a") is None


def test_parse_band():
    assert nl.parse_band("≤26") == (0, 26) and nl.parse_band("27–29") == (27, 29) and nl.parse_band("≥33") == (33, 150)
    assert nl.parse_band("<16") == (0, 15) and nl.parse_band("20+") == (20, 150) and nl.parse_band("16–17") == (16, 17)
    with pytest.raises(ValueError):
        nl.parse_band("many")


def test_projection_probabilities():
    ids = list(nl.PARTY_IDS)
    prev = pd.Series({"pvv": 2450878, "glpvda": 1643073, "vvd": 1589519, "nsc": 1343287, "d66": 656292, "bbb": 485551, "cda": 345822,
                      "sp": 328225, "denk": 246765, "pvdd": 235148, "fvd": 232963, "sgp": 217270, "cu": 212532, "volt": 178802,
                      "ja21": 71345, "50plus": 51043}, dtype=float).reindex(ids)
    polls = pd.Series({"pvv": 25.0, "glpvda": 23.7, "vvd": 18.3, "nsc": 0.3, "d66": 23.0, "bbb": 4.0, "cda": 19.3, "sp": 4.3,
                       "denk": 3.3, "pvdd": 3.3, "fvd": 6.3, "sgp": 3.0, "cu": 2.7, "volt": 2.0, "ja21": 9.3, "50plus": 2.0})
    s = nl.Settings(w_prev=0.0, err=0.048, other=0.018, draws=4000)
    c = nl.central_shares(polls, prev, s)
    assert c.sum() == pytest.approx(1.0) and c["pvv"] > c["d66"] > c["vvd"]
    c0 = nl.central_shares(None, prev, s)
    assert c0["pvv"] == pytest.approx(prev["pvv"] / prev.sum())
    seats = nl.simulate_seats(c, s, np.random.default_rng(1))
    assert seats.shape == (4000, 16) and (seats.sum(axis=1) == 150).all()
    p = nl.outcome_probabilities(seats, ids, {})
    assert sum(p[f"most:{i}"] for i in ids) == pytest.approx(1.0)       # exactly one party has the most seats
    assert sum(p[f"second:{i}"] for i in ids) == pytest.approx(1.0)
    assert p["most:pvv"] > p["most:d66"] > p["most:vvd"]
    # same seed, same draws
    again = nl.simulate_seats(c, s, np.random.default_rng(1))
    assert (again == seats).all()


def test_swing_projection_scales_to_central_share():
    votes_prev = pd.DataFrame({"pvv": [100, 300], "d66": [100, 100]})
    total_prev = pd.Series({"pvv": 400.0, "d66": 200.0})
    central = pd.Series({"pvv": 0.4, "d66": 0.6})
    proj = nl.swing_projection(votes_prev, total_prev, central)
    # national shares were 2/3 and 1/3; the factors are 0.6 and 1.8
    assert proj["pvv"].tolist() == pytest.approx([60, 180]) and proj["d66"].tolist() == pytest.approx([180, 180])
