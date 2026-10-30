"""US election-night dress rehearsal: replay past official counts as recorder snapshots, run `brunder us live` on them,
serve the output with brunder.liveserve and measure; `page` checks the dashboard in headless browsers.

Feed (written exactly as brunder.snap / brunder.kalshi record do: <snap>/<key>/<UTC time>.<ext>[.gz] + index.jsonl):
  Georgia  ga_enr_Georgia_item_<id>: the GA SoS certified 2022 Governor and US Senate ballot items (Enhanced Voting,
           by vote type; cached by `brunder us priors` / us._ga_enr under data/raw/us/ga), every county's vote-type
           groups scaled to what had been posted at the simulated time.
  Texas    the Civix countyInfo + election files of the night feed, built from data_public/us2026_priors.csv
           (tx_sen_2024 as "U. S. Senator", tx_gov_2022 as "Governor"), padded with non-modelled races to the real
           file's ~5 MB (gzipped like the recorder does for bodies over 1 MB).
  NC, FL   ncsbe_county + ncsbe_results_<county> (NC 2022 Senate, early 46 %) and the DOS election-night text file
           (FL 2022 Senate and governor), from data_public/us2026_priors.csv.
  PA, MN,  the feeds of brunder.us_feeds_extra (PA GetCountyBreak, MN allracesbycounty.txt, NE/NM GetMapData) and
  NE, NM,  the AZ Enhanced Voting governor item (the 2026 test item as template), from the configs' prior races in
  AZ       data_public/us2026_priors.csv (CA, OH, SC and VT races get no replayed count and stay pre-count).
  House    U.S. House contests (analysis/us_house_rehearsal.py) inside the TX/NC/FL bodies, GA/AZ House items
           (ga_enr_<Georgia|Arizona>_house_<nn>) and OH XML / SC Clarity files with House contests only, from
           data_public/us2026_house_priors.csv (the House races run in their own unit, brunder-us-house-live;
           analysis/us_house_night.py bench replays them on this archive; statewide parsing ignores these rows).
  Markets  polymarket_books / kalshi_books for every configured race: one level each side of a price that starts at
           the pre-count timeline's last Polymarket / Kalshi value and drifts toward the replayed winner.
The races run are the v1 configs of deploy/brunder-us-live.service. The replayed elections are the races' own prior
races for GA/NC/FL/TX-gov, so "counted vs 2024" arrows are ~0 there by construction (the projected arrows draw).
Count order: analysis/us_calibrate.py's simulated night (per county the early + mail batch first, then the Election
Day vote in 4 chunks, larger counties later), compressed into the night. Snapshot times are back-dated so the
`night` minutes of simulated time end at wall time `minutes` after the start: the live model sees a full-resolution
night (one snapshot per simulated minute) delivered `night / minutes` times faster than real time.

    uv run python analysis/us_night_replay.py run --work /tmp/brr --live-port 8791 --minutes 15
    uv run --with playwright python analysis/us_night_replay.py page --site-port 8792 --live http://127.0.0.1:8791/ --minutes 15
    uv run python analysis/us_night_replay.py ab --work /tmp/ab --old-root <worktree at the old commit> --minutes 30 --night 30
`ab` runs the old checkout's and this checkout's `brunder us live` side by side on one replayed archive (the feed writes
brunder.snap's poll marker after each simulated minute) and reports latency from a minute's snapshots being on disk to
its row being written / served, CPU, peak RSS, and row-by-row identity with `brunder us export` of the final archive.
Firefox fails to start from a home directory whose name ends in "." ("Couldn't load XPCOM"): copy Nightly.app
elsewhere and set FIREFOX_EXECUTABLE=<copy>/Nightly.app/Contents/MacOS/firefox.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from brunder import us

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us_house_rehearsal import HouseRehearsal  # noqa: E402  (House contests in the TX/NC/FL bodies; GA/AZ/OH/SC keys)

ROOT = us.ROOT_DIR                    # the checkout the brunder package runs from (this file may live elsewhere)
# the v1 configs of the live unit's ExecStart (deploy/brunder-us-live.service), so the rehearsal runs what the VM runs
CONFIGS = [ROOT / p for p in __import__("re").findall(r"configs/us2026_\w+_v1\.toml",
                                                      (ROOT / "deploy" / "brunder-us-live.service").read_text())]
GA_EID = "2022NovGen"
TX_SOURCES = {"sen": ("tx_sen_2024", "U. S. Senator", "Federal", ("Colin Allred", "Ted Cruz")),
              "gov": ("tx_gov_2022", "Governor", "StateWide", ("Beto O'Rourke", "Greg Abbott"))}
TX_BYTES = 5_000_000


# ------------------------------------------------------------------ count order (analysis/us_calibrate.py events)

def schedule(size: np.ndarray, rng: np.random.Generator, span: float) -> list[tuple[float, np.ndarray, np.ndarray]]:
    """Per county: (early batch time, 4 Election Day chunk times, chunk weights), minutes after poll close. Same
    generator as us_calibrate.events (early at U(0, 60) + 30·rank, chunks over U(30, 240 + 120·rank)), times scaled
    so the last county finishes by `span` minutes."""
    rank = size.argsort().argsort() / max(len(size) - 1, 1)
    out = []
    for i in range(len(size)):
        t0 = rng.uniform(0, 60) + 30 * rank[i]
        out.append((t0, np.sort(t0 + rng.uniform(30, 240 + 120 * rank[i], 4)), rng.dirichlet(np.ones(4))))
    k = span / max(max(ts[-1] for _, ts, _ in out), 1)
    return [(t0 * k, ts * k, w) for t0, ts, w in out]


def posted(s: tuple[float, np.ndarray, np.ndarray], t: float) -> tuple[float, float, bool]:
    """(early fraction, Election Day fraction, all precincts in) of one county at t minutes after poll close."""
    t0, ts, w = s
    return float(t >= t0), float(w[ts <= t].sum()), bool(t >= ts[-1])


# ------------------------------------------------------------------ Georgia: certified ballot items, scaled

class Georgia:
    def __init__(self, rng: np.random.Generator, span: float):
        self.items = {}
        for race in ("gov", "sen"):
            us._ga_enr(GA_EID, race)                               # fetched once into data/raw/us/ga
            self.items[race] = json.loads((us.US_RAW / "ga" / f"{GA_EID}_{race}.json").read_bytes())
        brs = self.items["gov"]["ballotItemWithBreakdown"]["breakdownResults"]
        self.names = [b["locality"]["name"][0]["text"] for b in brs]
        size = np.array([sum(o["voteCount"] for o in b["ballotOptions"]) for b in brs], float)
        self.units = np.maximum(3, np.round(size / 2500)).astype(int)
        self.sched = dict(zip(self.names, schedule(size, rng, span)))

    def bodies(self, t: float) -> dict[str, bytes]:
        out = {}
        for race, item in self.items.items():
            b = json.loads(json.dumps(item))                       # deep copy
            x = b["ballotItemWithBreakdown"]
            totals: dict[str, int] = {}
            done = 0
            for br in x["breakdownResults"]:
                name = br["locality"]["name"][0]["text"]
                fe, fl, complete = posted(self.sched[name], t)
                for o in br["ballotOptions"]:
                    for g in o.get("groupResults") or []:
                        early = us._GA_EARLY.match(g["groupName"][0]["text"].strip())
                        g["voteCount"] = int(round(g["voteCount"] * (fe if early else fl)))
                    o["voteCount"] = sum(g["voteCount"] for g in o.get("groupResults") or [])
                    totals[o["id"]] = totals.get(o["id"], 0) + o["voteCount"]
                n = int(self.units[self.names.index(name)])
                br["precinctReportingStatus"] = {"reportingUnits": n if complete else int(n * fl * 0.95),
                                                 "totalUnits": n}
                br["voteTotal"] = sum(o["voteCount"] for o in br["ballotOptions"])
                done += complete
            for o in x["summaryResults"]["ballotOptions"]:
                o["voteCount"] = totals.get(o["id"], 0)
            x["reportingStatus"] = {**(x.get("reportingStatus") or {}), "reportingUnits": done,
                                    "totalUnits": len(x["breakdownResults"])}
            out[f"ga_enr_Georgia_item_{x['id']}"] = json.dumps(b, separators=(",", ":")).encode()
        return out


# ------------------------------------------------------------------ Texas: Civix files from the county priors

class Texas:
    house: HouseRehearsal | None = None          # House contests (analysis/us_house_rehearsal.py), set by feed()

    def __init__(self, rng: np.random.Generator, span: float):
        pr = pd.read_csv(ROOT / "data_public" / "us2026_priors.csv", comment="#")
        self.c = us.counties("tx")
        self.v = {}
        for race, (src, *_ ) in TX_SOURCES.items():
            d = pr[pr["source"] == src].set_index("unit_id").reindex(self.c["unit_id"]).fillna(0)
            self.v[race] = d[["dem", "rep", "total"]].to_numpy(float)
        size = self.v["sen"][:, 2]
        self.prec = np.maximum(2, np.round(size / 2000)).astype(int)
        self.sched = schedule(size, rng, span)
        # early + mail: 60 % of the vote (config early_share), 2 points more Democratic than the county's final share
        self.early = 0.6
        # filler races (not modelled: race_key None) to reach the real file's size
        per = max(1, int(TX_BYTES / len(self.c) / 190))
        self.filler = [{f"9{k:03d}": {"N": f"State Representative District {k}",
                                      "C": {f"{k}{j}": {"N": f"Candidate {k}-{j}", "P": "REP" if j else "DEM",
                                                        "V": int(rng.integers(100, 90000)), "EV": 0} for j in range(2)}}
                        for k in range(per)} for _ in range(len(self.c))]

    def _votes(self, race: str, i: int, fe: float, fl: float) -> tuple[list[int], list[int]]:
        d, r, tot = self.v[race][i]
        o = max(tot - d - r, 0)
        s = d / (d + r) if d + r else 0.5
        se = min(max(s + 0.02, 0), 1)
        ed, er = self.early * (d + r) * se, self.early * (d + r) * (1 - se)
        eo = self.early * o
        V = [ed * fe + (d - ed) * fl, er * fe + (r - er) * fl, eo * fe + (o - eo) * fl]
        E = [ed * fe, er * fe, eo * fe]
        return [int(round(max(x, 0))) for x in V], [int(round(x)) for x in E]

    def bodies(self, t: float) -> dict[str, bytes]:
        county, state = {}, {race: [0, 0, 0, 0, 0, 0] for race in TX_SOURCES}
        for i in range(len(self.c)):
            fe, fl, complete = posted(self.sched[i], t)
            races = {}
            for race, (_, title, _, names) in TX_SOURCES.items():
                V, E = self._votes(race, i, fe, fl)
                races["1" if race == "sen" else "2"] = {"N": title, "C": {
                    "1": {"N": names[0], "P": "DEM", "V": V[0], "EV": E[0]},
                    "2": {"N": names[1], "P": "REP", "V": V[1], "EV": E[1]},
                    "3": {"N": "Other", "P": "LIB", "V": V[2], "EV": E[2]}}}
                for k in range(3):
                    state[race][k] += V[k]
                    state[race][3 + k] += E[k]
            f = fe * self.early + fl * (1 - self.early)
            for fr in self.filler[i].values():
                for j, cand in enumerate(fr["C"].values()):
                    cand["V"] = int(round(f * (j + 1) * 1000))
            n = int(self.prec[i])
            tot = self.v["sen"][i][2]
            county[str(i + 1)] = {"N": self.c.loc[i, "name"],
                                  "Summary": {"PRR": n if complete else int(n * fl * 0.95), "PRP": n, "PLR": 0, "NPL": 0,
                                              "VC": int(tot * f), "RV": int(tot * 1.6)},
                                  "Races": {**races, **self.filler[i],
                                            **(self.house.tx_races(i, fe, fl, complete) if self.house else {})}}
        enc = lambda o: base64.b64encode(json.dumps(o, separators=(",", ":")).encode()).decode()  # noqa: E731
        elec = {"ElectionName": "2026 NOVEMBER 3RD GENERAL ELECTION (REHEARSAL)"}
        for race, (_, title, block, names) in TX_SOURCES.items():
            s = state[race]
            elec[block] = enc({"Races": [{"N": title, "Candidates": [
                {"N": names[0], "P": "DEM", "V": s[0], "EV": s[3]}, {"N": names[1], "P": "REP", "V": s[1], "EV": s[4]},
                {"N": "Other", "P": "LIB", "V": s[2], "EV": s[5]}]}]})
        return {us.FEEDS["tx"]["county"]: json.dumps({"upload": enc(county)}).encode(),
                us.FEEDS["tx"]["election"]: json.dumps(elec).encode()}


# ------------------------------------------------------------------ NC (NCSBE ENR) and FL (DOS election-night file)

class _PriorFeed:
    """County votes from data_public/us2026_priors.csv (dem, rep, other = total − dem − rep) with a count order."""

    def __init__(self, st: str, sources: dict[str, str], rng: np.random.Generator, span: float):
        pr = pd.read_csv(ROOT / "data_public" / "us2026_priors.csv", comment="#")
        self.c = us.counties(st)
        self.v = {}
        for race, src in sources.items():
            d = pr[pr["source"] == src].set_index("unit_id").reindex(self.c["unit_id"]).fillna(0)
            self.v[race] = np.c_[d["dem"], d["rep"], np.maximum(d["total"] - d["dem"] - d["rep"], 0)]
        size = next(iter(self.v.values())).sum(axis=1)
        self.prec = np.maximum(2, np.round(size / 2000)).astype(int)
        self.sched = schedule(size, rng, span)


class NorthCarolina(_PriorFeed):
    """ncsbe_county (county.txt) + one ncsbe_results_<cid> per county (results_<cid>.txt), NC 2022 Senate; early
    (one-stop + mail) = 46 % of the vote (config early_share), 3 points more Democratic than the county's final."""
    house: HouseRehearsal | None = None

    def __init__(self, rng, span):
        super().__init__("nc", {"sen": "nc_sen_2022"}, rng, span)

    def bodies(self, t: float) -> dict[str, bytes]:
        info, out = [{"cid": "0", "cnm": "STATE"}], {}
        for i in range(len(self.c)):
            fe, fl, complete = posted(self.sched[i], t)
            d, r, o = self.v["sen"][i]
            s = d / (d + r) if d + r else 0.5
            se = min(max(s + 0.03, 0), 1)
            e = [0.46 * (d + r) * se, 0.46 * (d + r) * (1 - se), 0.46 * o]
            n = int(self.prec[i])
            rows = []
            for (name, pty), tot, ev in zip((("Cheri Beasley", "DEM"), ("Ted Budd", "REP"), ("Shannon W. Bray", "LIB")),
                                           (d, r, o), e):
                early, late = ev * fe, (tot - ev) * fl
                rows.append({"cid": str(i + 1), "cnm": "US SENATE (VOTE FOR 1)", "bnm": name, "pty": pty,
                             "vct": str(int(round(early + late))), "evc": str(int(round(early * 0.8))),
                             "avc": str(int(round(early)) - int(round(early * 0.8))), "ovc": str(int(round(late))),
                             "pvc": "0", "prt": str(n if complete else int(n * fl * 0.95)), "ptl": str(n)})
            if self.house:
                rows += self.house.nc_rows(i, fe, fl, complete, n)
            out[f"ncsbe_results_{i + 1}"] = json.dumps(rows).encode()
            info.append({"cid": str(i + 1), "cnm": self.c.loc[i, "name"].upper(),
                         "bct": str(int((d + r + o) * (0.46 * fe + 0.54 * fl))), "rtl": str(int((d + r + o) * 1.9))})
        out["ncsbe_county"] = json.dumps(info).encode()
        return out


