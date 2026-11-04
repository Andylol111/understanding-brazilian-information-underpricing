"""Early signal from the first counted U.S. House districts (Indiana, Kentucky, then the nation): DISPLAY ONLY, unregistered.

Reads the recorded civicAPI House race (``civic_87524``: 435 districts, each with its candidates' votes and a
percent-reporting value) from the shared archive, read only, and writes ``<out>/us2026/inky.json``.  No network code,
no model, no registered file is touched; a failure in here is logged and skipped (``run_loop``), never raised into the
added-source live loop that hosts it.

What is computed, per district with votes (IN and KY always, any other district from 20 % reporting):
  - D two-party share so far, D / (D + R) of the top Democrat and top Republican on the ballot;
  - raw swing vs the 2024 president (and vs the 2024 House race where the lines did not change), in share points.
    RAW SWINGS ARE COMPOSITION-BIASED: which precincts report first (and early / mail ballots) is not random;
  - swing vs EXPECTED: the district's own expectation, the pre-count model baseline of the House district file
    (2024 president on the 2026 lines + the poll-implied national swing, ``baseline.share``).  Where a county file with
    the 2024 president by county exists (IN-05, the added Indiana model) the expectation is the county composition of
    what has been counted (the 2024 president in the counties that have reported, plus the same poll-implied swing);
  - counted votes vs expected votes at that reporting level (turnout; expected = EST_TURNOUT x 2024 two-party votes).
Aggregates are vote-weighted over contested districts with >= 10 % reporting: Indiana, Kentucky, both, and the nation
(shown as a read only once 20 districts nationally have >= 25 % reporting).

"Early-signal adjusted" (UNREGISTERED, not validated): the aggregate swing vs expected, shrunk by n / (n + 20), is added
to the pre-count share of every Senate and House race (probit shift ``dz`` = shift / the race's sd), so a page can
apply it to races not yet counted.  Seats are also tallied here with every race at its pre-count chance, before / after.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT

HOUSE_KEY = "civic_87524"
IN_KEY = "in_house"
OUT_NAME = "inky.json"
EST_TURNOUT = 0.72                    # brunder.us.EST_TURNOUT: 2026 midterm / 2024 president two-party votes
MIN_REPORT = 10.0                     # a district enters the aggregate from this percent reporting
SHOW_REPORT = 20.0                    # districts other than IN / KY are listed from this percent reporting
NATIONAL_PCT, NATIONAL_N = 25.0, 20   # "too early for a national read" until NATIONAL_N districts have NATIONAL_PCT
SHRINK_N = 20.0                       # adjustment weight n / (n + SHRINK_N)
MIN_N_ADJUST = 5                      # no adjustment from fewer contested districts than this
FOCUS = ("IN", "KY")
P7 = ("TX", "OH", "MI", "ME", "KS", "AK", "IA")
LABEL = "early signal, not validated; partial counts can mislead"
ADJ_LABEL = ("Early-signal adjusted (unregistered, not validated): applies the swing seen in counted districts to "
             "races not yet counted.")
SOURCE = {"name": "civicAPI (civicapi.org)", "url": "https://civicapi.org"}
_NAME = re.compile(r"(\d{8}T\d{6}\.\d{6})Z\.json(\.gz)?$")
_KEY = re.compile(r"^([a-z]{2})-(\d{2}|al)$")
_WARNED: set[tuple[str, str]] = set()


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _round(x, nd=2):
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ baselines (static project files)

def load_baselines(root: Path = ROOT) -> dict:
    """Districts (2024 president, 2024 House, pre-count baseline on the 2026 lines), the Senate races' pre-count
    figures and the poll-implied swings of the 7 Senate states, from site/data/us2026 (committed project files)."""
    base = Path(root) / "site" / "data" / "us2026"
    hd = json.loads((base / "house" / "districts.json").read_text())
    redrawn = set(hd.get("redrawn") or [])
    districts = {}
    for k, v in hd["districts"].items():
        p, h, b = v.get("pres24") or {}, v.get("house24") or {}, v.get("baseline") or {}
        districts[k] = {"st": v.get("st") or k[:2], "pres24_d2": p.get("d2"), "pres24_votes": (p.get("dv") or 0) + (p.get("rv") or 0),
                        "house24_d2": h.get("d2") if h.get("d_vs_r") and v.get("st") not in redrawn else None,
                        "uncontested24": bool(h.get("uncontested")), "share": b.get("share"), "sd": b.get("sd"), "p_d": b.get("p_d")}
    ov = json.loads((base / "overview.json").read_text())["offices"]["senate"]
    state = {s: c["d24_alloc"] / (c["d24_alloc"] + c["r24_alloc"]) for s, c in hd["state_check"].items()
             if c.get("d24_alloc") and c.get("r24_alloc")}
    races = []
    for o in ov["races"]:
        est, pol = o.get("estimate") or {}, o.get("polls") or {}
        share, sd = est.get("share", pol.get("avg")), est.get("sd", pol.get("sd"))
        p = est.get("P", pol.get("p_win"))
        races.append({"key": o["race_key"], "st": o["state"].upper(), "name": o.get("state_name"), "special": bool(o.get("special")),
                      "share": share, "sd": sd, "p": p, "aligned": (o.get("dem_side") or {}).get("aligned")})
    swings = []
    for r in races:
        if r["st"] in P7 and r["share"] is not None and r["st"] in state and r["st"] not in {x["st"] for x in swings}:
            swings.append({"st": r["st"], "poll_share": _round(100 * r["share"], 2), "pres24": _round(100 * state[r["st"]], 2),
                           "swing": _round(100 * (r["share"] - state[r["st"]]), 1)})
    swings.sort(key=lambda s: P7.index(s["st"]))
    hold = ov.get("holdovers") or {}
    nat = (hd.get("national") or {}).get("generic_ballot") or {}
    return {"districts": districts, "senate": races, "senate_swings": swings, "not_up_d": (hold.get("D") or 0) + (hold.get("I") or 0),
            "not_up_r": hold.get("R") or 0, "majority": ov.get("majority"), "generic_swing": _round(100 * nat["swing_vs_pres24"], 1)
            if nat.get("swing_vs_pres24") is not None else None, "generic_avg": nat.get("avg"),
            "in05_prior": _in05_prior(Path(root)), "built": hd.get("built")}


def _in05_prior(root: Path) -> dict[str, tuple[float, float]]:
    """county unit_id -> (D, R) votes of the 2024 president in the county's part of IN-05 (data_public priors)."""
    f = root / "data_public" / "us2026_extra_priors_in.csv"
    out = {}
    try:
        with f.open(newline="") as fh:
            for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                if r["source"] == "in-05_pres_2024":
                    out[r["unit_id"]] = (float(r["dem"]), float(r["rep"]))
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ the recorded snapshots

