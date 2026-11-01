"""analysis/us_ev_canon.py, us_ev_daily.py, us_ev_site.py: one source per series (never summed), return-date files, the built JSON.
Fixtures are tiny aggregate tables (no voter-level data)."""
import io
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_ev_canon as canon  # noqa: E402
import us_ev_daily as daily  # noqa: E402
import us_ev_db as db  # noqa: E402
import us_ev_site as site  # noqa: E402


def rows(*r):
    return pd.DataFrame(r, columns=["state", "as_of_date", "measure", "party", "count", "source_id"])


# ------------------------------------------------------------------------------------------------------------ canon
def pa_two_sources():
    """The 2026-10-30 Pennsylvania figures that were once added up: DOS xlsx 1,041,756 and data.pa.gov 1,045,149."""
    return rows(
        ("pa", "2026-10-30", "mail_returned", "ALL", 1041756, "pa_mail_daily_xlsx"),
        ("pa", "2026-10-30", "mail_returned", "ALL", 1045149, "pa_mail_socrata"),
        ("pa", "2026-10-30", "mail_returned", "D", 675923, "pa_mail_socrata"),
        ("pa", "2026-10-30", "mail_returned", "D", 673855, "pa_mail_daily_xlsx"),
        ("pa", "2026-10-29", "mail_returned", "ALL", 1010823, "pa_mail_socrata"),
        ("pa", "2026-10-29", "mail_returned", "ALL", 1011048, "legacy_ev_pa_co_nm_pa"),
    )


def test_canonical_never_adds_two_sources():
    c = canon.canonical(pa_two_sources())
    top = c[(c["party"] == "ALL") & (c["as_of_date"] == "2026-10-30")]
    assert top["count"].tolist() == [1045149] and top["source_id"].tolist() == ["pa_mail_socrata"]    # not 2,086,905
    assert top["count"].iloc[0] != 1041756 + 1045149
    d = c[c["party"] == "D"]                                 # parties come from the same source as the ALL row
    assert d["count"].tolist() == [675923]
    day = c[(c["as_of_date"] == "2026-10-29") & (c["party"] == "ALL")]
    assert day["source_id"].tolist() == ["pa_mail_socrata"]  # live collector before the legacy panel


def test_canonical_fills_dates_from_lower_ranked_agreeing_source():
    r = rows(("nc", "2026-10-28", "total", "ALL", 100, "legacy_x"), ("nc", "2026-10-29", "total", "ALL", 200, "legacy_x"),
             ("nc", "2026-10-29", "total", "ALL", 201, "nc_abs_counts"), ("nc", "2026-10-30", "total", "ALL", 300, "nc_abs_counts"))
    c = canon.canonical(r)
    assert dict(zip(c["as_of_date"], c["source_id"])) == {"2026-10-28": "legacy_x", "2026-10-29": "nc_abs_counts", "2026-10-30": "nc_abs_counts"}
    assert canon.audit(r, r.assign(county_fips="1").iloc[0:0], c)["canonical_has_two_sources"] == []


def test_canonical_drops_a_source_that_contradicts_the_top_one():
    r = rows(("or", "2026-10-30", "total", "ALL", 1181637, "or_ballot_returns"), ("or", "2026-10-30", "total", "ALL", 1086787, "legacy_ev_long1_or"),
             ("or", "2026-10-20", "total", "ALL", 500000, "legacy_ev_long1_or"))
    c = canon.canonical(r)
    assert set(c["source_id"]) == {"or_ballot_returns"}      # the legacy panel is a different definition: not spliced in
    _, kept, _ = canon.prepare(r)
    assert canon.dropped_sources(r, kept) == [{"state": "or", "measure": "total", "source": "legacy_ev_long1_or"}]


def test_daily_source_ranks_first_and_party_all_is_completed():
    r = rows(("nc", "2026-10-30", "total", "D", 40, "daily_nc_voter_file"), ("nc", "2026-10-30", "total", "R", 60, "daily_nc_voter_file"),
             ("nc", "2026-10-30", "total", "ALL", 101, "nc_abs_counts"))
    c = canon.canonical(r)
    assert set(c["source_id"]) == {"daily_nc_voter_file"} and c[c["party"] == "ALL"]["count"].tolist() == [100]


def test_county_rows_must_add_up_to_state_row():
    st = rows(("pa", "2026-10-30", "mail_returned", "ALL", 30, "s"))
    co = pd.DataFrame([("pa", "42001", "2026-10-30", "mail_returned", "ALL", 10, "s"), ("pa", "42003", "2026-10-30", "mail_returned", "ALL", 19, "s")],
                      columns=["state", "county_fips", "as_of_date", "measure", "party", "count", "source_id"])
    bad = canon.county_vs_state(co, st)
    assert bad and bad[0]["county_sum"] == 29 and bad[0]["state_total"] == 30
    assert canon.county_vs_state(co.assign(count=[10, 20]), st) == []


