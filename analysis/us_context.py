"""Electorate context for the midterms page (site/midterms.html): who is in the early vote and on the rolls now vs 2018 / 2020 /
2022 / 2024, registration over time by party, what voters say matters, poll results by group.

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_context.py     # -> site/data/us2026_context/*.json

One harmonised taxonomy (race, age, gender, party, density) covers every state, source and year (us_ctx_tax); a source's categories are
mapped into it only where they union exactly into a group, everything else keeps the source's own dim id (age_co, age_uf, age_ncreg,
race_la). The mapping table is written into ev_groups.json.

Modules: us_ctx_states (state list + market tiers), us_ctx_tax (taxonomy, mapping, cells), us_ctx_nc (NC per-voter / voter_stats counts, cached under /tmp/us_ctx_cache),
us_ctx_ev (state entries: NC, CO, SC, LA, UF states), us_ctx_reg (NC registration mix, registration series).

Inputs
  NC per-voter absentee files 2018 / 2020 / 2022 / 2024 / 2026 + VR_Snapshot_20181106 (2018 ethnicity), voter_stats_<date>.zip (SSD, official)
  $BRUNDER_DATA/raw/us/ev_groups/ co_<date>.xlsx, sc_<date>.xlsx, sc_2022-GE-final.xlsx, uf_<date>.csv (aggregates copied from the server
    collector's raw/ directory: rsync -a --rsync-path="sudo -n rsync" oracle-slotbank:/srv/brunder/evdata/us_ev/raw/{co,sc,us} ...),
    raw/us/ev_party/co/att (CO daily workbooks 2020-2024, 2018 PDF text), raw/us/ev_party/la (LA early-vote and registration PDFs)
  data_public/us2026_context/reg_*.csv, issues_*.csv, crosstabs_groups.csv (collected from official / public sources)
Group results are counts of ballots or registrations by the group's recorded attribute, never how anyone voted. Counties
are grouped by population density (a place attribute), not by who lives there.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import us_ctx_ev as ev  # noqa: E402
import us_ctx_nc as nc  # noqa: E402
import us_ctx_reg as rg  # noqa: E402
import us_ctx_states  # noqa: E402
import us_ctx_tax as tx  # noqa: E402

OUT = ROOT / "site" / "data" / "us2026_context"
PUB = ROOT / "data_public" / "us2026_context"

# names the tests (and other analysis code) use
race_group, age_group, age_ncreg_group = nc.race_group, nc.age_group, nc.age_ncreg_group
party_group, gender_group, density_tier = nc.party_group, nc.gender_group, nc.density_tier
nc_count, cumulate = nc.nc_count, nc.cumulate
_r = tx.r2


def _latest(d: Path, pat: str) -> Path | None:
    f = sorted(d.glob(pat))
    return f[-1] if f else None


# ----------------------------------------------------------------------------------- early vote by group, every state
def build_groups(raw: Path, mix: dict | None, log=print) -> dict | None:
    uf_file = _latest(raw / "ev_groups", "uf_*.csv")
    if uf_file is None:
        return None
    uf = ev.uf_states(uf_file)
    entries: dict[str, dict] = {}
    nr = raw / "ev_party" / "nc"
    if (nr / "absentee_20261103.zip").exists():
        counts, stats = nc.ev_counts(nr)
        entries["nc"] = ev.nc_entry(counts, stats, rg.nc_reg_shares(mix), (mix or {}).get("asof_now", ""), log)
    for st, fn in (("co", lambda: ev.co_entry(raw, uf, log)), ("sc", lambda: ev.sc_entry(raw, uf, log)),
                   ("la", lambda: ev.la_entry(raw, log))):
        e = fn()
        if e:
            entries[st] = e
    for st in ("ga", "mi", "nv", "pa", "ks", "id", "ia", "ct", "de", "ok", "ri", "vt", "wa"):
        if uf.get(st):
            entries[st] = ev.uf_entry(st, uf[st])
    states = sorted(entries.values(), key=lambda e: (not e["baselines"], -e["total"]))
    used = {k.split(":", 1)[0] for e in states for k in e["own"]}
    return {"built": date.today().isoformat(), "taxonomy": tx.TAXONOMY, "own_dims": [d for d in tx.OWN_DIMS if d["dim"] in used],
            "years": tx.YEARS, "mapping": tx.mapping_rows(), "states": states, "uf_file": uf_file.name,
            "note": "Share = the group's share of the ballots that have the attribute recorded; coverage = those ballots as a share of all "
                    "ballots counted. Past years are the baselines named per state."}


# --------------------------------------------------------------------------------- registration over time (CSVs)
def moe(n: float | None, p: float = 0.5) -> float | None:
    """Approximate 95% margin of error in points for a proportion in a simple random sample of n (before design effect)."""
    return None if not n or n <= 0 else tx.r2(196 * math.sqrt(p * (1 - p) / n), 1)


def _reg_summary(st: str, series: list[dict], url: str) -> dict:
    return rg.reg_summary(series, url)


# ------------------------------------------------------------------------------------------------- polls: issues
def _records(d: pd.DataFrame) -> list[dict]:
    return [{k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in r.items()} for r in d.to_dict("records")]


def load_issues() -> dict:
    out = {"gallup": [], "polls": []}
    g, p = PUB / "issues_gallup.csv", PUB / "issues_polls.csv"
    if g.exists():
        out["gallup"] = _records(pd.read_csv(g))
    if p.exists():
        out["polls"] = _records(pd.read_csv(p))
    return out


# a poll's own group label -> harmonised key, only where it is the same group
_TAX = {"race": {"white": "White", "white or caucasian": "White",
                 "black": "Black", "afram/black": "Black", "black/af-am": "Black", "black or african american": "Black",
                 "hispanic": "Hispanic", "hisp/lat": "Hispanic", "hispanic/latino": "Hispanic", "hispanic or latino": "Hispanic",
                 "hispanic or latino of any race": "Hispanic", "latino": "Hispanic",
                 "asian": "Asian"},
        "age": {"18-29": "18-29", "18-29 years": "18-29", "30-44": "30-44", "30-44 years": "30-44", "45-64": "45-64",
                "45-64 years": "45-64", "65+": "65+", "65 or more years": "65+", "65 and over": "65+"},
        "gender": {"female": "Female", "women": "Female", "woman": "Female", "male": "Male", "men": "Male", "man": "Male"}}


def tax_key(dim: str, group: str) -> str | None:
    g = _TAX.get(str(dim), {}).get(str(group).strip().lower())
    return f"{dim}:{g}" if g else None


def load_crosstabs() -> dict:
    f = PUB / "crosstabs_groups.csv"
    if not f.exists():
        return {"rows": []}
    d = pd.read_csv(f)
    rows = []
    for r in d.to_dict("records"):
        r = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in r.items()}
        gn = r.get("group_n")
        if gn is None and r.get("group_share") is not None and r.get("sample_n"):
            gn = r["sample_n"] * r["group_share"] / 100
            r["group_n_est"] = True
        r["group_n_used"] = None if gn is None else int(round(gn))
        r["moe_group"] = moe(gn)
        r["moe_poll"] = moe(r.get("sample_n"))
        if r.get("dem_pct") is not None and r.get("rep_pct") is not None:
            r["margin"] = tx.r2(r["dem_pct"] - r["rep_pct"], 1)
        r["tax"] = tax_key(r.get("dim"), r.get("group"))
        rows.append(r)
    return {"rows": rows}


# ---------------------------------------------------------------------------------------------------------- main
def write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, allow_nan=False, default=str) + "\n")


def main() -> int:
    import us_ev_db as db
    raw = db.data_root() / "raw" / "us"
    today = date.today().isoformat()
    mix = rg.build_nc_mix(raw)
    grp = build_groups(raw, mix)
    reg = rg.load_registration(PUB, raw, today)
    write("ev_groups.json", grp)
    write("reg_nc_mix.json", mix)
    write("registration.json", reg)
    write("issues.json", load_issues())
    write("crosstabs.json", load_crosstabs())
    write("states.json", us_ctx_states.build())
    write("meta.json", {"built": today, "ev_groups": bool(grp), "reg_mix": bool(mix),
                        "baselines": {e["state"]: sorted(e["baselines"]) for e in (grp or {}).get("states", []) if e["baselines"]},
                        "registration_states": sorted(reg["states"])})
    print("ok", bool(grp), bool(mix), len(reg["states"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
