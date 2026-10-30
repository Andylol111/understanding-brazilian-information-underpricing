"""v3 options of ``brunder.newzealand``: off by default (v1/v2 draws bit-identical) and sensible when on."""
import hashlib
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import newzealand as nz

CONFIGS = Path(__file__).parent.parent / "configs"


def _units() -> pd.DataFrame:
    pr = nz.load_prior()
    el = pd.DataFrame({"name": list(pr.index)}, index=range(1, len(pr) + 1))
    el["region"] = np.where(el.index <= 64, "gen", "maori")
    el["unit_id"] = [f"nz-{r}-{i:02d}" for i, r in zip(el.index, el["region"])]
    return nz.prior_units(el, pr)


def _counts(u: pd.DataFrame, frac: float) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    c, n = nz.empty_counts(u), len(u)
    c["places_total"] = 20.0
    c["places"] = np.floor(frac * 20.0)
    c["f"] = c["places"] / 20.0
    for k in nz.PARTY_KEYS:
        c[f"pv_{k}"] = np.floor(c["f"] * u["V"] * u[k] * rng.uniform(.8, 1.2, n))
        c[f"cv_{k}"] = np.floor(c["f"] * u["V"] * u[k] * rng.uniform(.7, 1.3, n))
    c["cv_IND"] = np.floor(c["f"] * u["V"] * 0.03)
    c["valid"] = sum(c[f"pv_{k}"] for k in nz.PARTY_KEYS)
    return c


# Seat draws of the v1 Settings() as produced by the last committed model, before any v3 option existed (src/brunder/newzealand.py at eb42e13): sha256 of the
# int64 seat array, first 16 hex digits.  A v3 change that perturbs v1/v2 output (even the order of random draws) fails here.
GOLDEN = {0.0: "cbc224c73fe380a0", 0.35: "5ec0f7ca8d1955f0", 1.0: "7ba3240743da44b3"}


@pytest.mark.parametrize("frac", sorted(GOLDEN))
def test_v1_settings_draw_bit_identical_seats(frac):
    u = _units()
    st = nz.Settings(draws=500)
    p = nz.project(u, _counts(u, frac), st)
    _, _, seats = nz.seat_draws(p, st, np.random.default_rng(20261107))
    assert hashlib.sha256(seats.tobytes()).hexdigest()[:16] == GOLDEN[frac]


def test_v1_and_v2_configs_leave_every_v3_option_off():
    off = nz.Settings()
    for name in ("nz2026_v1.toml", "nz2026_v2.toml"):
        cfg = nz.load_config(CONFIGS / name)
        st = nz.settings(cfg)
        assert st == replace(off, **{f.name: getattr(st, f.name) for f in fields(st) if f.name in cfg["model"]})
        assert st.count_log_sd == 0 and st.poll_shares is None and st.special_shift is None
        assert st.advance_shrink == 0 and st.advance_file is None and st.error_df == 0 and st.bloc_log_sd == 0


def test_v3_config_inherits_markets_and_turns_options_on():
    v2 = nz.load_config(CONFIGS / "nz2026_v2.toml")
    v3 = nz.load_config(CONFIGS / "nz2026_v3.toml")
    assert [m["rid"] for m in v3["markets"]] == [m["rid"] for m in v2["markets"]]
    assert v3["id"] == "nz2026_v3" and v3["seed"] == v2["seed"] and v3["feed"] == v2["feed"]
    st = nz.settings(v3)
    assert st.count_log_sd > 0 and st.error_df > 2 and st.special_log_sd > 0
    assert set(st.poll_shares) <= set(nz.PARTY_KEYS) and 0.5 < sum(st.poll_shares.values()) < 1
    assert set(st.special_shift) <= set(nz.PARTY_KEYS)
    assert st.special_shift["GP"] > 0 > st.special_shift["NAT"]       # specials lean left / overseas / Maori


