"""Registration over time and the NC registration mix for the midterms context page.

Series: data_public/us2026_context/reg_<st>.csv (collected from official reports, from Oct 2022 on) plus EARLIER official reports that
sit on disk: NC voter_stats files, LA 'sta_comb' registration PDFs, CO monthly workbooks, FL party-by-county archives, NM registration
PDFs, and the MD / OR year-end register counts in raw/us/ev_party/ev_long1_reg.csv. Points are added only where the file exists."""
from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import us_ctx_nc as nc
import us_ctx_tax as tx
from us_ctx_tax import r2

BASE_FROM = "2022-10-01"                  # the change is measured from the first report on/after this date


def _pt(d, D, R, O, U, total, basis):
    return {"date": d, "D": int(D), "R": int(R), "O": None if O is None else int(O), "U": None if U is None else int(U),
            "total": int(total), "basis": basis}


# ------------------------------------------------------------------------------------------------------- NC registration mix
def build_nc_mix(raw: Path, log=print) -> dict | None:
    """voter_stats registration counts by race / gender / party / county density and age band: now vs 2018, 2020, 2022, 2024."""
    nr = raw / "ev_party" / "nc"
    tier = nc.nc_density()
    files = sorted(nr.glob("voter_stats_20*.zip"))
    past_f = {y: nr / f for y, f in nc.REG_FILES.items() if (nr / f).exists()}
    if not files or not past_f:
        return None
    now_f = files[-1]
    now = nc.reg_counts(now_f, tier)
    past = {y: nc.reg_counts(f, tier) for y, f in past_f.items()}
    rows = []
    for dim in ("race", "age_ncreg", "gender", "party", "density"):
        tn = sum(now[dim].values())
        for g in tx.order(dim):
            n = now[dim].get(g, 0)
            p = {}
            for y, c in sorted(past.items(), reverse=True):
                tp = sum(c[dim].values())
                p[str(y)] = {"share": r2(100 * c[dim].get(g, 0) / tp), "n": int(c[dim].get(g, 0)),
                             "asof": nc.reg_file_date(past_f[y])}
            rows.append({"dim": dim, "dim_label": tx.label(dim), "group": g, "share_now": r2(100 * n / tn), "n_now": int(n), "past": p})
    log(f"  nc reg mix: now {nc.reg_file_date(now_f)} {sum(now['party'].values()):,}; " +
        ", ".join(f"{y} {sum(c['party'].values()):,}" for y, c in sorted(past.items())))
    return {"state": "nc", "asof_now": nc.reg_file_date(now_f), "rows": rows,
            "source": "NC State Board of Elections voter registration statistics (voter_stats files)",
            "url": "https://www.ncsbe.gov/results-data/voter-registration-data"}


def nc_reg_shares(mix: dict | None) -> dict[str, dict[str, float]]:
    """{dim: {group: share_now}} from the mix rows, for the early-vote tables' registered share."""
    out: dict[str, dict[str, float]] = {}
    for r in (mix or {}).get("rows", []):
        if r["share_now"] is not None:
            out.setdefault(r["dim"], {})[r["group"]] = r["share_now"]
    return out


# ------------------------------------------------------------------------------------------------------- earlier points
def nc_points(raw: Path) -> list[dict]:
    nr = raw / "ev_party" / "nc"
    tier = nc.nc_density()
    out = []
    for y in (2018, 2020):
        f = nr / nc.REG_FILES[y]
        if f.exists():
            p = nc.reg_counts(f, tier)["party"]
            tot = sum(p.values())
            out.append(_pt(nc.reg_file_date(f), p["Democratic"], p["Republican"], tot - p["Democratic"] - p["Republican"],
                           p["Unaffiliated"], tot, "all"))
    return out


