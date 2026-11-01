import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import us_ev_src_e as e  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "us_ev"


def test_nyc_checkins_boroughs_to_counties():
    rows = e.parse_nyc_checkins((FX / "ny_nyc_checkins_excerpt.html").read_bytes())
    g = {(r["county_fips"], r["as_of_date"]): r["count"] for r in rows}
    assert len(rows) == 10
    assert g[("36061", "2026-10-24")] == 24704 and g[("36085", "2026-10-25")] == 13776
    assert g[("36047", "2026-10-25")] == 47177 and g[("36005", "2026-10-24")] == 9994 and g[("36081", "2026-10-24")] == 20839
    assert all(r["state"] == "NY" and r["measure"] == "inperson_early" and r["party"] == "ALL" for r in rows)


def test_nyc_checkins_total_mismatch_and_empty():
    bad = (FX / "ny_nyc_checkins_excerpt.html").read_bytes().replace(b"Staten Island - 7,382", b"Staten Island - 7,383")
    with pytest.raises(ValueError):
        e.parse_nyc_checkins(bad)
    with pytest.raises(ValueError):
        e.parse_nyc_checkins(b"<html>nothing</html>")


def test_clark_daily_cumulative_skips_unreported_day():
    rows = e.parse_clark_daily((FX / "nv_clark_ev_excerpt.html").read_bytes())
    assert [(r["as_of_date"], r["count"]) for r in rows] == [
        ("2026-10-17", 300), ("2026-10-18", 450), ("2026-10-19", 700), ("2026-10-24", 1000), ("2026-10-25", 1200)]
    assert all(r["county_fips"] == "32003" and r["state"] == "NV" and r["measure"] == "inperson_early" for r in rows)


def test_clark_daily_sum_mismatch():
    bad = (FX / "nv_clark_ev_excerpt.html").read_bytes().replace(b"<td>1,200</td>", b"<td>1,201</td>")
    with pytest.raises(ValueError):
        e.parse_clark_daily(bad)


def test_davidson_cumulative():
    rows = e.parse_davidson((FX / "tn_davidson_ev_excerpt.html").read_bytes())
    assert [(r["as_of_date"], r["count"]) for r in rows] == [("2026-10-14", 8393), ("2026-10-15", 18723), ("2026-10-17", 25780)]
    assert all(r["county_fips"] == "47037" and r["state"] == "TN" for r in rows)


def test_davidson_inconsistent():
    bad = (FX / "tn_davidson_ev_excerpt.html").read_bytes().replace(b"<td>18,723</td>", b"<td>18,724</td>")
    # cumulative values are not cross-checked day by day, but the Total column must match the sum of the daily row
    assert e.parse_davidson(bad)[1]["count"] == 18724
    bad2 = (FX / "tn_davidson_ev_excerpt.html").read_bytes().replace(b"<td>25,780</td></tr>\n<tr><td>Cumulative", b"<td>25,781</td></tr>\n<tr><td>Cumulative")
    with pytest.raises(ValueError):
        e.parse_davidson(bad2)
    with pytest.raises(ValueError):
        e.parse_davidson(b"<html></html>")


def test_uf_election_lab_secondary_rows():
    rows = e.parse_uf_election_lab((FX / "uf_election_lab_excerpt.csv").read_bytes(), min_states=3)
    g = {(r["state"], r["measure"], r["party"]): (r["as_of_date"], r["count"]) for r in rows}
    assert g[("AZ", "mail_requested", "ALL")] == ("2026-10-31", 3318151)
    assert g[("AZ", "total", "D")] == ("2026-10-31", 581774) and g[("AZ", "total", "U")][1] == 385952
    assert ("AZ", "inperson_early", "ALL") not in g                  # 0 = not reported = not stored
    assert g[("GA", "inperson_early", "ALL")][1] == 2704627 and ("GA", "total", "D") not in g
    assert e.UF_SOURCE_ID in e.db.SOURCES and e.db.SOURCES[e.UF_SOURCE_ID].kind == "secondary"


def test_uf_election_lab_bad_layout():
    with pytest.raises(ValueError):
        e.parse_uf_election_lab((FX / "uf_election_lab_excerpt.csv").read_bytes())      # only 3 states
    with pytest.raises(ValueError):
        e.parse_uf_election_lab(b"a,b\n1,2\n", min_states=1)


def test_secondary_rows_do_not_count_as_official_coverage(tmp_path):
    st = e.db.Store(root=tmp_path)
    try:
        rows = e.parse_uf_election_lab((FX / "uf_election_lab_excerpt.csv").read_bytes(), min_states=3)
        st.add_rows(rows, source_id=e.UF_SOURCE_ID, snapshot_id=1, level="state")
        assert st.db.execute("SELECT COUNT(*) FROM ev_state").fetchone()[0] == len(rows)
        t = e.db.status_table(st).set_index("state")
        assert t.loc["az", "latest_as_of"] == "" and t.loc["az", "measures"] == ""
    finally:
        st.close()


def test_alameda_vbm_party_rows():
    rows = e.parse_alameda_vbm((FX / "ca_alameda_vbm_excerpt.html").read_bytes(), "2026-10-31")
    g = {(r["measure"], r["party"]): r["count"] for r in rows}
    assert g[("mail_requested", "ALL")] == 983103 and g[("mail_returned", "ALL")] == 232920
    assert g[("mail_returned", "D")] == 152084 and g[("mail_requested", "U")] == 251531 and g[("mail_returned", "R")] == 29644
    assert g[("mail_returned", "other")] == 6129 + 943 + 1396 + 792
    assert all(r["county_fips"] == "06001" and r["as_of_date"] == "2026-10-31" for r in rows)


def test_alameda_vbm_rejects_wrong_year_and_bad_sum():
    body = (FX / "ca_alameda_vbm_excerpt.html").read_bytes()
    with pytest.raises(ValueError):
        e.parse_alameda_vbm(body, "2027-01-02")
    with pytest.raises(ValueError):
        e.parse_alameda_vbm(body.replace(b"29,644", b"29,645"), "2026-10-31")