class Florida(_PriorFeed):
    """The DOS election-night file (tab-separated, no vote-type split), FL 2022 Senate and governor."""
    house: HouseRehearsal | None = None

    COLS = ["ElectionDate", "PartyCode", "PartyName", "RaceCode", "RaceName", "CountyCode", "CountyName", "Juris1num",
            "Juris2num", "Precincts", "PrecinctsReporting", "CanNameLast", "CanNameFirst", "CanNameMiddle", "CanVotes"]
    CANDS = {"sen": ("USS", "United States Senator", (("Demings", "Val", "DEM"), ("Rubio", "Marco", "REP"),
                                                       ("Other", "Write-in", "NPA"))),
             "gov": ("GOV", "Governor", (("Crist", "Charlie", "DEM"), ("DeSantis", "Ron", "REP"),
                                         ("Other", "Write-in", "NPA")))}

    def __init__(self, rng, span):
        super().__init__("fl", {"sen": "fl_sen_2022", "gov": "fl_gov_2022"}, rng, span)

    def bodies(self, t: float) -> dict[str, bytes]:
        lines = ["\t".join(self.COLS)]
        for i in range(len(self.c)):
            fe, fl, complete = posted(self.sched[i], t)
            f = 0.6 * fe + 0.4 * fl                                  # early + mail post at poll close
            n = int(self.prec[i])
            for race, (code, title, cands) in self.CANDS.items():
                for (last, first, pty), v in zip(cands, self.v[race][i]):
                    lines.append("\t".join(["11/03/2026", pty, pty, code, title, self.c.loc[i, "name"][:3].upper(),
                                            self.c.loc[i, "name"], "   ", " ", str(n),
                                            str(n if complete else int(n * fl * 0.95)), last, first, "",
                                            str(int(round(v * f)))]))
            if self.house:
                lines += self.house.fl_lines(i, fe, fl, complete, n)
        return {us.FEEDS["fl"]["enight"]: ("\n".join(lines) + "\n").encode()}

