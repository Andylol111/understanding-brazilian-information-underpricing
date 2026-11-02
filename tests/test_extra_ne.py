"""Parsers of the northeast/mid-Atlantic secondary sources (brunder.extra_va, extra_md) on past-cycle files:
VA 2024 general 1st/5th District items (Enhanced Voting API), MD 2024 general District 1 county table
House document (the registered fixture, used for the 11th-district parser). No network."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from brunder import extra_md, extra_va, us

FX = Path(__file__).parent / "fixtures" / "us"
PRI = Path(__file__).parents[1] / "data_public"


def _gz(name: str) -> bytes:
    return gzip.open(FX / name).read()


def _sum(df, party):
    return float(df.loc[df["party"] == party, "votes"].sum())


@pytest.mark.parametrize("n,dem,rep,units", [(1, 208445, 269657, 18), (5, 184229, 249564, 24)])
def test_va_2024_matches_certified_item_totals(n, dem, rep, units):
    df = extra_va.parse("va_house", {f"d0{n}": _gz(f"extra_va_2024_cd0{n}.json.gz")})
    assert list(df.columns) == us.COLS
    assert set(df["race"]) == {f"house-0{n}"}
    assert _sum(df, "DEM") == dem and _sum(df, "REP") == rep      # statewide summaryResults of the item
    assert df["unit_id"].nunique() == units and df["unit_id"].str.fullmatch(r"us-va-51\d{3}").all()
    one = df[df["unit_id"] == df["unit_id"].iloc[0]]
    assert (one["reporting"] == one["units"]).all() and one["units"].iloc[0] > 0
    assert 0.3 < df["early"].sum() / df["votes"].sum() < 0.7       # Early Voting + Mailed Absentee


def test_va_precount_and_garbage_are_empty():
    assert extra_va.parse("va_house", {"d01": _gz("extra_va_2026_cd01_precount.json.gz")}).empty
    assert extra_va.parse("va_house", {"d01": b"not json", "d05": None}).empty
    assert extra_va.parse("va_house", {}).empty


def test_va_non_house_item_is_ignored():
    doc = json.loads(_gz("extra_va_2024_cd01.json.gz"))
    doc["ballotItemWithBreakdown"]["name"][0]["text"] = "Member, United States Senate"
    assert extra_va.parse("va_house", {"x": json.dumps(doc).encode()}).empty


def test_va_record_urls_and_feeds():
    assert all(r["url"].startswith("https://enr.elections.virginia.gov/") and r["interval"] >= 30
               and re.fullmatch(r"[A-Za-z0-9._-]+", r["key"]) for r in extra_va.RECORD)
    assert {v for roles in extra_va.FEEDS.values() for v in roles.values()} == {r["key"] for r in extra_va.RECORD}


def test_md_2024_county_table_matches_certified():
    df = extra_md.parse("md_house", {"d01": _gz("extra_md_2024_cd01_detail.html.gz")})
    assert list(df.columns) == us.COLS and set(df["race"]) == {"house-01"}
    assert _sum(df, "DEM") == 154985 and _sum(df, "REP") == 246356     # official district totals (summary page)
    assert df["unit_id"].nunique() == 11 and "us-md-24025" in set(df["unit_id"])      # Harford
    assert df["unit_id"].str.fullmatch(r"us-md-24\d{3}").all()


def test_md_summary_page_has_no_county_table_and_precount_is_empty():
    assert extra_md.parse("md_house", {"d01": _gz("extra_md_2024_cd01_summary.html.gz")}).empty
    assert extra_md.parse("md_house", {"d01": _gz("extra_md_2026_cd01_precount.html.gz")}).empty
    assert extra_md.parse("md_house", {"d01": b"<html><body>404</body></html>", "d02": None}).empty


def test_md_2026_template_partial_count():
    """The 2026 page layout (thead/tbody, NR = not reported) with two counties counted."""
    html = _gz("extra_md_2026_cd01_precount.html.gz").decode()
    html = re.sub(r"<td>Caroline</td>(<td class=\"alignRight\">NR</td>){4}",
                  '<td>Caroline</td><td class="alignRight">1,000</td><td class="alignRight">3,000</td>'
                  '<td class="alignRight">0</td><td class="alignRight">5</td>', html, count=1)
    df = extra_md.parse("md_house", {"d01": html.encode()})
    assert df["unit_id"].unique().tolist() == ["us-md-24011"]
    assert _sum(df, "DEM") == 1000 and _sum(df, "REP") == 3000 and len(df) == 4


@pytest.mark.parametrize("mod,src,units", [("va", "va-01_pres_2024", 18), ("va", "va-05_pres_2024", 24),
                                           ("md", "md-01_pres_2024", 11)])
def test_priors_cover_the_feed_units(mod, src, units):
    p = pd.read_csv(PRI / f"us2026_extra_priors_{mod}.csv", comment="#")
    x = p[p["source"] == src]
    assert len(x) == units and (x["dem"] + x["rep"] <= x["total"] + 1).all() and x["unit_id"].is_unique


@pytest.mark.parametrize("mod,src,fixture,parse", [
    ("va", "va-01_pres_2024", "extra_va_2024_cd01.json.gz", lambda b: extra_va.parse("va_house", {"d": b})),
    ("va", "va-05_pres_2024", "extra_va_2024_cd05.json.gz", lambda b: extra_va.parse("va_house", {"d": b})),
    ("md", "md-01_pres_2024", "extra_md_2024_cd01_detail.html.gz", lambda b: extra_md.parse("md_house", {"d": b}))])
def test_prior_units_equal_feed_units(mod, src, fixture, parse):
    p = pd.read_csv(PRI / f"us2026_extra_priors_{mod}.csv", comment="#")
    assert set(p.loc[p["source"] == src, "unit_id"]) == set(parse(_gz(fixture))["unit_id"])


@pytest.mark.parametrize("src,d,r", [("va-01_pres_2024", 226773, 250556), ("va-05_pres_2024", 191727, 246047),
                                     ("md-01_pres_2024", 171556, 243356)])
def test_prior_district_sums_match_districts_json(src, d, r):
    dist = f"{src[:2].upper()}-{src[3:5]}"
    ref = json.loads((Path(__file__).parents[1] / "site/data/us2026/house/districts.json").read_text())
    ref = ref["districts"][dist]["pres24"]
    p = pd.concat([pd.read_csv(f, comment="#") for f in PRI.glob("us2026_extra_priors_*.csv")])
    x = p[p["source"] == src]
    assert abs(x["dem"].sum() - ref["dv"]) < 2 and abs(x["rep"].sum() - ref["rv"]) < 2
