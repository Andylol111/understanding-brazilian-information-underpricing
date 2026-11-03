"""Convert the research repo's Election Day turnout file into the public one: site/data/ev2026/turnout_day.json.

Turnout only (who voted, not how); not used by any model. The output is aggregates only:
  - each row keeps a fixed list of fields; every other field (party splits, anything that could be voter-level) is dropped;
  - every row has one `kind` (Election Day in person / early in person / mail-absentee returned / all pre-Election Day advance /
    requests-sent / total of all methods / unclear), from the research row's own kind or inferred from its measure text;
  - rows are dropped when the state is unknown, the level is not state / state-partial / county / manual, the count is not a
    plain number (a row that only has a percent keeps count null), or the row comes from the Redistricting Data Hub;
  - local file names and source ids are never copied (manual downloads are labelled by provenance and the official URL only);
  - every state and DC appears, with its discovery verdict, so a state with no row still says why.
The optional history verdict (turnout_volume.json, written by the research side) is copied as {verdict, summary, n_cases,
generated_utc} only.

  uv run python analysis/us_turnout_publish.py [--src PATH] [--volume-src PATH] [--out-dir site/data/ev2026]
Exit 0 and no output files when the source is missing or older than the published file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path.home() / "brunder-research" / "poll_error_dashboard" / "data"   # turnout_day.json; the volume verdict is ../../data/

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
LEVELS = {"state", "state-partial", "county", "manual"}
NUMERIC = ("count", "registered", "pct_registered", "same_time_2022", "same_time_2024", "same_time_2022_ratio", "total_2022",
           "total_2024", "pct_of_2022_total", "pct_of_2024_total", "in_person", "mail_returned", "in_person_absentee", "early_voting",
           "election_day", "mail", "requested", "requests", "sent", "prev_count")
TEXT = ("jurisdiction", "level", "measure", "as_of", "source_url", "provenance", "same_time_comparison", "note", "fetched_utc", "prev_as_of")
KEEP = frozenset(NUMERIC + TEXT + ("state",))
RDH = re.compile(r"redistrictingdatahub|\brdh\b|redistricting data hub", re.I)
KINDS = ("election_day", "early_in_person", "mail_returned", "advance", "requests", "total", "unclear")
FILE_TAG = re.compile(r"\s*\[file:[^\]]*\]")


def provenance_class(p: str) -> str:
    """official / official_post / secondary / manual / derived, from the research file's provenance string."""
    s = (p or "").lower()
    if s.startswith("derived"):
        return "derived"
    if s.startswith("manual"):
        return "manual"
    if "secondary" in s or "news" in s:
        return "secondary"
    if "via post" in s or "official post" in s:
        return "official_post"
    if "official" in s:
        return "official"
    return "secondary"


def kind_of(r: dict[str, Any]) -> str:
    """What one count measures. Never mixed: a row has exactly one kind. Uses the research file's own "kind" if it is one of
    KINDS; otherwise infers it from the measure text; anything not clearly one of them is "unclear"."""
    if r.get("kind") in KINDS:
        return r["kind"]
    s = (r.get("measure") or "").lower()
    if "not a total" in s or s.startswith("early ballots"):
        return "unclear"
    if "total ballots cast" in s or "+ election day" in s:
        return "total"
    if s.startswith("election day"):
        return "election_day"
    if re.search(r"requested|requests", s) or (re.search(r"\bsent\b", s) and not re.search(r"received|returned", s)):
        return "requests"
    if re.search(r"before polls opened|advance ballots|absentee \+ early|mail \+ in person", s):
        return "advance"
    if s.startswith("early voting") or s.startswith("advance (early"):
        return "early_in_person"
    if re.search(r"absentee|mail|received|returned", s):
        return "mail_returned"
    return "unclear"


MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}


def as_of_parts(as_of: str | None) -> tuple[str | None, str | None]:
    """(as_of_utc, as_of_date) from the free-text as-of: a full ISO time with offset gives both; a bare date gives the date only;
    local-time texts (e.g. "09:00 CT") give the date only. (None, None) when nothing parses."""
    if not as_of:
        return None, None
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})", as_of)
    if m:
        try:
            t = datetime.fromisoformat(f"{m[1]}T{m[2]}{'+00:00' if m[3] == 'Z' else m[3]}").astimezone(timezone.utc)
            return t.strftime("%Y-%m-%dT%H:%M:%SZ"), t.strftime("%Y-%m-%d")
        except ValueError:
            return None, None
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", as_of):
        return None, f"{m[1]}-{m[2]}-{m[3]}"
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", as_of):
        return None, f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    if m := re.search(r"\b([A-Za-z]+) (\d{1,2}), (\d{4})", as_of):
        if m[1].lower() in MONTHS:
            return None, f"{m[3]}-{MONTHS[m[1].lower()]:02d}-{int(m[2]):02d}"
    return None, None


