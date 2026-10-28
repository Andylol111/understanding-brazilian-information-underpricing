"""Bulgaria presidential runoff 2026-11-01 (eid bg2026r2): CIK result pages, first-round prior, nowcast.

results.cik.bg (the CIK's official results site, run by Information Services JSC) answers every scripted request,
from a home connection and from our datacenter VM alike, with a Cloudflare managed challenge (HTTP 403,
`cf-mitigated: challenge`); only robots.txt passes. We do not solve or evade it, so nothing here downloads from
CIK. Everything reads files a person saved in a browser (or Internet Archive captures of the same pages);
docs/bulgaria.md lists which files, where they go, and the commands.

Inputs, all official CIK pages / files:
  * result pages `rezultati/index.html` (national), `rezultati/01.html` … `31.html` (one per multi-member electoral
    region, MIR) and `rezultati/32.html` (abroad). Each carries its own stamp "Данни към HH:MM на DD.MM.YYYY при
    обработени X% СИК протоколи" (data as of local time, share of polling-station protocols processed) and the
    votes per candidate pair. Saved repeatedly on the night they are the count over time, MIR by MIR.
  * the open-data archive `opendata/export.zip` (section-level final protocols, CSV with ';'), published after the
    CIK's decision on the final result. It has no times, so it only serves as a prior / the final outcome.

Model: brunder.nowcast with one unit per MIR (the finest level that has a time stamp on the night) and the six
NUTS-2 planning regions as the pooling level (Sofia's three MIRs and Plovdiv's two would make oblast pooling almost
unit-level). Abroad (MIR 32) is its own unpooled unit. Prior per MIR = first-round votes of the two finalists, the
other candidates' votes split by the rule in the frozen config (configs/bg2026r2_v1.toml), which was chosen on the
2016 and 2021 runoffs only (analysis/bg_calibrate.py).
"""

from __future__ import annotations

import gzip
import html as _html
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import nowcast
from .paths import ROOT

EID = "bg2026r2"
TZ = ZoneInfo("Europe/Sofia")
RESULTS = ROOT / "results" / "bulgaria"
ABROAD = "32"

# MIR (multi-member electoral region) -> (name, ISO 3166-2:BG oblast, NUTS-2 planning region). MIR numbers follow
# the CIK; Sofia city is MIRs 23-25, Plovdiv oblast is MIRs 16 (city) and 17 (rest of the oblast).
MIRS: dict[str, tuple[str, str, str]] = {
    "01": ("Blagoevgrad", "01", "bg41"), "02": ("Burgas", "02", "bg34"), "03": ("Varna", "03", "bg33"),
    "04": ("Veliko Tarnovo", "04", "bg32"), "05": ("Vidin", "05", "bg31"), "06": ("Vratsa", "06", "bg31"),
    "07": ("Gabrovo", "07", "bg32"), "08": ("Dobrich", "08", "bg33"), "09": ("Kardzhali", "09", "bg42"),
    "10": ("Kyustendil", "10", "bg41"), "11": ("Lovech", "11", "bg31"), "12": ("Montana", "12", "bg31"),
    "13": ("Pazardzhik", "13", "bg42"), "14": ("Pernik", "14", "bg41"), "15": ("Pleven", "15", "bg31"),
    "16": ("Plovdiv city", "16", "bg42"), "17": ("Plovdiv oblast", "16", "bg42"), "18": ("Razgrad", "17", "bg32"),
    "19": ("Ruse", "18", "bg32"), "20": ("Silistra", "19", "bg32"), "21": ("Sliven", "20", "bg34"),
    "22": ("Smolyan", "21", "bg42"), "23": ("Sofia 23", "22", "bg41"), "24": ("Sofia 24", "22", "bg41"),
    "25": ("Sofia 25", "22", "bg41"), "26": ("Sofia oblast", "23", "bg41"), "27": ("Stara Zagora", "24", "bg34"),
    "28": ("Targovishte", "25", "bg33"), "29": ("Haskovo", "26", "bg42"), "30": ("Shumen", "27", "bg33"),
    "31": ("Yambol", "28", "bg34"), "32": ("Abroad", "", "zz"),
}
REGIONS = {"bg31": "North-West", "bg32": "North-Central", "bg33": "North-East", "bg34": "South-East",
           "bg41": "South-West", "bg42": "South-Central", "zz": "Abroad"}


