"""analysis/us_ev_pace.py, us_ev_uf2022.py and the pace entry of us_ev_site.py: day alignment, pace, share of certified 2022 votes,
flags, and the parse of an archived 2022 state page. Fixtures are tiny synthetic tables."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_ev_pace as pace  # noqa: E402
import us_ev_site as site  # noqa: E402
import us_ev_uf2022 as uf22  # noqa: E402


def hist_rows(*r):
    return pd.DataFrame(r, columns=["st", "year", "kind", "is_final", "days_before", "asof_date", "ev_total"]).assign(asof_date=lambda d: pd.to_datetime(d["asof_date"]))


def uf_rows(*r):
    return pd.DataFrame(r, columns=["st", "capture", "as_of", "days_before", "total", "definition"])


def test_nearest_exact_and_prefers_earlier_day_on_tie():
    assert pace.nearest([9, 6, 5, 2], 5) == 2
    assert pace.nearest([6, 4], 5) == 0                      # tie: the earlier day (more days before), never a later count
    assert pace.nearest([10, 1], 5) is None                   # both beyond MAX_GAP
    assert pace.nearest([], 5) is None


def test_build_pace_gap_flag_and_certified_share():
    now = {"value": 600.0, "date": "2026-10-31", "db": 3, "source": "official", "measure": "m"}
    then = {"value": 400.0, "date": "2022-11-04", "db": 4, "source": "official", "measure": "m", "definition_ok": True}
    e = pace.build(now, then, 2000.0)
    assert e["pace"] == pytest.approx(1.5) and e["gap"] == 1 and e["flags"] == []     # one day off is the same day
    assert e["pct22"] == pytest.approx(30.0) and e["now"]["source_label"] == "official" and e["then"]["source_label"] == "official"
    far = pace.build(now, {**then, "db": 6}, 2000.0)
    assert far["gap"] == 3 and far["flags"] == ["gap"]
    bad = pace.build({**now, "source": "compiled"}, {**then, "source": "uf2022", "definition_ok": False}, None)
    assert bad["flags"] == ["definition"] and bad["pct22"] is None and bad["now"]["source_label"] == "compiled (UF)" and bad["then"]["source_label"] == "archived UF 2022"


def test_build_without_then_keeps_certified_share_and_reason():
    e = pace.build({"value": 50.0, "date": "2026-10-31", "db": 3, "source": "compiled", "measure": "m"}, None, 200.0, "why not")
    assert e["pace"] is None and e["then"] is None and e["pct22"] == pytest.approx(25.0) and e["why"] == "why not"


def test_official_then_aligns_and_explains():
    h = hist_rows(("nc", 2022, "total", False, 7, "2022-11-01", 100), ("nc", 2022, "total", False, 4, "2022-11-04", 300), ("nc", 2022, "total", True, 0, "2022-11-08", 900),
                  ("nc", 2020, "total", False, 3, "2020-10-31", 700))
    t, why = pace.official_then(h, "total", 3)
    assert t["value"] == 300 and t["db"] == 4 and t["date"] == "2022-11-04" and not why              # final row and other cycles never used
    t, why = pace.official_then(h, "total", 12)
    assert t is None and "4 to 7" in why
    assert pace.official_then(h, "mail", 3)[0] is None
    assert "published no past" in pace.official_then(h.iloc[0:0], "total", 3)[1]


def test_uf_then_definition_match():
    r = uf_rows(("ks", "20221105", "2022-11-05", 3, 900.0, pace.BOTH), ("ks", "20221101", "2022-11-01", 7, 400.0, pace.BOTH))
    t, _ = pace.uf_then(r, 3, True, False)
    assert t["value"] == 900 and t["source"] == "uf2022" and t["definition_ok"] and t["capture"] == "20221105"
    r2 = uf_rows(("pa", "20221105", "2022-11-05", 3, 900.0, "mail returned only (no in-person early voting reported)"))
    assert pace.uf_then(r2, 3, True, False)[0]["definition_ok"] is False        # 2026 has in-person too: not the same measure
    assert pace.uf_then(r2, 3, False, True)[0]["definition_ok"] is True
    none, why = pace.uf_then(r.iloc[1:], 3, True, False)
    assert none is None and "7 to 7" in why
    assert "no archived page" in pace.uf_then(r.iloc[0:0], 3, True, False)[1]


def test_certified_total_is_largest_office():
    d = pd.DataFrame({"st": ["nc", "nc", "nc", "nc"], "year": [2022, 2022, 2020, 2022], "office": ["sen", "house_total", "sen", "gov"], "total_all": [3773555.0, 3760381.0, 9e9, None]})
    assert pace.certified_2022(d) == {"nc": 3773555.0}


def test_parse_archived_state_page():
    html = ("<html><script>x</script><body><p>Last update: 11-5-2022</p><p>Total Early Votes: <b>1,500</b> In-Person Early Votes: 600 "
            "Mail Ballots Returned: 900 Mail Ballots Requested: 2,000</p></body></html>")
    p = uf22.parse_page(html)
    assert p == {"as_of": "2022-11-05", "days_before": 3, "total": 1500, "inperson": 600, "mail": 900}
    assert uf22.definition(p) == pace.BOTH
    assert uf22.definition({**p, "inperson": None}) == "total as the page states it (parts do not add up to it)"
    assert uf22.definition({"total": 900, "inperson": None, "mail": 900}).startswith("mail returned only")
    assert uf22.parse_page("<p>no date</p>") is None


def state_dict(total, inperson, db=3, compiled=False, mail=True):
    return {"latest": {"total": total, "inperson": inperson, "date": "2026-10-31", "db": db}, "compiled": compiled, "basis": "Ballots returned.",
            "methods": {"mail": "mail_returned" if mail else None, "inperson": "inperson_early" if inperson else None}}


def test_state_pace_official_then_falls_back_to_uf_and_explains():
    hist = {"state": hist_rows(("nc", 2022, "total", False, 3, "2022-11-05", 500), ("nc", 2022, "total", False, 7, "2022-11-01", 200),
                               ("nc", 2022, "inperson", False, 3, "2022-11-05", 1)).assign(st=lambda d: d["st"])}
    cert = {"nc": 5000.0, "ks": 1000.0, "ak": 100.0}
    e = site.state_pace("nc", state_dict(1000, 400), hist, cert, uf_rows())
    assert e["pace"] == pytest.approx(2.0) and e["then"]["source"] == "official" and e["pct22"] == pytest.approx(20.0) and e["gap"] == 0
    uf = uf_rows(("ks", "20221105", "2022-11-05", 3, 800.0, pace.BOTH))
    k = site.state_pace("ks", state_dict(1200, 300, compiled=True), hist, cert, uf)
    assert k["pace"] == pytest.approx(1.5) and k["then"]["source"] == "uf2022" and k["now"]["source"] == "compiled" and k["flags"] == []
    a = site.state_pace("ak", state_dict(50, None, compiled=True), hist, cert, uf)
    assert a["pace"] is None and a["pct22"] == pytest.approx(50.0) and "no past day-by-day" in a["why"] and "no archived page" in a["why"]
    far = site.state_pace("nc", state_dict(1000, 400, db=12), hist, cert, uf_rows())
    assert far["pace"] is None and "covers 3 to 7" in far["why"]
