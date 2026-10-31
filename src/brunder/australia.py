"""Australia, House of Representatives: AEC media-feed parsers, count-night projection, seat simulation.

Data: the Australian Electoral Commission's election-night media feed archive (ftp://mediafeedarchive.aec.gov.au/<event>/,
listed at https://results.aec.gov.au/); every ~90 s a zip with one EML-based XML message. The Detailed granularity feed
carries, per polling place, first-preference votes and the two-candidate-preferred (TCP) count, with the time each was
published; the Preload feed carries candidates, parties and the previous election's votes mapped on to this election's
polling places and divisions (the AEC's own "Historic" and notional figures). Licence: see docs/australia.md.

Everything here works on the data structures returned by `parse_results` / `parse_preload`. A "frame" is one published
feed message; the replay uses a frame from the moment it was created (`created`, UTC).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

M = "{http://www.aec.gov.au/xml/schema/mediafeed}"
E = "{urn:oasis:names:tc:evs:schema:eml}"
TZ = "Australia/Sydney"          # the feed stamps are Canberra local time (AEST in May)
STAMP = re.compile(r"-(\d{14})\.zip$")

# Party groups used to match candidates between elections. The Coalition is one group (Liberal, National, LNP, CLP).
COALITION = {"LP", "NP", "LNP", "CLP", "LNQ", "LNC", "NATS", "LIB", "NAT"}
LABOR = {"ALP"}
GREENS = {"GRN", "AG"}


def utc(stamp: str) -> pd.Timestamp:
    return pd.Timestamp(stamp).tz_localize(TZ, ambiguous="NaT").tz_convert("UTC")


def stamp_of(name: str) -> pd.Timestamp:
    return pd.Timestamp(STAMP.search(name).group(1)).tz_localize(TZ).tz_convert("UTC")


def read_zip(path: Path | str, member: str = "results") -> bytes:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".xml") and member in n]
        if len(names) != 1:
            raise ValueError(f"{path}: expected one {member} xml, got {names}")
        return z.read(names[0])


def _votes(el) -> int:
    return int(el.text) if el is not None and el.text else 0


def _cand_votes(parent) -> dict[int, int]:
    return {int(c.find(E + "CandidateIdentifier").get("Id")): _votes(c.find(M + "Votes"))
            for c in parent.findall(M + "Candidate")}


def parse_results(xml: bytes) -> dict:
    """One Detailed Light / LightProgress message -> {created, updated, div: {id: {...}}}."""
    root = ET.fromstring(xml)
    res = root.find(M + "Results")
    out = {"created": utc(root.get("Created")), "updated": utc(res.get("Updated")), "phase": res.get("Phase"),
           "verbosity": res.get("Verbosity"), "div": {}}
    for c in res.iter(M + "Contest"):
        if not c.find(E + "ContestIdentifier").get("Id").isdigit():     # Senate contests are per state
            continue
        cid = int(c.find(E + "ContestIdentifier").get("Id"))
        fp = c.find(M + "FirstPreferences")
        d = {"name": c.find(E + "ContestIdentifier").find(E + "ContestName").text,
             "updated": c.get("Updated"), "enrol": int(c.find(M + "Enrolment").text or 0),
             "fp": {}, "fp_formal": 0, "fp_informal": 0, "fp_ret": 0, "fp_exp": 0,
             "tcp": None, "tcp_ret": 0, "tcp_exp": 0, "maverick": False, "restricted": False, "tpp": None, "pp": {}}
        if fp is not None:
            d["fp"] = _cand_votes(fp)
            d["fp_formal"] = _votes(fp.find(M + "Formal/" + M + "Votes"))
            d["fp_informal"] = _votes(fp.find(M + "Informal/" + M + "Votes"))
            d["fp_ret"], d["fp_exp"] = int(fp.get("PollingPlacesReturned") or 0), int(fp.get("PollingPlacesExpected") or 0)
        t = c.find(M + "TwoCandidatePreferred")
        if t is not None:
            d["maverick"], d["restricted"] = t.get("Maverick") == "true", t.get("Restricted") == "true"
            d["tcp_ret"], d["tcp_exp"] = int(t.get("PollingPlacesReturned") or 0), int(t.get("PollingPlacesExpected") or 0)
            if t.find(M + "Candidate") is not None:
                d["tcp"] = _cand_votes(t)
        tp = c.find(M + "TwoPartyPreferred")
        if tp is not None:
            d["tpp"] = {int(x.find(M + "CoalitionIdentifier").get("Id")): _votes(x.find(M + "Votes"))
                        for x in tp.findall(M + "Coalition")}
        pps = c.find(M + "PollingPlaces")
        for p in (pps if pps is not None else []):
            pid = int(p.find(M + "PollingPlaceIdentifier").get("Id"))
            pf = p.find(M + "FirstPreferences")
            pt = p.find(M + "TwoCandidatePreferred")
            rec = {"fp_t": pf.get("Updated") if pf is not None else None,
                   "fp": _cand_votes(pf) if pf is not None and pf.get("Updated") else {},
                   "formal": _votes(pf.find(M + "Formal/" + M + "Votes")) if pf is not None else 0,
                   "tcp_t": pt.get("Updated") if pt is not None else None,
                   "tcp": _cand_votes(pt) if pt is not None and pt.get("Updated") and pt.find(M + "Candidate") is not None else {}}
            d["pp"][pid] = rec
        out["div"][cid] = d
    return out


def parse_preload(xml: bytes) -> dict:
    """Detailed Preload message -> static tables: candidates, divisions (with the previous election's notional
    two-party-preferred votes) and polling places with the previous election's first preferences."""
    root = ET.fromstring(xml)
    res = root.find(M + "Results")
    cands, divs, booths = {}, {}, {}
    for c in res.iter(M + "Contest"):
        if not c.find(E + "ContestIdentifier").get("Id").isdigit():
            continue
        did = int(c.find(E + "ContestIdentifier").get("Id"))
        pd_ = c.find(M + "PollingDistrictIdentifier")
        fp = c.find(M + "FirstPreferences")
        d = {"id": did, "name": c.find(E + "ContestIdentifier").find(E + "ContestName").text,
             "state": pd_.find(M + "StateIdentifier").get("Id") if pd_ is not None else None,
             "enrol": int(c.find(M + "Enrolment").text or 0),
             "enrol_hist": int(c.find(M + "Enrolment").get("Historic") or 0), "hist_tpp": None, "hist_fp": {},
             "hist_formal": 0, "expected": int(fp.get("PollingPlacesExpected") or 0) if fp is not None else 0}
        if fp is not None:
            for k in fp.findall(M + "Candidate"):
                cid = int(k.find(E + "CandidateIdentifier").get("Id"))
                aff = k.find(E + "AffiliationIdentifier")
                nm = k.find(E + "CandidateIdentifier").find(E + "CandidateName")
                v = k.find(M + "Votes")
                cands[cid] = {"id": cid, "div": did, "name": nm.text if nm is not None else "",
                              "party": aff.get("ShortCode") if aff is not None else "IND",
                              "party_name": aff.find(E + "RegisteredName").text if aff is not None else "Independent",
                              "independent": k.get("Independent") == "true" or aff is None,
                              "pos": int(k.find(M + "BallotPosition").text), "incumbent": k.find(M + "Incumbent").get("Notional") == "true"
                              if k.find(M + "Incumbent") is not None else False}
                d["hist_fp"][cid] = int(v.get("Historic") or 0)
            f = fp.find(M + "Formal/" + M + "Votes")
            d["hist_formal"] = int(f.get("Historic") or 0) if f is not None else 0
        tp = c.find(M + "TwoPartyPreferred")
        if tp is not None:
            d["hist_tpp"] = {int(x.find(M + "CoalitionIdentifier").get("Id")): int(x.find(M + "Votes").get("Historic") or 0)
                             for x in tp.findall(M + "Coalition")}
        divs[did] = d
        pps = c.find(M + "PollingPlaces")
        for p in (pps if pps is not None else []):
            pi = p.find(M + "PollingPlaceIdentifier")
            pid = int(pi.get("Id"))
            pf = p.find(M + "FirstPreferences")
            hist, formal = {}, 0
            if pf is not None:
                for k in list(pf.findall(M + "Candidate")) + list(pf.findall(M + "Ghost")):
                    hist[int(k.find(E + "CandidateIdentifier").get("Id"))] = int(k.find(M + "Votes").get("Historic") or 0)
                fo = pf.find(M + "Formal/" + M + "Votes")
                formal = int(fo.get("Historic") or 0) if fo is not None else 0
            booths[(did, pid)] = {"div": did, "pp": pid, "name": pi.get("Name"), "class": pi.get("Classification") or "Ordinary",
                                  "hist_fp": hist, "hist_formal": formal}
    return {"cands": cands, "divs": divs, "booths": booths}


