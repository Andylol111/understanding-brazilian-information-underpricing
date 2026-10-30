"""House contests for the election-night dress rehearsal (analysis/us_night_replay.py): the U.S. Representative rows
each recorded feed would carry, so the House configs (configs/us2026_house/<st>-<nn>_v1.toml, brunder.us_house_night; own
unit brunder-us-house-live, measured by analysis/us_house_night.py bench) see a count.

Votes per county part of each 2026 district = data_public/us2026_house_priors.csv (2024 president) × 0.72, the Democratic
share moved by one draw per district from N(+0.03, 0.03); early + mail batch = the state's config early_share of the
votes, 2 points more Democratic than the part's final share. Hooks (contract with us_night_replay.py):
  Texas.bodies       races.update(house.tx_races(i, fe, fl, complete))
  Florida.bodies     lines += house.fl_lines(i, fe, fl, complete, n)
  NorthCarolina      rows += house.nc_rows(i, fe, fl, complete, n)
  feed()             bodies.update(house.ev_bodies(t)); bodies.update(house.oh_sc_bodies(t))   (own count order)
with i = row in brunder.us.counties(st), fe / fl / complete = us_night_replay.posted(), n = that class's precincts.
"""

from __future__ import annotations

import json
import tomllib
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from brunder import us

PRIORS = us.ROOT_DIR / "data_public" / "us2026_house_priors.csv"
EARLY = {"tx": 0.6, "nc": 0.46, "fl": 0.6, "ga": 0.64, "az": 0.75, "oh": 0.5, "sc": 0.6}
TURNOUT = us.EST_TURNOUT


def _names(st: str, n: int) -> tuple[str, str]:
    f = us.ROOT_DIR / "configs" / "us2026_house" / f"{st}-{n:02d}_v1.toml"
    if f.exists():
        E = tomllib.loads(f.read_text())["election"]
        return E["a"]["name"], E["b"]["name"]
    return f"Democratic candidate {st.upper()}-{n:02d}", f"Republican candidate {st.upper()}-{n:02d}"


