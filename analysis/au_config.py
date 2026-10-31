"""Write configs/au2025_v1.toml: model settings (from the earlier-election calibration) and the market definitions.

Every market is a rule on the simulated final seat counts of Labor (ALP), the Coalition (LNC: Liberal, National, LNP,
CLP together), the Greens (GRN) and everyone else (OTH), or on the winner of one division. Markets that depend on
government formation after a hung result (minority, "other formation", the other prime-minister names) are NOT modelled.

    uv run python analysis/au_config.py results/australia/calibration_2025_v1.json    # -> configs/au2025_v1.toml
    uv run python analysis/au_config.py results/australia/calibration_2025_v2.json    # -> configs/au2025_v2.toml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from brunder.paths import ROOT

POLL_CLOSE = "2025-05-03T08:00:00Z"       # 18:00 AEST in the eastern states; 18:00 AWST (10:00 UTC) in Western Australia
DIV = {"Bennelong": 105, "Grayndler": 121, "Brisbane": 156, "Dickson": 252}

PMW = "australia-parliamentary-election-winner"
PMG = "next-government-of-australia"
PMM = "australia-election-of-seats-margin-of-victory"
PML = "of-seats-labor-win-in-australian-election"
PMC = "of-seats-liberalnational-coalition-wins-in-australian-election"
PMP = "next-prime-minister-of-australia"
KL, KC, KG = "KXAUSSEATSLABOR-25", "KXAUSSEATSLIBNAT-25", "KXAUSSEATSGREENS-25"

# race id -> (name, rule, [(platform, event, market)])
RACES: list[tuple] = [
    ("most-alp", "Labor wins the most seats", {"kind": "most", "party": "ALP"},
     [("polymarket", PMW, "will-labour-win-the-most-seats-in-the-next-australian-parliamentary-election"),
      ("kalshi", "KXAUSTRALIA", "KXAUSTRALIA-L")]),
    ("most-lnc", "The Coalition wins the most seats", {"kind": "most", "party": "LNC"},
     [("polymarket", PMW, "will-the-liberalnational-coalition-win-the-most-seats-in-the-next-australian-parliamentary-election"),
      ("kalshi", "KXAUSTRALIA", "KXAUSTRALIA-LNC")]),
    ("most-grn", "The Greens win the most seats", {"kind": "most", "party": "GRN"},
     [("polymarket", PMW, "will-the-greens-win-the-most-seats-in-the-next-australian-parliamentary-election"),
      ("kalshi", "KXAUSTRALIA", "KXAUSTRALIA-G")]),
    ("most-oth", "Another party or independent wins the most seats", {"kind": "most", "party": "OTH"},
     [("polymarket", PMW, "will-another-party-win-the-most-seats-in-the-next-australian-parliamentary-election")]),
    ("pm-albanese", "Albanese is the next prime minister (modelled as: Labor wins the most seats)",
     {"kind": "most", "party": "ALP"},
     [("polymarket", PMP, "will-anthony-albanese-be-the-next-prime-minister-of-australia-after-the-2025-election")]),
    ("pm-dutton", "Dutton is the next prime minister (modelled as: the Coalition wins the most seats)",
     {"kind": "most", "party": "LNC"},
     [("polymarket", PMP, "will-peter-dutton-be-the-next-prime-minister-of-australia-after-the-2025-election")]),
    ("alp-majority", "Labor wins a majority (76 or more seats)", {"kind": "majority", "party": "ALP"},
     [("polymarket", PMG, "will-the-next-government-of-australia-be-a-labor-majority")]),
    ("lnc-majority", "The Coalition wins a majority (76 or more seats)", {"kind": "majority", "party": "LNC"},
     [("polymarket", PMG, "will-the-next-government-of-australia-be-a-liberalnational-majority")]),
    ("grn-4plus", "The Greens win 4 or more seats", {"kind": "seats", "party": "GRN", "lo": 4, "hi": 150},
     [("polymarket", "will-the-patient-focus-pf-win-a-seat-in-norwegian-election-328",
       "will-the-patient-focus-pf-win-a-seat-in-norwegian-election-328"), ("kalshi", KG, f"{KG}-3")]),
    ("hung-parliament", "No party wins a majority (hung parliament)", {"kind": "hung"},
     [("polymarket", "australia-election-results-in-a-hung-parliament", "australia-election-results-in-a-hung-parliament")]),
]
# Labor seat bands: id, label, lo, hi, Polymarket slug, Kalshi ticker
for rid, lab, lo, hi, pm, ka in [
        ("alp-seats-u65", "Labor wins under 65 seats", 0, 64, "will-the-australian-labor-party-win-less-than-65-seats", f"{KL}-65"),
        ("alp-seats-65-69", "Labor wins 65 to 69 seats", 65, 69, "will-the-australian-labor-party-win-between-65-and-69-seats", f"{KL}-67.0"),
        ("alp-seats-70-74", "Labor wins 70 to 74 seats", 70, 74, "will-the-australian-labor-party-win-between-70-and-74-seats", f"{KL}-72.0"),
        ("alp-seats-75-79", "Labor wins 75 to 79 seats", 75, 79, "will-the-australian-labor-party-win-between-75-and-79-seats", f"{KL}-77.0"),
        ("alp-seats-80-84", "Labor wins 80 to 84 seats", 80, 84, "will-the-australian-labor-party-win-between-80-and-84-seats", f"{KL}-82.0"),
        ("alp-seats-85plus", "Labor wins 85 or more seats", 85, 150, "will-the-australian-labor-party-win-85-or-more-seats", f"{KL}-84")]:
    RACES.append((rid, lab, {"kind": "seats", "party": "ALP", "lo": lo, "hi": hi},
                  [("polymarket", PML, pm), ("kalshi", KL, ka)]))
for rid, lab, lo, hi, pm, ka in [
        ("lnc-seats-u45", "The Coalition wins under 45 seats", 0, 44, "will-the-liberalnational-coalition-win-less-than-45-seats", f"{KC}-45"),
        ("lnc-seats-45-49", "The Coalition wins 45 to 49 seats", 45, 49, "will-the-liberalnational-coalition-win-between-45-and-49-seats", f"{KC}-47.0"),
        ("lnc-seats-50-54", "The Coalition wins 50 to 54 seats", 50, 54, "will-the-liberalnational-coalition-win-between-50-and-54-seats", f"{KC}-52.0"),
        ("lnc-seats-55-59", "The Coalition wins 55 to 59 seats", 55, 59, "will-the-liberalnational-coalition-win-between-55-and-59-seats", f"{KC}-57.0"),
        ("lnc-seats-60-64", "The Coalition wins 60 to 64 seats", 60, 64, "will-the-liberalnational-coalition-win-between-60-and-64-seats", f"{KC}-62.0"),
        ("lnc-seats-65-69", "The Coalition wins 65 to 69 seats", 65, 69, "will-the-liberalnational-coalition-win-between-65-and-69-seats", f"{KC}-67.0"),
        ("lnc-seats-70-74", "The Coalition wins 70 to 74 seats", 70, 74, "will-the-liberalnational-coalition-win-between-70-and-74-seats", f"{KC}-72.0"),
        ("lnc-seats-75plus", "The Coalition wins 75 or more seats", 75, 150, "will-the-liberalnational-coalition-win-75-or-more-seats", f"{KC}-74")]:
    RACES.append((rid, lab, {"kind": "seats", "party": "LNC", "lo": lo, "hi": hi},
                  [("polymarket", PMC, pm), ("kalshi", KC, ka)]))
# margin of victory = Labor seats minus Coalition seats
for rid, lab, lo, hi, pm in [
        ("seatmargin-alp40", "Labor leads the Coalition by 40 or more seats", 40, 150, "will-the-labor-party-win-by-40-or-more-seats"),
        ("seatmargin-alp30", "Labor leads the Coalition by 30 to 39 seats", 30, 39, "will-the-labor-party-win-by-3039-seats"),
        ("seatmargin-alp20", "Labor leads the Coalition by 20 to 29 seats", 20, 29, "will-the-labor-party-win-by-2029-seats"),
        ("seatmargin-alp10", "Labor leads the Coalition by 10 to 19 seats", 10, 19, "will-the-labor-party-win-by-1019-seats"),
        ("seatmargin-alp1", "Labor leads the Coalition by 1 to 9 seats", 1, 9, "will-the-labor-party-win-by-19-seats"),
        ("seatmargin-lnc0", "The Coalition leads by 0 to 9 seats (or a tie)", -9, 0, "will-the-coalition-win-by-19-seats"),
        ("seatmargin-lnc10", "The Coalition leads by 10 to 19 seats", -19, -10, "will-the-coalition-win-by-1019-seats"),
        ("seatmargin-lnc20", "The Coalition leads by 20 to 29 seats", -29, -20, "will-the-coalition-win-by-2029-seats"),
        ("seatmargin-lnc30", "The Coalition leads by 30 or more seats", -150, -30, "will-the-coalition-win-by-30-or-more-seats")]:
    RACES.append((rid, lab, {"kind": "margin", "lo": lo, "hi": hi}, [("polymarket", PMM, pm)]))
RACES.append(("seatmargin-other", "Neither Labor nor the Coalition wins the most seats", {"kind": "most", "party": "OTH"},
              [("polymarket", PMM, "will-another-party-or-independent-win-the-most-seats")]))
# seat markets
RACES += [
    ("dutton-loses", "Dutton loses his seat (Dickson)", {"kind": "div", "div": DIV["Dickson"], "groups": ["ALP", "GRN", "OTH"]},
     [("polymarket", "will-peter-dutton-lose-his-seat", "will-peter-dutton-lose-his-seat"),
      ("kalshi", "KXAUSSEATDUTTON-25", "KXAUSSEATDUTTON-25")]),
    ("albanese-loses", "Albanese loses his seat (Grayndler)", {"kind": "div", "div": DIV["Grayndler"], "groups": ["LNC", "GRN", "OTH"]},
     [("polymarket", "will-anthony-albanese-lose-his-seat", "will-anthony-albanese-lose-his-seat"),
      ("kalshi", "KXAUSSEATALBANESE-25", "KXAUSSEATALBANESE-25")]),
    ("bennelong-alp", "Labor wins Bennelong", {"kind": "div", "div": DIV["Bennelong"], "groups": ["ALP"]},
     [("polymarket", "australia-election-seat-of-bennelong", "will-labor-win-the-seat-of-bennelong-in-the-2025-australian-election"),
      ("kalshi", "KXAUSSEATBENNELONG-25", "KXAUSSEATBENNELONG-25-LABOR")]),
    ("bennelong-lnc", "The Liberals win Bennelong", {"kind": "div", "div": DIV["Bennelong"], "groups": ["LNC"]},
     [("polymarket", "australia-election-seat-of-bennelong", "will-the-liberals-win-the-seat-of-bennelong-in-the-2025-australian-election"),
      ("kalshi", "KXAUSSEATBENNELONG-25", "KXAUSSEATBENNELONG-25-LIBERAL")]),
    ("brisbane-alp", "Labor wins Brisbane", {"kind": "div", "div": DIV["Brisbane"], "groups": ["ALP"]},
     [("polymarket", "australia-election-seat-of-brisbane", "will-labor-win-the-seat-of-brisbane-in-the-2025-australian-election"),
      ("kalshi", "KXAUSSEATBRISBANE-25", "KXAUSSEATBRISBANE-25-LABOR")]),
    ("brisbane-grn", "The Greens win Brisbane", {"kind": "div", "div": DIV["Brisbane"], "groups": ["GRN"]},
     [("polymarket", "australia-election-seat-of-brisbane", "will-the-greens-win-the-seat-of-brisbane-in-the-2025-australian-election"),
      ("kalshi", "KXAUSSEATBRISBANE-25", "KXAUSSEATBRISBANE-25-GREENS")]),
    ("brisbane-lnc", "The LNP wins Brisbane", {"kind": "div", "div": DIV["Brisbane"], "groups": ["LNC"]},
     [("polymarket", "australia-election-seat-of-brisbane", "will-the-liberal-national-party-win-the-seat-of-brisbane-in-the-2025-australian-election"),
      ("kalshi", "KXAUSSEATBRISBANE-25", "KXAUSSEATBRISBANE-25-LIBERALNATIONAL")]),
]


def toml_val(v) -> str:
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    return repr(v)


def main(calib: str) -> None:
    c = json.loads(Path(calib).read_text())
    p = c["settings"]
    v = c.get("variant", 1)
    why = ("headline: no setting or rule was changed after the 2025 count had been scored with these settings (a smoke test "
           "with untuned default settings came first)"
           if v == 1 else
           "SENSITIVITY (not the headline): adds floors under the state and national swing errors and compares a third candidate "
           "with the leading major party. Written AFTER the v1 run had been scored on the 2025 count and its misses inspected, "
           "so it is tuned with knowledge of the 2025 result; settings are still fitted only on the earlier nights")
    head = f"""# Australia 2025 House of Representatives election replay (RETROSPECTIVE, NOT PRE-REGISTERED: written after the count