def test_poll_tilt_hits_the_night_scale_target():
    u = _units()
    parties = list(nz.PARTY_KEYS)
    prior = np.maximum(u[parties].to_numpy(float), 1e-6)
    prior /= prior.sum(1, keepdims=True)
    V = u["V"].to_numpy(float)
    poll = {"NAT": .30, "LAB": .30, "GP": .12, "ACT": .10, "NZF": .09, "TPMAO": .03, "OPP": .05}
    shift = {"NAT": -.03, "GP": .06}
    s = nz.tilt_to_poll(prior, V, parties, poll, shift)
    nat = (V[:, None] * s).sum(0) / V.sum()
    i = parties.index("NAT")
    for k, v in poll.items():
        # the count is on the election-night scale: poll share times exp(-shift), relative to National
        want = (v * np.exp(-shift.get(k, 0.0))) / (poll["NAT"] * np.exp(-shift["NAT"]))
        assert nat[parties.index(k)] / nat[i] == pytest.approx(want, rel=1e-3)
    assert np.allclose(s.sum(1), 1)


def test_special_shift_moves_final_shares_by_the_shift_and_tails_widen():
    u = _units()
    c = _counts(u, 1.0)
    base = nz.Settings(draws=2000, count_log_sd=0.12, count_exp=1.0, floor_log_sd=0.0, candidate_log_sd=0.0)
    shifted = replace(base, special_shift={"GP": 0.2})
    p = nz.project(u, c, base)
    v0, _ = nz.simulate(p, base, np.random.default_rng(1))
    v1, _ = nz.simulate(p, shifted, np.random.default_rng(1))
    gp = p.parties.index("GP")
    s0, s1 = v0[:, gp] / v0.sum(1), v1[:, gp] / v1.sum(1)
    assert s1.mean() / s0.mean() == pytest.approx(np.exp(0.2), rel=0.1)      # renormalisation dampens it slightly
    early = nz.project(u, _counts(u, 0.25), base)
    ve, _ = nz.simulate(early, base, np.random.default_rng(1))
    sd = lambda v: np.log(v[:, gp] / v.sum(1)).std()
    assert sd(ve) > sd(v0) + 0.03                                           # more uncertainty with less counted
    heavy = replace(base, error_df=4.0)
    vh, _ = nz.simulate(early, heavy, np.random.default_rng(1))
    assert sd(vh) == pytest.approx(sd(ve), rel=0.25)                       # unit-variance t keeps the scale


def test_advance_split_identities():
    u = _units()
    parties = list(nz.PARTY_KEYS)
    rng = np.random.default_rng(3)
    n, K = len(u), len(parties)
    prior = np.maximum(u[parties].to_numpy(float), 1e-6)
    prior /= prior.sum(1, keepdims=True)
    prof = nz.AdvanceProfile(np.full(n, .6), np.full(n, .35), rng.normal(0, .1, (n, K)))
    a, d = nz._advance_split(prior, prof, 1.0)
    assert np.allclose(.6 * a + .4 * d, prior, atol=1e-3)
    assert np.allclose(a.sum(1), 1) and np.allclose(d.sum(1), 1)
    st = nz.Settings(draws=50, advance_shrink=1.0)
    # nothing counted and everything counted are unaffected by the advance / election-day split
    for frac in (0.0, 1.0):
        c = _counts(u, frac)
        p0, p1 = nz.project(u, c, nz.Settings(draws=50)), nz.project(u, c, st, advance=prof)
        assert np.allclose(p0.mean, p1.mean, rtol=1e-6)
    # advance places first: after the advance places the remainder is election-day-heavy
    part = _counts(u, 0.4)
    p0, p1 = nz.project(u, part, nz.Settings(draws=50)), nz.project(u, part, st, advance=prof)
    assert not np.allclose(p0.mean, p1.mean, rtol=1e-4)


def test_advance_profile_from_table_and_crosswalk():
    t = nz.load_advance_table(Path(__file__).parent.parent / "data_public" / "nz2023_advance_split.csv")
    assert len(t) == 72 and {"adv_places", "ed_places", "adv_NAT", "ed_NAT"} <= set(t.columns)
    names = ["Auckland Central", "Glendene", "Not A Place", "Kelston"]
    prof = nz.advance_profile(names, t, list(nz.PARTY_KEYS))
    assert prof.alpha.shape == (4,) and prof.dev.shape == (4, len(nz.PARTY_KEYS))
    assert np.all(prof.dev[2] == 0)                                         # unknown electorate: national average
    assert 0.4 < prof.alpha[0] < 0.9 and 0.1 < prof.place_frac[0] < 0.7
    assert prof.alpha[1] == prof.alpha[3] and np.all(prof.dev[1] == prof.dev[3])      # Glendene <- Kelston