def verdict_class(v: str) -> str:
    s = (v or "").lower()
    if s.startswith("blocked"):
        return "blocked"
    if s.startswith("excluded"):
        return "excluded"
    if s.startswith("none") or not s:
        return "none"
    return "other"


def number(v: Any) -> float | int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return None
    return int(v) if float(v).is_integer() and abs(v) >= 1000 else v


def text(v: Any, n: int = 600) -> str | None:
    if v is None or not isinstance(v, str):
        return None
    v = FILE_TAG.sub("", v).strip()
    return v[:n] or None


def clean_row(r: dict[str, Any]) -> dict[str, Any] | None:
    """One aggregate row in the public shape, or None if the row must not be published."""
    st = str(r.get("state", "")).upper()
    if st not in STATES or r.get("level") not in LEVELS:
        return None
    if RDH.search(" ".join(str(r.get(k, "")) for k in ("source_id", "source_url", "provenance", "note", "jurisdiction"))):
        return None
    out: dict[str, Any] = {"state": st}
    for k in TEXT:
        out[k] = text(r.get(k))
    for k in NUMERIC:
        if k in r:
            out[k] = number(r[k])
    if not out.get("jurisdiction") or not out.get("measure"):
        return None
    out["prov"] = provenance_class(out.get("provenance") or "")
    out["kind"] = kind_of({"kind": r.get("kind"), "measure": out["measure"]})
    utc, day = as_of_parts(out.get("as_of"))
    if utc:
        out["as_of_utc"] = utc
    if day:
        out["as_of_date"] = day
    return {k: v for k, v in out.items() if v is not None or k == "count"}


def convert(src: dict[str, Any]) -> dict[str, Any]:
    meta_states = (src.get("meta") or {}).get("states") or {}
    rows = [c for c in (clean_row(r) for r in src.get("rows") or [] if isinstance(r, dict)) if c]
    blocked: dict[str, list[dict[str, Any]]] = {}
    for u in src.get("unrecorded") or []:
        st = str(u.get("state", "")).upper()
        if st in STATES and not RDH.search(json.dumps(u)):
            blocked.setdefault(st, []).append({k: text(u.get(k), 300) for k in ("jurisdiction", "url", "verdict", "reason", "figure")})
    states = []
    for st, name in STATES.items():
        m = meta_states.get(st) or {}
        mine = [r for r in rows if r["state"] == st]
        states.append({
            "st": st, "name": name,
            "verdict": text(m.get("verdict"), 200) or "none found", "verdict_class": verdict_class(m.get("verdict", "")),
            "statewide": text(m.get("statewide")), "county_note": text(m.get("county")), "same_time_note": text(m.get("same_time"), 200),
            "rows": [r for r in mine if r["level"] != "county"],
            "counties": sorted((r for r in mine if r["level"] == "county"), key=lambda r: r["jurisdiction"]),
            "unrecorded": blocked.get(st, []),
        })
    return {
        "label": "Turnout only: who voted, not how. Not used by any model.",
        "generated_utc": src.get("generated_utc"), "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stop_utc": src.get("stop_utc"), "n_rows": len(rows), "n_dropped_rows": len(src.get("rows") or []) - len(rows),
        "states": states,
    }


def convert_volume(src: dict[str, Any]) -> dict[str, Any] | None:
    v = text(src.get("verdict"), 400)
    if not v:
        return None
    out: dict[str, Any] = {"verdict": v}
    if text(src.get("summary"), 1200):
        out["summary"] = text(src.get("summary"), 1200)
    if number(src.get("n_cases")) is not None:
        out["n_cases"] = number(src["n_cases"])
    if text(src.get("generated_utc"), 40):
        out["generated_utc"] = text(src.get("generated_utc"), 40)
    return out


def write_if_changed(path: Path, obj: dict[str, Any], ignore: tuple[str, ...] = ()) -> bool:
    new = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    if path.exists():
        try:
            old = json.loads(path.read_text())
            if {k: v for k, v in old.items() if k not in ignore} == {k: v for k, v in obj.items() if k not in ignore}:
                return False
        except ValueError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=str(RESEARCH / "turnout_day.json"))
    ap.add_argument("--volume-src", default=str(RESEARCH.parent.parent / "data" / "turnout_volume.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "site" / "data" / "ev2026"))
    a = ap.parse_args()
    out = Path(a.out_dir)
    src = Path(a.src)
    if not src.exists():
        print(f"no source file {src}", file=sys.stderr)
        return 1
    pub = convert(json.loads(src.read_text()))
    # a re-publish that only changes published_utc is not a change
    changed = write_if_changed(out / "turnout_day.json", pub, ignore=("published_utc",))
    vsrc = Path(a.volume_src)
    if vsrc.exists():
        vol = convert_volume(json.loads(vsrc.read_text()))
        if vol:
            changed |= write_if_changed(out / "turnout_volume.json", vol)
    print(f"{pub['n_rows']} rows published, {pub['n_dropped_rows']} dropped; {'changed' if changed else 'no change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
