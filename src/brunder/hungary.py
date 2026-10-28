"""Hungary, National Assembly (Országgyűlés) elections: official count parsers, seat allocation, and a count
nowcast built on brunder.nowcast.

Seats (Act CCIII of 2011 on the election of members of parliament, ss. 3, 14-16; 199 seats):
  1. 106 single-member districts (OEVK), first past the post.
  2. 93 national-list seats. Compensation ("töredék") votes per nominating organisation: every vote of a losing
     district candidate, and the winner's votes minus (runner-up's votes + 1). They go to the party list of the
     candidate's nominating organisation(s) (same jelölő csoport), else they are lost.
  3. A party list takes part if its list votes reach 5% (joint list of two parties 10%, three or more 15%) of all
     valid list votes (party and nationality lists).
  4. Nationality lists: preferential quota = floor((list + compensation votes of the passing party lists + all
     nationality-list votes) / 93 / 4); a nationality list at or above it wins one seat.
  5. The remaining seats go by D'Hondt over list + compensation votes of the passing party lists (and, in law,
     the nationality lists that won a preferential seat with their votes reduced by the quota).
`allocate_many` reproduces the official 2022 result (tests/test_hungary.py).

Count (vtr.valasztas.hu): every publication is a folder <MMDDHHMM> (Budapest time) with OevkJkv.json (each
district's candidate votes and polling stations processed) and ListasJkv.json (list votes, nation and 20
counties, domestic and postal). Two races are nowcast at each publication:
  - list: Tisza (A) vs Fidesz-KDNP (B) national list vote. brunder.nowcast.nowcast over the 20 counties (one
    pooled group) for station votes, plus a postal-vote term (non-resident postal ballots; prior = the previous
    election's postal split, shrunk toward its own count) and an envelope term (transferred and consular voters,
    counted days later, at the projected share + env_shift);
  - districts: brunder.nowcast's equations at district level (106 districts in 20 county groups) with joint
    draws (`district_draws`), so each draw has 106 winners, compensation votes, and list seats.
Seat and margin markets are priced from those draws; the national error of the list and district models share
one normal draw (perfectly correlated), a simplification.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import nowcast as nc
from .paths import RAW

RAW_HU = RAW / "hungary"
TZ = "Europe/Budapest"
LIST_SEATS = 93
SMD_SEATS = 106
# folders of the official site: candidate data ("ver") and the final count ("szavossz")
ELECTIONS = {2018: {"date": "2018-04-08"},
             2022: {"date": "2022-04-03", "ver": "04022333", "final": "04161400"},
             2026: {"date": "2026-04-12", "ver": "04112100", "final": "05071600"}}


def load_config(path: Path | str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_id"] = Path(path).stem
    return cfg


def _j(year: int, rel: str) -> dict:
    return json.loads((RAW_HU / f"ogy{year}" / rel).read_text())


def _list(d: dict) -> list:
    return d.get("list", d.get("data"))


def version_time(year: int, v: str) -> pd.Timestamp:
    """Publication time (UTC) of count folder <MMDDHHMM> (Budapest local time)."""
    t = pd.Timestamp(f"{year}-{v[:2]}-{v[2:4]} {v[4:6]}:{v[6:]}").tz_localize(TZ)
    return t.tz_convert("UTC")


def versions(year: int) -> list[str]:
    return (RAW_HU / f"ogy{year}" / "versions.txt").read_text().split()


def unit_id(maz: str, evk: str) -> str:
    return f"hu-{maz}-{evk}"


# ======================================================================================== metadata

def candidates(year: int) -> pd.DataFrame:
    """District candidates: ej_id, maz, evk, jlcs (nominating-group code), jlcs_name."""
    E = ELECTIONS[year]
    rows = [{"ej_id": c["ej_id"], "maz": c["maz"], "evk": c["evk"], "jlcs": int(c.get("jlcs_kod") or 0),
             "jlcs_name": c.get("jlcs_nev", "")} for c in _list(_j(year, f"{E['ver']}/ver/EgyeniJeloltek.json"))]
    return pd.DataFrame(rows).drop_duplicates("ej_id").set_index("ej_id")


def party_lists(year: int) -> pd.DataFrame:
    """National lists: tl_id -> jlcs, name, kind (O party, K joint, N nationality), threshold (fraction)."""
    E = ELECTIONS[year]
    rows = []
    for x in _list(_j(year, f"{E['ver']}/ver/ListakEsJeloltek.json")):
        rows.append({"tl_id": x["tl_id"], "jlcs": int(x["jlcs_kod"]), "name": x["jlcs_nev"], "kind": x["lista_tip"],
                     "threshold": float(x.get("hatar") or 5) / 100, "state": str(x.get("allapot"))})
    return pd.DataFrame(rows).set_index("tl_id")


def counties(year: int) -> dict[str, str]:
    E = ELECTIONS[year]
    return {m["maz"]: (m.get("nevi") or m.get("nev") or m.get("maz_nev")).title()
            for m in _list(_j(year, f"{E['ver']}/ver/Megyek.json"))}


# ======================================================================================== count files

def smd_count(oevk: dict, cands: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """OevkJkv -> (units: unit, maz, evk, f, stations, stations_total, valid; votes: unit, ej_id, jlcs, votes)."""
    units, votes = [], []
    for r in _list(oevk):
        j = r.get("egyeni_jkv") or {}
        u = unit_id(r["maz"], r["evk"])
        tot = j.get("norm_szk_db")
        done = j.get("feldolg_norm_szk_db")
        f = done / tot if tot else (j.get("feldar") or 0.0) / 100
        units.append({"unit": u, "maz": r["maz"], "evk": r["evk"], "f": float(f),
                      "stations": float(done) if done is not None else np.nan,
                      "stations_total": float(tot) if tot else np.nan, "valid": float(j.get("szl_ervenyes") or 0),
                      "env_counted": float(j.get("megjel_kuvi_atjel") or 0)})
        for t in j.get("tetelek") or []:
            votes.append({"unit": u, "ej_id": t["ej_id"], "votes": float(t.get("szavazat") or 0)})
    U = pd.DataFrame(units).set_index("unit")
    V = pd.DataFrame(votes, columns=["unit", "ej_id", "votes"])
    V["jlcs"] = V["ej_id"].map(cands["jlcs"]).fillna(0).astype(int)
    return U, V


def district_registered(year: int) -> pd.DataFrame:
    """Registered voters per district (OevkAdatok, published before election day): reg (all on the district's
    rolls) and env (transferred within Hungary + consular: their ballots come back in envelopes days later)."""
    E = ELECTIONS[year]
    rows = {unit_id(r["maz"], r["evk"]): {"reg": float(r["letszam"]["osszesen"]),
                                          "env": float(r["letszam"]["atjel"] + r["letszam"]["kuvi"])}
            for r in _list(_j(year, f"{E['ver']}/ver/OevkAdatok.json"))}
    return pd.DataFrame.from_dict(rows, orient="index")


def counted_share(U: pd.DataFrame, reg: pd.Series) -> float:
    """Share of polling stations processed nationally: station counts where the feed has them (2026), else each
    district's processed share weighted by its registered voters (2022)."""
    if U["stations_total"].notna().all() and U["stations_total"].sum() > 0:
        return float(U["stations"].sum() / U["stations_total"].sum())
    w = (reg["reg"] if isinstance(reg, pd.DataFrame) else reg).reindex(U.index).fillna(0.0)
    return float((U["f"] * w).sum() / w.sum()) if w.sum() else float(U["f"].mean())


