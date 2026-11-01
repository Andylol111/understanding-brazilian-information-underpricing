"""Parsers of analysis/us_ev_src_f.py (group counts: race, age, gender) on trimmed real excerpts of the published files."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))
import us_ev_db as db  # noqa: E402
import us_ev_src_f as f  # noqa: E402

F = Path(__file__).parent / "fixtures" / "us_ev"


def get(rows, measure, group, state=None):
    got = [r["count"] for r in rows if r["measure"] == measure and r["age_band"] == group and (state is None or r["state"] == state)]
    assert len(got) == 1, (measure, group, got)
    return got[0]


def test_registry():
    db.load_extensions()
    for sid in (f.SRC_SC, f.SRC_CO, f.SRC_UF):
        assert db.SOURCES[sid].fn is not None
    assert db.SOURCES[f.SRC_LA].fn is None and "robots" in db.SOURCES[f.SRC_LA].access


def test_sc_race():
    r = f.parse_sc_race((F / "sc_absentee_race_excerpt.xlsx").read_bytes(), "2026-10-30")
    assert get(r, "mail_returned", "race:Black") == 10242 and get(r, "mail_returned", "race:White") == 26589
    assert get(r, "mail_requested", "race:Black") == 13326
    assert sum(x["count"] for x in r if x["measure"] == "mail_returned") == 37868
    assert {x["party"] for x in r} == {"ALL"} and {x["state"] for x in r} == {"sc"}


def test_sc_race_must_add_up():
    import io

    import openpyxl
    wb = openpyxl.load_workbook(F / "sc_absentee_race_excerpt.xlsx")
    for row in wb["Sheet2"].iter_rows():
        if row[1].value == "Total":
            row[11].value += 1
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="do not add up"):
        f.parse_sc_race(buf.getvalue(), "2026-10-30")


def test_co_groups():
    r = f.parse_co_groups((F / "co_activity_trim.xlsx").read_bytes(), "2026-10-29")
    assert get(r, "total", "gender:Female") == 696284 and get(r, "total", "gender:Male") == 632350
    assert get(r, "total", "gender:Other / undisclosed") == 4380 + 2284
    assert get(r, "total", "age:18-24") == 32402 + 28000 + 975 + 409
    assert get(r, "total", "age:75+") == 143788 + 123652 + 334 + 185
    for g in ("gender:Female", "age:65-74", "age:18-24"):
        assert get(r, "mail_returned", g) + get(r, "inperson_early", g) == get(r, "total", g)
    assert sum(x["count"] for x in r if x["measure"] == "total" and x["age_band"].startswith("age:")) == 1335298


def test_uf_groups():
    r = f.parse_uf_groups((F / "uf_groups_excerpt.csv").read_bytes(), min_states=1)
    nc = {x["age_band"]: x["count"] for x in r if x["state"] == "nc" and x["measure"] == "total"}
    assert nc["race:White"] == 1849717 and nc["race:Black"] == 593810 and nc["age:18-25"] == 201148 and nc["gender:Female"] == 1495375
    assert get(r, "total", "race:Black", "sc") == 10242            # SC race covers mail ballots only
    assert not [x for x in r if x["state"] == "ga" and x["age_band"] == "race:Hispanic" and x["count"] <= 0]
    assert {x["as_of_date"] for x in r if x["state"] == "nc"} == {"2026-11-01"}
    la = {x["age_band"] for x in r if x["state"] == "la" and x["measure"] == "total"}
    assert la == {"race:White", "race:Black", "race:Other", "gender:Male", "gender:Female"}
    with pytest.raises(ValueError):
        f.parse_uf_groups((F / "uf_groups_excerpt.csv").read_bytes())          # fewer than 40 states


def test_la_stats():
    a = f.parse_la_ev_stats((F / "la_ev_stats_2026.txt").read_text())
    assert a["end_date"] == "2026-10-29" and a["votes"] == 527104
    assert a["groups"]["race:Black"] == 181076 and a["groups"]["gender:Female"] == 305688
    b = f.parse_la_ev_stats((F / "la_ev_stats_2022.txt").read_text())
    assert b["end_date"] == "2022-11-15" and b["votes"] == 377428 and b["groups"]["race:Other"] == 11644
    reg = f.parse_la_registration((F / "la_registration_2026.txt").read_text())
    assert reg == {"total": 2978143, "White": 1854793, "Black": 933052, "Other": 190298}


def test_derived_collectors(tmp_path, monkeypatch):
    """CO and SC rows are parsed from snapshots stored by the county collectors, without any request."""
    monkeypatch.setenv("BRUNDER_EV_ROOT", str(tmp_path))
    st = db.Store(tmp_path)
    try:
        body = (F / "co_activity_trim.xlsx").read_bytes()
        p = tmp_path / "raw" / "co"
        p.mkdir(parents=True)
        (p / "x_co_ev_county-xl-20261029GenElectionActivity.xlsx").write_bytes(body)
        st.db.execute("INSERT INTO snapshots(source_id,part,state,url,fetched_utc,file,valid) VALUES "
                      "('co_ev_county','xl-20261029GenElectionActivity','co','u','2026-10-31T00:00:00Z',"
                      "'raw/co/x_co_ev_county-xl-20261029GenElectionActivity.xlsx',1)")
        db.load_extensions()
        db.SOURCES[f.SRC_CO].fn(st)
        db.SOURCES[f.SRC_CO].fn(st)                                              # idempotent
        n = st.db.execute("SELECT COUNT(*), MIN(as_of_date) FROM ev_state WHERE source_id=?", (f.SRC_CO,)).fetchone()
        assert n[0] == 3 * (3 + 8) and n[1] == "2026-10-29"
    finally:
        st.close()
