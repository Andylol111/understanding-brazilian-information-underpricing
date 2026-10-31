"""brunder.france: parsers, size-order projection, tau at work (synthetic data, no network, no BRUNDER_DATA)."""

import numpy as np
import pandas as pd

from brunder import france as fr

FE_PAGE = """<html><body><table><tr><td>Election présidentielle 2022</td></tr></table>
<h4><font color="red">Résultats incomplets calculés sur la base de 86% des électeurs inscrits</font></h4>
<table><thead><tr><th>Liste des candidats </th><th>Voix</th><th>% Inscrits</th><th>% Exprimés</th></tr></thead><tbody>
<tr><td>M. Emmanuel MACRON</td><td>8 356 697</td><td>20,07</td><td>27,42</td></tr>
<tr><td>Mme Marine LE PEN</td><td>7 623 991</td><td>18,31</td><td>25,01</td></tr>
<tr><td>M. Jean-Luc MÉLENCHON</td><td>6 218 747</td><td>14,94</td><td>20,40</td></tr></tbody></table>
<table><thead><tr><th></th><th>Nombre</th><th>% Inscrits</th><th>% Votants</th></tr></thead><tbody>
<tr><td>Inscrits</td><td>41 630 970</td><td></td><td></td></tr><tr><td>Abstentions</td><td>10 443 659</td><td>25,09</td><td></td></tr>
<tr><td>Votants</td><td>31 187 311</td><td>74,91</td><td></td></tr><tr><td>Blancs</td><td>480 000</td><td>1,15</td><td>1,54</td></tr>
<tr><td>Nuls</td><td>225 677</td><td>0,54</td><td>0,72</td></tr><tr><td>Exprimés</td><td>30 481 634</td><td>73,22</td><td>97,74</td></tr>
</tbody></table></body></html>"""


def test_surname_and_slug():
    assert fr.surname("Mme Marine LE PEN") == "LE_PEN"
    assert fr.surname("M. Jean-Luc MÉLENCHON") == "MELENCHON"
    assert fr.surname("M. Nicolas DUPONT-AIGNAN") == "DUPONT_AIGNAN"
    assert fr.slug("Mélenchon") == "MELENCHON"


def test_parse_fe_reads_candidates_and_counted_base():
    st = fr.parse_fe(FE_PAGE.encode("latin-1"))
    assert st["votes"] == {"MACRON": 8356697, "LE_PEN": 7623991, "MELENCHON": 6218747}
    assert (st["inscrits"], st["exprimes"], st["pct_inscrits"]) == (41630970, 30481634, 86.0)
    assert fr.parse_fe(b"<html><body>Resultats non parvenus</body></html>") is None


def test_read_final_wide_format_groups_stations_by_commune(tmp_path):
    head = ";".join(["Code du département", "Libellé du département", "Code de la circonscription", "Libellé de la circonscription",
                     "Code de la commune", "Libellé de la commune", "Code du b.vote", "Inscrits", "Abstentions", "% Abs/Ins", "Votants",
                     "% Vot/Ins", "Blancs", "% Blancs/Ins", "% Blancs/Vot", "Nuls", "% Nuls/Ins", "% Nuls/Vot", "Exprimés", "% Exp/Ins",
                     "% Exp/Vot", "N°Panneau", "Sexe", "Nom", "Prénom", "Voix", "% Voix/Ins", "% Voix/Exp"])

    def row(bureau, ins, vot, exp, a, b):
        return (f"01;Ain;04;4e;001;X;{bureau};{ins};{ins - vot};0;{vot};0;{vot - exp};0;0;0;0;0;{exp};0;0;"
                f"1;M;MACRON;Emmanuel;{a};0;0;2;F;LE PEN;Marine;{b};0;0")
    p = tmp_path / "f.txt"
    p.write_text("\r\n".join([head, row("0001", 600, 500, 490, 300, 190), row("0002", 400, 300, 290, 100, 190)]), encoding="latin-1")
    d = fr.read_final(p)
    assert list(d.index) == ["01001"] and d.loc["01001", "inscrits"] == 1000
    assert (d.loc["01001", "MACRON"], d.loc["01001", "LE_PEN"], d.loc["01001", "exprimes"]) == (400, 380, 780)