def list_count(listas: dict) -> dict:
    """ListasJkv -> national and county list votes. Keys: nat (meta dict), dom / mail (Series tl_id -> votes,
    national), county (DataFrame maz x tl_id, domestic), county_f (Series maz -> share of stations processed)."""
    rows = _list(listas)
    nat = next(r for r in rows if r.get("oszint") == "5")
    dom = pd.Series({t["tl_id"]: float(t.get("hazai_szavazat") or 0) for t in nat.get("tetelek") or []}, dtype=float)
    mail = pd.Series({t["tl_id"]: float(t.get("level_szavazat") or 0) for t in nat.get("tetelek") or []}, dtype=float)
    cty, cf = {}, {}
    for r in rows:
        if r.get("oszint") == "4":
            cty[r["maz"]] = {t["tl_id"]: float(t.get("hazai_szavazat", t.get("osszes_szavazat")) or 0)
                             for t in r.get("tetelek") or []}
            cf[r["maz"]] = float(r.get("feldar") or 0) / 100
    meta = {k: v for k, v in nat.items() if k != "tetelek"}
    return {"nat": meta, "dom": dom, "mail": mail,
            "county": pd.DataFrame.from_dict(cty, orient="index").fillna(0.0).sort_index(),
            "county_f": pd.Series(cf, dtype=float).sort_index()}


def read_version(year: int, v: str, cands: pd.DataFrame) -> dict | None:
    """One count publication; None when neither file exists. Missing parts are None."""
    base = RAW_HU / f"ogy{year}" / v / "szavossz"
    out = {"v": v, "t": version_time(year, v), "smd": None, "list": None}
    if (base / "OevkJkv.json").exists():
        out["smd"] = smd_count(json.loads((base / "OevkJkv.json").read_text()), cands)
    if (base / "ListasJkv.json").exists():
        out["list"] = list_count(json.loads((base / "ListasJkv.json").read_text()))
    return out if out["smd"] is not None or out["list"] is not None else None


# ======================================================================================== seats

def comp_votes(votes: pd.DataFrame) -> pd.Series:
    """Compensation votes per nominating group (jlcs) from final district votes (unit, jlcs, votes)."""
    v = votes.sort_values(["unit", "votes"], ascending=[True, False])
    first = v.groupby("unit").cumcount() == 0
    second = v.groupby("unit")["votes"].transform(lambda s: s.iloc[1] if len(s) > 1 else 0.0)
    c = np.where(first, v["votes"] - (second + 1), v["votes"])
    return pd.Series(c, index=v.index).groupby(v["jlcs"]).sum()


