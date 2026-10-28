import json
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import liveserve, newzealand as nz

F = Path(__file__).parent / "fixtures"


def test_mmp_reproduces_official_2023_result_exactly():
    official = nz.read_summary(F / "nz2023_overall_results.csv").set_index("party")
    votes = official["party_votes"].to_dict()
    electorate = official["electorate_seats"].to_dict()
    got = nz.allocate(votes, electorate)
    expected = official["total_seats"].to_dict()
    assert got == expected
    assert sum(got.values()) == 122                 # Te Pāti Māori's two-seat overhang
    assert got["National Party"] == 48
    assert got["Labour Party"] == 34
    assert got["Te Pāti Māori"] == 6


def test_mmp_threshold_electorate_exemption_overhang_and_independent():
    votes = np.array([[60, 39, 1], [60, 39, 1]], dtype=float)
    electorate = np.array([[1, 0, 0], [1, 0, 3]])
    got = nz.allocate_many(votes, electorate, total=10)
    assert got[0].tolist() == [6, 4, 0]             # 1% party is below threshold
    assert got[1].tolist() == [6, 4, 3]             # electorate win exempts it, three-seat overhang
    independent = nz.allocate_many(votes[:1], electorate[:1], total=10, independent_seats=1)
    assert independent.sum() == 9                    # one seat is outside the party-list allocation


def test_real_2023_voting_place_csv():
    d = nz.read_voting_place_party_votes(F / "nz2023_party_voting_places_1.csv")
    assert set(d["kind"]) == {"advance", "election-day", "special"}
    assert d.iloc[0]["electorate"] == "Auckland Central"
    assert d.iloc[0]["National Party"] == 38
    assert d.iloc[2]["area"] == "Auckland City"    # blank area cells inherit the preceding area
    special = d[d["place"].str.startswith("Special Votes BEFORE")].iloc[0]
    assert special["valid"] == 5013 and special["Green Party"] == 1326


def test_official_2026_test_xml_parsers():
    parties = nz.parse_parties((F / "nz2026_parties.xml").read_bytes())
    candidates = nz.parse_candidates((F / "nz2026_candidates_e01.xml").read_bytes())
    e = nz.parse_electorate((F / "nz2026_e01_test.xml").read_bytes(), parties, candidates)
    assert e.e_no == 1 and e.updated == "2026-10-22T15:19:30"
    assert e.final and e.places == e.places_total == 0 and e.f == 0
    assert e.party_votes == {"ACT": 0, "GP": 0, "LAB": 0, "NAT": 0, "OPP": 0, "ALCP": 0}
    assert set(e.candidate_votes) == {"ACT", "GP", "LAB", "NAT", "OPP", "ALCP"}


def test_projection_finishes_at_the_count_and_outcomes():
    units = pd.DataFrame({"unit_id": ["nz-gen-01", "nz-gen-02"], "name": ["A", "B"],
        "region": ["gen", "gen"], "V": [100, 100], "winner": ["NAT", "LAB"],
        **{p: ([.6, .4] if p == "NAT" else [.4, .6] if p == "LAB" else [0, 0]) for p in nz.PARTY_KEYS}},
        index=[1, 2])
    c = nz.empty_counts(units)
    c[["f", "places", "places_total", "valid"]] = [[1, 10, 10, 100], [1, 10, 10, 100]]
    c["pv_NAT"], c["pv_LAB"] = [55, 35], [45, 65]
    c["cv_NAT"], c["cv_LAB"] = [60, 30], [40, 70]
    p = nz.project(units, c, nz.Settings(draws=100, floor_log_sd=0, candidate_log_sd=0))
    assert p.counted_frac == 1 and p.mean.sum() == 200
    assert p.mean[p.parties.index("NAT")] == 90
    assert p.mean[p.parties.index("LAB")] == 110
    votes, electorate, seats = nz.seat_draws(p, nz.Settings(draws=100, floor_log_sd=0, candidate_log_sd=0),
                                              np.random.default_rng(1))
    assert np.all(electorate[:, p.parties.index("NAT")] == 1)
    assert np.all(electorate[:, p.parties.index("LAB")] == 1)
    markets = [{"rid": "nat", "kind": "most_seats", "party": "NAT"},
               {"rid": "lab-ahead", "kind": "vote_ahead", "a": "LAB", "b": "NAT"}]
    out = nz.outcomes(votes, seats, p.parties, markets)
    assert set(out) == {"nat", "lab-ahead"} and len(out["nat"]) == 100
    independent = c.copy()
    independent.loc[1, [f"cv_{x}" for x in nz.PARTY_KEYS]] = 0
    independent.loc[1, "cv_IND"] = 100
    p_ind = nz.project(units, independent, nz.Settings(draws=100, floor_log_sd=0, candidate_log_sd=0))
    _, electorate_ind, seats_ind = nz.seat_draws(
        p_ind, nz.Settings(draws=100, floor_log_sd=0, candidate_log_sd=0), np.random.default_rng(1))
    assert np.all(electorate_ind.sum(1) == 1)
    assert np.all(seats_ind.sum(1) == 119)