def latest_snapshot(d: Path, parse, tries: int = 3):
    """(UTC time of the newest snapshot of key folder `d` that `parse` accepts, parsed value, file name), skipping a
    partial file for the one before it; None when there is none."""
    try:
        names = sorted(f.name for f in d.iterdir() if _NAME.search(f.name))
    except OSError:
        return None
    for name in reversed(names[-tries:]):
        try:
            raw = (d / name).read_bytes()
            raw = gzip.decompress(raw) if name.endswith(".gz") else raw
            val = parse(raw)
            if val is not None:
                m = _NAME.search(name)
                return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S.%f").replace(tzinfo=timezone.utc), val, name
        except Exception as e:                       # partial write, bad gzip, truncated JSON: the previous one
            if (d.name, name) not in _WARNED:        # once per file, not every cycle
                _WARNED.add((d.name, name))
                print(f"inky: snapshot skipped {d.name}/{name}: {type(e).__name__}: {e}", flush=True)
    return None


def _parse_house(raw: bytes):
    doc = json.loads(raw)
    return doc if isinstance(doc.get("region_results"), dict) else None


def _side(party) -> str:
    p = str(party or "").strip().lower()
    return "D" if p.startswith("dem") or p == "d" else "R" if p.startswith("rep") or p == "r" else "O"


# ------------------------------------------------------------------ one district

