"""Poland, presidential election 2025 (round 1 on 2025-05-18, runoff on 2025-06-01): replay of the PKW count.

Data. The PKW results site is a single-page app that reads gzip-compressed protobuf files
(`data/obkw/pl_po_woj.<epoch>.blob` round 1, `data/obkw2/...` runoff). The schema below is read off the site's own
JavaScript (the generated protobuf.js decoders `HierarchiaOBKW`, `Statystyki`, `StatRes`, `Protokol`,
`Protokol.PozProtokolu`, `SciezkaWHierarchii`, `HierarchyRes`, `Zestawienia`); only the fields used here are
decoded. One file per version, covering the whole country:

  top field 3 (repeated)  one voivodeship: field 1 = path {1: TERYT*2 (sint32), 2: name}, field 4 = Statystyki
  top field 18 (repeated) the two extra units: TERYT 149800 ships, 149900 abroad (same layout as field 3)
  top field 4             the whole country (Statystyki)
  Statystyki: 5 = registered voters of the unit (constant over the night), 12 = number of obwody (polling
              districts) in the unit, 16 = StatRes {2: obwody counted, 3: Protokol aggregated over the counted obwody}
  Protokol: 5 = eligible voters in the counted obwody (sint32), 27 = valid votes (sint32),
            28 (repeated) = PozProtokolu {3: votes (sint32), 8: committee id (sint32)}
  top field 16            national HierarchyRes over the counted obwody: 3 = eligible voters, 6 = valid votes,
                          7 (repeated) = {1: candidate id, 2: votes}, each a `Zestawienia` with one number per
                          gmina-size class (fields 7..14: up to 5,000 / 5,001-10,000 / 10,001-20,000 /
                          20,001-50,000 / 50,001-100,000 / 100,001-200,000 / 200,001-500,000 / over 500,000
                          residents) and 5 = abroad

The version epoch is the moment PKW generated the file; the Internet Archive capture comes later and is not used as
the publication time. Everything here is read from the files, nothing is fetched (analysis/pl_fetch.py fetches).

Model. `brunder.nowcast` over cells = voivodeship x gmina-size class (16 x 8 = 128 cells, group = voivodeship) plus
abroad with ships (its own group, kept out of the national swing). PKW publishes the count only by voivodeship and
by size class (marginals), so the counted votes of each cell are reconstructed by raking: the counted share of each
cell's electorate is a logistic fit g = expit(alpha_voivodeship + beta_class) to the two sets of counted-electorate
marginals, and each candidate's counted votes in a cell are the prior's votes per voter times the counted voters,
raked to that candidate's two sets of counted-vote marginals. The prior is the 2020 result of the same round
(Trzaskowski against Duda, the candidate whose place Nawrocki takes) by cell. See docs/poland.md.
"""

from __future__ import annotations

import gzip
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit
from scipy.stats import norm

from . import nowcast as nc
from .paths import DATA

RAW_PL = DATA / "raw" / "poland"

# committee ids in the count files (= field 8 of PozProtokolu, sint32-decoded) and candidate ids (class tables)
COMMITTEE = {"nawrocki": 51653, "trzaskowski": 51611, "mentzen": 51610}
CANDIDATE = {"nawrocki": 4279782, "trzaskowski": 4279586}
VOIVODESHIPS = {
    "02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie", "08": "lubuskie", "10": "łódzkie",
    "12": "małopolskie", "14": "mazowieckie", "16": "opolskie", "18": "podkarpackie", "20": "podlaskie",
    "22": "pomorskie", "24": "śląskie", "26": "świętokrzyskie", "28": "warmińsko-mazurskie", "30": "wielkopolskie",
    "32": "zachodniopomorskie",
}
WOJ = list(VOIVODESHIPS)
SHIPS, ABROAD = "98", "99"          # TERYT powiat 1498 (ships) and 1499 (abroad); the model merges both into ABROAD
UNITS = [*WOJ, ABROAD]
UNIT_NAMES = {**VOIVODESHIPS, ABROAD: "abroad and ships"}
CLASS_FIELDS = [7, 8, 9, 10, 11, 12, 13, 14]
N_CLASS = len(CLASS_FIELDS)


