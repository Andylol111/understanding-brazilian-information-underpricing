"""Find Polymarket ↔ Kalshi market pairs for the same proposition; write configs/pairs/<election>.toml.

    uv run python analysis/arb_pairs.py                 # every election in SPEC
    uv run python analysis/arb_pairs.py br2026r2 us2026

SPEC lists, per election, groups of (Polymarket event, Kalshi event) on the same question. Within a
group, outcomes are paired by name (`arb.match_outcomes`, unique both ways; `aliases` overrides).
Kinds:
  outcomes   multi-outcome events with the same candidate/party list → K YES ≡ PM YES
  single     one binary market each (PM event's only live market ↔ the Kalshi event's only market)
  br_states  Brazil runoff: PM per-state events (from configs/night_r2_v1.toml) ↔ Kalshi "Flávio wins
             <state>" markets; PM Flávio market → same side, PM Lula market → opposite side

Each pair records Polymarket's exact resolution text, Kalshi's resolution in OUR OWN WORDS
(`K_SAYS`, by series; `{x}` = the outcome) with a link to the market page, and
`resolution_differences`: automatic flags (sources, deadlines, tie and party/person rules detected in
the two texts, worded by us) plus the group's manual `notes`. Every pair is `risk_free = false`: the
venues never share one resolution source and timetable, so a cross-venue position can lose on both
legs; see docs/kalshi.md.

Reads only public listings (Kalshi Trade API, Polymarket Gamma). Kalshi's Data Terms cover market
descriptions, so no Kalshi title or rule text, and no Kalshi price or result, is written here.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

from brunder import _http, arb, kalshi, polymarket

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "configs" / "pairs"

BR_A, BR_B = "Lula", "Flávio Bolsonaro"
IL_ALIASES = {"Amcha Yisrael": "People of Israel", "RZP-Zehut": "Religious Zionist Party–Zehut",
              "The Reservists and the Economic Party": "Reservists–New Economic Party"}

# Kalshi resolution per series, paraphrased by us (read from the contract texts on 2026-10-28; check
# the linked market page and contract terms before relying on it). {x} = the outcome's name.
_GOV_PARTY = ("Yes if the party of the listed nominee ({x}) has its candidate inaugurated as governor after "
              "the 2026 election; can settle early on a media projection; closes at the swearing-in.")
_SEN_PARTY = ("Yes if the party of the listed nominee ({x}) wins this seat, judged by who is sworn in for the "
              "term starting 2027; can settle early on a media projection.")
_RANK = "Yes if {x} ranks {n} by {by} in the certified results, ranked by Kalshi's counting rules."
K_SAYS = {
    "KXBRPRES1R": "Yes if {x} gets the most first-round votes in 2026; closes once the winner is officially declared or certified.",
    "KXBRPRESIDENT2": _RANK.format(x="{x}", n="second", by="first-round votes"),
    "KXBRPRESIDENT3": _RANK.format(x="{x}", n="third", by="first-round votes"),
    "KXBRAZILPRES1R": "Yes if some candidate passes the 50% threshold in round one, so no runoff is needed.",
    "KXBRPRES": "Yes if {x} wins the 2026 presidential election (either round); closes once a winner is announced.",
    "KXBRRUNOFFSTATES": "Yes if Flávio Bolsonaro comes first in {x} in the certified runoff count; an exact tie pays pro rata; votes abroad belong to no state.",
    "KXBRRUNOFFSP": "Yes if {x} comes first in São Paulo in the certified runoff count; an exact tie pays pro rata.",
    "KXBRRUNOFFABROAD": "Yes if {x} comes first among votes cast abroad in the certified runoff count; an exact tie pays pro rata.",
    "KXDFGOV": "Yes if {x} wins the 2026 Federal District governor election (any round); can settle early on a media-call consensus.",
    "KXISRAELPM": ("Yes if {x} is the first person formally installed as Prime Minister (not caretaker) through government "
                   "formation after the 2026 election, before 27 Oct 2027."),
    "KXISRAELKNESSET": ("Yes if {x} wins the most Knesset seats; a seat tie goes to the list entering government, then to "
                        "vote share; can settle early on a media consensus."),
    "KXISRAEL2ND": _RANK.format(x="{x}", n="second", by="Knesset seats"),
    "KXISRAEL3RD": _RANK.format(x="{x}", n="third", by="Knesset seats"),
    "KXISRAEL4TH": _RANK.format(x="{x}", n="fourth", by="Knesset seats"),
    "KXISRAELBLOCMOST": ("Yes if the named bloc ({x}) wins more Knesset seats than the other bloc, counting only the lists "
                         "each venue names; a seat tie pays both sides half."),
    "KXISRAELANYSEAT": "Yes if {x} wins at least one Knesset seat once results are final (challenge period over or resolved).",
    "CONTROLH": ("Yes if the {x} wins House control: early on a consensus of media calls, otherwise by the Speaker's "
                 "party on 1 Feb 2027."),
    "CONTROLS": "Yes if the {x} wins Senate control: early on a consensus of media calls, otherwise by the fallback in Kalshi's contract terms.",
    "KXGOVCA": "Yes if {x} is elected governor of California in 2026; can settle early on a media projection.",
    "KXGOVAK": "Yes if {x} is elected governor of Alaska in 2026; can settle early on a media projection.",
    "GOVPARTYCA": _GOV_PARTY, "GOVPARTYTX": _GOV_PARTY, "GOVPARTYOH": _GOV_PARTY, "GOVPARTYGA": _GOV_PARTY,
    "GOVPARTYME": _GOV_PARTY, "GOVPARTYMI": _GOV_PARTY, "GOVPARTYIA": _GOV_PARTY, "GOVPARTYKS": _GOV_PARTY,
    "SENATETX": _SEN_PARTY, "SENATEOHS": _SEN_PARTY.replace("term starting 2027", "special-election seat"),
    "SENATEGA": _SEN_PARTY, "SENATEME": _SEN_PARTY, "SENATEMI": _SEN_PARTY, "SENATEIA": _SEN_PARTY,
    "SENATEKS": _SEN_PARTY,
    "KXAKSENATE": "Yes if {x} wins the 2026 Alaska Senate election; closes once the winner is officially declared or certified.",
    "KXMISENATE": ("Yes if {x} wins the 2026 Michigan Senate race as the nominee of the listed party (No if not "
                   "nominated); can settle early on a media consensus."),
    "KXTAIPEIMAYOR": "Yes if {x} wins the 2026 Taipei mayoral election; can settle early on a media-call consensus.",
    "KXNEWTAIPEIMAYOR": "Yes if {x} wins the 2026 New Taipei mayoral election; can settle early on a media-call consensus.",
    "KXFRENCHPRES": "Yes if {x} wins the next French presidential election.",
    "KXFRPRESADVANCE": "Yes if {x} qualifies for the runoff, or wins outright in round one.",
    "KXFRPRES1R1ST": _RANK.format(x="{x}", n="first", by="round-one votes as proclaimed by the Constitutional Council"),
    "KXFRPRES1R2ND": _RANK.format(x="{x}", n="second", by="round-one votes as proclaimed by the Constitutional Council"),
    "KXFRPRES1R3RD": _RANK.format(x="{x}", n="third", by="round-one votes as proclaimed by the Constitutional Council"),
    "KXFRPRESBALLOT": "Yes if {x} is on the official first-round candidate list before 30 Jun 2027.",
    "KXFRENCHPRES1R": "Yes if a candidate wins more than half of valid round-one votes, so no runoff is needed.",
    "KXSPAINPARLI": ("Yes if {x} wins the most seats in the next Spanish general election; a seat tie goes to the party "
                     "entering government, then to vote share."),
    "KXSPAIN2ND": _RANK.format(x="{x}", n="second", by="Congress seats"),
    "KXSPAIN3RD": _RANK.format(x="{x}", n="third", by="Congress seats"),
    "KXSPANISHPM": ("Yes if {x} is the first person formally installed as Prime Minister through government formation "
                    "after the next election, before 22 Aug 2028."),
    "KXVOTESPAIN": "Yes if PP and Vox together get at least 50% of the certified Congress vote.",
}
SPEC: dict[str, dict] = {
    "br2026r1": {
        "name": "Brazil presidential election, first round", "date": "2026-10-04",
        "groups": [
            {"race_id": "br", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election-first-round-winner", "k_event": "KXBRPRES1R-26OCT04",
             "notes": []},
            {"race_id": "br-2nd", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election-first-round-2nd-place", "k_event": "KXBRPRESIDENT2-BRPRES26-2",
             "notes": []},
            {"race_id": "br-3rd", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election-first-round-3rd-place", "k_event": "KXBRPRESIDENT3-BRPRES26-3",
             "notes": []},
            {"race_id": "br-outright", "kind": "single", "a": "someone wins outright", "b": "runoff",
             "pm_event": "will-any-presidential-candidate-win-outright-in-the-first-round-of-the-brazil-election",
             "k_event": "KXBRAZILPRES1R-26OCT04", "notes": []},
            # the whole-election winner traded heavily on first-round night too
            {"race_id": "br-final", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election", "k_event": "KXBRPRES-26",
             "aliases": {"Luiz Inácio Lula da Silva": "Luiz Inácio Lula da Silva"}, "notes": []},
        ]},
    "br2026r2": {
        "name": "Brazil presidential runoff", "date": "2026-10-25",
        "groups": [
            {"race_id": "br", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election", "k_event": "KXBRPRES-26", "notes": []},
            {"race_id": "<uf>", "kind": "br_states", "a": BR_A, "b": BR_B,
             "k_event": "KXBRRUNOFFSTATES-26OCT25-1", "notes": []},
            {"race_id": "sp", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election-second-round-1st-place-in-sao-paulo",
             "k_event": "KXBRRUNOFFSP-26OCT25-1", "notes": []},
            {"race_id": "abroad", "kind": "outcomes", "a": BR_A, "b": BR_B,
             "pm_event": "brazil-presidential-election-second-round-1st-place-abroad",
             "k_event": "KXBRRUNOFFABROAD-26OCT25-1", "notes": []},
        ]},
    "br2026r2gov": {
        "name": "Brazil governor runoffs", "date": "2026-10-25",
        "groups": [
            {"race_id": "gov-df", "kind": "outcomes", "a": "Celina Leão", "b": "Leandro Grass",
             "pm_event": "federal-district-governor-election-winner", "k_event": "KXDFGOV-26OCT25",
             "notes": ["Polymarket's event closed on 2026-10-05 (end date before the runoff); Kalshi's covered both rounds"]},
        ]},
    "il2026": {
        "name": "Israel Knesset election", "date": "2026-10-27",
        "groups": [
            {"race_id": "pm", "kind": "outcomes", "a": "Benjamin Netanyahu", "b": None,
             "pm_event": "who-will-be-the-next-prime-minister-of-israel-after-the-next-election",
             "k_event": "KXISRAELPM-26OCT27", "notes": []},
            {"race_id": "knesset", "kind": "outcomes", "a": "Likud", "b": None,
             "pm_event": "israeli-legislative-election-winner", "k_event": "KXISRAELKNESSET-26", "notes": []},
            {"race_id": "2nd", "kind": "outcomes", "a": "Likud", "b": None, "aliases": IL_ALIASES,
             "pm_event": "israeli-legislative-election-2nd-place", "k_event": "KXISRAEL2ND-26OCT27-2", "notes": []},
            {"race_id": "3rd", "kind": "outcomes", "a": "Likud", "b": None, "aliases": IL_ALIASES,
             "pm_event": "israeli-legislative-election-3rd-place", "k_event": "KXISRAEL3RD-26OCT27-3", "notes": []},
            {"race_id": "4th", "kind": "outcomes", "a": "Likud", "b": None, "aliases": IL_ALIASES,
             "pm_event": "israeli-legislative-election-4th-place", "k_event": "KXISRAEL4TH-26OCT27-4", "notes": []},
            {"race_id": "bloc", "kind": "explicit", "a": "Netanyahu bloc", "b": "Opposition bloc",
             "pm_event": "israel-election-which-bloc-wins-more-seats", "k_event": "KXISRAELBLOCMOST-26OCT27",
             # PM's one market: first token "Bibi" (Netanyahu bloc), second "Opp."
             "pairs": [("israel-election-which-bloc-wins-more-seats", "KXISRAELBLOCMOST-26OCT27-BLOCB", True)],
             "notes": ["Same bloc lists on both venues (Netanyahu bloc: Likud, Otzma Yehudit, RZP-Zehut, Amcha Yisrael, "
                       "UTJ, Shas; opposition: The Democrats, Yashar, Together, Yisrael Beiteinu); other lists count for neither",
                       "Seat ties: Polymarket breaks by the blocs' combined valid votes, then 50-50; Kalshi pays both "
                       "markets $0.50 — a tie broken on votes pays Polymarket 1/0 but Kalshi 0.5/0.5"]},
            {"race_id": "seat", "kind": "outcomes", "a": None, "b": None, "aliases": {"RZP-Zehut": "RZP-Zehut", "Amcha Yisrael": "People of Israel"},
             "pm_event": "which-parties-will-win-a-seat-in-the-2026-knesset-elections",
             "k_event": "KXISRAELANYSEAT-26OCT27", "notes": []},
        ]},
    "us2026": {
        "name": "US midterm elections", "date": "2026-11-03",
        "groups": [
            {"race_id": "house", "kind": "outcomes", "a": "Democratic Party", "b": "Republican Party",
             "pm_event": "which-party-will-win-the-house-in-2026", "k_event": "CONTROLH-2026", "notes": []},
            {"race_id": "senate", "kind": "outcomes", "a": "Democratic Party", "b": "Republican Party",
             "pm_event": "which-party-will-win-the-senate-in-2026", "k_event": "CONTROLS-2026", "notes": []},
            {"race_id": "ca-gov", "kind": "outcomes", "a": "Xavier Becerra", "b": "Steve Hilton",
             "pm_event": "california-governor-election-2026", "k_event": "KXGOVCA-26", "notes": []},
            {"race_id": "ca-gov", "kind": "outcomes", "a": "Xavier Becerra", "b": "Steve Hilton",
             "pm_event": "california-governor-election-2026", "k_event": "GOVPARTYCA-26", "notes": []},
            {"race_id": "tx-sen", "kind": "outcomes", "a": "James Talarico", "b": "Ken Paxton",
             "pm_event": "texas-senate-election-winner", "k_event": "SENATETX-26", "notes": []},
            {"race_id": "tx-gov", "kind": "outcomes", "a": "Gina Hinojosa", "b": "Greg Abbott",
             "pm_event": "texas-governor-winner-2026", "k_event": "GOVPARTYTX-26", "notes": []},
            {"race_id": "oh-sen", "kind": "outcomes", "a": "Sherrod Brown", "b": "Jon Husted",
             "pm_event": "ohio-senate-election-winner", "k_event": "SENATEOHS-26", "notes": []},
            {"race_id": "oh-gov", "kind": "outcomes", "a": "Amy Acton", "b": "Vivek Ramaswamy",
             "pm_event": "ohio-governor-winner-2026", "k_event": "GOVPARTYOH-26", "notes": []},
            {"race_id": "ga-sen", "kind": "outcomes", "a": "Jon Ossoff", "b": "Mike Collins",
             "pm_event": "georgia-senate-election-winner", "k_event": "SENATEGA-26", "notes": []},
            {"race_id": "ga-gov", "kind": "outcomes", "a": "Keisha Lance Bottoms", "b": "Rick Jackson",
             "pm_event": "georgia-governor-winner-2026", "k_event": "GOVPARTYGA-26", "notes": []},
            {"race_id": "ak-sen", "kind": "outcomes", "a": "Mary Peltola", "b": "Dan Sullivan",
             "pm_event": "alaska-senate-election-winner", "k_event": "KXAKSENATE-26NOV03", "notes": []},
            {"race_id": "ak-gov", "kind": "outcomes", "a": "Jonathan Kreiss-Tomkins", "b": "Bernadette Wilson",
             "aliases": {"David Bronson": "Dave Bronson", "Matt Claman": "Matthew Claman"},
             "pm_event": "alaska-governor-election-winner", "k_event": "KXGOVAK-26", "notes": []},
            {"race_id": "me-sen", "kind": "outcomes", "a": "Troy Jackson", "b": "Susan Collins",
             "pm_event": "maine-senate-election-winner", "k_event": "SENATEME-26", "notes": []},
            {"race_id": "me-gov", "kind": "outcomes", "a": "Hannah Pingree", "b": "Robert Charles",
             "pm_event": "maine-governor-winner-2026", "k_event": "GOVPARTYME-26", "notes": []},
            {"race_id": "mi-sen", "kind": "outcomes", "a": "Abdul El-Sayed", "b": "Mike Rogers",
             "pm_event": "michigan-senate-election-winner", "k_event": "SENATEMI-26", "notes": []},
            {"race_id": "mi-sen", "kind": "outcomes", "a": "Abdul El-Sayed", "b": "Mike Rogers",
             "pm_event": "michigan-senate-election-winner", "k_event": "KXMISENATE-26", "notes": []},
            {"race_id": "mi-gov", "kind": "outcomes", "a": "Jocelyn Benson", "b": "John James",
             "pm_event": "michigan-governor-winner-2026", "k_event": "GOVPARTYMI-26", "notes": []},
            {"race_id": "ia-sen", "kind": "outcomes", "a": "Josh Turek", "b": "Ashley Hinson",
             "pm_event": "iowa-senate-election-winner", "k_event": "SENATEIA-26", "notes": []},
            {"race_id": "ia-gov", "kind": "outcomes", "a": "Rob Sand", "b": "Zach Lahn",
             "pm_event": "iowa-governor-winner-2026", "k_event": "GOVPARTYIA-26", "notes": []},
            {"race_id": "ks-sen", "kind": "outcomes", "a": "Adam Hamilton", "b": "Roger Marshall",
             "pm_event": "kansas-senate-election-winner", "k_event": "SENATEKS-26", "notes": []},
            {"race_id": "ks-gov", "kind": "outcomes", "a": "Cindy Holscher", "b": "Ty Masterson",
             "pm_event": "kansas-governor-winner-2026", "k_event": "GOVPARTYKS-27", "notes": []},
        ]},
    "tw2026": {
        "name": "Taiwan local elections (Taipei, New Taipei mayors)", "date": "2026-11-28",
        "groups": [
            {"race_id": "taipei", "kind": "outcomes", "a": "Chiang Wan-an", "b": None,
             "pm_event": "taipei-mayor-election-winner-20260813125857100", "k_event": "KXTAIPEIMAYOR-26NOV28",
             "aliases": {"Puma Shen": "Shen Bo-yang"}, "notes": []},
            {"race_id": "new-taipei", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "new-taipei-mayor-election-winner-20260813125857200", "k_event": "KXNEWTAIPEIMAYOR-26NOV28",
             "notes": []},
        ]},
    "fr2027": {
        "name": "French presidential election", "date": "2027-04-11",
        "groups": [
            {"race_id": "fr", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "next-french-presidential-election", "k_event": "KXFRENCHPRES-27", "notes": []},
            {"race_id": "fr-advance", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "next-french-presidential-election-who-will-advance-to-the-2nd-round",
             "k_event": "KXFRPRESADVANCE-26APR18", "notes": []},
            {"race_id": "fr-r1-1st", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "french-presidential-election-first-round-winner", "k_event": "KXFRPRES1R1ST-27-1", "notes": []},
            {"race_id": "fr-r1-2nd", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "french-presidential-election-first-round-2nd-place", "k_event": "KXFRPRES1R2ND-27-2", "notes": []},
            {"race_id": "fr-r1-3rd", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "french-presidential-election-first-round-3rd-place", "k_event": "KXFRPRES1R3RD-27-3", "notes": []},
            {"race_id": "fr-ballot", "kind": "outcomes", "a": None, "b": None,
             "pm_event": "2027-french-presidential-election-who-will-be-on-the-ballot",
             "k_event": "KXFRPRESBALLOT-27JUN30", "notes": []},
            {"race_id": "fr-outright", "kind": "single", "a": "someone wins outright", "b": "runoff",
             "pm_event": "will-anyone-win-frances-presidential-election-in-the-first-round",
             "k_event": "KXFRENCHPRES1R-27APR18", "notes": []},
        ]},
    "es2026": {
        "name": "Spanish general election", "date": "2026-11-29",
        "groups": [
            {"race_id": "es", "kind": "outcomes", "a": "PP", "b": "PSOE",
             "pm_event": "spain-general-election-winner", "k_event": "KXSPAINPARLI-27",
             "aliases": {"PSOE": "Spanish Socialist Workers' Party", "PP": "People's Party"}, "notes": []},
            {"race_id": "es-2nd", "kind": "outcomes", "a": "PP", "b": "PSOE",
             "pm_event": "spain-general-election-2nd-place", "k_event": "KXSPAIN2ND-26NOV29-2",
             "aliases": {"PP": "People's Party"}, "notes": []},
            {"race_id": "es-3rd", "kind": "outcomes", "a": "PP", "b": "PSOE",
             "pm_event": "spain-general-election-3rd-place", "k_event": "KXSPAIN3RD-26NOV29-3",
             "aliases": {"PP": "People's Party"}, "notes": []},
            {"race_id": "es-pm", "kind": "outcomes", "a": "Alberto Núñez Feijóo", "b": "Pedro Sánchez",
             "pm_event": "next-prime-minister-of-spain-20260625005215443", "k_event": "KXSPANISHPM-27AUG22",
             "notes": []},
            {"race_id": "es-ppvox50", "kind": "single", "a": "PP + Vox ≥ 50% of vote", "b": "below",
             "pm_event": "spain-general-election-pp-vox-get-50-of-vote", "k_event": "KXVOTESPAIN-26NOV29PPVOX",
             "notes": []},
        ]},
}


# ---------------------------------------------------------------------------------------------- fetch

def pm_markets(slug: str) -> list[dict]:
    """Live-or-traded binary markets of a Polymarket event (placeholders without volume dropped)."""
    ev = polymarket.event(slug)
    out = []
    for m in ev["markets"]:
        if not m.get("clobTokenIds") or float(m.get("volume") or 0) == 0:
            continue
        toks, outs = json.loads(m["clobTokenIds"]), json.loads(m["outcomes"])
        fs = m.get("feeSchedule") or {}
        on = bool(m.get("feesEnabled"))
        out.append({"slug": m["slug"], "label": m.get("groupItemTitle") or m["question"], "question": m["question"],
                    "rules": m.get("description") or "", "yes_token": toks[outs.index("Yes")] if "Yes" in outs else toks[0],
                    "condition_id": m["conditionId"], "fee_rate": float(fs.get("rate") or 0) if on else 0.0,
                    "fee_exponent": float(fs.get("exponent") or 1), "closed": bool(m.get("closed")),
                    "end": m.get("endDate"), "event": slug, "event_title": ev.get("title", "")})
    return out


_series_cache: dict[str, dict] = {}


def k_markets(event_ticker: str) -> list[dict]:
    """Kalshi event markets with rule texts, the event's settlement sources and fee multiplier."""
    with _http.client() as c:
        d = kalshi._get(c, f"/events/{event_ticker}", {"with_nested_markets": "true"})
        ev = d["event"]
        s = ev["series_ticker"]
        if s not in _series_cache:
            _series_cache[s] = kalshi._get(c, f"/series/{s}")["series"]
    ser = _series_cache[s]
    mult = ev.get("fee_multiplier_override", ser.get("fee_multiplier", 1))
    sources = [x.get("name") for x in (ev.get("settlement_sources") or ser.get("settlement_sources") or [])]
    out = []
    for m in ev.get("markets") or d.get("markets") or []:
        rules = "\n\n".join(x for x in (m.get("rules_primary"), m.get("rules_secondary")) if x)
        out.append({"ticker": m["ticker"], "label": m.get("yes_sub_title") or m.get("title"), "title": m.get("title", ""),
                    "rules": rules, "early_close": m.get("early_close_condition") or "",
                    "latest_expiration": m.get("latest_expiration_time"), "status": m.get("status"),
                    "event": event_ticker, "series": s, "fee_multiplier": float(mult if mult is not None else 1),
                    "fee_type": ev.get("fee_type_override") or ser.get("fee_type"), "sources": sources,
                    "contract_terms_url": ser.get("contract_terms_url", "")})
    return out