def unit_id(mir: str) -> str:
    """bg-<NUTS-2 region>-<MIR>, e.g. bg-bg41-23 (Sofia 23); abroad bg-zz-32."""
    return f"bg-{MIRS[mir][2]}-{mir}"


# ------------------------------------------------------------------ result pages (rezultati/*.html)

@dataclass(frozen=True)
class Page:
    scope: str                      # "national" or a MIR code "01".."32"
    as_of: pd.Timestamp             # the page's own "Данни към" stamp, UTC
    processed: float                # share of polling-station protocols processed, 0-1
    votes: dict[str, int]           # candidate pair (as printed, "<first names> и <first names>") -> votes
    numbers: dict[str, str]         # candidate pair -> ballot number
    none: int                       # "I support no one"


_STAMP = re.compile(r"Данни към\s+(\d{1,2}):(\d{2})\s+на\s+(\d{2})\.(\d{2})\.(\d{4})\s+при\s+обработени\s+"
                    r"([\d.,]+)\s*%")
_SELF = re.compile(r'href="[^"]*rezultati/(\d{2}|index)\.html"[^>]*>\s*Резултати\s*<')
_ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>\s*<strong>\s*([\d-]+)\s*</strong>\s*</td>\s*<td[^>]*>\s*<strong>(.*?)</strong>"
                  r".*?</td>\s*<td[^>]*data-sort=\"(\d+)\"", re.DOTALL)


def _local(h: str, m: str, d: str, mo: str, y: str) -> pd.Timestamp:
    t = datetime(int(y), int(mo), int(d), int(h), int(m), tzinfo=TZ)
    return pd.Timestamp(t).tz_convert("UTC")


def parse_page(text: str) -> Page:
    """One CIK results page (national index, MIR page or the abroad summary), as served or as saved by a browser.

    Scope comes from the page's own 'Резултати' navigation link (rezultati/NN.html or index.html). The vote table
    is the first `table-results` table; its rows are "<number> | <pair> | votes | %"; the "-" row is
    "Не подкрепям никого" (support no one)."""
    m = _STAMP.search(text)
    if not m:
        raise ValueError("no 'Данни към' stamp: not a CIK results page")
    as_of = _local(*m.groups()[:5])
    processed = float(m.group(6).replace(",", ".")) / 100
    s = _SELF.search(text)
    if not s:
        raise ValueError("no results self-link: cannot tell which region this page is")
    scope = "national" if s.group(1) == "index" else s.group(1)
    i = text.find("table-results")
    j = text.find("</table>", i)
    if i < 0 or j < 0:
        raise ValueError("no results table")
    votes, numbers, none = {}, {}, 0
    for no, who, v in _ROW.findall(text[i:j]):
        name = " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", who)).split())
        if no == "-":
            none = int(v)
        else:
            votes[name] = int(v)
            numbers[name] = no
    if not votes:
        raise ValueError("results table has no candidate rows")
    return Page(scope, as_of, processed, votes, numbers, none)


def pick(votes: dict[str, int], surname: str) -> int:
    """Votes of the pair whose presidential candidate's name contains `surname` (e.g. 'Йотова')."""
    hits = [v for k, v in votes.items() if surname in k.split(" и ")[0]]
    if len(hits) != 1:
        raise KeyError(f"{surname!r}: {len(hits)} matching candidate pairs")
    return hits[0]


def read_pages(folder: Path) -> list[Page]:
    """Every parseable results page under `folder` (any depth; *.html, *.htm, *.html.gz). Pages that are not CIK
    results pages (challenge pages, other site pages saved by mistake) are reported and skipped."""
    out = []
    for f in sorted(Path(folder).rglob("*")):
        if not f.is_file() or not re.search(r"\.html?(\.gz)?$", f.name):
            continue
        b = f.read_bytes()
        b = gzip.decompress(b) if f.suffix == ".gz" else b
        try:
            out.append(parse_page(b.decode("utf-8", errors="replace")))
        except ValueError as e:
            print(f"skipped {f}: {e}", flush=True)
    return out


