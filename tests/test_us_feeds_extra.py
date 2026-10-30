"""brunder.us_feeds_extra: PA, MN, NE and NM election-night parsers on official certified results, their priors, the
recorder keys, and that the parsers of the states covered before (CA … FL) are unchanged by the new hooks."""
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us
from brunder import us_feeds_extra as X

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us"


def rd(name: str) -> bytes:
    b = (FIX / name).read_bytes()
    return gzip.decompress(b) if name.endswith(".gz") else b


def _state(df: pd.DataFrame, race: str) -> pd.Series:
    s = df[(df["level"] == "state") & (df["race"] == race)]
    return s.groupby("party")["votes"].sum()


def _county_sum_is_state(df: pd.DataFrame) -> None:
    c = df[df["level"] == "county"].groupby(["race", "candidate"])["votes"].sum()
    s = df[df["level"] == "state"].set_index(["race", "candidate"])["votes"]
    assert np.allclose(c.sort_index().to_numpy(), s.sort_index().to_numpy())


# ------------------------------------------------------------------ parsers on certified results

def test_parse_pa_2022_governor_certified():
    df = us.parse_feed("pa", {"gov": rd("pa_2022_gov_countybreak.json.gz")})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 67 and set(c["unit_id"]) == set(us.counties("pa")["unit_id"])
    s = _state(df, "gov")
    assert s["DEM"] == 3031137 and s["REP"] == 2238477               # Shapiro, Mastriano (PA DoS certified)
    adams = c[(c["unit_id"] == "us-pa-42001") & (c["party"] == "DEM")].iloc[0]
    assert adams["votes"] == 18821 and adams["early"] == 7425       # mail-in = early
    assert c["reporting"].isna().all()
    _county_sum_is_state(df)


def test_parse_pa_2026_test_file_and_empty():
    df = us.parse_feed("pa", {"gov": rd("pa_2026_gov_countybreak.json.gz")})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 67 and c["votes"].sum() == 0
    assert set(c["candidate"]) >= {"JOSH SHAPIRO", "STACY GARRITY"} and {"DEM", "REP"} <= set(c["party"])
    assert us.parse_feed("pa", {"gov": None}).empty


def test_parse_mn_2022_governor_and_2024_senate_certified():
    g = us.parse_feed("mn", {"county": rd("mn_20221108_allracesbycounty_subset.txt.gz")})
    assert g[g["level"] == "county"]["unit_id"].nunique() == 87
    s = _state(g, "gov")
    assert s["DEM"] == 1312349 and s["REP"] == 1119941               # Walz (DFL), Jensen (MN SoS canvass)
    assert set(g["race"]) == {"gov"}                                 # other offices in the file are skipped
    hen = g[(g["unit_id"] == "us-mn-27053") & (g["party"] == "DEM")].iloc[0]     # Hennepin
    assert hen["reporting"] == hen["units"] == 397                 # every Hennepin precinct reported
    _county_sum_is_state(g)
    s24 = _state(us.parse_feed("mn", {"county": rd("mn_20241105_allracesbycounty_subset.txt.gz")}), "sen")
    assert s24["DEM"] == 1792441 and s24["REP"] == 1291712          # Klobuchar, White


def test_parse_mn_2026_latin1_test_file():
    raw = rd("mn_20261103_allracesbycounty_subset.txt.gz")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")                                          # ISO-8859-1, as the SoS publishes it
    X.CHECKS[X.FEEDS["mn"]["county"]](raw)                           # complete text is accepted …
    with pytest.raises(ValueError):
        X.CHECKS[X.FEEDS["mn"]["county"]](raw[:-7])                  # … a truncated one is not
    df = us.parse_feed("mn", {"county": raw})
    c = df[df["level"] == "county"]
    assert c["unit_id"].nunique() == 87 and set(c["race"]) == {"gov", "sen"}
    assert set(c[c["race"] == "sen"]["candidate"]) >= {"Peggy Flanagan", "Michele Tafoya"}
    assert set(c[c["race"] == "gov"]["candidate"]) >= {"Amy Klobuchar and Ben Schierer", "Lisa Demuth and Ryan Wilson"}


def test_mn_county_id_is_alphabetical_fips():
    names = {u: n for u, n in X._county_ids("mn").values()}
    tbl = [ln.split(";") for ln in rd("mn_cntytbl.txt.gz").decode("latin-1").splitlines() if ln.strip()]
    assert len(tbl) == 87
    for cid, name, _ in tbl:
        assert X._key(names[f"us-mn-{27000 + 2 * int(cid) - 1}"]) == X._key(name)