# ---------------------------------------------------------------------------------------------- differences

def auto_diffs(p: dict, k: dict, same_side: bool) -> list[str]:
    """Resolution differences visible in the two rule texts."""
    pr, kr = p["rules"], f"{k['rules']}\n{k['early_close']}"
    out = []
    src = re.search(r"[^.\n]*(?:resolution source|consensus of|official (?:results|information))[^.\n]*\.", pr, re.I)
    pm_src = f"“{src.group(0).strip()}”" if src else "its description (no source sentence found)"
    out.append(f"Sources differ: Polymarket: {pm_src} Kalshi: its contract's Source Agencies "
               f"({', '.join(k['sources']) or 'see contract terms'}) under the {k['series']} contract terms.")
    m = re.search(r"(?:known|sworn in|elected|determined)[^.]*? by ([A-Z][a-z]+ \d{1,2}, \d{4}(?:, [\d:]+ ?[AP]M ET)?)", pr)
    if m:
        out.append(f"Deadlines differ: Polymarket's fallback date is {m.group(1)}; "
                   f"Kalshi's latest expiration is {k['latest_expiration']}.")
    if re.search(r"sworn in|inaugurated|swearing", kr, re.I) and not re.search(r"sworn in|inaugurat", pr, re.I):
        out.append("Kalshi pays on who is sworn in/inaugurated (or earlier on a media consensus); Polymarket on the "
                   "election result — a winner who is not seated (death, disqualification, contest) splits the legs.")
    elif re.search(r"sworn in|invested|appointed", kr, re.I) and re.search(r"sworn in", pr, re.I):
        out.append("Both pay on the office-holder actually sworn in, but with different deadlines and caretaker/interim "
                   "rules (Polymarket text vs the Kalshi market page).")
    if re.search(r"representative of the \w+ party|party identification", kr, re.I) and not re.search(r"\bparty\b", p["label"], re.I) \
            and not re.search(r"democrat|republican", p["slug"]):
        out.append("Kalshi resolves on the party; Polymarket on the named person — a replacement nominee splits the legs.")
    if re.search(r"democrat|republican", p["slug"]) and not re.search(r"\bparty\b", kr, re.I):
        out.append("Polymarket resolves on the party; Kalshi on the named person — a replacement nominee splits the legs.")
    if re.search(r"proportionally", kr, re.I):
        out.append("Ties: Kalshi splits exact ties proportionally (1/number tied); Polymarket's rule differs or is silent.")
    if re.search(r"certified", kr, re.I) and not re.search(r"certif", pr, re.I):
        out.append("Kalshi waits for certified results (or early close on certification); Polymarket can resolve on credible reporting.")
    if re.search(r"Speaker of the House on February 1", kr):
        out.append("Kalshi falls back to the Speaker's party on 2027-02-01; Polymarket on control 'after the midterms' per its text.")
    if not same_side:
        out.append("Opposite-side pair: assumes exactly two candidates and no tie/annulment (then both NO-equivalents can lose).")
    return out


