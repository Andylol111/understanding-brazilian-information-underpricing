"""Australia (src/brunder/australia.py): AEC media-feed parsers, observation, projection, seat simulation and market rules
on a tiny synthetic feed with the real message structure (two divisions, three polling places)."""

import numpy as np
import pandas as pd
import pytest

from brunder import australia as au

NS = ('xmlns="http://www.aec.gov.au/xml/schema/mediafeed" xmlns:eml="urn:oasis:names:tc:evs:schema:eml"')


def cand(cid, name, party, pos, votes=0, hist=0, independent=False, incumbent=False):
    aff = "" if independent else (f'<eml:AffiliationIdentifier Id="1" ShortCode="{party}"><eml:RegisteredName>'
                                  f'{"The Greens (VIC)" if party == "GVIC" else party}</eml:RegisteredName></eml:AffiliationIdentifier>')
    return (f'<Candidate{" Independent=\"true\"" if independent else ""}><eml:CandidateIdentifier Id="{cid}"><eml:CandidateName>'
            f'{name}</eml:CandidateName></eml:CandidateIdentifier>{aff}<BallotPosition>{pos}</BallotPosition>'
            f'<Incumbent Notional="{str(incumbent).lower()}">{str(incumbent).lower()}</Incumbent>'
            f'<Votes Historic="{hist}">{votes}</Votes></Candidate>')


def preload_xml() -> bytes:
    def booth(pid, name, hist):
        cs = "".join(f'<Candidate><eml:CandidateIdentifier Id="{c}"/><Votes Historic="{v}">0</Votes></Candidate>' for c, v in hist.items())
        return (f'<PollingPlace><PollingPlaceIdentifier Id="{pid}" Name="{name}"/><FirstPreferences>{cs}'
                f'<Formal><Votes Historic="{sum(hist.values())}">0</Votes></Formal></FirstPreferences></PollingPlace>')

    def contest(did, name, state, cands, hist_tpp, booths):
        return (f'<Contest><eml:ContestIdentifier Id="{did}"><eml:ContestName>{name}</eml:ContestName></eml:ContestIdentifier>'
                f'<PollingDistrictIdentifier Id="{did}"><Name>{name}</Name><StateIdentifier Id="{state}"/></PollingDistrictIdentifier>'
                f'<Enrolment Historic="1000">1100</Enrolment><FirstPreferences PollingPlacesExpected="2">{"".join(cands)}'
                f'<Formal><Votes Historic="1000">0</Votes></Formal></FirstPreferences>'
                f'<TwoPartyPreferred><Coalition><CoalitionIdentifier Id="1"/><Votes Historic="{hist_tpp[0]}">0</Votes></Coalition>'
                f'<Coalition><CoalitionIdentifier Id="2"/><Votes Historic="{hist_tpp[1]}">0</Votes></Coalition></TwoPartyPreferred>'
                f'<PollingPlaces>{"".join(booths)}</PollingPlaces></Contest>')

    c1 = [cand(1, "ONE, A", "ALP", 1, hist=500, incumbent=True), cand(2, "TWO, B", "LP", 2, hist=400),
          cand(3, "GREEN, C", "GVIC", 3, hist=100)]
    c2 = [cand(4, "FOUR, D", "ALP", 1, hist=300), cand(5, "FIVE, E", "NP", 2, hist=500, incumbent=True),
          cand(6, "SIX, Frank", "IND", 3, hist=200, independent=True)]
    x = (f'<MediaFeed {NS} Created="2025-05-03T21:01:17"><Results Updated="2025-05-03T21:01:04" Phase="ElectionNight" '
         f'Verbosity="Preload"><Election><House><Contests>'
         + contest(10, "Alpha", "NSW", c1, (520, 480), [booth(100, "North", {1: 300, 2: 200, 3: 50}), booth(101, "South", {1: 200, 2: 200, 3: 50})])
         + contest(20, "Beta", "VIC", c2, (400, 600), [booth(200, "East", {4: 300, 5: 500, 6: 200})])
         + '<Contest><eml:ContestIdentifier Id="NSW"><eml:ContestName>NSW Senate</eml:ContestName></eml:ContestIdentifier></Contest>'
         + '</Contests></House></Election></Results></MediaFeed>')
    return x.encode()