def group_of(cand: dict) -> str:
    """Party group used to recognise the same contest between elections; independents by surname."""
    p = cand["party"]
    if "greens" in (cand.get("party_name") or "").lower():
        return "GRN"
    if cand["independent"] or p in ("IND", "", None):
        return "IND:" + re.sub(r"[^A-Z]", "", cand["name"].split(",")[0].upper()) if cand["name"] else "IND"
    if p in COALITION:
        return "LNC"
    if p in LABOR:
        return "ALP"
    if p in GREENS:
        return "GRN"
    return p


# ----------------------------------------------------------------------------------------------- static tables

def pair_key(groups) -> tuple[str, str]:
    """Orientation of a two-candidate contest: Labor first, else Coalition first, else alphabetical."""
    g = sorted(set(groups), key=lambda x: (x != "ALP", x != "LNC", x))
    return tuple(g)


def final_booths(frame: dict, pre: dict) -> dict[int, dict]:
    """A finished election's polling places keyed by polling place id: TCP votes and first preferences by party group."""
    C = pre["cands"]
    grp = {c: group_of(v) for c, v in C.items()}
    out = {}
    for did, d in frame["div"].items():
        pair = frozenset(grp[c] for c in (d["tcp"] or {}) if c in grp)
        for pid, r in d["pp"].items():
            if not r["fp"] and not r["tcp"]:
                continue
            tcp = {}
            for c, v in r["tcp"].items():
                tcp[grp[c]] = tcp.get(grp[c], 0) + v
            fp = {}
            for c, v in r["fp"].items():
                if c in grp:
                    fp[grp[c]] = fp.get(grp[c], 0) + v
            out[pid] = {"div": did, "pair": pair, "tcp": tcp, "fp": fp, "formal": r["formal"]}
    return out


