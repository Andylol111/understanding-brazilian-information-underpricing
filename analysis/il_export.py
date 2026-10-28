"""Export Israel 2026 (26th Knesset) to the dashboard (site/data/il2026/, site/data/geo/il_localities.topo.json).

Only publishable inputs (docs/israel.md, "What can and cannot be published"):
  - Polymarket prices of the twelve scored markets, from the books recorded on our server
    (results/israel/pm_prices.parquet, written by analysis/il_score.py), one row per minute;
  - question texts from the public Polymarket API;
  - official CEC 2022 (25th Knesset) final ballot-box file, summed to localities, as the map's prior layer;
  - CBS locality file (bycode2022.xlsx; data.gov.il "Localities in Israel", licence Other (Open)) for names and
    districts; locality polygons from analysis/il_geo.py (CBS statistical areas 2022, CBS open licence);
  - aggregate scores from results/israel/stats.json (no per-release or per-minute count series).
Nothing derived from the 2026 count mirrors (votes26, earthelects, navonyaniv) is written except the aggregate
scores named above, so timeline count fields are null and meta.count_timeline is false.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/il_geo.py      # geometry first
    BRUNDER_DATA=~/brunder-data-local uv run --with openpyxl python analysis/il_export.py
    ... --official <CEC 2026 final expb.csv> --certified <ISO UTC>   # once the CEC final is published
"""

from __future__ import annotations

import argparse
import json
import tomllib

import pandas as pd

from brunder import israel as il
from brunder import polymarket as pm
from brunder.paths import RAW, ROOT

SITE = ROOT / "site" / "data"
OUT = SITE / "il2026"
GEO = SITE / "geo" / "il_localities.topo.json"
RES = ROOT / "results" / "israel"
CFG = ROOT / "configs" / "il2026_v1.toml"
CBS_FILE = RAW / "israel" / "cbs" / "bycode2022.xlsx"
CBS_URL = "https://www.cbs.gov.il/he/publications/doclib/2019/ishuvim/bycode2022.xlsx"
CBS_LICENCE = "https://www.cbs.gov.il/he/Pages/רישיון-שימוש.aspx"
SA_ITEM = "https://www.arcgis.com/home/item.html?id=372606908bcf49a69adaebc24fefc04a"
# 2022 lists of the bloc that formed the government (Likud, Religious Zionism-Otzma, Shas, UTJ)
BLOC22 = ["מחל", "ט", "שס", "ג"]
DISTRICTS = {1: "Jerusalem", 2: "Northern", 3: "Haifa", 4: "Central", 5: "Tel Aviv", 6: "Southern",
             7: "Judea and Samaria"}
NAV = "#1f4e9c"
OPP = "#2a9d8f"            # distinct from the dashboard's model orange (#c4501b) and #1b1b1b
EVIDENCE = "not-preregistered"     # contract label; details in evidence_note
EV_NOTE = ("not pre-registered: settings chosen on the official 2021/2022 counts; config first committed after "
           "scoring; scored once on 2026 with post-run data-handling fixes (prereg/NOTES.md, Israel section)")


def dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def localities() -> pd.DataFrame:
    """CBS locality list: English name (Hebrew if none) and district code."""
    x = pd.read_excel(CBS_FILE)
    x = x.rename(columns={"סמל יישוב": "code", "שם יישוב באנגלית": "name_en", "שם יישוב": "name_he",
                          "סמל מחוז": "district"})
    x["code"] = x["code"].astype(int)
    x["name"] = x["name_en"].fillna(x["name_he"]).astype(str).str.strip()
    return x.set_index("code")[["name", "district"]]


def polygon_codes() -> set[int]:
    doc = json.loads(GEO.read_text())
    return {int(g["properties"]["unit_id"][3:]) for g in doc["objects"]["localities"]["geometries"]}