def pages_frame(pages: list[Page], a: str, b: str) -> pd.DataFrame:
    """One row per page: scope, as_of, processed, a, b (votes of the two finalists, by surname), other (all other
    candidates), none. Duplicate saves of the same page and stamp collapse to one row."""
    rows = [{"scope": p.scope, "as_of": p.as_of, "processed": p.processed, "a": pick(p.votes, a),
             "b": pick(p.votes, b), "other": sum(p.votes.values()) - pick(p.votes, a) - pick(p.votes, b),
             "none": p.none} for p in pages]
    df = pd.DataFrame(rows, columns=["scope", "as_of", "processed", "a", "b", "other", "none"])
    return df.drop_duplicates().sort_values(["as_of", "scope"]).reset_index(drop=True)


# ------------------------------------------------------------------ open data archive (opendata/export.zip)

def _member(z: zipfile.ZipFile, stem: str, part: str | None = None) -> str | None:
    """Text of the archive member <stem>_<date>.txt or <stem>.txt, or None. Archives with several elections need
    `part`, a substring of the member path: the 2016 archive has one folder per round ("06.11.2016/",
    "13.11.2016/"), the 2021 first-round archive one per ballot ("pvr/" president, "np/" parliament)."""
    hits = [n for n in z.namelist()
            if re.fullmatch(rf"{stem}(_[\d.]+)?\.txt", n.rsplit("/", 1)[-1], re.IGNORECASE)
            and (part is None or part in n)]
    if len(hits) > 1:
        raise ValueError(f"{len(hits)} {stem} files in archive ({hits}); pass `part` to pick one")
    return z.read(hits[0]).decode("utf-8-sig") if hits else None


def _vote_fields(readme: str | None) -> int:
    """Fields per ballot number in votes.txt, from the readme's description ("... в последователност № П/К/ИК;
    действителни гласове[;...]"): 2 in 2021, 5 in 2016 (valid; valid B; valid M; invalid). Default 2."""
    if not readme:
        return 2
    part = readme[readme.find("votes.txt", readme.find("Структура")):]
    m = re.search(r"последователност\s+(.+)", part)
    return len([x for x in m.group(1).split(";") if x.strip()]) if m else 2


def read_export_votes(path: Path, part: str | None = None) -> pd.DataFrame:
    """Section-level candidate votes from a CIK open-data archive (export.zip). votes.txt has one row per section
    protocol; paper, machine and abroad protocols are separate rows (2021) and their sum is the official result.
    Rows are `[form;]section;admin unit id;(ballot no.;valid votes[;more fields])*` (the form column exists from
    2021; the fields per ballot number come from the readme). Returns section (9 digits), mir (its first two
    digits), no, votes (valid votes, the first field after the ballot number)."""
    with zipfile.ZipFile(path) as z:
        text = _member(z, "votes", part)
        step = _vote_fields(_member(z, "readme", part))
    if text is None:
        raise ValueError(f"{path}: no votes.txt in archive")
    rows = []
    for line in text.splitlines():
        p = line.strip().split(";")
        k = next((i for i in (0, 1) if i < len(p) and re.fullmatch(r"\d{9}", p[i])), None)
        if k is None:
            continue
        sec, rest = p[k], p[k + 2:]
        for i in range(0, len(rest) - 1, step):
            if re.fullmatch(r"\d+", rest[i]):
                rows.append((sec, sec[:2], rest[i], int(rest[i + 1] or 0)))
    df = pd.DataFrame(rows, columns=["section", "mir", "no", "votes"])
    if df.empty:
        raise ValueError(f"{path}: no section votes parsed")
    return df


def read_export_candidates(path: Path, part: str | None = None) -> dict[str, str]:
    """Ballot number -> candidate pair (cik_candidates.txt: `no;committee;position;pair[;...]`)."""
    with zipfile.ZipFile(path) as z:
        text = _member(z, "cik_candidates", part)
    if text is None:
        raise ValueError(f"{path}: no cik_candidates.txt in archive")
    out = {}
    for line in text.splitlines():
        p = line.strip().split(";")
        if len(p) >= 4 and p[0].isdigit():
            out[p[0]] = p[3].strip()
    return out


def read_export_sections(path: Path, part: str | None = None) -> pd.DataFrame:
    """sections.txt: section (9 digits), mir, ekatte, settlement ('гр.X' town, 'с.X' village; abroad
    'Country, City'), town (settlement is a town)."""
    with zipfile.ZipFile(path) as z:
        text = _member(z, "sections", part)
    if text is None:
        raise ValueError(f"{path}: no sections.txt in archive")
    rows = []
    for line in text.splitlines():
        p = line.strip().split(";")
        if len(p) >= 5 and re.fullmatch(r"\d{9}", p[0]):
            rows.append((p[0], p[0][:2], p[3], p[4].strip(), p[4].strip().lower().startswith("гр")))
    return pd.DataFrame(rows, columns=["section", "mir", "ekatte", "settlement", "town"])