def final_divisions(frame: dict, pre: dict) -> dict[int, dict]:
    """Final result per division: TCP pair and votes (group keyed), 2PP votes, winner group, formal votes."""
    C = pre["cands"]
    grp = {c: group_of(v) for c, v in C.items()}
    out = {}
    for did, d in frame["div"].items():
        tcp = {}
        for c, v in (d["tcp"] or {}).items():
            tcp[grp[c]] = tcp.get(grp[c], 0) + v
        win = max(tcp, key=tcp.get) if tcp else None
        fpg = {}
        for c, v in d["fp"].items():
            if c in grp:
                fpg[grp[c]] = fpg.get(grp[c], 0) + v
        out[did] = {"tcp": tcp, "tpp": d["tpp"], "winner": win, "fp": fpg, "formal": d["fp_formal"], "maverick": d["maverick"]}
    return out


def static_tables(pre: dict, prev_booths: dict[int, dict] | None, state_of: dict | None = None) -> dict:
    """Everything the observation step needs about an election before its count starts."""
    C, D, B = pre["cands"], pre["divs"], pre["booths"]
    grp = {c: group_of(v) for c, v in C.items()}
    by_div: dict[int, list] = {}
    for (did, pid), b in B.items():
        by_div.setdefault(did, []).append(b)
    return {"grp": grp, "cands": C, "divs": D, "booths": by_div, "prev": prev_booths or {},
            "booth_hist": {pid: b["hist_formal"] for (did, pid), b in B.items()},
            "incumbent": {did: next((grp[c] for c, v in C.items() if v["div"] == did and v["incumbent"]), None) for did in D}}