def district_row(key: str, reg: dict, base: dict) -> dict | None:
    """The row of one district (civicAPI region `reg`, baseline `base`); None when the key is not a House district."""
    m = _KEY.match(key.lower())
    if not m:
        return None
    name = f"{m.group(1).upper()}-{m.group(2).upper()}"
    top = {"D": None, "R": None}
    other = 0.0
    for c in reg.get("candidates") or []:
        v, s = float(c.get("votes") or 0), _side(c.get("party"))
        if s == "O":
            other += v
        elif top[s] is None or v > top[s][0]:
            top[s] = (v, c.get("name"))
    pct = float(reg.get("percent_reporting") or 0.0)
    d, r = (top["D"][0] if top["D"] else 0.0), (top["R"][0] if top["R"] else 0.0)
    n2 = d + r
    pair = top["D"] is not None and top["R"] is not None
    row = {"id": name, "st": name[:2], "pct": _round(pct, 1), "d_votes": int(d), "r_votes": int(r), "counted": int(n2),
           "d_cand": top["D"][1] if top["D"] else None, "r_cand": top["R"][1] if top["R"] else None,
           "uncontested": bool(n2 > 0 and not pair), "uncontested24": bool(base and base["uncontested24"])}
    row["status"] = "no votes yet" if n2 <= 0 else "uncontested (no D-R pair)" if not pair else "counting"
    row["d2"] = _round(d / n2, 4) if n2 > 0 and pair else None
    if not base:
        return row
    exp_final = EST_TURNOUT * base["pres24_votes"]
    row.update({"pres24_d2": base["pres24_d2"], "house24_d2": base["house24_d2"], "expected_share": base["share"], "expected_basis": "district baseline",
                "exp_votes": int(exp_final), "exp_votes_counted": int(exp_final * pct / 100.0)})
    row.update({"swing_pres": None, "swing_house24": None, "swing_expected": None, "swing_poll_implied": None})
    if row["d2"] is not None:
        row["swing_pres"] = _round(100 * (row["d2"] - base["pres24_d2"]), 1)
        row["swing_house24"] = None if base["house24_d2"] is None else _round(100 * (row["d2"] - base["house24_d2"]), 1)
        row["swing_expected"] = None if base["share"] is None else _round(100 * (row["d2"] - base["share"]), 1)
        row["swing_poll_implied"] = None if base["share"] is None else _round(100 * (base["share"] - base["pres24_d2"]), 1)
    ec = exp_final * pct / 100.0
    row["turnout_ratio"] = _round(n2 / ec, 2) if row["d2"] is not None and ec > 0 else None
    return row


def in05_composition(rows, prior: dict, base: dict) -> dict | None:
    """IN-05's expectation from the counties that have reported: the 2024 president in those counties' parts of the
    district (+ the district's poll-implied swing) against the counted two-party share of the official county file.
    `rows`: the parsed in_house frame (brunder.extra_in.parse)."""
    d = rows[(rows["race"] == "house-05") & (rows["party"] == "DEM")].groupby("unit_id")["votes"].max()
    r = rows[(rows["race"] == "house-05") & (rows["party"] == "REP")].groupby("unit_id")["votes"].max()
    units = sorted(set(d.index) | set(r.index))
    num = den = dv = rv = 0.0
    n_counties = 0
    for u in units:
        dd, rr = float(d.get(u, 0.0)), float(r.get(u, 0.0))
        pd_, pr = prior.get(u, (0.0, 0.0))
        if dd + rr <= 0 or pd_ + pr <= 0:
            continue
        n_counties += 1
        dv, rv = dv + dd, rv + rr
        num, den = num + (dd + rr) * pd_ / (pd_ + pr), den + dd + rr
    if den <= 0 or not base or base["share"] is None:
        return None
    delta = base["share"] - base["pres24_d2"]
    exp = num / den + delta
    return {"counties_reporting": n_counties, "counted": int(dv + rv), "d2": _round(dv / (dv + rv), 4), "expected_share": _round(exp, 4),
            "composition_pres24": _round(num / den, 4), "swing_expected": _round(100 * (dv / (dv + rv) - exp), 1)}


# ------------------------------------------------------------------ aggregates