def test_parse_ne_2020_senate_certified_and_2026_service():
    df = us.parse_feed("ne", {"sen": rd("ne_2020_sen_mapdata.json.gz")})
    assert df[df["level"] == "county"]["unit_id"].nunique() == 93
    s = _state(df, "sen")
    assert s["REP"] == 583507 and s["DEM"] == 227191                 # Sasse, Janicek (NE SoS canvass 2020)
    _county_sum_is_state(df)
    live = us.parse_feed("ne", {"sen": rd("ne_2026_sen_mapdata.json.gz"), "gov": rd("ne_2026_gov_mapdata.json.gz")})
    c = live[live["level"] == "county"]
    assert c["unit_id"].nunique() == 93 and set(c["race"]) == {"sen", "gov"} and c["votes"].sum() == 0
    osb = c[(c["race"] == "sen") & (c["candidate"] == "Dan Osborn")]
    assert set(osb["party"]) == {"PET"} and len(osb) == 93
    st = live[(live["level"] == "state") & (live["race"] == "sen") & (live["party"] == "REP")].iloc[0]
    assert st["reporting"] == 0 and st["units"] == 1322              # statewide precincts in the 2026 file


def test_parse_nm_certified_and_2026_unreported_star():
    df = us.parse_feed("nm", {"gov": rd("nm_2022_gov_mapdata.json.gz"), "sen": rd("nm_2024_sen_mapdata.json.gz")})
    assert df[df["level"] == "county"]["unit_id"].nunique() == 33
    g, s = _state(df, "gov"), _state(df, "sen")
    assert g["DEM"] == 370168 and g["REP"] == 324701                 # Lujan Grisham, Ronchetti (NM SoS canvass)
    assert s["DEM"] == 497333 and s["REP"] == 405978                 # Heinrich, Domenici
    assert us.parse_feed("nm", {"gov": None, "sen": None}).empty
    live = us.parse_feed("nm", {"gov": rd("nm_2026_gov_mapdata.json.gz"), "sen": rd("nm_2026_sen_mapdata.json.gz")})
    c = live[live["level"] == "county"]
    assert c["unit_id"].nunique() == 33 and c["votes"].sum() == 0     # '*' before a county reports
    assert set(c[c["race"] == "gov"]["candidate"]) == {"DEB HAALAND AND STEPHANIE GARCIA RICHARD",
                                                      "GREGGORY D HULL AND DAVID M GALLEGOS"}


def test_mapdata_wrong_race_yields_no_rows():
    j = json.loads(rd("nm_2026_sen_mapdata.json.gz"))
    for x in j:
        x["RaceName"] = "Secretary of State"
    assert us.parse_feed("nm", {"sen": json.dumps(j).encode()}).empty


# ------------------------------------------------------------------ recorder keys, checks, priors, configs

def test_feed_keys_distinct_and_recorded_urls():
    keys = [k for f in X.FEEDS.values() for k in f.values()]
    assert len(set(keys)) == len(keys) == len(X.URLS)
    assert sorted(keys) == sorted(us._name(u) for u in X.URLS)
    for st in X.FEEDS:
        assert us.FEEDS[st] == X.FEEDS[st] and st in us.STATES and st in us.POLL_CLOSE and st in us.SOURCE_INFO
    unit = (ROOT / "deploy" / "brunder-snap-us.service").read_text()
    for u in X.URLS:
        assert f"--url '{u}'" in unit


def test_pa_check_rejects_truncated_json_string():
    raw = rd("pa_2026_gov_countybreak.json.gz")
    X.CHECKS[X.FEEDS["pa"]["gov"]](raw)
    with pytest.raises(ValueError):
        X.CHECKS[X.FEEDS["pa"]["gov"]](raw[:-40])


def test_archive_uses_feed_checks(tmp_path):
    key = X.FEEDS["mn"]["county"]
    d = tmp_path / key
    d.mkdir()
    raw = rd("mn_20261103_allracesbycounty_subset.txt.gz")
    (d / "20261104T020000.000000Z.html").write_bytes(raw)
    (d / "20261104T020030.000000Z.html").write_bytes(raw[:-7])      # truncated: skipped
    arc = us.Archive(tmp_path)
    arc.refresh()
    f = arc.at(key, pd.Timestamp("2026-11-04T03:00Z"))
    assert f is not None and f.name.startswith("20261104T020000")


@pytest.mark.parametrize("src", sorted(X.CERTIFIED))
def test_public_priors_match_certified(src):
    p = us.load_priors(path=us.PUBLIC_PRIORS)
    p = p[p["source"] == src]
    d, r = X.CERTIFIED[src]
    assert abs(p["dem"].sum() - d) <= 1e-4 * d and abs(p["rep"].sum() - r) <= 1e-4 * r   # AZ: MEDSL ±22 votes
    st = src[:2]
    assert set(p["unit_id"]) == set(us.counties(st)["unit_id"])


NEW = ["pa_gov", "mn_sen", "mn_gov", "ne_sen", "ne_gov", "nm_gov", "nm_sen", "az_gov"]