# ---- protobuf wire format (just enough: varint, length-delimited, fixed32/64 skipped)
def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 128:
            return r, i


def _fields(b: bytes) -> list[tuple[int, int, int | bytes]]:
    i, out = 0, []
    while i < len(b):
        k, i = _varint(b, i)
        f, t = k >> 3, k & 7
        if t == 0:
            v, i = _varint(b, i)
        elif t == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif t == 1:
            v, i = b[i:i + 8], i + 8
        elif t == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {t}")
        out.append((f, t, v))
    return out


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def ungzip(raw: bytes) -> bytes:
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def _stat(buf: bytes) -> dict:
    """Statystyki -> {n_obw, counted, valid, votes{committee id: votes}, registered, elig}; counted/valid/votes are
    0/{} before any count."""
    out = {"n_obw": 0, "counted": 0, "valid": 0, "votes": {}, "registered": 0, "elig": 0}
    for f, _t, v in _fields(buf):
        if f == 12:
            out["n_obw"] = v
        elif f == 5:
            out["registered"] = v
        elif f == 16:
            for g, _t2, w in _fields(v):
                if g == 2:
                    out["counted"] = w
                elif g == 3:
                    for h, _t3, x in _fields(w):
                        if h == 5:
                            out["elig"] = _zz(x)
                        elif h == 27:
                            out["valid"] = _zz(x)
                        elif h == 28:
                            pos = {k: val for k, _t4, val in _fields(x)}
                            out["votes"][_zz(pos[8])] = _zz(pos.get(3, 0))
    return out


def _classes(buf: bytes) -> dict:
    """National HierarchyRes -> {"elig": [8 class values], "votes": {candidate id: [8 class values]}}."""
    def zest(b: bytes) -> list[int]:
        d = {k: v for k, _t, v in _fields(b)}
        return [int(d.get(c, 0)) for c in CLASS_FIELDS]
    out = {"elig": [0] * N_CLASS, "votes": {}}
    for g, _t, w in _fields(buf):
        if g == 3:
            out["elig"] = zest(w)
        elif g == 7:
            d = {k: v for k, _t2, v in _fields(w)}
            if 2 in d:
                out["votes"][int(d[1])] = zest(d[2])
    return out


def parse_blob(raw: bytes) -> dict:
    """One pl_po_woj version -> {"units": {code: stat}, "national": stat, "classes": _classes}. Unit codes as UNITS
    plus SHIPS."""
    units, national, classes = {}, None, {"elig": [0] * N_CLASS, "votes": {}}
    for f, _t, v in _fields(ungzip(raw)):
        if f in (3, 18):
            sub = {g: w for g, _t2, w in _fields(v)}
            path = {g: w for g, _t2, w in _fields(sub[1])}
            teryt = _zz(path[1] if f == 3 else path.get(3, path.get(1)))
            code = f"{teryt // 10000:02d}" if f == 3 else (SHIPS if teryt == 149800 else ABROAD if teryt == 149900 else None)
            if code is not None and 4 in sub:
                units[code] = _stat(sub[4])
        elif f == 4:
            national = _stat(v)
        elif f == 16:
            classes = _classes(v)
    return {"units": units, "national": national, "classes": classes}


def blob_version(path: Path | str) -> int:
    return int(re.search(r"pl_po_woj\.(\d+)\.blob", str(path)).group(1))


def versions(rnd: int) -> list[Path]:
    """Archived count versions of a round, oldest first."""
    return sorted((RAW_PL / "blobs" / f"r{rnd}").glob("pl_po_woj.*.blob"), key=blob_version)


def version_time(ver: int) -> pd.Timestamp:
    return pd.Timestamp(ver, unit="s", tz="UTC")


