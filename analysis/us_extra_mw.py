"""Midwest/West House coverage (added 2026-11-02, unregistered, secondary): priors, configs and parser checks for
the U.S. House districts read from brunder.extra_clarity (IA, CO Clarity ENR) and brunder.extra_mt (MT results web
service). docs/us_coverage_2026_mw.md has the per-district verdicts.

    uv run python analysis/us_extra_mw.py priors    # data_public/us2026_extra_priors_{clarity_mw,mt}.csv
    uv run python analysis/us_extra_mw.py configs   # configs/us2026_extra/<st>-<nn>_v1/v2.toml
    uv run python analysis/us_extra_mw.py verify    # parsers on the 2024 fixtures vs certified House totals

Prior: 2024 president per county piece of each 2026 district, exactly analysis/us_house_night.county_pieces (the
allocation of site/data/us2026/house/districts.json); IA, CO and MT were not redrawn and are not in
data_public/us2026_house_priors.csv (its states are brunder.us_house_night.HOUSE_STATES), so the rows are computed
with the same function. Settings: the replayed "feeds without a vote-type split" group of
data_public/us2026_house_calibration.json (none of these feeds splits by vote type).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_house_night as N  # noqa: E402

from brunder import us  # noqa: E402
from brunder import extra_clarity as C  # noqa: E402
from brunder import extra_in as IN  # noqa: E402
from brunder import extra_mo as MO  # noqa: E402
from brunder import extra_mt as M  # noqa: E402
from brunder import extra_wi as WI  # noqa: E402

ROOT = us.ROOT_DIR
DISTRICTS = {"clarity_mw": {"ia": (1, 2, 3), "co": (3, 4, 5, 8)}, "mt": {"mt": (1,)}, "mo": {"mo": (2,)}, "in": {"in": (5,)}, "wi": {"wi": (1,)}}
MODULES = {"clarity_mw": C, "mt": M, "mo": MO, "in": IN, "wi": WI}
OUT_CFG = ROOT / "configs" / "us2026_extra"
FIX = ROOT / "tests" / "fixtures" / "us"
SOURCE_URL = {"ia": "https://electionresults.iowa.gov/IA/", "co": "https://results.enr.clarityelections.com/CO/",
              "mt": "https://electionresults.mt.gov/ (data: esersws.mt.gov/ResultsAjax.svc/GetMapData)",
              "mo": "https://extcontent.stlouisco.com/BOE/eResults/ ; https://www.livevoterturnout.com/ENR/stcharlesmoenr/",
              "in": "https://enr.indianavoters.in.gov/",
              "wi": "https://apps.kenoshacounty.org/ElectionResults_v2/ ; http://elections.co.rock.wi.us/"}
SOURCE_NAME = {"ia": "Iowa Secretary of State, Election Night Reporting (Clarity)",
               "co": "Colorado Secretary of State, Election Night Reporting (Clarity)",
               "mt": "Montana Secretary of State, election results web service",
               "mo": "St. Louis County Board of Elections and St. Charles County Election Authority",
               "in": "Indiana Election Division, Election Night Reporting",
               "wi": "Kenosha County and Rock County clerks"}
NOTE = {"mo": ("# PARTIAL COVERAGE: St. Louis and St. Charles counties = 84 % of the district's 2024 presidential vote;\n"
               "# Franklin and Warren counties (16 %) have no night source and are projected from the prior and swing.\n"),
        "wi": ("# PARTIAL COVERAGE: Kenosha and Rock (WI-01 part) counties = 38 % of the district's 2024 presidential vote;\n"
               "# Racine (27 %, site 403), Milwaukee (21 %, Cloudflare challenge) and Walworth (14 %, no fixed URL) are\n"
               "# projected from the prior and the swing of the covered counties.\n")}


def priors() -> list[Path]:
    states = sorted({st for d in DISTRICTS.values() for st in d})
    pcs = N.county_pieces(states)
    g = pcs.groupby(["st", "cd26", "county"])[["d24", "r24", "t24"]].sum().reset_index()
    g = g[g["t24"] > 0.5]
    dj = json.loads((ROOT / "site" / "data" / "us2026" / "house" / "districts.json").read_text())["districts"]
    out = []
    for src, dd in DISTRICTS.items():
        rows, worst = [], 0.0
        for st, ns in dd.items():
            for n in ns:
                x = g[(g["st"] == st.upper()) & (g["cd26"] == n)]
                ref = dj[f"{st.upper()}-{n:02d}"]["pres24"]
                worst = max(worst, abs(x["d24"].sum() - ref["dv"]) / ref["dv"], abs(x["r24"].sum() - ref["rv"]) / ref["rv"])
                rows.append(pd.DataFrame({"source": N.hn.prior_source(st, n), "unit_id": f"us-{st}-" + x["county"],
                                          "dem": x["d24"].round(1), "rep": x["r24"].round(1),
                                          "total": x["t24"].round(1)}))
        assert worst < 1e-3, f"{src}: district sums differ from districts.json by {worst:.2%}"
        pri = pd.concat(rows, ignore_index=True)
        head = [f"# US midterms 2026 House district priors, secondary source {src} (analysis/us_extra_mw.py priors;",
                "# added 2026-11-02, unregistered): 2024 president (Harris, Trump, all candidates) in each county's part",
                "# of each 2026 district (analysis/us_house_night.county_pieces, the allocation of",
                f"# site/data/us2026/house/districts.json; max deviation of district sums {worst:.1e}).",
                "# Sources: MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024",
                "#   (doi:10.7910/DVN/VOQCHQ) and U.S. President Precinct-Level Returns 2024 (doi:10.7910/DVN/XDJYKC), CC0;",
                "#   US Census Bureau 119th/120th Congress block equivalency files (public domain)."]
        f = ROOT / "data_public" / f"us2026_extra_priors_{src}.csv"
        f.write_text("\n".join(head) + "\n" + pri.to_csv(index=False))
        print(f"{f.name}: {pri['source'].nunique()} districts, {len(pri)} county pieces, max dev {worst:.1e}")
        out.append(f)
    return out


_HEAD = ("# SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED. {did} U.S. House from {sname} ({kind} feed,\n"
         "# brunder.{mod}; analysis/us_extra_mw.py configs). Kept out of registered scoring.\n")


def configs() -> list[Path]:
    m = json.loads((ROOT / "site" / "data" / "us2026" / "house" / "markets.json").read_text())
    cal = json.loads(N.PUBLIC_CAL.read_text())
    n_grp = sum(d[:2].lower() not in N.SPLIT_FEEDS for d in cal["districts"])
    OUT_CFG.mkdir(parents=True, exist_ok=True)
    out = []
    for src, dd in DISTRICTS.items():
        mod = MODULES[src]
        for st, ns in dd.items():
            for n in ns:
                did, nn = f"{st.upper()}-{n:02d}", f"{n:02d}"
                mk = m["districts"][did]
                pm, ka = mk.get("pm") or {}, mk.get("kalshi") or {}
                a = (pm.get("cands") or {}).get("D") or (ka.get("cands") or {}).get("D") or "Democratic candidate"
                b = (pm.get("cands") or {}).get("R") or (ka.get("cands") or {}).get("R") or "Republican candidate"
                pmm = N._pm_market(pm["slug"]) if pm.get("slug") else None
                p_txt = ", ".join(f"{k} {v:.3f}" for k, v in (("Polymarket", pm.get("p")), ("Kalshi", ka.get("p")))
                                  if v is not None)
                for ver in ("v1", "v2"):
                    c = cal["chosen"][f"nosplit|{ver}"]
                    bc = c["by_check"]
                    cal_txt = (f"{mod.STATES[st]}'s feed is not in the replay (added 2026-11-02); settings of the "
                               f"replayed group of {N.GROUP_LABEL['nosplit'].format(n=n_grp)}; 90 % coverage "
                               f"{bc['0.05']['cover90']:.2f}/{bc['0.25']['cover90']:.2f}/{bc['0.5']['cover90']:.2f} at "
                               f"5/25/50 % counted, mean |error| {bc['0.05']['mae']:.3f}/{bc['0.25']['mae']:.3f}/"
                               f"{bc['0.5']['mae']:.3f}.")
                    if ver == "v1":
                        head, prior_extra, model_extra = ("No swing: the count alone moves the 2024 president "
                                                          "baseline."), "", ""
                    else:
                        head = ("Identical to v1 except a prior on the district swing = the generic-ballot shift "
                                "(markets.json generic_ballot) with its error and the spread of House vs president.")
                        prior_extra = f"generic_ballot_shift = {N.GB_SHIFT}   # VoteHub generic-ballot average 0.5477 − 0.4925\n"
                        model_extra = (f"swing_prior_mean = {N.GB_SHIFT}   # = generic_ballot_shift\n"
                                       f"swing_prior_sd = {N.V2_SD}     # sqrt({N.GB_SD}^2 generic-ballot error + "
                                       f"{N.HVP_SD}^2 House vs president, 2024)\n")
                    text = N._CFG.format(
                        did=did, ver=ver, head=head, cal=cal_txt, p_txt=p_txt, built=m["built"], st=st, nn=nn,
                        rid=N.hn.race_id(st, n), close=mod.POLL_CLOSE[st], a=a.replace('"', "'"),
                        b=b.replace('"', "'"), src=N.hn.prior_source(st, n), prior_extra=prior_extra, tau=c["tau"],
                        rho=c["rho"], sigma=N.SIGMA_MUN0, uf=c["sigma_uf0"], model_extra=model_extra,
                        turnout=N.TURNOUT, vt="false", early="", pm_event=pm.get("slug") or "",
                        pm_market=(pmm or {}).get("market", ""),
                        pm_question=(pmm or {}).get("question", "").replace('"', "'"),
                        k_txt=f"event {ka['event']}, YES = {ka['event']}-D" if ka.get("event") else "market: none")
                    text = text.replace(f'id = "us2026_house_{st}-{nn}_{ver}"', f'id = "us2026_extra_{st}-{nn}_{ver}"')
                    text = text.replace("Frozen before polls close; the integrator registers it (prereg/registry.jsonl).",
                                        "Frozen before polls close; NOT registered (secondary source).")
                    text = text.replace("data_public/us2026_house_priors.csv,\n# analysis/us_house_night.py priors;",
                                        f"data_public/us2026_extra_priors_{src}.csv,\n# analysis/us_extra_mw.py priors;")
                    text = text.replace(f'state = "{st}"\n',
                                        f'state = "{st}"\nsource = "{SOURCE_NAME[st]}"\nsource_url = "{SOURCE_URL[st]}"\n')
                    text = _HEAD.format(did=did, sname=SOURCE_NAME[st], kind=f"{st}_house",
                                        mod=mod.__name__.split(".")[-1]) + NOTE.get(st, "") + text
                    f = OUT_CFG / f"{st}-{nn}_{ver}.toml"
                    f.write_text(text)
                    out.append(f)
    print(f"{len(out)} configs in {OUT_CFG}")
    return out


def _fx(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


def verify() -> pd.DataFrame:
    """Parsed 2024 House totals (fixtures) vs districts.json house24 (certified)."""
    dj = json.loads((ROOT / "site" / "data" / "us2026" / "house" / "districts.json").read_text())["districts"]
    parsed = {
        "ia": C.parse("ia_house", {r: _fx(f"extra_clarity_ia_2024_{r}.json.gz") for r in C.FILES}),
        "co": C.parse("co_house", {r: _fx(f"extra_clarity_co_2024_{r}.json.gz") for r in C.FILES}),
        "mt": M.parse("mt_house", {"d01": _fx("extra_mt_2024_cd01.json.gz")}),
        "in": IN.parse("in_house", {"offcat": _fx("extra_in_2024_offcat1005.json.gz")}),
    }
    mo = MO.parse("mo_house", {"stlouis": _fx("extra_mo_stlouis_2024_subset.pdf.gz"),
                               "stcharles": _fx("extra_mo_stcharles_2024_subset.html.gz")})
    print("MO-02 2024, St. Louis + St. Charles parts (84 % of the district):",
          mo[mo["race"] == "house-02"].groupby(["unit_id", "party"])["votes"].sum().to_dict())
    out = []
    for st, df in parsed.items():
        g = df[df["party"].isin(["DEM", "REP"])].groupby(["race", "party"])["votes"].sum().unstack()
        for race, x in g.iterrows():
            h = dj[f"{st.upper()}-{race[6:]}"]["house24"]
            out.append({"district": f"{st.upper()}-{race[6:]}", "dem": x["DEM"], "rep": x["REP"], "ref_dem": h["dv"],
                        "ref_rep": h["rv"], "counties": int(df[df["race"] == race]["unit_id"].nunique())})
    r = pd.DataFrame(out)
    r["dev"] = np.maximum((r["dem"] - r["ref_dem"]).abs(), (r["rep"] - r["ref_rep"]).abs())
    print(r.to_string(index=False))
    return r


if __name__ == "__main__":
    {"priors": priors, "configs": configs, "verify": verify}[sys.argv[1]]()