def export_frame(path: Path, a: str, b: str, part: str | None = None) -> pd.DataFrame:
    """Final result per MIR from an export.zip, in the shape of pages_frame (scope = MIR, processed = 1, as_of NaT):
    a, b = votes of the pairs whose presidential candidate has surname a / b, other = all other candidates."""
    v = read_export_votes(path, part)
    names = read_export_candidates(path, part)
    by = v.pivot_table(index="mir", columns="no", values="votes", aggfunc="sum", fill_value=0)
    na, nb = (next(k for k, n in names.items() if s in n.split(" и ")[0]) for s in (a, b))
    tot = by.sum(axis=1)
    return pd.DataFrame({"scope": by.index.to_numpy(), "as_of": pd.NaT, "processed": 1.0, "a": by[na].to_numpy(),
                         "b": by[nb].to_numpy(), "other": (tot - by[na] - by[nb]).to_numpy(), "none": 0})


# ------------------------------------------------------------------ prior

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def prior_from_frame(final: pd.DataFrame, transfer: dict) -> pd.DataFrame:
    """MIR prior from first-round votes per MIR (columns scope = MIR code, a, b, other).

    transfer["rule"]:
      "two_way"   s0 = a / (a + b)                       (others split like the MIR's own finalists)
      "fixed"     s0 = (a + t·other) / (a + b + other)   with t = transfer["to_a"] (same t everywhere)
    V0 = a + b + other (first-round votes for candidates; the turnout scale k of the model absorbs the change)."""
    f = final[final["scope"].isin(MIRS)].set_index("scope").sort_index()
    tot = f["a"] + f["b"] + f["other"]
    rule = transfer.get("rule", "two_way")
    if rule == "two_way":
        s0 = f["a"] / (f["a"] + f["b"])
    elif rule == "fixed":
        s0 = (f["a"] + float(transfer["to_a"]) * f["other"]) / tot
    else:
        raise ValueError(f"unknown transfer rule {rule!r}")
    return pd.DataFrame({"uf": [MIRS[m][2] for m in f.index], "cd_mun": f.index, "s0": s0.to_numpy(),
                         "V0": tot.to_numpy(dtype=float)})


def latest(df: pd.DataFrame, t: pd.Timestamp | None = None) -> pd.DataFrame:
    """Latest row per scope with as_of <= t (all rows if t is None)."""
    d = df if t is None else df[df["as_of"] <= t]
    return d.sort_values("as_of").groupby("scope").tail(1)


def params(cfg: dict) -> nowcast.Params:
    M = dict(cfg.get("model", {}))
    keys = ("kappa", "rho", "tau", "sigma_mun0", "nu_mun", "sigma_uf0", "nu_uf")
    return nowcast.Params(**{k: M[k] for k in keys if k in M}, unpooled=("zz",))


def state(pages: pd.DataFrame) -> pd.DataFrame:
    """nowcast count state per MIR from the latest page of each MIR: n = a + b (two-way valid), a, f = protocols
    processed."""
    p = pages[pages["scope"].isin(MIRS)]
    return pd.DataFrame({"uf": [MIRS[m][2] for m in p["scope"]], "cd_mun": p["scope"].to_numpy(),
                         "n": (p["a"] + p["b"]).to_numpy(dtype=float), "a": p["a"].to_numpy(dtype=float),
                         "f": p["processed"].clip(0, 1).to_numpy(dtype=float)})


def project(cfg: dict, prior: pd.DataFrame, pages: pd.DataFrame | None = None) -> dict:
    """P(A wins) and projected A share of the two-way vote given the latest page of each MIR (none = pre-count)."""
    st = state(pages) if pages is not None and len(pages) else pd.DataFrame(columns=["uf", "cd_mun", "n", "a", "f"])
    return nowcast.nowcast(st, prior, params(cfg))


