"""Archived UF Election Lab / US Elections Project 2022 general early-vote pages -> one parsed table (docs/us_ev_site.md).

  BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_uf2022.py [survey|parse] [--states nc,fl]

Where 2022 lived: Michael McDonald's 2022 tracker was a set of RPubs documents, not a page of election.lab.ufl.edu (the Lab's own
tracker starts in 2024): a national page (rpubs.com/ElectProject/early_vote_2022) and one page per state
(rpubs.com/ElectProject/<ST>_early_vote_2022). Each RPubs page is an iframe onto a static HTML file
(rstudio-pubs-static.s3.amazonaws.com/<id>_<hash>.html) that carries a "Last update" date and the state's headline counts.
Only the web.archive.org copy is used: CDX index + `id_` captures, at most one request a second, honest user agent.

survey  finds, for each state, every archived static state page captured 2022-09-20 .. 2022-11-08 and stores the raw HTML under
        $BRUNDER_DATA/raw/us/uf_2022_wayback/ (plus survey.json: what was asked and what exists, including states with nothing).
parse   reads those files into $BRUNDER_DATA/processed/us2026/uf_2022_wayback.csv, one row per capture (never mixed with official rows).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import us_ev_db as db  # noqa: E402

UA = "brunder/0.1 (research; recorder)"
ELECTION_DAY_2022 = date(2022, 11, 8)
WINDOW = ("20220920", "20221108")
STATES = ("AK AR AZ CA CO CT DC FL GA HI IA ID IL IN KS LA MD MA ME MI MN MS MT NC ND NE NJ NM NV NY OH OK OR PA RI SC SD TN TX WA WI WV VA UT VT").split()
CDX = "https://web.archive.org/cdx/search/cdx"
WB = "https://web.archive.org/web/{ts}id_/{url}"
STATIC = "rstudio-pubs-static.s3.amazonaws.com/"
LABEL = "UF Election Lab / US Elections Project 2022 state page, archived by the Internet Archive"


def raw_dir() -> Path:
    d = db.data_root() / "raw" / "us" / "uf_2022_wayback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def table_path() -> Path:
    return db.data_root() / "processed" / "us2026" / "uf_2022_wayback.csv"


class Wayback:
    """One request a second at most; the Archive answers 503 'Temporarily Offline' now and then: wait and retry."""

    def __init__(self):
        import httpx
        self.cli = httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True)
        self.last = 0.0

    def get(self, url, tries=6, **kw):
        for i in range(tries):
            wait = 1.05 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.cli.get(url, **kw)
            except Exception:
                time.sleep(5 * (i + 1)); continue
            if r.status_code >= 500 or "Temporarily Offline" in r.text[:800]:
                time.sleep(6 * (i + 1)); continue
            return r
        return None

    def cdx(self, **p):
        p.setdefault("output", "json")
        r = self.get(CDX, params=p)
        if r is None or r.status_code != 200:
            return None
        d = r.json() if r.text.strip() else []
        return [dict(zip(d[0], x)) for x in d[1:]] if d else []


NUM = r"([\d,]+)"


def parse_page(html: str) -> dict | None:
    """Headline of a 2022 state page: 'Last update', Total / In-Person / Mail Ballots Returned. None if there is no date."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    m = re.search(r"Last update[d]?:\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", t)
    if not m:
        return None
    asof = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    g = lambda label: (lambda x: int(x.group(1).replace(",", "")) if x else None)(re.search(label + r"[^0-9]{0,12}" + NUM, t))  # noqa: E731
    total, inp, mail = g(r"Total Early Votes:"), g(r"In-Person Early Votes:"), g(r"Mail Ballots Returned:")
    return {"as_of": asof.isoformat(), "days_before": (ELECTION_DAY_2022 - asof).days, "total": total, "inperson": inp, "mail": mail}