# ---- official final results by gmina (PKW csv): the prior (2020) and the electorate of each cell (2025)
def _read_final(zipname: str) -> pd.DataFrame:
    with zipfile.ZipFile(RAW_PL / "final" / zipname) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        return pd.read_csv(z.open(name), sep=";", encoding="utf-8-sig", dtype=str)


def gminas(year: int, rnd: int, cand_a: str = "TRZASKOWSKI", cand_b: str = "DUDA|NAWROCKI") -> pd.DataFrame:
    """Final result per gmina from the PKW csv: unit (voivodeship code, ABROAD or SHIPS), key (6-digit TERYT, Warsaw's
    districts merged into 146500; None abroad), E (eligible voters), a, b (votes for A and B), valid. Abroad has one row
    per country in 2025 and one row in 2020."""
    d = _read_final(f"pl{year}r{rnd}_gminy.zip")
    t6 = (d["TERYT Gminy"] if "TERYT Gminy" in d.columns else d["Kod TERYT"]).fillna("").str.zfill(6)
    powiat = d["Powiat"].fillna("").str.lower()
    ships = t6.str.startswith("1498") | powiat.eq("statki")
    abroad = t6.str.startswith("1499") | powiat.eq("zagranica")
    unit = pd.Series(np.where(ships, SHIPS, np.where(abroad, ABROAD, t6.str[:2])), index=d.index)
    key = t6.where(~t6.str.startswith("1465"), "146500").where(~(ships | abroad), None)
    col = lambda pat: next(c for c in d.columns if re.search(pat, c, re.I))  # noqa: E731
    num = lambda c: pd.to_numeric(d[c], errors="coerce").fillna(0)  # noqa: E731
    g = pd.DataFrame({"unit": unit, "key": key, "E": num(col(r"^Liczba wyborców uprawnionych")),
                      "a": num(col(cand_a)), "b": num(col(cand_b)),
                      "valid": num(col(r"^Liczba głosów ważnych oddanych łącznie"))})
    dom = g[g["key"].notna()].groupby(["unit", "key"], as_index=False)[["E", "a", "b", "valid"]].sum()
    rest = g[g["key"].isna()].assign(unit=lambda x: ABROAD)[["unit", "key", "E", "a", "b", "valid"]]
    return pd.concat([dom, rest.groupby("unit", as_index=False).agg({"key": "first", "E": "sum", "a": "sum", "b": "sum",
                                                                     "valid": "sum"})], ignore_index=True)


def final_by_unit(year: int, rnd: int) -> pd.DataFrame:
    """Final A, B and valid votes and eligible voters per unit (voivodeships and ABROAD incl. ships)."""
    return gminas(year, rnd).groupby("unit")[["E", "a", "b", "valid"]].sum().reindex(UNITS)


# ---- gmina-size classes. PKW's classes are by residents (not published in the csv); they are rebuilt from the
# eligible voters: gminas sorted by electorate, cut so that each class holds the electorate PKW reports for it.
class Classes:
    """Class of every gmina: by TERYT key from the 2025 round-1 assignment (so the same gmina is in the same class in
    every election and round), by electorate thresholds for keys that did not exist in 2025."""

    def __init__(self):
        g = gminas(2025, 1, "TRZASKOWSKI", "NAWROCKI")
        g = g[g["unit"].isin(WOJ)].sort_values("E")
        tot = np.array(parse_blob(versions(1)[-1].read_bytes())["classes"]["elig"], float)
        mid = g["E"].cumsum() - g["E"] / 2
        k = np.searchsorted(np.cumsum(tot)[:-1], mid.to_numpy())
        self.kmap = pd.Series(k, index=g["key"].to_numpy())
        self.edges = np.array([g["E"].to_numpy()[k == j].min() for j in range(1, N_CLASS)], float)

    def of(self, key: pd.Series, E: pd.Series) -> np.ndarray:
        by_e = np.searchsorted(self.edges, np.asarray(E, float), side="right")
        return key.map(self.kmap).fillna(pd.Series(by_e, index=key.index)).astype(int).to_numpy()