def la_points(raw: Path) -> list[dict]:
    """LA registration statistics PDFs: STATE row = total (white, black, other), then the same for Democrats, Republicans, No party,
    Other parties."""
    import us_ev_src_f as sf
    out = []
    for f in sorted((raw / "ev_party" / "la" / "reg").glob("20*_sta_comb.pdf")):
        y, md = f.name[:4], f.name[5:9]
        if int(y) < 2018:
            continue
        lines = sf.pdf_text(f).splitlines()
        try:                                                  # 2026 report: Democrats, Republicans, No party, Other parties
            v = sf._la_after(lines, "STATE", 20)
            tot, dem, rep, nop, oth = v[0], v[4], v[8], v[12], v[16]
        except ValueError:                                    # 2018-2024 reports: Democrats, Republicans, Other parties (incl. no party)
            v = sf._la_after(lines, "STATE", 16)
            tot, dem, rep, nop, oth = v[0], v[4], v[8], None, v[12]
        if dem + rep + (nop or 0) + oth != tot:
            raise ValueError(f"LA registration {f.name}: parties do not add up")
        out.append(_pt(f"{y}-{md[:2]}-{md[2:]}", dem, rep, tot - dem - rep, nop, tot, "all"))
    return out


def co_points(raw: Path) -> list[dict]:
    """CO monthly 'Party & Status' workbooks: active voters, D / R / unaffiliated."""
    import warnings

    import openpyxl
    out = []
    for f in sorted((raw / "ev_party" / "co" / "reg").glob("20*_OctoberStatistics.xlsx")):
        if "_October_" in f.name or int(f.name[:4]) not in (2018, 2020):
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
            ws = list(wb["Party & Status"].iter_rows(values_only=True))
        m = re.search(r"(\d\d)/(\d\d)/(\d{4})", " ".join(str(r[0]) for r in ws[:3] if r and r[0]))
        h = next(r for r in ws[:6] if "DEM" in r)
        tot = next(r for r in ws if r[0] == "Total")
        ti = next(r.index("Active Total") for r in ws[:6] if "Active Total" in r)
        D, R, U, T = tot[h.index("DEM")], tot[h.index("REP")], tot[h.index("UAF")], tot[ti]
        out.append(_pt(f"{m[3]}-{m[1]}-{m[2]}", D, R, T - D - R, U, T, "active"))
    return out


def fl_points(raw: Path) -> list[dict]:
    """FL party-by-county archives, October sheet, active registered voters, statewide TOTALS row."""
    import warnings

    import openpyxl
    out = []
    for y in (2018, 2020):
        z = raw / "ev_party" / "fl" / f"reg_archive_{y}.zip"
        if not z.exists():
            continue
        with zipfile.ZipFile(z) as zf:
            name = next((n for n in zf.namelist() if "party" in n.lower() and "county" in n.lower()), None)
            if name is None:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                wb = openpyxl.load_workbook(io.BytesIO(zf.read(name)), read_only=True, data_only=True)
                if "October" not in wb.sheetnames:
                    continue
                rows = list(wb["October"].iter_rows(values_only=True))
        m = re.search(r"(\d\d)/(\d\d)/(\d{4})", " ".join(str(r[0]) for r in rows[:3] if r and r[0]))
        h = next(r for r in rows[:8] if r and str(r[0]).strip().upper() == "COUNTY")
        col = {k: next(i for i, c in enumerate(h) if c and k in str(c).upper()) for k in ("REPUBLICAN", "DEMOCRATIC", "NO PARTY", "TOTAL")}
        t = next(r for r in rows if r and str(r[0]).strip().upper().startswith("TOTAL"))
        D, R, U, T = t[col["DEMOCRATIC"]], t[col["REPUBLICAN"]], t[col["NO PARTY"]], t[col["TOTAL"]]
        out.append(_pt(f"{m[3]}-{m[1]}-{m[2]}", D, R, T - D - R, U, T, "active"))
    return out


_MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                       "October", "November", "December"], 1)}


def nm_points(raw: Path) -> list[dict]:
    out = []
    for f in sorted((raw / "ev_party" / "nm" / "reg").glob("nm_reg_20*.pdf.layout.txt")):
        txt = f.read_text(errors="replace")
        m = re.search(r"As of (\w+) (\d+), (\d{4})", txt)
        t = next((ln for ln in txt.splitlines() if ln.strip().startswith("TOTAL ")), None)
        if not m or not t or m[1] not in _MONTHS:
            continue
        v = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?(?!\d|\.|,|\s*%)", re.sub(r"\d[\d,.]*\s*%", "", t))]
        if len(v) != 6 or sum(v[:5]) != v[5]:
            continue
        D, R, U, T = v[0], v[2], v[3], v[5]
        out.append(_pt(f"{m[3]}-{_MONTHS[m[1]]:02d}-{int(m[2]):02d}", D, R, T - D - R, U, T, "all"))
    return out