# ---------------------------------------------------------------------------------------------- build

def yes_is(label: str, g: dict) -> str:
    if g.get("a") and arb.same_name(label, g["a"]):
        return "a"
    if g.get("b") and arb.same_name(label, g["b"]):
        return "b"
    return "other"


def _pair(eid: str, race: str, g: dict, p: dict, k: dict, same_side: bool, prop: str,
          pm_side: str, k_side: str) -> dict:
    return {
        "id": f"{race}:{p['slug'][:60]}~{k['ticker']}", "race_id": race, "proposition": prop,
        "pm_market": p["slug"], "kalshi_ticker": k["ticker"], "same_side": same_side,
        "pm_event": p["event"], "kalshi_event": k["event"], "kalshi_series": k["series"],
        "pm_question": p["question"],
        "pm_yes_token": p["yes_token"], "pm_condition_id": p["condition_id"],
        "pm_yes_is": pm_side, "kalshi_yes_is": k_side,
        "pm_fee_rate": p["fee_rate"], "pm_fee_exponent": p["fee_exponent"],
        "kalshi_fee_multiplier": k["fee_multiplier"], "kalshi_fee_type": k["fee_type"],
        "risk_free": False,
        "resolution_differences": auto_diffs(p, k, same_side) + list(g.get("notes") or []),
        "pm_rules": p["rules"],
        "kalshi_resolution": K_SAYS[k["series"]].format(x=k["label"]),
        "kalshi_url": f"https://kalshi.com/markets/{k['series'].lower()}",
        "kalshi_contract_terms": k["contract_terms_url"],
        "_race": {"a": g.get("a") or "", "b": g.get("b") or ""},      # → [races] table, not written per pair
    }


