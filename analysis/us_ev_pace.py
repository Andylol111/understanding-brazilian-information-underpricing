"""Pace of this year's early vote against the same day before Election Day in 2022 (docs/us_ev_site.md, "Pace vs 2022").

Pure functions, no I/O except the two readers at the bottom. For one state:
  now   this year's ballots so far (official if collected, else the UF Election Lab's compiled figure) and its days before Election Day
  then  the 2022 figure nearest the same number of days before Election Day: official past series where the state has one,
        else a page of the 2022 UF / US Elections Project tracker as archived by the Internet Archive
  pace  now / then;  pct22  now as a share of the certified 2022 total votes (largest statewide office total)
A 2022 figure more than MAX_GAP days from the aligned day is not used; one more than 1 day off is used but flagged.
"""
from __future__ import annotations

import pandas as pd

MAX_GAP = 3            # days between the aligned day and the 2022 figure used; beyond this there is no pace
EXACT_GAP = 1          # up to this many days off is not flagged
BOTH = "mail returned + in-person early"
SRC_NOW = {"official": "official", "compiled": "compiled (UF)"}
SRC_THEN = {"official": "official", "uf2022": "archived UF 2022"}


def nearest(days: list[int], d0: int, max_gap: int = MAX_GAP) -> int | None:
    """Index of the 2022 point whose days-before is nearest d0; on a tie the earlier day (larger days-before), so a 2022 count is never
    taken from after the aligned day when an earlier one is as close. None if the nearest is beyond max_gap."""
    if not len(days):
        return None
    i = min(range(len(days)), key=lambda k: (abs(days[k] - d0), -days[k]))
    return i if abs(days[i] - d0) <= max_gap else None


def build(now: dict, then: dict | None, cert22: float | None, why: str = "") -> dict:
    """now: {value, date, db, source: official|compiled, measure: text}; then: {value, date, db, source: official|uf2022, measure: text,
    definition_ok: bool, capture?: str, page_as_of?: str}. Returns the entry the page shows."""
    d0 = now["db"]
    e = {"now": {**now, "source_label": SRC_NOW[now["source"]]}, "then": None, "pace": None, "gap": None, "pct22": None, "cert22": cert22,
         "flags": [], "why": why}
    if cert22 and now["value"]:
        e["pct22"] = 100 * now["value"] / cert22
    if then is None:
        return e
    gap = then["db"] - d0
    e["then"] = {**then, "source_label": SRC_THEN[then["source"]]}
    e["gap"] = gap
    if abs(gap) > EXACT_GAP:
        e["flags"].append("gap")
    if not then.get("definition_ok", True):
        e["flags"].append("definition")
    if then["value"] and then["value"] > 0 and now["value"] is not None:
        e["pace"] = now["value"] / then["value"]
    return e


def official_then(hs: pd.DataFrame, kind: str, d0: int) -> tuple[dict | None, str]:
    """Nearest official 2022 point of one state's past panel (rows: year, kind, is_final, days_before, asof_date, ev_total)."""
    h = hs[(hs["year"] == 2022) & (hs["kind"] == kind) & (~hs["is_final"])]
    if h.empty:
        return None, "no day-by-day count for 2022 in the official files" if not hs.empty else "this state published no past day-by-day series"
    h = h.sort_values("days_before")
    days = [int(x) for x in h["days_before"]]
    i = nearest(days, d0)
    if i is None:
        return None, f"the official 2022 series covers {min(days)} to {max(days)} days before Election Day, not {d0}"
    r = h.iloc[i]
    return {"value": float(r["ev_total"]), "date": str(pd.Timestamp(r["asof_date"]).date()), "db": days[i], "source": "official",
            "measure": "ballots in the official past file", "definition_ok": True}, ""


def uf_then(rows: pd.DataFrame, d0: int, now_has_both: bool, now_has_mail_only: bool) -> tuple[dict | None, str]:
    """Nearest archived UF 2022 page (columns: capture, as_of, days_before, total, definition). definition_ok: the page's total is
    built the way this year's is (mail + in-person where this year has both; mail only where this year has only mail)."""
    r0 = rows[rows["total"].notna()]
    if r0.empty:
        return None, "no archived page of the 2022 UF tracker for this state in the weeks before Election Day"
    days = [int(x) for x in r0["days_before"]]
    i = nearest(days, d0)
    if i is None:
        return None, (f"the archived 2022 UF pages for this state are {min(days)} to {max(days)} days before Election Day, "
                      f"not {d0}")
    r = r0.iloc[i]
    d = str(r["definition"])
    ok = (d == BOTH and now_has_both) or (d.startswith("mail returned only") and now_has_mail_only) or (d.startswith("mail returned only") and not now_has_both)
    return {"value": float(r["total"]), "date": str(r["as_of"]), "db": days[i], "source": "uf2022", "measure": d, "definition_ok": bool(ok),
            "capture": str(r["capture"]), "page_as_of": str(r["as_of"])}, ""


def certified_2022(results_state: pd.DataFrame) -> dict[str, float]:
    """Certified 2022 total votes per state: the largest office total (governor, senate, or all US House votes)."""
    d = results_state[results_state["year"] == 2022]
    return {k: float(v) for k, v in d.groupby("st")["total_all"].max().dropna().items()}


def read_certified(root) -> dict[str, float]:
    f = root / "processed" / "us2026" / "unified" / "results_state.csv"
    return certified_2022(pd.read_csv(f)) if f.exists() else {}


def read_uf2022(root) -> pd.DataFrame:
    f = root / "processed" / "us2026" / "uf_2022_wayback.csv"
    cols = ["st", "capture", "capture_days_before", "as_of", "days_before", "total", "inperson", "mail", "definition", "source", "url"]
    return pd.read_csv(f, dtype={"capture": str}) if f.exists() and f.stat().st_size else pd.DataFrame(columns=cols)