def cells(year: int, rnd: int, classes: Classes, cand_b: str = "DUDA|NAWROCKI") -> pd.DataFrame:
    """Domestic cells (voivodeship x class): E, a, b, votes, plus the abroad row. Index (unit, k); abroad has k = 0."""
    g = gminas(year, rnd, cand_b=cand_b)
    dom = g[g["unit"].isin(WOJ)].copy()
    dom["k"] = classes.of(dom["key"], dom["E"])
    c = dom.groupby(["unit", "k"])[["E", "a", "b", "valid"]].sum()
    full = pd.MultiIndex.from_product([WOJ, range(N_CLASS)], names=["unit", "k"])
    c = c.reindex(full, fill_value=0.0)
    ab = g[g["unit"] == ABROAD][["E", "a", "b", "valid"]].sum()
    row = pd.DataFrame([ab.to_numpy()], columns=c.columns, index=pd.MultiIndex.from_tuples([(ABROAD, 0)], names=["unit", "k"]))
    return pd.concat([c, row])


def prior(rnd: int, classes: Classes | None = None) -> pd.DataFrame:
    """nowcast prior (uf, cd_mun, s0, V0) per cell from the 2020 election of the same round, V0 scaled by the cell's
    2025 / 2020 electorate. Cells are `<woj>-<class>`; group (uf) = voivodeship; abroad is group `zz`."""
    classes = Classes() if classes is None else classes
    p20 = cells(2020, rnd, classes)
    e25 = cells(2025, rnd, classes, "NAWROCKI")["E"]
    n20 = p20["a"] + p20["b"]
    growth = (e25 / p20["E"].where(p20["E"] > 0)).fillna(1.0)
    out = pd.DataFrame({"uf": [("zz" if u == ABROAD else u) for u, _ in p20.index],
                        "cd_mun": [f"{u}-{k}" for u, k in p20.index],
                        "s0": (p20["a"] / n20.where(n20 > 0)).fillna(0.5).to_numpy(),
                        "V0": (n20 * growth).to_numpy()})
    return out[out["V0"] > 0].reset_index(drop=True)


# ---- cell state from the published marginals
def _logit_root(e: np.ndarray, shift: np.ndarray, target: float) -> float:
    """x with sum(e * expit(x + shift)) = target, clipped to [-60, 60] when the target is out of reach."""
    f = lambda x: float((e * expit(x + shift)).sum()) - target  # noqa: E731
    if f(60.0) <= 0:
        return 60.0
    if f(-60.0) >= 0:
        return -60.0
    return brentq(f, -60.0, 60.0, xtol=1e-9)


def fit_fraction(E: np.ndarray, Uw: np.ndarray, Uk: np.ndarray, sweeps: int = 60) -> np.ndarray:
    """Counted share g[w, k] of each cell's electorate: g = expit(alpha_w + beta_k) with sum_k E g = Uw and
    sum_w E g = Uk (alternating one-dimensional root finds). Rows/columns with nothing counted are 0."""
    W, K = E.shape
    a, b = np.zeros(W), np.zeros(K)
    live_w, live_k = Uw > 0, Uk > 0
    for _ in range(sweeps):
        old = (a.copy(), b.copy())
        for w in np.flatnonzero(live_w):
            a[w] = _logit_root(E[w] * live_k, b, Uw[w])
        for k in np.flatnonzero(live_k):
            b[k] = _logit_root(E[:, k] * live_w, a, Uk[k])
        if max(np.abs(a - old[0]).max(), np.abs(b - old[1]).max()) < 1e-7:
            break
    return expit(a[:, None] + b[None, :]) * live_w[:, None] * live_k[None, :]