def make_prior(sizes, s0, x0=0.7):
    s0 = np.asarray(s0, float)
    order = np.argsort(sizes)
    return fr.Prior(cats=["A", "B", "OTHER"], V=np.asarray(sizes, float)[order], x0=np.full(len(sizes), x0), s0=s0[order])


def test_project_counts_the_counted_exactly_and_fills_the_rest_from_the_prior():
    pr = make_prior([100, 200, 300, 400], [[.5, .3, .2]] * 4)
    p = fr.Params(kappa=0.0)
    # the smallest two units (300 registered) are in; they voted exactly as the prior says: no swing, the rest follows the prior
    o = fr.project(pr, 300, 210, np.array([105.0, 63.0]), p)
    assert np.allclose(o["share"], [.5, .3, .2], atol=1e-9)
    assert abs(o["counted"] - 0.3) < 1e-9 and o["lam"] == 1.0
    # A counts 10 points above the prior in the counted set: the ratio swing is carried to the uncounted units
    o = fr.project(pr, 300, 210, np.array([126.0, 63.0]), p)
    # ratios 1.2, 1, 0.5 on prior shares .5, .3, .2 already add to one: the uncounted units vote .6 / .3 / .1
    expect = (np.array([126, 63, 21.0]) + 490 * np.array([.6, .3, .1])) / (210 + 490)
    assert np.allclose(o["share"], expect, atol=1e-9)


def test_project_finished_count_returns_the_count_and_zero_sd():
    pr = make_prior([100, 200], [[.5, .3, .2]] * 2)
    o = fr.project(pr, 300, 210, np.array([100.0, 70.0]), fr.Params())
    assert np.allclose(o["share"], [100 / 210, 70 / 210, 40 / 210]) and o["counted"] == 1.0 and not o["sd0"].any()


def test_kappa_shrinks_the_swing_to_the_prior():
    pr = make_prior([100, 200, 300, 400], [[.5, .3, .2]] * 4)
    far = fr.project(pr, 300, 210, np.array([126.0, 63.0]), fr.Params(kappa=0.0))["share"][0]
    near = fr.project(pr, 300, 210, np.array([126.0, 63.0]), fr.Params(kappa=1e9))["share"][0]
    assert abs(near - (126 + 490 * .5) / 700) < 1e-3 and far > near


def test_order_error_widens_the_band_by_tau_times_uncounted():
    sd0 = np.array([0.001, 0.002])
    sd = fr.sd_at(sd0, 0.6, 0.02)
    assert np.allclose(sd, np.sqrt(sd0**2 + (0.4 * 0.02) ** 2))
    assert np.allclose(fr.sd_at(sd0, 1.0, 0.05), sd0)


def test_draws_sum_to_one_and_mixture_pools_the_taus():
    rng = np.random.default_rng(1)
    share, sd0 = np.array([.4, .35, .25]), np.array([.001, .001, .001])
    d = fr.mixture_draws(share, sd0, 0.5, (0.0, 0.04), 2000, rng)
    assert d.shape == (4000, 3) and np.allclose(d.sum(axis=1), 1.0, atol=1e-9)
    assert d[:2000, 0].std() < d[2000:, 0].std()


def test_pwin_two():
    assert abs(fr.pwin_two(0.5, 0.01) - 0.5) < 1e-12 and fr.pwin_two(0.6, 0.0) == 1.0 and fr.pwin_two(0.58, 0.01) > 0.999


def test_needed_tau_zero_when_errors_are_within_the_base_band():
    frames = pd.DataFrame({"counted": [0.5, 0.7, 0.9], "err_A": [0.0005, -0.0005, 0.0], "sd0_A": [0.002] * 3})
    assert fr.needed_tau(frames, ["A"]) == 0.0
    frames["err_A"] = [0.02, -0.02, 0.015]
    assert fr.needed_tau(frames, ["A"]) > 0.0
