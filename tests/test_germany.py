"""Germany 2025 parsers, prior and count model (src/brunder/germany.py) on small real fixtures: result-page tables as the
Internet Archive captured them on 2025-02-23 and rows of the Bundeswahlleiterin's open-data files."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import germany as g

FIX = Path(__file__).parent / "fixtures" / "germany"


def page(name: str) -> dict:
    p = g.parse_page((FIX / name).read_text(encoding="utf-8"))
    assert p is not None
    return p


def test_national_page_stamp_and_votes():
    p = page("bund_de_2211.html")
    assert p["stamp"] == pd.Timestamp("2025-02-23 22:11:26") and (p["counted"], p["total"]) == (80, 299)
    v = g.to_votes(p)
    assert v["electorate"] == 15_630_492 and v["valid"] == 12_968_999        # second votes
    assert v["Union"] == 2_030_430 + 2_142_650                               # CDU + CSU
    assert v["BSW"] == 570_575 and v["AfD"] == 2_842_638


def test_english_wahlkreis_page():
    p = page("wk102_en.html")
    assert p["stamp"] == pd.Timestamp("2025-02-23 23:19:00") and (p["counted"], p["total"]) == (1, 1)
    v = g.to_votes(p)
    assert v["electorate"] == 216_618 and v["valid"] == 172_565 and v["Union"] == 49_129 and v["SPD"] == 34_679


def test_brandenburg_prints_greens_as_gruene_b90():
    v = g.to_votes(page("land12_de.html"))
    assert v["GRÜNE"] == 108_591 and v["Union"] == 297_987
    assert g.party_of("GRÜNE/B 90") == "GRÜNE" and g.party_of("DIE LINKE") == "Die Linke"
    assert g.party_of("Volt") == g.OTHER and g.party_of("CSU") == "Union"


def test_stamp_converted_to_utc_row():
    row = g._vec(page("wk102_en.html"), g.to_votes(page("wk102_en.html")))
    assert row["t"] == pd.Timestamp("2025-02-23 22:19:00", tz="UTC") and row["k"] == 1
    assert row["Übrige"] == pytest.approx(row["valid"] - sum(row[a] for a in g.PARTIES))
    assert row["Übrige"] > 0


def test_final_and_prior_files():
    wk, _ = g.read_kerg2(FIX / "kerg2_two_wahlkreise.csv")
    assert list(wk.index) == [102, 103] and set(wk["land"]) == {5}
    assert wk.loc[102, "electorate"] == 216_618 and wk.loc[102, "Union"] > 0 and wk.loc[102, "BSW"] > 0
    assert (wk[g.ALL].sum(axis=1) == wk["valid"]).all()
    u = g.read_umrechnung(FIX / "umrechnung_two_wahlkreise.csv")
    assert list(u.index) == [102, 103] and (u["BSW"] == 0).all() and (u["land"] == 5).all()
    assert (u[g.ALL].sum(axis=1) == u["valid"]).all()


def _setup(known: list[int]):
    wk, _ = g.read_kerg2(FIX / "kerg2_two_wahlkreise.csv")
    u = g.read_umrechnung(FIX / "umrechnung_two_wahlkreise.csv")
    prior = g.make_prior(u, wk["electorate"], 0.3)
    t = pd.Timestamp("2025-02-23 21:00", tz="UTC")
    rows = []
    for n in known:
        r = {"t": t, "k": 1, "electorate": wk.loc[n, "electorate"], "valid": wk.loc[n, "valid"],
             **{a: wk.loc[n, a] for a in g.ALL}}
        rows.append(pd.Series(r, name=n))
    wkdf = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["t", "k", "electorate", "valid", *g.ALL])
    night = {"wk": wkdf, "land": {}, "bund": pd.DataFrame(columns=["t"])}
    return night, prior, wk, t + pd.Timedelta(minutes=1)


def test_state_is_none_before_any_page():
    night, prior, _, t = _setup([])
    assert g.state_at(night, prior, t) is None


def test_projection_of_a_complete_count_is_the_result():
    night, prior, wk, t = _setup([102, 103])
    r = g.project(g.state_at(night, prior, t), night, prior)
    final = wk[g.ALL].sum() / wk["valid"].sum()
    assert r["f_rem"] == pytest.approx(0, abs=1e-9) and r["counted"] == pytest.approx(1.0)
    assert np.allclose(r["share"], final.to_numpy())


def test_partial_count_projection():
    night, prior, wk, t = _setup([102])
    r = g.project(g.state_at(night, prior, t), night, prior)
    assert r["share"].sum() == pytest.approx(1.0)
    assert 0.3 < r["f_rem"] < 0.7 and 0.3 < r["counted"] < 0.7
    # the other Wahlkreis is projected at its prior plus the swing seen in the counted one: Union gains on the 2021 prior
    assert r["share"][g.ALL.index("Union")] > g.make_prior(
        g.read_umrechnung(FIX / "umrechnung_two_wahlkreise.csv"), wk["electorate"], 0.3).loc[103, "Union"]


def test_party_sd_and_draws():
    p = g.Params()
    sh = np.array([0.28, 0.21, 0.16, 0.12, 0.09, 0.05, 0.04, 0.05])
    assert (g.party_sd(sh, 0.0, p) == p.sd_floor).all() and (g.party_sd(sh, 1.0, p) > g.party_sd(sh, 0.3, p)).all()
    C = g.correlation(None, [], -0.1)
    chol = np.linalg.cholesky(C)
    z = np.random.default_rng(0).standard_normal((4000, g.NP))
    d = g.draw_shares(sh, 0.5, p, chol, z)
    assert np.allclose(d.sum(axis=1), 1.0) and abs(d[:, 0].mean() - 0.28) < 0.01


def test_market_events():
    d = np.array([[0.30, 0.20, 0.17, 0.12, 0.09, 0.04, 0.05, 0.03],
                  [0.25, 0.26, 0.17, 0.12, 0.09, 0.05, 0.04, 0.02]])
    assert g.event_prob(d, {"kind": "max", "party": "Union"}) == 0.5
    assert g.event_prob(d, {"kind": "second", "party": "AfD"}) == 0.5
    assert g.event_prob(d, {"kind": "atleast", "party": "BSW", "x": 0.05}) == 0.5
    assert g.event_prob(d, {"kind": "range", "party": "Union", "lo": 0.28, "hi": 0.30}) == 0.0
    mk = [{"market": "a", "kind": "max", "party": "Union"}, {"market": "b", "kind": "range", "party": "SPD", "lo": 0.15, "hi": 0.2}]
    assert g.all_probs(d, mk) == {"a": 0.5, "b": 1.0}
    assert g.rule({"kind": "atleast", "party": "BSW", "x": 0.05}, d[0]) is False


def test_btw17_kerg_reader_final_and_vorperiode():
    f = g.read_btw17_kerg(FIX / "btw17_kerg_two_wahlkreise.csv")
    v = g.read_btw17_kerg(FIX / "btw17_kerg_two_wahlkreise.csv", previous=True)
    assert list(f.index) == [1, 2] and set(f["land"]) == {1}          # the Land rows (gehört zu 99) are not Wahlkreise
    assert (f[g.ALL].sum(axis=1) == f["valid"]).all() and (v[g.ALL].sum(axis=1) == v["valid"]).all()
    assert f.loc[1, "Union"] > 0 and (f["BSW"] == 0).all() and v.loc[1, "AfD"] > 0
    assert f.loc[1, "electorate"] != v.loc[1, "electorate"]


def test_poll_prior_matches_the_poll_and_keeps_the_pattern():
    u = g.read_umrechnung(FIX / "umrechnung_two_wahlkreise.csv")
    wk, _ = g.read_kerg2(FIX / "kerg2_two_wahlkreise.csv")
    prior = g.make_prior(u, wk["electorate"], 0.3)
    poll = {"Union": 0.30, "AfD": 0.21, "SPD": 0.15, "GRÜNE": 0.13, "Die Linke": 0.07, "BSW": 0.045, "FDP": 0.04,
            "Übrige": 0.055}
    tilted = g.poll_prior(prior, poll)
    w = (tilted["E"] * tilted["tau0"]).to_numpy()
    nat = (tilted[g.ALL].to_numpy() * w[:, None]).sum(axis=0) / w.sum()
    assert np.allclose(tilted[g.ALL].sum(axis=1), 1.0)
    assert np.allclose(nat, [poll[a] for a in g.ALL], atol=1e-6)
    # proportional swing keeps the order of the two Wahlkreise for a party
    assert (tilted.loc[102, "Union"] > tilted.loc[103, "Union"]) == (prior.loc[102, "Union"] > prior.loc[103, "Union"])


def test_prior_votes_zero_is_unchanged_and_large_holds_the_prior():
    night, prior, wk, t = _setup([102])
    st = g.state_at(night, prior, t)
    base = g.project(st, night, prior)
    assert np.allclose(base["share"], g.project(st, night, prior, g.Params(prior_votes=0.0))["share"])
    held = g.project(st, night, prior, g.Params(prior_votes=1e12, kappa=1e12))
    # with the swing shrunk to nothing the uncounted Wahlkreis stays at its prior
    row = prior.loc[103, g.ALL].to_numpy(float)
    assert np.allclose(held["s_wk"][list(held["ids"]).index(103)], row / row.sum())
    assert abs(held["share"][g.ALL.index("Union")] - base["share"][g.ALL.index("Union")]) > 1e-4


def test_poll_table_parser_and_window_dates():
    import importlib.util
    spec = importlib.util.spec_from_file_location("de_polls", Path(__file__).parent.parent / "analysis" / "de_polls.py")
    dp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dp)
    assert dp._dates("27\u201330 Dec 2024", 2025) == (pd.Timestamp("2024-12-27"), pd.Timestamp("2024-12-30"))
    assert dp._dates("28 Sep \u2013 1 Oct 2021", 2021) == (pd.Timestamp("2021-09-28"), pd.Timestamp("2021-10-01"))
    html = ("<table><tr><th>Polling firm</th><th>Fieldwork date</th><th>Samplesize</th><th>Union</th><th>AfD</th><th>SPD</th>"
            "<th>Others</th></tr><tr><td>2025 federal election</td><td>23 Feb 2025</td><td>\u2013</td><td>28.5</td><td>20.8</td>"
            "<td>16.4</td><td>34.3</td></tr><tr><td>INSA[2]</td><td>21\u201322 Feb 2025</td><td>2,005</td><td>29.5</td><td>21</td>"
            "<td>15</td><td>34.5</td></tr><tr><td>Old</td><td>1 Jan 2024</td><td>1,000</td><td>31</td><td>19</td><td>16</td>"
            "<td>34</td></tr></table>")
    polls, result = dp.parse_page(html, 2025)
    assert len(polls) == 1 and polls.loc[0, "pollster"] == "INSA" and polls.loc[0, "Union"] == 29.5
    assert result["Union"] == 28.5
