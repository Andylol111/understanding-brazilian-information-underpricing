"""One series per state and measure out of the early-vote database (docs/us_ev_site.md).

The database keeps every source's rows side by side: the same ballots can sit there twice (Pennsylvania's daily xlsx and its
data.pa.gov extract, Hawaii's two reports, the pre-database legacy panels next to the live collectors). Adding them up is the
mistake behind "PA shows 2,086,905 returned ballots": that is 1,041,756 (DOS xlsx) + 1,045,149 (data.pa.gov), two counts of
the same 1.04 million. Nothing downstream may sum over `source_id`. `canonical()` picks, for every state x measure x date, ONE
source (explicit order below, then live collectors before legacy panels, then the source with more dates) and keeps all
of that source's parties for the date, so a party split always adds up to its own total.

`audit()` is the guard: for every source and date it checks that county rows add up to the state row and that no two sources
that cover the same date disagree by more than OVERLAP_TOL.
"""
from __future__ import annotations

import pandas as pd

LEGACY = "legacy_"
OVERLAP_TOL = 0.02          # two sources for one date may differ by this much (different extract hour), not more
# Same ballots, two publications: listed first = the series that is shown. Everything else follows the generic rule.
SOURCE_ORDER: dict[str, list[str]] = {
    "pa": ["pa_mail_socrata", "pa_mail_daily_xlsx"],       # the extract has the whole by-return-date history
    "hi": ["hi_absentee_recon", "hi_voting_report"],
    "md": ["md_mail_county", "md_mail_requests"],
}
DAILY_PREFIX = "daily_"     # pseudo sources rebuilt from return-date files (analysis/us_ev_daily.py): best resolution, first