def allocate_many(L: np.ndarray, C: np.ndarray, thr: np.ndarray, NV: np.ndarray,
                  seats: int = LIST_SEATS) -> tuple[np.ndarray, np.ndarray]:
    """List seats for each draw. L: list votes (draws x party lists; include every party list so the threshold
    base is complete), C: compensation votes (same shape), thr: threshold fraction per party list (inf = never
    passes), NV: nationality-list votes (draws x nationality lists). Returns (party seats, nationality seats)."""
    L, C, NV = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (L, C, NV))
    n = L.shape[0]
    total = L.sum(1, keepdims=True) + NV.sum(1, keepdims=True)
    ok = L >= thr[None, :] * total
    T = np.where(ok, L + C, 0.0)
    quota = np.floor((T.sum(1) + NV.sum(1)) / seats / 4)
    nat = (NV >= quota[:, None]) & (NV > 0)
    left = seats - nat.sum(1)
    V = np.concatenate([T, np.where(nat, NV - quota[:, None], 0.0)], axis=1)
    live = np.concatenate([ok, nat], axis=1)
    s = np.zeros_like(V, dtype=np.int64)
    rows = np.arange(n)
    for _ in range(seats):
        g = left > 0
        if not g.any():
            break
        q = np.where(live, V / (s + 1), -np.inf)
        j = q.argmax(1)
        s[rows[g], j[g]] += 1
        left -= g
    P = L.shape[1]
    return s[:, :P], s[:, P:] + nat.astype(np.int64)


def final_seats(year: int) -> pd.DataFrame:
    """Recompute the official seat result of `year` from the final count: per list, district seats, list seats."""
    E = ELECTIONS[year]
    cands = candidates(year)
    _, V = smd_count(_j(year, f"{E['final']}/szavossz/OevkJkv.json"), cands)
    lc = list_count(_j(year, f"{E['final']}/szavossz/ListasJkv.json"))
    pl = party_lists(year)
    tot = lc["dom"].add(lc["mail"], fill_value=0)
    party = pl[pl["kind"] != "N"]
    natl = pl[pl["kind"] == "N"]
    comp = comp_votes(V)
    Lv = tot.reindex(party.index).fillna(0).to_numpy()
    Cv = party["jlcs"].map(comp).fillna(0).to_numpy()
    s, ns = allocate_many(Lv, Cv, party["threshold"].to_numpy(), tot.reindex(natl.index).fillna(0).to_numpy())
    wins = V.loc[V.groupby("unit")["votes"].idxmax(), "jlcs"].value_counts()
    out = pd.DataFrame({"name": pl["name"], "jlcs": pl["jlcs"], "list_votes": tot.reindex(pl.index).fillna(0),
                        "comp_votes": pl["jlcs"].map(comp).fillna(0)})
    out["list_seats"] = pd.Series(np.concatenate([s[0], ns[0]]), index=list(party.index) + list(natl.index))
    out["district_seats"] = out["jlcs"].map(wins).fillna(0).astype(int)
    out["seats"] = out["list_seats"] + out["district_seats"]
    return out.sort_values("seats", ascending=False)


# ======================================================================================== prior

def prior_from_votes(votes: pd.DataFrame, a: list, b: list, key: str) -> pd.DataFrame:
    """Per unit: s0 = A / (A + B), V0 = A + B, o0 = other valid votes / (A + B); `votes` has unit, `key`, votes."""
    g = votes.assign(grp=np.where(votes[key].isin(a), "a", np.where(votes[key].isin(b), "b", "o")))
    p = g.pivot_table(index="unit", columns="grp", values="votes", aggfunc="sum", fill_value=0.0)
    for c in "abo":
        if c not in p:
            p[c] = 0.0
    two = p["a"] + p["b"]
    return pd.DataFrame({"s0": p["a"] / two, "V0": two, "o0": p["o"] / two}).dropna()


