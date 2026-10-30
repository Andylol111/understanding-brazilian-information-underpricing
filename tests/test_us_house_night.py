import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us
from brunder import us_house_night as hn

FX = Path(__file__).parent / "fixtures" / "us"
CONFIGS = sorted((us.ROOT_DIR / "configs" / "us2026_house").glob("*_v*.toml"))


def _gz(name: str) -> bytes:
    return gzip.decompress((FX / name).read_bytes())


@pytest.mark.parametrize("name,race", [
    ("U. S. REPRESENTATIVE DISTRICT 23", "house-23"),                        # TX Civix
    ("Representative in Congress, District 13", "house-13"),                 # FL RaceName (parser keys on USR)
    ("US HOUSE OF REPRESENTATIVES DISTRICT 01 ", "house-01"),                # NCSBE (after "(VOTE FOR 1)" is cut)
    ("U.S.  House of Representatives, District  7", "house-07"),             # SC Clarity
    ("Representative to Congress - District 09", "house-09"),                # OH NIST XML
    ("US House of Representatives - District 12", "house-12"),               # GA Enhanced Voting
    ("U.S. Representative in Congress - District No. 6", "house-06"),        # AZ Enhanced Voting
    ("STATE REPRESENTATIVE DISTRICT 71", None),
    ("State  House of Representatives, District  1", None),
    ("U. S. SENATOR", None),
])
def test_house_race_names(name, race):
    assert hn.house_race(name) == race


def test_fl_official_2024_district_totals_match_certified():
    rows = hn.parse_fl(_gz("house_fl_20241105_official_usr.txt.gz"))
    tot = hn.district_totals(rows)
    # certified 2024 (Florida Division of Elections official results; MEDSL precinct returns agree to the vote)
    assert tot.loc["house-02", ["dem", "rep"]].tolist() == [154323, 247957]
    assert tot.loc["house-13", ["dem", "rep"]].tolist() == [185930, 225636]
    assert rows.groupby("race")["unit_id"].nunique().to_dict() == {"house-02": 16, "house-13": 1}
    assert (rows["level"] == "county").all() and rows["early"].isna().all()


def test_nc_enr_county_files_house_rows():
    rows = hn.parse_nc(_gz("nc_20221108_county.txt.gz"), [_gz(f"nc_20221108_results_{i}.txt.gz") for i in (1, 60, 92)])
    wake = rows[(rows["unit_id"] == "us-nc-37183") & (rows["race"] == "house-02")].set_index("party")
    assert wake.loc["DEM", "votes"] == 190714 and wake.loc["REP", "votes"] == 104155     # Ross v Villaverde, Wake
    assert wake.loc["DEM", "early"] == 75031 + 19428                                    # one-stop + mail
    assert wake.loc["DEM", "reporting"] == wake.loc["DEM", "units"] == 145
    assert sorted(rows["race"].unique()) == ["house-02", "house-04", "house-12", "house-13", "house-14"]


def test_sc_clarity_house_rows_split_by_county():
    rows = hn.parse_sc(_gz("sc_115412_ALL.json.gz"), _gz("sc_115412_summary.json.gz"), _gz("sc_115412_status.json.gz"))
    assert set(rows["race"]) <= {f"house-{i:02d}" for i in range(1, 8)}
    assert set(rows["party"]) >= {"DEM", "REP"}
    # a county appears once per district it belongs to, never in two rows of the same district and candidate
    assert not rows.duplicated(["unit_id", "race", "candidate"]).any()


def test_tx_civix_house_progress_is_per_race():
    rows = hn.parse_tx(_gz("house_tx_53815_countyInfo_subset.json.gz"))
    wise = rows[rows["unit_id"] == "us-tx-48497"]
    assert set(wise["race"]) == {"house-13", "house-26"}                  # Wise County is split between two districts
    assert wise.groupby("race")["units"].first().to_dict() == {"house-13": 7.0, "house-26": 23.0}
    assert set(rows.loc[rows["race"] == "house-23", "party"]) == {"DEM", "REP", "IND"}
    assert (rows["votes"] == 0).all() and not rows["race"].str.startswith("gov").any()


def test_oh_xml_house_contests_by_county():
    rows = hn.parse_oh(_gz("house_oh_vssc_2026test_cd09_cd13.xml.gz"))
    assert rows.groupby("race")["unit_id"].nunique().to_dict() == {"house-09": 10, "house-13": 3}
    assert {"DEM", "REP"} <= set(rows["party"])
    assert rows["units"].notna().all()


def test_ga_az_enhanced_voting_items():
    ga = hn.parse_ev([_gz("house_ga_2024_cd02_item.json.gz")], "ga")
    assert hn.district_totals(ga).loc["house-02", ["dem", "rep"]].tolist() == [176028, 136473]   # certified 2024
    assert (ga["level"] == "county").all() and ga["early"].sum() > 0
    az = hn.parse_ev([_gz("house_az_2026_cd01_item.json.gz")], "az")
    assert set(az["race"]) == {"house-01"} and set(az["unit_id"]) == {"us-az-04013"}