# ------------------------------------------------------------------ PA, MN, NE, NM, AZ (brunder.us_feeds_extra; AZ EV)

class Pennsylvania(_PriorFeed):
    """PA GetCountyBreak (a JSON string), PA 2022 governor; mail-in 23 % of the vote, 15 points more Democratic than
    the county's final, posted at once, then Election Day votes; no precinct counts."""

    CANDS = (("JOSH SHAPIRO", "DEM"), ("STACY GARRITY", "REP"), ("KEN KRAWCHUK", "LIB"))

    def __init__(self, rng, span):
        super().__init__("pa", {"gov": "pa_gov_2022"}, rng, span)

    def bodies(self, t: float) -> dict[str, bytes]:
        block = {}
        for i in range(len(self.c)):
            fe, fl, _ = posted(self.sched[i], t)
            d, r, o = self.v["gov"][i]
            s = d / (d + r) if d + r else 0.5
            se = min(max(s + 0.15, 0), 1)
            mail = [0.23 * (d + r) * se, 0.23 * (d + r) * (1 - se), 0.23 * o]
            rows = []
            for (name, pty), tot, m in zip(self.CANDS, (d, r, o), mail):
                mv, ed = m * fe, max(tot - m, 0) * fl
                rows.append({"CandidateName": name, "PartyName": pty, "Votes": str(int(round(mv)) + int(round(ed))),
                             "MailInVotes": str(int(round(mv))), "ElectionDayVotes": str(int(round(ed))),
                             "ProvisionalVotes": "0"})
            block[self.c.loc[i, "name"].upper()] = rows
        body = json.dumps({"Election": {"Statewide": [block]}})
        return {us.FEEDS["pa"]["gov"]: json.dumps(body).encode()}