def aggregate(rows: list[dict], min_pct: float = MIN_REPORT) -> dict:
    """Vote-weighted (counted two-party votes) means over the contested districts of `rows` with >= min_pct reporting."""
    use = [r for r in rows if r.get("d2") is not None and r["pct"] >= min_pct and r.get("swing_pres") is not None and r["id"] != "AK-AL"]
    out = {"n": len(use), "min_pct": min_pct, "districts": [r["id"] for r in use]}
    if not use:
        return {**out, "swing_pres": None, "swing_expected": None, "poll_implied": None, "turnout_ratio": None, "reporting_share": None,
                "counted": 0, "se_expected": None}
    w = [r["counted"] for r in use]
    sw = sum(w)

    def wm(f):
        xs = [(f(r), wi) for r, wi in zip(use, w) if f(r) is not None]
        t = sum(wi for _, wi in xs)
        return _round(sum(x * wi for x, wi in xs) / t, 2) if t > 0 else None
    exp_c = sum(r["exp_votes_counted"] for r in use)
    exp_f = sum(r["exp_votes"] for r in use)
    sx = [r["swing_expected"] for r in use if r.get("swing_expected") is not None]
    se = None
    if len(sx) >= 3:
        m = sum(sx) / len(sx)
        se = _round(math.sqrt(sum((x - m) ** 2 for x in sx) / (len(sx) - 1)) / math.sqrt(len(sx)), 2)
    return {**out, "swing_pres": wm(lambda r: r["swing_pres"]), "swing_house24": wm(lambda r: r.get("swing_house24")),
            "swing_expected": wm(lambda r: r.get("swing_expected")), "poll_implied": wm(lambda r: r.get("swing_poll_implied")),
            "se_expected": se, "turnout_ratio": _round(sw / exp_c, 2) if exp_c > 0 else None, "counted": int(sw),
            "reporting_share": _round(sum(r["exp_votes_counted"] for r in use) / exp_f, 3) if exp_f > 0 else None,
            "mean_pct": _round(sum(r["pct"] for r in use) / len(use), 1)}


# ------------------------------------------------------------------ adjusted projection

def _tally(ps: list[float], not_up_d: int = 0, not_up_r: int = 0) -> dict:
    total = len(ps) + not_up_d + not_up_r
    e = not_up_d + sum(ps)
    d = min(total, max(0, round(e)))
    return {"expected_d": _round(e, 1), "d": d, "r": total - d}


def adjustment(agg_basis: dict | None, basis_name: str | None, bases: dict) -> dict:
    """The probit shift per race from the chosen aggregate, and the seat tallies before / after it."""
    n = agg_basis["n"] if agg_basis else 0
    sx = agg_basis["swing_expected"] if agg_basis else None
    ok = basis_name is not None and sx is not None and n >= MIN_N_ADJUST
    w = n / (n + SHRINK_N) if n else 0.0
    shift = w * sx / 100.0 if ok else 0.0
    adj = {"applied": bool(ok), "basis": basis_name if ok else None, "n": n, "weight": _round(w, 3),
           "aggregate_swing_expected": sx, "shift_pts": _round(100 * shift, 2), "label": ADJ_LABEL}
    house, hp0, hp1 = {}, [], []
    for k, b in bases["districts"].items():
        if b["share"] is None or b["sd"] in (None, 0) or b["p_d"] is None:
            continue
        p0 = b["p_d"]
        movable = ok and not b["uncontested24"] and 0.0 < p0 < 1.0
        p1 = _phi((b["share"] + shift - 0.5) / b["sd"]) if movable else p0
        house[k] = {"pre": _round(p0, 4), "adj": _round(p1, 4), "dz": _round(shift / b["sd"], 4) if movable else 0.0}
        hp0.append(p0)
        hp1.append(p1)
    senate, sp0, sp1 = {}, [], []
    for r in bases["senate"]:
        if r["share"] is None or r["sd"] in (None, 0) or r["p"] is None:
            continue
        movable = ok and r["aligned"] is None and 0.0 < r["p"] < 1.0
        p1 = _phi((r["share"] + shift - 0.5) / r["sd"]) if movable else r["p"]
        senate[r["key"]] = {"pre": _round(r["p"], 4), "adj": _round(p1, 4), "dz": _round(shift / r["sd"], 4) if movable else 0.0}
        sp0.append(r["p"])
        sp1.append(p1)
    adj["house"], adj["senate"] = house, senate
    adj["seats"] = {"note": "every race at its pre-count chance (pre) and shifted (adj); the website line keeps the live chance of races already counted",
                    "senate": {"pre": _tally(sp0, bases["not_up_d"], bases["not_up_r"]), "adj": _tally(sp1, bases["not_up_d"], bases["not_up_r"]),
                               "majority": bases["majority"]},
                    "house": {"pre": _tally(hp0), "adj": _tally(hp1), "majority": 218}}
    return adj


