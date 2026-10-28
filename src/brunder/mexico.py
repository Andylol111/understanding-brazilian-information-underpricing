"""Mexico presidential elections (INE): PREP acta parser, publication times, section priors, count replay.

Data (all INE, "Términos de libre uso", docs/mexico.md):
  - PREP database (Programa de Resultados Electorales Preliminares): one row per expected acta (tally sheet of
    one polling station) plus out-of-catalogue actas, with votes per party / coalition combination and the acta's
    FECHA_HORA_ACOPIO (received at the collection centre), FECHA_HORA_CAPTURA (data keyed) and
    FECHA_HORA_VERIFICACION (data verified), in central Mexico time; the header states the UTC offset
    ("(UTC-6)" in 2024, "(UTC-5)" in 2018, when central Mexico kept summer time). 2024 is comma-separated
    UTF-8 with CR line ends; 2018 is pipe-separated Latin-1 with a leading "sep=|" line.
  - Cómputos distritales (official count): by casilla (computos2018 "CW" file) or by section (SICEE
    "*_SEE_PRE_NAL_SEC.csv"). Used for priors (previous election) and as the final result (scoring only).

Units: electoral sections, keyed (uf, cd_mun) = (2-digit state id, 4-digit section). Votes that belong to no
section go to unpooled pseudo-units in region "zz": abroad (casilla type M, or section 0 in cómputos files) one
unit per state ("m01" ...), early voting (type A) and pre-trial detention (type P) one national unit each.

Publication (when an acta became visible on the PREP site): PREP published a cut every 5 minutes from opening
(20:00 central time). Checked against archived cuts (Wayback copies of avanceNacional.json and state files,
2024): a cut labelled c held the actas verified up to about c + 1 to c + 4 minutes and was online by c + 4 to
c + 6 minutes. Rule: visible = max(ceil_5min(verification - 3.5 min), opening) + 5 min.

Race: A vs B two-way share, A / (A + B) > ½ (2024: Sheinbaum vs Gálvez; the third candidate cannot win, so this
is the winner market's event). A and B are given as lists of party / coalition-combination columns.
"""

from __future__ import annotations

import dataclasses
import io
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd

from . import nowcast
from .paths import RAW

STATES = {
    1: ("ags", "Aguascalientes"), 2: ("bc", "Baja California"), 3: ("bcs", "Baja California Sur"),
    4: ("camp", "Campeche"), 5: ("coah", "Coahuila"), 6: ("col", "Colima"), 7: ("chis", "Chiapas"),
    8: ("chih", "Chihuahua"), 9: ("cdmx", "Ciudad de México"), 10: ("dgo", "Durango"), 11: ("gto", "Guanajuato"),
    12: ("gro", "Guerrero"), 13: ("hgo", "Hidalgo"), 14: ("jal", "Jalisco"), 15: ("mex", "Estado de México"),
    16: ("mich", "Michoacán"), 17: ("mor", "Morelos"), 18: ("nay", "Nayarit"), 19: ("nl", "Nuevo León"),
    20: ("oax", "Oaxaca"), 21: ("pue", "Puebla"), 22: ("qro", "Querétaro"), 23: ("qroo", "Quintana Roo"),
    24: ("slp", "San Luis Potosí"), 25: ("sin", "Sinaloa"), 26: ("son", "Sonora"), 27: ("tab", "Tabasco"),
    28: ("tamps", "Tamaulipas"), 29: ("tlax", "Tlaxcala"), 30: ("ver", "Veracruz"), 31: ("yuc", "Yucatán"),
    32: ("zac", "Zacatecas"),
}
ZZ = "zz"                                    # region of the pseudo-units (kept out of the national swing)
_TIME_FORMATS = ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")


# ============================================================================ parsing

