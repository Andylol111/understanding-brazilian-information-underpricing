"""One harmonised taxonomy for the midterms context tables (race, age, gender, party, density) + the explicit source-to-taxonomy
mapping table. A source's categories are mapped into a harmonised group only when they union exactly into it; everything else is
kept under the source's OWN dim id (age_co, age_uf, age_ncreg, race_la) and is never compared with a harmonised dim."""
from __future__ import annotations

RACE = ["White", "Black", "Hispanic", "Asian", "Other / unknown"]
AGE = ["18-29", "30-44", "45-64", "65+"]
GENDER = ["Female", "Male", "Undisclosed"]
PARTY = ["Democratic", "Republican", "Unaffiliated", "Other party"]
DENS = ["Dense counties (500+ people/sq mi)", "Middle counties (100-500)", "Sparse counties (under 100)"]

TAXONOMY = [
    {"dim": "race", "label": "Race / ethnicity", "groups": RACE},
    {"dim": "age", "label": "Age", "groups": AGE},
    {"dim": "gender", "label": "Gender", "groups": GENDER},
    {"dim": "party", "label": "Party registration", "groups": PARTY},
    {"dim": "density", "label": "Kind of county", "groups": DENS},
]
DIM_LABEL = {t["dim"]: t["label"] for t in TAXONOMY}
DIM_ORDER = {t["dim"]: t["groups"] for t in TAXONOMY}

CO_BANDS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"]
UF_BANDS = ["18-25", "26-40", "41-65", "66+"]
LA_RACE = ["White", "Black", "Other"]
OWN_DIMS = [
    {"dim": "age_co", "label": "Age, Colorado's bands", "groups": CO_BANDS},
    {"dim": "age_uf", "label": "Age, UF Election Lab bands", "groups": UF_BANDS},
    {"dim": "age_ncreg", "label": "Age, NC registration bands", "groups": UF_BANDS},
    {"dim": "race_la", "label": "Race, Louisiana's categories", "groups": LA_RACE},
]
OWN_LABEL = {d["dim"]: d["label"] for d in OWN_DIMS}
OWN_ORDER = {d["dim"]: d["groups"] for d in OWN_DIMS}

YEARS = [{"year": 2018, "type": "midterm"}, {"year": 2022, "type": "midterm"},
         {"year": 2020, "type": "presidential"}, {"year": 2024, "type": "presidential"}]
