"""State list for the midterms page (site/midterms.html): every state and DC, with its statewide races and how close the
markets say each is, from site/data/us2026/overview.json (read only).

Competitiveness = the statewide race (Senate or governor) whose market chance is closest to 50%; the chance is the mean of the
Polymarket and Kalshi prices for the Democratic side (or the aligned independent). The same cut-offs apply to every state.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERVIEW = ROOT / "site" / "data" / "us2026" / "overview.json"

NAMES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "dc": "District of Columbia", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho", "il": "Illinois",
    "in": "Indiana", "ia": "Iowa", "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri", "mt": "Montana",
    "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania",
    "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
}
# (id, label, low, high) on the Democratic-side chance; the first match wins
TIERS = [("tossup", "Toss-up", 0.40, 0.60), ("lean_d", "Leans Democratic", 0.60, 0.75), ("lean_r", "Leans Republican", 0.25, 0.40),
         ("far", "Not close", 0.0, 1.0)]


def _price(race: dict) -> float | None:
    ps = [race[k]["price"] for k in ("pm", "kalshi") if race.get(k) and race[k].get("price") is not None
          and race[k].get("yes_is", "dem_side") in ("dem_side", "dem")]
    return round(sum(ps) / len(ps), 3) if ps else None


def tier_of(p: float | None) -> str:
    if p is None:
        return "none"
    for tid, _, lo, hi in TIERS:
        if lo <= p < hi or (tid == "tossup" and p == 0.60):
            return tid
    return "far"


def build() -> dict:
    o = json.loads(OVERVIEW.read_text())
    by: dict[str, list[dict]] = {}
    for off in ("senate", "governor"):
        for r in o["offices"][off]["races"]:
            p = _price(r)
            if p is None:
                continue
            by.setdefault(r["state"], []).append({"key": r["race_key"], "office": off, "special": bool(r.get("special")), "p": p,
                                                  "side": (r.get("dem_side") or {}).get("name"), "party": (r.get("dem_side") or {}).get("party")})
    states = []
    for st, name in sorted(NAMES.items(), key=lambda kv: kv[1]):
        races = sorted(by.get(st, []), key=lambda x: abs(x["p"] - 0.5))
        states.append({"st": st, "name": name, "tier": tier_of(races[0]["p"]) if races else "none", "races": races})
    return {"as_of": o["as_of"], "tiers": [{"id": t, "label": l, "lo": lo, "hi": hi} for t, l, lo, hi in TIERS] + [{"id": "none", "label": "No statewide race", "lo": None, "hi": None}],
            "states": states,
            "source": "Polymarket and Kalshi prices (mean), from the election page's data"}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1)[:600])