def build(eid: str) -> list[dict]:
    pairs = []
    for g in SPEC[eid]["groups"]:
        km = k_markets(g["k_event"])
        if g["kind"] == "br_states":
            cfg = tomllib.loads((ROOT / "configs" / "night_r2_v1.toml").read_text())["markets"]["states"]
            for uf, s in cfg.items():
                pms = pm_markets(s["event"])
                state = pms[0]["event_title"].split("1st Place in ")[-1] if pms else ""
                ks = [k for k in km if arb.norm(k["label"]) == arb.norm(state)]
                if not ks:
                    continue
                for p in pms:
                    if arb.same_name(p["label"], BR_B):
                        pairs.append(_pair(eid, uf, g, p, ks[0], True, f"{BR_B} wins {state} in the runoff", "b", "b"))
                    elif arb.same_name(p["label"], BR_A):       # Kalshi YES = Flávio wins the state
                        pairs.append(_pair(eid, uf, g, p, ks[0], False, f"{BR_A} wins {state} in the runoff", "a", "b"))
            continue
        pms = pm_markets(g["pm_event"])
        if g["kind"] in ("single", "explicit"):
            # single: each side's only market; explicit: g["pairs"] = [(pm slug, kalshi ticker, same_side)]
            want = g.get("pairs") or ([(pms[0]["slug"], km[0]["ticker"], True)] if len(pms) == len(km) == 1 else [])
            if not want:
                print(f"  {g['race_id']}: single pair expected, got {len(pms)} PM / {len(km)} Kalshi", file=sys.stderr)
            for slug, ticker, same in want:
                p = next(x for x in pms if x["slug"] == slug)
                k = next(x for x in km if x["ticker"] == ticker)
                pairs.append(_pair(eid, g["race_id"], g, p, k, same, p["question"], "a", "a" if same else "b"))
            continue
        matched = arb.match_outcomes([p["label"] for p in pms], [k["label"] for k in km], g.get("aliases"))
        for i, j in matched:
            p, k = pms[i], km[j]
            side = yes_is(p["label"], g)
            pairs.append(_pair(eid, g["race_id"], g, p, k, True, f"{p['label']} — {p['event_title']}", side, side))
        unmatched = [p["label"] for n, p in enumerate(pms) if n not in {i for i, _ in matched}]
        if unmatched:
            print(f"  {eid}/{g['race_id']}: PM outcomes without a Kalshi match: {unmatched[:12]}", file=sys.stderr)
    return pairs