# ------------------------------------------------------------------ the document

def build(doc: dict | None, bases: dict, now: datetime, snap_time: datetime | None = None, snap_name: str | None = None,
          in5: dict | None = None) -> dict:
    """The inky.json document for one civicAPI House document `doc` (None: not recorded yet)."""
    out = {"version": 1, "label": LABEL, "unregistered": True, "t": _iso(now), "source": SOURCE,
           "units": "shares are fractions of the two-party vote (D / (D + R)); swings in share points; votes are D + R",
           "notes": ["RAW SWINGS ARE COMPOSITION-BIASED: the first precincts to report are not a random sample (urban vs rural, early vs "
                     "Election-Day ballots); read the reporting % beside every figure",
                     "swing vs expected compares the counted share with the district's own pre-count expectation (2024 president on the 2026 "
                     "lines + the poll-implied national swing; IN-05: the 2024 president in the counties that have reported)",
                     "turnout = counted two-party votes / expected votes at the reported %, expected = 0.72 x 2024 two-party votes"],
           "baselines_built": bases.get("built"),
           "polls": _polls(bases)}
    regions = (doc or {}).get("region_results") or {}
    rows = []
    for k, reg in regions.items():
        row = district_row(k, reg, bases["districts"].get(k.upper()))
        if row is not None:
            rows.append(row)
    if in5 and in5.get("swing_expected") is not None:
        for r in rows:
            if r["id"] == "IN-05":
                r["composition"] = in5
                if r.get("d2") is not None:
                    r["swing_expected_baseline"] = r.get("swing_expected")
                    r["swing_expected"] = in5["swing_expected"]
                    r["expected_basis"] = "county composition (official county file)"
                    r["expected_share"] = in5["expected_share"]
    counting = [r for r in rows if r["counted"] > 0]
    out["status"] = "waiting" if not counting or (doc or {}).get("test_data") else "counting"
    if snap_time is not None:
        out["snapshot"] = {"file": snap_name, "t": _iso(snap_time), "age_s": int((now - snap_time).total_seconds()),
                           "last_updated": (doc or {}).get("last_updated")}
    by_st = {s: [r for r in rows if r["st"] == s] for s in FOCUS}
    n25 = sum(1 for r in rows if r["d2"] is not None and r["pct"] >= NATIONAL_PCT and r["id"] != "AK-AL")
    nat = aggregate(rows)
    nat["ready"] = n25 >= NATIONAL_N
    nat["n_reporting_25"] = n25
    nat["needed"] = NATIONAL_N
    nat["note"] = None if nat["ready"] else f"too early for a national read: {n25} of {NATIONAL_N} districts have {NATIONAL_PCT:.0f}% reporting"
    ink = aggregate(by_st["IN"] + by_st["KY"])
    out["aggregates"] = {"in": aggregate(by_st["IN"]), "ky": aggregate(by_st["KY"]), "in_ky": ink, "national": nat}
    shown = [r for r in rows if r["st"] in FOCUS or (r["pct"] >= SHOW_REPORT and r["counted"] > 0)]
    out["districts"] = {r["id"]: r for r in sorted(shown, key=lambda r: r["id"])}
    out["focus_missing"] = sorted(f"{s}-{n:02d}" for s, k in (("IN", 9), ("KY", 6)) for n in range(1, k + 1)
                                  if f"{s}-{n:02d}" not in {r["id"] for r in rows})
    # the seat adjustment waits for the national read: early IN / KY returns are early ballots first (composition-biased),
    # so a shift from them alone moved the public seat line by the count order, not by the vote (seen live 2026-11-03)
    out["adjusted"] = adjustment(nat, "national", bases) if nat["ready"] else adjustment(None, None, bases)
    return out