# ------------------------------------------------------------------------------------------------------------- daily
def test_nc_count_counts_accepted_kinds_and_cures_only():
    head = ["county_desc", "ballot_rtn_dt", "ballot_req_type", "voter_party_code", "ballot_rtn_status"]
    body = [["WAKE", "10/29/2026", "EARLY VOTING", "DEM", "ACCEPTED"], ["WAKE", "10/29/2026", "EARLY VOTING", "UNA", "ACCEPTED"],
            ["WAKE", "10/29/2026", "MAIL", "REP", "ACCEPTED - CURED"], ["WAKE", "10/29/2026", "MAIL", "REP", "ACCEPTED - EXCEPTION"],
            ["WAKE", "10/29/2026", "MAIL", "REP", "PENDING CURE"], ["WAKE", "10/29/2026", "EARLY VOTING", "DEM", "CANCELLED"],
            ["WAKE", "", "MAIL", "DEM", "ACCEPTED"], ["WAKE", "10/29/2026", "MAIL"]]
    c = daily.nc_count([head] + body)
    assert c == {("WAKE", "2026-10-29", "inperson", "D"): 1, ("WAKE", "2026-10-29", "inperson", "U"): 1, ("WAKE", "2026-10-29", "mail", "R"): 2}
    df = daily.nc_frame(c)
    assert df["county_fips"].unique().tolist() == ["37183"] and df["n"].sum() == 4


def test_cumulative_from_inflow_adds_up_and_folds_earlier_days():
    inflow = pd.DataFrame([("nc", "37183", "2026-08-30", "mail", "D", 5), ("nc", "37183", "2026-09-05", "mail", "D", 2),
                           ("nc", "37183", "2026-09-06", "inperson", "R", 4), ("nc", "37001", "2026-09-06", "inperson", "D", 1),
                           ("nc", "37001", "2026-09-09", "mail", "U", 7)], columns=daily.COLS)
    co, st = daily.cumulative(inflow, "2026-09-08", "2026-09-04", {"mail": "mail_accepted", "inperson": "inperson_early"})
    g = lambda m, p, d: int(st[(st.measure == m) & (st.party == p) & (st.as_of_date == d)]["count"].iloc[0])      # noqa: E731
    assert g("mail_accepted", "ALL", "2026-09-04") == 5                       # the 30 Aug ballots are folded into the first day
    assert g("mail_accepted", "ALL", "2026-09-08") == 7 and g("inperson_early", "R", "2026-09-08") == 4
    assert g("total", "ALL", "2026-09-08") == 12 and g("total", "D", "2026-09-06") == 8
    assert st["as_of_date"].max() == "2026-09-08"                             # the 9 Sep ballot is after as_of: not counted
    tot = co[(co.measure == "total") & (co.party == "ALL") & (co.as_of_date == "2026-09-08")]["count"].sum()
    assert tot == g("total", "ALL", "2026-09-08")                             # counties add up to the state


def test_pa_frame_aggregates_bydate_without_voter_rows():
    body = json.dumps({"rows_updated_utc": "2026-10-31T08:52:14+00:00", "status": [],
                       "bydate": [{"countyname": "ADAMS", "party": "DEM", "ballotreturneddate": "2026-10-29T00:00:00.000", "n": "5"},
                                  {"countyname": "ADAMS", "party": "GRN", "ballotreturneddate": "2026-10-29T00:00:00.000", "n": "2"},
                                  {"countyname": "ADAMS", "party": "OTH", "ballotreturneddate": "2026-10-29T00:00:00.000", "n": "1"}]}).encode()
    f = daily.pa_frame(body)
    assert f[f.party == "other"]["n"].iloc[0] == 3 and f[f.party == "D"]["n"].iloc[0] == 5
    assert daily.pa_as_of(body) == "2026-10-30"


# -------------------------------------------------------------------------------------------------------------- site
def test_lead_is_democratic_minus_republican_share_of_returned_ballots():
    s = lambda *v: pd.Series(v, dtype=float)       # noqa: E731
    got = site.lead_pp(s(50, 30), s(30, 30), s(15, 30), s(5, 10))
    assert got.round(1).tolist() == [20.0, 0.0]
    assert site.lead_pp(s(50), s(None), s(1), s(1)).isna().all()


def tiny_db(path: Path, sources=True):
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    for sid, st, kind in (("pa_mail_daily_xlsx", "pa", "county"), ("pa_mail_socrata", "pa", "county"), ("uf_election_lab", "us", "secondary")):
        con.execute("INSERT INTO sources(state, source_id, url, kind, format, cadence, access, licence, notes) VALUES (?,?,?,?,?,?,?,?,?)",
                    (st, sid, f"https://example.org/{sid}", kind, "x", "daily", "open", "x", f"{sid}. Notes."))
    con.execute("INSERT INTO snapshots(snapshot_id, source_id, part, state, url, fetched_utc, valid) VALUES (1,'pa_mail_socrata','','pa','u','2026-10-31T10:00:00Z',1)")
    rows_ = [("pa", "2026-10-30", "mail_returned", "ALL", 1045149, "pa_mail_socrata"), ("pa", "2026-10-30", "mail_returned", "D", 675923, "pa_mail_socrata"),
             ("pa", "2026-10-30", "mail_returned", "R", 264586, "pa_mail_socrata"), ("pa", "2026-10-30", "mail_returned", "other", 104640, "pa_mail_socrata"),
             ("pa", "2026-10-29", "mail_returned", "ALL", 1010823, "pa_mail_socrata"),
             ("pa", "2026-10-30", "mail_returned", "ALL", 1041756, "pa_mail_daily_xlsx")]
    for r in rows_:
        con.execute("INSERT INTO ev_state(state, as_of_date, measure, party, age_band, count, source_id, snapshot_id) VALUES (?,?,?,?, 'ALL', ?,?,1)", r)
    for fips, n in (("42001", 600000), ("42003", 445149)):
        con.execute("INSERT INTO ev_county(state, county_fips, as_of_date, measure, party, age_band, count, source_id, snapshot_id) VALUES ('pa',?,'2026-10-30','mail_returned','ALL','ALL',?,'pa_mail_socrata',1)", (fips, n))
    con.commit()
    con.close()