# ---------------------------------------------------------------------------------------------- write

def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if v is None:
        return '""'
    if isinstance(v, list):
        return "[\n" + "".join(f"  {_toml(x)},\n" for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def write(eid: str, pairs: list[dict]) -> Path:
    s = SPEC[eid]
    lines = [f"# Polymarket ↔ Kalshi pairs for {s['name']} — generated by analysis/arb_pairs.py; edit SPEC there.",
             "# same_side: Kalshi YES ≡ Polymarket YES. yes_is: race side (a/b/other) of each venue's YES.",
             "# risk_free is false for every pair: see resolution_differences and docs/kalshi.md.",
             f"election = {_toml(eid)}", f"name = {_toml(s['name'])}", f"date = {_toml(s['date'])}",
             "", "[races]"]
    races = {}
    for p in pairs:
        races.setdefault(p["race_id"], p["_race"])
    for r, ab in races.items():
        lines.append(f"{_toml(r)} = {{ a = {_toml(ab['a'])}, b = {_toml(ab['b'])} }}")
    for p in pairs:
        lines += ["", "[[pair]]"] + [f"{k} = {_toml(v)}" for k, v in p.items() if k != "_race"]
    path = OUT / f"{eid}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    tomllib.loads(path.read_text())                      # must round-trip
    return path


def main(eids: list[str]) -> None:
    for eid in eids or list(SPEC):
        pairs = build(eid)
        print(f"{eid}: {len(pairs)} pairs → {write(eid, pairs)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