def test_parse_feed_dispatch_and_existing_kinds_untouched():
    blob = _gz("house_fl_20241105_official_usr.txt.gz")
    house = us.parse_feed("fl_house", {"enight": blob})
    assert house.equals(hn.parse_fl(blob))
    assert us.parse_feed("fl", {"enight": blob}).empty                    # the statewide kind never sees House rows
    for st in ("tx", "oh", "sc", "nc", "fl"):
        assert us.FEEDS[f"{st}_house"] == us.FEEDS[st]
    assert us.FEEDS["ga"] == {"item": "ga_enr_Georgia_item_*"} and us.FEEDS["ga_house"] == {"item": "ga_enr_Georgia_house_*"}


def test_county_model_runs_on_a_district():
    """The unchanged brunder.us model on one district's counties: FL-02 2024 fully counted projects the count."""
    rows = hn.parse_fl(_gz("house_fl_20241105_official_usr.txt.gz"))
    pri = us.race_prior({"prior": {"share": "fl-02_pres_2024", "votes": "fl-02_pres_2024"},
                         "election": {"state": "fl"}}, us.load_priors())
    assert pri is not None and len(pri) >= 16
    cfg = {"election": {"state": "fl", "race": "house-02", "a": {"party": "DEM"}, "b": {"party": "REP"}},
           "model": {"tau": 0.04, "rho": 0.3, "sigma_mun0": 0.08},
           "count": {"turnout_ratio": 0.72, "f_complete": 0.98, "learn_turnout": True}}
    res, units = us.project(rows, cfg, pri)
    final = 154323 / (154323 + 247957)
    assert abs(res["share_hat"] - final) < 0.005 and res["p_win"] < 0.01
    empty, _ = us.project(us._frame([]), cfg, pri)
    assert abs(empty["share_hat"] - us.baseline_share(pri)) < 1e-9 and 0.02 < empty["share_sd"] < 0.15


@pytest.mark.skipif(not CONFIGS, reason="no House configs")
def test_house_configs_load_and_have_priors():
    cfgs, versions = us.load_races(CONFIGS)
    pr = us.load_priors()
    assert cfgs and all(c["election"]["race_id"] in versions for c in cfgs)
    for c in cfgs + [v for vs in versions.values() for v in vs.values()]:
        E = c["election"]
        st, nn = re.fullmatch(r"house:([A-Z]{2})-(\d\d)", E["race_id"]).groups()
        assert E["state"] == st.lower() and E["race"] == f"house-{nn}" and E["feed"] == f"{st.lower()}_house"
        assert E["feed"] in us.FEEDS and E["state"] in us.POLL_CLOSE
        pri = us.race_prior(c, pr)
        assert pri is not None and not pri.empty
        p = us.params(c)
        assert (p.swing_prior_sd is not None) == (us.config_version(c) == 2)
        assert c["markets"]["polymarket"]["market"] or c["markets"]["kalshi"] is not None
    assert np.all([us.config_version(c) == 1 for c in cfgs])


def test_priors_file_district_sums_positive():
    pr = pd.read_csv(us.ROOT_DIR / "data_public" / "us2026_house_priors.csv", comment="#")
    assert pr["source"].str.fullmatch(r"(tx|fl|nc|sc|oh|ga|az)-\d\d_pres_2024").all()
    assert pr.groupby("source")[["dem", "rep"]].sum().gt(0).all().all()
    assert not pr.duplicated(["source", "unit_id"]).any()


def test_house_exporter_writes_only_house_live(tmp_path):
    """The House process reads the recorder archive and writes only <out>/us2026/house_live/."""
    key = us.FEEDS["fl"]["enight"]
    (tmp_path / "snap" / key).mkdir(parents=True)
    (tmp_path / "snap" / key / "20261104T003000.000000Z.txt").write_bytes(_gz("house_fl_20241105_official_usr.txt.gz"))
    cfg = us.ROOT_DIR / "configs" / "us2026_house" / "fl-13_v1.toml"
    if not cfg.exists():
        pytest.skip("no FL-13 config")
    ex = hn.HouseExporter([cfg], tmp_path / "snap", max_retries=1)
    ex.update()
    d = ex.write(tmp_path / "out")
    assert d == tmp_path / "out" / "us2026" / "house_live"
    written = {p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file()}
    assert written == {f"us2026/house_live/{f}" for f in ("meta.json", "timeline.json", "timeline_tail.json",
                                                          "status.json", "units/house:FL-13.json")}
    row = json.loads((d / "timeline.json").read_text())["house:FL-13"][-1]
    assert row["counted"] > 0 and row["P"] is not None and row["P_v2"] is not None


def test_house_timeline_thinning_bounds_rows():
    t = pd.date_range("2026-11-04T00:00Z", "2026-11-07T00:00Z", freq="1min")
    rows = [{"t": us._iso(x), "P": 0.5} for x in t]
    out = hn.HouseExporter.thin(rows)
    ts = pd.to_datetime([r["t"] for r in out])
    assert out[-1] is rows[-1] and len(out) == len({r["t"] for r in out})
    assert (ts >= t[-1] - pd.Timedelta("6h")).sum() == 361                       # last 6 h: every minute
    mid = ts[(ts < t[-1] - pd.Timedelta("6h")) & (ts >= t[-1] - pd.Timedelta("48h"))]
    assert len(mid) == 42 * 6                                                    # 6–48 h: one per 10 minutes
    assert len(ts[ts < t[-1] - pd.Timedelta("48h")]) == 24                        # older: one per hour
    assert hn.HouseExporter.thin(out) == out                                      # idempotent
