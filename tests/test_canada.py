"""Parsers for Elections Canada / Élections Québec files (src/brunder/canada.py), on small inline samples."""

import gzip
import json
from datetime import datetime, timezone

import pandas as pd

from brunder import canada as ca

TABLE12 = (
    "\ufeffProvince,Electoral District Name/Nom de circonscription,Electoral District Number/Numéro de circonscription,"
    "Candidate/Candidat,Candidate Residence/Résidence du candidat,Candidate Occupation/Profession du candidat,"
    "Votes Obtained/Votes obtenus,Percentage of Votes Obtained /Pourcentage des votes obtenus,Majorité,Pourcentage\n"
    '"Newfoundland and Labrador/Terre-Neuve-et-Labrador","Avalon","10001","Paul Connors Liberal/Libéral","x","y",'
    "27563,58.6,10610,22.6\n"
    '"Newfoundland and Labrador/Terre-Neuve-et-Labrador","Avalon","10001","Steve Kent Conservative/Conservateur",'
    '"x","y",16953,36.0,,\n'
    '"Quebec/Québec","Abitibi—Témiscamingue","24001","A B Bloc Québécois","x","y",20000,40.0,1000,2.0\n'
    '"Quebec/Québec","Abitibi—Témiscamingue","24001","C D NDP-New Democratic Party/NPD-Nouveau Parti démocratique",'
    '"x","y",5000,10.0,,\n'
    '"Quebec/Québec","Abitibi—Témiscamingue","24001","E F Rhinoceros/Rhinocéros","x","y",100,0.2,,\n'
)

TRANS = (
    "\ufeffPROV_ID,PROV_NAME,FED_NUM,FED_FR,FED_EN,Bloc Québécois,Conservative Party of Canada/Parti conservateur du "
    "Canada,Liberal Party of Canada/Parti libéral du Canada,Marijuana Party/Parti Marijuana,Rejected_Ballots,"
    "Transposed_Votes,Registered_Electors,Estimated_Turnout\n"
    "10,NL,10001,Avalon,Avalon,NA,11636,17916,12,254,35468,67065,52.89%\n"
)


def test_unit_id_and_party():
    assert ca.unit_id(35075) == "ca-on-35075"
    assert ca.unit_id(10001) == "ca-nl-10001"
    assert ca.qc_unit_id(689) == "ca-qc-p689"
    assert ca.party_of("Paul Connors Liberal/Libéral") == "LPC"
    assert ca.party_of("X Y People's Party - PPC") == "PPC"
    assert ca.party_of("X Y Independent/Indépendant(e)") == "OTH"


def test_read_table12(tmp_path):
    p = tmp_path / "t12.csv"
    p.write_text(TABLE12, encoding="utf-8")
    v, names = ca.read_table12(p)
    assert list(v.columns) == list(ca.PARTIES)
    assert v.loc[10001, "LPC"] == 27563 and v.loc[10001, "CPC"] == 16953
    assert v.loc[24001, "BQ"] == 20000 and v.loc[24001, "NDP"] == 5000 and v.loc[24001, "OTH"] == 100
    assert names[24001] == "Abitibi—Témiscamingue"


def test_read_transposition(tmp_path):
    p = tmp_path / "tr.csv"
    p.write_text(TRANS, encoding="utf-8")
    v = ca.read_transposition(p)
    assert v.loc[10001, "LPC"] == 17916 and v.loc[10001, "CPC"] == 11636
    assert v.loc[10001, "BQ"] == 0 and v.loc[10001, "OTH"] == 12   # NA -> 0; rejected/electors not counted


def test_parse_enr_national():
    page = ("<html><table><tr><td>Conservative</td><td>149</td><td>43.4 %</td><td>6,125,604</td><td>41.7 %</td></tr>"
            "<tr><td>Liberal</td><td>163</td><td>47.5 %</td><td>6,291,763</td><td>42.9 %</td></tr>"
            "<tr><td>NDP-New Democratic Party</td><td>7</td><td>2.0 %</td><td>864,486</td><td>5.9 %</td></tr></table>"
            "Polls Reporting: 65,020 of 75,482 (86.14 %) Last updated at 00:43 ET</html>")
    s = ca.parse_enr_national(page, datetime(2025, 4, 29, 4, 46, 6, tzinfo=timezone.utc))
    assert s["updated"] == datetime(2025, 4, 29, 4, 43, tzinfo=timezone.utc)
    assert s["polls"] == (65020, 75482)
    assert s["seats"] == {"CPC": 149, "LPC": 163, "NDP": 7}
    assert s["votes"]["LPC"] == 6291763
    # an ET stamp later than the capture's ET clock time belongs to the previous day (capture 00:05 ET on 29 Apr)
    s = ca.parse_enr_national(page.replace("00:43", "23:59"), datetime(2025, 4, 29, 4, 5, tzinfo=timezone.utc))
    assert s["updated"] == datetime(2025, 4, 29, 3, 59, tzinfo=timezone.utc)


def test_parse_dgeq_gzip():
    d = {"statistiques": {"partisPolitiques": [
            {"abreviationPartiPolitique": "PQ", "nbCirconscriptionsEnAvance": 1},
            {"abreviationPartiPolitique": "PLQ/QLP", "nbCirconscriptionsEnAvance": 0}],
         "nbBureauVote": 120, "nbBureauVoteRempli": 60, "isResultatsFinaux": False,
         "iso8601DateMAJ": "2026-10-05T21:31:08,953-04:00"},
         "circonscriptions": [{"numeroCirconscription": 689, "nomCirconscription": "Abitibi-Est", "candidats": [
             {"abreviationPartiPolitique": "PQ", "nbVoteTotal": 700},
             {"abreviationPartiPolitique": "PLQ/QLP", "nbVoteTotal": 300},
             {"abreviationPartiPolitique": "PCOQ", "nbVoteTotal": 200},
             {"abreviationPartiPolitique": "PCQ/CPQ", "nbVoteTotal": 5}]}]}
    s = ca.parse_dgeq(gzip.compress(json.dumps(d).encode()))
    assert s["updated"] == pd.Timestamp("2026-10-06T01:31:08.953Z")
    assert s["boxes"] == (60, 120) and not s["final"]
    v = s["votes"]
    assert v.loc[689, "PQ"] == 700 and v.loc[689, "PCQ"] == 200 and v.loc[689, "OTH"] == 5
    assert s["leading"] == {"PQ": 1, "PLQ": 0} and s["names"][689] == "Abitibi-Est"


def test_market_lists_unique():
    for spec in (ca.federal_markets(), ca.quebec_markets()):
        ids = [m["race_id"] for m in spec]
        assert len(ids) == len(set(ids))
    assert len(ca.federal_markets()) == 26