def crosswalk(prior_year: int, year: int) -> pd.DataFrame:
    """Weights mapping the prior election's district votes onto this election's districts (Hungary redrew
    Budapest, Pest, Fejér and Csongrád-Csanád in 2024). Rows: (unit_prior, unit, w), where w is the share of the
    prior district's registered voters that now sits in `unit`. Settlements in one district in both elections
    map whole; settlements split between districts are mapped by polling station (station number matched across
    elections; stations without a match follow the settlement's voter split)."""
    def tel(y):
        E = ELECTIONS[y]
        out = {}
        for x in _list(_j(y, f"{E['ver']}/ver/Telepulesek.json")):
            d = x.get("leiro", x)
            out[(d["maz"], d["taz"])] = (list(d["evk_lst"]), float(x["letszam"]["osszesen"]))
        return out

    def stations(y, maz, taz):
        E = ELECTIONS[y]
        f = RAW_HU / f"ogy{y}" / E["ver"] / "ver" / maz / f"Szavazokorok-{maz}-{taz}.json"
        d = _list(json.loads(f.read_text()))
        lst = d["szavazokorok"] if isinstance(d, dict) else d
        out = {}
        for s in lst:
            d = s.get("leiro", s)
            out[d["sorszam"]] = (d["evk"], float(s["letszam"]["osszesen"]))
        return out

    A, B = tel(prior_year), tel(year)
    rows = []
    for k, (ev_a, n_a) in A.items():
        ev_b, _ = B[k]
        if len(ev_a) == 1 and len(ev_b) == 1:
            rows.append((unit_id(k[0], ev_a[0]), unit_id(k[0], ev_b[0]), n_a))
            continue
        sa = stations(prior_year, *k) if len(ev_a) > 1 else None
        sb = stations(year, *k) if len(ev_b) > 1 else None
        if sb is not None:
            split_b = pd.Series({e: sum(n for ee, n in sb.values() if ee == e) for e in ev_b})
            split_b = split_b / split_b.sum()
        if sa is None:                                   # whole prior settlement in one prior district
            if sb is None:
                rows.append((unit_id(k[0], ev_a[0]), unit_id(k[0], ev_b[0]), n_a))
            else:
                rows += [(unit_id(k[0], ev_a[0]), unit_id(k[0], e), n_a * w) for e, w in split_b.items()]
            continue
        for no, (e_a, n) in sa.items():
            if sb is None:
                rows.append((unit_id(k[0], e_a), unit_id(k[0], ev_b[0]), n))
            elif no in sb:
                rows.append((unit_id(k[0], e_a), unit_id(k[0], sb[no][0]), n))
            else:
                rows += [(unit_id(k[0], e_a), unit_id(k[0], e), n * w) for e, w in split_b.items()]
    X = pd.DataFrame(rows, columns=["unit_prior", "unit", "n"]).groupby(["unit_prior", "unit"], as_index=False)["n"].sum()
    X["w"] = X["n"] / X.groupby("unit_prior")["n"].transform("sum")
    return X[["unit_prior", "unit", "w"]]


def map_votes(votes: pd.DataFrame, cw: pd.DataFrame, key: str) -> pd.DataFrame:
    """Prior district votes (unit, key, votes) onto current districts with crosswalk weights."""
    m = votes.merge(cw, left_on="unit", right_on="unit_prior", suffixes=("_old", ""))
    m["votes"] = m["votes"] * m["w"]
    return m.groupby(["unit", key], as_index=False)["votes"].sum()


def previous_results(year: int) -> pd.DataFrame:
    """The previous election's district results as published on this election's site (ElozoOevkEredmenyek; the
    districts of the previous election): unit, name (nominating group name), votes."""
    E = ELECTIONS[year]
    rows = [{"unit": unit_id(r["maz"], r["evk"]), "name": r["jlcs_nev"], "votes": float(r["szavazat"])}
            for r in _list(_j(year, f"{E['ver']}/ver/ElozoOevkEredmenyek.json"))]
    return pd.DataFrame(rows)


# ======================================================================================== model

@dataclass(frozen=True)
class Settings:
    nowcast: nc.Params = field(default_factory=nc.Params)        # district model (districts in county groups)
    list_nowcast: nc.Params = field(default_factory=nc.Params)   # list model (counties, one pooled group)
    mail_kappa: float = 20_000.0      # pseudo-votes behind the prior postal split
    mail_tau: float = 0.02            # sd of the postal split of the uncounted postal votes, beyond sampling
    mail_return: float = 0.59         # final valid postal ballots / registered postal voters (previous election)
    mail_two_way: float = 0.98        # A+B share of valid postal list votes before any postal vote is counted
    other_tau: float = 0.10           # relative sd of each third party's projected vote (shared nationally)
    envelope_rate: float = 0.93       # envelope votes cast / registered envelope voters (transferred + consular)
    env_shift: float = 0.0            # A two-way share of envelope votes minus that of the station votes
    env_sd: float = 0.10              # sd of that shift (one shared error)
    turnout_sd: float = 0.005         # sd of the projected final turnout (fraction)
    draws: int = 4000


def _county_tables(L: dict, tl_a: int, tl_b: int) -> pd.DataFrame:
    c = L["county"]
    return pd.DataFrame({"uf": "hu", "cd_mun": c.index, "a": c.get(tl_a, 0.0), "b": c.get(tl_b, 0.0),
                         "f": L["county_f"].reindex(c.index).fillna(0.0)}).reset_index(drop=True)