def results_xml(alp_n=330, lp_n=170, tcp=True) -> bytes:
    def cs(d):
        return "".join(f'<Candidate><eml:CandidateIdentifier Id="{c}"/><Votes>{v}</Votes></Candidate>' for c, v in d.items())

    tcp_x = (f'<TwoCandidatePreferred Updated="2025-05-03T20:11:00">{cs({1: alp_n, 2: lp_n})}</TwoCandidatePreferred>'
             if tcp else '<TwoCandidatePreferred Maverick="true"/>')
    booth = (f'<PollingPlace><PollingPlaceIdentifier Id="100"/><FirstPreferences Updated="2025-05-03T20:00:00">'
             f'{cs({1: 300, 2: 160, 3: 40})}<Formal><Votes>500</Votes></Formal></FirstPreferences>'
             f'{tcp_x if tcp else ""}</PollingPlace>'
             '<PollingPlace><PollingPlaceIdentifier Id="101"/><FirstPreferences><Formal><Votes>0</Votes></Formal></FirstPreferences>'
             '<TwoCandidatePreferred/></PollingPlace>')
    x = (f'<MediaFeed {NS} Created="2025-05-03T21:01:17"><Results Updated="2025-05-03T21:01:04" Phase="ElectionNight" '
         f'Verbosity="Light"><Election><House><Contests>'
         f'<Contest Updated="2025-05-03T21:00:09"><eml:ContestIdentifier Id="10"><eml:ContestName>Alpha</eml:ContestName></eml:ContestIdentifier>'
         f'<Enrolment>1100</Enrolment><FirstPreferences PollingPlacesReturned="1" PollingPlacesExpected="2">{cs({1: 300, 2: 160, 3: 40})}'
         f'<Formal><Votes>500</Votes></Formal><Informal><Votes>9</Votes></Informal></FirstPreferences>'
         f'{tcp_x.replace("Updated=", "PollingPlacesReturned=\"1\" PollingPlacesExpected=\"2\" Updated=") if tcp else tcp_x}'
         f'<TwoPartyPreferred><Coalition><CoalitionIdentifier Id="1"/><Votes>{alp_n}</Votes></Coalition>'
         f'<Coalition><CoalitionIdentifier Id="2"/><Votes>{lp_n}</Votes></Coalition></TwoPartyPreferred>'
         f'<PollingPlaces>{booth}</PollingPlaces></Contest>'
         '<Contest><eml:ContestIdentifier Id="20"><eml:ContestName>Beta</eml:ContestName></eml:ContestIdentifier><Enrolment>900</Enrolment>'
         '<TwoCandidatePreferred Restricted="true"/></Contest>'
         '<Contest><eml:ContestIdentifier Id="VIC"><eml:ContestName>VIC Senate</eml:ContestName></eml:ContestIdentifier><Enrolment>5</Enrolment></Contest>'
         '</Contests></House></Election></Results></MediaFeed>')
    return x.encode()


def test_group_of_matches_parties_across_elections():
    assert au.group_of({"party": "LNP", "party_name": "Liberal National Party", "independent": False, "name": "A, B"}) == "LNC"
    assert au.group_of({"party": "GVIC", "party_name": "The Greens (VIC)", "independent": False, "name": "A, B"}) == "GRN"
    assert au.group_of({"party": "NP", "party_name": "Nationals", "independent": False, "name": "A, B"}) == "LNC"
    assert au.group_of({"party": "IND", "party_name": "Independent", "independent": True, "name": "Le, Dai"}) == "IND:LE"
    assert au.group_of({"party": "ON", "party_name": "One Nation", "independent": False, "name": "A, B"}) == "ON"


def test_parse_preload_skips_senate_and_maps_history():
    pre = au.parse_preload(preload_xml())
    assert sorted(pre["divs"]) == [10, 20] and len(pre["cands"]) == 6 and len(pre["booths"]) == 3
    assert pre["divs"][10]["hist_tpp"] == {1: 520, 2: 480} and pre["divs"][10]["state"] == "NSW"
    assert pre["cands"][6]["independent"] and pre["cands"][1]["incumbent"] and not pre["cands"][2]["incumbent"]
    assert pre["booths"][(10, 100)]["hist_formal"] == 550 and pre["booths"][(20, 200)]["hist_fp"][5] == 500


def test_parse_results_times_votes_and_flags():
    fr = au.parse_results(results_xml())
    assert fr["created"] == pd.Timestamp("2025-05-03 11:01:17", tz="UTC")        # 21:01 AEST
    a, b = fr["div"][10], fr["div"][20]
    assert sorted(fr["div"]) == [10, 20]
    assert a["tcp"] == {1: 330, 2: 170} and a["tpp"] == {1: 330, 2: 170} and (a["fp_ret"], a["fp_exp"]) == (1, 2)
    assert a["pp"][100]["fp"] == {1: 300, 2: 160, 3: 40} and a["pp"][100]["tcp"] == {1: 330, 2: 170}
    assert a["pp"][101]["fp"] == {} and a["pp"][101]["tcp"] == {}                  # no Updated stamp: not counted yet
    assert b["restricted"] and b["tcp"] is None
    assert au.parse_results(results_xml(tcp=False))["div"][10]["maverick"]