class HouseRehearsal:
    """Per county (row i of us.counties(st)): [(district, dem, rep, other, precincts)], precomputed."""

    def __init__(self, st: str, rng: np.random.Generator | None = None, span: float = 360.0):
        rng = rng or np.random.default_rng(20261103)
        self.st, self.span, self.early = st, span, EARLY[st]
        pr = pd.read_csv(PRIORS, comment="#")
        pr = pr[pr["source"].str.startswith(f"{st}-")]
        self.c = us.counties(st)
        idx = {u: i for i, u in enumerate(self.c["unit_id"])}
        self.parts: list[list[tuple[int, float, float, float, int]]] = [[] for _ in range(len(self.c))]
        self.cands: dict[int, tuple[str, str]] = {}
        for src, g in pr.groupby("source"):
            n = int(src[3:5])
            self.cands[n] = _names(st, n)
            swing = float(rng.normal(0.03, 0.03))
            for x in g.itertuples():
                if x.unit_id not in idx:
                    continue
                two = (x.dem + x.rep) * TURNOUT
                s = min(max(x.dem / (x.dem + x.rep) + swing, 0.02), 0.98) if x.dem + x.rep > 0 else 0.5
                other = max(x.total - x.dem - x.rep, 0) * TURNOUT
                self.parts[idx[x.unit_id]].append((n, two * s, two * (1 - s), other, max(1, int(round(two / 2000)))))
        size = np.array([sum(p[1] + p[2] for p in ps) for ps in self.parts])
        rank = size.argsort().argsort() / max(len(size) - 1, 1)
        sched = []
        for i in range(len(size)):                    # us_night_replay.schedule (own draws: separate keys)
            t0 = rng.uniform(0, 60) + 30 * rank[i]
            sched.append((t0, np.sort(t0 + rng.uniform(30, 240 + 120 * rank[i], 4)), rng.dirichlet(np.ones(4))))
        k = span / max(max(ts[-1] for _, ts, _ in sched), 1)
        self.sched = [(t0 * k, ts * k, w) for t0, ts, w in sched]

    def _posted(self, i: int, t: float) -> tuple[float, float, bool]:
        t0, ts, w = self.sched[i]
        return float(t >= t0), float(w[ts <= t].sum()), bool(t >= ts[-1])

    def _split(self, d: float, r: float, o: float, fe: float, fl: float) -> tuple[list[float], list[float]]:
        """(posted votes D, R, other; of which early) for one county part."""
        s = d / (d + r) if d + r else 0.5
        se = min(max(s + 0.02, 0), 1)
        ed, er, eo = self.early * (d + r) * se, self.early * (d + r) * (1 - se), self.early * o
        V = [ed * fe + max(d - ed, 0) * fl, er * fe + max(r - er, 0) * fl, eo * fe + (o - eo) * fl]
        return V, [ed * fe, er * fe, eo * fe]

    # ---------------------------------------------------------------- hooks into existing rehearsal bodies

    def tx_races(self, i: int, fe: float, fl: float, complete: bool) -> dict:
        out = {}
        for n, d, r, o, prec in self.parts[i]:
            V, E = self._split(d, r, o, fe, fl)
            a, b = self.cands[n]
            out[f"8{n:03d}"] = {"N": f"U. S. REPRESENTATIVE DISTRICT {n}", "PR": prec if complete else int(prec * fl * 0.95),
                                "TP": prec, "C": {
                                    "1": {"N": a.upper(), "P": "DEM", "V": int(round(V[0])), "EV": int(round(E[0]))},
                                    "2": {"N": b.upper(), "P": "REP", "V": int(round(V[1])), "EV": int(round(E[1]))},
                                    "3": {"N": "OTHER", "P": "LIB", "V": int(round(V[2])), "EV": int(round(E[2]))}}}
        return out

    def fl_lines(self, i: int, fe: float, fl: float, complete: bool, n_prec: int) -> list[str]:
        f = 0.6 * fe + 0.4 * fl                    # as us_night_replay.Florida: early + mail post at poll close
        name = self.c.loc[i, "name"]
        out = []
        for n, d, r, o, prec in self.parts[i]:
            a, b = self.cands[n]
            for (full, pty), v in zip(((a, "DEM"), (b, "REP"), ("Write-in Other", "NPA")), (d, r, o)):
                first, _, last = full.rpartition(" ")
                out.append("\t".join(["11/03/2026", pty, pty, "USR", f"Representative in Congress, District {n}",
                                      name[:3].upper(), name, f"{n:03d}", " ", str(prec),
                                      str(prec if complete else int(prec * fl * 0.95)), last, first, "",
                                      str(int(round(v * f)))]))
        return out

    def nc_rows(self, i: int, fe: float, fl: float, complete: bool, n_prec: int) -> list[dict]:
        out = []
        for n, d, r, o, prec in self.parts[i]:
            V, E = self._split(d, r, o, fe, fl)
            a, b = self.cands[n]
            for (nm, pty), v, e in zip(((a, "DEM"), (b, "REP"), ("Other", "LIB")), V, E):
                out.append({"cid": str(i + 1), "cnm": f"US HOUSE OF REPRESENTATIVES DISTRICT {n:02d} (VOTE FOR 1)",
                            "bnm": nm, "pty": pty, "vct": str(int(round(v))), "evc": str(int(round(e * 0.8))),
                            "avc": str(int(round(e)) - int(round(e * 0.8))), "ovc": str(int(round(v - e))),
                            "pvc": "0", "prt": str(prec if complete else int(prec * fl * 0.95)), "ptl": str(prec)})
        return out

    # ---------------------------------------------------------------- own bodies (own count order)

    def ev_bodies(self, t: float) -> dict[str, bytes]:
        """GA / AZ: one Enhanced Voting ballot item per district (keys ga_enr_<Georgia|Arizona>_house_<n>)."""
        if self.st not in ("ga", "az"):
            return {}
        juris = "Georgia" if self.st == "ga" else "Arizona"
        early_name = "Advance Voting Votes" if self.st == "ga" else "Early Voting"
        late_name = "Election Day Votes" if self.st == "ga" else "Election Day"
        items: dict[int, dict] = {}
        for i, ps in enumerate(self.parts):
            fe, fl, complete = self._posted(i, t)
            for n, d, r, o, prec in ps:
                V, E = self._split(d, r, o, fe, fl)
                a, b = self.cands[n]
                opts = [{"name": [{"text": f"{nm} ({p[0]})"}], "party": {"abbreviation": p[:3]}, "voteCount": int(round(v)),
                         "groupResults": [{"groupName": [{"text": early_name}], "voteCount": int(round(e))},
                                          {"groupName": [{"text": late_name}], "voteCount": int(round(v)) - int(round(e))}]}
                        for nm, p, v, e in ((a, "DEM", V[0], E[0]), (b, "REP", V[1], E[1]))]
                it = items.setdefault(n, {"name": [{"text": (f"US House of Representatives - District {n}" if self.st == "ga"
                                                             else f"U.S. Representative in Congress - District No. {n}")}],
                                          "breakdownResults": [], "reportingStatus": {"reportingUnits": 0, "totalUnits": 0}})
                it["breakdownResults"].append({"locality": {"name": [{"text": self.c.loc[i, "name"]}]},
                                               "precinctReportingStatus": {
                                                   "reportingUnits": prec if complete else int(prec * fl * 0.95),
                                                   "totalUnits": prec},
                                               "ballotOptions": opts})
        out = {}
        for n, it in items.items():
            tot = {}
            for br in it["breakdownResults"]:
                for o in br["ballotOptions"]:
                    k = o["party"]["abbreviation"]
                    tot.setdefault(k, {**o, "voteCount": 0, "groupResults": []})["voteCount"] += o["voteCount"]
            it["summaryResults"] = {"ballotOptions": list(tot.values())}
            out[f"ga_enr_{juris}_house_{n:02d}"] = json.dumps({"ballotItemWithBreakdown": it},
                                                             separators=(",", ":")).encode()
        return out

    def oh_sc_bodies(self, t: float) -> dict[str, bytes]:
        """OH: a NIST 1622 XML with only the House contests; SC: Clarity ALL/summary/status with only House contests."""
        if self.st == "oh":
            return {us.FEEDS["oh"]["xml"]: self._oh_xml(t)}
        if self.st == "sc":
            return self._sc(t)
        return {}

    def _oh_xml(self, t: float) -> bytes:
        ns = "http://grouper.ieee.org/groups/1622/groups/2/V1/1622_2-election_results_reporting.xsd"
        gp, cands, contests = [], [], {}
        for i, ps in enumerate(self.parts):
            if not ps:
                continue
            fe, fl, complete = self._posted(i, t)
            prec = sum(p[4] for p in ps)
            gp.append(f'<GpUnit xsi:type="ReportingUnit" ObjectId="c{i}" Name="{self.c.loc[i, "name"]}" Type="County" '
                      f'IsElectoralDistrict="false" SubUnitsReported="{prec if complete else int(prec * fl * 0.95)}" '
                      f'TotalSubUnits="{prec}"/>')
            for n, d, r, o, _ in ps:
                V, _ = self._split(d, r, o, fe, fl)
                contests.setdefault(n, []).append((f"c{i}", V))
        body = []
        for n, parts in sorted(contests.items()):
            sels = []
            for j, (nm, pty) in enumerate(zip(self.cands[n], ("pd", "pr"))):
                cands.append(f'<Candidate ObjectId="k{n}_{j}"><BallotName><LanguageString>{escape(nm)}</LanguageString>'
                             f'</BallotName><PartyId>{pty}</PartyId></Candidate>')
                vcs = "".join(f'<VoteCounts Type="Total" Count="{int(round(V[j]))}"><GpUnitId>{g}</GpUnitId></VoteCounts>'
                              for g, V in parts)
                sels.append(f'<BallotSelection xsi:type="CandidateSelection" ObjectId="s{n}_{j}">'
                            f'<CandidateId>k{n}_{j}</CandidateId>{vcs}</BallotSelection>')
            body.append(f'<Contest xsi:type="CandidateContest" ObjectId="h{n}" '
                        f'Name="Representative to Congress - District {n:02d}">{"".join(sels)}</Contest>')
        xml = (f'<?xml version="1.0"?><ElectionReport xmlns="{ns}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
               f'IsTest="true"><GpUnitCollection>{"".join(gp)}</GpUnitCollection><PartyCollection>'
               f'<Party ObjectId="pd" Abbreviation="DEM"/><Party ObjectId="pr" Abbreviation="REP"/></PartyCollection>'
               f'<CandidateCollection>{"".join(cands)}</CandidateCollection><Election><ContestCollection>'
               f'{"".join(body)}</ContestCollection></Election></ElectionReport>')
        return xml.encode()

    def _sc(self, t: float) -> dict[str, bytes]:
        keys = sorted(self.cands)
        summary = [{"K": f"9{n:02d}", "C": f"U.S. House of Representatives, District {n}",
                    "CH": [f"DEM {self.cands[n][0]}", f"REP {self.cands[n][1]}"], "P": ["DEM", "REP"], "V": [0, 0]}
                   for n in keys]
        allj, st = [], {"P": [], "PX": [], "PY": [], "B": [], "R": []}
        for i, ps in enumerate(self.parts):
            if not ps:
                continue
            fe, fl, complete = self._posted(i, t)
            prec = sum(p[4] for p in ps)
            C, V = [], []
            for n, d, r, o, _ in ps:
                v, _ = self._split(d, r, o, fe, fl)
                C.append(f"9{n:02d}")
                V.append([int(round(v[0])), int(round(v[1]))])
            allj.append({"A": self.c.loc[i, "name"], "C": C, "V": V})
            tot = sum(p[1] + p[2] + p[3] for p in ps)
            st["P"].append(self.c.loc[i, "name"])
            st["PX"].append(prec if complete else int(prec * fl * 0.95))
            st["PY"].append(prec)
            st["B"].append(int(tot * (self.early * fe + (1 - self.early) * fl)))
            st["R"].append(int(tot * 1.6))
        F = us.FEEDS["sc"]
        return {F["all"]: json.dumps({"Contests": allj}).encode(), F["summary"]: json.dumps(summary).encode(),
                F["status"]: json.dumps(st).encode()}