def list_race(L: dict, prior_c: pd.DataFrame, prior_mail: tuple[float, float], tl_a: int, tl_b: int,
              st: Settings, n_env: float) -> dict:
    """National list two-way race (A vs B). prior_c: county prior (uf, cd_mun, s0, V0); prior_mail: (s0, valid
    two-way postal votes of the previous election); n_env: registered envelope voters (transferred within
    Hungary + consular), whose ballots are counted days later into the domestic totals. Station votes by
    brunder.nowcast.nowcast over counties; envelope and postal votes added as their own terms."""
    cs = _county_tables(L, tl_a, tl_b)
    cs["n"] = cs["a"] + cs["b"]
    dom = nc.nowcast(cs[["uf", "cd_mun", "n", "a", "f"]], prior_c, st.list_nowcast)
    meta = L["nat"]
    n_dom = float(cs["n"].sum())
    q = n_dom / float(L["dom"].sum()) if L["dom"].sum() > 0 else st.mail_two_way
    # envelopes: expected two-way votes not yet counted, at the station projection + env_shift
    R_e = max(st.envelope_rate * n_env - float(meta.get("beerkezett_boritekok") or 0.0), 0.0) * q
    s_e = float(np.clip(dom["share_hat"] + st.env_shift, 0, 1))
    var_e = (2 * R_e) ** 2 * st.env_sd ** 2
    # postal ballots
    nm_a, nm_b = float(L["mail"].get(tl_a, 0.0)), float(L["mail"].get(tl_b, 0.0))
    n_m = nm_a + nm_b
    got = float(meta.get("level_ervenyes_irat") or 0.0)
    two_frac = n_m / float(L["mail"].sum()) if L["mail"].sum() > 0 else st.mail_two_way
    E_m = max(two_frac * max(got, st.mail_return * float(meta.get("vp_level") or 0.0)), n_m)
    s0_m, _ = prior_mail
    s_m = (nm_a + st.mail_kappa * s0_m) / (n_m + st.mail_kappa)
    R_m = max(E_m - n_m, 0.0)
    var_m = (2 * R_m) ** 2 * (s_m * (1 - s_m) / (n_m + st.mail_kappa) + st.mail_tau ** 2)
    M = dom["margin_hat"] + R_e * (2 * s_e - 1) + (2 * nm_a - n_m) + R_m * (2 * s_m - 1)
    sd = float(np.sqrt(dom["margin_sd"] ** 2 + var_e + var_m))
    tot_dom = n_dom / dom["counted_frac"] if dom["counted_frac"] and dom["counted_frac"] > 0 else float(prior_c["V0"].sum())
    T = tot_dom + R_e + E_m
    raw_n = n_dom + n_m
    return {"share": 0.5 + M / (2 * T), "sd": sd / (2 * T), "P": float(norm.cdf(M / sd)) if sd > 0 else float(M > 0),
            "margin_votes": M, "margin_sd": sd, "total_two_way": T,
            "counted": float(raw_n / T) if T else 0.0,
            "raw": float((cs["a"].sum() + nm_a) / raw_n) if raw_n else np.nan,
            "dom": dom, "env": {"remaining": R_e, "share": s_e},
            "mail": {"n": n_m, "a": nm_a, "expected": E_m, "share": s_m}}


def _district_moments(state: pd.DataFrame, prior: pd.DataFrame, p: nc.Params) -> pd.DataFrame:
    """brunder.nowcast.nowcast's per-unit quantities (uf = county, cd_mun = district): remaining votes R, projected
    remaining share s_hat, and the variance components, so draws can be taken per district."""
    df = prior.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0
    expected_counted = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / expected_counted if expected_counted > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k
    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    c = df[counted]
    delta = float(np.average(c["d"], weights=c["n"])) if len(c) else 0.0
    by = c.groupby("uf").agg(W=("n", "sum"), W2=("n", lambda x: (x ** 2).sum())) if len(c) else \
        pd.DataFrame(columns=["W", "W2"])
    by["wd"] = c.assign(wd=c["n"] * c["d"]).groupby("uf")["wd"].sum() if len(c) else []
    by = by.reindex(df["uf"].unique()).fillna(0.0)
    by["delta_u"] = (by["wd"] + p.kappa * delta) / (by["W"] + p.kappa)
    df = df.join(by[["delta_u"]], on="uf")
    resid = (df["d"] - df["delta_u"]).fillna(0.0)
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted).clip(0, 1)
    sig_m2 = nc._post_var(resid[counted].to_numpy(), df.loc[counted, "n"].to_numpy(), p.sigma_mun0, p.nu_mun)
    pooled = by["W"] > 0
    raw_u = by.loc[pooled, "wd"] / by.loc[pooled, "W"]
    sig_b2 = nc._post_var(raw_u.to_numpy(), by.loc[pooled, "W"].to_numpy(), p.sigma_uf0, p.nu_uf)
    lam = by["W"] / (by["W"] + p.kappa)
    by["var_u"] = (lam ** 2 * sig_m2 * by["W2"] / by["W"].where(by["W"] > 0) ** 2).fillna(0.0) + sig_b2 * (1 - lam)
    Wp, W2p = by.loc[pooled, "W"], by.loc[pooled, "W2"]
    var_delta = (sig_m2 * W2p.sum() + sig_b2 * (Wp ** 2).sum()) / Wp.sum() ** 2 if Wp.sum() > 0 else 0.0
    df["var_i"] = sig_m2 * np.where(counted, 1 - df["f"], 1.0)
    df = df.join(by[["var_u"]], on="uf").join((1 - lam).rename("one_m_lam"), on="uf")
    df.attrs.update({"var_delta": var_delta, "tau": p.tau, "k": k, "delta": delta})
    return df.reset_index(drop=True)