@pytest.mark.parametrize("race", NEW)
def test_new_configs_load_with_public_priors(race):
    for v in (1, 2, 3):
        p = ROOT / "configs" / f"us2026_{race}_v{v}.toml"
        if not p.exists():
            assert race == "nm_gov" and v == 3                     # no polls: v2 = v1, no v3
            continue
        cfg = us.load_config(p)
        pri = us.race_prior(cfg, us.load_priors(path=us.PUBLIC_PRIORS))
        assert pri is not None and len(pri) == len(us.counties(cfg["election"]["state"]))
        assert cfg["election"]["poll_close_utc"] == us.POLL_CLOSE[cfg["election"]["state"]]


@pytest.mark.parametrize("race,fix", [("pa_gov", {"gov": "pa_2022_gov_countybreak.json.gz"}),
                                      ("mn_gov", {"county": "mn_20221108_allracesbycounty_subset.txt.gz"}),
                                      ("nm_gov", {"gov": "nm_2022_gov_mapdata.json.gz"})])
def test_model_on_a_finished_count_recovers_the_result(race, fix):
    """The model fed a complete certified count projects that count's winner with near certainty."""
    cfg = us.load_config(ROOT / "configs" / f"us2026_{race}_v1.toml")
    st = cfg["election"]["state"]
    rows = us.parse_feed(st, {k: rd(v) for k, v in fix.items()})
    pri = us.race_prior(cfg, us.load_priors(path=us.PUBLIC_PRIORS))
    res, units = us.project(rows, cfg, pri)
    assert res["p_win"] > 0.99                                     # the 2022 Democrat (party DEM) won each


# ------------------------------------------------------------------ states covered before: unchanged

CASES = {   # every pre-existing parser on every pre-existing fixture, through parse_feed
    "ca": lambda: us.parse_feed("ca", {"results": rd("ca_governor_county_all.json"), "status": rd("ca_status.json")}),
    "tx24": lambda: us.parse_feed("tx", {"election": rd("tx_53814_election.json"),
                                         "county": rd("tx_53814_countyInfo.json.gz")}),
    "tx26": lambda: us.parse_feed("tx", {"election": rd("tx_53815_election.json"),
                                         "county": rd("tx_53815_countyInfo.json.gz")}),
    "oh": lambda: us.parse_feed("oh", {"xml": rd("oh_vssc1622.xml.gz"), "csv": rd("oh_summary.csv")}),
    "ak": lambda: us.parse_feed("ak", {"js": rd("ak_statewide.js.gz")}),
    "ga": lambda: us.parse_feed("ga", {"item": [rd("ga_2024_president_item.json.gz")]}),
    "az": lambda: us.parse_feed("az", {"item": [rd("az_2026_governor_item_testdata.json.gz")]}),
    "sc22": lambda: us.parse_feed("sc", {"all": rd("sc_115412_ALL.json.gz"), "summary": rd("sc_115412_summary.json.gz"),
                                         "status": rd("sc_115412_status.json.gz")}),
    "sc24": lambda: us.parse_feed("sc", {"all": rd("sc_122436_ALL.json.gz"), "summary": rd("sc_122436_summary.json.gz"),
                                         "status": rd("sc_122436_status.json.gz")}),
    "vt24": lambda: us.parse_feed("vt", {"main": rd("vt_2024_main.json.gz"),
                                         "statewide": rd("vt_2024_statewide_governor.json.gz")}),
    "vt26": lambda: us.parse_feed("vt", {"main": rd("vt_2026_main.json.gz"),
                                         "statewide": rd("vt_2026_statewide_governor.json.gz")}),
    "nc22": lambda: us.parse_feed("nc", {"county": rd("nc_20221108_county.txt.gz"),
                                         "results": [rd(f"nc_20221108_results_{i}.txt.gz") for i in (1, 60, 92)]}),
    "nc26": lambda: us.parse_feed("nc", {"county": rd("nc_20261103_county.txt.gz"),
                                         "results": [rd("nc_20261103_results_92.txt.gz")]}),
    "fl22": lambda: us.parse_feed("fl", {"enight": rd("fl_20221108_official_subset.txt.gz")}),
    "fl26": lambda: us.parse_feed("fl", {"enight": rd("fl_20261103_enight_subset.txt.gz")}),
}
HEAD_DIGESTS = FIX / "parse_digests_head.json"   # computed with src/ of commit 3fa8783, before the new hooks


def digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()


@pytest.mark.parametrize("case", sorted(CASES))
def test_existing_parsers_byte_identical_to_head(case):
    assert digest(CASES[case]()) == json.loads(HEAD_DIGESTS.read_text())[case]


def test_existing_feed_keys_unchanged():
    head = json.loads(HEAD_DIGESTS.read_text())["_feeds"]
    for k, v in head.items():
        assert us.FEEDS[k] == v