class Minnesota(_PriorFeed):
    """MN allracesbycounty.txt (ISO-8859-1, CRLF), governor and Senate both from the 2022 governor by county."""

    CANDS = {"sen": ("0102", "U.S. Senator", (("Peggy Flanagan", "DFL"), ("Michele Tafoya", "R"), ("Other", "IND"))),
             "gov": ("0331", "Governor & Lt Governor", (("Amy Klobuchar and Ben Schierer", "DFL"),
                                                        ("Lisa Demuth and Ryan Wilson", "R"), ("Other", "GP")))}

    def __init__(self, rng, span):
        super().__init__("mn", {"sen": "mn_gov_2022", "gov": "mn_gov_2022"}, rng, span)

    def bodies(self, t: float) -> dict[str, bytes]:
        lines = []
        for i in range(len(self.c)):
            fe, fl, complete = posted(self.sched[i], t)
            f = 0.3 * fe + 0.7 * fl
            n = int(self.prec[i])
            cid = (int(self.c.loc[i, "unit_id"][-3:]) + 1) // 2
            rep = n if complete else int(n * fl * 0.95)
            for race, (oid, office, cands) in self.CANDS.items():
                v = [int(round(x * f)) for x in self.v[race][i]]
                for k, ((name, pty), x) in enumerate(zip(cands, v)):
                    lines.append(f"MN;{cid:02d};;{oid};{office};;{k + 1:04d};{name};;;{pty};{rep};{n};{x};0.00;{sum(v)}")
        lines.append("MN;01;;0500;County Commissioner Dist. 1 (Aitkin, Été);;0101;Test;;;NP;0;1;0;0.00;0")
        return {us.FEEDS["mn"]["county"]: ("\r\n".join(lines) + "\r\n").encode("latin-1")}


class MapData(_PriorFeed):
    """NE / NM results web service GetMapData (one race per file); NE wraps the list in {"d": …}."""

    RACES = {"ne": {"sen": ("ne_sen_2024", "For United States Senator - 6  Year Term",
                            (("Dan  Osborn ", "PET"), ("Pete  Ricketts ", "REP"), ("Mike  Marvin ", "LMN"))),
                    "gov": ("ne_gov_2022", "For Governor and Lt. Governor",
                            (("Lynne Walz and Ben Steffen ", "DEM"), ("Jim Pillen and Joe Kelly ", "REP"),
                             ("Rick Beard and Keenya Barnes ", "LMN")))},
             "nm": {"sen": ("nm_sen_2024", "United States Senator", (("BEN R LUJAN", "DEM"), ("LARRY E MARKER", "REP"),
                                                                    ("OTHER", "LIB"))),
                    "gov": ("nm_gov_2022", "Governor and Lieutenant Governor",
                            (("DEB HAALAND AND STEPHANIE GARCIA RICHARD", "DEM"),
                             ("GREGGORY D HULL AND DAVID M GALLEGOS", "REP"), ("OTHER", "LIB")))}}

    def __init__(self, st, rng, span):
        self.st = st
        super().__init__(st, {race: src for race, (src, _, _) in self.RACES[st].items()}, rng, span)

    def bodies(self, t: float) -> dict[str, bytes]:
        out = {}
        for race, (_, title, cands) in self.RACES[self.st].items():
            rows = []
            for i in range(len(self.c)):
                fe, fl, complete = posted(self.sched[i], t)
                f = 0.5 * fe + 0.5 * fl
                n = int(self.prec[i])
                for (name, pty), v in zip(cands, self.v[race][i]):
                    rows.append({"CountyName": self.c.loc[i, "name"], "RaceName": title, "calcCandidate": name,
                                 "PartyCode": pty, "calcCandidateVotes": str(int(round(v * f))) if fe or fl else "*",
                                 "PrecinctsReporting": n if complete else int(n * fl * 0.95), "TotalPrecincts": n})
            out[us.FEEDS[self.st][race]] = json.dumps({"d": rows} if self.st == "ne" else rows).encode()
        return out


class Arizona(_PriorFeed):
    """AZ Enhanced Voting governor ballot item: the 2026 test item (tests/fixtures/us) with the 2022 governor's county
    votes, early ballots (60 %) at once, Election Day and late earlies with the precincts."""

    def __init__(self, rng, span):
        super().__init__("az", {"gov": "az_gov_2022"}, rng, span)
        self.item = json.loads(gzip.decompress((ROOT / "tests" / "fixtures" / "us" /
                                                "az_2026_governor_item_testdata.json.gz").read_bytes()))

    def bodies(self, t: float) -> dict[str, bytes]:
        b = json.loads(json.dumps(self.item))
        x = b["ballotItemWithBreakdown"]
        row = {us._norm(n): i for i, n in enumerate(self.c["name"])}
        totals: dict[str, int] = {}
        for br in x["breakdownResults"]:
            i = row[us._norm(br["locality"]["name"][0]["text"])]
            fe, fl, complete = posted(self.sched[i], t)
            d, r, o = self.v["gov"][i]
            for opt in br["ballotOptions"]:
                nm = opt["name"][0]["text"]
                tot = d if "Hobbs" in nm else r if "Biggs" in nm else o / 3
                for g in opt.get("groupResults") or []:
                    gn = g["groupName"][0]["text"].strip()
                    g["voteCount"] = int(round(tot * (0.6 * fe if gn == "Early Voting" else
                                                      0.25 * fl if gn == "Election Day" else
                                                      0.15 * fl if gn.startswith("Late Early") else 0)))
                opt["voteCount"] = sum(g["voteCount"] for g in opt.get("groupResults") or [])
                totals[opt["id"]] = totals.get(opt["id"], 0) + opt["voteCount"]
            n = br["precinctReportingStatus"]["totalUnits"]
            br["precinctReportingStatus"]["reportingUnits"] = n if complete else int(n * fl * 0.95)
        for opt in x["summaryResults"]["ballotOptions"]:
            opt["voteCount"] = totals.get(opt["id"], 0)
        return {f"ga_enr_Arizona_item_{x['id']}": json.dumps(b, separators=(",", ":")).encode()}



# ------------------------------------------------------------------ markets