def _text(path: Path | str) -> str:
    """File text: UTF-8, else Latin-1 (SICEE 2024 files carry a UTF-8 BOM before a Latin-1 body)."""
    raw = Path(path).read_bytes().removeprefix(b"\xef\xbb\xbf")
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        s = raw.decode("latin-1")
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _table(text: str, first_col: str) -> pd.DataFrame:
    m = re.search(rf"^{first_col}[|,]", text, flags=re.MULTILINE)
    if m is None:
        raise ValueError(f"no header line starting with {first_col}")
    sep = text[m.end() - 1]
    return pd.read_csv(io.StringIO(text[m.start():]), sep=sep, dtype=str, keep_default_na=False)


def _num(s: pd.Series) -> pd.Series:
    """Vote cells: digits, or '-' / 'N/A' / '' / 'Ilegible' / 'Sin dato' (counted as 0, as the PREP does)."""
    return pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(), errors="coerce").fillna(0.0)


def _int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.strip().str.strip("'"), errors="coerce")


def _times(s: pd.Series) -> pd.Series:
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for f in _TIME_FORMATS:
        out = out.fillna(pd.to_datetime(s, format=f, errors="coerce"))
    return out


def prep_header(text: str) -> dict:
    """Cut time (UTC) and the UTC offset of every time in the file, from the header line 'DD/MM/YYYY HH:MM (UTC-6)'."""
    m = re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}) \(UTC([+-]\d+)\)", text[:2000])
    if m is None:
        raise ValueError("PREP header without '(UTC±h)' cut time")
    off = int(m.group(2))
    cut = pd.Timestamp(pd.to_datetime(m.group(1), format="%d/%m/%Y %H:%M")) - pd.Timedelta(hours=off)
    return {"cut_utc": cut.tz_localize("UTC"), "utc_offset_h": off}