def prior_share(S: dict, did: int, gA: str, gB: str) -> tuple[float | None, str]:
    """Prior final share of group A in the A-v-B count of a division, on this election's boundaries.
    Classic Labor-v-Coalition contests use the AEC's own notional two-party-preferred votes; every other pair is built
    from the previous election's polling-place TCP counts in the same pair, weighted by the votes the AEC's Preload
    says each place took last time. None if the previous election did not have this pair at enough of the places."""
    D = S["divs"][did]
    if (gA, gB) == ("ALP", "LNC") and D["hist_tpp"] and sum(D["hist_tpp"].values()) > 0:
        return D["hist_tpp"][1] / (D["hist_tpp"][1] + D["hist_tpp"][2]), "notional"
    num = den = tot = 0.0
    for b in S["booths"].get(did, []):
        w = b["hist_formal"]
        tot += w
        p = S["prev"].get(b["pp"])
        if p and p["pair"] == frozenset((gA, gB)):
            a, o = p["tcp"].get(gA, 0), p["tcp"].get(gB, 0)
            if a + o > 0:
                num += w * a / (a + o)
                den += w
    if tot > 0 and den >= 0.4 * tot:
        return num / den, "booths"
    return None, "none"


def observe(frame: dict, S: dict) -> dict:
    """Reduce one feed frame to per-division sufficient statistics for the projection (independent of model settings)."""
    grp, C = S["grp"], S["cands"]
    out = {}
    for did, d in frame["div"].items():
        if did not in S["divs"]:
            continue
        o = {"fp_ret": d["fp_ret"], "fp_exp": d["fp_exp"], "tcp_ret": d["tcp_ret"], "maverick": d["maverick"],
             "restricted": d["restricted"], "pair": None, "tpp": d["tpp"], "fp_formal": d["fp_formal"]}
        fpg: dict[str, int] = {}
        for c, v in d["fp"].items():
            if c in grp:
                fpg[grp[c]] = fpg.get(grp[c], 0) + v
        o["fpg"] = fpg
        o["booth_fp"] = sum(r["formal"] for r in d["pp"].values() if r["fp"])
        o["booth_hist"] = sum(S["booth_hist"].get(pid, 0) for pid, r in d["pp"].items() if r["fp"])
        tcp = d["tcp"]
        if tcp and not d["maverick"] and not d["restricted"]:
            gs = {c: grp[c] for c in tcp if c in grp}
            if len(set(gs.values())) == 2:
                gA, gB = pair_key(gs.values())
                o["pair"] = (gA, gB)
                o["tcp_total"] = (sum(v for c, v in tcp.items() if gs.get(c) == gA), sum(v for c, v in tcp.items() if gs.get(c) == gB))
                prior, how = prior_share(S, did, gA, gB)
                o["prior"], o["prior_how"] = prior, how
                n = va = vm = dev = ssq = 0
                for pid, r in d["pp"].items():
                    if not r["tcp"]:
                        continue
                    a = sum(v for c, v in r["tcp"].items() if gs.get(c) == gA)
                    b = sum(v for c, v in r["tcp"].items() if gs.get(c) == gB)
                    if a + b == 0:
                        continue
                    n += a + b
                    va += a
                    p = S["prev"].get(pid)
                    if p and p["pair"] == frozenset((gA, gB)) and p["tcp"].get(gA, 0) + p["tcp"].get(gB, 0) > 0:
                        pa = p["tcp"].get(gA, 0) / (p["tcp"].get(gA, 0) + p["tcp"].get(gB, 0))
                        vm += a + b
                        dev += a - pa * (a + b)
                        ssq += (a - pa * (a + b)) ** 2 / (a + b)
                o.update(n=n, va=va, vm=vm, dev=dev, ssq=ssq, booths=sum(1 for r in d["pp"].values() if r["tcp"]))
        elif d["maverick"] and d["tpp"] and not d["restricted"]:
            # the AEC's pre-selected pair was wrong, so it publishes no two-candidate count: use its division-level
            # Labor-v-Coalition two-party count (no polling-place detail), and let the first-preference leaders decide
            # whether a third candidate overtakes (see `simulate`)
            a, b = d["tpp"].get(1, 0), d["tpp"].get(2, 0)
            prior, how = prior_share(S, did, "ALP", "LNC")
            o.update(pair=("ALP", "LNC"), tcp_total=(a, b), prior=prior, prior_how=how, n=a + b, va=a, vm=0, dev=0.0,
                     ssq=0.0, booths=0, fallback=True)
        out[did] = o
    return out


