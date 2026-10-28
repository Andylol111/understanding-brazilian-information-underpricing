"""Build data_public/us2026_races.json: every 2026 US Senate and governor race, for the national overview.

Sources (fetched once, politely, cached under $BRUNDER_DATA/raw/us/overview/):
- Senate seats, classes, current senators and parties: senate.gov contact XML
  (https://www.senate.gov/general/contact_information/senators_cfm.xml). Class II seats are up in 2026. The
  specials (Ohio and Florida, class III) come from senate.gov's list of appointed senators
  (https://www.senate.gov/senators/AppointedSenators.htm): Husted and Moody were appointed in January 2025, and
  their seats are filled at the 2026 election. Seats not up in 2026 give the holdover counts.
- Governors and their parties: National Governors Association, https://www.nga.org/governors/ and one page per
  state (robots.txt crawl delay of 10 s respected). The 36 states with 2026 governor elections are listed below.
- Markets: Polymarket Gamma events (`<state>-senate-election-winner`, `<state>-governor-winner-2026`, …) and
  Kalshi events (SENATE<ST>-26, GOVPARTY<ST>-26, …), public APIs.

Alignment ("dem_side", P = P(dem_side wins)): the Democratic party's market, except where the Democrat is not
competitive and the main non-Republican is an independent (Polymarket has no Democrat market priced ≥ 1% but has an
"independent" market): then the independent is the dem_side, flagged with "aligned": "independent".
Candidates are the market listings' Democratic / Republican (and independent) names; "open" = the incumbent is
not among them (None when the listing names parties only and no official ballot data was available).

    BRUNDER_DATA=… uv run python analysis/us_overview_races.py
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from brunder import kalshi, polymarket, us
from brunder.arb import load_pairs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data_public" / "us2026_races.json"
CACHE = us.US_RAW / "overview"
UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
NAMES = {"al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California", "co": "Colorado",
         "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
         "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana",
         "me": "Maine", "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota",
         "ms": "Mississippi", "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
         "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York", "nc": "North Carolina",
         "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania",
         "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas",
         "ut": "Utah", "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
         "wi": "Wisconsin", "wy": "Wyoming"}
GOV_2026 = "al ak az ar ca co ct fl ga hi id il ia ks me md ma mi mn ne nv nh nm ny oh ok or pa ri sc sd tn tx vt wi wy"
SPECIALS = {"oh": "Husted", "fl": "Moody"}           # appointed class III senators (senate.gov AppointedSenators)
PM_PATTERNS = {"senate": ["{s}-senate-election-winner", "{s}-senate-election-winner-154",
                          "{s}-senate-election-winner-2026"],
               "governor": ["{s}-governor-winner-2026", "{s}-governor-election-winner", "{s}-governor-election-2026"]}
KALSHI_EVENTS = {"senate": ["SENATE{ST}S-26", "SENATE{ST}-26"],
                 "governor": ["GOVPARTY{ST}-26", "KXGOVPARTY{ST}-26", "GOVPARTY{ST}-27", "GOVPARTY{ST}-28"]}
# official ballot data already in this project (certified 2026 primaries / feeds): incumbent on the ballot?
OPEN_OFFICIAL = {("ak", "senate"): False, ("ak", "governor"): True}
PARTY_NAMES = {"ak-sen": ("Mary Peltola", "Dan Sullivan"), "ak-gov": ("Jonathan Kreiss-Tomkins", "Bernadette Wilson")}


def _get(url: str, name: str, delay: float = 0.0) -> bytes:
    f = CACHE / name
    if f.exists():
        return f.read_bytes()
    time.sleep(delay)
    r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(r.content)
    return r.content


def _text(html: bytes) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", html.decode("utf-8", "ignore")))


def senators() -> list[dict]:
    root = ET.fromstring(_get("https://www.senate.gov/general/contact_information/senators_cfm.xml", "senators.xml"))
    return [{c.tag: (c.text or "").strip() for c in m} for m in root.findall("member")]


def governor(st: str) -> tuple[str, str]:
    slug = NAMES[st].lower().replace(" ", "-")
    t = _text(_get(f"https://www.nga.org/governors/{slug}/", f"nga_{st}.html", delay=10.0))
    name = re.search(r"Gov\. ([^|]+?) \|", t).group(1).strip()
    party = re.search(r"Party \| ([A-Za-z ]+?) \|", t).group(1).strip()
    return name, {"Republican": "R", "Democrat": "D", "Democratic": "D", "Independent": "I"}.get(party, party)


def pm_event(st: str, office: str) -> dict | None:
    for p in PM_PATTERNS[office]:
        try:
            return polymarket.event(p.format(s=NAMES[st].lower().replace(" ", "-")))
        except KeyError:
            time.sleep(0.2)
    return None


def _price(m: dict) -> float:
    try:
        return float(json.loads(m["outcomePrices"])[0])
    except Exception:
        return 0.0


def kalshi_event(st: str, office: str) -> dict | None:
    for p in KALSHI_EVENTS[office]:
        try:
            ev = kalshi.event(p.format(ST=st.upper()))
            if ev.get("markets"):
                return ev
        except Exception:
            pass
    return None


def main() -> None:
    pairs = {p.race_id: p for p in load_pairs(ROOT / "configs" / "pairs" / "us2026.toml")[1]}
    modeled = {us.load_config(p)["election"]["race_id"] for p in sorted((ROOT / "configs").glob("us2026_*_v1.toml"))}
    sens = senators()
    up = {m["state"].lower(): m for m in sens if m["class"] == "Class II"}
    up |= {m["state"].lower(): m for m in sens if m["state"].lower() in SPECIALS and m["last_name"] == SPECIALS[m["state"].lower()]}
    holdovers = {"D": 0, "R": 0, "I": 0}
    for m in sens:
        if m is not up.get(m["state"].lower()):
            holdovers[m["party"]] += 1
    races = []
    for office, states in (("senate", sorted(up)), ("governor", GOV_2026.split())):
        for st in states:
            key = f"{st}-{'sen' if office == 'senate' else 'gov'}"
            if office == "senate":
                m = up[st]
                inc, inc_party = f"{m['first_name']} {m['last_name']}", m["party"]
            else:
                inc, inc_party = governor(st)
            ev = pm_event(st, office)
            mk = [m for m in (ev or {}).get("markets", []) if not m.get("closed") and m.get("outcomePrices")]
            dem = next((m for m in mk if re.search(r"-democrats?-win", m["slug"])), None)
            ind = next((m for m in mk if "-an-independent-win" in m["slug"]), None)
            aligned = None
            if (dem is None or _price(dem) < 0.01) and ind is not None and _price(ind) >= 0.01:
                dem, aligned = ind, "independent"
            if dem is None and key in PARTY_NAMES:               # candidate-only events (Alaska)
                dem = next((m for m in mk if us._norm(PARTY_NAMES[key][0]).lower() in
                            us._norm(m["slug"].replace("-", " ")).lower()), None)
            if dem is None and key == "ca-gov":
                dem = next(m for m in mk if "becerra" in m["slug"])
            tok = None
            if dem is not None:
                ids = json.loads(dem["clobTokenIds"]) if isinstance(dem["clobTokenIds"], str) else dem["clobTokenIds"]
                tok = ids[0]
            kev = kalshi_event(st, office)
            cands, kt = {}, None
            if kev:
                for km in kev["markets"]:
                    side = km["ticker"].rsplit("-", 1)[1]
                    label = km.get("yes_sub_title") or km.get("subtitle") or ""
                    cands[side] = label
                if aligned == "independent":
                    kt = next((km["ticker"] for km in kev["markets"]
                               if km["ticker"].rsplit("-", 1)[1] not in ("D", "R")
                               and not km["ticker"].endswith("-D")), None)
                else:
                    kt = next((km["ticker"] for km in kev["markets"] if km["ticker"].endswith("-D")), None)
                series = kev.get("series_ticker") or kev["event_ticker"].split("-")[0]
            elif key in pairs:
                p = next(q for q in load_pairs(ROOT / "configs" / "pairs" / "us2026.toml")[1]
                         if q.race_id == key and q.kalshi_yes_is == "a")
                kt, series = p.kalshi_ticker, p.kalshi_series
            names = [v for v in cands.values() if v and "party" not in v.lower()]
            last = inc.split()[-1].lower()
            if (st, office) in OPEN_OFFICIAL:
                open_ = OPEN_OFFICIAL[(st, office)]
            elif names:
                open_ = not any(last in n.lower() for n in names)
            else:
                open_ = None
            dem_name = (cands.get("D") if aligned is None else
                        next((v for k, v in cands.items() if k not in ("D", "R")), None))
            if key in PARTY_NAMES:
                dem_name = PARTY_NAMES[key][0]
            races.append({
                "race_key": key, "state": st, "state_name": NAMES[st], "office": office,
                "special": office == "senate" and st in SPECIALS, "incumbent": inc, "incumbent_party": inc_party,
                "open": open_,
                "dem_side": {"name": dem_name, "party": "I" if aligned else "D",
                             "aligned": "independent: the main non-Republican candidate (no competitive Democrat)"
                             if aligned else None},
                "candidates": {k: v for k, v in cands.items() if v and "party" not in v.lower()} or None,
                "modeled": key in modeled, "race_id": key if key in modeled else None,
                "pm": {"event": ev["slug"], "slug": dem["slug"], "token_id": tok, "yes_is": "dem_side"}
                if dem is not None else None,
                "kalshi": {"ticker": kt, "series": series, "yes_is": "dem_side"} if kt else None,
            })
            print(key, inc, inc_party, open_, races[-1]["pm"] and races[-1]["pm"]["slug"], kt, aligned or "", flush=True)
    control = {}
    for chamber, ev_slug, kt in (("senate", "which-party-will-win-the-senate-in-2026", "CONTROLS-2026-D"),
                                 ("house", "which-party-will-win-the-house-in-2026", "CONTROLH-2026-D")):
        ev = polymarket.event(ev_slug)
        m = next(m for m in ev["markets"] if "democrat" in m["slug"] and not m.get("closed"))
        ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
        control[chamber] = [{"platform": "polymarket", "event": ev_slug, "slug": m["slug"], "token_id": ids[0],
                             "question": m["question"], "yes_is": "dem"},
                            {"platform": "kalshi", "ticker": kt, "series": kt.split("-")[0], "yes_is": "dem",
                             "question": f"Democrats win control of the {chamber} (Kalshi; our wording)"}]
    out = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "holdovers": {"senate": holdovers},
           "control_markets": control, "races": races,
           "sources": [
               {"name": "U.S. Senate, senators' contact information (classes, parties)",
                "url": "https://www.senate.gov/general/contact_information/senators_cfm.xml", "licence": "public domain"},
               {"name": "U.S. Senate, appointed senators (2026 special elections: Ohio, Florida)",
                "url": "https://www.senate.gov/senators/AppointedSenators.htm", "licence": "public domain"},
               {"name": "National Governors Association, current governors", "url": "https://www.nga.org/governors/",
                "licence": "facts cited"},
               {"name": "Polymarket (Gamma API) and Kalshi (public API) market listings: candidates, market ids",
                "url": "https://polymarket.com", "licence": "facts cited; Kalshi © Kalshi"}]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(OUT, len(races), "races", holdovers)


if __name__ == "__main__":
    main()