def district_draws(state: pd.DataFrame, prior: pd.DataFrame, p: nc.Params, z_nat: np.ndarray,
                   rng: np.random.Generator, env_shift: float = 0.0, env_sd: float = 0.0) -> tuple[pd.DataFrame, np.ndarray]:
    """Joint draws of each district's final two-way A share. z_nat: (draws,) standard normals for the national
    error (shared with the list race). Envelope votes still to come (state column R_env, two-way) vote at the
    district's drawn share + env_shift, with one shared error of sd env_sd. Returns (moments per district with
    R_env, final A two-way share draws x districts)."""
    m = _district_moments(state.drop(columns=["R_env"], errors="ignore"), prior, p)
    m["R_env"] = m["cd_mun"].map(state.set_index("cd_mun")["R_env"]).fillna(0.0) if "R_env" in state else 0.0
    D = len(z_nat)
    ufs = m["uf"].unique()
    zu = pd.DataFrame(rng.standard_normal((D, len(ufs))), columns=ufs)
    z2 = rng.standard_normal(D)
    nat = m.attrs["tau"] * z_nat[:, None] + \
        (m["one_m_lam"].to_numpy() * np.sqrt(m.attrs["var_delta"]))[None, :] * z2[:, None]
    s = m["s_hat"].to_numpy()[None, :] + nat + zu[m["uf"]].to_numpy() * np.sqrt(m["var_u"].to_numpy())[None, :] + \
        rng.standard_normal((D, len(m))) * np.sqrt(m["var_i"].to_numpy())[None, :]
    s = np.clip(s, 0, 1)
    s_e = np.clip(s + env_shift + env_sd * rng.standard_normal(D)[:, None], 0, 1)
    R, Re = m["R"].to_numpy()[None, :], m["R_env"].to_numpy()[None, :]
    final_a = m["a"].to_numpy()[None, :] + R * s + Re * s_e
    final_n = m["n"].to_numpy()[None, :] + R + Re
    return m, np.divide(final_a, final_n, out=np.full_like(final_a, 0.5), where=final_n > 0)


def other_shares(V: pd.DataFrame, U: pd.DataFrame, jl_a: int, jl_b: int, keep: list[int],
                 prior_o0: pd.Series) -> pd.DataFrame:
    """Third-party district votes relative to the A+B two-way votes, per district and kept party (others pooled
    under 0): the district's own counted ratio where counted, else the national counted ratio (prior o0 split
    like the national ratio before anything is counted)."""
    v = V.assign(grp=np.where(V["jlcs"].isin(keep), V["jlcs"], 0))
    two = v[v["jlcs"].isin([jl_a, jl_b])].groupby("unit")["votes"].sum().reindex(U.index).fillna(0.0)
    oth = v[~v["jlcs"].isin([jl_a, jl_b])].pivot_table(index="unit", columns="grp", values="votes", aggfunc="sum",
                                                        fill_value=0.0).reindex(U.index).fillna(0.0)
    for g in [*keep, 0]:
        if g not in oth:
            oth[g] = 0.0
    oth = oth[[*keep, 0]]
    nat = oth.sum() / two.sum() if two.sum() > 0 else None
    out = oth.div(two.where(two > 0), axis=0)
    if nat is None:
        mix = pd.Series(1.0 / len(oth.columns), index=oth.columns)
        fill = pd.DataFrame(np.outer(prior_o0.reindex(U.index).fillna(prior_o0.mean()), mix), index=U.index,
                            columns=oth.columns)
    else:
        fill = pd.DataFrame(np.tile(nat.to_numpy(), (len(U), 1)), index=U.index, columns=oth.columns)
    return out.where(two > 0, fill).fillna(0.0)


# ======================================================================================== priors and states

def prior_2022_for_2026(cfg: dict) -> dict:
    """2022 final district votes (official, by nominating group) mapped onto the 2026 districts; county prior =
    the same votes summed by county; postal prior = the 2022 postal split of the two prior lists."""
    G = cfg["groups"]
    py = cfg["prior_year"]
    E = ELECTIONS[py]
    _, V = smd_count(_j(py, f"{E['final']}/szavossz/OevkJkv.json"), candidates(py))
    cw = crosswalk(py, cfg["year"])
    Vm = map_votes(V[["unit", "jlcs", "votes"]], cw, "jlcs")
    lc = list_count(_j(py, f"{E['final']}/szavossz/ListasJkv.json"))
    pl = party_lists(py)
    tl = lambda codes: pl.index[pl["jlcs"].isin(codes)]
    ma, mb = lc["mail"].reindex(tl(G["prior_a"])).sum(), lc["mail"].reindex(tl(G["prior_b"])).sum()
    return _prior_tables(Vm, G["prior_a"], G["prior_b"], "jlcs", (float(ma / (ma + mb)), float(ma + mb)))


def prior_2018_for_2022(a_names: list[str], b_names: list[str], mail: tuple[float, float]) -> dict:
    """2018 district results as published on the 2022 site (same district map), by nominating-group name."""
    return _prior_tables(previous_results(2022), a_names, b_names, "name", mail)


def _prior_tables(votes: pd.DataFrame, a: list, b: list, key: str, mail: tuple[float, float]) -> dict:
    d = prior_from_votes(votes, a, b, key)
    d["uf"] = d.index.str.slice(3, 5)
    d = d.rename_axis("cd_mun").reset_index()[["uf", "cd_mun", "s0", "V0", "o0"]]
    c = d.assign(A=d["s0"] * d["V0"]).groupby("uf").agg(A=("A", "sum"), V0=("V0", "sum"))
    county = pd.DataFrame({"uf": "hu", "cd_mun": c.index, "s0": c["A"] / c["V0"], "V0": c["V0"]}).reset_index(drop=True)
    return {"district": d, "county": county, "mail": mail}