def _selftest() -> None:   # uv run python analysis/us_house_rehearsal.py: every state's bodies parse to House rows
    from brunder import us_house_night as hn
    for st in ("tx", "fl", "nc", "ga", "az", "oh", "sc"):
        h = HouseRehearsal(st)
        t = h.span
        if st == "tx":
            rows = pd.concat([pd.DataFrame([{"race": hn.house_race(r["N"]), "votes": c["V"]} for r in
                                            h.tx_races(i, 1.0, 1.0, True).values() for c in r["C"].values()])
                              for i in range(len(h.c)) if h.parts[i]])
        elif st == "fl":
            cols = ["ElectionDate", "PartyCode", "PartyName", "RaceCode", "RaceName", "CountyCode", "CountyName",
                    "Juris1num", "Juris2num", "Precincts", "PrecinctsReporting", "CanNameLast", "CanNameFirst",
                    "CanNameMiddle", "CanVotes"]
            lines = ["\t".join(cols)] + [x for i in range(len(h.c)) for x in h.fl_lines(i, 1.0, 1.0, True, 1)]
            rows = hn.parse_fl(("\n".join(lines) + "\n").encode())
        elif st == "nc":
            info = [{"cid": str(i + 1), "cnm": h.c.loc[i, "name"].upper(), "bct": "1", "rtl": "1"} for i in range(len(h.c))]
            rows = hn.parse_nc(json.dumps(info).encode(),
                               [json.dumps(h.nc_rows(i, 1.0, 1.0, True, 1)).encode() for i in range(len(h.c))])
        elif st in ("ga", "az"):
            rows = hn.parse_ev(list(h.ev_bodies(t).values()), st)
        elif st == "oh":
            rows = hn.parse_oh(h.oh_sc_bodies(t)[us.FEEDS["oh"]["xml"]])
        else:
            b = h.oh_sc_bodies(t)
            rows = hn.parse_sc(b[us.FEEDS["sc"]["all"]], b[us.FEEDS["sc"]["summary"]], b[us.FEEDS["sc"]["status"]])
        print(st, rows["race"].nunique(), "districts", int(rows["votes"].sum()), "votes")


if __name__ == "__main__":
    _selftest()
