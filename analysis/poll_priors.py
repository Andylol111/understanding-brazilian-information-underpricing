"""Poll-based starting estimates for the replays (the Germany v3 idea, for Poland, Brazil, Mexico, Hungary).

Rule (written before any 2025/2026 night was replayed with it; the prior is a labelled sensitivity "chosen after the result was known"):

  1. Poll average of the target election: every poll in data_public/<cc>_polls_<tag>.csv whose fieldwork ended in the 14 days before
     election day (fieldwork end <= the day before; plain mean, no weighting by firm or sample). For every poll, the two-way share of
     side A, A / (A + B), is formed from the columns printed in that poll (so undecided / blank columns drop out), then averaged.
  2. Poll error: the same average and the same two-way share for every EARLIER election of that country with a usable poll table
     (only elections held before the target's date, rounds pooled), against the official result's two-way share. The sd of the
     starting estimate is the root-mean-square of those errors (bias is not corrected, n is small and said so); no floor, no tuning.
  3. Starting estimate: the nowcast swing prior (brunder.nowcast.Params.swing_prior_mean / swing_prior_sd, count_scaled_terms): the
     mean is the poll two-way share minus the prior's vote-weighted two-way share, the sd is the poll error from step 2. It carries
     the chance from poll close, before any count, and fades as the count's own swing takes over. Every other setting is v1's.

    uv run python analysis/poll_priors.py [election_id ...]      # prints the averages and errors used; writes results/poll_priors/<id>.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_public"
OUT = ROOT / "results" / "poll_priors"
WINDOW_DAYS = 14


def read_polls(name: str) -> pd.DataFrame:
    d = pd.read_csv(DATA / name, comment="#", parse_dates=["fieldwork_start", "fieldwork_end"], skipinitialspace=True)
    return d


def _num(s: pd.Series) -> pd.Series:
    """Printed percentages; '<0.5' / '<1' (only ever small 'others') count as half the bound."""
    t = s.astype(str).str.replace(",", ".").str.rstrip("%").str.strip()
    lt = t.str.startswith("<")
    v = pd.to_numeric(t.str.lstrip("<"), errors="coerce")
    return v.where(~lt, v / 2)


def two_way(polls: pd.DataFrame, a: list[str], b: list[str]) -> pd.Series:
    """Per poll: sum of A columns / (sum of A + sum of B columns); NaN where a side is not reported."""
    sa = sum(_num(polls[c]).fillna(0) for c in a)
    sb = sum(_num(polls[c]).fillna(0) for c in b)
    return (sa / (sa + sb)).where((sa + sb) > 0)


def window(polls: pd.DataFrame, date: str, days: int = WINDOW_DAYS) -> pd.DataFrame:
    e = pd.Timestamp(date)
    return polls[(polls["fieldwork_end"] >= e - pd.Timedelta(days=days)) & (polls["fieldwork_end"] < e)]


def average(file: str, date: str, a: list[str], b: list[str]) -> dict:
    p = read_polls(file)
    if "phase" in p:                                   # runoff files also hold pre-first-round head-to-head hypotheticals
        p = p[p["phase"] == "post_R1"]
    if p["fieldwork_end"].isna().all() and "note" in p:   # Mexico 2012: only the month is printed; the three flagged final releases
        p = p[p["note"].astype(str).str.contains("Última encuesta previa")]
    else:
        p = window(p, date)
    s = two_way(p, a, b).dropna()
    if s.empty:
        raise ValueError(f"no usable polls in the {WINDOW_DAYS} days before {date} in {file}")
    return {"file": file, "date": date, "n_polls": int(len(s)), "two_way": float(s.mean()),
            "first_fieldwork_end": str(p["fieldwork_end"].min().date()) if p["fieldwork_end"].notna().any() else "month only (final releases)",
            "last_fieldwork_end": str(p["fieldwork_end"].max().date()) if p["fieldwork_end"].notna().any() else "month only (final releases)",
            "pollsters": sorted(set(p.loc[s.index, "pollster"].astype(str)))}


def official(results_file: str, key: dict, a: list[str], b: list[str]) -> float:
    r = pd.read_csv(DATA / results_file, comment="#", skipinitialspace=True)
    for k, v in key.items():
        r = r[r[k] == v]
    if len(r) != 1:
        raise ValueError(f"{results_file} {key}: {len(r)} rows")
    r = r.iloc[0]
    sa, sb = sum(float(r[c]) for c in a), sum(float(r[c]) for c in b)
    return sa / (sa + sb)


# Earlier elections per country: (label, date, polls file, results key, A columns, B columns). Only those before a target are used.
# The A / B orientation is irrelevant for an error (the same two sides in the poll and the result); it follows the model's A where there is one.
HIST = {
    "pl": ("pl_poll_results.csv", [
        ("2010 round 1", "2010-06-20", "pl_polls_2010r1.csv", {"year": 2010, "round": 1}, ["Komorowski"], ["Kaczyński"]),
        ("2010 round 2", "2010-07-04", "pl_polls_2010r2.csv", {"year": 2010, "round": 2}, ["Komorowski"], ["Kaczyński"]),
        ("2015 round 1", "2015-05-10", "pl_polls_2015r1.csv", {"year": 2015, "round": 1}, ["Komorowski"], ["Duda"]),
        ("2015 round 2", "2015-05-24", "pl_polls_2015r2.csv", {"year": 2015, "round": 2}, ["Komorowski"], ["Duda"]),
        ("2020 round 1", "2020-06-28", "pl_polls_2020r1.csv", {"year": 2020, "round": 1}, ["Trzaskowski"], ["Duda"]),
        ("2020 round 2", "2020-07-12", "pl_polls_2020r2.csv", {"year": 2020, "round": 2}, ["Trzaskowski"], ["Duda"]),
        ("2025 round 1", "2025-05-18", "pl_polls_2025r1.csv", {"year": 2025, "round": 1}, ["Trzaskowski"], ["Nawrocki"]),
        ("2025 round 2", "2025-06-01", "pl_polls_2025r2.csv", {"year": 2025, "round": 2}, ["Trzaskowski"], ["Nawrocki"]),
    ]),
    "mx": ("mx_poll_results.csv", [
        ("2006", "2006-07-02", "mx_polls_2006.csv", {"year": 2006}, ["Lopez_Obrador"], ["Calderon", "Madrazo"]),
        ("2012", "2012-07-01", "mx_polls_2012.csv", {"year": 2012}, ["Lopez_Obrador"], ["Pena_Nieto", "Vazquez_Mota"]),
        ("2018", "2018-07-01", "mx_polls_2018.csv", {"year": 2018}, ["Lopez_Obrador"], ["Anaya", "Meade"]),
        ("2024", "2024-06-02", "mx_polls_2024.csv", {"year": 2024}, ["Sheinbaum"], ["Galvez"]),
    ]),
    "hu": ("hu_poll_results.csv", [
        ("2014", "2014-04-06", "hu_polls_2014.csv", {"year": 2014}, ["Fidesz-KDNP"], ["Unity"]),
        ("2018", "2018-04-08", "hu_polls_2018.csv", {"year": 2018}, ["Fidesz-KDNP"], ["Jobbik"]),
        ("2022", "2022-04-03", "hu_polls_2022.csv", {"year": 2022}, ["Fidesz-KDNP"], ["EM"]),
        ("2026", "2026-04-12", "hu_polls_2026.csv", {"year": 2026}, ["Tisza"], ["Fidesz-KDNP"]),
    ]),
    "br": ("br_poll_results.csv", [
        ("2014 round 1", "2014-10-05", "br_polls_2014r1.csv", {"year": 2014, "round": 1}, ["Rousseff"], ["Neves"]),
        ("2014 round 2", "2014-10-26", "br_polls_2014r2.csv", {"year": 2014, "round": 2}, ["Rousseff"], ["Neves"]),
        ("2018 round 1", "2018-10-07", "br_polls_2018r1.csv", {"year": 2018, "round": 1}, ["Haddad"], ["J_Bolsonaro"]),
        ("2018 round 2", "2018-10-28", "br_polls_2018r2.csv", {"year": 2018, "round": 2}, ["Haddad"], ["J_Bolsonaro"]),
        ("2022 round 1", "2022-10-02", "br_polls_2022r1.csv", {"year": 2022, "round": 1}, ["Lula"], ["J_Bolsonaro"]),
        ("2022 round 2", "2022-10-30", "br_polls_2022r2.csv", {"year": 2022, "round": 2}, ["Lula"], ["J_Bolsonaro"]),
        ("2026 round 1", "2026-10-04", "br_polls_2026r1.csv", {"year": 2026, "round": 1}, ["Lula"], ["F_Bolsonaro"]),
        ("2026 round 2", "2026-10-25", "br_polls_2026r2.csv", {"year": 2026, "round": 2}, ["Lula"], ["F_Bolsonaro"]),
    ]),
}


def errors(country: str, before: str) -> pd.DataFrame:
    res, hist = HIST[country]
    rows = []
    for label, date, pf, key, a, b in hist:
        if date >= before:
            continue
        avg = average(pf, date, a, b)
        fin = official(res, key, a, b)
        rows.append({"election": label, "date": date, "n_polls": avg["n_polls"], "poll_two_way": avg["two_way"],
                     "official_two_way": fin, "error": fin - avg["two_way"]})
    return pd.DataFrame(rows)


def prior_estimate(country: str, target_date: str, file: str, a: list[str], b: list[str]) -> dict:
    """{poll average of the target, its error sd from earlier elections, the errors used}."""
    avg = average(file, target_date, a, b)
    err = errors(country, target_date)
    if err.empty:
        raise ValueError("no earlier election with polls")
    rms = float(np.sqrt((err["error"] ** 2).mean()))
    return {**avg, "sd": rms, "n_errors": int(len(err)), "errors": err.round(5).to_dict("records")}


# target id -> (country, date, poll file, A, B)
TARGETS = {
    "pl2025r1": ("pl", "2025-05-18", "pl_polls_2025r1.csv", ["Trzaskowski"], ["Nawrocki"]),
    "pl2025r2": ("pl", "2025-06-01", "pl_polls_2025r2.csv", ["Trzaskowski"], ["Nawrocki"]),
    "mx2024": ("mx", "2024-06-02", "mx_polls_2024.csv", ["Sheinbaum"], ["Galvez"]),
    "hu2026": ("hu", "2026-04-12", "hu_polls_2026.csv", ["Tisza"], ["Fidesz-KDNP"]),
    "br2022r1": ("br", "2022-10-02", "br_polls_2022r1.csv", ["Lula"], ["J_Bolsonaro"]),
    "br2022r2": ("br", "2022-10-30", "br_polls_2022r2.csv", ["Lula"], ["J_Bolsonaro"]),
    "br2026r1": ("br", "2026-10-04", "br_polls_2026r1.csv", ["Lula"], ["F_Bolsonaro"]),
    "br2026r2": ("br", "2026-10-25", "br_polls_2026r2.csv", ["Lula"], ["F_Bolsonaro"]),
}


WIKI = {"pl": "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2025_Polish_presidential_election",
        "br": "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2026_Brazilian_presidential_election",
        "mx": "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2024_Mexican_general_election",
        "hu": "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2026_Hungarian_parliamentary_election"}


def publish_meta(eid: str, country: str, a: str, b: str, est: dict, final: float, base: float) -> None:
    """Add the v3 note and the poll source to site/data/<id>/meta.json (idempotent; v1 notes and sources untouched). Every number is
    taken from the config / the result, none typed."""
    f = ROOT / "site" / "data" / eid / "meta.json"
    meta = json.loads(f.read_text())
    note = (f"Sensitivity (v3, poll start): the same model as v1, started from the last two weeks of polls instead of the previous election alone. "
            f"The {est['n_polls']} polls that ended {est['first_fieldwork_end']} to {est['last_fieldwork_end']} put {a} at {100 * est['two_way']:.1f}% of the {a} + {b} vote "
            f"(the prior had {100 * base:.1f}%); the swing is allowed an error of {100 * est['sd']:.1f} points, the root-mean-square error of the same kind of poll average "
            f"in {est['n_errors']} earlier elections of this country. The official result was {100 * final:.1f}%. The chance exists from poll close, before any count, and "
            "fades into the count as counted votes take over. It was designed after the result was known, so it is a sensitivity, not a forecast; v1 stays the headline.")
    meta["notes"] = [n for n in meta["notes"] if not n.startswith("Sensitivity (v3")] + [note]
    src = {"name": "Opinion polling tables on Wikipedia (polls by the named pollsters; fieldwork dates and samples in data_public/)",
           "url": WIKI[country], "licence": "CC BY-SA 4.0 (Wikipedia text and tables); the polls are published by the pollsters"}
    meta["sources"] = [x for x in meta["sources"] if not x["name"].startswith("Opinion polling tables on Wikipedia")] + [src]
    f.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))


def main(argv: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for eid in argv or list(TARGETS):
        c, d, f, a, b = TARGETS[eid]
        est = prior_estimate(c, d, f, a, b)
        (OUT / f"{eid}.json").write_text(json.dumps(est, indent=1, ensure_ascii=False))
        print(eid, f"poll two-way {est['two_way']:.4f} (n={est['n_polls']}), sd {est['sd']:.4f} from {est['n_errors']} earlier elections")
        print(pd.DataFrame(est["errors"]).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
