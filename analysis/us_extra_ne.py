"""Priors and configs of the VA / MD / PA-11 secondary House models (brunder.extra_va, extra_md, extra_pa).

    uv run python analysis/us_extra_ne.py priors   # data_public/us2026_extra_priors_{va,md,pa}.csv
    uv run python analysis/us_extra_ne.py configs  # configs/us2026_extra/<st>-<nn>_v1.toml

Priors are the 2024 president (Harris v Trump) per county piece of each district, the allocation of
analysis/us_house_night.py county_pieces (the registered House priors' method; VA and MD lines did not change for
2026). PA-11 rows are copied from data_public/us2026_house_priors.csv. Only units the feed lists are kept (checked
against the 2026 Virginia item and the 2024 Maryland county table; the pieces are the same sets).
Settings of the configs = data_public/us2026_house_calibration.json "chosen" for the feed group: Virginia and Maryland
(no early/election-day split used) take the group without a vote-type split (FL, SC, OH replay), PA-11 the group with one
and early_share 0.23 as the registered PA House configs (same feed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
PIECES = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/cne/pieces_va_md.csv")

# district -> (module, feed kind, source name for a link in the header, source url)
DISTRICTS = {
    "VA-01": ("va", "va_house", "Virginia Department of Elections, election night reporting",
              "https://enr.elections.virginia.gov/results/public/virginia/elections/2026-November-General"),
    "VA-05": ("va", "va_house", "Virginia Department of Elections, election night reporting",
              "https://enr.elections.virginia.gov/results/public/virginia/elections/2026-November-General"),
    "MD-01": ("md", "md_house", "Maryland State Board of Elections, unofficial 2026 general election results",
              "https://elections.maryland.gov/elections/2026/general_results/gen_results_2026_4_1.html"),
    "PA-11": ("pa", "pa_house_x", "Pennsylvania Department of State, election returns (county break, U.S. House 11th)",
              "https://www.electionreturns.pa.gov/"),
}
PRIOR_ONLY = ("VA-02", "VA-07")        # recorded by brunder-extra-rec (Senate/VA-02/VA-07); priors only, not competitive
HEAD = ["# US midterms 2026 House district priors, secondary sources ({mod}; analysis/us_extra_ne.py priors):",
        "# 2024 president (Harris, Trump, all candidates) in each county's part of the district. Source",
        "# '<st>-<nn>_pres_2024', unit_id us-<st>-<5-digit county FIPS>. Sources: MIT Election Data and Science Lab,",
        "#   County Presidential Election Returns 2000-2024 (doi:10.7910/DVN/VOQCHQ) and U.S. President Precinct-Level",
        "#   Returns 2024 (doi:10.7910/DVN/XDJYKC), CC0; US Census Bureau 119th/120th Congress block equivalency files and",
        "#   2020 P.L. 94-171 population (public domain); allocation analysis/us_house.py (as data_public/us2026_house_priors.csv)."]


def priors() -> list[Path]:
    pcs = pd.read_csv(PIECES, dtype={"county": str})
    out = []
    for mod, sts in (("va", ("VA",)), ("md", ("MD",)), ("pa", ())):
        if mod == "pa":
            src = pd.read_csv(ROOT / "data_public" / "us2026_house_priors.csv", comment="#")
            body = src[src["source"] == "pa-11_pres_2024"]
        else:
            g = pcs[pcs["st"].isin(sts)].groupby(["st", "cd26", "county"])[["d24", "r24", "t24"]].sum().reset_index()
            g = g[g["t24"] > 0.5]
            keep = {d for d, (m, *_r) in DISTRICTS.items() if m == mod} | set(PRIOR_ONLY)
            g = g[[f"{s}-{int(c):02d}" in keep for s, c in zip(g["st"], g["cd26"])]]
            body = pd.DataFrame({"source": [f"{s.lower()}-{int(c):02d}_pres_2024" for s, c in zip(g["st"], g["cd26"])],
                                 "unit_id": "us-" + g["st"].str.lower() + "-" + g["county"],
                                 "dem": g["d24"].round(1), "rep": g["r24"].round(1), "total": g["t24"].round(1)})
        f = ROOT / "data_public" / f"us2026_extra_priors_{mod}.csv"
        f.write_text("\n".join(h.format(mod=mod) for h in HEAD) + "\n" + body.to_csv(index=False))
        print(f"{f.name}: {body['source'].nunique()} districts, {len(body)} rows")
        out.append(f)
    return out


_CFG = """# SECONDARY SOURCE, ADDED 2026-11-02, UNREGISTERED. US midterms 2026-11-03, {did} U.S. House: model v1 on {src_name}.
# No swing: the count alone moves the 2024 president baseline. Counted units = the district's counties (a split
# county: only its part in the district, as the feed reports it).
# Prior = 2024 president in each county's part of the district (data_public/us2026_extra_priors_{mod}.csv,
# analysis/us_extra_ne.py priors; same allocation as site/data/us2026/house/districts.json). Settings (tau, rho, sigma_uf0)
# are the group "{grp}" settings of data_public/us2026_house_calibration.json: {cal}
# The {st_name} feed is not in that replay; its parser is checked on {check}.
# Nothing here was chosen on 2026 count data. Market selection: P(D) {p_txt} in markets.json built {built}.
id = "us2026_extra_{st}-{nn}_v1"