def replay(cfg: dict, prior: pd.DataFrame, night: pd.DataFrame) -> pd.DataFrame:
    """Model at every distinct page stamp of the night: t, counted (two-way votes counted / projected total), raw
    (A share of the two-way count so far, from the MIR pages), share, sd, P, mirs (MIRs with a page by then), and the
    national page's own processed share and raw A share when one was saved with that stamp."""
    rows = []
    nat = night[night["scope"] == "national"].set_index("as_of")
    for t in sorted(night["as_of"].unique()):
        cur = latest(night[night["scope"] != "national"], t)
        res = project(cfg, prior, cur)
        n = (cur["a"] + cur["b"]).sum()
        row = {"t": t, "counted": res["counted_frac"], "raw": cur["a"].sum() / n if n else np.nan,
               "share": res["share_hat"], "sd": res["share_sd"], "P": res["p_win"], "mirs": len(cur)}
        if t in nat.index:
            r = nat.loc[[t]].iloc[-1]
            row.update(national_processed=r["processed"], national_raw=r["a"] / (r["a"] + r["b"]))
        rows.append(row)
    return pd.DataFrame(rows)


def outcome(final: pd.DataFrame) -> dict:
    """Final two-way result from the national page (or the sum of MIR pages)."""
    nat = final[final["scope"] == "national"]
    a, b = (int(nat["a"].iloc[-1]), int(nat["b"].iloc[-1])) if len(nat) else \
        (int(latest(final[final["scope"].isin(MIRS)])["a"].sum()), int(latest(final[final["scope"].isin(MIRS)])["b"].sum()))
    return {"a": a, "b": b, "share": a / (a + b), "a_wins": a > b}


# ------------------------------------------------------------------ markets and night statistics