ELECTION_DAY = {2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05", 2026: "2026-11-03"}

S_NC, S_NCREG, S_CO, S_SC, S_LA, S_UF = ("NC per-voter file", "NC voter_stats file", "CO daily workbook", "SC absentee statistics",
                                         "LA statewide report", "UF Election Lab")


def label(dim: str) -> str:
    return DIM_LABEL.get(dim) or OWN_LABEL[dim]


def order(dim: str) -> list[str]:
    return DIM_ORDER.get(dim) or OWN_ORDER[dim]


def _m(source, dim, group, frm, status="mapped"):
    return {"source": source, "dim": dim, "group": group, "from": frm, "status": status}


def _nm(source, dim, group, frm, why):
    return _m(source, dim, group, frm, f"not mapped: {why}")


_NC_RACE = [
    ("White", "race = WHITE and ethnicity not HISPANIC or LATINO (NOT HISPANIC, UNDESIGNATED or blank)"),
    ("Black", "race = BLACK or AFRICAN AMERICAN and ethnicity not HISPANIC or LATINO"),
    ("Hispanic", "ethnicity = HISPANIC or LATINO (any race)"),
    ("Asian", "race = ASIAN and ethnicity not HISPANIC or LATINO"),
    ("Other / unknown", "race = AMERICAN INDIAN, PACIFIC ISLANDER, TWO or MORE, OTHER or UNDESIGNATED, not Hispanic"),
]
_NC_GENDER = [("Female", "sex = F"), ("Male", "sex = M"), ("Undisclosed", "sex = U, blank or any other code")]
_NC_PARTY = [("Democratic", "party = DEM"), ("Republican", "party = REP"), ("Unaffiliated", "party = UNA"),
             ("Other party", "party = LIB, GRE, CST, JFA, NLB or any other code")]
_NC_DENS = [(g, f"county population density {t} (ACS county file)") for g, t in zip(
    DENS, ["500 or more people per sq mi", "100 to 500 people per sq mi", "under 100 people per sq mi"])]


def mapping_rows() -> list[dict]:
    r: list[dict] = []
    for src in (S_NC, S_NCREG):
        r += [_m(src, "race", g, f) for g, f in _NC_RACE]
        r += [_m(src, "gender", g, f) for g, f in _NC_GENDER]
        r += [_m(src, "party", g, f) for g, f in _NC_PARTY]
        r += [_m(src, "density", g, f) for g, f in _NC_DENS]
    r += [_m(S_NC, "age", "18-29", "exact age 18 to 29, re-binned"), _m(S_NC, "age", "30-44", "exact age 30 to 44, re-binned"),
          _m(S_NC, "age", "45-64", "exact age 45 to 64, re-binned"), _m(S_NC, "age", "65+", "exact age 65 or more, re-binned")]
    r += [_nm(S_NCREG, "age", None, "age bands 18-25, 26-40, 41-65, 66+", "cut points differ; shown as own bands (age_ncreg)")]
    r += [_m(S_CO, "gender", "Female", "Female"), _m(S_CO, "gender", "Male", "Male"),
          _m(S_CO, "gender", "Undisclosed", "Not disclosed + Unknown + X")]
    r += [_nm(S_CO, "age", "18-29", "bands 18-24 and 25-34", "25-34 straddles 29; shown as own bands (age_co)"),
          _nm(S_CO, "age", "30-44", "bands 25-34 and 35-44", "25-34 straddles 30; shown as own bands (age_co)"),
          _m(S_CO, "age", "45-64", "bands 45-54 + 55-64"), _m(S_CO, "age", "65+", "bands 65-74 + 75 and over"),
          _nm(S_CO, "race", None, "no race or ethnicity published", "no race breakdown exists")]
    r += [_m(S_SC, "race", "White", "race = White"), _m(S_SC, "race", "Black", "race = Black / African American"),
          _m(S_SC, "race", "Hispanic", "race = Hispanic (a separate race category in SC data)"),
          _m(S_SC, "race", "Asian", "race = Asian"),
          _m(S_SC, "race", "Other / unknown", "race = Native American + Multiple + Other + Unknown")]
    r += [_nm(S_SC, "age", None, "no age breakdown published", "no age breakdown exists"),
          _nm(S_SC, "gender", None, "no gender breakdown published", "no gender breakdown exists")]
    r += [_nm(S_LA, "race", None, "White / Black / Other",
              "report does not say whether Hispanic voters sit in White, Black or Other; shown as own categories (race_la)"),
          _m(S_LA, "gender", "Female", "sex = FEMALE"), _m(S_LA, "gender", "Male", "sex = MALE"),
          _m(S_LA, "gender", "Undisclosed", "votes minus male minus female (no sex on file)")]
    r += [_m(S_UF, "race", "White", "nh_white (non-Hispanic white)"), _m(S_UF, "race", "Black", "nh_black"),
          _m(S_UF, "race", "Hispanic", "hispanic"), _m(S_UF, "race", "Asian", "nh_asian"),
          _m(S_UF, "race", "Other / unknown", "nh_native_american + nh_other"),
          _nm(S_UF, "age", None, "age_1..age_4 = 18-25, 26-40, 41-65, 66+", "cut points differ; shown as own bands (age_uf)"),
          _m(S_UF, "gender", "Female", "female"), _m(S_UF, "gender", "Male", "male"),
          _m(S_UF, "gender", "Undisclosed", "gender_unknown")]
    return r


# ------------------------------------------------------------------------------------------------ cell helpers
def r2(x, nd: int = 2):
    import math
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def share(counts: dict[str, int]) -> dict[str, float]:
    t = sum(counts.values())
    return {k: 100 * v / t for k, v in counts.items()} if t else {}


def _den(counts: dict) -> int:
    """denominator of a dim's shares: the key '_den' if the groups are only part of the recorded ballots, else their sum."""
    return counts["_den"] if "_den" in counts else sum(counts.values())


def cell(now: dict, g: str, past: dict[int, dict] | None = None, reg_share: dict | None = None) -> dict | None:
    """One group cell: share of the ballots whose attribute is recorded and their count, the registered share if given, and the
    same group in each past year (that year's counts, that year's denominator). Counts may carry a '_den' denominator."""
    if g not in now or not _den(now):
        return None
    c = {"share": r2(100 * now[g] / _den(now)), "n": int(now[g])}
    if reg_share and g in reg_share:
        c["reg_share"] = r2(reg_share[g])
    pa = {}
    for yr, cnt in (past or {}).items():
        if g in cnt and _den(cnt):
            pa[str(yr)] = {"share": r2(100 * cnt[g] / _den(cnt)), "n": int(cnt[g])}
    if pa:
        c["past"] = dict(sorted(pa.items(), reverse=True))
    return c


def cells(now: dict[str, dict], past: dict[int, dict[str, dict]] | None = None,
          reg: dict[str, dict] | None = None) -> tuple[dict, dict]:
    """{dim: counts} (+ past {year: {dim: counts}}, registered shares {dim: {group: share}}) -> (groups, own) cell dicts keyed
    '<dim>:<group>'; harmonised dims go to groups, every other dim to own."""
    groups, own = {}, {}
    for dim, cnt in now.items():
        for g in order(dim):
            c = cell(cnt, g, {y: d[dim] for y, d in (past or {}).items() if dim in d}, (reg or {}).get(dim))
            if c:
                (groups if dim in DIM_ORDER else own)[f"{dim}:{g}"] = c
    return groups, own