def longreg_points(raw: Path) -> dict[str, list[dict]]:
    """MD / OR eligible active voters at each election (raw/us/ev_party/ev_long1_reg.csv)."""
    f = raw / "ev_party" / "ev_long1_reg.csv"
    out: dict[str, list[dict]] = {}
    if not f.exists():
        return out
    for r in csv.DictReader(f.open()):
        if int(r["year"]) < 2018:
            continue
        out.setdefault(r["state"].upper(), []).append(_pt(r["date"], r["D"], r["R"], int(r["O"]), None,
                                                          int(r["D"]) + int(r["R"]) + int(r["O"]), "active"))
    return out


def earlier_points(raw: Path, log=print) -> dict[str, list[dict]]:
    """{ST: [points]} of every earlier official report found on disk."""
    ex = {"NC": nc_points(raw), "LA": la_points(raw), "CO": co_points(raw), "FL": fl_points(raw), "NM": nm_points(raw)}
    ex.update(longreg_points(raw))
    for st, pts in ex.items():
        log(f"  reg {st}: {len(pts)} earlier points " + (f"({min(p['date'] for p in pts)}..{max(p['date'] for p in pts)})" if pts else ""))
    return ex


# ------------------------------------------------------------------------------------------------------- series
def _i(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else int(x)


def reg_summary(series: list[dict], url: str) -> dict:
    """series -> first report on/after BASE_FROM (the base) and the latest, net change by party."""
    base = next((s for s in series if s["date"] >= BASE_FROM), series[0])
    last = series[-1]
    ch = {}
    for k in ("D", "R", "O", "U", "total"):
        a, b = base.get(k), last.get(k)
        ch[k] = None if a is None or b is None else {"n": b - a, "pct": r2(100 * (b / a - 1)) if a else None}
    return {"series": series, "base_date": base["date"], "latest_date": last["date"], "change": ch,
            "d_minus_r_change": None if None in (base["D"], base["R"], last["D"], last["R"])
            else (last["D"] - last["R"]) - (base["D"] - base["R"]), "url": url}


def _records(d: pd.DataFrame) -> list[dict]:
    return [{k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in r.items()} for r in d.to_dict("records")]


def load_registration(pub: Path, raw: Path, today: str, log=print) -> dict:
    """Series per state: the collected CSVs + earlier official points. A report dated after `today` (some are titled with the
    Election Day they are for) is dated `today` with its own date kept in the basis; points before BASE_FROM are added only to
    states that already have a series of later points, and never replace a collected point."""
    extra = earlier_points(raw, log)
    states, blocked, urls = {}, [], {}
    for f in sorted(pub.glob("reg_*.csv")):
        if f.name.startswith("reg_blocked"):
            blocked += _records(pd.read_csv(f).fillna(""))
            continue
        d = pd.read_csv(f)
        if d.empty:
            continue
        st = str(d["st"].iloc[0]).upper()
        d = d.sort_values("date")
        for c in ("D", "R", "O", "total", "U"):
            if c not in d:
                d[c] = np.nan
        states[st] = [{"date": r.date, "D": _i(r.D), "R": _i(r.R), "O": _i(r.O), "U": _i(r.U), "total": _i(r.total),
                       "basis": getattr(r, "basis", "")} for r in d.itertuples()]
        urls[st] = str(d["source_url"].dropna().iloc[-1]) if "source_url" in d and d["source_url"].notna().any() else ""
    if "LA" in extra and extra["LA"]:
        states["LA"], urls["LA"] = [], "https://electionstatistics.sos.la.gov/"
    for st, pts in extra.items():
        if st not in states:
            continue
        have = {s["date"] for s in states[st]}
        first = min((s["date"] for s in states[st]), default="9999")
        # earlier points only: a collected series keeps its own report dates from BASE_FROM on (so the base and change are unchanged)
        states[st] += [p for p in pts if p["date"] not in have and (st == "LA" or p["date"] < min(first, BASE_FROM))]
    out = {}
    for st, series in states.items():
        for s in series:
            if s["date"] > today:
                s["basis"] = f"{s['basis']}; report titled {s['date']}, shown at the build date".strip("; ")
                s["date"] = today
        series.sort(key=lambda s: s["date"])
        out[st] = reg_summary(series, urls[st])
    return {"states": dict(sorted(out.items())), "blocked": blocked}