def source_rank(rows: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(state, source_id) -> rank, smaller wins. Explicit order, then daily files, live collectors, legacy; more dates first."""
    ndates = rows[rows["party"] == "ALL"].groupby(["state", "source_id"])["as_of_date"].nunique()
    out: dict[tuple[str, str], float] = {}
    for (st, sid), n in ndates.items():
        order = SOURCE_ORDER.get(st, [])
        if sid in order:
            out[(st, sid)] = order.index(sid) + 100
        elif sid.startswith(DAILY_PREFIX):
            out[(st, sid)] = 0
        else:
            out[(st, sid)] = 1000 + (5000 if sid.startswith(LEGACY) else 0) - n
    return out


def with_all_party(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Rows of a source that gives parties but no ALL row get ALL = sum of parties (never both: ALL is not summed twice)."""
    have = rows[rows["party"] == "ALL"][keys].drop_duplicates()
    parts = rows[rows["party"] != "ALL"].groupby(keys, as_index=False)["count"].sum()
    miss = parts.merge(have, on=keys, how="left", indicator=True)
    miss = miss[miss["_merge"] == "left_only"].drop(columns="_merge").assign(party="ALL")
    return pd.concat([rows, miss], ignore_index=True) if len(miss) else rows


def drop_disagreeing(rows: pd.DataFrame, rank: dict[tuple[str, str], float]) -> pd.DataFrame:
    """A lower-ranked source that contradicts the top source on a date they share (more than OVERLAP_TOL) is a different
    definition or a stale panel: it is left out of that state x measure altogether instead of being spliced in."""
    a = rows[rows["party"] == "ALL"].groupby(["state", "measure", "as_of_date", "source_id"], as_index=False)["count"].sum()
    drop = set()
    for (st, m), x in a.groupby(["state", "measure"]):
        srcs = sorted(x["source_id"].unique(), key=lambda i: rank[(st, i)])
        if len(srcs) < 2:
            continue
        top = x[x["source_id"] == srcs[0]].set_index("as_of_date")["count"]
        for s in srcs[1:]:
            o = x[x["source_id"] == s].set_index("as_of_date")["count"]
            j = pd.concat([top, o], axis=1, join="inner", keys=["a", "b"])
            if len(j) and ((j["a"] - j["b"]).abs() / j[["a", "b"]].max(axis=1).clip(lower=1) > OVERLAP_TOL).any():
                drop.add((st, m, s))
    if not drop:
        return rows
    return rows[[(s, m, i) not in drop for s, m, i in zip(rows["state"], rows["measure"], rows["source_id"])]]


def prepare(rows: pd.DataFrame, level_keys: tuple[str, ...] = ("state",)) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """-> (rows with ALL completed, rows kept after dropping contradicting sources, rank). See drop_disagreeing."""
    rows = with_all_party(rows, list(level_keys) + ["measure", "as_of_date", "source_id"])
    rank = source_rank(rows)
    return rows, drop_disagreeing(rows, rank), rank


def dropped_sources(rows: pd.DataFrame, kept: pd.DataFrame) -> list[dict]:
    a = rows.groupby(["state", "measure", "source_id"]).size().index
    b = set(kept.groupby(["state", "measure", "source_id"]).size().index)
    return [{"state": s, "measure": m, "source": i} for s, m, i in a if (s, m, i) not in b]


def canonical(rows: pd.DataFrame, level_keys: tuple[str, ...] = ("state",)) -> pd.DataFrame:
    """rows: state[, county_fips], as_of_date, measure, party, count, source_id (latest value per key, age ALL).
    -> the same columns, one source per (level, measure, date)."""
    unit = list(level_keys)
    _, rows, rank = prepare(rows, level_keys)
    rows = rows.assign(_rank=[rank[(s, i)] for s, i in zip(rows["state"], rows["source_id"])])
    allrows = rows[rows["party"] == "ALL"]
    best = (allrows.sort_values("_rank", kind="stable").groupby(unit + ["measure", "as_of_date"], sort=False).head(1)
            [unit + ["measure", "as_of_date", "source_id"]])
    return rows.merge(best, on=unit + ["measure", "as_of_date", "source_id"]).drop(columns="_rank")


def overlap(rows: pd.DataFrame) -> list[dict]:
    """State rows where two sources cover the same (measure, date) with party ALL and differ by more than OVERLAP_TOL."""
    a = rows[rows["party"] == "ALL"]
    g = a.groupby(["state", "measure", "as_of_date"])
    out = []
    for (st, m, d), x in g:
        if x["source_id"].nunique() < 2:
            continue
        hi, lo = x["count"].max(), x["count"].min()
        if hi > 0 and (hi - lo) / hi > OVERLAP_TOL:
            out.append({"state": st, "measure": m, "date": d, "sources": dict(zip(x["source_id"], x["count"]))})
    return out


def county_vs_state(county: pd.DataFrame, state: pd.DataFrame, tol: float = 0.0) -> list[dict]:
    """Per source: summing the county rows of one (date, measure, party) must give the state row (tol = relative slack)."""
    c = county.groupby(["state", "source_id", "as_of_date", "measure", "party"], as_index=False)["count"].sum()
    m = c.merge(state, on=["state", "source_id", "as_of_date", "measure", "party"], suffixes=("_county", "_state"))
    bad = m[(m["count_county"] - m["count_state"]).abs() > tol * m["count_state"].clip(lower=1)]
    return bad.rename(columns={"count_county": "county_sum", "count_state": "state_total"}).to_dict("records")


def audit(state: pd.DataFrame, county: pd.DataFrame, canon_state: pd.DataFrame) -> dict:
    """Everything that would silently inflate or contradict a total. Empty lists mean clean. `state`/`county` are the raw
    rows of the sources that were kept (county rows only when the same source also has a state row)."""
    canon_sum = canon_state[canon_state["party"] == "ALL"].groupby(["state", "measure", "as_of_date"])["source_id"].nunique()
    return {"county_not_equal_state": county_vs_state(county, state),
            "sources_disagree": overlap(state),
            "canonical_has_two_sources": [k for k, n in canon_sum.items() if n > 1]}