# ----------------------------------------------------------------------------------------------- projection

SEAT_GROUPS = ("ALP", "LNC", "GRN", "OTH")
N_SEATS = 150
MAJORITY = 76


def seat_group(g: str) -> str:
    return g if g in ("ALP", "LNC", "GRN") else "OTH"


def default_params() -> dict:
    """Settings of the count-night projection; the values used for an election are chosen on EARLIER elections only
    (configs/au2025.toml records them and where they came from)."""
    return {"K_d": 15000.0, "K_s": 80000.0, "K_n": 200000.0, "s_d0": 0.04, "s_floor": 0.008, "s_st0": 0.012,
            "s_nat0": 0.029, "s_nat_f": 0.0, "s_st_f": 0.0, "m_mode": "min", "fb": 0.5, "s_np": 0.12, "K_np": 3000.0, "fp_b": 12.0, "p_inc": 0.85, "m_a": -1.5, "m_b": 12.0}


def project(obs: dict, S: dict, p: dict) -> dict:
    """Per-division projection of the final two-candidate share of the first-listed group (Labor where it stands).

    The swing of a division is measured on the polling places already counted, against the same places at the previous
    election (the AEC's own matched-places idea), pooled towards the swing of its state and of the country. Only
    Labor-v-Coalition contests feed the state and national swing. Settings: version 1 has no floors under the state and
    national swing errors (s_st_f = s_nat_f = 0) and compares a third candidate with the weaker major party (m_mode
    "min"); version 2, a sensitivity added after the 2025 count had been scored, has floors and compares with the leader."""
    ids = sorted(S["divs"])
    n = len(ids)
    mean = [0.5] * n
    sd_div = [0.0] * n
    st_w = [0.0] * n            # weight on the state shock
    nat_w = [0.0] * n
    pair = [None] * n
    kind = ["none"] * n
    prior = [None] * n
    ne = [0.0] * n
    sw = [0.0] * n
    classic = [False] * n
    state = [S["divs"][d]["state"] for d in ids]
    for i, did in enumerate(ids):
        o = obs.get(did)
        if not o or not o.get("pair"):
            continue
        pair[i] = o["pair"]
        pr = o["prior"]
        classic[i] = o["pair"] == ("ALP", "LNC")
        if pr is None:
            kind[i] = "noprior"
            prior[i] = 0.5
            if o["n"] > 0:
                sw[i], ne[i] = o["va"] / o["n"] - 0.5, float(o["n"])
            else:
                sw[i], ne[i] = 0.0, 0.0
            continue
        prior[i] = pr
        if o["n"] == 0:
            kind[i] = "prior"
        elif o["vm"] >= 0.3 * o["n"]:
            kind[i] = "matched"
            sw[i], ne[i] = o["dev"] / o["vm"], float(o["vm"])
        else:
            kind[i] = "unmatched"
            sw[i], ne[i] = o["va"] / o["n"] - pr, p["fb"] * o["n"]
    use = [i for i in range(n) if classic[i] and kind[i] in ("matched", "unmatched")]
    num = sum(ne[i] * sw[i] for i in use)
    den = sum(ne[i] for i in use)
    mu_nat = num / (den + p["K_n"])
    sd_nat = (p["s_nat_f"] ** 2 + p["s_nat0"] ** 2 * p["K_n"] / (den + p["K_n"])) ** 0.5
    st_mu, st_sd = {}, {}
    for s in set(state):
        us = [i for i in use if state[i] == s]
        nn = sum(ne[i] for i in us)
        mu_s = (sum(ne[i] * sw[i] for i in us) + p["K_s"] * mu_nat) / (nn + p["K_s"])
        st_mu[s] = mu_s
        st_sd[s] = (p["s_st_f"] ** 2 + p["s_st0"] ** 2 * p["K_s"] / (nn + p["K_s"])) ** 0.5
    for i in range(n):
        if pair[i] is None:
            continue
        if kind[i] == "noprior":
            k = p["K_np"]
            mean[i] = 0.5 + (ne[i] * sw[i]) / (ne[i] + k)
            sd_div[i] = (p["s_floor"] ** 2 + p["s_np"] ** 2 * k / (ne[i] + k)) ** 0.5
            continue
        shrink_to = st_mu[state[i]] if classic[i] else 0.0
        mean[i] = prior[i] + (ne[i] * sw[i] + p["K_d"] * shrink_to) / (ne[i] + p["K_d"])
        sd_div[i] = (p["s_floor"] ** 2 + p["s_d0"] ** 2 * p["K_d"] / (ne[i] + p["K_d"])) ** 0.5
        if classic[i]:
            st_w[i] = nat_w[i] = 1.0
    return {"ids": ids, "state": state, "pair": pair, "kind": kind, "prior": prior, "mean": mean, "sd_div": sd_div,
            "st_w": st_w, "nat_w": nat_w, "mu_nat": mu_nat, "sd_nat": sd_nat, "st_sd": st_sd, "st_mu": st_mu,
            "classic": classic}


