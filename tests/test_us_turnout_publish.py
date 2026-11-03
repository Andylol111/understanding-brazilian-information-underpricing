"""analysis/us_turnout_publish.py: aggregates only, fixed fields, drop rules, one kind per row, every state present."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import us_turnout_publish as tp  # noqa: E402


def row(**kw):
    base = {"source_id": "x", "state": "FL", "jurisdiction": "St. Lucie County", "level": "county", "measure": "Election Day check-ins so far (in person)",
            "count": 16167, "as_of": "2026-11-03T17:00:07.18+00:00", "source_url": "https://example.gov/a.json",
            "provenance": "official file (machine-recorded)"}
    base.update(kw)
    return base


SRC = {
    "generated_utc": "2026-11-03T17:09:47Z", "stop_utc": "2026-11-04T03:00:00Z",
    "meta": {"states": {"FL": {"verdict": "recordable (ED live)", "statewide": "forbidden host", "county": "66 counties"},
                        "AK": {"verdict": "excluded (project rule)"}, "AL": {"verdict": "none"}}},
    "unrecorded": [{"state": "AZ", "jurisdiction": "Maricopa County", "url": "https://m.gov", "verdict": "blocked", "reason": "403", "figure": "check-ins"}],
    "rows": [
        row(dem=100, rep=90, unaffiliated=10, voter_id="V1", name="A. Voter", address="1 Main St"),            # kept row; extra fields dropped
        row(state="TX", jurisdiction="Harris County", source_url="https://redistrictingdatahub.org/x.csv"),    # RDH: dropped
        row(state="ZZ"),                                                                                         # unknown state: dropped
        row(level="precinct"),                                                                                   # not an aggregate level: dropped
        row(state="WI", level="manual", jurisdiction="Wisconsin (statewide)", measure="Absentee ballots returned (incl. in-person absentee), cumulative",
            count="796607", as_of="2026-10-29 (as-of date)", provenance="manual (downloaded by user in browser)", note="statewide row [file: Abs_2026.csv]"),
        row(state="MI", level="manual", jurisdiction="Michigan", measure="ballots cast before polls opened (AB returned + early in-person)",
            count=2400000.0, provenance="secondary (news)", as_of="2026-11-03 07:00 ET"),
    ],
}


def test_publish_rules():
    out = tp.convert(SRC)
    assert out["n_rows"] == 3 and out["n_dropped_rows"] == 3
    assert len(out["states"]) == 51 and {s["st"] for s in out["states"]} >= {"DC", "AK", "AL", "FL"}
    by = {s["st"]: s for s in out["states"]}
    # a state with no row still says why; blocked sources are carried; verdict classes are fixed
    assert by["AK"]["rows"] == [] and by["AK"]["verdict_class"] == "excluded" and by["AL"]["verdict_class"] == "none"
    assert by["AZ"]["unrecorded"][0]["verdict"] == "blocked"
    # county rows split from state rows, and only whitelisted fields survive (no party split, no voter-level keys)
    fl = by["FL"]["counties"][0]
    assert not set(fl) - tp.KEEP - {"prov", "kind", "as_of_utc", "as_of_date"}
    assert fl["count"] == 16167 and fl["kind"] == "election_day" and fl["prov"] == "official"
    assert fl["as_of_utc"] == "2026-11-03T17:00:07Z"
    assert "RDH" not in str(out) and "redistrictingdatahub" not in str(out) and "voter_id" not in str(out) and "Main St" not in str(out)
    # text counts are not numbers; local file names never leave; date-only and local-time as-of keep the date only; one kind per row
    wi = by["WI"]["rows"][0]
    assert wi["count"] is None and "[file" not in wi["note"] and wi["prov"] == "manual"
    assert wi["kind"] == "mail_returned" and wi["as_of_date"] == "2026-10-29" and "as_of_utc" not in wi
    mi = by["MI"]["rows"][0]
    assert mi["kind"] == "advance" and mi["prov"] == "secondary" and mi["as_of_date"] == "2026-11-03"
    assert tp.kind_of({"measure": "something else"}) == "unclear" and tp.kind_of({"kind": "requests", "measure": "x"}) == "requests"
    assert tp.kind_of({"measure": "Total ballots cast so far (early + mail + Election Day)"}) == "total"


def test_volume_copy_keeps_only_verdict_fields():
    v = tp.convert_volume({"verdict": "Null on size.", "summary": "165 races.", "n_cases": 165, "corr": 0.25, "per_race": [1, 2]})
    assert v == {"verdict": "Null on size.", "summary": "165 races.", "n_cases": 165}
    assert tp.convert_volume({"summary": "no verdict"}) is None