# night with the result and the market resolutions known to the author). {why}.
# Settings come only from the count nights of {', '.join(map(str, c['fitted_on']))} replayed in feed order
# (analysis/au_calibrate.py{' --variant 2' if v == 2 else ''} -> {Path(sys.argv[1]).name}); no 2025 count went into them.
# s_nat0 = root-mean-square national two-party swing of 2016, 2019 and 2022 (preload notional vs final).
id = "au2025_v{v}"
election = "Australia, House of Representatives, 2025-05-03; polls closed 08:00 UTC (18:00 AEST), Western Australia 10:00 UTC"
poll_close_utc = "{POLL_CLOSE}"
night_since = "20250503165000"
night_until = "20250504040000"
night_end_utc = "2025-05-03T18:00:00Z"            # 04:00 AEST
draws = 8000
seed = 20250503

[model]                      # brunder.australia.project / simulate
""" + "\n".join(f"{k} = {toml_val(val)}" for k, val in p.items()) + "\n\n[prices]\nstart_utc = \"2025-05-03T04:00:00Z\"\nend_utc = \"2025-05-04T04:00:00Z\"\n"
    body = []
    for rid, name, rule, mks in RACES:
        for plat, ev, mk in mks:
            body.append("\n[[markets]]\n" + f"race = {toml_val(rid)}\nname = {toml_val(name)}\nplatform = {toml_val(plat)}\n"
                        f"event = {toml_val(ev)}\nmarket = {toml_val(mk)}\n" +
                        "".join(f"{k} = {toml_val(v)}\n" for k, v in rule.items()))
    out = ROOT / "configs" / f"au2025_v{v}.toml"
    out.write_text(head + "".join(body))
    print(out, len(RACES), "races", sum(len(r[3]) for r in RACES), "markets")


if __name__ == "__main__":
    main(sys.argv[1])
