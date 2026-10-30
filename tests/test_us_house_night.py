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


def test_mn_official_2022_and_2024_district_totals_match_certified():
    """MN SoS allracesbycounty.txt (House lines of the official file): every district equals FEC 2022 / MEDSL 2024."""
    certified = {
        "2022": {"house-01": (125457, 159621), "house-02": (165583, 148576), "house-03": (198883, 134797),
                 "house-04": (200055, 95493), "house-05": (214224, 70702), "house-06": (120852, 198145),
                 "house-07": (84455, 204766), "house-08": (141009, 188755)},
        "2024": {"house-01": (156375, 220929), "house-02": (231751, 175621), "house-03": (240209, 170427),
                 "house-04": (242802, 117618), "house-05": (261066, 86213), "house-06": (155836, 260095),
                 "house-07": (114979, 275098), "house-08": (176724, 244498)},
    }
    for year, expected in certified.items():
        rows = hn.parse_mn(_gz(f"house_mn_{year}_ushouse.txt.gz"))
        tot = hn.district_totals(rows)
        assert {r: tuple(tot.loc[r, ["dem", "rep"]].astype(int)) for r in tot.index} == expected
        assert rows.groupby("race")["unit_id"].nunique()["house-02"] == 5 and rows["units"].gt(0).all()


def test_pa_official_2024_district_totals_match_certified():
    """PA DoS API (election 105 = 2024 general), office 11: the five configured districts equal MEDSL 2024."""
    rows = hn.parse_pa({f"d{n:02d}": _gz(f"house_pa_2024_cd{n:02d}.json.gz") for n in (1, 7, 8, 10, 16)})
    assert hn.district_totals(rows)[["dem", "rep"]].astype(int).to_dict("index") == {
        "house-01": {"dem": 202042, "rep": 261390}, "house-07": {"dem": 199626, "rep": 203688},
        "house-08": {"dem": 189411, "rep": 195663}, "house-10": {"dem": 200434, "rep": 205567},
        "house-16": {"dem": 146709, "rep": 256923}}
    assert rows.groupby("race")["unit_id"].nunique().to_dict() == {
        "house-01": 2, "house-07": 4, "house-08": 5, "house-10": 3, "house-16": 6}
    assert 0 < rows["early"].sum() < rows["votes"].sum() and rows["units"].isna().all()


def test_nm_official_2024_district_totals():
    """NM SoS archive (election 2882): the state's own canvass (MEDSL's precinct file differs by ≤ 26 votes)."""
    rows = hn.parse_map_house({f"d{n:02d}": _gz(f"house_nm_2024_cd{n:02d}.json.gz") for n in (1, 2, 3)}, "nm")
    assert hn.district_totals(rows)[["dem", "rep"]].astype(int).to_dict("index") == {
        "house-01": {"dem": 193203, "rep": 149546}, "house-02": {"dem": 138173, "rep": 127145},
        "house-03": {"dem": 162342, "rep": 126085}}
    assert rows.groupby("race")["unit_id"].nunique().to_dict() == {"house-01": 10, "house-02": 15, "house-03": 18}


def test_ne_2026_service_house_districts():
    """NE 2026 general (zeroed before the count): Sarpy is split between NE-01 and NE-02; a wrong race is dropped."""
    blobs = {f"d{n:02d}": _gz(f"house_ne_2026_cd{n:02d}.json.gz") for n in (1, 2)}
    rows = hn.parse_map_house(blobs, "ne")
    assert set(rows.loc[rows["race"] == "house-02", "unit_id"]) == {"us-ne-31055", "us-ne-31153", "us-ne-31155"}
    assert "us-ne-31153" in set(rows.loc[rows["race"] == "house-01", "unit_id"])
    assert {"DEM", "REP"} <= set(rows["party"]) and rows["votes"].sum() == 0
    assert hn.parse_map_house({"sen": _gz("ne_2026_sen_mapdata.json.gz")}, "ne").empty


def test_new_state_house_feeds_are_recorded():
    unit = (us.ROOT_DIR / "deploy" / "brunder-snap-us.service").read_text()
    for urls in us._extra.HOUSE_URLS.values():
        for u in urls.values():
            assert f"--url '{u}'" in unit
    for st in ("pa", "mn", "ne", "nm"):
        assert us.FEEDS[f"{st}_house"] == us._extra.HOUSE_FEEDS[f"{st}_house"]
    for c in CONFIGS:
        ev = us.load_config(c)["markets"]["polymarket"]["event"]
        assert not ev or f"--pm-event {ev} " in unit + " ", c.name


def _partial(rows: pd.DataFrame, frac: np.ndarray) -> pd.DataFrame:
    """A mid-count state: each unit's votes, early votes and precincts scaled by its own fraction."""
    f = rows["unit_id"].map(dict(zip(sorted(rows["unit_id"].unique()), frac))).to_numpy()
    out = rows.copy()
    for col in ("votes", "early", "reporting"):
        out[col] = np.floor(out[col] * f)
    return out