def simulate(pj: dict, obs: dict, S: dict, p: dict, draws: int, rng) -> dict:
    """Joint draws of the 150 division winners and the seat counts of Labor, Coalition, Greens and others."""
    import numpy as np

    ids, n = pj["ids"], len(pj["ids"])
    states = sorted(set(pj["state"]))
    z_nat = rng.standard_normal(draws)
    z_st = {s: rng.standard_normal(draws) for s in states}
    z_d = rng.standard_normal((draws, n))
    win = np.empty((draws, n), dtype=np.int8)             # index into SEAT_GROUPS
    grp = {g: i for i, g in enumerate(SEAT_GROUPS)}
    for i, did in enumerate(ids):
        pr = pj["pair"][i]
        if pr is not None:
            sh = pj["mean"][i] + pj["sd_div"][i] * z_d[:, i]
            if pj["st_w"][i]:
                sh = sh + pj["sd_nat"] * z_nat + pj["st_sd"][pj["state"][i]] * z_st[pj["state"][i]]
            a, b = grp[seat_group(pr[0])], grp[seat_group(pr[1])]
            win[:, i] = np.where(sh > 0.5, a, b)
            o = obs.get(did) or {}
            if o.get("fallback"):
                fpg, tot = o.get("fpg") or {}, o.get("fp_formal") or 0
                thirds = sorted(((g, v) for g, v in fpg.items() if g not in ("ALP", "LNC")), key=lambda kv: -kv[1])
                if thirds and tot > 0:
                    g3, v3 = thirds[0]
                    major = (min if p["m_mode"] == "min" else max)(fpg.get("ALP", 0), fpg.get("LNC", 0))
                    x = v3 / tot - major / tot       # third candidate against the weaker (v1) or the leading (v2) major party
                    p3 = 1 / (1 + np.exp(-(p["m_a"] + p["m_b"] * x)))
                    win[:, i] = np.where(rng.random(draws) < p3, grp[seat_group(g3)], win[:, i])
            continue
        # no usable two-candidate count (restricted before 6pm, or the AEC's pre-selected pair was wrong):
        # the leader on first preferences, or the sitting group, with a logistic weight on the lead
        o = obs.get(did) or {}
        fp = sorted((o.get("fpg") or {}).items(), key=lambda kv: -kv[1])
        inc = S["incumbent"].get(did)
        tot = o.get("fp_formal") or 0
        if len(fp) >= 2 and tot > 0:
            (g1, v1), (g2, v2) = fp[0], fp[1]
            lead = (v1 - v2) / tot
            p1 = 1 / (1 + np.exp(-(p["fp_b"] * lead)))        # lead 0 -> even; the pair's own preferences are unknown
            if inc in (g1, g2):
                p1 = 0.5 * p1 + 0.5 * (p["p_inc"] if inc == g1 else 1 - p["p_inc"]) if tot < 0.02 * S["divs"][did]["enrol"] else p1
            u = rng.random(draws)
            win[:, i] = np.where(u < p1, grp[seat_group(g1)], grp[seat_group(g2)])
        else:
            u = rng.random(draws)
            other = "LNC" if inc == "ALP" else "ALP"
            win[:, i] = np.where(u < p["p_inc"], grp[seat_group(inc or "OTH")], grp[seat_group(other)])
    seats = np.stack([(win == k).sum(1) for k in range(len(SEAT_GROUPS))], axis=1)
    return {"win": win, "seats": seats}