def rake(M: np.ndarray, rows: np.ndarray, cols: np.ndarray, sweeps: int = 200) -> np.ndarray:
    """Iterative proportional fitting of M[w, k] to row sums `rows` and column sums `cols` (cols rescaled to the row
    total first). Cells with M = 0 stay 0; a margin that cannot be met (all-zero row/column) is dropped."""
    M = M.astype(float).copy()
    if rows.sum() <= 0 or cols.sum() <= 0 or M.sum() <= 0:
        return np.zeros_like(M)
    cols = cols * rows.sum() / cols.sum()
    for _ in range(sweeps):
        rs = M.sum(1)
        M *= np.divide(rows, rs, out=np.zeros_like(rows, float), where=rs > 0)[:, None]
        cs = M.sum(0)
        M *= np.divide(cols, cs, out=np.zeros_like(cols, float), where=cs > 0)[None, :]
        if np.abs(M.sum(1) - rows).max() < 1e-6 * rows.sum():
            break
    return M


def margins(path: Path | str, cand_a: str = "trzaskowski", cand_b: str = "nawrocki") -> dict:
    """Published marginals of one version: per voivodeship (U eligible counted, A, B votes), per size class (same), and
    the abroad+ships unit (n, a, f)."""
    d = parse_blob(Path(path).read_bytes())
    ca, cb = COMMITTEE[cand_a], COMMITTEE[cand_b]
    u = d["units"]
    z = {"n_obw": 0, "counted": 0, "valid": 0, "votes": {}, "registered": 0, "elig": 0}
    w = lambda f: np.array([f(u.get(c, z)) for c in WOJ], float)  # noqa: E731
    cl = d["classes"]
    ab = [u.get(SHIPS, z), u.get(ABROAD, z)]
    reg = sum(x["registered"] for x in ab)
    return {"Uw": w(lambda s: s["elig"]), "Aw": w(lambda s: s["votes"].get(ca, 0)), "Bw": w(lambda s: s["votes"].get(cb, 0)),
            "Uk": np.array(cl["elig"], float), "Ak": np.array(cl["votes"].get(CANDIDATE[cand_a], [0] * N_CLASS), float),
            "Bk": np.array(cl["votes"].get(CANDIDATE[cand_b], [0] * N_CLASS), float),
            "abroad": {"n": sum(x["votes"].get(ca, 0) + x["votes"].get(cb, 0) for x in ab),
                       "a": sum(x["votes"].get(ca, 0) for x in ab),
                       "f": min(1.0, sum(x["elig"] for x in ab) / reg) if reg else 0.0},
            "obw": sum(x["counted"] for x in u.values()), "obw_total": sum(x["n_obw"] for x in u.values())}


def cell_state(m: dict, E: pd.Series, rate_a: pd.Series, rate_b: pd.Series) -> pd.DataFrame:
    """nowcast count state (uf, cd_mun, n, a, f) of every cell from published marginals `m` (see `margins`).
    E: 2025 electorate per cell (index (unit, k)); rate_a / rate_b: prior votes per eligible voter per cell."""
    Ed = E.loc[WOJ].unstack("k").reindex(columns=range(N_CLASS)).to_numpy(float)
    g = fit_fraction(Ed, m["Uw"], m["Uk"])
    U = Ed * g
    ra = rate_a.loc[WOJ].unstack("k").reindex(columns=range(N_CLASS)).to_numpy(float)
    rb = rate_b.loc[WOJ].unstack("k").reindex(columns=range(N_CLASS)).to_numpy(float)
    A = rake(U * ra, m["Aw"], m["Ak"])
    B = rake(U * rb, m["Bw"], m["Bk"])
    rows = [{"uf": w, "cd_mun": f"{w}-{k}", "n": A[i, k] + B[i, k], "a": A[i, k], "f": g[i, k]}
            for i, w in enumerate(WOJ) for k in range(N_CLASS)]
    ab = m["abroad"]
    rows.append({"uf": "zz", "cd_mun": f"{ABROAD}-0", "n": float(ab["n"]), "a": float(ab["a"]), "f": ab["f"]})
    return pd.DataFrame(rows)