def unit_keys(ent: pd.Series, section: pd.Series, tipo: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(uf, cd_mun): sections, or the zz pseudo-units for abroad (M / section 0), early voting (A), prison (P)."""
    ent = ent.astype(int)
    sec = section.fillna(0).astype(int)
    tipo = tipo.str.strip().str.upper()
    uf = ent.map(lambda e: f"{e:02d}")
    cd = sec.map(lambda s: f"{s:04d}")
    abroad = (tipo == "M") | (sec == 0)
    uf = uf.mask(abroad | tipo.isin(["A", "P"]), ZZ)
    cd = cd.mask(abroad, ent.map(lambda e: f"m{e:02d}"))
    cd = cd.mask(tipo == "A", "a").mask(tipo == "P", "p")
    return uf, cd


def read_prep(path: Path | str, a_cols: list[str], b_cols: list[str]) -> tuple[pd.DataFrame, dict]:
    """One row per acta in the catalogue of expected actas (out-of-catalogue actas dropped).

    Columns: acta, ent, district, section, tipo, uf, cd_mun, a, b, ln (lista nominal), counted (CONTABILIZADA = 1),
    t_acopio / t_captura / t_verif (UTC, NaT if none). Header dict: cut_utc, utc_offset_h, expected, out_of_catalogue."""
    text = _text(path)
    hdr = prep_header(text)
    d = _table(text, "CLAVE_CASILLA").rename(columns={"ID_ESTADO": "ID_ENTIDAD"})
    missing = [c for c in [*a_cols, *b_cols] if c not in d.columns]
    if missing:
        raise KeyError(f"PREP file lacks columns {missing}")
    ooc = (d["CONTABILIZADA"].str.strip() == "2") | d["OBSERVACIONES"].str.contains("atálogo|atalogo", regex=True)
    d = d[~ooc].copy()
    tipo = d["TIPO_CASILLA"].str.strip().str.upper()
    sec = _int(d["SECCION"]).where(~tipo.isin(["M", "A", "P"]))
    uf, cd = unit_keys(d["ID_ENTIDAD"], sec, tipo)
    off = pd.Timedelta(hours=-hdr["utc_offset_h"])
    out = pd.DataFrame({
        "acta": d["CLAVE_ACTA"].str.strip("'"), "ent": _int(d["ID_ENTIDAD"]).astype(int),
        "district": _int(d["ID_DISTRITO_FEDERAL"]).fillna(0).astype(int), "section": sec.fillna(0).astype(int),
        "tipo": tipo, "uf": uf, "cd_mun": cd,
        "a": sum(_num(d[c]) for c in a_cols), "b": sum(_num(d[c]) for c in b_cols),
        "ln": _num(d["LISTA_NOMINAL"]), "counted": d["CONTABILIZADA"].str.strip() == "1",
        **{f"t_{k.lower()}": (_times(d[f"FECHA_HORA_{k}"]) + off).dt.tz_localize("UTC")
           for k in ("ACOPIO", "CAPTURA", "VERIFICACION")},
    }).rename(columns={"t_verificacion": "t_verif"})
    hdr.update(expected=len(out), out_of_catalogue=int(ooc.sum()))
    return out.reset_index(drop=True), hdr


def read_computos(path: Path | str, a_cols: list[str], b_cols: list[str]) -> pd.DataFrame:
    """Official count by unit (uf, cd_mun, ent, a, b, ln) from a cómputos casilla file (computos2018 'CW') or a
    SICEE section file ('*_SEE_PRE_NAL_SEC.csv'). Abroad rows (casilla type M / section 0) go to zz:m<ent>."""
    text = _text(path)
    first = "CLAVE_CASILLA" if re.search(r"^CLAVE_CASILLA[|,]", text, flags=re.MULTILINE) else "CIRCUNSCRIPCION"
    d = _table(text, first).rename(columns={"ID_ENTIDAD": "ID_ESTADO"})
    missing = [c for c in [*a_cols, *b_cols] if c not in d.columns]
    if missing:
        raise KeyError(f"cómputos file lacks columns {missing}")
    tipo = d["TIPO_CASILLA"] if "TIPO_CASILLA" in d else pd.Series("B", index=d.index)
    sec = _int(d["SECCION"]).fillna(0)
    uf, cd = unit_keys(d["ID_ESTADO"], sec, tipo)
    ln_col = next(c for c in ("LISTA_NOMINAL_CASILLA", "LISTA_NOMINAL") if c in d)
    out = pd.DataFrame({"uf": uf, "cd_mun": cd, "ent": _int(d["ID_ESTADO"]).astype(int),
                        "a": sum(_num(d[c]) for c in a_cols), "b": sum(_num(d[c]) for c in b_cols),
                        "ln": _num(d[ln_col])})
    return out.groupby(["uf", "cd_mun"], as_index=False).agg(ent=("ent", "first"), a=("a", "sum"), b=("b", "sum"),
                                                             ln=("ln", "sum"))


# ============================================================================ publication

def visible_at(t_verif: pd.Series, opening_utc: pd.Timestamp, step_min: float = 5.0, include_min: float = 3.5,
               delay_min: float = 5.0) -> pd.Series:
    """When a verified acta appeared on the PREP site (module docstring). NaT stays NaT."""
    step = pd.Timedelta(minutes=step_min)
    cut = (t_verif - pd.Timedelta(minutes=include_min)).dt.ceil(step)
    cut = cut.where(cut.isna() | (cut >= opening_utc), opening_utc)
    return cut + pd.Timedelta(minutes=delay_min)


# ============================================================================ prior

def catalogue(actas: pd.DataFrame) -> pd.DataFrame:
    """Per unit: expected actas k, lista nominal, state and federal district. Known before the count: the PREP
    catalogue of expected actas (with each casilla's lista nominal) is public before the first cut."""
    return actas.groupby(["uf", "cd_mun"], as_index=False).agg(
        ent=("ent", "first"), district=("district", "max"), k=("acta", "size"), ln=("ln", "sum"))


def prior(prev: pd.DataFrame, cat: pd.DataFrame) -> pd.DataFrame:
    """Two-way prior per catalogue unit from the previous election's official count by unit.

    s0 = A / (A + B) of the unit in the previous election; V0 = the unit's lista nominal now (in the catalogue,
    published before election day) times its previous two-way votes per registered voter, so units whose
    territory changed with re-sectioning keep a size that matches the actas expected now. Units new since then
    (and 2024's early-voting and prison units) take s0 and the votes-per-voter rate of the other units in the same
    federal district (zz units: of the whole country). Units of the previous election that are not in the
    catalogue are dropped (they cannot be counted)."""
    p = cat.merge(prev[["uf", "cd_mun", "a", "b", "ln"]].rename(columns={"ln": "ln0"}), on=["uf", "cd_mun"], how="left")
    p["two"] = p["a"] + p["b"]
    have = p["a"].notna() & (p["two"] > 0) & (p["ln0"] > 0)
    p["grp"] = np.where(p["uf"] == ZZ, "nat", p["ent"].astype(str) + "-" + p["district"].astype(str))
    h = p[have]
    g = h.groupby("grp").agg(a=("a", "sum"), two=("two", "sum"), ln0=("ln0", "sum"))
    nat_s, nat_t = h["a"].sum() / h["two"].sum(), h["two"].sum() / h["ln0"].sum()
    rate_s = (g["a"] / g["two"]).reindex(p["grp"]).fillna(nat_s).to_numpy()
    rate_t = (g["two"] / g["ln0"]).reindex(p["grp"]).fillna(nat_t).to_numpy()
    p["s0"] = np.where(have, p["a"] / p["two"].where(have), rate_s)
    p["V0"] = p["ln"] * np.where(have, p["two"] / p["ln0"].where(have), rate_t)
    p["imputed"] = ~have
    return p.loc[p["V0"] > 0, ["uf", "cd_mun", "s0", "V0", "imputed"]].reset_index(drop=True)


# ============================================================================ replay

def count_states(actas: pd.DataFrame, cat: pd.DataFrame, times: pd.DatetimeIndex
                 ) -> Iterator[tuple[pd.Timestamp, pd.DataFrame]]:
    """Count state (uf, cd_mun, n = A + B counted, a, f = share of the unit's actas counted) at each time, from
    the counted actas' `visible` times. Incremental: each acta is added once."""
    idx = pd.MultiIndex.from_frame(cat[["uf", "cd_mun"]])
    c = actas[actas["counted"] & actas["visible"].notna()].sort_values("visible")
    pos = idx.get_indexer(pd.MultiIndex.from_frame(c[["uf", "cd_mun"]]))
    if (pos < 0).any():
        raise ValueError("counted acta outside the catalogue")
    vis = c["visible"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]")
    av, nv = c["a"].to_numpy(float), (c["a"] + c["b"]).to_numpy(float)
    n, a, k = np.zeros(len(cat)), np.zeros(len(cat)), np.zeros(len(cat))
    kk = cat["k"].to_numpy(float)
    base = cat[["uf", "cd_mun"]].reset_index(drop=True)
    j = 0
    for t in times:
        j2 = int(np.searchsorted(vis, np.datetime64(pd.Timestamp(t).tz_convert("UTC").tz_localize(None), "ns"),
                                 side="right"))
        if j2 > j:
            sl = slice(j, j2)
            np.add.at(n, pos[sl], nv[sl])
            np.add.at(a, pos[sl], av[sl])
            np.add.at(k, pos[sl], 1.0)
            j = j2
        yield t, base.assign(n=n.copy(), a=a.copy(), f=np.minimum(k / kk, 1.0))


def project_units(state: pd.DataFrame, pri: pd.DataFrame, p: nowcast.Params) -> pd.DataFrame:
    """Per-unit point projection of the final A share, the same estimate `nowcast.nowcast` sums (its share_hat):
    counted votes plus the remaining R_i at s_hat_i. Columns uf, cd_mun, n, a, R, s_hat, proj."""
    df = pri.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0
    expected_counted = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / expected_counted if expected_counted > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k
    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    c = df[counted]
    pool = c[~c["uf"].isin(p.unpooled)]
    delta = float(np.average(pool["d"], weights=pool["n"])) if len(pool) else 0.0
    W = c.groupby("uf")["n"].sum()
    wd = (c["n"] * c["d"]).groupby(c["uf"]).sum()
    du = ((wd + p.kappa * delta) / (W + p.kappa)).reindex(df["uf"].unique()).fillna(delta)
    df["delta_u"] = df["uf"].map(du)
    resid = (df["d"] - df["delta_u"]).fillna(0.0)
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted).clip(0, 1)
    tot = df["n"] + df["R"]
    df["proj"] = (df["a"] + df["R"] * df["s_hat"]) / tot.where(tot > 0)
    return df[["uf", "cd_mun", "n", "a", "R", "s_hat", "proj"]]


# ============================================================================ configured runs

def load_config(path: Path | str) -> dict:
    cfg = tomllib.loads(Path(path).read_text())
    cfg["_path"] = str(path)
    return cfg


def params(cfg: dict, **overrides) -> nowcast.Params:
    """nowcast.Params from [model] (unset keys keep the nowcast defaults); abroad/early/prison units unpooled."""
    M = {k: v for k, v in cfg.get("model", {}).items() if k in {f.name for f in dataclasses.fields(nowcast.Params)}}
    return nowcast.Params(**{**M, "unpooled": (ZZ,), **overrides})


def setup(cfg: dict, raw: Path = RAW) -> dict:
    """Actas with visible times, the catalogue, the prior and the official final share for a configured election."""
    E, P, F, V = cfg["election"], cfg["prior"], cfg["final"], cfg["publication"]
    actas, hdr = read_prep(raw / E["prep"], E["a_cols"], E["b_cols"])
    actas["visible"] = visible_at(actas["t_verif"], pd.Timestamp(V["opening_utc"]), V["step_min"], V["include_min"],
                                  V["delay_min"])
    cat = catalogue(actas)
    pri = prior(read_computos(raw / P["file"], P["a_cols"], P["b_cols"]), cat)
    fin = read_computos(raw / F["file"], F["a_cols"], F["b_cols"])
    return {"actas": actas, "header": hdr, "cat": cat, "prior": pri, "final_units": fin,
            "final_share": float(fin["a"].sum() / (fin["a"].sum() + fin["b"].sum()))}


def national_path(s: dict, p: nowcast.Params, times: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per time: counted (model share of expected two-way votes counted), actas (share of expected actas
    counted), raw (A share of the counted two-way votes), share / sd / P (nowcast), margin_sd and total (for
    re-scaling sd to another tau: Var M(tau) = Var M(p.tau) + (2 R)^2 (tau^2 - p.tau^2), R = total (1 - counted))."""
    rows = []
    k_all = float(s["cat"]["k"].sum())
    for t, st in count_states(s["actas"], s["cat"], times):
        r = nowcast.nowcast(st, s["prior"], p)
        n = float(st["n"].sum())
        rows.append({"t": t, "counted": r["counted_frac"], "actas": float((st["f"] * s["cat"]["k"]).sum()) / k_all,
                     "raw": float(st["a"].sum()) / n if n > 0 else np.nan, "share": r["share_hat"],
                     "sd": r["share_sd"], "P": r["p_win"], "margin_sd": r["margin_sd"],
                     "total": r["margin_sd"] / (2 * r["share_sd"]) if r["share_sd"] > 0 else np.nan})
    return pd.DataFrame(rows)


def sd_at_tau(path: pd.DataFrame, tau0: float, tau: float) -> pd.Series:
    """Share sd of a national_path run with tau0, re-scaled to tau (the tau term is additive in Var M)."""
    R2 = (2 * path["total"] * (1 - path["counted"])) ** 2
    return np.sqrt(path["margin_sd"] ** 2 + R2 * (tau**2 - tau0**2)) / (2 * path["total"])