# ----------------------------------------------------------------------------------------------- markets

def market_hit(m: dict, seats, win, ids: list[int]):
    """Boolean array over draws (or a single draw): does the market's YES condition hold? `seats` (n, 4) in
    SEAT_GROUPS order; `win` (n, 150) winner group index per division in the order `ids`."""
    import numpy as np

    k = m["kind"]
    s = np.atleast_2d(seats)
    ALP, LNC, GRN, OTH = (s[:, i] for i in range(4))
    if k == "most":
        mine = {"ALP": ALP, "LNC": LNC, "GRN": GRN, "OTH": OTH}[m["party"]]
        others = [x for g, x in (("ALP", ALP), ("LNC", LNC), ("GRN", GRN), ("OTH", OTH)) if g != m["party"]]
        hit = np.ones(len(s), dtype=bool)
        for o in others:
            hit &= mine > o
        return hit
    if k == "majority":
        return {"ALP": ALP, "LNC": LNC}[m["party"]] >= MAJORITY
    if k == "hung":
        return (ALP < MAJORITY) & (LNC < MAJORITY)
    if k == "seats":
        x = {"ALP": ALP, "LNC": LNC, "GRN": GRN, "OTH": OTH}[m["party"]]
        return (x >= m["lo"]) & (x <= m["hi"])
    if k == "margin":
        d = ALP - LNC
        return (d >= m["lo"]) & (d <= m["hi"])
    if k == "div":
        w = np.atleast_2d(win)[:, ids.index(m["div"])]
        return np.isin(w, [SEAT_GROUPS.index(g) for g in m["groups"]])
    raise ValueError(k)


def seats_of_final(fin: dict[int, dict], ids: list[int]):
    """(seats[4], win[150]) from final division results."""
    import numpy as np

    win = np.array([SEAT_GROUPS.index(seat_group(fin[i]["winner"])) if fin[i]["winner"] else 3 for i in ids], dtype=np.int8)
    return np.array([(win == k).sum() for k in range(4)]), win