def definition(p: dict) -> str:
    """What the page's 'Total Early Votes' is made of, so a mismatch with the 2026 measure can be flagged."""
    if p["total"] is None:
        return "no total on the page"
    if p["inperson"] is not None and p["mail"] is not None and p["inperson"] + p["mail"] == p["total"]:
        return "mail returned + in-person early"
    if p["inperson"] is None and p["mail"] is not None and p["mail"] == p["total"]:
        return "mail returned only (no in-person early voting reported)"
    if p["mail"] is None and p["inperson"] is not None and p["inperson"] == p["total"]:
        return "in-person early only"
    return "total as the page states it (parts do not add up to it)"


def survey(states: list[str], log=print) -> dict:
    wb, out = Wayback(), raw_dir()
    ids_f = out / "state_ids.json"
    ids = json.loads(ids_f.read_text())["ids"] if ids_f.exists() else {}
    res = json.loads((out / "survey.json").read_text()) if (out / "survey.json").exists() else {}
    for st in states:
        if st not in ids:
            caps = wb.cdx(url=f"rpubs.com/ElectProject/{st}_early_vote_2022", fl="timestamp", filter="statuscode:200", limit="1", **{"from": "20221101", "to": "20221231"})
            if caps:
                r = wb.get(WB.format(ts=caps[0]["timestamp"], url=f"https://rpubs.com/ElectProject/{st}_early_vote_2022"))
                m = re.search(r"<iframe[^>]*src='[^']*/(\d+)_", r.text) if r is not None and r.status_code == 200 else None
                if m:
                    ids[st] = m.group(1)
            ids_f.write_text(json.dumps({"ids": ids, "source": "iframe of rpubs.com/ElectProject/<ST>_early_vote_2022 as archived (web.archive.org id_)"}, indent=1))
        if st not in ids:
            res[st] = {"id": None, "captures": [], "note": "state page not found in the archive"}; log(f"{st}: no state page in the archive"); continue
        caps = wb.cdx(url=f"{STATIC}{ids[st]}_", matchType="prefix", fl="timestamp,original,digest", filter="statuscode:200", collapse="digest",
                      **{"from": WINDOW[0], "to": WINDOW[1]})
        if caps is None:
            log(f"{st}: index unavailable, rerun"); continue
        got = []
        for c in caps:
            f = out / f"{st}_{c['timestamp']}.html"
            if not f.exists():
                r = wb.get(WB.format(ts=c["timestamp"], url=c["original"]))
                if r is None or r.status_code != 200:
                    continue
                f.write_bytes(r.content)
            got.append({"capture": c["timestamp"], "url": c["original"], "file": f.name})
        res[st] = {"id": ids[st], "captures": got, "asked": f"{STATIC}{ids[st]}_* captured {WINDOW[0]}..{WINDOW[1]}"}
        log(f"{st}: {len(got)} archived state pages in the window")
        (out / "survey.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    return res


def parse(log=print) -> pd.DataFrame:
    out = raw_dir()
    rows = []
    for f in sorted(out.glob("??_2022*.html")):
        st, ts = f.stem.split("_")
        p = parse_page(f.read_text(errors="replace"))
        if p is None:
            log(f"{f.name}: no 'Last update' date, skipped"); continue
        cap = datetime.strptime(ts, "%Y%m%d%H%M%S")
        if cap.date() > ELECTION_DAY_2022:
            log(f"{f.name}: captured after Election Day, not a same-day figure, skipped"); continue
        rows.append({"st": st.lower(), "capture": ts, "capture_days_before": (ELECTION_DAY_2022 - cap.date()).days, **p, "definition": definition(p),
                     "source": LABEL, "url": f"https://web.archive.org/web/{ts}/https://rpubs.com/ElectProject/{st}_early_vote_2022"})
    df = pd.DataFrame(rows, columns=["st", "capture", "capture_days_before", "as_of", "days_before", "total", "inperson", "mail", "definition", "source", "url"])
    table_path().parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(table_path(), index=False)
    log(f"{len(df)} rows -> {table_path()}")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["survey", "parse"])
    ap.add_argument("--states", default=",".join(s.lower() for s in STATES))
    a = ap.parse_args(argv)
    if a.cmd == "survey":
        survey([s.upper() for s in a.states.split(",")])
    parse()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
