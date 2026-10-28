"""Canada: parsers for Elections Canada's official results files and the Polymarket markets used
on the dashboard (docs/canada.md).

No count over time is published by Elections Canada (its election-night site, enr.elections.ca,
keeps only the latest state), so this module holds final-results parsers, the archived snapshots
of the national results page (Internet Archive captures of an official page carrying Elections
Canada's own "Last updated at" stamp), and the market list. Nothing here fits a count model.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

# Elections Canada province codes (first two digits of the FED number) -> postal abbreviation
PROV = {10: "nl", 11: "pe", 12: "ns", 13: "nb", 24: "qc", 35: "on", 46: "mb", 47: "sk", 48: "ab",
        59: "bc", 60: "yt", 61: "nt", 62: "nu"}
PROV_NAME = {"nl": "Newfoundland and Labrador", "pe": "Prince Edward Island", "ns": "Nova Scotia",
             "nb": "New Brunswick", "qc": "Quebec", "on": "Ontario", "mb": "Manitoba", "sk": "Saskatchewan",
             "ab": "Alberta", "bc": "British Columbia", "yt": "Yukon", "nt": "Northwest Territories",
             "nu": "Nunavut"}

PARTIES = ("LPC", "CPC", "NDP", "BQ", "GPC", "PPC", "OTH")
# candidate-string suffixes in the official Table 12 (party affiliation as printed on the ballot)
_SUFFIX = [("Liberal/Libéral", "LPC"), ("Conservative/Conservateur", "CPC"),
           ("NDP-New Democratic Party/NPD-Nouveau Parti démocratique", "NDP"), ("Bloc Québécois", "BQ"),
           ("Green Party/Parti Vert", "GPC"), ("People's Party - PPC", "PPC")]
# column prefixes in the transposition file
_TRANS = {"Liberal Party of Canada": "LPC", "Conservative Party of Canada": "CPC", "New Democratic Party": "NDP",
          "Bloc Québécois": "BQ", "Green Party of Canada": "GPC", "People's Party of Canada": "PPC"}


def unit_id(fed_num: int) -> str:
    """`ca-<province postal code>-<5-digit FED number>`, e.g. 35075 -> ca-on-35075."""
    return f"ca-{PROV[int(fed_num) // 1000]}-{int(fed_num):05d}"


def party_of(candidate: str) -> str:
    """Party code from an official Table 12 candidate string ("Paul Connors Liberal/Libéral")."""
    for suf, code in _SUFFIX:
        if candidate.rstrip().endswith(suf):
            return code
    return "OTH"


def _wide(df: pd.DataFrame) -> pd.DataFrame:
    w = df.pivot_table(index="fed_num", columns="party", values="votes", aggfunc="sum", fill_value=0)
    for p in PARTIES:
        if p not in w:
            w[p] = 0
    return w[list(PARTIES)].astype(int)


def read_table12(path) -> tuple[pd.DataFrame, pd.Series]:
    """Official results Table 12 (candidates by electoral district) -> (votes by FED x party, FED names)."""
    t = pd.read_csv(path, encoding="utf-8-sig")
    t.columns = ["prov", "fed", "fed_num", "cand", "res", "occ", "votes", "pct", "maj", "majpct"]
    t["fed_num"] = t["fed_num"].astype(int)
    t["party"] = t["cand"].map(party_of)
    names = t.drop_duplicates("fed_num").set_index("fed_num")["fed"].map(lambda s: s.split("/")[0])
    return _wide(t), names


def read_transposition(path) -> pd.DataFrame:
    """Elections Canada transposition of the 2021 (44th GE) vote to the 2023 representation order
    (transposition_343_fed_vote_count.csv) -> votes by FED x party."""
    v = pd.read_csv(path, encoding="utf-8-sig")
    out = pd.DataFrame(index=v["FED_NUM"].astype(int).rename("fed_num"))
    out[list(PARTIES)] = 0
    for c in v.columns[5:]:
        if c in ("Rejected_Ballots", "Transposed_Votes", "Registered_Electors", "Estimated_Turnout"):
            continue
        code = next((k for name, k in _TRANS.items() if c.split("/")[0] == name), "OTH")
        out[code] += pd.to_numeric(v[c], errors="coerce").fillna(0).astype(int).to_numpy()
    return out.astype(int)


_ENR_PARTY = {"Liberal": "LPC", "Conservative": "CPC", "NDP-New Democratic Party": "NDP", "Bloc Québécois": "BQ",
              "Green Party": "GPC", "People's Party - PPC": "PPC"}
ET = timezone(timedelta(hours=-4))   # EDT on 2025-04-28/29


def parse_enr_national(page: str, capture: datetime) -> dict:
    """Elections Canada's election-night national page (enr.elections.ca/National.aspx), as archived.

    Returns {"updated": UTC datetime from the page's own "Last updated at HH:MM ET" stamp (date taken
    from the capture, stepping back a day if the stamp would be after the capture), "polls": (reported,
    total), "seats": {party: seats leading or elected}, "votes": {party: votes}}."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
    m = re.search(r"Polls Reporting: ([\d,]+) of ([\d,]+)", t)
    polls = (int(m[1].replace(",", "")), int(m[2].replace(",", ""))) if m else None
    u = re.search(r"Last updated at (\d{1,2}):(\d{2}) ET", t)
    updated = None
    if u:
        cap_et = capture.astimezone(ET)
        stamp = cap_et.replace(hour=int(u[1]), minute=int(u[2]), second=0, microsecond=0)
        if stamp > cap_et + timedelta(minutes=1):
            stamp -= timedelta(days=1)
        updated = stamp.astimezone(timezone.utc)
    seats, votes = {}, {}
    names = sorted(_ENR_PARTY, key=len, reverse=True)
    for name in names:
        r = re.search(re.escape(name) + r" (\d+) [\d.]+ % ([\d,]+) [\d.]+ %", t)
        if r:
            seats[_ENR_PARTY[name]] = int(r[1])
            votes[_ENR_PARTY[name]] = int(r[2].replace(",", ""))
    return {"updated": updated, "polls": polls, "seats": seats, "votes": votes}