class Markets:
    """P(YES) per Polymarket market and Kalshi ticker: start at the pre-count timeline's last price, drift toward
    the replayed winner as the count of that race's state completes (others: a small random walk)."""

    WINNER = {"ga-gov": "b", "ga-sen": "a", "tx-sen": "b", "tx-gov": "b", "nc-sen": "b", "fl-sen": "b",
              "fl-gov": "b",                         # the replayed elections' winners (GA/NC/FL 2022, TX 2024/2022)
              "pa-gov": "a", "mn-sen": "a", "mn-gov": "a", "ne-sen": "b", "ne-gov": "b", "nm-gov": "a",
              "nm-sen": "a", "az-gov": "a"}          # PA/MN/NE/NM/AZ 2022 governor, NE/NM 2024 Senate

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        cfgs, _ = us.load_races(CONFIGS)
        pre = json.loads((ROOT / "site" / "data" / "us2026" / "timeline_precount.json").read_text())
        km = us.kalshi_markets(cfgs)
        self.m = []
        for c in cfgs:
            rid = c["election"]["race_id"]
            last = (pre.get(rid) or [{}])[-1]
            pm = us._pm_market(c)
            if pm and last.get("pm") is not None:
                self.m.append(("pm", pm[0], rid, last["pm"], pm[1]))
            k = km.get(rid)
            if k and last.get("kalshi") is not None:
                self.m.append(("k", k["ticker"], rid, last["kalshi"], k["yes_is"]))
        self.x = {key: float(np.log(p / (1 - p))) for _, key, _, p, _ in self.m}

    def bodies(self, progress: dict[str, float]) -> dict[str, bytes]:
        pm, ks = {}, {}
        for kind, key, rid, p0, yes_is in self.m:
            x = self.x[key] + self.rng.normal(0, 0.03)
            w = self.WINNER.get(rid)
            if w:
                target = 3.5 if w == "a" else -3.5
                x += 0.15 * progress.get(rid, 0) * (target - x)
            self.x[key] = x
            pa = 1 / (1 + np.exp(-x))
            y = round(float(pa if yes_is == "a" else 1 - pa), 3)
            bid, ask = max(0.001, y - 0.005), min(0.999, y + 0.005)
            if kind == "pm":
                pm[key] = {"market": key, "bids": [{"price": f"{bid:.3f}", "size": "1500"}],
                           "asks": [{"price": f"{ask:.3f}", "size": "1500"}], "last_trade_price": f"{y:.3f}"}
            else:
                ks[key] = {"yes_dollars": [[f"{bid:.4f}", "500.00"]], "no_dollars": [[f"{1 - ask:.4f}", "500.00"]]}
        return {"polymarket_books": json.dumps(pm, sort_keys=True).encode(),
                "kalshi_books": json.dumps(ks, sort_keys=True).encode()}


# ------------------------------------------------------------------ writer (brunder.snap.run's save, verbatim layout)

class Writer:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.last: dict[str, str] = {}
        self.index = out / "index.jsonl"

    def save(self, key: str, body: bytes, t: datetime) -> None:
        h = hashlib.sha256(body).hexdigest()
        if self.last.get(key) == h:
            return
        self.last[key] = h
        d = self.out / key
        d.mkdir(parents=True, exist_ok=True)
        big = len(body) > 1_000_000
        f = d / f"{t:%Y%m%dT%H%M%S.%fZ}.json{'.gz' if big else ''}"
        tmp = self.out / ".tmp" / f"{key}.{f.name}"
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(gzip.compress(body) if big else body)
        os.replace(tmp, f)
        with self.index.open("a") as fh:
            fh.write(json.dumps({"t": t.isoformat(), "key": key, "file": str(f.relative_to(self.out)), "sha256": h,
                                 "bytes": len(body), "gzip": big,
                                 "fetched": datetime.now(timezone.utc).isoformat()}) + "\n")

    def mark(self, nxt: datetime) -> None:
        """brunder.snap.mark: every snapshot named before `nxt` (simulated time) is on disk."""
        from brunder.snap import mark
        mark(self.out, "snap", nxt, 60.0)


def feed(snap: Path, minutes: float, night: float, seed: int = 20261103, pre: float = 10.0,
         tx_every: float = 2.0) -> None:
    """Write the replay: `night` simulated minutes (poll close `pre` minutes in), one poll per simulated minute
    (Texas every `tx_every`), back-dated so the simulated night ends `minutes` wall minutes from now."""
    rng = np.random.default_rng(seed)
    span = night - pre - 5
    ga, tx, mk = Georgia(rng, span), Texas(rng, span), Markets(rng)
    nc, fl = NorthCarolina(rng, span), Florida(rng, span)
    more = [Pennsylvania(rng, span), Minnesota(rng, span), MapData("ne", rng, span), MapData("nm", rng, span),
            Arizona(rng, span)]                                   # after the others: their schedules are unchanged
    house = {st: HouseRehearsal(st, rng, span) for st in ("tx", "nc", "fl", "ga", "az", "oh", "sc")}   # after those
    tx.house, nc.house, fl.house = house["tx"], house["nc"], house["fl"]
    w = Writer(snap)
    speed = night / minutes
    w0 = datetime.now(timezone.utc)
    s0 = w0 + timedelta(minutes=minutes) - timedelta(minutes=night)
    print(f"feed: {night:.0f} simulated min from {s0:%H:%M:%S} in {minutes:.0f} wall min ({speed:.0f}x) into {snap}",
          flush=True)
    for m in range(int(night) + 1):
        sim = s0 + timedelta(minutes=m)
        wall = w0 + (sim - s0) / speed
        time.sleep(max(0.0, (wall - datetime.now(timezone.utc)).total_seconds()))
        t = m - pre                                                # minutes after poll close
        prog = {}
        if t >= 0:
            for k, b in ga.bodies(t).items():
                w.save(k, b, sim)
            if m % tx_every == 0:
                for k, b in tx.bodies(t).items():
                    w.save(k, b, sim)
            more_bodies = (x for f in more for x in f.bodies(t).items())
            house_bodies = (x for st in ("ga", "az", "oh", "sc")
                            for x in (*house[st].ev_bodies(t).items(), *house[st].oh_sc_bodies(t).items()))
            for k, b in (*nc.bodies(t).items(), *fl.bodies(t).items(), *more_bodies, *house_bodies):
                w.save(k, b, sim)
            prog = {r: min(1.0, t / span) for r in Markets.WINNER}
        for k, b in mk.bodies(prog).items():
            w.save(k, b, sim)
        w.mark(sim + timedelta(minutes=1))                           # the next simulated poll's start
    print(f"feed: done at {datetime.now(timezone.utc):%H:%M:%S}", flush=True)


# ------------------------------------------------------------------ run: feed + us live + liveserve, measured

def _ps(pid: int) -> tuple[float, float] | None:
    """(RSS MB, CPU seconds) of a process and its children."""
    try:
        o = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,time="], capture_output=True, text=True).stdout
    except OSError:
        return None
    rows = [ln.split() for ln in o.splitlines()]
    kids, todo = {pid}, [pid]
    while todo:
        p = todo.pop()
        for r in rows:
            if int(r[1]) == p and int(r[0]) not in kids:
                kids.add(int(r[0]))
                todo.append(int(r[0]))

    def secs(s: str) -> float:
        d, _, s = s.rpartition("-")
        parts = [float(x) for x in s.split(":")]
        v = sum(x * 60 ** i for i, x in enumerate(reversed(parts)))
        return v + (float(d) * 86400 if d else 0)
    sel = [r for r in rows if int(r[0]) in kids]
    return sum(int(r[2]) for r in sel) / 1024, sum(secs(r[3]) for r in sel)