@pytest.mark.parametrize("case", ["fl", "nc", "pa", "mn"])
def test_numpy_projection_equals_us_project(case):
    """brunder.us_house_night.project = brunder.us.project (v1 and v2 configs, empty / partial / full counts)."""
    st, cds, rows = {
        "fl": ("fl", (2, 13), lambda: hn.parse_fl(_gz("house_fl_20241105_official_usr.txt.gz"))),
        "nc": ("nc", (1, 13, 14), lambda: hn.parse_nc(_gz("nc_20221108_county.txt.gz"),
                                                     [_gz(f"nc_20221108_results_{i}.txt.gz") for i in (1, 60, 92)])),
        "pa": ("pa", (1, 7, 8, 10, 16), lambda: hn.parse_pa(
            {f"d{n:02d}": _gz(f"house_pa_2024_cd{n:02d}.json.gz") for n in (1, 7, 8, 10, 16)})),
        "mn": ("mn", (1, 2, 6, 8), lambda: hn.parse_mn(_gz("house_mn_2024_ushouse.txt.gz"))),
    }[case]
    rows = rows()
    pr = us.load_priors()
    rng = np.random.default_rng(7)
    for cd in cds:
        for ver in (1, 2):
            p = us.ROOT_DIR / "configs" / "us2026_house" / f"{st}-{cd:02d}_v{ver}.toml"
            if not p.exists():
                continue
            c = us.load_config(p)
            for vt in (False, True):
                c = {**c, "count": {**c["count"], "vote_type": vt}}
                pri = us.race_prior(c, pr)
                n_units = rows["unit_id"].nunique()
                states = [us._frame([]), rows, _partial(rows, rng.uniform(0, 1, n_units)),
                          _partial(rows, rng.choice([0.0, 0.3, 1.0], n_units))]
                for r in states:
                    want, wu = us.project(r, c, pri)
                    got, gu = hn.project(r, c, pri)
                    assert set(got) == set(want)
                    for k, v in want.items():
                        assert (np.isnan(v) and np.isnan(got[k])) or got[k] == pytest.approx(v, rel=1e-9, abs=1e-9), k
                    assert list(gu.columns) == list(wu.columns)
                    for col in wu.columns:
                        if wu[col].dtype.kind == "f":
                            np.testing.assert_allclose(gu[col], wu[col], rtol=1e-9, atol=1e-6)
                        else:
                            assert (gu[col].to_numpy() == wu[col].to_numpy()).all(), col


def test_house_exporter_reprojects_only_changed_districts(tmp_path, monkeypatch):
    """A feed change that leaves a district's rows unchanged does not re-run its projection."""
    key = us.FEEDS["fl"]["enight"]
    d = tmp_path / "snap" / key
    d.mkdir(parents=True)
    blob = _gz("house_fl_20241105_official_usr.txt.gz")
    d.joinpath("20261104T003000.000000Z.txt").write_bytes(blob)
    cfgs = [us.ROOT_DIR / "configs" / "us2026_house" / f"fl-{n:02d}_v1.toml" for n in (2, 13)]
    ex = hn.HouseExporter(cfgs, tmp_path / "snap", max_retries=1)
    calls = []
    monkeypatch.setattr(ex, "project", lambda r, c, p: calls.append(c["election"]["race_id"]) or hn.project(r, c, p))
    ex.update(pd.Timestamp("2026-11-04T00:31Z"))
    first = len(calls)
    # FL-13 (Pinellas) changes, FL-02 does not: a new snapshot of the same file with one line edited
    lines = blob.decode("latin-1").splitlines(keepends=True)
    i = next(i for i, x in enumerate(lines) if "\tUSR\t" in x and "\tPIN\t" in x)
    f = lines[i].split("\t")
    f[-1] = str(int(f[-1]) + 1) + "\n"
    lines[i] = "\t".join(f)
    d.joinpath("20261104T003500.000000Z.txt").write_bytes("".join(lines).encode("latin-1"))
    ex.update(pd.Timestamp("2026-11-04T00:36Z"))
    later = calls[first:]
    assert later and set(later) == {"house:FL-13"}


def test_incremental_archive_lists_as_archive(tmp_path):
    """IncrementalArchive.refresh gives brunder.us.Archive's listing as files are added and removed."""
    a, b = us.Archive(tmp_path), hn.IncrementalArchive(tmp_path)
    (tmp_path / ".tmp").mkdir()
    for i, (key, ts) in enumerate([("k1", "20261104T003000.000000Z.json"), ("k2", "20261104T003015.500000Z.txt.gz"),
                                   ("k1", "20261104T002930.000000Z.json"), ("k1", "notes.txt"),
                                   ("k2", "20261104T003045.000000Z.txt.gz")]):
        (tmp_path / key).mkdir(exist_ok=True)
        (tmp_path / key / ts).write_bytes(b"{}")
        if i == 3:
            (tmp_path / "k1" / "20261104T003000.000000Z.json").unlink()
        a.refresh()
        b.refresh()
        assert b._files == a._files and b.times() == a.times()


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
    assert pr["source"].str.fullmatch(r"(tx|fl|nc|sc|oh|ga|az|pa|mn|ne|nm)-\d\d_pres_2024").all()
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