# ---- Polymarket: one dashboard race per binary question, A = the YES side unless noted.
# (event slug, market slug, race id, race name, A name, B name, region)
_MOST = "will-the-{p}-win-the-most-seats-in-{prov}-in-the-next-canadian-election"
PROVINCE_EVENTS = {"on": "ontario", "qc": "quebec", "bc": "british-columbia", "ab": "alberta", "mb": "manitoba",
                   "sk": "saskatchewan", "ns": "nova-scotia", "nb": "new-brunswick", "pe": "prince-edward-island",
                   "nl": "newfoundland-and-labrador"}


def federal_markets() -> list[dict]:
    """The 2025 federal markets on the dashboard: the national winner markets, the winning band of
    each margin event, and each province's 'most seats' market for the eventual provincial winner."""
    m = [
        {"event": "next-prime-minster-of-canada", "slug": "will-mark-carney-be-the-next-canadian-prime-minister",
         "race_id": "pm-carney", "name": "Mark Carney is the next prime minister", "a": "Carney", "b": "Not Carney",
         "region": None},
        {"event": "next-prime-minster-of-canada", "slug": "will-pierre-poilievre-be-the-next-canadian-prime-minister",
         "race_id": "pm-poilievre", "name": "Pierre Poilievre is the next prime minister", "a": "Poilievre",
         "b": "Not Poilievre", "region": None},
        {"event": "which-party-wins-most-seats-in-canadian-election",
         "slug": "will-the-liberal-party-win-the-most-seats-in-the-next-canadian-election",
         "race_id": "most-lpc", "name": "Liberals win the most seats", "a": "Liberal", "b": "Another party",
         "region": None},
        {"event": "which-party-wins-most-seats-in-canadian-election",
         "slug": "will-the-conservative-party-win-the-most-seats-in-the-next-canadian-election",
         "race_id": "most-cpc", "name": "Conservatives win the most seats", "a": "Conservative",
         "b": "Another party", "region": None},
        {"event": "canada-election-of-seats-margin-of-victory", "slug": "will-the-liberal-party-win-by-2549-seats",
         "race_id": "seatmargin-lpc-25-49", "name": "Liberals win by 25-49 seats", "a": "Liberals by 25-49",
         "b": "Other margin", "region": None},
        {"event": "canada-election-popular-vote-margin-of-victory",
         "slug": "will-the-liberal-party-win-the-canadian-election-by-0-3",
         "race_id": "votemargin-lpc-0-3", "name": "Liberals win the popular vote by 0-3 points",
         "a": "Liberals by 0-3", "b": "Other margin", "region": None},
    ]
    for prov, ev in PROVINCE_EVENTS.items():
        rivals = [("liberal-party", "lpc", "Liberals", "Liberal")]
        rivals.append(("bloc-qubcois", "bq", "Bloc Québécois", "Bloc Québécois") if prov == "qc"
                      else ("conservative-party", "cpc", "Conservatives", "Conservative"))
        for p, code, nm, a in rivals:
            slug = _MOST.format(p=p, prov=ev)
            if prov == "qc" and code == "lpc":   # Polymarket's slug for this market says "third-most"
                slug = slug.replace("win-the-most", "win-the-third-most")
            m.append({"event": f"which-party-wins-most-seats-in-{ev}", "slug": slug,
                      "race_id": f"{prov}-most-{code}", "name": f"{nm} win{'s' if code == 'bq' else ''} the most seats "
                      f"in {PROV_NAME[prov]}", "a": a, "b": "Another party", "region": prov})
    return m


