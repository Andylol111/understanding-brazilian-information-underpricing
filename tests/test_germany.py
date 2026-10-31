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
