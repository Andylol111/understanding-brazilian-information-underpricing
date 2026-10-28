"""brunder.bulgaria: CIK result pages and open-data archives (real fixtures), prior, MIR nowcast, frozen config.

Fixtures (official CIK files, from Internet Archive captures; tests/fixtures/bulgaria):
  pvr202610_rezultati_{index,06,32}.html.gz  2026 first round, national / Vratsa (MIR 06) / abroad result pages
  pvrns2021_tur2_export_mir06.zip            2021 runoff open-data archive, rows of MIR 06 only
  pvrnr2016_export_mir06.zip                 2016 archive (both rounds), rows of MIR 06 only
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from brunder import bulgaria as bg
from brunder import nowcast

F = Path(__file__).parent / "fixtures" / "bulgaria"
CONFIG = Path(__file__).parents[1] / "configs" / "bg2026r2_v1.toml"


def page(name: str) -> bg.Page:
    return bg.parse_page(gzip.decompress((F / name).read_bytes()).decode("utf-8"))


# ------------------------------------------------------------------ result pages

def test_national_page():
    p = page("pvr202610_rezultati_index.html.gz")
    assert p.scope == "national"
    # "Данни към 15:00 на 26.10.2026": Sofia time, EET (UTC+2) after DST ended on 25 Oct
    assert p.as_of == pd.Timestamp("2026-10-26T13:00:00Z")
    assert p.processed == 1.0
    assert len(p.votes) == 27
    assert bg.pick(p.votes, "Йотова") == 528_929
    assert bg.pick(p.votes, "Гюров") == 486_214
    assert p.none == 67_353
    assert p.numbers["Илияна Малинова Йотова и Кирил Александров Вълчев"] == "25"
    assert p.numbers["Андрей Атанасов Гюров и Георги Димитров Кандев"] == "4"


def test_mir_and_abroad_pages():
    v = page("pvr202610_rezultati_06.html.gz")
    assert (v.scope, v.processed) == ("06", 1.0)
    assert (bg.pick(v.votes, "Йотова"), bg.pick(v.votes, "Гюров"), v.none) == (13_242, 5_835, 1_385)
    a = page("pvr202610_rezultati_32.html.gz")
    assert a.scope == bg.ABROAD
    assert a.as_of == pd.Timestamp("2026-10-26T08:00:00Z")
    assert (bg.pick(a.votes, "Йотова"), bg.pick(a.votes, "Гюров"), a.none) == (18_465, 37_101, 2_342)


def test_pick_uses_the_presidential_candidate_only():
    votes = {"Румен Георгиев Радев и Илияна Малинова Йотова": 10, "Анастас Герджиков и Невяна Митева": 5}
    with pytest.raises(KeyError):
        bg.pick(votes, "Йотова")                      # 2021: Iotova was the vice-presidential candidate
    assert bg.pick(votes, "Радев") == 10


def test_not_a_results_page():
    challenge = "<html><head><title>Just a moment...</title></head><body>Enable JavaScript</body></html>"
    with pytest.raises(ValueError, match="Данни към"):
        bg.parse_page(challenge)
    text = gzip.decompress((F / "pvr202610_rezultati_06.html.gz").read_bytes()).decode()
    with pytest.raises(ValueError, match="self-link"):
        bg.parse_page(text.replace("rezultati/06.html", "x/06.html"))


def test_read_pages_and_frame(tmp_path: Path):
    for n in ("pvr202610_rezultati_index.html.gz", "pvr202610_rezultati_06.html.gz"):
        shutil.copy(F / n, tmp_path / n)
    (tmp_path / "sub").mkdir()
    shutil.copy(F / "pvr202610_rezultati_06.html.gz", tmp_path / "sub" / "again.html.gz")   # saved twice
    (tmp_path / "challenge.html").write_text("<title>Just a moment...</title>")
    pages = bg.read_pages(tmp_path)
    assert len(pages) == 3
    df = bg.pages_frame(pages, "Йотова", "Гюров")
    assert len(df) == 2                                # the duplicate save collapses
    nat = df[df["scope"] == "national"].iloc[0]
    assert nat["a"] + nat["b"] + nat["other"] == sum(page("pvr202610_rezultati_index.html.gz").votes.values())


# ------------------------------------------------------------------ open-data archives

def test_export_2021_runoff():
    z = F / "pvrns2021_tur2_export_mir06.zip"
    assert bg.read_export_candidates(z) == {
        "6": "Румен Георгиев Радев и Илияна Малинова Йотова",
        "15": "Анастас Георгиев Герджиков и Невяна Михайлова Митева-Матеева"}
    v = bg.read_export_votes(z)
    assert set(v["mir"]) == {"06"} and v["section"].nunique() == 330
    f = bg.export_frame(z, "Радев", "Герджиков")
    assert f[["scope", "a", "b", "other"]].iloc[0].tolist() == ["06", 38_191, 14_187, 0]
    s = bg.read_export_sections(z)
    assert len(s) == 330 and s["town"].sum() == 176


def test_export_2016_needs_the_round():
    z = F / "pvrnr2016_export_mir06.zip"
    with pytest.raises(ValueError, match="part"):
        bg.read_export_votes(z)
    r2 = bg.export_frame(z, "Радев", "Цачева", "13.11.2016/")
    assert r2[["a", "b", "other"]].iloc[0].tolist() == [57_567, 29_151, 0]
    r1 = bg.export_frame(z, "Радев", "Цачева", "06.11.2016/")
    assert r1[["a", "b", "other"]].iloc[0].tolist() == [30_538, 21_553, 39_217]


def test_vote_fields_from_readme():
    import zipfile
    with zipfile.ZipFile(F / "pvrns2021_tur2_export_mir06.zip") as z:
        assert bg._vote_fields(bg._member(z, "readme")) == 2
    with zipfile.ZipFile(F / "pvrnr2016_export_mir06.zip") as z:
        assert bg._vote_fields(bg._member(z, "readme", "13.11.2016/")) == 5
    assert bg._vote_fields(None) == 2


# ------------------------------------------------------------------ regions, prior, model

def test_mirs_and_units():
    assert sorted(bg.MIRS) == [f"{i:02d}" for i in range(1, 33)]
    assert {v[1] for k, v in bg.MIRS.items() if k != bg.ABROAD} == {f"{i:02d}" for i in range(1, 29)}
    assert {v[2] for v in bg.MIRS.values()} == set(bg.REGIONS)
    assert bg.unit_id("23") == "bg-bg41-23" and bg.unit_id("32") == "bg-zz-32"


def synthetic_r1() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for m in bg.MIRS:
        tot = int(rng.integers(30_000, 90_000))
        a = int(tot * rng.uniform(0.25, 0.45))
        b = int(tot * rng.uniform(0.2, 0.4))
        rows.append({"scope": m, "as_of": pd.NaT, "processed": 1.0, "a": a, "b": b, "other": tot - a - b, "none": 0})
    return pd.DataFrame(rows)


def test_prior_rules():
    r1 = synthetic_r1()
    tw = bg.prior_from_frame(r1, {"rule": "two_way"}).set_index("cd_mun")
    ev = bg.prior_from_frame(r1, {"rule": "fixed", "to_a": 0.5}).set_index("cd_mun")
    r = r1.set_index("scope")
    assert np.allclose(tw["s0"], r["a"] / (r["a"] + r["b"]))
    assert np.allclose(ev["s0"], (r["a"] + 0.5 * r["other"]) / (r["a"] + r["b"] + r["other"]))
    assert tw.loc["32", "uf"] == "zz" and tw.loc["23", "uf"] == "bg41"
    with pytest.raises(ValueError):
        bg.prior_from_frame(r1, {"rule": "magic"})


def night_from(final: pd.DataFrame, fractions: list[float], t0: str = "2026-11-01T18:30:00Z") -> pd.DataFrame:
    """Pages over the night: every MIR reports the same fraction of its final votes at each step."""
    rows = []
    for i, q in enumerate(fractions):
        t = pd.Timestamp(t0) + pd.Timedelta(minutes=15 * i)
        for _, r in final.iterrows():
            rows.append({"scope": r["scope"], "as_of": t, "processed": q, "a": round(q * r["a"]),
                         "b": round(q * r["b"]), "other": 0, "none": 0})
        rows.append({"scope": "national", "as_of": t, "processed": q, "a": round(q * final["a"].sum()),
                     "b": round(q * final["b"].sum()), "other": 0, "none": 0})
    return pd.DataFrame(rows)


def test_replay_converges_to_the_final_count():
    cfg = bg.load_config(CONFIG)
    r1 = synthetic_r1()
    prior = bg.prior_from_frame(r1, cfg["prior"]["transfer"])
    final = r1.assign(a=(r1["a"] * 1.4).round(), b=r1["b"] * 1.1)[["scope", "a", "b"]]
    night = night_from(final, [0.02, 0.2, 0.6, 1.0])
    tl = bg.replay(cfg, prior, night)
    assert len(tl) == 4 and (tl["mirs"] == 32).all()
    true = final["a"].sum() / (final["a"] + final["b"]).sum()
    assert tl["counted"].iloc[-1] == pytest.approx(1.0)
    assert tl["share"].iloc[-1] == pytest.approx(true, abs=1e-6)
    assert tl["sd"].is_monotonic_decreasing
    assert tl["P"].iloc[-1] == pytest.approx(float(true > 0.5), abs=1e-6)
    assert tl["national_processed"].tolist() == [0.02, 0.2, 0.6, 1.0]
    assert bg.outcome(night)["share"] == pytest.approx(true, abs=1e-4)


def test_latest_uses_only_pages_up_to_t():
    final = synthetic_r1()[["scope", "a", "b"]]
    night = night_from(final, [0.1, 0.5])
    cur = bg.latest(night, pd.Timestamp("2026-11-01T18:40:00Z"))
    assert (cur["processed"] == 0.1).all() and len(cur) == 33


def test_settle_time():
    idx = pd.date_range("2026-11-01T18:00Z", periods=5, freq="15min")
    s = pd.Series([0.6, 0.95, 0.8, 0.92, 0.99], index=idx)
    assert bg.settle_time(s, True, 0.9) == idx[3]           # the dip at 18:30 resets it
    assert bg.settle_time(1 - s, False, 0.9) == idx[3]
    assert bg.settle_time(pd.Series([0.95, 0.97], index=idx[:2]), True, 0.9) == idx[0]
    assert bg.settle_time(s, False, 0.9) is None


def test_night_stats_keys():
    cfg = bg.load_config(CONFIG)
    r1 = synthetic_r1()
    prior = bg.prior_from_frame(r1, cfg["prior"]["transfer"])
    final = r1.assign(a=(r1["a"] * 1.4).round(), b=r1["b"] * 1.1)[["scope", "a", "b"]]
    night = night_from(final, [0.02, 0.06, 0.2, 0.6, 1.0])
    tl = bg.replay(cfg, prior, night).assign(pm=np.nan, kalshi=np.nan)
    st = bg.night_stats(tl, bg.outcome(night), cfg)
    assert set(st) == {"calibration", "settle", "early_error", "evidence"}
    assert st["settle"]["pm"] is None and st["settle"]["kalshi"] is None
    assert st["settle"]["model"] is not None and st["calibration"]["stamps"] == 4     # 2, 6, 20, 60% counted
    assert set(st["early_error"]) == {"1%", "5%", "10%", "25%"}


# ------------------------------------------------------------------ frozen config

def test_config():
    cfg = bg.load_config(CONFIG)
    assert cfg["id"] == "bg2026r2_v1"
    assert cfg["election"]["poll_close_utc"] == "2026-11-01T18:00:00Z"
    p = bg.params(cfg)
    assert isinstance(p, nowcast.Params) and p.unpooled == ("zz",)
    for k, v in cfg["model"].items():
        assert getattr(p, k) == v
    assert cfg["prior"]["transfer"]["rule"] in ("two_way", "fixed")
    assert cfg["candidates"]["a_surname"] == "Йотова" and cfg["candidates"]["b_surname"] == "Гюров"


def test_cli_prior_from_pages(tmp_path: Path, capsys):
    """`brunder bulgaria prior` refuses an incomplete first round (here: 3 of 33 pages)."""
    for n in ("pvr202610_rezultati_index.html.gz", "pvr202610_rezultati_06.html.gz", "pvr202610_rezultati_32.html.gz"):
        shutil.copy(F / n, tmp_path / n)
    with pytest.raises(SystemExit, match="missing"):
        bg.cli(SimpleNamespace(step="prior", config=CONFIG, r1=tmp_path, r2=None, no_markets=True))