def run(a) -> None:
    work = Path(a.work)
    snap, out = work / "snap" / us.TAG, work / "site_data"
    if work.exists() and not a.keep:
        shutil.rmtree(work)
    out.mkdir(parents=True, exist_ok=True)
    src = ROOT / "site" / "data"
    shutil.copytree(src / "us2026", out / "us2026", dirs_exist_ok=True)      # the repo's pre-count files, as on the VM
    for f in ("elections.json", "summary.json"):
        shutil.copy2(src / f, out / f)
    log = open(work / "rehearsal.log", "a")
    ft = threading.Thread(target=feed, args=(snap, a.minutes, a.night), daemon=True)
    ft.start()
    time.sleep(5)
    until = (datetime.now(timezone.utc) + timedelta(minutes=a.minutes + a.tail)).strftime("%Y-%m-%dT%H:%MZ")
    cmd = [*a.cmd.split(), "brunder", "us", "live", *map(str, CONFIGS), "--out", str(out), "--snap", str(snap),
           "--interval", "30", "--until", until]
    live = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**{k: v for k, v in os.environ.items() if k != "BRUNDER_DATA"},   # as the unit
                                 "PYTHONUNBUFFERED": "1"}, start_new_session=True)
    srv = subprocess.Popen([sys.executable if not a.py else a.py, "-m", "brunder.liveserve", "--root", str(out),
                            "--election", "us2026", "--election", "nz2026", "--host", "127.0.0.1",
                            "--port", str(a.live_port), "--allow-origin", a.origin, "--report-every", "60"],
                           cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1",
                                          "PYTHONDONTWRITEBYTECODE": "1"},
                           stdout=log, stderr=subprocess.STDOUT)
    cycles, samples = [], []

    def reader():
        for line in live.stdout:
            now = datetime.now(timezone.utc)
            log.write(f"[{now:%H:%M:%S.%f}] {line}")
            log.flush()
            hh = line[:8]
            if len(hh) == 8 and hh[2] == ":" and " c=" in line:
                t0 = now.replace(hour=int(hh[:2]), minute=int(hh[3:5]), second=int(hh[6:8]), microsecond=0)
                cycles.append({"t0": t0.isoformat(), "latency_s": round((now - t0).total_seconds(), 2)})
    threading.Thread(target=reader, daemon=True).start()
    t_end = time.time() + 60 * (a.minutes + a.tail)
    last_cpu = None
    while time.time() < t_end and live.poll() is None:
        s = _ps(live.pid)
        sv = _ps(srv.pid)
        if s:
            cpu = s[1]
            pct = None if last_cpu is None else round(100 * (cpu - last_cpu[1]) / (time.time() - last_cpu[0]), 1)
            last_cpu = (time.time(), cpu)
            samples.append({"t": round(time.time()), "live_rss_mb": round(s[0], 1), "live_cpu_s": cpu,
                            "live_cpu_pct": pct, "serve_rss_mb": round(sv[0], 1) if sv else None})
        time.sleep(5)
    for p in (live, srv):
        if p.poll() is None:
            p.terminate()
    sizes = {}
    for f in sorted((out / "us2026").rglob("*.json")):
        if "house" in f.parts or "hub" in f.parts:
            continue
        raw = f.read_bytes()
        sizes[str(f.relative_to(out))] = {"bytes": len(raw), "gzip": len(gzip.compress(raw, 6))}
    tl = json.loads((out / "us2026" / "timeline.json").read_text())
    rows = {rid: [r for r in v if r.get("counted") is not None] for rid, v in tl.items()}
    last = {rid: {k: v[-1].get(k) for k in ("t", "counted", "P", "P_v2", "P_v3", "P_v4", "pm", "kalshi",
                                             "turnout_vs_expected")} for rid, v in rows.items() if v}
    lat = [c["latency_s"] for c in cycles]
    rep = {"cycles": len(cycles), "latency_s": {"median": float(np.median(lat)) if lat else None,
                                                "p95": float(np.percentile(lat, 95)) if lat else None,
                                                "max": max(lat) if lat else None},
           "live_rss_mb_max": max((s["live_rss_mb"] for s in samples), default=None),
           "live_cpu_pct_mean": round(float(np.mean([s["live_cpu_pct"] for s in samples if s["live_cpu_pct"] is not None])), 1) if samples else None,
           "serve_rss_mb_max": max((s["serve_rss_mb"] or 0 for s in samples), default=None),
           "count_rows": {rid: len(v) for rid, v in rows.items()}, "last": last, "sizes": sizes,
           "snap_mb": round(sum(f.stat().st_size for f in snap.rglob("*") if f.is_file()) / 1e6, 1),
           "cycle_detail": cycles, "samples": samples}
    (work / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("cycle_detail", "samples", "sizes")}, indent=1,
                     default=str))


# ------------------------------------------------------------------ page: headless Chromium + Firefox