def _polls(bases: dict) -> dict:
    sw = bases["senate_swings"]
    text = None
    if sw:
        lo, hi = min(sw, key=lambda s: s["swing"]), max(sw, key=lambda s: s["swing"])
        sgn = lambda x: ("+" if x > 0 else "\u2212" if x < 0 else "") + f"{abs(x):.1f}"
        text = (f"polls imply swings of {sgn(lo['swing'])} ({lo['st']}) to {sgn(hi['swing'])} ({hi['st']}) vs 2024 in the {len(sw)} Senate states"
                + (f"; the national generic ballot implies {sgn(bases['generic_swing'])}" if bases.get("generic_swing") is not None else ""))
    return {"senate_states": sw, "generic_ballot_swing": bases.get("generic_swing"), "text": text,
            "note": "poll average (dem side) minus the state's 2024 two-party president share, share points"}


# ------------------------------------------------------------------ the loop

def _digest(o: dict) -> str:
    return json.dumps({k: v for k, v in o.items() if k not in ("t",)}, sort_keys=True, separators=(",", ":"))


def run_loop(out: Path, snap_root: Path, shared_root: Path | None = None, interval: float = 30.0, stop_at=None,
             write_json=None, root: Path = ROOT, cycles: int | None = None) -> None:
    """Every `interval` s: read the newest civic_87524 snapshot and the IN-05 county file, rewrite <out>/us2026/inky.json when
    something changed (or every 2 min).  Any exception is printed and the cycle skipped: this loop never raises."""
    from . import us
    write_json = write_json or us._write_json
    bases = None
    last, wrote, cache = None, 0.0, {}
    n = 0
    while (stop_at is None or datetime.now(timezone.utc) < stop_at) and (cycles is None or n < cycles):
        t0 = time.monotonic()
        n += 1
        try:
            if bases is None:
                bases = load_baselines(root)
            now = datetime.now(timezone.utc)
            snap = None
            for d in ([shared_root / HOUSE_KEY] if shared_root else []) + [snap_root / HOUSE_KEY]:
                if d.exists():
                    snap = latest_snapshot(d, _parse_house)
                    if snap:
                        break
            doc, st, name = (snap[1], snap[0], snap[2]) if snap else (None, None, None)
            in5 = _in5(snap_root, bases, cache)
            res = build(doc, bases, now, st, name, in5)
            dig = _digest({k: v for k, v in res.items() if k != "snapshot"}) + (name or "")
            if dig != last or time.monotonic() - wrote > 120:
                write_json(Path(out) / us.EID / OUT_NAME, res)
                last, wrote = dig, time.monotonic()
                if res["status"] == "counting":
                    a = res["aggregates"]["in_ky"]
                    print(f"{now:%H:%M:%S} inky: {len(res['districts'])} districts, IN+KY n={a['n']} swing vs expected {a['swing_expected']}", flush=True)
        except Exception as e:                       # never stop the extra live loop
            print(f"{datetime.now(timezone.utc):%H:%M:%S} inky error, skipped: {type(e).__name__}: {e}", flush=True)
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))


def _in5(snap_root: Path, bases: dict, cache: dict) -> dict | None:
    """IN-05 county composition from the added recorder's Indiana file (own archive); None when absent. Parsed only when the
    newest file changes."""
    d = Path(snap_root) / IN_KEY
    if not d.exists():
        return None
    try:
        names = sorted(f.name for f in d.iterdir() if _NAME.search(f.name))
    except OSError:
        return None
    if not names:
        return None
    if cache.get("name") == names[-1]:
        return cache.get("val")
    from . import extra_in

    def parse(raw: bytes):
        rows = extra_in.parse("in_house", {"offcat": raw})
        return rows if len(rows) else None
    snap = latest_snapshot(d, parse)
    val = in05_composition(snap[1], bases["in05_prior"], bases["districts"].get("IN-05")) if snap else None
    cache["name"], cache["val"] = names[-1], val
    return val