class Replay:
    """Everything a replay of one round needs that does not depend on the count: prior, electorate, prior rates."""

    def __init__(self, rnd: int):
        self.rnd = rnd
        self.classes = Classes()
        self.prior = prior(rnd, self.classes)
        self.E = cells(2025, rnd, self.classes, "NAWROCKI")["E"]
        p20 = cells(2020, rnd, self.classes)
        e = p20["E"].where(p20["E"] > 0)
        self.rate_a = (p20["a"] / e).fillna(0.0)
        self.rate_b = (p20["b"] / e).fillna(0.0)

    def state(self, path: Path | str) -> pd.DataFrame:
        return cell_state(margins(path), self.E, self.rate_a, self.rate_b)


# ---- unit-level projection (for the map) and national figures
def project(state: pd.DataFrame, prior_: pd.DataFrame, p: nc.Params) -> pd.DataFrame:
    """Per cell: remaining votes R and projected share s_hat, with the same equations as `nowcast.nowcast` (without the
    optional swing prior / Hispanic slope): the aggregate share_hat of `nowcast` equals sum(a + R s_hat) / sum(n + R)
    (tested)."""
    df = prior_.merge(state, on=["uf", "cd_mun"], how="left").fillna({"n": 0.0, "a": 0.0, "f": 0.0})
    df = df[df["V0"] > 0].copy()
    counted = df["n"] > 0
    exp_counted = (df["f"] * df["V0"]).sum()
    k = df["n"].sum() / exp_counted if exp_counted > 0 else 1.0
    df["R"] = (1 - df["f"]).clip(lower=0) * df["V0"] * k
    df["d"] = np.where(counted, df["a"] / df["n"].where(counted) - df["s0"], np.nan)
    c = df[counted]
    pool = c[~c["uf"].isin(p.unpooled)]
    delta = float(np.average(pool["d"], weights=pool["n"])) if len(pool) else 0.0
    by = c.groupby("uf").apply(lambda g: pd.Series({"W": g["n"].sum(), "wd": (g["n"] * g["d"]).sum()}), include_groups=False) \
        if len(c) else pd.DataFrame(columns=["W", "wd"])
    by = by.reindex(df["uf"].unique()).fillna(0.0)
    by["delta_u"] = (by["wd"] + p.kappa * delta) / (by["W"] + p.kappa)
    df = df.join(by[["delta_u"]], on="uf")
    resid = (df["d"] - df["delta_u"]).fillna(0.0)
    df["s_hat"] = (df["s0"] + df["delta_u"] + p.rho * resid * counted).clip(0, 1)
    return df


def unit_projection(state: pd.DataFrame, prior_: pd.DataFrame, p: nc.Params) -> pd.DataFrame:
    """Per voivodeship (and abroad): counted share (raw), projected final share and expected share counted."""
    df = project(state, prior_, p)
    df["unit"] = df["cd_mun"].str.split("-").str[0]
    df["fin_a"] = df["a"] + df["R"] * df["s_hat"]
    df["fin_n"] = df["n"] + df["R"]
    g = df.groupby("unit").agg(n=("n", "sum"), a=("a", "sum"), R=("R", "sum"), fin_a=("fin_a", "sum"), fin_n=("fin_n", "sum"))
    return pd.DataFrame({"raw": (g["a"] / g["n"].where(g["n"] > 0)), "proj": g["fin_a"] / g["fin_n"],
                         "counted": g["n"] / g["fin_n"]}).reindex(UNITS)


def national(state: pd.DataFrame) -> tuple[float, float]:
    """Raw national tally: A's share of A + B votes counted and the votes counted (A + B)."""
    n = float(state["n"].sum())
    return (float(state["a"].sum() / n) if n > 0 else float("nan")), n