def district_state(U: pd.DataFrame, V: pd.DataFrame, jl_a: int, jl_b: int, env: pd.Series | None = None,
                   envelope_rate: float = 0.0) -> pd.DataFrame:
    """Count state per district for the nowcast: n = A + B counted, a = A counted, f = stations processed;
    R_env = envelope votes still to come (expected minus counted, at the district's counted two-way fraction)."""
    a = V[V["jlcs"] == jl_a].groupby("unit")["votes"].sum().reindex(U.index).fillna(0.0)
    b = V[V["jlcs"] == jl_b].groupby("unit")["votes"].sum().reindex(U.index).fillna(0.0)
    q = np.where(U["valid"] > 0, (a + b) / U["valid"].where(U["valid"] > 0), 0.9)
    R_env = 0.0 if env is None else \
        np.maximum(envelope_rate * env.reindex(U.index).fillna(0.0) - U["env_counted"], 0.0) * q
    return pd.DataFrame({"uf": U["maz"], "cd_mun": U.index, "n": a + b, "a": a,
                         "f": np.where(a + b > 0, U["f"], 0.0), "R_env": R_env}).reset_index(drop=True)


# ======================================================================================== one publication

def registered(year: int) -> dict[str, float]:
    """Registered voters known before election day (Telepulesek): at home, and envelope voters (transferred
    within Hungary + at consulates). Postal voters (vp_level) are in every count publication."""
    E = ELECTIONS[year]
    T = _list(_j(year, f"{E['ver']}/ver/Telepulesek.json"))
    return {"home": float(sum(x["letszam"]["honos"] for x in T)),
            "env": float(sum(x["letszam"]["atjel"] + x["letszam"]["kuvi"] for x in T))}


def turnout(L: dict, reg: dict, st: Settings) -> tuple[float, float]:
    """Projected final turnout (votes cast / all registered incl. postal, the NVI and market definition):
    home voters at the turnout of the stations processed so far, envelope voters at envelope_rate, postal
    ballots at mail_return (or the valid ones already received, if more)."""
    m = L["nat"]
    seen = float(m.get("vp_belf_njben") or 0)
    if seen <= 0:
        return np.nan, np.nan
    home = float(m.get("szavkorben_megjelent") or 0) / seen * reg["home"]
    env = max(st.envelope_rate * reg["env"], float(m.get("beerkezett_boritekok") or 0))
    vp_level = float(m.get("vp_level") or 0)
    mail = max(float(m.get("level_ervenyes_irat") or 0), st.mail_return * vp_level)
    return (home + env + mail) / (reg["home"] + reg["env"] + vp_level), st.turnout_sd