def test_observe_matched_swing_against_previous_election():
    pre = au.parse_preload(preload_xml())
    prev = {100: {"div": 10, "pair": frozenset(("ALP", "LNC")), "tcp": {"ALP": 580, "LNC": 420}, "fp": {}, "formal": 1000}}
    S = au.static_tables(pre, prev)
    o = au.observe(au.parse_results(results_xml()), S)
    x = o[10]
    assert x["pair"] == ("ALP", "LNC") and x["prior"] == pytest.approx(0.52) and x["prior_how"] == "notional"
    assert (x["n"], x["va"], x["vm"]) == (500, 330, 500)
    assert x["dev"] == pytest.approx(330 - 0.58 * 500)                             # ALP 66% against 58% at the same place
    assert o[20]["pair"] is None and o[20]["restricted"]


def test_observe_maverick_falls_back_to_division_two_party_count():
    pre = au.parse_preload(preload_xml())
    o = au.observe(au.parse_results(results_xml(tcp=False)), au.static_tables(pre, {}))
    x = o[10]
    assert x["fallback"] and x["pair"] == ("ALP", "LNC") and x["vm"] == 0 and (x["va"], x["n"]) == (330, 500)


def test_projection_moves_toward_the_counted_swing_and_simulates_winners():
    pre = au.parse_preload(preload_xml())
    prev = {100: {"div": 10, "pair": frozenset(("ALP", "LNC")), "tcp": {"ALP": 580, "LNC": 420}, "fp": {}, "formal": 1000}}
    S = au.static_tables(pre, prev)
    o = au.observe(au.parse_results(results_xml()), S)
    p = au.default_params() | {"K_d": 100.0, "K_s": 100.0, "K_n": 100.0}
    pj = au.project(o, S, p)
    i = pj["ids"].index(10)
    assert pj["mean"][i] > 0.52 and pj["kind"][i] == "matched" and pj["classic"][i]
    assert pj["pair"][pj["ids"].index(20)] is None
    sim = au.simulate(pj, o, S, p, 400, np.random.default_rng(0))
    assert sim["seats"].shape == (400, 4) and (sim["seats"].sum(1) == 2).all()
    assert (sim["win"][:, i] == au.SEAT_GROUPS.index("ALP")).mean() > 0.95        # 66% counted, prior 52%


def test_market_rules():
    seats = np.array([[94, 43, 1, 12], [70, 70, 1, 9], [60, 80, 2, 8], [74, 60, 4, 12]])
    win = np.zeros((4, 3), dtype=np.int8)
    win[0, 1] = 1
    hit = lambda m: au.market_hit(m, seats, win, [1, 2, 3]).tolist()    # noqa: E731
    assert hit({"kind": "most", "party": "ALP"}) == [True, False, False, True]
    assert hit({"kind": "most", "party": "OTH"}) == [False, False, False, False]
    assert hit({"kind": "majority", "party": "ALP"}) == [True, False, False, False]
    assert hit({"kind": "majority", "party": "LNC"}) == [False, False, True, False]
    assert hit({"kind": "seats", "party": "ALP", "lo": 85, "hi": 150}) == [True, False, False, False]
    assert hit({"kind": "seats", "party": "GRN", "lo": 4, "hi": 150}) == [False, False, False, True]
    assert hit({"kind": "margin", "lo": 40, "hi": 150}) == [True, False, False, False]
    assert hit({"kind": "margin", "lo": -9, "hi": 0}) == [False, True, False, False]       # a tie belongs to the Coalition band
    assert hit({"kind": "margin", "lo": -150, "hi": -10}) == [False, False, True, False]
    assert hit({"kind": "div", "div": 2, "groups": ["LNC"]}) == [True, False, False, False]
    assert hit({"kind": "hung"}) == [False, True, False, True]                              # nobody reaches 76


def test_seats_of_final_counts_winner_groups():
    fin = {1: {"winner": "ALP"}, 2: {"winner": "LNC"}, 3: {"winner": "IND:RYAN"}, 4: {"winner": "GRN"}}
    seats, win = au.seats_of_final(fin, [1, 2, 3, 4])
    assert seats.tolist() == [1, 1, 1, 1] and win.tolist() == [0, 1, 3, 2]