# ---- 2020 polling-district results (for calibration by simulated reporting order)
def obwody_2020(rnd: int, classes: Classes) -> pd.DataFrame:
    """2020 results per obwód: unit, class k, eligible voters, A (Trzaskowski) and B (Duda) votes. Abroad (1499xx) and
    ships (1498xx) rows are merged into ABROAD (k = 0). Class = that of the obwód's gmina (Warsaw's districts as one)."""
    with zipfile.ZipFile(RAW_PL / "final" / f"pl2020r{rnd}_obwody.zip") as z:
        d = pd.read_csv(z.open(next(n for n in z.namelist() if n.endswith(".csv"))), sep=";", encoding="utf-8-sig", dtype=str)
    t6 = d["Kod TERYT"].str.zfill(6)
    ab = t6.str.startswith("1498") | t6.str.startswith("1499")
    num = lambda pat: pd.to_numeric(d[next(c for c in d.columns if re.search(pat, c, re.I))], errors="coerce").fillna(0)  # noqa: E731
    o = pd.DataFrame({"unit": t6.str[:2].where(~ab, ABROAD),
                      "key": t6.where(~t6.str.startswith("1465"), "146500").where(~ab, None),
                      "voters": num(r"^Liczba wyborców uprawnionych"), "a": num("TRZASKOWSKI"), "b": num("DUDA")})
    g = o[~ab].groupby("key")["voters"].sum()
    kmap = pd.Series(classes.of(pd.Series(g.index, index=g.index), g), index=g.index)
    o["k"] = o["key"].map(kmap).fillna(0).astype(int)
    return o.drop(columns="key")


def simulated_selection(obw: pd.DataFrame, f: float, rng: np.random.Generator, order_sd: float = 0.7) -> pd.DataFrame:
    """The obwody counted after a share f of each unit's obwody have reported, in simulated order: smallest first, with
    log-normal noise of sd `order_sd` on the size (so that large urban obwody tend to come last, as on a real night)."""
    key = np.log(obw["voters"].clip(lower=1)) + order_sd * rng.standard_normal(len(obw))
    o = obw.assign(key=key)
    return pd.concat([g.sort_values("key").iloc[: int(round(f * len(g)))] for _, g in o.groupby("unit")])


def margins_of(s: pd.DataFrame, obw: pd.DataFrame) -> dict:
    """Published marginals (as `margins`) of a set of counted obwody `s` out of `obw`."""
    dom = s[s["unit"].isin(WOJ)]
    by_w = dom.groupby("unit")[["voters", "a", "b"]].sum().reindex(WOJ, fill_value=0.0)
    by_k = dom.groupby("k")[["voters", "a", "b"]].sum().reindex(range(N_CLASS), fill_value=0.0)
    ab = s[s["unit"] == ABROAD]
    tot_ab = obw.loc[obw["unit"] == ABROAD, "voters"].sum()
    return {"Uw": by_w["voters"].to_numpy(), "Aw": by_w["a"].to_numpy(), "Bw": by_w["b"].to_numpy(),
            "Uk": by_k["voters"].to_numpy(), "Ak": by_k["a"].to_numpy(), "Bk": by_k["b"].to_numpy(),
            "abroad": {"n": float((ab["a"] + ab["b"]).sum()), "a": float(ab["a"].sum()),
                       "f": float(ab["voters"].sum() / tot_ab) if tot_ab else 0.0},
            "obw": len(s), "obw_total": len(obw)}


def band_p(share: np.ndarray, sd: np.ndarray, side: str, lo: float, hi: float) -> np.ndarray:
    """P(candidate `side` ("n" Nawrocki, "t" Trzaskowski) wins by lo..hi points of valid votes) from a normal on
    Trzaskowski's two-way share (valid votes = A + B in a runoff): Nawrocki's margin is 1 - 2 share, Trzaskowski's 2 share - 1."""
    if side == "n":
        s_lo, s_hi = (1 - hi / 100) / 2, (1 - lo / 100) / 2
    else:
        s_lo, s_hi = (1 + lo / 100) / 2, (1 + hi / 100) / 2
    sd = np.maximum(sd, 1e-9)
    return norm.cdf((s_hi - share) / sd) - norm.cdf((s_lo - share) / sd)
