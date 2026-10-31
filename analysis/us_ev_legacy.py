"""Ingest what already existed on the SSD before the early-vote DB (read-only; docs/us_ev_db.md).

  * $BRUNDER_DATA/tmp/ev_src/*_panel.csv  -> ev_state (cumulative D/R/other by date and kind, 2016-2026, from the
    collectors behind docs/us_ev_gap.md); each panel file is registered as a snapshot (sha256, not copied).
  * $BRUNDER_DATA/raw/us/ev_party/<state>/ and raw/us/earlyvote/  -> every small file registered as a snapshot of
    `legacy_raw_<state>` (sha256 in the manifest; huge voter-level files such as NC VR snapshots are skipped).
  * County-level 2026 files among them (NC absentee_counts_county, FL Stats_*_VbmVoted/EarlyVoted) are parsed with the
    same parsers as the live collectors into the same source ids, so the series continue seamlessly.
Idempotent: re-running adds nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

import us_ev_db as db

PANEL_STATE_MEASURE = {"mail": "mail_returned", "inperson": "inperson_early", "total": "total"}
RAW_MAX_BYTES = 60_000_000
SKIP_SUFFIX = (".missing", ".404", ".pyc", ".lastmodified")


def _panel_rows(df: pd.DataFrame, state: str) -> list[dict]:
    rows = []
    for r in df.itertuples(index=False):
        if r.D + r.R + r.O == 0:
            continue                                             # before the series starts (zero padding)
        m = "mail_accepted" if (state == "nc" and r.kind == "mail") else PANEL_STATE_MEASURE[r.kind]
        for p, v in (("D", r.D), ("R", r.R), ("other", r.O), ("ALL", r.D + r.R + r.O)):
            rows.append({"state": state, "as_of_date": r.date, "measure": m, "party": p, "count": int(v)})
    return rows


def ingest_panels(st: db.Store) -> int:
    n = 0
    for f in sorted((db.data_root() / "tmp" / "ev_src").glob("*_panel.csv")):
        mod = f.name.removesuffix("_panel.csv")
        d = pd.read_csv(f)
        for state, g in d.groupby("state"):
            sid = f"legacy_{mod}_{state}"
            notes = "; ".join(sorted({f"{k}: {str(v)[:120]}" for k, v in g.groupby("kind")["note"].first().items()}))
            db.register(db.Source(state, sid, str(f), "state", "csv", "static (ingested, read-only)",
                                  "existing SSD file", "official state publications compiled by the EV-gap collectors",
                                  f"cumulative D/R/other by date, cycles {sorted(set(g['year']))}; O = everything not D/R "
                                  f"(includes unaffiliated). {notes}"[:900], None))
            st.sync_sources()
            snap = st.register_existing(sid, f, state=state, url=str(f))
            n += st.add_rows(_panel_rows(g, state), source_id=sid, snapshot_id=snap, level="state")
    return n


def ingest_raw_registry(st: db.Store) -> int:
    roots = [db.data_root() / "raw" / "us" / "ev_party", db.data_root() / "raw" / "us" / "earlyvote"]
    n = 0
    for root in roots:
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            if f.name.endswith(SKIP_SUFFIX) or f.stat().st_size > RAW_MAX_BYTES or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(root)
            state = rel.parts[0] if root.name == "ev_party" and len(rel.parts) > 1 else (
                f.name.split("_")[0] if root.name == "earlyvote" else "us")
            if len(state) != 2:
                state = "us"
            sid = f"legacy_raw_{state}"
            if sid not in db.SOURCES:
                db.register(db.Source(state, sid, str(root), "raw archive", "various", "static (ingested, read-only)",
                                      "existing SSD files", "official state publications", "files collected 2026-10-28..30 "
                                      "before the EV database; registered with sha256, not copied", None))
            had = st.db.execute("SELECT 1 FROM snapshots WHERE source_id=? AND file=?",
                                (sid, str(f.relative_to(db.data_root())))).fetchone()
            if st.register_existing(sid, f, state=state, url="") is not None and not had:
                n += 1
    st.sync_sources()
    st.db.commit()
    return n


def ingest_county_files(st: db.Store) -> int:
    """NC absentee_counts_county and FL Stats files already on the SSD -> the live collectors' source ids."""
    n = 0
    root = db.data_root() / "raw" / "us"
    for f, stamp in ((root / "earlyvote" / "nc_counts_20261028084130.csv", "2026-10-28"),
                     (root / "ev_party" / "nc" / "absentee_counts_county_20261103.csv", "2026-10-30")):
        if not f.exists():
            continue
        as_of = (pd.Timestamp(stamp) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        snap = st.register_existing("nc_abs_counts", f, state="nc")
        crow, srow = db.parse_nc_counts(f.read_bytes(), as_of)
        n += st.add_rows(crow, source_id="nc_abs_counts", snapshot_id=snap, level="county", mark_parsed=False)
        n += st.add_rows(srow, source_id="nc_abs_counts", snapshot_id=snap, level="state")
    fl = root / "ev_party" / "fl"
    got = {}
    for key, pat in (("vbm_voted", "Stats_49894_VbmVoted_*.txt"), ("early_voted", "Stats_49894_EarlyVoted_*.txt")):
        for f in sorted(fl.glob(pat)):
            got[key] = (f, db.parse_fl_stats(f.read_bytes()))
    if len(got) == 2:
        crow, srow = db.fl_rows({k: v[1] for k, v in got.items()})
        snaps = [st.register_existing("fl_county_stats", v[0], state="fl", part=k) for k, v in got.items()]
        n += st.add_rows(crow, source_id="fl_county_stats", snapshot_id=max(snaps), level="county", mark_parsed=False)
        n += st.add_rows(srow, source_id="fl_county_stats", snapshot_id=max(snaps), level="state")
    return n


def ingest_legacy(st: db.Store) -> str:
    db.load_extensions()
    st.sync_sources()
    a = ingest_panels(st)
    b = ingest_county_files(st)
    c = ingest_raw_registry(st)
    return f"legacy ingest: {a} state rows from panels, {b} rows from county files, {c} raw files registered"