[election]
eid = "us2026"
race_id = "house:{ST}-{nn}"
name = "{did} U.S. House"
state = "{st}"
race = "house-{nn}"
feed = "{kind}"
source = "{src_name}"
source_url = "{src_url}"
poll_close_utc = "{close}"
a = {{ party = "DEM", name = "{a}", color = "#1f5fbf" }}
b = {{ party = "REP", name = "{b}", color = "#c8102e" }}

[prior]
share = "{st}-{nn}_pres_2024"
votes = "{st}-{nn}_pres_2024"

[model]
tau = {tau}
rho = {rho}
sigma_mun0 = 0.08
sigma_uf0 = {uf}

[count]
turnout_ratio = {turnout}
f_max = 0.95
f_complete = 0.98
use_outstanding = false
learn_turnout = true
vote_type = {vt}
{early}
[markets.polymarket]
event = "{pm_event}"
market = "will-the-democratic-party-win-the-{st}-{nn}-house-seat"
question = "Will the Democratic Party win the {did} House seat?"
yes_is = "a"

[markets.kalshi]
ticker = ""                # Kalshi event {k_event}, YES = {k_event}-D
"""
_CHECK = {"va": "the 2024 general 1st and 5th District items of the same API (certified totals matched exactly)",
          "md": "the 2024 general District 1 county table (official totals matched exactly)",
          "pa": "the registered PA 2024 House document of PA-10 (district 11's URL was not requested)"}
_STATE = {"va": "Virginia", "md": "Maryland", "pa": "Pennsylvania"}
_CLOSE = {"va": "2026-11-04T00:00:00Z", "md": "2026-11-04T01:00:00Z", "pa": "2026-11-04T01:00:00Z"}


def configs() -> list[Path]:
    from brunder import us
    mk = json.loads((ROOT / "site/data/us2026/house/markets.json").read_text())
    cal = json.loads((ROOT / "data_public/us2026_house_calibration.json").read_text())["chosen"]
    d = ROOT / "configs" / "us2026_extra"
    d.mkdir(exist_ok=True)
    out = []
    for did, (mod, kind, src_name, src_url) in DISTRICTS.items():
        st, nn = did[:2].lower(), did[3:]
        v = mk["districts"][did]
        pm, ka = v.get("pm") or {}, v.get("kalshi") or {}
        grp = "split|v1" if mod == "pa" else "nosplit|v1"
        c = cal[grp]
        bc = c["by_check"]
        cal_txt = (f"90 % coverage {bc['0.05']['cover90']:.2f}/{bc['0.25']['cover90']:.2f}/{bc['0.5']['cover90']:.2f} "
                   f"at 5/25/50 % counted, mean |error| {bc['0.05']['mae']:.3f}/{bc['0.25']['mae']:.3f}/{bc['0.5']['mae']:.3f}.")
        p_txt = ", ".join(f"{k} {x['p']:.3f}" for k, x in (("Polymarket", pm), ("Kalshi", ka)) if x.get("p") is not None)
        cands = {**(ka.get("cands") or {}), **(pm.get("cands") or {})}
        vt = bool(c["vote_type"])
        text = _CFG.format(did=did, mod=mod, grp=grp.split("|")[0], cal=cal_txt, st_name=_STATE[mod], check=_CHECK[mod],
                           p_txt=p_txt, built=mk["built"], st=st, ST=st.upper(), nn=nn, kind=kind, src_name=src_name,
                           src_url=src_url, close=_CLOSE[mod], a=cands.get("D", "Democratic candidate").replace('"', "'"),
                           b=cands.get("R", "Republican candidate").replace('"', "'"), tau=c["tau"], rho=c["rho"],
                           uf=c["sigma_uf0"], turnout=us.EST_TURNOUT, vt=str(vt).lower(),
                           early="early_share = 0.23\n" if vt else "", pm_event=pm.get("slug", ""),
                           k_event=ka.get("event", ""))
        f = d / f"{st}-{nn}_v1.toml"
        f.write_text(text)
        out.append(f)
    print(*[p.name for p in out])
    return out


if __name__ == "__main__":
    {"priors": priors, "configs": configs}[sys.argv[1]]()