def evaluate(pub: dict, prior: dict, cfg: dict, st: Settings, ctx: dict,
             rng: np.random.Generator) -> dict:
    """Model state after one count publication: race summaries and P(YES) per configured market. ctx: pl
    (party_lists), reg (registered), reg_d (district_registered) of the election."""
    pl = ctx["pl"]
    G = cfg["groups"]
    U, V = pub["smd"]
    L = pub["list"]
    lr = list_race(L, prior["county"], prior["mail"], G["tl_a"], G["tl_b"], st, ctx["reg"]["env"])
    D = st.draws
    z = rng.standard_normal(D)
    state = district_state(U, V, G["a"], G["b"], ctx["reg_d"]["env"], st.envelope_rate)
    mom, s = district_draws(state, prior["district"], st.nowcast, z, rng, st.env_shift, st.env_sd)
    units = mom["cd_mun"].to_numpy()

    # third parties: party lists with >= min_list_share of the counted list vote
    tot_list = L["dom"].add(L["mail"], fill_value=0)
    party = pl[(pl["kind"] != "N")]
    natl = pl[pl["kind"] == "N"]
    counted_party = tot_list.reindex(party.index).fillna(0)
    share = counted_party / max(counted_party.sum(), 1)
    third = [t for t in party.index if t not in (G["tl_a"], G["tl_b"]) and share.get(t, 0) >= G["min_list_share"]]
    third_j = [int(party.loc[t, "jlcs"]) for t in third]
    zp = rng.standard_normal((D, len(third))) * st.other_tau

    # list votes per draw
    raw_n = lr["mail"]["n"] + float(L["dom"].get(G["tl_a"], 0) + L["dom"].get(G["tl_b"], 0))
    e = lr["total_two_way"] / raw_n if raw_n > 0 else 1.0
    T2 = lr["total_two_way"]
    A_L = np.clip(T2 * (lr["share"] + lr["sd"] * z), 0, T2)
    B_L = T2 - A_L
    th = np.maximum(counted_party.reindex(third).to_numpy()[None, :] * e * (1 + zp), 0) if third else np.zeros((D, 0))
    rest = max(float(counted_party.sum() - counted_party.get(G["tl_a"], 0) - counted_party.get(G["tl_b"], 0)
                     - counted_party.reindex(third).sum()), 0.0) * e
    NV = tot_list.reindex(natl.index).fillna(0).to_numpy()[None, :] * e * np.ones((D, 1))
    Lmat = np.column_stack([A_L, B_L, th, np.full(D, rest)])
    thr = np.concatenate([[party.loc[G["tl_a"], "threshold"], party.loc[G["tl_b"], "threshold"]],
                          party.loc[third, "threshold"].to_numpy(), [np.inf]])

    # district votes per draw
    nf = (mom["n"] + mom["R"] + mom["R_env"]).to_numpy()[None, :]
    dA = nf * s
    dB = nf - dA
    ratios = other_shares(V, U, G["a"], G["b"], third_j, prior["district"].set_index("cd_mun")["o0"])
    ratios = ratios.reindex(units).fillna(0.0)
    dT = [nf * ratios[j].to_numpy()[None, :] * (1 + zp[:, [k]]) for k, j in enumerate(third_j)]
    dO = np.broadcast_to(nf * ratios[0].to_numpy()[None, :], dA.shape)
    cube = np.stack([dA, dB, *dT, dO], axis=2)                 # draws x districts x (A, B, third..., pooled)
    order = np.argsort(-cube, axis=2)
    top = np.take_along_axis(cube, order[:, :, :1], 2)[:, :, 0]
    second = np.take_along_axis(cube, order[:, :, 1:2], 2)[:, :, 0]
    win = order[:, :, 0]
    win = np.where(win == cube.shape[2] - 1, -1, win)          # pooled others never hold a list
    w_mask = np.zeros_like(cube, dtype=bool)                   # winners' compensation = surplus over runner-up + 1
    np.put_along_axis(w_mask, order[:, :, :1], True, axis=2)
    comp = np.where(w_mask, (top - second - 1)[:, :, None], cube).sum(1)[:, :-1]
    wins = np.stack([(win == k).sum(1) for k in range(cube.shape[2] - 1)], axis=1)
    Cmat = np.column_stack([comp, np.zeros(D)])
    ls, ns = allocate_many(Lmat, Cmat, thr, NV)
    seats = ls[:, :-1] + wins
    sA, sB = seats[:, 0], seats[:, 1]

    party_tot = Lmat.sum(1)
    out = {"P": {}, "list": {k: v for k, v in lr.items() if k not in ("dom", "mail")},
           "smd": {"share": float(np.mean(dA.sum(1) / nf.sum())), "sd": float(np.std(dA.sum(1) / nf.sum())),
                   "raw": float(state["a"].sum() / state["n"].sum()) if state["n"].sum() else np.nan,
                   "counted": counted_share(U, ctx["reg_d"])},
           "seats": {"a_mean": float(sA.mean()), "a_sd": float(sA.std()), "b_mean": float(sB.mean()),
                     "P_a_most": float(np.mean((sA > sB) | ((sA == sB) & (A_L > B_L)))),
                     "share": float(np.mean(sA / np.maximum(sA + sB, 1))), "share_sd": float(np.std(sA / np.maximum(sA + sB, 1)))},
           "districts": pd.DataFrame({"unit": units, "proj": s.mean(0), "P_a": (win == 0).mean(0),
                                     "f": mom["f"].to_numpy(),
                                     "raw": np.where(mom["n"] > 0, mom["a"] / mom["n"].where(mom["n"] > 0), np.nan)})}
    to, tsd = turnout(L, ctx["reg"], st)
    out["turnout"] = {"mean": to, "sd": tsd}
    a_most = (sA > sB) | ((sA == sB) & (A_L > B_L))
    for m in cfg["markets"]:
        k, side = m["kind"], m.get("party")
        if k in ("most_seats", "pm"):
            y = a_most if side == "a" else ~a_most
        elif k == "list_winner":
            y = (A_L > B_L) if side == "a" else (B_L > A_L)
            y = y & ((A_L if side == "a" else B_L) > (th.max(1) if th.shape[1] else 0))
        elif k == "list_margin":
            mine, other = (A_L, B_L) if side == "a" else (B_L, A_L)
            runner = np.maximum(other, th.max(1) if th.shape[1] else 0)
            mg = 100 * (mine - runner) / party_tot
            y = (mg >= m["lo"]) & (mg < m["hi"])
        elif k == "seats_band":
            y = (sA >= m["lo"]) & (sA <= m["hi"])
        elif k == "seats_atleast":
            y = sA >= m["n"]
        elif k == "turnout":
            out["P"][m["id"]] = float(norm.cdf((m["hi"] / 100 - to) / tsd) - norm.cdf((m["lo"] / 100 - to) / tsd)) \
                if np.isfinite(to) else np.nan
            continue
        else:
            raise ValueError(f"unknown market kind {k}")
        out["P"][m["id"]] = float(np.mean(y))
    out["draws"] = {"sA": sA, "A_L": A_L, "B_L": B_L, "party_tot": party_tot}
    return out


def settings_from_config(cfg: dict) -> Settings:
    M = cfg["model"]
    o = M.get("other", {})
    return Settings(nowcast=nc.Params(**M.get("district", {})), list_nowcast=nc.Params(**M.get("list", {})),
                    **{k: (int(v) if k == "draws" else float(v)) for k, v in o.items()})