# ---- Quebec (Élections Québec / DGEQ open data, resultats.json)
# DGEQ abbreviations -> codes; "PCOQ" is the Parti conservateur du Québec ("PCQ" and "PCQ/CPQ" are other parties)
QC_PARTY = {"PQ": "PQ", "PLQ/QLP": "PLQ", "CAQ": "CAQ", "PCOQ": "PCQ", "QS": "QS", "PVQ/GPQ": "PVQ"}
QC_PARTIES = ("PQ", "PLQ", "CAQ", "PCQ", "QS", "PVQ", "OTH")


def qc_unit_id(numero: int) -> str:
    """`ca-qc-p<circonscription number>`; the `p` keeps provincial codes apart from federal ones (ca-qc-24xxx)."""
    return f"ca-qc-p{int(numero)}"


def parse_dgeq(raw: bytes) -> dict:
    """Élections Québec results file (resultats.json, live or archived; gzip or plain) ->
    {"updated": UTC Timestamp, "final": bool, "boxes": (complete, total), "votes": DataFrame circonscription x party,
     "names": {numero: name}, "leading": {party: circonscriptions leading}}."""
    import gzip
    import json
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    d = json.loads(raw.decode("utf-8"))
    st = d["statistiques"]
    rows, names = [], {}
    for c in d["circonscriptions"]:
        names[int(c["numeroCirconscription"])] = c["nomCirconscription"]
        for k in c.get("candidats", []):
            rows.append({"fed_num": int(c["numeroCirconscription"]),
                         "party": QC_PARTY.get(k.get("abreviationPartiPolitique") or "", "OTH"),
                         "votes": int(k.get("nbVoteTotal") or 0)})
    w = pd.DataFrame(rows, columns=["fed_num", "party", "votes"]).pivot_table(
        index="fed_num", columns="party", values="votes", aggfunc="sum", fill_value=0)
    for p in QC_PARTIES:
        if p not in w:
            w[p] = 0
    stamp = st.get("iso8601DateMAJ")
    return {"updated": pd.Timestamp(stamp.replace(",", ".")).tz_convert("UTC") if stamp else None,
            "final": bool(st.get("isResultatsFinaux")),
            "boxes": (int(st.get("nbBureauVoteRempli") or 0), int(st.get("nbBureauVote") or 0)),
            "votes": w[list(QC_PARTIES)].astype(int), "names": names,
            "leading": {QC_PARTY.get(p["abreviationPartiPolitique"], "OTH"): int(p["nbCirconscriptionsEnAvance"])
                        for p in st["partisPolitiques"]}}


def quebec_markets() -> list[dict]:
    """2026 Quebec markets on the dashboard (A = YES)."""
    ev = "quebec-general-election-winner"
    most = "will-{p}-win-the-most-seats-in-the-2026-quebec-general-election"
    out = [{"event": ev, "slug": most.format(p=p), "race_id": f"most-{code.lower()}",
            "name": f"{nm} wins the most seats", "a": nm, "b": "Another party", "region": None}
           for p, code, nm in (("parti-qubcois", "PQ", "Parti Québécois"),
                               ("parti-libral-du-qubec", "PLQ", "Quebec Liberal Party"),
                               ("coalition-avenir-qubec", "CAQ", "Coalition Avenir Québec"),
                               ("parti-conservateur-du-qubec", "PCQ", "Conservative Party of Quebec"))]
    out.append({"event": "will-the-pq-win-a-majority-in-the-2026-quebec-general-election",
                "slug": "will-the-pq-win-a-majority-in-the-2026-quebec-general-election", "race_id": "pq-majority",
                "name": "Parti Québécois wins a majority", "a": "PQ majority", "b": "No PQ majority", "region": None})
    out.append({"event": "next-premier-of-quebec-594",
                "slug": "will-paul-st-pierre-plamondon-be-the-next-premier-of-quebec-following-the-2026-quebec-general-"
                        "election", "race_id": "premier-psp", "name": "Paul St-Pierre Plamondon is the next premier",
                "a": "St-Pierre Plamondon", "b": "Someone else", "region": None})
    return out