def test_feed_urls_are_bounded_electorate_aggregates():
    urls = nz.feed_urls()
    assert len(urls) == 76
    assert f"{nz.BASE}/e01/e01.xml" in urls and f"{nz.BASE}/e71/e71.xml" in urls
    assert not any("votingplaces" in u for u in urls)


def test_liveserve_allows_nz_when_configured(tmp_path):
    root = tmp_path / "data"
    (root / "nz2026").mkdir(parents=True)
    (root / "nz2026" / "timeline_tail.json").write_text("{}")
    files = liveserve.Files(root, ["nz2026"], [])
    assert files.resolve("/nz2026/timeline_tail.json") == root / "nz2026" / "timeline_tail.json"
    assert files.resolve("/us2026/timeline_tail.json") is None


def test_v2_only_adds_rank_markets_to_v1():
    root = Path(__file__).parents[1] / "configs"
    v1 = nz.load_config(root / "nz2026_v1.toml")
    v2 = nz.load_config(root / "nz2026_v2.toml")
    assert v2["model"] == v1["model"]
    assert v2["prior"] == v1["prior"]
    assert v2["seed"] == v1["seed"]
    assert v2["feed"] == v1["feed"]
    assert v2["markets"][:len(v1["markets"])] == v1["markets"]
    assert len(v2["markets"]) == len(v1["markets"]) + 12
    assert {(m["kind"], m["rank"]) for m in v2["markets"][len(v1["markets"]):]} == {("rank", 2), ("rank", 3)}


def test_live_writes_precount_contract_immediately_without_returns(tmp_path, monkeypatch):
    parties = nz.parse_parties((F / "nz2026_parties.xml").read_bytes())
    candidates = nz.parse_candidates((F / "nz2026_candidates_e01.xml").read_bytes())
    electorates = pd.DataFrame(
        [{"e_no": 1, "name": "Auckland Central", "region": "gen", "unit_id": "nz-gen-01"}]).set_index("e_no")
    monkeypatch.setattr(nz, "_fetch_references", lambda: (parties, electorates, candidates))
    exporter = nz.Exporter(Path(__file__).parents[1] / "configs" / "nz2026_v2.toml", tmp_path / "snap")
    dest = exporter.write(tmp_path / "site")
    assert {p.name for p in dest.iterdir()} == {
        "meta.json", "timeline_precount.json", "timeline.json", "timeline_tail.json",
        "units.json", "stats.json", "arb.json", "entry.json"}
    meta = json.loads((dest / "meta.json").read_text())
    timeline = json.loads((dest / "timeline.json").read_text())
    assert meta["phase"] == "pre-count" and len(meta["races"]) == 46
    assert len(timeline) == 46 and all(len(rows) == 1 for rows in timeline.values())