async def _check_engine(pw, eng: str, base: str, a, r: dict) -> None:
    exe = os.environ.get(f"{eng.upper()}_EXECUTABLE")         # e.g. Firefox copied out of a home dir ending in "."
    br = await getattr(pw, eng).launch(**({"executable_path": exe} if exe else {}))
    try:
        pg = await br.new_page(viewport={"width": 1512, "height": 900})
        pg.on("pageerror", lambda e: r["errors"].append(f"pageerror: {e}"))
        pg.on("console", lambda m: r["errors"].append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        pg.on("requestfailed", lambda q: r["errors"].append(f"requestfailed: {q.url} {q.failure}"))
        c = r["checks"]
        # national view: live badge, office summary, swing arrows "Counted vs 2024"
        await pg.goto(base, wait_until="load")
        await pg.wait_for_function("document.querySelector('#live') && !document.querySelector('#live').hidden",
                                   timeout=60000)
        await pg.wait_for_timeout(4000)
        c["live_badge"] = await pg.inner_text("#live")
        c["summary_chars"] = len(await pg.inner_text("#nat-summary"))
        sel = pg.locator(".nat-layer select")
        if await sel.count():
            await sel.select_option("arrows")
            await pg.wait_for_timeout(3000)
            btn = pg.locator("button[data-arr='live']")
            c["arrows_live_enabled"] = await btn.count() > 0 and not await btn.is_disabled()
            if c["arrows_live_enabled"]:
                await btn.click()
                await pg.wait_for_timeout(3000)
            c["arrow_paths"] = await pg.locator(".ov-arrows path").count()
        # race views: readout strip (chips incl. turnout), turnout chart view, Polymarket / Kalshi end labels
        for rid in a.races.split(","):
            await pg.goto(f"{base}&race={rid}", wait_until="load")
            await pg.wait_for_function("document.querySelector('#readout .chips')", timeout=60000)
            await pg.wait_for_timeout(12000)                     # the map worker delivers the units frames
            c[f"{rid}_chips"] = [" ".join(x.split()) for x in await pg.locator("#readout .chip").all_inner_texts()]
            c[f"{rid}_end_labels"] = await pg.locator("#timeline .end-label").all_text_contents()   # SVG: no innerText
            tv = pg.locator("#tl-view button[data-view='turnout']")
            c[f"{rid}_turnout_view"] = await tv.count() > 0
            if await tv.count():
                await tv.click()
                await pg.wait_for_timeout(1500)
                c[f"{rid}_turnout_view_text"] = " ".join((await pg.inner_text("#timeline"))[:200].split())
                await pg.locator("#tl-view button").first.click()
        # refresh behaviour: rows held advance with the feed; JS heap after GC stays flat (Chromium)
        held, heap = r["held"], r["heap_mb"]
        cdp = await pg.context.new_cdp_session(pg) if eng == "chromium" else None
        if cdp:
            await cdp.send("Performance.enable")
        t_end = time.time() + 60 * (a.minutes if eng == "chromium" else min(a.minutes, 2))
        while time.time() < t_end:
            perf = json.loads(await pg.evaluate("JSON.stringify(window.__dashPerf.timelineHeld || null)")) or {}
            held.append([round(time.time()), perf.get(a.races.split(",")[-1]), await pg.inner_text("#live"),
                         await pg.evaluate("window.__dashPerf.timelineFetch || null")])
            if cdp:
                await cdp.send("HeapProfiler.collectGarbage")
                m = {x["name"]: x["value"] for x in (await cdp.send("Performance.getMetrics"))["metrics"]}
                heap.append(round(m["JSHeapUsedSize"] / 1e6, 1))
            await pg.wait_for_timeout(30000)
    finally:
        await br.close()


async def page_check(a) -> None:
    from playwright.async_api import async_playwright
    import functools
    import http.server

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.site_port),
                                            functools.partial(Handler, directory=str(ROOT / "site")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{a.site_port}/election.html?id=us2026&live={a.live}"
    results = {}
    async with async_playwright() as pw:
        for eng in a.engines.split(","):
            r = results[eng] = {"errors": [], "checks": {}, "held": [], "heap_mb": []}
            try:
                await _check_engine(pw, eng, base, a, r)
            except Exception as e:
                r["failed"] = f"{type(e).__name__}: {str(e)[:500]}"
            r["errors"] = r["errors"][:50]
            Path(a.report).write_text(json.dumps(results, indent=1))
    httpd.shutdown()
    print(json.dumps(results, indent=1)[:20000])


# ------------------------------------------------------------------ ab: old vs new live model on one replayed archive

def _q(v: list[float], p: float) -> float | None:
    return round(float(np.percentile(v, p)), 2) if v else None


def _observe(port: int, path: Path, stop: threading.Event, seen: list) -> None:
    """Every 0.25 s: stat timeline_tail.json (write time) and GET it from liveserve with If-None-Match (served time);
    each new served version -> (served wall s, file mtime s, latest row minute over races)."""
    import urllib.error
    import urllib.request
    etag = None
    while not stop.is_set():
        try:
            mt = path.stat().st_mtime
            req = urllib.request.Request(f"http://127.0.0.1:{port}/us2026/timeline_tail.json",
                                         headers={"If-None-Match": etag} if etag else {})
            with urllib.request.urlopen(req, timeout=5) as r:
                body, etag = r.read(), r.headers.get("ETag")
            served = time.time()
            tl = json.loads(body)
            last = max((rows[-1]["t"] for k, rows in tl.items() if isinstance(rows, list) and rows
                        and rows[-1].get("counted") is not None), default=None)
            seen.append((served, mt, last))
        except urllib.error.HTTPError as e:
            if e.code != 304:
                pass
        except Exception:
            pass
        stop.wait(0.25)


def _rows(tl: dict) -> dict[str, dict[str, dict]]:
    return {rid: {r["t"]: r for r in rows if r.get("counted") is not None} for rid, rows in tl.items()
            if isinstance(rows, list)}


def compare(a: dict, b: dict) -> dict:
    """Count rows (counted not null) at the minutes both timelines have: how many differ, and in which fields."""
    ra, rb = _rows(a), _rows(b)
    n = diff = 0
    fields: dict[str, int] = {}
    for rid in ra.keys() & rb.keys():
        for t in ra[rid].keys() & rb[rid].keys():
            n += 1
            x, y = ra[rid][t], rb[rid][t]
            bad = [k for k in x.keys() | y.keys() if x.get(k) != y.get(k)]
            if bad:
                diff += 1
                for k in bad:
                    fields[k] = fields.get(k, 0) + 1
    return {"rows_compared": n, "rows_differing": diff, "fields": dict(sorted(fields.items(), key=lambda kv: -kv[1])[:8])}


def ab(a) -> None:
    """Feed one replayed night; run the old checkout's and this checkout's `brunder us live` on it side by side (each
    with its own liveserve), observe both, then compare their timelines with `brunder us export` of the final archive.
    Latency of simulated minute m = time from its snapshots being on disk (the feed's poll marker) until its minute row
    is first in timeline_tail.json (written: file mtime; served: liveserve answer)."""
    work = Path(a.work)
    if work.exists():
        shutil.rmtree(work)
    snap = work / "snap" / us.TAG
    src = ROOT / "site" / "data"
    sides = {"old": Path(a.old_root).resolve(), "new": ROOT}
    outs = {k: work / f"site_{k}" for k in sides}
    for o in outs.values():
        shutil.copytree(src / "us2026", o / "us2026", dirs_exist_ok=True)
        for f in ("elections.json", "summary.json"):
            shutil.copy2(src / f, o / f)
    marks: list[tuple[float, str]] = []                               # (wall s, simulated next-poll time)
    ft = threading.Thread(target=feed, args=(snap, a.minutes, a.night), daemon=True)
    ft.start()
    time.sleep(3)
    until = (datetime.now(timezone.utc) + timedelta(minutes=a.minutes + a.tail)).strftime("%Y-%m-%dT%H:%MZ")
    env = {**{k: v for k, v in os.environ.items() if k != "BRUNDER_DATA"}, "PYTHONUNBUFFERED": "1"}
    procs, servers, logs = {}, {}, {}
    for i, (k, root) in enumerate(sides.items()):
        cfgs = [str(root / Path(c).relative_to(ROOT)) for c in CONFIGS]
        extra = ["--updates", str(work / f"updates_{k}.jsonl")] if k == "new" else []
        logs[k] = open(work / f"live_{k}.log", "w")
        procs[k] = subprocess.Popen([*a.cmd.split(), "brunder", "us", "live", *cfgs, "--out", str(outs[k]), "--snap",
                                     str(snap), "--interval", "30", "--until", until, *extra],
                                    cwd=root, stdout=logs[k], stderr=subprocess.STDOUT, env=env, start_new_session=True)
        servers[k] = subprocess.Popen([sys.executable, "-m", "brunder.liveserve", "--root", str(outs[k]), "--election",
                                       "us2026", "--host", "127.0.0.1", "--port", str(a.port + i)],
                                      cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                                      stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    stop, seen = threading.Event(), {k: [] for k in sides}
    obs = [threading.Thread(target=_observe, args=(a.port + i, outs[k] / "us2026" / "timeline_tail.json", stop, seen[k]),
                            daemon=True) for i, k in enumerate(sides)]
    for t in obs:
        t.start()
    marker = snap / ".poll" / "snap.json"
    samples = {k: [] for k in sides}
    t_end = time.time() + 60 * (a.minutes + a.tail)
    last_mark, next_sample = None, 0.0
    while time.time() < t_end and all(p.poll() is None for p in procs.values()):
        try:
            d = json.loads(marker.read_text())
            if d["next"] != last_mark:                                 # done = when the minute was fully on disk
                marks.append((datetime.fromisoformat(d["done"]).timestamp(), d["next"]))
                last_mark = d["next"]
        except (OSError, ValueError, KeyError):
            pass
        if time.time() >= next_sample:
            next_sample = time.time() + 5
            for k, p in procs.items():
                s = _ps(p.pid)
                if s:
                    samples[k].append((time.time(), *s))
        time.sleep(0.25)
    stop.set()
    for p in (*procs.values(), *servers.values()):
        if p.poll() is None:
            p.terminate()
    for p in procs.values():
        p.wait(30)
    rep: dict = {"minutes": a.minutes, "night": a.night, "speed": round(a.night / a.minutes, 2), "sides": {}}
    step = pd.Timedelta("1min")
    for k in sides:
        lat_w, lat_s = [], []
        for i, (wall, nxt) in enumerate(marks[:-1]):
            s_m = pd.Timestamp(nxt) - step                             # the minute's snapshots are named s_m
            row = us._iso(s_m.ceil(step))
            v = next(((sv, mt) for sv, mt, last in seen[k] if last and last >= row), None)
            if v:
                lat_s.append(v[0] - wall)
                lat_w.append(v[1] - wall)
        cpu = samples[k]
        busy = (cpu[-1][2] - cpu[0][2]) / max(cpu[-1][0] - cpu[0][0], 1e-9) if len(cpu) > 1 else None
        rep["sides"][k] = {"minutes_measured": len(lat_w),
                           "written_s": {"median": _q(lat_w, 50), "p95": _q(lat_w, 95), "max": _q(lat_w, 100)},
                           "served_s": {"median": _q(lat_s, 50), "p95": _q(lat_s, 95), "max": _q(lat_s, 100)},
                           "cpu_s": round(cpu[-1][2], 1) if cpu else None,
                           "cpu_pct_mean": round(100 * busy, 1) if busy is not None else None,
                           "rss_mb_max": round(max(x[1] for x in cpu), 1) if cpu else None,
                           "versions_served": len(seen[k])}
    ex_out = work / "site_export"
    shutil.copytree(src / "us2026", ex_out / "us2026", dirs_exist_ok=True)
    subprocess.run([*a.cmd.split(), "brunder", "us", "export", *map(str, CONFIGS), "--out", str(ex_out), "--snap",
                    str(snap)], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    tls = {k: json.loads((o / "us2026" / "timeline.json").read_text()) for k, o in
           {**outs, "export": ex_out}.items()}
    rep["identical"] = {"new_vs_export": compare(tls["new"], tls["export"]),
                        "old_vs_export": compare(tls["old"], tls["export"]),
                        "old_vs_new": compare(tls["old"], tls["new"])}
    def units(o: Path) -> dict[str, dict]:
        out = {}
        for f in sorted((o / "us2026" / "units").glob("*.json")):
            u = json.loads(f.read_text())
            u["frames"] = [{k: v for k, v in fr.items() if k != "t"} for fr in u.get("frames", [])]
            out[f.name] = u
        return out
    uo, un = units(outs["old"]), units(outs["new"])
    same = [k for k in uo.keys() & un.keys() if uo[k] == un[k]]
    rep["identical"]["units_old_vs_new"] = {"files": len(uo.keys() & un.keys()), "identical": len(same),
                                            "differing": sorted(set(uo) & set(un) - set(same))}
    (work / "ab_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    f = sub.add_parser("feed")
    f.add_argument("--snap", type=Path, required=True)
    f.add_argument("--minutes", type=float, default=15)
    f.add_argument("--night", type=float, default=180)
    r = sub.add_parser("run")
    r.add_argument("--work", required=True)
    r.add_argument("--minutes", type=float, default=15)
    r.add_argument("--night", type=float, default=180)
    r.add_argument("--tail", type=float, default=3, help="minutes to keep running after the feed ends")
    r.add_argument("--live-port", type=int, default=8791)
    r.add_argument("--origin", default="http://127.0.0.1:8792")
    r.add_argument("--cmd", default="uv run --frozen", help="prefix for the brunder command")
    r.add_argument("--py", default=None, help="python for liveserve (the VM unit uses /usr/bin/python3)")
    r.add_argument("--keep", action="store_true")
    p = sub.add_parser("page")
    p.add_argument("--site-port", type=int, default=8792)
    p.add_argument("--live", required=True)
    p.add_argument("--minutes", type=float, default=15)
    p.add_argument("--engines", default="chromium,firefox")
    p.add_argument("--races", default="ga-sen,tx-sen,nc-sen,fl-gov", help="race views checked; the last is tracked")
    p.add_argument("--report", default="/tmp/brr_page.json")
    b = sub.add_parser("ab", help="old vs new live model on one replayed archive: latency, CPU, memory, identity")
    b.add_argument("--work", required=True)
    b.add_argument("--old-root", required=True, help="checkout of the old code (git worktree at the base commit)")
    b.add_argument("--minutes", type=float, default=30)
    b.add_argument("--night", type=float, default=30, help="= --minutes for real time; larger to accelerate")
    b.add_argument("--tail", type=float, default=2)
    b.add_argument("--port", type=int, default=8871)
    b.add_argument("--cmd", default="uv run --frozen")
    a = ap.parse_args()
    if a.cmd_name == "feed":
        feed(a.snap, a.minutes, a.night)
    elif a.cmd_name == "run":
        run(a)
    elif a.cmd_name == "ab":
        ab(a)
    else:
        asyncio.run(page_check(a))


if __name__ == "__main__":
    main()
