"""Canada 2025 federal election (2025-04-28) and Quebec 2026 general election (2026-10-05) -> dashboard files
under site/data/ca2025/ and site/data/qc2026/, and the maps site/data/geo/ca_districts.topo.json and
site/data/geo/ca_qc_circonscriptions.topo.json. Sources, licences and limits: docs/canada.md.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/ca_export.py [ca2025] [qc2026]

Prices only on the timeline (meta.count_timeline = false): Elections Canada publishes no count over
time. The map is one final frame (official results, Table 12) against Elections Canada's transposition
of the 2021 vote onto the 2023 districts. The only official over-time evidence is a handful of
Internet Archive captures of Elections Canada's election-night national page, each carrying the page's
own "Last updated at" stamp; they are used for the aggregate market-behaviour note only.

Downloads are cached under $BRUNDER_DATA/raw/canada (about 30 MB, never committed). Geometry needs Node
(`npx mapshaper`, pinned as in analysis/geo_build.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from brunder import canada as ca
from brunder import polymarket as pm
from brunder.paths import RAW

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "data" / "ca2025"
GEO = ROOT / "site" / "data" / "geo" / "ca_districts.topo.json"
CACHE = RAW / "canada"
MAPSHAPER = ["npx", "-y", "mapshaper@0.6.121"]
UA = "brunder-research/1.0 (+https://github.com/Andylol111/understanding-brazilian-information-underpricing)"
MAX_BYTES = 3_000_000

TABLE12 = "https://www.elections.ca/res/rep/off/ovrGE45/62/data_donnees/table_tableau12.csv"
OVR = "https://www.elections.ca/res/rep/off/ovrGE45/home.html"
TRANSPOSITION = ("https://open.canada.ca/data/dataset/08c4ffbc-c513-41a4-b741-d35b41b50c22/resource/"
                 "463aa010-d9c1-470e-b6e3-d18a1c7a6a4a/download/transposition_343_fed_vote_count.csv")
TRANSPOSITION_PAGE = "https://open.canada.ca/data/en/dataset/08c4ffbc-c513-41a4-b741-d35b41b50c22"
BOUNDARIES = "https://www.elections.ca/res/cir/mapsCorner/vector/FederalElectoralDistricts_2025_SHP.zip"
BOUNDARIES_PAGE = "https://open.canada.ca/data/en/dataset/97a2a33c-54cc-4f2e-82c1-047ad8212f05"
EC_TERMS = "https://www.elections.ca/content.aspx?section=pri&document=index&lang=e"
OGL = "https://open.canada.ca/en/open-government-licence-canada"
ENR = "https://enr.elections.ca/National.aspx?lang=e"
# Internet Archive captures of ENR's national page from first poll close (23:00 UTC) to the end of the
# count; listed from the CDX index (web.archive.org/cdx/search/cdx?url=enr.elections.ca/National.aspx*).
ENR_CAPTURES = ["20250428233006", "20250429002314", "20250429044606", "20250429065559", "20250429082933",
                "20250429102707"]

START = pd.Timestamp("2025-04-28T22:00:00Z")   # 18:00 ET
END = pd.Timestamp("2025-04-29T16:00:00Z")     # 12:00 ET
FIRST_CLOSE = "2025-04-28T23:00:00Z"           # Newfoundland, 20:30 NDT
LAST_CLOSE = "2025-04-29T02:00:00Z"            # British Columbia/Yukon, 19:00 PDT
SETTLE = 0.9
NAV, OPP = "#c8102e", "#1a4782"                # Liberal red, Conservative blue
PARTY_NAME = {"LPC": "Liberal", "CPC": "Conservative", "NDP": "NDP", "BQ": "Bloc Québécois", "GPC": "Green",
              "PPC": "People's Party", "OTH": "Other"}
REASON = "no official count over time is available"


def fetch(url: str, name: str) -> Path:
    p = CACHE / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            p.write_bytes(r.read())
        time.sleep(1)
    return p


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------ geometry
def build_geo() -> None:
    z = fetch(BOUNDARIES, "FederalElectoralDistricts_2025_SHP.zip")
    d = CACHE / "fed2025"
    if not d.exists():
        zipfile.ZipFile(z).extractall(d)
    prov = json.dumps({str(k): v for k, v in ca.PROV.items()})
    topo(next(d.rglob("FED_CA_2025_EN.shp")), GEO,
         f"unit_id='ca-'+({prov})[String(Math.floor(FED_NUM/1000))]+'-'+FED_NUM, name=ED_NAMEE", "districts", 343,
         "1.5%", proj=True)


# ------------------------------------------------------------------ Polymarket
def prices(m: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = CACHE / "polymarket" / f"{m['slug']}.json"
    if not p.exists():
        df = pm.price_history(m["yes_token"], start, end, fidelity_min=1, chunk=pd.Timedelta(hours=6))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(df.assign(time=df["time"].map(iso)).to_json(orient="records"))
        time.sleep(0.5)
    df = pd.read_json(p, dtype=False)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.floor("min")
    return df.groupby("time")["price"].last()


def settle_time(series: pd.Series, yes_won: bool, start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp | None:
    """First minute from which P(eventual outcome) stays >= 0.9 to the end of the window (forward-filled)."""
    s = series.reindex(pd.date_range(start, end, freq="min")).ffill()
    q = s if yes_won else 1 - s
    bad = q[~(q >= SETTLE)]
    if len(bad) == 0:
        return start
    last_bad = bad.index[-1]
    return None if last_bad >= q.index[-1] else last_bad + pd.Timedelta(minutes=1)


def market_block(spec: list[dict], start: pd.Timestamp, end: pd.Timestamp, color: dict) -> tuple[list, dict, dict]:
    """Races (A = YES), price-only timeline rows and Polymarket settle times for a market list."""
    for ev in sorted({m["event"] for m in spec}):
        mk = pm.markets(pm.event(ev)).set_index("slug")
        for m in spec:
            if m["event"] == ev:
                r = mk.loc[m["slug"]]
                m.update(question=r["question"], yes_token=r["yes_token"], yes_won=r["last_prices"][0] == 1.0)
    races, timeline, settle = [], {}, {}
    for m in spec:
        races.append({"race_id": m["race_id"], "name": m["name"], "region": m["region"],
                      "a": {"id": "yes", "name": m["a"], "color": color.get(m["a"], color["default"])},
                      "b": {"id": "no", "name": m["b"], "color": "#8a8a8a"},
                      "markets": [{"platform": "polymarket", "id": m["slug"], "question": m["question"], "yes_is": "a"}]})
        s = prices(m, start, end)
        keep = (s.round(4) != s.round(4).shift()) | (s.index == s.index[-1])   # rows only where the price moves
        timeline[m["race_id"]] = [{"t": iso(t), "counted": None, "raw": None, "share": None, "sd": None, "P": None,
                                   "pm": round(float(p), 4), "kalshi": None} for t, p in s[keep].items()]
        st = settle_time(s, m["yes_won"], start, end)
        settle[m["race_id"]] = {"model": None, "polymarket": iso(st), "kalshi": None, "raw": None,
                                "outcome": "yes" if m["yes_won"] else "no"}
    return races, timeline, settle


def null_stats(settle: dict) -> dict:
    """Standard stats keys with everything that needs a count over time null, plus the reasons."""
    return {
        "calibration": {r: None for r in settle}, "settle": settle,
        "catchup": {"polymarket": None, "kalshi": None},
        "early_error": {r: None for r in settle},
        "capacity": {"polymarket": None, "kalshi": None}, "arbitrage": None,
        "accuracy": None, "profit": None,
        "evidence": {"settle": "market-only"},
        "evidence_note": ("market-only: no count model was run (no official count over time exists); market prices "
                          "and official final results only. The 0.9 settle rule is descriptive, chosen after the fact."),
        "reasons": {
            "calibration": REASON, "early_error": REASON, "settle.model": REASON, "settle.raw": REASON,
            "catchup.polymarket": REASON + " (catch-up is measured against the model's settle time)",
            "accuracy": REASON, "profit": REASON + " (no model signal to trade on)",
            "capacity": REASON, "settle.kalshi": "no Kalshi data (project rule)",
            "catchup.kalshi": "no Kalshi data (project rule)", "arbitrage": "no Kalshi data (project rule)",
            "timeline": "count fields are null: " + REASON,
        },
    }


def topo(src: Path, out: Path, each: str, layer: str, n: int, pct: str, proj: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*MAPSHAPER, "-i", str(src), *(["-proj", "wgs84"] if proj else []), "-each", each,
                    "-filter-fields", "unit_id,name", "-simplify", pct, "keep-shapes", "-rename-layers", layer,
                    "-o", "format=topojson", "quantization=100000", str(out)], check=True, stdout=subprocess.DEVNULL)
    t = json.loads(out.read_text())
    ids = {g["properties"]["unit_id"] for g in t["objects"][layer]["geometries"]}
    assert out.stat().st_size < MAX_BYTES, out.stat().st_size
    assert len(ids) == n, len(ids)


# ------------------------------------------------------------------ official snapshots
def enr_snapshots() -> list[dict]:
    out = []
    for ts in ENR_CAPTURES:
        page = fetch(f"https://web.archive.org/web/{ts}id_/{ENR}", f"enr_national_{ts}.html")
        cap = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        s = ca.parse_enr_national(page.read_text(encoding="utf-8", errors="replace"), cap)
        out.append({"capture": iso(cap), "updated": iso(s["updated"]), "polls": s["polls"], "seats": s["seats"],
                    "votes": s["votes"], "archive": f"https://web.archive.org/web/{ts}/{ENR}"})
    return out


def canada_main() -> None:
    # ---- official results
    v25, names = ca.read_table12(fetch(TABLE12, "table_tableau12.csv"))
    v21 = ca.read_transposition(fetch(TRANSPOSITION, "transposition_343_fed_vote_count.csv"))
    feds = sorted(v25.index)
    assert len(feds) == 343 and set(feds) == set(v21.index)
    v21 = v21.loc[feds]
    v25 = v25.loc[feds]

    def two_way(v):
        tw = v["LPC"] + v["CPC"]
        return (v["LPC"] / tw.where(tw > 0)), tw

    a25, _ = two_way(v25)
    a21, tw21 = two_way(v21)
    tot25 = v25.sum(axis=1)
    ranked = v25.apply(lambda r: r.sort_values(ascending=False).index.tolist(), axis=1)
    winner = ranked.str[0]
    margin = [(v25.at[f, ranked[f][0]] - v25.at[f, ranked[f][1]]) / tot25[f] for f in feds]
    seats = winner.value_counts().to_dict()
    prov_of = {f: ca.PROV[f // 1000] for f in feds}
    seats_by_prov = pd.crosstab(pd.Series(prov_of), winner)

    i1000 = lambda x: -1 if pd.isna(x) else int(round(1000 * x))
    units = {
        "unit_ids": [ca.unit_id(f) for f in feds], "region": [prov_of[f] for f in feds],
        "names": [names[f] for f in feds],
        "prior": [i1000(x) for x in a21], "V0": [int(x) for x in tw21],
        "frames": [{"t": iso(END), "f": [1000] * len(feds), "a": [i1000(x) for x in a25],
                    "proj": [i1000(x) for x in a25],
                    "winner": winner.tolist(), "margin": [i1000(x) for x in margin],
                    "swing": [-1 if pd.isna(x) or pd.isna(y) else int(round(1000 * (x - y))) for x, y in zip(a25, a21)]}],
    }

    # ---- markets
    races, timeline, settle = market_block(ca.federal_markets(), START, END,
                                           {"Conservative": OPP, "Bloc Québécois": "#33b2cc", "default": NAV})

    # ---- market behaviour vs Elections Canada's own (archived) national page
    snaps = enr_snapshots()
    # official comparison only once every district has polls closed (earlier captures cover the east only)
    after_close = [s for s in snaps if s["updated"] and s["updated"] >= LAST_CLOSE]
    lead = [s for s in after_close if s["seats"] and max(s["seats"], key=s["seats"].get) == "LPC"]
    # first snapshot from which every later snapshot also shows the Liberals leading
    first_lpc = None
    for i, s in enumerate(after_close):
        if all(x in lead for x in after_close[i:]):
            first_lpc = s
            break
    behaviour = {
        "settle_threshold": SETTLE,
        "polymarket_settle": {r: v["polymarket"] for r, v in settle.items()},
        "official_snapshots": snaps,
        "official_first_lpc_lead": first_lpc and {"updated": first_lpc["updated"], "polls": first_lpc["polls"],
                                                  "seats": first_lpc["seats"]},
        "settled_at_open": [r for r, v in settle.items() if v["polymarket"] == iso(START)],
        "note": ("Official timestamps are Elections Canada's own 'Last updated at' stamps on its election-night "
                 "national page, read from the few Internet Archive captures made that night; they bound when "
                 "the official page showed a state, not when it first did. Province pages have one capture "
                 "inside the count, so no provincial comparison is made."),
    }

    stats = null_stats(settle)
    stats["market_behaviour"] = behaviour
    stats["outcome"] = {"seats": {PARTY_NAME[k]: int(v) for k, v in seats.items()},
                        "seats_by_province": {p: {PARTY_NAME[k]: int(n) for k, n in row.items() if n}
                                              for p, row in seats_by_prov.iterrows()},
                        "votes_share_pct": {PARTY_NAME[k]: round(100 * v25[k].sum() / tot25.sum(), 2)
                                            for k in ca.PARTIES}}

    sources = [
        {"name": "Elections Canada, Forty-Fifth General Election 2025: Official Voting Results, Table 12 (candidates "
                 "by electoral district), raw data", "url": TABLE12,
         "licence": f"Elections Canada non-commercial reproduction terms ({EC_TERMS}); earlier general elections' "
                    "official results are published on open.canada.ca under the Open Government Licence - Canada"},
        {"name": "Elections Canada, Transposition of the Vote from the 44th General Election to the 2023 Representation "
                 "Orders (transposition_343_fed_vote_count.csv)", "url": TRANSPOSITION_PAGE,
         "licence": f"Open Government Licence - Canada ({OGL})"},
        {"name": "Elections Canada, Electoral Geography Boundary Files (45th General Election), Federal Electoral "
                 "Districts 2025 (SHP), simplified", "url": BOUNDARIES_PAGE,
         "licence": f"Open Government Licence - Canada ({OGL})"},
        {"name": "Elections Canada election-night results page (National.aspx), Internet Archive captures",
         "url": "https://web.archive.org/web/2025*/" + ENR,
         "licence": f"Elections Canada non-commercial reproduction terms ({EC_TERMS})"},
        {"name": "Polymarket price history (CLOB prices-history, 1-minute) and market questions (Gamma API)",
         "url": "https://polymarket.com/event/next-prime-minster-of-canada", "licence": "Polymarket public data"},
    ]
    lpc_settle = settle["most-lpc"]["polymarket"]
    notes = [
        "Prices only: Elections Canada publishes no count over time (its election-night site keeps only the latest "
        "state), so there is no count timeline and no model; accuracy and profit cannot be measured.",
        f"Prices: Polymarket 1-minute price history (YES token), {iso(START)} to {iso(END)} (18:00 to 12:00 ET); a "
        "row is written only when the price changes (plus the last minute). Settled = price of the eventual outcome "
        f">= {SETTLE} from then to the end of the window.",
        f"Polls closed from {FIRST_CLOSE} (Newfoundland) to {LAST_CLOSE} (British Columbia, Yukon).",
        "Races: each Polymarket question is its own two-way race, A = YES. Provinces: the 'most seats' markets for "
        "the Liberals and their main rival (Conservatives; Bloc Québécois in Quebec). Margin events: the winning band "
        "only.",
        "Map: one final frame (its time is the end of the price window; the official results were published later). "
        "Colour = Liberal share of the Liberal + Conservative vote in the official 2025 results; "
        "prior = the same share in Elections Canada's transposition of the 2021 vote onto the 2023 districts. "
        "Frame extras: 'winner' (party code), 'margin' (winner minus runner-up, share of valid votes x1000), 'swing' "
        "(change in Liberal two-way share x1000).",
        f"Result: Liberals {seats.get('LPC', 0)} seats, Conservatives {seats.get('CPC', 0)}, Bloc "
        f"{seats.get('BQ', 0)}, NDP {seats.get('NDP', 0)}, Green {seats.get('GPC', 0)} (Official Voting Results, "
        "Table 12).",
    ]
    notes.append("Already settled when the price window opened (18:00 ET, before polls closed): "
                 + ", ".join(behaviour["settled_at_open"]) + ".")
    unsettled = [f"{r} (YES at {timeline[r][-1]['pm']:.2f} at 12:00 ET)" for r, v in settle.items() if not v["polymarket"]]
    if unsettled:
        notes.append("Not settled by the end of the window: " + ", ".join(unsettled) + ".")
    if first_lpc:
        no_capture = [s["updated"] for s in snaps if s["updated"] and s["updated"] < LAST_CLOSE][-1]
        notes.append(
            f"Official timing, coarse: the first archived capture of Elections Canada's national page after all polls "
            f"closed is stamped 'Last updated' {first_lpc['updated']} ({first_lpc['polls'][0]:,} of "
            f"{first_lpc['polls'][1]:,} polls reporting, Liberals {first_lpc['seats'].get('LPC')} leading or elected, "
            f"Conservatives {first_lpc['seats'].get('CPC')}); the one before it is stamped {no_capture}. Polymarket's "
            f"'Liberals win the most seats' market settled at {lpc_settle}. Captures are hours apart, so this bounds "
            "when the official page showed the result; it does not date it.")
    meta = {
        "id": "ca2025", "name": "Canada, 45th federal general election", "date": "2025-04-28",
        "tz": "America/Toronto", "poll_close_utc": LAST_CLOSE,
        "geometry": "geo/ca_districts.topo.json", "geometry_object": "districts", "unit_key": "unit_id",
        "count_timeline": False,
        "count_note": "Prices only: Elections Canada publishes no count over time; the map shows the official final "
                      "results.",
        "regions": [{"id": p, "name": ca.PROV_NAME[p]} for p in ca.PROV.values()],
        "races": races,
        "map": {"prior": "Liberal share of Liberal + Conservative votes, 2021 transposed to 2023 districts (official)",
                "final": "official 2025 results (Table 12)"},
        "sources": sources, "notes": notes,
    }
    dump(OUT / "meta.json", meta)
    dump(OUT / "timeline.json", timeline)
    dump(OUT / "units.json", units)
    dump(OUT / "stats.json", stats)
    build_geo()

    summary = (f"Prices only (no official count over time exists): Polymarket's 'Liberals win the most seats' market "
               f"settled at {lpc_settle}")
    if first_lpc:
        summary += (f"; the first archived Elections Canada page after polls closed is stamped "
                    f"{first_lpc['updated'][11:16]} UTC and showed the Liberals leading")
    entry = {"id": "ca2025", "name": "Canada federal election", "date": "2025-04-28", "country": "CA",
             "status": "replay", "unit_level": "electoral district", "n_units": len(feds),
             "evidence": "market-only", "summary": summary + "."}
    (OUT / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    print(f"ca2025: {len(races)} races, {len(feds)} districts, geometry {GEO.stat().st_size / 1e6:.2f} MB; "
          f"seats {seats}", file=sys.stderr)


# ================================================================== Quebec 2026
QC_OUT = ROOT / "site" / "data" / "qc2026"
QC_GEO = ROOT / "site" / "data" / "geo" / "ca_qc_circonscriptions.topo.json"
QC_FINAL = "https://donnees.electionsquebec.qc.ca/production/provincial/resultats/archives/gen2026-10-05/resultats.json"
QC_LIVE = "https://donnees.electionsquebec.qc.ca/production/provincial/resultats/resultats.json"
QC_BOUNDARIES = "https://donnees.electionsquebec.qc.ca/autres/provincial/circonscriptions_electorales_sans_eau_2026.json"
QC_DATA_PAGE = "https://www.dgeq.org/donnees.html"
QC_LICENCE = "https://www.dgeq.org/licence.html"
QC_MENTION = ("Comprend des données ouvertes octroyées sous la licence d'utilisation des données ouvertes du directeur "
              "général des élections disponible à l'adresse Web dgeq.org. L'octroi de la licence n'implique aucune "
              "approbation par le directeur général des élections de l'utilisation des données ouvertes qui en est faite.")
# Internet Archive captures of the live results file on election night (CDX index of QC_LIVE)
QC_CAPTURES = ["20261006000043", "20261006013233", "20261006123508"]
QC_START = pd.Timestamp("2026-10-05T22:00:00Z")   # 18:00 EDT
QC_END = pd.Timestamp("2026-10-06T16:00:00Z")     # 12:00 EDT
QC_CLOSE = "2026-10-06T00:00:00Z"                 # 20:00 EDT
QC_COLOR = {"Parti Québécois": "#004c9d", "Quebec Liberal Party": "#d71920", "Coalition Avenir Québec": "#00a7e1",
            "Conservative Party of Quebec": "#1a4782", "default": "#004c9d"}
QC_NAME = {"PQ": "Parti Québécois", "PLQ": "Quebec Liberal Party", "CAQ": "Coalition Avenir Québec",
           "PCQ": "Conservative Party of Quebec", "QS": "Québec solidaire", "PVQ": "Green Party of Quebec",
           "OTH": "Other"}


def quebec_main() -> None:
    fin = ca.parse_dgeq(fetch(QC_FINAL, "quebec/resultats_gen2026-10-05.json").read_bytes())
    assert fin["final"]
    v = fin["votes"]
    nums = sorted(v.index)
    assert len(nums) == 127
    tot = v.sum(axis=1)
    tw = v["PQ"] + v["PLQ"]
    a = v["PQ"] / tw.where(tw > 0)
    ranked = v.apply(lambda r: r.sort_values(ascending=False).index.tolist(), axis=1)
    winner = ranked.str[0]
    margin = [(v.at[n, ranked[n][0]] - v.at[n, ranked[n][1]]) / tot[n] for n in nums]
    seats = winner.value_counts().to_dict()
    i1000 = lambda x: -1 if pd.isna(x) else int(round(1000 * x))
    units = {"unit_ids": [ca.qc_unit_id(n) for n in nums], "region": ["qc"] * len(nums),
             "names": [fin["names"][n] for n in nums], "prior": [-1] * len(nums), "V0": [0] * len(nums),
             "frames": [{"t": iso(fin["updated"]), "f": [1000] * len(nums), "a": [i1000(x) for x in a.loc[nums]],
                         "proj": [i1000(x) for x in a.loc[nums]], "winner": winner.loc[nums].tolist(),
                         "margin": [i1000(x) for x in margin]}]}

    races, timeline, settle = market_block(ca.quebec_markets(), QC_START, QC_END, QC_COLOR)

    snaps = []
    for ts in QC_CAPTURES:
        s = ca.parse_dgeq(fetch(f"https://web.archive.org/web/{ts}id_/{QC_LIVE}", f"quebec/live_{ts}.json").read_bytes())
        snaps.append({"capture": iso(datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)),
                      "updated": iso(s["updated"]), "boxes": list(s["boxes"]), "final": s["final"],
                      "leading": s["leading"], "archive": f"https://web.archive.org/web/{ts}/{QC_LIVE}"})
    # at least 1% of polling stations reported (the 20:00 capture holds 29 of 20,704), then PQ ahead in every later one
    after = [s for s in snaps if s["updated"] and s["updated"] >= QC_CLOSE and s["boxes"][0] >= 0.01 * s["boxes"][1]]
    first_pq = next((s for i, s in enumerate(after)
                     if all(max(x["leading"], key=x["leading"].get) == "PQ" for x in after[i:])), None)
    at_open = [r for r, x in settle.items() if x["polymarket"] == iso(QC_START)]
    stats = null_stats(settle)
    stats["market_behaviour"] = {
        "settle_threshold": SETTLE, "polymarket_settle": {r: x["polymarket"] for r, x in settle.items()},
        "official_snapshots": snaps,
        "official_first_pq_lead": first_pq and {k: first_pq[k] for k in ("updated", "boxes", "leading")},
        "settled_at_open": at_open,
        "note": ("Official timestamps are the iso8601DateMAJ field of Élections Québec's live results file, read from "
                 "the Internet Archive captures made that night (three, hours apart); they bound when the official "
                 "file showed a state, not when it first did.")}
    stats["outcome"] = {"seats": {QC_NAME[k]: int(n) for k, n in seats.items()},
                        "votes_share_pct": {QC_NAME[k]: round(100 * v[k].sum() / tot.sum(), 2) for k in ca.QC_PARTIES},
                        "official_final_updated": iso(fin["updated"])}
    pq_settle = settle["most-pq"]["polymarket"]
    notes = [
        "Prices only: Élections Québec's live results file keeps only its latest state, and the Internet Archive "
        "holds three captures from election night (one before any box reported), so there is no count timeline and "
        "no model; accuracy and profit cannot be measured.",
        f"Prices: Polymarket 1-minute price history (YES token), {iso(QC_START)} to {iso(QC_END)} (18:00 to 12:00 "
        "EDT); a row is written only when the price changes (plus the last minute). Settled = price of the eventual "
        f"outcome >= {SETTLE} from then to the end of the window.",
        f"Polls closed at {QC_CLOSE} (20:00 EDT).",
        "Map: one final frame, the official final results file (isResultatsFinaux true). Colour = Parti Québécois "
        "share of PQ + Liberal votes. No prior: 2022 was held on 125 different circonscriptions and Élections Québec "
        "publishes no transposition onto the 2026 map. Frame extras: 'winner' (party code), 'margin' (winner minus "
        "runner-up, share of valid votes x1000).",
        "Result: " + ", ".join(f"{QC_NAME[k]} {n}" for k, n in sorted(seats.items(), key=lambda x: -x[1]))
        + " seats (127).",
        QC_MENTION,
    ]
    notes.append(f"Already settled when the price window opened (18:00 EDT, before polls closed): {', '.join(at_open)}.")
    if first_pq:
        notes.append(
            f"Official timing, coarse: the first archived capture of Élections Québec's live file with at least 1% of "
            f"polling stations reported is stamped {first_pq['updated']} ({first_pq['boxes'][0]:,} of "
            f"{first_pq['boxes'][1]:,}; PQ leading in {first_pq['leading'].get('PQ')} of 127, short of the 64 needed "
            f"for a majority); Polymarket's 'PQ wins a majority' market settled 'no' at "
            f"{settle['pq-majority']['polymarket']}.")
    meta = {
        "id": "qc2026", "name": "Quebec general election", "date": "2026-10-05", "tz": "America/Toronto",
        "poll_close_utc": QC_CLOSE, "geometry": "geo/ca_qc_circonscriptions.topo.json",
        "geometry_object": "circonscriptions", "unit_key": "unit_id", "count_timeline": False,
        "count_note": "Prices only: no official count over time survives; the map shows the official final results.",
        "regions": [{"id": "qc", "name": "Quebec"}], "races": races,
        "map": {"prior": None, "final": "official final results (Élections Québec)"},
        "sources": [
            {"name": "Élections Québec (directeur général des élections), résultats de l'élection générale du "
                     "5 octobre 2026 (resultats.json)", "url": QC_FINAL,
             "licence": f"Licence sur l'utilisation des données ouvertes du directeur général des élections "
                        f"({QC_LICENCE}). {QC_MENTION}"},
            {"name": "Élections Québec, circonscriptions électorales 2026 (sans eau), simplified", "url": QC_DATA_PAGE,
             "licence": f"Licence sur l'utilisation des données ouvertes du directeur général des élections "
                        f"({QC_LICENCE})"},
            {"name": "Élections Québec live results file, Internet Archive captures",
             "url": "https://web.archive.org/web/2026*/" + QC_LIVE,
             "licence": f"Licence sur l'utilisation des données ouvertes du directeur général des élections "
                        f"({QC_LICENCE})"},
            {"name": "Polymarket price history (CLOB prices-history, 1-minute) and market questions (Gamma API)",
             "url": "https://polymarket.com/event/quebec-general-election-winner", "licence": "Polymarket public data"},
        ],
        "notes": notes,
    }
    dump(QC_OUT / "meta.json", meta)
    dump(QC_OUT / "timeline.json", timeline)
    dump(QC_OUT / "units.json", units)
    dump(QC_OUT / "stats.json", stats)
    topo(fetch(QC_BOUNDARIES, "quebec/circonscriptions_electorales_sans_eau_2026.json"), QC_GEO,
         "unit_id='ca-qc-p'+CO_CEP, name=NM_CEP", "circonscriptions", 127, "8%", proj=False)
    summary = ("Prices only (no official count over time survives): the PQ 'most seats' and premier markets were "
               "already above 0.9 before polls closed; 'PQ wins a majority' settled 'no' at "
               f"{settle['pq-majority']['polymarket'][11:16]} UTC")
    if first_pq:
        summary += (f", when Élections Québec's file (stamped {first_pq['updated'][11:16]} UTC) showed the PQ leading "
                    f"in {first_pq['leading'].get('PQ')} of 127")
    entry = {"id": "qc2026", "name": "Quebec general election", "date": "2026-10-05", "country": "CA",
             "status": "replay", "unit_level": "circonscription", "n_units": len(nums),
             "evidence": "market-only", "summary": summary + "."}
    (QC_OUT / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    print(f"qc2026: {len(races)} races, {len(nums)} circonscriptions, geometry {QC_GEO.stat().st_size / 1e6:.2f} MB; "
          f"seats {seats}", file=sys.stderr)


def main() -> None:
    which = sys.argv[1:] or ["ca2025", "qc2026"]
    for eid in which:
        {"ca2025": canada_main, "qc2026": quebec_main}[eid]()


if __name__ == "__main__":
    main()