def test_built_site_files_count_pa_once_and_label_uf_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUNDER_DATA", str(tmp_path))          # no past-cycle panels, no return-date files here
    p = tmp_path / "ev.sqlite"
    tiny_db(p)
    con = sqlite3.connect(p)                                   # the Lab's rows: a state with no official count, and PA as a cross-check
    con.execute("INSERT INTO fetch_log(ts, state, source_id, outcome) VALUES ('2026-10-31T12:00:00Z','us','uf_election_lab','new')")
    for st, d, m, party, n in (("nc", "2026-10-30", "total", "ALL", 2500000), ("nc", "2026-10-30", "total", "D", 1000000), ("nc", "2026-10-30", "total", "R", 700000),
                               ("pa", "2026-10-30", "total", "ALL", 1046000)):
        con.execute("INSERT INTO ev_state(state, as_of_date, measure, party, age_band, count, source_id, snapshot_id) VALUES (?,?,?,?, 'ALL', ?, 'uf_election_lab', 1)", (st, d, m, party, n))
    con.commit()
    con.close()
    res = site.build(p, tmp_path / "out", use_daily=False, log=lambda *_: None)
    out = tmp_path / "out"
    idx = json.loads((out / "index.json").read_text())
    assert len(idx["states"]) == 51
    pa = json.loads((out / "state_pa.json").read_text())
    assert pa["latest"]["total"] == 1045149                    # one source, not 1,045,149 + 1,041,756 (and not the Lab's 1,046,000)
    assert pa["series"]["total"] == [1010823, 1045149] and pa["series"]["added_total"][-1] == 1045149 - 1010823
    assert pa["latest"]["lead"] == pytest.approx(100 * (675923 - 264586) / (675923 + 264586 + 104640), abs=0.001)
    assert pa["uf_check"]["diff"] == 1046000 - 1045149 and pa["uf_check"]["official_total"] == 1045149
    assert [c["total"] for c in json.loads((out / "counties_pa.json").read_text())["counties"]] == [600000, 445149]
    nc = next(c for c in idx["states"] if c["st"] == "nc")      # no official row: compiled, labelled, never called collected
    assert nc["status"] == "compiled" and nc["total"] == 2500000 and nc["detail"] == site.UF_LABEL and nc["official"]["status"] == "not_published"
    assert nc["party"] is True and nc["lead"] == pytest.approx(100 * (1000000 - 700000) / 1700000, abs=0.001)
    assert json.loads((out / "state_nc.json").read_text())["compiled"] is True
    assert next(c for c in idx["states"] if c["st"] == "pa")["status"] == "collected"
    assert idx["tracker"]["licence"] == site.UF_LICENCE and idx["counts"]["compiled"] == 1
    assert res["audit"]["county_not_equal_state"] == []


def test_county_only_source_is_not_a_state_total(tmp_path, monkeypatch):
    """Clark County alone (EVGaps' nv_clark_ev) must show as 'county only' coverage, never as Nevada's total."""
    monkeypatch.setenv("BRUNDER_DATA", str(tmp_path))
    p = tmp_path / "ev.sqlite"
    tiny_db(p)
    con = sqlite3.connect(p)
    con.execute("INSERT INTO sources(state, source_id, url, kind, format, cadence, access, licence, notes) VALUES ('nv','nv_clark_ev','https://example.org/clark','county','x','daily','open','x','Clark County only.')")
    con.execute("INSERT INTO ev_county(state, county_fips, as_of_date, measure, party, age_band, count, source_id, snapshot_id) VALUES ('nv','32003','2026-10-30','total','ALL','ALL',350000,'nv_clark_ev',1)")
    con.commit()
    con.close()
    site.build(p, tmp_path / "out", use_daily=False, log=lambda *_: None)
    idx = json.loads((tmp_path / "out" / "index.json").read_text())
    nv = next(c for c in idx["states"] if c["st"] == "nv")
    assert nv["status"] == "collected" and nv["level"] == "county" and nv["partial"] is True and nv["total"] is None
    assert "not a state total" in nv["detail"]
    assert not (tmp_path / "out" / "state_nv.json").exists() and (tmp_path / "out" / "counties_nv.json").exists()