def units(loc: pd.DataFrame, official: str | None, certified: str | None) -> tuple[dict, list[int], int, int]:
    """Voting localities (2022 valid votes > 0) that have a polygon; returns units, codes, and the 2022 valid
    votes and localities left off the map (double envelopes, localities without a CBS polygon)."""
    prior = il.by_locality(il.read_expb(RAW / tomllib.loads(CFG.read_text())["prior"]["file"]))
    poly = polygon_codes()
    voting = [c for c in prior.index if prior.at[c, "valid"] > 0]
    codes = [c for c in voting if c in poly and c in loc.index]
    off = [c for c in voting if c not in codes]
    dropped = int(prior.loc[off, "valid"].sum())
    p = prior.loc[codes]
    a = p[[c for c in BLOC22 if c in p.columns]].sum(axis=1) / p["valid"]
    out = {"unit_ids": [f"il-{c}" for c in codes],
           "region": [f"d{int(loc.at[c, 'district'])}" for c in codes],
           "names": [loc.at[c, "name"] for c in codes],
           "prior": (1000 * a).round().astype(int).tolist(),
           "V0": p["valid"].astype(int).tolist(),
           "a": {"name": "Government bloc (2022: Likud, RZ-Otzma, Shas, UTJ)", "color": NAV},
           "b": {"name": "Other lists", "color": OPP},
           "frames": []}
    if official:
        cfg = tomllib.loads(CFG.read_text())
        f = il.by_locality(il.read_expb(official)).reindex(codes)
        net = [k for k in cfg["blocs"]["netanyahu"] if k in f.columns]
        share = (f[net].sum(axis=1) / f["valid"]).where(f["valid"] > 0)
        a26 = (1000 * share).round().fillna(-1).astype(int).tolist()
        out["frames"] = [{"t": certified, "f": [1000 if v >= 0 else 0 for v in a26], "a": a26, "proj": a26}]
    return out, codes, dropped, len(off)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", help="official CEC 2026 final expb.csv (adds the final map frame)")
    ap.add_argument("--certified", help="ISO UTC time the CEC published the final result")
    args = ap.parse_args()
    cfg = tomllib.loads(CFG.read_text())
    stats_in = json.loads((RES / "stats.json").read_text())

    # ---- races: one per scored binary market, A = the market's YES side
    names = cfg["lists"]
    qs = {}
    for ev in sorted({m["event"] for m in cfg["markets"]}):
        for _, r in pm.markets(pm.event(ev)).iterrows():
            qs[r["slug"]] = (r["question"], r["outcomes"])
    races = []
    for m in cfg["markets"]:
        q, outcomes = qs[m["id"]]
        if m["kind"] == "most_seats":
            p = names[m["party"]].split(" (")[0]
            slug = "".join(ch if ch.isalnum() else "-" for ch in p.lower()).strip("-").replace("--", "-")
            rid, nm = f"most-{slug}", f"{p} wins the most seats"
            a, b = {"id": "yes", "name": p, "color": NAV}, {"id": "no", "name": f"Another list", "color": OPP}
        elif m["kind"] == "seats_band":
            band = f"{m['lo']}-{m['hi']}" if m["hi"] < 120 and m["lo"] > 0 else (
                f"fewer than {m['hi'] + 1}" if m["lo"] == 0 else f"{m['lo']} or more")
            rid, nm = f"likud-{m['lo']}", f"Likud wins {band} seats"
            a, b = {"id": "yes", "name": f"Likud {band}", "color": NAV}, {"id": "no", "name": "Other seat count", "color": OPP}
        else:
            assert outcomes[0] == "Bibi", outcomes   # YES token = Netanyahu bloc
            rid, nm = "bloc", "Which bloc wins more seats"
            a = {"id": "netanyahu", "name": "Netanyahu bloc", "color": NAV}
            b = {"id": "opposition", "name": "Opposition bloc", "color": OPP}
        races.append({"race_id": rid, "name": nm, "region": None, "a": a, "b": b,
                      "markets": [{"platform": "polymarket", "id": m["id"], "question": q, "yes_is": "a"}]})
    rid_of = {r["markets"][0]["id"]: r["race_id"] for r in races}

    # ---- timeline: Polymarket only, last price in each minute
    d = pd.read_parquet(RES / "pm_prices.parquet")
    d["price"] = d["price"].clip(lower=d["bid"].fillna(0.0), upper=d["ask"].fillna(1.0))
    d["t"] = d["t"].dt.floor("min")
    d = d.sort_values("t").groupby(["market", "t"])["price"].last().reset_index()
    timeline = {}
    for mid, g in d.groupby("market"):
        timeline[rid_of[mid]] = [{"t": iso(t), "counted": None, "raw": None, "share": None, "sd": None, "P": None,
                                  "pm": round(float(p), 4), "kalshi": None} for t, p in zip(g["t"], g["price"])]

    # ---- units + geometry
    loc = localities()
    u, codes, dropped, n_off = units(loc, args.official, args.certified)

    # ---- stats: aggregate scores only
    st_in = stats_in["settle"]
    settle, leads = {}, {}
    for mid, s in st_in.items():
        r = rid_of[mid]
        settle[r] = {"model": None, "polymarket": iso(pd.Timestamp(s["polymarket"])) if s["polymarket"] else None,
                     "kalshi": None, "raw": None}
        leads[r] = {"contested": s["contested"], "lead_min": s["lead_min"], "lead_min_gate1pct": s["lead_min_gate1pct"]}
    cal = stats_in["calibration"]["list_shares"]
    ee = {q: {"model": v["model_mae_pt"], "raw": v["raw_mae_pt"]} for q, v in stats_in["early_error"].items() if v}
    cu = stats_in["catchup"]["polymarket"]
    br = pd.DataFrame(stats_in["brier_by_release"])
    stats = {
        "calibration": {r: None for r in rid_of.values()},
        "calibration_list_shares": {"coverage90_1_95": cal["coverage90_1_95"], "mean_z": cal["mean_z"],
                                    "n": cal["n"], "releases": cal["releases"]},
        "settle": settle,
        "catchup": {"polymarket": {k: cu[k] for k in ("slope30", "lo", "hi", "markets", "n")}, "kalshi": None},
        "early_error": {r: None for r in rid_of.values()},
        "early_error_list_shares_mae": ee,
        "capacity": {"polymarket": None, "kalshi": None},
        "arbitrage": None,
        "evidence": {k: EVIDENCE for k in ("calibration", "settle", "catchup", "early_error", "capacity", "arbitrage")},
        "evidence_note": EV_NOTE,
        "brier": {"releases": int(len(br)), "markets": int(br["n"].max()),
                  "model_better": int((br.brier_model < br.brier_market).sum()),
                  "market_better": int((br.brier_market < br.brier_model).sum()),
                  "mean_model": round(float(br.brier_model.mean()), 4),
                  "mean_market": round(float(br.brier_market.mean()), 4),
                  "summary": "market better up to 2.5% counted; model better from 7.6% and at every later release "
                             "except 13.8%"},
        "lead": {"by_race": leads, **stats_in["settle_summary"]},
        "outcome": stats_in["outcome"],
        "reasons": {
            "settle.model": "model settle times come from the 2026 count, which we hold only from third-party mirrors "
                            "(private research use); the lead over Polymarket is given in 'lead' as an aggregate",
            "settle.raw": "raw-count settle times come from the same third-party count",
            "settle.kalshi": "Kalshi data licence (docs/kalshi.md)",
            **{f"calibration.{r}": "seat markets have no share interval to calibrate; list-share calibration over "
                                   "all releases 1-95% counted is in 'calibration_list_shares' (against the provisional "
                                   "outcome, which lacks the double envelopes the projection includes)"
               for r in rid_of.values()},
            **{f"early_error.{r}": "seat markets have no share error; list-share mean absolute error (lists >= 1%, "
                                   "points, model vs raw count) is in 'early_error_list_shares_mae'"
               for r in rid_of.values()},
            "capacity": "no trading rule is specified for Israel",
            "arbitrage": "Kalshi data licence (docs/kalshi.md)",
            "catchup.kalshi": "Kalshi data licence (docs/kalshi.md)",
            "timeline": "count fields are null: no official 2026 count over time exists in any archive",
        },
    }

    final = bool(args.official)
    notes = [
        "Count over time not shown: the CEC's 2026 results site could not be reached from here (HTTP 403/404, not "
        "circumvented) and no archive holds the official count during the night; the only 2026 count we hold is "
        "from third-party mirrors licensed for private research only. So the timeline shows Polymarket prices "
        "only, and the model's scores are given as aggregates.",
        "Prices: Polymarket books recorded every 30 s on our server from 18:31 UTC 27 Oct to 12:00 UTC 28 Oct; mid "
        "if spread <= 10c, else last trade, bounded by the best bid and ask; last value in each minute.",
        f"Scores are {EV_NOTE}.",
        f"Outcome used for the scores: {stats_in['outcome']}.",
        "Lead = Polymarket settle minus model settle (positive: model first); settled = P(eventual outcome) >= 0.9 "
        f"from then to 12:00 UTC 28 Oct. Contested markets: {stats_in['settle_summary']['contested_markets']}; median "
        f"lead {stats_in['settle_summary']['median_lead_min']:+.0f} min as configured, "
        f"{stats_in['settle_summary']['median_lead_min_gate1pct']:+.0f} min if the model waits for 1% counted "
        "(a sensitivity added after the first run).",
        "Map: locality polygons (CBS statistical areas 2022, dissolved by locality code); built-up areas only, so "
        "regional-council open land between localities is blank. Colour = share of valid votes for the "
        "2022 government bloc (Likud, Religious Zionism-Otzma Yehudit, Shas, UTJ) in the official 2022 final. "
        + ("The 2026 frame is the official CEC final: share for the Netanyahu bloc as named in the Polymarket market."
           if final else "The 2026 final map is pending the CEC's official final result."),
        f"Not on the map: {dropped:,} of the 2022 valid votes ({n_off} units: the double envelopes of soldiers, "
        "diplomats, prisoners etc., and localities such as recognised Bedouin tribes that have no CBS statistical "
        "area).",
        "Model only, no market: P(Netanyahu bloc >= 61 seats) ended at 0; outcome no.",
    ]
    meta = {
        "id": "il2026", "name": "Israel, 26th Knesset election", "date": "2026-10-27", "tz": "Asia/Jerusalem",
        "poll_close_utc": cfg["poll_close_utc"], "geometry": "geo/il_localities.topo.json",
        "geometry_object": "localities", "geometry_type": "polygons", "unit_key": "unit_id",
        "count_timeline": False,
        "count_note": ("Prices only: the 2026 count over time cannot be published (the official CEC count was not "
                       "reachable or archived; the copies we hold are third-party, private research use). Model "
                       "scores are shown as totals below."),
        "regions": [{"id": f"d{k}", "name": v} for k, v in DISTRICTS.items()],
        "races": races,
        "map": {"prior": "2022 government-bloc share of valid votes (official CEC final)",
                "final": "official CEC 2026 final" if final else None},
        "sources": [
            {"name": "Central Elections Committee, 25th Knesset ballot-box results (expb.csv), via the Internet "
                     "Archive; also listed on data.gov.il", "url": cfg["prior"]["source"], "licence": "Other (Open), data.gov.il"},
            {"name": "Central Bureau of Statistics, localities file 2022 (bycode2022.xlsx): names and districts",
             "url": CBS_URL, "licence": "Other (Open), data.gov.il dataset 'Localities in Israel'"},
            {"name": "הלשכה המרכזית לסטטיסטיקה (הלמ\"ס), אזורים סטטיסטיים 2022, (28/10/2026), dissolved by locality "
                     "and simplified; מועתק ומופץ כמות שהוא – as is – על פי רישיון שימוש של הלמ\"ס",
             "url": SA_ITEM, "licence": f"CBS open licence ({CBS_LICENCE})"},
            {"name": "Polymarket order books (recorded) and market questions (public API)",
             "url": "https://polymarket.com/event/israeli-legislative-election-winner", "licence": "Polymarket public data"},
        ],
        "notes": notes,
    }
    dump(OUT / "meta.json", meta)
    dump(OUT / "timeline.json", timeline)
    dump(OUT / "units.json", u)
    dump(OUT / "stats.json", stats)

    entry = {"id": "il2026", "name": "Israel, 26th Knesset election", "date": "2026-10-27", "country": "IL",
             "status": "replay", "unit_level": "locality", "n_units": len(codes), "evidence": EVIDENCE,
             "summary": (f"On the {stats_in['settle_summary']['contested_markets']} contested Polymarket markets the count "
                         f"model settled a median {stats_in['settle_summary']['median_lead_min']:.0f} minutes before the "
                         "market (5 of 6 first), but trailed by a median 28 minutes when made to wait for 1% counted; "
                         "prices only, as the 2026 count over time cannot be published.")}
    # landing entry; the integrator merges site/data/<eid>/entry.json into elections.json
    (OUT / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    print(f"il2026: {len(races)} races, {len(codes)} localities, geometry {GEO.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