def market_prices(cfg: dict, start: pd.Timestamp, end: pd.Timestamp, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """P(A) on `grid` from Polymarket's CLOB 1-minute price history and Kalshi's 1-minute candles (mid if the
    spread is <= 10c, else last trade), both public APIs. A venue that fails is left empty (NaN)."""
    from . import kalshi, polymarket
    out = pd.DataFrame(index=grid, columns=["pm", "kalshi"], dtype=float)
    pm = cfg.get("markets", {}).get("polymarket", {})
    if pm.get("yes_token"):
        try:
            h = polymarket.price_history(pm["yes_token"], start, end, fidelity_min=1, chunk=pd.Timedelta(hours=6))
            s = h.set_index("time")["price"].astype(float).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            s = s if pm.get("yes_is", "a") == "a" else 1 - s
            out["pm"] = s.reindex(s.index.union(grid)).ffill().reindex(grid)
        except Exception as e:
            print(f"Polymarket history failed: {type(e).__name__}: {e}", flush=True)
    km = cfg.get("markets", {}).get("kalshi", {})
    if km.get("ticker"):
        try:
            c = kalshi.candlesticks(km["series"], km["ticker"], start, end, 1)
            if len(c):
                p = kalshi.prob_from_candles(c, grid)
                out["kalshi"] = (p if km.get("yes_is", "a") == "a" else 1 - p).to_numpy(dtype=float)
        except Exception as e:
            print(f"Kalshi history failed: {type(e).__name__}: {e}", flush=True)
    return out


def settle_time(s: pd.Series, a_wins: bool, level: float) -> pd.Timestamp | None:
    """First time from which P(eventual winner) >= level holds to the end of the series (forward-filled)."""
    q = (s if a_wins else 1 - s).ffill()
    bad = q[~(q >= level)]
    if len(bad) == 0:
        return q.index[0] if len(q) else None
    later = q.index[q.index > bad.index[-1]]
    return later[0] if len(later) else None


def night_stats(tl: pd.DataFrame, res: dict, cfg: dict) -> dict:
    """Standard metrics for the night (same keys as the dashboard's stats.json where they apply): calibration
    (90% interval coverage of the final share at stamps with 1-95% counted, mean z), settle times, early error
    (model and raw minus the final share, points, at the first stamp past each threshold)."""
    E = cfg.get("evaluation", {})
    level = E.get("settle_level", 0.9)
    t = tl.set_index("t")
    z = (res["share"] - t["share"]) / t["sd"]
    mid = t["counted"].between(0.01, 0.95)
    settle = {"model": settle_time(t["P"], res["a_wins"], level),
              "raw": settle_time((t["raw"] > 0.5).astype(float), res["a_wins"], level)}
    for v in ("pm", "kalshi"):
        settle[v] = settle_time(t[v], res["a_wins"], level) if v in t and t[v].notna().any() else None
    early = {}
    for th in E.get("early_error_thresholds", [0.01, 0.05, 0.10, 0.25]):
        past = t[t["counted"] >= th]
        if len(past):
            r = past.iloc[0]
            early[f"{th:.0%}"] = {"model": round(100 * (r["share"] - res["share"]), 2),
                                  "raw": round(100 * (r["raw"] - res["share"]), 2)}
    return {"calibration": {"coverage90_1_95": round(float((z[mid].abs() <= 1.645).mean()), 3) if mid.any() else None,
                            "mean_z": round(float(z[mid].mean()), 3) if mid.any() else None, "stamps": int(mid.sum())},
            "settle": {k: (v.strftime("%Y-%m-%dT%H:%M:%SZ") if v is not None else None) for k, v in settle.items()},
            "early_error": early, "evidence": "forward"}


# ------------------------------------------------------------------ command line

def first_round(cfg: dict, r1: Path) -> pd.DataFrame:
    """Final first-round result per MIR from a folder of saved result pages or from the first-round export.zip."""
    C = cfg["candidates"]
    if Path(r1).suffix == ".zip":
        return export_frame(Path(r1), C["a_surname"], C["b_surname"])
    fin = latest(pages_frame(read_pages(r1), C["a_surname"], C["b_surname"]))
    missing = sorted(set(MIRS) - set(fin["scope"]))
    if missing:
        raise SystemExit(f"first-round pages missing for MIRs {missing} in {r1}")
    notdone = fin[(fin["scope"].isin(MIRS)) & (fin["processed"] < 1)]
    if len(notdone):
        raise SystemExit(f"first-round pages not final (processed < 100%) for {notdone['scope'].tolist()}")
    return fin


def cli(a) -> None:
    """`brunder bulgaria prior|ingest CONFIG --r1 DIR|export.zip [--r2 DIR]` (docs/bulgaria.md)."""
    cfg = load_config(a.config)
    pri = prior_from_frame(first_round(cfg, a.r1), cfg["prior"]["transfer"])
    pre = project(cfg, pri)
    print(f"pre-count: P({cfg['candidates']['a_name']} wins) = {pre['p_win']:.3f}, share {pre['share_hat']:.4f} "
          f"± {pre['share_sd']:.4f} ({len(pri)} MIRs)")
    if a.step == "prior":
        print(pri.assign(unit_id=[unit_id(m) for m in pri["cd_mun"]]).round(4).to_string(index=False))
        return
    if a.r2 is None:
        raise SystemExit("ingest needs --r2 (folder of runoff result pages saved on the night)")
    C = cfg["candidates"]
    night = pages_frame(read_pages(a.r2), C["a_surname"], C["b_surname"])
    if night.empty:
        raise SystemExit(f"no runoff result pages in {a.r2}")
    tl = replay(cfg, pri, night)
    close = pd.Timestamp(cfg["election"]["poll_close_utc"])
    grid = pd.DatetimeIndex(tl["t"])
    if not a.no_markets:
        mk = market_prices(cfg, close - pd.Timedelta(hours=2), grid.max() + pd.Timedelta(minutes=5), grid)
        tl["pm"], tl["kalshi"] = mk["pm"].to_numpy(), mk["kalshi"].to_numpy()
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{cfg['id']}_replay.csv"
    tl.assign(t=tl["t"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_csv(out, index=False)
    night.assign(as_of=night["as_of"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_csv(
        RESULTS / f"{cfg['id']}_pages.csv", index=False)
    meta = {"config": str(a.config), "pages": len(night), "stamps": int(night["as_of"].nunique()),
            "first": str(night["as_of"].min()), "last": str(night["as_of"].max()),
            "precount": {"P": pre["p_win"], "share": pre["share_hat"], "sd": pre["share_sd"]}}
    nat = night[night["scope"] == "national"]
    if len(nat) and nat["processed"].max() >= 1:
        meta["outcome"] = res = outcome(night)
        meta["stats"] = night_stats(tl, res, cfg)
    else:
        meta["outcome"] = None
        meta["note"] = "no national page at 100% processed among the saved pages: outcome and statistics not computed"
    (RESULTS / f"{cfg['id']}_replay.json").write_text(json.dumps(meta, indent=1, default=str) + "\n")
    pd.set_option("display.width", 200)
    print(tl.round(4).to_string(index=False))
    print(json.dumps({k: meta[k] for k in ("outcome", "stats") if k in meta}, indent=1, default=str))
    print(f"wrote {out}")
